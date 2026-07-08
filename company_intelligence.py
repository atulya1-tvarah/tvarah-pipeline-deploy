"""Company intelligence lookup module.

Loads company_db.json and provides fuzzy-match lookups used by:
  - llm_experience_judge.py  (company context for LLM project prompts)
  - rubric_engine.py         (company signal enrichment)
  - experience_credibility.py (credibility baseline)

Public API:
    lookup_company(name)          -> dict | None
    get_company_skill_db(name)    -> dict
    get_company_signal(name)      -> str  ("HIGH" | "MEDIUM" | "LOW" | "UNKNOWN")
    get_company_tier(name)        -> int  (1-5, 5=unknown)
    get_implied_skills(name, role_title) -> list[str]
    enrich_company_context(name)  -> dict  (all fields, safe for LLM payload)
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).parent / "company_db.json"

# ---------------------------------------------------------------------------
# DB loader (cached)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_db() -> list[dict[str, Any]]:
    if not _DB_PATH.exists():
        return []
    try:
        return json.loads(_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def reload_db() -> None:
    """Call after programmatically adding to company_db.json at runtime."""
    _load_db.cache_clear()


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, remove punctuation/extra spaces for matching."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _token_overlap(a: str, b: str) -> float:
    """Fraction of tokens in b that appear in a (for partial name match)."""
    ta = set(_normalise(a).split())
    tb = set(_normalise(b).split())
    if not tb:
        return 0.0
    return len(ta & tb) / len(tb)


# ---------------------------------------------------------------------------
# Core lookup
# ---------------------------------------------------------------------------

def lookup_company(name: str | None) -> dict[str, Any] | None:
    """Fuzzy-match company name against the DB. Returns best match or None.

    Matching priority:
      1. Exact normalised name match
      2. Alias match (exact)
      3. Alias prefix / token overlap >= 0.75
      4. Main name token overlap >= 0.75
    """
    if not name or not isinstance(name, str):
        return None
    norm = _normalise(name)
    if not norm:
        return None

    db = _load_db()
    best: dict[str, Any] | None = None
    best_score = 0.0

    for entry in db:
        # 1. Exact name
        if _normalise(entry.get("name", "")) == norm:
            return entry

        # 2. Alias exact
        for alias in entry.get("aliases") or []:
            if _normalise(alias) == norm:
                return entry

        # 3. Alias token overlap
        for alias in entry.get("aliases") or []:
            score = _token_overlap(norm, _normalise(alias))
            if score > best_score:
                best_score = score
                best = entry

        # 4. Name token overlap
        score = _token_overlap(norm, _normalise(entry.get("name", "")))
        if score > best_score:
            best_score = score
            best = entry

    if best_score >= 0.75 and best:
        return best
    return None


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def get_company_skill_db(name: str | None) -> dict[str, list[str]]:
    """Return the skill_db dict (by category) or empty dict if unknown."""
    entry = lookup_company(name)
    if entry is None:
        return {}
    return entry.get("skill_db") or {}


def get_company_signal(name: str | None) -> str:
    """Return signal strength: HIGH | MEDIUM | LOW | UNKNOWN."""
    entry = lookup_company(name)
    if entry is None:
        return "UNKNOWN"
    return entry.get("signal_strength") or "UNKNOWN"


def get_company_tier(name: str | None) -> int:
    """Return company tier 1–5 (5 = not found in DB)."""
    entry = lookup_company(name)
    if entry is None:
        return 5
    tier = entry.get("tier")
    try:
        return int(tier)
    except (TypeError, ValueError):
        return 5


def get_implied_skills(name: str | None, role_title: str = "") -> list[str]:
    """Return a flat list of implied skills based on company DB + role title hint."""
    skill_db = get_company_skill_db(name)
    if not skill_db:
        return []

    all_skills: list[str] = []
    role_lower = (role_title or "").lower()

    # Heuristic: weight categories by role keyword
    priority_keys: list[str] = []
    if any(w in role_lower for w in ("ml", "machine learning", "data scientist", "ai")):
        priority_keys = ["ml", "data", "engineering"]
    elif any(w in role_lower for w in ("data engineer", "platform", "infra", "infrastructure")):
        priority_keys = ["infra", "data", "engineering"]
    elif any(w in role_lower for w in ("consultant", "strategy", "manager", "director")):
        priority_keys = ["strategy", "delivery", "analytics"]
    elif any(w in role_lower for w in ("software", "backend", "developer", "engineer")):
        priority_keys = ["engineering", "infra", "data"]
    else:
        priority_keys = list(skill_db.keys())

    seen: set[str] = set()
    for key in priority_keys:
        for skill in skill_db.get(key) or []:
            if skill not in seen:
                seen.add(skill)
                all_skills.append(skill)

    # Fill remaining categories
    for key, skills in skill_db.items():
        if key not in priority_keys:
            for skill in skills or []:
                if skill not in seen:
                    seen.add(skill)
                    all_skills.append(skill)

    return all_skills[:15]  # cap to avoid overloading LLM prompt


def enrich_company_context(name: str | None) -> dict[str, Any]:
    """Return a safe, LLM-ready dict summarising company intelligence.

    Always returns a dict (never None). Unknown companies get sentinel values.
    """
    entry = lookup_company(name)
    if entry is None:
        return {
            "name": name or "Unknown",
            "known": False,
            "tier": 5,
            "signal_strength": "UNKNOWN",
            "domain": "UNKNOWN",
            "company_type": "UNKNOWN",
            "is_funded": None,
            "funding_stage": None,
            "work_type": "UNKNOWN",
            "skill_db_summary": [],
            "culture_signals": [],
            "notes": "Company not found in database. Score conservatively; probe for actual scope.",
        }

    # Flatten skill_db to a summary list
    skill_db = entry.get("skill_db") or {}
    all_skills = []
    for skills in skill_db.values():
        all_skills.extend(skills or [])

    return {
        "name": entry.get("name"),
        "known": entry.get("known", False),
        "tier": entry.get("tier", 5),
        "signal_strength": entry.get("signal_strength", "UNKNOWN"),
        "domain": entry.get("domain", "UNKNOWN"),
        "sub_domain": entry.get("sub_domain", ""),
        "company_type": entry.get("company_type", "UNKNOWN"),
        "is_funded": entry.get("is_funded"),
        "funding_stage": entry.get("funding_stage"),
        "work_type": entry.get("work_type", "UNKNOWN"),
        "headcount_band": entry.get("headcount_band"),
        "skill_db_summary": all_skills[:20],
        "culture_signals": entry.get("culture_signals") or [],
        "notes": entry.get("notes") or "",
    }


# ---------------------------------------------------------------------------
# Dataset management
# ---------------------------------------------------------------------------

def add_company(entry: dict[str, Any]) -> bool:
    """Append a new company entry to company_db.json.

    Returns True on success. The entry must have at least 'name'.
    Reloads the in-memory cache.
    """
    if not isinstance(entry, dict) or not entry.get("name"):
        return False
    db = list(_load_db())  # copy
    # Deduplicate by name
    existing_names = {_normalise(e.get("name", "")) for e in db}
    if _normalise(entry["name"]) in existing_names:
        return False  # already exists
    db.append(entry)
    try:
        _DB_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
        reload_db()
        return True
    except Exception:
        return False


def update_company(name: str, updates: dict[str, Any]) -> bool:
    """Merge updates into an existing company entry and save.

    Returns True if found and saved.
    """
    db = list(_load_db())
    norm = _normalise(name)
    for i, entry in enumerate(db):
        if _normalise(entry.get("name", "")) == norm:
            db[i] = {**entry, **updates}
            try:
                _DB_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
                reload_db()
                return True
            except Exception:
                return False
    return False
