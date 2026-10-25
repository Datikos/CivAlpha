"""Company events from 8-K item codes (ADR-0011).

The SEC's filing index (the submissions documents the SEC step stores for every company) lists each 8-K's items and its
acceptance time. Items 1.01, 1.02, 1.03, 1.05, 2.01, 2.05, 2.06, 3.01, 4.01 and 4.02 become events from the index alone.
Item 5.02 (departure or appointment of officers and directors) is read: the keyword rule `exec-1` marks a departure word
within WINDOW characters of the chief executive's or the chief financial officer's title. The rule was fixed in ADR-0011
before any outcome was looked at.

Events live in `company_event`, not in `filing`: the passage rules, the exposure derivation and the corporate-action
detector see no new 8-Ks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..corpactions import to_text
from .jobs import Log, Progress
from .sec.client import SecClient, archive_url, submissions_url
from .sec.parsers import FilingMeta, parse_submissions, parse_submissions_page
from .settings import settings
from .sql import Db, db
from .storage import DocumentStore, NewDocument
from .tickers import TickerResolver
from .universe import UniverseService

ITEM_KINDS = {"1.01": "MATERIAL_AGREEMENT", "1.02": "AGREEMENT_TERMINATED", "1.03": "BANKRUPTCY", "1.05": "CYBER_INCIDENT",
              "2.01": "ACQUISITION_COMPLETED", "2.05": "RESTRUCTURING", "2.06": "IMPAIRMENT", "3.01": "LISTING_NOTICE",
              "4.01": "AUDITOR_CHANGE", "4.02": "NON_RELIANCE"}
DISTRESS = ("BANKRUPTCY", "RESTRUCTURING", "IMPAIRMENT", "LISTING_NOTICE", "AUDITOR_CHANGE", "NON_RELIANCE", "CYBER_INCIDENT")
EXEC_ITEM = "5.02"
EXEC_KINDS = ("CEO_DEPARTURE", "CFO_DEPARTURE")
OTHER_5_02 = "OFFICER_DIRECTOR_CHANGE"
RULE = "exec-1"
WINDOW = 300                 # characters between a departure word and the title
EVIDENCE_CHARS = 300
NIGHTLY_DAYS = 30            # the pipeline adds 8-Ks accepted in the last 30 days; the backfill job reads the history

_FLAGS = re.IGNORECASE
_DEPART = re.compile(r"\bresign|\bretire|\bstep(?:s|ped|ping)?\s+down|\bdepart|\bterminat|\bseparat|\bcease[sd]?\s+to\s+serve"
                     r"|\bwill\s+not\s+(?:stand|seek)\s+(?:for\s+)?re-?election|\btransition(?:s|ed|ing)?\s+from", _FLAGS)
_ROLES = {"CEO_DEPARTURE": re.compile(r"chief\s+executive\s+officer|\bCEO\b|principal\s+executive\s+officer", _FLAGS),
          "CFO_DEPARTURE": re.compile(r"chief\s+financial\s+officer|\bCFO\b|principal\s+financial\s+officer", _FLAGS)}
_ITEM_HEAD = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", _FLAGS)
# the item's own title ("Departure of Directors or Certain Officers; ...") carries a departure word: never read
_TITLE = re.compile(r"Departure\s+of\s+Directors\s+or\s+(?:Certain|Principal)\s+Officers", _FLAGS)
_SIGNATURE = re.compile(r"\bSIGNATURES?\b")


def item_section(text: str, item: str = EXEC_ITEM) -> str:
    """The text of one item: from its heading to the next item heading or the signature block; empty when absent."""
    heads = [(m.start(), m.group(1)) for m in _ITEM_HEAD.finditer(text)]
    starts = [s for s, i in heads if i == item]
    if not starts:
        return ""
    start = starts[0]
    nxt = [s for s, i in heads if s > start and i != item]
    end = nxt[0] if nxt else len(text)
    sig = _SIGNATURE.search(text, start)
    if sig and sig.start() < end:
        end = sig.start()
    return text[start:end]


def exec_kinds(section: str) -> list[tuple[str, str]]:
    """(kind, evidence) for each executive whose departure the section reports: a departure word within WINDOW characters
    of the title, either side."""
    out = []
    section = _TITLE.sub(" ", section)
    departs = [m.start() for m in _DEPART.finditer(section)]
    for kind, role in _ROLES.items():
        for m in role.finditer(section):
            near = [d for d in departs if abs(d - m.start()) <= WINDOW]
            if near:
                lo = max(0, min(near[0], m.start()) - 60)
                out.append((kind, re.sub(r"\s+", " ", section[lo:lo + EVIDENCE_CHARS]).strip()[:EVIDENCE_CHARS]))
                break
    return out


def item_set(items: str | None) -> set[str]:
    return {i.strip() for i in (items or "").split(",") if i.strip()}


@dataclass
class Summary:
    filings: int = 0
    events: dict[str, int] = field(default_factory=dict)
    fetched: int = 0
    failed: int = 0


class CompanyEventService:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, tickers: TickerResolver | None = None,
                 universe: UniverseService | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.tickers = tickers or TickerResolver(self.db)
        self.universe = universe or UniverseService(self.db)

    def update(self, sec: SecClient, log: Log, since: date | None = None, with_pages: bool = False,
               companies: list[dict] | None = None) -> Summary:
        """Events of the 8-Ks accepted on or after `since` (default: NIGHTLY_DAYS ago) for every member (or `companies`),
        from the stored filing index (`with_pages`: also its stored older pages, for the history)."""
        since = since or date.today() - timedelta(days=NIGHTLY_DAYS)
        out = Summary()
        companies = companies if companies is not None else self.universe.companies()
        p = Progress(log, len(companies), "8-K events")
        for i, c in enumerate(companies, 1):
            cid = int(c["id"])
            p.step(f"8-K events {c['name']} ({i}/{len(companies)})")
            cik = self.tickers.cik_of(cid)
            if not cik:
                continue
            for f in self._index(cik, with_pages):
                if f.form != "8-K" or f.accepted_at is None or f.accepted_at.date() < since:
                    continue
                try:
                    self._one(sec, cid, cik, f, out)
                except Exception as e:  # noqa: BLE001 - one filing must not stop the rest
                    out.failed += 1
                    log(f"8-K events: {f.accession_no} failed ({type(e).__name__}: {str(e)[:120]})")
        p.finish()
        made = ", ".join(f"{k} {v}" for k, v in sorted(out.events.items())) or "none"
        log(f"8-K events since {since}: {out.filings} filings read, new events {made}; {out.fetched} documents fetched, "
            f"{out.failed} failed")
        return out

    def _index(self, cik: str, with_pages: bool) -> list[FilingMeta]:
        doc = self.docs.latest_by_locator(submissions_url(cik), None)
        body = self.docs.content(doc) if doc is not None else None
        if body is None:
            return []
        sub = parse_submissions(body)
        filings = list(sub.filings)
        if with_pages:
            seen = {f.accession_no for f in filings}
            for page in sub.older_pages:
                pd_ = self.docs.latest_by_locator("https://data.sec.gov/submissions/" + page.name, None)
                pb = self.docs.content(pd_) if pd_ is not None else None
                for f in parse_submissions_page(pb) if pb else []:
                    if f.accession_no not in seen:
                        seen.add(f.accession_no)
                        filings.append(f)
        return filings

    def _one(self, sec: SecClient, cid: int, cik: str, f: FilingMeta, out: Summary) -> None:
        items = item_set(f.items)
        wanted = [i for i in items if i in ITEM_KINDS or i == EXEC_ITEM]
        if not wanted or self.db.scalar("SELECT exists(SELECT 1 FROM company_event WHERE accession_no = :a)", a=f.accession_no):
            return
        out.filings += 1
        rows: list[tuple[str, str, str, str | None, int | None]] = [(i, ITEM_KINDS[i], "ITEM", None, None) for i in items if i in ITEM_KINDS]
        if EXEC_ITEM in items:
            text, doc_id = self._text(sec, cik, f, out)
            if text is None:
                if not rows:
                    return              # no document yet: the 5.02 is read on a later run
            else:
                kinds = exec_kinds(item_section(text)) or [(OTHER_5_02, None)]
                rows += [(EXEC_ITEM, k, RULE, ev, doc_id) for k, ev in kinds]
        with self.db.transaction():
            for item, kind, method, evidence, doc_id in rows:
                n = self.db.execute("""
                    INSERT INTO company_event (company_id, accession_no, item, kind, accepted_at, filed_date, method, evidence, source_document_id)
                    VALUES (:c, :a, :i, :k, :at, :fd, :m, :ev, :d) ON CONFLICT (accession_no, kind) DO NOTHING""",
                                    c=cid, a=f.accession_no, i=item, k=kind, at=f.accepted_at, fd=f.filing_date or f.accepted_at.date(),
                                    m=method, ev=evidence, d=doc_id)
                if n:
                    out.events[kind] = out.events.get(kind, 0) + 1

    def _text(self, sec: SecClient, cik: str, f: FilingMeta, out: Summary) -> tuple[str | None, int | None]:
        """The 8-K's primary document as text: from storage when the SEC step already stored it, else from EDGAR."""
        if not f.primary_document:
            return None, None
        url = archive_url(cik, f.accession_no, f.primary_document)
        doc = self.docs.latest_by_locator(url, None)
        body = self.docs.content(doc) if doc is not None else None
        if body is None:
            body = sec.get(url)
            if body is None:
                return None, None
            out.fetched += 1
            doc = self.docs.store(NewDocument("SEC_FILING", "SEC EDGAR", url, f.accession_no, f"8-K {f.accession_no}", f.accepted_at,
                                              "text/html", body))
        return to_text(body), doc.id


def backfill_job(params: dict, log: Log) -> Summary:
    """ADR-0011: every 8-K of the lookback window (SEC_LOOKBACK_YEARS) from the stored filing indexes."""
    from .sec import SecClientFactory
    from .sec.ingestion import _minus_years
    since = date.fromisoformat(params["from"]) if params.get("from") else _minus_years(date.today(), settings().sec.lookback_years)
    return CompanyEventService().update(SecClientFactory().configured(), log, since=since, with_pages=True)
