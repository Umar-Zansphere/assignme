"""
apollo_client.py — Apollo.io API client for B2B company & contact search.

Apollo provides comprehensive B2B data: companies, contacts, emails,
phone numbers, funding, tech stack, and more.

API Docs: https://apolloio.github.io/apollo-api-docs/

Usage:
    from apollo_client import search_organizations, search_people
"""

import httpx
from config import APOLLO_API_KEY, APOLLO_BASE_URL
from utils import get_logger, retry

log = get_logger("apollo")


class ApolloError(Exception):
    """Raised when Apollo API call fails."""
    pass


@retry(max_attempts=3, base_delay=3.0)
def _api_post(endpoint: str, data: dict) -> dict:
    """Make authenticated POST request to Apollo API."""
    if not APOLLO_API_KEY:
        raise ApolloError("APOLLO_API_KEY not set in .env")

    url = f"{APOLLO_BASE_URL}/{endpoint.lstrip('/')}"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    # Apollo uses api_key in the request body
    data["api_key"] = APOLLO_API_KEY

    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, json=data, headers=headers)
        if response.status_code == 429:
            raise ApolloError("Apollo rate limit reached — retry later")
        response.raise_for_status()

    return response.json()


def search_organizations(
    keywords: list[str] | None = None,
    industries: list[str] | None = None,
    locations: list[str] | None = None,
    min_employees: int | None = None,
    max_employees: int | None = None,
    per_page: int = 25,
    page: int = 1,
) -> list[dict]:
    """
    Search for organizations on Apollo.

    Returns list of normalized company dicts with keys:
        name, website, industry, country, employee_count, linkedin_url,
        description, founded_year, funding_stage, phone
    """
    log.info(f"Apollo org search: keywords={keywords}, industries={industries}, locations={locations}")

    body = {
        "page": page,
        "per_page": min(per_page, 100),
    }

    if keywords:
        body["q_organization_keyword_tags"] = keywords
    if industries:
        body["organization_industry_tag_ids"] = industries
    if locations:
        body["organization_locations"] = locations
    if min_employees is not None or max_employees is not None:
        body["organization_num_employees_ranges"] = [
            f"{min_employees or 1},{max_employees or 10000}"
        ]

    try:
        result = _api_post("/v1/mixed_companies/search", body)
    except Exception as e:
        log.error(f"Apollo search failed: {e}")
        return []

    organizations = result.get("organizations", [])
    log.info(f"Apollo returned {len(organizations)} organizations")

    normalized = []
    for org in organizations:
        normalized.append({
            "name": org.get("name", ""),
            "website": org.get("website_url") or org.get("primary_domain", ""),
            "industry": org.get("industry", ""),
            "country": org.get("country", ""),
            "employee_count": org.get("estimated_num_employees"),
            "linkedin_url": org.get("linkedin_url", ""),
            "description": org.get("short_description", ""),
            "founded_year": org.get("founded_year"),
            "funding_stage": org.get("latest_funding_stage", ""),
            "phone": org.get("phone", ""),
            "city": org.get("city", ""),
            "state": org.get("state", ""),
            "logo_url": org.get("logo_url", ""),
            "apollo_id": org.get("id", ""),
            "source": "apollo",
        })

    return normalized


def search_people(
    organization_domains: list[str] | None = None,
    titles: list[str] | None = None,
    locations: list[str] | None = None,
    per_page: int = 10,
    page: int = 1,
) -> list[dict]:
    """
    Search for people (contacts) on Apollo.

    Useful for Strategy A — Apollo already provides contact + email.

    Returns list of normalized contact dicts with keys:
        name, email, role, linkedin_url, phone, company_name, verified
    """
    log.info(f"Apollo people search: domains={organization_domains}, titles={titles}")

    body = {
        "page": page,
        "per_page": min(per_page, 100),
    }

    if organization_domains:
        body["q_organization_domains"] = "\n".join(organization_domains)
    if titles:
        body["person_titles"] = titles
    if locations:
        body["person_locations"] = locations

    try:
        result = _api_post("/v1/mixed_people/search", body)
    except Exception as e:
        log.error(f"Apollo people search failed: {e}")
        return []

    people = result.get("people", [])
    log.info(f"Apollo returned {len(people)} people")

    contacts = []
    for person in people:
        email = person.get("email", "")
        contacts.append({
            "name": person.get("name", ""),
            "first_name": person.get("first_name", ""),
            "last_name": person.get("last_name", ""),
            "email": email,
            "role": person.get("title", ""),
            "linkedin_url": person.get("linkedin_url", ""),
            "phone": (person.get("phone_numbers") or [{}])[0].get("sanitized_number", "") if person.get("phone_numbers") else "",
            "company_name": (person.get("organization") or {}).get("name", ""),
            "company_domain": (person.get("organization") or {}).get("primary_domain", ""),
            "city": person.get("city", ""),
            "country": person.get("country", ""),
            "headline": person.get("headline", ""),
            "verified": "APOLLO_VERIFIED" if email else None,
            "apollo_id": person.get("id", ""),
            "source": "apollo",
        })

    return contacts


def enrich_company(domain: str) -> dict | None:
    """
    Enrich a single company by domain using Apollo's organization enrichment.

    Returns normalized company dict or None.
    """
    log.info(f"Apollo enriching domain: {domain}")

    try:
        result = _api_post("/v1/organizations/enrich", {"domain": domain})
    except Exception as e:
        log.error(f"Apollo enrich failed for {domain}: {e}")
        return None

    org = result.get("organization")
    if not org:
        return None

    return {
        "name": org.get("name", ""),
        "website": org.get("website_url") or domain,
        "industry": org.get("industry", ""),
        "country": org.get("country", ""),
        "employee_count": org.get("estimated_num_employees"),
        "linkedin_url": org.get("linkedin_url", ""),
        "description": org.get("short_description", ""),
        "founded_year": org.get("founded_year"),
        "funding_stage": org.get("latest_funding_stage", ""),
        "phone": org.get("phone", ""),
        "source": "apollo",
    }


if __name__ == "__main__":
    # Quick test
    results = search_organizations(
        keywords=["electric vehicle"],
        locations=["India"],
        max_employees=200,
        per_page=5,
    )
    for r in results:
        print(f"  {r['name']} — {r['industry']} — {r['employee_count']} employees — {r['website']}")

