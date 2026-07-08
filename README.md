# Resume Intelligence Engine

AI-powered resume analysis system built on BERT signal extraction + LLM judging (Mistral / OpenRouter).
Analyses OCR-extracted JSON resumes and produces structured recruiter-grade scorecards, DNA profiling, skill evidence maps, and qualitative insights.

---

## Architecture

```
OCR JSON Resume
      │
      ▼
┌─────────────────────────────────────────────┐
│  Signal Extraction Layer                    │
│  ├── BERT priors  (role / DNA / depth)      │
│  ├── Skill evidence engine (evidence.py)    │
│  ├── Experience engine                      │
│  ├── Education engine                       │
│  └── DNA engine  (5-type weighted zones)    │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  LLM Scoring Layer  (Mistral / OpenRouter)  │
│  ├── Scorecard  (0-100, 6 dimensions)       │
│  ├── Skill judgments                        │
│  └── Recruiter narrative                    │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
         Structured JSON output
         + Browser UI (FastAPI)
         + Streamlit dashboard
```

> **Note:** Scoring is LLM-only. If the LLM provider is unavailable the API returns HTTP 503.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11 or 3.12 | 3.10 may work but untested |
| Git | any | for cloning |
| Git LFS | any | **required** — BERT model weights are stored via LFS |
| LLM API key | — | Mistral or OpenRouter (see `.env` section) |

Install Git LFS if you don't have it:
```bash
# macOS
brew install git-lfs

# Ubuntu / Debian
sudo apt install git-lfs

# Windows  (download installer from https://git-lfs.com)
git lfs install   # run once after installing
```

---

## 1. Clone

```bash
git lfs install          # only needed once per machine
git clone https://github.com/tvarahRepo/resume-analysis.git
cd resume-analysis
```

> `git clone` will automatically pull LFS objects (the 4 BERT model bundles, ~2.3 GB total).
> If they didn't download, run `git lfs pull` manually inside the repo directory.

---

## 2. Python environment

```bash
python -m venv .venv

# Activate — Linux / macOS
source .venv/bin/activate

# Activate — Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Activate — Windows (CMD)
.venv\Scripts\activate.bat
```

---

## 3. Install dependencies

```bash
# Core API + web dependencies
pip install -r requirements.txt

# BERT / ML dependencies  (PyTorch, Transformers, etc.)
pip install -r requirements-ml.txt
```

> `requirements-ml.txt` installs PyTorch. If you have a GPU and want CUDA support, install the matching PyTorch CUDA build from https://pytorch.org/get-started/locally/ **before** running the above.

---

## 4. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in your keys. Minimum required fields:

```env
# ── LLM Provider ─────────────────────────────────────────────
# Choose one: mistral | openrouter | ollama
LLM_PROVIDER=openrouter

# ── OpenRouter (recommended — free tier available) ───────────
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_TIMEOUT_SEC=120
OPENROUTER_RETRIES=1

# ── Mistral (alternative) ────────────────────────────────────
# MISTRAL_API_KEY=your_mistral_key
# MISTRAL_BASE_URL=https://api.mistral.ai/v1

# ── Ollama (local, no API key needed) ────────────────────────
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://127.0.0.1:11434
# OLLAMA_MODEL=mistral:7b-instruct

# ── Model selection ──────────────────────────────────────────
SCORING_MODEL=openrouter/free
ANALYSIS_MODEL=openrouter/free
SUMMARY_MODEL=openrouter/free
PRIMARY_MODEL=openrouter/free
FALLBACK_MODEL=google/gemma-3-27b-it:free

# ── Feature flags ────────────────────────────────────────────
ENABLE_LLM_SCORING=true
ENABLE_LLM_SUMMARY=true

# ── BERT model bundle ────────────────────────────────────────
TRAINED_MODELS_DIR=trained_models_release_2026_04_18
```

---

## 5. Verify BERT models loaded

```bash
python -c "
from bert_signal_engine import infer_bert_priors
print('BERT models loaded OK')
"
```

Expected output: `BERT models loaded OK`
If you see a `FileNotFoundError`, the LFS objects didn't download — run `git lfs pull`.

---

## 6. Run the API server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Open in browser:

| URL | Purpose |
|-----|---------|
| `http://localhost:8000` | Main upload UI — drag and drop OCR JSON |
| `http://localhost:8000/health` | Health check |
| `http://localhost:8000/evals` | Evaluation dashboard |
| `http://localhost:8000/docs` | Auto-generated API docs (Swagger) |

---

## 7. Run Streamlit dashboard (optional)

In a **second terminal** (with the venv active):

```bash
streamlit run dashboard.py
```

Opens at `http://localhost:8501` — connects to the FastAPI backend at `http://localhost:8000`.

---

## Input format

The system expects an **OCR-extracted JSON** resume. A minimal example:

```json
{
  "name": "Jane Doe",
  "email": "jane@example.com",
  "experience": [
    {
      "title": "Senior Data Scientist",
      "company": "Acme Corp",
      "start_date": "2021-06",
      "end_date": "2024-01",
      "description": "Built ML pipelines with PySpark and MLflow. Reduced churn by 18%."
    }
  ],
  "education": [
    {
      "institution": "IIT Bombay",
      "degree": "B.Tech",
      "field_of_study": "Computer Science",
      "gpa": "8.9/10"
    }
  ],
  "skills": ["Python", "PySpark", "MLflow", "SQL", "Docker"]
}
```

See `sample_resume.json` for a full example.

---

## Project layout

```
resume-analysis/
├── app.py                      # FastAPI application + browser UI
├── engine.py                   # Main orchestrator (calls all engines)
├── taxonomy.py                 # Role families, skill map, institute dict, affinity maps
├── evidence.py                 # Skill evidence extraction
├── bert_signal_engine.py       # BERT prior inference (role / DNA / depth)
├── dna_engine.py               # DNA profiling (5 types, weighted zones)
├── education_engine.py         # Education scoring, degree level, field-tech fit
├── experience_engine.py        # Experience analysis, stability, trajectory
├── scoring_engine.py           # LLM scoring orchestration (no deterministic fallback)
├── semantic_taxonomy.py        # Cluster scoring + role family ranking
├── rule_based_qualitative.py   # Strengths, gaps, risk flags, panel suggestions
├── llm_resume_judge.py         # LLM skill judgments + resume analysis calls
├── llm_client.py               # LLM provider abstraction (Mistral / OpenRouter / Ollama)
├── dashboard.py                # Streamlit UI
├── eval_framework.py           # Evaluation & regression framework
├── requirements.txt            # Core dependencies
├── requirements-ml.txt         # PyTorch / Transformers
├── .env.example                # Environment variable template
└── trained_models_release_2026_04_18/   # BERT model weights (via Git LFS)
    ├── role_family/
    ├── dna_fit/
    ├── skill_depth/
    └── project_type/
```

---

## LLM provider options

| Provider | Free tier | Setup |
|----------|-----------|-------|
| **OpenRouter** | Yes (`google/gemma-3-27b-it:free`) | Set `OPENROUTER_API_KEY` |
| **Mistral** | Paid | Set `MISTRAL_API_KEY` |
| **Ollama** | Local / free | Install Ollama, pull `mistral:7b-instruct`, set `LLM_PROVIDER=ollama` |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `git lfs pull` hangs or fails | Check internet, re-run `git lfs pull --include="*.safetensors"` |
| `RuntimeError: Form data requires python-multipart` | `pip install python-multipart` |
| HTTP 503 on upload | LLM API key missing or provider down — check `.env` and provider status |
| `BERT models loaded` error | `TRAINED_MODELS_DIR` path in `.env` doesn't match folder name |
| Torch not found | Run `pip install -r requirements-ml.txt` |
| Port 8000 in use | `uvicorn app:app --port 8001 --reload` |

---

## Documentation

| File | Contents |
|------|---------|
| `RESUME_ANALYSIS_SYSTEM_DOC.md` | Full system architecture and scoring design |
| `CONFIDENCE_TRUST_SCORE_DOCUMENTATION.md` | Confidence and trust scoring details |
| `TRAINING_AND_RELEASE_GUIDE.md` | BERT training, evaluation, and release process |
| `EVAL_FRAMEWORK_FULL_DOCUMENTATION.md` | Evaluation framework across ML, product, and traces |
| `VM_DEPLOY_AND_RETRAIN_RUNBOOK.md` | VM deployment and retraining runbook |
