from datetime import datetime, timedelta, timezone

import json

import pandas as pd
import pytest
from sqlalchemy import text

from civalpha.strategies import registry
from civalpha.dividends import DIV_FEATURES
from civalpha.strategies.ai import AI_EVENTS_FEATURES, AI_FEATURE_SET, AI_FEATURES, FUND_MODEL_FEATURES


def _lab(keys_metrics):
    return {"results": [{"key": k, "family": fam, "name": k, "description": {"name": k, "entry": "e", "exit": "x"},
                         "params": params, "metrics": {"sharpe": sh, "excessReturn": 0.01, "excessCiLow": -0.1, "excessCiHigh": 0.1, "maxDrawdown": -0.2}}
                        for k, fam, params, sh in keys_metrics]}


@pytest.fixture
def eng(pg):
    e = pg.engine
    with e.begin() as c:
        c.execute(text("DELETE FROM trial_registry"))
        c.execute(text("DELETE FROM strategy_result"))
        c.execute(text("DELETE FROM strategy_run"))
    yield e
    with e.begin() as c:
        c.execute(text("DELETE FROM trial_registry"))
        c.execute(text("DELETE FROM strategy_result"))
        c.execute(text("DELETE FROM strategy_run"))


def _run(e, run_at):
    with e.begin() as c:
        return int(c.execute(text("INSERT INTO strategy_run (run_at, data_cutoff, oos_start, config, summary) VALUES (:t, '2026-10-05', '2021-07-07', '{}', '') RETURNING id"), dict(t=run_at)).scalar_one())


def test_feature_set_changes_make_a_new_trial_and_old_ones_keep_counting(eng):
    t0 = datetime(2026, 10, 5, tzinfo=timezone.utc)
    r1 = _run(eng, t0)
    lab1 = _lab([("MOM_12_1", "TREND", {}, 1.1), ("AI_GBM", "AI", {}, 0.7), ("EW_BUY_HOLD", "BENCHMARK", {}, 0.8),
                 ("AI_FUND", "AI", {"features": FUND_MODEL_FEATURES}, 0.6)])
    registry.register_run(eng, r1, lab1, tested_at=t0, git_commit="abc")
    assert registry.trial_count(eng) == 3                      # benchmark excluded
    keys = {r["trial_key"] for r in _rows(eng)}
    assert keys == {"MOM_12_1", f"AI_GBM@{AI_FEATURE_SET}", "AI_FUND@GBM_FUND_18"}
    # a second run on a smaller AI feature set: the old trial stays, the new one is added, MOM_12_1 is updated not duplicated
    r2 = _run(eng, t0 + timedelta(days=1))
    lab2 = _lab([("MOM_12_1", "TREND", {}, 1.2), ("AI_GBM", "AI", {"features": AI_FEATURES[:-3]}, 0.9)])
    registry.register_run(eng, r2, lab2, tested_at=t0 + timedelta(days=1), git_commit="def")
    rows = {r["trial_key"]: r for r in _rows(eng)}
    assert registry.trial_count(eng) == 4
    assert rows["MOM_12_1"]["runs"] == 2 and rows["MOM_12_1"]["sharpe"] == 1.2 and rows["MOM_12_1"]["git_commit"] == "def"
    assert rows["MOM_12_1"]["first_tested_at"].replace(tzinfo=timezone.utc) if rows["MOM_12_1"]["first_tested_at"].tzinfo is None else rows["MOM_12_1"]["first_tested_at"] == t0
    assert f"AI_GBM@{AI_FEATURE_SET}" in rows and f"AI_GBM@GBM_AI_{len(AI_FEATURES) - 3}" in rows
    # the count a new run must deflate for includes trials it does not re-run
    assert registry.trial_count_including(eng, ["MOM_12_1", "NEW_RULE"]) == 5


def test_backfill_infers_ai_feature_set_from_the_recorded_ai_div_list(eng):
    t = datetime(2026, 10, 4, tzinfo=timezone.utc)
    r1, r2 = _run(eng, t), _run(eng, t + timedelta(days=1))
    rows = [
        (r1, "AI_GBM", "AI", {}, 0.7), (r1, "AI_DIV", "AI", {"features": AI_FEATURES[:35] + DIV_FEATURES}, 0.6), (r1, "SMA_50_200", "TREND", {}, 1.0),
        (r2, "AI_GBM", "AI", {}, 0.8), (r2, "AI_DIV", "AI", {"features": AI_EVENTS_FEATURES + DIV_FEATURES}, 0.6), (r2, "AI_NO_EVENTS", "AI", {"features": AI_FEATURES}, 0.9),
        (r2, "EW_BUY_HOLD", "BENCHMARK", {}, 0.8),
    ]
    with eng.begin() as c:
        for run, k, fam, params, sh in rows:
            c.execute(text("""INSERT INTO strategy_result (run_id, strategy_key, family, name, description, params, metrics, equity, yearly, cost_sensitivity, verdict)
                              VALUES (:r, :k, :f, :k, '{}', CAST(:p AS jsonb), CAST(:m AS jsonb), '[]', '[]', '{}', '')"""),
                      dict(r=run, k=k, f=fam, p=json.dumps(params), m=json.dumps({"sharpe": sh})))
    commits = [(t - timedelta(hours=1), "c0"), (t + timedelta(hours=12), "c1")]
    n = registry.backfill(eng, commits)
    assert n == 6
    reg = {r["trial_key"]: r for r in _rows(eng)}
    # AI_NO_EVENTS is the standard rule on 39 inputs: registered as AI_GBM@GBM_AI_39, not as a trial of its own
    assert set(reg) == {"AI_GBM@GBM_AI_35", "AI_DIV@GBM_AI_DIV_38", "SMA_50_200", "AI_GBM@GBM_AI_42", "AI_DIV@GBM_AI_DIV_45", "AI_GBM@GBM_AI_39"}
    assert reg["AI_GBM@GBM_AI_35"]["source"] == "backfill-inferred" and "inferred" in reg["AI_GBM@GBM_AI_35"]["notes"]
    assert reg["AI_GBM@GBM_AI_35"]["git_commit"] == "c0" and reg["AI_GBM@GBM_AI_42"]["git_commit"] == "c1"
    assert reg["SMA_50_200"]["source"] == "backfill" and reg["SMA_50_200"]["feature_set"] is None
    assert registry.trial_count(eng) == 6


def test_same_inputs_under_another_row_name_are_one_trial():
    from civalpha.strategies.ai import AI_EVENTS_FEATURES
    a = registry.feature_set_of({"key": "AI_WITH_EVENTS", "family": "AI", "params": {"features": AI_EVENTS_FEATURES}})
    assert a == f"GBM_AI_{len(AI_EVENTS_FEATURES)}"
    assert registry.trial_key("AI_WITH_EVENTS", a) == registry.trial_key("AI_GBM", a) == f"AI_GBM@GBM_AI_{len(AI_EVENTS_FEATURES)}"
    assert registry.trial_key("AI_SIZED", "GBM_AI_39") == "AI_SIZED@GBM_AI_39"


def _rows(e):
    with e.connect() as c:
        return [dict(r._mapping) for r in c.execute(text("SELECT * FROM trial_registry"))]
