"""
reply_checker.py — Stage 9: Reply Checker

Connects to inbox via IMAP for each active mailbox, detects replies to sent
emails by matching In-Reply-To / References headers with stored Message-IDs.
Marks companies as REPLIED and cancels pending follow-ups.

Multi-mailbox: iterates over all active mailboxes with IMAP credentials
configured, rather than using a single global IMAP account.

Usage:
    python reply_checker.py
    python reply_checker.py --dry-run
"""

import sys
import imaplib
import email as email_lib
from email.header import decode_header

from database import get_session, init_db
from models import Email, Company, Contact, ReplyLog, Mailbox
from event_processor import record_reply
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


def fetch_replies_for_mailbox(
    mailbox: Mailbox,
    sent_message_ids: set[str],
) -> list[dict]:
    """
    Connect to inbox for a specific mailbox and find replies
    to our sent emails.

    For Google OAuth mailboxes: uses the Gmail API (no IMAP password needed).
    For SMTP mailboxes: uses IMAP (traditional approach).

    Args:
        mailbox: The Mailbox model with credentials
        sent_message_ids: Set of Message-IDs we've sent

    Returns:
        List of reply dicts with keys: in_reply_to, from, subject, body, mailbox_id
    """
    # Use Gmail API for OAuth-connected Google mailboxes
    if (
        mailbox.provider == "google"
        and mailbox.oauth_connected
        and mailbox.oauth_refresh_token
    ):
        return _fetch_replies_gmail_api(mailbox, sent_message_ids)

    # Fall back to IMAP for SMTP mailboxes
    return _fetch_replies_imap(mailbox, sent_message_ids)


def _fetch_replies_gmail_api(
    mailbox: Mailbox,
    sent_message_ids: set[str],
) -> list[dict]:
    """Fetch replies using the Gmail API for OAuth-connected mailboxes."""
    try:
        from google_oauth import list_recent_messages, get_message, get_message_headers, get_message_body

        log.info(f"Checking replies via Gmail API for {mailbox.email}")
        messages = list_recent_messages(mailbox, max_results=100)
        log.info(f"  {len(messages)} recent messages in inbox")

        replies = []
        for msg_stub in messages:
            msg = get_message(mailbox, msg_stub["id"])
            if not msg:
                continue

            headers = get_message_headers(msg)

            in_reply_to = headers.get("in-reply-to", "").strip()
            references = headers.get("references", "").strip()

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
                body = get_message_body(msg)
                replies.append({
                    "in_reply_to": matched_id,
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", ""),
                    "body": body[:2000],
                    "mailbox_id": mailbox.id,
                })

        log.info(f"  Found {len(replies)} replies via Gmail API")
        return replies

    except Exception as e:
        log.error(f"Gmail API reply check failed for {mailbox.email}: {e}")
        return []


def _fetch_replies_imap(
    mailbox: Mailbox,
    sent_message_ids: set[str],
) -> list[dict]:
    """Fetch replies using IMAP for SMTP mailboxes."""
    imap_host = mailbox.imap_host
    imap_port = mailbox.imap_port or 993
    imap_user = mailbox.imap_username or mailbox.smtp_username
    imap_pass = mailbox.imap_password or mailbox.smtp_password

    if not imap_host or not imap_user or not imap_pass:
        log.debug(f"No IMAP credentials for mailbox {mailbox.email}, skipping")
        return []

    replies = []

    try:
        log.info(f"Checking replies for mailbox {mailbox.email} ({imap_host}:{imap_port})")
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(imap_user, imap_pass)
        mail.select("INBOX")

        import datetime
        # Search for recent emails (last 7 days)
        date_since = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(SINCE "{date_since}")')
        if status != "OK":
            log.warning(f"IMAP search failed for {mailbox.email}")
            return []

        msg_nums = data[0].split()
        log.info(f"  Checking {len(msg_nums)} recent emails")

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
                    "mailbox_id": mailbox.id,
                })

        mail.logout()

    except imaplib.IMAP4.error as e:
        log.error(f"IMAP error for {mailbox.email}: {e}")
    except Exception as e:
        log.error(f"Error checking replies for {mailbox.email}: {e}")

    return replies


def run(dry_run: bool = False):
    """Check for replies across all active mailboxes and update company statuses."""
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

        from sqlalchemy import or_

        # Get all active mailboxes with IMAP or OAuth credentials
        mailboxes = (
            session.query(Mailbox)
            .filter_by(is_active=1)
            .filter(
                or_(
                    (Mailbox.imap_host.isnot(None)) & (Mailbox.imap_username.isnot(None) | Mailbox.smtp_username.isnot(None)),
                    (Mailbox.oauth_connected == 1) & (Mailbox.oauth_refresh_token.isnot(None))
                )
            )
            .all()
        )

        if not mailboxes:
            log.warning("No mailboxes with IMAP/OAuth credentials configured")
            return

        log.info(f"Checking {len(mailboxes)} mailbox(es) for replies")

        # Fetch replies from all mailboxes
        all_replies = []
        for mailbox in mailboxes:
            replies = fetch_replies_for_mailbox(mailbox, set(msg_id_lookup.keys()))
            all_replies.extend(replies)

        log.info(f"Found {len(all_replies)} replies total")

        for reply in all_replies:
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

            # Record reply event for health tracking
            mailbox_id = reply.get("mailbox_id") or matched_email.mailbox_id
            if mailbox_id:
                record_reply(session, matched_email.id, mailbox_id)

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
                pending_email.blocked_reason = "COMPANY_REPLIED"
                log.info(f"  Cancelled follow-up email #{pending_email.id}")

        log.info("Reply checking complete")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Reply checker failed: {e}", exc_info=True)
        sys.exit(1)
