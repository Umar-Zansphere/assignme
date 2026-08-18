"""
apify_client.py — Wrapper for Apify actor execution.

Focused on:
  - LinkedIn job scraping (QA roles, USA)

Usage:
    from apify_client import get_linkedin_job_postings
"""

import time
import re
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


if __name__ == "__main__":
    print("Apify client loaded. Actor IDs:")
    for name, actor_id in ACTORS.items():
        print(f"  {name}: {actor_id}")
