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
