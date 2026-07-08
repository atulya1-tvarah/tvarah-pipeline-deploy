from __future__ import annotations

from typing import Any


def build_telephonic_questions(analysis: dict[str, Any]) -> dict[str, Any]:
    scorecard = analysis.get("scorecard", {})
    score = scorecard.get("total_score", 0)
    if not scorecard.get("llm_used") or score < 65:
        return {"enabled": False, "threshold": 65, "questions": []}

    top_skills = analysis.get("skill_analysis", {}).get("top_skills", [])[:5]
    qualitative = analysis.get("qualitative_analysis", {})
    semantic = analysis.get("semantic_analysis", {})
    experience = analysis.get("experience_analysis", {})
    dna = analysis.get("dna_fit", {})

    questions = []
    role_family = semantic.get("top_role_family", "the target role").replace("_", " ").title()
    questions.append({
        "theme": "Role Fit",
        "question": f"Which one project best proves your fit for {role_family}, and what was the exact business outcome?",
        "why": "Confirms whether the resume’s strongest theme is backed by one anchor project.",
    })

    for skill in top_skills[:3]:
        questions.append({
            "theme": "Skill Depth",
            "question": f"You show strong depth in {skill.get('skill')}. Walk me through one hands-on implementation, the design choices you made, and the trade-offs you handled.",
            "why": "Validates real ownership instead of keyword familiarity.",
        })

    if experience.get("business_impacts"):
        questions.append({
            "theme": "Impact",
            "question": "What measurable outcome are you most proud of, and how much of that impact was directly attributable to your work?",
            "why": "Tests business ownership and attribution clarity.",
        })
    else:
        questions.append({
            "theme": "Impact",
            "question": "Pick one project from your resume and explain how success was measured, even if no metric is explicitly listed.",
            "why": "Checks whether the candidate can translate delivery into business impact.",
        })

    gaps = qualitative.get("gaps", [])
    if gaps:
        questions.append({
            "theme": "Validation",
            "question": f"The resume leaves limited clarity around this area: {gaps[0]}. Can you clarify the depth and recency here?",
            "why": "Targets the biggest ambiguity before investing in later rounds.",
        })

    questions.append({
        "theme": "Ownership",
        "question": "When a model or pipeline underperformed in production or experimentation, how did you debug it and what did you change?",
        "why": "Assesses troubleshooting maturity and operational thinking.",
    })
    questions.append({
        "theme": "Communication",
        "question": f"How would your previous stakeholders describe your working style in one sentence, and why does that align with a {dna.get('primary_dna', 'HYBRID')} profile?",
        "why": "Adds a fast screen for stakeholder fit and operating DNA.",
    })

    return {"enabled": True, "threshold": 65, "questions": questions[:8]}
