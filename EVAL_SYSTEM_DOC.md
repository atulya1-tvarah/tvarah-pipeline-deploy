# Eval System Doc

## Purpose

This evaluation framework is designed to play the role that tools like Galileo or Braintrust play for LLM and ML systems:

- protect model quality during iteration
- make regressions visible before release
- compare experiments against baselines
- identify failure slices instead of only looking at one average score

The goal is not only to score the system, but to make improvements safer and faster.

## What This Framework Evaluates

The evaluator runs the real `analyze_resume()` pipeline on one file or a dataset folder and captures:

- prediction outputs
- score consistency
- LLM usage success
- expectation matching
- classification quality for role family, band, and DNA
- score error for total score
- failure slices and top failing cases
- regression against a previous report

## Core Concepts

### 1. Dataset

A dataset is:

- one resume JSON file
- or a folder of resume JSON files

Optional expectation manifests can be attached to the dataset so the evaluator knows what "good" looks like.

### 2. Experiment Run

Every eval execution is treated as a run with:

- `run_id`
- `run_label`
- `dataset_name`
- timestamp

This gives the framework experiment-tracking behavior similar to Braintrust-style runs.

### 3. Expectations

Expectations are optional ground-truth-like targets such as:

- expected role family
- expected band
- expected DNA
- expected total score
- score tolerance
- min score
- max score
- tags for slicing

This lets the system measure not only internal stability, but business relevance.

### 4. Baseline Comparison

The framework can compare a new run against a previous report.

This gives:

- metric deltas
- gate results
- explicit regressions

This is the protection layer that stops silent quality drops.

## Metrics Included

### System Reliability Metrics

- `llm_score_success_rate`
- `llm_skill_success_rate`
- `score_consistency_rate`
- `cases_with_missing_component_justifications`
- `cases_with_missing_component_rationales`

These tell us whether the pipeline is reliable and explainable.

### Business-Facing Metrics

- `expectation_match_rate`
- `role_family_match_rate`
- `band_match_rate`
- `dna_match_rate`
- `score_mae`

These tell us whether the outputs are directionally correct and recruiter-usable.

### Slice Metrics

The framework creates slices by tags such as:

- role family
- score band
- LLM applied vs fallback
- score consistency
- user-provided expectation tags

This is important because regressions often hide inside a segment even when headline averages look fine.

## Regression Gates

Default gates are included for important metrics such as:

- LLM success rate
- score consistency
- expectation match rate
- role family match rate
- band match rate
- score MAE

Each gate can enforce:

- minimum acceptable value
- maximum acceptable value
- maximum allowed negative delta vs baseline
- maximum allowed positive delta where lower is better

This is the mechanism that makes the framework protective rather than only descriptive.

## Report Structure

Each run produces a JSON report containing:

- `run`
- `dataset`
- `summary`
- `quality_metrics`
- `slices`
- `regression`
- `top_failures`
- `cases`

### Why This Matters

- `summary` gives the topline health of the system
- `quality_metrics` gives per-target evaluation details
- `slices` shows hidden weak segments
- `regression` tells us what got worse
- `top_failures` gives direct debugging targets
- `cases` gives full per-resume traceability

## Example Commands

### Basic eval run

```powershell
.\.venv\Scripts\python.exe eval_framework.py .\single_resume_output --dataset-name "local-smoke"
```

### Eval with expectations

```powershell
.\.venv\Scripts\python.exe eval_framework.py .\single_resume_output --expectations .\eval_expectations.json --dataset-name "golden-set"
```

### Eval with baseline regression comparison

```powershell
.\.venv\Scripts\python.exe eval_framework.py .\single_resume_output --expectations .\eval_expectations.json --baseline-report .\eval_runs\previous-run\report.json --dataset-name "golden-set" --run-label "prompt-v2"
```

## Example Expectation Manifest

```json
{
  "dataset_name": "golden-set",
  "cases": [
    {
      "case_id": "_RajatSachdeva[7y_0m]",
      "expected_role_family": "AI_ARCHITECT",
      "expected_band": "GOOD",
      "expected_dna": "HYBRID",
      "min_score": 60,
      "max_score": 75,
      "tags": ["consulting", "mid-career", "genai"]
    }
  ]
}
```

## Recommended Usage Pattern

### Before a change

Run eval on:

- a small smoke dataset
- a golden recruiter-reviewed dataset

Store the generated report.

### After a change

Run the new eval with:

- the same dataset
- the same expectations
- the previous report as baseline

### Release decision

Release only if:

- gates pass
- no important regression appears
- top failures are understood

## How This Helps Improve The Model

This framework helps improvement in four ways:

### 1. Faster iteration

Prompt, scoring, and model changes can be compared immediately.

### 2. Safer releases

A new version is not accepted only because it "looks better" on one resume.

### 3. Better debugging

Instead of generic "quality dropped", the framework shows:

- which cases failed
- which labels drifted
- which slices regressed

### 4. Better collaboration

Product, recruiting, and ML stakeholders can look at the same report and discuss the same evidence.

## Positioning

This is a local, repo-native evaluation system inspired by Galileo and Braintrust ideas:

- experiment runs
- structured metrics
- regression checks
- failure analysis
- dataset-driven evaluation

It is intentionally lightweight so it can run inside the repo without requiring a separate SaaS layer.

## Next Extensions

Recommended future upgrades:

- UI page for eval runs and regression history
- support for reviewer feedback ingestion as eval labels
- per-skill depth accuracy and calibration metrics
- prompt version tracking
- experiment leaderboard across runs
- pass/fail CI integration

## Summary

This framework turns evaluation from a one-off report into a repeatable quality protection system. That is the main shift:

- from manual inspection
- to tracked experiments
- to regression-aware model improvement
