"""ADR-0011: company events from 8-K item codes and the item 5.02 keyword rule (exec-1), read from the stored filing index;
the 5.02 document is taken from storage or fetched once; nothing goes into `filing`."""
import json
from datetime import date, datetime, timedelta, timezone

from civalpha.platform import company_events as ce
from civalpha.platform.sec.client import archive_url, submissions_url
from civalpha.platform.storage import DocumentStore, NewDocument


def section(body: str) -> str:
    return ce.item_section("Item 5.02 Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain "
                           "Officers; Compensatory Arrangements of Certain Officers. " + body + " Item 9.01 Financial Statements and Exhibits.")


def kinds(body: str) -> list[str]:
    return [k for k, _ in ce.exec_kinds(section(body))]


def test_the_5_02_rule_reads_departures_of_the_ceo_and_the_cfo():
    assert kinds("On May 1, John Doe informed the Board of his decision to retire as Chief Executive Officer, effective June 30.") == ["CEO_DEPARTURE"]
    assert kinds("Jane Roe resigned from her position as Executive Vice President and Chief Financial Officer.") == ["CFO_DEPARTURE"]
    assert kinds("Mr. Doe will step down as CEO; Ms. Roe, our CFO, will also depart at the end of the year.") == ["CEO_DEPARTURE", "CFO_DEPARTURE"]
    assert kinds("The Company's principal executive officer will transition from the role on March 1.") == ["CEO_DEPARTURE"]


def test_appointments_directors_and_the_item_title_are_not_departures():
    assert kinds("On May 1 the Board appointed Jane Roe as Chief Executive Officer.") == []          # the title's "Departure" is not read
    assert kinds("Director Ann Lee will not stand for re-election at the annual meeting.") == []    # a director, no CEO/CFO named
    assert kinds("The Compensation Committee approved the 2026 bonus plan for the Chief Executive Officer.") == []
    far = "Mr. Smith resigned from the Board. " + "The committee met several times to review matters. " * 10 + "Our CEO thanked him."
    assert kinds(far) == []                                                                         # more than 300 characters apart
    ev = ce.exec_kinds(section("Jane Roe resigned as Chief Financial Officer."))[0][1]
    assert "resigned as Chief Financial Officer" in ev and len(ev) <= ce.EVIDENCE_CHARS and "Departure of Directors" not in ev


def test_item_section_stops_at_the_next_item_or_the_signature():
    text = "Item 2.02 Results. Revenue rose. Item 5.02 Departure. Jane resigned as CFO. Item 7.01 Regulation FD. CEO retired."
    assert ce.item_section(text).endswith("Jane resigned as CFO. ") and "Regulation FD" not in ce.item_section(text)
    assert "SIGNATURE" not in ce.item_section("Item 5.02 Jane resigned as CFO. SIGNATURES Pursuant to the requirements")
    assert ce.item_section("Item 2.02 Results only") == ""


# ------------------------------------------------------------------------------------------- the service

CIK = "0001326801"


def _index(rows):
    cols = {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": [], "acceptanceDateTime": [], "primaryDocument": [], "items": []}
    for acc, form, accepted, doc, items in rows:
        cols["accessionNumber"].append(acc); cols["form"].append(form); cols["filingDate"].append(accepted[:10])
        cols["reportDate"].append(accepted[:10]); cols["acceptanceDateTime"].append(accepted); cols["primaryDocument"].append(doc)
        cols["items"].append(items)
    return json.dumps({"cik": "1326801", "name": "Meta", "tickers": ["META"], "exchanges": ["Nasdaq"],
                       "filings": {"recent": cols, "files": []}}).encode()


class _Sec:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def get(self, url):
        self.calls.append(url)
        return self.bodies.get(url)


def test_update_reads_items_from_the_index_and_fetches_5_02_once(universe, tdb, tmp_path):
    docs = DocumentStore(tdb, tmp_path)
    recent = (date.today() - timedelta(days=5)).isoformat()
    old = (date.today() - timedelta(days=400)).isoformat()
    rows = [("0001-26-1", "8-K", recent + "T20:05:00.000Z", "ceo.htm", "5.02,9.01"),
            ("0001-26-2", "8-K", recent + "T21:30:00.000Z", "deal.htm", "1.01,2.03,9.01"),
            ("0001-26-3", "8-K", recent + "T12:00:00.000Z", "restr.htm", "2.05,2.06,7.01"),
            ("0001-26-4", "8-K", recent + "T12:00:00.000Z", "board.htm", "5.02"),
            ("0001-26-5", "10-Q", recent + "T12:00:00.000Z", "q.htm", ""),
            ("0001-25-9", "8-K", old + "T12:00:00.000Z", "old.htm", "5.02")]
    docs.store(NewDocument("SEC_API", "SEC EDGAR", submissions_url(CIK), None, "index", None, "application/json", _index(rows)))
    stored = archive_url(CIK, "0001-26-4", "board.htm")                       # already stored by the SEC step: no request
    docs.store(NewDocument("SEC_FILING", "SEC EDGAR", stored, "0001-26-4", "8-K", None, "text/html",
                           b"<p>Item 5.02 The Board elected Ann Lee as a director.</p>"))
    sec = _Sec({archive_url(CIK, "0001-26-1", "ceo.htm"):
                b"<p>Item 5.02 Departure of Directors or Certain Officers.</p><p>John Doe will retire as Chief Executive Officer.</p>"})
    svc = ce.CompanyEventService(tdb, docs, universe=universe)
    lines = []
    out = svc.update(sec, lines.append)
    got = {(r["accession_no"], r["kind"]) for r in tdb.all("SELECT accession_no, kind FROM company_event")}
    assert got == {("0001-26-1", "CEO_DEPARTURE"), ("0001-26-2", "MATERIAL_AGREEMENT"), ("0001-26-3", "RESTRUCTURING"),
                   ("0001-26-3", "IMPAIRMENT"), ("0001-26-4", "OFFICER_DIRECTOR_CHANGE")}          # the old 8-K is outside 30 days
    assert sec.calls == [archive_url(CIK, "0001-26-1", "ceo.htm")] and out.fetched == 1 and out.filings == 4
    ceo = tdb.one("SELECT * FROM company_event WHERE kind = 'CEO_DEPARTURE'")
    assert ceo["method"] == "exec-1" and "retire as Chief Executive Officer" in ceo["evidence"] and ceo["source_document_id"]
    assert ceo["accepted_at"].astimezone(timezone.utc) == datetime.fromisoformat(recent + "T20:05:00+00:00")
    assert tdb.scalar("SELECT count(*) FROM filing WHERE accession_no LIKE '0001-26-%'") == 0
    # a rerun stores nothing twice and fetches nothing; the history reaches the old 8-K
    again = svc.update(sec, lines.append)
    assert again.filings == 0 and len(sec.calls) == 1
    hist = svc.update(_Sec({archive_url(CIK, "0001-25-9", "old.htm"): b"Item 5.02 Jane Roe resigned as CFO."}), lines.append,
                      since=date.today() - timedelta(days=500))
    assert hist.events == {"CFO_DEPARTURE": 1}


def test_a_5_02_without_its_document_is_read_on_a_later_run(universe, tdb, tmp_path):
    docs = DocumentStore(tdb, tmp_path)
    day = (date.today() - timedelta(days=2)).isoformat()
    docs.store(NewDocument("SEC_API", "SEC EDGAR", submissions_url(CIK), None, "index", None, "application/json",
                           _index([("0001-26-7", "8-K", day + "T20:00:00.000Z", "x.htm", "5.02")])))
    svc = ce.CompanyEventService(tdb, docs, universe=universe)
    assert svc.update(_Sec({}), lambda _l: None).events == {}
    assert svc.update(_Sec({archive_url(CIK, "0001-26-7", "x.htm"): b"Item 5.02 The CFO resigned."}), lambda _l: None).events == {"CFO_DEPARTURE": 1}


def test_backfill_endpoint_queues_the_job(api):
    r = api.post("/api/admin/company-events/backfill", json={"from": "2020-10-05"})
    assert r.status_code == 200 and r.json()["jobType"] == "COMPANY_EVENTS_BACKFILL" and r.json()["params"] == {"from": "2020-10-05"}
    from civalpha.platform.sql import db
    db().execute("DELETE FROM pipeline_job WHERE id = :i", i=r.json()["id"])


def test_panel_counts_an_8k_from_the_first_close_after_acceptance():
    import numpy as np
    import pandas as pd

    from civalpha.strategies.panel import MarketPanel
    from helpers import make_bundle
    b = make_bundle(n_days=400)                                                     # 2020-01-01 onwards
    b.company_events = pd.DataFrame({
        "company_id": [1, 1, 1, 2],
        "accession_no": ["a-1", "a-2", "a-2", "a-3"],
        "kind": ["CEO_DEPARTURE", "RESTRUCTURING", "IMPAIRMENT", "CFO_DEPARTURE"],
        "accepted_at": pd.to_datetime(["2020-06-02T22:00Z", "2020-06-03T15:00Z", "2020-06-03T15:00Z", "2020-09-01T15:00Z"], utc=True)})
    p = MarketPanel.from_bundle(b)
    m = p.company_events()
    cal = p.calendar
    d = lambda s: cal.searchsorted(pd.Timestamp(s))  # noqa: E731
    ceo = m["ceo_departure_21d"][1]
    assert ceo.iloc[d("2020-06-02")] == 0 and ceo.iloc[d("2020-06-03")] == 1       # accepted after the close: next session
    assert m["distress_8k_21d"][1].iloc[d("2020-06-03")] == 1                       # one 8-K with two distress items counts once
    dep = m["exec_departure_63d"][1]
    assert np.isnan(dep.iloc[d("2020-06-01")]) and dep.iloc[d("2020-07-01")] == 1 and dep.iloc[d("2020-12-01")] == 0
    assert m["exec_departure_63d"][2].iloc[d("2020-09-01")] == 1 and m["distress_8k_63d"][2].iloc[d("2020-09-01")] == 0


def test_the_events_endpoint_lists_a_companys_8ks(api):
    from civalpha.platform.sql import db
    meta = db().scalar("SELECT company_id FROM cik_mapping WHERE cik = '0001326801' LIMIT 1")
    for acc, kind, item, ev in (("t-1", "CEO_DEPARTURE", "5.02", "John Doe will retire as Chief Executive Officer."),
                                ("t-2", "RESTRUCTURING", "2.05", None), ("t-3", "OFFICER_DIRECTOR_CHANGE", "5.02", None)):
        db().execute("""INSERT INTO company_event (company_id, accession_no, item, kind, accepted_at, filed_date, method, evidence)
                        VALUES (:c, :a, :i, :k, now() - interval '3 days', current_date - 3, :m, :e)""",
                     c=meta, a=acc, i=item, k=kind, m="exec-1" if item == "5.02" else "ITEM", e=ev)
    try:
        r = api.get("/api/companies/META/events").json()
        by = {e["kind"]: e for e in r["events"]}
        assert set(by) == {"CEO_DEPARTURE", "RESTRUCTURING"}                                   # director changes hidden by default
        ceo, restr = by["CEO_DEPARTURE"], by["RESTRUCTURING"]
        assert ceo["label"] == "CEO departure" and not ceo["distress"] and restr["distress"] and restr["evidence"] is None
        assert ceo["url"].startswith("https://www.sec.gov/Archives/edgar/data/1326801/") and r["lastYear"]["CEO_DEPARTURE"] == 1
        assert len(api.get("/api/companies/META/events?all=true").json()["events"]) == 3
    finally:
        db().execute("DELETE FROM company_event WHERE accession_no LIKE 't-%'")


# ------------------------------------------------------------------------------------------- decision 9: Claude reads 5.02

from types import SimpleNamespace

from civalpha.platform import exec_changes as xc


def _msg(answer, stop="end_turn"):
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


A_CEO = {"ceo_change": True, "cfo_change": False, "previously_reported": False, "departure_type": "RETIREMENT",
         "evidence": "John Doe will retire as Chief Executive Officer."}
A_NONE = {"ceo_change": False, "cfo_change": False, "previously_reported": False, "departure_type": "NONE", "evidence": ""}


def test_answers_are_parsed_strictly_and_follow_ups_are_not_new_changes():
    assert xc.parse(_msg(A_CEO)) == A_CEO
    assert xc.parse(_msg(A_CEO, stop="refusal")) is None and xc.parse(_msg(A_CEO, stop="max_tokens")) is None
    assert xc.parse(_msg("not json")) is None and xc.parse(_msg({**A_CEO, "departure_type": "FIRED"})) is None
    assert xc.kinds_of(A_CEO) == ["CEO_DEPARTURE"] and xc.kinds_of(A_NONE) == ["OFFICER_DIRECTOR_CHANGE"]
    assert xc.kinds_of({**A_CEO, "cfo_change": True}) == ["CEO_DEPARTURE", "CFO_DEPARTURE"]
    assert xc.kinds_of({**A_CEO, "previously_reported": True}) == ["OFFICER_DIRECTOR_CHANGE"]
    p = xc.request_params("claude-opus-5-5", "Meta", "Item 5.02 text")
    assert p["output_config"]["format"]["schema"] == xc.SCHEMA and p["output_config"]["effort"] == "low"
    assert "Meta" in p["messages"][0]["content"] and "<item_5_02>\nItem 5.02 text\n</item_5_02>" in p["messages"][0]["content"]


class _Batches:
    def __init__(self, answers):
        self.answers, self.created, self.status = answers, [], "in_progress"

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id="msgbatch_1")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self.status)

    def results(self, batch_id):
        for acc, a in self.answers.items():
            if a == "errored":
                yield SimpleNamespace(custom_id=acc, result=SimpleNamespace(type="errored"))
            else:
                yield SimpleNamespace(custom_id=acc, result=SimpleNamespace(type="succeeded", message=_msg(a)))


def _seed_5_02(tdb, universe, tmp_path):
    docs = DocumentStore(tdb, tmp_path)
    day = (date.today() - timedelta(days=3)).isoformat()
    rows = [("0001-26-1", "8-K", day + "T20:00:00.000Z", "a.htm", "5.02"), ("0001-26-2", "8-K", day + "T20:00:00.000Z", "b.htm", "5.02"),
            ("0001-26-3", "8-K", day + "T20:00:00.000Z", "c.htm", "5.02")]
    docs.store(NewDocument("SEC_API", "SEC EDGAR", submissions_url(CIK), None, "index", None, "application/json", _index(rows)))
    sec = _Sec({archive_url(CIK, a, d): f"<p>Item 5.02 {a}: the Executive Vice President, who reports to our CEO, will retire.</p>".encode()
                for a, _f, _t, d, _i in rows})
    ce.CompanyEventService(tdb, docs, universe=universe).update(sec, lambda _l: None)
    return docs


def test_the_batch_replaces_the_rules_rows_and_is_collected_once(universe, tdb, tmp_path):
    docs = _seed_5_02(tdb, universe, tmp_path)
    assert tdb.scalar("SELECT count(*) FROM company_event WHERE kind = 'CEO_DEPARTURE' AND method = 'exec-1'") == 3   # the rule's misses
    batches = _Batches({"0001-26-1": A_CEO, "0001-26-2": A_NONE, "0001-26-3": "errored"})
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    r = xc.ExecChangeReader(tdb, docs, client=client, model="claude-opus-5-5")
    lines = []
    assert r.submit(lines.append) == "msgbatch_1"
    sent = batches.created[0]
    assert sorted(q["custom_id"] for q in sent) == ["0001-26-1", "0001-26-2", "0001-26-3"]
    assert "reports to our CEO" in sent[0]["params"]["messages"][0]["content"]
    assert r.pending() == []                                                    # in an uncollected batch: not sent twice
    assert r.collect(lines.append).read == 0 and "still in_progress" in lines[-1]
    batches.status = "ended"
    got = r.collect(lines.append)
    assert got.read == 2 and got.failed == 1 and got.kinds == {"CEO_DEPARTURE": 1, "OFFICER_DIRECTOR_CHANGE": 1}
    rows = {(x["accession_no"], x["kind"], x["method"]) for x in tdb.all("SELECT accession_no, kind, method FROM company_event")}
    assert rows == {("0001-26-1", "CEO_DEPARTURE", "exec-llm-1"), ("0001-26-2", "OFFICER_DIRECTOR_CHANGE", "exec-llm-1"),
                    ("0001-26-3", "CEO_DEPARTURE", "exec-1")}                   # the errored one keeps the rule's row
    det = tdb.scalar("SELECT detail FROM company_event WHERE accession_no = '0001-26-1'")
    assert det["departure_type"] == "RETIREMENT" and det["model"] == "claude-opus-5-5"
    assert r.collect(lines.append).read == 0                                     # collected once
    assert [x["accession_no"] for x in r.pending()] == ["0001-26-3"]            # left for the next batch


def test_new_filings_are_read_one_by_one(universe, tdb, tmp_path):
    docs = _seed_5_02(tdb, universe, tmp_path)
    answers = iter([_msg(A_NONE), RuntimeError("overloaded"), _msg({**A_CEO, "previously_reported": True})])

    def create(**params):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a
    r = xc.ExecChangeReader(tdb, docs, client=SimpleNamespace(messages=SimpleNamespace(create=create)), model="claude-opus-5-5")
    out = r.read_new(lambda _l: None, since=datetime.now(timezone.utc) - timedelta(days=30))
    assert out.read == 2 and out.failed == 1
    assert tdb.scalar("SELECT count(*) FROM company_event WHERE method = 'exec-llm-1' AND kind = 'OFFICER_DIRECTOR_CHANGE'") == 2
