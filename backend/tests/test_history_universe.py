"""ADR-0012: the point-in-time history universe (the yearly top by public float, as known on 1 April), its membership
spans, ticker matching for companies that stopped trading, the price backfill, and the weekly studies."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from civalpha.platform import universe_pit as up
from civalpha.platform.history_backfill import HistoryBackfill
from civalpha.platform.market.sync import PriceSyncService, Target
from civalpha.platform.tasks import study_day


def test_reconstitution_dates_and_frames():
    assert up.reconstitution_day(2015) == date(2015, 4, 1)
    assert up.reconstitution_day(2018) == date(2018, 4, 2)          # 2018-03-30 Good Friday, 04-01 a Sunday
    assert up.frames_for(2015) == ["CY2014Q2I", "CY2014Q1I", "CY2013Q4I", "CY2013Q3I"]   # floats known by 1 April 2015


def test_top_floats_reject_scale_errors_and_keep_the_largest():
    floats = {"A": (9e11, None), "B": (5e10, None), "C": (4e15, None), "D": (3e12, None), "E": (1e9, None)}
    assets = {"D": (1e9, None)}                                       # a $3T float on $1B of assets: a scale error
    assert up.top_floats(floats, assets, {}, n=2) == [("A", 9e11), ("B", 5e10)]
    assert up.newest([{"A": (1.0, date(2014, 6, 30))}, {"A": (2.0, date(2014, 3, 31)), "B": (3.0, None)}]) == \
        {"A": (1.0, date(2014, 6, 30)), "B": (3.0, None)}


def test_spans_merge_years_end_at_the_missed_reconstitution_and_at_the_owners_start():
    members = {2015: {"A", "B"}, 2016: {"A"}, 2017: {"A", "B"}, 2018: {"B"}}
    s = up.spans(members, 2018)
    assert s["A"] == [(date(2015, 4, 1), date(2018, 4, 2))]
    assert s["B"] == [(date(2015, 4, 1), date(2016, 4, 1)), (date(2017, 4, 3), up.OWNER_START)]
    assert up.clip(s["B"], date(2019, 6, 14)) == [(date(2015, 4, 1), date(2016, 4, 1)), (date(2017, 4, 3), date(2019, 6, 15))]
    assert up.clip(s["B"], date(2016, 1, 4)) == [(date(2015, 4, 1), date(2016, 1, 5))]


def test_names_and_cover_symbols():
    assert up.name_similarity("TWITTER, INC.", "Twitter Inc") == 1.0
    assert up.name_similarity("Activision Blizzard, Inc.", "Activision Blizzard Inc") == 1.0
    assert up.name_similarity("Exxon Mobil Corporation", "Xilinx Inc") == 0.0
    assert up.name_similarity("Celanese Corp", "Celanese Corp - Series A") == 1.0       # Tiingo's share-class suffix
    assert up.name_similarity("V F CORP", "VF Corp") == 1.0 and up.norm_name("A O Smith Corp") == "ao smith"
    cover = ("Securities registered pursuant to Section 12(b) of the Act: Title of each class Trading Symbol(s) Name of each "
             "exchange on which registered Common Stock, par value $0.000005 per share TWTR New York Stock Exchange")
    assert up.cover_symbol(cover) == "TWTR" and up.cover_symbol("no table here") is None


def _frame(rows):
    return json.dumps({"data": [{"cik": c, "entityName": n, "val": v, "end": e} for c, n, v, e in rows]}).encode()


def test_reconstitute_reads_four_frames_per_year():
    calls = []

    def get(url):
        calls.append(url)
        if "EntityPublicFloat" in url and "CY2014Q2I" in url:
            return _frame([(1, "Big Co", 9e11, "2014-06-30"), (2, "Mid Co", 4e10, "2014-06-30"), (3, "Bad Co", 9e15, "2014-06-30")])
        if "EntityPublicFloat" in url and "CY2013Q4I" in url:
            return _frame([(4, "June Co", 6e10, "2013-12-31")])
        return None
    r = up.reconstitute(get, 2015, 2015, n=2)
    assert r.members[2015] == {"0000000001", "0000000004"} and r.names["0000000004"] == "June Co"
    assert len(calls) == 12                                            # 4 quarters x float, assets, shares


# ------------------------------------------------------------------------------------------- the universe step

class _Sec:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def get(self, url):
        self.calls.append(url)
        return self.bodies.get(url)


class _Tiingo:
    def __init__(self, meta, dates, closes=None):
        self.meta, self.d, self.c = meta, dates, closes or {}

    def all_meta(self):
        return self.meta

    def closes(self, t, start):
        return self.c.get(t.lower(), [])

    def dates(self, t):
        return self.d.get(t.lower(), (None, None, None))


def _rec(members, names, details=None):
    """A Reconstitution as `reconstitute` returns it: every member ranked, unchecked unless `details` gives a candidate's
    (float, float date, shares, assets)."""
    details = details or {}
    ranked = {y: [up.Candidate(c, *details.get(c, (1e11, None, None, None))) for c in sorted(m)] for y, m in members.items()}
    return up.Reconstitution(members={y: set(m) for y, m in members.items()}, names=names, ranked=ranked)


def _subs(cik, name, tickers, sic):
    return json.dumps({"cik": cik, "name": name, "tickers": tickers, "exchanges": [], "sic": sic, "sicDescription": "",
                       "filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": [],
                                              "acceptanceDateTime": [], "primaryDocument": [], "items": []}, "files": []}}).encode()


def test_the_universe_step_adds_companies_of_the_past_with_their_spans(universe, tdb, tmp_path, monkeypatch):
    from civalpha.platform.sec.client import submissions_url
    from civalpha.platform.storage import DocumentStore
    meta = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801'")
    rec = _rec({2015: {"0001326801", "0001418091", "0000000777", "0000000888"}, 2016: {"0001326801", "0001418091"},
                2017: {"0001326801"}},
               {"0001418091": "TWITTER, INC.", "0000000777": "Holding Shell", "0000000888": "Ghost Corp"})
    monkeypatch.setattr("civalpha.platform.history_backfill.reconstitute", lambda get: rec)
    sec = _Sec({submissions_url("0001418091"): _subs("1418091", "Twitter, Inc.", [], "7370"),
                submissions_url("0000000777"): _subs("777", "Holding Shell", [], ""),           # no SIC code: no sector
                submissions_url("0000000888"): _subs("888", "Ghost Corp", [], "7370")})
    tiingo = _Tiingo(meta=[{"ticker": "twtr", "name": "Twitter Inc"}],
                     dates={"twtr": ("Twitter Inc", date(2013, 11, 7), date(2022, 10, 28))})
    h = HistoryBackfill(tdb, sec=sec, tiingo=tiingo, docs=DocumentStore(tdb, tmp_path))
    lines = []
    out = h.step_universe(lines.append)
    assert out.known == 1 and out.added == 1 and out.tickers == {"name": 1}
    assert {k: len(v) for k, v in out.skipped.items()} == {"no sector for the SIC code": 1, "no ticker": 1}
    tw = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001418091'")
    th = tdb.one("SELECT symbol, valid_to FROM ticker_history WHERE company_id = :c", c=tw)
    assert th["symbol"] == "TWTR" and th["valid_to"] == date(2022, 10, 29)
    spans = tdb.all("SELECT universe, valid_from, valid_to FROM universe_membership WHERE company_id = :c ORDER BY valid_from", c=tw)
    assert [(s["universe"], s["valid_from"], s["valid_to"]) for s in spans] == [("history-top350", date(2015, 4, 1), date(2017, 4, 3))]
    assert tdb.scalar("SELECT count(*) FROM universe_membership WHERE company_id = :c AND universe = 'history-top350'", c=meta) == 1
    assert "history" in tdb.scalars("SELECT tag FROM company_tag WHERE company_id = :c", c=tw)
    # the owner's universe does not change, and a past company is not a member today
    assert tw not in [c["id"] for c in universe.companies()]
    again = h.step_universe(lambda _l: None)                           # a rerun replaces the spans, adds nothing
    assert again.added == 0 and tdb.scalar("SELECT count(*) FROM universe_membership WHERE company_id = :c", c=tw) == 1


# ------------------------------------------------------------------------------------------- price backfill

class _Provider:
    name, split_adjusted = "fake", False

    def __init__(self):
        self.calls = []

    def fetch(self, symbol, from_, to):
        from civalpha.platform.market.csv_prices import PriceBarRow
        from civalpha.platform.market.providers import Series
        self.calls.append((symbol, from_, to))
        bars = [PriceBarRow(symbol, from_, 10.0, 10.0, 10.0, 10.0, 100)]
        return Series(bars, [], b"[]", "application/json", "fake://" + symbol)


def test_price_backfill_fetches_only_the_missing_years(universe, tdb, tmp_path, monkeypatch):
    from civalpha.platform.storage import DocumentStore
    svc = PriceSyncService(tdb, docs=DocumentStore(tdb, tmp_path), pause=0)
    prov = _Provider()
    monkeypatch.setattr(svc, "provider", lambda: prov)
    intc = tdb.scalar("SELECT company_id FROM ticker_history WHERE symbol = 'INTC'")
    tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'INTC', '2019-01-02', 50, 't')", c=intc)
    tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (NULL, 'XLC', '2015-01-05', 50, 't')")
    out = svc.backfill([(Target(intc, "INTC"), None), (Target(None, "XLC"), None), (Target(intc, "INTC"), date(2018, 6, 1))],
                       date(2015, 1, 2), lambda _l: None)
    assert prov.calls[0] == ("INTC", date(2015, 1, 2), date(2019, 1, 1))     # up to the day before the first stored bar
    assert len(prov.calls) == 1 and out.inserted == 1                        # XLC already reaches the start; INTC now does


# ------------------------------------------------------------------------------------------- the weekly studies

def test_scheduled_runs_study_on_the_study_day_only():
    ny = ZoneInfo("America/New_York")
    assert study_day({}) and study_day({"trigger": None})                                       # by hand: always
    assert study_day({"trigger": "schedule"}, datetime(2026, 10, 23, 18, 30, tzinfo=ny))        # a Friday in New York
    assert not study_day({"trigger": "schedule"}, datetime(2026, 10, 26, 18, 30, tzinfo=ny))    # Monday
    assert study_day({"trigger": "catch-up", "session": "2026-10-23"}, datetime(2026, 10, 24, 9, 0, tzinfo=ny))   # Friday's session


def test_a_predecessor_registrant_counts_for_its_stored_successor(universe, tdb, tmp_path, monkeypatch):
    """Medtronic Inc became Medtronic plc in 2015; Facebook's old filer and today's META share the stock."""
    from civalpha.platform.history_backfill import merge_spans
    from civalpha.platform.sec.client import submissions_url
    from civalpha.platform.storage import DocumentStore
    meta = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801'")
    rec = _rec({2015: {"0000000555"}, 2016: {"0000000555"}, 2017: {"0001326801"}}, {"0000000555": "META PLATFORMS HOLDINGS INC"})
    monkeypatch.setattr("civalpha.platform.history_backfill.reconstitute", lambda get: rec)
    sec = _Sec({submissions_url("0000000555"): _subs("555", "Meta Platforms Holdings Inc", [], "7370")})
    tiingo = _Tiingo(meta=[{"ticker": "meta", "name": "Meta Platforms Inc"}],
                     dates={"meta": ("Meta Platforms Inc", date(2012, 5, 18), date(2026, 10, 23))})
    out = HistoryBackfill(tdb, sec=sec, tiingo=tiingo, docs=DocumentStore(tdb, tmp_path)).step_universe(lambda _l: None)
    assert out.added == 0 and out.tickers == {"successor": 1}
    spans = [(s["valid_from"], s["valid_to"]) for s in tdb.all(
        "SELECT valid_from, valid_to FROM universe_membership WHERE company_id = :c AND universe = 'history-top350' ORDER BY 1", c=meta)]
    assert spans == [(date(2015, 4, 1), up.OWNER_START)]                     # predecessor 2015-16 + own 2017- merged
    assert merge_spans([(date(2017, 1, 1), date(2018, 1, 1)), (date(2015, 1, 1), date(2017, 1, 1)), (date(2020, 1, 1), date(2021, 1, 1))]) == \
        [(date(2015, 1, 1), date(2018, 1, 1)), (date(2020, 1, 1), date(2021, 1, 1))]


def test_a_rerun_can_be_limited_to_some_companies(universe, tdb, tmp_path):
    from civalpha.platform.history_backfill import STEP_WEIGHT, STEPS
    from civalpha.platform.storage import DocumentStore
    h = HistoryBackfill(tdb, sec=_Sec({}), tiingo=_Tiingo([], {}), docs=DocumentStore(tdb, tmp_path))
    assert {c["symbol"] for c in h.companies()} == {"META", "INTC"}
    h.symbols = {"INTC"}
    assert [c["symbol"] for c in h.companies()] == ["INTC"]
    assert set(STEP_WEIGHT) == set(STEPS) and STEP_WEIGHT["filings"] > sum(v for k, v in STEP_WEIGHT.items() if k != "filings")


# ------------------------------------------------------------------------------------------- amendment 1

def test_a_float_far_above_shares_times_close_and_assets_is_a_scale_error():
    june = date(2022, 6, 30)
    medallion = up.Candidate("M", 1.83e11, june, 2.42e7, 2.6e9)      # 1,301 times shares x $5.80, 70 times assets
    resmed = up.Candidate("R", 3.07e10, june, 1.46e5, 4.7e9)         # the shares frame is wrong; the float is 6.5x assets
    nvidia = up.Candidate("N", 2.7e12, june, 2.46e9, 8.5e10)         # pre-split shares: 9.7 times
    assert up.scale_error(medallion, 5.80) and not up.scale_error(resmed, 212.56) and not up.scale_error(nvidia, 113.06)
    assert not up.scale_error(medallion, None)                        # no close: nothing to check against
    rec = up.Reconstitution(ranked={2023: [up.Candidate("A", 5e11, june, 1e9, 1e11), medallion, up.Candidate("B", 2e10, june, 1e9, 1e10),
                                           up.Candidate("C", 1e10, june, 1e9, 1e10)]})
    members, rejected = up.checked_members(rec, lambda cik, day, y: {"A": 500.0, "M": 5.80, "B": 20.0}.get(cik), n=2)
    assert members == {2023: {"A", "B"}} and rejected == {2023: ["M"]}   # the next-ranked takes the place


def test_a_history_span_ends_after_the_first_close_below_a_dollar():
    span = [(date(2022, 4, 1), date(2024, 4, 1))]
    svb = [(date(2023, 3, 9), 106.04), (date(2023, 3, 28), 0.4), (date(2023, 3, 29), 0.9), (date(2023, 4, 20), 1.2)]
    assert up.listing_cut(span, svb) == [(date(2022, 4, 1), date(2023, 3, 29))]    # the -99.6% day counts, then out
    assert up.listing_cut([(date(2023, 4, 3), date(2024, 4, 1))], svb) == []         # already below $1 before the span
    assert up.listing_cut(span, [(date(2022, 4, 1), 50.0), (date(2023, 1, 3), 45.0)]) == span


def test_the_universe_step_passes_over_a_scale_error_and_the_listing_step_cuts(universe, tdb, tmp_path, monkeypatch):
    from civalpha.platform.sec.client import submissions_url
    from civalpha.platform.storage import DocumentStore
    june = date(2014, 6, 30)
    rec = _rec({2015: {"0001326801", "0000000901", "0000000902"}}, {"0000000901": "Tiny Bank", "0000000902": "Next Co"},
               {"0001326801": (2e11, june, 2.5e9, 2e10), "0000000901": (1.5e11, june, 2e7, 1e9), "0000000902": (1e10, june, 4e8, 5e9)})
    rec.ranked[2015].sort(key=lambda c: -c.float)
    monkeypatch.setattr("civalpha.platform.history_backfill.reconstitute", lambda get: rec)
    sec = _Sec({submissions_url("0000000901"): _subs("901", "Tiny Bank", ["TINY"], "6022"),
                submissions_url("0000000902"): _subs("902", "Next Co", ["NXT"], "7370")})
    tiingo = _Tiingo(meta=[], dates={"tiny": ("Tiny Bank", date(2010, 1, 4), date(2026, 10, 23)),
                                     "nxt": ("Next Co", date(2010, 1, 4), date(2026, 10, 23))},
                     closes={"tiny": [(date(2014, 6, 30), 7.5)], "nxt": [(date(2014, 6, 30), 25.0)]})
    meta = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801'")
    tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'META', '2014-06-30', 67, 't')", c=meta)
    h = HistoryBackfill(tdb, sec=sec, tiingo=tiingo, docs=DocumentStore(tdb, tmp_path))
    lines = []
    out = h.step_universe(lines.append)
    assert out.rejected == {2015: ["0000000901"]} and out.added == 1     # $150B on 20M shares at $7.50: 1,000 times
    assert tdb.scalar("SELECT count(*) FROM cik_mapping WHERE cik = '0000000901'") == 0
    nxt = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0000000902'")
    assert any("scale errors" in x for x in lines)
    for d, c in (("2015-04-01", 20.0), ("2019-03-04", 0.6), ("2019-03-05", 0.5)):
        tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'NXT', :d, :p, 't')", c=nxt, d=d, p=c)
    assert h.step_listing(lambda _l: None) == {"cut": 1, "dropped": 0}
    assert tdb.all("SELECT valid_from, valid_to FROM universe_membership WHERE company_id = :c AND universe = 'history-top350'", c=nxt) == \
        [{"valid_from": date(2015, 4, 1), "valid_to": date(2019, 3, 5)}]
    assert h.step_listing(lambda _l: None) == {"cut": 0, "dropped": 0}  # a rerun changes nothing
