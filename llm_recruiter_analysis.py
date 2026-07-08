
from __future__ import annotations
import json, os
from dotenv import load_dotenv
from llm_client import call_llm_text, provider_enabled, summary_model
load_dotenv()

def _call_model(model_name: str, prompt: str):
    return call_llm_text(
        model_name=model_name,
        system_prompt="You are a recruiter-grade hiring analyst. Write a concise hiring note using only the provided structured evidence. Do not hallucinate. Do not explain what the data is for. Do not say phrases like 'purpose of this data' or describe the prompt itself. Write directly for a recruiter. Mention overall fit, strengths, gaps, validation questions, and suggested interview panel.",
        user_prompt=prompt,
        max_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "900")),
    )


def _summary_looks_valid(text: str | None) -> bool:
    if not isinstance(text, str):
        return False
    cleaned = text.strip()
    if len(cleaned) < 120:
        return False
    low = cleaned.lower()
    banned = [
        "purpose of this data",
        "this data represents",
        "let's break down what this data tells us",
        "the data strongly suggests",
    ]
    return not any(phrase in low for phrase in banned)

def _deterministic_recruiter_summary(result):
    overview = result.get("candidate_overview", {})
    semantic = result.get("semantic_analysis", {})
    experience = result.get("experience_analysis", {})
    scorecard = result.get("scorecard", {})
    rubric = result.get("rubric_scorecard", {})
    qualitative = result.get("qualitative_analysis", {})
    top_skills = result.get("skill_analysis", {}).get("top_skills", [])[:6]
    skill_text = ", ".join(
        f"{skill.get('skill')} ({skill.get('depth_label', skill.get('evidence_level'))})"
        for skill in top_skills
        if skill.get("skill")
    ) or "No strong skill evidence available."
    strengths = "; ".join(qualitative.get("strengths", [])[:3]) or "No major strengths were surfaced."
    gaps = "; ".join(qualitative.get("gaps", [])[:3]) or "No major gaps surfaced from the resume alone."
    panel = ", ".join(qualitative.get("panel_suggestion", [])) or "General technical interview"
    # Prefer rubric score when available
    rubric_ready = bool(rubric.get("total_score"))
    display_score = rubric.get("total_score") if rubric_ready else scorecard.get("total_score", "N/A")
    display_band = scorecard.get("band", "N/A")
    score_label = (
        f"{display_score}/100 (Exp {rubric.get('experience_score')}/40 | Skills {rubric.get('skills_score')}/40 | Edu {rubric.get('education_score')}/20)"
        if rubric_ready else str(display_score)
    )
    ai_ready = bool(scorecard.get("llm_used"))
    overall_fit_line = (
        f"Overall fit: {overview.get('name', 'Candidate')} appears best aligned to "
        f"{semantic.get('top_role_family', 'UNKNOWN').replace('_', ' ')} with a rubric score of "
        f"{score_label} ({display_band})."
        if (rubric_ready or ai_ready) else
        f"Overall fit: {overview.get('name', 'Candidate')} appears best aligned to "
        f"{semantic.get('top_role_family', 'UNKNOWN').replace('_', ' ')} based on the current structured evidence."
    )
    benchmarking_line = (
        scorecard.get('benchmark_summary', 'Resume was benchmarked against its experience band.')
        if ai_ready else
        "AI scoring is still pending, so this note is based on deterministic extraction and rule-based evidence review."
    )
    return (
        f"{overall_fit_line}\n\n"
        f"Benchmarking context: {benchmarking_line}\n\n"
        f"Technical depth: strongest observable skills include {skill_text}.\n\n"
        f"Experience and impact: the resume indicates {experience.get('total_experience_years', 0)} years of experience, "
        f"{'client-facing exposure' if experience.get('client_facing') else 'limited explicit client-facing evidence'}, "
        f"and {len(experience.get('business_impacts', []))} quantified impact markers.\n\n"
        f"DNA fit: the operating style is assessed as {result.get('dna_fit', {}).get('primary_dna', 'HYBRID')}.\n\n"
        f"Strengths: {strengths}\n\n"
        f"Risks and validation questions: {gaps}\n\n"
        f"Suggested interview panel: {panel}."
    )

def generate_recruiter_analysis(result):
    if not result.get("scorecard", {}).get("llm_used"):
        return _deterministic_recruiter_summary(result)
    llm_enabled = os.getenv("ENABLE_LLM_SUMMARY", "false").lower() == "true"
    # Merge rubric scores into scorecard for LLM so it references the correct total
    rubric = result.get("rubric_scorecard", {})
    merged_scorecard = dict(result.get("scorecard") or {})
    if rubric.get("total_score"):
        merged_scorecard["total_score"] = rubric["total_score"]
        merged_scorecard["experience_rubric"] = rubric.get("experience_score")
        merged_scorecard["skills_rubric"] = rubric.get("skills_score")
        merged_scorecard["education_rubric"] = rubric.get("education_score")
        merged_scorecard["scoring_note"] = "Score is from the 100pt rubric (Exp 40 + Skills 40 + Edu 20), not the legacy 6-dimension scorecard."
    compact = {
        "candidate_overview": result.get("candidate_overview"),
        "top_role_family": result.get("semantic_analysis", {}).get("top_role_family"),
        "role_family_scores": result.get("semantic_analysis", {}).get("role_family_scores", [])[:4],
        "skill_highlights": result.get("skill_analysis", {}).get("top_skills", [])[:10],
        "experience_analysis": result.get("experience_analysis"),
        "dna_fit": result.get("dna_fit"),
        "scorecard": merged_scorecard,
        "qualitative_analysis": result.get("qualitative_analysis"),
    }
    prompt = "Create a recruiter-ready note with sections: Overall fit, Technical depth, Experience and impact, DNA fit, Risks/validation questions, Suggested interview panel.\n\n" + json.dumps(compact, indent=2)
    primary = summary_model("qwen2.5:14b-instruct")
    fallback = os.getenv("FALLBACK_MODEL", "google/gemma-3-27b-it:free").strip()
    if llm_enabled and provider_enabled():
        for model in [primary, fallback]:
            try:
                out = _call_model(model, prompt)
                if _summary_looks_valid(out):
                    return out
            except Exception:
                pass
    return _deterministic_recruiter_summary(result)
