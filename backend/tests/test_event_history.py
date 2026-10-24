"""ADR-0009: official event streams, the Federal Register queries shared by the live run and the history, FOMC rate
changes from FRED's upper target, and the EVENT_BACKFILL job (mock HTTP; ingestion against PostgreSQL)."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

from civalpha.features import event_features
from civalpha.platform.events import EventDraft, EventService, LiveEventSources, Source, Target, live, streams
from civalpha.platform.events.backfill import EventBackfill, fomc_changes
from civalpha.platform.settings import Events
from civalpha.platform.storage import DocumentStore
from helpers import make_bundle


def agency(slug, name):
    return {"name": name, "slug": slug}


PROCLAMATION = {"title": "Adjusting Imports of Steel Into the United States", "type": "Presidential Document", "abstract": None,
                "document_number": "2019-10872", "html_url": "https://www.federalregister.gov/d/2019-10872", "publication_date": "2019-05-21",
                "agencies": [agency("executive-office-of-the-president", "Executive Office of the President")],
                "raw_text_url": "https://www.federalregister.gov/full_text/2019-10872.txt"}
RECIPROCAL = {"title": "Regulating Imports With a Reciprocal Tariff", "type": "Presidential Document", "abstract": None,
              "document_number": "2025-06063", "html_url": "https://www.federalregister.gov/d/2025-06063", "publication_date": "2025-04-07",
              "agencies": [agency("executive-office-of-the-president", "Executive Office of the President")],
              "raw_text_url": "https://www.federalregister.gov/full_text/2025-06063.txt"}
NOT_TRADE = {"title": "National Day of Remembrance", "type": "Presidential Document", "abstract": None, "document_number": "2019-1",
             "html_url": "https://www.federalregister.gov/d/2019-1", "publication_date": "2019-05-21", "agencies": []}
USTR_301 = {"title": "Notice of Modification of Section 301 Action: China's Acts, Policies, and Practices", "type": "Notice",
            "abstract": "The U.S. Trade Representative has determined to increase the additional duty to 25 percent.",
            "document_number": "2019-09681", "html_url": "https://www.federalregister.gov/d/2019-09681", "publication_date": "2019-05-09",
            "agencies": [agency(streams.USTR, "Trade Representative, Office of United States")]}
USTR_GSP = {"title": "Generalized System of Preferences: Product Petitions", "type": "Notice", "abstract": "Petitions to modify the list.",
            "document_number": "2019-2", "html_url": "https://www.federalregister.gov/d/2019-2", "publication_date": "2019-05-10",
            "agencies": [agency(streams.USTR, "Trade Representative, Office of United States")]}
ENTITY_LIST = {"title": "Addition of Entities to the Entity List", "type": "Rule",
               "abstract": "This rule amends the Export Administration Regulations by adding entities in China.",
               "document_number": "2019-10616", "html_url": "https://www.federalregister.gov/d/2019-10616", "publication_date": "2019-05-21",
               "agencies": [agency("commerce-department", "Commerce Department"), agency(streams.BIS, "Industry and Security Bureau")]}
CASE = {"title": "Xanthan Gum From the People's Republic of China: Preliminary Results of Antidumping Duty Administrative Review",
        "type": "Notice", "abstract": None, "document_number": "2026-3", "html_url": "https://www.federalregister.gov/d/2026-3",
        "publication_date": "2026-10-14", "agencies": [agency(streams.ITA, "International Trade Administration")]}
FERC = {"title": "Combined Notice of Filings", "type": "Notice", "abstract": "Tariff amendment filed.", "document_number": "2026-4",
        "html_url": "https://www.federalregister.gov/d/2026-4", "publication_date": "2026-10-14",
        "agencies": [agency("federal-energy-regulatory-commission", "Federal Energy Regulatory Commission")]}


def page(results, total_pages=1):
    return json.dumps({"count": len(results), "total_pages": total_pages, "results": results}).encode()


def client(routes, seen):
    routes = {str(httpx.URL(k)): v for k, v in routes.items()}

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        body = routes.get(str(request.url))
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def sources(routes, seen):
    return LiveEventSources(Events(True, True, []), "CivAlpha research", client(routes, seen))


def fr_routes(start, end, by_query: dict[int, list[bytes]]) -> dict:
    out = {}
    for i, (_n, c) in enumerate(live.FR_QUERIES):
        bodies = by_query.get(i, [page([])])
        for n, body in enumerate(bodies, 1):
            out[live.fr_url(c, start, end, n)] = body
    return out


# ------------------------------------------------------------------------------------------- streams

def test_streams_follow_the_statutory_instrument():
    assert streams.classify(PROCLAMATION) == "PRESIDENTIAL"
    assert streams.classify(USTR_301) == "USTR_301"
    assert streams.classify(USTR_GSP) == "OTHER_TRADE"
    assert streams.classify({**USTR_GSP, "title": "Section 232 inclusions process"}) == "SECTION_232"
    assert streams.classify(ENTITY_LIST) == "EXPORT_CONTROL"
    assert streams.classify({**ENTITY_LIST, "type": "Notice"}) == "OTHER_TRADE"      # a notice about the list is not a rule
    assert streams.classify(CASE) == "AD_CVD"
    assert streams.classify(FERC) == "OTHER_TRADE"
    # older stored records name the agency without a slug
    assert streams.classify({**USTR_301, "agencies": [{"name": "Trade Representative, Office of United States"}]}) == "USTR_301"
    assert [streams.severity(s) for s in ("PRESIDENTIAL", "SECTION_232", "USTR_301", "EXPORT_CONTROL", "AD_CVD")] == [1.0, 0.8, 0.8, 0.6, 0.5]
    assert streams.modelled("FOMC") and streams.modelled("MANUAL") and not streams.modelled("AD_CVD") and not streams.modelled(None)


# ------------------------------------------------------------------------------------------- Federal Register

def test_federal_register_keeps_material_documents_once_across_queries_and_pages():
    start, end = date(2019, 1, 1), date(2019, 12, 31)
    full_text = b"<pre>... imports of all articles from Canada and Mexico ... and from the People's Republic of China ...</pre>"
    routes = fr_routes(start, end, {0: [page([PROCLAMATION, NOT_TRADE], total_pages=2), page([RECIPROCAL], total_pages=2)],
                                    1: [page([USTR_301, USTR_GSP])],
                                    2: [page([PROCLAMATION])],                   # also found by the "section 232" query
                                    3: [page([ENTITY_LIST, CASE, FERC])]})
    routes[RECIPROCAL["raw_text_url"]] = full_text
    seen = []
    drafts = sources(routes, seen).federal_register(start, end)
    by = {d.source.url: d for d in drafts}
    assert sorted(d.stream for d in drafts) == ["EXPORT_CONTROL", "PRESIDENTIAL", "PRESIDENTIAL", "USTR_301"]
    steel = by[PROCLAMATION["html_url"]]
    assert steel.attributes["severity"] == 1.0 and {t.target_code for t in steel.targets} == {"STEEL"}
    assert "targets_from" not in steel.attributes and PROCLAMATION["raw_text_url"] not in seen      # its title had a target
    reciprocal = by[RECIPROCAL["html_url"]]
    assert reciprocal.attributes["targets_from"] == "full_text" and {t.target_code for t in reciprocal.targets} == {"CA", "MX", "CN"}
    assert by[USTR_301["html_url"]].attributes["severity"] == 0.8
    assert by[ENTITY_LIST["html_url"]].event_type == "EXPORT_CONTROL" and by[ENTITY_LIST["html_url"]].attributes["severity"] == 0.6
    assert steel.published_at == datetime(2019, 5, 21, 13, 45, tzinfo=timezone.utc)
    assert len([u for u in seen if "documents.json" in u]) == 5        # 2 pages + 3 queries


def test_the_live_run_asks_for_the_last_two_weeks():
    end = date.today()
    seen = []
    routes = fr_routes(end - timedelta(days=live.LIVE_DAYS), end, {1: [page([USTR_301])]})
    assert [d.stream for d in sources(routes, seen).federal_register()] == ["USTR_301"]


# ------------------------------------------------------------------------------------------- FOMC history

STATEMENT_0731 = b"<p>the Committee decided to lower the target range for the federal funds rate to 2 to 2-1/4 percent.</p>"
STATEMENT_0303 = (b"<p>the Committee decided to lower the target range for the federal funds rate by 1/2 percentage point, to 1 to "
                  b"1-1/4 percent.</p>")


def test_fomc_changes_from_the_upper_target():
    obs = [(date(2019, 7, 31), 2.5), (date(2019, 8, 1), 2.25), (date(2019, 8, 2), 2.25), (date(2019, 8, 3), None),
           (date(2020, 3, 4), 1.25), (date(2018, 12, 20), 2.5)]
    assert fomc_changes(obs, date(2019, 1, 1), date(2026, 1, 1)) == [(date(2019, 8, 1), 2.25, 2.5), (date(2020, 3, 4), 1.25, 2.25)]
    assert fomc_changes(obs, date(2020, 1, 1), date(2026, 1, 1)) == [(date(2020, 3, 4), 1.25, 2.25)]


def test_fomc_history_reads_the_statement_of_the_decision_day():
    url = live.FOMC_STATEMENT.format
    routes = {url(day=date(2019, 7, 31)): STATEMENT_0731, url(day=date(2020, 3, 3)): STATEMENT_0303,
              url(day=date(2022, 3, 16)): STATEMENT_0303}          # says 1-1/4 where FRED says 0.5: skipped
    lines = []
    drafts = sources(routes, []).fomc_history([(date(2019, 8, 1), 2.25, 2.5), (date(2020, 3, 4), 1.25, 2.25),
                                               (date(2022, 3, 17), 0.5, 0.25), (date(2023, 1, 9), 4.5, 4.25)], lines.append)
    a, b = drafts
    assert (a.event_date, a.attributes["rate_change_bps"], a.title, a.stream) == (date(2019, 7, 31), -25, "FOMC statement: target range lowered", "FOMC")
    assert a.published_at == datetime(2019, 7, 31, 18, 0, tzinfo=timezone.utc)                 # 14:00 New York (EDT)
    assert a.targets == [Target("INTEREST_RATE", "US_POLICY_RATE", -25.0)]
    assert (b.event_date, b.attributes["rate_change_bps"]) == (date(2020, 3, 3), -100)          # from FRED, not the "by 1/2"
    assert lines == ["FOMC history: statement of 2022-03-16 says 1.25, FRED 0.5; skipped",
                     "FOMC history: no statement found for the change effective 2023-01-09"]


# ------------------------------------------------------------------------------------------- ingestion

@pytest.fixture
def events(tdb, universe, tmp_path):
    return EventService(tdb, DocumentStore(tdb, tmp_path))


def _draft(category="TRADE_TARIFF", title="Adjusting Imports of Steel", day=date(2019, 5, 21), role="OFFICIAL_PRIMARY", url="https://x/1",
           attrs=None, stream=None, content=None):
    published = datetime(day.year, day.month, day.day, 13, 45, tzinfo=timezone.utc)
    return EventDraft(category, "TARIFF_IMPOSED" if category == "TRADE_TARIFF" else "RATE_DECISION", title, None, day, published,
                      "Executive Office of the President", attrs or {"severity": 1.0}, [Target("PRODUCT", "STEEL", None)],
                      Source(url, title, "Federal Register", role, published, content, None if content is None else "application/json"), stream)


def _stream(tdb, event_id):
    return tdb.scalar("SELECT stream FROM policy_event WHERE id = :i", i=event_id)


def test_streams_are_stored_and_defaulted(tdb, events):
    assert _stream(tdb, events.ingest(_draft(stream="PRESIDENTIAL")).event_id) == "PRESIDENTIAL"
    assert _stream(tdb, events.ingest(_draft(title="Owner's notice", url="https://x/2", day=date(2019, 6, 1))).event_id) == "MANUAL"
    news = events.ingest(_draft(title="Wire: steel tariffs", url="https://news/3", day=date(2019, 7, 1), role="NEWS_DISCOVERY"))
    assert _stream(tdb, news.event_id) is None
    up = events.ingest(_draft(title="Wire: steel tariffs", url="https://x/4", day=date(2019, 7, 1), stream="SECTION_232"))
    assert up.upgraded and up.event_id == news.event_id and _stream(tdb, news.event_id) == "SECTION_232"
    rate = events.ingest(_draft("MONETARY_POLICY", "FOMC statement", date(2019, 9, 18), url="https://fed/1", attrs={}))
    assert _stream(tdb, rate.event_id) == "FOMC"


def test_a_rate_change_without_its_size_gets_it_from_the_previous_decision(tdb, events):
    events.ingest(_draft("MONETARY_POLICY", "FOMC statement", date(2019, 7, 31), url="https://fed/a",
                         attrs={"rate_change_bps": -25, "target_lower_pct": 2.0, "target_upper_pct": 2.25}))
    r = events.ingest(_draft("MONETARY_POLICY", "FOMC statement", date(2019, 9, 18), url="https://fed/b",
                             attrs={"target_lower_pct": 1.75, "target_upper_pct": 2.0}))
    assert tdb.scalar("SELECT attributes->>'rate_change_bps' FROM policy_event WHERE id = :i", i=r.event_id) == "-25"


def test_stored_events_are_classified_from_their_federal_register_record(tdb, events):
    case = events.ingest(_draft(title=CASE["title"], url=CASE["html_url"], day=date(2026, 10, 14), attrs={"severity": 0.5},
                                content=json.dumps(CASE).encode()))
    ustr = events.ingest(_draft(title=USTR_301["title"], url=USTR_301["html_url"], day=date(2019, 5, 9), attrs={"severity": 0.5},
                                content=json.dumps(USTR_301).encode()))
    manual = events.ingest(_draft(title="Owner's notice", url="https://owner/1", day=date(2020, 1, 6)))
    tdb.execute("UPDATE policy_event SET stream = NULL WHERE id IN (:a, :b, :c)", a=case.event_id, b=ustr.event_id, c=manual.event_id)
    lines = []
    assert events.classify_unstreamed(lines.append) == {"AD_CVD": 1, "USTR_301": 1, "MANUAL": 1}
    assert lines == ["event streams: AD_CVD 1, MANUAL 1, USTR_301 1 classified"]
    sev = lambda i: tdb.scalar("SELECT (attributes->>'severity')::float8 FROM policy_event WHERE id = :i", i=i)  # noqa: E731
    assert (sev(case.event_id), sev(ustr.event_id)) == (0.5, 0.8)            # severity follows a material stream only
    assert events.classify_unstreamed() == {}


def test_a_case_notice_changes_no_feature():
    """The same official tariff event moves trade_shock as a presidential action and not as an anti-dumping case notice."""
    exposures = pd.DataFrame({"id": [1], "company_id": [1], "target_type": "COUNTRY", "target_code": "CN", "exposure_channel": "REVENUE",
                              "share": [0.3], "basis": "DIRECTLY_REPORTED", "confidence": "HIGH", "method": "XBRL",
                              "available_at": pd.to_datetime(["2020-01-02T00:00Z"], utc=True), "passage_id": [None], "filing_id": [None]})
    targets = pd.DataFrame({"event_id": [1], "target_type": "COUNTRY", "target_code": "CN", "magnitude": [25.0]})

    def shock(stream):
        ev = pd.DataFrame({"id": [1], "category": "TRADE_TARIFF", "event_type": "TARIFF_IMPOSED", "title": "x",
                           "published_at": pd.to_datetime(["2020-06-01T13:45Z"], utc=True), "evidence_status": "OFFICIAL",
                           "stream": [stream], "attributes": [{"severity": 1.0}]})
        b = make_bundle(events=ev, targets=targets, exposures=exposures)
        return event_features(b, 1, pd.Timestamp("2020-06-02T21:00Z"), 0.0)[0]["trade_shock"]

    assert shock("PRESIDENTIAL") < 0 and shock("AD_CVD") == 0.0


# ------------------------------------------------------------------------------------------- the job

class FakeMacro:
    def __init__(self, obs):
        self.obs, self.fred, self.asked = obs, type("F", (), {"enabled": True})(), []

    def current_values(self, series_id, start):
        self.asked.append((series_id, start))
        return self.obs


def test_the_upper_target_is_read_without_vintages(tdb, tmp_path):
    """DFEDTARU has more vintage dates than ALFRED allows (3,819 on 2026-10-24, limit 2,000): current values only."""
    from civalpha.platform.macro import MacroService
    from civalpha.platform.settings import Fred

    body = b'{"observations":[{"date":"2019-07-31","value":"2.50"},{"date":"2019-08-01","value":"2.25"},{"date":"2019-08-03","value":"."}]}'
    seen = []
    http = client({"https://api.stlouisfed.org/fred/series/observations?file_type=json&series_id=DFEDTARU"
                   "&observation_start=2018-01-01&api_key=KEY": body}, seen)
    m = MacroService(tdb, DocumentStore(tdb, tmp_path), Fred("KEY", "https://api.stlouisfed.org/fred", ["FEDFUNDS"]), http)
    assert m.current_values("DFEDTARU", date(2018, 1, 1)) == [(date(2019, 7, 31), 2.5), (date(2019, 8, 1), 2.25), (date(2019, 8, 3), None)]
    assert "realtime_start" not in seen[0]
    url = tdb.scalar("SELECT url FROM source_document WHERE title = 'DFEDTARU current values'")
    assert url is not None and "KEY" not in url                                 # the stored locator omits the key


def test_backfill_loads_both_sources_and_a_rerun_stores_nothing_twice(tdb, universe, tmp_path):
    start, end = date(2019, 1, 1), date(2019, 12, 31)
    routes = fr_routes(start, end, {0: [page([PROCLAMATION])], 1: [page([USTR_301])]})
    routes[live.FOMC_STATEMENT.format(day=date(2019, 7, 31))] = STATEMENT_0731
    obs = [(date(2019, 7, 30), 2.5), (date(2019, 7, 31), 2.5), (date(2019, 8, 1), 2.25)]
    macro = FakeMacro(obs)
    job = EventBackfill(tdb, sources(routes, []), macro)
    job.events = EventService(tdb, DocumentStore(tdb, tmp_path))
    lines = []
    first = job.run(lines.append, start, end)
    assert first.created == {"FOMC": 1, "PRESIDENTIAL": 1, "USTR_301": 1} and first.failed == 0
    assert macro.asked == [("DFEDTARU", date(2018, 1, 1))]                     # a year early: the range the first change left
    assert "event history: new events by stream: FOMC 1, PRESIDENTIAL 1, USTR_301 1; 0 already on record, 0 failed" in lines
    again = job.run(lambda _l: None, start, end)
    assert again.created == {} and again.existing == 3
    assert tdb.scalar("SELECT count(*) FROM policy_event WHERE stream IS NOT NULL") == 3


def test_backfill_endpoint_queues_the_job(api):
    assert api.post("/api/admin/events/backfill", json={"from": "2026-01-02", "to": "2026-01-01"}).status_code == 400
    r = api.post("/api/admin/events/backfill", json={"from": "2019-01-01"})
    assert r.status_code == 200 and r.json()["jobType"] == "EVENT_BACKFILL" and r.json()["params"] == {"from": "2019-01-01", "to": None}
    from civalpha.platform.sql import db
    db().execute("DELETE FROM pipeline_job WHERE id = :i", i=r.json()["id"])      # the worker is not running in tests
