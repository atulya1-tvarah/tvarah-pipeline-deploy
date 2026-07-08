"""Per-question interview answer scoring engine.

Scores a recruiter's question + candidate verbal answer transcript using LLM.
Returns 0-10 score with recruiter-grade structured feedback and rubric parameter mapping.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Theme → rubric parameter mapping
# ---------------------------------------------------------------------------
THEME_TO_RUBRIC_PARAM: dict[str, str] = {
    "skill_depth":            "skill_depth",
    "depth_validation":       "skill_depth",
    "mandatory_depth_probe":  "skill_depth",
    "mandatory_gap":          "skill_depth",
    "skill_recency":          "skill_recency",
    "role_fit":               "career_progression",
    "career_trajectory":      "career_progression",
    "impact":                 "awards_recognition",
    "ownership":              "mentorship_signal",
    "leadership":             "mentorship_signal",
    "mentorship":             "mentorship_signal",
    "stakeholder":            "stakeholder_management",
    "stakeholder_management": "stakeholder_management",
    "international":          "international_exposure",
    "international_exposure": "international_exposure",
    "communication":          "bonus",
    "problem_solving":        "bonus",
    "conceptual_depth":       "bonus",
    "dna_fit":                "bonus",
    "gap_probe":              "skill_list_years",
    "validation":             "career_progression",
    "stability":              "stability",
    "project_quality":        "project_1",
    "education_value":        "course_relevance",
}

# Max points for each rubric parameter (for normalisation)
PARAM_MAX_PTS: dict[str, float] = {
    "skill_depth": 10.0, "skill_list_years": 5.0, "skill_recency": 7.0,
    "yoy_learning": 3.0, "certifications": 2.0, "coding_community": 2.0,
    "unique_skill_combos": 2.0, "bonus": 8.0,
    "career_progression": 4.0, "stakeholder_management": 2.0,
    "mentorship_signal": 3.0, "awards_recognition": 3.0,
    "international_exposure": 2.0, "stability": 4.0, "company_tier": 5.0,
    "project_1": 6.0, "project_2": 6.0,
    "institute_tier": 6.0, "course_relevance": 5.0, "degree_level": 4.0,
}

QUESTION_SCORE_SYSTEM_PROMPT = """
You are a senior technical recruiter scoring a candidate's verbal answer to an interview question.

Score the answer on a 0-10 scale (0.5 steps allowed):
  0-2  : No credible answer, deflection, or clearly no knowledge
  3-4  : Vague or generic — mentions the topic but no concrete project, decision, or outcome
  5-6  : Adequate — one specific example but missing depth, trade-offs, or quantification
  7-8  : Good — specific project, clear personal ownership, mentions design decisions or trade-offs
  9-10 : Excellent — specific evidence, quantified outcome, clear ownership, describes failure/learning, would do differently

Strict deduction rules:
- "We did X" without "I specifically did Y" → -1 point
- No outcome or business impact mentioned → cannot score above 8
- Buzzword-heavy with no technical substance → max 4
- Answer shorter than 3 sentences → max 5
- Candidate deflects or says "I don't remember" → max 3

One-shot scoring example:
Q: "Walk me through a production system you built using Spark."
Weak answer — score 5/10: "We did data processing with Spark at my last company. We had pipelines and it was good."
  → Deductions: "We did" (no I), no outcome, under 3 sentences, buzzword only.
Strong answer — score 8/10: "I owned the ingestion pipeline that processed 2TB daily using Spark Structured Streaming on EMR.
  I redesigned the shuffle partitioning after hitting OOM errors, reducing job time from 4h to 45min.
  Trade-off: we picked EMR over Databricks to keep costs predictable for our scale."
  → Credits: specific role, quantified outcome, design decision, trade-off mentioned.

Return strict JSON only:
{
  "score_0_to_10": float,
  "confidence": "HIGH|MEDIUM|LOW",
  "what_was_strong": "1-2 sentences on the strongest part of the answer",
  "what_was_missing": "1-2 sentences on what would have raised the score",
  "follow_up_probe": "One targeted follow-up question to dig deeper",
  "recruiter_note": "Crisp 1-sentence recruiter-ready note for the hiring brief",
  "evidence_cited": "Specific claim or project from the answer that most supports the score"
}
""".strip()


def score_question_answer(
    question: str,
    theme: str,
    answer_transcript: str,
    skill: str = "",
    candidate_context: str = "",
) -> dict[str, Any]:
    """Score a single question + answer pair. Returns structured score dict."""
    try:
        from llm_client import call_llm_json, analysis_model, provider_enabled  # type: ignore
        if not provider_enabled():
            return _fallback_score(answer_transcript)
    except Exception:
        return _fallback_score(answer_transcript)

    parts = []
    if candidate_context:
        parts.append(f"Candidate context (from resume): {candidate_context}")
    parts.append(f"Interview question: {question}")
    if skill:
        parts.append(f"Skill / area being probed: {skill}")
    parts.append(f"Candidate's verbal answer:\n---\n{answer_transcript.strip()}\n---")

    try:
        result = call_llm_json(
            analysis_model("qwen2.5:14b-instruct"),
            [
                {"role": "system", "content": QUESTION_SCORE_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            max_tokens=600,
        )
        if not isinstance(result, dict):
            return _fallback_score(answer_transcript)
        result["rubric_param"] = THEME_TO_RUBRIC_PARAM.get(theme, "bonus")
        result["theme"] = theme
        result["skill"] = skill
        return result
    except Exception:
        return _fallback_score(answer_transcript)


def _fallback_score(transcript: str) -> dict[str, Any]:
    words = len((transcript or "").split())
    if words < 15:
        score, note = 2.0, "Answer too brief to evaluate."
    elif words < 50:
        score, note = 3.5, "Short answer — needs concrete examples."
    elif words < 120:
        score, note = 5.0, "Moderate answer. LLM unavailable for full depth assessment."
    else:
        score, note = 6.0, "Detailed answer. LLM unavailable — manual review needed."
    return {
        "score_0_to_10": score, "confidence": "LOW",
        "what_was_strong": "N/A (LLM unavailable)",
        "what_was_missing": "N/A (LLM unavailable)",
        "follow_up_probe": "N/A", "recruiter_note": note,
        "evidence_cited": "N/A",
        "rubric_param": "bonus", "theme": "unknown", "skill": "",
    }


def aggregate_call_scores_to_rubric_overrides(
    question_scores: list[dict[str, Any]],
) -> dict[str, float]:
    """Convert per-question 0-10 scores → rubric parameter overrides.

    For each rubric param, averages all question scores that map to it,
    then normalises to the param's max point value.
    Bonus param (communication/PS/domain) caps at 8 pts total.
    """
    param_buckets: dict[str, list[float]] = {}
    for qs in question_scores:
        param = qs.get("rubric_param", "bonus")
        raw = float(qs.get("score_0_to_10") or 5)
        param_buckets.setdefault(param, []).append(raw)

    overrides: dict[str, float] = {}
    for param, scores in param_buckets.items():
        avg = sum(scores) / len(scores)
        max_pts = PARAM_MAX_PTS.get(param, 5.0)
        overrides[param] = round((avg / 10.0) * max_pts, 1)
    return overrides
