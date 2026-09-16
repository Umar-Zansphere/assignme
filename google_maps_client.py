"""
google_maps_client.py — Google Maps data via Apify actor.

Uses the Apify Google Maps Scraper actor to find local/physical businesses.
Normalizes results into the standard pipeline Company schema.

Usage:
    from google_maps_client import search_places
"""

from apify_client import run_actor
from utils import get_logger

log = get_logger("google_maps")

# Apify actor for Google Maps scraping
GOOGLE_MAPS_ACTOR = "compass/crawler-google-places"


def search_places(
    queries: list[str],
    max_results: int = 100,
    language: str = "en",
    country: str | None = None,
) -> list[dict]:
    """
    Search Google Maps for businesses matching the given queries.

    Args:
        queries: List of search terms (e.g., ["dentist in Pune", "EV showroom India"])
        max_results: Max results per query
        language: Language code
        country: Country code filter (e.g., "IN", "US")

    Returns:
        List of normalized company dicts with keys:
            name, website, phone, address, category, rating,
            country, employee_count (None), industry, source
    """
    log.info(f"Google Maps search: {len(queries)} queries, max {max_results} results each")

    all_results = []

    for query in queries:
        log.info(f"  Searching: '{query}'")

        input_data = {
            "searchStringsArray": [query],
            "maxCrawledPlacesPerSearch": min(max_results, 200),
            "language": language,
            "skipClosedPlaces": True,
        }

        if country:
            input_data["countryCode"] = country

        try:
            raw_results = run_actor(GOOGLE_MAPS_ACTOR, input_data, timeout_secs=600)
            log.info(f"  Got {len(raw_results)} places for '{query}'")
        except Exception as e:
            log.error(f"  Google Maps scraper failed for '{query}': {e}")
            continue

        for place in raw_results:
            name = place.get("title") or place.get("name", "")
            if not name:
                continue

            # Extract website — prefer direct website over Google Maps URL
            website = place.get("website") or place.get("url", "")

            # Extract phone
            phone = place.get("phone") or place.get("phoneUnformatted", "")

            # Extract address components
            address = place.get("address") or ""
            city = place.get("city") or ""
            state = place.get("state") or ""
            country_name = place.get("countryCode") or place.get("country") or ""

            # Category
            category = place.get("categoryName") or ""
            categories = place.get("categories", [])
            if categories and not category:
                category = categories[0] if isinstance(categories[0], str) else ""

            # Rating
            rating = place.get("totalScore") or place.get("rating")
            review_count = place.get("reviewsCount") or 0

            normalized = {
                "name": name.strip(),
                "website": website,
                "phone": phone,
                "address": address,
                "city": city,
                "state": state,
                "country": country_name,
                "category": category,
                "rating": float(rating) if rating else None,
                "review_count": review_count,
                "industry": category,  # Use Maps category as industry
                "employee_count": None,  # Maps doesn't provide this
                "linkedin_url": "",
                "source": "google_maps",
                "maps_url": place.get("url", ""),
                "place_id": place.get("placeId", ""),
            }

            # Check if owner info is available
            owner_name = place.get("ownerName") or ""
            if owner_name:
                normalized["contact_name"] = owner_name
                normalized["contact_source"] = "google_maps_owner"

            all_results.append(normalized)

    # Deduplicate by name + address (same business can appear in multiple queries)
    seen = set()
    unique = []
    for r in all_results:
        key = (r["name"].lower(), (r.get("address") or "").lower()[:50])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    log.info(f"Google Maps total: {len(all_results)} raw → {len(unique)} unique places")
    return unique


if __name__ == "__main__":
    # Quick test
    results = search_places(
        queries=["electric motorcycle showroom India"],
        max_results=10,
    )
    for r in results:
        print(f"  {r['name']} — {r['category']} — {r['phone']} — {r['website']}")

