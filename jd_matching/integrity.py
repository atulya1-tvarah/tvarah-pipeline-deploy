from __future__ import annotations
from datetime import datetime
from typing import Dict, List
from .helpers import parse_ym, months_between, norm_text


def _resume_root(resume_json: dict) -> dict:
    root = resume_json.get("resume_data", resume_json)
    if isinstance(root, dict) and "insight_info" in root:
        merged = {}
        if isinstance(root.get("basic_info"), dict):
            merged.update(root.get("basic_info") or {})
        if isinstance(root.get("insight_info"), dict):
            merged.update(root.get("insight_info") or {})
        for k, v in root.items():
            if k not in {"basic_info", "insight_info"}:
                merged.setdefault(k, v)
        return merged
    return root


def evaluate_integrity(resume_json: dict) -> Dict[str, object]:
    root = _resume_root(resume_json)
    work = root.get("work_experience_info", []) or []
    flags: List[str] = []
    hard_flags: List[str] = []
    score = 100
    now = datetime.now().replace(day=1)
    spans = []
    for idx, exp in enumerate(work):
        start = parse_ym(exp.get("start_date"))
        end = parse_ym(exp.get("end_date")) if exp.get("end_date") else now
        title = exp.get("job_title") or "role"
        company = exp.get("company_name") or "company"
        if not start:
            flags.append(f"Unclear start date for {title} at {company}")
            score -= 5
            continue
        if start > now:
            hard_flags.append(f"Future-dated start date for {title} at {company}")
            score -= 25
        if end < start:
            hard_flags.append(f"End date before start date for {title} at {company}")
            score -= 20
        if end > now:
            flags.append(f"Future-dated end date for {title} at {company}")
            score -= 10
        spans.append((start, end, title, company))

    spans_sorted = sorted(spans, key=lambda x: x[0])
    for i in range(1, len(spans_sorted)):
        prev = spans_sorted[i-1]
        cur = spans_sorted[i]
        if cur[0] < prev[1] and months_between(cur[0], prev[1]) > 2:
            flags.append(f"Overlapping roles: {prev[2]} at {prev[3]} and {cur[2]} at {cur[3]}")
            score -= 8

    # An entry with no end_date already defaults to "now" above (still
    # ongoing), so this only fires for a genuine gap: the most recent job
    # has an explicit past end date and nothing newer follows it -- the
    # candidate is currently between jobs. Same >2-month threshold as the
    # overlap check above, for consistency.
    if spans_sorted:
        latest_end = spans_sorted[-1][1]
        gap_to_now = months_between(latest_end, now)
        if gap_to_now > 2:
            latest_title, latest_company = spans_sorted[-1][2], spans_sorted[-1][3]
            flags.append(f"Candidate not currently employed: {latest_title} at {latest_company} ended ~{gap_to_now} months ago")
            score -= 8

    phone = (((root.get("basic_info") or {}).get("contact_info") or {}).get("primary_phone_number") or "")
    if phone and len(''.join(ch for ch in phone if ch.isdigit())) < 10:
        flags.append("Phone number appears incomplete")
        score -= 4

    summary_text = norm_text(str(root))
    if 'managed 15' in summary_text and 'team' not in summary_text:
        flags.append("Leadership claim formatting looks inconsistent")
        score -= 3

    confidence = 'high' if score >= 85 else 'medium' if score >= 65 else 'low'
    return {
        "integrity_score": max(0, min(100, score)),
        "confidence": confidence,
        "warning_flags": flags[:10],
        "hard_flags": hard_flags[:10],
    }
