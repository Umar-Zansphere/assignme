"""
campaign_builder.py — Stage 0: Campaign Intelligence

Takes a natural language brief from the user, uses Ollama LLM to decompose
it into a structured campaign config, then lets the user review & edit.

Usage:
    from campaign_builder import decompose_brief
    config = decompose_brief("Find EV startups in India importing parts...")
"""

import json
import config
from openrouter_client import call_llm_with_schema
from utils import get_logger, safe_json_dumps


def _get_available_sources() -> tuple[set, str]:
    sources = {}
    for name, data in config.AVAILABLE_DATA_SOURCES.items():
        # Check availability
        if name in ("apollo"):
            available = bool(config.APOLLO_API_KEY)
        elif name not in ("google_maps", "searxng"):
            available = bool(config.APIFY_API_TOKEN)
        else:
            available = True
            
        sources[name] = {
            "available": available,
            "desc": data["description"]
        }

    lines = []
    available_set = set()
    for name, info in sources.items():
        if info["available"]:
            lines.append(f'- ✅ AVAILABLE: "{name}" — {info["desc"]}')
            available_set.add(name)
        else:
            lines.append(f'- ❌ UNAVAILABLE (no API key): "{name}" — {info["desc"]}')

    return available_set, "\n".join(lines)

log = get_logger("campaign_builder")


DECOMPOSE_PROMPT = """You are an expert B2B sales strategist and campaign architect.

Given a natural language brief describing a target audience, decompose it into
a structured campaign configuration that will drive an automated lead generation pipeline.

Think carefully about:
1. What kind of companies the user is looking for
2. Where those companies can be found (data sources)
3. What search queries would find them on each platform
4. What scoring rules would separate good leads from bad ones
5. Who the decision maker is at these companies
6. How the outreach email should be framed

Available data sources — ONLY use sources marked ✅ AVAILABLE:
{available_sources_block}

Respond with ONLY a JSON object containing ALL these keys:

{{
  "name": "Short campaign name (3-5 words)",
  "target_industries": ["Industry 1", "Industry 2", ...],
  "target_geography": ["Country or Region 1", ...],
  "target_company_size": "min-max employees, e.g. 1-200",
  "target_roles": ["Role 1 (highest priority)", "Role 2", "Role 3"],
  
  "source_selection": ["source1", "source2"],
  "search_queries": {{
    "google_maps": ["query 1 for maps", "query 2"],
    "searxng": ["web search query 1", "web search query 2", "web search query 3"]
  }},
  
  "scoring_rules": [...see rules section below...],
  "scoring_threshold": <integer>,
  "exclusion_list": ["keyword or phrase to hard-exclude 1", "..."],
  
  "our_offering": "What the sender's company provides (inferred from brief)",
  "value_proposition": "Why the target would care about this offering",
  "pitch_angle": "How to frame the cold outreach email — what hook to use",
  "research_questions": [
    "Question to investigate about each company during research stage",
    "Another question specific to this campaign's angle"
  ]
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCES (CRITICAL RULES)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- ONLY include sources marked ✅ AVAILABLE in source_selection. NEVER use ❌ sources.
- google_maps is MANDATORY for every campaign. It is the primary company discovery tool.
- searxng is MANDATORY for every campaign. It supplements google_maps with web data.
- linkedin should ONLY be added if the brief specifically asks about hiring activity or job postings. Do NOT use linkedin for general company discovery.
- Only add search_queries for sources you included in source_selection.
- search_queries for google_maps should include the target geography in the query string (e.g., "EV startups India", not just "EV startups").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING RULES (READ EVERY WORD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Rule types:
- "keyword" — matches keywords against company name, industry, description
- "size"    — checks employee count range (use fields: "min", "max")
- "geography" — checks company country (use field: "countries", NOT "keywords")
- "signal"  — checks what source found the company (use field: "sources")

KEYWORD MATCHING — most important type:
  Keyword rules match against: company name, industry, category, and AI description.
  In early pipeline stages, most companies only have a NAME — no industry, no description.
  Therefore: your positive keyword rules MUST include terms that appear in company NAMES,
  not just terms that appear in websites or descriptions.
  
  Example ICP: pharma manufacturers
  ✅ GOOD keywords: ["pharma", "pharmaceutical", "pharma pvt", "laboratories", "biotech"]
     (these appear in company names like "Healing Pharma Pvt Ltd")
  ❌ BAD keywords: ["digitize", "implementation", "digital transformation"]
     (these only appear in website text — useless when companies have no description)

NEGATIVE KEYWORD RULES — for disqualifying bad fits:
  Use negative rules (weight < 0) to penalize company types that are clearly not your ICP.
  Example: if targeting pharma manufacturers, penalize retail pharmacies and hospitals.
  ✅ GOOD: {{"rule": "Retail pharmacy - not a B2B software buyer", "weight": -60, "type": "keyword", "keywords": ["pharmacy", "chemist", "drug store", "dispensary"]}}

GEOGRAPHY RULES — STRICT RULES:
  ⚠️  NEVER create a negative geography rule targeting the same country as target_geography.
      If target_geography is ["India"], do NOT create a rule that penalizes "India".
      This is contradictory and will disqualify all your leads.
  ✅  If you want to give a bonus for being in the target geography, use a "geography" rule
      with a POSITIVE weight and list the target countries in the "countries" field.
  ✅  If you want to penalize companies OUTSIDE the target geography, do NOT create a
      geography rule — the pipeline handles this automatically via the geography penalty.
  ✅  ALWAYS use the field name "countries" (not "keywords") in geography rules.
  Example: {{"rule": "Based in target market", "weight": 20, "type": "geography", "countries": ["India"]}}

SIGNAL RULES:
  Use signal rules to reward companies found via higher-quality sources.
  Example: companies found via searxng or apollo tend to have more digital presence.
  {{"rule": "Found via B2B database or web search", "weight": 15, "type": "signal", "sources": ["searxng", "apollo", "linkedin"]}}

THRESHOLD CALIBRATION — CRITICAL:
  scoring_threshold must be reachable by a typical target company.
  Before setting the threshold, calculate the max score for a typical good lead:
    max_score = (highest positive keyword rule weight) + (geography auto-bonus: +20) + (signal rule weight if applicable)
  Then set scoring_threshold = max_score × 0.7  (round to nearest 5)
  
  Example: best keyword rule = +40, geography bonus = +20, signal rule = +15 → max = 75
  Threshold = 75 × 0.7 ≈ 50  ✓
  
  ❌ NEVER set scoring_threshold higher than the sum of all positive rule weights + 20.
     If you do, NO company will ever qualify.

RULE DESIGN CHECKLIST (verify before responding):
  □ At least one positive keyword rule uses words that appear in company NAMES
  □ No negative geography rule targets a country in target_geography
  □ All geography rules use "countries" field, not "keywords"
  □ scoring_threshold ≤ (sum of positive rule weights + 20)
  □ exclusion_list contains company TYPE keywords (e.g. "pharmacy"), not vague terms
"""


def decompose_brief(brief: str) -> dict:
    """
    Decompose a natural language campaign brief into structured config.

    Args:
        brief: Natural language description of target audience and offering.

    Returns:
        dict with all campaign configuration fields.
    """
    log.info(f"Decomposing campaign brief ({len(brief)} chars)...")

    available_sources, sources_block = _get_available_sources()
    system_prompt = DECOMPOSE_PROMPT.format(available_sources_block=sources_block)

    result = call_llm_with_schema(
        system_prompt=system_prompt,
        user_prompt=f"Campaign Brief:\n\n{brief}",
        required_keys=[
            "name", "target_industries", "target_geography",
            "target_roles", "source_selection", "search_queries",
            "scoring_rules", "our_offering", "value_proposition",
        ],
    )

    # Ensure all expected keys have defaults
    defaults = {
        "name": "Untitled Campaign",
        "target_industries": [],
        "target_geography": [],
        "target_company_size": "1-500",
        "target_roles": ["Founder", "CEO", "CTO"],
        "source_selection": ["searxng"],
        "search_queries": {"searxng": []},
        "scoring_rules": [],
        "scoring_threshold": 60,
        "exclusion_list": [],
        "our_offering": "",
        "value_proposition": "",
        "pitch_angle": "",
        "research_questions": [],
    }

    for key, default in defaults.items():
        if key not in result or result[key] is None:
            result[key] = default

    # Remove strict filtering to allow custom LLM-suggested sources
    # (e.g. upwork, custom apify actors)
    # Ensure google_maps and searxng are always included
    for must_have in ("google_maps", "searxng"):
        if must_have not in result["source_selection"]:
            result["source_selection"].append(must_have)

    # Clean up search_queries — only keep queries for selected sources
    cleaned_queries = {}
    for source in result["source_selection"]:
        queries = result.get("search_queries", {}).get(source, [])
        if isinstance(queries, str):
            queries = [queries]
        
        # Fallback if LLM failed to generate queries for mandatory sources
        if not queries and source in ("google_maps", "searxng"):
            geography_str = " ".join(result.get("target_geography", []))
            industry_str = " ".join(result.get("target_industries", []))
            name_str = result.get("name", "companies")
            fallback_query = f"{industry_str} {name_str} {geography_str}".strip()
            queries = [fallback_query]
            
        cleaned_queries[source] = queries
    result["search_queries"] = cleaned_queries

    log.info(f"Campaign decomposed: '{result['name']}' — sources: {result['source_selection']}")
    return result


def create_campaign_from_config(session, config: dict, brief: str):
    """
    Create a Campaign record in the DB from a decomposed config.

    Args:
        session: SQLAlchemy session
        config: dict from decompose_brief()
        brief: original natural language brief

    Returns:
        Campaign ORM object (flushed, has ID)
    """
    from models import Campaign

    campaign = Campaign(
        name=config.get("name", "Untitled Campaign"),
        brief=brief,
        target_industries=safe_json_dumps(config.get("target_industries", [])),
        target_geography=safe_json_dumps(config.get("target_geography", [])),
        target_company_size=config.get("target_company_size", "1-500"),
        target_roles=safe_json_dumps(config.get("target_roles", [])),
        search_queries=safe_json_dumps(config.get("search_queries", {})),
        source_selection=safe_json_dumps(config.get("source_selection", [])),
        scoring_rules=safe_json_dumps(config.get("scoring_rules", [])),
        scoring_threshold=config.get("scoring_threshold", 60),
        exclusion_list=safe_json_dumps(config.get("exclusion_list", [])),
        our_offering=config.get("our_offering", ""),
        value_proposition=config.get("value_proposition", ""),
        pitch_angle=config.get("pitch_angle", ""),
        research_questions=safe_json_dumps(config.get("research_questions", [])),
    )
    session.add(campaign)
    session.flush()
    log.info(f"Campaign created: id={campaign.id}, name='{campaign.name}'")
    return campaign


def update_campaign_from_config(session, campaign_id: int, config: dict):
    """
    Update an existing Campaign record from an edited config dict.

    Args:
        session: SQLAlchemy session
        campaign_id: ID of the campaign to update
        config: dict with campaign configuration fields

    Returns:
        Updated Campaign ORM object, or None if not found
    """
    from models import Campaign

    campaign = session.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        log.warning(f"Campaign {campaign_id} not found for update")
        return None

    # Update all editable fields
    field_map = {
        "name": "name",
        "target_industries": ("target_industries", True),
        "target_geography": ("target_geography", True),
        "target_company_size": "target_company_size",
        "target_roles": ("target_roles", True),
        "target_verified_emails": "target_verified_emails",
        "search_queries": ("search_queries", True),
        "source_selection": ("source_selection", True),
        "scoring_rules": ("scoring_rules", True),
        "scoring_threshold": "scoring_threshold",
        "exclusion_list": ("exclusion_list", True),
        "our_offering": "our_offering",
        "value_proposition": "value_proposition",
        "pitch_angle": "pitch_angle",
        "research_questions": ("research_questions", True),
    }

    for config_key, mapping in field_map.items():
        if config_key not in config:
            continue
        if isinstance(mapping, tuple):
            db_field, needs_json = mapping
            setattr(campaign, db_field, safe_json_dumps(config[config_key]))
        else:
            setattr(campaign, mapping, config[config_key])

    # Update brief if provided
    if "brief" in config:
        campaign.brief = config["brief"]

    log.info(f"Campaign updated: id={campaign.id}, name='{campaign.name}'")
    return campaign


def soft_delete_campaign(session, campaign_id: int):
    """
    Soft-delete a campaign by setting deleted_at and status=ARCHIVED.
    Does NOT cascade-delete associated data.

    Args:
        session: SQLAlchemy session
        campaign_id: ID of the campaign to delete

    Returns:
        True if deleted, False if not found
    """
    from models import Campaign
    from utils import utcnow

    campaign = session.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        log.warning(f"Campaign {campaign_id} not found for deletion")
        return False

    campaign.status = "ARCHIVED"
    campaign.is_active = 0
    campaign.deleted_at = utcnow()
    log.info(f"Campaign soft-deleted: id={campaign.id}, name='{campaign.name}'")
    return True


if __name__ == "__main__":
    # Quick test
    brief = """
    We are Zansphere, a software solutions company. We want to find EV motorcycle
    startups in India that are importing parts and assembling electric two-wheelers.
    They need custom dashboard software or an embedded OS for their smart instrument clusters.
    """
    config = decompose_brief(brief)
    print(json.dumps(config, indent=2))
