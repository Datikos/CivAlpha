import numpy as np
import pandas as pd

from civalpha import calibration


def _frame(n_folds=8, per_fold=600, seed=0, overconfident=True):
    """Outcomes drawn from a true probability q; the model reports p = 0.5 + 3 (q - 0.5) (too sure) or q (calibrated)."""
    rng = np.random.default_rng(seed)
    rows = []
    for f in range(n_folds):
        q = np.clip(rng.normal(0.5, 0.12, per_fold), 0.05, 0.95)
        y = rng.random(per_fold) < q
        p = np.clip(0.5 + 3 * (q - 0.5), 0.01, 0.99) if overconfident else q
        rows.append(pd.DataFrame({"model_kind": "M", "fold": f, "as_of_date": pd.Timestamp("2022-01-01") + pd.to_timedelta(f * 63 + np.arange(per_fold) // 20, "D"),
                                  "probability": p, "outcome": y, "excess_return": np.where(y, 0.03, -0.03)}))
    return pd.concat(rows, ignore_index=True)


def test_isotonic_map_uses_past_folds_only_and_skips_the_first_ones():
    df = _frame()
    cal = calibration.isotonic_forward(df, min_prior_folds=3)
    assert cal[df["fold"] < 3].isna().all() and cal[df["fold"] >= 3].notna().all()
    # changing outcomes in a later fold must not change the calibrated values of an earlier fold
    altered = df.copy()
    altered.loc[altered["fold"] == 7, "outcome"] = ~altered.loc[altered["fold"] == 7, "outcome"]
    cal2 = calibration.isotonic_forward(altered, min_prior_folds=3)
    pd.testing.assert_series_equal(cal[df["fold"] <= 7], cal2[df["fold"] <= 7])   # fold 7's own outcomes never enter its map


def test_calibration_fixes_an_overconfident_model_and_leaves_a_calibrated_one_alone():
    over = calibration.study_model(_frame(overconfident=True))
    assert over["after"]["ece"] < over["before"]["ece"]
    assert over["after"]["brier"] < over["before"]["brier"] and over["brierDiffCi95"][1] < 0
    assert over["after"]["spread"] < over["before"]["spread"]
    fine = calibration.study_model(_frame(overconfident=False, seed=1))
    assert abs(fine["brierDiff"]) < 0.005
    assert fine["brierDiffCi95"][0] <= 0 <= fine["brierDiffCi95"][1] or fine["brierDiffCi95"][0] > -0.003


def test_report_has_reliability_bins_and_confident_decile_before_and_after():
    r = calibration.report(_frame(), evaluation_id=1)
    m = r["models"]["M"]
    for side in ("before", "after"):
        assert sum(b["count"] for b in m[side]["reliability"]) == m["n"]
        assert m[side]["confident"]["n"] == round(0.1 * m["n"])
        assert m[side]["confident"]["meanNetExcess"] == m[side]["confident"]["meanGrossExcess"] - calibration.COST_PER_POSITION
    assert "isotonic calibration fitted on past folds" in r["summary"]
