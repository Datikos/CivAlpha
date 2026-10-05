"""The research universe: company, ticker_history, cik_mapping, universe_membership.

Companies are added, edited, removed and restored through the API (the Universe page), with the CIK and name from
SEC company_tickers.json. Membership is time-ranged [valid_from, valid_to): removing a stock closes its span, so past
forecasts and point-in-time features still see it as a member for the dates it was one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .errors import BadRequest, NotFound
from .sql import Db, db
from .tickers import TickerResolver, pad_cik

EPOCH = date(1990, 1, 1)
DEFAULT_UNIVERSE = "default"


@dataclass(frozen=True)
class TickerSpan:
    symbol: str
    valid_from: date | None
    valid_to: date | None


def _required(v: str | None, name: str) -> str:
    if v is None or not str(v).strip():
        raise BadRequest(f"{name} is required")
    return str(v)


def _blank_to_none(v: str | None) -> str | None:
    return None if v is None or not str(v).strip() else str(v).strip()


MAX_TAGS = 20
MAX_TAG_LENGTH = 40
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.&/+\-]*")


def normalize_tags(tags) -> list[str]:
    """Cleans a list of user-defined tags: trimmed, inner whitespace collapsed, case-insensitive duplicates dropped
    (the first spelling wins), blanks ignored. Raises BadRequest for a tag that is too long, uses other characters
    than letters, digits, space, _ . & / + -, or when there are more than MAX_TAGS."""
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        t = " ".join(str(raw or "").split())
        if not t:
            continue
        if len(t) > MAX_TAG_LENGTH:
            raise BadRequest(f"tag {t[:MAX_TAG_LENGTH]!r}… is longer than {MAX_TAG_LENGTH} characters")
        if not _TAG.fullmatch(t):
            raise BadRequest(f"tag {t!r} may only use letters, digits, spaces and _ . & / + -")
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    if len(out) > MAX_TAGS:
        raise BadRequest(f"at most {MAX_TAGS} tags per company")
    return out


class UniverseService:
    def __init__(self, database: Db | None = None):
        self.db = database or db()
        self.tickers = TickerResolver(self.db)

    def universe_name(self) -> str:
        """Name recorded on membership rows (one universe per database)."""
        return self.db.scalar("SELECT universe FROM universe_membership ORDER BY id LIMIT 1") or DEFAULT_UNIVERSE

    def benchmark_symbols(self) -> list[str]:
        """Benchmark ETFs: the sector benchmarks assigned to companies."""
        return sorted(self.db.scalars("SELECT DISTINCT benchmark_symbol FROM company"))

    # ------------------------------------------------------------------ management
    def add(self, symbol, name, cik, sector, industry, benchmark_symbol, member_since: date | None,
            former_tickers: list[TickerSpan] | None = None, tags: list[str] | None = None) -> int:
        """Adds a company; `former_tickers` records earlier symbols (e.g. FB before META) as closed spans, `tags` are
        the user-defined categories it starts with (see `normalize_tags`)."""
        sym = _required(symbol, "symbol").upper().strip()
        clean_tags = normalize_tags(tags)
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
            spans = list(former_tickers or [])
            # the current symbol starts where the last former one ended (or at the epoch without history)
            spans.append(TickerSpan(sym, max((t.valid_to for t in spans if t.valid_to), default=EPOCH), None))
            cid = self._create(_required(name, "name").strip(), c, _required(sector, "sector").strip(), _blank_to_none(industry),
                               bench, spans, since, "manual")
            if clean_tags:
                self.set_tags(cid, clean_tags)
            return cid

    def _create(self, name, cik, sector, industry, benchmark, spans, member_since, source) -> int:
        universe = self.universe_name()
        cid = self.db.scalar("""INSERT INTO company (name, sector, industry, benchmark_symbol)
                                VALUES (:n, :s, :i, :b) RETURNING id""", n=name, s=sector, i=industry, b=benchmark)
        self.db.execute("INSERT INTO cik_mapping (company_id, cik, valid_from, source) VALUES (:c, :cik, :f, :src)",
                        c=cid, cik=cik, f=EPOCH, src=source)
        for t in spans:
            self.db.execute("INSERT INTO ticker_history (company_id, symbol, valid_from, valid_to, source) VALUES (:c, :s, :f, :t, :src)",
                            c=cid, s=t.symbol.upper(), f=t.valid_from, t=t.valid_to, src=source)
        self.db.execute("INSERT INTO universe_membership (universe, company_id, valid_from) VALUES (:u, :c, :f)",
                        u=universe, c=cid, f=member_since)
        return cid

    def edit(self, company_id: int, name=None, sector=None, industry=None, benchmark_symbol=None) -> None:
        self._require(company_id)
        self.db.execute("""
            UPDATE company SET name = coalesce(:n, name), sector = coalesce(:s, sector), industry = coalesce(:i, industry),
                   benchmark_symbol = coalesce(:b, benchmark_symbol) WHERE id = :id""",
            n=_blank_to_none(name), s=_blank_to_none(sector), i=_blank_to_none(industry),
            b=None if not benchmark_symbol or not benchmark_symbol.strip() else benchmark_symbol.upper().strip(), id=company_id)

    # ------------------------------------------------------------------ tags
    def set_tags(self, company_id: int, tags) -> list[str]:
        """Replaces a company's tags with the cleaned list and returns it (an empty list clears them). A tag that
        another company already carries in a different case takes that spelling, so a tag is one tag universe-wide."""
        self._require(company_id)
        clean = normalize_tags(tags)
        with self.db.transaction():
            self.db.execute("DELETE FROM company_tag WHERE company_id = :c", c=company_id)
            known = {t.lower(): t for t in self.db.scalars("SELECT DISTINCT tag FROM company_tag")}
            clean = [known.get(t.lower(), t) for t in clean]
            for t in clean:
                self.db.execute("INSERT INTO company_tag (company_id, tag) VALUES (:c, :t)", c=company_id, t=t)
        return clean

    def tags_of(self, company_id: int) -> list[str]:
        return list(self.db.scalars("SELECT tag FROM company_tag WHERE company_id = :c ORDER BY lower(tag)", c=company_id))

    def tags_by_company(self) -> dict[int, list[str]]:
        """Every company's tags, keyed by company id (companies without tags are absent)."""
        out: dict[int, list[str]] = {}
        for r in self.db.all("SELECT company_id, tag FROM company_tag ORDER BY company_id, lower(tag)"):
            out.setdefault(r["company_id"], []).append(r["tag"])
        return out

    def all_tags(self) -> list[dict]:
        """Distinct tags across the universe with how many companies carry each, most used first."""
        return self.db.all("""SELECT tag, count(*) AS count FROM company_tag
                              GROUP BY tag ORDER BY count DESC, lower(tag)""")

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
            for t in ("company_tag", "universe_membership", "ticker_history", "cik_mapping"):
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
