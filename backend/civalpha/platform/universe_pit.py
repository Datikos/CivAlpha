"""The history universe (ADR-0012): which companies were the 350 largest, year by year, as it could be known then.

Rule (fixed in ADR-0012 before anything was measured): on the first NYSE session of April of year Y the universe becomes
the TOP_N largest filers by public float among the floats measured in the twelve months to 30 June of year Y-1 (the SEC
XBRL frames CY{Y-2}Q3I to CY{Y-1}Q2I, newest value per company), each checked against total assets and the price per
share it implies (sec/discovery.plausible). A float as of 30 June is in the 10-K filed by about March, so it is known on
1 April. Membership is stored as spans of the universe HISTORY_UNIVERSE that end at the next reconstitution a company
misses, at its last trading day, or on OWNER_START, the day the owner's universe begins; the owner's spans are never
touched.

Tickers: the SEC index's current tickers, else the trading symbol on the company's own latest 10-K cover page, else a
unique Tiingo name match; a ticker is kept only if Tiingo's trading dates cover the company's membership.

Amendment 1 (2026-10-27): two checks the first load lacked. A float is a scale error when it is more than
SCALE_TO_CAP times shares outstanding times the close on the float date *and* more than SCALE_TO_ASSETS times total
assets (`scale_error`; the next-ranked company takes the place). A history membership ends after the first close below
MIN_CLOSE, the exchanges' minimum bid (`listing_cut`): a bankrupt company's stock goes on trading over the counter.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .catchup import is_session
from .sec.discovery import ASSETS_FRAME, PUBLIC_FLOAT_FRAME, SHARES_FRAME, parse_float_frame, plausible
from .tickers import pad_cik

TOP_N = 350
FIRST_YEAR = 2015
HISTORY_UNIVERSE = "history-top350"
OWNER_START = date(2026, 10, 5)          # the owner's universe of 352 begins; the history universe ends here
MIN_NAME_SIMILARITY = 0.5
RESERVE = 60                             # ranks past TOP_N read, to fill the places of floats found to be scale errors
# Amendment 1. Measured on the first load: the 49 scale-error member-years had a float 81 to 5,952 times shares x close
# (most about 1,000); the highest legitimate ratio was 9.7 (NVIDIA's pre-split share count). The assets condition keeps
# a right float whose shares frame is wrong (ResMed 2022, Linde 2019: ratio over 900, float under 7 times assets).
SCALE_TO_CAP = 20.0
SCALE_TO_ASSETS = 10.0
CLOSE_WINDOW_DAYS = 190                  # the close used for the check is the nearest within this of the float date
MIN_CLOSE = 1.0                          # dollars: the NYSE and Nasdaq minimum bid for continued listing

_STOP = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|companies|ltd|limited|plc|holdings?|group|the|nv|sa|lp|"
                   r"llc|trust|class [a-z]|de|new)\b")


def reconstitution_day(year: int) -> date:
    """The first NYSE session on or after 1 April."""
    d = date(year, 4, 1)
    while not is_session(d):
        d += timedelta(days=1)
    return d


def frames_for(year: int) -> list[str]:
    """The quarterly frames whose floats are known on 1 April of `year`: the twelve months to 30 June of year - 1."""
    y = year - 1
    return [f"CY{y}Q2I", f"CY{y}Q1I", f"CY{y - 1}Q4I", f"CY{y - 1}Q3I"]


def frame_url(name: str, template: str) -> str:
    m = re.fullmatch(r"CY(\d{4})Q([1-4])I", name)
    return template.format(year=m.group(1), quarter=m.group(2))


def newest(frames: list[dict[str, tuple[float, date | None]]]) -> dict[str, tuple[float, date | None]]:
    out: dict[str, tuple[float, date | None]] = {}
    for f in frames:
        for cik, (v, end) in f.items():
            if cik not in out or (end or date.min) > (out[cik][1] or date.min):
                out[cik] = (v, end)
    return out


def top_floats(floats: dict, assets: dict, shares: dict, n: int = TOP_N) -> list[tuple[str, float]]:
    """The `n` largest plausible floats, largest first: (cik, float)."""
    ok = [(cik, v) for cik, (v, _end) in floats.items()
          if v > 0 and plausible(v, (assets.get(cik) or (None,))[0], (shares.get(cik) or (None,))[0])]
    return sorted(ok, key=lambda x: -x[1])[:n]


def spans(members: dict[int, set[str]], last_year: int, end_cap: date = OWNER_START) -> dict[str, list[tuple[date, date]]]:
    """Membership spans per CIK from the members of each reconstitution year: consecutive years merge; a span ends at the
    reconstitution the company misses, or at `end_cap` after the last year."""
    out: dict[str, list[tuple[date, date]]] = {}
    years = sorted(y for y in members if y <= last_year)
    for cik in sorted(set().union(*(members[y] for y in years)) if years else []):
        start = None
        for y in years:
            inside = cik in members[y]
            if inside and start is None:
                start = reconstitution_day(y)
            if not inside and start is not None:
                out.setdefault(cik, []).append((start, reconstitution_day(y)))
                start = None
        if start is not None:
            out.setdefault(cik, []).append((start, end_cap))
    return out


def clip(company_spans: list[tuple[date, date]], last_trading_day: date | None) -> list[tuple[date, date]]:
    """Spans cut at the day after the last trading day (a company that stopped trading is no longer a member)."""
    if last_trading_day is None:
        return company_spans
    stop = last_trading_day + timedelta(days=1)
    return [(a, min(b, stop)) for a, b in company_spans if a < stop]


def norm_name(s: str | None) -> str:
    """Lower case without legal forms; Tiingo's share-class suffix (" - Series A") dropped; split initials joined
    ("V F CORP" and "VF Corp" are one name)."""
    s = re.split(r"\s+-\s+", s or "")[0]
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and "))
    s = re.sub(r"\s+", " ", _STOP.sub(" ", s)).strip()
    return re.sub(r"\b([a-z0-9]) (?=[a-z0-9]\b)", r"\1", s)


def name_similarity(a: str | None, b: str | None) -> float:
    x, y = set(norm_name(a).split()), set(norm_name(b).split())
    return len(x & y) / len(x | y) if x and y else 0.0


def cover_symbol(text: str) -> str | None:
    """The first trading symbol on a 10-K cover page (required there since 2019): the table "Title of each class |
    Trading Symbol(s) | Name of each exchange" lists the common stock first."""
    head = text[:20000]
    m = re.search(r"Trading\s+Symbol", head)
    if not m:
        return None
    window = head[m.end():m.end() + 600]
    for t in re.findall(r"\b([A-Z]{1,5}(?:[.\-][A-Z])?)\b", window):
        if t not in {"NYSE", "LLC", "INC", "NASDAQ", "THE", "NA", "N", "A", "LLP", "USD", "ADR", "ADS", "NAME", "EACH", "CLASS"}:
            return t
    return None


@dataclass
class TickerMatch:
    ticker: str | None
    how: str                        # index | cover | name | none
    tiingo_name: str | None = None
    start: date | None = None
    end: date | None = None
    note: str = ""


@dataclass
class Candidate:
    cik: str
    float: float
    end: date | None                # the float's date
    shares: float | None
    assets: float | None


@dataclass
class Reconstitution:
    members: dict[int, set[str]] = field(default_factory=dict)       # year -> CIKs (the top n, before the price check)
    names: dict[str, str] = field(default_factory=dict)              # CIK -> entity name in the frames
    floats: dict[int, dict[str, float]] = field(default_factory=dict)
    ranked: dict[int, list[Candidate]] = field(default_factory=dict)  # year -> the top n + RESERVE, largest first


def scale_error(c: Candidate, close: float | None) -> bool:
    """True when the float cannot be right next to the market value it implies; False when it can or cannot be checked
    (no shares or no close). Without an assets frame the market-value condition decides alone."""
    if not c.shares or not close or close <= 0:
        return False
    return c.float > SCALE_TO_CAP * c.shares * close and (not c.assets or c.float > SCALE_TO_ASSETS * c.assets)


def checked_members(rec: Reconstitution, close_on, n: int = TOP_N) -> tuple[dict[int, set[str]], dict[int, list[str]]]:
    """Members per year after the price check: `close_on(cik, day, year) -> float | None` is the close nearest the
    float date; a scale error is passed over and the next-ranked candidate moves up. Returns (members, rejected CIKs)."""
    members: dict[int, set[str]] = {}
    rejected: dict[int, list[str]] = {}
    for y, cands in sorted(rec.ranked.items()):
        keep: list[str] = []
        for c in cands:
            if len(keep) == n:
                break
            if scale_error(c, close_on(c.cik, c.end, y) if c.end else None):
                rejected.setdefault(y, []).append(c.cik)
            else:
                keep.append(c.cik)
        members[y] = set(keep)
    return members, rejected


def listing_cut(company_spans: list[tuple[date, date]], closes: list[tuple[date, float]]) -> list[tuple[date, date]]:
    """Spans ended after the first session whose close is below MIN_CLOSE (that session's return still counts, as a
    delisting return would); a span whose last close before it is already below is dropped. `closes` sorted by day."""
    out = []
    for a, b in company_spans:
        before = [c for d, c in closes if d < a]
        if before and before[-1] < MIN_CLOSE:
            continue
        low = next((d for d, c in closes if a <= d < b and c < MIN_CLOSE), None)
        out.append((a, low + timedelta(days=1)) if low else (a, b))
    return out


def reconstitute(get, first_year: int = FIRST_YEAR, last_year: int = OWNER_START.year, n: int = TOP_N,
                 reserve: int = RESERVE) -> Reconstitution:
    """`get(url) -> bytes | None` (the SEC client). Members per reconstitution year, and the ranked candidates with a
    reserve for `checked_members`."""
    out = Reconstitution()
    cache: dict[str, dict] = {}

    def frame(name: str, template: str) -> dict:
        key = template + name
        if key not in cache:
            body = get(frame_url(name, template))
            cache[key] = parse_float_frame(body) if body else {}
            if body and template == PUBLIC_FLOAT_FRAME:
                for r in json.loads(body).get("data") or []:
                    out.names.setdefault(pad_cik(str(r.get("cik"))), str(r.get("entityName") or ""))
        return cache[key]

    for y in range(first_year, last_year + 1):
        names = frames_for(y)
        floats = newest([frame(f, PUBLIC_FLOAT_FRAME) for f in names])
        assets = newest([frame(f, ASSETS_FRAME) for f in names])
        shares = newest([frame(f, SHARES_FRAME) for f in names])
        top = top_floats(floats, assets, shares, n + reserve)
        out.members[y] = {c for c, _ in top[:n]}
        out.floats[y] = dict(top[:n])
        out.ranked[y] = [Candidate(c, v, floats[c][1], (shares.get(c) or (None,))[0], (assets.get(c) or (None,))[0])
                         for c, v in top]
    return out
