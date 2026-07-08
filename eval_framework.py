from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4

from engine import analyze_resume
from models import ResumeInput


DEFAULT_GATES: dict[str, dict[str, float | None]] = {
    "llm_score_success_rate": {"min": 0.6, "delta_min": -0.05},
    "llm_skill_success_rate": {"min": 0.6, "delta_min": -0.05},
    "score_consistency_rate": {"min": 1.0, "delta_min": -0.01},
    "expectation_match_rate": {"min": 0.75, "delta_min": -0.05},
    "role_family_match_rate": {"min": 0.7, "delta_min": -0.05},
    "band_match_rate": {"min": 0.75, "delta_min": -0.05},
    "score_mae": {"max": 8.0, "delta_max": 2.0},
    # BERT coverage gates — % of cases where model ran with conf >= threshold
    "skill_depth_bert_coverage": {"min": 0.65},
    "role_family_bert_coverage": {"min": 0.55},
    "dna_bert_coverage": {"min": 0.55},
    "rubric_parameter_consistency_rate": {"min": 0.90},
}


def _now_utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "run"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _collect_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(path for path in input_path.rglob("*.json") if path.is_file())


def _score_consistency(scorecard: dict[str, Any]) -> bool:
    component_keys = [
        "skill_score",
        "experience_score",
        "role_alignment_score",
        "impact_score",
        "stability_score",
        "dna_score",
    ]
    component_total = sum(int(scorecard.get(key, 0) or 0) for key in component_keys)
    return component_total == int(scorecard.get("total_score", 0) or 0)


def _confidence_to_score(value: str | None) -> float:
    normalized = str(value or "").strip().upper()
    if normalized == "HIGH":
        return 1.0
    if normalized == "MEDIUM":
        return 0.66
    if normalized == "LOW":
        return 0.33
    return 0.0


def _normalized_ratio(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return max(0.0, min(1.0, value / maximum))


def _extraction_completeness_score(analysis: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    overview = analysis.get("candidate_overview", {}) or {}
    checks = {
        "name": overview.get("name") not in (None, "", "N/A"),
        "email": overview.get("email") not in (None, "", "N/A"),
        "phone": overview.get("phone") not in (None, "", "N/A"),
        "location": overview.get("location") not in (None, "", "N/A"),
        "profile_summary": overview.get("profile_summary") not in (None, "", "N/A"),
    }
    score = round(sum(1.0 for ok in checks.values() if ok) / max(len(checks), 1), 3)
    return score, checks


def _evidence_density_score(analysis: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    semantic = analysis.get("semantic_analysis", {}) or {}
    scorecard = analysis.get("scorecard", {}) or {}
    skill_analysis = analysis.get("skill_analysis", {}) or {}
    component_inputs = scorecard.get("component_inputs", {}) or {}
    skill_inputs = component_inputs.get("skill_score", {}) or {}
    top_skills = skill_analysis.get("top_skills", []) or []
    strong_skill_count = int(skill_inputs.get("strong_skills", 0) or 0)
    evidence_backed_ratio = float(semantic.get("skill_consistency_score", 0) or 0)
    recent_skill_hits = int(skill_inputs.get("recent_skill_hits", 0) or 0)
    architecture_hits = int(skill_inputs.get("architecture_skill_hits_count", 0) or 0)
    judged_skill_ratio = 0.0
    if top_skills:
        judged_skill_ratio = sum(1.0 for item in top_skills if item.get("judged_reason")) / len(top_skills)
    blended = (
        (evidence_backed_ratio * 0.45)
        + (_normalized_ratio(strong_skill_count, 8) * 0.25)
        + (_normalized_ratio(recent_skill_hits, 5) * 0.15)
        + (_normalized_ratio(architecture_hits, 3) * 0.05)
        + (judged_skill_ratio * 0.10)
    )
    return round(min(1.0, blended), 3), {
        "skill_consistency_score": evidence_backed_ratio,
        "strong_skill_count": strong_skill_count,
        "recent_skill_hits": recent_skill_hits,
        "architecture_hits": architecture_hits,
        "judged_skill_ratio": round(judged_skill_ratio, 3),
    }


def _fallback_reliability_score(analysis: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    scorecard = analysis.get("scorecard", {}) or {}
    llm_status = analysis.get("llm_status", {}) or {}
    score_used = 1.0 if scorecard.get("llm_used") else 0.45
    skill_used = 1.0 if llm_status.get("skill_judgment") == "applied" else 0.4
    dna_used = 1.0 if llm_status.get("dna_judgment") == "applied" else 0.55
    overall = round((score_used * 0.45) + (skill_used * 0.3) + (dna_used * 0.25), 3)
    return overall, {
        "score_judgment": llm_status.get("score_judgment"),
        "skill_judgment": llm_status.get("skill_judgment"),
        "dna_judgment": llm_status.get("dna_judgment"),
    }


def _analysis_confidence(analysis: dict[str, Any]) -> dict[str, Any]:
    scorecard = analysis.get("scorecard", {}) or {}
    confidences = scorecard.get("dimension_confidence", {}) or {}
    confidence_values = [_confidence_to_score(value) for value in confidences.values()]
    model_certainty = round(sum(confidence_values) / max(len(confidence_values), 1), 3) if confidence_values else 0.0
    evidence_density, evidence_detail = _evidence_density_score(analysis)
    extractor_completeness, extractor_detail = _extraction_completeness_score(analysis)
    fallback_reliability, fallback_detail = _fallback_reliability_score(analysis)
    consistency_score = 1.0 if _score_consistency(scorecard) else 0.35
    avg_score = round(
        (model_certainty * 0.35)
        + (evidence_density * 0.25)
        + (consistency_score * 0.10)
        + (fallback_reliability * 0.20)
        + (extractor_completeness * 0.10),
        3,
    )
    label = "LOW"
    if avg_score >= 0.84:
        label = "HIGH"
    elif avg_score >= 0.5:
        label = "MEDIUM"
    return {
        "label": label,
        "score": avg_score,
        "raw_score": avg_score,
        "components": {
            "model_certainty": model_certainty,
            "evidence_density": evidence_density,
            "score_consistency": consistency_score,
            "fallback_reliability": fallback_reliability,
            "extractor_completeness": extractor_completeness,
        },
        "details": {
            "dimension_confidence": confidences,
            "evidence": evidence_detail,
            "fallbacks": fallback_detail,
            "extractor": extractor_detail,
        },
        "calibration": {
            "status": "uncalibrated",
            "support": 0,
            "window": None,
        },
    }


def _confidence_label(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= 0.84:
        return "HIGH"
    if score >= 0.5:
        return "MEDIUM"
    return "LOW"


def _load_confidence_calibration_points(runs_dir: Path | None = None) -> list[dict[str, float]]:
    root = runs_dir or Path("eval_runs")
    if not root.exists():
        return []
    points: list[dict[str, float]] = []
    for report_path in root.rglob("report.json"):
        try:
            report = _load_json(report_path)
        except Exception:
            continue
        for case in report.get("cases", []) or []:
            expectation_result = case.get("expectation_result")
            confidence = case.get("confidence", {}) or {}
            score = confidence.get("score")
            if expectation_result is None or score is None:
                continue
            if expectation_result.get("matched") not in {True, False}:
                continue
            points.append(
                {
                    "score": float(score),
                    "matched": 1.0 if expectation_result.get("matched") else 0.0,
                }
            )
    return points


def _apply_confidence_calibration(confidence: dict[str, Any], calibration_points: list[dict[str, float]] | None) -> dict[str, Any]:
    points = calibration_points or []
    raw_score = confidence.get("raw_score")
    if raw_score is None or len(points) < 20:
        return confidence
    scored = sorted(points, key=lambda item: abs(item["score"] - float(raw_score)))
    support = min(len(scored), 25)
    neighborhood = scored[:support]
    if support < 10:
        return confidence
    calibrated = round(sum(item["matched"] for item in neighborhood) / support, 3)
    window = round(max(abs(item["score"] - float(raw_score)) for item in neighborhood), 3) if neighborhood else 0.0
    return {
        **confidence,
        "score": calibrated,
        "label": _confidence_label(calibrated),
        "calibration": {
            "status": "empirical_knn",
            "support": support,
            "window": window,
        },
    }


def _business_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    scorecard = analysis.get("scorecard", {}) or {}
    experience = analysis.get("experience_analysis", {}) or {}
    qualitative = analysis.get("qualitative_analysis", {}) or {}
    telephonic = analysis.get("telephonic_round", {}) or {}
    total_score = int(scorecard.get("total_score", 0) or 0)
    return {
        "screening_recommended": total_score >= 65,
        "telephonic_enabled": bool(telephonic.get("enabled")),
        "risk_flag_count": len(qualitative.get("risk_flags", []) or []),
        "gap_count": len(qualitative.get("gaps", []) or []),
        "strength_count": len(qualitative.get("strengths", []) or []),
        "impact_marker_count": len(experience.get("business_impacts", []) or []),
        "decision_maker_signal": bool(experience.get("decision_maker")),
    }


def _failure_trace(analysis: dict[str, Any]) -> dict[str, Any]:
    llm_status = analysis.get("llm_status", {}) or {}
    scorecard = analysis.get("scorecard", {}) or {}
    telemetry = analysis.get("llm_telemetry", {}) or {}
    failed_requests = [item for item in (telemetry.get("requests", []) or []) if not item.get("success")]
    return {
        "score_failure_reason": scorecard.get("llm_failure_reason", ""),
        "skill_judgment_reason": llm_status.get("skill_judgment_reason", ""),
        "dna_judgment_reason": llm_status.get("dna_judgment_reason", ""),
        "failed_llm_requests": failed_requests,
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _extract_case_id(path: Path, payload: dict[str, Any]) -> str:
    return str(
        _first_present(
            payload.get("record_id"),
            payload.get("resume_id"),
            payload.get("candidate_id"),
            path.stem,
        )
    )


def _extract_case_id_from_payload(payload: dict[str, Any], fallback_name: str = "live-analysis") -> str:
    return str(
        _first_present(
            payload.get("record_id"),
            payload.get("resume_id"),
            payload.get("candidate_id"),
            payload.get("file_name"),
            fallback_name,
        )
    )


def _normalize_expectations(raw: dict[str, Any], files: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not raw:
        return {}, {}
    if "cases" in raw and isinstance(raw["cases"], list):
        normalized: dict[str, dict[str, Any]] = {}
        dataset_meta = {key: value for key, value in raw.items() if key != "cases"}
        for item in raw["cases"]:
            if not isinstance(item, dict):
                continue
            case_id = str(_first_present(item.get("case_id"), item.get("record_id"), item.get("file"), item.get("filename"), "")).strip()
            if case_id:
                normalized[case_id] = item
        return normalized, dataset_meta

    file_names = {path.name for path in files}
    if any(key in file_names for key in raw.keys()):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}, {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}, {}


def _lookup_expectation(path: Path, case_id: str, expectations: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return expectations.get(case_id) or expectations.get(path.name) or expectations.get(path.stem)


def _build_case_tags(analysis: dict[str, Any], expectation: dict[str, Any] | None) -> list[str]:
    tags = set()
    scorecard = analysis.get("scorecard", {}) or {}
    llm_status = analysis.get("llm_status", {}) or {}
    role_family = str(analysis.get("semantic_analysis", {}).get("top_role_family", "") or "").strip()
    band = str(scorecard.get("band", "") or "").strip()
    if role_family:
        tags.add(f"role:{role_family}")
    if band:
        tags.add(f"band:{band}")
    tags.add("score:consistent" if _score_consistency(scorecard) else "score:inconsistent")
    tags.add("llm_score:applied" if scorecard.get("llm_used") else "llm_score:fallback")
    tags.add("llm_skill:applied" if llm_status.get("skill_judgment") == "applied" else "llm_skill:fallback")
    if expectation:
        for tag in expectation.get("tags", []) or []:
            if tag:
                tags.add(f"user:{tag}")
    return sorted(tags)


def _expectation_result(expectation: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    result = {"matched": True, "checks": []}
    semantic = analysis.get("semantic_analysis", {}) or {}
    scorecard = analysis.get("scorecard", {}) or {}
    dna_fit = analysis.get("dna_fit", {}) or {}
    overview = analysis.get("candidate_overview", {}) or {}

    role_family = str(semantic.get("top_role_family", "") or "")
    band = str(scorecard.get("band", "") or "")
    total_score = int(scorecard.get("total_score", 0) or 0)
    dna = str(dna_fit.get("primary_dna", "") or "")
    candidate_name = str(overview.get("name", "") or "")

    def add_check(field: str, expected: Any, actual: Any, matched: bool, detail: str | None = None) -> None:
        result["checks"].append(
            {
                "field": field,
                "expected": expected,
                "actual": actual,
                "matched": matched,
                "detail": detail or "",
            }
        )
        result["matched"] = result["matched"] and matched

    if expectation.get("expected_role_family"):
        add_check("expected_role_family", expectation["expected_role_family"], role_family, role_family == str(expectation["expected_role_family"]))
    if expectation.get("expected_band"):
        add_check("expected_band", expectation["expected_band"], band, band == str(expectation["expected_band"]))
    if expectation.get("expected_dna"):
        add_check("expected_dna", expectation["expected_dna"], dna, dna == str(expectation["expected_dna"]))
    if expectation.get("expected_total_score") is not None:
        tolerance = int(expectation.get("score_tolerance", 0) or 0)
        expected_score = int(expectation["expected_total_score"])
        difference = abs(total_score - expected_score)
        add_check(
            "expected_total_score",
            expected_score,
            total_score,
            difference <= tolerance,
            detail=f"tolerance={tolerance}, diff={difference}",
        )
    if expectation.get("min_score") is not None:
        add_check("min_score", expectation["min_score"], total_score, total_score >= int(expectation["min_score"]))
    if expectation.get("max_score") is not None:
        add_check("max_score", expectation["max_score"], total_score, total_score <= int(expectation["max_score"]))
    if expectation.get("name_required") is True:
        add_check("name_required", True, candidate_name, candidate_name not in {"", "N/A"})
    if expectation.get("must_use_llm_score") is True:
        used = bool(scorecard.get("llm_used"))
        add_check("must_use_llm_score", True, used, used)

    return result


def _extract_prediction_snapshot(analysis: dict[str, Any]) -> dict[str, Any]:
    scorecard = analysis.get("scorecard", {}) or {}
    return {
        "role_family": analysis.get("semantic_analysis", {}).get("top_role_family"),
        "band": scorecard.get("band"),
        "dna": analysis.get("dna_fit", {}).get("primary_dna"),
        "total_score": int(scorecard.get("total_score", 0) or 0),
        "llm_used": bool(scorecard.get("llm_used")),
        "llm_skill_used": analysis.get("llm_status", {}).get("skill_judgment") == "applied",
    }


def _extract_bert_predictions(analysis: dict[str, Any], expectation: dict[str, Any] | None) -> dict[str, Any]:
    """Extract per-case BERT prediction accuracy tracking."""
    evidence_packets = analysis.get("evidence_packets", {}) or {}
    bert_priors = evidence_packets.get("bert_priors", {}) or {}

    role_prior = bert_priors.get("role_family_prior", {}) or {}
    dna_prior = bert_priors.get("dna_prior", {}) or {}
    cp_prior = bert_priors.get("career_progression_prior", {}) or {}
    sm_prior = bert_priors.get("stakeholder_prior", {}) or {}
    ms_prior = bert_priors.get("mentorship_prior", {}) or {}
    skill_priors = bert_priors.get("skill_depth_priors", []) or []

    exp = expectation or {}

    # Check role_family match if expectation available
    role_label = role_prior.get("label")
    exp_role = exp.get("expected_role_family")
    role_matched = (role_label == exp_role) if (role_label and exp_role) else None

    dna_label = dna_prior.get("label")
    exp_dna = exp.get("expected_dna")
    dna_matched = (dna_label == exp_dna) if (dna_label and exp_dna) else None

    # Average BERT confidence for skill_depth priors
    skill_confidences = [
        float(p.get("confidence") or 0)
        for p in skill_priors
        if isinstance(p, dict) and p.get("confidence") is not None
    ]
    avg_skill_conf = round(sum(skill_confidences) / len(skill_confidences), 4) if skill_confidences else None

    return {
        "role_family": {
            "predicted": role_label,
            "confidence": round(float(role_prior.get("confidence") or 0), 4),
            "source": role_prior.get("source"),
            "matched_expected": role_matched,
        },
        "dna_fit": {
            "predicted": dna_label,
            "confidence": round(float(dna_prior.get("confidence") or 0), 4),
            "source": dna_prior.get("source"),
            "matched_expected": dna_matched,
        },
        "career_progression": {
            "predicted": cp_prior.get("label"),
            "confidence": round(float(cp_prior.get("confidence") or 0), 4),
            "source": cp_prior.get("source"),
        },
        "stakeholder_management": {
            "predicted": sm_prior.get("label"),
            "confidence": round(float(sm_prior.get("confidence") or 0), 4),
            "source": sm_prior.get("source"),
        },
        "mentorship_signal": {
            "predicted": ms_prior.get("label"),
            "confidence": round(float(ms_prior.get("confidence") or 0), 4),
            "source": ms_prior.get("source"),
        },
        "skill_depth_avg_confidence": avg_skill_conf,
        "skill_depth_count": len(skill_priors),
    }


def _extract_expectation_targets(expectation: dict[str, Any] | None) -> dict[str, Any]:
    if not expectation:
        return {}
    return {
        "expected_role_family": expectation.get("expected_role_family"),
        "expected_band": expectation.get("expected_band"),
        "expected_dna": expectation.get("expected_dna"),
        "expected_total_score": expectation.get("expected_total_score"),
        "score_tolerance": expectation.get("score_tolerance"),
        "min_score": expectation.get("min_score"),
        "max_score": expectation.get("max_score"),
        "tags": expectation.get("tags", []),
    }


def _make_case_record(path: Path, analysis: dict[str, Any], expectation: dict[str, Any] | None, case_id: str, calibration_points: list[dict[str, float]] | None = None) -> dict[str, Any]:
    scorecard = analysis.get("scorecard", {}) or {}
    llm_status = analysis.get("llm_status", {}) or {}
    overview = analysis.get("candidate_overview", {}) or {}
    telemetry = analysis.get("llm_telemetry", {}) or {}
    expectation_result = _expectation_result(expectation, analysis) if expectation else None
    predicted = _extract_prediction_snapshot(analysis)
    confidence = _apply_confidence_calibration(_analysis_confidence(analysis), calibration_points)
    bert_predictions = _extract_bert_predictions(analysis, expectation)

    return {
        "case_id": case_id,
        "file": str(path),
        "candidate_name": overview.get("name"),
        "analysis_snapshot": {
            "candidate_overview": analysis.get("candidate_overview", {}) or {},
            "semantic_analysis": analysis.get("semantic_analysis", {}) or {},
            "dna_fit": analysis.get("dna_fit", {}) or {},
            "recruiter_summary": analysis.get("recruiter_summary", ""),
        },
        "predicted": predicted,
        "expected": _extract_expectation_targets(expectation),
        "expectation_result": expectation_result,
        "score_consistent": _score_consistency(scorecard),
        "missing_component_justifications": [
            key
            for key, value in (scorecard.get("component_justifications", {}) or {}).items()
            if not isinstance(value, dict) or not value.get("strongest_evidence") or not value.get("main_gap") or not value.get("why_not_lower")
        ],
        "missing_component_rationales": [
            key
            for key, value in (scorecard.get("component_rationales", {}) or {}).items()
            if not value
        ],
        "skill_count": len(analysis.get("skill_analysis", {}).get("all_skills", {}) or {}),
        "top_skill_count": len(analysis.get("skill_analysis", {}).get("top_skills", []) or []),
        "skill_judgment_reason": llm_status.get("skill_judgment_reason", ""),
        "score_failure_reason": scorecard.get("llm_failure_reason", ""),
        "tags": _build_case_tags(analysis, expectation),
        "latency_ms": round(float(telemetry.get("wall_clock_latency_ms", telemetry.get("total_latency_ms", 0)) or 0), 2),
        "average_llm_latency_ms": round(float(telemetry.get("average_latency_ms", 0) or 0), 2),
        "prompt_tokens": int(telemetry.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(telemetry.get("completion_tokens", 0) or 0),
        "total_tokens": int(telemetry.get("total_tokens", 0) or 0),
        "remaining_context_tokens": int(telemetry.get("remaining_context_tokens_max", 0) or 0),
        "estimated_cost_usd": float(telemetry.get("estimated_cost_usd", 0) or 0),
        "end_to_end_cost_usd": float(telemetry.get("estimated_cost_usd", 0) or 0),
        "confidence": confidence,
        "business_metrics": _business_metrics(analysis),
        "failure_trace": _failure_trace(analysis),
        "rubric_scorecard": analysis.get("rubric_scorecard") or {},
        "bert_predictions": bert_predictions,
    }


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def _rubric_parameter_stats(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute distribution stats for each rubric parameter across all evaluated cases."""
    # Map of param_name -> {section, max_pts, scores[]}
    param_data: dict[str, dict[str, Any]] = {}
    bert_driven_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}

    for case in cases:
        rubric = case.get("rubric_scorecard", {}) or {}
        breakdown = rubric.get("breakdown", {}) or {}
        for section_name, section in breakdown.items():
            if not isinstance(section, dict):
                continue
            for param, entry in section.items():
                if not isinstance(entry, dict) or "score" not in entry:
                    continue
                score = float(entry.get("score") or 0)
                max_pts = float(entry.get("max") or 0)
                bert_driven = bool(entry.get("bert_label") and entry.get("bert_confidence", 0) >= 0.45)

                if param not in param_data:
                    param_data[param] = {"section": section_name, "max_pts": max_pts, "scores": [], "dist": {}}
                    bert_driven_counts[param] = 0
                    total_counts[param] = 0

                param_data[param]["scores"].append(score)
                score_key = int(round(score))
                param_data[param]["dist"][score_key] = param_data[param]["dist"].get(score_key, 0) + 1
                if bert_driven:
                    bert_driven_counts[param] += 1
                total_counts[param] += 1

    result = {}
    for param, data in sorted(param_data.items()):
        scores = data["scores"]
        result[param] = {
            "section": data["section"],
            "max_possible": data["max_pts"],
            "mean": round(sum(scores) / len(scores), 3) if scores else None,
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "distribution": {str(k): v for k, v in sorted(data["dist"].items())},
            "bert_driven_rate": round(bert_driven_counts[param] / max(total_counts[param], 1), 3),
            "n": len(scores),
        }
    return result


def _bert_confusion_matrices(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Build per-task confusion matrices from bert_predictions where expected labels are available."""
    tasks = {
        "role_family": ("role_family", "expected_role_family"),
        "dna_fit": ("dna_fit", "expected_dna"),
    }
    result = {}
    for task_key, (pred_key, exp_key) in tasks.items():
        predictions = []
        actuals = []
        for case in cases:
            bert = case.get("bert_predictions", {}) or {}
            exp = case.get("expected", {}) or {}
            pred_entry = bert.get(pred_key, {}) or {}
            predicted = pred_entry.get("predicted")
            expected = exp.get(exp_key)
            if predicted and expected:
                predictions.append(str(predicted))
                actuals.append(str(expected))

        if not predictions:
            continue

        labels = sorted(set(predictions) | set(actuals))
        label2idx = {l: i for i, l in enumerate(labels)}
        n = len(labels)
        matrix = [[0] * n for _ in range(n)]
        for pred, act in zip(predictions, actuals):
            matrix[label2idx[act]][label2idx[pred]] += 1

        per_class_acc = []
        for i, label in enumerate(labels):
            row_total = sum(matrix[i])
            acc = round(matrix[i][i] / row_total, 3) if row_total > 0 else None
            per_class_acc.append({"label": label, "accuracy": acc, "support": row_total})

        correct = sum(matrix[i][i] for i in range(n))
        total = sum(sum(row) for row in matrix)
        macro_acc = round(
            sum(p["accuracy"] for p in per_class_acc if p["accuracy"] is not None)
            / max(sum(1 for p in per_class_acc if p["accuracy"] is not None), 1),
            3,
        )
        result[task_key] = {
            "labels": labels,
            "matrix": matrix,
            "per_class_accuracy": per_class_acc,
            "macro_accuracy": macro_acc,
            "total_accuracy": round(correct / total, 3) if total > 0 else None,
            "n": total,
        }
    return result


def _bert_coverage_rates(cases: list[dict[str, Any]]) -> dict[str, float | None]:
    """Compute % of cases where each BERT model ran with confidence >= 0.45."""
    skill_covered = []
    role_covered = []
    dna_covered = []

    for case in cases:
        bert = case.get("bert_predictions", {}) or {}
        role_conf = float((bert.get("role_family") or {}).get("confidence") or 0)
        dna_conf = float((bert.get("dna_fit") or {}).get("confidence") or 0)
        skill_conf = bert.get("skill_depth_avg_confidence")

        role_covered.append(1.0 if role_conf >= 0.45 else 0.0)
        dna_covered.append(1.0 if dna_conf >= 0.45 else 0.0)
        if skill_conf is not None:
            skill_covered.append(1.0 if float(skill_conf) >= 0.45 else 0.0)

    return {
        "skill_depth_bert_coverage": _avg(skill_covered),
        "role_family_bert_coverage": _avg(role_covered),
        "dna_bert_coverage": _avg(dna_covered),
    }


def _slice_metrics(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    tag_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        for tag in case.get("tags", []):
            tag_buckets[tag].append(case)

    slices = []
    for tag, bucket in sorted(tag_buckets.items()):
        expectation_cases = [item for item in bucket if item.get("expectation_result")]
        slices.append(
            {
                "tag": tag,
                "count": len(bucket),
                "average_total_score": _avg([float(item["predicted"]["total_score"]) for item in bucket]),
                "expectation_match_rate": _avg([1.0 if item["expectation_result"]["matched"] else 0.0 for item in expectation_cases]) if expectation_cases else None,
                "llm_score_success_rate": _avg([1.0 if item["predicted"]["llm_used"] else 0.0 for item in bucket]),
                "llm_skill_success_rate": _avg([1.0 if item["predicted"]["llm_skill_used"] else 0.0 for item in bucket]),
                "score_consistency_rate": _avg([1.0 if item["score_consistent"] else 0.0 for item in bucket]),
                "average_confidence_score": _avg([float(item.get("confidence", {}).get("score") or 0.0) for item in bucket if item.get("confidence", {}).get("score") is not None]),
                "average_latency_ms": _avg([float(item.get("latency_ms", 0) or 0) for item in bucket]),
                "average_cost_usd": _avg([float(item.get("end_to_end_cost_usd", 0) or 0) for item in bucket]),
            }
        )
    return {"tag_slices": slices}


def _classification_metrics(cases: list[dict[str, Any]], expected_key: str, predicted_key: str) -> dict[str, Any]:
    labeled = [
        case for case in cases
        if case.get("expected", {}).get(expected_key) not in (None, "", [], {})
    ]
    if not labeled:
        return {"coverage": 0, "match_rate": None, "support_by_label": {}, "errors_by_predicted_label": {}}

    matches = []
    support = Counter()
    errors = Counter()
    for case in labeled:
        expected = str(case["expected"][expected_key])
        predicted = str(case["predicted"].get(predicted_key) or "")
        support[expected] += 1
        matched = predicted == expected
        matches.append(1.0 if matched else 0.0)
        if not matched:
            errors[predicted or "EMPTY"] += 1
    return {
        "coverage": len(labeled),
        "match_rate": _avg(matches),
        "support_by_label": dict(sorted(support.items())),
        "errors_by_predicted_label": dict(sorted(errors.items(), key=lambda item: item[1], reverse=True)),
    }


def _score_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [
        case for case in cases
        if case.get("expected", {}).get("expected_total_score") is not None
    ]
    if not labeled:
        return {"coverage": 0, "mae": None, "max_abs_error": None}
    abs_errors = [
        abs(int(case["predicted"]["total_score"]) - int(case["expected"]["expected_total_score"]))
        for case in labeled
    ]
    return {
        "coverage": len(labeled),
        "mae": _avg([float(value) for value in abs_errors]),
        "max_abs_error": max(abs_errors) if abs_errors else None,
    }


def _top_failures(cases: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    failures = []
    for case in cases:
        expectation_result = case.get("expectation_result")
        if expectation_result and not expectation_result.get("matched"):
            failures.append(
                {
                    "case_id": case["case_id"],
                    "file": case["file"],
                    "candidate_name": case["candidate_name"],
                    "predicted": case["predicted"],
                    "expected": case["expected"],
                    "failed_checks": [check for check in expectation_result.get("checks", []) if not check.get("matched")],
                    "score_failure_reason": case.get("score_failure_reason", ""),
                    "skill_judgment_reason": case.get("skill_judgment_reason", ""),
                    "tags": case.get("tags", []),
                    "confidence": case.get("confidence", {}),
                    "latency_ms": case.get("latency_ms"),
                    "total_tokens": case.get("total_tokens"),
                    "end_to_end_cost_usd": case.get("end_to_end_cost_usd"),
                    "failure_trace": case.get("failure_trace", {}),
                }
            )
        elif case.get("missing_component_justifications") or case.get("missing_component_rationales") or not case.get("score_consistent"):
            failures.append(
                {
                    "case_id": case["case_id"],
                    "file": case["file"],
                    "candidate_name": case["candidate_name"],
                    "predicted": case["predicted"],
                    "expected": case.get("expected", {}),
                    "failed_checks": [],
                    "score_failure_reason": case.get("score_failure_reason", ""),
                    "skill_judgment_reason": case.get("skill_judgment_reason", ""),
                    "missing_component_justifications": case.get("missing_component_justifications", []),
                    "missing_component_rationales": case.get("missing_component_rationales", []),
                    "score_consistent": case.get("score_consistent"),
                    "tags": case.get("tags", []),
                    "confidence": case.get("confidence", {}),
                    "latency_ms": case.get("latency_ms"),
                    "total_tokens": case.get("total_tokens"),
                    "end_to_end_cost_usd": case.get("end_to_end_cost_usd"),
                    "failure_trace": case.get("failure_trace", {}),
                }
            )
    return failures[:limit]


def _summary_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    expectation_cases = [case for case in cases if case.get("expectation_result")]
    role_metrics = _classification_metrics(cases, "expected_role_family", "role_family")
    band_metrics = _classification_metrics(cases, "expected_band", "band")
    dna_metrics = _classification_metrics(cases, "expected_dna", "dna")
    score_metrics = _score_metrics(cases)
    bert_coverage = _bert_coverage_rates(cases)

    summary = {
        "total_cases": len(cases),
        "llm_score_success_rate": _avg([1.0 if case["predicted"]["llm_used"] else 0.0 for case in cases]),
        "llm_skill_success_rate": _avg([1.0 if case["predicted"]["llm_skill_used"] else 0.0 for case in cases]),
        "score_consistency_rate": _avg([1.0 if case["score_consistent"] else 0.0 for case in cases]),
        "average_total_score": _avg([float(case["predicted"]["total_score"]) for case in cases]),
        "role_prediction_rate": _avg([1.0 if case["predicted"].get("role_family") not in (None, "", "UNKNOWN") else 0.0 for case in cases]),
        "band_prediction_rate": _avg([1.0 if case["predicted"].get("band") not in (None, "", "UNKNOWN") else 0.0 for case in cases]),
        "dna_prediction_rate": _avg([1.0 if case["predicted"].get("dna") not in (None, "", "UNKNOWN") else 0.0 for case in cases]),
        "name_extraction_rate": _avg([1.0 if case.get("candidate_name") not in (None, "", "N/A") else 0.0 for case in cases]),
        "contact_extraction_rate": _avg([
            1.0
            if any(
                value not in (None, "", "N/A")
                for value in [
                    ((case.get("analysis_snapshot", {}) or {}).get("candidate_overview", {}) or {}).get("email"),
                    ((case.get("analysis_snapshot", {}) or {}).get("candidate_overview", {}) or {}).get("phone"),
                ]
            )
            else 0.0
            for case in cases
        ]),
        "recruiter_summary_rate": _avg([
            1.0
            if ((case.get("analysis_snapshot", {}) or {}).get("recruiter_summary") not in (None, "", "N/A"))
            else 0.0
            for case in cases
        ]),
        "average_confidence_score": _avg([float(case.get("confidence", {}).get("score") or 0.0) for case in cases if case.get("confidence", {}).get("score") is not None]),
        "average_latency_ms": _avg([float(case.get("latency_ms", 0) or 0) for case in cases]),
        "total_prompt_tokens": sum(int(case.get("prompt_tokens", 0) or 0) for case in cases),
        "total_completion_tokens": sum(int(case.get("completion_tokens", 0) or 0) for case in cases),
        "total_tokens": sum(int(case.get("total_tokens", 0) or 0) for case in cases),
        "average_tokens_per_resume": _avg([float(case.get("total_tokens", 0) or 0) for case in cases]),
        "average_cost_per_resume_usd": _avg([float(case.get("end_to_end_cost_usd", 0) or 0) for case in cases]),
        "total_cost_usd": round(sum(float(case.get("end_to_end_cost_usd", 0) or 0) for case in cases), 6),
        "average_remaining_context_tokens": _avg([float(case.get("remaining_context_tokens", 0) or 0) for case in cases]),
        "cases_with_missing_component_justifications": sum(1 for case in cases if case["missing_component_justifications"]),
        "cases_with_missing_component_rationales": sum(1 for case in cases if case["missing_component_rationales"]),
        "expectation_case_count": len(expectation_cases),
        "expectation_match_rate": _avg([1.0 if case["expectation_result"]["matched"] else 0.0 for case in expectation_cases]) if expectation_cases else None,
        "role_family_match_rate": role_metrics.get("match_rate"),
        "band_match_rate": band_metrics.get("match_rate"),
        "dna_match_rate": dna_metrics.get("match_rate"),
        "score_mae": score_metrics.get("mae"),
        "screening_recommendation_rate": _avg([1.0 if case.get("business_metrics", {}).get("screening_recommended") else 0.0 for case in cases]),
        "telephonic_enable_rate": _avg([1.0 if case.get("business_metrics", {}).get("telephonic_enabled") else 0.0 for case in cases]),
        "average_risk_flag_count": _avg([float(case.get("business_metrics", {}).get("risk_flag_count", 0) or 0) for case in cases]),
        "average_gap_count": _avg([float(case.get("business_metrics", {}).get("gap_count", 0) or 0) for case in cases]),
        # ── Rubric-aware metrics ──
        "rubric_score_present_rate": _avg([
            1.0 if case.get("rubric_scorecard", {}).get("total_score") else 0.0
            for case in cases
        ]),
        "rubric_llm_judged_rate": _avg([
            1.0 if case.get("rubric_scorecard", {}).get("llm_judged") else 0.0
            for case in cases
        ]),
        "rubric_section_sum_consistency": _avg([
            1.0 if abs(
                float(case.get("rubric_scorecard", {}).get("total_score") or 0)
                - float(case.get("rubric_scorecard", {}).get("experience_score") or 0)
                - float(case.get("rubric_scorecard", {}).get("skills_score") or 0)
                - float(case.get("rubric_scorecard", {}).get("education_score") or 0)
            ) <= 1.0 else 0.0
            for case in cases
            if case.get("rubric_scorecard", {}).get("total_score")
        ]) if any(case.get("rubric_scorecard", {}).get("total_score") for case in cases) else None,
        "rubric_exp_score_mae": _avg([
            abs(
                float(case.get("rubric_scorecard", {}).get("experience_score") or 0)
                - float(case.get("expected", {}).get("expected_rubric_exp_score") or
                        case.get("rubric_scorecard", {}).get("experience_score") or 0)
            )
            for case in cases
            if case.get("rubric_scorecard", {}).get("experience_score")
            and case.get("expected", {}).get("expected_rubric_exp_score")
        ]) if any(case.get("expected", {}).get("expected_rubric_exp_score") for case in cases) else None,
        "rubric_avg_total_score": _avg([
            float(case.get("rubric_scorecard", {}).get("total_score") or 0)
            for case in cases
            if case.get("rubric_scorecard", {}).get("total_score")
        ]) if any(case.get("rubric_scorecard", {}).get("total_score") for case in cases) else None,
        "rubric_reject_flag_rate": _avg([
            1.0 if case.get("rubric_scorecard", {}).get("reject_flags") else 0.0
            for case in cases
        ]),
        # BERT coverage rates (for regression gates)
        "skill_depth_bert_coverage": bert_coverage.get("skill_depth_bert_coverage"),
        "role_family_bert_coverage": bert_coverage.get("role_family_bert_coverage"),
        "dna_bert_coverage": bert_coverage.get("dna_bert_coverage"),
        # Rubric parameter consistency: % cases where section sums match total
        "rubric_parameter_consistency_rate": _avg([
            1.0 if abs(
                float(case.get("rubric_scorecard", {}).get("total_score") or 0)
                - float(case.get("rubric_scorecard", {}).get("experience_score") or 0)
                - float(case.get("rubric_scorecard", {}).get("skills_score") or 0)
                - float(case.get("rubric_scorecard", {}).get("education_score") or 0)
            ) <= 1.0 else 0.0
            for case in cases
            if case.get("rubric_scorecard", {}).get("total_score")
        ]) if any(case.get("rubric_scorecard", {}).get("total_score") for case in cases) else None,
    }
    return {
        "summary": summary,
        "quality_metrics": {
            "role_family": role_metrics,
            "band": band_metrics,
            "dna": dna_metrics,
            "score": score_metrics,
        },
    }


def _compare_metric(current: Any, baseline: Any) -> float | None:
    if current is None or baseline is None:
        return None
    return round(float(current) - float(baseline), 3)


def _regression_analysis(current_summary: dict[str, Any], baseline_report: dict[str, Any] | None, gates: dict[str, dict[str, float | None]]) -> dict[str, Any]:
    if not baseline_report:
        return {"baseline_available": False, "metric_deltas": {}, "gate_results": [], "regressions": []}

    baseline_summary = baseline_report.get("summary", {})
    metric_deltas = {
        key: {
            "current": current_summary.get(key),
            "baseline": baseline_summary.get(key),
            "delta": _compare_metric(current_summary.get(key), baseline_summary.get(key)),
        }
        for key in sorted(set(current_summary) | set(baseline_summary))
        if isinstance(current_summary.get(key), (int, float, type(None))) and isinstance(baseline_summary.get(key), (int, float, type(None)))
    }

    gate_results = []
    regressions = []
    for metric_name, gate in gates.items():
        current = current_summary.get(metric_name)
        baseline = baseline_summary.get(metric_name)
        status = "pass"
        reasons = []
        min_value = gate.get("min")
        max_value = gate.get("max")
        delta_min = gate.get("delta_min")
        delta_max = gate.get("delta_max")

        if min_value is not None and current is not None and current < min_value:
            status = "fail"
            reasons.append(f"below_min:{min_value}")
        if max_value is not None and current is not None and current > max_value:
            status = "fail"
            reasons.append(f"above_max:{max_value}")
        if delta_min is not None and current is not None and baseline is not None and (float(current) - float(baseline)) < delta_min:
            status = "fail"
            reasons.append(f"delta_below:{delta_min}")
        if delta_max is not None and current is not None and baseline is not None and (float(current) - float(baseline)) > delta_max:
            status = "fail"
            reasons.append(f"delta_above:{delta_max}")

        row = {
            "metric": metric_name,
            "current": current,
            "baseline": baseline,
            "status": status,
            "reasons": reasons,
        }
        gate_results.append(row)
        if status == "fail":
            regressions.append(row)

    return {
        "baseline_available": True,
        "metric_deltas": metric_deltas,
        "gate_results": gate_results,
        "regressions": regressions,
    }


def evaluate_paths(
    input_path: Path,
    expectations_path: Path | None = None,
    baseline_report_path: Path | None = None,
    dataset_name: str | None = None,
    run_label: str | None = None,
) -> dict[str, Any]:
    files = _collect_inputs(input_path)
    raw_expectations = _load_json(expectations_path) if expectations_path else {}
    expectations, dataset_meta = _normalize_expectations(raw_expectations, files)
    baseline_report = _load_json(baseline_report_path) if baseline_report_path else None
    calibration_points = _load_confidence_calibration_points()

    cases = []
    bulk_started_at = datetime.now(UTC).isoformat()
    bulk_t0 = time.perf_counter()
    for path in files:
        payload = _load_json(path)
        case_id = _extract_case_id(path, payload)
        expectation = _lookup_expectation(path, case_id, expectations)
        case_started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        analysis = analyze_resume(ResumeInput.from_any(payload))
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        case_completed_at = datetime.now(UTC).isoformat()
        analysis.setdefault("llm_telemetry", {})
        analysis["llm_telemetry"]["wall_clock_latency_ms"] = elapsed_ms
        case = _make_case_record(path, analysis, expectation, case_id, calibration_points)
        case["started_at"] = case_started_at
        case["completed_at"] = case_completed_at
        case["elapsed_ms"] = elapsed_ms
        cases.append(case)

    metrics_payload = _summary_metrics(cases)
    regression = _regression_analysis(metrics_payload["summary"], baseline_report, DEFAULT_GATES)
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{_safe_slug(run_label or dataset_name or input_path.stem)}-{uuid4().hex[:8]}"

    # Bulk timing summary
    bulk_total_ms = round((time.perf_counter() - bulk_t0) * 1000, 2)
    elapsed_list = [c.get("elapsed_ms") or 0 for c in cases]
    bulk_analysis_summary = {
        "started_at": bulk_started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "total_wall_clock_ms": bulk_total_ms,
        "total_wall_clock_s": round(bulk_total_ms / 1000, 2),
        "resume_count": len(cases),
        "throughput_per_min": round(len(cases) / max(bulk_total_ms / 60000, 0.001), 2),
        "per_resume_ms": {
            "min": round(min(elapsed_list), 1) if elapsed_list else 0,
            "max": round(max(elapsed_list), 1) if elapsed_list else 0,
            "avg": round(sum(elapsed_list) / max(len(elapsed_list), 1), 1),
            "p50": round(sorted(elapsed_list)[len(elapsed_list) // 2], 1) if elapsed_list else 0,
        },
        "per_resume_timing": [
            {"case_id": c.get("case_id", ""), "file": c.get("file", ""),
             "started_at": c.get("started_at", ""), "completed_at": c.get("completed_at", ""),
             "elapsed_ms": c.get("elapsed_ms", 0)}
            for c in cases
        ],
    }

    return {
        "run": {
            "run_id": run_id,
            "run_label": run_label or "",
            "dataset_name": dataset_name or dataset_meta.get("dataset_name") or input_path.stem,
            "input_path": str(input_path),
            "generated_at": _now_utc_iso(),
            "expectations_path": str(expectations_path) if expectations_path else "",
            "baseline_report_path": str(baseline_report_path) if baseline_report_path else "",
        },
        "dataset": {
            "case_count": len(files),
            "expectation_case_count": sum(1 for case in cases if case.get("expectation_result")),
            "metadata": dataset_meta,
        },
        "summary": metrics_payload["summary"],
        "quality_metrics": metrics_payload["quality_metrics"],
        "rubric_parameter_stats": _rubric_parameter_stats(cases),
        "bert_confusion_matrices": _bert_confusion_matrices(cases),
        "slices": _slice_metrics(cases),
        "regression": regression,
        "top_failures": _top_failures(cases),
        "bulk_analysis_summary": bulk_analysis_summary,
        "cases": cases,
    }


def save_report(report: dict[str, Any], output_path: Path | None) -> Path:
    if output_path is None:
        run = report.get("run", {})
        run_id = run.get("run_id", "eval-run")
        output_path = Path("eval_runs") / run_id / "report.json"
    _write_json(output_path, report)
    return output_path


def build_eval_report_from_analyses(
    analyses: list[dict[str, Any]],
    payloads: list[dict[str, Any]] | None = None,
    file_labels: list[str] | None = None,
    dataset_name: str | None = None,
    run_label: str | None = None,
    baseline_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cases = []
    payloads = payloads or [{} for _ in analyses]
    file_labels = file_labels or [f"case_{idx + 1}.json" for idx in range(len(analyses))]
    calibration_points = _load_confidence_calibration_points()
    for idx, analysis in enumerate(analyses):
        payload = payloads[idx] if idx < len(payloads) else {}
        file_label = file_labels[idx] if idx < len(file_labels) else f"case_{idx + 1}.json"
        case_id = _extract_case_id_from_payload(payload, Path(file_label).stem)
        analysis.setdefault("llm_telemetry", {})
        cases.append(_make_case_record(Path(file_label), analysis, None, case_id, calibration_points))

    metrics_payload = _summary_metrics(cases)
    regression = _regression_analysis(metrics_payload["summary"], baseline_report, DEFAULT_GATES)
    dataset_slug = dataset_name or "live-analysis"
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{_safe_slug(run_label or dataset_slug)}-{uuid4().hex[:8]}"
    return {
        "run": {
            "run_id": run_id,
            "run_label": run_label or "",
            "dataset_name": dataset_slug,
            "input_path": "",
            "generated_at": _now_utc_iso(),
            "expectations_path": "",
            "baseline_report_path": "",
        },
        "dataset": {
            "case_count": len(cases),
            "expectation_case_count": 0,
            "metadata": {"source": "live_analysis"},
        },
        "summary": metrics_payload["summary"],
        "quality_metrics": metrics_payload["quality_metrics"],
        "rubric_parameter_stats": _rubric_parameter_stats(cases),
        "bert_confusion_matrices": _bert_confusion_matrices(cases),
        "slices": _slice_metrics(cases),
        "regression": regression,
        "top_failures": _top_failures(cases),
        "cases": cases,
    }


def save_live_analysis_report(
    analysis: dict[str, Any],
    payload: dict[str, Any] | None = None,
    file_label: str | None = None,
    runs_dir: Path | None = None,
) -> Path:
    report = build_eval_report_from_analyses(
        analyses=[analysis],
        payloads=[payload or {}],
        file_labels=[file_label or "live_analysis.json"],
        dataset_name="live-analysis",
        run_label=file_label or "live-analysis",
    )
    output_path = (runs_dir or Path("eval_runs")) / report["run"]["run_id"] / "report.json"
    return save_report(report, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a local evaluation experiment for the resume analysis pipeline with metrics, slices, failures, and baseline regression checks."
    )
    parser.add_argument("input_path", help="Path to one resume JSON file or a directory containing resume JSON files.")
    parser.add_argument("--expectations", help="Optional expectation manifest with labels and score targets.", default=None)
    parser.add_argument("--baseline-report", help="Optional prior eval report JSON for regression comparison.", default=None)
    parser.add_argument("--dataset-name", help="Optional human-friendly dataset name.", default=None)
    parser.add_argument("--run-label", help="Optional run label, for example prompt-v2 or bert-refresh.", default=None)
    parser.add_argument("--output", help="Optional output path. Defaults to eval_runs/<run_id>/report.json", default=None)
    args = parser.parse_args()

    report = evaluate_paths(
        input_path=Path(args.input_path),
        expectations_path=Path(args.expectations) if args.expectations else None,
        baseline_report_path=Path(args.baseline_report) if args.baseline_report else None,
        dataset_name=args.dataset_name,
        run_label=args.run_label,
    )
    output_path = save_report(report, Path(args.output) if args.output else None)
    print(json.dumps(report["summary"], indent=2))
    print(json.dumps({"regressions": report["regression"]["regressions"]}, indent=2))
    print(f"\nWrote detailed report to {output_path.resolve()}")


if __name__ == "__main__":
    main()
