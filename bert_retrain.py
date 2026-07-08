"""
bert_retrain.py — One-command BERT retrain pipeline.

Usage:
    python bert_retrain.py --resume path/to/resume.json [path/to/resume2.json ...]
    python bert_retrain.py --resume-dir path/to/resume_folder/
    python bert_retrain.py --from-eval-runs          # uses existing eval_runs/ analysis output
    python bert_retrain.py --tasks skill_depth role_family dna_fit project_type
    python bert_retrain.py --resume ... --dry-run     # show counts, don't train

Steps performed:
  1. For each resume JSON: run analyze_resume() -> get full analysis output
  2. Feed all analyses to training_data_builder.build_training_exports()
  3. Print label distribution and warn if any class has <20 examples
  4. Run train_bert.train_task() for each requested task
  5. Print final validation accuracy + macro F1 per task
  6. Write training_report.json in the output dir
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_resume_files(paths: list[str], resume_dir: str | None) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        fp = Path(p)
        if fp.is_file():
            files.append(fp)
        elif fp.is_dir():
            files.extend(sorted(fp.rglob("*.json")))
    if resume_dir:
        d = Path(resume_dir)
        if d.is_dir():
            files.extend(sorted(d.rglob("*.json")))
    seen: set[str] = set()
    unique: list[Path] = []
    for f in files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle multiple extractor formats:
    - {resume_data: {...}, judge_results: ...}  -> unwrap resume_data
    - {data: {...}}                              -> standard app format
    - {experience: [...], skills: [...], ...}   -> direct normalized format
    """
    if "resume_data" in payload and isinstance(payload["resume_data"], dict):
        return payload["resume_data"]
    return payload


def _disable_llm_after_import() -> None:
    """Call after importing engine/llm_client to override dotenv's load_dotenv(override=True).
    Also sets BERT_TRAINING_MODE=1 so scoring_engine uses heuristic fallback instead of LLM."""
    import os
    os.environ["MISTRAL_API_KEY"] = ""
    os.environ["OPENROUTER_API_KEY"] = ""
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["BERT_TRAINING_MODE"] = "1"


def _analyze_resume_file(path: Path) -> dict[str, Any] | None:
    """Run analyze_resume() on a JSON file. Returns None on failure."""
    try:
        from engine import analyze_resume
        from models import ResumeInput
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = _unwrap_payload(payload)
        resume_input = ResumeInput.from_any(payload)
        result = analyze_resume(resume_input)
        result["_source_file"] = str(path)
        return result
    except Exception as exc:
        print(f"  [WARN] Failed to analyze {path.name}: {exc}")
        return None


def _collect_from_eval_runs(eval_dir: Path) -> list[dict[str, Any]]:
    """Load already-analyzed results from eval_runs/ JSONL files."""
    results: list[dict[str, Any]] = []
    for f in sorted(eval_dir.rglob("*.json")):
        try:
            data = _load_json(f)
            # eval_run files have "report" key with "cases" list
            if "cases" in data:
                for case in data["cases"]:
                    snap = case.get("analysis_snapshot", {})
                    if snap:
                        results.append(snap)
            elif "candidate_overview" in data:
                results.append(data)
        except Exception:
            pass
    return results


def _write_resumes_to_dir(resume_jsons: list[dict[str, Any]], out_dir: Path) -> list[Path]:
    """Write unwrapped resume JSONs to a dir so training_data_builder can re-analyse them.
    Each dict should be a normalizable resume payload (NOT an analysis output).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, resume in enumerate(resume_jsons):
        name = (
            (resume.get("personal_info") or {}).get("full_name")
            or (resume.get("personal_info") or {}).get("name")
            or f"resume_{i}"
        )
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name))
        out_path = out_dir / f"{safe}_{i}.json"
        out_path.write_text(json.dumps(resume, ensure_ascii=False), encoding="utf-8")
        written.append(out_path)
    return written


def _print_label_distribution(jsonl_path: Path, task: str) -> dict[str, int]:
    if not jsonl_path.exists():
        print(f"  [WARN] No training file at {jsonl_path}")
        return {}
    from collections import Counter
    counts: Counter = Counter()
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            label = row.get("label")
            if isinstance(label, dict):
                label = label.get("depth_label") or label.get("label")
            if label:
                counts[str(label)] += 1
    print(f"\n  [{task}] Label distribution ({sum(counts.values())} total examples):")
    for label, count in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "|" * min(40, count // 2)
        flag = "  <-- WARN low" if count < 20 else ""
        print(f"    {label:<30} {count:>4}  {bar}{flag}", flush=True)
    return dict(counts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="BERT retrain pipeline for Resume Intelligence.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", nargs="+", metavar="FILE", help="One or more analyzed resume JSON files")
    group.add_argument("--resume-dir", metavar="DIR", help="Directory of resume JSON files")
    group.add_argument("--from-eval-runs", action="store_true", help="Use existing eval_runs/ analysis output")
    parser.add_argument("--tasks", nargs="+",
                        choices=["skill_depth", "role_family", "dna_fit", "project_type",
                                 "career_progression", "stakeholder_management", "mentorship_signal"],
                        default=["skill_depth", "role_family", "dna_fit",
                                 "career_progression", "stakeholder_management", "mentorship_signal"],
                        help="Which BERT tasks to train (default: all 6 primary tasks)")
    parser.add_argument("--model-name", default="answerdotai/ModernBERT-base",
                        help="HuggingFace base model to fine-tune")
    parser.add_argument("--output-dir", default="trained_models_v3",
                        help="Where to save trained models")
    parser.add_argument("--training-data-dir", default="training_data",
                        help="Where to write JSONL training exports")
    parser.add_argument("--epochs", type=int, default=None, help="Override epochs (default: task preset)")
    parser.add_argument("--lr", type=float, default=2e-5, help="Learning rate")
    parser.add_argument("--dry-run", action="store_true", help="Build training data but don't train")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Skip analysis step — treat input files as already-analyzed JSON")
    parser.add_argument("--gold-labels", default="feedback_data/gold_labels.jsonl",
                        help="Optional gold label override file")
    parser.add_argument("--no-llm", action="store_true", default=True,
                        help="Disable LLM calls during analysis (default: True, much faster)")
    parser.add_argument("--with-llm", action="store_true",
                        help="Enable LLM calls during analysis (slow, ~120s/resume)")
    args = parser.parse_args()

    # Pre-import engine so load_dotenv(override=True) fires, then kill API keys
    # to avoid slow Mistral/OpenRouter calls during training data generation
    if not args.with_llm:
        import sys as _sys
        print("  [info] Pre-loading engine modules to neutralise dotenv...", flush=True)
        try:
            import engine as _eng  # noqa: F401 — triggers load_dotenv(override=True)
        except Exception:
            pass
        _disable_llm_after_import()
        print("  [info] LLM disabled (use --with-llm to enable)", flush=True)

    training_data_dir = Path(args.training_data_dir)
    output_dir = Path(args.output_dir)

    # ── Step 1: Collect raw resume payloads ──────────────────────────────
    print("=" * 60)
    print("BERT Retrain Pipeline")
    print("=" * 60)

    # resume_payloads: unwrapped resume dicts (for training_data_builder's own analysis)
    # analyses: full engine analysis results (only used for --from-eval-runs)
    resume_payloads: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []

    if args.from_eval_runs:
        eval_dir = Path("eval_runs")
        print(f"\n[1/4] Loading analyses from {eval_dir}/")
        analyses = _collect_from_eval_runs(eval_dir)
        print(f"  Found {len(analyses)} analysis records in eval_runs/")
        # For eval_runs mode, extract the original resume_data if available
        for a in analyses:
            raw = a.get("_raw_resume") or {}
            resume_payloads.append(raw if raw else a)  # fallback: pass analysis as-is

    elif args.resume or args.resume_dir:
        files = _collect_resume_files(args.resume or [], args.resume_dir)
        print(f"\n[1/4] Found {len(files)} resume JSON files")
        if not files:
            print("  ERROR: No resume files found.")
            sys.exit(1)

        print("  Loading and unwrapping resume payloads…")
        for f in files:
            try:
                payload = json.loads(f.read_text(encoding="utf-8"))
                resume_payloads.append(_unwrap_payload(payload))
            except Exception as exc:
                print(f"  [WARN] {f.name}: {exc}")
        print(f"  Loaded {len(resume_payloads)} resume payloads")

    else:
        parser.print_help()
        sys.exit(0)

    if not resume_payloads:
        print("\nERROR: No resume payloads loaded. Check your input files.")
        sys.exit(1)

    print(f"\n  Total resumes ready: {len(resume_payloads)}")

    # ── Step 2: Write unwrapped resume JSONs for training_data_builder ───
    # training_data_builder re-runs its own normalization/evidence pipeline,
    # so we must give it the original resume payloads (NOT analysis outputs).
    print(f"\n[2/4] Writing resume payloads to {training_data_dir}/resumes/")
    resumes_dir = training_data_dir / "resumes"
    _write_resumes_to_dir(resume_payloads, resumes_dir)

    # ── Step 3: Build JSONL training exports ─────────────────────────────
    print(f"\n[3/4] Building training exports -> {training_data_dir}/")
    from training_data_builder import build_training_exports
    export_result = build_training_exports(
        source_paths=[str(resumes_dir)],
        output_dir=str(training_data_dir),
    )
    print(f"  Export done: {export_result.get('counts', {})}")

    # Print label distributions
    distributions: dict[str, dict[str, int]] = {}
    for task in args.tasks:
        task_file_map = {
            "skill_depth": "skill_depth.jsonl",
            "role_family": "role_family.jsonl",
            "dna_fit": "dna_fit.jsonl",
            "project_type": "project_type.jsonl",
            "career_progression": "career_progression.jsonl",
            "stakeholder_management": "stakeholder_management.jsonl",
            "mentorship_signal": "mentorship_signal.jsonl",
        }
        jsonl_path = training_data_dir / task_file_map[task]
        distributions[task] = _print_label_distribution(jsonl_path, task)

    if args.dry_run:
        print("\n[DRY RUN] Skipping training. Check label distributions above.")
        print(f"  Training data written to: {training_data_dir}/")
        return

    # ── Step 4: Train ─────────────────────────────────────────────────────
    print(f"\n[4/4] Training BERT models -> {output_dir}/")
    try:
        from train_bert import train_task
    except ImportError as exc:
        print(f"  ERROR: {exc}")
        print("  Install: .venv/Scripts/pip install -r requirements-ml.txt")
        sys.exit(1)

    gold_labels_file = args.gold_labels if Path(args.gold_labels).exists() else None
    all_metrics: dict[str, Any] = {}

    for task in args.tasks:
        dist = distributions.get(task, {})
        total = sum(dist.values())
        if total < 10:
            print(f"\n  [{task}] SKIPPED — only {total} examples (need ≥10)")
            continue

        print(f"\n  [{task}] Training on {total} examples…")
        t0 = time.time()
        try:
            result = train_task(
                data_dir=str(training_data_dir),
                task=task,
                output_dir=str(output_dir),
                model_name=args.model_name,
                epochs=args.epochs,
                learning_rate=args.lr,
                batch_size=None,
                max_length=None,
                validation_ratio=0.2,
                seed=42,
                gold_labels_file=gold_labels_file,
                preset="cpu_fast",
                rebalance=True,
                max_examples_per_label=None,
                upsample_target_per_label=None,
                collapse_skill_depth_labels=(task == "skill_depth"),
            )
            elapsed = time.time() - t0
            metrics = result.get("metrics", {})
            acc = metrics.get("eval_accuracy", metrics.get("eval_accuracy", "N/A"))
            f1 = metrics.get("eval_macro_f1", "N/A")
            print(f"  [{task}] Done in {elapsed:.0f}s | val_accuracy={acc} | macro_F1={f1}")
            print(f"  [{task}] Model saved -> {output_dir}/{task}/")
            all_metrics[task] = result
        except Exception as exc:
            print(f"  [{task}] FAILED: {exc}")
            all_metrics[task] = {"error": str(exc)}

    # ── Summary ───────────────────────────────────────────────────────────
    report_path = output_dir / "training_report.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "resume_count": len(resume_payloads),
        "tasks": args.tasks,
        "model_name": args.model_name,
        "distributions": distributions,
        "metrics": all_metrics,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print("Training complete.")
    print(f"  Models:  {output_dir}/")
    print(f"  Report:  {report_path}")
    print("\nTo activate BERT in the app:")
    print(f"  EVIDENCE_ENCODER_BACKEND=transformers ENABLE_NEW_RUBRIC=true uvicorn app:app --port 8000")
    print("=" * 60)


if __name__ == "__main__":
    main()
