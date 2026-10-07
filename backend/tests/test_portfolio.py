"""ADR-0004: advice on the owner's holdings. Rule order, sizing, the live-test gate on model actions, the append-only
store, outcome resolution, and the privacy rule on /api/portfolio."""
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from civalpha.platform.app import protected_request
from civalpha.platform.portfolio import (ADD_BAND, TRIM_BAND, PortfolioService, advise_holding, basis_of, review_date,
                                         volatility_size)
from civalpha.platform.tickers import TickerResolver
from civalpha.strategies.ai import AiConfig

CFG = AiConfig()
AS_OF = date(2026, 10, 5)
PENDING = {"kind": "AI_BOOK_21", "verdict": "PENDING", "resolved": 0}
PASS = {"kind": "AI_BOOK_21", "verdict": "PASS", "resolved": 600, "auc": 0.56}


def holding(symbol="AAA", shares=10.0, cost=100.0, cid=1):
    return {"symbol": symbol, "companyId": cid, "shares": shares, "avgCostUsd": cost}


def decision(p=0.52, vol=0.40, cid=1, action="STAY_OUT"):
    return {"id": 7, "strategyKey": "AI_SIZED", "companyId": cid, "symbol": "AAA", "action": action, "probability": p,
            "probabilityCalibrated": 0.5, "rank": 12, "entryP": 0.55, "exitP": 0.48, "vol21": vol}


def advise(h=None, close=100.0, d=None, flags=(), total=10_000.0, cash=0.0, live=PENDING):
    return advise_holding(h or holding(), close, decision() if d is None else d, list(flags), total, cash, live, CFG, AS_OF)


# ------------------------------------------------------------------ rules (pure)
def test_volatility_size_is_the_books_size():
    assert volatility_size(0.40, CFG) == pytest.approx(0.10)          # 0.04 / 0.40
    assert volatility_size(0.10, CFG) == pytest.approx(0.20)          # capped at max_weight
    assert volatility_size(None, CFG) == pytest.approx(1 / 8)         # unknown volatility: the equal slice


def test_concentration_trims_to_the_volatility_size():
    # 10 shares x 100 = 1,000 of 4,000 = 25% > 20% cap; volatility size 10% -> sell down to 400 = 6 shares
    a = advise(total=4_000.0)
    assert (a["action"], a["layer"], a["headline"], a["rule"]) == ("TRIM", "RISK", "TRIM", "OVER_CAP")
    assert a["targetWeight"] == pytest.approx(0.10) and a["tradeShares"] == -6.0


def test_over_risk_size_trims_below_the_cap():
    # weight 16% < 20% cap but above 1.5 x 10% = 15%
    a = advise(total=1_000 / 0.16)
    assert a["action"] == "TRIM" and a["rule"] == "OVER_RISK_SIZE"


def test_broken_series_reviews_before_risk_and_model():
    flag = {"rule": "UNEXPLAINED_MOVE", "date": "2026-10-01", "move": -0.84, "text": "x"}
    a = advise(d=decision(p=0.40), flags=[flag], total=4_000.0)
    assert a["action"] == "REVIEW" and a["layer"] == "DATA"
    assert [r["rule"] for r in a["reasons"]] == ["UNEXPLAINED_MOVE", "OVER_CAP", "MODEL_EXIT"]   # every rule that fired is listed


def test_sell_is_shown_as_opinion_while_the_live_test_is_pending():
    a = advise(d=decision(p=0.45), total=20_000.0)
    assert (a["action"], a["layer"], a["headline"]) == ("SELL", "MODEL", "HOLD")
    assert a["tradeShares"] == -10.0 and a["model"]["proven"] is False
    passed = advise(d=decision(p=0.45), total=20_000.0, live=PASS)
    assert passed["headline"] == "SELL" and passed["model"]["proven"] is True


def test_add_is_limited_by_cash():
    # weight 1,000 / 20,000 = 5% < 0.67 x 10%; wants 10% - 5% = 1,000 more; cash 450 buys 4 shares at 100
    a = advise(d=decision(p=0.60), total=20_000.0, cash=450.0, live=PASS)
    assert (a["action"], a["headline"], a["tradeShares"]) == ("ADD", "ADD", 4.0)
    no_cash = advise(d=decision(p=0.60), total=20_000.0, cash=50.0, live=PASS)
    assert no_cash["action"] == "ADD" and no_cash["tradeShares"] is None and no_cash["reasons"][-1]["rule"] == "NO_CASH"


def test_hysteresis_band_holds_between_add_and_trim():
    # 12% weight: above 0.67 x 10% (no ADD) and below 1.5 x 10% (no TRIM); p between exit and entry
    a = advise(d=decision(p=0.60), total=1_000 / 0.12, live=PASS)
    assert a["action"] == "HOLD" and a["rule"] == "NONE" and ADD_BAND * 0.10 < 0.12 < TRIM_BAND * 0.10
    assert {t["action"] for t in a["triggers"]} == {"SELL", "TRIM", "ADD"}


def test_not_covered_symbol_is_valued_at_cost():
    a = advise_holding(holding(symbol="ZZZZ", cid=None), None, None, [], 10_000.0, 0.0, PENDING, CFG, AS_OF)
    assert a["action"] == "NOT_COVERED" and a["rule"] == "NOT_IN_UNIVERSE" and a["valueUsd"] == 1_000.0
    assert a["triggers"] == [] and a["model"]["proven"] is False


def test_review_date_is_21_weekdays_ahead():
    assert review_date(date(2026, 10, 5)) == date(2026, 11, 3)


def test_basis_changes_with_any_edit():
    h = [holding("AAA", 10, 100), holding("BBB", 5, 20)]
    assert basis_of(h, 100) == basis_of(list(reversed(h)), 100.0)
    assert basis_of(h, 100) != basis_of(h, 101)
    assert basis_of(h, 100) != basis_of([holding("AAA", 11, 100), holding("BBB", 5, 20)], 100)


# ------------------------------------------------------------------ the store
def _prices(tdb, cid, symbol, closes, start="2026-08-24"):
    days = pd.bdate_range(start, periods=len(closes))
    for d, c in zip(days, closes):
        tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, :s, :d, :x, 'test')",
                    c=cid, s=symbol, d=d.date(), x=c)
    return days[-1].date()


@pytest.fixture
def svc(tdb, universe):
    ids = {s: TickerResolver(tdb).company_ever(s) for s in ("META", "INTC")}
    last = _prices(tdb, ids["META"], "META", [100.0] * 30)
    _prices(tdb, ids["INTC"], "INTC", [20.0] * 20 + [8.0] + [8.0] * 9)   # a -60% raw move with no corporate action
    book = {ids["META"]: {**decision(p=0.45, cid=ids["META"]), "symbol": "META"},
            ids["INTC"]: {**decision(p=0.60, cid=ids["INTC"]), "symbol": "INTC"}}
    s = PortfolioService(tdb, live_test_fn=lambda: PENDING, decisions_fn=lambda d: (last, book))
    s.ids, s.day = ids, last
    return s


def test_advice_idempotent_per_portfolio_state(svc):
    svc.set_holding("meta", 10, 90)
    svc.set_holding("INTC", 50, 25)
    svc.set_holding("ZZZZ", 3, 10)
    svc.set_cash(500)
    first = svc.advise()
    assert (first.as_of, first.created, first.existing) == (svc.day, 3, 0)
    again = svc.advise()
    assert (again.created, again.existing) == (0, 3)
    svc.set_holding("META", 12, 90)                  # an edit during the day adds a new basis, the old rows stay
    assert svc.advise().created == 3
    page = svc.advice()
    by = {r["symbol"]: r for r in page["advice"]}
    # META is 56% of the portfolio: the concentration trim comes before the model's (unproven) exit, which is still listed
    assert by["META"]["shares"] == 12 and by["META"]["action"] == "TRIM" and by["META"]["headline"] == "TRIM"
    assert [r["rule"] for r in by["META"]["reasons"]] == ["OVER_CAP", "MODEL_EXIT"]
    assert by["INTC"]["action"] == "REVIEW" and by["INTC"]["rule"] == "UNEXPLAINED_MOVE"
    assert by["ZZZZ"]["action"] == "NOT_COVERED"
    assert page["totalUsd"] == pytest.approx(12 * 100 + 50 * 8 + 3 * 10 + 500)
    assert svc.db.scalar("SELECT count(*) FROM holding_advice") == 6


def test_advice_rows_are_append_only(svc):
    svc.set_holding("META", 10, 90)
    svc.advise()
    with pytest.raises(Exception, match="immutable"):
        svc.db.execute("UPDATE holding_advice SET action = 'HOLD'")


def test_removed_holding_leaves_the_page_but_not_the_record(svc):
    svc.set_holding("META", 10, 90)
    svc.set_holding("INTC", 5, 25)
    svc.advise()
    svc.remove_holding("INTC")
    assert [r["symbol"] for r in svc.advice()["advice"]] == ["META"]
    assert svc.db.scalar("SELECT count(*) FROM holding_advice WHERE symbol = 'INTC'") == 1


def test_holdings_are_validated(svc):
    from civalpha.platform.errors import BadRequest, NotFound

    for bad in (dict(symbol="", shares=1, avg_cost=1), dict(symbol="META", shares=0, avg_cost=1),
                dict(symbol="META", shares=1, avg_cost=-1), dict(symbol="ME TA", shares=1, avg_cost=1)):
        with pytest.raises(BadRequest):
            svc.set_holding(**bad)
    with pytest.raises(NotFound):
        svc.remove_holding("NOPE")


def test_buy_ideas_are_the_books_positions_not_held(svc):
    _, book = svc.decisions_fn(None)
    book[svc.ids["INTC"]]["action"] = "ENTER"       # the fixture's book holds the same dict on every call
    svc.set_holding("META", 10, 90)
    svc.advise()
    ideas = svc.advice()["ideas"]
    assert [i["symbol"] for i in ideas] == ["INTC"] and ideas[0]["proven"] is False


def test_advice_outcome_resolves_after_21_trading_days(tdb, universe):
    ids = {s: TickerResolver(tdb).company_ever(s) for s in ("META", "INTC")}
    _prices(tdb, ids["META"], "META", [100.0])
    s = PortfolioService(tdb, live_test_fn=lambda: PENDING,
                         decisions_fn=lambda d: (date(2026, 8, 24), {ids["META"]: {**decision(cid=ids["META"]), "symbol": "META"}}))
    s.set_holding("META", 10, 90)
    s.advise()
    bundle = SimpleNamespace(calendar=pd.bdate_range("2026-08-03", periods=60), tr={ids["META"]: np.linspace(100, 110, 60)},
                             bench_tr={"XLC": np.full(60, 100.0)})
    r = s.resolve_outcomes(lambda: bundle)
    assert r == {"resolved": 1, "pending": 0}
    row = s.advice()["advice"][0]
    assert row["outcome"]["excessReturn"] > 0 and row["outcome"]["windowEndDate"] > "2026-09-21"
    assert s.resolve_outcomes(lambda: pytest.fail("nothing pending: no bundle load")) == {"resolved": 0, "pending": 0}
    tr = s.track_record()
    act = next(a for a in tr["actions"] if a["action"] == row["action"])
    assert act["resolved"] == 1 and act["meanExcess"] is None          # below the 30-row threshold: no mean shown


# ------------------------------------------------------------------ privacy
def test_portfolio_is_protected_on_every_method():
    for m in ("GET", "PUT", "POST"):
        assert protected_request("/api/portfolio/holdings", m)
    assert protected_request("/api/portfolio", "GET")


def test_portfolio_api_needs_the_token_and_round_trips(api, monkeypatch):
    from civalpha.platform import settings

    r = api.put("/api/portfolio/holdings", json={"symbol": "meta", "shares": 3, "avgCostUsd": 250.5, "note": "long term"})
    assert r.status_code == 200 and r.json()["symbol"] == "META" and r.json()["companyId"] is not None
    assert api.put("/api/portfolio/cash", json={"cashUsd": 1000}).json() == {"cashUsd": 1000.0}
    assert api.put("/api/portfolio/holdings", json={"symbol": "META", "shares": -1, "avgCostUsd": 1}).status_code == 400
    got = api.get("/api/portfolio/holdings").json()
    assert got["cashUsd"] == 1000.0 and [h["symbol"] for h in got["holdings"]] == ["META"]
    assert api.get("/api/portfolio/advice").status_code == 200
    assert api.post("/api/portfolio/advice").status_code == 200
    assert api.get("/api/portfolio/track-record").json()["minResolved"] == 30
    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "s3cret")
    settings.settings.cache_clear()
    assert api.get("/api/portfolio/holdings").status_code == 401
    assert api.get("/api/portfolio/advice").status_code == 401
    assert api.get("/api/portfolio/holdings", headers={"X-Admin-Token": "s3cret"}).status_code == 200
    rm = {"json": {"symbol": "META"}, "headers": {"X-Admin-Token": "s3cret"}}
    assert api.post("/api/portfolio/holdings/remove", **rm).status_code == 204
    assert api.post("/api/portfolio/holdings/remove", **rm).status_code == 404
    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "")
    settings.settings.cache_clear()
    api.put("/api/portfolio/cash", json={"cashUsd": 0})
