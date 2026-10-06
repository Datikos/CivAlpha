import numpy as np
import pandas as pd

from civalpha import livetest


def _rows(n_companies=300, skill=0.0, seed=1, resolved=True):
    """Two as-of dates per company; outcomes drawn with P(y=1) = 0.5 + skill * (p - 0.5) * 2, so skill=1 is perfectly
    calibrated information and skill=0 is noise."""
    rng = np.random.default_rng(seed)
    out = []
    for c in range(n_companies):
        p = float(np.clip(rng.normal(0.5, 0.05), 0.3, 0.7))
        for d in livetest.AS_OF_DATES:
            y = rng.random() < 0.5 + skill * (p - 0.5) * 2
            out.append(dict(company_id=c, as_of_date=d, model_kind="BASELINE", series_key=f"{c}|BASELINE|{d}", version=1,
                            p=p, outcome=(bool(y) if resolved else None), excess_return=(0.02 if y else -0.02)))
    return pd.DataFrame(out)


def test_noise_is_never_skill_and_information_is_skill():
    # a model with no information but spread-out probabilities scores below the base rate: that is HARMFUL by design
    noise = livetest.score_model(_rows(skill=0.0), n_boot=300)
    assert noise["verdict"] in ("NO_SKILL", "HARMFUL")
    assert noise["brierSkill"] < 0.01
    informed = livetest.score_model(_rows(skill=1.0, seed=2), n_boot=300)
    assert informed["verdict"] == "SKILL"
    assert informed["auc"] > 0.55 and informed["brierSkill"] > 0


def test_anti_informed_model_is_harmful():
    m = livetest.score_model(_rows(skill=-1.0, seed=3), n_boot=300)
    assert m["verdict"] == "HARMFUL"
    assert m["ci95"]["brierSkill"][1] < 0


def test_incomplete_until_95_percent_resolved():
    rows = _rows(n_companies=100)
    rows["outcome"] = rows["outcome"].astype(object)
    rows.loc[rows.index[:12], "outcome"] = None      # 12 of 200 unresolved: 94%
    assert livetest.score_model(rows, n_boot=50)["verdict"] == "INCOMPLETE"
    assert livetest.score_model(_rows(n_companies=100, resolved=False), n_boot=50)["verdict"] == "PENDING"


def test_confident_decile_and_costs():
    rows = _rows(n_companies=50, seed=4)
    m = livetest.score_model(rows, n_boot=20)
    assert m["confidentN"] == 10                      # 10% of 100 forecasts
    # excess is +-2% by construction, so the mean gross over 10 positions is a multiple of 0.4%; net = gross - 40 bp
    gross = m["confidentMeanNetExcess"] + livetest.COST_PER_POSITION
    assert abs(gross / 0.004 - round(gross / 0.004)) < 1e-9
    assert -0.02 - 1e-12 <= gross <= 0.02 + 1e-12


def test_fingerprint_is_order_independent_and_version_sensitive():
    rows = _rows(n_companies=5)
    a = livetest.fingerprint(rows)["BASELINE"]
    assert livetest.fingerprint(rows.sample(frac=1, random_state=0))["BASELINE"] == a
    rows.loc[0, "version"] = 2
    assert livetest.fingerprint(rows)["BASELINE"] != a


def test_paired_difference_is_zero_for_identical_models():
    a = _rows(n_companies=40)
    b = a.copy()
    b["model_kind"] = "AUGMENTED"
    d = livetest.paired_difference(b, a, n_boot=20)
    assert d["n"] == 80 and d["brierDiff"] == 0.0 and d["maxAbsProbDiff"] == 0.0


def test_batch_2_reports_the_book_model_and_closes_at_ten_dates():
    rows = _rows(n_companies=20)
    rows["model_kind"] = "AI_BOOK_21"
    r = livetest.report(rows, batch="2")
    assert set(r["models"]) == {"AI_BOOK_21"} and r["fingerprintMatches"] is None
    assert r["asOfDates"] == list(livetest.AS_OF_DATES) and r["populationClosed"] is False
    assert "augmentedMinusBaseline" not in r
    assert "LIMIT 10" in livetest.BATCH2_SQL and "AI_BOOK_21" in livetest.BATCH2_SQL
