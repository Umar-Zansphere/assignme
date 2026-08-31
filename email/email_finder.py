#!/usr/bin/env python3
"""
email_finder.py — Hunter.io-style email finder

Given a person's full name and a company domain, this tool:
  1. Generates the common corporate email patterns (first.last@, flast@, etc.)
  2. Looks up the domain's MX records to find its mail server
  3. Probes each candidate via SMTP RCPT TO (without sending a real message)
  4. Detects "catch-all" domains (which accept any address, making SMTP
     probing unreliable) and adjusts confidence accordingly
  5. Returns candidates ranked by confidence, like Hunter's pattern+verify approach

USAGE:
    python3 email_finder.py "Jane Doe" example.com
    python3 email_finder.py "Jane Doe" example.com --no-verify
    python3 email_finder.py "Jane Doe" example.com --from-addr you@yourdomain.com

NOTES / LIMITATIONS (read before relying on this):
  - Many mail servers (Google Workspace, Outlook 365, Proofpoint, etc.) reject
    or greylist SMTP RCPT probing, or accept everything as a catch-all to
    prevent exactly this kind of enumeration. Verification is best-effort,
    not authoritative.
  - Some networks block outbound port 25 entirely (most home ISPs and many
    cloud providers do). If that's the case here, use --no-verify to just
    get ranked pattern guesses without live checking.
  - Use responsibly: only look up business contact emails you have a
    legitimate reason to contact, and follow applicable anti-spam law
    (e.g. CAN-SPAM, GDPR/PECR) and each site's terms of service.
"""

import argparse
import logging
import random
import re
import smtplib
import socket
import string
import sys
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import dns.resolver
except ImportError:
    print("Missing dependency. Install with: pip install dnspython", file=sys.stderr)
    sys.exit(1)

# SearXNG layer is optional — email_finder still works (pattern+SMTP only) without it.
try:
    from searxng_scraper import SearXNGClient, find_emails_for_person, dedupe_hits
    HAS_SEARXNG = True
except ImportError:
    HAS_SEARXNG = False

logger = logging.getLogger("email_finder")


# --------------------------------------------------------------------------
# 1. Name parsing & pattern generation
# --------------------------------------------------------------------------

def split_name(full_name: str):
    """Split a full name into (first, last). Middle names/initials are dropped."""
    parts = re.sub(r"[^A-Za-z\s\-']", "", full_name).split()
    if len(parts) == 0:
        raise ValueError("No usable name parts found")
    if len(parts) == 1:
        return parts[0].lower(), ""
    first = parts[0].lower()
    last = parts[-1].lower()
    # strip hyphens/apostrophes variants separately if needed later
    return first, last


def generate_candidates(full_name: str, domain: str) -> List[str]:
    """Generate ranked candidate emails using the most common corporate patterns.
    Order roughly reflects real-world frequency (per Hunter's own published stats:
    first.last and flast are the most common patterns)."""
    first, last = split_name(full_name)
    f, l = first[:1], last[:1]
    domain = domain.lower().strip()

    if not last:
        # single-word name — limited pattern set
        patterns = [first]
    else:
        patterns = [
            f"{first}.{last}",     # jane.doe
            f"{first}{last}",      # janedoe
            f"{f}{last}",          # jdoe
            f"{first}",            # jane
            f"{first}_{last}",     # jane_doe
            f"{last}.{first}",     # doe.jane
            f"{last}{first}",      # doejane
            f"{first}.{l}",        # jane.d
            f"{last}",             # doe
            f"{f}.{last}",         # j.doe
            f"{first}{l}",         # janed
        ]

    seen = set()
    ordered = []
    for p in patterns:
        addr = f"{p}@{domain}"
        if addr not in seen:
            seen.add(addr)
            ordered.append(addr)
    return ordered


# --------------------------------------------------------------------------
# 2. MX lookup
# --------------------------------------------------------------------------

def get_mx_host(domain: str) -> Optional[str]:
    """Return the highest-priority MX host for a domain, or None if not found."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        # lowest preference number = highest priority
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".")
    except Exception:
        return None


# --------------------------------------------------------------------------
# 3. SMTP verification
# --------------------------------------------------------------------------

@dataclass
class VerifyResult:
    email: str
    smtp_code: Optional[int] = None
    accepted: Optional[bool] = None   # True/False/None(unknown)
    reason: str = ""


def port25_reachable(mx_host: str, timeout: int = 5) -> bool:
    """Quick raw-socket check: can we even open a TCP connection to port 25?
    Run this ONCE before probing candidates — if it fails, outbound SMTP is
    almost certainly blocked at the network level (very common on residential
    ISPs and cloud providers), and every subsequent per-candidate probe would
    fail identically after its own long timeout. Catching that here avoids
    minutes of guaranteed-to-fail waiting."""
    try:
        with socket.create_connection((mx_host, 25), timeout=timeout):
            return True
    except Exception as e:
        logger.debug(f"Port 25 reachability check to {mx_host} failed: {e}")
        return False


def _random_local_part(n=16) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def check_catch_all(mx_host: str, domain: str, from_addr: str, timeout: int = 8) -> Optional[bool]:
    """Probe a definitely-fake address. If the server accepts it, the domain
    is a catch-all and per-address SMTP verification can't be trusted."""
    fake = f"{_random_local_part()}@{domain}"
    result = smtp_probe(mx_host, fake, from_addr, timeout)
    return result.accepted


def smtp_probe(mx_host: str, email: str, from_addr: str, timeout: int = 8) -> VerifyResult:
    """Connect to the mail server and issue RCPT TO without sending a message.
    2xx = accepted, 5xx = rejected, anything else/unreachable = unknown."""
    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as smtp:
            smtp.ehlo_or_helo_if_needed()
            try:
                smtp.starttls()
                smtp.ehlo()
            except Exception:
                pass  # TLS not offered/required — continue in plaintext
            smtp.mail(from_addr)
            code, message = smtp.rcpt(email)
            accepted = 200 <= code < 300 or code == 250
            return VerifyResult(email=email, smtp_code=code, accepted=accepted,
                                 reason=message.decode(errors="ignore") if isinstance(message, bytes) else str(message))
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, smtplib.SMTPException, OSError) as e:
        return VerifyResult(email=email, accepted=None, reason=f"unreachable: {e}")


# --------------------------------------------------------------------------
# 4. Orchestration
# --------------------------------------------------------------------------

@dataclass
class Finding:
    email: str
    confidence: str      # "verified" | "high" | "medium" | "low" | "unverified"
    detail: str = ""
    source_url: str = ""


# Confidence tiers ranked best-to-worst for sorting output.
CONFIDENCE_ORDER = {"verified": 0, "high": 1, "medium": 2, "unverified": 3, "low": 4}


def find_email(full_name: str, domain: str, verify: bool = True,
                from_addr: str = "verify@example.com",
                searxng_url: Optional[str] = None,
                max_web_queries: int = 5) -> List[Finding]:
    candidates = generate_candidates(full_name, domain)
    findings_by_email = {}

    # --- Layer 1: public web search via SearXNG (strongest signal — real evidence) ---
    if searxng_url:
        if not HAS_SEARXNG:
            logger.warning("searxng_scraper.py not importable — skipping web search layer. "
                            "Make sure it's in the same directory as email_finder.py.")
        else:
            logger.info(f"Querying SearXNG at {searxng_url} for public mentions...")
            client = SearXNGClient(searxng_url)
            if not client.check_connection():
                logger.warning("SearXNG health check failed — skipping web search layer. "
                                "Run 'python3 searxng_scraper.py --url %s --check --debug' "
                                "to diagnose." % searxng_url)
            else:
                hits = dedupe_hits(find_emails_for_person(client, full_name, domain, max_web_queries))
                for h in hits:
                    logger.debug(f"Web hit: {h.email} from {h.source_url}")
                    # Keep the first (best) source per email
                    if h.email not in findings_by_email:
                        findings_by_email[h.email] = Finding(
                            email=h.email, confidence="verified",
                            detail=f"found publicly at {h.source_url}",
                            source_url=h.source_url)

    # --- Layer 2: SMTP pattern verification (for candidates not already web-verified) ---
    if verify:
        mx_host = get_mx_host(domain)
        if not mx_host:
            logger.warning(f"No MX record found for {domain} — cannot run SMTP verification.")
            for c in candidates:
                findings_by_email.setdefault(c, Finding(
                    email=c, confidence="unverified",
                    detail="no MX record found for domain — cannot verify"))
        else:
            logger.debug(f"Using MX host: {mx_host}")

            if not port25_reachable(mx_host):
                logger.warning(
                    f"Could not open a TCP connection to {mx_host}:25 — outbound SMTP (port 25) "
                    "appears to be blocked on this network. This is common on residential ISPs and "
                    "many cloud providers, and cannot be fixed from within this script. "
                    "Try again from a network/server that allows outbound port 25, or use --no-verify."
                )
                for c in candidates:
                    findings_by_email.setdefault(c, Finding(
                        email=c, confidence="unverified",
                        detail="SMTP unreachable — port 25 appears blocked on this network"))
            else:
                logger.info("Checking whether domain is a catch-all (accepts any address)...")
                catch_all = check_catch_all(mx_host, domain, from_addr)
                logger.info(f"Catch-all domain: {catch_all}")

                to_probe = [c for c in candidates if c not in findings_by_email]
                for i, c in enumerate(to_probe, 1):
                    if catch_all:
                        findings_by_email[c] = Finding(
                            email=c, confidence="low",
                            detail="domain accepts all addresses (catch-all) — pattern guess only")
                        continue

                    logger.info(f"SMTP probe {i}/{len(to_probe)}: {c} ...")
                    result = smtp_probe(mx_host, c, from_addr)
                    logger.debug(f"SMTP probe {c}: accepted={result.accepted} code={result.smtp_code} reason={result.reason}")
                    if result.accepted is True:
                        findings_by_email[c] = Finding(email=c, confidence="high",
                                                        detail=f"SMTP accepted (code {result.smtp_code})")
                    elif result.accepted is False:
                        findings_by_email[c] = Finding(email=c, confidence="low",
                                                        detail=f"SMTP rejected (code {result.smtp_code})")
                    else:
                        findings_by_email[c] = Finding(email=c, confidence="medium",
                                                        detail=f"could not verify: {result.reason}")
    else:
        for c in candidates:
            findings_by_email.setdefault(c, Finding(
                email=c, confidence="unverified",
                detail="pattern only, no SMTP check performed"))

    findings = list(findings_by_email.values())
    findings.sort(key=lambda f: CONFIDENCE_ORDER.get(f.confidence, 5))
    return findings


# --------------------------------------------------------------------------
# 5. CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Find a likely corporate email address for a person.")
    ap.add_argument("full_name", help='Full name, e.g. "Jane Doe"')
    ap.add_argument("domain", help="Company domain, e.g. example.com")
    ap.add_argument("--no-verify", action="store_true",
                     help="Skip MX/SMTP verification, just print ranked pattern guesses")
    ap.add_argument("--from-addr", default="verify@example.com",
                     help="MAIL FROM address to use during SMTP probing (use a real domain you control for best results)")
    ap.add_argument("--searxng-url", default=None,
                     help="Base URL of a SearXNG instance (e.g. http://localhost:8888) to search "
                          "the public web for already-published emails before falling back to SMTP guessing")
    ap.add_argument("--max-web-queries", type=int, default=5,
                     help="Max number of SearXNG queries to run (default 5)")
    ap.add_argument("--debug", action="store_true",
                     help="Verbose logging of every SMTP probe and SearXNG request/response")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # Progress messages (SMTP probe N/M, catch-all check, etc.) stay visible by default
    # at INFO level so a slow gateway doesn't look like a hang. --debug additionally
    # unlocks per-request HTTP details from urllib3/requests, which are noisy otherwise.
    if not args.debug:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)

    if args.searxng_url and not HAS_SEARXNG:
        print("Warning: --searxng-url given but searxng_scraper.py could not be imported.\n"
              "Make sure searxng_scraper.py is in the same directory, and 'pip install requests'.\n",
              file=sys.stderr)

    print(f"\nSearching for: {args.full_name} @ {args.domain}\n" + "-" * 60)

    findings = find_email(args.full_name, args.domain,
                           verify=not args.no_verify, from_addr=args.from_addr,
                           searxng_url=args.searxng_url, max_web_queries=args.max_web_queries)

    tags = {"verified": "✔ WEB ", "high": "✔ HIGH", "medium": "? MED ",
            "low": "  low ", "unverified": "  ?   "}
    for f in findings:
        print(f"[{tags[f.confidence]}] {f.email:<35} {f.detail}")

    if not args.no_verify and all(f.confidence == "unverified" for f in findings):
        print("\nNote: verification could not run (no MX found, or SMTP unreachable —\n"
              "many networks/ISPs block outbound port 25). Try --no-verify for pattern-only results,\n"
              "or run this from a network/server that allows outbound SMTP.")

    if args.searxng_url and HAS_SEARXNG and not any(f.confidence == "verified" for f in findings):
        print("\nNote: no public web mentions found via SearXNG. Debug that layer directly with:\n"
              f"  python3 searxng_scraper.py --url {args.searxng_url} --name \"{args.full_name}\" "
              f"--domain {args.domain} --debug")


if __name__ == "__main__":
    main()