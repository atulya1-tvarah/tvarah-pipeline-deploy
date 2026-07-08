"""LLM-based project type confirmation and experience judgment.

Reuses the skill judgment pattern from llm_resume_judge.py.

Public function:
    judge_projects_llm(project_items, experience, credibility, max_projects=2)
    → list[dict] | None
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from llm_judging_assets import PROJECT_JUDGE_SYSTEM_PROMPT
from llm_client import analysis_model, call_llm_json, call_llm_text, provider_enabled

# ---------------------------------------------------------------------------
# JSON schema for LLM output
# ---------------------------------------------------------------------------

PROJECT_JUDGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project_index": {"type": "integer"},
                    "confirmed_type": {"type": "string"},
                    "complexity_score": {"type": "number"},
                    "verdict_label": {"type": "string"},
                    "era_context": {"type": "string"},
                    "reverse_engineered_scope": {"type": "string"},
                    "scope_assessment": {"type": "string"},
                    "implied_skills": {"type": "array", "items": {"type": "string"}},
                    "claimed_skills_verified": {"type": "array", "items": {"type": "string"}},
                    "skill_gaps_detected": {"type": "array", "items": {"type": "string"}},
                    "green_flags": {"type": "array", "items": {"type": "string"}},
                    "red_flags": {"type": "array", "items": {"type": "string"}},
                    "role_intent": {"type": "string"},
                    "candidate_signal": {"type": "string"},
                    "skill_exhibition_type": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string"},
                    "interview_probe": {"type": "string"},
                },
                "required": [
                    "project_index", "confirmed_type", "complexity_score",
                    "verdict_label", "implied_skills", "reason",
                    "confidence", "interview_probe", "candidate_signal",
                    "role_intent", "skill_exhibition_type",
                ],
            },
        },
        "candidate_assessment": {
            "type": "object",
            "properties": {
                "overall_candidate_signal": {"type": "string"},
                "role_targeting": {"type": "string"},
                "primary_skill_exhibition": {"type": "string"},
                "career_trajectory": {"type": "string"},
                "excellence_indicators": {"type": "array", "items": {"type": "string"}},
                "watch_outs": {"type": "array", "items": {"type": "string"}},
                "recommended_interview_depth": {"type": "string"},
            },
            "required": [
                "overall_candidate_signal", "role_targeting",
                "career_trajectory", "recommended_interview_depth",
            ],
        },
    },
    "required": ["project_judgments", "candidate_assessment"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _build_project_payload(
    project_items: list[dict[str, Any]],
    experience: dict[str, Any],
    credibility: dict[str, Any],
    company_intel_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from datetime import datetime
    role_tier_match = credibility.get("role_tier_match", {})
    current_year = datetime.now().year

    projects_payload = []
    for idx, item in enumerate(project_items, start=1):
        company_name = item.get("company") or ""
        title = item.get("title") or item.get("role") or ""
        start_date = item.get("start_date") or item.get("from") or ""
        end_date = item.get("end_date") or item.get("to") or ""

        # Derive era hint from dates
        start_year = None
        end_year = None
        for raw in [start_date, end_date]:
            if not raw:
                continue
            import re as _re
            m = _re.search(r"(20\d{2}|19\d{2})", str(raw))
            if m:
                yr = int(m.group(1))
                if start_year is None:
                    start_year = yr
                else:
                    end_year = yr
        if start_year and not end_year:
            end_year = current_year

        era_label = ""
        if start_year:
            era_label = f"{start_year}–{end_year or current_year}"

        # Company intelligence
        cmp_intel: dict[str, Any] = {}
        if company_intel_map and company_name:
            cmp_intel = company_intel_map.get(company_name) or {}
        if not cmp_intel and company_name:
            try:
                from company_intelligence import enrich_company_context
                cmp_intel = enrich_company_context(company_name)
            except Exception:
                cmp_intel = {}

        projects_payload.append({
            "project_index": idx,
            "company": company_name,
            "title": title,
            "era": era_label,
            "project_type_rule_detected": item.get("project_type") or "UNKNOWN",
            "project_type_bert_prior": item.get("project_type_prior"),
            "project_type_bert_confidence": item.get("project_type_prior_confidence"),
            "description": (str(item.get("description") or item.get("problem") or ""))[:600],
            "skills_claimed": (item.get("skills") or [])[:15],
            "domain": item.get("domain"),
            "business_impact": item.get("business_impact"),
            "company_intel": {
                "known": cmp_intel.get("known", False),
                "tier": cmp_intel.get("tier"),
                "signal_strength": cmp_intel.get("signal_strength"),
                "domain": cmp_intel.get("domain"),
                "company_type": cmp_intel.get("company_type"),
                "work_type": cmp_intel.get("work_type"),
                "headcount_band": cmp_intel.get("headcount_band"),
                "expected_tech_stack": (cmp_intel.get("skill_db_summary") or [])[:12],
                "culture_signals": (cmp_intel.get("culture_signals") or [])[:4],
                "notes": cmp_intel.get("notes") or "",
            } if cmp_intel else {"known": False, "notes": "Company not in database — use title and description signals only."},
        })

    return {
        "candidate_context": {
            "role_family": role_tier_match.get("role_family"),
            "company_tier": role_tier_match.get("tier"),
            "depth_floor": role_tier_match.get("depth_floor"),
            "total_experience_years": experience.get("total_experience_years"),
            "progression": experience.get("progression"),
            "must_have_gaps": (credibility.get("must_have_gaps") or [])[:5],
            "career_progression_label": (credibility.get("progression_plausibility") or ""),
        },
        "projects": projects_payload,
        "analysis_instructions": (
            "Perform deep reverse-engineering per the system prompt. "
            "Use company_intel to infer what this person MUST have known and done. "
            "The era field tells you what the industry/tech landscape looked like. "
            "project_type_rule_detected and project_type_bert_prior are hints — you may correct them. "
            "Return project_judgments for all project_index values AND a candidate_assessment block."
        ),
    }


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def judge_projects_llm(
    project_items: list[dict[str, Any]],
    experience: dict[str, Any],
    credibility: dict[str, Any],
    max_projects: int = 2,
    company_intel_map: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Use LLM to perform deep reverse-engineered project judgment.

    Args:
        project_items: project_types list from experience (up to max_projects items)
        experience: experience analysis dict
        credibility: output of compute_experience_credibility()
        max_projects: cap number of projects sent to LLM
        company_intel_map: pre-built {company_name: enrich_company_context()} dict

    Returns:
        Dict with keys:
          "project_judgments": list of per-project judgment dicts
          "candidate_assessment": holistic assessment across all projects
        or None if LLM unavailable.
    """
    if os.getenv("ENABLE_LLM_SCORING", "true").lower() != "true":
        return None
    if not provider_enabled("ENABLE_LLM_SCORING"):
        return None
    if not project_items:
        return None

    items = project_items[:max_projects]
    model_name = analysis_model("qwen2.5:14b-instruct")
    compact_mode = os.getenv("LLM_PROVIDER", "").strip().lower() == "ollama"

    payload = _build_project_payload(items, experience, credibility, company_intel_map)

    messages = [
        {"role": "system", "content": PROJECT_JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                separators=(",", ":") if compact_mode else None,
                indent=None if compact_mode else 2,
            ),
        },
    ]

    # Rich output needs more tokens — allow for detailed reverse-engineering
    max_tokens = 400 if compact_mode else 1800

    judged = call_llm_json(
        model_name=model_name,
        messages=messages,
        max_tokens=max_tokens,
        schema=PROJECT_JUDGMENT_SCHEMA,
    )

    if judged and isinstance(judged.get("project_judgments"), list):
        return {
            "project_judgments": judged["project_judgments"],
            "candidate_assessment": judged.get("candidate_assessment") or {},
        }

    # Text fallback
    text_fallback = call_llm_text(
        model_name=model_name,
        system_prompt=PROJECT_JUDGE_SYSTEM_PROMPT,
        user_prompt=(
            "Return ONLY a JSON object with project_judgments array and candidate_assessment object.\n\n"
            + json.dumps(
                payload,
                separators=(",", ":") if compact_mode else None,
                indent=None if compact_mode else 2,
            )
        ),
        max_tokens=max_tokens,
    )
    repaired = _extract_json_dict_from_text(text_fallback)
    if isinstance(repaired, dict) and isinstance(repaired.get("project_judgments"), list):
        return {
            "project_judgments": repaired["project_judgments"],
            "candidate_assessment": repaired.get("candidate_assessment") or {},
        }

    return None
