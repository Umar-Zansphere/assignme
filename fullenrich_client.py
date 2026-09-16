"""
fullenrich_client.py — FullEnrich API client for email finding & enrichment.

Wraps FullEnrich's REST API (v2) to:
  1. Find a verified work email for a specific person at a company
  2. Batch-enrich up to 100 contacts at once

FullEnrich uses an async model:
  - POST /contact/enrich/bulk → starts enrichment, returns enrichment_id
  - GET  /contact/enrich/bulk/{id} → poll until status=FINISHED

API Docs: https://docs.fullenrich.com
Auth: Bearer token in Authorization header
Credits: 1 credit per work email found (only charged on success)

Usage:
    from fullenrich_client import find_email, enrich_contacts_bulk

    result = find_email("John", "Doe", "intercom.com")
    # → {"email": "john.doe@intercom.com", "status": "DELIVERABLE", ...}

    results = enrich_contacts_bulk([
        {"first_name": "John", "last_name": "Doe", "domain": "intercom.com"},
    ])
"""

import time
import httpx

from config import FULLENRICH_API_KEY, FULLENRICH_BASE_URL
from utils import get_logger

log = get_logger("fullenrich_client")

# ── Constants ─────────────────────────────────────────────
_TIMEOUT = 30          # seconds per HTTP request
_MAX_RETRIES = 3       # retries per HTTP request
_BASE_DELAY = 2.0      # seconds, for exponential backoff

# Polling config for async enrichment
_POLL_INTERVAL = 5.0   # seconds between polls
_POLL_MAX_WAIT = 120   # max seconds to wait for enrichment result
_POLL_BACKOFF = 1.5    # multiply interval each poll


def _headers() -> dict:
    """Build request headers with Bearer auth."""
    if not FULLENRICH_API_KEY:
        raise ValueError(
            "FULLENRICH_API_KEY is not set. Add it to your .env file. "
            "Get one at https://app.fullenrich.com/app/api"
        )
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FULLENRICH_API_KEY}",
    }


def _request(method: str, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
    """
    Make an HTTP request to FullEnrich API with retry + backoff.

    Returns the JSON response dict.
    Raises on persistent failure.
    """
    url = f"{FULLENRICH_BASE_URL}/{endpoint.lstrip('/')}"
    last_error = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                if method.upper() == "GET":
                    resp = client.get(url, headers=_headers(), params=params)
                else:
                    resp = client.post(url, json=payload, headers=_headers(), params=params)

            if resp.status_code == 429:
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    f"FullEnrich rate limited (429). Retrying in {delay:.0f}s "
                    f"(attempt {attempt}/{_MAX_RETRIES})"
                )
                time.sleep(delay)
                continue

            if resp.status_code == 401:
                log.error("FullEnrich API key is invalid (401 Unauthorized)")
                return {"error": True, "error_code": "AUTH_FAILED"}

            if resp.status_code >= 500:
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    f"FullEnrich server error ({resp.status_code}). "
                    f"Retrying in {delay:.0f}s (attempt {attempt}/{_MAX_RETRIES})"
                )
                time.sleep(delay)
                continue

            data = resp.json()

            # Handle FullEnrich-specific error responses (400 with code/message)
            if resp.status_code == 400:
                error_code = data.get("code", "BAD_REQUEST")
                error_msg = data.get("message", "Bad request")

                # "Enrichment not ready" is not a real error — it's a polling signal
                if error_code == "error.enrichment.in_progress":
                    return {"error": True, "error_code": "IN_PROGRESS", "message": error_msg}

                log.warning(f"FullEnrich bad request: {error_code} — {error_msg}")
                return {"error": True, "error_code": error_code, "message": error_msg}

            if resp.status_code == 402:
                log.error("FullEnrich insufficient credits (402)")
                return {"error": True, "error_code": "CREDITS_INSUFFICIENT"}

            if resp.status_code == 404:
                log.warning("FullEnrich enrichment not found (404)")
                return {"error": True, "error_code": "NOT_FOUND"}

            return data

        except httpx.TimeoutException:
            last_error = "Request timed out"
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                f"FullEnrich request timed out. Retrying in {delay:.0f}s "
                f"(attempt {attempt}/{_MAX_RETRIES})"
            )
            time.sleep(delay)

        except Exception as e:
            last_error = str(e)
            log.error(f"FullEnrich request failed: {e}")
            if attempt < _MAX_RETRIES:
                time.sleep(_BASE_DELAY)
            continue

    log.error(f"FullEnrich request failed after {_MAX_RETRIES} attempts: {last_error}")
    return {"error": True, "error_code": "REQUEST_FAILED", "message": last_error}


# ── Polling ───────────────────────────────────────────────

def _poll_for_result(enrichment_id: str) -> dict:
    """
    Poll GET /contact/enrich/bulk/{enrichment_id} until FINISHED.

    Uses exponential backoff between polls.

    Returns the full response dict when finished, or an error dict.
    """
    interval = _POLL_INTERVAL
    elapsed = 0.0

    log.info(f"  Polling FullEnrich enrichment {enrichment_id}...")

    while elapsed < _POLL_MAX_WAIT:
        time.sleep(interval)
        elapsed += interval

        data = _request("GET", f"contact/enrich/bulk/{enrichment_id}")

        if data.get("error"):
            error_code = data.get("error_code", "")
            if error_code == "IN_PROGRESS":
                log.debug(f"  Enrichment still in progress ({elapsed:.0f}s elapsed)...")
                interval = min(interval * _POLL_BACKOFF, 30.0)
                continue
            # Real error
            return data

        status = data.get("status", "")
        if status == "FINISHED":
            cost = data.get("cost", {}).get("credits", 0)
            log.info(f"  Enrichment finished (cost: {cost} credits)")
            return data
        elif status in ("CANCELED", "CREDITS_INSUFFICIENT", "RATE_LIMIT", "UNKNOWN"):
            log.warning(f"  Enrichment ended with status: {status}")
            return {"error": True, "error_code": status, "raw": data}
        else:
            # CREATED, IN_PROGRESS — keep polling
            log.debug(f"  Enrichment status: {status} ({elapsed:.0f}s elapsed)...")
            interval = min(interval * _POLL_BACKOFF, 30.0)

    log.warning(f"  Enrichment timed out after {_POLL_MAX_WAIT}s")
    return {"error": True, "error_code": "POLL_TIMEOUT"}


# ── Email Finder (Single Contact) ────────────────────────

def find_email(first_name: str, last_name: str, domain: str, linkedin_url: str = "") -> dict:
    """
    Find a verified work email for a person at a company via FullEnrich.

    Uses the Bulk Enrichment endpoint with a batch of 1.

    Args:
        first_name: Person's first name.
        last_name: Person's last name.
        domain: Company root domain (e.g., "intercom.com").
        linkedin_url: Optional LinkedIn profile URL (improves hit rate by +5-20%).

    Returns:
        dict with keys:
            - "email": str — verified email or ""
            - "status": str — "DELIVERABLE", "HIGH_PROBABILITY", "CATCH_ALL", "not_found", "error"
            - "confidence": int — 0-100
            - "catch_all": bool — True if email is catch-all
            - "source": str — "FULLENRICH_VERIFIED", "FULLENRICH_CATCH_ALL", "NOT_FOUND"
            - "raw": dict — full API response for debugging
            - "enrichment": dict — extracted contact + company data
    """
    if not first_name or not last_name or not domain:
        log.warning(
            f"find_email: missing inputs — "
            f"name='{first_name} {last_name}', domain='{domain}'"
        )
        return _empty_result()

    log.info(f"FullEnrich email lookup: {first_name} {last_name} @ {domain}")

    # Build the contact entry
    contact_data = {
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "domain": domain.strip().lower(),
        "enrich_fields": ["contact.work_emails"],
    }

    # LinkedIn URL significantly improves hit rate
    if linkedin_url:
        contact_data["linkedin_url"] = linkedin_url.strip()

    # Build the enrichment request
    enrichment_name = f"{first_name.strip()} {last_name.strip()} @ {domain.strip()}"
    payload = {
        "name": enrichment_name,
        "data": [contact_data],
    }

    # Step 1: Start enrichment
    start_resp = _request("POST", "contact/enrich/bulk", payload, params={"silentFail": "true"})

    if start_resp.get("error"):
        error_code = start_resp.get("error_code", "UNKNOWN")
        log.info(f"  FullEnrich returned error on start: {error_code}")
        return _empty_result(raw=start_resp)

    enrichment_id = start_resp.get("enrichment_id")
    if not enrichment_id:
        log.warning("  FullEnrich returned no enrichment_id")
        return _empty_result(raw=start_resp)

    log.info(f"  Enrichment started: {enrichment_id}")

    # Step 2: Poll for result
    result_data = _poll_for_result(enrichment_id)

    if result_data.get("error"):
        log.info(f"  FullEnrich enrichment failed: {result_data.get('error_code')}")
        return _empty_result(raw=result_data)

    # Step 3: Parse result
    return _parse_enrichment_result(result_data)


# ── Bulk Enrichment ───────────────────────────────────────

def enrich_contacts_bulk(contacts: list[dict], enrichment_name: str = "API Bulk Enrichment") -> list[dict]:
    """
    Enrich up to 100 contacts in a single batch.

    Args:
        contacts: List of dicts, each with:
            - "first_name": str (required)
            - "last_name": str (required)
            - "domain": str (required, or provide linkedin_url)
            - "linkedin_url": str (optional but recommended)
            - "company_name": str (optional)
            - "custom": dict (optional, for pass-through data)
        enrichment_name: Readable name for this batch (visible in dashboard).

    Returns:
        List of result dicts, one per contact, in the same order as input.
        Each dict has the same shape as find_email() return value.
    """
    if not contacts:
        return []

    if len(contacts) > 100:
        log.warning(f"FullEnrich batch size {len(contacts)} exceeds 100, truncating")
        contacts = contacts[:100]

    # Build enrichment data
    data_entries = []
    for c in contacts:
        entry = {
            "first_name": c.get("first_name", "").strip(),
            "last_name": c.get("last_name", "").strip(),
            "enrich_fields": ["contact.work_emails"],
        }
        if c.get("domain"):
            entry["domain"] = c["domain"].strip().lower()
        if c.get("company_name"):
            entry["company_name"] = c["company_name"].strip()
        if c.get("linkedin_url"):
            entry["linkedin_url"] = c["linkedin_url"].strip()
        if c.get("custom"):
            entry["custom"] = c["custom"]
        data_entries.append(entry)

    payload = {
        "name": enrichment_name,
        "data": data_entries,
    }

    log.info(f"FullEnrich bulk enrichment: {len(contacts)} contacts")

    # Start
    start_resp = _request("POST", "contact/enrich/bulk", payload, params={"silentFail": "true"})

    if start_resp.get("error"):
        log.error(f"  FullEnrich bulk start failed: {start_resp.get('error_code')}")
        return [_empty_result(raw=start_resp) for _ in contacts]

    enrichment_id = start_resp.get("enrichment_id")
    if not enrichment_id:
        return [_empty_result(raw=start_resp) for _ in contacts]

    log.info(f"  Bulk enrichment started: {enrichment_id}")

    # Poll
    result_data = _poll_for_result(enrichment_id)

    if result_data.get("error"):
        return [_empty_result(raw=result_data) for _ in contacts]

    # Parse each contact result
    results = []
    data_list = result_data.get("data", [])

    for i, entry in enumerate(data_list):
        parsed = _parse_single_contact(entry)
        results.append(parsed)

    # Pad if fewer results than inputs (shouldn't happen with silentFail=true)
    while len(results) < len(contacts):
        results.append(_empty_result())

    log.info(f"  Bulk enrichment complete: {sum(1 for r in results if r['email'])} emails found out of {len(contacts)}")
    return results


# ── Response Parsing ──────────────────────────────────────

def _parse_enrichment_result(data: dict) -> dict:
    """Parse a single-contact enrichment result (data has one entry in data[])."""
    entries = data.get("data", [])
    if not entries:
        log.info("  FullEnrich returned no data entries")
        return _empty_result(raw=data)

    return _parse_single_contact(entries[0], full_response=data)


def _parse_single_contact(entry: dict, full_response: dict | None = None) -> dict:
    """
    Parse one enriched contact record from FullEnrich response.

    Response structure per contact:
        entry.contact_info.most_probable_work_email.email / .status
        entry.contact_info.work_emails[].email / .status
        entry.profile.full_name, headline, employment, etc.
    """
    contact_info = entry.get("contact_info") or {}
    profile = entry.get("profile") or {}
    custom = entry.get("custom") or {}

    # ── Extract email ──
    best_email_obj = contact_info.get("most_probable_work_email") or {}
    email = (best_email_obj.get("email") or "").strip().lower()
    email_status = (best_email_obj.get("status") or "").upper()

    # Fallback: check work_emails array
    if not email:
        work_emails = contact_info.get("work_emails") or []
        for we in work_emails:
            candidate = (we.get("email") or "").strip().lower()
            if candidate:
                email = candidate
                email_status = (we.get("status") or "").upper()
                break

    if not email:
        log.info(f"  FullEnrich found no email for this contact")
        return _empty_result(raw=full_response or entry)

    # ── Determine verification status ──
    # FullEnrich statuses: DELIVERABLE, HIGH_PROBABILITY, CATCH_ALL, INVALID, INVALID_DOMAIN
    is_catch_all = email_status == "CATCH_ALL"
    is_verified = email_status in ("DELIVERABLE", "HIGH_PROBABILITY")

    if is_catch_all:
        source = "FULLENRICH_CATCH_ALL"
        log.info(f"  [CATCH-ALL] FullEnrich found: {email} (status={email_status})")
    else:
        source = "FULLENRICH_VERIFIED"
        log.info(f"  [VERIFIED] FullEnrich found: {email} (status={email_status})")

    # Map confidence
    confidence_map = {
        "DELIVERABLE": 100,
        "HIGH_PROBABILITY": 85,
        "CATCH_ALL": 50,
        "INVALID": 0,
        "INVALID_DOMAIN": 0,
    }
    confidence = confidence_map.get(email_status, 70)

    return {
        "email":      email,
        "status":     email_status or "DELIVERABLE",
        "confidence": confidence,
        "catch_all":  is_catch_all,
        "source":     source,
        "raw":        full_response or entry,
        "custom":     custom,
        # ── Rich enrichment data ──
        "enrichment": _extract_enrichment(entry),
    }


def _extract_enrichment(entry: dict) -> dict:
    """
    Extract all valuable data from a FullEnrich enrichment result.

    Returns a flat dict with contact-level and company-level fields
    matching the same structure as prospeo_client._extract_enrichment().
    """
    profile = entry.get("profile") or {}
    contact_info = entry.get("contact_info") or {}

    # ── Contact-level ──
    location = profile.get("location") or {}
    social = profile.get("social_profiles", {}).get("professional_network") or {}
    employment = profile.get("employment") or {}
    current_job = employment.get("current") or {}

    contact = {
        "headline":          (profile.get("headline") or "").strip(),
        "description":       (profile.get("description") or "").strip(),
        "timezone":          "",  # FullEnrich doesn't provide timezone directly
        "city":              location.get("city") or "",
        "region":            location.get("region") or "",
        "country":           location.get("country") or "",
        "country_code":      location.get("country_code") or "",
        "fullenrich_id":     profile.get("id") or "",
        "current_job_title": (current_job.get("title") or "").strip(),
        "seniority":         (current_job.get("seniority") or "").strip(),
        "linkedin_url":      social.get("url") or "",
        "linkedin_handle":   social.get("handle") or "",
        "linkedin_id":       social.get("id") or "",
        "connection_count":  social.get("connection_count") or 0,
        # Rich data Prospeo doesn't have
        "skills":            profile.get("skills") or [],
        "languages":         profile.get("languages") or [],
        "educations":        profile.get("educations") or [],
        "employment_history": _extract_employment_history(employment),
        "job_functions":     current_job.get("job_functions") or [],
    }

    # ── Phone data (if enriched) ──
    phones = []
    most_probable_phone = contact_info.get("most_probable_phone")
    if most_probable_phone:
        phones.append(most_probable_phone)
    for p in (contact_info.get("phones") or []):
        if p not in phones:
            phones.append(p)
    contact["phones"] = phones

    # ── Personal emails (if enriched) ──
    personal_emails = []
    most_probable_personal = contact_info.get("most_probable_personal_email")
    if most_probable_personal and most_probable_personal.get("email"):
        personal_emails.append(most_probable_personal)
    for pe in (contact_info.get("personal_emails") or []):
        if pe not in personal_emails:
            personal_emails.append(pe)
    contact["personal_emails"] = personal_emails

    # ── Company-level (from current employment) ──
    company_data = _extract_company_from_employment(current_job)

    return {"contact": contact, "company": company_data}


def _extract_employment_history(employment: dict) -> list[dict]:
    """Extract simplified employment history."""
    history = []
    for job in (employment.get("all") or []):
        company = job.get("company") or {}
        history.append({
            "title":      job.get("title") or "",
            "seniority":  job.get("seniority") or "",
            "company":    company.get("name") or "",
            "domain":     company.get("domain") or "",
            "is_current": job.get("is_current", False),
            "start_at":   job.get("start_at") or "",
            "end_at":     job.get("end_at") or "",
        })
    return history


def _extract_company_from_employment(current_job: dict) -> dict:
    """Extract company data from the current employment entry."""
    company = current_job.get("company") or {}
    locations = company.get("locations") or {}
    hq = locations.get("headquarters") or {}
    industry = company.get("industry") or {}
    social = company.get("social_profiles", {}).get("professional_network") or {}

    return {
        "name":             company.get("name") or "",
        "domain":           company.get("domain") or "",
        "website":          company.get("website") or "",
        "description_ai":   (company.get("description") or "").strip(),
        "industry":         industry.get("main_industry") or "",
        "employee_count":   company.get("headcount") or 0,
        "headcount_range":  company.get("headcount_range") or "",
        "year_founded":     company.get("year_founded") or 0,
        "company_type":     company.get("company_type") or "",
        "specialties":      company.get("specialties") or [],
        "technologies":     [t.get("name", "") for t in (company.get("technologies") or [])],
        "country":          hq.get("country") or "",
        "city":             hq.get("city") or "",
        "region":           hq.get("region") or "",
        "country_code":     hq.get("country_code") or "",
        "linkedin_url":     social.get("url") or "",
        "logo_url":         company.get("logo_url") or "",
        # These fields are Prospeo-specific — leave empty for FullEnrich
        "tech_stack":       [t.get("name", "") for t in (company.get("technologies") or [])],
        "active_job_titles": [],
        "active_job_count": 0,
        "keywords":         company.get("specialties") or [],
        "funding_stage":    "",
        "revenue_range":    "",
        "is_b2b":           0,
    }


def _empty_result(raw: dict | None = None) -> dict:
    """Return a standardized empty/not-found result."""
    return {
        "email":      "",
        "status":     "not_found",
        "confidence": 0,
        "catch_all":  False,
        "source":     "NOT_FOUND",
        "raw":        raw or {},
    }


if __name__ == "__main__":
    # Quick test — uses FullEnrich's hardcoded free test contact
    import sys
    import json

    if len(sys.argv) >= 4:
        first, last, dom = sys.argv[1], sys.argv[2], sys.argv[3]
        linkedin = sys.argv[4] if len(sys.argv) > 4 else ""
    else:
        # Free test contact (0 credits)
        first, last, dom = "Grégoire", "Démogé", "fullenrich.com"
        linkedin = "https://www.linkedin.com/in/demoge/"
        print("Using FullEnrich free test contact (0 credits):\n")

    print(f"Finding email for: {first} {last} @ {dom}")
    if linkedin:
        print(f"LinkedIn: {linkedin}")

    result = find_email(first, last, dom, linkedin_url=linkedin)

    print(f"\n{'='*60}")
    print(f"Email:      {result['email']}")
    print(f"Status:     {result['status']}")
    print(f"Confidence: {result['confidence']}")
    print(f"Source:     {result['source']}")
    print(f"Catch-all:  {result['catch_all']}")

    if result.get("enrichment"):
        enrichment = result["enrichment"]
        contact = enrichment.get("contact", {})
        company = enrichment.get("company", {})

        print(f"\n── Contact Enrichment ──")
        print(f"  Title:    {contact.get('current_job_title', '')}")
        print(f"  Headline: {contact.get('headline', '')}")
        print(f"  City:     {contact.get('city', '')}")
        print(f"  Country:  {contact.get('country', '')}")
        print(f"  LinkedIn: {contact.get('linkedin_url', '')}")
        if contact.get("skills"):
            print(f"  Skills:   {', '.join(contact['skills'][:5])}")

        print(f"\n── Company Enrichment ──")
        print(f"  Name:      {company.get('name', '')}")
        print(f"  Industry:  {company.get('industry', '')}")
        print(f"  Headcount: {company.get('employee_count', 0)}")
        print(f"  Country:   {company.get('country', '')}")

    print(f"\n── Raw Response ──")
    print(json.dumps(result.get("raw", {}), indent=2, default=str)[:2000])
