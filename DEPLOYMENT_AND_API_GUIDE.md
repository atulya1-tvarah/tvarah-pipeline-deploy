# Tvarah Resume Intelligence — Deployment & API Guide

**For admin use. Hand this to Aditya / infra team.**

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  tvarah-portal-ui  (Next.js 14, port 3000)                      │
│  github: tvarahRepo/tvarah-portal-ui                            │
│  Auth: Keycloak + next-auth    Data: Prisma → PostgreSQL        │
│  Also calls: TVARAH_API_URL (Spring Boot / FastAPI backend)     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ REST (Keycloak Bearer token)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI AI Engine  (Python, port 8000)                         │
│  github: atulya1-tvarah/Resume-parsing  (main repo)             │
│  github: adityabikramtvarah/Resume-analysis-  (mirror)         │
│  - /api/v1/*  → Portal REST API (JWT)                           │
│  - /*         → Internal HTML recruiter UI                      │
│  - BERT models: trained_models_release_v4/                      │
│  - LLM: OpenRouter (free tier GLM/Gemma)                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │ psycopg2
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  PostgreSQL  (10.0.0.6:5432,  DB: tvarah)                       │
│  Contains BOTH sets of tables:                                  │
│  - Liquibase-managed: candidate, job_description, user, skill…  │
│  - App-internal: candidates, job_postings, users, jd_matches…   │
│  - AI tables: candidate_ai_profile, candidate_outcome…          │
│  - Prisma-managed: (when npx prisma migrate deploy is run)      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. VM Setup — FastAPI AI Engine

### 1.1  Prerequisites (on VM)
```
Windows 10/11 or Windows Server 2019+
Python 3.11 or 3.12
Git + Git LFS
Node.js 18+ (for UI only)
```

### 1.2  Clone / Pull the repository
```cmd
:: First time
cd E:\
git lfs install
git clone https://github.com/atulya1-tvarah/Resume-parsing.git Resume-analysis-
cd Resume-analysis-

:: Every subsequent update (run as admin or pull user)
cd E:\Resume-analysis-
git pull origin main
git lfs pull
```

### 1.3  Python virtual environment
```cmd
cd E:\Resume-analysis-
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-ml.txt
```

### 1.4  Environment file
Create `E:\Resume-analysis-\.env`:
```env
# Database
DB_HOST=10.0.0.6
DB_PORT=5432
DB_NAME=tvarah
DB_USER=tvarah
DB_PASSWORD=4pVhZ873Rm6C

# Portal JWT (keep this secret in production)
PORTAL_JWT_SECRET=tvarah-portal-secret-change-in-prod-2025

# LLM — OpenRouter
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=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
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_TIMEOUT_SEC=120
OPENROUTER_RETRIES=1
PRIMARY_MODEL=openrouter/free
FALLBACK_MODEL=google/gemma-3-27b-it:free
SCORING_MODEL=openrouter/free
ANALYSIS_MODEL=openrouter/free
SUMMARY_MODEL=openrouter/free
TEMPERATURE=0
ENABLE_LLM_SCORING=true
ENABLE_LLM_SUMMARY=true

# BERT models
TRAINED_MODELS_DIR=trained_models_release_v4
EVIDENCE_ENCODER_BACKEND=transformers
ENABLE_NEW_RUBRIC=true
```

### 1.5  Start the server
```cmd
cd E:\Resume-analysis-
.venv\Scripts\activate
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

To restart without opening a new terminal window:
```powershell
# PowerShell — kills old process and starts fresh
.\restart_server.ps1
```

### 1.6  Verify it's running
```
http://10.0.0.6:8000/health          → {"status":"ok"}
http://10.0.0.6:8000/api/v1/health   → {"status":"ok","service":"tvarah-portal-api"}
```

### 1.7  BERT models
The trained models ship inside the repo under `trained_models_release_v4/`:
```
trained_models_release_v4/
  role_family/    model.safetensors  (ModernBERT, 68% acc, 10 classes)
  skill_depth/    model.safetensors  (DistilBERT)
  dna_fit/        model.safetensors  (DistilBERT, 4 classes)
```
These are auto-loaded at startup via `TRAINED_MODELS_DIR=trained_models_release_v4`.
No extra step needed — `git lfs pull` downloads them.

---

## 2. UI Setup — Next.js Portal

### 2.1  Clone
```cmd
cd E:\
git clone https://github.com/tvarahRepo/tvarah-portal-ui.git
cd tvarah-portal-ui
```

### 2.2  Install dependencies
```cmd
npm install
```

### 2.3  Environment file
Create `E:\tvarah-portal-ui\.env` (or `.env.local`):
```env
# Database — same PostgreSQL used by the AI engine
DATABASE_URL="postgresql://tvarah:4pVhZ873Rm6C@10.0.0.6:5432/tvarah"

# Next-auth
NEXTAUTH_URL=http://10.0.0.6:3000
NEXTAUTH_SECRET=tvarah-nextauth-secret-change-in-prod

# Tvarah backend (Spring Boot or FastAPI)
# Point to FastAPI engine for AI features, Spring Boot for Keycloak auth
TVARAH_API_URL=http://10.0.0.6:8000
NEXT_PUBLIC_TVARAH_API_URL=http://10.0.0.6:8000

# Keycloak (fill in when Keycloak is provisioned)
KEYCLOAK_ISSUER_URI=https://keycloak.tvarah.com/realms/tvarah
KEYCLOAK_BACKEND_CLIENT_ID=tvarahbackend
KEYCLOAK_BACKEND_CLIENT_SECRET=<from Keycloak admin>
KEYCLOAK_CLIENT_ID=tvarah-portal
```

### 2.4  Prisma — generate client + push schema
```cmd
npx prisma generate
npx prisma db push       # creates/updates Prisma-managed tables in tvarah DB
```

### 2.5  Start UI
```cmd
# Development
npm run dev              # http://10.0.0.6:3000

# Production build
npm run build
npm run start            # http://10.0.0.6:3000
```

---

## 3. Portal API Reference  (`/api/v1/*`)

Base URL (local): `http://10.0.0.6:8000`
Auth: `Authorization: Bearer <JWT>` on all endpoints except `/auth/login` and `/health`.

### Auth
| Method | Path | Body / Params | Response |
|--------|------|---------------|----------|
| `POST` | `/api/v1/auth/login` | `{email, password}` | `{access_token, token_type, user}` |
| `GET`  | `/api/v1/auth/me` | — | user profile |

**Default credentials (all users):**  password = `Tvarah@2025`

| User | Email | Role |
|------|-------|------|
| Sandeep Guduru | `sandeep@tvarah.com` | super_admin |
| Aditya Bikram | `Aditya.vikram@tvarah.com` | Site Admin |
| Uday Singh | `uday.matta@tvarah.com` | Site Admin |
| Rajeev Kuchana | `rajeev@tvarah.com` | Site Admin |
| Sandeep Guduru | `sandeep.guduru@tvarah.com` | Site Admin |
| Sairupa Golla | `sairupa.golla@tvarah.com` | Site Admin |
| Vikas Vij | `vikas@tvarah.com` | Site Admin |
| Ravi Sharma | `ravi.sharma@gmail.com` | Client-TA Head |
| Uday Singh | `us007sep@gmail.com` | Admin |

### Candidates
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/candidates` | Paginated list. Params: `q`, `stage`, `role_family`, `min_score`, `page`, `page_size` |
| `GET`  | `/api/v1/candidates/{id}` | Full profile: base info + experience + education + skills + AI analysis |
| `POST` | `/api/v1/candidates` | Create candidate. Body: `{first_name, last_name, primary_email, …}` |
| `PATCH`| `/api/v1/candidates/{id}` | Update stage/pipeline/scores. Body: `{stage, pipeline_status, assigned_recruiter, interview_date, interview_round, recruiter_score, panel_score}` |
| `POST` | `/api/v1/candidates/{id}/analyze` | Trigger AI analysis. Body: optional `{parsed_resume: {...}}` |
| `GET`  | `/api/v1/candidates/{id}/analysis` | Get latest AI analysis |
| `POST` | `/api/v1/candidates/{id}/outcome` | Record outcome. Body: `{outcome, rejection_stage, placed_company, placed_role, placed_date, ctc_offered, feedback_notes}` |
| `GET`  | `/api/v1/candidates/{id}/outcome` | Get outcome |

**Candidate stages pipeline:**
```
SOURCED → SCREENING → TELEPHONIC → L1_INTERVIEW → L2_INTERVIEW → OFFER → PLACED
                                                                        ↘ REJECTED
```

### Jobs / JDs
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/jobs` | List JDs. Params: `status`, `page`, `page_size` |
| `POST` | `/api/v1/jobs` | Create JD. Body: `{code, job_type, job_level, description, required_skills[], yoe_min, yoe_max, total_positions, location_city, department, role_summary}` |
| `GET`  | `/api/v1/jobs/{id}` | JD detail + candidate matches |
| `PATCH`| `/api/v1/jobs/{id}` | Update status/positions/deadline |
| `DELETE`| `/api/v1/jobs/{id}` | Close JD |
| `POST` | `/api/v1/jobs/{id}/match_all` | Run AI match against all analyzed candidates |

### Matching
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/candidates/{cid}/match/{jd_id}` | Get match result for candidate↔JD pair |
| `POST` | `/api/v1/candidates/{cid}/match/{jd_id}` | Trigger match for specific candidate↔JD |

### Dashboard
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/dashboard/stats` | Overview: total candidates, analyzed, open jobs, placed, stage breakdown, role breakdown, score stats |

### Users (admin only)
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/users` | List all users |
| `POST` | `/api/v1/users/invite` | Create user. Body: `{first_name, last_name, email, role, password}` |
| `PATCH`| `/api/v1/users/{id}` | Update role/status/password |

### Reference Data
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/ref/skills` | All 65 skills with category/tier |
| `GET`  | `/api/v1/ref/job_titles` | All job titles |
| `GET`  | `/api/v1/ref/companies?q=name` | Company search |

---

## 4. BERT Models

### What's trained
| Task | Model | Classes | Accuracy |
|------|-------|---------|----------|
| `role_family` | ModernBERT-base | 10 (AI_ARCHITECT, GENAI_DATA_SCIENTIST, NLP_LLM_ENGINEER, …) | 68% |
| `skill_depth` | DistilBERT | 5 (AWARENESS → ARCHITECT_LEVEL) | — |
| `dna_fit` | DistilBERT | 4 (CONSULTING, PRODUCT, PLATFORM_INFRA, DOMAIN_SPECIALIST) | — |

### Repo locations
- **Primary**: `atulya1-tvarah/Resume-parsing` (main, git lfs)
- **Mirror**: `adityabikramtvarah/Resume-analysis-`

### Retraining (when needed)
```cmd
cd E:\Resume-analysis-
.venv\Scripts\activate

:: Rebuild training exports from new JSONs
python training_data_builder.py "path\to\json\folder" --output-dir training_exports_new

:: Retrain (all tasks)
python bert_retrain.py --resume path\to\json\ training_exports_new\ \
  --tasks role_family skill_depth dna_fit career_progression stakeholder_management mentorship_signal \
  --model-name distilbert-base-uncased --output-dir trained_models_v5

:: Promote best models to release bundle
mkdir trained_models_release_v5
xcopy /E /I /Y trained_models_v5\role_family trained_models_release_v5\role_family

:: Update .env
:: TRAINED_MODELS_DIR=trained_models_release_v5

:: Push to GitHub (Git LFS handles large files)
git add trained_models_release_v5\
git commit -m "feat: BERT v5 release bundle"
git push origin main
git push atulya main
```

---

## 5. Database Quick Reference

**Connection:** `postgresql://tvarah:4pVhZ873Rm6C@10.0.0.6:5432/tvarah`

### Key tables
| Table | Owner | Purpose |
|-------|-------|---------|
| `candidate` | Liquibase (prod) | Core candidate records (UUID PK) |
| `candidate_ai_profile` | App | AI scores, band, role_family, analysis_json |
| `candidate_outcome` | App | PLACED / REJECTED / IN_PROGRESS |
| `candidate_score` | Liquibase (prod) | Score history |
| `candidate_resume_summary` | Liquibase (prod) | BERT tags, parsed resume JSON |
| `candidate_intelligence_insight` | Liquibase (prod) | Decision signals, risk indicators |
| `candidate_experience` | Liquibase (prod) | Work history |
| `candidate_education` | Liquibase (prod) | Education history |
| `candidate_skill` | Liquibase (prod) | Skill proficiency |
| `job_description` | Liquibase (prod) | JDs with skills, exp range, etc. |
| `candidate_job` | Liquibase (prod) | Candidate↔JD application links |
| `candidate_job_evaluation` | Liquibase (prod) | Match scores per candidate↔JD |
| `user` | Liquibase (prod) | Portal users (Keycloak-linked, now also password_hash) |
| `skill` | Liquibase (prod) | 65 skills with category/tier |
| `candidates` | App internal | AI engine's own candidate store (TEXT PK, analysis JSON) |
| `job_postings` | App internal | AI engine's own JD store |
| `users` | App internal | AI engine's own user store (password_hash) |

---

## 6. Quick Troubleshoot

| Symptom | Fix |
|---------|-----|
| `/api/v1/health` returns 404 | Server not updated. Pull + restart |
| Login returns 403 "Account inactive" | Run: `UPDATE "user" SET status='Active' WHERE status IN ('Draft','Pending')` |
| BERT not loading | Check `TRAINED_MODELS_DIR` in `.env`. Run `git lfs pull` |
| DB connection refused | Check `DB_HOST`, firewall on 10.0.0.6:5432 |
| `npm run dev` fails on UI | Run `npx prisma generate` first, then `npm install` |
| UI login loops | `NEXTAUTH_SECRET` missing or `KEYCLOAK_ISSUER_URI` wrong |

---

## 7. Pending / For Keycloak Admin

The Next.js portal UI (`tvarah-portal-ui`) uses **Keycloak** for auth — it cannot use the FastAPI JWT directly. To make the UI login work:

1. Provision Keycloak realm `tvarah` at `https://keycloak.tvarah.com`
2. Create client `tvarahbackend` with `client_credentials` grant
3. Create client `tvarah-portal` for OIDC
4. Add Spring Boot backend endpoint: `POST /api/v1/auth/login` (sends OTP email)
5. Add Spring Boot backend endpoint: `POST /api/v1/auth/verify-otp` (returns Keycloak token)
6. Fill in `.env`: `KEYCLOAK_ISSUER_URI`, `KEYCLOAK_BACKEND_CLIENT_ID`, `KEYCLOAK_BACKEND_CLIENT_SECRET`

**Until Keycloak is set up**, the UI login page will call `portal.tvarah.com` (production Spring Boot) or fail if that's not accessible. The FastAPI `/api/v1/*` endpoints work independently with JWT and can be used by any other client.
