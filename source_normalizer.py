"""
source_normalizer.py — Unified data normalization layer for all pipeline sources.

Every data source (Google Maps, Apollo, LinkedIn, Clutch, Yelp, Indeed,
Upwork, Crunchbase, YellowPages, SearXNG) produces raw dicts with
different field names.  This module maps each source's output into a
single **standard schema** so the rest of the pipeline (scorer, finder,
verifier, …) receives uniform data regardless of origin.

Usage:
    from source_normalizer import normalize_result, normalize_batch

    # Single result
    std = normalize_result(raw_dict, source="clutch")

    # Batch
    std_list = normalize_batch(raw_list, source="yelp")
"""

from utils import get_logger

log = get_logger("normalizer")


# ═══════════════════════════════════════════════════════════
# STANDARD SCHEMA DEFAULTS
# Every normalizer MUST return a dict with ALL of these keys.
# ═══════════════════════════════════════════════════════════

FIELD_DEFAULTS: dict = {
    # ── Core Identity ──
    "name": "",
    "website": "",
    "industry": "",
    "country": "",
    "description": "",

    # ── Size & Metrics ──
    "employee_count": None,
    "rating": None,
    "review_count": None,

    # ── Contact Info ──
    "phone": "",
    "address": "",
    "city": "",
    "state": "",
    "linkedin_url": "",

    # ── Financial ──
    "funding_stage": "",
    "revenue_range": "",

    # ── Pipeline Metadata ──
    "source": "",

    # ── Signal Metadata (prefixed with _) ──
    "_signal_type": "COMPANY_FOUND",
    "_signal_title": "",
    "_signal_description": "",
    "_signal_url": "",

    # ── Optional Contact Lead (prefixed with _) ──
    "_contact_name": "",
    "_contact_role": "",
    "_contact_email": "",
    "_contact_linkedin": "",
}


def _safe_str(val) -> str:
    """Coerce to stripped string, treating None/non-str as empty."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    return str(val).strip()


def _safe_int(val) -> int | None:
    """Coerce to int or None."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        cleaned = val.replace(",", "").strip()
        try:
            return int(cleaned)
        except (ValueError, TypeError):
            return None
    return None


def _safe_float(val) -> float | None:
    """Coerce to float or None."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except (ValueError, TypeError):
            return None
    return None


def _apply_defaults(result: dict) -> dict:
    """Ensure every key from FIELD_DEFAULTS is present."""
    merged = dict(FIELD_DEFAULTS)
    merged.update({k: v for k, v in result.items() if v is not None and v != ""})
    # Re-apply defaults for keys that ended up as None/empty
    for key, default in FIELD_DEFAULTS.items():
        if key not in merged or merged[key] is None:
            merged[key] = default
    return merged


# ═══════════════════════════════════════════════════════════
# PER-SOURCE NORMALIZERS
# ═══════════════════════════════════════════════════════════

def _normalize_google_maps(raw: dict) -> dict:
    """Normalize compass/google-maps-scraper output."""
    name = _safe_str(raw.get("title") or raw.get("name"))
    category = _safe_str(raw.get("categoryName"))
    if not category:
        cats = raw.get("categories", [])
        if cats and isinstance(cats[0], str):
            category = cats[0]

    return {
        "name": name,
        "website": _safe_str(raw.get("website") or raw.get("url")),
        "industry": category,
        "country": _safe_str(raw.get("countryCode") or raw.get("country")),
        "description": _safe_str(raw.get("description")),
        "employee_count": None,
        "rating": _safe_float(raw.get("totalScore") or raw.get("rating")),
        "review_count": _safe_int(raw.get("reviewsCount")),
        "phone": _safe_str(raw.get("phone") or raw.get("phoneUnformatted")),
        "address": _safe_str(raw.get("address")),
        "city": _safe_str(raw.get("city")),
        "state": _safe_str(raw.get("state")),
        "linkedin_url": "",
        "funding_stage": "",
        "revenue_range": "",
        "source": "google_maps",
        "_signal_type": "COMPANY_FOUND",
        "_signal_title": f"Found on Google Maps: {name}",
        "_signal_description": _safe_str(raw.get("description")),
        "_signal_url": _safe_str(raw.get("url") or raw.get("maps_url")),
        "_contact_name": _safe_str(raw.get("ownerName")),
        "_contact_role": "Owner" if raw.get("ownerName") else "",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


def _normalize_apollo(raw: dict) -> dict:
    """Normalize Apollo.io organization search output."""
    return {
        "name": _safe_str(raw.get("name")),
        "website": _safe_str(raw.get("website_url") or raw.get("primary_domain") or raw.get("website")),
        "industry": _safe_str(raw.get("industry")),
        "country": _safe_str(raw.get("country")),
        "description": _safe_str(raw.get("short_description") or raw.get("description")),
        "employee_count": _safe_int(raw.get("estimated_num_employees") or raw.get("employee_count")),
        "rating": None,
        "review_count": None,
        "phone": _safe_str(raw.get("phone")),
        "address": "",
        "city": _safe_str(raw.get("city")),
        "state": _safe_str(raw.get("state")),
        "linkedin_url": _safe_str(raw.get("linkedin_url")),
        "funding_stage": _safe_str(raw.get("latest_funding_stage") or raw.get("funding_stage")),
        "revenue_range": "",
        "source": "apollo",
        "_signal_type": "COMPANY_FOUND",
        "_signal_title": f"Found on Apollo: {_safe_str(raw.get('name'))}",
        "_signal_description": _safe_str(raw.get("short_description") or raw.get("description")),
        "_signal_url": _safe_str(raw.get("linkedin_url")),
        "_contact_name": _safe_str(raw.get("contact_name")),
        "_contact_role": _safe_str(raw.get("role")),
        "_contact_email": _safe_str(raw.get("email")),
        "_contact_linkedin": "",
    }


def _normalize_searxng(raw: dict) -> dict:
    """Normalize SearXNG LLM-extracted output."""
    return {
        "name": _safe_str(raw.get("name")),
        "website": _safe_str(raw.get("website")),
        "industry": _safe_str(raw.get("industry")),
        "country": _safe_str(raw.get("country")),
        "description": _safe_str(raw.get("description")),
        "employee_count": _safe_int(raw.get("employee_count")),
        "rating": None,
        "review_count": None,
        "phone": _safe_str(raw.get("phone")),
        "address": "",
        "city": "",
        "state": "",
        "linkedin_url": _safe_str(raw.get("linkedin_url")),
        "funding_stage": "",
        "revenue_range": "",
        "source": "searxng",
        "_signal_type": "COMPANY_FOUND",
        "_signal_title": f"Found via web search: {_safe_str(raw.get('name'))}",
        "_signal_description": _safe_str(raw.get("description")),
        "_signal_url": _safe_str(raw.get("website")),
        "_contact_name": "",
        "_contact_role": "",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


def _normalize_linkedin(raw: dict) -> dict:
    """Normalize LinkedIn Jobs Scraper output (from apify_client)."""
    # The LinkedIn scraper returns pre-normalized dicts from apify_client.py
    # with fields like company, title, url, company_website, etc.
    company = _safe_str(raw.get("company") or raw.get("companyName"))

    return {
        "name": company,
        "website": _safe_str(raw.get("company_website") or raw.get("companyWebsite")),
        "industry": _safe_str(raw.get("industry") or raw.get("industries")),
        "country": _safe_str(raw.get("country")),
        "description": _safe_str(raw.get("company_description") or raw.get("companyDescription")),
        "employee_count": _safe_int(raw.get("company_employee_count") or raw.get("companyEmployeesCount")),
        "rating": None,
        "review_count": None,
        "phone": "",
        "address": "",
        "city": "",
        "state": "",
        "linkedin_url": _safe_str(raw.get("company_linkedin_url") or raw.get("companyLinkedinUrl")),
        "funding_stage": "",
        "revenue_range": "",
        "source": "linkedin",
        "_signal_type": "JOB_POSTING",
        "_signal_title": _safe_str(raw.get("title")),
        "_signal_description": _safe_str(raw.get("description", ""))[:2000],
        "_signal_url": _safe_str(raw.get("url") or raw.get("jobUrl")),
        "_contact_name": _safe_str(raw.get("poster_name") or raw.get("jobPosterName")),
        "_contact_role": _safe_str(raw.get("poster_title") or raw.get("jobPosterTitle")),
        "_contact_email": "",
        "_contact_linkedin": _safe_str(raw.get("poster_profile_url") or raw.get("jobPosterProfileUrl")),
    }


def _normalize_clutch(raw: dict) -> dict:
    """Normalize epctex/clutchco-scraper output."""
    name = _safe_str(raw.get("title") or raw.get("name"))

    # Parse location into components
    location = _safe_str(raw.get("location") or raw.get("headquarters"))
    city, state, country = "", "", ""
    if location:
        parts = [p.strip() for p in location.split(",")]
        if len(parts) >= 3:
            city, state, country = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            city, country = parts[0], parts[1]
        elif len(parts) == 1:
            country = parts[0]

    # Employee size — Clutch returns ranges like "50 - 249"
    emp_str = _safe_str(raw.get("employeeSize") or raw.get("employees"))
    emp_count = None
    if emp_str:
        # Take the midpoint of the range
        nums = [int(n) for n in emp_str.replace(",", "").split("-") if n.strip().isdigit()]
        if nums:
            emp_count = sum(nums) // len(nums)

    # Build description from hourly rate + min project size
    desc_parts = []
    hourly = _safe_str(raw.get("hourlyRate"))
    if hourly:
        desc_parts.append(f"Hourly rate: {hourly}")
    min_proj = _safe_str(raw.get("minProjectSize"))
    if min_proj:
        desc_parts.append(f"Min project: {min_proj}")

    services = raw.get("services", [])
    industry = ""
    if isinstance(services, list) and services:
        industry = ", ".join(services[:3]) if isinstance(services[0], str) else ""
    elif isinstance(services, str):
        industry = services

    return {
        "name": name,
        "website": _safe_str(raw.get("websiteUrl") or raw.get("website")),
        "industry": industry,
        "country": country,
        "description": "; ".join(desc_parts) if desc_parts else _safe_str(raw.get("bio")),
        "employee_count": emp_count,
        "rating": _safe_float(raw.get("rating")),
        "review_count": _safe_int(raw.get("reviewCount")),
        "phone": _safe_str(raw.get("phone")),
        "address": "",
        "city": city,
        "state": state,
        "linkedin_url": _safe_str(raw.get("linkedin") or raw.get("linkedinUrl")),
        "funding_stage": "",
        "revenue_range": "",
        "source": "clutch",
        "_signal_type": "SERVICE_PROVIDER",
        "_signal_title": f"Clutch profile: {name}",
        "_signal_description": _safe_str(raw.get("bio") or raw.get("tagline")),
        "_signal_url": _safe_str(raw.get("profileLink") or raw.get("url")),
        "_contact_name": "",
        "_contact_role": "",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


def _normalize_yelp(raw: dict) -> dict:
    """Normalize api-ninja/yelp-ultimate-scraper output."""
    name = _safe_str(raw.get("name") or raw.get("businessName"))

    categories = raw.get("categories", [])
    industry = ""
    if isinstance(categories, list) and categories:
        if isinstance(categories[0], dict):
            industry = categories[0].get("title", "")
        elif isinstance(categories[0], str):
            industry = categories[0]
    elif isinstance(categories, str):
        industry = categories

    return {
        "name": name,
        "website": _safe_str(raw.get("website") or raw.get("businessUrl")),
        "industry": industry,
        "country": "USA",  # Yelp is primarily US-focused
        "description": _safe_str(raw.get("snippet")),
        "employee_count": None,
        "rating": _safe_float(raw.get("rating")),
        "review_count": _safe_int(raw.get("reviewCount")),
        "phone": _safe_str(raw.get("phone")),
        "address": _safe_str(
            raw.get("addressLine1", "") or raw.get("address", "")
        ),
        "city": _safe_str(raw.get("city")),
        "state": _safe_str(raw.get("regionCode") or raw.get("state")),
        "linkedin_url": "",
        "funding_stage": "",
        "revenue_range": "",
        "source": "yelp",
        "_signal_type": "COMPANY_FOUND",
        "_signal_title": f"Found on Yelp: {name}",
        "_signal_description": _safe_str(raw.get("snippet")),
        "_signal_url": _safe_str(raw.get("businessUrl") or raw.get("url")),
        "_contact_name": "",
        "_contact_role": "",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


def _normalize_indeed(raw: dict) -> dict:
    """Normalize kaix/indeed-scraper output."""
    company = _safe_str(raw.get("companyName") or raw.get("company"))

    return {
        "name": company,
        "website": _safe_str(raw.get("companyWebsite")),
        "industry": _safe_str(raw.get("industry")),
        "country": _safe_str(raw.get("country")),
        "description": _safe_str(raw.get("description", ""))[:2000],
        "employee_count": _safe_int(raw.get("companySize")),
        "rating": _safe_float(raw.get("companyRating")),
        "review_count": _safe_int(raw.get("reviewCount")),
        "phone": "",
        "address": "",
        "city": _safe_str(raw.get("city")),
        "state": _safe_str(raw.get("state")),
        "linkedin_url": "",
        "funding_stage": "",
        "revenue_range": _safe_str(raw.get("revenue")),
        "source": "indeed",
        "_signal_type": "JOB_POSTING",
        "_signal_title": _safe_str(raw.get("title")),
        "_signal_description": _safe_str(raw.get("description", ""))[:2000],
        "_signal_url": _safe_str(raw.get("jobUrl") or raw.get("url")),
        "_contact_name": "",
        "_contact_role": "",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


def _normalize_upwork(raw: dict) -> dict:
    """
    Normalize neatrat/upwork-job-scraper output.

    Upwork clients are often anonymous. We store the job poster / client
    details so the pipeline can later search LinkedIn for the actual
    company behind the posting.
    """
    # Client name — Upwork sometimes exposes it, sometimes not
    client_name = _safe_str(
        raw.get("clientName") or raw.get("client", {}).get("name", "") if isinstance(raw.get("client"), dict) else ""
    )
    # If no client name, use the job title as a placeholder signal
    name = client_name if client_name else _safe_str(raw.get("title", "Unknown Upwork Client"))

    # Skills as industry proxy
    skills = raw.get("skills", [])
    industry = ""
    if isinstance(skills, list) and skills:
        industry = ", ".join(skills[:5]) if isinstance(skills[0], str) else ""

    # Budget info for description
    budget = _safe_str(raw.get("budget"))
    job_type = _safe_str(raw.get("jobType"))
    desc_parts = []
    if budget:
        desc_parts.append(f"Budget: {budget}")
    if job_type:
        desc_parts.append(f"Type: {job_type}")
    exp_level = _safe_str(raw.get("experienceLevel"))
    if exp_level:
        desc_parts.append(f"Level: {exp_level}")

    return {
        "name": name,
        "website": "",
        "industry": industry,
        "country": _safe_str(raw.get("clientLocation") or raw.get("country")),
        "description": "; ".join(desc_parts) if desc_parts else _safe_str(raw.get("description", ""))[:500],
        "employee_count": None,
        "rating": None,
        "review_count": None,
        "phone": "",
        "address": "",
        "city": "",
        "state": "",
        "linkedin_url": "",
        "funding_stage": "",
        "revenue_range": _safe_str(raw.get("clientTotalSpent")),
        "source": "upwork",
        "_signal_type": "FREELANCE_POSTING",
        "_signal_title": _safe_str(raw.get("title")),
        "_signal_description": _safe_str(raw.get("description", ""))[:2000],
        "_signal_url": _safe_str(raw.get("url")),
        # Store client/poster details for LinkedIn company lookup
        "_contact_name": client_name,
        "_contact_role": "Upwork Client",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


def _normalize_crunchbase(raw: dict) -> dict:
    """Normalize curious_coder/crunchbase-scraper output."""
    name = _safe_str(
        raw.get("Identifier") or raw.get("name") or raw.get("identifier")
    )

    # Location — Crunchbase returns "City, State, Country"
    location = _safe_str(
        raw.get("Location Identifiers") or raw.get("location") or raw.get("headquarters")
    )
    city, state, country = "", "", ""
    if location:
        parts = [p.strip() for p in location.split(",")]
        if len(parts) >= 3:
            city, state, country = parts[0], parts[1], parts[-1]
        elif len(parts) == 2:
            city, country = parts[0], parts[-1]
        elif len(parts) == 1:
            country = parts[0]

    # Categories
    cats = raw.get("Categories") or raw.get("categories") or ""
    industry = cats if isinstance(cats, str) else ", ".join(cats[:3]) if isinstance(cats, list) else ""

    return {
        "name": name,
        "website": _safe_str(raw.get("Website") or raw.get("website")),
        "industry": industry,
        "country": country,
        "description": _safe_str(raw.get("Short Description") or raw.get("description")),
        "employee_count": _safe_int(raw.get("Num Employees") or raw.get("num_employees")),
        "rating": None,
        "review_count": None,
        "phone": _safe_str(raw.get("Phone Number") or raw.get("phone")),
        "address": "",
        "city": city,
        "state": state,
        "linkedin_url": _safe_str(raw.get("LinkedIn") or raw.get("linkedin")),
        "funding_stage": _safe_str(raw.get("Last Funding Type") or raw.get("Funding Total") or raw.get("funding_stage")),
        "revenue_range": _safe_str(raw.get("Revenue Range") or raw.get("revenue_range")),
        "source": "crunchbase",
        "_signal_type": "FUNDED_COMPANY",
        "_signal_title": f"Crunchbase: {name}",
        "_signal_description": _safe_str(raw.get("Short Description") or raw.get("description")),
        "_signal_url": _safe_str(raw.get("url") or raw.get("profileUrl")),
        "_contact_name": "",
        "_contact_role": "",
        "_contact_email": _safe_str(raw.get("Contact Email") or raw.get("contact_email")),
        "_contact_linkedin": "",
    }


def _normalize_yellowpages(raw: dict) -> dict:
    """Normalize yellowpages-usa-business-lead-scraper output."""
    name = _safe_str(raw.get("businessName") or raw.get("name"))

    categories = raw.get("categories", [])
    industry = _safe_str(raw.get("primaryCategory"))
    if not industry and isinstance(categories, list) and categories:
        industry = categories[0] if isinstance(categories[0], str) else ""

    return {
        "name": name,
        "website": _safe_str(raw.get("website")),
        "industry": industry,
        "country": "USA",  # YellowPages is US-only
        "description": _safe_str(raw.get("snippet")),
        "employee_count": None,
        "rating": _safe_float(raw.get("rating")),
        "review_count": _safe_int(raw.get("reviewCount")),
        "phone": _safe_str(raw.get("phone")),
        "address": _safe_str(raw.get("fullAddress") or raw.get("address")),
        "city": _safe_str(raw.get("city")),
        "state": _safe_str(raw.get("state")),
        "linkedin_url": "",
        "funding_stage": "",
        "revenue_range": "",
        "source": "yellowpages",
        "_signal_type": "COMPANY_FOUND",
        "_signal_title": f"YellowPages: {name}",
        "_signal_description": _safe_str(raw.get("snippet")),
        "_signal_url": _safe_str(raw.get("yellowPagesUrl") or raw.get("url")),
        "_contact_name": "",
        "_contact_role": "",
        "_contact_email": "",
        "_contact_linkedin": "",
    }


# ═══════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════

_NORMALIZERS = {
    "google_maps": _normalize_google_maps,
    "apollo": _normalize_apollo,
    "searxng": _normalize_searxng,
    "linkedin": _normalize_linkedin,
    "clutch": _normalize_clutch,
    "yelp": _normalize_yelp,
    "indeed": _normalize_indeed,
    "upwork": _normalize_upwork,
    "crunchbase": _normalize_crunchbase,
    "yellowpages": _normalize_yellowpages,
}


def normalize_result(raw: dict, source: str) -> dict | None:
    """
    Normalize a single raw result dict from any source into the standard schema.

    Args:
        raw: Raw result dict from a source runner or Apify actor.
        source: Source key (e.g., "google_maps", "clutch").

    Returns:
        Normalized dict with all standard fields, or None if name is empty.
    """
    normalizer = _NORMALIZERS.get(source)
    if not normalizer:
        log.warning(f"No normalizer for source '{source}', passing through with defaults")
        result = dict(raw)
        result["source"] = source
        return _apply_defaults(result)

    try:
        result = normalizer(raw)
    except Exception as e:
        log.warning(f"Normalization failed for {source}: {e}")
        return None

    # Apply defaults for any missing fields
    result = _apply_defaults(result)

    # Skip results without a name
    name = result.get("name", "").strip()
    if not name or name.lower() in ("unknown", "n/a", "none", "null", "undefined") or len(name) < 2:
        return None

    return result


def normalize_batch(raw_list: list[dict], source: str) -> list[dict]:
    """
    Normalize a batch of raw results from a single source.

    Args:
        raw_list: List of raw result dicts.
        source: Source key.

    Returns:
        List of normalized dicts (invalid results are filtered out).
    """
    results = []
    for raw in raw_list:
        normalized = normalize_result(raw, source)
        if normalized:
            results.append(normalized)

    log.info(f"Normalized {len(raw_list)} raw {source} results → {len(results)} valid")
    return results
