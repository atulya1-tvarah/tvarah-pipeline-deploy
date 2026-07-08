from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

CLIENT_REPORTS_DIR = Path(__file__).resolve().parent / "client_reports"

_VALID_EVENTS = {"selected", "joined", "churned", "rejected"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_dir(client_id: str) -> Path:
    d = CLIENT_REPORTS_DIR / client_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


# ---------------------------------------------------------------------------
# Position event tracking
# ---------------------------------------------------------------------------

def append_position_event(client_id: str, event: dict[str, Any]) -> None:
    """Append a position event (selected | joined | churned | rejected)."""
    event_type = event.get("event_type", "")
    if event_type not in _VALID_EVENTS:
        raise ValueError(f"event_type must be one of {_VALID_EVENTS}, got {event_type!r}")
    record = {"timestamp": _now_iso(), **event}
    path = _client_dir(client_id) / "position_tracking.jsonl"
    _append_jsonl(path, record)


def get_client_report(client_id: str, months_back: int = 3) -> dict[str, Any]:
    """Compute selection / joining / churn rates over the past N months."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months_back * 30)
    path = _client_dir(client_id) / "position_tracking.jsonl"
    rows = _read_jsonl(path)

    counts: dict[str, int] = {e: 0 for e in _VALID_EVENTS}
    for row in rows:
        ts_str = row.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            continue
        ev = row.get("event_type", "")
        if ev in counts:
            counts[ev] += 1

    total_selected = counts["selected"]
    total_joined = counts["joined"]
    total_rejected = counts["rejected"]
    total_churned = counts["churned"]
    total_pipeline = total_selected + total_rejected

    return {
        "client_id": client_id,
        "months_back": months_back,
        "total_selected": total_selected,
        "total_joined": total_joined,
        "total_rejected": total_rejected,
        "total_churned": total_churned,
        "selection_rate": round(total_selected / max(total_pipeline, 1), 3),
        "joining_rate": round(total_joined / max(total_selected, 1), 3),
        "churn_rate": round(total_churned / max(total_joined, 1), 3),
    }


# ---------------------------------------------------------------------------
# Candidate feedback
# ---------------------------------------------------------------------------

def save_candidate_feedback(
    client_id: str, candidate_id: str, feedback: dict[str, Any]
) -> None:
    record = {"timestamp": _now_iso(), "candidate_id": candidate_id, **feedback}
    path = _client_dir(client_id) / "candidate_feedback.jsonl"
    _append_jsonl(path, record)


# ---------------------------------------------------------------------------
# Client (recruiter) feedback
# ---------------------------------------------------------------------------

def save_client_feedback(client_id: str, feedback: dict[str, Any]) -> None:
    record = {"timestamp": _now_iso(), **feedback}
    path = _client_dir(client_id) / "client_feedback.jsonl"
    _append_jsonl(path, record)
