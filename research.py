"""
research.py — Stage 5: Campaign-Aware Research Agent

Uses local Ollama LLM to research qualified companies.
Scrapes company websites via crawl4ai (Playwright) to provide deep context.
Generates structured summaries, identifies pain points, and tech stack.

Campaign-aware: reads campaign.our_offering, campaign.research_questions,
and campaign.pitch_angle to tailor research to the specific campaign angle.

Usage:
    python research.py
    python research.py --dry-run
"""

import json
import sys

from database import get_session, init_db
from models import Company, Signal, Research, Campaign
from openrouter_client import call_llm_with_schema
from web_scraper import scrape_website
from utils import get_logger, safe_json_dumps, safe_json_loads, utcnow

log = get_logger("research")

# Default prompt for legacy (no-campaign) mode
RESEARCH_PROMPT_DEFAULT = """You are a B2B sales research assistant.

Given the following information about a company and the signals we detected,
produce a detailed research brief for a sales rep.

Respond with ONLY a JSON object containing:
- "summary": string (2-3 sentence company overview — what they do, stage, size)
- "pain_points": array of strings (3-5 likely pain points relevant to the outreach angle)
- "tech_stack": array of strings (likely technologies they use)
- "recent_news": string (brief summary of what triggered our attention — the signal)
- "talking_points": array of strings (2-3 personalized talking points for outreach)
- "urgency": string ("high", "medium", or "low" — how urgent is their likely need)
"""


def _build_campaign_prompt(campaign: Campaign) -> str:
    """Build a campaign-specific research prompt."""
    our_offering = campaign.our_offering or "software solutions"
    research_questions = safe_json_loads(campaign.research_questions) or []
    pitch_angle = campaign.pitch_angle or ""

    # Build the research questions section
    questions_text = ""
    if research_questions:
        questions_text = "\n\nSpecific questions to investigate:\n" + "\n".join(
            f"  {i+1}. {q}" for i, q in enumerate(research_questions)
        )

    return f"""You are a B2B sales research assistant working on a targeted outreach campaign.

Our company provides: {our_offering}
Campaign angle: {pitch_angle}

Given the company information and signals below, produce a research brief
that helps a sales rep write a hyper-personalized cold email.
{questions_text}

Focus on:
1. What specific problems this company likely faces that our offering solves
2. Any signals that indicate urgency or buying intent
3. Technology or processes they use that relate to our offering
4. Personalization hooks — recent news, growth, changes

Respond with ONLY a JSON object containing:
- "summary": string (2-3 sentence company overview)
- "pain_points": array of strings (3-5 pain points SPECIFIC to our offering angle)
- "tech_stack": array of strings (technologies they use)
- "recent_news": string (what triggered our attention — the signal/source)
- "talking_points": array of strings (2-3 personalized talking points referencing our offering)
- "urgency": string ("high", "medium", or "low")
- "campaign_fit_notes": string (why this company is a good/bad fit for this specific campaign)
"""


def run(dry_run: bool = False):
    """Research all companies with status EMAIL_VERIFIED."""
    init_db()
    log.info("Starting research agent...")

    with get_session() as session:
        companies = session.query(Company).filter_by(status="EMAIL_VERIFIED").all()
        company_ids = [c.id for c in companies]
        log.info(f"Found {len(companies)} companies to research")

    researched = 0

    for cid in company_ids:
        with get_session() as session:
            company = session.query(Company).filter_by(id=cid).first()
            if not company:
                continue

            log.info(f"Researching: {company.name}")

            if dry_run:
                log.info(f"  [DRY RUN] Would research {company.name}")
                continue

            try:
                # Load campaign-specific prompt
                campaign = None
                if company.campaign_id:
                    campaign = session.query(Campaign).filter_by(id=company.campaign_id).first()

                if campaign:
                    system_prompt = _build_campaign_prompt(campaign)
                    log.info(f"  Using campaign prompt: '{campaign.name}'")
                else:
                    system_prompt = RESEARCH_PROMPT_DEFAULT
                    log.info("  Using default research prompt (no campaign)")

                # Gather all signals for context
                signals = session.query(Signal).filter_by(company_id=company.id).all()
                signals_text = "\n".join(
                    f"- [{s.signal_type}] {s.title or ''} (source: {s.source or ''}, url: {s.raw_url or ''})"
                    for s in signals
                )

                # Pull Prospeo enrichment already stored on the company model
                prospeo_desc = company.description_ai or ""
                prospeo_tech = safe_json_loads(company.tech_stack_json) or []
                prospeo_jobs = safe_json_loads(company.active_job_titles) or []
                prospeo_keywords = safe_json_loads(company.keywords) or []

                # Scrape company website for deep LLM context
                website_content = ""
                if company.website:
                    log.info(f"  Scraping website for context: {company.website}")
                    website_content = scrape_website(company.website)
                    if website_content:
                        log.info(f"  Got {len(website_content)} chars of website context")
                    else:
                        log.info("  No website content scraped")

                # Build Prospeo context block
                prospeo_context = ""
                if prospeo_desc:
                    prospeo_context += f"\nProspeo Company Description: {prospeo_desc}"
                if prospeo_tech:
                    prospeo_context += f"\nProspeo Confirmed Tech Stack: {', '.join(prospeo_tech)}"
                if prospeo_jobs:
                    prospeo_context += f"\nActive Job Posting Titles: {', '.join(prospeo_jobs[:10])}"
                if prospeo_keywords:
                    prospeo_context += f"\nCompany Keywords: {', '.join(prospeo_keywords)}"
                if company.revenue_range:
                    prospeo_context += f"\nRevenue Range: {company.revenue_range}"
                if company.funding_stage:
                    prospeo_context += f"\nFunding Stage: {company.funding_stage}"

                # Extra fields from new sources
                source_context = ""
                if company.signal_source:
                    source_context += f"\nDiscovery Source: {company.signal_source}"
                if company.category:
                    source_context += f"\nBusiness Category: {company.category}"
                if company.address:
                    source_context += f"\nAddress: {company.address}"
                if company.rating:
                    source_context += f"\nRating: {company.rating}"
                if company.phone:
                    source_context += f"\nPhone: {company.phone}"

                user_prompt = f"""Company: {company.name}
Website: {company.website or 'Unknown'}
Industry: {company.industry or 'Unknown'}
Country: {company.country or 'Unknown'}
Employee count: {company.employee_count or 'Unknown'}
LinkedIn: {company.linkedin_url or 'N/A'}
ICP Score: {company.icp_score}
{prospeo_context}
{source_context}

Detected signals:
{signals_text}

{f'Company Website Content (scraped):{chr(10)}{website_content[:8000]}' if website_content else ''}"""

                data = call_llm_with_schema(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    required_keys=["summary", "pain_points"],
                )

                # Upsert research record
                existing = session.query(Research).filter_by(company_id=company.id).first()
                if existing:
                    existing.summary = data.get("summary", "")
                    existing.pain_points = safe_json_dumps(data.get("pain_points", []))
                    existing.tech_stack = safe_json_dumps(data.get("tech_stack", []))
                    existing.recent_news = data.get("recent_news", "")
                    existing.raw_json = safe_json_dumps(data)
                else:
                    research = Research(
                        company_id=company.id,
                        summary=data.get("summary", ""),
                        pain_points=safe_json_dumps(data.get("pain_points", [])),
                        tech_stack=safe_json_dumps(data.get("tech_stack", [])),
                        recent_news=data.get("recent_news", ""),
                        raw_json=safe_json_dumps(data),
                    )
                    session.add(research)

                company.status = "RESEARCH_DONE"
                company.updated_at = utcnow()
                researched += 1

                log.info(f"  [OK] Research complete: {data.get('summary', '')[:80]}...")

            except Exception as e:
                log.error(f"  [FAIL] Research failed for {company.name}: {e}")

    log.info(f"Researched {researched} companies")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Research agent failed: {e}", exc_info=True)
        sys.exit(1)

