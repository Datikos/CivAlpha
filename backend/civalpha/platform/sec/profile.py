"""Builds a company profile for a ticker from SEC EDGAR, so a new universe member needs only its symbol.

Two sources, both public: company_tickers.json gives the CIK and registrant name; the submissions JSON gives the
exchange, SIC code and former names. The SIC code is turned into a suggested sector, benchmark ETF and industry key
(see `sectors.suggest`), which the Universe page shows for review before the company is added.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .. import sectors
from ..sql import Db, db
from ..tickers import TickerResolver
from .client import SecClient, SecClientFactory, submissions_url
from .parsers import parse_submissions
from .ticker_lookup import SecTickerLookup


@dataclass
class Profile:
    symbol: str
    cik: str | None = None
    name: str | None = None
    source: str | None = None
    exchange: str | None = None
    sic: str | None = None
    sic_description: str | None = None
    former_names: list[str] = field(default_factory=list)
    sector: str | None = None
    benchmark_symbol: str | None = None
    industry: str | None = None
    sector_confidence: str | None = None
    sector_note: str | None = None
    # candidate sectors for an ambiguous SIC code, as {"sector", "benchmarkSymbol"}; empty when the code is clear
    sector_alternatives: list[dict] = field(default_factory=list)
    existing: dict | None = None
    warnings: list[str] = field(default_factory=list)


class CompanyProfiler:
    def __init__(self, database: Db | None = None, lookup: SecTickerLookup | None = None,
                 factory: SecClientFactory | None = None):
        self.db = database or db()
        self.lookup = lookup or SecTickerLookup(factory)
        self.factory = factory or SecClientFactory()

    def profile(self, symbol: str) -> Profile:
        """Never raises for a missing or unreachable source: what could not be found is left None and explained in `warnings`."""
        sym = symbol.upper().strip()
        p = Profile(sym)
        try:
            m = self.lookup.lookup(sym)
        except Exception as e:  # noqa: BLE001
            m = None
            p.warnings.append(f"SEC ticker list unavailable: {e}")
        if m is None:
            if not p.warnings:
                p.warnings.append(f"{sym} is not in SEC company_tickers.json; enter the name and CIK manually")
        else:
            p.cik, p.name, p.source = m.cik, m.name, m.source
            self._submissions(p, self.factory.configured())
        self._existing(p)
        return p

    def _submissions(self, p: Profile, sec: SecClient) -> None:
        try:
            body = sec.get(submissions_url(p.cik))
        except Exception as e:  # noqa: BLE001
            p.warnings.append(f"SEC submissions unavailable: {e}")
            return
        if body is None:
            p.warnings.append(f"SEC submissions not found for CIK {p.cik}")
            return
        sub = parse_submissions(body)
        if sub.name:
            p.name = sub.name
        p.exchange = sub.exchanges[0] if sub.exchanges else None
        p.sic, p.sic_description = sub.sic, sub.sic_description
        # EDGAR lists re-registrations of the same name as "former" names; only real renames are worth showing
        current = (p.name or "").casefold()
        p.former_names = [n for n in dict.fromkeys(sub.former_names) if n.casefold() != current]
        s = sectors.suggest(sub.sic, sub.sic_description)
        if s is None:
            p.warnings.append("No sector could be suggested from the SIC code; choose the sector and benchmark ETF")
        else:
            p.sector, p.benchmark_symbol, p.industry = s.sector, s.benchmark_symbol, s.industry
            p.sector_confidence, p.sector_note = s.confidence, s.note
            p.sector_alternatives = [{"sector": a, "benchmarkSymbol": sectors.SECTOR_ETF[a]} for a in s.alternatives]

    def _existing(self, p: Profile) -> None:
        t = TickerResolver(self.db)
        cid = t.company_by_cik(p.cik) if p.cik else None
        if cid is None:
            cid = t.company_at(p.symbol, date.today())
        if cid is None:
            return
        active = bool(self.db.scalar("SELECT exists(SELECT 1 FROM universe_membership WHERE company_id = :c AND valid_to IS NULL)", c=cid))
        p.existing = {"companyId": cid, "symbol": t.current_symbol(cid), "active": active}
