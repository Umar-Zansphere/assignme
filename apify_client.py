"""
apify_client.py — Wrapper for Apify actor execution.

Usage:
    from apify_client import get_new_job_postings, get_product_launches, get_company_news

    jobs = get_new_job_postings()
    launches = get_product_launches()
    news = get_company_news(["https://techcrunch.com/feed/"])
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
            "target": "daily",
            "maxItems": 30,
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

def _extract_company_from_title(title: str) -> str:
    """Extract likely company name from a news headline."""
    if not title:
        return ""
    
    # Unescape HTML entities (e.g. &#8217;, &amp;)
    cleaned = html.unescape(title).strip()
    
    # Strip common editorial prefixes
    cleaned = re.sub(r'^(Exclusive|Report|Breaking|Analysis|Watch|Video|Podcast|Interview|Review|Opinion):\s*', '', cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'^(Defense tech|Fintech|Biotech|Health tech|Crypto|AI startup|Startup)\s+', '', cleaned, flags=re.IGNORECASE).strip()

    # Skip non-company editorial / promo headlines
    lower = cleaned.lower()
    if any(lower.startswith(p) for p in ("get up to", "your table", "how to", "why ", "what ", "where ", "here's ", "here is ", "vogue just", "hacker pleads", "gen z ")):
        return ""

    # Check for "<Company>'s ..."
    match_possessive = re.match(r"^([A-Z0-9][A-Za-z0-9\.\s&-]+?)['’]s\b", cleaned)
    if match_possessive:
        candidate = match_possessive.group(1).strip()
        if len(candidate) > 1 and candidate.lower() not in ("here", "there", "everyone", "today", "yesterday"):
            return candidate

    # Check for common headline patterns: "<Company> raises...", "<Company> acquires...", etc.
    match = re.match(r'^([A-Z0-9][A-Za-z0-9\.\s&-]+?)(?:\s+(?:raises|acquires|launches|partners|hires|secures|unveils|expands|leads|inks|files|debuts|rolls out|says|plans|builds|to\s+|and\s+|lays off|shuts|closes|buys|brings))\b', cleaned)
    if match:
        candidate = match.group(1).strip()
        if len(candidate) > 1 and candidate.lower() not in ("why", "how", "what", "where", "when", "here", "this", "after", "amid", "as", "new", "get", "chatgpt"):
            return candidate

    # Fallback: take first 1-2 capitalized words if reasonable
    words = cleaned.split()
    cap_words = []
    for w in words[:2]:
        if w and w[0].isupper() and w.lower() not in ("why", "how", "what", "where", "when", "here", "this", "after", "amid", "as", "exclusive", "report", "breaking", "analysis", "watch", "get", "your"):
            cap_words.append(w)
        else:
            break
    if cap_words:
        return " ".join(cap_words)

    return ""


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
        title = item.get("title", "").strip()
        if not title:
            continue

        company_name = _extract_company_from_title(title)
        # Avoid using journalist author as company name
        if not company_name:
            continue

        results.append({
            "company": company_name,
            "title": title,
            "url": item.get("link") or item.get("url", ""),
            "description": item.get("description") or item.get("summary", ""),
            "source": "rss",
            "signal_type": "NEWS",
        })

    return results


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
    Used by enrichment.py for extracting company info.
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
