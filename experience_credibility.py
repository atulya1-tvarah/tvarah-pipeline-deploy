"""Experience credibility engine.

Consumes role_expectation_map + experience dict + semantic dict + evidence_map
→ credibility signals a recruiter can act on.

Public function:
    compute_experience_credibility(experience, semantic, evidence_map) → dict
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from role_expectation_map import get_credibility_template, get_era_stack, _normalize_role

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEPTH_ORDER = ["AWARENESS", "FOUNDATIONAL", "HANDS_ON", "ADVANCED", "ARCHITECT_LEVEL"]


def _depth_idx(label: str) -> int:
    return _DEPTH_ORDER.index(str(label or "FOUNDATIONAL").upper()) if str(label or "").upper() in _DEPTH_ORDER else 1


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _parse_year(date_str: str | None) -> int | None:
    """Extract 4-digit year from a date string."""
    if not date_str:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", str(date_str))
    if m:
        return int(m.group())
    return None


def _current_year() -> int:
    return datetime.now(timezone.utc).year


def _normalise_skill(s: str) -> str:
    return str(s or "").strip().lower()


def _claimed_skills(evidence_map: dict[str, Any]) -> set[str]:
    """Return set of normalised skill names with APPLIED/DEEP/EXPERT evidence."""
    result = set()
    for item in evidence_map.values():
        level = str(item.get("evidence_level") or "NONE").upper()
        if level in ("APPLIED", "DEEP", "EXPERT"):
            skill = _normalise_skill(str(item.get("skill") or ""))
            if skill:
                result.add(skill)
    return result


def _all_mentioned_skills(evidence_map: dict[str, Any]) -> set[str]:
    """All skills in evidence map regardless of level."""
    return {_normalise_skill(str(item.get("skill") or "")) for item in evidence_map.values() if item.get("skill")}


def _skill_in_set(skill: str, skill_set: set[str]) -> bool:
    """Fuzzy skill match: either exact or substring match."""
    norm = _normalise_skill(skill)
    if norm in skill_set:
        return True
    # Partial match: if the target skill is a substring of any claimed skill
    for claimed in skill_set:
        if norm in claimed or claimed in norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Core credibility computation
# ---------------------------------------------------------------------------

def compute_experience_credibility(
    experience: dict[str, Any],
    semantic: dict[str, Any],
    evidence_map: dict[str, Any],
) -> dict[str, Any]:
    """Compute experience credibility signals.

    Returns:
        {
            "credibility_score": float (0.0–1.0),
            "role_tier_match": dict,
            "must_have_gaps": list[str],
            "unexplained_claims": list[str],
            "era_mismatches": list[dict],
            "progression_plausibility": str,
            "per_company_credibility": list[dict],
            "credibility_narrative": str,
        }
    """
    # ── 1. Resolve role_family and best tier ──────────────────────────────
    role_family = str(
        semantic.get("top_role_family")
        or semantic.get("bert_role_family_prior", {}).get("label")
        or "DATA_SCIENTIST"
    ).upper()
    role_family = _normalize_role(role_family)

    company_tiers = {}
    for cp in (experience.get("company_profiles") or []):
        if isinstance(cp, dict):
            cname = str(cp.get("company") or "").strip().lower()
            tier = _safe_int(cp.get("tier") or cp.get("company_tier"), 5)
            if cname:
                company_tiers[cname] = tier

    best_tier = min(company_tiers.values(), default=5)
    total_years = _safe_float(experience.get("total_experience_years"), 0.0)

    # Determine approximate start year from experience
    all_starts = []
    for item in (experience.get("tenure_with_dates") or []):
        if isinstance(item, dict):
            yr = _parse_year(str(item.get("start_date") or item.get("from") or ""))
            if yr:
                all_starts.append(yr)
    career_start_year = min(all_starts) if all_starts else (_current_year() - int(total_years or 1))

    template = get_credibility_template(role_family, best_tier, total_years, career_start_year)

    role_tier_match = {
        "role_family": role_family,
        "tier": best_tier,
        "source": "semantic+company_profiles",
        "depth_floor": template["depth_floor"],
    }

    # ── 2. Skill gap analysis ──────────────────────────────────────────────
    claimed = _claimed_skills(evidence_map)
    all_mentioned = _all_mentioned_skills(evidence_map)
    must_have = template["must_have_skills"]
    likely = template["likely_skills"]

    must_have_gaps = [
        s for s in must_have
        if not _skill_in_set(s, claimed)
    ]

    # ── 3. Era mismatch detection ──────────────────────────────────────────
    era_mismatches: list[dict] = []
    for item in (experience.get("tenure_with_dates") or []):
        if not isinstance(item, dict):
            continue
        company = str(item.get("company") or "").strip()
        start_yr = _parse_year(str(item.get("start_date") or item.get("from") or ""))
        end_yr = _parse_year(str(item.get("end_date") or item.get("to") or ""))
        if not start_yr:
            continue
        mid_yr = (start_yr + (end_yr or _current_year())) // 2
        expected_era_skills = get_era_stack(role_family, best_tier, mid_yr)
        expected_norm = {_normalise_skill(s) for s in expected_era_skills}

        # Check skills claimed for this company vs expected era
        role_skills = []
        for cp in (experience.get("company_profiles") or []):
            if isinstance(cp, dict) and str(cp.get("company") or "").strip().lower() == company.lower():
                role_skills = [_normalise_skill(s) for s in (cp.get("skills") or [])]
                break

        for skill in role_skills:
            if not skill or skill in expected_norm:
                continue
            # A skill claimed during this period but not in era stack
            # Flag only if it's notably newer (post-era) or older (pre-era)
            is_future_tech = False
            is_archaic_tech = False
            # Simple heuristics for common future/archaic flags
            if mid_yr < 2020 and any(kw in skill for kw in ["llm", "langchain", "gpt", "vector database", "rag"]):
                is_future_tech = True
            if mid_yr > 2018 and any(kw in skill for kw in ["cobol", "fortran", "powerbuilder"]):
                is_archaic_tech = True

            if is_future_tech:
                era_mismatches.append({
                    "skill": skill,
                    "company": company,
                    "claimed_year": mid_yr,
                    "note": f"'{skill}' was not mainstream at Tier {best_tier} {role_family} roles in {mid_yr}. Possible inflation.",
                })
            elif is_archaic_tech:
                era_mismatches.append({
                    "skill": skill,
                    "company": company,
                    "claimed_year": mid_yr,
                    "note": f"'{skill}' is unusually archaic for a {mid_yr} {role_family} role. May indicate legacy system context.",
                })

    # ── 4. Unexplained claims (skills claimed but implausible for tier) ────
    unexplained_claims: list[str] = []
    if best_tier >= 4:
        # For IT-services/consulting tier, flag if claiming FAANG-specific depth tools
        tier1_signals = {"kubernetes", "kafka", "ray", "mlflow", "kubeflow", "triton",
                         "databricks", "delta lake", "feature store", "mlops platform",
                         "distributed training", "service mesh"}
        for skill_norm in claimed:
            if any(t1 in skill_norm for t1 in tier1_signals):
                unexplained_claims.append(
                    f"'{skill_norm}' is typically APPLIED at Tier 1/2 companies — "
                    f"verify depth at Tier {best_tier} context"
                )
    elif best_tier == 1:
        # At Tier 1, flag if candidate lacks core must-haves but claims advanced tools
        core_missing_count = len(must_have_gaps)
        advanced_claims = [
            s for s in claimed
            if any(adv in s for adv in ["architect", "design", "platform", "governance"])
        ]
        if core_missing_count >= len(must_have) // 2 and advanced_claims:
            for s in advanced_claims[:3]:
                unexplained_claims.append(
                    f"Claims '{s}' but missing {core_missing_count} core must-have skills — credibility gap"
                )

    # ── 5. Depth floor check ───────────────────────────────────────────────
    depth_floor = template["depth_floor"]
    # Get the candidate's top skill depth from evidence
    top_depth = "FOUNDATIONAL"
    for item in evidence_map.values():
        d = str(item.get("depth_label") or item.get("bert_depth_prior") or "").upper()
        if d in _DEPTH_ORDER and _depth_idx(d) > _depth_idx(top_depth):
            top_depth = d

    depth_ok = _depth_idx(top_depth) >= _depth_idx(depth_floor)

    # ── 6. Progression plausibility ───────────────────────────────────────
    progression = str(experience.get("progression") or "STABLE").upper()
    career_traj = _safe_int(experience.get("career_trajectory_score"), 3)

    # At Tier 1 with < 3 yrs, FAST_TRACK is suspicious
    if best_tier == 1 and total_years < 3 and career_traj >= 5:
        prog_plausibility = (
            "SUSPICIOUS — FAST_TRACK trajectory at Tier 1 with <3 years total experience. "
            "Verify titles with LinkedIn and references."
        )
    elif best_tier <= 2 and total_years >= 8 and career_traj <= 1:
        prog_plausibility = (
            "SUSPICIOUS — Stagnant trajectory at top-tier company over 8+ years. "
            "May indicate individual contributor plateau or title inflation."
        )
    elif career_traj >= 4:
        prog_plausibility = "CREDIBLE — Strong upward progression consistent with tenure and tier."
    elif career_traj >= 2:
        prog_plausibility = "CREDIBLE — Moderate progression. Typical for mid-level career arc."
    else:
        prog_plausibility = "FAST — Rapid title growth; verify with references or LinkedIn."

    # ── 7. Per-company credibility ────────────────────────────────────────
    per_company_credibility: list[dict] = []
    for cp in (experience.get("company_profiles") or []):
        if not isinstance(cp, dict):
            continue
        company = str(cp.get("company") or "unknown").strip()
        c_tier = _safe_int(cp.get("tier") or cp.get("company_tier"), 5)
        c_start = _parse_year(str(cp.get("start_date") or ""))
        c_yrs = _safe_float(cp.get("years") or cp.get("tenure_years"), 0.0)
        c_skills = [_normalise_skill(s) for s in (cp.get("skills") or [])]

        c_template = get_credibility_template(role_family, c_tier,
                                               c_yrs, c_start or career_start_year)
        c_must = c_template["must_have_skills"]
        c_gaps = [s for s in c_must if not _skill_in_set(s, set(c_skills) | claimed)]
        c_era = get_era_stack(role_family, c_tier, c_start or career_start_year)
        c_era_hits = [s for s in c_era if _skill_in_set(s, set(c_skills))]

        c_verdict = (
            "CREDIBLE" if len(c_gaps) <= len(c_must) // 3
            else "GAP_DETECTED" if len(c_gaps) <= len(c_must) // 2
            else "LOW_CONFIDENCE"
        )

        per_company_credibility.append({
            "company": company,
            "tier": c_tier,
            "verdict": c_verdict,
            "must_have_gaps": c_gaps[:5],
            "era_skill_hits": c_era_hits[:5],
        })

    # ── 8. Credibility score (0.0–1.0) ───────────────────────────────────
    # Component A (0.5): must_have coverage
    must_hit_count = len(must_have) - len(must_have_gaps)
    component_a = (must_hit_count / max(len(must_have), 1)) * 0.5

    # Component B (0.3): no era mismatches
    component_b = 0.3 if not era_mismatches else max(0.0, 0.3 - 0.05 * len(era_mismatches))

    # Component C (0.2): progression ok + depth floor met
    progression_ok = "SUSPICIOUS" not in prog_plausibility
    component_c = 0.2 if (progression_ok and depth_ok) else (0.1 if progression_ok or depth_ok else 0.0)

    credibility_score = round(min(1.0, component_a + component_b + component_c), 2)

    # ── 9. Narrative (2–3 sentences) ──────────────────────────────────────
    narrative_parts: list[str] = []

    # Coverage sentence
    if len(must_have_gaps) == 0:
        narrative_parts.append(
            f"Candidate claims all expected must-have skills for a "
            f"Tier {best_tier} {role_family.replace('_', ' ').title()} — strong foundational signal."
        )
    elif len(must_have_gaps) <= 2:
        narrative_parts.append(
            f"Most must-have skills for a Tier {best_tier} {role_family.replace('_', ' ').title()} "
            f"are present, but '{must_have_gaps[0]}' "
            f"{'and ' + chr(39) + must_have_gaps[1] + chr(39) if len(must_have_gaps) > 1 else ''}"
            f" is missing — probe during interview."
        )
    else:
        gaps_str = ", ".join(f"'{s}'" for s in must_have_gaps[:3])
        narrative_parts.append(
            f"Notable must-have skill gaps detected for Tier {best_tier} "
            f"{role_family.replace('_', ' ').title()} role: {gaps_str} — credibility is limited."
        )

    # Era / unexplained claims sentence
    if era_mismatches:
        narrative_parts.append(
            f"{len(era_mismatches)} era mismatch(es) found — "
            f"e.g., '{era_mismatches[0].get('skill')}' at {era_mismatches[0].get('company')} "
            f"around {era_mismatches[0].get('claimed_year')}. Verify with reference check."
        )
    elif unexplained_claims:
        narrative_parts.append(
            f"Some advanced tool claims ({unexplained_claims[0].split(' —')[0]}) "
            f"need validation given the company tier context."
        )

    # Progression sentence
    narrative_parts.append(f"Progression: {prog_plausibility.split(' — ')[0]}.")

    credibility_narrative = " ".join(narrative_parts)

    return {
        "credibility_score": credibility_score,
        "role_tier_match": role_tier_match,
        "must_have_gaps": must_have_gaps,
        "unexplained_claims": unexplained_claims[:5],
        "era_mismatches": era_mismatches[:5],
        "progression_plausibility": prog_plausibility,
        "per_company_credibility": per_company_credibility,
        "credibility_narrative": credibility_narrative,
    }
