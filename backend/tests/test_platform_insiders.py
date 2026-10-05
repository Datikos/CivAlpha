"""Insider transactions: the SEC data sets and Form 4 XML parse, storage is idempotent, the features and setups use only
trades known at the close, and silence counts as zero."""
import io
import zipfile
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from civalpha import pit
from civalpha.features import DataBundle, insider_feature
from civalpha.platform.insiders import (InsiderIngestionService, Transaction, parse_dataset, parse_form4_xml, quarters_back,
                                         xml_document_name)
from civalpha.strategies import setups
from civalpha.strategies.panel import MarketPanel
from helpers import make_bundle


def _zip(subs, owners, trans) -> bytes:
    def tsv(head, rows):
        return "\t".join(head) + "\n" + "".join("\t".join(str(x) for x in r) + "\n" for r in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL"], subs))
        z.writestr("REPORTINGOWNER.tsv", tsv(["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"], owners))
        z.writestr("NONDERIV_TRANS.tsv", tsv(["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                              "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP"], trans))
    return buf.getvalue()


SUBS = [("0001-25-1", "30-MAY-2025", "28-MAY-2025", "4", "0001326801", "Meta Platforms, Inc.", "META"),
        ("0001-25-2", "02-JUN-2025", "30-MAY-2025", "4/A", "0001326801", "Meta Platforms, Inc.", "META"),
        ("0001-25-3", "02-JUN-2025", "30-MAY-2025", "3", "0001326801", "Meta Platforms, Inc.", "META"),
        ("0001-25-9", "02-JUN-2025", "30-MAY-2025", "4", "0000999999", "Someone Else", "ELSE")]
OWNERS = [("0001-25-1", "0000000111", "Doe Jane", "Officer", "Chief Financial Officer"),
          ("0001-25-2", "0000000222", "Roe Rick", "Director", ""),
          ("0001-25-9", "0000000333", "Other Person", "TenPercentOwner", "")]
TRANS = [("0001-25-1", "71", "Class A Common Stock", "28-MAY-2025", "P", "1000.0", "500.5", "A", "5000.0", "D"),
         ("0001-25-1", "72", "Class A Common Stock", "28-MAY-2025", "F", "100.0", "500.5", "D", "4900.0", "D"),
         ("0001-25-2", "73", "Class A Common Stock", "30-MAY-2025", "S", "200.0", "510", "D", "800.0", "I"),
         ("0001-25-2", "74", "Class A Common Stock", "", "S", "200.0", "510", "D", "800.0", "I"),
         ("0001-25-9", "75", "Common", "30-MAY-2025", "P", "10.0", "1", "A", "10.0", "D")]


def test_data_set_parses_forms_4_for_the_universe_only():
    rows = parse_dataset(_zip(SUBS, OWNERS, TRANS), {"0001326801"})
    assert [(r.accession_no, r.trans_sk, r.trans_code) for r in rows] == [("0001-25-1", "71", "P"), ("0001-25-1", "72", "F"), ("0001-25-2", "73", "S")]
    p = rows[0]
    assert (p.owner_name, p.relationship, p.title, p.trans_date, p.filed_date) == ("Doe Jane", "Officer", "Chief Financial Officer", date(2025, 5, 28), date(2025, 5, 30))
    assert p.acquired and p.shares == 1000.0 and p.price == 500.5 and p.shares_after == 5000.0 and p.ownership == "D"
    assert rows[2].relationship == "Director" and not rows[2].acquired and rows[2].issuer_cik == "0001326801"
    assert parse_dataset(_zip(SUBS, OWNERS, TRANS), {"0000000001"}) == []
    assert quarters_back(date(2026, 10, 5), 1) == ["2025q3_form345", "2025q4_form345", "2026q1_form345", "2026q2_form345", "2026q3_form345"]


FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument><documentType>4</documentType><periodOfReport>2026-09-30</periodOfReport>
<issuer><issuerCik>0001326801</issuerCik><issuerName>Meta</issuerName><issuerTradingSymbol>META</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000000444</rptOwnerCik><rptOwnerName>Zed Zoe</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><securityTitle><value>Class A</value></securityTitle><transactionDate><value>2026-09-29</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>300</value></transactionShares><transactionPricePerShare><value>700.25</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>1300</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
<nonDerivativeTransaction><securityTitle><value>Class A</value></securityTitle><transactionDate><value>2026-09-30</value></transactionDate>
<transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>50</value></transactionShares><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction>
</nonDerivativeTable>
<derivativeTable><derivativeTransaction><transactionCoding><transactionCode>A</transactionCode></transactionCoding></derivativeTransaction></derivativeTable>
</ownershipDocument>"""


def test_form4_xml_parses_the_owner_and_non_derivative_rows():
    rows = parse_form4_xml(FORM4, "0002-26-1", date(2026, 10, 1))
    assert len(rows) == 2 and all(r.issuer_cik == "0001326801" and r.owner_name == "Zed Zoe" and r.relationship == "Officer" and r.title == "CEO" for r in rows)
    assert (rows[0].trans_code, rows[0].acquired, rows[0].shares, rows[0].price, rows[0].shares_after, rows[0].ownership) == ("P", True, 300.0, 700.25, 1300.0, "D")
    assert (rows[1].trans_code, rows[1].acquired, rows[1].price, rows[1].trans_sk) == ("S", False, None, "xml-1")
    assert xml_document_name("xslF345X06/wk-form4_1790972209.xml") == "wk-form4_1790972209.xml"
    assert xml_document_name("form4.html") is None and xml_document_name(None) is None


class _Sec:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def get(self, url):
        self.calls.append(url)
        return self.bodies.get(url)

    def mode(self):
        return "fake"


def test_ingestion_stores_once_and_reads_recent_form4_filings(universe, tdb):
    from civalpha.platform.storage import DocumentStore
    from civalpha.platform.tickers import TickerResolver
    meta = TickerResolver(tdb).company_by_cik("0001326801")
    zip_bytes = _zip(SUBS, OWNERS, TRANS)
    sub = {"cik": "1326801", "name": "Meta", "tickers": ["META"], "exchanges": ["Nasdaq"],
           "filings": {"recent": {"accessionNumber": ["0002-26-1", "0002-26-0"], "form": ["4", "10-K"], "filingDate": ["2026-10-01", "2026-01-30"],
                                  "reportDate": ["2026-09-30", "2025-12-31"], "acceptanceDateTime": ["2026-10-01T20:00:00.000Z", "2026-01-30T21:00:00.000Z"],
                                  "primaryDocument": ["xslF345X06/wk-form4_1.xml", "meta-10k.htm"], "items": ["", ""]}, "files": []}}
    import json
    today = date(2026, 10, 5)
    bodies = {"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2026q2_form345.zip": zip_bytes,
              "https://data.sec.gov/submissions/CIK0001326801.json": json.dumps(sub).encode(),
              "https://www.sec.gov/Archives/edgar/data/1326801/0002261/wk-form4_1.xml": FORM4}
    sec = _Sec(bodies)
    svc = InsiderIngestionService(tdb, DocumentStore(tdb, root=None), universe=universe, lookback_years=1)
    lines = []
    r = svc.ingest(sec, lines.append, today=today)
    assert r.datasets == 1 and r.filings == 1 and r.transactions == 5          # 3 from the data set + 2 from the XML
    rows = tdb.all("SELECT accession_no, trans_sk, trans_code, available_at, source FROM insider_transaction WHERE company_id = :c ORDER BY id", c=meta)
    assert [(x["accession_no"], x["trans_code"]) for x in rows] == [("0001-25-1", "P"), ("0001-25-1", "F"), ("0001-25-2", "S"), ("0002-26-1", "P"), ("0002-26-1", "S")]
    assert rows[0]["available_at"].astimezone(timezone.utc) == datetime(2025, 5, 31, 3, 59, 59, tzinfo=timezone.utc)   # end of 30 May in New York
    assert rows[3]["source"] == "form4-xml"
    assert any("2026q2_form345" in l for l in lines)
    # a second run: the data set is remembered, the filing is already stored, nothing is inserted twice
    n = len(sec.calls)
    r2 = svc.ingest(sec, lines.append, today=today)
    assert r2.transactions == 0 and r2.datasets == 0
    assert tdb.scalar("SELECT count(*) FROM insider_transaction WHERE company_id = :c", c=meta) == 5
    assert not any(u.endswith("2026q2_form345.zip") for u in sec.calls[n:])     # stored data sets are not fetched again


def test_scale_errors_in_the_reported_price_fall_back_to_the_close():
    from civalpha.features import plausible_prices
    cal = pd.bdate_range("2025-11-03", periods=20)
    close = np.full(20, 1000.0)
    t = pd.DataFrame({"trans_date": [cal[5], cal[5], cal[5], cal[5], pd.NaT], "price": [1031414.0, 1030.31, None, 0.9, 5.0]})
    assert plausible_prices(t, close, cal).tolist() == [1000.0, 1030.31, 1000.0, 1000.0, 5.0]
    assert plausible_prices(t, None, cal).tolist() == [1031414.0, 1030.31, 0.0, 0.9, 5.0]


def _insiders(cal, rows):
    """rows: (company_id, calendar index of the filing day, code, acquired, shares, price, owner)"""
    return pd.DataFrame([{"company_id": c, "available_at": pd.Timestamp(cal[i].date(), tz="America/New_York") + pd.Timedelta(hours=23, minutes=59, seconds=59),
                          "trans_date": cal[i], "trans_code": code, "acquired": acq, "shares": sh, "price": pr, "owner_cik": owner, "owner_name": owner}
                         for c, i, code, acq, sh, pr, owner in rows])


def test_insider_feature_is_point_in_time_and_zero_when_silent():
    n = 400
    b = make_bundle(n_days=n, n_companies=2, seed=1)
    cal = b.calendar
    rows = [(1, 200, "P", True, 1000.0, 50.0, "A"), (1, 205, "P", True, 1000.0, 50.0, "B"), (1, 260, "S", False, 500.0, 60.0, "C"),
            (1, 210, "A", True, 1e6, 0.0, "GRANT")]
    bundle = DataBundle.build(b.companies, _stock(b), _bench(b), None, b.facts, b.exposures, b.events, b.targets, b.macro, b.membership,
                              insiders=_insiders(cal, rows))
    # a filing on day 200 (end of day New York) is first known at the close of day 201
    s200 = bundle.insider_signal(1, pit.close_ts(cal[200]), pit.close_ts(cal[100]))
    s201 = bundle.insider_signal(1, pit.close_ts(cal[201]), pit.close_ts(cal[100]))
    assert s200["buys"] == 0 and s201 == {"net_value": 50000.0, "buyers": 1, "sellers": 0, "buys": 1, "sells": 0}
    s = bundle.insider_signal(1, pit.close_ts(cal[250]), pit.close_ts(cal[250 - 63]))
    assert s["buyers"] == 2 and s["sellers"] == 0 and s["net_value"] == pytest.approx(100000.0)              # the grant is not a signal
    s = bundle.insider_signal(1, pit.close_ts(cal[270]), pit.close_ts(cal[270 - 63]))
    assert s["buyers"] == 0 and s["sellers"] == 1 and s["net_value"] == pytest.approx(-30000.0)              # the buys have aged out
    assert bundle.insider_signal(2, pit.close_ts(cal[270]), pit.close_ts(cal[0]))["buys"] == 0
    # no filed share count in this bundle -> no market cap -> the feature is NaN rather than a wrong number
    assert np.isnan(insider_feature(bundle, 1, 270, pit.close_ts(cal[270])))
    p = MarketPanel.from_bundle(bundle)
    m = p.insiders()
    assert m["insider_buyers_21d"].iat[200, 0] == 0 and m["insider_buyers_21d"].iat[201, 0] == 1
    assert m["insider_buyers_21d"].iat[206, 0] == 2 and m["insider_buyers_21d"].iat[201 + 21, 0] == 1 and m["insider_buyers_21d"].iat[206 + 21, 0] == 0
    assert m["insider_sellers_21d"].iat[262, 0] == 1 and (m["insider_buyers_21d"][2] == 0).all()
    fires = {s.key: s.fn(p, setups.SetupConfig()) for s in setups.setups() if s.family == "INSIDER"}
    assert fires["INSIDER_BUY"][1].to_numpy().nonzero()[0].tolist() == [201]
    assert fires["INSIDER_CLUSTER_BUY"][1].to_numpy().nonzero()[0].tolist() == [206]
    assert not fires["INSIDER_CLUSTER_SELL"][1].any()


def _stock(b):
    return pd.concat([pd.DataFrame({"company_id": c, "symbol": f"S{c}", "trade_date": b.calendar, "close": b.close[c]}) for c in b.tr])


def _bench(b):
    return pd.DataFrame({"symbol": "BMK", "trade_date": b.calendar, "close": b.bench_tr["BMK"] * 100})
