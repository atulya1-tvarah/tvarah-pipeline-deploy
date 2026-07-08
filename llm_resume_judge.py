from __future__ import annotations

import json
import os
import re
from typing import Any

from llm_judging_assets import ANALYSIS_SYSTEM_PROMPT
from llm_client import analysis_model, call_llm_json, call_llm_text, get_last_llm_error, provider_enabled

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "semantic_analysis": {
            "type": "object",
            "properties": {
                "recruiter_summary": {"type": "string"},
                "top_role_family": {"type": "string"},
                "role_family_rationale": {"type": "string"},
                "consistency_readout": {"type": "string"},
                "inferred_strength_areas": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["recruiter_summary", "top_role_family", "role_family_rationale", "consistency_readout", "inferred_strength_areas"],
            "additionalProperties": False,
        },
        "dna_judgment": {
            "type": "object",
            "properties": {
                "primary_dna": {"type": "string"},
                "confidence": {"type": "string"},
                "reason": {"type": "string"},
                "evidence_used": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["primary_dna", "confidence", "reason", "evidence_used"],
            "additionalProperties": False,
        },
        "qualitative_analysis": {
            "type": "object",
            "properties": {
                "strengths": {"type": "array", "items": {"type": "string"}},
                "gaps": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "panel_suggestion": {"type": "array", "items": {"type": "string"}},
                "recommendation": {"type": "string"},
            },
            "required": ["strengths", "gaps", "risk_flags", "panel_suggestion", "recommendation"],
            "additionalProperties": False,
        },
    },
    "required": ["semantic_analysis", "dna_judgment", "qualitative_analysis"],
    "additionalProperties": False,
}

SKILL_JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "top_skill_judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "score_0_to_5": {"type": "number"},
                    "verdict_label": {"type": "string"},
                    "confidence": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence_used": {"type": "array", "items": {"type": "string"}},
                    "interview_probe": {"type": "string"},
                },
                "required": ["skill", "score_0_to_5", "verdict_label", "confidence", "reason", "evidence_used", "interview_probe"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["top_skill_judgments"],
    "additionalProperties": False,
}


def _analysis_top_skills(skill_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return skill_analysis.get("top_skills", [])[:6]


def _extract_json_dict_from_text(content: str | None) -> dict[str, Any] | None:
    if not isinstance(content, str) or not content.strip():
        return None
    text = content.strip()
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())
    start = text.find("{")
    if start != -1:
        candidates.append(text[start:].strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _skill_payload(top_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "skill": skill.get("skill"),
            "cluster": skill.get("cluster"),
            "source_categories": skill.get("source_categories", []),
            "evidence_level": skill.get("evidence_level"),
            "depth_label": skill.get("depth_label"),
            "weighted_years": skill.get("years_of_usage"),
            "raw_years": skill.get("raw_years_of_usage"),
            "recency": skill.get("recency"),
            "recency_months": skill.get("recency_months"),
            "matched_context_count": skill.get("matched_context_count"),
            "project_contexts": skill.get("project_contexts", [])[:3],
            "project_type_mix": skill.get("project_type_mix", [])[:3],
            "coding_signal": skill.get("coding_signal"),
            "coding_strength_signal": skill.get("coding_strength_signal"),
            "architecture_signal": skill.get("architecture_signal"),
            "open_source_signal": skill.get("open_source_signal"),
            "upskill_signal": skill.get("upskill_signal"),
            "artifact_evidence": skill.get("artifact_evidence", [])[:3],
            "advanced_topic_signals": skill.get("advanced_topic_signals", [])[:4],
            "evidence_roles": [
                {
                    "title": role.get("title"),
                    "company": role.get("company"),
                    "evidence_level": role.get("evidence_level"),
                    "weighted_months": role.get("weighted_months"),
                }
                for role in skill.get("evidence_roles", [])[:3]
            ],
            "bert_depth_prior": skill.get("bert_depth_prior"),
            "bert_depth_confidence": skill.get("bert_depth_confidence"),
            "evidence_reasons": skill.get("reasons", [])[:3],
        }
        for skill in top_skills
    ]


def build_llm_skill_judgments(
    overview: dict[str, Any],
    skill_analysis: dict[str, Any],
    scorecard: dict[str, Any],
) -> dict[str, Any] | None:
    if os.getenv("ENABLE_LLM_SCORING", "true").lower() != "true":
        return None
    if not provider_enabled("ENABLE_LLM_SCORING"):
        return None
    model_name = analysis_model("qwen2.5:14b-instruct")
    compact_mode = os.getenv("LLM_PROVIDER", "").strip().lower() == "ollama"
    top_skills = _analysis_top_skills(skill_analysis)
    payload = {
        "candidate_overview": {
            "name": overview.get("name"),
            "profile_summary": overview.get("profile_summary"),
        },
        "experience_band": scorecard.get("experience_band"),
        "scorecard": {
            "band": scorecard.get("band"),
            "dimension_ratings": scorecard.get("dimension_ratings", {}),
        },
        "top_skills": _skill_payload(top_skills),
        "task": {
            "instructions": [
                "Return ONLY top_skill_judgments in strict JSON.",
                "Judge skill depth from evidence, not keyword counts.",
                "Use recency, project type, coding signal, architecture signal, artifacts, and upskill signal when available.",
                "If the evidence is mostly maintenance or support, reflect that in the verdict.",
                "Use a strict 0-5 rubric with 0.5 steps: 0 none, 1 mention, 2 foundational, 3 applied, 4 strong repeated evidence, 5 rare exceptional depth.",
                "Do not assign 5 unless there is repeated recent depth plus clear ownership or architecture leadership.",
                "Avoid repetitive wording across skills.",
                "Keep each reason under 24 words and evidence_used to at most 2 short bullets.",
            ]
        },
    }
    if compact_mode:
        payload = {
            "candidate_overview": payload["candidate_overview"],
            "experience_band": payload["experience_band"],
            "top_skills": [
                {
                    "skill": skill.get("skill"),
                    "cluster": skill.get("cluster"),
                    "evidence_level": skill.get("evidence_level"),
                    "depth_label": skill.get("depth_label"),
                    "weighted_years": skill.get("weighted_years"),
                    "recency": skill.get("recency"),
                    "matched_context_count": skill.get("matched_context_count"),
                    "coding_strength_signal": skill.get("coding_strength_signal"),
                    "architecture_signal": skill.get("architecture_signal"),
                    "project_type_mix": skill.get("project_type_mix", [])[:2],
                }
                for skill in payload.get("top_skills", [])[:5]
            ],
            "task": payload["task"],
        }
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, separators=(",", ":") if compact_mode else None, indent=None if compact_mode else 2)},
    ]
    judged = call_llm_json(
        model_name=model_name,
        messages=messages,
        max_tokens=220 if compact_mode else 900,
        schema=SKILL_JUDGMENT_SCHEMA,
    )
    if judged:
        return judged
    text_fallback = call_llm_text(
        model_name=model_name,
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
        user_prompt=(
            "Return ONLY a JSON object with top_skill_judgments.\n\n"
            + json.dumps(payload, separators=(",", ":") if compact_mode else None, indent=None if compact_mode else 2)
        ),
        max_tokens=220 if compact_mode else 900,
    )
    repaired = _extract_json_dict_from_text(text_fallback)
    if isinstance(repaired, dict) and isinstance(repaired.get("top_skill_judgments"), list):
        return repaired
    return None


def build_llm_resume_analysis(
    overview: dict[str, Any],
    skill_analysis: dict[str, Any],
    semantic_analysis: dict[str, Any],
    experience_analysis: dict[str, Any],
    dna_fit: dict[str, Any],
    scorecard: dict[str, Any],
) -> dict[str, Any] | None:
    if os.getenv("ENABLE_LLM_SCORING", "true").lower() != "true":
        return None
    if not provider_enabled("ENABLE_LLM_SCORING"):
        return None
    model_name = analysis_model("qwen2.5:14b-instruct")
    compact_mode = os.getenv("LLM_PROVIDER", "").strip().lower() == "ollama"
    top_skills = _analysis_top_skills(skill_analysis)
    payload = {
        "candidate_overview": {
            "name": overview.get("name"),
            "location": overview.get("location"),
            "profile_summary": overview.get("profile_summary"),
        },
        "experience_band": scorecard.get("experience_band"),
        "scorecard": {
            "total_score": scorecard.get("total_score"),
            "band": scorecard.get("band"),
            "benchmark_summary": scorecard.get("benchmark_summary"),
            "dimension_ratings": scorecard.get("dimension_ratings", {}),
            "dimension_confidence": scorecard.get("dimension_confidence", {}),
        },
        "top_skills": _skill_payload(top_skills),
        "semantic_analysis": {
            "top_role_family": semantic_analysis.get("top_role_family"),
            "role_family_scores": semantic_analysis.get("role_family_scores", [])[:5],
            "skill_consistency_score": semantic_analysis.get("skill_consistency_score"),
            "weak_skill_count": semantic_analysis.get("weak_skill_count"),
            "inferred_skills": semantic_analysis.get("inferred_skills", [])[:5],
            "bert_role_family_prior": semantic_analysis.get("bert_role_family_prior", {}),
        },
        "experience_analysis": {
            "total_experience_years": experience_analysis.get("total_experience_years"),
            "titles": experience_analysis.get("titles", [])[:5],
            "companies": experience_analysis.get("companies", [])[:5],
            "progression": experience_analysis.get("progression"),
            "client_facing": experience_analysis.get("client_facing"),
            "international_exposure": experience_analysis.get("international_exposure"),
            "decision_maker": experience_analysis.get("decision_maker"),
            "complexity_signal_score": experience_analysis.get("complexity_signal_score"),
            "leadership_signal_score": experience_analysis.get("leadership_signal_score"),
            "ownership_signal_score": experience_analysis.get("ownership_signal_score"),
            "problem_solving_signal_score": experience_analysis.get("problem_solving_signal_score"),
            "project_types": experience_analysis.get("project_types", [])[:3],
            "company_profiles": experience_analysis.get("company_profiles", [])[:4],
            "domain_tags": experience_analysis.get("domain_tags", [])[:5],
            "dominant_operating_model": experience_analysis.get("dominant_operating_model"),
            "relocation_flexibility_signal": experience_analysis.get("relocation_flexibility_signal"),
            "business_impacts": experience_analysis.get("business_impacts", [])[:6],
        },
        "dna_fit": {
            "primary_dna": dna_fit.get("primary_dna"),
            "bert_dna_prior": dna_fit.get("bert_dna_prior", {}),
        },
        "task": {
            "instructions": [
                "Return JSON with keys semantic_analysis and qualitative_analysis.",
                "semantic_analysis must include recruiter_summary, top_role_family, role_family_rationale, consistency_readout, inferred_strength_areas.",
                "Return dna_judgment with primary_dna, confidence, reason, and evidence_used.",
                "qualitative_analysis must include strengths, gaps, risk_flags, panel_suggestion, recommendation.",
                "Use recruiter-readable language, not extractor jargon.",
                "Keep recruiter_summary under 80 words.",
                "Keep role_family_rationale under 22 words.",
                "Keep consistency_readout under 20 words.",
                "Keep dna_judgment.reason under 20 words.",
                "Return at most 3 strengths, 2 gaps, 2 risk flags, 3 panel suggestions, and 5 top_skill_judgments.",
                "Use a strict skill rubric with 0.5 steps allowed: 0 no evidence, 1 mention only, 2 foundational, 3 applied, 4 strong repeated evidence, 5 rare expert-level evidence.",
                "Do not assign 5 unless the skill shows repeated recent depth plus clear ownership or architecture leadership.",
                "Do not let simple keyword counts decide DNA. Judge consulting, product, hybrid, or domain-specialist from the actual operating pattern across roles.",
                "For every judgment, mention the strongest evidence and the main missing proof or limitation.",
            ]
        },
    }
    if compact_mode:
        payload = {
            "candidate_overview": payload["candidate_overview"],
            "experience_band": payload["experience_band"],
            "scorecard": payload["scorecard"],
            "top_skills": [
                {
                    "skill": skill.get("skill"),
                    "cluster": skill.get("cluster"),
                    "evidence_level": skill.get("evidence_level"),
                    "depth_label": skill.get("depth_label"),
                    "weighted_years": skill.get("weighted_years"),
                    "recency": skill.get("recency"),
                    "matched_context_count": skill.get("matched_context_count"),
                    "coding_strength_signal": skill.get("coding_strength_signal"),
                    "architecture_signal": skill.get("architecture_signal"),
                    "project_type_mix": skill.get("project_type_mix", [])[:2],
                }
                for skill in payload.get("top_skills", [])[:5]
            ],
            "semantic_analysis": {
                "top_role_family": payload.get("semantic_analysis", {}).get("top_role_family"),
                "role_family_scores": payload.get("semantic_analysis", {}).get("role_family_scores", [])[:3],
                "skill_consistency_score": payload.get("semantic_analysis", {}).get("skill_consistency_score"),
                "weak_skill_count": payload.get("semantic_analysis", {}).get("weak_skill_count"),
                "inferred_skills": payload.get("semantic_analysis", {}).get("inferred_skills", [])[:4],
                "bert_role_family_prior": payload.get("semantic_analysis", {}).get("bert_role_family_prior", {}),
            },
            "experience_analysis": {
                "total_experience_years": experience_analysis.get("total_experience_years"),
                "titles": experience_analysis.get("titles", [])[:4],
                "progression": experience_analysis.get("progression"),
                "client_facing": experience_analysis.get("client_facing"),
                "international_exposure": experience_analysis.get("international_exposure"),
                "business_impacts": experience_analysis.get("business_impacts", [])[:4],
                "complexity_signal_score": experience_analysis.get("complexity_signal_score"),
                "leadership_signal_score": experience_analysis.get("leadership_signal_score"),
            },
            "dna_fit": {
                "primary_dna": dna_fit.get("primary_dna"),
                "bert_dna_prior": dna_fit.get("bert_dna_prior", {}),
            },
            "task": payload["task"],
        }
    return call_llm_json(
        model_name=model_name,
        messages=[
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, separators=(",", ":") if compact_mode else None, indent=None if compact_mode else 2)},
        ],
        max_tokens=280 if compact_mode else 420,
        schema=ANALYSIS_SCHEMA,
    )


def get_last_skill_judgment_error() -> str:
    return get_last_llm_error()
