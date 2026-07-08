
from __future__ import annotations

from taxonomy import DOMAIN_FIT_MAP, ROLE_CORE_SKILLS, ROLE_FAMILY_TAXONOMY

_TECH_ROLES = {
    "AI_ARCHITECT", "GENAI_DATA_SCIENTIST", "CORE_DATA_SCIENTIST", "APPLIED_SCIENTIST",
    "ML_ENGINEER", "MLOPS_DATA_SCIENTIST", "DATA_ENGINEER", "ANALYTICS_ENGINEER",
    "NLP_LLM_ENGINEER", "COMPUTER_VISION_ENGINEER", "ROBOTICS_AUTONOMY_ENGINEER",
    "PLATFORM_ENGINEER", "RESEARCH_SCIENTIST",
}

# Map skill clusters → interview panel types
_CLUSTER_TO_PANEL = {
    "PROGRAMMING": "Coding / Technical Problem Solving",
    "STATISTICS_ML": "ML / Analytics Deep Dive",
    "DEEP_LEARNING_GENAI": "ML / Analytics Deep Dive",
    "MLOPS_DEPLOYMENT": "Architecture / Systems Design",
    "BIG_DATA": "Architecture / Systems Design",
    "VISION_ROBOTICS": "Applied Systems / Robotics Deep Dive",
    "EDGE_EMBEDDED": "Applied Systems / Robotics Deep Dive",
    "PRODUCT_ANALYTICS": "Business Impact / Product Thinking",
    "VISUALIZATION_BI": "Business Impact / Product Thinking",
    "DOMAIN_FINANCE": "Domain / Quant Deep Dive",
    "DOMAIN_RETAIL": "Domain / Retail Analytics Deep Dive",
    "DOMAIN_MARKETING": "Domain / Marketing Analytics Deep Dive",
    "DOMAIN_SUPPLY_CHAIN": "Domain / Supply Chain Analytics Deep Dive",
}


def analyze_qualitative(evidence_map, semantic, experience, dna, score, education=None):
    strengths = []
    gaps = []
    risk_flags = []
    panel = []

    top = semantic.get("top_role_family", "UNKNOWN")
    if top != "UNKNOWN":
        strengths.append(f"Strongest fit appears to be {top.replace('_', ' ').title()} based on clustered skill evidence.")

    top_evidenced = [
        meta for meta in evidence_map.values()
        if meta["evidence_level"] in {"DEEP", "EXPERT", "APPLIED"}
    ]
    top_evidenced.sort(
        key=lambda meta: (
            ["NONE", "MENTION", "WEAK", "APPLIED", "DEEP", "EXPERT"].index(meta["evidence_level"]),
            meta.get("years_of_usage", 0),
            meta.get("matched_context_count", 0),
        ),
        reverse=True,
    )
    if top_evidenced:
        strength_names = ", ".join(meta["skill"] for meta in top_evidenced[:5] if meta.get("skill"))
        strengths.append(f"Best evidenced technical areas include {strength_names}.")

    # Role-specific core skills check (replaces hardcoded PySpark/AWS/SQL/Python)
    core_skills = ROLE_CORE_SKILLS.get(top, ["Python", "SQL"])
    weak_core = [
        skill for skill in core_skills
        if evidence_map.get(skill, {}).get("evidence_level") in {"MENTION", "WEAK", "NONE", None}
        or skill not in evidence_map
    ]
    if weak_core:
        risk_flags.append(f"Core skills needing validation for {top.replace('_', ' ').title()}: {', '.join(weak_core[:4])}.")

    if any(meta.get("architecture_signal") for meta in top_evidenced[:8]):
        strengths.append("Some architecture or design exposure is visible in recent technical work.")
    if any(meta.get("open_source_signal") for meta in top_evidenced[:8]):
        strengths.append("The resume shows at least one open-source or community-style contribution signal.")

    if experience.get("progression"):
        strengths.append("Career progression signal detected across title or organization changes.")
    else:
        gaps.append("Limited visible progression signal from experience chronology.")

    if experience.get("career_trajectory_score", 2) >= 4:
        strengths.append("Career trajectory shows consistent upward seniority progression.")
    elif experience.get("career_trajectory_score", 2) <= 1:
        risk_flags.append("Career trajectory shows a declining or flat seniority pattern.")

    if experience.get("mobility_signal") == "HIGH":
        strengths.append("Mobility signal is high based on location spread or relocation flexibility.")
    if experience.get("loyalty_signal") == "HIGH":
        strengths.append("Average tenure suggests a relatively loyal employment pattern.")
    if experience.get("decision_maker"):
        strengths.append("Decision-making or initiative ownership signal present.")
    elif experience.get("total_experience_years", 0) >= 6:
        gaps.append("Experience level suggests leadership potential, but decision-making evidence is limited.")
    if experience.get("client_facing"):
        strengths.append("Client-facing/stakeholder-facing experience present.")
        panel.append("Business / Stakeholder Interview")
    if experience.get("international_exposure"):
        strengths.append("International/global exposure signal detected.")
    if experience.get("business_impacts"):
        strengths.append("Quantified business impact is visible in the resume.")
        if experience.get("has_verbal_impacts"):
            strengths.append("Verbal impact qualifiers (e.g., doubled, tripled, 2x faster) detected alongside quantified metrics.")
    else:
        gaps.append("Quantified business outcomes are limited or absent.")
    if experience.get("problem_solving_signal_score", 0) >= 4:
        strengths.append("Project language suggests credible problem solving and delivery ownership.")
    if experience.get("project_types"):
        project_mix = ", ".join(sorted({item.get("project_type", "UNKNOWN") for item in experience.get("project_types", []) if item.get("project_type")}))
        gaps.append(f"Project mix inferred from the resume: {project_mix}. Candidate validation may still be needed where project type is unclear.")

    # Domain fit gap for domain-specific roles
    if top in DOMAIN_FIT_MAP:
        required_domains = DOMAIN_FIT_MAP[top].get("required", [])
        candidate_domains = set(experience.get("domain_tags", []))
        if required_domains and not any(d in candidate_domains for d in required_domains):
            domain_str = " or ".join(required_domains)
            risk_flags.append(f"Role requires domain expertise in {domain_str} but no strong domain signal detected in experience.")

    if education:
        if education.get("highest_institute_tier") == "TIER_1":
            strengths.append("Education footprint includes at least one Tier 1 institute signal.")
        if education.get("education_gap_flag"):
            gaps.append("Education-to-employment gap exceeds 12 months and should be validated.")
        if education.get("strongest_course_value_signal") == "FOUNDATIONAL":
            risk_flags.append("Education pedigree appears modest for highly selective roles.")
        # Non-tech degree risk flag for tech roles
        if top in _TECH_ROLES and not education.get("has_tech_degree", True):
            risk_flags.append("No STEM/engineering degree detected; formal technical education may be a gap for this role.")

    # Panel suggestions using top-role cluster weights
    role_config = ROLE_FAMILY_TAXONOMY.get(top, {})
    top_clusters = sorted(role_config.get("weights", {}).items(), key=lambda x: x[1], reverse=True)[:4]
    for cluster, _ in top_clusters:
        panel_type = _CLUSTER_TO_PANEL.get(cluster)
        if panel_type:
            panel.append(panel_type)

    # Fallback panel additions based on cluster presence
    clusters = semantic.get("cluster_map", {})
    if clusters.get("PROGRAMMING"):
        panel.append("Coding / Technical Problem Solving")
    if clusters.get("STATISTICS_ML") or clusters.get("DEEP_LEARNING_GENAI"):
        panel.append("ML / Analytics Deep Dive")
    if clusters.get("MLOPS_DEPLOYMENT") or clusters.get("BIG_DATA"):
        panel.append("Architecture / Systems Design")
    if clusters.get("VISION_ROBOTICS") or clusters.get("EDGE_EMBEDDED"):
        panel.append("Applied Systems / Robotics Deep Dive")
    if clusters.get("PRODUCT_ANALYTICS"):
        panel.append("Business Impact / Product Thinking")

    return {
        "strengths": strengths[:12],
        "gaps": gaps[:12],
        "risk_flags": sorted(set(risk_flags))[:10],
        "panel_suggestion": sorted(set(panel)),
        "recommendation": score["band"],
    }
