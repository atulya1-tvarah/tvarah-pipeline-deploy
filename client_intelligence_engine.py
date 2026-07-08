"""Module 3 — Client Side Intelligence.

Functions:
  build_candidate_fit_narrative — 5-7 fit bullets + fit_score
  search_candidates             — multi-filter candidate search
  find_similar_companies        — hybrid taxonomy + LLM fallback
  resource_intelligence         — candidates from a given source company
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from company_tier_taxonomy import TIER_MAP, COMPANY_DOMAIN_TAGS, classify_company_tier, get_company_domain_tags


# ---------------------------------------------------------------------------
# 1. Candidate fit narrative
# ---------------------------------------------------------------------------

def build_candidate_fit_narrative(
    candidate_analysis: dict[str, Any],
    client_config: dict[str, Any],
    role_config: dict[str, Any],
    rubric_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a recruiter-ready fit narrative with 5-7 bullets.

    Returns:
        fit_score (0-100), fit_bullets (list[str]), mandatory_skill_coverage,
        gap_mandatory_skills, dna_match, tier_match, stability_match, yoe_in_range.
    """
    # --- Extract candidate signals ---
    overview = candidate_analysis.get("candidate_overview") or candidate_analysis.get("overview") or {}
    experience = candidate_analysis.get("experience_analysis") or {}
    skill_analysis = candidate_analysis.get("skill_analysis") or {}
    top_skills = skill_analysis.get("top_skills") or []
    dna = candidate_analysis.get("dna_fit") or {}
    scorecard = candidate_analysis.get("scorecard") or {}
    rubric = rubric_result or candidate_analysis.get("rubric_scorecard") or {}

    # Build skill evidence lookup
    skill_ev: dict[str, str] = {}
    for s in top_skills:
        name = str(s.get("skill") or "").lower()
        if name:
            skill_ev[name] = str(s.get("evidence_level") or "NONE").upper()

    # --- Mandatory skill coverage ---
    mandatory = role_config.get("mandatory_skills") or []
    covered = [e for e in mandatory if skill_ev.get(str(e.get("skill") or "").lower(), "NONE") not in ("NONE", "MENTION")]
    gaps = [e.get("skill") for e in mandatory if skill_ev.get(str(e.get("skill") or "").lower(), "NONE") in ("NONE", "MENTION")]
    mandatory_coverage = round(len(covered) / max(len(mandatory), 1), 2)

    # --- DNA match ---
    hiring_strategy = client_config.get("hiring_strategy") or {}
    preferred_dna = str(hiring_strategy.get("preferred_dna") or "").upper()
    candidate_dna = str(dna.get("primary_dna") or "").upper()
    dna_match = preferred_dna == candidate_dna if preferred_dna else None

    # --- Company tier match ---
    companies = experience.get("companies") or []
    candidate_best_tier = 5
    for c in companies:
        t = classify_company_tier(str(c or ""), llm_fallback=False)
        if t < candidate_best_tier:
            candidate_best_tier = t
    preferred_tier = int(hiring_strategy.get("company_tier_preference") or 5)
    tier_match = candidate_best_tier <= preferred_tier

    # --- Stability match ---
    avg_tenure = float(experience.get("average_tenure_months") or 0)
    min_stability = float(hiring_strategy.get("stability_min_months") or 0)
    stability_match = avg_tenure >= min_stability if min_stability > 0 else None

    # --- YoE in range ---
    yoe = float(experience.get("total_experience_years") or 0)
    yoe_range = role_config.get("yoe_range") or {}
    yoe_min = float(yoe_range.get("min") or 0)
    yoe_max = float(yoe_range.get("max") or 999)
    yoe_in_range = yoe_min <= yoe <= yoe_max

    # --- Fit score (0-100) ---
    score_parts = [
        mandatory_coverage * 40,
        (1 if tier_match else 0) * 15,
        (1 if dna_match else 0.5 if dna_match is None else 0) * 15,
        (1 if stability_match else 0.5 if stability_match is None else 0) * 10,
        (1 if yoe_in_range else 0) * 10,
        (float(scorecard.get("total_score") or 0) / 100) * 10,
    ]
    fit_score = round(min(100, sum(score_parts)), 1)

    # --- Try LLM for fit bullets; fall back to rule-based ---
    fit_bullets = _llm_fit_bullets(
        overview=overview,
        experience=experience,
        top_skills=top_skills,
        dna=dna,
        covered=covered,
        gaps=gaps,
        role_config=role_config,
        client_config=client_config,
        fit_score=fit_score,
    )

    return {
        "fit_score": fit_score,
        "fit_bullets": fit_bullets,
        "mandatory_skill_coverage": mandatory_coverage,
        "gap_mandatory_skills": gaps,
        "covered_mandatory_skills": [e.get("skill") for e in covered],
        "dna_match": dna_match,
        "tier_match": tier_match,
        "best_candidate_tier": candidate_best_tier,
        "stability_match": stability_match,
        "avg_tenure_months": avg_tenure,
        "yoe_in_range": yoe_in_range,
        "candidate_yoe": yoe,
    }


def _llm_fit_bullets(
    overview: dict,
    experience: dict,
    top_skills: list,
    dna: dict,
    covered: list,
    gaps: list,
    role_config: dict,
    client_config: dict,
    fit_score: float,
) -> list[str]:
    """Call LLM for 5-7 recruiter fit bullets; return rule-based fallback on failure."""
    try:
        from llm_client import call_llm_json  # type: ignore
        from llm_judging_assets import CLIENT_FIT_SYSTEM_PROMPT  # type: ignore

        role_family = role_config.get("role_family", "")
        mandatory_names = [e.get("skill") for e in (role_config.get("mandatory_skills") or [])]
        strong_skills = [s.get("skill") for s in top_skills[:5] if s.get("evidence_level") in ("APPLIED", "DEEP", "EXPERT")]
        yoe = experience.get("total_experience_years", 0)
        name = overview.get("name", "The candidate")
        companies = (experience.get("companies") or [])[:3]

        user_msg = (
            f"Candidate: {name}, {yoe} years, current/recent companies: {', '.join(str(c) for c in companies)}.\n"
            f"Role family: {role_family}.\n"
            f"Mandatory skills covered: {', '.join(str(s.get('skill')) for s in covered) or 'none'}.\n"
            f"Mandatory skill gaps: {', '.join(str(g) for g in gaps) or 'none'}.\n"
            f"Strong skills from resume: {', '.join(str(s) for s in strong_skills) or 'none'}.\n"
            f"DNA fit: {dna.get('primary_dna', 'UNKNOWN')}.\n"
            f"Overall fit score: {fit_score}/100.\n"
            f"Generate 5-7 recruiter-grade fit bullets (evidence-grounded, no fluff).\n"
            f'Return JSON: {{"fit_bullets": ["bullet1", "bullet2", ...]}}'
        )
        result = call_llm_json(
            system_prompt=CLIENT_FIT_SYSTEM_PROMPT,
            user_message=user_msg,
            schema={
                "type": "object",
                "properties": {"fit_bullets": {"type": "array", "items": {"type": "string"}}},
                "required": ["fit_bullets"],
            },
            label="client_fit_bullets",
        )
        bullets = result.get("fit_bullets") if result else None
        if bullets and isinstance(bullets, list) and len(bullets) >= 3:
            return bullets[:7]
    except Exception:
        pass

    # Rule-based fallback
    return _rule_based_fit_bullets(overview, experience, top_skills, covered, gaps, dna, fit_score)


def _rule_based_fit_bullets(
    overview: dict,
    experience: dict,
    top_skills: list,
    covered: list,
    gaps: list,
    dna: dict,
    fit_score: float,
) -> list[str]:
    bullets = []
    yoe = experience.get("total_experience_years", 0)
    name = overview.get("name", "The candidate")

    if yoe:
        bullets.append(f"{name} brings {yoe} years of experience with demonstrated career progression.")
    covered_names = [str(e.get("skill")) for e in covered]
    if covered_names:
        bullets.append(f"Covers {len(covered_names)} of the mandatory skills: {', '.join(covered_names[:4])}.")
    if gaps:
        bullets.append(f"Gaps in mandatory skills: {', '.join(str(g) for g in gaps[:3])} — recommend validation at interview.")
    strong = [s.get("skill") for s in top_skills[:3] if s.get("evidence_level") in ("APPLIED", "DEEP", "EXPERT")]
    if strong:
        bullets.append(f"Strong depth evidence in: {', '.join(str(s) for s in strong)}.")
    if experience.get("international_exposure"):
        bullets.append("International exposure signals adaptability for global teams.")
    if experience.get("client_facing"):
        bullets.append("Client-facing track record supports stakeholder management expectations.")
    bullets.append(f"Overall fit score: {fit_score}/100.")
    return bullets[:7]


# ---------------------------------------------------------------------------
# 2. Candidate search
# ---------------------------------------------------------------------------

def search_candidates(
    query: dict[str, Any],
    candidate_scores_dir: str,
) -> list[dict[str, Any]]:
    """Filter candidate score records by query params.

    Supported keys: role_family, min_score, max_score,
                    skills (list, requires APPLIED+),
                    company_tier_max, yoe_min, yoe_max, dna.
    """
    scores_dir = Path(candidate_scores_dir)
    if not scores_dir.exists():
        return []

    role_family = str(query.get("role_family") or "").upper()
    min_score = query.get("min_score")
    max_score = query.get("max_score")
    required_skills = [str(s).lower() for s in (query.get("skills") or [])]
    company_tier_max = query.get("company_tier_max")
    yoe_min = query.get("yoe_min")
    yoe_max = query.get("yoe_max")
    dna_filter = str(query.get("dna") or "").upper()

    results = []
    for path in scores_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        total = record.get("current_total", 0)
        if min_score is not None and total < min_score:
            continue
        if max_score is not None and total > max_score:
            continue

        # Grab the most recent stage for detail filters
        stages = record.get("stages") or []
        latest = stages[-1] if stages else {}
        breakdown = latest.get("breakdown") or {}
        exp_breakdown = breakdown.get("experience") or {}
        skills_breakdown = breakdown.get("skills") or {}

        # Role family (stored in semantic_analysis if available, else skip)
        # For a simple filter we just include all if role_family not in record
        # (full analysis object is not stored in score file by default)

        # YoE filter — stored as float if candidate overview is embedded
        # Not natively in score file; skip if not available (permissive)

        # Skills filter — not in score file directly; permissive pass
        # (For strict filtering, full analysis must be embedded; we store score only)

        # DNA filter
        if dna_filter:
            candidate_dna = str(record.get("dna", "") or "").upper()
            if candidate_dna and candidate_dna != dna_filter:
                continue

        # Company tier filter from experience breakdown
        best_tier = (exp_breakdown.get("company_tier") or {}).get("best_tier", 5)
        if company_tier_max is not None and isinstance(best_tier, int) and best_tier > company_tier_max:
            continue

        results.append({
            "candidate_id": record.get("candidate_id"),
            "candidate_name": record.get("candidate_name"),
            "current_stage": record.get("current_stage"),
            "current_total": total,
            "reject_flags": latest.get("reject_flags", []),
        })

    return results


# ---------------------------------------------------------------------------
# 3. Similar companies (hybrid taxonomy + LLM)
# ---------------------------------------------------------------------------

def find_similar_companies(
    candidate_analysis: dict[str, Any],
    top_n: int = 10,
) -> dict[str, Any]:
    """Find similar companies to those in the candidate's experience.

    Step 1: taxonomy lookup (domain_tags + operating_model + role_family).
    Step 2: LLM fallback if < 5 taxonomy matches.
    """
    _exp = candidate_analysis.get("experience_analysis")
    if not _exp:
        _stages = candidate_analysis.get("stages")
        _exp = (_stages[-1] if _stages else {}) if isinstance(_stages, list) else {}
    experience = _exp if isinstance(_exp, dict) else {}
    companies_in_profile = [str(c) for c in (experience.get("companies") or []) if c]
    domain_tags = list(experience.get("domain_tags") or [])
    operating_model = str(experience.get("dominant_operating_model") or "HYBRID").upper()
    role_family = str(
        (candidate_analysis.get("semantic_analysis") or {}).get("top_role_family") or ""
    ).upper()

    # Build domain tag set from candidate companies
    all_domain_tags: set[str] = set(domain_tags)
    candidate_company_lower = {c.lower() for c in companies_in_profile}
    for company in companies_in_profile:
        tags = get_company_domain_tags(company)
        all_domain_tags.update(tags)

    # Step 1: taxonomy match
    taxonomy_matches: list[dict[str, Any]] = []
    for company_key, tier in TIER_MAP.items():
        if company_key in candidate_company_lower:
            continue  # skip companies already in profile
        company_tags = get_company_domain_tags(company_key)
        overlap = all_domain_tags.intersection(company_tags)
        if overlap:
            taxonomy_matches.append({
                "company": company_key.title(),
                "tier": tier,
                "domain_tags": company_tags,
                "overlap_tags": sorted(overlap),
                "relevance_reason": f"Shares domain tags: {', '.join(sorted(overlap))}",
                "source": "taxonomy",
            })

    # Sort by overlap size, then tier
    taxonomy_matches.sort(key=lambda x: (-len(x["overlap_tags"]), x["tier"]))

    similar: list[dict[str, Any]] = taxonomy_matches[:top_n]

    # Step 2: LLM fallback if < 5 taxonomy hits
    if len(similar) < 5:
        llm_companies = _llm_similar_companies(
            companies_in_profile=companies_in_profile,
            domain_tags=sorted(all_domain_tags),
            operating_model=operating_model,
            role_family=role_family,
            existing_names={m["company"].lower() for m in similar},
            needed=top_n - len(similar),
        )
        similar.extend(llm_companies)

    return {
        "similar_companies": similar[:top_n],
        "source_companies": companies_in_profile,
        "domain_tags_used": sorted(all_domain_tags),
        "taxonomy_match_count": len(taxonomy_matches),
    }


def _llm_similar_companies(
    companies_in_profile: list[str],
    domain_tags: list[str],
    operating_model: str,
    role_family: str,
    existing_names: set[str],
    needed: int,
) -> list[dict[str, Any]]:
    """Call LLM to suggest similar companies. Returns empty list on failure."""
    try:
        from llm_client import call_llm_json  # type: ignore
        from llm_judging_assets import SIMILAR_COMPANIES_SYSTEM_PROMPT  # type: ignore

        user_msg = (
            f"Candidate has worked at: {', '.join(companies_in_profile[:5]) or 'unknown'}.\n"
            f"Domain tags: {', '.join(domain_tags[:8]) or 'unknown'}.\n"
            f"Operating model: {operating_model}. Role family: {role_family or 'unknown'}.\n"
            f"Suggest {needed + 2} similar companies the candidate would transition well into.\n"
            f"Exclude: {', '.join(list(existing_names)[:10])}.\n"
            f'Return JSON: {{"companies": [{{"name":"...", "domain":"...", "tier":1, "relevance_reason":"..."}}]}}'
        )
        result = call_llm_json(
            system_prompt=SIMILAR_COMPANIES_SYSTEM_PROMPT,
            user_message=user_msg,
            schema={
                "type": "object",
                "properties": {
                    "companies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "domain": {"type": "string"},
                                "tier": {"type": "integer"},
                                "relevance_reason": {"type": "string"},
                            },
                            "required": ["name", "domain", "tier", "relevance_reason"],
                        },
                    }
                },
                "required": ["companies"],
            },
            label="similar_companies_llm",
        )
        companies = (result or {}).get("companies") or []
        return [
            {**c, "source": "llm"}
            for c in companies
            if isinstance(c, dict) and c.get("name", "").lower() not in existing_names
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 4. Resource intelligence
# ---------------------------------------------------------------------------

def resource_intelligence(
    source_company: str,
    candidate_scores_dir: str,
) -> dict[str, Any]:
    """Find all candidates whose experience mentions source_company (fuzzy match)."""
    scores_dir = Path(candidate_scores_dir)
    if not scores_dir.exists():
        return {"source_company": source_company, "candidates": [], "count": 0}

    source_lower = source_company.lower().strip()
    matches = []

    for path in scores_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Check if any stage breakdown has a company mention
        stages = record.get("stages") or []
        found = False
        for stage in stages:
            breakdown = stage.get("breakdown") or {}
            exp_bd = breakdown.get("experience") or {}
            company_tier = exp_bd.get("company_tier") or {}
            # Also check if raw record has a companies field embedded
            raw_companies = record.get("companies") or []
            for company in raw_companies:
                if source_lower in str(company).lower():
                    found = True
                    break
            # Fuzzy: check if source appears in candidate_name or breakdown keys
            if source_lower in json.dumps(stage).lower():
                found = True
            if found:
                break

        if found:
            matches.append({
                "candidate_id": record.get("candidate_id"),
                "candidate_name": record.get("candidate_name"),
                "current_total": record.get("current_total"),
                "current_stage": record.get("current_stage"),
            })

    return {
        "source_company": source_company,
        "candidates": matches,
        "count": len(matches),
    }
