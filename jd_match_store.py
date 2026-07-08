from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JD_MATCHES_DIR = Path(__file__).resolve().parent / "jd_matches"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_jd_match(jd_id: str, candidate_id: str, result: dict[str, Any]) -> Path:
    """Persist a JD match result for a candidate."""
    target_dir = JD_MATCHES_DIR / jd_id
    target_dir.mkdir(parents=True, exist_ok=True)
    result = dict(result)
    result["jd_id"] = jd_id
    result["candidate_id"] = candidate_id
    result["matched_at"] = result.get("matched_at") or _now_iso()
    path = target_dir / f"{candidate_id}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import save_jd_match as _db_save
        _db_save(jd_id, candidate_id, result)
    except Exception:
        pass
    return path


def load_jd_match(jd_id: str, candidate_id: str) -> dict[str, Any] | None:
    path = JD_MATCHES_DIR / jd_id / f"{candidate_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_jd_matches(jd_id: str) -> list[dict[str, Any]]:
    """Return summary rows for all candidates matched against this JD."""
    target_dir = JD_MATCHES_DIR / jd_id
    if not target_dir.exists():
        return []
    results = []
    for path in target_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        results.append({
            "candidate_id": record.get("candidate_id"),
            "candidate_name": record.get("candidate_name", ""),
            "jd_id": jd_id,
            "overall_score": record.get("overall_score"),
            "jd_match_score": record.get("jd_match_score"),
            "combined_score": record.get("combined_score"),
            "rubric_score": record.get("rubric_score"),
            "rubric_stage": record.get("rubric_stage"),
            "recommendation": record.get("recommendation"),
            "matched_at": record.get("matched_at"),
            "skill_match_details": record.get("skill_match_details", {}),
            "fit_reasons": record.get("fit_reasons") or [],
        })
    results.sort(key=lambda r: r.get("combined_score") or r.get("overall_score") or 0, reverse=True)
    return results


def delete_jd_match(jd_id: str, candidate_id: str) -> bool:
    path = JD_MATCHES_DIR / jd_id / f"{candidate_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
