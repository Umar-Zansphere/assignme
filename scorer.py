"""
scorer.py — Stage 2: Campaign-Driven ICP Scorer

Loads scoring rules from the campaign config. Applies dynamic rules based on
what the LLM generated and the user reviewed. Falls back to legacy hardcoded
rules if no campaign config exists.

Usage:
    python scorer.py
    python scorer.py --dry-run
    python scorer.py --rescore
"""

import sys

from database import get_session, init_db
from models import Company, Signal, Campaign
from config import ICP_SCORE_THRESHOLD, ICP_SCORING_RULES
from utils import get_logger, utcnow, safe_json_loads

log = get_logger("scorer")

# Track which contradictory geography rules have already been warned about
# so we don't spam the log with the same message for every company.
_warned_geo_rules: set[str] = set()


def _load_campaign_rules(session, company: Company) -> tuple[list[dict], int, list[str], list[str]]:
    """
    Load scoring rules from the company's campaign.

    Returns:
        (scoring_rules, threshold, exclusion_list, target_geography)
    """
    if company.campaign_id:
        campaign = session.query(Campaign).filter_by(id=company.campaign_id).first()
        if campaign:
            rules = safe_json_loads(campaign.scoring_rules) or []
            threshold = campaign.scoring_threshold or ICP_SCORE_THRESHOLD
            exclusions = safe_json_loads(campaign.exclusion_list) or []
            geography = safe_json_loads(campaign.target_geography) or []
            return rules, threshold, exclusions, geography

    return [], ICP_SCORE_THRESHOLD, [], []


def calculate_score_dynamic(
    company: Company,
    signals: list[Signal],
    rules: list[dict],
    exclusions: list[str],
    target_geography: list[str] | None = None,
) -> tuple[int, list[str]]:
    """
    Calculate ICP score using campaign-defined dynamic rules.

    Rule types:
        - "keyword": matches keywords against company name/industry/description
        - "size": checks employee count range
        - "geography": checks company country
        - "signal": checks what signal source found the company
    """
    score = 0
    reasons = []

    name_lower = (company.name or "").lower()
    industry_lower = (company.industry or "").lower()
    country_lower = (company.country or "").lower()
    description_lower = (company.description_ai or "").lower()
    category_lower = (company.category or "").lower()

    # ── Hard disqualification by exclusion list ──
    all_text = f"{name_lower} {industry_lower} {category_lower}"
    for exclusion in exclusions:
        if exclusion.lower() in all_text:
            score -= 200
            reasons.append(f"DISQUALIFIED (matches exclusion '{exclusion}'): -200")
            return score, reasons

    # ── Automatic geography enforcement ──
    # If campaign has target_geography and the company has a known country,
    # penalize companies outside the target region heavily.
    if target_geography and country_lower:
        geo_lower = {g.lower().strip() for g in target_geography}
        address_lower = (company.address or "").lower()
        all_location = f"{country_lower} {address_lower}"
        if not any(g in all_location for g in geo_lower):
            score -= 100
            reasons.append(f"Outside target geography {target_geography} (country={company.country}): -100")

    # ── Apply dynamic rules ──
    for rule in rules:
        rule_type = rule.get("type", "keyword")
        weight = rule.get("weight", 0)
        rule_desc = rule.get("rule", "Unknown rule")

        if rule_type == "keyword":
            keywords = rule.get("keywords", [])
            if not keywords:
                continue

            # For NEGATIVE rules, only match against company name — NOT industry/category
            # because Google Maps assigns generic categories like "Motor vehicle dealer"
            # to actual manufacturers, which causes mass false disqualifications.
            if weight < 0:
                searchable = f"{name_lower}"
            else:
                # For positive rules, match broadly against all fields
                searchable = f"{name_lower} {industry_lower} {description_lower} {category_lower}"

            matched = False
            matched_kw = None
            for kw in keywords:
                kw_lower = kw.lower().strip()
                # First try exact phrase match
                if kw_lower in searchable:
                    matched = True
                    matched_kw = kw
                    break
                # Then try: if ALL individual words in the keyword appear in the text
                words = kw_lower.split()
                if len(words) > 1 and all(w in searchable for w in words):
                    matched = True
                    matched_kw = kw
                    break

            if matched:
                score += weight
                reasons.append(f"{rule_desc} (matched '{matched_kw}'): {'+' if weight > 0 else ''}{weight}")

        elif rule_type == "size":
            emp = company.employee_count or 0
            min_size = rule.get("min", 0)
            max_size = rule.get("max", 999999)
            if min_size <= emp <= max_size:
                score += weight
                reasons.append(f"{rule_desc} (employees={emp}): {'+' if weight > 0 else ''}{weight}")

        elif rule_type == "geography":
            # Support both 'countries' and 'keywords' — LLM sometimes emits the wrong field
            target_countries = [c.lower() for c in (rule.get("countries") or rule.get("keywords") or [])]

            # Guard against a common LLM mistake: generating a *negative* geography rule
            # that penalises the very country the campaign is already targeting.
            # The automatic geography bonus/penalty logic handles in/out-of-target regions,
            # so a contradictory rule like "not in India" with target_geography=["India"]
            # would unfairly reject all leads.  Skip it and warn.
            if weight < 0 and target_geography:
                geo_lower_set = {g.lower().strip() for g in target_geography}
                contradictory = [c for c in target_countries if c in geo_lower_set]
                if contradictory:
                    if rule_desc not in _warned_geo_rules:
                        _warned_geo_rules.add(rule_desc)
                        log.warning(
                            f"Skipping contradictory negative geography rule '{rule_desc}': "
                            f"it penalises {contradictory} which is in target_geography {target_geography}. "
                            f"The automatic geography logic already handles this."
                        )
                    continue

            if country_lower in target_countries:
                score += weight
                reasons.append(f"{rule_desc} (country={company.country}): {'+' if weight > 0 else ''}{weight}")

        elif rule_type == "signal":
            signal_sources = {s.source for s in signals}
            target_sources = rule.get("sources", [])
            if any(src in signal_sources for src in target_sources):
                score += weight
                reasons.append(f"{rule_desc}: {'+' if weight > 0 else ''}{weight}")

    # ── Automatic geography bonus ──
    # Companies in the target geography get a baseline boost
    if target_geography and country_lower:
        geo_lower = {g.lower().strip() for g in target_geography}
        address_lower = (company.address or "").lower()
        all_location = f"{country_lower} {address_lower}"
        if any(g in all_location for g in geo_lower):
            score += 20
            reasons.append(f"In target geography {target_geography}: +20")

    # ── Multi-source bonus ──
    sources = safe_json_loads(company.signal_sources_json) or []
    if len(sources) >= 3:
        score += 40
        reasons.append(f"Multi-source bonus (found in {len(sources)} sources): +40")
    elif len(sources) >= 2:
        score += 20
        reasons.append(f"Multi-source bonus (found in {len(sources)} sources): +20")

    return score, reasons


def calculate_score_legacy(company: Company, signals: list[Signal]) -> tuple[int, list[str]]:
    """
    Legacy hardcoded scoring — used when no campaign rules exist.
    Preserves backward compatibility with the original QA-focused pipeline.
    """
    score = 0
    reasons = []

    name_lower = (company.name or "").lower()
    industry_lower = (company.industry or "").lower()

    # Hard disqualifications
    _DISQUALIFY_PATTERNS = (
        "university", "college", "school", "hospital", "clinic",
        "government", "church", "nonprofit", "foundation",
        "staffing", "recruiting", "recruiter", "talent solutions",
        "human capital", "manpower", "temp agency",
    )
    for pattern in _DISQUALIFY_PATTERNS:
        if pattern in name_lower or pattern in industry_lower:
            return -200, [f"DISQUALIFIED (contains '{pattern}'): -200"]

    # Signal-based scoring
    signal_types = {s.signal_type for s in signals}
    if "JOB_POSTING" in signal_types:
        pts = ICP_SCORING_RULES.get("hiring_engineers", 30)
        score += pts
        reasons.append(f"Hiring (job posting signal): +{pts}")

    if "FUNDING" in signal_types:
        pts = ICP_SCORING_RULES.get("recent_funding", 25)
        score += pts
        reasons.append(f"Recent funding: +{pts}")

    # Industry scoring
    industry = industry_lower
    if any(kw in industry for kw in ("tech", "software", "ai", "internet")):
        pts = ICP_SCORING_RULES.get("tech_ai", 20)
        score += pts
        reasons.append(f"Tech/AI industry: +{pts}")

    # Geography
    country = (company.country or "").upper()
    if country in ("USA", "US"):
        score += ICP_SCORING_RULES.get("usa", 10)
        reasons.append(f"USA: +{ICP_SCORING_RULES.get('usa', 10)}")

    # Employee count
    emp = company.employee_count or 0
    if 20 <= emp <= 200:
        pts = ICP_SCORING_RULES.get("employee_20_200", 20)
        score += pts
        reasons.append(f"20-200 employees: +{pts}")
    elif emp > 10000:
        pts = ICP_SCORING_RULES.get("employee_10000_plus", -50)
        score += pts
        reasons.append(f">10000 employees: {pts}")

    return score, reasons


def _calibrate_threshold(rules: list[dict], threshold: int, target_geography: list[str]) -> int:
    """
    Sanity-check the threshold against what the rules can realistically score.

    If the threshold exceeds the maximum achievable positive score, clamp it to
    80% of that max so companies can actually qualify. Logs a warning when this
    happens so operators know the campaign rules may need tuning.

    Note: Multi-source bonuses (+20/+40) are NOT included in the max because they
    are rare (most companies come from a single source) and would make the
    calibration too optimistic.
    """
    positive_rule_max = sum(r.get("weight", 0) for r in rules if r.get("weight", 0) > 0)
    # Only count the geography bonus — it's reliable for in-target companies.
    # Multi-source bonuses are excluded as they apply to very few companies.
    auto_bonus = 20 if target_geography else 0
    max_achievable = positive_rule_max + auto_bonus

    if max_achievable > 0 and threshold > max_achievable:
        calibrated = max(1, int(max_achievable * 0.8))
        log.warning(
            f"Threshold {threshold} exceeds max achievable score {max_achievable} "
            f"(positive_rules={positive_rule_max} + geo_bonus={auto_bonus}). "
            f"Auto-calibrating threshold to {calibrated} so companies can qualify. "
            f"Consider lowering the campaign's scoring_threshold."
        )
        return calibrated
    return threshold



def run(dry_run: bool = False, rescore: bool = False, target_campaign_id: int | None = None):
    """Score companies using campaign rules or legacy fallback."""
    init_db()
    mode = "RESCORE ALL" if rescore else "new ENRICHED companies"
    log.info(f"Starting ICP scoring ({mode})...")

    with get_session() as session:
        # Get active campaign IDs
        from utils import get_active_campaign_ids
        active_ids = get_active_campaign_ids(session)
        if target_campaign_id:
            if target_campaign_id not in active_ids:
                log.info(f"Campaign {target_campaign_id} is not active. Skipping.")
                return
            active_ids = [target_campaign_id]
        
        if rescore:
            # Include REJECTED so a re-run can recover companies wrongly disqualified
            companies = session.query(Company).filter(
                Company.status.notin_(["NEW_SIGNAL"]),
                Company.campaign_id.in_(active_ids)
            ).all()
        else:
            companies = session.query(Company).filter(
                Company.status == "ENRICHED",
                Company.campaign_id.in_(active_ids)
            ).all()

        log.info(f"Found {len(companies)} companies to score")

        qualified = 0
        rejected = 0

        # Cache calibrated thresholds per campaign to avoid recalculating per company
        _calibrated_thresholds: dict[int | None, int] = {}

        for company in companies:
            signals = session.query(Signal).filter_by(company_id=company.id).all()

            # Load campaign-specific rules
            rules, threshold, exclusions, geography = _load_campaign_rules(session, company)

            if rules:
                # Calibrate threshold once per campaign
                cid = company.campaign_id
                if cid not in _calibrated_thresholds:
                    _calibrated_thresholds[cid] = _calibrate_threshold(rules, threshold, geography)
                threshold = _calibrated_thresholds[cid]

                # Dynamic campaign-driven scoring
                score, reasons = calculate_score_dynamic(company, signals, rules, exclusions, geography)
                log.info(f"  {company.name}: score={score} (campaign threshold={threshold})")
            else:
                # Legacy fallback
                score, reasons = calculate_score_legacy(company, signals)
                threshold = ICP_SCORE_THRESHOLD
                log.info(f"  {company.name}: score={score} (legacy threshold={threshold})")

            for r in reasons:
                log.info(f"    {r}")

            if dry_run:
                new_status = "QUALIFIED" if score >= threshold else "REJECTED"
                log.info(f"    [DRY RUN] -> {new_status}")
                continue

            company.icp_score = score
            company.updated_at = utcnow()

            if score >= threshold:
                # Allow ENRICHED and REJECTED companies to become QUALIFIED.
                # Do not touch companies already in downstream states (EMAILED, REPLIED, etc.)
                if company.status in ("ENRICHED", "REJECTED"):
                    company.status = "QUALIFIED"
                qualified += 1
            else:
                company.status = "REJECTED"
                rejected += 1

        log.info(f"Scoring complete: {qualified} qualified, {rejected} rejected")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    rescore = "--rescore" in sys.argv
    target_campaign_id = None
    for arg in sys.argv:
        if arg.startswith("--campaign-id="):
            target_campaign_id = int(arg.split("=")[1])
            
    try:
        run(dry_run=dry_run, rescore=rescore, target_campaign_id=target_campaign_id)
    except Exception as e:
        log.error(f"Scorer failed: {e}", exc_info=True)
        sys.exit(1)

