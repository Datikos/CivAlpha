"""ADR-0006: live quotes for display. A thread in the worker polls Tiingo's IEX endpoint every CIVALPHA_QUOTES_SECONDS
while the US market is open and keeps the latest quote per symbol in `live_quote`.

Only the portfolio read path uses these quotes. Features, forecasts, the recorded book and the advice rules work on
daily closes (`price_bar`); an intraday price must never reach them.

Without an IEX exchange agreement Tiingo leaves `last` and bid/ask null (IEX policy of 2025-02-01); `tngoLast`, its
reference price (the last IEX trade or the mid), is filled and is what is stored. The API key travels in the
Authorization header, never in the URL, so it cannot end up in a log line.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from ..errors import Problem
from ..settings import settings
from ..sql import Db, db
from .providers import RateLimited, vendor_symbol

log = logging.getLogger("civalpha.quotes")

NEW_YORK = ZoneInfo("America/New_York")
URL = "https://api.tiingo.com/iex/"
BATCH = 100                         # tickers per request (the unfiltered call returns ~4,000 symbols and is slow)
TIMEOUT = 30.0
OPEN = time(9, 25)                  # poll a little before the open...
CLOSE = time(16, 10)                # ...and a little after the close, so the last quote of the day is the close's
STALE_AFTER = timedelta(minutes=15)
MIN_SECONDS = 60


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    prev_close: Decimal | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    volume: int | None
    quoted_at: datetime


def market_window(now: datetime) -> bool:
    """Weekdays 09:25-16:10 New York. Exchange holidays are not known; a poll then returns the previous day's quotes,
    which their timestamps show."""
    ny = now.astimezone(NEW_YORK)
    return ny.weekday() < 5 and OPEN <= ny.time() <= CLOSE


def market_open(now: datetime) -> bool:
    ny = now.astimezone(NEW_YORK)
    return ny.weekday() < 5 and time(9, 30) <= ny.time() < time(16, 0)


def is_stale(quoted_at: datetime | None, now: datetime) -> bool:
    """A quote older than 15 minutes while the market is open. After the close the last quote of the day is current."""
    if quoted_at is None:
        return True
    return market_open(now) and now - quoted_at > STALE_AFTER


def _dec(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except ArithmeticError:
        return None
    return d if d.is_finite() else None


def parse(rows: list[dict], wanted: dict[str, str]) -> list[Quote]:
    """Tiingo IEX rows -> quotes for the platform's symbols. `wanted` maps the vendor ticker (upper case) to the
    platform symbol. Rows without a positive tngoLast or a timestamp are skipped."""
    out = []
    for r in rows:
        sym = wanted.get(str(r.get("ticker") or "").upper())
        price = _dec(r.get("tngoLast"))
        ts = r.get("timestamp")
        if sym is None or price is None or price <= 0 or not ts:
            continue
        try:
            when = datetime.fromisoformat(str(ts))      # nanosecond fractions are truncated to microseconds
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        vol = r.get("volume")
        out.append(Quote(sym, price, _dec(r.get("prevClose")), _dec(r.get("open")), _dec(r.get("high")), _dec(r.get("low")),
                         int(vol) if isinstance(vol, (int, float)) and not isinstance(vol, bool) else None,
                         when.astimezone(timezone.utc)))
    return out


class TiingoQuotes:
    name = "Tiingo IEX"

    def __init__(self, token: str, http: httpx.Client | None = None):
        if not token:
            raise ValueError("TIINGO_API_KEY is required for live quotes")
        self.token = token
        self.http = http

    def fetch(self, symbols: list[str]) -> list[Quote]:
        wanted = {vendor_symbol(s): s for s in symbols}
        tickers = sorted(wanted)
        out: list[Quote] = []
        for i in range(0, len(tickers), BATCH):
            batch = tickers[i:i + BATCH]
            r = self._get({"tickers": ",".join(t.lower() for t in batch)})
            if r.status_code == 429:
                raise RateLimited("Tiingo request limit reached")
            if r.status_code != 200:
                raise Problem(f"Tiingo IEX HTTP {r.status_code}")
            out += parse(r.json(), wanted)
        return out

    def _get(self, params: dict) -> httpx.Response:
        headers = {"Authorization": f"Token {self.token}", "Content-Type": "application/json"}
        if self.http is not None:
            return self.http.get(URL, params=params, headers=headers, timeout=TIMEOUT)
        with httpx.Client(timeout=TIMEOUT) as c:
            return c.get(URL, params=params, headers=headers)


class QuoteService:
    def __init__(self, database: Db | None = None, source=None):
        self.db = database or db()
        self.source = source

    def enabled(self) -> bool:
        s = settings()
        return s.quotes_seconds > 0 and s.prices.provider.lower() == "tiingo" and bool(s.prices.tiingo_api_key)

    def symbols(self) -> dict[str, int | None]:
        """Active universe members (current ticker), the benchmark ETFs and every held symbol -> company id or None."""
        rows = self.db.all("""
            SELECT DISTINCT ON (c.id) t.symbol, c.id AS company_id FROM company c
            JOIN ticker_history t ON t.company_id = c.id
            WHERE EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = c.id AND m.valid_from <= current_date
                          AND (m.valid_to IS NULL OR m.valid_to > current_date))
            ORDER BY c.id, t.valid_from DESC""")
        out: dict[str, int | None] = {r["symbol"]: r["company_id"] for r in rows}
        for b in self.db.scalars("SELECT DISTINCT benchmark_symbol FROM company"):
            out.setdefault(b, None)
        for h in self.db.all("SELECT DISTINCT symbol, company_id FROM holding"):
            out.setdefault(h["symbol"], h["company_id"])
        return out

    def poll(self) -> int:
        """One poll: fetch every tracked symbol, replace the stored quotes, drop symbols no longer tracked. Returns the
        number of quotes stored."""
        source = self.source or TiingoQuotes(settings().prices.tiingo_api_key)
        tracked = self.symbols()
        quotes = source.fetch(sorted(tracked))
        with self.db.transaction():
            self.db.executemany("""
                INSERT INTO live_quote (symbol, company_id, price, prev_close, open, high, low, volume, quoted_at, fetched_at, provider)
                VALUES (:s, :c, :p, :pc, :o, :h, :l, :v, :q, now(), :pr)
                ON CONFLICT (symbol) DO UPDATE SET company_id = EXCLUDED.company_id, price = EXCLUDED.price,
                    prev_close = EXCLUDED.prev_close, open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    volume = EXCLUDED.volume, quoted_at = EXCLUDED.quoted_at, fetched_at = now(), provider = EXCLUDED.provider""",
                [{"s": q.symbol, "c": tracked.get(q.symbol), "p": q.price, "pc": q.prev_close, "o": q.open, "h": q.high, "l": q.low,
                  "v": q.volume, "q": q.quoted_at, "pr": getattr(source, "name", "quotes")} for q in quotes])
            self.db.execute("DELETE FROM live_quote WHERE NOT (symbol = ANY(:s))", s=list(tracked))
        return len(quotes)

    def public(self) -> dict:
        """Quotes of the active universe and the benchmark ETFs, for the research pages. Symbols held outside the
        universe are left out: in accounts mode they would tell one user what another holds."""
        now = datetime.now(timezone.utc)
        rows = self.db.all("""
            SELECT q.symbol, q.price::float8 AS price, q.prev_close::float8 AS prev_close, q.quoted_at FROM live_quote q
            WHERE q.symbol IN (SELECT DISTINCT benchmark_symbol FROM company)
               OR EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = q.company_id AND m.valid_from <= current_date
                          AND (m.valid_to IS NULL OR m.valid_to > current_date))
            ORDER BY q.symbol""")
        quotes = [{"symbol": r["symbol"], "price": r["price"], "prevClose": r["prev_close"],
                   "change": (r["price"] / r["prev_close"] - 1.0) if r["prev_close"] else None,
                   "quotedAt": r["quoted_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                   "stale": is_stale(r["quoted_at"], now)} for r in rows]
        latest = max((r["quoted_at"] for r in rows), default=None)
        return {"enabled": self.enabled(), "marketOpen": market_open(now),
                "asOf": None if latest is None else latest.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "quotes": quotes,
                "note": "Tiingo's reference price (the last IEX trade or the mid), polled every few minutes in market hours. "
                        "Display only: forecasts, the book and the advice use daily closes."}

    def latest(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        return {r["symbol"]: r for r in self.db.all("""
            SELECT symbol, price::float8 AS price, prev_close::float8 AS prev_close, quoted_at FROM live_quote
            WHERE symbol = ANY(:s)""", s=symbols)}


def run_poller(stop: threading.Event) -> None:
    """The worker's quote thread: poll inside the market window, sleep otherwise. Errors are logged and the loop goes
    on; a rate limit waits ten intervals."""
    svc = QuoteService()
    if not svc.enabled():
        log.info("live quotes off (CIVALPHA_QUOTES_SECONDS=0 or the price provider is not Tiingo)")
        return
    every = max(MIN_SECONDS, settings().quotes_seconds)
    log.info("live quotes every %d s, weekdays 09:25-16:10 New York", every)
    while not stop.is_set():
        wait = every
        if market_window(datetime.now(timezone.utc)):
            try:
                n = svc.poll()
                log.info("live quotes: %d stored", n)
            except RateLimited:
                log.warning("live quotes: Tiingo rate limit; pausing")
                wait = every * 10
            except Exception as e:  # noqa: BLE001 - a failed poll must not stop the thread
                # the error type only: an HTTP error's text carries the request URL, whose ticker list includes holdings
                log.warning("live quotes failed: %s", type(e).__name__)
        stop.wait(wait)
