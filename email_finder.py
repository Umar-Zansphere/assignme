"""
email_finder.py — Custom Email Finder (replaces Apify overpowered/email-finder)

Multi-strategy email discovery using free techniques:
  1. Web scraping (company website + Google search for email patterns)
  2. Pattern generation + SMTP verification (MX + RCPT TO)
  3. Catch-all domain detection
  4. Fallback pattern guessing

Usage:
    from email_finder import find_email

    result = find_email("John", "Doe", "example.com")
    # Returns: {"email": "...", "verified": bool, "source": "...", "raw": {...}}
"""

import re
import socket
import smtplib
import time
import random
import string

import httpx

try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False

from utils import get_logger

log = get_logger("email_finder")

# ── Constants ─────────────────────────────────────────────
SMTP_TIMEOUT = 10
SMTP_DELAY_BETWEEN_CHECKS = 1.0  # seconds between SMTP checks to avoid blacklisting

# Email regex for scraping
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Pages likely to contain contact info
CONTACT_PAGE_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/leadership",
    "/people",
    "/company",
]

# Common email patterns ranked by probability in corporate environments
# Format: (pattern_name, weight) — higher weight = more likely
PATTERN_WEIGHTS = {
    "first.last": 35,       # john.doe@company.com — most common
    "first_last": 8,        # john_doe@company.com
    "flast": 20,            # jdoe@company.com
    "firstl": 10,           # johnd@company.com
    "first": 15,            # john@company.com
    "last.first": 5,        # doe.john@company.com
    "first.l": 5,           # john.d@company.com
    "f.last": 2,            # j.doe@company.com
}


# ── Pattern Generation ────────────────────────────────────

def _generate_patterns(first_name: str, last_name: str, domain: str) -> list[tuple[str, str, int]]:
    """
    Generate common corporate email patterns.

    Returns:
        List of (email, pattern_name, weight) tuples, sorted by weight descending.
    """
    first = first_name.lower().strip()
    last = last_name.lower().strip()
    d = domain.lower().strip()

    if not first or not last or not d:
        return []

    patterns = [
        (f"{first}.{last}@{d}", "first.last", PATTERN_WEIGHTS["first.last"]),
        (f"{first}_{last}@{d}", "first_last", PATTERN_WEIGHTS["first_last"]),
        (f"{first[0]}{last}@{d}", "flast", PATTERN_WEIGHTS["flast"]),
        (f"{first}{last[0]}@{d}", "firstl", PATTERN_WEIGHTS["firstl"]),
        (f"{first}@{d}", "first", PATTERN_WEIGHTS["first"]),
        (f"{last}.{first}@{d}", "last.first", PATTERN_WEIGHTS["last.first"]),
        (f"{first}.{last[0]}@{d}", "first.l", PATTERN_WEIGHTS["first.l"]),
        (f"{first[0]}.{last}@{d}", "f.last", PATTERN_WEIGHTS["f.last"]),
    ]

    # Sort by weight descending (most probable first)
    patterns.sort(key=lambda x: x[2], reverse=True)
    return patterns


# ── SMTP Verification ─────────────────────────────────────

def _get_mx_host(domain: str) -> str | None:
    """Resolve the primary MX host for a domain."""
    if HAS_DNS:
        try:
            mx_records = dns.resolver.resolve(domain, "MX")
            mx_host = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
            return mx_host
        except Exception:
            pass

    # Fallback: try the domain itself
    try:
        socket.getaddrinfo(domain, 25)
        return domain
    except socket.gaierror:
        return None


def _verify_email_smtp(email: str, mx_host: str | None = None) -> bool:
    """
    Verify an email address via SMTP RCPT TO check.

    Returns True if the mail server accepts the recipient.
    Returns False if rejected or connection fails.
    """
    if not email or "@" not in email:
        return False

    domain = email.split("@")[1]

    if not mx_host:
        mx_host = _get_mx_host(domain)

    if not mx_host:
        return False

    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as server:
            server.ehlo("verify.local")
            server.mail("verify@verify.local")
            code, _ = server.rcpt(email)
            return code == 250
    except (smtplib.SMTPException, socket.error, OSError) as e:
        log.debug(f"SMTP verification failed for {email}: {e}")
        return False


def _is_catch_all(domain: str, mx_host: str | None = None) -> bool:
    """
    Detect if a domain is a catch-all (accepts any email address).

    Sends RCPT TO with a random, obviously-fake address.
    If accepted → catch-all domain (verification is unreliable).
    """
    if not mx_host:
        mx_host = _get_mx_host(domain)

    if not mx_host:
        return False

    # Generate a random address that definitely doesn't exist
    random_user = "zxqkj" + "".join(random.choices(string.ascii_lowercase, k=8)) + "test"
    fake_email = f"{random_user}@{domain}"

    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as server:
            server.ehlo("verify.local")
            server.mail("verify@verify.local")
            code, _ = server.rcpt(fake_email)
            if code == 250:
                log.info(f"Domain {domain} is catch-all (accepts any address)")
                return True
            return False
    except (smtplib.SMTPException, socket.error, OSError):
        return False


# ── Web Scraping Strategy ─────────────────────────────────

def _scrape_emails_from_web(
    domain: str, first_name: str, last_name: str
) -> list[str]:
    """
    Scrape company website and Google for email addresses matching the person.

    Strategy:
        1. Fetch company contact/about/team pages and extract emails
        2. Google search for '"firstname lastname" "@domain"' to find published emails

    Returns list of matching email addresses found (may be empty).
    """
    first = first_name.lower().strip()
    last = last_name.lower().strip()
    found_emails: set[str] = set()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # ── Step 1: Scrape company website pages ──
    base_url = f"https://{domain}"

    with httpx.Client(timeout=8.0, follow_redirects=True, verify=False) as client:
        for path in CONTACT_PAGE_PATHS:
            url = f"{base_url}{path}"
            try:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200 and resp.text:
                    page_emails = EMAIL_REGEX.findall(resp.text)
                    for email in page_emails:
                        email_lower = email.lower()
                        email_domain = email_lower.split("@")[1] if "@" in email_lower else ""
                        # Only keep emails from the target domain
                        if email_domain == domain.lower():
                            found_emails.add(email_lower)
            except Exception:
                continue

    # ── Step 2: Google search for published emails ──
    # Use a simple Google scrape (no Apify needed) via the search URL
    search_queries = [
        f'"{first_name} {last_name}" "@{domain}" email',
        f'"{first_name} {last_name}" "{domain}" contact',
    ]

    with httpx.Client(timeout=8.0, follow_redirects=True, verify=False) as client:
        for query in search_queries:
            try:
                resp = client.get(
                    "https://www.google.com/search",
                    params={"q": query, "num": 5},
                    headers=headers,
                )
                if resp.status_code == 200 and resp.text:
                    page_emails = EMAIL_REGEX.findall(resp.text)
                    for email in page_emails:
                        email_lower = email.lower()
                        email_domain = email_lower.split("@")[1] if "@" in email_lower else ""
                        if email_domain == domain.lower():
                            found_emails.add(email_lower)
            except Exception:
                continue

    # ── Step 3: Try Jina Reader for JS-rendered pages ──
    try:
        jina_url = f"https://r.jina.ai/{base_url}/contact"
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(jina_url, headers={"User-Agent": headers["User-Agent"]})
            if resp.status_code == 200 and resp.text:
                page_emails = EMAIL_REGEX.findall(resp.text)
                for email in page_emails:
                    email_lower = email.lower()
                    email_domain = email_lower.split("@")[1] if "@" in email_lower else ""
                    if email_domain == domain.lower():
                        found_emails.add(email_lower)
    except Exception:
        pass

    # Filter: prioritize emails that match the person's name
    person_emails = []
    other_emails = []

    for email in found_emails:
        local_part = email.split("@")[0]
        if first in local_part or last in local_part:
            person_emails.append(email)
        else:
            other_emails.append(email)

    # Person-matching emails first, then others
    return person_emails + other_emails


# ── Main Entry Point ──────────────────────────────────────

def find_email(first_name: str, last_name: str, domain: str) -> dict:
    """
    Find a verified email address using multiple free strategies.

    Replaces the Apify overpowered/email-finder actor with a self-contained
    multi-strategy approach.

    Args:
        first_name: Person's first name.
        last_name: Person's last name (surname).
        domain: Company domain (e.g., 'company.com').

    Returns:
        dict with keys:
            - "email": str (found email or "")
            - "verified": bool (True if SMTP-verified or scraped from web)
            - "source": str ("SMTP_VERIFIED", "WEB_SCRAPED", "CATCH_ALL_PATTERN",
                             "PATTERN_UNVERIFIED", "NOT_FOUND")
            - "raw": dict (details about the discovery method)
    """
    if not first_name or not last_name or not domain:
        log.warning(
            f"find_email: missing inputs — "
            f"name='{first_name} {last_name}', domain='{domain}'"
        )
        return {"email": "", "verified": False, "source": "NOT_FOUND", "raw": {}}

    first_name = first_name.strip()
    last_name = last_name.strip()
    domain = domain.strip().lower()

    log.info(f"Finding email for {first_name} {last_name} @ {domain}")

    # ── Strategy 1: Web Scraping ──────────────────────────
    log.info("  Strategy 1: Web scraping...")
    try:
        scraped_emails = _scrape_emails_from_web(domain, first_name, last_name)
        if scraped_emails:
            email = scraped_emails[0]
            log.info(f"  [WEB] Found email on web: {email}")
            return {
                "email": email,
                "verified": True,
                "source": "WEB_SCRAPED",
                "raw": {
                    "method": "web_scraping",
                    "all_found": scraped_emails[:5],
                },
            }
    except Exception as e:
        log.debug(f"  Web scraping failed: {e}")

    # ── Strategy 2: Pattern Generation + SMTP Verification ──
    log.info("  Strategy 2: Pattern generation + SMTP verification...")
    patterns = _generate_patterns(first_name, last_name, domain)
    mx_host = _get_mx_host(domain)

    if mx_host:
        # First check if domain is catch-all
        is_catch_all = _is_catch_all(domain, mx_host)

        if not is_catch_all:
            # SMTP verification is meaningful — try each pattern
            for email, pattern_name, weight in patterns:
                try:
                    time.sleep(SMTP_DELAY_BETWEEN_CHECKS)
                    if _verify_email_smtp(email, mx_host):
                        log.info(
                            f"  [SMTP] Verified: {email} (pattern={pattern_name})"
                        )
                        return {
                            "email": email,
                            "verified": True,
                            "source": "SMTP_VERIFIED",
                            "raw": {
                                "method": "smtp_verification",
                                "pattern": pattern_name,
                                "weight": weight,
                                "mx_host": mx_host,
                            },
                        }
                except Exception as e:
                    log.debug(f"  SMTP check failed for {email}: {e}")
                    continue
        else:
            # ── Strategy 3: Catch-All Domain ──────────────
            # Domain accepts everything — use the highest-weight pattern
            log.info("  Strategy 3: Catch-all domain detected, using best pattern...")
            if patterns:
                best_email, best_pattern, best_weight = patterns[0]
                log.info(
                    f"  [CATCH-ALL] Using best pattern: {best_email} "
                    f"(pattern={best_pattern})"
                )
                return {
                    "email": best_email,
                    "verified": False,
                    "source": "CATCH_ALL_PATTERN",
                    "raw": {
                        "method": "catch_all_best_pattern",
                        "pattern": best_pattern,
                        "weight": best_weight,
                        "mx_host": mx_host,
                        "catch_all": True,
                    },
                }
    else:
        log.debug(f"  No MX record found for {domain}")

    # ── Strategy 4: Fallback — Best Pattern Unverified ────
    log.info("  Strategy 4: Fallback to best unverified pattern...")
    if patterns:
        best_email, best_pattern, best_weight = patterns[0]
        log.info(f"  [GUESS] Best pattern: {best_email} (pattern={best_pattern})")
        return {
            "email": best_email,
            "verified": False,
            "source": "PATTERN_UNVERIFIED",
            "raw": {
                "method": "pattern_fallback",
                "pattern": best_pattern,
                "weight": best_weight,
                "mx_available": mx_host is not None,
            },
        }

    log.info(f"  No email found for {first_name} {last_name} @ {domain}")
    return {"email": "", "verified": False, "source": "NOT_FOUND", "raw": {}}


if __name__ == "__main__":
    # Quick test
    import sys

    if len(sys.argv) >= 4:
        first, last, dom = sys.argv[1], sys.argv[2], sys.argv[3]
    else:
        first, last, dom = "test", "user", "example.com"

    print(f"Finding email for: {first} {last} @ {dom}")
    result = find_email(first, last, dom)
    print(f"Result: {result}")
