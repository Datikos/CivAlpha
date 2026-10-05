"""Insider transactions (SEC Forms 4): what officers, directors and 10% owners bought and sold in their own company.

Two sources, both public:
  * the SEC's quarterly insider-transactions data sets (one zip per calendar quarter with every Form 3/4/5 as TSV
    tables), read for the lookback window and filtered to the universe's issuer CIKs: the history;
  * each company's Form 4 XML filings newer than the latest data set (from the submissions index the filing
    ingestion already fetches), so the signal does not lag a quarter.

Only non-derivative transactions are stored (common stock, not options), with their code: P (open-market purchase) and
S (open-market sale) are the informative ones; grants (A), exercises (M), tax withholding (F) and gifts (G) are kept for
the record but not counted as a signal. `available_at` is the end of the filing day in New York: the data sets carry
no acceptance time, and the end of the day is never earlier than the true acceptance (safe for point-in-time use).
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree as ET

from .jobs import Log
from .sec.client import SecClient, archive_url
from .sec.parsers import end_of_day_new_york, parse_submissions
from .sql import Db, db
from .storage import DocumentStore, NewDocument
from .tickers import TickerResolver, pad_cik
from .universe import UniverseService

DATASET_URLS = ("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{name}.zip",
                "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{name}.zip")
SIGNAL_CODES = frozenset({"P", "S"})
RELATIONSHIPS = ("Officer", "Director", "TenPercentOwner", "Other")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


@dataclass(frozen=True)
class Transaction:
    accession_no: str
    trans_sk: str
    owner_cik: str | None
    owner_name: str
    relationship: str
    title: str | None
    trans_date: date
    filed_date: date
    trans_code: str
    acquired: bool
    shares: float
    price: float | None
    shares_after: float | None
    ownership: str | None
    security_title: str | None
    issuer_cik: str


@dataclass(frozen=True)
class Result:
    datasets: int
    filings: int
    transactions: int


def dataset_name(year: int, quarter: int) -> str:
    return f"{year}q{quarter}_form345"


def quarters_back(today: date, years: int) -> list[str]:
    """Data-set names from the quarter `years` ago up to the last completed quarter, oldest first."""
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(years * 4 + 1):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(dataset_name(y, q))
    return list(reversed(out))


def _date(s: str | None) -> date | None:
    """Data-set dates look like 30-MAY-2025; XML dates like 2025-05-30."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})-([A-Z]{3})-(\d{4})", s.upper())
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _num(s) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _relationship(rel: str | None, is_director=None, is_officer=None, is_ten=None) -> str:
    r = (rel or "").replace(" ", "")
    if is_officer or "Officer" in r:
        return "Officer"
    if is_director or "Director" in r:
        return "Director"
    if is_ten or "TenPercent" in r:
        return "TenPercentOwner"
    return "Other"


def parse_dataset(zip_bytes: bytes, issuer_ciks: set[str]) -> list[Transaction]:
    """Non-derivative transactions of Forms 4 (and 4/A) whose issuer is one of `issuer_ciks` (10-digit)."""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))

    def rows(name: str):
        with z.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")

    subs: dict[str, tuple[str, date]] = {}
    for r in rows("SUBMISSION.tsv"):
        cik = pad_cik(r.get("ISSUERCIK") or "")
        filed = _date(r.get("FILING_DATE"))
        if cik in issuer_ciks and (r.get("DOCUMENT_TYPE") or "").startswith("4") and filed is not None:
            subs[r["ACCESSION_NUMBER"]] = (cik, filed)
    if not subs:
        return []
    owners: dict[str, tuple[str | None, str, str, str | None]] = {}
    for r in rows("REPORTINGOWNER.tsv"):
        a = r.get("ACCESSION_NUMBER")
        if a in subs and a not in owners:        # the first reporting owner names the filing (joint filings are rare)
            owners[a] = (pad_cik(r.get("RPTOWNERCIK") or "") or None, (r.get("RPTOWNERNAME") or "").strip(),
                         _relationship(r.get("RPTOWNER_RELATIONSHIP")), (r.get("RPTOWNER_TITLE") or "").strip() or None)
    out = []
    for r in rows("NONDERIV_TRANS.tsv"):
        a = r.get("ACCESSION_NUMBER")
        if a not in subs:
            continue
        cik, filed = subs[a]
        td = _date(r.get("TRANS_DATE"))
        shares = _num(r.get("TRANS_SHARES"))
        code = (r.get("TRANS_CODE") or "").strip()
        if td is None or shares is None or not code:
            continue
        o = owners.get(a, (None, "Unknown", "Other", None))
        out.append(Transaction(a, str(r.get("NONDERIV_TRANS_SK")), o[0], o[1] or "Unknown", o[2], o[3], td, filed, code,
                               (r.get("TRANS_ACQUIRED_DISP_CD") or "").strip().upper() == "A", shares,
                               _num(r.get("TRANS_PRICEPERSHARE")), _num(r.get("SHRS_OWND_FOLWNG_TRANS")),
                               (r.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip() or None,
                               (r.get("SECURITY_TITLE") or "").strip() or None, cik))
    return out


def _text(e, path: str) -> str | None:
    n = e.find(path)
    if n is None:
        return None
    v = n.find("value")
    t = (v.text if v is not None else n.text) or ""
    return t.strip() or None


def parse_form4_xml(xml: bytes, accession_no: str, filed: date) -> list[Transaction]:
    """The ownershipDocument of a Form 4: reporting owner and non-derivative transactions."""
    root = ET.fromstring(xml)
    issuer = pad_cik(_text(root, "issuer/issuerCik") or "")
    ro = root.find("reportingOwner")
    owner_cik = owner_name = title = None
    rel = "Other"
    if ro is not None:
        owner_cik = pad_cik(_text(ro, "reportingOwnerId/rptOwnerCik") or "") or None
        owner_name = _text(ro, "reportingOwnerId/rptOwnerName")
        r = ro.find("reportingOwnerRelationship")
        if r is not None:
            flag = lambda p: (_text(r, p) or "").lower() in ("1", "true")  # noqa: E731
            rel = _relationship(None, flag("isDirector"), flag("isOfficer"), flag("isTenPercentOwner"))
            title = _text(r, "officerTitle")
    out = []
    for i, t in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        td = _date(_text(t, "transactionDate"))
        shares = _num(_text(t, "transactionAmounts/transactionShares"))
        code = _text(t, "transactionCoding/transactionCode")
        if td is None or shares is None or not code:
            continue
        out.append(Transaction(accession_no, f"xml-{i}", owner_cik, owner_name or "Unknown", rel, title, td, filed, code,
                               (_text(t, "transactionAmounts/transactionAcquiredDisposedCode") or "").upper() == "A", shares,
                               _num(_text(t, "transactionAmounts/transactionPricePerShare")),
                               _num(_text(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                               _text(t, "ownershipNature/directOrIndirectOwnership"), _text(t, "securityTitle"), issuer))
    return out


def xml_document_name(primary_document: str | None) -> str | None:
    """Submissions point at the rendered form (xslF345X05/wk-form4_1.xml); the raw XML is the same name without the folder."""
    if not primary_document:
        return None
    name = primary_document.split("/")[-1]
    return name if name.lower().endswith(".xml") else None


class InsiderIngestionService:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, tickers: TickerResolver | None = None,
                 universe: UniverseService | None = None, lookback_years: int = 6):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.tickers = tickers or TickerResolver(self.db)
        self.universe = universe or UniverseService(self.db)
        self.lookback_years = lookback_years

    def ingest(self, sec: SecClient, log: Log, today: date | None = None) -> Result:
        today = today or date.today()
        companies = self.universe.companies()
        by_cik = {}
        for c in companies:
            cik = self.tickers.cik_of(c["id"])
            if cik:
                by_cik[pad_cik(cik)] = int(c["id"])
        n_sets = n_filings = n_rows = 0
        latest_set_end: date | None = None
        for name in quarters_back(today, self.lookback_years):
            if self.db.scalar("SELECT exists(SELECT 1 FROM source_document WHERE source_type = 'SEC_INSIDER_DATASET' AND title = :t)", t=name):
                latest_set_end = _quarter_end(name)
                continue
            body = url = None
            for template in DATASET_URLS:
                url = template.format(name=name)
                body = sec.get(url)
                if body is not None:
                    break
            if body is None:
                log(f"insiders: data set {name} not available yet")
                continue
            doc = self.docs.store(NewDocument("SEC_INSIDER_DATASET", "SEC EDGAR", url, None, name, None, "application/zip", body))
            rows = parse_dataset(body, set(by_cik))
            inserted = self._store(rows, by_cik, name, doc.id)
            n_sets += 1
            n_rows += inserted
            latest_set_end = _quarter_end(name)
            log(f"insiders: data set {name}: {len(rows)} transactions for tracked companies, {inserted} new")
        # the gap after the latest data set: each company's recent Form 4 filings from its submissions index
        for cik, cid in by_cik.items():
            try:
                f, r = self._recent_form4(sec, cik, cid, latest_set_end)
                n_filings += f
                n_rows += r
            except Exception as e:  # noqa: BLE001 - one company must not stop the rest
                log(f"insiders: {self.tickers.current_symbol(cid)}: Form 4 fetch failed ({e})")
        log(f"insiders: {n_sets} data sets loaded, {n_filings} recent Form 4 filings read, {n_rows} transactions stored")
        return Result(n_sets, n_filings, n_rows)

    def _recent_form4(self, sec: SecClient, cik: str, cid: int, after: date | None) -> tuple[int, int]:
        doc = self.docs.latest_by_locator(f"https://data.sec.gov/submissions/CIK{cik}.json", None)
        body = self.docs.content(doc) if doc is not None else None
        if body is None:
            body = sec.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if body is None:
            return 0, 0
        sub = parse_submissions(body)
        filings = rows = 0
        for f in sub.filings:
            if f.form not in ("4", "4/A") or f.filing_date is None or (after is not None and f.filing_date <= after):
                continue
            if self.db.scalar("SELECT exists(SELECT 1 FROM insider_transaction WHERE accession_no = :a)", a=f.accession_no):
                continue
            name = xml_document_name(f.primary_document)
            if name is None:
                continue
            url = archive_url(cik, f.accession_no, name)
            xml = sec.get(url)
            if xml is None:
                continue
            d = self.docs.store(NewDocument("SEC_FILING", "SEC EDGAR", url, f.accession_no, f"Form {f.form} {f.accession_no}",
                                            f.accepted_at, "application/xml", xml))
            trans = parse_form4_xml(xml, f.accession_no, f.filing_date)
            filings += 1
            rows += self._store(trans, {cik: cid}, "form4-xml", d.id)
        return filings, rows

    def _store(self, rows: list[Transaction], by_cik: dict[str, int], source: str, doc_id: int | None) -> int:
        n = 0
        with self.db.transaction():
            for t in rows:
                cid = by_cik.get(t.issuer_cik)
                if cid is None:
                    continue
                n += self.db.execute("""
                    INSERT INTO insider_transaction (company_id, accession_no, trans_sk, owner_cik, owner_name, relationship, title,
                        trans_date, filed_date, available_at, trans_code, acquired, shares, price, shares_after, ownership,
                        security_title, source, source_document_id)
                    VALUES (:c, :a, :sk, :oc, :on, :rel, :title, :td, :fd, :at, :code, :acq, :sh, :pr, :after, :own, :sec, :src, :doc)
                    ON CONFLICT (accession_no, trans_sk) DO NOTHING""",
                    c=cid, a=t.accession_no, sk=t.trans_sk, oc=t.owner_cik, on=t.owner_name, rel=t.relationship, title=t.title,
                    td=t.trans_date, fd=t.filed_date, at=end_of_day_new_york(t.filed_date), code=t.trans_code, acq=t.acquired,
                    sh=t.shares, pr=t.price, after=t.shares_after, own=t.ownership, sec=t.security_title, src=source, doc=doc_id)
        return n


def _quarter_end(name: str) -> date:
    m = re.fullmatch(r"(\d{4})q([1-4])_form345", name)
    y, q = int(m.group(1)), int(m.group(2))
    return date(y, q * 3, [31, 30, 30, 31][q - 1])
