"""ADR-0003: the corporate-action detector (text extraction, keyword rule, Jev client, reconciliation, evaluation)."""
import json

import httpx
import numpy as np
import pandas as pd
import pytest

from civalpha import corpactions as ca
from civalpha.platform.jev import JevClient, JevError
from civalpha.platform.settings import Jev

EIGHT_K = """<html><body>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p><p>FORM 8-K</p>
<p>Corteva, Inc. (Exact name of registrant)</p>
<table><tr><td>Common Stock, par value $0.01</td><td>CTVA</td><td>New York Stock Exchange</td></tr></table>
<p>Check the appropriate box below if the Form 8-K filing is intended to simultaneously satisfy the filing obligation...</p>
<p><b>Item 8.01 Other Events</b></p>
<p>Effective upon the Distribution, each Corteva stockholder of record as of the Record Date will receive one share of
Vylor Common Stock for every share of Corteva common stock held. The separation and distribution is expected to be
completed prior to 9:30 a.m. on October&nbsp;1, 2026.</p>
<p><b>Item 9.01 Financial Statements and Exhibits</b></p><p>99.1 Press release</p>
<p>SIGNATURES</p><p>Pursuant to the requirements of the Securities Exchange Act of 1934, the registrant has duly caused...</p>
</body></html>"""


def test_item_text_keeps_the_items_and_drops_cover_and_signature():
    t = ca.item_text(ca.to_text(EIGHT_K))
    assert t.startswith("Item 8.01 Other Events")
    assert "Vylor Common Stock for every share" in t and "October 1, 2026" in t
    assert "Check the appropriate box" not in t and "Pursuant to the requirements" not in t
    assert len(ca.item_text("Item 8.01 " + "x" * 50_000)) == ca.MAX_STATE_CHARS


@pytest.mark.parametrize("text,kind", [
    ("Item 8.01. The separation and distribution of Vylor is expected to be completed", "SPIN_OFF_OR_DISTRIBUTION"),
    ("Item 5.03. The Board approved a 1-for-20 reverse stock split effective September 15", "REVERSE_SPLIT"),
    ("Item 8.01. The Board declared a ten-for-one forward split of the common stock", "FORWARD_SPLIT"),
    ("Item 8.01. Our 2026 annual meeting will be held virtually on May 3", "NONE"),
])
def test_keyword_rule(text, kind):
    c = ca.keyword_classify(text)
    assert c.kind == kind and c.model == ca.KEYWORD_RULE
    assert c.flagged is (kind != "NONE")
    if kind != "NONE":
        assert c.evidence and len(c.evidence) <= len(text)


def test_reconcile_window_is_minus_5_to_plus_120_days():
    actions = pd.DataFrame({"id": [1, 2, 3], "company_id": [7, 7, 8], "ex_date": ["2026-10-01", "2027-06-01", "2026-09-20"],
                            "action_type": ["SPIN_OFF", "SPLIT", "SPLIT"]})
    cands = pd.DataFrame({"company_id": [7, 7, 7, 8], "accepted_at": pd.to_datetime(
        ["2026-09-15T12:09:00Z", "2026-10-04T12:00:00Z", "2026-10-07T12:00:00Z", "2026-09-30T12:00:00Z"], utc=True)})
    r = ca.reconcile(cands, actions)
    # before the ex-date; 3 days after (inside the 5-day tolerance); 6 days after and the next action is 237 days out;
    # another company's action 10 days before its filing
    assert list(r) == [1, 1, None, None]


def _client(handler):
    return JevClient(Jev(api_key="k", model="jev-1.13.0", base_url="https://api.typesafe.ai/v1/systemone"),
                     transport=httpx.MockTransport(handler))


def test_jev_request_is_pinned_and_answers_are_mapped():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"model": "jev-1.13.0", "usage": {"input_tokens": 410, "output_tokens": 70}, "answers": {
            "kind": {"type": "choice", "choice": "spin_off_or_distribution", "confidence": 0.9,
                     "probabilities": {"spin_off_or_distribution": 0.91, "none": 0.06, "forward_split": 0.03}},
            "changes_shares": {"type": "noul", "noul": 0.88}}})

    c = ca.jev_classify(_client(handler), "Item 8.01 ... one share of Vylor for every share of Corteva")
    assert seen["auth"] == "Bearer k" and seen["body"]["model"] == "jev-1.13.0"
    assert set(seen["body"]["questions"]) == {"kind", "changes_shares"}
    assert seen["body"]["questions"]["kind"]["type"] == "choice" and seen["body"]["questions"]["changes_shares"]["type"] == "noul"
    assert (c.kind, c.probability, c.model) == ("SPIN_OFF_OR_DISTRIBUTION", 0.88, "jev-1.13.0") and c.flagged
    assert json.loads(c.evidence)["tokens"] == 410
    # a "none" choice or a low probability is not a flag
    assert not ca.Classified("FORWARD_SPLIT", 0.3, "jev-1.13.0", "").flagged
    assert not ca.Classified("NONE", 0.9, "jev-1.13.0", "").flagged


@pytest.mark.parametrize("response", [httpx.Response(500, text="boom"),
                                      httpx.Response(200, json={"model": "jev-1.13.0", "answers": {"kind": {}}})])
def test_jev_failures_raise_a_jev_error(response):
    with pytest.raises(JevError):
        _client(lambda _req: response).ask("x", ca.JEV_QUESTIONS)
    with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
        JevClient(Jev(api_key="")).ask("x", ca.JEV_QUESTIONS)


def test_evaluation_counts_event_recall_and_quiet_flags_with_cis():
    actions = pd.DataFrame({"id": [1, 2, 3], "company_id": [1, 2, 3], "symbol": ["A", "B", "C"],
                            "ex_date": pd.to_datetime(["2024-06-10", "2024-07-15", "2024-03-01"]),
                            "action_type": ["SPLIT", "SPLIT", "SPIN_OFF"], "value": [10.0, 4.0, 5.0]})
    t = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    cands = pd.DataFrame([
        dict(filing_id=10, company_id=1, symbol="A", accepted_at=t("2024-05-22"), kind="FORWARD_SPLIT", evidence="stock split", flagged=True),
        dict(filing_id=11, company_id=2, symbol="B", accepted_at=t("2024-06-01"), kind="NONE", evidence="", flagged=False),
        dict(filing_id=12, company_id=3, symbol="C", accepted_at=t("2024-02-01"), kind="SPIN_OFF_OR_DISTRIBUTION", evidence="spin-off", flagged=True),
        dict(filing_id=13, company_id=4, symbol="D", accepted_at=t("2024-05-01"), kind="SPIN_OFF_OR_DISTRIBUTION", evidence="spin-off", flagged=True),
        dict(filing_id=14, company_id=5, symbol="E", accepted_at=t("2024-05-01"), kind="NONE", evidence="", flagged=False),
        dict(filing_id=15, company_id=6, symbol="F", accepted_at=t("2024-05-01"), kind="NONE", evidence="", flagged=False),
    ])
    r = ca.evaluate(cands, actions, actions, n_boot=200)
    assert (r["events"], r["eventsFlagged"]) == (3, 2) and np.isclose(r["recall"], 2 / 3)
    assert r["missed"] == [{"symbol": "B", "exDate": "2024-07-15", "type": "SPLIT", "value": 4.0, "relevant8Ks": 1}]
    # quiet filings: companies 4, 5, 6 have no action within 180 days; one of the three is flagged (a gap or an error)
    assert (r["quietFilings"], r["quietFlagged"]) == (3, 1) and [x["filing_id"] for x in r["quietFlaggedList"]] == [13]
    lo, hi = r["recallCi95"]
    assert 0 <= lo <= r["recall"] <= hi <= 1
    keyword = {**r}
    assert ca.adoption(keyword, {**r, "recall": 1.0})["adopt"] is True
    assert ca.adoption(keyword, {**r, "quietFlagRate": r["quietFlagRate"] + 0.05})["adopt"] is False


def test_jev_run_without_a_key_does_no_work_and_says_why():
    logs = []

    class Off:
        enabled = False

    r = ca.run(engine=None, method="jev", client=Off(), log=logs.append)     # engine=None: no database is touched
    assert r == {"method": "JEV", "classified": 0, "skipped": True, "errors": 0}
    assert logs == ["JEV skipped: TYPESAFE_API_KEY is not set (ADR-0003 phase 2)"]


def test_keyword_run_stores_candidates_reconciled_with_recorded_actions(pg, tmp_path):
    """End to end on a database: two 8-Ks of one company, one announcing a spin-off that is recorded, one quiet."""
    from sqlalchemy import text

    e = pg.engine
    docs = tmp_path / "documents"
    (docs / "aa").mkdir(parents=True)
    (docs / "aa" / "spin.html").write_text(EIGHT_K)
    (docs / "aa" / "quiet.html").write_text("<p>FORM 8-K</p><p>Item 8.01 Other Events</p><p>The annual meeting moves to May 3.</p><p>SIGNATURES</p>")
    with e.begin() as c:
        cid = c.execute(text("INSERT INTO company (name, sector, benchmark_symbol) VALUES ('Spin Test Co', 'Consumer Staples', 'XLP') RETURNING id")).scalar_one()
        c.execute(text("INSERT INTO ticker_history (company_id, symbol, valid_from, source) VALUES (:c, 'SPNT', '1990-01-01', 'test')"), {"c": cid})
        ids = []
        for acc, path, when in (("0000000001-26-000001", "aa/spin.html", "2026-09-15T12:09:00Z"), ("0000000001-26-000002", "aa/quiet.html", "2026-03-02T12:00:00Z")):
            d = c.execute(text("INSERT INTO source_document (source_type, publisher, accession_no, storage_path, content_type) "
                               "VALUES ('SEC_FILING', 'SEC', :a, :p, 'text/html') RETURNING id"), {"a": acc, "p": path}).scalar_one()
            ids.append(c.execute(text("INSERT INTO filing (company_id, cik, accession_no, form_type, filed_date, accepted_at, items, source_document_id) "
                                      "VALUES (:c, '9999990001', :a, '8-K', CAST(:t AS date), CAST(:t AS timestamptz), '8.01,9.01', :d) RETURNING id"),
                                 {"c": cid, "a": acc, "t": when, "d": d}).scalar_one())
        act = c.execute(text("INSERT INTO corporate_action (company_id, symbol, ex_date, action_type, value, provider) "
                             "VALUES (:c, 'SPNT', '2026-10-01', 'SPIN_OFF', 68.26, 'manual') RETURNING id"), {"c": cid}).scalar_one()
    try:
        r = ca.run(e, "keyword", documents_dir=str(docs), log=lambda _m: None)
        assert r["classified"] >= 2
        with e.connect() as c:
            rows = {row.filing_id: row for row in c.execute(text("SELECT * FROM corporate_action_candidate WHERE company_id = :c"), {"c": cid})}
        assert rows[ids[0]].kind == "SPIN_OFF_OR_DISTRIBUTION" and rows[ids[0]].reconciled_action_id == act
        assert rows[ids[1]].kind == "NONE" and rows[ids[1]].reconciled_action_id is None
        assert rows[ids[0]].review == "UNREVIEWED" and rows[ids[0]].model == ca.KEYWORD_RULE
        # a second run classifies nothing new; nothing was written to corporate_action
        assert ca.run(e, "keyword", documents_dir=str(docs), log=lambda _m: None)["classified"] == 0
        with e.connect() as c:
            assert c.execute(text("SELECT count(*) FROM corporate_action WHERE company_id = :c"), {"c": cid}).scalar_one() == 1
        assert ca.gaps(e).query("symbol == 'SPNT'").empty            # the spin-off is reconciled: no gap
    finally:   # everything this test committed, so later tests do not see a third company or the XLP benchmark
        with e.begin() as c:
            c.execute(text("DELETE FROM corporate_action_candidate WHERE company_id = :c"), {"c": cid})
            c.execute(text("DELETE FROM corporate_action WHERE company_id = :c"), {"c": cid})
            c.execute(text("DELETE FROM filing WHERE company_id = :c"), {"c": cid})
            c.execute(text("DELETE FROM source_document WHERE accession_no IN ('0000000001-26-000001', '0000000001-26-000002')"))
            c.execute(text("DELETE FROM ticker_history WHERE company_id = :c"), {"c": cid})
            c.execute(text("DELETE FROM company WHERE id = :c"), {"c": cid})
