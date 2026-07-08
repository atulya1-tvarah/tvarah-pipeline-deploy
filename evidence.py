
from __future__ import annotations
import re
from datetime import date
from taxonomy import CANONICAL_SKILL_MAP
from utils import flatten_text, month_diff, parse_date, normalize_text

ACTION_VERBS = {
    "build", "built", "develop", "developed", "design", "designed", "implement", "implemented",
    "create", "created", "engineer", "engineered", "optimize", "optimized", "deploy", "deployed",
    "lead", "led", "own", "owned", "architect", "architected", "automate", "automated",
    "migrate", "migrated", "tune", "tuned", "maintain", "maintained", "support", "supported",
    "integrate", "integrated", "orchestrate", "orchestrated", "perform", "performed",
    "conduct", "conducted", "research", "researched",
}
ADVANCED_SIGNALS = {"architecture","scal","optimization","performance","distributed","pipeline","production","real-time","registry","orchestration","drift","monitoring","vector","fine-tuning","rlhf","transformer","batch","stream","latency"}
MAINTENANCE_SIGNALS = {"support","maintenance","incident","bug fix","l2","l3"}
DEV_SIGNALS = {"build","develop","design","implement","create","architect","deploy","engineer","optimize","integrate"}
OPEN_SOURCE_SIGNALS = {"github","open source","pull request","contributor","oss"}
LEVEL_ORDER = ["NONE","MENTION","WEAK","APPLIED","DEEP","EXPERT"]
LEVEL_TO_SCORE = {"NONE": 0, "MENTION": 1, "WEAK": 2, "APPLIED": 3, "DEEP": 4, "EXPERT": 5}
LEVEL_TO_DURATION_WEIGHT = {"NONE": 0.0, "MENTION": 0.05, "WEAK": 0.15, "APPLIED": 0.35, "DEEP": 0.55, "EXPERT": 0.7}
GENERIC_NON_SKILLS = {
    "it services and it consulting",
    "product leadership",
    "exploratory analysis",
    "ai-driven usps",
    "e-commerce",
    "data engineering",
    "cloud engineering",
    "search engine development",
}
CANONICAL_TO_ALIASES = {}
for alias, (canonical, _) in CANONICAL_SKILL_MAP.items():
    CANONICAL_TO_ALIASES.setdefault(canonical, set()).add(alias)
    CANONICAL_TO_ALIASES[canonical].add(canonical.lower())

SKILL_SOURCE_BUCKETS = {
    "programming_languages": "programming_languages",
    "frameworks_and_libraries": "frameworks_and_libraries",
    "tools_and_platforms": "tools_and_platforms",
    "databases": "databases",
    "cloud_and_infra": "cloud_and_infra",
    "soft_skills": "soft_skills",
    "certified_skills": "certified_skills",
}

def _aliases_for_skill(skill: str):
    return sorted(CANONICAL_TO_ALIASES.get(skill, {skill.lower()}), key=len, reverse=True)

def _alias_matches(alias: str, text: str) -> bool:
    alias = alias.lower().strip()
    if not alias:
        return False
    pattern = r"(?<!\w)" + re.escape(alias).replace(r"\ ", r"\s+") + r"(?!\w)"
    return re.search(pattern, text) is not None

def classify_project_type(text: str) -> str:
    low = text.lower()
    if any(s in low for s in MAINTENANCE_SIGNALS): return "MAINTENANCE_SUPPORT"
    if any(s in low for s in DEV_SIGNALS): return "DEVELOPMENT"
    return "UNKNOWN"

def classify_evidence_level(skill: str, text: str):
    low = text.lower(); reasons=[]
    aliases = [alias for alias in _aliases_for_skill(skill) if alias]
    matched_aliases = [alias for alias in aliases if _alias_matches(alias, low)]
    if not matched_aliases: return "NONE", reasons
    verb_hits = sum(1 for v in ACTION_VERBS if v in low)
    adv_hits = sum(1 for x in ADVANCED_SIGNALS if x in low)
    reasons.append(f"matched_aliases={','.join(matched_aliases[:3])}")
    if verb_hits: reasons.append(f"action_verbs={verb_hits}")
    if adv_hits: reasons.append(f"advanced_signals={adv_hits}")
    if adv_hits >= 2 and verb_hits >= 1: return "EXPERT", reasons
    if adv_hits >= 1 and verb_hits >= 1: return "DEEP", reasons
    if verb_hits >= 1: return "APPLIED", reasons
    if any(x in low for x in {"used","worked on","experience with","hands-on"}):
        reasons.append("usage_phrase"); return "WEAK", reasons
    return "MENTION", reasons

def infer_skill_depth(level: str, years_of_usage: float, context_count: int, architecture_signal: bool, coding_signal: bool, recency: str):
    score = LEVEL_TO_SCORE.get(level, 0)
    if years_of_usage >= 2:
        score += 1
    if years_of_usage >= 4:
        score += 1
    if context_count >= 2:
        score += 1
    if context_count >= 3:
        score += 1
    if architecture_signal:
        score += 1
    if coding_signal:
        score += 1
    if recency == "OLD":
        score -= 1
    if level == "EXPERT" and years_of_usage >= 6 and context_count >= 3 and architecture_signal and recency != "OLD":
        return "ARCHITECT_LEVEL", min(score, 10)
    if level in {"DEEP", "EXPERT"} and years_of_usage >= 4 and context_count >= 2 and recency != "OLD" and (architecture_signal or coding_signal):
        return "ADVANCED", min(score, 10)
    if level in {"APPLIED", "DEEP", "EXPERT"} and (years_of_usage >= 1 or context_count >= 1):
        return "HANDS_ON", min(score, 10)
    if level in {"WEAK", "MENTION"}:
        return "FOUNDATIONAL", min(score, 10)
    return "AWARENESS", min(score, 10)

def _items(resume_data):
    out=[]
    for key in ["experience","work_experience","professional_experience","employment","projects"]:
        val = resume_data.get(key)
        if isinstance(val, list):
            out.extend([v for v in val if isinstance(v, dict)])
    return out

def _should_keep_unmapped_skill(value: str) -> bool:
    normalized = normalize_text(value).lower()
    if not normalized or normalized in GENERIC_NON_SKILLS:
        return False
    if len(normalized.split()) > 4:
        return False
    return True


def _categorized_skill_sources(resume_data):
    raw_data = resume_data.get("raw_data", {}) if isinstance(resume_data.get("raw_data"), dict) else {}
    skills_info = raw_data.get("skills_info", {}) if isinstance(raw_data.get("skills_info"), dict) else {}
    categorized = {}
    for bucket, label in SKILL_SOURCE_BUCKETS.items():
        for item in skills_info.get(bucket, []) if isinstance(skills_info.get(bucket), list) else []:
            normalized = normalize_text(item)
            if not normalized:
                continue
            mapped = CANONICAL_SKILL_MAP.get(normalized.lower())
            canonical = mapped[0] if mapped else normalized
            categorized.setdefault(canonical, set()).add(label)
    return categorized


def _skill_artifacts(skill: str, resume_data):
    aliases = _aliases_for_skill(skill)
    artifact_sources = {
        "certification": resume_data.get("certifications", []),
        "certificate": resume_data.get("certificates", []),
        "patent": resume_data.get("patents", []),
        "achievement": resume_data.get("achievements", []),
    }
    matched = []
    for artifact_type, values in artifact_sources.items():
        values = values if isinstance(values, list) else [values]
        for value in values:
            text = normalize_text(value).lower()
            if not text:
                continue
            if any(_alias_matches(alias, text) for alias in aliases):
                matched.append(f"{artifact_type}: {normalize_text(value)}")
    return matched[:4]

def collect_skill_evidence(resume_data):
    text_blob = flatten_text(resume_data)
    categorized_sources = _categorized_skill_sources(resume_data)
    explicit_skills = set()
    for key in ["skills","competencies","technical_skills","tools","technologies","certified_skills"]:
        val = resume_data.get(key)
        if isinstance(val, list):
            for item in val:
                normalized = normalize_text(item)
                if not normalized:
                    continue
                mapped = CANONICAL_SKILL_MAP.get(normalized.lower())
                if mapped:
                    explicit_skills.add(mapped[0])
                elif len(normalized) >= 3 and _should_keep_unmapped_skill(normalized):
                    explicit_skills.add(normalized)
        elif isinstance(val, dict):
            for inner in val.values():
                if isinstance(inner, list):
                    for item in inner:
                        normalized = normalize_text(item)
                        if not normalized:
                            continue
                        mapped = CANONICAL_SKILL_MAP.get(normalized.lower())
                        if mapped:
                            explicit_skills.add(mapped[0])
                        elif len(normalized) >= 3 and _should_keep_unmapped_skill(normalized):
                            explicit_skills.add(normalized)
    for token, (canonical, _) in CANONICAL_SKILL_MAP.items():
        if _alias_matches(token, text_blob.lower()):
            explicit_skills.add(canonical)
    evidence={}
    items=_items(resume_data)
    for skill in sorted(explicit_skills):
        skill_low = skill.lower()
        aliases = _aliases_for_skill(skill)
        cluster = None
        for token, (canonical, cluster_name) in CANONICAL_SKILL_MAP.items():
            if canonical.lower()==skill_low or token==skill_low:
                cluster = cluster_name
                break
        matched=[]; weighted_total_months=0.0; raw_total_months=0; latest_end=None; earliest_start=None; pts=[]; architecture=False; coding=False; open_source=False; strongest="NONE"; reasons=[]; evidence_roles=[]; action_hits_total=0; advanced_hits_total=0; advanced_topics=set()
        for item in items:
            ctx = flatten_text(item)
            ctx_low = ctx.lower()
            if not any(_alias_matches(alias, ctx_low) for alias in aliases): continue
            level, rs = classify_evidence_level(skill, ctx)
            matched.append({"company": item.get("company") or item.get("organization"), "title": item.get("title") or item.get("role"), "context": ctx[:700], "evidence_level": level, "reasons": rs, "project_type": classify_project_type(ctx)})
            if LEVEL_ORDER.index(level) > LEVEL_ORDER.index(strongest): strongest = level
            reasons.extend(rs)
            action_hits_total += sum(1 for reason in rs if reason.startswith("action_verbs=") for _ in [0])
            advanced_hits_total += sum(1 for reason in rs if reason.startswith("advanced_signals=") for _ in [0])
            low = ctx_low
            if "architect" in low or "architecture" in low or "design" in low: architecture = True
            if any(v in low for v in {"code","coding","script","implementation","developed","built"}): coding = True
            if any(sig in low for sig in OPEN_SOURCE_SIGNALS): open_source = True
            for signal in ADVANCED_SIGNALS:
                if signal in low:
                    advanced_topics.add(signal)
            start = parse_date(item.get("start_date") or item.get("from"))
            _raw_end = item.get("end_date") or item.get("to") or item.get("duration_end")
            end = parse_date(_raw_end) if _raw_end else date.today()  # null end_date = current role
            raw_months = month_diff(start, end)
            weighted_months = round(raw_months * LEVEL_TO_DURATION_WEIGHT.get(level, 0.0), 1)
            raw_total_months += raw_months
            weighted_total_months += weighted_months
            evidence_roles.append({
                "company": item.get("company") or item.get("organization"),
                "title": item.get("title") or item.get("role"),
                "start_date": item.get("start_date") or item.get("from"),
                "end_date": item.get("end_date") or item.get("to") or item.get("duration_end"),
                "evidence_level": level,
                "raw_months": raw_months,
                "weighted_months": weighted_months,
            })
            if end and (latest_end is None or end > latest_end): latest_end = end
            if start and (earliest_start is None or start < earliest_start): earliest_start = start
            pt = classify_project_type(ctx)
            if pt != "UNKNOWN": pts.append(pt)
        recency="UNKNOWN"
        recency_months = None
        if latest_end:
            age = month_diff(latest_end, parse_date("present"))
            recency_months = age
            recency = "RECENT" if age <= 12 else ("MID" if age <= 36 else "OLD")
        years_of_usage = round(weighted_total_months/12,1) if weighted_total_months else 0.0
        raw_years_of_usage = round(raw_total_months/12,1) if raw_total_months else 0.0
        depth_label, strength_score = infer_skill_depth(strongest, years_of_usage, len(matched), architecture, coding, recency)
        project_type_mix = sorted(set(pts[-3:])) if pts else []
        artifact_evidence = _skill_artifacts(skill, resume_data)
        coding_strength = (
            "STRONG" if coding and len(matched) >= 2 and recency == "RECENT" else
            "MODERATE" if coding else
            "LIMITED"
        )
        upskill_signal = bool(
            recency == "RECENT"
            and (
                len(matched) >= 2
                or bool(artifact_evidence)
                or (earliest_start and latest_end and month_diff(earliest_start, latest_end) >= 18)
            )
        )
        evidence[skill] = {
            "skill": skill,
            "cluster": cluster,
            "source_categories": sorted(categorized_sources.get(skill, set())),
            "evidence_level": strongest,
            "depth_label": depth_label,
            "strength_score": strength_score,
            "years_of_usage": years_of_usage,
            "raw_years_of_usage": raw_years_of_usage,
            "recency": recency,
            "recency_months": recency_months,
            "architecture_signal": architecture,
            "coding_signal": coding,
            "coding_strength_signal": coding_strength,
            "open_source_signal": open_source,
            "upskill_signal": upskill_signal,
            "artifact_evidence": artifact_evidence,
            "advanced_topic_signals": sorted(advanced_topics)[:6],
            "project_contexts": pts[-3:],
            "project_type_mix": project_type_mix,
            "matched_context_count": len(matched),
            "contexts": matched[:5],
            "evidence_roles": evidence_roles[:5],
            "reasons": sorted(set(reasons)),
        }
    return evidence
