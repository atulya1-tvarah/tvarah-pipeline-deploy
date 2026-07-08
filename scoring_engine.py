
from __future__ import annotations

import json
import os
from typing import Any
from decimal import Decimal, ROUND_HALF_UP

from bert_signal_engine import build_evidence_packets
from llm_client import call_llm_json, get_last_llm_error, get_last_llm_provider_used, llm_provider, provider_enabled, scoring_model
from llm_judging_assets import SCORING_SYSTEM_PROMPT
from taxonomy import DOMAIN_FIT_MAP, ROLE_DNA_AFFINITY

MAX_SCORES = {
    "skill_score": 30,
    "experience_score": 20,
    "role_alignment_score": 15,
    "impact_score": 15,
    "stability_score": 10,
    "dna_score": 10,
}

RATING_TO_COMPONENT = {
    "skill_score": "skill_strength_0_to_5",
    "experience_score": "experience_depth_0_to_5",
    "role_alignment_score": "role_alignment_0_to_5",
    "impact_score": "business_impact_0_to_5",
    "stability_score": "career_stability_0_to_5",
    "dna_score": "dna_fit_0_to_5",
}

MINIMAL_SCORECARD_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_score": {"type": "number"},
        "experience_score": {"type": "number"},
        "role_alignment_score": {"type": "number"},
        "impact_score": {"type": "number"},
        "stability_score": {"type": "number"},
        "dna_score": {"type": "number"},
        "dimension_ratings": {
            "type": "object",
            "properties": {
                "skill_strength_0_to_5": {"type": "number"},
                "experience_depth_0_to_5": {"type": "number"},
                "role_alignment_0_to_5": {"type": "number"},
                "business_impact_0_to_5": {"type": "number"},
                "career_stability_0_to_5": {"type": "number"},
                "dna_fit_0_to_5": {"type": "number"},
            },
            "required": [
                "skill_strength_0_to_5",
                "experience_depth_0_to_5",
                "role_alignment_0_to_5",
                "business_impact_0_to_5",
                "career_stability_0_to_5",
                "dna_fit_0_to_5",
            ],
            "additionalProperties": False,
        },
        "dimension_confidence": {
            "type": "object",
            "properties": {
                "skill_strength": {"type": "string"},
                "experience_depth": {"type": "string"},
                "role_alignment": {"type": "string"},
                "business_impact": {"type": "string"},
                "career_stability": {"type": "string"},
                "dna_fit": {"type": "string"},
            },
            "required": [
                "skill_strength",
                "experience_depth",
                "role_alignment",
                "business_impact",
                "career_stability",
                "dna_fit",
            ],
            "additionalProperties": False,
        },
        "overall_band": {"type": "string"},
        "total_score": {"type": "number"},
        "benchmark_summary": {"type": "string"},
        "benchmark_definition": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": [
        "skill_score",
        "experience_score",
        "role_alignment_score",
        "impact_score",
        "stability_score",
        "dna_score",
        "dimension_ratings",
        "dimension_confidence",
        "overall_band",
        "total_score",
        "benchmark_summary",
        "benchmark_definition",
        "rationale",
    ],
    "additionalProperties": False,
}

EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "component_rationales": {
            "type": "object",
            "properties": {
                "skill_score": {"type": "string"},
                "experience_score": {"type": "string"},
                "role_alignment_score": {"type": "string"},
                "impact_score": {"type": "string"},
                "stability_score": {"type": "string"},
                "dna_score": {"type": "string"},
            },
            "required": ["skill_score", "experience_score", "role_alignment_score", "impact_score", "stability_score", "dna_score"],
            "additionalProperties": False,
        },
        "justification_notes": {
            "type": "object",
            "properties": {
                "skill_score": {"type": "array", "items": {"type": "string"}},
                "experience_score": {"type": "array", "items": {"type": "string"}},
                "role_alignment_score": {"type": "array", "items": {"type": "string"}},
                "impact_score": {"type": "array", "items": {"type": "string"}},
                "stability_score": {"type": "array", "items": {"type": "string"}},
                "dna_score": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["skill_score", "experience_score", "role_alignment_score", "impact_score", "stability_score", "dna_score"],
            "additionalProperties": False,
        },
        "component_justifications": {
            "type": "object",
            "properties": {
                "skill_score": {
                    "type": "object",
                    "properties": {
                        "strongest_evidence": {"type": "string"},
                        "main_gap": {"type": "string"},
                        "why_not_lower": {"type": "string"},
                    },
                    "required": ["strongest_evidence", "main_gap", "why_not_lower"],
                    "additionalProperties": False,
                },
                "experience_score": {
                    "type": "object",
                    "properties": {
                        "strongest_evidence": {"type": "string"},
                        "main_gap": {"type": "string"},
                        "why_not_lower": {"type": "string"},
                    },
                    "required": ["strongest_evidence", "main_gap", "why_not_lower"],
                    "additionalProperties": False,
                },
                "role_alignment_score": {
                    "type": "object",
                    "properties": {
                        "strongest_evidence": {"type": "string"},
                        "main_gap": {"type": "string"},
                        "why_not_lower": {"type": "string"},
                    },
                    "required": ["strongest_evidence", "main_gap", "why_not_lower"],
                    "additionalProperties": False,
                },
                "impact_score": {
                    "type": "object",
                    "properties": {
                        "strongest_evidence": {"type": "string"},
                        "main_gap": {"type": "string"},
                        "why_not_lower": {"type": "string"},
                    },
                    "required": ["strongest_evidence", "main_gap", "why_not_lower"],
                    "additionalProperties": False,
                },
                "stability_score": {
                    "type": "object",
                    "properties": {
                        "strongest_evidence": {"type": "string"},
                        "main_gap": {"type": "string"},
                        "why_not_lower": {"type": "string"},
                    },
                    "required": ["strongest_evidence", "main_gap", "why_not_lower"],
                    "additionalProperties": False,
                },
                "dna_score": {
                    "type": "object",
                    "properties": {
                        "strongest_evidence": {"type": "string"},
                        "main_gap": {"type": "string"},
                        "why_not_lower": {"type": "string"},
                    },
                    "required": ["strongest_evidence", "main_gap", "why_not_lower"],
                    "additionalProperties": False,
                },
            },
            "required": ["skill_score", "experience_score", "role_alignment_score", "impact_score", "stability_score", "dna_score"],
            "additionalProperties": False,
        },
    },
    "required": [
        "component_rationales",
        "justification_notes",
        "component_justifications",
    ],
    "additionalProperties": False,
}

EXPERIENCE_BANDS = [
    {
        "name": "EARLY_0_3",
        "label": "0-3 Years",
        "min_years": 0,
        "max_years": 3,
        "expected": {
            "strong_skills": 2,
            "applied_skills": 4,
            "avg_strength": 3.0,
            "cluster_breadth": 3,
            "complexity": 2,
            "leadership": 0,
            "impact_count": 1,
            "role_score": 10,
        },
        "narrative": "Evaluate for hands-on foundations, learning velocity, and evidence of execution.",
    },
    {
        "name": "GROWTH_3_6",
        "label": "3-6 Years",
        "min_years": 3,
        "max_years": 6,
        "expected": {
            "strong_skills": 4,
            "applied_skills": 6,
            "avg_strength": 4.5,
            "cluster_breadth": 4,
            "complexity": 4,
            "leadership": 1,
            "impact_count": 2,
            "role_score": 12,
        },
        "narrative": "Evaluate for independent ownership, stronger delivery depth, and business usefulness.",
    },
    {
        "name": "MID_6_10",
        "label": "6-10 Years",
        "min_years": 6,
        "max_years": 10,
        "expected": {
            "strong_skills": 6,
            "applied_skills": 8,
            "avg_strength": 5.5,
            "cluster_breadth": 5,
            "complexity": 6,
            "leadership": 2,
            "impact_count": 3,
            "role_score": 13,
        },
        "narrative": "Evaluate for robust execution, cross-functional ownership, architecture awareness, and mentoring potential.",
    },
    {
        "name": "LEAD_10_PLUS",
        "label": "10+ Years",
        "min_years": 10,
        "max_years": 999,
        "expected": {
            "strong_skills": 7,
            "applied_skills": 10,
            "avg_strength": 6.0,
            "cluster_breadth": 5,
            "complexity": 8,
            "leadership": 3,
            "impact_count": 4,
            "role_score": 14,
        },
        "narrative": "Evaluate for leadership, system thinking, strategic judgement, and repeatable business impact.",
    },
]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(str(value).strip())
    except Exception:
        return None


def _round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _component_scores_from_dimension_ratings(dimension_ratings: dict[str, float]) -> dict[str, int]:
    return {
        key: _round_half_up(_clamp((dimension_ratings[rating_key] / 5.0) * MAX_SCORES[key], 0, MAX_SCORES[key]))
        for key, rating_key in RATING_TO_COMPONENT.items()
    }


def _ratio_score(actual: float, expected: float, max_points: int, floor_ratio: float = 0.0) -> int:
    if expected <= 0:
        return max_points if actual > 0 else 0
    ratio = actual / expected
    ratio = _clamp(ratio, floor_ratio, 1.2)
    return int(round(min(1.0, ratio) * max_points))


def _experience_band(total_years: float) -> dict[str, Any]:
    for band in EXPERIENCE_BANDS:
        if band["min_years"] <= total_years < band["max_years"]:
            return band
    return EXPERIENCE_BANDS[-1]


_DEPTH_WEIGHTS = {"EXPERT": 3.0, "DEEP": 2.0, "APPLIED": 1.2, "WEAK": 0.5, "MENTION": 0.2}
_RECENCY_MULT = {"RECENT": 1.2, "MODERATE": 1.0, "DATED": 0.8}


def _depth_weighted_score(evidence_map: dict[str, Any], top12_skills: list[dict[str, Any]], band_expected_strong: float) -> float:
    """Compute depth-weighted skill score normalized to 30 points."""
    total = 0.0
    for skill in top12_skills:
        level = skill.get("evidence_level", "MENTION")
        recency = skill.get("recency", "MODERATE")
        arch = skill.get("architecture_signal", False)
        dw = _DEPTH_WEIGHTS.get(level, 0.2)
        rm = _RECENCY_MULT.get(recency, 1.0)
        arch_bonus = 1.3 if arch else 1.0
        total += dw * rm * arch_bonus
    # Normalize: expected max ≈ band_expected_strong * 3.0 (expert) * 1.2 (recent) * 1.3 (arch)
    expected_max = max(band_expected_strong * 3.0 * 1.2 * 1.3, 1.0)
    return round(min(30.0, (total / expected_max) * 30.0), 2)


def _domain_fit_score(experience: dict[str, Any], top_role_family: str) -> int:
    """Return 1-5 domain fit score. Neutral (3) for role-agnostic roles."""
    if top_role_family not in DOMAIN_FIT_MAP:
        return 3
    mapping = DOMAIN_FIT_MAP[top_role_family]
    candidate_domains = set(experience.get("domain_tags", []))
    if any(d in candidate_domains for d in mapping.get("required", [])):
        return 5
    if any(d in candidate_domains for d in mapping.get("preferred", [])):
        return 3
    return 1  # domain mismatch penalty


def _banded_inputs(evidence_map, semantic, experience, dna, education=None):
    ranked_skills = sorted(
        [m for m in evidence_map.values() if m.get("evidence_level") != "NONE"],
        key=lambda m: (
            ["NONE", "MENTION", "WEAK", "APPLIED", "DEEP", "EXPERT"].index(m.get("evidence_level", "NONE")),
            m.get("matched_context_count", 0),
            m.get("years_of_usage", 0),
        ),
        reverse=True,
    )
    skills = ranked_skills[:12]
    strong = sum(1 for m in skills if m["evidence_level"] in {"DEEP", "EXPERT"})
    applied = sum(1 for m in skills if m["evidence_level"] == "APPLIED")
    weak = sum(1 for m in skills if m["evidence_level"] in {"MENTION", "WEAK"})
    breadth = len({m.get("cluster") for m in skills if m.get("cluster")})
    recent_hits = sum(1 for m in skills if m.get("recency") == "RECENT")
    architecture_hits = sum(1 for m in skills if m.get("architecture_signal"))
    avg_strength = round(sum(m.get("strength_score", 0) for m in skills) / max(len(skills), 1), 2)
    role_scores = semantic.get("role_family_scores", [])
    top_role = role_scores[0] if role_scores else {"role_family": "UNKNOWN", "score": 0, "must_have_hits": 0, "matched_clusters": [], "title_bonus": 0}
    years = experience.get("total_experience_years", 0)
    band = _experience_band(years)
    return {
        "experience_band": band,
        "strong_skills": strong,
        "applied_skills": applied,
        "weak_skills": weak,
        "cluster_breadth": breadth,
        "recent_skill_hits": recent_hits,
        "architecture_skill_hits": architecture_hits,
        "average_skill_strength": avg_strength,
        "top_role": top_role,
        "business_impact_count": experience.get("impact_count", len(experience.get("business_impacts", []))),
        "experience_years": years,
        "complexity_signal_score": experience.get("complexity_signal_score", 0),
        "leadership_signal_score": experience.get("leadership_signal_score", 0),
        "decision_maker": experience.get("decision_maker", False),
        "client_facing": experience.get("client_facing", False),
        "international_exposure": experience.get("international_exposure", False),
        "progression": experience.get("progression", False),
        "fast_learner": experience.get("fast_learner", False),
        "base_stability_score": experience.get("stability_score", 0),
        "career_trajectory_score": experience.get("career_trajectory_score", 2),
        "mobility_signal": experience.get("mobility_signal"),
        "loyalty_signal": experience.get("loyalty_signal"),
        "dna": dna,
        "education": education or {},
        "scored_skill_window": len(skills),
    }


def _fallback_component_rationales(inputs: dict[str, Any]) -> dict[str, str]:
    """Text-only fallbacks for when the LLM explanation call fails. Not used for numeric scoring."""
    top_role = str(inputs["top_role"].get("role_family", "UNKNOWN")).replace("_", " ")
    return {
        "skill_score": (
            "The resume shows broad, credible technical evidence across multiple relevant areas."
            if inputs["strong_skills"] >= 6 else
            "The resume shows meaningful hands-on technical evidence, but depth is more selective."
            if inputs["strong_skills"] >= 3 else
            "Some relevant skill evidence is present, but depth signal is still developing."
        ),
        "experience_score": (
            "The profile reads like an independently operating contributor with visible delivery maturity."
            if inputs["experience_years"] >= 3 and inputs["complexity_signal_score"] >= 4 else
            "The experience shows some credible ownership but overall scope needs deeper validation."
        ),
        "role_alignment_score": f"The strongest directional fit remains {top_role.title()} based on resume evidence.",
        "impact_score": (
            "Business usefulness is visible, though repeatable quantified impact needs more proof."
            if inputs["client_facing"] or inputs["international_exposure"] else
            "Business impact is harder to verify from the resume alone."
        ),
        "stability_score": (
            "Career progression appears reasonably stable for the observed tenure."
            if inputs["progression"] else
            "The trajectory is plausible, though progression signal is not especially strong."
        ),
        "dna_score": f"The operating style reads most like a {str(inputs['dna'].get('primary_dna', 'HYBRID')).lower().replace('_', ' ')} profile.",
    }


def _fallback_justification_notes(inputs: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "skill_score": [f"{inputs['strong_skills']} stronger skills surfaced in the scored window."],
        "experience_score": [f"{inputs['experience_years']} years with complexity signal {inputs['complexity_signal_score']}."],
        "role_alignment_score": [f"Top fit is {str(inputs['top_role'].get('role_family', 'UNKNOWN')).replace('_', ' ')}."],
        "impact_score": [f"{inputs['business_impact_count']} quantified impact markers were found."],
        "stability_score": ["Progression and tenure pattern were used as stability cues."],
        "dna_score": [f"Operating style currently reads as {inputs['dna'].get('primary_dna', 'UNKNOWN')}."],
    }


def _fallback_component_justifications(inputs: dict[str, Any]) -> dict[str, dict[str, str]]:
    top_role = str(inputs["top_role"].get("role_family", "UNKNOWN")).replace("_", " ").title()
    return {
        "skill_score": {"strongest_evidence": f"{inputs['strong_skills']} stronger skills surfaced.", "main_gap": "Depth breadth still needs sharper proof.", "why_not_lower": "Enough repeated technical evidence present."},
        "experience_score": {"strongest_evidence": f"{inputs['experience_years']} years, complexity {inputs['complexity_signal_score']}.", "main_gap": "Leadership range not fully proven.", "why_not_lower": "Real delivery scope and problem complexity visible."},
        "role_alignment_score": {"strongest_evidence": f"Strongest fit: {top_role}.", "main_gap": "Not perfect across every must-have cluster.", "why_not_lower": "Role match supported beyond isolated keywords."},
        "impact_score": {"strongest_evidence": f"{inputs['business_impact_count']} quantified impact markers.", "main_gap": "Outcomes not quantified clearly enough.", "why_not_lower": "Delivery usefulness signals present."},
        "stability_score": {"strongest_evidence": "Trajectory and progression directionally healthy.", "main_gap": "Long-tenure or advancement proof limited.", "why_not_lower": "Career movement reads as credible."},
        "dna_score": {"strongest_evidence": f"Operating style: {inputs['dna'].get('primary_dna', 'UNKNOWN')}.", "main_gap": "Pattern still mixed rather than overwhelmingly one-sided.", "why_not_lower": "Enough evidence of repeatable working style."},
    }


def _call_llm_score(payload: dict[str, Any]) -> dict[str, Any] | None:
    if os.getenv("ENABLE_LLM_SCORING", "true").lower() != "true":
        return None
    if not provider_enabled("ENABLE_LLM_SCORING"):
        return None
    model_name = scoring_model("google/gemma-3-27b-it:free")
    compact_mode = llm_provider() == "ollama"
    compact_payload = payload
    if compact_mode:
        compact_payload = {
            "experience_band": {
                "label": payload.get("experience_band", {}).get("label"),
                "narrative": payload.get("experience_band", {}).get("narrative"),
            },
            "top_role_family": payload.get("top_role_family"),
            "role_family_scores": [
                {
                    "role_family": item.get("role_family"),
                    "score": item.get("score"),
                }
                for item in payload.get("role_family_scores", [])[:3]
            ],
            "skill_summary": payload.get("skill_summary"),
            "top_skills": [
                {
                    "skill": skill.get("skill"),
                    "cluster": skill.get("cluster"),
                    "evidence_level": skill.get("evidence_level"),
                    "depth_label": skill.get("depth_label"),
                    "years_of_usage": skill.get("years_of_usage"),
                    "recency": skill.get("recency"),
                    "matched_context_count": skill.get("matched_context_count"),
                    "project_contexts": skill.get("project_contexts", [])[:2],
                }
                for skill in payload.get("top_skills", [])[:6]
            ],
            "experience_summary": payload.get("experience_summary"),
            "semantic_summary": {
                "skill_consistency_score": payload.get("semantic_summary", {}).get("skill_consistency_score"),
                "weak_skill_count": payload.get("semantic_summary", {}).get("weak_skill_count"),
                "inferred_skills": payload.get("semantic_summary", {}).get("inferred_skills", [])[:4],
            },
            "dna": {
                "primary_dna": payload.get("dna", {}).get("primary_dna"),
            },
            "judge_guidance": payload.get("judge_guidance"),
        }
    prompt = (
        "You are a recruiter-grade evaluation engine. Judge the candidate relative to the provided experience band rubric. "
        "Use the evidence packets as your grounding layer. "
        "Decide the scoring and depth judgement yourself; do not follow any deterministic formula. "
        "Rate each core dimension on a 0 to 5 scale, assign confidence, and then produce an overall recruiter score from 0 to 100. "
        "Also return legacy component scores for compatibility, but they should reflect your judgment, not extractor math. "
        "Scoring rubric: 0=no evidence, 1=mention only, 2=foundational, 3=applied, 4=strong, 5=rare expert/architect-level. "
        "A 5/5 should be rare and only used when recent repeated evidence, clear ownership, and strong complexity are all present. "
        "If business proof is thin or evidence is inferred rather than explicit, reduce the score or confidence. "
        "Keep benchmark_summary under 24 words, benchmark_definition under 20 words, and rationale under 28 words. "
        "Return ONLY valid JSON with keys: skill_score, experience_score, role_alignment_score, impact_score, stability_score, dna_score, "
        "dimension_ratings, dimension_confidence, total_score, overall_band, benchmark_summary, benchmark_definition, rationale. "
        "overall_band must be one of EXCEPTIONAL, STRONG, GOOD, MODERATE, REVIEW. "
        "Confidence values must be one of HIGH, MEDIUM, LOW.\n\n"
        + json.dumps(compact_payload, separators=(",", ":") if compact_mode else None, indent=None if compact_mode else 2)
    )
    messages = [{"role": "system", "content": SCORING_SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": prompt})
    return call_llm_json(
        model_name=model_name,
        messages=messages,
        max_tokens=240 if compact_mode else 380,
        schema=MINIMAL_SCORECARD_SCHEMA,
    )


def _call_llm_score_explanations(payload: dict[str, Any], judged_scorecard: dict[str, Any]) -> dict[str, Any] | None:
    if os.getenv("ENABLE_LLM_SCORING", "true").lower() != "true":
        return None
    if not provider_enabled("ENABLE_LLM_SCORING"):
        return None
    model_name = scoring_model("google/gemma-3-27b-it:free")
    compact_mode = llm_provider() == "ollama"
    explanation_payload = {
        "top_role_family": payload.get("top_role_family"),
        "experience_band": payload.get("experience_band", {}).get("label"),
        "scored_result": {
            "dimension_ratings": judged_scorecard.get("dimension_ratings", {}),
            "overall_band": judged_scorecard.get("overall_band"),
            "total_score": judged_scorecard.get("total_score"),
        },
        "top_skills": payload.get("top_skills", [])[:5],
        "experience_summary": payload.get("experience_summary", {}),
        "semantic_summary": payload.get("semantic_summary", {}),
        "dna": payload.get("dna", {}),
    }
    prompt = (
        "Write short recruiter-style explanations for an already-scored candidate. "
        "Return ONLY valid JSON with keys component_rationales and justification_notes. "
        "component_rationales must contain skill_score, experience_score, role_alignment_score, impact_score, stability_score, dna_score. "
        "Each component rationale must stay under 16 words. "
        "justification_notes must use the same keys and each value must be a list with exactly 1 short note string under 12 words. "
        "Also return component_justifications using the same keys. "
        "Each component_justifications entry must include strongest_evidence, main_gap, and why_not_lower. "
        "Each of those fields must stay under 18 words and be specific to the resume evidence. "
        "Mention strongest evidence and one limitation where possible.\n\n"
        + json.dumps(explanation_payload, separators=(",", ":") if compact_mode else None, indent=None if compact_mode else 2)
    )
    return call_llm_json(
        model_name=model_name,
        messages=[{"role": "system", "content": SCORING_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        max_tokens=180 if compact_mode else 220,
        schema=EXPLANATION_SCHEMA,
    )


def _validated_llm_scorecard(judged: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(judged, dict):
        from llm_client import _set_last_llm_error, get_last_llm_error  # type: ignore
        if not get_last_llm_error():
            _set_last_llm_error("LLM scorecard validation failed: parsed payload was not a JSON object.")
        return None
    try:
        dimension_ratings = judged.get("dimension_ratings")
        dimension_confidence = judged.get("dimension_confidence")
        if not isinstance(dimension_ratings, dict) or not isinstance(dimension_confidence, dict):
            from llm_client import _set_last_llm_error  # type: ignore
            _set_last_llm_error("LLM scorecard validation failed: dimension_ratings or dimension_confidence missing.")
            return None
        normalized_ratings = {}
        for key, value in dimension_ratings.items():
            value = _coerce_float(value)
            if value is None:
                from llm_client import _set_last_llm_error  # type: ignore
                _set_last_llm_error(f"LLM scorecard validation failed: dimension_ratings['{key}'] was not numeric.")
                return None
            if value < 0 or value > 5:
                from llm_client import _set_last_llm_error  # type: ignore
                _set_last_llm_error("LLM scorecard validation failed: dimension rating out of range.")
                return None
            normalized_ratings[key] = value
        allowed_confidence = {"HIGH", "MEDIUM", "LOW"}
        for key, value in dimension_confidence.items():
            normalized = str(value or "").strip().upper()
            if normalized not in allowed_confidence:
                from llm_client import _set_last_llm_error  # type: ignore
                _set_last_llm_error(f"LLM scorecard validation failed: dimension_confidence['{key}'] must be HIGH, MEDIUM, or LOW.")
                return None
            dimension_confidence[key] = normalized
        component_scores = _component_scores_from_dimension_ratings(normalized_ratings)
        benchmark_summary = str(judged.get("benchmark_summary") or "").strip()
        if not benchmark_summary:
            from llm_client import _set_last_llm_error  # type: ignore
            _set_last_llm_error("LLM scorecard validation failed: benchmark_summary missing.")
            return None
        rationale = str(judged.get("rationale") or "").strip()
        if sum(component_scores.values()) == 0 and not rationale:
            from llm_client import _set_last_llm_error  # type: ignore
            _set_last_llm_error("LLM scorecard validation failed: scorecard was empty and rationale missing.")
            return None
        return {
            **judged,
            **component_scores,
            "benchmark_summary": benchmark_summary,
            "rationale": rationale,
            "dimension_ratings": normalized_ratings,
            "dimension_confidence": dimension_confidence,
            "model_total_score_raw": _coerce_float(judged.get("total_score")),
            "derived_total_score": sum(component_scores.values()),
        }
    except Exception:
        from llm_client import _set_last_llm_error  # type: ignore
        _set_last_llm_error("LLM scorecard validation failed: unable to coerce returned values into expected schema.")
        return None


def _score_with_llm(evidence_map, semantic, experience, dna, education=None):
    inputs = _banded_inputs(evidence_map, semantic, experience, dna, education)
    band = inputs["experience_band"]
    top_skill_summaries = []
    for skill in sorted(
        evidence_map.values(),
        key=lambda x: (
            ["NONE", "MENTION", "WEAK", "APPLIED", "DEEP", "EXPERT"].index(x.get("evidence_level", "NONE")),
            x.get("years_of_usage", 0),
            x.get("matched_context_count", 0),
        ),
        reverse=True,
    )[:8]:
        top_skill_summaries.append({
            "skill": skill.get("skill"),
            "cluster": skill.get("cluster"),
            "evidence_level": skill.get("evidence_level"),
            "depth_label": skill.get("depth_label"),
            "years_of_usage": skill.get("years_of_usage"),
            "recency": skill.get("recency"),
            "matched_context_count": skill.get("matched_context_count"),
            "project_contexts": skill.get("project_contexts", []),
            "evidence_reasons": skill.get("reasons", [])[:3],
            "evidence_samples": [
                {
                    "company": ctx.get("company"),
                    "title": ctx.get("title"),
                    "evidence_level": ctx.get("evidence_level"),
                    "project_type": ctx.get("project_type"),
                }
                for ctx in skill.get("contexts", [])[:2]
            ],
        })
    payload = {
        "experience_band": band,
        "evidence_packets": build_evidence_packets(
            overview={"name": "", "location": "", "profile_summary": ""},
            evidence_map=evidence_map,
            semantic=semantic,
            experience=experience,
            dna=dna,
            bert_priors={
                "role_family_prior": semantic.get("bert_role_family_prior", {}),
                "dna_prior": dna.get("bert_dna_prior", {}),
                "project_type_priors": [
                    {
                        "company": item.get("company"),
                        "title": item.get("title"),
                        "predicted_project_type": item.get("project_type_prior"),
                        "confidence": item.get("project_type_prior_confidence"),
                    }
                    for item in experience.get("project_types", [])
                    if item.get("project_type_prior")
                ],
            },
        ),
        "top_role_family": inputs["top_role"].get("role_family"),
        "top_role_score": inputs["top_role"].get("score"),
        "role_family_scores": semantic.get("role_family_scores", [])[:5],
        "skill_summary": {
            "evidence_backed_skills": inputs["strong_skills"] + inputs["applied_skills"],
            "skills_needing_validation": inputs["weak_skills"],
            "cluster_breadth": inputs["cluster_breadth"],
            "recent_skill_hits": inputs["recent_skill_hits"],
        },
        "top_skills": top_skill_summaries,
        "experience_summary": {
            "experience_years": inputs["experience_years"],
            "complexity_signal_score": inputs["complexity_signal_score"],
            "leadership_signal_score": inputs["leadership_signal_score"],
            "decision_maker": inputs["decision_maker"],
            "client_facing": inputs["client_facing"],
            "international_exposure": inputs["international_exposure"],
            "progression": inputs["progression"],
            "fast_learner": inputs["fast_learner"],
            "business_impact_count": inputs["business_impact_count"],
            "has_verbal_impacts": experience.get("has_verbal_impacts", False),
            "career_trajectory_score": inputs.get("career_trajectory_score", 2),
            "stability_score": inputs["base_stability_score"],
            "domain_tags": experience.get("domain_tags", []),
            "domain_fit_signal": _domain_fit_score(experience, inputs["top_role"].get("role_family", "UNKNOWN")),
            "mobility_signal": experience.get("mobility_signal"),
            "loyalty_signal": experience.get("loyalty_signal"),
            "average_tenure_months": experience.get("average_tenure_months"),
            "project_types": experience.get("project_types", [])[:5],
            "yearly_skill_learning": experience.get("yearly_skill_learning", [])[:5],
        },
        "education_summary": {
            "highest_institute_tier": inputs["education"].get("highest_institute_tier"),
            "highest_education_score": inputs["education"].get("highest_education_score"),
            "strongest_course_value_signal": inputs["education"].get("strongest_course_value_signal"),
            "has_tech_degree": inputs["education"].get("has_tech_degree", False),
            "education_gap_flag": inputs["education"].get("education_gap_flag"),
            "course_families": inputs["education"].get("course_families", [])[:4],
            "gpa_summary": inputs["education"].get("gpa_summary", [])[:3],
        },
        "semantic_summary": {
            "skill_consistency_score": semantic.get("skill_consistency_score"),
            "weak_skill_count": semantic.get("weak_skill_count"),
            "inferred_skills": semantic.get("inferred_skills", [])[:4],
        },
        "dna": {
            "primary_dna": dna.get("primary_dna"),
            "secondary_dna": dna.get("secondary_dna"),
            "dna_confidence": dna.get("dna_confidence"),
            "dna_strength_pct": dna.get("dna_strength_pct"),
            "dna_fit": dna.get("dna_fit"),
            "dna_reason": dna.get("dna_reason"),
            "consulting_score": dna.get("consulting_score", 0),
            "product_score": dna.get("product_score", 0),
            "domain_specialist_score": dna.get("domain_specialist_score", 0),
            "research_score": dna.get("research_score", 0),
            "platform_infra_score": dna.get("platform_infra_score", 0),
        },
        "judge_guidance": {
            "instructions": [
                "Judge from evidence quality, ownership, scope, and outcome credibility.",
                "Do not mirror internal numeric fields as final truth.",
                "Treat usage years as a soft signal, not a guaranteed fact.",
                "Use recruiter-style reasoning and calibrate to seniority.",
                "Dimension ratings may use 0.5 steps when evidence sits between bands.",
                "For every dimension, explain the strongest supporting evidence, the main limitation, and why the score was not lower.",
                "Only use 5.0 for rare cases with repeated recent depth, ownership, complexity, and strong outcome or architecture proof.",
            ]
        },
    }
    judged = _validated_llm_scorecard(_call_llm_score(payload))
    if not judged:
        return None
    explanation = _call_llm_score_explanations(payload, judged)
    fallback_rationales = _fallback_component_rationales(inputs)
    fallback_notes = _fallback_justification_notes(inputs)
    fallback_component_justifications = _fallback_component_justifications(inputs)
    if isinstance(explanation, dict):
        component_rationales = explanation.get("component_rationales") if isinstance(explanation.get("component_rationales"), dict) else fallback_rationales
        justification_notes = explanation.get("justification_notes") if isinstance(explanation.get("justification_notes"), dict) else fallback_notes
        component_justifications = explanation.get("component_justifications") if isinstance(explanation.get("component_justifications"), dict) else fallback_component_justifications
    else:
        component_rationales = fallback_rationales
        justification_notes = fallback_notes
        component_justifications = fallback_component_justifications
    component_scores = {
        key: int(_clamp(float(judged.get(key, 0)), 0, MAX_SCORES[key]))
        for key in MAX_SCORES
    }
    if judged.get("dimension_ratings"):
        component_scores = _component_scores_from_dimension_ratings(judged["dimension_ratings"])
    return _build_scorecard(
        component_scores=component_scores,
        inputs=inputs,
        scoring_mode="llm_benchmark",
        benchmark_summary=str(judged.get("benchmark_summary") or f"LLM judged candidate against the {band['label']} benchmark."),
        benchmark_definition=str(judged.get("benchmark_definition") or f"LLM benchmark for {band['label']}: judge this candidate relative to seniority, evidence quality, ownership, complexity, and business impact rather than fixed thresholds."),
        llm_used=True,
        rationale=str(judged.get("rationale") or ""),
        overall_band=str(judged.get("overall_band") or ""),
        component_rationales=component_rationales,
        justification_notes=justification_notes,
        component_justifications=component_justifications,
        dimension_ratings=judged.get("dimension_ratings") if isinstance(judged.get("dimension_ratings"), dict) else {},
        dimension_confidence=judged.get("dimension_confidence") if isinstance(judged.get("dimension_confidence"), dict) else {},
        llm_total_score=sum(component_scores.values()),
    )


def _band_from_total(total: int) -> str:
    if total >= 85:
        return "EXCEPTIONAL"
    if total >= 75:
        return "STRONG"
    if total >= 65:
        return "GOOD"
    if total >= 50:
        return "MODERATE"
    return "REVIEW"


def _build_scorecard(component_scores: dict[str, int], inputs: dict[str, Any], scoring_mode: str, benchmark_summary: str, llm_used: bool, rationale: str = "", overall_band: str = "", component_rationales: dict[str, Any] | None = None, benchmark_definition: str = "", llm_failure_reason: str = "", dimension_ratings: dict[str, Any] | None = None, dimension_confidence: dict[str, Any] | None = None, llm_total_score: int | None = None, justification_notes: dict[str, Any] | None = None, component_justifications: dict[str, Any] | None = None):
    total = max(0, min(100, llm_total_score if llm_total_score is not None else sum(component_scores.values())))
    top_role = inputs["top_role"]
    band = inputs["experience_band"]
    normalization_explanation = (
        "Each 0-5 AI dimension is converted proportionally into its weighted bucket, and the six weighted buckets sum to 100."
        if dimension_ratings else
        "The six weighted component buckets sum to 100."
    )
    return {
        **component_scores,
        "average_skill_strength": inputs["average_skill_strength"],
        "max_scores": MAX_SCORES,
        "experience_band": {
            "name": band["name"],
            "label": band["label"],
            "narrative": band["narrative"],
        },
        "scoring_mode": scoring_mode,
        "llm_used": llm_used,
        "llm_provider": get_last_llm_provider_used() if llm_used else llm_provider(),
        "llm_failure_reason": llm_failure_reason,
        "benchmark_summary": benchmark_summary,
        "benchmark_definition": benchmark_definition or benchmark_summary,
        "rationale": rationale,
        "component_rationales": component_rationales or {},
        "justification_notes": justification_notes or {},
        "component_justifications": component_justifications or {},
        "dimension_ratings": dimension_ratings or {},
        "dimension_confidence": dimension_confidence or {},
        "score_normalization": {
            "method": "dimension_weighted",
            "explanation": normalization_explanation,
        },
        "component_inputs": {
            "skill_score": {
                "average_skill_strength": inputs["average_skill_strength"],
                "strong_skills": inputs["strong_skills"],
                "applied_skills": inputs["applied_skills"],
                "cluster_breadth": inputs["cluster_breadth"],
                "recent_skill_hits": inputs["recent_skill_hits"],
                "architecture_skill_hits_count": inputs["architecture_skill_hits"],
                "weak_skill_penalty": inputs["weak_skills"],
                "benchmark_expected": band["expected"]["strong_skills"],
            },
            "experience_score": {
                "total_experience_years": inputs["experience_years"],
                "complexity_signal_score": inputs["complexity_signal_score"],
                "leadership_signal_score": inputs["leadership_signal_score"],
                "decision_maker": inputs["decision_maker"],
                "mobility_signal": inputs.get("mobility_signal"),
                "benchmark_expected": band["expected"]["complexity"],
            },
            "role_alignment_score": {
                "top_role_family": top_role.get("role_family", "UNKNOWN"),
                "top_role_score": top_role.get("score", 0),
                "matched_clusters": top_role.get("matched_clusters", []),
                "must_have_hits": top_role.get("must_have_hits", 0),
                "title_bonus": top_role.get("title_bonus", 0),
                "benchmark_expected": band["expected"]["role_score"],
            },
            "impact_score": {
                "business_impact_count": inputs["business_impact_count"],
                "client_facing": inputs["client_facing"],
                "international_exposure": inputs["international_exposure"],
                "benchmark_expected": band["expected"]["impact_count"],
            },
            "stability_score": {
                "base_stability_score": inputs["base_stability_score"],
                "progression": inputs["progression"],
                "fast_learner": inputs["fast_learner"],
                "loyalty_signal": inputs.get("loyalty_signal"),
            },
            "dna_score": {
                "primary_dna": inputs["dna"].get("primary_dna"),
                "secondary_dna": inputs["dna"].get("secondary_dna"),
                "dna_confidence": inputs["dna"].get("dna_confidence"),
                "dna_strength_pct": inputs["dna"].get("dna_strength_pct"),
                "dna_fit": inputs["dna"].get("dna_fit"),
                "dna_reason": inputs["dna"].get("dna_reason"),
                "consulting_score": inputs["dna"].get("consulting_score", 0),
                "product_score": inputs["dna"].get("product_score", 0),
                "domain_specialist_score": inputs["dna"].get("domain_specialist_score", 0),
                "research_score": inputs["dna"].get("research_score", 0),
                "platform_infra_score": inputs["dna"].get("platform_infra_score", 0),
            },
            "education_context": {
                "highest_institute_tier": inputs["education"].get("highest_institute_tier") if isinstance(inputs.get("education"), dict) else None,
                "strongest_course_value_signal": inputs["education"].get("strongest_course_value_signal") if isinstance(inputs.get("education"), dict) else None,
                "education_gap_flag": inputs["education"].get("education_gap_flag") if isinstance(inputs.get("education"), dict) else None,
            },
        },
        "total_score": total,
        "band": overall_band if overall_band in {"EXCEPTIONAL", "STRONG", "GOOD", "MODERATE", "REVIEW"} else _band_from_total(total),
    }


def _score_heuristic_fallback(evidence_map, semantic, experience, dna, education=None):
    """Pure rule-based scorecard — used when BERT_TRAINING_MODE=1 and LLM is disabled.
    Scores are approximate but sufficient for training label generation."""
    inp = _banded_inputs(evidence_map, semantic, experience, dna, education)
    band = inp["experience_band"]

    # skill_score /30: strong skills + applied + recency + breadth
    sk = min(30, round(
        inp["strong_skills"] * 3.0
        + inp["applied_skills"] * 1.5
        + inp["recent_skill_hits"] * 1.0
        + inp["cluster_breadth"] * 0.5
        + inp["architecture_skill_hits"] * 1.0
    ))

    # experience_score /20
    yr = inp["experience_years"]
    ex = min(20, round(
        min(yr / 12.0 * 5, 10)  # up to 10 pts from years (caps at 24 yrs)
        + inp["complexity_signal_score"] * 1.5
        + inp["leadership_signal_score"] * 1.0
        + (2 if inp["progression"] else 0)
        + (1 if inp["decision_maker"] else 0)
    ))

    # role_alignment_score /15
    top_role = inp["top_role"]
    rl = min(15, round(
        (top_role.get("score", 0) / 10.0) * 8
        + top_role.get("must_have_hits", 0) * 1.0
        + (len(top_role.get("matched_clusters", [])) or 0) * 0.5
        + (top_role.get("title_bonus", 0) or 0) * 2
    ))

    # impact_score /15
    im = min(15, round(
        min(inp["business_impact_count"] * 2.0, 10)
        + (2 if inp["decision_maker"] else 0)
        + (2 if inp["client_facing"] else 0)
        + (1 if inp["international_exposure"] else 0)
    ))

    # stability_score /10
    st = min(10, max(0, int(inp["base_stability_score"] * 2)))

    # dna_score /10
    dna_conf = float(dna.get("dna_confidence", 0) or 0)
    dna_str = float(dna.get("dna_strength_pct", 0) or 0) / 100.0
    dn = min(10, round(dna_conf * 5 + dna_str * 5))

    component_scores = {
        "skill_score": sk,
        "experience_score": ex,
        "role_alignment_score": rl,
        "impact_score": im,
        "stability_score": st,
        "dna_score": dn,
    }
    return _build_scorecard(
        component_scores=component_scores,
        inputs=inp,
        scoring_mode="heuristic_fallback",
        benchmark_summary=f"Heuristic scoring (LLM disabled) — {band['label']}.",
        benchmark_definition="Rule-based approximate scores; used for BERT training data only.",
        llm_used=False,
        rationale="",
        overall_band="",
        component_rationales=_fallback_component_rationales(inp),
    )


def compute_score(evidence_map, semantic, experience, dna, education=None):
    """Returns LLM scorecard, or heuristic fallback when BERT_TRAINING_MODE=1."""
    import os
    if os.getenv("BERT_TRAINING_MODE", "0") == "1":
        return _score_heuristic_fallback(evidence_map, semantic, experience, dna, education)
    return _score_with_llm(evidence_map, semantic, experience, dna, education)
