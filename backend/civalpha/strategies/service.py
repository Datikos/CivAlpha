"""Strategy lab orchestration: backtest every strategy on one out-of-sample window, and today's AI decisions."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from sklearn.metrics import roc_auc_score

from .. import db, pit
from ..evaluation import brier, coverage_curve, coverage_sentence, log_loss
from ..features import DataBundle
from . import backtest, registry, stats
from .ai import (AI_CONF_KEY, AI_DIV_FEATURES, AI_EVENTS_FEATURES, AI_FEATURE_SET, AI_FEATURES, AI_KEY, AI_RANK_KEY, AI_RANK_SIZED_KEY,
                 AI_RANK_VOL_KEY, AI_SIZED_KEY, AI_WITH_EVENTS_KEY, BOOK_KEY,
                 FUND_MODEL_FEATURES, ALGORITHM, CODE_VERSION, AiConfig, ai_strategies,
                 dataset, decide_positions, explain, new_model, size_by_conviction, size_by_volatility, sized_weight,
                 technical_features, walk_forward_probabilities)
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
def run_lab(bundle: DataBundle, cfg: LabConfig, n_trials: int | None = None) -> dict:
    """Backtest every strategy. `n_trials` is the number of trials the Deflated Sharpe Ratio deflates for: the trial
    registry's count including this run's candidates (backtest_strategies passes it); without it, this run's candidates."""
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
    conf_w, _ = decide_positions(wf["prob"], panel.member, ai_cfg.confident(), start_idx=oos)
    vol_21 = technical_features(panel)["vol_21"]
    sized_w = size_by_volatility(ai_w, vol_21, ai_cfg)
    rank_w, _ = decide_positions(wf["prob"], panel.member, ai_cfg.ranked(), start_idx=oos)
    rank_sized_w = size_by_conviction(rank_w, vol_21, wf["prob"], ai_cfg)
    rank_vol_w = size_by_volatility(rank_w, vol_21, ai_cfg)
    wf_ev = walk_forward_probabilities(panel, ai_cfg, data, features=AI_EVENTS_FEATURES)
    events_w, _ = decide_positions(wf_ev["prob"], panel.member, ai_cfg, start_idx=oos)
    ai_coverage = ai_coverage_curve(panel, data, wf["prob"], cfg.cost_bps_per_side)
    strategies = rule_strategies() + ai_strategies(ai_cfg, ai_w, fund_w, div_w, conf_w, sized_w, rank_w, rank_sized_w,
                                                   rank_vol_w, events_w)
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
    n_trials = max(int(n_trials or 0), len(candidates))
    results = []
    for key, r in runs.items():
        s: Strategy = r["strategy"]
        m = stats.metrics(r["res"], ref, cash, r["trades"])
        if key != REFERENCE:
            ex = r["res"].net - ref.net
            lo, hi = stats.stationary_bootstrap_ci(ex)
            m.update({"excessReturn": float(np.mean(ex) * stats.DAYS), "excessCiLow": lo, "excessCiHigh": hi,
                      "informationRatio": stats.sharpe(ex),
                      "deflatedSharpe": stats.deflated_sharpe(ex, n_trials, trial_sr) if key in candidates else None})
        results.append({"key": key, "family": s.family, "name": s.name, "description": s.describe(), "params": s.params,
                        "metrics": m, "equity": stats.equity_points(r["res"]), "yearly": stats.yearly_returns(r["res"]),
                        "costSensitivity": r["sens"], "verdict": stats.verdict(m, s.family == "BENCHMARK"),
                        "trades": [{**t, "companyId": t["asset"] if s.assets == "STOCKS" else None,
                                    "symbol": panel.symbols.get(t["asset"], str(t["asset"]))} for t in r["trades"]]})
    results.sort(key=lambda x: -(x["metrics"]["sharpe"] if np.isfinite(x["metrics"]["sharpe"]) else -1e9))
    config = {"costBpsPerSide": cfg.cost_bps_per_side, "costSensitivityBps": list(COST_SENSITIVITY_BPS),
              "reference": REFERENCE, "nCandidates": len(candidates), "nTrials": n_trials, "ai": ai_cfg.params(), "aiFolds": wf["folds"],
              "bookKey": BOOK_KEY, "featureSets": {k: registry.feature_set_of(r) for k, r in ((x["key"], x) for x in results)},
              "dividendFeatureTest": feature_test, "aiCoverage": ai_coverage,
              "execution": "Decided at the close, traded at the next close; long-only; idle cash earns realized FEDFUNDS",
              "verdictRule": f"SUPPORTED only if >= {stats.MIN_YEARS:g} years out of sample, the 95% CI of the excess return over "
                             f"{REFERENCE} is above 0, and the Deflated Sharpe Ratio (deflated for the {n_trials} trials in the trial "
                             f"registry: every strategy and feature-set variant backtested on this history, {len(candidates)} of them "
                             f"in this run) >= {stats.DSR_LEVEL}"}
    return {"oosStart": panel.calendar[start].date(), "dataCutoff": panel.calendar[-1].date(), "config": config,
            "results": results, "summary": _summary(results, cfg, n_trials, feature_test, ai_coverage, len(candidates))}


def ai_coverage_curve(panel: MarketPanel, data: pd.DataFrame, prob: pd.DataFrame, cost_bps_per_side: float) -> list[dict]:
    """Abstention on the AI's own out-of-sample forecasts: long only, ranked by probability, on every (day, company)
    row the model scored and whose outcome is known; costs are 2 legs (buy and sell the stock)."""
    rows = data[data["label"].notna()]
    ci = panel.px.columns.get_indexer(rows["company_id"].to_numpy())
    ri = rows["idx"].to_numpy()
    p = prob.to_numpy(float)[ri, ci]
    return coverage_curve(p, rows["label"].to_numpy(float), rows["fwd_excess"].to_numpy(float), ri,
                          cost_bps_per_side=cost_bps_per_side, cost_legs=2, side="long")


def _trade_labels(s: Strategy, cfg: AiConfig) -> tuple[str, str]:
    if s.key == AI_CONF_KEY:
        return f"p ≥ {cfg.confident_entry_p:.2f}, top {cfg.max_positions}", f"p < {cfg.confident_exit_p:.2f}"
    if s.key in (AI_RANK_KEY, AI_RANK_SIZED_KEY, AI_RANK_VOL_KEY):
        m = cfg.ranked().swap_margin
        return f"p ≥ {cfg.entry_p:.2f}, top {cfg.max_positions} or beats weakest holding by {m:.2f}", f"p < {cfg.exit_p:.2f} or replaced"
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


def _summary(results: list[dict], cfg: LabConfig, n: int, feature_test: dict | None = None,
             ai_coverage: list[dict] | None = None, n_run: int | None = None) -> str:
    n_run = n_run if n_run is not None else n
    m0 = results[0]["metrics"]
    ok = [r["name"] for r in results if r["verdict"].endswith("SUPPORTED by this backtest")]
    best = max((r for r in results if r["family"] != "BENCHMARK"), key=lambda r: r["metrics"]["sharpe"] if np.isfinite(r["metrics"]["sharpe"]) else -1e9)
    ref = next(r for r in results if r["key"] == REFERENCE)["metrics"]
    parts = [f"{len(results)} strategies backtested on the same out-of-sample window ({m0['start']} to {m0['end']}, "
             f"{m0['years']:.1f} years) after {cfg.cost_bps_per_side:g} bp per side."]
    parts.append(f"Equal-weight buy & hold: CAGR {ref['cagr']*100:+.1f}%, Sharpe {ref['sharpe']:.2f}, max drawdown {ref['maxDrawdown']*100:.1f}%.")
    parts.append(f"Highest Sharpe among active strategies: {best['name']} ({best['metrics']['sharpe']:.2f}).")
    tried = (f"the {n} trials in the trial registry (every strategy and feature-set variant backtested on this history, "
             f"{n_run} of them in this run)")
    if ok:
        parts.append(f"Beat buy & hold after correcting for {tried}: {', '.join(ok)}. This is a backtest, "
                     f"not evidence of live profitability.")
    else:
        parts.append(f"No strategy beat buy & hold once the test accounts for {tried}; differences are "
                     f"consistent with luck.")
    if feature_test and "brierDiff" in feature_test:
        a, b = feature_test["withoutDividends"], feature_test["withDividends"]
        lo, hi = feature_test["ciLow"], feature_test["ciHigh"]
        verdict = ("they improved it" if hi < 0 else "they made it worse" if lo > 0 else
                   "the difference is within noise")
        parts.append(f"Dividend signals in the AI: out-of-sample Brier {a['brier']:.4f} without, {b['brier']:.4f} with "
                     f"(difference 95% CI {lo:+.4f} to {hi:+.4f}), AUC {a['auc']:.3f} vs {b['auc']:.3f} on the same "
                     f"{feature_test['rows']:,} forecasts: {verdict}.")
    by_key = {r["key"]: r["metrics"] for r in results}
    base, conf, sized = by_key.get(AI_KEY), by_key.get(AI_CONF_KEY), by_key.get(AI_SIZED_KEY)
    if base and conf and sized:
        parts.append(f"The AI's decision layer: acting only on confident forecasts (p ≥ {cfg.ai().confident_entry_p:.2f}) gives "
                     f"Sharpe {conf['sharpe']:.2f} at {conf['exposure']:.0%} invested against {base['sharpe']:.2f} at "
                     f"{base['exposure']:.0%} for the standard rule; sizing each position by its volatility gives Sharpe "
                     f"{sized['sharpe']:.2f} with a max drawdown of {sized['maxDrawdown']*100:.1f}% against "
                     f"{base['maxDrawdown']*100:.1f}%.")
    rank, tilted, rank_vol = by_key.get(AI_RANK_KEY), by_key.get(AI_RANK_SIZED_KEY), by_key.get(AI_RANK_VOL_KEY)
    if base and rank and tilted:
        parts.append(f"Letting the book follow the ranking (replace the weakest holding when a candidate beats it by "
                     f"{cfg.ai().ranked().swap_margin:.2f}) gives Sharpe {rank['sharpe']:.2f} with {rank['trades']:,} trades against "
                     f"{base['sharpe']:.2f} with {base['trades']:,}; adding conviction and volatility sizing gives Sharpe "
                     f"{tilted['sharpe']:.2f} and a max drawdown of {tilted['maxDrawdown']*100:.1f}%.")
    if sized and rank_vol and tilted:
        parts.append(f"Separating the two: ranking with volatility sizing and no tilt gives Sharpe {rank_vol['sharpe']:.2f} against "
                     f"{sized['sharpe']:.2f} for volatility sizing alone, the recorded book ({BOOK_KEY}) (the replacement rule's "
                     f"cost), and {tilted['sharpe']:.2f} with the conviction tilt (the tilt's effect).")
    ew_sized, mom_sized = by_key.get("EW_SIZED"), by_key.get("MOM_12_1_SIZED")
    if sized and ew_sized:
        gap = sized["sharpe"] - ew_sized["sharpe"]
        same = abs(gap) < 0.10 and abs(sized["maxDrawdown"] - ew_sized["maxDrawdown"]) < 0.05
        parts.append(f"Sizing without a forecast: the whole universe under the book's volatility sizing (EW_SIZED) gives Sharpe "
                     f"{ew_sized['sharpe']:.2f} with a max drawdown of {ew_sized['maxDrawdown']*100:.1f}%"
                     + (f", and 12-1 momentum sized the same way (MOM_12_1_SIZED) {mom_sized['sharpe']:.2f} with {mom_sized['maxDrawdown']*100:.1f}%" if mom_sized else "")
                     + f", against {sized['sharpe']:.2f} and {sized['maxDrawdown']*100:.1f}% for the recorded book ({BOOK_KEY}): "
                     + ("the AI's contribution beyond sizing is nil on this window." if same else
                        f"the gap of {gap:+.2f} Sharpe to the whole-universe row is the most the AI's selection can be credited with on this window, "
                        f"before any correction for the trials made")
                     + (f"; a 12-1 momentum screen under the same sizing does better still ({mom_sized['sharpe']:.2f}), so the sizing, not the forecast, "
                        f"is the part that travels." if mom_sized and not same and mom_sized["sharpe"] > sized["sharpe"] else "."))
    ev = by_key.get(AI_WITH_EVENTS_KEY)
    if base and ev:
        parts.append(f"With the policy-event features added back the standard rule gives Sharpe {ev['sharpe']:.2f} against "
                     f"{base['sharpe']:.2f} without them.")
    if ai_coverage:
        parts.append(coverage_sentence(ai_coverage, "the AI's own forecasts"))
    return " ".join(p for p in parts if p)


def planned_trial_keys() -> list[str]:
    """Trial keys of the strategies a lab run scores (rules plus the AI rows on their current feature sets)."""
    pairs = [(s.key, s.family) for s in rule_strategies()] + [(k, "AI") for k in registry.AI_FEATURE_SETS]
    return registry.planned_trial_keys(pairs, AiConfig().horizon)


def backtest_strategies(engine, cfg: LabConfig | None = None) -> dict:
    cfg = cfg or LabConfig()
    bundle = db.load_bundle(engine)
    n_trials = registry.trial_count_including(engine, planned_trial_keys())
    lab = run_lab(bundle, cfg, n_trials=n_trials)
    run_id = db.insert_strategy_run(engine, lab)
    registry.register_run(engine, run_id, lab)
    return {"runId": run_id, "summary": lab["summary"], "nTrials": n_trials,
            "results": [{"key": r["key"], "verdict": r["verdict"], **{k: db._clean(r["metrics"].get(k)) for k in ("cagr", "sharpe", "maxDrawdown")}}
                        for r in lab["results"]]}


# --------------------------------------------------------------------------- decisions
PRIOR_BOOK_KEYS = (AI_RANK_SIZED_KEY, AI_KEY)   # keys the daily book was recorded under before BOOK_KEY, newest first


def decide(engine, as_of: str | None = None, cfg: LabConfig | None = None) -> dict:
    """Today's AI action per company under the recorded book (BOOK_KEY). The backend persists them (append-only) and
    adds explanations. Yesterday's book comes from the latest decisions under BOOK_KEY; before the first of those it is
    seeded from the holdings recorded under the previous book keys, so switching the book does not restart from cash."""
    cfg = cfg or LabConfig()
    bundle = db.load_bundle(engine)
    # an as-of date means "after that day's close", like forecast replays
    d = pit.last_trading_date_at(bundle.calendar, pit.close_ts(pd.Timestamp(as_of))) if as_of else bundle.calendar[-1]
    if d is None:
        raise ValueError("no trading day at or before the requested date")
    held = db.previous_ai_holdings(engine, BOOK_KEY, d.date())
    if not held and not db.has_decisions(engine, BOOK_KEY, d.date()):
        for k in PRIOR_BOOK_KEYS:
            if k != BOOK_KEY and db.has_decisions(engine, k, d.date()):
                held = db.previous_ai_holdings(engine, k, d.date())
                break
    return {"decisions": decisions_at(bundle, int(bundle.calendar.get_loc(d)), cfg, held)}


def decisions_at(bundle: DataBundle, idx: int, cfg: LabConfig, held: set[int],
                 panel: MarketPanel | None = None, data: pd.DataFrame | None = None) -> list[dict]:
    """One decision per company at close(idx) under the recorded book (AI_SIZED): the standard thresholds, no replacement
    rule, and the volatility size as `weight`. `model.book` describes the rule (swapMargin and tilt are null: no swaps, no
    conviction tilt); `model.sizing` repeats the volatility size and the confident flag."""
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
    vols = rows["vol_21"].to_numpy(float)
    cids = [int(c) for c in rows["company_id"].to_numpy()]
    slots = {c: float(weights.at[d, c]) for c in cids}
    raw_sized = {c: sized_weight(slots[c], float(v), ai_cfg) for c, v in zip(cids, vols)}
    sized_scale = 1.0 / max(1.0, sum(raw_sized.values()))   # no leverage: scale every position down if the book exceeds 1
    out = []
    for i, cid in enumerate(cids):
        action = actions.at[d, cid]
        out.append(db._clean({
            "companyId": cid, "symbol": panel.symbols[cid], "name": panel.names[cid], "asOfDate": str(d.date()),
            "strategyKey": BOOK_KEY, "action": action, "probability": float(p[i]), "rank": int(rank[cid]),
            "weight": raw_sized[cid] * sized_scale, "entryP": ai_cfg.entry_p, "exitP": ai_cfg.exit_p,
            "maxPositions": ai_cfg.max_positions, "factors": explain(model, X[i], medians), "ruleVotes": votes.get(cid, {}),
            "model": {"algorithm": ALGORITHM, "codeVersion": CODE_VERSION, "trainedThrough": str(trained_through),
                      "nTrain": int(len(train)), "horizon": ai_cfg.horizon, "params": ai_cfg.params(),
                      "book": {"key": BOOK_KEY, "maxPositions": ai_cfg.max_positions, "slot": slots[cid],
                               "swapMargin": ai_cfg.swap_margin, "tilt": None, "replacedBy": None, "replaces": None,
                               "features": "no policy-event features", "featureSet": AI_FEATURE_SET, "horizon": ai_cfg.horizon},
                      "sizing": {"vol21": float(vols[i]), "sizedWeight": raw_sized[cid] * sized_scale,
                                 "confident": bool(p[i] >= ai_cfg.confident_entry_p),
                                 "volBudget": ai_cfg.vol_budget, "maxWeight": ai_cfg.max_weight,
                                 "confidentEntryP": ai_cfg.confident_entry_p}},
        }))
    return out
