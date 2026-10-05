"""Universe expansion: SEC-only discovery (exchange list + public-float frames) and the batch add job."""
import json
from datetime import date

import pytest

from civalpha.platform.errors import BadRequest
from civalpha.platform.expand import SECTOR_REVIEW_TAG, UniverseExpansion, clean_candidates
from civalpha.platform.sec.client import FixtureSecClient
from civalpha.platform.sec.discovery import (ASSETS_FRAME, COMPANY_TICKERS_EXCHANGE, SHARES_FRAME, CompanyDiscovery, frame_url, parse_exchange_list,
                                             parse_float_frame, plausible, recent_frames)
from civalpha.platform.sec.profile import Profile

EXCHANGE = {"fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"],
                     [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"], [19617, "JPMORGAN CHASE & CO", "JPM", "NYSE"],
                     [1326801, "Meta Platforms, Inc.", "META", "Nasdaq"], [99, "Tiny Corp", "TINY", "Nasdaq"],
                     [77, "Pink Sheet Co", "PINK", "OTC"], [55, "Warrant", "WT.WS+", "NYSE"], ["", "No CIK", "NOCK", "NYSE"]]}


def _frame(rows):
    return {"taxonomy": "dei", "tag": "EntityPublicFloat", "data": [{"cik": c, "end": e, "val": v} for c, e, v in rows]}


class _Client:
    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        b = self.bodies.get(url)
        return None if b is None else json.dumps(b).encode()

    def mode(self):
        return "fake"


class _Factory:
    def __init__(self, client):
        self.client = client

    def configured(self):
        return self.client


def test_exchange_list_and_float_frames_parse_defensively():
    ls = parse_exchange_list(json.dumps(EXCHANGE).encode())
    assert [l.ticker for l in ls] == ["AAPL", "GOOGL", "GOOG", "JPM", "META", "TINY", "PINK"]   # odd ticker and blank CIK dropped
    assert ls[0].cik == "0000320193" and ls[3].exchange == "NYSE"
    f = parse_float_frame(json.dumps(_frame([(320193, "2025-03-28", 2.9e12), (320193, "2024-03-29", 2.6e12), (99, "2025-06-30", "x"),
                                             (19617, "2025-06-30", -5)])).encode())
    assert f == {"0000320193": (2.9e12, date(2025, 3, 28))}          # newest wins; junk and negatives dropped
    assert parse_exchange_list(b"{}") == [] and parse_float_frame(b"[]") == {}
    assert recent_frames(date(2026, 10, 5)) == ["CY2026Q3I", "CY2026Q2I", "CY2026Q1I", "CY2025Q4I", "CY2025Q3I", "CY2025Q2I",
                                                "CY2025Q1I", "CY2024Q4I"]
    assert recent_frames(date(2026, 1, 15), 2) == ["CY2025Q4I", "CY2025Q3I"]
    assert frame_url("CY2026Q2I").endswith("/dei/EntityPublicFloat/USD/CY2026Q2I.json")
    with pytest.raises(ValueError):
        frame_url("nope")


def test_discovery_ranks_by_float_one_ticker_per_company_and_skips_tracked():
    today = date(2026, 10, 5)
    names = recent_frames(today)
    urls = {n: frame_url(n) for n in names}
    assets = {**{frame_url(n, ASSETS_FRAME): None for n in names}, **{frame_url(n, SHARES_FRAME): None for n in names}}
    assets[frame_url("CY2025Q2I", ASSETS_FRAME)] = _frame([(320193, "2025-06-28", 3.3e11), (1652044, "2025-06-30", 4.5e11),
                                                            (19617, "2025-06-30", 4.0e12), (99, "2025-06-30", 1e8)])
    bodies = {**assets, COMPANY_TICKERS_EXCHANGE: EXCHANGE,
              urls["CY2026Q3I"]: None,                                                  # frames not published yet
              urls["CY2026Q2I"]: None,
              urls["CY2026Q1I"]: _frame([(320193, "2026-03-27", 3.0e12)]),            # Apple's second fiscal quarter ends in March
              urls["CY2025Q4I"]: None,
              urls["CY2025Q3I"]: _frame([(1326801, "2025-09-30", 1.5e12), (1652044, "2025-09-30", 1.0e12)]),
              urls["CY2025Q2I"]: _frame([(1652044, "2025-06-30", 2.1e12), (19617, "2025-06-30", 6e11), (99, "2025-06-30", 5e8),
                                         (1326801, "2025-06-30", 1.4e12)]),
              urls["CY2025Q1I"]: _frame([(320193, "2025-03-28", 2.6e12)]),
              urls["CY2024Q4I"]: _frame([(99, "2024-12-31", 5e14)])}                  # Tiny's float tagged 1,000,000x too big
    client = _Client(bodies)
    d = CompanyDiscovery(_Factory(client))
    r = d.candidates(["Nasdaq", "NYSE"], 1e9, 10, exclude_ciks={"0001326801"}, today=today)
    assert [c.symbol for c in r.candidates] == ["AAPL", "GOOGL", "JPM"]      # GOOG dropped (same CIK), META tracked, TINY too small
    assert not any("implausible" in n for n in r.notes)                      # Tiny's bad value is skipped for its plausible one
    assert r.candidates[1].public_float == 1.0e12 and r.candidates[1].float_as_of == date(2025, 9, 30)   # newest float date wins
    assert r.candidates[0].public_float == 3.0e12
    assert (r.listed, r.matched, r.already_tracked) == (5, 4, 1)
    assert r.frames == ["CY2026Q1I", "CY2025Q3I", "CY2025Q2I", "CY2025Q1I", "CY2024Q4I"] and sum("not available" in n for n in r.notes) == 3
    n = len(client.calls)
    r2 = d.candidates(["nasdaq"], 2.5e12, 5, today=today)                   # cached: no new requests; case-insensitive exchange
    assert [c.symbol for c in r2.candidates] == ["AAPL"] and len(client.calls) == n
    assert d.candidates(["NYSE"], 1e9, 0, today=today).candidates == []


def test_scale_errors_in_the_public_float_are_ignored():
    assert plausible(3.0e12, 3.3e11) and plausible(1e10, None) and plausible(5e10, 0)
    assert plausible(3.25e11, 1.35e11, 1.77e9)   # AbbVie: tiny book equity, ordinary share price
    assert plausible(2.5e10, 7e9, 2.8e6)         # NVR: $8,900 a share is real
    assert plausible(9e11, 1.15e12, 5e5)         # Berkshire class A: $1.8M a share, but the float is below its assets
    assert not plausible(4.4e15, 2e9)            # a $4 billion float tagged as $4 quadrillion
    assert not plausible(6.8e12, 1.9e9)          # a 1000x error on an asset-light company
    assert not plausible(9.6e11, 1.8e10, 5.9e7)  # a bank: assets 18B let 961B through; $16,000 a share does not
    assert not plausible(6e12, None)             # above any listed company even without assets
    assert plausible(9.6e11, None, 5.9e7) is False and plausible(9.6e8, None, 5.9e7)
    today = date(2026, 10, 5)
    names = recent_frames(today)
    bodies = {COMPANY_TICKERS_EXCHANGE: EXCHANGE, **{frame_url(n): None for n in names},
              **{frame_url(n, ASSETS_FRAME): None for n in names}, **{frame_url(n, SHARES_FRAME): None for n in names}}
    bodies[frame_url("CY2025Q2I")] = _frame([(320193, "2025-06-30", 4.4e15), (19617, "2025-06-30", 6e11), (1326801, "2025-06-30", 9e11)])
    bodies[frame_url("CY2025Q2I", ASSETS_FRAME)] = _frame([(320193, "2025-06-30", 3.3e11), (1326801, "2025-06-30", 1.8e10)])
    bodies[frame_url("CY2025Q2I", SHARES_FRAME)] = _frame([(1326801, "2025-06-30", 5.9e7)])
    r = CompanyDiscovery(_Factory(_Client(bodies))).candidates(["Nasdaq", "NYSE"], 1e9, 10, today=today)
    assert [c.symbol for c in r.candidates] == ["JPM"] and any("2 companies ignored" in n for n in r.notes)


def test_fixture_client_serves_frames(tmp_path):
    (tmp_path / "frames" / "dei" / "EntityPublicFloat" / "USD").mkdir(parents=True)
    (tmp_path / "frames" / "dei" / "EntityPublicFloat" / "USD" / "CY2026Q2I.json").write_bytes(b'{"data": []}')
    assert FixtureSecClient(tmp_path).get(frame_url("CY2026Q2I")) == b'{"data": []}'
    assert FixtureSecClient(tmp_path).get(frame_url("CY2026Q1I")) is None


def test_candidates_are_validated():
    assert clean_candidates([{"symbol": "aapl", "cik": "320193", "name": "Apple"}, {"symbol": "X", "cik": "320193"}]) == \
        [{"symbol": "AAPL", "cik": "320193", "name": "Apple"}]
    for bad in (None, [], [{"symbol": "AAPL"}], [{"cik": "1"}], "AAPL"):
        with pytest.raises(BadRequest):
            clean_candidates(bad)


class _Profiler:
    """Profiles from a table: sector by symbol; 'review' marks an ambiguous SIC code."""

    def __init__(self, table):
        self.table = table

    def profile(self, symbol):
        p = Profile(symbol)
        row = self.table.get(symbol)
        if row is None:
            p.warnings.append(f"{symbol} is not in SEC company_tickers.json")
            return p
        p.cik, p.name, p.sector, p.benchmark_symbol, p.industry, p.sector_confidence = row
        return p


class _Jobs:
    def __init__(self):
        self.submitted = []

    def submit(self, job_type, params=None):
        self.submitted.append((job_type, params))
        return {"id": 42}


def test_expansion_adds_companies_with_sectors_tags_and_a_price_sync(universe, tdb):
    table = {"AAPL": ("0000320193", "Apple Inc.", "Technology", "XLK", "CONSUMER_ELECTRONICS", "high"),
             "MSFT": ("0000789019", "Microsoft Corp", "Technology", "XLK", "SOFTWARE", "review"),
             "NOSIC": ("0000000077", "No Sector Inc", None, None, None, None)}
    jobs = _Jobs()
    lines = []
    ingested = []
    x = UniverseExpansion(tdb, universe, _Profiler(table), jobs)
    r = x.run({"candidates": [{"symbol": "AAPL", "cik": "320193"}, {"symbol": "MSFT", "cik": "789019"},
                              {"symbol": "NOSIC", "cik": "77"}, {"symbol": "GHOST", "cik": "88"},
                              {"symbol": "META", "cik": "1326801"}],
               "tag": "mega", "ingestSec": True, "syncPrices": True}, lines.append,
              ingest=lambda cid, log: ingested.append(cid))
    assert [a["symbol"] for a in r.added] == ["AAPL", "MSFT"] and r.review == 1 and r.price_job == 42
    assert {s["symbol"] for s in r.skipped} == {"NOSIC", "GHOST", "META"}
    assert jobs.submitted[0][0] == "PRICE_SYNC" and ingested == [a["companyId"] for a in r.added]
    syms = {c["id"]: c for c in universe.companies()}
    msft = next(a for a in r.added if a["symbol"] == "MSFT")
    assert syms[msft["companyId"]]["sector"] == "Technology" and syms[msft["companyId"]]["benchmark_symbol"] == "XLK"
    assert universe.tags_of(msft["companyId"]) == ["mega", SECTOR_REVIEW_TAG]
    aapl = next(a for a in r.added if a["symbol"] == "AAPL")
    assert universe.tags_of(aapl["companyId"]) == ["mega"]
    assert any("added 2 companies" in l for l in lines)
    # nothing to add: no price sync is queued
    r2 = UniverseExpansion(tdb, universe, _Profiler(table), _Jobs()).run({"candidates": [{"symbol": "AAPL", "cik": "320193"}]}, lines.append)
    assert r2.added == [] and r2.price_job is None and "already" in r2.skipped[0]["reason"]


def test_expand_endpoint_validates_before_queueing(api):
    assert api.post("/api/admin/universe/expand", json={"candidates": []}).status_code == 400
    assert api.post("/api/admin/universe/expand", json={"candidates": [{"symbol": "AAPL"}]}).status_code == 400
    r = api.post("/api/admin/universe/expand", json={"candidates": [{"symbol": "AAPL", "cik": "320193"}], "memberSince": "2999-01-01"})
    assert r.status_code == 400 and "future" in r.json()["error"]
    assert api.get("/api/admin/universe/discover?limit=0").status_code == 400
    assert api.get("/api/admin/universe/discover?exchanges=").status_code == 400
