"""
seed_mailboxes.py - Seed Hostinger (or any SMTP/IMAP) mailboxes into the DB.

Creates mailbox records with full SMTP + IMAP credentials and auto-creates
DomainHealth records for each unique domain.

Usage:
    python seed_mailboxes.py                         # Seeds from MAILBOXES list below
    python seed_mailboxes.py --test-connections       # Seed + test SMTP connections
    python seed_mailboxes.py --dry-run                # Preview without writing to DB

Edit the MAILBOXES list below to add your Hostinger (or any provider) credentials.
"""

import sys
from database import get_session, init_db
from models import Mailbox, DomainHealth
from utils import get_logger

log = get_logger("seed_mailboxes")


# --- DEBUG / QA CONTACT ---
# This email is injected into every campaign as the first recipient.
# It helps verify: inbox delivery, unsubscribe links, mailbox rotation,
# template rendering, and follow-up sequencing.
DEBUG_CONTACT_EMAIL = "umar.mohamed@zansphere.com"
DEBUG_CONTACT_NAME = "Umar Mohamed (Debug)"


# --- PROVIDER PRESETS ---
PROVIDER_PRESETS = {
    "hostinger": {
        "smtp_host": "smtp.hostinger.com",
        "smtp_port": 465,
        "smtp_use_tls": 0,  # Port 465 uses implicit SSL, not STARTTLS
        "imap_host": "imap.hostinger.com",
        "imap_port": 993,
    },
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_use_tls": 0,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
    },
    "outlook": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_use_tls": 1,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
    },
    "zoho": {
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 465,
        "smtp_use_tls": 0,
        "imap_host": "imap.zoho.com",
        "imap_port": 993,
    },
}


# --- MAILBOX CREDENTIALS ---
# Add your Hostinger (or any provider) mailboxes here.
# Each dict is one mailbox with its SMTP + IMAP credentials.
#
# For Hostinger, use your full email as username and your mailbox password.
# Preset options: "hostinger", "gmail", "outlook", "zoho"
# Or manually specify smtp_host/imap_host for custom providers.

MAILBOXES = [
    # -- Hostinger Mailboxes --
    # Uncomment and fill in your real credentials:
    #
    # {
    #     "email": "outreach@yourdomain.com",
    #     "display_name": "Your Name",
    #     "password": "your-hostinger-mailbox-password",
    #     "preset": "hostinger",
    # },
    # {
    #     "email": "hello@yourdomain.com",
    #     "display_name": "Hello from YourCompany",
    #     "password": "your-hostinger-mailbox-password",
    #     "preset": "hostinger",
    # },

    # -- Gmail Mailboxes (App Password) --
    # For Gmail, use an App Password (not your regular password).
    # Generate at: https://myaccount.google.com/apppasswords
    #
    # {
    #     "email": "yourname@gmail.com",
    #     "display_name": "Your Name",
    #     "password": "abcd efgh ijkl mnop",
    #     "preset": "gmail",
    # },

    # -- Custom SMTP/IMAP (any provider) --
    # {
    #     "email": "sales@company.com",
    #     "display_name": "Sales Team",
    #     "password": "password123",
    #     "smtp_host": "mail.company.com",
    #     "smtp_port": 587,
    #     "smtp_use_tls": 1,
    #     "imap_host": "mail.company.com",
    #     "imap_port": 993,
    # },
]


def seed_mailboxes(test_connections: bool = False, dry_run: bool = False):
    """Seed mailboxes into the database."""
    init_db()

    if not MAILBOXES:
        log.warning("No mailboxes defined in MAILBOXES list.")
        print("\nNo mailboxes to seed. Edit the MAILBOXES list in seed_mailboxes.py.\n")
        return

    print(f"\nSeeding {len(MAILBOXES)} mailbox(es)...\n")

    created = 0
    skipped = 0
    failed = 0

    with get_session() as session:
        for mb_config in MAILBOXES:
            email = mb_config["email"]
            password = mb_config["password"]
            display_name = mb_config.get("display_name", email.split("@")[0])
            domain = email.split("@")[-1]

            # Resolve preset
            preset_name = mb_config.get("preset", "")
            preset = PROVIDER_PRESETS.get(preset_name, {})

            smtp_host = mb_config.get("smtp_host", preset.get("smtp_host", ""))
            smtp_port = mb_config.get("smtp_port", preset.get("smtp_port", 587))
            smtp_use_tls = mb_config.get("smtp_use_tls", preset.get("smtp_use_tls", 1))
            imap_host = mb_config.get("imap_host", preset.get("imap_host", ""))
            imap_port = mb_config.get("imap_port", preset.get("imap_port", 993))

            if not smtp_host:
                print(f"   FAIL  {email} - no SMTP host (set 'preset' or 'smtp_host')")
                failed += 1
                continue

            # Check if already exists
            existing = session.query(Mailbox).filter_by(email=email).first()
            if existing:
                print(f"   SKIP  {email} - already exists (id={existing.id})")
                skipped += 1
                continue

            if dry_run:
                print(f"   [DRY RUN] Would create: {email} ({smtp_host}:{smtp_port})")
                created += 1
                continue

            # Create mailbox
            mailbox = Mailbox(
                email=email,
                display_name=display_name,
                domain=domain,
                provider="smtp",
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_username=email,
                smtp_password=password,
                smtp_use_tls=smtp_use_tls,
                imap_host=imap_host,
                imap_port=imap_port,
                imap_username=email,
                imap_password=password,
                status="ACTIVE",
                warmup_status="WARMUP_PENDING",
                is_active=1,
            )
            session.add(mailbox)
            session.flush()

            # Ensure DomainHealth record exists
            dh = session.query(DomainHealth).filter_by(domain=domain).first()
            if not dh:
                session.add(DomainHealth(domain=domain))
                print(f"   Created domain health for: {domain}")

            print(f"   OK  {email} - created (id={mailbox.id}, {smtp_host}:{smtp_port})")
            created += 1

            # Test connection if requested
            if test_connections:
                from provider_adapter import SMTPProvider
                provider = SMTPProvider(mailbox)
                ok, msg = provider.test_connection()
                status = "OK" if ok else "FAIL"
                print(f"       Connection test: {status} - {msg}")

    print(f"\n{'=' * 60}")
    print(f"   Created: {created}  |  Skipped: {skipped}  |  Failed: {failed}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    test_conn = "--test-connections" in sys.argv

    try:
        seed_mailboxes(test_connections=test_conn, dry_run=dry_run)
    except Exception as e:
        log.error(f"Seed failed: {e}", exc_info=True)
        print(f"\nSeed failed: {e}\n")
        sys.exit(1)
