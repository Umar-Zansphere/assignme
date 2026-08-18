"""
search_client.py — Web search via self-hosted SearXNG instance.

Replaces the Apify google-search-scraper with a free, self-hosted SearXNG
metasearch engine that aggregates results from Google, Bing, DuckDuckGo, etc.

Usage:
    from search_client import search_web

    results = search_web('"Acme Corp" "CTO" site:linkedin.com/in', max_results=5)
    # Returns: [{"title": str, "url": str, "description": str}, ...]
"""

import httpx

from config import SEARXNG_URL
from utils import get_logger, retry

log = get_logger("search")


class SearchError(Exception):
    """Raised when search API call fails."""
    pass


@retry(max_attempts=3, base_delay=2.0)
def search_web(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web via SearXNG JSON API.

    Args:
        query: Search query string (supports operators like site:, "quotes", etc.)
        max_results: Maximum number of results to return.

    Returns:
        List of dicts with keys: title, url, description
    """
    if not query or not query.strip():
        return []

    if not SEARXNG_URL:
        raise SearchError("SEARXNG_URL is not configured in .env")

    log.info(f"Searching: {query[:80]}...")

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                    "language": "en",
                    "pageno": 1,
                },
            )
            response.raise_for_status()
    except httpx.ConnectError as e:
        raise SearchError(f"Cannot connect to SearXNG at {SEARXNG_URL}: {e}")
    except httpx.HTTPStatusError as e:
        raise SearchError(f"SearXNG returned {e.response.status_code}: {e.response.text[:200]}")

    data = response.json()
    raw_results = data.get("results", [])

    results = []
    seen_urls: set[str] = set()

    for item in raw_results[:max_results]:
        url = (item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        results.append({
            "title": (item.get("title") or "").strip(),
            "url": url,
            "description": (item.get("content") or "").strip(),
        })

    log.info(f"Search returned {len(results)} results")
    return results


# ── Convenience aliases (drop-in replacements) ────────────

def search_google(query: str, max_results: int = 5) -> list[dict]:
    """
    Drop-in replacement for the old Apify-based search_google().

    Same signature and return format.
    """
    return search_web(query, max_results=max_results)


if __name__ == "__main__":
    # Quick test
    results = search_web("SearXNG test query", max_results=3)
    print(f"Found {len(results)} results:")
    for r in results:
        print(f"  - {r['title']}: {r['url']}")
        print(f"    {r['description'][:100]}")
