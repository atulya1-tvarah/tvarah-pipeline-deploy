"""Normalize raw GitHub profiles into Tvarah-compatible scored candidates."""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone

logger = logging.getLogger("resume_intelligence.sourcing.profile_normalizer")


def _account_age_years(created_at: str) -> float:
    if not created_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days / 365.25
    except Exception:
        return 0.0


def _skill_signals(profile: dict) -> set[str]:
    """Build a set of lowercased tech signals from all profile data."""
    signals: set[str] = set()

    for lang in profile.get("primary_languages") or []:
        signals.add(lang.lower())

    for r in profile.get("top_repos") or []:
        if r.get("language"):
            signals.add(r["language"].lower())
        for topic in r.get("topics") or []:
            signals.add(topic.lower())
        desc = (r.get("description") or "").lower()
        # Extract common tech keywords from repo descriptions
        for token in re.findall(r"[a-z][a-z0-9_\-.+#]{1,20}", desc):
            signals.add(token)

    for topic in profile.get("repo_topics") or []:
        signals.add(topic.lower())

    bio = (profile.get("bio") or "").lower()
    for token in re.findall(r"[a-z][a-z0-9_\-.+#]{1,20}", bio):
        signals.add(token)

    readme = (profile.get("readme_text") or "").lower()
    # Extract tech terms from README (longer tokens are more specific)
    for token in re.findall(r"[a-z][a-z0-9_\-.+#]{2,25}", readme):
        signals.add(token)

    return signals


def _skill_match_score(profile: dict, criteria: dict) -> tuple[int, list[str], list[str]]:
    """Return (score 0-40, matched_skills, missing_skills)."""
    required = list(dict.fromkeys(s.lower() for s in (criteria.get("skills") or [])))
    if not required:
        return 20, [], []  # neutral when no skills specified

    signals = _skill_signals(profile)
    matched = []
    missing = []

    for skill in required:
        # Flexible matching: "fastapi" matches "fastapi", "fast-api" in signals
        skill_key = skill.replace(" ", "").replace("-", "").replace("_", "")
        norm_signals = {s.replace("-", "").replace("_", "") for s in signals}
        found = (
            skill in signals
            or skill_key in norm_signals
            # substring only for skills >= 4 chars (avoids "go" matching "django")
            or (len(skill_key) >= 4 and any(skill_key in s for s in norm_signals if len(s) > len(skill_key)))
        )
        if found:
            matched.append(skill)
        else:
            missing.append(skill)

    ratio = len(matched) / len(required)
    return min(40, int(ratio * 40)), matched, missing


def _activity_score(profile: dict) -> int:
    """0-25: followers, stars, repos."""
    followers = profile.get("followers") or 0
    total_stars = profile.get("total_stars") or 0
    public_repos = profile.get("public_repos") or 0

    follower_pts = min(10, int(math.log1p(followers) * 2))
    star_pts = min(10, int(math.log1p(total_stars) * 1.5))
    repo_pts = min(5, int(math.log1p(public_repos)))
    return follower_pts + star_pts + repo_pts


def _seniority_proxy_score(profile: dict, criteria: dict) -> int:
    """0-20: account age, repo complexity, seniority alignment."""
    age = _account_age_years(profile.get("created_at", ""))
    requested = criteria.get("seniority", "MID")

    age_pts = (
        10 if age >= 7 else
        8 if age >= 5 else
        6 if age >= 3 else
        3 if age >= 1 else 1
    )

    repos = [r for r in (profile.get("top_repos") or []) if not r.get("fork")]
    starred = [r.get("stars", 0) for r in repos if r.get("stars", 0) > 0]
    avg_stars = sum(starred) / len(starred) if starred else 0
    complexity_pts = min(6, int(math.log1p(avg_stars)))

    inferred = "JUNIOR" if age < 2 else ("SENIOR" if age >= 5 else "MID")
    lvl_map = {"JUNIOR": 0, "MID": 1, "SENIOR": 2}
    diff = abs(lvl_map.get(inferred, 1) - lvl_map.get(requested, 1))
    seniority_pts = 4 if diff == 0 else (2 if diff == 1 else 0)

    return min(20, age_pts + complexity_pts + seniority_pts)


def _completeness_score(profile: dict) -> int:
    """0-15: bio, company, location, email, blog/website."""
    pts = 0
    if profile.get("bio"):
        pts += 4
    if profile.get("readme_text"):
        pts += 3  # bonus for having a profile README
    if profile.get("company"):
        pts += 3
    if profile.get("location"):
        pts += 2
    if profile.get("email"):
        pts += 2
    if profile.get("blog"):
        pts += 1
    return min(15, pts)


def _estimate_yoe(profile: dict) -> float:
    age = _account_age_years(profile.get("created_at", ""))
    return round(max(0.0, age - 1.0), 1)


def normalize(raw_profile: dict, criteria: dict) -> dict:
    """Convert a raw GitHub profile into a scored Tvarah sourcing candidate."""
    username = raw_profile.get("username", "")

    skill_pts, matched_skills, missing_skills = _skill_match_score(raw_profile, criteria)
    activity_pts = _activity_score(raw_profile)
    seniority_pts = _seniority_proxy_score(raw_profile, criteria)
    completeness_pts = _completeness_score(raw_profile)
    total = skill_pts + activity_pts + seniority_pts + completeness_pts

    langs = raw_profile.get("primary_languages") or []
    top_repos = raw_profile.get("top_repos") or []
    topics = raw_profile.get("repo_topics") or []

    return {
        "candidate_id": f"gh_{username}",
        "source": "github",
        "github_username": username,
        "display_name": raw_profile.get("display_name") or username,
        "location": raw_profile.get("location") or "",
        "company": raw_profile.get("company") or "",
        "bio": raw_profile.get("bio") or "",
        "blog": raw_profile.get("blog") or "",
        "tech_stack": langs,
        "repo_topics": topics,
        "top_repos": top_repos,
        "yoe_proxy": _estimate_yoe(raw_profile),
        "sourcing_score": total,
        "score_breakdown": {
            "skill_match": skill_pts,
            "activity": activity_pts,
            "seniority": seniority_pts,
            "completeness": completeness_pts,
        },
        "matched_skills": [s.title() for s in matched_skills],
        "missing_skills": [s.title() for s in missing_skills],
        "github_url": raw_profile.get("github_url") or f"https://github.com/{username}",
        "avatar_url": raw_profile.get("avatar_url") or "",
        "email": raw_profile.get("email") or None,
        "followers": raw_profile.get("followers") or 0,
        "public_repos": raw_profile.get("public_repos") or 0,
        "total_stars": raw_profile.get("total_stars") or 0,
        "created_at_gh": raw_profile.get("created_at") or "",
        "readme_text": raw_profile.get("readme_text") or "",
        "resume_pdf_url": raw_profile.get("resume_pdf_url") or "",
        "resume_status": "found" if raw_profile.get("resume_pdf_url") else "not_found",
        "pipeline_status": "sourced",
    }


def normalize_many(raw_profiles: list[dict], criteria: dict) -> list[dict]:
    results = [normalize(p, criteria) for p in raw_profiles]
    results.sort(key=lambda x: x["sourcing_score"], reverse=True)
    return results
