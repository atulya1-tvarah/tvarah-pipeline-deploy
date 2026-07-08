# Eval Framework Full Documentation

## Purpose

This document explains the evaluation framework built for the Resume Intelligence system.

It covers:

- why the eval framework exists
- how it fits the resume analysis pipeline
- what it measures from an ML perspective
- what it measures from a product perspective
- how traces are represented today
- how experiment runs, baselines, and regressions work
- how close the framework is to Galileo
- what is still missing

This document is written to make the framework understandable for:

- engineering
- ML
- product
- recruiting stakeholders
- client conversations

## 1. Why We Built This Eval Framework

The resume analysis system is no longer a simple parser.

It now contains multiple decision layers:

- resume normalization
- skill evidence extraction
- semantic taxonomy inference
- experience analysis
- DNA fit analysis
- BERT priors
- LLM-based scoring and reasoning
- fallback logic when LLM steps fail
- recruiter-style output generation

Because of this, quality cannot be validated by checking one or two resumes manually.

Every change to:

- prompts
- fallback wording
- scoring logic
- BERT models
- taxonomy
- evidence extraction
- UI score presentation

can improve one part of the system while silently breaking another.

The eval framework exists to prevent that.

Its job is to make model and pipeline improvement safe, repeatable, and measurable.

## 2. What The Eval Framework Is

The eval framework is a local, repo-native evaluation system that runs the real resume analysis pipeline on one or more resume JSON files and creates a structured experiment report.

In practice, it does four things:

1. runs the actual production analysis flow
2. compares predictions to expected outcomes when those are available
3. computes summary metrics and slice metrics
4. compares a new run to an older baseline run to catch regressions

This makes it an evaluation system, not just a script that prints scores.

## 3. Where It Fits In The Resume Analysis Pipeline

The evaluator calls the same `analyze_resume()` function used by the application.

That means the framework evaluates the actual operational pipeline:

1. resume JSON input is loaded
2. resume structure is normalized
3. skill evidence is extracted
4. semantic role taxonomy is inferred
5. experience signals are computed
6. DNA fit is computed
7. BERT priors are merged
8. scorecard is generated
9. LLM reasoning and fallback paths are attached
10. final output JSON is produced

This is important because the eval framework is not testing a toy approximation of the system. It is testing the real analysis graph.

## 4. Core Design Principles

The framework was designed around these principles:

### A. Evaluate the actual system

The eval should reflect production behavior, not a simplified benchmark-only path.

### B. Support business truth, not only internal consistency

A technically consistent score is not enough if the predicted role family or band is not useful for recruiting decisions.

### C. Make regressions visible

The framework should not only report numbers. It should tell us when a new version is worse than the last accepted version.

### D. Be lightweight and local

The framework should work inside the repo without requiring an external SaaS platform.

### E. Stay aligned with resume analysis realities

This system is not a generic chatbot. It is a structured hiring-oriented evaluator. The framework must reflect that domain.

## 5. High-Level Architecture

At a high level, the evaluator has five layers:

### Layer 1. Dataset input

Inputs are:

- one resume JSON file
- or a directory of resume JSON files
- plus an optional expectation manifest

### Layer 2. Analysis execution

Each case is sent through the real `analyze_resume()` pipeline.

### Layer 3. Case trace creation

The framework builds a structured per-case record that captures:

- file
- candidate name
- predictions
- expectations
- match result
- quality warnings
- tags

### Layer 4. Metrics and slices

The framework computes:

- summary metrics
- classification metrics
- score error metrics
- slice-level metrics

### Layer 5. Regression layer

The framework compares the new run to a previous run and decides:

- whether gates pass
- whether anything regressed
- which metrics are drifting

## 6. What We Measure From An ML Perspective

From an ML point of view, the framework is measuring both output quality and system reliability.

### A. Role-family prediction quality

This checks how often the predicted role family matches the expected role family when labeled expectations exist.

Why it matters:

- role family is one of the highest-value outputs for hiring decisions
- it is influenced by skill evidence, taxonomy, titles, BERT priors, and LLM reasoning

Metric used:

- `role_family_match_rate`

### B. Band prediction quality

This checks how often the score band matches the expected band.

Why it matters:

- the band is what recruiters will use quickly
- errors here create decision noise even if the numeric score is only slightly off

Metric used:

- `band_match_rate`

### C. DNA classification quality

This checks whether the system correctly classifies the candidate operating style such as:

- consulting
- product
- hybrid
- domain specialist

Why it matters:

- DNA fit is important for organizational fit and interviewer routing

Metric used:

- `dna_match_rate`

### D. Score quality

This measures how close the predicted total score is to the expected total score where a target score exists.

Why it matters:

- recruiter trust is damaged if score movement feels arbitrary
- product decisions depend on score thresholds

Metric used:

- `score_mae`
- `max_abs_error`

### E. LLM system reliability

This does not measure label correctness directly. It measures whether the intended LLM path actually succeeded.

Metrics used:

- `llm_score_success_rate`
- `llm_skill_success_rate`

Why it matters:

- a model can appear stable only because the system is falling back too often
- hidden fallback usage can distort perceived quality

### F. Internal score integrity

This checks whether component totals actually equal the published total score.

Metric used:

- `score_consistency_rate`

Why it matters:

- this protects mathematical credibility
- it prevents UI trust failures

### G. Explainability completeness

The framework also checks whether the system is generating the justification fields required for trustworthy output.

Signals tracked:

- missing component justifications
- missing component rationales

Why it matters:

- in hiring systems, unsupported outputs are operationally weak even if the raw predictions are accurate

## 7. What We Measure From A Product Perspective

From a product point of view, the eval framework measures whether the system is useful, reliable, and trustworthy for hiring workflows.

### A. Recruiter usefulness

This is approximated through:

- role family correctness
- band correctness
- score quality
- quality of justifications

Why it matters:

- recruiters need direction, not just raw technical output

### B. Decision stability

A product is hard to trust if small internal changes lead to major output instability.

This is evaluated through:

- baseline comparisons
- regression gates
- slice comparisons

### C. Failure visibility

Product systems need clear answers when something goes wrong.

The framework provides:

- top failure cases
- failed expectation checks
- fallback reasons
- missing explanation warnings

### D. Segment trust

A product may look good overall but still fail badly for certain resume types.

That is why slice analysis exists.

Examples:

- consulting resumes
- data engineering profiles
- GenAI-heavy candidates
- resumes with sparse quantified impact

### E. Release safety

The framework supports release decisions by showing:

- whether a run improved or regressed
- whether important thresholds passed
- whether there are unresolved top failures

## 8. How Traces Are Set Today

This system does not yet have a full distributed tracing layer like a production observability platform.

However, it does have a structured per-case evaluation trace.

In our context, a trace means:

- the per-case path from input file
- through production analysis execution
- to predictions
- to expected targets
- to match results
- to quality warnings

### Current trace unit

The current trace unit is the case record inside the eval report.

Each case trace includes:

- `case_id`
- `file`
- `candidate_name`
- `predicted`
- `expected`
- `expectation_result`
- `score_consistent`
- `missing_component_justifications`
- `missing_component_rationales`
- `skill_judgment_reason`
- `score_failure_reason`
- `tags`

This is enough for:

- offline debugging
- experiment comparison
- slice creation
- failure inspection

### What traces are not yet

Today traces do not yet include:

- step-by-step latency
- token usage per sub-call
- live production request IDs
- timeline of BERT vs LLM vs fallback decisions
- per-module execution spans

So the current trace model is eval-trace oriented, not observability-trace oriented.

## 9. How Datasets Work

The dataset can be:

- a single JSON file
- or a directory tree of JSON files

The evaluator discovers files recursively when a folder is given.

This makes it easy to support:

- smoke datasets
- golden recruiter-reviewed sets
- large benchmark folders

## 10. How Expectations Work

Expectations are optional labels or business targets attached to cases.

Examples:

- expected role family
- expected band
- expected DNA
- expected total score
- allowed score tolerance
- minimum score
- maximum score
- required presence of name
- mandatory LLM scoring usage
- tags

The expectation layer is how the framework connects technical evaluation to business truth.

Without expectations, the evaluator can still check system health.

With expectations, it can check usefulness and correctness.

## 11. How Summary Metrics Work

The eval report creates a summary section with top-level metrics such as:

- total cases
- LLM score success rate
- LLM skill success rate
- score consistency rate
- average total score
- expectation match rate
- role-family match rate
- band match rate
- DNA match rate
- score MAE

This gives the top-line view of the run.

## 12. How Slice Metrics Work

Each case gets tags.

Tags may come from:

- predicted role family
- predicted band
- score consistency
- LLM applied vs fallback
- user-provided expectation tags

The evaluator groups cases by tag and computes metrics per slice.

This helps answer questions like:

- are GenAI resumes getting worse?
- are fallback-heavy cases becoming more common?
- are consulting profiles scoring incorrectly?

This is one of the strongest parts of the framework because overall averages can be misleading.

## 13. How Top Failures Work

Top failures are the highest-signal broken or risky cases surfaced from the run.

These include:

- expectation mismatches
- missing justifications
- inconsistent scoring
- major fallback cases

Each top-failure record includes:

- case identity
- predicted outputs
- expected outputs
- failed checks
- score failure reason
- skill judgment reason
- tags

This is effectively the debugging queue for the team.

## 14. How Baselines And Regressions Work

If a previous run report is supplied, the evaluator compares the new run with the old one.

It computes:

- current metric value
- baseline metric value
- delta

Then it checks gates.

### Gate examples

Examples of gates currently used:

- minimum LLM score success rate
- minimum LLM skill success rate
- minimum score consistency rate
- minimum expectation match rate
- minimum role-family match rate
- minimum band match rate
- maximum score MAE
- maximum allowed negative delta vs baseline

This is the core protection mechanism.

It changes the eval from:

- "interesting report"

to:

- "release protection system"

## 15. How The Eval UI Works

The app now includes an eval workspace at:

- `/evals`

It reads reports from:

- `eval_runs/`

The UI includes:

### A. Run History

This shows saved eval runs and lets the user select one.

### B. Leaderboard

This ranks runs using a lightweight composite score built from:

- expectation match rate
- score consistency rate
- LLM score success rate
- penalties for regression count and failed gates

This is not a scientific metric. It is an operator-friendly prioritization aid.

### C. Slice Explorer

This shows per-tag slice metrics.

### D. Regression Gate Viewer

This shows which gates passed or failed against baseline.

### E. Top Failure Explorer

This gives drilldown into the most important broken or risky cases.

### F. Raw Run JSON

This gives full transparency for debugging.

## 16. Why This Eval Framework Is In Line With Resume Analysis

Yes, the framework is aligned with the resume analysis system.

That is because it measures the same things the product is trying to optimize.

### Alignment with resume analysis objectives

The resume analysis system is trying to produce:

- credible role family classification
- credible score banding
- explainable skill evidence
- useful recruiter notes
- stable total scoring
- reliable fallback behavior

The eval framework measures exactly those concerns.

### Alignment with recruiter workflow

Recruiters care about:

- role fit
- score credibility
- hiring band
- operating style
- explanation quality
- failure transparency

The framework maps naturally to those outputs.

### Alignment with model iteration

When we retrain BERT, change prompts, or adjust evidence logic, the framework tells us whether those changes improved the hiring-oriented outputs or degraded them.

So this is not a generic eval framework bolted on top of the product. It is aligned with the actual shape of the product.

## 17. Comparison With Galileo

Galileo positions itself as an AI evaluation and observability platform that supports dataset-based evals, offline experimentation, failure analysis, production monitoring, and guardrail-style workflows. Source: [galileo.ai](https://galileo.ai/).

### Where Our Framework Matches Galileo Concepts

#### A. Dataset-first evaluation

Galileo emphasizes datasets as the basis for evaluation.

We already support:

- local benchmark datasets
- expectation manifests
- tagged case sets

#### B. Offline experiment runs

Galileo treats evaluation as an ongoing engineering workflow.

We now support:

- run IDs
- run labels
- dataset naming
- saved reports

#### C. Regression protection

Galileo focuses heavily on safe iteration.

We already support:

- baseline comparison
- metric deltas
- gate checks
- explicit regression lists

#### D. Failure analysis

Galileo emphasizes surfacing failure patterns and individual bad cases.

We already support:

- top failures
- failed expectation checks
- slice metrics
- fallback reasoning visibility

#### E. Custom eval logic

Galileo supports business-specific evaluators.

Our expectation manifests already let us encode domain-specific targets for resume analysis.

## 18. Where We Are Still Behind Galileo

This is the honest gap view.

### A. No production observability yet

We do not yet have:

- live request monitoring
- production drift tracking
- real-time alerts
- continuous online traces

### B. No annotation workflow yet

We do not yet have:

- reviewer labeling UI
- approval queues
- active feedback ingestion into eval datasets

### C. No advanced run management UI yet

We now have an eval workspace, but it is still lightweight.

We do not yet have:

- full experiment lineage
- filtering across many runs
- charting and historical trend graphs
- dataset version management

### D. No runtime guardrails

We do not yet use eval outputs to:

- block deployment automatically
- route risky cases differently
- trigger runtime intervention

### E. No production-grade trace graph

We have case traces inside eval reports.

We do not yet have:

- distributed spans
- request-level observability
- sub-call token and latency tracking

## 19. Honest Positioning

The correct way to position our framework today is:

- it is Galileo-inspired
- it is strong as an offline eval and regression system
- it is aligned with our resume analysis product
- it is not yet a full Galileo-like observability platform

That is the honest and defensible framing.

## 20. Current Files Involved

### `eval_framework.py`

The core evaluation engine.

### `app.py`

The application UI and API layer, including:

- `/api/eval/runs`
- `/api/eval/runs/{run_id}`
- `/evals`

### `EVAL_SYSTEM_DOC.md`

Conceptual system note for the evaluation framework.

### `EVAL_FRAMEWORK_VS_GALILEO.md`

Comparison note showing similarities and gaps versus Galileo.

### `engine.py`

The production analysis graph that the evaluator calls.

## 21. Recommended Next Steps

If we want to improve this framework further, the strongest next steps are:

### Phase 1. Stronger labeled datasets

- recruiter-reviewed golden dataset
- clearer target score expectations
- more business tags

### Phase 2. Better eval UI

- historical charts
- run-to-run comparison pages
- better case filters

### Phase 3. Feedback ingestion

- use recruiter corrections
- use interview outcomes
- use final hiring outcomes

### Phase 4. CI gating

- fail release if critical eval metrics regress

### Phase 5. Production observability

- live traces
- fallback-rate monitoring
- drift and failure trend monitoring

## 22. Summary

The eval framework we built is:

- aligned with the resume analysis system
- useful for both ML and product quality control
- capable of dataset-based experiments
- capable of baseline regression protection
- capable of slice and failure analysis
- partially aligned with Galileo concepts

From an ML perspective, it protects prediction quality and system reliability.

From a product perspective, it protects recruiter trust, decision stability, and release safety.

From a tracing perspective, it currently provides structured offline case traces, but not yet full production observability.

So the right conclusion is:

- yes, the framework is in line with our resume analysis system
- yes, it is a real evaluation framework
- no, it is not yet a full Galileo-style platform
- but it is a strong and practical foundation to grow into that direction
