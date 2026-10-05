"""FastAPI entrypoint for SilicoPulse."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import ai, analytics, ml
from . import auth, copilot_agent, evidence, insights, upload_handler
from .auth import get_current_user, require_permission
from .config import CORS_ORIGINS, gemini_key
from .store import store


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.load_or_generate()
    yield


app = FastAPI(
    title="SilicoPulse Analytics API",
    description="AI-Powered Silicon Validation & Configuration Intelligence Platform",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# Every analytics/ML/AI route requires a valid JWT; /api/health and /api/login stay public.
api = APIRouter(dependencies=[Depends(get_current_user)])


class GenerateReq(BaseModel):
    n_runs: int = Field(10_000, ge=500, le=100_000)
    n_config: int = Field(100, ge=13, le=400)
    n_random: int = Field(51, ge=6, le=200)
    seed: int = 42


class PredictReq(BaseModel):
    config: dict = {}
    context: dict = {}


class RecommendReq(BaseModel):
    context: dict = {}
    max_risk: float = Field(0.05, gt=0, lt=1)


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=20000)


class ChatReq(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=60)
    filters: dict = Field(default_factory=dict)  # active dashboard filters (hardware, environment, workload, dates, ...)


class CopilotReq(BaseModel):
    query: str = Field(..., min_length=2, max_length=2000)


def _filters(environment=None, hardware=None, workload=None, date_from=None, date_to=None) -> dict:
    return {"environment": environment, "hardware": hardware, "workload": workload, "date_from": date_from, "date_to": date_to}


@app.get("/api/health")
def health():
    return {"status": "ok", "runs": 0 if store.df is None else len(store.df), "analytics_cache": store.warm_status}


@api.get("/api/meta")
def meta():
    b = store.bundle
    m = store.meta
    return {
        "n_runs": len(store.df), "n_config_params": len(m["config_params"]), "n_random_vars": len(m["random_vars"]),
        "n_profiles": m["n_profiles"], "log_lines": m["log_lines"], "generated_at": m["generated_at"],
        "key_params": m["key_params"], "sandbox_params": m["sandbox_params"],
        "choices": {k: b.choices[k] for k in m["sandbox_params"] + m["context"]}, "defaults": {k: b.defaults[k] for k in m["sandbox_params"] + m["context"]},
        "model": {"engine": "LightGBM + RandomForest" if ml.HAS_LGB else "HistGradientBoosting + RandomForest", "auc": b.auc, "train_seconds": round(store.train_seconds, 2)},
        "ai": {"gemini_configured": bool(gemini_key())},
        "dataset": store.dataset_status(),
        "filters": analytics.filter_options(),
    }


@api.post("/api/dataset/generate", dependencies=[Depends(require_permission("generate"))])
def generate(req: GenerateReq):
    timing = store.regenerate(req.n_runs, req.n_config, req.n_random, req.seed)
    return {**timing, **meta()}


@api.get("/api/dataset/sample")
def sample(limit: int = 200):
    return store.df.drop("log_trace").head(limit).to_dicts()


@api.get("/api/overview")
def overview(environment: str | None = None, hardware: str | None = None, workload: str | None = None, date_from: str | None = None, date_to: str | None = None):
    return analytics.overview(_filters(environment, hardware, workload, date_from, date_to))


@api.get("/api/summary")
async def summary():
    return await ai.executive_summary()


@api.get("/api/discovery")
def discovery():
    return analytics.discovery()


@api.get("/api/randomization")
def randomization():
    return analytics.randomization()


@api.get("/api/drift")
def drift():
    return store.cached("drift", analytics.drift)


@api.get("/api/rootcause")
def rootcause():
    return analytics.root_cause()


@api.get("/api/runs")
def runs(outcome: str | None = None, signature: str | None = None, q: str | None = None, limit: int = 60):
    return analytics.list_runs(outcome, signature, q, limit)


@api.get("/api/diff/suggest")
def diff_suggest(signature: str | None = None):
    return analytics.suggest_pair(signature)


@api.get("/api/diff")
def diff(run_a: str, run_b: str):
    try:
        return analytics.diff(run_a.upper(), run_b.upper())
    except KeyError:
        raise HTTPException(404, "Run not found")


@api.post("/api/predict")
def predict(req: PredictReq):
    cfg = {**req.context, **req.config}
    p = store.bundle.predict([cfg])
    risk = float(0.6 * p["risk"][0] + 0.4 * p["risk_rf"][0])
    return {"failure_risk": round(risk, 4), "risk_gbm": round(float(p["risk"][0]), 4), "risk_rf": round(float(p["risk_rf"][0]), 4),
            "expected_throughput": round(float(p["throughput"][0]), 1), "shap": store.bundle.shap(cfg, top=12),
            "base_rate": round(float(store.df["failed"].mean()), 4),
            "uncertainty": {"model_disagreement": round(abs(float(p["risk"][0]) - float(p["risk_rf"][0])), 4)},
            "model_quality": {"roc_auc": store.bundle.auc, "cv_roc_auc_mean": store.bundle.validation.get("cv", {}).get("roc_auc_mean"),
                              "brier": store.bundle.validation.get("holdout", {}).get("brier")}}


@api.post("/api/recommend")
async def recommend(req: RecommendReq):
    key = f"recommend::{sorted(req.context.items())}::{req.max_risk}"
    recs = await run_in_threadpool(lambda: store.cached(key, lambda: ml.recommend(store.bundle, req.context, max_risk=req.max_risk, df=store.df)))
    best = recs[0]
    shap = store.bundle.shap({**best["context"], **best["config"]}, top=10)
    explanation, source = await ai.explain_recommendation(best, shap)
    return {"recommendations": recs, "shap": shap, "explanation": explanation, "source": source}


# --------------------------------------------------------------------------- evidence layer (new, additive)
def _ev_filters(environment=None, hardware=None, workload=None, date_from=None, date_to=None, signature=None, config_id=None, seed=None) -> dict:
    return evidence.clean_filters({"environment": environment, "hardware": hardware, "workload": workload, "date_from": date_from,
                                   "date_to": date_to, "signature": signature, "config_id": config_id, "seed": seed})


@api.get("/api/insights")
def get_insights(environment: str | None = None, hardware: str | None = None, workload: str | None = None, date_from: str | None = None,
                 date_to: str | None = None, signature: str | None = None, config_id: str | None = None, seed: int | None = None):
    """Structured, evidence-backed insights for the (optionally filtered) active dataset."""
    f = _ev_filters(environment, hardware, workload, date_from, date_to, signature, config_id, seed)
    return insights.insights(f)


@api.get("/api/insights/{section}")
def get_insight_section(section: str, environment: str | None = None, hardware: str | None = None, workload: str | None = None,
                        date_from: str | None = None, date_to: str | None = None, signature: str | None = None,
                        config_id: str | None = None, seed: int | None = None):
    f = _ev_filters(environment, hardware, workload, date_from, date_to, signature, config_id, seed)
    sections = {
        "parameters": lambda: evidence.parameter_risk(f),
        "pairs": lambda: insights.toxic_pairs(f),
        "environment": lambda: {"thresholds": evidence.environment_thresholds(f),
                                "drift": analytics.drift()["crossings"] if not f else None},
        "signatures": lambda: insights.failure_signatures(f),
        "hardware": lambda: insights.hardware_comparison(f),
        "guardrails": lambda: insights.guardrails(f),
        "seeds": lambda: {"seeds": analytics.seed_points(), "policy": analytics.randomization()["seed_policy"]},
        "determinism": lambda: {k: v for k, v in analytics.determinism().items() if k != "clusters"},
    }
    if section not in sections:
        raise HTTPException(404, f"Unknown section. Available: {', '.join(sections)}")
    return sections[section]()


@api.get("/api/data-quality")
def data_quality():
    return insights.data_quality()


@api.get("/api/model/validation")
def model_validation():
    b = store.bundle
    return {"auc": b.auc, **b.validation}


@api.post("/api/copilot/chat")
async def copilot_chat(req: ChatReq):
    """Gemini function-calling agent; streams Server-Sent Events (status/tool/text/chart/suggestions/done)."""
    msgs = [m.model_dump() for m in req.messages]
    if msgs[-1]["role"] != "user" or not msgs[-1]["content"].strip():
        raise HTTPException(422, "The last message must be a non-empty user message")
    return StreamingResponse(copilot_agent.chat_stream(msgs, evidence.clean_filters(req.filters)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.post("/api/copilot")
async def copilot(req: CopilotReq):
    return await ai.copilot(req.query)


app.include_router(auth.router)
app.include_router(upload_handler.router)
app.include_router(api)
