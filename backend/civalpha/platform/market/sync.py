"""Downloads daily bars and corporate actions for every active universe member and every benchmark ETF from the
configured provider. Incremental: each symbol resumes a few days before its last stored bar (so late corrections are
picked up and versioned); symbols without prices start at the configured history start.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from ..errors import Problem
from ..jobs import Log, report
from ..settings import Prices, settings
from ..sql import Db, db
from ..storage import DocumentStore, NewDocument
from ..tickers import Span, TickerResolver
from ..universe import UniverseService
from .csv_prices import ActionRow
from .data import MarketDataService, Resolved
from .providers import PriceProvider, RateLimited, TiingoProvider, YahooChartProvider

NEW_YORK = ZoneInfo("America/New_York")
OVERLAP_DAYS = 7
DEFAULT_HISTORY_START = date(2019, 1, 2)


@dataclass(frozen=True)
class Target:
    company_id: int | None      # None = benchmark ETF
    symbol: str


@dataclass(frozen=True)
class Summary:
    symbols: int
    failed: int
    inserted: int
    corrected: int


def last_completed_session(ny_now: datetime | None = None) -> date:
    """Today if the US session has closed (after 16:30 New York), otherwise yesterday: partial bars are never stored."""
    now = ny_now.astimezone(NEW_YORK) if ny_now is not None and ny_now.tzinfo is not None else (ny_now or datetime.now(NEW_YORK))
    return now.date() - timedelta(days=1) if now.time() < time(16, 30) else now.date()


def latest_weekday(d: date) -> date:
    """The last Monday-Friday on or before `d`: a Friday bar is current all weekend (holidays still cost one request)."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def symbol_on(spans: list[Span], d: date, fallback: str) -> str:
    return next((sp.symbol for sp in spans if sp.covers(d)), fallback)


class PriceSyncService:
    def __init__(self, database: Db | None = None, market: MarketDataService | None = None, docs: DocumentStore | None = None,
                 universe: UniverseService | None = None, tickers: TickerResolver | None = None, prices: Prices | None = None,
                 http: httpx.Client | None = None, pause: float = 0.3):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.tickers = tickers or TickerResolver(self.db)
        self.universe = universe or UniverseService(self.db)
        self.market = market or MarketDataService(self.db, self.docs, self.tickers, self.universe)
        self.prices = prices if prices is not None else settings().prices
        self.http = http
        self.pause = pause      # seconds between symbols: be gentle with free endpoints

    def enabled(self) -> bool:
        return self.prices is not None and self.prices.enabled

    def provider_name(self) -> str:
        return self.prices.provider.lower() if self.enabled() else "none"

    def provider(self) -> PriceProvider:
        if not self.enabled():
            raise Problem("No price provider is configured. Set CIVALPHA_PRICE_PROVIDER=yahoo (no key; unofficial, "
                          "personal research only) or CIVALPHA_PRICE_PROVIDER=tiingo with TIINGO_API_KEY in .env, then restart; "
                          "or import a prices CSV.")
        name = self.prices.provider.lower()
        if name == "yahoo":
            return YahooChartProvider(self.http)
        if name == "tiingo":
            return TiingoProvider(self.prices.tiingo_api_key, self.http)
        raise Problem(f"unknown CIVALPHA_PRICE_PROVIDER '{self.prices.provider}' (use yahoo or tiingo)")

    def targets(self) -> list[Target]:
        """Active members (current ticker) plus all benchmark ETFs."""
        out = [Target(c["id"], self.tickers.current_symbol(c["id"])) for c in self.universe.companies()]
        out += [Target(None, b) for b in self.universe.benchmark_symbols()]
        return out

    def sync(self, log: Log, provider: PriceProvider | None = None) -> Summary:
        """Syncs with `provider`, or with the configured one when None."""
        p = provider if provider is not None else self.provider()
        to = last_completed_session()
        start = (self.prices.history_start if self.prices is not None else None) or DEFAULT_HISTORY_START
        failed = inserted = corrected = current = 0
        rate_limit: str | None = None
        targets = self.targets()
        log(f"price sync from {p.name} for {len(targets)} symbols through {to}")
        for i, t in enumerate(targets):
            report(log, i, len(targets), f"prices {t.symbol} ({i + 1}/{len(targets)})")
            try:
                last = self._last_bar(t)
                if last is not None and not last < latest_weekday(to):
                    current += 1   # already has the latest completed session: no request needed
                    continue
                from_ = start if last is None else last - timedelta(days=OVERLAP_DAYS)
                if from_ > to:
                    continue
                s = p.fetch(t.symbol, from_, to)
                if p.split_adjusted and last is not None and any(a.type == "SPLIT" and a.ex_date > last for a in s.actions):
                    # a new split re-scales the provider's whole history: re-download so stored bars stay consistent
                    log(f"{t.symbol}: new split detected; re-downloading full history")
                    s = p.fetch(t.symbol, start, to)
                bars = [b for b in s.bars if not b.date > to and b.close is not None]
                d = self.docs.store(NewDocument("PRICE_FILE", p.name, s.locator, None, f"{t.symbol} daily prices {from_}..{to}", None,
                                                s.content_type, s.raw))
                # store each bar under the ticker valid on its date (vendors report renamed stocks under today's symbol)
                spans = [] if t.company_id is None else [sp for sp in self.tickers.all_spans() if sp.company_id == t.company_id]
                resolved = [Resolved(t.company_id, b if not spans else replace(b, symbol=symbol_on(spans, b.date, b.symbol)))
                            for b in bars]
                r = self.market.store(resolved, p.name, d.id)
                actions = [ActionRow(a.symbol, a.ex_date, "SPLIT_INFO", a.value, a.announced_at)
                           if p.split_adjusted and a.type == "SPLIT" else a for a in s.actions]
                acts = self.market.store_actions(t.company_id, actions, p.name, d.id)
                inserted += r.inserted
                corrected += r.revised
                log(f"{t.symbol}: {r.inserted} new bars, {r.revised} corrected, {acts} corporate actions")
                if self.pause > 0:
                    _time.sleep(self.pause)
            except RateLimited as e:
                rate_limit = str(e)
                left = len(targets) - i
                failed += left
                log(f"{t.symbol}: {e}. Stopping here so the remaining {left} symbols do not use up more quota; they update on the next run.")
                break
            except Exception as e:  # noqa: BLE001 - one symbol's failure must not stop the others
                failed += 1
                log(f"{t.symbol}: FAILED {e}")
        report(log, len(targets), len(targets), "price sync done")
        log(f"price sync done: {len(targets)} symbols, {current} already current, {failed} failed, {inserted} new bars, {corrected} corrected")
        if rate_limit is not None and failed + current == len(targets) and inserted == 0:
            raise Problem(rate_limit + "; no prices were updated. Try again later (an hour on the free plan).")
        if failed == len(targets) and targets:
            raise Problem("price sync failed for every symbol; check network access and the provider settings")
        return Summary(len(targets), failed, inserted, corrected)

    def _last_bar(self, t: Target) -> date | None:
        if t.company_id is None:
            return self.db.scalar("SELECT max(trade_date) FROM price_bar WHERE company_id IS NULL AND symbol = :s", s=t.symbol)
        return self.db.scalar("SELECT max(trade_date) FROM price_bar WHERE company_id = :c", c=t.company_id)
