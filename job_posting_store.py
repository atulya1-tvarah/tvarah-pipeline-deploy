from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_POSTINGS_DIR = Path(__file__).resolve().parent / "job_postings"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def save_job_posting(jd: dict[str, Any]) -> str:
    """Save a job posting. Returns jd_id."""
    JOB_POSTINGS_DIR.mkdir(parents=True, exist_ok=True)
    title = jd.get("title") or "untitled"
    slug = _slugify(title)
    # Make unique by appending timestamp millis
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17]
    jd_id = f"{slug}-{ts}"
    record = {
        "jd_id": jd_id,
        "title": title,
        "company": jd.get("company") or jd.get("company_name", ""),
        "status": jd.get("status", "open"),
        "created_at": jd.get("created_at") or _now_iso(),
        "yoe_min": jd.get("yoe_min"),
        "yoe_max": jd.get("yoe_max"),
        "preferred_dna": jd.get("preferred_dna"),
        "role_family": jd.get("role_family"),
        "mandatory_skills": jd.get("mandatory_skills", []),
        "nice_to_have_skills": jd.get("nice_to_have_skills", []),
        "description": jd.get("description", ""),
    }
    path = JOB_POSTINGS_DIR / f"{jd_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from database import upsert_job_posting as _db_upsert
        _db_upsert(record)
    except Exception:
        pass
    return jd_id


def load_job_posting(jd_id: str) -> dict[str, Any] | None:
    path = JOB_POSTINGS_DIR / f"{jd_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_job_postings(include_closed: bool = False) -> list[dict[str, Any]]:
    if not JOB_POSTINGS_DIR.exists():
        return []
    results = []
    for path in JOB_POSTINGS_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not include_closed and record.get("status") == "closed":
            continue
        results.append(record)
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results


def close_job_posting(jd_id: str) -> bool:
    """Soft-delete: set status = 'closed'. Returns True if found."""
    record = load_job_posting(jd_id)
    if record is None:
        return False
    record["status"] = "closed"
    path = JOB_POSTINGS_DIR / f"{jd_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


if __name__ == "__main__":
    # Smoke test
    jd_id = save_job_posting({"title": "ML Engineer", "mandatory_skills": []})
    print("Created:", jd_id)
    print("List:", [j["jd_id"] for j in list_job_postings()])
    print("Load:", load_job_posting(jd_id))
    close_job_posting(jd_id)
    print("After close (include_closed=False):", [j["jd_id"] for j in list_job_postings()])
    print("After close (include_closed=True):", [j["jd_id"] for j in list_job_postings(include_closed=True)])
