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


class ReviewingLlm(LlmProvider):
    """A model whose review is scripted per symbol; None means the review failed."""

    def __init__(self, reviews):
        self.reviews = reviews
        self.briefs = []

    @property
    def enabled(self):
        return True

    @property
    def name(self):
        return "stub-reviewer"

    def review_decision(self, company_name, symbol, brief):
        self.briefs.append(brief)
        return self.reviews.get(symbol)


def _brief(d):
    return {"symbol": d["symbol"], "decision": {"probability": d["probability"]}, "prices": {"lastClose": 100.0}}


def test_review_is_logged_next_to_the_entry_and_never_blocks_it(universe, tdb):
    from civalpha.platform.llm import DecisionReview
    log = []
    llm = ReviewingLlm({"META": DecisionReview("CAUTION", "MEDIUM", "Results are due in 6 trading days.", ("EARNINGS_IMMINENT",))})
    svc = DecisionService(tdb, llm, brief_fn=_brief, review_mode="advisory")
    r = svc.persist_all([decision(tdb, "2026-02-02", "ENTER"), decision(tdb, "2026-02-03", "HOLD")], log.append)
    assert (r.created, r.reviewed, r.vetoed) == (2, 1, 0)
    assert len(llm.briefs) == 1 and llm.briefs[0]["symbol"] == "META"      # HOLD is not a candidate, so it is not reviewed
    row = tdb.one("""SELECT d.action, d.weight, r.stance, r.confidence, r.flags, r.veto, r.brief, r.model FROM strategy_decision d
                     JOIN decision_review r ON r.decision_id = d.id WHERE d.as_of_date = '2026-02-02'""")
    assert row["action"] == "ENTER" and float(row["weight"]) == 0.125           # advisory: the decision is unchanged
    assert (row["stance"], row["confidence"], row["flags"], row["veto"], row["model"]) == ("CAUTION", "MEDIUM", ["EARNINGS_IMMINENT"], False, "stub-reviewer")
    assert row["brief"]["prices"]["lastClose"] == 100.0                        # what the model saw is kept for audit
    assert any(l.startswith("REVIEW META: CAUTION") for l in log)

    # a failed review (None) still stores the decision, with no review row; re-running an existing day reviews nothing
    r2 = DecisionService(tdb, ReviewingLlm({}), brief_fn=_brief, review_mode="advisory").persist_all([decision(tdb, "2026-02-04", "ENTER")], log.append)
    assert (r2.created, r2.reviewed) == (1, 0)
    assert tdb.scalar("SELECT count(*) FROM decision_review") == 1
    again = DecisionService(tdb, llm, brief_fn=_brief, review_mode="advisory").persist_all([decision(tdb, "2026-02-02", "ENTER")], log.append)
    assert (again.existing, again.reviewed) == (1, 0) and len(llm.briefs) == 1


def test_veto_mode_turns_a_disagreed_entry_into_stay_out(universe, tdb):
    from civalpha.platform.llm import DecisionReview
    log = []
    llm = ReviewingLlm({"META": DecisionReview("DISAGREE", "HIGH", "An 84% one-day drop with no recorded split: the series is broken.",
                                               ("DATA_ARTEFACT", "CORPORATE_ACTION"))})
    d = decision(tdb, "2026-03-02", "ENTER")
    d["model"] = {"horizon": 10, "book": {"key": "AI_RANK_SIZED", "slot": 0.125, "replaces": "INTC"}, "sizing": {"sizedWeight": 0.13}}
    r = DecisionService(tdb, llm, brief_fn=_brief, review_mode="veto").persist_all([d], log.append)
    assert (r.created, r.reviewed, r.vetoed) == (1, 1, 1)
    row = tdb.one("""SELECT d.action, d.weight, d.model, r.stance, r.veto FROM strategy_decision d
                     JOIN decision_review r ON r.decision_id = d.id WHERE d.as_of_date = '2026-03-02'""")
    assert row["action"] == "STAY_OUT" and float(row["weight"]) == 0.0
    assert row["model"]["book"]["vetoed"] is True and row["model"]["book"]["slot"] == 0.0 and row["model"]["sizing"]["sizedWeight"] == 0.0
    assert (row["stance"], row["veto"]) == ("DISAGREE", True)
    assert any(l.startswith("VETO META") for l in log) and not any(l.startswith("ENTER META") for l in log)

    # in advisory mode the same DISAGREE is only logged
    r2 = DecisionService(tdb, llm, brief_fn=_brief, review_mode="advisory").persist_all([decision(tdb, "2026-03-03", "ENTER")], log.append)
    assert (r2.created, r2.reviewed, r2.vetoed) == (1, 1, 0)
    assert tdb.scalar("SELECT action FROM strategy_decision WHERE as_of_date = '2026-03-03'") == "ENTER"
    # and with the review off nothing is asked of the model
    before = len(llm.briefs)
    DecisionService(tdb, llm, brief_fn=_brief, review_mode="off").persist_all([decision(tdb, "2026-03-04", "ENTER")], log.append)
    assert len(llm.briefs) == before
