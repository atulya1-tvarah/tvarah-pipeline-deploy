from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_TRAINING_DIR = Path("training_exports")
DEFAULT_LABEL_DIR = Path("feedback_data")
DEFAULT_GOLD_FILE = DEFAULT_LABEL_DIR / "gold_labels.jsonl"
DEFAULT_REVIEW_QUEUE = DEFAULT_LABEL_DIR / "label_review_queue.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _label_text(row: dict[str, Any], task: str) -> str:
    label = row.get("label")
    if task == "skill_depth" and isinstance(label, dict):
        return str(label.get("depth_label") or "")
    if isinstance(label, dict):
        return ""
    return str(label or "")


def _review_text(row: dict[str, Any], task: str) -> str:
    if task == "project_type":
        return str(row.get("classifier_text") or row.get("text") or row.get("resume_text") or "")
    if task == "skill_depth":
        return str(row.get("classifier_text") or "")
    return str(row.get("classifier_text") or row.get("resume_text") or "")


def build_review_queue(training_dir: str = str(DEFAULT_TRAINING_DIR), output_file: str = str(DEFAULT_REVIEW_QUEUE)) -> dict[str, Any]:
    training_path = Path(training_dir)
    tasks = ["role_family", "dna_fit", "project_type", "skill_depth"]
    all_rows: list[dict[str, Any]] = []
    for task in tasks:
        file_path = training_path / f"{task}.jsonl"
        rows = _load_jsonl(file_path)
        labels = [_label_text(row, task) for row in rows if _label_text(row, task)]
        counts = Counter(labels)
        for row in rows:
            label = _label_text(row, task)
            if not label:
                continue
            rarity = counts.get(label, 0)
            if task == "skill_depth" and label == "AWARENESS":
                continue
            priority = "normal"
            if rarity <= 3:
                priority = "high"
            elif task in {"role_family", "dna_fit"}:
                priority = "high"
            all_rows.append(
                {
                    "task": task,
                    "record_id": row.get("record_id") or row.get("resume_id"),
                    "resume_id": row.get("resume_id"),
                    "candidate_name": row.get("candidate_name"),
                    "source_file": row.get("source_file"),
                    "silver_label": label,
                    "gold_label": "",
                    "priority": priority,
                    "label_frequency": rarity,
                    "text": _review_text(row, task)[:4000],
                    "metadata": {
                        "skill": row.get("skill"),
                        "role_title": row.get("role_title"),
                        "company": row.get("company"),
                        "candidates": row.get("candidates", []),
                        "features": row.get("features", {}),
                    },
                }
            )
    all_rows.sort(key=lambda item: (0 if item["priority"] == "high" else 1, item["task"], item["candidate_name"] or ""))
    output_path = Path(output_file)
    _write_jsonl(output_path, all_rows)
    return {
        "output_file": str(output_path),
        "rows": len(all_rows),
        "high_priority_rows": sum(1 for row in all_rows if row["priority"] == "high"),
    }


def bootstrap_gold_labels(review_queue_file: str = str(DEFAULT_REVIEW_QUEUE), gold_file: str = str(DEFAULT_GOLD_FILE)) -> dict[str, Any]:
    queue_rows = _load_jsonl(Path(review_queue_file))
    seed_rows = [
        {
            "task": row.get("task"),
            "record_id": row.get("record_id"),
            "resume_id": row.get("resume_id"),
            "candidate_name": row.get("candidate_name"),
            "source_file": row.get("source_file"),
            "silver_label": row.get("silver_label"),
            "gold_label": row.get("gold_label") or "",
            "review_status": "pending",
            "review_notes": "",
        }
        for row in queue_rows
    ]
    output_path = Path(gold_file)
    _write_jsonl(output_path, seed_rows)
    return {
        "gold_file": str(output_path),
        "rows": len(seed_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build label review queues and gold-label templates from training exports.")
    parser.add_argument("--training-dir", default=str(DEFAULT_TRAINING_DIR))
    parser.add_argument("--output-file", default=str(DEFAULT_REVIEW_QUEUE))
    parser.add_argument("--gold-file", default=str(DEFAULT_GOLD_FILE))
    parser.add_argument("--bootstrap-gold", action="store_true")
    args = parser.parse_args()

    queue_result = build_review_queue(training_dir=args.training_dir, output_file=args.output_file)
    result: dict[str, Any] = {"review_queue": queue_result}
    if args.bootstrap_gold:
        result["gold_labels"] = bootstrap_gold_labels(review_queue_file=args.output_file, gold_file=args.gold_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
