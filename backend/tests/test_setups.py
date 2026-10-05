"""Setup playbook: triggers fire once and use only data known at the close, outcomes follow the price path, planted
edges are found, and on noise nothing is declared SUPPORTED."""
import json

import numpy as np
import pandas as pd
import pytest

from civalpha.db import _clean
from civalpha.features import DataBundle
from civalpha.strategies import setups
from civalpha.strategies.panel import MarketPanel
from helpers import make_bundle


def _bundle(closes: np.ndarray, bench: np.ndarray | None = None, volume: np.ndarray | None = None, actions=None) -> DataBundle:
    n, k = closes.shape
    cal = pd.bdate_range("2018-01-01", periods=n)
    frames = []
    for c in range(k):
        f = pd.DataFrame({"company_id": c + 1, "symbol": f"S{c+1}", "trade_date": cal, "close": closes[:, c]})
        if volume is not None:
            f["volume"] = volume[:, c]
        frames.append(f)
    b = make_bundle(n_days=n, n_companies=k, actions=actions)
    bench_df = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": 100.0 if bench is None else bench})
    return DataBundle.build(b.companies, pd.concat(frames), bench_df, b.actions, b.facts, b.exposures, b.events, b.targets, b.macro,
                            b.membership.assign(valid_from=pd.Timestamp("2017-01-01")))


def _perturbed(n_days, seed, after_idx):
    b = make_bundle(n_days=n_days, seed=seed)
    for c in b.tr:
        b.tr[c][after_idx + 1:] *= 3.0
    for s in b.bench_tr:
        b.bench_tr[s][after_idx + 1:] *= 0.2
    return b


def test_edge_fires_once_per_episode():
    cal = pd.bdate_range("2024-01-01", periods=6)
    cond = pd.DataFrame({"a": [False, True, True, False, True, True], "b": [True, True, False, np.nan, False, True]}, index=cal)
    e = setups.edge(cond)
    assert e["a"].tolist() == [False, True, False, False, True, False]
    assert e["b"].tolist() == [True, False, False, False, False, True]


@pytest.mark.parametrize("key", [s.key for s in setups.setups()])
def test_triggers_use_only_data_known_at_the_close(key):
    i = 330
    s = next(x for x in setups.setups() if x.key == key)
    cfg = setups.SetupConfig()
    a = s.fn(MarketPanel.from_bundle(make_bundle(n_days=420, seed=3)), cfg)
    b = s.fn(MarketPanel.from_bundle(_perturbed(420, 3, i)), cfg)
    pd.testing.assert_frame_equal(a.iloc[:i + 1], b.iloc[:i + 1])


def test_outcomes_are_excess_over_the_etf_from_the_next_close():
    n = 300
    closes = np.full((n, 1), 10.0)
    closes[100:] = 12.0                                   # +20% at day 100
    bench = np.full(n, 100.0)
    bench[100:] = 105.0                                   # the ETF gains 5% the same day
    p = MarketPanel.from_bundle(_bundle(closes, bench))
    ex = setups.forward_excess(p, 21)
    # signal at 98 -> entry close(99) -> window to close(120): stock +20%, ETF +5%
    assert ex.iat[98, 0] == pytest.approx(0.20 - 0.05)
    assert ex.iat[99, 0] == pytest.approx(0.0)            # entered at close(100), after the jump
    assert ex.iloc[-22:, 0].isna().all() and np.isfinite(ex.iat[n - 23, 0])


def test_planted_breakouts_are_found_and_scored():
    n = 1300
    closes = np.full((n, 3), 100.0)
    # flat prices; every ~120 days (staggered per company) a new 52-week high followed by a 12% rally, then flat again
    for j in range(3):
        for t in range(300 + 40 * j, n - 100, 120):
            level = closes[t - 1, j]
            closes[t, j] = level * 1.01
            closes[t + 1:t + 22, j] = level * np.linspace(1.015, 1.12, 21)
            closes[t + 22:, j] = level * 1.12
    p = MarketPanel.from_bundle(_bundle(closes))
    res = setups.run_study(p, setups.SetupConfig(horizons=(21,), min_triggers=5))
    row = next(r for r in res["setups"] if r["key"] == "BREAKOUT_52W")
    st = row["horizons"]["21"]
    assert row["triggers"] >= 20 and st["n"] >= 20
    assert st["hitRate"] == 1.0 and st["meanExcess"] > 0.08 and st["grade"] == "SUPPORTED"
    assert st["ciLow"] <= st["meanExcess"] + 1e-9 and st["meanExcess"] <= st["ciHigh"] + 1e-9
    assert st["payoff"] is None or st["payoff"] > 1                        # every planted outcome is a win: no losses to divide by
    assert next(r for r in res["setups"] if r["key"] == "GOLDEN_CROSS")["triggers"] > 0
    assert res["config"]["tests"] == len(res["setups"]) and res["config"]["bonferroniZ"] > 2.9
    json.dumps(_clean(res))


def test_dividend_and_volume_setups_fire_on_planted_events():
    n = 600
    cal = pd.bdate_range("2018-01-01", periods=n)
    closes = np.full((n, 2), 50.0)
    closes[400:, 0] = 54.0                                                   # +8% on a 4x volume day
    volume = np.full((n, 2), 1e6)
    volume[400, 0] = 4e6
    rows = [(1, cal[i], "CASH_DIVIDEND", 0.5 if i < 300 else 0.6) for i in range(40, n, 63)]   # raised after day 300
    rows += [(2, cal[i], "CASH_DIVIDEND", 0.5 if i < 300 else 0.2) for i in range(40, n, 63)]  # cut after day 300
    actions = pd.DataFrame([(c, f"S{c}", d, t, v) for c, d, t, v in rows], columns=["company_id", "symbol", "ex_date", "action_type", "value"])
    p = MarketPanel.from_bundle(_bundle(closes, volume=volume, actions=actions))
    cfg = setups.SetupConfig()
    reg = {s.key: s for s in setups.setups()}
    up = reg["VOLUME_SURGE_UP"].fn(p, cfg)
    assert up.to_numpy().sum() == 1 and up.iat[400, 0]
    assert reg["VOLUME_SURGE_DOWN"].fn(p, cfg).to_numpy().sum() == 0
    raise_ = reg["DIVIDEND_RAISE"].fn(p, cfg)
    cut = reg["DIVIDEND_CUT"].fn(p, cfg)
    assert raise_[1].sum() >= 1 and raise_[2].sum() == 0
    assert cut[2].sum() >= 1 and cut[1].sum() == 0
    first_raise = int(np.flatnonzero(raise_[1].to_numpy())[0])
    assert first_raise >= 300 and raise_[1].iloc[first_raise + 1:first_raise + 60].sum() == 0   # counted once per change


def test_study_on_noise_supports_nothing_and_lists_fresh_triggers():
    p = MarketPanel.from_bundle(make_bundle(n_days=900, n_companies=6, seed=9))
    res = setups.run_study(p)
    assert set(res["base"]) == {"5", "21", "63"} and len(res["setups"]) == len(setups.setups())
    for r in res["setups"]:
        for st in r["horizons"].values():
            assert st["grade"] != "SUPPORTED", r["key"]
    assert res["today"]["freshDays"] == 5 and {t["key"] for t in res["today"]["setups"]} == {r["key"] for r in res["setups"]}
    for t in res["today"]["setups"]:
        for s in t["stocks"]:
            assert 0 <= s["daysAgo"] < 5
    assert res["today"]["stockCount"] <= 6 and "setups tested" in res["headline"]
    json.dumps(_clean(res))
