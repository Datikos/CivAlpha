"""Doubler study: outcomes follow the price path, the screen uses only data known at the close, and on noise nothing
is declared found."""
import numpy as np
import pandas as pd
import pytest

from civalpha.features import DataBundle
from civalpha.strategies import doublers
from civalpha.strategies.panel import MarketPanel
from civalpha.strategies.rules import rule_strategies
from helpers import make_bundle


def _bundle(closes: np.ndarray, volume: np.ndarray | None = None) -> DataBundle:
    n, k = closes.shape
    cal = pd.bdate_range("2018-01-01", periods=n)
    frames = []
    for c in range(k):
        f = pd.DataFrame({"company_id": c + 1, "symbol": f"S{c+1}", "trade_date": cal, "close": closes[:, c]})
        if volume is not None:
            f["volume"] = volume[:, c]
        frames.append(f)
    b = make_bundle(n_days=n, n_companies=k)
    bench = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": 100.0})
    return DataBundle.build(b.companies, pd.concat(frames), bench, None, b.facts, b.exposures, b.events, b.targets, b.macro,
                            b.membership.assign(valid_from=pd.Timestamp("2017-01-01")))


def test_outcomes_measure_from_the_next_close_and_find_a_planted_double():
    n = 400
    closes = np.full((n, 1), 10.0)
    closes[300:330, 0] = np.linspace(10, 30, 30)      # triples between day 300 and 329
    closes[330:, 0] = 8.0                             # then collapses
    p = MarketPanel.from_bundle(_bundle(closes))
    o = doublers.forward_outcomes(p.px, 21, 1.0, 0.5)
    # signal at 299 -> entry at close(300); the window is the 21 closes 301..321
    entry = closes[300, 0]
    assert o["max_return"].iat[299, 0] == pytest.approx(closes[301:322, 0].max() / entry - 1)
    assert o["end_return"].iat[299, 0] == pytest.approx(closes[321, 0] / entry - 1)
    assert o["max_drawdown"].iat[299, 0] == pytest.approx(closes[301:322, 0].min() / entry - 1)   # worst close, above the entry here
    hit = o["hit"].iloc[:, 0].to_numpy()
    assert hit[293:306].all() and not hit[:293].any() and not hit[306:].any()   # closes[t+22] >= 2 x closes[t+1]
    assert bool(o["lost"].iat[315, 0])                                          # entry ~21, the window holds the fall to 8
    assert not bool(o["lost"].iat[299, 0])
    assert not o["known"].iloc[-22:, 0].any() and o["known"].iat[-23, 0]      # the last horizon+1 days have no outcome yet


def test_episodes_report_the_day_the_double_was_reached():
    n = 400
    closes = np.full((n, 2), 10.0)
    closes[200:, 0] = 25.0                                              # one jump: every window around it is a hit
    p = MarketPanel.from_bundle(_bundle(closes))
    res = doublers.run_study(p, doublers.DoublerConfig(horizons=(21,)))
    h = res["horizons"]["21"]
    assert h["companiesWithHits"] == ["S1"] and len(h["episodes"]) == 1
    e = h["episodes"][0]
    assert e["signalDate"] == str(p.calendar[200 - 22].date()) and e["entryDate"] == str(p.calendar[179].date())
    assert e["daysToDouble"] == 21 and e["doubledOn"] == str(p.calendar[200].date())
    assert h["base"]["hits"] == 21 and 0 < h["base"]["hitRate"] < 0.05
    assert res["headline"].startswith("Over") and "1 episodes" in res["headline"]


def _perturbed(n_days, seed, after_idx):
    b = make_bundle(n_days=n_days, seed=seed)
    for c in b.tr:
        b.tr[c][after_idx + 1:] *= 3.0
        b.close[c][after_idx + 1:] *= 3.0
    return b


def test_screen_uses_only_data_known_at_the_close():
    i = 330
    cfg = doublers.DoublerConfig(max_price=1e9)                         # the random walks trade near 50: lift the price cap
    a = MarketPanel.from_bundle(make_bundle(n_days=420, seed=3))
    b = MarketPanel.from_bundle(_perturbed(420, 3, i))
    fa, fb = doublers.screen_features(a, cfg), doublers.screen_features(b, cfg)
    for k in doublers.PROFILE_FEATURES:
        pd.testing.assert_frame_equal(fa[k].iloc[:i + 1], fb[k].iloc[:i + 1])
    pd.testing.assert_frame_equal(doublers.screen(a, cfg).iloc[:i + 1], doublers.screen(b, cfg).iloc[:i + 1])
    assert not fa["ret_21"].iloc[i + 5:].equals(fb["ret_21"].iloc[i + 5:])
    # the outcomes, on purpose, do see the later prices
    oa, ob = (doublers.forward_outcomes(p.px, 21, 1.0, 0.5) for p in (a, b))
    assert not oa["max_return"].iloc[i - 20:i].equals(ob["max_return"].iloc[i - 20:i])


def test_screen_fires_on_a_volatile_cheap_breakout_with_a_volume_spike():
    n, k = 400, 10
    rng = np.random.default_rng(1)
    closes = 5.0 * np.exp(np.cumsum(rng.normal(0, 0.004, (n, k)), axis=0))
    wild = np.exp(np.cumsum(rng.normal(0, 0.05, n)))
    closes[:, 0] = 15.0 * wild / wild.max()                                # one wild name, never above $15
    volume = np.full((n, k), 1e6)
    volume[350, 0] = 5e6
    p = MarketPanel.from_bundle(_bundle(closes, volume))
    cfg = doublers.DoublerConfig()
    f = doublers.screen_features(p, cfg)
    c = doublers.screen_conditions(f, cfg)
    assert c["volumeSpike"].to_numpy().sum() == 1 and c["volumeSpike"].iat[350, 0]
    assert c["volatile"].iloc[100:, 0].all()                                # top of the universe by volatility
    assert c["small"].iloc[100:].all().all()                                # cheap; no filings -> size unknown, allowed
    assert f["market_cap"].isna().all().all()
    fires = doublers.screen(p, cfg)
    assert fires.iat[350, 0]
    # the screen is exactly its parts: volatile and small, with a trigger
    expected = c["volatile"] & c["small"] & (c["breakout"] | c["volumeSpike"])
    pd.testing.assert_frame_equal(fires, expected.astype(bool))


def test_study_on_noise_finds_no_doublers_and_says_so():
    p = MarketPanel.from_bundle(make_bundle(n_days=700, n_companies=5, seed=9))
    res = doublers.run_study(p)
    assert set(res["horizons"]) == {"21", "42", "63"}
    for h in res["horizons"].values():
        assert h["base"]["n"] > 1000 and h["base"]["hitRate"] == 0
        assert h["episodes"] == [] and "NOT" in h["verdict"]
        assert h["screen"]["n"] == 0 or h["screen"]["hitRate"] == 0
    assert len(res["today"]["stocks"]) == 5 and all(not s["fires"] for s in res["today"]["stocks"])
    assert res["stockDays"] == 5 * 700 and res["universeSize"] == 5
    import json
    from civalpha.db import _clean
    json.dumps(_clean(res))                                               # storable as is


def test_screen_is_a_registered_strategy_with_a_stop_and_a_holding_limit():
    s = next(x for x in rule_strategies() if x.key == "DOUBLER_SCREEN")
    assert s.family == "SPECULATIVE" and s.trailing_stop == 0.5 and s.params["holdDays"] == 63
    n = 500
    closes = np.full((n, 2), 10.0)
    wild = np.exp(np.cumsum(np.random.default_rng(2).normal(0.0, 0.05, n)))
    closes[:, 0] = 18.0 * wild / wild.max()
    volume = np.full((n, 2), 1e6)
    volume[300, 0] = 1e7
    p = MarketPanel.from_bundle(_bundle(closes, volume))
    w = s.weights(p)
    assert (w.iloc[:, 1] == 0).all()                                      # the flat name never qualifies (not volatile)
    on = (w.iloc[:, 0] > 0).to_numpy()
    edges = np.flatnonzero(np.diff(np.r_[0, on.astype(int), 0]))
    runs = edges[1::2] - edges[0::2]
    assert on.any() and runs.max() <= 64                                  # state_machine counts the entry day as day 0
