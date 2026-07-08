from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CANDIDATE_ANALYSES_DIR = Path(__file__).resolve().parent / "candidate_analyses"


def save_candidate_analysis(candidate_id: str, analysis: dict[str, Any]) -> Path:
    """Persist the full analysis dict produced by analyze_resume()."""
    CANDIDATE_ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATE_ANALYSES_DIR / f"{candidate_id}.json"
    path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import upsert_candidate
        upsert_candidate(candidate_id, analysis)
    except Exception:
        pass
    return path


def load_candidate_analysis(candidate_id: str) -> dict[str, Any] | None:
    path = CANDIDATE_ANALYSES_DIR / f"{candidate_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_candidate_analyses() -> list[dict[str, Any]]:
    """Return lightweight summary rows for all stored analyses (via DB when available)."""
    try:
        from database import list_candidates
        rows = list_candidates()
        if rows:
            return [
                {
                    "candidate_id": r["candidate_id"],
                    "name": r["name"] or r["candidate_id"],
                    "role_family": r["role_family"] or "",
                    "band": r["band"] or "",
                    "resume_score_100": r["resume_score"],
                    "dna": r["dna"] or "",
                }
                for r in rows
            ]
    except Exception:
        pass
    # Fallback: scan JSON files
    if not CANDIDATE_ANALYSES_DIR.exists():
        return []
    summaries = []
    for path in CANDIDATE_ANALYSES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        overview = data.get("candidate_overview") or {}
        rubric = data.get("rubric_scorecard") or {}
        stage_scores = rubric.get("stage_scores") or {}
        summaries.append({
            "candidate_id": data.get("candidate_id") or path.stem,
            "name": data.get("candidate_name") or overview.get("name") or path.stem,
            "role_family": data.get("role_family") or "",
            "band": rubric.get("overall_band") or "",
            "resume_score_100": stage_scores.get("resume_score_100") or rubric.get("total_score"),
            "dna": data.get("dna_fit") or "",
        })
    return summaries
