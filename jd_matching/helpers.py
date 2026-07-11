from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, List, Optional

_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10, 'october': 10,
    'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def norm_skill(s: str) -> str:
    s = norm_text(s)
    replacements = {
        "ipython": "jupyter",
        "jupyter notebook": "jupyter",
        "sklearn": "scikit-learn",
        "scikit learn": "scikit-learn",
        "spark sql": "apache spark",
        "amazon web services": "aws",
        "aws cloud": "aws",
        "powerbi": "power bi",
        "qlik sense": "qlik",
        "b.tech": "bachelor of technology",
        "m.tech": "master of technology",
        "elt": "etl",
    }
    return replacements.get(s, s)


def unique_norm(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        n = norm_skill(item)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def parse_years_from_string(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"(\d+(\.\d+)?)", s)
    if m:
        return float(m.group(1))
    return None


def parse_ym(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    raw = s.strip().replace('/', '-').replace(',', ' ')
    low = raw.lower()
    if low in {"present", "current", "now", "till date", "ongoing"}:
        return datetime.now().replace(day=1)

    # Was previously truncating `raw` to len(fmt.replace('%','')) chars
    # before parsing -- that's the length of the format *template* (e.g.
    # "%Y-%m" -> "Y-m" -> 3), not the expected length of an actual date
    # string, so every "YYYY-MM" input got cut down to 3 characters (just
    # the year's first 3 digits) and never matched any format here. It fell
    # through to the bare-4-digit-year regex fallback below every time,
    # silently returning January 1st of the right year regardless of the
    # real month -- e.g. parse_ym("2023-09") returned 2023-01-01. This
    # affected every date computation in this module (overlap detection,
    # future-date checks). strptime already fails cleanly on a genuine
    # mismatch, so there's no need to pre-truncate at all.
    for fmt in ("%Y-%m", "%Y-%b", "%Y-%B", "%b-%Y", "%B-%Y", "%m-%Y", "%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(day=1)
        except Exception:
            pass

    m = re.search(r"([A-Za-z]+)[\s\-]+(20\d{2}|19\d{2})", raw)
    if m:
        mon = _MONTHS.get(m.group(1).lower())
        year = int(m.group(2))
        if mon:
            return datetime(year, mon, 1)
    m = re.search(r"(20\d{2}|19\d{2})[\s\-]+([A-Za-z]+)", raw)
    if m:
        year = int(m.group(1))
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            return datetime(year, mon, 1)
    m = re.search(r"(20\d{2}|19\d{2})", raw)
    if m:
        return datetime(int(m.group(1)), 1, 1)
    return None


def months_between(start: datetime, end: datetime) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))
