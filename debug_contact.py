"""
debug_contact.py - Hardcoded debug/QA contact injected into every campaign.

Ensures that umar.mohamed@zansphere.com receives every campaign email,
which enables:
  - Verifying inbox delivery across mailbox rotations
  - Testing unsubscribe links
  - Reviewing email template rendering
  - Validating follow-up sequencing
  - Debugging SMTP/IMAP connectivity

Usage (auto-called by email_writer.py):
    from debug_contact import ensure_debug_contact

    # Inside your email writing loop, before writing emails:
    ensure_debug_contact(session, campaign)
"""

from models import Company, Contact, Signal
from utils import get_logger, utcnow, safe_json_dumps

log = get_logger("debug_contact")


# --- HARDCODED DEBUG CONTACT ---
# This is ALWAYS included in every campaign, regardless of any filters.
# Change this to your own email to receive all test emails.
DEBUG_EMAIL = "umar.mohamed@zansphere.com"
DEBUG_NAME = "Umar Mohamed"
DEBUG_COMPANY = "Zansphere (Debug)"
DEBUG_DOMAIN = "zansphere.com"


def ensure_debug_contact(session, campaign) -> tuple:
    """
    Ensure the debug contact exists in the DB and is linked to the given campaign.

    Creates:
      - A Company record for "Zansphere (Debug)" if not exists
      - A Contact record for the debug email if not exists
      - A Signal record linking to the campaign

    Returns:
        (company, contact) tuple

    This function is idempotent - safe to call multiple times.
    """
    campaign_id = campaign.id if campaign else None

    # --- Company ---
    company = (
        session.query(Company)
        .filter_by(name=DEBUG_COMPANY, campaign_id=campaign_id)
        .first()
    )

    if not company:
        company = Company(
            name=DEBUG_COMPANY,
            domain=DEBUG_DOMAIN,
            industry="Software Development",
            country="India",
            description="Internal debug/QA company - receives all campaign emails for testing.",
            employee_count=10,
            signal_source="debug_seed",
            signal_sources_json=safe_json_dumps(["debug_seed"]),
            campaign_id=campaign_id,
            status="EMAIL_READY",
        )
        session.add(company)
        session.flush()
        log.info(f"[DEBUG] Created debug company: {DEBUG_COMPANY} (id={company.id})")

        # Add a signal
        session.add(Signal(
            company_id=company.id,
            source="debug_seed",
            signal_type="DEBUG_QA_CONTACT",
            title="Debug contact - auto-injected for QA",
            raw_json=safe_json_dumps({
                "purpose": "Receives all campaign emails for testing",
                "email": DEBUG_EMAIL,
            }),
        ))
    else:
        # Ensure it's in the right status for email writing
        if company.status in ("ENRICHED", "SCORED"):
            company.status = "EMAIL_READY"

    # --- Contact ---
    contact = (
        session.query(Contact)
        .filter_by(company_id=company.id, email=DEBUG_EMAIL)
        .first()
    )

    if not contact:
        contact = Contact(
            company_id=company.id,
            name=DEBUG_NAME,
            email=DEBUG_EMAIL,
            role="Founder & CEO",
            source="debug_seed",
            channel="EMAIL",
            verified="VALID",
        )
        session.add(contact)
        session.flush()
        log.info(f"[DEBUG] Created debug contact: {DEBUG_EMAIL} (id={contact.id})")

    return company, contact


def inject_debug_contact_for_all_campaigns(session):
    """
    Ensure the debug contact exists for ALL active campaigns.
    Called during system startup or manually for verification.
    """
    from models import Campaign

    campaigns = session.query(Campaign).filter_by(is_active=1).all()

    if not campaigns:
        log.info("[DEBUG] No active campaigns found, skipping debug contact injection")
        return

    for campaign in campaigns:
        company, contact = ensure_debug_contact(session, campaign)
        log.info(
            f"[DEBUG] Campaign '{campaign.name}' (id={campaign.id}): "
            f"debug company={company.id}, contact={contact.id}"
        )

    log.info(f"[DEBUG] Debug contact ensured for {len(campaigns)} campaign(s)")
