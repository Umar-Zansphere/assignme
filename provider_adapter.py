"""
provider_adapter.py — Provider Abstraction Layer

Abstracts mail sending behind a provider interface.
The campaign engine calls send(message) — it doesn't know or care
which provider is used. The provider is determined by mailbox.provider.

Supported providers:
    - smtp:   Direct SMTP sending (default, current behaviour)
    - google: Google Workspace SMTP relay (same SMTP transport, Gmail-aware defaults)

The SMTP provider is the primary implementation. The abstraction exists so
adding Google Workspace API or other providers later doesn't require
rearchitecting.

Usage:
    from provider_adapter import get_provider, PreparedMessage
    provider = get_provider(mailbox)
    result = provider.send(message)
"""

import smtplib
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid, formataddr

from models import Mailbox
from utils import get_logger

log = get_logger("provider_adapter")


# ── Data Classes ──────────────────────────────────────────
@dataclass
class PreparedMessage:
    """A fully prepared message ready for sending."""
    to_email: str
    from_email: str
    from_name: str
    subject: str
    body_plain: str
    body_html: str | None = None
    reply_to_header: str | None = None      # In-Reply-To for threading
    references_header: str | None = None     # References for threading
    list_unsubscribe: str | None = None      # List-Unsubscribe header
    list_unsubscribe_post: str | None = None # List-Unsubscribe-Post header
    custom_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class SendResult:
    """Result of a send attempt."""
    success: bool
    message_id: str | None = None       # Our Message-ID
    provider_message_id: str | None = None  # Provider's message ID (if different)
    error: str | None = None
    error_code: int | None = None       # SMTP error code
    bounce_type: str | None = None      # HARD, SOFT (if immediate bounce)
    is_permanent: bool = False          # True for 5xx errors


# ── Base Provider ─────────────────────────────────────────
class MailProvider:
    """Base class for mail providers."""

    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox

    def send(self, message: PreparedMessage) -> SendResult:
        """Send a prepared message. Must be implemented by subclasses."""
        raise NotImplementedError

    def test_connection(self) -> tuple[bool, str]:
        """Test the connection to the mail provider. Returns (success, message)."""
        raise NotImplementedError

    def get_provider_name(self) -> str:
        """Return the provider name."""
        raise NotImplementedError


# ── SMTP Provider ─────────────────────────────────────────
class SMTPProvider(MailProvider):
    """Direct SMTP sending. Works with any SMTP server."""

    def get_provider_name(self) -> str:
        return "smtp"

    def _build_mime_message(self, message: PreparedMessage) -> tuple[MIMEMultipart, str]:
        """Build a MIME message and return (msg, message_id)."""
        msg = MIMEMultipart("alternative")

        # From / To / Subject
        msg["From"] = formataddr((message.from_name, message.from_email))
        msg["To"] = message.to_email
        msg["Subject"] = message.subject

        # Message-ID
        domain = message.from_email.split("@")[-1] if "@" in message.from_email else "local"
        message_id = make_msgid(domain=domain)
        msg["Message-ID"] = message_id

        # Threading headers
        if message.reply_to_header:
            msg["In-Reply-To"] = message.reply_to_header
        if message.references_header:
            msg["References"] = message.references_header

        # Compliance headers (RFC 8058 one-click unsubscribe)
        if message.list_unsubscribe:
            msg["List-Unsubscribe"] = f"<{message.list_unsubscribe}>"
        if message.list_unsubscribe_post:
            msg["List-Unsubscribe-Post"] = message.list_unsubscribe_post

        # Custom headers
        for key, value in message.custom_headers.items():
            msg[key] = value

        # Bodies
        msg.attach(MIMEText(message.body_plain, "plain"))

        if message.body_html:
            msg.attach(MIMEText(message.body_html, "html"))
        else:
            # Auto-generate simple HTML from plain text
            html_body = message.body_plain.replace("\n", "<br>")
            msg.attach(MIMEText(f"<html><body><p>{html_body}</p></body></html>", "html"))

        return msg, message_id

    def send(self, message: PreparedMessage) -> SendResult:
        """Send via SMTP."""
        try:
            msg, message_id = self._build_mime_message(message)

            mb = self.mailbox
            host = mb.smtp_host
            port = mb.smtp_port or 587
            username = mb.smtp_username
            password = mb.smtp_password
            use_tls = bool(mb.smtp_use_tls)

            # Port 465 = implicit SSL (SMTP_SSL), others = STARTTLS
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=30) as server:
                    if username and password:
                        server.login(username, password)
                    server.sendmail(message.from_email, message.to_email, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=30) as server:
                    if use_tls:
                        server.starttls()
                    if username and password:
                        server.login(username, password)
                    server.sendmail(message.from_email, message.to_email, msg.as_string())

            return SendResult(
                success=True,
                message_id=message_id,
            )

        except smtplib.SMTPRecipientsRefused as e:
            # Hard bounce — address does not exist
            return SendResult(
                success=False,
                error=str(e),
                bounce_type="HARD",
                is_permanent=True,
            )

        except smtplib.SMTPDataError as e:
            code = getattr(e, "smtp_code", 0) or 0
            is_permanent = code >= 500
            return SendResult(
                success=False,
                error=str(e),
                error_code=code,
                bounce_type="HARD" if is_permanent else "SOFT",
                is_permanent=is_permanent,
            )

        except smtplib.SMTPAuthenticationError as e:
            return SendResult(
                success=False,
                error=f"Authentication failed: {e}",
                is_permanent=True,
            )

        except smtplib.SMTPException as e:
            return SendResult(
                success=False,
                error=str(e),
                bounce_type="SOFT",
            )

        except Exception as e:
            return SendResult(
                success=False,
                error=f"Unexpected error: {e}",
            )

    def test_connection(self) -> tuple[bool, str]:
        """Test SMTP connection without sending."""
        try:
            mb = self.mailbox
            host = mb.smtp_host
            port = mb.smtp_port or 587

            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                    if mb.smtp_username and mb.smtp_password:
                        server.login(mb.smtp_username, mb.smtp_password)
                    return True, f"Connected to {host}:{port} (SSL)"
            else:
                with smtplib.SMTP(host, port, timeout=15) as server:
                    if mb.smtp_use_tls:
                        server.starttls()
                    if mb.smtp_username and mb.smtp_password:
                        server.login(mb.smtp_username, mb.smtp_password)
                    return True, f"Connected to {host}:{port} (STARTTLS={'yes' if mb.smtp_use_tls else 'no'})"

        except smtplib.SMTPAuthenticationError as e:
            return False, f"Authentication failed: {e}"
        except Exception as e:
            return False, f"Connection failed: {e}"


# ── Google Workspace Provider (OAuth + Gmail API) ─────────
class GoogleWorkspaceProvider(MailProvider):
    """Google Workspace/Gmail provider using OAuth 2.0 and the Gmail API.

    Flow:
        1. User connects via OAuth in the dashboard
        2. Refresh token stored in mailbox.oauth_refresh_token
        3. On send: auto-refresh access token → Gmail API
        4. On read: auto-refresh access token → Gmail API

    Falls back to SMTP if OAuth is not connected (backward compatibility).
    """

    def get_provider_name(self) -> str:
        return "google"

    def _is_oauth_connected(self) -> bool:
        """Check if this mailbox has a valid OAuth connection."""
        return bool(
            self.mailbox.oauth_connected
            and self.mailbox.oauth_refresh_token
        )

    def send(self, message: PreparedMessage) -> SendResult:
        """Send via Gmail API using OAuth, or fall back to SMTP."""
        if not self._is_oauth_connected():
            log.info(f"Mailbox {self.mailbox.email}: OAuth not connected, falling back to SMTP")
            # Fall back to SMTP with Gmail defaults
            if not self.mailbox.smtp_host:
                self.mailbox.smtp_host = "smtp.gmail.com"
            if not self.mailbox.smtp_port:
                self.mailbox.smtp_port = 465
            return SMTPProvider(self.mailbox).send(message)

        from google_oauth import send_gmail

        result = send_gmail(
            mailbox=self.mailbox,
            to_email=message.to_email,
            from_name=message.from_name,
            subject=message.subject,
            body_plain=message.body_plain,
            body_html=message.body_html,
            reply_to_header=message.reply_to_header,
            references_header=message.references_header,
            list_unsubscribe=message.list_unsubscribe,
            list_unsubscribe_post=message.list_unsubscribe_post,
            custom_headers=message.custom_headers,
        )

        return SendResult(
            success=result.get("success", False),
            message_id=result.get("message_id"),
            provider_message_id=result.get("provider_message_id"),
            error=result.get("error"),
            error_code=result.get("error_code"),
            bounce_type=result.get("bounce_type"),
            is_permanent=result.get("is_permanent", False),
        )

    def test_connection(self) -> tuple[bool, str]:
        """Test Google OAuth connection."""
        if not self._is_oauth_connected():
            return False, "OAuth not connected. Click 'Connect Google' in Mailbox Management."

        try:
            from google_oauth import get_valid_credentials
            credentials = get_valid_credentials(self.mailbox)
            if credentials and credentials.token:
                return True, f"Google OAuth connected ✓ (token valid for {self.mailbox.email})"
            else:
                return False, "OAuth token expired and could not be refreshed. Reconnect."
        except Exception as e:
            return False, f"OAuth test failed: {e}"


# ── Provider Factory ──────────────────────────────────────
_PROVIDERS = {
    "smtp": SMTPProvider,
    "google": GoogleWorkspaceProvider,
}


def get_provider(mailbox: Mailbox) -> MailProvider:
    """
    Get the appropriate provider for a mailbox.

    Args:
        mailbox: The Mailbox model instance

    Returns:
        An instantiated MailProvider subclass
    """
    provider_name = (mailbox.provider or "smtp").lower()
    provider_class = _PROVIDERS.get(provider_name, SMTPProvider)
    return provider_class(mailbox)
