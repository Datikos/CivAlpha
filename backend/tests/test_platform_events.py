"""Policy events: dedup rules, text signals, vocabulary (ported EventRulesTest), ingestion against PostgreSQL,
and the live feeds against a mock HTTP transport."""
from datetime import date, datetime, timezone

import httpx
import pytest

from civalpha.platform.errors import BadRequest
from civalpha.platform.events import EventDraft, EventService, LiveEventSources, Source, Target
from civalpha.platform.events import dedup, signals, vocabulary
from civalpha.platform.events.dedup import Existing, find_duplicate
from civalpha.platform.settings import Events
from civalpha.platform.storage import DocumentStore


# ------------------------------------------------------------------------------------------- EventRulesTest

def draft(cat, typ, title, day, targets, url):
    return EventDraft(cat, typ, title, None, date.fromisoformat(day), datetime.fromisoformat(day + "T20:00:00+00:00"), None, {},
                      targets, Source(url, title, "x", "NEWS_DISCOVERY", None, None, None))


OFFICIAL = Existing(7, "TRADE_TARIFF", "TARIFF_IMPOSED", "New duties on electronics assembled in China and Vietnam", date(2026, 9, 22),
                    frozenset({"COUNTRY:CN", "COUNTRY:VN", "PRODUCT:CONSUMER_ELECTRONICS"}),
                    frozenset({"https://www.federalregister.gov/d/2026-1"}))


def test_news_report_of_same_event_is_merged():
    d = draft("TRADE_TARIFF", "TARIFF_IMPOSED", "Wire report: new duties on electronics assembled in China and Vietnam", "2026-09-22", [],
              "https://news/1")
    assert find_duplicate(d, [OFFICIAL]) == 7


def test_same_url_is_always_the_same_event():
    d = draft("TRADE_TARIFF", "TARIFF_IMPOSED", "Completely different words", "2026-01-01", [], "https://www.federalregister.gov/d/2026-1")
    assert find_duplicate(d, [OFFICIAL]) == 7


def test_different_event_nearby_is_not_merged():
    d = draft("TRADE_TARIFF", "TARIFF_PROPOSED", "Officials weigh duties on Mexican auto parts", "2026-09-25",
              [Target("COUNTRY", "MX", None), Target("PRODUCT", "AUTOS", None)], "https://news/2")
    assert find_duplicate(d, [OFFICIAL]) is None


def test_same_story_outside_date_window_is_not_merged():
    d = draft("TRADE_TARIFF", "TARIFF_IMPOSED", "New duties on electronics assembled in China and Vietnam", "2026-10-10", [], "https://news/3")
    assert find_duplicate(d, [OFFICIAL]) is None


def test_one_rate_decision_per_meeting_date():
    fomc = Existing(3, "MONETARY_POLICY", "RATE_DECISION", "FOMC statement", date(2025, 9, 17), frozenset(), frozenset())
    d = draft("MONETARY_POLICY", "RATE_DECISION", "Fed cuts rates by a quarter point", "2025-09-17", [], "https://news/4")
    assert find_duplicate(d, [fomc]) == 3
    nxt = draft("MONETARY_POLICY", "RATE_DECISION", "Fed cuts rates by a quarter point", "2025-10-29", [], "https://news/5")
    assert find_duplicate(nxt, [fomc]) is None


def test_parses_fomc_statement_rate_decisions():
    r = signals.rate_decision("the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point "
                              "to 4 to 4-1/4 percent.")
    assert r is not None
    assert r.change_bps == -25
    assert r.lower == 4.0
    assert r.upper == 4.25
    r = signals.rate_decision("the Committee decided to raise the target range for the federal funds rate to 5-1/4 to 5-1/2 percent.")
    assert r is not None and r.upper == 5.5
    r = signals.rate_decision("the Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent "
                              "and to 4-1/4 to 4-1/2 percent.")
    assert r is not None and r.change_bps == 0


def test_extracts_trade_targets():
    t = signals.trade_targets("Notice of action: 25% additional duties on semiconductors from China and Taiwan.")
    assert {"CN", "TW", "SEMICONDUCTORS"} <= {x.target_code for x in t}
    assert t[0].magnitude == 25.0
    assert signals.trade_event_type("Notice of product exclusions and suspension of duties") == "TARIFF_REDUCED"


def test_maps_xbrl_geographic_members():
    assert vocabulary.geo_member("country:CN") == vocabulary.GeoMatch("CN", True)
    assert vocabulary.geo_member("aapl:GreaterChinaSegmentMember") == vocabulary.GeoMatch("CN", False)
    assert vocabulary.geo_member("ext:EuropeMember") == vocabulary.GeoMatch("EU", False)
    assert vocabulary.geo_member("ext:RestOfWorldMember") is None
    assert vocabulary.geo_member("us-gaap:AmericasMember") is None


# ------------------------------------------------------------------------------------------- port details

def test_rate_decision_in_basis_points_and_unicode_dashes():
    r = signals.rate_decision("decided to raise the target range for the federal funds rate by 75 basis points\nto 2‑1/4 to 2-1/2 percent")
    assert (r.change_bps, r.lower, r.upper) == (75, 2.25, 2.5)
    assert signals.rate_decision("decided to raise the target range for the federal funds rate to 2−1/4 to 2-1/2 percent") is None
    r = signals.rate_decision("decided to raise the target range for the federal funds rate by 75 basis points to 2—1/4 to 2-1/2 percent")
    assert (r.change_bps, r.lower, r.upper) == (75, 2.25, 2.5)
    r = signals.rate_decision("decided to cut the target range for the federal funds rate by 1/2 percentage point to 4.75 to 5 percent")
    assert (r.change_bps, r.lower, r.upper) == (-50, 4.75, 5.0)


def test_dedup_key_tokens_follow_java_hash_set_order():
    # strings produced by the Java backend for the same titles
    assert " ".join(dedup.tokens_java_order("New duties on electronics assembled in China and Vietnam")) == \
        "china electronic duty vietnam assembled"
    assert " ".join(dedup.tokens_java_order("Fed cuts rates by a quarter point")) == "cut fed rat point quarter"
    assert " ".join(dedup.tokens_java_order(
        "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen")) == \
        "nine fifteen six one seven two three thirteen eight fourteen four twelve eleven ten five"


# ------------------------------------------------------------------------------------------- EventService (database)

@pytest.fixture
def events(tdb, universe, tmp_path):
    return EventService(tdb, DocumentStore(tdb, tmp_path))


def _ts(s):
    return datetime.fromisoformat(s)


def official_draft(url="https://www.federalregister.gov/d/2026-1", published="2026-09-22T13:45:00+00:00", attrs=None):
    title = "New duties on electronics assembled in China and Vietnam"
    return EventDraft("TRADE_TARIFF", "TARIFF_IMPOSED", title, "25% additional duties", date(2026, 9, 22), _ts(published), "USTR",
                      attrs if attrs is not None else {"severity": 0.8, "tariff_rate_pct": 25},
                      [Target("COUNTRY", "CN", 25.0), Target("COUNTRY", "VN", 25.0), Target("PRODUCT", "CONSUMER_ELECTRONICS", 25.0)],
                      Source(url, title, "Federal Register", "OFFICIAL_PRIMARY", _ts(published), b"<html>notice</html>", "text/html"))


def news_draft(url="https://news.example/1", title="Wire report: new duties on electronics assembled in China and Vietnam",
               published="2026-09-22T15:00:00+00:00", targets=None):
    return EventDraft("TRADE_TARIFF", "TARIFF_IMPOSED", title, None, date(2026, 9, 22), _ts(published), None, {"severity": 0.3},
                      targets or [], Source(url, title, "news.example", "NEWS_DISCOVERY", _ts(published), None, None))


def _event(tdb, event_id):
    return tdb.one("SELECT * FROM policy_event WHERE id = :id", id=event_id)


def _sources(tdb, event_id):
    return tdb.all("""SELECT es.role, sd.url, sd.source_type FROM event_source es JOIN source_document sd ON sd.id = es.source_document_id
                      WHERE es.event_id = :e ORDER BY sd.id""", e=event_id)


def test_news_report_merges_into_official_event(tdb, events):
    r = events.ingest(official_draft())
    assert (r.deduplicated, r.upgraded, r.created) == (False, False, True)
    e = _event(tdb, r.event_id)
    assert e["evidence_status"] == "OFFICIAL"
    assert e["version"] == 1
    assert e["attributes"] == {"severity": 0.8, "tariff_rate_pct": 25}
    assert e["dedup_key"] == "TRADE_TARIFF|2026-09-22|china electronic duty vietnam assembled"
    assert e["actor_id"] == events.actor_id("USTR")
    assert _sources(tdb, r.event_id) == [{"role": "OFFICIAL_PRIMARY", "url": "https://www.federalregister.gov/d/2026-1",
                                          "source_type": "OFFICIAL_EVENT"}]
    assert tdb.scalar("SELECT count(*) FROM event_target WHERE event_id = :e", e=r.event_id) == 3
    rec = tdb.all("SELECT record_type, summary, source_document_id FROM actor_record WHERE actor_id = :a", a=e["actor_id"])
    assert [(x["record_type"], x["summary"]) for x in rec] == [("ACTION", "New duties on electronics assembled in China and Vietnam")]

    n = events.ingest(news_draft())
    assert (n.event_id, n.deduplicated, n.upgraded, n.created) == (r.event_id, True, False, False)
    assert tdb.scalar("SELECT count(*) FROM policy_event") == 1
    assert _event(tdb, r.event_id)["version"] == 1
    assert _event(tdb, r.event_id)["evidence_status"] == "OFFICIAL"
    assert [s["role"] for s in _sources(tdb, r.event_id)] == ["OFFICIAL_PRIMARY", "NEWS_DISCOVERY"]
    assert _sources(tdb, r.event_id)[1]["source_type"] == "NEWS"

    again = events.ingest(news_draft())   # same URL again: same event, link not repeated
    assert again.event_id == r.event_id and again.deduplicated
    assert len(_sources(tdb, r.event_id)) == 2


def test_official_source_upgrades_news_only_event(tdb, events):
    n = events.ingest(news_draft(targets=[Target("COUNTRY", "CN", None)]))
    assert (n.deduplicated, n.upgraded, n.created) == (False, False, True)
    e = _event(tdb, n.event_id)
    assert e["evidence_status"] == "NEWS_ONLY"
    assert e["actor_id"] is None
    assert e["published_at"] == _ts("2026-09-22T15:00:00+00:00")
    assert tdb.scalar("SELECT count(*) FROM actor_record") == 0

    o = events.ingest(official_draft(published="2026-09-22T13:45:00+00:00"))
    assert (o.event_id, o.deduplicated, o.upgraded, o.created) == (n.event_id, True, True, False)
    e = _event(tdb, n.event_id)
    assert e["evidence_status"] == "OFFICIAL"
    assert e["version"] == 2
    assert e["published_at"] == _ts("2026-09-22T13:45:00+00:00")
    assert e["attributes"] == {"severity": 0.8, "tariff_rate_pct": 25}   # merged over the news attributes
    assert e["title"] == "Wire report: new duties on electronics assembled in China and Vietnam"
    assert e["actor_id"] == events.actor_id("USTR")
    targets = tdb.all("SELECT target_type, target_code, magnitude FROM event_target WHERE event_id = :e ORDER BY id", e=n.event_id)
    assert [(t["target_type"], t["target_code"]) for t in targets] == [("COUNTRY", "CN"), ("COUNTRY", "VN"), ("PRODUCT", "CONSUMER_ELECTRONICS")]
    assert targets[0]["magnitude"] is None   # existing target kept as it was
    assert [s["role"] for s in _sources(tdb, n.event_id)] == ["NEWS_DISCOVERY", "OFFICIAL_PRIMARY"]
    assert tdb.scalar("SELECT count(*) FROM actor_record WHERE actor_id = :a AND record_type = 'ACTION'", a=e["actor_id"]) == 1

    # a second official document for an event that is already OFFICIAL: linked, no new version
    s = events.ingest(official_draft(url="https://www.federalregister.gov/d/2026-1-correction"))
    assert (s.event_id, s.deduplicated, s.upgraded) == (n.event_id, True, False)
    assert _event(tdb, n.event_id)["version"] == 2


def test_monetary_vote_record_and_actor_profile(tdb, events):
    events.upsert_actor("Federal Open Market Committee", "INSTITUTION", "Federal Reserve Act", None, "Sets the policy rate")
    a = tdb.one("SELECT * FROM policy_actor WHERE name = 'Federal Open Market Committee'")
    assert (a["actor_type"], a["authority"], a["affiliation"], a["profile_note"]) == \
        ("INSTITUTION", "Federal Reserve Act", None, "Sets the policy rate")
    pub = _ts("2025-09-17T18:00:00+00:00")
    d = EventDraft("MONETARY_POLICY", "RATE_DECISION", "FOMC statement: target range lowered", "FOMC statement", date(2025, 9, 17), pub,
                   "Federal Open Market Committee", {"rate_change_bps": -25, "votes_for": 11, "votes_against": 1},
                   [Target("INTEREST_RATE", "US_POLICY_RATE", -25.0)],
                   Source("https://www.federalreserve.gov/x.htm", "FOMC statement", "Fed", "OFFICIAL_PRIMARY", pub, b"x", "text/html"))
    r = events.ingest(d)
    rec = tdb.one("SELECT * FROM actor_record WHERE actor_id = :a", a=a["id"])
    assert rec["record_type"] == "VOTE"
    assert rec["summary"] == "FOMC statement: target range lowered (recorded vote: 11 for, 1 against)"
    assert _event(tdb, r.event_id)["actor_id"] == a["id"]
    # one decision per meeting date, whatever the wording
    m = events.ingest(EventDraft("MONETARY_POLICY", "RATE_DECISION", "Fed trims rates", None, date(2025, 9, 17), pub, None, None, None,
                                 Source("https://news.example/fed", "Fed trims rates", "news", "NEWS_DISCOVERY")))
    assert m.event_id == r.event_id and m.deduplicated


def test_affected_companies_follow_exposures(tdb, events):
    r = events.ingest(official_draft())
    assert events.affected_companies(r.event_id) == []
    ids = tdb.scalars("SELECT id FROM company ORDER BY id LIMIT 2")
    for cid, code in zip(ids, ("CN", "VN")):
        tdb.execute("""INSERT INTO company_exposure (company_id, target_type, target_code, exposure_channel, basis, confidence, method, available_at)
                       VALUES (:c, 'COUNTRY', :code, 'REVENUE', 'ESTIMATED', 'LOW', 'RULE_KEYWORD', now())""", c=cid, code=code)
        tdb.execute("""INSERT INTO company_exposure (company_id, target_type, target_code, exposure_channel, basis, confidence, method, available_at)
                       VALUES (:c, 'COUNTRY', 'JP', 'REVENUE', 'ESTIMATED', 'LOW', 'RULE_KEYWORD', now())""", c=cid)
    assert events.affected_companies(r.event_id) == sorted(ids)


def test_an_event_needs_a_source_url(events):
    d = official_draft()
    with pytest.raises(BadRequest, match="an event needs a source URL"):
        events.ingest(EventDraft(d.category, d.event_type, d.title, d.summary, d.event_date, d.published_at, d.actor_name, d.attributes,
                                 d.targets, Source(None, "t", "p", "OFFICIAL_PRIMARY")))
    with pytest.raises(BadRequest):
        events.ingest(EventDraft(d.category, d.event_type, d.title, d.summary, d.event_date, d.published_at, None, None, None, None))


# ------------------------------------------------------------------------------------------- LiveEventSources

FED_RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>FRB: Press Release - Monetary Policy</title>
<atom:link href="https://www.federalreserve.gov/feeds/press_monetary.xml" rel="self"/>
<item><title><![CDATA[Federal Reserve issues FOMC statement]]></title><link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm</link>
<pubDate>Wed, 17 Sep 2025 18:00:00 GMT</pubDate></item>
<item><title>Minutes of the Federal Open Market Committee</title><link>https://www.federalreserve.gov/m.htm</link>
<pubDate>Wed, 08 Oct 2025 18:00:00 GMT</pubDate></item>
</channel></rss>"""

FOMC_HTML = b"""<html><head><title>Federal Reserve Board - FOMC statement</title><script>var to = "x";</script></head>
<body><div id="article"><p>In support of its goals and in light of the shift in the balance of risks, the Committee decided to lower the
target range for the federal funds rate by 1/4 percentage point to 4&#8209;1/4 to 4-1/2 percent.</p><p>Voting for the monetary policy action</p></div></body></html>"""

NEWS_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Officials announce 25% tariff on Chinese semiconductors</title><link>https://wire.example/a</link>
<description>&lt;p&gt;New duties&amp;nbsp;on chips &lt;b&gt;from&lt;/b&gt; China&lt;/p&gt;</description><pubDate>Mon, 22 Sep 2025 14:00:00 +0000</pubDate></item>
<item><title>Fed signals interest rate path</title><link>https://wire.example/b</link><description>Markets &amp; the FOMC &nbsp; today</description></item>
<item><title>Local sports results</title><link>https://wire.example/c</link><pubDate>Mon, 22 Sep 2025 14:00:00 GMT</pubDate></item>
<item><title>Tariff story without a link</title></item>
</channel></rss>"""

FR_JSON = b"""{"count":2,"results":[
{"title":"Notice of Action: Additional Duties on Semiconductors From the People's Republic of China","type":"Notice",
 "abstract":"USTR imposes 50 percent additional duties under Section 301.","document_number":"2026-01234",
 "html_url":"https://www.federalregister.gov/documents/2026/09/22/2026-01234/notice","publication_date":"2026-09-22",
 "agencies":[{"name":"Trade Representative, Office of United States","id":491}]},
{"title":"Endangered species: listing of a beetle","abstract":null,"document_number":"2026-1","html_url":"https://x","publication_date":"2026-09-22","agencies":[]}
]}"""


def _transport(routes, seen):
    routes = {str(httpx.URL(k)): v for k, v in routes.items()}   # httpx normalizes the host to lower case

    def handler(request: httpx.Request):
        seen.append(request)
        body = routes.get(str(request.url))
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)
    return httpx.MockTransport(handler)


def _live(routes, events, seen, ua=""):
    return LiveEventSources(events, ua, httpx.Client(transport=_transport(routes, seen)))


def test_live_sources_parse_feeds_and_skip_failures():
    from civalpha.platform.events import live

    seen, lines = [], []
    feeds = ["https://Wire.Example/rss.xml", "  ", "https://broken.example/rss"]
    src = _live({live.FEDERAL_REGISTER: FR_JSON, live.FED_RSS: FED_RSS,
                 "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm": FOMC_HTML,
                 "https://Wire.Example/rss.xml": NEWS_RSS},
                Events(True, True, feeds), seen)
    drafts = src.collect(lines.append)
    assert lines == ["Federal Register: 1 candidate events", "Federal Reserve RSS: 1 candidate events",
                     "news https://Wire.Example/rss.xml: 2 candidate events",
                     "news https://broken.example/rss: failed (HTTP 404 for https://broken.example/rss)"]
    assert all(r.headers["User-Agent"] == "CivAlpha research" for r in seen)

    fr, fed, n1, n2 = drafts
    assert (fr.category, fr.event_type, fr.actor_name, fr.event_date) == \
        ("TRADE_TARIFF", "TARIFF_IMPOSED", "Trade Representative, Office of United States", date(2026, 9, 22))
    assert fr.published_at == datetime(2026, 9, 22, 13, 45, tzinfo=timezone.utc)
    assert fr.attributes == {"severity": 0.5, "auto_extracted": True, "document_number": "2026-01234"}
    assert {(t.target_type, t.target_code, t.magnitude) for t in fr.targets} == {("COUNTRY", "CN", 50.0), ("PRODUCT", "SEMICONDUCTORS", 50.0)}
    assert fr.source.role == "OFFICIAL_PRIMARY" and fr.source.content_type == "application/json"
    assert fr.source.content.startswith(b'{"title":"Notice of Action')

    assert fed.title == "FOMC statement: target range lowered"
    assert fed.summary == "Federal Reserve issues FOMC statement"
    assert fed.attributes == {"rate_change_bps": -25, "target_lower_pct": 4.25, "target_upper_pct": 4.5}
    assert fed.targets == [Target("INTEREST_RATE", "US_POLICY_RATE", -25.0)]
    assert fed.event_date == date(2025, 9, 17) and fed.source.content == FOMC_HTML
    assert fed.source.publisher == "Board of Governors of the Federal Reserve System"

    assert (n1.category, n1.event_type, n1.summary) == ("TRADE_TARIFF", "TARIFF_IMPOSED", "New duties on chips from China")
    assert n1.source.publisher == "Wire.Example" and n1.source.role == "NEWS_DISCOVERY" and n1.source.content is None
    assert n1.published_at == datetime(2025, 9, 22, 14, 0, tzinfo=timezone.utc)
    assert {t.target_code for t in n1.targets} == {"CN", "SEMICONDUCTORS"}
    assert (n2.category, n2.event_type, n2.targets, n2.attributes) == ("MONETARY_POLICY", "RATE_DECISION", [], {"severity": 0.3})
    assert n2.summary == "Markets & the FOMC today"
    assert n2.published_at.tzinfo is not None   # no pubDate: now


def test_live_sources_disabled_by_default_and_user_agent():
    seen, lines = [], []
    assert _live({}, Events(False, False, []), seen).collect(lines.append) == []
    assert lines == [] and seen == []
    src = _live({}, Events(False, True, []), seen, ua="Research Bot ops@example.com")
    src.collect(lines.append)
    assert seen[0].headers["User-Agent"] == "Research Bot ops@example.com"
    assert lines == ["Federal Reserve RSS: failed (HTTP 404 for https://www.federalreserve.gov/feeds/press_monetary.xml)"]


def test_rss_pub_date_is_rfc_1123_only():
    from civalpha.platform.events.live import parse_rfc1123

    assert parse_rfc1123("Wed, 17 Sep 2025 18:00:00 GMT") == datetime(2025, 9, 17, 18, tzinfo=timezone.utc)
    assert parse_rfc1123("17 sep 2025 14:00 -0400") == datetime(2025, 9, 17, 18, tzinfo=timezone.utc)
    assert parse_rfc1123("Tue, 31 Sep 2025 18:00:00 GMT").date() == date(2025, 9, 30)
    for bad in ("Thu, 17 Sep 2025 18:00:00 GMT", "Wed, 17 Sep 2025 18:00:00 EST", "2025-09-17T18:00:00Z", "Wed, 17 Sep 2025 18:00:00 Z"):
        with pytest.raises(ValueError):
            parse_rfc1123(bad)
