"""Stack Overflow / Stack Exchange API candidate sourcing.

Uses the free Stack Exchange API (300 req/day unauthenticated, 10,000/day with a key).
Set STACKOVERFLOW_API_KEY env var for higher rate limits.

Strategy:
  1. Map requested skills → SO tag names.
  2. Fetch top-answerers per tag  (GET /tags/{tag}/top-answerers/all_time).
  3. Batch-fetch full user profiles.
  4. Enrich each user's top tags (parallel).
  5. Score + normalize into the Tvarah sourcing profile shape.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

logger = logging.getLogger("resume_intelligence.sourcing.stackoverflow_sourcer")

_SO_API = "https://api.stackexchange.com/2.3"
_SITE = "stackoverflow"
_PARALLEL = 5

# ---------------------------------------------------------------------------
# Skill → SO tag mapping
# ---------------------------------------------------------------------------
_SKILL_TO_TAG: dict[str, str] = {
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "c++": "c++",
    "c#": "c#",
    "ruby": "ruby",
    "swift": "swift",
    "kotlin": "kotlin",
    "scala": "scala",
    "php": "php",
    "r": "r",
    "sql": "sql",
    "machine learning": "machine-learning",
    "ml": "machine-learning",
    "deep learning": "deep-learning",
    "nlp": "nlp",
    "data science": "data-science",
    "tensorflow": "tensorflow",
    "pytorch": "pytorch",
    "pandas": "pandas",
    "numpy": "numpy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "spark": "apache-spark",
    "kafka": "apache-kafka",
    "react": "reactjs",
    "vue": "vue.js",
    "angular": "angular",
    "node.js": "node.js",
    "nodejs": "node.js",
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "spring": "spring",
    "docker": "docker",
    "kubernetes": "kubernetes",
    "aws": "amazon-web-services",
    "gcp": "google-cloud-platform",
    "azure": "azure",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mongodb": "mongodb",
    "redis": "redis",
    "elasticsearch": "elasticsearch",
    "llm": "large-language-model",
    "bert": "bert-language-model",
    "transformers": "huggingface-transformers",
    "android": "android",
    "ios": "ios",
    "unity": "unity3d",
    "linux": "linux",
    "bash": "bash",
    "git": "git",
}


def _map_skills_to_tags(skills: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        s = skill.lower().strip()
        tag = _SKILL_TO_TAG.get(s) or re.sub(r"[^a-z0-9.+#]+", "-", s).strip("-")
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags[:5]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _params(**extra) -> dict:
    p = {"site": _SITE}
    key = os.getenv("STACKOVERFLOW_API_KEY", "")
    if key:
        p["key"] = key
    p.update(extra)
    return p


def _get(path: str, **params) -> dict | None:
    url = f"{_SO_API}{path}"
    try:
        resp = requests.get(url, params=_params(**params), timeout=15)
        if resp.status_code == 400:
            # Often means tag doesn't exist
            return None
        if resp.status_code == 429:
            logger.warning("SO rate limit — backing off")
            time.sleep(1)
            return None
        if resp.status_code != 200:
            logger.debug("SO %s → %d", path, resp.status_code)
            return None
        data = resp.json()
        remaining = data.get("quota_remaining")
        if remaining is not None and remaining < 20:
            logger.warning("SO quota low: %d remaining", remaining)
        return data
    except Exception as exc:
        logger.debug("SO request error %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_top_answerers(tag: str) -> list[tuple[dict, int]]:
    """Return list of (user_stub, score) for top answerers of a tag."""
    data = _get(f"/tags/{tag}/top-answerers/all_time", pagesize=30)
    if not data:
        return []
    results = []
    for item in data.get("items") or []:
        u = item.get("user") or {}
        uid = u.get("user_id")
        score = item.get("score") or 0
        if uid:
            results.append((u, score))
    return results


def _fetch_users_batch(user_ids: list[int]) -> list[dict]:
    if not user_ids:
        return []
    id_str = ";".join(str(i) for i in user_ids[:100])
    data = _get(f"/users/{id_str}", order="desc", sort="reputation", pagesize=100)
    if not data:
        return []
    return data.get("items") or []


def _fetch_user_tags(user_id: int) -> list[str]:
    data = _get(f"/users/{user_id}/tags", order="desc", sort="popular", pagesize=15)
    if not data:
        return []
    return [item.get("name", "") for item in (data.get("items") or []) if item.get("name")]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _account_age_years(creation_ts: int) -> float:
    if not creation_ts:
        return 0.0
    dt = datetime.fromtimestamp(creation_ts, tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days / 365.25


def _skill_match(top_tags: list[str], criteria: dict) -> tuple[int, list[str], list[str]]:
    required_raw = [s.lower().strip() for s in (criteria.get("skills") or [])]
    # Also include the mapped SO tags themselves as acceptable matches
    required_tags = _map_skills_to_tags(required_raw)
    all_required = list(dict.fromkeys(required_raw + required_tags))
    if not all_required:
        return 20, [], []

    tag_norm = {t.replace("-", "").replace(".", ""): t for t in top_tags}
    matched, missing = [], []
    for skill in required_raw or required_tags:
        sk = skill.replace("-", "").replace(".", "").replace(" ", "")
        found = (
            sk in tag_norm
            or any(sk in t for t in tag_norm if len(t) >= len(sk) >= 3)
        )
        (matched if found else missing).append(skill)

    ratio = len(matched) / max(len(required_raw or required_tags), 1)
    return min(40, int(ratio * 40)), matched, missing


def _activity_score(user: dict) -> int:
    rep = user.get("reputation") or 0
    answers = user.get("answer_count") or 0
    badges = user.get("badge_counts") or {}
    gold = badges.get("gold") or 0
    rep_pts = min(12, int(math.log1p(rep) * 1.2))
    ans_pts = min(8, int(math.log1p(answers) * 1.8))
    gold_pts = min(5, gold)
    return min(25, rep_pts + ans_pts + gold_pts)


def _seniority_score(user: dict, criteria: dict) -> int:
    age = _account_age_years(user.get("creation_date") or 0)
    rep = user.get("reputation") or 0
    requested = criteria.get("seniority", "MID")
    age_pts = 10 if age >= 7 else (8 if age >= 5 else (6 if age >= 3 else (3 if age >= 1 else 1)))
    rep_tier = "JUNIOR" if rep < 1000 else ("SENIOR" if rep >= 10000 else "MID")
    lvl_map = {"JUNIOR": 0, "MID": 1, "SENIOR": 2}
    diff = abs(lvl_map.get(rep_tier, 1) - lvl_map.get(requested, 1))
    tier_pts = 6 if diff == 0 else (3 if diff == 1 else 0)
    complexity_pts = min(4, int(math.log1p(max(rep / 1000, 0))))
    return min(20, age_pts + tier_pts + complexity_pts)


def _completeness_score(user: dict) -> int:
    pts = 0
    if user.get("location"):
        pts += 4
    if user.get("website_url"):
        pts += 4
    if (user.get("answer_count") or 0) > 0:
        pts += 4
    if user.get("display_name"):
        pts += 3
    return min(15, pts)


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def _normalize(user: dict, top_tags: list[str], criteria: dict, tag_score: int = 0) -> dict:
    user_id = user.get("user_id") or user.get("account_id", 0)
    profile_id = f"so_{user_id}"

    skill_pts, matched, missing = _skill_match(top_tags, criteria)
    # Boost skill score if they're a highly-scored answerer in a relevant tag
    if tag_score:
        skill_pts = min(40, skill_pts + min(6, int(math.log1p(tag_score) * 0.7)))

    activity_pts = _activity_score(user)
    seniority_pts = _seniority_score(user, criteria)
    completeness_pts = _completeness_score(user)
    total = skill_pts + activity_pts + seniority_pts + completeness_pts

    rep = user.get("reputation") or 0
    rep_label = (
        "Grandmaster" if rep >= 50000 else
        "Expert" if rep >= 10000 else
        "Active" if rep >= 1000 else
        "Contributor"
    )
    badges = user.get("badge_counts") or {}
    bio = (
        f"Stack Overflow {rep_label} — {rep:,} reputation, "
        f"{user.get('answer_count', 0)} answers, "
        f"{badges.get('gold', 0)}g/{badges.get('silver', 0)}s/{badges.get('bronze', 0)}b badges"
    )
    created_iso = (
        datetime.fromtimestamp(user["creation_date"], tz=timezone.utc).isoformat()
        if user.get("creation_date") else ""
    )

    return {
        "candidate_id": profile_id,
        "source": "stackoverflow",
        "github_username": profile_id,  # universal profile_id key used by store
        "display_name": user.get("display_name") or profile_id,
        "location": user.get("location") or "",
        "company": "",
        "bio": bio,
        "blog": user.get("website_url") or "",
        "tech_stack": top_tags[:10],
        "repo_topics": top_tags,
        "top_repos": [],
        "yoe_proxy": round(max(0.0, _account_age_years(user.get("creation_date") or 0) - 1.0), 1),
        "sourcing_score": total,
        "score_breakdown": {
            "skill_match": skill_pts,
            "activity": activity_pts,
            "seniority": seniority_pts,
            "completeness": completeness_pts,
        },
        "matched_skills": [s.title() for s in matched],
        "missing_skills": [s.title() for s in missing],
        "profile_url": user.get("link") or f"https://stackoverflow.com/users/{user_id}",
        "github_url": user.get("link") or f"https://stackoverflow.com/users/{user_id}",
        "avatar_url": user.get("profile_image") or "",
        "email": None,
        "followers": rep,
        "public_repos": user.get("answer_count") or 0,
        "total_stars": (badges.get("gold") or 0) * 10,
        "created_at": created_iso,
        "so_reputation": rep,
        "so_answers": user.get("answer_count") or 0,
        "so_badges": badges,
        "so_top_tags": top_tags,
        "pipeline_status": "sourced",
        "resume_pdf_url": "",
        "resume_status": "not_applicable",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def source_candidates(criteria: dict, count: int = 20) -> list[dict]:
    """Search Stack Overflow and return normalized Tvarah candidate profiles."""
    skills = criteria.get("skills") or []
    tags = _map_skills_to_tags(skills) or ["python"]

    logger.info("SO sourcing: skills=%s → tags=%s count=%d", skills, tags[:3], count)

    # Step 1: Collect top-answerers across relevant tags
    user_score: dict[int, int] = {}   # user_id → cumulative tag score
    user_tags: dict[int, list[str]] = {}   # user_id → tags they're known for

    for tag in tags[:3]:
        answerers = _fetch_top_answerers(tag)
        for user_stub, score in answerers[:30]:
            uid = user_stub.get("user_id")
            if not uid:
                continue
            if uid not in user_score:
                user_score[uid] = 0
                user_tags[uid] = []
            user_score[uid] += score
            user_tags[uid].append(tag)

    if not user_score:
        logger.warning("SO: no users found for tags %s", tags)
        return []

    # Step 2: Rank by score, take top 2×count for filtering headroom
    top_ids = sorted(user_score, key=lambda u: user_score[u], reverse=True)[: min(count * 2, 60)]

    # Step 3: Batch-fetch full profiles
    users_raw = _fetch_users_batch(top_ids)
    user_by_id = {u.get("user_id"): u for u in users_raw if u.get("user_id")}

    # Step 4: Enrich with per-user top tags (parallel)
    enriched: dict[int, list[str]] = {}
    with ThreadPoolExecutor(max_workers=_PARALLEL) as ex:
        futs = {ex.submit(_fetch_user_tags, uid): uid for uid in top_ids[:count]}
        for fut in as_completed(futs):
            uid = futs[fut]
            try:
                enriched[uid] = fut.result()
            except Exception:
                enriched[uid] = user_tags.get(uid, [])

    # Step 5: Normalize and return
    candidates: list[dict] = []
    for uid in top_ids:
        user = user_by_id.get(uid)
        if not user:
            continue
        top_tags = enriched.get(uid) or user_tags.get(uid, [])
        candidates.append(_normalize(user, top_tags, criteria, user_score.get(uid, 0)))

    candidates.sort(key=lambda c: c["sourcing_score"], reverse=True)
    logger.info("SO: returning %d candidates", min(len(candidates), count))
    return candidates[:count]
