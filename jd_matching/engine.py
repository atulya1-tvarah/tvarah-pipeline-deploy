from __future__ import annotations
import json
from .models import HiringManagerInputs
from .orchestrator import run_matching_orchestration
from .prompts import MATCH_SYSTEM, MATCH_USER


def _get_jd_root(jd_json: dict) -> dict:
    return jd_json.get("jd_data", jd_json)


def _get_resume_root(resume_json: dict) -> dict:
    return resume_json.get("resume_data", resume_json)


def generate_screening_questions(resume_json: dict, jd_json: dict, deterministic: dict) -> list[dict]:
    overall_score = deterministic.get("overall_score", 0)
    if overall_score < 30:
        return []
    questions = []
    jd_root = _get_jd_root(jd_json)
    resume_root = _get_resume_root(resume_json)
    skill_details = deterministic.get("skill_match_details", {}) or {}
    matched_mandatory = skill_details.get("matched_mandatory", []) or []
    missing_mandatory = skill_details.get("missing_mandatory", []) or []
    adjacent_mandatory = skill_details.get("adjacent_mandatory", []) or []
    role_title = jd_root.get("role_title", "this role")
    responsibilities = jd_root.get("summary_responsibilities", []) or []
    if matched_mandatory:
        skill = matched_mandatory[0]
        questions.append({"question": f"Walk me through one end-to-end problem where you used {skill} in a production, client, or business-critical context relevant to {role_title}.",
                          "intent": "Validate depth, ownership, and business outcome on a core matched skill",
                          "what_good_answer_looks_like": "Specific context, exact contribution, decisions taken, trade-offs, and measurable result."})
    if adjacent_mandatory:
        skill = adjacent_mandatory[0]
        questions.append({"question": f"This role asks for {skill}. Your profile shows adjacent evidence rather than direct proof. What closest tools or environments have you used, and what would translate directly?",
                          "intent": "Separate adjacent skill transfer from direct experience",
                          "what_good_answer_looks_like": "Honest gap acknowledgment plus concrete adjacent tooling and fast-transfer logic."})
    if missing_mandatory:
        skill = missing_mandatory[0]
        questions.append({"question": f"{skill} is not clearly evidenced. Is it genuinely missing, lightly used, or simply under-described on the resume?",
                          "intent": "Test honesty and reduce false negatives from resume wording",
                          "what_good_answer_looks_like": "Clear answer with examples instead of vague reassurance."})
    if responsibilities:
        questions.append({"question": f"Which of your past roles maps most directly to this expectation: '{responsibilities[0]}'?",
                          "intent": "Map actual role history to the JD's highest-value responsibility",
                          "what_good_answer_looks_like": "A direct, non-generic mapping with outcomes and scope."})
    questions.append({"question": "Which claim on your resume would you most want me to verify in an interview because it best represents your value?",
                      "intent": "Find the strongest authentic evidence and test confidence",
                      "what_good_answer_looks_like": "One strong claim with verifiable detail, not a broad self-pitch."})
    jd_text = json.dumps(jd_root, ensure_ascii=False).lower()
    if any(kw in jd_text for kw in ["stakeholder", "communication", "presentation", "leadership", "cross-functional"]):
        questions.append({"question": "Describe a situation where you had to communicate a complex technical finding to a non-technical audience — what was the outcome?",
                          "intent": "Assess communication depth and stakeholder influence",
                          "what_good_answer_looks_like": "Specific audience, the complexity simplified, the business decision it drove."})
    resume_certs = (resume_root.get("skills_info") or {}).get("certified_skills") or []
    if resume_certs or any(kw in jd_text for kw in ["certified", "certification", "aws certified", "pmp", "azure"]):
        questions.append({"question": "Which certifications on your profile are actively applied in your daily work, and which were primarily for career progression?",
                          "intent": "Distinguish practical certification value from credential collection",
                          "what_good_answer_looks_like": "Honest distinction with concrete examples of applied certified skills."})
    jd_work_mode = (jd_root.get("work_mode") or "").lower()
    jd_locations = jd_root.get("location") or jd_root.get("locations") or []
    if jd_locations and "remote" not in jd_work_mode:
        loc_str = jd_locations if isinstance(jd_locations, str) else ", ".join(str(x) for x in (jd_locations if isinstance(jd_locations, list) else [jd_locations])[:2])
        questions.append({"question": f"This role is based in {loc_str}. Are you currently located there, or would relocation or commuting be required — and is that feasible for you?",
                          "intent": "Surface location constraints early to avoid late-stage drop-offs",
                          "what_good_answer_looks_like": "Clear yes/no on current location, honest statement on relocation or commute feasibility."})
    jd_level = jd_root.get("job_level") or jd_root.get("seniority_level") or ""
    tiles = deterministic.get("top_tiles", {})
    job_level_fit = tiles.get("job_level_fit", 65)
    if jd_level and job_level_fit < 70:
        questions.append({"question": f"This role is defined as {jd_level}. How would you describe your current operating level — are you in growth mode, operating fully at level, or looking to step back into a more focused individual-contributor role?",
                          "intent": "Validate seniority alignment and detect overqualification or underqualification early",
                          "what_good_answer_looks_like": "Honest self-assessment with clarity on what they want from this role and why."})
    return questions[:10]


def generate_match(resume_json: dict, jd_json: dict, hiring_manager_inputs: dict | None = None) -> dict:
    hmi = HiringManagerInputs(**(hiring_manager_inputs or {}))
    deterministic = run_matching_orchestration(resume_json, jd_json, hmi.model_dump())
    parsed = {
        "recruiter_summary": deterministic.get("recruiter_summary", "Deterministic semantic analysis returned."),
        "strengths": deterministic["quick_view"]["top_strengths"],
        "risks": deterministic["quick_view"]["top_gaps"],
        "rationale": deterministic["quick_view"]["screening_questions"],
    }
    # LLM polishing is disabled — config.py not available in resume_intelligence
    llm = None
    final = {**deterministic,
        "recruiter_summary": parsed["recruiter_summary"],
        "strengths": parsed["strengths"],
        "risks": parsed["risks"],
        "rationale": parsed["rationale"],
        "screening_questions": generate_screening_questions(resume_json=resume_json, jd_json=jd_json, deterministic=deterministic),
        "llm_powered": False,
    }
    return final
