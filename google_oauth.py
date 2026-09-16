"""
google_oauth.py — Google OAuth 2.0 Integration

Handles the full OAuth lifecycle for Google Workspace/Gmail mailboxes:
    1. Generate authorization URL → user consents
    2. Exchange authorization code for tokens
    3. Store refresh_token in Mailbox record
    4. Refresh access_token when expired (automatic)
    5. Send email via Gmail API using access_token
    6. Read inbox via Gmail API for reply checking

Flow:
    Dashboard: "Connect Google" button
         ↓
    /oauth/google/authorize?mailbox_id=X
         ↓
    Google Consent Screen (gmail.send + gmail.readonly)
         ↓
    /oauth/google/callback?code=...&state=mailbox_id
         ↓
    Exchange code for tokens
         ↓
    Store refresh_token + access_token in Mailbox
         ↓
    On send: Gmail API with auto-refreshed access_token

Prerequisites:
    1. Create a Google Cloud project
    2. Enable the Gmail API
    3. Create OAuth 2.0 credentials (Web application)
    4. Set redirect URI: http://localhost:8090/oauth/google/callback
    5. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env

Usage:
    from google_oauth import build_authorize_url, exchange_code, send_gmail, refresh_access_token
"""

import base64
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid, formataddr
from urllib.parse import urlencode

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI, GOOGLE_OAUTH_SCOPES,
)
from utils import get_logger

log = get_logger("google_oauth")

_GOOGLE_AUTH_URI  = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


# ── OAuth Flow ────────────────────────────────────────────

def _get_client_config() -> dict:
    """Build the client config dict for Google OAuth."""
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


def build_authorize_url(mailbox_id: int) -> str:
    """
    Generate the Google OAuth authorization URL.

    Builds the URL manually to avoid google_auth_oauthlib's Flow, which
    generates a PKCE code_verifier internally but cannot persist it across
    the two separate HTTP requests (authorize → callback). Using the raw
    OAuth2 authorization endpoint with no code_challenge bypasses PKCE
    entirely, which is correct for server-side web applications using a
    client secret.

    Args:
        mailbox_id: The Mailbox ID to attach as state (returned in callback)

    Returns:
        The authorization URL to redirect the user to
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env. "
            "Create credentials at https://console.cloud.google.com/apis/credentials"
        )

    params = {
        "client_id":             GOOGLE_CLIENT_ID,
        "redirect_uri":          GOOGLE_REDIRECT_URI,
        "response_type":         "code",
        "scope":                 " ".join(GOOGLE_OAUTH_SCOPES),
        "access_type":           "offline",   # get refresh_token
        "prompt":                "consent",   # always return refresh_token
        "include_granted_scopes": "true",
        "state":                 str(mailbox_id),
    }

    authorization_url = f"{_GOOGLE_AUTH_URI}?{urlencode(params)}"
    log.info(f"Generated OAuth URL for mailbox {mailbox_id}")
    return authorization_url


def exchange_code(authorization_code: str) -> dict:
    """
    Exchange an authorization code for tokens.

    Uses a direct POST to Google's token endpoint rather than the Flow
    helper, so there is no PKCE code_verifier mismatch between requests.

    Args:
        authorization_code: The code from Google's callback

    Returns:
        Dict with: access_token, refresh_token, token_expiry, email
    """
    resp = requests.post(
        _GOOGLE_TOKEN_URI,
        data={
            "code":          authorization_code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  GOOGLE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        timeout=30,
    )

    if not resp.ok:
        raise RuntimeError(
            f"Token exchange failed {resp.status_code}: {resp.text}"
        )

    data = resp.json()

    if "error" in data:
        raise RuntimeError(
            f"Token exchange error: {data['error']} — {data.get('error_description', '')}"
        )

    access_token  = data["access_token"]
    refresh_token = data.get("refresh_token", "")
    expires_in    = data.get("expires_in", 3600)
    token_expiry  = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Build a Credentials object just to call the userinfo endpoint
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=_GOOGLE_TOKEN_URI,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_OAUTH_SCOPES,
    )
    user_email = _get_user_email(credentials)

    result = {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_expiry":  token_expiry,
        "email":         user_email,
    }

    log.info(f"OAuth tokens exchanged for {user_email}")
    return result


def _get_user_email(credentials: Credentials) -> str:
    """Get the authenticated user's email address from Google."""
    try:
        service = build("oauth2", "v2", credentials=credentials)
        user_info = service.userinfo().get().execute()
        return user_info.get("email", "")
    except Exception as e:
        log.warning(f"Could not get user email: {e}")
        return ""


# ── Token Refresh ─────────────────────────────────────────

def get_valid_credentials(mailbox) -> Credentials | None:
    """
    Get valid OAuth credentials for a mailbox.
    Auto-refreshes the access token if expired.

    Args:
        mailbox: Mailbox model instance with OAuth fields

    Returns:
        google.oauth2.credentials.Credentials or None
    """
    if not mailbox.oauth_refresh_token:
        log.warning(f"No refresh token for mailbox {mailbox.email}")
        return None

    credentials = Credentials(
        token=mailbox.oauth_access_token,
        refresh_token=mailbox.oauth_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_OAUTH_SCOPES,
    )

    # Check if access token is expired or about to expire (5 min buffer)
    now = datetime.now(timezone.utc)
    expiry = mailbox.oauth_token_expiry
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    needs_refresh = (
        not credentials.token
        or not expiry
        or now >= expiry - timedelta(minutes=5)
    )

    if needs_refresh:
        try:
            from google.auth.transport.requests import Request
            credentials.refresh(Request())

            # Update the mailbox with new tokens
            # (caller must save to DB)
            mailbox.oauth_access_token = credentials.token
            if credentials.expiry:
                mailbox.oauth_token_expiry = credentials.expiry.replace(tzinfo=timezone.utc)

            log.info(f"Refreshed access token for {mailbox.email}")
        except Exception as e:
            log.error(f"Token refresh failed for {mailbox.email}: {e}")
            return None

    return credentials


def refresh_access_token(mailbox) -> bool:
    """
    Explicitly refresh the access token for a mailbox.
    Updates the mailbox object in-place (caller must commit).

    Returns True if refresh succeeded.
    """
    credentials = get_valid_credentials(mailbox)
    return credentials is not None


# ── Gmail API: Send ───────────────────────────────────────

def send_gmail(
    mailbox,
    to_email: str,
    from_name: str,
    subject: str,
    body_plain: str,
    body_html: str | None = None,
    reply_to_header: str | None = None,
    references_header: str | None = None,
    list_unsubscribe: str | None = None,
    list_unsubscribe_post: str | None = None,
    custom_headers: dict | None = None,
) -> dict:
    """
    Send an email via Gmail API.

    Returns:
        Dict with: success, message_id, provider_message_id, error
    """
    credentials = get_valid_credentials(mailbox)
    if not credentials:
        return {
            "success": False,
            "error": "No valid OAuth credentials. Reconnect the mailbox.",
            "is_permanent": True,
        }

    try:
        # Build MIME message
        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr((from_name, mailbox.email))
        msg["To"] = to_email
        msg["Subject"] = subject

        # Message-ID
        domain = mailbox.email.split("@")[-1] if "@" in mailbox.email else "gmail.com"
        message_id = make_msgid(domain=domain)
        msg["Message-ID"] = message_id

        # Threading
        if reply_to_header:
            msg["In-Reply-To"] = reply_to_header
        if references_header:
            msg["References"] = references_header

        # Compliance headers
        if list_unsubscribe:
            msg["List-Unsubscribe"] = f"<{list_unsubscribe}>"
        if list_unsubscribe_post:
            msg["List-Unsubscribe-Post"] = list_unsubscribe_post

        # Custom headers
        if custom_headers:
            for key, value in custom_headers.items():
                msg[key] = value

        # Bodies
        msg.attach(MIMEText(body_plain, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))
        else:
            html_body = body_plain.replace("\n", "<br>")
            msg.attach(MIMEText(f"<html><body><p>{html_body}</p></body></html>", "html"))

        # Encode as base64url
        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        # Send via Gmail API
        service = build("gmail", "v1", credentials=credentials)
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw_message})
            .execute()
        )

        provider_msg_id = sent.get("id", "")
        log.info(f"Gmail API sent: {to_email} (id={provider_msg_id})")

        return {
            "success": True,
            "message_id": message_id,
            "provider_message_id": provider_msg_id,
        }

    except HttpError as e:
        error_msg = str(e)
        status_code = e.resp.status if hasattr(e, "resp") else 0

        # Classify the error
        is_permanent = status_code in (400, 403, 404)
        bounce_type = None
        if status_code == 400 and "invalid" in error_msg.lower():
            bounce_type = "HARD"
            is_permanent = True

        log.error(f"Gmail API error for {to_email}: {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "error_code": status_code,
            "bounce_type": bounce_type,
            "is_permanent": is_permanent,
        }

    except Exception as e:
        log.error(f"Gmail send failed for {to_email}: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# ── Gmail API: Read (for reply checking) ─────────────────

def list_recent_messages(mailbox, max_results: int = 100) -> list[dict]:
    """
    List recent messages from the inbox via Gmail API.

    Returns list of dicts with: id, threadId, snippet
    """
    credentials = get_valid_credentials(mailbox)
    if not credentials:
        return []

    try:
        service = build("gmail", "v1", credentials=credentials)
        results = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX"], maxResults=max_results, q="newer_than:7d")
            .execute()
        )
        return results.get("messages", [])
    except Exception as e:
        log.error(f"Gmail list messages failed for {mailbox.email}: {e}")
        return []


def get_message(mailbox, message_id: str) -> dict | None:
    """
    Get a specific message by ID via Gmail API.

    Returns the full message payload or None.
    """
    credentials = get_valid_credentials(mailbox)
    if not credentials:
        return None

    try:
        service = build("gmail", "v1", credentials=credentials)
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return msg
    except Exception as e:
        log.error(f"Gmail get message failed: {e}")
        return None


def get_message_headers(message: dict) -> dict[str, str]:
    """Extract headers from a Gmail API message payload into a simple dict."""
    headers = {}
    for header in message.get("payload", {}).get("headers", []):
        headers[header["name"].lower()] = header["value"]
    return headers


def get_message_body(message: dict) -> str:
    """Extract plain text body from a Gmail API message payload."""
    payload = message.get("payload", {})

    # Single-part message
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart message
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    return ""
