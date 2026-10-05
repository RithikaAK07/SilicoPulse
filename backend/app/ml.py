"""ML engine: feature encoding, model training, risk prediction, SHAP and recommendations.

Models trained on every dataset (re)generation:
  * full_rf      RandomForest on config + randomization + context  -> global feature importance (Q1, Q3)
  * risk_model   LightGBM on config + context (pre-execution view) -> failure probability (Q7)
  * risk_rf      RandomForest on the same features                 -> ensemble agreement / confidence (Q8)
  * tput_model   LightGBM regressor on passing runs                -> expected throughput (Q7, Q8)
SHAP values come from LightGBM's native TreeSHAP (pred_contrib=True); if LightGBM
is unavailable the engine falls back to sklearn HistGradientBoosting + occlusion attribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix, mean_absolute_error,
                             precision_recall_fscore_support, r2_score, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, train_test_split

try:
    import lightgbm as lgb

    HAS_LGB = True
except Exception:  # pragma: no cover
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    HAS_LGB = False


@dataclass
class Encoder:
    columns: list[str]
    cat_maps: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def fit(cls, df: pl.DataFrame, columns: list[str]) -> "Encoder":
        enc = cls(columns=columns)
        for c in columns:
            if df[c].dtype == pl.Utf8:
                enc.cat_maps[c] = {v: i for i, v in enumerate(sorted(df[c].unique().to_list()))}
        return enc

    def transform(self, df: pl.DataFrame) -> np.ndarray:
        cols = []
        for c in self.columns:
            if c in self.cat_maps:
                m = self.cat_maps[c]
                cols.append(np.array([m.get(v, -1) for v in df[c].to_list()], dtype=float))
            else:
                cols.append(df[c].cast(pl.Float64).to_numpy())
        return np.column_stack(cols)


@dataclass
class ModelBundle:
    meta: dict
    full_enc: Encoder
    pre_enc: Encoder
    full_rf: RandomForestClassifier
    risk_model: object
    risk_rf: RandomForestClassifier
    tput_model: object
    defaults: dict
    choices: dict
    auc: float
    tput_scale: float
    importance_full: dict[str, float]
    mutual_info: dict[str, float]
    correlation: dict[str, float]
    validation: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ predict
    def frame_from_config(self, configs: list[dict]) -> pl.DataFrame:
        cats = self.pre_enc.cat_maps
        rows = []
        for cfg in configs:
            row = dict(self.defaults)
            row.update({k: v for k, v in cfg.items() if k in row})
            rows.append({k: (str(v) if k in cats else float(v)) for k, v in row.items()})
        return pl.DataFrame(rows, schema={c: (pl.Utf8 if c in cats else pl.Float64) for c in self.pre_enc.columns})

    def predict(self, configs: list[dict]) -> dict:
        X = self.pre_enc.transform(self.frame_from_config(configs))
        p_gb = _proba(self.risk_model, X)
        p_rf = self.risk_rf.predict_proba(X)[:, 1]
        tput = np.clip(_regress(self.tput_model, X), 1, None)
        return {"risk": p_gb, "risk_rf": p_rf, "throughput": tput, "X": X}

    def predict_frame_risk(self, df: pl.DataFrame) -> np.ndarray:
        """Pre-execution failure probability for every run in a frame (used for expected-vs-observed seed analysis)."""
        return _proba(self.risk_model, self.pre_enc.transform(df))

    def shap(self, config: dict, top: int = 12) -> list[dict]:
        X = self.pre_enc.transform(self.frame_from_config([config]))
        if HAS_LGB:
            contrib = self.risk_model.predict(X, pred_contrib=True)[0][:-1]
        else:  # occlusion attribution on log-odds
            base = _logit(_proba(self.risk_model, X))[0]
            contrib = []
            for j, c in enumerate(self.pre_enc.columns):
                Xo = X.copy()
                Xo[0, j] = self.pre_enc.transform(self.frame_from_config([{c: self.defaults[c]}]))[0, j]
                contrib.append(base - _logit(_proba(self.risk_model, Xo))[0])
            contrib = np.array(contrib)
        cfg_full = self.frame_from_config([config]).row(0, named=True)
        order = np.argsort(-np.abs(contrib))[:top]
        return [
            {"feature": self.pre_enc.columns[j], "value": _jsonable(cfg_full[self.pre_enc.columns[j]]), "contribution": round(float(contrib[j]), 4)}
            for j in order
        ]


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _proba(model, X):
    return model.predict_proba(X)[:, 1]


def _regress(model, X):
    return model.predict(X)


def _jsonable(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


RF_MAX_ROWS = 40_000  # bootstrap rows per tree on very large datasets
CV_MAX_ROWS = 30_000  # rows used for cross-validation on very large datasets


def _new_risk_model(n_estimators: int = 350):
    if HAS_LGB:
        return lgb.LGBMClassifier(n_estimators=n_estimators, learning_rate=0.05, num_leaves=31, min_child_samples=20,
                                  subsample=0.9, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
    return HistGradientBoostingClassifier(max_iter=n_estimators, learning_rate=0.06)


def validate(model, Xtr, Xte, ytr, yte, columns: list[str], throughput: np.ndarray | None = None) -> dict:
    """Hold-out + cross-validated evaluation of the pre-execution failure-risk model."""
    p = _proba(model, Xte)
    pred = (p >= 0.5).astype(int)
    prec, rec, f1, _ = precision_recall_fscore_support(yte, pred, average="binary", zero_division=0)
    grid = np.round(np.arange(0.05, 0.951, 0.01), 2)
    f1s = []
    for t in grid:
        pr, rc, f, _ = precision_recall_fscore_support(yte, (p >= t).astype(int), average="binary", zero_division=0)
        f1s.append((f, pr, rc, t))
    best_f1, best_p, best_r, best_t = max(f1s)
    tn, fp, fn, tp = confusion_matrix(yte, (p >= best_t).astype(int), labels=[0, 1]).ravel()
    bins = np.linspace(0, 1, 11)
    which = np.clip(np.digitize(p, bins) - 1, 0, 9)
    calibration = [{"bin": f"{bins[b]:.1f}-{bins[b + 1]:.1f}", "mean_predicted": round(float(p[which == b].mean()), 4),
                    "observed_rate": round(float(yte[which == b].mean()), 4), "count": int((which == b).sum())}
                   for b in range(10) if (which == b).sum() > 0]
    # k-fold CV on the training split (subsampled for very large data)
    rng = np.random.default_rng(1)
    idx = np.arange(len(ytr)) if len(ytr) <= CV_MAX_ROWS else rng.choice(len(ytr), CV_MAX_ROWS, replace=False)
    folds = []
    for tr_i, va_i in StratifiedKFold(n_splits=5, shuffle=True, random_state=2).split(Xtr[idx], ytr[idx]):
        m = _new_risk_model(200)
        m.fit(Xtr[idx][tr_i], ytr[idx][tr_i])
        folds.append(float(roc_auc_score(ytr[idx][va_i], _proba(m, Xtr[idx][va_i]))))
    out = {
        "split": {"train": int(len(ytr)), "test": int(len(yte)),
                  "strategy": "stratified 80/20 hold-out test set + 5-fold stratified CV on the training split"},
        "test_size": int(len(yte)),
        "class_balance": {"failure_rate_train": round(float(ytr.mean()), 4), "failure_rate_test": round(float(yte.mean()), 4)},
        "holdout": {"roc_auc": round(float(roc_auc_score(yte, p)), 4), "pr_auc": round(float(average_precision_score(yte, p)), 4),
                    "brier": round(float(brier_score_loss(yte, p)), 4), "threshold": 0.5,
                    "precision": round(float(prec), 4), "recall": round(float(rec), 4), "f1": round(float(f1), 4),
                    "best_f1_threshold": float(best_t), "best_f1": round(float(best_f1), 4),
                    "precision_at_best": round(float(best_p), 4), "recall_at_best": round(float(best_r), 4),
                    "confusion_matrix_at_best": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}},
        "cv": {"folds": 5, "rows_used": int(len(idx)), "roc_auc_mean": round(float(np.mean(folds)), 4),
               "roc_auc_std": round(float(np.std(folds)), 4), "roc_auc_folds": [round(x, 4) for x in folds]},
        "calibration": calibration,
        "model": "LightGBM gradient boosting (pre-execution features: configuration + context)" if HAS_LGB else "HistGradientBoosting",
    }
    if HAS_LGB:
        gain = model.booster_.feature_importance(importance_type="gain")
        tot = gain.sum() or 1
        order = np.argsort(-gain)[:15]
        out["feature_importance"] = [{"feature": columns[j], "gain_share": round(float(gain[j] / tot), 4)} for j in order]
        sample = Xte[rng.choice(len(Xte), min(2000, len(Xte)), replace=False)]
        contrib = np.abs(model.predict(sample, pred_contrib=True)[:, :-1]).mean(axis=0)
        order = np.argsort(-contrib)[:12]
        out["global_shap"] = [{"feature": columns[j], "mean_abs_shap": round(float(contrib[j]), 4)} for j in order]
    return out


def train(df: pl.DataFrame, meta: dict) -> ModelBundle:
    config_cols = meta["config_params"]
    random_cols = meta["random_vars"]
    ctx_cols = meta["context"]
    full_cols = config_cols + random_cols + ctx_cols
    pre_cols = config_cols + ctx_cols

    y = df["failed"].to_numpy()
    full_enc = Encoder.fit(df, full_cols)
    pre_enc = Encoder.fit(df, pre_cols)
    Xf = full_enc.transform(df)
    Xp = pre_enc.transform(df)

    rf_rows = None if len(df) <= RF_MAX_ROWS else RF_MAX_ROWS / len(df)
    full_rf = RandomForestClassifier(n_estimators=160, max_depth=14, min_samples_leaf=3, n_jobs=-1, random_state=7,
                                     class_weight="balanced_subsample", max_samples=rf_rows)
    full_rf.fit(Xf, y)
    importance_full = dict(zip(full_cols, full_rf.feature_importances_.round(5).tolist()))

    rng = np.random.default_rng(3)
    idx = rng.choice(len(df), size=min(4000, len(df)), replace=False)
    discrete = np.array([c in full_enc.cat_maps or c == "seed" or (c in config_cols and df[c].n_unique() <= 32) for c in full_cols])
    mi = mutual_info_classif(Xf[idx], y[idx], discrete_features=discrete, random_state=0)
    mutual_info = dict(zip(full_cols, np.round(mi, 5).tolist()))
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = [np.corrcoef(Xf[:, j], y)[0, 1] for j in range(Xf.shape[1])]
    correlation = dict(zip(full_cols, [0.0 if np.isnan(c) else round(float(c), 4) for c in corr]))

    Xtr, Xte, ytr, yte = train_test_split(Xp, y, test_size=0.2, random_state=11, stratify=y)
    if HAS_LGB:
        risk_model = lgb.LGBMClassifier(n_estimators=350, learning_rate=0.05, num_leaves=31, min_child_samples=20, subsample=0.9, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
    else:
        risk_model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06)
    risk_model.fit(Xtr, ytr)
    auc = float(roc_auc_score(yte, _proba(risk_model, Xte)))
    validation = validate(risk_model, Xtr, Xte, ytr, yte, pre_cols, df["throughput_mbps"].to_numpy())
    risk_model.fit(Xp, y)

    risk_rf = RandomForestClassifier(n_estimators=150, max_depth=12, min_samples_leaf=4, n_jobs=-1, random_state=5, max_samples=rf_rows)
    risk_rf.fit(Xp, y)

    ok = y == 0
    if HAS_LGB:
        tput_model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, verbose=-1)
    else:
        tput_model = HistGradientBoostingRegressor(max_iter=300)
    t_all = df["throughput_mbps"].to_numpy()
    if ok.sum() >= 50 and np.ptp(t_all[ok]) > 0:
        tr_i, te_i = train_test_split(np.flatnonzero(ok), test_size=0.2, random_state=11)
        probe = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, verbose=-1) if HAS_LGB else HistGradientBoostingRegressor(max_iter=300)
        probe.fit(Xp[tr_i], t_all[tr_i])
        pt = probe.predict(Xp[te_i])
        validation["throughput_model"] = {"r2": round(float(r2_score(t_all[te_i], pt)), 4), "mae": round(float(mean_absolute_error(t_all[te_i], pt)), 2),
                                          "test_rows": int(len(te_i))}
    else:
        validation["throughput_model"] = {"note": "throughput not available or constant"}
    tput_model.fit(Xp[ok], t_all[ok])

    defaults, choices = {}, {}
    key_choice = {p["name"]: p["choices"] for p in meta["key_params"]}
    for c in pre_cols:
        mode = df[c].mode().sort()[0]
        defaults[c] = mode if c in pre_enc.cat_maps else float(mode)
        if c in key_choice:
            choices[c] = key_choice[c]
        elif c in pre_enc.cat_maps:
            choices[c] = sorted(pre_enc.cat_maps[c].keys())
    return ModelBundle(
        meta=meta, full_enc=full_enc, pre_enc=pre_enc, full_rf=full_rf, risk_model=risk_model, risk_rf=risk_rf,
        tput_model=tput_model, defaults=defaults, choices=choices, auc=round(auc, 4),
        tput_scale=float(np.percentile(df["throughput_mbps"].to_numpy()[ok], 95)),
        importance_full=importance_full, mutual_info=mutual_info, correlation=correlation, validation=validation,
    )


def _norm(v) -> str:
    if isinstance(v, (np.integer, np.floating)):
        v = v.item()
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _support(df: pl.DataFrame, key_params: list[str], cands: list[dict], max_profiles: int = 5000):
    """Distance (number of differing key parameters) from each candidate to the nearest observed
    configuration, plus that configuration's observed runs / failure rate."""
    prof = df.group_by(key_params).agg(pl.len().alias("n"), pl.col("failed").mean().alias("fr")).sort("n", descending=True).head(max_profiles)
    codes: dict[str, dict[str, int]] = {}
    O = np.zeros((len(prof), len(key_params)), dtype=np.int32)
    C = np.zeros((len(cands), len(key_params)), dtype=np.int32)
    for j, k in enumerate(key_params):
        m = codes.setdefault(k, {})
        O[:, j] = [m.setdefault(_norm(v), len(m)) for v in prof[k].to_list()]
        C[:, j] = [m.setdefault(_norm(c[k]), len(m)) for c in cands]
    dist = np.zeros((len(cands), len(prof)), dtype=np.int16)
    for j in range(len(key_params)):
        dist += C[:, None, j] != O[None, :, j]
    nearest = dist.argmin(axis=1)
    return dist[np.arange(len(cands)), nearest], prof["n"].to_numpy()[nearest], prof["fr"].to_numpy()[nearest]


def _pareto(risk: np.ndarray, tput: np.ndarray) -> np.ndarray:
    order = np.lexsort((risk, -tput))
    mask = np.zeros(len(risk), dtype=bool)
    best = np.inf
    for i in order:
        if risk[i] < best:
            mask[i], best = True, risk[i]
    return mask


def recommend(bundle: ModelBundle, context: dict, n_candidates: int = 4000, max_risk: float = 0.05, seed: int = 0,
              df: pl.DataFrame | None = None) -> list[dict]:
    """Random search over the key-parameter space + greedy refinement of generic flags.

    With `df`, candidates far from any observed configuration are penalised and every result
    reports its training-data support, so extrapolations are explicit."""
    rng = np.random.default_rng(seed)
    key_params = [p["name"] for p in bundle.meta["key_params"]]
    cands = []
    for _ in range(n_candidates):
        cfg = {k: rng.choice(bundle.choices[k]).item() for k in key_params}
        cfg.update(context)
        cands.append(cfg)
    pred = bundle.predict(cands)
    risk = 0.6 * pred["risk"] + 0.4 * pred["risk_rf"]
    tput = pred["throughput"]
    utility = (tput / bundle.tput_scale) * (1 - risk) ** 3 - 2.0 * np.clip(risk - max_risk, 0, None)
    dist = None
    if df is not None and len(df):
        dist, near_n, near_fr = _support(df, key_params, cands)
        utility = utility - 0.06 * np.clip(dist - 1, 0, None)  # prefer configurations the data actually covers
    pareto = _pareto(risk, tput)
    order = np.argsort(-utility)

    # greedy flip of the most influential generic flags on the winner
    generic = [c for c in bundle.meta["config_params"] if c not in key_params]
    generic = sorted(generic, key=lambda c: -bundle.importance_full.get(c, 0))[:6]
    best = dict(cands[order[0]])
    best_u = utility[order[0]]
    for g in generic:
        if g in bundle.pre_enc.cat_maps or not 0 <= float(bundle.defaults[g]) <= 4:
            continue  # greedy refinement only flips small-integer / boolean flags
        vals = sorted(set(float(v) for v in [0, 1, bundle.defaults[g]]))
        trials = [dict(best, **{g: v}) for v in vals]
        p = bundle.predict(trials)
        r = 0.6 * p["risk"] + 0.4 * p["risk_rf"]
        u = (p["throughput"] / bundle.tput_scale) * (1 - r) ** 3 - 2.0 * np.clip(r - max_risk, 0, None)
        if u.max() > best_u:
            best_u, best = u.max(), trials[int(u.argmax())]

    pick_idx = [int(order[0])] + [int(i) for i in order[1:5]]
    picks = [best] + [cands[i] for i in order[1:5]]
    final = bundle.predict(picks)
    hi_tput = int(np.argmax(tput))
    lo_risk = int(np.argmin(risk))
    out = []
    for i, cfg in enumerate(picks):
        p_gb, p_rf = float(final["risk"][i]), float(final["risk_rf"][i])
        r = 0.6 * p_gb + 0.4 * p_rf
        agreement = 1 - min(1.0, abs(p_gb - p_rf) * 4)
        extra = {}
        if dist is not None:
            ci = pick_idx[i]
            d = int(dist[ci])
            exact = int(near_n[ci]) if d == 0 else 0
            support_factor = 1.0 if exact >= 5 else 0.7 if d <= 1 else 0.4 if d == 2 else 0.1
            conf = 0.4 * agreement + 0.25 * (1 - min(1.0, r * 5)) + 0.15 * bundle.auc + 0.2 * support_factor
            extra["support"] = {"exact_matching_runs": exact, "nearest_observed_distance": d,
                                "nearest_observed_runs": int(near_n[ci]), "nearest_observed_failure_rate": round(float(near_fr[ci]), 4),
                                "key_parameters_compared": len(key_params)}
            extra["extrapolation"] = d > 2
        else:
            conf = 0.5 * agreement + 0.3 * (1 - min(1.0, r * 5)) + 0.2 * bundle.auc
        shap = bundle.shap({**context, **cfg}, top=8)
        extra["uncertainty"] = {"model_disagreement": round(abs(p_gb - p_rf), 4), "risk_range": [round(min(p_gb, p_rf), 4), round(max(p_gb, p_rf), 4)]}
        extra["pareto_optimal"] = bool(pareto[pick_idx[i]]) if i else bool(pareto[pick_idx[0]])
        extra["why"] = [f"{s['feature']}={s['value']} lowers predicted failure log-odds by {abs(s['contribution']):.2f}" for s in shap if s["contribution"] < 0][:3]
        extra["tradeoffs"] = [
            f"vs highest-throughput candidate: {float(tput[hi_tput]) - float(final['throughput'][i]):+,.0f} MB/s, risk {float(risk[hi_tput]) - r:+.1%}",
            f"vs lowest-risk candidate: {float(tput[lo_risk]) - float(final['throughput'][i]):+,.0f} MB/s, risk {float(risk[lo_risk]) - r:+.1%}",
        ]
        out.append({**extra, 
            "rank": i + 1,
            "config": {k: _jsonable(v) if not isinstance(v, (np.integer, np.floating)) else _jsonable(v.item()) for k, v in cfg.items() if k in key_params or k in generic},
            "context": context,
            "failure_risk": round(r, 4),
            "expected_throughput": round(float(final["throughput"][i]), 1),
            "confidence": round(float(np.clip(conf, 0, 0.99)), 3),
            "model_agreement": round(agreement, 3),
        })
    return out
