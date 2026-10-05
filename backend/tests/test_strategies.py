"""Strategy lab: no look-ahead, honest accounting, rules do what their theory says, and noise is not 'alpha'."""
import numpy as np
import pandas as pd
import pytest

from civalpha.features import DataBundle
from civalpha.strategies import backtest, stats
from civalpha.strategies.ai import AiConfig, decide_positions, walk_forward_probabilities
from civalpha.strategies.panel import MarketPanel
from civalpha.strategies.rules import rule_strategies
from civalpha.strategies.service import LabConfig, decisions_at, run_lab
from helpers import make_bundle
from civalpha.dividends import DIV_FEATURES

SMALL_AI = AiConfig(min_train_days=150, fold_length=40, min_samples_leaf=20, max_iter=30, max_positions=2)


def _perturbed(n_days, seed, after_idx):
    b = make_bundle(n_days=n_days, seed=seed)
    for c in b.tr:
        b.tr[c][after_idx + 1:] *= 3.0
    for s in b.bench_tr:
        b.bench_tr[s][after_idx + 1:] *= 0.2
    return b


@pytest.mark.parametrize("key", [s.key for s in rule_strategies()])
def test_rule_strategies_use_only_data_known_at_the_close(key):
    i = 330
    s = next(x for x in rule_strategies() if x.key == key)
    a = s.weights(MarketPanel.from_bundle(make_bundle(n_days=420, seed=3)))
    b = s.weights(MarketPanel.from_bundle(_perturbed(420, 3, i)))
    pd.testing.assert_frame_equal(a.iloc[:i + 1], b.iloc[:i + 1])


def test_ai_decisions_use_only_data_known_at_the_close():
    i = 400
    out = []
    for bundle in (make_bundle(n_days=520, seed=4), _perturbed(520, 4, i)):
        p = MarketPanel.from_bundle(bundle)
        wf = walk_forward_probabilities(p, SMALL_AI)
        w, _ = decide_positions(wf["prob"], p.member, SMALL_AI, start_idx=wf["oos_start_idx"])
        out.append((wf["prob"], w))
    pd.testing.assert_frame_equal(out[0][0].iloc[:i + 1], out[1][0].iloc[:i + 1])
    pd.testing.assert_frame_equal(out[0][1].iloc[:i + 1], out[1][1].iloc[:i + 1])
    # sanity: the perturbation does change later decisions' inputs
    assert not out[0][0].iloc[i + 60:].equals(out[1][0].iloc[i + 60:])


def test_ai_training_labels_are_resolved_before_each_refit():
    wf = walk_forward_probabilities(MarketPanel.from_bundle(make_bundle(n_days=520, seed=5)), SMALL_AI)
    assert len(wf["folds"]) >= 3
    for f in wf["folds"]:
        assert f["maxTrainLabelIdx"] < f["testStartIdx"]


def test_decision_rule_has_hysteresis_and_position_cap():
    cal = pd.bdate_range("2024-01-01", periods=4)
    prob = pd.DataFrame([[0.60, 0.58, 0.40], [0.50, 0.70, 0.56], [0.47, 0.70, 0.56], [0.49, 0.70, 0.56]], index=cal, columns=[1, 2, 3])
    member = pd.DataFrame(True, index=cal, columns=[1, 2, 3])
    cfg = AiConfig(entry_p=0.55, exit_p=0.48, max_positions=2)
    w, a = decide_positions(prob, member, cfg)
    assert list(a.iloc[0]) == ["ENTER", "ENTER", "STAY_OUT"]
    assert list(a.iloc[1]) == ["HOLD", "HOLD", "STAY_OUT"]     # 0.50 >= exit 0.48: kept; cap of 2 blocks name 3
    assert list(a.iloc[2]) == ["EXIT", "HOLD", "ENTER"]        # 0.47 < exit; slot freed for name 3
    assert list(a.iloc[3]) == ["STAY_OUT", "HOLD", "HOLD"]     # 0.49 is not enough to re-enter
    assert w.iloc[3].tolist() == [0.0, 0.5, 0.5]


def test_engine_lags_execution_and_charges_traded_weight():
    cal = pd.bdate_range("2024-01-01", periods=4)
    rets = pd.DataFrame({"A": [0.0, 0.10, 0.20, -0.10]}, index=cal)
    w = pd.DataFrame({"A": [1.0, 1.0, 0.0, 0.0]}, index=cal)
    cash = pd.Series(0.0, index=cal)
    r = backtest.run(w, rets, cash, start_idx=1, cost_bps_per_side=10)
    # close(0) decision -> buy at close(1) (10 bp on 100%); held through days 2 and 3;
    # the close(2) decision to sell executes at close(3), so day 3's -10% is still earned
    assert r.gross.tolist() == pytest.approx([0.0, 0.20, -0.10])
    assert r.cost.tolist() == pytest.approx([0.001, 0.0, 0.001])
    assert r.equity[-1] == pytest.approx(0.999 * 1.2 * 0.9 * 0.999)
    assert r.exposure.tolist() == [1.0, 1.0, 0.0]


def test_engine_idle_cash_earns_the_cash_rate():
    cal = pd.bdate_range("2024-01-01", periods=3)
    rets = pd.DataFrame({"A": [0.0, 0.05, 0.05]}, index=cal)
    w = pd.DataFrame({"A": [0.5, 0.5, 0.5]}, index=cal)
    r = backtest.run(w, rets, pd.Series(0.001, index=cal), start_idx=1, cost_bps_per_side=0)
    assert r.gross[1] == pytest.approx(0.5 * 0.05 + 0.5 * 0.001)


def _bundle_from_closes(closes: np.ndarray) -> DataBundle:
    n, k = closes.shape
    cal = pd.bdate_range("2015-01-01", periods=n)
    stock = pd.concat([pd.DataFrame({"company_id": c + 1, "symbol": f"S{c+1}", "trade_date": cal, "close": closes[:, c]}) for c in range(k)])
    b = make_bundle(n_days=n, n_companies=k)
    bench = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": 100.0})
    return DataBundle.build(b.companies, stock, bench, None, b.facts, b.exposures, b.events, b.targets, b.macro,
                            b.membership.assign(valid_from=pd.Timestamp("2014-01-01")))


def _total_return(key, closes, bps=0.0):
    p = MarketPanel.from_bundle(_bundle_from_closes(closes))
    s = next(x for x in rule_strategies() if x.key == key)
    return backtest.run(s.weights(p), p.ret, p.cash_ret, 260, bps).equity[-1] - 1.0


def test_trend_rules_profit_from_planted_trends():
    rng = np.random.default_rng(0)
    n, k = 1500, 3
    drift = np.where((np.arange(n) // 250) % 2 == 0, 0.003, -0.003)       # long up and down legs
    closes = 100 * np.exp(np.cumsum(drift[:, None] + rng.normal(0, 0.008, (n, k)), axis=0))
    bh = _total_return("EW_BUY_HOLD", closes)
    assert _total_return("SMA_50_200", closes) > bh + 0.2
    assert _total_return("DONCHIAN_55_20", closes) > bh + 0.2


def test_mean_reversion_rules_profit_from_planted_reversion():
    rng = np.random.default_rng(1)
    n, k = 1500, 3
    x = np.zeros((n, k))
    for t in range(1, n):
        x[t] = 0.85 * x[t - 1] + rng.normal(0, 0.02, k)                     # stationary around a flat 200-day mean
    closes = 100 * np.exp(x + 0.0003 * np.arange(n)[:, None])
    assert _total_return("BOLLINGER_20_2", closes) > 0.3
    assert _total_return("RSI2_SMA200", closes) > 0.3
    assert _total_return("REVERSAL_5D", closes) > 0.3


def test_trailing_stop_exits_after_a_ten_percent_fall_from_the_high():
    p = MarketPanel.from_bundle(_bundle_from_closes(np.column_stack([np.r_[np.linspace(100, 200, 300), np.linspace(200, 150, 50)]])))
    s = next(x for x in rule_strategies() if x.key == "SMA_50_200_TSTOP10")
    w = s.weights(p)[1].to_numpy()
    px = p.px[1].to_numpy()                                   # total-return index (starts at 1.0)
    first_off = int(np.argmax((w == 0) & (np.arange(len(w)) > 250)))
    assert px[first_off] <= px.max() * 0.9 + 1e-12
    assert px[first_off - 1] > px.max() * 0.9


def test_statistics_on_known_series():
    assert stats.max_drawdown(np.array([1.0, 1.2, 0.6, 0.9])) == pytest.approx(-0.5)
    assert stats.cagr(np.full(252, 1.0) * np.r_[np.ones(251), 2.0]) == pytest.approx(1.0)
    rng = np.random.default_rng(2)
    strong = rng.normal(0.002, 0.01, 1000)
    noise = rng.normal(0.0, 0.01, 1000)
    trials = [stats.per_period_sharpe(rng.normal(0, 0.01, 1000)) for _ in range(20)]
    assert stats.deflated_sharpe(strong, 1, [0.0]) > 0.99
    assert stats.deflated_sharpe(noise, 20, trials) < 0.95
    # more trials -> a higher bar
    assert stats.deflated_sharpe(strong * 0.1 + noise, 50, trials) <= stats.deflated_sharpe(strong * 0.1 + noise, 2, trials[:2]) + 1e-12
    lo, hi = stats.stationary_bootstrap_ci(strong)
    assert lo > 0 and hi > lo


def test_on_pure_noise_no_strategy_is_declared_a_winner():
    lab = run_lab(make_bundle(n_days=1450, n_companies=5, seed=9), LabConfig(max_positions=2))
    keys = {r["key"] for r in lab["results"]}
    assert {"EW_BUY_HOLD", "SMA_50_200", "AI_GBM", "AI_GBM_TSTOP10", "QUALITY_GROWTH", "AI_DIV", "DIV_YIELD"} <= keys
    test = lab["config"]["dividendFeatureTest"]
    assert test["rows"] > 1000 and test["ciLow"] <= test["brierDiff"] <= test["ciHigh"]
    assert lab["results"][0]["metrics"]["years"] >= 3
    starts = {r["metrics"]["start"] for r in lab["results"]}
    assert len(starts) == 1                                  # every strategy is scored on the same window
    for r in lab["results"]:
        assert "SUPPORTED by" not in r["verdict"], r["key"]
    assert "No strategy beat" in lab["summary"]


def _dividends(cal, after_idx=None, boost=1.0):
    """Quarterly dividends for companies 1 and 2 (2 raises its payment), a 2:1 split of company 1; payments after
    `after_idx` are multiplied by `boost` and get an extra special one, to test that earlier days never see them."""
    rows = []
    for k, i in enumerate(range(20, len(cal), 63)):
        late = after_idx is not None and i > after_idx
        rows.append((1, cal[i], "CASH_DIVIDEND", 0.5 * (boost if late else 1.0)))
        rows.append((2, cal[i], "CASH_DIVIDEND", (0.2 + 0.05 * (k // 4)) * (boost if late else 1.0)))
    rows.append((1, cal[300], "SPLIT", 2.0))
    if after_idx is not None:
        rows.append((2, cal[after_idx + 5], "CASH_DIVIDEND", 9.0))
    return pd.DataFrame([(c, f"S{c}", d, t, v) for c, d, t, v in rows], columns=["company_id", "symbol", "ex_date", "action_type", "value"])


def test_dividend_signals_use_only_dividends_known_at_the_close():
    i = 380
    cal = pd.bdate_range("2020-01-01", periods=520)
    panels = [MarketPanel.from_bundle(make_bundle(n_days=520, seed=4, actions=_dividends(cal, *args))) for args in ((), (i, 3.0))]
    a, b = (p.dividends() for p in panels)
    for k in ("div_yield", "div_growth"):
        pd.testing.assert_frame_equal(a[k].iloc[:i + 1], b[k].iloc[:i + 1])
        assert not a[k].iloc[i + 10:].equals(b[k].iloc[i + 10:])
    assert (a["div_yield"][[3, 4]].iloc[200:] == 0).all().all()          # non-payers yield 0
    assert a["div_growth"][2].iloc[-1] > 0                               # company 2 raised its dividend
    s = next(x for x in rule_strategies() if x.key == "DIV_YIELD")
    pd.testing.assert_frame_equal(s.weights(panels[0]).iloc[:i + 1], s.weights(panels[1]).iloc[:i + 1])
    from civalpha.strategies.ai import dataset
    d = dataset(panels[0], SMALL_AI)
    assert set(DIV_FEATURES) <= set(d.columns) and d["div_yield"].notna().any()


def test_live_decisions_have_actions_reasons_and_rule_votes():
    b = make_bundle(n_days=520, n_companies=4, seed=6)
    out = decisions_at(b, len(b.calendar) - 1, LabConfig(max_positions=2), held={1})
    assert {d["companyId"] for d in out} == {1, 2, 3, 4}
    assert all(d["action"] in {"ENTER", "EXIT", "HOLD", "STAY_OUT"} for d in out)
    held_after = [d for d in out if d["action"] in {"ENTER", "HOLD"}]
    assert len(held_after) <= 2
    one = next(d for d in out if d["companyId"] == 1)
    assert one["action"] in {"HOLD", "EXIT"}                 # it was held before, so it cannot be a fresh ENTER
    assert len(one["factors"]) == 5 and "SMA_50_200" in one["ruleVotes"]


def test_volatility_sizing_gives_calm_names_more_and_never_leverages():
    from civalpha.strategies.ai import AiConfig, size_by_volatility, sized_weight
    cal = pd.bdate_range("2024-01-01", periods=3)
    w = pd.DataFrame([[0.25, 0.25, 0.0, 0.25], [0.25, 0.25, 0.25, 0.25], [0.0, 0.0, 0.0, 0.0]], index=cal, columns=[1, 2, 3, 4])
    vol = pd.DataFrame([[0.20, 0.40, 0.10, np.nan], [0.05, 0.05, 0.05, 0.05], [0.2, 0.2, 0.2, 0.2]], index=cal, columns=[1, 2, 3, 4])
    cfg = AiConfig(max_positions=4, vol_budget=0.04, max_weight=0.20)
    s = size_by_volatility(w, vol, cfg)
    assert s.iloc[0].tolist() == pytest.approx([0.20, 0.10, 0.0, 0.25])   # calm name capped at 20%, volatile one 10%, not held = 0, no vol = equal slice
    assert (s.iloc[1] == 0.20).all()                                       # 0.04 / 0.05 = 80% each, capped at 20%; total 80%, no scaling needed
    assert s.iloc[2].sum() == 0.0
    assert sized_weight(0.0, 0.1, cfg) == 0.0 and sized_weight(0.125, np.nan, cfg) == 0.125
    assert sized_weight(0.125, 0.8, cfg) == pytest.approx(0.05)


def test_confident_variant_trades_less_and_lab_reports_the_decision_layer():
    lab = run_lab(make_bundle(n_days=1450, n_companies=5, seed=9), LabConfig(max_positions=2))
    by = {r["key"]: r for r in lab["results"]}
    assert {"AI_CONF", "AI_SIZED"} <= set(by)
    assert by["AI_CONF"]["metrics"]["exposure"] <= by["AI_GBM"]["metrics"]["exposure"]
    assert by["AI_CONF"]["metrics"]["trades"] <= by["AI_GBM"]["metrics"]["trades"]
    assert by["AI_CONF"]["params"]["entry_p"] == 0.60 and by["AI_CONF"]["description"]["entry"].startswith("Model probability ≥ 0.60")
    assert by["AI_SIZED"]["metrics"]["exposure"] <= 1.0
    curve = lab["config"]["aiCoverage"]
    assert [r["coverage"] for r in curve][-1] == 1.0 and all(r["side"] == "long" for r in curve)
    assert "decision layer" in lab["summary"]


def test_live_decisions_carry_a_volatility_sized_weight():
    b = make_bundle(n_days=520, n_companies=4, seed=6)
    out = decisions_at(b, len(b.calendar) - 1, LabConfig(max_positions=2), held=set())
    sizing = [d["model"]["sizing"] for d in out]
    assert all(set(s) >= {"vol21", "sizedWeight", "confident", "volBudget", "maxWeight", "confidentEntryP"} for s in sizing)
    assert sum(s["sizedWeight"] for s in sizing) <= 1.0 + 1e-9
    for d in out:
        s = d["model"]["sizing"]
        assert (s["sizedWeight"] > 0) == (d["weight"] > 0)
        assert s["confident"] == (d["probability"] >= 0.60)
