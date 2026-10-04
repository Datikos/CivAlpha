"""Market data: provider parsing, sessions, CSV import with ticker changes, versioned corrections and the price sync
(ported from the Java PriceProvidersTest and PlatformIntegrationTest)."""
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from civalpha.platform.errors import Problem
from civalpha.platform.macro import MacroService, Observation, parse_csv
from civalpha.platform.market import (ActionRow, MarketDataService, PriceBarRow, PriceProvider, PriceSyncService,
                                        RateLimited, Series, TiingoProvider, YahooChartProvider, last_completed_session,
                                        latest_weekday, parse_bars, vendor_symbol)
from civalpha.platform.settings import Fred, Prices
from civalpha.platform.storage import DocumentStore
from civalpha.platform.tickers import TickerResolver


# ---------------------------------------------------------------------------------------------- providers (unit)
def test_parses_yahoo_chart_with_events_and_skips_empty_bars():
    # 2024-06-07 and 2024-06-10 at 09:30 New York (13:30 UTC); a null bar in between
    body = b"""
        {"chart":{"result":[{"meta":{"symbol":"NVDA","exchangeTimezoneName":"America/New_York","gmtoffset":-14400},
          "timestamp":[1717767000,1717853400,1718026200],
          "events":{"dividends":{"1718026200":{"amount":0.01,"date":1718026200}},
                    "splits":{"1718026200":{"date":1718026200,"numerator":10,"denominator":1,"splitRatio":"10:1"}}},
          "indicators":{"quote":[{"open":[119.77,null,120.37],"high":[121.0,null,123.1],"low":[118.6,null,117.01],
                                  "close":[120.888,null,121.79],"volume":[412386000,null,314162700]}]}}],"error":null}}"""
    s = YahooChartProvider.parse(body, "NVDA", "u")
    assert len(s.bars) == 2
    assert s.bars[0].date == date(2024, 6, 7)
    assert s.bars[0].close == Decimal("120.8880")
    assert str(s.bars[0].close) == "120.8880"
    assert s.bars[1].date == date(2024, 6, 10)
    assert sorted(a.type for a in s.actions) == ["CASH_DIVIDEND", "SPLIT"]
    (split,) = [a for a in s.actions if a.type == "SPLIT"]
    assert split.value == Decimal(10)


def test_parses_tiingo_raw_closes_with_split_factor_and_dividends():
    body = b"""
        [{"date":"2024-06-07T00:00:00.000Z","close":1208.88,"high":1216.9,"low":1180.22,"open":1197.7,"volume":41238600,
          "adjClose":120.86,"divCash":0.0,"splitFactor":1.0},
         {"date":"2024-06-10T00:00:00.000Z","close":121.79,"high":123.1,"low":117.01,"open":120.37,"volume":314162700,
          "adjClose":121.77,"divCash":0.01,"splitFactor":10.0}]"""
    s = TiingoProvider.parse(body, "NVDA", "u")
    assert [b.date for b in s.bars] == [date(2024, 6, 7), date(2024, 6, 10)]
    assert s.bars[0].close == Decimal("1208.88")   # raw, not split-adjusted
    assert sorted(a.type for a in s.actions) == ["CASH_DIVIDEND", "SPLIT"]


def test_never_stores_the_current_session_before_the_close():
    ny = ZoneInfo("America/New_York")
    assert last_completed_session(datetime(2026, 10, 2, 15, 0, tzinfo=ny)) == date(2026, 10, 1)
    assert last_completed_session(datetime(2026, 10, 2, 17, 0, tzinfo=ny)) == date(2026, 10, 2)
    assert vendor_symbol("brk.b") == "BRK-B"


def test_a_friday_bar_is_current_over_the_weekend():
    assert latest_weekday(date(2026, 10, 3)) == date(2026, 10, 2)
    assert latest_weekday(date(2026, 10, 4)) == date(2026, 10, 2)
    assert latest_weekday(date(2026, 10, 5)) == date(2026, 10, 5)


def test_price_csv_validation_and_macro_csv():
    with pytest.raises(ValueError, match="price CSV needs column 'close'"):
        parse_bars(b"symbol,date\nXLC,2024-01-02\n")
    with pytest.raises(ValueError, match="empty CSV"):
        parse_bars(b"")
    rows = parse_bars("﻿Symbol,Date,Close,Volume\r\nxlc,2024-01-02,60.50,\r\n\r\n".encode())
    assert rows == [PriceBarRow("XLC", date(2024, 1, 2), None, None, None, Decimal("60.50"), None)]
    obs = parse_csv("series_id,obs_date,value,realtime_start,realtime_end\nFEDFUNDS,2024-01-01,5.33,2024-02-01,\nFEDFUNDS,2024-02-01,.,2024-03-01,2024-04-01\n")
    assert obs == [Observation("FEDFUNDS", date(2024, 1, 1), 5.33, date(2024, 2, 1), None),
                   Observation("FEDFUNDS", date(2024, 2, 1), None, date(2024, 3, 1), date(2024, 4, 1))]


# ---------------------------------------------------------------------------------------------- database
@pytest.fixture
def docs(tdb, tmp_path):
    return DocumentStore(tdb, tmp_path / "docs")


@pytest.fixture
def market(tdb, docs, universe):
    return MarketDataService(tdb, docs, TickerResolver(tdb), universe)


@pytest.fixture
def price_sync(tdb, docs, universe, market):
    return PriceSyncService(tdb, market, docs, universe, TickerResolver(tdb), Prices("none", "", date(2019, 1, 2)), pause=0)


def meta(tdb) -> int:
    return TickerResolver(tdb).company_by_cik("0001326801")


def test_ticker_change_keeps_one_company_across_prices_and_lookups(tdb, market, universe):
    csv = (b"symbol,date,open,high,low,close,volume\n"
           b"FB,2022-06-08,195,197,193,196.64,100\n"
           b"META,2022-06-09,194,195,184,184.00,100\n"
           b"XLC,2022-06-08,60,61,59,60.5,100\n"
           b"XLC,2022-06-09,60,61,59,59.9,100\n"
           b"FB,2022-06-10,1,1,1,1,1\n")
    r = market.import_prices(csv, "ticker-change.csv", "test", False)
    # FB after the rename is not a valid symbol for this company any more
    assert r.unknown_symbols == ["FB"]
    assert tdb.scalars("SELECT DISTINCT company_id FROM price_bar WHERE symbol IN ('FB','META')") == [meta(tdb)]
    t = TickerResolver(tdb)
    assert t.company_ever("FB") == meta(tdb)
    assert t.company_at("FB", date(2021, 1, 4)) == meta(tdb)
    assert t.company_at("META", date(2021, 1, 4)) is None
    # the company lookup endpoint resolves /companies/FB to the current symbol
    assert t.current_symbol(t.company_ever("FB")) == "META"

    # a newly observed symbol (e.g. from SEC submissions) closes the current span and preserves history
    change = date(2030, 1, 2)
    assert "META -> MTAX" in universe.record_observed_ticker("0001326801", "MTAX", change, "test")
    assert t.company_at("META", date(2030, 1, 1)) == meta(tdb)
    assert t.company_at("META", change) is None
    assert t.company_at("MTAX", change) == meta(tdb)
    assert universe.record_observed_ticker("0001326801", "MTAX", date(2030, 1, 7), "test") is None   # idempotent


def test_missing_benchmarks_are_reported_before_calling_the_model(tdb, market):
    tdb.execute("DELETE FROM price_bar WHERE company_id IS NULL")
    assert market.missing_benchmarks() == ["XLC"]
    with pytest.raises(Problem, match=r"No benchmark ETF prices are loaded \(XLC\)"):
        market.require_benchmarks()
    tdb.execute("INSERT INTO price_bar (symbol, trade_date, close, provider) VALUES ('XLC', '2024-01-02', 60, 'test')")
    assert market.missing_benchmarks() == []
    assert market.require_benchmarks() is None


class FakeHistory(PriceProvider):
    """Returns META history under today's symbol, including dates when it traded as FB."""
    name = "fake"
    split_adjusted = False

    def fetch(self, symbol, from_, to):
        d1, d2 = date(2022, 6, 3), date(2022, 6, 10)
        bars = [PriceBarRow(symbol, d1, None, None, None, Decimal("190.78"), 1),
                PriceBarRow(symbol, d2, None, None, None, Decimal("175.57"), 1)]
        acts = [ActionRow(symbol, d2, "CASH_DIVIDEND", Decimal("0.10"), None)]
        return Series(bars, acts, b"[]", "application/json", "https://fake/" + symbol)


def test_price_sync_stores_provider_bars_under_the_ticker_valid_on_each_date(tdb, price_sync, market):
    fake = FakeHistory()
    log: list[str] = []
    summary = price_sync.sync(log.append, fake)
    assert summary.failed == 0
    assert tdb.scalar("SELECT symbol FROM price_bar WHERE company_id = :c AND trade_date = '2022-06-03'", c=meta(tdb)) == "FB"
    assert tdb.scalar("SELECT symbol FROM price_bar WHERE company_id = :c AND trade_date = '2022-06-10'", c=meta(tdb)) == "META"
    assert tdb.scalar("SELECT count(*) FROM price_bar WHERE company_id IS NULL AND symbol = 'XLC' AND trade_date = '2022-06-10'") == 1
    assert "XLC" not in market.missing_benchmarks()
    assert log[0].startswith("price sync from fake for 3 symbols through ")
    assert "META: 2 new bars, 0 corrected, 1 corporate actions" in log
    # running again only re-checks the overlap window: nothing new
    assert price_sync.sync(log.append, fake).inserted == 0
    # never mixed into the synthetic demo
    tdb.execute("UPDATE company SET is_demo = true WHERE id = :c", c=meta(tdb))
    with pytest.raises(Problem, match="synthetic demo"):
        price_sync.sync(log.append, fake)


def test_price_corrections_are_versioned_not_dropped(tdb, market):
    v1 = b"symbol,date,open,high,low,close,volume\nXLC,2023-03-01,60,61,59,60.00,100\nXLC,2023-03-02,60,61,59,61.00,100\n"
    first = market.import_prices(v1, "vendor-v1.csv", "vendor", False)
    assert first.inserted == 2
    v2 = b"symbol,date,open,high,low,close,volume\nXLC,2023-03-01,60,61,59,60.00,100\nXLC,2023-03-02,60,61,59,61.50,120\n"
    second = market.import_prices(v2, "vendor-v2.csv", "vendor", False)
    assert second.inserted == 0
    assert second.unchanged == 1
    assert second.revised == 1
    bar = tdb.one("SELECT close, version FROM price_bar WHERE symbol = 'XLC' AND trade_date = '2023-03-02'")
    assert float(bar["close"]) == 61.5
    assert bar["version"] == 2
    old = tdb.one("""
        SELECT r.close, r.version, d1.url AS was_from, d2.url AS replaced_by FROM price_bar_revision r
        JOIN source_document d1 ON d1.id = r.source_document_id JOIN source_document d2 ON d2.id = r.superseded_by_document_id
        WHERE r.symbol = 'XLC' AND r.trade_date = '2023-03-02'""")
    assert float(old["close"]) == 61.0
    assert old["version"] == 1
    assert old["was_from"] == "file://vendor-v1.csv"
    assert old["replaced_by"] == "file://vendor-v2.csv"
    # re-importing the corrected file again changes nothing
    assert market.import_prices(v2, "vendor-v2.csv", "vendor", False).revised == 0


def test_price_sync_stops_at_the_rate_limit_and_skips_symbols_that_are_current(price_sync):
    state = {"calls": 0, "limited": True}

    class Fake(PriceProvider):
        name = "fake"
        split_adjusted = False

        def fetch(self, symbol, from_, to):
            state["calls"] += 1
            if state["calls"] == 2 and state["limited"]:
                raise RateLimited("fake request limit reached")
            bar = PriceBarRow(symbol, to, None, None, None, Decimal("100"), 1)
            return Series([bar], [], b"[]", "application/json", "https://fake/" + symbol)

    fake = Fake()
    log: list[str] = []
    # the second request is refused: the sync stops instead of spending the rest of the quota
    first = price_sync.sync(log.append, fake)
    assert state["calls"] == 2
    assert first.inserted == 1
    assert first.failed == first.symbols - 1
    assert any("fake request limit reached" in line and "next run" in line for line in log)

    # next run: every symbol is fetched once; the one already current is skipped without a request
    state["limited"] = False
    state["calls"] = 10
    second = price_sync.sync(log.append, fake)
    assert state["calls"] - 10 == second.symbols - 1
    assert second.failed == 0

    # a run with everything current makes no requests at all
    state["calls"] = 10
    assert price_sync.sync(log.append, fake).inserted == 0
    assert state["calls"] == 10


def test_unconfigured_provider_and_macro_store(tdb, docs, price_sync):
    with pytest.raises(Problem, match="No price provider is configured"):
        price_sync.sync(lambda _: None)
    assert price_sync.provider_name() == "none"
    macro = MacroService(tdb, docs, Fred("", "https://fred.invalid", []))
    with pytest.raises(Problem, match="FRED_API_KEY is not set"):
        macro.fetch_fred("FEDFUNDS")
    macro.upsert_series("FEDFUNDS", "Federal Funds Rate", "Percent", "Monthly", "test")
    obs = [Observation("FEDFUNDS", date(2024, 1, 1), 5.33, date(2024, 2, 1), None)]
    assert macro.insert(obs, False) == 1
    assert macro.insert([Observation("FEDFUNDS", date(2024, 1, 1), 5.33, date(2024, 2, 1), date(2024, 3, 1))], False) == 1
    assert tdb.scalar("SELECT realtime_end FROM macro_observation WHERE series_id = 'FEDFUNDS'") == date(2024, 3, 1)
