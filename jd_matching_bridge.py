"""Bridge between resume_intelligence analysis format and jd_matching engine.

Handles format adaptation + combined scoring for the unified platform.
Phase 4: Added BERT signal extraction, BERT-based score adjustment, and LLM narrative enrichment.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from candidate_analysis_store import load_candidate_analysis
from candidate_score_store import load_candidate_score
from job_posting_store import load_job_posting
from jd_match_store import save_jd_match, load_jd_match
from jd_matching.engine import generate_match
from jd_matching.prompts import MATCH_SYSTEM, MATCH_USER

_EVAL_RUNS_DIR = Path(__file__).resolve().parent / "eval_runs"


def _save_jd_match_eval_snapshot(enriched: dict) -> None:
    """Persist a lightweight eval snapshot of the JD match result to eval_runs/jd_matches/."""
    try:
        run_id = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + f"-jd-match-{uuid4().hex[:8]}"
        )
        snapshot = {
            "run_id": run_id,
            "source": "jd_match",
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "candidate_id": enriched.get("candidate_id"),
            "candidate_name": enriched.get("candidate_name"),
            "jd_id": enriched.get("jd_id"),
            "jd_title": enriched.get("jd_title"),
            "jd_match_score": enriched.get("jd_match_score"),
            "rubric_score": enriched.get("rubric_score"),
            "rubric_stage": enriched.get("rubric_stage"),
            "combined_score": enriched.get("combined_score"),
            "combined_breakdown": enriched.get("combined_breakdown"),
            "recommendation": enriched.get("recommendation"),
            "llm_narrative_used": enriched.get("llm_narrative_used"),
            "recruiter_summary": enriched.get("recruiter_summary"),
            "strengths": enriched.get("strengths"),
            "risks": enriched.get("risks"),
            "skill_match_details": enriched.get("skill_match_details"),
            "experience_gap_display": enriched.get("experience_gap_display"),
        }
        out_dir = _EVAL_RUNS_DIR / "jd_matches"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{run_id}.json").write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Failed to save JD match eval snapshot: %s", exc)

logger = logging.getLogger("resume_intelligence.jd_bridge")

# ---------------------------------------------------------------------------
# Lazy LLM import (only used when LLM is available)
# ---------------------------------------------------------------------------

def _try_import_llm():
    """Return (call_llm_json, summary_model, provider_enabled) or (None, None, None)."""
    try:
        from llm_client import call_llm_json, summary_model, provider_enabled
        return call_llm_json, summary_model, provider_enabled
    except ImportError:
        return None, None, None

# Maps resume_intelligence role families → jd_matching job level labels
ROLE_FAMILY_TO_LEVEL: dict[str, str] = {
    "DATA_ENGINEER": "senior",
    "DATA_SCIENTIST": "senior",
    "ML_ENGINEER": "senior",
    "AGENTIC_AI": "senior",
    "ANALYST": "mid",
    "BI_LEADER": "director",
    "PLATFORM_INFRA": "senior",
    "DOMAIN_SPECIALIST": "mid",
    "CONSULTING": "senior",
}


# ---------------------------------------------------------------------------
# BERT signal extraction
# ---------------------------------------------------------------------------

# Maps analysis depth_label → tier rank (higher = better)
_DEPTH_RANK: dict[str, int] = {
    "AWARENESS": 0,
    "FOUNDATIONAL": 1,
    "HANDS_ON": 2,
    "ADVANCED": 3,
    "ARCHITECT_LEVEL": 4,
}

# Maps JD role_family → expected BERT role family labels
_ROLE_FAMILY_BERT_MAP: dict[str, list[str]] = {
    "DATA_ENGINEER": ["DATA_ENGINEER", "ANALYTICS_ENGINEER", "MLOPS_DATA_SCIENTIST", "PLATFORM_ENGINEER"],
    "DATA_SCIENTIST": ["CORE_DATA_SCIENTIST", "PRODUCT_DATA_SCIENTIST", "GENAI_DATA_SCIENTIST", "APPLIED_SCIENTIST", "RESEARCH_SCIENTIST"],
    "ML_ENGINEER": ["ML_ENGINEER", "MLOPS_DATA_SCIENTIST", "APPLIED_SCIENTIST"],
    "AGENTIC_AI": ["NLP_LLM_ENGINEER", "GENAI_DATA_SCIENTIST", "AI_ARCHITECT", "APPLIED_SCIENTIST"],
    "ANALYST": ["DATA_ANALYST", "QUANT_DATA_ANALYST", "RETAIL_ANALYTICS", "MARKETING_ANALYTICS", "ANALYTICS_ENGINEER"],
    "BI_LEADER": ["ANALYTICS_MANAGER", "DATA_ANALYST", "QUANT_DATA_ANALYST"],
    "PLATFORM_INFRA": ["PLATFORM_ENGINEER", "MLOPS_DATA_SCIENTIST", "DATA_ENGINEER"],
    "DOMAIN_SPECIALIST": ["SUPPLY_CHAIN_ANALYTICS", "RETAIL_ANALYTICS", "MARKETING_ANALYTICS", "GENERALIST_DATA"],
    "CONSULTING": ["GENERALIST_DATA", "DATA_ANALYST", "APPLIED_SCIENTIST"],
}

# Maps analysis dna primary_dna → score adjustment (+/-)
_DNA_TO_JD_FAMILY_FIT: dict[str, list[str]] = {
    "CONSULTING": ["CONSULTING"],
    "PRODUCT": ["DATA_SCIENTIST", "ML_ENGINEER", "AGENTIC_AI"],
    "PLATFORM_INFRA": ["DATA_ENGINEER", "ML_ENGINEER", "PLATFORM_INFRA"],
    "DOMAIN_SPECIALIST": ["ANALYST", "DOMAIN_SPECIALIST", "BI_LEADER"],
}


def extract_bert_signals(analysis: dict) -> dict[str, Any]:
    """Extract BERT + heuristic signals from the candidate analysis JSON.

    Returns a flat dict with all signals needed for scoring adjustments and LLM context.
    """
    sem = analysis.get("semantic_analysis") or {}
    dna = analysis.get("dna_fit") or {}
    exp = analysis.get("experience_analysis") or {}
    skill_a = analysis.get("skill_analysis") or {}
    top_skills = skill_a.get("top_skills") or []

    # Role family
    rf_prior = sem.get("bert_role_family_prior") or {}
    bert_role_family = rf_prior.get("label") or sem.get("top_role_family") or ""
    bert_role_family_conf = float(rf_prior.get("confidence") or 0.0)
    bert_role_family_source = rf_prior.get("source") or "unknown"

    # DNA fit
    dna_prior = dna.get("bert_dna_prior") or {}
    bert_dna_fit = dna_prior.get("label") or dna.get("primary_dna") or ""
    dna_confidence = dna.get("dna_confidence") or "UNKNOWN"
    primary_dna = dna.get("primary_dna") or ""

    # Career progression — BERT prior (may be null if model unavailable)
    cp_prior = sem.get("career_progression_prior") or {}
    bert_career_progression = cp_prior.get("label")  # may be None
    # Fallback: derive from career_trajectory_score (0-5)
    trajectory = exp.get("career_trajectory_score")
    if bert_career_progression is None and trajectory is not None:
        if trajectory >= 4:
            bert_career_progression = "FAST_TRACK"
        elif trajectory >= 3:
            bert_career_progression = "GROWING"
        elif trajectory >= 2:
            bert_career_progression = "LATERAL"
        else:
            bert_career_progression = "DECLINING"

    # Stakeholder management — BERT prior
    sh_prior = sem.get("stakeholder_prior") or {}
    bert_stakeholder = sh_prior.get("label")
    # Fallback from experience flags
    if bert_stakeholder is None:
        leadership = exp.get("leadership_signal_score") or 0
        client_facing = exp.get("client_facing") or False
        if leadership >= 4 or (exp.get("decision_maker")):
            bert_stakeholder = "C_LEVEL"
        elif client_facing:
            bert_stakeholder = "CLIENT_FACING"
        elif leadership >= 2:
            bert_stakeholder = "INTERNAL"
        else:
            bert_stakeholder = "NONE"

    # Mentorship — BERT prior
    mp_prior = sem.get("mentorship_prior") or {}
    bert_mentorship = mp_prior.get("label")
    if bert_mentorship is None:
        leadership = exp.get("leadership_signal_score") or 0
        bert_mentorship = "FORMAL" if leadership >= 4 else "IMPLIED" if leadership >= 2 else "NONE"

    # Skill depth — derive from top_skills depth_label distribution
    depth_ranks = [_DEPTH_RANK.get(s.get("depth_label") or "", 2) for s in top_skills[:15] if isinstance(s, dict)]
    if depth_ranks:
        avg_rank = sum(depth_ranks) / len(depth_ranks)
        if avg_rank >= 3.5:
            skill_depth_tier = "ARCHITECT_LEVEL"
        elif avg_rank >= 2.5:
            skill_depth_tier = "ADVANCED"
        elif avg_rank >= 1.5:
            skill_depth_tier = "HANDS_ON"
        elif avg_rank >= 0.8:
            skill_depth_tier = "FOUNDATIONAL"
        else:
            skill_depth_tier = "AWARENESS"
    else:
        skill_depth_tier = "UNKNOWN"

    # Top skills summary for LLM context (skill + depth_label pairs)
    top_skills_summary = ", ".join(
        f"{s.get('skill', '')} ({s.get('depth_label', '?')})"
        for s in top_skills[:8]
        if isinstance(s, dict) and s.get("skill")
    )

    return {
        "bert_role_family": bert_role_family,
        "bert_role_family_confidence": round(bert_role_family_conf, 2),
        "bert_role_family_source": bert_role_family_source,
        "bert_dna_fit": bert_dna_fit,
        "primary_dna": primary_dna,
        "dna_confidence": dna_confidence,
        "bert_career_progression": bert_career_progression or "UNKNOWN",
        "bert_stakeholder": bert_stakeholder or "NONE",
        "bert_mentorship": bert_mentorship or "NONE",
        "skill_depth_tier": skill_depth_tier,
        "top_skills_summary": top_skills_summary,
        # raw heuristic signals for delta computation
        "career_trajectory_score": int(trajectory) if trajectory is not None else None,
        "leadership_signal_score": int(exp.get("leadership_signal_score") or 0),
        "client_facing": bool(exp.get("client_facing")),
    }


def compute_bert_jd_delta(bert_signals: dict, match_result: dict, jd: dict) -> int:
    """Compute a small BERT-based score adjustment delta (-5 … +5).

    Applied to the final combined_score to reflect ML-derived signals that the
    deterministic engine cannot capture (skill depth quality, career trajectory,
    stakeholder seniority, role-family alignment).
    """
    delta = 0
    jd_role_family = (jd.get("role_family") or "").upper()

    # 1. Skill depth quality
    tier = bert_signals.get("skill_depth_tier", "UNKNOWN")
    if tier in ("ADVANCED", "ARCHITECT_LEVEL"):
        delta += 2
    elif tier in ("FOUNDATIONAL", "AWARENESS"):
        delta -= 2

    # 2. BERT role family alignment with JD role_family
    bert_rf = (bert_signals.get("bert_role_family") or "").upper()
    expected_families = _ROLE_FAMILY_BERT_MAP.get(jd_role_family, [])
    if bert_rf and expected_families:
        if bert_rf in expected_families:
            delta += 2
        else:
            delta -= 1

    # 3. DNA fit alignment with JD role_family
    primary_dna = (bert_signals.get("primary_dna") or "").upper()
    if primary_dna and jd_role_family:
        dna_aligned_jd_families = _DNA_TO_JD_FAMILY_FIT.get(primary_dna, [])
        if jd_role_family in dna_aligned_jd_families:
            delta += 1
        elif dna_aligned_jd_families:  # DNA exists but doesn't match
            delta -= 1

    # 4. Career progression
    progression = bert_signals.get("bert_career_progression", "")
    if progression == "FAST_TRACK":
        delta += 1
    elif progression == "DECLINING":
        delta -= 1

    # 5. Stakeholder management
    stakeholder = bert_signals.get("bert_stakeholder", "")
    if stakeholder in ("CLIENT_FACING", "C_LEVEL"):
        delta += 1

    return max(-5, min(5, delta))


# ---------------------------------------------------------------------------
# Stage notes extraction
# ---------------------------------------------------------------------------

def _collect_stage_notes(candidate_scores: dict) -> tuple[str, str]:
    """Extract recruiter and panel free-text notes from the score stages.

    Returns (recruiter_notes, panel_notes) as plain strings.
    """
    recruiter_notes = ""
    panel_notes = ""
    for stage in (candidate_scores.get("stages") or []):
        breakdown = stage.get("breakdown") or {}
        stage_name = (stage.get("stage") or "").lower()
        # Notes may be stored directly or inside breakdown
        notes = (
            stage.get("notes") or stage.get("recruiter_notes") or stage.get("panel_notes")
            or breakdown.get("notes") or breakdown.get("recruiter_notes") or ""
        )
        if not notes:
            continue
        if stage_name in ("recruiter",):
            recruiter_notes = str(notes)
        elif stage_name in ("panel", "interview"):
            panel_notes = str(notes)
    return recruiter_notes or "None recorded", panel_notes or "None recorded"


# ---------------------------------------------------------------------------
# LLM narrative enrichment
# ---------------------------------------------------------------------------

def generate_llm_narrative(
    match_result: dict,
    bert_signals: dict,
    jd: dict,
    analysis: dict,
    rubric_score: int,
    rubric_stage: str,
    combined_score: int,
    bert_delta: int,
    stage_notes: tuple[str, str],
) -> dict[str, Any] | None:
    """Call LLM to generate enriched recruiter narrative from match data.

    Returns a dict with keys: recruiter_summary, strengths, risks, rationale, stage_note
    Returns None if LLM is unavailable or fails (caller falls back to deterministic output).
    """
    call_llm_json, summary_model_fn, provider_enabled_fn = _try_import_llm()
    if call_llm_json is None or provider_enabled_fn is None:
        return None
    if not provider_enabled_fn():
        return None

    recruiter_notes, panel_notes = stage_notes
    jd_root = jd  # already the raw JD dict from job_posting_store

    # Extract match sub-scores
    skill_details = match_result.get("skill_match_details") or {}
    top_tiles = match_result.get("top_tiles") or {}
    debug = match_result.get("debug") or {}
    integrity = (match_result.get("resume_quality") or {})

    # Build integrity flags string
    integrity_score = integrity.get("integrity_score", debug.get("integrity", {}).get("integrity_score", 100))
    raw_integrity = debug.get("integrity") or {}
    integrity_flags_list = (raw_integrity.get("warning_flags") or []) + (raw_integrity.get("hard_flags") or [])
    integrity_flags = "; ".join(integrity_flags_list[:4]) or "None"

    # Education + company signals from tile reasons
    tile_reasons = match_result.get("tile_reasons") or {}
    education_signal = tile_reasons.get("education_pedigree_reason") or "Not available"
    company_signal = tile_reasons.get("company_pedigree_reason") or "Not available"

    # JD skills summary
    jd_mand = debug.get("jd_mandatory") or []
    jd_opt = debug.get("jd_optional") or []
    desc = jd_root.get("description") or ""
    jd_description_summary = (desc[:300] + "…") if len(desc) > 300 else desc or "Not provided"

    user_prompt = MATCH_USER.format(
        role_title=jd_root.get("title") or match_result.get("jd_title") or "Unknown Role",
        job_level=(jd_root.get("role_family") or ""),
        min_years=jd_root.get("yoe_min") or 0,
        overall_score=match_result.get("overall_score", 0),
        jd_alignment_score=match_result.get("jd_alignment_score", 0),
        skill_recency_score=match_result.get("skill_recency_score", 0),
        domain_score=match_result.get("domain_score", 0),
        evidence_strength=top_tiles.get("evidence_strength", 0),
        job_level_fit=top_tiles.get("job_level_fit", 0),
        experience_gap_display=match_result.get("experience_gap_display", "Unknown"),
        integrity_score=integrity_score,
        recommendation=match_result.get("recommendation", "UNKNOWN"),
        matched_mandatory_count=len(skill_details.get("matched_mandatory") or []),
        matched_mandatory=", ".join(skill_details.get("matched_mandatory") or []) or "None",
        adjacent_mandatory_count=len(skill_details.get("adjacent_mandatory") or []),
        adjacent_mandatory=", ".join(skill_details.get("adjacent_mandatory") or []) or "None",
        missing_mandatory_count=len(skill_details.get("missing_mandatory") or []),
        missing_mandatory=", ".join(skill_details.get("missing_mandatory") or []) or "None",
        matched_optional=", ".join(skill_details.get("matched_optional") or []) or "None",
        bonus_skills=", ".join((skill_details.get("bonus_skills") or [])[:10]) or "None",
        resume_years=debug.get("resume_years_estimate") or 0,
        top_skills_summary=bert_signals.get("top_skills_summary") or "Not available",
        education_signal=education_signal,
        company_signal=company_signal,
        integrity_flags=integrity_flags,
        bert_skill_depth=bert_signals.get("skill_depth_tier", "UNKNOWN"),
        bert_role_family=bert_signals.get("bert_role_family") or "Unknown",
        bert_role_family_confidence=bert_signals.get("bert_role_family_confidence", 0.0),
        bert_dna_fit=bert_signals.get("bert_dna_fit") or bert_signals.get("primary_dna") or "Unknown",
        dna_confidence=bert_signals.get("dna_confidence", "UNKNOWN"),
        bert_career_progression=bert_signals.get("bert_career_progression", "UNKNOWN"),
        bert_stakeholder=bert_signals.get("bert_stakeholder", "NONE"),
        bert_mentorship=bert_signals.get("bert_mentorship", "NONE"),
        bert_delta=bert_delta,
        rubric_stage=rubric_stage,
        rubric_score=rubric_score,
        combined_score=combined_score,
        recruiter_notes=recruiter_notes,
        panel_notes=panel_notes,
        jd_mandatory_skills=", ".join(jd_mand[:20]) or "Not specified",
        jd_optional_skills=", ".join(jd_opt[:15]) or "Not specified",
        jd_description_summary=jd_description_summary,
    )

    messages = [
        {"role": "system", "content": MATCH_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    try:
        model = summary_model_fn("google/gemma-3-27b-it:free")
        result = call_llm_json(model, messages, max_tokens=800)
        if result and isinstance(result, dict) and "recruiter_summary" in result:
            logger.info("LLM narrative generated for JD match (model=%s)", model)
            return result
    except Exception as exc:
        logger.warning("LLM narrative generation failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Format adapters
# ---------------------------------------------------------------------------

def adapt_analysis_to_jd_resume_format(analysis: dict) -> dict:
    """Convert candidate_analyses/{id}.json format → jd_matching resume_data format.

    Prefers _raw_resume if saved alongside the analysis (added in Phase 3 app.py change).
    Falls back to reconstructing from analysis fields.
    """
    # Fast path: raw resume JSON was saved alongside the analysis
    raw_resume = analysis.get("_raw_resume")
    if raw_resume and isinstance(raw_resume, dict):
        # Ensure it has the resume_data wrapper the jd_matching engine expects
        if "resume_data" in raw_resume:
            return raw_resume
        return {"resume_data": raw_resume}

    # Fallback: reconstruct from analysis fields
    overview = analysis.get("candidate_overview") or {}
    exp_analysis = analysis.get("experience_analysis") or {}
    skill_analysis = analysis.get("skill_analysis") or {}
    edu_analysis = analysis.get("education_analysis") or {}

    # Build work_experience_info
    work_experience_info = []
    for item in (exp_analysis.get("items") or exp_analysis.get("entries") or []):
        if not isinstance(item, dict):
            continue
        work_experience_info.append({
            "job_title": item.get("title") or item.get("job_title") or "",
            "company_name": item.get("company") or item.get("company_name") or "",
            "start_date": item.get("start_date") or "",
            "end_date": item.get("end_date") or "",
            "role_description": item.get("description") or item.get("role_description") or item.get("summary") or "",
            "experience_insights": item.get("experience_insights") or "",
        })

    # Build skills_info from skill_analysis
    all_skills = skill_analysis.get("all_skills") or {}
    top_skills = skill_analysis.get("top_skills") or []
    all_skill_names = list(all_skills.keys()) if isinstance(all_skills, dict) else []

    # Try to bucket them by type if type info is available, else put in tools
    programming_languages, frameworks, tools, databases, cloud = [], [], [], [], []
    for skill_entry in top_skills:
        if not isinstance(skill_entry, dict):
            continue
        name = skill_entry.get("skill") or skill_entry.get("name") or ""
        skill_type = (skill_entry.get("type") or skill_entry.get("category") or "").lower()
        if "lang" in skill_type or "python" in name.lower() or "java" in name.lower() or "scala" in name.lower() or "r " in name.lower():
            programming_languages.append(name)
        elif "cloud" in skill_type or name.lower() in {"aws", "azure", "gcp", "google cloud"}:
            cloud.append(name)
        elif "db" in skill_type or "database" in skill_type or name.lower() in {"sql", "mysql", "postgresql", "mongodb", "redshift", "snowflake", "bigquery"}:
            databases.append(name)
        elif "framework" in skill_type or "library" in skill_type:
            frameworks.append(name)
        else:
            tools.append(name)

    # Remaining all_skill_names not in top_skills
    top_skill_names_set = {s.get("skill") or s.get("name") or "" for s in top_skills if isinstance(s, dict)}
    for name in all_skill_names:
        if name not in top_skill_names_set:
            tools.append(name)

    # Build education_info
    education_info = []
    for edu in (edu_analysis.get("entries") or edu_analysis.get("items") or []):
        if not isinstance(edu, dict):
            continue
        education_info.append({
            "degree": edu.get("degree") or edu.get("qualification") or "",
            "institution_name": edu.get("institution") or edu.get("institution_name") or "",
            "field_of_study": edu.get("field") or edu.get("field_of_study") or "",
            "education_level": edu.get("level") or edu.get("education_level") or "",
        })

    # Build domain_data from top_skills contexts
    domain_values = []
    for skill_entry in top_skills:
        if not isinstance(skill_entry, dict):
            continue
        for ctx in (skill_entry.get("contexts") or []):
            if ctx and ctx not in domain_values:
                domain_values.append(ctx)

    contact = overview.get("contact") or {}
    resume_data = {
        "basic_info": {
            "name": overview.get("name") or analysis.get("candidate_name") or "",
            "contact_info": {
                "email": overview.get("email") or contact.get("email") or "",
                "city": overview.get("location") or contact.get("city") or "",
                "country": contact.get("country") or "",
                "primary_phone_number": overview.get("phone") or contact.get("phone") or "",
            },
        },
        "work_experience_info": work_experience_info,
        "skills_info": {
            "programming_languages": list(dict.fromkeys(programming_languages)),
            "frameworks_and_libraries": list(dict.fromkeys(frameworks)),
            "tools_and_platforms": list(dict.fromkeys(tools)),
            "databases": list(dict.fromkeys(databases)),
            "cloud_and_infra": list(dict.fromkeys(cloud)),
            "certified_skills": [],
            "domain_skills": [],
            "soft_skills": [],
        },
        "education_info": education_info,
        "domain_data": {
            "overall_candidate_domain": domain_values[:10],
        },
    }
    return {"resume_data": resume_data}


def adapt_jd_to_jd_matching_format(jd: dict) -> dict:
    """Convert job_posting_store JD format → jd_matching jd_data format."""
    title = jd.get("title") or ""
    role_family = jd.get("role_family") or ""
    job_level = ROLE_FAMILY_TO_LEVEL.get(role_family.upper(), "senior")

    # mandatory_skills in job_postings can be list[str] or list[{skill, weight}]
    raw_mandatory = jd.get("mandatory_skills") or []
    mandatory_skill_names = []
    for item in raw_mandatory:
        if isinstance(item, str):
            mandatory_skill_names.append(item)
        elif isinstance(item, dict):
            name = item.get("skill") or item.get("name") or ""
            if name:
                mandatory_skill_names.append(name)

    # optional / nice_to_have
    raw_optional = jd.get("nice_to_have_skills") or []
    optional_skills = [s if isinstance(s, str) else (s.get("skill") or "") for s in raw_optional]
    optional_skills = [s for s in optional_skills if s]

    # Split mandatory skills into buckets (best-effort heuristic)
    programming_languages, tools, databases, cloud = [], [], [], []
    frameworks = []
    for name in mandatory_skill_names:
        lower = name.lower()
        if lower in {"python", "java", "scala", "r", "sql", "go", "c++", "javascript", "typescript", "rust"}:
            programming_languages.append(name)
        elif lower in {"aws", "azure", "gcp", "google cloud", "databricks", "snowflake"}:
            cloud.append(name)
        elif lower in {"mysql", "postgresql", "mongodb", "redshift", "bigquery", "cassandra", "hbase"}:
            databases.append(name)
        elif lower in {"spark", "kafka", "airflow", "dbt", "pandas", "scikit-learn", "tensorflow", "pytorch",
                       "langchain", "langgraph", "rag", "xgboost", "power bi", "tableau", "qlik"}:
            frameworks.append(name)
        else:
            tools.append(name)

    description = jd.get("description") or ""
    responsibilities = [line.strip() for line in description.split("\n") if line.strip()][:10]

    jd_data = {
        "role_title": title,
        "job_level": job_level,
        "min_years_experience": jd.get("yoe_min"),
        "max_years_experience": jd.get("yoe_max"),
        "mandatory_skills": {
            "programming_languages": programming_languages,
            "frameworks_and_libraries": frameworks,
            "tools": tools,
            "databases": databases,
            "cloud_and_infra": cloud,
        },
        "optional_skills": optional_skills,
        "summary_responsibilities": responsibilities,
        "preferred_dna": jd.get("preferred_dna"),
        "role_family": role_family,
        "description": description,
    }
    return {"jd_data": jd_data}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def get_best_rubric_score(candidate_scores: dict) -> tuple[int, str]:
    """Return (score_100, stage_label) using most complete stage available.

    Priority: panel_score_100 → recruiter_score_100 → resume_score_100
    """
    stages = candidate_scores.get("stages") or []
    panel_score = recruiter_score = resume_score = None
    for stage in stages:
        ss = stage.get("stage_scores") or {}
        if ss.get("panel_score_100") is not None:
            panel_score = int(ss["panel_score_100"])
        if ss.get("recruiter_score_100") is not None:
            recruiter_score = int(ss["recruiter_score_100"])
        if ss.get("resume_score_100") is not None:
            resume_score = int(ss["resume_score_100"])
        # Also accept direct stage scores for older format
        if stage.get("stage") == "panel" and panel_score is None:
            panel_score = int(stage.get("total_score") or 0)
        elif stage.get("stage") == "recruiter" and recruiter_score is None:
            recruiter_score = int(stage.get("total_score") or 0)

    if panel_score is not None:
        return panel_score, "panel"
    if recruiter_score is not None:
        return recruiter_score, "recruiter"
    if resume_score is not None:
        return resume_score, "resume"
    # Last fallback: current_total
    return int(candidate_scores.get("current_total") or 0), "resume"


def compute_combined_score(jd_match_score: int) -> dict[str, Any]:
    """Return JD match score as the recruiter-facing score — pure JD-driven, no rubric blend."""
    return {
        "combined": jd_match_score,
        "jd_weight": 1.0,
        "jd_contribution": jd_match_score,
    }


# ---------------------------------------------------------------------------
# Full match flow
# ---------------------------------------------------------------------------

def match_candidate_to_jd(candidate_id: str, jd_id: str) -> dict[str, Any]:
    """Full integration flow: load, adapt, match, score, save, return enriched result.

    Phase 4 enhancements:
    1. Load analysis from candidate_analyses/{candidate_id}.json
    2. Load JD from job_postings/{jd_id}.json
    3. Adapt both to jd_matching formats
    4. Call jd_matching.engine.generate_match()
    5. Extract BERT signals from analysis → compute BERT adjustment delta
    6. Compute combined_score = jd_match_score + bert_delta  (pure JD-driven, no rubric blend)
    7. Call LLM (if available) for enriched recruiter narrative
    8. Merge LLM narrative into result, fall back to deterministic if LLM fails
    9. Save result via jd_match_store and return
    """
    analysis = load_candidate_analysis(candidate_id)
    if analysis is None:
        raise ValueError(f"No analysis found for candidate_id={candidate_id!r}. Analyse the resume first.")

    jd = load_job_posting(jd_id)
    if jd is None:
        raise ValueError(f"No JD found for jd_id={jd_id!r}.")

    resume_fmt = adapt_analysis_to_jd_resume_format(analysis)
    jd_fmt = adapt_jd_to_jd_matching_format(jd)

    try:
        match_result = generate_match(resume_fmt, jd_fmt)
    except Exception as exc:
        logger.error("JD matching failed candidate=%s jd=%s error=%s", candidate_id, jd_id, exc)
        raise

    jd_match_score = match_result.get("overall_score", 0)

    # Load rubric scores — kept for reference/profile display only, not used in scoring
    candidate_scores = load_candidate_score(candidate_id) or {}
    rubric_score_100, rubric_stage = get_best_rubric_score(candidate_scores)

    # Base score = pure JD match (no rubric blend)
    combined_info = compute_combined_score(jd_match_score)
    base_combined = combined_info["combined"]

    # Extract BERT signals and compute adjustment
    bert_signals = extract_bert_signals(analysis)
    bert_delta = compute_bert_jd_delta(bert_signals, match_result, jd)

    # Apply BERT delta to combined score (clamped 0-100)
    combined_adjusted = max(0, min(100, base_combined + bert_delta))

    candidate_name = (
        analysis.get("candidate_name")
        or (analysis.get("candidate_overview") or {}).get("name")
        or candidate_id
    )

    # Collect stage notes for LLM context
    stage_notes = _collect_stage_notes(candidate_scores)

    # Try LLM narrative enrichment
    llm_narrative = generate_llm_narrative(
        match_result=match_result,
        bert_signals=bert_signals,
        jd=jd,
        analysis=analysis,
        rubric_score=rubric_score_100,
        rubric_stage=rubric_stage,
        combined_score=combined_adjusted,
        bert_delta=bert_delta,
        stage_notes=stage_notes,
    )

    enriched = {
        **match_result,
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "jd_id": jd_id,
        "jd_title": jd.get("title", ""),
        "jd_match_score": jd_match_score,
        "rubric_score": rubric_score_100,
        "rubric_stage": rubric_stage,
        "combined_score": combined_adjusted,
        "combined_breakdown": {
            **combined_info,
            "combined_base": base_combined,
            "bert_delta": bert_delta,
            "rubric_score_ref": rubric_score_100,
            "rubric_stage_ref": rubric_stage,
            "bert_signals_summary": {
                "role_family": bert_signals.get("bert_role_family"),
                "dna_fit": bert_signals.get("primary_dna"),
                "skill_depth_tier": bert_signals.get("skill_depth_tier"),
                "career_progression": bert_signals.get("bert_career_progression"),
                "stakeholder": bert_signals.get("bert_stakeholder"),
            },
        },
        "llm_narrative_used": llm_narrative is not None,
    }

    # Merge LLM narrative (overrides deterministic fields when available)
    if llm_narrative:
        for key in ("recruiter_summary", "strengths", "risks", "rationale"):
            if llm_narrative.get(key):
                enriched[key] = llm_narrative[key]
        if llm_narrative.get("stage_note"):
            enriched["stage_note"] = llm_narrative["stage_note"]

    save_jd_match(jd_id, candidate_id, enriched)
    _save_jd_match_eval_snapshot(enriched)
    logger.info(
        "Matched candidate=%s jd=%s jd_score=%s combined=%s bert_delta=%s rubric_ref=%s(%s) llm=%s",
        candidate_id, jd_id, jd_match_score, combined_adjusted, bert_delta,
        rubric_score_100, rubric_stage, enriched["llm_narrative_used"],
    )
    return enriched
