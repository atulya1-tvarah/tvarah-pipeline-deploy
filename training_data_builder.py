from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from bert_signal_engine import build_evidence_packets
from dna_engine import classify_dna
from engine import normalize_resume_data
from evidence import collect_skill_evidence
from experience_engine import analyze_experience
from semantic_taxonomy import build_semantic_taxonomy
from utils import dedupe_keep_order, flatten_text, normalize_text


def _json_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        elif path.is_file() and path.suffix.lower() == ".json":
            files.append(path)
    unique: list[Path] = []
    seen = set()
    for path in files:
        marker = str(path.resolve()).lower()
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return unique


def _load_resume(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_id(path: Path, normalized: dict[str, Any]) -> str:
    name = normalize_text(normalized.get("name") or path.stem) or path.stem
    safe_name = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
    return f"{safe_name}_{path.stem.lower()}"


def _resume_text(normalized: dict[str, Any]) -> str:
    sections: list[str] = []
    if normalized.get("profile_summary"):
        sections.append(f"Summary: {normalized['profile_summary']}")
    competencies = normalized.get("competencies") or []
    if competencies:
        sections.append(f"Competencies: {', '.join(str(item) for item in competencies[:40])}")
    skills = normalized.get("skills") or []
    if skills:
        sections.append(f"Skills: {', '.join(str(item) for item in skills[:60])}")
    education_items = normalized.get("education") or []
    for item in education_items[:6]:
        if isinstance(item, dict):
            sections.append(
                "Education: "
                + " | ".join(
                    part
                    for part in [
                        normalize_text(item.get("degree")),
                        normalize_text(item.get("field_of_study")),
                        normalize_text(item.get("institution_name")),
                        normalize_text(item.get("start_date")),
                        normalize_text(item.get("end_date")),
                    ]
                    if part
                )
            )
    for item in (normalized.get("experience") or [])[:8]:
        if not isinstance(item, dict):
            continue
        sections.append(
            "Experience: "
            + " | ".join(
                part
                for part in [
                    normalize_text(item.get("title")),
                    normalize_text(item.get("company")),
                    normalize_text(item.get("start_date")),
                    normalize_text(item.get("end_date")),
                    normalize_text(item.get("description")),
                ]
                if part
            )
        )
    if not sections:
        sections.append(flatten_text(normalized))
    return normalize_text("\n".join(sections))


def _overview(normalized: dict[str, Any]) -> dict[str, Any]:
    personal = normalized.get("personal_info", {}) if isinstance(normalized.get("personal_info"), dict) else {}
    return {
        "name": normalized.get("name") or "N/A",
        "email": normalized.get("email") or personal.get("email") or "N/A",
        "phone": normalized.get("phone") or personal.get("phone") or "N/A",
        "location": normalized.get("location") or personal.get("location") or "N/A",
        "profile_summary": normalized.get("profile_summary") or normalized.get("summary") or "",
    }


def _base_record(path: Path, normalized: dict[str, Any]) -> dict[str, Any]:
    extraction_metadata = normalized.get("extraction_metadata", {}) if isinstance(normalized.get("extraction_metadata"), dict) else {}
    return {
        "resume_id": _candidate_id(path, normalized),
        "source_file": str(path),
        "candidate_name": normalized.get("name") or path.stem,
        "resume_text": _resume_text(normalized),
        "extraction_metadata": {
            "wrapped_input": bool(extraction_metadata.get("wrapped_input")),
            "reflection_loop": extraction_metadata.get("reflection_loop", 0),
            "judge_results": extraction_metadata.get("judge_results", []),
        },
    }


ROLE_FAMILY_COLLAPSE: dict[str, str] = {
    "ANALYTICS_ENGINEER": "DATA_ANALYST",
    "MARKETING_ANALYTICS": "DATA_ANALYST",
    "ML_ENGINEER": "APPLIED_SCIENTIST",
    "PLATFORM_ENGINEER": "DATA_ENGINEER",
    "CORE_DATA_SCIENTIST": "PRODUCT_DATA_SCIENTIST",
    "QUANT_DATA_ANALYST": "DATA_ANALYST",
    "MLOPS_DATA_SCIENTIST": "DATA_ENGINEER",
    "ROBOTICS_AUTONOMY_ENGINEER": "APPLIED_SCIENTIST",
}


def _role_family_rows(
    base: dict[str, Any],
    semantic: dict[str, Any],
    evidence_packets: dict[str, Any],
    experience: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_label = str(semantic.get("top_role_family") or "").strip().upper()
    label = ROLE_FAMILY_COLLAPSE.get(raw_label, raw_label) if raw_label else None
    if not label:
        return []

    total_yoe = evidence_packets.get("experience_packet", {}).get("total_experience_years", 0)
    top_skills = [
        p.get("skill") for p in (evidence_packets.get("skill_packets") or [])[:3]
        if p.get("skill")
    ]
    # Infer education level from resume_text heuristic
    resume_text_lower = base.get("resume_text", "").lower()
    if "phd" in resume_text_lower or "doctorate" in resume_text_lower:
        edu_level = "phd"
    elif "master" in resume_text_lower or " msc " in resume_text_lower or " mba " in resume_text_lower:
        edu_level = "master"
    else:
        edu_level = "bachelor"

    classifier_text = "\n".join(
        [
            base.get("resume_text", ""),
            "Top role candidates: "
            + ", ".join(
                f"{item.get('role_family')}({item.get('score_signal')})"
                for item in evidence_packets.get("role_packets", [])[:5]
                if item.get("role_family")
            ),
            "Strength areas: "
            + ", ".join(str(item) for item in evidence_packets.get("semantic_packet", {}).get("inferred_strength_areas", [])[:6]),
            "Titles: "
            + ", ".join(str(item) for item in evidence_packets.get("experience_packet", {}).get("titles", [])[:6]),
            f"Total YoE: {total_yoe}",
            "Top skills: " + ", ".join(str(s) for s in top_skills),
            f"Education level: {edu_level}",
        ]
    ).strip()
    return [
        {
            **base,
            "task": "role_family_classification",
            "label_source": "silver_bootstrap",
            "label": label,
            "original_label": raw_label,
            "collapsed": raw_label != label,
            "classifier_text": classifier_text,
            "candidates": semantic.get("role_family_scores", [])[:5],
            "evidence_packets": {
                "role_packets": evidence_packets.get("role_packets", []),
                "semantic_packet": evidence_packets.get("semantic_packet", {}),
                "experience_packet": evidence_packets.get("experience_packet", {}),
            },
        }
    ]


DNA_COLLAPSE: dict[str, str] = {
    "HYBRID": "PRODUCT",
    "RESEARCH": "DOMAIN_SPECIALIST",
}


def _dna_rows(
    base: dict[str, Any],
    dna: dict[str, Any],
    experience: dict[str, Any],
    evidence_packets: dict[str, Any],
) -> list[dict[str, Any]]:
    # Confidence gate: skip if primary_dna_confidence < 0.5
    dna_confidence = float(dna.get("primary_dna_confidence") or dna.get("confidence") or 1.0)
    if dna_confidence < 0.5:
        return []

    raw_label = str(dna.get("primary_dna") or "").strip().upper()
    label = DNA_COLLAPSE.get(raw_label, raw_label) if raw_label else None
    if not label:
        return []

    classifier_text = "\n".join(
        [
            base.get("resume_text", ""),
            "Operating model: " + str(experience.get("dominant_operating_model") or ""),
            "Companies: " + ", ".join(str(item) for item in experience.get("companies", [])[:6]),
            "Titles: " + ", ".join(str(item) for item in experience.get("titles", [])[:6]),
            "Business impacts: " + ", ".join(str(item) for item in experience.get("business_impacts", [])[:8]),
        ]
    ).strip()
    return [
        {
            **base,
            "task": "dna_fit_classification",
            "label_source": "silver_bootstrap",
            "label": label,
            "original_label": raw_label,
            "collapsed": raw_label != label,
            "classifier_text": classifier_text,
            "priors": {
                "consulting_score": dna.get("consulting_score"),
                "product_score": dna.get("product_score"),
                "domain_specialist_score": dna.get("domain_specialist_score"),
                "dominant_operating_model": experience.get("dominant_operating_model"),
                "dna_confidence": dna_confidence,
            },
            "evidence_packets": {
                "dna_packet": evidence_packets.get("dna_packet", {}),
                "experience_packet": evidence_packets.get("experience_packet", {}),
            },
        }
    ]


def _experience_rows(
    base: dict[str, Any],
    normalized: dict[str, Any],
    experience: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    project_type_map = {
        (entry.get("company"), entry.get("title")): entry.get("project_type")
        for entry in experience.get("project_types", [])
        if isinstance(entry, dict)
    }
    for index, item in enumerate(normalized.get("experience") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                **base,
                "task": "project_type_classification",
                "record_id": f"{base['resume_id']}_exp_{index + 1}",
                "label_source": "silver_bootstrap",
                "label": project_type_map.get((item.get("company"), item.get("title")), "UNKNOWN"),
                "role_title": item.get("title"),
                "company": item.get("company"),
                "text": normalize_text(
                    " | ".join(
                        part
                        for part in [
                            item.get("title"),
                            item.get("company"),
                            item.get("start_date"),
                            item.get("end_date"),
                            item.get("description"),
                        ]
                        if part
                    )
                ),
            }
        )
    return rows


def _skill_rows(
    base: dict[str, Any],
    evidence_map: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ranked = sorted(
        evidence_map.values(),
        key=lambda item: (
            ["NONE", "MENTION", "WEAK", "APPLIED", "DEEP", "EXPERT"].index(item.get("evidence_level", "NONE")),
            item.get("years_of_usage", 0),
            item.get("matched_context_count", 0),
        ),
        reverse=True,
    )
    for item in ranked:
        skill_name = item.get("skill")
        if not skill_name:
            continue

        evidence_level = str(item.get("evidence_level") or "NONE").upper()
        depth_label = str(item.get("depth_label") or "").upper()
        years_of_usage = float(item.get("years_of_usage") or 0)

        # Confidence gate: skip ambiguous WEAK evidence with very low usage
        if evidence_level == "WEAK" and years_of_usage < 1.0:
            continue

        # Assign AWARENESS class for low-signal entries
        if evidence_level in {"NONE", "MENTION"} or depth_label == "AWARENESS":
            effective_depth_label = "AWARENESS"
        else:
            effective_depth_label = depth_label if depth_label else None

        evidence_used = []
        for ctx in item.get("contexts", [])[:3]:
            evidence_used.append(
                normalize_text(
                    " | ".join(
                        part
                        for part in [
                            ctx.get("title"),
                            ctx.get("company"),
                            ctx.get("context"),
                        ]
                        if part
                    )
                )
            )

        # Enriched signals
        strength_score = item.get("strength_score") or item.get("years_of_usage") or 0
        artifact_evidence = bool(item.get("architecture_signal") or item.get("open_source_signal"))
        advanced_topic_signals = bool(item.get("architecture_signal"))

        rows.append(
            {
                **base,
                "task": "skill_depth_classification",
                "record_id": f"{base['resume_id']}_{normalize_text(str(skill_name)).lower().replace(' ', '_')}",
                "label_source": "silver_bootstrap",
                "skill": skill_name,
                "classifier_text": normalize_text(
                    "\n".join(
                        [
                            f"Skill: {skill_name}",
                            f"Resume summary: {base.get('resume_text', '')}",
                            "Evidence: " + " ; ".join(entry for entry in evidence_used if entry),
                            "Signals: "
                            + ", ".join(
                                str(part)
                                for part in [
                                    item.get("cluster"),
                                    evidence_level,
                                    effective_depth_label,
                                    f"weighted_years={years_of_usage}",
                                    f"raw_years={item.get('raw_years_of_usage')}",
                                    f"contexts={item.get('matched_context_count')}",
                                    f"recency={item.get('recency')}",
                                    f"strength_score={strength_score}",
                                    "artifact_evidence" if artifact_evidence else "",
                                    "advanced_topic" if advanced_topic_signals else "",
                                    "coding" if item.get("coding_signal") else "",
                                ]
                                if str(part).strip()
                            ),
                        ]
                    )
                ),
                "label": {
                    "present": evidence_level not in {"NONE", "MENTION"},
                    "depth_label": effective_depth_label,
                    "evidence_level": evidence_level,
                    "score_prior_0_to_5": min(
                        5,
                        max(
                            0,
                            (
                                5
                                if effective_depth_label == "ARCHITECT_LEVEL"
                                else 4
                                if effective_depth_label == "ADVANCED"
                                else 3
                                if effective_depth_label == "HANDS_ON"
                                else 2
                                if effective_depth_label == "FOUNDATIONAL"
                                else 1
                                if effective_depth_label == "AWARENESS"
                                else 0
                            ),
                        ),
                    ),
                    "recency": item.get("recency"),
                },
                "features": {
                    "cluster": item.get("cluster"),
                    "weighted_years": years_of_usage,
                    "raw_years": item.get("raw_years_of_usage"),
                    "matched_context_count": item.get("matched_context_count"),
                    "project_contexts": item.get("project_contexts", []),
                    "architecture_signal": bool(item.get("architecture_signal")),
                    "coding_signal": bool(item.get("coding_signal")),
                    "open_source_signal": bool(item.get("open_source_signal")),
                    "strength_score": strength_score,
                    "artifact_evidence": artifact_evidence,
                    "advanced_topic_signals": advanced_topic_signals,
                },
                "evidence_used": [entry for entry in evidence_used if entry],
            }
        )
    return rows


def _career_progression_rows(
    base: dict[str, Any],
    normalized: dict[str, Any],
    experience: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate career_progression training rows (DECLINING/LATERAL/GROWING/FAST_TRACK)."""
    trajectory_score = int(experience.get("career_trajectory_score") or 1)
    if trajectory_score >= 4:
        label = "FAST_TRACK"
    elif trajectory_score == 3:
        label = "GROWING"
    elif trajectory_score == 2:
        label = "LATERAL"
    else:
        label = "DECLINING"

    # Build chronological title|company|years sequence
    exp_items = [item for item in (normalized.get("experience") or []) if isinstance(item, dict)]
    # Sort chronologically (oldest first)
    def _sort_key(item: dict[str, Any]) -> str:
        return str(item.get("start_date") or "9999")
    exp_items_sorted = sorted(exp_items, key=_sort_key)

    role_tuples = []
    for item in exp_items_sorted[:8]:
        title = normalize_text(item.get("title") or "")
        company = normalize_text(item.get("company") or "")
        start = str(item.get("start_date") or "")
        end = str(item.get("end_date") or "present")
        if title:
            role_tuples.append(f"{title} @ {company} ({start}–{end})")

    titles = experience.get("titles", [])
    total_yoe = experience.get("total_experience_years", 0)

    classifier_text = normalize_text(
        " | ".join(role_tuples)
        + f"\nTotal YoE: {total_yoe}"
        + "\nTitles: " + ", ".join(str(t) for t in titles[:6])
    )

    if not classifier_text.strip():
        return []

    return [
        {
            **base,
            "task": "career_progression_classification",
            "label_source": "silver_bootstrap",
            "label": label,
            "classifier_text": classifier_text,
            "features": {
                "trajectory_score": trajectory_score,
                "total_yoe": total_yoe,
                "role_count": len(role_tuples),
            },
        }
    ]


def _stakeholder_rows(
    base: dict[str, Any],
    normalized: dict[str, Any],
    experience: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate stakeholder_management training rows (NONE/INTERNAL/CLIENT_FACING/C_LEVEL)."""
    client_facing = bool(experience.get("client_facing"))
    decision_maker = bool(experience.get("decision_maker"))
    leadership_score = int(experience.get("leadership_signal_score") or 0)

    # C_LEVEL: decision-maker signal (requires 6+ yoe) AND strong leadership
    if decision_maker and leadership_score > 1:
        label = "C_LEVEL"
    elif client_facing:
        label = "CLIENT_FACING"
    elif leadership_score > 0:
        label = "INTERNAL"
    else:
        label = "NONE"

    # Classifier text: top 5 job descriptions
    exp_items = [item for item in (normalized.get("experience") or []) if isinstance(item, dict)]
    descriptions = []
    for item in exp_items[:5]:
        desc = normalize_text(item.get("description") or "")
        title = normalize_text(item.get("title") or "")
        company = normalize_text(item.get("company") or "")
        if desc or title:
            descriptions.append(f"{title} @ {company}: {desc}"[:1000])

    classifier_text = normalize_text("\n---\n".join(descriptions))
    if not classifier_text.strip():
        return []

    return [
        {
            **base,
            "task": "stakeholder_management_classification",
            "label_source": "silver_bootstrap",
            "label": label,
            "classifier_text": classifier_text,
            "features": {
                "client_facing": client_facing,
                "decision_maker": decision_maker,
                "leadership_signal_score": leadership_score,
            },
        }
    ]


def _mentorship_rows(
    base: dict[str, Any],
    normalized: dict[str, Any],
    experience: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate mentorship_signal training rows (NONE/IMPLIED/FORMAL/LEAD)."""
    leadership_score = int(experience.get("leadership_signal_score") or 0)
    if leadership_score >= 3:
        label = "LEAD"
    elif leadership_score == 2:
        label = "FORMAL"
    elif leadership_score == 1:
        label = "IMPLIED"
    else:
        label = "NONE"

    # Classifier text: top 5 job titles + role descriptions with team/management language
    exp_items = [item for item in (normalized.get("experience") or []) if isinstance(item, dict)]
    segments = []
    for item in exp_items[:5]:
        title = normalize_text(item.get("title") or "")
        desc = normalize_text(item.get("description") or "")
        if title or desc:
            segments.append(f"{title}: {desc}"[:800])

    classifier_text = normalize_text("\n---\n".join(segments))
    if not classifier_text.strip():
        return []

    return [
        {
            **base,
            "task": "mentorship_signal_classification",
            "label_source": "silver_bootstrap",
            "label": label,
            "classifier_text": classifier_text,
            "features": {
                "leadership_signal_score": leadership_score,
            },
        }
    ]


def _manifest(
    source_files: list[Path],
    role_rows: list[dict[str, Any]],
    dna_rows: list[dict[str, Any]],
    project_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    career_progression_rows: list[dict[str, Any]] | None = None,
    stakeholder_rows: list[dict[str, Any]] | None = None,
    mentorship_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source_resume_count": len(source_files),
        "role_family_rows": len(role_rows),
        "dna_rows": len(dna_rows),
        "project_type_rows": len(project_rows),
        "skill_rows": len(skill_rows),
        "career_progression_rows": len(career_progression_rows or []),
        "stakeholder_management_rows": len(stakeholder_rows or []),
        "mentorship_signal_rows": len(mentorship_rows or []),
        "label_source": "silver_bootstrap",
        "notes": [
            "These rows are generated from the existing extracted JSON format.",
            "Labels are weak priors from the current parser/evidence engines, not final gold labels.",
            "Use recruiter corrections and interview outcomes to replace silver labels with gold labels before full supervised training.",
        ],
    }


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def build_training_exports(source_paths: list[str], output_dir: str) -> dict[str, Any]:
    files = _json_files(source_paths)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    role_rows: list[dict[str, Any]] = []
    dna_rows_list: list[dict[str, Any]] = []
    project_rows: list[dict[str, Any]] = []
    skill_rows: list[dict[str, Any]] = []
    career_progression_rows: list[dict[str, Any]] = []
    stakeholder_rows: list[dict[str, Any]] = []
    mentorship_rows: list[dict[str, Any]] = []

    for path in files:
        raw = _load_resume(path)
        normalized = normalize_resume_data(raw)
        overview = _overview(normalized)
        evidence_map = collect_skill_evidence(normalized)
        semantic = build_semantic_taxonomy(evidence_map, normalized)
        experience = analyze_experience(normalized)
        dna = classify_dna(normalized)
        evidence_packets = build_evidence_packets(overview, evidence_map, semantic, experience, dna)
        base = _base_record(path, normalized)

        role_rows.extend(_role_family_rows(base, semantic, evidence_packets, experience))
        dna_rows_list.extend(_dna_rows(base, dna, experience, evidence_packets))
        project_rows.extend(_experience_rows(base, normalized, experience))
        skill_rows.extend(_skill_rows(base, evidence_map))
        career_progression_rows.extend(_career_progression_rows(base, normalized, experience))
        stakeholder_rows.extend(_stakeholder_rows(base, normalized, experience))
        mentorship_rows.extend(_mentorship_rows(base, normalized, experience))

    manifest = _manifest(
        files, role_rows, dna_rows_list, project_rows, skill_rows,
        career_progression_rows, stakeholder_rows, mentorship_rows,
    )
    outputs = {
        "role_family": output / "role_family.jsonl",
        "dna_fit": output / "dna_fit.jsonl",
        "project_type": output / "project_type.jsonl",
        "skill_depth": output / "skill_depth.jsonl",
        "career_progression": output / "career_progression.jsonl",
        "stakeholder_management": output / "stakeholder_management.jsonl",
        "mentorship_signal": output / "mentorship_signal.jsonl",
    }
    counts = {
        "role_family": _write_jsonl(outputs["role_family"], role_rows),
        "dna_fit": _write_jsonl(outputs["dna_fit"], dna_rows_list),
        "project_type": _write_jsonl(outputs["project_type"], project_rows),
        "skill_depth": _write_jsonl(outputs["skill_depth"], skill_rows),
        "career_progression": _write_jsonl(outputs["career_progression"], career_progression_rows),
        "stakeholder_management": _write_jsonl(outputs["stakeholder_management"], stakeholder_rows),
        "mentorship_signal": _write_jsonl(outputs["mentorship_signal"], mentorship_rows),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Training exports: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return {
        "output_dir": str(output),
        "source_resume_count": len(files),
        "counts": counts,
        "files": {name: str(path) for name, path in outputs.items()},
        "manifest": str(output / "manifest.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build BERT/LLM training exports directly from the extracted resume JSON format already used by the app."
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="One or more resume JSON files or directories containing resume JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        default="training_exports",
        help="Directory where JSONL training exports should be written.",
    )
    args = parser.parse_args()
    result = build_training_exports(args.sources, args.output_dir)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
