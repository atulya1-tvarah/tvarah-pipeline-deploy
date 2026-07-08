from __future__ import annotations
from typing import Dict, Iterable, List, Set
from .helpers import norm_skill, norm_text

SKILL_ONTOLOGY: Dict[str, Dict[str, List[str]]] = {
    "aws": {"aliases": ["amazon web services", "aws cloud"], "members": ["redshift", "s3", "glue", "athena", "emr", "lambda"]},
    "azure": {"aliases": ["microsoft azure", "azure cloud"], "members": ["synapse", "adf", "azure data factory", "databricks on azure"]},
    "databricks": {"aliases": ["azure databricks"], "members": ["delta lake", "unity catalog", "dbx"]},
    "etl frameworks": {"aliases": ["etl", "elt", "data pipelines"], "members": ["dbt", "apache airflow", "airflow", "ssis", "informatica", "talend", "glue", "azure data factory"]},
    "power bi": {"aliases": ["powerbi"], "members": ["dax", "power query"]},
    "tableau": {"aliases": [], "members": ["tableau prep"]},
    "qlik": {"aliases": ["qlik sense", "qlikview"], "members": []},
    "sql": {"aliases": ["advanced sql"], "members": ["mysql", "postgresql", "sql server", "bigquery sql"]},
    "snowflake": {"aliases": [], "members": []},
    "machine learning": {"aliases": ["ml", "predictive modeling"], "members": ["scikit-learn", "xgboost"]},
}

ROLE_DNA_KEYWORDS = {
    "consulting": ["client", "consulting", "advisory", "multi-client", "engagement"],
    "product": ["product", "growth", "funnel", "experimentation", "retention"],
    "hybrid": ["cross-functional", "stakeholder", "business", "analytics"],
    "domain specialist": ["retail", "supply chain", "marketing", "finance", "manufacturing", "edtech"],
}

ROLE_FAMILY_KEYWORDS = {
    "data_engineer": ["etl", "pipeline", "orchestration", "spark", "warehouse", "airflow", "databricks"],
    "data_scientist": ["machine learning", "model", "statistics", "experiment", "forecasting"],
    "analyst": ["dashboard", "power bi", "tableau", "sql", "analytics", "kpi"],
    "bi_leader": ["bi", "business intelligence", "stakeholder", "dashboard", "kpi", "delivery"],
}


def skill_forms(skill: str) -> Set[str]:
    canon = norm_skill(skill)
    forms = {canon}
    info = SKILL_ONTOLOGY.get(canon, {})
    forms.update(norm_skill(x) for x in info.get("aliases", []))
    forms.update(norm_skill(x) for x in info.get("members", []))
    return {x for x in forms if x}


def canonicalize_skill(skill: str) -> str:
    s = norm_skill(skill)
    for canon, info in SKILL_ONTOLOGY.items():
        if s == canon or s in [norm_skill(x) for x in info.get("aliases", [])] or s in [norm_skill(x) for x in info.get("members", [])]:
            return canon
    return s


def expand_skill_inventory(raw_skills: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for skill in raw_skills:
        canon = canonicalize_skill(skill)
        out.add(canon)
        out.update(skill_forms(canon))
    return out


def find_adjacent_matches(jd_skill: str, candidate_skills: Iterable[str]) -> List[str]:
    canon = canonicalize_skill(jd_skill)
    forms = skill_forms(canon)
    normalized = {norm_skill(x) for x in candidate_skills}
    matches = sorted(x for x in normalized if x in forms and x != canon)
    return matches


def infer_skills_from_text(text: str) -> Set[str]:
    text_n = norm_text(text)
    found = set()
    for canon in SKILL_ONTOLOGY:
        forms = skill_forms(canon)
        if any(form in text_n for form in forms):
            found.add(canon)
    return found
