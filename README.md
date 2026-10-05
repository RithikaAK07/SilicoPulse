# SilicoPulse

**AI-Powered Silicon Validation & Configuration Intelligence Platform** (SanDisk Hackathon)

This app ingests large, high-dimensional storage test campaigns and answers the eight core hackathon questions. The default dataset has 10,000+ executions, 100+ configuration parameters, 50+ randomized variables, telemetry, log traces and pass/fail outcomes. The analysis runs on ML models, DuckDB/Polars OLAP queries and a Gemini GenAI layer. The GenAI layer falls back to local heuristics when Gemini is unavailable.

```
frontend/  Next.js 14 · React 18 · TypeScript · Tailwind · shadcn-style UI · Recharts + Chart.js · Zustand · TanStack Query
backend/   FastAPI · DuckDB · Polars · scikit-learn · LightGBM · Google Gemini (raw HTTP)
```

## Quick start (Windows)

```powershell
# 1. Backend (first run creates the venv; generates a 10k-run dataset + trains models on startup in ~5 s)
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev          # http://localhost:3000
```

You can also run `start.ps1` from the repo root to launch both. API docs are at http://localhost:8000/docs.

Set `NEXT_PUBLIC_API_URL` (see `frontend/.env.local.example`) if the API is not on `localhost:8000`.

## Login & roles

Opening http://localhost:3000 redirects to `/login`. The login page has one-click demo buttons for three roles:

| Role | Email / password | Permissions |
|---|---|---|
| Validation Lead | `admin@sandisk.com` / `admin123` | read, upload CSV, generate datasets |
| VLSI Engineer | `engineer@sandisk.com` / `eng123` | read, upload CSV, generate datasets |
| Executive Viewer | `executive@sandisk.com` / `exec123` | read-only |

How auth works:

- **Backend (`backend/app/auth.py`):**
  - Passwords are stored as bcrypt hashes.
  - Sessions are HS256 JWTs that last 8 hours.
  - Every `/api/*` route except `/api/health` and `/api/login` needs a bearer token.
  - Upload and generate routes check permissions and return 403 for the Executive Viewer.
  - The signing secret comes from the `JWT_SECRET` environment variable. If that isn't set, a random secret is saved in `backend/data/.jwt_secret`.
- **Frontend:** `src/context/AuthContext.tsx` keeps the token in localStorage and checks it with `/api/me` on load. An expired session sends the user back to `/login`.

## CSV upload (`/upload` tab)

1. Drag and drop a CSV, or click the dropzone. `POST /api/upload-csv/preview` stores the file and auto-detects each column's role: outcome, performance metric, seed, timestamp, error signature, log text, run/config ID, context, config parameter, randomized variable or telemetry.
2. A mapping dialog opens. You pick the **Outcome** column and which of its values mean FAIL, plus the **Performance metric**. You can also map the optional fields and override any column's role.
3. `POST /api/upload-csv` maps the CSV onto the platform's internal columns, re-points DuckDB at it and retrains every model. All Q1–Q8 views and the Copilot then use the uploaded data. Columns the CSV doesn't have get neutral defaults, and the dashboard shows "–" for them.
4. The header badge shows **Using Uploaded CSV** or **Using Benchmark Data**. **Revert to benchmark data** calls `POST /api/dataset/reset`. The uploaded dataset is saved to disk and survives an API restart.

The **📥 Download Sample SanDisk Execution CSV** button calls `GET /api/download-sample-csv`. It returns a 1,000-row execution log you can upload straight back to test the pipeline.

Uploads must have at least 50 rows, at least 10 passing and 10 failing runs, and be no larger than 200 MB. A rejected upload never replaces the active dataset.

## AI Copilot (fully AI-driven)

`/copilot` is a chatbot driven by a Gemini agent that calls tools (`backend/app/copilot_agent.py`, endpoint `POST /api/copilot/chat`, which streams Server-Sent Events).

- **Reads the whole conversation.** Gemini gets the chat history, so follow-up questions work. It decides on its own how to respond.
- **Fetches real data with tools.** It can call 16 tools over the live dataset:
  - overview and KPIs
  - feature importance, Pareto configs and parameter pairs
  - randomization and seed analysis, determinism
  - root causes, config profile and compare, run diff, list runs
  - predict and recommend
  - **read-only SQL** (`query_data`), which runs in a DuckDB sandbox with no file or network access
  - two display tools: `render_chart` and `suggest_follow_ups`
- **Starts from a snapshot.** A pre-computed analytics snapshot is in its instructions, so common questions take a single Gemini call.
- **Streams the answer.** Text arrives token by token, along with live "analysis steps", the charts it chooses and clickable follow-up questions. There are Stop and Retry buttons, and the chat history persists.
- **No canned answers.** If every model is out of quota, the chat shows an explicit error with a retry time instead.

## Gemini AI layer

The key is hard-coded in `backend/app/config.py` (`GEMINI_API_KEY`). A `GEMINI_API_KEY` environment variable takes precedence over it. Free-tier quotas apply per model (about 20 requests per day each), so calls rotate through a pool of 7 Flash models (`GEMINI_MODELS` in `config.py`, logic in `app/gemini_pool.py`). A model that returns 429 is skipped until Google's `retryDelay` has passed. A model that returns 503 (overloaded) is skipped for 20 seconds. The executive summary and recommendation explanations fall back to local text when no model is available; the Copilot does not use local text.

If a call fails, every AI feature switches to the local heuristic engine in `app/ai.py`, so the app never crashes. Failures that trigger this:

- The key is empty or still the placeholder.
- The key is invalid or Google denies the project (HTTP 401/403). The app waits 5 minutes before retrying.
- Gemini is rate-limited (HTTP 429). The app waits 60 seconds before retrying.
- The network fails or the call times out.

The UI shows a badge with the source of each answer: **Gemini** or **Local heuristic AI**.

> ⚠️ The key is in source code. Don't push this repo publicly with the key inside. Move it to the env var before sharing.

## How each hackathon question is answered

| Q | Where | Method |
|---|---|---|
| Q1 Influence | Config Discovery | Random-Forest impurity importance + mutual information + point-biserial correlation |
| Q2 Optimal pairs | Config Discovery | Pareto frontier (throughput ↑ vs failure rate ↓) per config profile; two-way pair mining (best & toxic); CSV/JSON export |
| Q3 Randomization impact | Randomization Engine | Failure-rate swing across octiles + RF importance + MI; sensitivity curves; seed × perturbation heatmap |
| Q4 Determinism | Randomization Engine | Profile repeatability across seeds (≥80% fail → deterministic); seed z-score vs config-only model expectation; K-Means failure clustering + PCA |
| Q5 Root cause | Root Cause & Logs | Per-signature fingerprints: condition-lift mining, log template mining (numbers → `<*>`), anomaly clusters |
| Q6 Diff & change impact | Root Cause & Logs | Run A vs Run B config/random/telemetry diff, SHAP-Δ change attribution, template-aligned log diff, auto-pair nearest passing run |
| Q7 Prediction | Predict & Prescribe | Pre-execution LightGBM + Random Forest ensemble (config + context only), live sandbox, TreeSHAP breakdown |
| Q8 Prescription | Predict & Prescribe | 4,000-candidate search maximizing throughput × (1−risk)³ under a risk ceiling + greedy flag refinement; confidence = model agreement + risk + AUC; NL explanation |

**Innovation features:**

- **AI Copilot.** Answers natural-language questions with an intent router and grounded facts. Gemini writes the Markdown; the reply also includes an inline chart and key-value chips.
- **Drift visualizer.** Chart.js charts of each randomized variable's daily mean and p95 against its drift limit.
- **2D hardware topology risk map.**
- **One-click synthetic dataset generator.** Comes with presets and retrains every model automatically.
- **Executive GenAI summary.**
- **SHAP explainability bars.**

## Synthetic data

`backend/app/generator.py` builds the dataset around a hidden ground-truth failure model with three kinds of drivers:

- **Deterministic config interactions:**
  - `queue_depth≥128 & write_cache=0`
  - `gc_policy=aggressive & block_size≤8K`
  - `zstd & threads≥32`
- **Stochastic drivers:**
  - thermal drift
  - timing jitter
  - 6 "unlucky" firmware seeds under load
- **Weak effects:** low over-provisioning and ECC strength.

The analytics recover these drivers without being told what they are. You can check the anomalous seeds against `hidden_unlucky_seeds` in `backend/data/meta.json`.
