"""Forecast and AI-decision stores: immutable, versioned, explanations never block decisions (ported Java ITs)."""
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import DBAPIError

from civalpha.platform.decisions import DecisionService
from civalpha.platform.forecasts import ForecastService, content_hash, java_double
from civalpha.platform.llm import LlmProvider
from civalpha.platform.sql import _conn
from civalpha.platform.tickers import TickerResolver


@contextmanager
def savepoint():
    """Statements expected to fail run in a savepoint so the test transaction stays usable."""
    sp = _conn.get().begin_nested()
    try:
        yield
    finally:
        if sp.is_active:
            sp.rollback()


def meta(tdb):
    return TickerResolver(tdb).company_by_cik("0001326801")


def test_java_double_formatting_matches_double_to_string():
    assert [java_double(x) for x in (0.0, 1.0, 0.5, 1e-4, 1.2345e-5, 1e7, 12345.0, 0.001, -3e-8, 123.456)] == \
        ["0.0", "1.0", "0.5", "1.0E-4", "1.2345E-5", "1.0E7", "12345.0", "0.001", "-3.0E-8", "123.456"]


def test_forecasts_are_immutable_and_versioned(universe, tdb):
    mv = tdb.scalar("""INSERT INTO model_version (model_kind, algorithm, feature_names, trained_through, training_cutoff, n_samples, params, code_version)
                       VALUES ('AUGMENTED', 'logistic_regression_l2', '[]', '2026-08-01', now(), 100, '{}', 'test') RETURNING id""")
    p = {"companyId": meta(tdb), "symbol": "META", "benchmarkSymbol": "XLC", "modelKind": "AUGMENTED", "modelVersionId": mv,
         "probability": 0.42, "probLow": 0.38, "probHigh": 0.47, "horizonTradingDays": 21, "asOfDate": "2026-09-30",
         "asOf": "2026-09-30T21:00:00Z", "target": "t", "uncertaintyNote": "n", "features": {"trade_shock": -0.1},
         "explanation": {"factors": []}, "sources": []}
    fs = ForecastService(tdb, issue_fn=lambda **_: [])
    v1 = fs.persist(dict(p), "LIVE", "first")
    assert v1 is not None
    assert fs.persist(dict(p), "LIVE", "same inputs") is None
    # a different model-version row with identical output is not new evidence
    assert content_hash({**p, "modelVersionId": mv + 1}) == content_hash(p)

    v2 = fs.persist({**p, "probability": 0.35, "features": {"trade_shock": -0.3}}, "LIVE", "New evidence: event #1")
    row = tdb.one("SELECT version, supersedes_id, reason FROM forecast WHERE id = :id", id=v2)
    assert row["version"] == 2 and row["supersedes_id"] == v1 and "supersedes v1" in row["reason"]

    for sql in ("UPDATE forecast SET probability = 0.9 WHERE id = :id", "DELETE FROM forecast WHERE id = :id"):
        with savepoint(), pytest.raises(DBAPIError, match="immutable"):
            tdb.execute(sql, id=v1)
    assert float(tdb.scalar("SELECT probability FROM forecast WHERE id = :id", id=v1)) == 0.42


class StubLlm(LlmProvider):
    def __init__(self, text):
        self.text = text

    @property
    def enabled(self):
        return True

    @property
    def name(self):
        return "stub"

    def explain_decision(self, company_name, symbol, decision):
        return self.text


def decision(tdb, date, action):
    return {"companyId": meta(tdb), "symbol": "META", "name": "Meta Platforms, Inc.", "asOfDate": date, "strategyKey": "AI_GBM",
            "action": action, "probability": 0.61, "entryP": 0.55, "exitP": 0.48, "weight": 0.125, "rank": 1, "maxPositions": 8,
            "factors": [{"feature": "mom_12_1", "contribution": 0.04}], "ruleVotes": {"SMA_50_200": True}, "model": {"horizon": 10}}


def test_ai_decisions_are_append_only_and_explanations_never_block_them(universe, tdb):
    log = []
    # a language model that fails returns None: the decision is stored anyway, without an explanation
    r = DecisionService(tdb, StubLlm(None)).persist_all([decision(tdb, "2026-01-05", "ENTER")], log.append)
    assert (r.created, r.explained) == (1, 0)
    did = tdb.scalar("SELECT id FROM strategy_decision WHERE company_id = :c AND as_of_date = '2026-01-05'", c=meta(tdb))
    assert tdb.scalar("SELECT count(*) FROM decision_explanation WHERE decision_id = :id", id=did) == 0
    assert log == ["ENTER META (p = 0.61)"]

    # re-running the same day neither duplicates nor changes the stored decision
    again = DecisionService(tdb, StubLlm("text")).persist_all([decision(tdb, "2026-01-05", "EXIT")], log.append)
    assert (again.created, again.existing) == (0, 1)
    assert tdb.scalar("SELECT action FROM strategy_decision WHERE id = :id", id=did) == "ENTER"

    # a working model explains ENTER/EXIT, but not HOLD
    ok = DecisionService(tdb, StubLlm("Momentum and the trend rules agree.")).persist_all(
        [decision(tdb, "2026-01-06", "EXIT"), decision(tdb, "2026-01-07", "HOLD")], log.append)
    assert (ok.created, ok.explained) == (2, 1)

    for sql in ("UPDATE strategy_decision SET action = 'EXIT' WHERE id = :id", "DELETE FROM strategy_decision WHERE id = :id"):
        with savepoint(), pytest.raises(DBAPIError, match="immutable"):
            tdb.execute(sql, id=did)
