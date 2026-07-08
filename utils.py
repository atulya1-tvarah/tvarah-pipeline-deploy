
from __future__ import annotations
import re
from datetime import date, datetime
from typing import Any

def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def flatten_text(data: Any) -> str:
    parts = []
    def _walk(obj):
        if obj is None:
            return
        if isinstance(obj, dict):
            for v in obj.values(): _walk(v)
        elif isinstance(obj, list):
            for item in obj: _walk(item)
        else:
            parts.append(str(obj))
    _walk(data)
    return normalize_text(" ".join(parts))

def parse_date(value: str | None):
    if not value: return None
    value = str(value).strip()
    fmts = ["%Y-%m-%d","%Y-%m","%b %Y","%B %Y","%b-%Y","%B-%Y","%m/%Y","%b-%y","%B-%y","%Y"]
    if value.lower() in {"present","current","ongoing","till date","till now"}:
        return date.today()
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt)
            month = dt.month if ("%m" in fmt or "%b" in fmt or "%B" in fmt) else 1
            return date(dt.year, month, 1)
        except Exception:
            pass
    return None

def month_diff(start, end):
    if not start or not end: return 0
    return max(0, (end.year-start.year)*12 + (end.month-start.month))

def first_non_empty(*values):
    for v in values:
        if v not in (None, "", [], {}): return v
    return None

def dedupe_keep_order(values):
    seen = set()
    out = []
    for value in values or []:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            value = normalize_text(value)
        marker = value if isinstance(value, (str, int, float, bool, tuple)) else repr(value)
        if marker not in seen:
            seen.add(marker)
            out.append(value)
    return out

def titlecase_name(value: str | None) -> str | None:
    value = normalize_text(value)
    if not value:
        return None
    if value.isupper():
        return value.title()
    return value

def join_location(*parts):
    cleaned = [normalize_text(part) for part in parts if normalize_text(part)]
    return ", ".join(cleaned) if cleaned else None

def get_by_path(data: Any, *path: str, default=None):
    current = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current
