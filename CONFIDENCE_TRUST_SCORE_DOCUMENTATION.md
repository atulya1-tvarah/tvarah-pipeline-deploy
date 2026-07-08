# Confidence Trust Score Documentation

## Purpose

The Resume Intelligence eval framework uses a **Confidence Trust Score** to indicate how trustworthy a resume analysis result is.

This score is not the same as the candidate's hiring score.

- The **candidate score** answers:
  - "How strong does this candidate look?"
- The **confidence trust score** answers:
  - "How much should we trust this analysis output?"

This distinction is important because a candidate may receive:

- a strong candidate score, but
- only medium confidence

if the resume evidence is weak, incomplete, fallback-heavy, or inconsistent.

---

## Why We Built It

A simple average of `HIGH / MEDIUM / LOW` confidence labels was too shallow for production use.

It did not account for:

- evidence quality
- extractor completeness
- fallback behavior
- score consistency
- operational reliability

The new trust score is designed to behave more like an eval-grade confidence signal inspired by systems such as Galileo:

- model certainty matters
- evidence density matters
- pipeline quality matters
- missing or fallback-heavy reasoning reduces trust

---

## What The Confidence Score Means

The confidence score is a **trust score for the analysis pipeline output**.

It estimates whether the result is:

- well-supported by the resume
- internally consistent
- generated with enough reliable evidence
- not overly dependent on fallback logic

The output includes:

- a numeric score from `0.0` to `1.0`
- a label:
  - `HIGH`
  - `MEDIUM`
  - `LOW`

In the UI this is shown as a percentage.

The current implementation has **two layers**:

1. a **composite trust score**
2. an **empirical calibration layer** when enough labeled eval history exists

So confidence is no longer just a raw composite score. It can now be corrected using historical expectation-backed eval outcomes.

Example:

- `0.77` -> `77%`
- label -> `MEDIUM`

---

## Base Trust Formula

The confidence trust score is a weighted blend of 5 components:

1. `Model certainty` = `35%`
2. `Evidence density` = `25%`
3. `Score consistency` = `10%`
4. `Fallback reliability` = `20%`
5. `Extractor completeness` = `10%`

### Formula

```text
raw_confidence_score =
  (model_certainty * 0.35) +
  (evidence_density * 0.25) +
  (score_consistency * 0.10) +
  (fallback_reliability * 0.20) +
  (extractor_completeness * 0.10)
```

This produces the **raw trust score**.

---

## Empirical Calibration Layer

After the raw trust score is computed, the framework attempts to calibrate it using prior labeled eval cases saved in `eval_runs/`.

### Calibration data source

Calibration points come from historical eval cases where:

- an `expectation_result` exists
- a confidence score exists
- the case has a known match / non-match outcome

Each calibration point contains:

- `confidence score`
- `matched = 1 or 0`

### Calibration method

The framework currently uses a **nearest-neighbor empirical calibration** approach.

For a new confidence score:

1. sort historical labeled cases by distance from the current raw score
2. take the nearest neighborhood
3. compute the empirical match rate in that neighborhood
4. use that empirical rate as the calibrated confidence

### Current thresholds

- minimum historical labeled cases required globally: `20`
- neighborhood size: up to `25`
- minimum local support required: `10`

If these support thresholds are not met, the system keeps the raw trust score.

### What this means

If historically:

- cases around raw confidence `0.78`
- actually match expectations about `0.72` of the time

then the calibrated confidence becomes approximately:

- `0.72`

This moves the score closer to observed evaluation reality.

---

## Final Confidence Output

The system now returns:

- `raw_score`
- `score`
- `label`
- `calibration.status`
- `calibration.support`
- `calibration.window`

### Interpretation

- `raw_score`
  - the composite trust score before historical correction
- `score`
  - the final score used by evals and UI
- `calibration.status`
  - whether empirical calibration was applied
- `calibration.support`
  - how many historical labeled neighbors were used
- `calibration.window`
  - how far the empirical neighborhood extended from the raw score

### Status values

- `empirical_knn`
  - calibrated using historical labeled eval outcomes
- `uncalibrated`
  - no strong enough historical support yet, so raw trust score is used

---

## Component Details

## 1. Model Certainty

This comes from the scorecard's dimension-level confidence judgments:

- `skill_strength`
- `experience_depth`
- `role_alignment`
- `business_impact`
- `career_stability`
- `dna_fit`

These are mapped as:

- `HIGH = 1.0`
- `MEDIUM = 0.66`
- `LOW = 0.33`

The average of those values becomes `model_certainty`.

### What it means

This measures how confident the scoring layer was across the main candidate-evaluation dimensions.

### Example

If the six dimensions are:

- `HIGH`
- `MEDIUM`
- `HIGH`
- `LOW`
- `MEDIUM`
- `MEDIUM`

Then:

```text
(1.0 + 0.66 + 1.0 + 0.33 + 0.66 + 0.66) / 6 = 0.718
```

So:

- `model_certainty = 0.718`

---

## 2. Evidence Density

This measures how much strong, recent, and structurally credible evidence exists in the resume.

It combines:

- `skill_consistency_score`
- `strong_skill_count`
- `recent_skill_hits`
- `architecture_hits`
- `judged_skill_ratio`

### Inputs

- `skill_consistency_score`
  - how much of the skill footprint is backed by meaningful evidence
- `strong_skill_count`
  - number of stronger skills found in the scored window
- `recent_skill_hits`
  - how many skills have recent evidence
- `architecture_hits`
  - how many skills show architecture/design-level cues
- `judged_skill_ratio`
  - how many top skills ended up with meaningful judged rationale

### Internal weighting

```text
evidence_density =
  (skill_consistency_score * 0.45) +
  (normalized_strong_skill_count * 0.25) +
  (normalized_recent_skill_hits * 0.15) +
  (normalized_architecture_hits * 0.05) +
  (judged_skill_ratio * 0.10)
```

### What it means

This measures whether the resume has enough real technical proof to justify trusting the analysis.

A resume with many keyword mentions but weak evidence will score lower here.

---

## 3. Score Consistency

This checks whether the score math is internally consistent.

### Logic

- if weighted component scores add up exactly to total score:
  - `score_consistency = 1.0`
- otherwise:
  - `score_consistency = 0.35`

### What it means

This prevents trusting a result whose score presentation is mathematically unstable.

This is especially important because candidate-facing or recruiter-facing scoring must be explainable.

---

## 4. Fallback Reliability

This measures how much of the pipeline used true model judgment versus fallback logic.

It looks at:

- score judgment
- skill judgment
- DNA judgment

### Mapping

- score judgment applied -> `1.0`
- score judgment fallback -> `0.45`

- skill judgment applied -> `1.0`
- skill judgment fallback -> `0.4`

- DNA judgment applied -> `1.0`
- DNA judgment fallback -> `0.55`

### Internal weighting

```text
fallback_reliability =
  (score_judgment_score * 0.45) +
  (skill_judgment_score * 0.30) +
  (dna_judgment_score * 0.25)
```

### What it means

If the analysis relied heavily on fallback logic, trust should drop.

This does not automatically mean the result is wrong. It means the system had less ideal inference support for that run.

---

## 5. Extractor Completeness

This measures whether the extracted candidate basics are complete enough for a trustworthy downstream read.

The current extractor completeness checks:

- `name`
- `email`
- `phone`
- `location`
- `profile_summary`

Each present field contributes equally.

### Formula

```text
extractor_completeness =
  present_fields / 5
```

### Example

If the resume has:

- name
- email
- phone
- no location
- profile summary

Then:

```text
4 / 5 = 0.80
```

### What it means

If extraction is incomplete, downstream analysis may still work, but trust should be reduced.

---

## Final Labels

After combining all five components:

- `>= 0.84` -> `HIGH`
- `>= 0.50` -> `MEDIUM`
- `< 0.50` -> `LOW`

### Interpretation

- `HIGH`
  - strong evidence, low fallback dependence, consistent score math, healthy extraction quality
- `MEDIUM`
  - mostly usable, but at least one trust dimension is weaker
- `LOW`
  - analysis should be treated cautiously and usually reviewed before downstream decision-making

---

## Example Walkthrough

Assume:

- `model_certainty = 0.72`
- `evidence_density = 0.68`
- `score_consistency = 1.00`
- `fallback_reliability = 0.58`
- `extractor_completeness = 1.00`

Then:

```text
confidence_score =
  (0.72 * 0.35) +
  (0.68 * 0.25) +
  (1.00 * 0.10) +
  (0.58 * 0.20) +
  (1.00 * 0.10)

= 0.252 + 0.170 + 0.100 + 0.116 + 0.100
= 0.738
```

Final result:

- `73.8%`
- label -> `MEDIUM`

This means:

- the analysis is directionally trustworthy
- but some parts of the pipeline or evidence quality still limit full trust

---

## Why Confidence Can Be Medium Even When Candidate Score Is High

This is expected and correct.

A candidate can score highly because:

- strong skills are present
- role fit looks strong
- scoring buckets add up well

But confidence can still be only medium if:

- business impact evidence is thin
- some skill judgments fell back
- extractor fields are incomplete
- architecture evidence is weak
- explanation quality is patchy

This is one of the main benefits of separating:

- candidate strength
- analysis trust

---

## How This Appears In Eval Framework

The eval framework uses this trust score in:

- per-case records
- top failures
- slice metrics
- run summary
- leaderboard

### Surfaced metrics

- average confidence score
- confidence label per failure case
- confidence-aware slice comparison
- trust-aware live analysis visibility

This allows us to answer:

- which runs were accurate
- which runs were reliable
- which runs were cheap/fast
- which runs looked strong but should still be treated carefully

---

## How This Aligns With Galileo-Style Thinking

Galileo-style eval systems distinguish between:

- output correctness
- output confidence
- runtime behavior
- failure analysis

Our trust score aligns with that philosophy because it blends:

- model certainty
- evidence support
- score integrity
- fallback dependency
- extraction completeness

This makes it more useful than a simple confidence average and much closer to a real eval-grade trust metric.

---

## Current Positioning

This confidence score should be positioned as a **composite evaluation metric for analysis trust**.

That framing is important.

Modern eval systems such as Galileo and Braintrust also distinguish between:

- task quality metrics
- scorer outputs
- observability signals
- runtime reliability

They do **not** present every metric as a probability of correctness.

Our confidence score belongs in that same category:

- it is an **operational trust metric**
- it is a **composite evaluator output**
- it is useful for ranking, triage, monitoring, regression detection, and review prioritization

It is **not** currently:

- a calibrated probability that the full resume analysis is correct
- a learned confidence model trained on downstream recruiter decisions
- a substitute for labeled eval datasets, experiment comparisons, or golden-set accuracy measurement

So the right interpretation is:

- **Use it as a trust and review-priority signal**
- **Do not use it as a mathematical probability of correctness**

### Better wording

Instead of saying:

> This is still heuristic

the better wording is:

> This is a composite eval-grade trust metric derived from model certainty, evidence support, pipeline reliability, and extraction completeness. It is intended for operational confidence and triage, not as a calibrated correctness probability.

That is more accurate and more defensible in front of a client.

---

## What It Is Valid For

This confidence score is appropriate for:

- ranking analyses by trustworthiness
- identifying resumes that need recruiter review first
- spotting fallback-heavy or weak-evidence runs
- monitoring trust degradation across prompt/model changes
- surfacing low-confidence slices in the eval framework
- pairing with latency, tokens, and cost as part of production observability

---

## What It Is Not Valid For

This confidence score should not be used alone for:

- claiming exact correctness probability
- replacing labeled accuracy benchmarks
- making irreversible hiring decisions without human review
- proving recruiter-outcome correlation unless validated on outcome data

---

## Path To A Stronger Future Version

If we want this to become even stronger over time, the next maturity steps are:

1. **Outcome calibration**

- train the score against recruiter corrections, interview outcomes, shortlist decisions, and hiring outcomes

2. **Golden-set calibration**

- learn confidence-performance relationships on a labeled evaluation dataset

3. **Reliability calibration curves**

- test whether confidence buckets actually correspond to higher or lower correctness rates

4. **Segment calibration**

- separately calibrate by role family, experience band, and resume quality segment

5. **Online monitoring**

- compare confidence trends against real production failure patterns

Once those are done, the score can move from:

- composite trust metric

toward:

- empirically calibrated confidence estimator

---

## Recommended Client-Facing Explanation

If you need to explain this to a client simply:

> The confidence score tells us how trustworthy the analysis is, not how strong the candidate is. It combines model certainty, resume evidence quality, score consistency, fallback behavior, and extraction completeness into one trust signal.

Short version:

> Candidate score tells us how good the profile looks. Confidence tells us how much we trust that read.

---

## Implementation Reference

The confidence trust score is implemented in:

- `E:\Dev\resume_intelligence\eval_framework.py`

Key functions:

- `_analysis_confidence(...)`
- `_evidence_density_score(...)`
- `_fallback_reliability_score(...)`
- `_extraction_completeness_score(...)`
