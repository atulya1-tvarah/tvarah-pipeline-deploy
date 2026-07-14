"""
pdf_to_json_extractor.py — Heuristic PDF/DOCX -> structured JSON extractor.

Produces JSON compatible with normalize_resume_data() / training_data_builder.
Uses pdfminer.six for PDF text extraction; python-docx for DOCX.

Usage:
    python pdf_to_json_extractor.py --input drive_resumes/ --output extracted_pdfs/
    python pdf_to_json_extractor.py --input drive_resumes/single.pdf --output extracted_pdfs/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text_pdf(path: Path) -> str:
    from pdfminer.high_level import extract_text
    try:
        return extract_text(str(path)) or ""
    except Exception as exc:
        print(f"  [WARN] pdfminer failed on {path.name}: {exc}")
        return ""


def _extract_text_docx(path: Path) -> str:
    try:
        import docx
        doc = docx.Document(str(path))
        return "\n".join(para.text for para in doc.paragraphs)
    except Exception as exc:
        print(f"  [WARN] python-docx failed on {path.name}: {exc}")
        return ""


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_pdf(path)
    if suffix in (".docx", ".doc"):
        return _extract_text_docx(path)
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

SECTION_HEADERS = {
    "summary": re.compile(
        r"^\s*(professional\s+summary|summary|profile|about\s+me|objective|career\s+objective"
        r"|professional\s+profile|executive\s+summary)\s*:?\s*$",
        re.IGNORECASE,
    ),
    "experience": re.compile(
        r"^\s*(work\s+experience|experience|professional\s+experience|employment\s+history"
        r"|career\s+history|work\s+history|relevant\s+experience|internship|internships"
        r"|roles?\s+&?\s*responsibilities?)\s*:?\s*$",
        re.IGNORECASE,
    ),
    "education": re.compile(
        r"^\s*(education|academic\s+background|educational\s+qualifications?|academic\s+details"
        r"|qualifications?|academics)\s*:?\s*$",
        re.IGNORECASE,
    ),
    "skills": re.compile(
        r"^\s*(skills?|technical\s+skills?|core\s+skills?|key\s+skills?|competenc(y|ies)"
        r"|expertise|areas?\s+of\s+expertise|proficienc(y|ies)|tools?\s+&?\s+technologies"
        r"|programming|languages?\s+&?\s+tools)\s*:?\s*$",
        re.IGNORECASE,
    ),
    "certifications": re.compile(
        r"^\s*(certif(ication|icate)s?|professional\s+certifications?|courses?"
        r"|training|licenses?\s+&?\s+certifications?)\s*:?\s*$",
        re.IGNORECASE,
    ),
    "projects": re.compile(
        r"^\s*(projects?|key\s+projects?|notable\s+projects?|academic\s+projects?"
        r"|personal\s+projects?)\s*:?\s*$",
        re.IGNORECASE,
    ),
    "achievements": re.compile(
        r"^\s*(achievements?|accomplishments?|awards?|honors?|recognitions?)\s*:?\s*$",
        re.IGNORECASE,
    ),
}


def _split_sections(text: str) -> dict[str, list[str]]:
    """Split resume text into named sections. Returns dict of section -> lines."""
    lines = text.split("\n")
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in lines:
        stripped = line.strip()
        matched = False
        for section_name, pattern in SECTION_HEADERS.items():
            if pattern.match(stripped):
                current = section_name
                sections.setdefault(current, [])
                matched = True
                break
        if not matched:
            sections.setdefault(current, []).append(line)
    return sections


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(?:\+91[\s-]?)?(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}|\+?[\d\s\-().]{10,16})")
_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)?"
    r"[\s,]*(?:19|20)\d{2}\b|\bPresent\b|\bCurrent\b|\bTill\s+Date\b",
    re.IGNORECASE,
)


_SECTION_WORDS = {
    "contact", "address", "phone", "email", "mobile", "skills", "education",
    "experience", "objective", "summary", "profile", "about", "linkedin",
    "github", "portfolio", "references", "projects", "achievements",
    "certifications", "awards", "publications", "languages", "hobbies",
    "interests", "personal", "declaration", "curriculum", "vitae", "resume",
}


def _extract_name(header_lines: list[str], filename: str) -> str:
    """Heuristic: name is typically the first non-empty line with 2-4 words."""
    for line in header_lines:
        stripped = line.strip()
        # Remove bullet chars and special formatting
        stripped = re.sub(r"[\u2022\u25cf\u2019\u2018\|•●]", "", stripped).strip()
        if not stripped:
            continue
        if _EMAIL_RE.search(stripped) or _PHONE_RE.search(stripped):
            continue
        if re.search(r"http|www\.|linkedin|github|@", stripped, re.IGNORECASE):
            continue
        if re.search(r"\d{4}|\d{10}", stripped):
            continue
        words = stripped.split()
        if len(words) < 1 or len(words) > 5:
            continue
        # Skip if any word is a known section label
        if any(w.lower().rstrip(":") in _SECTION_WORDS for w in words):
            continue
        # Skip all-caps single word that looks like a section header
        if len(words) == 1 and stripped.isupper():
            continue
        # A name should have mostly alphabetic words
        alpha_ratio = sum(1 for w in words if re.match(r"^[A-Za-z.'-]+$", w)) / len(words)
        if alpha_ratio < 0.7:
            continue
        return stripped
    # Fall back to filename stem
    stem = Path(filename).stem
    stem = re.sub(r"\[\d+y_\d+m\].*", "", stem).strip()
    stem = re.sub(r"_+", " ", stem).strip()
    return stem


def _extract_contact(all_text: str) -> tuple[str, str]:
    """Return (email, phone)."""
    email_match = _EMAIL_RE.search(all_text)
    email = email_match.group(0) if email_match else ""
    phone_match = _PHONE_RE.search(all_text)
    phone = phone_match.group(0).strip() if phone_match else ""
    return email, phone


# -- Experience parsing -------------------------------------------------------

_DATE_RANGE_RE = re.compile(
    r"(?P<start>"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)?"
    r"[\s,]*(?:19|20)\d{2})"
    r"\s*[-–—to]+\s*"
    r"(?P<end>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)?"
    r"[\s,]*(?:(?:19|20)\d{2}|Present|Current|Till\s*Date))",
    re.IGNORECASE,
)


def _parse_experience(lines: list[str]) -> list[dict[str, Any]]:
    """Heuristic experience parser — handles most common resume formats."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    desc_lines: list[str] = []

    def _flush():
        nonlocal current, desc_lines
        if current:
            current["role_description"] = " ".join(l.strip() for l in desc_lines if l.strip())
            entries.append(current)
        current = None
        desc_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        date_match = _DATE_RANGE_RE.search(stripped)
        # Heuristic: a line with a date range that's relatively short is likely a job header
        if date_match and len(stripped) < 200:
            # Try to extract company and title from surrounding context
            before_date = stripped[: date_match.start()].strip().strip("|-,")
            # Split on common separators: | , at @
            parts = re.split(r"\s*[|,@]\s*|\s+at\s+", before_date, maxsplit=1)
            title = parts[0].strip() if parts else before_date
            company = parts[1].strip() if len(parts) > 1 else ""
            is_current = bool(re.search(r"Present|Current|Till", date_match.group("end"), re.IGNORECASE))
            _flush()
            current = {
                "job_title": title,
                "company_name": company,
                "start_date": date_match.group("start").strip(),
                "end_date": date_match.group("end").strip(),
                "is_current_role": is_current,
            }
        elif current is not None:
            # Could be company name on next line or description
            if not current.get("company_name") and not desc_lines:
                # If the line looks like a company name (short, no bullets)
                if len(stripped) < 80 and not stripped.startswith(("•", "-", "*", "–", "·")):
                    current["company_name"] = stripped
                    continue
            desc_lines.append(stripped)
        # else: pre-experience lines, skip

    _flush()
    return entries


# -- Skills parsing -----------------------------------------------------------

COMMON_SKILLS = [
    # Languages
    "Python", "R", "SQL", "Java", "Scala", "C++", "C#", "JavaScript", "TypeScript",
    "Go", "Rust", "Kotlin", "Swift", "MATLAB", "Julia", "Bash", "Shell",
    # ML/AI
    "Machine Learning", "Deep Learning", "NLP", "Computer Vision", "Reinforcement Learning",
    "Transfer Learning", "Generative AI", "LLM", "RAG", "Fine-tuning", "Feature Engineering",
    "Model Deployment", "MLOps", "Data Science", "Statistical Modeling", "Time Series",
    # Frameworks
    "TensorFlow", "PyTorch", "Keras", "Scikit-learn", "XGBoost", "LightGBM",
    "Hugging Face", "Transformers", "spaCy", "NLTK", "FastAPI", "Flask", "Django",
    "Spark", "PySpark", "Hadoop", "Airflow", "dbt", "Kafka", "Flink",
    # Cloud/Infra
    "AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "Terraform",
    "CI/CD", "Jenkins", "GitHub Actions", "MLflow", "Kubeflow", "Vertex AI",
    "SageMaker", "Azure ML", "Databricks", "Snowflake", "Redshift",
    # Databases
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Cassandra",
    "BigQuery", "DynamoDB", "Oracle", "SQL Server", "Hive", "Presto",
    # Data Engineering
    "ETL", "Data Pipeline", "Data Warehouse", "Data Lake", "Data Lakehouse",
    "Apache Spark", "Apache Kafka", "Apache Airflow", "Delta Lake", "Parquet",
    # BI/Viz
    "Tableau", "Power BI", "Looker", "Grafana", "Matplotlib", "Seaborn", "Plotly",
    # Other tech
    "REST API", "GraphQL", "Microservices", "System Design", "Architecture",
    "OpenAI", "LangChain", "Vector Database", "Pinecone", "Weaviate", "FAISS",
    # Methodologies
    "Agile", "Scrum", "DevOps", "DataOps", "A/B Testing",
]


def _parse_skills(lines: list[str], full_text: str = "") -> list[str]:
    """Extract skill names from skills section lines + full text scan."""
    found: set[str] = set()
    section_text = " ".join(lines)

    # 1. Scan for common skills in section text
    for skill in COMMON_SKILLS:
        pattern = r"\b" + re.escape(skill) + r"\b"
        if re.search(pattern, section_text, re.IGNORECASE):
            found.add(skill)

    # 2. Parse comma/bullet separated tokens in skill lines
    for line in lines:
        stripped = line.strip().lstrip("•-*·–")
        if not stripped:
            continue
        # Split on common delimiters
        tokens = re.split(r"[,|;/•\n]+", stripped)
        for token in tokens:
            t = token.strip().strip("()")
            # Filter: 1-50 chars, not a sentence
            if 1 < len(t) < 50 and " " not in t or (len(t.split()) <= 4):
                if not re.search(r"[0-9]{4}", t) and not _EMAIL_RE.search(t):
                    found.add(t)

    # 3. Scan full text for common skills (catches skills mentioned in experience)
    if full_text:
        for skill in COMMON_SKILLS:
            pattern = r"\b" + re.escape(skill) + r"\b"
            if re.search(pattern, full_text, re.IGNORECASE):
                found.add(skill)

    return sorted(found)


# -- Education parsing --------------------------------------------------------

_DEGREE_RE = re.compile(
    r"\b(B\.?Tech|M\.?Tech|B\.?E|M\.?E|B\.?Sc|M\.?Sc|B\.?A|M\.?A|MBA|M\.?S|Ph\.?D|"
    r"Bachelor|Master|Doctor|B\.?Com|B\.?B\.?A|M\.?C\.?A|B\.?C\.?A|Diploma|Certificate)\b",
    re.IGNORECASE,
)


def _parse_education(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        degree_match = _DEGREE_RE.search(stripped)
        year_match = re.search(r"(19|20)\d{2}", stripped)
        if degree_match or (year_match and len(stripped) < 200):
            if current:
                entries.append(current)
            current = {
                "degree": degree_match.group(0) if degree_match else "",
                "institution_name": "",
                "field_of_study": "",
                "start_date": "",
                "end_date": year_match.group(0) if year_match else "",
            }
            # Try to get field_of_study from same line
            rest = stripped[degree_match.end():].strip(" -–|,") if degree_match else stripped
            if rest and len(rest) < 100:
                current["field_of_study"] = rest.split("\n")[0][:80]
        elif current and not current.get("institution_name") and stripped:
            current["institution_name"] = stripped[:100]

    if current:
        entries.append(current)
    return entries


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def _infer_skills_info(skills_flat: list[str]) -> dict[str, list[str]]:
    """Sort skills into rough categories."""
    prog_langs = {"Python", "R", "SQL", "Java", "Scala", "C++", "C#", "JavaScript",
                  "TypeScript", "Go", "Rust", "Kotlin", "Swift", "MATLAB", "Julia", "Bash", "Shell"}
    cloud = {"AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "Terraform",
             "CI/CD", "Jenkins", "GitHub Actions", "Vertex AI", "SageMaker", "Databricks"}
    dbs = {"PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Cassandra",
           "BigQuery", "DynamoDB", "Oracle", "SQL Server", "Hive", "Presto", "Snowflake", "Redshift"}
    frameworks = {"TensorFlow", "PyTorch", "Keras", "Scikit-learn", "XGBoost", "LightGBM",
                  "Hugging Face", "Transformers", "spaCy", "NLTK", "FastAPI", "Flask", "Django",
                  "Spark", "PySpark", "Hadoop", "Airflow", "dbt", "Kafka", "Flink",
                  "MLflow", "Kubeflow", "LangChain"}

    categorized: dict[str, list[str]] = {
        "programming_languages": [], "frameworks_and_libraries": [],
        "tools_and_platforms": [], "databases": [],
        "cloud_and_infra": [], "soft_skills": [], "domain_skills": [], "certified_skills": [],
    }
    seen = set()
    for s in skills_flat:
        if s in seen:
            continue
        seen.add(s)
        sl = s.lower()
        if s in prog_langs:
            categorized["programming_languages"].append(s)
        elif s in cloud:
            categorized["cloud_and_infra"].append(s)
        elif s in dbs:
            categorized["databases"].append(s)
        elif s in frameworks:
            categorized["frameworks_and_libraries"].append(s)
        else:
            categorized["domain_skills"].append(s)
    return categorized


_LLM_EXTRACTION_PROMPT = """\
You are a resume parser. Extract the resume into strict JSON — no markdown, no extra text.

Output schema (use exactly these keys):
{
  "full_name": "...",
  "email": "...",
  "phone": "...",
  "profile_summary": "2-3 sentence professional summary",
  "work_experience": [
    {
      "job_title": "...",
      "company_name": "...",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM or Present",
      "is_current_role": false,
      "role_description": "Key responsibilities and achievements. Include specific tools, metrics, impact.",
      "skills": ["Python", "Spark", "AWS"]
    }
  ],
  "education": [
    {
      "degree": "Full degree name",
      "institution_name": "...",
      "education_level": "BACHELOR|MASTER|PHD|DIPLOMA|CERTIFICATION|OTHER",
      "end_date": "YYYY",
      "gpa": null
    }
  ],
  "skills": {
    "programming_languages": ["Python", "SQL"],
    "frameworks": ["TensorFlow", "LangChain"],
    "tools": ["Docker", "Airflow"],
    "cloud_platforms": ["AWS", "Azure"],
    "databases": ["PostgreSQL", "MongoDB"],
    "domain_skills": ["Machine Learning", "NLP"]
  }
}

Rules:
- Dates must be YYYY-MM format (e.g. "2021-03"). Use "Present" for current role end_date.
- skills per experience entry: list only tools/languages mentioned in that role's description.
- education_level: BACHELOR for B.Tech/B.S./B.E., MASTER for M.S./M.Tech/MBA, PHD for PhD/Doctorate.
- If a field is unknown use null for scalars, [] for arrays.
- CRITICAL - company_name must be the candidate's actual EMPLOYER (the organization that hired/paid
  them), never an internal project, account, initiative, product, or team name. IT-services/
  consulting resumes very often list ONE employer with several indented sub-headings underneath for
  different projects/accounts/initiatives (e.g. employer "Accenture" as the header, then "GenAI
  Initiative", "Client Migration Program", "Data Platform Revamp" as sub-bullets each with their own
  dates). Those sub-headings are NOT separate companies -- when you see this nested pattern, use the
  single top-level employer's name as company_name for every one of those date ranges, and fold the
  project/initiative name into role_description instead (e.g. "Worked on the GenAI Initiative
  project..."). Only emit a different company_name when the resume text itself states a genuinely
  different, real, named employer -- a distinct legal company/organization the candidate actually
  worked for, not just a differently-named internal project under the same employer. If in doubt
  (the name doesn't read like a real company and appears nested under an existing employer heading
  with no clear "joined a new company" signal), treat it as a project, not a new employer.
"""


def _llm_extract_resume(text: str) -> dict[str, Any] | None:
    """Use LLM to extract structured resume data from raw text.
    Returns a normalized resume_data dict or None if extraction fails."""
    try:
        from llm_client import call_llm_json, analysis_model, provider_enabled
        if not provider_enabled():
            return None
        result = call_llm_json(
            model_name=analysis_model("mistral-medium-latest"),
            messages=[
                {"role": "system", "content": _LLM_EXTRACTION_PROMPT},
                {"role": "user", "content": f"Resume text:\n\n{text[:8000]}"},
            ],
            max_tokens=3500,
        )
    except Exception as _exc:
        print(f"[pdf_extractor] LLM extraction exception: {_exc}")
        return None

    if not isinstance(result, dict):
        print(f"[pdf_extractor] LLM returned non-dict: {type(result)}")
        return None

    # Map LLM output to resume_data schema used by normalize_resume_data()
    work_exp = []
    for job in (result.get("work_experience") or []):
        if not isinstance(job, dict):
            continue
        work_exp.append({
            "job_title": job.get("job_title", ""),
            "company_name": job.get("company_name", ""),
            "start_date": job.get("start_date", ""),
            "end_date": job.get("end_date", ""),
            "is_current_role": bool(job.get("is_current_role")),
            "role_description": job.get("role_description", ""),
            "skills": job.get("skills") or [],
        })

    education = []
    for edu in (result.get("education") or []):
        if not isinstance(edu, dict):
            continue
        education.append({
            "degree": edu.get("degree", ""),
            "institution_name": edu.get("institution_name", ""),
            "education_level": edu.get("education_level", ""),
            "end_date": edu.get("end_date", ""),
            "gpa": edu.get("gpa"),
        })

    skills_raw = result.get("skills") or {}
    if not isinstance(skills_raw, dict):
        skills_raw = {}

    name = result.get("full_name", "")
    return {
        "personal_info": {
            "full_name": name,
            "first_name": name.split()[0] if name else "",
            "last_name": name.split()[-1] if name and len(name.split()) > 1 else "",
        },
        "contact_info": {
            "primary_email": result.get("email", ""),
            "primary_phone_number": result.get("phone", ""),
        },
        "profile_summary": result.get("profile_summary", ""),
        "work_experience_info": work_exp,
        "education_info": education,
        "skills_info": {
            "programming_languages": skills_raw.get("programming_languages", []),
            "frameworks": skills_raw.get("frameworks", []),
            "tools": skills_raw.get("tools", []),
            "cloud_platforms": skills_raw.get("cloud_platforms", []),
            "databases": skills_raw.get("databases", []),
            "domain_skills": skills_raw.get("domain_skills", []),
        },
    }


def pdf_to_resume_json(path: Path) -> dict[str, Any]:
    """Convert a PDF/DOCX to structured resume JSON.

    Tries LLM extraction first (better quality scores). Falls back to
    heuristic parsing if LLM is unavailable or fails.
    """
    text = extract_text(path)
    if not text.strip():
        return {}

    # Try LLM extraction (produces accurate dates, education_level, skills-per-role)
    llm_data = _llm_extract_resume(text)
    if llm_data:
        return {
            "resume_data": llm_data,
            "judge_results": [],
            "reflection_loop": 0,
            "_extraction_method": "llm_extraction",
            "_source_file": str(path),
        }

    # Fallback: heuristic parsing
    sections = _split_sections(text)
    header_lines = sections.get("header", [])
    exp_lines = sections.get("experience", [])
    edu_lines = sections.get("education", [])
    skill_lines = sections.get("skills", [])

    name = _extract_name(header_lines, path.name)
    email, phone = _extract_contact(text)
    experience = _parse_experience(exp_lines)
    education = _parse_education(edu_lines)
    skills_flat = _parse_skills(skill_lines, text)
    skills_info = _infer_skills_info(skills_flat)

    summary_lines = sections.get("summary", [])
    summary = " ".join(l.strip() for l in summary_lines if l.strip())[:1000] or ""

    return {
        "resume_data": {
            "personal_info": {
                "full_name": name,
                "first_name": name.split()[0] if name else "",
                "last_name": name.split()[-1] if name and len(name.split()) > 1 else "",
                "work_authorization": None,
                "gender": None,
                "nationality": None,
            },
            "contact_info": {
                "primary_email": email,
                "primary_phone_number": phone,
                "postal_address": None,
                "linkedin_url": None,
            },
            "education_info": education,
            "work_experience_info": experience,
            "skills_info": skills_info,
            "profile_summary": summary,
        },
        "judge_results": [],
        "reflection_loop": 0,
        "_extraction_method": "heuristic_pdf_parser",
        "_source_file": str(path),
    }


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(input_path: str, output_dir: str, skip_existing: bool = True) -> dict[str, Any]:
    inp = Path(input_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if inp.is_file():
        files = [inp]
    else:
        files = sorted(inp.rglob("*.pdf")) + sorted(inp.rglob("*.docx")) + sorted(inp.rglob("*.doc"))

    results = {"total": len(files), "success": 0, "failed": 0, "skipped": 0}

    for i, f in enumerate(files):
        out_path = out / (f.stem + ".json")
        if skip_existing and out_path.exists():
            results["skipped"] += 1
            continue
        print(f"  [{i+1}/{len(files)}] {f.name}", flush=True)
        try:
            data = pdf_to_resume_json(f)
            if not data:
                print(f"    [WARN] Empty output for {f.name}")
                results["failed"] += 1
                continue
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            results["success"] += 1
        except Exception as exc:
            print(f"    [WARN] Failed: {exc}")
            results["failed"] += 1

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch PDF/DOCX -> JSON converter for BERT training pipeline.")
    parser.add_argument("--input", required=True, help="Input PDF/DOCX file or directory")
    parser.add_argument("--output", required=True, help="Output directory for JSON files")
    parser.add_argument("--no-skip", action="store_true", help="Re-extract even if JSON already exists")
    args = parser.parse_args()

    print(f"Extracting from {args.input} -> {args.output}")
    results = run_batch(args.input, args.output, skip_existing=not args.no_skip)
    print(f"\nDone: {results['success']} success, {results['failed']} failed, {results['skipped']} skipped")


if __name__ == "__main__":
    main()
