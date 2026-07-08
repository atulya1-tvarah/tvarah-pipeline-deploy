"""15 resume templates.  Clients are assigned 5-6 to rotate through.

Each template dict contains:
  id, name, description, target_profiles (list of role/DNA hints),
  section_order (list of section names), emphasis (primary focus),
  formatting_hints (dict).
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 15 canonical resume templates
# ---------------------------------------------------------------------------
RESUME_TEMPLATES: dict[str, dict[str, Any]] = {
    "template_01_executive": {
        "id": "template_01_executive",
        "name": "Executive Leadership",
        "description": "High-level leadership format emphasising strategic impact, P&L ownership, and board-level visibility.",
        "target_profiles": ["DIRECTOR", "VP", "C_SUITE", "HEAD_OF"],
        "section_order": ["executive_summary", "core_competencies", "career_achievements", "experience", "education", "awards"],
        "emphasis": "strategic_impact",
        "formatting_hints": {"max_pages": 2, "bullet_style": "achievement_first", "dates_format": "year_only"},
    },
    "template_02_technical_deep": {
        "id": "template_02_technical_deep",
        "name": "Technical Deep-Dive",
        "description": "Architecture and engineering depth. Best for principal engineers, staff engineers, and architects.",
        "target_profiles": ["PRINCIPAL", "STAFF_ENGINEER", "ARCHITECT", "DATA_ENGINEER"],
        "section_order": ["technical_summary", "skills_matrix", "experience", "notable_projects", "education", "certifications"],
        "emphasis": "technical_depth",
        "formatting_hints": {"max_pages": 3, "bullet_style": "tech_action_result", "dates_format": "month_year"},
    },
    "template_03_skills_led": {
        "id": "template_03_skills_led",
        "name": "Skills-Led",
        "description": "Leads with a comprehensive, grouped skills section. Ideal for career changers or skill-heavy profiles.",
        "target_profiles": ["MID_LEVEL", "CAREER_CHANGER", "FREELANCER"],
        "section_order": ["profile_summary", "skills_and_tools", "experience", "projects", "education"],
        "emphasis": "skill_breadth",
        "formatting_hints": {"max_pages": 2, "bullet_style": "skill_tagged", "dates_format": "month_year"},
    },
    "template_04_project_led": {
        "id": "template_04_project_led",
        "name": "Project-Led",
        "description": "Prominently features 3-4 signature projects with problem/solution/impact framing.",
        "target_profiles": ["ML_ENGINEER", "DATA_SCIENTIST", "PRODUCT_MANAGER"],
        "section_order": ["profile_summary", "signature_projects", "experience", "skills", "education"],
        "emphasis": "project_ownership",
        "formatting_hints": {"max_pages": 2, "bullet_style": "problem_solution_impact", "dates_format": "month_year"},
    },
    "template_05_impact_metrics": {
        "id": "template_05_impact_metrics",
        "name": "Impact & Metrics",
        "description": "Every bullet quantified. Best for candidates with strong measurable outcomes.",
        "target_profiles": ["PRODUCT_MANAGER", "DATA_ANALYST", "GROWTH"],
        "section_order": ["impact_summary", "key_metrics_achieved", "experience", "skills", "education"],
        "emphasis": "quantified_impact",
        "formatting_hints": {"max_pages": 2, "bullet_style": "numbers_first", "dates_format": "month_year"},
    },
    "template_06_consulting": {
        "id": "template_06_consulting",
        "name": "Consulting & Advisory",
        "description": "Client engagement and problem-solving narrative. Suited for consulting DNA profiles.",
        "target_profiles": ["CONSULTANT", "ADVISORY", "SOLUTION_ARCHITECT"],
        "section_order": ["profile_summary", "client_engagements", "competencies", "experience", "education", "certifications"],
        "emphasis": "client_impact",
        "formatting_hints": {"max_pages": 2, "bullet_style": "situation_task_action_result", "dates_format": "month_year"},
    },
    "template_07_product_manager": {
        "id": "template_07_product_manager",
        "name": "Product Manager",
        "description": "PM-focused with roadmap ownership, user metrics, and cross-functional leadership.",
        "target_profiles": ["PRODUCT_MANAGER", "PRODUCT_LEAD"],
        "section_order": ["product_vision", "metrics_owned", "experience", "skills", "education"],
        "emphasis": "product_ownership",
        "formatting_hints": {"max_pages": 2, "bullet_style": "user_impact_outcome", "dates_format": "month_year"},
    },
    "template_08_data_science": {
        "id": "template_08_data_science",
        "name": "Data Science & ML",
        "description": "Model-building, experimentation, and ML system design with publications/patents highlighted.",
        "target_profiles": ["DATA_SCIENTIST", "ML_ENGINEER", "RESEARCH_SCIENTIST"],
        "section_order": ["profile_summary", "research_highlights", "technical_skills", "experience", "publications", "education"],
        "emphasis": "research_and_model_ownership",
        "formatting_hints": {"max_pages": 3, "bullet_style": "hypothesis_method_result", "dates_format": "month_year"},
    },
    "template_09_cloud_infra": {
        "id": "template_09_cloud_infra",
        "name": "Cloud & Infrastructure",
        "description": "DevOps, SRE, and cloud engineering with availability, latency, and cost metrics.",
        "target_profiles": ["DEVOPS", "SRE", "CLOUD_ENGINEER", "PLATFORM_ENGINEER"],
        "section_order": ["profile_summary", "certifications_and_tools", "experience", "infrastructure_projects", "education"],
        "emphasis": "systems_reliability",
        "formatting_hints": {"max_pages": 2, "bullet_style": "tech_metric_outcome", "dates_format": "month_year"},
    },
    "template_10_startup_generalist": {
        "id": "template_10_startup_generalist",
        "name": "Startup Generalist",
        "description": "Fast-paced, multi-hat format emphasising ownership and zero-to-one contributions.",
        "target_profiles": ["FOUNDING_ENGINEER", "FULLSTACK", "EARLY_STAGE"],
        "section_order": ["headline", "what_i_build", "experience", "open_source", "education"],
        "emphasis": "breadth_and_ownership",
        "formatting_hints": {"max_pages": 1, "bullet_style": "ownership_and_launch", "dates_format": "month_year"},
    },
    "template_11_analyst_bi": {
        "id": "template_11_analyst_bi",
        "name": "Analyst & BI",
        "description": "Business intelligence, dashboarding, and reporting with tool and data-source expertise.",
        "target_profiles": ["DATA_ANALYST", "BI_ANALYST", "BUSINESS_ANALYST"],
        "section_order": ["profile_summary", "tools_and_data_sources", "experience", "dashboards_built", "education"],
        "emphasis": "data_driven_decisions",
        "formatting_hints": {"max_pages": 2, "bullet_style": "insight_and_action", "dates_format": "month_year"},
    },
    "template_12_academic_research": {
        "id": "template_12_academic_research",
        "name": "Academic / Research",
        "description": "CV-style with publications, patents, conference talks, and academic credentials leading.",
        "target_profiles": ["RESEARCHER", "PHD", "ACADEMIC"],
        "section_order": ["research_interests", "publications", "experience", "education", "awards", "talks"],
        "emphasis": "research_output",
        "formatting_hints": {"max_pages": 4, "bullet_style": "academic_citation", "dates_format": "year_only"},
    },
    "template_13_international": {
        "id": "template_13_international",
        "name": "International Profile",
        "description": "Multi-country experience with language skills, visa status, and relocation notes front-loaded.",
        "target_profiles": ["GLOBAL_MOBILITY", "EXPAT", "INTERNATIONAL_HIRE"],
        "section_order": ["profile_summary", "language_and_geography", "experience", "skills", "education"],
        "emphasis": "global_exposure",
        "formatting_hints": {"max_pages": 2, "bullet_style": "cross_border_impact", "dates_format": "month_year"},
    },
    "template_14_fresher_campus": {
        "id": "template_14_fresher_campus",
        "name": "Fresher / Campus",
        "description": "Education-first, project-heavy format for candidates with < 2 years of experience.",
        "target_profiles": ["FRESHER", "INTERN", "CAMPUS_HIRE"],
        "section_order": ["education", "projects", "skills", "internships", "certifications", "extracurriculars"],
        "emphasis": "learning_potential",
        "formatting_hints": {"max_pages": 1, "bullet_style": "project_and_learning", "dates_format": "month_year"},
    },
    "template_15_hybrid_narrative": {
        "id": "template_15_hybrid_narrative",
        "name": "Hybrid Narrative",
        "description": "Story-led format combining professional summary paragraphs with bullet highlights. Universal fit.",
        "target_profiles": ["ANY"],
        "section_order": ["career_narrative", "highlights", "experience", "skills", "education"],
        "emphasis": "story_and_evidence",
        "formatting_hints": {"max_pages": 2, "bullet_style": "narrative_with_bullets", "dates_format": "month_year"},
    },
}

# Default assignment: first 6 templates when no explicit assignment
DEFAULT_TEMPLATE_IDS = list(RESUME_TEMPLATES.keys())[:6]


def select_template_for_client(
    client_id: str,
    candidate_rubric: dict[str, Any] | None,
    role_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pick the best template for a candidate given client and role context.

    Falls back to template_15_hybrid_narrative if no strong match.
    """
    from client_config_store import load_client_config
    config = load_client_config(client_id)
    assigned_ids = (config or {}).get("assigned_templates") or DEFAULT_TEMPLATE_IDS
    available = [RESUME_TEMPLATES[tid] for tid in assigned_ids if tid in RESUME_TEMPLATES]
    if not available:
        return RESUME_TEMPLATES["template_15_hybrid_narrative"]

    role_family = (role_config or {}).get("role_family", "").upper() if role_config else ""
    # Map role families to preferred templates
    role_to_template = {
        "DATA_ENGINEER": "template_02_technical_deep",
        "ML_ENGINEER": "template_08_data_science",
        "DATA_SCIENTIST": "template_08_data_science",
        "PRODUCT_MANAGER": "template_07_product_manager",
        "DATA_ANALYST": "template_11_analyst_bi",
        "BI_ANALYST": "template_11_analyst_bi",
        "DEVOPS": "template_09_cloud_infra",
        "SRE": "template_09_cloud_infra",
        "CONSULTANT": "template_06_consulting",
    }
    preferred_id = role_to_template.get(role_family)
    if preferred_id and preferred_id in assigned_ids and preferred_id in RESUME_TEMPLATES:
        return RESUME_TEMPLATES[preferred_id]

    # Fallback: first available
    return available[0]


def render_resume_outline(
    candidate_overview: dict[str, Any],
    skill_analysis: dict[str, Any],
    experience_analysis: dict[str, Any],
    template: dict[str, Any],
) -> dict[str, Any]:
    """Produce a structured resume outline dict from candidate data + template."""
    section_order = template.get("section_order", [])
    top_skills = [s.get("skill") for s in (skill_analysis.get("top_skills") or [])[:10] if s.get("skill")]
    companies = experience_analysis.get("companies") or []
    titles = experience_analysis.get("titles") or []
    years = experience_analysis.get("total_experience_years", 0)

    sections: dict[str, Any] = {}
    for section in section_order:
        if section in ("experience", "work_history"):
            sections[section] = {
                "entries": [
                    {"company": c, "title": t}
                    for c, t in zip(companies[:6], titles[:6])
                ]
            }
        elif section in ("skills", "technical_skills", "skills_and_tools", "skills_matrix"):
            sections[section] = {"skills": top_skills}
        elif section in ("profile_summary", "executive_summary", "career_narrative", "headline"):
            sections[section] = {"text": candidate_overview.get("profile_summary", "")}
        elif section in ("education",):
            sections[section] = {"note": "Education details from candidate record"}
        elif section in ("certifications",):
            sections[section] = {"items": candidate_overview.get("certificates") or []}
        else:
            sections[section] = {}

    return {
        "template_id": template["id"],
        "template_name": template["name"],
        "emphasis": template.get("emphasis"),
        "formatting_hints": template.get("formatting_hints", {}),
        "section_order": section_order,
        "sections": sections,
        "candidate_name": candidate_overview.get("name", ""),
        "total_experience_years": years,
    }
