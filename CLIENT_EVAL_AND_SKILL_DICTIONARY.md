# Resume Intelligence
## Evaluation Framework And Skill Dictionary

### Purpose

This document explains, in business language, two important parts of the Resume Intelligence system:

1. The evaluation framework used to assess whether the analysis engine is behaving reliably and consistently.
2. The skill dictionary and evidence model used to interpret resume skills with more depth than simple keyword matching.

The goal is to make the system understandable for client review, governance, and future calibration discussions.

---

## 1. Evaluation Framework

### Why an evaluation framework is needed

A resume analysis engine should not be judged only by whether it produces output. It should also be judged by whether:

- the output is internally consistent
- the system uses AI successfully when expected
- the reasoning is specific rather than generic
- scores are explainable
- role and skill conclusions are directionally correct for known examples

The evaluation framework gives us a repeatable way to measure this.

### What the evaluation framework does

The evaluation framework runs one resume JSON file, or an entire folder of resume JSON files, through the same analysis pipeline used by the application.

For each resume, it captures:

- candidate name
- detected role family
- final score and band
- whether LLM scoring succeeded
- whether LLM skill judgment succeeded
- whether component scores add up correctly to the total score
- whether score justifications are present
- any failure reason if AI scoring or AI skill judgment did not succeed

At the end, it produces:

- a summary view for the full test set
- a case-by-case diagnostic report

### What the evaluation framework measures

The current framework measures the following:

#### 1. LLM score success rate

This tells us how often the scoring LLM completed successfully and produced a usable scorecard.

Why it matters:

- shows model reliability
- helps detect prompt or provider instability
- helps compare models over time

#### 2. LLM skill judgment success rate

This tells us how often the system successfully generated recruiter-style skill judgments instead of falling back to deterministic text.

Why it matters:

- shows whether the skill reasoning layer is robust
- helps identify whether skill prompts are too large or too brittle

#### 3. Score consistency rate

This checks whether the weighted component scores add up exactly to the final total score.

Why it matters:

- prevents mismatches between displayed component scores and total score
- improves trust in the scoring UI
- ensures the scoring logic remains mathematically coherent

#### 4. Missing justification checks

This checks whether each score dimension has:

- a short rationale
- justification notes
- deeper structured justification:
  - strongest evidence
  - main gap
  - why the score was not lower

Why it matters:

- improves recruiter confidence
- supports explainability in client reviews
- makes calibration discussions easier

#### 5. Optional expectation matching

The framework can also compare system results against a simple expectation file.

For example, for selected resumes we can specify:

- expected role family
- expected score band
- minimum score
- maximum score

Why it matters:

- helps validate whether the engine behaves sensibly on benchmark resumes
- provides a practical regression test after prompt, model, or rules changes

### How the evaluation framework is used

The evaluation framework is useful in three common situations:

#### A. Before and after system changes

When prompts, models, taxonomies, or rules are changed, the framework can be run before and after the change to compare:

- score consistency
- AI success rates
- role-family outcomes
- missing justification counts

#### B. During client calibration

If the client has sample resumes with known expected outcomes, those can be added to the expectation file to assess whether the engine is aligning with recruiter judgment.

#### C. For production monitoring

Over time, the evaluation framework can be used to:

- detect drift in AI reliability
- detect increasing fallback behavior
- monitor score explainability coverage

### Output structure

The evaluation framework produces:

#### Summary

A high-level overview across all tested resumes, including:

- total number of resumes evaluated
- average score
- LLM scoring success rate
- LLM skill judgment success rate
- score consistency rate
- counts of missing justifications
- expectation match rate, if expectations were provided

#### Case-level details

A per-resume record containing:

- file name
- candidate name
- role family
- band
- total score
- whether AI scoring was used
- whether AI skill judgment was used
- whether score math is consistent
- missing justification fields
- score failure reason
- skill judgment failure reason
- optional expectation results

### Why this matters to the client

This framework gives the client confidence that the system is not just producing output, but is being evaluated against:

- reliability
- mathematical consistency
- explainability
- benchmark expectations

It creates a foundation for controlled improvement rather than ad hoc prompt changes.

---

## 2. Skill Dictionary And Evidence Model

### Why a skill dictionary is needed

Most resume systems only do keyword matching. That creates weak analysis because it does not distinguish between:

- a skill that is only listed
- a skill used once in a support project
- a skill used repeatedly in recent development work
- a skill used in architecture or leadership contexts

The skill dictionary is designed to solve this by combining:

- canonical skill mapping
- role-aware grouping
- duration evidence
- recency
- project context
- coding signal
- architecture signal
- certification or artifact evidence
- recruiter-style reasoning

### What the skill dictionary contains

The skill dictionary groups resume skills into structured buckets aligned with recruiter and engineering use cases.

The current skill categories include:

#### Core structured skill buckets

- programming_languages
- frameworks_and_libraries
- tools_and_platforms
- databases
- cloud_and_infra
- soft_skills
- certified_skills

These are the primary client-facing skill groups derived from extracted resume structure.

#### Canonical capability clusters used for reasoning

The system also maps skills into broader capability clusters so that it can reason about role fit, adjacency, and technical depth. These include:

- PROGRAMMING
- STATISTICS_ML
- DEEP_LEARNING_GENAI
- BIG_DATA
- CLOUD_INFRA
- MLOPS_DEPLOYMENT
- VISUALIZATION_BI
- DATA_MANAGEMENT
- SYSTEMS_ARCHITECTURE
- PRODUCT_ANALYTICS
- EXPERIMENTATION_RCA
- role-specific domain clusters such as retail, marketing, finance, and supply chain

This layered approach lets the system reason both narrowly at skill level and broadly at role-family level.

### What the system identifies for each skill

For each meaningful skill in a resume, the engine tries to identify the following:

#### 1. Skill detection

The system identifies:

- explicitly listed skills
- skills found in role descriptions
- normalized skill aliases

Example:

- “power bi”, “PowerBI”, and “dashboard creation” can be connected under a canonical BI skill family if the evidence supports it

#### 2. Skill duration

The engine estimates:

- raw duration of use
- weighted duration of credible use

Weighted duration is used because not every mention is equally strong. Direct implementation counts more than a passing mention.

#### 3. Skill recency

The engine marks whether the evidence is:

- RECENT
- MID
- OLD
- UNKNOWN

This matters because recent evidence is more relevant than older evidence, especially for fast-moving domains such as AI, GenAI, MLOps, and cloud.

#### 4. Skill depth

The engine estimates the practical level of the skill:

- AWARENESS
- FOUNDATIONAL
- HANDS_ON
- ADVANCED
- ARCHITECT_LEVEL

This is not based on keyword counts alone. It is influenced by:

- repeated role evidence
- usage duration
- evidence strength
- coding or implementation signals
- architecture signals
- recency

#### 5. Coding strength signal

The system checks whether the skill appears in a context suggesting real coding or implementation.

It looks for signals such as:

- built
- developed
- implemented
- script
- code
- API
- pipeline

This helps answer a client question such as:

"Does the candidate really work hands-on, or just know the concept?"

#### 6. Architecture signal

The system separately checks for design and architecture evidence.

Examples include:

- system design
- architecture
- scalable systems
- production architecture
- migration design

This helps distinguish:

- development-only skill usage
- architecture-level responsibility

#### 7. Project context

The engine classifies the context in which the skill appears, such as:

- DEVELOPMENT
- MAINTENANCE_SUPPORT
- UNKNOWN

This matters because the same skill carries different strength depending on where it was used.

In general:

- development work is stronger evidence of ownership and active application
- maintenance or support work may still be valuable, but usually signals less depth than build-oriented delivery

#### 8. Open-source signal

The engine looks for open-source style indicators such as:

- GitHub
- open source
- contributor
- pull request

This is used as an additional quality signal, not as a mandatory requirement.

#### 9. Upskill signal

The engine tries to detect whether the candidate appears to have continued building the skill over time.

This can be inferred from:

- recent evidence
- repeated appearances across roles
- certifications or artifacts
- timeline progression

This is useful for identifying learning velocity and current relevance.

#### 10. Artifact evidence

The engine checks for supporting evidence tied to the skill, such as:

- certifications
- patents
- achievements

This does not automatically prove strong depth, but it strengthens credibility when aligned with actual role usage.

### How evidence strength is determined

The engine currently classifies evidence into the following levels:

- NONE
- MENTION
- WEAK
- APPLIED
- DEEP
- EXPERT

This level is inferred from:

- skill alias matches
- action verbs
- advanced-topic signals
- context richness

Examples:

- a simple listing may count as MENTION
- a role description with implementation verbs may count as APPLIED
- repeated advanced context with stronger delivery language may count as DEEP or EXPERT

### Why the skill dictionary is stronger than a normal keyword list

The value of the skill dictionary is not just that it knows skill names. It knows how to reason about:

- what the skill is
- where it was used
- how recently it was used
- how strongly it was evidenced
- whether it appears in coding or architecture contexts
- whether the candidate seems to be building on it over time

This produces much richer recruiter analysis than simple search-based systems.

---

## 3. How Skills Connect To Role Scoring

The skill dictionary does not stand alone. It feeds into:

- top skill evidence
- semantic taxonomy
- role-family fit
- score breakdown
- telephonic round questions

For example:

- strong Spark, PySpark, ETL, CI/CD, and cloud evidence may strengthen a Data Engineer or Analytics Engineer fit
- strong Prompt Engineering, RAG, LangChain, and architecture evidence may strengthen an NLP/LLM Engineer fit
- strong Dashboarding, SQL, Power BI, and KPI work may strengthen Analytics or BI-oriented fits

This means skills are not only detected, but interpreted within role context.

---

## 4. Explainable Score Justification

### Why score justification matters

A score without explanation is hard to trust.

The system now aims to justify each score dimension using three layers:

#### A. Short component rationale

A concise recruiter-style sentence for the score dimension.

Example:

- "Strong repeated engineering evidence, but architecture breadth is still selective."

#### B. Justification notes

Short support notes to highlight the most important evidence or limitation.

#### C. Structured score justification

For each dimension, the system can now provide:

- strongest_evidence
- main_gap
- why_not_lower

This allows the UI to show a simple score at first, then expand into deeper justification when needed.

### Why this is valuable

This gives recruiters and clients a transparent explanation of:

- why the score is at its current level
- what held it back from being higher
- what prevents it from being lower

This is especially important for:

- hiring managers
- client reviewers
- model calibration workshops
- audit and governance discussions

---

## 5. Client Value Summary

### What this framework gives the client

The combination of the evaluation framework and the skill dictionary gives the client:

- a measurable way to assess model quality
- a richer interpretation of resume skills
- stronger explainability for scores
- better recruiter usability
- a foundation for controlled calibration over time

### In simple terms

The system is moving from:

- "I found these keywords"

to:

- "I found these skills, measured how strongly they are evidenced, checked how recent they are, understood whether they were used in development or support contexts, and explained why the candidate earned this score."

That is the level of reasoning needed for a recruiter-grade resume intelligence product.

---

## 6. Suggested Next Phase

For a stronger client-ready rollout, the next recommended enhancements are:

1. Add benchmark expectation files for known good and known weak resumes.
2. Add client-approved calibration cases by role family.
3. Expand the evaluation framework to include:
   - role-family precision by benchmark set
   - justification coverage score
   - fallback rate by provider/model
4. Expand education and leadership evaluation into the same structured explainability format.
5. Add downloadable evaluation summaries for stakeholder review.

---

## 7. Operational Note

The evaluation framework is designed for repeat use. It can be run:

- before and after scoring changes
- before and after LLM provider changes
- before client demos
- during calibration workshops

This makes the system easier to improve in a controlled and measurable way.
