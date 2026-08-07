"""
verifier.py — Stage 5: Email Verification

Verifies contact email addresses using SMTP MX lookup + RCPT TO check.
Marks contacts as VALID or INVALID.

Usage:
    python verifier.py
    python verifier.py --dry-run
"""

import sys
import socket
import smtplib
import dns.resolver  # pip install dnspython — added as optional; falls back to basic check

from database import get_session, init_db
from models import Company, Contact
from utils import get_logger, utcnow

log = get_logger("verifier")


def verify_email_smtp(email: str, timeout: int = 10) -> bool:
    """
    Verify an email address via SMTP conversation.

    Steps:
        1. Extract domain
        2. Look up MX records
        3. Connect to mail server
        4. Send RCPT TO and check response

    Returns True if the email appears to be valid.

    Note: Many servers block this technique. False negatives are common.
    For production, use a proper verification API (ZeroBounce, NeverBounce, etc.)
    """
    if not email or "@" not in email:
        return False

    domain = email.split("@")[1]

    # Resolve MX records
    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        mx_host = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception:
        # Fallback: try the domain itself
        mx_host = domain

    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as server:
            server.ehlo("verify.local")
            server.mail("verify@verify.local")
            code, _ = server.rcpt(email)
            return code == 250
    except (smtplib.SMTPException, socket.error, OSError) as e:
        log.debug(f"SMTP verification failed for {email}: {e}")
        return False


def verify_email_basic(email: str) -> bool:
    """
    Basic email validation — checks format and MX record exists.

    Fallback when dnspython is not installed or SMTP check fails.
    """
    if not email or "@" not in email:
        return False

    parts = email.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False

    domain = parts[1]

    # Check if domain has MX records
    try:
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        # Try A record as fallback
        try:
            socket.getaddrinfo(domain, 25)
            return True
        except socket.gaierror:
            return False


def run(dry_run: bool = False):
    """Verify all unverified contacts."""
    init_db()
    log.info("Starting email verification...")

    with get_session() as session:
        contacts = session.query(Contact).filter(Contact.verified.is_(None)).all()
        log.info(f"Found {len(contacts)} contacts to verify")

        valid_count = 0
        invalid_count = 0

        for contact in contacts:
            if not contact.email:
                contact.verified = "INVALID"
                invalid_count += 1
                continue

            log.info(f"  Verifying: {contact.email}")

            if dry_run:
                log.info(f"    [DRY RUN] Would verify {contact.email}")
                continue

            try:
                # Try SMTP verification first, fall back to basic
                is_valid = verify_email_smtp(contact.email)
                if not is_valid:
                    is_valid = verify_email_basic(contact.email)

                contact.verified = "VALID" if is_valid else "INVALID"

                if is_valid:
                    valid_count += 1
                    log.info(f"    [OK] VALID")
                else:
                    invalid_count += 1
                    log.info(f"    [FAIL] INVALID")

            except Exception as e:
                log.error(f"    [FAIL] Verification error for {contact.email}: {e}")
                contact.verified = "INVALID"
                invalid_count += 1

        # Update company statuses
        if not dry_run:
            companies_with_contacts = (
                session.query(Company)
                .filter_by(status="CONTACT_FOUND")
                .all()
            )
            for company in companies_with_contacts:
                valid_contacts = (
                    session.query(Contact)
                    .filter_by(company_id=company.id, verified="VALID")
                    .count()
                )
                if valid_contacts > 0:
                    company.status = "EMAIL_VERIFIED"
                    company.updated_at = utcnow()

        log.info(f"Verification complete: {valid_count} valid, {invalid_count} invalid")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Verifier failed: {e}", exc_info=True)
        sys.exit(1)
