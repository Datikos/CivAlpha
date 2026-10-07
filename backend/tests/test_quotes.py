"""ADR-0006: live quotes. Parsing Tiingo IEX rows with `last` null (no IEX agreement), batching, the market window,
staleness, replace-and-delete in live_quote, and the portfolio's live value."""
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from civalpha.platform.market.quotes import BATCH, QuoteService, TiingoQuotes, is_stale, market_open, market_window, parse

ROW = {"ticker": "BRK-B", "timestamp": "2026-10-07T12:55:09.010975556-04:00", "tngoLast": 505.48, "last": None,
       "prevClose": 505.54, "open": 506.5, "high": 507.07, "low": 504.32, "volume": 71141.0, "mid": 506.66,
       "bidPrice": None, "askPrice": None}


def utc(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def test_parse_uses_tngo_last_and_maps_vendor_symbols_back():
    q = parse([ROW, {**ROW, "ticker": "NOPE"}, {**ROW, "ticker": "MSFT", "tngoLast": None}], {"BRK-B": "BRK.B", "MSFT": "MSFT"})
    assert len(q) == 1 and q[0].symbol == "BRK.B" and q[0].price == Decimal("505.48") and q[0].prev_close == Decimal("505.54")
    assert q[0].quoted_at == utc("2026-10-07T16:55:09.010975+00:00") and q[0].volume == 71141


def test_requests_go_in_batches_with_the_key_in_a_header():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        tickers = request.url.params["tickers"].split(",")
        return httpx.Response(200, json=[{**ROW, "ticker": t.upper()} for t in tickers])

    src = TiingoQuotes("secret-key", httpx.Client(transport=httpx.MockTransport(handler)))
    symbols = [f"S{i}" for i in range(BATCH + 5)]
    quotes = src.fetch(symbols)
    assert len(seen) == 2 and len(quotes) == BATCH + 5
    assert all(r.headers["Authorization"] == "Token secret-key" and "secret-key" not in str(r.url) for r in seen)


@pytest.mark.parametrize("when, window, open_", [
    ("2026-10-07T09:20:00-04:00", False, False),
    ("2026-10-07T09:26:00-04:00", True, False),
    ("2026-10-07T12:00:00-04:00", True, True),
    ("2026-10-07T16:05:00-04:00", True, False),
    ("2026-10-07T16:15:00-04:00", False, False),
    ("2026-10-10T12:00:00-04:00", False, False),      # Saturday
])
def test_market_window(when, window, open_):
    assert market_window(utc(when)) is window and market_open(utc(when)) is open_


def test_stale_only_while_the_market_is_open():
    q = utc("2026-10-07T12:00:00-04:00")
    assert is_stale(q, utc("2026-10-07T12:20:00-04:00")) and not is_stale(q, utc("2026-10-07T12:10:00-04:00"))
    assert not is_stale(utc("2026-10-07T15:59:00-04:00"), utc("2026-10-07T20:00:00-04:00"))     # after the close
    assert is_stale(None, utc("2026-10-07T20:00:00-04:00"))


class FakeSource:
    name = "fake"

    def __init__(self, prices):
        self.prices = prices
        self.asked = None

    def fetch(self, symbols):
        self.asked = symbols
        from civalpha.platform.market.quotes import Quote

        return [Quote(s, Decimal(str(p)), Decimal("100"), None, None, None, 10, utc("2026-10-07T12:00:00-04:00"))
                for s, p in self.prices.items() if s in symbols]


def test_poll_replaces_quotes_and_drops_untracked_symbols(tdb, universe):
    tdb.execute("INSERT INTO live_quote (symbol, price, quoted_at, provider) VALUES ('GONE', 1, now(), 'x')")
    src = FakeSource({"META": 101, "INTC": 102, "XLC": 99})
    svc = QuoteService(tdb, src)
    assert svc.poll() == 3
    assert {"META", "INTC", "XLC"} <= set(src.asked)
    assert tdb.scalar("SELECT count(*) FROM live_quote WHERE symbol = 'GONE'") == 0
    src.prices["META"] = 105
    svc.poll()
    assert tdb.scalar("SELECT price FROM live_quote WHERE symbol = 'META'") == 105
    assert tdb.scalar("SELECT company_id FROM live_quote WHERE symbol = 'XLC'") is None


def test_portfolio_live_value_uses_quotes_and_falls_back_to_the_close(tdb, universe):
    from civalpha.platform.portfolio import PortfolioService
    from civalpha.platform.tickers import TickerResolver

    cid = TickerResolver(tdb).company_ever("META")
    tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'META', '2026-10-06', 100, 't')", c=cid)
    book = {cid: {"id": 1, "strategyKey": "AI_SIZED", "companyId": cid, "symbol": "META", "action": "HOLD", "probability": 0.5,
                  "probabilityCalibrated": None, "rank": 1, "entryP": 0.55, "exitP": 0.48, "vol21": 0.3}}
    svc = PortfolioService(tdb, live_test_fn=lambda: {"verdict": "PENDING"}, decisions_fn=lambda d: (date(2026, 10, 6), book))
    svc.set_holding("META", 10, 90)
    svc.set_holding("ZZZZ", 5, 20)
    svc.set_cash(50)
    svc.advise()
    assert svc.advice()["live"] is None                         # no quotes yet
    QuoteService(tdb, FakeSource({"META": 110})).poll()
    a = svc.advice()
    by = {r["symbol"]: r for r in a["advice"]}
    assert by["META"]["live"]["price"] == 110 and by["META"]["live"]["change"] == pytest.approx(0.10)
    assert by["META"]["live"]["valueUsd"] == 1100 and by["ZZZZ"]["live"] is None
    assert a["live"]["totalUsd"] == pytest.approx(1100 + 5 * 20 + 50)     # ZZZZ at its advice value (cost), plus cash
    assert a["live"]["changeUsd"] == pytest.approx(10 * (110 - 100))
    assert by["META"]["valueUsd"] == 1000                        # the advice row itself stays on the close


def test_models_never_read_live_quotes():
    """Nothing outside the portfolio read path and the poller mentions the table."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "civalpha"
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if "live_quote" in p.read_text())
    assert users == ["platform/market/quotes.py"], users


def test_public_quotes_leave_out_symbols_held_outside_the_universe(tdb, universe):
    from civalpha.platform.portfolio import PortfolioService

    PortfolioService(tdb).set_holding("SECRETX", 1, 1)
    QuoteService(tdb, FakeSource({"META": 101, "XLC": 99, "SECRETX": 5})).poll()
    assert tdb.scalar("SELECT count(*) FROM live_quote WHERE symbol = 'SECRETX'") == 1     # the holder's page gets it...
    out = QuoteService(tdb).public()
    assert {q["symbol"] for q in out["quotes"]} == {"META", "XLC"}                          # ...the research pages do not
    meta = next(q for q in out["quotes"] if q["symbol"] == "META")
    assert meta["change"] == pytest.approx(0.01) and meta["quotedAt"].endswith("Z")
