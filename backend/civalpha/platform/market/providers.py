"""Replaceable sources of daily bars and corporate actions (Tiingo, Yahoo chart API).

Whether a provider's data may be used for model training or shown publicly is a licensing decision recorded in
configuration, not implied by API access.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import httpx

from ..errors import Problem
from ..http import client
from .csv_prices import ActionRow, PriceBarRow


class RateLimited(Problem):
    """The provider refused because the account's request quota is used up (HTTP 429); retrying now only wastes quota."""


@dataclass(frozen=True)
class Series:
    bars: list[PriceBarRow]
    actions: list[ActionRow]
    raw: bytes
    content_type: str
    locator: str        # the request URL with any credential removed (stored as provenance)


def vendor_symbol(symbol: str) -> str:
    """Vendor symbols use '-' for share classes (BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


class PriceProvider(ABC):
    name: str = "provider"
    # True when the provider's closes are already split-adjusted. Splits are then stored as SPLIT_INFO (informational) so
    # they are not applied a second time, and a newly announced split triggers a full re-download so all stored bars
    # share the same adjustment.
    split_adjusted: bool = False

    @abstractmethod
    def fetch(self, symbol: str, from_: date, to: date) -> Series:
        ...


def _json(body: bytes):
    # numbers as a JSON library reading doubles would see them, but exact Decimal from there on (like Jackson decimalValue)
    return json.loads(body, parse_float=lambda s: Decimal(repr(float(s))))


def _is_number(v) -> bool:
    return isinstance(v, (int, Decimal)) and not isinstance(v, bool)


def _get(c: httpx.Client | None, url: str, headers: dict) -> httpx.Response:
    if c is not None:
        return c.get(url, headers=headers, timeout=30.0)
    with client(timeout=30.0) as own:
        return own.get(url, headers=headers)


class TiingoProvider(PriceProvider):
    """Tiingo end-of-day prices (free API key; check the plan's terms for your use). Returns raw closes with a per-day
    split factor and cash dividend, which matches how CivAlpha stores prices."""

    BASE = "https://api.tiingo.com/tiingo/daily/"
    name = "Tiingo"
    split_adjusted = False

    def __init__(self, token: str | None, http: httpx.Client | None = None):
        if token is None or token.strip() == "":
            raise ValueError("TIINGO_API_KEY is required for the tiingo price provider")
        self.token = token
        self.http = http

    def fetch(self, symbol: str, from_: date, to: date) -> Series:
        base = f"{self.BASE}{vendor_symbol(symbol).lower()}/prices?startDate={from_.isoformat()}&endDate={to.isoformat()}"
        r = _get(self.http, base + "&token=" + quote_plus(self.token), {"Content-Type": "application/json"})
        if r.status_code == 404:
            raise ValueError("unknown symbol at Tiingo: " + symbol)
        if r.status_code == 429:
            raise RateLimited("Tiingo request limit reached (the free plan allows 50 requests an hour and 1,000 a day)")
        if r.status_code != 200:
            raise Problem(f"Tiingo HTTP {r.status_code}")
        return self.parse(r.content, symbol.upper(), base)

    @staticmethod
    def parse(body: bytes, symbol: str, locator: str) -> Series:
        bars, actions = [], []
        for d in _json(body):
            day = date.fromisoformat(str(d.get("date", ""))[:10])
            vol = d.get("volume")
            bars.append(PriceBarRow(symbol, day, _num(d, "open"), _num(d, "high"), _num(d, "low"), _num(d, "close"),
                                    int(vol) if _is_number(vol) else None))
            split = _num(d, "splitFactor")
            if split is not None and split != 1 and split > 0:
                actions.append(ActionRow(symbol, day, "SPLIT", split.normalize(), None))
            div = _num(d, "divCash")
            if div is not None and div > 0:
                actions.append(ActionRow(symbol, day, "CASH_DIVIDEND", div, None))
        return Series(bars, actions, body, "application/json", locator)


def _num(d: dict, f: str) -> Decimal | None:
    v = d.get(f)
    return Decimal(v) if _is_number(v) else None


class YahooChartProvider(PriceProvider):
    """Yahoo Finance chart endpoint. No key, but unofficial: no SLA, and Yahoo's terms restrict automated use and
    redistribution, so treat it as personal research only. Closes are split-adjusted (not dividend-adjusted); dividends
    and splits come from the "events" block."""

    BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
    name = "Yahoo Finance (unofficial chart API)"
    split_adjusted = True

    def __init__(self, http: httpx.Client | None = None):
        self.http = http

    def fetch(self, symbol: str, from_: date, to: date) -> Series:
        p1 = int(datetime.combine(from_, time(0), timezone.utc).timestamp())
        p2 = int(datetime.combine(to + timedelta(days=1), time(0), timezone.utc).timestamp())
        url = (f"{self.BASE}{vendor_symbol(symbol)}?period1={p1}&period2={p2}"
               "&interval=1d&events=div%2Csplit&includeAdjustedClose=true")
        r = _get(self.http, url, {"User-Agent": "Mozilla/5.0 (CivAlpha research)"})
        if r.status_code == 404:
            raise ValueError("unknown symbol at Yahoo: " + symbol)
        if r.status_code == 429:
            raise RateLimited("Yahoo is rate-limiting requests")
        if r.status_code != 200:
            raise Problem(f"Yahoo HTTP {r.status_code}")
        return self.parse(r.content, symbol.upper(), url)

    @staticmethod
    def parse(body: bytes, symbol: str, url: str) -> Series:
        root = _obj(_obj(_json(body)).get("chart"))
        err = root.get("error")
        if isinstance(err, dict):
            desc = err.get("description")
            raise Problem("Yahoo: " + (desc if isinstance(desc, str) else ""))
        results = root.get("result")
        res = _obj(results[0] if isinstance(results, list) and results else None)
        zone = _zone(_obj(res.get("meta")))
        ts = res.get("timestamp") if isinstance(res.get("timestamp"), list) else []
        quotes = _obj(res.get("indicators")).get("quote")
        q = _obj(quotes[0] if isinstance(quotes, list) and quotes else None)

        def at(name: str, i: int):
            arr = q.get(name)
            return arr[i] if isinstance(arr, list) and i < len(arr) else None

        bars = []
        for i, t in enumerate(ts):
            close = at("close", i)
            if close is None:
                continue   # halted/holiday placeholder
            d = _local_date(t, zone)
            vol = at("volume", i)
            bars.append(PriceBarRow(symbol, d, _dec4(at("open", i)), _dec4(at("high", i)), _dec4(at("low", i)), _dec4(close),
                                    int(vol) if _is_number(vol) else None))
        events = _obj(res.get("events"))
        actions = []
        for v in map(_obj, _obj(events.get("dividends")).values()):
            actions.append(ActionRow(symbol, _local_date(v.get("date", 0), zone), "CASH_DIVIDEND", _decimal(v.get("amount")), None))
        for v in map(_obj, _obj(events.get("splits")).values()):
            ratio = (_decimal(v.get("numerator")) / _decimal(v.get("denominator"))).quantize(Decimal("1E-8"), rounding=ROUND_HALF_UP)
            actions.append(ActionRow(symbol, _local_date(v.get("date", 0), zone), "SPLIT", ratio.normalize(), None))
        return Series(bars, actions, body, "application/json", url)


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _decimal(v) -> Decimal:
    return Decimal(v) if _is_number(v) else Decimal(0)


def _dec4(v) -> Decimal | None:
    return Decimal(v).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if _is_number(v) else None


def _local_date(epoch_seconds, zone: tzinfo) -> date:
    return datetime.fromtimestamp(int(epoch_seconds), zone).date()


def _zone(meta: dict) -> tzinfo:
    tz = meta.get("exchangeTimezoneName")
    if isinstance(tz, str) and tz:
        try:
            return ZoneInfo(tz)
        except (KeyError, ValueError):
            pass
    off = meta.get("gmtoffset")
    return timezone(timedelta(seconds=int(off) if _is_number(off) else -14400))
