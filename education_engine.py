from __future__ import annotations

import re
from typing import Any

from taxonomy import COURSE_DICTIONARY, GPA_BENCHMARKS
from utils import dedupe_keep_order, first_non_empty, normalize_text, parse_date
from institute_lookup import lookup_institute as _lookup_institute_ext


def _education_items(resume_data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in resume_data.get("education", []) or []:
        if isinstance(item, dict):
            out.append(item)
    return out


def _normalize_lookup_key(value: str | None) -> str:
    cleaned = normalize_text(value).lower()
    return re.sub(r"[^a-z0-9+& ]+", " ", cleaned).strip()


def _lookup_institute(institution: str | None) -> dict[str, Any]:
    """Delegates to institute_lookup module (dict → cache → LLM)."""
    return _lookup_institute_ext(institution)


def _lookup_course(*values: str | None) -> dict[str, Any]:
    text = " ".join(_normalize_lookup_key(value) for value in values if value)
    if not text:
        return {}
    exact = COURSE_DICTIONARY.get(text)
    if exact:
        return exact
    # Longest-match wins: "quantitative economics" (22 chars) beats "msc" (3 chars)
    best_meta: dict[str, Any] = {}
    best_len = 0
    for alias, meta in COURSE_DICTIONARY.items():
        if alias in text and len(alias) > best_len:
            best_meta = meta
            best_len = len(alias)
    return best_meta


def _extract_gpa(value: Any) -> tuple[float | None, str | None]:
    if value in (None, "", [], {}):
        return None, None
    text = normalize_text(str(value))
    matches = re.findall(r"\d+(?:\.\d+)?", text)
    if not matches:
        return None, None
    numeric = float(matches[0])
    if "/10" in text or numeric <= 10:
        return numeric, "10_POINT"
    if "/4" in text or numeric <= 4.1:
        return numeric, "4_POINT"
    return numeric, "100_POINT"


def _gpa_readout(gpa_value: float | None, scale_key: str | None) -> str | None:
    if gpa_value is None or scale_key not in GPA_BENCHMARKS:
        return None
    benchmark = GPA_BENCHMARKS[scale_key]
    if gpa_value >= benchmark["excellent"]:
        return "EXCELLENT"
    if gpa_value >= benchmark["good"]:
        return "GOOD"
    if gpa_value >= benchmark["acceptable"]:
        return "ACCEPTABLE"
    return "LOW"


def _timeline_bounds(entries: list[dict[str, Any]]) -> tuple[Any, Any]:
    starts = []
    ends = []
    for entry in entries:
        start = parse_date(first_non_empty(entry.get("start_date"), entry.get("from"), entry.get("education_start_date")))
        end = parse_date(first_non_empty(entry.get("end_date"), entry.get("to"), entry.get("education_end_date"), entry.get("graduation_date"), entry.get("passing_year")))
        if start:
            starts.append(start)
        if end:
            ends.append(end)
    return (min(starts) if starts else None, max(ends) if ends else None)


_TECH_FAMILIES = {"ENGINEERING", "COMPUTER_APPLICATIONS", "RESEARCH", "ANALYTICS"}
_SEMI_TECH_FAMILIES = {"SCIENCE", "MANAGEMENT"}
_NON_TECH_FAMILIES = {"ARTS", "COMMERCE"}

_DEGREE_LEVEL_MAP = {
    "phd": "PHD", "doctor of philosophy": "PHD",
    "m.tech": "MASTER", "mtech": "MASTER", "msc": "MASTER", "m.sc": "MASTER",
    "mca": "MASTER", "mba": "MASTER", "pgdm": "MASTER", "pdgm": "MASTER",
    "m.com": "MASTER", "ma": "MASTER",
    "b.tech": "BACHELOR", "btech": "BACHELOR", "be": "BACHELOR", "b.e": "BACHELOR",
    "bsc": "BACHELOR", "bca": "BACHELOR", "b.com": "BACHELOR", "ba": "BACHELOR",
}


def _degree_level(degree: str | None, course_meta: dict) -> str:
    if not degree:
        return "UNKNOWN"
    key = normalize_text(degree).lower()
    # Check exact from degree_level_map first
    for alias, level in _DEGREE_LEVEL_MAP.items():
        if alias in key:
            return level
    # Fallback hints
    if "phd" in key or "doctor" in key:
        return "PHD"
    if "master" in key or "m." in key or "msc" in key or "mba" in key or "pgdm" in key:
        return "MASTER"
    if "bachelor" in key or "b." in key or "bsc" in key or "bca" in key or "be" in key:
        return "BACHELOR"
    if "diploma" in key or "certificate" in key:
        return "DIPLOMA"
    return "UNKNOWN"


_TECH_DEGREE_HINTS = {"tech", "engineering", "computer science", "cs", "cse", "ece", "computer applications", "information technology", "it"}
_NON_TECH_DEGREE_HINTS = {"arts", "commerce", "humanities", "law", "sociology", "history", "literature"}


def _field_tech_fit(course_family: str, degree_text: str = "") -> str:
    if course_family in _TECH_FAMILIES:
        return "TECH"
    if course_family in _SEMI_TECH_FAMILIES:
        return "SEMI_TECH"
    if course_family in _NON_TECH_FAMILIES:
        return "NON_TECH"
    # Fallback: infer from degree/field text
    low = (degree_text or "").lower()
    if any(hint in low for hint in _TECH_DEGREE_HINTS):
        return "TECH"
    if any(hint in low for hint in _NON_TECH_DEGREE_HINTS):
        return "NON_TECH"
    return "UNKNOWN"


# ── IT vs Non-IT stream classification ──────────────────────────────────────
# "IT stream" means the candidate studied CS/IT/Software; everything else is Non-IT.
# Uses word-boundary patterns to avoid "cs" matching "electronics".
import re as _re

_IT_STREAM_PATTERNS = [
    r"\bcomputer science\b", r"\bcse\b", r"\bcs\b", r"\binformation technology\b",
    r"\bit\b", r"\bsoftware engineering\b", r"\bdata science\b",
    r"\bartificial intelligence\b", r"\bmachine learning\b",
    r"\bmca\b", r"\bbca\b", r"\bcomputer applications\b",
    r"\bcomputer engineering\b", r"\binformation systems\b",
    # Analytics / quantitative degrees are treated as IT-equivalent for data roles
    r"\bstatistics\b", r"\banalytics\b", r"\bquantitative\b", r"\beconometrics\b",
    r"\boperations research\b", r"\bactuarial\b", r"\bmsqe\b",
]
_IT_STREAM_RE = _re.compile("|".join(_IT_STREAM_PATTERNS))

# Stream relevance ranking (lower = more relevant to tech roles): ECE=1, EEE=2, EE=3, Mech=4, Civil=5
_STREAM_RANK: dict[str, int] = {
    "electronics and communication": 1, "ece": 1, "etc": 1, "e&tc": 1,
    "electronics": 1,
    "electrical and electronics": 2, "eee": 2,
    "electrical engineering": 3, "electrical": 3, " ee ": 3,
    "mechanical engineering": 4, "mechanical": 4, "mech": 4,
    "civil engineering": 5, "civil": 5,
}


def _is_it_stream(degree: str | None, field: str | None) -> bool:
    text = " ".join(filter(None, [degree, field])).lower()
    return bool(_IT_STREAM_RE.search(text))


def _stream_relevance_rank(degree: str | None, field: str | None) -> int | None:
    """Return a rank 1-5 (1=most relevant like ECE, 5=least like Civil). None = unknown/IT."""
    text = " ".join(filter(None, [degree, field])).lower()
    for hint, rank in _STREAM_RANK.items():
        if hint in text:
            return rank
    return None


def _compute_education_score(tier: str, gpa_band: str | None, course_value: str, degree_lvl: str) -> float:
    """Compute a single education entry score on [0, 10]."""
    base = {"TIER_1": 8.0, "TIER_2": 6.0, "TIER_3": 4.0, "TIER_4": 2.0}.get(tier, 3.0)
    gpa_bonus = {"EXCELLENT": 1.5, "GOOD": 0.75, "ACCEPTABLE": 0.0, "LOW": -0.5}.get(gpa_band or "", 0.0)
    course_penalty = {"HIGH": 0.0, "MEDIUM": -0.5, "FOUNDATIONAL": -1.5}.get(course_value, 0.0)
    degree_bonus = {"PHD": 1.0, "MASTER": 0.5, "BACHELOR": 0.0}.get(degree_lvl, 0.0)
    raw = base + gpa_bonus + course_penalty + degree_bonus
    return round(max(0.0, min(10.0, raw)), 2)


def analyze_education(resume_data: dict[str, Any]) -> dict[str, Any]:
    entries = _education_items(resume_data)
    normalized_entries = []
    tier_tags = []
    value_tags = []
    education_scores: list[float] = []

    for entry in entries:
        institution = first_non_empty(
            entry.get("institution_name"),
            entry.get("institution"),
            entry.get("college"),
            entry.get("school"),
            entry.get("university"),
        )
        degree = first_non_empty(entry.get("degree"), entry.get("course"), entry.get("education_level"))
        field = first_non_empty(entry.get("field_of_study"), entry.get("specialization"), entry.get("major"))
        grade_text = first_non_empty(entry.get("gpa"), entry.get("grade"), entry.get("cgpa"), entry.get("score"), entry.get("percentage"))
        institute_meta = _lookup_institute(institution)
        course_meta = _lookup_course(degree, field)
        gpa_value, gpa_scale = _extract_gpa(grade_text)
        gpa_band = _gpa_readout(gpa_value, gpa_scale)
        tier = institute_meta.get("tier", "UNKNOWN")
        course_value = course_meta.get("value_signal", "UNKNOWN")
        course_family = course_meta.get("family", "UNKNOWN")
        degree_lvl = _degree_level(degree, course_meta)
        field_fit = _field_tech_fit(course_family, f"{degree or ''} {field or ''}")
        entry_score = _compute_education_score(tier, gpa_band, course_value, degree_lvl)
        tier_tags.append(tier)
        value_tags.append(course_value)
        education_scores.append(entry_score)
        it_stream = _is_it_stream(degree, field)
        stream_rank = _stream_relevance_rank(degree, field)
        normalized_entries.append(
            {
                "institution": institution or "UNKNOWN",
                "institution_canonical": institute_meta.get("canonical_name", institution or "UNKNOWN"),
                "institution_source": institute_meta.get("source", "dictionary"),  # "llm_search" if auto-resolved
                "tier": tier,
                "institution_category": institute_meta.get("category", "UNKNOWN"),
                "institution_streams": institute_meta.get("streams", []),
                "institution_city": institute_meta.get("city", ""),
                "institution_nirf_rank": institute_meta.get("nirf_rank"),
                "degree": degree or "UNKNOWN",
                "degree_level": degree_lvl,
                "course_family": course_family,
                "course_canonical": course_meta.get("canonical_name", degree or "UNKNOWN"),
                "course_value_signal": course_value,
                "field_of_study": field or "UNKNOWN",
                "field_tech_fit": field_fit,
                "is_it_stream": it_stream,
                "stream_relevance_rank": stream_rank,
                "gpa_raw": grade_text or "N/A",
                "gpa_value": gpa_value,
                "gpa_scale": gpa_scale or "UNKNOWN",
                "gpa_band": gpa_band or "UNKNOWN",
                "education_score": entry_score,
                "start_date": first_non_empty(entry.get("start_date"), entry.get("from"), entry.get("education_start_date")),
                "end_date": first_non_empty(entry.get("end_date"), entry.get("to"), entry.get("education_end_date"), entry.get("graduation_date"), entry.get("passing_year")),
            }
        )

    education_start, education_end = _timeline_bounds(entries)
    first_job_start = None
    for item in resume_data.get("experience", []) or []:
        if not isinstance(item, dict):
            continue
        parsed = parse_date(first_non_empty(item.get("start_date"), item.get("from")))
        if parsed and (first_job_start is None or parsed < first_job_start):
            first_job_start = parsed

    gap_months = 0
    if education_end and first_job_start:
        gap_months = max(0, (first_job_start.year - education_end.year) * 12 + (first_job_start.month - education_end.month))

    highest_tier = "UNKNOWN"
    for tier in ["TIER_1", "TIER_2", "TIER_3", "TIER_4"]:
        if tier in tier_tags:
            highest_tier = tier
            break

    strongest_course_value = "UNKNOWN"
    for value in ["HIGH", "MEDIUM", "FOUNDATIONAL"]:
        if value in value_tags:
            strongest_course_value = value
            break

    highest_education_score = round(max(education_scores), 2) if education_scores else 3.0
    has_tech_degree = any(
        entry.get("field_tech_fit") == "TECH" for entry in normalized_entries
    )

    return {
        "education_entries": normalized_entries,
        "highest_institute_tier": highest_tier,
        "strongest_course_value_signal": strongest_course_value,
        "highest_education_score": highest_education_score,
        "has_tech_degree": has_tech_degree,
        "education_gap_flag": gap_months > 12,
        "education_gap_months": gap_months,
        "education_start_date": str(education_start) if education_start else "",
        "education_end_date": str(education_end) if education_end else "",
        "top_institutes": dedupe_keep_order([entry["institution_canonical"] for entry in normalized_entries if entry.get("institution_canonical")]),
        "course_families": dedupe_keep_order([entry["course_family"] for entry in normalized_entries if entry.get("course_family") and entry["course_family"] != "UNKNOWN"]),
        "gpa_summary": dedupe_keep_order([
            f"{entry['course_canonical']}: {entry['gpa_raw']} ({entry['gpa_band']})"
            for entry in normalized_entries
            if entry.get("gpa_raw") not in {"N/A", "", None}
        ]),
    }
