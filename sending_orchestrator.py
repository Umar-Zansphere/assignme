"""
sending_orchestrator.py — Sending Orchestrator

Central control plane for all outbound email. Every message passes through
the 13-step pipeline before it touches an SMTP connection.

Pipeline:
     1. Is recipient suppressed?
     2. Is recipient address valid?
     3. Is campaign active?
     4. Is sending allowed at this time?
     5. Compliance check (content requirements)
     6. Select eligible mailbox (rotation + health)
     7. Has mailbox exceeded daily limit?
     8. Has mailbox exceeded hourly limit?
     9. Has domain exceeded its aggregate limit?
    10. Has bounce threshold been exceeded? (circuit breaker)
    11. Has spam threshold been exceeded? (circuit breaker)
    12. Send via provider adapter
    13. Record event + update counters

Usage:
    from sending_orchestrator import request_send
    result = request_send(session, email, campaign)
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from models import (
    Email, Contact, Company, Campaign, Mailbox, DomainHealth,
)
from compliance import check_compliance, build_unsubscribe_url
from policy_engine import (
    get_mailbox_policy, check_circuit_breakers, check_domain_circuit_breakers,
)
from provider_adapter import get_provider, PreparedMessage, SendResult
from event_processor import record_send, process_bounce
from config import BLOCKED_SENDING_DOMAINS, UNSUBSCRIBE_BASE_URL
from utils import get_logger, utcnow

log = get_logger("orchestrator")


# ── Unsubscribe footer template ───────────────────────────
UNSUBSCRIBE_FOOTER_PLAIN = (
    "\n\n---\n"
    "Don't want to hear from me? Unsubscribe: {url}"
)
UNSUBSCRIBE_FOOTER_HTML = (
    '<br><br><hr style="border:none;border-top:1px solid #ccc;margin:20px 0">'
    '<p style="font-size:11px;color:#999">'
    "Don't want to hear from me? "
    '<a href="{url}" style="color:#999">Unsubscribe</a></p>'
)


@dataclass
class OrchestratorResult:
    """Result of a send request through the orchestrator."""
    sent: bool = False
    blocked: bool = False
    reason: str | None = None
    mailbox: Mailbox | None = None
    message_id: str | None = None
    provider_result: SendResult | None = None


# ── Mailbox Selection ─────────────────────────────────────
def _select_mailbox(session: Session) -> Mailbox | None:
    """
    Select the next eligible mailbox using round-robin rotation.

    Eligible = active, not paused, warmup allows sending, under daily cap,
    and domain is not blocked.
    """
    mailboxes = (
        session.query(Mailbox)
        .filter_by(is_active=1)
        .filter(Mailbox.status != "PAUSED")
        .filter(Mailbox.status != "DISABLED")
        .filter(Mailbox.warmup_status != "WARMUP_PENDING")
        .filter(Mailbox.warmup_status != "PAUSED")
        .order_by(Mailbox.last_sent_at.asc().nullsfirst())  # round-robin by least recently used
        .all()
    )

    if not mailboxes:
        return None

    for mailbox in mailboxes:
        # Skip blocked domains
        if mailbox.domain in BLOCKED_SENDING_DOMAINS:
            log.warning(f"Skipping mailbox {mailbox.email}: domain {mailbox.domain} is blocked")
            continue

        # Check domain health
        domain_health = (
            session.query(DomainHealth)
            .filter_by(domain=mailbox.domain)
            .first()
        )
        if domain_health and domain_health.status in ("BLOCKED", "DEGRADED"):
            log.warning(f"Skipping mailbox {mailbox.email}: domain {mailbox.domain} status={domain_health.status}")
            continue

        # Check policy
        policy = get_mailbox_policy(session, mailbox)
        if policy.can_send:
            return mailbox

    return None


# ── The 13-Step Pipeline ──────────────────────────────────
def request_send(
    session: Session,
    email_record: Email,
    campaign: Campaign | None = None,
) -> OrchestratorResult:
    """
    Central send pipeline. Every outbound message goes through this.

    Returns an OrchestratorResult indicating whether the message was sent,
    blocked, or failed, and why.
    """

    # ── Step 1: Get contact ──
    contact = session.query(Contact).filter_by(id=email_record.contact_id).first()
    if not contact or not contact.email:
        email_record.status = "BLOCKED"
        email_record.blocked_reason = "NO_RECIPIENT_EMAIL"
        return OrchestratorResult(blocked=True, reason="NO_RECIPIENT_EMAIL")

    # ── Step 2: Is recipient address valid? ──
    if contact.verified == "INVALID":
        email_record.status = "BLOCKED"
        email_record.blocked_reason = "CONTACT_INVALID"
        return OrchestratorResult(blocked=True, reason="CONTACT_INVALID")

    # ── Step 3: Is campaign active? ──
    if campaign and not campaign.is_active:
        email_record.status = "BLOCKED"
        email_record.blocked_reason = "CAMPAIGN_INACTIVE"
        return OrchestratorResult(blocked=True, reason="CAMPAIGN_INACTIVE")

    # ── Step 4: Sending time check ──
    # (For now, always allowed — can add business hours logic later)

    # ── Step 5: Compliance check ──
    compliance_result = check_compliance(session, email_record, contact, campaign)
    if not compliance_result.approved:
        email_record.status = "BLOCKED"
        email_record.blocked_reason = compliance_result.reason
        log.info(f"Compliance blocked email #{email_record.id}: {compliance_result.reason}")
        return OrchestratorResult(blocked=True, reason=compliance_result.reason)

    # ── Step 6: Select eligible mailbox ──
    mailbox = _select_mailbox(session)
    if not mailbox:
        # Don't mark as BLOCKED — it can retry when mailboxes are available
        log.info(f"No eligible mailbox for email #{email_record.id}")
        return OrchestratorResult(blocked=True, reason="NO_ELIGIBLE_MAILBOX")

    # ── Step 7: Daily limit check ──
    policy = get_mailbox_policy(session, mailbox)
    if not policy.can_send:
        log.info(f"Mailbox {mailbox.email} cannot send: {policy.reason}")
        return OrchestratorResult(blocked=True, reason=f"MAILBOX_{policy.reason}")

    # ── Step 8: Hourly limit (already checked in policy) ──

    # ── Step 9: Domain aggregate limit ──
    domain_cb = check_domain_circuit_breakers(session, mailbox.domain)
    if domain_cb.triggered:
        log.warning(f"Domain {mailbox.domain} circuit breaker: {domain_cb.breakers}")
        return OrchestratorResult(blocked=True, reason=f"DOMAIN_CIRCUIT_BREAKER:{','.join(domain_cb.actions)}")

    # ── Step 10: Bounce threshold (circuit breaker) ──
    # ── Step 11: Spam threshold (circuit breaker) ──
    mb_cb = check_circuit_breakers(session, mailbox)
    if mb_cb.triggered:
        log.warning(f"Mailbox {mailbox.email} circuit breaker: {mb_cb.breakers}")
        # Apply circuit breaker actions
        for action in mb_cb.actions:
            if action == "pause_mailbox":
                mailbox.status = "PAUSED"
                mailbox.warmup_status = "PAUSED"
        return OrchestratorResult(blocked=True, reason=f"MAILBOX_CIRCUIT_BREAKER:{','.join(mb_cb.actions)}")

    # ── Step 12: Send via provider adapter ──
    # Build unsubscribe URL
    unsub_url = build_unsubscribe_url(email_record.unsubscribe_token)

    # Build body with unsubscribe footer
    body_plain = email_record.body + UNSUBSCRIBE_FOOTER_PLAIN.format(url=unsub_url)
    body_html_raw = email_record.body.replace("\n", "<br>")
    body_html = f"<html><body><p>{body_html_raw}</p>{UNSUBSCRIBE_FOOTER_HTML.format(url=unsub_url)}</body></html>"

    # Build threading headers for follow-ups
    reply_to_header = None
    references_header = None
    if email_record.sequence_number > 0:
        initial = (
            session.query(Email)
            .filter_by(
                company_id=email_record.company_id,
                contact_id=email_record.contact_id,
                sequence_number=0,
            )
            .first()
        )
        if initial and initial.message_id:
            reply_to_header = initial.message_id
            references_header = initial.message_id

    message = PreparedMessage(
        to_email=contact.email,
        from_email=mailbox.email,
        from_name=mailbox.display_name or mailbox.email.split("@")[0],
        subject=email_record.subject,
        body_plain=body_plain,
        body_html=body_html,
        reply_to_header=reply_to_header,
        references_header=references_header,
        list_unsubscribe=unsub_url,
        list_unsubscribe_post="List-Unsubscribe=One-Click",
    )

    provider = get_provider(mailbox)
    send_result = provider.send(message)

    # ── Step 13: Record event + update counters ──
    if send_result.success:
        email_record.status = "SENT"
        email_record.sent_at = utcnow()
        email_record.message_id = send_result.message_id
        email_record.mailbox_id = mailbox.id

        # Record send event
        record_send(
            session,
            email_id=email_record.id,
            mailbox_id=mailbox.id,
            provider_message_id=send_result.provider_message_id,
        )

        # Update company status
        company = session.query(Company).filter_by(id=email_record.company_id).first()
        if company and company.status in ("EMAIL_READY", "EMAIL_SENT"):
            company.status = "EMAIL_SENT"
            company.updated_at = utcnow()

        # Auto-activate warmup if first send from this mailbox
        if mailbox.warmup_status == "WARMUP_PENDING":
            from policy_engine import apply_warmup_transition
            apply_warmup_transition(session, mailbox, force_state="WARMUP_ACTIVE")

        log.info(
            f"[OK] Email #{email_record.id} sent via {mailbox.email} "
            f"to {contact.email} (Message-ID: {send_result.message_id})"
        )

        return OrchestratorResult(
            sent=True,
            mailbox=mailbox,
            message_id=send_result.message_id,
            provider_result=send_result,
        )

    else:
        # Send failed
        email_record.status = "FAILED"
        email_record.mailbox_id = mailbox.id

        # Process bounce if applicable
        if send_result.bounce_type:
            process_bounce(
                session,
                email_id=email_record.id,
                mailbox_id=mailbox.id,
                bounce_type=send_result.bounce_type,
                reason=send_result.error or "",
            )

        log.error(
            f"[FAIL] Email #{email_record.id} via {mailbox.email}: "
            f"{send_result.error}"
        )

        return OrchestratorResult(
            sent=False,
            blocked=False,
            reason=f"SEND_FAILED:{send_result.error}",
            mailbox=mailbox,
            provider_result=send_result,
        )
