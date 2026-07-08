"""Bulk resume analysis pipeline.

Processes multiple resume JSON files concurrently using ThreadPoolExecutor.
Supports optional JD matching after analysis.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

logger = logging.getLogger("resume_intelligence.bulk")

BULK_JOBS_DIR = Path(__file__).resolve().parent / "bulk_jobs"
_BULK_MAX_WORKERS = int(os.getenv("BULK_MAX_WORKERS", "4"))
_ENABLE_BULK_FAST = os.getenv("ENABLE_BULK_MODE_FAST", "true").lower() == "true"

# In-memory job registry (survives within a server process; JSON files for restarts)
_BULK_JOBS: dict[str, dict] = {}


class BulkJobRow(TypedDict):
    candidate_id: str
    name: str
    filename: str
    status: str
    resume_score: Any
    jd_match_score: Any
    combined_score: Any
    band: str
    role_family: str
    dna: str
    yoe: Any
    error: str
    # Timing fields (Phase 4)
    started_at: str       # ISO UTC timestamp when processing started
    completed_at: str     # ISO UTC timestamp when processing finished
    elapsed_ms: Any       # wall-clock ms for this resume
    already_existed: bool # True if analysis was loaded from store, not re-run


class BulkJob(TypedDict):
    job_id: str
    status: Literal["running", "done", "partial"]
    total: int
    completed: int
    failed: int
    results: list[BulkJobRow]
    created_at: str
    finished_at: str      # ISO UTC timestamp when the whole job finished


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_job(job: dict) -> None:
    BULK_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = BULK_JOBS_DIR / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_job_from_disk(job_id: str) -> dict | None:
    path = BULK_JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-resume processing
# ---------------------------------------------------------------------------

def _process_single(payload: dict, filename: str, jd_id: str | None) -> BulkJobRow:
    """Analyse one resume payload + optional JD match. Returns a summary row.

    Phase 4: Checks if candidate already exists in the store — if yes, loads the
    existing analysis instead of re-running the full pipeline. Always runs JD match
    if jd_id is provided and a match hasn't been done yet (or re-runs it).
    """
    import time as _time
    from candidate_analysis_store import load_candidate_analysis, save_candidate_analysis
    from candidate_score_store import save_candidate_score

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = _time.perf_counter()

    row: BulkJobRow = {
        "candidate_id": "",
        "name": "",
        "filename": filename,
        "status": "error",
        "resume_score": None,
        "jd_match_score": None,
        "combined_score": None,
        "band": "",
        "role_family": "",
        "dna": "",
        "yoe": None,
        "error": "",
        "started_at": started_at,
        "completed_at": "",
        "elapsed_ms": None,
        "already_existed": False,
    }

    # --- Step 1: Parse the payload to determine candidate_id early ---
    try:
        from models import ResumeInput
        resume_input = ResumeInput.from_any(payload)
    except Exception as exc:
        row["error"] = f"Invalid payload: {exc}"
        row["completed_at"] = datetime.now(timezone.utc).isoformat()
        row["elapsed_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
        return row

    # Pre-derive candidate_id from payload to check existence before running analysis
    _INVALID = {"n/a", "na", "none", "null", "unknown", ""}
    def _cval(v): return v if v and str(v).strip().lower() not in _INVALID and "/" not in str(v) else None

    def _candidate_id_from_payload(p: dict) -> str | None:
        """Try to derive candidate_id from raw payload fields before full analysis."""
        rd = p.get("resume_data") or p
        bi = rd.get("basic_info") or {}
        ci = bi.get("contact_info") or {}
        email = _cval(ci.get("email") or bi.get("email") or p.get("email") or "")
        name = _cval(bi.get("name") or p.get("name") or "")
        return (email or name or "").replace(" ", "_") or None

    pre_cid = _candidate_id_from_payload(payload)

    # --- Step 2: Check if candidate already exists in the analysis store ---
    result = None
    already_existed = False
    if pre_cid:
        existing = load_candidate_analysis(pre_cid)
        if existing:
            result = existing
            already_existed = True
            logger.info("Bulk: candidate=%s already in store — skipping re-analysis", pre_cid)

    # --- Step 3: Run full analysis if not already in store ---
    if result is None:
        try:
            from engine import analyze_resume
            result = analyze_resume(resume_input)
        except Exception as exc:
            row["error"] = f"Analysis failed: {exc}"
            row["completed_at"] = datetime.now(timezone.utc).isoformat()
            row["elapsed_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
            return row

    # Derive final candidate_id from analysis result
    overview = result.get("candidate_overview") or {}
    candidate_id = (
        _cval(overview.get("email")) or
        _cval(overview.get("name")) or
        _cval(filename.replace(".json", "")) or
        f"bulk_{uuid.uuid4().hex[:8]}"
    ).replace(" ", "_")

    if not already_existed:
        result["candidate_id"] = candidate_id
        result["_raw_resume"] = payload

        # Skip expensive LLM recruiter summary in bulk mode
        if not _ENABLE_BULK_FAST:
            try:
                from llm_recruiter_analysis import generate_recruiter_analysis
                result["recruiter_summary"] = generate_recruiter_analysis(result)
            except Exception:
                pass

        # Persist analysis
        try:
            save_candidate_analysis(candidate_id, result)
        except Exception as exc:
            logger.warning("Failed to save bulk analysis candidate=%s: %s", candidate_id, exc)

        # Save eval framework snapshot (same as single-resume UI flow)
        try:
            from eval_framework import save_live_analysis_report
            save_live_analysis_report(
                analysis=result,
                payload=payload,
                file_label=filename,
                runs_dir=Path(__file__).resolve().parent / "eval_runs",
            )
        except Exception as exc:
            logger.warning("Failed to save bulk eval snapshot candidate=%s: %s", candidate_id, exc)

        # Persist rubric score at resume stage
        rubric_scorecard = result.get("rubric_scorecard")
        if rubric_scorecard:
            try:
                candidate_name = overview.get("name", "")
                save_candidate_score(candidate_id, rubric_scorecard, stage="resume", candidate_name=candidate_name)
            except Exception as exc:
                logger.warning("Failed to save bulk score candidate=%s: %s", candidate_id, exc)

    # --- Step 4: Extract resume_score from whatever is available ---
    rubric_scorecard = result.get("rubric_scorecard") or {}
    stage_scores = rubric_scorecard.get("stage_scores") or {}
    resume_score = stage_scores.get("resume_score_100") or rubric_scorecard.get("total_score")

    # --- Step 5: Optional JD matching (always run if jd_id provided) ---
    jd_match_score = None
    combined_score = None
    if jd_id:
        try:
            from jd_matching_bridge import match_candidate_to_jd
            match = match_candidate_to_jd(candidate_id, jd_id)
            jd_match_score = match.get("overall_score")
            combined_score = match.get("combined_score")
        except Exception as exc:
            logger.warning("JD match failed bulk candidate=%s jd=%s: %s", candidate_id, jd_id, exc)

    elapsed_ms = round((_time.perf_counter() - t0) * 1000, 1)
    completed_at = datetime.now(timezone.utc).isoformat()

    row["candidate_id"] = candidate_id
    row["name"] = overview.get("name") or candidate_id
    row["status"] = "ok"
    row["resume_score"] = resume_score
    row["jd_match_score"] = jd_match_score
    row["combined_score"] = combined_score
    row["band"] = result.get("overall_band") or rubric_scorecard.get("overall_band") or ""
    row["role_family"] = result.get("role_family") or ""
    row["dna"] = result.get("dna_fit") or ""
    row["yoe"] = (result.get("experience_analysis") or {}).get("total_years") or (result.get("experience_analysis") or {}).get("total_experience_years")
    row["started_at"] = started_at
    row["completed_at"] = completed_at
    row["elapsed_ms"] = elapsed_ms
    row["already_existed"] = already_existed
    return row


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------

def _run_bulk_job(job_id: str, file_payloads: list[dict], jd_id: str | None) -> None:
    """Background thread: process all payloads and update job state."""
    job = _BULK_JOBS[job_id]
    job["status"] = "running"

    # Pre-populate results with "running" placeholder rows so UI shows activity immediately
    filename_to_idx: dict[str, int] = {}
    for i, fp in enumerate(file_payloads):
        placeholder: BulkJobRow = {
            "candidate_id": "", "name": fp["filename"], "filename": fp["filename"],
            "status": "running", "resume_score": None, "jd_match_score": None,
            "combined_score": None, "band": "", "role_family": "", "dna": "",
            "yoe": None, "error": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": "", "elapsed_ms": None, "already_existed": False,
        }
        job["results"].append(placeholder)
        filename_to_idx[fp["filename"]] = i
    _save_job(job)

    futures = {}
    with ThreadPoolExecutor(max_workers=_BULK_MAX_WORKERS) as executor:
        for fp in file_payloads:
            fut = executor.submit(_process_single, fp["payload"], fp["filename"], jd_id)
            futures[fut] = fp["filename"]

        for fut in as_completed(futures):
            filename = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:
                row = BulkJobRow(
                    candidate_id="", name="", filename=filename,
                    status="error", resume_score=None, jd_match_score=None,
                    combined_score=None, band="", role_family="", dna="",
                    yoe=None, error=str(exc),
                )
            # Replace the placeholder row in-place so order is stable
            idx = filename_to_idx.get(filename)
            if idx is not None:
                job["results"][idx] = row
            else:
                job["results"].append(row)
            if row["status"] == "ok":
                job["completed"] += 1
            else:
                job["failed"] += 1
            _save_job(job)

    job["status"] = "partial" if job["failed"] > 0 else "done"
    job["finished_at"] = datetime.now(timezone.utc).isoformat()
    _save_job(job)
    logger.info("Bulk job %s done total=%s completed=%s failed=%s",
                job_id, job["total"], job["completed"], job["failed"])


def create_bulk_job(file_payloads: list[dict], jd_id: str | None = None) -> str:
    """Launch background thread, return job_id.

    Each item in file_payloads: {"filename": str, "payload": dict}
    """
    import threading
    job_id = uuid.uuid4().hex
    job: BulkJob = {
        "job_id": job_id,
        "status": "running",
        "total": len(file_payloads),
        "completed": 0,
        "failed": 0,
        "results": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": "",
        "jd_id": jd_id,
    }
    _BULK_JOBS[job_id] = job
    _save_job(job)

    thread = threading.Thread(
        target=_run_bulk_job,
        args=(job_id, file_payloads, jd_id),
        daemon=True,
    )
    thread.start()
    return job_id


def get_bulk_job(job_id: str) -> dict | None:
    if job_id in _BULK_JOBS:
        return _BULK_JOBS[job_id]
    # Try disk (e.g. after restart)
    job = _load_job_from_disk(job_id)
    if job:
        _BULK_JOBS[job_id] = job
    return job


def list_bulk_jobs() -> list[dict]:
    """Return summary rows for all known bulk jobs."""
    # Merge in-memory + disk
    all_ids = set(_BULK_JOBS.keys())
    BULK_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for path in BULK_JOBS_DIR.glob("*.json"):
        all_ids.add(path.stem)

    summaries = []
    for job_id in all_ids:
        job = get_bulk_job(job_id)
        if not job:
            continue
        summaries.append({
            "job_id": job["job_id"],
            "status": job["status"],
            "total": job["total"],
            "completed": job["completed"],
            "failed": job.get("failed", 0),
            "created_at": job["created_at"],
            "jd_id": job.get("jd_id"),
        })
    summaries.sort(key=lambda j: j["created_at"], reverse=True)
    return summaries
