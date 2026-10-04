"""Time machine: predictions use only data known at the as-of close; facts are scored from later data."""
import json

import numpy as np
import pytest

from civalpha_ml import timemachine as tm
from helpers import make_bundle

H = (5, 10, 21, 63)


def _perturbed(n_days, seed, after_idx):
    b = make_bundle(n_days=n_days, n_companies=8, seed=seed)
    for c in b.tr:
        b.tr[c][after_idx + 1:] *= 3.0
        b.close[c][after_idx + 1:] *= 3.0
    for s in b.bench_tr:
        b.bench_tr[s][after_idx + 1:] *= 0.2
    return b


def _decided(pred):
    """The parts decided at the as-of close (everything except later facts)."""
    return json.dumps({c: {k: v for k, v in s.items() if k in ("odds", "range", "ai", "closeAsOf")}
                       for c, s in sorted(pred["stocks"].items())}, sort_keys=True, default=str)


def test_predictions_use_only_data_known_at_the_as_of_close():
    idx = 600
    a = tm.predict_as_of(make_bundle(n_days=760, n_companies=8, seed=4), idx)
    b_bundle = _perturbed(760, 4, idx)
    b = tm.predict_as_of(b_bundle, idx)
    assert _decided(a) == _decided(b)
    some = next(iter(a["stocks"].values()))
    assert set(some["odds"]) == {str(h) for h in H} and set(some["range"]) == {str(h) for h in H}
    assert some["ai"]["action"] in {"ENTER", "STAY_OUT"}
    r = some["range"]["21"]
    assert r["q10"] <= r["q50"] <= r["q90"]
    # sanity: the facts do see the changed future
    fa = tm.realized(make_bundle(n_days=760, n_companies=8, seed=4), idx, tm.predict_as_of(make_bundle(n_days=760, n_companies=8, seed=4), idx))
    fb = tm.realized(b_bundle, idx, b)
    c = next(iter(fa["stocks"]))
    assert fa["stocks"][c]["actual"]["21"]["stockReturn"] != fb["stocks"][c]["actual"]["21"]["stockReturn"]


def test_realized_returns_match_the_price_path_and_pending_horizons_stay_unknown():
    b = make_bundle(n_days=700, n_companies=8, seed=5)
    idx = len(b.calendar) - 30          # 63-day outcome lies beyond the data
    pred = {"horizons": list(H), "stocks": {1: {"benchmarkSymbol": "BMK"}}}
    res = tm.realized(b, idx, pred)
    s = res["stocks"][1]
    tr, bm = b.tr[1], b.bench_tr["BMK"]
    assert s["actual"]["21"]["stockReturn"] == pytest.approx(tr[idx + 21] / tr[idx] - 1)
    assert s["actual"]["21"]["excess"] == pytest.approx((tr[idx + 21] / tr[idx]) - (bm[idx + 21] / bm[idx]))
    assert s["actual"]["5"]["execReturn"] == pytest.approx(tr[idx + 6] / tr[idx + 1] - 1)      # entered at the next close
    assert s["actual"]["63"] is None
    assert len(s["path"]) == 30 and s["path"][0]["stock"] == 0.0


def test_summary_scores_hits_band_coverage_and_ai_picks():
    def stock(sym, p, ret, bench, q, action):
        return {"symbol": sym, "odds": {"21": {"AUGMENTED": p, "BASELINE": 0.5}},
                "range": {"21": {"q10": q[0], "q50": q[1], "q90": q[2], "naiveQ10": -0.1, "naiveQ50": 0.0, "naiveQ90": 0.1}},
                "ai": {"action": action},
                "actual": {"21": {"stockReturn": ret, "benchmarkReturn": bench, "excess": ret - bench, "beat": ret > bench,
                                  "execReturn": ret, "execBenchmarkReturn": bench, "endDate": "2024-02-01"}}}
    res = {"horizons": [21], "models": {"odds21": {"baseRate": 0.5}}, "stocks": {
        1: stock("A", 0.7, 0.08, 0.02, (-0.02, 0.03, 0.09), "ENTER"),     # right call, inside band
        2: stock("B", 0.6, 0.00, 0.03, (0.01, 0.04, 0.08), "STAY_OUT"),   # wrong call, outside band
        3: stock("C", 0.3, -0.05, 0.01, (-0.08, -0.02, 0.03), "STAY_OUT"),  # right call, inside band
        4: stock("D", 0.4, 0.06, 0.01, (-0.03, 0.01, 0.05), "ENTER")}}    # wrong call, outside band
    sm = tm.summarize(res)["21"]
    assert sm["resolved"] == 4
    assert sm["odds"]["AUGMENTED"]["hitRate"] == pytest.approx(0.5)
    assert sm["odds"]["AUGMENTED"]["brier"] == pytest.approx(np.mean([0.3 ** 2, 0.6 ** 2, 0.3 ** 2, 0.6 ** 2]))
    assert sm["range"]["coverage"] == pytest.approx(0.5)
    assert sm["ai"]["picks"] == ["A", "D"]
    assert sm["ai"]["picksReturn"] == pytest.approx(0.07)
    assert sm["ai"]["universeReturn"] == pytest.approx(np.mean([0.08, 0.0, -0.05, 0.06]))
    assert sm["ai"]["picksBeatSector"] == 2


def test_dates_without_enough_history_are_refused():
    b = make_bundle(n_days=400, n_companies=4, seed=1)
    with pytest.raises(ValueError, match="too early"):
        tm.predict_as_of(b, 150)
