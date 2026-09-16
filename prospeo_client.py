"""
prospeo_client.py — Prospeo API client for email finding & verification.

Wraps Prospeo's REST API to:
  1. Find a verified email for a specific person at a company domain
  2. Search a domain for all known email addresses (for pattern learning)

API Docs: https://api.prospeo.io
Auth: X-KEY header with API key

Usage:
    from prospeo_client import find_email, domain_search

    result = find_email("John", "Doe", "intercom.com")
    # → {"email": "john.doe@intercom.com", "status": "verified", ...}

    emails = domain_search("intercom.com")
    # → [{"email": "...", "name": "...", "role": "..."}, ...]
"""

import time
import httpx

from config import PROSPEO_API_KEY, PROSPEO_BASE_URL
from utils import get_logger

log = get_logger("prospeo_client")

# ── Constants ─────────────────────────────────────────────
_TIMEOUT = 30  # seconds
_MAX_RETRIES = 3
_BASE_DELAY = 2.0  # seconds, for exponential backoff


def _headers() -> dict:
    """Build request headers with API key."""
    if not PROSPEO_API_KEY:
        raise ValueError(
            "PROSPEO_API_KEY is not set. Add it to your .env file. "
            "Get one at https://prospeo.io"
        )
    return {
        "Content-Type": "application/json",
        "X-KEY": PROSPEO_API_KEY,
    }


def _post(endpoint: str, payload: dict) -> dict:
    """
    Make a POST request to Prospeo API with retry + backoff.

    Returns the JSON response dict.
    Raises on persistent failure.
    """
    url = f"{PROSPEO_BASE_URL}/{endpoint.lstrip('/')}"
    last_error = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(url, json=payload, headers=_headers())

            if resp.status_code == 429:
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    f"Prospeo rate limited (429). Retrying in {delay:.0f}s "
                    f"(attempt {attempt}/{_MAX_RETRIES})"
                )
                time.sleep(delay)
                continue

            if resp.status_code == 401:
                log.error("Prospeo API key is invalid (401 Unauthorized)")
                return {"error": True, "error_code": "AUTH_FAILED"}

            if resp.status_code >= 500:
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    f"Prospeo server error ({resp.status_code}). "
                    f"Retrying in {delay:.0f}s (attempt {attempt}/{_MAX_RETRIES})"
                )
                time.sleep(delay)
                continue

            data = resp.json()
            return data

        except httpx.TimeoutException:
            last_error = "Request timed out"
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                f"Prospeo request timed out. Retrying in {delay:.0f}s "
                f"(attempt {attempt}/{_MAX_RETRIES})"
            )
            time.sleep(delay)

        except Exception as e:
            last_error = str(e)
            log.error(f"Prospeo request failed: {e}")
            if attempt < _MAX_RETRIES:
                time.sleep(_BASE_DELAY)
            continue

    log.error(f"Prospeo request failed after {_MAX_RETRIES} attempts: {last_error}")
    return {"error": True, "error_code": "REQUEST_FAILED", "message": last_error}


# ── Email Finder (via Enrich Person) ─────────────────────

def find_email(first_name: str, last_name: str, domain: str) -> dict:
    """
    Find a verified email for a person at a company domain via Prospeo.

    Uses the /enrich-person endpoint as documented at:
    https://prospeo.io/api-docs/enrich-person

    Payload: { "data": { "first_name", "last_name", "company_website" } }
    Response email is nested at: person.email.email

    Args:
        first_name: Person's first name.
        last_name: Person's last name.
        domain: Company root domain (e.g., "intercom.com").

    Returns:
        dict with keys:
            - "email": str — verified email or ""
            - "status": str — "VALID", "catch-all", "not_found", "error"
            - "confidence": int — 0-100
            - "catch_all": bool — True if domain is catch-all
            - "source": str — "PROSPEO_VERIFIED", "PROSPEO_CATCH_ALL", "NOT_FOUND"
            - "raw": dict — full API response for debugging
    """
    if not first_name or not last_name or not domain:
        log.warning(
            f"find_email: missing inputs — "
            f"name='{first_name} {last_name}', domain='{domain}'"
        )
        return _empty_result()

    log.info(f"Prospeo email lookup: {first_name} {last_name} @ {domain}")

    payload = {
        "only_verified_email": False,
        "data": {
            "first_name":      first_name.strip(),
            "last_name":       last_name.strip(),
            "company_website": domain.strip().lower(),
        },
    }

    data = _post("enrich-person", payload)

    # Handle error responses
    if data.get("error"):
        error_code = data.get("error_code", str(data.get("error", "UNKNOWN")))
        log.info(f"  Prospeo returned error: {error_code}")
        return _empty_result(raw=data)

    # Response structure: data.person.email.email / data.person.email.status
    person = data.get("person") or {}
    email_obj = person.get("email") or {}

    email = (email_obj.get("email") or "").strip().lower()
    email_status = (email_obj.get("status") or "").upper()  # "VALID", "CATCH_ALL", etc.

    if not email:
        log.info(f"  Prospeo found no email for {first_name} {last_name} @ {domain}")
        return _empty_result(raw=data)

    # Determine verification status
    # Prospeo returns status: "VERIFIED" (good), "CATCH_ALL", "RISKY", "INVALID"
    is_catch_all = email_status != "VERIFIED"

    if is_catch_all:
        source = "PROSPEO_CATCH_ALL"
        log.info(f"  [CATCH-ALL] Prospeo found: {email} (status={email_status})")
    else:
        source = "PROSPEO_VERIFIED"
        log.info(f"  [VERIFIED] Prospeo found: {email} (status={email_status})")

    return {
        "email":      email,
        "status":     email_status or "VERIFIED",
        "confidence": 100,
        "catch_all":  is_catch_all,
        "source":     source,
        "raw":        data,
        # ── Rich enrichment data extracted from the response ──
        "enrichment": _extract_enrichment(data),
    }


def _extract_enrichment(data: dict) -> dict:
    """
    Extract all valuable data from a Prospeo /enrich-person response.

    Returns a flat dict with contact-level and company-level fields
    that can be stored directly against Contact and Company models.
    """
    person  = data.get("person")  or {}
    company = data.get("company") or {}

    # ── Contact-level ──────────────────────────────────────
    location_p = person.get("location") or {}
    contact = {
        "headline":   (person.get("headline") or "").strip(),
        "timezone":   location_p.get("time_zone") or "",       # "America/New_York"
        "city":       location_p.get("city") or "",
        "prospeo_id": person.get("linkedin_member_id") or "",
        # Current job title from Prospeo (may be richer than the LLM-extracted role)
        "current_job_title": (person.get("current_job_title") or "").strip(),
        # LinkedIn URL as confirmed by Prospeo
        "linkedin_url": (person.get("linkedin_url") or "").strip(),
    }

    # ── Company-level ──────────────────────────────────────
    tech      = company.get("technology") or {}
    jobs      = company.get("job_postings") or {}
    funding   = company.get("funding") or {}
    attrs     = company.get("attributes") or {}
    location_c = company.get("location") or {}

    tech_names  = tech.get("technology_names") or []
    job_titles  = jobs.get("active_titles") or []
    keywords    = company.get("keywords") or []

    # Funding stage: prefer Prospeo's stage string; fall back to round name
    funding_stage = (
        funding.get("stage")
        or funding.get("last_round_type")
        or ""
    )
    if isinstance(funding_stage, dict):
        funding_stage = funding_stage.get("name") or ""

    company_data = {
        "description_ai":   (company.get("description_ai") or "").strip(),
        "tech_stack":       tech_names[:30],            # list[str], cap at 30
        "active_job_titles": job_titles[:50],           # list[str], cap at 50
        "active_job_count": jobs.get("active_count") or 0,
        "keywords":         keywords[:20],              # list[str]
        "funding_stage":    funding_stage,
        "revenue_range":    company.get("revenue_range_printed") or "",
        "is_b2b":           1 if attrs.get("is_b2b") else 0,
        # Fresher employee count from Prospeo (LinkedIn is often stale)
        "employee_count":   company.get("employee_count") or 0,
        # Industry from Prospeo (may be more granular than LinkedIn's)
        "industry":         (company.get("industry") or "").strip(),
        # Company location
        "country":          location_c.get("country") or "",
        "city":             location_c.get("city") or "",
    }

    return {"contact": contact, "company": company_data}


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


# ── Domain Search ─────────────────────────────────────────

def domain_search(domain: str) -> list[dict]:
    """
    Search for all known email addresses at a domain.

    Useful for pattern learning — if Prospeo returns several emails
    for a domain, we can extract the common naming pattern.

    Args:
        domain: Company domain (e.g., "acme.com").

    Returns:
        List of dicts, each with:
            - "email": str
            - "first_name": str
            - "last_name": str
            - "role": str
            - "status": str — email verification status
    """
    if not domain:
        return []

    log.info(f"Prospeo domain search: {domain}")

    payload = {"domain": domain.strip().lower()}
    data = _post("domain-search", payload)

    if data.get("error"):
        log.info(f"  Domain search returned error: {data.get('error_code', 'UNKNOWN')}")
        return []

    # Parse results — Prospeo returns a list of contacts
    contacts_raw = data.get("data", data.get("results", data.get("contacts", [])))

    if not isinstance(contacts_raw, list):
        log.debug(f"  Unexpected domain search response format: {type(contacts_raw)}")
        return []

    contacts = []
    for item in contacts_raw:
        if not isinstance(item, dict):
            continue
        email = (item.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue

        contacts.append({
            "email": email,
            "first_name": (item.get("first_name") or "").strip(),
            "last_name": (item.get("last_name") or "").strip(),
            "role": (item.get("title") or item.get("role") or "").strip(),
            "status": (item.get("email_status") or "unknown").lower(),
        })

    log.info(f"  Domain search found {len(contacts)} contacts at {domain}")
    return contacts


if __name__ == "__main__":
    # Quick test
    import sys

    if len(sys.argv) >= 4:
        first, last, dom = sys.argv[1], sys.argv[2], sys.argv[3]
        print(f"Finding email for: {first} {last} @ {dom}")
        result = find_email(first, last, dom)
        print(f"Result: {result}")
    elif len(sys.argv) >= 2:
        dom = sys.argv[1]
        print(f"Domain search: {dom}")
        results = domain_search(dom)
        for r in results:
            print(f"  {r['email']} — {r['first_name']} {r['last_name']} ({r['role']})")
    else:
        print("Usage:")
        print("  python prospeo_client.py <first> <last> <domain>  — find email")
        print("  python prospeo_client.py <domain>                 — domain search")
