"""ADR-0002: calibrated probabilities in the lab, the decision and the walk-forward."""
import numpy as np
import pandas as pd

from civalpha.evaluation import EvalConfig, run_walk_forward
from civalpha.features import build_panel
from civalpha.strategies import service
from civalpha.strategies.ai import AI_CAL_KEY, AI_SIZED_CAL_KEY, AiConfig, calibrated_probabilities, dataset, walk_forward_probabilities
from civalpha.strategies.panel import MarketPanel
from helpers import make_bundle


def _lab_inputs():
    b = make_bundle(n_days=1000, n_companies=6, seed=5)
    mp = MarketPanel.from_bundle(b)
    cfg = AiConfig(min_train_days=400, fold_length=63)
    data = dataset(mp, cfg)
    wf = walk_forward_probabilities(mp, cfg, data)
    return b, mp, cfg, data, wf


def test_calibrated_probabilities_use_resolved_earlier_folds_only():
    b, mp, cfg, data, wf = _lab_inputs()
    cal = calibrated_probabilities(wf["prob"], data, wf["folds"], cfg)
    starts = [f["testStartIdx"] for f in wf["folds"]]
    # first three folds stay raw
    raw_block = slice(starts[0], starts[3])
    pd.testing.assert_frame_equal(cal.iloc[raw_block], wf["prob"].iloc[raw_block])
    # later folds are mapped (values differ) and stay inside [0, 1]
    later = cal.iloc[starts[3]:]
    assert not np.allclose(later.fillna(0).to_numpy(), wf["prob"].iloc[starts[3]:].fillna(0).to_numpy())
    assert np.nanmin(later.to_numpy()) >= 0 and np.nanmax(later.to_numpy()) <= 1
    # flipping outcomes that resolve after fold k starts must not change fold k's mapped values
    k = 4
    S = starts[k]
    altered = data.copy()
    late = altered["idx"] + cfg.horizon + 1 >= S
    altered.loc[late, "label"] = 1.0 - altered.loc[late, "label"]
    cal2 = calibrated_probabilities(wf["prob"], altered, wf["folds"], cfg)
    end = starts[k + 1] if k + 1 < len(starts) else len(cal)
    pd.testing.assert_frame_equal(cal.iloc[S:end], cal2.iloc[S:end])


def test_lab_backtests_the_calibrated_rows_and_registers_their_keys():
    b = make_bundle(n_days=1000, n_companies=6, seed=5)
    lab = service.run_lab(b, service.LabConfig())
    keys = {r["key"] for r in lab["results"]}
    assert {AI_CAL_KEY, AI_SIZED_CAL_KEY} <= keys
    assert lab["config"]["calibration"]["rows"] == [AI_CAL_KEY, AI_SIZED_CAL_KEY]
    assert isinstance(lab["config"]["aiCoverageCalibrated"], list)
    assert "calibrated on earlier folds (ADR-0002)" in lab["summary"]
    assert {f"{AI_CAL_KEY}@GBM_AI_39@21d", f"{AI_SIZED_CAL_KEY}@GBM_AI_39@21d"} <= set(service.planned_trial_keys())


def test_decision_carries_a_calibrated_probability_but_acts_on_the_raw_one():
    b = make_bundle(n_days=1000, n_companies=6, seed=5)
    idx = len(b.calendar) - 1
    rng = np.random.default_rng(0)
    p = rng.uniform(0.2, 0.8, 20000)
    rows = pd.DataFrame({"fold": 0, "as_of_date": "2024-01-02", "probability": p, "outcome": rng.random(20000) < 0.5})  # noise: everything maps near 0.5
    without = service.decisions_at(b, idx, service.LabConfig(), set())
    with_cal = service.decisions_at(b, idx, service.LabConfig(), set(), calibration=(7, rows))
    assert all(d["probabilityCalibrated"] is None and d["model"]["calibration"] is None for d in without)
    assert all(d["probabilityCalibrated"] is not None for d in with_cal)
    assert np.mean([abs(d["probabilityCalibrated"] - 0.5) for d in with_cal]) < 0.05   # honest noise reads as a coin flip
    assert [d["action"] for d in with_cal] == [d["action"] for d in without]      # the action still comes from the raw p
    assert with_cal[0]["model"]["calibration"]["fittedOn"].endswith("evaluation 7")


def test_walk_forward_reports_calibrated_metrics_per_model():
    b = make_bundle(n_days=900, n_companies=8, seed=5)
    panel = build_panel(b, sample_every=7)
    res = run_walk_forward(panel, b.calendar, EvalConfig(min_train_days=300))
    for k in ("BASELINE", "AUGMENTED"):
        c = res["metrics"][k]["calibrated"]
        assert c and {"brierSkill", "auc", "ece", "spread", "rawSpread", "brierDiffCi95", "n"} <= set(c)
        assert c["n"] < res["metrics"][k]["n"]            # the first folds are left out
