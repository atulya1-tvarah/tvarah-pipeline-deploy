"""GitLab API candidate sourcing.

Uses the public GitLab REST API v4.
Works unauthenticated for public data; set GITLAB_TOKEN for higher rate limits.

Strategy:
  1. Search public projects by topic matching requested skills.
  2. Collect unique user-owned project namespaces.
  3. Enrich each user: profile + project list in parallel.
  4. Score + normalize into the Tvarah sourcing profile shape
     (mirrors the GitHub sourcer closely).
"""
from __future__ import annotations

import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

logger = logging.getLogger("resume_intelligence.sourcing.gitlab_sourcer")

_GL_API = "https://gitlab.com/api/v4"
_PARALLEL = 5

# ---------------------------------------------------------------------------
# Skill → GitLab topic mapping
# ---------------------------------------------------------------------------
_SKILL_TO_TOPIC: dict[str, str] = {
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "c++": "cpp",
    "c#": "csharp",
    "ruby": "ruby",
    "swift": "swift",
    "kotlin": "kotlin",
    "scala": "scala",
    "php": "php",
    "r": "r",
    "machine learning": "machine-learning",
    "ml": "machine-learning",
    "deep learning": "deep-learning",
    "nlp": "nlp",
    "data science": "data-science",
    "tensorflow": "tensorflow",
    "pytorch": "pytorch",
    "react": "react",
    "vue": "vue",
    "angular": "angular",
    "node.js": "nodejs",
    "nodejs": "nodejs",
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "spring": "spring",
    "docker": "docker",
    "kubernetes": "kubernetes",
    "devops": "devops",
    "aws": "aws",
    "terraform": "terraform",
    "ansible": "ansible",
    "postgresql": "postgresql",
    "mongodb": "mongodb",
    "redis": "redis",
    "elasticsearch": "elasticsearch",
    "linux": "linux",
    "bash": "bash",
    "llm": "llm",
    "ai": "artificial-intelligence",
    "android": "android",
    "ios": "ios",
    "unity": "unity",
}


def _map_skills_to_topics(skills: list[str]) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        s = skill.lower().strip()
        topic = _SKILL_TO_TOPIC.get(s) or re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        if topic and topic not in seen:
            seen.add(topic)
            topics.append(topic)
    return topics[:4]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    token = os.getenv("GITLAB_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(path: str, params: dict | None = None) -> dict | list | None:
    url = f"{_GL_API}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params or {}, timeout=15)
        if resp.status_code == 429:
            logger.warning("GitLab rate limit hit")
            return None
        if resp.status_code in (401, 403, 404):
            return None
        if resp.status_code != 200:
            logger.debug("GL %s → %d", path, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.debug("GitLab API error %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _search_projects_by_topic(topic: str, per_page: int = 30) -> list[dict]:
    """Search public projects by topic tag."""
    data = _get("/projects", {
        "topic": topic,
        "order_by": "star_count",
        "sort": "desc",
        "visibility": "public",
        "per_page": per_page,
        "page": 1,
    })
    return data if isinstance(data, list) else []


def _search_projects_by_keyword(keyword: str, per_page: int = 20) -> list[dict]:
    """Fallback: full-text search on project names/descriptions."""
    data = _get("/projects", {
        "search": keyword,
        "order_by": "star_count",
        "sort": "desc",
        "visibility": "public",
        "per_page": per_page,
        "page": 1,
    })
    return data if isinstance(data, list) else []


def _fetch_user_profile(user_id: int) -> dict | None:
    data = _get(f"/users/{user_id}")
    return data if isinstance(data, dict) else None


def _fetch_user_projects(user_id: int, per_page: int = 20) -> list[dict]:
    data = _get(f"/users/{user_id}/projects", {
        "order_by": "star_count",
        "sort": "desc",
        "visibility": "public",
        "per_page": per_page,
        "page": 1,
    })
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _account_age_years(created_at: str) -> float:
    if not created_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days / 365.25
    except Exception:
        return 0.0


def _skill_match(profile_signals: set[str], criteria: dict) -> tuple[int, list[str], list[str]]:
    required = [s.lower().strip() for s in (criteria.get("skills") or [])]
    if not required:
        return 20, [], []
    sig_norm = {s.replace("-", "").replace("_", "").replace(".", "") for s in profile_signals}
    matched, missing = [], []
    for skill in required:
        sk = skill.replace("-", "").replace("_", "").replace(".", "").replace(" ", "")
        found = (
            sk in sig_norm
            or any(sk in s for s in sig_norm if len(s) >= len(sk) >= 3)
        )
        (matched if found else missing).append(skill)
    ratio = len(matched) / max(len(required), 1)
    return min(40, int(ratio * 40)), matched, missing


def _activity_score(user: dict, projects: list[dict]) -> int:
    total_stars = sum(p.get("star_count") or 0 for p in projects)
    total_forks = sum(p.get("forks_count") or 0 for p in projects)
    num_projects = len(projects)
    star_pts = min(10, int(math.log1p(total_stars) * 1.5))
    fork_pts = min(8, int(math.log1p(total_forks) * 1.8))
    repo_pts = min(7, int(math.log1p(num_projects)))
    return min(25, star_pts + fork_pts + repo_pts)


def _seniority_score(user: dict, projects: list[dict], criteria: dict) -> int:
    age = _account_age_years(user.get("created_at") or "")
    requested = criteria.get("seniority", "MID")
    age_pts = 10 if age >= 7 else (8 if age >= 5 else (6 if age >= 3 else (3 if age >= 1 else 1)))

    total_stars = sum(p.get("star_count") or 0 for p in projects)
    avg_stars = total_stars / len(projects) if projects else 0
    complexity_pts = min(6, int(math.log1p(avg_stars)))

    inferred = "JUNIOR" if age < 2 else ("SENIOR" if age >= 5 else "MID")
    lvl_map = {"JUNIOR": 0, "MID": 1, "SENIOR": 2}
    diff = abs(lvl_map.get(inferred, 1) - lvl_map.get(requested, 1))
    seniority_pts = 4 if diff == 0 else (2 if diff == 1 else 0)

    return min(20, age_pts + complexity_pts + seniority_pts)


def _completeness_score(user: dict) -> int:
    pts = 0
    if user.get("bio"):
        pts += 4
    if user.get("location"):
        pts += 4
    if user.get("website_url"):
        pts += 3
    if user.get("organization"):
        pts += 2
    if user.get("name"):
        pts += 2
    return min(15, pts)


def _build_skill_signals(user: dict, projects: list[dict]) -> set[str]:
    signals: set[str] = set()
    bio = (user.get("bio") or "").lower()
    for token in re.findall(r"[a-z][a-z0-9_\-.+#]{1,20}", bio):
        signals.add(token)
    for p in projects:
        lang = (p.get("programming_language") or "").lower()
        if lang:
            signals.add(lang)
        for topic in p.get("topics") or []:
            signals.add(topic.lower())
        desc = (p.get("description") or "").lower()
        for token in re.findall(r"[a-z][a-z0-9_\-.+#]{1,20}", desc):
            signals.add(token)
    return signals


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def _normalize(user: dict, projects: list[dict], criteria: dict) -> dict:
    user_id = user.get("id", 0)
    username = user.get("username") or str(user_id)
    profile_id = f"gl_{username}"

    signals = _build_skill_signals(user, projects)
    skill_pts, matched, missing = _skill_match(signals, criteria)
    activity_pts = _activity_score(user, projects)
    seniority_pts = _seniority_score(user, projects, criteria)
    completeness_pts = _completeness_score(user)
    total = skill_pts + activity_pts + seniority_pts + completeness_pts

    total_stars = sum(p.get("star_count") or 0 for p in projects)
    langs: list[str] = []
    lang_seen: set[str] = set()
    topics_all: set[str] = set()
    for p in projects:
        lang = p.get("programming_language") or ""
        if lang and lang not in lang_seen:
            lang_seen.add(lang)
            langs.append(lang)
        for t in p.get("topics") or []:
            topics_all.add(t.lower())

    top_repos = []
    for p in sorted(projects, key=lambda x: x.get("star_count") or 0, reverse=True)[:8]:
        top_repos.append({
            "name": p.get("path") or p.get("name") or "",
            "description": (p.get("description") or "")[:140],
            "stars": p.get("star_count") or 0,
            "language": p.get("programming_language") or "",
            "topics": p.get("topics") or [],
            "url": p.get("web_url") or "",
        })

    profile_url = user.get("web_url") or f"https://gitlab.com/{username}"

    return {
        "candidate_id": profile_id,
        "source": "gitlab",
        "github_username": profile_id,
        "display_name": user.get("name") or username,
        "location": user.get("location") or "",
        "company": user.get("organization") or "",
        "bio": (user.get("bio") or "")[:250],
        "blog": user.get("website_url") or "",
        "tech_stack": langs[:8],
        "repo_topics": sorted(topics_all)[:20],
        "top_repos": top_repos,
        "yoe_proxy": round(max(0.0, _account_age_years(user.get("created_at") or "") - 1.0), 1),
        "sourcing_score": total,
        "score_breakdown": {
            "skill_match": skill_pts,
            "activity": activity_pts,
            "seniority": seniority_pts,
            "completeness": completeness_pts,
        },
        "matched_skills": [s.title() for s in matched],
        "missing_skills": [s.title() for s in missing],
        "profile_url": profile_url,
        "github_url": profile_url,
        "avatar_url": user.get("avatar_url") or "",
        "email": user.get("public_email") or None,
        "followers": 0,
        "public_repos": len(projects),
        "total_stars": total_stars,
        "created_at": user.get("created_at") or "",
        "pipeline_status": "sourced",
        "resume_pdf_url": "",
        "resume_status": "not_applicable",
    }


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def _enrich_user(user_id: int) -> tuple[dict | None, list[dict]]:
    """Fetch user profile + projects in parallel sub-requests."""
    def _get_profile():
        return _fetch_user_profile(user_id)

    def _get_projects():
        return _fetch_user_projects(user_id)

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_profile = ex.submit(_get_profile)
        fut_projects = ex.submit(_get_projects)
        user = fut_profile.result()
        projects = fut_projects.result()

    return user, projects


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def source_candidates(criteria: dict, count: int = 20) -> list[dict]:
    """Search GitLab and return normalized Tvarah candidate profiles."""
    skills = criteria.get("skills") or []
    topics = _map_skills_to_topics(skills) or ["python"]

    logger.info("GitLab sourcing: skills=%s → topics=%s count=%d", skills, topics[:3], count)

    # Step 1: Collect user IDs from projects matching relevant topics
    user_id_to_stars: dict[int, int] = {}
    user_id_to_username: dict[int, str] = {}

    for topic in topics[:3]:
        projects = _search_projects_by_topic(topic, per_page=30)
        if not projects:
            # Fallback to keyword search
            projects = _search_projects_by_keyword(topic, per_page=20)
        for p in projects:
            ns = p.get("namespace") or {}
            if ns.get("kind") != "user":
                continue
            uid = ns.get("id")
            if not uid:
                continue
            stars = p.get("star_count") or 0
            if uid not in user_id_to_stars:
                user_id_to_stars[uid] = 0
                user_id_to_username[uid] = ns.get("path") or ""
            user_id_to_stars[uid] += stars

    if not user_id_to_stars:
        logger.warning("GitLab: no users found for topics %s", topics)
        return []

    # Step 2: Rank by accumulated stars, take top 2×count
    ranked_ids = sorted(user_id_to_stars, key=lambda uid: user_id_to_stars[uid], reverse=True)
    top_ids = ranked_ids[:min(count * 2, 50)]

    # Step 3: Enrich users in parallel
    candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=_PARALLEL) as ex:
        futs = {ex.submit(_enrich_user, uid): uid for uid in top_ids}
        for fut in as_completed(futs):
            uid = futs[fut]
            try:
                user, projects = fut.result()
                if not user:
                    continue
                normalized = _normalize(user, projects, criteria)
                candidates.append(normalized)
            except Exception as exc:
                logger.debug("GitLab enrich failed for user %d: %s", uid, exc)

    candidates.sort(key=lambda c: c["sourcing_score"], reverse=True)
    logger.info("GitLab: returning %d candidates", min(len(candidates), count))
    return candidates[:count]
