"""Dual JSON + SQLite storage for sourcing jobs and sourced candidate profiles."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE_DIR = Path(__file__).resolve().parent.parent
SOURCING_JOBS_DIR = _BASE_DIR / "sourcing_jobs"
SOURCED_PROFILES_DIR = _BASE_DIR / "sourced_profiles"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


# ---------------------------------------------------------------------------
# Sourcing Jobs
# ---------------------------------------------------------------------------

def create_sourcing_job(query_text: str, parsed_criteria: dict, jd_id: str | None = None) -> str:
    """Create a new sourcing job, persist to JSON + SQLite. Returns job_id."""
    SOURCING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(query_text) or "search"
    job_id = f"src-{slug}-{_ts()}"
    record: dict[str, Any] = {
        "job_id": job_id,
        "query_text": query_text,
        "criteria": parsed_criteria,
        "jd_id": jd_id,
        "status": "pending",
        "results_count": 0,
        "created_at": _now_iso(),
        "candidates": [],
    }
    path = SOURCING_JOBS_DIR / f"{job_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import upsert_sourcing_job as _db
        _db(record)
    except Exception:
        pass
    return job_id


def save_sourcing_results(job_id: str, results: list[dict]) -> None:
    """Attach candidate results to an existing sourcing job."""
    path = SOURCING_JOBS_DIR / f"{job_id}.json"
    record: dict[str, Any] = {}
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
    record["candidates"] = results
    record["results_count"] = len(results)
    record["status"] = "done"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import upsert_sourcing_job as _db
        _db(record)
    except Exception:
        pass


def load_sourcing_job(job_id: str) -> dict | None:
    path = SOURCING_JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_sourcing_jobs() -> list[dict]:
    if not SOURCING_JOBS_DIR.exists():
        return []
    results = []
    for path in SOURCING_JOBS_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            # Return lightweight summary (no full candidates list)
            results.append({
                "job_id": record.get("job_id", path.stem),
                "query_text": record.get("query_text", ""),
                "status": record.get("status", ""),
                "results_count": record.get("results_count", 0),
                "jd_id": record.get("jd_id"),
                "created_at": record.get("created_at", ""),
            })
        except Exception:
            continue
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


# ---------------------------------------------------------------------------
# Sourced Profiles
# ---------------------------------------------------------------------------

def save_sourced_profile(profile: dict) -> None:
    """Persist a normalized sourced candidate profile."""
    SOURCED_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    username = profile.get("github_username", "")
    if not username:
        return
    path = SOURCED_PROFILES_DIR / f"{username}.json"
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import upsert_sourced_candidate as _db
        _db(profile)
    except Exception:
        pass


def load_sourced_profile(github_username: str) -> dict | None:
    path = SOURCED_PROFILES_DIR / f"{github_username}.json"
    if not path.exists():
        # Try DB
        try:
            from database import get_sourced_candidate as _db
            return _db(github_username)
        except Exception:
            pass
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def update_pipeline_status(github_username: str, status: str) -> None:
    """Update pipeline_status for a cached sourced profile."""
    path = SOURCED_PROFILES_DIR / f"{github_username}.json"
    if path.exists():
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["pipeline_status"] = status
        path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import update_sourced_pipeline_status as _db
        _db(github_username, status)
    except Exception:
        pass
