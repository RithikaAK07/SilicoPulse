"""GenAI layer: Google Gemini over raw HTTP, with a deterministic local fallback.

Every public function returns usable output even when the Gemini key is the
placeholder, invalid, offline, or rate limited (HTTP 429): the local heuristic
engine composes the same structured Markdown from the analytics facts.
"""
from __future__ import annotations

import json
import re
import time

import httpx
from fastapi.concurrency import run_in_threadpool
import polars as pl

from . import analytics, ml
from . import gemini_pool
from .config import GEMINI_TIMEOUT_S, gemini_key
from .store import store

_cooldown_until = 0.0
_cooldown_reason = ""
SYSTEM = ("You are the AI analyst inside SilicoPulse, an AI-powered silicon validation & configuration intelligence platform for SanDisk hardware test analytics. "
          "Answer ONLY from the JSON facts provided. Be precise, quantitative and concise. Use GitHub Markdown with short "
          "headings, bullet points and bold key numbers. Always end with an 'Actionable next steps' list. Never invent run IDs or numbers.")


async def gemini(prompt: str, facts: dict, max_tokens: int = 900) -> tuple[str | None, str]:
    """Return (text, source). text is None when the caller must fall back."""
    global _cooldown_until, _cooldown_reason
    key = gemini_key()
    if not key:
        return None, "local-heuristic (no Gemini key)"
    if time.time() < _cooldown_until:
        return None, f"local-heuristic ({_cooldown_reason})"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": f"{prompt}\n\nFACTS (JSON):\n{json.dumps(facts, default=str)[:24000]}"}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens + 3000},  # headroom for model "thinking"
    }
    errors = []
    deadline = time.monotonic() + GEMINI_TIMEOUT_S  # one budget for the whole fallback chain
    async with httpx.AsyncClient() as client:
        for model in gemini_pool.available_models():
            remaining = deadline - time.monotonic()
            if remaining < 2:
                errors.append("timeout")
                break
            try:
                r = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                      headers={"x-goog-api-key": key}, json=body, timeout=remaining)
                if r.status_code != 200:
                    gemini_pool.note_failure(model, r.status_code, r.content)
                if r.status_code == 429:
                    errors.append(f"{model} HTTP 429")
                    continue  # per-model quota: try the next model in the pool
                if r.status_code in (401, 403):  # bad key / project denied: don't retry for 5 minutes
                    _cooldown_until, _cooldown_reason = time.time() + 300, f"Gemini HTTP {r.status_code}: access denied"
                    return None, f"local-heuristic (Gemini HTTP {r.status_code})"
                if r.status_code != 200:
                    errors.append(f"{model} HTTP {r.status_code}")
                    continue
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                if text:
                    return text, model
                errors.append(f"{model} empty")
            except Exception as e:  # network / parsing errors -> try next model, then fall back
                errors.append(f"{model} {type(e).__name__}")
    if not errors and not gemini_pool.available_models():
        errors.append("all models rate-limited")
    return None, f"local-heuristic (Gemini unavailable: {'; '.join(errors) or 'error'})"


def _pct(x) -> str:
    return f"{float(x) * 100:.1f}%"


# --------------------------------------------------------------------------- executive summary
def _summary_facts() -> dict:
    ov = analytics.overview({})
    disc = analytics.discovery()
    rc = analytics.root_cause()
    rnd = analytics.randomization()
    det = rnd["determinism"]
    return {
        "kpis": ov["kpis"], "log_lines_processed": store.meta.get("log_lines"),
        "top_influencers": [{k: r[k] for k in ("feature", "score", "correlation")} for r in disc["importance"][:6]],
        "pareto_configs": [p for p in disc["pareto"] if p["pareto"]][:6],
        "worst_pairs": disc["pairs"]["worst"][:4], "best_pairs": disc["pairs"]["best"][:4],
        "random_impact": [{k: r[k] for k in ("variable", "impact_score", "fail_rate_spread")} for r in rnd["sensitivity"][:5]],
        "anomalous_seeds": [s for s in rnd["seeds"] if s["flag"]][:8],
        "failure_classes": det["failure_classes"],
        "fingerprints": [{k: f[k] for k in ("signature", "count", "share", "deterministic_share", "top_conditions")} for f in rc["fingerprints"]],
        "model_auc": store.bundle.auc,
    }


def _local_summary(f: dict) -> str:
    k = f["kpis"]
    classes = {c["class"]: c["count"] for c in f["failure_classes"]}
    det, sto = classes.get("deterministic", 0), sum(v for c, v in classes.items() if c != "deterministic")
    fp = f["fingerprints"]
    lines = [
        "## Executive Summary",
        f"Analyzed **{k['total_executions']:,} executions** ({f['log_lines_processed'] or 0:,} log lines) across **{k['unique_configs']} configuration profiles** "
        f"and **{k['unique_seeds']} random seeds**. Pass rate is **{_pct(k['pass_rate'])}** with mean healthy throughput of **{k['mean_throughput']:,} MB/s**; "
        f"**{k['high_risk_configs']} profiles** fail more than 30% of the time.",
        "",
        "### Key findings",
        f"- **Top failure drivers (Q1):** " + ", ".join(f"`{r['feature']}`" for r in f["top_influencers"][:4]) + ".",
    ]
    if f["worst_pairs"]:
        w = f["worst_pairs"][0]
        lines.append(f"- **Most toxic interaction (Q2):** `{w['a']}={w['a_val']}` × `{w['b']}={w['b_val']}` fails **{_pct(w['fail_rate'])}** of runs ({w['lift']}× baseline).")
    if f["random_impact"]:
        r = f["random_impact"][0]
        lines.append(f"- **Most impactful randomization (Q3):** `{r['variable']}` swings failure rate by **{_pct(r['fail_rate_spread'])}** across its range.")
    lines.append(f"- **Determinism (Q4):** {det:,} failures are **deterministic** (config-driven, reproduce on any seed) vs {sto:,} **stochastic** (seed/thermal/jitter-driven).")
    if f["anomalous_seeds"]:
        lines.append(f"- **Anomalous seeds:** " + ", ".join(f"`{s['seed']}` ({s['lift']}×)" for s in f["anomalous_seeds"][:5]) + " fail far above model expectation.")
    if fp:
        top = fp[0]
        cond = top["top_conditions"][0]["condition"] if top["top_conditions"] else "n/a"
        lines.append(f"- **Dominant root cause (Q5):** `{top['signature']}` accounts for **{_pct(top['share'])}** of failures; strongest precursor: `{cond}`.")
    lines.append(f"- **Predictive model (Q7):** pre-execution failure-risk model AUC = **{f['model_auc']:.3f}**.")
    lines += ["", "### Actionable next steps",
              "1. Block deterministic-failure configurations in CI pre-flight using the risk model (threshold 30%).",
              "2. Quarantine anomalous seeds and re-run them under controlled thermal conditions to confirm firmware defects.",
              "3. Adopt the Pareto-optimal recommended configuration from the Predictive & Prescriptive tab for the next campaign."]
    return "\n".join(lines)


async def executive_summary() -> dict:
    facts = await run_in_threadpool(_summary_facts)
    text, source = await gemini("Write a presentation-ready executive summary (max 250 words) of this storage test campaign. "
                                "Cover pass rate, top failure drivers, deterministic vs stochastic failures, dominant root cause and recommendations.", facts)
    return {"markdown": text or _local_summary(facts), "source": source, "facts": facts}


# --------------------------------------------------------------------------- recommendation explanation
async def explain_recommendation(rec: dict, shap: list[dict]) -> tuple[str, str]:
    facts = {"recommendation": rec, "shap_log_odds": shap}
    text, source = await gemini("In <=120 words explain why this configuration is recommended for the next run, "
                                "citing the SHAP contributions (negative = reduces failure risk) and the trade-offs.", facts, 400)
    if text:
        return text, source
    helps = [s for s in shap if s["contribution"] < 0][:3]
    hurts = [s for s in shap if s["contribution"] > 0][:2]
    md = (f"Predicted failure risk **{_pct(rec['failure_risk'])}** at **{rec['expected_throughput']:,} MB/s** "
          f"(confidence {_pct(rec['confidence'])}). ")
    if helps:
        md += "Risk is pushed down mainly by " + ", ".join(f"`{h['feature']}={h['value']}` ({h['contribution']:+.2f})" for h in helps) + ". "
    if hurts:
        md += "Residual risk comes from " + ", ".join(f"`{h['feature']}={h['value']}` ({h['contribution']:+.2f})" for h in hurts) + ", accepted for the throughput gain."
    return md, source


# --------------------------------------------------------------------------- copilot
_CFG = re.compile(r"CFG-\d{4}", re.I)
_KV = re.compile(r"([a-z_]+)\s*=\s*([\w.\-]+)")


def _intent(q: str) -> str:
    ql = q.lower()
    if len(_CFG.findall(q)) >= 2 or "compare" in ql or "more often than" in ql:
        return "compare"
    if "seed" in ql:
        return "seeds"
    if any(w in ql for w in ("determin", "stochastic", "flaky", "repeatab", "randomness")):
        return "determinism"
    if any(w in ql for w in ("recommend", "optimal", "best config", "highest performance", "next run")):
        return "recommend"
    if any(w in ql for w in ("predict", "risk of", "what if")) or _KV.search(ql):
        return "predict"
    if any(w in ql for w in ("influen", "important", "parameter", "setting", "driver")):
        return "importance"
    if any(w in ql for w in ("root cause", "why", "signature", "fail", "error", "log")):
        return "rootcause"
    if " vs " in ql:
        return "compare"
    return "summary"


def _profile_row(cid: str) -> dict | None:
    prof = analytics._profile_stats().filter(pl.col("config_id") == cid.upper())
    return prof.row(0, named=True) if len(prof) else None


def _compare(q: str) -> dict:
    ids = [i.upper() for i in _CFG.findall(q)]
    prof = analytics._profile_stats()
    if len(ids) < 2:
        worst = prof.filter(pl.col("runs") >= 15).sort("fail_rate", descending=True).row(0, named=True)["config_id"]
        best = prof.filter(pl.col("runs") >= 15).sort("fail_rate").row(0, named=True)["config_id"]
        ids = (ids + [worst, best])[:2] if ids else [worst, best]
    a, b = _profile_row(ids[0]), _profile_row(ids[1])
    if not a or not b:
        return {"markdown": f"I couldn't find enough runs for `{ids[0]}` / `{ids[1]}` (need ≥5 runs each).", "facts": {}}
    if a["fail_rate"] < b["fail_rate"]:
        a, b = b, a  # a = worse
    bundle = store.bundle
    key = [p["name"] for p in store.meta["key_params"]]
    diffs = [k for k in key if a[k] != b[k]]
    sa = {s["feature"]: s for s in bundle.shap({k: a[k] for k in key}, top=60)}
    sb = {s["feature"]: s for s in bundle.shap({k: b[k] for k in key}, top=60)}
    impact = sorted(({"param": k, "worse": a[k], "better": b[k], "shap_delta": round(sa.get(k, {"contribution": 0})["contribution"] - sb.get(k, {"contribution": 0})["contribution"], 3)} for k in diffs),
                    key=lambda x: -x["shap_delta"])
    md = [f"## Why `{a['config_id']}` fails more than `{b['config_id']}`",
          f"| Metric | `{a['config_id']}` | `{b['config_id']}` |", "|---|---|---|",
          f"| Failure rate | **{_pct(a['fail_rate'])}** | **{_pct(b['fail_rate'])}** |",
          f"| Throughput (MB/s) | {a['throughput']:,.0f} | {b['throughput']:,.0f} |",
          f"| Instability index | {a['instability']:.3f} | {b['instability']:.3f} |",
          f"| Runs | {a['runs']} | {b['runs']} |", "", "### Root-cause attribution (SHAP Δ log-odds)"]
    md += [f"- `{d['param']}`: **{d['worse']}** vs {d['better']} → {d['shap_delta']:+.2f}" for d in impact[:5]] or ["- Key parameters identical; difference driven by generic flags / randomness."]
    tp = "higher" if a["throughput"] > b["throughput"] else "lower"
    md += ["", "### Trade-offs", f"- `{a['config_id']}` delivers {tp} throughput; moving to `{b['config_id']}` changes throughput by {b['throughput'] - a['throughput']:+,.0f} MB/s while cutting failure rate by {_pct(a['fail_rate'] - b['fail_rate'])}.",
           "", "### Actionable next steps", f"1. Change `{impact[0]['param'] if impact else 'n/a'}` first — it carries the largest risk delta.", "2. Re-run both profiles with 5 shared seeds to confirm the effect is deterministic."]
    chart = {"type": "bar", "title": "SHAP Δ per changed parameter", "data": [{"name": d["param"], "value": d["shap_delta"]} for d in impact[:8]]}
    kv = [{"k": "Worse config", "v": a["config_id"]}, {"k": "Better config", "v": b["config_id"]}, {"k": "Δ failure rate", "v": _pct(a["fail_rate"] - b["fail_rate"])}]
    return {"markdown": "\n".join(md), "chart": chart, "kv": kv, "facts": {"worse": a, "better": b, "impact": impact}}


def _seeds() -> dict:
    s = analytics.randomization()["seeds"][:8]
    if not s:
        return {"markdown": "## Seed analysis unavailable\n\nThe active dataset has no random-seed column, so seed repeatability cannot be scored. "
                            "Map a seed column on the **CSV Data Upload** tab to enable it.", "facts": {}}
    md = ["## Top risky seeds", "Ranked by excess failures over what the configuration-only model expects (binomial z-score).", "",
          "| Seed | Runs | Observed | Expected | Lift | z |", "|---|---|---|---|---|---|"]
    md += [f"| `{x['seed']}` | {x['runs']} | {_pct(x['observed'])} | {_pct(x['expected'])} | **{x['lift']}×** | {x['z']} |" for x in s]
    flagged = [x for x in s if x["flag"]]
    md += ["", f"**{len(flagged)} seeds** are statistically anomalous (z > 3): their failures are **stochastic but seed-reproducible**, typical of firmware race conditions (`FW_ASSERT_0x3F`).",
           "", "### Actionable next steps", "1. Pin the top 3 seeds into a nightly regression suite.", "2. Capture firmware traces for these seeds at high traffic intensity."]
    return {"markdown": "\n".join(md), "chart": {"type": "bar", "title": "Failure lift vs expectation", "data": [{"name": x["seed"], "value": x["lift"]} for x in s]},
            "kv": [{"k": "Riskiest seed", "v": s[0]["seed"]}, {"k": "Lift", "v": f"{s[0]['lift']}×"}], "facts": {"seeds": s}}


def _rootcause() -> dict:
    fps = analytics.root_cause()["fingerprints"]
    md = ["## Failure root-cause summary"]
    for f in fps[:5]:
        cond = ", ".join(f"`{c['condition']}` ({c['lift']}×)" for c in f["top_conditions"][:2]) or "no dominant condition"
        kind = "deterministic" if f["deterministic_share"] > 0.5 else "stochastic"
        md.append(f"- **{f['signature']}** — {f['count']:,} failures ({_pct(f['share'])}), mostly *{kind}*. {f['description']}. Precursors: {cond}.")
    md += ["", "### Actionable next steps", f"1. Fix `{fps[0]['signature']}` first — it is the largest failure bucket.", "2. Add pre-flight guards for the deterministic precursors listed above."]
    return {"markdown": "\n".join(md), "chart": {"type": "bar", "title": "Failures by signature", "data": [{"name": f["signature"], "value": f["count"]} for f in fps]},
            "kv": [{"k": "Signatures", "v": str(len(fps))}, {"k": "Top", "v": fps[0]["signature"]}], "facts": {"fingerprints": fps}}


def _importance() -> dict:
    imp = analytics.discovery()["importance"][:10]
    md = ["## Settings that most influence pass/fail", "", "| Rank | Parameter | Score | Correlation |", "|---|---|---|---|"]
    md += [f"| {i + 1} | `{r['feature']}` | {r['score']:.3f} | {r['correlation']:+.3f} |" for i, r in enumerate(imp)]
    md += ["", "Scores blend Random-Forest impurity importance (60%) and mutual information (40%). Positive correlation = higher value → more failures.",
           "", "### Actionable next steps", "1. Constrain the top-3 parameters in the test matrix.", "2. Use the Config Sandbox to probe their interactions."]
    return {"markdown": "\n".join(md), "chart": {"type": "bar", "title": "Influence score", "data": [{"name": r["feature"], "value": r["score"]} for r in imp]},
            "kv": [{"k": "#1 driver", "v": imp[0]["feature"]}], "facts": {"importance": imp}}


def _determinism() -> dict:
    d = analytics.determinism()
    classes = {c["class"]: c["count"] for c in d["failure_classes"]}
    md = ["## Deterministic vs stochastic failures",
          f"- **Deterministic:** {classes.get('deterministic', 0):,} failures from profiles that fail ≥80% of runs regardless of seed.",
          f"- **Stochastic:** {classes.get('stochastic', 0) + classes.get('stable', 0):,} failures that depend on seed, thermal drift or timing jitter.", "",
          "| Signature | Deterministic | Stochastic |", "|---|---|---|"]
    md += [f"| `{r['error_signature']}` | {r['deterministic']} | {r['stochastic']} |" for r in d["by_signature"]]
    md += ["", "### Actionable next steps", "1. Deterministic → fix configuration rules; stochastic → harden firmware & thermal margins."]
    return {"markdown": "\n".join(md), "chart": {"type": "bar", "title": "Failures by class", "data": [{"name": k, "value": v} for k, v in classes.items()]},
            "kv": [{"k": k, "v": f"{v:,}"} for k, v in classes.items()], "facts": d["by_signature"]}


def _predict(q: str) -> dict:
    cfg = {}
    choices = store.bundle.choices
    for k, v in _KV.findall(q.lower()):
        if k in choices:
            try:
                cfg[k] = float(v) if not isinstance(choices[k][0], str) else v
            except ValueError:
                pass
    p = store.bundle.predict([cfg])
    shap = store.bundle.shap(cfg, top=8)
    risk = float(0.6 * p["risk"][0] + 0.4 * p["risk_rf"][0])
    md = [f"## Predicted outcome", f"Configuration overrides: {', '.join(f'`{k}={v}`' for k, v in cfg.items()) or '_none parsed — using dataset defaults_'}", "",
          f"- **Failure risk:** {_pct(risk)}", f"- **Expected throughput:** {float(p['throughput'][0]):,.0f} MB/s", "", "### Top SHAP contributions"]
    md += [f"- `{s['feature']}={s['value']}` → {s['contribution']:+.3f}" for s in shap[:6]]
    md += ["", "Tip: write overrides as `queue_depth=256 write_cache=0`."]
    return {"markdown": "\n".join(md), "chart": {"type": "bar", "title": "SHAP (log-odds)", "data": [{"name": s["feature"], "value": s["contribution"]} for s in shap]},
            "kv": [{"k": "Risk", "v": _pct(risk)}, {"k": "Throughput", "v": f"{float(p['throughput'][0]):,.0f} MB/s"}], "facts": {"config": cfg, "risk": risk, "shap": shap}}


def _recommend() -> dict:
    recs = store.cached("recommend::default", lambda: ml.recommend(store.bundle, {}, df=store.df))
    r = recs[0]
    md = ["## Recommended configuration for the next run",
          f"**Failure risk {_pct(r['failure_risk'])}** · **{r['expected_throughput']:,} MB/s** · confidence **{_pct(r['confidence'])}**", "",
          "| Parameter | Value |", "|---|---|"]
    md += [f"| `{k}` | {v} |" for k, v in r["config"].items()]
    md += ["", "### Trade-offs"] + [f"- Alternative #{x['rank']}: risk {_pct(x['failure_risk'])}, {x['expected_throughput']:,} MB/s" for x in recs[1:4]]
    md += ["", "### Actionable next steps", "1. Run the recommended config on 5 seeds to validate.", "2. Promote to the default test profile if pass rate ≥ 98%."]
    return {"markdown": "\n".join(md), "chart": {"type": "bar", "title": "Throughput of top candidates (MB/s)", "data": [{"name": f"#{x['rank']}", "value": x["expected_throughput"]} for x in recs]},
            "kv": [{"k": "Risk", "v": _pct(r["failure_risk"])}, {"k": "Throughput", "v": f"{r['expected_throughput']:,} MB/s"}, {"k": "Confidence", "v": _pct(r["confidence"])}],
            "facts": {"recommendations": recs}}


def _route(query: str, intent: str) -> dict:
    if intent == "compare":
        res = _compare(query)
    elif intent == "seeds":
        res = _seeds()
    elif intent == "rootcause":
        res = _rootcause()
    elif intent == "importance":
        res = _importance()
    elif intent == "determinism":
        res = _determinism()
    elif intent == "predict":
        res = _predict(query)
    elif intent == "recommend":
        res = _recommend()
    else:
        f = _summary_facts()
        res = {"markdown": _local_summary(f), "facts": f, "kv": [{"k": "Pass rate", "v": _pct(f["kpis"]["pass_rate"])}]}
    return res


async def copilot(query: str) -> dict:
    intent = _intent(query)
    res = await run_in_threadpool(_route, query, intent)
    text, source = await gemini(f"User question: {query}\nAnswer it with structured Markdown (headings, a table if useful, root causes, explicit trade-offs).", res.get("facts", {}))
    return {"intent": intent, "markdown": text or res["markdown"], "chart": res.get("chart"), "kv": res.get("kv", []), "source": source}
