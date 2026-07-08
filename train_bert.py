from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


TASK_FAST_DEFAULTS: dict[str, dict[str, Any]] = {
    "role_family": {
        "epochs": 3,
        "batch_size": 16,
        "max_length": 128,
        "max_examples_per_label": 120,
        "upsample_target_per_label": 80,
    },
    "dna_fit": {
        "epochs": 3,
        "batch_size": 16,
        "max_length": 128,
        "max_examples_per_label": 180,
        "upsample_target_per_label": 120,
    },
    "project_type": {
        "epochs": 1,
        "batch_size": 16,
        "max_length": 128,
        "max_examples_per_label": 900,
        "upsample_target_per_label": 0,
    },
    "skill_depth": {
        "epochs": 3,
        "batch_size": 16,
        "max_length": 128,
        "max_examples_per_label": 400,
        "upsample_target_per_label": 200,
    },
    "career_progression": {
        "epochs": 3,
        "batch_size": 16,
        "max_length": 192,
        "max_examples_per_label": 120,
        "upsample_target_per_label": 80,
    },
    "stakeholder_management": {
        "epochs": 3,
        "batch_size": 16,
        "max_length": 192,
        "max_examples_per_label": 120,
        "upsample_target_per_label": 80,
    },
    "mentorship_signal": {
        "epochs": 3,
        "batch_size": 16,
        "max_length": 192,
        "max_examples_per_label": 120,
        "upsample_target_per_label": 80,
    },
}


def _ensure_training_dependencies() -> None:
    missing: list[str] = []
    try:
        import torch  # noqa: F401
    except Exception:
        missing.append("torch")
    try:
        import transformers  # noqa: F401
    except Exception:
        missing.append("transformers")
    if missing:
        package_list = " ".join(missing)
        raise RuntimeError(
            "Missing training dependencies: "
            f"{', '.join(missing)}. Install them first with "
            f"`.\\.venv\\Scripts\\pip.exe install -r requirements-ml.txt` "
            f"or at minimum `.\\.venv\\Scripts\\pip.exe install {package_list}`."
        )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _load_gold_labels(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    overrides: dict[tuple[str, str], str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if str(row.get("review_status") or "").lower() not in {"approved", "gold", "confirmed"}:
                continue
            task = str(row.get("task") or "").strip()
            record_id = str(row.get("record_id") or "").strip()
            gold_label = str(row.get("gold_label") or "").strip()
            if task and record_id and gold_label:
                overrides[(task, record_id)] = gold_label
    return overrides


def _task_file(task: str) -> str:
    mapping = {
        "role_family": "role_family.jsonl",
        "dna_fit": "dna_fit.jsonl",
        "project_type": "project_type.jsonl",
        "skill_depth": "skill_depth.jsonl",
        "career_progression": "career_progression.jsonl",
        "stakeholder_management": "stakeholder_management.jsonl",
        "mentorship_signal": "mentorship_signal.jsonl",
    }
    if task not in mapping:
        raise ValueError(f"Unsupported task: {task}")
    return mapping[task]


def _extract_text(row: dict[str, Any], task: str) -> str:
    if task == "project_type":
        return str(row.get("classifier_text") or row.get("text") or row.get("resume_text") or "")
    if task == "skill_depth":
        return str(row.get("classifier_text") or row.get("resume_text") or "")
    return str(row.get("classifier_text") or row.get("resume_text") or "")


def _extract_label(row: dict[str, Any], task: str) -> str | None:
    label = row.get("label")
    if isinstance(label, dict):
        if task == "skill_depth":
            depth_label = str(label.get("depth_label") or "").strip().upper()
            if not depth_label:
                return None
            return depth_label
        return None
    if label in (None, "", {}):
        return None
    label_text = str(label).strip()
    if not label_text:
        return None
    return label_text


def _normalize_skill_depth_label(label_text: str, collapse_skill_depth_labels: bool) -> str | None:
    normalized = str(label_text or "").strip().upper()
    if not normalized:
        return None
    # AWARENESS is now a valid class — do not drop it
    if collapse_skill_depth_labels and normalized == "FOUNDATIONAL":
        return "HANDS_ON"
    return normalized


def _apply_gold_label_overrides(rows: list[dict[str, Any]], task: str, gold_overrides: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    if not gold_overrides:
        return rows
    updated: list[dict[str, Any]] = []
    for row in rows:
        record_id = str(row.get("record_id") or row.get("resume_id") or "").strip()
        gold = gold_overrides.get((task, record_id))
        if gold:
            patched = dict(row)
            patched["label"] = gold
            patched["label_source"] = "gold_override"
            updated.append(patched)
        else:
            updated.append(row)
    return updated


def _prepare_examples(
    rows: list[dict[str, Any]],
    task: str,
    collapse_skill_depth_labels: bool,
) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for row in rows:
        text = _extract_text(row, task).strip()
        label = _extract_label(row, task)
        if task == "skill_depth" and label:
            label = _normalize_skill_depth_label(label, collapse_skill_depth_labels)
        if not text or not label:
            continue
        examples.append(
            {
                "text": text,
                "label": label,
                "record_id": str(row.get("record_id") or row.get("resume_id") or ""),
                "source_file": str(row.get("source_file") or ""),
            }
        )
    return examples


def _minimum_examples_per_label(task: str) -> int:
    if task == "role_family":
        return 3
    if task == "skill_depth":
        return 2
    return 1


def _filter_rare_labels(examples: list[dict[str, Any]], task: str) -> list[dict[str, Any]]:
    minimum = _minimum_examples_per_label(task)
    if minimum <= 1:
        return examples
    counts: dict[str, int] = {}
    for example in examples:
        counts[example["label"]] = counts.get(example["label"], 0) + 1
    return [example for example in examples if counts.get(example["label"], 0) >= minimum]


def _split_examples(examples: list[dict[str, Any]], validation_ratio: float, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(examples) < 5:
        return examples, []
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for example in examples:
        buckets.setdefault(example["label"], []).append(example)
    training: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for bucket in buckets.values():
        shuffled = bucket[:]
        rng.shuffle(shuffled)
        if len(shuffled) == 1:
            training.extend(shuffled)
            continue
        validation_size = max(1, int(round(len(shuffled) * validation_ratio)))
        if validation_size >= len(shuffled):
            validation_size = len(shuffled) - 1
        validation.extend(shuffled[:validation_size])
        training.extend(shuffled[validation_size:])
    rng.shuffle(training)
    rng.shuffle(validation)
    return training, validation


def _label_maps(examples: list[dict[str, Any]]) -> tuple[dict[str, int], dict[int, str]]:
    labels = sorted({example["label"] for example in examples})
    label2id = {label: index for index, label in enumerate(labels)}
    id2label = {index: label for label, index in label2id.items()}
    return label2id, id2label


def _class_weights(train_examples: list[dict[str, Any]], label2id: dict[str, int]) -> list[float]:
    counts = {label: 0 for label in label2id}
    for example in train_examples:
        counts[example["label"]] += 1
    total = sum(counts.values())
    num_classes = max(len(counts), 1)
    weights: list[float] = []
    for label, index in sorted(label2id.items(), key=lambda item: item[1]):
        count = max(counts[label], 1)
        raw_weight = (total / (num_classes * count)) ** 0.5
        weights.append(min(2.5, max(0.5, raw_weight)))
    return weights


def _rebalance_examples(
    examples: list[dict[str, Any]],
    max_examples_per_label: int,
    upsample_target_per_label: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not examples:
        return examples, {"applied": False}
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for example in examples:
        buckets.setdefault(example["label"], []).append(example)

    rebalanced: list[dict[str, Any]] = []
    original_counts = {label: len(items) for label, items in buckets.items()}
    sampled_counts: dict[str, int] = {}

    for label, items in sorted(buckets.items()):
        shuffled = items[:]
        rng.shuffle(shuffled)
        limited = shuffled[:max_examples_per_label] if max_examples_per_label > 0 else shuffled
        if upsample_target_per_label > 0 and limited:
            while len(limited) < upsample_target_per_label:
                limited.append(rng.choice(limited))
        sampled_counts[label] = len(limited)
        rebalanced.extend(limited)

    rng.shuffle(rebalanced)
    return rebalanced, {
        "applied": True,
        "max_examples_per_label": max_examples_per_label,
        "upsample_target_per_label": upsample_target_per_label,
        "original_counts": original_counts,
        "sampled_counts": sampled_counts,
    }


def _resolve_training_defaults(
    task: str,
    preset: str,
    epochs: int | None,
    batch_size: int | None,
    max_length: int | None,
    max_examples_per_label: int | None,
    upsample_target_per_label: int | None,
) -> dict[str, int]:
    defaults = TASK_FAST_DEFAULTS.get(task, {}) if preset == "cpu_fast" else {}
    return {
        "epochs": int(epochs if epochs is not None else defaults.get("epochs", 3)),
        "batch_size": int(batch_size if batch_size is not None else defaults.get("batch_size", 4)),
        "max_length": int(max_length if max_length is not None else defaults.get("max_length", 768)),
        "max_examples_per_label": int(
            max_examples_per_label if max_examples_per_label is not None else defaults.get("max_examples_per_label", 0)
        ),
        "upsample_target_per_label": int(
            upsample_target_per_label
            if upsample_target_per_label is not None
            else defaults.get("upsample_target_per_label", 0)
        ),
    }


def _accuracy(predictions: np.ndarray, labels: np.ndarray) -> float:
    if len(labels) == 0:
        return 0.0
    return float((predictions == labels).mean())


def _macro_f1(predictions: np.ndarray, labels: np.ndarray) -> float:
    unique_labels = sorted(set(labels.tolist()))
    if not unique_labels:
        return 0.0
    scores: list[float] = []
    for label in unique_labels:
        tp = int(((predictions == label) & (labels == label)).sum())
        fp = int(((predictions == label) & (labels != label)).sum())
        fn = int(((predictions != label) & (labels == label)).sum())
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        if precision + recall == 0:
            scores.append(0.0)
        else:
            scores.append(2 * precision * recall / (precision + recall))
    return float(sum(scores) / len(scores))


def train_task(
    data_dir: str,
    task: str,
    output_dir: str,
    model_name: str,
    epochs: int | None,
    learning_rate: float,
    batch_size: int | None,
    max_length: int | None,
    validation_ratio: float,
    seed: int,
    gold_labels_file: str | None,
    preset: str,
    rebalance: bool,
    max_examples_per_label: int | None,
    upsample_target_per_label: int | None,
    collapse_skill_depth_labels: bool,
) -> dict[str, Any]:
    _ensure_training_dependencies()

    import torch
    from torch.utils.data import Dataset
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
    )

    data_path = Path(data_dir) / _task_file(task)
    rows = _load_jsonl(data_path)
    gold_overrides = _load_gold_labels(Path(gold_labels_file)) if gold_labels_file else {}
    rows = _apply_gold_label_overrides(rows, task, gold_overrides)
    resolved_defaults = _resolve_training_defaults(
        task=task,
        preset=preset,
        epochs=epochs,
        batch_size=batch_size,
        max_length=max_length,
        max_examples_per_label=max_examples_per_label,
        upsample_target_per_label=upsample_target_per_label,
    )
    epochs = resolved_defaults["epochs"]
    batch_size = resolved_defaults["batch_size"]
    max_length = resolved_defaults["max_length"]

    examples = _prepare_examples(rows, task, collapse_skill_depth_labels)
    examples = _filter_rare_labels(examples, task)
    if len(examples) < 2:
        raise RuntimeError(f"Not enough usable examples found for task '{task}' in {data_path}")

    train_examples, validation_examples = _split_examples(examples, validation_ratio, seed)
    rebalance_summary = {"applied": False}
    if rebalance:
        train_examples, rebalance_summary = _rebalance_examples(
            train_examples,
            max_examples_per_label=resolved_defaults["max_examples_per_label"],
            upsample_target_per_label=resolved_defaults["upsample_target_per_label"],
            seed=seed,
        )
    label2id, id2label = _label_maps(examples)

    tokenizer = AutoTokenizer.from_pretrained(model_name)

    class JsonlTextDataset(Dataset):
        def __init__(self, examples: list[dict[str, Any]]):
            self.examples = examples

        def __len__(self) -> int:
            return len(self.examples)

        def __getitem__(self, index: int) -> dict[str, Any]:
            example = self.examples[index]
            encoded = tokenizer(
                example["text"],
                truncation=True,
                padding="max_length",
                max_length=max_length,
                return_tensors="pt",
            )
            return {
                "input_ids": encoded["input_ids"].squeeze(0),
                "attention_mask": encoded["attention_mask"].squeeze(0),
                "labels": torch.tensor(label2id[example["label"]], dtype=torch.long),
            }

    train_dataset = JsonlTextDataset(train_examples)
    eval_dataset = JsonlTextDataset(validation_examples) if validation_examples else None

    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    )
    class_weight_values = _class_weights(train_examples, label2id)
    class_weight_tensor = torch.tensor(class_weight_values, dtype=torch.float)

    task_output_dir = Path(output_dir) / task
    task_output_dir.mkdir(parents=True, exist_ok=True)

    def compute_metrics(eval_pred: Any) -> dict[str, float]:
        logits, labels = eval_pred
        predictions = np.argmax(logits, axis=-1)
        return {
            "accuracy": _accuracy(predictions, labels),
            "macro_f1": _macro_f1(predictions, labels),
        }

    training_args = TrainingArguments(
        output_dir=str(task_output_dir),
        learning_rate=learning_rate,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        num_train_epochs=epochs,
        weight_decay=0.01,
        eval_strategy="epoch" if eval_dataset else "no",
        save_strategy="epoch" if eval_dataset else "no",
        logging_steps=max(1, len(train_examples) // max(1, batch_size)),
        load_best_model_at_end=bool(eval_dataset),
        metric_for_best_model="macro_f1" if eval_dataset else None,
        greater_is_better=True if eval_dataset else None,
        report_to="none",
        seed=seed,
    )

    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.get("labels")
            outputs = model(
                input_ids=inputs.get("input_ids"),
                attention_mask=inputs.get("attention_mask"),
            )
            logits = outputs.get("logits")
            loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight_tensor.to(logits.device))
            loss = loss_fn(logits, labels)
            return (loss, outputs) if return_outputs else loss

    trainer = WeightedTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        compute_metrics=compute_metrics if eval_dataset else None,
        processing_class=tokenizer,
    )

    trainer.train()
    trainer.save_model(str(task_output_dir))
    tokenizer.save_pretrained(str(task_output_dir))

    metrics = {}
    if eval_dataset:
        metrics = trainer.evaluate()

    metadata = {
        "task": task,
        "model_name": model_name,
        "input_file": str(data_path),
        "train_examples": len(train_examples),
        "validation_examples": len(validation_examples),
        "labels": label2id,
        "metrics": metrics,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "batch_size": batch_size,
        "max_length": max_length,
        "seed": seed,
        "preset": preset,
        "rebalance": rebalance_summary,
        "collapse_skill_depth_labels": collapse_skill_depth_labels if task == "skill_depth" else False,
        "gold_overrides_applied": sum(1 for key in gold_overrides if key[0] == task),
    }
    (task_output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train a first-pass ModernBERT/BERT classifier on the JSONL training exports built from extracted resume JSON."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory containing role_family.jsonl, dna_fit.jsonl, and project_type.jsonl exports.",
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=["role_family", "dna_fit", "project_type", "skill_depth",
                 "career_progression", "stakeholder_management", "mentorship_signal"],
        help="Which classification task to train.",
    )
    parser.add_argument(
        "--output-dir",
        default="trained_models",
        help="Directory where the trained model should be saved.",
    )
    parser.add_argument(
        "--model-name",
        default="answerdotai/ModernBERT-base",
        help="Hugging Face model checkpoint to fine-tune.",
    )
    parser.add_argument("--preset", choices=["standard", "cpu_fast"], default="cpu_fast")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--max-length", type=int, default=None)
    parser.add_argument("--validation-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rebalance", dest="rebalance", action="store_true")
    parser.add_argument("--no-rebalance", dest="rebalance", action="store_false")
    parser.set_defaults(rebalance=True)
    parser.add_argument("--max-examples-per-label", type=int, default=None)
    parser.add_argument("--upsample-target-per-label", type=int, default=None)
    parser.add_argument("--collapse-skill-depth-labels", dest="collapse_skill_depth_labels", action="store_true")
    parser.add_argument("--keep-skill-depth-labels", dest="collapse_skill_depth_labels", action="store_false")
    parser.set_defaults(collapse_skill_depth_labels=True)
    parser.add_argument(
        "--gold-labels-file",
        default="feedback_data/gold_labels.jsonl",
        help="Optional reviewed gold-label file. Only rows with review_status=approved/gold/confirmed are applied.",
    )
    args = parser.parse_args()

    result = train_task(
        data_dir=args.data_dir,
        task=args.task,
        output_dir=args.output_dir,
        model_name=args.model_name,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        max_length=args.max_length,
        validation_ratio=args.validation_ratio,
        seed=args.seed,
        gold_labels_file=args.gold_labels_file,
        preset=args.preset,
        rebalance=args.rebalance,
        max_examples_per_label=args.max_examples_per_label,
        upsample_target_per_label=args.upsample_target_per_label,
        collapse_skill_depth_labels=args.collapse_skill_depth_labels,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
