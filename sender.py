"""
sender.py — Stage 8: Email Sender

Sends scheduled emails via SMTP.
Tracks sent time and Message-ID for reply detection.

Usage:
    python sender.py
    python sender.py --dry-run
"""

import sys
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid, formataddr

from database import get_session, init_db
from models import Email, Contact, Company
from config import (
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    SMTP_FROM_NAME, SMTP_FROM_EMAIL, SMTP_USE_TLS,
    EMAIL_SEND_DELAY_SECONDS,
)
from utils import get_logger, utcnow

log = get_logger("sender")


def send_email_smtp(
    to_email: str,
    subject: str,
    body: str,
    reply_to: str | None = None,
) -> str:
    """
    Send a single email via SMTP.

    Returns the Message-ID of the sent email.

    Raises:
        smtplib.SMTPException: If sending fails.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = to_email
    msg["Subject"] = subject

    message_id = make_msgid(domain=SMTP_FROM_EMAIL.split("@")[-1] if "@" in SMTP_FROM_EMAIL else "local")
    msg["Message-ID"] = message_id

    if reply_to:
        msg["In-Reply-To"] = reply_to
        msg["References"] = reply_to

    # Plain text body
    msg.attach(MIMEText(body, "plain"))

    # Also create a simple HTML version
    html_body = body.replace("\n", "<br>")
    msg.attach(MIMEText(f"<html><body><p>{html_body}</p></body></html>", "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())

    return message_id


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

        for email in emails:
            # Check if company has been replied to (stop follow-ups)
            company = session.query(Company).filter_by(id=email.company_id).first()
            if company and company.status == "REPLIED":
                email.status = "CANCELLED"
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

            try:
                # For follow-ups, thread under the initial email
                reply_to = None
                if email.sequence_number > 0:
                    initial = (
                        session.query(Email)
                        .filter_by(
                            company_id=email.company_id,
                            contact_id=email.contact_id,
                            sequence_number=0,
                        )
                        .first()
                    )
                    if initial and initial.message_id:
                        reply_to = initial.message_id

                message_id = send_email_smtp(
                    to_email=contact.email,
                    subject=email.subject,
                    body=email.body,
                    reply_to=reply_to,
                )

                email.status = "SENT"
                email.sent_at = utcnow()
                email.message_id = message_id
                sent_count += 1

                # Update company status on first send
                if company and company.status in ("EMAIL_READY", "EMAIL_SENT"):
                    company.status = "EMAIL_SENT"
                    company.updated_at = utcnow()

                log.info(f"    [OK] Sent (Message-ID: {message_id})")

                # Rate limiting
                if EMAIL_SEND_DELAY_SECONDS > 0:
                    time.sleep(EMAIL_SEND_DELAY_SECONDS)

            except smtplib.SMTPException as e:
                email.status = "FAILED"
                failed_count += 1
                log.error(f"    [FAIL] SMTP error: {e}")

            except Exception as e:
                email.status = "FAILED"
                failed_count += 1
                log.error(f"    [FAIL] Unexpected error: {e}")

        log.info(f"Sending complete: {sent_count} sent, {failed_count} failed")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Sender failed: {e}", exc_info=True)
        sys.exit(1)
