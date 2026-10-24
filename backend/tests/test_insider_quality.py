"""ADR-0010: the Rule 10b5-1 flag from the SEC data sets and Form 4 XML (also for rows stored before it was read), the
role from the title, and the routine / informed classification, point in time."""
import io
import zipfile
from datetime import date

import pandas as pd

from civalpha import insider_quality as iq
from civalpha.features import _insider_frame
from civalpha.platform.insiders import InsiderIngestionService, dataset_plan_flags, parse_dataset, parse_form4_xml, plan_flag
from civalpha.platform.storage import DocumentStore, NewDocument
from helpers import make_bundle


def _zip(subs, with_flag=True) -> bytes:
    head = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"] + (["AFF10B5ONE"] if with_flag else [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", "\t".join(head) + "\n" + "".join("\t".join(r[:len(head)]) + "\n" for r in subs))
        z.writestr("REPORTINGOWNER.tsv", "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
                   + "".join(f"{r[0]}\t0000000111\tDoe Jane\tOfficer\tSVP & CFO\n" for r in subs))
        z.writestr("NONDERIV_TRANS.tsv", "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_ACQUIRED_DISP_CD\n"
                   + "".join(f"{r[0]}\t{i}\t02-MAY-2024\tS\t100\tD\n" for i, r in enumerate(subs)))
    return buf.getvalue()


SUBS = [("0001-24-1", "03-MAY-2024", "4", "0001326801", "1"), ("0001-24-2", "03-MAY-2024", "4", "0001326801", "false"),
        ("0001-24-3", "03-MAY-2024", "4", "0001326801", ""), ("0001-24-4", "03-MAY-2024", "4/A", "0001326801", "true")]

FORM4 = b"""<?xml version="1.0"?><ownershipDocument><documentType>4</documentType><aff10b5One>1</aff10b5One>
<issuer><issuerCik>0001326801</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000000444</rptOwnerCik><rptOwnerName>Zed Zoe</rptOwnerName></reportingOwnerId></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-09-29</value></transactionDate>
<transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>50</value></transactionShares><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


def test_the_10b5_1_flag_is_read_from_both_sources():
    assert [plan_flag(v) for v in ("1", "true", "TRUE", "0", "false", "", None)] == [True, True, True, False, False, None, None]
    rows = parse_dataset(_zip(SUBS), {"0001326801"})
    assert [r.plan_10b5_1 for r in rows] == [True, False, None, True]
    assert [r.plan_10b5_1 for r in parse_dataset(_zip(SUBS, with_flag=False), {"0001326801"})] == [None] * 4   # before 2023-Q1
    assert dataset_plan_flags(_zip(SUBS)) == {"0001-24-1": True, "0001-24-2": False, "0001-24-3": None, "0001-24-4": True}
    assert [r.plan_10b5_1 for r in parse_form4_xml(FORM4, "0002-26-1", date(2026, 10, 1))] == [True]
    assert [r.plan_10b5_1 for r in parse_form4_xml(FORM4.replace(b"<aff10b5One>1</aff10b5One>", b""), "0002-26-1", date(2026, 10, 1))] == [None]


def test_rows_stored_before_the_flag_get_it_from_their_document(universe, tdb, tmp_path):
    docs = DocumentStore(tdb, tmp_path)
    svc = InsiderIngestionService(tdb, docs, universe=universe)
    zip_doc = docs.store(NewDocument("SEC_INSIDER_DATASET", "SEC EDGAR", "https://sec/2024q2.zip", None, "2024q2_form345", None,
                                     "application/zip", _zip(SUBS)))
    xml_doc = docs.store(NewDocument("SEC_FILING", "SEC EDGAR", "https://sec/f4.xml", "0002-26-1", "Form 4", None, "application/xml", FORM4))
    meta = tdb.scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801' LIMIT 1")
    svc._store(parse_dataset(_zip(SUBS), {"0001326801"}), {"0001326801": meta}, "2024q2_form345", zip_doc.id)
    svc._store(parse_form4_xml(FORM4, "0002-26-1", date(2026, 10, 1)), {"0001326801": meta}, "form4-xml", xml_doc.id)
    tdb.execute("UPDATE insider_transaction SET plan_10b5_1 = NULL")              # as stored before V23
    lines = []
    assert svc.backfill_plan_flags(lines.append) == 4                             # 3 known in the data set + the XML row
    flags = {r["accession_no"]: r["plan_10b5_1"] for r in tdb.all("SELECT accession_no, plan_10b5_1 FROM insider_transaction ORDER BY id")}
    assert flags == {"0001-24-1": True, "0001-24-2": False, "0001-24-3": None, "0001-24-4": True, "0002-26-1": True}
    assert lines == ["insiders: 10b5-1 flag read for 4 stored transactions"]
    assert svc.backfill_plan_flags(lines.append) == 0


def test_role_from_the_title():
    assert [iq.insider_role("Officer", t) for t in ("Chief Executive Officer", "SVP & CFO", "Chairman and CEO", "President & CEO CET",
                                                     "Chief Financial Officer", "VP & Controller", None)] == \
        ["CEO_CFO"] * 5 + ["Officer", "Officer"]
    assert iq.insider_role("Director", "") == "Director" and iq.insider_role(None, None) == "Other"
    assert iq.insider_role("Director", float("nan")) == "Director" and iq.insider_role(float("nan"), float("nan")) == "Other"


def _trades(rows):
    """rows: (owner, trade date, plan flag); each public at the end of its trade date."""
    return pd.DataFrame({"company_id": 1, "owner": [r[0] for r in rows], "trans_date": pd.to_datetime([r[1] for r in rows]),
                         "available_at": pd.to_datetime([r[1] + "T23:00Z" for r in rows], utc=True),
                         "plan_10b5_1": [r[2] if len(r) > 2 else None for r in rows]})


START = date(2020, 7, 2)


def test_routine_informed_planned_and_not_classifiable():
    rows = [("A", "2021-03-10"), ("A", "2022-03-11"), ("A", "2023-03-09"), ("A", "2024-03-12"),     # every March: routine
            ("B", "2021-08-02"), ("B", "2022-02-14"), ("B", "2023-11-20"), ("B", "2024-09-05"),     # trades every year, not in September
            ("C", "2024-05-06"),                                                                   # no history
            ("D", "2021-03-10"), ("D", "2022-03-11"), ("D", "2023-03-09"), ("D", "2024-09-05", True),
            ("E", "2020-08-03"), ("E", "2021-08-02"), ("E", "2022-08-01"), ("E", "2023-08-01")]     # Aug 2023: first classifiable month
    q = list(iq.classify(_trades(rows), START))
    assert q[3] == iq.ROUTINE and q[7] == iq.INFORMED and q[8] == iq.NOT_CLASSIFIABLE and q[12] == iq.PLANNED
    assert q[16] == iq.ROUTINE
    assert q[:3] == [iq.NOT_CLASSIFIABLE] * 3                     # before 2023-07 the data cannot look back three years
    assert iq.classifiable_from(START) == date(2023, 7, 1)
    assert list(iq.classify(_trades(rows), None)) == [iq.NOT_CLASSIFIABLE] * 12 + [iq.PLANNED] + [iq.NOT_CLASSIFIABLE] * 4


def test_a_later_trade_never_changes_an_earlier_classification():
    base = [("B", "2021-08-02"), ("B", "2022-02-14"), ("B", "2023-11-20"), ("B", "2024-09-05"), ("B", "2024-11-03")]
    later = base + [("B", "2025-09-08"), ("B", "2026-09-04"), ("B", "2027-09-06")]
    assert list(iq.classify(_trades(base), START)) == list(iq.classify(_trades(later), START))[:len(base)]
    # trades made public at the same time do not count as each other's history
    same = [("F", "2021-06-01"), ("F", "2022-06-01"), ("F", "2023-06-01")]
    t = _trades(same + [("F", "2024-06-03"), ("F", "2024-06-03")])
    assert list(iq.classify(t, START))[-2:] == [iq.ROUTINE, iq.ROUTINE]


def test_the_bundle_marks_informed_and_executive_trades():
    b = make_bundle(n_days=1300)
    days = ["2021-08-02", "2022-02-14", "2023-11-20", "2024-09-05"]
    ins = pd.DataFrame({"company_id": 1, "available_at": pd.to_datetime([d + "T23:00Z" for d in days], utc=True),
                        "trans_date": pd.to_datetime(days), "trans_code": "P", "acquired": True, "shares": 10.0, "price": 50.0,
                        "owner_cik": "0000000111", "owner_name": "Doe Jane", "relationship": "Officer", "title": "Chief Financial Officer",
                        "plan_10b5_1": None})
    b.insiders = _insider_frame(ins)
    b.insiders.loc[0, "available_at"] = pd.Timestamp("2020-07-02T03:59:59Z")       # the data start (first data set)
    times, value, is_buy, owners, informed, execs = b.insider_quality_trades(1)
    assert list(informed) == [False, False, False, True] and all(execs) and all(is_buy)


def _panel_with(rows):
    """rows: (owner, title, code, trade date, plan flag); public at the end of the trade date; the first row sets the data start."""
    from civalpha.strategies.panel import MarketPanel
    b = make_bundle(n_days=1300)                                                    # 2020-01-01 to 2024-12
    ins = pd.DataFrame({"company_id": 1, "available_at": pd.to_datetime([r[3] + "T23:00Z" for r in rows], utc=True),
                        "trans_date": pd.to_datetime([r[3] for r in rows]), "trans_code": [r[2] for r in rows],
                        "acquired": [r[2] == "P" for r in rows], "shares": 10.0, "price": 50.0, "owner_cik": [r[0] for r in rows],
                        "owner_name": [r[0] for r in rows], "relationship": "Officer", "title": [r[1] for r in rows],
                        "plan_10b5_1": [r[4] for r in rows]})
    b.insiders = _insider_frame(ins)
    return b, MarketPanel.from_bundle(b)


def test_informed_inputs_count_informed_trades_only_and_are_unknown_before_three_years():
    rows = [("X", None, "S", "2020-07-02", None),                                   # the data start
            ("CEO", "Chief Executive Officer", "P", "2021-08-02", None), ("CEO", "Chief Executive Officer", "P", "2022-02-14", None),
            ("CEO", "Chief Executive Officer", "P", "2023-11-20", None), ("CEO", "Chief Executive Officer", "P", "2024-09-05", None),
            ("PLAN", "Director", "P", "2024-09-05", True)]
    b, p = _panel_with(rows)
    m = p.insiders()
    cal = p.calendar
    before, sept = cal.searchsorted(pd.Timestamp("2023-06-30")), cal.searchsorted(pd.Timestamp("2024-09-06"))
    assert pd.isna(m["insider_exec_buyers_63d"].iloc[before, 0]) and pd.isna(m["insider_informed_net_63d"].iloc[before, 0])
    assert m["insider_exec_buyers_63d"].iloc[sept - 2, 0] == 0                       # before the informed purchase is public
    assert m["insider_exec_buyers_63d"].iloc[sept, 0] == 1 and m["insider_informed_buyers_21d"].iloc[sept, 0] == 1   # the plan buy is not counted
    assert m["insider_buyers_21d"].iloc[sept, 0] == 2                                 # the plain count still sees both


def test_informed_inputs_do_not_look_ahead():
    rows = [("X", None, "S", "2020-07-02", None), ("A", None, "S", "2021-03-10", None), ("A", None, "S", "2022-03-11", None),
            ("A", None, "S", "2023-08-09", None), ("B", None, "S", "2023-08-10", None)]
    later = rows + [("A", None, "S", "2024-03-12", None), ("C", None, "S", "2024-06-03", None)]
    _, p1 = _panel_with(rows)
    _, p2 = _panel_with(later)
    cut = p1.calendar.searchsorted(pd.Timestamp("2024-01-02"))
    for k in ("insider_informed_sellers_21d", "insider_exec_buyers_63d"):
        a, b2 = p1.insiders()[k].iloc[:cut, 0].to_numpy(), p2.insiders()[k].iloc[:cut, 0].to_numpy()
        assert ((a == b2) | (pd.isna(a) & pd.isna(b2))).all()


def test_the_insiders_endpoint_marks_each_trade(api):
    from civalpha.platform.sql import db
    meta = db().scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801' LIMIT 1")
    rows = [("2020-07-02", "S", None, "Other Person"), ("2021-08-02", "P", None, "Doe Jane"), ("2022-02-14", "P", None, "Doe Jane"),
            ("2023-11-20", "P", None, "Doe Jane"), ("2024-09-05", "P", None, "Doe Jane"), ("2024-09-06", "S", True, "Roe Rick")]
    ids = [db().scalar("""
        INSERT INTO insider_transaction (company_id, accession_no, trans_sk, owner_cik, owner_name, relationship, title, trans_date,
            filed_date, available_at, trans_code, acquired, shares, price, source, plan_10b5_1)
        VALUES (:c, :a, '1', NULL, :o, 'Officer', :t, :d, :d, CAST(:d AS date) + interval '23 hours', :code, :acq, 10, 50, 'test', :plan)
        RETURNING id""", c=meta, a=f"test-{i}", o=o, t="Chief Financial Officer" if o == "Doe Jane" else None, d=date.fromisoformat(d),
                       code=code, acq=code == "P", plan=plan) for i, (d, code, plan, o) in enumerate(rows)]
    try:
        r = api.get("/api/companies/META/insiders?limit=10").json()
        by = {t["id"]: t for t in r["transactions"]}
        assert by[ids[4]]["quality"] == "INFORMED" and by[ids[4]]["role"] == "CEO_CFO"
        assert by[ids[5]]["quality"] == "PLANNED" and by[ids[5]]["tradingPlan"] is True
        assert by[ids[1]]["quality"] == "NOT_CLASSIFIABLE" and by[ids[0]]["role"] == "Officer"
        assert set(r["windows"]["252d"]) >= {"informedBuys", "informedSells", "plannedSells", "routineTrades"}
    finally:
        db().execute("DELETE FROM insider_transaction WHERE accession_no LIKE 'test-%'")
