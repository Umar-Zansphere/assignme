"""
scorer.py — Stage 3: ICP Scorer

Applies rule-based scoring to enriched companies.
Qualifies or rejects based on score threshold.

Usage:
    python scorer.py
    python scorer.py --dry-run
"""

import sys

from database import get_session, init_db
from models import Company, Signal
from config import ICP_SCORE_THRESHOLD, ICP_SCORING_RULES
from utils import get_logger, utcnow

log = get_logger("scorer")


def calculate_score(company: Company, signals: list[Signal]) -> tuple[int, list[str]]:
    """
    Calculate ICP score for a company based on rules.

    Returns:
        (total_score, list of matched rules with points)
    """
    score = 0
    reasons = []

    # ── Signal-based scoring ───────────────────────────────
    signal_types = {s.signal_type for s in signals}

    if "JOB_POSTING" in signal_types:
        # Check if hiring engineers specifically
        engineering_keywords = {"engineer", "developer", "software", "backend", "frontend", "devops", "sre", "qa"}
        for s in signals:
            if s.signal_type == "JOB_POSTING" and s.title:
                title_lower = s.title.lower()
                if any(kw in title_lower for kw in engineering_keywords):
                    pts = ICP_SCORING_RULES.get("hiring_engineers", 30)
                    score += pts
                    reasons.append(f"Hiring engineers: +{pts}")
                    break  # Count once

    if "PRODUCT_LAUNCH" in signal_types:
        pts = ICP_SCORING_RULES.get("product_launch", 15)
        score += pts
        reasons.append(f"Product launch: +{pts}")

    if "FUNDING" in signal_types:
        pts = ICP_SCORING_RULES.get("recent_funding", 25)
        score += pts
        reasons.append(f"Recent funding: +{pts}")

    # ── Industry scoring ───────────────────────────────────
    industry = (company.industry or "").lower()
    if "health" in industry:
        pts = ICP_SCORING_RULES.get("healthtech", 20)
        score += pts
        reasons.append(f"HealthTech: +{pts}")
    elif "fin" in industry:
        pts = ICP_SCORING_RULES.get("fintech", 15)
        score += pts
        reasons.append(f"FinTech: +{pts}")

    # ── Geography scoring ──────────────────────────────────
    country = (company.country or "").upper()
    if country in ("USA", "US", "UNITED STATES"):
        pts = ICP_SCORING_RULES.get("usa", 10)
        score += pts
        reasons.append(f"USA: +{pts}")
    elif country in ("UK", "GERMANY", "FRANCE", "NETHERLANDS", "SWEDEN"):
        pts = ICP_SCORING_RULES.get("europe", 5)
        score += pts
        reasons.append(f"Europe: +{pts}")

    # ── Employee count scoring ─────────────────────────────
    emp = company.employee_count or 0
    if 20 <= emp <= 200:
        pts = ICP_SCORING_RULES.get("employee_20_200", 20)
        score += pts
        reasons.append(f"20-200 employees: +{pts}")
    elif 200 < emp <= 1000:
        pts = ICP_SCORING_RULES.get("employee_200_1000", 10)
        score += pts
        reasons.append(f"200-1000 employees: +{pts}")

    return score, reasons


def run(dry_run: bool = False):
    """Score all companies with status ENRICHED."""
    init_db()
    log.info("Starting ICP scoring...")

    with get_session() as session:
        companies = session.query(Company).filter_by(status="ENRICHED").all()
        log.info(f"Found {len(companies)} companies to score")

        qualified = 0
        rejected = 0

        for company in companies:
            signals = session.query(Signal).filter_by(company_id=company.id).all()
            score, reasons = calculate_score(company, signals)

            log.info(f"  {company.name}: score={score} (threshold={ICP_SCORE_THRESHOLD})")
            for r in reasons:
                log.info(f"    {r}")

            if dry_run:
                status = "QUALIFIED" if score >= ICP_SCORE_THRESHOLD else "REJECTED"
                log.info(f"    [DRY RUN] → {status}")
                continue

            company.icp_score = score
            company.updated_at = utcnow()

            if score >= ICP_SCORE_THRESHOLD:
                company.status = "QUALIFIED"
                qualified += 1
            else:
                company.status = "REJECTED"
                rejected += 1

        log.info(f"Scoring complete: {qualified} qualified, {rejected} rejected")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        run(dry_run=dry_run)
    except Exception as e:
        log.error(f"Scorer failed: {e}", exc_info=True)
        sys.exit(1)
