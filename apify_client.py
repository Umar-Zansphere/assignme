"""
apify_client.py — Wrapper for Apify actor execution.

Usage:
    from apify_client import get_new_job_postings, get_product_launches, get_company_news

    jobs = get_new_job_postings()
    launches = get_product_launches()
    news = get_company_news(["https://techcrunch.com/feed/"])
"""

import time
import httpx

from config import APIFY_API_TOKEN, APIFY_BASE_URL
from utils import get_logger, retry

log = get_logger("apify")


class ApifyError(Exception):
    """Raised when Apify API call fails."""
    pass


# ── Known Actor IDs ─────────────────────────────────────────
# Replace these with actual Apify actor IDs from the Apify Store.
# These are real community actors — verify they exist before first run.
ACTORS = {
    "greenhouse_scraper": "fantastic-jobs/greenhouse-jobs-api",
    "lever_scraper": "bovi/greenhouse-lever-ashby-job-scraper",
    "linkedin_scraper": "curious_coder/linkedin-jobs-scraper", 
    "product_hunt": "maximedupre/product-hunt-scraper",
    "rss_reader": "automation-lab/rss-feed-reader",
    "web_scraper": "apify/web-scraper",
    "google_search": "apify/google-search-scraper",
}

# Default LinkedIn search URLs — edit these to match your ICP
LINKEDIN_SEARCH_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=qa%20engineer&location=United%20States&f_TPR=r604800",
    "https://www.linkedin.com/jobs/search/?keywords=software%20test%20engineer&location=United%20States&f_TPR=r604800",
    "https://www.linkedin.com/jobs/search/?keywords=sdet&location=United%20States&f_TPR=r604800",
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


# ── Normalized Data Fetchers ──────────────────────────────

def get_new_job_postings(
    boards: list[str] | None = None,
) -> list[dict]:
    """
    Scrape job boards for engineering hiring signals.

    Returns normalized list:
        [{"company": str, "title": str, "url": str, "source": str}, ...]
    """
    boards = boards or ["greenhouse", "lever"]
    all_postings = []

    for board in boards:
        actor_id = ACTORS.get(f"{board}_scraper")
        if not actor_id:
            log.warning(f"No actor configured for board: {board}")
            continue

        try:
            # Each actor has different input schemas — adjust per actor
            raw_items = run_actor(actor_id, {
                "maxItems": 100,
            })

            for item in raw_items:
                all_postings.append({
                    "company": item.get("company") or item.get("companyName", "Unknown"),
                    "title": item.get("title") or item.get("jobTitle", ""),
                    "url": item.get("url") or item.get("jobUrl", ""),
                    "source": board,
                    "signal_type": "JOB_POSTING",
                })
        except Exception as e:
            log.error(f"Failed to scrape {board}: {e}")

    return all_postings


def get_linkedin_job_postings(
    search_urls: list[str] | None = None,
    max_results: int = 100,
) -> list[dict]:
    """
    Scrape LinkedIn for job postings using curious_coder/linkedin-jobs-scraper.

    Args:
        search_urls: LinkedIn job search URLs. Defaults to LINKEDIN_SEARCH_URLS.
        max_results: Max jobs to scrape per URL.

    Returns normalized list:
        [{"company": str, "title": str, "url": str, "source": str, "description": str}, ...]
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
    for item in raw_items:
        company_name = (
            item.get("companyName")
            or item.get("company", {}).get("name", "")
            if isinstance(item.get("company"), dict)
            else item.get("company", "")
        ) or "Unknown"

        results.append({
            "company": company_name.strip(),
            "title": item.get("title") or item.get("jobTitle", ""),
            "url": item.get("jobUrl") or item.get("url", ""),
            "description": item.get("description", "")[:500],
            "source": "linkedin",
            "signal_type": "JOB_POSTING",
        })

    log.info(f"LinkedIn returned {len(results)} job postings")
    return results


def get_product_launches() -> list[dict]:
    """
    Scrape Product Hunt for new product launches.

    Returns normalized list:
        [{"company": str, "title": str, "url": str, "source": str}, ...]
    """
    try:
        raw_items = run_actor(ACTORS["product_hunt"], {
            "target": "developer-tools",
            "maxItems": 50,
        })
    except Exception as e:
        log.error(f"Failed to scrape Product Hunt: {e}")
        return []

    results = []
    for item in raw_items:
        results.append({
            "company": item.get("name") or item.get("title", "Unknown"),
            "title": item.get("tagline") or item.get("title", ""),
            "url": item.get("url") or item.get("websiteUrl", ""),
            "description": item.get("description", ""),
            "source": "producthunt",
            "signal_type": "PRODUCT_LAUNCH",
        })

    return results


def get_company_news(rss_urls: list[str] | None = None) -> list[dict]:
    """
    Scrape RSS feeds / news sources.

    Returns normalized list:
        [{"company": str, "title": str, "url": str, "source": str, "description": str}, ...]
    """
    rss_urls = rss_urls or [
        "https://techcrunch.com/feed/",
    ]

    try:
        raw_items = run_actor(ACTORS["rss_reader"], {
            "feeds": rss_urls,
            "maxItemsPerFeed": 50,
        })
    except Exception as e:
        log.error(f"Failed to scrape RSS: {e}")
        return []

    results = []
    for item in raw_items:
        results.append({
            "company": item.get("author") or "Unknown",
            "title": item.get("title", ""),
            "url": item.get("link") or item.get("url", ""),
            "description": item.get("description") or item.get("summary", ""),
            "source": "rss",
            "signal_type": "NEWS",
        })

    return results


def scrape_website(url: str) -> str:
    """
    Scrape a single webpage and return its text content.

    Used by enrichment.py for extracting company info.
    """
    try:
        raw_items = run_actor(ACTORS["web_scraper"], {
            "startUrls": [{"url": url}],
            "maxPagesPerCrawl": 1,
            "pageFunction": """
                async function pageFunction(context) {
                    const { page } = context;
                    const text = await page.evaluate(() => document.body.innerText);
                    return { url: page.url(), text: text.substring(0, 5000) };
                }
            """,
        })
        if raw_items:
            return raw_items[0].get("text", "")
    except Exception as e:
        log.error(f"Failed to scrape {url}: {e}")

    return ""


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
