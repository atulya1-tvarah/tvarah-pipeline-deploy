"""Parse natural language sourcing queries into structured criteria using LLM."""
from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger("resume_intelligence.sourcing.query_parser")

_DEFAULT_CRITERIA: dict[str, Any] = {
    "skills": [],
    "seniority": "MID",
    "location": "",
    "count": 20,
    "role_family": "",
    "github_languages": [],
}

_SENIORITY_WORDS = {
    "senior": "SENIOR", "sr": "SENIOR", "lead": "SENIOR", "staff": "SENIOR",
    "principal": "SENIOR", "architect": "SENIOR",
    "junior": "JUNIOR", "jr": "JUNIOR", "entry": "JUNIOR", "fresher": "JUNIOR",
    "mid": "MID", "middle": "MID",
}

_LANG_KEYWORDS = [
    "python", "java", "javascript", "typescript", "go", "golang", "rust",
    "c++", "c#", "ruby", "swift", "kotlin", "scala", "r", "php",
]


def _regex_fallback(query: str) -> dict[str, Any]:
    """Best-effort regex parse when LLM is unavailable."""
    q = query.lower()
    criteria = dict(_DEFAULT_CRITERIA)

    # Count
    m = re.search(r"\b(\d+)\b", q)
    if m:
        criteria["count"] = min(int(m.group(1)), 50)

    # Seniority
    for word, level in _SENIORITY_WORDS.items():
        if word in q:
            criteria["seniority"] = level
            break

    # GitHub languages
    langs = [lang for lang in _LANG_KEYWORDS if lang in q]
    if langs:
        criteria["github_languages"] = [l.capitalize() for l in langs[:3]]

    # Skills (naive: extract capitalized tech terms)
    tech_terms = re.findall(r"\b(?:fastapi|django|flask|react|vue|angular|spring|node\.?js|ml|ai|tensorflow|pytorch|kubernetes|docker|aws|gcp|azure|postgres|mongodb|redis|kafka|spark|hadoop|llm|nlp|bert|transformers)\b", q, re.I)
    skills = list({t.upper() for t in tech_terms})
    if not skills:
        # Use the language as skill fallback
        skills = criteria["github_languages"][:]
    criteria["skills"] = skills[:8]

    # Location (simple heuristic)
    location_patterns = [
        r"\bin\s+([\w\s,]+?)(?:\s+with|\s+having|\s+and|\s+who|$)",
        r"\bfrom\s+([\w\s,]+?)(?:\s+with|\s+having|\s+and|\s+who|$)",
        r"\b(bangalore|mumbai|delhi|hyderabad|pune|chennai|kolkata|india|remote|us|uk|canada|australia|singapore|berlin|london)\b",
    ]
    for pat in location_patterns:
        m = re.search(pat, q, re.I)
        if m:
            criteria["location"] = m.group(1).strip().title()
            break

    return criteria


def parse_query(query: str, jd: dict | None = None) -> dict[str, Any]:
    """Return structured criteria from a natural language query.

    Tries LLM first, falls back to regex heuristics.
    """
    system_prompt = (
        "You are a technical recruiting assistant. Parse the given candidate sourcing query "
        "into structured JSON criteria. Return ONLY valid JSON.\n\n"
        "Output schema:\n"
        "{\n"
        '  "skills": [list of required skill/technology strings],\n'
        '  "seniority": "SENIOR" | "MID" | "JUNIOR",\n'
        '  "location": "city or country string, empty if not specified",\n'
        '  "count": integer number of candidates requested (default 20, max 50),\n'
        '  "role_family": "inferred role family, e.g. SOFTWARE_ENGINEERING, DATA_SCIENCE, DEVOPS",\n'
        '  "github_languages": [list of primary programming languages to filter on GitHub]\n'
        "}"
    )
    user_parts = [f"Query: {query}"]
    if jd:
        title = jd.get("title", "")
        skills = jd.get("mandatory_skills", [])
        if title:
            user_parts.append(f"JD title: {title}")
        if skills:
            user_parts.append(f"JD mandatory skills: {', '.join(skills[:10])}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]

    try:
        from llm_client import call_llm_json, primary_model
        model = primary_model("z-ai/glm-4.5-air:free")
        result = call_llm_json(model, messages, max_tokens=400)
        if result and isinstance(result, dict):
            # Normalise and clamp
            result.setdefault("skills", [])
            result.setdefault("seniority", "MID")
            result.setdefault("location", "")
            result.setdefault("count", 20)
            result.setdefault("role_family", "")
            result.setdefault("github_languages", [])
            result["count"] = min(int(result.get("count") or 20), 50)
            result["seniority"] = str(result.get("seniority", "MID")).upper()
            if result["seniority"] not in ("SENIOR", "MID", "JUNIOR"):
                result["seniority"] = "MID"
            return result
    except Exception as exc:
        logger.warning("LLM query parse failed: %s — using regex fallback", exc)

    return _regex_fallback(query)
