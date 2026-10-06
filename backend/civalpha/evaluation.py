"""Chronological walk-forward evaluation.

Folds: the timeline after `min_train_days` is cut into consecutive test blocks of `fold_length` trading
days. A model for a block is trained only on samples whose label window had closed before the block
starts (sample index + horizon < block start), i.e. overlapping labels are purged. Every sample is
predicted exactly once, by a model that never saw its period.

Reported: Brier score, log loss, AUC, accuracy, calibration bins, Brier skill vs the training base rate,
the paired augmented-vs-baseline Brier difference with a date-block bootstrap CI, a long/short
simulation after transaction costs on non-overlapping 21-day periods entered at the next close, and a
coverage curve: what acting only on the most confident forecasts would have earned after costs.
"""
from __future__ import annotations

# The pre-registered live test: the bar the LIVE forecasts must clear, written down on 2026-10-06 before any of the
# 1,557 open forecasts had resolved, so the verdict cannot be fitted to the data after the fact. PENDING until
# min_resolved outcomes exist per model; then PASS only if every criterion holds, else FAIL.
LIVE_TEST = {
    "registeredOn": "2026-10-06",
    "minResolved": 500,
    "minAuc": 0.53,
    "brierBelowBaseRate": True,
    "rule": "On at least 500 resolved LIVE forecasts per model: AUC >= 0.53 and Brier below the base-rate Brier "
            "(what always forecasting the realized hit rate would score). Registered before the first outcome resolved.",
}


def live_test_verdict(resolved: int, auc: float | None, brier: float | None, base_rate: float | None) -> dict:
    """Score one model against LIVE_TEST. base_rate is the realized share of positive outcomes among resolved forecasts."""
    base_brier = None if base_rate is None else float(base_rate * (1.0 - base_rate))
    if resolved < LIVE_TEST["minResolved"] or auc is None or brier is None or base_brier is None:
        verdict = "PENDING"
    else:
        verdict = "PASS" if auc >= LIVE_TEST["minAuc"] and brier < base_brier else "FAIL"
    return {"resolved": int(resolved), "auc": auc, "brier": brier, "baseRateBrier": base_brier, "verdict": verdict,
            "progress": min(1.0, resolved / LIVE_TEST["minResolved"])}

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .features import FEATURES
from .model import LogitModel
from .returns import HORIZON

KINDS = ("BASELINE", "AUGMENTED")     # the two live forecast models; always evaluated and compared with each other
COVERAGE_LEVELS = (0.05, 0.10, 0.20, 0.30, 0.50, 1.00)
FEATURE_SET_IDS = {"BASELINE": f"LOGIT_BASELINE_{len(FEATURES['BASELINE'])}", "AUGMENTED": f"LOGIT_AUGMENTED_{len(FEATURES['AUGMENTED'])}"}


@dataclass(frozen=True)
class ModelSpec:
    """One model in the walk-forward: which inputs, which algorithm, which label. `feature_set` is the identifier every
    output carries (LOGIT_BASELINE_10, GBM_AI_39, ...), so a number can always be traced to the exact input list."""
    kind: str
    feature_set: str
    features: tuple
    algorithm: str                      # "logit" (model.LogitModel) or "gbm" (strategies.ai.AiModel)
    horizon: int                        # trading days in the label window
    entry: str                          # "close(t)": the forecast target; "next close": the book's label, t+1 -> t+1+horizon
    label_col: str = "label"
    excess_col: str = "excess_return"
    exec_col: str = "exec_excess_return"

    def info(self) -> dict:
        return {"kind": self.kind, "featureSet": self.feature_set, "algorithm": self.algorithm, "horizon": self.horizon,
                "entry": self.entry, "nFeatures": len(self.features), "features": list(self.features),
                "label": f"{self.horizon}-day excess return vs sector ETF, entered at {self.entry}"}


def default_specs() -> list[ModelSpec]:
    return [ModelSpec(k, FEATURE_SET_IDS[k], tuple(FEATURES[k]), "logit", HORIZON, "close(t)") for k in KINDS]


@dataclass
class EvalConfig:
    horizon: int = HORIZON
    sample_every: int = 7
    embargo: int = HORIZON
    fold_length: int = 63
    min_train_days: int = 504
    cost_bps_per_side: float = 10.0
    signal_band: float = 0.03
    n_boot: int = 0          # bootstrap refits per fold model (0 = point model only; evaluation does not need bands)
    ci_boot: int = 500


def walk_forward_folds(idxs: np.ndarray, n_calendar: int, cfg: EvalConfig) -> list[dict]:
    first = int(idxs.min())
    start = first + cfg.min_train_days
    folds = []
    k = 0
    while start <= int(idxs.max()):
        end = min(start + cfg.fold_length, n_calendar)
        folds.append({"fold": k, "test_start_idx": start, "test_end_idx": end - 1,
                      "train_max_idx": start - cfg.embargo - 1})
        start, k = end, k + 1
    return folds


def train_mask(panel: pd.DataFrame, fold: dict, cfg: EvalConfig, label_col: str = "label") -> pd.Series:
    # label of sample i is known at close(i + horizon); require it strictly before the test block starts. cfg.horizon is
    # the longest label in the run (21), so a shorter label (the book's 10-day one) is purged more than strictly needed.
    return (panel["idx"] + cfg.horizon < fold["test_start_idx"]) & panel[label_col].notna()


def _fit_predict(spec: ModelSpec, train: pd.DataFrame, test: pd.DataFrame, cfg: EvalConfig) -> np.ndarray:
    feats = list(spec.features)
    if spec.algorithm == "logit":
        m = LogitModel(feats, n_boot=cfg.n_boot).fit(train, train[spec.label_col].astype(bool), groups=train["idx"])
        return m.predict(test)
    if spec.algorithm == "gbm":
        from .strategies.ai import AiConfig, new_model   # local import: strategies.ai imports this module
        m = new_model(AiConfig(horizon=spec.horizon)).fit(train[feats].to_numpy(float), train[spec.label_col].astype(int).to_numpy())
        return m.predict_proba(test[feats].to_numpy(float))[:, 1]
    raise ValueError(f"unknown algorithm {spec.algorithm}")


def run_walk_forward(panel: pd.DataFrame, calendar: pd.DatetimeIndex, cfg: EvalConfig, specs: list[ModelSpec] | None = None) -> dict:
    """Walk-forward over `specs` (default: the two live models). Every spec is scored on the same test blocks; a spec with
    its own label columns (the book's 10-day label) is scored on the rows where that label is known."""
    specs = specs or default_specs()
    by_kind = {sp.kind: sp for sp in specs}
    folds = walk_forward_folds(panel["idx"].to_numpy(), len(calendar), cfg)
    preds, fold_out = [], []
    for fold in folds:
        fo = {"fold": fold["fold"], "testStart": str(calendar[fold["test_start_idx"]].date()),
              "testEnd": str(calendar[min(fold["test_end_idx"], len(calendar) - 1)].date()), "brier": {}}
        for sp in specs:
            sub = panel[panel[sp.label_col].notna()]
            test = sub[(sub["idx"] >= fold["test_start_idx"]) & (sub["idx"] <= fold["test_end_idx"])]
            train = sub[train_mask(sub, fold, cfg, sp.label_col)]
            if test.empty or len(train) < 200 or train[sp.label_col].nunique() < 2:
                continue
            p = _fit_predict(sp, train, test, cfg)
            y = test[sp.label_col].astype(bool)
            fo["brier"][sp.kind] = float(np.mean((p - y.astype(float)) ** 2))
            fo.setdefault("trainEnd", str(calendar[int(train["idx"].max())].date()))
            fo.setdefault("nTrain", int(len(train)))
            fo.setdefault("nTest", int(len(test)))
            preds.append(pd.DataFrame({"company_id": test["company_id"].values, "as_of_date": test["as_of_date"].values,
                                       "idx": test["idx"].values, "model_kind": sp.kind, "fold": fold["fold"],
                                       "probability": p, "outcome": y.values,
                                       "excess_return": test[sp.excess_col].values,
                                       "exec_excess_return": test[sp.exec_col].values,
                                       "train_base_rate": float(train[sp.label_col].astype(float).mean())}))
        if fo["brier"]:
            fold_out.append(fo)
    if not preds:
        raise ValueError("not enough history for a walk-forward evaluation")
    P = pd.concat(preds, ignore_index=True)
    kinds = [sp.kind for sp in specs if (P.model_kind == sp.kind).any()]
    metrics = {k: classification_metrics(P[P.model_kind == k]) for k in kinds}
    calib = {k: calibration_bins(P[P.model_kind == k]) for k in kinds}
    comparison = compare_models(P, cfg) if all(k in kinds for k in KINDS) else {"n": 0}
    comparison.update(fold_consistency(fold_out))
    trading = {k: trading_simulation(P[P.model_kind == k], cfg, horizon=by_kind[k].horizon) for k in kinds}
    for k in kinds:
        d = P[P.model_kind == k]
        trading[k]["coverage"] = coverage_curve(d["probability"].to_numpy(float), d["outcome"].to_numpy(float),
                                                d["exec_excess_return"].to_numpy(float), d["idx"].to_numpy(),
                                                cost_bps_per_side=cfg.cost_bps_per_side, cost_legs=4)
    models = {k: by_kind[k].info() for k in kinds}
    config = {"horizon": cfg.horizon, "sampleEvery": cfg.sample_every, "embargo": cfg.embargo, "foldLength": cfg.fold_length,
              "minTrainDays": cfg.min_train_days, "costBpsPerSide": cfg.cost_bps_per_side, "signalBand": cfg.signal_band,
              "models": models}
    return {"config": config, "metrics": metrics, "calibration": calib, "comparison": comparison,
            "trading": trading, "folds": fold_out, "predictions": P, "models": models,
            "verdict": verdict(metrics, comparison, trading, cfg, models)}


# --------------------------------------------------------------------------- metrics
def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def classification_metrics(df: pd.DataFrame) -> dict:
    p, y = df["probability"].to_numpy(float), df["outcome"].to_numpy(float)
    base = df["train_base_rate"].to_numpy(float)
    b, b_ref = brier(p, y), brier(base, y)
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    return {"n": int(len(y)), "brier": b, "brierReference": b_ref, "brierSkill": 1 - b / b_ref if b_ref > 0 else 0.0,
            "logLoss": log_loss(p, y), "auc": auc, "accuracy": float(np.mean((p > 0.5) == (y > 0.5))),
            "baseRate": float(y.mean()), "meanPredicted": float(p.mean())}


def calibration_bins(df: pd.DataFrame, n_bins: int = 10) -> list[dict]:
    p, y = df["probability"].to_numpy(float), df["outcome"].to_numpy(float)
    edges = np.linspace(0, 1, n_bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.sum():
            out.append({"binLow": float(lo), "binHigh": float(hi), "meanPredicted": float(p[m].mean()),
                        "observedRate": float(y[m].mean()), "count": int(m.sum())})
    return out


def compare_models(P: pd.DataFrame, cfg: EvalConfig) -> dict:
    a = P[P.model_kind == "AUGMENTED"].set_index(["company_id", "idx"]).sort_index()
    b = P[P.model_kind == "BASELINE"].set_index(["company_id", "idx"]).sort_index()
    j = a.join(b, lsuffix="_a", rsuffix="_b", how="inner")
    y = j["outcome_a"].astype(float).to_numpy()
    d = (j["probability_a"].to_numpy() - y) ** 2 - (j["probability_b"].to_numpy() - y) ** 2
    dates = j.index.get_level_values("idx").to_numpy()
    uniq = np.unique(dates)
    per_date = pd.Series(d).groupby(dates).sum()
    counts = pd.Series(np.ones_like(d)).groupby(dates).sum()
    rng = np.random.default_rng(11)
    boots = []
    for _ in range(cfg.ci_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        boots.append(per_date.loc[pick].sum() / counts.loc[pick].sum())
    auc_a = roc_auc_score(y, j["probability_a"]) if len(np.unique(y)) == 2 else float("nan")
    auc_b = roc_auc_score(y, j["probability_b"]) if len(np.unique(y)) == 2 else float("nan")
    return {"n": int(len(d)), "brierDiff": float(d.mean()), "ciLow": float(np.percentile(boots, 2.5)),
            "ciHigh": float(np.percentile(boots, 97.5)), "aucDiff": float(auc_a - auc_b),
            "note": "brierDiff = Brier(augmented) - Brier(baseline); negative means the augmented model is better. "
                    "95% CI from a bootstrap over as-of dates."}


def fold_consistency(folds: list[dict]) -> dict:
    """How often the augmented model beats the baseline fold by fold, with a two-sided sign test (ties dropped)."""
    diffs = [f["brier"]["AUGMENTED"] - f["brier"]["BASELINE"] for f in folds if set(KINDS) <= set(f.get("brier", {}))]
    wins = sum(d < 0 for d in diffs)
    losses = sum(d > 0 for d in diffs)
    n = wins + losses
    if n == 0:
        return {"foldsAugmentedBetter": 0, "foldsCompared": 0, "signTestP": None}
    k = min(wins, losses)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"foldsAugmentedBetter": wins, "foldsCompared": n, "signTestP": p}


def trading_simulation(df: pd.DataFrame, cfg: EvalConfig, horizon: int | None = None) -> dict:
    """Long/short each stock against its sector benchmark on non-overlapping periods.

    Position +1 if p >= 0.5 + band, -1 if p <= 0.5 - band. Entry at the close after the as-of date,
    holding `horizon` trading days. Cost per position per period = 2 legs (stock + benchmark hedge)
    x 2 sides (entry + exit) x cost_bps_per_side.
    """
    horizon = horizon or cfg.horizon
    step = max(1, math.ceil(horizon / cfg.sample_every))   # rebalance dates far enough apart that holding periods do not overlap
    dates = np.sort(df["idx"].unique())
    rebal = set(dates[::step])
    d = df[df["idx"].isin(rebal) & df["exec_excess_return"].notna()].copy()
    d["pos"] = np.where(d["probability"] >= 0.5 + cfg.signal_band, 1, np.where(d["probability"] <= 0.5 - cfg.signal_band, -1, 0))
    d = d[d["pos"] != 0]
    cost = 4 * cfg.cost_bps_per_side / 1e4
    if d.empty:
        return {"periods": 0, "note": "no positions passed the signal band"}
    d["gross"] = d["pos"] * d["exec_excess_return"]
    d["net"] = d["gross"] - cost
    per = d.groupby("idx").agg(gross=("gross", "mean"), net=("net", "mean"), n=("pos", "size"))
    n = len(per)
    periods_per_year = 252 / horizon
    sd = per["net"].std(ddof=1) if n > 1 else float("nan")
    t = per["net"].mean() / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    return {"periods": int(n), "meanGross": float(per["gross"].mean()), "meanNet": float(per["net"].mean()),
            "tStatNet": float(t), "hitRate": float((per["net"] > 0).mean()),
            "annualizedNet": float(per["net"].mean() * periods_per_year),
            "sharpeNet": float(per["net"].mean() / sd * math.sqrt(periods_per_year)) if sd and sd > 0 else float("nan"),
            "avgPositions": float(per["n"].mean()), "costPerPositionPerPeriod": cost,
            "turnoverCostPerPeriod": cost}


def date_block_bootstrap(values: np.ndarray, date_idx: np.ndarray, block: int = 21, n_boot: int = 500,
                         seed: int = 11) -> tuple[float, float]:
    """95% CI of the mean of `values`, resampling blocks of `block` consecutive dates (forecasts on neighbouring
    days share most of their outcome window, so single rows are not independent)."""
    if len(values) == 0:
        return float("nan"), float("nan")
    dates = np.unique(date_idx)
    per = pd.Series(values).groupby(date_idx).agg(["sum", "count"]).reindex(dates)
    s, c = per["sum"].to_numpy(), per["count"].to_numpy()
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(dates) // block)
    starts = rng.integers(0, max(1, len(dates) - block + 1), size=(n_boot, n_blocks))
    pick = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1) % len(dates)
    boots = s[pick].sum(axis=1) / c[pick].sum(axis=1)
    return float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def coverage_curve(p: np.ndarray, y: np.ndarray, excess: np.ndarray, date_idx: np.ndarray, cost_bps_per_side: float,
                   cost_legs: int = 4, side: str = "both", levels: tuple = COVERAGE_LEVELS, block: int = 21) -> list[dict]:
    """Abstention: act only on the most confident forecasts and see what that would have earned.

    Forecasts are ranked by confidence, |p - 0.5| (side="both": long when p > 0.5, short when p < 0.5) or by p
    (side="long": long only, the most probable outperformers first). Each level keeps the top `coverage` share of
    all forecasts and reports the confidence needed to get in, the accuracy of the direction call, the Brier score,
    and the mean excess return over the sector ETF of acting on them, gross and net of `cost_legs` x
    cost_bps_per_side per position (4 legs for a stock position hedged with its ETF, 2 for the stock alone), with
    a date-block bootstrap 95% CI of the net figure. Rows without a known return are dropped.
    """
    ok = np.isfinite(p) & np.isfinite(y) & np.isfinite(excess)
    p, y, excess, date_idx = p[ok], y[ok], excess[ok], date_idx[ok]
    if len(p) == 0:
        return []
    if side == "long":
        conf = p.copy()
        direction = np.ones_like(p)
    else:
        conf = np.abs(p - 0.5)
        direction = np.where(p >= 0.5, 1.0, -1.0)
    order = np.argsort(-conf, kind="stable")
    cost = cost_legs * cost_bps_per_side / 1e4
    right = ((p > 0.5) == (y > 0.5)).astype(float)
    sq = (p - y) ** 2
    gross = direction * excess
    net = gross - cost
    out = []
    for level in levels:
        n = int(max(1, round(level * len(p))))
        top = order[:n]
        lo, hi = date_block_bootstrap(net[top], date_idx[top], block=block)
        out.append({"coverage": float(level), "n": n, "minConfidence": float(conf[top].min()),
                    "accuracy": float(right[top].mean()), "brier": float(sq[top].mean()),
                    "meanGross": float(gross[top].mean()), "meanNet": float(net[top].mean()),
                    "ciLow": lo, "ciHigh": hi, "costPerPosition": cost, "side": side})
    return out


def verdict(metrics: dict, comparison: dict, trading: dict, cfg: EvalConfig, models: dict | None = None) -> str:
    models = models or {}
    fs = {k: (models.get(k) or {}).get("featureSet", FEATURE_SET_IDS.get(k, k)) for k in metrics}
    if not all(k in metrics for k in KINDS):
        return " ".join(f"{k} ({fs[k]}): Brier skill {m['brierSkill']:+.3f}, AUC {m['auc']:.3f} on {m['n']} predictions."
                        for k, m in metrics.items())
    a, b = metrics["AUGMENTED"], metrics["BASELINE"]
    parts = [f"Walk-forward, {a['n']} out-of-sample predictions per model."]
    parts.append(f"Brier skill vs base rate: baseline ({fs['BASELINE']}) {b['brierSkill']:+.3f}, augmented ({fs['AUGMENTED']}) "
                 f"{a['brierSkill']:+.3f}; AUC baseline {b['auc']:.3f}, augmented {a['auc']:.3f}.")
    for k, m in metrics.items():
        if k in KINDS:
            continue
        info = models.get(k) or {}
        parts.append(f"{k} ({fs[k]}, the recorded book's inputs, {info.get('horizon', '?')}-day label entered at {info.get('entry', '?')}): "
                     f"Brier skill {m['brierSkill']:+.3f}, AUC {m['auc']:.3f} on {m['n']} predictions.")
    if comparison["ciHigh"] < 0:
        parts.append("The augmented model's Brier score is lower than the baseline's and the 95% CI excludes zero.")
    elif comparison["ciLow"] > 0:
        parts.append("The augmented model is WORSE than the baseline (95% CI excludes zero).")
    else:
        parts.append("The difference between the augmented and baseline models is not statistically distinguishable from zero.")
    if comparison.get("foldsCompared"):
        parts.append(f"Augmented better in {comparison['foldsAugmentedBetter']} of {comparison['foldsCompared']} folds "
                     f"(sign test p = {comparison['signTestP']:.2f}).")
    tr = trading["AUGMENTED"]
    if tr.get("periods", 0) == 0:
        parts.append("No trading periods passed the signal band; no profitability evidence.")
    else:
        supported = tr["periods"] >= 36 and tr["meanNet"] > 0 and tr["tStatNet"] > 2.0
        parts.append(f"Long/short simulation after {cfg.cost_bps_per_side:.0f} bp per side per leg: mean net {tr['meanNet']*100:+.2f}% per "
                     f"{cfg.horizon}-day period over {tr['periods']} periods (t = {tr['tStatNet']:.2f}).")
        parts.append("Profitability claim: " + ("the simulation is consistent with positive after-cost returns, but this is a backtest, "
                     "not evidence of live profitability." if supported else "NOT supported by this evidence."))
    parts.append(coverage_sentence(tr.get("coverage") or [], "augmented forecasts"))
    return " ".join(parts).strip()


def coverage_sentence(curve: list[dict], what: str, level: float = 0.10) -> str:
    """One sentence on abstention: what acting only on the most confident `level` share would have earned."""
    row = next((r for r in curve if abs(r["coverage"] - level) < 1e-9), None)
    if row is None or row["n"] < 30:
        return ""
    verdict = ("positive after costs" if row["ciLow"] > 0 else "negative after costs" if row["ciHigh"] < 0
               else "within noise")
    return (f"Acting only on the most confident {level:.0%} of {what} ({row['n']:,} calls): "
            f"{row['accuracy']:.0%} right, mean excess return {row['meanNet']*100:+.2f}% per position after costs "
            f"(95% CI {row['ciLow']*100:+.2f}% to {row['ciHigh']*100:+.2f}%): {verdict}.")
