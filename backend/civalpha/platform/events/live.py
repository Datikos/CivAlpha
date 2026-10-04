"""Official and discovery feeds (all optional, disabled by default):

- Federal Register API (official trade/tariff notices, proclamations, rules)
- Federal Reserve monetary-policy press RSS (FOMC statements; rate change parsed from the statement)
- generic news RSS feeds (discovery only; stored as NEWS_ONLY until an official document is linked)
"""
from __future__ import annotations

import calendar
import html.entities
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

import httpx
from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, ProcessingInstruction, Tag
from defusedxml.ElementTree import fromstring as parse_xml

from .. import http
from ..jobs import Log
from ..settings import Events, settings
from . import signals
from .model import EventDraft, Source, Target

log = logging.getLogger("civalpha.events.live")

_FLAGS = re.IGNORECASE | re.ASCII
TRADE = re.compile(r"tariff|duties|duty|section 301|section 232|export control|import", _FLAGS)
MONETARY = re.compile(r"fomc|federal funds|interest rate|federal reserve", _FLAGS)
FEDERAL_REGISTER = ("https://www.federalregister.gov/api/v1/documents.json?per_page=40&order=newest"
                    "&conditions[term]=tariff&conditions[type][]=RULE&conditions[type][]=PRESDOCU&conditions[type][]=NOTICE")
FED_RSS = "https://www.federalreserve.gov/feeds/press_monetary.xml"


class LiveEventSources:
    def __init__(self, events: Events | None = None, user_agent: str | None = None, client: httpx.Client | None = None):
        s = settings() if events is None or user_agent is None else None
        self.events = events if events is not None else s.events
        ua = user_agent if user_agent is not None else s.sec.user_agent
        self.user_agent = "CivAlpha research" if ua is None or not ua.strip() else ua
        self._client = client

    def collect(self, log_line: Log) -> list[EventDraft]:
        out: list[EventDraft] = []
        if self.events.federal_register_enabled:
            out.extend(self._safe("Federal Register", self.federal_register, log_line))
        if self.events.fed_rss_enabled:
            out.extend(self._safe("Federal Reserve RSS", self.fed_rss, log_line))
        for f in self.events.news_feeds or []:
            if f.strip():
                out.extend(self._safe("news " + f, lambda f=f: self.news(f), log_line))
        return out

    def _safe(self, name: str, source: Callable[[], list[EventDraft]], log_line: Log) -> list[EventDraft]:
        try:
            drafts = source()
            log_line(f"{name}: {len(drafts)} candidate events")
            return drafts
        except Exception as e:  # noqa: BLE001 - one failing feed must not stop the others
            log_line(f"{name}: failed ({_message(e)})")
            log.warning("event source %s failed", name, exc_info=True)
            return []

    def federal_register(self) -> list[EventDraft]:
        body = self._get(FEDERAL_REGISTER)
        out: list[EventDraft] = []
        for r in json.loads(body).get("results") or []:
            title = _str(r.get("title"), "")
            abstract = _str(r.get("abstract"), "")
            if not TRADE.search(title + " " + abstract):
                continue
            url = _str(r.get("html_url"), "")
            pub = _iso_date(_str(r.get("publication_date"), ""))
            # FR public inspection precedes; publication ~8:45 ET
            published = datetime(pub.year, pub.month, pub.day, 13, 45, tzinfo=timezone.utc)
            agencies = r.get("agencies")
            first = agencies[0] if isinstance(agencies, list) and agencies and isinstance(agencies[0], dict) else {}
            agency = _str(first.get("name"), "Federal Register")
            attrs = {"severity": 0.5, "auto_extracted": True, "document_number": _str(r.get("document_number"), "")}
            text = title + ". " + abstract
            content = json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            out.append(EventDraft("TRADE_TARIFF", signals.trade_event_type(text), title, abstract, pub, published, agency, attrs,
                                  signals.trade_targets(text),
                                  Source(url, title, "Federal Register", "OFFICIAL_PRIMARY", published, content, "application/json", False)))
        return out

    def fed_rss(self) -> list[EventDraft]:
        rss = _xml(self._get(FED_RSS))
        out: list[EventDraft] = []
        for item in rss.iter("item"):
            title = _text(_required(item, "title"))
            if "fomc statement" not in title.lower():
                continue
            link = _text(_required(item, "link"))
            published = parse_rfc1123(_text(_required(item, "pubDate")))
            page = self._get(link)
            decision = signals.rate_decision(html_text(page))
            attrs: dict = {}
            if decision is not None:
                attrs["rate_change_bps"] = decision.change_bps
                attrs["target_lower_pct"] = decision.lower
                attrs["target_upper_pct"] = decision.upper
            verb = ("statement" if decision is None
                    else "raised" if decision.change_bps > 0 else "lowered" if decision.change_bps < 0 else "maintained")
            out.append(EventDraft("MONETARY_POLICY", "RATE_DECISION", "FOMC statement: target range " + verb, title,
                                  published.date(), published, "Federal Open Market Committee", attrs,
                                  [Target("INTEREST_RATE", "US_POLICY_RATE", None if decision is None else float(decision.change_bps))],
                                  Source(link, title, "Board of Governors of the Federal Reserve System", "OFFICIAL_PRIMARY", published,
                                         page, "text/html", False)))
        return out

    def news(self, feed_url: str) -> list[EventDraft]:
        rss = _xml(self._get(feed_url))
        out: list[EventDraft] = []
        for item in rss.iter("item"):
            t, dsc = _first(item, "title"), _first(item, "description")
            title = "" if t is None else _text(t)
            desc = "" if dsc is None else html_text(_text(dsc))
            text = title + ". " + desc
            trade = TRADE.search(text) is not None
            if not trade and not MONETARY.search(text):
                continue
            ln = _first(item, "link")
            link = None if ln is None else _text(ln)
            if link is None or not link.strip():
                continue
            pd = _first(item, "pubDate")
            published = datetime.now(timezone.utc) if pd is None else parse_rfc1123(_text(pd))
            out.append(EventDraft("TRADE_TARIFF" if trade else "MONETARY_POLICY",
                                  signals.trade_event_type(text) if trade else "RATE_DECISION",
                                  title, desc, published.date(), published, None, {"severity": 0.3},
                                  signals.trade_targets(text) if trade else [],
                                  Source(link, title, _host(feed_url), "NEWS_DISCOVERY", published, None, None, False)))
        return out

    def _get(self, url: str) -> bytes:
        c = self._client or http.client()
        try:
            r = c.get(url, headers={"User-Agent": self.user_agent})
        finally:
            if self._client is None:
                c.close()
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        return r.content


# ---------------------------------------------------------------------------------------------- parsing helpers

def _message(e: Exception) -> str:
    return str(e) or type(e).__name__


def _str(v, default: str) -> str:
    """JSON value as text; missing or null -> default."""
    if v is None:
        return default
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return default
    return str(v)


def _iso_date(s: str) -> date:
    """LocalDate.parse: strictly yyyy-MM-dd."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
        raise ValueError(f"Text '{s}' could not be parsed as a date")
    return date.fromisoformat(s)


def _host(url: str) -> str | None:
    netloc = urlsplit(url).netloc.rpartition("@")[2]
    if netloc.startswith("["):
        return netloc[:netloc.find("]") + 1] or None
    return netloc.split(":")[0] or None


_CDATA = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.DOTALL)
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_BARE_AMP = re.compile(r"&(?!#[0-9]+;|#[xX][0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)")
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _lenient(chunk: str) -> str:
    """Feeds often use HTML entities (&nbsp;) or a bare '&', which an XML parser rejects but the lenient parser of the
    original code accepted: rewrite HTML named entities as numeric references and escape bare ampersands."""
    def entity(m: re.Match) -> str:
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        return f"&#{cp};" if cp is not None else "&amp;" + name + ";"
    return _ENTITY.sub(entity, _BARE_AMP.sub("&amp;", chunk))


def _xml(body: bytes) -> Element:
    """RSS parsed safely (defusedxml: no entity expansion, no external resources). The body is read as UTF-8."""
    s = body.decode("utf-8", errors="replace").lstrip("﻿")
    parts = _CDATA.split(s)
    s = "".join(p if i % 2 else _lenient(p) for i, p in enumerate(parts))
    return parse_xml(s)


def _first(el: Element, tag: str) -> Element | None:
    """First descendant with exactly this (unprefixed) tag name, like a CSS tag selector on the XML tree."""
    for d in el.iter(tag):
        if d is not el:
            return d
    return None


def _required(el: Element, tag: str) -> Element:
    found = _first(el, tag)
    if found is None:
        raise ValueError(f"RSS item without <{tag}>")
    return found


_WS = re.compile(r"[ \t\n\f\r ]+")


def _normalize(s: str) -> str:
    return _WS.sub(" ", s).strip(" ")


def _text(el: Element) -> str:
    """Element text including descendants, whitespace collapsed and trimmed."""
    return _normalize("".join(el.itertext()))


_BLOCK = frozenset("""address article aside blockquote body br dd details dialog div dl dt fieldset figcaption figure footer form
h1 h2 h3 h4 h5 h6 head header hgroup hr html li main nav ol p pre section summary table tbody td tfoot th thead title tr ul
caption option li""".split())


def html_text(markup: bytes | str) -> str:
    """Visible text of an HTML document or fragment: block elements are separated by a space, inline elements are not,
    whitespace is collapsed (scripts, styles and comments are left out), like jsoup's Document.text()."""
    if isinstance(markup, bytes):
        markup = markup.decode("utf-8", errors="replace")
    soup = BeautifulSoup(markup, "lxml")
    out: list[str] = []

    def walk(node) -> None:
        for child in node.children:
            if isinstance(child, Tag):
                if child.name in ("script", "style"):   # data, not text
                    continue
                block = child.name in _BLOCK
                if block:
                    out.append(" ")
                walk(child)
                if block:
                    out.append(" ")
            elif isinstance(child, NavigableString) and not isinstance(child, (Comment, Doctype, ProcessingInstruction)):
                out.append(str(child))

    walk(soup)
    return _normalize("".join(out))


# ---------------------------------------------------------------------------------------------- RFC 1123 dates

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_RFC1123 = re.compile(
    r"(?:(mon|tue|wed|thu|fri|sat|sun), )?([0-9]+) ([a-z]{3}) ([0-9]+) ([0-9]+):([0-9]+)(?::([0-9]+))? (gmt|[+-][0-9]{2}(?:[0-9]{2})?)",
    re.IGNORECASE | re.ASCII)


def parse_rfc1123(s: str) -> datetime:
    """java.time DateTimeFormatter.RFC_1123_DATE_TIME as ZonedDateTime.parse applies it (case-insensitive, lenient field
    widths, SMART resolver), e.g. "Tue, 3 Jun 2008 11:05:30 GMT" or "... +0200". The day name is optional and must
    match the date, seconds are optional, the zone must be GMT or +HH[MM] (names like "EST" or "Z" are rejected),
    days 29-31 past the month's end are clamped to its last day and 24:00 is midnight of the next day."""
    def fail(why: str = "") -> ValueError:
        return ValueError(f"Text '{s}' could not be parsed as an RFC 1123 date-time{why}")

    m = _RFC1123.fullmatch(s)
    if m is None or m.group(3).lower() not in _MONTHS:
        raise fail()
    year, month, day = int(m.group(4)), _MONTHS[m.group(3).lower()], int(m.group(2))
    hour, minute, second = int(m.group(5)), int(m.group(6)), int(m.group(7) or 0)
    end_of_day = hour == 24 and minute == 0 and second == 0
    if not 1 <= year <= 9999 or not 1 <= day <= 31 or (hour > 23 and not end_of_day) or minute > 59 or second > 59:
        raise fail()
    day = min(day, calendar.monthrange(year, month)[1])
    zone = m.group(8)
    if zone.lower() == "gmt":
        tz = timezone.utc
    else:
        delta = timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5] or 0))
        if int(zone[3:5] or 0) > 59 or delta > timedelta(hours=18):
            raise fail()
        tz = timezone(-delta if zone[0] == "-" else delta)
    dt = datetime(year, month, day, 0 if end_of_day else hour, minute, second, tzinfo=tz)
    if m.group(1) is not None and _DAYS[dt.weekday()] != m.group(1).lower():
        raise fail(f": day of week {m.group(1)} conflicts with {dt.date()}")
    return dt + timedelta(days=1) if end_of_day else dt
