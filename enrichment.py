"""
enrichment.py — Stage 2: Company Enrichment

Takes companies with status NEW_SIGNAL and enriches them with
website, industry, country, employee count, LinkedIn, GitHub.

Usage:
    python enrichment.py
    python enrichment.py --dry-run
"""

import sys

from database import get_session, init_db
from models import Company
from apify_client import scrape_website
from openrouter_client import call_llm_with_schema
from utils import get_logger, extract_domain, utcnow

log = get_logger("enrichment")

ENRICHMENT_PROMPT = """You are a company research assistant.
Given the following website text from a company, extract structured information.

Respond with ONLY a JSON object containing these keys:
- "industry": string (e.g., "HealthTech", "FinTech", "SaaS", "E-commerce")
- "country": string (country where HQ is, e.g., "USA", "UK", "Germany")
- "employee_count": integer estimate (best guess from context, 0 if unknown)
- "linkedin_url": string (LinkedIn company page URL if mentioned, else "")
- "github_url": string (GitHub org URL if mentioned, else "")
- "description": string (one-line summary of what the company does)

If information is not available, use reasonable defaults or empty strings."""


def run(dry_run: bool = False):
    """Enrich all companies with status NEW_SIGNAL."""
    init_db()
    log.info("Starting company enrichment...")

    with get_session() as session:
        companies = session.query(Company).filter_by(status="NEW_SIGNAL").all()
        log.info(f"Found {len(companies)} companies to enrich")

        if not companies:
            log.info("No companies to enrich")
            return

        for company in companies:
            log.info(f"Enriching: {company.name}")

            if dry_run:
                log.info(f"  [DRY RUN] Would enrich {company.name}")
                continue

            try:
                # Try to scrape the company website
                website_text = ""
                website_url = company.website or ""

                if website_url:
                    domain = extract_domain(website_url)
                    if domain and not domain.startswith(("greenhouse", "lever", "producthunt")):
                        website_text = scrape_website(f"https://{domain}")

                # Use LLM to extract structured data
                if website_text:
                    user_prompt = f"Company name: {company.name}\n\nWebsite text:\n{website_text[:3000]}"
                else:
                    user_prompt = (
                        f"Company name: {company.name}\n"
                        f"Website: {website_url}\n\n"
                        "The website text is not available. "
                        "Based on the company name and any knowledge you have, provide your best estimates."
                    )

                data = call_llm_with_schema(
                    system_prompt=ENRICHMENT_PROMPT,
                    user_prompt=user_prompt,
                    required_keys=["industry", "country"],
                )

                # Update company
                company.industry = data.get("industry", "")
                company.country = data.get("country", "")
                company.employee_count = data.get("employee_count", 0)
                company.linkedin_url = data.get("linkedin_url", "")
                company.github_url = data.get("github_url", "")
                company.status = "ENRICHED"
                company.updated_at = utcnow()

                log.info(f"  [OK] Enriched: industry={company.industry}, country={company.country}")

            except Exception as e:
                log.error(f"  [FAIL] Failed to enrich {company.name}: {e}")
                # Don't change status — will retry next run


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Enrichment failed: {e}", exc_info=True)
        sys.exit(1)
