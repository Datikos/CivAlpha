"""Strategy lab orchestration: backtest every strategy on one out-of-sample window, and today's AI decisions."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from sklearn.metrics import roc_auc_score

from .. import db, pit
from ..evaluation import brier, log_loss
from ..features import DataBundle
from . import backtest, stats
from .ai import (AI_DIV_FEATURES, AI_FEATURES, AI_KEY, FUND_MODEL_FEATURES, ALGORITHM, CODE_VERSION, AiConfig, ai_strategies, dataset,
                 decide_positions, explain, new_model, walk_forward_probabilities)
from .base import Strategy
from .panel import MarketPanel
from .rules import rule_strategies, rule_votes

log = logging.getLogger(__name__)

REFERENCE = "EW_BUY_HOLD"
COST_SENSITIVITY_BPS = (0.0, 10.0, 25.0)


@dataclass
class LabConfig:
    cost_bps_per_side: float = 10.0
    max_positions: int = 8
    entry_p: float = 0.55
    exit_p: float = 0.48

    def ai(self) -> AiConfig:
        return AiConfig(entry_p=self.entry_p, exit_p=self.exit_p, max_positions=self.max_positions)


# --------------------------------------------------------------------------- backtest
def run_lab(bundle: DataBundle, cfg: LabConfig) -> dict:
    panel = MarketPanel.from_bundle(bundle)
    ai_cfg = cfg.ai()
    data = dataset(panel, ai_cfg)
    wf = walk_forward_probabilities(panel, ai_cfg, data)
    oos = wf["oos_start_idx"]
    ai_w, _ = decide_positions(wf["prob"], panel.member, ai_cfg, start_idx=oos)
    wf_fund = walk_forward_probabilities(panel, ai_cfg, data, features=FUND_MODEL_FEATURES)
    fund_w, _ = decide_positions(wf_fund["prob"], panel.member, ai_cfg, start_idx=oos)
    wf_div = walk_forward_probabilities(panel, ai_cfg, data, features=AI_DIV_FEATURES)
    div_w, _ = decide_positions(wf_div["prob"], panel.member, ai_cfg, start_idx=oos)
    feature_test = compare_forecasts(panel, data, wf["prob"], wf_div["prob"])
    strategies = rule_strategies() + ai_strategies(ai_cfg, ai_w, fund_w, div_w)
    start = oos + 1  # first decision is at close(oos), first trade at close(oos + 1)
    if start >= len(panel.calendar) - 1:
        raise ValueError("not enough out-of-sample history to backtest strategies")

    runs: dict[str, dict] = {}
    for s in strategies:
        w = s.weights(panel)
        rets, px = (panel.ret, panel.px) if s.assets == "STOCKS" else (panel.etf_ret, panel.etf_px)
        res = backtest.run(w, rets, panel.cash_ret, start, cfg.cost_bps_per_side)
        entry_l, exit_l = _trade_labels(s, ai_cfg)
        tr = backtest.trades(w, px, start, entry_l, exit_l, s.trailing_stop)
        sens = {}
        for bps in COST_SENSITIVITY_BPS:
            r2 = res if bps == cfg.cost_bps_per_side else backtest.run(w, rets, panel.cash_ret, start, bps)
            sens[f"{bps:g}"] = {"cagr": stats.cagr(r2.equity), "sharpe": stats.sharpe(r2.net - panel.cash_ret.to_numpy()[start:])}
        runs[s.key] = {"strategy": s, "res": res, "trades": tr, "sens": sens}

    ref = runs[REFERENCE]["res"]
    cash = panel.cash_ret.to_numpy()[start:]
    candidates = [k for k, r in runs.items() if r["strategy"].family != "BENCHMARK"]
    trial_sr = [stats.per_period_sharpe(runs[k]["res"].net - ref.net) for k in candidates]
    results = []
    for key, r in runs.items():
        s: Strategy = r["strategy"]
        m = stats.metrics(r["res"], ref, cash, r["trades"])
        if key != REFERENCE:
            ex = r["res"].net - ref.net
            lo, hi = stats.stationary_bootstrap_ci(ex)
            m.update({"excessReturn": float(np.mean(ex) * stats.DAYS), "excessCiLow": lo, "excessCiHigh": hi,
                      "informationRatio": stats.sharpe(ex),
                      "deflatedSharpe": stats.deflated_sharpe(ex, len(candidates), trial_sr) if key in candidates else None})
        results.append({"key": key, "family": s.family, "name": s.name, "description": s.describe(), "params": s.params,
                        "metrics": m, "equity": stats.equity_points(r["res"]), "yearly": stats.yearly_returns(r["res"]),
                        "costSensitivity": r["sens"], "verdict": stats.verdict(m, s.family == "BENCHMARK"),
                        "trades": [{**t, "companyId": t["asset"] if s.assets == "STOCKS" else None,
                                    "symbol": panel.symbols.get(t["asset"], str(t["asset"]))} for t in r["trades"]]})
    results.sort(key=lambda x: -(x["metrics"]["sharpe"] if np.isfinite(x["metrics"]["sharpe"]) else -1e9))
    config = {"costBpsPerSide": cfg.cost_bps_per_side, "costSensitivityBps": list(COST_SENSITIVITY_BPS),
              "reference": REFERENCE, "nCandidates": len(candidates), "ai": ai_cfg.params(), "aiFolds": wf["folds"],
              "dividendFeatureTest": feature_test,
              "execution": "Decided at the close, traded at the next close; long-only; idle cash earns realized FEDFUNDS",
              "verdictRule": f"SUPPORTED only if >= {stats.MIN_YEARS:g} years out of sample, the 95% CI of the excess return over "
                             f"{REFERENCE} is above 0, and the Deflated Sharpe Ratio (deflated for {len(candidates)} strategies) "
                             f">= {stats.DSR_LEVEL}"}
    return {"oosStart": panel.calendar[start].date(), "dataCutoff": panel.calendar[-1].date(), "config": config,
            "results": results, "summary": _summary(results, cfg, len(candidates), feature_test)}


def _trade_labels(s: Strategy, cfg: AiConfig) -> tuple[str, str]:
    if s.family == "AI":
        return f"p ≥ {cfg.entry_p:.2f}, top {cfg.max_positions}", f"p < {cfg.exit_p:.2f}"
    if s.sizing == "EQUAL":
        return "Selected", "Dropped at rebalance"
    return "Entry rule", "Exit rule"


def compare_forecasts(panel: MarketPanel, data: pd.DataFrame, without: pd.DataFrame, with_: pd.DataFrame,
                      block: int = 21, n_boot: int = 1000, seed: int = 11) -> dict:
    """Out-of-sample forecast quality of the AI with and without the dividend signals on the same (day, company) rows:
    those both models scored and whose outcome is known. Lower Brier / log loss and higher AUC are better.

    brierDiff = Brier(with) - Brier(without), negative when the dividend signals help. Its 95% CI resamples blocks of
    `block` consecutive dates, because forecasts on neighbouring days share most of their outcome window."""
    rows = data[data["label"].notna()]
    ci = panel.px.columns.get_indexer(rows["company_id"].to_numpy())
    ri = rows["idx"].to_numpy()
    a, b = without.to_numpy(float)[ri, ci], with_.to_numpy(float)[ri, ci]
    ok = np.isfinite(a) & np.isfinite(b)
    a, b, ri = a[ok], b[ok], ri[ok]
    y = rows["label"].to_numpy(float)[ok]
    if len(y) == 0 or len(np.unique(y)) < 2:
        return {"rows": int(len(y))}

    def scores(p: np.ndarray) -> dict:
        return {"brier": brier(p, y), "logLoss": log_loss(p, y), "auc": float(roc_auc_score(y, p))}

    d = (b - y) ** 2 - (a - y) ** 2
    dates = np.unique(ri)
    per_date = pd.Series(d).groupby(ri).agg(["sum", "count"]).reindex(dates)
    s, c = per_date["sum"].to_numpy(), per_date["count"].to_numpy()
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(dates) // block)
    starts = rng.integers(0, max(1, len(dates) - block + 1), size=(n_boot, n_blocks))
    pick = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1) % len(dates)
    boots = s[pick].sum(axis=1) / c[pick].sum(axis=1)
    return {"rows": int(len(y)), "dates": int(len(dates)), "baseRate": float(y.mean()),
            "withoutDividends": scores(a), "withDividends": scores(b), "brierDiff": float(d.mean()),
            "ciLow": float(np.percentile(boots, 2.5)), "ciHigh": float(np.percentile(boots, 97.5)),
            "note": f"brierDiff = Brier(with dividends) - Brier(without); negative means the dividend signals help. "
                    f"95% CI from a bootstrap over blocks of {block} consecutive dates."}


def _summary(results: list[dict], cfg: LabConfig, n: int, feature_test: dict | None = None) -> str:
    m0 = results[0]["metrics"]
    ok = [r["name"] for r in results if r["verdict"].endswith("SUPPORTED by this backtest")]
    best = max((r for r in results if r["family"] != "BENCHMARK"), key=lambda r: r["metrics"]["sharpe"] if np.isfinite(r["metrics"]["sharpe"]) else -1e9)
    ref = next(r for r in results if r["key"] == REFERENCE)["metrics"]
    parts = [f"{len(results)} strategies backtested on the same out-of-sample window ({m0['start']} to {m0['end']}, "
             f"{m0['years']:.1f} years) after {cfg.cost_bps_per_side:g} bp per side."]
    parts.append(f"Equal-weight buy & hold: CAGR {ref['cagr']*100:+.1f}%, Sharpe {ref['sharpe']:.2f}, max drawdown {ref['maxDrawdown']*100:.1f}%.")
    parts.append(f"Highest Sharpe among active strategies: {best['name']} ({best['metrics']['sharpe']:.2f}).")
    if ok:
        parts.append(f"Beat buy & hold after correcting for testing {n} strategies: {', '.join(ok)}. This is a backtest, "
                     f"not evidence of live profitability.")
    else:
        parts.append(f"No strategy beat buy & hold once the test accounts for trying {n} strategies; differences are "
                     f"consistent with luck.")
    if feature_test and "brierDiff" in feature_test:
        a, b = feature_test["withoutDividends"], feature_test["withDividends"]
        lo, hi = feature_test["ciLow"], feature_test["ciHigh"]
        verdict = ("they improved it" if hi < 0 else "they made it worse" if lo > 0 else
                   "the difference is within noise")
        parts.append(f"Dividend signals in the AI: out-of-sample Brier {a['brier']:.4f} without, {b['brier']:.4f} with "
                     f"(difference 95% CI {lo:+.4f} to {hi:+.4f}), AUC {a['auc']:.3f} vs {b['auc']:.3f} on the same "
                     f"{feature_test['rows']:,} forecasts: {verdict}.")
    return " ".join(parts)


def backtest_strategies(engine, cfg: LabConfig | None = None) -> dict:
    cfg = cfg or LabConfig()
    bundle = db.load_bundle(engine)
    lab = run_lab(bundle, cfg)
    run_id = db.insert_strategy_run(engine, lab)
    return {"runId": run_id, "summary": lab["summary"],
            "results": [{"key": r["key"], "verdict": r["verdict"], **{k: db._clean(r["metrics"].get(k)) for k in ("cagr", "sharpe", "maxDrawdown")}}
                        for r in lab["results"]]}


# --------------------------------------------------------------------------- decisions
def decide(engine, as_of: str | None = None, cfg: LabConfig | None = None) -> dict:
    """Today's AI action per company. The backend persists them (append-only) and adds explanations."""
    cfg = cfg or LabConfig()
    bundle = db.load_bundle(engine)
    # an as-of date means "after that day's close", like forecast replays
    d = pit.last_trading_date_at(bundle.calendar, pit.close_ts(pd.Timestamp(as_of))) if as_of else bundle.calendar[-1]
    if d is None:
        raise ValueError("no trading day at or before the requested date")
    held = db.previous_ai_holdings(engine, AI_KEY, d.date())
    return {"decisions": decisions_at(bundle, int(bundle.calendar.get_loc(d)), cfg, held)}


def decisions_at(bundle: DataBundle, idx: int, cfg: LabConfig, held: set[int],
                 panel: MarketPanel | None = None, data: pd.DataFrame | None = None) -> list[dict]:
    ai_cfg = cfg.ai()
    panel = MarketPanel.from_bundle(bundle) if panel is None else panel
    data = dataset(panel, ai_cfg) if data is None else data
    train = data[(data["idx"] + ai_cfg.horizon + 1 <= idx) & data["label"].notna()]
    if len(train) < 200 or train["label"].nunique() < 2:
        raise ValueError(f"not enough labelled history before {panel.calendar[idx].date()} to train the AI ({len(train)} samples)")
    model = new_model(ai_cfg).fit(train[AI_FEATURES].to_numpy(float), train["label"].astype(int).to_numpy())
    rows = data[data["idx"] == idx]
    if rows.empty:
        return []
    X = rows[AI_FEATURES].to_numpy(float)
    p = model.predict_proba(X)[:, 1]
    d = panel.calendar[idx]
    prob = pd.DataFrame(np.nan, index=[d], columns=panel.px.columns)
    prob.loc[d, rows["company_id"].to_numpy()] = p
    member = panel.member.loc[[d]]
    weights, actions = decide_positions(prob, member, ai_cfg, held0=held)
    votes = rule_votes(panel, idx, rule_strategies())
    medians = train[AI_FEATURES].median().to_numpy(float)
    trained_through = panel.calendar[int(train["idx"].max())].date()
    rank = pd.Series(p, index=rows["company_id"].to_numpy()).rank(ascending=False, method="first")
    out = []
    for i, cid in enumerate(rows["company_id"].to_numpy()):
        cid = int(cid)
        out.append(db._clean({
            "companyId": cid, "symbol": panel.symbols[cid], "name": panel.names[cid], "asOfDate": str(d.date()),
            "strategyKey": AI_KEY, "action": actions.at[d, cid], "probability": float(p[i]), "rank": int(rank[cid]),
            "weight": float(weights.at[d, cid]), "entryP": ai_cfg.entry_p, "exitP": ai_cfg.exit_p,
            "maxPositions": ai_cfg.max_positions, "factors": explain(model, X[i], medians), "ruleVotes": votes.get(cid, {}),
            "model": {"algorithm": ALGORITHM, "codeVersion": CODE_VERSION, "trainedThrough": str(trained_through),
                      "nTrain": int(len(train)), "horizon": ai_cfg.horizon, "params": ai_cfg.params()},
        }))
    return out
