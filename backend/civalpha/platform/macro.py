"""Macro series with full vintage history (ALFRED real-time periods), so a forecast for date D only sees values as
they were published on D, not later revisions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus

import httpx

from .errors import Problem
from .http import client
from .market.csv_prices import parse_date
from .settings import Fred, settings
from .sql import Db, db
from .storage import DocumentStore, NewDocument

_LINE_BREAK = re.compile(r"\r\n|[\n\x0b\x0c\r\x85  ]")   # Java's \R


@dataclass(frozen=True)
class Observation:
    series_id: str
    obs_date: date
    value: float | None
    realtime_start: date
    realtime_end: date | None       # None = still current


def parse_csv(csv: str) -> list[Observation]:
    """CSV columns: series_id,obs_date,value,realtime_start,realtime_end (empty end = still current)."""
    out = []
    lines = _LINE_BREAK.split(csv)
    for line in lines[1:]:
        if line.strip() == "":
            continue
        c = line.split(",")
        out.append(Observation(c[0], parse_date(c[1]), None if c[2].strip() == "" or c[2] == "." else float(c[2]),
                               parse_date(c[3]), parse_date(c[4]) if len(c) > 4 and c[4].strip() != "" else None))
    return out


class MacroService:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, fred: Fred | None = None,
                 http: httpx.Client | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.fred = fred if fred is not None else settings().fred
        self.http = http

    parse_csv = staticmethod(parse_csv)

    def upsert_series(self, series_id: str, title: str, units: str | None, frequency: str | None, source: str) -> None:
        self.db.execute("""
            INSERT INTO macro_series (series_id, title, units, frequency, source) VALUES (:id, :t, :u, :f, :s)
            ON CONFLICT (series_id) DO UPDATE SET title = EXCLUDED.title, units = EXCLUDED.units, frequency = EXCLUDED.frequency""",
                        id=series_id, t=title, u=units, f=frequency, s=source)

    def insert(self, obs: list[Observation], demo: bool) -> int:
        n = self.db.executemany("""
            INSERT INTO macro_observation (series_id, obs_date, value, realtime_start, realtime_end, is_demo)
            VALUES (:s, :d, :v, :rs, :re, :demo)
            ON CONFLICT (series_id, obs_date, realtime_start) DO UPDATE SET realtime_end = EXCLUDED.realtime_end""",
                                [{"s": o.series_id, "d": o.obs_date, "v": o.value, "rs": o.realtime_start, "re": o.realtime_end, "demo": demo}
                                 for o in obs])
        return max(0, n)

    def fetch_fred(self, series_id: str) -> int:
        """Fetches all vintages of a FRED series through the ALFRED real-time period parameters. Requires FRED_API_KEY."""
        if self.fred is None or not self.fred.enabled:
            raise Problem("FRED_API_KEY is not set")
        base = self.fred.base_url
        key = quote_plus(self.fred.api_key)
        seriess = json.loads(self._get(f"{base}/series?file_type=json&series_id={series_id}&api_key={key}")).get("seriess")
        meta = seriess[0] if isinstance(seriess, list) and seriess and isinstance(seriess[0], dict) else {}
        self.upsert_series(series_id, _text(meta.get("title"), series_id), _text(meta.get("units"), None),
                           _text(meta.get("frequency"), None), "FRED/ALFRED")
        url = (f"{base}/series/observations?file_type=json&series_id={series_id}"
               f"&realtime_start=1776-07-04&realtime_end=9999-12-31&observation_start=2015-01-01&api_key={key}")
        body = self._get(url)
        # the stored locator omits the API key
        self.docs.store(NewDocument("MACRO", "FRED/ALFRED (Federal Reserve Bank of St. Louis)",
                                    f"{base}/series/observations?series_id={series_id}&realtime_start=1776-07-04&realtime_end=9999-12-31",
                                    None, f"{series_id} all vintages", None, "application/json", body, False))
        obs = []
        for o in json.loads(body).get("observations") or []:
            v = str(o.get("value"))
            end = str(o.get("realtime_end"))
            obs.append(Observation(series_id, date.fromisoformat(str(o.get("date"))), None if v == "." else float(v),
                                   date.fromisoformat(str(o.get("realtime_start"))), None if end == "9999-12-31" else date.fromisoformat(end)))
        return self.insert(obs, False)

    def _get(self, url: str) -> bytes:
        if self.http is not None:
            r = self.http.get(url)
        else:
            with client() as c:
                r = c.get(url)
        if r.status_code != 200:
            raise OSError(f"FRED HTTP {r.status_code}")
        return r.content


def _text(v, default):
    """Like Jackson asString(default): the node's text, the default for missing/null nodes."""
    if v is None:
        return default
    return v if isinstance(v, str) else str(v)
