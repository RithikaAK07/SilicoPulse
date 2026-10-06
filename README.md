# SilicoPulse

**AI-Powered Silicon Validation & Configuration Intelligence Platform** (SanDisk Hackathon)

SilicoPulse ingests large, high-dimensional storage test campaigns and answers the eight core hackathon questions. A campaign can have 100,000+ executions, 100+ configuration parameters, 50+ randomized variables, telemetry, log traces and pass/fail outcomes.

Every statistic is computed in Python: DuckDB/Polars aggregations, scikit-learn and LightGBM models, and statistical tests. Gemini only interprets and explains that **evidence**; it never produces the numbers.

```
frontend/  Next.js 14 · React 18 · TypeScript · Tailwind · shadcn-style UI · Recharts + Chart.js · Zustand · TanStack Query
backend/   FastAPI · DuckDB · Polars · scikit-learn · LightGBM · SciPy · Google Gemini (raw HTTP, function calling)
docs/      architecture.md: layers, data flow, evidence pipeline, Copilot pipeline
```

See **[docs/architecture.md](docs/architecture.md)** for the full architecture.

## Quick start (Windows)

```powershell
# 1. Backend: generates a 10k-run benchmark and trains the models on first start (~10 s)
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy NUL .env            # then add: GEMINI_API_KEY=<your key>   (optional: the app runs without AI)
.venv\Scripts\uvicorn app.main:app --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev              # http://localhost:3000
```

You can also run `start.ps1` from the repo root to launch both. API docs are at http://localhost:8000/docs.

## Environment variables

| Variable | Where | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | backend (`backend/.env` locally, Railway variables in production) | – | Gemini key. **Never commit it.** |
| `JWT_SECRET` | backend | random value persisted in `data/.jwt_secret` | HS256 signing secret. Set it in production so sessions survive redeploys. |
| `CORS_ORIGINS` | backend | `*` | Comma-separated allowed origins. In production, set it to the frontend URL(s). |
| `SILICOPULSE_DEFAULT_RUNS` | backend | `10000` | Size of the generated benchmark (tested up to 100,000). |
| `SILICOPULSE_DATA_DIR` | backend | `backend/data` | Data folder (the tests point it at a temp dir). |
| `MIN_PAIR_SAMPLES` | backend | `0` (auto: 15–60, scales with data size) | Minimum runs before a parameter combination can be called toxic. |
| `MIN_GROUP_SAMPLES` | backend | `30` | Minimum runs per parameter value or threshold group. |
| `MIN_SEED_RUNS` | backend | `20` | Minimum runs before a seed can be scored or flagged. |
| `MIN_PROFILE_REPEATS` / `MIN_PROFILE_SEEDS` | backend | `5` / `3` | Repeats and distinct seeds needed before a config can be called deterministic. |
| `SIGNIFICANCE_ALPHA` | backend | `0.01` | Significance level, applied with Bonferroni correction. |
| `NEXT_PUBLIC_API_URL` | frontend (build time) | `http://localhost:8000` | Backend URL. |

## Login & roles

Opening http://localhost:3000 redirects to `/login`, which has one-click demo buttons for three roles. These are **demo credentials** for the hackathon prototype:

| Role | Email / password | Permissions |
|---|---|---|
| Validation Lead | `admin@sandisk.com` / `admin123` | read, upload CSV, generate datasets |
| VLSI Engineer | `engineer@sandisk.com` / `eng123` | read, upload CSV, generate datasets |
| Executive Viewer | `executive@sandisk.com` / `exec123` | read-only |

- **Backend (`backend/app/auth.py`):** passwords are stored as bcrypt hashes, and sessions are HS256 JWTs that last 8 hours. Every `/api/*` route except `/api/health` and `/api/login` needs a bearer token. Upload and generate routes return 403 for the Executive Viewer.
- **Frontend:** `src/context/AuthContext.tsx` keeps the token in localStorage and checks it with `/api/me` on load.

## Evidence layer (trust)

The **Evidence & Guardrails** page (`/insights`) and the Copilot both read from one evidence engine (`app/evidence.py`, `app/insights.py`, `app/stats.py`). Every finding has a sample size, a failure rate vs the baseline, a lift, a 95% Wilson CI, a p-value and z-score (Bonferroni-corrected), and an evidence-strength label. Findings use **associational language**: no causal method is implemented, so nothing claims causation.

| Analysis | What it computes |
|---|---|
| High-risk parameters | Importance, correlation, χ² p-value, Cramér's V, and the riskiest and safest value with failure rate, lift and n |
| Toxic combinations | Runs, failures, failure rate, baseline, lift, odds ratio, interaction lift (vs the stronger single setting) and p-value, with a configurable minimum support |
| Environmental thresholds | A risk threshold **learned** per variable (the percentile cut with the best two-proportion z), plus how the share of runs beyond it changed from early to late in the campaign. Nothing like "60 °C" is assumed in advance. |
| Failure signatures | Hierarchy: signature → associated parameters → environment (Cohen's d) → seeds → hardware → log patterns → supporting runs; deterministic, stochastic or mixed tendency |
| Determinism | Per-profile repeatability with minimum repeats and seeds; anything below that threshold is labelled **insufficient** |
| Anomalous seeds | Observed vs model-expected failure rate, z-score, p-value and 95% CI; flagged only with ≥ `MIN_SEED_RUNS` runs and a Bonferroni-significant result |
| Hardware | Failure rate with CI, significance vs the other tiers, matched-configuration comparison, cross-tier patterns, dominant signatures, sensitivity |
| Guardrails | Risky conditions **discovered from data** (pairs, values, thresholds), each with failure rate, baseline, lift, n, significance, severity and a safer observed alternative |
| Data quality | Totals, missing values (measured at upload), duplicates, invalid values, log and timestamp coverage, and **real vs derived** fields with explicit warnings (e.g. "Seed analysis unavailable: random seed field not present.") |
| Model validation | 80/20 hold-out + 5-fold stratified CV: ROC-AUC, PR-AUC, precision, recall, F1, confusion matrix, calibration curve, Brier score, class balance, global SHAP |

New endpoints, all **additive**: existing response formats are unchanged and only gain optional fields.

- `GET /api/insights`, filterable with `?hardware=…&environment=…&workload=…&date_from=…&date_to=…&signature=…&config_id=…&seed=…`
- `GET /api/insights/{parameters|pairs|environment|signatures|hardware|guardrails|seeds|determinism}`
- `GET /api/data-quality` and `GET /api/model/validation`

## CSV upload (`/upload` tab)

1. Drag and drop a CSV. `POST /api/upload-csv/preview` stores it and auto-detects each column's role.
2. The mapping dialog asks for the **Outcome** column (and which values mean FAIL) and the **Performance metric**. You can override any column's role.
3. `POST /api/upload-csv` canonicalizes the data, records missing values and log coverage, re-points DuckDB, retrains every model and pre-computes the analytics. Any column that perfectly predicts pass/fail (a leaked label) is dropped automatically and reported.
4. The header badge shows **Using Uploaded CSV** or **Using Benchmark Data**. **Revert to benchmark data** calls `POST /api/dataset/reset`.

**📥 Download Sample SanDisk Execution CSV** (`GET /api/download-sample-csv`) returns a 1,000-row log to test the pipeline. An upload needs ≥ 50 rows and ≥ 10 passing and 10 failing runs, and can be up to 200 MB.

### Universal file preprocessing

The upload tab accepts **CSV, TXT, LOG, JSON, XLSX, XLS and ZIP** (≤ 200 MB). Each file runs through `backend/app/preprocessor/`:
file-type detection → parsing → field detection (role, confidence and reason per column) → mapping → validation. Only then is it handed to the
existing ingestion pipeline. The UI shows each step, the detected data type, every column's suggested role and unit, the validation messages,
and (for a ZIP) every member with its status.

- `POST /api/upload/preview` returns the preview. `POST /api/upload/ingest` takes `upload_id`, `part_id`, a `mode` and a `mapping`.
  - `mode=execution` uses the existing canonicalize-and-retrain path and the existing mapping dialog.
  - `mode=telemetry` is described below.
- The legacy `/api/upload-csv/preview` and `/api/upload-csv` endpoints are unchanged and still CSV-only.
- **Telemetry-only data (no PASS/FAIL outcome)**, such as a machine-health CSV with `Timestamp, vibration_g, motor_temp_c, spindle_rpm`:
  - It is accepted and classified (e.g. *Industrial telemetry*).
  - No outcome is ever invented, and it can't be ingested as an execution log.
  - It is stored as a separate telemetry dataset: per-channel statistics, robust-z anomalies, trends, sampling gaps and charts. The active execution dataset and models are not changed.
  - Analyses that need an outcome, error signature or seed report *"Required field not available for this analysis."*
  - `GET /api/telemetry/status` returns the telemetry dataset; `POST /api/telemetry/reset` removes it.
- **Units** come only from explicit column-name suffixes (`_c`, `_rpm`, `_g`, `_kpa`, …); otherwise they show as *unit unknown*. The only conversion is an explicit °F → °C.
- **ZIP safety:**
  - Members are read in memory and never extracted.
  - Path-traversal and absolute names are rejected (zip-slip).
  - Limits apply to member count (100), member size (200 MB), total uncompressed size (500 MB) and compression ratio (200×).
  - Encrypted and nested archives are not processed.
  - Files with identical columns can be combined (tagged with `source_file`); incompatible files are kept separate and the reason is shown.
- **Excel** is read with `fastexcel` (calamine, no macros or formulas executed). Each non-empty sheet becomes a selectable dataset.

## AI Copilot (evidence-grounded)

`/copilot` is a Gemini function-calling agent (`app/copilot_agent.py`, `POST /api/copilot/chat`, which streams Server-Sent Events). For each question:

1. **Intent detection.** A keyword classifier routes the question: parameters, pairs, environment, seeds, determinism, signatures, hardware, recommendation, prediction or data quality.
2. **Evidence retrieval.** The matching analytics are computed for the **active dashboard filters** and placed in the prompt. This is also shown to the user as evidence cards.
3. **Generation.** Gemini answers from that evidence. It can call 26 tools for more, and the 10 evidence tools receive the dashboard filters automatically. It is told never to compute statistics itself, to give n and the baseline with every rate, to use associational language, and to follow the structure Executive Finding → Evidence → Top Factors → Failure Pattern → Affected Configurations → Evidence Strength → Recommended Action.
4. **Grounding check.** Every figure in the answer is traced back to a number the analytics produced. The UI shows "Evidence check: N/M figures traced to computed analytics" and lists anything it couldn't trace.

## Gemini AI layer

The key comes **only** from the environment: `backend/.env` locally (gitignored) or the Railway service variables in production. Free-tier quotas apply per model (about 20 requests per day each), so calls rotate through a pool of Flash models (`GEMINI_MODELS` in `config.py`, logic in `app/gemini_pool.py`):

- A model that returns 429 is skipped until Google's `retryDelay` has passed.
- A model that returns 503 is skipped for 20 seconds.

When no model is available:

- The **Copilot** shows an explicit error with a retry time. It never gives a canned answer.
- The **executive summary** and **recommendation explanations** fall back to local text, and their badge says so.

## How each hackathon question is answered

| Q | Where | Method |
|---|---|---|
| Q1 Influence | Config Discovery, Evidence | RF importance + mutual information + correlation; per-value χ², Cramér's V, lift, significance |
| Q2 Optimal pairs | Config Discovery, Evidence | Pareto frontier per profile; toxic and best pairs with lift, odds ratio, interaction lift, Bonferroni p, min support |
| Q3 Randomization impact | Randomization Engine, Evidence | Failure-rate swing + RF importance + MI; learned thresholds; seed × perturbation heatmap |
| Q4 Determinism | Randomization Engine | Repeatability across seeds with minimum repeats and seeds; seed z-tests with CI; K-Means failure clusters |
| Q5 Root cause | Root Cause & Logs, Evidence | Signature hierarchy: condition lift, environment shift (Cohen's d), associated seeds, hardware, log templates |
| Q6 Diff & change impact | Root Cause & Logs | Run diff, SHAP-Δ attribution, template-aligned log diff |
| Q7 Prediction | Predict & Prescribe, Evidence | LightGBM + RF ensemble on pre-execution features; hold-out + CV, calibration, confusion matrix |
| Q8 Prescription | Predict & Prescribe | 4,000-candidate search with training-data **support** (nearest observed config, explicit **extrapolation** label), uncertainty, Pareto status, why and trade-offs |

## Scale (100,000+ executions)

Measured on a 100,000-run benchmark:

- **Startup:** about 38 s to generate the data and train every model.
- **Pre-computation:** heavy analytics are computed in the background after each dataset load, and are ready about 6 s later.
- **Responses:** every endpoint answers in under 1.5 s, and filtered insights take under 1 s.
- **Memory:** peaks at about 1.2 GB.

What keeps it fast:

- **Data shape:** vectorized Polars/DuckDB, with no row-by-row loops over the dataset.
- **Caching:** results are cached per dataset and per filter, with per-key locks so concurrent requests don't recompute.
- **Model training:** random forests use at most 40k bootstrap rows per tree, and cross-validation uses at most 30k rows.
- **Payloads:** only summaries go to the browser; chart points and profile lists are capped.

## Tests

```powershell
cd backend
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The suite (64 tests) runs against an isolated temporary data folder with no network or Gemini calls. It covers:

- auth and roles
- backward compatibility of every existing endpoint
- the evidence layer, with checks that its numbers match a recomputation from the raw data
- learned thresholds, seed and determinism policies, guardrails and model validation
- upload, mapping, missing values and the leakage guard
- the universal preprocessor: every file type, telemetry-only data, ZIP safety (zip-slip, bombs), and validation messages
- the Copilot pipeline (intents, evidence retrieval, filter injection, grounding check, quota handling) against a mock Gemini server

The frontend is checked with `npx tsc --noEmit` and `npm run build`.

## Deployment (Railway)

The Railway project `silicopulse-backend` has two services, both deployed from this repo with the Railway CLI. The local folder is linked with `railway link`.

| Service | Root | Start | Variables |
|---|---|---|---|
| `silicopulse-backend` | `backend/` | `Procfile`: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | `GEMINI_API_KEY`, `JWT_SECRET`, `CORS_ORIGINS` |
| `silicopulse-frontend` | `frontend/` | `next build` / `next start` | `NEXT_PUBLIC_API_URL` = backend URL |

```powershell
cd backend;  railway up --service silicopulse-backend  --detach
cd frontend; railway up --service silicopulse-frontend --detach
```

Health check: `GET /api/health`, which also reports whether the background pre-computation has finished.

## Security notes

- No secrets in source control: `.env` and `backend/data/` are gitignored, and `config.py` keeps `GEMINI_API_KEY = ""`.
- Set `JWT_SECRET` and `CORS_ORIGINS` in production.
- The Copilot's SQL tool runs read-only `SELECT` statements in a DuckDB connection with external access disabled (no file, network or extension access).
- The demo credentials are for the hackathon only. Replace `_USERS` in `auth.py` with a real identity provider before any real use.

## Synthetic data

`backend/app/generator.py` builds the benchmark around a hidden ground-truth failure model:

- **Deterministic config interactions:**
  - `queue_depth≥128 & write_cache=0`
  - `gc_policy=aggressive & block_size≤8K`
  - `zstd & threads≥32`
- **Stochastic drivers:**
  - a thermal ramp above 60 °C
  - timing jitter above 22 µs
  - 6 "unlucky" firmware seeds under load
- **Weak effects:** low over-provisioning and ECC strength.

The analytics recover these without being told about them. For example, the learned thresholds come out near 64 °C and 22.6 µs, and the flagged seeds are a subset of `hidden_unlucky_seeds` in `data/meta.json`. None of these values appear anywhere in the analytics code.
