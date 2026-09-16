"""
compliance.py — Compliance Engine

Central compliance module for suppression, unsubscribe, and send eligibility.
Every outbound message must pass through this engine before sending.

Suppression check order:
    1. GLOBAL SUPPRESSION (email + domain)
    2. CAMPAIGN SUPPRESSION
    3. ACCOUNT SUPPRESSION
    → SEND or → BLOCK

Usage:
    from compliance import is_suppressed, suppress_email, process_unsubscribe
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from models import SuppressionList, Email, Contact, Campaign
from config import UNSUBSCRIBE_BASE_URL
from utils import get_logger, utcnow

log = get_logger("compliance")

# ── Suppression Reasons ────────────────────────────────────
# These define the behaviour and reversibility of each suppression
PERMANENT_REASONS = frozenset({
    "HARD_BOUNCE",
    "SPAM_COMPLAINT",
    "INVALID_ADDRESS",
})
ADMIN_REVERSIBLE_REASONS = frozenset({
    "UNSUBSCRIBED",
    "DO_NOT_CONTACT",
    "MANUAL_BLOCK",
    "DOMAIN_BLOCKED",
    "LEGAL_REQUEST",
})
ALL_REASONS = PERMANENT_REASONS | ADMIN_REVERSIBLE_REASONS


# ── Data Classes ───────────────────────────────────────────
@dataclass
class ComplianceResult:
    """Result of a compliance check."""
    approved: bool
    reason: str | None = None    # Why it was blocked
    suppression_id: int | None = None  # Which suppression record matched


# ── Unsubscribe Token Management ───────────────────────────
def generate_unsubscribe_token() -> str:
    """Generate a unique unsubscribe token. UUID-based, no email exposure."""
    return uuid.uuid4().hex


def build_unsubscribe_url(token: str) -> str:
    """Build the full unsubscribe URL from base URL + token."""
    base = UNSUBSCRIBE_BASE_URL.rstrip("/")
    return f"{base}/{token}"


# ── Suppression Checks ────────────────────────────────────
def is_suppressed(
    session: Session,
    email_address: str,
    campaign_id: int | None = None,
) -> tuple[bool, str | None]:
    """
    Check if an email address is suppressed.

    Checks in order:
        1. Global email-level suppression
        2. Global domain-level suppression
        3. Campaign-level suppression (if campaign_id given)
        4. Account-level suppression

    Returns:
        (is_suppressed: bool, reason: str | None)
    """
    if not email_address:
        return True, "INVALID_ADDRESS"

    email_lower = email_address.lower().strip()
    domain = email_lower.split("@")[-1] if "@" in email_lower else None

    # 1. Global email-level
    entry = (
        session.query(SuppressionList)
        .filter_by(email=email_lower, scope="GLOBAL")
        .first()
    )
    if entry:
        return True, entry.reason

    # 2. Global domain-level
    if domain:
        domain_entry = (
            session.query(SuppressionList)
            .filter_by(domain=domain, scope="GLOBAL", reason="DOMAIN_BLOCKED")
            .first()
        )
        if domain_entry:
            return True, "DOMAIN_BLOCKED"

    # 3. Campaign-level
    if campaign_id:
        campaign_entry = (
            session.query(SuppressionList)
            .filter_by(email=email_lower, scope="CAMPAIGN", campaign_id=campaign_id)
            .first()
        )
        if campaign_entry:
            return True, campaign_entry.reason

    # 4. Account-level
    account_entry = (
        session.query(SuppressionList)
        .filter_by(email=email_lower, scope="ACCOUNT")
        .first()
    )
    if account_entry:
        return True, account_entry.reason

    return False, None


def check_compliance(
    session: Session,
    email_record: Email,
    contact: Contact,
    campaign: Campaign | None = None,
) -> ComplianceResult:
    """
    Full compliance check for an outbound message.

    Checks:
        1. Recipient suppression (all scopes)
        2. Contact validity
        3. Unsubscribe token present (required for compliance headers)
        4. Body present and reasonable
    """
    # 1. Suppression check
    if not contact or not contact.email:
        return ComplianceResult(approved=False, reason="NO_RECIPIENT_EMAIL")

    campaign_id = campaign.id if campaign else email_record.campaign_id
    suppressed, reason = is_suppressed(session, contact.email, campaign_id)
    if suppressed:
        return ComplianceResult(approved=False, reason=f"SUPPRESSED:{reason}")

    # 2. Contact validity
    if contact.verified == "INVALID":
        return ComplianceResult(approved=False, reason="CONTACT_INVALID")

    # 3. Unsubscribe token
    if not email_record.unsubscribe_token:
        return ComplianceResult(approved=False, reason="NO_UNSUBSCRIBE_TOKEN")

    # 4. Content check
    if not email_record.body or len(email_record.body.strip()) < 10:
        return ComplianceResult(approved=False, reason="EMPTY_OR_SHORT_BODY")

    return ComplianceResult(approved=True)


# ── Suppression Management ─────────────────────────────────
def suppress_email(
    session: Session,
    email_address: str,
    reason: str,
    source: str,
    scope: str = "GLOBAL",
    campaign_id: int | None = None,
) -> SuppressionList | None:
    """
    Add an email to the suppression list.

    Args:
        email_address: The email to suppress
        reason: One of ALL_REASONS
        source: Where the suppression came from ("unsubscribe_link", "bounce_handler", etc.)
        scope: GLOBAL, CAMPAIGN, or ACCOUNT
        campaign_id: Required if scope=CAMPAIGN
    """
    if not email_address:
        return None

    email_lower = email_address.lower().strip()
    domain = email_lower.split("@")[-1] if "@" in email_lower else None

    # Check if already suppressed at this scope
    existing = (
        session.query(SuppressionList)
        .filter_by(email=email_lower, scope=scope, campaign_id=campaign_id)
        .first()
    )
    if existing:
        log.info(f"Already suppressed: {email_lower} (reason={existing.reason}, scope={scope})")
        return existing

    entry = SuppressionList(
        email=email_lower,
        domain=domain,
        reason=reason,
        source=source,
        scope=scope,
        campaign_id=campaign_id,
    )
    session.add(entry)
    log.info(f"Suppressed: {email_lower} (reason={reason}, source={source}, scope={scope})")
    return entry


def suppress_domain(
    session: Session,
    domain: str,
    source: str = "manual",
) -> SuppressionList | None:
    """Block an entire domain from receiving emails."""
    domain_lower = domain.lower().strip()

    existing = (
        session.query(SuppressionList)
        .filter_by(domain=domain_lower, reason="DOMAIN_BLOCKED", scope="GLOBAL")
        .first()
    )
    if existing:
        return existing

    entry = SuppressionList(
        domain=domain_lower,
        reason="DOMAIN_BLOCKED",
        source=source,
        scope="GLOBAL",
    )
    session.add(entry)
    log.info(f"Domain blocked: {domain_lower}")
    return entry


def unsuppress_email(
    session: Session,
    email_address: str,
    scope: str = "GLOBAL",
    campaign_id: int | None = None,
) -> bool:
    """
    Remove an email from the suppression list.
    Only allowed for non-permanent reasons.
    """
    email_lower = email_address.lower().strip()

    entry = (
        session.query(SuppressionList)
        .filter_by(email=email_lower, scope=scope, campaign_id=campaign_id)
        .first()
    )
    if not entry:
        return False

    if entry.reason in PERMANENT_REASONS:
        log.warning(
            f"Cannot unsuppress {email_lower}: reason={entry.reason} is permanent"
        )
        return False

    session.delete(entry)
    log.info(f"Unsuppressed: {email_lower} (was reason={entry.reason})")
    return True


# ── Unsubscribe Processing ─────────────────────────────────
def process_unsubscribe(session: Session, token: str) -> bool:
    """
    Process an unsubscribe request by token.

    1. Find the email record by unsubscribe_token
    2. Add the recipient to GLOBAL suppression
    3. Cancel all pending follow-ups to that contact

    Returns True if processed, False if token not found.
    """
    email_record = (
        session.query(Email)
        .filter_by(unsubscribe_token=token)
        .first()
    )
    if not email_record:
        log.warning(f"Unsubscribe token not found: {token}")
        return False

    contact = (
        session.query(Contact)
        .filter_by(id=email_record.contact_id)
        .first()
    )
    if not contact or not contact.email:
        log.warning(f"No contact email for unsubscribe token: {token}")
        return False

    # Add to global suppression
    suppress_email(
        session,
        contact.email,
        reason="UNSUBSCRIBED",
        source="unsubscribe_link",
        scope="GLOBAL",
    )

    # Cancel all pending/scheduled emails to this contact
    pending = (
        session.query(Email)
        .filter(
            Email.contact_id == contact.id,
            Email.status.in_(["SCHEDULED", "DRAFT"]),
        )
        .all()
    )
    for pending_email in pending:
        pending_email.status = "CANCELLED"
        pending_email.blocked_reason = "RECIPIENT_UNSUBSCRIBED"

    log.info(
        f"Unsubscribe processed: {contact.email} "
        f"(cancelled {len(pending)} pending emails)"
    )
    return True


# ── CSV Import/Export ──────────────────────────────────────
def import_suppression_csv(
    session: Session,
    filepath: str,
    reason: str,
    source: str = "csv_import",
) -> int:
    """
    Import emails from a CSV file into the suppression list.
    CSV should have an 'email' column (or be a single-column list).

    Returns count of newly suppressed emails.
    """
    import csv

    count = 0
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Try to find email column
        fieldnames = reader.fieldnames or []
        email_field = None
        for name in fieldnames:
            if name.lower().strip() in ("email", "email_address", "e-mail"):
                email_field = name
                break

        if not email_field:
            # Assume single-column file
            f.seek(0)
            for line in f:
                addr = line.strip()
                if "@" in addr:
                    entry = suppress_email(session, addr, reason, source)
                    if entry:
                        count += 1
        else:
            for row in reader:
                addr = row.get(email_field, "").strip()
                if addr and "@" in addr:
                    entry = suppress_email(session, addr, reason, source)
                    if entry:
                        count += 1

    log.info(f"CSV import: {count} emails suppressed from {filepath}")
    return count


def export_suppression_csv(session: Session, filepath: str) -> int:
    """Export the full suppression list to CSV. Returns count."""
    import csv

    entries = session.query(SuppressionList).order_by(SuppressionList.created_at.desc()).all()

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["email", "domain", "reason", "source", "scope", "created_at"])
        for e in entries:
            writer.writerow([
                e.email or "",
                e.domain or "",
                e.reason,
                e.source or "",
                e.scope,
                str(e.created_at) if e.created_at else "",
            ])

    log.info(f"CSV export: {len(entries)} entries to {filepath}")
    return len(entries)
