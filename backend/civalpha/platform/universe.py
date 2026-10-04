"""The research universe: company, ticker_history, cik_mapping, universe_membership.

config/universe.yml only seeds companies whose CIK is not in the database yet; after that, additions, removals and
edits made through the API are authoritative and are never undone by a pipeline run. Membership is time-ranged
[valid_from, valid_to): removing a stock closes its span, so past forecasts and point-in-time features still see it
as a member for the dates it was one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from .errors import BadRequest, NotFound
from .settings import settings
from .sql import Db, db
from .tickers import TickerResolver, pad_cik

EPOCH = date(1990, 1, 1)


@dataclass(frozen=True)
class TickerSpan:
    symbol: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class Entry:
    symbol: str
    name: str
    cik: str
    sector: str
    industry: str | None
    benchmark: str
    ticker_history: list[TickerSpan] = field(default_factory=list)


@dataclass(frozen=True)
class UniverseConfig:
    universe: str
    start_date: date | None
    benchmarks: dict[str, str]
    companies: list[Entry]

    @staticmethod
    def load(path: str | Path) -> "UniverseConfig":
        y = yaml.safe_load(Path(path).read_text())
        entries = []
        for c in y.get("companies") or []:
            hist = [TickerSpan(str(m["symbol"]), _date(m.get("valid_from")), _date(m.get("valid_to"))) for m in c.get("ticker_history") or []]
            entries.append(Entry(str(c["symbol"]), c.get("name"), str(c.get("cik")), c.get("sector"), c.get("industry"),
                                 c.get("benchmark"), hist))
        return UniverseConfig(y.get("universe"), _date(y.get("start_date")), dict(y.get("benchmarks") or {}), entries)


def _date(o) -> date | None:
    if o is None:
        return None
    if isinstance(o, datetime):
        return o.date()
    if isinstance(o, date):
        return o
    return date.fromisoformat(str(o))


def _required(v: str | None, name: str) -> str:
    if v is None or not str(v).strip():
        raise BadRequest(f"{name} is required")
    return str(v)


def _blank_to_none(v: str | None) -> str | None:
    return None if v is None or not str(v).strip() else str(v).strip()


class UniverseService:
    def __init__(self, database: Db | None = None, universe_file: str | None = None):
        self.db = database or db()
        self.file = Path(universe_file or settings().universe_file)
        self.tickers = TickerResolver(self.db)

    def config(self) -> UniverseConfig:
        try:
            return UniverseConfig.load(self.file)
        except OSError as e:
            raise RuntimeError(f"cannot read universe file {self.file}") from e

    def universe_name(self) -> str:
        return self.config().universe if self.file.is_file() else "default"

    def benchmark_symbols(self) -> list[str]:
        """Benchmark ETFs: those configured plus any benchmark assigned to a company in the database."""
        out = set(self.db.scalars("SELECT DISTINCT benchmark_symbol FROM company"))
        if self.file.is_file():
            out |= set(self.config().benchmarks)
        return sorted(out)

    def load(self, demo: bool) -> int:
        """Seeds companies from the config whose CIK is not in the database yet; returns the number created."""
        cfg = self.config()
        created = 0
        with self.db.transaction():
            for e in cfg.companies:
                cik = pad_cik(e.cik)
                if self.tickers.company_by_cik(cik) is not None:
                    continue
                hist = e.ticker_history or [TickerSpan(e.symbol, EPOCH, None)]
                self._create(e.name, cik, e.sector, e.industry, e.benchmark, hist, cfg.start_date, demo, "universe config")
                created += 1
        return created

    # ------------------------------------------------------------------ management
    def add(self, symbol, name, cik, sector, industry, benchmark_symbol, member_since: date | None) -> int:
        sym = _required(symbol, "symbol").upper().strip()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", sym):
            raise BadRequest("symbol must be 1-10 letters, digits, '.' or '-'")
        c = pad_cik(_required(cik, "cik"))
        if c == "0000000000" or len(c) != 10:
            raise BadRequest("cik must be the SEC Central Index Key (up to 10 digits)")
        bench = _required(benchmark_symbol, "benchmarkSymbol").upper().strip()
        today = date.today()
        since = member_since or today
        if since > today:
            raise BadRequest("memberSince cannot be in the future")
        with self.db.transaction():
            by_cik = self.tickers.company_by_cik(c)
            if by_cik is not None:
                raise BadRequest(f"CIK {c} already belongs to {self.tickers.current_symbol(by_cik)} (company {by_cik}); restore or edit it instead")
            by_sym = self.tickers.company_at(sym, today)
            if by_sym is not None:
                raise BadRequest(f"{sym} is already the current ticker of company {by_sym}")
            return self._create(_required(name, "name").strip(), c, _required(sector, "sector").strip(), _blank_to_none(industry),
                                bench, [TickerSpan(sym, EPOCH, None)], since, False, "manual")

    def _create(self, name, cik, sector, industry, benchmark, spans, member_since, demo, source) -> int:
        cid = self.db.scalar("""INSERT INTO company (name, sector, industry, benchmark_symbol, is_demo)
                                VALUES (:n, :s, :i, :b, :demo) RETURNING id""", n=name, s=sector, i=industry, b=benchmark, demo=demo)
        self.db.execute("INSERT INTO cik_mapping (company_id, cik, valid_from, source) VALUES (:c, :cik, :f, :src)",
                        c=cid, cik=cik, f=EPOCH, src=source)
        for t in spans:
            self.db.execute("INSERT INTO ticker_history (company_id, symbol, valid_from, valid_to, source) VALUES (:c, :s, :f, :t, :src)",
                            c=cid, s=t.symbol.upper(), f=t.valid_from, t=t.valid_to, src=source)
        self.db.execute("INSERT INTO universe_membership (universe, company_id, valid_from) VALUES (:u, :c, :f)",
                        u=self.universe_name(), c=cid, f=member_since)
        return cid

    def edit(self, company_id: int, name=None, sector=None, industry=None, benchmark_symbol=None) -> None:
        self._require(company_id)
        self.db.execute("""
            UPDATE company SET name = coalesce(:n, name), sector = coalesce(:s, sector), industry = coalesce(:i, industry),
                   benchmark_symbol = coalesce(:b, benchmark_symbol) WHERE id = :id""",
            n=_blank_to_none(name), s=_blank_to_none(sector), i=_blank_to_none(industry),
            b=None if not benchmark_symbol or not benchmark_symbol.strip() else benchmark_symbol.upper().strip(), id=company_id)

    def remove(self, company_id: int, effective: date | None) -> None:
        """Ends membership on `effective` (exclusive): the stock is no longer a member from that date on."""
        self._require(company_id)
        on = effective or date.today()
        u = self.universe_name()
        with self.db.transaction():
            n = self.db.execute("""UPDATE universe_membership SET valid_to = :d
                                   WHERE company_id = :c AND universe = :u AND valid_to IS NULL AND valid_from < :d""",
                                d=on, c=company_id, u=u)
            if n == 0:
                # a span that starts on/after the removal date never took effect
                pending = self.db.execute("""DELETE FROM universe_membership WHERE company_id = :c AND universe = :u
                                             AND valid_to IS NULL AND valid_from >= :d""", c=company_id, u=u, d=on)
                if pending == 0:
                    raise BadRequest(f"company {company_id} is not an active member")

    def restore(self, company_id: int, effective: date | None) -> None:
        """Opens a new membership span from `effective` (removal history is kept)."""
        self._require(company_id)
        if self.is_active(company_id):
            raise BadRequest(f"company {company_id} is already an active member")
        on = effective or date.today()
        u = self.universe_name()
        last_end = self.db.scalar("SELECT max(valid_to) FROM universe_membership WHERE company_id = :c AND universe = :u", c=company_id, u=u)
        if last_end is not None and on < last_end:
            raise BadRequest(f"restore date must be on or after {last_end}")
        self.db.execute("INSERT INTO universe_membership (universe, company_id, valid_from) VALUES (:u, :c, :f)", u=u, c=company_id, f=on)

    def change_ticker(self, company_id: int, new_symbol: str, effective: date | None) -> None:
        """Records a ticker change effective on a date; earlier data stays attached under the old symbol."""
        self._require(company_id)
        sym = _required(new_symbol, "symbol").upper().strip()
        on = effective or date.today()
        other = self.tickers.company_at(sym, on)
        if other is not None and other != company_id:
            raise BadRequest(f"{sym} is used by company {other}")
        cik = self.tickers.cik_of(company_id)
        if self.record_observed_ticker(cik, sym, on, "manual") is None:
            raise BadRequest(f"{sym} is already the ticker on {on}")

    def delete(self, company_id: int) -> None:
        """Deletes a company with no data attached; companies with data can only be removed from the universe."""
        self._require(company_id)
        used = self.db.one("""
            SELECT (SELECT count(*) FROM price_bar WHERE company_id = :c) AS prices,
                   (SELECT count(*) FROM filing WHERE company_id = :c) AS filings,
                   (SELECT count(*) FROM forecast WHERE company_id = :c) AS forecasts,
                   (SELECT count(*) FROM company_exposure WHERE company_id = :c) AS exposures,
                   (SELECT count(*) FROM corporate_action WHERE company_id = :c) AS actions,
                   (SELECT count(*) FROM backtest_prediction WHERE company_id = :c) AS backtests""", c=company_id)
        if sum(int(v) for v in used.values()) > 0:
            shown = "{" + ", ".join(f"{k}={v}" for k, v in used.items()) + "}"
            raise BadRequest(f"company {company_id} has data attached {shown}; remove it from the universe instead (its history is kept)")
        with self.db.transaction():
            for t in ("universe_membership", "ticker_history", "cik_mapping"):
                self.db.execute(f"DELETE FROM {t} WHERE company_id = :c", c=company_id)
            self.db.execute("DELETE FROM company WHERE id = :c", c=company_id)

    def record_observed_ticker(self, cik: str | None, symbol: str, observed_on: date, source: str) -> str | None:
        """Applies an observed (symbol, cik) mapping: a changed symbol closes the current span and opens a new one."""
        if cik is None:
            return None
        cid = self.tickers.company_by_cik(cik)
        if cid is None:
            return None
        with self.db.transaction():
            current = self.tickers.symbol_at(cid, observed_on)
            if current is not None and current.upper() == symbol.upper():
                return None
            self.db.execute("UPDATE ticker_history SET valid_to = :d WHERE company_id = :c AND valid_to IS NULL AND valid_from < :d",
                            d=observed_on, c=cid)
            self.db.execute("INSERT INTO ticker_history (company_id, symbol, valid_from, source) VALUES (:c, :s, :d, :src)",
                            c=cid, s=symbol.upper(), d=observed_on, src=source)
        return f"{current or '?'} -> {symbol.upper()}"

    # ------------------------------------------------------------------ queries
    def companies(self) -> list[dict]:
        """Companies that are members today (pipeline steps act on these)."""
        return self.db.all("""
            SELECT c.id, c.name, c.benchmark_symbol, c.industry, c.sector FROM company c
            WHERE EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = c.id AND m.valid_from <= current_date
                            AND (m.valid_to IS NULL OR m.valid_to > current_date))
            ORDER BY c.id""")

    def is_active(self, company_id: int) -> bool:
        return bool(self.db.scalar("SELECT exists(SELECT 1 FROM universe_membership WHERE company_id = :c AND universe = :u AND valid_to IS NULL)",
                                   c=company_id, u=self.universe_name()))

    def any_companies(self) -> bool:
        return bool(self.db.scalar("SELECT exists(SELECT 1 FROM company)"))

    def _require(self, company_id: int) -> None:
        if not self.db.scalar("SELECT exists(SELECT 1 FROM company WHERE id = :id)", id=company_id):
            raise NotFound(f"company {company_id} not found")
