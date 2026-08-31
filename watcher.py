"""
watcher.py — Stage 1: Signal Watcher

Monitors LinkedIn for QA role hiring signals (USA focus).
Stores new companies with FULL enrichment data from LinkedIn
(website, industry, country, employee count, LinkedIn URL, job poster contact).

This eliminates the need for a separate enrichment stage — LinkedIn
already provides all the company data we need.

Status flow: NEW_SIGNAL (watcher) → ENRICHED (watcher sets this directly)

Usage:
    python watcher.py
    python watcher.py --dry-run
"""
import re
import sys
from database import get_session, init_db
from models import Company, Signal, Contact
from apify_client import get_linkedin_job_postings
from utils import get_logger
log = get_logger("watcher")

# Legal suffixes to strip for deduplication
_LEGAL_SUFFIX_RE = re.compile(
    r"[,.]?\s*(Inc\.?|LLC\.?|Ltd\.?|Limited|Corp\.?|Corporation|\(.*?\)|GmbH|S\.A\.|B\.V\.|A\.G\.|PLC|LP|LLP)\s*$",
    re.IGNORECASE,
)


def _normalize_company_name(name: str) -> str:
    """
    Normalize a company name for deduplication.

    Strips legal suffixes, trailing punctuation, and collapses whitespace.

    Examples:
        "Stripe, Inc."   → "Stripe"
        "Stripe Inc"     → "Stripe"
        "Acme Corp."     → "Acme"
        "OpenAI, LLC"    → "OpenAI"
    """
    name = name.strip()
    # Repeatedly strip to handle things like "Corp, Inc."
    for _ in range(3):
        cleaned = _LEGAL_SUFFIX_RE.sub("", name).strip().rstrip(".,;")
        if cleaned == name:
            break
        name = cleaned
    return name.strip()


def run(dry_run: bool = False):
    """Fetch QA job signals from LinkedIn (USA), store with full enrichment data."""
    init_db()
    log.info("Starting signal watcher (QA roles, USA, LinkedIn only)...")

    if dry_run:
        log.info("[DRY RUN] Would fetch from: linkedin")
        log.info("[DRY RUN] Skipping API calls")
        return

    # Fetch from LinkedIn (the only source we need)
    log.info("Fetching LinkedIn QA job postings (USA)...")
    try:
        all_signals = get_linkedin_job_postings()
        log.info(f"LinkedIn: {len(all_signals)} signals collected")
    except Exception as e:
        log.error(f"LinkedIn FAILED: {e}")
        all_signals = []

    if not all_signals:
        log.info("No signals collected. Exiting.")
        return

    # Deduplicate and store
    new_companies = 0
    new_signals = 0
    new_contacts = 0
    skipped = 0

    with get_session() as session:
        for signal_data in all_signals:
            raw_name = signal_data.get("company", "").strip()
            if not raw_name or raw_name.lower() in ("unknown", "n/a", "none", "null", "undefined") or len(raw_name) < 2:
                skipped += 1
                continue

            # Normalize name to prevent "Stripe Inc" vs "Stripe, Inc." duplicates
            company_name = _normalize_company_name(raw_name)
            if len(company_name) < 2:
                skipped += 1
                continue

            raw_url = signal_data.get("url", "").strip()

            # Find or create company (check DB and pending session objects)
            company = session.query(Company).filter_by(name=company_name).first()
            if not company:
                company = next((obj for obj in session.new if isinstance(obj, Company) and obj.name == company_name), None)

            if not company:
                # Create company with ALL LinkedIn enrichment data
                company = Company(
                    name=company_name,
                    website=signal_data.get("company_website", "").strip() or raw_url,
                    industry=signal_data.get("industry", "").strip(),
                    country=_normalize_country(signal_data.get("country", "")),
                    employee_count=signal_data.get("company_employee_count", 0),
                    linkedin_url=signal_data.get("company_linkedin_url", "").strip(),
                    # Skip enrichment — go straight to ENRICHED status
                    status="ENRICHED",
                )
                session.add(company)
                session.flush()  # Get the company ID
                new_companies += 1
                log.info(f"  New company: {company_name} (website={company.website}, industry={company.industry}, country={company.country}, employees={company.employee_count})")
            else:
                # Update existing company with richer data if we have it
                if signal_data.get("company_website") and not company.website:
                    company.website = signal_data["company_website"]
                if signal_data.get("industry") and not company.industry:
                    company.industry = signal_data["industry"]
                if signal_data.get("country") and not company.country:
                    company.country = _normalize_country(signal_data["country"])
                if signal_data.get("company_employee_count") and not company.employee_count:
                    company.employee_count = signal_data["company_employee_count"]
                if signal_data.get("company_linkedin_url") and not company.linkedin_url:
                    company.linkedin_url = signal_data["company_linkedin_url"]

            # Check for duplicate signal
            signal_type = signal_data.get("signal_type", "UNKNOWN")
            existing = session.query(Signal).filter_by(
                company_id=company.id,
                signal_type=signal_type,
                raw_url=raw_url,
            ).first()
            if not existing and raw_url:
                existing = next(
                    (obj for obj in session.new if isinstance(obj, Signal)
                     and obj.company_id == company.id
                     and obj.signal_type == signal_type
                     and obj.raw_url == raw_url),
                    None
                )

            if existing:
                skipped += 1
                continue

            # Create new signal with full data
            signal = Signal(
                company_id=company.id,
                signal_type=signal_type,
                source=signal_data.get("source", ""),
                title=signal_data.get("title", ""),
                description=signal_data.get("description", ""),
                raw_url=raw_url,
            )
            session.add(signal)
            new_signals += 1

            # ── Create contact from job poster (free lead!) ──
            poster_name = signal_data.get("poster_name", "").strip()
            poster_profile = signal_data.get("poster_profile_url", "").strip()
            if poster_name and len(poster_name.split()) >= 2:
                # Check if contact already exists
                existing_contact = session.query(Contact).filter_by(
                    company_id=company.id,
                    name=poster_name,
                ).first()
                if not existing_contact:
                    existing_contact = next(
                        (obj for obj in session.new if isinstance(obj, Contact)
                         and obj.company_id == company.id
                         and obj.name == poster_name),
                        None
                    )
                if not existing_contact:
                    contact = Contact(
                        company_id=company.id,
                        name=poster_name,
                        role=signal_data.get("poster_title", ""),
                        linkedin_url=poster_profile,
                    )
                    session.add(contact)
                    new_contacts += 1
                    log.info(f"    Contact lead: {poster_name} ({signal_data.get('poster_title', '')})")

    log.info(f"Watcher complete:")
    log.info(f"  {new_companies} new companies (with full enrichment)")
    log.info(f"  {new_signals} new signals")
    log.info(f"  {new_contacts} contact leads from job posters")
    log.info(f"  {skipped} skipped/duplicates")


def _normalize_country(raw: str) -> str:
    """Normalize country codes to full names."""
    raw = (raw or "").strip().upper()
    mapping = {
        "US": "USA",
        "USA": "USA",
        "UNITED STATES": "USA",
        "UK": "UK",
        "GB": "UK",
        "GREAT BRITAIN": "UK",
        "UNITED KINGDOM": "UK",
        "DE": "Germany",
        "GERMANY": "Germany",
        "FR": "France",
        "FRANCE": "France",
        "CA": "Canada",
        "CANADA": "Canada",
        "AU": "Australia",
        "AUSTRALIA": "Australia",
    }
    return mapping.get(raw, raw)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Watcher failed: {e}", exc_info=True)
        sys.exit(1)