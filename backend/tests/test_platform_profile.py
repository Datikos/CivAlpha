"""Ticker -> company profile (SIC-based sector suggestion, SEC registrant fields)."""
import json

from civalpha.platform import sectors
from civalpha.platform.sec.parsers import parse_submissions
from civalpha.platform.sec.profile import CompanyProfiler
from civalpha.platform.sec.ticker_lookup import Match


def test_sic_suggestions_match_the_sectors_the_universe_already_uses():
    nvda = sectors.suggest("3674", "Semiconductors & Related Devices")
    assert (nvda.sector, nvda.benchmark_symbol, nvda.industry, nvda.confidence) == ("Technology", "XLK", "SEMICONDUCTORS", "high")
    assert "SIC 3674 Semiconductors" in nvda.note
    assert sectors.suggest(3559).industry == "SEMICONDUCTOR_EQUIPMENT"      # AMAT, LRCX
    assert sectors.suggest(3571).industry == "CONSUMER_ELECTRONICS"         # AAPL
    assert sectors.suggest(5331).sector == "Consumer Staples"               # COST
    assert sectors.suggest(5961).industry == "ECOMMERCE"                    # AMZN
    assert sectors.suggest(3711).benchmark_symbol == "XLY"                  # TSLA
    assert sectors.suggest(2836).sector == "Health Care"                    # AMGN
    assert sectors.suggest(6798).sector == "Real Estate"
    assert sectors.suggest(4911).benchmark_symbol == "XLU"
    # 7370 covers Microsoft and Meta alike: suggested, but flagged for review
    meta = sectors.suggest("7370")
    assert meta.sector == "Technology" and meta.confidence == "review" and "pick" in meta.note
    assert meta.alternatives == ("Technology", "Communication Services")
    assert nvda.alternatives == ()
    # business services NEC (7389) and the generic ranges list the sectors they span, the suggestion first
    assert sectors.suggest(7389).alternatives[:2] == ("Industrials", "Technology")
    assert sectors.suggest(3805).alternatives[0] == "Technology" and len(sectors.suggest(3805).alternatives) == 3
    assert sectors.suggest(None) is None
    assert sectors.suggest("abc") is None
    assert sectors.suggest(9999) is None
    assert set(sectors.SECTOR_ETF.values()) >= {"XLK", "XLV", "XLC", "XLY", "XLP", "XLI"}


SUBMISSIONS = {
    "cik": "1045810", "name": "NVIDIA CORP", "tickers": ["NVDA"], "exchanges": ["Nasdaq"],
    "sic": "3674", "sicDescription": "Semiconductors & Related Devices", "stateOfIncorporation": "DE", "fiscalYearEnd": "0126",
    "formerNames": [{"name": "NVIDIA CORP/CA", "from": "1998-04-23T00:00:00.000Z"}],
    "filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": [], "acceptanceDateTime": [],
                           "primaryDocument": [], "items": []}, "files": []},
}


def test_submissions_carry_the_registrant_profile():
    sub = parse_submissions(json.dumps(SUBMISSIONS).encode())
    assert sub.exchanges == ["Nasdaq"] and sub.sic == "3674" and sub.sic_description.startswith("Semiconductors")
    assert sub.former_names == ["NVIDIA CORP/CA"] and sub.state_of_incorporation == "DE"
    bare = parse_submissions(b'{"cik": "1", "name": "X", "filings": {"recent": {}}}')
    assert bare.exchanges == [] and bare.sic is None and bare.former_names == []


class _Lookup:
    def __init__(self, match):
        self.match = match

    def lookup(self, symbol):
        if isinstance(self.match, Exception):
            raise self.match
        return self.match


class _Sec:
    def __init__(self, body):
        self.body = body

    def get(self, url):
        return self.body

    def mode(self):
        return "fake"


class _Factory:
    def __init__(self, body):
        self.sec = _Sec(body)

    def configured(self):
        return self.sec


def test_profile_is_prefilled_from_edgar(tdb, universe):
    match = Match("NVDA", "0001045810", "Nvidia Corp", "SEC company_tickers.json (fake)")
    p = CompanyProfiler(tdb, _Lookup(match), _Factory(json.dumps(SUBMISSIONS).encode())).profile(" nvda ")
    assert (p.symbol, p.cik, p.name, p.exchange) == ("NVDA", "0001045810", "NVIDIA CORP", "Nasdaq")
    assert (p.sector, p.benchmark_symbol, p.industry, p.sector_confidence) == ("Technology", "XLK", "SEMICONDUCTORS", "high")
    assert p.former_names == ["NVIDIA CORP/CA"]
    assert p.existing is None and p.warnings == [] and p.sector_alternatives == []
    ambiguous = dict(SUBMISSIONS, sic="7389", sicDescription="Services-Business Services, NEC")
    p = CompanyProfiler(tdb, _Lookup(match), _Factory(json.dumps(ambiguous).encode())).profile("NVDA")
    assert p.sector_confidence == "review"
    assert p.sector_alternatives[0] == {"sector": "Industrials", "benchmarkSymbol": "XLI"}
    assert {a["sector"] for a in p.sector_alternatives} >= {"Technology", "Consumer Discretionary"}
    # EDGAR repeats the current name as a "former" name after a re-registration: not a rename
    same = dict(SUBMISSIONS, formerNames=[{"name": "nvidia corp"}, {"name": "NVIDIA CORP/CA"}, {"name": "NVIDIA CORP/CA"}])
    p = CompanyProfiler(tdb, _Lookup(match), _Factory(json.dumps(same).encode())).profile("NVDA")
    assert p.former_names == ["NVIDIA CORP/CA"]


def test_profile_degrades_gracefully(tdb, universe):
    # unknown ticker: nothing from EDGAR, one warning, no crash
    p = CompanyProfiler(tdb, _Lookup(None), _Factory(None)).profile("ZZZZ")
    assert p.cik is None and p.sector is None and "not in SEC company_tickers.json" in p.warnings[0]
    # ticker list down
    p = CompanyProfiler(tdb, _Lookup(RuntimeError("boom")), _Factory(None)).profile("NVDA")
    assert p.cik is None and "unavailable" in p.warnings[0]
    # known ticker but submissions missing: name and CIK still come through
    match = Match("NVDA", "0001045810", "Nvidia Corp", "fake")
    p = CompanyProfiler(tdb, _Lookup(match), _Factory(None)).profile("NVDA")
    assert p.name == "Nvidia Corp" and p.sector is None and "not found" in p.warnings[0]


def test_profile_flags_a_company_that_is_already_tracked(tdb, universe):
    match = Match("INTC", "0000050863", "Intel Corp", "fake")
    p = CompanyProfiler(tdb, _Lookup(match), _Factory(None)).profile("INTC")
    assert p.existing["symbol"] == "INTC" and p.existing["active"] is True
    # a former ticker resolves through the CIK too
    match = Match("FB", "0001326801", "Meta Platforms", "fake")
    p = CompanyProfiler(tdb, _Lookup(match), _Factory(None)).profile("FB")
    assert p.existing["symbol"] == "META"
