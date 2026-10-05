"""Earnings releases: the press-release exhibit (EX-99.1) of every results 8-K (Item 2.02), stored as a source document,
and its guidance tone by keyword rules.

The 8-K itself is a cover; the numbers and the outlook are in Exhibit 99.1. The exhibit's file name varies, so it is
read from the filing's index-headers file (the SGML header lists every document with its TYPE and FILENAME), with a
file-name heuristic over the filing directory as the fallback.

Guidance tone (an ESTIMATED value, method RULE_KEYWORD): RAISED / LOWERED / MAINTAINED when a sentence says the company
raised, lowered or reaffirmed its guidance or outlook; PROVIDED when an outlook is given without such a verb; NONE when
the release has no outlook language. The matched sentence is stored as evidence.
"""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass

from ..earnings import is_results_8k
from .jobs import Log
from .sec.client import SecClient, archive_url
from .sec.parsers import index_names
from .sec.passages import _parse, _remove, element_text
from .sql import Db, db
from .storage import DocumentStore, NewDocument
from .tickers import TickerResolver

EXTRACTOR_VERSION = "guidance-rules-v3"
TONES = ("RAISED", "LOWERED", "MAINTAINED", "PROVIDED", "NONE")
_GUIDE = r"(?:guidance|outlook|forecast)"
# sentences about these are not outlook statements even when they use the same verbs
_NOT_GUIDANCE = re.compile(r"\bdividend|buyback|repurchase|net cash|conference call|webcast|will provide|forward[- ]looking statements\b", re.I)
_SCOPE = r"(?:(?:its|our|the|full[- ]year|fiscal(?: year)?(?: 20\d\d)?|annual|20\d\d|FY ?\d\d|quarterly|revenue|EPS|earnings|sales)\s+){0,4}"
_AFTER = r"(?:\s+(?:range|for [^.]{0,40}?)?\s*(?:was|were|has been|have been|is|are)?\s*)"    # "guidance was raised", "guidance range raised"
RULES: list[tuple[str, re.Pattern]] = [
    ("RAISED", re.compile(rf"\b(?:rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|lift(?:s|ed|ing)?|boost(?:s|ed|ing)?)\s+{_SCOPE}{_GUIDE}"
                          rf"|{_GUIDE}{_AFTER}(?:raised|increased|lifted|boosted)\b"
                          rf"|{_GUIDE}[^.]{{0,80}}\b(?:above|higher than|up from)\s+(?:its |our |the )?(?:prior|previous|earlier)", re.I)),
    ("LOWERED", re.compile(rf"\b(?:lower(?:s|ed|ing)?|reduc(?:e|es|ed|ing)|cut(?:s|ting)?|trim(?:s|med|ming)?)\s+{_SCOPE}{_GUIDE}"
                           rf"|{_GUIDE}{_AFTER}(?:lowered|reduced|cut|trimmed)\b"
                           rf"|{_GUIDE}[^.]{{0,80}}\b(?:below|lower than|down from)\s+(?:its |our |the )?(?:prior|previous|earlier)", re.I)),
    ("MAINTAINED", re.compile(rf"\b(?:reaffirm(?:s|ed|ing)?|maintain(?:s|ed|ing)?|reiterat(?:e|es|ed|ing)|confirm(?:s|ed|ing)?|unchanged)\s*{_SCOPE}{_GUIDE}"
                              rf"|{_GUIDE}{_AFTER}(?:reaffirmed|maintained|reiterated|confirmed|unchanged)\b"
                              rf"|{_GUIDE}\s+(?:remains?|is|was)\s+unchanged", re.I)),
    ("PROVIDED", re.compile(r"\b(?:guidance|outlook)\b|\bexpects?\b[^.]{0,120}\b(?:fiscal|full[- ]year|quarter|20\d\d)\b", re.I)),
]
_SENTENCE = re.compile(r"[^.!?]*[.!?]")
_HEADER_DOC = re.compile(r"(?:<|&lt;)TYPE(?:>|&gt;)\s*(EX-99[^\s<&]*)[\s\S]{0,400}?(?:<|&lt;)FILENAME(?:>|&gt;)\s*([^\s<&]+)", re.I)
_EXHIBIT_NAME = re.compile(r"(?:ex|exhibit)[-_]?99[-_.]?1?\b|[-_]99_?1[-_.]|991", re.I)


@dataclass(frozen=True)
class Guidance:
    tone: str
    evidence: str | None


@dataclass(frozen=True)
class Result:
    symbol: str
    filings: int
    releases: int


def exhibit_from_headers(text: str) -> str | None:
    """The file name of Exhibit 99.1 (or the first EX-99 document) from an index-headers file."""
    best = None
    for m in _HEADER_DOC.finditer(text):
        kind, name = m.group(1).upper(), m.group(2)
        if not name.lower().endswith((".htm", ".html", ".txt")):
            continue
        if kind.startswith("EX-99.1") or kind == "EX-99":
            return name
        best = best or name
    return best


def guess_exhibit(names: list[str]) -> str | None:
    """A press-release exhibit by file name, when the headers do not say."""
    cands = [n for n in names if n.lower().endswith((".htm", ".html")) and _EXHIBIT_NAME.search(n) and not n.lower().startswith("r")]
    return sorted(cands, key=len)[0] if cands else None


def release_text(html: str) -> str:
    doc = _parse(html)
    if doc is None:
        return ""
    _remove(doc)
    return element_text(doc)


def classify_guidance(text: str) -> Guidance:
    """The strongest tone any sentence carries, with that sentence as evidence (RAISED and LOWERED beat MAINTAINED, which
    beats PROVIDED); the forward-looking-statements boilerplate is ignored."""
    body = re.split(r"forward[- ]looking statements", text, maxsplit=1, flags=re.I)[0] or text
    body = html_lib.unescape(body)
    found: dict[str, str] = {}
    for s in _SENTENCE.finditer(body):
        sent = s.group(0).strip()
        if len(sent) < 20 or _NOT_GUIDANCE.search(sent):
            continue
        for tone, rx in RULES:
            m = rx.search(sent)
            if tone not in found and m:
                found[tone] = _snippet(sent, m.start())
    for tone in TONES[:-1]:
        if tone in found:
            return Guidance(tone, found[tone])
    return Guidance("NONE", None)


def _snippet(sentence: str, at: int, width: int = 240) -> str:
    """The clause around the match: headline run-ons (contact lines, tickers) before the last clause break are dropped."""
    head = sentence[:at]
    cut = max(head.rfind("•"), head.rfind(";"), head.rfind(") "), head.rfind(" – "), head.rfind(" — "))
    start = cut + 1 if cut >= 0 and at - cut > 25 else 0
    out = sentence[start:].strip(" •;–—")
    return out if len(out) <= width else out[:width].rsplit(" ", 1)[0] + "…"


class EarningsIngestionService:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, tickers: TickerResolver | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.tickers = tickers or TickerResolver(self.db)

    def reclassify(self, log: Log) -> int:
        """Re-reads the stored exhibits of releases classified by an older rule version with the current rules."""
        rows = self.db.all("""SELECT r.id, r.exhibit_document_id FROM earnings_release r
                              WHERE r.extractor_version <> :v AND r.exhibit_document_id IS NOT NULL""", v=EXTRACTOR_VERSION)
        n = 0
        for r in rows:
            d = self.docs.get(int(r["exhibit_document_id"]))
            body = self.docs.content(d) if d is not None else None
            if body is None:
                continue
            g = classify_guidance(release_text(body.decode("utf-8", errors="replace")))
            self.db.execute("UPDATE earnings_release SET guidance_tone = :t, guidance_text = :e, extractor_version = :v WHERE id = :id",
                            t=g.tone, e=g.evidence, v=EXTRACTOR_VERSION, id=r["id"])
            n += 1
        if n:
            log(f"earnings releases: {n} reclassified with {EXTRACTOR_VERSION}")
        return n

    def ingest(self, sec: SecClient, company_id: int, log: Log) -> Result:
        """Every results 8-K of the company without a stored release: fetch its press-release exhibit and classify it."""
        symbol = self.tickers.current_symbol(company_id)
        cik = self.tickers.cik_of(company_id)
        rows = self.db.all("""SELECT f.id, f.accession_no, f.accepted_at, f.items, f.form_type FROM filing f
                              WHERE f.company_id = :c AND f.form_type = '8-K'
                                AND NOT EXISTS (SELECT 1 FROM earnings_release r WHERE r.filing_id = f.id)
                              ORDER BY f.accepted_at""", c=company_id)
        n = done = 0
        for f in rows:
            if not is_results_8k(f["form_type"], f["items"]):
                continue
            n += 1
            try:
                done += self._one(sec, cik, company_id, f)
            except Exception as e:  # noqa: BLE001 - one release must not stop the rest
                log(f"{symbol}: earnings release {f['accession_no']} failed ({e})")
        if n:
            log(f"{symbol}: {done} of {n} earnings releases read")
        return Result(symbol, n, done)

    def _one(self, sec: SecClient, cik: str, company_id: int, f: dict) -> int:
        acc = f["accession_no"]
        name = None
        hdr = sec.get(archive_url(cik, acc, f"{acc}-index-headers.html"))
        if hdr is not None:
            name = exhibit_from_headers(hdr.decode("utf-8", errors="replace"))
        if name is None:
            index = sec.get(archive_url(cik, acc, "index.json"))
            if index is not None:
                name = guess_exhibit(index_names(index))
        tone, evidence, doc_id = "UNKNOWN", None, None
        if name is not None:
            url = archive_url(cik, acc, name)
            body = sec.get(url)
            if body is not None:
                d = self.docs.store(NewDocument("SEC_FILING", "SEC EDGAR", url, acc, f"Earnings release exhibit {name}", f["accepted_at"],
                                                "text/html", body))
                doc_id = d.id
                g = classify_guidance(release_text(body.decode("utf-8", errors="replace")))
                tone, evidence = g.tone, g.evidence
        self.db.execute("""
            INSERT INTO earnings_release (company_id, filing_id, accession_no, accepted_at, exhibit_name, exhibit_document_id,
                                          guidance_tone, guidance_text, method, extractor_version)
            VALUES (:c, :f, :a, :at, :n, :d, :tone, :ev, 'RULE_KEYWORD', :v) ON CONFLICT (filing_id) DO NOTHING""",
            c=company_id, f=f["id"], a=acc, at=f["accepted_at"], n=name, d=doc_id, tone=tone, ev=evidence, v=EXTRACTOR_VERSION)
        return 1
