# Prompt Documentation — Resume Intelligence

> **Last updated:** 2026-05-30
> **Version:** v4 (BERT retrain + JD matching integrated)

---

## Overview Table

| # | File | Prompt constant / location | Purpose | ~Tokens (system) | Scoring Scale | Key output |
|---|------|---------------------------|---------|-------------------|---------------|------------|
| 1 | `llm_judging_assets.py` | `SCORING_SYSTEM_PROMPT` | LLM judgment of all 6 scorecard dimensions | ~180 | 0-5 per dimension (×weights = 100pt total) | JSON scorecard |
| 2 | `llm_judging_assets.py` | `SCORING_FEWSHOT` | Two calibration examples for scorer | ~800 (2 examples) | — | Context only |
| 3 | `llm_resume_judge.py` | inline system string | Semantic + qualitative LLM analysis | ~400 | — | recruiter_summary, top_skills, DNA |
| 4 | `llm_judging_assets.py` | `ANALYSIS_SYSTEM_PROMPT` | Recruiter-grade analysis framing | ~80 | — | Wraps #3 |
| 5 | `llm_judging_assets.py` | `CLIENT_FIT_SYSTEM_PROMPT` | 5-7 fit bullets for client briefs | ~130 | — | fit_bullets[] |
| 6 | `llm_judging_assets.py` | `SIMILAR_COMPANIES_SYSTEM_PROMPT` | Talent market intelligence | ~120 | Tier 1-5 | companies[] |
| 7 | `llm_judging_assets.py` | `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT` | Post-screen rubric parameter override | ~200 | 0-max per param | experience{} |
| 8 | `llm_judging_assets.py` | `PROJECT_JUDGE_SYSTEM_PROMPT` | Deep reverse-engineering of each project | ~600 | 0-5 complexity | project analysis |
| 9 | `llm_judging_assets.py` | `RUBRIC_JUSTIFY_SYSTEM_PROMPT` | Scorecard justification text | ~350 | — | justifications{} |
| 10 | `llm_recruiter_analysis.py` | inline | Recruiter narrative generation | ~200 | — | recruiter_summary text |
| 11 | `interview_question_engine.py` | inline | Interview question generation | ~300 | — | questions[] |
| 12 | `telephonic_question_engine.py` | inline | Phone screen question set | ~250 | — | telephonic_questions[] |
| 13 | `transcript_scoring_engine.py` | inline | Transcript → rubric score delta | ~300 | 0-100 delta | score update |
| 14 | `question_scoring_engine.py` | `QUESTION_SCORE_SYSTEM_PROMPT` | Per-question answer scoring | ~250 | 0-10 per answer | score + feedback |
| 15 | `boss_explainability.py` | inline | Hiring-manager explainability layer | ~200 | — | rationale prose |
| 16 | `company_intelligence.py` | inline | Company-tier and signal analysis | ~150 | Tier 1-4 | company_tier, signals |
| 17 | `experience_credibility.py` | inline | Experience claim credibility check | ~200 | 0-1 confidence | credibility flags |
| 18 | `client_intelligence_engine.py` | `CLIENT_FIT_SYSTEM_PROMPT` | Client-specific candidate fit | ~130 | — | fit_bullets[] |
| 19 | `scoring_engine.py` | inline prompt assembly | Compact context for LLM judging | ~500 assembled | 0-100 | full scorecard |

---

## Prompt Catalogue

### 1. `SCORING_SYSTEM_PROMPT`
**File:** `llm_judging_assets.py`
**Role:** Senior recruiter / hiring panel lead
**Input:** Evidence summary from deterministic extraction + BERT signals
**Output:** JSON scorecard — 6 dimension scores (0-5) × weights → 100pt total

Key calibration rules:
- 0-5 with 0.5 steps (see `SHARED_DEPTH_RUBRIC` shared constant)
- HIGH confidence requires multiple direct signals; use MEDIUM/LOW for inference
- Mixed evidence → lower score
- Every rationale must state: strongest evidence, what limitation kept it lower, why not lower

**Critical constraints:**
- Do NOT treat keyword counts as evidence
- Reward ownership, complexity, strategic thinking, quantified outcomes
- Penalise inflated claims, vague breadth, missing business proof

---

### 2. `SCORING_FEWSHOT`
**File:** `llm_judging_assets.py`
**Type:** Two calibration examples (user-turn messages)
**Examples:**
- 3-6 year candidate → score 72, band GOOD (GenAI breadth, modest impact)
- 10+ year candidate → score 79, band STRONG (architecture depth, weak quantification)

These anchor the model to realistic calibration across bands.

---

### 3. LLM Resume Judge (inline, `llm_resume_judge.py`)
**Lines:** ~270-310
**Role:** Semantic + qualitative analyst
**Key outputs:** `recruiter_summary` (≤80 words), `top_role_family`, `role_family_rationale` (≤22 words), `consistency_readout`, `top_skill_judgments`, `dna_judgment`, `strengths`, `gaps`, `risk_flags`

**Word limits (as of v4):**
- `recruiter_summary`: ≤80 words (increased from 55 in v4)
- `role_family_rationale`: ≤22 words
- `consistency_readout`: ≤20 words
- `dna_judgment.reason`: ≤20 words
- Max 3 strengths, 2 gaps, 2 risk flags, 3 panel suggestions, 5 top_skill_judgments

---

### 4. `ANALYSIS_SYSTEM_PROMPT`
**File:** `llm_judging_assets.py`
**Role:** Wrapping framing for the resume judge LLM call
**Key instructions:**
- Interpret evidence like a hiring manager — not a keyword counter
- Be skeptical of over-claimed breadth
- Speak crisp recruiter language
- Every verdict must state supporting evidence AND missing proof

---

### 5. `CLIENT_FIT_SYSTEM_PROMPT`
**File:** `llm_judging_assets.py` + `client_intelligence_engine.py`
**Output:** 5-7 fit bullets in third-person recruiter language
**Rules:** Each bullet must reference a concrete signal; address mandatory gaps explicitly; no inflated summaries

---

### 6. `SIMILAR_COMPANIES_SYSTEM_PROMPT`
**Output:** Up to 10 similar companies with domain, tier (1=FAANG to 5=unknown), and relevance_reason
**Constraints:** Do not suggest companies already in candidate history; prefer recognisable companies

---

### 7. `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT`
**Purpose:** Post-phone-screen rubric override — anchors from deterministic extraction reviewed by LLM
**Parameters scored:** `career_progression` (max 3), `international_exposure` (max 2), `stakeholder_management` (max 2), `mentorship_signal` (max 3), `awards_recognition` (max 3)
**Constraints:** Reasons under 80 words; cite company/role/project evidence for every score

---

### 8. `PROJECT_JUDGE_SYSTEM_PROMPT`
**File:** `llm_judging_assets.py`
**Purpose:** Deep reverse-engineering of project reality from company + role + era
**Per-project outputs:**
- `confirmed_type`: DEVELOPMENT / MIGRATION / ANALYTICS / INFRASTRUCTURE / RESEARCH / CONSULTING / OPERATIONS / TRANSFORMATION / MAINTENANCE / HYBRID
- `complexity_score` (0-5, one decimal): Most candidates are 2-3; only top 5% reach 4+
- `verdict_label`: 3-5 word recruiter-readable label
- `era_context`: 1 sentence on industry landscape during this project
- `reverse_engineered_scope`: 2-3 sentences inferring hidden reality from company/role/era
- `implied_skills`: 5-10 skills the candidate must have used (not stated)
- `claimed_skills_verified`, `skill_gaps_detected`
- `green_flags` (2-4), `red_flags` (1-3)
- `role_intent`: What role is this person building toward?
- `candidate_signal`: EXCELLENT / STRONG / AVERAGE / WEAK
- `skill_exhibition_type`: DEPTH / BREADTH / LEADERSHIP / HANDS_ON / STRATEGIC
- `interview_probe`: Single sharp question under 25 words

---

### 9. `RUBRIC_JUSTIFY_SYSTEM_PROMPT`
**File:** `llm_judging_assets.py`
**Purpose:** Generate scorecard justification text for all rubric parameters
**Special rules for project_1/project_2 (4-part paragraph):**
1. WHAT IT WAS — verdict_label, company, role level, era/scope
2. WHY THIS SCORE — evidence that drove the score
3. WHAT THE SIGNAL MEANS — plain recruiter language interpretation
4. WHAT TO PROBE — single most critical unverified claim (use "To verify:" not "Probe")

**Formatting:** Continuous paragraph, no bullets, no headers
**Wording standardization (v4):** Use "To verify:" consistently — not "Key question:" or "Probe"

---

### 10. LLM Recruiter Analysis (`llm_recruiter_analysis.py`)
**Purpose:** Generate the narrative recruiter_summary shown in the main UI
**Input:** Full analysis result dict
**Output:** Free-text summary (not structured JSON) — compact prose for UI display

---

### 11. Interview Question Engine (`interview_question_engine.py`)
**Purpose:** Generate tailored interview questions based on resume signals + client role config
**Output:** 10-15 questions split into RECRUITER and PANEL stages
**Each question:** question text, rubric_param, priority (HIGH/MEDIUM/LOW), what_it_tests, scoring_guide

---

### 12. Telephonic Question Engine (`telephonic_question_engine.py`)
**Purpose:** Streamlined 5-question phone screen set
**Focus:** Must-have skill validation + career intent + red flag probes

---

### 13. Transcript Scoring Engine (`transcript_scoring_engine.py`)
**Purpose:** Score a full interview transcript against the resume, computing rubric parameter deltas
**Output:** Updated rubric overrides + overall transcript quality score

---

### 14. `QUESTION_SCORE_SYSTEM_PROMPT`
**File:** `question_scoring_engine.py`
**Purpose:** Score a single verbal answer (0-10 scale)
**Scale:**
- 0-2: No answer / deflection
- 3-4: Vague, no concrete project
- 5-6: Adequate — one example, lacks depth
- 7-8: Good — specific ownership, design decisions
- 9-10: Excellent — quantified, ownership, failure/learning

**Deduction rules:**
- "We did X" without "I did Y" → -1
- No business impact → max 8
- Buzzword-heavy, no substance → max 4
- Under 3 sentences → max 5
- Deflects / "I don't remember" → max 3

**One-shot example (added v4):** Shows 5/10 weak vs 8/10 strong Spark answer for calibration

---

### 15. Boss Explainability (`boss_explainability.py`)
**Purpose:** Hiring-manager-readable rationale for scores (non-recruiter audience)
**Output:** Plain English explanation of score drivers

---

### 16-18. Intelligence Prompts
- `company_intelligence.py` — company tier classification and signals
- `experience_credibility.py` — date/claim credibility checks
- `client_intelligence_engine.py` — client-specific fit narrative (mirrors #5)

---

### 19. Scoring Engine Inline Prompt (`scoring_engine.py`)
**Purpose:** Assemble compact context for LLM judging from deterministic extraction
**~500 assembled tokens** including evidence summary, BERT scores, and band
**Feeds into:** `SCORING_SYSTEM_PROMPT` + `SCORING_FEWSHOT` call chain

---

## Shared Constants

### `SHARED_DEPTH_RUBRIC` (added v4)
**File:** `llm_judging_assets.py`
**Used in:** `SCORING_SYSTEM_PROMPT`, available for inline prompts in `scoring_engine.py`
**Eliminates redundancy** across the 0-5 depth calibration description

---

## Optimization Notes

### Token Budget
- Largest single call: `PROJECT_JUDGE_SYSTEM_PROMPT` (~600 tokens system + resume context)
- Bulk mode: Skip `generate_recruiter_analysis()` to save ~900 tokens × N resumes (`ENABLE_BULK_MODE_FAST=true`)
- All analysis calls: Use streaming=False (synchronous) via `llm_client.py`

### Calibration Strategy
- All LLM judges are anchored to the `SCORING_FEWSHOT` examples
- BERT signals feed in as prior anchors — LLM can confirm or correct
- Confidence levels guard against over-confident outputs (HIGH is rare)

### Word Limits
| Field | Limit | Notes |
|-------|-------|-------|
| `recruiter_summary` | 80 words | Increased from 55 in v4 |
| `role_family_rationale` | 22 words | Appropriate — stays concise |
| `consistency_readout` | 20 words | |
| `dna_judgment.reason` | 20 words | |
| `component_rationale` | 16 words | Per dimension in scoring |
| `interview_probe` | 25 words | Sharp, specific to the project |
| `reason` (rubric params) | 80 words | Post-screen overrides |

---

## Known Limitations

1. **No multi-turn conversation** — each LLM call is independent; no conversation memory
2. **Scoring engine doesn't use `SHARED_DEPTH_RUBRIC` directly yet** — inline prompt assembly duplicates the rubric text
3. **JD matching LLM narrative disabled** — `jd_matching/engine.py` sets `llm = None`; narrative comes from deterministic fallback only
4. **Resume integrity heuristics are shallow** — `evaluate_integrity()` uses simple date/overlap checks, not LLM-based verification
5. **DNA fit is single-label** — only one primary DNA profile returned; multi-label case not yet supported

---

## Education Scoring Rules (Non-Prompt, Deterministic)

These rules live in `rubric_engine.py` + `education_engine.py` + `taxonomy.py` — not in LLM prompts.

### Institute Tier → Points (out of 5)
| Tier | Base | GPA Excellent bonus | GPA Good bonus |
|------|------|---------------------|----------------|
| TIER_1 (IIT / IIM / ISI / ISB / IIIT-H / global top-200) | 4.0 | +1.0 | +1.0 |
| TIER_2 (strong state/NIT/VIT/Manipal) | 3.0 | +0.5 | — |
| TIER_3 | 2.0 | — | — |
| TIER_4 | 1.0 | — | — |

**Full-name institution matching** — taxonomy now includes full-name aliases for:
- Indian Institute of Technology Kharagpur / Bombay / Delhi / Madras / Kanpur / Roorkee / Guwahati / Hyderabad
- Indian Statistical Institute / Indian Statistical Institute Kolkata / ISICAL
- Indian Institute of Management Ahmedabad / Bangalore / Calcutta / Lucknow / Kozhikode / Indore

### Course Relevance → Points (out of 2)
| Signal | Courses | Points |
|--------|---------|--------|
| HIGH | CS / Engineering / MCA / PhD / Research / **Analytics / Statistics / Quantitative Economics / Econometrics / Operations Research / Data Science / AI** | 2.0 |
| MEDIUM | B.Sc / M.Sc / MBA / Management | 1.5 |
| FOUNDATIONAL | Arts / Commerce | 0.5 |

**Rationale:** Quantitative Economics (MSQE), Statistics, Analytics degrees are treated as HIGH relevance for data science / analytics roles — on par with CS/Engineering degrees.

### IT Stream Classification
Degrees containing these keywords are treated as "IT stream" (affects degree combo score):
- Standard: CS, CSE, IT, Software Engineering, Data Science, AI/ML, MCA
- **Added:** Statistics, Analytics, Quantitative, Econometrics, Operations Research, Actuarial, MSQE

### Patents / Publications Bonus (out of 2.5)
| Condition | Points |
|-----------|--------|
| Patents or publications detected | 2.5 |
| **TIER_1 institution, no patents** | **0.5** (elite academic research culture credit) |
| **TIER_2 institution, no patents** | **0.25** (partial research culture credit) |
| Other institution, no patents | 0.0 |

**Rationale:** Admission to IIT / ISI / IIM already demonstrates exceptional academic selectivity. The patents parameter should not heavily penalize candidates who excelled academically but chose applied careers over research.

---

## Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| v1 | 2025 | Initial scoring + analysis prompts |
| v2 | 2025 | Added PROJECT_JUDGE, RUBRIC_JUSTIFY, telephonic questions |
| v3 | 2026-Q1 | BERT signals added; rubric_engine wired to BERT outputs; new career_progression/stakeholder/mentorship tasks |
| v4 | 2026-05-30 | Added `SHARED_DEPTH_RUBRIC`; recruiter_summary limit 55→80 words; standardized "To verify:" in RUBRIC_JUSTIFY; one-shot example in QUESTION_SCORE_SYSTEM_PROMPT; JD matching integrated (engine.py, jd_matching_bridge.py); bulk pipeline (bulk_pipeline.py) |
| v5 | 2026-05-31 | Education scoring overhaul: full-name IIT/IIM/ISI aliases in taxonomy; Statistics/Quant Econ/Analytics = HIGH course relevance + IT-stream; TIER_1 gets 0.5 base patent pts; TIER_2 gets 0.25; garbage institute cache entries cleared |
