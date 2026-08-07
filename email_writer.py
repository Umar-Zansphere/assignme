"""
email_writer.py — Stage 7: Email Writer

Uses OpenRouter LLM to generate personalized outreach emails.
Creates initial email + 2 follow-ups per company/contact pair.

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

log = get_logger("email_writer")

EMAIL_PROMPT = """You are an elite B2B cold email copywriter.

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


def _get_or_create_campaign(session) -> Campaign:
    """Get active campaign or create default."""
    campaign = session.query(Campaign).filter_by(is_active=1).first()
    if not campaign:
        campaign = Campaign(name="Default Campaign")
        session.add(campaign)
        session.flush()
    return campaign


def run(dry_run: bool = False):
    """Generate emails for all companies with status RESEARCH_DONE."""
    init_db()
    log.info("Starting email writer...")

    with get_session() as session:
        companies = session.query(Company).filter_by(status="RESEARCH_DONE").all()
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

            log.info(f"Writing emails for: {company_name}")

            if dry_run:
                log.info(f"  [DRY RUN] Would generate emails for {company_name}")
                continue

            contact = (
                session.query(Contact)
                .filter_by(company_id=cid, verified="VALID")
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

Research:
- Summary: {research_summary}
- Pain Points: {', '.join(pain_points[:3]) if pain_points else 'N/A'}
- Tech Stack: {', '.join(tech_stack[:5]) if tech_stack else 'N/A'}
- Recent News: {recent_news or 'N/A'}

Signal that triggered outreach: {recent_news or 'hiring engineers'}
"""

            data = call_llm_with_schema(
                system_prompt=EMAIL_PROMPT,
                user_prompt=user_prompt,
                required_keys=["subject", "body", "followup_1_body", "followup_2_body"],
            )

            # Step 3: Write emails and update company status in a fast isolated transaction
            with get_session() as session:
                campaign = _get_or_create_campaign(session)
                now = utcnow()

                subj = data["subject"]
                f1_subj = data.get("followup_1_subject") or f"Re: {subj}"
                f2_subj = data.get("followup_2_subject") or f"Re: {subj}"

                emails_to_create = [
                    {
                        "sequence_number": 0,
                        "subject": subj,
                        "body": data["body"],
                        "scheduled_at": now,  # Send immediately
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
                        campaign_id=campaign.id,
                        sequence_number=email_data["sequence_number"],
                        subject=email_data["subject"],
                        body=email_data["body"],
                        status="SCHEDULED",
                        scheduled_at=email_data["scheduled_at"],
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
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Email writer failed: {e}", exc_info=True)
        sys.exit(1)
