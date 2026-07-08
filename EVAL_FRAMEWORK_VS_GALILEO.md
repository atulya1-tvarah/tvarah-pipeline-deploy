# Eval Framework Vs Galileo

## Purpose

This document explains:

- how the local evaluation framework works in this repository
- what problems it is solving
- how close it is to a platform like Galileo
- what is still missing if we want to become more Galileo-like

This is written as an operator guide, not only as a technical note.

## The Problem We Are Solving

When we change:

- prompts
- scoring rules
- skill reasoning
- BERT models
- fallback logic
- UI-to-score mapping

we need a reliable way to answer:

- did the system improve?
- did anything regress?
- which resumes got worse?
- which slices got worse?
- can we release safely?

Without an eval framework, teams end up checking one or two resumes manually and hoping the overall system improved. That is risky.

## What Our Eval Framework Does

Our local eval framework turns evaluation into a repeatable experiment workflow.

It does this by running the real `analyze_resume()` pipeline on one file or many files and then producing a structured report.

The framework currently supports:

- dataset-based evaluation
- expectation-based checking
- experiment-style run metadata
- summary metrics
- slice metrics
- baseline comparison
- regression gates
- top failure capture
- per-case traceability

In simple words:

- input = resume dataset plus optional expectations
- process = run the full analysis engine
- output = report showing quality, failures, and regressions

## How The Eval Flow Works

### Step 1. Choose a dataset

The dataset can be:

- one JSON resume
- a folder of many JSON resumes

Optional expectation data can be attached to the dataset.

Examples of expectation data:

- expected role family
- expected band
- expected DNA
- expected total score
- min and max acceptable score
- tags like consulting, data-engineering, genai, mid-career

### Step 2. Run the real analysis pipeline

For each case, the evaluator runs the same production analysis flow:

- normalize resume
- build skill evidence
- infer semantic taxonomy
- infer experience and DNA
- compute scorecard
- collect LLM status
- produce final analysis output

This is important because we are evaluating the actual system, not a simplified mock.

### Step 3. Create a per-case record

For every resume, the framework stores:

- candidate name
- file path
- predicted role, band, DNA, and total score
- expectation targets
- expectation match result
- score consistency
- missing justifications
- missing rationales
- failure reasons
- tags for slicing

This is the "debuggable evidence layer" of the framework.

### Step 4. Build summary metrics

The framework computes top-level metrics such as:

- LLM score success rate
- LLM skill success rate
- score consistency rate
- average total score
- expectation match rate
- role family match rate
- band match rate
- DNA match rate
- score MAE

This gives the one-page health summary for a run.

### Step 5. Build slices

The framework groups cases into slices using tags such as:

- role family
- score band
- LLM applied vs fallback
- score consistency
- user-provided business tags

This matters because overall averages can hide problems inside a segment.

Example:

- overall quality may look stable
- but consulting resumes may have regressed badly

Slice metrics help us catch that.

### Step 6. Compare to a baseline

If an older report is provided, the framework compares the new run to that previous run.

It computes:

- metric deltas
- gate results
- regressions

This is the protection layer.

Instead of asking "does this look okay?", we ask:

- is this better than the last accepted run?
- did any critical metric fall below threshold?

### Step 7. Surface top failures

The framework extracts the most important failing cases, including:

- mismatched expectations
- missing score justifications
- inconsistent totals
- fallback-heavy failures

This gives the team a practical bug-fixing queue.

## The Main Files Involved

### `eval_framework.py`

This is the engine that:

- runs the dataset
- computes metrics
- computes slices
- compares to baseline
- writes the eval report

### `EVAL_SYSTEM_DOC.md`

This is the conceptual system note that explains:

- why the eval exists
- what metrics it uses
- how the regression workflow works

### `engine.py`

This is the actual analysis pipeline being evaluated.

That is why the eval results are meaningful for production behavior.

## How Close We Are To Galileo

Galileo describes itself as an AI observability and eval engineering platform where offline evals become production guardrails, with dataset capture, evaluator building, failure analysis, and production monitoring. Source: [galileo.ai](https://galileo.ai/).

### Where We Already Match Galileo Concepts

We are already aligned on these ideas:

#### 1. Dataset-first evaluation

Galileo emphasizes building datasets from development, synthetic, and production data.

We already support:

- local datasets of resume JSON files
- expectation manifests
- tagged cases

This is the same core eval mindset.

#### 2. Offline experiment runs

Galileo treats eval as an engineering workflow, not a one-time manual test.

We now do the same through:

- run metadata
- dataset naming
- run labels
- saved JSON reports

#### 3. Regression protection

Galileo focuses on turning measurement into safe iteration.

Our framework now does that using:

- baseline comparison
- metric deltas
- gate checks
- regression lists

#### 4. Failure analysis

Galileo highlights failure modes, hidden patterns, and debugging insights.

We now support:

- top failures
- slice metrics
- missing rationale checks
- LLM fallback visibility

#### 5. Custom business evals

Galileo supports custom evaluators based on domain logic.

Our expectation manifest is an early version of that idea because we can encode:

- expected role family
- score constraints
- business labels
- mandatory LLM usage

## Where We Are Still Behind Galileo

This is the honest gap analysis.

### 1. No production observability yet

Galileo supports live monitoring and production data workflows.

We do not yet have:

- live traffic ingestion
- real-time tracing
- online dashboards for production drift
- alerting

Today our framework is offline and batch-oriented.

### 2. No annotation workflow yet

Galileo supports subject matter expert feedback as a living asset.

We do not yet have:

- a reviewer UI for annotations
- approval queues tied directly into eval datasets
- automatic dataset updates from recruiter feedback

### 3. No eval UI yet

Galileo has a product surface for:

- browsing runs
- comparing experiments
- drilling into failures

Our current output is JSON-first.

It is powerful, but not yet analyst-friendly.

### 4. No production guardrail execution

Galileo talks about using evals as guardrails.

We do not yet do:

- blocking or routing based on eval results
- automatic escalations
- runtime policy decisions from eval thresholds

### 5. No model distillation or low-cost judge deployment

Galileo talks about turning expensive eval logic into cheaper models.

We do not yet have:

- distilled evaluator models
- judge optimization loops
- evaluator model serving at runtime

## Practical Scorecard: How Close Are We?

If Galileo is treated as a full eval + observability platform, then our current framework is:

- strong on offline eval structure
- moderate on regression protection
- early on dataset operations
- weak on observability
- weak on production guardrails
- weak on user-facing eval UI

In short:

- we are meaningfully aligned on eval engineering
- we are not yet a full Galileo-like platform

## What We Can Reliably Say Today

We can confidently say that our framework is:

- Galileo-inspired
- dataset-driven
- experiment-oriented
- regression-aware
- suitable for protecting model and prompt changes before release

We should not yet say that it fully matches Galileo in:

- observability
- live monitoring
- guardrails
- enterprise eval operations

## Recommended Roadmap To Become More Galileo-Like

### Phase 1. Golden dataset and stronger expectations

Add:

- recruiter-reviewed gold labels
- tagged benchmark sets
- stronger expected-score and role checks

This improves trust in the offline eval layer.

### Phase 2. Eval run UI

Add:

- run history page
- baseline comparison page
- leaderboard of runs
- slice explorer
- top-failure explorer

This makes the framework much easier for non-engineers to use.

### Phase 3. Feedback ingestion

Add:

- recruiter corrections
- interview outcomes
- final hiring decision
- label review workflows

This turns eval into a living system rather than static JSON files.

### Phase 4. Release gates

Add:

- CI checks
- pass/fail thresholds
- block deploy if regressions occur

This is where eval starts actively protecting production.

### Phase 5. Production observability

Add:

- live scoring traces
- score drift monitoring
- LLM fallback rate monitoring
- error trend dashboards

This is the biggest step toward a real Galileo-like operating model.

## Example Usage Pattern

### Before making a change

Run:

```powershell
.\.venv\Scripts\python.exe eval_framework.py .\single_resume_output --expectations .\eval_expectations.json --dataset-name "golden-set" --run-label "baseline"
```

Save the resulting report.

### After changing prompts or models

Run:

```powershell
.\.venv\Scripts\python.exe eval_framework.py .\single_resume_output --expectations .\eval_expectations.json --baseline-report .\eval_runs\baseline-report.json --dataset-name "golden-set" --run-label "candidate-v2"
```

Then inspect:

- summary metrics
- regressions
- top failures
- slices

### Release decision

Release only if:

- important metrics pass
- no serious regression is introduced
- top failures are understood

## Summary

Our eval framework already does the most important first job:

- it protects model quality from silent regressions
- it creates a repeatable experiment workflow
- it gives structured evidence for improvement decisions

Compared to Galileo:

- we are already on the right eval engineering path
- we still need UI, feedback loops, observability, and guardrails to get truly close
