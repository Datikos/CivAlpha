"""Company exposures derived from filings (vocabulary: civalpha.platform.events.vocabulary).

Every exposure is tied to the filing (and a passage or XBRL fact when available), becomes available at the filing's
acceptance time, and is labelled either DIRECTLY_REPORTED (a reported number, e.g. revenue by country) or ESTIMATED
(inferred from text, ratios, the company's industry, or an LLM) with a confidence level.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from .events.vocabulary import COUNTRIES, PRODUCTS, geo_member, product_for_industry
from .llm import LlmProvider, provider
from .sql import Db, db

_FLAGS = re.IGNORECASE | re.ASCII   # Java Pattern.CASE_INSENSITIVE: ASCII-only case folding and \b

# ---------------------------------------------------------------------------------------------------- derivation
_SUPPLY = re.compile(r"manufactur|assembl|supplier|suppl(y|ies)|sourc|contract manufactur|foundr|fabricat", _FLAGS)
_REVENUE = re.compile(r"revenue|net sales|customers in|demand", _FLAGS)
_SUBSTANTIAL = re.compile(r"substantially all|majority of|most of", _FLAGS)
_SIGNIFICANT = re.compile(r"significant portion|significant|material share|material portion", _FLAGS)
_SENTENCE = re.compile(r"(?<=[.;])\s+", re.ASCII)
_GEO_CONCEPTS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")


@dataclass(frozen=True)
class Candidate:
    target_type: str
    target_code: str
    channel: str
    share: Decimal | None
    basis: str
    confidence: str
    method: str
    passage_id: int | None
    fact_id: int | None
    rationale: str


def _rank(c: Candidate) -> int:
    r = 100 if c.basis == "DIRECTLY_REPORTED" else 0
    r += {"HIGH": 30, "MEDIUM": 20}.get(c.confidence, 10)
    return r + (1 if c.share is not None else 0)


def divide4(a: Decimal, b: Decimal) -> Decimal:
    """a / b rounded to 4 decimal places, half up (BigDecimal.divide(b, 4, HALF_UP))."""
    q = Fraction(a) / Fraction(b)
    sign = -1 if q < 0 else 1
    n = int(abs(q) * 10000 + Fraction(1, 2))   # floor(|q| * 10^4 + 1/2): half rounds away from zero
    return Decimal(sign * n).scaleb(-4)


def _abbreviate(s: str | None) -> str:
    if s is None:
        return ""
    return s[:300] + "…" if len(s) > 300 else s


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE.split(text)
    while len(parts) > 1 and parts[-1] == "":   # String.split drops trailing empty strings
        parts.pop()
    return parts


class ExposureService:
    def __init__(self, database: Db | None = None, llm: LlmProvider | None = None):
        self.db = database or db()
        self.llm = llm or provider()

    def derive_for_filing(self, filing_id: int) -> int:
        with self.db.transaction():
            f = self.db.one("""
                SELECT f.id, f.company_id, f.form_type, f.accepted_at, f.period_of_report, c.industry, c.name
                FROM filing f JOIN company c ON c.id = f.company_id WHERE f.id = :id""", id=filing_id)
            if f is None:
                raise LookupError(f"filing {filing_id} not found")
            company_id, form = f["company_id"], f["form_type"]
            if self.db.scalar("SELECT count(*) FROM company_exposure WHERE filing_id = :f", f=filing_id) > 0:
                return 0   # already derived (idempotent)
            cands: list[Candidate] = []
            if form.startswith("10-K"):
                cands += self.from_geographic_facts(filing_id)
                cands += self.from_leverage(filing_id, company_id)
                p = product_for_industry(f["industry"])
                if p is not None:
                    cands.append(Candidate("PRODUCT", p, "REVENUE", None, "ESTIMATED", "MEDIUM", "SECTOR_MAP", None, None,
                                           f"Company industry ({f['industry']}) sells products in this category"))
            if form.startswith("10-K") or form.startswith("10-Q") or form.startswith("8-K"):
                cands += self.from_passages(filing_id, f["name"])
            # de-duplicate by (type, code, channel): prefer directly reported, then higher confidence
            best: dict[str, Candidate] = {}
            for c in cands:
                k = f"{c.target_type}|{c.target_code}|{c.channel}"
                prev = best.get(k)
                if prev is None or _rank(c) > _rank(prev):
                    best[k] = c
            n = 0
            for c in best.values():
                version = self.db.scalar("""
                    SELECT coalesce(max(version), 0) + 1 FROM company_exposure
                    WHERE company_id = :c AND target_type = :t AND target_code = :code AND exposure_channel = :ch""",
                    c=company_id, t=c.target_type, code=c.target_code, ch=c.channel)
                self.db.execute("""
                    INSERT INTO company_exposure (company_id, target_type, target_code, exposure_channel, share, basis, confidence,
                        method, filing_id, passage_id, xbrl_fact_id, available_at, period_end, rationale, version)
                    VALUES (:c, :t, :code, :ch, :share, :basis, :conf, :m, :f, :p, :x, :at, :pe, :r, :v)""",
                    c=company_id, t=c.target_type, code=c.target_code, ch=c.channel, share=c.share, basis=c.basis,
                    conf=c.confidence, m=c.method, f=filing_id, p=c.passage_id, x=c.fact_id, at=f["accepted_at"],
                    pe=f["period_of_report"], r=c.rationale, v=version)
                n += 1
            return n

    def from_geographic_facts(self, filing_id: int) -> list[Candidate]:
        """Revenue by geography from dimensional XBRL facts: member value / consolidated total for the same period."""
        rows = self.db.all("""
            SELECT id, concept, value, period_start, period_end, dimensions->>'srt:StatementGeographicalAxis' AS member
            FROM xbrl_fact WHERE filing_id = :f
              AND concept IN ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet')
              AND (dims_key = '' OR dimensions->>'srt:StatementGeographicalAxis' IS NOT NULL)""", f=filing_id)
        passage = self._first_passage(filing_id, "GEOGRAPHIC_REVENUE")
        out = []
        for r in rows:
            member = r["member"]
            if member is None:
                continue
            total = next((t["value"] for t in rows if t["member"] is None and t["concept"] == r["concept"]
                          and t["period_end"] == r["period_end"] and t["period_start"] == r["period_start"]), None)
            if total is None or total <= 0:
                continue
            g = geo_member(member)
            if g is None:
                continue
            out.append(Candidate("COUNTRY", g.code, "REVENUE", divide4(r["value"], total), "DIRECTLY_REPORTED",
                                 "HIGH" if g.exact else "MEDIUM", "XBRL_DIMENSION", passage, r["id"],
                                 f"Revenue tagged {member} / consolidated revenue" + ("" if g.exact else " (region mapped to nearest country code)")))
        return out

    def from_leverage(self, filing_id: int, company_id: int) -> list[Candidate]:
        """Rate sensitivity proxy: long-term debt / assets reported in the filing (the ratio is reported; the sensitivity is inferred)."""
        rows = self.db.all("""
            SELECT id, concept, value FROM xbrl_fact WHERE filing_id = :f AND dims_key = '' AND period_start IS NULL
              AND concept IN ('LongTermDebtNoncurrent', 'LongTermDebt', 'Assets') ORDER BY period_end DESC""", f=filing_id)
        debt = next((r for r in rows if r["concept"].startswith("LongTermDebt")), None)
        assets = next((r for r in rows if r["concept"] == "Assets"), None)
        if debt is None or assets is None or assets["value"] <= 0:
            return []
        ratio = divide4(debt["value"], assets["value"])
        passage = self._first_passage(filing_id, "RATES")
        if passage is None:
            passage = self._first_passage(filing_id, "DEBT")
        return [Candidate("INTEREST_RATE", "US_POLICY_RATE", "FINANCING", ratio, "ESTIMATED", "MEDIUM", "XBRL_RATIO", passage, debt["id"],
                          f"Long-term debt / total assets = {ratio} (reported values); sensitivity to policy rates is inferred")]

    def from_passages(self, filing_id: int, company_name: str) -> list[Candidate]:
        """Keyword rules over extracted passages (and optional LLM hints). Always ESTIMATED."""
        ps = self.db.all("SELECT id, topic, text FROM filing_passage WHERE filing_id = :f AND topic IN ('TRADE','COSTS','RISK','GEOGRAPHIC_REVENUE')",
                         f=filing_id)
        out: list[Candidate] = []
        llm_done: set[str] = set()
        for p in ps:
            pid, text = p["id"], p["text"]
            for sentence in _split_sentences(text):
                supply = _SUPPLY.search(sentence) is not None
                revenue = _REVENUE.search(sentence) is not None
                if not supply and not revenue:
                    continue
                substantial, significant = _SUBSTANTIAL.search(sentence), _SIGNIFICANT.search(sentence)
                conf = "MEDIUM" if substantial or significant else "LOW"
                share = Decimal("0.60") if substantial else (Decimal("0.35") if significant else None)
                for code, pattern in COUNTRIES.items():
                    if code == "US":
                        continue   # domestic operations are not a trade exposure
                    if not pattern.search(sentence):
                        continue
                    out.append(Candidate("COUNTRY", code, "SUPPLY_CHAIN" if supply else "REVENUE", share, "ESTIMATED", conf,
                                         "RULE_KEYWORD", pid, None, f'Filing text: "{_abbreviate(sentence)}"'))
                for code, pattern in PRODUCTS.items():
                    if pattern.search(sentence) and supply:
                        out.append(Candidate("PRODUCT", code, "COST_INPUT", None, "ESTIMATED", "LOW", "RULE_KEYWORD", pid, None,
                                             f'Filing text: "{_abbreviate(sentence)}"'))
            if self.llm.enabled and p["topic"] == "TRADE" and text not in llm_done:
                llm_done.add(text)
                for h in self.llm.extract_exposures(company_name, text):
                    out.append(Candidate(h.target_type, h.target_code, h.channel, None, "ESTIMATED", h.confidence, "LLM", pid, None,
                                         f"{self.llm.name}: {_abbreviate(h.rationale)}"))
        return out

    def _first_passage(self, filing_id: int, topic: str) -> int | None:
        return self.db.scalar("SELECT id FROM filing_passage WHERE filing_id = :f AND topic = :t ORDER BY id LIMIT 1", f=filing_id, t=topic)
