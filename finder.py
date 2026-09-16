"""
finder.py — Stage 3: Campaign-Aware Contact Discovery

4-strategy contact finder based on what data is already available:
  Strategy A: Apollo already gave email → skip
  Strategy B: Have domain, no name → Prospeo /domain-search
  Strategy C: Have website → scrape + LLM extraction
  Strategy D: B2B corporate → SearXNG search + LLM + Prospeo enrich (original flow)

Reads target_roles from campaign config instead of hardcoded roles.
Flags phone-only leads for manual outreach.

Usage:
    python finder.py
    python finder.py --dry-run
"""

import json
import sys
import time

from database import get_session, init_db
from models import Company, Contact, Research, Campaign
from search_client import search_google
from email_finder import find_email
from openrouter_client import call_llm_with_schema
from utils import get_logger, extract_domain, safe_json_dumps, safe_json_loads, utcnow

log = get_logger("finder")

# Default target roles (used when no campaign config exists)
DEFAULT_TARGET_ROLES = ["CTO", "VP Engineering", "Engineering Manager", "Head of Product", "Head of Engineering"]

# LinkedIn profile URL patterns to filter search results
_LINKEDIN_PROFILE_PATTERNS = ("linkedin.com/in/", "linkedin.com/pub/")

# Seconds to wait between companies (avoids SearXNG engine rate limits)
_SEARCH_DELAY = 4

CONTACT_EXTRACTION_PROMPT = """You are a sales intelligence assistant that identifies decision makers at companies.

You will receive search results. Your job is to pick the SINGLE best decision-maker contact.

Priority order for target roles (highest to lowest):
{roles_text}

Rules:
- You MUST always pick someone — never return empty strings if any person is visible in the results.
- Prefer people whose title or LinkedIn snippet mentions the company name.
- Skip recruiters, staffing agents, and job posters unless they are the only option.
- Extract the LinkedIn profile URL from the search result URL (must contain /in/).
- If multiple candidates exist, pick the most senior one.

Respond with ONLY a JSON object:
{{
  "name": "Full Name",
  "role": "Their exact job title",
  "linkedin_url": "https://linkedin.com/in/...",
  "first_name": "First",
  "last_name": "Last"
}}

If truly no person can be identified at all, return all empty strings."""


def _get_target_roles(session, company: Company) -> list[str]:
    """Load target roles from campaign or use defaults."""
    if company.campaign_id:
        campaign = session.query(Campaign).filter_by(id=company.campaign_id).first()
        if campaign and campaign.target_roles:
            roles = safe_json_loads(campaign.target_roles)
            if roles:
                return roles
    return DEFAULT_TARGET_ROLES


def _strategy_a_check(session, company: Company) -> bool:
    """Strategy A: Check if we already have a contact with email (e.g., from Apollo)."""
    existing = session.query(Contact).filter(
        Contact.company_id == company.id,
        Contact.email != None,
        Contact.email != "",
    ).first()
    if existing:
        log.info(f"  [Strategy A] Already have contact with email: {existing.name} ({existing.email})")
        if company.status == "QUALIFIED":
            company.status = "CONTACT_FOUND"
            company.contact_channel = "EMAIL"
            company.updated_at = utcnow()
        return True
    return False


def _strategy_d_search(session, company: Company, target_roles: list[str]) -> bool:
    """Strategy D: SearXNG search → LLM extraction → Prospeo email lookup."""
    # Build search query with campaign roles
    roles_expr = " OR ".join(f'"{r}"' for r in target_roles[:5])
    query = f'"{company.name}" ({roles_expr}) linkedin'
    results = search_google(query, max_results=10)

    # Filter to LinkedIn profile URLs first
    linkedin_results = [
        r for r in results
        if any(p in r.get("url", "") for p in _LINKEDIN_PROFILE_PATTERNS)
    ]

    # Fallback: broader query if no LinkedIn profiles found
    if not linkedin_results and not results:
        time.sleep(2)
        query2 = f'{company.name} {target_roles[0]} linkedin'
        results2 = search_google(query2, max_results=10)
        linkedin_results = [
            r for r in results2
            if any(p in r.get("url", "") for p in _LINKEDIN_PROFILE_PATTERNS)
        ]
        if not linkedin_results:
            linkedin_results = results2[:3]

    if not linkedin_results:
        linkedin_results = results[:3]

    if not linkedin_results:
        log.warning(f"  [Strategy D] No search results for {company.name}")
        return False

    # Build the prompt with campaign-specific roles
    roles_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(target_roles))
    prompt = CONTACT_EXTRACTION_PROMPT.format(roles_text=roles_text)

    search_text = "\n".join(
        f"- {r['title']}: {r['description']} ({r['url']})"
        for r in linkedin_results
    )

    data = call_llm_with_schema(
        system_prompt=prompt,
        user_prompt=(
            f"Company: {company.name}\n"
            f"Pick the best decision-maker from these search results:\n\n"
            f"{search_text}"
        ),
        required_keys=["name", "role"],
    )

    name = data.get("name", "").strip()
    if not name:
        log.warning(f"  [Strategy D] LLM could not extract a name from results")
        return False

    # Check for duplicate contact
    existing = session.query(Contact).filter_by(
        company_id=company.id, name=name
    ).first()
    if existing:
        log.info(f"  [SKIP] Contact already exists: {name}")
        return True  # Considered "found"

    # Extract name parts
    first_name = data.get("first_name", "").strip() or (name.split()[0] if name else "")
    last_name = data.get("last_name", "").strip() or (name.split()[-1] if len(name.split()) > 1 else "")

    if not last_name or first_name == last_name:
        log.warning(f"  [SKIP] Only first name found ('{name}') — need last name for email lookup")
        return False

    # Determine company domain
    domain = extract_domain(company.website or "")
    if not domain or any(d in domain for d in (
        "techcrunch.com", "producthunt.com", "ycombinator.com",
        "news", "reuters.com", "bloomberg.com", "upwork.com", "linkedin.com"
    )):
        domain = f"{company.name.lower().replace(' ', '')}.com"

    # ── Email Discovery via FullEnrich (primary) + Prospeo (fallback) ──
    contact_linkedin_from_search = data.get("linkedin_url", "")
    log.info(f"  Looking up email: {first_name} {last_name} @ {domain}")
    email_result = find_email(first_name, last_name, domain, linkedin_url=contact_linkedin_from_search)

    email_address = None
    email_source = "NOT_FOUND"
    verified_status = "NOT_FOUND"

    if email_result["email"]:
        email_address = email_result["email"]
        email_source = email_result["source"]
        verified_status = email_source
        log.info(f"  [FOUND] Email: {email_address} ({email_source})")
    else:
        log.warning(f"  [NO EMAIL] No verified email for {first_name} {last_name} @ {domain}")

    # Extract enrichment data (may come from FullEnrich or Prospeo)
    enrichment = email_result.get("enrichment") or {}
    e_contact = enrichment.get("contact") or {}
    e_company = enrichment.get("company") or {}

    contact_linkedin = e_contact.get("linkedin_url") or contact_linkedin_from_search
    contact_role = e_contact.get("current_job_title") or data.get("role", "")

    # Use enrichment ID from whichever provider found the email
    enrichment_id = e_contact.get("prospeo_id") or e_contact.get("fullenrich_id") or ""

    contact = Contact(
        company_id=company.id,
        name=name,
        role=contact_role,
        email=email_address,
        linkedin_url=contact_linkedin,
        email_source=email_source,
        verified=verified_status,
        headline=e_contact.get("headline") or "",
        timezone=e_contact.get("timezone") or "",
        city=e_contact.get("city") or "",
        prospeo_id=enrichment_id,
    )
    session.add(contact)

    # Update company with enrichment data (works for both FullEnrich and Prospeo)
    if e_company:
        if e_company.get("description_ai"):
            company.description_ai = e_company["description_ai"]
        if e_company.get("tech_stack"):
            company.tech_stack_json = safe_json_dumps(e_company["tech_stack"])
        if e_company.get("active_job_titles"):
            company.active_job_titles = safe_json_dumps(e_company["active_job_titles"])
        if e_company.get("keywords"):
            company.keywords = safe_json_dumps(e_company["keywords"])
        if e_company.get("funding_stage"):
            company.funding_stage = e_company["funding_stage"]
        if e_company.get("revenue_range"):
            company.revenue_range = e_company["revenue_range"]
        company.is_b2b = e_company.get("is_b2b", 0)
        if e_company.get("employee_count") and not company.employee_count:
            company.employee_count = e_company["employee_count"]
        if e_company.get("industry") and not company.industry:
            company.industry = e_company["industry"]
        company.prospeo_enriched = 1

    # Pre-fill Research if enrichment gave enough data
    tech_for_research = e_company.get("tech_stack") or e_company.get("technologies") or []
    if e_company.get("description_ai") and tech_for_research:
        existing_research = session.query(Research).filter_by(company_id=company.id).first()
        if not existing_research:
            enrichment_source = "fullenrich" if "FULLENRICH" in email_source else "prospeo"
            pre_research = Research(
                company_id=company.id,
                summary=e_company["description_ai"],
                tech_stack=safe_json_dumps(tech_for_research),
                pain_points=safe_json_dumps([]),
                recent_news=safe_json_dumps(e_company.get("active_job_titles", [])[:5]),
                raw_json=safe_json_dumps({"source": f"{enrichment_source}_prefill"}),
            )
            session.add(pre_research)

    # Update company status and contact channel
    company.status = "CONTACT_FOUND"
    company.contact_channel = "EMAIL" if email_address else "DISCOVERY_NEEDED"
    company.updated_at = utcnow()

    log.info(f"  [OK] Contact saved: {name} ({contact.role}) - {contact.email or 'NO EMAIL'} [{email_source}]")
    return True


def run(dry_run: bool = False):
    """Find decision makers for all qualified companies."""
    init_db()
    log.info("Starting decision maker finder...")

    with get_session() as session:
        companies = session.query(Company).filter_by(status="QUALIFIED").all()
        company_ids = [c.id for c in companies]
        log.info(f"Found {len(companies)} companies to find contacts for")

    found = 0
    failed = 0

    for cid in company_ids:
        with get_session() as session:
            company = session.query(Company).filter_by(id=cid).first()
            if not company:
                continue

            log.info(f"Finding contacts for: {company.name}")

            if dry_run:
                log.info(f"  [DRY RUN] Would search for contacts at {company.name}")
                continue

            try:
                # Load campaign-specific target roles
                target_roles = _get_target_roles(session, company)
                log.info(f"  Target roles: {target_roles[:3]}")

                # Strategy A: Already have a contact with email
                if _strategy_a_check(session, company):
                    found += 1
                    continue

                # Strategy D: Search → LLM → Prospeo (main flow)
                if _strategy_d_search(session, company, target_roles):
                    found += 1
                else:
                    # Flag phone-only companies
                    if company.phone:
                        company.contact_channel = "PHONE_ONLY"
                        log.info(f"  [PHONE_ONLY] {company.name} has phone: {company.phone}")
                    failed += 1

                time.sleep(_SEARCH_DELAY)

            except Exception as e:
                failed += 1
                log.error(f"  [FAIL] Failed for {company.name}: {e}")

    log.info(f"Found contacts for {found} companies ({failed} failed)")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Finder failed: {e}", exc_info=True)
        sys.exit(1)

