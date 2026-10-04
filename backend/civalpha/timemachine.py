"""Time machine: forecast as of a past close using only what was known then, then compare with what happened.

`predict_as_of` decides everything at close(as-of) — models are trained only on outcomes that had resolved by
then and every input is read point-in-time — and never looks at later prices (tests/test_timemachine.py checks
this by changing all later prices). `realized` then reads the later prices to score the predictions:

  * odds   P(stock beats its sector ETF over h days), BASELINE and AUGMENTED logistic models refitted per horizon;
  * ai     the AI strategy's ENTER/STAY_OUT decisions that day (no earlier holdings), executed at the next close;
  * range  10th/50th/90th percentile of the stock's total return over h days (quantile gradient boosting on the
           AI's feature set), compared with a naive band from the unconditional training quantiles.

One date is only ~one outcome per stock: the summary says so, and multi-year evidence lives in the walk-forward
evaluation and the strategy lab.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score

from . import db, pit
from .features import FEATURES, MIN_HISTORY, DataBundle, build_panel, build_rows
from .model import LogitModel
from .strategies.ai import AI_FEATURES, AiConfig, dataset
from .strategies.panel import MarketPanel
from .strategies.service import LabConfig, decisions_at

log = logging.getLogger(__name__)

HORIZONS = (5, 10, 21, 63)
QUANTILES = (0.1, 0.5, 0.9)
RANGE_SAMPLE_EVERY = 3
MIN_TRAIN = 200
KINDS = ("BASELINE", "AUGMENTED")


# --------------------------------------------------------------------------- models
class QuantileModel:
    """Quantile gradient boosting; features with no values in the training window are neutralized (as AiModel)."""

    def __init__(self, q: float, seed: int = 7):
        self.reg = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=150, learning_rate=0.05, max_depth=3,
                                                 min_samples_leaf=100, early_stopping=False, random_state=seed)
        self.empty = np.zeros(0, dtype=bool)

    def _prep(self, X):
        X = np.array(X, dtype=float, copy=True)
        X[:, self.empty] = 0.0
        return X

    def fit(self, X, y):
        self.empty = np.isnan(X).all(axis=0)
        self.reg.fit(self._prep(X), y)
        return self

    def predict(self, X):
        return self.reg.predict(self._prep(X))


def _excess(bundle: DataBundle, cid: int, bsym: str, i: int, h: int) -> float:
    s, b = bundle.tr.get(cid), bundle.bench_tr.get(bsym)
    if s is None or b is None or i + h >= len(s) or i < 0:
        return np.nan
    v = (s[i + h] / s[i]) - (b[i + h] / b[i])
    return float(v) if np.isfinite(v) else np.nan


# --------------------------------------------------------------------------- predictions (data <= as-of only)
def predict_as_of(bundle: DataBundle, idx: int, horizons=HORIZONS, panel: MarketPanel | None = None) -> dict:
    d = bundle.calendar[idx]
    if idx < MIN_HISTORY + max(horizons):
        raise ValueError(f"{d.date()} is too early: the models need more price history before it")
    panel = MarketPanel.from_bundle(bundle) if panel is None else panel
    out: dict = {"asOfDate": str(d.date()), "horizons": list(horizons), "stocks": {}, "models": {}}

    # 1) odds: logistic models refitted per horizon on labels resolved by close(idx)
    hist = build_panel(bundle)
    hist = hist[hist["idx"] <= idx].copy()
    now = pd.DataFrame(build_rows(bundle, idx, with_labels=False))
    if now.empty:
        raise ValueError(f"no universe member has enough history on {d.date()}")
    for h in horizons:
        hist[f"y{h}"] = [_excess(bundle, int(r.company_id), r.benchmark_symbol, int(r.idx), h) if r.idx + h <= idx else np.nan
                         for r in hist[["company_id", "benchmark_symbol", "idx"]].itertuples(index=False)]
        train = hist[hist[f"y{h}"].notna()]
        info = {"nTrain": int(len(train))}
        if len(train) < MIN_TRAIN or (train[f"y{h}"] > 0).nunique() < 2:
            out["models"][f"odds{h}"] = {**info, "skipped": "not enough resolved history"}
            continue
        y = train[f"y{h}"] > 0
        info["baseRate"] = float(y.mean())
        info["trainedThrough"] = str(bundle.calendar[int(train["idx"].max())].date())
        for kind in KINDS:
            m = LogitModel(FEATURES[kind], n_boot=0).fit(train, y, groups=train["idx"])
            for cid, p in zip(now["company_id"], m.predict(now)):
                out["stocks"].setdefault(int(cid), {}).setdefault("odds", {}).setdefault(str(h), {})[kind] = float(p)
        out["models"][f"odds{h}"] = info

    # 2) the AI strategy's decisions that day (as if starting fresh, with no earlier holdings)
    ai_cfg = AiConfig()
    data = dataset(panel, ai_cfg)
    try:
        for dec in decisions_at(bundle, idx, LabConfig(), held=set(), panel=panel, data=data):
            out["stocks"].setdefault(dec["companyId"], {})["ai"] = {
                "action": dec["action"], "probability": dec["probability"], "rank": dec["rank"], "weight": dec["weight"],
                "factors": dec["factors"][:3]}
        out["models"]["ai"] = {"horizon": ai_cfg.horizon, "entryP": ai_cfg.entry_p, "exitP": ai_cfg.exit_p,
                               "maxPositions": ai_cfg.max_positions}
    except ValueError as e:
        out["models"]["ai"] = {"skipped": str(e)}

    # 3) return range: quantile models of the h-day total return
    rows_now = data[data["idx"] == idx]
    px = panel.px.to_numpy(float)
    col = panel.px.columns.get_indexer(data["company_id"].to_numpy())
    di = data["idx"].to_numpy()
    for h in horizons:
        end = di + h
        ok = (end <= idx) & (di % RANGE_SAMPLE_EVERY == 0)
        tgt = np.full(len(data), np.nan)
        tgt[ok] = px[end[ok], col[ok]] / px[di[ok], col[ok]] - 1.0
        tr_mask = np.isfinite(tgt)
        if tr_mask.sum() < MIN_TRAIN or rows_now.empty:
            out["models"][f"range{h}"] = {"nTrain": int(tr_mask.sum()), "skipped": "not enough resolved history"}
            continue
        X, y = data.loc[tr_mask, AI_FEATURES].to_numpy(float), tgt[tr_mask]
        Xn = rows_now[AI_FEATURES].to_numpy(float)
        preds = np.sort(np.column_stack([QuantileModel(q).fit(X, y).predict(Xn) for q in QUANTILES]), axis=1)
        naive = np.quantile(y, QUANTILES)
        for cid, (lo, mid, hi) in zip(rows_now["company_id"], preds):
            out["stocks"].setdefault(int(cid), {}).setdefault("range", {})[str(h)] = {
                "q10": float(lo), "q50": float(mid), "q90": float(hi),
                "naiveQ10": float(naive[0]), "naiveQ50": float(naive[1]), "naiveQ90": float(naive[2])}
        out["models"][f"range{h}"] = {"nTrain": int(tr_mask.sum()), "sampleEvery": RANGE_SAMPLE_EVERY}

    comp = bundle.companies.set_index("id")
    for cid, s in out["stocks"].items():
        c = comp.loc[cid]
        close = bundle.close.get(cid)
        s.update({"companyId": cid, "symbol": str(c["symbol"]), "name": str(c.get("name", c["symbol"])),
                  "benchmarkSymbol": str(c["benchmark_symbol"]),
                  "closeAsOf": float(close[idx]) if close is not None and np.isfinite(close[idx]) else None})
    return out


# --------------------------------------------------------------------------- facts (later data)
def realized(bundle: DataBundle, idx: int, pred: dict) -> dict:
    """What actually happened after close(idx): returns per horizon and the daily path, per stock."""
    T = len(bundle.calendar)
    hmax = max(pred["horizons"])
    for cid, s in pred["stocks"].items():
        tr, b = bundle.tr.get(cid), bundle.bench_tr.get(s["benchmarkSymbol"])
        act = {}
        for h in pred["horizons"]:
            if tr is None or b is None or idx + h >= T:
                act[str(h)] = None  # not known yet
                continue
            sr, br = tr[idx + h] / tr[idx] - 1.0, b[idx + h] / b[idx] - 1.0
            er = (tr[idx + 1 + h] / tr[idx + 1] - 1.0) if idx + 1 + h < T else np.nan   # entered at the next close
            eb = (b[idx + 1 + h] / b[idx + 1] - 1.0) if idx + 1 + h < T else np.nan
            act[str(h)] = {"stockReturn": float(sr), "benchmarkReturn": float(br), "excess": float(sr - br), "beat": bool(sr > br),
                           "execReturn": None if np.isnan(er) else float(er), "execBenchmarkReturn": None if np.isnan(eb) else float(eb),
                           "endDate": str(bundle.calendar[idx + h].date())}
        s["actual"] = act
        last = min(idx + hmax, T - 1)
        s["path"] = [] if tr is None else [
            {"date": str(bundle.calendar[k].date()), "stock": float(tr[k] / tr[idx] - 1.0),
             "benchmark": float(b[k] / b[idx] - 1.0) if b is not None else None} for k in range(idx, last + 1)]
    return pred


def summarize(res: dict) -> dict:
    stocks = list(res["stocks"].values())
    out = {}
    for h in res["horizons"]:
        k = str(h)
        done = [s for s in stocks if (s.get("actual") or {}).get(k)]
        hs: dict = {"resolved": len(done), "endDate": done[0]["actual"][k]["endDate"] if done else None}
        odds = {}
        for kind in KINDS:
            pts = [(s["odds"][k][kind], s["actual"][k]) for s in done if kind in s.get("odds", {}).get(k, {})]
            if not pts:
                continue
            p = np.array([x[0] for x in pts])
            y = np.array([1.0 if x[1]["beat"] else 0.0 for x in pts])
            ex = np.array([x[1]["excess"] for x in pts])
            base = res["models"].get(f"odds{h}", {}).get("baseRate", 0.5)
            order = np.argsort(-p)
            n5 = min(5, len(p) // 2)
            odds[kind] = {"n": len(p), "hitRate": float(np.mean((p > 0.5) == (y > 0.5))), "brier": float(np.mean((p - y) ** 2)),
                          "brierBaseRate": float(np.mean((base - y) ** 2)),
                          "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
                          "topMinusBottomExcess": float(ex[order[:n5]].mean() - ex[order[-n5:]].mean()) if n5 else None}
        hs["odds"] = odds
        rng = [(s["range"][k], s["actual"][k]["stockReturn"]) for s in done if k in s.get("range", {})]
        if rng:
            inside = [r["q10"] <= a <= r["q90"] for r, a in rng]
            naive = [r["naiveQ10"] <= a <= r["naiveQ90"] for r, a in rng]
            hs["range"] = {"n": len(rng), "coverage": float(np.mean(inside)), "naiveCoverage": float(np.mean(naive)),
                           "target": 0.8, "medianAbsError": float(np.median([abs(a - r["q50"]) for r, a in rng])),
                           "naiveMedianAbsError": float(np.median([abs(a - r["naiveQ50"]) for r, a in rng])),
                           "avgWidth": float(np.mean([r["q90"] - r["q10"] for r, _ in rng])),
                           "naiveWidth": float(np.mean([r["naiveQ90"] - r["naiveQ10"] for r, _ in rng]))}
        execd = [s for s in done if s["actual"][k].get("execReturn") is not None and "ai" in s]
        if execd:
            picks = [s for s in execd if s["ai"]["action"] == "ENTER"]
            uni = float(np.mean([s["actual"][k]["execReturn"] for s in execd]))
            ai = {"universeReturn": uni, "picks": [s["symbol"] for s in picks]}
            if picks:
                pr = float(np.mean([s["actual"][k]["execReturn"] for s in picks]))
                ai.update({"picksReturn": pr, "excessVsUniverse": pr - uni,
                           "picksBeatSector": int(sum(s["actual"][k]["execReturn"] > s["actual"][k]["execBenchmarkReturn"] for s in picks))})
            hs["ai"] = ai
        out[k] = hs
    return out


def headline(res: dict) -> str:
    sm = res["summary"]
    parts = [f"As of the close of {res['asOfDate']}, using only data known then."]
    k = "21" if "21" in sm else str(res["horizons"][0])
    h = sm.get(k, {})
    if not h.get("resolved"):
        return " ".join(parts + [f"The {k}-day outcomes are not known yet."])
    o = h.get("odds", {}).get("AUGMENTED")
    if o:
        parts.append(f"{k}-day beat-the-sector odds: {o['hitRate'] * 100:.0f}% of {o['n']} calls right "
                     f"(Brier {o['brier']:.3f} vs {o['brierBaseRate']:.3f} for always predicting the base rate).")
    a = h.get("ai")
    if a and "picksReturn" in a:
        parts.append(f"AI picks ({', '.join(a['picks'])}) returned {a['picksReturn'] * 100:+.1f}% vs {a['universeReturn'] * 100:+.1f}% "
                     f"for the whole universe.")
    elif a:
        parts.append("The AI entered no stock that day.")
    r = h.get("range")
    if r:
        parts.append(f"The 10–90% return band held {r['coverage'] * 100:.0f}% of actual {k}-day returns "
                     f"(target 80%; naive band {r['naiveCoverage'] * 100:.0f}%).")
    parts.append("One date is one draw: treat this as an illustration, not evidence.")
    return " ".join(parts)


def run(engine, as_of: str) -> dict:
    bundle = db.load_bundle(engine)
    d = pit.last_trading_date_at(bundle.calendar, pit.close_ts(pd.Timestamp(as_of)))
    if d is None:
        raise ValueError(f"no trading day on or before {as_of}")
    idx = int(bundle.calendar.get_loc(d))
    res = realized(bundle, idx, predict_as_of(bundle, idx))
    res["summary"] = summarize(res)
    res["headline"] = headline(res)
    res["stocks"] = sorted(res["stocks"].values(), key=lambda s: s["symbol"])
    res["dataCutoff"] = str(bundle.calendar[-1].date())
    is_demo = bool(bundle.companies["is_demo"].any()) if "is_demo" in bundle.companies else False
    run_id = db.insert_time_machine(engine, res, is_demo)
    return {"runId": run_id, "headline": res["headline"]}
