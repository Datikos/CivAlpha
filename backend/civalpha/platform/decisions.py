"""The AI strategy's daily decisions: stored append-only (a database trigger rejects UPDATE/DELETE) and, when a
language model is configured, explained in plain language for ENTER and EXIT actions. The explanation is written
afterwards, in its own table, and never changes the decision.

The language model can also review the final candidates (ENTER actions) as a second, logged layer: it sees a brief of
what the platform knows about the stock (the decision's own inputs, recent prices and corporate actions, the latest
results announcements, insider activity, key filed facts) and returns a stance (AGREE / CAUTION / DISAGREE) with a
rationale and flags. CIVALPHA_LLM_REVIEW selects how it is used:
  * off       no review;
  * advisory  (default) the review is stored next to the decision and shown, the decision is unchanged;
  * veto      a DISAGREE turns the ENTER into STAY_OUT before it is stored (weight 0); the review row says it did.
The brief is stored with the review so every opinion can be audited and, once outcomes resolve, scored.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, timedelta

from ..strategies import service as strategies
from .jobs import Log
from .llm import DecisionReview, LlmProvider, provider
from .settings import settings
from .sql import Db, db, engine, jsonb

EXPLAINED = {"ENTER", "EXIT"}
REVIEWED = {"ENTER"}
BRIEF_PRICE_DAYS = 45       # calendar days of prices in the brief (about two months of sessions)
LARGE_MOVE = 0.30           # a one-day close-to-close move this big with no recorded action is flagged for the reviewer


@dataclass
class DecideResult:
    created: int = 0
    existing: int = 0
    explained: int = 0
    reviewed: int = 0
    vetoed: int = 0


def decision_brief(d: dict) -> dict:
    """What the reviewer is shown for one decision: the decision's own inputs plus the platform's record of the stock.
    Built from the same readers the API serves, so the brief contains nothing a user could not see on the pages."""
    from .api import read   # imported here: the API package imports settings and services at module load

    symbol = d["symbol"]
    as_of = date.fromisoformat(d["asOfDate"])
    model = d.get("model") or {}
    brief = {
        "asOfDate": d["asOfDate"], "symbol": symbol, "name": d.get("name"), "horizonTradingDays": model.get("horizon"),
        "decision": {**{k: d.get(k) for k in ("action", "probability", "rank", "entryP", "exitP", "weight", "maxPositions")},
                     "book": model.get("book"), "factors": d.get("factors"),
                     "rulesHolding": sorted(k for k, v in (d.get("ruleVotes") or {}).items() if v),
                     "rulesNotHolding": sorted(k for k, v in (d.get("ruleVotes") or {}).items() if not v)},
        "platformEvidence": ("The model's out-of-sample AUC is about 0.50 and no single input is informative after multiple-testing "
                             "correction; the setup playbook grades 'stock drops on results day' as NOISE and supports only "
                             "'oversold pullback in an uptrend' and 'results-day jump'. 21-day volatility above 150% annualized "
                             "usually means a broken price series, not a market event."),
    }
    try:
        px = read.company_prices(symbol, from_=(as_of - timedelta(days=BRIEF_PRICE_DAYS)).isoformat())
        bars = [b for b in px.get("bars", []) if b.get("close") is not None]
        moves = [(bars[i]["date"], float(bars[i]["close"]) / float(bars[i - 1]["close"]) - 1.0) for i in range(1, len(bars))
                 if float(bars[i - 1]["close"]) > 0]
        big = max(moves, key=lambda m: abs(m[1]), default=None)
        first, last = (bars[0], bars[-1]) if bars else (None, None)
        brief["prices"] = {
            "lastDate": last and last["date"], "lastClose": last and float(last["close"]),
            "returnSince": first and first["date"], "stockReturn": (float(last["close"]) / float(first["close"]) - 1.0) if bars and float(first["close"]) > 0 else None,
            "benchmarkReturn": (float(last["benchmarkClose"]) / float(first["benchmarkClose"]) - 1.0)
            if bars and first.get("benchmarkClose") and last.get("benchmarkClose") else None,
            "largestDailyMove": big and {"date": big[0], "pct": big[1], "largeWithNoRecordedAction": abs(big[1]) >= LARGE_MOVE and not px.get("corporateActions")},
            "corporateActions": px.get("corporateActions", []),
        }
    except Exception as e:  # noqa: BLE001 - a thin brief is better than no review
        brief["prices"] = {"unavailable": str(e)}
    try:
        e = read.company_earnings(symbol, limit=3)
        brief["earnings"] = {"nextEstimate": e.get("nextEstimate"),
                             "recent": [{k: a.get(k) for k in ("sessionDate", "reaction", "guidanceTone")} for a in e.get("announcements", [])]}
    except Exception as ex:  # noqa: BLE001
        brief["earnings"] = {"unavailable": str(ex)}
    try:
        ins = read.company_insiders(symbol, limit=1)
        brief["insiders"] = {k: ins.get("windows", {}).get(k) for k in ("21d", "63d")}
    except Exception as ex:  # noqa: BLE001
        brief["insiders"] = {"unavailable": str(ex)}
    try:
        c = read.company(symbol)
        brief["keyFacts"] = (c.get("keyFacts") or [])[:12]
        brief["sector"] = c.get("sector")
    except Exception as ex:  # noqa: BLE001
        brief["keyFacts"] = {"unavailable": str(ex)}
    return brief


def vetoed(d: dict) -> dict:
    """The decision as stored after a veto: no position, the slot left empty, the book marked so the page can say why."""
    out = copy.deepcopy(d)
    out["action"] = "STAY_OUT"
    out["weight"] = 0.0
    model = out.setdefault("model", {})
    book = model.setdefault("book", {})
    book.update({"slot": 0.0, "vetoed": True})
    if "sizing" in model:
        model["sizing"]["sizedWeight"] = 0.0
    return out


class DecisionService:
    def __init__(self, database: Db | None = None, llm: LlmProvider | None = None, decide_fn=None, brief_fn=None,
                 review_mode: str | None = None):
        self.db = database or db()
        self.llm = llm or provider()
        self.decide_fn = decide_fn or (lambda as_of: strategies.decide(engine(), as_of)["decisions"])
        self.brief_fn = brief_fn or decision_brief
        self.review_mode = review_mode or settings().llm.review_mode

    def decide(self, as_of: date | None, log: Log) -> DecideResult:
        return self.persist_all(self.decide_fn(as_of.isoformat() if as_of else None), log)

    def persist_all(self, decisions: list[dict], log: Log) -> DecideResult:
        r = DecideResult()
        for d in decisions:
            review = brief = None
            if self._reviewable(d) and not self._exists(d):
                review, brief = self._review(d)
                if review is not None and self.review_mode == "veto" and review.stance == "DISAGREE":
                    log(f"VETO {d['symbol']} (p = {float(d['probability']):.2f}): {review.rationale}")
                    d = vetoed(d)
                    r.vetoed += 1
            did = self.persist(d)
            if did is None:
                r.existing += 1
                continue
            r.created += 1
            action = d["action"]
            if action not in ("HOLD", "STAY_OUT"):
                log(f"{action} {d['symbol']} (p = {float(d['probability']):.2f})")
            if review is not None and self._store_review(did, review, brief, bool((d.get("model") or {}).get("book", {}).get("vetoed"))):
                r.reviewed += 1
                log(f"REVIEW {d['symbol']}: {review.stance} ({review.confidence.lower()} confidence){', ' + ', '.join(review.flags) if review.flags else ''}")
            if self.llm.enabled and action in EXPLAINED and self._explain(did, d):
                r.explained += 1
        return r

    def persist(self, d: dict) -> int | None:
        """Inserts one decision; None if this (company, date, strategy) was already decided."""
        return self.db.scalar("""
            INSERT INTO strategy_decision (company_id, as_of_date, strategy_key, action, probability, entry_p, exit_p, weight, rank,
                                           factors, rule_votes, model)
            VALUES (:c, :d, :k, :a, :p, :ep, :xp, :w, :r, CAST(:f AS jsonb), CAST(:v AS jsonb), CAST(:m AS jsonb))
            ON CONFLICT (company_id, as_of_date, strategy_key) DO NOTHING
            RETURNING id""",
            c=int(d["companyId"]), d=date.fromisoformat(d["asOfDate"]), k=d["strategyKey"], a=d["action"],
            p=float(d["probability"]), ep=float(d["entryP"]), xp=float(d["exitP"]), w=float(d["weight"]), r=int(d["rank"]),
            f=jsonb(d.get("factors")), v=jsonb(d.get("ruleVotes")), m=jsonb(d.get("model")))

    def _reviewable(self, d: dict) -> bool:
        return self.llm.enabled and self.review_mode != "off" and d["action"] in REVIEWED

    def _exists(self, d: dict) -> bool:
        return self.db.scalar("SELECT 1 FROM strategy_decision WHERE company_id = :c AND as_of_date = :d AND strategy_key = :k",
                              c=int(d["companyId"]), d=date.fromisoformat(d["asOfDate"]), k=d["strategyKey"]) is not None

    def _review(self, d: dict) -> tuple[DecisionReview | None, dict | None]:
        try:
            brief = self.brief_fn(d)
        except Exception:  # noqa: BLE001 - no brief, no review; the decision is stored regardless
            return None, None
        return self.llm.review_decision(d.get("name"), d.get("symbol"), brief), brief

    def _store_review(self, decision_id: int, review: DecisionReview, brief: dict | None, veto: bool) -> bool:
        return self.db.execute("""
            INSERT INTO decision_review (decision_id, stance, confidence, rationale, flags, brief, veto, model)
            VALUES (:id, :s, :c, :r, CAST(:f AS jsonb), CAST(:b AS jsonb), :v, :m) ON CONFLICT DO NOTHING""",
            id=decision_id, s=review.stance, c=review.confidence, r=review.rationale, f=jsonb(list(review.flags)),
            b=jsonb(brief or {}), v=veto, m=self.llm.name) > 0

    def _explain(self, decision_id: int, d: dict) -> bool:
        facts = {k: d.get(k) for k in ("action", "probability", "entryP", "exitP", "rank", "maxPositions", "factors", "ruleVotes")}
        facts["horizonTradingDays"] = (d.get("model") or {}).get("horizon")
        text = self.llm.explain_decision(d.get("name"), d.get("symbol"), facts)
        if not text or not text.strip():
            return False
        self.db.execute("INSERT INTO decision_explanation (decision_id, text, model) VALUES (:id, :t, :m) ON CONFLICT DO NOTHING",
                        id=decision_id, t=text, m=self.llm.name)
        return True
