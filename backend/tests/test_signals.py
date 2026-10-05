"""Signal health: the IC finds a planted signal, flags decay, grades noise as noise, and is storable."""
import json

import numpy as np
import pandas as pd

from civalpha.db import _clean
from civalpha.strategies import signals
from civalpha.strategies.panel import MarketPanel
from helpers import make_bundle


def test_spearman_and_monthly_pooling():
    x = np.array([1.0, 2.0, 3.0, 4.0, np.nan])
    assert signals.spearman(x, np.array([10.0, 20.0, 30.0, 40.0, 50.0])) == 1.0
    assert signals.spearman(x, -x) == -1.0
    assert np.isnan(signals.spearman(np.ones(5), x)) and np.isnan(signals.spearman(x[:2], x[:2]))
    cal = pd.bdate_range("2024-01-01", periods=60)
    df = pd.DataFrame({"idx": np.repeat(np.arange(60), 5), "f": np.tile([1.0, 2.0, 3.0, 4.0, 5.0], 60),
                       "fwd_excess": np.tile([-0.02, -0.01, 0.0, 0.01, 0.02], 60)})
    m = signals.monthly_ic(df, "f", cal, min_stocks=5)
    assert [x["month"] for x in m] == ["2024-01", "2024-02", "2024-03"] and all(abs(x["ic"] - 1.0) < 1e-9 for x in m)
    assert signals.monthly_ic(df, "f", cal, min_stocks=6) == []                     # too few stocks per day
    # the HAC t-statistic shrinks for a positively autocorrelated series and matches the plain one for white noise
    rng = np.random.default_rng(1)
    white = rng.normal(0.05, 0.1, 20000)
    plain = white.mean() / (white.std(ddof=1) / np.sqrt(len(white)))
    assert abs(signals.hac_tstat(white, 3) / plain - 1) < 0.1
    smooth = np.convolve(rng.normal(0.0, 0.1, 4000), np.ones(6) / 6, mode="valid") + 0.03
    plain_s = smooth.mean() / (smooth.std(ddof=1) / np.sqrt(len(smooth)))
    assert signals.hac_tstat(smooth, 3) < 0.7 * plain_s


def test_summary_grades_signal_noise_and_decay():
    cfg = signals.SignalConfig()
    z = 3.0
    strong = [{"month": str(m), "ic": 0.1 + 0.01 * ((i % 3) - 1), "n": 50} for i, m in enumerate(pd.period_range("2020-01", periods=36, freq="M"))]
    s = signals.summarize(strong, cfg, z, 10)
    assert s["grade"] == "INFORMATIVE" and s["signHitRate"] == 1.0 and not s["decaying"] and s["icIr"] > 5
    noise = [{"month": str(m), "ic": 0.2 * (-1) ** i, "n": 50} for i, m in enumerate(pd.period_range("2020-01", periods=36, freq="M"))]
    assert signals.summarize(noise, cfg, z, 10)["grade"] == "NOISE"
    faded = [{"month": str(m), "ic": (0.15 if i < 24 else -0.08), "n": 50} for i, m in enumerate(pd.period_range("2020-01", periods=36, freq="M"))]
    f = signals.summarize(faded, cfg, z, 10)
    assert f["decaying"] and f["recentIc"] < 0 < f["earlierIc"] and "DECAYING" in f["verdict"]
    assert signals.summarize(strong[:5], cfg, z, 10)["grade"] == "NOT_TESTABLE"


def test_study_finds_a_planted_signal_and_nothing_else():
    n, k = 900, 8
    b = make_bundle(n_days=n, n_companies=k, seed=5)
    p = MarketPanel.from_bundle(b)
    # plant: next-day's relative move is partly predictable from a feature we control: use rev_5d by construction?
    # simpler: run on noise and check the shape and that noise is graded noise
    res = signals.run_study(p, signals.SignalConfig(min_months=6))
    assert res["config"]["tests"] == len(res["features"]) >= 30
    for r in res["features"]:
        # volatility is a real rank signal even on a driftless random walk (a volatile stock's median 21-day return is
        # below zero), so only the non-volatility inputs must come out as noise
        if "vol" in r["feature"]:
            continue
        assert r["grade"] in ("NOISE", "NOT_TESTABLE", "SUGGESTIVE"), (r["feature"], r["tStat"])
    assert "model inputs tested" in res["headline"]
    json.dumps(_clean(res))
    # a planted signal: a feature equal to the future excess return itself is found (sanity of the plumbing)
    from civalpha.strategies.ai import AiConfig, dataset
    df = dataset(p, AiConfig(horizon=21))
    df["oracle"] = df["fwd_excess"] + np.random.default_rng(0).normal(0, 0.02, len(df))
    m = signals.monthly_ic(df, "oracle", p.calendar, 5)
    s = signals.summarize(m, signals.SignalConfig(min_months=6), 3.0, 1)
    assert s["grade"] == "INFORMATIVE" and s["meanIc"] > 0.5
