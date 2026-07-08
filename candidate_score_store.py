from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_SCORES_DIR = Path(__file__).resolve().parent / "candidate_scores"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_candidate_score(
    candidate_id: str,
    rubric_result: dict[str, Any],
    stage: str,
    candidate_name: str = "",
) -> Path:
    """Append a stage result to a candidate's score history JSON file.

    Creates the file on first call (resume stage).
    Appends to existing stages list on subsequent calls.
    """
    CANDIDATE_SCORES_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATE_SCORES_DIR / f"{candidate_id}.json"

    # Build the new stage entry
    stage_entry: dict[str, Any] = {
        "stage": stage,
        "timestamp": _now_iso(),
        "total_score": rubric_result.get("total_score", 0),
        "experience_score": rubric_result.get("experience_score", 0),
        "skills_score": rubric_result.get("skills_score", 0),
        "education_score": rubric_result.get("education_score", 0),
        "breakdown": rubric_result.get("breakdown", {}),
        "reject_flags": rubric_result.get("reject_flags", []),
    }
    # Persist stage_scores block (contains *_score_100 normalised values) if present
    if rubric_result.get("stage_scores"):
        stage_entry["stage_scores"] = rubric_result["stage_scores"]

    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    else:
        existing = {
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "current_stage": stage,
            "current_total": 0,
            "stages": [],
        }

    # Calculate delta vs previous stage
    if existing["stages"]:
        prev_total = existing["stages"][-1].get("total_score", 0)
        stage_entry["delta"] = stage_entry["total_score"] - prev_total
        if rubric_result.get("params_updated"):
            stage_entry["params_updated"] = rubric_result["params_updated"]

    existing["current_stage"] = stage
    existing["current_total"] = stage_entry["total_score"]
    if not existing.get("candidate_name") and candidate_name:
        existing["candidate_name"] = candidate_name

    existing["stages"].append(stage_entry)

    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import update_candidate_stage_score
        update_candidate_stage_score(candidate_id, stage, stage_entry["total_score"])
    except Exception:
        pass
    return path


def load_candidate_score(candidate_id: str) -> dict[str, Any] | None:
    """Load full stage history for a candidate. Returns None if not found."""
    path = CANDIDATE_SCORES_DIR / f"{candidate_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_candidate_scores(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return summary rows for all stored candidates, optionally filtered.

    Supported filter keys:
        min_score (int), max_score (int),
        stage (str), candidate_name_contains (str).
    """
    if not CANDIDATE_SCORES_DIR.exists():
        return []
    filters = filters or {}
    results = []
    for path in CANDIDATE_SCORES_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        total = record.get("current_total", 0)
        if "min_score" in filters and total < filters["min_score"]:
            continue
        if "max_score" in filters and total > filters["max_score"]:
            continue
        if "stage" in filters and record.get("current_stage") != filters["stage"]:
            continue
        if "candidate_name_contains" in filters:
            needle = filters["candidate_name_contains"].lower()
            if needle not in (record.get("candidate_name") or "").lower():
                continue
        results.append({
            "candidate_id": record.get("candidate_id"),
            "candidate_name": record.get("candidate_name"),
            "current_stage": record.get("current_stage"),
            "current_total": total,
        })
    return results
