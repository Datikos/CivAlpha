"""ADR-0003 phase 3: the owner reviews the corporate-action candidates the 8-K detector stored.

A flagged candidate with no reconciling action is a feed gap. Each one is resolved in one of four ways:
  * REJECTED: the filing only mentions a split or spin-off (a restated past split, merger terms);
  * CONFIRMED and linked to an action the feed already recorded (a split factor, or a cash dividend worth the shares
    distributed): the prices are right, the gap closes;
  * CONFIRMED and a missing distribution entered as a SPIN_OFF action (ex-date, value per share): the total-return index
    applies it like a dividend from the next pipeline run, the gap closes;
  * CONFIRMED only: a real event still ahead; the gap stays open, so holders keep seeing "Check the data".
Every review is written to the audit trail. Nothing here changes a price bar.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .. import corpactions as ca
from . import auth
from .auth import Caller
from .errors import BadRequest, NotFound
from .rows import camel, value
from .sql import Db, db

REVIEWS = ("UNREVIEWED", "CONFIRMED", "REJECTED")
STATUSES = ("gap", "unreviewed", "confirmed", "rejected", "flagged", "all")
PORTFOLIO_DAYS = 120                # the portfolio rule's window (portfolio.CANDIDATE_DAYS)
NEARBY_BEFORE, NEARBY_AFTER = 60, 130     # an 8-K often reports a distribution weeks after its ex-date (FDX, 2026)
MAX_IDS = 500


def _flagged_sql() -> str:
    return "(c.kind <> 'NONE' AND (c.method = 'KEYWORD' OR c.probability >= 0.5))"


class CorporateActionReview:
    def __init__(self, database: Db | None = None):
        self.db = database or db()

    # ---------------------------------------------------------------- reading
    def candidates(self, status: str = "gap", symbol: str | None = None, limit: int = 500) -> dict:
        if status not in STATUSES:
            raise BadRequest(f"status: one of {', '.join(STATUSES)}")
        where = {
            "gap": f"{_flagged_sql()} AND c.reconciled_action_id IS NULL AND c.review <> 'REJECTED'",
            "unreviewed": f"{_flagged_sql()} AND c.review = 'UNREVIEWED'",
            "confirmed": "c.review = 'CONFIRMED'",
            "rejected": "c.review = 'REJECTED'",
            "flagged": _flagged_sql(),
            "all": "TRUE",
        }[status]
        rows = self.db.all(f"""
            SELECT c.id, c.filing_id, c.company_id, co.name,
                   (SELECT th.symbol FROM ticker_history th WHERE th.company_id = c.company_id ORDER BY th.valid_from DESC LIMIT 1) AS symbol,
                   c.accepted_at, c.method, c.model, c.kind, c.probability, c.evidence, c.reconciled_action_id, c.review,
                   c.reviewed_at, u.username AS reviewed_by, c.review_note, f.accession_no, f.items, sd.url AS filing_url
            FROM corporate_action_candidate c
            JOIN company co ON co.id = c.company_id
            JOIN filing f ON f.id = c.filing_id
            LEFT JOIN source_document sd ON sd.id = f.source_document_id
            LEFT JOIN app_user u ON u.id = c.reviewed_by
            WHERE {where}
              AND (CAST(:sym AS text) IS NULL OR c.company_id IN (SELECT company_id FROM ticker_history WHERE upper(symbol) = upper(:sym)))
            ORDER BY c.accepted_at DESC, c.id DESC LIMIT :n""", sym=(symbol or None), n=max(1, min(limit, 2000)))
        items = [camel(r) for r in rows]
        self._context(items)
        counts = self.db.one(f"""
            SELECT count(*) FILTER (WHERE {_flagged_sql()} AND c.reconciled_action_id IS NULL AND c.review <> 'REJECTED') AS gap,
                   count(*) FILTER (WHERE {_flagged_sql()} AND c.reconciled_action_id IS NULL AND c.review <> 'REJECTED'
                                    AND c.accepted_at > now() - make_interval(days => :d)) AS gap_recent,
                   count(*) FILTER (WHERE {_flagged_sql()} AND c.review = 'UNREVIEWED') AS unreviewed,
                   count(*) FILTER (WHERE c.review = 'CONFIRMED') AS confirmed,
                   count(*) FILTER (WHERE c.review = 'REJECTED') AS rejected,
                   count(*) FILTER (WHERE {_flagged_sql()}) AS flagged,
                   count(*) AS read
            FROM corporate_action_candidate c""", d=PORTFOLIO_DAYS)
        return {"status": status, "counts": camel(counts), "portfolioDays": PORTFOLIO_DAYS, "candidates": items,
                "rule": ca.KEYWORD_RULE,
                "note": "A gap is a flagged 8-K with no recorded split or spin-off from 5 days before its acceptance to 120 days "
                        "after. A gap younger than 120 days puts holders of the stock on \"Check the data\" (My portfolio)."}

    def _context(self, items: list[dict]) -> None:
        """Per candidate: the actions the feed recorded around it, and the largest one-day raw-close move without a recorded
        action in its window, so the reviewer can tell a mention from an event and an adjusted event from a broken price."""
        cids = sorted({i["companyId"] for i in items})
        if not cids:
            return
        acts = pd.DataFrame(self.db.all("""SELECT id, company_id, ex_date, action_type, value::float8 AS value, provider
                                           FROM corporate_action WHERE company_id = ANY(:c)""", c=cids),
                            columns=["id", "company_id", "ex_date", "action_type", "value", "provider"])
        px = pd.DataFrame(self.db.all("""SELECT company_id, trade_date, close::float8 AS close FROM price_bar
                                         WHERE company_id = ANY(:c) ORDER BY company_id, trade_date""", c=cids),
                          columns=["company_id", "trade_date", "close"])
        if len(px):
            px["ret"] = px.groupby("company_id")["close"].pct_change()
        today = pd.Timestamp(date.today())
        for it in items:
            t = pd.Timestamp(it["acceptedAt"]).tz_localize(None).normalize()
            lo, hi = t - pd.Timedelta(days=NEARBY_BEFORE), t + pd.Timedelta(days=NEARBY_AFTER)
            a = acts[(acts.company_id == it["companyId"]) & (pd.to_datetime(acts.ex_date) >= lo) & (pd.to_datetime(acts.ex_date) <= hi)]
            # cash dividends below 2% of the close are ordinary dividends, not distributions: left out to keep the list short
            near = []
            for r in a.sort_values("ex_date").itertuples():
                close = self._close_before(px, it["companyId"], pd.Timestamp(r.ex_date))
                share = (r.value / close) if close and r.action_type == "CASH_DIVIDEND" else None
                if r.action_type == "CASH_DIVIDEND" and (share is None or share < 0.02):
                    continue
                near.append({"id": int(r.id), "exDate": value(r.ex_date), "type": r.action_type, "value": r.value,
                             "provider": r.provider, "shareOfClose": share})
            it["nearbyActions"] = near
            p = px[(px.company_id == it["companyId"]) & (pd.to_datetime(px.trade_date) >= t - pd.Timedelta(days=5))
                   & (pd.to_datetime(px.trade_date) <= t + pd.Timedelta(days=ca.RECONCILE_AFTER_DAYS))]
            adays = set(pd.to_datetime(acts[acts.company_id == it["companyId"]].ex_date))
            p = p[~pd.to_datetime(p.trade_date).isin(adays) & p["ret"].notna()] if len(p) else p
            if len(p):
                j = p["ret"].abs().idxmax()
                it["largestMove"] = {"date": value(p.loc[j, "trade_date"]), "move": float(p.loc[j, "ret"])}
            else:
                it["largestMove"] = None
            it["inPortfolioWindow"] = bool(t >= today - pd.Timedelta(days=PORTFOLIO_DAYS))
            it["evidence"] = (it.get("evidence") or "")[:400]

    @staticmethod
    def _close_before(px: pd.DataFrame, cid: int, day: pd.Timestamp) -> float | None:
        p = px[(px.company_id == cid) & (pd.to_datetime(px.trade_date) < day)]
        return float(p["close"].iloc[-1]) if len(p) else None

    # ---------------------------------------------------------------- reviewing
    def review(self, caller: Caller, ids: list[int], review: str, note: str | None = None, link_action_id: int | None = None) -> int:
        """Sets the review of one or more candidates. `link_action_id` (one candidate only, CONFIRMED) reconciles it with an
        action the feed recorded: the prices are right and the gap closes."""
        if review not in REVIEWS:
            raise BadRequest(f"review: one of {', '.join(REVIEWS)}")
        ids = sorted({int(i) for i in ids or []})
        if not ids or len(ids) > MAX_IDS:
            raise BadRequest(f"ids: 1 to {MAX_IDS} candidate ids")
        if note is not None and len(note) > 500:
            raise BadRequest("note: at most 500 characters")
        if link_action_id is not None and (len(ids) != 1 or review != "CONFIRMED"):
            raise BadRequest("linkActionId: one candidate, confirmed")
        with self.db.transaction():
            rows = self.db.all("SELECT id, company_id, review, reconciled_action_id, kind FROM corporate_action_candidate WHERE id = ANY(:i)", i=ids)
            if len(rows) != len(ids):
                raise NotFound("unknown candidate id")
            if link_action_id is not None:
                ok = self.db.scalar("SELECT 1 FROM corporate_action WHERE id = :a AND company_id = :c", a=link_action_id, c=rows[0]["company_id"])
                if not ok:
                    raise BadRequest("linkActionId: an action of the same company")
            self.db.execute("""
                UPDATE corporate_action_candidate SET review = :r, review_note = :n, reviewed_at = now(), reviewed_by = :u,
                       reconciled_action_id = COALESCE(:a, reconciled_action_id)
                WHERE id = ANY(:i)""", r=review, n=(note or None), u=caller.user_id, a=link_action_id, i=ids)
            for r in rows:
                auth.record(self.db, caller, "CANDIDATE_REVIEWED", "corporate_action_candidate", r["id"],
                            before={"review": r["review"], "reconciledActionId": r["reconciled_action_id"]},
                            after={"review": review, "note": note, "reconciledActionId": link_action_id or r["reconciled_action_id"]})
        return len(ids)

    def add_spin_off(self, caller: Caller, candidate_id: int, ex_date: date, value_per_share: float, note: str | None = None) -> dict:
        """Records a distribution the feed missed as a SPIN_OFF action (value = market value per parent share of what was
        distributed, at the ex-date close), confirms the candidate and reconciles every open candidate of the company whose
        window covers the ex-date. The total-return index applies it like a cash dividend from the next pipeline run."""
        c = self.db.one("SELECT id, company_id, accepted_at FROM corporate_action_candidate WHERE id = :i", i=candidate_id)
        if c is None:
            raise NotFound(f"candidate {candidate_id} not found")
        if ex_date > date.today():
            raise BadRequest("exDate: the distribution must have happened; confirm only, and enter it after the ex-date")
        try:
            v = float(value_per_share)
        except (TypeError, ValueError):
            raise BadRequest("valuePerShare: a number") from None
        prev = self.db.scalar("""SELECT close::float8 FROM price_bar WHERE company_id = :c AND trade_date < :d
                                 ORDER BY trade_date DESC LIMIT 1""", c=c["company_id"], d=ex_date)
        if not v > 0 or (prev is not None and v >= prev):
            raise BadRequest(f"valuePerShare: more than 0 and less than the close before the ex-date ({prev})")
        sym = self.db.scalar("SELECT symbol FROM ticker_history WHERE company_id = :c ORDER BY valid_from DESC LIMIT 1", c=c["company_id"])
        with self.db.transaction():
            if self.db.scalar("SELECT 1 FROM corporate_action WHERE symbol = :s AND ex_date = :d AND action_type = 'SPIN_OFF'", s=sym, d=ex_date):
                raise BadRequest(f"a SPIN_OFF of {sym} on {ex_date} is already recorded")
            aid = self.db.scalar("""INSERT INTO corporate_action (company_id, symbol, ex_date, action_type, value, provider)
                                    VALUES (:c, :s, :d, 'SPIN_OFF', :v, 'manual') RETURNING id""", c=c["company_id"], s=sym, d=ex_date, v=v)
            self.db.execute("""
                UPDATE corporate_action_candidate SET reconciled_action_id = :a
                WHERE company_id = :c AND reconciled_action_id IS NULL
                  AND :d BETWEEN (accepted_at AT TIME ZONE 'UTC')::date - :b AND (accepted_at AT TIME ZONE 'UTC')::date + :f""",
                a=aid, c=c["company_id"], d=ex_date, b=ca.RECONCILE_BEFORE_DAYS, f=ca.RECONCILE_AFTER_DAYS)
            self.db.execute("""UPDATE corporate_action_candidate SET review = 'CONFIRMED', review_note = :n, reviewed_at = now(),
                               reviewed_by = :u, reconciled_action_id = :a WHERE id = :i""", n=note or None, u=caller.user_id, a=aid, i=candidate_id)
            auth.record(self.db, caller, "SPIN_OFF_ENTERED", "corporate_action", aid,
                        after={"symbol": sym, "exDate": ex_date.isoformat(), "value": v, "candidateId": candidate_id, "note": note})
        return {"actionId": aid, "symbol": sym, "exDate": ex_date.isoformat(), "value": v,
                "note": "Recorded. Features and forecasts use it from the next pipeline run."}
