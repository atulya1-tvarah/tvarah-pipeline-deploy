from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from candidate_score_store import CANDIDATE_SCORES_DIR

PIPELINE_FILE = Path(__file__).resolve().parent / "recruiter_pipeline.json"

VALID_STAGES = ["Applied", "Shortlisted", "Telephonic", "Panel", "Hired", "Rejected"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_pipeline() -> dict[str, Any]:
    if not PIPELINE_FILE.exists():
        return {}
    try:
        return json.loads(PIPELINE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pipeline(data: dict[str, Any]) -> None:
    PIPELINE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def add_to_pipeline(candidate_id: str, jd_id: str | None = None) -> dict[str, Any]:
    """Add a candidate to the pipeline at 'Applied' stage. Idempotent if already present."""
    pipeline = _load_pipeline()
    if candidate_id in pipeline:
        return pipeline[candidate_id]
    entry: dict[str, Any] = {
        "stage": "Applied",
        "jd_id": jd_id,
        "added_at": _now_iso(),
        "updated_at": _now_iso(),
        "notes": "",
        "stage_history": [{"stage": "Applied", "timestamp": _now_iso()}],
    }
    pipeline[candidate_id] = entry
    _save_pipeline(pipeline)
    return entry


def move_stage(candidate_id: str, new_stage: str, notes: str | None = None) -> dict[str, Any]:
    """Move candidate to a new pipeline stage. Raises ValueError for unknown stage."""
    if new_stage not in VALID_STAGES:
        raise ValueError(f"Invalid stage '{new_stage}'. Must be one of {VALID_STAGES}")
    pipeline = _load_pipeline()
    if candidate_id not in pipeline:
        # Auto-add first
        add_to_pipeline(candidate_id)
        pipeline = _load_pipeline()
    entry = pipeline[candidate_id]
    entry["stage"] = new_stage
    entry["updated_at"] = _now_iso()
    if notes is not None:
        entry["notes"] = notes
    entry.setdefault("stage_history", []).append({"stage": new_stage, "timestamp": _now_iso()})
    pipeline[candidate_id] = entry
    _save_pipeline(pipeline)
    return entry


def set_notes(candidate_id: str, notes: str) -> dict[str, Any]:
    pipeline = _load_pipeline()
    if candidate_id not in pipeline:
        add_to_pipeline(candidate_id)
        pipeline = _load_pipeline()
    pipeline[candidate_id]["notes"] = notes
    pipeline[candidate_id]["updated_at"] = _now_iso()
    _save_pipeline(pipeline)
    return pipeline[candidate_id]


def get_pipeline_entry(candidate_id: str) -> dict[str, Any] | None:
    return _load_pipeline().get(candidate_id)


def _load_candidate_meta(candidate_id: str) -> dict[str, Any]:
    path = CANDIDATE_SCORES_DIR / f"{candidate_id}.json"
    if not path.exists():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        stages = record.get("stages") or []
        stage_names = {s.get("stage", "") for s in stages}
        # Latest stage_scores block for score-100 values
        latest_ss = {}
        for s in reversed(stages):
            latest_ss = s.get("stage_scores") or {}
            if latest_ss:
                break
        return {
            "candidate_name": record.get("candidate_name", ""),
            "current_total": record.get("current_total", 0),
            "resume_score_100": latest_ss.get("resume_score_100") or (
                stages[0].get("total_score", 0) if stages else 0
            ),
            "recruiter_score_100": latest_ss.get("recruiter_score_100"),
            "panel_score_100": latest_ss.get("panel_score_100"),
            "has_recruiter_score": "recruiter" in stage_names,
            "has_panel_score": "panel" in stage_names,
            "current_stage_score": record.get("current_stage", ""),
        }
    except Exception:
        return {}


def list_pipeline(stage: str | None = None, jd_id: str | None = None) -> list[dict[str, Any]]:
    """Return enriched pipeline entries joined with candidate_scores/ data."""
    pipeline = _load_pipeline()
    results = []
    for candidate_id, entry in pipeline.items():
        if stage and entry.get("stage") != stage:
            continue
        if jd_id and entry.get("jd_id") != jd_id:
            continue
        row = {"candidate_id": candidate_id, **entry}
        meta = _load_candidate_meta(candidate_id)
        row.update(meta)
        results.append(row)
    results.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return results
