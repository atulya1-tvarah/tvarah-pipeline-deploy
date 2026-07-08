"""
Institute lookup with LLM-powered fallback for unknown colleges.

Priority:  taxonomy dict  →  local JSON cache  →  LLM search  →  UNKNOWN
Results from LLM are cached in  institute_overrides.json  so the call is made only once per college.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("resume_intelligence.institute_lookup")

_CACHE_PATH = Path("institute_overrides.json")
_cache: dict[str, Any] | None = None   # lazy-loaded

_LLM_SYSTEM = """You are an education database expert specialising in Indian and global universities.
Given an institution name, classify it and return ONLY valid JSON with this exact schema:
{
  "canonical_name": "...",
  "tier": "TIER_1|TIER_2|TIER_3|TIER_4|UNKNOWN",
  "country": "India or actual country",
  "city": "city or state",
  "category": "ENGINEERING_ELITE|ENGINEERING_STRONG|TECH_STRONG|TECH_PRIVATE|MBA_ELITE|MBA_STRONG|GENERAL_STRONG|GENERAL_PRIVATE|RESEARCH_ELITE|RESEARCH_STRONG|ANALYTICS_ELITE|GLOBAL_ELITE|UNKNOWN",
  "streams": ["CS","ECE","EEE","Mech","Civil","Management","Science"],
  "nirf_rank": null,
  "source": "llm_search"
}

Tier guide:
  TIER_1 = IIT / IIM / BITS Pilani / IIIT-H / NIT-Trichy/Warangal/Surathkal / ISB / XLRI / global top-200
  TIER_2 = well-regarded state universities, strong NITs, VIT, Manipal, PESIT, top private colleges in metro cities
  TIER_3 = mid-tier private engineering or management colleges
  TIER_4 = below-average, purely local or obscure institutions
  UNKNOWN = cannot determine with reasonable confidence

Streams: list only what this college is known to offer.
Return ONLY valid JSON — no markdown, no commentary."""


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9& ]+", " ", (name or "").lower()).strip()


def _load_cache() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if _CACHE_PATH.exists():
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_to_cache(key: str, meta: dict[str, Any]) -> None:
    cache = _load_cache()
    cache[key] = meta
    try:
        _CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("institute_lookup: failed to write cache: %s", exc)


def _llm_lookup(institution_name: str) -> dict[str, Any]:
    """Call LLM to classify an unknown institute. Returns meta dict or {}."""
    try:
        from llm_client import call_llm_json, analysis_model
        model = analysis_model("google/gemma-3-27b-it:free")
        messages = [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": f"Institution: {institution_name}"},
        ]
        result = call_llm_json(model, messages, max_tokens=400)
        if result and isinstance(result, dict) and result.get("canonical_name"):
            result["source"] = "llm_search"
            logger.info("institute_lookup: LLM classified %r → tier=%s", institution_name, result.get("tier"))
            return result
    except Exception as exc:
        logger.warning("institute_lookup: LLM call failed for %r: %s", institution_name, exc)
    return {}


def lookup_institute(institution: str | None) -> dict[str, Any]:
    """
    Return institute meta dict.  Falls back to LLM + cache for unknowns.
    Always returns a dict (empty if completely unknown).
    """
    if not institution:
        return {}

    key = _normalize(institution)
    if not key:
        return {}

    # 1. Primary taxonomy dictionary
    from taxonomy import EDUCATION_INSTITUTE_DICTIONARY
    exact = EDUCATION_INSTITUTE_DICTIONARY.get(key)
    if exact:
        return exact
    # Partial match in primary dict
    for alias, meta in EDUCATION_INSTITUTE_DICTIONARY.items():
        if alias in key or key in alias:
            return meta

    # 2. Local JSON cache (previously LLM-resolved)
    cache = _load_cache()
    cached = cache.get(key)
    if cached:
        return cached
    # Partial match in cache
    for alias, meta in cache.items():
        if alias in key or key in alias:
            return meta

    # 3. LLM search
    logger.info("institute_lookup: %r not in dictionary — calling LLM", institution)
    meta = _llm_lookup(institution)
    if meta:
        _save_to_cache(key, meta)
        return meta

    return {}
