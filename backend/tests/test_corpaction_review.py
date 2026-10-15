"""ADR-0003 phase 3: reviewing corporate-action candidates. Gaps listed with their context, reject, confirm and link to an
action the feed recorded, enter a missing spin-off (validated, reconciling the company's open candidates), audit rows,
and the owner-only route."""
from datetime import date

import pytest

from civalpha.platform import auth
from civalpha.platform.corpaction_review import CorporateActionReview
from civalpha.platform.errors import BadRequest
from civalpha.platform.tickers import TickerResolver


@pytest.fixture
def setup(tdb, universe):
    cid = TickerResolver(tdb).company_ever("META")
    for d, close in (("2026-05-01", 100), ("2026-05-04", 100), ("2026-05-05", 70), ("2026-05-06", 71)):
        tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'META', :d, :x, 't')",
                    c=cid, d=date.fromisoformat(d), x=close)

    def candidate(acc: str, accepted: str, kind: str = "SPIN_OFF_OR_DISTRIBUTION") -> int:
        doc = tdb.scalar("INSERT INTO source_document (source_type, publisher, accession_no, storage_path, content_type) "
                         "VALUES ('SEC_FILING', 'SEC', :a, 'x.html', 'text/html') RETURNING id", a=acc)
        fid = tdb.scalar("INSERT INTO filing (company_id, cik, accession_no, form_type, filed_date, accepted_at, items, source_document_id) "
                         "VALUES (:c, '0001326801', :a, '8-K', CAST(:t AS date), CAST(:t AS timestamptz), '8.01', :d) RETURNING id",
                         c=cid, a=acc, t=accepted, d=doc)
        return tdb.scalar("INSERT INTO corporate_action_candidate (filing_id, company_id, accepted_at, method, model, kind, evidence) "
                          "VALUES (:f, :c, CAST(:t AS timestamptz), 'KEYWORD', 'keyword-1', :k, 'a pro rata distribution of SpinCo') RETURNING id",
                          f=fid, c=cid, t=accepted, k=kind)

    return {"cid": cid, "candidate": candidate, "svc": CorporateActionReview(tdb)}


def test_gaps_carry_the_price_check_and_the_feeds_nearby_actions(setup, tdb):
    s = setup
    a = s["candidate"]("0000000009-26-000001", "2026-04-20T12:00:00Z")
    s["candidate"]("0000000009-26-000002", "2026-04-21T12:00:00Z", kind="NONE")
    tdb.execute("INSERT INTO corporate_action (company_id, symbol, ex_date, action_type, value, provider) "
                "VALUES (:c, 'META', '2026-05-06', 'CASH_DIVIDEND', 0.5, 't')", c=s["cid"])          # an ordinary dividend
    out = s["svc"].candidates("gap")
    ids = [c["id"] for c in out["candidates"]]
    assert ids == [a] and out["counts"]["gap"] == 1 and out["counts"]["read"] == 2
    row = out["candidates"][0]
    assert row["largestMove"] == {"date": "2026-05-05", "move": pytest.approx(-0.30)}      # the break the feed left unexplained
    assert row["nearbyActions"] == []                                                       # 0.5 is 0.7% of the close: not a distribution


def test_reject_closes_the_gap_and_is_audited(setup, tdb):
    s = setup
    a = s["candidate"]("0000000009-26-000003", "2026-04-20T12:00:00Z")
    assert s["svc"].review(auth.LOCAL, [a], "REJECTED", "restates a past split") == 1
    assert s["svc"].candidates("gap")["candidates"] == []
    row = s["svc"].candidates("rejected")["candidates"][0]
    assert row["review"] == "REJECTED" and row["reviewNote"] == "restates a past split" and row["reviewedAt"]
    assert tdb.scalar("SELECT action FROM audit_event WHERE target_type = 'corporate_action_candidate' AND target_id = :i", i=str(a)) == "CANDIDATE_REVIEWED"
    with pytest.raises(BadRequest):
        s["svc"].review(auth.LOCAL, [a], "MAYBE")


def test_confirm_and_link_to_the_feeds_action(setup, tdb):
    s = setup
    a = s["candidate"]("0000000009-26-000004", "2026-04-20T12:00:00Z")
    div = tdb.scalar("INSERT INTO corporate_action (company_id, symbol, ex_date, action_type, value, provider) "
                     "VALUES (:c, 'META', '2026-05-05', 'CASH_DIVIDEND', 30, 't') RETURNING id", c=s["cid"])
    near = s["svc"].candidates("gap")["candidates"][0]["nearbyActions"]
    assert [n["id"] for n in near] == [div] and near[0]["shareOfClose"] == pytest.approx(0.30)
    s["svc"].review(auth.LOCAL, [a], "CONFIRMED", "distribution paid as a dividend by the feed", link_action_id=div)
    assert s["svc"].candidates("gap")["candidates"] == []
    with pytest.raises(BadRequest):            # linking needs one candidate
        s["svc"].review(auth.LOCAL, [a, a + 1], "CONFIRMED", link_action_id=div)


def test_confirm_only_keeps_the_gap_open(setup):
    s = setup
    a = s["candidate"]("0000000009-26-000005", "2026-04-20T12:00:00Z")
    s["svc"].review(auth.LOCAL, [a], "CONFIRMED", "announced, not yet effective")
    assert [c["id"] for c in s["svc"].candidates("gap")["candidates"]] == [a]


def test_enter_a_missing_spin_off_reconciles_the_company(setup, tdb):
    s = setup
    a = s["candidate"]("0000000009-26-000006", "2026-04-20T12:00:00Z")
    b = s["candidate"]("0000000009-26-000007", "2026-04-28T12:00:00Z")
    with pytest.raises(BadRequest, match="less than the close"):
        s["svc"].add_spin_off(auth.LOCAL, a, date(2026, 5, 5), 150)
    with pytest.raises(BadRequest, match="must have happened"):
        s["svc"].add_spin_off(auth.LOCAL, a, date(2099, 1, 2), 10)
    r = s["svc"].add_spin_off(auth.LOCAL, a, date(2026, 5, 5), 30, "SpinCo 1:1 at its first close")
    act = tdb.one("SELECT action_type, value, provider FROM corporate_action WHERE id = :i", i=r["actionId"])
    assert (act["action_type"], float(act["value"]), act["provider"]) == ("SPIN_OFF", 30.0, "manual")
    rows = {x["id"]: x for x in s["svc"].candidates("all")["candidates"]}
    assert rows[a]["review"] == "CONFIRMED" and rows[a]["reconciledActionId"] == r["actionId"]
    assert rows[b]["reconciledActionId"] == r["actionId"] and rows[b]["review"] == "UNREVIEWED"     # same event, other 8-K
    with pytest.raises(BadRequest, match="already recorded"):
        s["svc"].add_spin_off(auth.LOCAL, b, date(2026, 5, 5), 30)


def test_routes_are_owner_only():
    from civalpha.platform.app import required_role

    for m in ("GET", "POST"):
        assert required_role("/api/admin/corporate-actions/candidates", m, accounts=True) == "OWNER"
        assert required_role("/api/admin/corporate-actions/candidates", m, accounts=False) == "OWNER"
