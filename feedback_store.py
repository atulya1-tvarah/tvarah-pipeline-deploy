from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FEEDBACK_DIR = Path(__file__).resolve().parent / "feedback_data"
FEEDBACK_FILE = FEEDBACK_DIR / "resume_feedback.jsonl"


def save_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with FEEDBACK_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    return {
        "stored": True,
        "path": str(FEEDBACK_FILE),
        "timestamp_utc": record["timestamp_utc"],
    }
