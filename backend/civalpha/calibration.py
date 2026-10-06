"""Calibration study on the stored walk-forward predictions: does mapping probabilities through an isotonic curve fitted on
PAST folds only change reliability, Brier skill, and what the most confident decile earns?

Research only. Nothing here touches the live models (docs/research/2026-10-forecasting-polish.md, task 8). The data are
the backtest_prediction rows of one model_evaluation: every (company, as-of date, model) scored once by a model that never
saw its period. For fold k the isotonic map is fitted on the predictions and outcomes of folds 0..k-1, so the calibrated
probability of a row only uses outcomes that had resolved before its own test block began (the walk-forward purge is
already in the folds). The first `min_prior_folds` folds have no map and are left out of both the before and the after
columns, so the two are compared on the same rows.

    python -m civalpha.calibration            # latest evaluation, JSON report
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

MIN_PRIOR_FOLDS = 3
COST_PER_POSITION = 4 * 10.0 / 1e4          # stock + ETF hedge, in and out, 10 bp per side: as the walk-forward coverage curve
CONFIDENT_SHARE = 0.10
N_BINS = 10


def isotonic_forward(df: pd.DataFrame, min_prior_folds: int = MIN_PRIOR_FOLDS) -> pd.Series:
    """Calibrated probability per row, NaN where fewer than `min_prior_folds` earlier folds exist. Columns: fold,
    probability, outcome. Fitted on earlier folds only, so later outcomes never shape an earlier row's map."""
    out = pd.Series(np.nan, index=df.index)
    folds = sorted(df["fold"].unique())
    for k in folds:
        prior = df[df["fold"] < k]
        if prior["fold"].nunique() < min_prior_folds or prior["outcome"].nunique() < 2:
            continue
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(prior["probability"].to_numpy(float), prior["outcome"].astype(float).to_numpy())
        rows = df["fold"] == k
        out[rows] = iso.predict(df.loc[rows, "probability"].to_numpy(float))
    return out


def reliability(p: np.ndarray, y: np.ndarray, n_bins: int = N_BINS) -> list[dict]:
    edges = np.linspace(0, 1, n_bins + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.sum():
            rows.append({"binLow": float(lo), "binHigh": float(hi), "count": int(m.sum()),
                         "meanPredicted": float(p[m].mean()), "observedRate": float(y[m].mean()),
                         "gap": float(p[m].mean() - y[m].mean())})
    return rows


def expected_calibration_error(bins: list[dict], n: int) -> float:
    return float(sum(b["count"] * abs(b["gap"]) for b in bins) / n) if n else float("nan")


def _scores(p: np.ndarray, y: np.ndarray, excess: np.ndarray, base_rate: float) -> dict:
    ref = base_rate * (1 - base_rate)
    brier = float(np.mean((p - y) ** 2))
    conf = np.abs(p - 0.5)
    n_top = max(1, int(round(CONFIDENT_SHARE * len(p))))
    top = np.argsort(-conf, kind="stable")[:n_top]
    direction = np.where(p[top] >= 0.5, 1.0, -1.0)
    bins = reliability(p, y)
    return {"n": int(len(p)), "brier": brier, "brierSkill": 1 - brier / ref if ref > 0 else float("nan"),
            "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan"),
            "meanPredicted": float(p.mean()), "spread": float(p.std()),
            "ece": expected_calibration_error(bins, len(p)), "reliability": bins,
            "confident": {"n": int(n_top), "minAbs": float(conf[top].min()),
                          "hitRate": float(np.mean((p[top] > 0.5) == (y[top] > 0.5))),
                          "meanGrossExcess": float(np.mean(direction * excess[top])),
                          "meanNetExcess": float(np.mean(direction * excess[top]) - COST_PER_POSITION),
                          "longShare": float(np.mean(direction > 0))}}


def block_ci_of_difference(d: np.ndarray, dates: np.ndarray, block: int = 21, n_boot: int = 500, seed: int = 11) -> list[float]:
    """95% CI of the mean of d from a bootstrap over blocks of `block` consecutive as-of dates."""
    uniq = np.unique(dates)
    groups = [np.flatnonzero(dates == u) for u in uniq]
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(uniq) // block)
    boots = []
    for _ in range(n_boot):
        starts = rng.integers(0, max(1, len(uniq) - block + 1), size=n_blocks)
        sel = np.concatenate([groups[(s + k) % len(uniq)] for s in starts for k in range(block)])
        boots.append(float(d[sel].mean()))
    return [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]


def study_model(df: pd.DataFrame, min_prior_folds: int = MIN_PRIOR_FOLDS) -> dict:
    """df: one model's rows with fold, as_of_date, probability, outcome, excess_return."""
    df = df.sort_values(["fold", "as_of_date"]).reset_index(drop=True)
    cal = isotonic_forward(df, min_prior_folds)
    ok = cal.notna() & df["excess_return"].notna()
    d = df[ok]
    if d.empty or d["outcome"].nunique() < 2:
        return {"n": 0, "note": "not enough folds to calibrate"}
    y = d["outcome"].astype(float).to_numpy()
    ex = d["excess_return"].to_numpy(float)
    base = float(y.mean())
    before = _scores(d["probability"].to_numpy(float), y, ex, base)
    after = _scores(cal[ok].to_numpy(float), y, ex, base)
    diff = (cal[ok].to_numpy(float) - y) ** 2 - (d["probability"].to_numpy(float) - y) ** 2
    dates = pd.to_datetime(d["as_of_date"]).to_numpy().astype("datetime64[D]").astype(np.int64)
    return {"n": int(len(d)), "foldsScored": sorted(int(f) for f in d["fold"].unique()), "minPriorFolds": min_prior_folds,
            "baseRate": base, "before": before, "after": after,
            "brierDiff": float(diff.mean()), "brierDiffCi95": block_ci_of_difference(diff, dates),
            "note": "brierDiff = Brier(calibrated) - Brier(raw) on the same rows; negative means calibration helped. "
                    "Excess returns are the label window, close(t) to close(t+21), minus 40 bp per position."}


def report(P: pd.DataFrame, evaluation_id: int | None = None) -> dict:
    out = {"evaluationId": evaluation_id, "models": {}}
    for kind, g in P.groupby("model_kind"):
        out["models"][kind] = study_model(g)
    out["summary"] = summary(out["models"])
    return out


def summary(models: dict) -> str:
    parts = []
    for kind, m in models.items():
        if not m.get("n"):
            continue
        b, a = m["before"], m["after"]
        lo, hi = m["brierDiffCi95"]
        verdict = "helped" if hi < 0 else "hurt" if lo > 0 else "changed nothing distinguishable from zero"
        parts.append(f"{kind}: isotonic calibration fitted on past folds {verdict} (Brier {b['brier']:.4f} -> {a['brier']:.4f}, "
                     f"skill {b['brierSkill']:+.3f} -> {a['brierSkill']:+.3f}, 95% CI of the difference {lo:+.4f} to {hi:+.4f}); "
                     f"ECE {b['ece']:.3f} -> {a['ece']:.3f}; the most confident 10% hit {b['confident']['hitRate']:.0%} -> "
                     f"{a['confident']['hitRate']:.0%} with mean net excess {b['confident']['meanNetExcess']*100:+.2f}% -> "
                     f"{a['confident']['meanNetExcess']*100:+.2f}% per position on {m['n']} rows.")
    return " ".join(parts)


def main() -> None:
    from sqlalchemy import text

    from civalpha.platform.sql import engine
    eid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with engine().connect() as c:
        if eid is None:
            eid = int(c.execute(text("SELECT max(id) FROM model_evaluation")).scalar_one())
        P = pd.read_sql(text("SELECT model_kind, fold, as_of_date, probability::float8 AS probability, outcome, excess_return::float8 AS excess_return "
                             "FROM backtest_prediction WHERE evaluation_id = :e"), c, params={"e": eid})
    json.dump(report(P, eid), sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
