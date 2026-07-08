"""
JD Match Prompt Library
=======================
Central reference for all prompts, scoring rules, and narrative guidelines
used in the Resume Intelligence JD matching pipeline.

Structure
---------
1. NARRATIVE_SYSTEM / NARRATIVE_USER_TEMPLATE — LLM analyst persona + data template
2. CLUSTER_DEFINITIONS — 5 CEO/CTO clusters with weights and dimension mapping
3. BERT_ADJUSTMENT_RULES — how BERT signals affect the final score
4. SCORING_DIMENSION_GUIDE — what each of the 11 dimensions measures
5. SCREENING_Q_PATTERNS — question templates keyed by skill gap pattern
6. TILE_REASON_GUIDELINES — per-dimension narrative guidance for LLM

Usage
-----
    from jd_match_prompt_library import CLUSTER_DEFINITIONS, NARRATIVE_SYSTEM
    # or: /prompt-library  (browse in browser at runtime)
"""

# =============================================================================
# 1. Narrative prompts (mirrors jd_matching/prompts.py — single source of truth)
# =============================================================================

NARRATIVE_SYSTEM = """You are a senior talent intelligence analyst assisting a hiring team make fast, accurate shortlisting decisions.

You receive structured JD-to-resume match data from a deterministic scoring engine, enriched with BERT classifier signals and hiring stage context. Your role is to synthesise this into recruiter-ready narrative that is specific, evidence-grounded, and hiring-decision-ready.

OUTPUT FORMAT — Respond with a valid JSON object containing exactly these keys:
{
  "recruiter_summary": "<3-4 sentence narrative: overall fit verdict, top strength with specific skill/evidence, top risk with specific skill/gap, hiring recommendation. Name actual skills, years, and role data — never be generic.>",
  "strengths": [
    "<strength 1: specific skill or evidence with context (e.g. '4+ years of Python with NL2SQL production deployment')>",
    "<strength 2>",
    "<strength 3>",
    "<strength 4>"
  ],
  "risks": [
    "<risk 1: specific gap, flag, or concern with evidence (e.g. 'Missing Spark — mandatory for this DE role, no adjacent signal found')>",
    "<risk 2>",
    "<risk 3>"
  ],
  "rationale": "<1-2 sentence screening guidance: what to probe in the recruiter call, what to validate, and what would change the recommendation>",
  "stage_note": "<1 sentence on which evaluation stage this JD match is based on (resume/recruiter/panel) and what additional context is missing>"
}

Strict rules:
- Never fabricate skills, years, or experiences not evidenced in the match data
- Reference specific skill names, years, companies, and role titles where available
- If integrity flags exist, include them in risks
- If the candidate is significantly underqualified (score < 40), say so directly and recommend REJECT
- If BERT signals contradict the JD fit (e.g. BERT detects DATA_ENGINEER but JD wants AGENTIC_AI), flag it
- Keep strengths and risks as plain strings — no nested objects or sub-lists
- Do not repeat the same point in strengths and risks
- Return only the JSON object — no markdown fencing, no prose before or after
"""

NARRATIVE_USER_TEMPLATE = """## JD-to-Resume Match Analysis

### Role: {role_title} | Level: {job_level} | Min Experience: {min_years}+ years

### Deterministic Match Scores
- Overall JD Match Score: {overall_score}/100
- JD Alignment (mandatory skill coverage): {jd_alignment_score}/100
- Skill Recency: {skill_recency_score}/100
- Domain Fit: {domain_score}/100
- Evidence Strength: {evidence_strength}/100
- Job Level Fit: {job_level_fit}/100
- Experience Gap: {experience_gap_display}
- Integrity Score: {integrity_score}/100
- Recommendation: {recommendation}

### Skill Coverage
- Exact mandatory matches ({matched_mandatory_count}): {matched_mandatory}
- Adjacent mandatory / proxy skills ({adjacent_mandatory_count}): {adjacent_mandatory}
- Missing mandatory ({missing_mandatory_count}): {missing_mandatory}
- Matched optional: {matched_optional}
- Bonus skills (extras beyond JD): {bonus_skills}

### Candidate Profile
- Estimated total experience: {resume_years} years
- Top skills by depth: {top_skills_summary}
- Education signal: {education_signal}
- Company pedigree signal: {company_signal}
- Integrity flags: {integrity_flags}

### BERT & Heuristic Classifier Signals (ML-derived)
- Predominant skill depth tier (from top skills): {bert_skill_depth}
- BERT role family detected: {bert_role_family} (confidence: {bert_role_family_confidence})
- DNA fit profile: {bert_dna_fit} (confidence: {dna_confidence})
- Career progression pattern: {bert_career_progression}
- Stakeholder management level: {bert_stakeholder}
- Mentorship signal: {bert_mentorship}
- BERT-based score adjustment applied: {bert_delta:+d} points

### Hiring Stage Context
- Best available rubric stage: {rubric_stage} (score: {rubric_score}/100)
- JD Match Score (with BERT quality adjustment): {combined_score}/100
- Recruiter notes: {recruiter_notes}
- Panel notes: {panel_notes}

### JD Requirements
- Mandatory skills: {jd_mandatory_skills}
- Optional / nice-to-have skills: {jd_optional_skills}
- Role description summary: {jd_description_summary}

Generate the recruiter narrative JSON now."""


# =============================================================================
# 2. 5-Cluster scoring model (CEO/CTO perspective)
# =============================================================================
# Each cluster groups correlated JD match dimensions so the hiring team can
# read the result as a coherent story rather than 11 independent signals.

CLUSTER_DEFINITIONS = [
    {
        "id": "technical",
        "label": "Technical Stack",
        "icon": "🎯",
        "description": (
            "Skill coverage, depth, and recency — does the candidate have the right "
            "tools at production quality and are they current? "
            "Coverage without depth is a breadth-only risk; depth without coverage means ramp-up."
        ),
        "dimensions": [
            ("Must-Have Skills",   "must_have_coverage", 0.50),
            ("Skill Depth",        "skill_depth",        0.35),
            ("Recent Relevance",   "recent_relevance",   0.15),
        ],
        "ceo_question": "Can this person actually do the job on day one?",
        "correlation_note": (
            "Skill Depth and Must-Have Coverage are correlated: high coverage + low depth "
            "indicates breadth-first career. High depth + low coverage indicates deep specialist "
            "who may need ramp-up on adjacent tools."
        ),
    },
    {
        "id": "execution",
        "label": "Execution & Evidence",
        "icon": "📋",
        "description": (
            "Has the candidate shipped? Evidence strength measures production deployments, "
            "quantified outcomes, and scale markers. Career velocity shows if they are accelerating "
            "or plateauing. These two are tightly correlated — fast-trackers produce more evidence."
        ),
        "dimensions": [
            ("Evidence Strength",   "evidence_strength",   0.60),
            ("Career Velocity",     "career_velocity",     0.40),
        ],
        "ceo_question": "Has this person delivered at scale with measurable results?",
        "correlation_note": (
            "Evidence Strength and Career Velocity are the two strongest correlated dimensions: "
            "fast-track careers consistently produce richer evidence. A low-velocity + low-evidence "
            "profile almost never recovers with BERT signals alone."
        ),
    },
    {
        "id": "rolefit",
        "label": "Role & Level Fit",
        "icon": "📊",
        "description": (
            "Does the candidate match the seniority, domain, and team expectations of this specific JD? "
            "Level fit (IC vs manager vs director) and domain alignment (industry, function) "
            "are evaluated jointly — a senior IC in the wrong domain is a 50% fit."
        ),
        "dimensions": [
            ("Job Level Fit",   "job_level_fit",  0.45),
            ("Domain Fit",      "domain_score",   0.35),
            ("Optional Extras", "optional_match", 0.20),
        ],
        "ceo_question": "Is this person the right level for this role in our domain?",
        "correlation_note": (
            "Job Level and Domain are independently evaluated but jointly decisive: "
            "a strong domain expert at the wrong level often creates management friction. "
            "Optional Extras (nice-to-haves) break ties at equal mandatory scores."
        ),
    },
    {
        "id": "credibility",
        "label": "Profile Credibility",
        "icon": "🏛",
        "description": (
            "Trust signals: education pedigree, company brand, and integrity score. "
            "These inform how much to weight the claimed skills and experience. "
            "Integrity flags (inconsistent dates, inflated titles) reduce confidence across all other clusters."
        ),
        "dimensions": [
            ("Integrity Score",       "integrity_score",       0.45),
            ("Company Signal",        "company_signal_score",  0.30),
            ("Education / Research",  "education_signal_score", 0.25),
        ],
        "ceo_question": "Can we trust the claims on this resume?",
        "correlation_note": (
            "Integrity, Company, and Education are independently sourced but positively correlated "
            "in practice — elite-educated candidates from branded companies have lower base rates "
            "of integrity issues. A single integrity flag warrants probe regardless of the others."
        ),
    },
    {
        "id": "practical",
        "label": "Practical Fit",
        "icon": "📍",
        "description": (
            "Logistical and team factors: location, notice period, and JD requirement completeness. "
            "A 95/100 technical fit candidate with 3-month notice and wrong location may still "
            "block hiring velocity."
        ),
        "dimensions": [
            ("Location / Availability", "location_score",      0.50),
            ("JD Completeness",         "jd_completeness",     0.30),
            ("Notice / Availability",   "notice_score",        0.20),
        ],
        "ceo_question": "Can we actually hire and onboard this person in time?",
        "correlation_note": (
            "JD Completeness affects all other cluster scores: an incomplete JD inflates "
            "must-have coverage (fewer skills to check) and deflates domain fit confidence. "
            "Always check JD Completeness before trusting a high overall score."
        ),
    },
]


# =============================================================================
# 3. BERT signal adjustment rules
# =============================================================================
# bert_delta = sum of individual signal adjustments, capped to ±5 total.

BERT_ADJUSTMENT_RULES = """
## BERT Score Adjustment Rules

The deterministic JD match score (0-100) is adjusted by BERT classifier signals
to produce the final Match Score. Total delta is capped at ±5 points.

### Positive adjustments (quality boosts)
| Signal                          | Condition                           | Delta |
|---------------------------------|-------------------------------------|-------|
| skill_depth                     | ADVANCED or ARCHITECT_LEVEL         | +2    |
| career_progression              | FAST_TRACK                          | +2    |
| stakeholder_management          | C_LEVEL                             | +1    |
| mentorship_signal               | LEAD or FORMAL                      | +1    |
| role_family match               | BERT role == JD role family         | +1    |

### Negative adjustments (quality penalties)
| Signal                          | Condition                           | Delta |
|---------------------------------|-------------------------------------|-------|
| skill_depth                     | AWARENESS only                      | -3    |
| career_progression              | DECLINING                           | -2    |
| integrity_flags                 | Any flag present                    | -2    |
| role_family mismatch            | BERT role != JD role family         | -1    |

### Notes
- BERT delta is stored in combined_breakdown.bert_delta
- Rubric score is stored as reference (rubric_score_ref) but does NOT affect final score
- Final: combined_score = jd_match_score + bert_delta (capped 0-100)
"""


# =============================================================================
# 4. Scoring dimension guide (what each dimension measures)
# =============================================================================

SCORING_DIMENSION_GUIDE = """
## 11-Dimension Scoring Guide

Each dimension is scored 0-100 by the deterministic matching engine.

| Dimension             | Key (top_tiles)      | Description                                               | Weight in engine |
|-----------------------|----------------------|-----------------------------------------------------------|-----------------|
| Must-Have Coverage    | must_have_coverage   | % of mandatory JD skills matched (exact + adjacent)       | 30%             |
| Skill Depth           | skill_depth          | BERT-inferred depth tier across matched skills            | 12%             |
| Recent Relevance      | recent_relevance     | How recently the candidate used the matched skills        | 10%             |
| Evidence Strength     | evidence_strength    | Presence of quantified outcomes, production deployments   | 12%             |
| Career Velocity       | career_velocity      | Promotion speed, progression quality (BERT)               | 8%              |
| Job Level Fit         | job_level_fit        | Seniority match vs JD level expectation                   | 8%              |
| Domain Fit            | domain_score         | Industry and functional domain alignment                  | 8%              |
| Optional Extras       | optional_match       | % of nice-to-have skills matched                         | 4%              |
| Integrity Score       | integrity_score      | Absence of consistency flags, gaps, and inflation markers | 4%              |
| Company Signal        | company_signal_score | Tier of past employers (FAANG, unicorn, consulting, etc.) | 2%              |
| Education Signal      | education_signal_score | Tier of education institution + field relevance         | 2%              |

Note: Cluster weights above are approximations of the full weighting model.
Exact weights are in jd_matching/engine.py :: DIMENSION_WEIGHTS.
"""


# =============================================================================
# 5. Screening question patterns (used in match detail UI)
# =============================================================================

SCREENING_Q_PATTERNS = {
    # Mandatory skill gaps → probe for hidden depth
    "missing_mandatory": (
        "We noticed {skill} is listed as a mandatory requirement. "
        "Can you walk us through your hands-on experience with {skill}, "
        "including the most recent project where you used it in production?"
    ),
    # Adjacent/proxy skills → verify transfer
    "adjacent_mandatory": (
        "Your profile shows experience with {adjacent_skill}, which we consider a proxy for {required_skill}. "
        "Can you describe a project where you used {adjacent_skill} to achieve something similar to what {required_skill} would solve?"
    ),
    # Overexperienced for level
    "overqualified_level": (
        "This role is positioned at {jd_level} level. Your profile suggests {candidate_level} experience. "
        "What draws you to this specific scope, and how do you plan to stay engaged if the role is narrower than your current one?"
    ),
    # Domain mismatch
    "domain_gap": (
        "Your background is primarily in {candidate_domain}. This role is in {jd_domain}. "
        "Can you give a specific example where a skill or framework from {candidate_domain} "
        "transferred directly to a {jd_domain} context?"
    ),
    # Integrity flag — career gap
    "career_gap": (
        "I noticed a gap between {gap_start} and {gap_end} in your timeline. "
        "Could you briefly describe what you were focused on during that period?"
    ),
    # Integrity flag — short tenure
    "short_tenure": (
        "I see several roles where you were at the company for less than a year. "
        "Can you help me understand the context — were these contract engagements, "
        "company closures, or another reason?"
    ),
    # Evidence probe — no quantification
    "low_evidence": (
        "Your profile mentions {project_or_role}, but we're looking for measurable impact. "
        "Can you give us a specific metric — cost saved, latency reduced, revenue generated — "
        "that you were personally responsible for in that project?"
    ),
    # Fast-track positive probe
    "fast_track": (
        "You progressed from {early_role} to {current_role} in {years} years — that's accelerated. "
        "Can you walk us through the project or contribution that drove your most significant promotion?"
    ),
}


# =============================================================================
# 6. Tile reason guidelines (LLM guidance for generating per-dimension rationale)
# =============================================================================

TILE_REASON_GUIDELINES = {
    "must_have_coverage": (
        "State exact match count and adjacent count. Name the missing mandatory skills. "
        "If coverage < 60%, call it out as a hard risk."
    ),
    "skill_depth": (
        "Reference the BERT skill_depth_tier. If ADVANCED/ARCHITECT, name 1-2 specific skills "
        "with evidence. If AWARENESS, be explicit about the risk."
    ),
    "recent_relevance": (
        "State whether top skills were used in the last 1-2 years. Name skills with recency gaps."
    ),
    "evidence_strength": (
        "Count and name quantified achievements. Flag if all experience is described without metrics."
    ),
    "career_velocity": (
        "Reference BERT career_progression label. Cite role progression timeline if available."
    ),
    "job_level_fit": (
        "Compare candidate's current/last title vs JD level. "
        "Flag over/underqualification explicitly — do not soften."
    ),
    "domain_score": (
        "Name the specific domain match or mismatch (e.g. 'fintech vs logistics'). "
        "Adjacent domain is a partial credit, not full credit."
    ),
    "optional_match": (
        "List which nice-to-have skills are matched. State the count ratio."
    ),
    "integrity_score": (
        "List any flags: unexplained gaps, tenure < 6 months, title inflation, inconsistent dates. "
        "If clean, say so explicitly."
    ),
    "company_signal_score": (
        "Name the highest-tier employer. If FAANG/unicorn/Tier-1 consulting, say it. "
        "If all employers are unknown, state that directly."
    ),
    "education_signal_score": (
        "Name the institution tier and degree relevance. "
        "IIT/IIM/ISI/top-5 global = TIER_1. Unknown college = LOW."
    ),
}
