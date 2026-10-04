"""Ticker symbol <-> company resolution over time-ranged ticker history [valid_from, valid_to).

"FB" on 2021-01-04 and "META" on 2023-01-03 resolve to the same company; a symbol later reused by another issuer
resolves to the right one for each date.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .sql import Db, db


@dataclass(frozen=True)
class Span:
    company_id: int
    symbol: str
    valid_from: date
    valid_to: date | None

    def covers(self, d: date) -> bool:
        return self.valid_from <= d and (self.valid_to is None or self.valid_to > d)


def pad_cik(cik: str) -> str:
    digits = re.sub(r"\D", "", str(cik))
    return "0" * max(0, 10 - len(digits)) + digits


def resolve(spans_for_symbol: list[Span], d: date) -> int | None:
    """In-memory equivalent of company_at for bulk imports."""
    c = [s for s in spans_for_symbol if s.covers(d)]
    return max(c, key=lambda s: s.valid_from).company_id if c else None


class TickerResolver:
    def __init__(self, database: Db | None = None):
        self.db = database or db()

    def company_at(self, symbol: str, d: date) -> int | None:
        return self.db.scalar("""
            SELECT company_id FROM ticker_history
            WHERE upper(symbol) = upper(:s) AND valid_from <= :d AND (valid_to IS NULL OR valid_to > :d)
            ORDER BY valid_from DESC LIMIT 1""", s=symbol, d=d)

    def company_ever(self, symbol: str) -> int | None:
        """Latest company that ever used the symbol (for URL lookups such as /companies/FB)."""
        return self.db.scalar("SELECT company_id FROM ticker_history WHERE upper(symbol) = upper(:s) ORDER BY valid_from DESC LIMIT 1",
                              s=symbol)

    def symbol_at(self, company_id: int, d: date) -> str | None:
        return self.db.scalar("""
            SELECT symbol FROM ticker_history
            WHERE company_id = :c AND valid_from <= :d AND (valid_to IS NULL OR valid_to > :d)
            ORDER BY valid_from DESC LIMIT 1""", c=company_id, d=d)

    def current_symbol(self, company_id: int) -> str:
        s = self.db.scalar("SELECT symbol FROM ticker_history WHERE company_id = :c ORDER BY valid_from DESC LIMIT 1", c=company_id)
        if s is None:
            raise LookupError(f"company {company_id} has no ticker")
        return s

    def company_by_cik(self, cik: str) -> int | None:
        return self.db.scalar("SELECT company_id FROM cik_mapping WHERE cik = :c AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
                              c=pad_cik(cik))

    def cik_of(self, company_id: int) -> str | None:
        return self.db.scalar("SELECT cik FROM cik_mapping WHERE company_id = :c AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
                              c=company_id)

    def all_spans(self) -> list[Span]:
        return [Span(r["company_id"], r["symbol"], r["valid_from"], r["valid_to"])
                for r in self.db.all("SELECT company_id, symbol, valid_from, valid_to FROM ticker_history")]
