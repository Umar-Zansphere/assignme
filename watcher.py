"""
watcher.py — Stage 1: Multi-Source Signal Watcher

Reads active campaign config from DB, runs selected data sources in parallel,
normalizes all results into standard Company schema, and deduplicates.

Supported sources (5 universal):
  - google_maps  — Apify Google Maps Scraper
  - apollo       — Apollo.io API (B2B company + contact search)
  - searxng      — Self-hosted metasearch engine (web search catch-all)
  - crunchbase   — Funded startup search (via SearXNG fallback)
  - linkedin     — LinkedIn Jobs Scraper (Apify actor)

Usage:
    python watcher.py
    python watcher.py --dry-run
    python watcher.py --campaign-id 1
"""
import re
import sys
import json

from database import get_session, init_db
from models import Company, Signal, Contact, Campaign
from utils import get_logger, safe_json_loads, safe_json_dumps
from source_normalizer import normalize_batch
from apify_client import run_actor

log = get_logger("watcher")

# Legal suffixes to strip for deduplication
_LEGAL_SUFFIX_RE = re.compile(
    r"[,.]?\s*(Inc\.?|LLC\.?|Ltd\.?|Limited|Corp\.?|Corporation|\(.*?\)|GmbH|S\.A\.|B\.V\.|A\.G\.|PLC|LP|LLP)\s*$",
    re.IGNORECASE,
)


def _normalize_company_name(name: str) -> str:
    """Normalize a company name for deduplication."""
    name = name.strip()
    for _ in range(3):
        cleaned = _LEGAL_SUFFIX_RE.sub("", name).strip().rstrip(".,;")
        if cleaned == name:
            break
        name = cleaned
    return name.strip()


def _normalize_country(raw: str) -> str:
    """Normalize country codes to full names."""
    raw = (raw or "").strip().upper()
    mapping = {
        "US": "USA", "USA": "USA", "UNITED STATES": "USA",
        "UK": "UK", "GB": "UK", "GREAT BRITAIN": "UK", "UNITED KINGDOM": "UK",
        "DE": "Germany", "GERMANY": "Germany",
        "FR": "France", "FRANCE": "France",
        "CA": "Canada", "CANADA": "Canada",
        "AU": "Australia", "AUSTRALIA": "Australia",
        "IN": "India", "INDIA": "India",
        "SG": "Singapore", "SINGAPORE": "Singapore",
        "AE": "UAE", "UAE": "UAE", "UNITED ARAB EMIRATES": "UAE",
    }
    return mapping.get(raw, raw.title() if raw else "")


# ═══════════════════════════════════════════════════════════
# SOURCE RUNNERS — each returns list[dict] with normalized fields
# ═══════════════════════════════════════════════════════════

_COUNTRY_CODE_MAP = {
    "india": "IN", "usa": "US", "united states": "US", "uk": "GB",
    "united kingdom": "GB", "canada": "CA", "australia": "AU",
    "germany": "DE", "france": "FR", "singapore": "SG", "uae": "AE",
    "united arab emirates": "AE", "japan": "JP", "china": "CN",
    "south korea": "KR", "brazil": "BR", "mexico": "MX",
    "indonesia": "ID", "malaysia": "MY", "thailand": "TH",
    "vietnam": "VN", "philippines": "PH", "nigeria": "NG",
    "south africa": "ZA", "kenya": "KE", "egypt": "EG",
    "saudi arabia": "SA", "israel": "IL", "turkey": "TR",
    "netherlands": "NL", "spain": "ES", "italy": "IT",
    "sweden": "SE", "norway": "NO", "denmark": "DK",
    "finland": "FI", "ireland": "IE", "new zealand": "NZ",
    "colombia": "CO", "argentina": "AR", "chile": "CL",
    "taiwan": "TW", "hong kong": "HK", "bangladesh": "BD",
    "pakistan": "PK", "sri lanka": "LK", "nepal": "NP",
    # State/region → country fallback
    "kerala": "IN", "karnataka": "IN", "tamil nadu": "IN",
    "maharashtra": "IN", "delhi": "IN", "bangalore": "IN",
    "mumbai": "IN", "hyderabad": "IN", "pune": "IN", "chennai": "IN",
}


def _geography_to_country_code(geography: list[str]) -> str | None:
    """Convert campaign target_geography to an Apify country code (must be lowercase)."""
    for geo in geography:
        code = _COUNTRY_CODE_MAP.get(geo.lower().strip())
        if code:
            return code.lower()
        # If it's already a 2-letter code
        if len(geo.strip()) == 2 and geo.strip().isalpha():
            return geo.strip().lower()
    return None


def _run_google_maps(queries: list[str], campaign: Campaign) -> list[dict]:
    """Run Google Maps Apify actor with campaign queries and geography filter."""
    from google_maps_client import search_places
    log.info(f"Running Google Maps source ({len(queries)} queries)...")

    # Extract country code from campaign geography
    geography = safe_json_loads(campaign.target_geography) or []
    country_code = _geography_to_country_code(geography)
    if country_code:
        log.info(f"  Filtering Google Maps to country: {country_code}")

    results = search_places(queries, max_results=100, country=country_code)

    # Post-filter: remove results from non-target countries as safety net
    if geography:
        geo_lower = {g.lower().strip() for g in geography}
        # Also include country codes in the filter set
        if country_code:
            geo_lower.add(country_code.lower())
        filtered = []
        for r in results:
            r_country = (r.get("country") or "").lower().strip()
            r_address = (r.get("address") or "").lower()
            # Accept if country matches OR any geography term appears in the address
            if (r_country in geo_lower or
                any(g in r_address or g in r_country for g in geo_lower) or
                not r_country):  # Keep if country is unknown (let scorer decide)
                filtered.append(r)
            else:
                log.debug(f"  Filtered out '{r.get('name')}' — country '{r_country}' not in {geography}")
        log.info(f"  Geography filter: {len(results)} → {len(filtered)} results")
        return normalize_batch(filtered, "google_maps")

    return normalize_batch(results, "google_maps")


def _run_apollo(queries: list[str], campaign: Campaign) -> list[dict]:
    """Run Apollo.io API search with campaign config."""
    from apollo_client import search_organizations
    log.info(f"Running Apollo source ({len(queries)} queries)...")

    # Parse campaign config for Apollo-specific filters
    geography = safe_json_loads(campaign.target_geography) or []
    size_range = campaign.target_company_size or "1-500"
    parts = size_range.split("-")
    min_emp = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 1
    max_emp = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 500

    all_results = []
    for query in queries:
        results = search_organizations(
            keywords=[query],
            locations=geography if geography else None,
            min_employees=min_emp,
            max_employees=max_emp,
            per_page=25,
        )
        all_results.extend(results)

    return normalize_batch(all_results, "apollo")


def _run_searxng(queries: list[str]) -> list[dict]:
    """Run SearXNG web search and extract company info via LLM."""
    from search_client import search_web
    from openrouter_client import call_llm
    log.info(f"Running SearXNG source ({len(queries)} queries)...")

    all_results = []
    for query in queries:
        try:
            search_results = search_web(query, max_results=10)
        except Exception as e:
            log.warning(f"  SearXNG failed for '{query}': {e}")
            continue

        if not search_results:
            continue

        # Format search results for LLM extraction
        results_text = "\n".join(
            f"- Title: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  Snippet: {r.get('snippet', '')}"
            for r in search_results[:10]
        )

        try:
            extracted = call_llm(
                system_prompt="""You are a data extraction expert. From the web search results below,
extract a JSON array of companies found. For each company return:
{"name": "Company Name", "website": "https://...", "industry": "...", "country": "...", "description": "brief description"}

Only include actual companies (not news sites, directories, or platforms).
If no companies can be extracted, return an empty array [].
Respond with ONLY the JSON array.""",
                user_prompt=f"Search query: {query}\n\nResults:\n{results_text}",
            )
        except Exception as e:
            log.warning(f"  LLM extraction failed for '{query}': {e}")
            continue

        if isinstance(extracted, list):
            for item in extracted:
                if isinstance(item, dict) and item.get("name"):
                    item["source"] = "searxng"
                    item.setdefault("employee_count", None)
                    item.setdefault("linkedin_url", "")
                    item.setdefault("phone", "")
                    all_results.append(item)

    log.info(f"SearXNG extracted {len(all_results)} companies")
    return normalize_batch(all_results, "searxng")


def _run_linkedin(queries: list[str]) -> list[dict]:
    """Run LinkedIn Jobs Scraper via Apify (existing flow)."""
    from apify_client import get_linkedin_job_postings
    log.info(f"Running LinkedIn source...")

    try:
        raw_signals = get_linkedin_job_postings()
    except Exception as e:
        log.error(f"  LinkedIn scraper failed: {e}")
        return []

    # Normalize LinkedIn results to standard format
    results = []
    for sig in raw_signals:
        name = sig.get("company", "").strip()
        if not name or name.lower() in ("unknown", "n/a", "none"):
            continue
        results.append({
            "name": name,
            "website": sig.get("company_website", ""),
            "industry": sig.get("industry", ""),
            "country": sig.get("country", ""),
            "employee_count": sig.get("company_employee_count"),
            "linkedin_url": sig.get("company_linkedin_url", ""),
            "source": "linkedin",
            "phone": "",
            # LinkedIn-specific extras
            "_signal_type": sig.get("signal_type", "JOB_POSTING"),
            "_signal_title": sig.get("title", ""),
            "_signal_description": sig.get("description", ""),
            "_signal_url": sig.get("url", ""),
            "_signal_source": sig.get("source", "linkedin"),
            "_poster_name": sig.get("poster_name", ""),
            "_poster_title": sig.get("poster_title", ""),
            "_poster_profile": sig.get("poster_profile_url", ""),
        })

    log.info(f"LinkedIn returned {len(results)} companies")
    return normalize_batch(results, "linkedin")


def _run_clutch(queries: list[str]) -> list[dict]:
    """Run Clutch.co scraper via Apify."""
    log.info(f"Running Clutch source...")
    all_results = []
    for query in queries:
        try:
            raw = run_actor("epctex/clutchco-scraper", {
                "search": query,
                "maxItems": 20,
            })
            all_results.extend(raw)
        except Exception as e:
            log.error(f"  Clutch scraper failed for '{query}': {e}")
    return normalize_batch(all_results, "clutch")


def _run_yelp(queries: list[str], campaign: Campaign) -> list[dict]:
    """Run Yelp scraper via Apify."""
    log.info(f"Running Yelp source...")
    geography = safe_json_loads(campaign.target_geography) or []
    location = geography[0] if geography else "USA"
    
    all_results = []
    for query in queries:
        try:
            raw = run_actor("api-ninja/yelp-ultimate-scraper", {
                "searchTerms": [query],
                "locations": [location],
                "limit": 20,
            })
            all_results.extend(raw)
        except Exception as e:
            log.error(f"  Yelp scraper failed for '{query}': {e}")
    return normalize_batch(all_results, "yelp")


def _run_indeed(queries: list[str], campaign: Campaign) -> list[dict]:
    """Run Indeed scraper via Apify."""
    log.info(f"Running Indeed source...")
    geography = safe_json_loads(campaign.target_geography) or []
    location = geography[0] if geography else "USA"
    
    all_results = []
    for query in queries:
        try:
            raw = run_actor("kaix/indeed-scraper", {
                "position": query,
                "location": location,
                "maxItemsPerSearch": 20,
            })
            all_results.extend(raw)
        except Exception as e:
            log.error(f"  Indeed scraper failed for '{query}': {e}")
    return normalize_batch(all_results, "indeed")


def _run_upwork(queries: list[str]) -> list[dict]:
    """Run Upwork job scraper via Apify."""
    log.info(f"Running Upwork source...")
    all_results = []
    for query in queries:
        try:
            raw = run_actor("neatrat/upwork-job-scraper", {
                "keyword": query,
                "maxItems": 20,
            })
            all_results.extend(raw)
        except Exception as e:
            log.error(f"  Upwork scraper failed for '{query}': {e}")
            
    # The normalizer will extract client details into _contact_name.
    # We can use that later in the pipeline to find the actual company via LinkedIn.
    return normalize_batch(all_results, "upwork")


def _run_crunchbase(queries: list[str]) -> list[dict]:
    """Run Crunchbase scraper via Apify."""
    log.info(f"Running Crunchbase source...")
    all_results = []
    for query in queries:
        try:
            raw = run_actor("curious_coder/crunchbase-scraper", {
                "queries": [query],
                "maxResults": 20,
            })
            all_results.extend(raw)
        except Exception as e:
            log.error(f"  Crunchbase scraper failed for '{query}': {e}")
    return normalize_batch(all_results, "crunchbase")


def _run_yellowpages(queries: list[str], campaign: Campaign) -> list[dict]:
    """Run YellowPages scraper via Apify."""
    log.info(f"Running YellowPages source...")
    geography = safe_json_loads(campaign.target_geography) or []
    location = geography[0] if geography else "New York, NY"
    
    all_results = []
    for query in queries:
        try:
            raw = run_actor("delicious_zebu/yellowpages-usa-business-lead-scraper", {
                "searchTerms": [query],
                "locations": [location],
                "maxItems": 20,
            })
            all_results.extend(raw)
        except Exception as e:
            log.error(f"  YellowPages scraper failed for '{query}': {e}")
    return normalize_batch(all_results, "yellowpages")


# ═══════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════

def run_campaign(campaign: Campaign, dry_run: bool = False):
    """Run the watcher for a specific campaign."""
    log.info(f"Running watcher for campaign: '{campaign.name}' (id={campaign.id})")

    source_selection = safe_json_loads(campaign.source_selection) or ["searxng"]
    search_queries = safe_json_loads(campaign.search_queries) or {}

    if dry_run:
        log.info(f"[DRY RUN] Sources: {source_selection}")
        log.info(f"[DRY RUN] Queries: {json.dumps(search_queries, indent=2)}")
        return

    # ── Run selected sources ──
    all_results = []

    if "google_maps" in source_selection:
        queries = search_queries.get("google_maps", [])
        if queries:
            results = _run_google_maps(queries, campaign)
            all_results.extend(results)

    if "apollo" in source_selection:
        queries = search_queries.get("apollo", [])
        if queries:
            results = _run_apollo(queries, campaign)
            all_results.extend(results)

    if "searxng" in source_selection:
        queries = search_queries.get("searxng", [])
        if queries:
            results = _run_searxng(queries)
            all_results.extend(results)

    if "linkedin" in source_selection:
        queries = search_queries.get("linkedin", [])
        if queries:
            results = _run_linkedin(queries)
            all_results.extend(results)

    if "clutch" in source_selection:
        queries = search_queries.get("clutch", [])
        if queries:
            results = _run_clutch(queries)
            all_results.extend(results)

    if "yelp" in source_selection:
        queries = search_queries.get("yelp", [])
        if queries:
            results = _run_yelp(queries, campaign)
            all_results.extend(results)

    if "indeed" in source_selection:
        queries = search_queries.get("indeed", [])
        if queries:
            results = _run_indeed(queries, campaign)
            all_results.extend(results)

    if "upwork" in source_selection:
        queries = search_queries.get("upwork", [])
        if queries:
            results = _run_upwork(queries)
            all_results.extend(results)

    if "crunchbase" in source_selection:
        queries = search_queries.get("crunchbase", [])
        if queries:
            results = _run_crunchbase(queries)
            all_results.extend(results)

    if "yellowpages" in source_selection:
        queries = search_queries.get("yellowpages", [])
        if queries:
            results = _run_yellowpages(queries, campaign)
            all_results.extend(results)

    if not all_results:
        log.info("No results from any source. Exiting.")
        return

    log.info(f"Total raw results: {len(all_results)} from {len(source_selection)} sources")

    # ── Normalize, deduplicate, and store ──
    _store_results(all_results, campaign)


def _store_results(all_results: list[dict], campaign: Campaign):
    """Normalize, deduplicate, and store results in DB."""
    new_companies = 0
    new_signals = 0
    new_contacts = 0
    skipped = 0

    # Group by normalized company name for deduplication
    company_groups = {}
    for result in all_results:
        raw_name = result.get("name", "").strip()
        if not raw_name or raw_name.lower() in ("unknown", "n/a", "none", "null", "undefined") or len(raw_name) < 2:
            skipped += 1
            continue

        norm_name = _normalize_company_name(raw_name)
        if len(norm_name) < 2:
            skipped += 1
            continue

        if norm_name not in company_groups:
            company_groups[norm_name] = []
        company_groups[norm_name].append(result)

    log.info(f"Deduplicated: {len(all_results)} results → {len(company_groups)} unique companies")

    with get_session() as session:
        for company_name, results in company_groups.items():
            # Merge data from multiple sources (pick best for each field)
            primary = results[0]  # Use first result as base
            sources = list(set(r.get("source", "unknown") for r in results))

            # Merge fields — prefer non-empty values
            merged = {
                "website": "",
                "industry": "",
                "country": "",
                "employee_count": None,
                "linkedin_url": "",
                "phone": "",
                "address": "",
                "category": "",
                "rating": None,
                "description": "",
                "funding_stage": "",
            }
            for r in results:
                for key in merged:
                    val = r.get(key)
                    if val and not merged[key]:
                        merged[key] = val

            # Find or create company (scoped to this campaign)
            company = session.query(Company).filter_by(
                name=company_name, campaign_id=campaign.id
            ).first()
            if not company:
                company = next(
                    (obj for obj in session.new if isinstance(obj, Company)
                     and obj.name == company_name and obj.campaign_id == campaign.id),
                    None,
                )

            if not company:
                company = Company(
                    name=company_name,
                    website=merged["website"],
                    industry=merged["industry"],
                    country=_normalize_country(merged["country"]),
                    employee_count=merged["employee_count"],
                    linkedin_url=merged["linkedin_url"],
                    phone=merged["phone"],
                    address=merged["address"],
                    category=merged["category"],
                    rating=merged["rating"],
                    signal_source=sources[0],
                    signal_sources_json=safe_json_dumps(sources),
                    campaign_id=campaign.id,
                    status="ENRICHED",
                )

                # Determine contact channel based on available data
                has_email = any(r.get("_contact_email") for r in results)
                has_phone = bool(merged["phone"])
                if has_email:
                    company.contact_channel = "EMAIL"
                elif has_phone:
                    company.contact_channel = "PHONE_ONLY"
                else:
                    company.contact_channel = "DISCOVERY_NEEDED"

                session.add(company)
                session.flush()
                new_companies += 1
                log.info(f"  New: {company_name} (sources={sources}, channel={company.contact_channel})")
            else:
                # Update existing with richer data
                for field in ["website", "industry", "phone", "address", "category", "rating"]:
                    val = merged.get(field)
                    if val and not getattr(company, field, None):
                        setattr(company, field, val)
                if not company.country and merged["country"]:
                    company.country = _normalize_country(merged["country"])
                if not company.employee_count and merged["employee_count"]:
                    company.employee_count = merged["employee_count"]
                # Track additional sources
                existing_sources = safe_json_loads(company.signal_sources_json) or []
                updated_sources = list(set(existing_sources + sources))
                company.signal_sources_json = safe_json_dumps(updated_sources)

            # Create signals from each source result
            for r in results:
                source = r.get("source", "unknown")
                signal_type = r.get("_signal_type", "COMPANY_FOUND")
                raw_url = r.get("_signal_url") or r.get("maps_url") or r.get("website", "")

                existing = session.query(Signal).filter_by(
                    company_id=company.id,
                    signal_type=signal_type,
                    source=source,
                ).first()
                if not existing:
                    signal = Signal(
                        company_id=company.id,
                        signal_type=signal_type,
                        source=source,
                        title=r.get("_signal_title") or f"Found via {source}",
                        description=r.get("description") or r.get("_signal_description", ""),
                        raw_url=raw_url,
                    )
                    session.add(signal)
                    new_signals += 1

            # Create contacts from source data
            for r in results:
                contact_name = r.get("_contact_name", "")
                if contact_name and len(contact_name.split()) >= 2:
                    existing_contact = session.query(Contact).filter_by(
                        company_id=company.id, name=contact_name
                    ).first()
                    if not existing_contact:
                        contact = Contact(
                            company_id=company.id,
                            name=contact_name,
                            role=r.get("_contact_role", ""),
                            email=r.get("_contact_email", ""),
                            linkedin_url=r.get("_contact_linkedin", ""),
                            email_source=r.get("source", ""),
                            verified=r.get("verified"),
                        )
                        session.add(contact)
                        new_contacts += 1
                        log.info(f"    Contact: {contact_name} ({contact.role})")

    log.info(f"Watcher complete for campaign '{campaign.name}':")
    log.info(f"  {new_companies} new companies")
    log.info(f"  {new_signals} signals")
    log.info(f"  {new_contacts} contacts")
    log.info(f"  {skipped} skipped")


def run(dry_run: bool = False, campaign_id: int | None = None):
    """Run watcher for active campaign(s)."""
    init_db()

    with get_session() as session:
        if campaign_id:
            campaigns = session.query(Campaign).filter_by(id=campaign_id).all()
        else:
            campaigns = session.query(Campaign).filter(
                Campaign.status == "ACTIVE",
                Campaign.deleted_at.is_(None)
            ).all()

        if not campaigns:
            log.warning("No active campaigns found. Create a campaign first via the dashboard.")
            return

        campaign_data = [(c.id, c.name) for c in campaigns]

    log.info(f"Found {len(campaign_data)} active campaign(s)")

    for cid, cname in campaign_data:
        with get_session() as session:
            campaign = session.query(Campaign).filter_by(id=cid).first()
            if campaign:
                run_campaign(campaign, dry_run=dry_run)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    campaign_id = None
    for arg in sys.argv:
        if arg.startswith("--campaign-id="):
            campaign_id = int(arg.split("=")[1])
    try:
        run(dry_run=dry_run, campaign_id=campaign_id)
    except Exception as e:
        log.error(f"Watcher failed: {e}", exc_info=True)
        sys.exit(1)

