"""
event_processor.py — Event Processor

Processes all send outcomes (SENT, DELIVERED, BOUNCED, COMPLAINT, UNSUBSCRIBED)
and maintains the health metrics for mailboxes and domains.

Event flow:
    SENT
     │
     ├── DELIVERED
     ├── BOUNCED (HARD / SOFT)
     │     → suppress if HARD
     │     → recalculate mailbox health
     │     → recalculate domain health
     ├── COMPLAINT
     │     → suppress permanently
     │     → recalculate + circuit breaker check
     └── UNSUBSCRIBED
           → global suppress + cancel follow-ups

After every event:
    1. Record the MessageEvent
    2. Update SendingStats counters
    3. Update Mailbox aggregate metrics
    4. Update DomainHealth aggregate metrics
    5. Check circuit breakers → may pause mailbox/domain/campaign
    6. Evaluate warmup state machine transitions

Usage:
    from event_processor import record_send, process_bounce, process_complaint
"""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    Mailbox, DomainHealth, SendingStats, MessageEvent,
    Email, Contact, SuppressionList,
)
from utils import get_logger, utcnow

log = get_logger("event_processor")


# ── Record Events ─────────────────────────────────────────
def record_event(
    session: Session,
    email_id: int,
    mailbox_id: int | None,
    event_type: str,
    bounce_type: str | None = None,
    bounce_reason: str | None = None,
    provider_message_id: str | None = None,
) -> MessageEvent:
    """Record a message event in the event pipeline."""
    event = MessageEvent(
        email_id=email_id,
        mailbox_id=mailbox_id,
        event_type=event_type,
        bounce_type=bounce_type,
        bounce_reason=bounce_reason,
        provider_message_id=provider_message_id,
    )
    session.add(event)
    return event


def _update_sending_stats(
    session: Session,
    mailbox_id: int,
    event_type: str,
):
    """Update the hourly sending stats for a mailbox."""
    now = utcnow()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour

    stats = (
        session.query(SendingStats)
        .filter_by(mailbox_id=mailbox_id, date=today, hour=hour)
        .first()
    )
    if not stats:
        stats = SendingStats(mailbox_id=mailbox_id, date=today, hour=hour)
        session.add(stats)

    if event_type == "SENT":
        stats.sent_count = (stats.sent_count or 0) + 1
    elif event_type == "BOUNCED":
        stats.bounce_count = (stats.bounce_count or 0) + 1
    elif event_type == "COMPLAINT":
        stats.complaint_count = (stats.complaint_count or 0) + 1
    elif event_type == "REPLY":
        stats.reply_count = (stats.reply_count or 0) + 1


# ── Send Recording ────────────────────────────────────────
def record_send(
    session: Session,
    email_id: int,
    mailbox_id: int,
    provider_message_id: str | None = None,
):
    """
    Record a successful send event.

    Updates:
        - MessageEvent table
        - SendingStats (daily/hourly counter)
        - Mailbox counters (total_sent, last_sent_at)
    """
    # Record event
    record_event(
        session, email_id, mailbox_id,
        event_type="SENT",
        provider_message_id=provider_message_id,
    )

    # Update stats
    _update_sending_stats(session, mailbox_id, "SENT")

    # Update mailbox counters
    mailbox = session.query(Mailbox).filter_by(id=mailbox_id).first()
    if mailbox:
        mailbox.total_sent = (mailbox.total_sent or 0) + 1
        mailbox.last_sent_at = utcnow()
        mailbox.updated_at = utcnow()


# ── Bounce Processing ────────────────────────────────────
def process_bounce(
    session: Session,
    email_id: int,
    mailbox_id: int | None,
    bounce_type: str,
    reason: str = "",
):
    """
    Process a bounce event.

    For HARD bounces:
        - Add recipient to suppression list
        - Invalidate contact email
    For all bounces:
        - Record event
        - Update mailbox counters
        - Recalculate mailbox health
        - Check circuit breakers
    """
    # Record event
    record_event(
        session, email_id, mailbox_id,
        event_type="BOUNCED",
        bounce_type=bounce_type,
        bounce_reason=reason,
    )

    if mailbox_id:
        _update_sending_stats(session, mailbox_id, "BOUNCED")

    # Get the email record
    email_record = session.query(Email).filter_by(id=email_id).first()
    if not email_record:
        return

    # For hard bounces: suppress and invalidate
    if bounce_type == "HARD":
        contact = session.query(Contact).filter_by(id=email_record.contact_id).first()
        if contact and contact.email:
            _suppress_email(session, contact.email, "HARD_BOUNCE", "bounce_handler")
            contact.verified = "INVALID"
            log.info(f"Hard bounce: suppressed {contact.email}, marked INVALID")

    # Update mailbox counters
    if mailbox_id:
        mailbox = session.query(Mailbox).filter_by(id=mailbox_id).first()
        if mailbox:
            mailbox.total_bounced = (mailbox.total_bounced or 0) + 1
            if bounce_type == "HARD":
                mailbox.total_hard_bounced = (mailbox.total_hard_bounced or 0) + 1
            else:
                mailbox.total_soft_bounced = (mailbox.total_soft_bounced or 0) + 1

            # Recalculate health
            recalculate_mailbox_health(session, mailbox)

            # Recalculate domain health
            recalculate_domain_health(session, mailbox.domain)


# ── Complaint Processing ──────────────────────────────────
def process_complaint(
    session: Session,
    email_id: int,
    mailbox_id: int | None,
):
    """
    Process a spam complaint event.

    - Permanently suppress the recipient
    - Record event
    - Update mailbox counters
    - Recalculate health
    - Check circuit breakers
    """
    # Record event
    record_event(
        session, email_id, mailbox_id,
        event_type="COMPLAINT",
    )

    if mailbox_id:
        _update_sending_stats(session, mailbox_id, "COMPLAINT")

    # Get recipient and suppress
    email_record = session.query(Email).filter_by(id=email_id).first()
    if email_record:
        contact = session.query(Contact).filter_by(id=email_record.contact_id).first()
        if contact and contact.email:
            _suppress_email(session, contact.email, "SPAM_COMPLAINT", "complaint_handler")
            log.info(f"Spam complaint: permanently suppressed {contact.email}")

        # Cancel all pending emails to this contact
        pending = (
            session.query(Email)
            .filter(
                Email.contact_id == email_record.contact_id,
                Email.status.in_(["SCHEDULED", "DRAFT"]),
            )
            .all()
        )
        for pe in pending:
            pe.status = "CANCELLED"
            pe.blocked_reason = "SPAM_COMPLAINT"

    # Update mailbox counters
    if mailbox_id:
        mailbox = session.query(Mailbox).filter_by(id=mailbox_id).first()
        if mailbox:
            mailbox.total_complaints = (mailbox.total_complaints or 0) + 1
            recalculate_mailbox_health(session, mailbox)
            recalculate_domain_health(session, mailbox.domain)


# ── Reply Recording ───────────────────────────────────────
def record_reply(
    session: Session,
    email_id: int,
    mailbox_id: int | None,
):
    """Record a reply event (positive signal for health)."""
    record_event(session, email_id, mailbox_id, event_type="REPLY")

    if mailbox_id:
        _update_sending_stats(session, mailbox_id, "REPLY")
        mailbox = session.query(Mailbox).filter_by(id=mailbox_id).first()
        if mailbox:
            mailbox.total_replies = (mailbox.total_replies or 0) + 1
            recalculate_mailbox_health(session, mailbox)


# ── Health Recalculation ──────────────────────────────────
def recalculate_mailbox_health(session: Session, mailbox: Mailbox):
    """
    Recalculate a mailbox's health metrics from its counters.

    Updates:
        - bounce_rate
        - spam_rate
        - reply_rate
        - reputation_score
    """
    sent = mailbox.total_sent or 0
    if sent == 0:
        return

    # Rates
    mailbox.bounce_rate = (mailbox.total_bounced or 0) / sent
    mailbox.spam_rate = (mailbox.total_complaints or 0) / sent
    mailbox.reply_rate = (mailbox.total_replies or 0) / sent

    # Reputation score
    from policy_engine import calculate_reputation_score
    mailbox.reputation_score = calculate_reputation_score(mailbox)

    # Check warmup transitions
    from policy_engine import apply_warmup_transition
    new_state = apply_warmup_transition(session, mailbox)
    if new_state:
        log.info(f"Mailbox {mailbox.email}: warmup auto-transition → {new_state}")

    # Check circuit breakers
    from policy_engine import check_circuit_breakers
    cb_result = check_circuit_breakers(session, mailbox)
    if cb_result.triggered:
        for breaker, action in zip(cb_result.breakers, cb_result.actions):
            log.warning(f"Circuit breaker: {breaker} → {action}")
            if action == "pause_mailbox":
                mailbox.status = "PAUSED"
                mailbox.warmup_status = "PAUSED"
                log.warning(f"Mailbox {mailbox.email} PAUSED by circuit breaker")

    mailbox.updated_at = utcnow()


def recalculate_domain_health(session: Session, domain: str):
    """
    Recalculate aggregate health for a domain from its mailboxes.
    """
    domain_health = session.query(DomainHealth).filter_by(domain=domain).first()
    if not domain_health:
        # Auto-create domain health record
        domain_health = DomainHealth(domain=domain)
        session.add(domain_health)

    # Aggregate from all mailboxes on this domain
    mailboxes = session.query(Mailbox).filter_by(domain=domain).all()

    total_sent = sum(m.total_sent or 0 for m in mailboxes)
    total_bounced = sum(m.total_bounced or 0 for m in mailboxes)
    total_complaints = sum(m.total_complaints or 0 for m in mailboxes)

    domain_health.total_sent = total_sent
    if total_sent > 0:
        domain_health.bounce_rate = total_bounced / total_sent
        domain_health.spam_rate = total_complaints / total_sent
    else:
        domain_health.bounce_rate = 0.0
        domain_health.spam_rate = 0.0

    # Aggregate reputation (average of mailbox scores)
    active_mailboxes = [m for m in mailboxes if m.is_active]
    if active_mailboxes:
        domain_health.reputation_score = sum(
            m.reputation_score or 50 for m in active_mailboxes
        ) // len(active_mailboxes)

    # Check domain-level circuit breakers
    from policy_engine import check_domain_circuit_breakers
    cb_result = check_domain_circuit_breakers(session, domain)
    if cb_result.triggered:
        for breaker, action in zip(cb_result.breakers, cb_result.actions):
            log.warning(f"Domain circuit breaker: {breaker} → {action}")
            if action in ("pause_domain", "stop_domain"):
                domain_health.status = "DEGRADED"
                log.warning(f"Domain {domain} DEGRADED by circuit breaker")

    domain_health.updated_at = utcnow()


# ── Bulk Health Recalculation ─────────────────────────────
def recalculate_all_health(session: Session):
    """Recalculate health for all active mailboxes and domains. Run periodically."""
    mailboxes = session.query(Mailbox).filter_by(is_active=1).all()
    domains_seen = set()

    for mailbox in mailboxes:
        # Update days_active
        if mailbox.warmup_started_at:
            delta = utcnow() - mailbox.warmup_started_at.replace(tzinfo=timezone.utc)
            mailbox.days_active = max(0, delta.days)

        recalculate_mailbox_health(session, mailbox)
        domains_seen.add(mailbox.domain)

    for domain in domains_seen:
        recalculate_domain_health(session, domain)

    log.info(f"Health recalculated: {len(mailboxes)} mailboxes, {len(domains_seen)} domains")


# ── Internal Helpers ──────────────────────────────────────
def _suppress_email(
    session: Session,
    email_address: str,
    reason: str,
    source: str,
):
    """Internal helper to add to suppression list without circular imports."""
    email_lower = email_address.lower().strip()
    domain = email_lower.split("@")[-1] if "@" in email_lower else None

    existing = (
        session.query(SuppressionList)
        .filter_by(email=email_lower, scope="GLOBAL")
        .first()
    )
    if existing:
        return

    entry = SuppressionList(
        email=email_lower,
        domain=domain,
        reason=reason,
        source=source,
        scope="GLOBAL",
    )
    session.add(entry)
