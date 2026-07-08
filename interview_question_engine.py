"""Interview question engine.

Generates recruiter-grade interview questions mapped to rubric parameters.
Each question carries: theme, rubric_param, what_it_tests, scoring_guide.
Questions are ordered by priority and grouped by rubric section.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Scoring guide lookup — tells recruiter what a good vs weak answer looks like
# ---------------------------------------------------------------------------
SCORING_GUIDES: dict[str, dict[str, str]] = {
    "skill_depth": {
        "strong": "Names specific technology, version, or library; describes a design decision; mentions a trade-off or failure.",
        "weak":   "Generic answer with no concrete project, scale, or outcome cited.",
        "follow_up": "Ask: What would you do differently today? What was the hardest edge case?"
    },
    "career_progression": {
        "strong": "Describes growing scope/ownership across roles; mentions specific promotions or expanding team size.",
        "weak":   "Lists job titles without explaining growing responsibility or impact.",
        "follow_up": "Ask: What changed in your responsibilities from year 1 to year 3?"
    },
    "stakeholder_management": {
        "strong": "Names actual stakeholders (CTO, product VP, client PM); explains conflict or negotiation with outcome.",
        "weak":   "Vague reference to 'working with business stakeholders' without specifics.",
        "follow_up": "Ask: Give me an example where a stakeholder pushed back on your recommendation."
    },
    "mentorship_signal": {
        "strong": "Names number of engineers mentored; gives concrete example of code review or unblocking junior.",
        "weak":   "Says 'I helped the team' without a named mentorship action or outcome.",
        "follow_up": "Ask: How did the engineer you mentored grow, and how do you know?"
    },
    "international_exposure": {
        "strong": "Mentions specific country, timezone overlap, client name, or onsite assignment duration.",
        "weak":   "Says 'global team' with no specifics on geography or collaboration challenges.",
        "follow_up": "Ask: What was the hardest cross-timezone or cross-culture challenge you navigated?"
    },
    "awards_recognition": {
        "strong": "Names a specific award, promotion with citation, patent number, or publication title.",
        "weak":   "Vague mention of 'good feedback' or 'strong reviews'.",
        "follow_up": "Ask: What did you do that others in your team didn't do to earn that recognition?"
    },
    "impact": {
        "strong": "States a specific metric before/after; names the business outcome (cost saved, revenue uplocked, error rate dropped).",
        "weak":   "Describes technical work without connecting to any business result.",
        "follow_up": "Ask: How was that outcome measured and who cared about that number?"
    },
    "communication_skills": {
        "strong": "Clear, concise explanation with correct vocabulary; adapts language for non-technical audience without losing accuracy.",
        "weak":   "Jargon-heavy, rambling, or can't simplify the concept for a lay audience.",
        "follow_up": "Ask: How would you explain this to a product manager with no ML background?"
    },
    "domain_skills": {
        "strong": "Demonstrates deep understanding of domain-specific constraints, trade-offs, and best practices.",
        "weak":   "Generic textbook answer with no domain-specific nuance or real-world experience.",
        "follow_up": "Ask: What's a common mistake people make in this domain that you've personally observed?"
    },
    "project_explanation": {
        "strong": "Structured walk-through: problem → constraints → design decisions → outcome → learnings. Self-aware about trade-offs.",
        "weak":   "Jumps straight to technology; can't articulate the problem or the business reason for the project.",
        "follow_up": "Ask: What would you do differently if you started this project today?"
    },
    "problem_solving": {
        "strong": "Breaks down the problem systematically; asks clarifying questions; explores multiple approaches before committing.",
        "weak":   "Jumps to the first solution without exploring constraints or edge cases.",
        "follow_up": "Ask: What assumptions did you make, and what happens if those are wrong?"
    },
    "stability": {
        "strong": "Explains a specific reason for each move; at least one role lasted 2+ years with growing scope.",
        "weak":   "Deflects or gives vague reasons for short tenures.",
        "follow_up": "Ask: What would have kept you longer at [shortest-tenure company]?"
    },
    "project_quality": {
        "strong": "Describes architecture choices, scale numbers, problems solved, and what failed.",
        "weak":   "Lists technologies used without explaining problems solved or decisions made.",
        "follow_up": "Ask: If you rebuilt this today, what would you change architecturally?"
    },
    "linkedin_activity": {
        "strong": "Active LinkedIn presence with posts, articles, or recommendations visible and recent.",
        "weak":   "Profile exists but no recent activity, incomplete sections, or no recommendations.",
        "follow_up": "Ask: Do you share technical content or contribute to professional communities online?"
    },
    "extra_curriculars": {
        "strong": "Hackathons, open-source contributions, community talks, volunteer tech work, or side projects with impact.",
        "weak":   "No activities outside day job; purely transactional career profile.",
        "follow_up": "Ask: What do you do outside work that makes you a better engineer?"
    },
}

# ---------------------------------------------------------------------------
# Rubric parameter → help text shown to recruiter
# ---------------------------------------------------------------------------
RUBRIC_PARAM_HELP: dict[str, dict[str, str]] = {
    "overall_experience": {
        "label": "Overall / Relevant Experience",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Total years of experience band-scored against seniority thresholds; JD-calibrated if a YoE range is configured.",
        "how_scored": "With JD range: ratio of years in target band × 3. Without JD range: bands — 10+ yrs=3, 6-10=2.5, 4-6=2, 2-4=1.5, 1-2=1, <1=0.5.",
        "recruiter_action": "Check if past roles map to target responsibilities. If domain changed recently, validate whether recent years count as relevant.",
        "green_flag": "Consistent trajectory with years fully in target band and no seniority mismatch.",
        "red_flag": "Large portion of experience in a completely different domain, or candidate is significantly over/under the target band.",
    },
    "career_breaks": {
        "label": "Career Breaks",
        "stage": "resume",
        "max": 2,
        "what_it_measures": "Number and length of unexplained employment gaps (>3 months, non-education).",
        "how_scored": "0 breaks = 2pts. 1 break = 1pt. 2+ breaks = 0pts + reject flag if >2.",
        "recruiter_action": "Ask the candidate to explain each gap briefly — upskilling, health, family, relocation are acceptable with context.",
        "green_flag": "Continuous employment trajectory or breaks explained by education/certification.",
        "red_flag": "3+ unexplained gaps of 6+ months each — flag for recruiter call.",
    },
    "career_progression": {
        "label": "Career Progression",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Trajectory of seniority and ownership growth across job titles.",
        "how_scored": "BERT+heuristic blended. FAST_TRACK=3, GROWING=2, LATERAL=1, DECLINING=0.5. Recruiter validates.",
        "recruiter_action": "Look for promotions, growing team sizes, expanding scope. Ask about specific inflection points in their career.",
        "green_flag": "IC → Senior IC → Lead / Manager with clear evidence of growing scope.",
        "red_flag": "Same title for 5+ years or downward moves without clear explanation.",
    },
    "stability": {
        "label": "Stability",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Average tenure per company as a loyalty and retention signal.",
        "how_scored": "Maps stability_score (0-5) to 3pts. HIGH loyalty signal = full credit. LOW = churn risk.",
        "recruiter_action": "Flag candidates with avg tenure <12 months. Ask about longest-stay role and what kept them there.",
        "green_flag": "2+ years at majority of companies; long-term roles show depth and commitment.",
        "red_flag": "Avg tenure under 12 months, 3+ companies in 3 years without clear reason.",
    },
    "company_tier": {
        "label": "Company Tier",
        "stage": "resume",
        "max": 5,
        "what_it_measures": "Quality of employers (Tier 1 = FAANG/top product → Tier 5 = unknown). Reflects bar of technical environment.",
        "how_scored": "Best tier across all companies. Tier1=5pts, Tier2=4pts, Tier3=3pts, Tier4=2pts, Tier5=1pt.",
        "recruiter_action": "Tier 4/5 doesn't mean weak — look at project quality and ownership instead. Tier 1 doesn't guarantee hands-on skills.",
        "green_flag": "At least one Tier 1-2 employer with recent experience.",
        "red_flag": "All Tier 5 employers with no notable open-source or community presence.",
    },
    "awards_recognition": {
        "label": "Awards & Recognition",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Named awards, promotions with citation, patents, publications, conference talks.",
        "how_scored": "LLM judges the achievements list. Only genuine recognition counts (not routine deliveries). 3 = 3pts, 2 = 2pts, etc.",
        "recruiter_action": "Ask candidates to explain any achievement — if they can't recall details, it may be inflated.",
        "green_flag": "Publication, patent, named award, or promotion specifically citing individual contribution.",
        "red_flag": "'Team recognition' without individual citation. Generic 'exceeded expectations' language.",
    },
    "international_exposure": {
        "label": "International Exposure",
        "stage": "resume",
        "max": 2,
        "what_it_measures": "Evidence of onsite international assignment, globally distributed team, or multi-country client work.",
        "how_scored": "LLM judges from experience text. Explicit evidence = 2pts. Implied = 1pt. None = 0pts.",
        "recruiter_action": "Ask candidates to name specific countries or clients. 'We had a US team' is weaker than 'I was onsite in London for 6 months'.",
        "green_flag": "Named international client, onsite country, or explicitly global delivery team.",
        "red_flag": "Company name suggests global presence but no individual international role evidence.",
    },
    "stakeholder_management": {
        "label": "Stakeholder Management",
        "stage": "resume",
        "max": 2,
        "what_it_measures": "Evidence of client-facing work, senior stakeholder interaction, or business partner collaboration.",
        "how_scored": "LLM judges from job descriptions. Explicit client/stakeholder language = 2pts. Cross-functional only = 1pt. Pure IC = 0pts.",
        "recruiter_action": "Look for words like 'client', 'customer', 'business partner', 'product owner', 'CXO'. Validate in call.",
        "green_flag": "Named client, regular business review ownership, or explicit external-facing deliverables.",
        "red_flag": "All work described in purely technical terms with no external or business-facing component.",
    },
    "mentorship_signal": {
        "label": "Mentorship Signal",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Evidence of leading engineers, conducting code reviews, or coaching junior team members.",
        "how_scored": "LLM judges from job descriptions. Led ≥2 roles = 3pts. 1 clear instance = 2pts. Implied = 1pt. None = 0pts.",
        "recruiter_action": "Ask: 'Tell me about someone you mentored and what happened to their career.'",
        "green_flag": "Named engineers mentored, specific code review outcomes, or junior-senior pairing evidence.",
        "red_flag": "Only 'collaborated' or 'worked with' language — no lead, coach, or mentor language anywhere.",
    },
    "project_1": {
        "label": "Project 1",
        "stage": "resume",
        "max": 8,
        "what_it_measures": "Quality of primary project: 8 criteria including role depth (ownership verb) and quantified impact.",
        "how_scored": "8 criteria × 1pt: type, title, description>50chars+ownership verb, duration≥3m, skills listed, domain tag, role depth, quantified impact.",
        "recruiter_action": "Ask the candidate to walk through this project: the problem, their specific role, the architecture, and the outcome.",
        "green_flag": "Multi-month project with clear ownership language, named tech, measurable outcome, and quantified business impact.",
        "red_flag": "Project listed as one line: 'Built ML model using Python.' No duration, no ownership, no outcome.",
    },
    "project_2": {
        "label": "Project 2",
        "stage": "resume",
        "max": 6,
        "what_it_measures": "Quality of second project: type, title, problem described, duration, skills listed, domain tagged.",
        "how_scored": "6 criteria × 1pt each: project type known, role/title present, description >20 chars, duration ≥3 months, skills listed, domain tag present.",
        "recruiter_action": "Use this to assess breadth — is the second project in a different domain or the same? Does it add diversity of evidence?",
        "green_flag": "Different domain or technology from Project 1, showing range of delivery.",
        "red_flag": "Same project described twice under different job titles.",
    },
    "skill_list_years": {
        "label": "Skill List — Years of Experience / Timeline",
        "stage": "recruiter",
        "max": 6,
        "what_it_measures": "Breadth and timeline of skills: how many skills have meaningful years of hands-on use.",
        "how_scored": "Recruiter scores 0–6 after validating the skill timeline: ≥6 credible skills = 6, each validated skill = 1pt.",
        "recruiter_action": "Ask candidate to walk through each key skill and the years they actively used it. Score only APPLIED+ skills.",
        "green_flag": "6+ skills with clear year ranges and non-trivial usage in real projects.",
        "red_flag": "Skill list padding — years are inflated or skills are listed from job descriptions without real usage.",
    },
    "skill_depth": {
        "label": "Skill Depth",
        "stage": "resume",
        "max": 8,
        "what_it_measures": "BERT-blended depth score for top skills, with LLM validation. Primary accuracy engine.",
        "how_scored": "Top 5 skills' BERT depth scores blended with evidence levels, rescaled to 8pts. Role-specific weights applied if client config present.",
        "recruiter_action": "Ask a technical depth probe for the top 2-3 skills. A DEEP/EXPERT score should be validated with a specific implementation question.",
        "green_flag": "ADVANCED or ARCHITECT_LEVEL BERT classification with recent evidence in the same domain.",
        "red_flag": "BERT confidence < 0.45 on key skills — evidence is sparse or conflicting.",
    },
    "skill_recency": {
        "label": "Skill Recency",
        "stage": "resume",
        "max": 6,
        "what_it_measures": "% of evidenced skills used in the last 12-18 months.",
        "how_scored": "Ratio of RECENT/CURRENT skills to total skills × 6pts.",
        "recruiter_action": "Ask candidates about stale skills explicitly: 'How current is your [skill X] knowledge? When did you last use it in production?'",
        "green_flag": "70%+ of evidenced skills are RECENT or CURRENT.",
        "red_flag": "Primary technical skills are 3+ years old with no recent reinforcement.",
    },
    "skills_learning_acumen": {
        "label": "Skills Learning Acumen",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Rate of new skill acquisition per year and evidence of continuous self-improvement.",
        "how_scored": "Fast learner flag (≥2 skills/yr across ≥2 years) = 3pts. Steady learning across ≥3 years = 2pts. Minimal = 1pt. None = 0pt.",
        "recruiter_action": "Ask: 'What did you teach yourself in the last 12 months outside of work requirements?'",
        "green_flag": "Clear new skills added every year, certifications, side projects, or emerging tech adoption.",
        "red_flag": "No new skills or certifications in the last 2+ years — potentially stagnant learner.",
    },
    "certifications": {
        "label": "Certifications",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Relevant professional certifications from recognised bodies.",
        "how_scored": "3+ certs = 3pts. 2 = 2pts. 1 = 1pt. None = 0pt.",
        "recruiter_action": "Verify certification issuer and year. Ask if they've applied the certification in production work.",
        "green_flag": "AWS/GCP/Azure certified, CFA, PMP, or domain-specific cert from a recognised body.",
        "red_flag": "Only soft-skills or non-technical certifications for a technical role.",
    },
    "coding_community": {
        "label": "Coding Community",
        "stage": "resume",
        "max": 3,
        "what_it_measures": "Open-source contributions, GitHub activity, or coding platform presence.",
        "how_scored": "3+ signals = 3pts. 2 = 2pts. 1 = 1pt. None = 0pt.",
        "recruiter_action": "Ask for GitHub profile or OSS project link. Evaluate quality of contributions, not just quantity.",
        "green_flag": "Active GitHub with meaningful commits, OSS project ownership, or LeetCode/Kaggle profile.",
        "red_flag": "No community footprint — all work is proprietary with no external validation.",
    },
    # ── Panel-stage params ─────────────────────────────────────────────────
    "communication_skills": {
        "label": "Communication Skills",
        "stage": "panel",
        "max": 5,
        "what_it_measures": "Clarity, structure, and adaptability of verbal and written communication during the panel.",
        "how_scored": "Panel scores 0–5: 5=exceptionally clear and audience-aware, 3=adequate, 1=unclear or jargon-heavy, 0=fails to communicate.",
        "recruiter_action": "Ask the candidate to explain their primary skill to a non-technical person. Note vocabulary, structure, and confidence.",
        "green_flag": "Clear structure, correct vocabulary, adapts explanation for audience, acknowledges what they don't know.",
        "red_flag": "Rambling, jargon-heavy, or cannot simplify technical concepts for business stakeholders.",
    },
    "domain_skills": {
        "label": "Domain Knowledge",
        "stage": "panel",
        "max": 5,
        "what_it_measures": "Depth of domain-specific knowledge assessed through panel technical questions.",
        "how_scored": "Panel scores 0–5: 5=expert-level nuance, 3=solid applied knowledge, 1=surface familiarity, 0=no credible domain knowledge.",
        "recruiter_action": "Ask 2-3 domain-specific scenario questions. Probe for trade-off awareness and real-world constraints.",
        "green_flag": "Names domain-specific constraints, anti-patterns, and recent developments; cites real examples.",
        "red_flag": "Textbook answers with no real-world context; can't name domain-specific failure modes.",
    },
    "project_explanation": {
        "label": "Project Walk-Through",
        "stage": "recruiter",
        "max": 3,
        "what_it_measures": "Quality of structured project explanation during the panel interview.",
        "how_scored": "Panel scores 0–3: 3=clear problem→design→outcome narrative with self-critique, 2=good structure minor gaps, 1=disjointed, 0=can't explain their own project.",
        "recruiter_action": "Ask: Walk me through your most complex project — start with the business problem, not the tech.",
        "green_flag": "Clear problem statement → design decisions with rationale → outcome → honest reflection on what failed.",
        "red_flag": "Jumps straight to tech stack; can't articulate the business problem or their specific ownership.",
    },
    "problem_solving": {
        "label": "Problem Solving",
        "stage": "panel",
        "max": 3,
        "what_it_measures": "Structured thinking and reasoning ability when given a live problem in the panel.",
        "how_scored": "Panel scores 0–3: 3=systematic decomposition+asks clarifying questions+explores multiple approaches, 2=mostly structured, 1=jumps to solution, 0=no structure.",
        "recruiter_action": "Give a domain-relevant problem. Observe: does the candidate clarify before answering? Do they enumerate approaches?",
        "green_flag": "Asks clarifying questions first; enumerates 2+ approaches; explains trade-offs before committing.",
        "red_flag": "Jumps to first solution without asking constraints; no awareness of edge cases or failure modes.",
    },
    # ── Education params ───────────────────────────────────────────────────
    "institute_tier": {
        "label": "Institute Tier",
        "stage": "resume",
        "max": 5,
        "what_it_measures": "Academic pedigree + GPA signal (absorbed). TIER_1 base=4, +1 if GPA GOOD/EXCELLENT.",
        "how_scored": "TIER_1=4+GPA bonus(1), TIER_2=3+GPA bonus(0.5), TIER_3=2, TIER_4=1, UNKNOWN=1. Max 5.",
        "recruiter_action": "Don't penalise UNKNOWN alone — check project quality, company tier, and skill depth instead. Institute is a prior, not a verdict.",
        "green_flag": "IIT, IISc, NIT, IIIT, or equivalent global top-50 university with good GPA.",
        "red_flag": "Institute tier unknown AND all other signals are weak — may need extra technical validation.",
    },
    "education_job_relevance": {
        "label": "Education-Job Relevance",
        "stage": "resume",
        "max": 2,
        "what_it_measures": "How directly the degree subject maps to the target role.",
        "how_scored": "HIGH=2pts (CS/CE/Data), MEDIUM=1.5pts, FOUNDATIONAL=0.5pts, UNKNOWN=1pt (neutral).",
        "recruiter_action": "Non-CS degrees with strong self-taught skills often outperform CS graduates who haven't stayed current. Use skill depth as the stronger signal.",
        "green_flag": "CS, Computer Engineering, Data Science, Statistics, or closely related technical degree.",
        "red_flag": "Non-technical degree AND low skill depth AND no certifications — evaluate practical work instead.",
    },
    "degree_level": {
        "label": "Degree Level",
        "stage": "resume",
        "max": 2,
        "what_it_measures": "Highest academic qualification achieved.",
        "how_scored": "PhD/Master=2pts, Bachelor=1.5pts, Diploma=1pt, Unknown=0.5pt.",
        "recruiter_action": "For research roles, Master's/PhD is a significant positive signal. For product/engineering roles, Bachelor's with strong project portfolio often equal.",
        "green_flag": "Master's or PhD in a directly relevant technical field.",
        "red_flag": "Missing education section entirely — ask the candidate to clarify.",
    },
    "education_gap": {
        "label": "Education Gap",
        "stage": "resume",
        "max": 1,
        "what_it_measures": "Gap between education end and first employment.",
        "how_scored": "≤6m=1pt, 6-12m=0.5pt, >12m=0pt.",
        "recruiter_action": "Ask about gaps >6 months — upskilling, exam prep, and family reasons are acceptable with context.",
        "green_flag": "Direct entry into employment within 6 months of graduation.",
        "red_flag": "12+ month unexplained gap after education with no certification or project to show for it.",
    },
    "exec_education": {
        "label": "Executive / Continuing Education",
        "stage": "resume",
        "max": 1,
        "what_it_measures": "Continuing education, executive programmes, distance learning, or MOOCs.",
        "how_scored": "Any detected entry = 1pt.",
        "recruiter_action": "Ask what motivated the exec education and how they applied it.",
        "green_flag": "Domain-relevant online certification or executive programme from a recognised provider.",
        "red_flag": "Low-quality or unverifiable MOOC certificates from unknown providers.",
    },
    "patents_publications": {
        "label": "Patents & Publications",
        "stage": "resume",
        "max": 2,
        "what_it_measures": "Patents filed or academic/technical publications — signals deep research or innovation contribution.",
        "how_scored": "Signal detected = 2pts.",
        "recruiter_action": "Ask the candidate to describe the patent or publication briefly — if they can't recall, it may be inflated.",
        "green_flag": "Named patent number, conference paper, or journal publication with clear individual contribution.",
        "red_flag": "Listed as 'co-author' on a paper with no ability to explain the contribution.",
    },
    "linkedin_activity": {
        "label": "LinkedIn Activity",
        "stage": "recruiter",
        "max": 1,
        "what_it_measures": "Recruiter-assessed LinkedIn profile quality and recent activity.",
        "how_scored": "Recruiter fills at discussion stage: 1=active profile with posts/recommendations, 0=inactive or thin.",
        "recruiter_action": "Review LinkedIn before the call. Check for recent posts, recommendations, and profile completeness.",
        "green_flag": "Profile with recent posts, 10+ recommendations, complete experience matching resume.",
        "red_flag": "Empty LinkedIn or significant mismatch between LinkedIn and submitted resume.",
    },
    "extra_curriculars": {
        "label": "Extra-Curriculars",
        "stage": "recruiter",
        "max": 1,
        "what_it_measures": "Hackathons, open-source leadership, community talks, or side projects validated at recruiter stage.",
        "how_scored": "Recruiter fills at discussion stage: 1=clear extra-curricular activity with impact, 0=none.",
        "recruiter_action": "Ask: What do you do outside work that makes you a better engineer or professional?",
        "green_flag": "Hackathon winner, conference speaker, OSS maintainer, or active side project with users.",
        "red_flag": "No activities outside day job — purely transactional career profile.",
    },
}


# ---------------------------------------------------------------------------
# Core question builder
# ---------------------------------------------------------------------------

def build_interview_questions(
    analysis: dict[str, Any],
    client_role_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate rubric-mapped interview questions from resume analysis."""
    skills = analysis.get("skill_analysis", {}).get("top_skills", [])
    gaps = analysis.get("qualitative_analysis", {}).get("gaps", [])
    dna = analysis.get("dna_fit", {})
    experience = analysis.get("experience_analysis", {})
    education = analysis.get("education_analysis", {})
    rubric = analysis.get("rubric_scorecard", {})
    rubric_bd = rubric.get("breakdown", {})
    exp_bd = rubric_bd.get("experience", {})
    skills_bd = rubric_bd.get("skills", {})
    edu_bd = rubric_bd.get("education", {})
    role_family = (
        analysis.get("semantic_analysis", {}).get("top_role_family")
        or analysis.get("role_fit", {}).get("top_role_family", "")
    )
    candidate_name = analysis.get("candidate_overview", {}).get("name", "The candidate")

    questions: list[dict[str, Any]] = []

    # ── 1. Skill depth probes ──────────────────────────────────────────────
    if client_role_config:
        skill_lookup = {str(s.get("skill") or "").lower(): s for s in skills}
        for entry in (client_role_config.get("mandatory_skills") or []):
            skill_name = entry.get("skill") or ""
            row = skill_lookup.get(skill_name.lower(), {})
            evidence = (row.get("evidence_level") or "NONE").upper()
            if evidence in ("NONE", "MENTION", "WEAK"):
                questions.append(_q(
                    priority="high", theme="mandatory_gap", skill=skill_name,
                    rubric_param="skill_depth",
                    question=(
                        f"{skill_name} is a mandatory skill for this role. Walk me through a production "
                        f"use case — what specifically did you build, at what scale, and what was the measurable outcome?"
                    ),
                    what_it_tests="Validates actual hands-on depth, not just keyword listing.",
                    evidence_reference=row.get("top_evidence", ""),
                ))
            else:
                questions.append(_q(
                    priority="medium", theme="mandatory_depth_probe", skill=skill_name,
                    rubric_param="skill_depth",
                    question=(
                        f"You have {evidence.lower()} evidence of {skill_name}. "
                        f"Describe the most architecturally complex {skill_name} implementation you owned — "
                        f"the design decisions you made, what failed, and what you'd change now."
                    ),
                    what_it_tests="Validates depth beyond surface evidence — owns the design or just followed instructions.",
                    evidence_reference=row.get("top_evidence", ""),
                ))
        for entry in (client_role_config.get("good_to_have_skills") or []):
            skill_name = entry.get("skill") or ""
            row = skill_lookup.get(skill_name.lower(), {})
            evidence = (row.get("evidence_level") or "NONE").upper()
            if evidence in ("NONE", "MENTION"):
                questions.append(_q(
                    priority="low", theme="good_to_have_gap", skill=skill_name,
                    rubric_param="skill_list_years",
                    question=f"{skill_name} is a nice-to-have here. Do you have any exposure — even hands-on exploration or a side project?",
                    what_it_tests="Lightweight check for breadth signal on non-critical skills.",
                    evidence_reference="",
                ))
    else:
        # No client config — question per evidenced skill
        for skill in skills[:8]:
            name = skill.get("skill") or ""
            evidence = (skill.get("evidence_level") or "WEAK").upper()
            depth = (skill.get("depth_label") or skill.get("depth") or "WEAK").upper()
            top_ev = skill.get("top_evidence") or skill.get("judged_reason") or ""
            if evidence in ("WEAK", "MENTION"):
                questions.append(_q(
                    priority="high", theme="gap_probe", skill=name,
                    rubric_param="skill_list_years",
                    question=(
                        f"Your resume lists {name} but the evidence is thin. "
                        f"Walk me through one project where you used it: what you built, your exact contribution, and the outcome."
                    ),
                    what_it_tests="Separates keyword listing from actual hands-on use.",
                    evidence_reference=top_ev,
                ))
            elif depth in ("HANDS_ON", "ADVANCED", "ARCHITECT_LEVEL", "APPLIED", "DEEP", "EXPERT"):
                questions.append(_q(
                    priority="medium", theme="depth_validation", skill=name,
                    rubric_param="skill_depth",
                    question=(
                        f"Your strongest evidence for {name} suggests hands-on depth. "
                        f"Tell me about the most technically difficult {name} problem you solved — "
                        f"the constraints, your decision, and the failure you had to debug."
                    ),
                    what_it_tests="Validates depth through specific technical narrative — avoids rehearsed answers.",
                    evidence_reference=top_ev,
                ))

    # ── 2. Career progression probe ───────────────────────────────────────
    prog_score = (exp_bd.get("career_progression") or {}).get("score", 0)
    prog_max = (exp_bd.get("career_progression") or {}).get("max", 3)
    questions.append(_q(
        priority="high" if prog_score < prog_max * 0.6 else "medium",
        theme="career_progression", skill="Career Progression",
        rubric_param="career_progression",
        stage="recruiter",
        question=(
            f"Walk me through your career arc — specifically what changed in your scope and ownership "
            f"when you moved from {'your first role' if not experience.get('companies') else experience.get('companies', ['role'])[0]} "
            f"to your most recent position. What decisions were you making that you couldn't make before?"
        ),
        what_it_tests="Validates seniority growth is real (decisions, ownership, scope) not just title inflation.",
        evidence_reference=f"Rubric score: {prog_score}/{prog_max}",
    ))

    # ── 3. Stakeholder management probe ───────────────────────────────────
    stake_score = (exp_bd.get("stakeholder_management") or {}).get("score", 0)
    questions.append(_q(
        priority="high" if stake_score == 0 else "medium",
        theme="stakeholder_management", skill="Stakeholder Management",
        rubric_param="stakeholder_management",
        stage="recruiter",
        question=(
            "Describe a situation where you had to get sign-off or alignment from a non-technical stakeholder "
            "on a technical decision. Who was involved, what was the resistance, and how did you resolve it?"
        ),
        what_it_tests="Validates business communication and influence without authority.",
        evidence_reference=f"Rubric score: {stake_score}/2",
    ))

    # ── 4. Mentorship / leadership probe ──────────────────────────────────
    mentor_score = (exp_bd.get("mentorship_signal") or {}).get("score", 0)
    questions.append(_q(
        priority="high" if mentor_score == 0 else "low",
        theme="mentorship", skill="Mentorship / Leadership",
        rubric_param="mentorship_signal",
        stage="recruiter",
        question=(
            "Tell me about a time you helped a junior colleague improve technically — "
            "what was the gap, how did you approach it, and what was the outcome for them and the team?"
        ),
        what_it_tests="Validates genuine leadership behaviour vs title-based claim.",
        evidence_reference=f"Rubric score: {mentor_score}/3",
    ))

    # ── 5. International exposure probe ───────────────────────────────────
    intl_score = (exp_bd.get("international_exposure") or {}).get("score", 0)
    questions.append(_q(
        priority="medium" if intl_score == 2 else "low",
        theme="international_exposure", skill="International Exposure",
        rubric_param="international_exposure",
        stage="recruiter",
        question=(
            "Have you worked with international teams or clients directly? "
            "If yes, name the country/client and describe one challenge specific to that context — "
            "timezone, culture, or process difference."
        ),
        what_it_tests="Validates international collaboration depth — not just company name.",
        evidence_reference=f"Rubric score: {intl_score}/2",
    ))

    # ── 6. Impact / business outcome probe ────────────────────────────────
    questions.append(_q(
        priority="high", theme="impact", skill="Business Impact",
        rubric_param="awards_recognition",
        stage="recruiter",
        question=(
            "Pick the project from your resume you're most proud of. "
            "Before telling me what you built, tell me: what business problem were you solving, "
            "and how was success measured — with a number?"
        ),
        what_it_tests="Forces business framing before technical description. Catches candidates who can't connect delivery to outcome.",
        evidence_reference="",
    ))

    # ── 7. Role-fit anchor question ────────────────────────────────────────
    if role_family:
        questions.append(_q(
            priority="high", theme="role_fit", skill=role_family,
            rubric_param="career_progression",
            stage="recruiter",
            question=(
                f"Your profile is aligned to {role_family.replace('_', ' ')}. "
                f"Which single project best proves you're ready for a senior position in this role family, "
                f"and why that one over everything else on your resume?"
            ),
            what_it_tests="Candidate's self-awareness about strongest evidence and fit narrative.",
            evidence_reference="",
        ))

    # ── 8. Stability probe (only if avg tenure < 18 months) ───────────────
    avg_tenure = float(experience.get("average_tenure_months") or 0)
    if avg_tenure < 18:
        shortest_company = ""
        for cp in (experience.get("company_profiles") or []):
            if cp.get("company"):
                shortest_company = cp.get("company")
                break
        questions.append(_q(
            priority="medium", theme="stability", skill="Career Stability",
            rubric_param="stability",
            stage="recruiter",
            question=(
                f"Your average tenure is {avg_tenure:.0f} months per company. "
                f"Walk me through what drove each move — specifically, what would have kept you longer at "
                f"{shortest_company or 'your previous employer'}?"
            ),
            what_it_tests="Validates whether short tenures are strategic or a retention risk.",
            evidence_reference=f"Avg tenure: {avg_tenure:.0f}m",
        ))

    # ── 9. Skill recency probe ─────────────────────────────────────────────
    recency_score = (skills_bd.get("skill_recency") or {}).get("score", 0)
    recency_max = (skills_bd.get("skill_recency") or {}).get("max", 6)
    if recency_score < recency_max * 0.5:
        stale_skills = [
            s.get("skill") for s in skills
            if (s.get("recency") or "").upper() in ("OLD", "STALE") and s.get("skill")
        ][:3]
        if stale_skills:
            questions.append(_q(
                priority="medium", theme="skill_recency", skill=", ".join(stale_skills),
                rubric_param="skill_recency",
                stage="recruiter",
                question=(
                    f"Skills like {', '.join(stale_skills)} appear in your resume but look dated. "
                    f"When did you last use these in a production system, and how current is your knowledge today?"
                ),
                what_it_tests="Validates whether old skills are still active or just resume filler.",
                evidence_reference=f"Recency score: {recency_score}/{recency_max}",
            ))

    # ── 10. Skills learning acumen probe ──────────────────────────────────
    questions.append(_q(
        priority="low", theme="skills_learning_acumen", skill="Learning Velocity",
        rubric_param="skills_learning_acumen",
        stage="recruiter",
        question=(
            "What's the most technically difficult thing you taught yourself in the last 12 months, "
            "completely outside your day job requirements? How did you learn it and how have you applied it?"
        ),
        what_it_tests="Tests self-driven learning — separates passive from active learners.",
        evidence_reference="",
    ))

    # ── 11. LinkedIn / extra-curricular probe ─────────────────────────────
    questions.append(_q(
        priority="low", theme="linkedin_activity", skill="LinkedIn Profile",
        rubric_param="linkedin_activity",
        stage="recruiter",
        question=(
            "Do you maintain an active online presence — LinkedIn posts, GitHub contributions, "
            "conference talks, or any community involvement? Walk me through what you've done in the last 6 months."
        ),
        what_it_tests="Validates community engagement and professional brand beyond the resume.",
        evidence_reference="",
    ))
    questions.append(_q(
        priority="low", theme="extra_curriculars", skill="Extra-Curriculars",
        rubric_param="extra_curriculars",
        stage="recruiter",
        question=(
            "Outside of your day job, what technical project, hackathon, or open-source contribution "
            "are you most proud of? What problem did it solve, and who uses it?"
        ),
        what_it_tests="Separates intrinsically motivated engineers from purely transactional ones.",
        evidence_reference="",
    ))

    # ── 12. DNA / operating style probe ───────────────────────────────────
    dna_type = (dna.get("primary_dna") or "HYBRID").upper()
    questions.append(_q(
        priority="low", theme="dna_fit", skill="Operating DNA",
        rubric_param="career_progression",
        stage="recruiter",
        question=(
            "How do you typically split your time between deep individual technical work vs. "
            "collaboration, meetings, and stakeholder communication? "
            "Give me an honest split from your last role."
        ),
        what_it_tests="Validates operating DNA (PRODUCT / CONSULTING / RESEARCH / HYBRID) against role requirements.",
        evidence_reference=f"Detected DNA: {dna_type}",
    ))

    # ── 13. Project deep-dive (recruiter validates coverage) ──────────────
    project_score = (exp_bd.get("project_1") or {}).get("score", 0)
    project_max = (exp_bd.get("project_1") or {}).get("max", 8)
    if project_score < project_max * 0.5:
        project_title = ""
        for p in (experience.get("project_types") or [])[:1]:
            project_title = p.get("title") or ""
        questions.append(_q(
            priority="high", theme="project_quality", skill="Project Quality",
            rubric_param="project_1",
            stage="recruiter",
            question=(
                f"Your primary project{' (' + project_title + ')' if project_title else ''} has limited detail in the resume. "
                f"Walk me through: the problem you were solving, your specific architecture decisions, "
                f"the scale (users, data volume, latency target), and one thing that went wrong."
            ),
            what_it_tests="Validates project complexity — separates 'worked on it' from 'owned it'.",
            evidence_reference=f"Project score: {project_score}/{project_max}",
        ))

    # ── 14. Education value probe (if tier UNKNOWN) ───────────────────────
    inst_tier = (edu_bd.get("institute_tier") or {}).get("score", 5)
    if inst_tier < 2:
        questions.append(_q(
            priority="low", theme="education_value", skill="Education",
            rubric_param="education_job_relevance",
            stage="recruiter",
            question=(
                "Tell me about the most technically rigorous course or project from your education "
                "that directly shaped how you approach problems at work today."
            ),
            what_it_tests="Gives candidates with unknown-tier institutions a chance to demonstrate academic rigour.",
            evidence_reference=f"Institute score: {inst_tier}/5",
        ))

    # ── 15–18. Panel-stage questions (always generated) ───────────────────
    top_skill = (skills[0].get("skill") if skills else None) or "your primary technical skill"
    questions.append(_q(
        priority="high", theme="communication_skills", skill="Communication",
        rubric_param="communication_skills",
        stage="panel",
        question=(
            f"Explain {top_skill} to me as if I'm a product manager with no technical background — "
            f"in under 90 seconds. Then tell me: what's the one thing most people misunderstand about it?"
        ),
        what_it_tests="Tests clarity, audience-awareness, and depth of conceptual understanding.",
        evidence_reference="Panel scores 0–5",
    ))
    questions.append(_q(
        priority="high", theme="domain_skills", skill="Domain Knowledge",
        rubric_param="domain_skills",
        stage="panel",
        question=(
            f"In the domain of {role_family.replace('_', ' ') if role_family else 'your primary domain'}, "
            f"what's a common architectural mistake you've seen in production systems? "
            f"How did you identify it and what did you change?"
        ),
        what_it_tests="Tests real-world domain knowledge beyond textbook theory — exposes hands-on experience.",
        evidence_reference="Panel scores 0–5",
    ))
    questions.append(_q(
        priority="high", theme="project_explanation", skill="Project Walk-Through",
        rubric_param="project_explanation",
        stage="recruiter",
        question=(
            "Walk me through your most technically complex project. Start with the business problem, "
            "explain the constraints you faced, the design decision you're most proud of, "
            "and one decision you'd make differently today."
        ),
        what_it_tests="Tests structured thinking, self-awareness, and ownership depth.",
        evidence_reference="Recruiter scores 0–3",
    ))
    questions.append(_q(
        priority="medium", theme="problem_solving", skill="Live Problem Solving",
        rubric_param="problem_solving",
        stage="panel",
        question=(
            f"I'll give you a scenario: [panel to insert domain-relevant problem here]. "
            f"Before you answer — what clarifying questions do you need to ask? "
            f"Then walk me through your thinking, not just your answer."
        ),
        what_it_tests="Tests systematic decomposition: clarify → enumerate approaches → evaluate trade-offs → commit.",
        evidence_reference="Panel scores 0–3",
    ))

    # De-duplicate and sort by priority
    seen: set[tuple[str, str]] = set()
    unique_questions: list[dict[str, Any]] = []
    for q in questions:
        key = (q["theme"], q["skill"])
        if key not in seen:
            seen.add(key)
            unique_questions.append(q)

    priority_order = {"high": 0, "medium": 1, "low": 2}
    unique_questions.sort(key=lambda q: (priority_order.get(q["priority"], 3), q["stage"] != "panel"))

    recruiter_qs = [q for q in unique_questions if q.get("stage") != "panel"]
    panel_qs     = [q for q in unique_questions if q.get("stage") == "panel"]

    return {
        "recruiter_questions": recruiter_qs,
        "panel_questions":     panel_qs,
        "questions":           unique_questions,   # backwards-compat: all questions
        "rubric_param_help":   RUBRIC_PARAM_HELP,
        "scoring_guides":      SCORING_GUIDES,
        "stage_summary": {
            "recruiter": {
                "total": len(recruiter_qs),
                "high":   sum(1 for q in recruiter_qs if q["priority"] == "high"),
                "params_covered": sorted({q["rubric_param"] for q in recruiter_qs}),
            },
            "panel": {
                "total": len(panel_qs),
                "high":   sum(1 for q in panel_qs if q["priority"] == "high"),
                "params_covered": sorted({q["rubric_param"] for q in panel_qs}),
            },
        },
        "total_questions":        len(unique_questions),
        "high_priority_count":    sum(1 for q in unique_questions if q["priority"] == "high"),
        "medium_priority_count":  sum(1 for q in unique_questions if q["priority"] == "medium"),
        "low_priority_count":     sum(1 for q in unique_questions if q["priority"] == "low"),
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _q(
    priority: str,
    theme: str,
    skill: str,
    rubric_param: str,
    question: str,
    what_it_tests: str,
    evidence_reference: str = "",
    stage: str = "recruiter",
) -> dict[str, Any]:
    guide = SCORING_GUIDES.get(rubric_param, SCORING_GUIDES.get("communication_skills", {}))
    param_help = RUBRIC_PARAM_HELP.get(rubric_param, {})
    return {
        "priority": priority,
        "stage": stage,
        "theme": theme,
        "skill": skill,
        "rubric_param": rubric_param,
        "max_pts": param_help.get("max"),
        "question": question,
        "what_it_tests": what_it_tests,
        "evidence_reference": evidence_reference,
        "scoring_guide": guide,
    }
