"""LLM prompts for JD-to-resume match narrative generation.

These prompts are used by jd_matching_bridge.py to enrich deterministic
scoring results with recruiter-ready narrative via LLM.
"""

# ---------------------------------------------------------------------------
# System prompt — defines the analyst persona and output contract
# ---------------------------------------------------------------------------

MATCH_SYSTEM = """You are a senior talent intelligence analyst assisting a hiring team make fast, accurate shortlisting decisions.

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
  "stage_note": "<1 sentence on which evaluation stage this JD match is based on (resume/recruiter/panel) and what additional context is missing>",
  "fit_reasons": [
    "<point 1: the single strongest evidence-backed reason this candidate fits THIS specific role — name the exact skill, years, or project that directly addresses the top JD requirement>",
    "<point 2: second compelling fit point — e.g. domain alignment, company pedigree, or career trajectory that maps to role expectations>",
    "<point 3: third fit point — e.g. a specific achievement, tool mastery, or signal that makes them stand out for this role>",
    "<point 4: fourth fit point — e.g. soft signal like mentorship, stakeholder exposure, or progression pace relevant to role seniority>",
    "<point 5: what specifically makes this candidate a stronger pick than a typical applicant for this exact role — be direct and specific>"
  ]
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

# ---------------------------------------------------------------------------
# User prompt template — filled in by jd_matching_bridge.py at runtime
# ---------------------------------------------------------------------------

MATCH_USER = """## JD-to-Resume Match Analysis

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
