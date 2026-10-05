# SilicoPulse architecture

```
Frontend (Next.js 14)
   │  JWT bearer · TanStack Query · dashboard filters (Zustand)
   ▼
API (FastAPI, backend/app/main.py · auth.py · upload_handler.py)
   │
   ▼
Data layer (store.py)            one active dataset: synthetic benchmark OR uploaded CSV
   │  Polars DataFrame + Arrow → DuckDB (zero-copy) · per-dataset cache with per-key locks
   ▼
Analytics (analytics.py)         Q1–Q6 views: overview, discovery, randomization, drift, root cause, diff
   │
   ▼
Evidence layer (evidence.py · insights.py · stats.py)
   │  filtered frames · learned thresholds · parameter risk · toxic pairs · signature hierarchy
   │  hardware comparison · guardrails · data quality · structured insights
   ▼
ML (ml.py)                       RF (importance), LightGBM + RF (pre-execution risk), LightGBM (throughput)
   │                             validation: hold-out + 5-fold CV, calibration, confusion matrix, global SHAP
   ▼
Copilot (copilot_agent.py · gemini_pool.py)
   │  intent detection → evidence retrieval (filter-aware) → Gemini function calling → evidence cards → grounding check
   ▼
Recommendation (ml.recommend)    candidate search + training-data support + extrapolation label + uncertainty + Pareto + why/trade-offs
```

## Data layer

- **`store.py`:** holds the active dataset. `_activate()` trains the models, then starts a background **warm-up** that pre-computes discovery, randomization, root cause, drift, data quality, insights and guardrails, so the first page loads stay fast at 100K+ runs. `/api/health` reports the warm-up state.
- **`cached(key, fn)`:** memoises per dataset version, with a per-key lock so concurrent requests never compute the same thing twice. Filtered analyses are cached per filter key (`evidence.filter_key`).
- **Uploads:** `upload_handler.py` maps arbitrary CSVs onto the canonical schema (run_id, config_id, failed/outcome, throughput, seed, timestamp, context, error_signature, log_trace, telemetry). Missing canonical fields become neutral defaults and are listed in `meta.synthetic_columns`; the data-quality report marks them as **derived**. Missing values and log coverage are recorded before any filling. Label-leaking columns are dropped.

## Evidence layer

All statistics are computed from counts. The examples below are from the 10K benchmark and are computed at runtime, never hard-coded.

| Component | Method |
|---|---|
| `stats.py` | Wilson CI, two-proportion z, binomial z, odds ratio (Haldane), χ² + Cramér's V, Cohen's d, evidence strength (Bonferroni), severity |
| `evidence.learned_threshold` | Scans the 5th–95th percentile cut points and picks the best-separating one by \|z\|. The direction (above/below) comes from the data. Corrected for the number of cut points × variables. |
| `evidence.parameter_risk` | Per-value failure rate vs all other runs; high-cardinality numerics are bucketed into quintiles |
| `insights.toxic_pairs` | Two-parameter cells with ≥ `MIN_PAIR_SAMPLES` runs: lift, odds ratio, interaction lift, Bonferroni p |
| `insights.failure_signatures` | Signature → associated conditions (lift, support ≥ 30%) → environment (Cohen's d vs passing runs) → seeds (binomial z) → hardware → logs → supporting runs |
| `insights.guardrails` | Candidates from significant pairs, risky values and thresholds; score = lift × log10(n) × evidence weight; each comes with a safer **observed** alternative |
| `insights.insights` | Structured insight objects: `{title, category, severity, finding, evidence{sample_size, failure_rate, baseline_failure_rate, lift, p_value, z_score, ci95, …}, affected_parameters, failure_signatures, supporting_runs, recommendation, explanation, evidence_strength}` |

Language policy: findings say "associated with", "elevated failure risk" or "strong failure predictor". Nothing says "causes", because no causal inference is implemented.

## ML pipeline

| Model | Purpose |
|---|---|
| `full_rf` | Random forest on config + randomization + context features; global importance (Q1, Q3) |
| `risk_model` | LightGBM on pre-execution features only (config + context); failure probability (Q7) and TreeSHAP |
| `risk_rf` | Random forest on the same features; ensemble partner, its disagreement gives uncertainty |
| `tput_model` | LightGBM regressor on passing runs; expected throughput, with R² and MAE reported |

`ml.validate` runs a stratified 80/20 hold-out plus 5-fold stratified CV on the training split. The random forests cap bootstrap rows at 40k, and CV uses at most 30k rows, which keeps 100K-run training at about 30 s.

## Copilot pipeline

1. `detect_intents(question)` classifies the question with keyword rules: parameters, pairs, environment, seeds, determinism, signatures, hardware, recommend, prediction, quality or greeting.
2. `retrieve_evidence(intents, filters)` fetches the pre-computed, filter-aware evidence and emits it as SSE `evidence` cards.
3. The system prompt holds the dataset schema, the active filters, a filter-aware snapshot (insights + KPIs + data-quality warnings), the retrieved evidence and the rules (no self-computed statistics, n and baseline with every rate, associational language, answer structure).
4. Gemini function-calls the tools when it needs more. The evidence tools receive the dashboard filters automatically, and the SQL tool runs in a sandboxed DuckDB.
5. `grounding_check(answer, sources)` traces every figure in the answer to an analytics number and emits it as an SSE `grounding` event.
6. Model rotation, retry-after handling and re-signing calls when switching models mid-turn happen in `gemini_pool.py` / `chat_stream`.

## Frontend

| Area | Pages and components |
|---|---|
| Existing pages (all kept) | Dashboard, Config Discovery, Randomization, Root Cause & Logs, Predict & Prescribe, AI Copilot, CSV Upload, Dataset Generator, Login |
| New page | Evidence & Guardrails (`/insights`) |
| Shared components | `components/evidence.tsx` (severity and evidence badges, evidence chips, insight cards with "Why this insight?", data-quality strip and panel), `components/filter-bar.tsx` (shared by Dashboard, Evidence and Copilot) |

## Deployment

The Railway project `silicopulse-backend` has two services: `silicopulse-backend` (root `backend/`, Procfile) and `silicopulse-frontend` (root `frontend/`, `NEXT_PUBLIC_API_URL` set to the backend URL). Deploy with `railway up --service <name>` from each folder. Secrets live only in the Railway variables.
