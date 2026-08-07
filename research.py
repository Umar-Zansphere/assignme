"""
research.py — Stage 6: Research Agent

Uses OpenRouter LLM to research qualified companies.
Generates structured summaries, identifies pain points, and tech stack.

Usage:
    python research.py
    python research.py --dry-run
"""

import json
import sys

from database import get_session, init_db
from models import Company, Signal, Research
from openrouter_client import call_llm_with_schema
from utils import get_logger, safe_json_dumps, utcnow

log = get_logger("research")

RESEARCH_PROMPT = """You are a B2B sales research assistant specializing in software companies.

Given the following information about a company and the signals we detected,
produce a detailed research brief for a sales rep.

Respond with ONLY a JSON object containing:
- "summary": string (2-3 sentence company overview — what they do, stage, size)
- "pain_points": array of strings (3-5 likely pain points related to QA, testing, software quality, engineering velocity)
- "tech_stack": array of strings (likely technologies they use, inferred from job postings and company profile)
- "recent_news": string (brief summary of what triggered our attention — the signal)
- "talking_points": array of strings (2-3 personalized talking points for outreach)
- "urgency": string ("high", "medium", or "low" — how urgent is their likely need)
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
                # Gather all signals for context
                signals = session.query(Signal).filter_by(company_id=company.id).all()
                signals_text = "\n".join(
                    f"- [{s.signal_type}] {s.title or ''} (source: {s.source or ''}, url: {s.raw_url or ''})"
                    for s in signals
                )

                user_prompt = f"""Company: {company.name}
Website: {company.website or 'Unknown'}
Industry: {company.industry or 'Unknown'}
Country: {company.country or 'Unknown'}
Employee count: {company.employee_count or 'Unknown'}
LinkedIn: {company.linkedin_url or 'N/A'}
GitHub: {company.github_url or 'N/A'}
ICP Score: {company.icp_score}

Detected signals:
{signals_text}
"""

                data = call_llm_with_schema(
                    system_prompt=RESEARCH_PROMPT,
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
