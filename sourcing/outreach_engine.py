"""Generate personalized cold outreach emails for sourced candidates."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("resume_intelligence.sourcing.outreach_engine")


def generate_outreach(candidate: dict, jd: dict | None = None, role_description: str = "") -> dict[str, str]:
    """Return {subject, body} personalized cold email for a sourced candidate."""
    username = candidate.get("github_username", "")
    name = candidate.get("display_name") or username
    tech_stack = candidate.get("tech_stack") or []
    top_repos = candidate.get("top_repos") or []
    yoe = candidate.get("yoe_proxy", 0)

    # Build context for the LLM
    repo_highlight = ""
    if top_repos:
        r = top_repos[0]
        repo_highlight = f"I noticed your project '{r.get('name', '')}'"
        if r.get("description"):
            repo_highlight += f" — {r['description']}"

    role_line = ""
    if jd:
        role_line = f"Role: {jd.get('title', '')} at {jd.get('company', 'our company')}"
    elif role_description:
        role_line = f"Role: {role_description}"

    system_prompt = (
        "You are a technical recruiter writing a short, personalized cold outreach email. "
        "The email must be professional, specific to the candidate's actual work, and under 120 words. "
        "Reference at least one of their repositories or technologies. "
        "Do NOT use generic phrases like 'I came across your profile'. "
        "Return ONLY valid JSON with keys 'subject' (string) and 'body' (string, with newlines as \\n)."
    )

    user_content = (
        f"Candidate: {name} (GitHub: @{username})\n"
        f"Tech stack: {', '.join(tech_stack[:5]) or 'not specified'}\n"
        f"Estimated experience: {yoe:.0f} years\n"
    )
    if repo_highlight:
        user_content += f"Repo highlight: {repo_highlight}\n"
    if role_line:
        user_content += f"{role_line}\n"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        from llm_client import call_llm_json, primary_model
        model = primary_model("z-ai/glm-4.5-air:free")
        result = call_llm_json(model, messages, max_tokens=400)
        if result and isinstance(result, dict) and result.get("subject") and result.get("body"):
            return {"subject": result["subject"], "body": result["body"]}
    except Exception as exc:
        logger.warning("LLM outreach generation failed: %s", exc)

    # Fallback template
    role_str = (jd.get("title") if jd else role_description) or "an exciting engineering role"
    tech_str = tech_stack[0] if tech_stack else "your technical background"
    return {
        "subject": f"Opportunity for {tech_str} engineer — {role_str}",
        "body": (
            f"Hi {name},\n\n"
            f"Your work on GitHub caught my attention, particularly your expertise in {', '.join(tech_stack[:3]) or 'software engineering'}. "
            f"We're hiring for {role_str} and think your background is a strong fit.\n\n"
            f"Would you be open to a quick 15-minute call to explore this?\n\n"
            f"Best regards"
        ),
    }
