"""
apify_client.py — Wrapper for Apify actor execution.

Focused on:
  - LinkedIn job scraping (QA roles, USA)
  - Email finding via overpowered/email-finder
  - Google search for contact discovery
  - Website scraping for enrichment

Usage:
    from apify_client import get_linkedin_job_postings, find_email
"""

import time
import re
import html
import httpx

from config import APIFY_API_TOKEN, APIFY_BASE_URL
from utils import get_logger, retry

log = get_logger("apify")


class ApifyError(Exception):
    """Raised when Apify API call fails."""
    pass


# ── Known Actor IDs ─────────────────────────────────────────
ACTORS = {
    "linkedin_scraper": "curious_coder/linkedin-jobs-scraper",
    "email_finder": "overpowered/email-finder",
    "google_search": "apify/google-search-scraper",
}

# Default LinkedIn search URLs — QA roles in USA only
LINKEDIN_SEARCH_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=qa%20engineer&location=United%20States&f_TPR=r604800",
    "https://www.linkedin.com/jobs/search/?keywords=software%20test%20engineer&location=United%20States&f_TPR=r604800",
    "https://www.linkedin.com/jobs/search/?keywords=sdet&location=United%20States&f_TPR=r604800",
    "https://www.linkedin.com/jobs/search/?keywords=quality%20assurance%20engineer&location=United%20States&f_TPR=r604800",
    "https://www.linkedin.com/jobs/search/?keywords=test%20automation%20engineer&location=United%20States&f_TPR=r604800",
]


# ── Core Apify Functions ──────────────────────────────────
@retry(max_attempts=3, base_delay=5.0)
def run_actor(actor_id: str, input_data: dict, timeout_secs: int = 300) -> list[dict]:
    """
    Run an Apify actor synchronously and return dataset items.

    Args:
        actor_id: Actor ID (e.g., 'apify/web-scraper').
        input_data: Input payload for the actor.
        timeout_secs: Max time to wait for completion.

    Returns:
        List of result items from the actor's default dataset.
    """
    if not APIFY_API_TOKEN:
        raise ApifyError("APIFY_API_TOKEN is not set in .env")

    headers = {"Authorization": f"Bearer {APIFY_API_TOKEN}"}

    log.info(f"Starting actor: {actor_id}")

    with httpx.Client(timeout=30.0) as client:
        # Start actor run
        # Apify API expects '~' instead of '/' in the actor ID path
        url_safe_actor_id = actor_id.replace("/", "~")
        resp = client.post(
            f"{APIFY_BASE_URL}/acts/{url_safe_actor_id}/runs",
            headers=headers,
            json=input_data,
        )
        if not resp.is_success:
            try:
                error_detail = resp.json()
            except Exception:
                error_detail = resp.text
            log.error(f"Apify actor start failed [{resp.status_code}]: {error_detail}")
        resp.raise_for_status()
        run_data = resp.json()["data"]
        run_id = run_data["id"]

        log.info(f"Actor run started: {run_id}")

        # Poll until finished
        start = time.time()
        while time.time() - start < timeout_secs:
            status_resp = client.get(
                f"{APIFY_BASE_URL}/actor-runs/{run_id}",
                headers=headers,
            )
            status_resp.raise_for_status()
            status = status_resp.json()["data"]["status"]

            if status == "SUCCEEDED":
                break
            elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise ApifyError(f"Actor run {run_id} ended with status: {status}")

            time.sleep(5)
        else:
            raise ApifyError(f"Actor run {run_id} timed out after {timeout_secs}s")

        # Fetch dataset items
        dataset_id = status_resp.json()["data"]["defaultDatasetId"]
        items_resp = client.get(
            f"{APIFY_BASE_URL}/datasets/{dataset_id}/items",
            headers=headers,
            params={"format": "json"},
        )
        items_resp.raise_for_status()

        items = items_resp.json()
        log.info(f"Actor {actor_id} returned {len(items)} items")
        return items


# ── LinkedIn Job Scraper ──────────────────────────────────

def get_linkedin_job_postings(
    search_urls: list[str] | None = None,
    max_results: int = 100,
) -> list[dict]:
    """
    Scrape LinkedIn for QA job postings using curious_coder/linkedin-jobs-scraper.

    Returns normalized list with ALL available data from the LinkedIn API,
    including company enrichment data and job poster contact info.
    This eliminates the need for a separate enrichment stage.
    """
    urls = search_urls or LINKEDIN_SEARCH_URLS

    try:
        raw_items = run_actor(ACTORS["linkedin_scraper"], {
            "urls": urls,
            "scrapeCompany": True,
            "count": max_results,
        })
    except Exception as e:
        log.error(f"Failed to scrape LinkedIn: {e}")
        return []

    results = []
    seen_urls: set[str] = set()

    for item in raw_items:
        # Extract company name — handle dict or string formats
        company_name = ""
        company_field = item.get("company")
        if isinstance(company_field, dict):
            company_name = company_field.get("name", "") or item.get("companyName", "")
        elif isinstance(company_field, str):
            company_name = company_field
        else:
            company_name = item.get("companyName", "")

        company_name = (company_name or "").strip()
        if not company_name or company_name.lower() in ("unknown", "n/a", "none"):
            continue

        # Build job URL — try multiple fields, fall back to constructing from ID
        job_url = (item.get("jobUrl") or item.get("url") or item.get("link") or "").strip()
        if not job_url and item.get("id"):
            job_url = f"https://www.linkedin.com/jobs/view/{item['id']}"

        if not job_url or job_url in seen_urls:
            continue
        seen_urls.add(job_url)

        # ── Extract ALL rich data from LinkedIn response ──

        # Company website (top-level field or nested in company dict)
        company_website = (
            item.get("companyWebsite", "")
            or (company_field.get("companyUrl", "") if isinstance(company_field, dict) else "")
            or (company_field.get("websiteUrl", "") if isinstance(company_field, dict) else "")
            or ""
        ).strip()

        # Company LinkedIn URL
        company_linkedin_url = (
            item.get("companyLinkedinUrl", "")
            or (company_field.get("linkedInUrl", "") if isinstance(company_field, dict) else "")
            or ""
        ).strip()

        # Company description
        company_description = (item.get("companyDescription", "") or "").strip()

        # Employee count
        company_employee_count = 0
        raw_emp = item.get("companyEmployeesCount")
        if isinstance(raw_emp, int):
            company_employee_count = raw_emp
        elif isinstance(raw_emp, str):
            try:
                company_employee_count = int(raw_emp.replace(",", "").strip())
            except (ValueError, AttributeError):
                pass

        # Location & Country
        location = (item.get("location", "") or item.get("jobLocation", "") or "").strip()
        country = ""
        addr = item.get("companyAddress")
        if isinstance(addr, dict):
            country = addr.get("addressCountry", "")
        # Fallback: if location mentions US states/cities, assume USA
        if not country and location:
            us_indicators = (
                "United States", ", CA", ", NY", ", TX", ", WA", ", IL",
                ", MA", ", CO", ", GA", ", FL", ", NC", ", VA",
                "Remote", "Hybrid", "Metropolitan Area",
            )
            if any(ind in location for ind in us_indicators):
                country = "US"

        # Industry
        industry = (item.get("industries", "") or "").strip()

        # Job metadata
        seniority_level = (item.get("seniorityLevel", "") or "").strip()
        employment_type = (item.get("employmentType", "") or "").strip()
        job_function = (item.get("jobFunction", "") or "").strip()

        # Job poster info (direct contact lead!)
        poster_name = (item.get("jobPosterName", "") or "").strip()
        poster_title = (item.get("jobPosterTitle", "") or "").strip()
        poster_profile_url = (item.get("jobPosterProfileUrl", "") or "").strip()

        # Salary & applicants
        salary_info = item.get("salaryInfo", []) or []
        applicants_count = (item.get("applicantsCount", "") or "").strip()

        # Job description — keep full text for research stage
        description_text = (
            item.get("descriptionText", "")
            or item.get("description", "")
            or ""
        ).strip()

        results.append({
            "company": company_name,
            "title": item.get("title") or item.get("jobTitle", ""),
            "url": job_url,
            "description": description_text[:2000],       # Keep more text for research
            "source": "linkedin",
            "signal_type": "JOB_POSTING",
            # Company enrichment data (replaces enrichment.py!)
            "company_website": company_website,
            "company_linkedin_url": company_linkedin_url,
            "company_description": company_description[:1000],
            "company_employee_count": company_employee_count,
            "location": location,
            "country": country,
            "industry": industry,
            # Job poster as a direct contact lead
            "poster_name": poster_name,
            "poster_title": poster_title,
            "poster_profile_url": poster_profile_url,
            # Extra metadata
            "seniority_level": seniority_level,
            "employment_type": employment_type,
            "job_function": job_function,
            "salary_info": salary_info,
            "applicants_count": applicants_count,
        })

    log.info(f"LinkedIn returned {len(results)} QA job postings")
    return results


# ── Email Finder (Apify) ─────────────────────────────────

def find_email(first_name: str, last_name: str, domain: str) -> dict:
    """
    Find a verified email address using the overpowered/email-finder Apify actor.

    Args:
        first_name: Person's first name.
        last_name: Person's last name (surname).
        domain: Company domain (e.g., 'company.com').

    Returns:
        dict with keys:
            - "email": str (found email or "")
            - "verified": bool (True if Apify confirmed deliverable)
            - "source": str ("APIFY_VERIFIED" or "NOT_FOUND")
            - "raw": dict (full actor response)
    """
    if not first_name or not last_name or not domain:
        log.warning(f"find_email: missing inputs — name='{first_name} {last_name}', domain='{domain}'")
        return {"email": "", "verified": False, "source": "NOT_FOUND", "raw": {}}

    try:
        items = run_actor(ACTORS["email_finder"], {
            "name": first_name.strip().lower(),
            "surname": last_name.strip().lower(),
            "domain": domain.strip().lower(),
        }, timeout_secs=120)

        if items and len(items) > 0:
            result = items[0]
            email = result.get("email", "") or result.get("Email", "") or ""
            # Check verification status from actor output
            is_verified = result.get("verified", False) or result.get("deliverable", False) or bool(email)

            if email:
                log.info(f"Email finder found: {email} (verified={is_verified})")
                return {
                    "email": email,
                    "verified": is_verified,
                    "source": "APIFY_VERIFIED" if is_verified else "APIFY_UNVERIFIED",
                    "raw": result,
                }

        log.info(f"Email finder: no email found for {first_name} {last_name} @ {domain}")
        return {"email": "", "verified": False, "source": "NOT_FOUND", "raw": {}}

    except Exception as e:
        log.error(f"Email finder failed for {first_name} {last_name} @ {domain}: {e}")
        return {"email": "", "verified": False, "source": "NOT_FOUND", "raw": {}}


# ── Website Scraper ───────────────────────────────────────

def _clean_html_text(raw_html: str, max_chars: int = 5000) -> str:
    """Strip script, style, navigation tags and return clean plain text."""
    if not raw_html:
        return ""
    
    # Remove script, style, nav, footer, header, svg, noscript
    cleaned = re.sub(r'<(script|style|nav|footer|header|svg|noscript|iframe)[^>]*>.*?</\1>', ' ', raw_html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    # Unescape HTML entities (&amp;, &quot;, etc.)
    cleaned = html.unescape(cleaned)
    # Collapse multiple whitespaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:max_chars]


def scrape_website(url: str) -> str:
    """
    Scrape a single webpage and return its text content.

    Prioritizes fast direct HTTP fetching, falling back to Jina reader API.
    Used by research.py for extracting company info.
    """
    if not url:
        return ""

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Step 1: Direct HTTP GET (fast & free)
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200 and resp.text:
                text = _clean_html_text(resp.text)
                if len(text) >= 100:
                    log.info(f"Direct scrape succeeded for {url} ({len(text)} chars)")
                    return text
    except Exception as e:
        log.debug(f"Direct scrape failed for {url}: {e}")

    # Step 2: Fallback to Jina Reader API (free, renders JavaScript / handles Cloudflare)
    try:
        jina_url = f"https://r.jina.ai/{url}"
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(jina_url, headers={"User-Agent": headers["User-Agent"]})
            if resp.status_code == 200 and resp.text:
                text = re.sub(r'\s+', ' ', resp.text).strip()[:5000]
                if len(text) >= 100:
                    log.info(f"Jina reader scrape succeeded for {url} ({len(text)} chars)")
                    return text
    except Exception as e:
        log.debug(f"Jina reader scrape failed for {url}: {e}")

    return ""


# ── Google Search ─────────────────────────────────────────

def search_google(query: str, max_results: int = 5) -> list[dict]:
    """
    Search Google via Apify.

    Returns: [{"title": str, "url": str, "description": str}, ...]
    """
    try:
        raw_items = run_actor(ACTORS["google_search"], {
            "queries": query,
            "maxPagesPerQuery": 1,
            "resultsPerPage": max_results,
        })
    except Exception as e:
        log.error(f"Google search failed for '{query}': {e}")
        return []

    results = []
    for item in raw_items:
        # Apify google-search-scraper nests results inside 'organicResults'
        organic = item.get("organicResults", [])
        if organic and isinstance(organic, list):
            for org in organic[:max_results]:
                results.append({
                    "title": org.get("title", ""),
                    "url": org.get("url") or org.get("link", ""),
                    "description": org.get("description") or org.get("snippet", ""),
                })
        else:
            # Fallback for flat items schema
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url") or item.get("link", ""),
                "description": item.get("description") or item.get("snippet", ""),
            })

    return results


if __name__ == "__main__":
    print("Apify client loaded. Actor IDs:")
    for name, actor_id in ACTORS.items():
        print(f"  {name}: {actor_id}")
