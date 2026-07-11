"""40 + 45 + 15 rubric scoring engine (3-stage: Resume → Recruiter → Panel).

Experience section (40 pts):
  ── 5 core scoring items (22 pts) ──
  overall_experience       4 pts   — years of experience (band-scored; JD-calibrated if YoE range set)
  career_progression       4 pts   — seniority trajectory (title-level growth)
  stability                4 pts   — avg tenure, loyalty signal, churn penalty
  company_tier             6 pts   — companies worked with (FAANG=6 … unknown=1)
  awards_recognition       4 pts   — achievements, commendations, honours
  ── penalty param ──
  career_breaks            2 pts   — duration-banded: 0 breaks=2, ≤6m=1.5, ≤12m=1.0, >12m=0.5, 2 breaks=0.5, 3+=0
  ── recruiter-stage params (7 pts) ──
  international_exposure   2 pts   — onsite, global teams, multi-country
  stakeholder_management   2 pts   — client-facing, cross-functional work
  mentorship_signal        3 pts   — lead/owned/mentored signals
  ── projects (16 pts) ──
  project_1               10 pts   — type + role + problem + duration + skills + domain + depth + impact
  project_2                6 pts   — type + role + problem + duration + skills + domain
  TOTAL                   47 → normalised to 40 at stage

Skills section (45 pts):
  skill_list_years         6 pts   — count of APPLIED/DEEP/EXPERT skills
  skill_depth (BERT)       8 pts   — BERT-primary blended depth score
  skill_recency            6 pts   — % of skills with RECENT/CURRENT evidence
  yoy_learning             3 pts   — year-on-year new skill acquisition rate
  certifications           3 pts   — relevant professional certifications
  coding_community         3 pts   — open-source / coding platform signal
  communication_skills     5 pts   — Panel stage only: verbal/written clarity
  domain_skills            5 pts   — Panel stage only: domain-specific knowledge depth
  project_explanation      3 pts   — Panel stage only: project walk-through quality
  problem_solving          3 pts   — Panel stage only: live problem-solving ability
  TOTAL                   45 (resume=16; panel adds up to 16)

Education section (15 pts):
  Core (10 pts):
    institute_tier         5 pts   — TIER_1=4+1GPA, TIER_2=3+0.5GPA, TIER_3=2, TIER_4=1, UNKNOWN=1
    degree_level           2 pts   — PhD/Master=2, Bachelor=1.5, Diploma=1, Unknown=0.5
    education_job_relevance 2 pts  — HIGH=2, MEDIUM=1.5, FOUNDATIONAL=0.5, UNKNOWN=1
    education_gap          1 pt    — ≤6m=1, 6-12m=0.5, >12m=0
  Bonus (5 pts):
    exec_education         1 pt    — continuing/executive/distance education
    patents_publications   2 pts   — patents or publications signal
    linkedin_activity      1 pt    — Pending recruiter stage
    extra_curriculars      1 pt    — Pending recruiter stage
  TOTAL                   15

Grand total: 100 pts
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any

from company_tier_taxonomy import classify_company_tier, tier_to_points

# ---------------------------------------------------------------------------
# BERT depth → numeric score mapping (primary accuracy engine)
# ---------------------------------------------------------------------------
BERT_DEPTH_TO_SCORE: dict[str, float] = {
    "AWARENESS": 0.5,
    "FOUNDATIONAL": 1.5,
    "HANDS_ON": 3.0,
    "ADVANCED": 4.0,
    "ARCHITECT_LEVEL": 5.0,
}

EVIDENCE_LEVEL_TO_SCORE: dict[str, float] = {
    "NONE": 0.0,
    "MENTION": 0.5,
    "WEAK": 1.5,
    "APPLIED": 3.0,
    "DEEP": 4.0,
    "EXPERT": 5.0,
}

# ---------------------------------------------------------------------------
# Archetype Detection & Dynamic Weight Reallocation
# ---------------------------------------------------------------------------

# Default section maxes — denominators for normalising before dynamic reallocation
_BASE_MAXES: dict[str, float] = {"edu": 15.0, "exp": 40.0, "skills": 45.0}

# Archetype weight table — each row sums to 100
# Archetype coding follows EDGE_CASES_AND_RED_FLAGS.md Section 1
WEIGHT_TABLE: dict[str, dict[str, float]] = {
    "A1":  {"edu": 15.0, "exp": 40.0, "skills": 45.0},   # Baseline (no reallocation)
    "A2":  {"edu":  5.0, "exp": 52.0, "skills": 43.0},   # Weak edu + FAANG
    "A3":  {"edu":  8.0, "exp": 47.0, "skills": 45.0},   # Weak edu + mid-tier
    "A4":  {"edu": 20.0, "exp": 37.0, "skills": 43.0},   # Elite edu + weak company
    "A5":  {"edu": 30.0, "exp": 10.0, "skills": 60.0},   # Fresh graduate (≤1 yr)
    "A6":  {"edu":  8.0, "exp": 47.0, "skills": 45.0},   # Senior 10+ YoE
    "A7":  {"edu": 25.0, "exp": 30.0, "skills": 45.0},   # PhD / Research track
    "A8":  {"edu":  5.0, "exp": 45.0, "skills": 50.0},   # Domain switcher
    "A9":  {"edu": 10.0, "exp": 42.0, "skills": 48.0},   # Founder / serial entrepreneur
    "A10": {"edu": 12.0, "exp": 40.0, "skills": 48.0},   # Consultant / contractor
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _param(score: float, max_pts: float, reason: str, **extra: Any) -> dict[str, Any]:
    """Standard sub-param dict with score, max, and recruiter reason."""
    # Use 2dp to preserve values like 1.25, 1.75 (banker's rounding would mangle them at 1dp)
    rounded = round(score, 2)
    # Drop trailing zero: 1.50 → 1.5, 1.00 → 1.0, but keep 1.25
    display = float(f"{rounded:.2f}".rstrip('0').rstrip('.') or '0')
    return {"score": display, "max": max_pts, "reason": reason, **extra}


# ---------------------------------------------------------------------------
# BERT blending for skill depth (primary accuracy engine)
# ---------------------------------------------------------------------------

def _bert_adjust_skill_depth(
    evidence_score_0_to_5: float,
    bert_prior_item: dict[str, Any] | None,
    evidence_level: str | None = None,
) -> float:
    """Blend evidence-based score with BERT depth prior.

    Confidence >= 0.65 → BERT primary (65 % BERT + 35 % evidence).
    Confidence 0.45–0.64 → equal blend.
    Confidence < 0.45 → evidence only.

    Guard: if BERT predicts AWARENESS but evidence_level is APPLIED/DEEP/EXPERT,
    downweight BERT confidence to 0.3 so evidence wins (prevents false AWARENESS on senior skills).
    """
    if not bert_prior_item:
        return evidence_score_0_to_5
    label = str(
        bert_prior_item.get("predicted_depth_label")
        or bert_prior_item.get("bert_depth_prior")
        or ""
    ).upper()
    confidence = _safe_float(
        bert_prior_item.get("bert_depth_confidence")
        or bert_prior_item.get("confidence"),
        0.0,
    )
    # Guard: BERT says AWARENESS but strong evidence contradicts it → evidence wins
    strong_evidence = str(evidence_level or "").upper() in {"APPLIED", "DEEP", "EXPERT"}
    if label == "AWARENESS" and strong_evidence:
        confidence = min(confidence, 0.3)

    bert_score = BERT_DEPTH_TO_SCORE.get(label)
    if bert_score is None:
        return evidence_score_0_to_5
    if confidence >= 0.65:
        return bert_score * 0.65 + evidence_score_0_to_5 * 0.35
    if confidence >= 0.45:
        return bert_score * 0.5 + evidence_score_0_to_5 * 0.5
    return evidence_score_0_to_5


# ---------------------------------------------------------------------------
# Career break detection
# ---------------------------------------------------------------------------

def _parse_ym(date_str: str) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%m/%Y", "%Y"):
        try:
            dt = datetime.strptime(date_str.strip()[:10], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _month_diff_dt(start: datetime, end: datetime) -> float:
    if end <= start:
        return 0.0
    return (end.year - start.year) * 12 + (end.month - start.month)


def _is_edu_overlap(
    gap_start: datetime,
    gap_end: datetime,
    edu_periods: list[tuple[datetime, datetime]],
) -> bool:
    """Return True if the gap overlaps any education period by ≥ 50% (E4 helper)."""
    gap_len = _month_diff_dt(gap_start, gap_end)
    if gap_len <= 0:
        return False
    for edu_start, edu_end in edu_periods:
        overlap_start = max(gap_start, edu_start)
        overlap_end = min(gap_end, edu_end)
        if overlap_end > overlap_start:
            overlap_months = _month_diff_dt(overlap_start, overlap_end)
            if overlap_months / gap_len >= 0.5:
                return True
    return False


def _detect_career_breaks(tenure_with_dates: list[dict[str, Any]], education_entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Count non-education gaps > 3 months between consecutive jobs, PLUS the
    gap (if any) between the most recent job's end and today.

    E4: Gaps that overlap an education entry (≥50% overlap) are excluded from
        break_count and returned in edu_breaks — "educational gap, verified."
    E5: Gaps > 3m and ≤ 18m that are not educational are classified as
        possible_parental_breaks (soft flag, no score penalty).

    A candidate whose most recent listed role already ended (an explicit
    past end date, not "present"/ongoing) with no newer entry is currently
    between jobs -- at least as decision-relevant as a gap between two past
    jobs, but the original consecutive-pairs loop never reaches it since
    there's no "next" entry to compare against. Reuses the exact same
    thresholds/classification as inter-job gaps rather than introducing a
    separate rule. An entry with no parseable end date already defaults to
    "now" below (i.e. still ongoing), so this naturally produces a ~0-month
    gap and never fires for genuinely current roles.

    Returns: break_count (hard breaks only), breaks, reject (True if > 2 hard
             breaks), edu_breaks (E4), possible_parental_breaks (E5).
    """
    _empty: dict[str, Any] = {
        "break_count": 0, "breaks": [], "reject": False,
        "edu_breaks": [], "possible_parental_breaks": [], "overlaps": [],
    }
    if not tenure_with_dates:
        return _empty

    now = datetime.now(timezone.utc)
    dated = []
    for entry in tenure_with_dates:
        start = _parse_ym(entry.get("start") or "")
        end = _parse_ym(entry.get("end") or "")
        if start:
            dated.append({
                "start": start,
                "end": end or now,
                "company": entry.get("company", ""),
            })
    if not dated:
        return _empty

    dated.sort(key=lambda x: x["start"])

    # Build education periods for E4 cross-reference
    edu_periods: list[tuple[datetime, datetime]] = []
    for e in (education_entries or []):
        e_start = _parse_ym(
            e.get("start_date") or e.get("from") or e.get("education_start_date") or ""
        )
        e_end = _parse_ym(
            e.get("end_date") or e.get("to") or e.get("graduation_date") or
            e.get("education_end_date") or e.get("passing_year") or ""
        )
        if e_start:
            edu_periods.append((e_start, e_end or now))

    breaks: list[dict[str, Any]] = []
    edu_breaks: list[dict[str, Any]] = []
    possible_parental_breaks: list[dict[str, Any]] = []

    def _classify_gap(gap_start: datetime, gap_end: datetime, after_company: str, before_company: str) -> None:
        gap_months = _month_diff_dt(gap_start, gap_end)
        if gap_months <= 3:
            return
        entry_dict: dict[str, Any] = {
            "after_company": after_company,
            "before_company": before_company,
            "gap_months": round(gap_months, 1),
            "gap_start": gap_start.strftime("%Y-%m"),
            "gap_end": gap_end.strftime("%Y-%m"),
        }
        # E4: Gap overlaps education → educational break, exclude from penalty
        if edu_periods and _is_edu_overlap(gap_start, gap_end, edu_periods):
            edu_breaks.append({**entry_dict, "reason": "educational"})
            return
        # E5: Short gap (> 3m and ≤ 18m) → possible parental/personal — soft flag, no score penalty
        if gap_months <= 18:
            possible_parental_breaks.append({**entry_dict, "reason": "possible_parental"})
            return
        # Hard break: gap > 18m, not educational
        breaks.append(entry_dict)

    for i in range(len(dated) - 1):
        _classify_gap(dated[i]["end"], dated[i + 1]["start"], dated[i]["company"], dated[i + 1]["company"])

    # Gap between the most recent job's end and today.
    latest = dated[-1]
    _classify_gap(latest["end"], now, latest["company"], "present day (not yet re-employed)")

    # Overlapping employment -- two roles active at the same time. An exact
    # duplicate (same company, byte-identical start AND end dates listed as
    # if they were two separate roles/projects) is a much stronger padding
    # signal than an ordinary few-week handoff overlap between two different
    # employers, so it's folded into the same `breaks` bucket (counts toward
    # break_count/reject) rather than only being a soft note. This mirrors
    # jd_matching/integrity.py's overlap check, which this rubric-scoring
    # path previously had no equivalent of at all.
    overlaps: list[dict[str, Any]] = []
    for i in range(len(dated)):
        for j in range(i + 1, len(dated)):
            a, b = dated[i], dated[j]
            if a["start"] >= b["end"] or b["start"] >= a["end"]:
                continue
            overlap_months = _month_diff_dt(max(a["start"], b["start"]), min(a["end"], b["end"]))
            if overlap_months <= 2:
                continue
            is_duplicate = (
                a["company"].strip().lower() == b["company"].strip().lower()
                and a["start"] == b["start"] and a["end"] == b["end"]
            )
            overlap_entry = {
                "company_a": a["company"], "company_b": b["company"],
                "overlap_months": round(overlap_months, 1), "duplicate": is_duplicate,
            }
            overlaps.append(overlap_entry)
            if is_duplicate:
                breaks.append({
                    "after_company": a["company"], "before_company": b["company"],
                    "gap_months": round(overlap_months, 1),
                    "gap_start": a["start"].strftime("%Y-%m"), "gap_end": a["end"].strftime("%Y-%m"),
                    "reason": "duplicate_employment_entry",
                })

    break_count = len(breaks)
    return {
        "break_count": break_count,
        "breaks": breaks,
        "reject": break_count > 2,
        "edu_breaks": edu_breaks,
        "possible_parental_breaks": possible_parental_breaks,
        "overlaps": overlaps,
    }


def detect_archetype(experience: dict[str, Any], education: dict[str, Any]) -> str:
    """Detect candidate archetype A1–A10 from experience + education signals.

    Priority order (first match wins):
      A5  → Fresh grad (≤1 yr or single job)
      A7  → PhD / 2+ publications
      A9  → Serial founder (2+ Founder/CEO entries)
      A8  → Domain switcher with 3+ YoE
      A6  → Senior (10+ YoE)
      A10 → Consultant/contractor pattern (3+ short stints <18m + keyword)
      A2  → Weak edu (TIER_4+/UNKNOWN) + FAANG (TIER_1)
      A3  → Weak edu (TIER_4+/UNKNOWN) + mid-tier (TIER_2)
      A4  → Elite edu (TIER_1) + weak company (TIER_3+)
      A1  → Baseline (default)

    See EDGE_CASES_AND_RED_FLAGS.md Section 1 for full archetype definitions.
    """
    yoe = _safe_float(experience.get("total_experience_years"), 0.0)
    titles_raw: list = experience.get("titles") or experience.get("job_titles") or []
    titles_text = " ".join(str(t) for t in titles_raw).lower()
    companies: list = experience.get("companies") or []
    tenures: list = experience.get("tenures") or []

    # Best company tier across all employers
    best_tier = 5
    for c in companies:
        t = classify_company_tier(str(c or ""), llm_fallback=False)
        if t < best_tier:
            best_tier = t

    # Education signals
    edu_tier_str = str(education.get("highest_institute_tier") or "UNKNOWN").upper()
    _edu_num = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3, "TIER_4": 4, "TIER_5": 5}
    edu_tier_num = _edu_num.get(edu_tier_str, 5)

    edu_entries: list = education.get("education_entries") or []
    degree_text = " ".join(
        str(e.get("degree_level_tag") or e.get("degree") or "") for e in edu_entries
    ).lower()
    is_phd = any(kw in degree_text for kw in ("phd", "ph.d", "doctor", "doctoral"))
    pub_count = len(experience.get("publications") or [])

    # Founder detection — count distinct Founder/CEO title entries
    _founder_kws = ("founder", "co-founder", "cofounder")
    founder_title_count = sum(
        1 for t in titles_raw if any(kw in str(t).lower() for kw in _founder_kws)
    )

    # Domain switch: education relevance from education engine
    edu_relevance = str(education.get("education_job_relevance") or "UNKNOWN").upper()
    is_domain_switcher = edu_relevance in ("FOUNDATIONAL", "UNKNOWN_DOMAIN") and yoe >= 3.0

    # Consultant/contractor: 3+ stints < 18m AND role keyword match
    short_stints_18 = sum(1 for m in tenures if 0 < m < 18)
    is_consultant_pattern = (
        short_stints_18 >= 3
        and len(tenures) >= 3
        and any(kw in titles_text for kw in ("consultant", "contractor", "freelance", "contract", "advisor"))
    )

    n_jobs = len(tenures)

    # --- Priority matching (first match wins) ---
    # A5: Fresh grad — ≤1 yr YoE, OR single very-short job (E18: resume with 1 job, very short)
    if yoe <= 1.0 or (n_jobs <= 1 and yoe <= 2.0):
        return "A5"
    if is_phd or pub_count >= 2:
        return "A7"
    if founder_title_count >= 2:
        return "A9"
    if is_domain_switcher:
        return "A8"
    if yoe >= 10.0:
        return "A6"
    if is_consultant_pattern:
        return "A10"
    if edu_tier_num >= 4 and best_tier == 1:
        return "A2"
    if edu_tier_num >= 4 and best_tier == 2:
        return "A3"
    if edu_tier_num == 1 and best_tier >= 3:
        return "A4"
    return "A1"


# ---------------------------------------------------------------------------
# Project scoring — 6 pts (1 pt per criterion)
# ---------------------------------------------------------------------------

def _score_project(
    project_item: dict[str, Any],
    experience_data: dict[str, Any],
    evidence_map: dict[str, Any],
    max_score: int = 6,
) -> dict[str, Any]:
    """Score one project 0–max_score. Base criteria (6): type, title, description, duration, skills, domain.
    When max_score>=8, two extra criteria are evaluated: role depth and quantified impact.
    When max_score>=10, two more criteria: cross-functional involvement and technical complexity."""
    score = 0
    criteria: list[str] = []
    missing: list[str] = []

    # 1. Known project type
    ptype = str(project_item.get("project_type") or "").upper()
    if ptype not in ("", "UNKNOWN"):
        score += 1
        criteria.append(f"type={ptype}")
    else:
        missing.append("project_type unknown")

    # 2. Title / role present
    title = str(project_item.get("title") or project_item.get("role") or "").strip()
    if title:
        score += 1
        criteria.append(f"title='{title}'")
    else:
        missing.append("no role/title")

    # 3. Description / problem statement (>20 chars)
    description = str(project_item.get("description") or project_item.get("problem") or "").strip()
    if len(description) > 20:
        score += 1
        criteria.append("description present")
    else:
        missing.append("no description")

    # 4. Duration >= 3 months
    start_dt = _parse_ym(str(project_item.get("start_date") or project_item.get("from") or ""))
    raw_end = str(project_item.get("end_date") or project_item.get("to") or "")
    end_dt = _parse_ym(raw_end) if raw_end else datetime.now(timezone.utc)
    if start_dt and end_dt:
        months = _month_diff_dt(start_dt, end_dt)
        if months >= 3:
            score += 1
            criteria.append(f"duration={months:.0f}m")
        else:
            missing.append(f"short duration ({months:.0f}m)")
    else:
        # Dates not parsed — benefit of the doubt for senior profiles
        score += 1
        criteria.append("duration assumed OK (dates unparseable)")

    # 5. Skills listed
    skills = project_item.get("skills") or []
    if isinstance(skills, list) and len(skills) >= 1:
        score += 1
        criteria.append(f"{len(skills)} skill(s)")
    else:
        missing.append("no skills listed")

    # 6. Domain tag present
    company = str(project_item.get("company") or "").lower()
    domain_tags = experience_data.get("domain_tags") or []
    has_domain = bool(domain_tags)
    if not has_domain:
        for cp in experience_data.get("company_profiles") or []:
            if str(cp.get("company") or "").lower() == company and cp.get("domain", "UNKNOWN") != "UNKNOWN":
                has_domain = True
                break
    if has_domain:
        score += 1
        criteria.append("domain tag present")
    else:
        missing.append("no domain tag")

    # 7 & 8. Extra criteria for 8-pt+ projects
    if max_score >= 8:
        import re as _re
        # 7. Role depth: description >50 chars AND contains ownership verb
        _depth_verbs = {
            "built", "designed", "led", "architected", "developed", "owned",
            "implemented", "optimised", "optimized", "created", "deployed",
            "delivered", "launched", "migrated", "scaled", "managed", "drove",
            "spearheaded", "established", "engineered", "integrated", "automated",
            "transformed", "refactored", "streamlined",
        }
        desc_lower = description.lower()
        has_depth = len(description) > 50 and any(v in desc_lower for v in _depth_verbs)
        if has_depth:
            score += 1
            criteria.append("role depth (ownership verb + length)")
        else:
            missing.append("no clear role depth (need >50 chars + ownership verb)")

        # 8. Quantified impact: number or % adjacent to outcome word
        _outcome_words = {
            "reduced", "increased", "improved", "saved", "accelerated", "grew",
            "delivered", "achieved", "deployed", "migrated", "scaled", "optimized",
            "automated", "streamlined", "enhanced", "boosted", "eliminated",
        }
        has_impact = (
            bool(_re.search(r"\d+\s*%", description))
            or (bool(_re.search(r"\d+", description)) and any(w in desc_lower for w in _outcome_words))
            or len(_re.findall(r"\b\d{2,}\b", description)) >= 3
        )
        if has_impact:
            score += 1
            criteria.append("quantified impact")
        else:
            missing.append("no quantified impact (need number/% near outcome word)")

    # 9 & 10. Extra criteria for 10-pt projects
    if max_score >= 10:
        import re as _re2
        title_lower = title.lower()
        desc_and_title = description.lower() + " " + title_lower

        # Senior titles imply cross-functional scope by definition
        _seniority_titles = {
            "vice president", "vp ", " vp", "director", "head of", "global lead",
            "global head", "chief", "cto", "coo", "ceo", "cdo", "svp", "evp",
            "managing director", "principal", "fellow",
        }
        _collab_words = {
            "team", "stakeholder", "cross-functional", "cross functional", "collaboration",
            "collaborated", "coordinated", "partnered", "cross-team", "product manager",
            "business analyst", "worked with", "aligned with", "global", "regional",
            "multi-region", "enterprise", "org", "organization", "leadership",
        }
        has_collab = (
            any(w in description.lower() for w in _collab_words)
            or any(w in title_lower for w in _collab_words)
            or any(w in title_lower for w in _seniority_titles)
        )
        if has_collab:
            score += 1
            criteria.append("cross-functional involvement")
        else:
            missing.append("no cross-functional involvement signals")

        # 10. Technical complexity / scale — check title + description
        _complexity_words = {
            "architecture", "distributed", "microservice", "microservices", "pipeline",
            "infrastructure", "scalable", "high-availability", "kubernetes", "cloud",
            "million", "billion", "terabyte", "latency", "throughput", "real-time",
            "streaming", "batch", "etl", "mlops", "system design",
            # Operations / platform / process signals for senior roles
            "platform", "platforms", "process", "operations", "ocean", "supply chain",
            "transformation", "modernization", "modernisation", "programme", "program",
            "fleet", "logistics", "enterprise", "end-to-end", "e2e", "integration",
            "rollout", "deployment", "scale", "global", "regional", "multi-country",
        }
        has_complexity = (
            any(w in description.lower() for w in _complexity_words)
            or any(w in title_lower for w in _complexity_words)
        )
        if has_complexity:
            score += 1
            criteria.append("technical complexity/scale")
        else:
            missing.append("no technical complexity or scale signals")

    if score >= max_score - 1:
        reason = f"Strong project evidence ({', '.join(criteria)}). Full marks or near-full."
    elif score >= max_score // 2:
        reason = f"Moderate project evidence ({', '.join(criteria)}). Missing: {', '.join(missing)}."
    else:
        reason = f"Thin project evidence. Only {score}/{max_score} criteria met. Missing: {', '.join(missing)}."

    return {"score": score, "max": max_score, "reason": reason, "criteria_met": criteria, "criteria_missing": missing}


# ---------------------------------------------------------------------------
# Role-specific skill weighting
# ---------------------------------------------------------------------------

def _apply_role_skill_weights(
    skill_evidence_rows: list[dict[str, Any]],
    client_role_config: dict[str, Any],
) -> float:
    """Compute a 0–10 weighted skill depth score using mandatory + good-to-have weights."""
    mandatory = client_role_config.get("mandatory_skills") or []
    good_to_have = client_role_config.get("good_to_have_skills") or []
    skill_lookup = {str(r.get("skill") or "").lower(): r for r in skill_evidence_rows if r.get("skill")}

    weighted_sum = 0.0
    max_possible = 0.0
    for entry in mandatory + good_to_have:
        skill_name = str(entry.get("skill") or "").lower()
        weight = _safe_float(entry.get("weight"), 1.0)
        max_possible += weight * 5.0
        row = skill_lookup.get(skill_name)
        if row:
            evidence_level_str = str(row.get("evidence_level") or "NONE").upper()
            evidence_score = EVIDENCE_LEVEL_TO_SCORE.get(evidence_level_str, 0.0)
            weighted_sum += weight * _bert_adjust_skill_depth(evidence_score, row, evidence_level_str)

    if max_possible <= 0:
        return 5.0
    return _clamp((weighted_sum / max_possible) * 10.0, 0.0, 10.0)


# ---------------------------------------------------------------------------
# LLM-enhanced project scoring helpers
# ---------------------------------------------------------------------------

def _build_project_reason(
    project_item: dict[str, Any],
    llm_judgment: dict[str, Any],
    base_result: dict[str, Any],
) -> str:
    """Build a multi-line recruiter-readable project reason string.

    Example output:
      "Production ML platform at Flipkart (Tier 2, 2020–2022). LLM: DEVELOPMENT confirmed.
       Implied: Kafka, Spark, Airflow. Claimed verified: PySpark, Airflow.
       Gap: no A/B testing framework mentioned despite recommendation context.
       Complexity: 4.2/5. Probe: 'Walk us through the full lifecycle of your model.'"
    """
    company = str(project_item.get("company") or "").strip() or "Unknown company"
    title = str(project_item.get("title") or project_item.get("role") or "").strip()
    start = str(project_item.get("start_date") or project_item.get("from") or "").strip()
    end = str(project_item.get("end_date") or project_item.get("to") or "").strip()
    period = f"{start}–{end}" if start or end else ""

    header_parts = [company]
    if period:
        header_parts.append(period)
    header = " | ".join(header_parts)
    if title:
        header = f"{title} @ {header}"

    confirmed_type = str(llm_judgment.get("confirmed_type") or "").strip()
    verdict_label = str(llm_judgment.get("verdict_label") or "").strip()
    complexity = _safe_float(llm_judgment.get("complexity_score"), 0.0)
    implied = [str(s) for s in (llm_judgment.get("implied_skills") or [])[:5]]
    verified = [str(s) for s in (llm_judgment.get("claimed_skills_verified") or [])[:5]]
    gaps = [str(s) for s in (llm_judgment.get("skill_gaps_detected") or [])[:3]]
    reason_text = str(llm_judgment.get("reason") or "").strip()
    probe = str(llm_judgment.get("interview_probe") or "").strip()
    confidence = str(llm_judgment.get("confidence") or "MEDIUM").strip()

    lines = []
    lines.append(f"{header}.")
    if verdict_label:
        lines.append(f"LLM verdict: {verdict_label} ({confirmed_type}, conf={confidence}).")
    if implied:
        lines.append(f"Implied skills: {', '.join(implied)}.")
    if verified:
        lines.append(f"Claimed & verified: {', '.join(verified)}.")
    if gaps:
        lines.append(f"Gaps detected: {', '.join(gaps)}.")
    if reason_text:
        lines.append(f"{reason_text}")
    lines.append(f"Complexity: {complexity}/5 (deterministic base: {base_result.get('score', 0)}/{base_result.get('max', 10)}).")
    if probe:
        lines.append(f"Probe: \"{probe}\"")

    return "  ".join(lines)


def _build_llm_enriched_reason(
    project_item: dict[str, Any],
    llm_j: dict[str, Any],
    base_result: dict[str, Any],
) -> str:
    """Build a rich, recruiter-readable reason string from LLM deep judgment.

    Combines:
      - Base deterministic signal summary (criteria met/missing)
      - LLM verdict label + confirmed type
      - Scope assessment
      - Implied skill highlights
      - Gap callout (if any)
      - Interview probe
    Score is NOT changed — this is purely narrative enrichment.
    """
    parts: list[str] = []

    company = str(project_item.get("company") or "").strip()
    title = str(project_item.get("title") or project_item.get("role") or "").strip()
    era = llm_j.get("era_context") or ""
    verdict_label = llm_j.get("verdict_label") or ""
    confirmed_type = llm_j.get("confirmed_type") or ""
    scope = llm_j.get("scope_assessment") or ""
    re_scope = llm_j.get("reverse_engineered_scope") or ""
    signal = llm_j.get("candidate_signal") or ""
    exhibit = llm_j.get("skill_exhibition_type") or ""
    role_intent = llm_j.get("role_intent") or ""
    probe = llm_j.get("interview_probe") or ""
    implied = llm_j.get("implied_skills") or []
    gaps = llm_j.get("skill_gaps_detected") or []
    green = llm_j.get("green_flags") or []
    red = llm_j.get("red_flags") or []

    # Detect if LLM corrected the rule-detected project type
    rule_type = str(project_item.get("project_type") or "").upper().strip()
    type_corrected = confirmed_type and rule_type and confirmed_type.upper() != rule_type
    type_display = confirmed_type or rule_type

    # Line 1: identity + type (LLM type takes precedence, note override if different)
    who = f"{title} @ {company}" if company else title
    type_note = (
        f"LLM: {confirmed_type} [overrides rule: {rule_type}]"
        if type_corrected else type_display
    )
    if verdict_label:
        parts.append(f"[{verdict_label}] — {who} ({type_note})")
    else:
        parts.append(f"{who} ({type_note})")

    # Line 2: era context
    if era:
        parts.append(f"Era: {era}")

    # Line 3: deterministic criteria summary — strip the stale rule-detected type entry
    # so it doesn't contradict the LLM type shown above
    criteria_met = [
        c for c in (base_result.get("criteria_met") or [])
        if not c.startswith("type=")  # remove raw rule type; LLM type shown in Line 1
    ]
    criteria_missing = base_result.get("criteria_missing") or []
    det_score = base_result.get("score", 0)
    det_max = base_result.get("max", 0)
    criteria_str = (
        f"Deterministic: {det_score}/{det_max}. "
        + (f"Met: {', '.join(criteria_met[:4])}. " if criteria_met else "")
        + (f"Missing: {', '.join(criteria_missing[:3])}." if criteria_missing else "")
    )
    parts.append(criteria_str)

    # Line 4: reverse-engineered scope (most valuable insight)
    if re_scope:
        parts.append(f"Reverse-engineered: {re_scope}")
    elif scope:
        parts.append(f"Scope: {scope}")

    # Line 5: implied skills — ONLY show for IC/technical roles; skip for STRATEGIC/CONSULTING VP roles
    _is_strategic_role = exhibit in ("STRATEGIC", "LEADERSHIP") or any(
        w in (title or "").lower()
        for w in ("vice president", "vp", "director", "chief", "head of", "global lead", "c-level")
    )
    if implied and not _is_strategic_role:
        parts.append(f"Implied must-have skills: {', '.join(implied[:6])}.")
    elif implied and _is_strategic_role:
        # For VP/strategic: show implied domain competencies, not tool names
        _domain_skills = [s for s in implied if not any(
            tech in s.lower() for tech in ["python", "sql", "java", "r ", "c++", "go ", "scala", "spark"]
        )]
        if _domain_skills:
            parts.append(f"Expected competencies: {', '.join(_domain_skills[:5])}.")

    # Line 6: skill gaps
    if gaps:
        parts.append(f"Gaps: {'; '.join(gaps[:3])}.")

    # Line 7: green flags
    if green:
        parts.append(f"Strengths: {'; '.join(green[:3])}.")

    # Line 8: red flags
    if red:
        parts.append(f"Watch-outs: {'; '.join(red[:2])}.")

    # Line 9: candidate signal + trajectory
    if signal or exhibit:
        parts.append(
            f"Signal: {signal}. Type: {exhibit}."
            + (f" Targeting: {role_intent}." if role_intent else "")
        )

    # Line 10: interview probe — labeled clearly for recruiter
    if probe:
        parts.append(f"Phone Screen Question: \"{probe}\"")

    return "\n".join(p for p in parts if p)


def _score_project_with_llm(
    project_item: dict[str, Any],
    base_result: dict[str, Any],
    llm_judgment: dict[str, Any] | None,
    experience_data: dict[str, Any],
    max_score: int = 6,
) -> dict[str, Any]:
    """Blend deterministic project score with LLM judgment.

    If LLM judgment is unavailable, returns the base deterministic result unchanged.
    Otherwise:
      - LLM complexity_score (0–5) is scaled to max_score
      - Blended: 40% deterministic + 60% LLM
      - Rich reason string is built with implied_skills, gaps, probe
    """
    if not llm_judgment:
        return base_result

    llm_pts = round((_safe_float(llm_judgment.get("complexity_score"), 0.0) / 5.0) * max_score, 1)
    base_pts = _safe_float(base_result.get("score"), 0.0)
    blended_pts = round(base_pts * 0.4 + llm_pts * 0.6, 1)
    blended_pts = _clamp(blended_pts, 0.0, float(max_score))

    reason = _build_project_reason(project_item, llm_judgment, base_result)

    return {
        **base_result,
        "score": blended_pts,
        "reason": reason,
        "llm_confirmed_type": llm_judgment.get("confirmed_type"),
        "verdict_label": llm_judgment.get("verdict_label"),
        "implied_skills": llm_judgment.get("implied_skills", []),
        "claimed_skills_verified": llm_judgment.get("claimed_skills_verified", []),
        "skill_gaps_detected": llm_judgment.get("skill_gaps_detected", []),
        "interview_probe": llm_judgment.get("interview_probe", ""),
        "llm_confidence": llm_judgment.get("confidence", ""),
        "complexity_score_llm": llm_judgment.get("complexity_score"),
    }


# ---------------------------------------------------------------------------
# EXPERIENCE section — 44 pts (normalised to 40)
# ---------------------------------------------------------------------------

def _score_experience_section(
    experience: dict[str, Any],
    bert_priors: dict[str, Any],
    client_role_config: dict[str, Any] | None,
    education: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bd: dict[str, Any] = {}
    reject_flags: list[str] = []

    total_years = _safe_float(experience.get("total_experience_years"), 0.0)

    # Pre-compute company tier map for edge-case detection (E3, E7, E15)
    _twd_all: list[dict[str, Any]] = experience.get("tenure_with_dates") or []
    _twd_companies = [str(e.get("company") or "") for e in _twd_all if e.get("company")]
    _pretier_map: dict[str, int] = {
        c: classify_company_tier(c, llm_fallback=False) for c in _twd_companies if c
    }

    # ── 1. Overall / Relevant experience (4 pts) ──────────────────────────
    yoe_range = (client_role_config or {}).get("yoe_range", {})
    yoe_min = _safe_float(yoe_range.get("min"), 0.0)
    yoe_max = _safe_float(yoe_range.get("max"), 999.0)
    if yoe_min > 0 and total_years > 0:
        ratio = min(total_years, yoe_max) / total_years
        rel_pts = round(_clamp(ratio * 4, 0, 4), 1)
        if ratio < 0.70:
            reject_flags.append(f"overall_experience={ratio:.0%} (<70%) — candidate may be over/under experienced for target role")
        reason = (
            f"{total_years:.1f} yrs total; {ratio:.0%} falls within JD target range "
            f"({yoe_min:.0f}–{yoe_max:.0f} yrs). "
            + ("Within range — full credit." if ratio >= 1.0 else f"Partial — {rel_pts}/4.")
        )
    elif total_years >= 10:
        rel_pts = 4.0
        reason = f"{total_years:.1f} yrs total — senior/principal band (10+ yrs). Full credit."
    elif total_years >= 6:
        rel_pts = 3.0
        reason = f"{total_years:.1f} yrs total — mid-senior band (6-10 yrs). Configure a JD YoE range for calibrated scoring."
    elif total_years >= 4:
        rel_pts = 2.5
        reason = f"{total_years:.1f} yrs total — mid-level band (4-6 yrs). Configure a JD YoE range for calibrated scoring."
    elif total_years >= 2:
        rel_pts = 2.0
        reason = f"{total_years:.1f} yrs total — junior-mid band (2-4 yrs). Limited experience depth without JD calibration."
    elif total_years >= 1:
        rel_pts = 1.5
        reason = f"{total_years:.1f} yrs total — early career (1-2 yrs). Minimal professional experience detected."
    elif total_years > 0:
        rel_pts = 1.0
        reason = f"{total_years:.1f} yrs total — sub-1-year experience. Likely fresher or career changer."
    else:
        rel_pts = 0.0
        reason = "No experience years detected in resume."
    bd["overall_experience"] = _param(rel_pts, 4, reason, total_years=total_years)

    # ── 2. Career breaks (2 pts) ──────────────────────────────────────────
    # Hard breaks (> 18m, non-educational) are penalised.
    # E4: Gaps overlapping education entries are excluded entirely.
    # E5: Gaps ≤ 18m are classified as possible_parental (soft note, no penalty).
    # Scoring bands apply only to hard breaks:
    #   0 hard breaks          → 2.0  (full credit)
    #   1 hard break ≤ 6m      → 1.5  (short gap — acceptable) [rare: gap must be >18m to be hard]
    #   1 hard break 7–12m     → 1.0  (moderate — notable)
    #   1 hard break > 12m     → 0.5  (long — recruiter must probe)
    #   2 hard breaks          → 0.5  (borderline, needs explanation)
    #   3+ hard breaks / reject → 0.0 (reject flag raised)
    tenure_with_dates = experience.get("tenure_with_dates") or []
    _edu_entries_for_breaks = (education or {}).get("education_entries") or []
    break_info = _detect_career_breaks(tenure_with_dates, _edu_entries_for_breaks)
    n_breaks = break_info["break_count"]
    breaks_list = break_info.get("breaks") or []
    edu_breaks_list = break_info.get("edu_breaks") or []
    parental_breaks_list = break_info.get("possible_parental_breaks") or []
    overlaps_list = break_info.get("overlaps") or []
    duplicate_overlaps = [o for o in overlaps_list if o.get("duplicate")]
    transition_overlaps = [o for o in overlaps_list if not o.get("duplicate")]

    # Build context note for E4/E5 events
    _break_context_note = ""
    if edu_breaks_list:
        _break_context_note += f" [{len(edu_breaks_list)} educational break(s) excluded — E4: verified study period.]"
    if parental_breaks_list:
        _break_context_note += (
            f" [{len(parental_breaks_list)} possible parental/personal break(s) ≤18m — "
            f"E5: soft flag only, no score penalty. Recruiter to verify.]"
        )
    if duplicate_overlaps:
        _break_context_note += (
            f" [{len(duplicate_overlaps)} duplicate employment entr{'y' if len(duplicate_overlaps) == 1 else 'ies'} "
            f"detected (same company, identical dates listed as separate roles) — counted as hard break(s).]"
        )
    if transition_overlaps:
        _break_context_note += (
            f" [{len(transition_overlaps)} overlapping employment period(s) detected between different employers — "
            f"soft flag only, no score penalty. Recruiter to verify.]"
        )

    if break_info["reject"]:
        reject_flags.append(f"career_breaks={n_breaks} — more than 2 unexplained employment gaps detected (each >18 months)")
        breaks_pts = 0.0
        reason = f"{n_breaks} hard career breaks detected — exceeds threshold. Reject flag raised.{_break_context_note}"
    elif n_breaks == 0:
        breaks_pts = 2.0
        reason = f"No unexplained employment gaps detected. Continuous career trajectory.{_break_context_note}"
    elif n_breaks == 1:
        gap_m = _safe_int(breaks_list[0].get("gap_months"), 0) if breaks_list else 0
        if gap_m <= 6:
            breaks_pts = 1.5
            reason = f"1 career break detected ({gap_m}m gap). Short gap — acceptable, minor penalty.{_break_context_note}"
        elif gap_m <= 12:
            breaks_pts = 1.0
            reason = f"1 career break detected ({gap_m}m gap). Moderate gap — notable, recruiter should ask for context.{_break_context_note}"
        else:
            breaks_pts = 0.5
            reason = f"1 career break detected ({gap_m}m gap). Long gap — recruiter must probe reason.{_break_context_note}"
    else:
        breaks_pts = 0.5
        reason = f"2 career breaks detected. Borderline — recruiter clarification required on both gaps.{_break_context_note}"
    bd["career_breaks"] = _param(
        breaks_pts, 2, reason,
        break_count=n_breaks, breaks=breaks_list,
        edu_breaks=edu_breaks_list, possible_parental_breaks=parental_breaks_list,
        overlaps=overlaps_list,
    )

    # ── 3. Career progression (4 pts) ─────────────────────────────────────
    trajectory = _safe_int(experience.get("career_trajectory_score"), 1)
    heuristic_traj_pts = round(_clamp((trajectory / 5.0) * 4, 0, 4), 1)
    _traj_labels = {5: "Strong upward — consistent seniority growth across roles.",
                    4: "Good progression — mostly upward with minor lateral moves.",
                    3: "Moderate — some upward movement but trajectory is mixed.",
                    2: "Flat or lateral — limited seniority growth visible.",
                    1: "Weak or declining — titles suggest stagnation or regression."}

    # BERT blending for career_progression
    CP_MAP = {"FAST_TRACK": 4, "GROWING": 3, "LATERAL": 2, "DECLINING": 0.5}
    cp_prior = bert_priors.get("career_progression_prior", {}) or {}
    cp_confidence = _safe_float(cp_prior.get("confidence"), 0.0)
    bert_cp_pts = CP_MAP.get(str(cp_prior.get("label") or "").upper())
    if bert_cp_pts is not None and cp_confidence >= 0.60:
        prog_pts = round(bert_cp_pts * 0.60 + heuristic_traj_pts * 0.40, 1)
        bert_note = f" BERT={cp_prior.get('label')} (conf={cp_confidence:.2f}, primary blend)."
    elif bert_cp_pts is not None and cp_confidence >= 0.40:
        prog_pts = round(bert_cp_pts * 0.40 + heuristic_traj_pts * 0.60, 1)
        bert_note = f" BERT={cp_prior.get('label')} (conf={cp_confidence:.2f}, light blend)."
    else:
        prog_pts = heuristic_traj_pts
        bert_note = ""
    prog_pts = round(_clamp(prog_pts, 0, 4), 1)

    reason = _traj_labels.get(trajectory, "Trajectory could not be computed.") + f" (heuristic={trajectory}/5){bert_note} → {prog_pts}/4 pts"
    bd["career_progression"] = _param(prog_pts, 4, reason, trajectory_score=trajectory,
                                       titles=experience.get("titles", [])[:5],
                                       bert_label=cp_prior.get("label"),
                                       bert_confidence=cp_confidence)

    # ── 4. Stability (4 pts) ───────────────────────────────────────────────
    stability_raw = _safe_float(experience.get("stability_score"), 3.0)
    avg_tenure = _safe_float(experience.get("average_tenure_months"), 0.0)
    loyalty = str(experience.get("loyalty_signal") or "UNKNOWN").upper()
    tenures_list = experience.get("tenures") or []
    n_roles = len(tenures_list)
    stab_pts = round(_clamp((stability_raw / 5.0) * 4, 0, 4), 1)

    # Build a richer reason that cites tenure stats and churn signals
    short_stints = sum(1 for m in tenures_list if 0 < m < 12) if tenures_list else 0
    total_months_exp = sum(tenures_list) if tenures_list else 0
    hop_rate = round(n_roles / (total_months_exp / 12.0), 1) if total_months_exp > 0 and n_roles >= 2 else 0.0

    if loyalty == "HIGH":
        base = f"High loyalty — avg tenure {avg_tenure:.0f}m across {n_roles} role(s)."
    elif loyalty == "MEDIUM":
        base = f"Medium loyalty — avg tenure {avg_tenure:.0f}m across {n_roles} role(s)."
    elif loyalty == "LOW":
        base = f"Low loyalty — avg tenure {avg_tenure:.0f}m across {n_roles} role(s). Churn risk."
    else:
        base = f"Avg tenure {avg_tenure:.0f}m across {n_roles} role(s). Loyalty signal unknown."

    if short_stints >= 2:
        base += f" {short_stints} stint(s) under 12m — job-hopping pattern."
    elif short_stints == 1:
        base += " 1 stint under 12m detected."

    if hop_rate >= 1.5:
        base += f" Role churn rate {hop_rate:.1f} roles/yr — above acceptable threshold."

    # ── Stability edge-case adjustments (E3, E7, E15) ─────────────────────
    _edge_stab_notes: list[str] = []
    _adj_short_stints = short_stints
    _adj_hop_rate = hop_rate

    # E15: Internal promotions — same-company sequential entries are NOT job-hops
    if _twd_all:
        _sorted_twd = sorted(
            [e for e in _twd_all if _parse_ym(e.get("start") or "")],
            key=lambda e: _parse_ym(e.get("start") or datetime.now(timezone.utc).isoformat()) or datetime.now(timezone.utc),
        )
        _internal_promos = sum(
            1 for j in range(len(_sorted_twd) - 1)
            if str(_sorted_twd[j].get("company") or "").lower().strip()
            == str(_sorted_twd[j + 1].get("company") or "").lower().strip()
            and str(_sorted_twd[j].get("company") or "").strip()
        )
        if _internal_promos >= 1:
            _adj_short_stints = max(0, _adj_short_stints - _internal_promos)
            _edge_stab_notes.append(
                f"E15: {_internal_promos} internal promotion(s) detected (same company, role change) — positive progression, not instability."
            )

    # E3: All TIER_1 companies → FAANG-to-FAANG moves are industry-normal (not job-hopping)
    if _pretier_map and all(t == 1 for t in _pretier_map.values()):
        _adj_short_stints = 0
        _adj_hop_rate = 0.0
        _edge_stab_notes.append(
            "E3: All employers are TIER_1 (FAANG/hyper-scale) — cross-company moves are industry-standard, not job-hopping."
        )

    # E7: Startup exits — TIER_4/5 company, stint < 18m, no gap immediately after
    if _twd_all and len(_twd_all) >= 2:
        _sorted_twd2 = sorted(
            [e for e in _twd_all if _parse_ym(e.get("start") or "")],
            key=lambda e: _parse_ym(e.get("start") or datetime.now(timezone.utc).isoformat()) or datetime.now(timezone.utc),
        )
        _startup_exits = 0
        for _j in range(len(_sorted_twd2) - 1):
            _coy = str(_sorted_twd2[_j].get("company") or "")
            _coy_tier = _pretier_map.get(_coy, classify_company_tier(_coy, llm_fallback=False))
            _s_start = _parse_ym(_sorted_twd2[_j].get("start") or "")
            _s_end = _parse_ym(_sorted_twd2[_j].get("end") or "")
            _next_start = _parse_ym(_sorted_twd2[_j + 1].get("start") or "")
            if _s_start and _s_end and _next_start:
                _stint_m = _month_diff_dt(_s_start, _s_end)
                _gap_after = _month_diff_dt(_s_end, _next_start)
                if _coy_tier >= 4 and _stint_m < 18 and _gap_after <= 3:
                    _startup_exits += 1
        if _startup_exits >= 1:
            _adj_short_stints = max(0, _adj_short_stints - _startup_exits)
            _edge_stab_notes.append(
                f"E7: {_startup_exits} likely startup exit(s) (TIER_4/5, <18m, no gap after) — not penalised as job-hopping."
            )

    # Apply adjustments: if any edge case fired, update reason and slightly boost stab score
    if _edge_stab_notes:
        _edge_note_str = " ".join(_edge_stab_notes)
        if _adj_short_stints < short_stints or _adj_hop_rate < hop_rate:
            base = base.replace(
                f"{short_stints} stint(s) under 12m — job-hopping pattern.",
                f"{_adj_short_stints} penalised short stint(s) after edge-case review."
                if _adj_short_stints > 0 else "No penalised short stints after edge-case review.",
            ).replace(
                "1 stint under 12m detected.",
                "1 stint (reviewed — not penalised per edge-case rules).",
            ).replace(
                f"Role churn rate {hop_rate:.1f} roles/yr — above acceptable threshold.",
                f"Hop rate {hop_rate:.1f}/yr (adjusted after edge-case context).",
            )
            # Small positive adjustment to stability score for correctly-classified profiles
            stab_pts = min(round(stab_pts + 0.5, 1), 4.0)
        base += f" {_edge_note_str}"

    reason = base + f" (stability={stability_raw}/5 → {stab_pts}/4 pts)"
    bd["stability"] = _param(
        stab_pts, 4, reason,
        avg_tenure_months=avg_tenure, loyalty_signal=loyalty,
        raw_stability_score=stability_raw, n_roles=n_roles,
        short_stints=short_stints, adjusted_short_stints=_adj_short_stints,
        hop_rate=hop_rate, edge_case_notes=_edge_stab_notes,
    )

    # ── 5. Company tier (6 pts) ────────────────────────────────────────────
    companies = experience.get("companies") or []
    best_tier = 5
    tier_map: dict[str, int] = {}
    for company in companies:
        t = classify_company_tier(str(company or ""), llm_fallback=False)
        tier_map[str(company)] = t
        if t < best_tier:
            best_tier = t
    company_pts = tier_to_points(best_tier, max_points=6)
    _tier_labels = {1: "FAANG / global hyper-scale", 2: "Unicorn / well-funded product",
                    3: "Mid-size funded / strong regional", 4: "IT services / consulting",
                    5: "Unknown / not in database"}
    best_company = next((c for c, t in tier_map.items() if t == best_tier), "unknown")
    reason = (
        f"Best company tier: Tier {best_tier} ({_tier_labels.get(best_tier, '?')}). "
        f"Best match: '{best_company}'. "
        f"{'Full credit for Tier 1.' if best_tier == 1 else 'Score reflects highest-tier employer found.'} "
        f"({company_pts}/6 pts)"
    )
    bd["company_tier"] = _param(company_pts, 6, reason,
                                 best_tier=best_tier, company_tiers=tier_map)

    # ── 6. Awards & recognition (4 pts) ───────────────────────────────────
    achievements = experience.get("achievements") or []
    ach_count = len(achievements) if isinstance(achievements, list) else 0
    awards_pts = _clamp(float(ach_count), 0, 4)
    if ach_count == 0:
        reason = "No awards, recognitions, or commendations found in resume."
    elif ach_count == 1:
        reason = f"1 achievement/recognition detected: '{achievements[0]}'. Moderate signal."
    elif ach_count <= 3:
        reason = f"{ach_count} achievements/recognitions detected. Good recognition signal."
    else:
        reason = f"{ach_count} achievements/recognitions detected. Strong recognition signal."
    bd["awards_recognition"] = _param(awards_pts, 4, reason, achievement_count=ach_count)

    # ── 7. International exposure (2 pts) ───────────────────────────────────
    # E16: If the resume signals international work experience (onsite, global teams,
    # multi-country language), auto-score 1.5/2 at the resume stage.
    # Recruiter validates during phone screen and can upgrade to the full 2 pts.
    # If no signal found, stays at 0 and recruiter must score from scratch.
    intl = experience.get("international_exposure", False)
    if intl:
        bd["international_exposure"] = _param(
            1.5, 2,
            "E16: International work experience detected on resume (onsite / global team / multi-country). "
            "Auto-scored 1.5/2. Recruiter to validate and upgrade to 2 if confirmed.",
            resume_signal=True,
        )
    else:
        bd["international_exposure"] = _param(
            0, 2,
            "No international exposure signals found in resume. Recruiter to validate and assign 0–2.",
            resume_signal=False,
            pending="recruiter",
        )

    # ── 8. Stakeholder management (2 pts) — RECRUITER STAGE ───────────────
    # Scored by recruiter after phone screen (not auto-scored from resume).
    client_facing = experience.get("client_facing", False)
    SM_MAP = {"C_LEVEL": 2, "CLIENT_FACING": 2, "INTERNAL": 1, "NONE": 0}
    sm_prior = bert_priors.get("stakeholder_prior", {}) or {}
    sm_confidence = _safe_float(sm_prior.get("confidence"), 0.0)
    bert_sm_label = str(sm_prior.get("label") or "").upper()
    stake_context = (
        f"Resume signals: client-facing / stakeholder language detected (BERT={bert_sm_label} conf={sm_confidence:.2f}). "
        "Recruiter to validate and assign 0–2."
        if client_facing or bert_sm_label not in ("", "NONE") else
        "No stakeholder management signals in resume. Recruiter to validate and assign 0–2."
    )
    bd["stakeholder_management"] = _param(0, 2, stake_context,
                                           resume_client_facing=client_facing,
                                           bert_label=sm_prior.get("label"),
                                           bert_confidence=sm_confidence,
                                           pending="recruiter")

    # ── 9. Mentorship / code reviews (3 pts) — RECRUITER STAGE ───────────
    # Scored by recruiter after phone screen (not auto-scored from resume).
    leadership_raw = _safe_int(experience.get("leadership_signal_score"), 0)
    ms_prior = bert_priors.get("mentorship_prior", {}) or {}
    ms_confidence = _safe_float(ms_prior.get("confidence"), 0.0)
    bert_ms_label = str(ms_prior.get("label") or "").upper()
    if leadership_raw >= 2:
        mentor_context = (
            f"Resume signals: strong mentorship/lead signals across {leadership_raw} roles"
            + (f", BERT={bert_ms_label} (conf={ms_confidence:.2f})" if bert_ms_label else "")
            + ". Recruiter to validate and assign 0–3."
        )
    elif leadership_raw == 1:
        mentor_context = (
            "Resume signals: weak lead/owned language (1 instance)"
            + (f", BERT={bert_ms_label}" if bert_ms_label else "")
            + ". Recruiter to validate and assign 0–3."
        )
    else:
        mentor_context = (
            "No mentorship / code-review signals detected in resume"
            + (f". BERT={bert_ms_label}" if bert_ms_label and bert_ms_label != "NONE" else "")
            + ". Recruiter to validate and assign 0–3."
        )
    bd["mentorship_signal"] = _param(0, 3, mentor_context,
                                      resume_leadership_score=leadership_raw,
                                      bert_label=ms_prior.get("label"),
                                      bert_confidence=ms_confidence,
                                      pending="recruiter")

    # ── 10. Project 1 (10 pts) + Project 2 (6 pts) ───────────────────────
    project_types = experience.get("project_types") or []
    _proj_max = {1: 10, 2: 6}
    # Pull LLM deep judgment if available (injected by engine.py)
    _llm_proj_result: dict[str, Any] = experience.get("_llm_project_judgment") or {}
    _llm_judgments: list[dict[str, Any]] = _llm_proj_result.get("project_judgments") or []
    _llm_judgment_by_idx: dict[int, dict[str, Any]] = {
        int(j.get("project_index", 0)): j for j in _llm_judgments if isinstance(j, dict)
    }
    _candidate_assessment: dict[str, Any] = _llm_proj_result.get("candidate_assessment") or {}

    for i, key in enumerate(["project_1", "project_2"], start=1):
        if len(project_types) >= i:
            base = _score_project(project_types[i - 1], experience, {}, max_score=_proj_max[i])
            llm_j = _llm_judgment_by_idx.get(i)
            if llm_j:
                # Enrich with LLM metadata — score stays deterministic
                base = {
                    **base,
                    # --- LLM-enriched reason (replaces terse deterministic reason) ---
                    "reason": _build_llm_enriched_reason(project_types[i - 1], llm_j, base),
                    # --- New LLM fields (metadata only, no score impact) ---
                    "llm_confirmed_type": llm_j.get("confirmed_type"),
                    "verdict_label": llm_j.get("verdict_label"),
                    "era_context": llm_j.get("era_context"),
                    "reverse_engineered_scope": llm_j.get("reverse_engineered_scope"),
                    "scope_assessment": llm_j.get("scope_assessment"),
                    "implied_skills": llm_j.get("implied_skills") or [],
                    "claimed_skills_verified": llm_j.get("claimed_skills_verified") or [],
                    "skill_gaps_detected": llm_j.get("skill_gaps_detected") or [],
                    "green_flags": llm_j.get("green_flags") or [],
                    "red_flags": llm_j.get("red_flags") or [],
                    "role_intent": llm_j.get("role_intent"),
                    "candidate_signal": llm_j.get("candidate_signal"),
                    "skill_exhibition_type": llm_j.get("skill_exhibition_type"),
                    "interview_probe": llm_j.get("interview_probe"),
                    "llm_confidence": llm_j.get("confidence"),
                }
            bd[key] = base
        else:
            bd[key] = _param(0, _proj_max[i],
                f"Project {i} data not available — score cannot be assigned.",
                criteria_met=[], criteria_missing=["no project data"])

    # ── Candidate holistic assessment (metadata only, from LLM) ───────────
    if _candidate_assessment:
        bd["_candidate_assessment"] = _candidate_assessment  # metadata only, no score field

    # ── Product/Service/Consulting tag (metadata only, 0 pts) ─────────────
    bd["operating_model_tag"] = experience.get("dominant_operating_model", "HYBRID")

    # ── Credibility check (metadata only, 0 pts — does not affect score) ──
    # Surfaced as a separate top-level key in compute_rubric_score output.
    # The credibility engine reverse-engineers expected skills/era/progression
    # from role×tier and flags gaps — but does NOT reduce the 40-pt experience score.
    credibility = experience.get("_credibility") or {}
    if credibility:
        bd["_credibility_check"] = credibility  # metadata only, no score field

    total_exp = sum(
        v["score"] for k, v in bd.items()
        if isinstance(v, dict) and "score" in v and k != "operating_model_tag"
    )
    return {
        "experience_score": round(_clamp(total_exp, 0, 40), 1),
        "breakdown": bd,
        "reject_flags": reject_flags,
    }


# ---------------------------------------------------------------------------
# SKILLS section — 40 pts (+8 bonus)
# ---------------------------------------------------------------------------

_CODING_LINK_RE = re.compile(
    r'(?:https?://)?(?:www\.)?'
    r'(?:github\.com|gitlab\.com|bitbucket\.org|kaggle\.com|leetcode\.com'
    r'|stackoverflow\.com/users|huggingface\.co|medium\.com/@'
    r'|codechef\.com|hackerrank\.com|codeforces\.com)'
    r'/[^\s,>)"\'\]<]+',
    re.IGNORECASE,
)

# E17: Competitive coding platform mentions (no URL required — plain text detection)
_COMPETITIVE_CODING_RE = re.compile(
    r'\b(?:leetcode|leet\s*code|hackerrank|hacker\s*rank|codeforces|codechef|'
    r'hackerearth|hacker\s*earth|coding\s*ninjas?|geeksforgeeks|gfg|atcoder|'
    r'topcoder|interviewbit)\b',
    re.IGNORECASE,
)
_HACKATHON_PRIZE_RE = re.compile(
    r'\bhackathon\b.{0,80}(?:win|won|winner|prize|award|1st|first|position|rank)',
    re.IGNORECASE,
)


def _score_skills_section(
    evidence_map: dict[str, Any],
    experience: dict[str, Any],
    bert_priors: dict[str, Any],
    client_role_config: dict[str, Any] | None,
    raw_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bd: dict[str, Any] = {}
    skill_rows = list(evidence_map.values()) if isinstance(evidence_map, dict) else []
    prior_lookup = {
        str(p.get("skill") or "").lower(): p
        for p in (bert_priors.get("skill_depth_priors") or [])
        if isinstance(p, dict)
    }

    # ── 1. Skill list + years of exp timeline (20 pts — resume auto-scored) ─
    # Score A = 20 pts. Raw scale 0–30: sum of years_of_usage for APPLIED/DEEP/EXPERT
    # skills (capped at 10 yrs per skill), normalised to 20 pts.
    # Panel may re-validate and cap to 6 pts in the final score.
    strong_skills = [
        r for r in skill_rows
        if str(r.get("evidence_level") or "").upper() in {"APPLIED", "DEEP", "EXPERT"}
    ]
    raw_skill_yrs = sum(
        min(_safe_float(r.get("years_of_usage"), 1.0), 10.0)
        for r in strong_skills
    )
    skill_list_pts = round(_clamp((raw_skill_yrs / 30.0) * 20.0, 0.0, 20.0), 1)
    if strong_skills:
        skill_names = ", ".join(r.get("skill", "") for r in strong_skills[:6])
        skill_list_reason = (
            f"{len(strong_skills)} skills with APPLIED/DEEP/EXPERT evidence "
            f"({skill_names}). Weighted skill-years: {raw_skill_yrs:.1f}/30. "
            f"Score {skill_list_pts}/20. Panel can re-validate and cap at 6 pts."
        )
    else:
        skill_list_reason = (
            "No skills with APPLIED or stronger evidence detected. "
            f"Score={skill_list_pts}/20. Panel can validate after technical interview."
        )
    bd["skill_list_years"] = _param(skill_list_pts, 20, skill_list_reason,
                                     strong_skill_count=len(strong_skills),
                                     raw_skill_years=raw_skill_yrs,
                                     strong_skills=[r.get("skill") for r in strong_skills[:8]])

    # ── 2. Skill depth — BERT primary (10 pts) ───────────────────────────
    if client_role_config:
        depth_raw = _apply_role_skill_weights(skill_rows, client_role_config)
        # _apply_role_skill_weights returns 0-10; use directly
        depth_pts = round(_clamp(depth_raw, 0, 10), 1)
        mandatory_names = [e.get("skill") for e in (client_role_config.get("mandatory_skills") or [])]
        reason = (
            f"Role-weighted depth score using mandatory skills ({', '.join(str(s) for s in mandatory_names[:4])}). "
            f"BERT signals blended with evidence. Score={depth_pts}/10."
        )
    else:
        top_rows = sorted(
            skill_rows,
            key=lambda r: EVIDENCE_LEVEL_TO_SCORE.get(str(r.get("evidence_level") or "").upper(), 0.0),
            reverse=True,
        )[:5]
        if top_rows:
            blended_avg = sum(
                _bert_adjust_skill_depth(
                    EVIDENCE_LEVEL_TO_SCORE.get(str(r.get("evidence_level") or "").upper(), 0.0),
                    prior_lookup.get(str(r.get("skill") or "").lower()),
                    str(r.get("evidence_level") or "").upper(),
                )
                for r in top_rows
            ) / len(top_rows)
            depth_pts = round(_clamp((blended_avg / 5.0) * 10.0, 0, 10), 1)
            top_names = [r.get("skill") for r in top_rows]
            bert_active = any(prior_lookup.get(str(r.get("skill") or "").lower()) for r in top_rows)
            reason = (
                f"BERT-blended depth across top {len(top_rows)} skills: {', '.join(str(n) for n in top_names)}. "
                f"{'BERT signals active and blended.' if bert_active else 'No BERT priors — evidence-only scoring.'} "
                f"Avg blended depth={blended_avg:.2f}/5 → {depth_pts}/10."
            )
        else:
            depth_pts = 0.0
            reason = "No skill evidence rows available for depth assessment."
    bd["skill_depth"] = _param(depth_pts, 10, reason)

    # ── 3. Skill recency (5 pts) ───────────────────────────────────────────
    # Importance-weighted: EXPERT/DEEP core skills (Python, AWS, ML) count more
    # than peripheral APPLIED tools (Dashboarding, Tableau), because recency
    # matters most for the skills that define the candidate's profile.
    _EV_WEIGHT = {"EXPERT": 3.0, "DEEP": 2.0, "APPLIED": 1.0}

    def _skill_importance(r):
        """Weight = depth × tenure blend. EXPERT @ 4yrs >> APPLIED @ 0.5yrs."""
        ev_w = _EV_WEIGHT.get(str(r.get("evidence_level") or "").upper(), 0.5)
        yrs  = min(float(r.get("years_of_usage") or 0.5), 6.0)
        return round(ev_w * (0.5 + yrs / 6.0), 3)

    scored_rows   = [r for r in skill_rows if str(r.get("evidence_level") or "").upper() in {"APPLIED", "DEEP", "EXPERT"}] or skill_rows
    recent_skills = [r for r in scored_rows if str(r.get("recency") or "").upper() in {"RECENT", "CURRENT"}]
    mid_skills    = [r for r in scored_rows if str(r.get("recency") or "").upper() == "MID"]

    # Importance-weighted totals (RECENT = full, MID = ½)
    w_recent = sum(_skill_importance(r)       for r in recent_skills)
    w_mid    = sum(_skill_importance(r) * 0.5 for r in mid_skills)
    w_total  = sum(_skill_importance(r)       for r in scored_rows)
    recency_ratio = (w_recent + w_mid) / max(w_total, 0.01)
    recency_pts   = round(_clamp(recency_ratio * 5, 0, 5), 1)

    top_recent_names = sorted(
        recent_skills, key=_skill_importance, reverse=True
    )[:4]
    top_recent_str = ", ".join(r.get("skill", "?") for r in top_recent_names)
    mid_note = f"; {len(mid_skills)} MID (½ weight)" if mid_skills else ""
    if recency_ratio >= 0.7:
        reason = (f"Importance-weighted recency {recency_ratio:.0%} — "
                  f"core skills current: {top_recent_str}{mid_note}. Strong recency signal.")
    elif recency_ratio >= 0.4:
        reason = (f"Importance-weighted recency {recency_ratio:.0%} — "
                  f"some core skills current: {top_recent_str}{mid_note}. Moderate recency.")
    else:
        reason = (f"Importance-weighted recency {recency_ratio:.0%} — "
                  f"key skills appear dated{mid_note}. Recency gap detected.")
    bd["skill_recency"] = _param(recency_pts, 5, reason,
                                  recent_skill_count=len(recent_skills),
                                  mid_skill_count=len(mid_skills),
                                  recency_ratio=round(recency_ratio, 3),
                                  recent_skills=[r.get("skill") for r in top_recent_names])

    # ── 4. Year-on-year learning (5 pts) ──────────────────────────────────
    fast_learner = experience.get("fast_learner", False)
    yearly = experience.get("yearly_skill_learning") or []
    if fast_learner:
        yoy_pts = 5.0
        reason = "Fast learner signal active — ≥2 new skills/year across ≥2 years. Full credit."
    elif len(yearly) >= 3:
        yoy_pts = 3.0
        reason = f"New skills acquired across {len(yearly)} calendar years — steady learning curve but not classified fast learner."
    elif len(yearly) >= 1:
        yoy_pts = 2.0
        reason = "Some year-on-year learning detected but limited to 1–2 years. Minimal signal."
    else:
        yoy_pts = 0.0
        reason = "No year-on-year skill learning pattern detected from resume dates."
    bd["skills_learning_acumen"] = _param(yoy_pts, 5, reason,
                                          fast_learner=fast_learner,
                                          years_with_new_skills=len(yearly))

    # ── 5. Certifications (5 pts) — patched in compute_rubric_score ───────
    bd["certifications"] = _param(0, 5, "Pending: patched from education analysis.")

    # ── 6. Coding platforms & community (E17 auto-scored at resume stage) ──
    # E17: Candidates with explicit coding platform profiles or competitive coding
    # mentions (LeetCode, HackerRank, Coding Ninjas, etc.) are auto-scored to give
    # them resume-stage credit. Recruiter can still upgrade to full 4 pts after
    # verifying activity depth (problems solved count, contest ratings, etc.).
    oss_count = sum(1 for r in skill_rows if r.get("open_source_signal"))
    _raw_text = json.dumps(raw_data) if raw_data else ""
    coding_links = list(dict.fromkeys(
        m.group(0) if m.group(0).startswith("http") else "https://" + m.group(0)
        for m in _CODING_LINK_RE.finditer(_raw_text)
    ))
    competitive_platforms = list(dict.fromkeys(
        m.group(0).lower() for m in _COMPETITIVE_CODING_RE.finditer(_raw_text)
    ))
    hackathon_prize_signal = bool(_HACKATHON_PRIZE_RE.search(_raw_text))

    if coding_links:
        # Explicit profile URLs — strongest signal (e.g. leetcode.com/username)
        cc_score = 3.0
        cc_stage = "resume"
        cc_reason = (
            f"E17: {len(coding_links)} coding profile link(s) detected on resume "
            f"({', '.join(coding_links[:2])}{', ...' if len(coding_links) > 2 else ''}). "
            f"{'Competitive platform mentions: ' + ', '.join(competitive_platforms[:3]) + '. ' if competitive_platforms else ''}"
            f"{'Hackathon prize signal detected. ' if hackathon_prize_signal else ''}"
            f"Auto-scored {cc_score}/4. Recruiter to validate activity depth and upgrade if warranted."
        )
    elif competitive_platforms:
        # Platform names mentioned without explicit links (e.g. "300+ LeetCode problems")
        cc_score = 2.0
        cc_stage = "resume"
        cc_reason = (
            f"E17: Competitive coding platform(s) mentioned in resume: "
            f"{', '.join(competitive_platforms[:4])}. "
            f"{'Hackathon prize signal detected. ' if hackathon_prize_signal else ''}"
            f"Auto-scored {cc_score}/4. Recruiter to obtain profile links and validate activity level."
        )
    elif oss_count > 0:
        # Open-source skill signals without explicit platform mention
        cc_score = 1.0
        cc_stage = "resume"
        cc_reason = (
            f"{oss_count} skill(s) with open-source contribution signals detected. "
            f"Auto-scored {cc_score}/4. Recruiter to confirm coding community engagement."
        )
    else:
        cc_score = 0
        cc_stage = "recruiter"
        cc_reason = "No coding platform links or competitive coding signals found. Recruiter to validate and assign 0–4."

    bd["coding_community"] = _param(
        cc_score, 4, cc_reason,
        stage=cc_stage,
        links=coding_links,
        competitive_platforms=competitive_platforms,
        hackathon_prize_signal=hackathon_prize_signal,
        oss_signal_count=oss_count,
    )

    # ── JD skill match flags (display only — no score) ───────────────────
    candidate_skill_names = {str(r.get("skill") or "").lower() for r in skill_rows}
    _mandatory_cfg = (client_role_config or {}).get("mandatory_skills") or []
    if _mandatory_cfg:
        def _skill_name(s): return (s.get("skill") if isinstance(s, dict) else s) or ""
        m_matched = [_skill_name(s) for s in _mandatory_cfg if _skill_name(s).lower() in candidate_skill_names]
        m_missing  = [_skill_name(s) for s in _mandatory_cfg if _skill_name(s).lower() not in candidate_skill_names]
        bd["mandatory_skills"] = {
            "type": "flag", "matched": m_matched, "missing": m_missing,
            "match_rate": f"{len(m_matched)}/{len(_mandatory_cfg)}",
        }
    _gth_cfg = (client_role_config or {}).get("good_to_have_skills") or []
    if _gth_cfg:
        def _skill_name(s): return (s.get("skill") if isinstance(s, dict) else s) or ""  # noqa: F811
        g_matched = [_skill_name(s) for s in _gth_cfg if _skill_name(s).lower() in candidate_skill_names]
        g_missing  = [_skill_name(s) for s in _gth_cfg if _skill_name(s).lower() not in candidate_skill_names]
        bd["good_to_have_skills"] = {
            "type": "flag", "matched": g_matched, "missing": g_missing,
            "match_rate": f"{len(g_matched)}/{len(_gth_cfg)}",
        }

    # ── Panel qualitative fields (no score — panel fills during interview) ─
    bd["coding_skills"]    = {"type": "panel_text", "value": "", "stage": "panel",
                               "note": "Panel only — assess live coding ability during technical interview."}
    bd["conceptual_skills"] = {"type": "panel_text", "value": "", "stage": "panel",
                                "note": "Panel only — assess conceptual understanding of core CS/domain concepts."}

    # ── Recruiter-stage params (start at 0 — filled by recruiter) ────────
    bd["project_explanation"] = _param(0, 3, "Recruiter stage only: quality of project walk-through during discussion.")

    # ── Panel-stage params (all start at 0 — filled at panel stage) ───────
    _PANEL_PARAMS = {
        "communication_skills": (5, "Panel stage only: verbal and written communication clarity."),
        "domain_skills": (5, "Panel stage only: domain-specific knowledge depth assessed in panel."),
        "problem_solving": (3, "Panel stage only: live problem-solving ability assessed in panel."),
    }
    for pkey, (pmax, pmsg) in _PANEL_PARAMS.items():
        bd[pkey] = _param(0, pmax, pmsg)

    _PANEL_KEYS = set(_PANEL_PARAMS.keys())
    base_total = sum(
        v["score"] for k, v in bd.items()
        if isinstance(v, dict) and "score" in v and k not in _PANEL_KEYS
    )
    return {
        "skills_score": round(_clamp(base_total, 0, 45), 1),
        "breakdown": bd,
    }


# ---------------------------------------------------------------------------
# EDUCATION section — 20 pts
# ---------------------------------------------------------------------------

def _score_education_section(education: dict[str, Any]) -> dict[str, Any]:
    """Map education_engine output to a 15-point rubric sub-section (10 core + 5 bonus)."""
    bd: dict[str, Any] = {}
    reject_flags: list[str] = []
    entries = education.get("education_entries") or []

    # ── Core params (10 pts total) ────────────────────────────────────────

    # ── 1. Institute tier (5 pts) — absorbs GPA signal ───────────────────
    highest_tier = str(education.get("highest_institute_tier") or "UNKNOWN").upper()
    gpa_bands = [str(e.get("gpa_band") or "UNKNOWN").upper() for e in entries if e.get("gpa_band")]
    _band_rank = {"EXCELLENT": 2, "GOOD": 1, "ACCEPTABLE": 0, "LOW": -1, "UNKNOWN": 0}
    best_band = max(gpa_bands, key=lambda b: _band_rank.get(b, 0), default="UNKNOWN") if gpa_bands else "UNKNOWN"

    _tier_base = {"TIER_1": 4.0, "TIER_2": 3.0, "TIER_3": 2.0, "TIER_4": 1.0}
    inst_pts = _tier_base.get(highest_tier, 1.0)
    gpa_bonus = 0.0
    if highest_tier == "TIER_1" and best_band in ("EXCELLENT", "GOOD"):
        gpa_bonus = 1.0
    elif highest_tier == "TIER_2" and best_band == "EXCELLENT":
        gpa_bonus = 0.5
    inst_pts = min(inst_pts + gpa_bonus, 5.0)

    _tier_desc = {
        "TIER_1": "Top-tier institution (IIT / NIT / IIM / equivalent global) — strong academic pedigree.",
        "TIER_2": "Well-regarded regional university — solid academic background.",
        "TIER_3": "Mid-tier institution — adequate academic foundation.",
        "TIER_4": "Below-average tier institution — functional credential only.",
    }
    top_inst = (education.get("top_institutes") or ["unknown"])[0]
    # Check if any institute was resolved via LLM search
    llm_resolved = any(e.get("institution_source") == "llm_search" for e in entries)
    source_note = " [tier via AI search]" if llm_resolved else ""
    # Include streams and city if available from LLM
    extra_info = ""
    for e in entries:
        city = e.get("institution_city", "")
        streams = e.get("institution_streams", [])
        nirf = e.get("institution_nirf_rank")
        if city or streams:
            parts = []
            if city:
                parts.append(city)
            if streams:
                parts.append("streams: " + "/".join(streams[:4]))
            if nirf:
                parts.append(f"NIRF #{nirf}")
            extra_info = " | " + ", ".join(parts)
            break
    reason = (
        _tier_desc.get(highest_tier, "Institution not found in dictionary — classified by AI search.")
        + f" ({top_inst}{extra_info}){source_note}"
        + (f" GPA {best_band} → +{gpa_bonus} bonus." if gpa_bonus else "")
    )
    bd["institute_tier"] = _param(inst_pts, 5, reason,
                                   highest_tier=highest_tier,
                                   best_gpa_band=best_band,
                                   institutes=education.get("top_institutes", []),
                                   llm_resolved=llm_resolved)

    # ── 2. Degree level × Stream (2 pts) — IT vs Non-IT matrix ───────────
    # Matrix from scoring sheet:
    # B.Tech IT=1.25  B.Tech Non-IT=1.0
    # M.Tech IT=1.5   M.Tech Non-IT=1.25
    # PhD/Double-Masters IT=2.0  PhD/Double-Masters Non-IT=1.75
    # Combo B.Tech-IT + M.Tech-IT=1.75  B.Tech-IT + M.Tech-Non-IT=1.25
    #        B.Tech-Non-IT + M.Tech-IT=1.5
    degree_levels = [str(e.get("degree_level") or "UNKNOWN").upper() for e in entries]
    it_flags = [bool(e.get("is_it_stream")) for e in entries]

    has_bachelor = any(d in ("BACHELOR",) for d in degree_levels)
    has_master   = any(d in ("MASTER",)   for d in degree_levels)
    has_phd      = any(d in ("PHD",)      for d in degree_levels)
    # IT flag for each degree level
    btech_it = any(
        dl == "BACHELOR" and it_flags[i]
        for i, dl in enumerate(degree_levels)
    )
    mtech_it = any(
        dl == "MASTER" and it_flags[i]
        for i, dl in enumerate(degree_levels)
    )
    phd_it = any(
        dl == "PHD" and it_flags[i]
        for i, dl in enumerate(degree_levels)
    )
    any_it = any(it_flags)

    # Stream relevance label for reason text
    stream_ranks = [e.get("stream_relevance_rank") for e in entries if e.get("stream_relevance_rank") is not None]
    stream_label_map = {1: "ECE/ETC", 2: "EEE", 3: "EE", 4: "Mechanical", 5: "Civil"}
    best_stream_label = stream_label_map.get(min(stream_ranks), "") if stream_ranks else ""

    # Score using combo matrix
    if has_phd or (has_master and has_bachelor):
        if has_phd:
            deg_pts = 2.0 if phd_it else 1.75
            combo_desc = f"PhD ({'IT' if phd_it else 'Non-IT'} stream)"
        else:
            # Double qualification (B.Tech + M.Tech)
            if btech_it and mtech_it:
                deg_pts = 1.75
                combo_desc = "B.Tech IT + M.Tech IT combination"
            elif btech_it and not mtech_it:
                deg_pts = 1.25
                combo_desc = "B.Tech IT + M.Tech Non-IT combination"
            elif not btech_it and mtech_it:
                deg_pts = 1.5
                combo_desc = "B.Tech Non-IT + M.Tech IT combination"
            else:
                deg_pts = 1.25
                combo_desc = "B.Tech Non-IT + M.Tech Non-IT combination"
    elif has_master:
        deg_pts = 1.5 if mtech_it else 1.25
        combo_desc = f"M.Tech ({'IT' if mtech_it else 'Non-IT'} stream)"
    elif has_bachelor:
        deg_pts = 1.25 if btech_it else 1.0
        combo_desc = f"B.Tech ({'IT' if btech_it else 'Non-IT'} stream)"
    else:
        deg_pts = 0.75
        combo_desc = "Diploma or unclassified qualification"

    stream_hint = f" Stream: {best_stream_label}." if best_stream_label else ""
    reason = (
        f"{combo_desc}.{stream_hint} "
        + ("IT stream (CS/CSE/IT/DS) gives higher credit. " if any_it else "Non-IT/ECE stream — partial credit. ")
        + f"Score: {deg_pts}/2."
    )
    bd["degree_level"] = _param(deg_pts, 2, reason,
                                 best_degree_level=max(degree_levels, key=lambda l: {"PHD":4,"MASTER":3,"BACHELOR":2,"DIPLOMA":1}.get(l,0), default="UNKNOWN"),
                                 is_it_stream=any_it,
                                 combo_desc=combo_desc)

    # ── 3. Education-to-job relevance (2 pts) ──────────────────────────────
    course_value = str(education.get("strongest_course_value_signal") or "UNKNOWN").upper()
    has_tech = bool(education.get("has_tech_degree"))
    _course_pts = {"HIGH": 2.0, "MEDIUM": 1.5, "FOUNDATIONAL": 0.5}
    rel_pts = float(_course_pts.get(course_value, 1.0))  # UNKNOWN → 1.0 (neutral)
    _course_desc = {
        "HIGH": "Highly relevant technical degree (CS / CE / IT / Data Science / Engineering). Full credit.",
        "MEDIUM": "Partially relevant degree (Science / Management / related field). Partial credit.",
        "FOUNDATIONAL": "Non-technical or tangentially related degree. Limited relevance.",
    }
    course_fams = education.get("course_families") or []
    reason = (
        _course_desc.get(course_value, "Course relevance unknown — unable to classify degree field.")
        + (f" Families: {', '.join(course_fams[:3])}." if course_fams else "")
    )
    bd["education_job_relevance"] = _param(rel_pts, 2, reason,
                                            course_value_signal=course_value,
                                            has_tech_degree=has_tech,
                                            course_families=course_fams)

    # ── 4. Education gap (1 pt) ────────────────────────────────────────────
    gap_flag = bool(education.get("education_gap_flag"))
    gap_months = _safe_int(education.get("education_gap_months"), 0)
    if gap_months <= 6:
        gap_pts = 1.0
        reason = f"No significant gap between education and first job ({gap_months}m). Smooth transition."
    elif gap_months <= 12:
        gap_pts = 0.5
        reason = f"Moderate gap of {gap_months}m between education end and first job. Recommend brief explanation."
    else:
        gap_pts = 0.0
        reason = f"Large education-to-employment gap of {gap_months}m detected. May indicate difficulty entering workforce."
        # Documented in SCORING_DOCUMENTATION.md ("Gap >12m also triggers
        # REJECT FLAG") but this section previously had no reject_flags list
        # at all to append to -- compute_rubric_score's final reject_flags
        # only ever pulled from the experience section's own list.
        reject_flags.append(f"education_gap={gap_months}m — gap between education and first job exceeds 12 months")
    bd["education_gap"] = _param(gap_pts, 1, reason,
                                  gap_months=gap_months, gap_flag=gap_flag)

    # ── Bonus sub-section (5 pts) — stored under "bonus" key ─────────────
    bonus_bd: dict[str, Any] = {}

    # exec_education (1 pt): continuing / executive / distance education entries
    exec_edu_entries = [
        e for e in entries
        if any(kw in str(e.get("degree_level") or "").lower() + str(e.get("course_family") or "").lower()
               for kw in ("executive", "continuing", "distance", "certification", "online", "mooc"))
    ]
    exec_pts = 1.25 if exec_edu_entries else 0.0
    bonus_bd["exec_education"] = _param(
        exec_pts, 1.25,
        "Continuing / executive / distance education entries found. 1.25 pts awarded."
        if exec_edu_entries else
        "No executive or continuing education entries detected.",
    )

    # patents_publications (2.5 pts) — patched in compute_rubric_score
    bonus_bd["patents_publications"] = _param(0, 2.5, "Pending: patched from experience/overview.")

    # linkedin_activity (1 pt) — filled at recruiter stage
    bonus_bd["linkedin_activity"] = _param(0, 1, "Pending recruiter stage: LinkedIn profile activity signal.")

    # extra_curriculars (1 pt) — auto-detected from resume (bonus); recruiter can update
    _ec_keywords = {
        "volunteer", "volunteering", "ngo", "sports", "cricket", "football", "basketball",
        "badminton", "tennis", "chess", "debate", "cultural", "fest", "hackathon",
        "marathon", "community", "club", "association", "society", "theatre", "music",
        "dance", "painting", "photography", "blog", "podcast", "toastmaster",
    }
    _ec_text = " ".join(
        str(e.get("course_family") or "") + " " + str(e.get("degree_name") or "") + " " + str(e.get("activities") or "")
        for e in entries
    ).lower()
    ec_signal = any(kw in _ec_text for kw in _ec_keywords)
    ec_pts = 1.25 if ec_signal else 0.0
    bonus_bd["extra_curriculars"] = _param(
        ec_pts, 1.25,
        ("Extra-curricular signals detected in resume (sports, volunteering, community, hobbies). 1.25 pts awarded. Recruiter can validate."
         if ec_signal else
         "No extra-curricular signals detected in resume. Recruiter to validate and award if applicable."),
        resume_signal=ec_signal,
    )

    bd["bonus"] = bonus_bd

    core_total = sum(
        v["score"] for k, v in bd.items()
        if isinstance(v, dict) and "score" in v and k != "bonus"
    )
    bonus_total = sum(
        v["score"] for v in bonus_bd.values()
        if isinstance(v, dict) and "score" in v
    )
    edu_score = round(_clamp(core_total + bonus_total, 0, 15), 2)
    # Dynamic denominator: show /10 when no bonus points earned, /15 when bonus > 0
    edu_display_max = 15 if bonus_total > 0 else 10
    return {
        "education_score": edu_score,
        "education_core_score": round(core_total, 2),
        "education_bonus_score": round(bonus_total, 2),
        "education_display_max": edu_display_max,
        "breakdown": bd,
        "reject_flags": reject_flags,
    }


# ---------------------------------------------------------------------------
# LLM judging pass — qualitative parameters
# ---------------------------------------------------------------------------

def _llm_judge_rubric_params(
    experience: dict[str, Any],
    evidence_map: dict[str, Any],
    exp_breakdown: dict[str, Any],
    skills_breakdown: dict[str, Any],
) -> dict[str, Any] | None:
    """Single LLM call that reviews and overrides qualitative rubric parameters.

    Judges: career_progression, international_exposure, stakeholder_management,
    mentorship_signal, awards_recognition (experience section) and
    unique_skill_combos (skills section).

    Returns LLM JSON or None on failure / provider unavailable.
    """
    try:
        from llm_client import call_llm_json, analysis_model, provider_enabled  # type: ignore
        if not provider_enabled():
            return None
    except Exception:
        return None

    try:
        from llm_judging_assets import RUBRIC_LLM_JUDGE_SYSTEM_PROMPT  # type: ignore
    except Exception:
        return None

    # Build compact evidence payload
    job_titles = experience.get("titles") or experience.get("job_titles") or []
    companies = experience.get("companies") or []
    projects = [
        {
            "title": p.get("title", ""),
            "type": p.get("project_type", ""),
            "skills": (p.get("skills") or [])[:6],
            "description": (p.get("description") or "")[:200],
        }
        for p in (experience.get("project_types") or [])[:3]
    ]
    achievements = (experience.get("achievements") or [])[:5]
    strong_skills = [
        {"skill": v.get("skill", k), "evidence": v.get("evidence_level"), "years": v.get("years_of_usage")}
        for k, v in list(evidence_map.items())[:20]
        if isinstance(v, dict) and v.get("evidence_level") in ("APPLIED", "DEEP", "EXPERT")
    ]

    anchors = {
        "career_progression": {
            "score": (exp_breakdown.get("career_progression") or {}).get("score", 0), "max": 4,
        },
        "international_exposure": {
            "score": (exp_breakdown.get("international_exposure") or {}).get("score", 0), "max": 2,
        },
        "stakeholder_management": {
            "score": (exp_breakdown.get("stakeholder_management") or {}).get("score", 0), "max": 2,
        },
        "mentorship_signal": {
            "score": (exp_breakdown.get("mentorship_signal") or {}).get("score", 0), "max": 3,
        },
        "awards_recognition": {
            "score": (exp_breakdown.get("awards_recognition") or {}).get("score", 0), "max": 4,
        },
    }

    payload = {
        "job_history": [
            {"title": t, "company": c}
            for t, c in zip(job_titles[:5], companies[:5])
        ],
        "projects": projects,
        "achievements": achievements,
        "strong_skills": strong_skills,
        "deterministic_anchors": anchors,
    }

    try:
        result = call_llm_json(
            analysis_model("qwen2.5:14b-instruct"),
            [
                {"role": "system", "content": RUBRIC_LLM_JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=900,
        )
        return result
    except Exception:
        return None


def _merge_llm_judges(
    llm_result: dict[str, Any],
    exp_breakdown: dict[str, Any],
    skills_breakdown: dict[str, Any],
) -> None:
    """Merge LLM-judged scores into breakdown dicts in-place.

    Params that are pending recruiter/panel fill (score must stay 0 at resume
    stage) are skipped — LLM can only annotate their reason, not set a score.
    """
    for param, judged in (llm_result.get("experience") or {}).items():
        if not isinstance(judged, dict) or param not in exp_breakdown:
            continue
        entry = exp_breakdown[param]
        if not isinstance(entry, dict):
            continue
        # Skip recruiter/panel-pending params — score stays 0 until that stage
        if entry.get("pending") or STAGE_MAP.get(param, "resume") != "resume":
            if judged.get("reason"):
                entry["reason"] = judged["reason"]  # annotate reason only
            continue
        raw_score = _safe_float(judged.get("score"), entry.get("score", 0))
        entry["score"] = round(_clamp(raw_score, 0, entry.get("max", raw_score)), 1)
        if judged.get("reason"):
            entry["reason"] = judged["reason"]
        entry["llm_confidence"] = judged.get("confidence", "MEDIUM")
        entry["llm_judged"] = True

    for param, judged in (llm_result.get("skills") or {}).items():
        if not isinstance(judged, dict) or param not in skills_breakdown:
            continue
        entry = skills_breakdown[param]
        if not isinstance(entry, dict):
            continue
        # Skip recruiter/panel-pending params
        if entry.get("pending") or STAGE_MAP.get(param, "resume") != "resume":
            if judged.get("reason"):
                entry["reason"] = judged["reason"]
            continue
        raw_score = _safe_float(judged.get("score"), entry.get("score", 0))
        entry["score"] = round(_clamp(raw_score, 0, entry.get("max", raw_score)), 1)
        if judged.get("reason"):
            entry["reason"] = judged["reason"]
        if judged.get("combinations"):
            entry["combinations"] = judged["combinations"]
        entry["llm_confidence"] = judged.get("confidence", "MEDIUM")
        entry["llm_judged"] = True


# ---------------------------------------------------------------------------
# LLM justification pass — recruiter-readable explanations for every param
# ---------------------------------------------------------------------------

def _llm_justify_all_params(
    exp_breakdown: dict[str, Any],
    skills_breakdown: dict[str, Any],
    edu_breakdown: dict[str, Any],
    bert_priors: dict[str, Any],
    experience: dict[str, Any],
    evidence_map: dict[str, Any],
) -> dict[str, Any] | None:
    """Single LLM call that writes a plain-English justification for every rubric param.

    Never changes scores — purely annotates the breakdown with llm_justification strings.
    Returns the parsed JSON dict (with 'justifications' key) or None on failure.
    """
    try:
        from llm_client import call_llm_json, analysis_model, provider_enabled  # type: ignore
    except ImportError:
        return None
    if not provider_enabled():
        return None

    try:
        from llm_judging_assets import RUBRIC_JUSTIFY_SYSTEM_PROMPT  # type: ignore
    except ImportError:
        return None

    # ── Build compact candidate evidence packet ──────────────────────
    titles   = [str(t) for t in (experience.get("titles") or [])[:5]]
    companies = [str(c) for c in (experience.get("companies") or [])[:5]]
    achievements = [str(a) for a in (experience.get("achievements") or [])[:4]]
    total_yoe = experience.get("total_experience_years", 0)

    # BERT priors for key params
    cp_prior  = bert_priors.get("career_progression_prior") or {}
    sm_prior  = bert_priors.get("stakeholder_prior") or {}
    ms_prior  = bert_priors.get("mentorship_prior") or {}
    rf_prior  = bert_priors.get("role_family_prior") or {}
    dna_prior = bert_priors.get("dna_prior") or {}
    skill_depth_priors = [
        {"skill": p.get("skill"), "depth": p.get("predicted_depth_label"), "conf": round(_safe_float(p.get("confidence"), 0.0), 2)}
        for p in (bert_priors.get("skill_depth_priors") or [])[:6]
        if isinstance(p, dict)
    ]

    # Top scored projects (brief) — enriched with LLM deep judgment if available
    project_types = (experience.get("project_types") or [])[:2]
    _llm_proj_result_j = experience.get("_llm_project_judgment") or {}
    _llm_proj_judgments_j: dict[int, dict[str, Any]] = {
        int(j.get("project_index", 0)): j
        for j in (_llm_proj_result_j.get("project_judgments") or [])
        if isinstance(j, dict)
    }
    _cand_assessment_j = _llm_proj_result_j.get("candidate_assessment") or {}

    projects_brief = []
    for _pidx, p in enumerate(project_types, start=1):
        if not isinstance(p, dict):
            continue
        _lj = _llm_proj_judgments_j.get(_pidx) or {}
        # Prefer LLM confirmed type; fall back to rule-detected
        _ptype = _lj.get("confirmed_type") or str(p.get("project_type") or "")
        # Prefer LLM implied skills (more accurate); fall back to raw skills
        _pskills = (_lj.get("implied_skills") or p.get("skills") or [])[:6]
        projects_brief.append({
            "project_index": _pidx,
            "title": str(p.get("title") or p.get("role") or "")[:60],
            "company": str(p.get("company") or "")[:40],
            "type": _ptype,
            "verdict_label": _lj.get("verdict_label") or "",
            "candidate_signal": _lj.get("candidate_signal") or "",
            "skill_exhibition_type": _lj.get("skill_exhibition_type") or "",
            "scope_assessment": (_lj.get("scope_assessment") or "")[:150],
            "role_intent": (_lj.get("role_intent") or "")[:100],
            "implied_skills": _pskills,
            "business_impact": str(p.get("business_impact") or "")[:120],
            "description": str(p.get("description") or "")[:200],
        })

    # Compact scored param snapshot — score/max + key metadata only
    def _snap(d: dict) -> dict:
        keep = {"score", "max", "bert_label", "bert_confidence",
                "yoe_total", "yoe_relevant", "avg_tenure_months", "loyalty_signal",
                "best_tier", "best_company", "achievement_count",
                "strong_skill_count", "raw_skill_years", "strong_skills",
                "recency_ratio", "recent_skills", "fast_learner",
                "cert_count", "certifications",
                "highest_tier", "best_degree_level", "gap_months", "course_value_signal"}
        snap = {k: v for k, v in d.items() if k in keep}
        # Trim lists to avoid token bloat
        for list_key in ("strong_skills", "recent_skills", "certifications"):
            if isinstance(snap.get(list_key), list):
                snap[list_key] = snap[list_key][:6]
        # Add brief rule reason as anchor
        if d.get("reason"):
            snap["anchor"] = str(d["reason"])[:90]
        return snap

    exp_snap = {
        k: _snap(v) for k, v in exp_breakdown.items()
        if isinstance(v, dict) and "score" in v
    }
    skills_snap = {
        k: _snap(v) for k, v in skills_breakdown.items()
        if isinstance(v, dict) and "score" in v
    }
    edu_core_snap = {
        k: _snap(v) for k, v in edu_breakdown.items()
        if isinstance(v, dict) and "score" in v and k != "bonus"
    }
    edu_bonus_snap = {
        k: _snap(v) for k, v in (edu_breakdown.get("bonus") or {}).items()
        if isinstance(v, dict) and "score" in v
    }

    evidence_packet = {
        "candidate": {
            "total_yoe": total_yoe,
            "titles": titles,
            "companies": companies,
            "achievements": achievements,
        },
        "bert_signals": {
            "career_progression": {"label": cp_prior.get("label"), "conf": round(_safe_float(cp_prior.get("confidence"), 0.0), 2)},
            "stakeholder":        {"label": sm_prior.get("label"), "conf": round(_safe_float(sm_prior.get("confidence"), 0.0), 2)},
            "mentorship":         {"label": ms_prior.get("label"), "conf": round(_safe_float(ms_prior.get("confidence"), 0.0), 2)},
            "role_family":        {"label": rf_prior.get("label"), "conf": round(_safe_float(rf_prior.get("confidence"), 0.0), 2)},
            "dna_fit":            {"label": dna_prior.get("label"), "conf": round(_safe_float(dna_prior.get("confidence"), 0.0), 2)},
            "skill_depth_priors": skill_depth_priors,
        },
        # projects includes LLM deep judgment fields (verdict_label, candidate_signal, etc.)
        "projects": projects_brief,
        # holistic candidate assessment from LLM deep judgment (if available)
        "candidate_assessment": _cand_assessment_j if _cand_assessment_j else None,
        "scored_params": {
            "experience": exp_snap,
            "skills":     skills_snap,
            "education":  {**edu_core_snap, "bonus": edu_bonus_snap},
        },
    }

    user_msg = (
        "Write a recruiter-readable justification for every parameter in scored_params. "
        "Use the candidate evidence, BERT signals, project details (including verdict_label, candidate_signal, scope_assessment), "
        "and candidate_assessment provided. "
        "For project_1 and project_2, follow the special rules for project justifications.\n\n"
        + json.dumps(evidence_packet, ensure_ascii=False)
    )

    try:
        result = call_llm_json(
            analysis_model("qwen2.5:14b-instruct"),
            [
                {"role": "system", "content": RUBRIC_JUSTIFY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=2400,  # increased: project_1/project_2 now 80-120 words each
        )
        return result if isinstance(result, dict) and "justifications" in result else None
    except Exception:
        return None


def _merge_justifications(
    justifications: dict[str, str],
    exp_breakdown: dict[str, Any],
    skills_breakdown: dict[str, Any],
    edu_breakdown: dict[str, Any],
) -> None:
    """Write llm_justification field in-place onto every matched param entry."""
    section_maps: list[dict[str, Any]] = [
        exp_breakdown,
        skills_breakdown,
        edu_breakdown,
        edu_breakdown.get("bonus") or {},
    ]
    for param_key, text in justifications.items():
        if not isinstance(text, str) or not text.strip():
            continue
        for section in section_maps:
            if param_key in section and isinstance(section[param_key], dict):
                section[param_key]["llm_justification"] = text.strip()
                break


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_PANEL_SKILL_KEYS = {"communication_skills", "domain_skills", "problem_solving"}

# ---------------------------------------------------------------------------
# Stage map — defines when each rubric param is scored / filled
# resume   = scored automatically from resume text (may be updated later)
# recruiter = starts at 0, filled by recruiter after discussion
# panel    = starts at 0, filled by panel after technical interview
# ---------------------------------------------------------------------------
STAGE_MAP: dict[str, str] = {
    # Experience — auto-scored from resume
    "overall_experience":       "resume",
    "career_breaks":            "resume",
    "career_progression":       "resume",
    "stability":                "resume",
    "company_tier":             "resume",
    "awards_recognition":       "resume",
    "project_1":                "resume",
    "project_2":                "resume",
    # Experience — E16: auto-scored from resume (1.5 if detected, 0 if not); recruiter upgrades.
    # Kept as "recruiter" in STAGE_MAP so the LLM judge can only annotate reason, not override score.
    # The _recruiter_exp_excl set in compute_rubric_score is updated to let E16 score count in total.
    "international_exposure":   "recruiter",
    # Experience — recruiter fills after phone screen (score starts at 0)
    "stakeholder_management":   "recruiter",
    "mentorship_signal":        "recruiter",
    # Skills — auto-scored from resume
    "skill_list_years":         "resume",
    "skill_depth":              "resume",
    "skill_recency":            "resume",
    "skills_learning_acumen":   "resume",
    "certifications":           "resume",
    # Skills — auto-scored from resume (E17: platform detected → 2-3; none → 0+recruiter upgrades)
    "coding_community":         "resume",
    # Skills — recruiter fills after phone screen (starts at 0)
    "project_explanation":      "recruiter",
    # Skills — panel fills after technical interview (start at 0)
    "communication_skills":     "panel",
    "domain_skills":            "panel",
    "problem_solving":          "panel",
    # Education core — auto-scored from resume
    "institute_tier":           "resume",
    "degree_level":             "resume",
    "education_job_relevance":  "resume",
    "education_gap":            "resume",
    # Education bonus — auto-detected from resume; recruiter/panel can update
    "exec_education":           "resume",
    "patents_publications":     "resume",
    "extra_curriculars":        "resume",
    # Education bonus — recruiter fills after discussion
    "linkedin_activity":        "recruiter",
}

# Params recruiter fills for the first time (score starts at 0 at resume stage)
# Note: international_exposure and coding_community are now auto-scored (E16/E17)
# and moved to RECRUITER_UPDATABLE — recruiter can upgrade the auto-score.
RECRUITER_FILLS = {
    "stakeholder_management", "mentorship_signal",
    "project_explanation", "linkedin_activity",
}

# Params recruiter can re-score/validate (already have a resume score)
RECRUITER_UPDATABLE = {
    "international_exposure", "stakeholder_management", "mentorship_signal",
    "awards_recognition", "project_1", "project_2",
    "linkedin_activity", "extra_curriculars", "project_explanation",
    "skills_learning_acumen", "skill_list_years",
    "coding_community",  # E17: auto-scored at resume; recruiter upgrades after discussion
}

# Params that panel can update/add
PANEL_UPDATABLE = {
    "communication_skills", "domain_skills", "problem_solving",
    "skill_depth", "skill_recency", "project_1", "project_2",
    "project_explanation", "skills_learning_acumen",
}


def _tag_stages(breakdown: dict[str, dict]) -> None:
    """Add 'stage' field in-place to every param entry using STAGE_MAP."""
    for key, val in breakdown.items():
        if isinstance(val, dict) and "score" in val:
            val["stage"] = STAGE_MAP.get(key, "resume")
        elif key == "bonus" and isinstance(val, dict):
            # Education bonus sub-dict
            for bkey, bval in val.items():
                if isinstance(bval, dict) and "score" in bval:
                    bval["stage"] = STAGE_MAP.get(bkey, "resume")


def compute_rubric_score(
    evidence_map: dict[str, Any],
    semantic: dict[str, Any],
    experience: dict[str, Any],
    dna: dict[str, Any],
    education: dict[str, Any],
    bert_priors: dict[str, Any],
    client_role_config: dict[str, Any] | None = None,
    raw_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute 40 + 45 + 15 = 100-point rubric scorecard (3-stage model).

    Returns:
        total_score (0–100), experience_score (0–40), skills_score (0–45),
        education_score (0–15), breakdown, reject_flags, timestamp.
    """
    exp_result = _score_experience_section(experience, bert_priors, client_role_config, education=education)
    skills_result = _score_skills_section(evidence_map, experience, bert_priors, client_role_config, raw_data=raw_data)
    edu_result = _score_education_section(education)

    # ── Patch certifications into skills from education ────────────────────
    cert_list = education.get("certificates") or education.get("certifications") or []
    cert_count = len(cert_list)
    cert_pts = _clamp(float(cert_count), 0, 5)
    cert_names = [str(c) for c in cert_list[:5]]
    if cert_count >= 5:
        cert_reason = f"{cert_count} certifications found: {', '.join(cert_names)}. Full credit."
    elif cert_count >= 3:
        cert_reason = f"{cert_count} certifications found: {', '.join(cert_names)}. Good credit."
    elif cert_count >= 1:
        cert_reason = f"{cert_count} certification(s) found: {', '.join(cert_names)}. Partial credit."
    else:
        cert_reason = "No professional certifications detected in education or certificates section."
    skills_result["breakdown"]["certifications"] = _param(
        cert_pts, 5, cert_reason, cert_count=cert_count, certifications=cert_names
    )

    # ── E8 / R6: Cert farming guard ────────────────────────────────────────
    # If certs ≥ 5 but skill depth is FOUNDATIONAL or below → cap cert score at 2 pts.
    _cert_farming_flag: str | None = None
    if cert_count >= 5:
        _skill_rows_cf = list(evidence_map.values()) if isinstance(evidence_map, dict) else []
        _strong_rows_cf = [
            r for r in _skill_rows_cf
            if str(r.get("evidence_level") or "").upper() in {"APPLIED", "DEEP", "EXPERT"}
        ]
        _depth_priors_cf = bert_priors.get("skill_depth_priors") or []
        _avg_bert_depth_cf = 0.0
        if _depth_priors_cf:
            _bscores = [
                BERT_DEPTH_TO_SCORE.get(str(p.get("predicted_depth_label") or "").upper(), 0.0)
                for p in _depth_priors_cf
            ]
            _avg_bert_depth_cf = sum(_bscores) / len(_bscores) if _bscores else 0.0
        if len(_strong_rows_cf) == 0 and _avg_bert_depth_cf <= BERT_DEPTH_TO_SCORE.get("FOUNDATIONAL", 1.5):
            _cert_entry = skills_result["breakdown"].get("certifications") or {}
            if isinstance(_cert_entry, dict) and _cert_entry.get("score", 0) > 2.0:
                _cert_entry["score"] = 2.0
                _cert_entry["reason"] = (
                    _cert_entry.get("reason", "") +
                    " [E8: Cert farming guard — capped at 2/5 pts: high cert count with foundational skill depth only.]"
                )
            _cert_farming_flag = (
                f"R6/E8: Cert farming detected — {cert_count} certifications but avg skill depth is "
                f"FOUNDATIONAL or below (BERT avg={_avg_bert_depth_cf:.2f}/5). "
                "Score capped at 2 pts. Verify real-world application in technical screen."
            )

    # ── Patch patents / publications into education bonus ──────────────────
    has_patents = bool(experience.get("patents")) or any(
        "patent" in str(v).lower() for v in (experience.get("achievements") or [])
    )
    # TIER_1/TIER_2 institutions already signal exceptional academic capability.
    # Reduce the patent penalty for elite graduates: TIER_1 gets 0.5 base pts,
    # TIER_2 gets 0.25 base pts, even without explicit patents/publications.
    edu_tier = str(education.get("highest_institute_tier") or "UNKNOWN").upper()
    patent_base = 0.5 if edu_tier == "TIER_1" else 0.25 if edu_tier == "TIER_2" else 0.0
    patent_pts = max(patent_base, min(float(has_patents) * 2.5, 2.5))
    if has_patents:
        patent_reason = "Patent or publication signal found in resume. 2.5 pts awarded."
    elif patent_base > 0:
        patent_reason = (
            f"{edu_tier} institution — {patent_base} pts awarded for elite academic research culture. "
            f"Full 2.5 pts available if patents/publications detected."
        )
    else:
        patent_reason = "No patents or publications detected in resume."
    edu_result["breakdown"]["bonus"]["patents_publications"] = _param(
        patent_pts, 2.5, patent_reason, has_patents=has_patents, institute_tier=edu_tier
    )

    # Recompute education score after patents patch
    edu_core = sum(
        v["score"] for k, v in edu_result["breakdown"].items()
        if isinstance(v, dict) and "score" in v and k != "bonus"
    )
    edu_bonus = sum(
        v["score"] for v in (edu_result["breakdown"].get("bonus") or {}).values()
        if isinstance(v, dict) and "score" in v
    )
    edu_result["education_score"] = round(_clamp(edu_core + edu_bonus, 0, 15), 1)

    # ── Recompute skills total after patches (exclude panel params) ────────
    skills_base = sum(
        v["score"] for k, v in skills_result["breakdown"].items()
        if isinstance(v, dict) and "score" in v and k not in _PANEL_SKILL_KEYS
    )
    skills_result["skills_score"] = round(_clamp(skills_base, 0, 45), 1)

    # ── LLM judging pass for qualitative parameters ───────────────────────
    llm_judges = _llm_judge_rubric_params(
        experience, evidence_map,
        exp_result["breakdown"], skills_result["breakdown"],
    )
    llm_judged = False
    if llm_judges and isinstance(llm_judges, dict):
        _merge_llm_judges(llm_judges, exp_result["breakdown"], skills_result["breakdown"])
        llm_judged = True
        # Recompute section totals after LLM adjustments
        # Exclude recruiter/panel-pending params so their score=0 doesn't pollute.
        # international_exposure excluded from this set (E16: now auto-scored from resume signal).
        _recruiter_exp_excl = {"stakeholder_management", "mentorship_signal"}
        exp_total = sum(
            v["score"] for k, v in exp_result["breakdown"].items()
            if isinstance(v, dict) and "score" in v
            and k != "operating_model_tag"
            and k not in _recruiter_exp_excl
        )
        exp_result["experience_score"] = round(_clamp(exp_total, 0, 40), 1)
        skills_base = sum(
            v["score"] for k, v in skills_result["breakdown"].items()
            if isinstance(v, dict) and "score" in v and k not in _PANEL_SKILL_KEYS
        )
        skills_result["skills_score"] = round(_clamp(skills_base, 0, 45), 1)

    total = round(
        _clamp(
            exp_result["experience_score"]
            + skills_result["skills_score"]
            + edu_result["education_score"],
            0, 100,
        ),
        1,
    )

    # ── Archetype detection + dynamic weight reallocation ─────────────────
    _archetype = detect_archetype(experience, education)
    _arch_weights = WEIGHT_TABLE.get(_archetype, WEIGHT_TABLE["A1"])
    _exp_raw = exp_result["experience_score"]
    _skills_raw = skills_result["skills_score"]
    _edu_raw = edu_result["education_score"]
    _exp_adj = round(_clamp((_exp_raw / _BASE_MAXES["exp"]) * _arch_weights["exp"], 0.0, _arch_weights["exp"]), 1)
    _skills_adj = round(_clamp((_skills_raw / _BASE_MAXES["skills"]) * _arch_weights["skills"], 0.0, _arch_weights["skills"]), 1)
    _edu_adj = round(_clamp((_edu_raw / _BASE_MAXES["edu"]) * _arch_weights["edu"], 0.0, _arch_weights["edu"]), 1)
    _archetype_total = round(_clamp(_exp_adj + _skills_adj + _edu_adj, 0.0, 100.0), 1)

    # ── Red flag detection (R1-R20) ────────────────────────────────────────
    _red_flags = detect_red_flags(
        experience=experience,
        education=education,
        evidence_map=evidence_map,
        bert_priors=bert_priors,
        cert_count=cert_count,
        jd_config=client_role_config,
    )
    if _cert_farming_flag:
        _red_flags["soft"].append(_cert_farming_flag)

    # ── LLM justification pass — recruiter-readable reasons ───────────────
    llm_justified = False
    try:
        justify_result = _llm_justify_all_params(
            exp_result["breakdown"],
            skills_result["breakdown"],
            edu_result["breakdown"],
            bert_priors,
            experience,
            evidence_map,
        )
        if justify_result and isinstance(justify_result.get("justifications"), dict):
            _merge_justifications(
                justify_result["justifications"],
                exp_result["breakdown"],
                skills_result["breakdown"],
                edu_result["breakdown"],
            )
            llm_justified = True
    except Exception:
        pass  # purely additive — never blocks scoring

    # ── Tag every param with its stage ────────────────────────────────────
    _tag_stages(exp_result["breakdown"])
    _tag_stages(skills_result["breakdown"])
    _tag_stages(edu_result["breakdown"])

    # ── Stage score summary ────────────────────────────────────────────────
    # Recruiter upgrades these (may already have a resume auto-score via E16/E17):
    #   experience: international_exposure(2) + stakeholder_management(2) + mentorship_signal(3) = 7
    #   skills:     project_explanation(3) + coding_community(4) = 7
    #   edu bonus:  linkedin_activity(1) = 1
    #   TOTAL recruiter_can_add = 15  (max available; actual addable = max - auto_score)
    # Panel fills these from scratch (score=0 at resume+recruiter stage):
    #   skills: communication_skills(5) + domain_skills(5) + problem_solving(3) = 13
    # Note: Grand total at each stage from Excel (Score A=100, Score B=100, Final=100)
    _recruiter_exp_pending  = {"international_exposure", "stakeholder_management", "mentorship_signal"}
    _recruiter_skill_pending = {"project_explanation", "coding_community"}
    _recruiter_edu_pending  = {"linkedin_activity"}
    _recruiter_pending_keys = _recruiter_exp_pending | _recruiter_skill_pending | _recruiter_edu_pending
    _panel_pending_keys = _PANEL_SKILL_KEYS  # comm(5)+domain(5)+prob(3) = 13
    edu_bonus_bd = edu_result["breakdown"].get("bonus") or {}
    # recruiter_can_add: max remaining pts recruiter can add (total max minus already auto-scored)
    recruiter_can_add = (
        sum(
            max(0, (exp_result["breakdown"].get(k) or {}).get("max", 0)
                - (exp_result["breakdown"].get(k) or {}).get("score", 0))
            for k in _recruiter_exp_pending
        )
        + sum(
            max(0, (skills_result["breakdown"].get(k) or {}).get("max", 0)
                - (skills_result["breakdown"].get(k) or {}).get("score", 0))
            for k in _recruiter_skill_pending
        )
        + sum((edu_bonus_bd.get(k) or {}).get("max", 0) for k in _recruiter_edu_pending)
    )
    panel_can_add = sum(
        (skills_result["breakdown"].get(k) or {}).get("max", 0)
        for k in _panel_pending_keys
    )

    # Section-specific pending breakdowns for UI display
    exp_recruiter_pending = sum(
        max(0, (exp_result["breakdown"].get(k) or {}).get("max", 0)
            - (exp_result["breakdown"].get(k) or {}).get("score", 0))
        for k in _recruiter_exp_pending
    )  # up to 7 pts: international_exposure(0-2) + stakeholder_management(2) + mentorship_signal(3)
    skills_recruiter_pending = sum(
        max(0, (skills_result["breakdown"].get(k) or {}).get("max", 0)
            - (skills_result["breakdown"].get(k) or {}).get("score", 0))
        for k in _recruiter_skill_pending
    )  # up to 7 pts: coding_community(0-4) + project_explanation(3)
    edu_recruiter_pending = sum(
        (edu_bonus_bd.get(k) or {}).get("max", 0) for k in _recruiter_edu_pending
    )  # 1 pt: linkedin_activity(1)

    # Section-level resume-achievable maxes: sum of max values for STAGE_MAP=="resume" params
    # This correctly reflects Score A column totals: exp=40, skills=30, edu=15 → total=85
    exp_resume_max = sum(
        (exp_result["breakdown"].get(k) or {}).get("max", 0)
        for k, v in exp_result["breakdown"].items()
        if isinstance(v, dict) and "max" in v and STAGE_MAP.get(k, "resume") == "resume"
    )
    skills_resume_max = sum(
        (skills_result["breakdown"].get(k) or {}).get("max", 0)
        for k, v in skills_result["breakdown"].items()
        if isinstance(v, dict) and "max" in v and STAGE_MAP.get(k, "resume") == "resume"
    )
    edu_resume_max = (
        sum(
            (edu_result["breakdown"].get(k) or {}).get("max", 0)
            for k, v in edu_result["breakdown"].items()
            if isinstance(v, dict) and "max" in v and k != "bonus" and STAGE_MAP.get(k, "resume") == "resume"
        )
        + sum(
            (edu_bonus_bd.get(k) or {}).get("max", 0)
            for k, v in edu_bonus_bd.items()
            if isinstance(v, dict) and "max" in v and STAGE_MAP.get(k, "resume") == "resume"
        )
    )
    resume_max_pts = exp_resume_max + skills_resume_max + edu_resume_max  # = 85 (Score A)
    recruiter_max_pts = 100  # Score B — all three stages total 100 per Excel
    resume_score_100 = round(
        _clamp((total / resume_max_pts * 100) if resume_max_pts > 0 else 0.0, 0.0, 100.0), 1
    )

    stage_scores = {
        "resume_score":                 total,
        "resume_score_100":             resume_score_100,
        "resume_max":                   resume_max_pts,
        "recruiter_can_add":            recruiter_can_add,
        "recruiter_max":                recruiter_max_pts,
        "recruiter_pending_params":     sorted(_recruiter_pending_keys),
        "exp_recruiter_pending_pts":    exp_recruiter_pending,
        "skills_recruiter_pending_pts": skills_recruiter_pending,
        "edu_recruiter_pending_pts":    edu_recruiter_pending,
        "panel_can_add":                panel_can_add,
        "panel_max":                    100,
        "panel_pending_params":         sorted(_panel_pending_keys),
        "full_score_potential":         100,
        # Section-level denominators per stage
        "exp_resume_max":               exp_resume_max,
        "skills_resume_max":            skills_resume_max,
        "edu_resume_max":               edu_resume_max,
    }

    # Extract credibility check from experience breakdown (metadata only)
    _cred_check = exp_result["breakdown"].pop("_credibility_check", None)

    return {
        "total_score": total,
        "experience_score": exp_result["experience_score"],
        "skills_score": skills_result["skills_score"],
        "education_score": edu_result["education_score"],
        "education_core_score": edu_result.get("education_core_score", edu_result["education_score"]),
        "education_bonus_score": edu_result.get("education_bonus_score", 0.0),
        "education_display_max": edu_result.get("education_display_max", 15),
        "breakdown": {
            "experience": exp_result["breakdown"],
            "skills": skills_result["breakdown"],
            "education": edu_result["breakdown"],
        },
        "credibility_check": _cred_check,   # reverse-engineering report — metadata only
        "reject_flags": exp_result["reject_flags"] + edu_result.get("reject_flags", []),
        "max_scores": {"experience": 40, "skills": 45, "education": 15, "total": 100},
        "stage_scores": stage_scores,
        # ── Archetype + dynamic weights ────────────────────────────────────
        "archetype": _archetype,
        "archetype_weights": _arch_weights,
        "archetype_total_score": _archetype_total,
        "archetype_section_scores": {
            "experience": _exp_adj,
            "skills": _skills_adj,
            "education": _edu_adj,
        },
        # ── Red flags (R1-R20) ─────────────────────────────────────────────
        "red_flags": _red_flags,
        "llm_judged": llm_judged,
        "llm_justified": llm_justified,
        "timestamp": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Red flag detection (R1–R20)
# ---------------------------------------------------------------------------

def detect_red_flags(
    experience: dict[str, Any],
    education: dict[str, Any],
    evidence_map: dict[str, Any],
    bert_priors: dict[str, Any],
    cert_count: int = 0,
    jd_config: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Detect hard and soft red flags from candidate signals.

    Returns {"hard": [...], "soft": [...]} with flag codes + messages.
    Hard flags → auto-reject or strong reject recommendation.
    Soft flags → recruiter attention, not auto-reject.

    Flags R1–R20 follow EDGE_CASES_AND_RED_FLAGS.md Section 3.
    """
    hard: list[str] = []
    soft: list[str] = []

    yoe = _safe_float(experience.get("total_experience_years"), 0.0)
    titles_raw: list = experience.get("titles") or experience.get("job_titles") or []
    companies: list = experience.get("companies") or []
    tenures: list = experience.get("tenures") or []
    project_types: list = experience.get("project_types") or []
    achievements: list = experience.get("achievements") or []
    tenure_with_dates: list = experience.get("tenure_with_dates") or []
    leadership_score = _safe_int(experience.get("leadership_signal_score"), 0)
    skill_rows = list(evidence_map.values()) if isinstance(evidence_map, dict) else []
    strong_rows = [r for r in skill_rows if str(r.get("evidence_level") or "").upper() in {"APPLIED", "DEEP", "EXPERT"}]
    edu_tier_str = str(education.get("highest_institute_tier") or "UNKNOWN").upper()
    edu_entries: list = education.get("education_entries") or []

    # Best company tier (recompute here to avoid dependency on _pretier_map scope)
    best_tier_rf = 5
    tier_seq: list[int] = []
    for c in companies:
        t = classify_company_tier(str(c or ""), llm_fallback=False)
        tier_seq.append(t)
        if t < best_tier_rf:
            best_tier_rf = t

    # ── R1: 3+ hard career breaks ─────────────────────────────────────────
    _bi = _detect_career_breaks(tenure_with_dates, edu_entries)
    if _bi["break_count"] >= 3:
        hard.append(
            f"R1: {_bi['break_count']} hard career breaks (>18m each, non-educational) detected. "
            "Strong reject signal — investigate or reject."
        )

    # ── R2: Declining company tier trajectory ─────────────────────────────
    if len(tier_seq) >= 3:
        # Recent = first entries (most recent job first in companies list)
        if tier_seq[0] <= 2 and tier_seq[-1] >= 4 and (tier_seq[-1] - tier_seq[0]) >= 2:
            hard.append(
                f"R2: Downward brand trajectory — company tier sequence {tier_seq[:4]} "
                "(started strong, ended at TIER_4/5). Investigate reason."
            )

    # ── R3: Title inflation without scope ─────────────────────────────────
    _inflated_title_kws = ("director", "vp", "vice president", "head of", "chief")
    inflated = [t for t in titles_raw if any(kw in str(t).lower() for kw in _inflated_title_kws)]
    if inflated and leadership_score == 0 and len(achievements) == 0:
        hard.append(
            f"R3: Title inflation suspected — '{inflated[0]}' with no team-ownership or achievement signals. "
            "Probe org scope in phone screen."
        )

    # ── R4: Buzzword resume, no quantified impact ─────────────────────────
    _has_numbers = any(
        re.search(r"\d+", str(p.get("description") or "")) for p in project_types
    )
    if project_types and not _has_numbers and len(achievements) == 0 and cert_count == 0:
        hard.append(
            "R4: No quantified metrics, no awards, no certifications found. "
            "May be a responsibility-lister, not an achiever."
        )

    # ── R5: Severe overqualification ─────────────────────────────────────
    if jd_config:
        _yoe_max = _safe_float((jd_config.get("yoe_range") or {}).get("max"), 0.0)
        if _yoe_max > 0 and yoe > _yoe_max * 2:
            hard.append(
                f"R5: Significantly overqualified — {yoe:.0f} yrs vs JD max {_yoe_max:.0f} yrs. "
                "High risk of early exit or salary mismatch."
            )

    # ── R6: Cert farming (hard flag) ─────────────────────────────────────
    _depth_priors_rf = bert_priors.get("skill_depth_priors") or []
    _avg_bert_depth_rf = 0.0
    if _depth_priors_rf:
        _bs = [BERT_DEPTH_TO_SCORE.get(str(p.get("predicted_depth_label") or "").upper(), 0.0) for p in _depth_priors_rf]
        _avg_bert_depth_rf = sum(_bs) / len(_bs) if _bs else 0.0
    if cert_count >= 5 and len(strong_rows) == 0 and _avg_bert_depth_rf <= BERT_DEPTH_TO_SCORE.get("FOUNDATIONAL", 1.5):
        hard.append(
            f"R6: Cert farming — {cert_count} certifications but skill depth is FOUNDATIONAL or below "
            f"(BERT avg={_avg_bert_depth_rf:.2f}/5). May not translate to real capability."
        )

    # ── R7: BERT role family mismatch with JD ────────────────────────────
    if jd_config:
        _rf_prior = bert_priors.get("role_family_prior", {}) or {}
        _rf_conf = _safe_float(_rf_prior.get("confidence"), 0.0)
        _rf_label = str(_rf_prior.get("label") or "").upper()
        _jd_family = str((jd_config or {}).get("role_family") or "").upper()
        if _rf_label and _jd_family and _rf_conf >= 0.70 and _rf_label != _jd_family:
            hard.append(
                f"R7: BERT signals role family '{_rf_label}' (conf={_rf_conf:.2f}) "
                f"but JD targets '{_jd_family}'. Possible wrong-fit application."
            )

    # ── R8: No verifiable output in 5+ year career ───────────────────────
    if yoe >= 5 and len(project_types) == 0 and len(achievements) == 0 and cert_count == 0:
        hard.append(
            "R8: No verifiable deliverables (projects, awards, certifications) across a 5+ year career. "
            "Cannot assess impact evidence."
        )

    # ── R9: Frequent lateral moves (soft) ────────────────────────────────
    _cp_prior = bert_priors.get("career_progression_prior", {}) or {}
    _cp_label = str(_cp_prior.get("label") or "").upper()
    if _cp_label == "LATERAL":
        soft.append(
            "R9: BERT signals lateral-only career progression. "
            "Assess growth mindset and ambition in phone screen."
        )

    # ── R10: Job-hopping pattern (soft) ──────────────────────────────────
    _total_months_rf = sum(tenures) if tenures else 0
    _n_roles_rf = len(tenures)
    _hop_rate_rf = (
        round(_n_roles_rf / (_total_months_rf / 12.0), 1)
        if _total_months_rf > 0 and _n_roles_rf >= 2 else 0.0
    )
    _short_12 = sum(1 for m in tenures if 0 < m < 12)
    if _hop_rate_rf > 1.5 or _short_12 >= 2:
        soft.append(
            f"R10: Job-hopping pattern detected — hop rate {_hop_rate_rf:.1f} roles/yr, "
            f"{_short_12} stints <12m. Context matters (startups/layoffs) — verify before penalising."
        )

    # ── R11: Large education gap >12m to first job (soft) ────────────────
    _edu_gap = _safe_float(education.get("education_gap_months") or education.get("gap_to_first_job_months"), 0.0)
    if _edu_gap > 12:
        soft.append(
            f"R11: Long gap ({_edu_gap:.0f}m) from education end to first job. "
            "May indicate difficulty entering workforce — verify context."
        )

    # ── R12: Overloaded skills list >30 (soft) ───────────────────────────
    _all_skills: list = (
        experience.get("skills") or experience.get("skill_names") or
        [r.get("skill") for r in skill_rows if r.get("skill")]
    )
    if len(_all_skills) > 30:
        soft.append(
            f"R12: Inflated skills list ({len(_all_skills)} skills listed). "
            "Depth likely shallow — probe top 5 skills in technical screen."
        )

    # ── R13: No progression in 5+ years at same company (soft) ───────────
    _unique_companies = list(dict.fromkeys(str(c) for c in companies))
    if len(_unique_companies) == 1 and yoe >= 5:
        _title_set = set(str(t).lower() for t in titles_raw)
        if len(_title_set) <= 2:
            soft.append(
                f"R13: Stagnation risk — {yoe:.0f} yrs at '{_unique_companies[0]}' "
                f"with only {len(_title_set)} distinct title(s). Assess internal scope and ambition."
            )

    # ── R14: Domain switch without bridge skills (soft) ──────────────────
    _edu_rel = str(education.get("education_job_relevance") or "").upper()
    _strong_ratio = len(strong_rows) / max(len(skill_rows), 1) if skill_rows else 0
    if _edu_rel in ("FOUNDATIONAL", "NONE") and _strong_ratio < 0.3:
        soft.append(
            "R14: Domain switch detected but bridge skills are weak (< 30% of skills are APPLIED or stronger). "
            "Higher onboarding risk — assess depth in technical screen."
        )

    # ── R15: COVID / recession gap context note (soft) ───────────────────
    for _br in (_bi.get("breaks") or []) + (_bi.get("possible_parental_breaks") or []):
        _gs = str(_br.get("gap_start") or "")
        if _gs[:4] in ("2020", "2021", "2022"):
            soft.append(
                f"R15: Career gap ({_br['gap_months']}m from {_gs}) aligns with COVID / industry layoff wave. "
                "Do not penalise automatically — verify context with candidate."
            )
            break

    # ── R16: Founder-only history, never an employee (soft) ──────────────
    _founder_kws_rf = ("founder", "co-founder", "cofounder", "ceo", "chief executive")
    if len(titles_raw) >= 2 and all(
        any(kw in str(t).lower() for kw in _founder_kws_rf) for t in titles_raw
    ):
        soft.append(
            "R16: All roles are Founder/CEO — candidate has never been a team contributor. "
            "Possible cultural fit risk for IC or team-member roles — verify."
        )

    # ── R17: Elite college, mediocre career (soft) ───────────────────────
    if edu_tier_str == "TIER_1" and best_tier_rf >= 4 and yoe >= 5:
        soft.append(
            f"R17: High-pedigree grad (TIER_1 institution) but career has not reached brand-name companies "
            f"(best tier = {best_tier_rf}). Could be lifestyle choice or performance — investigate."
        )

    # ── R18: Very recently acquired skills only (soft) ───────────────────
    _recent_only = [
        r for r in strong_rows
        if _safe_float(r.get("years_of_usage"), 0.0) < 1.5
    ]
    if len(_recent_only) >= 3 and len(strong_rows) > 0 and len(_recent_only) / len(strong_rows) > 0.7:
        soft.append(
            "R18: Most applied skills appear recently acquired (<1.5 yrs experience each). "
            "Depth may be shallow — probe in technical screen."
        )

    # ── R19: No coding community / OSS signals (soft — tech roles only) ──
    _oss_count = sum(1 for r in skill_rows if r.get("open_source_signal"))
    if _oss_count == 0 and skill_rows:
        _rf_label_comm = str((bert_priors.get("role_family_prior") or {}).get("label") or "").upper()
        _tech_families = {
            "DATA_ENGINEER", "ML_ENGINEER", "SOFTWARE_ENGINEER",
            "BACKEND", "FULLSTACK", "PLATFORM_INFRA",
        }
        if _rf_label_comm in _tech_families:
            soft.append(
                "R19: No coding community / open-source signals detected for a technical role. "
                "Lower signal for research or platform-engineering positions."
            )

    # ── R20: Templated / copy-paste project descriptions (soft) ──────────
    if len(project_types) >= 2:
        from difflib import SequenceMatcher as _SM
        _d1 = str(project_types[0].get("description") or "").strip()
        _d2 = str(project_types[1].get("description") or "").strip()
        if len(_d1) > 50 and len(_d2) > 50:
            _sim = _SM(None, _d1.lower(), _d2.lower()).ratio()
            if _sim > 0.65:
                soft.append(
                    f"R20: Project descriptions appear templated (similarity {_sim:.0%}). "
                    "Probe ownership and individual contribution in phone screen."
                )

    return {"hard": hard, "soft": soft}


# ---------------------------------------------------------------------------
# Stage update — recruiter / panel
# ---------------------------------------------------------------------------

def apply_stage_update(
    base_rubric: dict[str, Any],
    stage_name: str,
    stage_overrides: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    """Merge recruiter or panel score overrides into an existing rubric result.

    stage_overrides: flat dict of param_name → new_score.
    Params can live in experience, skills, or education breakdown sections.
    Returns an updated rubric dict with recomputed section totals.
    """
    updated = copy.deepcopy(base_rubric)
    params_updated: list[str] = []

    bd = updated.get("breakdown") or {}
    exp_bd = bd.get("experience") or {}
    skills_bd = bd.get("skills") or {}
    edu_bd = bd.get("education") or {}

    edu_bonus_bd = edu_bd.get("bonus") or {}
    section_lookup = {
        **{k: ("experience", exp_bd) for k in exp_bd},
        **{k: ("skills", skills_bd) for k in skills_bd},
        **{k: ("education", edu_bd) for k in edu_bd if k != "bonus"},
        **{k: ("education_bonus", edu_bonus_bd) for k in edu_bonus_bd},
    }

    for param, new_score in stage_overrides.items():
        if param not in section_lookup:
            continue
        section_name, section_bd = section_lookup[param]
        entry = section_bd.get(param)
        if isinstance(entry, dict):
            if entry.get("type") == "panel_text":
                entry["value"] = str(new_score)
            else:
                entry["score"] = float(new_score)
                entry["reason"] = entry.get("reason", "") + f" [Updated at {stage_name} stage: {new_score}]"
        params_updated.append(param)

    # Recompute section totals
    exp_total = sum(
        v["score"] for k, v in exp_bd.items()
        if isinstance(v, dict) and "score" in v and k != "operating_model_tag"
    )
    skills_total = sum(
        v["score"] for k, v in skills_bd.items()
        if isinstance(v, dict) and "score" in v
    )
    edu_core = sum(
        v["score"] for k, v in edu_bd.items()
        if isinstance(v, dict) and "score" in v and k != "bonus"
    )
    edu_bonus = sum(
        v["score"] for v in (edu_bd.get("bonus") or {}).values()
        if isinstance(v, dict) and "score" in v
    )
    edu_total = edu_core + edu_bonus

    updated["experience_score"] = round(_clamp(exp_total, 0, 40), 1)
    updated["skills_score"] = round(_clamp(skills_total, 0, 45), 1)
    updated["education_score"] = round(_clamp(edu_total, 0, 15), 1)
    updated["total_score"] = round(
        _clamp(updated["experience_score"] + updated["skills_score"] + updated["education_score"], 0, 100), 1
    )
    updated["params_updated"] = params_updated
    updated["stage"] = stage_name

    # Compute per-stage normalized score out of 100
    ss = updated.get("stage_scores") or {}
    resume_max  = ss.get("resume_max", 76)
    rec_max     = ss.get("recruiter_max", resume_max + ss.get("recruiter_can_add", 11))
    new_total   = updated["total_score"]
    if stage_name == "recruiter":
        stage_score_100 = round(_clamp((new_total / rec_max * 100) if rec_max > 0 else 0.0, 0.0, 100.0), 1)
        updated.setdefault("stage_scores", {})["recruiter_score_100"] = stage_score_100
    elif stage_name == "panel":
        stage_score_100 = new_total  # panel max = 100
        updated.setdefault("stage_scores", {})["panel_score_100"] = stage_score_100
    else:
        stage_score_100 = round(_clamp((new_total / resume_max * 100) if resume_max > 0 else 0.0, 0.0, 100.0), 1)
        updated.setdefault("stage_scores", {})["resume_score_100"] = stage_score_100
    updated["stage_score_100"] = stage_score_100

    return updated
