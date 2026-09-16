"""
email_finder.py — Email Finder with FullEnrich (Primary) + Prospeo (Fallback) + Domain Pattern Learning.

Multi-provider email discovery pipeline:
  1. Cached domain pattern lookup (free — no API call)
  2. FullEnrich API — primary finder (20+ provider waterfall, highest hit rate)
  3. Prospeo API — fallback finder (also provides company enrichment data)
  4. Pattern learning — extracts and caches the naming convention
     from each verified email so future lookups at the same domain
     are faster and cheaper

Key principle: NEVER return unverified / guessed emails.
If neither provider can find it, we return NOT_FOUND instead of guessing.

Usage:
    from email_finder import find_email

    result = find_email("John", "Doe", "example.com")
    # Returns: {"email": "...", "verified": bool, "source": "...", "raw": {...}}
"""

import re

from config import FULLENRICH_API_KEY
from utils import get_logger
from prospeo_client import find_email as prospeo_find_email

log = get_logger("email_finder")

# ── Pattern Detection ─────────────────────────────────────

# Known patterns and how to detect them from (local_part, first, last)
KNOWN_PATTERNS = [
    ("first.last",  lambda f, l: f"{f}.{l}"),
    ("first_last",  lambda f, l: f"{f}_{l}"),
    ("flast",       lambda f, l: f"{f[0]}{l}"),
    ("firstl",      lambda f, l: f"{f}{l[0]}"),
    ("first",       lambda f, l: f),
    ("last.first",  lambda f, l: f"{l}.{f}"),
    ("first.l",     lambda f, l: f"{f}.{l[0]}"),
    ("f.last",      lambda f, l: f"{f[0]}.{l}"),
    ("firstlast",   lambda f, l: f"{f}{l}"),
    ("last",        lambda f, l: l),
]


def detect_pattern(email: str, first_name: str, last_name: str) -> str | None:
    """
    Reverse-engineer the email pattern from a known email + person name.

    Given email "john.doe@acme.com" and name "John Doe":
        local_part = "john.doe"
        Matches pattern "first.last" → returns "first.last"

    Returns the pattern name string, or None if no match.
    """
    if not email or "@" not in email or not first_name or not last_name:
        return None

    local_part = email.split("@")[0].lower()
    first = first_name.lower().strip()
    last = last_name.lower().strip()

    if not first or not last:
        return None

    for pattern_name, generator in KNOWN_PATTERNS:
        try:
            expected = generator(first, last)
            if local_part == expected:
                return pattern_name
        except (IndexError, TypeError):
            continue

    return None


def generate_from_pattern(
    pattern: str, first_name: str, last_name: str, domain: str
) -> str | None:
    """
    Generate an email address from a known pattern.

    Given pattern "first.last", name "Jane Smith", domain "acme.com":
        → "jane.smith@acme.com"

    Returns the generated email, or None if the pattern is unknown.
    """
    first = first_name.lower().strip()
    last = last_name.lower().strip()
    domain = domain.lower().strip()

    if not first or not last or not domain:
        return None

    for pattern_name, generator in KNOWN_PATTERNS:
        if pattern_name == pattern:
            try:
                local_part = generator(first, last)
                return f"{local_part}@{domain}"
            except (IndexError, TypeError):
                return None

    return None


# ── Pattern Cache (DB) ────────────────────────────────────

def _get_cached_pattern(domain: str) -> dict | None:
    """
    Look up the cached email pattern for a domain.

    Returns {"pattern": str, "catch_all": str, "confidence": int} or None.
    """
    from database import get_session
    from models import EmailPattern

    domain = domain.lower().strip()

    try:
        with get_session() as session:
            record = session.query(EmailPattern).filter_by(domain=domain).first()
            if record:
                return {
                    "pattern": record.pattern,
                    "catch_all": record.catch_all,
                    "confidence": record.confidence,
                    "sample_email": record.sample_email,
                }
    except Exception as e:
        log.debug(f"Pattern cache lookup failed for {domain}: {e}")

    return None


def _save_pattern(
    domain: str,
    pattern: str,
    catch_all: str = "UNKNOWN",
    sample_email: str = "",
) -> None:
    """
    Save or update the cached email pattern for a domain.

    If a pattern already exists for this domain, increment the confidence
    counter (more confirmed examples = higher confidence).
    """
    from database import get_session
    from models import EmailPattern
    from utils import utcnow

    domain = domain.lower().strip()

    try:
        with get_session() as session:
            existing = session.query(EmailPattern).filter_by(domain=domain).first()

            if existing:
                # Same pattern → increase confidence
                if existing.pattern == pattern:
                    existing.confidence += 1
                    existing.updated_at = utcnow()
                    if catch_all != "UNKNOWN":
                        existing.catch_all = catch_all
                    log.debug(
                        f"Pattern cache updated for {domain}: "
                        f"{pattern} (confidence={existing.confidence})"
                    )
                else:
                    # Different pattern — new one wins if it seems more recent
                    existing.pattern = pattern
                    existing.confidence = 1
                    existing.catch_all = catch_all
                    existing.sample_email = sample_email
                    existing.updated_at = utcnow()
                    log.info(
                        f"Pattern cache REPLACED for {domain}: "
                        f"was '{existing.pattern}' → now '{pattern}'"
                    )
            else:
                new_pattern = EmailPattern(
                    domain=domain,
                    pattern=pattern,
                    confidence=1,
                    catch_all=catch_all,
                    sample_email=sample_email,
                )
                session.add(new_pattern)
                log.info(
                    f"Pattern cache CREATED for {domain}: "
                    f"{pattern} (sample: {sample_email})"
                )
    except Exception as e:
        log.warning(f"Failed to save pattern cache for {domain}: {e}")


# ── FullEnrich Finder ─────────────────────────────────────

def _try_fullenrich(first_name: str, last_name: str, domain: str, linkedin_url: str = "") -> dict | None:
    """
    Try to find email via FullEnrich (primary provider).

    Returns the FullEnrich result dict if email found, or None.
    Only called if FULLENRICH_API_KEY is configured.
    """
    if not FULLENRICH_API_KEY:
        log.debug("  FullEnrich API key not configured, skipping")
        return None

    try:
        from fullenrich_client import find_email as fullenrich_find_email
        result = fullenrich_find_email(first_name, last_name, domain, linkedin_url=linkedin_url)

        if result.get("email"):
            return result
        else:
            log.info(f"  FullEnrich found no email for {first_name} {last_name} @ {domain}")
            return None

    except Exception as e:
        log.warning(f"  FullEnrich lookup failed: {e}")
        return None


# ── Main Entry Point ──────────────────────────────────────

def find_email(first_name: str, last_name: str, domain: str, linkedin_url: str = "") -> dict:
    """
    Find a verified email address using FullEnrich (primary) + Prospeo (fallback)
    + domain pattern cache.

    Flow:
      1. Check cached pattern for this domain
         → If found: generate candidate (informational — still need API verification)

      2. Try FullEnrich (primary — 20+ provider waterfall, highest hit rate)
         → If found: return + learn pattern + cache it

      3. Try Prospeo (fallback — also provides rich company enrichment data)
         → If found: return + learn pattern + cache it

      4. If nothing works: return NOT_FOUND (NEVER guess)

    Args:
        first_name: Person's first name.
        last_name: Person's last name (surname).
        domain: Company domain (e.g., 'company.com').
        linkedin_url: Optional LinkedIn profile URL (improves FullEnrich hit rate).

    Returns:
        dict with keys:
            - "email": str (found email or "")
            - "verified": bool
            - "source": str ("FULLENRICH_VERIFIED", "FULLENRICH_CATCH_ALL",
                             "PROSPEO_VERIFIED", "PROSPEO_CATCH_ALL",
                             "NOT_FOUND")
            - "catch_all": bool
            - "raw": dict (details about the discovery method)
            - "enrichment": dict (contact + company data from whichever provider found it)
    """
    if not first_name or not last_name or not domain:
        log.warning(
            f"find_email: missing inputs — "
            f"name='{first_name} {last_name}', domain='{domain}'"
        )
        return _not_found()

    first_name = first_name.strip()
    last_name = last_name.strip()
    domain = domain.strip().lower()

    log.info(f"Finding email for {first_name} {last_name} @ {domain}")

    # ── Step 1: Check domain pattern cache ────────────────
    cached = _get_cached_pattern(domain)
    if cached and cached["pattern"]:
        log.info(
            f"  Cache hit: domain '{domain}' uses pattern "
            f"'{cached['pattern']}' (confidence={cached['confidence']})"
        )
        # Generate candidate for logging/debugging — we still verify via API
        candidate = generate_from_pattern(
            cached["pattern"], first_name, last_name, domain
        )
        if candidate:
            log.info(f"  Pattern candidate: {candidate}")

    # ── Step 2: Try FullEnrich (primary) ──────────────────
    log.info(f"  Trying FullEnrich (primary) for {first_name} {last_name} @ {domain}")
    fe_result = _try_fullenrich(first_name, last_name, domain, linkedin_url=linkedin_url)

    if fe_result and fe_result.get("email"):
        email = fe_result["email"]
        is_catch_all = fe_result.get("catch_all", False)
        source = fe_result.get("source", "FULLENRICH_VERIFIED")

        # Learn pattern
        detected = detect_pattern(email, first_name, last_name)
        if detected:
            catch_all_str = "YES" if is_catch_all else "NO"
            _save_pattern(domain, detected, catch_all_str, email)
            log.info(f"  Learned pattern for {domain}: {detected}")

        log.info(f"  [OK] FullEnrich found: {email} ({source})")

        return {
            "email": email,
            "verified": not is_catch_all,
            "source": source,
            "catch_all": is_catch_all,
            "raw": {
                "method": "fullenrich",
                "detected_pattern": detected,
                "fullenrich_result": fe_result.get("raw", {}),
            },
            "enrichment": fe_result.get("enrichment"),
        }

    # ── Step 3: Try Prospeo (fallback) ────────────────────
    log.info(f"  Trying Prospeo (fallback) for {first_name} {last_name} @ {domain}")

    prospeo_result = prospeo_find_email(first_name, last_name, domain)

    if prospeo_result["email"]:
        email = prospeo_result["email"]
        is_catch_all = prospeo_result.get("catch_all", False)
        source = prospeo_result.get("source", "PROSPEO_VERIFIED")

        # Learn pattern
        detected = detect_pattern(email, first_name, last_name)
        if detected:
            catch_all_str = "YES" if is_catch_all else "NO"
            _save_pattern(domain, detected, catch_all_str, email)
            log.info(f"  Learned pattern for {domain}: {detected}")
        else:
            log.debug(
                f"  Could not detect pattern from {email} "
                f"for {first_name} {last_name}"
            )

        log.info(f"  [OK] Prospeo (fallback) found: {email} ({source})")

        return {
            "email": email,
            "verified": not is_catch_all,
            "source": source,
            "catch_all": is_catch_all,
            "raw": {
                "method": "prospeo_fallback",
                "detected_pattern": detected,
                "prospeo_result": prospeo_result.get("raw", {}),
            },
            "enrichment": prospeo_result.get("enrichment"),
        }

    # ── Step 4: Not found — NO guessing ───────────────────
    log.info(f"  [NOT FOUND] No verified email for {first_name} {last_name} @ {domain}")
    return _not_found()


def _not_found() -> dict:
    """Return a standardized not-found result."""
    return {
        "email": "",
        "verified": False,
        "source": "NOT_FOUND",
        "catch_all": False,
        "raw": {},
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 4:
        first, last, dom = sys.argv[1], sys.argv[2], sys.argv[3]
        linkedin = sys.argv[4] if len(sys.argv) > 4 else ""
    else:
        first, last, dom = "test", "user", "example.com"
        linkedin = ""

    print(f"Finding email for: {first} {last} @ {dom}")
    result = find_email(first, last, dom, linkedin_url=linkedin)
    print(f"Result: {result}")
