"""
finder.py — Stage 4: Decision Maker Finder

Finds CTO / Engineering Manager / Head of Product for qualified companies.
Uses Apify Google Search to find LinkedIn profiles, then extracts contact info.
Uses Apify overpowered/email-finder for email discovery (not guessing).

Usage:
    python finder.py
    python finder.py --dry-run
"""

import sys

from database import get_session, init_db
from models import Company, Contact
from apify_client import search_google, find_email
from openrouter_client import call_llm_with_schema
from utils import get_logger, extract_domain, guess_email, utcnow

log = get_logger("finder")

TARGET_ROLES = ["CTO", "VP Engineering", "Engineering Manager", "Head of Product", "Head of Engineering"]

CONTACT_EXTRACTION_PROMPT = """You are a sales intelligence assistant.

Given the following search results for finding a decision maker at a company,
extract the most likely contact.

Respond with ONLY a JSON object containing:
- "name": full name of the person (string)
- "role": their job title (string)
- "linkedin_url": their LinkedIn profile URL (string, or "")
- "first_name": their first name (string)
- "last_name": their last name (string)

If you cannot determine the information, return empty strings."""


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
                contact_found = False

                for role in TARGET_ROLES:
                    query = f'"{company.name}" "{role}" site:linkedin.com/in'
                    results = search_google(query, max_results=3)

                    if not results:
                        continue

                    # Use LLM to extract contact info from search results
                    search_text = "\n".join(
                        f"- {r['title']}: {r['description']} ({r['url']})"
                        for r in results
                    )

                    data = call_llm_with_schema(
                        system_prompt=CONTACT_EXTRACTION_PROMPT,
                        user_prompt=(
                            f"Company: {company.name}\n"
                            f"Target role: {role}\n\n"
                            f"Search results:\n{search_text}"
                        ),
                        required_keys=["name", "role"],
                    )

                    name = data.get("name", "").strip()
                    if not name:
                        continue

                    # Check for duplicate contact
                    existing = session.query(Contact).filter_by(
                        company_id=company.id,
                        name=name,
                    ).first()

                    if existing:
                        continue

                    # Extract name parts
                    first_name = data.get("first_name", "").strip() or (name.split()[0] if name else "")
                    last_name = data.get("last_name", "").strip() or (name.split()[-1] if len(name.split()) > 1 else "")

                    # Determine company domain
                    domain = extract_domain(company.website or "")
                    if not domain or any(d in domain for d in ("techcrunch.com", "producthunt.com", "ycombinator.com", "news", "reuters.com", "bloomberg.com", "upwork.com", "linkedin.com")):
                        domain = f"{company.name.lower().replace(' ', '')}.com"

                    # Step 1: Try Apify email finder (real verification)
                    log.info(f"  Looking up email via Apify: {first_name} {last_name} @ {domain}")
                    email_result = find_email(first_name, last_name, domain)

                    email_address = ""
                    email_source = ""
                    verified_status = None

                    if email_result["email"]:
                        # Apify found a real email
                        email_address = email_result["email"]
                        email_source = email_result["source"]  # APIFY_VERIFIED or APIFY_UNVERIFIED
                        verified_status = "APIFY_VERIFIED" if email_result["verified"] else "APIFY_UNVERIFIED"
                        log.info(f"  [APIFY] Found email: {email_address} ({email_source})")
                    else:
                        # Step 2: Fall back to pattern guessing
                        email_guesses = guess_email(first_name, last_name, domain) if domain else []
                        email_address = email_guesses[0] if email_guesses else f"{first_name.lower()}@{domain}"
                        email_source = "GUESSED"
                        verified_status = "GUESSED"
                        log.info(f"  [GUESSED] Pattern-derived email: {email_address}")

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
                    contact_found = True

                    log.info(f"  [OK] Found: {name} ({contact.role}) - {contact.email} [{email_source}]")
                    break  # One contact per company is enough

                if contact_found:
                    company.status = "CONTACT_FOUND"
                    company.updated_at = utcnow()
                    found += 1
                else:
                    failed += 1
                    log.warning(f"  [FAIL] No contacts found for {company.name}")

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
