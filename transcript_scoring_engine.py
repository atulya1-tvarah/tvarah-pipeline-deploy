from __future__ import annotations

from typing import Any


def score_transcript_against_resume(
    resume_analysis: dict[str, Any],
    transcript_analysis: dict[str, Any],
    candidate_id: str | None = None,
) -> dict[str, Any]:
    updated_skills = []
    score_delta = 0
    validated_claims = []
    weakened_claims = []

    resume_skill_list = resume_analysis.get("skill_analysis", {}).get("top_skills", resume_analysis.get("skills_analysis", []))
    transcript_skills = transcript_analysis.get("skill_analysis", {}).get("top_skills", transcript_analysis.get("skills_analysis", []))
    resume_skills = {s["skill"]: s for s in resume_skill_list}

    for t_skill in transcript_skills:
        skill_name = t_skill.get("skill")
        resume_skill = resume_skills.get(skill_name)

        if not resume_skill:
            continue

        before = resume_skill.get("evidence_level", "MENTION")
        after = t_skill.get("evidence_level", before)

        updated = dict(resume_skill)
        updated["resume_evidence_level"] = before
        updated["interview_evidence_level"] = after
        updated["interview_notes"] = t_skill.get("top_evidence", "")

        if before != after:
            if after in ["APPLIED", "DEEP", "EXPERT"] and before in ["MENTION", "WEAK"]:
                score_delta += 2
                validated_claims.append(skill_name)
            elif after in ["MENTION", "WEAK"] and before in ["APPLIED", "DEEP", "EXPERT"]:
                score_delta -= 2
                weakened_claims.append(skill_name)

        updated_skills.append(updated)

    panel_result = {
        "updated_skills": updated_skills,
        "validated_claims": validated_claims,
        "weakened_claims": weakened_claims,
        "score_delta": score_delta,
        "final_score": resume_analysis.get("scorecard", {}).get("total_score", resume_analysis.get("final_score", 0)) + score_delta,
        "recommendation_shift": "IMPROVED" if score_delta > 0 else "WEAKENED" if score_delta < 0 else "UNCHANGED",
    }

    # Persist panel stage to candidate score store if candidate_id provided
    if candidate_id:
        try:
            import os
            if os.getenv("ENABLE_NEW_RUBRIC", "false").lower() == "true":
                from candidate_score_store import save_candidate_score
                # Build a rubric-compatible dict for the panel result
                rubric_proxy = {
                    "total_score": panel_result["final_score"],
                    "experience_score": 0,
                    "skills_score": 0,
                    "breakdown": {"panel": panel_result},
                    "reject_flags": [],
                }
                save_candidate_score(
                    candidate_id=candidate_id,
                    rubric_result=rubric_proxy,
                    stage="panel",
                )
        except Exception:
            pass

    return panel_result
