
from __future__ import annotations
from fastapi import HTTPException
from evidence import collect_skill_evidence
from semantic_taxonomy import build_semantic_taxonomy
from scoring_engine import compute_score
from dna_engine import classify_dna
from experience_engine import analyze_experience
from education_engine import analyze_education
from rule_based_qualitative import analyze_qualitative
from telephonic_question_engine import build_telephonic_questions
from llm_resume_judge import build_llm_resume_analysis, build_llm_skill_judgments, get_last_skill_judgment_error
from bert_signal_engine import infer_bert_priors
from llm_client import get_llm_telemetry, reset_llm_telemetry
from utils import dedupe_keep_order, first_non_empty, get_by_path, join_location, titlecase_name

def _listify(value):
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]

def _collect_skills(raw):
    skill_sources = [
        raw.get("skills"),
        raw.get("competencies"),
        raw.get("technical_skills"),
        raw.get("tools"),
        raw.get("technologies"),
        raw.get("certified_skills"),
        raw.get("skills_info"),
        get_by_path(raw, "insight_info", "overall_candidate_domain"),
        get_by_path(raw, "domain_data", "overall_candidate_domain"),
    ]
    nested_skills = get_by_path(raw, "insight_info", "skills_info", default={}) or {}
    if isinstance(nested_skills, dict):
        skill_sources.extend(nested_skills.values())
    skills = []
    for source in skill_sources:
        if isinstance(source, dict):
            for inner in source.values():
                skills.extend(_listify(inner))
        else:
            skills.extend(_listify(source))
    return dedupe_keep_order(skills)

def _collect_text_list(*sources):
    values = []
    for source in sources:
        if isinstance(source, list):
            values.extend(source)
        elif isinstance(source, dict):
            values.extend(source.values())
        elif source not in (None, "", {}, []):
            values.append(source)
    flattened = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
        elif value not in (None, "", {}, []):
            flattened.append(value)
    return dedupe_keep_order(flattened)

def _generate_profile_summary(personal_info, skills, experience_items, domain_hints):
    name = personal_info.get("name") or "The candidate"
    titles = [str(item.get("title") or item.get("role") or "").strip() for item in experience_items if item.get("title") or item.get("role")]
    current_title = titles[0] if titles else "professional"
    companies = [str(item.get("company") or item.get("organization") or "").strip() for item in experience_items if item.get("company") or item.get("organization")]
    top_skills = ", ".join(skills[:6]) if skills else "relevant technical capabilities"
    domains = ", ".join(domain_hints[:3]) if domain_hints else "data and AI delivery"
    company_note = f" across organizations such as {', '.join(companies[:2])}" if companies else ""
    return (
        f"{name} appears to be a {current_title} with experience in {domains}{company_note}. "
        f"The resume highlights strengths in {top_skills}. "
        f"The profile suggests a mix of technical delivery, practical problem solving, and progressive responsibility."
    )

def _normalize_experience_item(item):
    if not isinstance(item, dict):
        return None
    return {
        "company": first_non_empty(item.get("company"), item.get("organization"), item.get("company_name")),
        "organization": first_non_empty(item.get("organization"), item.get("company"), item.get("company_name")),
        "title": first_non_empty(item.get("title"), item.get("role"), item.get("job_title")),
        "role": first_non_empty(item.get("role"), item.get("title"), item.get("job_title")),
        "start_date": first_non_empty(item.get("start_date"), item.get("from")),
        # is_current_role is a structured boolean signal from the extractor
        # and takes priority over a literal end_date string when it says
        # "yes, ongoing" -- confirmed live that the extractor sometimes
        # writes a end_date anyway for a role it also correctly marked
        # is_current_role=True (e.g. reusing the start date as a bogus end
        # date), and first_non_empty() would previously pick that wrong
        # literal value since it's checked before is_current_role at all.
        # Also fixes a dead comparison: this checked
        # str(is_current_role).lower() == "yes", but the field is a JSON
        # boolean (str(True).lower() == "true", never "yes"), so the
        # fallback never actually fired even in the cases it was reached.
        "end_date": (
            "Present"
            if item.get("is_current_role") in (True, "true", "True", "yes", "Yes", 1)
            else first_non_empty(item.get("end_date"), item.get("to"), item.get("duration_end"))
        ),
        "description": first_non_empty(item.get("description"), item.get("role_description"), item.get("experience_insights")),
        "skills": dedupe_keep_order(item.get("skills", [])),
        "location": first_non_empty(item.get("location"), item.get("company_location")),
        "employment_type": item.get("employment_type"),
        "raw": item,
    }

def normalize_resume_data(raw):
    if not isinstance(raw, dict):
        return {"raw_data": raw}
    extraction_wrapper = raw if any(key in raw for key in ("resume_data", "jd_data", "judge_results", "reflection_loop")) else None
    if isinstance(raw.get("resume_data"), dict):
        raw = raw["resume_data"]
    top_level_personal = raw.get("personal_info", {}) if isinstance(raw.get("personal_info"), dict) else {}
    top_level_contact = raw.get("contact_info", {}) if isinstance(raw.get("contact_info"), dict) else {}
    nested_personal = get_by_path(raw, "basic_info", "personal_info", default={}) or {}
    nested_contact = get_by_path(raw, "basic_info", "contact_info", default={}) or {}
    personal = {**top_level_personal, **nested_personal}
    contact = {**top_level_contact, **nested_contact}
    insight = raw.get("insight_info", {}) if isinstance(raw.get("insight_info"), dict) else {}
    work_items = []
    for key in ["experience", "work_experience", "professional_experience", "employment", "projects"]:
        value = raw.get(key)
        if isinstance(value, list):
            work_items.extend(value)
    work_items.extend(_listify(raw.get("work_experience_info")))
    work_items.extend(_listify(insight.get("work_experience_info")))
    normalized_experience = [item for item in (_normalize_experience_item(entry) for entry in work_items) if item]
    summary = first_non_empty(
        raw.get("profile_summary"),
        raw.get("summary"),
        raw.get("professional_summary"),
        raw.get("overall_insights"),
        insight.get("overall_insights"),
        raw.get("education_insights"),
        insight.get("education_insights"),
    )
    location = first_non_empty(
        raw.get("location"),
        get_by_path(raw, "personal_info", "location"),
        contact.get("postal_address"),
        join_location(contact.get("current_city"), contact.get("current_state"), contact.get("current_country")),
    )
    personal_info = {
        "name": titlecase_name(first_non_empty(raw.get("name"), get_by_path(raw, "personal_info", "name"), get_by_path(raw, "personal_info", "full_name"), personal.get("name"), personal.get("full_name"), " ".join(part for part in [personal.get("first_name"), personal.get("middle_name"), personal.get("last_name")] if part))),
        "email": first_non_empty(raw.get("email"), get_by_path(raw, "personal_info", "email"), contact.get("primary_email"), contact.get("secondary_email")),
        "phone": first_non_empty(raw.get("phone"), get_by_path(raw, "personal_info", "phone"), contact.get("primary_phone_number"), contact.get("secondary_phone_number")),
        "location": location,
        "work_authorization": personal.get("work_authorization"),
        "current_city": contact.get("current_city"),
        "current_country": contact.get("current_country"),
    }
    domain_hints = _collect_text_list(
        get_by_path(raw, "domain_data", "overall_candidate_domain"),
        get_by_path(raw, "insight_info", "overall_candidate_domain"),
        raw.get("domains"),
        raw.get("industry"),
        raw.get("domain"),
    )
    competencies = _collect_text_list(
        raw.get("competencies"),
        raw.get("core_competencies"),
        raw.get("functional_competencies"),
        get_by_path(raw, "basic_info", "competencies"),
        domain_hints,
    )
    certificates = []
    for education in _listify(insight.get("education_info")):
        if not isinstance(education, dict):
            continue
        if str(education.get("education_level", "")).lower() == "certification":
            certificates.append(first_non_empty(education.get("field_of_study"), education.get("degree"), education.get("institution_name")))
    certificates.extend(_listify(raw.get("certificates")))
    certificates.extend(_listify(raw.get("certifications")))
    patents = _collect_text_list(raw.get("patents"), get_by_path(raw, "basic_info", "patents"))
    achievements = _collect_text_list(raw.get("achievements"), insight.get("achievements"), get_by_path(raw, "basic_info", "achievements"))
    extracurricular = _collect_text_list(raw.get("extracurricular_activities"), raw.get("extra_curricular"), raw.get("extracurriculars"))
    if not summary:
        summary = _generate_profile_summary(personal_info, _collect_skills(raw), normalized_experience, domain_hints)
    normalized = {
        "name": personal_info["name"],
        "email": personal_info["email"],
        "phone": personal_info["phone"],
        "location": personal_info["location"],
        "profile_summary": summary,
        "summary": summary,
        "personal_info": personal_info,
        "skills": _collect_skills(raw),
        "competencies": dedupe_keep_order(competencies + _collect_skills(raw) + domain_hints),
        "technical_skills": _collect_skills(raw),
        "experience": normalized_experience,
        "education": _listify(raw.get("education_info")) + _listify(insight.get("education_info")) + _listify(raw.get("education")),
        "certificates": dedupe_keep_order(certificates),
        "certifications": dedupe_keep_order(certificates),
        "patents": patents,
        "achievements": achievements,
        "extracurricular_activities": extracurricular,
        "projects": dedupe_keep_order(_listify(raw.get("projects"))),
        "domain_data": raw.get("domain_data", {}),
        "insight_info": insight,
        "raw_data": raw,
        "extraction_metadata": {
            "judge_results": extraction_wrapper.get("judge_results", []) if extraction_wrapper else [],
            "reflection_loop": extraction_wrapper.get("reflection_loop", 0) if extraction_wrapper else 0,
            "wrapped_input": bool(extraction_wrapper),
        },
    }
    return normalized

def _candidate_overview(resume_data):
    personal = resume_data.get("personal_info", {}) if isinstance(resume_data.get("personal_info"), dict) else {}
    summary = first_non_empty(resume_data.get("profile_summary"), resume_data.get("summary"), resume_data.get("professional_summary"))
    return {
        "name": first_non_empty(personal.get("name"), resume_data.get("name"), "N/A"),
        "email": first_non_empty(personal.get("email"), resume_data.get("email"), "N/A"),
        "phone": first_non_empty(personal.get("phone"), resume_data.get("phone"), "N/A"),
        "location": first_non_empty(personal.get("location"), resume_data.get("location"), "N/A"),
        "profile_summary": summary or "Profile summary not explicitly provided in the resume.",
        "competencies": first_non_empty(resume_data.get("competencies"), resume_data.get("skills"), resume_data.get("technical_skills"), []),
        "certificates": first_non_empty(resume_data.get("certificates"), resume_data.get("certifications"), []),
        "patents": first_non_empty(resume_data.get("patents"), []),
        "achievements": first_non_empty(resume_data.get("achievements"), []),
        "extracurricular_activities": first_non_empty(resume_data.get("extracurricular_activities"), []),
    }


def _merge_project_type_priors(experience: dict[str, Any], bert_priors: dict[str, Any]) -> dict:
    project_types = [dict(item) for item in (experience.get("project_types") or []) if isinstance(item, dict)]
    predicted = bert_priors.get("project_type_priors", []) if isinstance(bert_priors, dict) else []
    if not project_types or not predicted:
        return experience
    prediction_lookup = {
        (str(item.get("company") or "").strip().lower(), str(item.get("title") or "").strip().lower()): item
        for item in predicted
        if isinstance(item, dict)
    }
    updated = []
    for item in project_types:
        key = (
            str(item.get("company") or "").strip().lower(),
            str(item.get("title") or "").strip().lower(),
        )
        prior = prediction_lookup.get(key)
        if prior and float(prior.get("confidence") or 0) >= 0.55:
            item["project_type_prior"] = prior.get("predicted_project_type")
            item["project_type_prior_confidence"] = prior.get("confidence")
            if item.get("project_type") in {None, "", "UNKNOWN"}:
                item["project_type"] = prior.get("predicted_project_type")
        updated.append(item)
    return {
        **experience,
        "project_types": updated,
    }


def _merge_role_family_prior(semantic: dict[str, Any], bert_priors: dict[str, Any]) -> dict:
    prior = bert_priors.get("role_family_prior", {}) if isinstance(bert_priors, dict) else {}
    predicted = str(prior.get("label") or "").strip().upper()
    confidence = float(prior.get("confidence") or 0)
    if not predicted:
        return semantic
    role_scores = [dict(item) for item in (semantic.get("role_family_scores") or []) if isinstance(item, dict)]
    found = False
    for item in role_scores:
        if str(item.get("role_family") or "").strip().upper() == predicted:
            found = True
            item["bert_confidence"] = confidence
            item["bert_candidates"] = prior.get("candidates", [])
            if confidence >= 0.55:
                item["score"] = max(int(item.get("score") or 0), int(round((confidence * 10) + 15)))
    if not found:
        role_scores.append(
            {
                "role_family": predicted,
                "score": int(round((confidence * 10) + 10)),
                "matched_clusters": [],
                "must_have_hits": 0,
                "title_bonus": 0,
                "bert_confidence": confidence,
                "bert_candidates": prior.get("candidates", []),
            }
        )
    role_scores.sort(key=lambda item: item.get("score", 0), reverse=True)
    top_role_family = semantic.get("top_role_family")
    if confidence >= 0.65:
        top_role_family = predicted
    return {
        **semantic,
        "role_family_scores": role_scores,
        "top_role_family": top_role_family,
        "bert_role_family_prior": prior,
    }


def _merge_dna_prior(dna: dict[str, Any], bert_priors: dict[str, Any]) -> dict:
    prior = bert_priors.get("dna_prior", {}) if isinstance(bert_priors, dict) else {}
    predicted = str(prior.get("label") or "").strip().upper()
    confidence = float(prior.get("confidence") or 0)
    if not predicted:
        return dna
    updated = {
        **dna,
        "bert_dna_prior": prior,
    }
    if confidence >= 0.6:
        updated["primary_dna"] = predicted
    return updated


def _attach_skill_priors(top_skills: list[dict], bert_priors: dict[str, Any]) -> list[dict]:
    priors = bert_priors.get("skill_depth_priors", []) if isinstance(bert_priors, dict) else []
    prior_lookup = {
        str(item.get("skill") or "").strip().lower(): item
        for item in priors
        if isinstance(item, dict) and str(item.get("skill") or "").strip()
    }
    updated = []
    for item in top_skills:
        prior = prior_lookup.get(str(item.get("skill") or "").strip().lower())
        enriched = dict(item)
        if prior:
            enriched["bert_depth_prior"] = prior.get("predicted_depth_label")
            enriched["bert_depth_confidence"] = prior.get("confidence")
            enriched["bert_depth_candidates"] = prior.get("candidates", [])
        updated.append(enriched)
    return updated

def analyze_resume(resume_input):
    import logging as _logging
    _diag = _logging.getLogger("resume_intelligence.diag")
    reset_llm_telemetry()
    raw_resume_data = resume_input.data if hasattr(resume_input, "data") else resume_input
    resume_data = normalize_resume_data(raw_resume_data)
    overview = _candidate_overview(resume_data)
    evidence_map = collect_skill_evidence(resume_data)
    _diag.info("DIAG: evidence collected")
    semantic = build_semantic_taxonomy(evidence_map, resume_data)
    _diag.info("DIAG: semantic taxonomy built")
    experience = analyze_experience(resume_data)
    _diag.info("DIAG: experience analyzed")
    education = analyze_education(resume_data)
    _diag.info("DIAG: education analyzed")
    dna = classify_dna(resume_data, top_role_family=semantic.get("top_role_family", "UNKNOWN"))
    _diag.info("DIAG: dna classified, entering infer_bert_priors")
    bert_priors = infer_bert_priors(overview, resume_data, evidence_map, semantic, experience, dna)
    _diag.info("DIAG: bert_priors done")
    semantic = _merge_role_family_prior(semantic, bert_priors)
    experience = _merge_project_type_priors(experience, bert_priors)
    dna = _merge_dna_prior(dna, bert_priors)

    # ── Credibility computation (reverse-engineer expected skills vs claimed) ─
    _cred: dict[str, Any] = {}
    try:
        _diag.info("DIAG: entering compute_experience_credibility")
        from experience_credibility import compute_experience_credibility
        _cred = compute_experience_credibility(experience, semantic, evidence_map)
        _diag.info("DIAG: compute_experience_credibility done")
        experience = {**experience, "_credibility": _cred}
    except Exception as _cred_exc:
        import logging
        logging.getLogger("resume_intelligence.engine").warning(
            "Credibility scoring failed: %s", _cred_exc
        )

    # ── LLM project judgment (deep reverse-engineering per project + candidate assessment) ──
    try:
        _diag.info("DIAG: entering LLM project judgment block")
        from llm_experience_judge import judge_projects_llm
        from company_intelligence import enrich_company_context
        project_types = experience.get("project_types") or []
        _diag.info("DIAG: project_types count=%s", len(project_types))
        if project_types:
            # Pre-build company intel for all unique companies
            _cmp_intel_map: dict[str, Any] = {}
            for _pt in project_types[:2]:
                _cname = str(_pt.get("company") or "").strip()
                if _cname and _cname not in _cmp_intel_map:
                    _diag.info("DIAG: enriching company context for %s", _cname)
                    _cmp_intel_map[_cname] = enrich_company_context(_cname)
            _diag.info("DIAG: company intel done, calling judge_projects_llm")

            _llm_project_result = judge_projects_llm(
                project_items=project_types,
                experience=experience,
                credibility=_cred,
                max_projects=2,
                company_intel_map=_cmp_intel_map,
            )
            _diag.info("DIAG: judge_projects_llm returned")
            if _llm_project_result:
                experience = {**experience, "_llm_project_judgment": _llm_project_result}
    except Exception as _pj_exc:
        import logging
        logging.getLogger("resume_intelligence.engine").warning(
            "LLM project judgment failed: %s", _pj_exc
        )

    score = compute_score(evidence_map, semantic, experience, dna, education)
    if score is None:
        from llm_client import get_last_llm_error
        reason = get_last_llm_error() or "LLM scoring service unavailable."
        raise HTTPException(status_code=503, detail=f"AI scoring unavailable: {reason}")
    base_result = {
        "candidate_overview": overview,
        "semantic_analysis": semantic,
        "experience_analysis": experience,
        "education_analysis": education,
        "dna_fit": dna,
        "scorecard": score,
        "bert_priors": bert_priors,
    }
    import os
    if os.getenv("ENABLE_NEW_RUBRIC", "false").lower() == "true":
        try:
            from rubric_engine import compute_rubric_score
            rubric_result = compute_rubric_score(
                evidence_map, semantic, experience, dna, education, bert_priors,
                client_role_config=None,
                raw_data=raw_resume_data,
            )
            base_result["rubric_scorecard"] = rubric_result
        except Exception as _rubric_exc:
            import logging
            logging.getLogger("resume_intelligence.engine").warning(
                "Rubric scoring failed: %s", _rubric_exc
            )
    top_skills = sorted(
        evidence_map.values(),
        key=lambda x: (
            ["NONE","MENTION","WEAK","APPLIED","DEEP","EXPERT"].index(x["evidence_level"]),
            x["years_of_usage"],
            1 if x.get("cluster") else 0,
            len(x.get("contexts", [])),
        ),
        reverse=True,
    )
    result = dict(base_result)
    result["skill_analysis"] = {"top_skills": _attach_skill_priors(top_skills, bert_priors), "all_skills": evidence_map}
    qualitative = analyze_qualitative(evidence_map, semantic, experience, dna, score, education)
    llm_skill_judgments = build_llm_skill_judgments(overview, result["skill_analysis"], score)
    skill_judgment_error = get_last_skill_judgment_error()
    llm_analysis = build_llm_resume_analysis(overview, result["skill_analysis"], semantic, experience, dna, score)
    def _fallback_skill_reason(skill_item):
        evidence_level = str(skill_item.get("evidence_level", "NONE")).title().lower()
        weighted_years = skill_item.get("years_of_usage", 0)
        raw_years = skill_item.get("raw_years_of_usage", 0)
        recency = str(skill_item.get("recency", "UNKNOWN")).lower()
        contexts = skill_item.get("matched_context_count", 0)
        project_contexts = dedupe_keep_order(skill_item.get("project_contexts", []))
        artifact_evidence = skill_item.get("artifact_evidence", []) or []
        advanced_topics = skill_item.get("advanced_topic_signals", []) or []
        coding_strength = str(skill_item.get("coding_strength_signal", "")).lower()
        sentences = []
        if weighted_years:
            sentences.append(
                f"{contexts} role context{'s' if contexts != 1 else ''} give this skill about {weighted_years} weighted years of {evidence_level} evidence."
            )
        elif raw_years:
            sentences.append(f"The resume mentions this skill across roughly {raw_years} raw years, but the weighted proof is still limited.")
        else:
            sentences.append("The skill is mentioned, but the resume does not provide enough dated evidence to estimate usable tenure.")
        if recency and recency != "unknown":
            sentences.append(f"The strongest evidence is {recency}.")
        if project_contexts:
            mix = ", ".join(project_contexts).replace("_", " ").lower()
            sentences.append(f"It appears mostly in {mix} work.")
        if skill_item.get("architecture_signal"):
            sentences.append("There are design or architecture cues around the work.")
        elif skill_item.get("coding_signal"):
            strength_phrase = f"{coding_strength} coding" if coding_strength and coding_strength != "limited" else "hands-on coding"
            sentences.append(f"The evidence suggests {strength_phrase} involvement.")
        if advanced_topics:
            sentences.append(f"Advanced topics surfaced around {', '.join(advanced_topics[:2])}.")
        if artifact_evidence:
            sentences.append("There is supporting artifact evidence tied to this skill.")
        if skill_item.get("open_source_signal"):
            sentences.append("Open-source style contribution signals are also present.")
        if skill_item.get("upskill_signal"):
            sentences.append("The timeline also suggests the candidate has continued to build this skill recently.")
        return " ".join(sentences[:4])
    def _fallback_skill_label(skill_item):
        level = str(skill_item.get("evidence_level", "NONE")).upper()
        depth = str(skill_item.get("depth_label", "")).upper()
        if level == "EXPERT" and depth == "ARCHITECT_LEVEL":
            return "Architect-level likely"
        if depth == "ADVANCED":
            return "Advanced evidence"
        if depth == "HANDS_ON":
            return "Hands-on evidence"
        if depth == "FOUNDATIONAL" or level in {"WEAK", "MENTION"}:
            return "Mentioned, needs validation"
        return "Limited evidence"

    for skill_item in result["skill_analysis"]["top_skills"]:
        skill_item["judged_strength_label"] = _fallback_skill_label(skill_item)
        skill_item["judged_score_0_to_5"] = None
        skill_item["judged_confidence"] = "Fallback"
        skill_item["judged_reason"] = _fallback_skill_reason(skill_item)
        skill_item["judged_evidence_used"] = []
        skill_item["interview_probe"] = ""
    result["llm_status"] = {
        "score_judgment": "applied" if score.get("llm_used") else "fallback_used",
        "skill_judgment": "applied" if llm_skill_judgments else "fallback_used",
        "skill_judgment_reason": "" if llm_skill_judgments else (skill_judgment_error or "The LLM skill-judgment response was unavailable or invalid for this run, so evidence-based fallback reasoning was used."),
        "dna_judgment": "applied" if llm_analysis else "fallback_used",
        "dna_judgment_reason": "" if llm_analysis else "DNA fit currently falls back to deterministic prior signals because the LLM DNA judgment was unavailable for this run.",
    }
    skill_judgments = llm_skill_judgments.get("top_skill_judgments", []) if isinstance(llm_skill_judgments, dict) else []
    judgment_by_skill = {
        str(item.get("skill", "")).strip().lower(): item
        for item in skill_judgments
        if isinstance(item, dict) and str(item.get("skill", "")).strip()
    }
    for skill_item in result["skill_analysis"]["top_skills"]:
        judged = judgment_by_skill.get(str(skill_item.get("skill", "")).strip().lower())
        if not judged:
            continue
        skill_item["judged_score_0_to_5"] = judged.get("score_0_to_5")
        skill_item["judged_strength_label"] = judged.get("verdict_label") or skill_item.get("judged_strength_label")
        skill_item["judged_confidence"] = judged.get("confidence") or "LLM"
        skill_item["judged_reason"] = judged.get("reason") or skill_item.get("judged_reason")
        skill_item["judged_evidence_used"] = judged.get("evidence_used") or []
        skill_item["interview_probe"] = judged.get("interview_probe") or ""
    if llm_analysis:
        semantic_update = llm_analysis.get("semantic_analysis", {}) if isinstance(llm_analysis.get("semantic_analysis"), dict) else {}
        dna_update = llm_analysis.get("dna_judgment", {}) if isinstance(llm_analysis.get("dna_judgment"), dict) else {}
        qualitative_update = llm_analysis.get("qualitative_analysis", {}) if isinstance(llm_analysis.get("qualitative_analysis"), dict) else {}
        result["semantic_analysis"] = {
            **semantic,
            "top_role_family": semantic_update.get("top_role_family", semantic.get("top_role_family")),
            "recruiter_summary": semantic_update.get("recruiter_summary", ""),
            "role_family_rationale": semantic_update.get("role_family_rationale", ""),
            "consistency_readout": semantic_update.get("consistency_readout", ""),
            "inferred_strength_areas": semantic_update.get("inferred_strength_areas", semantic.get("inferred_skills", [])),
        }
        result["qualitative_analysis"] = {
            **qualitative,
            **qualitative_update,
        }
        result["dna_fit"] = {
            **dna,
            "primary_dna": dna_update.get("primary_dna", dna.get("primary_dna")),
            "llm_confidence": dna_update.get("confidence", ""),
            "llm_reason": dna_update.get("reason", ""),
            "llm_evidence_used": dna_update.get("evidence_used", []),
        }
        result["llm_analysis_used"] = True
    else:
        result["qualitative_analysis"] = qualitative
        result["semantic_analysis"] = {
            **semantic,
            "recruiter_summary": "",
            "role_family_rationale": "",
            "consistency_readout": "",
            "inferred_strength_areas": semantic.get("inferred_skills", []),
        }
        result["dna_fit"] = {
            **dna,
            "llm_confidence": "",
            "llm_reason": "",
            "llm_evidence_used": [],
        }
        result["llm_analysis_used"] = False
    result["telephonic_round"] = build_telephonic_questions(result)
    result["llm_telemetry"] = get_llm_telemetry()
    return result
