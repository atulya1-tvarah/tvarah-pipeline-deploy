"""gold_label_generator.py — Convert recruiter feedback and eval run outcomes into gold labels.

Reads from:
  - feedback_data/  (recruiter decision JSON files with corrected_role_family, recruiter_decision, etc.)
  - eval_runs/      (eval report JSONs with case-level expectation_result and analysis snapshots)

Writes to:
  - feedback_data/gold_labels.jsonl

Gold label format (consumed by train_bert.py --gold-labels-file):
  {
    "record_id": str,
    "task": str,                 # role_family / dna_fit / skill_depth
    "gold_label": str,
    "source": str,               # recruiter_correction | recruiter_confirmed | interview_inferred
    "confidence": float,         # 1.0 = gold, 0.8 = high-confidence silver
    "source_file": str,
    "review_status": "approved"  # required by train_bert.py loader
  }

Usage:
    python gold_label_generator.py
    python gold_label_generator.py --feedback-dir feedback_data/ --eval-dir eval_runs/ --output feedback_data/gold_labels.jsonl
    python gold_label_generator.py --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


# Mapping from interview outcome + corrected_band to inferred skill_depth tier
BAND_TO_SKILL_DEPTH: dict[str, str] = {
    "STRONG_HIRE": "ADVANCED",
    "HIRE": "HANDS_ON",
    "BORDERLINE": "FOUNDATIONAL",
    "REJECT": "AWARENESS",
    "NO_HIRE": "FOUNDATIONAL",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = f"{row.get('task')}::{row.get('record_id')}"
        if key not in seen:
            seen.add(key)
            unique.append(row)
    with path.open("w", encoding="utf-8") as fh:
        for row in unique:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(unique)


def _collect_feedback_files(feedback_dir: Path) -> list[Path]:
    return sorted(
        f for f in feedback_dir.rglob("*.json")
        if f.is_file() and "gold_labels" not in f.name
    )


def _collect_eval_report_files(eval_dir: Path) -> list[Path]:
    return sorted(f for f in eval_dir.rglob("report.json") if f.is_file())


def _extract_from_feedback_file(path: Path) -> list[dict[str, Any]]:
    """Parse a recruiter feedback JSON and extract gold labels."""
    try:
        data = _load_json(path)
    except Exception as exc:
        print(f"  [WARN] Cannot read {path.name}: {exc}")
        return []

    rows: list[dict[str, Any]] = []
    record_id = str(data.get("record_id") or data.get("resume_id") or data.get("candidate_id") or path.stem)
    source_file = str(path)

    # 1. Explicit role_family correction
    corrected_role = str(data.get("corrected_role_family") or "").strip().upper()
    if corrected_role:
        rows.append({
            "record_id": record_id,
            "task": "role_family",
            "gold_label": corrected_role,
            "source": "recruiter_correction",
            "confidence": 1.0,
            "source_file": source_file,
            "review_status": "approved",
        })

    # 2. Explicit DNA correction
    corrected_dna = str(data.get("corrected_dna") or "").strip().upper()
    if corrected_dna:
        rows.append({
            "record_id": record_id,
            "task": "dna_fit",
            "gold_label": corrected_dna,
            "source": "recruiter_correction",
            "confidence": 1.0,
            "source_file": source_file,
            "review_status": "approved",
        })

    # 3. Recruiter selection confirms predicted role_family silver label
    recruiter_decision = str(data.get("recruiter_decision") or "").strip().lower()
    predicted_role = str(data.get("predicted_role_family") or "").strip().upper()
    if recruiter_decision == "selected" and predicted_role and not corrected_role:
        rows.append({
            "record_id": record_id,
            "task": "role_family",
            "gold_label": predicted_role,
            "source": "recruiter_confirmed",
            "confidence": 0.85,
            "source_file": source_file,
            "review_status": "approved",
        })

    # 4. Interview outcome + corrected_band → infer skill_depth tier
    corrected_band = str(data.get("corrected_band") or "").strip().upper()
    interview_outcome = str(data.get("interview_outcome") or "").strip().upper()
    infer_source = corrected_band or interview_outcome
    skill_depth_label = BAND_TO_SKILL_DEPTH.get(infer_source)
    if skill_depth_label and infer_source:
        rows.append({
            "record_id": record_id,
            "task": "skill_depth",
            "gold_label": skill_depth_label,
            "source": "interview_inferred",
            "confidence": 0.80 if infer_source == corrected_band else 0.70,
            "source_file": source_file,
            "review_status": "approved",
        })

    # 5. Explicit career_progression correction
    corrected_cp = str(data.get("corrected_career_progression") or "").strip().upper()
    if corrected_cp:
        rows.append({
            "record_id": record_id,
            "task": "career_progression",
            "gold_label": corrected_cp,
            "source": "recruiter_correction",
            "confidence": 1.0,
            "source_file": source_file,
            "review_status": "approved",
        })

    return rows


def _extract_from_eval_report(path: Path) -> list[dict[str, Any]]:
    """Extract confirmed labels from eval report cases where expectation matched."""
    try:
        report = _load_json(path)
    except Exception as exc:
        print(f"  [WARN] Cannot read {path.name}: {exc}")
        return []

    rows: list[dict[str, Any]] = []
    for case in report.get("cases", []) or []:
        exp_result = case.get("expectation_result")
        if not exp_result or not exp_result.get("matched"):
            continue

        expected = case.get("expected", {}) or {}
        predicted = case.get("predicted", {}) or {}
        case_id = str(case.get("case_id") or "")
        source_file = str(path)

        # Only confirm when expected == predicted (full match)
        exp_role = str(expected.get("expected_role_family") or "").strip().upper()
        pred_role = str(predicted.get("role_family") or "").strip().upper()
        if exp_role and pred_role and exp_role == pred_role:
            rows.append({
                "record_id": case_id,
                "task": "role_family",
                "gold_label": exp_role,
                "source": "eval_confirmed",
                "confidence": 0.90,
                "source_file": source_file,
                "review_status": "approved",
            })

        exp_dna = str(expected.get("expected_dna") or "").strip().upper()
        pred_dna = str(predicted.get("dna") or "").strip().upper()
        if exp_dna and pred_dna and exp_dna == pred_dna:
            rows.append({
                "record_id": case_id,
                "task": "dna_fit",
                "gold_label": exp_dna,
                "source": "eval_confirmed",
                "confidence": 0.90,
                "source_file": source_file,
                "review_status": "approved",
            })

    return rows


def generate_gold_labels(
    feedback_dir: str = "feedback_data",
    eval_dir: str = "eval_runs",
    output_path: str = "feedback_data/gold_labels.jsonl",
    dry_run: bool = False,
) -> dict[str, Any]:
    fb_dir = Path(feedback_dir)
    ev_dir = Path(eval_dir)
    out_path = Path(output_path)

    all_rows: list[dict[str, Any]] = []

    # From recruiter feedback files
    fb_count = 0
    if fb_dir.exists():
        for path in _collect_feedback_files(fb_dir):
            extracted = _extract_from_feedback_file(path)
            all_rows.extend(extracted)
            fb_count += len(extracted)
    print(f"  Extracted {fb_count} gold labels from feedback_data/")

    # From eval run reports
    ev_count = 0
    if ev_dir.exists():
        for path in _collect_eval_report_files(ev_dir):
            extracted = _extract_from_eval_report(path)
            all_rows.extend(extracted)
            ev_count += len(extracted)
    print(f"  Extracted {ev_count} gold labels from eval_runs/")

    # Deduplicate (task + record_id), prefer higher confidence
    deduped: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        key = f"{row.get('task')}::{row.get('record_id')}"
        existing = deduped.get(key)
        if existing is None or float(row.get("confidence") or 0) > float(existing.get("confidence") or 0):
            deduped[key] = row

    final_rows = list(deduped.values())

    # Task distribution summary
    task_counts: dict[str, int] = {}
    for row in final_rows:
        t = row.get("task", "unknown")
        task_counts[t] = task_counts.get(t, 0) + 1

    print(f"  Total unique gold labels: {len(final_rows)}")
    for task, count in sorted(task_counts.items()):
        print(f"    {task}: {count}")

    if dry_run:
        print("  [DRY RUN] No file written.")
        return {
            "total": len(final_rows),
            "task_counts": task_counts,
            "output_path": str(out_path),
            "written": False,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = _write_jsonl(out_path, final_rows)
    print(f"  Wrote {written} gold labels to {out_path}")
    return {
        "total": written,
        "task_counts": task_counts,
        "output_path": str(out_path),
        "written": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate gold labels from recruiter feedback and eval run outcomes for BERT retraining."
    )
    parser.add_argument("--feedback-dir", default="feedback_data",
                        help="Directory with recruiter feedback JSON files.")
    parser.add_argument("--eval-dir", default="eval_runs",
                        help="Directory with eval run report.json files.")
    parser.add_argument("--output", default="feedback_data/gold_labels.jsonl",
                        help="Output JSONL path for gold labels.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print stats without writing the output file.")
    args = parser.parse_args()

    print("=" * 60)
    print("Gold Label Generator")
    print("=" * 60)
    result = generate_gold_labels(
        feedback_dir=args.feedback_dir,
        eval_dir=args.eval_dir,
        output_path=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
