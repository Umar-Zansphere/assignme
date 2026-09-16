"""
dns_checker.py — DNS Verification

Verifies SPF, DKIM, and DMARC records for sending domains.
Uses dnspython (already in requirements).

Google requires SPF/DKIM for senders and DMARC for bulk senders.
Microsoft similarly requires SPF, DKIM and DMARC for high-volume senders.

Usage:
    from dns_checker import verify_domain, get_dns_checklist
"""

from dataclasses import dataclass, field

import dns.resolver

from models import DomainHealth
from database import get_session
from utils import get_logger, utcnow

log = get_logger("dns_checker")


@dataclass
class DNSCheckResult:
    """Result of a single DNS check."""
    valid: bool
    record_type: str      # SPF, DKIM, DMARC
    value: str | None = None  # The actual record value found
    error: str | None = None


@dataclass
class DomainVerificationResult:
    """Full DNS verification result for a domain."""
    domain: str
    spf: DNSCheckResult = None
    dkim: DNSCheckResult = None
    dmarc: DNSCheckResult = None
    all_passed: bool = False
    checks: list[dict] = field(default_factory=list)


def _resolve_txt(name: str) -> list[str]:
    """Resolve TXT records for a domain name."""
    try:
        answers = dns.resolver.resolve(name, "TXT")
        records = []
        for rdata in answers:
            # TXT records can be multi-string, join them
            txt = b"".join(rdata.strings).decode("utf-8", errors="replace")
            records.append(txt)
        return records
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except Exception as e:
        log.warning(f"DNS query failed for {name}: {e}")
        return []


def check_spf(domain: str) -> DNSCheckResult:
    """
    Check if domain has a valid SPF record.

    Looks for a TXT record starting with 'v=spf1' on the domain.
    """
    records = _resolve_txt(domain)

    for record in records:
        if record.lower().startswith("v=spf1"):
            return DNSCheckResult(
                valid=True,
                record_type="SPF",
                value=record,
            )

    return DNSCheckResult(
        valid=False,
        record_type="SPF",
        error=f"No SPF record found on {domain}",
    )


def check_dkim(domain: str, selector: str = "default") -> DNSCheckResult:
    """
    Check if domain has a DKIM record.

    Tries a comprehensive list of selectors covering all major email providers:
    Google Workspace, Microsoft 365, Hostinger, Zoho, SendGrid, Mailgun,
    Amazon SES, Brevo/Sendinblue, Postmark, and common generic patterns.
    """
    # Build ordered selector list — provider-specific first, generic last
    selectors_to_try = [selector] if selector != "default" else []
    selectors_to_try.extend([
        # Google Workspace
        "google",
        # Microsoft 365 / Outlook
        "selector1", "selector2",
        # Hostinger Mail (the user's provider — hostingermail-a/b/c._domainkey)
        "hostingermail-a", "hostingermail-b", "hostingermail-c",
        # Zoho Mail
        "zoho", "zoho1", "zoho2",
        # SendGrid
        "s1", "s2", "smtpapi",
        # Mailgun
        "mailo", "pic",
        # Amazon SES
        "amazonses",
        # Brevo / Sendinblue
        "mail",
        # Postmark
        "pm",
        # Fastmail
        "fm1", "fm2", "fm3",
        # SparkPost
        "scph0316", "scph0817",
        # Generic / legacy
        "dkim", "default", "key1", "key2", "k1",
        "email", "outbound", "smtp",
    ])
    # Deduplicate while preserving order
    seen = set()
    unique_selectors = []
    for s in selectors_to_try:
        if s not in seen:
            seen.add(s)
            unique_selectors.append(s)
    selectors_to_try = unique_selectors

    found_selector = None
    for sel in selectors_to_try:
        dkim_name = f"{sel}._domainkey.{domain}"
        records = _resolve_txt(dkim_name)

        for record in records:
            if "v=DKIM1" in record or "p=" in record:
                found_selector = sel
                return DNSCheckResult(
                    valid=True,
                    record_type="DKIM",
                    value=f"{sel}._domainkey → {record[:80]}{'...' if len(record) > 80 else ''}",
                )

        # Also try CNAME (Hostinger, Google Workspace, and others use CNAMEs)
        try:
            answers = dns.resolver.resolve(dkim_name, "CNAME")
            if answers:
                found_selector = sel
                return DNSCheckResult(
                    valid=True,
                    record_type="DKIM",
                    value=f"{sel}._domainkey → CNAME: {str(answers[0])}",
                )
        except Exception:
            pass

    # None matched — report all tried selectors in the error
    tried_summary = ", ".join(selectors_to_try[:8]) + (
        f" … (+{len(selectors_to_try)-8} more)" if len(selectors_to_try) > 8 else ""
    )
    return DNSCheckResult(
        valid=False,
        record_type="DKIM",
        error=f"No DKIM record found (tried {len(selectors_to_try)} selectors: {tried_summary})",
    )


def check_dmarc(domain: str) -> DNSCheckResult:
    """
    Check if domain has a DMARC record.

    Looks for a TXT record at _dmarc.<domain>.
    """
    dmarc_name = f"_dmarc.{domain}"
    records = _resolve_txt(dmarc_name)

    for record in records:
        if record.lower().startswith("v=dmarc1"):
            return DNSCheckResult(
                valid=True,
                record_type="DMARC",
                value=record,
            )

    return DNSCheckResult(
        valid=False,
        record_type="DMARC",
        error=f"No DMARC record found at _dmarc.{domain}",
    )


def verify_domain(domain: str) -> DomainVerificationResult:
    """
    Run full DNS verification on a domain.

    Checks SPF, DKIM, and DMARC. Updates the DomainHealth record in the database.
    """
    log.info(f"Verifying DNS for domain: {domain}")

    spf_result = check_spf(domain)
    dkim_result = check_dkim(domain)
    dmarc_result = check_dmarc(domain)

    all_passed = spf_result.valid and dkim_result.valid and dmarc_result.valid

    # Update database
    with get_session() as session:
        domain_health = session.query(DomainHealth).filter_by(domain=domain).first()
        if not domain_health:
            domain_health = DomainHealth(domain=domain)
            session.add(domain_health)

        domain_health.spf_valid = 1 if spf_result.valid else 0
        domain_health.dkim_valid = 1 if dkim_result.valid else 0
        domain_health.dmarc_valid = 1 if dmarc_result.valid else 0
        domain_health.dns_last_checked = utcnow()

        if all_passed:
            if domain_health.status == "PENDING_VERIFICATION":
                domain_health.status = "VERIFIED"
                log.info(f"Domain {domain}: DNS VERIFIED [OK]")
        else:
            if domain_health.status != "BLOCKED":
                domain_health.status = "PENDING_VERIFICATION"

    result = DomainVerificationResult(
        domain=domain,
        spf=spf_result,
        dkim=dkim_result,
        dmarc=dmarc_result,
        all_passed=all_passed,
    )

    # Build checklist
    result.checks = get_dns_checklist_from_results(domain, spf_result, dkim_result, dmarc_result)

    status = "[OK] ALL PASSED" if all_passed else "[FAIL] ISSUES FOUND"
    log.info(
        f"DNS verification for {domain}: {status} "
        f"(SPF={'OK' if spf_result.valid else 'FAIL'} "
        f"DKIM={'OK' if dkim_result.valid else 'FAIL'} "
        f"DMARC={'OK' if dmarc_result.valid else 'FAIL'})"
    )

    return result


def get_dns_checklist(domain: str) -> list[dict]:
    """
    Get a DNS configuration checklist for a domain.

    Returns a list of dicts with: record_type, name, status, value, instruction
    """
    spf = check_spf(domain)
    dkim = check_dkim(domain)
    dmarc = check_dmarc(domain)
    return get_dns_checklist_from_results(domain, spf, dkim, dmarc)


def get_dns_checklist_from_results(
    domain: str,
    spf: DNSCheckResult,
    dkim: DNSCheckResult,
    dmarc: DNSCheckResult,
) -> list[dict]:
    """Build checklist from existing check results."""
    checklist = []

    # SPF
    checklist.append({
        "record_type": "SPF",
        "name": f"{domain}",
        "dns_type": "TXT",
        "status": "✓" if spf.valid else "✗",
        "value": spf.value or spf.error,
        "instruction": (
            "Add a TXT record on @ with: v=spf1 include:_spf.google.com ~all"
            if not spf.valid else "SPF configured correctly"
        ),
    })

    # DKIM
    checklist.append({
        "record_type": "DKIM",
        "name": "selector._domainkey." + domain,
        "dns_type": "TXT/CNAME",
        "status": "✓" if dkim.valid else "✗",
        "value": dkim.value or dkim.error,
        "instruction": (
            "Configure DKIM in your email provider (Google Workspace → Admin → Apps → Gmail → Authenticate email)"
            if not dkim.valid else "DKIM configured correctly"
        ),
    })

    # DMARC
    checklist.append({
        "record_type": "DMARC",
        "name": f"_dmarc.{domain}",
        "dns_type": "TXT",
        "status": "✓" if dmarc.valid else "✗",
        "value": dmarc.value or dmarc.error,
        "instruction": (
            f"Add a TXT record on _dmarc.{domain} with: v=DMARC1; p=none; rua=mailto:dmarc@{domain}"
            if not dmarc.valid else "DMARC configured correctly"
        ),
    })

    return checklist


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dns_checker.py <domain>")
        sys.exit(1)

    domain = sys.argv[1]
    result = verify_domain(domain)

    print(f"\nDNS Verification: {domain}")
    print("=" * 50)
    for check in result.checks:
        print(f"  {check['status']} {check['record_type']:6s} {check['value']}")
    print()
    if result.all_passed:
        print("✅ All DNS checks passed — domain ready for sending")
    else:
        print("❌ DNS issues found — fix before sending")
        for check in result.checks:
            if check["status"] == "✗":
                print(f"   → {check['instruction']}")
