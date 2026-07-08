from __future__ import annotations
import json
from datetime import datetime
from typing import Dict, List, Set
from .helpers import norm_skill, norm_text, parse_ym
from .models import SemanticSkillEvidence
from .ontology import skill_forms, find_adjacent_matches

ACTION_WORDS = ["built","designed","developed","implemented","led","optimized","deployed","created","architected","production","scaled","improved","owned","delivered","mentored","managed","translated"]
OUTCOME_WORDS = ["reduced","improved","increased","enabled","optimized","saved","grew","accelerated","impact"]
OWNER_WORDS = ["owned","led","managed","architected","drove"]


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


def _collect_resume_blocks(resume_json: dict) -> List[dict]:
    resume = _resume_root(resume_json)
    blocks = []
    for exp in resume.get("work_experience_info", []) or []:
        blocks.append({
            "context": "work",
            "text": json.dumps(exp, ensure_ascii=False).lower(),
            "date": exp.get("end_date") or exp.get("start_date"),
        })
    for proj in resume.get("project_info", []) or resume.get("projects", []) or []:
        blocks.append({"context": "project", "text": json.dumps(proj, ensure_ascii=False).lower(), "date": None})
    blocks.append({"context": "skills_section", "text": json.dumps(resume.get("skills_info", {}), ensure_ascii=False).lower(), "date": None})
    return blocks


def _classify_depth(evidence_count: int, strong_signal_count: int, owner_signal_count: int) -> str:
    if owner_signal_count >= 1 and strong_signal_count >= 2:
        return "expert"
    if evidence_count >= 2 and strong_signal_count >= 1:
        return "applied"
    if evidence_count >= 1:
        return "basic"
    return "none"


def build_semantic_skill_analysis(resume_json: dict, candidate_skills: Set[str], target_skills: Set[str], synonym_map: Dict[str, List[str]]) -> Dict[str, SemanticSkillEvidence]:
    blocks = _collect_resume_blocks(resume_json)
    out = {}
    for raw_skill in sorted(target_skills):
        skill = norm_skill(raw_skill)
        aliases = [norm_skill(a) for a in synonym_map.get(skill, [])]
        all_forms = set([skill] + aliases) | skill_forms(skill)
        matched = False
        adjacent_match = False
        evidence_sources, aliases_hit, contexts, snippets = [], [], [], []
        evidence_count = 0
        strong_signal_count = 0
        owner_signal_count = 0
        recent_dates = []
        outcome_signal = False
        ownership_level = "mentioned"
        if skill in candidate_skills:
            matched = True
            evidence_sources.append("normalized_skill_inventory")
            contexts.append("skills_section")
            evidence_count += 1
        elif find_adjacent_matches(skill, candidate_skills):
            adjacent_match = True
            evidence_sources.append("adjacent_skill_inventory")
            evidence_count += 1
        for block in blocks:
            text = norm_text(block["text"])
            local_hit = False
            local_aliases = []
            for form in all_forms:
                if form and form in text:
                    local_hit = True
                    matched = True
                    evidence_count += 1
                    if form != skill:
                        local_aliases.append(form)
            if not local_hit and skill not in candidate_skills and any(adj in text for adj in find_adjacent_matches(skill, candidate_skills)):
                adjacent_match = True
            if local_hit:
                evidence_sources.append("resume_text")
                contexts.append(block["context"])
                aliases_hit.extend(local_aliases)
                if any(word in text for word in ACTION_WORDS):
                    strong_signal_count += 1
                if any(word in text for word in OWNER_WORDS):
                    owner_signal_count += 1
                if any(word in text for word in OUTCOME_WORDS):
                    outcome_signal = True
                if block.get("date"):
                    dt = parse_ym(block.get("date"))
                    if dt:
                        recent_dates.append(dt)
                snippet = text[:260].replace("\n", " ")
                if snippet:
                    snippets.append(snippet)
        depth = _classify_depth(evidence_count, strong_signal_count, owner_signal_count)
        if owner_signal_count >= 2:
            ownership_level = "lead"
        elif owner_signal_count >= 1:
            ownership_level = "owner"
        elif strong_signal_count >= 1:
            ownership_level = "contributor"
        latest = max(recent_dates) if recent_dates else None
        if latest is None:
            recency_label = "unknown"
        else:
            diff_years = max(0, datetime.now().year - latest.year)
            recency_label = "recent" if diff_years <= 1 else "moderate" if diff_years <= 3 else "old"
        confidence = 90 if depth == "expert" else 70 if depth == "applied" else 45 if depth == "basic" else 10 if adjacent_match else 0
        out[skill] = SemanticSkillEvidence(
            matched=matched or adjacent_match,
            depth=depth,
            evidence_sources=list(dict.fromkeys(evidence_sources)),
            aliases_matched=list(dict.fromkeys(aliases_hit)),
            contexts=list(dict.fromkeys(contexts)),
            snippets=snippets[:3],
            confidence=confidence,
            ownership_level=ownership_level,
            recency_label=recency_label,
            outcome_signal=outcome_signal,
            adjacent_match=adjacent_match,
        )
    return out
