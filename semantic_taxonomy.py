
from __future__ import annotations
from collections import defaultdict
from taxonomy import ROLE_FAMILY_TAXONOMY

def build_semantic_taxonomy(evidence_map, resume_data=None):
    cluster_map = defaultdict(list)
    for skill, meta in evidence_map.items():
        cluster = meta.get("cluster")
        if cluster and meta.get("evidence_level") != "NONE":
            cluster_map[cluster].append(skill)
    cluster_map = {k: sorted(set(v)) for k,v in cluster_map.items()}
    flat = {s for skills in cluster_map.values() for s in skills}
    title_bonus = defaultdict(int)
    total_years = 0
    if isinstance(resume_data, dict):
        total_years = sum((item.get("end_date") is not None) for item in resume_data.get("experience", []))
        for item in resume_data.get("experience", []):
            title = str(item.get("title") or item.get("role") or "").lower()
            if "architect" in title:
                title_bonus["AI_ARCHITECT"] += 4
            if "lead" in title or "principal" in title:
                title_bonus["AI_ARCHITECT"] += 2
                title_bonus["ML_ENGINEER"] += 1
            if "data scientist" in title or "machine learning engineer" in title:
                title_bonus["CORE_DATA_SCIENTIST"] += 1
                title_bonus["GENAI_DATA_SCIENTIST"] += 1
                title_bonus["APPLIED_SCIENTIST"] += 1
            if "ml engineer" in title or "machine learning engineer" in title:
                title_bonus["ML_ENGINEER"] += 2
                title_bonus["NLP_LLM_ENGINEER"] += 1
            if "autonomy" in title or "robotics" in title:
                title_bonus["ROBOTICS_AUTONOMY_ENGINEER"] += 3
                title_bonus["COMPUTER_VISION_ENGINEER"] += 2
            if "analyst" in title:
                title_bonus["PRODUCT_DATA_SCIENTIST"] += 1
            if "data engineer" in title or "analytics engineer" in title:
                title_bonus["DATA_ENGINEER"] += 2
                title_bonus["ANALYTICS_ENGINEER"] += 2
    role_scores=[]
    for family, meta in ROLE_FAMILY_TAXONOMY.items():
        score=0; matched=[]
        for cluster, weight in meta["weights"].items():
            if cluster_map.get(cluster):
                score += weight; matched.append(cluster)
        must = sum(1 for s in meta["must_have_any"] if s in flat)
        bonus = title_bonus.get(family, 0)
        if family == "AI_ARCHITECT" and bonus == 0:
            score -= 4
        if family == "AI_ARCHITECT" and len(cluster_map.get("SYSTEMS_ARCHITECTURE", [])) == 0:
            score -= 2
        if family in {"ROBOTICS_AUTONOMY_ENGINEER", "COMPUTER_VISION_ENGINEER"} and len(cluster_map.get("VISION_ROBOTICS", [])) == 0:
            score -= 3
        role_scores.append({"role_family": family, "score": score + must + bonus, "matched_clusters": matched, "must_have_hits": must, "title_bonus": bonus})
    role_scores.sort(key=lambda x: x["score"], reverse=True)
    top = role_scores[0]["role_family"] if role_scores else "UNKNOWN"
    low = {s.lower() for s in flat}
    inferred=[]
    if "pyspark" in low or "spark" in low: inferred.append("Distributed Data Processing")
    if "docker" in low and "kubernetes" in low: inferred.append("Containerized Deployment")
    if "transformers" in low or "rag" in low: inferred.append("LLM Application Engineering")
    if "attribution modeling" in low or "mmm" in low: inferred.append("Marketing Measurement")
    if "milp" in low or "pyomo" in low: inferred.append("Optimization Modeling")
    if "robotics" in low or "navigation" in low or "sensor fusion" in low: inferred.append("Autonomy Engineering")
    if "architecture design" in low or "system design" in low: inferred.append("System Architecture")
    evidence_count = sum(1 for m in evidence_map.values() if m.get("evidence_level") in {"APPLIED","DEEP","EXPERT"})
    total = max(len(evidence_map), 1)
    return {"cluster_map": cluster_map, "role_family_scores": role_scores, "top_role_family": top, "inferred_skills": sorted(set(inferred)), "skill_consistency_score": round(evidence_count/total, 2), "weak_skill_count": sum(1 for m in evidence_map.values() if m.get("evidence_level") in {"MENTION","WEAK"})}
