from __future__ import annotations

import re
from collections import defaultdict

from taxonomy import CANONICAL_SKILL_MAP
from utils import dedupe_keep_order, flatten_text, month_diff, normalize_text, parse_date

CLIENT_WORDS = ["client", "customer", "stakeholder", "business partner", "fund manager", "brand manager"]
INTERNATIONAL_WORDS = ["onsite", "global", "london", "singapore", "dubai", "san francisco", "remote", "europe", "us", "usa", "uk"]
LEADERSHIP_WORDS = ["led", "owned", "introduced", "built team", "managed team", "new initiative", "principal", "lead", "architected", "stakeholder management"]
COMPLEXITY_WORDS = ["end-to-end", "architecture", "production", "scalable", "optimization", "cross-functional", "distributed", "real-time", "embedded", "multi-agent", "autonomy", "system design"]
PROBLEM_SOLVING_WORDS = ["solved", "improved", "optimized", "reduced", "launched", "introduced", "built", "designed", "automated", "fixed"]
SUPPORT_WORDS = ["support", "incident", "ticket", "bug", "l2", "l3", "runbook"]
MAINTENANCE_WORDS = ["maintenance", "enhancement", "stabilization", "sustain", "supporting existing"]
DEVELOPMENT_WORDS = ["build", "develop", "design", "implement", "launch", "deploy", "create", "architect"]
POC_WORDS = ["poc", "proof of concept", "prototype", "pilot"]
MIGRATION_WORDS = ["migration", "migrated", "modernization", "lift and shift", "cloud migration"]
CONSULTING_HINTS = ["consulting", "services", "analytics", "advisory", "solutions"]
PRODUCT_HINTS = ["product", "platform", "labs", "consumer", "saas"]
DOMAIN_HINTS = {
    "e-commerce": "ECOMMERCE",
    "ecommerce": "ECOMMERCE",
    "retail": "RETAIL",
    "healthcare": "HEALTHCARE",
    "pharma": "HEALTHCARE",
    "pharmaceutical": "HEALTHCARE",
    "bank": "BFSI",
    "finance": "BFSI",
    "insurance": "BFSI",
    "fintech": "BFSI",
    "supply chain": "SUPPLY_CHAIN",
    "manufacturing": "MANUFACTURING",
    "iot": "IOT",
    "telecom": "TELECOM",
    "logistics": "SUPPLY_CHAIN",
    "edtech": "EDTECH",
    "media": "MEDIA",
    "gaming": "GAMING",
    "aerospace": "AEROSPACE",
    "energy": "ENERGY",
    "real estate": "REAL_ESTATE",
    "proptech": "REAL_ESTATE",
}
IMPACT_PATTERN = r"\b\d+(?:\.\d+)?%|\$\s?\d+[\d,]*(?:\.\d+)?(?:\s?(?:million|mn|m|k|cr|crore|lakhs))?|\b\d+(?:\.\d+)?\s?(?:million|mn|m|k|cr|crore|lakhs)\b|\b\d+x\b"
VERBAL_IMPACT_PATTERN = r"\b(?:doubled|tripled|halved|cut\s+\w+\s+by|reduced\s+\w+\s+by\s+half|2x(?:\s+faster)?|3x(?:\s+faster)?|10[×x](?:\s+faster)?|10x(?:\s+faster)?|halved)\b"


def _items(resume_data):
    out = []
    for key in ["experience", "work_experience", "professional_experience", "employment"]:
        val = resume_data.get(key)
        if isinstance(val, list):
            out.extend([v for v in val if isinstance(v, dict)])
    return out


def _normalize_skill(value: str) -> str:
    key = normalize_text(value).lower()
    return CANONICAL_SKILL_MAP.get(key, (normalize_text(value), None))[0]


def _extract_skills(item: dict, ctx: str) -> list[str]:
    explicit = item.get("skills")
    skills = []
    if isinstance(explicit, list):
        skills.extend(str(skill) for skill in explicit if skill)
    for alias, (canonical, _) in CANONICAL_SKILL_MAP.items():
        if alias in ctx:
            skills.append(canonical)
    return dedupe_keep_order([_normalize_skill(skill) for skill in skills if normalize_text(skill)])


def _project_type(text: str) -> str:
    low = text.lower()
    if any(word in low for word in MIGRATION_WORDS):
        return "MIGRATION"
    if any(word in low for word in POC_WORDS):
        return "POC"
    if any(word in low for word in MAINTENANCE_WORDS):
        return "MAINTENANCE"
    if any(word in low for word in SUPPORT_WORDS):
        return "SUPPORT"
    if any(word in low for word in DEVELOPMENT_WORDS):
        return "DEVELOPMENT"
    return "UNKNOWN"


def _company_profile(company: str, text: str) -> dict:
    low = f"{company} {text}".lower()
    operating_model = "HYBRID"
    if any(word in low for word in CONSULTING_HINTS):
        operating_model = "CONSULTING"
    elif any(word in low for word in PRODUCT_HINTS):
        operating_model = "PRODUCT"
    size = "UNKNOWN"
    if any(word in low for word in {"global", "mnc", "fortune", "enterprise", "listed", "publicly traded"}):
        size = "LARGE"
    elif any(word in low for word in {"series b", "series c", "series d", "mid-size", "50-500"}):
        size = "MEDIUM"
    elif any(word in low for word in {"startup", "seed", "series a", "early stage", "pre-series"}):
        size = "STARTUP"
    domain = "UNKNOWN"
    for token, label in DOMAIN_HINTS.items():
        if token in low:
            domain = label
            break
    return {"company": company or "UNKNOWN", "operating_model": operating_model, "size": size, "domain": domain}


def _location_bucket(locations: list[str], relocation_signal: bool) -> dict:
    cleaned = dedupe_keep_order([normalize_text(location) for location in locations if normalize_text(location)])
    distinct = len(cleaned)
    label = "LOW"
    if distinct >= 3 or relocation_signal:
        label = "HIGH"
    elif distinct >= 2:
        label = "MEDIUM"
    return {
        "mobility_signal": label,
        "distinct_locations": cleaned,
    }


def _loyalty_bucket(tenures: list[int]) -> dict:
    if not tenures:
        return {"loyalty_signal": "UNKNOWN", "average_tenure_months": 0}
    average = round(sum(tenures) / max(len(tenures), 1), 1)
    label = "LOW"
    if average >= 30:
        label = "HIGH"
    elif average >= 18:
        label = "MEDIUM"
    return {"loyalty_signal": label, "average_tenure_months": average}


def _yearly_skill_learning(skill_history: dict[int, set[str]]) -> list[dict]:
    if not skill_history:
        return []
    seen = set()
    rows = []
    for year in sorted(skill_history):
        current = skill_history[year]
        new_skills = sorted(current - seen)
        seen.update(current)
        rows.append(
            {
                "year": year,
                "new_skills": new_skills[:12],
                "new_skill_count": len(new_skills),
                "active_skill_count": len(current),
            }
        )
    return rows


def _company_skill_alignment(role_skills: list[str], project_type: str, profile: dict) -> dict:
    alignment = "GENERALIST"
    if project_type in {"DEVELOPMENT", "MIGRATION"} and any(skill in role_skills for skill in {"PySpark", "Spark", "ETL", "Data Pipelines", "SQL"}):
        alignment = "DATA_PLATFORM"
    elif any(skill in role_skills for skill in {"Power BI", "Dashboarding", "Tableau", "Excel"}):
        alignment = "ANALYTICS_BI"
    elif any(skill in role_skills for skill in {"Python", "Forecasting", "Recommendation Systems", "Churn Modeling", "Classification"}):
        alignment = "ML_ANALYTICS"
    if profile.get("operating_model") == "CONSULTING":
        alignment = f"{alignment}_CONSULTING"
    elif profile.get("operating_model") == "PRODUCT":
        alignment = f"{alignment}_PRODUCT"
    return {
        "alignment": alignment,
        "skills": role_skills[:8],
    }


_SENIORITY_LEVELS = {
    "vp": 6, "vice president": 6, "svp": 6, "evp": 6,
    "director": 5, "head of": 5,
    "manager": 4, "engineering manager": 4,
    "lead": 3, "tech lead": 3, "team lead": 3, "principal": 3,
    "senior": 2, "sr.": 2,
}


def _title_seniority(title: str) -> int:
    low = title.lower()
    for label, level in _SENIORITY_LEVELS.items():
        if label in low:
            return level
    return 1  # IC / junior


def _compute_stability_score(tenures: list[int], titles: list[str]) -> float:
    """Tenure-band stability score on [1, 5].

    Bands from average tenure, then apply recency-weighted short-stint penalties
    and a job-hopping rate penalty for high-volume role changers.
    """
    if not tenures:
        return 3.0

    n = len(tenures)
    avg = sum(tenures) / n

    # Base score from average tenure
    if avg >= 36:
        score = 5.0   # 3+ year avg — strong retention
    elif avg >= 24:
        score = 4.0   # 2–3 year avg — solid
    elif avg >= 18:
        score = 3.5   # 18–24m avg — reasonable
    elif avg >= 12:
        score = 3.0   # 1–18m avg — neutral
    elif avg >= 8:
        score = 2.0   # 8–12m avg — concerning
    else:
        score = 1.5   # <8m avg — churn risk

    # Recency-weighted short-stint penalties (index 0 = most recent in reverse-chron)
    for i, months in enumerate(tenures):
        weight = 1.5 if i == 0 else (1.0 if i == 1 else 0.5)
        if months < 6:
            score -= 0.8 * weight
        elif months < 12:
            score -= 0.4 * weight

    # Job-hopping rate penalty (>2 roles/year across career)
    total = sum(tenures)
    if n >= 4 and total > 0:
        rate = n / (total / 12.0)
        if rate > 2.0:
            score -= 0.5

    # Upward-title bonus (max +0.5)
    upward_words = {"senior", "lead", "manager", "head", "director", "vp", "principal", "architect", "chief"}
    upward_count = sum(1 for t in titles if any(w in t.lower() for w in upward_words))
    score += min(upward_count * 0.25, 0.5)

    return round(max(1.0, min(5.0, score)), 1)


def _score_sequence(seq: list[int]) -> int:
    if len(seq) >= 3:
        diffs = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
        if all(d >= 0 for d in diffs) and sum(d > 0 for d in diffs) >= 2:
            return 5
        if sum(d > 0 for d in diffs) >= 1 and sum(d < 0 for d in diffs) <= 1:
            return 4
    if seq[-1] > seq[0]:
        return 3
    if max(seq) - min(seq) <= 1:
        return 2  # Flat
    return 1  # Declining


def _career_trajectory_score(titles: list[str], role_complexity: list[int] | None = None) -> int:
    """Score career trajectory 1-5 based on seniority progression.
    Resumes are typically reverse-chronological (newest first), so we also
    evaluate the reversed sequence to detect upward careers.

    Title text alone misses genuine growth when someone's job title stays
    the same across employers (e.g. "Data Scientist" throughout, a common
    pattern for IC track hires) while the actual scope/complexity of their
    work clearly escalates. role_complexity -- a per-role count of
    COMPLEXITY_WORDS/PROBLEM_SOLVING_WORDS hits, same chronological order as
    titles -- is used as a secondary signal: a title-flat sequence with a
    clear complexity uptrend is upgraded from "Flat" rather than scored as
    if nothing changed. This can only raise a flat/declining title-based
    verdict, never lower a genuine title-based promotion -- conservative by
    design, since title progression is still the stronger signal when it
    exists.
    """
    if len(titles) < 2:
        return 3  # Not enough data
    levels = [_title_seniority(t) for t in titles]
    fwd = _score_sequence(levels)
    rev = _score_sequence(list(reversed(levels)))
    title_score = max(fwd, rev)

    if title_score == 2 and role_complexity and len(role_complexity) == len(titles) and len(role_complexity) >= 2:
        c_fwd = _score_sequence(role_complexity)
        c_rev = _score_sequence(list(reversed(role_complexity)))
        if max(c_fwd, c_rev) >= 4:
            return 3  # Title stayed flat, but scope/complexity clearly grew.

    return title_score


def analyze_experience(resume_data):
    items = _items(resume_data)
    total_months = 0
    titles = []
    companies = []
    progression = False
    client_facing = False
    international = False
    impacts = []
    verbal_impacts = []
    complexity = 0
    leadership = 0
    decision = False
    fast = False
    project_types = []
    problem_solving = 0
    ownership_signals = 0
    company_profiles = []
    domain_tags = []
    locations = []
    tenures = []
    skill_history = defaultdict(set)
    company_skill_summary = []
    previous_company = None
    previous_title = None
    same_company_growth = False
    complexity_by_role: list[int] = []

    for item in items:
        title = normalize_text(item.get("title") or item.get("role"))
        company = normalize_text(item.get("company") or item.get("organization"))
        titles.append(title)
        companies.append(company)
        ctx = flatten_text(item).lower()
        start = parse_date(item.get("start_date") or item.get("from"))
        end = parse_date(item.get("end_date") or item.get("to"))
        role_months = month_diff(start, end)
        total_months += role_months
        tenures.append(role_months)
        location = normalize_text(item.get("location") or item.get("company_location"))
        if location:
            locations.append(location)
        if any(w in ctx for w in CLIENT_WORDS):
            client_facing = True
        if any(w in ctx for w in INTERNATIONAL_WORDS):
            international = True
        if any(w in ctx for w in LEADERSHIP_WORDS) or any(w in title.lower() for w in {"lead", "manager", "principal", "architect", "head"}):
            leadership += 1
            ownership_signals += 1
            decision = True
        role_complexity_count = sum(1 for w in COMPLEXITY_WORDS if w in ctx) + sum(1 for w in PROBLEM_SOLVING_WORDS if w in ctx)
        complexity += sum(1 for w in COMPLEXITY_WORDS if w in ctx)
        problem_solving += sum(1 for w in PROBLEM_SOLVING_WORDS if w in ctx)
        complexity_by_role.append(role_complexity_count)
        impacts.extend(re.findall(IMPACT_PATTERN, ctx, flags=re.IGNORECASE))
        verbal_impacts.extend(re.findall(VERBAL_IMPACT_PATTERN, ctx, flags=re.IGNORECASE))
        project_type = _project_type(ctx)
        role_skills = _extract_skills(item, ctx)
        if start:
            skill_history[start.year].update(role_skills)
        project_types.append(
            {
                "company": company,
                "title": title,
                "project_type": project_type,
                "start_date": item.get("start_date") or item.get("from"),
                "end_date": item.get("end_date") or item.get("to"),
                "skills": role_skills[:8],
                "description": str(item.get("description") or item.get("role_description") or ""),
            }
        )
        profile = _company_profile(company, ctx)
        company_profiles.append(profile)
        company_skill_summary.append(
            {
                "company": company,
                "title": title,
                "project_type": project_type,
                **_company_skill_alignment(role_skills, project_type, profile),
            }
        )
        if profile["domain"] != "UNKNOWN":
            domain_tags.append(profile["domain"])
        if previous_company and company and company == previous_company and previous_title and previous_title != title:
            same_company_growth = True
        previous_company = company
        previous_title = title

    if len(titles) >= 2:
        progression = same_company_growth or len(set(titles)) > 1 or len(set(companies)) > 1
    yearly_learning = _yearly_skill_learning(skill_history)
    if len(yearly_learning) >= 2:
        fast = sum(1 for row in yearly_learning if row.get("new_skill_count", 0) >= 2) >= 2
    years = round(total_months / 12, 1) if total_months else 0.0
    operating_models = [profile["operating_model"] for profile in company_profiles if profile["operating_model"] != "HYBRID"]
    dominant_operating_model = operating_models[0] if operating_models else "HYBRID"
    relocation_signal = international or "remote" in flatten_text(resume_data).lower() or "relocation" in flatten_text(resume_data).lower()
    mobility = _location_bucket(locations, relocation_signal)
    loyalty = _loyalty_bucket([months for months in tenures if months > 0])
    stability = _compute_stability_score(tenures, titles)
    trajectory = _career_trajectory_score(titles, complexity_by_role)
    # Build tenure_with_dates for rubric_engine career break detection
    tenure_with_dates = []
    for item in items:
        company = normalize_text(item.get("company") or item.get("organization"))
        start_raw = item.get("start_date") or item.get("from") or ""
        end_raw = item.get("end_date") or item.get("to") or ""
        start_dt = parse_date(start_raw)
        end_dt = parse_date(end_raw)
        months = month_diff(start_dt, end_dt)
        tenure_with_dates.append({
            "start": start_dt.strftime("%Y-%m") if start_dt else "",
            "end": end_dt.strftime("%Y-%m") if end_dt else "",
            "company": company or "",
            "months": months,
        })
    return {
        "total_experience_years": years,
        "titles": titles,
        "companies": companies,
        "progression": progression,
        "same_company_growth": same_company_growth,
        "client_facing": client_facing,
        "international_exposure": international,
        "business_impacts": impacts[:10],
        "impact_count": len(impacts) + len(verbal_impacts),
        "has_verbal_impacts": len(verbal_impacts) > 0,
        "project_types": project_types[:8],
        "complexity_signal_score": min(complexity, 12),
        "leadership_signal_score": leadership,
        "ownership_signal_score": ownership_signals,
        "problem_solving_signal_score": min(problem_solving, 12),
        "decision_maker": decision if years >= 6 else False,
        "fast_learner": fast,
        "stability_score": stability,
        "tenures": tenures,
        "career_trajectory_score": trajectory,
        "company_profiles": company_profiles[:8],
        "domain_tags": sorted(set(domain_tags)),
        "dominant_operating_model": dominant_operating_model,
        "relocation_flexibility_signal": relocation_signal,
        "mobility_signal": mobility["mobility_signal"],
        "distinct_locations": mobility["distinct_locations"],
        "loyalty_signal": loyalty["loyalty_signal"],
        "average_tenure_months": loyalty["average_tenure_months"],
        "yearly_skill_learning": yearly_learning,
        "company_skill_alignment": company_skill_summary[:8],
        "company_project_combinations": [
            f"{entry['company']} | {entry['project_type']}"
            for entry in project_types[:8]
            if entry.get("company") or entry.get("project_type")
        ],
        "tenure_with_dates": tenure_with_dates,
    }
