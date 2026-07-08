# Resume Intelligence — Scoring Documentation

## Overview

Every resume is scored on a **100-point rubric** across 3 sections:

| Section | Max | Stage |
|---------|-----|-------|
| Experience | 40 | Resume auto + Recruiter validates |
| Skills | 45 | Resume auto + Recruiter + Panel |
| Education | 15 | Resume auto + Recruiter validates |
| **Total** | **100** | |

Scoring happens in **3 stages**:
- **Resume stage** — automatic (Python + BERT + LLM Judge)
- **Recruiter stage** — human fills 4 params after phone screen (+11 pts max)
- **Panel stage** — human fills 3 params after technical interview (+13 pts max)

---

## Scoring Methods Used

| Method | What it means |
|--------|--------------|
| **Pure Python** | Deterministic heuristic from parsed resume fields (dates, titles, keywords, counts) |
| **Python + BERT** | Python heuristic blended with a trained BERT classifier signal |
| **Python + LLM Judge** | Python score used as anchor; LLM reviews evidence and can accept or correct it |
| **Python + BERT + LLM** | All three: Python heuristic → BERT blend → LLM final review |
| **Recruiter / Panel** | Starts at 0; human assigns score after discussion |

---

## How BERT Is Used

BERT classifiers are trained on 7 tasks and run **before** the rubric engine. They produce **priors** (predicted label + confidence score) that are blended into 4 rubric parameters:

### BERT Tasks and Their Rubric Impact

| BERT Task | Classes | Rubric Param Impacted | How It's Used |
|-----------|---------|----------------------|---------------|
| `skill_depth` | AWARENESS / FOUNDATIONAL / HANDS_ON / ADVANCED / ARCHITECT_LEVEL | **skill_depth** (primary) | Confidence ≥0.65 → 65% BERT + 35% evidence. Confidence 0.45–0.64 → 50/50 blend. <0.45 → evidence only. |
| `career_progression` | DECLINING / LATERAL / GROWING / FAST_TRACK | **career_progression** | Confidence ≥0.60 → 60% BERT + 40% heuristic. 0.40–0.59 → 50/50. <0.40 → heuristic only. |
| `stakeholder_management` | NONE / INTERNAL / CLIENT_FACING / C_LEVEL | **stakeholder_management** | Confidence ≥0.60 → BERT primary. Else heuristic (keyword scan) only. |
| `mentorship_signal` | NONE / IMPLIED / FORMAL / LEAD | **mentorship_signal** | Confidence ≥0.60 → BERT primary. Else heuristic (lead/managed/mentored count) only. |
| `role_family` | 9 role classes | DNA / role fit | Used for semantic role family classification, not directly in rubric score. |
| `dna_fit` | CONSULTING / PRODUCT / PLATFORM_INFRA / DOMAIN_SPECIALIST | DNA tag | Used for operating model tag, not directly in rubric score. |
| `project_type` | Existing project tags | project type labels | Auto-fills blank project_type fields if confidence ≥0.55. |

### BERT Guard Clause
If BERT predicts **AWARENESS** but the parsed `evidence_level` is APPLIED / DEEP / EXPERT, BERT confidence is downweighted to ≤0.3 to prevent false negatives on clearly evidenced skills.

---

## How the LLM Judge Works

After the Python + BERT pass, an LLM (Mistral/Qwen) reviews **5 experience parameters** using the resume's evidence summary as context:

| Param | LLM Scoring Scale |
|-------|------------------|
| career_progression | 3=strong upward (IC→Lead→Senior→Arch), 2=mostly upward, 1=flat/lateral, 0.5=stagnation |
| international_exposure | 2=explicit onsite/global team, 1=implied global context, 0=none |
| stakeholder_management | 2=explicit client-facing/C-level, 1=cross-functional only, 0=pure IC |
| mentorship_signal | 3=led engineers ≥2 roles, 2=one clear instance, 1=implied, 0=none |
| awards_recognition | Count only: named awards, promotions with citation, patents, publications, conference talks |

**LLM receives**: job history, top 3 projects, top 5 achievements, top 20 APPLIED+ skills, and the deterministic anchor scores as a starting point.
**LLM returns**: `{score, reason, confidence: HIGH/MEDIUM/LOW}` per parameter.
**Merge rule**: LLM output replaces the Python anchor only if the response parses correctly.

---

## Parameter-by-Parameter Breakdown

---

### EXPERIENCE (40 pts)

| Parameter | Excel Name | Max | Method | Stage | How Scored |
|-----------|-----------|-----|--------|-------|-----------|
| `overall_experience` | Overall Experience / Relevant Experience | 3 | Pure Python | resume | **With JD YoE range**: `ratio = min(total_yrs, yoe_max) / total_yrs × 3`. Reject flag if <70%. **Without JD**: band score — 10+yrs=3, 6-10=2.5, 4-6=2, 2-4=1.5, 1-2=1, <1=0.5 |
| `career_breaks` | Career Breaks | 2 | Pure Python | resume | Parse all employment date gaps >3 months. 0 breaks=2pts, 1 break=1pt, 2 breaks=0pts. >2 breaks → REJECT FLAG |
| `career_progression` | Career Progression | 3 | **Python + BERT + LLM** | resume | Heuristic: title seniority trajectory (IC→Lead→Sr→Arch) scored 0–5. BERT prior blended at confidence thresholds. LLM can override. Final scaled to 0–3 |
| `stability` | Stability | 3 | Python + LLM | resume | Heuristic stability score (0–5) from tenure analysis + loyalty signal. Scaled (raw/5) × 3. LLM can override |
| `company_tier` | Companies Worked With | 5 | Pure Python | resume | Classify each company: Tier1(FAANG)=5, Tier2(Unicorn)=4, Tier3(Mid)=3, Tier4(Services)=2, Unknown=1. Take best tier from career history |
| `awards_recognition` | Awards & Recognitions | 3 | **Python + LLM** | resume | Python: count achievements/commendations (0–3). LLM validates whether claims are genuine recognition vs. marketing language |
| `international_exposure` | International Exposure | 2 | **Python + LLM** | resume | Python: scan for "onsite", "global team", "multi-country", "relocation" → 2 or 0. LLM reviews for implied vs explicit signals |
| `stakeholder_management` | Stakeholder Management | 2 | **Python + BERT + LLM** | resume | Python: scan for "client", "customer", "stakeholder" language. BERT: C_LEVEL/CLIENT_FACING/INTERNAL/NONE. LLM final review |
| `mentorship_signal` | Mentorship / Code Reviews / Interviews | 3 | **Python + BERT + LLM** | resume | Python: count lead/managed/mentored instances. BERT: LEAD/FORMAL/IMPLIED/NONE. LLM final review |
| `project_1` | Project 1 — Latest Project | 8 | Pure Python | resume | 8 criteria, 1pt each: (1) project_type known, (2) title present, (3) description >20 chars, (4) duration ≥3m, (5) ≥1 skill listed, (6) domain tag present, (7) description >50 chars + ownership verb (built/led/owned/designed/architected/implemented/optimised), (8) quantified impact (number/% near outcome word: reduced/improved/grew/saved) |
| `project_2` | Project 2 — 2nd Latest Project | 6 | Pure Python | resume | Same as project_1 but only 6 criteria (excludes criteria 7 and 8) |

**Experience Total Max at Resume Stage**: 40 pts
*(All 11 params auto-scored. LLM judgment is applied to 5 of them as a refinement pass.)*

---

### SKILLS (45 pts)

| Parameter | Excel Name | Max | Method | Stage | How Scored |
|-----------|-----------|-----|--------|-------|-----------|
| `skill_list_years` | Skill List — Years of Experience / Timeline | 6 | **Recruiter fills** | recruiter (0 at resume) | Resume extracts skill list with evidence levels for recruiter reference. Recruiter validates the timeline during screening and assigns 0–6 (each validated APPLIED+ skill with clear years = 1pt) |
| `skill_depth` | Skill Depth | 8 | **BERT-primary** | resume | Primary BERT task. Top 5 skills scored: evidence_level → 0–5 raw (MENTION=0.5, WEAK=1.5, APPLIED=3, DEEP=4, EXPERT=5). BERT prior blended by confidence tier. Average blended score scaled (avg/5) × 8. With JD config: role-weighted scoring |
| `skill_recency` | Skill Recency | 6 | Pure Python | resume | Count skills with recency = RECENT or CURRENT. Score = (recent_count / total_skills) × 6 |
| `skills_learning_acumen` | Skills Learning Acumen | 3 | Pure Python | resume | fast_learner flag (≥2 new skills/year across ≥2 years)=3pts. Yearly new skill entries ≥3 years=2pts. 1–2 years=1pt. None=0 |
| `certifications` | Certifications | 3 | Pure Python (patched) | resume | Counted from education analysis. Score = clamp(cert_count, 0, 3). Patched into skills at compute time |
| `coding_community` | Coding Platforms / Community Contributions | 3 | Pure Python | resume | Count skills with `open_source_signal=True` in evidence map. 3+ signals=3, 2=2, 1=1, 0=0 |
| `project_explanation` | Project Explanation Skills | 3 | **Recruiter fills** | recruiter (0 at resume) | Recruiter scores quality of project walk-through during phone screen: 3=clear problem→design→outcome narrative, 2=good structure minor gaps, 1=disjointed, 0=cannot explain own project |
| `communication_skills` | Communication & Presentation Skills | 5 | **Panel fills** | panel (0 at resume) | Panel scores verbal clarity, structure, audience adaptability: 5=exceptional, 3=adequate, 1=unclear, 0=fails to communicate |
| `domain_skills` | Domain Skills | 5 | **Panel fills** | panel (0 at resume) | Panel scores domain-specific knowledge depth via scenario questions: 5=expert nuance, 3=solid applied, 1=surface familiarity, 0=none |
| `problem_solving` | Problem Solving Skills | 3 | **Panel fills** | panel (0 at resume) | Panel scores live problem-solving: 3=systematic breakdown with edge cases, 2=good approach minor gaps, 1=jumps to solution, 0=unable |
| `mandatory_skills` | Mandatory Skills — As per JD | — | **Flag only** | resume (display) | JD match only. Lists matched ✅ / missing ❌ mandatory skills. No score contribution. Only shown when JD config provided |
| `good_to_have_skills` | Good to Have Skills — As per JD | — | **Flag only** | resume (display) | Same as mandatory_skills but for optional JD skills |
| `coding_skills` | Coding Skills | — | **Panel qualitative** | panel (text) | Panel free-text assessment of live coding ability. No numeric score |
| `conceptual_skills` | Conceptual Skills | — | **Panel qualitative** | panel (text) | Panel free-text assessment of CS/domain conceptual understanding. No numeric score |

**Skills at resume stage**: skill_depth + skill_recency + skills_learning_acumen + certifications + coding_community (max ~23 pts auto-scored)
**Recruiter adds**: skill_list_years + project_explanation = up to 9 pts
**Panel adds**: communication_skills + domain_skills + problem_solving = up to 13 pts
**Skills ceiling**: 45 pts

---

### EDUCATION (15 pts = 10 core + 5 bonus)

#### Core (10 pts)

| Parameter | Excel Name | Max | Method | Stage | How Scored |
|-----------|-----------|-----|--------|-------|-----------|
| `institute_tier` | Institutes — Tier, GPA, Stream | 5 | Pure Python | resume | Tier map: TIER_1(IIT/NIT/IIM/Global Top)=4 base, TIER_2(regional)=3, TIER_3(mid)=2, TIER_4(low)=1. GPA bonus: +1 if TIER_1 + EXCELLENT/GOOD, +0.5 if TIER_2 + EXCELLENT. Capped at 5 |
| `degree_level` | Highest Education & Stream | 2 | Pure Python | resume | PhD or Master=2, Bachelor=1.5, Diploma=1, Unknown=0.5 |
| `education_job_relevance` | Education to Job Relevance | 2 | Pure Python | resume | Map strongest_course_value_signal: HIGH=2, MEDIUM=1.5, FOUNDATIONAL=0.5, UNKNOWN=1 |
| `education_gap` | Education Gaps | 1 | Pure Python | resume | Gap between last education end date and first job start. ≤6m=1, 6–12m=0.5, >12m=0. Gap >12m also triggers REJECT FLAG |

#### Bonus (5 pts)

| Parameter | Excel Name | Max | Method | Stage | How Scored |
|-----------|-----------|-----|--------|-------|-----------|
| `exec_education` | Executive / Distance Education | 1 | Pure Python | resume | Keyword scan in education entries for "executive", "continuing", "distance", "online", "mooc", "certification". Match=1, no match=0 |
| `patents_publications` | Patents / Publications | 2 | Pure Python (patched) | resume | Scan experience.patents or achievement text for "patent" or "publication". Boolean × 2. Patched into edu bonus at compute time |
| `linkedin_activity` | LinkedIn / Social Media Activeness | 1 | **Recruiter fills** | recruiter (0 at resume) | Recruiter validates LinkedIn profile activity during screening. 1=active professional presence, 0=absent/inactive |
| `extra_curriculars` | Extra Curricular Activities | 1 | **Recruiter fills** | recruiter (0 at resume) | Recruiter validates extra-curricular activities during screening |

---

## Stage Score Summary

| Stage | Params Added | Max Pts Added | Cumulative Max |
|-------|-------------|--------------|----------------|
| **Resume** | All auto-scored params | ~76 pts | 76 |
| **Recruiter** | skill_list_years, project_explanation, linkedin_activity, extra_curriculars | +11 pts | 87 |
| **Panel** | communication_skills, domain_skills, problem_solving | +13 pts | 100 |

---

## Reject Flags

Two conditions auto-flag a candidate for rejection (score drops to 0 for that param AND a flag is raised):

| Condition | Flag |
|-----------|------|
| Career breaks > 2 unexplained gaps (each >3 months) | `career_breaks` reject flag |
| Education gap > 12 months before first job | `education_gap` reject flag |
| Relevant experience ratio < 70% of JD YoE range (when configured) | `overall_experience` reject flag |

---

## Pipeline Execution Order

```
PDF/DOCX
   ↓
[1] Text Extraction (pdfminer / python-docx)
   ↓
[2] Normalize Resume Data
    → candidate_overview, experience_entries, skill_rows, education_entries
   ↓
[3] Sub-Analysis Engines (parallel)
    → collect_skill_evidence()    — skill rows with evidence_level, recency, OSS signal
    → analyze_experience()        — tenure, stability, company tiers, projects, progression
    → analyze_education()         — tier, GPA, gaps, relevance, certs
    → classify_dna()              — operating model (CONSULTING / PRODUCT / HYBRID)
   ↓
[4] BERT Priors (bert_signal_engine.infer_bert_priors)
    → skill_depth_priors          — top 12 skills × 5-class depth label + confidence
    → career_progression_prior    — single label + confidence
    → stakeholder_prior           — single label + confidence
    → mentorship_prior            — single label + confidence
    → role_family_prior           — role class + confidence
    → dna_prior                   — operating model + confidence
    → project_type_priors         — per-job label + confidence
   ↓
[5] Rubric Score (compute_rubric_score)
    → _score_experience_section() — Python heuristics + BERT blend
    → _score_skills_section()     — Python heuristics + BERT-primary for skill_depth
    → _score_education_section()  — Python heuristics
    → certifications patch        — from education → skills
    → patents patch               — from experience → edu bonus
   ↓
[6] LLM Judge (_llm_judge_rubric_params)
    → Reviews: career_progression, international_exposure,
               stakeholder_management, mentorship_signal, awards_recognition
    → Can accept or override Python+BERT anchors
    → Returns score + reason + confidence per param
   ↓
[7] Stage Scoring + Tag
    → _tag_stages() adds stage field to every param
    → stage_scores dict: resume_score, recruiter_can_add, panel_can_add
   ↓
[8] Recruiter Analysis (LLM narrative)
    → generate_recruiter_analysis() — overall LLM narrative for recruiter brief
   ↓
[OUTPUT] rubric_scorecard (100-pt JSON) + recruiter_summary + stage_scores
```

---

## Scoring Confidence Levels

Where applicable (LLM-judged params), confidence is reported as:
- **HIGH** — Multiple direct signals; rare; requires strong repeated evidence
- **MEDIUM** — Inferential judgment; one credible signal; typical default
- **LOW** — Weak or ambiguous evidence; score may change after interview

---

*Documentation generated from rubric_engine.py, bert_signal_engine.py, llm_judging_assets.py, engine.py*
