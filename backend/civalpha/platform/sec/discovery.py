"""Finds candidate companies for the research universe from SEC data alone: every listed registrant with its
exchange (company_tickers_exchange.json) and its latest reported public float (the XBRL frames API for
dei:EntityPublicFloat, one request per calendar quarter for every filer at once).

Public float is what a 10-K states as the market value of shares held by non-affiliates as of the last business day of
the registrant's second fiscal quarter, so it is a licence-free size measure that lags the market by up to a year. The
10-K that reports it is filed two quarters later, so the last eight quarterly frames are read (every fiscal-year end,
with a margin) and the newest value per CIK wins.

Filers get the scale wrong often enough to matter (a $4 billion float tagged as $4 quadrillion), so every value is checked
two ways with frames of the same quarters: against total assets (us-gaap:Assets; no company is worth more than
MAX_FLOAT_TO_ASSETS times its assets) and, because a bank's assets dwarf its worth, against the price per share the float
implies (float / dei:EntityCommonStockSharesOutstanding; above MAX_IMPLIED_PRICE is a scale error unless the float is
small next to assets, which is how Berkshire's class A shares look). A value that fails is ignored and the next-newest
value that passes is used instead. A value that is too small by the same mistake simply fails the size screen, which is
the safe direction.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..tickers import pad_cik
from .client import SecClientFactory
from .parsers import as_string

COMPANY_TICKERS_EXCHANGE = "https://www.sec.gov/files/company_tickers_exchange.json"
PUBLIC_FLOAT_FRAME = "https://data.sec.gov/api/xbrl/frames/dei/EntityPublicFloat/USD/CY{year}Q{quarter}I.json"
ASSETS_FRAME = "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY{year}Q{quarter}I.json"
SHARES_FRAME = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{year}Q{quarter}I.json"
MAX_FLOAT = 5e12                 # no listed company is worth more; above this the scale is wrong
MAX_FLOAT_TO_ASSETS = 200.0      # asset-light growth companies run to ~50x; a 1000x scale error lands far above
MAX_IMPLIED_PRICE = 10_000.0     # dollars per share; only Berkshire's class A trades above this
MIN_ASSETS_RATIO_FOR_PRICE = 10.0  # ...and its float is under 10x assets, which a bank's scale error is not
DEFAULT_EXCHANGES = ("Nasdaq", "NYSE")
_TICKER = re.compile(r"[A-Z0-9.\-]{1,10}")


@dataclass(frozen=True)
class Listing:
    cik: str
    name: str
    ticker: str
    exchange: str


@dataclass(frozen=True)
class Candidate:
    symbol: str
    cik: str
    name: str
    exchange: str
    public_float: float
    float_as_of: date | None


@dataclass
class Discovery:
    candidates: list[Candidate]
    matched: int               # listings on the chosen exchanges with a public float at or above the minimum
    listed: int                # listings on the chosen exchanges
    already_tracked: int       # matched companies left out because they are in the database
    frames: list[str]
    notes: list[str] = field(default_factory=list)


def parse_exchange_list(b: bytes) -> list[Listing]:
    """company_tickers_exchange.json: {"fields": ["cik", "name", "ticker", "exchange"], "data": [[...], ...]}."""
    root = json.loads(b)
    fields = [as_string(f) for f in (root.get("fields") or [])] if isinstance(root, dict) else []
    out = []
    for row in (root.get("data") or []) if isinstance(root, dict) else []:
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        r = dict(zip(fields, row))
        t = as_string(r.get("ticker")).upper().strip()
        cik = as_string(r.get("cik")).strip()
        if not t or not cik or not _TICKER.fullmatch(t):
            continue
        out.append(Listing(pad_cik(cik), as_string(r.get("name")).strip(), t, as_string(r.get("exchange")).strip()))
    return out


def parse_float_frame(b: bytes) -> dict[str, tuple[float, date | None]]:
    """An instant USD frame (dei:EntityPublicFloat, us-gaap:Assets): {"data": [{"cik": 320193, "end": "2025-03-28",
    "val": 2.9e12, ...}, ...]}; the newest value per CIK."""
    root = json.loads(b)
    out: dict[str, tuple[float, date | None]] = {}
    for r in (root.get("data") or []) if isinstance(root, dict) else []:
        if not isinstance(r, dict):
            continue
        try:
            val = float(r.get("val"))
        except (TypeError, ValueError):
            continue
        cik = as_string(r.get("cik")).strip()
        if not cik or val < 0:
            continue
        end = as_string(r.get("end")).strip()
        try:
            end_d = date.fromisoformat(end) if end else None
        except ValueError:
            end_d = None
        key = pad_cik(cik)
        if key not in out or (end_d or date.min) > (out[key][1] or date.min):
            out[key] = (val, end_d)
    return out


FRAMES_BACK = 8


def recent_frames(today: date, n: int = FRAMES_BACK) -> list[str]:
    """The names of the last `n` completed calendar quarters, newest first (CY2026Q2I ...)."""
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"CY{y}Q{q}I")
    return out


def frame_url(name: str, template: str = PUBLIC_FLOAT_FRAME) -> str:
    m = re.fullmatch(r"CY(\d{4})Q([1-4])I", name)
    if m is None:
        raise ValueError(name)
    return template.format(year=m.group(1), quarter=m.group(2))


def plausible(public_float: float, assets: float | None, shares: float | None = None) -> bool:
    """False when the float can only be a scale error (see the module docstring). Missing assets or shares give no
    information and are not held against the company."""
    if public_float > MAX_FLOAT:
        return False
    has_assets = assets is not None and assets > 0
    if has_assets and public_float > MAX_FLOAT_TO_ASSETS * assets:
        return False
    if shares is not None and shares > 0 and public_float / shares > MAX_IMPLIED_PRICE:
        return has_assets and public_float <= MIN_ASSETS_RATIO_FOR_PRICE * assets
    return True


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CompanyDiscovery:
    """Joins the exchange list with the public-float frames; both are cached for a day."""

    def __init__(self, factory: SecClientFactory | None = None):
        self.factory = factory or SecClientFactory()
        self._listings: list[Listing] = []
        self._floats: dict[str, list[tuple[float, date | None]]] = {}    # newest first
        self._assets: dict[str, float] = {}
        self._shares: dict[str, float] = {}
        self._frames: list[str] = []
        self._notes: list[str] = []
        self._loaded_at = _EPOCH
        self._lock = threading.Lock()

    def _load(self, today: date) -> None:
        if datetime.now(timezone.utc) - self._loaded_at < timedelta(hours=24) and self._listings:
            return
        client = self.factory.configured()
        body = client.get(COMPANY_TICKERS_EXCHANGE)
        listings = parse_exchange_list(body) if body is not None else []
        floats: dict[str, list[tuple[float, date | None]]] = {}
        book = {ASSETS_FRAME: {}, SHARES_FRAME: {}}
        used, notes = [], []
        for name in recent_frames(today):
            fb = client.get(frame_url(name))
            if fb is None:
                notes.append(f"SEC frame {name} not available")
                continue
            used.append(name)
            for cik, v in parse_float_frame(fb).items():
                floats.setdefault(cik, []).append(v)
            for template, newest in book.items():
                bb = client.get(frame_url(name, template))
                for cik, (val, end) in (parse_float_frame(bb) if bb is not None else {}).items():
                    if cik not in newest or (end or date.min) > (newest[cik][1] or date.min):
                        newest[cik] = (val, end)
        for v in floats.values():
            v.sort(key=lambda x: x[1] or date.min, reverse=True)
        if not listings:
            notes.append("SEC company_tickers_exchange.json unavailable or empty")
        self._listings, self._floats, self._frames, self._notes = listings, floats, used, notes
        self._assets = {cik: a[0] for cik, a in book[ASSETS_FRAME].items()}
        self._shares = {cik: a[0] for cik, a in book[SHARES_FRAME].items()}
        self._loaded_at = datetime.now(timezone.utc)

    def _float_of(self, cik: str) -> tuple[float, date | None] | None:
        """The newest reported public float that is plausible next to the company's assets and shares outstanding."""
        a, n = self._assets.get(cik), self._shares.get(cik)
        for val, end in self._floats.get(cik, []):
            if plausible(val, a, n):
                return val, end
        return None

    def candidates(self, exchanges=DEFAULT_EXCHANGES, min_public_float: float = 2e9, limit: int = 100,
                   exclude_ciks: set[str] | None = None, today: date | None = None) -> Discovery:
        """Listings on `exchanges` with a public float of at least `min_public_float`, largest first, one per CIK
        (the first ticker the SEC lists, so GOOGL rather than GOOG), without the CIKs in `exclude_ciks`."""
        today = today or date.today()
        wanted = {e.strip().lower() for e in exchanges if e and e.strip()}
        exclude = exclude_ciks or set()
        with self._lock:
            self._load(today)
            listings, frames, notes = self._listings, list(self._frames), list(self._notes)
            floats = {l.cik: self._float_of(l.cik) for l in listings}
            implausible = sum(1 for l in listings if self._floats.get(l.cik) and floats[l.cik] is None)
        if implausible:
            notes.append(f"{implausible} companies ignored: every public float they reported is implausible next to their "
                         f"total assets or shares outstanding (a scale error in the filing)")
        seen: set[str] = set()
        listed = matched = tracked = 0
        rows: list[Candidate] = []
        for l in listings:
            if l.exchange.lower() not in wanted or l.cik in seen:
                continue
            seen.add(l.cik)
            listed += 1
            f = floats.get(l.cik)
            if f is None or f[0] < min_public_float:
                continue
            matched += 1
            if l.cik in exclude:
                tracked += 1
                continue
            rows.append(Candidate(l.ticker, l.cik, l.name, l.exchange, f[0], f[1]))
        rows.sort(key=lambda c: (-c.public_float, c.symbol))
        return Discovery(rows[:max(0, limit)], matched, listed, tracked, frames, notes)
