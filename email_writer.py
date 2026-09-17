"""
email_writer.py — Stage 6: Campaign-Aware Email Writer

Generates personalized email sequences using campaign framing:
  - our_offering, value_proposition, pitch_angle from campaign config
  - References the actual signal that triggered outreach
  - Uses campaign target_roles for tone adaptation

Falls back to generic cold email prompt when no campaign exists.

Usage:
    python email_writer.py
    python email_writer.py --dry-run
"""

import json
import sys
from datetime import timedelta

from database import get_session, init_db
from models import Company, Contact, Research, Email, Campaign
from openrouter_client import call_llm_with_schema
from config import FOLLOWUP_1_DELAY_DAYS, FOLLOWUP_2_DELAY_DAYS
from utils import get_logger, safe_json_loads, utcnow
from compliance import generate_unsubscribe_token
from debug_contact import ensure_debug_contact, inject_debug_contact_for_all_campaigns

log = get_logger("email_writer")

DEBUG_MODE = False

# Default prompt (no campaign)
EMAIL_PROMPT_DEFAULT = """You are an elite B2B cold email copywriter.

Write a personalized cold email sequence for outbound sales outreach.

Rules:
- Keep each email under 150 words
- Be conversational, not salesy
- Reference the specific signal that triggered outreach
- Reference one specific pain point from the research
- Don't use generic phrases like "I hope this finds you well"
- Include a clear, low-friction CTA (e.g., "Worth a quick chat?")
- Follow-ups should add new value, not just "checking in"
- Use the decision maker's first name

Respond with ONLY a JSON object containing:
- "subject": string (email subject line, short and curiosity-driving)
- "body": string (initial email body, use \\n for line breaks)
- "followup_1_subject": string (follow-up 1 subject, can be "Re: <original subject>")
- "followup_1_body": string (follow-up 1 body — different angle, new value)
- "followup_2_subject": string (follow-up 2 subject)
- "followup_2_body": string (follow-up 2 body — breakup email, last attempt)
"""


def _build_campaign_email_prompt(campaign: Campaign) -> str:
    """Build campaign-specific email writing prompt."""
    our_offering = campaign.our_offering or "our solution"
    value_prop = campaign.value_proposition or ""
    pitch_angle = campaign.pitch_angle or ""

    return f"""You are an elite B2B cold email copywriter working on a targeted campaign.

Our company provides: {our_offering}
Value proposition: {value_prop}
Outreach angle: {pitch_angle}

Write a personalized cold email sequence that frames our offering around the
specific needs of this company based on the research and signals below.

Rules:
- Keep each email under 150 words
- Be conversational, NOT salesy — sound like a peer, not a vendor
- Reference the specific signal or discovery that triggered outreach
- Reference one specific pain point from the research
- Don't use generic phrases like "I hope this finds you well"
- Frame our offering as solving THEIR specific problem
- Include a clear, low-friction CTA (e.g., "Worth a quick chat?")
- Follow-ups should add new value, not just "checking in"
- Use the decision maker's first name
- Adapt tone to the role (e.g., technical for CTO, business-focused for CEO)

Respond with ONLY a JSON object containing:
- "subject": string (email subject line, short and curiosity-driving)
- "body": string (initial email body, use \\n for line breaks)
- "followup_1_subject": string
- "followup_1_body": string (different angle, new value)
- "followup_2_subject": string
- "followup_2_body": string (breakup email, last attempt)
"""


def _get_or_create_campaign(session) -> Campaign:
    """Get active campaign or create default."""
    campaign = session.query(Campaign).filter_by(is_active=1).first()
    if not campaign:
        campaign = Campaign(name="Default Campaign")
        session.add(campaign)
        session.flush()
    return campaign


def run(dry_run: bool = False, target_campaign_id: int | None = None):
    """Generate 3-step email sequences for RESEARCH_DONE companies."""
    init_db()
    log.info("Starting email writer agent...")

    # For testing: If NO emails exist at all, inject our debug contact
    if DEBUG_MODE:
        try:
            with get_session() as session:
                inject_debug_contact_for_all_campaigns(session)
        except Exception as e:
            log.warning(f"Debug contact injection failed (non-fatal): {e}")

    with get_session() as session:
        # Only write emails for companies from active campaigns
        from utils import get_active_campaign_ids
        active_ids = get_active_campaign_ids(session)
        if target_campaign_id:
            if target_campaign_id not in active_ids:
                log.info(f"Campaign {target_campaign_id} is not active. Skipping.")
                return
            active_ids = [target_campaign_id]
        
        companies = session.query(Company).filter(
            Company.status == "RESEARCH_DONE",
            Company.campaign_id.in_(active_ids)
        ).all()
        company_ids = [c.id for c in companies]
        log.info(f"Found {len(companies)} companies to write emails for")

    written = 0

    for cid in company_ids:
        # Step 1: Read company, contact, research data
        with get_session() as session:
            company = session.query(Company).filter_by(id=cid).first()
            if not company:
                continue

            company_name = company.name
            company_industry = company.industry or "Unknown"
            company_country = company.country or "Unknown"
            company_employees = company.employee_count or "Unknown"
            company_campaign_id = company.campaign_id
            company_signal_source = company.signal_source or "unknown"

            log.info(f"Writing emails for: {company_name}")

            if dry_run:
                log.info(f"  [DRY RUN] Would generate emails for {company_name}")
                continue

            # Load campaign-specific prompt
            campaign = None
            if company_campaign_id:
                campaign = session.query(Campaign).filter_by(id=company_campaign_id).first()

            if campaign:
                system_prompt = _build_campaign_email_prompt(campaign)
                log.info(f"  Using campaign prompt: '{campaign.name}'")
            else:
                system_prompt = EMAIL_PROMPT_DEFAULT
                log.info("  Using default email prompt")

            # Accept any contact that has a real email address
            _ACCEPTED_VERIFIED = [
                "PROSPEO_VERIFIED", "FULLENRICH_VERIFIED", "VALID", "APIFY_VERIFIED", "APOLLO_VERIFIED",
                "SMTP_VERIFIED", "WEB_SCRAPED", "PATTERN_ACCEPTED",
            ]
            contact = (
                session.query(Contact)
                .filter_by(company_id=cid)
                .filter(
                    Contact.verified.in_(_ACCEPTED_VERIFIED)
                    | (
                        (Contact.email != None) &
                        (Contact.email != "") &
                        (Contact.verified != "NOT_FOUND")
                    )
                )
                .order_by(
                    Contact.verified.in_(_ACCEPTED_VERIFIED).desc()
                )
                .first()
            )
            if not contact:
                log.warning(f"  No valid contact for {company_name}, skipping")
                continue

            contact_id = contact.id
            contact_name = contact.name
            contact_role = contact.role

            research = session.query(Research).filter_by(company_id=cid).first()
            if not research:
                log.warning(f"  No research for {company_name}, skipping")
                continue

            research_summary = research.summary or ""
            pain_points = safe_json_loads(research.pain_points) or []
            tech_stack = safe_json_loads(research.tech_stack) or []
            recent_news = research.recent_news or ""

            # Get campaign fit notes if available
            raw_data = safe_json_loads(research.raw_json) or {}
            campaign_fit = raw_data.get("campaign_fit_notes", "")

        # Step 2: Call LLM outside of database transaction
        try:
            first_name = contact_name.split()[0] if contact_name else "there"

            user_prompt = f"""Decision Maker:
- Name: {contact_name}
- First Name: {first_name}
- Role: {contact_role}

Company:
- Name: {company_name}
- Industry: {company_industry}
- Country: {company_country}
- Employee Count: {company_employees}
- Discovery Source: {company_signal_source}

Research:
- Summary: {research_summary}
- Pain Points: {', '.join(pain_points[:3]) if pain_points else 'N/A'}
- Tech Stack: {', '.join(tech_stack[:5]) if tech_stack else 'N/A'}
- Recent News: {recent_news or 'N/A'}
{f'- Campaign Fit: {campaign_fit}' if campaign_fit else ''}

Signal that triggered outreach: {recent_news or f'Discovered via {company_signal_source}'}
"""

            data = call_llm_with_schema(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                required_keys=["subject", "body", "followup_1_body", "followup_2_body"],
            )

            # Step 3: Write emails and update company status
            with get_session() as session:
                # Use the company's campaign or get/create one
                if company_campaign_id:
                    campaign_obj = session.query(Campaign).filter_by(id=company_campaign_id).first()
                    if not campaign_obj:
                        campaign_obj = _get_or_create_campaign(session)
                else:
                    campaign_obj = _get_or_create_campaign(session)

                now = utcnow()

                subj = data["subject"]
                f1_subj = data.get("followup_1_subject") or f"Re: {subj}"
                f2_subj = data.get("followup_2_subject") or f"Re: {subj}"

                emails_to_create = [
                    {
                        "sequence_number": 0,
                        "subject": subj,
                        "body": data["body"],
                        "scheduled_at": now,
                    },
                    {
                        "sequence_number": 1,
                        "subject": f1_subj,
                        "body": data["followup_1_body"],
                        "scheduled_at": now + timedelta(days=FOLLOWUP_1_DELAY_DAYS),
                    },
                    {
                        "sequence_number": 2,
                        "subject": f2_subj,
                        "body": data["followup_2_body"],
                        "scheduled_at": now + timedelta(days=FOLLOWUP_2_DELAY_DAYS),
                    },
                ]

                for email_data in emails_to_create:
                    email = Email(
                        company_id=cid,
                        contact_id=contact_id,
                        campaign_id=campaign_obj.id,
                        sequence_number=email_data["sequence_number"],
                        subject=email_data["subject"],
                        body=email_data["body"],
                        status="SCHEDULED",
                        scheduled_at=email_data["scheduled_at"],
                        unsubscribe_token=generate_unsubscribe_token(),
                    )
                    session.add(email)

                comp = session.query(Company).filter_by(id=cid).first()
                if comp:
                    comp.status = "EMAIL_READY"
                    comp.updated_at = utcnow()

                written += 1
                log.info(f"  [OK] Generated 3 emails (subject: {data['subject'][:50]}...)")

        except Exception as e:
            log.error(f"  [FAIL] Email writing failed for {company_name}: {e}")

    log.info(f"Generated emails for {written} companies")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    target_campaign_id = None
    for arg in sys.argv:
        if arg.startswith("--campaign-id="):
            target_campaign_id = int(arg.split("=")[1])
            
    try:
        run(dry_run=dry_run, target_campaign_id=target_campaign_id)
    except Exception as e:
        log.error(f"Email writer failed: {e}", exc_info=True)
        sys.exit(1)

