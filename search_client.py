"""
search_client.py — Multi-provider web search with automatic fallback.

Providers (tried in order until one succeeds):
  1. SearXNG   — self-hosted metasearch engine (free, may be rate-limited)
  2. Serper     — serper.dev API ($0.001/query, very reliable)
  3. Apify      — apify.com Google Search scraper (pay-per-use, reliable)

Configuration in .env:
  SEARCH_PROVIDERS=searxng,serper,apify   # order defines priority
  SEARXNG_URL=http://localhost:8080
  SERPER_API_KEY=<your_key>
  APIFY_API_TOKEN=<your_token>            # already used for LinkedIn

Usage:
    from search_client import search_web
    results = search_web('"Stripe" "CTO" linkedin', max_results=10)
    # -> [{"title": str, "url": str, "description": str}, ...]
"""

import httpx

from config import (
    SEARXNG_URL,
    SERPER_API_KEY,
    APIFY_API_TOKEN,
    APIFY_BASE_URL,
    SEARCH_PROVIDERS,
)
from utils import get_logger

log = get_logger("search")


class SearchError(Exception):
    """Raised when a single provider fails (triggers fallback)."""
    pass


# -- Provider implementations ---------------------------------------

def _search_searxng(query: str, max_results: int) -> list[dict]:
    """Search via self-hosted SearXNG JSON API."""
    if not SEARXNG_URL:
        raise SearchError("SEARXNG_URL not configured")
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "categories": "general",
                        "language": "en", "pageno": 1},
            )
            response.raise_for_status()
    except httpx.ConnectError as e:
        raise SearchError(f"SearXNG unreachable at {SEARXNG_URL}: {e}")
    except httpx.HTTPStatusError as e:
        raise SearchError(f"SearXNG HTTP {e.response.status_code}")
    except Exception as e:
        raise SearchError(f"SearXNG error: {e}")

    data = response.json()
    raw = data.get("results", [])
    if not raw:
        unresponsive = data.get("unresponsive_engines", [])
        names = [e[0] if isinstance(e, list) else str(e) for e in unresponsive]
        raise SearchError(f"SearXNG 0 results (blocked engines: {', '.join(names) or 'none'})")

    return _normalize(raw, max_results, url_key="url", desc_key="content")


def _search_serper(query: str, max_results: int) -> list[dict]:
    """Search via Serper.dev Google Search API ($0.001/query)."""
    if not SERPER_API_KEY:
        raise SearchError("SERPER_API_KEY not configured")
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": max_results, "gl": "us", "hl": "en"},
            )
            response.raise_for_status()
    except httpx.ConnectError as e:
        raise SearchError(f"Serper unreachable: {e}")
    except httpx.HTTPStatusError as e:
        raise SearchError(f"Serper HTTP {e.response.status_code}: {e.response.text[:100]}")
    except Exception as e:
        raise SearchError(f"Serper error: {e}")

    raw = response.json().get("organic", [])
    if not raw:
        raise SearchError("Serper returned 0 organic results")
    return _normalize(raw, max_results, url_key="link", desc_key="snippet")


def _search_apify(query: str, max_results: int) -> list[dict]:
    """Search via Apify Google Search Scraper (pay-per-use fallback)."""
    if not APIFY_API_TOKEN:
        raise SearchError("APIFY_API_TOKEN not configured")
    run_url = f"{APIFY_BASE_URL}/acts/apify~google-search-scraper/run-sync-get-dataset-items"
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                run_url,
                params={"token": APIFY_API_TOKEN},
                json={"queries": query, "maxPagesPerQuery": 1,
                      "resultsPerPage": max_results, "countryCode": "us", "languageCode": "en"},
            )
            response.raise_for_status()
    except httpx.ConnectError as e:
        raise SearchError(f"Apify unreachable: {e}")
    except httpx.HTTPStatusError as e:
        raise SearchError(f"Apify HTTP {e.response.status_code}: {e.response.text[:100]}")
    except Exception as e:
        raise SearchError(f"Apify error: {e}")

    pages = response.json()
    if not pages:
        raise SearchError("Apify returned no pages")

    results: list[dict] = []
    seen: set[str] = set()
    for page in pages:
        for item in page.get("organicResults", []):
            url = (item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append({
                "title": (item.get("title") or "").strip(),
                "url": url,
                "description": (item.get("description") or "").strip(),
            })
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    if not results:
        raise SearchError("Apify returned pages but no organic results")
    return results


# -- Provider registry ---------------------------------------------

_PROVIDERS: dict = {
    "searxng": _search_searxng,
    "serper":  _search_serper,
    "apify":   _search_apify,
}


def _normalize(raw: list[dict], max_results: int, url_key: str, desc_key: str) -> list[dict]:
    """Normalize any provider's raw results into the common dict format."""
    results: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        url = (item.get(url_key) or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({
            "title": (item.get("title") or "").strip(),
            "url": url,
            "description": (item.get(desc_key) or "").strip(),
        })
        if len(results) >= max_results:
            break
    return results


# -- Public API ----------------------------------------------------

def search_web(query: str, max_results: int = 10) -> list[dict]:
    """
    Search the web with automatic fallback across all configured providers.

    Provider priority is set by SEARCH_PROVIDERS in .env (comma-separated).
    Each provider is tried in order; on any error or 0 results the next
    provider is automatically tried.

    Args:
        query:       Search query (supports boolean operators, quotes)
        max_results: Max results to return

    Returns:
        List of {title, url, description} dicts. Empty list if all fail.
    """
    if not query or not query.strip():
        return []

    providers = [p.strip().lower() for p in SEARCH_PROVIDERS.split(",") if p.strip()]
    if not providers:
        log.error("SEARCH_PROVIDERS is empty — configure at least one provider")
        return []

    log.info(f"Searching: {query[:80]}...")

    errors: list[str] = []
    for name in providers:
        fn = _PROVIDERS.get(name)
        if fn is None:
            log.warning(f"Unknown provider '{name}' in SEARCH_PROVIDERS — skipping")
            continue
        try:
            results = fn(query, max_results)
            log.info(f"Search returned {len(results)} results [{name}]")
            return results
        except SearchError as e:
            log.warning(f"[{name}] {e} — trying next provider")
            errors.append(f"{name}: {e}")
        except Exception as e:
            log.warning(f"[{name}] unexpected error: {e} — trying next provider")
            errors.append(f"{name}: {e}")

    log.error(f"All providers failed: {' | '.join(errors)}")
    return []


# -- Drop-in alias -------------------------------------------------

def search_google(query: str, max_results: int = 10) -> list[dict]:
    """Backwards-compatible alias for search_web()."""
    return search_web(query, max_results=max_results)


# -- CLI test ------------------------------------------------------

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "stripe CTO linkedin"
    print(f"Query: {q}")
    print(f"Providers: {SEARCH_PROVIDERS}\n")
    found = search_web(q, max_results=5)
    print(f"Found {len(found)} results:")
    for r in found:
        print(f"  [{r['title']}]")
        print(f"   {r['url']}")
        print(f"   {r['description'][:120]}")
