"""
policy_engine.py — Policy Engine

Dynamic send rate calculation, warmup state machine, and circuit breakers.
No hard-coded daily caps — effective limits are computed from mailbox health,
warmup state, bounce rate, spam rate, and domain reputation.

Warmup state machine:
    WARMUP_PENDING → WARMUP_ACTIVE → ESTABLISHED → HEALTHY
    HEALTHY → DEGRADED → PAUSED → RECOVERY → ESTABLISHED

Circuit breakers:
    hard_bounce_rate > 5%       → pause mailbox
    spam_rate >= 0.3%           → pause domain (Google critical)
    spam_rate >= 0.1%           → reduce volume 50% (Google warning)
    bounce_rate > 10%           → pause mailbox
    dns_auth_fails              → stop domain

Usage:
    from policy_engine import calculate_effective_daily_limit, check_circuit_breakers
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Mailbox, DomainHealth, SendingStats, MessageEvent
from config import (
    SPAM_RATE_HEALTHY, SPAM_RATE_WARNING,
    HARD_BOUNCE_PAUSE_THRESHOLD, DOMAIN_PAUSE_SPAM_THRESHOLD,
    BOUNCE_RATE_WARNING, BOUNCE_RATE_CRITICAL,
)
from utils import get_logger, utcnow

log = get_logger("policy_engine")


# ── Warmup State Machine ──────────────────────────────────
# Base daily caps per warmup phase (before policy adjustments)
WARMUP_BASE_CAPS = {
    "WARMUP_PENDING":   0,    # no sending
    "WARMUP_ACTIVE":    5,    # start slow
    "ESTABLISHED":      20,   # building reputation
    "HEALTHY":          40,   # full capacity (adjusted by metrics)
    "DEGRADED":         8,    # reduced while investigating
    "PAUSED":           0,    # no sending
    "RECOVERY":         8,    # slow restart
}

# Transition conditions
WARMUP_TRANSITIONS = {
    # (from_state, to_state): condition description (checked in evaluate_warmup_transition)
    ("WARMUP_PENDING", "WARMUP_ACTIVE"):   "manual activation or first send",
    ("WARMUP_ACTIVE", "ESTABLISHED"):      "days_active >= 7 AND total_sent >= 30 AND bounce_rate < 3%",
    ("ESTABLISHED", "HEALTHY"):            "days_active >= 21 AND total_sent >= 150 AND bounce_rate < 2% AND spam_rate < 0.1%",
    ("HEALTHY", "DEGRADED"):               "bounce_rate > 5% OR spam_rate >= 0.1%",
    ("ESTABLISHED", "DEGRADED"):           "bounce_rate > 5% OR spam_rate >= 0.1%",
    ("DEGRADED", "PAUSED"):                "bounce_rate > 10% OR spam_rate >= 0.3%",
    ("PAUSED", "RECOVERY"):                "manual resume by admin",
    ("RECOVERY", "ESTABLISHED"):           "7 days with bounce_rate < 3% AND spam_rate < 0.1%",
}


# ── Data Classes ──────────────────────────────────────────
@dataclass
class CircuitBreakerResult:
    """Result of a circuit breaker check."""
    triggered: bool
    breakers: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


@dataclass
class MailboxPolicy:
    """Calculated policy for a mailbox."""
    effective_daily_limit: int
    effective_hourly_limit: int
    can_send: bool
    reason: str | None = None      # why can't send
    volume_multiplier: float = 1.0  # 0.5 = half speed, 1.0 = normal


# ── Daily Send Count ──────────────────────────────────────
def get_sent_today(session: Session, mailbox_id: int) -> int:
    """Get the number of emails sent today by a specific mailbox."""
    today = utcnow().strftime("%Y-%m-%d")
    result = (
        session.query(func.coalesce(func.sum(SendingStats.sent_count), 0))
        .filter_by(mailbox_id=mailbox_id, date=today)
        .scalar()
    )
    return int(result)


def get_sent_this_hour(session: Session, mailbox_id: int) -> int:
    """Get the number of emails sent this hour by a specific mailbox."""
    now = utcnow()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour
    result = (
        session.query(func.coalesce(func.sum(SendingStats.sent_count), 0))
        .filter_by(mailbox_id=mailbox_id, date=today, hour=hour)
        .scalar()
    )
    return int(result)


# ── Effective Daily Limit Calculation ──────────────────────
def calculate_effective_daily_limit(session: Session, mailbox: Mailbox) -> int:
    """
    Calculate the effective daily sending limit for a mailbox.

    Factors:
        - warmup_status (base cap from state machine phase)
        - days_active
        - reputation_score (0-100)
        - bounce_rate (recent)
        - spam_rate (recent)
        - historical delivery success

    Returns the maximum number of emails this mailbox should send today.
    """
    base_cap = WARMUP_BASE_CAPS.get(mailbox.warmup_status, 0)

    if base_cap == 0:
        return 0

    # ── Reputation multiplier ──
    # reputation_score 0-100 maps to 0.0-1.3x multiplier
    rep = mailbox.reputation_score or 50
    if rep >= 80:
        rep_mult = 1.3
    elif rep >= 60:
        rep_mult = 1.0
    elif rep >= 40:
        rep_mult = 0.7
    elif rep >= 20:
        rep_mult = 0.4
    else:
        rep_mult = 0.1

    # ── Bounce rate penalty ──
    bounce = mailbox.bounce_rate or 0.0
    if bounce >= BOUNCE_RATE_CRITICAL:
        bounce_mult = 0.0  # stop
    elif bounce >= BOUNCE_RATE_WARNING:
        bounce_mult = 0.3  # heavy reduction
    elif bounce >= 0.02:  # 2%
        bounce_mult = 0.7  # moderate reduction
    else:
        bounce_mult = 1.0

    # ── Spam rate penalty (Google tiered thresholds) ──
    spam = mailbox.spam_rate or 0.0
    if spam >= SPAM_RATE_WARNING:  # >= 0.3% → critical
        spam_mult = 0.0  # stop sending
    elif spam >= SPAM_RATE_HEALTHY:  # >= 0.1% → warning
        spam_mult = 0.5  # reduce by half
    else:
        spam_mult = 1.0  # healthy

    # ── Days active bonus ──
    # Gradual ramp-up over the first 30 days
    days = mailbox.days_active or 0
    if days >= 30:
        age_mult = 1.2
    elif days >= 14:
        age_mult = 1.0
    elif days >= 7:
        age_mult = 0.8
    else:
        age_mult = 0.6

    # Combine multipliers
    effective = base_cap * rep_mult * bounce_mult * spam_mult * age_mult
    return max(0, int(effective))


def calculate_effective_hourly_limit(daily_limit: int) -> int:
    """Spread daily limit across business hours (8 hours)."""
    if daily_limit <= 0:
        return 0
    # Divide daily limit across 8 business hours, minimum 1 per hour
    return max(1, daily_limit // 8)


# ── Mailbox Policy Calculation ─────────────────────────────
def get_mailbox_policy(session: Session, mailbox: Mailbox) -> MailboxPolicy:
    """
    Calculate the full policy for a mailbox.

    Returns a MailboxPolicy with effective limits and whether sending is allowed.
    """
    # Check if mailbox is active
    if not mailbox.is_active:
        return MailboxPolicy(
            effective_daily_limit=0,
            effective_hourly_limit=0,
            can_send=False,
            reason="MAILBOX_DISABLED",
        )

    if mailbox.status == "PAUSED":
        return MailboxPolicy(
            effective_daily_limit=0,
            effective_hourly_limit=0,
            can_send=False,
            reason="MAILBOX_PAUSED",
        )

    if mailbox.status == "DISABLED":
        return MailboxPolicy(
            effective_daily_limit=0,
            effective_hourly_limit=0,
            can_send=False,
            reason="MAILBOX_DISABLED",
        )

    daily_limit = calculate_effective_daily_limit(session, mailbox)
    hourly_limit = calculate_effective_hourly_limit(daily_limit)

    if daily_limit <= 0:
        return MailboxPolicy(
            effective_daily_limit=0,
            effective_hourly_limit=0,
            can_send=False,
            reason=f"WARMUP_STATUS_{mailbox.warmup_status}",
        )

    # Check if already at cap
    sent_today = get_sent_today(session, mailbox.id)
    sent_hour = get_sent_this_hour(session, mailbox.id)

    if sent_today >= daily_limit:
        return MailboxPolicy(
            effective_daily_limit=daily_limit,
            effective_hourly_limit=hourly_limit,
            can_send=False,
            reason="DAILY_CAP_REACHED",
        )

    if sent_hour >= hourly_limit:
        return MailboxPolicy(
            effective_daily_limit=daily_limit,
            effective_hourly_limit=hourly_limit,
            can_send=False,
            reason="HOURLY_CAP_REACHED",
        )

    return MailboxPolicy(
        effective_daily_limit=daily_limit,
        effective_hourly_limit=hourly_limit,
        can_send=True,
    )


# ── Circuit Breakers ──────────────────────────────────────
def check_circuit_breakers(
    session: Session,
    mailbox: Mailbox,
) -> CircuitBreakerResult:
    """
    Check all circuit breakers for a mailbox.

    Returns which breakers were triggered and recommended actions.
    """
    breakers = []
    actions = []

    bounce = mailbox.bounce_rate or 0.0
    spam = mailbox.spam_rate or 0.0
    hard_bounce = (mailbox.total_hard_bounced or 0) / max(mailbox.total_sent or 1, 1)

    # Hard bounce rate > threshold → pause mailbox
    if hard_bounce > HARD_BOUNCE_PAUSE_THRESHOLD:
        breakers.append(f"hard_bounce_rate={hard_bounce:.1%} > {HARD_BOUNCE_PAUSE_THRESHOLD:.1%}")
        actions.append("pause_mailbox")

    # Spam rate >= 0.3% → pause domain (Google critical)
    if spam >= SPAM_RATE_WARNING:
        breakers.append(f"spam_rate={spam:.3%} >= {SPAM_RATE_WARNING:.3%} (CRITICAL)")
        actions.append("pause_domain")

    # Spam rate >= 0.1% → reduce volume (Google warning)
    elif spam >= SPAM_RATE_HEALTHY:
        breakers.append(f"spam_rate={spam:.3%} >= {SPAM_RATE_HEALTHY:.3%} (WARNING)")
        actions.append("reduce_volume_50%")

    # Bounce rate > critical → pause mailbox
    if bounce >= BOUNCE_RATE_CRITICAL:
        breakers.append(f"bounce_rate={bounce:.1%} >= {BOUNCE_RATE_CRITICAL:.1%}")
        actions.append("pause_mailbox")

    # Bounce rate > warning → reduce volume
    elif bounce >= BOUNCE_RATE_WARNING:
        breakers.append(f"bounce_rate={bounce:.1%} >= {BOUNCE_RATE_WARNING:.1%}")
        actions.append("reduce_volume")

    return CircuitBreakerResult(
        triggered=len(breakers) > 0,
        breakers=breakers,
        actions=actions,
    )


def check_domain_circuit_breakers(
    session: Session,
    domain: str,
) -> CircuitBreakerResult:
    """Check circuit breakers at the domain level."""
    domain_health = (
        session.query(DomainHealth)
        .filter_by(domain=domain)
        .first()
    )
    if not domain_health:
        return CircuitBreakerResult(triggered=False)

    breakers = []
    actions = []

    # Domain-level spam rate
    spam = domain_health.spam_rate or 0.0
    if spam >= DOMAIN_PAUSE_SPAM_THRESHOLD:
        breakers.append(f"domain_spam_rate={spam:.3%} >= {DOMAIN_PAUSE_SPAM_THRESHOLD:.3%}")
        actions.append("pause_domain")

    # DNS authentication failure
    if domain_health.status == "PENDING_VERIFICATION":
        breakers.append("dns_not_verified")
        actions.append("stop_domain")

    if domain_health.status == "BLOCKED":
        breakers.append("domain_blocked")
        actions.append("stop_domain")

    return CircuitBreakerResult(
        triggered=len(breakers) > 0,
        breakers=breakers,
        actions=actions,
    )


# ── Warmup State Machine Transitions ──────────────────────
def evaluate_warmup_transition(session: Session, mailbox: Mailbox) -> str | None:
    """
    Evaluate if a mailbox should transition to a new warmup state.

    Returns the new state, or None if no transition should occur.
    Does NOT apply the transition — caller must update the mailbox.
    """
    current = mailbox.warmup_status
    bounce = mailbox.bounce_rate or 0.0
    spam = mailbox.spam_rate or 0.0
    days = mailbox.days_active or 0
    sent = mailbox.total_sent or 0

    # WARMUP_ACTIVE → ESTABLISHED
    if current == "WARMUP_ACTIVE":
        if days >= 7 and sent >= 30 and bounce < 0.03:
            return "ESTABLISHED"

    # ESTABLISHED → HEALTHY
    elif current == "ESTABLISHED":
        if days >= 21 and sent >= 150 and bounce < 0.02 and spam < SPAM_RATE_HEALTHY:
            return "HEALTHY"
        # ESTABLISHED → DEGRADED
        if bounce > HARD_BOUNCE_PAUSE_THRESHOLD or spam >= SPAM_RATE_HEALTHY:
            return "DEGRADED"

    # HEALTHY → DEGRADED
    elif current == "HEALTHY":
        if bounce > HARD_BOUNCE_PAUSE_THRESHOLD or spam >= SPAM_RATE_HEALTHY:
            return "DEGRADED"

    # DEGRADED → PAUSED
    elif current == "DEGRADED":
        if bounce >= BOUNCE_RATE_CRITICAL or spam >= SPAM_RATE_WARNING:
            return "PAUSED"

    # RECOVERY → ESTABLISHED (after 7 days of good metrics)
    elif current == "RECOVERY":
        if days >= 7 and bounce < 0.03 and spam < SPAM_RATE_HEALTHY:
            return "ESTABLISHED"

    return None


def apply_warmup_transition(
    session: Session,
    mailbox: Mailbox,
    force_state: str | None = None,
) -> str | None:
    """
    Evaluate and apply warmup state transition for a mailbox.

    If force_state is provided, skip evaluation and force the transition
    (used for manual admin overrides like PAUSED → RECOVERY).

    Returns the new state, or None if no transition occurred.
    """
    old_state = mailbox.warmup_status

    if force_state:
        new_state = force_state
    else:
        new_state = evaluate_warmup_transition(session, mailbox)

    if new_state and new_state != old_state:
        mailbox.warmup_status = new_state
        mailbox.updated_at = utcnow()

        if new_state == "WARMUP_ACTIVE" and not mailbox.warmup_started_at:
            mailbox.warmup_started_at = utcnow()

        if new_state == "PAUSED":
            mailbox.status = "PAUSED"

        log.info(
            f"Mailbox {mailbox.email}: warmup transition "
            f"{old_state} → {new_state}"
        )
        return new_state

    return None


# ── Reputation Score Calculation ──────────────────────────
def calculate_reputation_score(mailbox: Mailbox) -> int:
    """
    Calculate an operational risk score (0-100) for a mailbox.

    This is OUR score, not Google's internal reputation score.
    Higher is better.
    """
    score = 50  # base score

    sent = mailbox.total_sent or 0
    if sent == 0:
        return score

    # Delivery rate contribution (+0 to +25)
    delivered = mailbox.total_delivered or 0
    delivery_rate = delivered / max(sent, 1)
    score += int(delivery_rate * 25)

    # Bounce rate penalty (-0 to -30)
    bounce = mailbox.bounce_rate or 0.0
    if bounce >= 0.10:
        score -= 30
    elif bounce >= 0.05:
        score -= 20
    elif bounce >= 0.03:
        score -= 10
    elif bounce >= 0.01:
        score -= 5

    # Spam complaint penalty (-0 to -40)
    spam = mailbox.spam_rate or 0.0
    if spam >= 0.003:  # >= 0.3%
        score -= 40
    elif spam >= 0.001:  # >= 0.1%
        score -= 25
    elif spam > 0:
        score -= 5

    # Reply rate bonus (+0 to +15)
    reply = mailbox.reply_rate or 0.0
    if reply >= 0.10:  # 10%+ reply rate
        score += 15
    elif reply >= 0.05:
        score += 10
    elif reply >= 0.02:
        score += 5

    # Age bonus (+0 to +10)
    days = mailbox.days_active or 0
    if days >= 30:
        score += 10
    elif days >= 14:
        score += 5

    return max(0, min(100, score))
