"""Central PostgreSQL database — Resume Intelligence.

Single source of truth for app_candidates, job postings, and JD matches.
On first call to init_db() all existing JSON files are migrated in automatically.
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("resume_intelligence.db")

_BASE_DIR = Path(__file__).resolve().parent
_lock = Lock()

# ---------------------------------------------------------------------------
# PostgreSQL connection config (reads from env / .env)
# ---------------------------------------------------------------------------

_PG: dict[str, Any] = {
    "host":     os.environ.get("DB_HOST", "10.0.0.6"),
    "port":     int(os.environ.get("DB_PORT", "5432")),
    "dbname":   os.environ.get("DB_NAME", "tvarah"),
    "user":     os.environ.get("DB_USER", "tvarah"),
    "password": os.environ.get("DB_PASSWORD", "4pVhZ873Rm6C"),
}


# ---------------------------------------------------------------------------
# Cursor wrapper — makes execute() chainable; rows returned as plain dicts
# ---------------------------------------------------------------------------

class _CursorWrapper:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params=None):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql: str, params_list):
        self._cur.executemany(sql, params_list)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self) -> list[dict]:
        return [dict(r) for r in (self._cur.fetchall() or [])]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


@contextmanager
def _conn():
    """Open a connection, yield a cursor wrapper; commit on success, rollback on error."""
    conn = psycopg2.connect(**_PG, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = _CursorWrapper(conn.cursor())
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS app_candidates (
        candidate_id    TEXT PRIMARY KEY,
        name            TEXT,
        email           TEXT,
        role_family     TEXT,
        band            TEXT,
        resume_score    REAL,
        recruiter_score REAL,
        panel_score     REAL,
        yoe             REAL,
        dna             TEXT,
        analysis_json   TEXT,
        created_at      TEXT,
        updated_at      TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_app_candidates_updated ON app_candidates(updated_at DESC)",

    """CREATE TABLE IF NOT EXISTS job_postings (
        jd_id            TEXT PRIMARY KEY,
        title            TEXT,
        company          TEXT DEFAULT '',
        role_family      TEXT,
        yoe_min          INTEGER,
        yoe_max          INTEGER,
        mandatory_skills TEXT,
        nice_to_have     TEXT,
        description      TEXT,
        dna_fit          TEXT,
        status           TEXT DEFAULT 'open',
        created_at       TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS app_jd_matches (
        jd_id           TEXT NOT NULL,
        candidate_id    TEXT NOT NULL,
        rubric_score    REAL,
        jd_match_score  REAL,
        combined_score  REAL,
        recommendation  TEXT,
        rubric_stage    TEXT,
        candidate_name  TEXT,
        match_json      TEXT,
        created_at      TEXT,
        PRIMARY KEY (jd_id, candidate_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_app_matches_jd ON app_jd_matches(jd_id, combined_score DESC)",

    """CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS sourcing_jobs (
        job_id        TEXT PRIMARY KEY,
        query_text    TEXT,
        criteria_json TEXT,
        jd_id         TEXT,
        status        TEXT DEFAULT 'pending',
        results_count INTEGER DEFAULT 0,
        created_at    TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS sourced_candidates (
        github_username TEXT PRIMARY KEY,
        display_name    TEXT,
        location        TEXT,
        company         TEXT,
        tech_stack_json TEXT,
        sourcing_score  REAL,
        yoe_proxy       REAL,
        github_url      TEXT,
        email           TEXT,
        pipeline_status TEXT DEFAULT 'sourced',
        sourcing_job_id TEXT,
        created_at      TEXT,
        updated_at      TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sourced_score ON sourced_candidates(sourcing_score DESC)",

    """CREATE TABLE IF NOT EXISTS candidate_outcomes (
        candidate_id    TEXT PRIMARY KEY,
        outcome         TEXT DEFAULT 'IN_PROGRESS',
        rejection_stage TEXT,
        placed_company  TEXT,
        placed_role     TEXT,
        placed_date     TEXT,
        feedback_notes  TEXT,
        recorded_by     TEXT,
        created_at      TEXT,
        updated_at      TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_updated ON candidate_outcomes(updated_at DESC)",

    """CREATE TABLE IF NOT EXISTS app_users (
        user_id       TEXT PRIMARY KEY,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name     TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'recruiter',
        is_active     INTEGER DEFAULT 1,
        created_at    TEXT,
        updated_at    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users(email)",

    """CREATE TABLE IF NOT EXISTS user_sessions (
        session_id TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)",

    """CREATE TABLE IF NOT EXISTS standup_updates (
        update_id  TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        jd_id      TEXT,
        date       TEXT NOT NULL,
        today      TEXT,
        blockers   TEXT,
        priorities TEXT,
        created_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_standup_user_date ON standup_updates(user_id, date DESC)",
]

_ALTER_COLUMNS: dict[str, list[str]] = {
    "job_postings": [
        "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS company TEXT DEFAULT ''",
        "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS assigned_recruiter TEXT",
        "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS deadline TEXT",
        "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS jd_status TEXT DEFAULT 'active'",
        "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS assigned_sales_agent TEXT",
    ],
    "app_candidates": [
        "ALTER TABLE app_candidates ADD COLUMN IF NOT EXISTS assigned_recruiter TEXT",
        "ALTER TABLE app_candidates ADD COLUMN IF NOT EXISTS interview_date TEXT",
        "ALTER TABLE app_candidates ADD COLUMN IF NOT EXISTS interview_time TEXT",
        "ALTER TABLE app_candidates ADD COLUMN IF NOT EXISTS interview_round TEXT",
    ],
}


def init_db() -> None:
    """Create tables and run one-time JSON migration."""
    with _conn() as c:
        for stmt in _DDL_STATEMENTS:
            c.execute(stmt)
        for stmts in _ALTER_COLUMNS.values():
            for stmt in stmts:
                c.execute(stmt)
    _migrate_once()


# ---------------------------------------------------------------------------
# Migration — runs exactly once
# ---------------------------------------------------------------------------

def _meta_get(key: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT value FROM app_meta WHERE key=%s", (key,)).fetchone()
        return row["value"] if row else None


def _meta_set(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO app_meta(key,value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value),
        )


def _migrate_once() -> None:
    if _meta_get("json_migrated") == "1":
        return
    logger.info("DB: one-time JSON → PostgreSQL migration starting")
    count = 0

    analyses_dir = _BASE_DIR / "candidate_analyses"
    scores_dir = _BASE_DIR / "candidate_scores"
    if analyses_dir.exists():
        for f in analyses_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                cid = data.get("candidate_id") or f.stem
                scores: dict = {}
                sf = scores_dir / f"{cid}.json"
                if sf.exists():
                    scores = json.loads(sf.read_text(encoding="utf-8"))
                _upsert_candidate_internal(cid, data, scores)
                count += 1
            except Exception as exc:
                logger.debug("Migrate skip %s: %s", f.name, exc)

    jp_dir = _BASE_DIR / "job_postings"
    if jp_dir.exists():
        for f in jp_dir.glob("*.json"):
            try:
                jd = json.loads(f.read_text(encoding="utf-8"))
                _upsert_job_internal(jd)
            except Exception as exc:
                logger.debug("Migrate skip JD %s: %s", f.name, exc)

    matches_dir = _BASE_DIR / "jd_matches"
    if matches_dir.exists():
        for jd_dir in matches_dir.iterdir():
            if not jd_dir.is_dir():
                continue
            for f in jd_dir.glob("*.json"):
                try:
                    m = json.loads(f.read_text(encoding="utf-8"))
                    _save_match_internal(jd_dir.name, f.stem, m)
                except Exception:
                    pass

    logger.info("DB: migration done — %d candidates imported", count)
    _meta_set("json_migrated", "1")


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------

def _extract_candidate_fields(data: dict, scores: dict | None = None) -> dict:
    overview = data.get("candidate_overview") or {}
    rubric = data.get("rubric_scorecard") or {}
    stage_sc = rubric.get("stage_scores") or {}
    sem = data.get("semantic_analysis") or {}
    dna = data.get("dna_fit") or {}
    exp = data.get("experience_analysis") or {}

    dna_val = dna.get("primary_dna") if isinstance(dna, dict) else (str(dna) if dna else "")

    resume_score = (
        stage_sc.get("resume_score_100") or
        rubric.get("total_score") or
        (data.get("rubric_result") or {}).get("total_score")
    )
    recruiter_score = stage_sc.get("recruiter_score_100")
    panel_score = stage_sc.get("panel_score_100")

    if scores:
        for entry in (scores.get("stages") or []):
            ss = entry.get("stage_scores") or {}
            stg = entry.get("stage")
            if stg == "resume" and not resume_score:
                resume_score = ss.get("resume_score_100") or entry.get("total_score")
            elif stg == "recruiter" and not recruiter_score:
                recruiter_score = ss.get("recruiter_score_100") or entry.get("total_score")
            elif stg == "panel" and not panel_score:
                panel_score = ss.get("panel_score_100") or entry.get("total_score")

    return {
        "name": (
            data.get("candidate_name") or
            overview.get("name") or
            overview.get("full_name") or ""
        ),
        "email": overview.get("email") or overview.get("contact_email") or "",
        "role_family": (
            data.get("role_family") or
            sem.get("top_role_family") or ""
        ),
        "band": rubric.get("overall_band") or "",
        "resume_score": resume_score,
        "recruiter_score": recruiter_score,
        "panel_score": panel_score,
        "yoe": exp.get("total_experience_years") or exp.get("total_years"),
        "dna": dna_val or "",
    }


def _upsert_candidate_internal(cid: str, data: dict, scores: dict | None = None) -> None:
    fields = _extract_candidate_fields(data, scores)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        row = c.execute("SELECT created_at FROM app_candidates WHERE candidate_id=%s", (cid,)).fetchone()
        created_at = row["created_at"] if row else now
        c.execute("""
            INSERT INTO app_candidates
            (candidate_id, name, email, role_family, band,
             resume_score, recruiter_score, panel_score, yoe, dna,
             analysis_json, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (candidate_id) DO UPDATE SET
                name=EXCLUDED.name, email=EXCLUDED.email,
                role_family=EXCLUDED.role_family, band=EXCLUDED.band,
                resume_score=EXCLUDED.resume_score, recruiter_score=EXCLUDED.recruiter_score,
                panel_score=EXCLUDED.panel_score, yoe=EXCLUDED.yoe, dna=EXCLUDED.dna,
                analysis_json=EXCLUDED.analysis_json, updated_at=EXCLUDED.updated_at
        """, (
            cid,
            fields["name"], fields["email"], fields["role_family"], fields["band"],
            fields["resume_score"], fields["recruiter_score"], fields["panel_score"],
            fields["yoe"], fields["dna"],
            json.dumps(data, ensure_ascii=False),
            created_at, now,
        ))


# ---------------------------------------------------------------------------
# Public API — Candidates
# ---------------------------------------------------------------------------

def upsert_candidate(candidate_id: str, analysis: dict, scores: dict | None = None) -> None:
    """Save or update a candidate from an analysis result dict."""
    with _lock:
        _upsert_candidate_internal(candidate_id, analysis, scores)


def update_candidate_stage_score(candidate_id: str, stage: str, score: float) -> None:
    """Update resume / recruiter / panel score for an existing candidate."""
    col = {"resume": "resume_score", "recruiter": "recruiter_score", "panel": "panel_score"}.get(stage)
    if not col:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            f"UPDATE app_candidates SET {col}=%s, updated_at=%s WHERE candidate_id=%s",
            (score, now, candidate_id),
        )


def get_candidate(candidate_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM app_candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("analysis_json"):
            try:
                d["analysis"] = json.loads(d["analysis_json"])
            except Exception:
                d["analysis"] = {}
        return d


def list_candidates(limit: int = 300) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT candidate_id, name, email, role_family, band,
                   resume_score, recruiter_score, panel_score, yoe, dna,
                   created_at, updated_at
            FROM app_candidates
            ORDER BY updated_at DESC
            LIMIT %s
        """, (limit,)).fetchall()
        return rows


# ---------------------------------------------------------------------------
# Public API — Job Postings
# ---------------------------------------------------------------------------

def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _upsert_job_internal(jd: dict) -> str:
    jd_id = jd.get("jd_id")
    if not jd_id:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17]
        jd_id = f"{_slugify(jd.get('title') or 'untitled')}-{ts}"
    mandatory = jd.get("mandatory_skills") or []
    nice = jd.get("nice_to_have_skills") or jd.get("nice_to_have") or []
    with _conn() as c:
        row = c.execute("SELECT created_at FROM job_postings WHERE jd_id=%s", (jd_id,)).fetchone()
        created_at = row["created_at"] if row else (jd.get("created_at") or datetime.now(timezone.utc).isoformat())
        c.execute("""
            INSERT INTO job_postings
            (jd_id, title, company, role_family, yoe_min, yoe_max,
             mandatory_skills, nice_to_have, description, dna_fit, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (jd_id) DO UPDATE SET
                title=EXCLUDED.title, company=EXCLUDED.company,
                role_family=EXCLUDED.role_family, yoe_min=EXCLUDED.yoe_min,
                yoe_max=EXCLUDED.yoe_max, mandatory_skills=EXCLUDED.mandatory_skills,
                nice_to_have=EXCLUDED.nice_to_have, description=EXCLUDED.description,
                dna_fit=EXCLUDED.dna_fit, status=EXCLUDED.status
        """, (
            jd_id,
            jd.get("title") or "Untitled",
            jd.get("company") or jd.get("company_name") or "",
            jd.get("role_family") or "",
            jd.get("yoe_min"),
            jd.get("yoe_max"),
            json.dumps(mandatory, ensure_ascii=False),
            json.dumps(nice, ensure_ascii=False),
            jd.get("description") or "",
            jd.get("dna_fit") or jd.get("preferred_dna") or "",
            jd.get("status") or "open",
            created_at,
        ))
    return jd_id


def upsert_job_posting(jd: dict) -> str:
    """Save or update a job posting. Returns jd_id."""
    with _lock:
        return _upsert_job_internal(jd)


def get_job_posting(jd_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM job_postings WHERE jd_id=%s", (jd_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("mandatory_skills", "nice_to_have"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else []
            except Exception:
                d[k] = []
        return d


def list_job_postings(include_closed: bool = False) -> list[dict]:
    with _conn() as c:
        sql = "SELECT * FROM job_postings"
        if not include_closed:
            sql += " WHERE status != 'closed'"
        sql += " ORDER BY created_at DESC"
        rows = c.execute(sql).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for k in ("mandatory_skills", "nice_to_have"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else []
                except Exception:
                    d[k] = []
            result.append(d)
        return result


def close_job_posting(jd_id: str) -> bool:
    with _conn() as c:
        c.execute("UPDATE job_postings SET status='closed' WHERE jd_id=%s", (jd_id,))
        return c.rowcount > 0


# ---------------------------------------------------------------------------
# Public API — JD Matches
# ---------------------------------------------------------------------------

def _save_match_internal(jd_id: str, candidate_id: str, result: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute("""
            INSERT INTO app_jd_matches
            (jd_id, candidate_id, rubric_score, jd_match_score, combined_score,
             recommendation, rubric_stage, candidate_name, match_json, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (jd_id, candidate_id) DO UPDATE SET
                rubric_score=EXCLUDED.rubric_score, jd_match_score=EXCLUDED.jd_match_score,
                combined_score=EXCLUDED.combined_score, recommendation=EXCLUDED.recommendation,
                rubric_stage=EXCLUDED.rubric_stage, candidate_name=EXCLUDED.candidate_name,
                match_json=EXCLUDED.match_json, created_at=EXCLUDED.created_at
        """, (
            jd_id, candidate_id,
            result.get("rubric_score"),
            result.get("jd_match_score") or result.get("overall_score"),
            result.get("combined_score"),
            result.get("recommendation"),
            result.get("rubric_stage"),
            result.get("candidate_name", ""),
            json.dumps(result, ensure_ascii=False),
            result.get("matched_at") or now,
        ))


def save_jd_match(jd_id: str, candidate_id: str, result: dict) -> None:
    with _lock:
        _save_match_internal(jd_id, candidate_id, result)


def get_jd_match(jd_id: str, candidate_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT match_json FROM app_jd_matches WHERE jd_id=%s AND candidate_id=%s",
            (jd_id, candidate_id)
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["match_json"])
        except Exception:
            return dict(row)


def list_jd_matches(jd_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT jd_id, candidate_id, candidate_name,
                   rubric_score, jd_match_score, combined_score,
                   recommendation, rubric_stage, created_at
            FROM app_jd_matches WHERE jd_id=%s
            ORDER BY combined_score DESC
        """, (jd_id,)).fetchall()
        return rows


# ---------------------------------------------------------------------------
# Public API — Sourcing Jobs
# ---------------------------------------------------------------------------

def upsert_sourcing_job(job: dict) -> None:
    """Save or update a sourcing job."""
    with _lock:
        now = datetime.now(timezone.utc).isoformat()
        job_id = job.get("job_id", "")
        with _conn() as c:
            row = c.execute("SELECT created_at FROM sourcing_jobs WHERE job_id=%s", (job_id,)).fetchone()
            created_at = row["created_at"] if row else (job.get("created_at") or now)
            c.execute("""
                INSERT INTO sourcing_jobs
                (job_id, query_text, criteria_json, jd_id, status, results_count, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (job_id) DO UPDATE SET
                    query_text=EXCLUDED.query_text, criteria_json=EXCLUDED.criteria_json,
                    jd_id=EXCLUDED.jd_id, status=EXCLUDED.status,
                    results_count=EXCLUDED.results_count
            """, (
                job_id,
                job.get("query_text") or "",
                json.dumps(job.get("criteria") or {}, ensure_ascii=False),
                job.get("jd_id"),
                job.get("status") or "pending",
                job.get("results_count") or 0,
                created_at,
            ))


def list_sourcing_jobs_db() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sourcing_jobs ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["criteria"] = json.loads(d.get("criteria_json") or "{}")
            except Exception:
                d["criteria"] = {}
            result.append(d)
        return result


# ---------------------------------------------------------------------------
# Public API — Sourced Candidates
# ---------------------------------------------------------------------------

def upsert_sourced_candidate(profile: dict) -> None:
    """Save or update a sourced GitHub candidate."""
    with _lock:
        now = datetime.now(timezone.utc).isoformat()
        username = profile.get("github_username", "")
        with _conn() as c:
            row = c.execute(
                "SELECT created_at FROM sourced_candidates WHERE github_username=%s", (username,)
            ).fetchone()
            created_at = row["created_at"] if row else now
            c.execute("""
                INSERT INTO sourced_candidates
                (github_username, display_name, location, company,
                 tech_stack_json, sourcing_score, yoe_proxy, github_url,
                 email, pipeline_status, sourcing_job_id, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (github_username) DO UPDATE SET
                    display_name=EXCLUDED.display_name, location=EXCLUDED.location,
                    company=EXCLUDED.company, tech_stack_json=EXCLUDED.tech_stack_json,
                    sourcing_score=EXCLUDED.sourcing_score, yoe_proxy=EXCLUDED.yoe_proxy,
                    github_url=EXCLUDED.github_url, email=EXCLUDED.email,
                    pipeline_status=EXCLUDED.pipeline_status,
                    sourcing_job_id=EXCLUDED.sourcing_job_id, updated_at=EXCLUDED.updated_at
            """, (
                username,
                profile.get("display_name") or "",
                profile.get("location") or "",
                profile.get("company") or "",
                json.dumps(profile.get("tech_stack") or [], ensure_ascii=False),
                profile.get("sourcing_score"),
                profile.get("yoe_proxy"),
                profile.get("github_url") or "",
                profile.get("email") or "",
                profile.get("pipeline_status") or "sourced",
                profile.get("sourcing_job_id") or "",
                created_at,
                now,
            ))


def get_sourced_candidate(github_username: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sourced_candidates WHERE github_username=%s", (github_username,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["tech_stack"] = json.loads(d.get("tech_stack_json") or "[]")
        except Exception:
            d["tech_stack"] = []
        return d


def update_sourced_pipeline_status(github_username: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE sourced_candidates SET pipeline_status=%s, updated_at=%s WHERE github_username=%s",
                (status, now, github_username),
            )


# ---------------------------------------------------------------------------
# Public API — Interview Scheduling
# ---------------------------------------------------------------------------

def update_interview_schedule(candidate_id: str, interview_date: str | None,
                               interview_time: str | None, interview_round: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE app_candidates SET interview_date=%s, interview_time=%s, interview_round=%s, updated_at=%s "
                "WHERE candidate_id=%s",
                (interview_date or None, interview_time or None, interview_round or None, now, candidate_id),
            )


def list_upcoming_interviews(days_ahead: int = 2) -> list[dict]:
    """Return app_candidates with an interview_date between today and today+days_ahead (inclusive)."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT candidate_id, name, email, interview_date, interview_time, interview_round, assigned_recruiter "
            "FROM app_candidates WHERE interview_date IS NOT NULL AND interview_date >= %s AND interview_date <= %s "
            "ORDER BY interview_date ASC, interview_time ASC",
            (today, cutoff),
        ).fetchall()
        return rows


# ---------------------------------------------------------------------------
# Public API — Candidate Outcomes
# ---------------------------------------------------------------------------

def upsert_outcome(candidate_id: str, data: dict) -> None:
    """Save or update a placement outcome for a candidate."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            row = c.execute(
                "SELECT created_at FROM candidate_outcomes WHERE candidate_id=%s", (candidate_id,)
            ).fetchone()
            created_at = row["created_at"] if row else now
            c.execute("""
                INSERT INTO candidate_outcomes
                (candidate_id, outcome, rejection_stage, placed_company,
                 placed_role, placed_date, feedback_notes, recorded_by,
                 created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (candidate_id) DO UPDATE SET
                    outcome=EXCLUDED.outcome, rejection_stage=EXCLUDED.rejection_stage,
                    placed_company=EXCLUDED.placed_company, placed_role=EXCLUDED.placed_role,
                    placed_date=EXCLUDED.placed_date, feedback_notes=EXCLUDED.feedback_notes,
                    recorded_by=EXCLUDED.recorded_by, updated_at=EXCLUDED.updated_at
            """, (
                candidate_id,
                data.get("outcome") or "IN_PROGRESS",
                data.get("rejection_stage") or "",
                data.get("placed_company") or "",
                data.get("placed_role") or "",
                data.get("placed_date") or "",
                data.get("feedback_notes") or "",
                data.get("recorded_by") or "",
                created_at,
                now,
            ))


def get_outcome(candidate_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM candidate_outcomes WHERE candidate_id=%s", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None


def list_outcomes(limit: int = 500) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT o.*, c.name, c.role_family, c.band,
                   c.resume_score, c.recruiter_score, c.panel_score
            FROM candidate_outcomes o
            LEFT JOIN app_candidates c ON c.candidate_id = o.candidate_id
            ORDER BY o.updated_at DESC
            LIMIT %s
        """, (limit,)).fetchall()
        return rows


def outcomes_summary() -> dict:
    """Return aggregate placement statistics."""
    rows = list_outcomes(limit=5000)
    total = len(rows)
    by_outcome: dict[str, int] = {}
    by_band: dict[str, dict] = {}
    by_role: dict[str, dict] = {}

    for r in rows:
        oc = r.get("outcome") or "IN_PROGRESS"
        by_outcome[oc] = by_outcome.get(oc, 0) + 1

        score = r.get("panel_score") or r.get("recruiter_score") or r.get("resume_score") or 0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if score >= 80:
            band = "80+"
        elif score >= 60:
            band = "60-79"
        elif score >= 40:
            band = "40-59"
        else:
            band = "<40"
        if band not in by_band:
            by_band[band] = {"total": 0, "placed": 0}
        by_band[band]["total"] += 1
        if oc == "PLACED":
            by_band[band]["placed"] += 1

        rf = r.get("role_family") or "Unknown"
        if rf not in by_role:
            by_role[rf] = {"total": 0, "placed": 0}
        by_role[rf]["total"] += 1
        if oc == "PLACED":
            by_role[rf]["placed"] += 1

    return {
        "total": total,
        "by_outcome": by_outcome,
        "by_band": by_band,
        "by_role": by_role,
        "recent": rows[:20],
    }


# ---------------------------------------------------------------------------
# Public API — Users
# ---------------------------------------------------------------------------

def create_user(email: str, password_hash: str, full_name: str, role: str = "recruiter") -> str:
    import uuid
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            c.execute("""
                INSERT INTO app_users (user_id, email, password_hash, full_name, role, is_active, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,1,%s,%s)
            """, (user_id, email, password_hash, full_name, role, now, now))
    return user_id


def get_user_by_email(email: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM app_users WHERE email=%s", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM app_users WHERE user_id=%s", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, email, full_name, role, is_active, created_at, updated_at FROM app_users ORDER BY created_at DESC"
        ).fetchall()
        return rows


def update_user(user_id: str, **fields) -> None:
    allowed = {"email", "password_hash", "full_name", "role", "is_active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k}=%s" for k in updates)
    values = list(updates.values()) + [user_id]
    with _lock:
        with _conn() as c:
            c.execute(f"UPDATE app_users SET {set_clause} WHERE user_id=%s", values)


def deactivate_user(user_id: str) -> None:
    update_user(user_id, is_active=0)


# ---------------------------------------------------------------------------
# Public API — Sessions
# ---------------------------------------------------------------------------

def create_session(user_id: str, expires_at: str) -> str:
    import uuid
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            c.execute("""
                INSERT INTO user_sessions (session_id, user_id, expires_at, created_at)
                VALUES (%s,%s,%s,%s)
            """, (session_id, user_id, expires_at, now))
    return session_id


def get_session(session_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM user_sessions WHERE session_id=%s", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_session(session_id: str) -> None:
    with _lock:
        with _conn() as c:
            c.execute("DELETE FROM user_sessions WHERE session_id=%s", (session_id,))


def cleanup_expired_sessions() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            c.execute("DELETE FROM user_sessions WHERE expires_at < %s", (now,))


# ---------------------------------------------------------------------------
# Public API — Standup Updates
# ---------------------------------------------------------------------------

def upsert_standup(user_id: str, date: str, data: dict) -> str:
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    jd_id = data.get("jd_id")
    with _lock:
        with _conn() as c:
            row = c.execute(
                "SELECT update_id FROM standup_updates WHERE user_id=%s AND date=%s AND (jd_id=%s OR (jd_id IS NULL AND %s IS NULL))",
                (user_id, date, jd_id, jd_id)
            ).fetchone()
            if row:
                update_id = row["update_id"]
                c.execute("""
                    UPDATE standup_updates SET today=%s, blockers=%s, priorities=%s
                    WHERE update_id=%s
                """, (data.get("today"), data.get("blockers"), data.get("priorities"), update_id))
            else:
                update_id = str(uuid.uuid4())
                c.execute("""
                    INSERT INTO standup_updates (update_id, user_id, jd_id, date, today, blockers, priorities, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (update_id, user_id, jd_id, date, data.get("today"), data.get("blockers"), data.get("priorities"), now))
    return update_id


def get_standup_by_date(user_id: str, date: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM standup_updates WHERE user_id=%s AND date=%s ORDER BY created_at",
            (user_id, date)
        ).fetchall()
        return rows


def list_standups_for_user(user_id: str, limit: int = 30) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM standup_updates WHERE user_id=%s ORDER BY date DESC, created_at DESC LIMIT %s",
            (user_id, limit)
        ).fetchall()
        return rows


def list_standups_for_team(limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT s.*, u.full_name, u.email
            FROM standup_updates s
            LEFT JOIN app_users u ON u.user_id = s.user_id
            ORDER BY s.date DESC, s.created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()
        return rows


# ---------------------------------------------------------------------------
# Public API — JD / Candidate Assignment
# ---------------------------------------------------------------------------

def assign_jd_to_recruiter(jd_id: str, recruiter_user_id: str) -> None:
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE job_postings SET assigned_recruiter=%s WHERE jd_id=%s",
                (recruiter_user_id, jd_id)
            )


def assign_sales_agent_to_jd(jd_id: str, sales_user_id: str) -> None:
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE job_postings SET assigned_sales_agent=%s WHERE jd_id=%s",
                (sales_user_id, jd_id)
            )


def set_jd_deadline(jd_id: str, deadline: str) -> None:
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE job_postings SET deadline=%s WHERE jd_id=%s",
                (deadline, jd_id)
            )


def assign_candidate_to_recruiter(candidate_id: str, recruiter_user_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE app_candidates SET assigned_recruiter=%s, updated_at=%s WHERE candidate_id=%s",
                (recruiter_user_id, now, candidate_id)
            )


def list_candidates_for_recruiter(recruiter_user_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT candidate_id, name, email, role_family, band,
                   resume_score, recruiter_score, panel_score, yoe, dna,
                   created_at, updated_at, assigned_recruiter
            FROM app_candidates
            WHERE assigned_recruiter=%s
            ORDER BY updated_at DESC
        """, (recruiter_user_id,)).fetchall()
        return rows


def list_jds_for_recruiter(recruiter_user_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT * FROM job_postings
            WHERE assigned_recruiter=%s AND status != 'closed'
            ORDER BY created_at DESC
        """, (recruiter_user_id,)).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for k in ("mandatory_skills", "nice_to_have"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else []
                except Exception:
                    d[k] = []
            result.append(d)
        return result


def get_recruiter_stats(recruiter_user_id: str) -> dict:
    """Sourced/screened/placed counts for a recruiter (current month)."""
    with _conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM app_candidates WHERE assigned_recruiter=%s",
            (recruiter_user_id,)
        ).fetchone()["count"]
        screened = c.execute(
            "SELECT COUNT(*) FROM app_candidates WHERE assigned_recruiter=%s AND recruiter_score IS NOT NULL",
            (recruiter_user_id,)
        ).fetchone()["count"]
        placed = c.execute("""
            SELECT COUNT(*) FROM candidate_outcomes co
            JOIN app_candidates ca ON ca.candidate_id = co.candidate_id
            WHERE ca.assigned_recruiter=%s AND co.outcome='PLACED'
        """, (recruiter_user_id,)).fetchone()["count"]
    return {"total": total, "screened": screened, "placed": placed}


def list_jds_for_sales_agent(sales_user_id: str) -> list[dict]:
    """JDs where the given user is the assigned sales agent."""
    with _conn() as c:
        rows = c.execute("""
            SELECT * FROM job_postings
            WHERE assigned_sales_agent=%s AND status != 'closed'
            ORDER BY created_at DESC
        """, (sales_user_id,)).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for k in ("mandatory_skills", "nice_to_have"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else []
                except Exception:
                    d[k] = []
            result.append(d)
    return result


def list_standups_for_roles(roles: tuple, limit: int = 100) -> list[dict]:
    """Standups from app_users whose role is in the given set."""
    placeholders = ",".join(["%s"] * len(roles))
    with _conn() as c:
        rows = c.execute(f"""
            SELECT s.*, u.full_name, u.email, u.role
            FROM standup_updates s
            LEFT JOIN app_users u ON u.user_id = s.user_id
            WHERE u.role IN ({placeholders})
            ORDER BY s.date DESC, s.created_at DESC
            LIMIT %s
        """, (*roles, limit)).fetchall()
        return rows


def get_team_leaderboard(roles: tuple | None = None) -> list[dict]:
    """Stats for team members, ordered by placements desc.

    roles: optional tuple of role strings to include; defaults to all recruiting roles.
    """
    if roles is None:
        roles = ("recruiter", "recruiter_head", "super_admin")
    app_users = [u for u in list_users() if u["role"] in roles and u["is_active"]]
    board = []
    for u in app_users:
        stats = get_recruiter_stats(u["user_id"])
        jds = list_jds_for_recruiter(u["user_id"])
        board.append({
            "user_id": u["user_id"],
            "full_name": u["full_name"],
            "email": u["email"],
            "role": u["role"],
            "jds_active": len(jds),
            "candidates_total": stats["total"],
            "candidates_screened": stats["screened"],
            "candidates_placed": stats["placed"],
            "placement_rate": round(stats["placed"] / stats["total"] * 100, 1) if stats["total"] else 0,
        })
    board.sort(key=lambda x: x["candidates_placed"], reverse=True)
    return board
