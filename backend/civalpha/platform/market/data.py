"""Imports daily bars and corporate actions.

Symbols are resolved to companies by date through ticker history (so FB rows before 2022-06-09 attach to the same
company as META rows after); configured benchmark ETFs are stored with company_id NULL. Prices are stored raw;
adjustments come from the corporate-action table. Re-importing an identical bar is a no-op; a bar with a different
close is a correction: the old values move to price_bar_revision and the bar becomes version n+1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..errors import Problem
from ..sql import Db, db
from ..storage import DocumentStore, NewDocument
from ..tickers import TickerResolver, resolve
from ..universe import UniverseService
from .csv_prices import ActionRow, PriceBarRow, parse_actions, parse_bars

_INSERT_ACTION = """
    INSERT INTO corporate_action (company_id, symbol, ex_date, action_type, value, announced_at, provider, source_document_id, is_demo)
    VALUES (:c, :s, :d, :t, :v, :a, :p, :doc, :demo) ON CONFLICT (symbol, ex_date, action_type) DO NOTHING"""


@dataclass(frozen=True)
class ImportResult:
    rows: int
    inserted: int
    unchanged: int
    revised: int
    unknown_symbols: list[str] = field(default_factory=list)   # sorted, distinct


@dataclass(frozen=True)
class Resolved:
    """A bar already attributed to a company (None company = benchmark ETF)."""
    company_id: int | None
    row: PriceBarRow


class MarketDataService:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, tickers: TickerResolver | None = None,
                 universe: UniverseService | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.tickers = tickers or TickerResolver(self.db)
        self.universe = universe or UniverseService(self.db)

    def import_prices(self, csv: bytes, file_name: str, provider: str, demo: bool) -> ImportResult:
        d = self.docs.store(NewDocument("PRICE_FILE", provider, "file://" + file_name, None, "Price file " + file_name, None,
                                        "text/csv", csv, demo))
        rows = parse_bars(csv)
        benchmarks = set(self.universe.benchmark_symbols())
        spans: dict[str, list] = {}
        for sp in self.tickers.all_spans():
            spans.setdefault(sp.symbol.upper(), []).append(sp)
        unknown: set[str] = set()
        resolved: list[Resolved] = []
        for r in rows:
            company_id = None
            if r.symbol not in benchmarks:
                company_id = resolve(spans.get(r.symbol, []), r.date)
                if company_id is None:
                    unknown.add(r.symbol)
                    continue
            resolved.append(Resolved(company_id, r))
        res = self.store(resolved, provider, d.id, demo)
        return ImportResult(len(rows), res.inserted, res.unchanged, res.revised, sorted(unknown))

    def store(self, resolved: list[Resolved], provider: str, document_id: int, demo: bool) -> ImportResult:
        """Writes resolved bars: new bars are inserted; an identical bar is a no-op; a different close is a correction
        (old values archived in price_bar_revision, bar becomes version n+1)."""
        with self.db.transaction():
            existing = {}
            syms = list(dict.fromkeys(x.row.symbol for x in resolved))
            if syms:
                for r in self.db.all("SELECT symbol, trade_date, close FROM price_bar WHERE symbol = ANY(:syms)", syms=syms):
                    existing[(r["symbol"], r["trade_date"])] = r["close"]
            unchanged = 0
            corrections: list[dict] = []
            batch: list[dict] = []
            seen: set = set()
            for x in resolved:
                r = x.row
                key = (r.symbol, r.date)
                if key in seen:
                    continue   # duplicate row within one import
                seen.add(key)
                prev = existing.get(key)
                p = {"c": x.company_id, "s": r.symbol, "d": r.date, "o": r.open, "h": r.high, "l": r.low, "cl": r.close,
                     "v": r.volume, "p": provider, "doc": document_id, "demo": demo}
                if prev is None:
                    batch.append(p)
                elif r.close is not None and prev.compare(r.close) == 0:
                    unchanged += 1
                else:
                    corrections.append(p)
            # archive the replaced values, then apply the corrections as a new version of each bar
            self.db.executemany("""
                INSERT INTO price_bar_revision (symbol, trade_date, company_id, version, open, high, low, close, volume, provider,
                                                source_document_id, ingested_at, superseded_by_document_id, is_demo)
                SELECT symbol, trade_date, company_id, version, open, high, low, close, volume, provider, source_document_id,
                       ingested_at, :doc, is_demo FROM price_bar WHERE symbol = :s AND trade_date = :d""", corrections)
            self.db.executemany("""
                UPDATE price_bar SET open = :o, high = :h, low = :l, close = :cl, volume = :v, provider = :p, source_document_id = :doc,
                       ingested_at = now(), version = version + 1 WHERE symbol = :s AND trade_date = :d""", corrections)
            inserted = max(0, self.db.executemany("""
                INSERT INTO price_bar (company_id, symbol, trade_date, open, high, low, close, volume, provider, source_document_id, is_demo)
                VALUES (:c, :s, :d, :o, :h, :l, :cl, :v, :p, :doc, :demo) ON CONFLICT (symbol, trade_date) DO NOTHING""", batch))
        return ImportResult(len(resolved), inserted, unchanged, len(corrections), [])

    def store_actions(self, company_id: int | None, actions: list[ActionRow], provider: str, document_id: int, demo: bool) -> int:
        """Stores corporate actions already attributed to a company (None = benchmark); duplicates are ignored."""
        n = 0
        for a in actions:
            n += self.db.execute(_INSERT_ACTION, c=company_id, s=a.symbol, d=a.ex_date, t=a.type, v=a.value, a=a.announced_at,
                                 p=provider, doc=document_id, demo=demo)
        return n

    def import_actions(self, csv: bytes, file_name: str, provider: str, demo: bool) -> int:
        d = self.docs.store(NewDocument("PRICE_FILE", provider, "file://" + file_name, None, "Corporate actions " + file_name, None,
                                        "text/csv", csv, demo))
        benchmarks = set(self.universe.benchmark_symbols())
        n = 0
        for a in parse_actions(csv):
            company_id = None if a.symbol in benchmarks else self.tickers.company_at(a.symbol, a.ex_date)
            n += self.db.execute(_INSERT_ACTION, c=company_id, s=a.symbol, d=a.ex_date, t=a.type, v=a.value, a=a.announced_at,
                                 p=provider, doc=d.id, demo=demo)
        return n

    def missing_benchmarks(self) -> list[str]:
        """Configured benchmark ETFs (config/universe.yml) that have no price bars yet, sorted."""
        present = set(self.db.scalars("SELECT DISTINCT symbol FROM price_bar WHERE company_id IS NULL"))
        return sorted(set(self.universe.benchmark_symbols()) - present)

    def require_benchmarks(self) -> str | None:
        """Every forecast and evaluation is relative to a sector benchmark, so they need benchmark prices.
        Raises Problem with an actionable message when none are loaded; returns a warning when only some are missing."""
        missing = self.missing_benchmarks()
        if self.latest_benchmark_date() is None:
            raise Problem("No benchmark ETF prices are loaded (" + ", ".join(self.universe.benchmark_symbols())
                          + "). Run 'Update prices' (needs CIVALPHA_PRICE_PROVIDER), load the demo dataset, or import a prices CSV "
                            "that includes these ETFs, then try again.")
        if not missing:
            return None
        return ("Warning: no prices for benchmark(s) " + ", ".join(missing)
                + "; companies benchmarked against them are skipped. Import their prices to include them.")

    def latest_benchmark_date(self) -> date | None:
        return self.db.scalar("SELECT max(trade_date) FROM price_bar WHERE company_id IS NULL")
