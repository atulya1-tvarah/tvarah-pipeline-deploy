from __future__ import annotations
import json
from statistics import mean
from .helpers import norm_skill, norm_text, parse_years_from_string
from .alignment import (
    extract_resume_skills, extract_jd_mandatory_skills, extract_jd_optional_skills,
    estimate_resume_experience_years, extract_resume_education_blob,
    extract_resume_company_blob, most_recent_job_title, extract_resume_domain_set,
    extract_jd_job_level, extract_jd_work_mode, extract_jd_location,
    extract_jd_industry_domains, extract_candidate_location, infer_candidate_level,
    JD_LEVEL_MAP,
)
from .integrity import evaluate_integrity
from .models import HiringManagerInputs, MatchFlags, SkillMatchDetails, ClientWeightedBreakdown, TopTiles, TileReasons, QuickView
from .semantic import build_semantic_skill_analysis
from .ontology import canonicalize_skill, find_adjacent_matches, ROLE_DNA_KEYWORDS

ROLE_TEMPLATES = {
    "data_engineer": ["spark","kafka","etl","databricks","delta lake","airflow"],
    "data_scientist": ["machine learning","statistics","scikit-learn","pandas","numpy","hypothesis testing"],
    "analyst": ["power bi","tableau","sql","dashboard","analytics","reporting"],
    "agentic_ai": ["llm","rag","langchain","langgraph","agent","prompt","vector","embedding"],
    "bi_leader": ["bi","business intelligence","power bi","tableau","qlik","kpi","delivery"],
}

def experience_gap_components(years, jd_min):
    if years is None or not isinstance(jd_min, int):
        return {
            "signed_years": 0.0,
            "signed_months": 0,
            "display": "Experience requirement not comparable",
            "status": "unknown",
        }
    signed_months = int(round((years - jd_min) * 12))
    signed_years = round(signed_months / 12.0, 2)
    abs_months = abs(signed_months)
    whole_years = abs_months // 12
    rem_months = abs_months % 12
    pieces = []
    if whole_years:
        pieces.append(f"{whole_years} year" + ("s" if whole_years != 1 else ""))
    if rem_months:
        pieces.append(f"{rem_months} month" + ("s" if rem_months != 1 else ""))
    if not pieces:
        pieces.append("0 months")
    amount = " ".join(pieces)
    if signed_months > 0:
        display = f"Candidate exceeds baseline by {amount}"
        status = "above"
    elif signed_months < 0:
        display = f"Candidate is below baseline by {amount}"
        status = "below"
    else:
        display = "Candidate matches the experience baseline"
        status = "matched"
    return {
        "signed_years": signed_years,
        "signed_months": signed_months,
        "display": display,
        "status": status,
    }


def _resume_root(resume_json: dict) -> dict:
    root = resume_json.get("resume_data", resume_json)
    if isinstance(root, dict) and "insight_info" in root:
        merged = {}
        if isinstance(root.get("basic_info"), dict):
            merged.update(root.get("basic_info") or {})
        if isinstance(root.get("insight_info"), dict):
            merged.update(root.get("insight_info") or {})
        for k, v in root.items():
            if k not in {"basic_info", "insight_info"}:
                merged.setdefault(k, v)
        return merged
    return root


def _jd_root(jd_json: dict) -> dict:
    return jd_json.get("jd_data", jd_json)


def detect_role(jd_json: dict) -> str:
    jd_root = _jd_root(jd_json)
    text = json.dumps(jd_root, ensure_ascii=False).lower()
    scores = {role: sum(1 for kw in kws if kw in text) for role, kws in ROLE_TEMPLATES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "generic"


def classify_experience_band(years: float | None) -> str:
    if years is None:
        return "unknown"
    if years < 6:
        return "0-6"
    if years < 10:
        return "6-10"
    return "10+"


def classify_jd_band(jd_json: dict) -> str:
    years = _jd_root(jd_json).get("min_years_experience")
    if not isinstance(years, int):
        return "unknown"
    return classify_experience_band(float(years))


def score_jd_alignment(resume_skills, jd_mand, jd_opt):
    matched_m = sorted(jd_mand.intersection(resume_skills))
    adjacent = sorted(s for s in jd_mand.difference(resume_skills) if find_adjacent_matches(s, resume_skills))
    missing_m = sorted(jd_mand.difference(resume_skills).difference(adjacent))
    matched_o = sorted(jd_opt.intersection(resume_skills))
    missing_o = sorted(jd_opt.difference(resume_skills))
    bonus = sorted(resume_skills.difference(jd_mand.union(jd_opt)))
    mand_exact = len(matched_m) / max(1, len(jd_mand)) if jd_mand else 0.6
    mand_adj = len(adjacent) / max(1, len(jd_mand)) if jd_mand else 0.0
    opt_cov = len(matched_o) / max(1, len(jd_opt)) if jd_opt else 0.5
    score = int(round(100 * (0.65 * mand_exact + 0.15 * mand_adj + 0.12 * opt_cov + 0.08 * min(1.0, len(bonus) / 20.0))))
    return score, SkillMatchDetails(
        matched_mandatory=matched_m, missing_mandatory=missing_m, adjacent_mandatory=adjacent,
        matched_optional=matched_o, missing_optional=missing_o, bonus_skills=bonus[:20]
    )


def score_skill_recency(semantic_analysis):
    if not semantic_analysis:
        return 0
    vals = []
    for v in semantic_analysis.values():
        base = 90 if v.recency_label == 'recent' else 65 if v.recency_label == 'moderate' else 35 if v.recency_label == 'old' else 40
        if v.depth == 'expert':
            base += 10
        elif v.depth == 'applied':
            base += 5
        vals.append(min(100, base))
    return int(round(mean(vals)))


def score_domain(resume_json, hmi, detected_role):
    domain_set = extract_resume_domain_set(resume_json)
    base = 35
    if detected_role in {"analyst", "bi_leader"} and any(x in domain_set for x in {"business intelligence", "retail analytics", "sales analytics", "marketing analytics", "data analysis"}):
        base += 25
    if len(domain_set) >= 4:
        base += 10
    if hmi.config.skills.domainSpecific:
        matches = sum(1 for x in hmi.config.skills.domainSpecific if norm_skill(x) in domain_set)
        base += min(20, matches * 8)
    return max(0, min(100, base))


def score_qualitative(hmi):
    if not hmi.rubric:
        return 60
    weighted = sum(item.weight * item.score_1_to_5 for item in hmi.rubric)
    max_possible = sum(item.weight * 5 for item in hmi.rubric) or 1.0
    return int(round(100 * weighted / max_possible))


def score_education_pedigree(resume_json, hmi):
    blob = extract_resume_education_blob(resume_json)
    score = 35
    reason = "Education details are limited."
    if hmi.config.education_rules.minimum_degree and hmi.config.education_rules.minimum_degree in blob:
        score += 20
        reason = f"Meets minimum degree expectation: {hmi.config.education_rules.minimum_degree}."
    if any(x in blob for x in hmi.config.education_rules.preferred_degrees):
        score += 15
        reason += " Field of study is aligned."
    if any(x in blob for x in hmi.config.education_rules.tier_1_keywords):
        score += 25
        reason += " Tier-1 institution signal detected."
    elif any(x in blob for x in hmi.config.education_rules.tier_2_keywords):
        score += 15
        reason += " Tier-2 institution signal detected."
    return min(100, score), reason


def score_company_pedigree(resume_json, hmi):
    blob = extract_resume_company_blob(resume_json)
    score = 40
    reason = "Company pedigree appears neutral."
    if any(x in blob for x in hmi.config.company_rules.fortune_500_companies):
        score += 30
        reason = "Fortune 500 / global enterprise signal detected."
    elif any(x in blob for x in hmi.config.company_rules.top_mncs):
        score += 20
        reason = "Top MNC signal detected."
    elif any(x in blob for x in hmi.config.company_rules.strong_startups):
        score += 18
        reason = "Strong startup signal detected."
    return min(100, score), reason


def compute_problem_solving_score(resume_json):
    text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    score = 45
    for kw in ["optimized", "identified", "root cause", "anomaly", "segmentation", "experimentation", "ab testing", "hypothesis"]:
        if kw in text:
            score += 6
    return min(100, score)


def compute_ownership_score(resume_json):
    text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    score = 35
    for kw in ["owned", "led", "managed", "designed", "partnered", "translated"]:
        if kw in text:
            score += 8
    return min(100, score)


def compute_communication_score(resume_json):
    text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    score = 40
    for kw in ["stakeholder", "leadership", "cross-functional", "presentation", "reviews"]:
        if kw in text:
            score += 8
    return min(100, score)


def compute_scale_complexity_score(resume_json):
    text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    score = 35
    for kw in ["large", "multi-source", "cross-functional", "complex", "enterprise", "client"]:
        if kw in text:
            score += 8
    return min(100, score)


def compute_evidence_strength(semantic_analysis, integrity_score):
    if not semantic_analysis:
        return 0
    vals = []
    for v in semantic_analysis.values():
        base = v.confidence
        if v.outcome_signal:
            base += 5
        if v.ownership_level in {"owner", "lead"}:
            base += 5
        if v.adjacent_match and not v.snippets:
            base -= 10
        vals.append(max(0, min(100, base)))
    score = int(round(mean(vals)))
    return max(0, min(100, int(round(score * (0.75 + integrity_score / 400)))))


def infer_dna_profile(resume_json):
    text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    scores = {dna: sum(1 for kw in kws if kw in text) for dna, kws in ROLE_DNA_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best, scores


def leadership_score_from_resume(resume_json, years, jd_band):
    text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    score = 20
    for kw in ["led", "managed", "mentored", "stakeholder", "hiring", "onboard"]:
        if kw in text:
            score += 10
    if jd_band == '10+' and years is not None and years < 8:
        score -= 20
    return max(0, min(100, score))


def build_client_weighted_breakdown(resume_json, jd_json, details, hmi, semantic_analysis, domain_score):
    years = estimate_resume_experience_years(resume_json) or 0.0
    jd_min = _jd_root(jd_json).get("min_years_experience") or 0
    expert_depth = sum(1 for v in semantic_analysis.values() if v.depth == "expert")
    applied_depth = sum(1 for v in semantic_analysis.values() if v.depth == "applied")
    skill_depth_score = min(30, expert_depth * 8 + applied_depth * 4)
    scale_score = max(0, min(30, int(round(15 + (years - jd_min) * 2)))) if jd_min else min(30, int(round(years * 2)))
    dna_profile, _ = infer_dna_profile(resume_json)
    dna_score = 22 if dna_profile in {"consulting", "hybrid", "domain specialist"} else 15
    integrity = evaluate_integrity(resume_json)
    evidence_strength = compute_evidence_strength(semantic_analysis, integrity["integrity_score"])
    leadership_score = max(0, min(30, int(round(leadership_score_from_resume(resume_json, years, classify_jd_band(jd_json)) * 0.3))))
    return ClientWeightedBreakdown(
        domain_fit=min(30, int(round((domain_score / 100) * 30))),
        scale_match=scale_score,
        skill_depth=skill_depth_score,
        dna_fit=dna_score,
        evidence=min(30, int(round((evidence_strength / 100) * 30))),
        leadership=leadership_score,
        domain_fit_reason="Domain fit derived from candidate domain enrichment and adjacent-role overlap",
        scale_match_reason=f"{years:.1f} years vs JD baseline {jd_min}",
        skill_depth_reason=f"{expert_depth} expert and {applied_depth} applied skills evidenced",
        dna_fit_reason=f"Detected working style is closest to {dna_profile}",
        evidence_reason="Evidence combines snippets, action verbs, outcomes, and integrity-weighted confidence",
        leadership_reason="Leadership inferred from title, ownership verbs, mentoring, and management signals",
    )


def build_top_tiles(details, semantic_analysis, domain_score, skill_recency_score, experience_gap_years, education_pedigree, company_pedigree, evidence_strength, job_level_fit=65, location_fit=70, industry_domain_fit=60):
    total_mand = max(1, len(details.matched_mandatory) + len(details.adjacent_mandatory) + len(details.missing_mandatory))
    must_have_coverage = int(round(100 * (len(details.matched_mandatory) + 0.5 * len(details.adjacent_mandatory)) / total_mand))
    expert_depth = sum(1 for v in semantic_analysis.values() if v.depth == "expert")
    applied_depth = sum(1 for v in semantic_analysis.values() if v.depth == "applied")
    basic_depth = sum(1 for v in semantic_analysis.values() if v.depth == "basic")
    total_semantic = max(1, len(semantic_analysis))
    skill_depth = int(round(min(100, ((expert_depth * 1.0 + applied_depth * 0.7 + basic_depth * 0.35) / total_semantic) * 100)))
    experience_fit = 100 if experience_gap_years >= 0 else max(15, int(round(100 + experience_gap_years * 15)))
    return TopTiles(
        must_have_coverage=must_have_coverage, skill_depth=skill_depth, recent_relevance=skill_recency_score,
        domain_fit=domain_score, experience_fit=experience_fit, evidence_strength=int(round(evidence_strength)),
        education_pedigree=education_pedigree, company_pedigree=company_pedigree,
        job_level_fit=job_level_fit, location_fit=location_fit, industry_domain_fit=industry_domain_fit,
    )


def build_tile_reasons(details, semantic_analysis, domain_score, skill_recency_score, years, jd_min, education_reason, company_reason, evidence_strength, job_level_reason="", location_reason="", industry_domain_reason=""):
    total_mand = max(1, len(details.matched_mandatory) + len(details.adjacent_mandatory) + len(details.missing_mandatory))
    expert_depth = sum(1 for v in semantic_analysis.values() if v.depth == "expert")
    applied_depth = sum(1 for v in semantic_analysis.values() if v.depth == "applied")
    return TileReasons(
        must_have_coverage_reason=f"Exact matches: {len(details.matched_mandatory)}, adjacent matches: {len(details.adjacent_mandatory)}, total mandatory skills: {total_mand}",
        skill_depth_reason=f"{expert_depth} expert and {applied_depth} applied skills evidenced across work/project contexts",
        recent_relevance_reason=f"Recent experience relevance score derived from role descriptions: {skill_recency_score}%",
        domain_fit_reason=f"Domain fit derived from detected role adjacency and candidate domain signals: {domain_score}%",
        experience_fit_reason=f"{years or 0:.1f} years vs JD baseline {jd_min or 0}",
        evidence_strength_reason=f"Evidence strength derived from in-depth skill evidence, outcomes, and integrity: {evidence_strength}%",
        education_pedigree_reason=education_reason,
        company_pedigree_reason=company_reason,
        job_level_fit_reason=job_level_reason,
        location_fit_reason=location_reason,
        industry_domain_fit_reason=industry_domain_reason,
    )


def build_quick_view(details, flags, experience_gap, education_pedigree, company_pedigree, expert_skills, applied_skills, integrity):
    strengths, gaps, screening = [], [], []
    if details.matched_mandatory:
        strengths.append(f"Matched {len(details.matched_mandatory)} mandatory skills exactly")
    if details.adjacent_mandatory:
        strengths.append(f"Adjacent evidence exists for: {', '.join(details.adjacent_mandatory[:3])}")
    if expert_skills:
        strengths.append(f"Strong depth in: {', '.join(expert_skills[:3])}")
    elif applied_skills:
        strengths.append(f"Applied depth in: {', '.join(applied_skills[:3])}")
    if details.missing_mandatory:
        gaps.append(f"Missing mandatory skills: {', '.join(details.missing_mandatory[:4])}")
    if experience_gap.get('status') == 'below':
        gaps.append(experience_gap['display'])
    elif experience_gap.get('status') == 'above':
        strengths.append(experience_gap['display'])
    if integrity['hard_flags']:
        gaps.append(integrity['hard_flags'][0])
    elif integrity['warning_flags']:
        gaps.append(integrity['warning_flags'][0])
    screening.append("Which required skills are genuinely missing versus present through adjacent tools or platforms?")
    screening.append("How strong is the evidence that the candidate used the matched skills in production, ownership, and outcome contexts?")
    screening.append("Is this a capability mismatch, a resume-writing issue, or primarily a seniority mismatch?")
    return QuickView(top_strengths=strengths[:4], top_gaps=gaps[:4], screening_questions=screening[:3])


def apply_filters(resume_json, jd_json, hmi, integrity):
    flags = MatchFlags(auto_reject_reasons=[], warning_flags=[])
    for item in integrity.get('warning_flags', []):
        flags.warning_flags.append(item)
    for item in integrity.get('hard_flags', []):
        flags.auto_reject_reasons.append(item)
    years = estimate_resume_experience_years(resume_json)
    jd_min = _jd_root(jd_json).get("min_years_experience")
    if years is not None and isinstance(jd_min, int) and jd_min >= 10 and years < max(6, jd_min - 4):
        flags.auto_reject_reasons.append("Seniority gap is too large for this leadership role")
    return flags


def build_human_eval(details, semantic_analysis, integrity, years, jd_min, dna_profile, domain_score, leadership_score, experience_gap):
    exact = details.matched_mandatory
    adjacent = details.adjacent_mandatory
    missing = details.missing_mandatory
    honesty = "high" if integrity['integrity_score'] >= 85 else "medium" if integrity['integrity_score'] >= 65 else "low"
    sematic = []
    for skill, ev in semantic_analysis.items():
        if ev.matched:
            sematic.append({
                "skill": skill,
                "depth": ev.depth,
                "ownership": ev.ownership_level,
                "recency": ev.recency_label,
                "outcome_signal": ev.outcome_signal,
                "adjacent": ev.adjacent_match,
            })
    return {
        "gap_analysis": {
            "exact_mandatory_matches": exact,
            "adjacent_mandatory_matches": adjacent,
            "missing_mandatory": missing,
            "experience_gap_years": experience_gap['signed_years'],
            "experience_gap_months": experience_gap['signed_months'],
            "experience_gap_display": experience_gap['display'],
            "role_mismatch": bool(jd_min and years is not None and years + 3 < jd_min),
        },
        "skill_analysis": sematic,
        "honesty_check": {
            "level": honesty,
            "integrity_score": integrity['integrity_score'],
            "flags": integrity['warning_flags'] + integrity['hard_flags'],
        },
        "semantic_check": {
            "matched_with_outcomes": [x['skill'] for x in sematic if x['outcome_signal']],
            "skills_with_owner_signal": [x['skill'] for x in sematic if x['ownership'] in {'owner', 'lead'}],
            "skills_only_adjacent": [x['skill'] for x in sematic if x['adjacent'] and x['depth'] == 'none'],
        },
        "qualitative_check": {
            "dna_profile": dna_profile,
            "domain_fit_score": domain_score,
            "leadership_reality_score": leadership_score,
            "decision_type": "role mismatch" if jd_min and years is not None and years + 3 < jd_min else ("strong fit" if not missing and not adjacent else "competitive fit"),
        }
    }


def score_job_level_fit(resume_json: dict, jd_json: dict) -> tuple:
    jd_level = extract_jd_job_level(jd_json)
    if not jd_level:
        return 65, "JD did not specify a job level."
    jd_num = JD_LEVEL_MAP.get(jd_level.lower().strip())
    if jd_num is None:
        return 60, f"JD level '{jd_level}' is non-standard; fit assumed neutral."
    candidate_level = infer_candidate_level(resume_json)
    cand_num = JD_LEVEL_MAP.get((candidate_level or "").lower().strip())
    if cand_num is None:
        years = estimate_resume_experience_years(resume_json) or 0.0
        cand_num = 4 if years >= 10 else 3 if years >= 6 else 2 if years >= 3 else 1
    diff = cand_num - jd_num
    if diff == 0:
        return 100, f"Candidate level matches JD expectation ({jd_level})."
    elif diff == 1:
        return 80, f"Candidate is one level above JD ({jd_level}); slight overqualification possible."
    elif diff == -1:
        return 65, f"Candidate is one level below JD ({jd_level}); growth potential exists."
    elif diff >= 2:
        return 50, f"Candidate appears overqualified for JD level ({jd_level})."
    else:
        return 30, f"Candidate level gap is significant for JD level ({jd_level})."


def score_location_fit(resume_json: dict, jd_json: dict) -> tuple:
    work_mode = extract_jd_work_mode(jd_json).lower()
    if "remote" in work_mode:
        return 100, "Role is fully remote — location is not a constraint."
    jd_locations = [x.lower().strip() for x in extract_jd_location(jd_json)]
    if not jd_locations:
        return 70, "JD did not specify a location; location fit assumed neutral."
    cand_loc = extract_candidate_location(resume_json)
    city = cand_loc.get("city", "")
    country = cand_loc.get("country", "")
    raw = cand_loc.get("raw", "")
    for jd_loc in jd_locations:
        if city and city in jd_loc:
            return 100, f"Candidate city '{city}' matches JD location '{jd_loc}'."
        if country and country in jd_loc:
            return 75, f"Candidate country '{country}' matches JD location '{jd_loc}' — city may differ."
        if raw and any(word in jd_loc for word in raw.split() if len(word) > 3):
            return 85, f"Partial location match for JD location '{jd_loc}'."
    if "hybrid" in work_mode:
        return 50, "Role is hybrid but no location overlap detected — relocation or commute may be required."
    return 40, "No location match found — candidate may need to relocate."


def score_industry_domain_fit(resume_json: dict, jd_json: dict) -> tuple:
    jd_domains = extract_jd_industry_domains(jd_json)
    if not jd_domains:
        return 60, "JD did not specify industry domains — fit assumed neutral."
    resume_domains = set(x.lower() for x in extract_resume_domain_set(resume_json))
    resume_text = norm_text(json.dumps(_resume_root(resume_json), ensure_ascii=False))
    if not resume_domains:
        resume_domains = {d for d in jd_domains if d in resume_text}
    matched = [d for d in jd_domains if any(d in rd or rd in d for rd in resume_domains)]
    if matched:
        return min(100, 60 + len(matched) * 15), f"Industry domain overlap: {', '.join(matched[:3])}."
    partial = [d for d in jd_domains if any(w in resume_text for w in d.split() if len(w) > 4)]
    if partial:
        return 45, f"Partial industry signal for: {', '.join(partial[:3])}."
    return 30, f"No clear industry domain overlap with: {', '.join(jd_domains[:3])}."


def suggest_adjacent_roles(detected_role, years, details, leadership_score):
    roles = []
    if detected_role in {'analyst', 'bi_leader'}:
        roles.extend(["Senior Business Intelligence Analyst", "Lead BI Analyst", "Analytics Manager", "Senior Analytics Consultant"])
    if leadership_score < 50:
        roles = [r for r in roles if 'Director' not in r]
    if years is not None and years < 6:
        roles = [r for r in roles if 'Manager' not in r] + ["Senior Analyst"]
    return list(dict.fromkeys(roles))[:5]


def compute_match(resume_json, jd_json, hmi):
    detected_role = detect_role(jd_json)
    resume_skills = extract_resume_skills(resume_json)
    jd_mand = extract_jd_mandatory_skills(jd_json)
    jd_opt = extract_jd_optional_skills(jd_json)
    jd_alignment_score, details = score_jd_alignment(resume_skills, jd_mand, jd_opt)
    config_must = set(norm_skill(x) for x in hmi.config.skills.mustHave)
    config_good = set(norm_skill(x) for x in hmi.config.skills.goodToHave)
    if hmi.use_config_must_have:
        details.matched_config_must_have = sorted(config_must.intersection(resume_skills))
        details.missing_config_must_have = sorted(config_must.difference(resume_skills))
    else:
        details.matched_config_must_have = []
        details.missing_config_must_have = []
    details.matched_good_to_have = sorted(config_good.intersection(resume_skills))
    synonym_map = {norm_skill(k): [norm_skill(x) for x in v] for k, v in hmi.config.skills.semanticSynonyms.items()}
    semantic_analysis = build_semantic_skill_analysis(
        resume_json=resume_json, candidate_skills=resume_skills,
        target_skills=jd_mand.union(jd_opt).union(config_good).union(config_must), synonym_map=synonym_map
    )
    expert_skills = [k for k, v in semantic_analysis.items() if v.depth == "expert"]
    applied_skills = [k for k, v in semantic_analysis.items() if v.depth == "applied"]
    skill_recency_score = score_skill_recency(semantic_analysis)
    domain_score = score_domain(resume_json, hmi, detected_role)
    qualitative_score = score_qualitative(hmi)
    education_pedigree_score, education_pedigree_reason = score_education_pedigree(resume_json, hmi)
    company_pedigree_score, company_pedigree_reason = score_company_pedigree(resume_json, hmi)
    integrity = evaluate_integrity(resume_json)
    evidence_strength = compute_evidence_strength(semantic_analysis, integrity['integrity_score'])
    problem_solving_score = compute_problem_solving_score(resume_json)
    ownership_score = compute_ownership_score(resume_json)
    communication_score = compute_communication_score(resume_json)
    scale_complexity_score = compute_scale_complexity_score(resume_json)
    job_level_fit_score, job_level_reason = score_job_level_fit(resume_json, jd_json)
    location_fit_score, location_reason = score_location_fit(resume_json, jd_json)
    industry_domain_fit_score, industry_domain_reason = score_industry_domain_fit(resume_json, jd_json)
    years = estimate_resume_experience_years(resume_json)
    jd_min = _jd_root(jd_json).get("min_years_experience")
    exp_gap = experience_gap_components(years, jd_min)
    jd_band = classify_jd_band(jd_json)
    leadership_real = leadership_score_from_resume(resume_json, years, jd_band)
    breakdown = build_client_weighted_breakdown(resume_json, jd_json, details, hmi, semantic_analysis, domain_score)
    weights = hmi.config.weights
    total_w = (
        weights.jdAlignment + weights.skillRecency + weights.domain + weights.skillDepth + weights.evidence +
        weights.leadership + weights.educationPedigree + weights.companyPedigree + weights.problemSolving +
        weights.ownership + weights.communication + weights.scaleComplexity + weights.integrity +
        weights.jobLevelFit + weights.industryDomainFit + weights.locationFit
    ) or 100.0
    expert_depth = sum(1 for v in semantic_analysis.values() if v.depth == "expert")
    applied_depth = sum(1 for v in semantic_analysis.values() if v.depth == "applied")
    basic_depth = sum(1 for v in semantic_analysis.values() if v.depth == "basic")
    total_semantic = max(1, len(semantic_analysis))
    skill_depth_percent = int(round(min(100, ((expert_depth * 1.0 + applied_depth * 0.7 + basic_depth * 0.35) / total_semantic) * 100)))
    quantitative_score = (
        weights.jdAlignment * jd_alignment_score + weights.skillRecency * skill_recency_score +
        weights.domain * domain_score + weights.skillDepth * skill_depth_percent +
        weights.evidence * evidence_strength + weights.leadership * leadership_real +
        weights.educationPedigree * education_pedigree_score + weights.companyPedigree * company_pedigree_score +
        weights.problemSolving * problem_solving_score + weights.ownership * ownership_score +
        weights.communication * communication_score + weights.scaleComplexity * scale_complexity_score +
        weights.integrity * integrity['integrity_score'] +
        weights.jobLevelFit * job_level_fit_score + weights.industryDomainFit * industry_domain_fit_score +
        weights.locationFit * location_fit_score
    ) / total_w
    overall_score = int(round(0.92 * quantitative_score + 0.08 * qualitative_score))
    flags = apply_filters(resume_json, jd_json, hmi, integrity)
    thresholds = hmi.config.thresholds
    if flags.auto_reject_reasons:
        recommendation, shortlist = "REJECT", False
    elif overall_score >= thresholds.telephonic:
        recommendation, shortlist = "SHORTLIST", True
    elif overall_score >= thresholds.backup:
        recommendation, shortlist = "SCREEN", False
    else:
        recommendation, shortlist = "REJECT", False
    top_tiles = build_top_tiles(details, semantic_analysis, domain_score, skill_recency_score, exp_gap['signed_years'], education_pedigree_score, company_pedigree_score, evidence_strength, job_level_fit_score, location_fit_score, industry_domain_fit_score)
    tile_reasons = build_tile_reasons(details, semantic_analysis, domain_score, skill_recency_score, years, jd_min, education_pedigree_reason, company_pedigree_reason, evidence_strength, job_level_reason, location_reason, industry_domain_reason)
    quick_view = build_quick_view(details, flags, exp_gap, education_pedigree_score, company_pedigree_score, expert_skills, applied_skills, integrity)
    dna_profile, dna_scores = infer_dna_profile(resume_json)
    human_eval = build_human_eval(details, semantic_analysis, integrity, years, jd_min, dna_profile, domain_score, leadership_real, exp_gap)
    adjacent_roles = suggest_adjacent_roles(detected_role, years, details, leadership_real)
    resume_quality_score = int(round(mean([integrity['integrity_score'], skill_depth_percent, evidence_strength, problem_solving_score, ownership_score, communication_score])))
    jd_fit_score = int(round(mean([top_tiles.must_have_coverage, domain_score, top_tiles.experience_fit, leadership_real, jd_alignment_score])))
    decision_type = "Role mismatch, not candidate failure" if jd_min and years is not None and years + 3 < jd_min else "Candidate-role fit assessment"
    recruiter_summary = (
        f"{decision_type}. Resume quality is {resume_quality_score}%, JD fit is {jd_fit_score}%. "
        f"Exact mandatory coverage is {len(details.matched_mandatory)}/{max(1, len(jd_mand))}, "
        f"adjacent coverage is {len(details.adjacent_mandatory)}, integrity is {integrity['integrity_score']}%, "
        f"and estimated experience is {years or 0:.1f} years against a JD baseline of {jd_min or 0}. {exp_gap['display']}."
    )
    return {
        "overall_score": overall_score,
        "jd_alignment_score": jd_alignment_score,
        "skill_recency_score": skill_recency_score,
        "domain_score": domain_score,
        "qualitative_score": qualitative_score,
        "experience_gap_years": exp_gap['signed_years'],
        "experience_gap_months": exp_gap['signed_months'],
        "experience_gap_display": exp_gap['display'],
        "skill_match_details": details.model_dump(),
        "flags": flags.model_dump(),
        "client_weighted_breakdown": breakdown.model_dump(),
        "top_tiles": top_tiles.model_dump(),
        "tile_reasons": tile_reasons.model_dump(),
        "quick_view": quick_view.model_dump(),
        "semantic_skill_analysis": {k: v.model_dump() for k, v in semantic_analysis.items()},
        "shortlist": shortlist,
        "recommendation": recommendation,
        "recruiter_summary": recruiter_summary,
        "resume_quality": {
            "score": resume_quality_score,
            "integrity_score": integrity['integrity_score'],
            "skill_depth_score": skill_depth_percent,
            "evidence_strength": evidence_strength,
            "problem_solving_score": problem_solving_score,
            "ownership_score": ownership_score,
            "communication_score": communication_score,
            "confidence": integrity['confidence'],
        },
        "jd_fit": {
            "score": jd_fit_score,
            "must_have_coverage": top_tiles.must_have_coverage,
            "domain_fit": domain_score,
            "experience_fit": top_tiles.experience_fit,
            "leadership_fit": leadership_real,
            "semantic_alignment": jd_alignment_score,
        },
        "human_like_eval": human_eval,
        "adjacent_role_suggestions": adjacent_roles,
        "claims_to_verify": (integrity['warning_flags'] + integrity['hard_flags'])[:8],
        "decision_type": decision_type,
        "screening_lens": {
            "candidate_band": classify_experience_band(years),
            "jd_band": jd_band,
            "dna_profile": dna_profile,
            "dna_scores": dna_scores,
        },
        "debug": {
            "detected_role": detected_role,
            "resume_skills": sorted(resume_skills),
            "jd_mandatory": sorted(jd_mand),
            "jd_optional": sorted(jd_opt),
            "resume_years_estimate": years,
            "jd_min_years": jd_min,
            "config_used_for_must_have": hmi.use_config_must_have,
            "education_pedigree_score": education_pedigree_score,
            "company_pedigree_score": company_pedigree_score,
            "problem_solving_score": problem_solving_score,
            "ownership_score": ownership_score,
            "communication_score": communication_score,
            "scale_complexity_score": scale_complexity_score,
            "evidence_strength": evidence_strength,
            "expert_skills": expert_skills,
            "applied_skills": applied_skills,
            "integrity": integrity,
            "job_level_fit_score": job_level_fit_score,
            "location_fit_score": location_fit_score,
            "industry_domain_fit_score": industry_domain_fit_score,
        }
    }
