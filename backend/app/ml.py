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
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

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

    full_rf = RandomForestClassifier(n_estimators=160, max_depth=14, min_samples_leaf=3, n_jobs=-1, random_state=7, class_weight="balanced_subsample")
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
    risk_model.fit(Xp, y)

    risk_rf = RandomForestClassifier(n_estimators=150, max_depth=12, min_samples_leaf=4, n_jobs=-1, random_state=5)
    risk_rf.fit(Xp, y)

    ok = y == 0
    if HAS_LGB:
        tput_model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, verbose=-1)
    else:
        tput_model = HistGradientBoostingRegressor(max_iter=300)
    tput_model.fit(Xp[ok], df["throughput_mbps"].to_numpy()[ok])

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
        importance_full=importance_full, mutual_info=mutual_info, correlation=correlation,
    )


def recommend(bundle: ModelBundle, context: dict, n_candidates: int = 4000, max_risk: float = 0.05, seed: int = 0) -> list[dict]:
    """Random search over the key-parameter space + greedy refinement of generic flags."""
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

    picks = [best] + [cands[i] for i in order[1:5]]
    final = bundle.predict(picks)
    out = []
    for i, cfg in enumerate(picks):
        p_gb, p_rf = float(final["risk"][i]), float(final["risk_rf"][i])
        r = 0.6 * p_gb + 0.4 * p_rf
        agreement = 1 - min(1.0, abs(p_gb - p_rf) * 4)
        conf = 0.5 * agreement + 0.3 * (1 - min(1.0, r * 5)) + 0.2 * bundle.auc
        out.append({
            "rank": i + 1,
            "config": {k: _jsonable(v) if not isinstance(v, (np.integer, np.floating)) else _jsonable(v.item()) for k, v in cfg.items() if k in key_params or k in generic},
            "context": context,
            "failure_risk": round(r, 4),
            "expected_throughput": round(float(final["throughput"][i]), 1),
            "confidence": round(float(np.clip(conf, 0, 0.99)), 3),
            "model_agreement": round(agreement, 3),
        })
    return out
