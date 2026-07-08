from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Set

from .helpers import norm_text, unique_norm, parse_ym, months_between
from .ontology import infer_skills_from_text, canonicalize_skill


def _resume_root(resume_json: dict) -> dict:
    root = resume_json.get("resume_data", resume_json)
    if isinstance(root, dict) and "insight_info" in root:
        merged = {}
        if isinstance(root.get("basic_info"), dict):
            merged.update(root.get("basic_info") or {})
        if isinstance(root.get("insight_info"), dict):
            merged.update(root.get("insight_info") or {})
        for k, v in root.items():
            if k not in {"basic_info", "insight_info"}:
                merged.setdefault(k, v)
        return merged
    return root


def _jd_root(jd_json: dict) -> dict:
    return jd_json.get("jd_data", jd_json)


def extract_resume_skills(resume_json: dict) -> Set[str]:
    resume = _resume_root(resume_json)
    skills_info = resume.get("skills_info", {}) or {}

    buckets = []
    for key in [
        "programming_languages",
        "frameworks_and_libraries",
        "tools_and_platforms",
        "databases",
        "cloud_and_infra",
        "soft_skills",
        "domain_skills",
        "certified_skills",
    ]:
        values = skills_info.get(key, []) or []
        if isinstance(values, list):
            buckets.extend([v for v in values if isinstance(v, str)])

    inferred = set()
    for exp in resume.get("work_experience_info", []) or []:
        desc = (exp.get("role_description") or "") + " " + (exp.get("experience_insights") or "")
        inferred.update(infer_skills_from_text(desc))
    buckets.extend(list(inferred))
    return set(canonicalize_skill(x) for x in unique_norm(buckets))


def extract_jd_mandatory_skills(jd_json: dict) -> Set[str]:
    jd = _jd_root(jd_json)
    ms = jd.get("mandatory_skills", {}) or {}
    buckets = []
    for key in ["programming_languages", "frameworks_and_libraries", "tools", "databases", "cloud_and_infra"]:
        values = ms.get(key, []) or []
        if isinstance(values, list):
            buckets.extend([v for v in values if isinstance(v, str)])
    return set(canonicalize_skill(x) for x in unique_norm(buckets))


def extract_jd_optional_skills(jd_json: dict) -> Set[str]:
    jd = _jd_root(jd_json)
    values = jd.get("optional_skills", []) or []
    return set(canonicalize_skill(x) for x in unique_norm([v for v in values if isinstance(v, str)]))


def estimate_resume_experience_years(resume_json: dict) -> Optional[float]:
    resume = _resume_root(resume_json)
    spans = []
    now = datetime.now().replace(day=1)
    for exp in resume.get("work_experience_info", []) or []:
        start = parse_ym(exp.get("start_date"))
        end = parse_ym(exp.get("end_date")) if exp.get("end_date") else now
        if start and end and end >= start:
            spans.append((start, end))
    if not spans:
        return None
    spans.sort(key=lambda x: x[0])
    merged = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    total_months = sum(months_between(s, e) for s, e in merged)
    return round(total_months / 12.0, 2)


def most_recent_job_title(resume_json: dict) -> Optional[str]:
    resume = _resume_root(resume_json)
    work = resume.get("work_experience_info", []) or []
    if not work:
        return None
    def sort_key(exp):
        return parse_ym(exp.get("start_date")) or datetime(1900, 1, 1)
    latest = sorted(work, key=sort_key, reverse=True)[0]
    title = latest.get("job_title")
    return str(title) if title else None


def extract_resume_education_blob(resume_json: dict) -> str:
    resume = _resume_root(resume_json)
    parts = []
    for edu in resume.get("education_info", []) or []:
        for key in ["degree", "field_of_study", "education_level", "institution_name", "institution_type"]:
            value = edu.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip().lower())
    return " ".join(parts)


def extract_resume_company_blob(resume_json: dict) -> str:
    resume = _resume_root(resume_json)
    parts = []
    for exp in resume.get("work_experience_info", []) or []:
        value = exp.get("company_name")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip().lower())
    return " ".join(parts)


def extract_resume_domain_set(resume_json: dict) -> Set[str]:
    root = _resume_root(resume_json)
    domain_data = resume_json.get("domain_data", {}) or root.get("domain_data", {}) or {}
    values = domain_data.get("overall_candidate_domain", []) or []
    return set(unique_norm([v for v in values if isinstance(v, str)]))


# Job-level hierarchy: higher number = more senior
JD_LEVEL_MAP: dict = {
    "intern": 0, "trainee": 0,
    "entry": 1, "junior": 1, "associate": 1,
    "mid": 2, "intermediate": 2, "mid-level": 2,
    "senior": 3, "sr": 3,
    "lead": 4, "principal": 4, "staff": 4,
    "manager": 5,
    "director": 6,
    "vp": 7, "vice president": 7,
    "chief": 8, "c-level": 8, "cto": 8, "ceo": 8, "cdo": 8, "cpo": 8,
}


def extract_jd_job_level(jd_json: dict) -> Optional[str]:
    jd = _jd_root(jd_json)
    return jd.get("job_level") or jd.get("seniority_level") or jd.get("level")


def extract_jd_work_mode(jd_json: dict) -> str:
    jd = _jd_root(jd_json)
    return str(jd.get("work_mode") or jd.get("work_type") or "not specified").strip()


def extract_jd_location(jd_json: dict) -> List[str]:
    jd = _jd_root(jd_json)
    loc = jd.get("location") or jd.get("locations") or []
    if isinstance(loc, str):
        return [loc] if loc.strip() else []
    if isinstance(loc, list):
        return [str(x).strip() for x in loc if x]
    return []


def extract_jd_salary_range(jd_json: dict) -> Optional[str]:
    jd = _jd_root(jd_json)
    return jd.get("salary_range") or jd.get("ctc_range") or jd.get("compensation")


def extract_jd_industry_domains(jd_json: dict) -> List[str]:
    jd = _jd_root(jd_json)
    domains = jd.get("industry_domains") or jd.get("industries") or jd.get("domain") or []
    if isinstance(domains, str):
        return [domains.lower().strip()] if domains.strip() else []
    if isinstance(domains, list):
        return [str(x).lower().strip() for x in domains if x]
    return []


def extract_candidate_location(resume_json: dict) -> dict:
    resume = _resume_root(resume_json)
    contact = resume.get("contact_info") or {}
    if not isinstance(contact, dict):
        contact = {}
    return {
        "city": str(contact.get("city") or contact.get("location") or "").strip().lower(),
        "country": str(contact.get("country") or "").strip().lower(),
        "raw": str(contact.get("address") or contact.get("location") or "").strip().lower(),
    }


def infer_candidate_level(resume_json: dict) -> Optional[str]:
    title = most_recent_job_title(resume_json)
    if not title:
        return None
    t = title.lower()
    best_match, best_len = None, 0
    for kw in JD_LEVEL_MAP:
        if kw in t and len(kw) > best_len:
            best_match, best_len = kw, len(kw)
    return best_match.title() if best_match else None
