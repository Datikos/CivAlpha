import pytest
import numpy as np
import pandas as pd

from civalpha.evaluation import EvalConfig, calibration_bins, fold_consistency, run_walk_forward, trading_simulation, train_mask, walk_forward_folds
from civalpha.features import build_panel
from civalpha.model import LogitModel
from helpers import make_bundle


def test_folds_are_chronological_and_purged():
    cfg = EvalConfig(min_train_days=100, fold_length=50)
    idxs = np.arange(0, 400, 7)
    folds = walk_forward_folds(idxs, 400, cfg)
    assert folds[0]["test_start_idx"] == 100
    for a, b in zip(folds, folds[1:]):
        assert b["test_start_idx"] == a["test_end_idx"] + 1
    panel = pd.DataFrame({"idx": idxs, "label": True})
    for f in folds:
        tr = panel[train_mask(panel, f, cfg)]
        # every training label window (idx .. idx+21) closed before the test block begins
        assert (tr["idx"] + cfg.horizon < f["test_start_idx"]).all()


def test_walk_forward_on_noise_finds_no_skill_and_claims_no_profit():
    b = make_bundle(n_days=900, n_companies=8, seed=5)
    panel = build_panel(b, sample_every=7)
    res = run_walk_forward(panel, b.calendar, EvalConfig(min_train_days=300))
    P = res["predictions"]
    # each (company, date) predicted once per model, never by a model trained on its period
    assert not P.duplicated(["company_id", "idx", "model_kind"]).any()
    assert abs(res["metrics"]["BASELINE"]["auc"] - 0.5) < 0.1
    assert "NOT supported" in res["verdict"] or res["trading"]["AUGMENTED"].get("periods", 0) == 0
    for k in ("BASELINE", "AUGMENTED"):
        assert sum(x["count"] for x in res["calibration"][k]) == res["metrics"][k]["n"]


def test_trading_costs_are_charged_per_leg_and_side():
    df = pd.DataFrame({"idx": [0, 0, 21, 21], "probability": [0.9, 0.1, 0.9, 0.5],
                       "exec_excess_return": [0.02, -0.01, 0.0, 0.05]})
    r = trading_simulation(df, EvalConfig(sample_every=21, cost_bps_per_side=10))
    # period 0: (+0.02 and +0.01)/2 = 0.015 gross; period 21: only the 0.9 position (0.0); 0.5 is inside the band
    assert abs(r["meanGross"] - 0.0075) < 1e-12
    assert abs(r["meanNet"] - (0.0075 - 0.004)) < 1e-12
    assert r["periods"] == 2


def test_calibration_bins_and_perfect_forecasts():
    df = pd.DataFrame({"probability": [0.05, 0.95, 0.95, 0.05], "outcome": [False, True, True, False]})
    bins = calibration_bins(df)
    assert [b["count"] for b in bins] == [2, 2]
    assert bins[0]["observedRate"] == 0.0 and bins[1]["observedRate"] == 1.0


def test_explanation_contributions_reconstruct_the_probability():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=500), "b": rng.normal(size=500)})
    y = pd.Series((X["a"] + rng.normal(size=500)) > 0)
    m = LogitModel(["a", "b"], n_boot=5).fit(X, y)
    row = X.iloc[3]
    e = m.explain(row)
    logit = e["intercept"] + sum(f["contribution"] for f in e["factors"])
    assert abs(1 / (1 + np.exp(-logit)) - m.predict(X.iloc[[3]])[0]) < 1e-9
    lo, hi = m.predict_interval(X.iloc[[3]])
    assert lo[0] <= hi[0]


def test_fold_consistency_sign_test():
    folds = [{"brier": {"BASELINE": 0.25, "AUGMENTED": a}} for a in (0.24, 0.24, 0.24, 0.24, 0.24, 0.26, 0.25)]
    c = fold_consistency(folds)
    assert c["foldsAugmentedBetter"] == 5 and c["foldsCompared"] == 6  # the tie is dropped
    assert abs(c["signTestP"] - 2 * (1 + 6) / 64) < 1e-12               # P(X<=1), X~Bin(6, 1/2), two-sided
    assert fold_consistency([])["signTestP"] is None


def test_coverage_curve_rewards_confidence_and_charges_costs():
    from civalpha.evaluation import coverage_curve
    rng = np.random.default_rng(3)
    n = 4000
    signal = rng.normal(0, 1, n)
    p = 1 / (1 + np.exp(-1.5 * signal))                       # informative probabilities
    excess = 0.02 * signal + rng.normal(0, 0.05, n)           # the realized excess return follows the signal
    y = (excess > 0).astype(float)
    dates = np.repeat(np.arange(n // 10), 10)
    curve = coverage_curve(p, y, excess, dates, cost_bps_per_side=10, cost_legs=4)
    assert [r["coverage"] for r in curve] == [0.05, 0.10, 0.20, 0.30, 0.50, 1.0]
    assert [r["n"] for r in curve] == [200, 400, 800, 1200, 2000, 4000]
    # the more selective the level, the higher the bar to get in and the better the calls
    assert all(a["minConfidence"] >= b["minConfidence"] for a, b in zip(curve, curve[1:]))
    assert curve[0]["accuracy"] > curve[-1]["accuracy"] > 0.6
    assert curve[0]["meanGross"] > curve[-1]["meanGross"] > 0
    for r in curve:
        assert r["meanNet"] == pytest.approx(r["meanGross"] - 0.004)   # 4 legs x 10 bp
        assert r["ciLow"] <= r["meanNet"] <= r["ciHigh"]
        assert 0 <= r["brier"] <= 0.25
    # long-only mode ranks by p itself and only ever buys
    long = coverage_curve(p, y, excess, dates, cost_bps_per_side=10, cost_legs=2, side="long")
    assert long[0]["minConfidence"] > 0.9 and long[0]["side"] == "long"
    assert long[0]["meanNet"] == pytest.approx(long[0]["meanGross"] - 0.002)
    assert coverage_curve(np.array([]), np.array([]), np.array([]), np.array([]), 10) == []


def test_coverage_curve_on_noise_stays_inside_its_interval():
    from civalpha.evaluation import coverage_curve
    rng = np.random.default_rng(4)
    n = 3000
    p = rng.uniform(0.3, 0.7, n)
    excess = rng.normal(0, 0.05, n)
    curve = coverage_curve(p, (excess > 0).astype(float), excess, np.repeat(np.arange(n // 5), 5), cost_bps_per_side=0)
    # a 95% interval misses zero 5% of the time, so on noise at most one of the six levels may look "profitable"
    assert sum(not (r["ciLow"] < 0 < r["ciHigh"]) for r in curve) <= 1
    for r in curve:
        assert abs(r["accuracy"] - 0.5) < 0.1
        assert abs(r["meanNet"]) < 0.01


def test_walk_forward_reports_a_coverage_curve_per_model():
    b = make_bundle(n_days=900, n_companies=8, seed=5)
    res = run_walk_forward(build_panel(b, sample_every=7), b.calendar, EvalConfig(min_train_days=300))
    for k in ("BASELINE", "AUGMENTED"):
        curve = res["trading"][k]["coverage"]
        assert curve[-1]["coverage"] == 1.0 and curve[-1]["n"] <= res["metrics"][k]["n"]
        assert curve[0]["n"] < curve[-1]["n"]
