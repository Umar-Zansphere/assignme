"""
web_scraper.py — Website scraping via Crawl4AI for LLM context.

Replaces the old raw-httpx + Jina Reader fallback approach with a dedicated
LLM-focused scraper that:
  - Uses Playwright (via crawl4ai) to handle JavaScript-rendered pages
  - Returns clean, structured Markdown instead of raw HTML
  - Handles Cloudflare, bot detection, and redirects automatically
  - Falls back gracefully to a direct HTTP approach if crawl4ai fails

Usage:
    from web_scraper import scrape_website

    text = scrape_website("https://acmecorp.com")
    # Returns clean markdown string, max ~10,000 chars
"""

import asyncio
import sys
import re
import html

import httpx

from utils import get_logger

log = get_logger("web_scraper")

# Max characters fed into the LLM prompt
MAX_CONTENT_CHARS = 10_000


# ── Helpers ──────────────────────────────────────────────────

def _clean_html_text(raw_html: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Strip noisy HTML tags and return plain text. Used as a fallback."""
    if not raw_html:
        return ""
    cleaned = re.sub(
        r'<(script|style|nav|footer|header|svg|noscript|iframe)[^>]*>.*?</\1>',
        ' ', raw_html, flags=re.DOTALL | re.IGNORECASE
    )
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:max_chars]


def _normalize_url(url: str) -> str:
    """Ensure the URL has a scheme."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


# ── Crawl4AI (primary) ────────────────────────────────────────

async def _crawl_with_crawl4ai(url: str) -> str:
    """
    Scrape a URL using crawl4ai's AsyncWebCrawler and return Markdown.

    Crawl4ai automatically:
      - Launches a headless Chromium browser (via Playwright)
      - Executes JavaScript
      - Extracts the main content as clean Markdown
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        browser_cfg = BrowserConfig(
            headless=True,
            verbose=False,
        )

        # Prune boilerplate (nav, ads, footers) and generate tight Markdown
        md_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=0.48,
                threshold_type="fixed",
                min_word_threshold=10,
            )
        )

        run_cfg = CrawlerRunConfig(
            markdown_generator=md_generator,
            page_timeout=20000,        # 20 seconds max per page
            wait_until="domcontentloaded",
            simulate_user=True,        # Mimic real user behaviour
            magic=True,                # Auto-handle consent popups etc.
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)

        if result.success:
            # Prefer the "fit" markdown (pruned) over the raw version
            content = (
                result.markdown.fit_markdown
                or result.markdown.raw_markdown
                or ""
            )
            content = content.strip()
            log.info(f"crawl4ai scraped {url} — {len(content)} chars")
            return content[:MAX_CONTENT_CHARS]

        log.warning(f"crawl4ai failed for {url}: {result.error_message}")
        return ""

    except ImportError:
        log.warning("crawl4ai not installed — run: pip install crawl4ai && crawl4ai-setup")
        return ""
    except Exception as e:
        log.debug(f"crawl4ai error for {url}: {e}")
        return ""


# ── Fallback: Direct HTTP + Jina Reader ──────────────────────

def _scrape_fallback(url: str) -> str:
    """
    Lightweight fallback when crawl4ai is unavailable or fails.

    Strategy:
      1. Direct HTTP GET + HTML clean
      2. Jina Reader API (renders JS, free, no key needed)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Step 1: Direct GET
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200 and resp.text:
                text = _clean_html_text(resp.text)
                if len(text) >= 100:
                    log.info(f"Fallback direct scrape OK for {url} ({len(text)} chars)")
                    return text
    except Exception as e:
        log.debug(f"Direct scrape failed for {url}: {e}")

    # Step 2: Jina Reader
    try:
        jina_url = f"https://r.jina.ai/{url}"
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(jina_url, headers={"User-Agent": headers["User-Agent"]})
            if resp.status_code == 200 and resp.text:
                text = re.sub(r'\s+', ' ', resp.text).strip()[:MAX_CONTENT_CHARS]
                if len(text) >= 100:
                    log.info(f"Jina Reader OK for {url} ({len(text)} chars)")
                    return text
    except Exception as e:
        log.debug(f"Jina Reader failed for {url}: {e}")

    return ""


# ── Public API ────────────────────────────────────────────────

def _run_async_in_thread(coro) -> str:
    """
    Run an async coroutine in a dedicated background thread with its own
    ProactorEventLoop. This is the only reliable way to use Playwright
    (which needs ProactorEventLoop for subprocess support) from synchronous
    code on Windows with Python 3.14+.
    """
    import threading
    result: list[str] = [""]
    error: list[Exception] = []

    def _thread_target():
        # Each thread gets its own event loop — avoids policy conflicts
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result[0] = loop.run_until_complete(coro)
        except Exception as e:
            error.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    t.join(timeout=30)  # 30-second hard timeout per page

    if error:
        raise error[0]
    return result[0]


def scrape_website(url: str) -> str:
    """
    Scrape a webpage and return clean text / Markdown for LLM consumption.

    Tries crawl4ai (Playwright) first for maximum quality, then falls back
    to a direct HTTP + Jina Reader approach.

    Args:
        url: The webpage URL to scrape.

    Returns:
        Clean Markdown or plain text (max ~10,000 chars). Empty string on failure.
    """
    url = _normalize_url(url)
    if not url:
        return ""

    log.info(f"Scraping: {url}")

    # Run crawl4ai in its own thread+loop to avoid Windows asyncio conflicts
    try:
        content = _run_async_in_thread(_crawl_with_crawl4ai(url))
        if content and len(content) >= 100:
            return content
    except Exception as e:
        log.debug(f"crawl4ai runner error for {url}: {e}")

    # Fall back to lightweight approach
    log.info(f"Falling back to HTTP scrape for {url}")
    return _scrape_fallback(url)


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    print(f"Scraping: {target}\n")
    result = scrape_website(target)
    print(result[:2000])
    print(f"\n--- Total: {len(result)} chars ---")
