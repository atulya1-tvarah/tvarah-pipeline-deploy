"""Kaggle API candidate sourcing.

Uses the Kaggle REST API (HTTP Basic Auth).
Set KAGGLE_USERNAME and KAGGLE_KEY env vars (same credentials as ~/.kaggle/kaggle.json).

Strategy:
  1. Search public notebooks/kernels by skill keywords.
  2. Aggregate per-author: total_votes, kernel_count, top_tags.
  3. Optionally enrich with Kaggle user profile (tier, bio, location).
  4. Score + normalize into the Tvarah sourcing profile shape.

Kaggle performance tiers (ascending): Novice → Contributor → Expert → Master → Grandmaster
"""
from __future__ import annotations

import logging
import math
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

logger = logging.getLogger("resume_intelligence.sourcing.kaggle_sourcer")

_KAGGLE_API = "https://www.kaggle.com/api/v1"
_PARALLEL = 5

_TIER_RANK = {
    "novice": 0,
    "contributor": 1,
    "expert": 2,
    "master": 3,
    "grandmaster": 4,
}
_TIER_LABEL = {0: "Novice", 1: "Contributor", 2: "Expert", 3: "Master", 4: "Grandmaster"}

# Map common skills to Kaggle search terms / tags
_SKILL_TO_KAGGLE_SEARCH: dict[str, str] = {
    "machine learning": "machine learning",
    "ml": "machine learning",
    "deep learning": "deep learning",
    "nlp": "natural language processing",
    "data science": "data science",
    "python": "python",
    "tensorflow": "tensorflow",
    "pytorch": "pytorch",
    "pandas": "pandas",
    "computer vision": "computer vision",
    "xgboost": "xgboost",
    "neural network": "neural network",
    "random forest": "random forest",
    "feature engineering": "feature engineering",
    "time series": "time series",
    "sql": "sql",
    "r": "r programming",
    "statistics": "statistics",
    "llm": "large language model",
    "transformers": "transformers",
    "eda": "exploratory data analysis",
    "data visualization": "data visualization",
    "classification": "classification",
    "regression": "regression",
    "clustering": "clustering",
    "reinforcement learning": "reinforcement learning",
    "generative ai": "generative ai",
    "bert": "bert",
}


def _search_term(skills: list[str]) -> str:
    for skill in skills:
        s = skill.lower().strip()
        term = _SKILL_TO_KAGGLE_SEARCH.get(s)
        if term:
            return term
    # Fallback: use first skill directly
    return skills[0] if skills else "machine learning"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _auth() -> tuple[str, str] | None:
    username = os.getenv("KAGGLE_USERNAME", "")
    key = os.getenv("KAGGLE_KEY", "")
    if not username or not key:
        return None
    return (username, key)


def _get(path: str, params: dict | None = None) -> dict | list | None:
    creds = _auth()
    if not creds:
        logger.warning("Kaggle credentials not set (KAGGLE_USERNAME / KAGGLE_KEY)")
        return None
    url = f"{_KAGGLE_API}{path}"
    try:
        resp = requests.get(url, auth=creds, params=params or {}, timeout=20)
        if resp.status_code == 401:
            logger.warning("Kaggle: invalid credentials")
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.debug("Kaggle %s → %d", path, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.debug("Kaggle API error %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _search_kernels(search: str, page_size: int = 50) -> list[dict]:
    """Search public notebooks. Returns raw kernel list items."""
    data = _get("/kernels/list", {
        "search": search,
        "language": "python",
        "sortBy": "voteCount",
        "pageSize": min(page_size, 100),
        "page": 1,
    })
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("kernels") or data.get("items") or []
    return []


def _search_datasets(search: str, page_size: int = 30) -> list[dict]:
    """Search public datasets to find additional authors."""
    data = _get("/datasets/list", {
        "search": search,
        "sortBy": "hotness",
        "fileType": "all",
        "pageSize": min(page_size, 50),
        "page": 1,
    })
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("datasets") or data.get("items") or []
    return []


def _fetch_user_profile(username: str) -> dict | None:
    """Try to fetch Kaggle user profile. Returns None if endpoint unavailable."""
    # Try documented-ish endpoint
    data = _get(f"/users/{username}/stats")
    if isinstance(data, dict) and data:
        return data
    # Some Kaggle API versions expose /users/{username}
    data = _get(f"/users/{username}")
    if isinstance(data, dict) and data:
        return data
    return None


def _get_author_kernels(username: str, page_size: int = 5) -> list[dict]:
    """Get a user's own kernels to assess quality."""
    data = _get("/kernels/list", {
        "authorSlug": username,
        "sortBy": "voteCount",
        "pageSize": page_size,
    })
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("kernels") or data.get("items") or []
    return []


# ---------------------------------------------------------------------------
# Tag extraction helpers
# ---------------------------------------------------------------------------

def _extract_tags(kernel: dict) -> list[str]:
    """Pull tech tags from a kernel item."""
    raw_tags = kernel.get("tags") or []
    tags: list[str] = []
    for t in raw_tags:
        if isinstance(t, str):
            tags.append(t.lower())
        elif isinstance(t, dict):
            name = t.get("ref") or t.get("displayName") or t.get("name") or ""
            if name:
                tags.append(name.lower())
    return tags


def _extract_author(kernel: dict) -> str:
    """Extract author username from a kernel item."""
    return (
        kernel.get("author")
        or kernel.get("authorRef")
        or kernel.get("authorSlug")
        or ""
    ).lower().strip()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _tier_to_int(tier_str: str) -> int:
    return _TIER_RANK.get((tier_str or "").lower().strip(), 0)


def _skill_match(author_tags: list[str], criteria: dict) -> tuple[int, list[str], list[str]]:
    required = [s.lower().strip() for s in (criteria.get("skills") or [])]
    if not required:
        return 20, [], []
    tag_norm = {t.replace("-", "").replace(" ", "") for t in author_tags}
    matched, missing = [], []
    for skill in required:
        sk = skill.replace("-", "").replace(" ", "")
        found = (
            sk in tag_norm
            or any(sk in t for t in tag_norm if len(sk) >= 3)
        )
        (matched if found else missing).append(skill)
    ratio = len(matched) / max(len(required), 1)
    return min(40, int(ratio * 40)), matched, missing


def _activity_score(total_votes: int, kernel_count: int, tier: int) -> int:
    vote_pts = min(12, int(math.log1p(total_votes) * 2.0))
    kernel_pts = min(8, int(math.log1p(kernel_count) * 2.5))
    tier_pts = min(5, tier)
    return min(25, vote_pts + kernel_pts + tier_pts)


def _seniority_score(tier: int, account_age: float, criteria: dict) -> int:
    requested = criteria.get("seniority", "MID")
    # Infer seniority from Kaggle tier
    inferred = "JUNIOR" if tier <= 1 else ("SENIOR" if tier >= 3 else "MID")
    lvl_map = {"JUNIOR": 0, "MID": 1, "SENIOR": 2}
    diff = abs(lvl_map.get(inferred, 1) - lvl_map.get(requested, 1))
    tier_pts = 8 if diff == 0 else (4 if diff == 1 else 0)
    age_pts = min(8, int(account_age * 1.5))
    complexity_pts = min(4, tier * 1)
    return min(20, tier_pts + age_pts + complexity_pts)


def _completeness_score(profile: dict) -> int:
    pts = 0
    if profile.get("display_name"):
        pts += 3
    if profile.get("location"):
        pts += 4
    if profile.get("bio"):
        pts += 4
    if profile.get("website"):
        pts += 2
    if profile.get("organization"):
        pts += 2
    return min(15, pts)


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def _normalize(
    username: str,
    agg: dict,
    user_profile: dict | None,
    criteria: dict,
) -> dict:
    profile_id = f"kg_{username}"

    total_votes = agg.get("total_votes", 0)
    kernel_count = agg.get("kernel_count", 0)
    author_tags = list(agg.get("tags", set()))
    tier = _tier_to_int(agg.get("tier", ""))

    # Enrich from user profile if available
    display_name = username
    location = ""
    bio_extra = ""
    website = ""
    organization = ""
    account_age = 0.0
    if user_profile:
        display_name = (
            user_profile.get("displayName")
            or user_profile.get("display_name")
            or user_profile.get("userName")
            or username
        )
        pf = user_profile.get("profile") or user_profile
        location = pf.get("location") or pf.get("city") or ""
        bio_extra = pf.get("bio") or ""
        website = pf.get("website") or pf.get("websiteUrl") or ""
        organization = pf.get("organization") or pf.get("company") or ""
        # tier from profile may override aggregated tier
        tier_str = (
            user_profile.get("tier")
            or user_profile.get("performanceTier")
            or user_profile.get("performance_tier")
            or agg.get("tier", "")
        )
        tier = max(tier, _tier_to_int(tier_str))
        joined = user_profile.get("registeredDate") or user_profile.get("joined") or ""
        if joined:
            try:
                dt = datetime.fromisoformat(joined.replace("Z", "+00:00"))
                account_age = (datetime.now(timezone.utc) - dt).days / 365.25
            except Exception:
                pass

    completeness_input = {
        "display_name": display_name,
        "location": location,
        "bio": bio_extra,
        "website": website,
        "organization": organization,
    }

    skill_pts, matched, missing = _skill_match(author_tags, criteria)
    activity_pts = _activity_score(total_votes, kernel_count, tier)
    seniority_pts = _seniority_score(tier, account_age, criteria)
    completeness_pts = _completeness_score(completeness_input)
    total = skill_pts + activity_pts + seniority_pts + completeness_pts

    tier_label = _TIER_LABEL.get(tier, "Contributor")
    bio = (
        f"Kaggle {tier_label} — {kernel_count} notebooks, {total_votes:,} total votes"
        + (f". {bio_extra}" if bio_extra else "")
    )
    profile_url = f"https://www.kaggle.com/{username}"

    return {
        "candidate_id": profile_id,
        "source": "kaggle",
        "github_username": profile_id,
        "display_name": display_name,
        "location": location,
        "company": organization,
        "bio": bio,
        "blog": website,
        "tech_stack": author_tags[:10],
        "repo_topics": author_tags,
        "top_repos": [],
        "yoe_proxy": round(max(0.0, account_age - 0.5), 1),
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
        "avatar_url": "",
        "email": None,
        "followers": total_votes,
        "public_repos": kernel_count,
        "total_stars": total_votes,
        "created_at": "",
        "kaggle_tier": tier_label,
        "kaggle_total_votes": total_votes,
        "kaggle_kernels": kernel_count,
        "kaggle_tags": author_tags,
        "pipeline_status": "sourced",
        "resume_pdf_url": "",
        "resume_status": "not_applicable",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def source_candidates(criteria: dict, count: int = 20) -> list[dict]:
    """Search Kaggle and return normalized Tvarah candidate profiles.

    Returns empty list if KAGGLE_USERNAME / KAGGLE_KEY env vars are not set.
    """
    if not _auth():
        logger.info("Kaggle sourcing skipped — credentials not configured")
        return []

    skills = criteria.get("skills") or []
    search_term = _search_term(skills)

    logger.info("Kaggle sourcing: term=%r count=%d", search_term, count)

    # Step 1: Search kernels + datasets to collect candidate authors
    kernels = _search_kernels(search_term, page_size=min(count * 3, 100))
    datasets = _search_datasets(search_term, page_size=min(count, 50))

    # Step 2: Aggregate per-author stats
    agg: dict[str, dict] = defaultdict(lambda: {
        "total_votes": 0,
        "kernel_count": 0,
        "tags": set(),
        "tier": "",
    })

    for item in kernels:
        author = _extract_author(item)
        if not author:
            continue
        votes = item.get("totalVotes") or item.get("total_votes") or 0
        agg[author]["total_votes"] += int(votes)
        agg[author]["kernel_count"] += 1
        for tag in _extract_tags(item):
            agg[author]["tags"].add(tag)

    for item in datasets:
        creator = (
            item.get("creatorRef")
            or item.get("ownerRef")
            or item.get("creator")
            or ""
        ).lower().strip()
        if not creator:
            continue
        votes = item.get("totalVotes") or item.get("upVoteCount") or 0
        agg[creator]["total_votes"] += int(votes)
        agg[creator]["kernel_count"] += 1  # count datasets too
        for tag in _extract_tags(item):
            agg[creator]["tags"].add(tag)

    if not agg:
        logger.warning("Kaggle: no authors found for %r", search_term)
        return []

    # Step 3: Rank by total_votes, pick top N for enrichment
    ranked = sorted(agg.items(), key=lambda kv: kv[1]["total_votes"], reverse=True)
    top_authors = [u for u, _ in ranked[:min(count * 2, 60)]]

    # Step 4: Enrich with user profiles (parallel, best-effort)
    user_profiles: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=_PARALLEL) as ex:
        futs = {ex.submit(_fetch_user_profile, u): u for u in top_authors[:count]}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                user_profiles[u] = fut.result()
            except Exception:
                user_profiles[u] = None

    # Step 5: Normalize
    candidates: list[dict] = []
    for username in top_authors:
        user_data = agg[username]
        profile = user_profiles.get(username)
        normalized = _normalize(username, user_data, profile, criteria)
        candidates.append(normalized)

    candidates.sort(key=lambda c: c["sourcing_score"], reverse=True)
    logger.info("Kaggle: returning %d candidates", min(len(candidates), count))
    return candidates[:count]
