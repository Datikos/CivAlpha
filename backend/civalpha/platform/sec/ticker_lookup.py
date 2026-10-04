"""Looks up a ticker's CIK and registrant name in SEC's company_tickers.json (live mode: sec.gov, cached for a day;
fixture mode: the local fixture file, which only covers the demo companies)."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..tickers import pad_cik
from .client import COMPANY_TICKERS, SecClientFactory
from .parsers import as_string


@dataclass(frozen=True)
class Match:
    symbol: str
    cik: str
    name: str
    source: str


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SecTickerLookup:
    def __init__(self, factory: SecClientFactory | None = None):
        self.factory = factory or SecClientFactory()
        self._cache: dict[str, Match] = {}
        self._loaded_at = _EPOCH
        self._lock = threading.Lock()

    def lookup(self, symbol: str) -> Match | None:
        with self._lock:
            if datetime.now(timezone.utc) - self._loaded_at >= timedelta(hours=24) or not self._cache:
                client = self.factory.configured()
                body = client.get(COMPANY_TICKERS)
                self._cache = parse(body if body is not None else b"{}", client.mode())
                self._loaded_at = datetime.now(timezone.utc)
            return self._cache.get(symbol.upper().strip())


def parse(b: bytes, mode: str) -> dict[str, Match]:
    out: dict[str, Match] = {}
    root = json.loads(b)
    for n in (root.values() if isinstance(root, dict) else []):
        n = n if isinstance(n, dict) else {}
        t = as_string(n.get("ticker")).upper()
        if not t:
            continue
        out.setdefault(t, Match(t, pad_cik(as_string(n.get("cik_str"))), as_string(n.get("title")),
                                f"SEC company_tickers.json ({mode})"))
    return out
