# Resume Analysis — Technical Documentation

## Pipeline Overview

A resume goes through **8 sequential stages** before producing a score.

```
PDF / DOCX / JSON
       ↓
[1] pdf_to_resume_json()          → pdf_to_json_extractor.py
       ↓
[2] normalize_resume_data()       → engine.py
       Unifies field names from all extractor formats into:
       candidate_overview, experience_entries, skill_rows,
       education_entries
       ↓
[3] Sub-analysis engines (parallel)
    ├── collect_skill_evidence()  → skill_evidence_engine.py
    ├── analyze_experience()      → experience_engine.py
    ├── analyze_education()       → education_engine.py
    └── classify_dna()            → dna_engine.py
       ↓
[4] infer_bert_priors()           → bert_signal_engine.py
    Runs up to 7 trained BERT classifiers (if models are present)
    and returns probability-weighted labels for 4 rubric params
       ↓
[5] compute_rubric_score()        → rubric_engine.py
    _score_experience_section()
    _score_skills_section()
    _score_education_section()
    + certifications patch (edu → skills)
    + patents patch (exp → edu bonus)
       ↓
[6] _llm_judge_rubric_params()    → rubric_engine.py
    LLM = Qwen2.5:14b-instruct (via Ollama)
    Prompt = RUBRIC_LLM_JUDGE_SYSTEM_PROMPT  (llm_judging_assets.py)
    Reviews 5 qualitative experience params, can accept or override
       ↓
[7] generate_recruiter_analysis() → llm_recruiter_analysis.py
    LLM narrative for recruiter brief (separate call, Mistral/Qwen)
       ↓
[8] build_interview_questions()   → interview_question_engine.py
    On-demand when recruiter clicks "Generate Interview Questions"
```

---

## 3-Stage Score Model

| Stage | Who fills | When | Max pts | Normalized |
|-------|-----------|------|---------|-----------|
| **Resume** | Automatic | On upload | 76 raw pts | `(raw/76) × 100` |
| **Recruiter** | Human recruiter | After phone screen | +11 (total 87) | `(raw/87) × 100` |
| **Panel** | Technical panel | After interview | +13 (total 100) | Raw = final score |

---

## BERT Signal Engine — `bert_signal_engine.py`

BERT classifiers are loaded from `trained_models_v3/` (or `TRAINED_MODELS_DIR` env var).
If a model directory does not exist, that task **silently falls back to heuristic-only** scoring.

| Task | Model Input | Classes | Confidence Thresholds |
|------|-------------|---------|----------------------|
| `skill_depth` | Skill name + evidence text per skill | AWARENESS / FOUNDATIONAL / HANDS_ON / ADVANCED / ARCHITECT_LEVEL | ≥0.65 → 65% BERT / 35% evidence; 0.45–0.64 → 50/50; <0.45 → evidence only |
| `career_progression` | Job titles + role descriptions | DECLINING / LATERAL / GROWING / FAST_TRACK | ≥0.60 → 60% BERT / 40% heuristic; 0.40–0.59 → 50/50; <0.40 → heuristic only |
| `stakeholder_management` | Full resume text | NONE / INTERNAL / CLIENT_FACING / C_LEVEL | ≥0.60 → BERT primary; else heuristic keyword scan |
| `mentorship_signal` | Full resume text | NONE / IMPLIED / FORMAL / LEAD | ≥0.60 → BERT primary; else heuristic count |
| `role_family` | Resume text | 9 role classes | Metadata / DNA tag only — not a direct rubric input |
| `dna_fit` | Resume text | CONSULTING / PRODUCT / PLATFORM_INFRA / DOMAIN_SPECIALIST | Operating model tag — not a direct rubric score |
| `project_type` | Per-job description | Existing project type taxonomy | confidence ≥0.55 → auto-fills blank project_type fields |

**BERT Guard Clause** (`rubric_engine.py`, `_bert_adjust_skill_depth()`):
If BERT predicts `AWARENESS` but the parsed `evidence_level` is `APPLIED`, `DEEP`, or `EXPERT`,
BERT confidence is **hard-capped to ≤0.3** to prevent false negatives on clearly evidenced skills.

---

## LLM Judge — `rubric_engine.py` → `_llm_judge_rubric_params()`

Called **once per resume** after Python + BERT scoring. Uses `Qwen2.5:14b-instruct` via Ollama.

**What it receives** (sent as JSON user message):
```json
{
  "job_history": [{"title": "...", "company": "..."}],
  "projects": [{"title":"...", "type":"...", "skills":[], "description":"..."}],
  "achievements": ["..."],
  "strong_skills": [{"skill":"...", "evidence":"APPLIED", "years":3}],
  "deterministic_anchors": {
    "career_progression": {"score": 2.5, "max": 3},
    "international_exposure": {"score": 2.0, "max": 2},
    "stakeholder_management": {"score": 2.0, "max": 2},
    "mentorship_signal": {"score": 1.0, "max": 3},
    "awards_recognition": {"score": 0.0, "max": 3}
  }
}
```

**System prompt** (`llm_judging_assets.py` → `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT`):
```
You are a senior technical recruiter scoring a candidate on specific rubric parameters.
You receive a compact evidence summary and deterministic anchor scores from resume parsing.
Your task: review each anchor and provide a final recruiter judgment.

Parameter scoring rules:
- career_progression (max 3): Judge title trajectory. 3=strong upward (IC→Lead→Senior→Arch/Mgr).
  2=mostly upward, minor lateral. 1=flat or lateral throughout. 0.5=stagnation or regression.
- international_exposure (max 2): 2=explicit onsite assignment, global/distributed team, multi-country client.
  1=implied global context. 0=no international signals.
- stakeholder_management (max 2): 2=explicit client-facing, C-level/senior stakeholder, product owner language.
  1=cross-functional only. 0=pure IC with no external-facing evidence.
- mentorship_signal (max 3): 3=led or managed engineers in ≥2 roles with code-review or direct reports.
  2=one clear lead/owned/coached instance. 1=implied by context only. 0=no signal.
- awards_recognition (max 3): Count only genuine recognition: named awards, promotions with citation,
  patents, publications, conference presentations. Routine deliveries do NOT count.

Rules:
- Cite specific evidence from the resume in every reason
- If anchor score is reasonable and well-supported, you may confirm it — but explain why
- If anchor is wrong, correct it with evidence
- Reasons must be under 80 words
```

**What it returns**:
```json
{
  "experience": {
    "career_progression":    {"score": 3, "reason": "...", "confidence": "HIGH|MEDIUM|LOW"},
    "international_exposure":{"score": 2, "reason": "...", "confidence": "MEDIUM"},
    "stakeholder_management":{"score": 2, "reason": "...", "confidence": "MEDIUM"},
    "mentorship_signal":     {"score": 2, "reason": "...", "confidence": "LOW"},
    "awards_recognition":    {"score": 0, "reason": "...", "confidence": "HIGH"}
  }
}
```

**Merge rule** (`_merge_llm_judges()`): LLM output replaces the Python+BERT anchor only if the
JSON parses correctly and the score is within the param's `max`. If LLM call fails (timeout,
invalid JSON), original Python+BERT scores are kept unchanged.

---

## Parameter-by-Parameter Scoring

---

### EXPERIENCE SECTION — 40 pts total

Computed in: **`rubric_engine.py` → `_score_experience_section()`**
Input from: **`experience_engine.py` → `analyze_experience()`**

---

#### `overall_experience` — 3 pts
**File**: `rubric_engine.py` lines ~370–412
**Method**: Pure Python

| Condition | Points |
|-----------|--------|
| JD YoE range set AND ratio ≥100% | 3.0 |
| JD YoE range set AND ratio 70–99% | `ratio × 3` |
| JD YoE range set AND ratio <70% | Reject flag raised |
| No JD — 10+ yrs | 3.0 |
| No JD — 6–10 yrs | 2.5 |
| No JD — 4–6 yrs | 2.0 |
| No JD — 2–4 yrs | 1.5 |
| No JD — 1–2 yrs | 1.0 |
| No JD — <1 yr | 0.5 |
| No experience detected | 0.0 |

`ratio = min(total_years, yoe_max) / total_years`
**Reject flag**: raised when `ratio < 0.70` (candidate too over/under qualified vs JD range).

---

#### `career_breaks` — 2 pts
**File**: `rubric_engine.py` lines ~414–432, helper `_detect_career_breaks()`
**Method**: Pure Python

Scans `tenure_with_dates` for gaps **>3 months** between consecutive employment entries,
excluding education periods.

| Breaks found | Points |
|---|---|
| 0 | 2.0 |
| 1 | 1.0 |
| 2 | 0.0 |
| >2 | 0.0 + **Reject flag** |

---

#### `career_progression` — 3 pts
**File**: `rubric_engine.py` lines ~434–463
**Method**: Python + BERT + LLM Judge

**Step 1 — Python heuristic**:
`experience_engine.py` assigns `career_trajectory_score` (0–5) based on title-seniority sequence
(IC → Lead → Senior → Principal/Architect). Mapped: `(trajectory/5) × 3`.

**Step 2 — BERT blend** (`bert_signal_engine.py`, task: `career_progression`):

| BERT label | Raw pts | Confidence ≥0.60 | Confidence 0.40–0.59 |
|---|---|---|---|
| FAST_TRACK | 3.0 | 60% BERT + 40% heuristic | 40% BERT + 60% heuristic |
| GROWING | 2.0 | same | same |
| LATERAL | 1.0 | same | same |
| DECLINING | 0.5 | same | same |
| <0.40 conf | — | heuristic only | — |

**Step 3 — LLM Judge**: Overrides Step 2 result if LLM response parses correctly.
Prompt: `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT` (see above).

---

#### `stability` — 3 pts
**File**: `rubric_engine.py` lines ~465–481
**Method**: Python + LLM Judge

`experience_engine.py` computes `stability_score` (0–5) from:
- Average tenure per role
- Loyalty signal (HIGH/MEDIUM/LOW based on tenure distribution)
- Churn penalty for frequent short stints (<12m)

Formula: `(stability_score / 5.0) × 3`, clamped to 3.

LLM Judge can override (same `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT` call, though `stability`
is not one of the 5 judged params — **Note: stability uses Python only, not LLM-judged**).

---

#### `company_tier` — 5 pts
**File**: `rubric_engine.py` lines ~483–504
**Method**: Pure Python — `company_tier_taxonomy.py`

Each company in work history is classified using `classify_company_tier()` which checks
against a built-in taxonomy (FAANG, unicorns, known companies) and falls back to keyword
heuristics. **Best tier** (lowest number = highest tier) across full career is taken.

| Tier | Examples | Points |
|---|---|---|
| 1 | Google, Microsoft, Meta, Amazon, Apple, Netflix, etc. | 5.0 |
| 2 | Unicorn / well-funded product company | 4.0 |
| 3 | Mid-size funded / strong regional | 3.0 |
| 4 | IT services / consulting (Infosys, Wipro, TCS…) | 2.0 |
| 5 | Unknown / not in database | 1.0 |

---

#### `awards_recognition` — 3 pts
**File**: `rubric_engine.py` lines ~506–516, then overridden by LLM Judge
**Method**: Python anchor + LLM Judge

Python: counts `achievements[]` list from `experience_engine.py`.
`awards_pts = clamp(achievement_count, 0, 3)`

LLM Judge (via `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT`) **reviews and can correct** the Python count,
since it distinguishes genuine recognition (named awards, promotions with citation, patents,
publications, conference talks) from routine delivery claims. This is where the LLM adds the
most value — rejecting inflated "delivered X% improvement" type entries.

---

#### `international_exposure` — 2 pts
**File**: `rubric_engine.py` lines ~518–526, then overridden by LLM Judge
**Method**: Python anchor + LLM Judge

Python: `experience_engine.py` sets `international_exposure=True` if any role description
contains keywords: `onsite`, `global team`, `multi-country`, `relocation`, `overseas`,
`international client`, `cross-border`, `distributed team`.

Score: `2.0 if True else 0.0`

LLM Judge can set to `1.0` for implied (but not explicit) global context, which the Python
heuristic cannot distinguish.

---

#### `stakeholder_management` — 2 pts
**File**: `rubric_engine.py` lines ~528–551
**Method**: Python + BERT + LLM Judge

**Python**: `client_facing` flag from `experience_engine.py` (keyword scan for `client`,
`customer`, `stakeholder`, `business partner`). Gives 2.0 or 0.0.

**BERT blend** (`bert_signal_engine.py`, task: `stakeholder_management`):

| BERT label | Score | Applied when |
|---|---|---|
| C_LEVEL | 2.0 | confidence ≥0.60 → replaces Python |
| CLIENT_FACING | 2.0 | same |
| INTERNAL | 1.0 | same |
| NONE | 0.0 | same |
| <0.60 confidence | — | Python heuristic used |

**LLM Judge**: Final override via `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT` (can set 0, 1, or 2).

---

#### `mentorship_signal` — 3 pts
**File**: `rubric_engine.py` lines ~553–581
**Method**: Python + BERT + LLM Judge

**Python**: `leadership_signal_score` from `experience_engine.py` counts occurrences of
`lead`, `managed`, `mentored`, `coached`, `code review`, `direct reports` across role descriptions.
Score = `clamp(count, 0, 3)`.

**BERT blend** (`bert_signal_engine.py`, task: `mentorship_signal`):

| BERT label | Score | Applied when |
|---|---|---|
| LEAD | 3.0 | confidence ≥0.60 → replaces Python |
| FORMAL | 2.0 | same |
| IMPLIED | 1.0 | same |
| NONE | 0.0 | same |
| <0.60 confidence | — | Python heuristic used |

**LLM Judge**: Final override via `RUBRIC_LLM_JUDGE_SYSTEM_PROMPT`.

---

#### `project_1` — 8 pts
**File**: `rubric_engine.py` → `_score_project(..., max_score=8)`
**Input**: `experience_engine.py` → `project_types[0]` (most recent role)
**Method**: Pure Python (8 criteria, 1 pt each)

| # | Criterion | Source field | Pass condition |
|---|---|---|---|
| 1 | Project type known | `project_type` | Not empty / not UNKNOWN |
| 2 | Role/title present | `title` | Non-empty string |
| 3 | Description present | `description` (from `role_description`) | len > 20 chars |
| 4 | Sufficient duration | `start_date` + `end_date` | ≥3 months; **benefit of the doubt** if dates unparseable |
| 5 | Skills listed | `skills[]` | ≥1 skill |
| 6 | Domain tag present | `experience.domain_tags` | At least one domain tag across career |
| 7 | Role depth | `description` | len > 50 chars AND contains ownership verb* |
| 8 | Quantified impact | `description` | `%` pattern OR (number + outcome word**) OR 3+ two-digit numbers |

*Ownership verbs: `built`, `designed`, `led`, `architected`, `developed`, `owned`,
`implemented`, `optimised`, `created`, `deployed`, `delivered`, `launched`, `migrated`,
`scaled`, `managed`, `drove`, `spearheaded`, `established`, `engineered`, `integrated`,
`automated`, `transformed`, `refactored`, `streamlined`

**Outcome words: `reduced`, `increased`, `improved`, `saved`, `accelerated`, `grew`,
`delivered`, `achieved`, `deployed`, `migrated`, `scaled`, `optimized`, `automated`,
`streamlined`, `enhanced`, `boosted`, `eliminated`

---

#### `project_2` — 6 pts
**File**: `rubric_engine.py` → `_score_project(..., max_score=6)`
**Input**: `experience_engine.py` → `project_types[1]` (second most recent role)
**Method**: Pure Python (same as project_1 but **only criteria 1–6**; no criteria 7 or 8)

---

### SKILLS SECTION — 45 pts total

Computed in: **`rubric_engine.py` → `_score_skills_section()`**
Input from: **`skill_evidence_engine.py` → `collect_skill_evidence()`**

---

#### `skill_list_years` — 6 pts *(Recruiter stage — starts at 0)*
**File**: `rubric_engine.py` lines ~626–643
**Method**: Recruiter fills

At resume stage: engine counts skills with `APPLIED/DEEP/EXPERT` evidence and stores
the list in the reason/metadata for the recruiter's reference.

**Score starts at 0.** Recruiter assigns 0–6 during phone screen by validating the
claimed years per skill (1 pt per validated APPLIED+ skill with a clear timeline, max 6).

---

#### `skill_depth` — 8 pts
**File**: `rubric_engine.py` lines ~645–681
**Method**: BERT-primary (Python evidence as fallback)

**Evidence-level → raw score map** (`EVIDENCE_LEVEL_TO_SCORE`):

| Evidence level | Raw score (0–5) |
|---|---|
| MENTION | 0.5 |
| WEAK | 1.5 |
| APPLIED | 3.0 |
| DEEP | 4.0 |
| EXPERT | 5.0 |

**BERT adjustment** (`_bert_adjust_skill_depth()`):
For each of the top-5 skills (by evidence level), the BERT `skill_depth` prior is blended:
- confidence ≥0.65 → `0.65 × BERT_score + 0.35 × evidence_score`
- confidence 0.45–0.64 → `0.50 × BERT_score + 0.50 × evidence_score`
- confidence <0.45 → evidence score only

BERT guard clause: if BERT says `AWARENESS` but evidence_level is `APPLIED/DEEP/EXPERT`,
BERT confidence is hard-capped to 0.3.

**Final formula**: `(avg_blended_score_across_top5 / 5.0) × 8.0`

**With JD config**: `_apply_role_skill_weights()` applies mandatory-skill weighting,
returns a 0–10 score, rescaled to `× 8/10`.

---

#### `skill_recency` — 6 pts
**File**: `rubric_engine.py` lines ~683–699
**Method**: Pure Python

`skill_evidence_engine.py` assigns each skill a recency label:
`CURRENT` (used in last role), `RECENT` (used ≤2 yrs ago), `DATED`, `HISTORICAL`.

Formula: `(count_RECENT_or_CURRENT / total_skills) × 6`, clamped to 6.

---

#### `skills_learning_acumen` — 3 pts
**File**: `rubric_engine.py` lines ~701–718
**Method**: Pure Python

`experience_engine.py` tracks `yearly_skill_learning` (new skills added per calendar year).

| Condition | Points |
|---|---|
| `fast_learner=True` (≥2 new skills/yr for ≥2 yrs) | 3.0 |
| New skills across ≥3 different years | 2.0 |
| New skills across 1–2 years | 1.0 |
| No pattern detected | 0.0 |

---

#### `certifications` — 3 pts
**File**: `rubric_engine.py` → `compute_rubric_score()` lines ~1133–1146 *(patch)*
**Method**: Pure Python — patched from education analysis

`education_engine.py` extracts `certificates[]` list. Patched into skills section:
`clamp(cert_count, 0, 3)` — each certification = 1 pt, max 3.

---

#### `coding_community` — 3 pts
**File**: `rubric_engine.py` lines ~723–734
**Method**: Pure Python

`skill_evidence_engine.py` sets `open_source_signal=True` on skills where GitHub,
Stack Overflow, LeetCode, HackerRank, or contribution language is detected in evidence.

| OSS signals | Points |
|---|---|
| ≥3 | 3.0 |
| 2 | 2.0 |
| 1 | 1.0 |
| 0 | 0.0 |

---

#### `project_explanation` — 3 pts *(Recruiter stage — starts at 0)*
**File**: `rubric_engine.py` line ~764
**Method**: Recruiter fills

Score 0–3 based on how clearly the candidate narrates their project during phone screen:
- 3 = clear problem → design → outcome narrative
- 2 = good structure with minor gaps
- 1 = disjointed or surface-level
- 0 = cannot explain their own project

---

#### `communication_skills` — 5 pts *(Panel stage — starts at 0)*
**File**: `rubric_engine.py` lines ~767–773
**Method**: Panel fills

Panel scores verbal clarity, structure, and audience adaptability during technical interview.

---

#### `domain_skills` — 5 pts *(Panel stage — starts at 0)*
**Method**: Panel fills

Panel scores domain-specific knowledge depth via scenario questions.

---

#### `problem_solving` — 3 pts *(Panel stage — starts at 0)*
**Method**: Panel fills

Panel scores live problem-solving: systematic breakdown, edge cases, approach quality.

---

#### `mandatory_skills` — Flag only (no score)
**File**: `rubric_engine.py` lines ~736–746
**Method**: Pure Python — only active when JD config (`client_role_config`) is provided

Matches candidate's extracted skill names against `client_role_config.mandatory_skills[]`.
Returns `{matched: [...], missing: [...], match_rate: "x/y"}`.
Displayed as colored chips (green = matched, red = missing) in UI.

---

#### `good_to_have_skills` — Flag only (no score)
Same as `mandatory_skills` but for `client_role_config.good_to_have_skills[]`.

---

#### `coding_skills` — Panel qualitative (no numeric score)
Panel free-text assessment of live coding ability. Stored as `{type: "panel_text", value: ""}`.

---

#### `conceptual_skills` — Panel qualitative (no numeric score)
Panel free-text assessment of CS/domain conceptual understanding.

---

### EDUCATION SECTION — 15 pts (10 core + 5 bonus)

Computed in: **`rubric_engine.py` → `_score_education_section()`**
Input from: **`education_engine.py` → `analyze_education()`**

---

#### `institute_tier` — 5 pts
**File**: `rubric_engine.py` lines ~797–827
**Method**: Pure Python

`education_engine.py` classifies each institution using a tier taxonomy.

| Tier | Examples | Base pts | GPA bonus |
|---|---|---|---|
| TIER_1 | IIT, NIT, IIM, global top-50 | 4.0 | +1.0 if GPA=EXCELLENT/GOOD |
| TIER_2 | Well-regarded regional university | 3.0 | +0.5 if GPA=EXCELLENT |
| TIER_3 | Mid-tier institution | 2.0 | none |
| TIER_4 | Below-average tier | 1.0 | none |
| UNKNOWN | Not in database | 1.0 | none |

Capped at 5.0.

---

#### `degree_level` — 2 pts
**File**: `rubric_engine.py` lines ~829–842
**Method**: Pure Python

Takes the **highest** degree level across all education entries.

| Level | Points |
|---|---|
| PhD | 2.0 |
| Master | 2.0 |
| Bachelor | 1.5 |
| Diploma | 1.0 |
| Unknown | 0.5 |

---

#### `education_job_relevance` — 2 pts
**File**: `rubric_engine.py` lines ~844–862
**Method**: Pure Python

`education_engine.py` computes `strongest_course_value_signal` by mapping degree fields
(CS, CE, IT, Data Science → HIGH; Science, Management → MEDIUM; Arts, Law → FOUNDATIONAL).

| Signal | Points |
|---|---|
| HIGH | 2.0 |
| MEDIUM | 1.5 |
| FOUNDATIONAL | 0.5 |
| UNKNOWN | 1.0 (neutral) |

---

#### `education_gap` — 1 pt
**File**: `rubric_engine.py` lines ~864–877
**Method**: Pure Python

Gap = months between last education end date and first job start date.

| Gap | Points |
|---|---|
| ≤6 months | 1.0 |
| 6–12 months | 0.5 |
| >12 months | 0.0 + **Reject flag** |

---

#### `exec_education` — 1 pt *(Bonus)*
**File**: `rubric_engine.py` lines ~882–894
**Method**: Pure Python

Scans education entries for keywords: `executive`, `continuing`, `distance`,
`certification`, `online`, `mooc`. Match = 1.0, no match = 0.0.

---

#### `patents_publications` — 2 pts *(Bonus — patched)*
**File**: `rubric_engine.py` → `compute_rubric_score()` lines ~1148–1160
**Method**: Pure Python — patched from experience analysis

Checks `experience.patents[]` list OR scans `achievements[]` for the string `"patent"`.
Boolean: `1 = 2.0 pts`, `0 = 0.0 pts`.

---

#### `linkedin_activity` — 1 pt *(Bonus — Recruiter stage)*
**File**: `rubric_engine.py` line ~900
**Method**: Recruiter fills

Recruiter checks LinkedIn profile during screening.
1 = active professional presence, 0 = absent or inactive.

---

#### `extra_curriculars` — 1 pt *(Bonus — Recruiter stage)*
**File**: `rubric_engine.py` line ~903
**Method**: Recruiter fills

Recruiter confirms extra-curricular activities during phone screen.
1 = confirmed activities (sports, volunteering, open source, community), 0 = none.

---

## Reject Flags

Raised by `_score_experience_section()` and `_score_education_section()`.
A flag scores the parameter at 0 AND marks the candidate for recruiter review.

| Condition | Flag key |
|---|---|
| Career breaks > 2 (each >3 months) | `career_breaks` |
| Education-to-job gap > 12 months | `education_gap` |
| Relevant experience ratio < 70% vs JD YoE range | `overall_experience` |

---

## File Reference Summary

| File | Role |
|---|---|
| `pdf_to_json_extractor.py` | PDF/DOCX → structured JSON extraction |
| `engine.py` | Main pipeline orchestrator, field normalization |
| `experience_engine.py` | Tenure, stability, progression, projects, achievements |
| `skill_evidence_engine.py` | Per-skill evidence level, recency, OSS signal |
| `education_engine.py` | Institute tier, GPA, gap, degree level, relevance |
| `dna_engine.py` | Operating model classification (CONSULTING/PRODUCT/HYBRID) |
| `company_tier_taxonomy.py` | Company tier lookup (FAANG → unknown) |
| `bert_signal_engine.py` | 7-task BERT inference, confidence-weighted blending |
| `rubric_engine.py` | All 30+ param scoring logic, BERT merge, LLM judge merge |
| `llm_judging_assets.py` | All LLM system prompts (judge, recruiter narrative, client fit) |
| `llm_recruiter_analysis.py` | Recruiter brief narrative generation (separate LLM call) |
| `interview_question_engine.py` | Per-param interview question templates |
| `candidate_score_store.py` | Save/load stage scores per candidate |
| `app.py` | FastAPI server, UI, `/resumeParse`, `/updateStageScore` |

---

*Generated from: rubric_engine.py, bert_signal_engine.py, llm_judging_assets.py,
experience_engine.py, education_engine.py, skill_evidence_engine.py*
