"""
sender.py — Stage 8: Email Sender

Sends scheduled emails via the sending orchestrator.
The sender is now a thin wrapper — all mailbox selection, rate limiting,
suppression checks, and compliance headers are handled by the orchestrator.

Usage:
    python sender.py
    python sender.py --dry-run
"""

import sys
import time

from database import get_session, init_db
from models import Email, Contact, Company, Campaign
from sending_orchestrator import request_send
from config import EMAIL_SEND_DELAY_SECONDS
from utils import get_logger, utcnow

log = get_logger("sender")


def run(dry_run: bool = False):
    """Send all scheduled emails that are due."""
    init_db()
    log.info("Starting email sender...")

    now = utcnow()

    with get_session() as session:
        # Get emails that are scheduled and due
        emails = (
            session.query(Email)
            .filter(
                Email.status == "SCHEDULED",
                Email.scheduled_at <= now,
            )
            .order_by(Email.scheduled_at)
            .all()
        )

        log.info(f"Found {len(emails)} emails to send")

        sent_count = 0
        failed_count = 0
        blocked_count = 0

        for email in emails:
            # Check if company has been replied to (stop follow-ups)
            company = session.query(Company).filter_by(id=email.company_id).first()
            if company and company.status == "REPLIED":
                email.status = "CANCELLED"
                email.blocked_reason = "COMPANY_ALREADY_REPLIED"
                log.info(f"  Cancelled email #{email.id} — company already replied")
                continue

            contact = session.query(Contact).filter_by(id=email.contact_id).first()
            if not contact or not contact.email:
                email.status = "FAILED"
                log.warning(f"  No email address for contact #{email.contact_id}")
                failed_count += 1
                continue

            log.info(
                f"  Sending to {contact.email}: \"{email.subject}\" "
                f"(seq={email.sequence_number})"
            )

            if dry_run:
                log.info(f"    [DRY RUN] Would send email #{email.id}")
                continue

            # Get campaign (if any)
            campaign = None
            if email.campaign_id:
                campaign = session.query(Campaign).filter_by(id=email.campaign_id).first()

            # ── Delegate to the sending orchestrator ──
            result = request_send(session, email, campaign)

            if result.sent:
                sent_count += 1
                log.info(
                    f"    [OK] Sent via {result.mailbox.email} "
                    f"(Message-ID: {result.message_id})"
                )
                # Rate limiting between sends
                if EMAIL_SEND_DELAY_SECONDS > 0:
                    time.sleep(EMAIL_SEND_DELAY_SECONDS)

            elif result.blocked:
                blocked_count += 1
                log.info(f"    [BLOCKED] {result.reason}")

                # If all mailboxes are at their daily cap, stop for today
                if result.reason in ("NO_ELIGIBLE_MAILBOX", "ALL_MAILBOXES_CAPPED"):
                    log.info("All mailboxes at daily cap — stopping sender for this run")
                    break

            else:
                failed_count += 1
                log.error(f"    [FAIL] {result.reason}")

        log.info(
            f"Sending complete: {sent_count} sent, "
            f"{blocked_count} blocked, {failed_count} failed"
        )


def send_test(to_email: str, subject: str | None = None, body: str | None = None):
    """
    Send a test email through the FULL 13-step orchestrator.

    Creates temporary DB records (debug company, contact, email) so that
    the sending pipeline runs exactly as it would for a real outreach email.
    Tests: mailbox selection, compliance, unsubscribe link, rate limits.

    Usage:
        python sender.py --test user@example.com
        python sender.py --test user@example.com --subject "Test" --body "Hello!"
    """
    init_db()
    from models import Company, Contact, Email, Campaign
    from compliance import generate_unsubscribe_token

    subject = subject or "[TEST] Assignme Pipeline Debug Email"
    body = body or (
        "Hi,\n\n"
        "This is a test email from the Assignme sales pipeline.\n\n"
        "If you received this, the following are working:\n"
        "  - Mailbox selection & rotation\n"
        "  - SMTP delivery\n"
        "  - Compliance headers (List-Unsubscribe)\n"
        "  - Unsubscribe link (click below to test)\n\n"
        "This is an automated debug message — no action needed.\n\n"
        "— Assignme Pipeline"
    )

    log.info(f"=== SENDING TEST EMAIL ===")
    log.info(f"  To: {to_email}")
    log.info(f"  Subject: {subject}")

    with get_session() as session:
        # Find or create debug company
        debug_company = session.query(Company).filter_by(name="__DEBUG_TEST__").first()
        if not debug_company:
            debug_company = Company(
                name="__DEBUG_TEST__",
                website="debug.test",
                industry="Debug",
                country="Debug",
                status="RESEARCH_DONE",
            )
            session.add(debug_company)
            session.flush()
            log.info(f"  Created debug company (id={debug_company.id})")

        # Find or create debug contact
        debug_contact = session.query(Contact).filter_by(
            company_id=debug_company.id, email=to_email
        ).first()
        if not debug_contact:
            name_parts = to_email.split("@")[0].replace(".", " ").replace("_", " ").title()
            debug_contact = Contact(
                company_id=debug_company.id,
                name=name_parts,
                role="Debug Recipient",
                email=to_email,
                email_source="DEBUG_TEST",
                verified="DEBUG_TEST",
            )
            session.add(debug_contact)
            session.flush()
            log.info(f"  Created debug contact (id={debug_contact.id})")

        # Create the test email record
        test_email = Email(
            company_id=debug_company.id,
            contact_id=debug_contact.id,
            campaign_id=None,
            sequence_number=0,
            subject=subject,
            body=body,
            status="SCHEDULED",
            scheduled_at=utcnow(),
            unsubscribe_token=generate_unsubscribe_token(),
        )
        session.add(test_email)
        session.flush()
        log.info(f"  Created test email (id={test_email.id})")

        # Push through the full orchestrator
        log.info(f"  Sending through 13-step orchestrator...")
        result = request_send(session, test_email, campaign=None)

        if result.sent:
            log.info(f"  [SUCCESS] Test email sent!")
            log.info(f"    Mailbox: {result.mailbox.email}")
            log.info(f"    Message-ID: {result.message_id}")
            log.info(f"    Unsubscribe token: {test_email.unsubscribe_token}")
            print(f"\n[OK] Test email sent to {to_email} via {result.mailbox.email}")
            print(f"   Message-ID: {result.message_id}")
        elif result.blocked:
            log.warning(f"  [BLOCKED] {result.reason}")
            print(f"\n[BLOCKED] Test email BLOCKED: {result.reason}")
        else:
            log.error(f"  [FAILED] {result.reason}")
            print(f"\n[FAILED] Test email FAILED: {result.reason}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv

    if "--test" in sys.argv:
        # Parse: python sender.py --test email@example.com [--subject "..."] [--body "..."]
        args = sys.argv[:]
        test_idx = args.index("--test")
        if test_idx + 1 >= len(args) or args[test_idx + 1].startswith("--"):
            print("Usage: python sender.py --test <email> [--subject <text>] [--body <text>]")
            sys.exit(1)
        to_email = args[test_idx + 1]

        subject = None
        body = None
        if "--subject" in args:
            si = args.index("--subject")
            if si + 1 < len(args):
                subject = args[si + 1]
        if "--body" in args:
            bi = args.index("--body")
            if bi + 1 < len(args):
                body = args[bi + 1]

        try:
            send_test(to_email, subject=subject, body=body)
        except Exception as e:
            log.error(f"Test send failed: {e}", exc_info=True)
            sys.exit(1)
    else:
        try:
            run(dry_run=dry_run)
        except Exception as e:
            log.error(f"Sender failed: {e}", exc_info=True)
            sys.exit(1)
