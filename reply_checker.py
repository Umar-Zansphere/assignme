"""
reply_checker.py — Stage 9: Reply Checker

Connects to inbox via IMAP, detects replies to sent emails
by matching In-Reply-To / References headers with stored Message-IDs.
Marks companies as REPLIED and cancels pending follow-ups.

Usage:
    python reply_checker.py
    python reply_checker.py --dry-run
"""

import sys
import imaplib
import email as email_lib
from email.header import decode_header

from database import get_session, init_db
from models import Email, Company, Contact, ReplyLog
from config import IMAP_HOST, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD
from utils import get_logger, utcnow

log = get_logger("reply_checker")


def _decode_header_value(value: str) -> str:
    """Decode email header value handling encodings."""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(part))
    return " ".join(parts)


def _get_header(msg, header_name: str) -> str:
    """Safely get and decode an email header."""
    raw = msg.get(header_name, "")
    return _decode_header_value(raw)


def _get_body(msg) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        if payload:
            return payload.decode(charset, errors="replace")
    return ""


def fetch_replies(sent_message_ids: set[str]) -> list[dict]:
    """
    Connect to IMAP inbox and find replies to our sent emails.

    Args:
        sent_message_ids: Set of Message-IDs we've sent.

    Returns:
        List of reply dicts with keys: in_reply_to, from, subject, body
    """
    if not IMAP_USERNAME or not IMAP_PASSWORD:
        log.error("IMAP credentials not configured")
        return []

    replies = []

    try:
        log.info(f"Connecting to IMAP: {IMAP_HOST}:{IMAP_PORT}")
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USERNAME, IMAP_PASSWORD)
        mail.select("INBOX")

        # Search for recent emails (last 7 days)
        status, data = mail.search(None, "(SINCE 7-DAYS-AGO)")
        if status != "OK":
            log.warning("IMAP search failed")
            return []

        msg_nums = data[0].split()
        log.info(f"Checking {len(msg_nums)} recent emails for replies")

        for num in msg_nums:
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            msg = email_lib.message_from_bytes(msg_data[0][1])

            # Check In-Reply-To and References headers
            in_reply_to = _get_header(msg, "In-Reply-To").strip()
            references = _get_header(msg, "References").strip()

            # Match against our sent Message-IDs
            matched_id = None
            if in_reply_to in sent_message_ids:
                matched_id = in_reply_to
            else:
                for ref in references.split():
                    ref = ref.strip()
                    if ref in sent_message_ids:
                        matched_id = ref
                        break

            if matched_id:
                replies.append({
                    "in_reply_to": matched_id,
                    "from": _get_header(msg, "From"),
                    "subject": _get_header(msg, "Subject"),
                    "body": _get_body(msg)[:2000],  # Truncate body
                })

        mail.logout()

    except imaplib.IMAP4.error as e:
        log.error(f"IMAP error: {e}")
    except Exception as e:
        log.error(f"Error checking replies: {e}")

    return replies


def run(dry_run: bool = False):
    """Check for replies and update company statuses."""
    init_db()
    log.info("Starting reply checker...")

    with get_session() as session:
        # Get all sent emails with Message-IDs
        sent_emails = (
            session.query(Email)
            .filter(
                Email.status == "SENT",
                Email.message_id.isnot(None),
            )
            .all()
        )

        if not sent_emails:
            log.info("No sent emails to check replies for")
            return

        # Build lookup: Message-ID → Email record
        msg_id_lookup: dict[str, Email] = {
            e.message_id: e for e in sent_emails if e.message_id
        }

        log.info(f"Tracking {len(msg_id_lookup)} sent Message-IDs")

        if dry_run:
            log.info("[DRY RUN] Would connect to IMAP and check for replies")
            return

        # Fetch replies
        replies = fetch_replies(set(msg_id_lookup.keys()))
        log.info(f"Found {len(replies)} replies")

        for reply in replies:
            matched_email = msg_id_lookup.get(reply["in_reply_to"])
            if not matched_email:
                continue

            # Check if we already logged this reply
            existing = (
                session.query(ReplyLog)
                .filter_by(
                    email_id=matched_email.id,
                    reply_from=reply["from"],
                )
                .first()
            )
            if existing:
                log.info(f"  Already logged reply from {reply['from']}")
                continue

            # Log the reply
            reply_log = ReplyLog(
                email_id=matched_email.id,
                company_id=matched_email.company_id,
                contact_id=matched_email.contact_id,
                reply_subject=reply["subject"],
                reply_body=reply["body"],
                reply_from=reply["from"],
            )
            session.add(reply_log)

            log.info(f"  [OK] Reply detected from {reply['from']}: {reply['subject']}")

            # Update company status
            company = session.query(Company).filter_by(id=matched_email.company_id).first()
            if company:
                company.status = "REPLIED"
                company.updated_at = utcnow()

            # Cancel all pending follow-ups for this company
            pending = (
                session.query(Email)
                .filter(
                    Email.company_id == matched_email.company_id,
                    Email.status == "SCHEDULED",
                )
                .all()
            )
            for pending_email in pending:
                pending_email.status = "CANCELLED"
                log.info(f"  Cancelled follow-up email #{pending_email.id}")

        log.info("Reply checking complete")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Reply checker failed: {e}", exc_info=True)
        sys.exit(1)
