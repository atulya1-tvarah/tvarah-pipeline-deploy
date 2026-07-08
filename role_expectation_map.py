"""Role × Tier expectation map for credibility analysis.

Maps (role_family, tier) → {must_have_skills, likely_skills, depth_floor,
era_stacks, responsibility_signals}.

Tier meanings (matches company_tier_taxonomy):
  1 = FAANG / global hyper-scale
  2 = Unicorn / well-funded product
  3 = Mid-size funded / strong regional
  4 = IT services / consulting
  5 = Unknown / not in database
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Core data structure
# ---------------------------------------------------------------------------

ROLE_EXPECTATIONS: dict[tuple[str, int], dict[str, Any]] = {

    # ── DATA_SCIENTIST ──────────────────────────────────────────────────────
    ("DATA_SCIENTIST", 1): {
        "must_have_skills": ["Python", "SQL", "Machine Learning", "Statistics", "A/B Testing"],
        "likely_skills": ["Spark", "Airflow", "Kubernetes", "MLflow", "Databricks",
                          "Distributed Computing", "Feature Stores", "Model Monitoring"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2016): ["R", "Scikit-learn", "Pandas", "Hadoop", "Hive", "SAS"],
            (2016, 2019): ["Scikit-learn", "TensorFlow", "Keras", "Spark", "Airflow"],
            (2019, 2022): ["PyTorch", "MLflow", "Kubeflow", "Databricks", "Ray", "XGBoost"],
            (2022, 2026): ["LLMs", "Vector Databases", "LangChain", "Vertex AI", "SageMaker",
                           "MLflow", "Ray", "Triton Inference Server"],
        },
        "responsibility_signals": ["production ML", "A/B testing", "model monitoring",
                                    "cross-functional", "business impact", "experimentation platform"],
    },
    ("DATA_SCIENTIST", 2): {
        "must_have_skills": ["Python", "SQL", "Machine Learning", "Statistics"],
        "likely_skills": ["Airflow", "MLflow", "Spark", "Cloud ML", "A/B Testing",
                          "Feature Engineering", "Model Deployment"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2016): ["R", "Scikit-learn", "Pandas", "MySQL"],
            (2016, 2019): ["Scikit-learn", "TensorFlow", "Airflow", "Spark"],
            (2019, 2022): ["PyTorch", "MLflow", "Databricks", "FastAPI"],
            (2022, 2026): ["LLMs", "Vector Databases", "SageMaker", "MLflow"],
        },
        "responsibility_signals": ["production ML", "model serving", "experimentation",
                                    "stakeholder reporting", "data pipeline"],
    },
    ("DATA_SCIENTIST", 3): {
        "must_have_skills": ["Python", "SQL", "Machine Learning", "Statistics"],
        "likely_skills": ["Scikit-learn", "Pandas", "Tableau", "PowerBI", "Excel", "Jupyter"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2012, 2016): ["R", "Excel", "SPSS", "SAS", "Scikit-learn"],
            (2016, 2019): ["Scikit-learn", "TensorFlow", "Pandas", "MySQL"],
            (2019, 2022): ["PyTorch", "Scikit-learn", "Airflow", "AWS"],
            (2022, 2026): ["Scikit-learn", "HuggingFace", "MLflow", "Azure ML"],
        },
        "responsibility_signals": ["data analysis", "model building", "reporting",
                                    "stakeholder presentations"],
    },
    ("DATA_SCIENTIST", 4): {
        "must_have_skills": ["Python", "SQL", "Excel"],
        "likely_skills": ["Scikit-learn", "Tableau", "PowerBI", "Statistics", "Reporting"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2012, 2016): ["Excel", "SAS", "SPSS", "R"],
            (2016, 2019): ["Python", "Scikit-learn", "Pandas", "Tableau"],
            (2019, 2022): ["Python", "Scikit-learn", "PowerBI", "AWS"],
            (2022, 2026): ["Python", "Scikit-learn", "PowerBI", "Tableau"],
        },
        "responsibility_signals": ["data analysis", "reporting", "dashboards", "client deliverables"],
    },
    ("DATA_SCIENTIST", 5): {
        "must_have_skills": ["Python", "SQL"],
        "likely_skills": ["Excel", "Statistics", "Scikit-learn", "Tableau"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2012, 2026): ["Python", "SQL", "Excel", "Statistics"],
        },
        "responsibility_signals": ["data analysis", "reporting"],
    },

    # ── DATA_ENGINEER ───────────────────────────────────────────────────────
    ("DATA_ENGINEER", 1): {
        "must_have_skills": ["Python", "SQL", "Spark", "Airflow", "Kafka", "Data Modeling"],
        "likely_skills": ["Kubernetes", "Terraform", "Delta Lake", "dbt", "Flink",
                          "Snowflake", "Databricks", "Stream Processing"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2016): ["Hadoop", "Hive", "Pig", "MapReduce", "Oozie", "HBase"],
            (2016, 2019): ["Spark", "Kafka", "Airflow", "Parquet", "S3", "Redshift"],
            (2019, 2022): ["Databricks", "Delta Lake", "dbt", "Flink", "Kubernetes", "Iceberg"],
            (2022, 2026): ["Databricks", "dbt", "Apache Iceberg", "Flink", "DuckDB",
                           "DataHub", "OpenLineage"],
        },
        "responsibility_signals": ["petabyte scale", "real-time pipelines", "data platform",
                                    "SLA ownership", "data governance", "lineage"],
    },
    ("DATA_ENGINEER", 2): {
        "must_have_skills": ["Python", "SQL", "Airflow", "Spark"],
        "likely_skills": ["Kafka", "dbt", "Snowflake", "Redshift", "Cloud Storage", "Data Modeling"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2016): ["Hadoop", "Hive", "Sqoop", "Oozie"],
            (2016, 2019): ["Spark", "Airflow", "S3", "Redshift", "Kafka"],
            (2019, 2022): ["Databricks", "dbt", "Snowflake", "Flink"],
            (2022, 2026): ["dbt", "Databricks", "Iceberg", "Fivetran"],
        },
        "responsibility_signals": ["data pipelines", "ELT/ETL", "data warehouse",
                                    "data quality", "CI/CD for data"],
    },
    ("DATA_ENGINEER", 3): {
        "must_have_skills": ["Python", "SQL", "ETL"],
        "likely_skills": ["Airflow", "Spark", "Redshift", "Snowflake", "AWS Glue"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2012, 2016): ["Hadoop", "Hive", "Informatica", "SSIS"],
            (2016, 2019): ["Python", "Airflow", "S3", "Redshift"],
            (2019, 2022): ["Airflow", "Spark", "Snowflake", "dbt"],
            (2022, 2026): ["dbt", "Snowflake", "Airflow", "BigQuery"],
        },
        "responsibility_signals": ["ETL pipelines", "data ingestion", "warehouse management"],
    },
    ("DATA_ENGINEER", 4): {
        "must_have_skills": ["SQL", "Python", "ETL"],
        "likely_skills": ["SSIS", "Informatica", "Talend", "Oracle", "SSIS"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2012, 2016): ["Informatica", "SSIS", "Oracle", "SQL Server"],
            (2016, 2019): ["Python", "SQL", "Talend", "SSIS"],
            (2019, 2022): ["Airflow", "SQL", "AWS Glue", "Python"],
            (2022, 2026): ["dbt", "SQL", "Python", "Airflow"],
        },
        "responsibility_signals": ["ETL development", "data migration", "reporting pipelines"],
    },
    ("DATA_ENGINEER", 5): {
        "must_have_skills": ["SQL", "Python"],
        "likely_skills": ["ETL tools", "Excel", "Reporting"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2012, 2026): ["SQL", "Python", "ETL"],
        },
        "responsibility_signals": ["data extraction", "reporting"],
    },

    # ── ML_ENGINEER ─────────────────────────────────────────────────────────
    ("ML_ENGINEER", 1): {
        "must_have_skills": ["Python", "PyTorch", "TensorFlow", "Kubernetes", "Docker",
                             "Model Serving", "MLOps"],
        "likely_skills": ["Triton", "Ray Serve", "ONNX", "TensorRT", "Kubeflow",
                          "Feature Stores", "Experiment Tracking"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2014, 2018): ["TensorFlow 1.x", "Scikit-learn", "Docker", "Flask"],
            (2018, 2021): ["PyTorch", "Kubernetes", "MLflow", "Kubeflow", "Triton"],
            (2021, 2026): ["Triton", "Ray", "BentoML", "ONNX", "LLM serving", "vLLM"],
        },
        "responsibility_signals": ["model serving", "inference optimization", "latency SLA",
                                    "training infrastructure", "MLOps platform"],
    },
    ("ML_ENGINEER", 2): {
        "must_have_skills": ["Python", "PyTorch", "Docker", "Model Deployment", "MLflow"],
        "likely_skills": ["Kubernetes", "FastAPI", "Triton", "Airflow", "Feature Engineering"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2014, 2018): ["TensorFlow", "Scikit-learn", "Docker", "Flask"],
            (2018, 2021): ["PyTorch", "Docker", "Kubernetes", "MLflow"],
            (2021, 2026): ["PyTorch", "MLflow", "Kubernetes", "BentoML", "Ray"],
        },
        "responsibility_signals": ["model deployment", "API serving", "experiment tracking",
                                    "training pipelines"],
    },
    ("ML_ENGINEER", 3): {
        "must_have_skills": ["Python", "Machine Learning", "Docker", "REST APIs"],
        "likely_skills": ["FastAPI", "Scikit-learn", "MLflow", "AWS SageMaker"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2014, 2018): ["Flask", "Scikit-learn", "AWS EC2"],
            (2018, 2021): ["Docker", "FastAPI", "Scikit-learn", "AWS"],
            (2021, 2026): ["Docker", "FastAPI", "MLflow", "AWS SageMaker"],
        },
        "responsibility_signals": ["model deployment", "API development", "model monitoring"],
    },
    ("ML_ENGINEER", 4): {
        "must_have_skills": ["Python", "Machine Learning", "SQL"],
        "likely_skills": ["Scikit-learn", "Flask", "REST APIs", "AWS"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2014, 2026): ["Python", "Scikit-learn", "Flask", "SQL"],
        },
        "responsibility_signals": ["model building", "data analysis", "reporting"],
    },
    ("ML_ENGINEER", 5): {
        "must_have_skills": ["Python", "Machine Learning"],
        "likely_skills": ["Scikit-learn", "Python", "SQL"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2014, 2026): ["Python", "Scikit-learn"]},
        "responsibility_signals": ["model building"],
    },

    # ── DATA_ANALYST ────────────────────────────────────────────────────────
    ("DATA_ANALYST", 1): {
        "must_have_skills": ["SQL", "Python", "Statistics", "A/B Testing", "Visualization"],
        "likely_skills": ["Tableau", "PowerBI", "Looker", "Spark", "Experimentation",
                          "Business Intelligence", "Dashboard Design"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2016): ["SQL", "Excel", "R", "Tableau", "SAS"],
            (2016, 2019): ["SQL", "Python", "Tableau", "Looker", "Redshift"],
            (2019, 2022): ["SQL", "Python", "Looker", "dbt", "Databricks"],
            (2022, 2026): ["SQL", "Python", "Tableau", "dbt", "Snowflake"],
        },
        "responsibility_signals": ["business metrics", "A/B testing", "self-serve analytics",
                                    "cross-functional insights", "experimentation"],
    },
    ("DATA_ANALYST", 2): {
        "must_have_skills": ["SQL", "Python", "Visualization", "Statistics"],
        "likely_skills": ["Tableau", "PowerBI", "Looker", "Excel", "A/B Testing"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2012, 2016): ["SQL", "Excel", "Tableau", "R"],
            (2016, 2019): ["SQL", "Python", "Tableau", "Redshift"],
            (2019, 2022): ["SQL", "Python", "Looker", "Snowflake"],
            (2022, 2026): ["SQL", "Python", "dbt", "Tableau", "Snowflake"],
        },
        "responsibility_signals": ["data analysis", "reporting", "dashboards", "stakeholder insights"],
    },
    ("DATA_ANALYST", 3): {
        "must_have_skills": ["SQL", "Excel", "Visualization"],
        "likely_skills": ["Python", "Tableau", "PowerBI", "Google Analytics"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2012, 2016): ["SQL", "Excel", "SSRS"],
            (2016, 2019): ["SQL", "Tableau", "Excel", "Python"],
            (2019, 2026): ["SQL", "Tableau", "PowerBI", "Python"],
        },
        "responsibility_signals": ["reporting", "dashboards", "business analysis"],
    },
    ("DATA_ANALYST", 4): {
        "must_have_skills": ["SQL", "Excel"],
        "likely_skills": ["PowerBI", "Tableau", "Python", "Reporting"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2012, 2026): ["SQL", "Excel", "PowerBI", "Tableau"],
        },
        "responsibility_signals": ["reporting", "data extraction", "client deliverables"],
    },
    ("DATA_ANALYST", 5): {
        "must_have_skills": ["Excel", "SQL"],
        "likely_skills": ["PowerBI", "Reporting"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2012, 2026): ["Excel", "SQL"]},
        "responsibility_signals": ["reporting", "data extraction"],
    },

    # ── BACKEND_ENGINEER ────────────────────────────────────────────────────
    ("BACKEND_ENGINEER", 1): {
        "must_have_skills": ["Python", "Java", "Go", "Microservices", "Kubernetes",
                             "Distributed Systems", "System Design"],
        "likely_skills": ["Kafka", "gRPC", "Redis", "PostgreSQL", "Terraform",
                          "Service Mesh", "CI/CD", "SRE practices"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2016): ["Java", "Spring", "MySQL", "Redis", "Memcached"],
            (2016, 2019): ["Python", "Go", "Kubernetes", "Docker", "Kafka", "Cassandra"],
            (2019, 2022): ["Go", "Python", "Kubernetes", "Kafka", "gRPC", "Terraform"],
            (2022, 2026): ["Go", "Python", "Kubernetes", "gRPC", "Kafka", "OpenTelemetry"],
        },
        "responsibility_signals": ["distributed systems", "high availability", "system design",
                                    "petabyte scale", "on-call SLA", "platform ownership"],
    },
    ("BACKEND_ENGINEER", 2): {
        "must_have_skills": ["Python", "Java", "Node.js", "REST APIs", "Databases",
                             "Docker", "Microservices"],
        "likely_skills": ["Kubernetes", "Kafka", "Redis", "PostgreSQL", "AWS", "CI/CD"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2012, 2016): ["Java", "Python", "MySQL", "AWS EC2"],
            (2016, 2019): ["Python", "Node.js", "Docker", "PostgreSQL", "Redis"],
            (2019, 2022): ["Python", "Go", "Docker", "Kubernetes", "AWS"],
            (2022, 2026): ["Python", "Go", "Kubernetes", "AWS", "Terraform"],
        },
        "responsibility_signals": ["API development", "microservices", "database design",
                                    "performance optimization"],
    },
    ("BACKEND_ENGINEER", 3): {
        "must_have_skills": ["Python", "Java", "REST APIs", "SQL"],
        "likely_skills": ["Node.js", "Django", "Spring Boot", "MySQL", "AWS"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2012, 2016): ["Java", "Spring", "MySQL", "REST APIs"],
            (2016, 2019): ["Python", "Django", "Flask", "MySQL"],
            (2019, 2022): ["Python", "FastAPI", "Docker", "PostgreSQL"],
            (2022, 2026): ["Python", "FastAPI", "Docker", "AWS"],
        },
        "responsibility_signals": ["API development", "backend services", "database management"],
    },
    ("BACKEND_ENGINEER", 4): {
        "must_have_skills": ["Java", "Python", "SQL", "REST APIs"],
        "likely_skills": ["Spring", "Django", "MySQL", "Git"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {
            (2012, 2026): ["Java", "Python", "SQL", "REST APIs"],
        },
        "responsibility_signals": ["API development", "maintenance", "client deliverables"],
    },
    ("BACKEND_ENGINEER", 5): {
        "must_have_skills": ["Python", "Java", "SQL"],
        "likely_skills": ["REST APIs", "Git"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2012, 2026): ["Python", "Java", "SQL"]},
        "responsibility_signals": ["development", "maintenance"],
    },

    # ── PLATFORM_ENGINEER ───────────────────────────────────────────────────
    ("PLATFORM_ENGINEER", 1): {
        "must_have_skills": ["Kubernetes", "Terraform", "Docker", "CI/CD", "Cloud",
                             "Infrastructure as Code", "Observability"],
        "likely_skills": ["Helm", "ArgoCD", "Prometheus", "Grafana", "Istio",
                          "Vault", "Crossplane", "OpenTelemetry"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2014, 2018): ["Puppet", "Chef", "Ansible", "Jenkins", "AWS EC2"],
            (2018, 2021): ["Kubernetes", "Terraform", "Docker", "ArgoCD", "Prometheus"],
            (2021, 2026): ["Kubernetes", "Terraform", "ArgoCD", "Istio", "OpenTelemetry",
                           "Crossplane", "Platform Engineering"],
        },
        "responsibility_signals": ["platform reliability", "developer experience", "SRE",
                                    "multi-cloud", "security posture", "FinOps"],
    },
    ("PLATFORM_ENGINEER", 2): {
        "must_have_skills": ["Kubernetes", "Docker", "Terraform", "CI/CD", "AWS/GCP/Azure"],
        "likely_skills": ["Helm", "ArgoCD", "Prometheus", "Grafana", "Jenkins"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2014, 2018): ["Ansible", "Jenkins", "Docker", "AWS EC2"],
            (2018, 2021): ["Kubernetes", "Terraform", "Docker", "Jenkins"],
            (2021, 2026): ["Kubernetes", "Terraform", "ArgoCD", "Prometheus"],
        },
        "responsibility_signals": ["infrastructure automation", "cloud migration",
                                    "CI/CD pipelines", "cost optimization"],
    },
    ("PLATFORM_ENGINEER", 3): {
        "must_have_skills": ["Docker", "AWS", "CI/CD", "Linux"],
        "likely_skills": ["Kubernetes", "Terraform", "Jenkins", "Ansible"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {
            (2014, 2018): ["Ansible", "Jenkins", "AWS EC2", "Linux"],
            (2018, 2021): ["Docker", "Jenkins", "AWS", "Terraform"],
            (2021, 2026): ["Docker", "Kubernetes", "Terraform", "Jenkins"],
        },
        "responsibility_signals": ["infrastructure management", "CI/CD", "cloud"],
    },
    ("PLATFORM_ENGINEER", 4): {
        "must_have_skills": ["Linux", "AWS", "Shell Scripting"],
        "likely_skills": ["Docker", "Jenkins", "Ansible", "Git"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2014, 2026): ["Linux", "Shell Scripting", "AWS", "Jenkins"]},
        "responsibility_signals": ["infrastructure support", "deployment"],
    },
    ("PLATFORM_ENGINEER", 5): {
        "must_have_skills": ["Linux", "AWS", "Shell Scripting"],
        "likely_skills": ["Docker", "Git"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2014, 2026): ["Linux", "Shell Scripting", "AWS"]},
        "responsibility_signals": ["system administration"],
    },

    # ── AI_ARCHITECT ────────────────────────────────────────────────────────
    ("AI_ARCHITECT", 1): {
        "must_have_skills": ["Python", "LLMs", "Machine Learning", "System Design",
                             "Cloud Architecture", "MLOps"],
        "likely_skills": ["LangChain", "Vector Databases", "RAG", "Fine-tuning",
                          "Kubernetes", "Distributed Training", "Responsible AI"],
        "depth_floor": "ARCHITECT_LEVEL",
        "era_stacks": {
            (2014, 2019): ["TensorFlow", "Kubernetes", "Spark", "Deep Learning"],
            (2019, 2022): ["PyTorch", "BERT", "Transformer models", "Kubernetes", "MLflow"],
            (2022, 2026): ["LLMs", "LangChain", "Vector Databases", "RAG", "Fine-tuning",
                           "Responsible AI", "AI Governance"],
        },
        "responsibility_signals": ["AI strategy", "architecture decisions", "cross-org influence",
                                    "responsible AI", "production LLM systems", "ML platform design"],
    },
    ("AI_ARCHITECT", 2): {
        "must_have_skills": ["Python", "LLMs", "Machine Learning", "System Design"],
        "likely_skills": ["LangChain", "Vector Databases", "RAG", "MLOps", "Cloud ML"],
        "depth_floor": "ARCHITECT_LEVEL",
        "era_stacks": {
            (2014, 2019): ["TensorFlow", "Spark", "Kubernetes"],
            (2019, 2022): ["PyTorch", "Transformers", "MLflow", "Kubernetes"],
            (2022, 2026): ["LLMs", "LangChain", "Vector Databases", "MLflow"],
        },
        "responsibility_signals": ["AI architecture", "LLM integration", "system design",
                                    "model selection", "cross-team leadership"],
    },
    ("AI_ARCHITECT", 3): {
        "must_have_skills": ["Python", "Machine Learning", "Deep Learning", "System Design"],
        "likely_skills": ["PyTorch", "TensorFlow", "Docker", "REST APIs", "Cloud ML"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2014, 2026): ["Python", "TensorFlow", "PyTorch", "Docker", "Cloud ML"],
        },
        "responsibility_signals": ["AI system design", "model architecture", "team guidance"],
    },
    ("AI_ARCHITECT", 4): {
        "must_have_skills": ["Python", "Machine Learning"],
        "likely_skills": ["Deep Learning", "TensorFlow", "Cloud", "REST APIs"],
        "depth_floor": "ADVANCED",
        "era_stacks": {(2014, 2026): ["Python", "Machine Learning", "Deep Learning"]},
        "responsibility_signals": ["model development", "consulting"],
    },
    ("AI_ARCHITECT", 5): {
        "must_have_skills": ["Python", "Machine Learning"],
        "likely_skills": ["Deep Learning", "TensorFlow"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {(2014, 2026): ["Python", "Machine Learning"]},
        "responsibility_signals": ["model development"],
    },

    # ── MANAGER ─────────────────────────────────────────────────────────────
    ("MANAGER", 1): {
        "must_have_skills": ["Leadership", "Strategy", "Stakeholder Management",
                             "Hiring", "Data-driven Decision Making"],
        "likely_skills": ["P&L ownership", "OKRs", "Product Roadmap", "Cross-functional",
                          "Executive Communication", "Mentorship"],
        "depth_floor": "ADVANCED",
        "era_stacks": {
            (2012, 2026): ["Leadership", "Strategy", "Stakeholder Management", "Hiring"],
        },
        "responsibility_signals": ["team management", "hiring", "P&L", "executive reporting",
                                    "strategy", "org building", "cross-functional leadership"],
    },
    ("MANAGER", 2): {
        "must_have_skills": ["Leadership", "Stakeholder Management", "Team Building"],
        "likely_skills": ["OKRs", "Product Roadmap", "Data-driven", "Mentorship", "Hiring"],
        "depth_floor": "ADVANCED",
        "era_stacks": {(2012, 2026): ["Leadership", "Stakeholder Management", "Team Building"]},
        "responsibility_signals": ["team management", "stakeholder reporting", "roadmap ownership"],
    },
    ("MANAGER", 3): {
        "must_have_skills": ["Project Management", "Leadership", "Communication"],
        "likely_skills": ["Agile", "JIRA", "Scrum", "Stakeholder Management"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {(2012, 2026): ["Project Management", "Agile", "JIRA"]},
        "responsibility_signals": ["team management", "delivery", "stakeholder communication"],
    },
    ("MANAGER", 4): {
        "must_have_skills": ["Project Management", "Communication"],
        "likely_skills": ["Agile", "JIRA", "Microsoft Project"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2012, 2026): ["Project Management", "Agile"]},
        "responsibility_signals": ["delivery management", "client communication"],
    },
    ("MANAGER", 5): {
        "must_have_skills": ["Project Management"],
        "likely_skills": ["Communication", "Coordination"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2012, 2026): ["Project Management"]},
        "responsibility_signals": ["delivery", "coordination"],
    },
}

# ---------------------------------------------------------------------------
# Fallback / generic expectations for role_families not fully enumerated
# ---------------------------------------------------------------------------

_GENERIC_FALLBACK: dict[int, dict[str, Any]] = {
    1: {
        "must_have_skills": ["System Design", "Coding", "Cloud"],
        "likely_skills": ["Kubernetes", "CI/CD", "Distributed Systems"],
        "depth_floor": "ADVANCED",
        "era_stacks": {(2012, 2026): ["Cloud", "Coding", "System Design"]},
        "responsibility_signals": ["ownership", "production systems", "cross-functional"],
    },
    2: {
        "must_have_skills": ["Coding", "Cloud", "System Design"],
        "likely_skills": ["Docker", "CI/CD", "Databases"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {(2012, 2026): ["Cloud", "Coding"]},
        "responsibility_signals": ["product delivery", "system design"],
    },
    3: {
        "must_have_skills": ["Coding", "SQL"],
        "likely_skills": ["Cloud", "CI/CD"],
        "depth_floor": "HANDS_ON",
        "era_stacks": {(2012, 2026): ["Coding", "SQL"]},
        "responsibility_signals": ["delivery", "feature development"],
    },
    4: {
        "must_have_skills": ["Coding"],
        "likely_skills": ["SQL", "Git"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2012, 2026): ["Coding", "SQL"]},
        "responsibility_signals": ["development", "maintenance"],
    },
    5: {
        "must_have_skills": ["Coding"],
        "likely_skills": ["SQL"],
        "depth_floor": "FOUNDATIONAL",
        "era_stacks": {(2012, 2026): ["Coding"]},
        "responsibility_signals": ["development"],
    },
}

# Normalize role names that might appear differently
_ROLE_ALIASES: dict[str, str] = {
    "DATA SCIENTIST": "DATA_SCIENTIST",
    "DATASCIENTIST": "DATA_SCIENTIST",
    "GENAI_DATA_SCIENTIST": "DATA_SCIENTIST",
    "GENAI DATA SCIENTIST": "DATA_SCIENTIST",
    "GEN AI DATA SCIENTIST": "DATA_SCIENTIST",
    "NLP_DATA_SCIENTIST": "DATA_SCIENTIST",
    "NLP DATA SCIENTIST": "DATA_SCIENTIST",
    "COMPUTER_VISION_ENGINEER": "ML_ENGINEER",
    "COMPUTER VISION ENGINEER": "ML_ENGINEER",
    "RESEARCH SCIENTIST": "DATA_SCIENTIST",
    "RESEARCH_SCIENTIST": "DATA_SCIENTIST",
    "APPLIED SCIENTIST": "DATA_SCIENTIST",
    "APPLIED_SCIENTIST": "DATA_SCIENTIST",
    "DATA ENGINEER": "DATA_ENGINEER",
    "DATAENGINEER": "DATA_ENGINEER",
    "ML ENGINEER": "ML_ENGINEER",
    "MACHINE LEARNING ENGINEER": "ML_ENGINEER",
    "MLENGINEER": "ML_ENGINEER",
    "DATA ANALYST": "DATA_ANALYST",
    "DATAANALYST": "DATA_ANALYST",
    "BACKEND ENGINEER": "BACKEND_ENGINEER",
    "BACKEND DEVELOPER": "BACKEND_ENGINEER",
    "SOFTWARE ENGINEER": "BACKEND_ENGINEER",
    "PLATFORM ENGINEER": "PLATFORM_ENGINEER",
    "DEVOPS ENGINEER": "PLATFORM_ENGINEER",
    "SRE": "PLATFORM_ENGINEER",
    "SITE RELIABILITY ENGINEER": "PLATFORM_ENGINEER",
    "AI ARCHITECT": "AI_ARCHITECT",
    "ML ARCHITECT": "AI_ARCHITECT",
    "ENGINEERING MANAGER": "MANAGER",
    "DATA SCIENCE MANAGER": "MANAGER",
    "TECH LEAD": "MANAGER",
    "TECHNICAL LEAD": "MANAGER",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _normalize_role(role_family: str) -> str:
    key = str(role_family or "").strip().upper().replace("-", "_")
    return _ROLE_ALIASES.get(key, key)


def _clamp_tier(tier: int) -> int:
    return max(1, min(5, int(tier)))


def get_role_expectations(role_family: str, tier: int) -> dict[str, Any]:
    """Return expectations dict for (role_family, tier).

    Falls back to generic expectations if role is not in the map.
    """
    role = _normalize_role(role_family)
    t = _clamp_tier(tier)
    result = ROLE_EXPECTATIONS.get((role, t))
    if result:
        return result
    # Try adjacent tier
    for fallback_t in [t + 1, t - 1, 3]:
        result = ROLE_EXPECTATIONS.get((role, fallback_t))
        if result:
            return result
    # Generic fallback
    return _GENERIC_FALLBACK.get(t, _GENERIC_FALLBACK[3])


def get_era_stack(role_family: str, tier: int, year: int) -> list[str]:
    """Return expected skills for a given calendar year at role/tier.

    Uses the era_stacks ranges in the expectation dict.
    """
    exp = get_role_expectations(role_family, tier)
    era_stacks = exp.get("era_stacks") or {}
    for (start_yr, end_yr), skills in era_stacks.items():
        if start_yr <= year < end_yr:
            return list(skills)
    # Fallback: latest era
    if era_stacks:
        last_key = max(era_stacks.keys(), key=lambda k: k[0])
        return list(era_stacks[last_key])
    return []


def get_credibility_template(
    role_family: str,
    tier: int,
    years: float,
    start_year: int,
) -> dict[str, Any]:
    """Return a complete credibility template for a role/tier/tenure/era.

    Combines must_have, likely, era_stack, depth_floor, and responsibility signals.
    """
    exp = get_role_expectations(role_family, tier)
    era_skills = get_era_stack(role_family, tier, start_year)
    return {
        "role_family": _normalize_role(role_family),
        "tier": tier,
        "years": years,
        "start_year": start_year,
        "must_have_skills": exp.get("must_have_skills", []),
        "likely_skills": exp.get("likely_skills", []),
        "era_skills": era_skills,
        "depth_floor": exp.get("depth_floor", "FOUNDATIONAL"),
        "responsibility_signals": exp.get("responsibility_signals", []),
    }
