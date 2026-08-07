"""
watcher.py — Stage 1: Signal Watcher
Monitors job boards (Greenhouse, Lever, LinkedIn), Product Hunt, and RSS feeds for buying signals.
Stores new companies and their signals in the database.
Usage:
    python watcher.py
    python watcher.py --dry-run
"""
import sys
from database import get_session, init_db
from models import Company, Signal
from apify_client import get_new_job_postings, get_linkedin_job_postings, get_product_launches, get_company_news
from utils import get_logger
log = get_logger("watcher")
def run(dry_run: bool = False):
    """Fetch signals from all sources and store new ones."""
    init_db()
    log.info("Starting signal watcher...")
    # Collect signals from all sources
    all_signals = []
    if dry_run:
        log.info("[DRY RUN] Would fetch from: greenhouse, lever, linkedin, producthunt, rss")
        log.info("[DRY RUN] Would fetch from: linkedin")
        log.info("[DRY RUN] Skipping API calls")
        return
    log.info("Fetching job postings (Greenhouse, Lever)...")
    all_signals.extend(get_new_job_postings(boards=["greenhouse", "lever"]))
    log.info("Fetching LinkedIn job postings...")
    all_signals.extend(get_linkedin_job_postings())
    log.info("Fetching Product Hunt launches...")
    all_signals.extend(get_product_launches())
    log.info("Fetching company news...")
    all_signals.extend(get_company_news())
    log.info(f"Collected {len(all_signals)} raw signals")
    # Deduplicate and store
    new_count = 0
    with get_session() as session:
        for signal_data in all_signals:
            company_name = signal_data.get("company", "").strip()
            if not company_name or company_name.lower() in ("unknown", "n/a", "none", "null", "undefined") or len(company_name) < 2:
                continue

            raw_url = signal_data.get("url", "").strip()

            # Find or create company (check DB and pending session objects)
            company = session.query(Company).filter_by(name=company_name).first()
            if not company:
                company = next((obj for obj in session.new if isinstance(obj, Company) and obj.name == company_name), None)

            if not company:
                company = Company(
                    name=company_name,
                    website=raw_url,
                    status="NEW_SIGNAL",
                )
                session.add(company)
                session.flush()  # Get the company ID

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
                continue

            # Create new signal
            signal = Signal(
                company_id=company.id,
                signal_type=signal_type,
                source=signal_data.get("source", ""),
                title=signal_data.get("title", ""),
                description=signal_data.get("description", ""),
                raw_url=raw_url,
            )
            session.add(signal)
            new_count += 1

    log.info(f"Stored {new_count} new signals")
if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Watcher failed: {e}", exc_info=True)
        sys.exit(1)