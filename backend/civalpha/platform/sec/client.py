"""Fetching SEC EDGAR resources by their canonical URL: live HTTPS (rate-limited) or a local fixture directory."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

import httpx

from .. import http
from ..errors import Problem
from ..settings import Sec, settings

log = logging.getLogger("civalpha.sec")

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{}.json"
COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"


def submissions_url(cik: str) -> str:
    return SUBMISSIONS.format(cik)


def company_facts_url(cik: str) -> str:
    return COMPANY_FACTS.format(cik)


def archive_url(cik: str, accession: str, document: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"


class SecClient(Protocol):
    """get() returns the body bytes, or None when the resource does not exist (HTTP 404 / missing fixture)."""

    def get(self, url: str) -> bytes | None: ...

    def mode(self) -> str: ...


class RateLimiter:
    """Minimal blocking limiter: at most `permits_per_second` acquisitions per second across threads."""

    def __init__(self, permits_per_second: float):
        if permits_per_second <= 0 or permits_per_second > 10:
            raise ValueError(f"SEC fair access allows at most 10 requests/second; got {float(permits_per_second)}")
        self._interval = int(1_000_000_000 / permits_per_second)
        self._next = 0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic_ns()
            slot = max(now, self._next)
            self._next = slot + self._interval
            wait = slot - now
        if wait > 0:
            time.sleep(wait / 1e9)


_UA = re.compile(r".+\s+\S+@\S+\.\S+", re.ASCII)


class LiveSecClient:
    """SEC EDGAR over HTTPS, following the SEC's automated-access rules: a declared User-Agent naming the requester and
    a contact e-mail, at most 10 requests/second (default 5), gzip, and back-off on 429/503."""

    def __init__(self, user_agent: str | None, max_rps: float, client: httpx.Client | None = None):
        if user_agent is None or not _UA.fullmatch(user_agent.strip()):
            raise Problem("SEC_USER_AGENT must identify you, e.g. 'CivAlpha research jane@example.com' (SEC automated access policy)")
        self.user_agent = user_agent.strip()
        self.limiter = RateLimiter(max_rps)
        self.http = client or http.client(timeout=60.0)

    def mode(self) -> str:
        return "live"

    def get(self, url: str) -> bytes | None:
        for attempt in range(4):
            self.limiter.acquire()
            try:
                # httpx decodes Content-Encoding: gzip itself
                res = self.http.get(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}, timeout=60.0)
            except httpx.RequestError as e:
                log.warning("SEC request error for %s: %r", url, e)
                continue
            s = res.status_code
            if s == 200:
                return res.content
            if s == 404:
                return None
            if s in (429, 503):
                backoff = 2 ** (attempt + 1) * 1000
                log.warning("SEC returned %s for %s; backing off %s ms", s, url, backoff)
                time.sleep(backoff / 1000)
                continue
            raise Problem(f"SEC request failed: HTTP {s} for {url}")
        raise Problem(f"SEC request failed after retries: {url}")


class FixtureSecClient:
    """Serves SEC URLs from a local directory laid out like the SEC hosts:
    data.sec.gov/submissions/... -> submissions/..., data.sec.gov/api/xbrl/companyfacts/... -> companyfacts/...,
    www.sec.gov/Archives/... -> Archives/..., www.sec.gov/files/... -> files/...
    Used for the demo dataset and for offline tests; parsing code is identical to live mode."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def mode(self) -> str:
        return "fixture"

    def get(self, url: str) -> bytes | None:
        p = self.resolve(url)
        if p is None or not p.is_file():
            return None
        return p.read_bytes()

    def resolve(self, url: str) -> Path | None:
        u = urlsplit(url)
        host, path = u.hostname, unquote(u.path)
        if host == "data.sec.gov" and path.startswith("/submissions/"):
            rel = path[1:]
        elif host == "data.sec.gov" and path.startswith("/api/xbrl/companyfacts/"):
            rel = "companyfacts/" + path[len("/api/xbrl/companyfacts/"):]
        elif host == "www.sec.gov" and (path.startswith("/Archives/") or path.startswith("/files/")):
            rel = path[1:]
        else:
            return None
        root = Path(os.path.normpath(self.root))
        p = Path(os.path.normpath(root / rel))
        return p if p == root or root in p.parents else None


class SecClientFactory:
    def __init__(self, sec: Sec | None = None, client: httpx.Client | None = None):
        self.sec = sec or settings().sec
        self.client = client
        self._live: LiveSecClient | None = None
        self._lock = threading.Lock()

    def configured(self) -> SecClient:
        """The configured client: live EDGAR (requires SEC_USER_AGENT) or fixtures."""
        if self.sec.live:
            if self._live is None:
                with self._lock:
                    if self._live is None:
                        self._live = LiveSecClient(self.sec.user_agent, self.sec.max_requests_per_second, self.client)
            return self._live
        return self.fixture(self.sec.fixture_dir)

    def fixture(self, directory: str | os.PathLike) -> SecClient:
        return FixtureSecClient(directory)
