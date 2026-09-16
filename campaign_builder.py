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
  
  "scoring_rules": [
    {{"rule": "Description of positive signal", "weight": 30, "type": "keyword", "keywords": ["keyword1", "keyword2"]}},
    {{"rule": "Description of negative signal", "weight": -50, "type": "keyword", "keywords": ["keyword1"]}}
  ],
  "scoring_threshold": 60,
  "exclusion_list": ["Industry or type to exclude 1", "Industry 2"],
  
  "our_offering": "What the sender's company provides (inferred from brief)",
  "value_proposition": "Why the target would care about this offering",
  "pitch_angle": "How to frame the cold outreach email — what hook to use",
  "research_questions": [
    "Question to investigate about each company during research stage",
    "Another question specific to this campaign's angle"
  ]
}}

CRITICAL RULES:
- ONLY include sources marked ✅ AVAILABLE in source_selection. NEVER use ❌ sources.
- google_maps is MANDATORY for every campaign. It is the primary company discovery tool.
- searxng is MANDATORY for every campaign. It supplements google_maps with web data.
- linkedin should ONLY be added if the brief specifically asks about hiring activity or job postings. Do NOT use linkedin for general company discovery.
- Only add search_queries for sources you included in source_selection.
- search_queries for google_maps should include the target geography in the query string (e.g., "EV startups India", not just "EV startups").

For scoring_rules, "type" can be:
- "keyword" — matches keywords against company name, industry, description
- "size" — checks employee count range  
- "geography" — checks company country
- "signal" — checks what signal/source found the company

IMPORTANT for keyword rules: Include a MIX of single words AND short phrases.
Single words like "ev", "battery", "solar" match more broadly.
Phrases like "electric vehicle" are more precise but may miss partial matches.
Always include the core single-word terms alongside any phrases.
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


if __name__ == "__main__":
    # Quick test
    brief = """
    We are Zansphere, a software solutions company. We want to find EV motorcycle
    startups in India that are importing parts and assembling electric two-wheelers.
    They need custom dashboard software or an embedded OS for their smart instrument clusters.
    """
    config = decompose_brief(brief)
    print(json.dumps(config, indent=2))
