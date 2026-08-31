"""
finder.py — Stage 4: Decision Maker Finder

Finds CTO / Engineering Manager / Head of Product for qualified companies.
Uses SearXNG search to find LinkedIn profiles, then extracts contact info.
Uses Prospeo API for verified email discovery (no guessing, no SMTP probing).

Usage:
    python finder.py
    python finder.py --dry-run
"""

import sys
import time

from database import get_session, init_db
from models import Company, Contact
from search_client import search_google
from email_finder import find_email
from openrouter_client import call_llm_with_schema
from utils import get_logger, extract_domain, utcnow

log = get_logger("finder")

TARGET_ROLES = ["CTO", "VP Engineering", "Engineering Manager", "Head of Product", "Head of Engineering"]

# LinkedIn profile URL patterns to filter search results
_LINKEDIN_PROFILE_PATTERNS = ("linkedin.com/in/", "linkedin.com/pub/")

# Seconds to wait between companies (avoids SearXNG engine rate limits)
_SEARCH_DELAY = 4

CONTACT_EXTRACTION_PROMPT = """You are a sales intelligence assistant that identifies decision makers at software companies.

You will receive LinkedIn search results. Your job is to pick the SINGLE best decision-maker contact.

Priority order (highest to lowest):
1. CTO / Chief Technology Officer
2. VP Engineering / VP of Engineering
3. Head of Engineering / Head of Product
4. Engineering Manager / Director of Engineering
5. COO / CEO (only if no engineering leader found)

Rules:
- You MUST always pick someone — never return empty strings if any person is visible in the results.
- Prefer people whose title or LinkedIn snippet mentions the company name.
- Skip recruiters, staffing agents, and job posters unless they are the only option.
- Extract the LinkedIn profile URL from the search result URL (must contain /in/).
- If multiple candidates exist, pick the most senior one.

Respond with ONLY a JSON object:
{
  "name": "Full Name",
  "role": "Their exact job title",
  "linkedin_url": "https://linkedin.com/in/...",
  "first_name": "First",
  "last_name": "Last"
}

If truly no person can be identified at all, return all empty strings."""


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
                # ONE combined query instead of one-per-role — drastically cuts
                # the number of SearXNG requests and avoids engine rate limits.
                roles_expr = " OR ".join(f'"{r}"' for r in TARGET_ROLES)
                query = f'"{company.name}" ({roles_expr}) linkedin'
                results = search_google(query, max_results=10)

                # Filter to LinkedIn profile URLs first
                linkedin_results = [
                    r for r in results
                    if any(p in r.get("url", "") for p in _LINKEDIN_PROFILE_PATTERNS)
                ]

                # Fallback: broader query if no LinkedIn profiles found
                if not linkedin_results and results == []:
                    time.sleep(2)  # Brief pause before retry
                    query2 = f'{company.name} engineering leader linkedin'
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
                    failed += 1
                    log.warning(f"  [FAIL] No search results for {company.name}")
                    time.sleep(_SEARCH_DELAY)
                    continue

                # Pick the best-matching role from the results via LLM
                search_text = "\n".join(
                    f"- {r['title']}: {r['description']} ({r['url']})"
                    for r in linkedin_results
                )

                # Since we combined roles into one search, we ask the LLM to pick the best role
                role = "CTO, VP Engineering, Engineering Manager, Head of Engineering, Head of Product"

                data = call_llm_with_schema(
                    system_prompt=CONTACT_EXTRACTION_PROMPT,
                    user_prompt=(
                        f"Company: {company.name}\n"
                        f"Pick the best decision-maker from these search results "
                        f"(prefer in this order: {role}):\n\n"
                        f"{search_text}"
                    ),
                    required_keys=["name", "role"],
                )

                name = data.get("name", "").strip()
                if not name:
                    failed += 1
                    log.warning(f"  [FAIL] LLM could not extract a name from results")
                    time.sleep(_SEARCH_DELAY)
                    continue

                # Check for duplicate contact
                existing = session.query(Contact).filter_by(
                    company_id=company.id,
                    name=name,
                ).first()

                if existing:
                    log.info(f"  [SKIP] Contact already exists: {name}")
                    time.sleep(_SEARCH_DELAY)
                    continue

                # Extract name parts
                first_name = data.get("first_name", "").strip() or (name.split()[0] if name else "")
                last_name = data.get("last_name", "").strip() or (name.split()[-1] if len(name.split()) > 1 else "")

                # Determine company domain
                domain = extract_domain(company.website or "")
                if not domain or any(d in domain for d in ("techcrunch.com", "producthunt.com", "ycombinator.com", "news", "reuters.com", "bloomberg.com", "upwork.com", "linkedin.com")):
                    domain = f"{company.name.lower().replace(' ', '')}.com"

                # Find verified email via Prospeo (no guessing)
                log.info(f"  Looking up email: {first_name} {last_name} @ {domain}")
                email_result = find_email(first_name, last_name, domain)

                email_address = None
                email_source = "NOT_FOUND"
                verified_status = "NOT_FOUND"

                if email_result["email"]:
                    # Prospeo found a verified email
                    email_address = email_result["email"]
                    email_source = email_result["source"]
                    verified_status = email_source
                    log.info(f"  [FOUND] Email: {email_address} ({email_source})")
                else:
                    # No verified email — do NOT guess
                    log.warning(
                        f"  [NO EMAIL] No verified email for "
                        f"{first_name} {last_name} @ {domain}. "
                        f"Contact saved without email."
                    )

                contact = Contact(
                    company_id=company.id,
                    name=name,
                    role=data.get("role", role),
                    email=email_address,
                    linkedin_url=data.get("linkedin_url", ""),
                    email_source=email_source,
                    verified=verified_status,
                )
                session.add(contact)

                log.info(
                    f"  [OK] Contact saved: {name} ({contact.role}) "
                    f"- {contact.email or 'NO EMAIL'} [{email_source}]"
                )
                time.sleep(_SEARCH_DELAY)

                company.status = "CONTACT_FOUND"
                company.updated_at = utcnow()
                found += 1

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
