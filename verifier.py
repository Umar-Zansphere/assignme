"""
verifier.py — Stage 5: Email Verification (Simplified)

With Prospeo handling email finding + verification, this stage is now
a lightweight gate that:

  1. Trusts Prospeo-sourced contacts (PROSPEO_VERIFIED, PROSPEO_CATCH_ALL)
  2. Runs basic sanity checks (format + MX record) on any other sources
  3. Advances companies to EMAIL_VERIFIED status

Verification tiers:
  PROSPEO_VERIFIED  — Prospeo found and verified the email (high confidence)
  PROSPEO_CATCH_ALL — Prospeo found the email but domain is catch-all (medium)
  PATTERN_ACCEPTED  — Format valid + MX record exists (basic sanity)
  INVALID           — Bad format, no MX record, or no email at all

Usage:
    python verifier.py
    python verifier.py --dry-run
"""

import re
import sys
import socket

try:
    import dns.resolver
    _HAS_DNS = True
except ImportError:
    _HAS_DNS = False

from database import get_session, init_db
from models import Company, Contact
from utils import get_logger, utcnow

log = get_logger("verifier")

# Statuses from Prospeo that are already verified — skip re-verification
_ALREADY_VERIFIED = {
    "PROSPEO_VERIFIED", "PROSPEO_CATCH_ALL",
    "FULLENRICH_VERIFIED", "FULLENRICH_CATCH_ALL",
    "APIFY_VERIFIED", "SMTP_VERIFIED", "WEB_SCRAPED",
}
# All statuses that can proceed to email writing
_USABLE = _ALREADY_VERIFIED | {"VALID", "PATTERN_ACCEPTED"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _format_ok(email: str) -> bool:
    """Basic regex format check."""
    return bool(email and _EMAIL_RE.match(email.strip()))


def _mx_exists(domain: str) -> bool:
    """Return True if the domain has at least one MX or A record."""
    if _HAS_DNS:
        try:
            dns.resolver.resolve(domain, "MX")
            return True
        except Exception:
            pass
        try:
            dns.resolver.resolve(domain, "A")
            return True
        except Exception:
            return False
    else:
        # Fallback: plain socket lookup
        try:
            socket.getaddrinfo(domain, 25)
            return True
        except socket.gaierror:
            return False


def verify_contact(email: str) -> str:
    """
    Run basic verification checks on an email address.

    This is only called for contacts NOT sourced from Prospeo.
    Prospeo-sourced contacts are trusted without re-verification.

    Decision tree:
      1. Bad format                  → INVALID
      2. Domain has no MX / A record → INVALID
      3. Format OK + MX exists       → PATTERN_ACCEPTED (proceed with caution)
    """
    email = (email or "").strip().lower()

    if not _format_ok(email):
        return "INVALID"

    domain = email.split("@")[1]
    if not _mx_exists(domain):
        log.debug(f"No MX/A record for domain '{domain}' — INVALID")
        return "INVALID"

    # Domain is real, format is valid — accept with medium confidence
    return "PATTERN_ACCEPTED"


def run(dry_run: bool = False, target_campaign_id: int | None = None):
    """Verify all unverified contacts using tiered trust model."""
    init_db()
    log.info("Starting email verification (Prospeo trust model)...")

    with get_session() as session:
        # Only verify contacts from active campaigns
        from utils import get_active_campaign_ids
        active_ids = get_active_campaign_ids(session)
        if target_campaign_id:
            if target_campaign_id not in active_ids:
                log.info(f"Campaign {target_campaign_id} is not active. Skipping.")
                return
            active_ids = [target_campaign_id]
        
        # Skip contacts already verified by a high-confidence source
        contacts = session.query(Contact).join(
            Company, Contact.company_id == Company.id
        ).filter(
            Contact.verified.notin_(list(_ALREADY_VERIFIED)),
            Contact.email.isnot(None),
            Company.campaign_id.in_(active_ids),
        ).all()

        pre_verified = session.query(Contact).filter(
            Contact.verified.in_(list(_ALREADY_VERIFIED))
        ).count()

        log.info(
            f"Found {len(contacts)} contacts to verify, "
            f"{pre_verified} already pre-verified (skipped)"
        )

        pattern_accepted = 0
        invalid_count = 0

        for contact in contacts:
            if not contact.email:
                contact.verified = "INVALID"
                invalid_count += 1
                continue

            log.info(f"  Verifying: {contact.email} (source: {contact.email_source or 'unknown'})")

            if dry_run:
                log.info(f"    [DRY RUN] Would verify {contact.email}")
                continue

            # Contacts marked NOT_FOUND have no email to verify
            if contact.email_source == "NOT_FOUND" or contact.verified == "NOT_FOUND":
                contact.verified = "INVALID"
                invalid_count += 1
                log.info(f"    ✗ INVALID (no verified email found)")
                continue

            result = verify_contact(contact.email)
            contact.verified = result

            if result == "PATTERN_ACCEPTED":
                pattern_accepted += 1
                log.info(f"    ~ PATTERN_ACCEPTED (MX exists, will proceed)")
            else:
                invalid_count += 1
                log.info(f"    ✗ INVALID (bad format or dead domain)")

        # Advance companies: any company with a usable contact → EMAIL_VERIFIED
        if not dry_run:
            companies = (
                session.query(Company)
                .filter_by(status="CONTACT_FOUND")
                .all()
            )
            advanced = 0
            for company in companies:
                usable = (
                    session.query(Contact)
                    .filter(
                        Contact.company_id == company.id,
                        Contact.verified.in_(list(_USABLE)),
                    )
                    .count()
                )
                if usable > 0:
                    company.status = "EMAIL_VERIFIED"
                    company.updated_at = utcnow()
                    advanced += 1
                    log.info(
                        f"  '{company.name}' → EMAIL_VERIFIED "
                        f"({usable} usable contact(s))"
                    )

            log.info(f"  Advanced {advanced} companies to EMAIL_VERIFIED")

        log.info(
            f"Verification complete: "
            f"{pattern_accepted} PATTERN_ACCEPTED, "
            f"{invalid_count} INVALID, "
            f"{pre_verified} pre-verified (skipped)"
        )


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    target_campaign_id = None
    for arg in sys.argv:
        if arg.startswith("--campaign-id="):
            target_campaign_id = int(arg.split("=")[1])
            
    try:
        run(dry_run=dry_run, target_campaign_id=target_campaign_id)
    except Exception as e:
        log.error(f"Verifier failed: {e}", exc_info=True)
        sys.exit(1)
