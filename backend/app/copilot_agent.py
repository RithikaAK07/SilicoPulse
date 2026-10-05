"""SilicoPulse AI Copilot: a Gemini function-calling agent over the live analytics engine.

Gemini reads the conversation, decides which analytics tools to call (feature importance,
root causes, seed analysis, config comparison, run diffs, predictions, recommendations,
read-only SQL, ...), receives the real results, and streams a grounded answer back.
Charts and follow-up suggestions are also chosen by the model via tools.

The endpoint streams Server-Sent Events:
  status | tool | tool_done | text (delta) | chart | suggestions | done | error
The copilot is fully AI-driven: there are no canned answers. If Gemini is rate-limited the
turn waits for Google's retryDelay and retries automatically; if it stays unreachable the
user gets an explicit error event with a suggested retry time.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import AsyncIterator

import duckdb
import httpx
import numpy as np
import polars as pl
from fastapi.concurrency import run_in_threadpool

from . import analytics, gemini_pool, ml
from .config import gemini_key
from .store import store

STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
MAX_ROUNDS = 6
HISTORY_TURNS = 12
REQUEST_TIMEOUT = 90.0
MAX_RATE_LIMIT_WAIT = 45.0  # total seconds a turn will wait out 429s before giving up
UI_TOOLS = {"render_chart", "suggest_follow_ups"}


# --------------------------------------------------------------------------- helpers
def _clean(obj, depth: int = 0):
    """Make tool results JSON-safe and compact (round floats, cap list/str sizes)."""
    if isinstance(obj, dict):
        return {str(k): _clean(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(obj)[:60]]
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else round(obj, 4)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return _clean(float(obj))
    if isinstance(obj, str):
        return obj if len(obj) <= 600 else obj[:600] + "…"
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    return str(obj)


def _key_names() -> list[str]:
    return [p["name"] for p in store.meta["key_params"]]


def _coerce(param: str, value):
    if param in store.bundle.pre_enc.cat_maps:
        return str(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


# --------------------------------------------------------------------------- tools
def t_overview(environment: str | None = None, hardware: str | None = None, workload: str | None = None) -> dict:
    f = {"environment": environment, "hardware": hardware, "workload": workload}
    ov = analytics.overview(f)
    w, p = analytics.where_clause(f)
    weekly = store.sql(f"""SELECT strftime(date_trunc('week', timestamp), '%Y-%m-%d') AS week, count(*) AS runs, avg(failed) AS fail_rate
                           FROM runs {w} GROUP BY 1 ORDER BY 1""", p)
    return {"dataset": store.dataset_status(), "filters": {k: v for k, v in f.items() if v}, "kpis": ov["kpis"],
            "failures_by_signature": ov["signatures"], "by_hardware": ov["by_hardware"], "weekly_trend": weekly,
            "filter_values": analytics.filter_options()}


def t_feature_importance(top_n: int = 15) -> dict:
    d = analytics.discovery()
    return {"model_auc": d["model_auc"], "method": "0.6 x RandomForest importance + 0.4 x mutual information; correlation sign = direction",
            "ranking": d["importance"][: max(1, min(int(top_n), 25))]}


def t_top_configurations(top_n: int = 10) -> dict:
    d = analytics.discovery()
    pareto = [p for p in d["pareto"] if p["pareto"]]
    return {"ranked_by": "throughput x (1-failure_rate)^2 x stability", "top_configurations": d["top_configs"][: max(1, min(int(top_n), 25))],
            "pareto_frontier": pareto, "profiles_analysed": len(d["pareto"])}


def t_parameter_pairs() -> dict:
    return analytics.discovery()["pairs"]


def t_randomization() -> dict:
    r = analytics.randomization()
    return {"has_seed_column": r["has_seed"], "impact_ranking": r["sensitivity"][:12],
            "riskiest_seeds": r["seeds"][:10], "anomalous_seed_count": sum(1 for s in r["seeds"] if s["flag"]),
            "sensitivity_curves": r["curves"], "drift": {k: v for k, v in analytics.drift().items() if k in ("variables", "crossings")}}


def t_determinism() -> dict:
    d = analytics.determinism()
    return {"definition": "deterministic = configuration profile fails >=80% of runs on any seed; stochastic = depends on seed/environment",
            "failure_classes": d["failure_classes"], "profile_classes": d["profile_classes"], "by_signature": d["by_signature"],
            "failure_clusters": d["clusters"]["summary"]}


def t_root_causes() -> dict:
    rc = analytics.root_cause()
    return {"fingerprints": rc["fingerprints"], "log_anomaly_templates": rc["log_anomalies"][:10]}


def t_config_profile(config_id: str) -> dict:
    prof = analytics._profile_stats()
    row = prof.filter(pl.col("config_id") == config_id.strip().upper())
    if not len(row):
        examples = prof.sort("runs", descending=True).head(8)["config_id"].to_list()
        return {"error": f"config '{config_id}' not found (profiles need >=5 runs)", "example_config_ids": examples}
    r = row.row(0, named=True)
    shap = store.bundle.shap({k: r[k] for k in _key_names()}, top=10)
    return {"profile": r, "shap_failure_log_odds": shap}


def t_compare_configs(config_a: str, config_b: str) -> dict:
    a, b = t_config_profile(config_a), t_config_profile(config_b)
    if "error" in a or "error" in b:
        return {"error": a.get("error") or b.get("error"), "example_config_ids": a.get("example_config_ids") or b.get("example_config_ids")}
    pa, pb = a["profile"], b["profile"]
    keys = _key_names()
    sa = {s["feature"]: s["contribution"] for s in store.bundle.shap({k: pa[k] for k in keys}, top=200)}
    sb = {s["feature"]: s["contribution"] for s in store.bundle.shap({k: pb[k] for k in keys}, top=200)}
    diffs = sorted(({"parameter": k, "a": pa[k], "b": pb[k], "shap_delta_a_minus_b": round(sa.get(k, 0) - sb.get(k, 0), 4)}
                    for k in keys if pa[k] != pb[k]), key=lambda x: -abs(x["shap_delta_a_minus_b"]))
    stats = ("runs", "fail_rate", "throughput", "instability", "p99", "distinct_seeds")
    return {"a": {k: pa[k] for k in ("config_id", *stats)}, "b": {k: pb[k] for k in ("config_id", *stats)},
            "differing_key_parameters": diffs, "note": "positive shap_delta => that setting in A raises failure risk vs B"}


def t_diff_runs(run_a: str | None = None, run_b: str | None = None, signature: str | None = None) -> dict:
    if not run_a or not run_b:
        pair = analytics.suggest_pair(signature)
        run_a, run_b = run_a or pair["run_a"], run_b or pair["run_b"]
    try:
        d = analytics.diff(run_a.upper(), run_b.upper())
    except KeyError:
        return {"error": "run not found", "example_failing_runs": [r["run_id"] for r in analytics.list_runs("fail", None, None, 5)]}
    return {"run_a": d["run_a"], "run_b": d["run_b"], "predicted_pre_execution_risk": d["predicted_risk"], "summary": d["summary"],
            "changed_config": [r for r in d["config"] if r["changed"]][:15], "changed_random": [r for r in d["random"] if r["changed"]][:10],
            "context": d["context"], "telemetry": d["telemetry"], "change_impact_shap": d["change_impact"][:8],
            "differing_log_lines": [l for l in d["logs"] if l["op"] != "equal"][:20]}


def t_list_runs(outcome: str | None = None, signature: str | None = None, limit: int = 10) -> dict:
    return {"runs": analytics.list_runs(outcome, signature, None, max(1, min(int(limit), 25)))}


def t_predict(parameters: list[dict] | None = None, environment: str | None = None, hardware: str | None = None, workload: str | None = None) -> dict:
    cfg, ignored = {}, []
    known = set(store.bundle.pre_enc.columns)
    for item in parameters or []:
        name, value = str(item.get("name", "")).strip(), item.get("value")
        (cfg.__setitem__(name, _coerce(name, value)) if name in known else ignored.append(name))
    for k, v in (("environment", environment), ("hardware", hardware), ("workload", workload)):
        if v:
            cfg[k] = v
    p = store.bundle.predict([cfg])
    risk = float(0.6 * p["risk"][0] + 0.4 * p["risk_rf"][0])
    return {"applied_parameters": cfg, "ignored_unknown_parameters": ignored, "unspecified_parameters": "set to dataset mode",
            "failure_risk": round(risk, 4), "risk_lightgbm": round(float(p["risk"][0]), 4), "risk_random_forest": round(float(p["risk_rf"][0]), 4),
            "expected_throughput_if_pass": round(float(p["throughput"][0]), 1), "fleet_baseline_failure_rate": round(float(store.df["failed"].mean()), 4),
            "shap_failure_log_odds": store.bundle.shap(cfg, top=8)}


def t_recommend(environment: str | None = None, hardware: str | None = None, workload: str | None = None, max_risk: float = 0.05) -> dict:
    ctx = {k: v for k, v in (("environment", environment), ("hardware", hardware), ("workload", workload)) if v}
    max_risk = float(min(max(max_risk or 0.05, 0.01), 0.5))
    key = f"recommend::{sorted(ctx.items())}::{max_risk}"
    recs = store.cached(key, lambda: ml.recommend(store.bundle, ctx, max_risk=max_risk))
    best = recs[0]
    return {"context": ctx, "risk_ceiling": max_risk, "search": "4,000 sampled configurations + greedy flag refinement",
            "recommendations": recs, "shap_for_best": store.bundle.shap({**best["context"], **best["config"]}, top=10)}


_SQL_OK = re.compile(r"^\s*(select|with)\b", re.I)


def t_query_data(sql: str) -> dict:
    """Read-only SQL over table `runs` in a sandboxed DuckDB connection (no file / network access)."""
    q = sql.strip().rstrip(";").strip()
    if ";" in q or not _SQL_OK.match(q):
        return {"error": "Only a single read-only SELECT/WITH statement over table `runs` is allowed."}
    con = duckdb.connect(config={"enable_external_access": False})
    try:
        con.register("runs", store.arrow)
        rel = con.execute(f"SELECT * FROM ({q}) AS sub LIMIT 50")
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, r)) for r in rel.fetchall()]
        return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated_to": 50}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    finally:
        con.close()


def t_render_chart(title: str, labels: list, series: list[dict], chart_type: str = "bar", y_label: str | None = None) -> dict:
    return {"status": "rendered", "points": len(labels)}


def t_suggest(questions: list[str]) -> dict:
    return {"status": "shown"}


TOOLS = {
    "get_dataset_overview": (t_overview, "Reading dataset overview"),
    "get_feature_importance": (t_feature_importance, "Ranking influential settings"),
    "get_top_configurations": (t_top_configurations, "Finding Pareto-optimal configurations"),
    "get_parameter_pairs": (t_parameter_pairs, "Mining parameter interactions"),
    "get_randomization_analysis": (t_randomization, "Analyzing seeds & randomization"),
    "get_determinism_analysis": (t_determinism, "Classifying deterministic vs stochastic failures"),
    "get_root_causes": (t_root_causes, "Fingerprinting root causes"),
    "get_config_profile": (t_config_profile, "Inspecting configuration profile"),
    "compare_configurations": (t_compare_configs, "Comparing configurations"),
    "diff_runs": (t_diff_runs, "Diffing executions"),
    "list_runs": (t_list_runs, "Listing executions"),
    "predict_configuration": (t_predict, "Predicting failure risk"),
    "recommend_configuration": (t_recommend, "Optimizing next-run configuration"),
    "query_data": (t_query_data, "Running SQL on execution data"),
    "render_chart": (t_render_chart, "Drawing chart"),
    "suggest_follow_ups": (t_suggest, "Preparing follow-ups"),
}

S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
CTX_PROPS = {"environment": S, "hardware": S, "workload": S}
DECLARATIONS = [
    {"name": "get_dataset_overview", "description": "KPIs (executions, pass rate, throughput, high-risk configs, instability), failures by error signature, failure rate by hardware and a weekly trend. Optional filters.",
     "parameters": {"type": "object", "properties": CTX_PROPS}},
    {"name": "get_feature_importance", "description": "Q1: which configuration parameters most influence pass/fail (RF importance, mutual information, correlation).",
     "parameters": {"type": "object", "properties": {"top_n": I}}},
    {"name": "get_top_configurations", "description": "Q2: best-performing configuration profiles and the Pareto frontier of throughput vs failure rate.",
     "parameters": {"type": "object", "properties": {"top_n": I}}},
    {"name": "get_parameter_pairs", "description": "Q2: best (high throughput, reliable) and most toxic (highest failure rate) two-parameter setting combinations.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_randomization_analysis", "description": "Q3: impact ranking of randomized variables, sensitivity curves, riskiest seeds (observed vs expected failure, z-score) and drift threshold crossings.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_determinism_analysis", "description": "Q4: deterministic vs stochastic failure split overall and per error signature, plus K-Means failure clusters.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_root_causes", "description": "Q5: failure fingerprints per error signature with precursor conditions (lift/support), deterministic share and log anomaly templates.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_config_profile", "description": "Stats and key settings of one configuration profile (e.g. CFG-0042) with SHAP failure drivers.",
     "parameters": {"type": "object", "properties": {"config_id": S}, "required": ["config_id"]}},
    {"name": "compare_configurations", "description": "Compare two configuration profiles: failure rate, throughput, differing key settings and their SHAP risk deltas. Use for 'why did A fail more than B'.",
     "parameters": {"type": "object", "properties": {"config_a": S, "config_b": S}, "required": ["config_a", "config_b"]}},
    {"name": "diff_runs", "description": "Q6: side-by-side diff of a passing run (run_a) and failing run (run_b): changed settings, randomized variables, telemetry, SHAP change impact and differing log lines. Omit run ids to auto-pick a nearest pass/fail pair (optionally for a signature).",
     "parameters": {"type": "object", "properties": {"run_a": S, "run_b": S, "signature": S}}},
    {"name": "list_runs", "description": "List example executions, optionally filtered by outcome ('pass'/'fail') or error signature.",
     "parameters": {"type": "object", "properties": {"outcome": S, "signature": S, "limit": I}}},
    {"name": "predict_configuration", "description": "Q7: predict failure probability and expected throughput for a configuration before running it. Unspecified parameters default to the dataset mode.",
     "parameters": {"type": "object", "properties": {
         "parameters": {"type": "array", "items": {"type": "object", "properties": {"name": S, "value": S}, "required": ["name", "value"]}},
         **CTX_PROPS}}},
    {"name": "recommend_configuration", "description": "Q8: recommend the optimal configuration for the next run (max throughput under a failure-risk ceiling) with confidence and SHAP explanation.",
     "parameters": {"type": "object", "properties": {**CTX_PROPS, "max_risk": N}}},
    {"name": "query_data", "description": "Run ONE read-only DuckDB SQL SELECT over table `runs` (one row per execution) for any custom question the other tools don't cover. Max 50 rows returned; aggregate where possible.",
     "parameters": {"type": "object", "properties": {"sql": S}, "required": ["sql"]}},
    {"name": "render_chart", "description": "Show a chart to the user under your answer. Use when a visual clarifies a comparison, ranking or trend. Values must come from tool results.",
     "parameters": {"type": "object", "properties": {
         "title": S, "chart_type": {"type": "string", "enum": ["bar", "horizontal_bar", "line"]},
         "labels": {"type": "array", "items": S},
         "series": {"type": "array", "items": {"type": "object", "properties": {"name": S, "values": {"type": "array", "items": N}}, "required": ["name", "values"]}},
         "y_label": S}, "required": ["title", "labels", "series"]}},
    {"name": "suggest_follow_ups", "description": "Offer 2-3 short, specific follow-up questions the user could click next.",
     "parameters": {"type": "object", "properties": {"questions": {"type": "array", "items": S}}, "required": ["questions"]}},
]


def _snapshot() -> dict:
    """Pre-computed key analytics of the active dataset, embedded in the system prompt."""
    def build():
        ov = analytics.overview({})
        d = analytics.discovery()
        r = analytics.randomization()
        rc = analytics.root_cause()
        det = analytics.determinism()
        return _clean({
            "kpis": ov["kpis"],
            "risk_model_auc": d["model_auc"],
            "top_influential_settings": [{k: x[k] for k in ("feature", "score", "correlation")} for x in d["importance"][:8]],
            "toxic_pairs": [{k: x[k] for k in ("a", "a_val", "b", "b_val", "fail_rate", "runs")} for x in d["pairs"]["worst"][:5]],
            "best_pairs": [{k: x[k] for k in ("a", "a_val", "b", "b_val", "fail_rate", "throughput")} for x in d["pairs"]["best"][:3]],
            "top_configs": [{k: x[k] for k in ("config_id", "runs", "fail_rate", "throughput", "pareto")} for x in d["top_configs"][:5]],
            "failure_signatures": [{"signature": f["signature"], "share": f["share"], "deterministic_share": f["deterministic_share"],
                                    "top_conditions": [c["condition"] for c in f["top_conditions"][:2]]} for f in rc["fingerprints"][:7]],
            "failure_classes": det["failure_classes"],
            "random_variable_impact": [{k: x[k] for k in ("variable", "impact_score", "fail_rate_spread")} for x in r["sensitivity"][:6]],
            "anomalous_seeds": [{k: x[k] for k in ("seed", "observed", "expected", "z")} for x in r["seeds"] if x["flag"]][:6],
            "failures_by_hardware": ov["by_hardware"],
        })
    return store.cached("copilot_snapshot", build)


def _system_prompt() -> str:
    m, df = store.meta, store.df
    ds = store.dataset_status()
    keys = [f"{p['name']} ({p['kind']}: {', '.join(map(str, p['choices'][:10]))}{'…' if len(p['choices']) > 10 else ''})" for p in m["key_params"]]
    rv = m["random_vars"]
    cols = ", ".join(f"{c}:{str(df[c].dtype).lower()}" for c in df.columns if c != "log_trace")
    return f"""You are **SilicoPulse AI**, the copilot inside SilicoPulse, an AI-Powered Silicon Validation & Configuration Intelligence Platform used by SanDisk hardware validation teams.
You chat with validation engineers and executives about their test campaign: configuration influence, Pareto-optimal configs, randomization/seed effects, deterministic vs stochastic failures, root causes, run diffs, failure-risk prediction and next-run recommendations.

How to behave:
- Read the user's message carefully and respond to what they actually asked, in a natural conversational tone. Greetings or small talk get a short friendly reply (no tools).
- Ground every number in data: use the ANALYTICS SNAPSHOT below when it already answers the question (then answer directly, no data tools needed); otherwise call tools. Never invent numbers, config IDs, run IDs or seeds. If a tool returns an error, fix the arguments (e.g. use an example ID it returns) or explain.
- You may call several tools, chain them, and use `query_data` (DuckDB SQL over table `runs`) for custom questions.
- Keep data-backed findings separate from engineering interpretation: only state a cause as fact if the tools/snapshot support it (e.g. lift, SHAP, signature conditions); phrase other explanations as hypotheses ("likely", "worth checking").
- If the request is ambiguous, either make a sensible assumption and state it, or ask one short clarifying question.
- Use `render_chart` when a visual helps (rankings, comparisons, trends): keep it to <=15 labels. Use `suggest_follow_ups` after substantive answers.
- IMPORTANT ordering: once you have the data you need, write your COMPLETE answer text first, then call `render_chart` / `suggest_follow_ups` at the very end of that same response. Never call them before the answer text.
- Earlier answers note visuals as "[Displayed chart(s): …]": those are already on screen, so never re-render them unless the user asks; only chart NEW data for the current question.
- Answer in GitHub Markdown: lead with the direct answer, then evidence (bold key numbers, small tables when useful), then explicit trade-offs and actionable next steps when relevant. Be concise: no filler, no repeating the question.
- Percentages: tool rates are fractions (0.26 = 26%).

Active dataset: {ds['label']}{f" ({ds['filename']})" if ds.get('filename') else ''}: {len(df):,} executions, overall failure rate {df['failed'].mean():.1%}, {m['n_profiles']} configuration profiles.
Key configuration parameters (most influential first): {'; '.join(keys)}.
Other config parameters: {len(m['config_params']) - len(keys)} more (e.g. {', '.join(m['config_params'][len(keys):len(keys) + 6])}).
Randomized variables ({len(rv)}): {', '.join(rv[:15])}{'…' if len(rv) > 15 else ''}.
Context values: environment={analytics.filter_options()['environment']}, hardware={analytics.filter_options()['hardware']}, workload={analytics.filter_options()['workload']}.
Columns not present in this dataset (filled with neutral defaults, don't analyse them): {ds['synthetic_columns'] or 'none'}.
SQL table `runs` columns: {cols}. `failed` is 0/1, `outcome` is 'pass'/'fail', `error_signature` is 'NONE' for passing runs.

ANALYTICS SNAPSHOT (pre-computed on the active dataset; fractions, 0.26 = 26%):
{json.dumps(_snapshot(), separators=(",", ":"), default=str)}"""


def _history(messages: list[dict]) -> list[dict]:
    contents = []
    for msg in messages[-HISTORY_TURNS:]:
        text = str(msg.get("content", "")).strip()[:6000]
        if not text:
            continue
        role = "model" if msg.get("role") == "assistant" else "user"
        if contents and contents[-1]["role"] == role:  # Gemini requires alternating turns
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


def _event(**kw) -> str:
    return f"data: {json.dumps(kw, default=str)}\n\n"


def _unavailable(reason: str, retry_after: float | None = None) -> str:
    return _event(type="error", message=reason, retry_after=None if retry_after is None else int(math.ceil(retry_after)))


SKIP_SIGNATURE = "skip_thought_signature_validator"  # documented marker for function calls from another model


def _contents_for(model: str, contents: list[dict], authors: dict[int, str]) -> list[dict]:
    """Thought signatures are model-specific: if a turn switches model mid-way, re-sign earlier calls."""
    if all(a == model for a in authors.values()):
        return contents
    out = []
    for i, c in enumerate(contents):
        if i in authors and authors[i] != model:
            c = {"role": c["role"], "parts": [({**p, "thoughtSignature": SKIP_SIGNATURE} if "functionCall" in p else
                                               {k: v for k, v in p.items() if k != "thoughtSignature"}) for p in c["parts"]]}
        out.append(c)
    return out


async def chat_stream(messages: list[dict]) -> AsyncIterator[str]:
    key = gemini_key()
    if not key:
        yield _unavailable("No Gemini API key is configured on the server (backend/app/config.py).")
        return

    contents = _history(messages)
    system = await run_in_threadpool(_system_prompt)
    body_base = {
        "systemInstruction": {"parts": [{"text": system}]},
        "tools": [{"functionDeclarations": DECLARATIONS}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4096, "thinkingConfig": {"thinkingLevel": "low"}},
    }
    yield _event(type="status", text="Understanding your question…")
    used_model, tools_used, wrote_text = None, [], False
    authors: dict[int, str] = {}  # index in `contents` -> model that produced that model turn
    waited, rounds = 0.0, 0

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        while rounds < MAX_ROUNDS:
            # prefer the model already used in this turn, then the pool order
            pool = gemini_pool.available_models()
            if used_model in pool:
                pool = [used_model] + [m for m in pool if m != used_model]
            parts, errors, ok, round_text = [], [], False, False
            for model in pool:
                body = {**body_base, "contents": _contents_for(model, contents, authors)}
                if model.endswith("lite") or "lite-" in model:
                    body["generationConfig"] = {k: v for k, v in body_base["generationConfig"].items() if k != "thinkingConfig"}
                try:
                    async with client.stream("POST", STREAM_URL.format(model=model), headers={"x-goog-api-key": key}, json=body) as r:
                        if r.status_code != 200:
                            raw = await r.aread()
                            gemini_pool.note_failure(model, r.status_code, raw)
                            errors.append(f"{model} HTTP {r.status_code}")
                            if r.status_code in (401, 403):
                                break  # key problem: other models won't help
                            if r.status_code == 400:
                                errors[-1] += f": {raw[:160].decode(errors='ignore')}"
                            continue
                        async for line in r.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            chunk = json.loads(line[6:])
                            for p in (chunk.get("candidates") or [{}])[0].get("content", {}).get("parts", []):
                                parts.append(p)
                                if p.get("text") and not p.get("thought"):
                                    wrote_text = round_text = True
                                    yield _event(type="text", delta=p["text"])
                    ok, used_model = True, model
                    break
                except (httpx.HTTPError, json.JSONDecodeError) as e:
                    gemini_pool.block(model, 15)
                    errors.append(f"{model} {type(e).__name__}")
                    if parts:  # stream broke mid-answer: keep what we have rather than replaying
                        ok, used_model = True, model
                        break

            if not ok:
                wait = gemini_pool.next_available_in()
                if 0 < wait and waited + wait <= MAX_RATE_LIMIT_WAIT:
                    yield _event(type="status", text=f"Gemini free-tier rate limit reached, retrying in {int(wait) + 1}s…")
                    await asyncio.sleep(wait + 1)
                    waited += wait + 1
                    continue
                if any(e.endswith(("HTTP 401", "HTTP 403")) for e in errors):
                    yield _unavailable("Gemini rejected the API key (HTTP 401/403). Check GEMINI_API_KEY in backend/app/config.py.")
                elif wait > 0 or (errors and all("HTTP 429" in e for e in errors)):
                    yield _unavailable("All Gemini models in the pool have used up their free-tier quota.", wait or None)
                else:
                    yield _unavailable(f"Gemini is temporarily unavailable ({'; '.join(errors) or 'no models available'}).", 10)
                if wrote_text:
                    yield _event(type="done", source=used_model or "gemini", tools=tools_used, partial=True)
                return
            rounds += 1

            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                if not wrote_text:
                    yield _event(type="text", delta="I couldn't produce an answer for that; could you rephrase or add detail?")
                yield _event(type="done", source=used_model, tools=tools_used)
                return

            if round_text and all(c.get("name") in UI_TOOLS for c in calls):
                # answer already written; chart / follow-ups are display-only, so skip another model round-trip
                for call in calls:
                    args = call.get("args") or {}
                    if call.get("name") == "render_chart":
                        yield _event(type="chart", chart=_clean(args))
                    else:
                        yield _event(type="suggestions", items=[str(q) for q in (args.get("questions") or [])][:3])
                    tools_used.append(call.get("name"))
                yield _event(type="done", source=used_model, tools=tools_used)
                return

            authors[len(contents)] = used_model
            contents.append({"role": "model", "parts": parts})  # keep thought signatures intact
            responses = []
            for call in calls:
                name, args = call.get("name", ""), call.get("args") or {}
                fn, label = TOOLS.get(name, (None, name))
                yield _event(type="tool", name=name, label=label, args=_clean(args))
                if name == "render_chart":
                    yield _event(type="chart", chart=_clean(args))
                elif name == "suggest_follow_ups":
                    yield _event(type="suggestions", items=[str(q) for q in (args.get("questions") or [])][:3])
                try:
                    result = await run_in_threadpool(fn, **args) if fn else {"error": f"unknown tool {name}"}
                except TypeError as e:
                    result = {"error": f"bad arguments: {e}"}
                except Exception as e:  # never let one tool failure kill the conversation
                    result = {"error": f"{type(e).__name__}: {e}"}
                tools_used.append(name)
                yield _event(type="tool_done", name=name, ok="error" not in result)
                resp = {"name": name, "response": {"result": _clean(result)}}
                if call.get("id"):
                    resp["id"] = call["id"]
                responses.append({"functionResponse": resp})
            contents.append({"role": "user", "parts": responses})
            yield _event(type="status", text="Writing the answer…")

    yield _event(type="text", delta="\n\n_(Stopped after the maximum number of analysis steps.)_")
    yield _event(type="done", source=used_model, tools=tools_used)
