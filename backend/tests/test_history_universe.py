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
    def __init__(self, meta, dates):
        self.meta, self.d = meta, dates

    def all_meta(self):
        return self.meta

    def dates(self, t):
        return self.d.get(t.lower(), (None, None, None))


def _subs(cik, name, tickers, sic):
    return json.dumps({"cik": cik, "name": name, "tickers": tickers, "exchanges": [], "sic": sic, "sicDescription": "",
                       "filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": [],
                                              "acceptanceDateTime": [], "primaryDocument": [], "items": []}, "files": []}}).encode()


def test_the_universe_step_adds_companies_of_the_past_with_their_spans(universe, tdb, tmp_path, monkeypatch):
    from civalpha.platform.sec.client import submissions_url
    from civalpha.platform.storage import DocumentStore
    meta = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801'")
    rec = up.Reconstitution(members={2015: {"0001326801", "0001418091", "0000000777", "0000000888"}, 2016: {"0001326801", "0001418091"},
                                     2017: {"0001326801"}},
                            names={"0001418091": "TWITTER, INC.", "0000000777": "Holding Shell", "0000000888": "Ghost Corp"})
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
    rec = up.Reconstitution(members={2015: {"0000000555"}, 2016: {"0000000555"}, 2017: {"0001326801"}},
                            names={"0000000555": "META PLATFORMS HOLDINGS INC"})
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
