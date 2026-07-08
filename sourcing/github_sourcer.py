"""GitHub API sourcing — parallel fetch with README, topics, and resume discovery."""
from __future__ import annotations

import base64
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

logger = logging.getLogger("resume_intelligence.sourcing.github_sourcer")

_GITHUB_API = "https://api.github.com"
_RAW_BASE = "https://raw.githubusercontent.com"
_PARALLEL_WORKERS = 5

# Repo names that likely contain a resume/CV PDF
_RESUME_REPO_NAMES = {"resume", "cv", "curriculum-vitae", "portfolio", "my-resume", "my-cv"}
_RESUME_FILE_EXTS = {".pdf"}


def _headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, params: dict | None = None, timeout: int = 15) -> dict | list | None:
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        if resp.status_code == 403:
            reset = resp.headers.get("X-RateLimit-Reset", "?")
            logger.warning("GitHub rate limit — reset at %s", reset)
            return None
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("GitHub API error %s: %s", url, exc)
        return None


def _get_bytes(url: str, timeout: int = 20) -> bytes | None:
    try:
        resp = requests.get(url, headers=_headers(), timeout=timeout)
        if resp.status_code == 200:
            return resp.content
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Profile README
# ---------------------------------------------------------------------------

def _fetch_profile_readme(username: str) -> str:
    """Fetch the user's profile README (username/username repo). Returns plain text."""
    data = _get(f"{_GITHUB_API}/repos/{username}/{username}/readme")
    if not data or not isinstance(data, dict):
        return ""
    encoded = data.get("content", "")
    if not encoded:
        return ""
    try:
        text = base64.b64decode(encoded.replace("\n", "")).decode("utf-8", errors="replace")
        return text[:3000]  # cap at 3 KB — enough for skill matching
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Resume PDF discovery
# ---------------------------------------------------------------------------

def _find_resume_url_in_repo(username: str, repo_name: str) -> str | None:
    """Scan a repo's root for PDF files (resume/cv)."""
    # Try main, master, HEAD
    for branch in ("main", "master"):
        tree = _get(
            f"{_GITHUB_API}/repos/{username}/{repo_name}/git/trees/{branch}",
            params={"recursive": "0"},
        )
        if tree and isinstance(tree, dict):
            for item in tree.get("tree") or []:
                path = item.get("path", "")
                if path.lower().endswith(".pdf"):
                    # Build raw URL
                    return f"{_RAW_BASE}/{username}/{repo_name}/{branch}/{path}"
    return None


def _find_resume_pdf_url(username: str, repos: list[dict]) -> str | None:
    """Return the first resume/CV PDF URL found across repos."""
    for r in repos:
        name = (r.get("name") or "").lower()
        if name in _RESUME_REPO_NAMES or any(kw in name for kw in ("resume", " cv", "portfolio")):
            url = _find_resume_url_in_repo(username, r.get("name", ""))
            if url:
                logger.info("Resume PDF found for %s: %s", username, url)
                return url
    return None


def _blog_is_pdf(blog: str) -> str | None:
    """If the blog URL directly ends in .pdf, return it."""
    if blog and blog.lower().strip().endswith(".pdf"):
        return blog.strip()
    return None


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def _enrich_user(username: str) -> dict | None:
    """Fetch full profile, repos, README, topics in parallel sub-requests."""

    # --- Fetch user profile and repos concurrently ---
    def _fetch_user():
        return _get(f"{_GITHUB_API}/users/{username}")

    def _fetch_repos():
        return _get(
            f"{_GITHUB_API}/users/{username}/repos",
            params={"sort": "updated", "per_page": 30, "type": "owner"},
        )

    def _fetch_readme():
        return _fetch_profile_readme(username)

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_user = ex.submit(_fetch_user)
        fut_repos = ex.submit(_fetch_repos)
        fut_readme = ex.submit(_fetch_readme)
        results["user"] = fut_user.result()
        results["repos_raw"] = fut_repos.result()
        results["readme"] = fut_readme.result()

    user = results["user"]
    if not user or not isinstance(user, dict):
        return None

    repos_raw = results["repos_raw"] or []
    readme_text = results["readme"] or ""

    repos: list[dict] = []
    lang_counts: dict[str, int] = {}
    topic_set: set[str] = set()
    total_stars = 0

    for r in (repos_raw if isinstance(repos_raw, list) else []):
        if r.get("fork"):
            continue
        stars = r.get("stargazers_count") or 0
        total_stars += stars
        lang = r.get("language") or ""
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        for topic in r.get("topics") or []:
            topic_set.add(topic.lower())
        repos.append({
            "name": r.get("name", ""),
            "stars": stars,
            "description": (r.get("description") or "")[:140],
            "language": lang,
            "url": r.get("html_url", ""),
            "topics": r.get("topics") or [],
            "updated_at": r.get("updated_at") or "",
        })

    # Sort by stars descending for display
    repos.sort(key=lambda x: x["stars"], reverse=True)
    primary_langs = sorted(lang_counts, key=lambda l: lang_counts[l], reverse=True)[:6]

    # Resume PDF discovery (only needs repos list)
    blog = (user.get("blog") or "").strip()
    resume_pdf_url = _blog_is_pdf(blog) or _find_resume_pdf_url(username, repos)

    return {
        "username": username,
        "display_name": user.get("name") or username,
        "bio": (user.get("bio") or "")[:250],
        "location": user.get("location") or "",
        "company": (user.get("company") or "").strip("@"),
        "email": user.get("email") or "",
        "blog": blog,
        "public_repos": user.get("public_repos") or 0,
        "followers": user.get("followers") or 0,
        "following": user.get("following") or 0,
        "avatar_url": user.get("avatar_url") or "",
        "github_url": user.get("html_url") or f"https://github.com/{username}",
        "created_at": user.get("created_at") or "",
        "primary_languages": primary_langs,
        "repo_topics": sorted(topic_set)[:20],
        "top_repos": repos[:8],
        "total_stars": total_stars,
        "readme_text": readme_text,
        "resume_pdf_url": resume_pdf_url,
        "lang_distribution": lang_counts,
    }


# ---------------------------------------------------------------------------
# Search query builder
# ---------------------------------------------------------------------------

def _build_search_query(criteria: dict) -> str:
    parts: list[str] = []

    langs = criteria.get("github_languages") or []
    if langs:
        parts.append(f"language:{langs[0]}")

    location = criteria.get("location") or ""
    if location:
        parts.append(f"location:{location}")

    seniority = criteria.get("seniority", "MID")
    if seniority == "SENIOR":
        parts.append("followers:>20")
    elif seniority == "MID":
        parts.append("followers:>5")
    else:
        parts.append("repos:>1")

    parts.append("type:user")
    return " ".join(parts) if parts else "type:user"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def source_candidates(criteria: dict, count: int = 20) -> list[dict]:
    """Search GitHub and return enriched profiles (parallel)."""
    q = _build_search_query(criteria)
    per_page = min(count, 30)

    logger.info("GitHub search: %r  count=%d", q, count)

    search_result = _get(
        f"{_GITHUB_API}/search/users",
        params={"q": q, "per_page": per_page, "sort": "followers", "order": "desc"},
    )

    if not search_result or not isinstance(search_result, dict):
        logger.warning("GitHub search returned nothing")
        return []

    items = search_result.get("items") or []
    logger.info("GitHub search → %d users", len(items))

    usernames = [item.get("login", "") for item in items[:count] if item.get("login")]

    profiles: list[dict] = []
    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as executor:
        future_to_user = {executor.submit(_enrich_user, u): u for u in usernames}
        for future in as_completed(future_to_user):
            try:
                result = future.result()
                if result:
                    profiles.append(result)
            except Exception as exc:
                logger.warning("Enrichment failed for %s: %s", future_to_user[future], exc)

    # Re-sort by followers (parallel completion is unordered)
    profiles.sort(key=lambda p: p.get("followers", 0), reverse=True)
    return profiles[:count]


def download_resume_pdf(url: str) -> bytes | None:
    """Download a resume PDF from a GitHub raw URL."""
    logger.info("Downloading resume PDF: %s", url)
    return _get_bytes(url, timeout=30)
