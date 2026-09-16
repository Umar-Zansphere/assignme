"""
unsubscribe_server.py — Unsubscribe Endpoint

Lightweight Flask app for handling unsubscribe requests.
Implements RFC 8058 one-click unsubscribe as required by Google for bulk senders.

Endpoints:
    POST /unsubscribe/<token>  ← RFC 8058 one-click (no login, no confirmation)
    GET  /unsubscribe/<token>  ← Human-readable confirmation page

The token identifies the recipient WITHOUT exposing their email in the URL.

Usage:
    python unsubscribe_server.py                    # Start on default port
    python unsubscribe_server.py --port 8090        # Start on custom port
"""

import sys

from flask import Flask, request, Response

from database import get_session, init_db
from compliance import process_unsubscribe
from config import UNSUBSCRIBE_SERVER_PORT
from utils import get_logger

log = get_logger("unsubscribe_server")

app = Flask(__name__)

# ── HTML Templates ────────────────────────────────────────
CONFIRMATION_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unsubscribed</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d0d1a;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }
        .card {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 48px 40px;
            max-width: 480px;
            width: 100%;
            text-align: center;
        }
        .icon { font-size: 48px; margin-bottom: 16px; }
        h1 {
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 12px;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        p { color: #a0aec0; line-height: 1.6; margin-bottom: 8px; }
        .subtle { font-size: 13px; color: #718096; margin-top: 24px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>You've been unsubscribed</h1>
        <p>You won't receive any more emails from us.</p>
        <p class="subtle">This change takes effect immediately.</p>
    </div>
</body>
</html>"""

ERROR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unsubscribe</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d0d1a;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }
        .card {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 48px 40px;
            max-width: 480px;
            width: 100%;
            text-align: center;
        }
        .icon { font-size: 48px; margin-bottom: 16px; }
        h1 { font-size: 24px; font-weight: 700; margin-bottom: 12px; color: #fc8181; }
        p { color: #a0aec0; line-height: 1.6; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚠️</div>
        <h1>Invalid unsubscribe link</h1>
        <p>This unsubscribe link is not valid or has already been processed.</p>
    </div>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────

@app.route("/unsubscribe/<token>", methods=["POST"])
def one_click_unsubscribe(token: str):
    """
    RFC 8058 one-click unsubscribe.

    Google's mail servers send a POST request when the user clicks
    "Unsubscribe" in the Gmail UI. No login, no account, no confirmation
    page required — just immediate suppression.
    """
    init_db()

    with get_session() as session:
        success = process_unsubscribe(session, token)

    if success:
        log.info(f"One-click unsubscribe processed: token={token[:8]}...")
        return Response(status=200)
    else:
        log.warning(f"One-click unsubscribe failed: token={token[:8]}...")
        return Response(status=404)


@app.route("/unsubscribe/<token>", methods=["GET"])
def unsubscribe_page(token: str):
    """
    Human-readable unsubscribe page.

    When a recipient clicks the unsubscribe link in the email body,
    they see a confirmation page. The unsubscribe is processed immediately
    on page load (no additional click required).
    """
    init_db()

    with get_session() as session:
        success = process_unsubscribe(session, token)

    if success:
        log.info(f"Unsubscribe page processed: token={token[:8]}...")
        return CONFIRMATION_PAGE, 200
    else:
        log.warning(f"Unsubscribe page - invalid token: {token[:8]}...")
        return ERROR_PAGE, 404


@app.route("/health", methods=["GET"])
def health_check():
    """Simple health check endpoint."""
    return {"status": "ok"}, 200


# ── Google OAuth Routes ───────────────────────────────────

OAUTH_SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Google Connected</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d0d1a;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .card {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 48px 40px;
            max-width: 520px;
            width: 100%;
            text-align: center;
        }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        h1 {{
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 12px;
            background: linear-gradient(90deg, #4285f4 0%, #34a853 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        p {{ color: #a0aec0; line-height: 1.6; margin-bottom: 8px; }}
        .email {{ color: #e2e8f0; font-weight: 600; }}
        .subtle {{ font-size: 13px; color: #718096; margin-top: 24px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>Google Account Connected</h1>
        <p>Mailbox <span class="email">{email}</span> is now connected via OAuth.</p>
        <p>Sending and reading will use the Gmail API — no app password needed.</p>
        <p class="subtle">You can close this tab and return to the dashboard.</p>
    </div>
</body>
</html>"""

OAUTH_ERROR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OAuth Error</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d0d1a;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .card {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 48px 40px;
            max-width: 520px;
            width: 100%;
            text-align: center;
        }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 12px; color: #fc8181; }}
        p {{ color: #a0aec0; line-height: 1.6; }}
        .error-detail {{ color: #fc8181; font-size: 0.85rem; margin-top: 16px; word-break: break-word; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">❌</div>
        <h1>Connection Failed</h1>
        <p>Could not connect the Google account.</p>
        <p class="error-detail">{error}</p>
    </div>
</body>
</html>"""


@app.route("/oauth/google/authorize", methods=["GET"])
def google_oauth_authorize():
    """
    Start the Google OAuth flow.

    Query params:
        mailbox_id: The Mailbox ID to connect
    """
    from config import GOOGLE_CLIENT_ID
    mailbox_id = request.args.get("mailbox_id")

    if not mailbox_id:
        return OAUTH_ERROR_PAGE.format(error="Missing mailbox_id parameter"), 400

    if not GOOGLE_CLIENT_ID:
        return OAUTH_ERROR_PAGE.format(
            error="GOOGLE_CLIENT_ID not configured. Set it in your .env file."
        ), 500

    try:
        from google_oauth import build_authorize_url
        url = build_authorize_url(int(mailbox_id))
        log.info(f"OAuth authorize: redirecting for mailbox {mailbox_id}")
        from flask import redirect
        return redirect(url)
    except Exception as e:
        log.error(f"OAuth authorize failed: {e}")
        return OAUTH_ERROR_PAGE.format(error=str(e)), 500


@app.route("/oauth/google/callback", methods=["GET"])
def google_oauth_callback():
    """
    Handle the Google OAuth callback.

    Google redirects here with:
        code: Authorization code to exchange for tokens
        state: The mailbox_id we passed in authorize
    """
    code = request.args.get("code")
    state = request.args.get("state")  # mailbox_id
    error = request.args.get("error")

    if error:
        log.warning(f"OAuth callback error: {error}")
        return OAUTH_ERROR_PAGE.format(error=f"Google returned error: {error}"), 400

    if not code or not state:
        return OAUTH_ERROR_PAGE.format(error="Missing code or state parameter"), 400

    try:
        mailbox_id = int(state)
    except ValueError:
        return OAUTH_ERROR_PAGE.format(error="Invalid mailbox_id in state"), 400

    try:
        from google_oauth import exchange_code
        from models import Mailbox, DomainHealth

        init_db()
        tokens = exchange_code(code)

        with get_session() as session:
            mailbox = session.query(Mailbox).filter_by(id=mailbox_id).first()
            if not mailbox:
                return OAUTH_ERROR_PAGE.format(error=f"Mailbox #{mailbox_id} not found"), 404

            # Store tokens
            mailbox.oauth_access_token = tokens["access_token"]
            mailbox.oauth_refresh_token = tokens["refresh_token"]
            mailbox.oauth_token_expiry = tokens.get("token_expiry")
            mailbox.oauth_connected = 1

            # Update mailbox email if we got one from Google
            oauth_email = tokens.get("email", "")
            if oauth_email and not mailbox.email:
                mailbox.email = oauth_email
            elif oauth_email and mailbox.email != oauth_email:
                log.warning(
                    f"OAuth email ({oauth_email}) differs from mailbox email ({mailbox.email}). "
                    f"Keeping mailbox email."
                )

            # Set IMAP/SMTP to Gmail defaults (for fallback/reply checking)
            if not mailbox.imap_host:
                mailbox.imap_host = "imap.gmail.com"
                mailbox.imap_port = 993
            if not mailbox.smtp_host:
                mailbox.smtp_host = "smtp.gmail.com"
                mailbox.smtp_port = 465

            email_display = mailbox.email

        log.info(f"OAuth callback: tokens stored for mailbox #{mailbox_id} ({email_display})")
        return OAUTH_SUCCESS_PAGE.format(email=email_display), 200

    except Exception as e:
        log.error(f"OAuth callback failed: {e}", exc_info=True)
        return OAUTH_ERROR_PAGE.format(error=str(e)), 500


# ── Main ──────────────────────────────────────────────────
if __name__ == "__main__":
    port = UNSUBSCRIBE_SERVER_PORT

    # Allow --port override
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    log.info(f"Starting web server on port {port}")
    print(f"Web server running on http://0.0.0.0:{port}")
    print(f"  POST /unsubscribe/<token>             — RFC 8058 one-click")
    print(f"  GET  /unsubscribe/<token>             — Human-readable page")
    print(f"  GET  /oauth/google/authorize          — Start Google OAuth")
    print(f"  GET  /oauth/google/callback           — Google OAuth callback")
    print(f"  GET  /health                          — Health check")

    app.run(host="0.0.0.0", port=port, debug=False)
