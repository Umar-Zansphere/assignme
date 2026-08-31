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

    # ── Hard disqualifications ─────────────────────────────
    # These org types will never buy B2B software QA services
    name_lower = (company.name or "").lower()
    industry_lower = (company.industry or "").lower()

    _DISQUALIFY_NAME_PATTERNS = (
        # Education
        "university", "college", "school", "institute", "academy",
        # Healthcare / Gov / Non-profit
        "hospital", "clinic", "health system", "medical center",
        "government", "department of ", "ministry of ",
        "church", "nonprofit", "foundation", "charity",
        # Staffing & recruiting — they post jobs but never buy QA tools
        "staffing", "recruiting", "recruiter", "talent solutions",
        "talent acquisition", "workforce solutions", "manpower",
        "temp agency", "placement agency", " hcm",  # e.g. "Cypress HCM"
        "human capital",
    )
    _DISQUALIFY_INDUSTRY_PATTERNS = (
        # Education / Gov / Non-profit
        "education", "higher education", "primary/secondary education",
        "hospital", "health care", "government administration",
        "non-profit", "nonprofit", "religious institutions",
        "military", "judiciary",
        # Staffing / HR — post tons of jobs, never QA buyers
        "staffing and recruiting", "staffing & recruiting",
        "human resources", "outsourcing/offshoring",
        "executive search",
    )

    for pattern in _DISQUALIFY_NAME_PATTERNS:
        if pattern in name_lower:
            score -= 200
            reasons.append(f"DISQUALIFIED (name contains '{pattern}'): -200")
            return score, reasons  # Early exit — no point scoring further

    for pattern in _DISQUALIFY_INDUSTRY_PATTERNS:
        if pattern in industry_lower:
            score -= 200
            reasons.append(f"DISQUALIFIED (industry='{company.industry}'): -200")
            return score, reasons



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

    if "NEWS" in signal_types:
        pts = ICP_SCORING_RULES.get("company_news", 15)
        score += pts
        reasons.append(f"Company news signal: +{pts}")

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
    elif any(kw in industry for kw in ("ai", "artificial intelligence", "tech", "software", "internet", "social media", "dating", "automotive")):
        pts = ICP_SCORING_RULES.get("tech_ai", 20)
        score += pts
        reasons.append(f"Tech/AI/Software industry: +{pts}")

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
    elif emp > 10000:
        pts = ICP_SCORING_RULES.get("employee_10000_plus", -50)
        score += pts
        reasons.append(f">10000 employees: {pts} (Enterprise Penalty)")

    return score, reasons


def run(dry_run: bool = False, rescore: bool = False):
    """
    Score companies.

    Args:
        dry_run:  Print what would happen without writing to DB.
        rescore:  If True, re-evaluate ALL companies (any status),
                  not just ENRICHED ones. Useful after adding new
                  disqualification rules.
    """
    init_db()
    mode = "RESCORE ALL" if rescore else "new ENRICHED companies"
    log.info(f"Starting ICP scoring ({mode})...")

    # Statuses that indicate the company is already downstream in the pipeline
    _DOWNSTREAM_STATUSES = {
        "QUALIFIED", "CONTACT_FOUND", "EMAIL_VERIFIED",
        "RESEARCH_DONE", "EMAIL_READY", "EMAIL_SENT", "REPLIED",
    }

    with get_session() as session:
        if rescore:
            # Re-score everything except NEW_SIGNAL (no data yet) and REJECTED
            companies = session.query(Company).filter(
                Company.status.notin_(["NEW_SIGNAL", "REJECTED"])
            ).all()
        else:
            companies = session.query(Company).filter_by(status="ENRICHED").all()

        log.info(f"Found {len(companies)} companies to score")

        qualified = 0
        rejected = 0
        reverted = 0

        for company in companies:
            signals = session.query(Signal).filter_by(company_id=company.id).all()
            score, reasons = calculate_score(company, signals)

            log.info(f"  {company.name}: score={score} (threshold={ICP_SCORE_THRESHOLD}, current={company.status})")
            for r in reasons:
                log.info(f"    {r}")

            if dry_run:
                new_status = "QUALIFIED" if score >= ICP_SCORE_THRESHOLD else "REJECTED"
                log.info(f"    [DRY RUN] -> {new_status}")
                continue

            company.icp_score = score
            company.updated_at = utcnow()

            if score >= ICP_SCORE_THRESHOLD:
                # Only update status if currently ENRICHED (don't demote downstream)
                if company.status == "ENRICHED":
                    company.status = "QUALIFIED"
                    qualified += 1
                else:
                    qualified += 1  # Already qualified or further — leave it
            else:
                was_downstream = company.status in _DOWNSTREAM_STATUSES
                company.status = "REJECTED"
                rejected += 1
                if was_downstream:
                    reverted += 1
                    log.warning(
                        f"  !! Reverted '{company.name}' from downstream pipeline "
                        f"(was {company.status!r}) — now REJECTED"
                    )

        summary = f"Scoring complete: {qualified} qualified, {rejected} rejected"
        if rescore:
            summary += f" ({reverted} reverted from downstream pipeline)"
        log.info(summary)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    rescore = "--rescore" in sys.argv
    try:
        run(dry_run=dry_run, rescore=rescore)
    except Exception as e:
        log.error(f"Scorer failed: {e}", exc_info=True)
        sys.exit(1)
