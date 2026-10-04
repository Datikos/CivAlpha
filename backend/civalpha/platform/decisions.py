"""The AI strategy's daily decisions: stored append-only (a database trigger rejects UPDATE/DELETE) and, when a
language model is configured, explained in plain language for ENTER and EXIT actions. The explanation is written
afterwards, in its own table, and never changes the decision."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..strategies import service as strategies
from .jobs import Log
from .llm import LlmProvider, provider
from .sql import Db, db, engine, jsonb

EXPLAINED = {"ENTER", "EXIT"}


@dataclass
class DecideResult:
    created: int = 0
    existing: int = 0
    explained: int = 0


class DecisionService:
    def __init__(self, database: Db | None = None, llm: LlmProvider | None = None, decide_fn=None):
        self.db = database or db()
        self.llm = llm or provider()
        self.decide_fn = decide_fn or (lambda as_of: strategies.decide(engine(), as_of)["decisions"])

    def decide(self, as_of: date | None, log: Log) -> DecideResult:
        return self.persist_all(self.decide_fn(as_of.isoformat() if as_of else None), log)

    def persist_all(self, decisions: list[dict], log: Log) -> DecideResult:
        r = DecideResult()
        for d in decisions:
            did = self.persist(d)
            if did is None:
                r.existing += 1
                continue
            r.created += 1
            action = d["action"]
            if action not in ("HOLD", "STAY_OUT"):
                log(f"{action} {d['symbol']} (p = {float(d['probability']):.2f})")
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

    def _explain(self, decision_id: int, d: dict) -> bool:
        facts = {k: d.get(k) for k in ("action", "probability", "entryP", "exitP", "rank", "maxPositions", "factors", "ruleVotes")}
        facts["horizonTradingDays"] = (d.get("model") or {}).get("horizon")
        text = self.llm.explain_decision(d.get("name"), d.get("symbol"), facts)
        if not text or not text.strip():
            return False
        self.db.execute("INSERT INTO decision_explanation (decision_id, text, model) VALUES (:id, :t, :m) ON CONFLICT DO NOTHING",
                        id=decision_id, t=text, m=self.llm.name)
        return True
