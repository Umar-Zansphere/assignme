#!/usr/bin/env python3
"""
searxng_scraper.py — public-web verification layer for email_finder.py

Queries a self-hosted SearXNG instance for public mentions of a person and
extracts any email addresses on the target domain from the results. This
gives a "found on the public web, here's the source URL" confidence tier
that's stronger evidence than SMTP pattern-guessing alone.

REQUIREMENTS
    Your SearXNG instance must have JSON output enabled. In settings.yml:

        search:
          formats:
            - html
            - json

    (Disabled by default on public instances to discourage scripted
    scraping — this only works against an instance you control.)

DEBUGGING THIS LAYER IN ISOLATION
    # 1. Confirm SearXNG is reachable and JSON is enabled:
    python3 searxng_scraper.py --url http://localhost:8888 --check

    # 2. Run one raw query and see what comes back:
    python3 searxng_scraper.py --url http://localhost:8888 --query "site:example.com email" --debug

    # 3. Run the full person/domain email search:
    python3 searxng_scraper.py --url http://localhost:8888 --name "Jane Doe" --domain example.com --debug

--debug turns on request/response-level logging (URLs hit, status codes,
timing, raw result counts, every regex match with its source) so you can
see exactly where a query is succeeding or failing without touching
email_finder.py at all.
"""

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import List

import requests

logger = logging.getLogger("email_finder.searxng")

EMAIL_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


@dataclass
class SearchHit:
    query: str
    email: str
    source_url: str
    title: str = ""
    snippet: str = ""


class SearXNGClient:
    def __init__(self, base_url: str, timeout: int = 10, categories: str = "general"):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.categories = categories
        self.session = requests.Session()

    def check_connection(self) -> bool:
        """Health check: confirms the instance is reachable AND returning JSON
        (the most common setup mistake is forgetting to enable json in settings.yml)."""
        try:
            t0 = time.monotonic()
            resp = self.session.get(
                f"{self.base_url}/search",
                params={"q": "test", "format": "json"},
                timeout=self.timeout,
            )
            elapsed = time.monotonic() - t0
            logger.debug(f"GET {self.base_url}/search?q=test&format=json -> {resp.status_code} in {elapsed:.2f}s")

            if resp.status_code != 200:
                logger.error(f"SearXNG returned HTTP {resp.status_code}. Body (first 300 chars): {resp.text[:300]}")
                return False

            try:
                data = resp.json()
            except ValueError:
                logger.error(
                    "Response was not JSON. Most likely cause: 'json' is missing from "
                    "search.formats in your searxng settings.yml. "
                    f"Response started with: {resp.text[:200]!r}"
                )
                return False

            logger.debug(f"OK — JSON keys returned: {list(data.keys())}")
            return True

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Could not connect to {self.base_url} — is the container running/port correct? ({e})")
            return False
        except requests.exceptions.Timeout:
            logger.error(f"Connection to {self.base_url} timed out after {self.timeout}s")
            return False

    def search(self, query: str, max_results: int = 15) -> List[dict]:
        """Run one query, return raw SearXNG result dicts (title/url/content)."""
        params = {"q": query, "format": "json", "categories": self.categories}
        logger.debug(f"Query: {query!r}")
        try:
            t0 = time.monotonic()
            resp = self.session.get(f"{self.base_url}/search", params=params, timeout=self.timeout)
            elapsed = time.monotonic() - t0
            logger.debug(f"  -> HTTP {resp.status_code} in {elapsed:.2f}s, {len(resp.content)} bytes")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for query {query!r}: {e}")
            return []

        if resp.status_code != 200:
            logger.error(f"Non-200 status for query {query!r}: {resp.status_code} — {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.error(f"Non-JSON response for query {query!r}. Check settings.yml 'json' format is enabled.")
            return []

        results = data.get("results", [])
        logger.debug(f"  -> {len(results)} results")
        for r in results[:max_results]:
            logger.debug(f"     - {r.get('url')}  ({(r.get('title') or '')[:60]})")
        return results[:max_results]


def build_queries(full_name: str, domain: str) -> List[str]:
    """A handful of targeted queries most likely to surface a publicly listed email."""
    return [
        f'"{full_name}" "{domain}"',
        f'"{full_name}" email {domain}',
        f'"{full_name}" site:{domain}',
        f'"{full_name}" contact {domain}',
        f'"{full_name}" linkedin {domain}',
    ]


def find_emails_for_person(client: SearXNGClient, full_name: str, domain: str,
                            max_queries: int = 5) -> List[SearchHit]:
    """Run the query set, pull out any @domain emails from title/snippet/url text."""
    domain = domain.lower()
    queries = build_queries(full_name, domain)[:max_queries]
    hits: List[SearchHit] = []

    for q in queries:
        for r in client.search(q):
            blob = " ".join(str(r.get(k, "")) for k in ("title", "content", "url"))
            for email in EMAIL_RE.findall(blob):
                if email.lower().endswith("@" + domain):
                    hit = SearchHit(
                        query=q,
                        email=email.lower(),
                        source_url=r.get("url", ""),
                        title=r.get("title", ""),
                        snippet=(r.get("content") or "")[:200],
                    )
                    logger.debug(f"MATCH  {hit.email}  <-  {hit.source_url}  (query: {q!r})")
                    hits.append(hit)

    logger.info(f"Found {len(hits)} raw email mention(s) for {full_name!r} on {domain}")
    return hits


def dedupe_hits(hits: List[SearchHit]) -> List[SearchHit]:
    seen = set()
    unique = []
    for h in hits:
        key = (h.email, h.source_url)
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


# --------------------------------------------------------------------------
# Standalone CLI — debug this layer without touching email_finder.py
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Debug/test the SearXNG scraping layer in isolation.")
    ap.add_argument("--url", required=True, help="Base URL of your SearXNG instance, e.g. http://localhost:8888")
    ap.add_argument("--check", action="store_true", help="Run a connection/health check and exit")
    ap.add_argument("--query", help="Run one raw query and print results")
    ap.add_argument("--name", help="Full name to search for (pair with --domain)")
    ap.add_argument("--domain", help="Domain to search for (pair with --name)")
    ap.add_argument("--debug", action="store_true", help="Verbose logging of every request/response/match")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    client = SearXNGClient(args.url)

    if args.check:
        ok = client.check_connection()
        print("SearXNG connection: OK" if ok else "SearXNG connection: FAILED (see log above)")
        sys.exit(0 if ok else 1)

    if args.query:
        results = client.search(args.query)
        print(f"\n{len(results)} result(s) for {args.query!r}:\n")
        for r in results:
            print(f"- {r.get('title', '')}\n  {r.get('url', '')}\n")
        return

    if args.name and args.domain:
        hits = dedupe_hits(find_emails_for_person(client, args.name, args.domain))
        print(f"\n{len(hits)} unique email mention(s) found:\n")
        for h in hits:
            print(f"- {h.email}\n  source: {h.source_url}\n  query:  {h.query}\n")
        return

    ap.print_help()


if __name__ == "__main__":
    main()