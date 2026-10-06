"""Scoring of the pre-registered live test (docs/research/live-test-2026-10.md).

Everything here is fixed by that document: the population query, the metrics, the bootstrap and the verdict rule. Change
the document first, in a dated section, and only then this file. Run against the platform's database with

    python -m civalpha.livetest            # JSON report per model

The functions take plain DataFrames so the arithmetic is testable without a database.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

REGISTERED_ON = "2026-10-06"
REGISTRATION_CUTOFF = "2026-10-06 11:00:00+00"      # versions issued at or after this instant are not part of the test
AS_OF_DATES = ("2026-10-02", "2026-10-05")
MODELS = ("BASELINE", "AUGMENTED")
EXPECTED_PER_MODEL = 702
MIN_RESOLVED_SHARE = 0.95
CONFIDENT_SHARE = 0.10
COST_PER_POSITION = 4 * 10.0 / 1e4                   # 4 legs (stock and ETF hedge, in and out) x 10 bp per side
N_BOOT = 2000
SEED = 20261006
FINGERPRINT = {"BASELINE": "73af3afc82c5eeb529e0739e9373ac90", "AUGMENTED": "0010f38379eec1e1489b32b9b4cf34f3"}

POPULATION_SQL = f"""
WITH scored AS (
  SELECT DISTINCT ON (series_key) id, series_key, company_id, symbol, model_kind, as_of_date, probability::float8 AS p,
         model_version_id, version, issued_at
  FROM forecast
  WHERE issue_mode = 'LIVE' AND model_kind IN ('BASELINE', 'AUGMENTED')
    AND as_of_date IN ('{AS_OF_DATES[0]}', '{AS_OF_DATES[1]}')
    AND issued_at < TIMESTAMPTZ '{REGISTRATION_CUTOFF}'
  ORDER BY series_key, version DESC)
SELECT s.*, o.outcome, o.excess_return::float8 AS excess_return, o.window_end_date
FROM scored s LEFT JOIN forecast_outcome o ON o.forecast_id = s.id
ORDER BY s.model_kind, s.as_of_date, s.company_id
"""


def fingerprint(rows: pd.DataFrame) -> dict:
    """md5 of 'series_key:version' joined by commas in series_key order, per model: must equal FINGERPRINT."""
    import hashlib
    out = {}
    for kind, g in rows.groupby("model_kind"):
        g = g.sort_values("series_key")
        s = ",".join(f"{k}:{int(v)}" for k, v in zip(g["series_key"], g["version"]))
        out[kind] = hashlib.md5(s.encode()).hexdigest()
    return out


def _metrics(p: np.ndarray, y: np.ndarray, excess: np.ndarray) -> dict:
    r = float(y.mean())
    ref = r * (1.0 - r)
    b = float(np.mean((p - y) ** 2))
    skill = 1.0 - b / ref if ref > 0 else float("nan")
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    conf = np.abs(p - 0.5)
    n_top = max(1, int(round(CONFIDENT_SHARE * len(p))))
    top = np.argsort(-conf, kind="stable")[:n_top]
    direction = np.where(p[top] >= 0.5, 1.0, -1.0)
    hit = float(np.mean((p[top] > 0.5) == (y[top] > 0.5)))
    net = direction * excess[top] - COST_PER_POSITION
    return {"n": int(len(p)), "baseRate": r, "brier": b, "brierReference": ref, "brierSkill": skill, "auc": auc,
            "confidentN": int(n_top), "confidentMinAbs": float(conf[top].min()), "confidentHitRate": hit,
            "confidentMeanNetExcess": float(np.nanmean(net))}


def score_model(rows: pd.DataFrame, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """Metrics and cluster-bootstrap CIs for one model's resolved forecasts.

    rows: one model's population (resolved or not) with columns company_id, p, outcome, excess_return. The cluster is the
    company: its two forecasts (as-of 2026-10-02 and 2026-10-05) share 20 of 21 sessions of their outcome window, so
    they are resampled together. Companies are drawn with replacement n_boot times; the CI is the 2.5th and 97.5th
    percentile of each metric over the resamples.
    """
    issued = int(len(rows))
    res = rows[rows["outcome"].notna()].copy()
    out = {"issued": issued, "resolved": int(len(res)), "resolvedShare": len(res) / issued if issued else 0.0}
    if len(res) == 0 or res["outcome"].nunique() < 2:
        return {**out, "verdict": "PENDING"}
    p = res["p"].to_numpy(float)
    y = res["outcome"].astype(float).to_numpy()
    ex = res["excess_return"].to_numpy(float)
    point = _metrics(p, y, ex)
    clusters = res["company_id"].to_numpy()
    uniq = np.unique(clusters)
    groups = [np.flatnonzero(clusters == u) for u in uniq]
    rng = np.random.default_rng(seed)
    boots = {k: [] for k in ("brierSkill", "auc", "confidentHitRate", "confidentMeanNetExcess")}
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), size=len(groups))
        idx = np.concatenate([groups[i] for i in pick])
        if len(np.unique(y[idx])) < 2:
            continue
        m = _metrics(p[idx], y[idx], ex[idx])
        for k in boots:
            boots[k].append(m[k])
    ci = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if v else [float("nan")] * 2 for k, v in boots.items()}
    out.update(point)
    out["ci95"] = ci
    out["verdict"] = verdict(out)
    return out


def verdict(m: dict) -> str:
    """INCOMPLETE until 95% of the issued forecasts have resolved; then SKILL if the Brier-skill CI is above zero and
    AUC > 0.5, HARMFUL if the Brier-skill CI is below zero, otherwise NO_SKILL."""
    if m.get("resolvedShare", 0.0) < MIN_RESOLVED_SHARE:
        return "INCOMPLETE"
    lo, hi = m["ci95"]["brierSkill"]
    if lo > 0 and m["auc"] > 0.5:
        return "SKILL"
    if hi < 0:
        return "HARMFUL"
    return "NO_SKILL"


def paired_difference(a: pd.DataFrame, b: pd.DataFrame, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """Secondary: Brier(AUGMENTED) - Brier(BASELINE) on the same resolved (company, as-of) rows, same cluster bootstrap."""
    key = ["company_id", "as_of_date"]
    j = a[a["outcome"].notna()][key + ["p", "outcome"]].merge(b[b["outcome"].notna()][key + ["p"]], on=key, suffixes=("_a", "_b"))
    if j.empty:
        return {"n": 0}
    y = j["outcome"].astype(float).to_numpy()
    d = (j["p_a"].to_numpy(float) - y) ** 2 - (j["p_b"].to_numpy(float) - y) ** 2
    clusters = j["company_id"].to_numpy()
    uniq = np.unique(clusters)
    groups = [np.flatnonzero(clusters == u) for u in uniq]
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), size=len(groups))
        boots.append(float(d[np.concatenate([groups[i] for i in pick])].mean()))
    return {"n": int(len(d)), "brierDiff": float(d.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "maxAbsProbDiff": float(np.abs(j["p_a"] - j["p_b"]).max())}


def report(rows: pd.DataFrame) -> dict:
    fp = fingerprint(rows)
    out = {"registeredOn": REGISTERED_ON, "registrationCutoff": REGISTRATION_CUTOFF, "fingerprintMatches": fp == FINGERPRINT,
           "fingerprint": fp, "models": {}}
    by = {k: g for k, g in rows.groupby("model_kind")}
    for kind in MODELS:
        g = by.get(kind)
        out["models"][kind] = score_model(g) if g is not None else {"issued": 0, "verdict": "PENDING"}
    if "AUGMENTED" in by and "BASELINE" in by:
        out["augmentedMinusBaseline"] = paired_difference(by["AUGMENTED"], by["BASELINE"])
    return out


def main() -> None:
    from sqlalchemy import text

    from civalpha.platform.sql import engine
    with engine().connect() as c:
        rows = pd.read_sql(text(POPULATION_SQL), c)
    json.dump(report(rows), sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
