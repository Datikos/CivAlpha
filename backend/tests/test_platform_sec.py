"""SEC parsers, clients, filing ingestion and exposure derivation (ported from the Java SecParsersTest and
PlatformIntegrationTest.revisedFilingIsVisibleOnlyAfterItsAcceptance)."""
import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from civalpha.platform.errors import Problem
from civalpha.platform.exposure import ExposureService
from civalpha.platform.llm import LlmProvider
from civalpha.platform.sec import FixtureSecClient, LiveSecClient, RateLimiter, submissions_url
from civalpha.platform.sec.ingestion import FilingIngestionService
from civalpha.platform.sec.parsers import (CONCEPTS, conventional_instance, index_names, parse_company_facts, parse_submissions,
                                             parse_submissions_page, parse_xbrl_instance, pick_instance)
from civalpha.platform.sec.passages import extract
from civalpha.platform.storage import DocumentStore
from civalpha.platform.tickers import TickerResolver


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------------------- parsers
def test_submissions_use_acceptance_time_and_fall_back_conservatively():
    js = """
        {"cik":"0000320193","name":"Apple Inc.","tickers":["AAPL"],
         "filings":{"recent":{
           "accessionNumber":["0000320193-24-000123","0000320193-24-000081"],
           "filingDate":["2024-11-01","2024-08-02"],
           "reportDate":["2024-09-28","2024-06-29"],
           "acceptanceDateTime":["2024-11-01T06:01:36.000Z",""],
           "form":["10-K","10-Q"],
           "primaryDocument":["aapl-20240928.htm","aapl-20240629.htm"],
           "items":["",""]}}}"""
    s = parse_submissions(js.encode())
    assert s.tickers == ["AAPL"]
    assert len(s.filings) == 2
    k = s.filings[0]
    assert k.accepted_at == utc("2024-11-01T06:01:36Z")
    assert k.is_annual
    # missing acceptance time -> end of the filing day in New York (never earlier than the real acceptance)
    assert s.filings[1].accepted_at == utc("2024-08-03T03:59:59Z")


def test_submissions_list_older_pages_and_pages_parse_like_recent():
    root = """
        {"cik":"0000320193","name":"Apple Inc.","tickers":["AAPL"],"filings":{
          "recent":{"accessionNumber":[],"filingDate":[],"reportDate":[],"acceptanceDateTime":[],"form":[],"primaryDocument":[],"items":[]},
          "files":[{"name":"CIK0000320193-submissions-001.json","filingCount":1200,"filingFrom":"1994-01-26","filingTo":"2014-07-21"}]}}"""
    s = parse_submissions(root.encode())
    assert len(s.older_pages) == 1
    assert s.older_pages[0].name == "CIK0000320193-submissions-001.json"
    assert s.older_pages[0].filing_to == date(2014, 7, 21)
    page = """
        {"accessionNumber":["0001193125-14-277160"],"filingDate":["2014-07-23"],"reportDate":["2014-06-28"],
         "acceptanceDateTime":["2014-07-23T16:31:21.000Z"],"form":["10-Q"],"primaryDocument":["d735836d10q.htm"],"items":[""]}"""
    filings = parse_submissions_page(page.encode())
    assert len(filings) == 1 and filings[0].form == "10-Q"


def test_locates_xbrl_instance_from_filing_index():
    index = """
        {"directory":{"name":"/Archives/edgar/data/320193/000032019324000123","item":[
          {"name":"0000320193-24-000123-index.htm"},{"name":"FilingSummary.xml"},{"name":"aapl-20240928.htm"},
          {"name":"aapl-20240928.xsd"},{"name":"aapl-20240928_cal.xml"},{"name":"aapl-20240928_htm.xml"}]}}"""
    names = index_names(index.encode())
    assert pick_instance(names, "aapl-20240928.htm") == "aapl-20240928_htm.xml"
    # pre-inline filings: standalone instance next to linkbases and schema
    old = ["FilingSummary.xml", "aapl-20100925.xml", "aapl-20100925.xsd", "aapl-20100925_lab.xml", "aapl-20100925_pre.xml"]
    assert pick_instance(old, "d10k.htm") == "aapl-20100925.xml"
    assert pick_instance(["FilingSummary.xml", "x.xsd"], "d.htm") is None
    assert conventional_instance("msft-10k_20240630.htm") == "msft-10k_20240630_htm.xml"


def test_company_facts_keeps_accession_filed_date_and_periods():
    js = """
        {"cik":320193,"entityName":"Apple Inc.","facts":{"us-gaap":{
          "Revenues":{"units":{"USD":[
             {"start":"2023-10-01","end":"2023-12-30","val":119575000000,"accn":"0000320193-24-000006","fy":2024,"fp":"Q1","form":"10-Q","filed":"2024-02-02"},
             {"start":"2023-10-01","end":"2023-12-30","val":119000000000,"accn":"0000320193-25-000008","fy":2025,"fp":"Q1","form":"10-Q","filed":"2025-01-31"}]}},
          "Assets":{"units":{"USD":[{"end":"2023-12-30","val":353514000000,"accn":"0000320193-24-000006","form":"10-Q","filed":"2024-02-02"}]}},
          "SomethingElse":{"units":{"USD":[{"end":"2023-12-30","val":1,"accn":"x","form":"10-Q","filed":"2024-02-02"}]}}}}}"""
    rows = parse_company_facts(js.encode(), CONCEPTS)
    assert len(rows) == 3
    # the comparative restatement is kept as its own row
    assert [r.accession_no for r in rows if r.concept == "Revenues"] == ["0000320193-24-000006", "0000320193-25-000008"]
    assets = next(r for r in rows if r.concept == "Assets")
    assert assets.period_start is None
    assert assets.dims_key == ""
    assert assets.value == Decimal("353514000000")


def test_xbrl_instance_extracts_geographic_dimensions():
    xml = """<?xml version="1.0"?>
        <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
          xmlns:us-gaap="http://fasb.org/us-gaap/2024" xmlns:srt="http://fasb.org/srt/2024" xmlns:country="http://xbrl.sec.gov/country/2024"
          xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <xbrli:context id="FY"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity>
            <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="CN"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
            <xbrli:segment><xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:CN</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
            <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="FY" unitRef="usd" decimals="-6">1000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
          <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="CN" unitRef="usd" decimals="-6">250</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
          <us-gaap:Assets contextRef="FY" unitRef="usd">5</us-gaap:Assets>
        </xbrli:xbrl>""".strip()
    rows = parse_xbrl_instance(xml.encode(), {"RevenueFromContractWithCustomerExcludingAssessedTax"}, "acc-1", "10-K", date(2025, 2, 1))
    assert len(rows) == 2
    cn = next(r for r in rows if r.dimensions)
    assert cn.dimensions["srt:StatementGeographicalAxis"] == "country:CN"
    assert cn.dims_key == "srt:StatementGeographicalAxis=country:CN"
    assert cn.unit == "USD"
    assert cn.taxonomy == "us-gaap"


def test_xbrl_parser_rejects_external_entities():
    xxe = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    with pytest.raises(ValueError):
        parse_xbrl_instance(xxe.encode(), None, "a", "10-K", date.today())


def test_passages_keep_section_topic_and_offsets():
    html = """
        <html><body><h2>Item 1A. Risk Factors</h2>
        <p>We rely on manufacturing partners in China for substantially all of our products; tariffs or export controls affecting China could increase our costs.</p>
        <p>Short.</p>
        <h2>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</h2>
        <p>Interest rate risk: a 100 basis point increase in rates would change interest expense on our floating-rate debt.</p></body></html>"""
    ps = extract(html)
    assert len(ps) == 2
    assert ps[0].topic == "TRADE"
    assert ps[0].section == "Item 1A. Risk Factors"
    assert ps[1].topic == "RATES"
    assert ps[0].char_end > ps[0].char_start


def test_live_client_requires_declared_user_agent_and_sane_rate():
    with pytest.raises(Problem):
        LiveSecClient("", 5)
    with pytest.raises(Problem):
        LiveSecClient("Mozilla/5.0", 5)
    with pytest.raises(ValueError):
        LiveSecClient("CivAlpha research jane@example.com", 20)
    LiveSecClient("CivAlpha research jane@example.com", 5)


def test_rate_limiter_spaces_requests():
    limiter = RateLimiter(10)
    t0 = time.monotonic()
    for _ in range(6):
        limiter.acquire()
    assert (time.monotonic() - t0) * 1000 >= 450


def test_fixture_client_maps_sec_urls_and_blocks_traversal(tmp_path):
    (tmp_path / "submissions").mkdir()
    (tmp_path / "submissions" / "CIK0000000001.json").write_text("{}")
    c = FixtureSecClient(tmp_path)
    assert c.get(submissions_url("0000000001")) is not None
    assert c.get("https://www.sec.gov/Archives/../../etc/passwd") is None
    assert c.get("https://example.com/submissions/CIK0000000001.json") is None


# ---------------------------------------------------------------------------------------------------- ingestion (database)
def _service(tdb, universe, tmp_path) -> FilingIngestionService:
    return FilingIngestionService(tdb, DocumentStore(tdb, tmp_path / "docs"), TickerResolver(tdb), universe,
                                  ExposureService(tdb, LlmProvider()), lookback_years=6)


def _pit_revenue(tdb, company_id: int, as_of: datetime) -> list[dict]:
    """The point-in-time view behind GET /api/companies/{symbol}/financials?asOf=... (latest known value per period, plus
    the value first reported)."""
    return tdb.all("""
        WITH known AS (
            SELECT * FROM xbrl_fact WHERE company_id = :c AND accepted_at <= :asof AND dims_key = '' AND taxonomy = 'us-gaap'
        ), ranked AS (
            SELECT k.*,
                   row_number() OVER (PARTITION BY concept, unit, period_start, period_end ORDER BY accepted_at DESC, id DESC) AS rn,
                   first_value(value) OVER (PARTITION BY concept, unit, period_start, period_end ORDER BY accepted_at, id) AS original_value
            FROM known k
        )
        SELECT * FROM ranked WHERE rn = 1 AND concept = 'Revenues' ORDER BY period_end, concept""", c=company_id, asof=as_of)


def test_revised_filing_is_visible_only_after_its_acceptance(tdb, universe, tmp_path):
    sec = tmp_path / "sec"
    (sec / "submissions").mkdir(parents=True)
    (sec / "companyfacts").mkdir(parents=True)
    # the amendment sits on an older paged index file, which ingestion must follow
    (sec / "submissions" / "CIK0000050863.json").write_text("""
        {"cik":"0000050863","name":"Intel","tickers":["INTC"],"filings":{"recent":{
          "accessionNumber":["0000050863-25-000010"], "filingDate":["2025-01-30"], "reportDate":["2024-12-28"],
          "acceptanceDateTime":["2025-01-30T21:05:00.000Z"], "form":["10-K"], "primaryDocument":[""], "items":[""]},
          "files":[{"name":"CIK0000050863-submissions-001.json","filingCount":1,"filingFrom":"2025-03-14","filingTo":"2025-03-14"},
                   {"name":"CIK0000050863-submissions-000.json","filingCount":1,"filingFrom":"1994-01-01","filingTo":"2001-01-01"}]}}""")
    (sec / "submissions" / "CIK0000050863-submissions-001.json").write_text("""
        {"accessionNumber":["0000050863-25-000099"], "filingDate":["2025-03-14"], "reportDate":["2024-12-28"],
         "acceptanceDateTime":["2025-03-14T20:00:00.000Z"], "form":["10-K/A"], "primaryDocument":[""], "items":[""]}""")
    (sec / "companyfacts" / "CIK0000050863.json").write_text("""
        {"cik":50863,"entityName":"Intel","facts":{"us-gaap":{"Revenues":{"units":{"USD":[
          {"start":"2023-12-31","end":"2024-12-28","val":53100000000,"accn":"0000050863-25-000010","fy":2024,"fp":"FY","form":"10-K","filed":"2025-01-30"},
          {"start":"2023-12-31","end":"2024-12-28","val":51500000000,"accn":"0000050863-25-000099","fy":2024,"fp":"FY","form":"10-K/A","filed":"2025-03-14"}]}}}}}""")
    intc = TickerResolver(tdb).company_by_cik("0000050863")
    service = _service(tdb, universe, tmp_path)
    log: list[str] = []
    res = service.ingest(FixtureSecClient(sec), intc, False, log.append)
    assert res.facts == 2
    assert res.filings == 2
    # the page outside the lookback window is not fetched (it does not exist; a fetch would be logged as missing)
    assert not any("submissions-000" in line for line in log)
    assert log[-1] == "INTC: 2 new filings, 2 facts, 0 passages, 0 exposures (fixture mode)"
    assert tdb.scalars("SELECT amends_accession FROM filing WHERE form_type = '10-K/A'") == ["0000050863-25-000010"]

    assert _pit_revenue(tdb, intc, utc("2025-01-30T21:00:00Z")) == []

    mid = _pit_revenue(tdb, intc, utc("2025-02-15T00:00:00Z"))[0]
    assert float(mid["value"]) == 5.31e10
    assert mid["value"] == mid["original_value"]          # not revised

    after = _pit_revenue(tdb, intc, utc("2025-03-15T00:00:00Z"))[0]
    assert float(after["value"]) == 5.15e10
    assert after["value"] != after["original_value"]      # revised
    assert float(after["original_value"]) == 5.31e10
    assert after["accession_no"] == "0000050863-25-000099"

    # re-ingesting is idempotent
    assert service.ingest(FixtureSecClient(sec), intc, False, log.append).facts == 0


def test_annual_report_yields_passages_dimensional_facts_and_exposures(tdb, universe, tmp_path):
    """A 10-K with a primary document and an inline-XBRL instance (laid out like the demo fixtures)."""
    sec = tmp_path / "sec"
    acc, doc = "0000050863-25-000010", "intc-20241228.htm"
    archive = sec / "Archives" / "edgar" / "data" / "50863" / acc.replace("-", "")
    archive.mkdir(parents=True)
    (sec / "submissions").mkdir()
    (sec / "submissions" / "CIK0000050863.json").write_text(json.dumps({
        "cik": "0000050863", "name": "Intel", "tickers": ["INTC"], "filings": {"recent": {
            "accessionNumber": [acc], "filingDate": ["2025-01-30"], "reportDate": ["2024-12-28"],
            "acceptanceDateTime": ["2025-01-30T21:05:00.000Z"], "form": ["10-K"], "primaryDocument": [doc], "items": [""]}}}))
    (archive / doc).write_text(
        "<html><body><h2>Item 1A. Risk Factors</h2><p>We rely on manufacturing partners and suppliers in Taiwan for substantially "
        "all of our products; tariffs, export controls or other trade restrictions affecting Taiwan could increase our costs.</p>"
        "<h2>Item 7A. Market Risk</h2><p>Interest rate risk: a 100 basis point increase in rates would change interest expense "
        "on floating-rate obligations.</p></body></html>")
    (archive / "intc-20241228_htm.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:us-gaap="http://fasb.org/us-gaap/2024" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">'
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        '<xbrli:context id="FY"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">50863</xbrli:identifier></xbrli:entity>'
        '<xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-28</xbrli:endDate></xbrli:period></xbrli:context>'
        '<xbrli:context id="CN"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">50863</xbrli:identifier><xbrli:segment>'
        '<xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:CN</xbrldi:explicitMember></xbrli:segment></xbrli:entity>'
        '<xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-28</xbrli:endDate></xbrli:period></xbrli:context>'
        '<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="FY" unitRef="usd">3000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>'
        '<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="CN" unitRef="usd">1000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>'
        '</xbrli:xbrl>')
    intc = TickerResolver(tdb).company_by_cik("0000050863")
    service = _service(tdb, universe, tmp_path)
    log: list[str] = []
    res = service.ingest(FixtureSecClient(sec), intc, False, log.append)
    assert (res.filings, res.facts, res.passages) == (1, 2, 2)
    exposures = {(e["target_type"], e["target_code"], e["exposure_channel"]): e for e in tdb.all(
        "SELECT * FROM company_exposure WHERE company_id = :c", c=intc)}
    cn = exposures[("COUNTRY", "CN", "REVENUE")]
    assert (cn["basis"], cn["confidence"], cn["method"], cn["share"]) == ("DIRECTLY_REPORTED", "HIGH", "XBRL_DIMENSION", Decimal("0.3333"))
    assert cn["available_at"] == utc("2025-01-30T21:05:00Z")
    tw = exposures[("COUNTRY", "TW", "SUPPLY_CHAIN")]
    assert (tw["basis"], tw["confidence"], tw["share"]) == ("ESTIMATED", "MEDIUM", Decimal("0.60"))
    assert exposures[("PRODUCT", "SEMICONDUCTORS", "REVENUE")]["method"] == "SECTOR_MAP"   # industry SEMICONDUCTORS
    assert res.exposures == len(exposures)
    # a second run neither re-fetches the document nor derives again
    again = service.ingest(FixtureSecClient(sec), intc, False, log.append)
    assert (again.filings, again.facts, again.passages, again.exposures) == (0, 0, 0, 0)
