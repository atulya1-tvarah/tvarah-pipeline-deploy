
from __future__ import annotations
import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from models import ResumeFeedback, ResumeInput, StageUpdateInput, CandidateSearchQuery, ClientFitRequest, QuestionAnswerInput, CallScoresInput
from engine import analyze_resume
from eval_framework import save_live_analysis_report
from llm_recruiter_analysis import generate_recruiter_analysis
from interview_question_engine import build_interview_questions
from transcript_scoring_engine import score_transcript_against_resume
from boss_explainability import build_boss_explainability
from feedback_store import save_feedback
from candidate_score_store import save_candidate_score, load_candidate_score, list_candidate_scores
from candidate_analysis_store import save_candidate_analysis, load_candidate_analysis, list_candidate_analyses
from job_posting_store import save_job_posting, load_job_posting, list_job_postings, close_job_posting
from client_config_store import save_client_config, load_client_config, load_role_config, upsert_role_config, list_client_configs
from client_report_store import append_position_event, get_client_report, save_candidate_feedback, save_client_feedback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("resume_intelligence.app")

# uvicorn runs a single worker/event loop here -- analyze_resume() and the
# PDF extraction step are synchronous CPU-bound calls that would otherwise
# block that one loop for their entire ~70s+ duration, serializing every
# resume parse in the system (across all recruiters) behind whichever one
# started first. Running them in a bounded thread pool instead lets uvicorn
# keep accepting/dispatching other requests, and PyTorch's C++ ops release
# the GIL during their own computation so concurrent parses get real
# parallelism, not just interleaving. Sized to 4 -- container has 8 vCPUs
# and 8GB memory with ~2.6GB already used by loaded models at idle, and
# torch is pinned to 1 thread per call (see bert_signal_engine.py), so 4
# concurrent parses is comfortably within both budgets without needing to
# guess at a higher number and risk OOM under real concurrent load.
_PARSE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resume-parse")

app = FastAPI(title="Resume Intelligence Engine")

# ── Portal REST API (JSON endpoints for tvarah-portal-ui) ────────────────────
try:
    from portal_api import router as _portal_router
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten to portal domain in prod
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(_portal_router)
    logging.getLogger("resume_intelligence.app").info("Portal API mounted at /api/v1")
except Exception as _pe:
    logging.getLogger("resume_intelligence.app").warning("Portal API not mounted: %s", _pe)

# Middleware is added after auth module is loaded (see below)

# Initialise SQLite DB on startup (creates tables + migrates JSON files once)
try:
    from database import init_db
    init_db()
    logger.info("DB initialised")
except Exception as _dbe:
    logger.warning("DB init failed: %s", _dbe)

# Auth helpers
from auth import hash_password, invalidate_session, make_session, resolve_session, verify_password
from database import create_user, get_user_by_email, update_user


def _seed_admin() -> None:
    email = os.getenv("ADMIN_EMAIL", "sandeep@company.com")
    password = os.getenv("ADMIN_PASSWORD", "changeme123")
    if not get_user_by_email(email):
        create_user(
            email=email,
            password_hash=hash_password(password),
            full_name=os.getenv("ADMIN_NAME", "Sandeep Guduru"),
            role="super_admin",
        )
        logger.info("Seeded super_admin: %s", email)

    # Seed Chandrima as sales_head
    rec_email = os.getenv("RECRUITER_EMAIL", "chandrima@company.com")
    rec_password = os.getenv("RECRUITER_PASSWORD", "recruiter123")
    existing_chandrima = get_user_by_email(rec_email)
    if not existing_chandrima:
        create_user(
            email=rec_email,
            password_hash=hash_password(rec_password),
            full_name=os.getenv("RECRUITER_NAME", "Chandrima"),
            role="sales_head",
        )
        logger.info("Seeded sales_head: %s", rec_email)
    elif existing_chandrima.get("role") == "recruiter":
        # Upgrade legacy seed to sales_head
        update_user(existing_chandrima["user_id"], role="sales_head")
        logger.info("Upgraded %s to sales_head", rec_email)


try:
    _seed_admin()
except Exception as _sae:
    logger.warning("Admin seed skipped: %s", _sae)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

class _LoginRedirect(Exception):
    pass


from fastapi import Request as _Req
from fastapi.responses import RedirectResponse as _RR
from starlette.middleware.base import BaseHTTPMiddleware


class _AuthRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except _LoginRedirect:
            return _RR("/login", status_code=302)


app.add_middleware(_AuthRedirectMiddleware)


def get_current_user(request: Request) -> dict:
    session_id = request.cookies.get("session_id")
    user = resolve_session(session_id)
    if not user:
        raise _LoginRedirect()
    return user


def require_role(*roles: str):
    def _dep(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return _dep


# ---------------------------------------------------------------------------
# Login / Logout routes
# ---------------------------------------------------------------------------

_LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Resume Intelligence — Portal Login</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",system-ui,sans-serif;background:linear-gradient(135deg,#EEF0FB 0%,#F5F3FF 100%);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.login-wrap{display:flex;width:960px;max-width:100%;min-height:540px;border-radius:20px;overflow:hidden;box-shadow:0 20px 60px rgba(53,51,149,.18)}
.login-side{flex:1.1;background:linear-gradient(150deg,#2D2B8A 0%,#4F46E5 60%,#7C3AED 100%);padding:52px 44px;color:#fff;display:flex;flex-direction:column;justify-content:space-between;position:relative;overflow:hidden}
.login-side::before{content:"";position:absolute;top:-60px;right:-60px;width:200px;height:200px;border-radius:50%;background:rgba(255,255,255,.07)}
.login-side::after{content:"";position:absolute;bottom:-40px;left:-40px;width:160px;height:160px;border-radius:50%;background:rgba(255,255,255,.05)}
.login-side h1{font-size:28px;font-weight:900;margin-bottom:10px;line-height:1.2;position:relative}
.login-side p{font-size:13.5px;opacity:.82;line-height:1.65;position:relative}
.feature-list{list-style:none;margin-top:30px;position:relative}
.feature-list li{display:flex;align-items:flex-start;gap:11px;margin-bottom:13px;font-size:13px;opacity:.88}
.feat-icon{width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,.18);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:11px}
.login-form-wrap{flex:1;background:#fff;padding:52px 44px;display:flex;flex-direction:column;justify-content:center}
.ri-logo{display:flex;align-items:center;gap:10px;margin-bottom:32px}
.ri-logo-icon{width:36px;height:36px;background:linear-gradient(135deg,#353395,#6366F1);border-radius:9px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:800}
.ri-logo-name{font-size:14px;font-weight:800;color:#262626}
.ri-logo-tag{font-size:11px;color:#62748E;font-weight:500}
.login-form-wrap h2{font-size:22px;font-weight:900;color:#262626;margin-bottom:4px}
.login-sub{font-size:13px;color:#62748E;margin-bottom:26px;line-height:1.5}
.form-group{margin-bottom:15px}
label{display:block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:#62748E;margin-bottom:5px}
input[type=email],input[type=password]{width:100%;padding:11px 13px;border:1.5px solid #CAD5E2;border-radius:9px;font-size:14px;color:#262626;outline:none;transition:border-color .15s,box-shadow .15s;font-family:inherit}
input:focus{border-color:#353395;box-shadow:0 0 0 3px rgba(53,51,149,.1)}
.btn-login{width:100%;padding:13px;background:linear-gradient(135deg,#353395,#6366F1);color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;margin-top:4px;letter-spacing:.01em;transition:opacity .15s,transform .1s}
.btn-login:hover{opacity:.92;transform:translateY(-1px)}
.btn-login:active{transform:translateY(0)}
.error{background:#FEF2F2;color:#DC2626;border:1px solid #FECACA;border-radius:9px;padding:11px 14px;font-size:13px;margin-bottom:16px;display:flex;align-items:flex-start;gap:8px}
.forgot-hint{margin-top:14px;font-size:12px;color:#62748E;text-align:center;line-height:1.5}
.brand{font-size:11.5px;color:#ADB5C8;margin-top:auto;padding-top:28px;border-top:1px solid #F1F5F9;margin-top:28px;display:flex;align-items:center;justify-content:space-between}
</style></head><body>
<div class="login-wrap">
  <div class="login-side">
    <div>
      <div style="font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.15em;opacity:.6;margin-bottom:10px">Resume Intelligence</div>
      <h1>Your Recruitment<br>Portal</h1>
      <p style="margin-top:8px">One login for everything — candidate scoring, JD matching, team standups, and placement tracking.</p>
      <ul class="feature-list">
        <li><span class="feat-icon">📄</span>AI-powered resume scoring &amp; rubric</li>
        <li><span class="feat-icon">🎯</span>Role taxonomy + DNA fit detection</li>
        <li><span class="feat-icon">📞</span>Recruiter &amp; panel interview workflows</li>
        <li><span class="feat-icon">📊</span>Team standup board &amp; leaderboard</li>
        <li><span class="feat-icon">✅</span>Placement outcome tracking</li>
      </ul>
    </div>
    <div style="font-size:11.5px;opacity:.5;position:relative">© Resume Intelligence · Internal Portal</div>
  </div>
  <div class="login-form-wrap">
    <div class="ri-logo">
      <div class="ri-logo-icon">RI</div>
      <div><div class="ri-logo-name">Resume Intelligence</div><div class="ri-logo-tag">Internal Portal</div></div>
    </div>
    <h2>Sign in</h2>
    <p class="login-sub">Use your portal credentials to access the platform.<br>Contact your admin if you need access.</p>
    {error_block}
    <form method="POST" action="/login" autocomplete="on">
      <div class="form-group"><label>Work Email</label><input type="email" name="email" required autofocus placeholder="you@company.com" autocomplete="email"></div>
      <div class="form-group"><label>Password</label><input type="password" name="password" required placeholder="••••••••" autocomplete="current-password"></div>
      <button class="btn-login" type="submit">Sign In to Portal</button>
    </form>
    <p class="forgot-hint">Forgot your password? Ask your <strong>admin</strong> to reset it from the Admin panel.</p>
    <div class="brand">
      <span>Resume Intelligence &mdash; v2.0</span>
      <span>Secure · Internal Use Only</span>
    </div>
  </div>
</div>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # Already logged in → redirect
    if resolve_session(request.cookies.get("session_id")):
        return RedirectResponse("/standup", status_code=302)
    return HTMLResponse(_LOGIN_PAGE.replace("{error_block}", ""))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    user = get_user_by_email(email)
    if not user or not user.get("is_active") or not verify_password(password, user["password_hash"]):
        err = '<div class="error">Invalid email or password. Please try again.</div>'
        return HTMLResponse(_LOGIN_PAGE.replace("{error_block}", err), status_code=401)
    session_id = make_session(user["user_id"])
    resp = RedirectResponse("/standup", status_code=302)
    resp.set_cookie("session_id", session_id, httponly=True, samesite="lax", max_age=7 * 86400)
    return resp


@app.get("/logout")
def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id:
        invalidate_session(session_id)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session_id")
    return resp
EVAL_RUNS_DIR = Path("eval_runs")
CANDIDATE_ANALYSES_DIR = Path("candidate_analyses")


def _load_eval_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_float(value):
    try:
        if value in (None, "", {}):
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value):
    try:
        if value in (None, "", {}):
            return None
        return int(value)
    except Exception:
        return None


def _summarize_eval_report(report: dict, path: Path) -> dict:
    run = report.get("run", {}) or {}
    summary = report.get("summary", {}) or {}
    regression = report.get("regression", {}) or {}
    dataset = report.get("dataset", {}) or {}
    regressions = regression.get("regressions", []) or []
    return {
        "run_id": run.get("run_id") or path.parent.name,
        "run_label": run.get("run_label") or "",
        "dataset_name": run.get("dataset_name") or dataset.get("metadata", {}).get("dataset_name") or "unknown-dataset",
        "generated_at": run.get("generated_at") or "",
        "report_path": str(path),
        "total_cases": _safe_int(summary.get("total_cases")) or 0,
        "expectation_case_count": _safe_int(summary.get("expectation_case_count")) or 0,
        "expectation_match_rate": _safe_float(summary.get("expectation_match_rate")),
        "role_family_match_rate": _safe_float(summary.get("role_family_match_rate")),
        "band_match_rate": _safe_float(summary.get("band_match_rate")),
        "dna_match_rate": _safe_float(summary.get("dna_match_rate")),
        "score_mae": _safe_float(summary.get("score_mae")),
        "llm_score_success_rate": _safe_float(summary.get("llm_score_success_rate")),
        "llm_skill_success_rate": _safe_float(summary.get("llm_skill_success_rate")),
        "score_consistency_rate": _safe_float(summary.get("score_consistency_rate")),
        "average_total_score": _safe_float(summary.get("average_total_score")),
        "role_prediction_rate": _safe_float(summary.get("role_prediction_rate")),
        "band_prediction_rate": _safe_float(summary.get("band_prediction_rate")),
        "dna_prediction_rate": _safe_float(summary.get("dna_prediction_rate")),
        "name_extraction_rate": _safe_float(summary.get("name_extraction_rate")),
        "contact_extraction_rate": _safe_float(summary.get("contact_extraction_rate")),
        "recruiter_summary_rate": _safe_float(summary.get("recruiter_summary_rate")),
        "average_confidence_score": _safe_float(summary.get("average_confidence_score")),
        "average_latency_ms": _safe_float(summary.get("average_latency_ms")),
        "average_tokens_per_resume": _safe_float(summary.get("average_tokens_per_resume")),
        "average_cost_per_resume_usd": _safe_float(summary.get("average_cost_per_resume_usd")),
        "total_cost_usd": _safe_float(summary.get("total_cost_usd")),
        "average_remaining_context_tokens": _safe_float(summary.get("average_remaining_context_tokens")),
        "regression_count": len(regressions),
        "gate_fail_count": len([item for item in regression.get("gate_results", []) or [] if item.get("status") == "fail"]),
    }


def _read_eval_runs() -> list[dict]:
    if not EVAL_RUNS_DIR.exists():
        return []
    runs = []
    for path in EVAL_RUNS_DIR.rglob("report.json"):
        try:
            report = _load_eval_report(path)
            runs.append({"summary": _summarize_eval_report(report, path), "report": report})
        except Exception as exc:
            logger.warning("Failed to load eval report path=%s error=%s", path, exc)
    runs.sort(key=lambda item: item["summary"].get("generated_at") or "", reverse=True)
    return runs


def _leaderboard_rows(runs: list[dict]) -> list[dict]:
    scored = []
    for item in runs:
        summary = item["summary"]
        expectation = summary.get("expectation_match_rate")
        consistency = summary.get("score_consistency_rate")
        llm = summary.get("llm_score_success_rate")
        penalties = summary.get("regression_count", 0) + summary.get("gate_fail_count", 0)
        composite = 0.0
        if expectation is not None:
            composite += expectation * 0.45
        if consistency is not None:
            composite += consistency * 0.3
        if llm is not None:
            composite += llm * 0.25
        composite -= penalties * 0.05
        row = dict(summary)
        row["composite_score"] = round(composite, 3)
        scored.append(row)
    scored.sort(key=lambda item: item["composite_score"], reverse=True)
    return scored[:25]

@app.get("/health")
def health():
    return {"status":"ok"}

@app.get("/upload", response_class=HTMLResponse)
def upload_page(user: dict = Depends(get_current_user)):
    sidebar_html = _sidebar("upload", user)
    return (f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Resume Intelligence — Upload</title><meta name="viewport" content="width=device-width, initial-scale=1">''' + _BASE_CSS + '''<style>
    .wrap{padding:24px 24px 60px}
    .hero,.card{background:#FFFFFF;border:1px solid #CAD5E2;border-radius:12px}
    .hero{padding:22px 24px 18px;margin-bottom:16px}h1,h2,h3{margin:0 0 10px;color:#262626}.muted{color:#62748E}.toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
    input[type=file]{padding:10px 12px;background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;color:#262626;font-size:13px}
    button,.linkbtn{background:#353395;color:#fff;border:none;border-radius:10px;padding:10px 18px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;font-size:13px}
    button:hover,.linkbtn:hover{opacity:.88}
    .grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.col-3{grid-column:span 3}.col-4{grid-column:span 4}.col-5{grid-column:span 5}.col-6{grid-column:span 6}.col-7{grid-column:span 7}.col-8{grid-column:span 8}.col-12{grid-column:span 12}.card{padding:16px}
    .metric{font-size:30px;font-weight:800;letter-spacing:-.02em;color:#353395}.kicker{text-transform:uppercase;letter-spacing:.1em;font-size:10px;color:#62748E;font-weight:600;margin-bottom:4px}
    .band{display:inline-flex;padding:5px 12px;border-radius:999px;font-size:12px;font-weight:700;border:1px solid #E0E0F5;background:#F0F0FB;color:#353395}
    .pill{display:inline-flex;align-items:center;background:#F0F0FB;border:1px solid #E0E0F5;border-radius:999px;padding:4px 10px;margin:3px 4px 3px 0;font-size:11px;font-weight:500;color:#353395}
    .skill-card,.question,.score-row{background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;padding:14px;margin-bottom:12px}
    .score-row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.score-row span:last-child{font-weight:700}
    .list{padding-left:18px;margin:0}.list li{margin-bottom:8px;color:#444}.summary{white-space:pre-wrap;line-height:1.55;color:#444}
    .progress{height:8px;border-radius:999px;background:#E5E7EB;overflow:hidden;margin-top:10px}.progress>div{height:100%;background:linear-gradient(90deg,#353395,#6366F1);width:0}
    pre{white-space:pre-wrap;background:#F8FAFC;border:1px solid #CAD5E2;border-radius:10px;padding:14px;max-height:460px;overflow:auto;color:#333;font-size:12px}
    .meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.meta div{padding:10px 12px;background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px}
    .question h3{font-size:14px;margin-bottom:6px;color:#353395}
    .semantic-block{background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;padding:14px;margin-top:12px}
    .semantic-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:8px}
    .semantic-title{font-weight:700;color:#262626}
    .semantic-note{color:#62748E;font-size:12px;line-height:1.45}
    .semantic-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}
    .semantic-col{background:#fff;border:1px solid #CAD5E2;border-radius:10px;padding:12px}
    .semantic-col h4{margin:0 0 8px;font-size:13px;color:#262626}
    .semantic-empty{color:#62748E;font-size:12px}
    .tile-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}
    .detail-card{background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;overflow:hidden}
    .detail-card summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
    .detail-card summary::-webkit-details-marker{display:none}
    .detail-body{padding:0 14px 14px}
    .detail-score{font-weight:800;color:#353395;white-space:nowrap}
    @media(max-width:980px){.col-3,.col-4,.col-5,.col-6,.col-7,.col-8,.col-12{grid-column:span 12}.meta,.semantic-grid{grid-template-columns:1fr}}
    @media(max-width:980px){.tile-grid{grid-template-columns:1fr}}
    .score-meta{flex:1}.score-meta b{display:block;color:#262626}.score-help{color:#62748E;font-size:12px;line-height:1.45;margin-top:4px}.score-inputs{color:#62748E;font-size:12px;line-height:1.45;margin-top:6px}
    </style></head><body>''' + f'<div class="app-shell">{sidebar_html}<div class="main">' + '''<div class="wrap"><div class="hero"><div class="kicker">Resume Analysis Engine</div><h1 style="font-size:22px;font-weight:800">Upload Resume</h1><p style="color:#62748E;font-size:14px;margin:4px 0 14px">Upload a resume JSON, PDF, or DOCX to generate skill depth, role taxonomy, scoring breakdown, and telephonic screening prompts.</p><div class="toolbar"><input type="file" id="file" accept=".json,.pdf,.docx"><button onclick="upload()">Analyze Resume</button><a class="linkbtn" href="/evals">Eval Workspace</a><div id="status" class="muted" style="font-size:13px">Ready. Accepts JSON, PDF, or DOCX.</div></div></div><div id="results" style="display:none;"><div class="col-12 card" style="margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px"><div><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#62748E;margin-bottom:4px">Analysis complete</div><div style="font-size:16px;font-weight:800;color:#262626" id="heroName">&mdash;</div></div><div style="display:flex;gap:10px;flex-wrap:wrap"><a id="linkRecruiterScreen" href="#" style="background:#D97706;color:#fff;border-radius:10px;padding:9px 18px;font-size:13px;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:6px">&#128222; Recruiter Screen &#8594;</a><a id="linkPanelScreen" href="#" style="background:#353395;color:#fff;border-radius:10px;padding:9px 18px;font-size:13px;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:6px">&#128203; Panel Screen &#8594;</a></div></div><div class="grid">
    <div class="col-3 card"><div class="kicker">Resume Score <span id="scoreMode" style="font-size:11px;font-weight:400;color:var(--muted)">/ 100</span></div><div id="score" class="metric">-</div><div id="scoreRawNote" class="score-inputs" style="margin-top:4px"></div><div class="progress" style="margin-top:6px"><div id="scoreBar"></div></div></div>
    <div class="col-3 card"><div class="kicker">Experience</div><div id="rubricExp" class="metric" style="font-size:28px">-</div><div class="progress" style="margin-top:8px"><div id="rubricExpBar"></div></div><div class="score-inputs" style="margin-top:6px">out of 40</div></div>
    <div class="col-3 card"><div class="kicker">Skills</div><div id="rubricSkills" class="metric" style="font-size:28px">-</div><div class="progress" style="margin-top:8px"><div id="rubricSkillsBar"></div></div><div class="score-inputs" style="margin-top:6px">out of 45 <span id="skillsRecruiterPending" style="color:var(--warn)"></span> <span id="skillsPanelPending" style="color:var(--primary2)"></span></div></div>
    <div class="col-3 card"><div class="kicker">Education <span style="font-size:11px;font-weight:400;color:var(--muted)">/ 15</span></div><div id="rubricEdu" class="metric" style="font-size:28px">-</div><div class="progress" style="margin-top:6px"><div id="rubricEduBar"></div></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px"><div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:8px 10px"><div style="font-size:10px;text-transform:uppercase;font-weight:700;color:var(--text2);letter-spacing:.08em">Core</div><div style="font-size:18px;font-weight:800;color:var(--primary)" id="rubricEduCore">-</div><div style="font-size:11px;color:var(--text2)">/ 10</div></div><div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:8px 10px"><div style="font-size:10px;text-transform:uppercase;font-weight:700;color:var(--text2);letter-spacing:.08em">Brownie Pts</div><div style="font-size:18px;font-weight:800;color:var(--amber)" id="rubricEduBonus">-</div><div style="font-size:11px;color:var(--text2)">/ 5</div></div></div><div class="score-inputs" style="margin-top:6px"><span id="eduRecruiterPending" style="color:var(--warn)"></span></div></div>
    <div class="col-3 card"><div class="kicker">Role family</div><div id="role" class="metric" style="font-size:22px;line-height:1.2">-</div></div>
    <div class="col-3 card"><div class="kicker">Band</div><div id="band" class="metric" style="font-size:24px">-</div><div id="bandPill" class="band" style="margin-top:10px">Pending</div></div>
    <div class="col-3 card"><div class="kicker">DNA fit</div><div id="dna" class="metric" style="font-size:22px;line-height:1.2">-</div><div id="dnaMeta" class="score-inputs" style="margin-top:10px"></div></div>
    <div class="col-3 card"><div class="kicker">Reject flags</div><div id="rejectFlags" class="score-inputs" style="margin-top:6px;color:var(--warn)">-</div></div>
    <div class="col-12 card" id="stageScoreCard" style="display:none"><h2>Stage Scores <span style="font-size:13px;font-weight:400;color:var(--muted)">— Resume auto-score → Recruiter fills → Panel fills</span></h2><div id="stageScoreContent"></div></div>
    <div class="col-5 card"><h2>Candidate Overview</h2><div id="overview" class="meta"></div></div>
    <div class="col-7 card"><h2>Recruiter Summary</h2><div id="summary" class="summary muted"></div></div>
    <div class="col-12 card" id="recruiterIntake" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:12px">
        <div><h2 style="margin:0">Recruiter Intake <span style="font-size:13px;font-weight:400;color:var(--muted)">— Fill these after the phone screen</span></h2><div class="muted" style="margin-top:4px;font-size:12px">These 4 params can only be scored by you after speaking with the candidate. Submit to update the score to a Recruiter-stage /100.</div></div>
        <div id="recruiterScoreBadge" style="font-size:28px;font-weight:800;color:var(--warn)"></div>
      </div>
      <div id="recruiterIntakeRows" style="display:grid;grid-template-columns:1fr 1fr;gap:12px"></div>
      <div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:center">
        <button id="submitRecruiterBtn" onclick="submitRecruiterScores()" style="background:linear-gradient(135deg,var(--amber),#F59E0B);color:#fff">Submit Recruiter Scores</button>
        <div id="recruiterSubmitStatus" class="muted" style="font-size:13px"></div>
      </div>
    </div>
    <div class="col-12 card" id="panelIntake" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:12px">
        <div><h2 style="margin:0">Panel Intake <span style="font-size:13px;font-weight:400;color:var(--muted)">— Fill these after the technical interview</span></h2><div class="muted" style="margin-top:4px;font-size:12px">These params are scored by the interview panel after the technical round. Submit to update to the final Panel-stage /100.</div></div>
        <div id="panelScoreBadge" style="font-size:28px;font-weight:800;color:var(--primary2)"></div>
      </div>
      <div id="panelIntakeRows" style="display:grid;grid-template-columns:1fr 1fr;gap:12px"></div>
      <div style="margin-top:14px">
        <div style="font-size:13px;font-weight:600;color:var(--muted);margin-bottom:10px">Qualitative Assessment (free text — no score)</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div style="background:var(--bg);border:1px solid var(--border);border-radius:14px;padding:14px">
            <b style="font-size:13px">Coding Skills Assessment</b>
            <div class="score-inputs" style="margin:6px 0 8px">Panel assessment of live coding ability — data structures, algorithms, code quality, debugging approach.</div>
            <textarea id="pi_coding_skills" rows="3" placeholder="e.g. Solved two medium LC problems cleanly, good complexity awareness, minor syntax slips..." style="width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px;resize:vertical"></textarea>
          </div>
          <div style="background:var(--bg);border:1px solid var(--border);border-radius:14px;padding:14px">
            <b style="font-size:13px">Conceptual Skills Assessment</b>
            <div class="score-inputs" style="margin:6px 0 8px">Panel assessment of core CS and domain conceptual understanding — system design, architecture decisions, tradeoffs.</div>
            <textarea id="pi_conceptual_skills" rows="3" placeholder="e.g. Strong on distributed system fundamentals, explained CAP theorem well, weaker on database internals..." style="width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px;resize:vertical"></textarea>
          </div>
        </div>
      </div>
      <div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:center">
        <button id="submitPanelBtn" onclick="submitPanelScores()" style="background:linear-gradient(135deg,#6b9fff,#4a7fe0);color:#fff">Submit Panel Scores</button>
        <div id="panelSubmitStatus" class="muted" style="font-size:13px"></div>
      </div>
    </div>
    <div class="col-12 card"><details id="rubricDetails" open><summary style="list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center"><h2 style="margin:0">Rubric Scorecard <span id="rubricTotal" style="font-size:18px;font-weight:400;color:var(--muted)"></span></h2><span style="font-size:12px;color:var(--muted)">Click to collapse ▾</span></summary><div id="rubricScorecard" style="margin-top:14px"></div></details><details id="legacyScoreDetails" style="margin-top:16px"><summary style="cursor:pointer;color:var(--muted);font-size:13px;padding:6px 0;list-style:none">&#9654; AI Judgment Scorecard (6-dimension 100pt)</summary><div id="scorebreakdown" style="margin-top:10px"></div></details></div>
    <div class="col-8 card"><h2>Top Skill Evidence</h2><div id="skills"></div></div>
    <div class="col-6 card"><h2>Semantic Taxonomy</h2><div id="semantic"></div></div>
    <div class="col-6 card"><h2>Experience Analysis</h2><div id="experience"></div></div>
    <div class="col-6 card"><h2>Education Analysis</h2><div id="education"></div></div>
    <div class="col-6 card"><h2>Strengths</h2><ul id="strengths" class="list"></ul></div>
    <div class="col-6 card"><h2>Gaps & Risks</h2><ul id="gaps" class="list"></ul><div id="risks" class="muted" style="margin-top:10px"></div></div>
    <div class="col-12 card" id="interviewPanel" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
        <div><h2 style="margin:0">Recruiter Call Panel</h2><div class="muted" style="margin-top:4px" id="interviewPanelIntro"></div></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button onclick="generateQuestions()" id="genQBtn">Generate Interview Questions</button>
          <button onclick="applyCallScores()" id="applyScoresBtn" style="display:none;background:linear-gradient(135deg,var(--amber),#F59E0B);color:#fff">Apply Call Scores to Rubric</button>
        </div>
      </div>
      <div id="interviewQuestionsWrap" style="margin-top:18px"></div>
      <div id="callScoresResult" style="margin-top:14px"></div>
    </div>
    <div class="col-12 card"><h2>Analysis Output JSON</h2><div class="muted" style="margin-bottom:12px">This section shows the normalized analysis response produced by the app, not the original uploaded extractor JSON.</div><pre id="raw"></pre></div>
    </div></div></div><script>
    function esc(x){return x ?? "N/A";}
    function pills(items){return (items||[]).map(i=>`<span class="pill">${i}</span>`).join("");}
    function boolLabel(v){return v===true?"Yes":v===false?"No":esc(v);}
    function formatProjectMix(skill){
      const roles = skill.evidence_roles || [];
      if(!roles.length) return "N/A";
      const total = {};
      roles.forEach((role, idx) => {
        const projectType = (skill.project_contexts || [])[idx] || "UNKNOWN";
        total[projectType] = (total[projectType] || 0) + (Number(role.weighted_months || 0));
      });
      return Object.entries(total)
        .sort((a,b) => b[1] - a[1])
        .map(([kind, months]) => `${kind.replaceAll("_"," ")} (${(months/12).toFixed(1)} yrs weighted)`)
        .join(", ");
    }
    function formatAttributedRoles(skill){
      const roles = skill.evidence_roles || [];
      if(!roles.length) return "N/A";
      return roles
        .map(role => `${esc(role.title)} @ ${esc(role.company)} (${esc(role.start_date)} - ${esc(role.end_date)})`)
        .join(" | ");
    }
    async function submitRecruiterScores(){
      const candidateId = _analysisData?.candidate_id;
      if(!candidateId){document.getElementById("recruiterSubmitStatus").innerText="No candidate ID found. Re-analyze the resume first.";return;}
      const params=["mentorship_signal","international_exposure","stakeholder_management","project_explanation","linkedin_activity","coding_community"];
      const overrides={};
      for(const k of params){
        const el=document.getElementById("ri_"+k);
        if(el&&el.value!=="") overrides[k]=parseFloat(el.value)||0;
      }
      if(!Object.keys(overrides).length){document.getElementById("recruiterSubmitStatus").innerText="Fill in at least one score.";return;}
      document.getElementById("recruiterSubmitStatus").innerText="Submitting...";
      try{
        const res=await fetch("/updateStageScore",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({candidate_id:candidateId,stage:"recruiter",stage_overrides:overrides,recruiter_notes:""})});
        const d=await res.json();
        if(!res.ok){document.getElementById("recruiterSubmitStatus").innerText="Error: "+(d.detail||res.statusText);return;}
        const s100=d.stage_score_100??d.new_total;
        document.getElementById("recruiterSubmitStatus").innerText=`✓ Saved. Recruiter score: ${s100}/100`;
        document.getElementById("recruiterScoreBadge").innerHTML=`${s100}<span style="font-size:14px;color:var(--muted)"> / 100</span>`;
        // Update main score card to recruiter-stage /100
        document.getElementById("score").innerText=s100;
        document.getElementById("scoreRawNote").innerHTML=`<span style="color:var(--warn);font-size:11px">Recruiter stage (raw ${d.new_total}/${d.stage_scores?.recruiter_max??87} pts)</span>`;
        // Update the stage score card too
        if(d.stage_scores){
          const ss2=d.stage_scores;
          if(ss2.recruiter_score_100!==undefined){
            // re-render recruiter card value
            document.querySelectorAll("#stageScoreContent .kicker").forEach(el=>{
              if(el.innerText==="RECRUITER STAGE"){
                el.nextElementSibling.innerHTML=`${ss2.recruiter_score_100}<span style="font-size:14px;color:var(--muted)"> / 100</span>`;
              }
            });
          }
        }
      }catch(e){document.getElementById("recruiterSubmitStatus").innerText="Error: "+e;}
    }
    async function submitPanelScores(){
      const candidateId = _analysisData?.candidate_id;
      if(!candidateId){document.getElementById("panelSubmitStatus").innerText="No candidate ID found. Re-analyze the resume first.";return;}
      const numericParams=["communication_skills","domain_skills","problem_solving"];
      const overrides={};
      for(const k of numericParams){
        const el=document.getElementById("pi_"+k);
        if(el&&el.value!=="") overrides[k]=parseFloat(el.value)||0;
      }
      // Qualitative text fields
      const codingEl=document.getElementById("pi_coding_skills");
      const conceptEl=document.getElementById("pi_conceptual_skills");
      if(codingEl&&codingEl.value.trim()) overrides["coding_skills"]=codingEl.value.trim();
      if(conceptEl&&conceptEl.value.trim()) overrides["conceptual_skills"]=conceptEl.value.trim();
      if(!Object.keys(overrides).length){document.getElementById("panelSubmitStatus").innerText="Fill in at least one score.";return;}
      document.getElementById("panelSubmitStatus").innerText="Submitting...";
      try{
        const res=await fetch("/updateStageScore",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({candidate_id:candidateId,stage:"panel",stage_overrides:overrides,recruiter_notes:""})});
        const d=await res.json();
        if(!res.ok){document.getElementById("panelSubmitStatus").innerText="Error: "+(d.detail||res.statusText);return;}
        const s100=d.stage_score_100??d.new_total;
        document.getElementById("panelSubmitStatus").innerText=`✓ Saved. Final panel score: ${s100}/100`;
        document.getElementById("panelScoreBadge").innerHTML=`${s100}<span style="font-size:14px;color:var(--muted)"> / 100</span>`;
        // Update main score card to panel-stage (final) /100
        document.getElementById("score").innerText=s100;
        document.getElementById("scoreMode").innerText="/ 100";
        document.getElementById("scoreRawNote").innerHTML=`<span style="color:var(--primary2);font-size:11px">Final panel score (${d.new_total}/100 pts)</span>`;
        // Update stage score card panel column
        if(d.stage_scores){
          const ss2=d.stage_scores;
          if(ss2.panel_score_100!==undefined){
            document.querySelectorAll("#stageScoreContent .kicker").forEach(el=>{
              if(el.innerText==="PANEL STAGE"){
                el.nextElementSibling.innerHTML=`${ss2.panel_score_100}<span style="font-size:14px;color:var(--muted)"> / 100</span>`;
              }
            });
          }
        }
      }catch(e){document.getElementById("panelSubmitStatus").innerText="Error: "+e;}
    }
    async function upload(){
      const file=document.getElementById("file").files[0];
      const status=document.getElementById("status");
      if(!file){status.innerText="Please select a JSON file.";return;}
      const fd=new FormData(); fd.append("file", file); status.innerText="Analyzing...";
      const res=await fetch("/resumeParse",{method:"POST",body:fd}); const data=await res.json();
      _analysisData = data;
      document.getElementById("results").style.display="block";
      const _cv=v=>(v&&v!=="N/A"&&v!=="NA"&&!v.includes("/")?v:null);
      const _cid=encodeURIComponent(_cv(data.candidate_id)||_cv(data.candidate_overview?.email)||_cv((data.candidate_overview?.name||"").replace(/\s+/g,"_"))||"");
      if(_cid){document.getElementById("linkRecruiterScreen").href="/recruiter-screen/"+_cid;document.getElementById("linkPanelScreen").href="/panel-screen/"+_cid;}
      const _cn=data.candidate_name||data.candidate_id||"";if(_cn)document.getElementById("heroName").textContent=_cn;
      if(!res.ok){status.innerText="Failed."; document.getElementById("raw").innerText=JSON.stringify(data,null,2); return;}
      const _t=data._timing||{};
      const _elapsed=_t.elapsed_ms!=null?((_t.elapsed_ms/1000).toFixed(1)+'s'):'—';
      const _at=_t.analyzed_at?new Date(_t.analyzed_at).toLocaleString():'';
      status.innerHTML=`Analysis complete. <span style="color:var(--text2);font-size:12px">Started: ${_at} &bull; Duration: ${_elapsed}</span>`;
      const rs=data.rubric_scorecard||{};
      const rubricReady=!!rs.total_score;
      const ss0=rs.stage_scores||{};
      // Show resume_score_100 (normalized) in the main score card; raw score as sub-note
      const score=rubricReady ? (ss0.resume_score_100 ?? rs.total_score) : (data.scorecard?.total_score ?? 0);
      const rawScore=rubricReady ? rs.total_score : 0;
      const aiScoreReady = !!data.scorecard?.llm_used;
      document.getElementById("score").innerText=esc(score);
      document.getElementById("scoreMode").innerText="/ 100";
      if(rubricReady && ss0.resume_score_100!==undefined){
        document.getElementById("scoreRawNote").innerHTML=`<span style="color:var(--muted);font-size:11px">Resume stage only (raw ${rawScore}/${ss0.resume_max} pts)</span>`;
      }
      document.getElementById("scoreBar").style.width=`${Math.max(0,Math.min(100,score))}%`;
      document.getElementById("band").innerText=aiScoreReady ? esc(data.scorecard?.band) : "—";
      document.getElementById("bandPill").innerText=aiScoreReady ? esc(data.scorecard?.band) : "—";
      if(rubricReady){
        document.getElementById("rubricExp").innerText=rs.experience_score??"-";
        document.getElementById("rubricExpBar").style.cssText=`height:100%;width:${Math.round((rs.experience_score/40)*100)}%;background:linear-gradient(90deg,var(--accent),var(--warn))`;
        document.getElementById("rubricSkills").innerText=rs.skills_score??"-";
        document.getElementById("rubricSkillsBar").style.cssText=`height:100%;width:${Math.round((rs.skills_score/45)*100)}%;background:linear-gradient(90deg,var(--accent),var(--warn))`;
        document.getElementById("rubricEdu").innerText=rs.education_score??"-";
        document.getElementById("rubricEduBar").style.cssText=`height:100%;width:${Math.round((rs.education_score/15)*100)}%;background:linear-gradient(90deg,var(--accent),var(--warn))`;
        {const eb=rs.breakdown?.education||{};let cs=0,bs=0;for(const[k,v]of Object.entries(eb)){if(!v||typeof v!=='object')continue;if(k==='bonus'){for(const bv of Object.values(v)){if(bv&&bv.score!=null)bs+=bv.score;}}else if(v.score!=null){cs+=v.score;}}document.getElementById('rubricEduCore').innerText=Math.round(cs*10)/10;document.getElementById('rubricEduBonus').innerText=Math.round(bs*10)/10;}
        document.getElementById("rejectFlags").innerText=(rs.reject_flags||[]).length ? rs.reject_flags.join(" | ") : "None";
        const ss=ss0;
        if(ss.skills_recruiter_pending_pts) document.getElementById("skillsRecruiterPending").innerText=`+${ss.skills_recruiter_pending_pts} recruiter`;
        if(ss.panel_can_add) document.getElementById("skillsPanelPending").innerText=`+${ss.panel_can_add} panel`;
        if(ss.edu_recruiter_pending_pts) document.getElementById("eduRecruiterPending").innerText=`+${ss.edu_recruiter_pending_pts} recruiter`;
      }
      document.getElementById("role").innerText=esc(data.semantic_analysis?.top_role_family)?.replaceAll("_"," ");
      document.getElementById("dna").innerText=esc(data.dna_fit?.primary_dna);
      const dnaMetaText = data.dna_fit?.llm_reason
        ? `AI confidence=${esc(data.dna_fit?.llm_confidence || "MEDIUM")} | ${esc(data.dna_fit?.llm_reason)}`
        : (data.llm_status?.dna_judgment_reason || "");
      document.getElementById("dnaMeta").innerText=dnaMetaText;
      const ov=data.candidate_overview||{};
      document.getElementById("overview").innerHTML=`<div><b>Name</b><br>${esc(ov.name)}</div><div><b>Email</b><br>${esc(ov.email)}</div><div><b>Phone</b><br>${esc(ov.phone)}</div><div><b>Location</b><br>${esc(ov.location)}</div><div style="grid-column:1/-1"><b>Profile Summary</b><br><span class="muted">${esc(ov.profile_summary)}</span></div>`;
      document.getElementById("summary").innerText=esc(data.recruiter_summary);
      const sc=data.scorecard||{};
      const scoreHelp = {
        "Skill Strength":"Judged on how credible, relevant, and seniority-appropriate the candidate's technical evidence looks for this resume.",
        "Experience":"Judged on scope, ownership, complexity, leadership maturity, and whether the experience reads like the claimed seniority.",
        "Role Alignment":"Judged on how naturally the resume fits the most plausible role family, not just keyword overlap.",
        "Impact":"Judged on visible business value, delivery outcomes, and whether the resume shows more than technical activity.",
        "Stability":"Judged on trajectory, consistency, growth, and whether the career path feels dependable for the target role.",
        "DNA":"Derived from the resume’s operating style signal such as consulting, product, hybrid, or domain-specialist orientation.",
        "Avg Skill Strength":"Support metric only. Helpful for internal context, but not the final recruiter judgment."
      };
      const maxScores = sc.max_scores || {};
      const componentRationales = sc.component_rationales || {};
      const componentJustifications = sc.component_justifications || {};
      const justificationNotes = sc.justification_notes || {};
      const rationaleKeyMap = {"Skill Strength":"skill_score","Experience":"experience_score","Role Alignment":"role_alignment_score","Impact":"impact_score","Stability":"stability_score","DNA":"dna_score"};
      const componentInputs = sc.component_inputs || {};
      const bandMeta = sc.experience_band || {};
      const benchmarkHeader = sc.llm_used ? "LLM Judgment" : "System Fallback";
      const benchmarkHelp = sc.llm_used
        ? `AI judged this resume against the ${esc(bandMeta.label)} experience stage using evidence quality, ownership, scope, and business impact.`
        : `AI scoring was unavailable for this run, so this is a temporary system read for a ${esc(bandMeta.label)} profile.`;
      const modeMeta = sc.llm_used
        ? `experience_stage=${esc(bandMeta.label)} | provider=${esc(sc.llm_provider)}`
        : `experience_stage=${esc(bandMeta.label)} | mode=fallback | provider=${esc(sc.llm_provider)}`;
      const benchmarkNarrative = sc.llm_used
        ? esc(sc.benchmark_summary || sc.benchmark_definition)
        : esc(sc.benchmark_definition || "Use this only as a directional read until AI judgment is available.");
      const failureReason = !sc.llm_used && sc.llm_failure_reason ? `<div class="score-inputs">AI reason: ${esc(sc.llm_failure_reason)}</div>` : "";
      const dimensionRatings = sc.dimension_ratings || {};
      const dimensionConfidence = sc.dimension_confidence || {};
      const normalizationMeta = sc.score_normalization?.explanation ? `<div class="score-inputs">${esc(sc.score_normalization.explanation)}</div>` : "";
      const scoreMetaTop = `<div class="score-row"><div class="score-meta"><b>${benchmarkHeader}</b><div class="score-help">${benchmarkHelp}</div><div class="score-inputs">${modeMeta}</div><div class="score-inputs">${benchmarkNarrative}</div>${normalizationMeta}${failureReason}${sc.rationale ? `<div class="score-inputs">${esc(sc.rationale)}</div>` : ""}</div><span>${esc(bandMeta.label)}</span></div>`;
      const scoreRows = [
        ["Skill Strength",sc.skill_score,maxScores.skill_score],["Experience",sc.experience_score,maxScores.experience_score],["Role Alignment",sc.role_alignment_score,maxScores.role_alignment_score],["Impact",sc.impact_score,maxScores.impact_score],["Stability",sc.stability_score,maxScores.stability_score],["DNA",sc.dna_score,maxScores.dna_score]
      ].map(([k,v,max])=>{
        const dimensionMap = {
          "Skill Strength":"skill_strength_0_to_5",
          "Experience":"experience_depth_0_to_5",
          "Role Alignment":"role_alignment_0_to_5",
          "Impact":"business_impact_0_to_5",
          "Stability":"career_stability_0_to_5",
          "DNA":"dna_fit_0_to_5"
        };
        const confidenceMap = {
          "Skill Strength":"skill_strength",
          "Experience":"experience_depth",
          "Role Alignment":"role_alignment",
          "Impact":"business_impact",
          "Stability":"career_stability",
          "DNA":"dna_fit"
        };
        const aiRange = dimensionRatings[dimensionMap[k]];
        const aiConfidence = dimensionConfidence[confidenceMap[k]];
        const aiNote = aiRange !== undefined && aiRange !== null ? `<div class="score-inputs">AI range judgment: ${esc(aiRange)} / 5${aiConfidence ? ` | confidence=${esc(aiConfidence)}` : ""}</div>` : "";
        const notes = justificationNotes[rationaleKeyMap[k]] || [];
        const noteHtml = notes.length ? `<div class="score-inputs"><b>Justification notes:</b> ${notes.map(esc).join(" | ")}</div>` : "";
        const justification = componentJustifications[rationaleKeyMap[k]] || {};
        const strongestEvidence = justification.strongest_evidence ? `<div class="score-inputs"><b>Strongest evidence:</b> ${esc(justification.strongest_evidence)}</div>` : "";
        const mainGap = justification.main_gap ? `<div class="score-inputs"><b>Main gap:</b> ${esc(justification.main_gap)}</div>` : "";
        const whyNotLower = justification.why_not_lower ? `<div class="score-inputs"><b>Why not lower:</b> ${esc(justification.why_not_lower)}</div>` : "";
        return `<details class="detail-card"><summary><div class="score-meta"><b>${k}</b><div class="score-help">${scoreHelp[k] || ""}</div><div class="score-inputs">${esc(componentRationales[rationaleKeyMap[k]] || "Judgment rationale unavailable.")}</div></div><div class="detail-score">${max ? `${esc(v)} / ${esc(max)}` : esc(v)}</div></summary><div class="detail-body">${aiNote}${strongestEvidence}${mainGap}${whyNotLower}${noteHtml}</div></details>`;
      }).join("");
      const fallbackNote = !sc.llm_used ? `<div class="semantic-block"><div class="semantic-title">Recruiter View</div><div class="semantic-note" style="margin-top:6px">Final hiring judgment is withheld until AI scoring succeeds. The system fallback remains available in the analysis output JSON for debugging, but it is not being presented here as a final recruiter score.</div></div>` : "";
      document.getElementById("scorebreakdown").innerHTML=scoreMetaTop + fallbackNote + (sc.llm_used ? `<div class="tile-grid">${scoreRows}</div>` : "");
      // Render new rubric scorecard
      const rubricBd=rs.breakdown||{};
      const PARAM_LABELS = {
        // Experience
        "overall_experience":      "Overall / Relevant Experience",
        "career_breaks":           "Career Breaks",
        "career_progression":      "Career Progression",
        "stability":               "Stability",
        "company_tier":            "Companies Worked With",
        "awards_recognition":      "Awards & Recognitions",
        "mentorship_signal":       "Mentorship / Code Reviews / Interviews",
        "international_exposure":  "International Exposure",
        "stakeholder_management":  "Stakeholder Management",
        "project_1":               "Project 1 — Latest Project",
        "project_2":               "Project 2 — 2nd Latest Project",
        // Skills
        "skill_list_years":        "Skill List — Years of Experience",
        "skill_depth":             "Skill Depth",
        "skill_recency":           "Skill Recency",
        "skills_learning_acumen":  "Skills Learning Acumen",
        "certifications":          "Certifications",
        "coding_community":        "Coding Platforms / Community",
        "project_explanation":     "Project Explanation Skills",
        "communication_skills":    "Communication & Presentation Skills",
        "domain_skills":           "Domain Skills",
        "problem_solving":         "Problem Solving Skills",
        "coding_skills":           "Coding Skills",
        "conceptual_skills":       "Conceptual Skills",
        "mandatory_skills":        "Mandatory Skills (JD Match)",
        "good_to_have_skills":     "Good to Have Skills (JD Match)",
        // Education core
        "institute_tier":          "Institute — Tier, GPA, Stream",
        "degree_level":            "Highest Education & Stream",
        "education_job_relevance": "Education to Job Relevance",
        "education_gap":           "Education Gaps",
        // Education bonus
        "bonus.exec_education":       "Executive / Distance Education",
        "bonus.patents_publications": "Patents / Publications",
        "bonus.linkedin_activity":    "LinkedIn / Social Media Activeness",
        "bonus.extra_curriculars":    "Extra Curricular Activities",
      };
      function paramLabel(k){
        return PARAM_LABELS[k] || k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());
      }
      function stageChip(stage){
        if(!stage) return '';
        const cfg={resume:['#DCFCE7','#16A34A'],recruiter:['#FEF3C7','#D97706'],panel:['#F0F0FB','#6366F1']};
        const [bg,fg]=cfg[stage]||['#DCFCE7','#16A34A'];
        return `<span style="font-size:10px;font-weight:700;text-transform:uppercase;background:${bg};color:${fg};border-radius:999px;padding:1px 7px;margin-left:6px;vertical-align:middle">${stage}</span>`;
      }
      function renderFlagCard(k,v){
        const chips=(arr,bg,fg)=>arr.map(s=>`<span style="display:inline-block;background:${bg};color:${fg};border-radius:999px;padding:2px 9px;font-size:11px;font-weight:600;margin:2px">${esc(s)}</span>`).join('');
        const matchChips=chips(v.matched||[],'#DCFCE7','#16A34A');
        const missChips=chips(v.missing||[],'#FEE2E2','#DC2626');
        const hasChips=(v.matched||[]).length+(v.missing||[]).length>0;
        if(!hasChips) return '';
        return `<div class="detail-card" style="padding:12px 14px;margin-bottom:6px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <b>${paramLabel(k)}</b>
            <span style="font-size:11px;font-weight:700;color:var(--muted)">${v.match_rate||''}</span>
          </div>
          <div>${matchChips}${missChips}</div>
        </div>`;
      }
      function renderPanelTextCard(k,v){
        const filled=v.value&&v.value.trim();
        return `<div class="detail-card" style="padding:12px 14px;margin-bottom:6px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b>${paramLabel(k)}</b>${stageChip('panel')}
          </div>
          <div class="muted" style="margin-top:6px;font-size:12px">${filled?esc(v.value):esc(v.note||'Panel fill required')}</div>
        </div>`;
      }
      function rubricSection(label,sectionBd,sectionScore,maxPts){
        if(!sectionBd) return '';
        const rows=Object.entries(sectionBd).map(([k,v])=>{
          if(!v||typeof v!=='object') return '';
          if(v.type==='flag') return renderFlagCard(k,v);
          if(v.type==='panel_text') return renderPanelTextCard(k,v);
          if(!('score' in v)) return '';
          const pct=v.max>0?Math.round((v.score/v.max)*100):0;
          const barColor=pct>=75?'var(--accent)':pct>=40?'var(--warn)':'#e05c5c';
          const pending=v.score===0&&v.stage&&v.stage!=='resume'?` <span style="font-size:10px;color:var(--muted)">(pending ${v.stage})</span>`:'';
          return `<details class="detail-card"><summary style="display:flex;justify-content:space-between;align-items:center;padding:12px 14px;gap:12px"><div class="score-meta"><b>${paramLabel(k)}</b>${stageChip(v.stage)}${pending}<div class="score-inputs" style="margin-top:4px">${esc(v.reason)}</div></div><div class="detail-score" style="white-space:nowrap;min-width:60px;text-align:right">${v.score} / ${v.max}</div></summary><div class="detail-body"><div class="progress" style="margin:4px 0 8px"><div style="height:100%;width:${pct}%;background:${barColor}"></div></div></div></details>`;
        }).join('');
        const pct=Math.round((sectionScore/maxPts)*100);
        return `<div style="margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><h3 style="margin:0;font-size:16px">${label}</h3><span style="font-size:22px;font-weight:800;color:var(--accent)">${sectionScore} <span style="font-size:14px;color:var(--muted)">/ ${maxPts}</span></span></div><div class="progress" style="margin-bottom:10px"><div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--accent),var(--warn))"></div></div><div class="tile-grid">${rows}</div></div>`;
      }
      if(rubricReady){
        document.getElementById("rubricTotal").innerText=`— ${rs.total_score} / 100`;
        // Education bonus expansion: merge bonus sub-params into flat list for rendering
        function flattenEduBd(eduBd){
          const out={};
          for(const [k,v] of Object.entries(eduBd||{})){
            if(k==='bonus'&&v&&typeof v==='object'&&!('score' in v)){
              for(const [bk,bv] of Object.entries(v)){out['bonus.'+bk]=bv;}
            } else { out[k]=v; }
          }
          return out;
        }
        document.getElementById("rubricScorecard").innerHTML=
          rubricSection('Experience (40 pts)',rubricBd.experience,rs.experience_score,40)+
          rubricSection('Skills (45 pts)',rubricBd.skills,rs.skills_score,45)+
          rubricSection('Education (15 pts)',flattenEduBd(rubricBd.education),rs.education_score,15);
        document.getElementById("legacyScoreDetails").style.display='';
        // Stage score breakdown
        if(ss0.resume_score!==undefined){
          document.getElementById("stageScoreCard").style.display='';
          const resumePct=Math.round((ss0.resume_score/ss0.full_score_potential)*100);
          const recPct=Math.round((ss0.recruiter_can_add/ss0.full_score_potential)*100);
          const panelPct=Math.round((ss0.panel_can_add/ss0.full_score_potential)*100);
          document.getElementById("stageScoreContent").innerHTML=`
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px">
              <div style="background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px">
                <div class="kicker">Resume Stage</div>
                <div class="metric" style="font-size:30px;font-weight:800;color:#16A34A">${ss0.resume_score_100}<span style="font-size:14px;color:var(--text2)"> / 100</span></div>
                <div class="score-inputs" style="margin-top:4px">Auto-scored from resume. <span style="color:var(--text2);font-size:11px">(${ss0.resume_score}/${ss0.resume_max} raw pts)</span></div>
              </div>
              <div style="background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px">
                <div class="kicker">Recruiter Stage</div>
                <div class="metric" style="font-size:30px;font-weight:800;color:var(--amber)">${ss0.recruiter_score_100 !== undefined ? ss0.recruiter_score_100+' <span style=\\"font-size:14px;color:var(--text2)\\"> / 100</span>' : '<span style=\\"font-size:14px;color:var(--text2)\\">+'+ss0.recruiter_can_add+' pts pending</span>'}</div>
                <div class="score-inputs" style="margin-top:4px">Params: ${(ss0.recruiter_pending_params||[]).map(p=>p.replace(/_/g,' ')).join(', ')}.</div>
              </div>
              <div style="background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px">
                <div class="kicker">Panel Stage</div>
                <div class="metric" style="font-size:30px;font-weight:800;color:var(--primary2)">${ss0.panel_score_100 !== undefined ? ss0.panel_score_100+' <span style=\\"font-size:14px;color:var(--text2)\\"> / 100</span>' : '<span style=\\"font-size:14px;color:var(--text2)\\">+'+ss0.panel_can_add+' pts pending</span>'}</div>
                <div class="score-inputs" style="margin-top:4px">Params: ${(ss0.panel_pending_params||[]).map(p=>p.replace(/_/g,' ')).join(', ')}.</div>
              </div>
            </div>
            <div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:4px;display:flex;gap:0;overflow:hidden">
              <div style="width:${resumePct}%;background:#16A34A;height:22px;border-radius:6px 0 0 6px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;min-width:30px">${resumePct}%</div>
              <div style="width:${recPct}%;background:var(--amber);height:22px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;min-width:${recPct>0?'20px':'0'}">${recPct>0?recPct+'%':''}</div>
              <div style="width:${panelPct}%;background:var(--primary2);height:22px;border-radius:0 6px 6px 0;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;min-width:${panelPct>0?'30px':'0'}">${panelPct>0?panelPct+'%':''}</div>
            </div>
            <div style="margin-top:6px;display:flex;gap:16px;font-size:11px;color:var(--text2)">
              <span style="color:#16A34A">&#9632; Resume (${ss0.resume_score_100}/100)</span>
              <span style="color:var(--amber)">&#9632; Recruiter ${ss0.recruiter_score_100 !== undefined ? '('+ss0.recruiter_score_100+'/100)' : '(+'+ss0.recruiter_can_add+' pending)'}</span>
              <span style="color:var(--primary2)">&#9632; Panel ${ss0.panel_score_100 !== undefined ? '('+ss0.panel_score_100+'/100)' : '(+'+ss0.panel_can_add+' pending)'}</span>
            </div>`;
        }
        // Recruiter intake form
        document.getElementById("recruiterIntake").style.display='';
        // Helper: look up a param's current scored entry from rubric breakdown
        function _getParamData(key) {
          const ro = _analysisData?.rubric_output;
          if (!ro) return null;
          const bd = ro.breakdown || {};
          return bd.experience?.[key] || bd.skills?.[key] || bd.education?.bonus?.[key] || null;
        }
        const RECRUITER_PARAMS = [
          {key:"mentorship_signal", label:"Mentorship / Code Reviews / Interviews", max:3, help:"3=clear lead/mentor role with team; 2=evidence of code reviews or junior guidance; 1=implied; 0=pure IC. Resume context shown in rubric.", guide:"Ask: Have you mentored junior engineers? Do you conduct code reviews? Have you interviewed candidates?"},
          {key:"international_exposure", label:"International Exposure", max:2, help:"2=onsite abroad or sustained global team work (E16: auto-scored 1.5 when resume signal detected); 1=cross-timezone coordination; 0=purely local.", guide:"Ask: Have you worked with global teams or clients? Any onsite stints outside India? Validate and upgrade to 2 if confirmed."},
          {key:"stakeholder_management", label:"Stakeholder Management", max:2, help:"2=client-facing or C-level interactions; 1=cross-functional internal; 0=no stakeholder management. Resume context shown in rubric.", guide:"Ask: Who did you work with outside your immediate team? Any client or business stakeholder interactions?"},
          {key:"project_explanation", label:"Project Explanation Skills", max:3, help:"3=structured narrative (problem → design → outcome → learnings); 2=good; 1=gaps; 0=cannot explain.", guide:"Ask: Pick your most complex project — walk me through the problem, your role, the decisions you made, and the outcome."},
          {key:"linkedin_activity", label:"LinkedIn / Social Media Activeness", max:1, help:"1=active professional presence (recent posts, endorsements, complete profile consistent with resume); 0=absent or inactive.", guide:"Check LinkedIn before/during call. 1pt if profile is active and consistent with resume."},
          {key:"coding_community", label:"Coding Platforms / Community", max:4, help:"4=active competitive coder (contest ratings, 200+ problems solved, hackathon prize); 3=explicit platform profile links detected (GitHub/LeetCode/HackerRank); 2=platform names mentioned in resume (E17: auto-scored); 1=OSS/open-source signals only; 0=none.", guide:"Ask: What is your LeetCode/HackerRank username? Share your profile — let's check contest ratings and problem count. Any hackathon prizes or open-source projects?"},
        ];
        document.getElementById("recruiterIntakeRows").innerHTML = RECRUITER_PARAMS.map(p=>{
          const pd = _getParamData(p.key);
          const curScore = (pd && pd.score != null && pd.score !== undefined) ? pd.score : "";
          const isAuto = pd && pd.score > 0 && (pd.resume_signal === true || pd.stage === "resume");
          const autoBadge = isAuto
            ? `<span style="font-size:10px;background:#DCFCE7;color:#16A34A;border-radius:6px;padding:2px 7px;font-weight:700;margin-left:8px">AUTO-SCORED</span>`
            : "";
          const curNote = (curScore !== "" && curScore > 0)
            ? `<span style="font-size:11px;color:var(--text2);margin-left:8px">Current: <b style="color:${isAuto?'#16A34A':'var(--text)'}">${curScore}</b> / ${p.max}</span>`
            : "";
          return `
          <div style="background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">
              <b style="font-size:13px">${p.label}${autoBadge}</b>
              <span style="font-size:11px;color:var(--amber);font-weight:700">max ${p.max}</span>
            </div>
            <div class="score-inputs" style="margin-bottom:8px">${p.help}</div>
            <div style="background:var(--amber-light);border-radius:8px;padding:8px 10px;font-size:11px;color:var(--text2);border-left:2px solid var(--amber);margin-bottom:10px">${p.guide}</div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span style="font-size:12px;color:var(--text2)">Score:</span>
              <input type="number" id="ri_${p.key}" min="0" max="${p.max}" step="${p.max===1?1:0.5}" value="${curScore}" placeholder="0–${p.max}"
                style="width:80px;background:var(--bg);border:1px solid ${isAuto?'#16A34A':'var(--border)'};border-radius:8px;color:var(--text);padding:6px 10px;font-size:14px;font-weight:700">
              <span style="font-size:11px;color:var(--text2)">/ ${p.max}</span>
              ${curNote}
            </div>
          </div>`;
        }).join('');
      } else {
        document.getElementById("rubricTotal").innerText='';
        document.getElementById("rubricScorecard").innerHTML='<div class="muted" style="padding:12px 0">Rubric not available. Restart server with ENABLE_NEW_RUBRIC=true to activate.</div>';
        document.getElementById("legacyScoreDetails").style.display='none';
      }
      const skills=(data.skill_analysis?.top_skills||[]).slice(0,12);
      const skillStatusNote = data.llm_status?.skill_judgment_reason ? `<div class="semantic-block"><div class="semantic-title">Skill Judgment Mode</div><div class="semantic-note" style="margin-top:6px">${esc(data.llm_status.skill_judgment_reason)}</div></div>` : "";
      document.getElementById("skills").innerHTML=skillStatusNote + skills.map(s=>`<div class="skill-card"><div><b>${s.skill}</b> <span class="band">${s.judged_strength_label || s.depth_label || s.evidence_level}</span></div><div class="muted" style="margin-top:6px">Evidence: ${s.evidence_level} | Recruiter judgment: ${esc(s.judged_strength_label || s.depth_label || s.evidence_level)}${s.judged_score_0_to_5 !== undefined && s.judged_score_0_to_5 !== null ? ` | AI score: ${esc(s.judged_score_0_to_5)} / 5` : ""} | Confidence: ${esc(s.judged_confidence || (data.llm_analysis_used ? "LLM" : "Fallback"))} | Weighted evidence tenure: ${esc(s.years_of_usage)} yrs | Raw matched tenure: ${esc(s.raw_years_of_usage)} yrs | Recency: ${esc(s.recency)} | Contexts: ${esc(s.matched_context_count)}</div><div class="muted" style="margin-top:6px">${esc(s.judged_reason || "No recruiter-style rationale was generated for this skill.")}</div>${(s.judged_evidence_used||[]).length ? `<div class="muted" style="margin-top:6px"><b>Evidence used:</b> ${s.judged_evidence_used.map(esc).join("; ")}</div>` : ""}<div class="muted" style="margin-top:6px"><b>Project context:</b> ${esc(formatProjectMix(s))}</div>${s.interview_probe ? `<div class="muted" style="margin-top:6px"><b>Interview probe:</b> ${esc(s.interview_probe)}</div>` : ""}${(s.evidence_roles||[]).length ? `<div class="muted" style="margin-top:8px"><b>Attributed roles:</b> ${esc(formatAttributedRoles(s))}</div>` : ""}</div>`).join("");
      const sem=data.semantic_analysis||{};
      const allSkills=Object.values(data.skill_analysis?.all_skills || {});
      const consistencyPct=Math.round((sem.skill_consistency_score || 0) * 100);
      const clusterMap=sem.cluster_map || {};
      const semanticHtml = Object.entries(clusterMap).map(([cluster, skills]) => {
        const strongSkills = skills.filter(skill => {
          const meta = allSkills.find(x => x.skill === skill);
          return meta && ["APPLIED","DEEP","EXPERT"].includes(meta.evidence_level);
        });
        const weakSkills = skills.filter(skill => {
          const meta = allSkills.find(x => x.skill === skill);
          return meta && ["MENTION","WEAK"].includes(meta.evidence_level);
        });
        return `<div class="semantic-block">
          <div class="semantic-head">
            <div><div class="semantic-title">${cluster}</div><div class="semantic-note">This cluster groups related skills so a recruiter can quickly see where the resume is strongest.</div></div>
            <div class="band">${skills.length} skills</div>
          </div>
          <div class="semantic-grid">
            <div class="semantic-col">
              <h4>Evidence-backed skills</h4>
              ${strongSkills.length ? pills(strongSkills) : '<div class="semantic-empty">No strongly evidenced skills detected in this cluster.</div>'}
            </div>
            <div class="semantic-col">
              <h4>Mentioned but needs validation</h4>
              ${weakSkills.length ? pills(weakSkills) : '<div class="semantic-empty">No weak-only mentions in this cluster.</div>'}
            </div>
          </div>
        </div>`;
      }).join("");
      document.getElementById("semantic").innerHTML=`${sem.role_family_rationale ? `<div class="semantic-block"><div class="semantic-title">Role Fit Interpretation</div><div class="semantic-note" style="margin-top:6px">${esc(sem.role_family_rationale)}</div></div>` : ""}${sem.consistency_readout ? `<div class="semantic-block"><div class="semantic-title">Consistency Interpretation</div><div class="semantic-note" style="margin-top:6px">${esc(sem.consistency_readout)}</div></div>` : ""}<div class="score-row"><div class="score-meta"><b>Consistency Score</b><div class="score-help">How much of the detected skill footprint is backed by meaningful evidence. Higher means the resume is more consistent between claimed skills and delivery proof.</div><div class="score-inputs">${consistencyPct}% of detected skills have APPLIED, DEEP, or EXPERT evidence.</div></div><span>${esc(sem.skill_consistency_score)} / 1.00</span></div><div style="margin-top:10px"><b>Inferred Strength Areas</b></div><div>${sem.inferred_strength_areas?.length ? pills(sem.inferred_strength_areas) : sem.inferred_skills?.length ? pills(sem.inferred_skills) : '<span class="semantic-empty">No inferred composite strengths surfaced.</span>'}</div><div style="margin-top:14px"><b>Cluster Evidence View</b></div><div class="score-help">A cluster is a broader capability area like programming, ML, GenAI, cloud, or MLOps. Skills shown on the left are backed by stronger evidence. Skills on the right appear in the resume but still need interview validation.</div>${semanticHtml || '<div class="semantic-empty" style="margin-top:12px">No clustered skill evidence was generated.</div>'}`;
      const ex=data.experience_analysis||{};
      document.getElementById("experience").innerHTML=`<div class="score-row"><span>Total Experience</span><span>${esc(ex.total_experience_years)} years</span></div><div class="score-row"><span>Progression</span><span>${boolLabel(ex.progression)}</span></div><div class="score-row"><span>Same Company Growth</span><span>${boolLabel(ex.same_company_growth)}</span></div><div class="score-row"><span>Mobility</span><span>${esc(ex.mobility_signal)}</span></div><div class="score-row"><span>Loyalty</span><span>${esc(ex.loyalty_signal)}</span></div><div class="score-row"><span>Avg Tenure</span><span>${esc(ex.average_tenure_months)} months</span></div><div class="score-row"><span>Client Facing</span><span>${boolLabel(ex.client_facing)}</span></div><div class="score-row"><span>International Exposure</span><span>${boolLabel(ex.international_exposure)}</span></div><div class="score-row"><span>Decision Maker</span><span>${boolLabel(ex.decision_maker)}</span></div><div class="score-row"><span>Fast Learner</span><span>${boolLabel(ex.fast_learner)}</span></div><div class="score-row"><span>Complexity Signal</span><span>${esc(ex.complexity_signal_score)}</span></div><div class="score-row"><span>Leadership Signal</span><span>${esc(ex.leadership_signal_score)}</span></div><div class="score-row"><span>Problem Solving Signal</span><span>${esc(ex.problem_solving_signal_score)}</span></div><div style="margin-top:12px"><b>Project Mix</b><div class="muted">${(ex.project_types||[]).map(p => `${esc(p.title)} (${esc(p.start_date)} - ${esc(p.end_date)}): ${esc(p.project_type)}`).join("<br>") || "N/A"}</div></div><div style="margin-top:12px"><b>Yearly Skill Learning</b><div class="muted">${(ex.yearly_skill_learning||[]).map(row => `${esc(row.year)}: +${esc(row.new_skill_count)} new skills${row.new_skills?.length ? ` (${row.new_skills.join(", ")})` : ""}`).join("<br>") || "N/A"}</div></div><div style="margin-top:12px"><b>Company Context</b><div class="muted">${(ex.company_profiles||[]).map(p => `${esc(p.company)} (${esc(p.operating_model)}, ${esc(p.size)}, ${esc(p.domain)})`).join("<br>") || "N/A"}</div></div><div style="margin-top:12px"><b>Company-Skill Alignment</b><div class="muted">${(ex.company_skill_alignment||[]).map(p => `${esc(p.company)}: ${esc(p.alignment)}${p.skills?.length ? ` | ${p.skills.join(", ")}` : ""}`).join("<br>") || "N/A"}</div></div><div style="margin-top:12px"><b>Domain Tags</b><div class="muted">${(ex.domain_tags||[]).join(", ") || "N/A"}</div></div><div style="margin-top:12px"><b>Business Impacts</b><div class="muted">${(ex.business_impacts||[]).join(", ") || "N/A"}</div></div>`;
      const edu=data.education_analysis||{};
      document.getElementById("education").innerHTML=`<div class="score-row"><span>Highest Institute Tier</span><span>${esc(edu.highest_institute_tier)}</span></div><div class="score-row"><span>Strongest Course Value</span><span>${esc(edu.strongest_course_value_signal)}</span></div><div class="score-row"><span>Education Gap Flag</span><span>${boolLabel(edu.education_gap_flag)}</span></div><div class="score-row"><span>Education Gap</span><span>${esc(edu.education_gap_months)} months</span></div><div style="margin-top:12px"><b>Institutes</b><div class="muted">${(edu.top_institutes||[]).join(", ") || "N/A"}</div></div><div style="margin-top:12px"><b>Course Families</b><div class="muted">${(edu.course_families||[]).join(", ") || "N/A"}</div></div><div style="margin-top:12px"><b>GPA Summary</b><div class="muted">${(edu.gpa_summary||[]).join("<br>") || "N/A"}</div></div><div style="margin-top:12px"><b>Education Entries</b><div class="muted">${(edu.education_entries||[]).map(entry => `${esc(entry.institution_canonical)} | ${esc(entry.course_canonical)} | ${esc(entry.tier)} | ${esc(entry.gpa_raw)} (${esc(entry.gpa_band)})`).join("<br>") || "N/A"}</div></div>`;
      document.getElementById("strengths").innerHTML=(data.qualitative_analysis?.strengths||[]).map(x=>`<li>${x}</li>`).join("");
      document.getElementById("gaps").innerHTML=(data.qualitative_analysis?.gaps||[]).map(x=>`<li>${x}</li>`).join("");
      document.getElementById("risks").innerHTML=`<b>Risk Flags:</b> ${(data.qualitative_analysis?.risk_flags||[]).join(", ") || "None surfaced"}`;
      // Show interview panel if above threshold
      const phone=data.telephonic_round||{};
      document.getElementById("interviewPanel").style.display="block";
      document.getElementById("interviewPanelIntro").innerText=phone.enabled
        ? `Candidate scored ${score} — above threshold ${phone.threshold}. Generate recruiter call questions below.`
        : `Generate structured interview questions for this candidate.`;
      document.getElementById("raw").innerText=JSON.stringify(data,null,2);
    }

    // ── Interview Questions ──
    let _analysisData = null;
    let _questionsData = null;
    let _questionScores = [];
    const PRIORITY_COLOR = {high:"#DC2626", medium:"#D97706", low:"#16A34A"};

    async function generateQuestions(){
      if(!_analysisData){ alert("Upload a resume first."); return; }
      const btn = document.getElementById("genQBtn");
      btn.textContent = "Generating…"; btn.disabled=true;
      try {
        const res = await fetch("/generateInterviewQuestions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({analysis:_analysisData})});
        _questionsData = await res.json();
        renderQuestions(_questionsData);
        document.getElementById("applyScoresBtn").style.display="";
      } catch(e){ alert("Failed: "+e); }
      btn.textContent = "Regenerate Questions"; btn.disabled=false;
    }

    function renderQuestions(qdata){
      const questions = qdata.questions || [];
      _questionScores = new Array(questions.length).fill(null);
      const helpMap = qdata.rubric_param_help || {};
      const wrap = document.getElementById("interviewQuestionsWrap");
      const ss = qdata.stage_summary || {};
      const recQs = qdata.recruiter_questions || questions.filter(q=>q.stage!=='panel');
      const panelQs = qdata.panel_questions || questions.filter(q=>q.stage==='panel');
      const stageBar = `<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
        <button onclick="showTab('recruiter')" id="tabRecruiter" style="padding:8px 16px;font-size:13px;border-radius:999px">Recruiter Call (${recQs.length})</button>
        <button onclick="showTab('panel')" id="tabPanel" style="padding:8px 16px;font-size:13px;border-radius:999px;background:linear-gradient(135deg,var(--primary2),var(--purple));color:#fff">Panel Interview (${panelQs.length})</button>
        <span class="muted" style="line-height:2.2">Total: ${questions.length} | High: ${qdata.high_priority_count||0} | Medium: ${qdata.medium_priority_count||0}</span>
      </div>
      <div id="tabRecruiterContent"></div>
      <div id="tabPanelContent" style="display:none"></div>`;
      wrap.innerHTML = stageBar;
      function renderTabQuestions(qs, containerId){
        document.getElementById(containerId).innerHTML = qs.map((q,_i) => {
          const i = questions.indexOf(q);
          const help = helpMap[q.rubric_param] || {};
          const guide = q.scoring_guide || {};
          const stageBadge = q.stage==='panel'
            ? `<span style="display:inline-block;background:var(--primary-light);color:var(--primary2);border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;text-transform:uppercase;margin-right:4px">PANEL</span>`
            : `<span style="display:inline-block;background:var(--amber-light);color:var(--amber);border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;text-transform:uppercase;margin-right:4px">RECRUITER</span>`;
          return `<div class="q-card" id="qcard${i}" style="background:var(--white);border:1px solid var(--border);border-radius:16px;padding:16px;margin-bottom:12px">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap">
              <div>${stageBadge}<span style="display:inline-block;background:#F9FAFB;border:1px solid #CAD5E2;color:${PRIORITY_COLOR[q.priority||"low"]||"#16A34A"};border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;text-transform:uppercase">${esc(q.priority)} Priority</span>
                <span class="pill" style="margin-left:6px">${esc(q.rubric_param)}${q.max_pts?` (max ${q.max_pts})`:" "}</span>
                ${q.skill ? `<span class="pill">${esc(q.skill)}</span>` : ""}
              </div>
              <span id="qscore${i}" style="font-size:22px;font-weight:800;color:var(--primary2)"></span>
            </div>
            <div style="margin-top:10px;font-size:15px;font-weight:600">${esc(q.question)}</div>
            ${q.what_it_tests ? `<div class="muted" style="margin-top:6px;font-size:12px"><b>What it tests:</b> ${esc(q.what_it_tests)}</div>` : ""}
            ${help.how_scored ? `<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--primary2);font-size:12px">Scoring guide</summary>
              <div style="padding:10px 0 4px;font-size:12px;color:var(--text2)">
                <b>How scored:</b> ${esc(help.how_scored)}<br>
                ${guide.strong ? `<b>Strong answer:</b> ${esc(guide.strong)}<br>` : ""}
                ${guide.weak ? `<b>Weak answer:</b> ${esc(guide.weak)}<br>` : ""}
                ${guide.follow_up ? `<b>Follow-up probe:</b> ${esc(guide.follow_up)}<br>` : ""}
                ${help.green_flag ? `<b>Green flags:</b> ${esc(help.green_flag)}<br>` : ""}
                ${help.red_flag ? `<b>Red flags:</b> ${esc(help.red_flag)}` : ""}
              </div>
            </details>` : ""}
            <div style="margin-top:12px">
              <label style="font-size:12px;color:var(--text2);display:block;margin-bottom:4px">Candidate answer transcript (paste or type):</label>
              <textarea id="transcript${i}" rows="3" placeholder="Paste candidate's verbal answer here…" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px;color:var(--text);font-family:inherit;font-size:13px;resize:vertical"></textarea>
            </div>
            <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
              <button onclick="scoreQuestion(${i})" style="padding:8px 14px;font-size:12px" id="scoreBtn${i}">Score This Answer</button>
              <span id="qfeedback${i}" class="muted" style="font-size:12px"></span>
            </div>
            <div id="qresult${i}" style="margin-top:8px"></div>
          </div>`;
        }).join("");
      }
      renderTabQuestions(recQs, "tabRecruiterContent");
      renderTabQuestions(panelQs, "tabPanelContent");
      window._activeTab = "recruiter";
    }
    function showTab(tab){
      window._activeTab = tab;
      document.getElementById("tabRecruiterContent").style.display = tab==="recruiter"?"":"none";
      document.getElementById("tabPanelContent").style.display = tab==="panel"?"":"none";
      document.getElementById("tabRecruiter").style.background = tab==="recruiter"?"linear-gradient(135deg,var(--amber),#F59E0B)":"";
      document.getElementById("tabRecruiter").style.color = tab==="recruiter"?"#fff":"";
      document.getElementById("tabPanel").style.background = tab==="panel"?"linear-gradient(135deg,var(--primary2),var(--purple))":"";
      document.getElementById("tabPanel").style.color = tab==="panel"?"#fff":"";
    }
    async function scoreQuestion(idx){
      if(!_questionsData) return;
      const q = _questionsData.questions[idx];
      const transcript = document.getElementById(`transcript${idx}`).value.trim();
      if(!transcript){ document.getElementById(`qfeedback${idx}`).innerText="Enter an answer first."; return; }
      const btn = document.getElementById(`scoreBtn${idx}`);
      btn.textContent="Scoring…"; btn.disabled=true;
      document.getElementById(`qfeedback${idx}`).innerText="";
      try {
        const res = await fetch("/scoreQuestionAnswer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          question: q.question, theme: q.theme||q.rubric_param, answer_transcript: transcript,
          skill: q.skill||"", candidate_context: "",
          candidate_id: _analysisData?.candidate_id||""
        })});
        const sc = await res.json();
        _questionScores[idx] = sc;
        const scoreVal = sc.score_0_to_10 || 0;
        const scoreColor = scoreVal>=7?"#16A34A":scoreVal>=5?"#D97706":"#DC2626";
        document.getElementById(`qscore${idx}`).innerHTML=`<span style="color:${scoreColor}">${scoreVal}/10</span>`;
        document.getElementById(`qresult${idx}`).innerHTML=`
          <div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px;font-size:13px">
            <div style="margin-bottom:6px"><b style="color:#16A34A">Strong:</b> ${esc(sc.what_was_strong)}</div>
            <div style="margin-bottom:6px"><b style="color:#D97706">Missing:</b> ${esc(sc.what_was_missing)}</div>
            <div style="margin-bottom:6px"><b>Follow-up probe:</b> ${esc(sc.follow_up_probe)}</div>
            <div><b>Recruiter note:</b> ${esc(sc.recruiter_note)}</div>
            <div style="margin-top:6px;color:var(--text2)">Confidence: ${esc(sc.confidence)} | Rubric param: ${esc(sc.rubric_param)}</div>
          </div>`;
        document.getElementById(`qfeedback${idx}`).innerText="Scored.";
      } catch(e){ document.getElementById(`qfeedback${idx}`).innerText="Error: "+e; }
      btn.textContent="Re-score"; btn.disabled=false;
    }

    async function applyCallScores(){
      const scored = _questionScores.filter(s=>s!==null);
      if(!scored.length){ alert("Score at least one question first."); return; }
      const candidateId = _analysisData?.candidate_id || prompt("Enter candidate ID:","");
      if(!candidateId) return;
      const btn = document.getElementById("applyScoresBtn");
      btn.textContent="Applying…"; btn.disabled=true;
      try {
        const res = await fetch("/applyCallScores",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          candidate_id: candidateId, question_scores: scored, stage:"recruiter", recruiter_notes:""
        })});
        const result = await res.json();
        document.getElementById("callScoresResult").innerHTML=`
          <div style="background:#DCFCE7;border:1px solid #BBF7D0;border-radius:14px;padding:14px;font-size:13px">
            <b style="color:#16A34A">Call scores applied to rubric.</b>
            <div class="muted" style="margin-top:6px">${scored.length} questions scored. Rubric param overrides:
              ${Object.entries(result.rubric_param_overrides||{}).map(([k,v])=>`<span class="pill">${k}: ${v}</span>`).join("")}
            </div>
          </div>`;
      } catch(e){ document.getElementById("callScoresResult").innerHTML=`<div style="color:var(--red)">Error: ${e}</div>`; }
      btn.textContent="Apply Call Scores to Rubric"; btn.disabled=false;
    }
    </script></div></div></body></html>''')

def _parse_resume_sync(content: bytes, fname: str) -> dict:
    # Everything in here is synchronous and CPU/IO-bound (PDF extraction,
    # the full analyze_resume pipeline, LLM calls, disk writes) -- this
    # function is the unit that _PARSE_EXECUTOR runs off the event loop
    # thread so concurrent /resumeParse requests don't serialize behind
    # each other.
    import tempfile
    from pathlib import Path as _Path
    suffix = _Path(fname).suffix.lower()
    if suffix in (".pdf", ".docx"):
        try:
            from pdf_to_json_extractor import pdf_to_resume_json
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = _Path(tmp.name)
            payload = pdf_to_resume_json(tmp_path)
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            if not payload:
                raise HTTPException(status_code=422, detail="PDF extraction returned empty result.")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"PDF extraction failed: {exc}")
    else:
        try:
            payload = json.loads(content)
        except Exception:
            raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON.")
    logger.info("Resume parse started file=%s", fname)
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    resume_input = ResumeInput.from_any(payload)
    _analyzed_at = _dt.now(_tz.utc).isoformat()
    _t0 = _time.perf_counter()
    result = analyze_resume(resume_input)
    _elapsed_ms = round((_time.perf_counter() - _t0) * 1000, 1)
    result["_timing"] = {
        "analyzed_at": _analyzed_at,
        "completed_at": _dt.now(_tz.utc).isoformat(),
        "elapsed_ms": _elapsed_ms,
    }
    result["recruiter_summary"] = generate_recruiter_analysis(result)
    live_eval_path = save_live_analysis_report(
        analysis=result,
        payload=payload,
        file_label=fname or "live_analysis.json",
        runs_dir=EVAL_RUNS_DIR,
    )
    # Always derive candidate_id and save analysis
    overview = result.get("candidate_overview", {})
    _INVALID = {"n/a", "na", "none", "null", "unknown", "", "N/A", "NA"}
    def _cval(v): return v if v and str(v).strip() not in _INVALID and "/" not in str(v) else None
    candidate_id = (_cval(overview.get("email")) or _cval(overview.get("name")) or _cval(fname) or "unknown").replace(" ", "_")
    candidate_name = overview.get("name", "")
    result["candidate_id"] = candidate_id
    result["_raw_resume"] = payload  # Saved for JD matching bridge
    # Persist rubric scorecard at resume stage if enabled
    rubric_scorecard = result.get("rubric_scorecard")
    if os.getenv("ENABLE_NEW_RUBRIC", "false").lower() == "true" and rubric_scorecard:
        try:
            save_candidate_score(candidate_id, rubric_scorecard, stage="resume", candidate_name=candidate_name)
        except Exception as exc:
            logger.warning("Failed to save candidate score: %s", exc)
    # Save full analysis for recruiter / panel screen retrieval
    try:
        CANDIDATE_ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
        (CANDIDATE_ANALYSES_DIR / f"{candidate_id}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Failed to save candidate analysis: %s", exc)
    logger.info(
        "Resume parse completed file=%s llm_used=%s provider=%s failure_reason=%s live_eval=%s",
        fname,
        result.get("scorecard", {}).get("llm_used"),
        result.get("scorecard", {}).get("llm_provider"),
        result.get("scorecard", {}).get("llm_failure_reason"),
        live_eval_path,
    )
    return result

@app.post("/resumeParse")
async def parse_resume(file: UploadFile = File(...)):
    fname = file.filename or "resume"
    content = await file.read()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_PARSE_EXECUTOR, _parse_resume_sync, content, fname)
    return JSONResponse(content=result)

@app.post("/generateInterviewQuestions")
async def generate_interview_questions(payload: dict):
    analysis = payload.get("analysis", {})
    client_id = payload.get("client_id")
    role_id = payload.get("role_id")
    client_role_config = None
    if client_id and role_id:
        client_role_config = load_role_config(client_id, role_id)
    return build_interview_questions(analysis, client_role_config=client_role_config)

@app.post("/feedback")
async def capture_feedback(payload: ResumeFeedback):
    stored = save_feedback(payload.model_dump())
    return JSONResponse(content={"status": "ok", **stored})

@app.get("/api/eval/runs")
def eval_runs_api():
    runs = _read_eval_runs()
    return {
        "run_count": len(runs),
        "runs": [item["summary"] for item in runs],
        "leaderboard": _leaderboard_rows(runs),
    }


@app.get("/api/eval/runs/{run_id}")
def eval_run_detail_api(run_id: str):
    for item in _read_eval_runs():
        if item["summary"]["run_id"] == run_id:
            return item["report"]
    raise HTTPException(status_code=404, detail="Eval run not found.")


@app.get("/api/candidateAnalysis/{candidate_id}")
async def get_candidate_analysis(candidate_id: str):
    path = CANDIDATE_ANALYSES_DIR / f"{candidate_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found. Please analyse this resume first.")
    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))


@app.get("/evals", response_class=HTMLResponse)
def eval_workspace(user: dict = Depends(require_role("super_admin"))):
    sidebar_html = _sidebar("evals", user)
    return (f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Eval Workspace</title><meta name="viewport" content="width=device-width, initial-scale=1">''' + _BASE_CSS + '''<style>
    .wrap{padding:24px 24px 60px}
    .hero,.card{background:#FFFFFF;border:1px solid #CAD5E2;border-radius:12px}
    .hero{padding:22px 24px 18px;margin-bottom:16px}.toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
    .linkbtn,button{background:#353395;color:#fff;border:none;border-radius:10px;padding:9px 18px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;font-size:13px}
    .linkbtn:hover,button:hover{opacity:.88}
    .kicker{text-transform:uppercase;letter-spacing:.1em;font-size:10px;color:#62748E;font-weight:600}h1,h2,h3{margin:0 0 10px;color:#262626}.muted{color:#62748E}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
    .col-3{grid-column:span 3}.col-4{grid-column:span 4}.col-5{grid-column:span 5}.col-6{grid-column:span 6}.col-7{grid-column:span 7}.col-8{grid-column:span 8}.col-12{grid-column:span 12}.card{padding:16px}
    .metric{font-size:30px;font-weight:800;letter-spacing:-.02em;color:#353395}.band{display:inline-flex;padding:5px 12px;border-radius:999px;font-size:12px;font-weight:700;border:1px solid #E0E0F5;background:#F0F0FB;color:#353395}
    .table{width:100%;border-collapse:collapse}.table th,.table td{padding:10px 8px;border-bottom:1px solid #F1F5F9;text-align:left;font-size:13px;vertical-align:top}.table th{color:#62748E;font-weight:600;font-size:11px;text-transform:uppercase}
    .pill{display:inline-flex;align-items:center;background:#F0F0FB;border:1px solid #E0E0F5;border-radius:999px;padding:3px 10px;margin:3px 4px 3px 0;font-size:11px;color:#353395;font-weight:500}
    .run-item,.failure-card,.slice-card{background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;padding:14px;margin-bottom:10px}
    .run-item{cursor:pointer}.run-item.active{border-color:#353395;background:#F0F0FB}.run-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.run-meta{color:#62748E;font-size:12px;line-height:1.45}
    .stat-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.stat{background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;padding:12px}.stat b{display:block;font-size:22px;color:#353395}
    .detail-card{background:#F9FAFB;border:1px solid #CAD5E2;border-radius:10px;overflow:hidden;margin-bottom:10px}.detail-card summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.detail-card summary::-webkit-details-marker{display:none}.detail-body{padding:0 14px 14px}
    pre{white-space:pre-wrap;background:#F8FAFC;border:1px solid #CAD5E2;border-radius:10px;padding:14px;max-height:420px;overflow:auto;color:#333;font-size:12px}.danger{color:#DC2626}.ok{color:#16A34A}
    @media(max-width:980px){.col-3,.col-4,.col-5,.col-6,.col-7,.col-8,.col-12{grid-column:span 12}.stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    </style></head><body>''' + f'<div class="app-shell">{sidebar_html}<div class="main">' + '''<div class="wrap"><div class="hero"><div class="kicker">Eval Engineering Workspace</div><h1 style="font-size:22px;font-weight:800">Eval Workspace</h1><p style="color:#62748E;font-size:14px;margin:4px 0 14px">Browse saved eval runs, compare experiment quality, inspect slice behavior, and drill into top failures.</p><div class="toolbar"><a class="linkbtn" href="/">&#8592; Dashboard</a><button onclick="reloadRuns()">Refresh Runs</button><div id="status" class="muted" style="font-size:13px">Loading runs...</div></div></div><div class="grid"><div class="col-4 card"><h2>Run History</h2><div id="runList" class="muted">No runs loaded yet.</div></div><div class="col-8 card"><h2>Leaderboard</h2><div id="leaderboard" class="muted">Loading leaderboard...</div></div><div class="col-12 card"><h2>Selected Run</h2><div id="emptyState" class="muted">Choose a run from the left to inspect summary metrics, slices, regressions, and failures.</div><div id="runDetail" style="display:none"><div id="runHeader" style="margin-bottom:12px"></div><div id="summaryStats" class="stat-grid"></div></div></div><div class="col-6 card"><h2>Slice Explorer</h2><div id="sliceList" class="muted">Select a run to inspect slices.</div></div><div class="col-6 card"><h2>Regression Gates</h2><div id="regressionList" class="muted">Select a run to inspect regression gates.</div></div><div class="col-12 card"><h2>Top Failures</h2><div id="failureList" class="muted">Select a run to inspect failing cases.</div></div><div class="col-12 card"><h2>Resumes in this Run</h2><div id="caseList" class="muted">Select a run to see all individual resumes.</div></div><div class="col-12 card"><h2>Run JSON</h2><pre id="raw">Select a run to inspect the raw eval report.</pre></div></div></div><script>
    let runIndex = [];
    let selectedRunId = null;
    function esc(x){return x ?? "N/A";}
    function pct(x){return x === null || x === undefined ? "N/A" : `${Math.round(Number(x) * 100)}%`;}
    function num(x){return x === null || x === undefined ? "N/A" : Number(x).toFixed(3).replace(/\\.000$/,'');}
    function ms(x){return x === null || x === undefined ? "N/A" : `${Math.round(Number(x))} ms`;}
    function usd(x){return x === null || x === undefined ? "N/A" : `$${Number(x).toFixed(4)}`;}
    function pills(items){return (items||[]).map(i=>`<span class="pill">${i}</span>`).join('');}
    function stat(label,value,help){return `<div class="stat"><div class="muted">${label}</div><b>${value}</b><div class="muted">${help||""}</div></div>`;}
    function renderLeaderboard(rows){
      if(!rows.length){return '<div class="muted">No eval runs have been saved yet. Analyze a resume from the main UI or generate one with `eval_framework.py`, then refresh this page.</div>';}
      return `<table class="table"><thead><tr><th>Run</th><th>Dataset</th><th>Composite</th><th>Confidence</th><th>Latency</th><th>Cost/Resume</th><th>Regressions</th></tr></thead><tbody>${rows.map(row=>`<tr><td><b>${esc(row.run_label || row.run_id)}</b><div class="run-meta">${esc(row.generated_at)}</div></td><td>${esc(row.dataset_name)}</td><td>${num(row.composite_score)}</td><td>${pct(row.average_confidence_score)}</td><td>${ms(row.average_latency_ms)}</td><td>${usd(row.average_cost_per_resume_usd)}</td><td class="${row.regression_count ? 'danger' : 'ok'}">${esc(row.regression_count)}</td></tr>`).join('')}</tbody></table>`;
    }
    function renderRunList(runs){
      if(!runs.length){return '<div class="muted">No saved eval runs found under `eval_runs/`. Analyze a resume once and it will appear here automatically.</div>';}
      return runs.map(run=>`<div class="run-item ${selectedRunId===run.run_id?'active':''}" onclick="selectRun('${run.run_id}')"><div class="run-head"><div><b>${esc(run.run_label || run.run_id)}</b><div class="run-meta">${esc(run.dataset_name)} | ${esc(run.generated_at)}</div></div><div class="band">${esc(run.total_cases)} cases</div></div><div class="run-meta" style="margin-top:8px">Confidence: ${pct(run.average_confidence_score)} | Latency: ${ms(run.average_latency_ms)} | Tokens/resume: ${num(run.average_tokens_per_resume)} | Cost/resume: ${usd(run.average_cost_per_resume_usd)} | Regressions: <span class="${run.regression_count ? 'danger' : 'ok'}">${esc(run.regression_count)}</span></div></div>`).join('');
    }
    function renderSlices(slices){
      const rows = slices?.tag_slices || [];
      if(!rows.length){return '<div class="muted">No slice metrics were generated for this run.</div>';}
      return rows.map(slice=>`<div class="slice-card"><div class="run-head"><div><b>${esc(slice.tag)}</b><div class="run-meta">${esc(slice.count)} cases</div></div><div class="band">${pct(slice.expectation_match_rate)}</div></div><div class="run-meta" style="margin-top:8px">Avg score: ${num(slice.average_total_score)} | Confidence: ${pct(slice.average_confidence_score)} | Latency: ${ms(slice.average_latency_ms)} | Cost: ${usd(slice.average_cost_usd)} | LLM score success: ${pct(slice.llm_score_success_rate)} | Consistency: ${pct(slice.score_consistency_rate)}</div></div>`).join('');
    }
    function renderRegressions(regression){
      if(!regression?.baseline_available){return '<div class="muted">No baseline report was attached to this run, so there are no regression deltas yet.</div>';}
      const gates = regression.gate_results || [];
      if(!gates.length){return '<div class="muted">No regression gate results were recorded.</div>';}
      return gates.map(g=>`<div class="failure-card"><div class="run-head"><div><b>${esc(g.metric)}</b><div class="run-meta">Current: ${esc(g.current)} | Baseline: ${esc(g.baseline)}</div></div><div class="band ${g.status==='fail'?'danger':'ok'}">${esc(g.status).toUpperCase()}</div></div>${(g.reasons||[]).length ? `<div class="run-meta" style="margin-top:8px">Reasons: ${(g.reasons||[]).map(esc).join(' | ')}</div>` : ''}</div>`).join('');
    }
    function renderCases(cases){
      if(!cases?.length){return '<div class="muted">No case records in this run.</div>';}
      return `<table class="table"><thead><tr><th>#</th><th>Candidate</th><th>Role Family</th><th>Band</th><th>Score</th><th>DNA</th><th>Latency</th></tr></thead><tbody>${cases.map((c,i)=>`<tr><td>${i+1}</td><td><b>${esc(c.candidate_name||c.case_id)}</b><div class="run-meta" style="font-size:11px">${esc(c.file)}</div></td><td>${esc(c.predicted?.role_family)}</td><td>${esc(c.predicted?.band)}</td><td>${esc(c.predicted?.total_score)}</td><td>${esc(c.predicted?.dna)}</td><td>${ms(c.latency_ms)}</td></tr>`).join('')}</tbody></table>`;
    }
    function renderFailures(failures){
      if(!failures?.length){return '<div class="muted">No top failures were captured for this run.</div>';}
      return failures.map(f=>`<details class="detail-card"><summary><div><b>${esc(f.candidate_name || f.case_id)}</b><div class="run-meta">${esc(f.file)}</div></div><div class="band">${esc((f.failed_checks||[]).length)} failed checks</div></summary><div class="detail-body"><div class="run-meta"><b>Predicted:</b> role=${esc(f.predicted?.role_family)} | band=${esc(f.predicted?.band)} | dna=${esc(f.predicted?.dna)} | score=${esc(f.predicted?.total_score)}</div><div class="run-meta" style="margin-top:8px"><b>Confidence:</b> ${esc(f.confidence?.label)} (${pct(f.confidence?.score)}) | <b>Latency:</b> ${ms(f.latency_ms)} | <b>Total tokens:</b> ${esc(f.total_tokens)} | <b>Cost:</b> ${usd(f.end_to_end_cost_usd)}</div>${f.expected ? `<div class="run-meta" style="margin-top:8px"><b>Expected:</b> ${esc(JSON.stringify(f.expected))}</div>` : ''}${(f.failed_checks||[]).length ? `<div class="run-meta" style="margin-top:8px"><b>Failed checks:</b> ${(f.failed_checks||[]).map(c=>`${esc(c.field)} expected=${esc(c.expected)} actual=${esc(c.actual)}`).join(' | ')}</div>` : ''}${(f.missing_component_justifications||[]).length ? `<div class="run-meta" style="margin-top:8px"><b>Missing justifications:</b> ${(f.missing_component_justifications||[]).map(esc).join(', ')}</div>` : ''}${f.score_failure_reason ? `<div class="run-meta" style="margin-top:8px"><b>Score failure reason:</b> ${esc(f.score_failure_reason)}</div>` : ''}${f.skill_judgment_reason ? `<div class="run-meta" style="margin-top:8px"><b>Skill judgment reason:</b> ${esc(f.skill_judgment_reason)}</div>` : ''}${(f.failure_trace?.failed_llm_requests||[]).length ? `<div class="run-meta" style="margin-top:8px"><b>Failure traces:</b><br>${(f.failure_trace.failed_llm_requests||[]).map(t=>`${esc(t.provider)} | ${esc(t.model)} | ${esc(t.mode)} | latency=${ms(t.latency_ms)} | tokens=${esc(t.total_tokens)} | error=${esc(t.error)}`).join('<br>')}</div>` : '<div class="run-meta" style="margin-top:8px"><b>Failure traces:</b> No failed LLM requests captured for this case.</div>'}<div style="margin-top:8px">${pills(f.tags || [])}</div></div></details>`).join('');
    }
    async function reloadRuns(){
      document.getElementById('status').innerText = 'Loading runs...';
      const res = await fetch('/api/eval/runs');
      const data = await res.json();
      runIndex = data.runs || [];
      document.getElementById('runList').innerHTML = renderRunList(runIndex);
      document.getElementById('leaderboard').innerHTML = renderLeaderboard(data.leaderboard || []);
      document.getElementById('status').innerText = `${runIndex.length} run(s) loaded.`;
      if(runIndex.length && !selectedRunId){
        selectRun(runIndex[0].run_id);
      } else if(selectedRunId){
        document.getElementById('runList').innerHTML = renderRunList(runIndex);
      }
    }
    async function selectRun(runId){
      selectedRunId = runId;
      document.getElementById('runList').innerHTML = renderRunList(runIndex);
      document.getElementById('status').innerText = `Loading run ${runId}...`;
      const res = await fetch(`/api/eval/runs/${encodeURIComponent(runId)}`);
      const run = await res.json();
      const summary = run.summary || {};
      const runMeta = run.run || {};
      const hasExpectations = Number(summary.expectation_case_count || 0) > 0;
      document.getElementById('emptyState').style.display = 'none';
      document.getElementById('runDetail').style.display = 'block';
      document.getElementById('runHeader').innerHTML = `<div class="run-head"><div><div class="kicker">Selected Run</div><h3>${esc(runMeta.run_label || runMeta.run_id)}</h3><div class="run-meta">${esc(runMeta.dataset_name)} | ${esc(runMeta.generated_at)} | input=${esc(runMeta.input_path)}</div></div><div>${pills([`cases:${esc(summary.total_cases)}`, `expectations:${esc(summary.expectation_case_count)}`, `baseline:${run.regression?.baseline_available ? 'yes' : 'no'}`])}</div></div>`;
      document.getElementById('summaryStats').innerHTML = [
        stat(hasExpectations ? 'Expectation Match' : 'Expectation Match', hasExpectations ? pct(summary.expectation_match_rate) : 'Live run', hasExpectations ? 'Share of expectation-labelled cases that fully matched.' : 'Requires expectation labels or a golden dataset.'),
        stat(hasExpectations ? 'Role Match' : 'Role Predicted', hasExpectations ? pct(summary.role_family_match_rate) : pct(summary.role_prediction_rate), hasExpectations ? 'Role family accuracy against expectation labels.' : 'Share of cases where a role family prediction was produced.'),
        stat(hasExpectations ? 'Band Match' : 'Band Predicted', hasExpectations ? pct(summary.band_match_rate) : pct(summary.band_prediction_rate), hasExpectations ? 'Band accuracy against expectation labels.' : 'Share of cases where a band prediction was produced.'),
        stat(hasExpectations ? 'DNA Match' : 'DNA Predicted', hasExpectations ? pct(summary.dna_match_rate) : pct(summary.dna_prediction_rate), hasExpectations ? 'DNA accuracy against expectation labels.' : 'Share of cases where a DNA label was produced.'),
        stat(hasExpectations ? 'Score MAE' : 'Name Extraction', hasExpectations ? num(summary.score_mae) : pct(summary.name_extraction_rate), hasExpectations ? 'Average absolute score error where target scores exist.' : 'Share of resumes where candidate name was extracted successfully.'),
        stat('LLM Score Success', pct(summary.llm_score_success_rate), 'How often LLM scoring succeeded.'),
        stat('LLM Skill Success', pct(summary.llm_skill_success_rate), 'How often LLM skill judging succeeded.'),
        stat('Consistency', pct(summary.score_consistency_rate), 'How often component totals matched total score.'),
        stat('Analysis Confidence', pct(summary.average_confidence_score), 'Average confidence across score dimensions.'),
        stat('Avg Latency', ms(summary.average_latency_ms), 'Average end-to-end latency per resume.'),
        stat('Total Tokens', num(summary.total_tokens), 'Prompt plus completion tokens across the run.'),
        stat('Tokens / Resume', num(summary.average_tokens_per_resume), 'Average total token use per resume.'),
        stat('Cost / Resume', usd(summary.average_cost_per_resume_usd), 'Estimated end-to-end LLM cost per resume.'),
        stat('Total Cost', usd(summary.total_cost_usd), 'Estimated LLM cost for the whole run.'),
        stat('Remaining Tokens', num(summary.average_remaining_context_tokens), 'Average remaining model context window after completion.'),
        stat('Contact Extraction', pct(summary.contact_extraction_rate), 'Share of resumes with at least one contact field extracted.'),
        stat('Recruiter Summary', pct(summary.recruiter_summary_rate), 'Share of resumes where recruiter summary was produced.'),
        stat('Screening Rate', pct(summary.screening_recommendation_rate), 'Share of resumes recommended for screening.'),
        stat('Telephonic Rate', pct(summary.telephonic_enable_rate), 'Share of resumes that crossed telephonic threshold.'),
        stat('Avg Risk Flags', num(summary.average_risk_flag_count), 'Average recruiter risk flags per resume.'),
        stat('Avg Gaps', num(summary.average_gap_count), 'Average recruiter gap count per resume.')
      ].join('');
      document.getElementById('sliceList').innerHTML = renderSlices(run.slices || {});
      document.getElementById('regressionList').innerHTML = renderRegressions(run.regression || {});
      document.getElementById('failureList').innerHTML = renderFailures(run.top_failures || []);
      document.getElementById('caseList').innerHTML = renderCases(run.cases || []);
      document.getElementById('raw').innerText = JSON.stringify(run, null, 2);
      document.getElementById('status').innerText = `Loaded run ${runId}.`;
    }
    reloadRuns();
    </script></div></div></body></html>''')

@app.get("/bossExplainability")
def boss_explainability():
    return build_boss_explainability({})


# ---------------------------------------------------------------------------
# Stage score update (recruiter / panel)
# ---------------------------------------------------------------------------

@app.post("/updateStageScore")
async def update_stage_score(payload: StageUpdateInput):
    """Apply recruiter or panel overrides to an existing rubric scorecard."""
    existing = load_candidate_score(payload.candidate_id)
    if not existing or not existing.get("stages"):
        raise HTTPException(status_code=404, detail=f"No score history found for candidate_id={payload.candidate_id!r}")
    # Use the latest stage rubric as base
    last_stage = existing["stages"][-1]
    base_rubric = {
        "total_score": last_stage.get("total_score", 0),
        "experience_score": last_stage.get("experience_score", 0),
        "skills_score": last_stage.get("skills_score", 0),
        "education_score": last_stage.get("education_score", 0),
        "breakdown": last_stage.get("breakdown", {}),
        "reject_flags": last_stage.get("reject_flags", []),
        "stage_scores": last_stage.get("stage_scores", {}),
    }
    from rubric_engine import apply_stage_update
    updated = apply_stage_update(
        base_rubric=base_rubric,
        stage_name=payload.stage,
        stage_overrides=payload.stage_overrides,
        candidate_id=payload.candidate_id,
    )
    if payload.recruiter_notes:
        updated["recruiter_notes"] = payload.recruiter_notes
    path = save_candidate_score(
        candidate_id=payload.candidate_id,
        rubric_result=updated,
        stage=payload.stage,
        candidate_name=existing.get("candidate_name", ""),
    )
    return JSONResponse(content={"status": "ok", "candidate_id": payload.candidate_id,
                                  "stage": payload.stage, "new_total": updated["total_score"],
                                  "stage_score_100": updated.get("stage_score_100"),
                                  "stage_scores": updated.get("stage_scores", {}),
                                  "path": str(path)})


@app.get("/candidateScore/{candidate_id}")
async def get_candidate_score(candidate_id: str):
    """Return full stage history for a candidate."""
    record = load_candidate_score(candidate_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"No score history found for candidate_id={candidate_id!r}")
    return JSONResponse(content=record)


# ---------------------------------------------------------------------------
# Client config endpoints
# ---------------------------------------------------------------------------

@app.post("/clients")
async def create_client(payload: dict):
    """Create or overwrite a client configuration."""
    client_id = payload.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id is required.")
    path = save_client_config(client_id, payload)
    return JSONResponse(content={"status": "ok", "client_id": client_id, "path": str(path)})


@app.get("/clients/{client_id}")
async def get_client(client_id: str):
    config = load_client_config(client_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Client {client_id!r} not found.")
    return JSONResponse(content=config)


@app.put("/clients/{client_id}/roles/{role_id}")
async def upsert_role(client_id: str, role_id: str, payload: dict):
    """Add or update a role config inside a client profile."""
    path = upsert_role_config(client_id, role_id, payload)
    return JSONResponse(content={"status": "ok", "client_id": client_id, "role_id": role_id, "path": str(path)})


@app.get("/clients/{client_id}/report")
async def client_report(client_id: str, months_back: int = 3):
    return JSONResponse(content=get_client_report(client_id, months_back=months_back))


@app.post("/clients/{client_id}/feedback")
async def client_feedback(client_id: str, payload: dict):
    """Capture a position event or feedback record for a client."""
    event_type = payload.get("event_type")
    candidate_id = payload.get("candidate_id")
    if event_type in {"selected", "joined", "churned", "rejected"}:
        append_position_event(client_id, payload)
    elif candidate_id:
        save_candidate_feedback(client_id, candidate_id, payload)
    else:
        save_client_feedback(client_id, payload)
    return JSONResponse(content={"status": "ok"})


@app.get("/clients/{client_id}/templates")
async def get_client_templates(client_id: str):
    """Return the resume templates assigned to this client."""
    config = load_client_config(client_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Client {client_id!r} not found.")
    from resume_template_engine import RESUME_TEMPLATES
    assigned_ids = config.get("assigned_templates") or list(RESUME_TEMPLATES.keys())[:6]
    templates = {tid: RESUME_TEMPLATES[tid] for tid in assigned_ids if tid in RESUME_TEMPLATES}
    return JSONResponse(content={"client_id": client_id, "templates": templates})


# ---------------------------------------------------------------------------
# Client intelligence endpoints
# ---------------------------------------------------------------------------

@app.post("/candidateFit")
async def candidate_fit(payload: ClientFitRequest):
    """Generate 5-7 fit bullets + fit score for a candidate against a client role."""
    client_config = load_client_config(payload.client_id)
    if not client_config:
        raise HTTPException(status_code=404, detail=f"Client {payload.client_id!r} not found.")
    role_config = load_role_config(payload.client_id, payload.role_id)
    if not role_config:
        raise HTTPException(status_code=404, detail=f"Role {payload.role_id!r} not found for client {payload.client_id!r}.")
    from client_intelligence_engine import build_candidate_fit_narrative
    result = build_candidate_fit_narrative(
        candidate_analysis=payload.analysis,
        client_config=client_config,
        role_config=role_config,
    )
    return JSONResponse(content=result)


@app.post("/searchCandidates")
async def search_candidates(query: CandidateSearchQuery):
    """Search stored candidate scores with multi-filter criteria."""
    from client_intelligence_engine import search_candidates as _search
    from candidate_score_store import CANDIDATE_SCORES_DIR
    results = _search(query.model_dump(), str(CANDIDATE_SCORES_DIR))
    return JSONResponse(content={"count": len(results), "candidates": results})


@app.post("/similarCompanies")
async def similar_companies(payload: dict):
    """Return up to 10 similar companies for a candidate's experience profile."""
    candidate_id = payload.get("candidate_id")
    analysis = payload.get("analysis")
    if not analysis and candidate_id:
        # Try to load from score store
        record = load_candidate_score(candidate_id)
        if record:
            analysis = record
    if not analysis:
        raise HTTPException(status_code=400, detail="Provide 'analysis' dict or a valid 'candidate_id'.")
    from client_intelligence_engine import find_similar_companies
    result = find_similar_companies(analysis, top_n=10)
    return JSONResponse(content=result)


@app.get("/resourceIntelligence/{company}")
async def resource_intelligence(company: str):
    """Find candidates whose experience includes the given source company."""
    from client_intelligence_engine import resource_intelligence as _ri
    from candidate_score_store import CANDIDATE_SCORES_DIR
    result = _ri(company, str(CANDIDATE_SCORES_DIR))
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Per-question transcript scoring
# ---------------------------------------------------------------------------

@app.post("/scoreQuestionAnswer")
async def score_question_answer_endpoint(payload: QuestionAnswerInput):
    """Score a single question + candidate verbal answer (0-10 scale, LLM-graded).
    Returns score, confidence, strengths, gaps, follow-up probe, recruiter note.
    """
    from question_scoring_engine import score_question_answer
    result = score_question_answer(
        question=payload.question,
        theme=payload.theme,
        answer_transcript=payload.answer_transcript,
        skill=payload.skill,
        candidate_context=payload.candidate_context,
    )
    return JSONResponse(content=result)


@app.post("/applyCallScores")
async def apply_call_scores(payload: CallScoresInput):
    """Aggregate per-question call scores into rubric overrides and save as recruiter stage.

    Accepts a list of question score dicts (each from /scoreQuestionAnswer).
    Computes rubric param overrides, merges with any existing rubric base,
    and writes recruiter stage to candidate_scores/{candidate_id}.json.
    """
    from question_scoring_engine import aggregate_call_scores_to_rubric_overrides
    from candidate_score_store import save_candidate_score, load_candidate_score

    rubric_overrides = aggregate_call_scores_to_rubric_overrides(payload.question_scores)

    # Load existing candidate record to attach delta context
    existing = load_candidate_score(payload.candidate_id) or {}
    base_total = (existing.get("current_total") or 0)

    # Build a minimal stage result for storage
    stage_result = {
        "stage": payload.stage,
        "rubric_param_overrides": rubric_overrides,
        "question_scores": payload.question_scores,
        "questions_scored": len(payload.question_scores),
        "avg_question_score": round(
            sum(float(q.get("score_0_to_10") or 5) for q in payload.question_scores) / max(1, len(payload.question_scores)), 2
        ),
        "recruiter_notes": payload.recruiter_notes or "",
    }

    saved = save_candidate_score(payload.candidate_id, stage_result, stage=payload.stage)
    return JSONResponse(content={
        "status": "ok",
        "candidate_id": payload.candidate_id,
        "stage": payload.stage,
        "rubric_param_overrides": rubric_overrides,
        "questions_scored": len(payload.question_scores),
        "saved_path": str(saved),
    })


# ---------------------------------------------------------------------------
# Recruiter Screen — phone screen (simplified: analysis + questions + score)
# ---------------------------------------------------------------------------

@app.get("/recruiter-screen/{candidate_id}", response_class=HTMLResponse)
def recruiter_screen(candidate_id: str, user: dict = Depends(get_current_user)):
    from fastapi.responses import HTMLResponse as _HR
    cid_js = candidate_id.replace("'", "\\'")
    _html = ("""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Phone Screen &mdash; Resume Intelligence</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#F9FAFB;--white:#FFFFFF;--border:#CAD5E2;--text:#262626;--text2:#62748E;--primary:#353395;--primary2:#6366F1;--primary-light:#F0F0FB;--primary-border:#E0E0F5;--amber:#D97706;--amber-light:#FEF3C7;--red:#DC2626;--red-light:#FEE2E2;--card:#FFFFFF;--line:#CAD5E2;--muted:#62748E;--accent:#353395;--accent2:#6366F1;--warn:#D97706;--danger:#DC2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Aptos,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--primary2);text-decoration:none}
.app-shell{display:flex;min-height:100vh}
.sidebar{width:240px;min-height:100vh;background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column;position:fixed;left:0;top:0;z-index:100}
.sidebar-logo{padding:14px 16px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.sidebar-logo-icon{width:32px;height:32px;background:linear-gradient(135deg,var(--primary),var(--primary2));border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:800;flex-shrink:0}
.sidebar-brand{font-size:13px;font-weight:800;color:var(--text)}
.sidebar-nav{flex:1;padding:8px;overflow-y:auto}
.nav-section-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);padding:10px 8px 4px}
.nav-item{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:8px;font-size:13px;font-weight:500;color:var(--text2);cursor:pointer;text-decoration:none;transition:background .12s,color .12s;white-space:nowrap}
.nav-item:hover{background:var(--bg);color:var(--text)}
.nav-item.active{background:var(--primary-light);color:var(--primary);font-weight:600}
.sidebar-footer{padding:8px;border-top:1px solid var(--border)}
.main-content{margin-left:240px;flex:1;min-height:100vh}
.wrap{max-width:1100px;padding:24px 24px 80px}
.card{background:var(--white);border:1px solid var(--border);border-radius:12px;padding:20px 24px;margin-bottom:14px;position:relative;overflow:hidden}
.abar{position:absolute;top:0;left:0;right:0;height:3px;border-radius:20px 20px 0 0}
.kicker{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:6px}
/* HERO */
.hero-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap}
.cname{font-size:32px;font-weight:900;letter-spacing:-.02em;color:var(--primary);line-height:1.1;margin-bottom:6px}
.score-badge{background:var(--white);border:1px solid var(--border);border-radius:16px;padding:14px 22px;text-align:center;min-width:110px;flex-shrink:0}
.score-val{font-size:36px;font-weight:900;color:var(--primary);line-height:1}
.score-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-top:4px}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 12px}
.pill{display:inline-flex;align-items:center;padding:4px 11px;border-radius:99px;font-size:11px;font-weight:700;background:var(--primary-light);border:1px solid var(--primary-border);color:var(--primary)}
.pill-g{color:#16A34A;background:#DCFCE7;border-color:#BBF7D0}.pill-b{color:var(--primary2);background:var(--primary-light);border-color:var(--primary-border)}.pill-w{color:var(--amber);background:var(--amber-light);border-color:#FDE68A}
.narrative{font-size:13px;color:var(--text2);line-height:1.65;margin-top:4px}
/* SCORE TILES */
.score-tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}
@media(max-width:540px){.score-tiles{grid-template-columns:1fr}}
.stile{background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px 18px}
.stile-val{font-size:26px;font-weight:900;line-height:1;color:var(--primary)}
.stile-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-top:4px}
.stile-sub{font-size:11px;color:var(--amber);margin-top:4px}
/* QUESTIONS */
.q-card{background:var(--white);border:1px solid var(--border);border-radius:14px;padding:16px 20px;margin-bottom:12px}
.q-card.scored{border-color:#16A34A}
.q-num{width:24px;height:24px;border-radius:50%;background:linear-gradient(135deg,var(--amber),#F59E0B);color:#fff;font-size:11px;font-weight:800;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;margin-right:10px}
.q-text{font-size:13px;font-weight:700;color:var(--text);line-height:1.55;margin-bottom:12px}
.q-tag{display:inline-flex;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;text-transform:uppercase;margin-right:5px;margin-bottom:8px}
.th{background:var(--red-light);color:var(--red);border:1px solid #FECACA}
.tm{background:var(--amber-light);color:var(--amber);border:1px solid #FDE68A}
.tl{background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0}
.ans{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px 13px;font-size:13px;font-family:inherit;color:var(--text);resize:vertical;min-height:72px;transition:border .15s;line-height:1.5}
.ans:focus{outline:none;border-color:var(--amber)}
.score-result{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin-top:10px;font-size:12px;display:none}
/* BUTTONS */
.btn-score{background:linear-gradient(135deg,var(--amber),#F59E0B);color:#fff;border:none;border-radius:12px;padding:13px 32px;font-size:14px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:7px;transition:opacity .15s,transform .1s}
.btn-score:hover{opacity:.9;transform:translateY(-1px)}
.btn-score:disabled{opacity:.5;cursor:not-allowed;transform:none}
.btn-ghost{background:var(--white);color:var(--text2);border:1px solid var(--border);border-radius:12px;padding:12px 22px;font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.btn-ghost:hover{background:var(--bg);color:var(--text)}
/* UPDATED SCORE */
.updated-panel{display:none;background:#DCFCE7;border:1px solid #BBF7D0;border-radius:16px;padding:24px 28px;margin-top:16px}
.new-score-val{font-size:56px;font-weight:900;color:var(--primary);line-height:1}
.spin{display:inline-block;width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--amber);border-radius:50%;animation:sp .6s linear infinite;vertical-align:middle}
@keyframes sp{to{transform:rotate(360deg)}}
.status-msg{font-size:13px;color:var(--text2);min-height:20px}
/* STRUCTURED QUESTION SECTIONS */
.qs-sec{margin-bottom:24px}
.qs-sec-hdr{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:var(--amber);padding:6px 0 10px;border-bottom:1px solid var(--border);margin-bottom:14px}
.qi-blk{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:8px;display:flex;align-items:flex-start;gap:12px;transition:border-color .15s}
.qi-blk:hover{border-color:var(--amber)}
.qi-num{width:22px;height:22px;border-radius:50%;background:linear-gradient(135deg,var(--amber),#F59E0B);color:#fff;font-size:11px;font-weight:900;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.qi-body{flex:1;min-width:0}
.qi-title{font-size:13px;font-weight:700;color:var(--text);margin-bottom:8px}
.qi-inp{background:var(--white);border:1px solid var(--border);border-radius:8px;padding:7px 10px;color:var(--text);font-size:13px;font-family:inherit;outline:none;transition:border-color .15s}
.qi-inp:focus{border-color:var(--amber)}
.qi-ta{width:100%;background:var(--white);border:1px solid var(--border);border-radius:8px;padding:8px 10px;color:var(--text);font-size:13px;font-family:inherit;outline:none;resize:vertical;min-height:60px;transition:border-color .15s}
.qi-ta:focus{border-color:var(--amber)}
.qi-sel{background:var(--white);border:1px solid var(--border);border-radius:8px;padding:7px 10px;color:var(--text);font-size:13px;outline:none;cursor:pointer}
.qi-sel:focus{border-color:var(--amber)}
.qi-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.qi-lbl{font-size:11px;color:var(--text2);white-space:nowrap}
.qi-chip{display:inline-flex;align-items:center;gap:5px;background:var(--white);border:1px solid var(--border);border-radius:20px;padding:4px 12px;font-size:11px;font-weight:600;color:var(--text2);cursor:pointer;transition:all .15s;user-select:none}
.qi-chip.on{background:var(--primary-light);border-color:var(--primary2);color:var(--primary2)}
.qi-skill-tag{background:var(--primary-light);border:1px solid var(--primary-border);border-radius:6px;padding:2px 8px;font-size:11px;color:var(--primary2);display:inline-block;margin-right:4px;margin-bottom:4px}
.qi-iq{font-size:12px;color:var(--text2);font-style:italic;border-left:2px solid #FDE68A;padding:6px 10px;margin-bottom:12px;line-height:1.55;background:var(--amber-light);border-radius:0 6px 6px 0}
.qi-fields-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.qi-field-grp{display:flex;flex-direction:column;gap:4px}
.qi-field-lbl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:var(--text2)}
.qi-sel-full{width:100%;background:var(--white);border:1px solid var(--border);border-radius:8px;padding:7px 10px;color:var(--text);font-size:13px;outline:none;cursor:pointer;transition:border-color .15s}
.qi-sel-full:focus{border-color:var(--amber)}
.qi-prof-chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.qi-prof-chip{padding:5px 14px;border-radius:20px;font-size:11px;font-weight:700;cursor:pointer;border:1px solid var(--border);background:var(--white);color:var(--text2);transition:all .15s;user-select:none}
.qi-prof-chip[data-level="Beginner"].on{background:#EFF6FF;border-color:#60A5FA;color:#2563EB}
.qi-prof-chip[data-level="Intermediate"].on{background:#DCFCE7;border-color:#4ADE80;color:#16A34A}
.qi-prof-chip[data-level="Advanced"].on{background:var(--amber-light);border-color:#F59E0B;color:var(--amber)}
.qi-prof-chip[data-level="Expert"].on{background:#FDF2F8;border-color:#F472B6;color:#DB2777}
.qi-resp-lbl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:var(--text2);margin-bottom:5px}
.qi-resume-meta{display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.qi-badge{display:inline-flex;align-items:center;font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;letter-spacing:.04em;white-space:nowrap}
.qi-badge-yoe{background:var(--primary-light);border:1px solid var(--primary-border);color:var(--primary2)}
.qi-badge-recent{background:#DCFCE7;border:1px solid #BBF7D0;color:#16A34A}
.qi-badge-mid{background:var(--amber-light);border:1px solid #FDE68A;color:var(--amber)}
.qi-badge-old{background:var(--red-light);border:1px solid #FECACA;color:var(--red)}
.qi-badge-evl{background:var(--bg);border:1px solid var(--border);color:var(--text2)}
.qi-ask{background:var(--primary-light);border:1px solid var(--primary-border);border-left:3px solid var(--primary2);border-radius:0 8px 8px 0;padding:10px 12px;margin-bottom:12px}
.qi-ask-hdr{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--primary2);margin-bottom:5px}
.qi-ask-q{font-size:12.5px;color:var(--text);line-height:1.6}
</style>
</head><body>__SIDEBAR_HTML__
<div class="main-content"><div class="wrap">

<!-- HERO CARD -->
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px">
  <div><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--text2)">Phone Screen</div><div style="font-size:10px;color:var(--text2);margin-top:2px"><a href="/" style="color:var(--text2)">Dashboard</a> &rsaquo; Recruiter Screen</div></div>
  <a href="/panel-screen/__CID__" class="nav-item" style="background:var(--primary);color:#fff;font-weight:700;border-radius:10px;padding:8px 16px">Panel Interview &#8594;</a>
</div>
<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--amber),#F59E0B);height:3px;position:absolute;top:0;left:0;right:0;border-radius:12px 12px 0 0"></div>
  <div class="kicker">Phone Screen &mdash; Stage 2 of 4</div>
  <div class="hero-row">
    <div style="flex:1;min-width:0">
      <div class="cname" id="cName">Loading&hellip;</div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:8px" id="cMeta"></div>
      <div class="pills" id="pillRow"></div>
      <div class="narrative" id="narrative"></div>
    </div>
    <div class="score-badge">
      <div class="score-val" id="scoreLive">&mdash;</div>
      <div class="score-lbl" id="scoreLbl">Resume Score</div>
    </div>
  </div>
  <div class="score-tiles">
    <div class="stile">
      <div class="stile-val" id="tExp">&mdash;</div>
      <div class="stile-lbl">Experience <span style="color:var(--muted)">/ 40</span></div>
      <div class="stile-sub" id="tExpPend"></div>
    </div>
    <div class="stile">
      <div class="stile-val" id="tSkills">&mdash;</div>
      <div class="stile-lbl">Skills <span style="color:var(--muted)">/ 45</span></div>
      <div class="stile-sub" id="tSkillsPend"></div>
    </div>
    <div class="stile">
      <div class="stile-val" id="tEdu">&mdash;</div>
      <div class="stile-lbl">Education <span style="color:var(--muted)">/ 15</span></div>
      <div class="stile-sub" id="tEduPend"></div>
    </div>
  </div>
</div>

<!-- QUESTIONS CARD -->
<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--warn),#ff9f43)"></div>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:10px">
    <div>
      <div class="kicker">Step 1 &mdash; Phone Screen Questions</div>
      <div style="font-size:15px;font-weight:800">Answer each question below, then score</div>
      <div style="font-size:12px;color:var(--muted);margin-top:3px">Questions are tailored to this candidate&#39;s resume. Type the candidate&#39;s answers as they respond.</div>
    </div>
    <button onclick="loadQs()" style="background:var(--amber-light);color:var(--amber);border:1px solid #FDE68A;border-radius:10px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer">&#8635; Regenerate</button>
  </div>
  <div id="qArea"><div style="text-align:center;padding:40px 0;color:var(--muted)"><span class="spin"></span>&nbsp; Generating questions&hellip;</div></div>
  <div style="margin-top:20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    <button class="btn-score" id="scoreBtn" onclick="scoreAll()" disabled>Score Answers &amp; Update Score &#8594;</button>
    <span class="status-msg" id="statusMsg"></span>
  </div>
  <!-- Updated score panel -->
  <div class="updated-panel" id="updatedPanel">
    <div style="font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#16A34A;margin-bottom:4px">&#10003; Score Updated</div>
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px">
      <div class="new-score-val" id="newScoreVal">&mdash;</div>
      <div style="font-size:16px;color:var(--muted)">/ 100 &mdash; after phone screen</div>
    </div>
    <div style="font-size:13px;color:var(--text2);margin-bottom:20px" id="scoreDelta"></div>
    <a href="/panel-screen/__CID__" class="btn-score" style="text-decoration:none;background:linear-gradient(135deg,var(--primary2),var(--purple))">Proceed to Panel Interview &#8594;</a>
  </div>
</div>

</div><!-- /wrap -->

<script>
const CID='__CID__';
let _analysis=null, _qs=[], _scored={};

function esc(x){return x==null?'':String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function pLabel(k){return({mentorship_signal:'Mentorship',international_exposure:'International Exposure',stakeholder_management:'Stakeholder Mgmt',project_explanation:'Project Walk-through',linkedin_activity:'LinkedIn',extra_curriculars:'Extra-curriculars',skill_depth:'Skill Depth',skill_recency:'Recency',certifications:'Certifications',coding_community:'Community',career_progression:'Progression',stability:'Stability',company_tier:'Company Tier',communication_skills:'Communication',domain_skills:'Domain',problem_solving:'Problem Solving'}[k]||k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase()));}

async function loadAll(){
  if(!CID){document.getElementById('cName').textContent='No candidate ID — please open from the Analysis page';return;}
  document.getElementById('cName').textContent='Loading '+CID+'…';
  /* 1. load score (last stage = most recent) */
  try{
    const r=await fetch('/candidateScore/'+encodeURIComponent(CID));
    if(r.ok){
      const d=await r.json();
      document.getElementById('cName').textContent=d.candidate_name||CID;
      document.getElementById('cMeta').textContent=CID;
      const stages=d.stages||[];
      const ss=(stages[stages.length-1]||{}).stage_scores||{};
      if(ss.resume_score_100!=null){document.getElementById('scoreLive').textContent=ss.resume_score_100;document.getElementById('scoreLbl').textContent='Resume Score';}
    }else{document.getElementById('cMeta').textContent='Score fetch: HTTP '+r.status;}
  }catch(e){document.getElementById('cMeta').textContent='Score fetch error: '+e;}
  /* 2. load analysis */
  try{
    const r=await fetch('/api/candidateAnalysis/'+encodeURIComponent(CID));
    if(!r.ok){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:14px">Analysis not found (HTTP '+r.status+'). Please re-upload the resume on the Analysis page first.</div>';return;}
    _analysis=await r.json();
    /* candidate name from overview if not set */
    const ovName=_analysis.candidate_overview?.name||'';
    if(ovName)document.getElementById('cName').textContent=ovName;
    /* hero pills: role family, DNA, exp count */
    const rf=((_analysis.semantic_analysis?.role_family_scores||[])[0]||{}).role_family||'';
    const dna=_analysis.dna_fit?.primary_dna||_analysis.dna_classification?.primary||'';
    const expItems=(_analysis.experience_analysis?.items||_analysis.experience?.items||[]);
    const pills=[];
    if(rf)pills.push('<span class="pill pill-g">'+rf.replace(/_/g,' ')+'</span>');
    if(dna)pills.push('<span class="pill pill-w">'+dna+'</span>');
    if(expItems.length)pills.push('<span class="pill pill-b">'+expItems.length+' roles</span>');
    document.getElementById('pillRow').innerHTML=pills.join('');
    /* narrative from overview summary */
    document.getElementById('narrative').textContent=_analysis.recruiter_narrative||_analysis.candidate_overview?.profile_summary||'';
    /* score tiles: use top-level rubric scores (not breakdown sub-objects) */
    const rs=_analysis.rubric_scorecard||{};
    const ss2=rs.stage_scores||{};
    if(rs.experience_score!=null){document.getElementById('tExp').textContent=rs.experience_score;}
    if(rs.skills_score!=null){document.getElementById('tSkills').textContent=rs.skills_score;}
    if(rs.education_score!=null){document.getElementById('tEdu').textContent=rs.education_score;}
    if(rs.total_score!=null){document.getElementById('scoreLive').textContent=rs.total_score;document.getElementById('scoreLbl').textContent='Resume Score';}
    if(ss2.exp_recruiter_pending_pts)document.getElementById('tExpPend').textContent='+'+ss2.exp_recruiter_pending_pts+' recruiter pending';
    if(ss2.skills_recruiter_pending_pts)document.getElementById('tSkillsPend').textContent='+'+ss2.skills_recruiter_pending_pts+' recruiter pending';
    if(ss2.edu_recruiter_pending_pts)document.getElementById('tEduPend').textContent='+'+ss2.edu_recruiter_pending_pts+' recruiter pending';
    /* load questions */
    loadQs();
  }catch(e){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:14px">Error loading analysis: '+esc(String(e))+'</div>';}
}

function loadQs(){
  if(!_analysis){document.getElementById('qArea').innerHTML='<div style="color:var(--muted);padding:14px">Analysis not loaded yet.</div>';return;}
  _qs=[];_scored={};
  renderQs();
}

function renderQs(){
  const sa=_analysis.skill_analysis||_analysis.skills||{};
  const rs=_analysis.rubric_scorecard||{};
  const flags=rs.reject_flags||[];
  const expItems=(_analysis.experience_analysis&&_analysis.experience_analysis.items)||(_analysis.experience&&_analysis.experience.items)||[];
  /* Pick top 5 recruiter-relevant skills: prioritise RECENT + highest evidence level */
  const _EVLORD=['NONE','MENTION','WEAK','APPLIED','DEEP','EXPERT'];
  const _RECORD={'RECENT':2,'MID':1,'OLD':0,'UNKNOWN':-1};
  const _allSkills=(sa.top_skills||[]);
  const _strong=_allSkills.filter(function(sk){
    const evl=typeof sk==='string'?'APPLIED':(sk.evidence_level||'NONE');
    return _EVLORD.indexOf(evl)>=_EVLORD.indexOf('APPLIED');
  }).sort(function(a,b){
    const ra=typeof a==='string'?'UNKNOWN':(a.recency||'UNKNOWN');
    const rb=typeof b==='string'?'UNKNOWN':(b.recency||'UNKNOWN');
    const recDiff=(_RECORD[rb]||0)-(_RECORD[ra]||0);
    if(recDiff!==0)return recDiff;
    const evlA=typeof a==='string'?'APPLIED':(a.evidence_level||'NONE');
    const evlB=typeof b==='string'?'APPLIED':(b.evidence_level||'NONE');
    return _EVLORD.indexOf(evlB)-_EVLORD.indexOf(evlA);
  });
  const topSkills=(_strong.length>=3?_strong:_allSkills).slice(0,5);
  let num=0;
  function qb(id,title,inputHtml){num++;return '<div class="qi-blk"><div class="qi-num">'+num+'</div><div class="qi-body"><div class="qi-title">'+title+'</div>'+inputHtml+'</div></div>';}

  /* -- Section 1: General Information -- */
  const genQs=[
    {id:'gen_loc_curr',title:'Current Location',ph:'City / Country'},
    {id:'gen_loc_pref',title:'Preferred Location',ph:'Willing to relocate to?'},
    {id:'gen_ctc_curr',title:'Current CTC',ph:'Current package (e.g. 12 LPA)'},
    {id:'gen_ctc_exp',title:'Expected CTC',ph:'Expected package'},
    {id:'gen_workmode',title:'Work Mode Preference',ph:'Remote / Hybrid / In-office'},
    {id:'gen_company',title:'Company Type Preference',ph:'Startup / MNC / Product / Service'},
    {id:'gen_reason',title:'Reason for Change',ph:'Why looking for a new opportunity?'},
    {id:'gen_role',title:'Expected Role / Title',ph:'What role are they targeting?'},
    {id:'gen_offers',title:'Active Offers / Notice Period',ph:'Competing offers? Notice period?'}
  ];
  let s1='<div class="qs-sec"><div class="qs-sec-hdr">&#128204; Section 1 &mdash; General Information</div>';
  genQs.forEach(function(q){
    _qs.push({id:q.id,section:'general',question:q.title,rubric_param:'',skill:'',label:q.title});
    s1+=qb(q.id,q.title,'<input type="text" class="qi-inp" style="width:100%" id="'+q.id+'" placeholder="'+q.ph+'" oninput="checkReady()">');
  });
  s1+='</div>';

  /* -- Section 2: Missing Information -- */
  let s2='<div class="qs-sec"><div class="qs-sec-hdr">&#9888; Section 2 &mdash; Missing Information</div>';
  const missList=[];
  if(flags.length){flags.forEach(function(f){missList.push({q:String(f),rp:'career_progression'});});}
  if(expItems.length>=1)missList.push({q:'Walk me through your most impactful project at '+esc(expItems[0].company||'your last role'),rp:'project_explanation'});
  if(expItems.length>=2)missList.push({q:'Describe a key project at '+esc(expItems[1].company||'your second role'),rp:'project_explanation'});
  if(!missList.length){s2+='<div style="color:var(--accent);font-size:13px;padding:8px 4px">&#10003; No significant gaps detected in the resume.</div>';}
  else{
    missList.forEach(function(m,i){
      const tid='miss_'+i;
      _qs.push({id:tid,section:'missing',question:m.q,rubric_param:m.rp||'',skill:'',label:m.q});
      s2+=qb(tid,m.q,'<textarea class="qi-ta" id="'+tid+'" rows="2" placeholder="Candidate response..." oninput="checkReady()"></textarea>');
    });
  }
  s2+='</div>';

  /* -- Section 3: Skill-Based Questions -- */
  const SKILL_IQ={
    'Leadership':'Tell me about the most complex cross-functional initiative you led in the past year. Walk me through your ownership, the obstacles you hit, and the measurable outcome.',
    'Operational Excellence':'Walk me through a process or system you significantly improved. What was broken, what did you change, and how did you measure success?',
    'Logistics Analytics':'Describe a logistics or supply-chain analytics problem you solved end-to-end. What data did you use, what was your approach, and what was the business impact?',
    'Process Transformation':'Tell me about a large-scale process transformation you drove. What was the before-state, what did you change, and what resistance did you face?',
    'Strategic Vision':'Walk me through a strategic decision you owned or significantly influenced. How did you build the case, who did you align, and what happened?',
    'Growth Analytics':'Describe the most impactful growth analysis you ran. How did you find the insight, what did you do with it, and how did you measure the result?',
    'Forecasting':'Walk me through a forecasting model you built end-to-end. What method, horizon, and accuracy did you achieve — and how was it used in production decisions?',
    'Scalable Systems':'Describe the most scalable system you designed. What were the actual bottlenecks, what trade-offs did you make, and what scale did you reach?',
    'AWS':'Walk me through a recent architecture you built on AWS. What services did you use, why, and how did you handle failure modes?',
    'Data Pipelines':'Walk me through a complex data pipeline you owned. How did you handle late data, failures, and quality validation in production?',
    'PySpark':'What is the largest PySpark job you have run? Walk me through the problem, your optimisation approach, and any skew or shuffle issues you faced.',
    'Machine Learning':'Walk me through the last ML model you shipped to production. How did you evaluate it, monitor it, and what broke first?',
    'SQL':'Describe the most complex query or data transformation you wrote. What was the business problem and what made it hard?',
    'Python':'What is the most production-critical Python code you have written? How did you test it, deploy it, and handle failures?',
    'Deep Learning':'Tell me about the most complex deep learning model you worked with. How did you train, tune, and deploy it?',
    'NLP':'Describe an NLP project you owned. What models, evaluation metrics, and edge cases did you have to handle?',
    'Product Management':'Walk me through a product you owned from ideation to launch. How did you prioritise, what trade-offs did you make, and how did you measure success?',
    'Analytics':'Describe an analysis that directly changed a business decision. How did you ensure it was correct and how did you communicate it?',
    'Data Engineering':'Walk me through a data warehouse or lakehouse design you built. What schema choices did you make and why?',
  };
  function skillIQ(name){
    return SKILL_IQ[name]||'Walk me through your most recent work with '+name+' in a live production or business context. What was your specific contribution, what challenges did you face, and what was the outcome?';
  }

  /* Helpers to pre-select dropdowns from resume data */
  function _yoeVal(yrs){
    if(!yrs||yrs<=0)return '';
    if(yrs<1)return '<1';
    if(yrs<1.8)return '1';
    if(yrs<2.8)return '2';
    if(yrs<3.8)return '3';
    if(yrs<4.8)return '4';
    if(yrs<5.8)return '5';
    if(yrs<=8.5)return '6-8';
    if(yrs<=12)return '9-12';
    return '12+';
  }
  function _lastUsedVal(recency,months){
    if(!recency||recency==='UNKNOWN')return '';
    if(recency==='RECENT'){
      if(!months||months<=3)return 'Current';
      if(months<=6)return '<6mo';
      return '6-12mo';
    }
    if(recency==='MID'){return months&&months<=24?'1-2yr':'2-3yr';}
    return '3+yr';
  }
  function _recencyBadge(recency){
    if(recency==='RECENT')return '<span class="qi-badge qi-badge-recent">RECENT</span>';
    if(recency==='MID')return '<span class="qi-badge qi-badge-mid">MID</span>';
    if(recency==='OLD')return '<span class="qi-badge qi-badge-old">OLD</span>';
    return '';
  }
  function _evlBadge(evl){
    if(!evl||evl==='NONE')return '';
    return '<span class="qi-badge qi-badge-evl">'+evl+'</span>';
  }

  let s3='<div class="qs-sec"><div class="qs-sec-hdr">&#128295; Section 3 &mdash; Skill-Based Questions</div>';
  if(topSkills.length){
    topSkills.forEach(function(sk,si){
      const skObj=typeof sk==='string'?{}:sk;
      const sname=esc(skObj.skill||skObj.name||(typeof sk==='string'?sk:('Skill '+(si+1))));
      const resumeYoe=skObj.years_of_usage||0;
      const recency=skObj.recency||'UNKNOWN';
      const recencyMonths=skObj.recency_months||null;
      const evl=skObj.evidence_level||'';
      const yid='skill_'+si+'_yoe',pid='skill_'+si+'_prof',lid='skill_'+si+'_last',nid='skill_'+si+'_note';
      _qs.push({id:nid,section:'skill',question:'Experience and proficiency in '+sname,rubric_param:'skill_depth',skill:sname,label:sname+' experience'});

      const preYoe=_yoeVal(resumeYoe);
      const preLastUsed=_lastUsedVal(recency,recencyMonths);

      const yoeOpts=['<option value="">Confirm</option>',
        '<option value="<1"'+(preYoe==='<1'?' selected':'')+'>Less than 1 yr</option>',
        '<option value="1"'+(preYoe==='1'?' selected':'')+'>1 year</option>',
        '<option value="2"'+(preYoe==='2'?' selected':'')+'>2 years</option>',
        '<option value="3"'+(preYoe==='3'?' selected':'')+'>3 years</option>',
        '<option value="4"'+(preYoe==='4'?' selected':'')+'>4 years</option>',
        '<option value="5"'+(preYoe==='5'?' selected':'')+'>5 years</option>',
        '<option value="6-8"'+(preYoe==='6-8'?' selected':'')+'>6\u20138 years</option>',
        '<option value="9-12"'+(preYoe==='9-12'?' selected':'')+'>9\u201312 years</option>',
        '<option value="12+"'+(preYoe==='12+'?' selected':'')+'>12+ years</option>',
      ].join('');

      const lastOpts=['<option value="">Confirm</option>',
        '<option value="Current"'+(preLastUsed==='Current'?' selected':'')+'>Currently using</option>',
        '<option value="<6mo"'+(preLastUsed==='<6mo'?' selected':'')+'>Less than 6 months ago</option>',
        '<option value="6-12mo"'+(preLastUsed==='6-12mo'?' selected':'')+'>6\u201312 months ago</option>',
        '<option value="1-2yr"'+(preLastUsed==='1-2yr'?' selected':'')+'>1\u20132 years ago</option>',
        '<option value="2-3yr"'+(preLastUsed==='2-3yr'?' selected':'')+'>2\u20133 years ago</option>',
        '<option value="3+yr"'+(preLastUsed==='3+yr'?' selected':'')+'>3+ years ago</option>',
      ].join('');

      s3+='<div class="qi-blk">'
        +'<div class="qi-num" style="background:linear-gradient(135deg,var(--primary2),var(--purple));color:#fff">'+(si+1)+'</div>'
        +'<div class="qi-body">'
        +'<div class="qi-title"><span class="qi-skill-tag">'+sname+'</span></div>'
        +'<div class="qi-resume-meta">'
        +(resumeYoe>0?'<span class="qi-badge qi-badge-yoe">Resume: '+resumeYoe+' yrs</span>':'')
        +_recencyBadge(recency)
        +_evlBadge(evl)
        +'</div>'
        +'<div class="qi-ask">'
        +'<div class="qi-ask-hdr">&#127908; Ask the candidate</div>'
        +'<div class="qi-ask-q">'+skillIQ(sname)+'</div>'
        +'</div>'
        +'<div class="qi-fields-row">'
        +'<div class="qi-field-grp"><span class="qi-field-lbl">Years of Experience</span>'
        +'<select class="qi-sel-full" id="'+yid+'" onchange="checkReady()">'+yoeOpts+'</select></div>'
        +'<div class="qi-field-grp"><span class="qi-field-lbl">Last Used</span>'
        +'<select class="qi-sel-full" id="'+lid+'" onchange="checkReady()">'+lastOpts+'</select></div>'
        +'</div>'
        +'<div class="qi-field-lbl" style="margin-bottom:6px">Proficiency</div>'
        +'<div class="qi-prof-chips">'
        +['Beginner','Intermediate','Advanced','Expert'].map(function(lv){return '<span class="qi-prof-chip" data-level="'+lv+'" data-sel="'+pid+'" onclick="selectProf(this)">'+lv+'</span>';}).join('')
        +'</div>'
        +'<select style="display:none" id="'+pid+'"><option value="">Select</option><option>Beginner</option><option>Intermediate</option><option>Advanced</option><option>Expert</option></select>'
        +'<div class="qi-resp-lbl">Recruiter Notes on Response</div>'
        +'<textarea class="qi-ta" id="'+nid+'" rows="3" placeholder="Was the answer specific? Did they own it clearly? Any red flags or strong signals..." oninput="checkReady()"></textarea>'
        +'</div></div>';
    });
  }
  const cpid='cloud_ans';
  _qs.push({id:cpid,section:'skill',question:'Cloud platform experience and certifications',rubric_param:'skill_depth',skill:'Cloud',label:'Cloud platforms'});
  s3+='<div class="qi-blk">'
    +'<div class="qi-num" style="background:linear-gradient(135deg,var(--primary2),var(--purple));color:#fff">'+(topSkills.length+1)+'</div>'
    +'<div class="qi-body">'
    +'<div class="qi-title">Cloud Platforms</div>'
    +'<div class="qi-row" style="margin-bottom:10px">'
    +'<span class="qi-chip" id="chip_aws" onclick="toggleChip(this)">AWS</span>'
    +'<span class="qi-chip" id="chip_azure" onclick="toggleChip(this)">Azure</span>'
    +'<span class="qi-chip" id="chip_gcp" onclick="toggleChip(this)">GCP</span>'
    +'<span class="qi-chip" id="chip_k8s" onclick="toggleChip(this)">Kubernetes</span>'
    +'<span class="qi-chip" id="chip_docker" onclick="toggleChip(this)">Docker</span>'
    +'</div>'
    +'<textarea class="qi-ta" id="'+cpid+'" rows="2" placeholder="Cloud experience, certifications, years of use..." oninput="checkReady()"></textarea>'
    +'</div></div>';
  s3+='</div>';

  /* -- Section 4: Recruiter Rubric Params (scored directly during call) -- */
  const rParams=[
    {key:'mentorship_signal',label:'Mentorship / Code Reviews',max:3,help:'3=clear lead/mentor; 2=code reviews; 1=implied; 0=IC only.',guide:'Ask: Have you mentored junior engineers or conducted code reviews?'},
    {key:'international_exposure',label:'International Exposure',max:2,help:'2=onsite abroad or sustained global work; 1=cross-timezone; 0=local.',guide:'Ask: Have you worked with global teams or clients?'},
    {key:'stakeholder_management',label:'Stakeholder Management',max:2,help:'2=client-facing or C-level; 1=cross-functional; 0=none.',guide:'Ask: Who did you interact with outside your immediate team?'},
    {key:'project_explanation',label:'Project Explanation Skills',max:3,help:'3=structured narrative; 2=good; 1=gaps; 0=cannot explain.',guide:'Ask: Walk me through your most complex project end-to-end.'},
    {key:'linkedin_activity',label:'LinkedIn Activeness',max:1,help:'1=active consistent profile; 0=absent/inactive.',guide:'Check LinkedIn before/during call.'},
  ];
  let s4='<div class="qs-sec"><div class="qs-sec-hdr">&#127775; Section 4 \u2014 Recruiter Rubric Params</div>';
  s4+='<div style="font-size:12px;color:var(--text2);margin-bottom:14px">Score these 5 parameters directly during the call. They update the Recruiter-stage /100 when you submit.</div>';
  s4+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">';
  rParams.forEach(function(p){
    s4+='<div style="background:var(--white);border:1px solid var(--border);border-radius:12px;padding:13px">'
      +'<div style="display:flex;justify-content:space-between;margin-bottom:5px">'
      +'<b style="font-size:13px">'+p.label+'</b>'
      +'<span style="font-size:11px;color:var(--amber);font-weight:700">max '+p.max+'</span></div>'
      +'<div style="font-size:12px;color:var(--text2);margin-bottom:6px">'+p.help+'</div>'
      +'<div style="background:var(--amber-light);border-left:2px solid var(--amber);border-radius:0 6px 6px 0;'
      +'padding:6px 9px;font-size:11px;color:var(--text2);margin-bottom:8px">'+p.guide+'</div>'
      +'<div style="display:flex;align-items:center;gap:7px">'
      +'<span style="font-size:12px;color:var(--text2)">Score:</span>'
      +'<input type="number" id="rp_'+p.key+'" min="0" max="'+p.max+'" step="'+(p.max===1?'1':'0.5')+'"'
      +' style="width:75px;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:5px 9px;'
      +'font-size:14px;font-weight:700;color:var(--text)" oninput="checkReady()">'
      +'<span style="font-size:11px;color:var(--text2)">/ '+p.max+'</span>'
      +'</div></div>';
  });
  s4+='</div></div>';

  document.getElementById('qArea').innerHTML=s1+s2+s3+s4;
  document.getElementById('scoreBtn').disabled=false;
}

function toggleChip(el){el.classList.toggle('on');}

function selectProf(el){
  const pid=el.dataset.sel;
  el.parentElement.querySelectorAll('.qi-prof-chip').forEach(function(c){c.classList.remove('on');});
  el.classList.add('on');
  const sel=document.getElementById(pid);
  if(sel)sel.value=el.dataset.level;
  checkReady();
}

function getAns(q){
  if(q.section==='skill'&&q.id!=='cloud_ans'&&q.id.slice(-5)==='_note'){
    const base=q.id.slice(0,-5);
    const yoe=(document.getElementById(base+'_yoe')?document.getElementById(base+'_yoe').value:'').trim();
    const prof=(document.getElementById(base+'_prof')?document.getElementById(base+'_prof').value:'');
    const last=(document.getElementById(base+'_last')?document.getElementById(base+'_last').value:'').trim();
    const note=(document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
    const parts=[];
    if(yoe)parts.push(yoe+' years');
    if(prof)parts.push(prof+' proficiency');
    if(last)parts.push('last used: '+last);
    if(note)parts.push(note);
    return parts.join(', ');
  }
  if(q.id==='cloud_ans'){
    const chips=[];
    ['chip_aws','chip_azure','chip_gcp','chip_k8s','chip_docker'].forEach(function(cid){
      const el=document.getElementById(cid);if(el&&el.classList.contains('on'))chips.push(el.textContent.trim());
    });
    const note=(document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
    return (chips.length?chips.join(', ')+'. ':'')+note;
  }
  return (document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
}

function checkReady(){document.getElementById('scoreBtn').disabled=false;}

async function scoreAll(){
  const btn=document.getElementById('scoreBtn');
  btn.disabled=true;btn.innerHTML='<span class="spin"></span>&nbsp; Scoring…';
  document.getElementById('statusMsg').textContent='Scoring answers…';
  _scored={};
  /* Build recruiter notes from general info fields */
  const notes=_qs.filter(function(q){return q.section==='general';}).map(function(q){
    const v=(document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
    return v?q.label+': '+v:'';
  }).filter(Boolean).join(' | ');
  /* Score non-general questions */
  const toScore=_qs.filter(function(q){return q.section!=='general';});
  const promises=toScore.map(async function(q,i){
    const ans=getAns(q);if(!ans)return;
    try{
      const r=await fetch('/scoreQuestionAnswer',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({question:q.question,theme:q.rubric_param||'',answer_transcript:ans,skill:q.skill||'',candidate_context:'',candidate_id:CID})});
      const sc=await r.json();_scored[i]=sc;
    }catch(e){}
  });
  await Promise.all(promises);
  document.getElementById('statusMsg').textContent='Applying scores to rubric…';
  const scoredArr=Object.values(_scored);
  let overrides={};
  if(scoredArr.length){
    try{
      const r=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({candidate_id:CID,question_scores:scoredArr,stage:'recruiter',recruiter_notes:notes})});
      const d=await r.json();
      overrides=d.rubric_param_overrides||{};
    }catch(e){}
  }
  /* Merge manually-entered rubric params into overrides */
  const _rpKeys=['mentorship_signal','international_exposure','stakeholder_management','project_explanation','linkedin_activity'];
  _rpKeys.forEach(function(k){const el=document.getElementById('rp_'+k);if(el&&el.value!=='')overrides[k]=parseFloat(el.value)||0;});
  document.getElementById('statusMsg').textContent='Saving updated score…';
  try{
    const r=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({candidate_id:CID,stage:'recruiter',stage_overrides:overrides,recruiter_notes:notes})});
    const d=await r.json();
    if(r.ok){
      const s100=d.stage_score_100??d.new_total;
      const prev=parseFloat(document.getElementById('scoreLive').textContent)||0;
      document.getElementById('scoreLive').textContent=s100;
      document.getElementById('scoreLbl').textContent='After Phone Screen';
      document.getElementById('newScoreVal').textContent=s100;
      const diff=Math.round((s100-prev)*10)/10;
      document.getElementById('scoreDelta').textContent='Resume score was '+prev+' now '+s100+'/100 ('+(diff>=0?'+':'')+diff+' pts from recruiter screen)';
      document.getElementById('updatedPanel').style.display='';
      document.getElementById('statusMsg').textContent='';
      try{await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'Telephonic'})});}catch(e){}
    }else{
      document.getElementById('statusMsg').textContent='Save error: '+(d.detail||r.statusText);
    }
  }catch(e){document.getElementById('statusMsg').textContent='Error: '+e;}
  btn.innerHTML='Score &amp; Update Score &#8594;';
  btn.disabled=false;
}

loadAll();
</script>
</div></div></body></html>""").replace("__CID__", cid_js).replace("__SIDEBAR_HTML__", _sidebar("upload", user))
    return _HR(content=_html, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Panel Screen — panel interview scoring page (dark theme, blue accent)
# ---------------------------------------------------------------------------

@app.get("/panel-screen/{candidate_id}", response_class=HTMLResponse)
def panel_screen(candidate_id: str, user: dict = Depends(get_current_user)):  # noqa: C901
    cid_js = candidate_id.replace("'", "\'")
    _html = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Panel Interview &mdash; Resume Intelligence</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#F9FAFB;--white:#FFFFFF;--border:#CAD5E2;--text:#262626;--text2:#62748E;--primary:#353395;--primary2:#6366F1;--primary-light:#F0F0FB;--primary-border:#E0E0F5;--amber:#D97706;--amber-light:#FEF3C7;--red:#DC2626;--red-light:#FEE2E2;--purple:#7C3AED;--card:#FFFFFF;--line:#CAD5E2;--muted:#62748E;--accent:#353395;--accent2:#6366F1;--warn:#D97706;--danger:#DC2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Aptos,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--primary2);text-decoration:none}
.app-shell{display:flex;min-height:100vh}
.sidebar{width:240px;min-height:100vh;background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column;position:fixed;left:0;top:0;z-index:100}
.sidebar-logo{padding:14px 16px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.sidebar-logo-icon{width:32px;height:32px;background:linear-gradient(135deg,var(--primary),var(--primary2));border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:800;flex-shrink:0}
.sidebar-brand{font-size:13px;font-weight:800;color:var(--text)}
.sidebar-nav{flex:1;padding:8px;overflow-y:auto}
.nav-section-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);padding:10px 8px 4px}
.nav-item{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:8px;font-size:13px;font-weight:500;color:var(--text2);cursor:pointer;text-decoration:none;transition:background .12s,color .12s;white-space:nowrap}
.nav-item:hover{background:var(--bg);color:var(--text)}
.nav-item.active{background:var(--primary-light);color:var(--primary);font-weight:600}
.sidebar-footer{padding:8px;border-top:1px solid var(--border)}
.main-content{margin-left:240px;flex:1;min-height:100vh}
.stage-strip{background:var(--white);border-bottom:1px solid var(--border)}
.stage-inner{max-width:1160px;margin:0 auto;padding:10px 24px;display:flex;align-items:center}
.st{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;color:var(--muted)}
.st-dot{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;flex-shrink:0;border:2px solid var(--line)}
.st.done .st-dot{background:linear-gradient(135deg,var(--primary),var(--primary2));color:#fff;border-color:transparent}
.st.done{color:var(--primary)}
.st.active .st-dot{background:linear-gradient(135deg,var(--primary2),var(--purple));color:#fff;border-color:transparent;box-shadow:0 0 14px rgba(99,102,241,.3)}
.st.active{color:var(--primary2)}
.st-line{flex:1;height:2px;margin:0 8px;border-radius:2px;background:var(--line)}
.st-line.lit{background:linear-gradient(90deg,var(--accent),var(--accent2))}
.wrap{max-width:1160px;margin:0 auto;padding:24px 24px 60px}
.card{background:var(--white);border:1px solid var(--border);border-radius:14px;padding:22px 26px;margin-bottom:14px;position:relative;overflow:hidden}
.abar{position:absolute;top:0;left:0;right:0;height:3px;border-radius:14px 14px 0 0}
.kicker{text-transform:uppercase;letter-spacing:.12em;font-size:10px;color:var(--text2);margin-bottom:7px}
.sec-title{font-size:15px;font-weight:800;color:var(--text);margin-bottom:3px}
.sec-sub{font-size:12px;color:var(--text2);line-height:1.5}
.hero-layout{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:start}
@media(max-width:640px){.hero-layout{grid-template-columns:1fr}}
.cand-name{font-size:30px;font-weight:900;letter-spacing:-.02em;color:var(--primary);margin-bottom:4px;line-height:1.1}
.cand-id{font-size:12px;color:var(--text2);margin-bottom:12px}
.pills{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:14px}
.pill{display:inline-flex;align-items:center;padding:5px 12px;border-radius:99px;font-size:12px;font-weight:700;background:var(--primary-light);border:1px solid var(--primary-border);color:var(--primary)}
.pill-g{color:#16A34A;background:#DCFCE7;border-color:#BBF7D0}.pill-b{color:var(--primary2);background:var(--primary-light);border-color:var(--primary-border)}.pill-w{color:var(--amber);background:var(--amber-light);border-color:#FDE68A}
.score-aside{display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.score-box{background:var(--white);border:1px solid var(--border);border-radius:14px;padding:12px 18px;text-align:center;min-width:100px}
.sv{font-size:24px;font-weight:900;line-height:1}.sv-a{color:#16A34A}.sv-w{color:var(--amber)}.sv-b{color:var(--primary2)}.sv-m{color:var(--text2)}
.s-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-top:3px}
.adds-box{background:var(--primary-light);border:1px solid var(--primary-border);border-radius:12px;padding:8px 14px;text-align:center}
.adds-v{display:block;font-size:18px;font-weight:900;color:var(--primary2)}.adds-l{font-size:10px;color:var(--text2)}
.rec-notes-box{background:var(--amber-light);border:1px solid #FDE68A;border-radius:14px;padding:14px 18px;margin-bottom:12px}
.skills-probe{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border);font-size:12px}.skills-probe:last-child{border:none}
.q-card{background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin-bottom:10px;transition:border-color .15s}
.q-card:hover{border-color:var(--primary2)}
.q-top{display:flex;gap:10px}
.q-num{width:26px;height:26px;border-radius:50%;background:var(--primary-light);color:var(--primary2);font-size:11px;font-weight:800;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.q-body{flex:1}
.q-text{font-size:13px;font-weight:700;color:var(--text);line-height:1.55}
.q-sub{font-size:11px;color:var(--text2);margin-top:4px}
.tags{display:flex;gap:5px;flex-wrap:wrap;margin:7px 0 0}
.tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;text-transform:uppercase}
.tgh{background:var(--red-light);color:var(--red);border:1px solid #FECACA}
.tgm{background:var(--primary-light);color:var(--primary2);border:1px solid var(--primary-border)}
.tgl{background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0}
.tgp{background:var(--bg);color:var(--text2);border:1px solid var(--border)}
.tgs{background:#F3E8FF;color:var(--purple);border:1px solid #E9D5FF}
.ans{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:9px 13px;font-size:12px;font-family:inherit;color:var(--text);resize:vertical;margin-top:10px;transition:border .15s}
.ans:focus{outline:none;border-color:var(--primary2)}
.sres{background:var(--bg);border:1px solid var(--border);border-radius:11px;padding:11px 14px;margin-top:9px;font-size:12px;color:var(--text)}
.apply-bar{background:var(--primary-light);border:1px solid var(--primary-border);border-radius:12px;padding:11px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}
.param-card{background:var(--white);border:1px solid var(--border);border-left:3px solid var(--primary2);border-radius:14px;padding:16px 20px;margin-bottom:10px;transition:border-color .2s,box-shadow .2s}
.param-card:hover{border-color:var(--primary2);box-shadow:0 4px 20px rgba(99,102,241,.08)}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}
.pname{font-size:13px;font-weight:800;color:var(--text)}
.pval{font-size:24px;font-weight:900;color:var(--primary2);line-height:1}.pmax{font-size:12px;color:var(--text2)}
.phelp{font-size:11px;color:var(--text2);line-height:1.5;margin:5px 0 6px}
.pguide{font-size:11px;color:var(--primary2);background:var(--primary-light);border-radius:8px;padding:6px 10px;margin-bottom:10px;line-height:1.5}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:99px;background:var(--border);outline:none;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,var(--primary2),var(--purple));border:2px solid var(--white);box-shadow:0 0 10px rgba(99,102,241,.3);cursor:pointer;transition:transform .12s}
input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.3);box-shadow:0 0 16px rgba(99,102,241,.5)}
.fromq{display:inline-flex;align-items:center;gap:4px;background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:99px;padding:2px 8px;font-size:10px;font-weight:700;margin-left:7px;vertical-align:middle}
.total-box{background:var(--primary-light);border:1px solid var(--primary-border);border-radius:14px;padding:14px 20px;text-align:center}
.total-val{font-size:36px;font-weight:900;color:var(--primary2);line-height:1}
.total-lbl{font-size:11px;color:var(--text2);margin-top:4px}
.notes-ta{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:12px 16px;font-size:13px;font-family:inherit;color:var(--text);resize:vertical;min-height:80px;transition:border .15s}
.notes-ta:focus{outline:none;border-color:var(--primary2)}
.btn-save{background:linear-gradient(135deg,var(--primary2),var(--purple));color:#fff;border:none;border-radius:12px;padding:13px 30px;font-size:14px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:7px;transition:opacity .15s,transform .1s}
.btn-save:hover{opacity:.9;transform:translateY(-1px)}
.btn-success{background:linear-gradient(135deg,#16A34A,#4ADE80);color:#fff;border:none;border-radius:12px;padding:12px 24px;font-size:13px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.btn-ghost{background:var(--white);color:var(--text2);border:1px solid var(--border);border-radius:12px;padding:11px 20px;font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px;transition:border-color .15s,color .15s}
.btn-ghost:hover{border-color:var(--primary2);color:var(--primary2)}
.save-msg{font-size:13px;color:var(--text2)}
.success-panel{display:none;background:#DCFCE7;border:1px solid #BBF7D0;border-radius:16px;padding:22px 26px;margin-top:14px}
.final-grid{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.ftile{background:var(--white);border:1px solid var(--border);border-radius:14px;padding:14px 20px;text-align:center;min-width:100px}
.fv{font-size:28px;font-weight:900;color:var(--primary2);line-height:1}.fl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-top:4px}
.ref-sum{list-style:none;background:var(--white);border:1px solid var(--border);border-radius:14px;padding:13px 20px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:700;color:var(--text2)}
.ref-sum:hover{border-color:var(--primary2);color:var(--text)}
details[open] .ref-sum{border-radius:14px 14px 0 0;border-color:var(--primary2);color:var(--text)}
.ref-body{background:var(--bg);border:1px solid var(--border);border-top:none;border-radius:0 0 14px 14px;padding:16px 20px}
.rrow{display:flex;align-items:flex-start;gap:10px;padding:5px 0;border-bottom:1px solid var(--border);font-size:11px}.rrow:last-child{border:none}
.rn{min-width:140px;font-weight:600;padding-top:2px;color:var(--text)}
.rb{flex:0 0 70px;padding-top:5px}.rbt{height:3px;border-radius:99px;background:var(--border);overflow:hidden}.rbf{height:100%;border-radius:99px}
.rs{min-width:44px;font-weight:800;color:var(--text)}.rj{flex:1;color:var(--text2);font-size:10px;line-height:1.4}
.stag{display:inline-flex;padding:1px 6px;border-radius:99px;font-size:9px;font-weight:700;margin-left:4px;vertical-align:middle}
.sr{background:var(--amber-light);color:var(--amber);border:1px solid #FDE68A}.sp{background:var(--primary-light);color:var(--primary2);border:1px solid var(--primary-border)}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--primary2);border-radius:50%;animation:sp .6s linear infinite;vertical-align:middle}
@keyframes sp{to{transform:rotate(360deg)}}
</style>
</head><body>
<div class="app-shell">__SIDEBAR_HTML__<div class="main-content">

<div class="stage-strip"><div class="stage-inner">
  <div class="st done"><div class="st-dot">&#10003;</div><span>Resume Analysis</span></div>
  <div class="st-line lit"></div>
  <div class="st done"><div class="st-dot">&#10003;</div><span>Phone Screen</span></div>
  <div class="st-line lit"></div>
  <div class="st active"><div class="st-dot">3</div><span>Panel Interview</span></div>
  <div class="st-line"></div>
  <div class="st"><div class="st-dot">4</div><span>Decision</span></div>
</div></div>

<div class="wrap">

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div class="hero-layout">
    <div>
      <div class="kicker">Panel Interview &mdash; Stage 3 of 4</div>
      <div class="cand-name" id="cName">Loading&hellip;</div>
      <div class="cand-id" id="cMeta"></div>
      <div class="pills" id="pillRow"></div>
      <div id="recNotesWrap" style="display:none" class="rec-notes-box">
        <div style="font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--warn);margin-bottom:6px">&#9888; Phone Screen Notes (from recruiter)</div>
        <div style="font-size:13px;color:var(--text);line-height:1.6" id="recNotesText"></div>
      </div>
      <div style="font-size:13px;color:var(--text);line-height:1.65" id="narrative"></div>
    </div>
    <div class="score-aside">
      <div class="score-box">
        <div class="sv sv-a" id="scoreResume">&mdash;</div><div class="s-lbl">Resume</div>
      </div>
      <div class="score-box">
        <div class="sv sv-w" id="scoreRec">pending</div><div class="s-lbl">Phone Screen</div>
      </div>
      <div class="adds-box">
        <span class="adds-v" id="panelAdds">+?</span>
        <span class="adds-l">pts panel can add</span>
      </div>
      <div class="score-box" id="panScoreBox" style="display:none">
        <div class="sv sv-b" id="scorePan">&mdash;</div><div class="s-lbl">Final Score</div>
      </div>
    </div>
  </div>
</div>

<div class="card" id="skillsCard" style="display:none">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div class="kicker">Skills to Probe</div>
  <div class="sec-title">Verify depth during the interview</div>
  <div class="sec-sub" style="margin-bottom:14px">These skills show weak or limited evidence on the resume</div>
  <div id="skillsProbe"></div>
</div>

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;flex-wrap:wrap;gap:10px">
    <div>
      <div class="kicker">Step 1 of 2 &mdash; Questions</div>
      <div class="sec-title">Technical Interview Questions</div>
      <div class="sec-sub">Deep technical questions targeting domain depth, problem-solving, and project ownership.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button onclick="generateQs()" style="background:var(--primary-light);color:var(--primary2);border:1px solid var(--primary-border);border-radius:10px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer">&#8635; Regenerate</button>
      <span id="genStatus" style="font-size:12px;color:var(--muted)"></span>
    </div>
  </div>
  <div id="qArea"><div style="text-align:center;padding:32px;color:var(--muted)"><span class="spin"></span> Generating technical questions&hellip;</div></div>
  <div id="applyBar" class="apply-bar" style="display:none">
    <div style="flex:1"><div style="font-weight:700;font-size:13px;color:var(--accent)">&#10003; Questions scored</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px" id="applySummary"></div></div>
    <button onclick="applyQScores()" style="background:linear-gradient(135deg,var(--primary2),var(--purple));color:#fff;border:none;border-radius:10px;padding:8px 18px;font-size:12px;font-weight:800;cursor:pointer">Apply to sliders &#8594;</button>
    <span id="applyMsg" style="font-size:12px;color:var(--muted)"></span>
  </div>
</div>

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:14px">
    <div>
      <div class="kicker">Step 2 of 2 &mdash; Parameters</div>
      <div class="sec-title">Panel Parameter Scores</div>
      <div class="sec-sub">Rate each dimension after the interview. These scores are added on top of resume + phone screen scores.</div>
    </div>
    <div class="total-box">
      <div class="total-val" id="liveTotal">0</div>
      <div class="total-lbl">/ 16 pts added</div>
    </div>
  </div>
  <div id="paramRows"></div>
  <div style="margin-top:22px">
    <div class="kicker" style="margin-bottom:8px">Panel Notes</div>
    <textarea id="panNotes" class="notes-ta" placeholder="Technical depth, ownership, communication quality, concerns, recommendation&hellip;"></textarea>
  </div>
  <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:16px">
    <button class="btn-save" onclick="savePanel()">Save Panel Score &#8594;</button>
    <span id="saveMsg" class="save-msg"></span>
  </div>
  <div class="success-panel" id="successPanel">
    <div style="font-size:16px;font-weight:800;color:var(--accent);margin-bottom:6px">&#10003; Panel score saved</div>
    <div style="font-size:13px;color:var(--muted);margin-bottom:20px">Final score: <b style="color:var(--accent2);font-size:22px" id="savedScoreVal">&mdash;</b><span style="color:var(--muted)"> / 100</span></div>
    <div class="final-grid" id="finalGrid"></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px">
      <button onclick="decide('Hired')" class="btn-success">&#10003; Mark as Hired</button>
      <button onclick="decide('Rejected')" style="background:linear-gradient(135deg,var(--red),#f87171);color:#fff;border:none;border-radius:12px;padding:12px 24px;font-size:13px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:6px">&#10005; Mark as Rejected</button>
      <a href="/portal" class="btn-ghost">View in Pipeline &rarr;</a>
      <a href="/recruiter/__CID__" class="btn-ghost">Full Portal &#8599;</a>
    </div>
    <div id="decideMsg" style="font-size:14px;font-weight:700"></div>
  </div>
</div>

<details style="margin-bottom:14px">
  <summary class="ref-sum">&#9656;&nbsp; Resume Scorecard Reference <span style="margin-left:auto;font-size:11px;font-weight:400;color:var(--muted)">Base scores &bull; panel params tagged</span></summary>
  <div class="ref-body"><div id="rsContent"><div style="color:var(--muted);font-size:12px;padding:8px 0">Loading&hellip;</div></div></div>
</details>

</div>

<script>
const CID='__CID__';
const PPARAMS=[
  {key:"communication_skills",label:"Communication Skills",max:5,step:0.5,help:"5 = exceptionally clear and structured; 4 = good; 3 = average; 2 = struggles; 1 = poor.",guide:"Assess verbal clarity, answer structure, and ability to explain technical concepts."},
  {key:"domain_skills",label:"Domain / Technical Depth",max:5,step:0.5,help:"5 = expert; 4 = strong; 3 = competent; 2 = foundational; 1 = weak.",guide:"Probe depth in primary domain: ML architecture, system design, data modelling, etc."},
  {key:"problem_solving",label:"Problem Solving",max:3,step:0.5,help:"3 = creative + systematic; 2 = methodical; 1 = ad hoc; 0 = stuck.",guide:"Give a real scenario and observe approach before asking for code."},
  {key:"project_explanation",label:"Project Deep-Dive",max:3,step:1,help:"3 = structured narrative with clear ownership and measurable impact; 2 = good; 1 = surface; 0 = cannot explain.",guide:"Ask: Walk me through the hardest technical decision you made on this project."},
];
let _analysis=null,_qs=[],_scored={};

function buildParams(){
  document.getElementById('paramRows').innerHTML=PPARAMS.map(p=>{
    const steps=Math.round(p.max/p.step);
    return '<div class="param-card" id="pc_'+p.key+'">'
      +'<div class="ph"><div><span class="pname">'+p.label+'</span><span id="fq_'+p.key+'" class="fromq" style="display:none">&#10003; from Qs</span></div>'
      +'<div style="display:flex;align-items:baseline;gap:3px"><span class="pval" id="rv_'+p.key+'">0</span><span class="pmax"> / '+p.max+'</span></div></div>'
      +'<div class="phelp">'+p.help+'</div>'
      +'<div class="pguide">&#9654; '+p.guide+'</div>'
      +'<input type="range" id="sl_'+p.key+'" min="0" max="'+steps+'" step="1" value="0" oninput="syncS(\''+p.key+'\','+p.step+')">'
      +'</div>';
  }).join('');
}
function syncS(key,step){
  const el=document.getElementById('sl_'+key),val=parseFloat(el.value)*step;
  document.getElementById('rv_'+key).textContent=val%1===0?val:val.toFixed(1);
  const t=PPARAMS.reduce((s,p)=>s+parseFloat(document.getElementById('sl_'+p.key)?.value||0)*p.step,0);
  document.getElementById('liveTotal').textContent=t%1===0?t:t.toFixed(1);
}
function setS(key,val){
  const p=PPARAMS.find(p=>p.key===key);if(!p)return;
  const el=document.getElementById('sl_'+key);if(!el)return;
  el.value=Math.round(val/p.step);syncS(key,p.step);
  const fq=document.getElementById('fq_'+key);if(fq)fq.style.display='';
}
function getV(key){const p=PPARAMS.find(p=>p.key===key);return p?parseFloat(document.getElementById('sl_'+p.key)?.value||0)*p.step:0;}

function pLbl(k){return({communication_skills:'Communication',domain_skills:'Domain Depth',problem_solving:'Problem Solving',project_explanation:'Project Deep-Dive',skill_depth:'Skill Depth',skill_recency:'Recency',mentorship_signal:'Mentorship',international_exposure:'Intl Exposure',stakeholder_management:'Stakeholder',career_progression:'Progression',stability:'Stability',company_tier:'Company Tier',certifications:'Certs'}[k]||k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase()));}

async function generateQs(){
  if(!_analysis)return;
  document.getElementById('qArea').innerHTML='<div style="text-align:center;padding:32px;color:var(--muted)"><span class="spin"></span> Generating technical questions&hellip;</div>';
  try{
    const r=await fetch('/generateInterviewQuestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({analysis:_analysis})});
    const d=await r.json();
    const all=d.questions||[];
    _qs=all.filter(q=>q.stage==='panel');
    if(!_qs.length)_qs=all.filter(q=>['domain','technical','system','problem','project'].some(t=>(q.theme||'').toLowerCase().includes(t)||(q.rubric_param||'').toLowerCase().includes(t)));
    if(!_qs.length)_qs=all.slice(Math.max(0,all.length-6));
    renderQs();document.getElementById('genStatus').textContent=_qs.length+' questions';
  }catch(e){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:12px">Error: '+e+'</div>';}
}
function renderQs(){
  if(!_qs.length){document.getElementById('qArea').innerHTML='<div style="color:var(--muted);padding:14px">No panel questions generated.</div>';return;}
  document.getElementById('qArea').innerHTML=_qs.map((q,i)=>{
    const tc={'high':'tgh','medium':'tgm','low':'tgl'}[q.priority||'medium'];
    return '<div class="q-card"><div class="q-top"><span class="q-num">'+(i+1)+'</span>'
      +'<div class="q-body">'
      +'<div class="tags"><span class="tag '+tc+'">'+(q.priority||'medium')+'</span>'
      +(q.rubric_param?'<span class="tag tgp">'+pLbl(q.rubric_param)+(q.max_pts?' &middot; '+q.max_pts+' pts':'')+'</span>':'')
      +(q.skill?'<span class="tag tgs">'+q.skill+'</span>':'')+'</div>'
      +'<div class="q-text" style="margin-top:8px">'+q.question+'</div>'
      +(q.what_it_tests?'<div class="q-sub"><b>Tests:</b> '+q.what_it_tests+'</div>':'')
      +'<textarea class="ans" id="ans_'+i+'" rows="3" placeholder="Type or paste the candidate\u2019s response\u2026"></textarea>'
      +'<div style="display:flex;align-items:center;gap:8px;margin-top:8px">'
      +'<button onclick="scoreQ('+i+')" id="sb_'+i+'" style="background:var(--primary-light);color:var(--primary2);border:1px solid var(--primary-border);border-radius:8px;padding:5px 13px;font-size:11px;font-weight:700;cursor:pointer">&#10003; Score</button>'
      +'<span id="sfb_'+i+'" style="font-size:11px;color:var(--muted)"></span>'
      +'<span id="qsc_'+i+'" style="font-size:16px;font-weight:900"></span>'
      +'</div><div id="sr_'+i+'"></div>'
      +'</div></div></div>';
  }).join('');
}
async function scoreQ(i){
  const q=_qs[i],ans=document.getElementById('ans_'+i).value.trim();
  if(!ans){document.getElementById('sfb_'+i).textContent='Enter answer first.';return;}
  const btn=document.getElementById('sb_'+i);btn.textContent='\u23f3';btn.disabled=true;
  try{
    const r=await fetch('/scoreQuestionAnswer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q.question,theme:q.theme||q.rubric_param||'',answer_transcript:ans,skill:q.skill||'',candidate_context:'',candidate_id:CID})});
    const sc=await r.json();_scored[i]=sc;
    const sv=sc.score_0_to_10||0,col=sv>=7?'var(--accent)':sv>=5?'var(--warn)':'var(--red)';
    document.getElementById('qsc_'+i).innerHTML='<span style="color:'+col+'">'+sv+'/10</span>';
    document.getElementById('sr_'+i).innerHTML='<div class="sres"><b style="color:'+col+'">'+sv+'/10</b>'+(sc.rubric_param?' <span style="color:var(--muted);font-size:10px">&rarr; '+pLbl(sc.rubric_param)+'</span>':'')+'<div style="margin-top:6px;color:var(--muted)"><b style="color:var(--text)">Strong:</b> '+(sc.what_was_strong||'\u2014')+'</div><div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Missing:</b> '+(sc.what_was_missing||'\u2014')+'</div><div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Probe:</b> '+(sc.follow_up_probe||'\u2014')+'</div></div>';
    document.getElementById('sfb_'+i).textContent='Scored.';
    const n=Object.keys(_scored).length;
    document.getElementById('applyBar').style.display='';
    document.getElementById('applySummary').textContent=n+' of '+_qs.length+' scored';
  }catch(e){document.getElementById('sfb_'+i).textContent='Error: '+e;}
  btn.textContent='Re-score';btn.disabled=false;
}
async function applyQScores(){
  const scored=Object.values(_scored);if(!scored.length)return;
  document.getElementById('applyMsg').textContent='Applying\u2026';
  try{
    const r=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,question_scores:scored,stage:'panel',recruiter_notes:''})});
    const d=await r.json();
    for(const[k,v]of Object.entries(d.rubric_param_overrides||{}))setS(k,v);
    document.getElementById('applyMsg').textContent='\u2713 Sliders updated from '+scored.length+' answers';
  }catch(e){document.getElementById('applyMsg').textContent='Error: '+e;}
}
async function savePanel(){
  const ov={};for(const p of PPARAMS){const v=getV(p.key);if(v>0)ov[p.key]=v;}
  if(!Object.keys(ov).length){document.getElementById('saveMsg').textContent='Move at least one slider.';return;}
  document.getElementById('saveMsg').textContent='Saving\u2026';
  try{
    const r=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'panel',stage_overrides:ov,recruiter_notes:document.getElementById('panNotes').value})});
    const d=await r.json();
    if(!r.ok){document.getElementById('saveMsg').textContent='Error: '+(d.detail||r.statusText);return;}
    const s100=d.stage_score_100??d.new_total;
    document.getElementById('saveMsg').textContent='';
    document.getElementById('scorePan').textContent=s100;
    document.getElementById('panScoreBox').style.display='';
    document.getElementById('savedScoreVal').textContent=s100;
    document.getElementById('successPanel').style.display='';
    const ss=d.stage_scores||{};
    document.getElementById('finalGrid').innerHTML=
      '<div class="ftile"><div class="fv" style="color:var(--accent)">'+(ss.resume_score_100??'\u2014')+'</div><div class="fl">Resume</div></div>'+
      '<div class="ftile"><div class="fv" style="color:var(--warn)">'+(ss.recruiter_score_100??'pending')+'</div><div class="fl">Phone Screen</div></div>'+
      '<div class="ftile"><div class="fv" style="color:var(--accent2)">'+(ss.panel_score_100??s100)+'</div><div class="fl">Panel / Final</div></div>';
    try{await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'Panel'})});}catch(e){}
  }catch(e){document.getElementById('saveMsg').textContent='Error: '+e;}
}
async function decide(stage){
  try{
    await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage})});
    const msg=document.getElementById('decideMsg');
    msg.textContent='\u2713 Candidate marked as '+stage;
    msg.style.color=stage==='Hired'?'var(--accent)':'var(--red)';
  }catch(e){}
}
function flatEdu(e){if(!e)return{};const o={};for(const[k,v]of Object.entries(e)){if(k!=='bonus')o[k]=v;}for(const[k,v]of Object.entries(e.bonus||{}))o[k]=v;return o;}
function renderSC(rs){
  if(!rs){document.getElementById('rsContent').innerHTML='<div style="color:var(--muted);font-size:12px">No scorecard.</div>';return;}
  const bd=rs.breakdown||{},ss=rs.stage_scores||{};
  const pp=new Set(ss.panel_pending_params||[]);
  const secs=[{l:'Experience',d:bd.experience||{},c:'#16A34A'},{l:'Skills',d:bd.skills||{},c:'#6366F1'},{l:'Education',d:flatEdu(bd.education||{}),c:'#D97706'}];
  let h='';
  for(const s of secs){
    h+='<div style="margin-bottom:14px"><div style="font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:'+s.c+';margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--line)">'+s.l+'</div>';
    for(const[k,v]of Object.entries(s.d)){
      if(!v||typeof v!=='object'||!('score' in v)||v.type==='info')continue;
      const pct=v.max>0?Math.round(v.score/v.max*100):0,fc=pct>=75?'#16A34A':pct>=45?'#D97706':'#DC2626';
      const sb=pp.has(k)?'<span class="stag sp">panel</span>':'';
      h+='<div class="rrow"><div class="rn">'+pLbl(k)+sb+'</div><div class="rb"><div class="rbt"><div class="rbf" style="width:'+pct+'%;background:'+fc+'"></div></div></div><div class="rs" style="color:'+fc+'">'+v.score+'/'+v.max+'</div><div class="rj">'+(v.llm_justification||v.reason||'')+'</div></div>';
    }
    h+='</div>';
  }
  document.getElementById('rsContent').innerHTML=h;
}
async function loadAll(){
  try{
    const r=await fetch('/candidateScore/'+encodeURIComponent(CID));
    if(r.ok){
      const d=await r.json();
      document.getElementById('cName').textContent=d.candidate_name||CID;
      document.getElementById('cMeta').textContent=CID;
      const stages=d.stages||[],ss=(stages[stages.length-1]||{}).stage_scores||{};
      if(ss.resume_score_100)document.getElementById('scoreResume').textContent=ss.resume_score_100;
      if(ss.recruiter_score_100!=null)document.getElementById('scoreRec').textContent=ss.recruiter_score_100;
      if(ss.panel_can_add)document.getElementById('panelAdds').textContent='+'+ss.panel_can_add;
      if(ss.panel_score_100!=null){document.getElementById('scorePan').textContent=ss.panel_score_100;document.getElementById('panScoreBox').style.display='';}
      const recStage=stages.find(s=>s.stage==='recruiter');
      if(recStage?.recruiter_notes){document.getElementById('recNotesText').textContent=recStage.recruiter_notes;document.getElementById('recNotesWrap').style.display='';}
    }
  }catch(e){}
  try{
    const r=await fetch('/api/candidateAnalysis/'+encodeURIComponent(CID));
    if(r.ok){
      _analysis=await r.json();
      const ov=_analysis.candidate_overview||{},rf=(_analysis.semantic_analysis?.role_family_scores||[])[0],dna=_analysis.dna_classification?.primary||'';
      const pills=[];
      if(rf)pills.push('<span class="pill pill-g">'+rf.role_family.replace(/_/g,' ')+'</span>');
      if(dna)pills.push('<span class="pill pill-w">'+dna+'</span>');
      const exp=(_analysis.experience?.items||[]).length;if(exp)pills.push('<span class="pill pill-b">'+exp+' roles</span>');
      document.getElementById('pillRow').innerHTML=pills.join('');
      document.getElementById('narrative').textContent=_analysis.recruiter_narrative||ov.profile_summary||'';
      const ev=_analysis.skills?.skill_evidence_map||{};
      const weak=Object.entries(ev).filter(([k,v])=>(v?.level==='WEAK'||v?.level==='MENTION')&&(v?.years||0)<=1).slice(0,8);
      if(weak.length){
        document.getElementById('skillsCard').style.display='';
        document.getElementById('skillsProbe').innerHTML=weak.map(([k,v])=>'<div class="skills-probe"><span style="font-weight:700;min-width:150px;color:var(--text)">'+k+'</span><span style="background:var(--amber-light);color:var(--amber);border:1px solid #FDE68A;border-radius:99px;padding:2px 9px;font-size:11px;font-weight:600">'+v.level+'</span><span style="color:var(--text2);font-size:11px;margin-left:6px">'+(v.years?v.years+' yr':'seen once')+'</span></div>').join('');
      }
      const rs=_analysis.rubric_scorecard||_analysis.rubric_score||null;
      if(rs){
        renderSC(rs);
        const ss=rs.stage_scores||{};
        if(ss.resume_score_100)document.getElementById('scoreResume').textContent=ss.resume_score_100;
        if(ss.panel_can_add)document.getElementById('panelAdds').textContent='+'+ss.panel_can_add;
      }
      generateQs();
    }
  }catch(e){}
  buildParams();
  try{await fetch('/api/pipeline/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID})});}catch(e){}
}
loadAll();
</script>
</div></div></body></html>""".replace("__CID__", cid_js).replace("__SIDEBAR_HTML__", _sidebar("pipeline", user))
    return _html


# ===========================================================================
# Dashboard (GET /)
# ===========================================================================

def _sidebar(active: str = "", user: dict | None = None) -> str:
    role = (user or {}).get("role", "super_admin")  # fallback for unauthenticated legacy calls

    def _item(key, href, svg, label):
        cls = " active" if active == key else ""
        return f'<a href="{href}" class="nav-item{cls}">{svg}<span>{label}</span></a>'

    ICO_DASH  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>'
    ICO_UP    = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>'
    ICO_BULK  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>'
    ICO_JOBS  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>'
    ICO_CAND  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21a8 8 0 1 0-16 0"/><circle cx="12" cy="7" r="4"/></svg>'
    ICO_PIPE  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
    ICO_EVAL  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>'
    ICO_PLIB  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><line x1="8" y1="7" x2="16" y2="7"/><line x1="8" y1="11" x2="14" y2="11"/></svg>'
    ICO_HELP  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>'
    ICO_SRC   = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M11 8v6M8 11h6"/></svg>'
    ICO_OUT   = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>'
    ICO_STAND = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>'
    ICO_ADMIN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93A10 10 0 1 0 21 12h-1"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>'

    # Records section — all roles
    records = (
        _item("dash", "/", ICO_DASH, "Dashboard") +
        _item("jobs", "/jobs", ICO_JOBS, "Jobs") +
        _item("candidates", "/candidates", ICO_CAND, "Candidates")
    )

    # Upload + Bulk — recruiters and above (not sales-only, not panel)
    if role in ("super_admin", "recruiter_head", "recruiter"):
        records += (
            _item("upload", "/upload", ICO_UP, "Upload") +
            _item("bulk", "/bulk", ICO_BULK, "Bulk")
        )

    # Tools section
    tools = _item("standup", "/standup", ICO_STAND, "Standup")

    if role in ("super_admin", "recruiter_head", "recruiter"):
        tools += (
            _item("pipeline", "/pipeline", ICO_PIPE, "Pipeline") +
            _item("outcomes", "/outcomes", ICO_OUT, "Outcomes")
        )

    if role in ("super_admin", "sales_head", "recruiter_head", "sales_executive"):
        tools += _item("sourcing", "/sourcing", ICO_SRC, "Sourcing")

    if role == "super_admin":
        tools += (
            _item("evals", "/evals", ICO_EVAL, "Evals") +
            _item("prompt-library", "/prompt-library", ICO_PLIB, "Prompt Library") +
            _item("admin", "/admin", ICO_ADMIN, "Admin")
        )

    # User info + logout in footer
    name_display = escape((user or {}).get("full_name", "")[:18]) if user else ""
    role_display = escape(role.replace("_", " ").title()) if user else ""
    user_footer = (
        f'<div style="padding:8px 10px 6px;border-top:1px solid var(--border);margin-top:4px">'
        f'<div style="font-size:12px;font-weight:700;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name_display}</div>'
        f'<div style="font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">{role_display}</div>'
        f'<a href="/logout" style="font-size:12px;color:var(--red);text-decoration:none;font-weight:600">Sign out</a>'
        f'</div>'
    ) if user else ""

    return (
        f'<aside class="sidebar">'
        f'<div class="sidebar-logo">'
        f'<div class="sidebar-logo-icon">RI</div>'
        f'<div class="sidebar-brand">Resume Intelligence</div>'
        f'</div>'
        f'<nav class="sidebar-nav">'
        f'<div class="nav-section-label">Records</div>{records}'
        f'<div class="nav-section-label" style="margin-top:6px">Tools</div>{tools}'
        f'</nav>'
        f'<div class="sidebar-footer">'
        f'<a href="/" class="nav-item">{ICO_HELP}<span>Help</span></a>'
        f'{user_footer}'
        f'</div>'
        f'</aside>'
    )


_BASE_CSS = """<style>
:root{--bg:#F8FAFC;--white:#FFFFFF;--border:#E2E8F0;--text:#0F172A;--text2:#475569;--primary:#1D4ED8;--primary2:#4F46E5;--primary-light:#EEF2FF;--primary-border:#C7D2FE;--amber:#D97706;--amber-light:#FFFBEB;--red:#DC2626;--red-light:#FEF2F2;--card:#FFFFFF;--line:#E2E8F0;--muted:#94A3B8;--accent:#1D4ED8;--accent2:#4F46E5;--warn:#D97706;--danger:#DC2626;--ok:#16A34A;--info:#0284C7}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Aptos,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--primary2);text-decoration:none}
.app-shell{display:flex;min-height:100vh}
.sidebar{width:240px;min-height:100vh;background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column;position:fixed;left:0;top:0;z-index:100}
.sidebar-logo{padding:14px 16px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.sidebar-logo-icon{width:32px;height:32px;background:linear-gradient(135deg,var(--primary),var(--primary2));border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:800;flex-shrink:0}
.sidebar-brand{font-size:13px;font-weight:800;color:var(--text)}
.sidebar-nav{flex:1;padding:8px;overflow-y:auto}
.nav-section-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);padding:10px 8px 4px}
.nav-item{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:8px;font-size:13px;font-weight:500;color:var(--text2);cursor:pointer;text-decoration:none;transition:background .12s,color .12s;white-space:nowrap}
.nav-item:hover{background:var(--bg);color:var(--text)}
.nav-item.active{background:var(--primary-light);color:var(--primary);font-weight:600}
.sidebar-footer{padding:8px;border-top:1px solid var(--border)}
.main{margin-left:240px;flex:1;min-height:100vh}
.wrap{padding:24px 24px 80px}
.card{background:var(--white);border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin-bottom:14px}
.kicker{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-bottom:6px}
.metric{font-size:34px;font-weight:900;letter-spacing:-.02em}
.pill{display:inline-flex;align-items:center;background:var(--primary-light);color:var(--primary);border:1px solid var(--primary-border);border-radius:999px;padding:3px 10px;margin:2px 3px 2px 0;font-size:11px;font-weight:500}
.band-STRONG{color:#16A34A}.band-GOOD{color:var(--primary2)}.band-AVERAGE{color:var(--amber)}.band-WEAK{color:var(--red)}
.btn{background:var(--primary);color:#fff;border:none;border-radius:10px;padding:9px 18px;font-weight:700;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px;transition:opacity .15s}
.btn:hover{opacity:.88;color:#fff}
.btn-sec{background:var(--white);color:var(--text2);border:1px solid var(--border);border-radius:10px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px;transition:background .12s}
.btn-sec:hover{background:var(--bg);color:var(--text)}
.btn-danger{background:transparent;color:var(--red);border:1px solid #FECACA;border-radius:10px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.btn-danger:hover{background:var(--red-light)}
.table{width:100%;border-collapse:collapse}
.table th{padding:10px 12px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--text2);border-bottom:1px solid var(--border)}
.table td{padding:11px 12px;border-bottom:1px solid #F1F5F9;font-size:13px;vertical-align:middle}
.table tbody tr:hover{background:#F8FAFC}
.score-bar{height:5px;border-radius:3px;background:#E5E7EB;overflow:hidden}
.score-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--primary),var(--primary2))}
.stat-card{background:var(--white);border:1px solid var(--border);border-radius:12px;padding:16px 18px;text-align:center}
.stat-val{font-size:28px;font-weight:900;color:var(--primary);line-height:1.1}
.stat-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-top:4px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text2)}
.form-input{background:var(--white);border:1px solid var(--border);border-radius:8px;padding:9px 12px;color:var(--text);font-size:13px;font-family:inherit;outline:none;transition:border-color .15s}
.form-input:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-light)}
.tag-SHORTLIST{background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;display:inline-block}
.tag-SCREEN{background:var(--amber-light);color:var(--amber);border:1px solid #FDE68A;border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;display:inline-block}
.tag-REJECT{background:var(--red-light);color:var(--red);border:1px solid #FECACA;border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
</style>"""


@app.get("/", response_class=HTMLResponse)
def dashboard(user: dict = Depends(get_current_user)):
    candidates = list_candidate_analyses()
    scores = list_candidate_scores()
    jobs = list_job_postings(include_closed=False)
    from pipeline_store import list_pipeline
    pipeline = list_pipeline()

    total_candidates = len(candidates)
    avg_score = 0
    if scores:
        vals = [s.get("current_total") or 0 for s in scores]
        avg_score = int(round(sum(vals) / max(1, len(vals))))
    active_jds = len(jobs)
    pipeline_count = len(pipeline)

    recent_rows = ""
    for c in candidates[:15]:
        cid = c.get("candidate_id", "")
        name = c.get("name", cid)
        score = c.get("resume_score_100") or "\u2014"
        band = c.get("band") or "\u2014"
        role = (c.get("role_family") or "\u2014").replace("_", " ")
        band_cls = f"band-{band}" if band in ("STRONG", "GOOD", "AVERAGE", "WEAK") else ""
        recent_rows += (
            f'<tr><td><a href="/candidates/{cid}" style="color:var(--accent2);font-weight:600">{name}</a></td>'
            f'<td><span class="{band_cls}">{score}</span></td>'
            f'<td><span class="pill {band_cls}">{band}</span></td>'
            f'<td style="color:var(--muted)">{role}</td>'
            f'<td><a href="/candidates/{cid}" class="btn-sec" style="padding:4px 10px;font-size:11px">View</a></td></tr>'
        )

    sidebar = _sidebar("dash", user)
    table_html = (
        "<table class='table'><thead><tr><th>Name</th><th>Score</th><th>Band</th><th>Role Family</th><th>Actions</th></tr></thead><tbody>"
        + recent_rows + "</tbody></table>"
    ) if candidates else "<div style='color:var(--text2);padding:20px 0'>No candidates yet. Upload a resume to get started.</div>"
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Resume Intelligence \u2014 Dashboard</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'>"
        f"<div class='wrap'>"
        f"<div style='margin-bottom:20px'>"
        f"<div class='kicker'>Platform Overview</div>"
        f"<h1 style='font-size:24px;font-weight:800;margin:4px 0 4px;color:var(--text)'>Dashboard</h1>"
        f"<p style='color:var(--text2);font-size:14px'>Analyse resumes, run bulk pipelines, match candidates against JDs, and track them through the hiring funnel.</p>"
        f"<div style='display:flex;gap:10px;margin-top:14px;flex-wrap:wrap'>"
        f"<a href='/upload' class='btn'>&#8679; Upload Resume</a>"
        f"<a href='/bulk' class='btn-sec'>&#128230; Bulk Upload</a>"
        f"<a href='/jobs' class='btn-sec'>&#128188; Create JD</a>"
        f"</div></div>"
        f"<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px'>"
        f"<div class='stat-card'><div class='stat-val'>{total_candidates}</div><div class='stat-lbl'>Candidates</div></div>"
        f"<div class='stat-card'><div class='stat-val'>{avg_score}<span style='font-size:16px;color:var(--text2)'>/100</span></div><div class='stat-lbl'>Avg Score</div></div>"
        f"<div class='stat-card'><div class='stat-val'>{active_jds}</div><div class='stat-lbl'>Active JDs</div></div>"
        f"<div class='stat-card'><div class='stat-val'>{pipeline_count}</div><div class='stat-lbl'>In Pipeline</div></div>"
        f"</div>"
        f"<div class='card'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:14px'>"
        f"<h2 style='font-size:16px;font-weight:700;color:var(--text)'>Recent Candidates</h2>"
        f"<a href='/upload' class='btn-sec' style='font-size:12px'>+ Add New</a></div>"
        f"{table_html}</div></div></div></div></body></html>"
    )


# ===========================================================================
# Bulk Upload UI + API
# ===========================================================================

@app.get("/bulk", response_class=HTMLResponse)
def bulk_upload_page(user: dict = Depends(get_current_user)):
    jobs_json = list_job_postings(include_closed=False)
    jd_options = "".join(f'<option value="{j["jd_id"]}">{j["title"]}</option>' for j in jobs_json)
    sidebar = _sidebar("bulk", user)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Bulk Analysis</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'>"
        f"<div class='wrap'>"
        f"<div style='margin-bottom:16px'><div class='kicker'>Upload</div>"
        f"<h1 style='font-size:24px;font-weight:800;color:var(--text)'>Bulk Resume Analysis</h1></div>"
        f"<div class='card'>"
        f"<div id='dropZone' style='border:2px dashed var(--border);border-radius:12px;padding:40px;text-align:center;cursor:pointer;transition:border-color .2s;margin-bottom:16px' onclick=\"document.getElementById('fileInput').click()\" ondragover=\"event.preventDefault();this.style.borderColor='var(--primary)'\" ondragleave=\"this.style.borderColor='var(--border)'\" ondrop='handleDrop(event)'>"
        f"<div style='font-size:32px;margin-bottom:8px'>&#8679;</div>"
        f"<div style='font-weight:700;margin-bottom:4px'>Drop PDF, DOCX, or JSON resume files here or click to browse</div>"
        f"<div style='color:var(--muted);font-size:13px'>PDF &amp; DOCX are extracted automatically &bull; JSON is analysed directly</div>"
        f"<input type='file' id='fileInput' multiple accept='.pdf,.docx,.json' style='display:none' onchange='handleFiles(this.files)'></div>"
        f"<div style='display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px'>"
        f"<div class='form-group' style='flex:1;min-width:200px'>"
        f"<label class='form-label'>Match against JD (optional)</label>"
        f"<select id='jdSelect' class='form-input'><option value=''>-- No JD matching --</option>{jd_options}</select></div>"
        f"<button class='btn' onclick='startBulk()' id='startBtn' disabled>&#128640; Analyze <span id='fileCount'>0</span> Resumes</button></div>"
        f"<div id='fileList' style='margin-bottom:12px'></div></div>"
        f"<div class='card' id='progressCard' style='display:none'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px'>"
        f"<h2 style='font-size:18px;font-weight:800'>Progress</h2>"
        f"<div id='progressSummary' style='color:var(--muted);font-size:13px'></div></div>"
        f"<div class='score-bar' style='margin-bottom:16px;height:8px'><div class='score-fill' id='progressBar' style='width:0%'></div></div>"
        f"<div id='resultRows'></div>"
        f"<div style='margin-top:16px;display:flex;gap:10px'>"
        f"<button class='btn-sec' onclick='exportCSV()' id='exportBtn' style='display:none'>&#128202; Export CSV</button></div></div>"
        f"</div>"
        f"<script>"
        f"let _files=[];let _jobId=null;let _pollInterval=null;"
        f"function handleDrop(e){{e.preventDefault();document.getElementById('dropZone').style.borderColor='var(--border)';handleFiles(e.dataTransfer.files);}}"
        f"function fileTag(name){{const ext=name.split('.').pop().toLowerCase();const c=ext==='json'?'#16A34A':'#353395';return `<span style='background:#F0F0FB;border:1px solid #E0E0F5;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700;color:${{c}};margin-right:4px'>${{ext.toUpperCase()}}</span>`;}}"
        f"function handleFiles(fileList){{_files=Array.from(fileList).filter(f=>/\\.(pdf|docx|json)$/i.test(f.name));"
        f"document.getElementById('fileCount').textContent=_files.length;"
        f"document.getElementById('startBtn').disabled=!_files.length;"
        f"document.getElementById('fileList').innerHTML=_files.map(f=>`<div style='display:inline-flex;align-items:center;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:4px 10px;margin:4px;font-size:12px;color:var(--text)'>${{fileTag(f.name)}}${{f.name}}</div>`).join('');}}"
        f"async function startBulk(){{if(!_files.length)return;const jdId=document.getElementById('jdSelect').value;"
        f"document.getElementById('startBtn').disabled=true;document.getElementById('progressCard').style.display='';"
        f"document.getElementById('progressBar').style.width='0%';document.getElementById('resultRows').innerHTML='<div style=\"color:var(--text2)\">Starting...</div>';"
        f"const fd=new FormData();for(const f of _files)fd.append('files',f);if(jdId)fd.append('jd_id',jdId);"
        f"const res=await fetch('/bulk_analyze',{{method:'POST',body:fd}});const data=await res.json();"
        f"if(!res.ok){{document.getElementById('resultRows').innerHTML=`<div style='color:var(--red)'>Error: ${{data.detail||res.statusText}}</div>`;document.getElementById('startBtn').disabled=false;return;}}"
        f"_jobId=data.job_id;"
        f"if(data.extraction_errors&&data.extraction_errors.length){{document.getElementById('resultRows').innerHTML=data.extraction_errors.map(e=>`<div style='background:var(--red-light);border:1px solid #FECACA;border-radius:8px;padding:8px 14px;margin-bottom:6px;font-size:12px;color:var(--red)'>&#9888; ${{e}}</div>`).join('');}}"
        f"_pollInterval=setInterval(pollStatus,1500);}}"
        f"async function pollStatus(){{if(!_jobId)return;const res=await fetch(`/bulk_status/${{_jobId}}`);const job=await res.json();"
        f"const pct=Math.round(100*(job.completed+job.failed)/Math.max(1,job.total));"
        f"document.getElementById('progressBar').style.width=pct+'%';"
        f"const running=(job.results||[]).filter(r=>r.status==='running').length;"
        f"document.getElementById('progressSummary').textContent=running>0?`${{job.completed+job.failed}}/${{job.total}} processed · ${{running}} analysing...`:`${{job.completed+job.failed}}/${{job.total}} processed (${{job.failed}} failed)`;"
        f"renderRows(job.results);"
        f"if(job.status==='done'||job.status==='partial'){{clearInterval(_pollInterval);document.getElementById('exportBtn').style.display='';"
        f"const existCount=(job.results||[]).filter(r=>r.already_existed).length;"
        f"const totalMs=(job.results||[]).reduce((s,r)=>s+(r.elapsed_ms||0),0);"
        f"const avgMs=Math.round(totalMs/Math.max(1,job.results.length));"
        f"let jobSummary=`Job done &bull; ${{job.completed}} OK &bull; ${{job.failed}} failed`;"
        f"if(existCount)jobSummary+=` &bull; ${{existCount}} already in system (skipped)`;"
        f"if(avgMs)jobSummary+=` &bull; avg ${{(avgMs/1000).toFixed(1)}}s/resume`;"
        f"if(job.created_at&&job.finished_at){{const totalSec=Math.round((new Date(job.finished_at)-new Date(job.created_at))/1000);jobSummary+=` &bull; total ${{totalSec}}s`;}}"
        f"document.getElementById('progressSummary').innerHTML=`<span style='color:var(--text2)'>${{jobSummary}}</span>`;}}}}"
        f"function scoreColor(s){{return s>=70?'#16A34A':s>=55?'#D97706':'#DC2626';}}"
        f"function fmtMs(ms){{if(ms==null)return'';if(ms<1000)return ms+'ms';return(ms/1000).toFixed(1)+'s';}}"
        f"function fmtTs(iso){{if(!iso)return'';try{{return new Date(iso).toLocaleTimeString([],{{hour:'2-digit',minute:'2-digit',second:'2-digit'}});}}catch{{return iso;}}}}"
        f"function renderRows(rows){{document.getElementById('resultRows').innerHTML=rows.map(r=>{{if(r.status==='running')return `<div style='background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:8px;font-size:13px;display:flex;gap:10px;align-items:center;opacity:.7'><span style='display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--primary);border-radius:50%;animation:spin 0.8s linear infinite'></span><b style='color:var(--text2)'>${{r.filename}}</b><span style='color:var(--text2);font-size:12px'>Analysing...</span></div>`;if(r.status==='error')return `<div style='background:var(--red-light);border:1px solid #FECACA;border-radius:8px;padding:10px 14px;margin-bottom:8px;font-size:13px;display:flex;gap:10px;align-items:center'><span style='color:var(--red)'>&#10007;</span><b>${{r.filename}}</b><span style='color:var(--red)'>Error: ${{r.error}}</span></div>`;const existBadge=r.already_existed?`<span style='background:#FEF3C7;color:#D97706;border:1px solid #FDE68A;border-radius:99px;padding:2px 7px;font-size:10px;font-weight:700'>&#8617; Already in system</span>`:'';const timingHtml=r.elapsed_ms!=null?`<span style='color:var(--text2);font-size:11px;white-space:nowrap' title='Started: ${{fmtTs(r.started_at)}} | Completed: ${{fmtTs(r.completed_at)}}'>&#8987; ${{fmtMs(r.elapsed_ms)}}</span>`:'';return `<div style='background:var(--white);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:8px;font-size:13px;display:flex;gap:12px;align-items:center;flex-wrap:wrap'><span style='color:#16A34A'>&#10003;</span><b style='flex:1;min-width:150px;color:var(--text)'>${{r.name||r.filename}}</b>${{existBadge}}${{r.resume_score!==null?`<span style='color:${{scoreColor(r.resume_score)}}'>Score: ${{r.resume_score}}</span>`:''}}<span style='color:var(--text2)'>${{r.band}}</span><span style='color:var(--primary2)'>${{(r.role_family||'').replace(/_/g,' ')}}</span>${{r.combined_score!==null?`<span>Combined: <b style='color:${{scoreColor(r.combined_score)}}'>${{r.combined_score}}</b></span>`:''}}${{timingHtml}}${{r.candidate_id?`<a href='/candidates/${{r.candidate_id}}' class='btn-sec' style='padding:4px 10px;font-size:11px'>View</a>`:''}}</div>`;}}).join('');}}"
        f"function exportCSV(){{if(!_jobId)return;fetch(`/bulk_status/${{_jobId}}`).then(r=>r.json()).then(job=>{{const rows=job.results;const hdr='candidate_id,name,filename,status,resume_score,jd_match_score,combined_score,band,role_family,dna,yoe,error,started_at,completed_at,elapsed_ms,already_existed';const lines=rows.map(r=>[r.candidate_id,r.name,r.filename,r.status,r.resume_score,r.jd_match_score,r.combined_score,r.band,r.role_family,r.dna,r.yoe,r.error,r.started_at||'',r.completed_at||'',r.elapsed_ms??'',r.already_existed?1:0].map(v=>JSON.stringify(v??'')).join(','));const blob=new Blob([[hdr,...lines].join('\\n')],{{type:'text/csv'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='bulk_results.csv';a.click();}});}}"
        f"</script></div></div></body></html>"
    )


@app.post("/bulk_analyze")
async def bulk_analyze(files: list[UploadFile] = File(...), jd_id: str | None = None):
    """Accept PDF, DOCX, or JSON resume files, extract if needed, launch bulk analysis job."""
    import tempfile
    from pathlib import Path as _Path
    from bulk_pipeline import create_bulk_job
    payloads = []
    extraction_errors = []
    for f in files:
        fname = f.filename or "resume"
        content = await f.read()
        suffix = _Path(fname).suffix.lower()
        if suffix == ".json":
            try:
                payload = json.loads(content)
                payloads.append({"filename": fname, "payload": payload})
            except Exception:
                extraction_errors.append(f"{fname}: invalid JSON")
        elif suffix in (".pdf", ".docx"):
            try:
                from pdf_to_json_extractor import pdf_to_resume_json
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = _Path(tmp.name)
                payload = pdf_to_resume_json(tmp_path)
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                if not payload:
                    extraction_errors.append(f"{fname}: extraction returned empty result")
                    continue
                payloads.append({"filename": fname, "payload": payload})
            except Exception as exc:
                extraction_errors.append(f"{fname}: {exc}")
        else:
            extraction_errors.append(f"{fname}: unsupported file type (use PDF, DOCX, or JSON)")
    if not payloads:
        detail = "No valid files could be processed."
        if extraction_errors:
            detail += " Errors: " + "; ".join(extraction_errors[:3])
        raise HTTPException(status_code=400, detail=detail)
    job_id = create_bulk_job(payloads, jd_id=jd_id or None)
    return JSONResponse(content={
        "job_id": job_id,
        "total": len(payloads),
        "extraction_errors": extraction_errors,
    })


@app.get("/bulk_status/{job_id}")
def bulk_status(job_id: str):
    from bulk_pipeline import get_bulk_job
    job = get_bulk_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk job not found.")
    return JSONResponse(content=job)


# ===========================================================================
# JD Manager UI + API
# ===========================================================================

@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(user: dict = Depends(get_current_user)):
    from jd_match_store import list_jd_matches
    jobs = list_job_postings(include_closed=False)
    job_cards = ""
    for j in jobs:
        jid = j["jd_id"]
        title = j.get("title", "Untitled")
        company = j.get("company") or ""
        skills = j.get("mandatory_skills") or []
        skill_names = [s if isinstance(s, str) else s.get("skill", "") for s in skills[:5]]
        yoe_min = j.get("yoe_min") or "?"
        yoe_max = j.get("yoe_max") or "?"
        role = (j.get("role_family") or "").replace("_", " ")
        match_count = len(list_jd_matches(jid))
        skill_pills = "".join(f"<span class='pill'>{s}</span>" for s in skill_names)
        company_html = f"<span style='color:var(--primary2);font-size:13px;font-weight:600'>{company}</span> &bull; " if company else ""
        company_html = f"<span style='color:var(--primary2);font-size:13px;font-weight:600'>{company}</span> &bull; " if company else ""
        job_cards += (
            f"<div class='card' style='margin-bottom:12px'>"
            f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap'>"
            f"<div><div class='kicker'>OPEN &bull; {role}</div>"
            f"<h3 style='font-size:17px;font-weight:700;margin:4px 0 4px;color:var(--text)'>{title}</h3>"
            f"<div style='color:var(--text2);font-size:13px;margin-bottom:8px'>{company_html}YoE: {yoe_min}\u2013{yoe_max} &bull; {match_count} matched</div>"
            f"<div>{skill_pills}</div></div>"
            f"<div style='display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start'>"
            f"<a href='/jobs/{jid}' class='btn'>View Leaderboard</a>"
            f"<button class='btn-danger' onclick=\"closeJD('{jid}')\">Close</button>"
            f"</div></div></div>"
        )

    sidebar = _sidebar("jobs", user)
    no_jobs = "<div class='card' style='color:var(--text2)'>No active JDs yet. Create one above.</div>"
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Job Descriptions</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px'>"
        f"<div><div class='kicker'>Recruitment</div><h1 style='font-size:24px;font-weight:800;color:var(--text)'>Job Descriptions</h1></div>"
        f"<button class='btn' onclick=\"document.getElementById('createForm').style.display=document.getElementById('createForm').style.display===''?'none':''\">+ Create New JD</button>"
        f"</div>"
        f"<div class='card' id='createForm' style='display:none;margin-bottom:20px'>"
        f"<h2 style='font-size:17px;font-weight:700;margin-bottom:14px;color:var(--text)'>Create New JD</h2>"
        f"<div style='margin-bottom:18px;padding:14px 16px;background:var(--bg);border-radius:10px;border:1px dashed var(--border)'>"
        f"<div style='font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--primary);margin-bottom:10px'>📄 Auto-fill from File</div>"
        f"<div style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px'>"
        f"<input type='file' id='jd_pdf_file' accept='.pdf,.docx,.txt,.json' style='display:none' onchange='extractJD()'>"
        f"<button class='btn-sec' onclick=\"document.getElementById('jd_pdf_file').click()\">Choose File</button>"
        f"<span style='color:var(--muted);font-size:12px'>PDF / DOCX → LLM extraction &nbsp;|&nbsp; JSON → direct load</span>"
        f"</div>"
        f"<div id='extractStatus' style='font-size:13px;color:var(--muted)'></div>"
        f"</div>"
        f"<div class='form-row'><div class='form-group'><label class='form-label'>Job Title *</label><input class='form-input' id='jd_title' placeholder='Senior Data Engineer'></div>"
        f"<div class='form-group'><label class='form-label'>Company / Organization</label><input class='form-input' id='jd_company' placeholder='Acme Corp'></div></div>"
        f"<div class='form-row'><div class='form-group'><label class='form-label'>Role Family</label><select class='form-input' id='jd_role'>"
        f"<option value='DATA_ENGINEER'>Data Engineer</option><option value='DATA_SCIENTIST'>Data Scientist</option>"
        f"<option value='ML_ENGINEER'>ML Engineer</option><option value='ANALYST'>Analyst</option>"
        f"<option value='BI_LEADER'>BI Leader</option><option value='AGENTIC_AI'>Agentic AI</option>"
        f"<option value='DOMAIN_SPECIALIST'>Domain Specialist</option></select></div></div>"
        f"<div class='form-row'><div class='form-group'><label class='form-label'>Min YoE</label><input class='form-input' id='jd_yoe_min' type='number' placeholder='5'></div>"
        f"<div class='form-group'><label class='form-label'>Max YoE</label><input class='form-input' id='jd_yoe_max' type='number' placeholder='10'></div></div>"
        f"<div class='form-group' style='margin-bottom:12px'><label class='form-label'>Mandatory Skills (comma-separated)</label>"
        f"<input class='form-input' id='jd_skills' placeholder='Spark, Kafka, Airflow, Databricks'></div>"
        f"<div class='form-group' style='margin-bottom:12px'><label class='form-label'>Nice-to-Have Skills (comma-separated)</label>"
        f"<input class='form-input' id='jd_nice' placeholder='dbt, Delta Lake'></div>"
        f"<div class='form-group' style='margin-bottom:16px'><label class='form-label'>Description / Responsibilities</label>"
        f"<textarea class='form-input' id='jd_desc' rows='4' placeholder='Key responsibilities...'></textarea></div>"
        f"<div style='display:flex;gap:10px;align-items:center'><button class='btn' onclick='createJD()'>Save JD</button>"
        f"<span id='createStatus' style='color:var(--muted);font-size:13px'></span></div></div>"
        f"{job_cards if jobs else no_jobs}"
        f"</div>"
        f"<script>"
        f"function _fillJDFields(d){{"
        f"let roleTitle=d.title||'';let company=d.company_name||d.company||'';"
        f"if(!company&&roleTitle.includes('_')){{const parts=roleTitle.split('_');company=parts[parts.length-1].trim();roleTitle=parts.slice(0,-1).join(' ').trim();}}"
        f"if(roleTitle)document.getElementById('jd_title').value=roleTitle;"
        f"if(company)document.getElementById('jd_company').value=company;"
        f"if(d.role_family)document.getElementById('jd_role').value=d.role_family;"
        f"if(d.yoe_min!=null)document.getElementById('jd_yoe_min').value=d.yoe_min;"
        f"if(d.yoe_max!=null)document.getElementById('jd_yoe_max').value=d.yoe_max;"
        f"const ms=d.mandatory_skills||[];if(ms.length)document.getElementById('jd_skills').value=ms.map(s=>typeof s==='string'?s:(s.skill||'')).filter(Boolean).join(', ');"
        f"const nh=d.nice_to_have_skills||[];if(nh.length)document.getElementById('jd_nice').value=nh.map(s=>typeof s==='string'?s:(s.skill||'')).filter(Boolean).join(', ');"
        f"if(d.description)document.getElementById('jd_desc').value=d.description;"
        f"}}"
        f"async function _saveJDFromFields(st,label){{const title=document.getElementById('jd_title').value.trim();"
        f"if(!title){{st.textContent='Cannot save: title is missing';st.style.color='var(--warn)';return;}}"
        f"st.textContent='Saving JD\u2026';st.style.color='var(--primary2)';"
        f"const skills=document.getElementById('jd_skills').value.split(',').map(s=>s.trim()).filter(Boolean);"
        f"const nice=document.getElementById('jd_nice').value.split(',').map(s=>s.trim()).filter(Boolean);"
        f"const body={{title,company:document.getElementById('jd_company').value.trim(),"
        f"role_family:document.getElementById('jd_role').value,"
        f"yoe_min:parseInt(document.getElementById('jd_yoe_min').value)||null,"
        f"yoe_max:parseInt(document.getElementById('jd_yoe_max').value)||null,"
        f"mandatory_skills:skills,nice_to_have_skills:nice,"
        f"description:document.getElementById('jd_desc').value.trim()}};"
        f"const res=await fetch('/jobs',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});"
        f"const data=await res.json();"
        f"if(res.ok){{window.location.href='/jobs/'+data.jd_id;}}"
        f"else{{st.textContent='Save failed: '+(data.detail||res.statusText);st.style.color='var(--warn)';}}}} "
        f"function _normJDFromFile(raw,filename){{"
        f"let d=raw;"
        f"if(raw.resume_data){{"
        f"const sk=raw.resume_data.skills_info||{{}};"
        f"d={{mandatory_skills:[...(sk.domain_skills||[]),...(sk.programming_languages||[]),...(sk.frameworks_and_libraries||[]),...(sk.tools_and_platforms||[])].filter(Boolean),"
        f"nice_to_have_skills:[...(sk.certified_skills||[]),...(sk.cloud_and_infra||[])].filter(Boolean),"
        f"description:''}};}}"
        f"if(!d.title&&filename){{"
        f"const stem=filename.replace(/\\.json$/i,'');"
        f"if(stem.includes('_')){{"
        f"const parts=stem.split('_');"
        f"d={{...d,company:parts[parts.length-1].trim(),title:parts.slice(0,-1).join(' ').trim()}};}}"
        f"else{{d={{...d,title:stem}};}}}}"
        f"return d;}}"
        f"async function extractJD(){{const file=document.getElementById('jd_pdf_file').files[0];if(!file)return;"
        f"const st=document.getElementById('extractStatus');st.style.color='var(--primary2)';"
        f"const isJson=file.name.toLowerCase().endsWith('.json');"
        f"if(isJson){{"
        f"st.textContent='Reading JSON\u2026';"
        f"try{{const text=await file.text();const raw=JSON.parse(text);"
        f"const d=_normJDFromFile(raw,file.name);"
        f"_fillJDFields(d);"
        f"await _saveJDFromFields(st,'JSON');"
        f"}}catch(e){{st.textContent='Error: '+e.message;st.style.color='var(--warn)';}}"
        f"return;}}"
        f"st.textContent='Extracting from '+(file.name.split('.').pop().toUpperCase())+'\u2026';"
        f"const fd=new FormData();fd.append('file',file);"
        f"try{{const res=await fetch('/jobs/extract_jd',{{method:'POST',body:fd}});"
        f"if(!res.ok){{st.textContent='Extraction failed: '+(await res.text());st.style.color='var(--warn)';return;}}"
        f"const d=await res.json();"
        f"_fillJDFields(d);"
        f"await _saveJDFromFields(st,'PDF');"
        f"}}catch(e){{st.textContent='Error: '+e;st.style.color='var(--warn)';}}}}"
        f"async function createJD(){{const title=document.getElementById('jd_title').value.trim();if(!title){{document.getElementById('createStatus').textContent='Title is required.';return;}}"
        f"const skills=document.getElementById('jd_skills').value.split(',').map(s=>s.trim()).filter(Boolean);"
        f"const nice=document.getElementById('jd_nice').value.split(',').map(s=>s.trim()).filter(Boolean);"
        f"const body={{title,company:document.getElementById('jd_company').value.trim(),role_family:document.getElementById('jd_role').value,yoe_min:parseInt(document.getElementById('jd_yoe_min').value)||null,yoe_max:parseInt(document.getElementById('jd_yoe_max').value)||null,mandatory_skills:skills,nice_to_have_skills:nice,description:document.getElementById('jd_desc').value.trim()}};"
        f"document.getElementById('createStatus').textContent='Saving...';"
        f"const res=await fetch('/jobs',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});const data=await res.json();"
        f"if(res.ok){{window.location.href='/jobs/'+data.jd_id;}}else{{document.getElementById('createStatus').textContent='Error: '+(data.detail||res.statusText);}}}}"
        f"async function closeJD(jdId){{if(!confirm('Close this JD? It will be removed from active listings.'))return;"
        f"await fetch('/jobs/'+jdId,{{method:'DELETE'}});location.reload();}}"
        f"</script></div></div></div></body></html>"
    )


@app.post("/jobs")
async def create_job(payload: dict):
    title = payload.get("title")
    if not title:
        raise HTTPException(status_code=400, detail="title is required.")
    jd_id = save_job_posting(payload)
    return JSONResponse(content={"status": "ok", "jd_id": jd_id})


@app.delete("/jobs/{jd_id}")
async def close_job(jd_id: str):
    ok = close_job_posting(jd_id)
    if not ok:
        raise HTTPException(status_code=404, detail="JD not found.")
    return JSONResponse(content={"status": "closed", "jd_id": jd_id})


# ===========================================================================
# JD Leaderboard UI
# ===========================================================================

@app.get("/jobs/{jd_id}", response_class=HTMLResponse)
def job_leaderboard(jd_id: str, user: dict = Depends(get_current_user)):
    jd = load_job_posting(jd_id)
    if jd is None:
        raise HTTPException(status_code=404, detail="JD not found.")
    from jd_match_store import list_jd_matches
    matches = list_jd_matches(jd_id)

    # Eval summary
    _eval_scores = [m.get("combined_score") for m in matches if m.get("combined_score") is not None]
    eval_avg = round(sum(_eval_scores) / len(_eval_scores), 1) if _eval_scores else 0
    eval_rec = {"SHORTLIST": 0, "SCREEN": 0, "REJECT": 0}
    for _m in matches:
        _r = _m.get("recommendation", "")
        if _r in eval_rec:
            eval_rec[_r] += 1
    _miss: dict = {}
    for _m in matches:
        for _s in ((_m.get("skill_match_details") or {}).get("missing_mandatory") or []):
            _miss[_s] = _miss.get(_s, 0) + 1
    top_missing = sorted(_miss.items(), key=lambda x: -x[1])[:6]

    title = jd.get("title", "JD")
    company = jd.get("company") or ""
    skills = jd.get("mandatory_skills") or []
    skill_names = [s if isinstance(s, str) else s.get("skill", "") for s in skills[:8]]

    def score_color(s):
        if s is None:
            return "#9CA3AF"
        return "#16A34A" if s >= 70 else "#D97706" if s >= 55 else "#DC2626"

    def bar(s):
        if s is None:
            return "\u2014"
        filled = int(round((s / 100) * 5))
        return "&#9646;" * filled + "&#9647;" * (5 - filled)

    rank = 0
    rows = ""
    for m in matches:
        rank += 1
        cid = m.get("candidate_id", "")
        name = m.get("candidate_name") or cid
        combined = m.get("combined_score")
        rec = m.get("recommendation", "")
        stage = m.get("rubric_stage", "resume")
        stage_color = {"panel": "#6366F1", "recruiter": "#D97706", "resume": "#62748E"}.get(stage, "#62748E")
        rec_cls = {"SHORTLIST": "tag-SHORTLIST", "SCREEN": "tag-SCREEN", "REJECT": "tag-REJECT"}.get(rec, "")
        fit_rs   = m.get("fit_reasons") or []
        fit_tip  = fit_rs[0] if fit_rs else ""
        rows += (
            f"<tr><td style='font-weight:700;color:var(--text2)'>{rank}</td>"
            f"<td><a href='/candidates/{cid}' style='font-weight:700;color:var(--primary2)'>{name}</a>"
            + (f"<div style='font-size:11px;color:var(--text2);margin-top:3px;font-style:italic;max-width:280px;line-height:1.4'>&#9733; {fit_tip}</div>" if fit_tip else "")
            + f"</td>"
            f"<td><a href='/jobs/{jd_id}/match/{cid}' style='color:{score_color(combined)};font-weight:700;font-size:16px;text-decoration:none'>"
            f"{combined if combined is not None else chr(8212)}</a>"
            f"<span style='font-size:10px;background:{stage_color};color:#fff;border-radius:99px;padding:1px 6px;font-weight:700;margin-left:6px'>{stage.upper()}</span></td>"
            f"<td style='color:var(--text2)'>{bar(combined)}</td>"
            f"<td><span class='{rec_cls}'>{rec}</span></td>"
            f"<td><a href='/jobs/{jd_id}/match/{cid}' class='btn' style='padding:4px 9px;font-size:11px;margin-right:4px'>Match Detail</a>"
            f"<a href='/candidates/{cid}' class='btn-sec' style='padding:4px 9px;font-size:11px;margin-right:4px'>Profile</a>"
            f"<button class='btn-sec' style='padding:4px 9px;font-size:11px' onclick=\"addToPipeline('{cid}','{jd_id}')\">+Pipeline</button></td></tr>"
        )

    skill_pills = "".join(f"<span class='pill'>{s}</span>" for s in skill_names)
    no_matches = "<div style='color:var(--muted);padding:20px 0'>No candidates matched yet. Click <b>Match All Candidates</b> to populate the leaderboard.</div>"
    table = (
        "<table class='table'><thead><tr><th>Rank</th><th>Candidate</th><th>Match Score</th>"
        "<th>Skill Fit</th><th>Rec.</th><th>Actions</th></tr></thead><tbody>"
        + rows + "</tbody></table>"
    ) if matches else no_matches

    # Eval summary card
    def _estat(lbl, val, color="#353395"):
        return (f"<div style='text-align:center;padding:12px 16px;background:var(--white);border:1px solid var(--border);"
                f"border-radius:10px;flex:1;min-width:80px'>"
                f"<div style='font-size:22px;font-weight:800;color:{color}'>{val}</div>"
                f"<div style='font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-top:2px'>{lbl}</div></div>")
    miss_pills = "".join(f"<span style='background:#FEE2E2;color:#991B1B;border-radius:99px;padding:2px 8px;font-size:11px;font-weight:500;margin:2px 3px 2px 0'>{s} \u00d7{n}</span>" for s, n in top_missing)
    eval_card = (
        f"<div class='card'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px'>"
        f"<div class='kicker'>Eval Overview &mdash; {len(matches)} candidates matched</div>"
        f"<a href='/evals' class='btn-sec' style='font-size:11px;padding:4px 12px'>Eval Workspace &rarr;</a></div>"
        f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px'>"
        f"{_estat('Avg Score', eval_avg)}"
        f"{_estat('Shortlist', eval_rec['SHORTLIST'], '#16A34A')}"
        f"{_estat('Screen', eval_rec['SCREEN'], '#D97706')}"
        f"{_estat('Reject', eval_rec['REJECT'], '#DC2626')}"
        f"<div style='flex:3;min-width:180px;background:var(--white);border:1px solid var(--border);border-radius:10px;padding:12px 14px'>"
        f"<div style='font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin-bottom:6px'>Top Missing Skills</div>"
        f"{miss_pills if miss_pills else '<div style=\"color:var(--text2);font-size:12px\">No missing skills data yet. Match candidates first.</div>'}"
        f"</div></div></div>"
    ) if matches else ""

    sidebar = _sidebar("jobs", user)
    matches_json = json.dumps(matches, ensure_ascii=False)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title} \u2014 Leaderboard</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"
        f"<div class='card'><div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px'>"
        f"<div><div class='kicker'><a href='/jobs' style='color:var(--text2)'>Jobs</a> &rsaquo; Leaderboard</div>"
        f"<h1 style='font-size:20px;font-weight:800;margin:4px 0 4px;color:var(--text)'>{title}</h1>"
        f"{'<div style=\"color:var(--primary2);font-size:13px;font-weight:600;margin-bottom:4px\">' + company + '</div>' if company else ''}"
        f"<div style='margin-bottom:8px'>{skill_pills}</div>"
        f"<div style='color:var(--text2);font-size:13px'>YoE: {jd.get('yoe_min','?')}\u2013{jd.get('yoe_max','?')} &bull; {len(matches)} candidates matched</div></div>"
        f"<div style='display:flex;gap:10px;flex-wrap:wrap'>"
        f"<button class='btn' onclick='matchAll()'>&#9889; Match All Candidates</button>"
        f"<button class='btn-sec' onclick='exportCSV()'>&#128202; CSV</button>"
        f"</div></div></div>"
        f"<div class='card' id='matchStatus' style='display:none;color:var(--text2)'></div>"
        f"{eval_card}"
        f"<div class='card'>"
        f"<div style='font-size:12px;color:var(--text2);margin-bottom:10px'>Match Score = JD fit score (adjusted by quality signals) &bull; Click score or <b>Match Detail</b> to see full breakdown &bull; Stage badge: last evaluation stage</div>"
        f"{table}</div></div></div></div>"
        f"<script>"
        f"const _matches={matches_json};"
        f"async function matchAll(){{const s=document.getElementById('matchStatus');s.style.display='';s.innerHTML='&#9889; Matching all candidates... This may take a moment.';"
        f"const res=await fetch('/jobs/{jd_id}/match_all',{{method:'POST'}});const data=await res.json();"
        f"if(res.ok){{s.innerHTML=`&#10003; Matched ${{data.matched}} candidates (${{data.failed}} failed). Reloading...`;setTimeout(()=>location.reload(),1500);}}"
        f"else{{s.innerHTML='Error: '+(data.detail||res.statusText);}}}}"
        f"async function addToPipeline(cid,jdId){{await fetch('/api/pipeline/add',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{candidate_id:cid,jd_id:jdId}})}});alert('Added to pipeline!');}}"
        f"function exportCSV(){{const rows=_matches;const hdr='rank,candidate_id,name,match_score,recommendation,stage';"
        f"const lines=rows.map((r,i)=>[i+1,r.candidate_id,r.candidate_name,r.combined_score,r.recommendation,r.rubric_stage].map(v=>JSON.stringify(v??'')).join(','));"
        f"const blob=new Blob([[hdr,...lines].join('\\n')],{{type:'text/csv'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='{jd_id}_leaderboard.csv';a.click();}}"
        f"</script></body></html>"
    )


@app.post("/jobs/{jd_id}/match_all")
async def match_all_candidates(jd_id: str):
    """Match all analyzed candidates against this JD."""
    jd = load_job_posting(jd_id)
    if jd is None:
        raise HTTPException(status_code=404, detail="JD not found.")
    from jd_matching_bridge import match_candidate_to_jd
    candidates = list_candidate_analyses()
    matched, failed = 0, 0
    errors = []
    for c in candidates:
        cid = c.get("candidate_id")
        if not cid:
            continue
        try:
            match_candidate_to_jd(cid, jd_id)
            matched += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{cid}: {exc}")
            logger.warning("match_all failed cid=%s jd=%s: %s", cid, jd_id, exc)
    return JSONResponse(content={"status": "ok", "jd_id": jd_id, "matched": matched, "failed": failed, "errors": errors[:5]})


# ===========================================================================
# Single candidate JD match endpoints
# ===========================================================================

@app.post("/candidates/{candidate_id}/match/{jd_id}")
async def match_candidate(candidate_id: str, jd_id: str):
    from jd_matching_bridge import match_candidate_to_jd
    try:
        result = match_candidate_to_jd(candidate_id, jd_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Matching failed: {exc}")
    return JSONResponse(content=result)


@app.get("/candidates/{candidate_id}/match/{jd_id}")
async def get_candidate_match(candidate_id: str, jd_id: str):
    from jd_match_store import load_jd_match
    result = load_jd_match(jd_id, candidate_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No match found. Run POST /candidates/{id}/match/{jd_id} first.")
    return JSONResponse(content=result)


from pydantic import BaseModel as _StatelessMatchBaseModel


class _StatelessMatchRequest(_StatelessMatchBaseModel):
    resume: dict
    jd: dict
    config: dict | None = None


@app.post("/match")
async def stateless_match(payload: _StatelessMatchRequest):
    """Stateless wrapper around jd_matching.engine.generate_match for external
    callers that hold their own resume/JD data (e.g. a separate portal) rather
    than IDs in this service's own candidate_analysis_store/job_posting_store."""
    from jd_matching.engine import generate_match
    hmi = {"config": payload.config} if payload.config else None
    try:
        result = generate_match(payload.resume, payload.jd, hmi)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Matching failed: {exc}")
    return JSONResponse(content=result)


# ===========================================================================
# Match Detail Page
# ===========================================================================

@app.get("/jobs/{jd_id}/match/{candidate_id}", response_class=HTMLResponse)
def match_detail_page(jd_id: str, candidate_id: str, user: dict = Depends(get_current_user)):
    """Full JD ↔ candidate match breakdown screen."""
    from jd_match_store import load_jd_match
    m = load_jd_match(jd_id, candidate_id)
    if m is None:
        raise HTTPException(status_code=404, detail="No match found. Run matching first from the leaderboard.")

    sidebar = _sidebar("jobs", user)

    # ── Core fields ──────────────────────────────────────────────────────────
    combined   = m.get("combined_score") or m.get("overall_score", 0) or 0
    jd_raw     = m.get("jd_match_score") or m.get("overall_score", 0) or 0
    cb         = m.get("combined_breakdown") or {}
    bert_delta = cb.get("bert_delta", 0)
    rubric_ref = cb.get("rubric_score_ref")
    bert_sigs  = cb.get("bert_signals_summary") or {}
    name       = m.get("candidate_name") or candidate_id
    jd_title   = m.get("jd_title") or jd_id
    rec        = m.get("recommendation", "")
    rec_color  = {"SHORTLIST": "#16A34A", "SCREEN": "#D97706", "REJECT": "#DC2626"}.get(rec, "#62748E")

    top_tiles   = m.get("top_tiles") or {}
    tile_rsns   = m.get("tile_reasons") or {}
    skill_match = m.get("skill_match_details") or {}
    semantic    = m.get("semantic_skill_analysis") or {}
    quick_view  = m.get("quick_view") or {}
    flags       = m.get("flags") or {}
    rq          = m.get("resume_quality") or {}
    jdf         = m.get("jd_fit") or {}
    screening_qs = m.get("screening_questions") or quick_view.get("screening_questions") or []
    auto_rejects = flags.get("auto_reject_reasons") or []
    warnings     = flags.get("warning_flags") or []
    strengths    = quick_view.get("top_strengths") or m.get("strengths") or []
    gaps_list    = quick_view.get("top_gaps") or m.get("risks") or []
    recruiter_summary = m.get("recruiter_summary", "")
    fit_reasons  = m.get("fit_reasons") or []
    adjacent_roles = m.get("adjacent_role_suggestions") or []
    exp_gap = m.get("experience_gap_display") or ""

    def _sc(s):
        if s is None: return "#9CA3AF"
        return "#16A34A" if s >= 70 else "#D97706" if s >= 50 else "#DC2626"

    def _bar(s):
        if s is None: return ""
        w = min(100, max(0, int(float(s))))
        return (f"<div style='height:5px;border-radius:99px;background:#F1F5F9;margin-top:4px'>"
                f"<div style='height:100%;width:{w}%;background:{_sc(s)};border-radius:99px'></div></div>")

    # ── 5-Cluster correlated scoring ─────────────────────────────────────────
    def _wt_avg(pairs):
        """Weighted average of [(weight, value), ...], skip None values."""
        valid = [(w, v) for w, v in pairs if v is not None]
        if not valid: return None
        tw = sum(w for w, v in valid)
        return round(sum(w * v for w, v in valid) / tw) if tw else None

    t = top_tiles  # shorthand
    clusters = [
        {
            "id": "technical",
            "icon": "🎯", "label": "Technical Stack",
            "desc": "Skill coverage, depth, and recency — does the candidate know the required tools and are those skills active?",
            "score": _wt_avg([(0.50, t.get("must_have_coverage")), (0.35, t.get("skill_depth")), (0.15, t.get("recent_relevance"))]),
            "dims": [("Must-Have Skills", "must_have_coverage", 0.50), ("Skill Depth", "skill_depth", 0.35), ("Recent Relevance", "recent_relevance", 0.15)],
        },
        {
            "id": "execution",
            "icon": "📋", "label": "Execution & Evidence",
            "desc": "Proven delivery — are claims backed by outcomes, ownership signals, and problem-solving track record?",
            "score": _wt_avg([(0.55, t.get("evidence_strength")), (0.25, rq.get("problem_solving_score")), (0.20, rq.get("ownership_score"))]),
            "dims": [("Evidence Strength", "evidence_strength", 0.55), ("Problem Solving", "__ps", 0.25), ("Ownership", "__ow", 0.20)],
        },
        {
            "id": "rolefit",
            "icon": "📊", "label": "Role & Level Fit",
            "desc": "Is this the right person for this specific role — seniority, experience depth, domain, and industry alignment?",
            "score": _wt_avg([(0.35, t.get("job_level_fit")), (0.30, t.get("experience_fit")), (0.25, t.get("domain_fit")), (0.10, t.get("industry_domain_fit"))]),
            "dims": [("Level Fit", "job_level_fit", 0.35), ("Experience Fit", "experience_fit", 0.30), ("Domain Fit", "domain_fit", 0.25), ("Industry Fit", "industry_domain_fit", 0.10)],
        },
        {
            "id": "credibility",
            "icon": "🏛", "label": "Profile Credibility",
            "desc": "Academic pedigree, company track record, and resume integrity — how trustworthy and credible is this profile?",
            "score": _wt_avg([(0.40, t.get("education_pedigree")), (0.35, t.get("company_pedigree")), (0.25, rq.get("integrity_score"))]),
            "dims": [("Education", "education_pedigree", 0.40), ("Company Pedigree", "company_pedigree", 0.35), ("Integrity", "__integ", 0.25)],
        },
        {
            "id": "practical",
            "icon": "📍", "label": "Practical Fit",
            "desc": "Location, work mode, and logistical compatibility for the role.",
            "score": t.get("location_fit"),
            "dims": [("Location Fit", "location_fit", 1.0)],
        },
    ]

    def _dim_val(key):
        """Get value for a dimension key (handles special __ps, __ow, __integ)."""
        if key == "__ps": return rq.get("problem_solving_score")
        if key == "__ow": return rq.get("ownership_score")
        if key == "__integ": return rq.get("integrity_score")
        return t.get(key)

    def _dim_reason(key):
        """Get reason text for a dimension key."""
        rsn = tile_rsns.get(f"{key}_reason") or tile_rsns.get(key, "")
        return rsn

    def _corr_narrative(cl):
        """Generate a correlated insight narrative for a cluster."""
        cid = cl["id"]; sc = cl["score"]
        t_mh = t.get("must_have_coverage"); t_sd = t.get("skill_depth"); t_rr = t.get("recent_relevance")
        t_ev = t.get("evidence_strength"); t_lf = t.get("job_level_fit"); t_ef = t.get("experience_fit")

        if cid == "technical":
            if t_mh is not None and t_sd is not None:
                gap = t_mh - t_sd
                if gap > 30:
                    note = (f"Coverage is strong ({t_mh}%) but depth lags ({t_sd}%) — candidate likely has surface-level familiarity with several required tools rather than deep production expertise. Skill depth and evidence are correlated: probe for specific production scenarios in screening.")
                elif gap < 10 and t_mh >= 60:
                    note = f"Coverage ({t_mh}%) and depth ({t_sd}%) are well-aligned — candidate demonstrates genuine expertise in the matched stack, not just keyword presence."
                elif t_mh < 50:
                    missing_str = ", ".join((skill_match.get("missing_mandatory") or [])[:3])
                    note = f"Critical gap: only {t_mh}% of mandatory skills matched. Core missing skills: {missing_str or 'see breakdown below'}. This is a fundamental stack mismatch."
                else:
                    note = f"Reasonable technical alignment — coverage at {t_mh}% with depth at {t_sd}%."
                if t_rr is not None and t_rr < 50:
                    note += f" Recency score ({t_rr}%) is low — verify that matched skills are still actively practiced."
                return note
            return "Run matching to see technical alignment analysis."

        if cid == "execution":
            t_ps = rq.get("problem_solving_score"); t_ow = rq.get("ownership_score")
            if t_ev is not None:
                if t_ev < 45 and t_sd is not None and t_sd >= 55:
                    return (f"Skill depth ({t_sd}%) is reasonable but evidence is weak ({t_ev}%) — the candidate may be technically capable but under-represents impact, lacks quantified outcomes, or hasn't worked in outcome-oriented environments. Ask for specific delivery examples.")
                elif t_ev >= 65:
                    return f"Strong evidence quality ({t_ev}%) — claims are supported by measurable outcomes and ownership signals. High signal-to-noise ratio in this profile."
                else:
                    return f"Moderate evidence ({t_ev}%) — some claims are backed by outcomes but others lack depth. Focus verification on the most critical mandatory skills."
            return "Evidence analysis not available — ensure semantic skill analysis ran."

        if cid == "rolefit":
            parts = []
            if t_lf is not None and t_lf < 60:
                parts.append(f"Level mismatch ({t_lf}%) — candidate appears {'overqualified' if (t_ef or 0) > 80 else 'under-qualified'} for this role.")
            if t_ef is not None and t_ef == 100:
                parts.append(f"Experience years well exceed the minimum — {'combined with level gap, overqualification risk is real' if t_lf and t_lf < 60 else 'a positive fit signal'}.")
            dn = t.get("domain_fit")
            if dn is not None and dn < 50:
                parts.append(f"Domain gap ({dn}%) — candidate comes from a different industry context and may need ramp time.")
            return " ".join(parts) or f"Role calibration is {'strong' if (sc or 0) >= 65 else 'mixed'} — review sub-dimension breakdown for specific misalignments."

        if cid == "credibility":
            t_ep = t.get("education_pedigree"); t_cp = t.get("company_pedigree"); t_int = rq.get("integrity_score")
            if t_ep is not None and t_cp is not None:
                if t_ep >= 60 and t_cp >= 55:
                    return f"Strong credibility profile — educational pedigree ({t_ep}%) and company track record ({t_cp}%) are solid anchors for this candidacy."
                elif t_ep < 40 or t_cp < 35:
                    return f"Mixed credibility signals — weaker pedigree scores ({t_ep}% edu / {t_cp}% company) mean demonstrated delivery evidence carries more weight than background alone."
                return f"Credibility signals are moderate — standard verification applies."
            return "Credibility scoring not available."

        if cid == "practical":
            loc = t.get("location_fit")
            if loc == 100: return "Location or remote mode is a full match — no relocation or logistics risk."
            if loc is not None and loc >= 75: return "Location is broadly compatible — minor logistics may apply."
            if loc is not None and loc < 50: return "Location mismatch — relocation or travel commitment required. Surface early in screening."
            return "Location not specified in JD — assumed neutral fit."
        return ""

    def _cluster_card(cl):
        sc = cl["score"]; c = _sc(sc); icon = cl["icon"]; lbl = cl["label"]; desc = cl["desc"]
        narrative = _corr_narrative(cl)
        w = min(100, max(0, int(float(sc)))) if sc is not None else 0
        bar_html = (f"<div style='height:6px;border-radius:99px;background:#F1F5F9;margin:8px 0 10px'>"
                    f"<div style='height:100%;width:{w}%;background:{c};border-radius:99px'></div></div>")
        dims_html = ""
        for dlbl, dkey, dwt in cl["dims"]:
            dv = _dim_val(dkey); dc = _sc(dv)
            dr = _dim_reason(dkey) if not dkey.startswith("__") else ""
            dr_short = (dr[:80] + "…") if len(dr) > 80 else dr
            wt_pct = int(dwt * 100)
            dims_html += (f"<div title='{dr}' style='background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 12px;flex:1;min-width:100px'>"
                          f"<div style='font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px'>"
                          f"{dlbl} <span style='font-weight:400;color:var(--border)'>·{wt_pct}%</span></div>"
                          f"<div style='font-size:20px;font-weight:800;color:{dc}'>{dv if dv is not None else '—'}"
                          f"<span style='font-size:10px;color:var(--text2);font-weight:400'>/100</span></div>"
                          f"{'<div style=\"font-size:10px;color:var(--text2);margin-top:3px;line-height:1.3\">' + dr_short + '</div>' if dr_short else ''}"
                          f"</div>")
        return (f"<div class='card' style='margin-bottom:14px'>"
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px'>"
                f"<div><div class='kicker' style='margin-bottom:0'>{icon} {lbl}</div>"
                f"<div style='font-size:11px;color:var(--text2);margin-top:2px'>{desc}</div></div>"
                f"<div style='font-size:32px;font-weight:900;color:{c};line-height:1'>{sc if sc is not None else '—'}"
                f"<span style='font-size:13px;color:var(--text2);font-weight:400'>/100</span></div>"
                f"</div>"
                f"{bar_html}"
                f"<div style='background:#F0F4FF;border:1px solid #DDE5FF;border-radius:8px;padding:10px 14px;margin-bottom:10px;font-size:13px;color:var(--text);line-height:1.55'>"
                f"<b style='color:var(--primary)'>Insight:</b> {narrative}</div>"
                f"<div style='display:flex;flex-wrap:wrap;gap:8px'>{dims_html}</div>"
                f"</div>")

    clusters_html = "".join(_cluster_card(cl) for cl in clusters)

    # Keep 11-D flat tiles for the deep-dive collapsible
    tile_defs = [
        ("must_have_coverage","Must-Have Skills","🎯"),("skill_depth","Skill Depth","🔬"),
        ("recent_relevance","Recent Relevance","📅"),("domain_fit","Domain Fit","🏢"),
        ("experience_fit","Experience Fit","⏱"),("evidence_strength","Evidence","📋"),
        ("education_pedigree","Education","🎓"),("company_pedigree","Company","🏆"),
        ("job_level_fit","Level Fit","📊"),("location_fit","Location","📍"),
        ("industry_domain_fit","Industry Fit","🌐"),
    ]
    def _tile(key, lbl, icon):
        sc = t.get(key); rsn = tile_rsns.get(f"{key}_reason") or tile_rsns.get(key, ""); c = _sc(sc)
        rsn_short = (rsn[:90] + "…") if len(rsn) > 90 else rsn
        return (f"<div title='{rsn}' style='background:var(--white);border:1px solid var(--border);border-radius:10px;padding:13px 14px;flex:1;min-width:120px'>"
                f"<div style='font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px'>{icon} {lbl}</div>"
                f"<div style='font-size:24px;font-weight:800;color:{c}'>{sc if sc is not None else '—'}<span style='font-size:11px;color:var(--text2);font-weight:400'>/100</span></div>"
                f"{_bar(sc)}"
                f"{'<div style=\"font-size:10px;color:var(--text2);margin-top:5px;line-height:1.3\">' + rsn_short + '</div>' if rsn_short else ''}"
                f"</div>")
    tiles_html = "".join(_tile(k, l, i) for k, l, i in tile_defs)

    # ── Skill Pills ──────────────────────────────────────────────────────────
    sem_lower = {k.lower(): v for k, v in semantic.items()}
    depth_icon = {"expert": "🟢", "applied": "🟡", "basic": "⚪"}
    def _spill(skill, bg, color, show_depth=True):
        di = ""
        if show_depth:
            sv = sem_lower.get(skill.lower()) or {}
            d = sv.get("depth", "")
            if d in depth_icon: di = " " + depth_icon[d]
        return (f"<span style='background:{bg};color:{color};border-radius:99px;padding:3px 10px;"
                f"font-size:12px;font-weight:500;margin:2px 4px 2px 0;display:inline-flex;align-items:center'>{skill}{di}</span>")

    def _skill_group(label, items, bg, color, show_depth=True):
        if not items: return ""
        pills = "".join(_spill(s, bg, color, show_depth) for s in items)
        return (f"<div style='margin-bottom:10px'>"
                f"<div style='font-size:11px;font-weight:700;color:{color};margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em'>{label} ({len(items)})</div>"
                f"{pills}</div>")

    skills_html = (
        _skill_group("✓ Matched Mandatory", skill_match.get("matched_mandatory") or [], "#DCFCE7", "#15803D") +
        _skill_group("≈ Adjacent Match", skill_match.get("adjacent_mandatory") or [], "#FEF3C7", "#92400E") +
        _skill_group("✗ Missing Mandatory", skill_match.get("missing_mandatory") or [], "#FEE2E2", "#991B1B", False) +
        _skill_group("+ Optional Matched", skill_match.get("matched_optional") or [], "#EEF2FF", "#4338CA") +
        _skill_group("★ Bonus Skills", (skill_match.get("bonus_skills") or [])[:20], "#F1F5F9", "#62748E", False)
    )

    # ── Flags ────────────────────────────────────────────────────────────────
    flags_html = ""
    if auto_rejects:
        flags_html += (f"<div style='background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;"
                       f"padding:10px 14px;margin-bottom:8px;font-size:13px;color:#DC2626'>"
                       f"<b>⚠ Auto-Reject:</b> " + " &bull; ".join(auto_rejects) + "</div>")
    if warnings:
        flags_html += (f"<div style='background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;"
                       f"padding:10px 14px;margin-bottom:8px;font-size:13px;color:#92400E'>"
                       f"<b>⚡ Warnings:</b> " + " &bull; ".join(warnings) + "</div>")

    # ── Screening questions ──────────────────────────────────────────────────
    sq_html = ""
    for i, q in enumerate(screening_qs[:8]):
        question = q.get("question", str(q)) if isinstance(q, dict) else str(q)
        intent   = q.get("intent", "") if isinstance(q, dict) else ""
        good     = q.get("what_good_answer_looks_like", "") if isinstance(q, dict) else ""
        sq_html += (f"<details style='border:1px solid var(--border);border-radius:8px;margin-bottom:6px;overflow:hidden'>"
                    f"<summary style='padding:10px 14px;cursor:pointer;font-weight:600;font-size:13px;"
                    f"list-style:none;display:flex;justify-content:space-between;align-items:center'>"
                    f"<span>Q{i+1}. {question}</span>"
                    f"{'<span style=\"font-size:11px;background:var(--bg);color:var(--text2);border-radius:99px;padding:2px 8px;margin-left:8px;font-weight:400\">' + intent + '</span>' if intent else ''}"
                    f"</summary>"
                    f"{'<div style=\"padding:10px 14px;font-size:13px;color:var(--text);background:#F9FAFB;border-top:1px solid var(--border);line-height:1.5\"><b style=\"color:var(--primary)\">Good answer:</b> ' + good + '</div>' if good else ''}"
                    f"</details>")

    # ── Semantic analysis table ──────────────────────────────────────────────
    depth_c = {"expert": "#16A34A", "applied": "#D97706", "basic": "#9CA3AF", "none": "#DC2626"}
    sem_rows = ""
    for skill, sv in list(semantic.items())[:40]:
        if not isinstance(sv, dict): continue
        d = sv.get("depth", ""); conf = sv.get("confidence", 0); own = sv.get("ownership_level", "")
        rcn = sv.get("recency_label", ""); outcome = "✓" if sv.get("outcome_signal") else "—"
        adj = "≈" if sv.get("adjacent_match") else ""; mtch = "✓" if sv.get("matched") else "—"
        dc = depth_c.get(d, "#9CA3AF")
        sem_rows += (f"<tr><td style='font-weight:600'>{skill}</td>"
                     f"<td style='color:{dc};font-weight:600'>{d or '—'}</td>"
                     f"<td>{conf}%</td><td style='color:var(--text2)'>{own}</td>"
                     f"<td style='color:var(--text2)'>{rcn}</td>"
                     f"<td style='color:#16A34A'>{outcome}</td>"
                     f"<td style='color:#D97706'>{adj}</td><td style='color:#16A34A'>{mtch}</td></tr>")
    sem_table = (
        f"<div style='overflow-x:auto'><table class='table'>"
        f"<thead><tr>{''.join(f'<th>{h}</th>' for h in ['Skill','Depth','Conf','Ownership','Recency','Outcome Signal','Adjacent','Matched'])}</tr></thead>"
        f"<tbody>{sem_rows}</tbody></table></div>") if sem_rows else "<div style='color:var(--text2)'>No semantic analysis data.</div>"

    # ── Inline metric bar ────────────────────────────────────────────────────
    def _metric(lbl, val):
        return (f"<div style='margin-bottom:10px'><div style='display:flex;justify-content:space-between;"
                f"font-size:12px;margin-bottom:3px'><span style='color:var(--text2)'>{lbl}</span>"
                f"<span style='font-weight:700;color:{_sc(val)}'>{val if val is not None else '—'}</span></div>"
                f"{_bar(val)}</div>")

    rq_html  = "".join(_metric(l, rq.get(k)) for l, k in [("Integrity","integrity_score"),("Evidence","evidence_strength"),("Skill Depth","skill_depth_score"),("Problem Solving","problem_solving_score"),("Ownership","ownership_score"),("Communication","communication_score")])
    jdf_html = "".join(_metric(l, jdf.get(k)) for l, k in [("Must-Have Coverage","must_have_coverage"),("Domain Fit","domain_fit"),("Experience Fit","experience_fit"),("Leadership Fit","leadership_fit"),("Semantic Alignment","semantic_alignment")])

    # ── BERT quality signals ─────────────────────────────────────────────────
    bert_html = "".join(
        f"<div style='display:inline-flex;flex-direction:column;align-items:center;background:var(--white);border:1px solid var(--border);border-radius:8px;padding:8px 14px;margin:4px 4px 4px 0'>"
        f"<div style='font-size:9px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.07em;margin-bottom:3px'>{lbl}</div>"
        f"<div style='font-weight:700;font-size:12px;color:var(--text)'>{bert_sigs.get(key) or '—'}</div></div>"
        for lbl, key in [("Role Family","role_family"),("DNA Fit","dna_fit"),("Skill Depth","skill_depth_tier"),("Career","career_progression"),("Stakeholder","stakeholder")]
    ) if bert_sigs else ""

    delta_note = (f"Base JD score: {jd_raw} + BERT quality adjustment: {'+' if bert_delta >= 0 else ''}{bert_delta} = <b>{combined}</b>") if bert_delta != 0 else f"JD match score: <b>{combined}</b>"

    # ── Strengths / gaps ─────────────────────────────────────────────────────
    str_html  = "".join(f"<div style='font-size:13px;padding:6px 0;border-bottom:1px solid var(--border);line-height:1.5'><span style='color:#16A34A;margin-right:6px'>✓</span>{s}</div>" for s in strengths) or "<div style='color:var(--text2)'>—</div>"
    gaps_html = "".join(f"<div style='font-size:13px;padding:6px 0;border-bottom:1px solid var(--border);line-height:1.5'><span style='color:#DC2626;margin-right:6px'>✗</span>{g}</div>" for g in gaps_list) or "<div style='color:var(--text2)'>—</div>"

    adj_roles_html = (" &bull; ".join(f"<span class='pill'>{r}</span>" for r in adjacent_roles)) if adjacent_roles else ""

    # ── Tavarah's Pick card (pre-computed to keep return as implicit concat) ──
    tavarah_html = (
        f"<div style='background:linear-gradient(135deg,#1E1B4B,#312E81);border-radius:16px;padding:20px 24px;margin-bottom:16px'>"
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:14px'>"
        f"<div style='background:rgba(255,255,255,0.15);border-radius:10px;padding:6px 10px;font-size:18px'>&#9733;</div>"
        f"<div><div style='font-size:13px;font-weight:800;color:#C7D2FE;text-transform:uppercase;letter-spacing:.1em'>Tavarah\u2019s Pick</div>"
        f"<div style='font-size:12px;color:rgba(199,210,254,0.7);margin-top:1px'>"
        f"5 reasons why {escape(name)} is the best fit for {escape(jd_title)}</div></div></div>"
        + "".join(
            f"<div style='display:flex;gap:12px;align-items:flex-start;margin-bottom:10px'>"
            f"<div style='min-width:22px;height:22px;background:rgba(255,255,255,0.12);border-radius:50%;"
            f"display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;color:#E0E7FF;flex-shrink:0'>{i+1}</div>"
            f"<div style='font-size:13px;color:#E0E7FF;line-height:1.55'>{escape(r)}</div></div>"
            for i, r in enumerate(fit_reasons[:5])
        )
        + f"</div>"
    ) if fit_reasons else ""

    # ── Final HTML ───────────────────────────────────────────────────────────
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{name} &times; {jd_title} &mdash; Match Detail</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}"
        f"<style>.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}"
        f".grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}"
        f"@media(max-width:900px){{.grid2,.grid3{{grid-template-columns:1fr}}}}</style>"
        f"</head><body><div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"

        # ── Hero ──
        f"<div class='card'><div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px'>"
        f"<div><div class='kicker'><a href='/jobs' style='color:var(--text2)'>Jobs</a> &rsaquo; "
        f"<a href='/jobs/{jd_id}' style='color:var(--text2)'>{jd_title}</a> &rsaquo; Match Detail</div>"
        f"<h1 style='font-size:22px;font-weight:800;margin:6px 0 4px'>{name}</h1>"
        f"<div style='color:var(--text2);font-size:13px'>{jd_title}"
        f"{' &bull; ' + exp_gap if exp_gap else ''}</div></div>"
        f"<div style='display:flex;align-items:center;gap:16px;flex-wrap:wrap'>"
        f"<div style='text-align:center'><div style='font-size:44px;font-weight:900;color:{rec_color};line-height:1'>{combined}</div>"
        f"<div style='font-size:11px;color:var(--text2);margin-top:2px'>/ 100</div></div>"
        f"<span style='background:{rec_color}18;color:{rec_color};border:1px solid {rec_color}44;border-radius:99px;padding:6px 16px;font-size:14px;font-weight:800'>{rec}</span>"
        f"</div></div>"
        f"<div style='margin-top:12px;display:flex;gap:10px;flex-wrap:wrap'>"
        f"<a href='/jobs/{jd_id}' class='btn-sec' style='font-size:12px;padding:5px 12px'>&larr; Leaderboard</a>"
        f"<a href='/candidates/{candidate_id}' class='btn-sec' style='font-size:12px;padding:5px 12px'>Candidate Profile</a>"
        f"<form method='post' action='/candidates/{candidate_id}/match/{jd_id}' style='display:inline'>"
        f"<button class='btn' style='font-size:12px;padding:5px 12px'>&#9889; Re-run Match</button></form>"
        f"</div></div>"
        f"{tavarah_html}"

        # ── Score overview: 3-col ──
        f"<div class='grid3'>"
        f"<div class='card'><div class='kicker'>JD Match Score</div>"
        f"<div style='font-size:11px;color:var(--text2);margin-bottom:12px'>{delta_note}</div>"
        f"{bert_html}"
        f"{'<div style=\"font-size:11px;color:var(--text2);margin-top:8px\">Rubric quality ref: ' + str(rubric_ref) + '/100</div>' if rubric_ref is not None else ''}"
        f"</div>"
        f"<div class='card'><div class='kicker'>Resume Quality</div>{rq_html}</div>"
        f"<div class='card'><div class='kicker'>JD Fit Breakdown</div>{jdf_html}</div>"
        f"</div>"

        # ── 5-Cluster match analysis ──
        f"{clusters_html}"

        # ── 11-D deep-dive (collapsible) ──
        f"<details class='card'><summary style='cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center'>"
        f"<span class='kicker' style='margin:0'>Raw 11-Dimension Scores</span>"
        f"<span style='font-size:12px;color:var(--text2)'>Click to expand</span></summary>"
        f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin-top:14px'>{tiles_html}</div></details>"

        # ── Flags ──
        f"{('<div style=\"margin-bottom:14px\">' + flags_html + '</div>') if flags_html else ''}"

        # ── Recruiter summary ──
        f"{'<div class=\"card\"><div class=\"kicker\" style=\"margin-bottom:6px\">Recruiter Summary</div><div style=\"font-size:13px;line-height:1.6;color:var(--text)\">' + recruiter_summary + '</div></div>' if recruiter_summary else ''}"

        # ── Strengths / Gaps ──
        f"<div class='grid2'>"
        f"<div class='card'><div class='kicker' style='margin-bottom:8px;color:#16A34A'>Strengths</div>{str_html}</div>"
        f"<div class='card'><div class='kicker' style='margin-bottom:8px;color:#DC2626'>Gaps &amp; Risks</div>{gaps_html}</div>"
        f"</div>"

        # ── Skill Match ──
        f"<div class='card'><div class='kicker' style='margin-bottom:12px'>Skill Match Analysis</div>"
        f"<div style='font-size:11px;color:var(--text2);margin-bottom:10px'>🟢 Expert &nbsp; 🟡 Applied &nbsp; ⚪ Basic</div>"
        f"{skills_html if skills_html else '<div style=\"color:var(--text2)\">No skill match data — run matching to populate.</div>'}"
        f"</div>"

        # ── Screening questions ──
        f"{'<div class=\"card\"><div class=\"kicker\" style=\"margin-bottom:10px\">Screening Questions (' + str(len(screening_qs[:8])) + ')</div>' + sq_html + '</div>' if sq_html else ''}"

        # ── Adjacent roles ──
        f"{'<div class=\"card\"><div class=\"kicker\" style=\"margin-bottom:8px\">Adjacent Role Suggestions</div>' + adj_roles_html + '</div>' if adj_roles_html else ''}"

        # ── Semantic deep dive ──
        f"<details class='card'><summary style='cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center'>"
        f"<span class='kicker' style='margin:0'>Deep Semantic Skill Analysis ({len(semantic)} skills)</span>"
        f"<span style='font-size:12px;color:var(--text2)'>Click to expand</span></summary>"
        f"<div style='margin-top:14px'>{sem_table}</div></details>"

        f"</div></div></div></body></html>"
    )


# ===========================================================================
# Candidate Profile Hub
# ===========================================================================

def _candidate_ui_css() -> str:
    return """
<style>
.cand-shell{padding:24px 24px 70px;background:#F5F7FD;min-height:100vh}
.cand-breadcrumb{display:flex;gap:8px;align-items:center;color:#7B879C;font-size:13px;font-weight:600;margin-bottom:18px}
.cand-breadcrumb a{color:#7B879C}
.cand-toolbar{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.cand-title{font-size:34px;font-weight:900;color:#1D2B52;letter-spacing:-.03em}
.cand-subtitle{font-size:14px;color:#6B7A90;margin-top:4px}
.cand-actions{display:flex;gap:10px;flex-wrap:wrap}
.cand-soft-btn{background:#fff;border:1px solid #D7DEEE;color:#49566E;border-radius:12px;padding:10px 14px;font-size:13px;font-weight:700;display:inline-flex;align-items:center;gap:7px}
.cand-primary-btn{background:#1F2A44;color:#fff;border:none;border-radius:12px;padding:10px 16px;font-size:13px;font-weight:800;display:inline-flex;align-items:center;gap:7px}
.cand-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}
.cand-stat{background:#fff;border:1px solid #E3E9F5;border-radius:18px;padding:18px 20px;box-shadow:0 10px 24px rgba(31,42,68,.04)}
.cand-stat-top{display:flex;align-items:center;gap:12px}
.cand-stat-icon{width:44px;height:44px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:16px}
.cand-stat-value{font-size:22px;font-weight:900;color:#14213D}
.cand-stat-label{font-size:13px;color:#7B879C;margin-top:2px}
.cand-filter-row{background:#fff;border:1px solid #E3E9F5;border-radius:18px;padding:14px 16px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.cand-filter-left,.cand-filter-right{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.cand-chip{background:#F4F0FF;color:#5B57EA;border:1px solid #DDD7FF;border-radius:12px;padding:9px 12px;font-size:13px;font-weight:700}
.cand-chip-muted{background:#fff;color:#546274;border:1px solid #D8E0EF;border-radius:12px;padding:9px 12px;font-size:13px;font-weight:700}
.cand-search{background:#fff;border:1px solid #D8E0EF;border-radius:12px;padding:10px 14px;font-size:13px;color:#25314D;min-width:280px}
.cand-panel{background:#fff;border:1px solid #E3E9F5;border-radius:20px;overflow:hidden;box-shadow:0 10px 24px rgba(31,42,68,.04)}
.cand-table{width:100%;border-collapse:collapse}
.cand-table th{padding:16px 14px;text-align:left;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:#95A2B6;border-bottom:1px solid #E9EEF8;background:#FBFCFF}
.cand-table td{padding:16px 14px;border-bottom:1px solid #EEF2F9;font-size:14px;color:#25314D;vertical-align:middle}
.cand-table tbody tr:hover{background:#FBFCFF}
.cand-person{display:flex;align-items:center;gap:12px}
.cand-avatar{width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;color:#fff;font-size:14px;flex-shrink:0}
.cand-name{font-size:14px;font-weight:800;color:#1E2B50}
.cand-meta{font-size:12px;color:#8B97AA;margin-top:2px}
.cand-pill{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:4px 10px;font-size:12px;font-weight:800;line-height:1}
.cand-pill-green{background:#E7F9EE;color:#148A44}
.cand-pill-amber{background:#FFF4D6;color:#B86B00}
.cand-pill-red{background:#FDE8E8;color:#D92D20}
.cand-pill-indigo{background:#EEF2FF;color:#4959F4}
.cand-score{font-size:14px;font-weight:900}
.cand-score-sub{font-size:12px;color:#7B879C;margin-top:2px}
.cand-link{color:#4959F4;font-weight:800}
.cand-empty{padding:28px;color:#7B879C}
.profile-shell{padding:24px 24px 70px;background:#F5F7FD;min-height:100vh}
.profile-topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.profile-hero{background:#fff;border:1px solid #E2E8F5;border-radius:24px;overflow:hidden;box-shadow:0 14px 34px rgba(31,42,68,.06);margin-bottom:18px}
.profile-hero-gradient{height:86px;background:linear-gradient(90deg,#4A43D6 0%,#788AF2 48%,#F0B7EB 100%)}
.profile-hero-body{padding:0 24px 24px;display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px}
.profile-main{display:flex;gap:18px;align-items:flex-start;margin-top:-28px}
.profile-avatar{width:78px;height:78px;border-radius:18px;background:linear-gradient(135deg,#28459D,#1F347B);display:flex;align-items:center;justify-content:center;color:#fff;font-size:28px;font-weight:900;border:4px solid #fff;box-shadow:0 10px 26px rgba(40,69,157,.18);flex-shrink:0}
.profile-name{font-size:34px;font-weight:900;color:#1B2744;letter-spacing:-.03em}
.profile-roleline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px}
.profile-kicker{font-size:13px;color:#7E8BA0;font-weight:600}
.profile-tag{display:inline-flex;align-items:center;border-radius:999px;padding:4px 10px;font-size:12px;font-weight:800}
.profile-tag-amber{background:#FFF1DD;color:#D97706;border:1px solid #FED7AA}
.profile-tag-indigo{background:#EEF2FF;color:#4F46E5;border:1px solid #C7D2FE}
.profile-summary{font-size:15px;line-height:1.7;color:#52637B;margin-top:12px;max-width:880px}
.profile-meta-strip{display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;color:#5E6F86;font-size:13px;font-weight:600}
.profile-actions{display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;padding-top:18px}
.profile-outline-btn{background:#fff;border:1px solid #CAD5E8;color:#506078;border-radius:12px;padding:10px 14px;font-size:13px;font-weight:800}
.profile-dark-btn{background:#1F2A44;color:#fff;border:none;border-radius:12px;padding:10px 16px;font-size:13px;font-weight:900}
.profile-grid{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;align-items:start}
.profile-card{background:#fff;border:1px solid #E2E8F5;border-radius:22px;box-shadow:0 14px 34px rgba(31,42,68,.05);overflow:hidden}
.profile-card-header{padding:16px 20px;background:linear-gradient(90deg,#4A43D6 0%,#7982F0 100%);color:#fff;display:flex;justify-content:space-between;align-items:center;gap:10px}
.profile-card-title{font-size:18px;font-weight:900}
.profile-card-sub{font-size:12px;color:rgba(255,255,255,.82)}
.profile-card-body{padding:18px 20px}
.profile-info-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.profile-mini-title{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#97A3B6;font-weight:900;margin-bottom:8px}
.profile-big-num{font-size:18px;font-weight:900;color:#1F2A44}
.profile-mini-text{font-size:13px;color:#5C6D84;line-height:1.6}
.profile-chip-row{display:flex;gap:8px;flex-wrap:wrap}
.profile-chip{display:inline-flex;align-items:center;gap:6px;background:#EEF2FF;color:#4F46E5;border:1px solid #D6DFFF;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:800}
.profile-section-spacer{margin-top:18px}
.profile-skill-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.profile-skill-card{background:#F9FBFF;border:1px solid #E6ECF8;border-radius:18px;padding:16px}
.profile-skill-card h4{font-size:14px;font-weight:900;color:#213054;margin-bottom:8px}
.profile-skill-card p{font-size:12px;color:#7B879C;margin-bottom:10px}
.profile-skill-score{font-size:28px;font-weight:900;color:#3659B5;line-height:1}
.profile-skill-score small{font-size:13px;color:#8B97AA;font-weight:700}
.profile-skill-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.profile-skill-tags span{background:#EEF4FF;border:1px solid #D4DFFF;border-radius:10px;padding:4px 8px;font-size:11px;color:#3A57A8;font-weight:800}
.profile-timeline{display:flex;flex-direction:column;gap:14px}
.profile-timeline-item{display:grid;grid-template-columns:26px minmax(0,1fr);gap:14px}
.profile-timeline-dot{display:flex;justify-content:center}
.profile-timeline-dot span{display:block;width:12px;height:12px;border-radius:50%;background:#6478F3;box-shadow:0 0 0 4px #EEF2FF;margin-top:20px}
.profile-role-card{background:#fff;border:1px solid #E8EEF9;border-radius:18px;padding:16px 18px}
.profile-role-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.profile-role-title{font-size:22px;font-weight:900;color:#1E2B50}
.profile-role-company{font-size:14px;color:#5A62D6;font-weight:800}
.profile-role-body{font-size:14px;color:#5B6D83;line-height:1.7}
.profile-jobs-table{width:100%;border-collapse:collapse}
.profile-jobs-table th{padding:12px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#95A2B6;border-bottom:1px solid #E8EEF9}
.profile-jobs-table td{padding:14px 10px;border-bottom:1px solid #EEF2F8;font-size:14px;color:#28354D}
.profile-note{font-size:12px;color:#8B97AA;line-height:1.6}
.profile-danger-list,.profile-good-list{padding-left:18px}
.profile-danger-list li,.profile-good-list li{margin-bottom:8px;font-size:14px;line-height:1.6}
.profile-good-list li{color:#2A5A3A}
.profile-danger-list li{color:#7A4B00}
.profile-raw{background:#0F172A;color:#D9E4FF;border-radius:16px;padding:16px;overflow:auto;max-height:420px;font-size:12px}
.profile-side-score{display:flex;align-items:center;justify-content:center;gap:18px;padding:10px 0 14px;border-bottom:1px solid #EDF2FD}
.profile-gauge{position:relative;width:118px;height:118px;border-radius:50%;background:conic-gradient(#5A53EA var(--pct), #DDE5FB 0)}
.profile-gauge::after{content:"";position:absolute;inset:12px;background:#fff;border-radius:50%}
.profile-gauge-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:1}
.profile-side-badges{display:flex;flex-direction:column;gap:10px}
.profile-side-pill{border-radius:999px;padding:8px 12px;font-size:13px;font-weight:800;display:inline-flex;align-items:center;gap:8px}
.profile-side-pill.indigo{background:#EEF2FF;color:#4F46E5;border:1px solid #C7D2FE}
.profile-side-pill.amber{background:#FFF4D6;color:#C56D0A;border:1px solid #FAD27E}
.profile-dim-list{display:flex;flex-direction:column;gap:16px;padding-top:16px}
.profile-dim-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
.profile-dim-name{font-size:14px;font-weight:900;color:#1F2A44}
.profile-dim-score{font-size:13px;font-weight:800;color:#4F46E5}
.profile-dim-bar{display:grid;grid-template-columns:repeat(10,1fr);gap:4px}
.profile-dim-bar span{display:block;height:18px;border-radius:5px;background:#E8EEFD}
.profile-dim-bar span.on{background:#5A53EA}
.profile-dim-note{font-size:12px;color:#6E7D92;margin-top:6px;line-height:1.45}
@media(max-width:1180px){.profile-hero-body,.profile-grid{grid-template-columns:1fr}.profile-actions{justify-content:flex-start}.profile-skill-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:980px){.cand-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.profile-info-grid{grid-template-columns:1fr}.profile-skill-grid{grid-template-columns:1fr}}
@media(max-width:760px){.cand-stats{grid-template-columns:1fr}.cand-search{min-width:unset;width:100%}.profile-main{flex-direction:column}.profile-name{font-size:28px}}
</style>
"""


def _score_bucket_label(score: float | int | None) -> tuple[str, str]:
    if score is None:
        return ("Unscored", "cand-pill-indigo")
    try:
        score = float(score)
    except Exception:
        return ("Unscored", "cand-pill-indigo")
    if score >= 85:
        return ("Excellent", "cand-pill-green")
    if score >= 70:
        return ("Good", "cand-pill-indigo")
    if score >= 55:
        return ("Moderate", "cand-pill-amber")
    return ("Needs review", "cand-pill-red")


def _stage_pill(stage: str | None) -> str:
    stage = (stage or "Active").strip()
    klass = "cand-pill-indigo"
    if stage in {"Shortlisted", "Telephonic", "Panel", "Hired"}:
        klass = "cand-pill-green"
    elif stage in {"Rejected"}:
        klass = "cand-pill-red"
    elif stage in {"Applied"}:
        klass = "cand-pill-amber"
    return f"<span class='cand-pill {klass}'>{escape(stage)}</span>"


def _candidate_palette(seed: str) -> str:
    colors = ["#4F46E5", "#0EA5E9", "#16A34A", "#F97316", "#9333EA", "#EF4444", "#14B8A6", "#3B82F6"]
    return colors[sum(ord(ch) for ch in seed) % len(colors)]


@app.get("/candidates", response_class=HTMLResponse)
def candidates_page(user: dict = Depends(get_current_user)):
    from pipeline_store import list_pipeline
    from jd_match_store import list_jd_matches

    candidates = list_candidate_analyses()
    score_map = {row.get("candidate_id"): row for row in list_candidate_scores()}
    pipeline_map = {row.get("candidate_id"): row for row in list_pipeline()}
    active_jobs = list_job_postings(include_closed=False)

    match_count_map: dict[str, int] = {}
    for jd in active_jobs:
        jd_id = jd.get("jd_id")
        if not jd_id:
            continue
        try:
            for match in list_jd_matches(jd_id):
                cid = match.get("candidate_id")
                if cid:
                    match_count_map[cid] = match_count_map.get(cid, 0) + 1
        except Exception:
            continue

    rows_html = ""
    shortlisted = 0
    pending_actions = 0
    active_count = 0
    for row in candidates:
        cid = row.get("candidate_id") or ""
        if not cid:
            continue
        analysis = load_candidate_analysis(cid) or {}
        overview = analysis.get("candidate_overview") or {}
        exp = analysis.get("experience_analysis") or {}
        semantic = analysis.get("semantic_analysis") or {}
        score_entry = score_map.get(cid) or {}
        pipe = pipeline_map.get(cid) or {}
        stage = pipe.get("stage") or "Active"
        if stage not in {"Rejected", "Hired"}:
            active_count += 1
        if stage == "Shortlisted":
            shortlisted += 1
        total_score = score_entry.get("current_total") or row.get("resume_score_100")
        _rubric_cand = analysis.get("rubric_scorecard") or {}
        _arch_cand = _rubric_cand.get("archetype") or ""
        _arch_total_cand = _rubric_cand.get("archetype_total_score")
        _hard_cand = (_rubric_cand.get("red_flags") or {}).get("hard") or []
        _disp_cand = float(_arch_total_cand) if _arch_total_cand is not None else total_score
        score_label, _ = _score_bucket_label(_disp_cand)
        location = overview.get("location") or "Unknown"
        yoe = exp.get("total_experience_years")
        yoe_txt = f"{float(yoe):.1f} yrs" if yoe not in (None, "") else "—"
        role = (semantic.get("top_role_family") or row.get("role_family") or "Candidate").replace("_", " ").title()
        employer = (exp.get("companies") or ["—"])[0] if isinstance(exp.get("companies"), list) and exp.get("companies") else "—"
        phone = overview.get("phone") or overview.get("email") or "—"
        jobs = match_count_map.get(cid, 0)
        relocate = exp.get("relocation_flexibility_signal")
        work_pref = "Open to Reloc." if relocate is True else "No Relocation" if relocate is False else "Flexible"
        action_count = 0
        current_stage = (score_entry.get("current_stage") or "").strip().lower()
        if current_stage == "resume":
            action_count += 1
        if stage in {"Shortlisted", "Telephonic", "Panel"} and current_stage != "panel":
            action_count += 1
        pending_actions += action_count
        avatar = "".join(part[:1] for part in (overview.get("name") or row.get("name") or cid).split()[:2]).upper() or "C"
        avatar_color = _candidate_palette(cid)
        rows_html += (
            f"<tr data-search='{escape((overview.get('name') or row.get('name') or cid) + ' ' + role + ' ' + location + ' ' + employer).lower()}'>"
            f"<td><div class='cand-person'><div class='cand-avatar' style='background:{avatar_color}'>{escape(avatar)}</div><div>"
            f"<div class='cand-name'><a class='cand-link' href='/candidates/{escape(cid)}'>{escape(overview.get('name') or row.get('name') or cid)}</a></div>"
            f"<div class='cand-meta'>{escape(cid)}</div></div></div></td>"
            f"<td><div style='font-weight:800;color:#1E2B50'>{escape(role)}</div><div class='cand-meta'>@ {escape(employer)}</div></td>"
            f"<td>{escape(str(phone))}</td><td>{escape(str(location))}</td><td>{escape(yoe_txt)}</td>"
            f"<td><div class='cand-score' style='color:{'#16A34A' if (_disp_cand or 0) >= 85 else '#4F46E5' if (_disp_cand or 0) >= 70 else '#D97706' if (_disp_cand or 0) >= 55 else '#DC2626'}'>{'—' if _disp_cand is None else int(round(float(_disp_cand)))}</div>"
            f"<div class='cand-score-sub'>{escape(score_label)}{'<span style=\"background:#EEF2FF;color:#4F46E5;border-radius:4px;padding:1px 5px;font-size:10px;margin-left:4px;font-weight:900\">' + escape(_arch_cand) + '</span>' if _arch_cand else ''}{'<span style=\"color:#B91C1C;font-size:10px;margin-left:3px\" title=\"Hard flags detected\">&#x1F6AB;</span>' if _hard_cand else ''}</div></td>"
            f"<td>{_stage_pill(stage)}</td>"
            f"<td><span class='cand-pill cand-pill-indigo'>{jobs} Job{'s' if jobs != 1 else ''}</span></td>"
            f"<td>{('<span class=\"cand-pill cand-pill-red\">' + str(action_count) + ' Action' + ('s' if action_count != 1 else '') + '</span>') if action_count else '<span class=\"cand-meta\">—</span>'}</td>"
            f"<td style='font-weight:700;color:{'#15803D' if work_pref == 'Open to Reloc.' else '#DC2626' if work_pref == 'No Relocation' else '#4F46E5'}'>{escape(work_pref)}</td></tr>"
        )

    total_candidates = len(candidates)
    stats = [
        ("12", "#EAF7F1", "#0F9D7A", "Total Candidates", str(total_candidates)),
        ("2", "#EEF9F0", "#1E9B4D", "Active", str(active_count)),
        ("3", "#F4EEFF", "#7C3AED", "Shortlisted", str(shortlisted)),
        ("!", "#FEF1EF", "#DC2626", "Action Pending", str(pending_actions)),
    ]
    stat_html = "".join(
        f"<div class='cand-stat'><div class='cand-stat-top'><div class='cand-stat-icon' style='background:{bg};color:{fg}'>{icon}</div><div><div class='cand-stat-value'>{escape(val)}</div><div class='cand-stat-label'>{label}</div></div></div></div>"
        for icon, bg, fg, label, val in stats
    )
    sidebar = _sidebar("candidates", user)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Candidates</title><meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}{_candidate_ui_css()}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='cand-shell'><div class='cand-breadcrumb'><a href='/'>Dashboard</a><span>/</span><span>Candidates</span></div>"
        f"<div class='cand-toolbar'><div><div class='cand-title'>Candidates</div><div class='cand-subtitle'>{total_candidates} total · {pending_actions} action pending</div></div><div class='cand-actions'><a href='/upload' class='cand-primary-btn'>+ Add Candidate</a></div></div>"
        f"<div class='cand-stats'>{stat_html}</div>"
        f"<div class='cand-filter-row'><div class='cand-filter-left'><span style='font-size:13px;color:#718096;font-weight:700'>Show me</span><span class='cand-chip'>Active</span><span class='cand-chip-muted'>Role / Title</span><span class='cand-chip-muted'>Pipeline Status</span><span class='cand-chip-muted'>Score Bucket</span><span class='cand-chip-muted'>Notice Period</span></div>"
        f"<div class='cand-filter-right'><input id='candSearch' class='cand-search' placeholder='Search candidates by name, role, location, company'><a href='/jobs' class='cand-soft-btn'>Jobs</a></div></div>"
        f"<div class='cand-panel'><table class='cand-table'><thead><tr><th>Candidate</th><th>Role</th><th>Contact No.</th><th>Location</th><th>Exp.</th><th>Score</th><th>Pipeline Status</th><th>Jobs</th><th>Actions</th><th>Work Preference</th></tr></thead><tbody id='candTableBody'>{rows_html or '<tr><td colspan=\"10\" class=\"cand-empty\">No candidates analysed yet.</td></tr>'}</tbody></table></div></div></div>"
        f"<script>const q=document.getElementById('candSearch');if(q)q.addEventListener('input',()=>{{const v=q.value.trim().toLowerCase();document.querySelectorAll('#candTableBody tr[data-search]').forEach(tr=>{{tr.style.display=!v||tr.dataset.search.includes(v)?'':'none';}});}});</script></body></html>"
    )


# ===========================================================================
# Outcomes — placement tracking & analytics
# ===========================================================================

def _outcomes_page(user: dict | None = None) -> str:
    from database import outcomes_summary
    from html import escape as _e
    summary = outcomes_summary()
    total   = summary["total"]
    by_oc   = summary["by_outcome"]
    by_band = summary["by_band"]
    by_role = summary["by_role"]
    recent  = summary["recent"]

    placed   = by_oc.get("PLACED", 0)
    rejected = by_oc.get("REJECTED", 0)
    withdrew = by_oc.get("WITHDREW", 0)
    in_prog  = by_oc.get("IN_PROGRESS", 0)
    rate_pct = round(placed / total * 100) if total else 0

    # KPI tiles
    def _kpi(label, val, color, sub=""):
        return (f"<div style='background:var(--white);border:1px solid var(--border);border-radius:14px;"
                f"padding:18px 22px;text-align:center'>"
                f"<div style='font-size:34px;font-weight:900;color:{color};line-height:1'>{val}</div>"
                f"<div style='font-size:12px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em;margin-top:4px'>{label}</div>"
                f"{'<div style=\"font-size:11px;color:var(--text2);margin-top:2px\">' + sub + '</div>' if sub else ''}"
                f"</div>")

    kpis = (
        f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px'>"
        + _kpi("Total Tracked", total, "var(--primary)")
        + _kpi("Placed", placed, "#16A34A")
        + _kpi("Placement Rate", f"{rate_pct}%", "#16A34A" if rate_pct >= 50 else "#D97706")
        + _kpi("Rejected", rejected, "#DC2626")
        + _kpi("Withdrew", withdrew, "#D97706")
        + _kpi("In Progress", in_prog, "#6366F1")
        + f"</div>"
    )

    # Score band breakdown
    band_order = ["80+", "60-79", "40-59", "<40"]
    band_colors = {"80+": "#16A34A", "60-79": "#3B82F6", "40-59": "#D97706", "<40": "#DC2626"}
    band_rows = ""
    for b in band_order:
        bd = by_band.get(b, {"total": 0, "placed": 0})
        bt, bp = bd["total"], bd["placed"]
        if bt == 0:
            continue
        brate = round(bp / bt * 100)
        bc = band_colors.get(b, "#9CA3AF")
        band_rows += (
            f"<tr style='border-bottom:1px solid var(--border)'>"
            f"<td style='padding:9px 12px;font-weight:600;color:{bc}'>{b}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{bt}</td>"
            f"<td style='padding:9px 12px;text-align:center;color:#16A34A;font-weight:600'>{bp}</td>"
            f"<td style='padding:9px 12px'>"
            f"<div style='display:flex;align-items:center;gap:8px'>"
            f"<div style='flex:1;background:var(--bg);border-radius:999px;height:8px;overflow:hidden'>"
            f"<div style='background:{bc};height:8px;width:{brate}%;border-radius:999px'></div></div>"
            f"<span style='font-size:12px;font-weight:700;color:{bc};min-width:32px'>{brate}%</span>"
            f"</div></td></tr>"
        )
    band_table = (
        f"<table style='width:100%;border-collapse:collapse'>"
        f"<thead><tr style='border-bottom:2px solid var(--border)'>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Score Band</th>"
        f"<th style='text-align:center;padding:8px 12px;font-size:12px;color:var(--text2)'>Total</th>"
        f"<th style='text-align:center;padding:8px 12px;font-size:12px;color:var(--text2)'>Placed</th>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Placement Rate</th>"
        f"</tr></thead><tbody>{band_rows or '<tr><td colspan=4 style=\"padding:12px;color:var(--text2);font-size:13px\">No data yet.</td></tr>'}</tbody></table>"
    )

    # Role family breakdown (top 8)
    role_rows = ""
    for rf, rd in sorted(by_role.items(), key=lambda x: -x[1]["total"])[:8]:
        if rd["total"] == 0:
            continue
        rrate = round(rd["placed"] / rd["total"] * 100)
        role_rows += (
            f"<tr style='border-bottom:1px solid var(--border)'>"
            f"<td style='padding:9px 12px;font-weight:500'>{_e(rf.replace('_',' ').title())}</td>"
            f"<td style='padding:9px 12px;text-align:center'>{rd['total']}</td>"
            f"<td style='padding:9px 12px;text-align:center;color:#16A34A;font-weight:600'>{rd['placed']}</td>"
            f"<td style='padding:9px 12px'>"
            f"<div style='display:flex;align-items:center;gap:8px'>"
            f"<div style='flex:1;background:var(--bg);border-radius:999px;height:8px;overflow:hidden'>"
            f"<div style='background:var(--primary);height:8px;width:{rrate}%;border-radius:999px'></div></div>"
            f"<span style='font-size:12px;font-weight:700;color:var(--primary);min-width:32px'>{rrate}%</span>"
            f"</div></td></tr>"
        )

    # Recent outcomes table
    OC_COLORS = {"PLACED": "#16A34A", "REJECTED": "#DC2626", "WITHDREW": "#D97706", "IN_PROGRESS": "#6366F1"}
    recent_rows = ""
    for r in recent:
        oc   = r.get("outcome") or "IN_PROGRESS"
        col  = OC_COLORS.get(oc, "#9CA3AF")
        nm   = _e(r.get("name") or r["candidate_id"])
        sc   = r.get("panel_score") or r.get("recruiter_score") or r.get("resume_score") or 0
        rf2  = _e((r.get("role_family") or "").replace("_", " ").title())
        co   = _e(r.get("placed_company") or r.get("rejection_stage") or "")
        rb   = _e(r.get("recorded_by") or "")
        cid2 = _e(r["candidate_id"])
        recent_rows += (
            f"<tr style='border-bottom:1px solid var(--border)'>"
            f"<td style='padding:9px 12px'><a href='/candidates/{cid2}' style='font-weight:600;color:var(--primary)'>{nm}</a></td>"
            f"<td style='padding:9px 12px;font-size:13px;color:var(--text2)'>{rf2}</td>"
            f"<td style='padding:9px 12px;text-align:center;font-weight:700'>{int(float(sc)) if sc else '—'}</td>"
            f"<td style='padding:9px 12px'><span style='background:{col}18;color:{col};border:1px solid {col}44;border-radius:999px;padding:2px 9px;font-size:11px;font-weight:700'>{oc.replace('_',' ')}</span></td>"
            f"<td style='padding:9px 12px;font-size:12px;color:var(--text2)'>{co}</td>"
            f"<td style='padding:9px 12px;font-size:12px;color:var(--text2)'>{rb}</td>"
            f"</tr>"
        )

    sidebar = _sidebar("outcomes", user)
    return (
        f"<!DOCTYPE html><html><head>"
        f"<meta charset='utf-8'><title>Outcomes \u00b7 Resume Intelligence</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"{_BASE_CSS}"
        f"</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"
        f"<div style='margin-bottom:20px'>"
        f"<div class='kicker'>Analytics</div>"
        f"<h1 style='margin:4px 0 6px;font-size:24px;font-weight:800'>Placement Outcomes</h1>"
        f"<p style='color:var(--text2);font-size:13px;margin:0'>Track candidate placement results to build your outcome data flywheel. "
        f"Record outcomes on individual candidate pages.</p></div>"
        + kpis
        + f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px'>"
        f"<div class='card' style='margin:0'><h2>By Score Band</h2>{band_table}</div>"
        f"<div class='card' style='margin:0'><h2>By Role Family</h2>"
        f"<table style='width:100%;border-collapse:collapse'>"
        f"<thead><tr style='border-bottom:2px solid var(--border)'>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Role Family</th>"
        f"<th style='text-align:center;padding:8px 12px;font-size:12px;color:var(--text2)'>Total</th>"
        f"<th style='text-align:center;padding:8px 12px;font-size:12px;color:var(--text2)'>Placed</th>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Rate</th>"
        f"</tr></thead><tbody>"
        + (role_rows or "<tr><td colspan=4 style='padding:12px;color:var(--text2);font-size:13px'>No data yet.</td></tr>")
        + f"</tbody></table></div></div>"
        f"<div class='card'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px'>"
        f"<h2 style='margin:0'>Recent Outcomes</h2>"
        f"<span style='font-size:12px;color:var(--text2)'>Record outcomes on candidate profile pages</span></div>"
        f"<div style='overflow-x:auto'><table style='width:100%;border-collapse:collapse'>"
        f"<thead><tr style='border-bottom:2px solid var(--border)'>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Candidate</th>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Role Family</th>"
        f"<th style='text-align:center;padding:8px 12px;font-size:12px;color:var(--text2)'>Score</th>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Outcome</th>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Details</th>"
        f"<th style='text-align:left;padding:8px 12px;font-size:12px;color:var(--text2)'>Recorded By</th>"
        f"</tr></thead><tbody>"
        + (recent_rows or "<tr><td colspan=6 style='padding:16px;color:var(--text2);font-size:13px;text-align:center'>No outcomes recorded yet. Open a candidate profile to record the first one.</td></tr>")
        + f"</tbody></table></div></div>"
        f"</div></div></div></body></html>"
    )


@app.get("/outcomes", response_class=HTMLResponse)
def outcomes_page(user: dict = Depends(get_current_user)):
    return _outcomes_page(user)


@app.post("/outcome/{candidate_id}")
def record_outcome(candidate_id: str, payload: dict):
    from database import upsert_outcome
    upsert_outcome(candidate_id, payload)
    return {"ok": True, "candidate_id": candidate_id, "outcome": payload.get("outcome")}


def _render_candidate_profile_v2(candidate_id: str, user: dict | None = None) -> HTMLResponse:  # noqa: C901
    from database import get_candidate
    from pipeline_store import get_pipeline_entry
    from jd_match_store import list_jd_matches
    from candidate_score_store import load_candidate_score as _lcs2
    from database import get_outcome as _goc

    row = get_candidate(candidate_id)
    analysis = (row.get("analysis") or {}) if row else {}
    if not analysis:
        analysis = load_candidate_analysis(candidate_id) or {}
    if not analysis and not row:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if not row:
        row = {}

    scores_data = _lcs2(candidate_id) or {}
    oc_data    = _goc(candidate_id) or {}
    oc_status  = oc_data.get("outcome") or "IN_PROGRESS"
    _OC_COLORS = {
        "PLACED":      ("#16A34A", "#DCFCE7", "#86EFAC"),
        "REJECTED":    ("#DC2626", "#FEF2F2", "#FECACA"),
        "WITHDREW":    ("#D97706", "#FFF7ED", "#FDE68A"),
        "IN_PROGRESS": ("#6366F1", "#EEF2FF", "#C7D2FE"),
    }
    _oc_fg, _oc_bg, _oc_bd = _OC_COLORS.get(oc_status, _OC_COLORS["IN_PROGRESS"])
    overview    = analysis.get("candidate_overview") or {}
    semantic    = analysis.get("semantic_analysis") or {}
    exp         = analysis.get("experience_analysis") or {}
    edu         = analysis.get("education_analysis") or {}
    scorecard   = analysis.get("scorecard") or {}
    rubric      = analysis.get("rubric_scorecard") or {}
    qualitative = analysis.get("qualitative_analysis") or {}
    dna_obj     = analysis.get("dna_fit") or {}

    # ── Basic info ──
    name     = overview.get("name") or analysis.get("candidate_name") or row.get("name") or candidate_id
    avatar   = "".join(p[:1] for p in name.split()[:2]).upper() or "C"
    email    = overview.get("email") or row.get("email") or ""
    phone    = overview.get("phone") or ""
    location = overview.get("location") or ""
    summary  = overview.get("profile_summary") or analysis.get("recruiter_summary") or ""
    recruiter_summary = analysis.get("recruiter_summary") or ""
    role_family_raw = semantic.get("top_role_family") or row.get("role_family") or ""
    role_family = role_family_raw.replace("_", " ").title()
    yoe = exp.get("total_experience_years") or row.get("yoe")
    yoe_txt = f"{float(yoe):.1f} yrs" if yoe not in (None, "") else "\u2014"
    dna = dna_obj.get("primary_dna") or row.get("dna") or ""
    if isinstance(dna, dict):
        dna = dna.get("primary_dna", "")
    band = row.get("band") or scorecard.get("band") or ""

    # ── Scoring ──
    _ARCH_DESCS = {
        "A1":"Baseline","A2":"Weak College + FAANG","A3":"Weak College + Mid-tier",
        "A4":"Elite College + Weak Co.","A5":"Fresh Graduate","A6":"Senior 10+ YoE",
        "A7":"PhD / Research","A8":"Domain Switcher","A9":"Founder","A10":"Consultant",
    }
    _archetype  = rubric.get("archetype") or "A1"
    _arch_weights = rubric.get("archetype_weights") or {"exp":40.0,"skills":45.0,"edu":15.0}
    _arch_section = rubric.get("archetype_section_scores") or {}
    _arch_total   = rubric.get("archetype_total_score")
    _red_flags_data = rubric.get("red_flags") or {"hard":[],"soft":[]}
    _arch_desc  = _ARCH_DESCS.get(_archetype, "Baseline")
    exp_score   = float(rubric.get("experience_score") or 0)
    sk_score    = float(rubric.get("skills_score") or 0)
    edu_score   = float(rubric.get("education_score") or 0)
    total_rubric = float(rubric.get("total_score") or 0)
    reject_flags = rubric.get("reject_flags") or scorecard.get("reject_flags") or []
    stage_sc    = rubric.get("stage_scores") or {}
    rubric_bd   = rubric.get("breakdown") or {}

    def _sse(stg):
        for e in reversed(scores_data.get("stages", [])):
            if e.get("stage") == stg:
                return e
        return None

    def _ss100(stg):
        e = _sse(stg)
        if not e:
            return None
        ss2 = e.get("stage_scores") or {}
        return ss2.get(f"{stg}_score_100") or e.get("total_score")

    total_score        = scores_data.get("current_total") or stage_sc.get("resume_score_100") or scorecard.get("total_score") or row.get("resume_score") or 0
    _display_score     = float(_arch_total) if _arch_total is not None else float(total_score or 0)
    score_label, _     = _score_bucket_label(_display_score)
    resume_score_100   = _ss100("resume") or stage_sc.get("resume_score_100") or total_score
    recruiter_score_100 = _ss100("recruiter") or stage_sc.get("recruiter_score_100")
    panel_score_100    = _ss100("panel") or stage_sc.get("panel_score_100")
    top_skills         = [sk for sk in ((analysis.get("skill_analysis") or {}).get("top_skills") or []) if isinstance(sk, dict)]

    # ── Tab 2: Summary data ──
    llm_analysis = analysis.get("llm_analysis") or {}
    llm_sem  = llm_analysis.get("semantic_analysis") or {}
    llm_dna  = llm_analysis.get("dna_judgment") or {}
    llm_qual = llm_analysis.get("qualitative_analysis") or {}
    _t2_recruiter_summary    = llm_sem.get("recruiter_summary") or analysis.get("recruiter_summary") or ""
    _t2_top_role_family      = llm_sem.get("top_role_family") or semantic.get("top_role_family") or ""
    _t2_role_rationale       = llm_sem.get("role_family_rationale") or semantic.get("role_family_rationale") or ""
    _t2_consistency          = llm_sem.get("consistency_readout") or ""
    _t2_inferred             = llm_sem.get("inferred_strength_areas") or semantic.get("inferred_skills") or []
    _t2_primary_dna          = llm_dna.get("primary_dna") or dna_obj.get("primary_dna") or ""
    _t2_dna_confidence       = llm_dna.get("confidence") or ""
    _t2_dna_reason           = llm_dna.get("reason") or dna_obj.get("dna_reason") or ""
    _t2_dna_evidence         = llm_dna.get("evidence_used") or []
    _t2_strengths            = (llm_qual.get("strengths") or qualitative.get("strengths") or [])[:3]
    _t2_gaps                 = (llm_qual.get("gaps") or qualitative.get("gaps") or [])[:3]
    _t2_risks                = (llm_qual.get("risk_flags") or qualitative.get("risk_flags") or [])[:3]
    _t2_panel_suggestions    = (llm_qual.get("panel_suggestion") or qualitative.get("panel_suggestion") or [])[:3]
    _t2_recommendation       = llm_qual.get("recommendation") or ""

    # ── Tab 3: Outside Projects data ──
    rubric_bd2  = rubric.get("breakdown") or {}
    _cc_bd      = (rubric_bd2.get("skills") or {}).get("coding_community") or {}
    _cc_score   = _cc_bd.get("score", 0)
    _cc_max     = _cc_bd.get("max", 4)
    _cc_reason  = _cc_bd.get("reason", "")
    _cc_links   = _cc_bd.get("links") or []
    _cc_plats   = _cc_bd.get("competitive_platforms") or []
    _cc_hack    = _cc_bd.get("hackathon_prize_signal", False)
    _cc_oss     = _cc_bd.get("oss_signal_count", 0)
    _coding_skills = [sk for sk in top_skills if sk.get("coding_signal") or sk.get("coding_strength_signal")]
    _oss_skills    = [sk for sk in top_skills if sk.get("open_source_signal")]
    _gh_user = None
    for _lnk in _cc_links:
        if _lnk and "github.com/" in (_lnk or "").lower():
            _parts = (_lnk or "").rstrip("/").split("github.com/")
            if len(_parts) > 1 and _parts[1]:
                _gh_user = _parts[1].split("/")[0]
                break

    # ── BERT signals ──
    sem_a    = analysis.get("semantic_analysis") or {}
    brf      = (sem_a.get("bert_role_family_prior") or {}).get("label") or sem_a.get("top_role_family") or ""
    bdna     = (dna_obj.get("bert_dna_prior") or {}).get("label") or dna
    bcp_raw  = (sem_a.get("career_progression_prior") or {}).get("label") or ""
    if not bcp_raw:
        traj = exp.get("career_trajectory_score")
        bcp_raw = ("FAST_TRACK" if traj and traj >= 4 else "GROWING" if traj and traj >= 3 else "LATERAL" if traj and traj >= 2 else "")
    bsh = (sem_a.get("stakeholder_prior") or {}).get("label") or ("CLIENT_FACING" if exp.get("client_facing") else "INTERNAL")

    # ── Color helpers ──
    _BC = {"STRONG":"#16A34A","GOOD":"#6366F1","AVERAGE":"#D97706","WEAK":"#DC2626"}
    def _bc(b): return _BC.get(b or "", "#9CA3AF")
    _DC = {"ARCHITECT_LEVEL":"#353395","ADVANCED":"#6366F1","HANDS_ON":"#16A34A","FOUNDATIONAL":"#D97706","AWARENESS":"#9CA3AF"}

    def _bpill(lbl, val, color="#6366F1"):
        if not val:
            return ""
        return (f"<span style='background:{color}18;color:{color};border:1px solid {color}33;"
                f"border-radius:8px;padding:3px 9px;font-size:11px;font-weight:600;margin:2px'>"
                f"<span style='color:var(--text2);font-weight:400'>{lbl}: </span>"
                f"{val.replace('_',' ')}</span>")

    bert_html = "".join(filter(None, [
        _bpill("Role Family", brf, "#353395"),
        _bpill("DNA", bdna, "#7C3AED"),
        _bpill("Progression", bcp_raw, "#16A34A" if bcp_raw=="FAST_TRACK" else "#D97706" if bcp_raw=="DECLINING" else "#6366F1"),
        _bpill("Stakeholder", bsh, "#0891B2"),
    ]))

    # ── Param label + rubric renderer ──
    _PL = {
        "overall_experience":"Overall / Relevant Experience","career_breaks":"Career Breaks",
        "career_progression":"Career Progression","stability":"Stability",
        "company_tier":"Companies Worked With","awards_recognition":"Awards & Recognitions",
        "mentorship_signal":"Mentorship / Code Reviews / Interviews",
        "international_exposure":"International Exposure","stakeholder_management":"Stakeholder Management",
        "project_1":"Project 1 \u2014 Latest Project","project_2":"Project 2 \u2014 2nd Latest Project",
        "skill_list_years":"Skill List \u2014 Years of Experience","skill_depth":"Skill Depth",
        "skill_recency":"Skill Recency","skills_learning_acumen":"Skills Learning Acumen",
        "certifications":"Certifications","coding_community":"Coding Platforms / Community",
        "project_explanation":"Project Explanation Skills",
        "communication_skills":"Communication & Presentation Skills",
        "domain_skills":"Domain Skills","problem_solving":"Problem Solving Skills",
        "coding_skills":"Coding Skills","conceptual_skills":"Conceptual Skills",
        "mandatory_skills":"Mandatory Skills (JD Match)","good_to_have_skills":"Good to Have Skills (JD Match)",
        "institute_tier":"Institute \u2014 Tier, GPA, Stream","degree_level":"Highest Education & Stream",
        "education_job_relevance":"Education to Job Relevance","education_gap":"Education Gaps",
        "bonus.exec_education":"Executive / Distance Education",
        "bonus.patents_publications":"Patents / Publications",
        "bonus.linkedin_activity":"LinkedIn / Social Media Activeness",
        "bonus.extra_curriculars":"Extra Curricular Activities",
    }
    def _pl(k): return _PL.get(k) or k.replace("_"," ").title()

    def _schip(stg):
        if not stg: return ""
        cfg = {"resume":("#DCFCE7","#16A34A"),"recruiter":("#FEF3C7","#D97706"),"panel":("#F0F0FB","#6366F1")}
        bg, fg = cfg.get(stg, ("#F3F4F6","#6B7280"))
        return (f"<span style='font-size:10px;font-weight:700;text-transform:uppercase;background:{bg};"
                f"color:{fg};border-radius:999px;padding:1px 7px;margin-left:5px'>{stg}</span>")

    def _pc(k, v):
        if not isinstance(v, dict): return ""
        pt = v.get("type")
        if pt == "flag":
            matched = v.get("matched") or []; missing = v.get("missing") or []
            if not matched and not missing: return ""
            mc = "".join(f"<span style='display:inline-block;background:#DCFCE7;color:#16A34A;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:600;margin:2px'>{escape(m)}</span>" for m in matched)
            xc = "".join(f"<span style='display:inline-block;background:#FEE2E2;color:#DC2626;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:600;margin:2px'>{escape(m)}</span>" for m in missing)
            return (f"<div style='background:var(--white);border:1px solid var(--border);border-radius:8px;"
                    f"padding:10px 12px;margin-bottom:6px'>"
                    f"<div style='display:flex;justify-content:space-between;margin-bottom:6px'>"
                    f"<b style='font-size:13px'>{_pl(k)}</b>"
                    f"<span style='font-size:11px;color:var(--text2)'>{escape(str(v.get('match_rate','')))} </span></div>"
                    f"<div>{mc}{xc}</div></div>")
        if pt == "panel_text":
            return (f"<div style='background:var(--white);border:1px solid var(--border);border-radius:8px;"
                    f"padding:10px 12px;margin-bottom:6px'>"
                    f"<b style='font-size:13px'>{_pl(k)}</b>{_schip('panel')}"
                    f"<div style='color:var(--text2);font-size:12px;margin-top:5px'>"
                    f"{escape(v.get('value') or v.get('note') or 'Panel fill required')}</div></div>")
        sc2 = v.get("score"); mx = v.get("max") or 0
        if sc2 is None: return ""
        pct2 = min(100, int(float(sc2)/max(1,float(mx))*100)) if mx else 0
        bc2 = "#16A34A" if pct2>=75 else "#D97706" if pct2>=40 else "#DC2626"
        stg2 = v.get("stage") or ""; reason = escape(v.get("reason") or "")
        pend = (f" <span style='font-size:10px;color:var(--text2)'>(pending {stg2})</span>"
                if sc2==0 and stg2 and stg2!="resume" else "")
        return (f"<details style='background:var(--white);border:1px solid var(--border);"
                f"border-radius:8px;margin-bottom:6px;overflow:hidden'>"
                f"<summary style='display:flex;justify-content:space-between;padding:10px 12px;"
                f"cursor:pointer;gap:10px;list-style:none'>"
                f"<div style='flex:1'><b style='font-size:13px'>{_pl(k)}</b>{_schip(stg2)}{pend}"
                f"<div style='color:var(--text2);font-size:12px;margin-top:3px'>{reason}</div></div>"
                f"<b style='color:var(--primary);white-space:nowrap'>{float(sc2):.1f}"
                f"&nbsp;<span style='font-size:11px;font-weight:400;color:var(--text2)'>/ {float(mx):.0f}</span></b>"
                f"</summary><div style='padding:0 12px 10px'>"
                f"<div style='background:#E5E7EB;border-radius:3px;height:4px;overflow:hidden'>"
                f"<div style='height:100%;width:{pct2}%;background:{bc2}'></div></div></div></details>")

    def _rs(lbl, sec_bd, sec_score, max_pts, color="#353395"):
        if not sec_bd: return ""
        flat = {}
        for k3, v3 in sec_bd.items():
            if k3 == "bonus" and isinstance(v3, dict) and "score" not in v3:
                for bk, bv in v3.items(): flat[f"bonus.{bk}"] = bv
            else:
                flat[k3] = v3
        cards = "".join(_pc(k3, v3) for k3, v3 in flat.items())
        if not cards: return ""
        pct3 = min(100, int(float(sec_score or 0)/max(1,max_pts)*100))
        return (f"<div style='margin-bottom:20px'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
                f"<h3 style='margin:0;font-size:15px;font-weight:700'>{lbl}</h3>"
                f"<span style='font-size:20px;font-weight:800;color:{color}'>{float(sec_score or 0):.0f} "
                f"<span style='font-size:12px;font-weight:400;color:var(--text2)'>/ {max_pts}</span></span></div>"
                f"<div style='background:#E5E7EB;height:4px;border-radius:3px;overflow:hidden;margin-bottom:10px'>"
                f"<div style='height:100%;width:{pct3}%;background:{color}'></div></div>"
                f"<div style='display:grid;grid-template-columns:repeat(2,1fr);gap:6px'>{cards}</div></div>")

    # ── Skill chips ──
    def _sk_chip(sk):
        dl = sk.get("depth_label") or sk.get("evidence_level") or ""
        dc = _DC.get(dl, "#9CA3AF")
        rec = sk.get("recency") or ""
        rdot = ("" if rec not in ("RECENT","MODERATE","OLD") else
                f"<span style='display:inline-block;width:5px;height:5px;background:"
                f"{'#16A34A' if rec=='RECENT' else '#D97706' if rec=='MODERATE' else '#DC2626'};"
                f"border-radius:50%;margin-left:3px;vertical-align:middle'></span>")
        yrs = sk.get("years_of_usage")
        ys = f" \u00b7 {float(yrs):.1f}y" if yrs else ""
        return (f"<span style='display:inline-flex;align-items:center;background:{dc}18;border:1px solid {dc}44;"
                f"border-radius:8px;padding:3px 9px;margin:2px;font-size:12px;color:{dc};font-weight:600'>"
                f"{escape(sk.get('skill',''))}"
                f"<span style='font-size:10px;font-weight:400;color:var(--text2);margin-left:3px'>{dl}{ys}</span>"
                f"{rdot}</span>")

    skills_chips = "".join(_sk_chip(sk) for sk in top_skills[:24])

    # ── Detailed evidence cards ──
    def _sk_ev(s):
        ev  = escape(s.get("evidence_level") or "")
        jl  = escape(s.get("judged_strength_label") or s.get("depth_label") or ev)
        js  = s.get("judged_score_0_to_5")
        yrs = s.get("years_of_usage"); raw_y = s.get("raw_years_of_usage")
        rec = escape(s.get("recency") or ""); ctx = s.get("matched_context_count")
        reason = escape(s.get("judged_reason") or "No rationale generated.")
        probe  = escape(s.get("interview_probe") or "")
        ev_used = s.get("judged_evidence_used") or []
        dc2 = _DC.get(s.get("depth_label") or s.get("evidence_level") or "", "#9CA3AF")
        roles_html = "".join(
            f"<div style='font-size:11px;color:var(--text2)'>"
            f"{escape(r.get('title',''))} @ {escape(r.get('company',''))} "
            f"({escape(r.get('start_date',''))} \u2013 {escape(r.get('end_date',''))})</div>"
            for r in (s.get("evidence_roles") or [])[:3])
        return (f"<div style='background:var(--white);border:1px solid var(--border);border-radius:10px;"
                f"padding:13px;margin-bottom:10px'>"
                f"<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px'>"
                f"<b style='font-size:14px'>{escape(s.get('skill',''))}</b>"
                f"<span style='background:{dc2}18;color:{dc2};border:1px solid {dc2}44;"
                f"border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700'>{jl}</span></div>"
                f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));"
                f"gap:3px;font-size:12px;color:var(--text2);margin-bottom:6px'>"
                f"<span>Evidence: {ev}</span>"
                f"{'<span>AI: ' + str(js) + '/5</span>' if js is not None else ''}"
                f"<span>Tenure: {yrs or chr(8212)} yrs</span>"
                f"<span>Recency: {rec}</span><span>Contexts: {ctx or chr(8212)}</span></div>"
                f"<div style='font-size:12px;color:var(--text2);margin-bottom:4px'>{reason}</div>"
                f"{'<div style=\"font-size:12px;color:var(--text2);margin-bottom:4px\"><b>Evidence:</b> ' + '; '.join(escape(e2) for e2 in ev_used[:3]) + '</div>' if ev_used else ''}"
                f"{'<div style=\"font-size:12px;color:#6366F1;font-style:italic\"><b>Probe:</b> ' + probe + '</div>' if probe else ''}"
                f"{'<div style=\"margin-top:5px\">' + roles_html + '</div>' if roles_html else ''}"
                f"</div>")

    skill_ev_html = "".join(_sk_ev(s) for s in top_skills[:10])

    # ── Semantic taxonomy ──
    all_sk_map = {sk.get("skill"): sk for sk in ((analysis.get("skill_analysis") or {}).get("all_skills") or []) if isinstance(sk, dict) and sk.get("skill")}
    sem_blocks = ""
    for cn, cskills in (sem_a.get("cluster_map") or {}).items():
        strong = [s for s in (cskills or []) if (all_sk_map.get(s) or {}).get("evidence_level") in
                  ("APPLIED","DEEP","EXPERT","HANDS_ON","ADVANCED","ARCHITECT_LEVEL")]
        weak   = [s for s in (cskills or []) if (all_sk_map.get(s) or {}).get("evidence_level") in
                  ("MENTION","WEAK","FOUNDATIONAL","AWARENESS")]
        sp = ("".join(f"<span style='display:inline-flex;background:var(--primary-light);border:1px solid var(--primary-border);border-radius:999px;padding:2px 9px;margin:2px;font-size:11px;font-weight:500;color:var(--primary)'>{escape(s)}</span>" for s in strong)
               or "<span style='color:var(--text2);font-size:12px'>None detected.</span>")
        wp = ("".join(f"<span style='display:inline-flex;background:#FFF7ED;border:1px solid #FDE68A;border-radius:999px;padding:2px 9px;margin:2px;font-size:11px;font-weight:500;color:#D97706'>{escape(s)}</span>" for s in weak)
               or "<span style='color:var(--text2);font-size:12px'>None detected.</span>")
        sem_blocks += (f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px'>"
                       f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
                       f"<b style='font-size:13px'>{escape(cn)}</b>"
                       f"<span style='background:var(--primary-light);color:var(--primary);border-radius:999px;padding:2px 8px;font-size:11px;font-weight:600'>{len(cskills or [])} skills</span></div>"
                       f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px'>"
                       f"<div style='background:#fff;border:1px solid var(--border);border-radius:8px;padding:10px'>"
                       f"<div style='font-size:11px;font-weight:600;color:var(--text2);margin-bottom:6px'>Evidence-backed</div>{sp}</div>"
                       f"<div style='background:#fff;border:1px solid var(--border);border-radius:8px;padding:10px'>"
                       f"<div style='font-size:11px;font-weight:600;color:var(--text2);margin-bottom:6px'>Needs validation</div>{wp}</div>"
                       f"</div></div>")
    cst = sem_a.get("skill_consistency_score") or 0
    role_rat = escape(sem_a.get("role_family_rationale") or "")

    # ── Experience rows ──
    def _bl(v): return "Yes" if v is True else "No" if v is False else str(v) if v is not None else "N/A"
    def _row(lbl, val):
        return (f"<div style='background:var(--bg);border-radius:6px;padding:6px 10px;margin-bottom:3px;"
                f"display:flex;justify-content:space-between;font-size:13px'>"
                f"<span style='color:var(--text2)'>{lbl}</span>"
                f"<span style='font-weight:600'>{val}</span></div>")

    exp_rows = (
        _row("Total Experience", f"{exp.get('total_experience_years', chr(8212))} years") +
        _row("Progression", _bl(exp.get("progression"))) +
        _row("Same Company Growth", _bl(exp.get("same_company_growth"))) +
        _row("Mobility", escape(str(exp.get("mobility_signal") or chr(8212)))) +
        _row("Avg Tenure", f"{exp.get('average_tenure_months', chr(8212))} months") +
        _row("Client Facing", _bl(exp.get("client_facing"))) +
        _row("International", _bl(exp.get("international_exposure"))) +
        _row("Decision Maker", _bl(exp.get("decision_maker"))) +
        _row("Leadership Signal", escape(str(exp.get("leadership_signal_score") or chr(8212)))) +
        _row("Problem Solving", escape(str(exp.get("problem_solving_signal_score") or chr(8212))))
    )
    for pt in (exp.get("project_types") or [])[:6]:
        exp_rows += (f"<div style='font-size:12px;color:var(--text2);padding:2px 0'>"
                     f"{escape(pt.get('title',''))} ({escape(str(pt.get('start_date','')))} \u2013 {escape(str(pt.get('end_date','')))})"
                     f": {escape(str(pt.get('project_type','')))}</div>")
    for cp in (exp.get("company_profiles") or [])[:4]:
        exp_rows += (f"<div style='font-size:12px;color:var(--text2);padding:2px 0'>"
                     f"{escape(cp.get('company',''))} \u2014 {escape(str(cp.get('operating_model','')))} "
                     f"\u00b7 {escape(str(cp.get('size','')))} \u00b7 {escape(str(cp.get('domain','')))}</div>")

    # ── Education rows ──
    edu_rows = (
        _row("Highest Tier", escape(str(edu.get("highest_institute_tier") or chr(8212)))) +
        _row("Strongest Course", escape(str(edu.get("strongest_course_value_signal") or chr(8212)))) +
        _row("Education Gap", f"{edu.get('education_gap_months', chr(8212))} months") +
        _row("Course Families", escape(", ".join(edu.get("course_families") or []) or chr(8212))) +
        _row("Top Institutes", escape(", ".join(edu.get("top_institutes") or []) or chr(8212)))
    )
    for e2 in (edu.get("education_entries") or edu.get("entries") or edu.get("items") or [])[:4]:
        edu_rows += (f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:12px'>"
                     f"<span style='font-weight:600'>{escape(str(e2.get('institution_canonical') or e2.get('institution') or ''))}</span>"
                     f" | {escape(str(e2.get('course_canonical') or e2.get('degree') or ''))}"
                     f" | {escape(str(e2.get('tier') or ''))}"
                     f"{'  | GPA: ' + escape(str(e2.get('gpa_raw',''))) + ' (' + escape(str(e2.get('gpa_band',''))) + ')' if e2.get('gpa_raw') else ''}"
                     f"</div>")

    # ── Strengths / Gaps ──
    strs     = qualitative.get("strengths") or ((analysis.get("llm_analysis") or {}).get("qualitative_analysis") or {}).get("strengths") or []
    gaps_list = qualitative.get("gaps") or []
    risks    = qualitative.get("risk_flags") or []

    # ── JD matches ──
    jdm_html = ""
    try:
        for jd in list_job_postings(include_closed=True)[:20]:
            jid = jd.get("jd_id") or ""
            if not jid: continue
            for m in list_jd_matches(jid):
                if (m.get("candidate_id") or "") == candidate_id:
                    rec2 = m.get("recommendation","")
                    rc2 = "#16A34A" if rec2=="SHORTLIST" else "#D97706" if rec2=="SCREEN" else "#DC2626"
                    cs2 = m.get("combined_score") or m.get("overall_score") or m.get("jd_match_score")
                    jdm_html += (f"<div style='display:flex;gap:10px;align-items:center;padding:7px 0;"
                                 f"border-bottom:1px solid var(--border);flex-wrap:wrap'>"
                                 f"<div style='flex:1;font-weight:600;font-size:13px'>{escape(jd.get('title') or jid)}</div>"
                                 f"{'<b style=\"color:#353395\">' + str(int(cs2)) + ' / 100</b>' if cs2 is not None else ''}"
                                 f"<span style='background:{rc2}18;color:{rc2};border:1px solid {rc2}44;"
                                 f"border-radius:99px;padding:2px 8px;font-size:11px;font-weight:700'>{escape(rec2)}</span>"
                                 f"<a href='/jobs/{escape(jid)}' class='btn-sec' style='padding:3px 9px;font-size:11px'>View</a></div>")
    except Exception:
        pass

    # ── Score metric tiles ──
    edu_bd2 = rubric_bd.get("education") or {}
    edu_core2 = 0.0; edu_bonus2 = 0.0
    for ke, ve in edu_bd2.items():
        if not isinstance(ve, dict): continue
        if ke == "bonus":
            for bv2 in ve.values():
                if isinstance(bv2, dict) and bv2.get("score") is not None:
                    edu_bonus2 += float(bv2.get("score", 0))
        elif ve.get("score") is not None:
            edu_core2 += float(ve.get("score", 0))

    def _mtile(kicker, val, sub="", color="#353395"):
        return (f"<div style='background:var(--white);border:1px solid var(--border);border-radius:10px;padding:13px'>"
                f"<div style='font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;"
                f"color:var(--text2);margin-bottom:5px'>{kicker}</div>"
                f"<div style='font-size:22px;font-weight:800;color:{color}'>{val}</div>"
                f"{'<div style=\"font-size:11px;color:var(--text2);margin-top:3px\">' + sub + '</div>' if sub else ''}"
                f"</div>")

    score_tiles_html = (
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px'>"
        + _mtile("Resume Score", int(_display_score), f"Archetype {escape(_archetype)} \u00b7 {escape(_arch_desc)}", _bc(band) if band else "#353395")
        + _mtile("Experience", f"{exp_score:.1f}", "/ 40 pts raw", "#353395")
        + _mtile("Skills", f"{sk_score:.1f}", "/ 45 pts raw", "#6366F1")
        + _mtile("Education", f"{edu_score:.1f}", f"Core {edu_core2:.1f} + Bonus {edu_bonus2:.1f} / 15", "#0891B2")
        + _mtile("Role Family", escape(role_family or chr(8212)), "", "#4B5563")
        + _mtile("Band", escape(band or chr(8212)), "AI judgment band", _bc(band) if band else "#9CA3AF")
        + _mtile("DNA Fit", escape(str(dna).replace("_"," ") if dna else chr(8212)), escape(str(dna_obj.get("dna_reason") or "")[:50]), "#7C3AED")
        + _mtile("Reject Flags", str(len(reject_flags)),
                 " \u00b7 ".join(escape(str(f)) for f in reject_flags[:2]) if reject_flags else "None",
                 "#DC2626" if reject_flags else "#16A34A")
        + "</div>"
    )

    # ── Stage scores ──
    ss0 = stage_sc
    r100  = resume_score_100 or 0
    rc100 = recruiter_score_100
    p100  = panel_score_100
    rmax  = ss0.get("resume_max") or 85
    rcadd = ss0.get("recruiter_can_add") or 11
    pnadd = ss0.get("panel_can_add") or 13
    fpot  = ss0.get("full_score_potential") or 100
    rpct  = int(round(rmax/max(1,fpot)*100))
    rcpct = int(round(rcadd/max(1,fpot)*100))
    ppct  = max(0, 100-rpct-rcpct)

    def _stile2(lbl, sc, pend, color):
        if sc is not None:
            sh = (f"<div style='font-size:26px;font-weight:800;color:{color}'>{int(float(sc))}"
                  f"<span style='font-size:12px;font-weight:400;color:var(--text2)'> / 100</span></div>")
        elif pend is not None:
            sh = f"<div style='font-size:14px;font-weight:700;color:var(--text2)'>+{pend} pts pending</div>"
        else:
            sh = "<div style='color:var(--text2);font-size:13px'>Not evaluated</div>"
        return (f"<div style='flex:1;min-width:120px;background:var(--white);border:1px solid var(--border);"
                f"border-radius:10px;padding:12px'>"
                f"<div style='font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;"
                f"color:var(--text2);margin-bottom:5px'>{lbl}</div>{sh}</div>")

    stage_bar = (
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px'>"
        + _stile2("Resume Stage", r100 if r100 else None, None, "#16A34A")
        + _stile2("Recruiter Stage", rc100, rcadd if rc100 is None else None, "#D97706")
        + _stile2("Panel Stage", p100, pnadd if p100 is None else None, "#6366F1")
        + f"</div>"
          f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:3px;"
          f"display:flex;overflow:hidden;margin-bottom:6px'>"
          f"<div style='width:{rpct}%;background:#16A34A;height:18px;border-radius:4px 0 0 4px;"
          f"display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff;min-width:28px'>{rpct}%</div>"
          f"<div style='width:{rcpct}%;background:#D97706;height:18px;"
          f"display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff'>"
          f"{''+str(rcpct)+'%' if rcpct>4 else ''}</div>"
          f"<div style='width:{ppct}%;background:#6366F1;height:18px;border-radius:0 4px 4px 0;"
          f"display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff'>"
          f"{''+str(ppct)+'%' if ppct>4 else ''}</div></div>"
          f"<div style='display:flex;gap:12px;font-size:11px;color:var(--text2)'>"
          f"<span style='color:#16A34A'>&#9632; Resume ({r100 or chr(8212)}/100)</span>"
          f"<span style='color:#D97706'>&#9632; Recruiter "
          f"{'(' + str(rc100) + '/100)' if rc100 else '(+' + str(rcadd) + ' pending)'}</span>"
          f"<span style='color:#6366F1'>&#9632; Panel "
          f"{'(' + str(p100) + '/100)' if p100 else '(+' + str(pnadd) + ' pending)'}</span>"
          f"</div>"
    )

    # ── Reject flags banner ──
    rfbanner = ""
    if reject_flags:
        rfbanner = (f"<div style='padding:10px 14px;background:#FEF2F2;border:1px solid #FECACA;"
                    f"border-radius:8px;font-size:12px;color:#DC2626;margin-bottom:12px'>"
                    f"<b>Reject flags:</b> "
                    + " &bull; ".join(escape(str(f)) for f in reject_flags)
                    + "</div>")

    # ── Rubric scorecard ──
    rubric_sec = ""
    if rubric_bd:
        bc3 = _bc(band)
        total_d = (f"{int(total_rubric)} <span style='font-size:11px;font-weight:700;background:{bc3}22;"
                   f"color:{bc3};border:1px solid {bc3}44;border-radius:99px;padding:2px 8px'>{escape(band)}</span>"
                   if total_rubric and band else str(int(total_rubric)) if total_rubric else chr(8212))
        rsecs = (
            _rs("Experience (40 pts)", rubric_bd.get("experience") or {}, exp_score, 40, "#353395") +
            _rs("Skills (45 pts)", rubric_bd.get("skills") or {}, sk_score, 45, "#6366F1") +
            _rs("Education (15 pts)", rubric_bd.get("education") or {}, edu_score, 15, "#0891B2")
        )
        if rsecs:
            rubric_sec = (f"<div class='card'><details open>"
                          f"<summary style='list-style:none;cursor:pointer;display:flex;justify-content:space-between;"
                          f"align-items:center;padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:12px'>"
                          f"<h2 style='margin:0'>Rubric Scorecard</h2>"
                          f"<div style='display:flex;align-items:center;gap:10px'>"
                          f"<span style='font-size:18px;font-weight:800;color:{bc3}'>{total_d}</span>"
                          f"<span style='font-size:12px;color:var(--text2)'>Click to collapse &#9660;</span>"
                          f"</div></summary>{rsecs}</details></div>")

    # ── Red flags section ──
    rf_sec = ""
    _hfl = (_red_flags_data.get("hard") or [])
    _sfl = (_red_flags_data.get("soft") or [])
    if _hfl or _sfl:
        rf_html = "".join(f"<div style='background:#FEE2E2;border:1px solid #FECACA;border-radius:8px;padding:8px 10px;margin-bottom:5px;font-size:12px;color:#B91C1C;font-weight:700'>&#x1F6AB; {escape(str(fl))}</div>" for fl in _hfl)
        rf_html += "".join(f"<div style='background:#FFF4D6;border:1px solid #FDE68A;border-radius:8px;padding:8px 10px;margin-bottom:5px;font-size:12px;color:#B45309'>&#x26A0;&#xFE0F; {escape(str(fl))}</div>" for fl in _sfl)
        rf_sec = (f"<div class='card'><h2>Red Flags &amp; Edge Cases "
                  f"<span style='font-size:12px;font-weight:400;color:var(--text2)'>Archetype {escape(_archetype)} \u00b7 {escape(_arch_desc)}</span></h2>"
                  f"{rf_html}</div>")

    # ── Overview fields ──
    ov_fields = ""
    for _lbl2, _val2 in [("Name",name),("Email",email),("Phone",phone),("Location",location)]:
        if _val2 and str(_val2) not in ("N/A","NA","n/a","","Unknown"):
            ov_fields += (f"<div><div style='font-size:11px;font-weight:600;text-transform:uppercase;"
                          f"letter-spacing:.07em;color:var(--text2)'>{_lbl2}</div>"
                          f"<div style='font-size:13px;margin-top:2px'>{escape(str(_val2))}</div></div>")
    if summary:
        ov_fields += (f"<div style='grid-column:1/-1'><div style='font-size:11px;font-weight:600;"
                      f"text-transform:uppercase;letter-spacing:.07em;color:var(--text2)'>Profile Summary</div>"
                      f"<div style='font-size:13px;color:var(--text2);margin-top:2px;line-height:1.55'>"
                      f"{escape(summary[:400])}</div></div>")

    cid_enc = candidate_id.replace("'", "%27")
    sidebar = _sidebar("candidates", user)

    # ── Tab 2: Summary HTML ──
    _t2_score_tiles = (
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-top:8px'>"
        + _mtile("Total Score", int(_display_score), f"Archetype {escape(_archetype)}", _bc(band) if band else "#353395")
        + _mtile("Band", escape(band or "\u2014"), "Strength band", _bc(band) if band else "#9CA3AF")
        + _mtile("Experience", f"{exp_score:.1f}", "/ 40 pts", "#353395")
        + _mtile("Skills", f"{sk_score:.1f}", "/ 45 pts", "#6366F1")
        + _mtile("Education", f"{edu_score:.1f}", "/ 15 pts", "#0891B2")
        + "</div>"
    )
    _dna_clean = (_t2_primary_dna or dna or "").replace("_", " ")
    _rf_clean  = (_t2_top_role_family or role_family or "").replace("_", " ").title()
    _dna_ev_html = "".join(
        f"<div style='font-size:12px;color:var(--text2);padding:2px 0;border-bottom:1px solid var(--border)'>{escape(str(e3))}</div>"
        for e3 in (_t2_dna_evidence or [])[:3]
    )
    _conf_badge = (
        f"<span style='background:#7C3AED18;color:#7C3AED;border:1px solid #7C3AED33;border-radius:999px;"
        f"padding:2px 8px;font-size:11px;font-weight:700;margin-left:8px'>{escape(_t2_dna_confidence)}</span>"
        if _t2_dna_confidence else ""
    )
    _fit_rationale_html = (
        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px'>"
        f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px'>"
        f"<div class='kicker'>DNA Fit</div>"
        f"<div style='font-size:18px;font-weight:800;color:#7C3AED;margin:4px 0'>{escape(_dna_clean) or chr(8212)}{_conf_badge}</div>"
        + (f"<div style='font-size:12px;color:var(--text2);margin-top:5px;line-height:1.55'>{escape(str(_t2_dna_reason)[:200])}</div>" if _t2_dna_reason else "")
        + (_dna_ev_html if _dna_ev_html else "")
        + f"</div>"
        f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px'>"
        f"<div class='kicker'>Role Family</div>"
        f"<div style='font-size:18px;font-weight:800;color:#353395;margin:4px 0'>{escape(_rf_clean) or chr(8212)}</div>"
        + (f"<div style='font-size:12px;color:var(--text2);margin-top:5px;line-height:1.55'>{escape(str(_t2_role_rationale)[:200])}</div>" if _t2_role_rationale else "")
        + (f"<div style='font-size:12px;color:var(--text2);margin-top:4px;font-style:italic'>{escape(str(_t2_consistency))}</div>" if _t2_consistency else "")
        + f"</div></div>"
    )
    _inferred_html = ""
    if _t2_inferred:
        _inferred_html = (
            "<div style='margin-top:10px'>"
            "<div style='font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--text2);margin-bottom:5px'>Inferred Strengths</div>"
            + "".join(f"<span class='pill'>{escape(str(i4))}</span>" for i4 in _t2_inferred[:8])
            + "</div>"
        )
    _str_items  = "".join(f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:13px;color:#16A34A'>&#10003; {escape(str(s4))}</div>" for s4 in _t2_strengths)
    _gap_items  = "".join(f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:13px;color:#D97706'>&#9888; {escape(str(g4))}</div>" for g4 in _t2_gaps)
    _risk_items = "".join(f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;color:#DC2626'>&#9888; {escape(str(r4))}</div>" for r4 in _t2_risks)
    _panel_items = "".join(f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;color:#7C3AED'>&#9654; {escape(str(p4))}</div>" for p4 in _t2_panel_suggestions)
    _t2_rec_card = ""
    if _t2_recommendation:
        _t2_rec_card = (
            f"<div class='card'>"
            f"<div class='kicker'>Recommendation</div>"
            f"<div style='font-size:14px;font-style:italic;color:var(--text);line-height:1.65;margin-top:6px'>{escape(str(_t2_recommendation))}</div>"
            f"</div>"
        )
    _tab2_html = (
        f"<div id='tab-summary' class='tab-content'>"
        f"<div class='card'>"
        f"<div class='kicker'>Score Overview</div>"
        + _t2_score_tiles
        + f"</div>"
        + f"<div class='card'><h2>Fit Rationale</h2>"
        + _fit_rationale_html
        + f"</div>"
        + f"<div class='card'><h2>Recruiter Summary</h2>"
          f"<div style='font-size:13px;color:var(--text);line-height:1.65'>"
        + (escape(_t2_recruiter_summary) if _t2_recruiter_summary else "<span style='color:var(--text2)'>LLM analysis not available. Re-analyze to generate.</span>")
        + f"</div>"
        + _inferred_html
        + f"</div>"
        + f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px'>"
          f"<div class='card' style='margin:0'><h2>Strengths</h2>"
        + (_str_items or "<div style='color:var(--text2);font-size:13px'>None captured.</div>")
        + f"</div>"
          f"<div class='card' style='margin:0'><h2>Gaps</h2>"
        + (_gap_items or "<div style='color:var(--text2);font-size:13px'>None captured.</div>")
        + f"</div>"
          f"<div class='card' style='margin:0'><h2>Risks &amp; Panel Suggestions</h2>"
        + (_risk_items or "")
        + (_panel_items or "")
        + ("<div style='color:var(--text2);font-size:13px'>None captured.</div>" if not _risk_items and not _panel_items else "")
        + f"</div></div>"
        + _t2_rec_card
        + f"</div>"
    )

    # ── Tab 3: Outside Projects HTML ──
    _cc_pct = min(100, int(float(_cc_score) / max(1, float(_cc_max)) * 100)) if _cc_max else 0
    _cc_color = "#16A34A" if _cc_pct >= 75 else "#D97706" if _cc_pct >= 40 else "#DC2626"
    _cc_links_html = ""
    if _cc_links:
        _cc_links_html = (
            "<div style='margin-top:10px'>"
            "<div style='font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--text2);margin-bottom:5px'>Profile Links Detected</div>"
            + "".join(
                f"<a href='{escape(str(lnk))}' target='_blank' style='display:inline-flex;align-items:center;gap:4px;"
                f"background:var(--primary-light);border:1px solid var(--primary-border);border-radius:8px;"
                f"padding:3px 10px;margin:2px;font-size:12px;color:var(--primary);text-decoration:none'>"
                f"&#128279; {escape(str(lnk)[:50])}{'...' if len(str(lnk))>50 else ''}</a>"
                for lnk in (_cc_links or []) if lnk
            )
            + "</div>"
        )
    _cc_plats_html = ""
    if _cc_plats:
        _cc_plats_html = (
            "<div style='margin-top:10px'>"
            "<div style='font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--text2);margin-bottom:5px'>Competitive Platforms</div>"
            + "".join(f"<span class='pill'>{escape(str(pl))}</span>" for pl in _cc_plats)
            + "</div>"
        )
    _hack_badge = (
        "<span style='background:#DCFCE7;color:#16A34A;border:1px solid #86EFAC;border-radius:999px;padding:4px 12px;font-size:12px;font-weight:700'>&#127942; Hackathon Prize Detected</span>"
        if _cc_hack else
        "<span style='background:#F3F4F6;color:#6B7280;border:1px solid #E5E7EB;border-radius:999px;padding:4px 12px;font-size:12px'>No Hackathon Prize</span>"
    )
    _oss_badge = (
        f"<span style='background:#EEF2FF;color:#6366F1;border:1px solid #C7D2FE;border-radius:999px;padding:4px 12px;font-size:12px;font-weight:700'>&#128190; OSS Signals: {int(_cc_oss)}</span>"
    )
    _coding_sk_html = ""
    if _coding_skills:
        _coding_sk_html = (
            f"<div class='card'>"
            f"<h2>Skills with Coding Signal</h2>"
            f"<div style='margin-top:6px'>"
            + "".join(_sk_chip(sk) for sk in _coding_skills[:12])
            + f"</div></div>"
        )
    _oss_sk_html = ""
    if _oss_skills:
        _oss_sk_html = (
            f"<div class='card'>"
            f"<h2>Open Source Skills</h2>"
            f"<div style='margin-top:6px'>"
            + "".join(_sk_chip(sk) for sk in _oss_skills[:12])
            + f"</div></div>"
        )
    _gh_hint = (
        f"<div style='color:var(--text2);font-size:13px'>Click <b>Outside Projects</b> tab to load GitHub repos for <b>@{escape(_gh_user)}</b>.</div>"
        if _gh_user else
        "<div style='color:var(--text2);font-size:13px'>No GitHub username detected from resume links.</div>"
    )
    _tab3_html = (
        f"<div id='tab-outside' class='tab-content'>"
        f"<div class='card'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
        f"<h2 style='margin:0'>Coding Community Signal</h2>"
        f"<span style='font-size:22px;font-weight:800;color:{_cc_color}'>{float(_cc_score):.1f}"
        f"<span style='font-size:12px;font-weight:400;color:var(--text2)'> / {float(_cc_max):.0f}</span></span></div>"
        f"<div style='background:#E5E7EB;height:4px;border-radius:3px;overflow:hidden;margin-bottom:10px'>"
        f"<div style='height:100%;width:{_cc_pct}%;background:{_cc_color}'></div></div>"
        + (f"<div style='font-size:13px;color:var(--text2);line-height:1.55;margin-bottom:8px'>{escape(str(_cc_reason))}</div>" if _cc_reason else "")
        + _cc_links_html
        + _cc_plats_html
        + f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin-top:12px'>{_hack_badge}{_oss_badge}</div>"
        + f"</div>"
        + _coding_sk_html
        + _oss_sk_html
        + f"<div class='card'>"
          f"<h2>GitHub Repositories</h2>"
          f"<div id='_ghRepos' style='margin-top:8px'>{_gh_hint}</div>"
          f"</div>"
        + f"</div>"
    )

    # ── Page CSS ──
    pcss = """<style>
.wrap{padding:22px 22px 80px}
.card{background:#FFFFFF;border:1px solid #CAD5E2;border-radius:12px;padding:18px 20px;margin-bottom:12px}
.card h2{margin:0 0 8px;color:#262626;font-size:16px;font-weight:700}
.kicker{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#62748E;margin-bottom:4px}
.pill{display:inline-flex;align-items:center;background:#F0F0FB;border:1px solid #E0E0F5;border-radius:999px;padding:3px 9px;margin:2px 2px 2px 0;font-size:11px;font-weight:500;color:#353395}
.btn{background:var(--primary);color:#fff;border:none;border-radius:10px;padding:8px 16px;font-weight:700;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.btn:hover{opacity:.88}
.btn-sec{background:#fff;color:var(--text2);border:1px solid var(--border);border-radius:8px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center}
.btn-sec:hover{background:var(--bg)}
@media(max-width:900px){[style*="grid-template-columns:1fr 1fr"]{grid-template-columns:1fr!important}[style*="grid-template-columns:repeat(auto-fit"]{grid-template-columns:1fr 1fr!important}}
.tab-bar{display:flex;margin-bottom:16px;border:1px solid #CAD5E2;border-radius:12px;overflow:hidden;background:#F8FAFC}
.tab-btn{flex:1;padding:11px 16px;border:none;border-right:1px solid #CAD5E2;background:transparent;font-size:13px;font-weight:600;color:#62748E;cursor:pointer;transition:all .15s}
.tab-btn:last-child{border-right:none}
.tab-btn:hover{background:#EEF2FF;color:#353395}
.tab-btn.tab-active{background:#fff;color:#353395;box-shadow:inset 0 -3px 0 #353395}
.tab-content{display:none}
.tab-content.tab-visible{display:block}
@media(max-width:700px){.tab-bar{border-radius:10px}[style*="grid-template-columns:1fr 1fr 1fr"]{grid-template-columns:1fr!important}}
</style>"""

    # ── JS (intake forms + interview questions) ──
    cid_js = json.dumps(candidate_id)
    pjs = (
        f"<script>\nconst _CID={cid_js};\n"
        """const _PP=[
  {key:"communication_skills",label:"Communication Skills",max:4,help:"Clarity, structured answers, listening, articulation.",guide:"Assess naturally during technical discussion."},
  {key:"domain_skills",label:"Domain Skills",max:5,help:"Depth of domain knowledge relevant to the role.",guide:"Ask: Explain a core domain concept in depth."},
  {key:"problem_solving",label:"Problem Solving",max:4,help:"Structured thinking, creative solutions, edge cases.",guide:"Give a problem-solving exercise or whiteboard question."},
];
function _fr(p,pfx,col){
  return `<div style="background:var(--white);border:1px solid var(--border);border-radius:12px;padding:13px">
    <div style="display:flex;justify-content:space-between;margin-bottom:5px">
      <b style="font-size:13px">${p.label}</b><span style="font-size:11px;color:${col};font-weight:700">max ${p.max}</span></div>
    <div style="font-size:12px;color:var(--text2);margin-bottom:6px">${p.help}</div>
    <div style="background:var(--bg);border-radius:6px;padding:6px 9px;font-size:11px;color:var(--text2);border-left:2px solid ${col};margin-bottom:8px">${p.guide}</div>
    <div style="display:flex;align-items:center;gap:7px">
      <span style="font-size:12px;color:var(--text2)">Score:</span>
      <input type="number" id="${pfx}_${p.key}" min="0" max="${p.max}" step="${p.max===1?1:0.5}"
        style="width:75px;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:5px 9px;font-size:14px;font-weight:700;color:var(--text)">
      <span style="font-size:11px;color:var(--text2)">/ ${p.max}</span></div></div>`;
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('_pRows').innerHTML=_PP.map(p=>_fr(p,'pi','#6366F1')).join('');
});
async function _subP(){
  const ov={};
  for(const p of _PP){const el=document.getElementById('pi_'+p.key);if(el&&el.value!=='')ov[p.key]=parseFloat(el.value)||0;}
  const ce=document.getElementById('pi_coding_skills');const coe=document.getElementById('pi_conceptual_skills');
  if(ce&&ce.value.trim())ov['coding_skills']=ce.value.trim();
  if(coe&&coe.value.trim())ov['conceptual_skills']=coe.value.trim();
  if(!Object.keys(ov).length){document.getElementById('_pSt').innerText='Fill at least one score.';return;}
  document.getElementById('_pSt').innerText='Submitting\u2026';
  try{
    const res=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:_CID,stage:'panel',stage_overrides:ov,recruiter_notes:''})});
    const d=await res.json();
    if(!res.ok){document.getElementById('_pSt').innerText='Error: '+(d.detail||res.statusText);return;}
    const s=d.stage_score_100??d.new_total;
    document.getElementById('_pSt').innerText='\u2713 Saved. Panel: '+s+'/100';
    document.getElementById('_pBadge').innerHTML=s+'<span style="font-size:13px;color:var(--text2)"> / 100</span>';
  }catch(e){document.getElementById('_pSt').innerText='Error: '+e;}
}
let _qd=null,_qs=[];
async function _gQ(){
  const btn=document.getElementById('_gQBtn');btn.textContent='Generating\u2026';btn.disabled=true;
  try{
    const res=await fetch('/generateInterviewQuestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:_CID})});
    _qd=await res.json();_rQ(_qd);document.getElementById('_aBtn').style.display='';
  }catch(e){alert('Failed: '+e);}
  btn.textContent='Regenerate Questions';btn.disabled=false;
}
function _rQ(qd){
  const qs=qd.questions||[];_qs=new Array(qs.length).fill(null);
  const rQs=qd.recruiter_questions||qs.filter(q=>q.stage!=='panel');
  const pQs=qd.panel_questions||qs.filter(q=>q.stage==='panel');
  const PC={high:'#DC2626',medium:'#D97706',low:'#16A34A'};
  const w=document.getElementById('_qW');
  w.innerHTML=`<div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <button onclick="_sT('r')" style="padding:7px 14px;font-size:13px;border-radius:999px;background:#FEF3C7;color:#D97706;border:1px solid #FDE68A;cursor:pointer">Recruiter (${rQs.length})</button>
    <button onclick="_sT('p')" style="padding:7px 14px;font-size:13px;border-radius:999px;background:var(--primary-light);color:var(--primary2);border:1px solid var(--primary-border);cursor:pointer">Panel (${pQs.length})</button>
    <span style="color:var(--text2);font-size:12px;line-height:2.4">Total: ${qs.length} | High: ${qd.high_priority_count||0}</span>
  </div><div id="_tR"></div><div id="_tP" style="display:none"></div>`;
  function _rt(list,cid){
    document.getElementById(cid).innerHTML=list.map(q=>{
      const pc=PC[q.priority]||'#9CA3AF';const idx=qs.indexOf(q);
      return `<div style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:8px">
        <div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:7px">
          <span style="background:${pc}18;color:${pc};border:1px solid ${pc}44;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700;text-transform:uppercase">${q.priority||'medium'}</span>
          <span style="font-size:13px;font-weight:600;flex:1">${q.question||''}</span></div>
        ${q.scoring_guide?.what_good_looks_like?'<div style="font-size:12px;color:var(--text2);margin-bottom:3px"><b>Good:</b> '+q.scoring_guide.what_good_looks_like+'</div>':''}
        ${q.scoring_guide?.red_flags?'<div style="font-size:12px;color:#DC2626"><b>Red flags:</b> '+q.scoring_guide.red_flags+'</div>':''}
        <div style="display:flex;align-items:center;gap:7px;margin-top:7px">
          <span style="font-size:12px;color:var(--text2)">Score (0-5):</span>
          <input type="number" min="0" max="5" step="0.5" style="width:65px;background:var(--white);border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:13px" onchange="_qs[${idx}]=parseFloat(this.value)||0">
        </div></div>`;
    }).join('');
  }
  _rt(rQs,'_tR');_rt(pQs,'_tP');
}
function _sT(t){document.getElementById('_tR').style.display=t==='r'?'':'none';document.getElementById('_tP').style.display=t==='p'?'':'none';}
async function _aQ(){
  if(!_qd)return;
  const res=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:_CID,questions:_qd.questions,scores:_qs})});
  const d=await res.json();
  document.getElementById('_qR').innerHTML='<div style="background:#DCFCE7;border:1px solid #86EFAC;border-radius:8px;padding:9px 13px;font-size:13px;color:#16A34A">\u2713 Scores applied. New total: '+(d.new_total||'\u2014')+'/100</div>';
}
// ── Outcome recording ──
(function(){
  const sel=document.getElementById('oc_outcome');
  if(!sel)return;
  function _tog(){
    const v=sel.value;
    ['oc_placed_grp','oc_role_grp'].forEach(id=>{const el=document.getElementById(id);if(el)el.style.display=v==='PLACED'?'':'none';});
    const rj=document.getElementById('oc_rej_grp');if(rj)rj.style.display=v==='REJECTED'?'':'none';
  }
  sel.addEventListener('change',_tog);
})();
async function _subOC(){
  const outcome=document.getElementById('oc_outcome').value;
  const body={
    outcome,
    rejection_stage:(document.getElementById('oc_rej_stage')||{}).value||'',
    placed_company:(document.getElementById('oc_company')||{}).value||'',
    placed_role:(document.getElementById('oc_role')||{}).value||'',
    placed_date:new Date().toISOString().split('T')[0],
    feedback_notes:(document.getElementById('oc_notes')||{}).value||'',
    recorded_by:(document.getElementById('oc_recorded_by')||{}).value||''
  };
  const st=document.getElementById('_ocSt');
  st.textContent='Saving\u2026';
  try{
    const r=await fetch('/outcome/'+_CID,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){st.innerHTML='<span style="color:#16A34A">\u2713 Saved \u2014 refreshing\u2026</span>';setTimeout(()=>location.reload(),800);}
    else{st.innerHTML='<span style="color:#DC2626">Server error</span>';}
  }catch(e){st.innerHTML='<span style="color:#DC2626">Network error</span>';}
}
"""
        + f"""
// ── Tab switching ──
function showTab(name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('tab-visible'));
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('tab-active'));
  document.getElementById('tab-' + name).classList.add('tab-visible');
  document.querySelector('.tab-btn[data-tab="' + name + '"]').classList.add('tab-active');
  if (name === 'outside' && !_ghLoaded) {{ _ghLoaded = true; _loadGH(); }}
}}
let _ghLoaded = false;
async function _loadGH() {{
  const ghUser = {json.dumps(_gh_user)};
  const el = document.getElementById('_ghRepos');
  if (!ghUser || !el) return;
  el.innerHTML = '<div style="color:var(--text2);font-size:13px">Loading repos for @' + ghUser + '\u2026</div>';
  try {{
    const res = await fetch('https://api.github.com/users/' + ghUser + '/repos?sort=updated&per_page=6&type=owner');
    if (!res.ok) {{ el.innerHTML = '<div style="color:var(--text2)">GitHub API error ' + res.status + '.</div>'; return; }}
    const repos = await res.json();
    if (!repos.length) {{ el.innerHTML = '<div style="color:var(--text2)">No public repos found.</div>'; return; }}
    const LC = {{JavaScript:'#F7DF1E',TypeScript:'#3178C6',Python:'#3776AB',Go:'#00ADD8',Rust:'#DEA584',Java:'#ED8B00'}};
    el.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px">'
      + repos.map(r => {{
          const lc = r.language ? (LC[r.language] || '#6B7280') : null;
          return '<div style="border:1px solid var(--border);border-radius:10px;padding:14px;background:var(--white)">'
            + '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">'
            + '<a href="' + r.html_url + '" target="_blank" style="font-weight:700;font-size:13px;color:var(--primary);text-decoration:none">' + r.name + '</a>'
            + '<span style="font-size:11px;color:var(--text2)">\u2b50 ' + r.stargazers_count + '</span></div>'
            + '<div style="font-size:12px;color:var(--text2);margin-bottom:8px;min-height:28px">' + (r.description || '<i>No description</i>') + '</div>'
            + (lc ? '<span style="font-size:11px;color:' + lc + '"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + lc + ';margin-right:3px"></span>' + r.language + '</span>' : '');
        }}).join('') + '</div>';
  }} catch(e) {{ el.innerHTML = '<div style="color:var(--text2)">Failed to load repos: ' + e + '</div>'; }}
}}
</script>"""
    )

    # ── Build page body ──
    body_html = (
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"
        # Breadcrumb + action bar
        f"<div style='margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px'>"
        f"<div style='display:flex;gap:8px;align-items:center'>"
        f"<a href='/' style='color:var(--text2);font-size:13px'>&larr; Dashboard</a>"
        f"<span style='color:var(--border)'>|</span>"
        f"<a href='/candidates' style='color:var(--text2);font-size:13px'>Candidates</a>"
        f"<span style='color:var(--border)'>|</span>"
        f"<span style='font-size:13px;color:var(--text)'>{escape(name)}</span></div>"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap'>"
        f"<a href='/recruiter-screen/{cid_enc}' class='btn' style='background:#D97706;color:#fff;font-size:12px;padding:7px 13px'>&#128222; Recruiter Screen</a>"
        f"<a href='/panel-screen/{cid_enc}' class='btn' style='background:#353395;font-size:12px;padding:7px 13px'>&#128203; Panel Screen</a>"
        f"</div></div>"
        # Hero card
        f"<div class='card'>"
        f"<div style='display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap'>"
        f"<div style='width:50px;height:50px;background:linear-gradient(135deg,var(--primary),var(--primary2));border-radius:14px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;font-weight:800;flex-shrink:0'>{escape(avatar)}</div>"
        f"<div style='flex:1;min-width:180px'>"
        f"<div class='kicker'>Candidate Profile</div>"
        f"<div style='font-size:22px;font-weight:800;margin:2px 0 5px;color:var(--text)'>{escape(name)}</div>"
        f"<div style='color:var(--text2);font-size:13px;margin-bottom:6px'>"
        f"{'<span>&#128231; ' + escape(str(email)) + '</span> &bull; ' if email and str(email) not in ('N/A','NA','n/a') else ''}"
        f"{'<span>&#128241; ' + escape(str(phone)) + '</span> &bull; ' if phone and str(phone) not in ('N/A','NA','n/a') else ''}"
        f"{'<span>&#128205; ' + escape(str(location)) + '</span> &bull; ' if location and str(location) not in ('N/A','NA','n/a','Unknown') else ''}"
        f"{'<span>&#9203; ' + yoe_txt + '</span>' if yoe_txt and yoe_txt != chr(8212) else ''}"
        f"</div>"
        f"{'<div style=\"font-size:13px;color:var(--text2);line-height:1.55;margin-bottom:7px;max-width:720px\">' + escape(summary[:280]) + ('&hellip;' if len(summary)>280 else '') + '</div>' if summary else ''}"
        f"<div style='margin-top:4px'>{bert_html}</div>"
        + (f"<div style='margin-top:8px;display:inline-flex;align-items:center;gap:6px;"
           f"background:{_oc_bg};border:1px solid {_oc_bd};border-radius:999px;"
           f"padding:4px 12px;font-size:12px;font-weight:700;color:{_oc_fg}'>"
           f"{'&#10003;' if oc_status == 'PLACED' else '&#9679;'} {oc_status.replace('_', ' ')}"
           + (f" &nbsp;&bull;&nbsp; {escape(oc_data.get('placed_company', ''))}" if oc_status == "PLACED" and oc_data.get("placed_company") else "")
           + f"</div>" if oc_data else "")
        + f"</div>"
        f"<div style='text-align:right;min-width:100px'>"
        f"<div style='font-size:44px;font-weight:900;color:{_bc(band) if band else '#353395'};line-height:1'>{int(_display_score)}</div>"
        f"{'<div style=\"font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + _bc(band) + ';margin-top:2px\">' + escape(band) + '</div>' if band else ''}"
        f"<div style='font-size:11px;color:var(--text2);margin-top:3px'>{escape(_archetype)} \u00b7 {escape(_arch_desc)}</div>"
        f"</div></div></div>"
        # Score tiles
        + score_tiles_html
        # Reject flags banner
        + rfbanner
        # Tab bar + open Tab 1 (Detail)
        + f"<div class='tab-bar'>"
          f"<button class='tab-btn tab-active' data-tab='detail' onclick=\"showTab('detail')\">&#128196; Detail</button>"
          f"<button class='tab-btn' data-tab='summary' onclick=\"showTab('summary')\">&#9889; Summary</button>"
          f"<button class='tab-btn' data-tab='outside' onclick=\"showTab('outside')\">&#128187; Outside Projects</button>"
          f"</div>"
          f"<div id='tab-detail' class='tab-content tab-visible'>"
        # Stage scores
        + f"<div class='card'>"
          f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px'>"
          f"<h2 style='margin:0'>Stage Scores</h2>"
          f"<div style='font-size:12px;color:var(--text2)'>Auto-scored from resume &bull; Recruiter &amp; Panel filled via intake forms below</div></div>"
          + stage_bar
          + f"</div>"
        # Candidate overview + recruiter summary
        + f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px'>"
          f"<div class='card' style='margin:0'><h2>Candidate Overview</h2>"
          f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px'>{ov_fields}</div></div>"
          f"<div class='card' style='margin:0'><h2>Recruiter Summary</h2>"
          f"<div style='font-size:13px;color:var(--text);line-height:1.6'>"
          + (escape(recruiter_summary) if recruiter_summary else "<span style='color:var(--text2)'>Not available. Re-analyze to generate.</span>")
          + f"</div></div></div>"
        # Rubric scorecard
        + rubric_sec
        # Top skill evidence
        + (f"<div class='card'>"
           f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:6px'>"
           f"<h2 style='margin:0'>Top Skill Evidence "
           f"<span style='font-size:12px;font-weight:400;color:var(--text2)'>({len(top_skills)} tracked)</span></h2>"
           f"<div style='font-size:12px;color:var(--text2)'>"
           + " ".join(f"<span style='color:{c}'>&bull; {d.replace('_',' ')}</span>" for d,c in _DC.items())
           + f"</div></div>"
             f"<div style='margin-bottom:10px'>{skills_chips}</div>"
             f"{'<details><summary style=\"list-style:none;cursor:pointer;color:var(--primary2);font-size:13px;font-weight:600;margin-bottom:8px\">&#9654; Detailed Evidence Cards (' + str(len(top_skills[:10])) + ')</summary>' + skill_ev_html + '</details>' if skill_ev_html else ''}"
             f"</div>" if skills_chips else "")
        # Semantic taxonomy
        + (f"<div class='card'>"
           f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px'>"
           f"<h2 style='margin:0'>Semantic Taxonomy</h2>"
           f"<span style='font-size:13px;font-weight:600;color:#353395'>Consistency: {int(cst*100)}%</span></div>"
           + (f"<div style='background:var(--bg);border-radius:8px;padding:9px 12px;font-size:13px;color:var(--text2);margin-bottom:10px'>{role_rat}</div>" if role_rat else "")
           + (sem_blocks or "<div style='color:var(--text2);font-size:13px'>No cluster evidence generated.</div>")
           + f"</div>" if sem_blocks else "")
        # Experience + Education side by side
        + f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px'>"
          f"<div class='card' style='margin:0'><h2>Experience Analysis</h2>{exp_rows}</div>"
          f"<div class='card' style='margin:0'><h2>Education Analysis</h2>{edu_rows}</div>"
          f"</div>"
        # Strengths + Gaps
        + (f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px'>"
           f"<div class='card' style='margin:0'><h2>Strengths</h2>"
           + "".join(f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:13px'>&#10003; {escape(s3)}</div>" for s3 in strs[:6])
           + ("<div style='color:var(--text2);font-size:13px;margin-top:4px'>No strengths captured.</div>" if not strs else "")
           + f"</div>"
             f"<div class='card' style='margin:0'><h2>Gaps &amp; Risks</h2>"
           + "".join(f"<div style='padding:5px 0;border-bottom:1px solid var(--border);font-size:13px;color:#D97706'>&#9888; {escape(g2)}</div>" for g2 in gaps_list[:6])
           + "".join(f"<div style='font-size:12px;color:#DC2626;margin-top:3px'>&#9888; {escape(r2)}</div>" for r2 in risks[:3])
           + ("<div style='color:var(--text2);font-size:13px;margin-top:4px'>No gaps captured.</div>" if not gaps_list and not risks else "")
           + f"</div></div>" if strs or gaps_list or risks else "")
        # Red flags edge cases
        + rf_sec
        # Panel intake
        + f"<div class='card'>"
          f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px'>"
          f"<div><h2 style='margin:0'>Panel Intake "
          f"<span style='font-size:12px;font-weight:400;color:var(--text2)'>\u2014 Fill after technical interview</span></h2>"
          f"<div style='color:var(--text2);font-size:12px;margin-top:3px'>Panel params. Submit to update Final Panel /100.</div></div>"
          f"<div id='_pBadge' style='font-size:24px;font-weight:800;color:#6366F1'></div></div>"
          f"<div id='_pRows' style='display:grid;grid-template-columns:1fr 1fr;gap:10px'></div>"
          f"<div style='margin-top:12px'>"
          f"<div style='font-size:12px;font-weight:600;color:var(--text2);margin-bottom:8px'>Qualitative Assessment (free text)</div>"
          f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px'>"
          f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px'>"
          f"<b style='font-size:13px'>Coding Skills</b>"
          f"<div style='color:var(--text2);font-size:12px;margin:5px 0 7px'>Panel assessment of live coding ability.</div>"
          f"<textarea id='pi_coding_skills' rows='3' placeholder='e.g. Solved two medium LC problems cleanly\u2026' style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:7px;color:var(--text);padding:7px 10px;font-size:13px;resize:vertical'></textarea></div>"
          f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px'>"
          f"<b style='font-size:13px'>Conceptual Skills</b>"
          f"<div style='color:var(--text2);font-size:12px;margin:5px 0 7px'>CS and domain conceptual understanding.</div>"
          f"<textarea id='pi_conceptual_skills' rows='3' placeholder='e.g. Strong on distributed system fundamentals\u2026' style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:7px;color:var(--text);padding:7px 10px;font-size:13px;resize:vertical'></textarea></div></div></div>"
          f"<div style='margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap'>"
          f"<button onclick='_subP()' style='background:linear-gradient(135deg,#6b9fff,#4a7fe0);color:#fff;border:none;border-radius:10px;padding:8px 16px;font-weight:700;font-size:13px;cursor:pointer'>Submit Panel Scores</button>"
          f"<div id='_pSt' style='font-size:13px;color:var(--text2)'></div></div></div>"
        # Recruiter call panel
        + f"<div class='card'>"
          f"<div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px'>"
          f"<div><h2 style='margin:0'>Recruiter Call Panel</h2>"
          f"<div style='color:var(--text2);font-size:12px;margin-top:3px'>Generate structured interview questions for recruiter &amp; panel screens.</div></div>"
          f"<div style='display:flex;gap:8px;flex-wrap:wrap'>"
          f"<button id='_gQBtn' onclick='_gQ()' style='background:var(--primary);color:#fff;border:none;border-radius:10px;padding:8px 16px;font-weight:700;font-size:13px;cursor:pointer'>Generate Interview Questions</button>"
          f"<button id='_aBtn' onclick='_aQ()' style='display:none;background:linear-gradient(135deg,#D97706,#F59E0B);color:#fff;border:none;border-radius:10px;padding:8px 16px;font-weight:700;font-size:13px;cursor:pointer'>Apply Call Scores</button>"
          f"</div></div>"
          f"<div id='_qW' style='margin-top:12px'></div><div id='_qR' style='margin-top:8px'></div></div>"
        # JD match history
        + f"<div class='card'>"
          f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
          f"<h2 style='margin:0'>JD Match History</h2>"
          f"<a href='/jobs' class='btn-sec' style='padding:5px 11px;font-size:12px'>+ Match to a JD</a></div>"
          + (jdm_html or "<div style='color:var(--text2);font-size:13px'>No JD matches saved yet.</div>")
          + f"</div>"
        # Interview scheduling card
        + f"<div class='card'>"
          f"<h2 style='margin:0 0 4px'>Interview Schedule</h2>"
          f"<div style='color:var(--text2);font-size:12px;margin-bottom:12px'>Set the next interview slot for this candidate.</div>"
          + (f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:12px;padding:9px 14px;"
             f"background:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px'>"
             f"<span style='font-size:18px'>&#128197;</span>"
             f"<div>"
             f"<div style='font-weight:700;color:#1D4ED8;font-size:13px'>{escape(row.get('interview_round') or 'Interview')}</div>"
             f"<div style='font-size:12px;color:#374151'>{escape(row.get('interview_date', ''))}"
             + (f" at {escape(row.get('interview_time', ''))}" if row.get('interview_time') else '')
             + f"</div></div></div>"
             if row.get('interview_date') else '')
          + f"<form method='POST' action='/candidate/{candidate_id}/schedule'>"
            f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px'>"
            f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px'>"
            f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Date</div>"
            f"<input type='date' name='interview_date' value='{escape(row.get('interview_date') or '')}'"
            f" style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);"
            f"border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'></div>"
            f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px'>"
            f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Time</div>"
            f"<input type='time' name='interview_time' value='{escape(row.get('interview_time') or '')}'"
            f" style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);"
            f"border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'></div>"
            f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px'>"
            f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Round</div>"
            f"<select name='interview_round' style='width:100%;background:var(--white);border:1px solid var(--border);"
            f"border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'>"
            f"<option value=''>— select —</option>"
            + "".join(
                f"<option value='{rnd}'{' selected' if row.get('interview_round') == rnd else ''}>{rnd}</option>"
                for rnd in ("Round 1", "Round 2", "Round 3", "Final Round", "HR Round", "Client Round")
            )
            + f"</select></div></div>"
              f"<div style='margin-top:12px'>"
              f"<button type='submit' style='background:var(--primary);color:#fff;border:none;border-radius:10px;"
              f"padding:8px 16px;font-weight:700;font-size:13px;cursor:pointer'>Save Schedule</button>"
              f"</div></form></div>"
        # Outcome recording card
        + f"<div class='card'>"
          f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;flex-wrap:wrap;gap:8px'>"
          f"<div><h2 style='margin:0 0 3px'>Placement Outcome</h2>"
          f"<div style='color:var(--text2);font-size:12px'>Record final disposition. Feeds the outcome analytics flywheel.</div></div>"
          f"<a href='/outcomes' style='font-size:12px;color:var(--primary);text-decoration:none'>View all outcomes &rarr;</a></div>"
        + (f"<div style='display:flex;align-items:center;gap:8px;margin:10px 0;padding:9px 14px;"
           f"background:{_oc_bg};border:1px solid {_oc_bd};border-radius:10px'>"
           f"<b style='color:{_oc_fg}'>{oc_status.replace('_', ' ')}</b>"
           + (f"<span style='color:var(--text2);font-size:13px'>&bull; {escape(oc_data.get('placed_company', ''))}</span>" if oc_data.get("placed_company") else "")
           + (f"<span style='color:var(--text2);font-size:13px'>&bull; {escape(oc_data.get('placed_role', ''))}</span>" if oc_data.get("placed_role") else "")
           + (f"<span style='color:var(--text2);font-size:12px;margin-left:auto'>{escape(oc_data.get('recorded_by', ''))}</span>" if oc_data.get("recorded_by") else "")
           + f"</div>" if oc_data else "")
        + f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:12px'>"
          f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px'>"
          f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Outcome</div>"
          f"<select id='oc_outcome' style='width:100%;background:var(--white);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'>"
          f"<option value='IN_PROGRESS'{' selected' if oc_status == 'IN_PROGRESS' else ''}>In Progress</option>"
          f"<option value='PLACED'{' selected' if oc_status == 'PLACED' else ''}>Placed \u2713</option>"
          f"<option value='REJECTED'{' selected' if oc_status == 'REJECTED' else ''}>Rejected</option>"
          f"<option value='WITHDREW'{' selected' if oc_status == 'WITHDREW' else ''}>Withdrew</option>"
          f"</select></div>"
          f"<div id='oc_placed_grp' style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px;{'display:none' if oc_status != 'PLACED' else ''}'>"
          f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Placed Company</div>"
          f"<input id='oc_company' type='text' value='{escape(oc_data.get('placed_company', ''))}' placeholder='e.g. Google'"
          f" style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'></div>"
          f"<div id='oc_role_grp' style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px;{'display:none' if oc_status != 'PLACED' else ''}'>"
          f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Placed Role</div>"
          f"<input id='oc_role' type='text' value='{escape(oc_data.get('placed_role', ''))}' placeholder='e.g. Senior SWE'"
          f" style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'></div>"
          f"<div id='oc_rej_grp' style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px;{'display:none' if oc_status != 'REJECTED' else ''}'>"
          f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Rejected At Stage</div>"
          f"<select id='oc_rej_stage' style='width:100%;background:var(--white);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'>"
          f"<option value=''>— select —</option>"
          + "".join(f"<option value='{st}'{' selected' if oc_data.get('rejection_stage') == st else ''}>{st.title()}</option>" for st in ("resume", "recruiter", "panel", "offer"))
          + f"</select></div></div>"
          f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px'>"
          f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px'>"
          f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Feedback Notes</div>"
          f"<textarea id='oc_notes' rows='2' placeholder='Recruiter notes, client feedback\u2026'"
          f" style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text);resize:vertical'>"
          f"{escape(oc_data.get('feedback_notes', ''))}</textarea></div>"
          f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px'>"
          f"<div style='font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:5px'>Recorded By</div>"
          f"<input id='oc_recorded_by' type='text' value='{escape(oc_data.get('recorded_by', ''))}' placeholder='Your name'"
          f" style='width:100%;box-sizing:border-box;background:var(--white);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:13px;color:var(--text)'></div></div>"
          f"<div style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px'>"
          f"<button onclick='_subOC()' style='background:var(--primary);color:#fff;border:none;border-radius:10px;padding:8px 16px;font-weight:700;font-size:13px;cursor:pointer'>Save Outcome</button>"
          f"<div id='_ocSt' style='font-size:13px;color:var(--text2)'></div></div></div>"
        # Analysis JSON
        + f"<div class='card'><details>"
          f"<summary style='list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center'>"
          f"<h2 style='margin:0'>Analysis Output JSON</h2>"
          f"<span style='font-size:12px;color:var(--text2)'>Click to expand &#9658;</span></summary>"
          f"<div style='color:var(--text2);font-size:12px;margin:8px 0'>Normalized analysis response from the pipeline.</div>"
          f"<pre style='white-space:pre-wrap;background:#F8FAFC;border:1px solid var(--border);border-radius:8px;padding:13px;max-height:500px;overflow:auto;color:#333;font-size:11px'>"
          f"{escape(json.dumps(analysis, indent=2, ensure_ascii=False))}</pre>"
          f"</details></div>"
          f"</div>"                          # close tab-detail
        + _tab2_html
        + _tab3_html
        + f"</div></div></div>"              # close wrap / main / app-shell
    )

    return HTMLResponse(
        f"<!DOCTYPE html><html><head>"
        f"<meta charset='utf-8'><title>{escape(name)} \u00b7 Candidate Profile</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"{_BASE_CSS}{pcss}"
        f"</head><body>{body_html}{pjs}</body></html>"
    )


@app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
def candidate_profile(candidate_id: str, user: dict = Depends(get_current_user)):
    return _render_candidate_profile_v2(candidate_id, user)


# ===========================================================================
# Prompt Library
# ===========================================================================

@app.get("/prompt-library", response_class=HTMLResponse)
def prompt_library_page(user: dict = Depends(require_role("super_admin"))):
    from jd_match_prompt_library import (
        NARRATIVE_SYSTEM, NARRATIVE_USER_TEMPLATE,
        CLUSTER_DEFINITIONS, BERT_ADJUSTMENT_RULES,
        SCORING_DIMENSION_GUIDE, TILE_REASON_GUIDELINES,
        SCREENING_Q_PATTERNS,
    )
    import html as _html

    sidebar_html = _sidebar("prompt-library", user)

    def _section(title: str, content: str, mono: bool = True) -> str:
        esc = _html.escape(content)
        body = f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.6'>{esc}</pre>" if mono else content
        return (
            f"<details class='card' style='margin-bottom:14px' open>"
            f"<summary style='cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center'>"
            f"<span class='kicker' style='margin:0'>{title}</span>"
            f"<span style='font-size:11px;color:var(--text2)'>Click to collapse</span></summary>"
            f"<div style='margin-top:12px'>{body}</div></details>"
        )

    def _cluster_ref(cl: dict) -> str:
        dims_rows = "".join(
            f"<tr><td style='padding:4px 10px 4px 0;color:var(--text)'>{d[0]}</td>"
            f"<td style='padding:4px 10px 4px 0;font-family:monospace;color:#6366F1'>{d[1]}</td>"
            f"<td style='padding:4px 0;color:var(--text2)'>{int(d[2]*100)}%</td></tr>"
            for d in cl["dimensions"]
        )
        return (
            f"<div style='border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px'>"
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:8px'>"
            f"<span style='font-size:20px'>{cl['icon']}</span>"
            f"<span style='font-weight:600;font-size:14px;color:var(--text)'>{cl['label']}</span></div>"
            f"<div style='font-size:12px;color:var(--text2);margin-bottom:10px'>{_html.escape(cl['description'])}</div>"
            f"<div style='font-size:11px;color:#6366F1;margin-bottom:8px'><strong>CEO question:</strong> {_html.escape(cl['ceo_question'])}</div>"
            f"<table style='width:100%;font-size:12px'><thead>"
            f"<tr><th style='text-align:left;padding-bottom:4px;color:var(--text2)'>Dimension</th>"
            f"<th style='text-align:left;padding-bottom:4px;color:var(--text2)'>Key</th>"
            f"<th style='text-align:left;padding-bottom:4px;color:var(--text2)'>Weight</th></tr></thead>"
            f"<tbody>{dims_rows}</tbody></table>"
            f"<div style='margin-top:10px;font-size:11px;color:var(--text2);border-top:1px solid var(--border);padding-top:8px'>"
            f"<strong>Correlation:</strong> {_html.escape(cl['correlation_note'])}</div>"
            f"</div>"
        )

    def _sq_ref(patterns: dict) -> str:
        rows = "".join(
            f"<tr><td style='padding:6px 12px 6px 0;font-family:monospace;font-size:11px;color:#6366F1;white-space:nowrap;vertical-align:top'>{k}</td>"
            f"<td style='padding:6px 0;font-size:12px;color:var(--text);line-height:1.5'>{_html.escape(v)}</td></tr>"
            for k, v in patterns.items()
        )
        return f"<table style='width:100%;font-size:12px'><tbody>{rows}</tbody></table>"

    def _tile_ref(guide: dict) -> str:
        rows = "".join(
            f"<tr><td style='padding:6px 12px 6px 0;font-family:monospace;font-size:11px;color:#6366F1;white-space:nowrap;vertical-align:top'>{k}</td>"
            f"<td style='padding:6px 0;font-size:12px;color:var(--text);line-height:1.5'>{_html.escape(v)}</td></tr>"
            for k, v in guide.items()
        )
        return f"<table style='width:100%;font-size:12px'><tbody>{rows}</tbody></table>"

    clusters_html = "".join(_cluster_ref(cl) for cl in CLUSTER_DEFINITIONS)

    body = (
        f"<div style='max-width:900px;margin:0 auto;padding:24px 16px'>"
        f"<div style='margin-bottom:20px'>"
        f"<div class='kicker'>Prompt & Scoring Library</div>"
        f"<div style='font-size:22px;font-weight:700;color:var(--text);margin:4px 0'>JD Match Intelligence</div>"
        f"<div style='font-size:13px;color:var(--text2)'>All prompts, scoring rules, and narrative guidelines used in the JD matching pipeline.</div>"
        f"</div>"

        + _section("LLM Analyst System Prompt (NARRATIVE_SYSTEM)", NARRATIVE_SYSTEM)
        + _section("LLM User Template (NARRATIVE_USER_TEMPLATE)", NARRATIVE_USER_TEMPLATE)
        + _section("5-Cluster Scoring Model (CLUSTER_DEFINITIONS)", clusters_html, mono=False)
        + _section("BERT Score Adjustment Rules", BERT_ADJUSTMENT_RULES)
        + _section("11-Dimension Scoring Guide", SCORING_DIMENSION_GUIDE)
        + _section("Screening Question Patterns", _sq_ref(SCREENING_Q_PATTERNS), mono=False)
        + _section("Tile Reason Guidelines (LLM)", _tile_ref(TILE_REASON_GUIDELINES), mono=False)

        + f"</div>"
    )

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Prompt Library</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head>"
        f"<body><div class='layout'>{sidebar_html}"
        f"<div class='main'>{body}</div></div></body></html>"
    )


# ===========================================================================
# JD PDF extraction endpoint
# ===========================================================================

_JD_EXTRACT_PROMPT = (
    "You are a recruiter parsing a Job Description document.\n"
    "Extract structured fields and return ONLY strict JSON — no markdown, no extra text.\n\n"
    "{\n"
    '  "title": "exact job title (role only, NOT company name)",\n'
    '  "company_name": "hiring company / organization name, or null if not found",\n'
    '  "role_family": "DATA_ENGINEER|DATA_SCIENTIST|ML_ENGINEER|ANALYST|BI_LEADER|AGENTIC_AI|DOMAIN_SPECIALIST",\n'
    '  "yoe_min": integer_or_null,\n'
    '  "yoe_max": integer_or_null,\n'
    '  "mandatory_skills": ["skill1", "skill2"],\n'
    '  "nice_to_have_skills": ["skill1"],\n'
    '  "description": "2-3 sentence summary of role and responsibilities",\n'
    '  "dna_fit": "PRODUCT|CONSULTING|PLATFORM_INFRA|DOMAIN_SPECIALIST"\n'
    "}\n\n"
    "Rules:\n"
    "- title: job role name only (e.g. 'AI Architect', not 'AI Architect_Sigmoid')\n"
    "- company_name: the organization hiring for this role\n"
    "- mandatory_skills: explicitly required tools/technologies/languages\n"
    "- nice_to_have_skills: marked preferred / a plus / bonus\n"
    "- yoe_min/max: from patterns like 5+ years, 3-7 years, minimum 4 years\n"
    "- Use null for unknown scalars, [] for unknown arrays\n"
    "- role_family and dna_fit: pick single best match"
)


@app.post("/jobs/extract_jd")
async def extract_jd_pdf(file: UploadFile = File(...)):
    """Extract structured JD fields from a PDF/DOCX/TXT file."""
    import tempfile
    from pathlib import Path as _P
    fname = file.filename or "jd.pdf"
    content = await file.read()
    suffix = _P(fname).suffix.lower()
    raw_text = ""
    if suffix in (".pdf", ".docx"):
        try:
            from pdf_to_json_extractor import extract_text
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = _P(tmp.name)
            raw_text = extract_text(tmp_path)
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Text extraction failed: {exc}")
    else:
        raw_text = content.decode("utf-8", errors="replace")
    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted.")
    try:
        from llm_client import call_llm_json, analysis_model, provider_enabled
        if not provider_enabled():
            return JSONResponse(content={})
        result = call_llm_json(
            model_name=analysis_model("mistral-medium-latest"),
            messages=[
                {"role": "system", "content": _JD_EXTRACT_PROMPT},
                {"role": "user", "content": f"Job Description:\n\n{raw_text[:6000]}"},
            ],
            max_tokens=600,
        )
        return JSONResponse(content=result if isinstance(result, dict) else {})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM extraction failed: {exc}")


# ===========================================================================
# Candidate Sourcing Agent
# ===========================================================================

from pydantic import BaseModel as _BaseModel

class SourceRequest(_BaseModel):
    query: str
    jd_id: str | None = None
    count: int = 20


_BOARD_CSS = """<style>
/* ── Command Center Design Tokens ─────────────────────────── */
:root{
  --navy:#0B1F3A;--brand:#2563EB;--indigo:#4F46E5;--cyan:#0891B2;
  --pg:#F6F8FB;--card:#FFFFFF;--soft:#F8FAFC;--blue-surf:#EFF6FF;
  --t1:#0F172A;--t2:#475569;--t3:#94A3B8;
  --border:#E2E8F0;--border-strong:#CBD5E1;
  --ok:#16A34A;--ok-bg:#ECFDF5;--ok-border:#A7F3D0;
  --warn:#D97706;--warn-bg:#FFFBEB;--warn-border:#FDE68A;
  --danger:#DC2626;--danger-bg:#FEF2F2;--danger-border:#FECACA;
  --info:#0284C7;--info-bg:#F0F9FF;--info-border:#BAE6FD;
  --shadow-sm:0 6px 18px rgba(15,23,42,.06);
  --shadow-md:0 10px 28px rgba(15,23,42,.10);
}
body{background:var(--pg)}

/* ── Command header ─────────────────────────────────────────── */
.cc-header{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:20px}
.cc-title-group{}
.cc-eyebrow{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--brand);margin-bottom:4px}
.cc-title{font-size:24px;font-weight:900;color:var(--navy);line-height:1.15;margin:0 0 4px}
.cc-subtitle{font-size:13px;color:var(--t2)}
.cc-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.cc-btn{padding:8px 16px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;border:1.5px solid var(--border);color:var(--t2);background:var(--card);box-shadow:var(--shadow-sm);transition:all .15s;white-space:nowrap}
.cc-btn:hover{border-color:var(--brand);color:var(--brand);box-shadow:var(--shadow-md)}
.cc-btn.primary{background:var(--brand);color:#fff;border-color:var(--brand)}
.cc-btn.primary:hover{background:#1D4ED8;box-shadow:0 6px 20px rgba(37,99,235,.35)}

/* ── Quick filter tabs ──────────────────────────────────────── */
.qf-bar{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px}
.qf-btn{padding:6px 14px;border-radius:999px;font-size:12px;font-weight:600;cursor:pointer;border:1.5px solid var(--border);color:var(--t2);background:var(--card);transition:all .15s;white-space:nowrap}
.qf-btn:hover{border-color:var(--brand);color:var(--brand)}
.qf-btn.active{background:var(--brand);color:#fff;border-color:var(--brand)}

/* ── Today's Brief ──────────────────────────────────────────── */
.brief-card{background:linear-gradient(135deg,var(--navy) 0%,#1E3A6E 100%);border-radius:14px;padding:18px 22px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;box-shadow:var(--shadow-md)}
.brief-text{color:rgba(255,255,255,.7);font-size:13px;line-height:1.6}
.brief-text strong{color:#fff;font-weight:700}
.brief-pills{display:flex;gap:8px;flex-wrap:wrap}
.brief-pill{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:999px;padding:4px 12px;font-size:11px;font-weight:700;color:#fff;white-space:nowrap}
.brief-pill.warn{background:rgba(217,119,6,.25);border-color:rgba(217,119,6,.5);color:#FCD34D}
.brief-pill.danger{background:rgba(220,38,38,.25);border-color:rgba(220,38,38,.5);color:#FCA5A5}
.brief-pill.ok{background:rgba(22,163,74,.25);border-color:rgba(22,163,74,.5);color:#86EFAC}

/* ── Health strip ───────────────────────────────────────────── */
.health-strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:20px}
.health-tile{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow-sm);transition:box-shadow .15s;cursor:default;position:relative;overflow:hidden}
.health-tile:hover{box-shadow:var(--shadow-md)}
.health-tile::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--tile-accent,var(--border))}
.health-tile-icon{width:28px;height:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px;margin-bottom:8px;background:var(--tile-icon-bg,var(--soft))}
.health-tile-val{font-size:22px;font-weight:900;color:var(--t1);line-height:1;letter-spacing:-.02em}
.health-tile-val a{color:inherit;text-decoration:none}
.health-tile-val a:hover{color:var(--brand)}
.health-tile-lbl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--t3);margin-top:3px}
.health-tile-sub{font-size:10px;color:var(--t2);margin-top:4px;line-height:1.4}
.health-tile.ok-tile{--tile-accent:var(--ok);--tile-icon-bg:var(--ok-bg)}
.health-tile.warn-tile{--tile-accent:var(--warn);--tile-icon-bg:var(--warn-bg)}
.health-tile.danger-tile{--tile-accent:var(--danger);--tile-icon-bg:var(--danger-bg)}
.health-tile.info-tile{--tile-accent:var(--info);--tile-icon-bg:var(--info-bg)}
.health-tile.brand-tile{--tile-accent:var(--brand);--tile-icon-bg:var(--blue-surf)}

/* ── Attention board (3-col priority) ───────────────────────── */
.attn-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin-bottom:20px}
.attn-col{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:var(--shadow-sm)}
.attn-col-hd{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}
.attn-col-hd-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.attn-col-hd-label{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}
.attn-col-hd-count{margin-left:auto;font-size:11px;font-weight:700;border-radius:999px;padding:1px 8px}
.attn-item{padding:8px 16px;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:10px;font-size:12px;color:var(--t2);line-height:1.4}
.attn-item:last-child{border-bottom:none}
.attn-item-dot{width:5px;height:5px;border-radius:50%;flex-shrink:0;margin-top:5px}
.attn-item-text{flex:1}
.attn-empty{padding:14px 16px;font-size:12px;color:var(--t3);font-style:italic;text-align:center}

/* ── Compliance row ─────────────────────────────────────────── */
.compliance-bar{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 18px;margin-bottom:20px;box-shadow:var(--shadow-sm)}
.compliance-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.compliance-title{font-size:13px;font-weight:800;color:var(--t1)}
.compliance-stat{font-size:12px;font-weight:700}
.compliance-track{height:8px;background:var(--border);border-radius:4px;overflow:hidden;margin-bottom:10px}
.compliance-fill{height:100%;border-radius:4px;background:var(--ok);transition:width .5s}
.compliance-names{display:flex;flex-wrap:wrap;gap:6px}
.comp-pill{font-size:11px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap}
.comp-pill.done{background:var(--ok-bg);color:var(--ok);border:1px solid var(--ok-border)}
.comp-pill.pending{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-border)}

/* ── Section headers ────────────────────────────────────────── */
.sec-hd{font-size:14px;font-weight:800;color:var(--t1);margin-bottom:3px;display:flex;align-items:center;gap:8px}
.sec-sub{font-size:12px;color:var(--t2);margin-bottom:14px}
.sec-divider{border:none;border-top:1px solid var(--border);margin:22px 0}

/* ── Team accountability cards ──────────────────────────────── */
.team-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px;margin-bottom:24px}
.team-card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;box-shadow:var(--shadow-sm);transition:box-shadow .15s,transform .15s;display:block;text-decoration:none}
.team-card:hover{box-shadow:var(--shadow-md);transform:translateY(-2px)}
.team-card-hd{padding:14px 16px 12px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border)}
.team-avatar{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;color:#fff;flex-shrink:0}
.team-card-name{font-size:14px;font-weight:800;color:var(--t1);line-height:1.2}
.team-role-chip{font-size:10px;font-weight:700;border-radius:999px;padding:2px 9px;margin-top:3px;display:inline-block}
.team-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border)}
.team-metric{background:var(--card);padding:10px 8px;text-align:center}
.team-metric-val{font-size:18px;font-weight:900;color:var(--t1);line-height:1}
.team-metric-val.ok{color:var(--ok)}
.team-metric-val.warn{color:var(--warn)}
.team-metric-val.danger{color:var(--danger)}
.team-metric-val.info{color:var(--info)}
.team-metric-lbl{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--t3);margin-top:3px}
.rate-bar-wrap{padding:8px 14px 10px;border-top:1px solid var(--border)}
.rate-bar-track{height:5px;background:var(--border);border-radius:3px;overflow:hidden;margin-top:3px}
.rate-bar-fill{height:100%;border-radius:3px;transition:width .4s}
.rate-bar-labels{display:flex;justify-content:space-between;font-size:10px;color:var(--t3);margin-top:3px}
.team-standup-row{padding:10px 14px;border-top:1px solid var(--border);background:var(--soft)}
.team-standup-today{font-size:12px;color:var(--t2);line-height:1.5}
.team-blocker-row{padding:8px 14px;background:var(--danger-bg);border-top:1px solid var(--danger-border)}
.team-blocker-text{font-size:11px;color:var(--danger);font-weight:600;line-height:1.4}
.team-card-actions{padding:10px 14px;border-top:1px solid var(--border);display:flex;gap:8px}
.team-btn{flex:1;text-align:center;padding:7px 10px;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;text-decoration:none;border:1.5px solid var(--border);color:var(--t2);background:var(--card);transition:all .12s}
.team-btn:hover{border-color:var(--brand);color:var(--brand)}
.team-btn.primary{background:var(--brand);color:#fff;border-color:var(--brand)}
.team-btn.primary:hover{background:#1D4ED8}

/* ── JD Pipeline compact cards ─────────────────────────────── */
.jd-grid{display:grid;gap:12px;margin-bottom:20px}
.jd-card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:var(--shadow-sm)}
.jd-card-hd{padding:14px 16px;display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.jd-role-title{font-size:14px;font-weight:800;color:var(--t1);margin-bottom:2px}
.jd-meta{font-size:11px;color:var(--t3)}
.jd-meta span{margin-right:10px}
.jd-meta-badge{font-size:10px;font-weight:700;border-radius:4px;padding:2px 8px;margin-left:4px}
.jd-stage-row{display:flex;gap:1px;border-top:1px solid var(--border)}
.jd-stage{flex:1;padding:8px 6px;text-align:center;font-size:11px;font-weight:600;color:var(--t3);background:var(--soft)}
.jd-stage-count{font-size:16px;font-weight:900;color:var(--t1);display:block;margin-bottom:1px;line-height:1}
.jd-stage-lbl{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.jd-stage.active-stage .jd-stage-count{color:var(--brand)}
.jd-stage.placed-stage .jd-stage-count{color:var(--ok)}
.jd-expand-btn{width:100%;padding:9px;background:var(--soft);border:none;border-top:1px solid var(--border);font-size:12px;font-weight:600;color:var(--brand);cursor:pointer;text-align:center;transition:background .15s}
.jd-expand-btn:hover{background:var(--blue-surf)}
.jd-kanban-wrap{display:none;border-top:1px solid var(--border)}
.jd-kanban-wrap.open{display:block}
/* ── JD SLA + timeline ──────────────────────────────────────── */
.jd-sla-breach{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;background:var(--danger-bg);color:var(--danger);border:1px solid var(--danger-border);border-radius:6px;padding:3px 9px;white-space:nowrap}
.jd-sla-ok{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;background:var(--ok-bg);color:var(--ok);border:1px solid var(--ok-border);border-radius:6px;padding:3px 9px;white-space:nowrap}
.jd-sla-warn{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-border);border-radius:6px;padding:3px 9px;white-space:nowrap}
.jd-timeline{display:flex;gap:0;border-top:1px solid var(--border);background:var(--soft);overflow:hidden}
.jd-tl-item{flex:1;padding:7px 10px;border-right:1px solid var(--border);text-align:center}
.jd-tl-item:last-child{border-right:none}
.jd-tl-val{font-size:13px;font-weight:800;color:var(--t1);line-height:1}
.jd-tl-lbl{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--t3);margin-top:2px}
/* ── Candidate dwell badge ──────────────────────────────────── */
.cand-dwell{font-size:9px;font-weight:700;color:var(--t3);float:right;margin-top:2px}
.cand-dwell.warn{color:var(--warn)}
.cand-dwell.danger{color:var(--danger)}
/* Kanban */
.kanban-scroll{padding:12px 14px 10px;overflow-x:auto}
.kanban-board{display:flex;gap:10px;min-width:max-content}
.kanban-col{width:190px;flex-shrink:0}
.kanban-col-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;padding:0 2px}
.kanban-col-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
.kanban-col-count{font-size:10px;font-weight:700;border-radius:999px;padding:1px 7px;min-width:20px;text-align:center}
.kanban-col-body{min-height:50px;display:flex;flex-direction:column;gap:6px;border-radius:8px;padding:7px}
.cand-card{background:var(--card);border:1px solid var(--border);border-radius:7px;padding:8px 10px;cursor:pointer;transition:all .15s;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.cand-card:hover{box-shadow:0 3px 10px rgba(0,0,0,.1);transform:translateY(-1px)}
.cand-name{font-size:12px;font-weight:600;color:var(--t1);line-height:1.2}
.cand-score{font-size:10px;font-weight:700;border-radius:4px;padding:2px 6px;display:inline-block;margin-top:4px}
.cand-empty{font-size:11px;color:var(--t3);padding:8px;text-align:center;background:var(--soft);border:1px dashed var(--border);border-radius:7px;font-style:italic}
.col-sourcing .kanban-col-title{color:#64748B}.col-sourcing .kanban-col-count{background:#F1F5F9;color:#64748B}.col-sourcing .kanban-col-body{background:#F8FAFC}
.col-screening .kanban-col-title{color:var(--info)}.col-screening .kanban-col-count{background:var(--info-bg);color:var(--info)}.col-screening .kanban-col-body{background:#F0F9FF}
.col-panel .kanban-col-title{color:var(--indigo)}.col-panel .kanban-col-count{background:#EEF2FF;color:var(--indigo)}.col-panel .kanban-col-body{background:#F5F3FF}
.col-offer .kanban-col-title{color:var(--warn)}.col-offer .kanban-col-count{background:var(--warn-bg);color:var(--warn)}.col-offer .kanban-col-body{background:#FFFBEB}
.col-placed .kanban-col-title{color:var(--ok)}.col-placed .kanban-col-count{background:var(--ok-bg);color:var(--ok)}.col-placed .kanban-col-body{background:#ECFDF5}

/* ── Activity table ─────────────────────────────────────────── */
.activity-card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:var(--shadow-sm);margin-bottom:20px}

/* ── Standup form ───────────────────────────────────────────── */
.standup-form-area{padding:14px 18px;border-top:1px solid var(--border);background:var(--soft)}
.standup-form-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
@media(max-width:700px){.standup-form-grid{grid-template-columns:1fr}}
.standup-field label{font-size:11px;font-weight:700;color:var(--t2);text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:5px}
.standup-field textarea{width:100%;padding:8px 10px;border:1.5px solid var(--border);border-radius:8px;font-size:12px;resize:none;font-family:inherit;line-height:1.5;outline:none;transition:border .15s;box-sizing:border-box;background:var(--card)}
.standup-field textarea:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.standup-submit{background:var(--brand);color:#fff;border:none;border-radius:8px;padding:9px 22px;font-size:13px;font-weight:700;cursor:pointer;transition:background .15s}
.standup-submit:hover{background:#1D4ED8}
/* Past standups */
.past-update{border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:8px;background:var(--card)}
.past-update-date{font-size:11px;font-weight:700;color:var(--brand);margin-bottom:6px}
/* Stats strip (recruiter self-view) */
.stats-strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:12px;margin-bottom:20px}
.stat-box{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;text-align:center}
.stat-box-val{font-size:24px;font-weight:900;color:var(--t1);line-height:1}
.stat-box-lbl{font-size:10px;color:var(--t3);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-top:4px}
/* Sidebar */
.nav-item{color:#475569}
.nav-item:hover{background:#F1F5F9;color:var(--t1)}
.nav-item.active{background:#EEF2FF;color:#3730A3;font-weight:700;border-left:3px solid #4F46E5}
</style>"""


# ===========================================================================
# Standup Board
# ===========================================================================

@app.get("/standup", response_class=HTMLResponse)
def standup_page(request: Request, user: dict = Depends(get_current_user)):  # noqa: C901
    from datetime import date, datetime as _dt
    from database import (
        list_jds_for_recruiter, list_candidates_for_recruiter,
        get_recruiter_stats, get_team_leaderboard,
        list_standups_for_user, list_standups_for_team, get_standup_by_date,
        list_job_postings, list_candidates, list_jd_matches,
        list_users as _lu,
    )
    role = user["role"]
    today_str = date.today().isoformat()
    # Pre-build user lookup (id → dict) used for name resolution on JD cards
    _users_map: dict[str, dict] = {u["user_id"]: u for u in _lu()}

    # ---------- helpers ----------
    def _stage_from_scores(c: dict) -> str:
        if c.get("panel_score") is not None:
            return "Panel"
        if c.get("recruiter_score") is not None:
            return "Screening"
        return "Sourcing"

    def _day_chip(created_at: str, deadline: str | None) -> str:
        try:
            created = _dt.fromisoformat(created_at).date()
            day_x = (date.today() - created).days + 1
        except Exception:
            day_x = "?"
        if deadline:
            try:
                dl = date.fromisoformat(deadline[:10])
                remaining = (dl - date.today()).days
                color = "#DC2626" if remaining < 0 else ("#D97706" if remaining <= 3 else "#16A34A")
                return (f'<span style="background:{color}20;color:{color};border:1px solid {color}40;'
                        f'border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700">Day {day_x} &bull; {remaining}d left</span>')
            except Exception:
                pass
        return f'<span style="background:#F0F0FB;color:#353395;border:1px solid #E0E0F5;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700">Day {day_x}</span>'

    _STAGE_AV = {
        "Sourcing":  "linear-gradient(135deg,#5E6C84,#97A0AF)",
        "Screening": "linear-gradient(135deg,#0065FF,#4C9AFF)",
        "Panel":     "linear-gradient(135deg,#5243AA,#8777D9)",
        "Offer":     "linear-gradient(135deg,#FF8B00,#FFAB00)",
        "Placed":    "linear-gradient(135deg,#00875A,#36B37E)",
    }
    _STAGE_SC = {
        "Sourcing":  ("background:#EBECF0;color:#5E6C84",),
        "Screening": ("background:#DEEBFF;color:#0052CC",),
        "Panel":     ("background:#EAE6FF;color:#5243AA",),
        "Offer":     ("background:#FFF0B3;color:#FF8B00",),
        "Placed":    ("background:#E3FCEF;color:#00875A",),
    }

    _KANBAN_COL_LIMIT = 4  # max cards shown before "+N more"

    def _kanban_card(cid: str, name: str, score, stage: str) -> str:
        score_str = f"{score:.0f}" if (score is not None and score != "") else None
        sc_style = _STAGE_SC.get(stage, _STAGE_SC["Sourcing"])[0]
        score_chip = (f'<span class="cand-score" style="{sc_style}">{score_str}</span>' if score_str else "")
        href = f'/candidates/{cid}' if cid else '#'
        return (
            f'<a href="{href}" style="text-decoration:none;display:block">'
            f'<div class="cand-card">'
            f'<div class="cand-name" style="font-size:12px;font-weight:600;color:#172B4D;line-height:1.3">{escape(name)}</div>'
            f'{score_chip}'
            f'</div>'
            f'</a>'
        )

    def _kanban_col_body(items: list, stage: str, jd_id: str) -> str:
        visible = items[:_KANBAN_COL_LIMIT]
        overflow = len(items) - _KANBAN_COL_LIMIT
        cards = "".join(_kanban_card(
            c.get("candidate_id", ""), c.get("name", "?"),
            c.get("panel_score") or c.get("recruiter_score") or c.get("resume_score"),
            stage) for c in visible)
        more = ""
        if overflow > 0:
            more = (f'<a href="/candidates?jd_id={jd_id}" style="display:block;text-align:center;'
                    f'font-size:11px;color:#0052CC;font-weight:700;padding:6px 0;'
                    f'border-top:1px solid #E8EEF9;margin-top:4px;text-decoration:none">'
                    f'+{overflow} more &#8594;</a>')
        return cards + more

    def _standup_form(jd_id: str | None = None) -> str:
        jd_hidden = f'<input type="hidden" name="jd_id" value="{escape(jd_id or "")}">' if jd_id else ""
        return (
            f'<div class="standup-form-area">'
            f'<form method="POST" action="/standup/submit">'
            f'{jd_hidden}'
            f'<input type="hidden" name="date" value="{today_str}">'
            f'<div style="font-size:11px;font-weight:700;color:#6B778C;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px">Daily Standup</div>'
            f'<div class="standup-form-grid">'
            f'<div class="standup-field"><label>Done Today</label>'
            f'<textarea name="today" rows="3" placeholder="What did you accomplish today?"></textarea></div>'
            f'<div class="standup-field"><label>Blockers</label>'
            f'<textarea name="blockers" rows="3" placeholder="Any blockers?"></textarea></div>'
            f'<div class="standup-field"><label>Tomorrow\'s Priorities</label>'
            f'<textarea name="priorities" rows="3" placeholder="Top priorities for tomorrow?"></textarea></div>'
            f'</div>'
            f'<div style="margin-top:10px">'
            f'<button type="submit" class="standup-submit">Submit Standup</button>'
            f'</div>'
            f'</form>'
            f'</div>'
        )

    # ---------- RECRUITER VIEW ----------
    if role == "recruiter":
        jds = list_jds_for_recruiter(user["user_id"])
        candidates = list_candidates_for_recruiter(user["user_id"])
        cand_map = {c["candidate_id"]: c for c in candidates}
        stats = get_recruiter_stats(user["user_id"])
        today_update = get_standup_by_date(user["user_id"], today_str)
        past_updates = list_standups_for_user(user["user_id"], limit=10)

        # Build kanban columns per JD
        kanban_html = ""
        STAGES = ["Sourcing", "Screening", "Panel", "Offer", "Placed"]
        for jd in jds:
            jd_id = jd["jd_id"]
            title = jd.get("title") or "Untitled JD"
            company = jd.get("company") or ""
            deadline = jd.get("deadline")
            created_at = jd.get("created_at") or today_str
            matches = list_jd_matches(jd_id)
            # bucket by stage
            buckets: dict[str, list] = {s: [] for s in STAGES}
            for m in matches:
                cid = m["candidate_id"]
                c = cand_map.get(cid) or {"name": m.get("candidate_name") or cid}
                stage = _stage_from_scores(c)
                if stage in buckets:
                    buckets[stage].append(c)
            _STAGE_CSS = {"Sourcing": "col-sourcing", "Screening": "col-screening",
                          "Panel": "col-panel", "Offer": "col-offer", "Placed": "col-placed"}
            # Resolve assigned people
            sa_uid = jd.get("assigned_sales_agent")
            sa_name = _users_map.get(sa_uid, {}).get("full_name", "") if sa_uid else ""
            rec_uid2 = jd.get("assigned_recruiter")
            rec_name2 = _users_map.get(rec_uid2, {}).get("full_name", "") if rec_uid2 else ""

            _cand_empty = '<div class="cand-empty">No candidates</div>'
            cols_html = ""
            for stage in STAGES:
                col_body = _kanban_col_body(buckets[stage], stage, jd_id)
                count = len(buckets[stage])
                css_cls = _STAGE_CSS.get(stage, "col-sourcing")
                cols_html += (
                    f'<div class="kanban-col {css_cls}">'
                    f'<div class="kanban-col-hd">'
                    f'<span class="kanban-col-title">{stage}</span>'
                    f'<span class="kanban-col-count">{count}</span>'
                    f'</div>'
                    f'<div class="kanban-col-body">'
                    f'{col_body or _cand_empty}'
                    f'</div>'
                    f'</div>'
                )
            # Build people chips for header
            people_chips = ""
            if sa_name:
                people_chips += (f'<span style="background:#E3FCEF;color:#00875A;border-radius:4px;padding:2px 8px;'
                                 f'font-size:11px;font-weight:600;margin-right:4px">&#128100; Sales: {escape(sa_name)}</span>')
            if rec_name2:
                people_chips += (f'<span style="background:#DEEBFF;color:#0052CC;border-radius:4px;padding:2px 8px;'
                                 f'font-size:11px;font-weight:600">&#128196; Recruiter: {escape(rec_name2)}</span>')
            kanban_html += (
                f'<div class="jd-section">'
                f'<div class="jd-section-hd">'
                f'<div>'
                f'<div class="jd-title">{escape(title)} '
                f'<a href="/candidates?jd_id={jd_id}" style="font-size:11px;color:#0052CC;font-weight:600;margin-left:6px">View all candidates &#8594;</a>'
                f'</div>'
                f'<div class="jd-company" style="margin-top:4px">{escape(company)}&ensp;{people_chips}</div>'
                f'</div>'
                f'{_day_chip(created_at, deadline)}'
                f'</div>'
                f'<div class="kanban-scroll"><div class="kanban-board">{cols_html}</div></div>'
                f'{_standup_form(jd_id)}'
                f'</div>'
            )

        if not jds:
            kanban_html = '<div class="jd-section"><div style="padding:32px;text-align:center;color:#97A0AF;font-size:14px">No JDs assigned yet — ask your admin to assign one.</div></div>'

        # Past standups collapsible
        past_html = ""
        for s in past_updates:
            if s.get("date") == today_str:
                continue
            past_html += (
                f'<div class="past-update">'
                f'<div class="past-update-date">{s.get("date","")}'
                f'{" &bull; " + escape(s.get("jd_id","")) if s.get("jd_id") else ""}</div>'
                f'<div style="font-size:12px;color:#172B4D;line-height:1.6">'
                f'<span style="font-weight:700;color:#6B778C">Done:</span> {escape(s.get("today") or "—")}<br>'
                f'<span style="font-weight:700;color:#DE350B">Blockers:</span> {escape(s.get("blockers") or "None")}<br>'
                f'<span style="font-weight:700;color:#6B778C">Next:</span> {escape(s.get("priorities") or "—")}'
                f'</div></div>'
            )

        stats_html = (
            f'<div class="stats-strip">'
            f'<div class="stat-box"><div class="stat-box-val">{stats["total"]}</div><div class="stat-box-lbl">Sourced</div></div>'
            f'<div class="stat-box"><div class="stat-box-val">{stats["screened"]}</div><div class="stat-box-lbl">Screened</div></div>'
            f'<div class="stat-box"><div class="stat-box-val" style="color:#00875A">{stats["placed"]}</div><div class="stat-box-lbl">Placed</div></div>'
            f'</div>'
        )

        # Upcoming interviews strip (today + tomorrow)
        from database import list_upcoming_interviews as _lui
        _upcoming = _lui(days_ahead=1)
        if _upcoming:
            from datetime import date as _d
            _today_iso = _d.today().isoformat()
            def _iv_row(iv: dict) -> str:
                is_today = iv.get("interview_date") == _today_iso
                label_bg = "#FEF3C7" if is_today else "#EFF6FF"
                label_col = "#92400E" if is_today else "#1D4ED8"
                label_txt = "Today" if is_today else "Tomorrow"
                time_txt = iv.get("interview_time") or ""
                round_txt = iv.get("interview_round") or "Interview"
                return (
                    f'<a href="/candidates/{escape(iv["candidate_id"])}" style="text-decoration:none;flex-shrink:0">'
                    f'<div style="background:#fff;border:1px solid #E0E7FF;border-radius:10px;padding:10px 14px;'
                    f'min-width:160px;max-width:200px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px;margin-bottom:4px">'
                    f'<div style="font-size:12px;font-weight:700;color:#172B4D;line-height:1.3">{escape(iv.get("name") or iv["candidate_id"])}</div>'
                    f'<span style="background:{label_bg};color:{label_col};border-radius:4px;padding:1px 6px;'
                    f'font-size:10px;font-weight:700;white-space:nowrap">{label_txt}</span></div>'
                    f'<div style="font-size:11px;color:#5243AA;font-weight:600">{escape(round_txt)}</div>'
                    f'<div style="font-size:11px;color:#6B778C;margin-top:2px">&#128336; {escape(time_txt) if time_txt else "Time TBD"}</div>'
                    f'</div></a>'
                )
            _iv_cards = "".join(_iv_row(iv) for iv in _upcoming)
            _upcoming_strip = (
                f'<div style="margin-bottom:18px">'
                f'<div style="font-size:11px;font-weight:700;color:#6B778C;text-transform:uppercase;'
                f'letter-spacing:.06em;margin-bottom:8px">&#128197; Upcoming Interviews ({len(_upcoming)})</div>'
                f'<div style="display:flex;gap:10px;flex-wrap:wrap">{_iv_cards}</div>'
                f'</div>'
            )
        else:
            _upcoming_strip = ""

        content = (
            f'<div class="wrap">'
            f'<div style="margin-bottom:22px;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:10px">'
            f'<div><div class="kicker">Standup Board</div>'
            f'<h1 style="font-size:22px;font-weight:800;color:#172B4D;margin:0">Good day, {escape(user["full_name"].split()[0])}! 👋</h1>'
            f'<p style="color:#6B778C;font-size:13px;margin-top:4px">{today_str}</p></div>'
            f'</div>'
            f'{_upcoming_strip}'
            f'{stats_html}'
            f'<div class="section-hd">Your JD Pipeline</div>'
            f'<div class="section-sub">Drag candidates through stages as they progress</div>'
            f'{kanban_html}'
            + (f'<details style="margin-top:8px"><summary style="cursor:pointer;list-style:none;font-size:13px;font-weight:700;color:#0052CC;padding:10px 0">'
               f'&#9654; Past Standups ({len([s for s in past_updates if s.get("date") != today_str])})</summary>'
               f'<div style="margin-top:8px">{past_html or "<div style=\'color:#97A0AF;font-size:13px;padding:8px 0\'>No past standups yet.</div>"}</div></details>'
               if past_updates else '')
            + f'</div>'
        )

    # ---------- SALES EXECUTIVE VIEW ----------
    elif role == "sales_executive":
        from database import list_jds_for_sales_agent, list_jd_matches
        jds = list_jds_for_sales_agent(user["user_id"])
        all_cands_for_sa = list_candidates()
        cand_map_sa = {c["candidate_id"]: c for c in all_cands_for_sa}
        today_update_sa = get_standup_by_date(user["user_id"], today_str)
        past_updates_sa = list_standups_for_user(user["user_id"], limit=10)

        # Build kanban per assigned-sales JD
        kanban_html = ""
        STAGES = ["Sourcing", "Screening", "Panel", "Offer", "Placed"]
        for jd in jds:
            jd_id = jd["jd_id"]
            title = jd.get("title") or "Untitled JD"
            company = jd.get("company") or ""
            deadline = jd.get("deadline")
            created_at = jd.get("created_at") or today_str
            matches = list_jd_matches(jd_id)
            buckets: dict[str, list] = {s: [] for s in STAGES}
            for m in matches:
                cid = m["candidate_id"]
                c = cand_map_sa.get(cid) or {"name": m.get("candidate_name") or cid}
                stage = _stage_from_scores(c)
                if stage in buckets:
                    buckets[stage].append(c)
            _STAGE_CSS = {"Sourcing": "col-sourcing", "Screening": "col-screening",
                          "Panel": "col-panel", "Offer": "col-offer", "Placed": "col-placed"}
            rec_uid = jd.get("assigned_recruiter")
            rec_name = _users_map.get(rec_uid, {}).get("full_name", "") if rec_uid else ""
            people_chips = ""
            if rec_name:
                people_chips += (f'<span style="background:#DEEBFF;color:#0052CC;border-radius:4px;padding:2px 8px;'
                                 f'font-size:11px;font-weight:600">&#128196; Recruiter: {escape(rec_name)}</span>')
            cols_html = ""
            for stage in STAGES:
                col_body = _kanban_col_body(buckets[stage], stage, jd_id)
                count = len(buckets[stage])
                cols_html += (
                    f'<div class="kanban-col {_STAGE_CSS.get(stage, "col-sourcing")}">'
                    f'<div class="kanban-col-hd"><span class="kanban-col-title">{stage}</span>'
                    f'<span class="kanban-col-count">{count}</span></div>'
                    f'<div class="kanban-col-body">{col_body or "<div class=\"cand-empty\">No candidates</div>"}</div>'
                    f'</div>'
                )
            kanban_html += (
                f'<div class="jd-section">'
                f'<div class="jd-section-hd">'
                f'<div><div class="jd-title">{escape(title)}'
                f'<a href="/candidates?jd_id={jd_id}" style="font-size:11px;color:#0052CC;font-weight:600;margin-left:6px">View all &#8594;</a>'
                f'</div><div class="jd-company" style="margin-top:4px">{escape(company)}&ensp;{people_chips}</div></div>'
                f'{_day_chip(created_at, deadline)}'
                f'</div>'
                f'<div class="kanban-scroll"><div class="kanban-board">{cols_html}</div></div>'
                f'{_standup_form(jd_id)}'
                f'</div>'
            )
        if not jds:
            kanban_html = '<div class="jd-section"><div style="padding:32px;text-align:center;color:#97A0AF;font-size:14px">No JDs assigned yet — ask your sales head to assign one.</div></div>'

        past_html_sa = ""
        for s in past_updates_sa:
            if s.get("date") == today_str:
                continue
            past_html_sa += (
                f'<div class="past-update">'
                f'<div class="past-update-date">{s.get("date","")}</div>'
                f'<div style="font-size:12px;color:#172B4D;line-height:1.6">'
                f'<span style="font-weight:700;color:#6B778C">Done:</span> {escape(s.get("today") or "—")}<br>'
                f'<span style="font-weight:700;color:#DE350B">Blockers:</span> {escape(s.get("blockers") or "None")}<br>'
                f'<span style="font-weight:700;color:#6B778C">Next:</span> {escape(s.get("priorities") or "—")}'
                f'</div></div>'
            )
        content = (
            f'<div class="wrap">'
            f'<div style="margin-bottom:22px">'
            f'<div class="kicker">Sales Executive &bull; Standup Board</div>'
            f'<h1 style="font-size:22px;font-weight:800;color:#172B4D;margin:0">Good day, {escape(user["full_name"].split()[0])}! 👋</h1>'
            f'<p style="color:#6B778C;font-size:13px;margin-top:4px">{today_str}</p></div>'
            f'<div class="section-hd">Your Client JDs</div>'
            f'<div class="section-sub">JDs you are managing from the sales side</div>'
            f'{kanban_html}'
            + (f'<details style="margin-top:8px"><summary style="cursor:pointer;list-style:none;font-size:13px;font-weight:700;color:#0052CC;padding:10px 0">'
               f'&#9654; Past Standups ({len([s for s in past_updates_sa if s.get("date") != today_str])})</summary>'
               f'<div style="margin-top:8px">{past_html_sa or "<div style=\'color:#97A0AF;font-size:13px;padding:8px 0\'>No past standups yet.</div>"}</div></details>'
               if past_updates_sa else '')
            + f'</div>'
        )

    # ---------- PANEL VIEW ----------
    elif role == "panel":
        # Panel sees only candidates assigned to them
        all_cands = list_candidates()
        mine = [c for c in all_cands if c.get("assigned_recruiter") == user["user_id"]]
        rows_html = ""
        for c in mine:
            cid = c["candidate_id"]
            name = c.get("name") or cid
            jd = c.get("role_family") or "—"
            score = c.get("panel_score") or c.get("recruiter_score") or "—"
            rows_html += (
                f'<tr><td><a href="/candidates/{cid}" style="color:#353395;font-weight:600">{escape(name)}</a></td>'
                f'<td>{escape(jd)}</td>'
                f'<td>{score}</td>'
                f'<td><a href="/panel-screen/{cid}" class="btn" style="font-size:12px;padding:6px 12px">Interview</a></td></tr>'
            )
        content = (
            f'<div class="wrap">'
            f'<div style="margin-bottom:20px"><div class="kicker">Panel Board</div>'
            f'<h1 style="font-size:24px;font-weight:800;color:#262626">Your Assigned Candidates</h1></div>'
            f'<div class="card">'
            f'<table class="table"><thead><tr><th>Candidate</th><th>Role</th><th>Score</th><th>Action</th></tr></thead>'
            f'<tbody>{rows_html or "<tr><td colspan=4 style=\'color:#62748E;text-align:center;padding:20px\'>No candidates assigned yet.</td></tr>"}</tbody></table>'
            f'</div></div>'
        )

    # ---------- SUPER ADMIN / SALES HEAD / RECRUITER HEAD VIEW ----------
    elif role in ("super_admin", "sales_head", "recruiter_head"):
        from database import list_users as _list_users, list_standups_for_roles, list_jds_for_sales_agent as _ljsa
        _all_users = _list_users()
        _all_users_map = {u["user_id"]: u for u in _all_users}

        # Role → visual config
        _ROLE_LABELS = {
            "super_admin": "Head", "sales_head": "Sales Head",
            "recruiter_head": "Lead Recruiter", "recruiter": "Recruiter",
            "sales_executive": "Sales Executive", "panel": "Panel",
        }
        _ROLE_COLORS = {
            "super_admin": "#172B4D", "sales_head": "#00875A",
            "recruiter_head": "#5243AA", "recruiter": "#FF8B00",
            "sales_executive": "#00BFA5", "panel": "#0065FF",
        }
        _ROLE_AVATAR = {
            "super_admin": "linear-gradient(135deg,#172B4D,#344563)",
            "sales_head": "linear-gradient(135deg,#00875A,#36B37E)",
            "recruiter_head": "linear-gradient(135deg,#5243AA,#8777D9)",
            "recruiter": "linear-gradient(135deg,#FF8B00,#FFAB00)",
            "sales_executive": "linear-gradient(135deg,#00BFA5,#1DE9B6)",
            "panel": "linear-gradient(135deg,#0065FF,#4C9AFF)",
        }

        # Determine which teams this role manages
        if role == "sales_head":
            rec_team_roles: tuple = ()
            sales_team_roles = ("sales_executive",)
            view_label = "Sales Head"
            show_admin_btn = False
        elif role == "recruiter_head":
            rec_team_roles = ("recruiter",)
            sales_team_roles = ()
            view_label = "Lead Recruiter"
            show_admin_btn = False
        else:  # super_admin
            rec_team_roles = ("recruiter", "recruiter_head")
            sales_team_roles = ("sales_head", "sales_executive")
            view_label = "Tvarah Head"
            show_admin_btn = True

        # Build recruiting leaderboard
        rec_board = get_team_leaderboard(roles=rec_team_roles) if rec_team_roles else []
        all_jds = list_job_postings(include_closed=False)

        # Determine which standup activity to show
        if role == "sales_head":
            standup_roles_filter = ("sales_head", "sales_executive")
        elif role == "recruiter_head":
            standup_roles_filter = ("recruiter_head", "recruiter")
        else:
            standup_roles_filter = ("super_admin", "sales_head", "sales_executive", "recruiter_head", "recruiter")

        team_updates = list_standups_for_roles(standup_roles_filter, limit=50)

        # ---- Helper: build a team member card ----
        def _team_card(r: dict, is_rec: bool = True) -> str:
            uid = r["user_id"]
            r_role = r.get("role", "recruiter")
            av_bg = _ROLE_AVATAR.get(r_role, "linear-gradient(135deg,#62748E,#94A3B8)")
            rc = _ROLE_COLORS.get(r_role, "#62748E")
            role_lbl = _ROLE_LABELS.get(r_role, r_role.replace("_", " ").title())
            initials = "".join(p[0].upper() for p in r["full_name"].split()[:2])
            role_chip = (f'<span style="background:{rc}15;color:{rc};border:1px solid {rc}30;'
                         f'border-radius:99px;padding:1px 8px;font-size:10px;font-weight:700">{role_lbl}</span>')
            if is_rec:
                rate = r.get("placement_rate", 0)
                rate_color = "#00875A" if rate >= 50 else ("#FF8B00" if rate >= 20 else "#DE350B")
                bar_w = min(int(rate), 100)
                stats_html = (
                    f'<div class="team-stats-row">'
                    f'<div class="team-stat"><div class="team-stat-val">{r["jds_active"]}</div><div class="team-stat-lbl">JDs</div></div>'
                    f'<div class="team-stat"><div class="team-stat-val">{r["candidates_total"]}</div><div class="team-stat-lbl">Sourced</div></div>'
                    f'<div class="team-stat"><div class="team-stat-val" style="color:#00875A">{r["candidates_placed"]}</div><div class="team-stat-lbl">Placed</div></div>'
                    f'</div>'
                    f'<div class="team-progress">'
                    f'<div class="team-progress-bar"><div class="team-progress-fill" style="width:{bar_w}%;background:{rate_color}"></div></div>'
                    f'<div class="team-progress-label"><span>Placement Rate</span><span style="color:{rate_color};font-weight:700">{rate}%</span></div>'
                    f'</div>'
                )
            else:
                jd_count = len(_ljsa(uid)) if is_rec is False else 0
                stats_html = (
                    f'<div class="team-stats-row">'
                    f'<div class="team-stat"><div class="team-stat-val">{jd_count}</div><div class="team-stat-lbl">JDs</div></div>'
                    f'</div>'
                )
            return (
                f'<a href="/standup/recruiter/{uid}" class="team-card">'
                f'<div class="team-card-top">'
                f'<div class="team-avatar" style="background:{av_bg}">{initials}</div>'
                f'<div style="min-width:0"><div class="team-card-name">{escape(r["full_name"])}</div>'
                f'<div style="margin-top:3px">{role_chip}</div></div>'
                f'</div>'
                f'{stats_html}'
                f'</a>'
            )

        # ---- Build recruiting team cards ----
        rec_cards_html = "".join(_team_card(r, is_rec=True) for r in rec_board)

        # ---- Build sales team cards ----
        sales_members = [u for u in _all_users if u["role"] in sales_team_roles and u.get("is_active")]
        sales_cards_html = "".join(_team_card(u, is_rec=False) for u in sales_members)

        # ---- Recruiting leaderboard table ----
        lb_rows = ""
        rank_medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for idx, r in enumerate(rec_board, 1):
            rate = r["placement_rate"]
            rate_color = "#00875A" if rate >= 50 else ("#FF8B00" if rate >= 20 else "#DE350B")
            bar_w = min(int(rate), 100)
            initials = "".join(p[0].upper() for p in r["full_name"].split()[:2])
            medal = rank_medals.get(idx, f"#{idx}")
            r_role = r.get("role", "recruiter")
            av_bg = _ROLE_AVATAR.get(r_role, "linear-gradient(135deg,#FF8B00,#FFAB00)")
            role_lbl = _ROLE_LABELS.get(r_role, r_role)
            lb_rows += (
                f'<tr style="cursor:pointer" onclick="location.href=\'/standup/recruiter/{r["user_id"]}\'">'
                f'<td style="text-align:center;font-size:14px">{medal}</td>'
                f'<td><div style="display:flex;align-items:center;gap:9px">'
                f'<div style="width:32px;height:32px;border-radius:8px;background:{av_bg};display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:800;flex-shrink:0">{initials}</div>'
                f'<div><a href="/standup/recruiter/{r["user_id"]}" style="color:#0052CC;font-weight:700;text-decoration:none;font-size:13px">{escape(r["full_name"])}</a>'
                f'<div style="font-size:10px;color:#97A0AF">{role_lbl}</div></div>'
                f'</div></td>'
                f'<td style="color:#6B778C;font-size:12px">{escape(r["email"])}</td>'
                f'<td style="text-align:center;font-weight:600">{r["jds_active"]}</td>'
                f'<td style="text-align:center;font-weight:600">{r["candidates_total"]}</td>'
                f'<td style="text-align:center;font-weight:600">{r["candidates_screened"]}</td>'
                f'<td style="text-align:center;font-weight:800;color:#00875A;font-size:15px">{r["candidates_placed"]}</td>'
                f'<td><div style="display:flex;align-items:center;gap:7px">'
                f'<div style="flex:1;height:7px;background:#EBECF0;border-radius:4px;overflow:hidden">'
                f'<div style="width:{bar_w}%;height:100%;background:{rate_color};border-radius:4px;transition:width .3s"></div></div>'
                f'<span style="color:{rate_color};font-weight:800;font-size:13px;white-space:nowrap">{rate}%</span>'
                f'</div></td>'
                f'<td><a href="/standup/recruiter/{r["user_id"]}" class="btn-sec" style="font-size:11px;padding:4px 10px;white-space:nowrap">View &#8594;</a></td>'
                f'</tr>'
            )

        # ---- All-JDs kanban ----
        # Filter JDs by what the role should see
        if role == "sales_head":
            jds_to_show = [j for j in all_jds if j.get("assigned_sales_agent") in
                           {u["user_id"] for u in sales_members} | {user["user_id"]}][:8]
        elif role == "recruiter_head":
            rec_uids = {r["user_id"] for r in rec_board}
            jds_to_show = [j for j in all_jds if j.get("assigned_recruiter") in rec_uids][:8]
        else:
            jds_to_show = all_jds[:8]

        STAGES = ["Sourcing", "Screening", "Panel", "Offer", "Placed"]
        all_cands = list_candidates()
        cand_map = {c["candidate_id"]: c for c in all_cands}

        jd_kanban = ""
        now_iso = date.today().isoformat()
        for jd in jds_to_show:
            jd_id = jd["jd_id"]
            title = jd.get("title") or "Untitled"
            company = jd.get("company") or ""
            deadline = jd.get("deadline")
            created_at = jd.get("created_at") or now_iso
            rec_uid = jd.get("assigned_recruiter")
            rec_info = _users_map.get(rec_uid, {}) if rec_uid else {}
            rec_name = rec_info.get("full_name", "")
            sa_uid2 = jd.get("assigned_sales_agent")
            sa_info = _users_map.get(sa_uid2, {}) if sa_uid2 else {}
            sa_name2 = sa_info.get("full_name", "")

            matches = list_jd_matches(jd_id)
            buckets2: dict[str, list] = {s: [] for s in STAGES}
            for m in matches:
                cid = m["candidate_id"]
                c = cand_map.get(cid) or {"name": m.get("candidate_name") or cid, "candidate_id": cid}
                stage = _stage_from_scores(c)
                if stage in buckets2:
                    buckets2[stage].append(c)

            is_overdue = False
            if deadline:
                try:
                    is_overdue = date.fromisoformat(deadline[:10]) < date.today()
                except Exception:
                    pass

            jd_cols_html = ""
            _STAGE_CSS2 = {"Sourcing": "col-sourcing", "Screening": "col-screening",
                           "Panel": "col-panel", "Offer": "col-offer", "Placed": "col-placed"}
            for stage in STAGES:
                col_body2 = _kanban_col_body(buckets2[stage], stage, jd_id)
                count = len(buckets2[stage])
                jd_cols_html += (
                    f'<div class="kanban-col {_STAGE_CSS2.get(stage, "col-sourcing")}">'
                    f'<div class="kanban-col-hd"><span class="kanban-col-title">{stage}</span>'
                    f'<span class="kanban-col-count">{count}</span></div>'
                    f'<div class="kanban-col-body">{col_body2 or "<div class=\"cand-empty\">No candidates</div>"}</div>'
                    f'</div>'
                )

            overdue_badge = ('<span style="background:#FFEBE6;color:#DE350B;border-radius:4px;padding:2px 7px;'
                             'font-size:10px;font-weight:700;margin-left:8px">OVERDUE</span>' if is_overdue else "")
            people_chips2 = ""
            if sa_name2:
                people_chips2 += (f'<span style="background:#E3FCEF;color:#00875A;border-radius:4px;padding:2px 7px;'
                                  f'font-size:10px;font-weight:600;margin-right:4px">&#128100; Sales: {escape(sa_name2)}</span>')
            if rec_name:
                people_chips2 += (f'<span style="background:#DEEBFF;color:#0052CC;border-radius:4px;padding:2px 7px;'
                                  f'font-size:10px;font-weight:600">&#128196; Recruiter: {escape(rec_name)}</span>')
            if not sa_name2 and not rec_name:
                people_chips2 = '<span style="background:#EBECF0;color:#5E6C84;border-radius:4px;padding:2px 7px;font-size:10px">Unassigned</span>'

            # ---- JD performance summary strip ----
            total_in_jd = sum(len(buckets2[s]) for s in STAGES)
            placed_in_jd = len(buckets2["Placed"])
            panel_in_jd  = len(buckets2["Panel"]) + placed_in_jd
            screen_in_jd = len(buckets2["Screening"]) + panel_in_jd
            place_rate_jd = f"{round(placed_in_jd/total_in_jd*100)}%" if total_in_jd else "—"

            def _person_pill(uid: str | None, label: str, bg: str, fg: str) -> str:
                if not uid:
                    return f'<span style="background:#EBECF0;color:#97A0AF;border-radius:4px;padding:3px 10px;font-size:11px">{label}: Unassigned</span>'
                u_info = _users_map.get(uid, {})
                u_name = u_info.get("full_name", uid)
                u_role = _ROLE_LABELS.get(u_info.get("role", ""), "")
                return (f'<div style="background:{bg};border-radius:6px;padding:5px 10px;min-width:120px">'
                        f'<div style="font-size:10px;color:{fg};opacity:.75;font-weight:600;text-transform:uppercase;letter-spacing:.05em">{label}</div>'
                        f'<div style="font-size:12px;font-weight:700;color:{fg}">{escape(u_name)}</div>'
                        f'<div style="font-size:10px;color:{fg};opacity:.7">{u_role}</div>'
                        f'</div>')

            rec_pill  = _person_pill(rec_uid, "Recruiter", "#EFF6FF", "#1E40AF")
            sa_pill2  = _person_pill(sa_uid2, "Sales", "#ECFDF5", "#065F46")

            jd_summary = (
                f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;'
                f'padding:10px 14px;background:#F8F9FB;border-top:1px solid #E8EEF9;border-radius:0 0 10px 10px">'
                f'{rec_pill}'
                f'{sa_pill2}'
                f'<div style="flex:1"></div>'
                f'<div style="display:flex;gap:16px;font-size:12px">'
                f'<div style="text-align:center"><div style="font-weight:800;font-size:18px;color:#172B4D">{total_in_jd}</div><div style="color:#97A0AF;font-size:10px;text-transform:uppercase">Total</div></div>'
                f'<div style="text-align:center"><div style="font-weight:800;font-size:18px;color:#0052CC">{screen_in_jd}</div><div style="color:#97A0AF;font-size:10px;text-transform:uppercase">Screened</div></div>'
                f'<div style="text-align:center"><div style="font-weight:800;font-size:18px;color:#00875A">{placed_in_jd}</div><div style="color:#97A0AF;font-size:10px;text-transform:uppercase">Placed</div></div>'
                f'<div style="text-align:center"><div style="font-weight:800;font-size:18px;color:{"#16A34A" if placed_in_jd>0 else "#D97706"}">{place_rate_jd}</div><div style="color:#97A0AF;font-size:10px;text-transform:uppercase">Rate</div></div>'
                f'</div>'
                f'</div>'
            )

            jd_kanban += (
                f'<div class="jd-section" style="padding:0;overflow:hidden;{"border-color:#FFBDAD;" if is_overdue else ""}">'
                f'<div class="jd-section-hd" style="padding:12px 16px">'
                f'<div><div class="jd-title">{escape(title)}{overdue_badge}'
                f'<a href="/candidates?jd_id={jd_id}" style="font-size:11px;color:#0052CC;font-weight:600;margin-left:6px">View all &#8594;</a></div>'
                f'<div class="jd-company" style="margin-top:4px">{escape(company)}</div></div>'
                f'{_day_chip(created_at, deadline)}'
                f'</div>'
                f'<div class="kanban-scroll" style="padding:0 12px 12px"><div class="kanban-board">{jd_cols_html}</div></div>'
                f'{jd_summary}'
                f'</div>'
            )

        # ---- Recent standup activity ----
        standup_rows = ""
        for s in team_updates[:15]:
            s_role = s.get("role", "")
            role_lbl_s = _ROLE_LABELS.get(s_role, s_role.replace("_", " ").title()) if s_role else ""
            role_chip_s = (f'<div style="font-size:10px;color:{_ROLE_COLORS.get(s_role,"#62748E")};font-weight:600">{role_lbl_s}</div>'
                           if role_lbl_s else "")
            standup_rows += (
                f'<tr>'
                f'<td><div style="font-weight:600">{escape(s.get("full_name") or s.get("user_id",""))}</div>{role_chip_s}</td>'
                f'<td style="color:#6B778C;font-size:12px">{s.get("date","")}</td>'
                f'<td style="font-size:12px">{escape((s.get("today") or "—")[:80])}</td>'
                f'<td style="font-size:12px;color:#DE350B">{escape((s.get("blockers") or "—")[:60])}</td>'
                f'</tr>'
            )

        # ---- Compute KPI aggregates ----
        from datetime import date as _date
        _month_start = _date.today().replace(day=1).isoformat()
        all_cands_all = list_candidates()
        all_jds_count = len(all_jds)
        pipeline_total = len(all_cands_all)
        total_placed = sum(r["candidates_placed"] for r in rec_board)
        total_sourced = sum(r["candidates_total"] for r in rec_board)
        total_jds_rec = sum(r["jds_active"] for r in rec_board)
        overall_rate = round(total_placed / total_sourced * 100, 1) if total_sourced else 0
        total_team_size = len(rec_board) + len(sales_members)

        # Today's standups
        today_standups = [s for s in team_updates if s.get("date") == today_str]
        blockers_today = [s for s in today_standups if (s.get("blockers") or "").strip() and s.get("blockers").strip().lower() not in ("none", "nil", "no blockers", "-", "\u2014")]
        blockers_count = len(blockers_today)
        followups_due = len([s for s in today_standups if (s.get("priorities") or "").strip()])

        # Overdue / unassigned JDs for Priority Attention
        overdue_jds = []
        unassigned_jds = []
        for jd in all_jds:
            dl = jd.get("deadline")
            if dl:
                try:
                    if _date.fromisoformat(dl[:10]) < _date.today():
                        overdue_jds.append(jd)
                except Exception:
                    pass
            if not jd.get("assigned_recruiter") or not jd.get("assigned_sales_agent"):
                unassigned_jds.append(jd)

        # ---- KPI cards ----
        def _kpi(icon, val, label, sub="", accent="var(--brand)", link=None):
            v_html = f'<a href="{link}">{val}</a>' if link else str(val)
            return (
                f'<div class="kpi-card" style="--kpi-accent:{accent}">'
                f'<div class="kpi-icon">{icon}</div>'
                f'<div class="kpi-val">{v_html}</div>'
                f'<div class="kpi-lbl">{label}</div>'
                + (f'<div class="kpi-sub">{sub}</div>' if sub else '')
                + f'</div>'
            )

        kpi_html = (
            f'<div class="kpi-grid">'
            + _kpi("\U0001f465", total_team_size, "Team Members", f"{len(rec_board)} recruiting \u00b7 {len(sales_members)} sales", "var(--brand)")
            + _kpi("\U0001f4cb", all_jds_count, "Active JDs", f"{len(overdue_jds)} overdue" if overdue_jds else "All on track",
                   "var(--warn)" if overdue_jds else "var(--ok)", "/jobs")
            + _kpi("\U0001f464", pipeline_total, "Pipeline Candidates", f"Across all JDs", "var(--info)", "/candidates")
            + _kpi("\U0001f4cc", followups_due, "Today's Follow-ups",
                   "From standup priorities", "var(--brand2)" if followups_due else "var(--t3)")
            + _kpi("\U0001f6a7", blockers_count, "Open Blockers",
                   "Reported today", "var(--danger)" if blockers_count else "var(--ok)")
            + _kpi("\U0001f3c6", total_placed, "Monthly Placements",
                   f"{overall_rate}% placement rate", "var(--ok)" if total_placed else "var(--t3)")
            + f'</div>'
        )

        # ---- Filters bar ----
        _all_users_for_filter = list(_all_users_map.values())
        owner_opts = '<option value="">All Owners</option>' + "".join(
            f'<option value="{u["user_id"]}">{escape(u["full_name"])}</option>'
            for u in _all_users_for_filter if u.get("is_active") and u["role"] in ("recruiter","recruiter_head","sales_head","sales_executive")
        )
        filters_html = (
            f'<div class="filter-bar">'
            f'<span class="filter-label">Filter:</span>'
            f'<input type="date" id="fDate" value="{today_str}" onchange="applyFilters()">'
            f'<select id="fTeam" onchange="applyFilters()">'
            f'<option value="">All Teams</option>'
            f'<option value="recruiting">Recruiting</option>'
            f'<option value="sales">Sales</option>'
            f'</select>'
            f'<select id="fRole" onchange="applyFilters()">'
            f'<option value="">All Roles</option>'
            f'<option value="recruiter_head">Lead Recruiter</option>'
            f'<option value="recruiter">Recruiter</option>'
            f'<option value="sales_head">Sales Head</option>'
            f'<option value="sales_executive">Sales Executive</option>'
            f'</select>'
            f'<select id="fOwner" onchange="applyFilters()">{owner_opts}</select>'
            f'<select id="fStatus" onchange="applyFilters()">'
            f'<option value="">All Status</option>'
            f'<option value="submitted">Standup Submitted</option>'
            f'<option value="pending">Standup Pending</option>'
            f'<option value="blocked">Has Blockers</option>'
            f'</select>'
            f'<button onclick="resetFilters()" style="margin-left:auto;border:1px solid var(--border);background:var(--pg);border-radius:8px;padding:6px 12px;font-size:12px;font-weight:600;color:var(--t2);cursor:pointer">Reset</button>'
            f'</div>'
        )

        # ---- Today's Standup Summary ----
        focus_items = "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;color:var(--t1)">'
            f'<span style="font-weight:700;color:var(--brand)">{escape(s.get("full_name",""))}</span>: '
            f'{escape((s.get("today") or "\u2014")[:100])}</div>'
            for s in today_standups[:6]
        ) or f'<div class="summary-empty">No standups submitted yet today</div>'

        blocker_items = "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid var(--danger-border);font-size:12px;color:var(--danger)">'
            f'<span style="font-weight:700">{escape(s.get("full_name",""))}</span>: '
            f'{escape((s.get("blockers") or "")[:100])}</div>'
            for s in blockers_today[:5]
        ) or f'<div class="summary-empty" style="color:var(--ok)">\u2713 No blockers reported</div>'

        priority_items = "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;color:var(--t2)">'
            f'<span style="font-weight:700;color:var(--brand2)">{escape(s.get("full_name",""))}</span>: '
            f'{escape((s.get("priorities") or "\u2014")[:100])}</div>'
            for s in today_standups if (s.get("priorities") or "").strip()
        ) or f'<div class="summary-empty">No priorities logged yet</div>'

        standup_summary_html = (
            f'<div class="summary-grid">'
            f'<div class="summary-card info"><div class="summary-card-title">\U0001f4cc Today\'s Focus</div>'
            f'<div class="summary-card-body">{focus_items}</div></div>'
            f'<div class="summary-card warn"><div class="summary-card-title">\U0001f4c5 Tomorrow\'s Priorities</div>'
            f'<div class="summary-card-body">{priority_items}</div></div>'
            f'<div class="summary-card {"danger" if blockers_count else "ok"}"><div class="summary-card-title">\U0001f6a7 Blockers ({blockers_count})</div>'
            f'<div class="summary-card-body">{blocker_items}</div></div>'
            f'<div class="summary-card"><div class="summary-card-title">\u2705 Submitted Today</div>'
            f'<div class="summary-card-body"><div class="kpi-val" style="font-size:36px;color:var(--ok)">{len(today_standups)}</div>'
            f'<div style="font-size:12px;color:var(--t2);margin-top:4px">of {total_team_size} team members</div></div></div>'
            f'</div>'
        )

        # ---- Priority Attention Required ----
        priority_rows = ""
        for jd in overdue_jds[:4]:
            dl = jd.get("deadline","")[:10]
            priority_rows += (
                f'<div class="priority-row">'
                f'<span class="priority-badge red">OVERDUE</span>'
                f'<span style="font-size:13px;font-weight:600;color:var(--t1);flex:1">{escape(jd.get("title",""))}</span>'
                f'<span style="font-size:11px;color:var(--danger)">Due: {dl}</span>'
                f'<a href="/candidates?jd_id={jd["jd_id"]}" style="font-size:11px;color:var(--brand);font-weight:600;text-decoration:none;margin-left:10px">View \u2192</a>'
                f'</div>'
            )
        for jd in unassigned_jds[:3]:
            missing = ("recruiter" if not jd.get("assigned_recruiter") else "") + (" & sales" if not jd.get("assigned_sales_agent") else "")
            priority_rows += (
                f'<div class="priority-row">'
                f'<span class="priority-badge orange">UNASSIGNED</span>'
                f'<span style="font-size:13px;font-weight:600;color:var(--t1);flex:1">{escape(jd.get("title",""))}</span>'
                f'<span style="font-size:11px;color:var(--warn)">Missing: {missing}</span>'
                f'<a href="/admin" style="font-size:11px;color:var(--brand);font-weight:600;text-decoration:none;margin-left:10px">Assign \u2192</a>'
                f'</div>'
            )
        for s in blockers_today[:3]:
            priority_rows += (
                f'<div class="priority-row">'
                f'<span class="priority-badge red">BLOCKER</span>'
                f'<span style="font-size:13px;font-weight:600;color:var(--t1);flex:1">{escape(s.get("full_name",""))}</span>'
                f'<span style="font-size:11px;color:var(--danger);flex:2">{escape((s.get("blockers") or "")[:80])}</span>'
                f'<a href="/standup/recruiter/{s.get("user_id","")}" style="font-size:11px;color:var(--brand);font-weight:600;text-decoration:none;margin-left:10px">Details \u2192</a>'
                f'</div>'
            )

        priority_section = ""
        if priority_rows:
            priority_section = (
                f'<div class="priority-section">'
                f'<div class="priority-title">\u26a0\ufe0f Priority Attention Required ({len(overdue_jds)+len(unassigned_jds)+blockers_count})</div>'
                f'{priority_rows}'
                f'</div>'
            )

        # ---- Build team accountability cards ----
        def _build_recruiter_card(r: dict) -> str:
            uid = r["user_id"]
            r_role = r.get("role", "recruiter")
            av_bg = _ROLE_AVATAR.get(r_role, "linear-gradient(135deg,#1D4ED8,#4F46E5)")
            rc = _ROLE_COLORS.get(r_role, "#1D4ED8")
            role_lbl = _ROLE_LABELS.get(r_role, r_role.replace("_"," ").title())
            initials = "".join(p[0].upper() for p in r["full_name"].split()[:2])
            rate = r.get("placement_rate", 0)
            rate_color = "var(--ok)" if rate >= 50 else ("var(--warn)" if rate >= 20 else "var(--danger)")

            recs_cands = [c for c in all_cands_all if c.get("assigned_recruiter") == uid]
            shortlisted = len([c for c in recs_cands if (c.get("recruiter_score") or 0) >= 60])
            interviews = len([c for c in recs_cands if c.get("panel_score") is not None])

            my_today = next((s for s in today_standups if s.get("user_id") == uid), None)
            today_txt = (my_today.get("today") or "")[:100] if my_today else ""
            blocker_txt = (my_today.get("blockers") or "").strip() if my_today else ""
            blocker_txt = "" if blocker_txt.lower() in ("none","nil","no blockers","-","\u2014","") else blocker_txt[:100]
            standup_badge = (
                '<span style="font-size:10px;font-weight:700;background:var(--ok-bg);color:var(--ok);border:1px solid var(--ok-border);border-radius:999px;padding:1px 8px">&#10003; Submitted</span>'
                if my_today else
                '<span style="font-size:10px;font-weight:700;background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-border);border-radius:999px;padding:1px 8px">Pending</span>'
            )
            empty_jd = r["jds_active"] == 0

            return (
                f'<div class="team-card" data-team="recruiting" data-role="{r_role}" data-owner="{uid}" '
                f'data-status="{"submitted" if my_today else "pending"}{"_blocked" if blocker_txt else ""}">'
                f'<div class="team-card-hd">'
                f'<div class="team-avatar" style="background:{av_bg}">{initials}</div>'
                f'<div style="flex:1;min-width:0">'
                f'<div class="team-card-name">{escape(r["full_name"])}</div>'
                f'<span class="team-role-chip" style="background:{rc}15;color:{rc};border:1px solid {rc}30">{role_lbl}</span>'
                f'</div>'
                f'<div style="text-align:right">{standup_badge}</div>'
                f'</div>'
                f'<div class="team-metrics">'
                f'<div class="team-metric"><div class="team-metric-val {"warn" if empty_jd else ""}">{r["jds_active"]}</div><div class="team-metric-lbl">Active JDs</div></div>'
                f'<div class="team-metric"><div class="team-metric-val">{r["candidates_total"]}</div><div class="team-metric-lbl">Sourced</div></div>'
                f'<div class="team-metric"><div class="team-metric-val info">{interviews}</div><div class="team-metric-lbl">Interviews</div></div>'
                f'<div class="team-metric"><div class="team-metric-val">{shortlisted}</div><div class="team-metric-lbl">Shortlisted</div></div>'
                f'<div class="team-metric"><div class="team-metric-val {"ok" if r["candidates_placed"]>0 else ""}">{r["candidates_placed"]}</div><div class="team-metric-lbl">Placed</div></div>'
                f'<div class="team-metric"><div class="team-metric-val {"ok" if rate>=50 else ("warn" if rate>0 else "")}">{rate}%</div><div class="team-metric-lbl">Rate</div></div>'
                f'</div>'
                + f'<div class="rate-bar-wrap">'
                + f'<div class="rate-bar-labels"><span>Placement Rate</span><span style="color:{rate_color};font-weight:700">{rate}%</span></div>'
                + f'<div class="rate-bar-track"><div class="rate-bar-fill" style="width:{min(int(rate),100)}%;background:{rate_color}"></div></div>'
                + f'</div>'
                + (f'<div style="padding:7px 14px;background:var(--warn-bg);border-top:1px solid var(--warn-border)"><div style="font-size:11px;color:var(--warn);font-weight:600">No active JDs assigned</div></div>'
                   if empty_jd else '')
                + f'<div class="team-standup-row">'
                + f'<div style="font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Today\'s Focus</div>'
                + f'<div class="team-standup-today">{escape(today_txt) if today_txt else "<i style=\'color:var(--t3)\'>No update submitted yet</i>"}</div>'
                + f'</div>'
                + (f'<div class="team-blocker-row"><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;color:var(--danger)">Blocker</div>'
                   f'<div class="team-blocker-text">{escape(blocker_txt)}</div></div>' if blocker_txt else '')
                + f'<div class="team-card-actions">'
                + f'<a href="/standup/recruiter/{uid}" class="team-btn">View Details</a>'
                + f'<a href="/standup/recruiter/{uid}" class="team-btn primary">+ Update</a>'
                + f'</div>'
                + f'</div>'
            )

        def _build_sales_card(u: dict) -> str:
            uid = u["user_id"]
            u_role = u.get("role", "sales_executive")
            av_bg = _ROLE_AVATAR.get(u_role, "linear-gradient(135deg,#00875A,#36B37E)")
            rc = _ROLE_COLORS.get(u_role, "#00875A")
            role_lbl = _ROLE_LABELS.get(u_role, u_role.replace("_"," ").title())
            initials = "".join(p[0].upper() for p in u["full_name"].split()[:2])

            jds_owned = _ljsa(uid)
            active_clients = len(set(j.get("company","") for j in jds_owned if j.get("company")))
            open_jds = len(jds_owned)
            closures = 0
            for jd in jds_owned:
                matches = list_jd_matches(jd["jd_id"])
                cids = [m["candidate_id"] for m in matches]
                closures += len([c for c in all_cands_all if c["candidate_id"] in cids and c.get("panel_score") is not None])

            my_today = next((s for s in today_standups if s.get("user_id") == uid), None)
            today_txt = (my_today.get("today") or "")[:100] if my_today else ""
            blocker_txt = (my_today.get("blockers") or "").strip() if my_today else ""
            blocker_txt = "" if blocker_txt.lower() in ("none","nil","no blockers","-","\u2014","") else blocker_txt[:100]
            standup_badge = (
                '<span style="font-size:10px;font-weight:700;background:var(--ok-bg);color:var(--ok);border:1px solid var(--ok-border);border-radius:999px;padding:1px 8px">&#10003; Submitted</span>'
                if my_today else
                '<span style="font-size:10px;font-weight:700;background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-border);border-radius:999px;padding:1px 8px">Pending</span>'
            )
            empty_jd = open_jds == 0

            return (
                f'<div class="team-card" data-team="sales" data-role="{u_role}" data-owner="{uid}" '
                f'data-status="{"submitted" if my_today else "pending"}{"_blocked" if blocker_txt else ""}">'
                f'<div class="team-card-hd">'
                f'<div class="team-avatar" style="background:{av_bg}">{initials}</div>'
                f'<div style="flex:1;min-width:0">'
                f'<div class="team-card-name">{escape(u["full_name"])}</div>'
                f'<span class="team-role-chip" style="background:{rc}15;color:{rc};border:1px solid {rc}30">{role_lbl}</span>'
                f'</div>'
                f'<div style="text-align:right">{standup_badge}</div>'
                f'</div>'
                f'<div class="team-metrics">'
                f'<div class="team-metric"><div class="team-metric-val {"warn" if active_clients==0 else "info"}">{active_clients}</div><div class="team-metric-lbl">Clients</div></div>'
                f'<div class="team-metric"><div class="team-metric-val {"warn" if empty_jd else ""}">{open_jds}</div><div class="team-metric-lbl">Open JDs</div></div>'
                f'<div class="team-metric"><div class="team-metric-val ok">{closures}</div><div class="team-metric-lbl">Interviews</div></div>'
                f'</div>'
                + (f'<div style="padding:7px 14px;background:var(--warn-bg);border-top:1px solid var(--warn-border)"><div style="font-size:11px;color:var(--warn);font-weight:600">No client JDs assigned yet</div></div>'
                   if empty_jd else '')
                + f'<div class="team-standup-row">'
                + f'<div style="font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Today\'s Focus</div>'
                + f'<div class="team-standup-today">{escape(today_txt) if today_txt else "<i style=\'color:var(--t3)\'>No update submitted yet</i>"}</div>'
                + f'</div>'
                + (f'<div class="team-blocker-row"><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;color:var(--danger)">Blocker</div>'
                   f'<div class="team-blocker-text">{escape(blocker_txt)}</div></div>' if blocker_txt else '')
                + f'<div class="team-card-actions">'
                + f'<a href="/standup/recruiter/{uid}" class="team-btn">View Details</a>'
                + f'<a href="/standup/recruiter/{uid}" class="team-btn primary">+ Update</a>'
                + f'</div>'
                + f'</div>'
            )

        rec_cards_html = "".join(_build_recruiter_card(r) for r in rec_board)
        sales_cards_html = "".join(_build_sales_card(u) for u in sales_members)

        # ---- Manager's standup form ----
        manager_standup = ""
        if role in ("sales_head", "recruiter_head"):
            manager_standup = (
                f'<div style="background:var(--card);border:1px solid var(--border);border-radius:12px;margin-bottom:20px;box-shadow:var(--shadow-sm)">'
                f'<div style="padding:14px 18px;border-bottom:1px solid var(--border)">'
                f'<div style="font-size:13px;font-weight:700;color:var(--t1)">Your Daily Standup</div>'
                f'<div style="font-size:12px;color:var(--t2)">{today_str}</div>'
                f'</div>'
                f'{_standup_form()}'
                f'</div>'
            )

        # ---- Team section blocks ----
        rec_section = ""
        if rec_team_roles:
            empty_rec = '<div style="background:var(--soft);border:1px dashed var(--border);border-radius:10px;padding:28px;text-align:center;color:var(--t3);font-size:13px">No recruiting team members yet. <a href="/admin" style="color:var(--brand);font-weight:600">Add one &rarr;</a></div>'
            rec_section = (
                f'<div class="sec-hd">Recruiting Team <span id="recGridBadge" style="font-size:11px;font-weight:600;color:var(--t3);margin-left:8px"></span></div>'
                f'<div class="sec-sub">Sourcing, screening, and placement performance</div>'
                f'<div class="team-grid" id="recGrid">'
                + (rec_cards_html or empty_rec)
                + f'</div>'
            )

        sales_section = ""
        if sales_team_roles:
            empty_sales = '<div style="background:var(--soft);border:1px dashed var(--border);border-radius:10px;padding:28px;text-align:center;color:var(--t3);font-size:13px">No sales team members yet. <a href="/admin" style="color:var(--brand);font-weight:600">Add one &rarr;</a></div>'
            sales_section = (
                f'<div class="sec-hd">Sales Team <span id="salesGridBadge" style="font-size:11px;font-weight:600;color:var(--t3);margin-left:8px"></span></div>'
                f'<div class="sec-sub">Client JD owners and business development</div>'
                f'<div class="team-grid" id="salesGrid">'
                + (sales_cards_html or empty_sales)
                + f'</div>'
            )

        # ---- Standup activity table ----
        standup_rows2 = ""
        for s in team_updates[:15]:
            s_role = s.get("role", "")
            role_lbl_s = _ROLE_LABELS.get(s_role, s_role.replace("_", " ").title()) if s_role else ""
            rc_s = _ROLE_COLORS.get(s_role, "var(--t3)")
            role_chip_s = (f'<div style="font-size:10px;color:{rc_s};font-weight:600">{role_lbl_s}</div>' if role_lbl_s else "")
            b_txt = (s.get("blockers") or "\u2014")[:60]
            b_color = "var(--danger)" if b_txt not in ("\u2014", "None", "none", "-") else "var(--t3)"
            standup_rows2 += (
                f'<tr>'
                f'<td><div style="font-weight:600;color:var(--t1)">{escape(s.get("full_name") or s.get("user_id",""))}</div>{role_chip_s}</td>'
                f'<td style="color:var(--t2);font-size:12px">{s.get("date","")}</td>'
                f'<td style="font-size:12px;color:var(--t2)">{escape((s.get("today") or "\u2014")[:80])}</td>'
                f'<td style="font-size:12px;color:{b_color};font-weight:{"600" if b_color=="var(--danger)" else "400"}">{escape(b_txt)}</td>'
                f'</tr>'
            )

        # ---- Leaderboard table ----
        lb_rows = ""
        rank_medals = {1: "1st", 2: "2nd", 3: "3rd"}
        for idx, r in enumerate(rec_board, 1):
            rate = r["placement_rate"]
            rate_color = "var(--ok)" if rate >= 50 else ("var(--warn)" if rate >= 20 else "var(--danger)")
            bar_w = min(int(rate), 100)
            initials = "".join(p[0].upper() for p in r["full_name"].split()[:2])
            medal = rank_medals.get(idx, f"#{idx}")
            r_role = r.get("role", "recruiter")
            av_bg = _ROLE_AVATAR.get(r_role, "linear-gradient(135deg,#FF8B00,#FFAB00)")
            role_lbl = _ROLE_LABELS.get(r_role, r_role)
            lb_rows += (
                f'<tr style="cursor:pointer" onclick="location.href=\'/standup/recruiter/{r["user_id"]}\'">'
                f'<td style="text-align:center;font-size:11px;font-weight:700;color:var(--t3)">{medal}</td>'
                f'<td><div style="display:flex;align-items:center;gap:9px">'
                f'<div style="width:30px;height:30px;border-radius:8px;background:{av_bg};display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:800;flex-shrink:0">{initials}</div>'
                f'<div><a href="/standup/recruiter/{r["user_id"]}" style="color:var(--brand);font-weight:700;text-decoration:none;font-size:13px">{escape(r["full_name"])}</a>'
                f'<div style="font-size:10px;color:var(--t3)">{role_lbl}</div></div>'
                f'</div></td>'
                f'<td style="text-align:center;font-weight:600">{r["jds_active"]}</td>'
                f'<td style="text-align:center;font-weight:600">{r["candidates_total"]}</td>'
                f'<td style="text-align:center;font-weight:800;color:var(--ok)">{r["candidates_placed"]}</td>'
                f'<td><div style="display:flex;align-items:center;gap:6px">'
                f'<div style="flex:1;height:5px;background:var(--border);border-radius:3px;overflow:hidden">'
                f'<div style="width:{bar_w}%;height:100%;background:{rate_color};border-radius:3px"></div></div>'
                f'<span style="color:{rate_color};font-weight:800;font-size:12px;white-space:nowrap">{rate}%</span>'
                f'</div></td>'
                f'</tr>'
            )

        # ---- Today's Brief pill status ----
        brief_ok = blockers_count == 0 and len(overdue_jds) == 0
        brief_status_pill = (
            f'<span class="brief-pill ok">All clear</span>'
            if brief_ok else
            (f'<span class="brief-pill danger">{blockers_count} blocker{"s" if blockers_count!=1 else ""}</span>' if blockers_count else '') +
            (f'<span class="brief-pill warn">{len(overdue_jds)} overdue JD{"s" if len(overdue_jds)!=1 else ""}</span>' if overdue_jds else '') +
            (f'<span class="brief-pill warn">{len(unassigned_jds)} unassigned JD{"s" if len(unassigned_jds)!=1 else ""}</span>' if unassigned_jds else '')
        )
        submitted_count = len(today_standups)
        total_team_count = total_team_size
        brief_text = (
            f'<strong>{all_jds_count}</strong> active JDs &nbsp;&bull;&nbsp; '
            f'<strong>{pipeline_total}</strong> candidates &nbsp;&bull;&nbsp; '
            f'<strong>{submitted_count}</strong> standups submitted &nbsp;&bull;&nbsp; '
            f'<strong>{len(unassigned_jds)}</strong> JDs need owner assignment'
        )

        # ---- Compliance section ----
        all_team_members = [u for u in _all_users if u.get("role") in (*rec_team_roles, *sales_team_roles) and u.get("is_active")]
        submitted_ids = {s.get("user_id") for s in today_standups}
        compliance_pct = round(submitted_count / len(all_team_members) * 100) if all_team_members else 0
        compliance_pills = ""
        for m in all_team_members:
            if m["user_id"] in submitted_ids:
                compliance_pills += f'<span class="comp-pill done">&#10003; {escape(m["full_name"])}</span>'
            else:
                compliance_pills += f'<span class="comp-pill pending">{escape(m["full_name"])}</span>'

        # ---- Attention board (3 columns) ----
        def _attn_col(label, color, items, badge_class):
            dot_html = f'<div class="attn-col-hd-dot" style="background:{color}"></div>'
            count_html = f'<span class="attn-col-hd-count" style="background:{color}20;color:{color}">{len(items)}</span>'
            hd = (f'<div class="attn-col-hd">{dot_html}'
                  f'<span class="attn-col-hd-label" style="color:{color}">{label}</span>'
                  f'{count_html}</div>')
            body = ""
            for item_text, sub in items:
                body += (f'<div class="attn-item">'
                         f'<div class="attn-item-dot" style="background:{color}"></div>'
                         f'<div class="attn-item-text"><div style="color:var(--t1);font-weight:600">{item_text}</div>'
                         f'{"<div style=\'font-size:11px;color:var(--t3)\'>" + sub + "</div>" if sub else ""}</div>'
                         f'</div>')
            if not items:
                body = '<div class="attn-empty">None — all good</div>'
            return f'<div class="attn-col">{hd}{body}</div>'

        critical_items = [(escape(s.get("full_name", s["user_id"])), escape((s.get("blockers") or "")[:50])) for s in blockers_today]
        needs_action_items = [(f'{escape(jd.get("role","JD"))} &mdash; {escape(jd.get("company","") or "No company")}', "Unassigned recruiter or sales agent") for jd in unassigned_jds[:5]]
        # Ageing candidates: sourced >14 days without progress
        ageing = []
        for c in all_cands_all:
            if c.get("recruiter_score") is None and c.get("panel_score") is None:
                ca = c.get("created_at", "")
                if ca:
                    try:
                        from datetime import date as _d2, datetime as _dt2
                        ca_date = _d2.fromisoformat(ca[:10])
                        if (_d2.today() - ca_date).days > 14:
                            ageing.append(c)
                    except Exception:
                        pass
        watchlist_items = [(escape(c.get("name") or c.get("candidate_id","?")), f'Sourced {((_d2.today() - _d2.fromisoformat(c["created_at"][:10])).days if c.get("created_at") else 0)}d ago — no screening') for c in ageing[:5]]

        attn_html = (
            f'<div class="attn-grid">'
            + _attn_col("Critical", "var(--danger)", critical_items, "red")
            + _attn_col("Needs Action", "var(--warn)", needs_action_items, "orange")
            + _attn_col("Watchlist", "#64748B", watchlist_items, "grey")
            + f'</div>'
        )

        # ---- Quick filter JS ----
        filter_js = """
<script>
function qfFilter(btn, key){
  document.querySelectorAll('.qf-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');

  // ── Team member cards ─────────────────────────────────────────
  document.querySelectorAll('.team-card').forEach(function(card){
    var ds = card.dataset.status || '';
    var show = true;
    if(key === 'pending' && ds.indexOf('submitted') === 0) show = false;
    if(key === 'blocked' && ds.indexOf('blocked') === -1) show = false;
    card.style.display = show ? '' : 'none';
    // Dim non-matching cards instead of hiding, for "all" reset
    card.style.opacity = show ? '' : '0.25';
    card.style.pointerEvents = show ? '' : 'none';
    if(key === 'all'){ card.style.display=''; card.style.opacity=''; card.style.pointerEvents=''; }
  });

  // ── JD pipeline cards ─────────────────────────────────────────
  // For "blocked": highlight JDs whose recruiter has a blocker; dim others
  // For "pending" / "all": reset all JD cards
  document.querySelectorAll('.jd-card').forEach(function(card){
    var hasBlocker = card.dataset.hasBlocker === '1';
    card.style.outline = '';
    card.style.outlineOffset = '';
    card.style.opacity = '';
    card.style.display = '';
    if(key === 'blocked'){
      if(hasBlocker){
        card.style.outline = '2px solid var(--danger)';
        card.style.outlineOffset = '2px';
        // auto-open kanban for blocker JDs
        var kw = card.querySelector('.jd-kanban-wrap');
        var kb = card.querySelector('.jd-expand-btn');
        if(kw && !kw.classList.contains('open')){
          kw.classList.add('open');
          if(kb) kb.textContent = 'Collapse Kanban';
        }
      } else {
        card.style.opacity = '0.35';
      }
    }
  });

  // ── Section labels: show count badge of visible cards ─────────
  var recVisible = 0, salesVisible = 0;
  document.querySelectorAll('#recGrid .team-card').forEach(function(c){
    if(c.style.display !== 'none') recVisible++;
  });
  document.querySelectorAll('#salesGrid .team-card').forEach(function(c){
    if(c.style.display !== 'none') salesVisible++;
  });
  var rb = document.getElementById('recGridBadge');
  var sb = document.getElementById('salesGridBadge');
  if(rb) rb.textContent = recVisible > 0 ? recVisible + ' shown' : 'none match';
  if(sb) sb.textContent = salesVisible > 0 ? salesVisible + ' shown' : 'none match';
}

function toggleKanban(id){
  var el = document.getElementById('kb_'+id);
  var btn = document.getElementById('kbbtn_'+id);
  if(el.classList.contains('open')){
    el.classList.remove('open');
    btn.textContent = 'Expand Kanban';
  } else {
    el.classList.add('open');
    btn.textContent = 'Collapse Kanban';
  }
}

function assignRecruiter(jid){
  var sel = document.getElementById('rec_sel_'+jid);
  if(!sel || !sel.value) return;
  var form = document.createElement('form');
  form.method = 'POST'; form.action = '/standup/assign-jd';
  var f1 = document.createElement('input'); f1.type='hidden'; f1.name='jd_id'; f1.value=jid; form.appendChild(f1);
  var f2 = document.createElement('input'); f2.type='hidden'; f2.name='recruiter_id'; f2.value=sel.value; form.appendChild(f2);
  document.body.appendChild(form); form.submit();
}
</script>
"""

        # ---- JD compact cards ----
        from datetime import date as _dc
        jd_compact = ""
        for jd in all_jds:
            jid = jd["jd_id"]
            jd_role = escape(jd.get("role") or "Untitled Role")
            company = escape(jd.get("company") or "")

            # Assigned people
            rec_uid = jd.get("assigned_recruiter")
            sales_uid = jd.get("assigned_sales_agent")
            rec_name = escape((_all_users_map.get(rec_uid) or {}).get("full_name", "")) if rec_uid else ""
            sales_name = escape((_all_users_map.get(sales_uid) or {}).get("full_name", "")) if sales_uid else ""
            assigned_html = ""
            if rec_name:
                assigned_html += f'<span style="font-size:10px;font-weight:600;background:#EFF6FF;color:var(--brand);border:1px solid #BFDBFE;border-radius:4px;padding:1px 7px;margin-right:4px">R: {rec_name}</span>'
            if sales_name:
                assigned_html += f'<span style="font-size:10px;font-weight:600;background:#ECFDF5;color:var(--ok);border:1px solid #A7F3D0;border-radius:4px;padding:1px 7px">S: {sales_name}</span>'
            # Inline recruiter assign dropdown (super_admin only, when no recruiter set)
            inline_assign_html = ""
            if not rec_uid and role == "super_admin":
                recruiter_opts = "".join(
                    f'<option value="{r["user_id"]}">{escape(r["full_name"])}</option>'
                    for r in rec_board
                )
                inline_assign_html = (
                    f'<div style="display:flex;align-items:center;gap:6px;margin-top:4px">'
                    f'<select id="rec_sel_{jid}" style="font-size:11px;padding:4px 8px;border:1.5px solid var(--warn-border);border-radius:6px;color:var(--t1);background:var(--warn-bg);outline:none;cursor:pointer">'
                    f'<option value="">Assign recruiter…</option>{recruiter_opts}'
                    f'</select>'
                    f'<button onclick="assignRecruiter(\'{jid}\')" style="padding:4px 12px;background:var(--brand);color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:700;cursor:pointer">Assign</button>'
                    f'</div>'
                )
            if not assigned_html and not inline_assign_html:
                assigned_html = '<span style="font-size:10px;font-weight:700;background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-border);border-radius:4px;padding:1px 7px">Unassigned</span>'

            # Days open
            created = jd.get("created_at", "")
            days_open = 0
            jd_created_date = None
            if created:
                try:
                    jd_created_date = _dc.fromisoformat(created[:10])
                    days_open = (_dc.today() - jd_created_date).days
                except Exception:
                    pass
            day_color = "var(--danger)" if days_open > 30 else ("var(--warn)" if days_open > 14 else "var(--t2)")

            # Stage counts + candidate dwell times
            matches = list_jd_matches(jid)
            cids = {m["candidate_id"] for m in matches}
            jd_cands = [c for c in all_cands_all if c["candidate_id"] in cids]

            sourcing_cands = [c for c in jd_cands if c.get("recruiter_score") is None and c.get("panel_score") is None]
            screen_cands   = [c for c in jd_cands if c.get("recruiter_score") is not None and c.get("panel_score") is None]
            panel_cands    = [c for c in jd_cands if c.get("panel_score") is not None and not c.get("offer_extended")]
            offer_cands    = [c for c in jd_cands if c.get("offer_extended") and not c.get("placed")]
            placed_cands   = [c for c in jd_cands if c.get("placed")]
            n_sourcing, n_screen, n_panel, n_offer, n_placed = (
                len(sourcing_cands), len(screen_cands), len(panel_cands), len(offer_cands), len(placed_cands)
            )
            total_cands = len(jd_cands)

            # ── 48-hour SLA check ──────────────────────────────────────
            # SLA = recruiter must screen at least 1 candidate within 48h of JD upload
            sla_met = n_screen > 0 or n_panel > 0 or n_offer > 0 or n_placed > 0
            sla_hours = days_open * 24
            if not rec_uid:
                sla_badge = '<span class="jd-sla-warn">&#9654; No recruiter assigned</span>'
            elif sla_met:
                sla_badge = '<span class="jd-sla-ok">&#10003; SLA Met</span>'
            elif sla_hours < 48:
                sla_badge = f'<span class="jd-sla-warn">&#9679; SLA: {48 - sla_hours:.0f}h left</span>'
            else:
                sla_badge = f'<span class="jd-sla-breach">&#9888; SLA Breach: {days_open}d no screening</span>'

            # ── JD timeline strip ──────────────────────────────────────
            # Time to first match (first candidate added to this JD)
            first_match_days = "—"
            avg_screen_days = "—"
            avg_panel_days = "—"
            if jd_cands and jd_created_date:
                cand_dates = []
                for c in jd_cands:
                    ca = c.get("created_at", "")
                    if ca:
                        try:
                            cand_dates.append(_dc.fromisoformat(ca[:10]))
                        except Exception:
                            pass
                if cand_dates:
                    first_match_days = (min(cand_dates) - jd_created_date).days

            # Avg dwell in sourcing (days since candidate created, for those still in sourcing)
            sourcing_dwells = []
            for c in sourcing_cands:
                ca = c.get("created_at", "")
                if ca:
                    try:
                        sourcing_dwells.append((_dc.today() - _dc.fromisoformat(ca[:10])).days)
                    except Exception:
                        pass
            avg_sourcing = round(sum(sourcing_dwells) / len(sourcing_dwells)) if sourcing_dwells else "—"

            timeline_html = (
                f'<div class="jd-timeline">'
                f'<div class="jd-tl-item"><div class="jd-tl-val" style="color:{day_color}">Day {days_open}</div><div class="jd-tl-lbl">JD Age</div></div>'
                f'<div class="jd-tl-item"><div class="jd-tl-val">{first_match_days if isinstance(first_match_days,str) else "Day " + str(first_match_days)}</div><div class="jd-tl-lbl">First Match</div></div>'
                f'<div class="jd-tl-item"><div class="jd-tl-val">{f"{avg_sourcing}d" if avg_sourcing != "—" else "—"}</div><div class="jd-tl-lbl">Avg in Sourcing</div></div>'
                f'<div class="jd-tl-item"><div class="jd-tl-val" style="color:{"var(--ok)" if n_placed>0 else "var(--t3)"}">{n_placed}</div><div class="jd-tl-lbl">Placed</div></div>'
                f'</div>'
            )

            # ── Kanban tiles with dwell time ───────────────────────────
            def _ktile(c, stage_label=""):
                sc = c.get("panel_score") or c.get("recruiter_score") or 0
                sc_col = "#16A34A" if sc >= 70 else ("#D97706" if sc >= 50 else "#DC2626")
                # Dwell: days since candidate record created (best proxy for stage entry)
                dwell = 0
                ca = c.get("created_at", "")
                if ca:
                    try:
                        dwell = (_dc.today() - _dc.fromisoformat(ca[:10])).days
                    except Exception:
                        pass
                dwell_cls = "danger" if dwell > 14 else ("warn" if dwell > 7 else "")
                dwell_txt = f'{dwell}d' if dwell > 0 else ""
                return (
                    f'<div class="cand-card" onclick="location.href=\'/candidate/{c["candidate_id"]}\'">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
                    f'<div class="cand-name">{escape(c.get("name") or c.get("candidate_id","?"))}</div>'
                    f'<span class="cand-dwell {dwell_cls}">{dwell_txt}</span>'
                    f'</div>'
                    f'<span class="cand-score" style="background:{sc_col}20;color:{sc_col}">{sc}</span>'
                    f'</div>'
                )

            def _kcol(title, css_class, stage_cands, stage):
                tiles = "".join(_ktile(c, title) for c in stage_cands[:4])
                overflow = len(stage_cands) - 4
                overflow_link = (
                    f'<a href="/candidates?jd={jid}&stage={stage}" style="font-size:11px;color:var(--brand);font-weight:600;text-align:center;display:block;padding:4px 0">+{overflow} more</a>'
                    if overflow > 0 else ''
                )
                empty = '<div class="cand-empty">Empty</div>' if not stage_cands else ''
                # Avg dwell for this column
                dwells = []
                for c in stage_cands:
                    ca = c.get("created_at", "")
                    if ca:
                        try:
                            dwells.append((_dc.today() - _dc.fromisoformat(ca[:10])).days)
                        except Exception:
                            pass
                avg_d = f'avg {round(sum(dwells)/len(dwells))}d' if dwells else ""
                avg_color = "var(--danger)" if (dwells and sum(dwells)/len(dwells) > 14) else ("var(--warn)" if (dwells and sum(dwells)/len(dwells) > 7) else "var(--t3)")
                avg_html = f'<span style="font-size:9px;color:{avg_color};font-weight:600">{avg_d}</span>' if avg_d else ''
                return (
                    f'<div class="kanban-col {css_class}">'
                    f'<div class="kanban-col-hd">'
                    f'<span class="kanban-col-title">{title}</span>'
                    f'<div style="display:flex;align-items:center;gap:5px">{avg_html}<span class="kanban-col-count">{len(stage_cands)}</span></div>'
                    f'</div>'
                    f'<div class="kanban-col-body">{tiles}{overflow_link}{empty}</div>'
                    f'</div>'
                )

            kanban_body = (
                f'<div class="kanban-scroll"><div class="kanban-board">'
                + _kcol("Sourcing", "col-sourcing", sourcing_cands, "sourcing")
                + _kcol("Screening", "col-screening", screen_cands, "screening")
                + _kcol("Panel", "col-panel", panel_cands, "panel")
                + _kcol("Offer", "col-offer", offer_cands, "offer")
                + _kcol("Placed", "col-placed", placed_cands, "placed")
                + f'</div></div>'
            )

            # Data attrs for JS filters
            blocker_uids = {s.get("user_id") for s in blockers_today}
            jd_has_blocker = "1" if (rec_uid and rec_uid in blocker_uids) else "0"
            jd_unassigned = "1" if not rec_uid else "0"

            jd_compact += (
                f'<div class="jd-card" data-has-blocker="{jd_has_blocker}" data-unassigned="{jd_unassigned}">'
                # Header row
                f'<div class="jd-card-hd">'
                f'<div>'
                f'<div class="jd-role-title">{jd_role}</div>'
                f'<div class="jd-meta" style="margin-top:3px">'
                f'{"<span>" + company + "</span>" if company else ""}'
                f'<span>{total_cands} candidates</span>'
                f'</div>'
                f'</div>'
                f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:5px">'
                f'{sla_badge}'
                f'{assigned_html}'
                f'{inline_assign_html}'
                f'</div>'
                f'</div>'
                # Timeline strip
                + timeline_html
                # Stage bar
                + f'<div class="jd-stage-row">'
                + f'<div class="jd-stage"><span class="jd-stage-count">{n_sourcing}</span><span class="jd-stage-lbl">Sourcing</span></div>'
                + f'<div class="jd-stage active-stage"><span class="jd-stage-count">{n_screen}</span><span class="jd-stage-lbl">Screening</span></div>'
                + f'<div class="jd-stage active-stage"><span class="jd-stage-count">{n_panel}</span><span class="jd-stage-lbl">Panel</span></div>'
                + f'<div class="jd-stage"><span class="jd-stage-count">{n_offer}</span><span class="jd-stage-lbl">Offer</span></div>'
                + f'<div class="jd-stage placed-stage"><span class="jd-stage-count">{n_placed}</span><span class="jd-stage-lbl">Placed</span></div>'
                + f'</div>'
                # Expand kanban
                + f'<button class="jd-expand-btn" id="kbbtn_{jid}" onclick="toggleKanban(\'{jid}\')">Expand Kanban</button>'
                + f'<div class="jd-kanban-wrap" id="kb_{jid}">{kanban_body}</div>'
                + f'</div>'
            )

        if not jd_compact:
            jd_compact = '<div style="background:var(--soft);border:1px dashed var(--border);border-radius:10px;padding:32px;text-align:center;color:var(--t3);font-size:13px">No active JDs. <a href="/jobs" style="color:var(--brand);font-weight:600">Create one &rarr;</a></div>'

        # ---- Health tiles ----
        def _htile(icon, val, label, sub, css_class, link=None):
            v_html = f'<a href="{link}">{val}</a>' if link else str(val)
            return (
                f'<div class="health-tile {css_class}">'
                f'<div class="health-tile-icon">{icon}</div>'
                f'<div class="health-tile-val">{v_html}</div>'
                f'<div class="health-tile-lbl">{label}</div>'
                + (f'<div class="health-tile-sub">{sub}</div>' if sub else '')
                + f'</div>'
            )
        health_status_class = "ok-tile" if (not blockers_count and not overdue_jds and not unassigned_jds) else ("danger-tile" if blockers_count else "warn-tile")
        health_status_text = "Healthy" if health_status_class == "ok-tile" else ("Blockers Active" if blockers_count else "Needs Action")
        health_html = (
            f'<div class="health-strip">'
            + _htile("&#9679;", health_status_text, "Hiring Health", f"{blockers_count} blockers, {len(overdue_jds)} overdue", health_status_class)
            + _htile("&#9632;", all_jds_count, "Active JDs", f"{len(overdue_jds)} overdue" if overdue_jds else "On track", "warn-tile" if overdue_jds else "brand-tile", "/jobs")
            + _htile("&#9650;", pipeline_total, "Pipeline", f"Across all JDs", "info-tile", "/candidates")
            + _htile("&#9679;", blockers_count, "Blockers", "Reported today", "danger-tile" if blockers_count else "ok-tile")
            + _htile("&#10003;", f"{submitted_count}/{total_team_count}", "Standups", f"{compliance_pct}% compliance", "ok-tile" if compliance_pct == 100 else ("warn-tile" if compliance_pct >= 50 else "danger-tile"))
            + f'</div>'
        )

        page_title = "Tvarah Hiring Command Center"
        role_kicker = _ROLE_LABELS.get(role, role.replace("_", " ").title())

        # Pre-compute compliance color to avoid f-string nesting issues
        _comp_color = "var(--ok)" if compliance_pct == 100 else ("var(--warn)" if compliance_pct >= 50 else "var(--danger)")

        content = (
            f'<div class="wrap">'
            # ── Header
            + f'<div class="cc-header">'
            + f'<div class="cc-title-group">'
            + f'<div class="cc-eyebrow">Tvarah &bull; {role_kicker}</div>'
            + f'<h1 class="cc-title">{page_title}</h1>'
            + f'<div class="cc-subtitle">Daily standup, accountability, and pipeline movement &nbsp;&middot;&nbsp; {today_str}</div>'
            + f'</div>'
            + f'<div class="cc-actions">'
            + f'<a href="/standup/submit" class="cc-btn primary">+ Add Standup</a>'
            + (f'<a href="/admin" class="cc-btn">Manage Team</a>' if show_admin_btn else '')
            + f'</div>'
            + f'</div>'
            # ── Quick filters
            + f'<div class="qf-bar">'
            + f'<button class="qf-btn active" onclick="qfFilter(this,\'all\')">All</button>'
            + f'<button class="qf-btn" onclick="qfFilter(this,\'pending\')">Pending Standups</button>'
            + f'<button class="qf-btn" onclick="qfFilter(this,\'blocked\')">Has Blockers</button>'
            + f'</div>'
            # ── Today's Brief
            + f'<div class="brief-card">'
            + f'<div class="brief-text">{brief_text}</div>'
            + f'<div class="brief-pills">{brief_status_pill}</div>'
            + f'</div>'
            # ── Health strip
            + health_html
            # ── Upcoming Interviews (today + tomorrow)
            + (lambda: (
                lambda _uiv: (
                    f'<div class="sec-hd">&#128197; Upcoming Interviews ({len(_uiv)})</div>'
                    f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">'
                    + "".join(
                        (lambda is_t, iv: (
                            f'<a href="/candidates/{escape(iv["candidate_id"])}" style="text-decoration:none;flex-shrink:0">'
                            f'<div style="background:#fff;border:1px solid {"#BFDBFE" if is_t else "#E0E7FF"};border-radius:10px;padding:10px 14px;min-width:160px;max-width:200px">'
                            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px;margin-bottom:4px">'
                            f'<div style="font-size:12px;font-weight:700;color:#172B4D;line-height:1.3">{escape(iv.get("name") or iv["candidate_id"])}</div>'
                            f'<span style="background:{"#FEF3C7" if is_t else "#EFF6FF"};color:{"#92400E" if is_t else "#1D4ED8"};border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700;white-space:nowrap">{"Today" if is_t else "Tomorrow"}</span></div>'
                            f'<div style="font-size:11px;color:#5243AA;font-weight:600">{escape(iv.get("interview_round") or "Interview")}</div>'
                            f'<div style="font-size:11px;color:#6B778C;margin-top:2px">&#128336; {escape(iv.get("interview_time") or "Time TBD")}</div>'
                            f'</div></a>'
                        ))(iv.get("interview_date") == today_str, iv)
                        for iv in _uiv
                    )
                    + f'</div>'
                ) if _uiv else ''
            )(list(__import__("database", fromlist=["list_upcoming_interviews"]).list_upcoming_interviews(days_ahead=1)))
            )()
            # ── Priority Attention
            + f'<div class="sec-hd">Priority Attention</div>'
            + f'<div class="sec-sub">Issues that need immediate action today</div>'
            + attn_html
            # ── Standup Compliance
            + f'<div class="compliance-bar">'
            + f'<div class="compliance-hd"><span class="compliance-title">Standup Compliance</span>'
            + f'<span class="compliance-stat" style="color:{_comp_color}">{submitted_count}/{len(all_team_members)} Submitted &nbsp;({compliance_pct}%)</span></div>'
            + f'<div class="compliance-track"><div class="compliance-fill" style="width:{compliance_pct}%;background:{_comp_color}"></div></div>'
            + f'<div class="compliance-names">{compliance_pills}</div>'
            + f'</div>'
            # ── Manager standup
            + manager_standup
            # ── Team Accountability
            + f'<div class="sec-hd">Team Accountability</div>'
            + f'<div class="sec-sub">Live performance and standup status for each team member</div>'
            + rec_section
            + sales_section
            # ── JD Pipeline
            + f'<div class="sec-divider"></div>'
            + f'<div class="sec-hd">JD Pipeline Overview</div>'
            + f'<div class="sec-sub">Active positions &mdash; click Expand Kanban to drill into candidate stages</div>'
            + f'<div class="jd-grid">{jd_compact}</div>'
            # ── Recent Activity
            + f'<div class="sec-divider"></div>'
            + f'<div class="sec-hd">Recent Standup Activity</div>'
            + f'<div class="sec-sub">Latest updates from the team</div>'
            + f'<div class="activity-card">'
            + f'<table class="table" style="margin:0"><thead><tr><th>Member</th><th>Date</th><th>Done Today</th><th>Blockers</th></tr></thead>'
            + f'<tbody>{standup_rows2 or "<tr><td colspan=4 style=\'color:var(--t3);text-align:center;padding:20px\'>No standups recorded yet.</td></tr>"}</tbody></table>'
            + f'</div>'
            # ── Leaderboard (collapsible)
            + (f'<div class="sec-divider"></div>'
               f'<details><summary style="cursor:pointer;list-style:none;font-size:13px;font-weight:700;color:var(--brand);padding:4px 0">&#9654; Recruiting Leaderboard</summary>'
               f'<div class="activity-card" style="margin-top:10px;overflow:hidden"><table class="table" style="margin:0"><thead><tr>'
               f'<th style="width:40px">#</th><th>Recruiter</th>'
               f'<th style="text-align:center">JDs</th><th style="text-align:center">Sourced</th>'
               f'<th style="text-align:center">Placed</th><th style="min-width:110px">Rate</th>'
               f'</tr></thead>'
               f'<tbody>{lb_rows or "<tr><td colspan=6 style=\'color:var(--t3);text-align:center;padding:16px\'>No recruiters yet.</td></tr>"}</tbody></table></div>'
               f'</details>' if rec_board else '')
            + filter_js
            + f'</div>'
        )

    else:
        content = f'<div class="wrap"><div class="kicker">Standup</div><p style="color:#97A0AF">Your role does not have a standup view configured.</p></div>'

    sidebar = _sidebar("standup", user)
    return HTMLResponse(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Standup Board — Resume Intelligence</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}{_BOARD_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'>{content}</div></div>"
        f"</body></html>"
    )


@app.get("/standup/recruiter/{recruiter_id}", response_class=HTMLResponse)
def recruiter_standup_detail(recruiter_id: str, user: dict = Depends(require_role("super_admin", "sales_head", "recruiter_head"))):  # noqa: C901
    from datetime import date, datetime as _dt
    from database import (
        get_user_by_id as _gub,
        list_jds_for_recruiter, list_candidates_for_recruiter,
        get_recruiter_stats, list_standups_for_user, list_jd_matches,
    )

    target = _gub(recruiter_id)
    if not target:
        raise HTTPException(status_code=404, detail="Recruiter not found")

    today_str = date.today().isoformat()
    STAGES = ["Sourcing", "Screening", "Panel", "Offer", "Placed"]

    jds = list_jds_for_recruiter(recruiter_id)
    candidates = list_candidates_for_recruiter(recruiter_id)
    stats = get_recruiter_stats(recruiter_id)
    standups = list_standups_for_user(recruiter_id, limit=30)
    cand_map = {c["candidate_id"]: c for c in candidates}

    # ── KPI tiles ──────────────────────────────────────────────────────────
    rate = round(stats["placed"] / stats["total"] * 100, 1) if stats["total"] else 0
    rate_color = "#16A34A" if rate >= 50 else ("#D97706" if rate >= 20 else "#DC2626")
    conv = round(stats["screened"] / stats["total"] * 100, 1) if stats["total"] else 0

    kpi_html = (
        f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px">'
        f'<div class="stat-card"><div class="stat-val">{stats["total"]}</div><div class="stat-lbl">Sourced</div></div>'
        f'<div class="stat-card"><div class="stat-val">{stats["screened"]}</div><div class="stat-lbl">Screened</div></div>'
        f'<div class="stat-card"><div class="stat-val">{stats["placed"]}</div><div class="stat-lbl">Placed</div></div>'
        f'<div class="stat-card"><div class="stat-val" style="color:{rate_color}">{rate}%</div><div class="stat-lbl">Placement Rate</div></div>'
        f'<div class="stat-card"><div class="stat-val">{len(jds)}</div><div class="stat-lbl">Active JDs</div></div>'
        f'</div>'
    )

    # ── Per-JD kanban with candidate cards ─────────────────────────────────
    def _stage_from_scores(c: dict) -> str:
        if c.get("panel_score") is not None:
            return "Panel"
        if c.get("recruiter_score") is not None:
            return "Screening"
        return "Sourcing"

    def _day_chip(created_at: str, deadline) -> str:
        try:
            day_x = (date.today() - _dt.fromisoformat(created_at).date()).days + 1
        except Exception:
            day_x = "?"
        if deadline:
            try:
                remaining = (date.fromisoformat(deadline[:10]) - date.today()).days
                color = "#DC2626" if remaining < 0 else ("#D97706" if remaining <= 3 else "#16A34A")
                return (f'<span style="background:{color}20;color:{color};border:1px solid {color}40;'
                        f'border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700">'
                        f'Day {day_x} &bull; {remaining}d left</span>')
            except Exception:
                pass
        return (f'<span style="background:#F0F0FB;color:#353395;border:1px solid #E0E0F5;'
                f'border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700">Day {day_x}</span>')

    stage_colors = {"Sourcing": "#62748E", "Screening": "#D97706", "Panel": "#6366F1", "Offer": "#0891B2", "Placed": "#16A34A"}

    jd_blocks = ""
    for jd in jds:
        jd_id = jd["jd_id"]
        title = jd.get("title") or "Untitled JD"
        company = jd.get("company") or ""
        deadline = jd.get("deadline")
        created_at = jd.get("created_at") or today_str
        matches = list_jd_matches(jd_id)
        buckets: dict[str, list] = {s: [] for s in STAGES}
        for m in matches:
            cid = m["candidate_id"]
            c = cand_map.get(cid) or {"name": m.get("candidate_name") or cid,
                                       "candidate_id": cid,
                                       "resume_score": m.get("rubric_score"),
                                       "recruiter_score": None, "panel_score": None}
            stage = _stage_from_scores(c)
            if stage in buckets:
                buckets[stage].append(c)

        is_overdue = False
        if deadline:
            try:
                is_overdue = date.fromisoformat(deadline[:10]) < date.today()
            except Exception:
                pass
        border = "#DC2626" if is_overdue else "#CAD5E2"

        # Count summary bar
        total_in_jd = sum(len(v) for v in buckets.values())
        stage_bar = ""
        for s in STAGES:
            cnt = len(buckets[s])
            if cnt:
                pct = round(cnt / total_in_jd * 100) if total_in_jd else 0
                stage_bar += (
                    f'<div style="flex:{pct};background:{stage_colors[s]};height:6px;'
                    f'border-radius:2px;margin-right:2px" title="{s}: {cnt}"></div>'
                )

        # Candidate table per JD
        cand_rows = ""
        all_jd_cands = [c for stage_cands in buckets.values() for c in stage_cands]
        for c in all_jd_cands:
            cid = c.get("candidate_id", "")
            name = c.get("name") or cid
            stage = _stage_from_scores(c)
            score = c.get("panel_score") or c.get("recruiter_score") or c.get("resume_score")
            score_str = f"{float(score):.0f}" if score is not None else "—"
            score_color = "#16A34A" if score and float(score) >= 70 else ("#D97706" if score and float(score) >= 50 else "#DC2626")
            stage_dot_color = stage_colors.get(stage, "#62748E")
            cand_rows += (
                f'<tr>'
                f'<td><a href="/candidates/{cid}" style="color:#353395;font-weight:600;text-decoration:none">{escape(name)}</a></td>'
                f'<td><span style="display:inline-flex;align-items:center;gap:5px">'
                f'<span style="width:7px;height:7px;border-radius:50%;background:{stage_dot_color};display:inline-block"></span>'
                f'{stage}</span></td>'
                f'<td><span style="font-weight:700;color:{score_color if score is not None else "#62748E"}">{score_str}</span></td>'
                f'<td><a href="/recruiter-screen/{cid}" class="btn-sec" style="font-size:11px;padding:4px 10px">Screen</a>'
                f'&nbsp;<a href="/panel-screen/{cid}" class="btn-sec" style="font-size:11px;padding:4px 10px">Panel</a></td>'
                f'</tr>'
            )

        jd_blocks += (
            f'<div style="background:#fff;border:2px solid {border};border-radius:12px;padding:16px;margin-bottom:14px">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;flex-wrap:wrap;gap:8px">'
            f'<div>'
            f'<div style="font-size:15px;font-weight:800;color:#262626">{escape(title)}'
            f'{"<span style=\'color:#DC2626;font-size:11px;margin-left:8px;font-weight:700\'>OVERDUE</span>" if is_overdue else ""}'
            f'</div>'
            f'<div style="font-size:12px;color:#62748E;margin-top:2px">{escape(company)}</div>'
            f'</div>'
            f'{_day_chip(created_at, deadline)}'
            f'</div>'
            f'<div style="display:flex;gap:2px;margin-bottom:12px;height:6px">{stage_bar}</div>'
            f'<table class="table">'
            f'<thead><tr><th>Candidate</th><th>Stage</th><th>Score</th><th>Actions</th></tr></thead>'
            f'<tbody>{cand_rows or "<tr><td colspan=4 style=\'color:#62748E;text-align:center;padding:10px\'>No candidates matched to this JD yet.</td></tr>"}</tbody>'
            f'</table>'
            f'</div>'
        )

    if not jds:
        jd_blocks = '<div style="color:#62748E;font-size:13px;padding:20px 0">No JDs assigned to this recruiter.</div>'

    # ── Candidate pipeline summary ──────────────────────────────────────────
    stage_counts: dict[str, int] = {s: 0 for s in STAGES}
    for c in candidates:
        stage_counts[_stage_from_scores(c)] += 1
    pipeline_html = (
        f'<div style="display:flex;gap:0;border:1px solid #CAD5E2;border-radius:10px;overflow:hidden;margin-bottom:20px">'
    )
    stage_icons = {"Sourcing": "🔍", "Screening": "📞", "Panel": "🎤", "Offer": "📋", "Placed": "✅"}
    for s in STAGES:
        bg = "#F0F0FB" if s == "Sourcing" else "#fff"
        pipeline_html += (
            f'<div style="flex:1;padding:14px 10px;text-align:center;border-right:1px solid #CAD5E2;background:{bg}">'
            f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:#62748E;font-weight:600;margin-bottom:4px">{stage_icons.get(s,"")} {s}</div>'
            f'<div style="font-size:26px;font-weight:900;color:{stage_colors.get(s,"#353395")}">{stage_counts[s]}</div>'
            f'</div>'
        )
    pipeline_html += '</div>'

    # ── Standup history ─────────────────────────────────────────────────────
    standup_rows = ""
    for s in standups:
        date_disp = s.get("date", "")
        today_badge = ' <span style="background:#F0F0FB;color:#353395;border-radius:999px;padding:1px 7px;font-size:10px;font-weight:700">Today</span>' if date_disp == today_str else ""
        standup_rows += (
            f'<tr>'
            f'<td style="font-weight:600;white-space:nowrap">{date_disp}{today_badge}</td>'
            f'<td style="font-size:12px;color:#444;max-width:220px">{escape((s.get("today") or "—")[:120])}</td>'
            f'<td style="font-size:12px;color:#DC2626;max-width:160px">{escape((s.get("blockers") or "—")[:80])}</td>'
            f'<td style="font-size:12px;color:#62748E;max-width:160px">{escape((s.get("priorities") or "—")[:80])}</td>'
            f'</tr>'
        )

    sidebar = _sidebar("standup", user)
    return HTMLResponse(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{escape(target['full_name'])} — Recruiter Performance</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"

        # Back + header
        f"<div style='margin-bottom:20px'>"
        f"<a href='/standup' style='font-size:12px;color:#62748E;text-decoration:none;display:inline-flex;align-items:center;gap:4px;margin-bottom:10px'>"
        f"&#8592; Back to Team Board</a>"
        f"<div class='kicker'>Recruiter Performance</div>"
        f"<div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap'>"
        f"<div style='width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,#353395,#6366F1);"
        f"display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;font-weight:800'>"
        f"{escape(target['full_name'][0].upper())}</div>"
        f"<div><h1 style='font-size:22px;font-weight:800;color:#262626;margin:0'>{escape(target['full_name'])}</h1>"
        f"<div style='font-size:13px;color:#62748E;margin-top:2px'>{escape(target['email'])}"
        f" &bull; <span style='text-transform:capitalize'>{target['role'].replace('_',' ')}</span></div></div>"
        f"</div></div>"

        # KPI tiles
        + kpi_html

        # Pipeline funnel
        + f"<div class='card' style='margin-bottom:16px;padding:16px 18px'>"
        + f"<h2 style='font-size:14px;font-weight:700;margin-bottom:12px'>Pipeline Funnel</h2>"
        + pipeline_html
        + f"</div>"

        # JD kanban
        + f"<div class='card' style='margin-bottom:16px;padding:16px 18px'>"
        + f"<h2 style='font-size:14px;font-weight:700;margin-bottom:14px'>JDs &amp; Candidates</h2>"
        + jd_blocks
        + f"</div>"

        # Standup log
        + f"<div class='card'>"
        + f"<h2 style='font-size:14px;font-weight:700;margin-bottom:12px'>Standup Log</h2>"
        + f"<table class='table'><thead><tr><th>Date</th><th>Done</th><th>Blockers</th><th>Next</th></tr></thead>"
        + f"<tbody>{standup_rows or '<tr><td colspan=4 style=\"color:#62748E;text-align:center;padding:16px\">No standups recorded yet.</td></tr>'}</tbody>"
        + f"</table></div>"

        + f"</div></div></div></body></html>"
    )


@app.post("/standup/assign-jd")
async def standup_assign_jd(request: Request, user: dict = Depends(require_role("super_admin"))):
    from database import assign_jd_to_recruiter as _ajr
    form = await request.form()
    jd_id = (form.get("jd_id") or "").strip()
    recruiter_id = (form.get("recruiter_id") or "").strip()
    if jd_id and recruiter_id:
        _ajr(jd_id, recruiter_id)
    return RedirectResponse("/standup", status_code=302)


@app.post("/standup/submit")
async def standup_submit(request: Request, user: dict = Depends(get_current_user)):
    from database import upsert_standup
    form = await request.form()
    date_val = (form.get("date") or "").strip()
    jd_id = (form.get("jd_id") or "").strip() or None
    data = {
        "jd_id": jd_id,
        "today": (form.get("today") or "").strip(),
        "blockers": (form.get("blockers") or "").strip(),
        "priorities": (form.get("priorities") or "").strip(),
    }
    if date_val:
        upsert_standup(user["user_id"], date_val, data)
    return RedirectResponse("/standup", status_code=302)


@app.post("/candidate/{candidate_id}/schedule")
async def save_interview_schedule(
    candidate_id: str,
    request: Request,
    user: dict = Depends(require_role("super_admin", "recruiter_head", "recruiter")),
):
    from database import update_interview_schedule
    form = await request.form()
    interview_date  = (form.get("interview_date")  or "").strip() or None
    interview_time  = (form.get("interview_time")  or "").strip() or None
    interview_round = (form.get("interview_round") or "").strip() or None
    update_interview_schedule(candidate_id, interview_date, interview_time, interview_round)
    return RedirectResponse(f"/candidates/{candidate_id}", status_code=302)


# ===========================================================================
# Admin Panel
# ===========================================================================

_ADMIN_CSS = """<style>
.user-card{background:#fff;border:1px solid #CAD5E2;border-radius:12px;padding:16px 18px;margin-bottom:10px;display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap}
.user-avatar{width:42px;height:42px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:800;color:#fff;flex-shrink:0}
.user-info{flex:1;min-width:180px}
.user-name{font-size:14px;font-weight:700;color:#262626;margin-bottom:2px}
.user-email{font-size:12px;color:#62748E}
.user-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.reset-form{display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap}
.reset-form input{padding:6px 10px;border:1.5px solid #CAD5E2;border-radius:7px;font-size:12px;width:160px;outline:none}
.reset-form input:focus{border-color:#353395}
.tag{border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;display:inline-block}
.section-title{font-size:16px;font-weight:800;color:#262626;margin-bottom:4px}
.section-sub{font-size:12px;color:#62748E;margin-bottom:16px}
.toast{background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:9px;padding:10px 14px;font-size:13px;margin-bottom:16px;font-weight:600}
</style>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user: dict = Depends(require_role("super_admin"))):  # noqa: C901
    from database import list_users as _list_users, list_job_postings as _list_jds
    users = _list_users()
    jds = _list_jds(include_closed=False)
    msg = request.query_params.get("msg", "")

    role_colors = {
        "super_admin": "#172B4D", "sales_head": "#00875A",
        "recruiter_head": "#5243AA", "recruiter": "#FF8B00",
        "sales_executive": "#00BFA5", "panel": "#0065FF",
    }
    role_labels = {
        "super_admin": "Super Admin", "sales_head": "Sales Head",
        "recruiter_head": "Lead Recruiter", "recruiter": "Recruiter",
        "sales_executive": "Sales Executive", "panel": "Panel",
    }
    avatar_bg = {
        "super_admin": "linear-gradient(135deg,#172B4D,#344563)",
        "sales_head": "linear-gradient(135deg,#00875A,#36B37E)",
        "recruiter_head": "linear-gradient(135deg,#5243AA,#8777D9)",
        "recruiter": "linear-gradient(135deg,#FF8B00,#FFAB00)",
        "sales_executive": "linear-gradient(135deg,#00BFA5,#1DE9B6)",
        "panel": "linear-gradient(135deg,#0065FF,#4C9AFF)",
    }

    # Build user cards
    user_cards = ""
    for u in users:
        uid = u["user_id"]
        role_key = u.get("role", "recruiter")
        rc = role_colors.get(role_key, "#62748E")
        ab = avatar_bg.get(role_key, "linear-gradient(135deg,#62748E,#94A3B8)")
        initials = "".join(p[0].upper() for p in (u.get("full_name") or "?").split()[:2])
        active = u.get("is_active", 1)
        active_badge = (f'<span class="tag" style="background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0">Active</span>'
                        if active else
                        f'<span class="tag" style="background:#FEE2E2;color:#DC2626;border:1px solid #FECACA">Inactive</span>')
        role_badge = f'<span class="tag" style="background:{rc}18;color:{rc};border:1px solid {rc}30">{role_labels.get(role_key, role_key)}</span>'
        toggle_label = "Deactivate" if active else "Activate"
        toggle_style = ("background:transparent;border:1px solid #FECACA;color:#DC2626" if active
                        else "background:transparent;border:1px solid #BBF7D0;color:#16A34A")

        user_cards += (
            f'<div class="user-card">'
            f'<div class="user-avatar" style="background:{ab}">{initials}</div>'
            f'<div style="flex:1;min-width:0">'
            f'<div class="user-name">{escape(u.get("full_name",""))}</div>'
            f'<div class="user-email">{escape(u.get("email",""))}</div>'
            f'<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap">{role_badge}{active_badge}</div>'
            f'<div class="user-actions">'
            # Deactivate/Activate
            f'<form method="POST" action="/admin/user/{uid}/deactivate" style="display:inline">'
            f'<button type="submit" style="{toggle_style};border-radius:7px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer">{toggle_label}</button></form>'
            # Reset password inline
            f'<form method="POST" action="/admin/user/{uid}/reset-password" style="display:inline-flex;align-items:center;gap:6px">'
            f'<input type="password" name="new_password" placeholder="New password" minlength="6" required '
            f'style="padding:5px 10px;border:1.5px solid #CAD5E2;border-radius:7px;font-size:12px;width:140px;outline:none">'
            f'<button type="submit" style="background:#F0F0FB;color:#353395;border:1px solid #E0E0F5;border-radius:7px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer">Reset Password</button>'
            f'</form>'
            f'</div>'
            f'</div>'
            f'</div>'
        )

    # JD assign dropdowns
    _active_users = [u for u in users if u.get("is_active")]
    recruiter_options = '<option value="">— Unassigned —</option>' + "".join(
        f'<option value="{u["user_id"]}">{escape(u["full_name"])} ({role_labels.get(u["role"],"?")})</option>'
        for u in sorted(_active_users, key=lambda u: (u["role"] != "recruiter_head", u["full_name"]))
        if u["role"] in ("recruiter", "recruiter_head", "super_admin")
    )
    sales_options = '<option value="">— Unassigned —</option>' + "".join(
        f'<option value="{u["user_id"]}">{escape(u["full_name"])} ({role_labels.get(u["role"],"?")})</option>'
        for u in _active_users if u["role"] in ("sales_head", "sales_executive", "super_admin")
    )
    jd_options = (
        "".join(f'<option value="{j["jd_id"]}">{escape((j["title"] or j["jd_id"])[:50])} — {escape(j.get("company") or "")}</option>' for j in jds)
        or '<option value="">No active JDs</option>'
    )

    toast = f'<div class="toast">&#10003; {escape(msg)}</div>' if msg else ""
    sidebar = _sidebar("admin", user)
    return HTMLResponse(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Admin Panel — Resume Intelligence</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}{_ADMIN_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'><div class='wrap'>"

        # Header
        + f"<div style='margin-bottom:22px'><div class='kicker'>Tvarah &bull; Admin</div>"
        + f"<h1 style='font-size:24px;font-weight:900;color:#262626;margin-bottom:4px'>User Management</h1>"
        + f"<p style='color:#62748E;font-size:13px'>Manage team members, reset passwords, and assign JDs.</p></div>"
        + toast

        # Org hierarchy card
        + f"<div class='card' style='margin-bottom:16px'>"
        + f"<div class='section-title'>Tvarah Org Hierarchy</div>"
        + f"<div class='section-sub'>Role structure — who reports to whom</div>"
        + f"<div style='display:flex;flex-direction:column;gap:0;margin-top:12px'>"
        + f"<div style='display:flex;align-items:center;gap:10px;padding:8px 12px;background:#F8F9FB;border-radius:8px;margin-bottom:4px'>"
        + f"<div style='width:10px;height:10px;border-radius:50%;background:#172B4D'></div>"
        + f"<span style='font-weight:700;color:#172B4D;font-size:13px'>Sandeep Guduru</span>"
        + f"<span style='background:#172B4D18;color:#172B4D;border:1px solid #172B4D30;border-radius:99px;padding:1px 8px;font-size:10px;font-weight:700'>Head</span>"
        + f"</div>"
        + f"<div style='margin-left:28px;border-left:2px solid #E2E8F0;padding-left:14px'>"
        + f"<div style='display:flex;align-items:center;gap:10px;padding:6px 12px;background:#E3FCEF18;border-radius:8px;margin-bottom:3px;margin-top:3px'>"
        + f"<div style='width:8px;height:8px;border-radius:50%;background:#00875A'></div>"
        + f"<span style='font-weight:600;color:#172B4D;font-size:13px'>Chandrima</span>"
        + f"<span style='background:#00875A18;color:#00875A;border:1px solid #00875A30;border-radius:99px;padding:1px 8px;font-size:10px;font-weight:700'>Sales Head</span>"
        + f"</div>"
        + f"<div style='margin-left:24px;border-left:2px solid #E2E8F0;padding-left:14px'>"
        + f"<div style='font-size:11px;color:#97A0AF;padding:4px 6px;font-style:italic'>Sales Executives report here</div>"
        + f"<div style='display:flex;flex-wrap:wrap;gap:4px;padding-bottom:4px'>"
        + "".join(
            f'<span style="background:#00BFA518;color:#00875A;border:1px solid #00BFA530;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:600">{escape(u["full_name"])}</span>'
            for u in users if u["role"] == "sales_executive" and u.get("is_active")
        )
        + f"</div></div>"
        + f"<div style='display:flex;align-items:center;gap:10px;padding:6px 12px;background:#EAE6FF18;border-radius:8px;margin-bottom:3px;margin-top:8px'>"
        + f"<div style='width:8px;height:8px;border-radius:50%;background:#5243AA'></div>"
        + f"<span style='font-weight:600;color:#172B4D;font-size:13px'>Lead Recruiter</span>"
        + f"<span style='background:#5243AA18;color:#5243AA;border:1px solid #5243AA30;border-radius:99px;padding:1px 8px;font-size:10px;font-weight:700'>Lead Recruiter</span>"
        + f"</div>"
        + f"<div style='margin-left:24px;border-left:2px solid #E2E8F0;padding-left:14px'>"
        + f"<div style='font-size:11px;color:#97A0AF;padding:4px 6px;font-style:italic'>Recruiters report here</div>"
        + f"<div style='display:flex;flex-wrap:wrap;gap:4px;padding-bottom:4px'>"
        + "".join(
            f'<span style="background:#FF8B0018;color:#D97706;border:1px solid #FF8B0030;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:600">{escape(u["full_name"])}</span>'
            for u in users if u["role"] == "recruiter" and u.get("is_active")
        )
        + f"</div></div>"
        + f"</div>"
        + f"</div>"
        + f"</div>"

        # Team members
        + f"<div class='card' style='margin-bottom:16px'>"
        + f"<div class='section-title'>Team Members <span style='font-size:13px;font-weight:500;color:#62748E'>({len(users)} total)</span></div>"
        + f"<div class='section-sub'>Click Reset Password next to any member to update their credentials.</div>"
        + (user_cards or "<div style='color:#62748E;font-size:13px;padding:12px 0'>No users yet.</div>")
        + f"</div>"

        # Invite new user
        + f"<div class='card' style='margin-bottom:16px'>"
        + f"<div class='section-title'>Add Team Member</div>"
        + f"<div class='section-sub'>New members will be able to sign in immediately with the password you set.</div>"
        + f"<form method='POST' action='/admin/invite'>"
        + f"<div class='form-row'>"
        + f"<div class='form-group'><label class='form-label'>Full Name</label><input class='form-input' name='full_name' required placeholder='Jane Smith'></div>"
        + f"<div class='form-group'><label class='form-label'>Work Email</label><input class='form-input' type='email' name='email' required placeholder='jane@company.com'></div>"
        + f"</div>"
        + f"<div class='form-row'>"
        + f"<div class='form-group'><label class='form-label'>Role</label>"
        + f"<select class='form-input' name='role'>"
        + f"<option value='recruiter'>Recruiter</option>"
        + f"<option value='recruiter_head'>Lead Recruiter</option>"
        + f"<option value='sales_executive'>Sales Executive</option>"
        + f"<option value='sales_head'>Sales Head</option>"
        + f"<option value='panel'>Panel Interviewer</option>"
        + f"<option value='super_admin'>Super Admin</option>"
        + f"</select></div>"
        + f"<div class='form-group'><label class='form-label'>Initial Password</label><input class='form-input' type='password' name='password' required placeholder='Min 6 characters' minlength='6'></div>"
        + f"</div>"
        + f"<button type='submit' class='btn' style='margin-top:4px'>&#43; Create Account</button>"
        + f"</form></div>"

        # Assign JD — recruiter + sales agent + deadline
        + f"<div class='card' style='margin-bottom:16px'>"
        + f"<div class='section-title'>Assign JD</div>"
        + f"<div class='section-sub'>Set recruiter, sales agent, and deadline for a JD. All fields shown on the standup board.</div>"
        + f"<form method='POST' action='/admin/assign-jd'>"
        + f"<div class='form-row'>"
        + f"<div class='form-group'><label class='form-label'>Job Description</label>"
        + f"<select class='form-input' name='jd_id'>{jd_options}</select></div>"
        + f"<div class='form-group'><label class='form-label'>&#128196; Recruiter (sourcing)</label>"
        + f"<select class='form-input' name='recruiter_id'>{recruiter_options}</select></div>"
        + f"</div>"
        + f"<div class='form-row'>"
        + f"<div class='form-group'><label class='form-label'>&#128100; Sales Agent (client)</label>"
        + f"<select class='form-input' name='sales_agent_id'>{sales_options}</select></div>"
        + f"<div class='form-group'><label class='form-label'>Deadline</label>"
        + f"<input class='form-input' type='date' name='deadline'></div>"
        + f"</div>"
        + f"<button type='submit' class='btn'>Save Assignment</button>"
        + f"</form></div>"

        + f"</div></div></div></body></html>"
    )


@app.post("/admin/invite")
async def admin_invite(request: Request, user: dict = Depends(require_role("super_admin"))):
    from database import create_user as _cu
    from urllib.parse import quote
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    full_name = (form.get("full_name") or "").strip()
    role = (form.get("role") or "recruiter").strip()
    password = (form.get("password") or "").strip()
    msg = "Account already exists."
    if email and full_name and password:
        existing = get_user_by_email(email)
        if not existing:
            _cu(email=email, password_hash=hash_password(password), full_name=full_name, role=role)
            msg = f"Account created for {full_name} ({email})"
    return RedirectResponse(f"/admin?msg={quote(msg)}", status_code=302)


@app.post("/admin/user/{user_id}/deactivate")
async def admin_toggle_user(user_id: str, request: Request, user: dict = Depends(require_role("super_admin"))):
    from database import get_user_by_id as _gu, update_user as _uu
    from urllib.parse import quote
    target = _gu(user_id)
    msg = "User not found."
    if target:
        new_state = 0 if target.get("is_active") else 1
        _uu(user_id, is_active=new_state)
        msg = f"{'Activated' if new_state else 'Deactivated'}: {target.get('full_name','')}"
    return RedirectResponse(f"/admin?msg={quote(msg)}", status_code=302)


@app.post("/admin/user/{user_id}/reset-password")
async def admin_reset_password(user_id: str, request: Request, user: dict = Depends(require_role("super_admin"))):
    from database import get_user_by_id as _gu, update_user as _uu
    from urllib.parse import quote
    form = await request.form()
    new_password = (form.get("new_password") or "").strip()
    target = _gu(user_id)
    msg = "User not found."
    if target and new_password and len(new_password) >= 6:
        _uu(user_id, password_hash=hash_password(new_password))
        msg = f"Password reset for {target.get('full_name','')}"
    elif new_password and len(new_password) < 6:
        msg = "Password must be at least 6 characters."
    return RedirectResponse(f"/admin?msg={quote(msg)}", status_code=302)


@app.post("/admin/assign-jd")
async def admin_assign_jd(request: Request, user: dict = Depends(require_role("super_admin"))):
    from database import assign_jd_to_recruiter as _ajr, set_jd_deadline as _sjd, assign_sales_agent_to_jd as _asa
    from urllib.parse import quote
    from database import get_user_by_id as _gub, get_job_posting as _gjp
    form = await request.form()
    jd_id = (form.get("jd_id") or "").strip()
    recruiter_id = (form.get("recruiter_id") or "").strip()
    sales_agent_id = (form.get("sales_agent_id") or "").strip()
    deadline = (form.get("deadline") or "").strip() or None
    msg = "Please select a JD."
    if jd_id:
        if recruiter_id:
            _ajr(jd_id, recruiter_id)
        if sales_agent_id:
            _asa(jd_id, sales_agent_id)
        if deadline:
            _sjd(jd_id, deadline)
        jd = _gjp(jd_id)
        rec = _gub(recruiter_id) if recruiter_id else None
        sa = _gub(sales_agent_id) if sales_agent_id else None
        parts = [f"JD '{(jd or {}).get('title','?')}'"]
        if rec:
            parts.append(f"recruiter: {rec['full_name']}")
        if sa:
            parts.append(f"sales: {sa['full_name']}")
        if deadline:
            parts.append(f"deadline: {deadline}")
        msg = " | ".join(parts) + " — saved."
    return RedirectResponse(f"/admin?msg={quote(msg)}", status_code=302)


# ===========================================================================

@app.get("/sourcing", response_class=HTMLResponse)
def sourcing_page(user: dict = Depends(require_role("super_admin", "sales_head", "recruiter_head"))):
    from sourcing.sourcing_store import list_sourcing_jobs
    jobs = list_sourcing_jobs()
    jobs_rows = ""
    for j in jobs[:20]:
        job_id = j.get("job_id", "")
        query = j.get("query_text", "")[:60]
        count = j.get("results_count", 0)
        status = j.get("status", "")
        created = (j.get("created_at") or "")[:16].replace("T", " ")
        status_color = "#16A34A" if status == "done" else "#D97706"
        jobs_rows += (
            f'<tr>'
            f'<td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{query}</td>'
            f'<td><span style="color:{status_color};font-weight:700">{count}</span></td>'
            f'<td style="color:var(--text2)">{created}</td>'
            f'<td><a href="/source/view/{job_id}" class="btn-sec" style="padding:4px 10px;font-size:11px">View</a></td>'
            f'</tr>'
        )

    jobs_table = (
        "<table class='table'><thead><tr><th>Query</th><th>Candidates</th><th>Date</th><th></th></tr></thead>"
        f"<tbody>{jobs_rows}</tbody></table>"
    ) if jobs else "<div style='color:var(--text2);padding:16px 0'>No sourcing runs yet. Run your first search above.</div>"

    try:
        from job_posting_store import list_job_postings as _ljp
        jd_list = _ljp(include_closed=False)
    except Exception:
        jd_list = []
    jd_options = "<option value=''>-- No JD (open search) --</option>" + "".join(
        f'<option value="{j["jd_id"]}">{j.get("title","")}</option>' for j in jd_list
    )

    sidebar = _sidebar("sourcing", user)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Candidate Sourcing — Tvarah</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'>"
        f"<div class='wrap'>"
        f"<div style='margin-bottom:20px'>"
        f"<div class='kicker'>Outbound</div>"
        f"<h1 style='font-size:24px;font-weight:800;margin:4px 0 4px;color:var(--text)'>Candidate Sourcing</h1>"
        f"<p style='color:var(--text2);font-size:14px'>Search GitHub with natural language — find engineers before they apply.</p>"
        f"</div>"
        f"<div class='card'>"
        f"<div class='kicker'>Find Candidates</div>"
        f"<div style='display:flex;gap:12px;flex-direction:column;margin-top:8px'>"
        f"<div class='form-group'>"
        f"<label class='form-label'>What are you looking for?</label>"
        f"<textarea id='queryInput' class='form-input' rows='3' placeholder='e.g. 10 senior Python engineers in Bangalore with FastAPI and ML experience'></textarea>"
        f"</div>"
        f"<div style='display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end'>"
        f"<div class='form-group' style='flex:1;min-width:220px'>"
        f"<label class='form-label'>Match against JD (optional)</label>"
        f"<select id='jdSelect' class='form-input'>{jd_options}</select>"
        f"</div>"
        f"<div class='form-group' style='min-width:100px'>"
        f"<label class='form-label'>Max results</label>"
        f"<input type='number' id='countInput' class='form-input' value='20' min='5' max='50'>"
        f"</div>"
        f"<button class='btn' onclick='runSource()' id='srcBtn'>&#128269; Find Candidates</button>"
        f"</div>"
        f"</div>"
        f"</div>"
        f"<div id='criteriaCard' style='display:none' class='card'>"
        f"<div class='kicker'>Parsed Criteria</div>"
        f"<div id='criteriaContent' style='font-size:13px;color:var(--text2)'></div>"
        f"</div>"
        f"<div id='resultsCard' style='display:none' class='card'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:14px'>"
        f"<div>"
        f"<div class='kicker'>Results</div>"
        f"<h2 style='font-size:16px;font-weight:700' id='resultsTitle'>Candidates Found</h2>"
        f"</div>"
        f"<a id='viewAllLink' href='#' class='btn-sec' style='font-size:12px'>View Full Results</a>"
        f"</div>"
        f"<div id='candidateCards'></div>"
        f"</div>"
        f"<div class='card' style='margin-top:8px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px'>"
        f"<h2 style='font-size:16px;font-weight:700;color:var(--text)'>Recent Sourcing Runs</h2>"
        f"</div>"
        f"{jobs_table}"
        f"</div>"
        f"<div class='card' style='margin-top:8px'>"
        f"<div class='kicker'>Downloads</div>"
        f"<h2 style='font-size:15px;font-weight:700;color:var(--text);margin:4px 0 10px'>Resume &amp; Scoring Resources</h2>"
        f"<div style='display:flex;gap:12px;flex-wrap:wrap'>"
        f"<a href='/sourcing/download/resume' class='btn-sec' style='display:inline-flex;align-items:center;gap:6px;font-size:13px'>"
        f"&#128196; Sample 100/100 Resume (.docx)</a>"
        f"<a href='/sourcing/download/resume-pdf' class='btn-sec' style='display:inline-flex;align-items:center;gap:6px;font-size:13px'>"
        f"&#128196; Sample 100/100 Resume (.pdf)</a>"
        f"<a href='/sourcing/download/guide' class='btn-sec' style='display:inline-flex;align-items:center;gap:6px;font-size:13px'>"
        f"&#128218; Resume Analysis Parameter Guide (.docx)</a>"
        f"</div>"
        f"<p style='font-size:12px;color:var(--text2);margin-top:10px'>The sample resume shows exactly what signals score maximum points across all rubric dimensions. The guide explains every parameter the scoring engine evaluates.</p>"
        f"</div>"
        f"</div></div></div>"
        f"<script>"
        f"async function runSource(){{"
        f"  const q=document.getElementById('queryInput').value.trim();"
        f"  if(!q)return;"
        f"  const jdId=document.getElementById('jdSelect').value;"
        f"  const count=parseInt(document.getElementById('countInput').value)||20;"
        f"  const btn=document.getElementById('srcBtn');"
        f"  btn.disabled=true;btn.textContent='Searching...';"
        f"  document.getElementById('resultsCard').style.display='none';"
        f"  document.getElementById('criteriaCard').style.display='none';"
        f"  try{{"
        f"    const res=await fetch('/source',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{query:q,jd_id:jdId||null,count}})}});"
        f"    const data=await res.json();"
        f"    if(!res.ok){{alert('Error: '+(data.detail||res.statusText));return;}}"
        f"    const c=data.criteria||{{}};"
        f"    const skillList=(c.skills||[]).join(', ')||'any';"
        f"    const langList=(c.github_languages||[]).join(', ')||'any';"
        f"    document.getElementById('criteriaContent').innerHTML="
        f"      `<b>Skills:</b> ${{skillList}} &bull; <b>Seniority:</b> ${{c.seniority||'MID'}}`"
        f"      +(c.location?` &bull; <b>Location:</b> ${{c.location}}`:'')"
        f"      +` &bull; <b>Languages:</b> ${{langList}}`"
        f"      +(data.resumes_found?` &bull; <span style='color:#16A34A'>&#128196; ${{data.resumes_found}} resume${{data.resumes_found>1?'s':''}} found &amp; analysed</span>`:'');"
        f"    document.getElementById('criteriaCard').style.display='';"
        f"    document.getElementById('resultsTitle').textContent='Top '+(data.top_candidates||[]).length+' of '+data.results_count+' candidates found';"
        f"    document.getElementById('viewAllLink').href='/source/view/'+data.job_id;"
        f"    _srcRank=0;document.getElementById('candidateCards').innerHTML=wrapTable((data.top_candidates||[]).map(renderCard).join(''));"
        f"    document.getElementById('resultsCard').style.display='';"
        f"  }}catch(e){{alert('Request failed: '+e);}}"
        f"  finally{{btn.disabled=false;btn.textContent='\U0001F50D Find Candidates';}}"
        f"}}"
        f"function scoreColor(s){{return s>=60?'#16A34A':s>=40?'#D97706':'#DC2626';}}"
        f"function resumeBadge(c){{"
        f"  if(c.resume_status==='analyzed')return `<a href='/candidates/gh_${{c.github_username}}' target='_blank' style='background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;text-decoration:none'>&#9733; Resume Analysed</a>`;"
        f"  if(c.resume_status==='found'||c.resume_pdf_url)return `<span style='background:#EFF6FF;color:#2563EB;border:1px solid #BFDBFE;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700'>&#128196; Resume Found</span>`;"
        f"  return '';"
        f"}}"
        f"function skillBadges(c){{"
        f"  const matched=(c.matched_skills||[]).map(s=>`<span style='background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600'>&#10003; ${{s}}</span>`).join(' ');"
        f"  const missing=(c.missing_skills||[]).map(s=>`<span style='background:#FEF2F2;color:#DC2626;border:1px solid #FECACA;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600'>&#10007; ${{s}}</span>`).join(' ');"
        f"  return matched+' '+missing;"
        f"}}"
        f"let _srcRank=0;"
        f"function renderCard(c){{"
        f"  _srcRank++;"
        f"  const score=c.sourcing_score||0;"
        f"  const sc=score>=60?'#16A34A':score>=40?'#D97706':'#DC2626';"
        f"  const bio=(c.bio||'').slice(0,80);"
        f"  const matched=(c.matched_skills||[]).slice(0,4).map(s=>`<span style='background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600'>&#10003; ${{s}}</span>`).join(' ');"
        f"  const missing=(c.missing_skills||[]).slice(0,3).map(s=>`<span style='background:#FEF2F2;color:#DC2626;border:1px solid #FECACA;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600'>&#10007; ${{s}}</span>`).join(' ');"
        f"  return `<tr style='vertical-align:top;border-bottom:1px solid var(--border)'>"
        f"<td style='text-align:center;color:#97A0AF;font-size:13px;font-weight:600;padding:10px 8px;width:32px'>#${{_srcRank}}</td>"
        f"<td style='padding:10px 8px'>"
        f"<div style='font-size:14px;font-weight:700;color:#172B4D;margin-bottom:3px'>${{c.display_name||c.github_username}}</div>"
        f"${{c.location?`<div style='font-size:11px;color:#97A0AF'>&#128205; ${{c.location}}</div>`:'' }}"
        f"<div style='font-size:12px;color:#5B6D83;margin-top:2px'>${{bio}}</div>"
        f"</td>"
        f"<td style='padding:10px 8px;max-width:240px'>${{matched}} ${{missing}}</td>"
        f"<td style='text-align:center;padding:10px 8px'><span style='font-size:20px;font-weight:900;color:${{sc}}'>${{score}}</span></td>"
        f"<td style='padding:10px 8px;white-space:nowrap'>"
        f"<a href='/source/candidate/${{c.github_username}}' class='btn' style='font-size:11px;padding:5px 10px'>View</a>"
        f"</td></tr>`;"
        f"}}"
        f"function wrapTable(rows){{return `<div style='overflow-x:auto'><table class='table' style='margin:0'><thead><tr><th style='width:32px'>#</th><th>Candidate</th><th>Skills</th><th style='text-align:center'>Score</th><th></th></tr></thead><tbody>${{rows}}</tbody></table></div>`;}}"
        f"</script>"
        f"</body></html>"
    )


@app.post("/source")
async def source_candidates(req: SourceRequest):
    """Parse NL query → parallel multi-source fetch → merge + dedup → resume analysis → return top candidates.

    Sources: GitHub, GitLab, Stack Overflow, Kaggle (last two require env vars).
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    from sourcing.query_parser import parse_query
    from sourcing.github_sourcer import source_candidates as gh_source
    from sourcing.profile_normalizer import normalize_many as gh_normalize
    from sourcing.stackoverflow_sourcer import source_candidates as so_source
    from sourcing.kaggle_sourcer import source_candidates as kg_source
    from sourcing.gitlab_sourcer import source_candidates as gl_source
    from sourcing.sourcing_store import create_sourcing_job, save_sourcing_results, save_sourced_profile

    # Load JD if provided
    jd: dict | None = None
    if req.jd_id:
        try:
            from job_posting_store import load_job_posting
            jd = load_job_posting(req.jd_id)
        except Exception:
            pass

    criteria = parse_query(req.query, jd=jd)
    total_count = min(req.count, 50)
    criteria["count"] = total_count

    job_id = create_sourcing_job(req.query, criteria, jd_id=req.jd_id)

    # --- Parallel multi-source fetch ---
    # Distribute count across sources; each gets at least 8 to ensure variety
    per_source = max(8, total_count // 3)

    def _gh_run():
        raw = gh_source(criteria, count=per_source)
        return gh_normalize(raw, criteria)

    def _so_run():
        return so_source(criteria, count=per_source)

    def _kg_run():
        return kg_source(criteria, count=per_source)

    def _gl_run():
        return gl_source(criteria, count=per_source)

    source_fns = {
        "github": _gh_run,
        "stackoverflow": _so_run,
        "kaggle": _kg_run,
        "gitlab": _gl_run,
    }

    all_candidates: list[dict] = []
    source_counts: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): name for name, fn in source_fns.items()}
        for fut in _as_completed(futs):
            name = futs[fut]
            try:
                results = fut.result() or []
                source_counts[name] = len(results)
                all_candidates.extend(results)
                logger.info("Source %s → %d candidates", name, len(results))
            except Exception as exc:
                logger.warning("Source %s failed: %s", name, exc)
                source_counts[name] = 0

    # Dedup by candidate_id — keep the entry with the highest sourcing_score
    seen: dict[str, dict] = {}
    for c in all_candidates:
        cid = c.get("candidate_id") or c.get("github_username", "")
        if not cid:
            continue
        if cid not in seen or (c.get("sourcing_score") or 0) > (seen[cid].get("sourcing_score") or 0):
            seen[cid] = c

    candidates = sorted(seen.values(), key=lambda c: c.get("sourcing_score") or 0, reverse=True)
    candidates = candidates[:total_count]

    # Attach job reference and persist
    for c in candidates:
        c["sourcing_job_id"] = job_id
        save_sourced_profile(c)

    # --- Parallel resume analysis for GitHub candidates that have a PDF URL ---
    candidates_with_resume = [c for c in candidates if c.get("resume_pdf_url")]
    if candidates_with_resume:
        from sourcing.resume_finder import try_fetch_and_analyze

        def _run_resume_analysis(candidate: dict) -> None:
            result = try_fetch_and_analyze(candidate)
            username = candidate.get("github_username", "")
            status = result.get("status", "not_found")
            from sourcing.sourcing_store import load_sourced_profile, save_sourced_profile as _save
            profile = load_sourced_profile(username)
            if profile:
                profile["resume_status"] = status
                if result.get("resume_score") is not None:
                    profile["resume_score"] = result["resume_score"]
                if result.get("band"):
                    profile["resume_band"] = result["band"]
                _save(profile)
            candidate["resume_status"] = status
            if result.get("resume_score") is not None:
                candidate["resume_score"] = result["resume_score"]
            if result.get("band"):
                candidate["resume_band"] = result["band"]

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [
                loop.run_in_executor(pool, _run_resume_analysis, c)
                for c in candidates_with_resume
            ]
            await asyncio.gather(*futs, return_exceptions=True)

    save_sourcing_results(job_id, candidates)

    return {
        "job_id": job_id,
        "results_count": len(candidates),
        "criteria": criteria,
        "source_breakdown": source_counts,
        "resumes_found": len(candidates_with_resume),
        "top_candidates": candidates[:5],
    }


@app.get("/source/jobs")
def list_source_jobs():
    from sourcing.sourcing_store import list_sourcing_jobs
    return list_sourcing_jobs()


@app.get("/source/view/{job_id}", response_class=HTMLResponse)
def source_view(job_id: str, user: dict = Depends(require_role("super_admin"))):
    from sourcing.sourcing_store import load_sourcing_job
    job = load_sourcing_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sourcing job not found")

    candidates = job.get("candidates") or []
    criteria = job.get("criteria") or {}
    query_text = job.get("query_text", "")

    def _score_bar(val: int, max_val: int, color: str = "var(--primary)") -> str:
        pct = int(val / max_val * 100) if max_val else 0
        return (
            f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:3px'>"
            f"<div style='width:80px;height:5px;border-radius:3px;background:#E5E7EB;overflow:hidden'>"
            f"<div style='height:100%;width:{pct}%;background:{color};border-radius:3px'></div></div>"
            f"<span style='font-size:11px;color:var(--text2)'>{val}</span>"
            f"</div>"
        )

    def _resume_badge_html(c: dict) -> str:
        status = c.get("resume_status", "not_found")
        username = c.get("github_username", "")
        if status == "analyzed":
            rscore = c.get("resume_score")
            rband = c.get("resume_band") or ""
            score_str = f" &bull; {rscore:.0f}" if rscore else ""
            return (f'<a href="/candidates/gh_{username}" target="_blank" style="background:#DCFCE7;color:#16A34A;'
                    f'border:1px solid #BBF7D0;border-radius:999px;padding:2px 10px;font-size:10px;font-weight:700;text-decoration:none">'
                    f'&#128196; Resume Analysed{score_str} {rband}</a>')
        if status in ("found", "pdf_found") or c.get("resume_pdf_url"):
            return ('<span style="background:#EFF6FF;color:#2563EB;border:1px solid #BFDBFE;'
                    'border-radius:999px;padding:2px 10px;font-size:10px;font-weight:700">&#128196; Resume Found</span>')
        return ""

    def _skill_match_html(c: dict) -> str:
        matched = c.get("matched_skills") or []
        missing = c.get("missing_skills") or []
        if not matched and not missing:
            return ""
        parts = []
        for s in matched[:5]:
            parts.append(f'<span style="background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600">&#10003; {s}</span>')
        for s in missing[:4]:
            parts.append(f'<span style="background:#FEF2F2;color:#DC2626;border:1px solid #FECACA;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600">&#10007; {s}</span>')
        return " ".join(parts)

    _SOURCE_BADGE_STYLE = {
        "github":        ("&#9889;", "#24292E", "#fff"),
        "gitlab":        ("&#9824;", "#FC6D26", "#fff"),
        "stackoverflow": ("&#128218;", "#F48024", "#fff"),
        "kaggle":        ("&#128200;", "#20BEFF", "#fff"),
    }

    def _source_badge_html(c: dict) -> str:
        src = (c.get("source") or "github").lower()
        icon, bg, fg = _SOURCE_BADGE_STYLE.get(src, ("&#9675;", "#888", "#fff"))
        label = src.replace("stackoverflow", "Stack Overflow").replace("gitlab", "GitLab").replace("github", "GitHub").replace("kaggle", "Kaggle")
        return (
            f'<span style="background:{bg};color:{fg};border-radius:4px;padding:1px 7px;'
            f'font-size:10px;font-weight:700">{icon} {label}</span>'
        )

    # Build compact table rows
    table_rows = ""
    for rank, c in enumerate(candidates, 1):
        username = c.get("github_username", "")
        score = c.get("sourcing_score", 0)
        score_color = "#16A34A" if score >= 60 else "#D97706" if score >= 40 else "#DC2626"
        profile_url = c.get("profile_url") or c.get("github_url") or f"https://github.com/{username}"
        resume_badge = _resume_badge_html(c)
        skill_match = _skill_match_html(c)
        source_badge = _source_badge_html(c)
        loc = c.get("location") or ""
        bio = (c.get("bio") or "")[:80]
        name = c.get("display_name") or username
        table_rows += (
            f'<tr style="vertical-align:top">'
            f'<td style="text-align:center;color:#97A0AF;font-size:13px;font-weight:600;padding:12px 8px;width:32px">#{rank}</td>'
            f'<td style="padding:12px 8px">'
            f'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:3px">'
            f'<b style="font-size:14px;color:#172B4D">{escape(name)}</b>'
            f'{source_badge}'
            f'{resume_badge}'
            f'</div>'
            f'{"<div style=\"font-size:11px;color:#97A0AF;margin-bottom:3px\">&#128205; " + escape(loc) + "</div>" if loc else ""}'
            f'<div style="font-size:12px;color:#5B6D83;line-height:1.4">{escape(bio)}</div>'
            f'</td>'
            f'<td style="padding:12px 8px;max-width:280px">{skill_match}</td>'
            f'<td style="text-align:center;padding:12px 8px">'
            f'<span style="font-size:20px;font-weight:900;color:{score_color}">{score}</span>'
            f'</td>'
            f'<td style="padding:12px 8px;white-space:nowrap">'
            f'<div style="display:flex;gap:6px;flex-direction:column">'
            f'<a href="/source/candidate/{username}" class="btn" style="font-size:11px;padding:5px 12px;white-space:nowrap">View</a>'
            f'<a href="{profile_url}" target="_blank" class="btn-sec" style="font-size:11px;padding:4px 10px;white-space:nowrap">Profile &#8599;</a>'
            f'</div>'
            f'</td>'
            f'</tr>'
        )

    table_html = (
        f'<div style="overflow-x:auto">'
        f'<table class="table" style="margin:0;border-collapse:separate;border-spacing:0">'
        f'<thead><tr>'
        f'<th style="width:32px">#</th>'
        f'<th>Candidate</th>'
        f'<th>Skills</th>'
        f'<th style="text-align:center">Score</th>'
        f'<th></th>'
        f'</tr></thead>'
        f'<tbody>{table_rows}</tbody>'
        f'</table>'
        f'</div>'
        if table_rows else
        "<div style='color:var(--text2);padding:20px'>No candidates in this sourcing run.</div>"
    )

    criteria_pills = (
        f'<span class="pill">Seniority: {criteria.get("seniority","")}</span>'
        + (f'<span class="pill">Location: {criteria.get("location","")}</span>' if criteria.get("location") else "")
        + "".join(f'<span class="pill">{s}</span>' for s in (criteria.get("skills") or [])[:6])
    )

    sidebar = _sidebar("sourcing", user)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Sourcing Results — Tvarah</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'>"
        f"<div class='wrap'>"
        f"<div style='margin-bottom:16px'>"
        f"<div class='kicker'><a href='/sourcing' style='color:var(--text2);text-decoration:none'>&#8592; Sourcing</a></div>"
        f"<h1 style='font-size:20px;font-weight:800;color:var(--text);margin:6px 0 4px'>{escape(query_text or job_id)}</h1>"
        f"<div style='display:flex;gap:6px;flex-wrap:wrap;margin-top:6px'>{criteria_pills}</div>"
        f"</div>"
        f"<div class='card' style='padding:0'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--border)'>"
        f"<div><span style='font-size:15px;font-weight:700;color:#172B4D'>{len(candidates)} candidates</span>"
        f"<span style='font-size:12px;color:#97A0AF;margin-left:8px'>ranked by fit score</span></div>"
        f"<a href='/sourcing' class='btn-sec' style='font-size:12px'>Back</a>"
        f"</div>"
        f"{table_html}"
        f"</div>"
        f"</div></div></div></body></html>"
    )


@app.get("/source/candidate/{github_username}", response_class=HTMLResponse)
def source_candidate_profile(github_username: str, user: dict = Depends(require_role("super_admin"))):
    from sourcing.sourcing_store import load_sourced_profile
    c = load_sourced_profile(github_username)
    if not c:
        raise HTTPException(status_code=404, detail="Sourced candidate not found")

    score = c.get("sourcing_score", 0)
    bd = c.get("score_breakdown") or {}
    stack = c.get("tech_stack") or []
    repos = c.get("top_repos") or []
    score_color = "#16A34A" if score >= 60 else "#D97706" if score >= 40 else "#DC2626"

    def _bar(label: str, val: int, max_val: int, color: str) -> str:
        pct = int(val / max_val * 100) if max_val else 0
        return (
            f'<div style="margin-bottom:10px">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
            f'<span style="font-size:12px;color:var(--text2)">{label}</span>'
            f'<span style="font-size:12px;font-weight:700">{val}/{max_val}</span>'
            f'</div>'
            f'<div style="height:7px;background:#E5E7EB;border-radius:4px;overflow:hidden">'
            f'<div style="height:100%;width:{pct}%;background:{color};border-radius:4px"></div>'
            f'</div></div>'
        )

    stack_html = "".join(f'<span class="pill">{t}</span>' for t in stack)
    topics = c.get("repo_topics") or []
    topics_html = "".join(
        f'<span style="background:#F0F0FB;color:var(--primary);border:1px solid var(--primary-border);border-radius:4px;padding:1px 7px;font-size:10px;margin:2px">{t}</span>'
        for t in topics[:12]
    )

    repos_html = ""
    for r in repos:
        lang_badge = (
            f'<span style="background:#F0F0FB;color:var(--primary);border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700">{r.get("language","")}</span>'
            if r.get("language") else ""
        )
        topic_badges = "".join(
            f'<span style="background:#F9FAFB;color:var(--text2);border-radius:3px;padding:0px 5px;font-size:10px">{t}</span>'
            for t in (r.get("topics") or [])[:3]
        )
        repos_html += (
            f'<div style="border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:8px">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap">'
            f'<a href="{r.get("url","#")}" target="_blank" style="font-weight:700;font-size:13px">{r.get("name","")}</a>'
            f'{lang_badge}'
            f'<span style="font-size:11px;color:var(--text2)">&#9733; {r.get("stars",0)}</span>'
            f'{topic_badges}'
            f'</div>'
            f'<div style="font-size:12px;color:var(--text2)">{r.get("description","")}</div>'
            f'</div>'
        )

    pipeline_status = c.get("pipeline_status", "sourced")
    if pipeline_status == "sourced":
        pipeline_btn = f'<button class="btn" onclick="addToPipeline(\'{github_username}\')" id="pipeBtn">&#43; Add to Pipeline</button>'
    else:
        pipeline_btn = '<span class="pill" style="background:#DCFCE7;color:#16A34A;border-color:#BBF7D0">&#10003; In Pipeline</span>'

    # Resume section
    resume_status = c.get("resume_status", "not_found")
    resume_pdf_url = c.get("resume_pdf_url") or ""
    resume_score = c.get("resume_score")
    resume_band = c.get("resume_band") or ""
    if resume_status == "analyzed":
        rscore_str = f" &bull; Score: {resume_score:.0f}" if resume_score else ""
        rband_str = f" &bull; Band: {resume_band}" if resume_band else ""
        resume_section = (
            f'<div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:8px;padding:12px 16px;margin-top:12px">'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="font-size:13px;font-weight:700;color:#16A34A">&#128196; Resume Analysed{rscore_str}{rband_str}</span>'
            f'<a href="/candidates/gh_{github_username}" class="btn" style="padding:5px 12px;font-size:11px">View Full Analysis</a>'
            f'<a href="{resume_pdf_url}" target="_blank" class="btn-sec" style="padding:4px 10px;font-size:11px">&#128196; Download PDF</a>'
            f'</div></div>'
        )
    elif resume_pdf_url:
        resume_section = (
            f'<div style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:8px;padding:12px 16px;margin-top:12px">'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="font-size:13px;font-weight:700;color:#2563EB">&#128196; Resume PDF Found</span>'
            f'<a href="{resume_pdf_url}" target="_blank" class="btn" style="padding:5px 12px;font-size:11px">&#11015; Download PDF</a>'
            f'</div></div>'
        )
    else:
        resume_section = ""

    # Skill match breakdown
    matched_skills = c.get("matched_skills") or []
    missing_skills = c.get("missing_skills") or []
    skill_match_html = ""
    if matched_skills or missing_skills:
        matched_badges = " ".join(
            f'<span style="background:#DCFCE7;color:#16A34A;border:1px solid #BBF7D0;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">&#10003; {s}</span>'
            for s in matched_skills
        )
        missing_badges = " ".join(
            f'<span style="background:#FEF2F2;color:#DC2626;border:1px solid #FECACA;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">&#10007; {s}</span>'
            for s in missing_skills
        )
        skill_match_html = (
            f'<div style="margin:10px 0">'
            f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--text2);margin-bottom:6px">Skill Match</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:4px">{matched_badges} {missing_badges}</div>'
            f'</div>'
        )

    avatar_url = c.get("avatar_url") or ""
    avatar_html = f'<img src="{avatar_url}" style="width:52px;height:52px;border-radius:50%;border:2px solid var(--border);flex-shrink:0">' if avatar_url else ""
    source = (c.get("source") or "github").lower()
    profile_url = c.get("profile_url") or c.get("github_url") or f"https://github.com/{github_username}"
    _src_meta = {
        "github":        ("&#9889; View on GitHub",     "#24292E", "#fff"),
        "gitlab":        ("&#9824; View on GitLab",     "#FC6D26", "#fff"),
        "stackoverflow": ("&#128218; Stack Overflow",   "#F48024", "#fff"),
        "kaggle":        ("&#128200; View on Kaggle",   "#20BEFF", "#fff"),
    }
    _plabel, _pbg, _pfg = _src_meta.get(source, ("&#9741; View Profile", "#353395", "#fff"))
    display_name = c.get("display_name") or github_username
    bio = c.get("bio") or ""
    location = c.get("location") or ""
    company = c.get("company") or ""
    email = c.get("email") or ""
    blog = c.get("blog") or ""
    followers = c.get("followers") or 0
    public_repos = c.get("public_repos") or 0
    total_stars = c.get("total_stars") or 0
    yoe_proxy = c.get("yoe_proxy") or 0
    loc_html = f"<span>&#128205; {location}</span>" if location else ""
    company_html = f"<span>&#127970; {company}</span>" if company else ""
    email_html = f"<span>&#9993; {email}</span>" if email else ""
    blog_html = f'<a href="{blog}" target="_blank" style="color:var(--primary2)">&#127760; {blog}</a>' if blog else ""
    no_repos_html = '<div style="color:var(--text2)">No repositories found.</div>'

    bars_html = (
        _bar("Skill Match", bd.get("skill_match", 0), 40, "var(--primary)")
        + _bar("Activity", bd.get("activity", 0), 25, "#16A34A")
        + _bar("Seniority Proxy", bd.get("seniority", 0), 20, "#6366F1")
        + _bar("Profile Completeness", bd.get("completeness", 0), 15, "#D97706")
    )

    sidebar = _sidebar("sourcing", user)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{display_name} \u2014 Tvarah</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>{_BASE_CSS}</head><body>"
        f"<div class='app-shell'>{sidebar}<div class='main'>"
        f"<div class='wrap'>"
        f"<div style='margin-bottom:16px'>"
        f"<div class='kicker'><a href='/sourcing' style='color:var(--text2)'>Sourcing</a> &rsaquo; Candidate</div>"
        f"</div>"
        f"<div class='card'>"
        f"<div style='display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;margin-bottom:16px'>"
        f"<div style='flex:1;min-width:240px'>"
        f"<div style='display:flex;align-items:center;gap:12px;margin-bottom:8px'>"
        f"{avatar_html}"
        f"<div>"
        f"<div style='font-size:20px;font-weight:800'>{display_name}</div>"
        f"<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px'>"
        f"<a href='{profile_url}' target='_blank' style='display:inline-flex;align-items:center;gap:5px;background:{_pbg};color:{_pfg};border-radius:6px;padding:4px 10px;font-size:12px;font-weight:600;text-decoration:none'>{_plabel}</a>"
        f"{blog_html}"
        f"</div></div>"
        f"</div>"
        f"<div style='font-size:13px;color:var(--text2);margin-bottom:6px'>{bio}</div>"
        f"<div style='font-size:13px;color:var(--text2);display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px'>"
        f"{loc_html}{company_html}{email_html}"
        f"<span>&#128101; {followers} followers</span>"
        f"<span>&#128193; {public_repos} repos</span>"
        f"<span>&#9733; {total_stars} stars</span>"
        f"</div>"
        f"{skill_match_html}"
        f"<div style='margin-bottom:8px'>{stack_html}</div>"
        f"{resume_section}"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:12px'>"
        f"{pipeline_btn}"
        f"<button class='btn-sec' onclick=\"generateOutreach('{github_username}')\" id='outreachBtn'>&#9993; Generate Outreach Email</button>"
        f"</div>"
        f"</div>"
        f"<div style='min-width:200px;max-width:240px'>"
        f"<div style='font-size:38px;font-weight:900;color:{score_color};text-align:center'>{score}</div>"
        f"<div style='font-size:10px;text-align:center;text-transform:uppercase;color:var(--text2);margin-bottom:14px'>Sourcing Score</div>"
        f"{bars_html}"
        f"<div style='font-size:11px;color:var(--text2);margin-top:4px'>~{yoe_proxy:.0f} years est. experience</div>"
        f"</div>"
        f"</div>"
        f"{'<div style=\"margin-bottom:10px\"><div class=\"kicker\">Repo Topics</div>' + topics_html + '</div>' if topics_html else ''}"
        f"<h3 style='font-size:14px;font-weight:700;margin-bottom:10px'>Top Repositories</h3>"
        f"{repos_html if repos_html else no_repos_html}"
        f"</div>"
        f"<!-- Outreach Modal -->"
        f"<div id='outreachModal' style='display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:200;align-items:center;justify-content:center'>"
        f"<div style='background:var(--white);border-radius:14px;padding:24px;max-width:560px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.25)'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:14px'>"
        f"<h3 style='font-size:16px;font-weight:700'>Outreach Email</h3>"
        f"<button onclick=\"document.getElementById('outreachModal').style.display='none'\" style='background:none;border:none;font-size:20px;cursor:pointer;color:var(--text2)'>&times;</button>"
        f"</div>"
        f"<div id='outreachContent'><div style='text-align:center;color:var(--text2)'>Generating...</div></div>"
        f"</div></div>"
        f"<script>"
        f"async function generateOutreach(username){{"
        f"  const modal=document.getElementById('outreachModal');"
        f"  modal.style.display='flex';"
        f"  document.getElementById('outreachContent').innerHTML='<div style=\"text-align:center;color:var(--text2)\">Generating personalised email...</div>';"
        f"  const btn=document.getElementById('outreachBtn');"
        f"  btn.disabled=true;"
        f"  try{{"
        f"    const res=await fetch('/source/outreach/'+username,{{method:'POST'}});"
        f"    const data=await res.json();"
        f"    if(!res.ok){{document.getElementById('outreachContent').innerHTML='<div style=\"color:var(--red)\">Error: '+(data.detail||res.statusText)+'</div>';return;}}"
        f"    document.getElementById('outreachContent').innerHTML="
        f"      '<div class=\"form-group\" style=\"margin-bottom:12px\"><div class=\"form-label\">Subject</div>"
        f"      <input class=\"form-input\" value=\"'+data.subject.replace(/\"/g,'&quot;')+'\" id=\"subjectLine\" style=\"width:100%\"></div>"
        f"      <div class=\"form-group\"><div class=\"form-label\">Body</div>"
        f"      <textarea class=\"form-input\" rows=\"8\" id=\"emailBody\" style=\"width:100%\">'+data.body+'</textarea></div>"
        f"      <button class=\"btn\" onclick=\"copyEmail()\" style=\"margin-top:8px\">&#128203; Copy to Clipboard</button>';"
        f"  }}finally{{btn.disabled=false;}}"
        f"}}"
        f"function copyEmail(){{"
        f"  const subject=document.getElementById('subjectLine')?.value||'';"
        f"  const body=document.getElementById('emailBody')?.value||'';"
        f"  navigator.clipboard.writeText('Subject: '+subject+'\\n\\n'+body).then(()=>alert('Copied!'));"
        f"}}"
        f"async function addToPipeline(username){{"
        f"  const btn=document.getElementById('pipeBtn');"
        f"  if(btn)btn.disabled=true;"
        f"  const res=await fetch('/source/pipeline/'+username,{{method:'POST'}});"
        f"  const data=await res.json();"
        f"  if(res.ok){{"
        f"    if(btn)btn.outerHTML='<span class=\"pill\" style=\"background:#DCFCE7;color:#16A34A;border-color:#BBF7D0\">&#10003; In Pipeline</span>';"
        f"    if(data.candidate_id)window.open('/candidates/'+data.candidate_id,'_blank');"
        f"  }}else{{if(btn)btn.disabled=false;alert('Error: '+(data.detail||res.statusText));}}"
        f"}}"
        f"</script>"
        f"</div></div></body></html>"
    )


@app.post("/source/outreach/{github_username}")
def source_outreach(github_username: str, jd_id: str | None = None):
    """Generate personalized outreach email for a sourced candidate."""
    from sourcing.sourcing_store import load_sourced_profile
    from sourcing.outreach_engine import generate_outreach

    c = load_sourced_profile(github_username)
    if not c:
        raise HTTPException(status_code=404, detail="Sourced candidate not found")

    jd: dict | None = None
    if jd_id:
        try:
            from job_posting_store import load_job_posting
            jd = load_job_posting(jd_id)
        except Exception:
            pass

    result = generate_outreach(c, jd=jd)
    return result


@app.post("/source/pipeline/{github_username}")
def source_add_to_pipeline(github_username: str):
    """Create a stub Tvarah candidate from a sourced GitHub profile."""
    from sourcing.sourcing_store import load_sourced_profile, update_pipeline_status
    from datetime import datetime, timezone

    c = load_sourced_profile(github_username)
    if not c:
        raise HTTPException(status_code=404, detail="Sourced candidate not found")

    candidate_id = f"gh_{github_username}"
    now = datetime.now(timezone.utc).isoformat()

    # Build a minimal Tvarah-compatible analysis stub
    stub = {
        "candidate_id": candidate_id,
        "candidate_name": c.get("display_name") or github_username,
        "source": "github_sourcing",
        "candidate_overview": {
            "name": c.get("display_name") or github_username,
            "email": c.get("email") or "",
            "location": c.get("location") or "",
            "summary": c.get("bio") or "",
        },
        "github_profile": {
            "username": github_username,
            "url": c.get("github_url") or f"https://github.com/{github_username}",
            "tech_stack": c.get("tech_stack") or [],
            "top_repos": c.get("top_repos") or [],
            "followers": c.get("followers") or 0,
            "public_repos": c.get("public_repos") or 0,
            "total_stars": c.get("total_stars") or 0,
        },
        "sourcing_metadata": {
            "sourcing_score": c.get("sourcing_score"),
            "score_breakdown": c.get("score_breakdown"),
            "yoe_proxy": c.get("yoe_proxy"),
            "sourcing_job_id": c.get("sourcing_job_id"),
        },
        "rubric_scorecard": {
            "overall_band": "",
            "stage_scores": {"resume_score_100": c.get("sourcing_score")},
        },
        "experience_analysis": {
            "total_experience_years": c.get("yoe_proxy") or 0,
        },
        "pipeline_status": "sourced_candidate",
        "created_at": now,
    }

    try:
        from candidate_analysis_store import save_candidate_analysis
        save_candidate_analysis(candidate_id, stub)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create candidate: {exc}")

    update_pipeline_status(github_username, "in_pipeline")

    return {"candidate_id": candidate_id, "message": "Candidate added to pipeline"}


# ---------------------------------------------------------------------------
# Document downloads
# ---------------------------------------------------------------------------

@app.get("/sourcing/download/resume")
def download_perfect_resume_docx():
    """Download the sample 100/100 resume as a Word document."""
    from sourcing.doc_generators import build_perfect_resume_docx
    content = build_perfect_resume_docx()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=Arjun_Mehta_Resume_100.docx"},
    )


@app.get("/sourcing/download/resume-pdf")
def download_perfect_resume_pdf():
    """Download the sample 100/100 resume as a PDF."""
    from sourcing.doc_generators import build_perfect_resume_pdf
    content = build_perfect_resume_pdf()
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Arjun_Mehta_Resume_100.pdf"},
    )


@app.get("/sourcing/download/guide")
def download_analysis_guide_docx():
    """Download the Tvarah resume analysis parameter guide as a Word document."""
    from sourcing.doc_generators import build_analysis_guide_docx
    content = build_analysis_guide_docx()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=Tvarah_Resume_Analysis_Guide.docx"},
    )
