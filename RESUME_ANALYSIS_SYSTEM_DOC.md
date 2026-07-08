# Resume Analysis System Doc

## 1. Purpose

This system has two layers:

1. Deterministic resume extraction and evidence building
2. AI-based judgment for scoring, narrative analysis, and recruiter summary

The design goal is:

- keep extraction deterministic and schema-first
- use AI only for judgment, interpretation, and recruiter-facing reasoning
- never present fallback math as final hiring judgment

---

## 2. Model Routing

The system supports task-based model routing through Ollama.

Current environment defaults in [`.env`](/E:/Dev/resume_intelligence/.env):

- `LLM_PROVIDER=ollama`
- `OLLAMA_MODEL=qwen2.5:14b-instruct`
- `OLLAMA_SCORING_MODEL=qwen2.5:14b-instruct`
- `OLLAMA_ANALYSIS_MODEL=qwen2.5:14b-instruct`
- `OLLAMA_SUMMARY_MODEL=qwen2.5:14b-instruct`

### 2.1 Which model does what

1. Scoring model
   File: [scoring_engine.py](/E:/Dev/resume_intelligence/scoring_engine.py)
   Function: recruiter-style score judgment
   Route: `scoring_model(...)`
   Output:
   - `skill_score`
   - `experience_score`
   - `role_alignment_score`
   - `impact_score`
   - `stability_score`
   - `dna_score`
   - `overall_band`
   - `benchmark_summary`
   - `benchmark_definition`
   - `rationale`
   - `component_rationales`

2. Analysis model
   File: [llm_resume_judge.py](/E:/Dev/resume_intelligence/llm_resume_judge.py)
   Function: semantic interpretation, qualitative analysis, top-skill judgment
   Route: `analysis_model(...)`
   Output:
   - `semantic_analysis`
   - `qualitative_analysis`
   - `top_skill_judgments`

3. Summary model
   File: [llm_recruiter_analysis.py](/E:/Dev/resume_intelligence/llm_recruiter_analysis.py)
   Function: recruiter summary paragraph
   Route: `summary_model(...)`
   Output:
   - recruiter-ready hiring note

### 2.2 Why this routing exists

Different tasks need different behavior:

- scoring needs strict JSON and dimension-level discipline
- analysis needs richer reasoning and recruiter language
- summary needs concise business writing

Even if we use one model for all three right now, the code is ready for different specialist models later.

---

## 3. End-to-End Flow

### Step 1. Resume ingestion

Entry:
- [app.py](/E:/Dev/resume_intelligence/app.py)
- endpoint: `/resumeParse`

What happens:
- uploaded JSON is read
- wrapped into [models.py](/E:/Dev/resume_intelligence/models.py) `ResumeInput`
- passed to [engine.py](/E:/Dev/resume_intelligence/engine.py) `analyze_resume(...)`

### Step 2. Normalization

File:
- [engine.py](/E:/Dev/resume_intelligence/engine.py)

Function:
- `normalize_resume_data(...)`

What it extracts and saves:
- name
- email
- phone
- location
- profile summary
- competencies
- technical skills
- work experience
- education
- certificates
- patents
- achievements
- extracurricular activities
- domain hints

If profile summary is missing:
- system generates a deterministic summary from title, skills, company hints, and domain hints

### Step 3. Skill evidence extraction

File:
- [evidence.py](/E:/Dev/resume_intelligence/evidence.py)

What it determines for each skill:
- canonical skill name
- cluster
- evidence level
  - `NONE`
  - `MENTION`
  - `WEAK`
  - `APPLIED`
  - `DEEP`
  - `EXPERT`
- depth label
  - `AWARENESS`
  - `FOUNDATIONAL`
  - `HANDS_ON`
  - `ADVANCED`
  - `ARCHITECT_LEVEL`
- weighted evidence tenure
- raw matched tenure
- recency
- matched context count
- project contexts
- architecture signal
- coding signal
- open source signal
- attributed roles

Important rule:
- tenure is weighted by evidence quality
- this prevents one weak mention in a long role from looking like deep experience

### Step 4. Semantic taxonomy

File:
- [semantic_taxonomy.py](/E:/Dev/resume_intelligence/semantic_taxonomy.py)

What it produces:
- `cluster_map`
- `role_family_scores`
- `top_role_family`
- `skill_consistency_score`
- `inferred_skills`

Role families are defined in:
- [taxonomy.py](/E:/Dev/resume_intelligence/taxonomy.py)

Examples:
- `ML_ENGINEER`
- `AI_ARCHITECT`
- `NLP_LLM_ENGINEER`
- `CORE_DATA_SCIENTIST`
- `DATA_ENGINEER`

### Step 5. Experience analysis

File:
- [experience_engine.py](/E:/Dev/resume_intelligence/experience_engine.py)

What it extracts:
- total experience
- titles
- companies
- progression
- same-company growth
- client-facing exposure
- international exposure
- business impacts
- project types
- complexity signal
- leadership signal
- ownership signal
- problem-solving signal
- decision-maker flag
- fast learner flag
- stability score
- company profiles
- domain tags
- dominant operating model
- relocation flexibility signal

### Step 6. DNA fit

File:
- [dna_engine.py](/E:/Dev/resume_intelligence/dna_engine.py)

What it classifies:
- `CONSULTING`
- `PRODUCT`
- `HYBRID`
- `DOMAIN_SPECIALIST`

### Step 7. AI scoring

File:
- [scoring_engine.py](/E:/Dev/resume_intelligence/scoring_engine.py)

This is the intended final scoring authority.

It sends the following evidence to the scoring model:

1. Experience stage
- `0-3 Years`
- `3-6 Years`
- `6-10 Years`
- `10+ Years`

2. Top role candidates
- top role family
- top role family scores

3. Skill evidence
- top skill list
- evidence level
- depth label
- weighted tenure
- recency
- context count
- project contexts
- evidence reasons
- sample supporting contexts

4. Experience evidence
- years
- complexity
- leadership
- decision-maker
- client-facing
- international exposure
- progression
- fast learner
- quantified impacts

5. Semantic evidence
- cluster map
- consistency score
- inferred strengths

6. DNA evidence
- consulting/product/hybrid orientation

### Step 8. AI semantic and qualitative analysis

File:
- [llm_resume_judge.py](/E:/Dev/resume_intelligence/llm_resume_judge.py)

The analysis model decides:

1. Semantic analysis
- recruiter summary
- top role family
- role-family rationale
- consistency readout
- inferred strength areas

2. Qualitative analysis
- strengths
- gaps
- risk flags
- panel suggestion
- recommendation

3. Top skill judgments
- `skill`
- `verdict_label`
- `confidence`
- `reason`
- `interview_probe`

### Step 9. Recruiter summary generation

File:
- [llm_recruiter_analysis.py](/E:/Dev/resume_intelligence/llm_recruiter_analysis.py)

The summary model writes:
- overall fit
- technical depth
- experience and impact
- DNA fit
- risks and validation questions
- suggested interview panel

If AI summary is invalid:
- deterministic recruiter summary is used

### Step 10. Telephonic questions

File:
- [telephonic_question_engine.py](/E:/Dev/resume_intelligence/telephonic_question_engine.py)

Important rule:
- telephonic script is only shown when AI scoring succeeded
- fallback score alone does not trigger recruiter action

---

## 4. How scoring is determined

### 4.1 Final intended scoring source

Final recruiter score should come from AI only.

That means:
- `llm_used=true`
- structured JSON passed validation
- component rationales present

Only then should the app treat the score as final recruiter judgment.

### 4.2 Score dimensions

The scorecard contains:

1. `Skill Strength`
What AI should judge:
- credibility of skill ownership
- relevance to role
- maturity of usage
- recency
- whether evidence is delivery-grade vs exposure-grade

2. `Experience`
What AI should judge:
- decision latitude
- complexity arc
- ownership
- delivery maturity
- whether seniority claim matches resume evidence

3. `Role Alignment`
What AI should judge:
- natural fit to the most plausible role family
- not just keyword overlap

4. `Impact`
What AI should judge:
- quantified business outcome
- value delivered
- stakeholder effect
- outcome credibility

5. `Stability`
What AI should judge:
- tenure consistency
- trajectory
- growth
- whether the career path feels dependable

6. `DNA`
What AI should judge:
- consulting vs product vs hybrid vs specialist operating style

### 4.3 Experience-stage calibration

AI does not score against a universal bar.
It is expected to judge relative to:

- `0-3 Years`
- `3-6 Years`
- `6-10 Years`
- `10+ Years`

This matters because:
- strong ownership for 3-6 years is different from strong leadership for 10+ years

---

## 5. What happens when AI scoring fails

If AI scoring fails:
- the system falls back to deterministic scoring in [scoring_engine.py](/E:/Dev/resume_intelligence/scoring_engine.py)
- but recruiter UI should not treat it as final hiring judgment

Common failure reasons:
- local model returns empty content
- local model returns invalid JSON
- local model returns partial JSON
- local model fails validation

The app now exposes:
- `llm_provider`
- `llm_failure_reason`

So the recruiter can see why AI judgment did not apply.

---

## 6. Why fallback exists

Fallback exists for system continuity only.

It is useful for:
- debugging
- smoke testing
- temporary fail-safe behavior

It is not intended to be:
- final recruiter judgment
- final telephonic gating
- final panel decision basis

---

## 7. Current deterministic extraction parameters

These are not meant to be final hiring judgment, but they do provide evidence:

### Skill evidence parameters

From [evidence.py](/E:/Dev/resume_intelligence/evidence.py):

- alias match
- action verbs
- advanced topic signals
- architecture language
- coding language
- open-source language
- weighted tenure
- raw tenure
- recency
- matched contexts
- project type

### Experience parameters

From [experience_engine.py](/E:/Dev/resume_intelligence/experience_engine.py):

- progression
- same-company growth
- client-facing
- international exposure
- business impact markers
- complexity terms
- leadership terms
- ownership terms
- problem-solving terms
- decision-maker eligibility for 6+ years
- fast learner signal
- stability score
- company context
- domain tags

### Taxonomy parameters

From [taxonomy.py](/E:/Dev/resume_intelligence/taxonomy.py) and [semantic_taxonomy.py](/E:/Dev/resume_intelligence/semantic_taxonomy.py):

- role family weights
- must-have skill presence
- cluster coverage
- title bonuses
- inferred strengths

---

## 8. Recruiter-facing output components

Target recruiter view:

1. AI insight narrative
- candidate identity
- strongest role fit
- one watch-out

2. Experience depth
- ownership
- impact
- complexity
- cross-functional scope
- growth

3. Skill depth map
- expert / deep / applied / surface
- recency
- evidence reason

4. Leadership intelligence
- people
- influence
- critical thinking
- collaboration
- thought leadership

5. Intent score
- not fully implemented yet
- planned to combine external activity and inferred motivation

6. Overall bucket
- should only be final when AI scoring succeeds

7. Probe card
- telephonic questions generated from weak or ambiguous areas

---

## 9. Current limitations

1. Local `1b` models are too weak for reliable structured recruiter scoring
2. Fallback extraction still uses heuristics and needs continuous tuning
3. Education intelligence is not yet fully built
4. Employer tier intelligence is still light
5. Intent scoring is not yet implemented
6. True 90%+ quality is not provable without benchmark evaluation

---

## 10. Recommended next milestones

### Milestone 1. Strong local AI path

- use `qwen2.5:14b-instruct` locally
- keep schema-first JSON enforcement
- validate scoring response before displaying it

### Milestone 2. Education engine

Build:
- education gap detection
- education-to-first-job gap
- institute tiering
- degree relevance
- PG upgrade signal
- publications check

### Milestone 3. Leadership engine

Build explicit sub-signals for:
- people management
- influence
- critical thinking
- collaboration
- thought leadership

### Milestone 4. Feedback moat

Store post-touchpoint outcomes:
- recruiter corrections
- telephonic answers
- shortlist/reject decisions
- client interview results
- offer/reject
- join/no-join

That feedback loop is the real moat.
Parsing is only the foundation.

---

## 12. Feedback mechanism

The system now includes a local feedback capture endpoint.

Endpoint:
- `/feedback`

Schema:
- `candidate_name`
- `source_file`
- `role_family_shown`
- `recruiter_decision`
- `recruiter_bucket`
- `corrected_role_family`
- `corrected_score`
- `corrected_band`
- `strengths_confirmed`
- `skills_needing_correction`
- `gaps_confirmed`
- `call_outcome`
- `interview_outcome`
- `joined`
- `notes`
- `raw_analysis`

Storage:
- local JSONL file at [feedback_data/resume_feedback.jsonl](/E:/Dev/resume_intelligence/feedback_data/resume_feedback.jsonl)

Why this matters:

1. It creates a gold dataset of recruiter corrections.
2. It lets us measure where AI or fallback judgment is wrong.
3. It creates training/evaluation material for future LoRA or fine-tuning.
4. It makes the system improve from actual hiring outcomes instead of only resume heuristics.

Important truth:

This does not automatically retrain the model in real time.
It enables the right dataset collection pipeline so we can later:

- benchmark prompts
- build evaluation sets
- tune thresholds
- fine-tune an open-source model
- compare model versions against recruiter-corrected outcomes

---

## 11. Practical truth

This system becomes strong when:

- extraction is deterministic
- AI is used as the judge
- invalid AI output is rejected
- recruiter UI only trusts validated AI judgment
- post-call outcomes are stored and used to improve future judgment

That is the path toward a proprietary and genuinely high-quality recruiter intelligence engine.
