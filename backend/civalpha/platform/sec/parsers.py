"""Parsers for SEC EDGAR resources: submissions (filing index), companyfacts (XBRL JSON), XBRL instances, and the
filing directory listing used to find a filing's XBRL instance."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from lxml import etree

NEW_YORK = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------------------------------- records
@dataclass(frozen=True)
class FilingMeta:
    accession_no: str
    form: str
    filing_date: date | None
    report_date: date | None
    accepted_at: datetime
    primary_document: str | None
    items: str | None

    @property
    def is_periodic(self) -> bool:
        return self.form.startswith("10-K") or self.form.startswith("10-Q")

    @property
    def is_amendment(self) -> bool:
        return self.form.endswith("/A")

    @property
    def is_annual(self) -> bool:
        return self.form.startswith("10-K")


@dataclass(frozen=True)
class FactRow:
    """One reported XBRL value. dimensions is empty for the consolidated (non-dimensional) value."""
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    filed: date
    accession_no: str
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def dims_key(self) -> str:
        if not self.dimensions:
            return ""
        return "|".join(f"{k}={v}" for k, v in sorted(self.dimensions.items()))


@dataclass(frozen=True)
class FilePage:
    """An older page of the filing index (filings.files[]), fetched from https://data.sec.gov/submissions/{name}."""
    name: str
    filing_from: date | None
    filing_to: date | None


@dataclass(frozen=True)
class Submissions:
    cik: str
    name: str
    tickers: list[str]
    filings: list[FilingMeta]
    older_pages: list[FilePage]


# ---------------------------------------------------------------------------------------------------- JSON helpers
def _json(b: bytes | str):
    return json.loads(b, parse_float=Decimal)


def as_string(v, default: str = "") -> str:
    """Jackson asString(): missing/null -> default, scalars -> their text."""
    if v is None:
        return default
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return default
    return str(v)


def _text(v) -> str | None:
    return None if v is None else as_string(v)


def _date(v) -> date | None:
    s = _text(v)
    return None if s is None or not s.strip() else date.fromisoformat(s)


def _at(arr, i: int):
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _as_int(v) -> int:
    """Jackson asInt(): numbers are truncated, numeric text is parsed, anything else is 0."""
    try:
        return int(Decimal(str(v)))
    except (ArithmeticError, ValueError):
        return 0


# ---------------------------------------------------------------------------------------------------- submissions
def parse_submissions(b: bytes) -> Submissions:
    """Parses https://data.sec.gov/submissions/CIK##########.json ("filings.recent" column arrays)."""
    root = _obj(_json(b))
    filings = _obj(root.get("filings"))
    out = _columns(_obj(filings.get("recent")))
    tickers = [as_string(t) for t in _list(root.get("tickers"))]
    pages = [FilePage(as_string(_obj(f).get("name")), _date(_obj(f).get("filingFrom")), _date(_obj(f).get("filingTo")))
             for f in _list(filings.get("files"))]
    return Submissions(as_string(root.get("cik")), as_string(root.get("name")), tickers, out, pages)


def parse_submissions_page(b: bytes) -> list[FilingMeta]:
    """Older pages hold the same column arrays at their top level."""
    return _columns(_obj(_json(b)))


def _columns(r: dict) -> list[FilingMeta]:
    acc = r.get("accessionNumber")
    out = []
    for i in range(len(acc) if isinstance(acc, list) else 0):
        filed = _date(_at(r.get("filingDate"), i))
        accepted = acceptance(_at(r.get("acceptanceDateTime"), i), filed)
        out.append(FilingMeta(as_string(acc[i]), as_string(_at(r.get("form"), i)), filed, _date(_at(r.get("reportDate"), i)),
                              accepted, _text(_at(r.get("primaryDocument"), i)), _text(_at(r.get("items"), i))))
    return out


def end_of_day_new_york(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=NEW_YORK)


def acceptance(v, filed: date | None) -> datetime:
    """EDGAR acceptance time. When it is missing we fall back to the end of the filing date in New York, which is never
    earlier than the true acceptance (conservative for point-in-time use)."""
    s = _text(v)
    if s is not None and s.strip():
        return datetime.fromisoformat(s if s.endswith("Z") or "+" in s else s + "Z")
    return end_of_day_new_york(filed)


# ---------------------------------------------------------------------------------------------------- companyfacts
CONCEPTS = frozenset({
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "CostOfRevenue",
    "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss", "ResearchAndDevelopmentExpense", "InterestExpense",
    "Assets", "Liabilities", "LongTermDebtNoncurrent", "LongTermDebt", "CashAndCashEquivalentsAtCarryingValue",
    "EarningsPerShareDiluted", "EntityCommonStockSharesOutstanding"})


def parse_company_facts(b: bytes, concepts: set[str] | frozenset[str] | None) -> list[FactRow]:
    """Parses https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json for a whitelist of concepts."""
    facts = _obj(_obj(_json(b)).get("facts"))
    out = []
    for tax, tax_node in facts.items():
        for concept, concept_node in _obj(tax_node).items():
            if concepts is not None and concept not in concepts:
                continue
            for unit, entries in _obj(_obj(concept_node).get("units")).items():
                for e in _list(entries):
                    if not isinstance(e, dict) or e.get("accn") is None or e.get("end") is None or e.get("val") is None:
                        continue
                    out.append(FactRow(tax, concept, unit, Decimal(str(e["val"])),
                                       _date(e.get("start")) if e.get("start") is not None else None,
                                       date.fromisoformat(as_string(e["end"])),
                                       _as_int(e["fy"]) if e.get("fy") is not None else None,
                                       as_string(e["fp"]) if e.get("fp") is not None else None,
                                       as_string(e.get("form")), date.fromisoformat(as_string(e.get("filed"))),
                                       as_string(e["accn"]), {}))
    return out


# ---------------------------------------------------------------------------------------------------- XBRL instance
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
_string_value = etree.XPath("string()")
# java.math.BigDecimal(String) grammar (ASCII digits)
_DECIMAL = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


def _java_trim(s: str) -> str:
    """String.trim(): strips every char <= U+0020."""
    i, j = 0, len(s)
    while i < j and s[i] <= " ":
        i += 1
    while j > i and s[j - 1] <= " ":
        j -= 1
    return s[i:j]


def _read_xml(b: bytes) -> etree._Element:
    """XXE-safe: no DTDs at all (like disallow-doctype-decl), no entity expansion, no network access."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False, remove_comments=False)
    try:
        tree = etree.fromstring(b, parser).getroottree()
    except etree.XMLSyntaxError as e:
        raise ValueError(f"invalid XBRL instance: {e}") from e
    if tree.docinfo.doctype or tree.docinfo.internalDTD is not None or tree.docinfo.externalDTD is not None:
        raise ValueError("invalid XBRL instance: DOCTYPE is disallowed")
    return tree.getroot()


def _child_date(ctx: etree._Element, name: str) -> date | None:
    found = ctx.iter(f"{{{XBRLI}}}{name}")
    el = next(found, None)
    if el is None:
        return None
    s = _java_trim(_string_value(el))
    if len(s) < 10:
        raise ValueError(f"invalid XBRL date: {s!r}")
    return date.fromisoformat(s[:10])


def parse_xbrl_instance(b: bytes, concepts: set[str] | frozenset[str] | None, accession: str, form: str, filed: date) -> list[FactRow]:
    """Extracts numeric facts, including dimensional ones (e.g. srt:StatementGeographicalAxis), from an XBRL instance
    document. companyfacts JSON omits dimensional facts, so geographic and segment revenue comes from here."""
    root = _read_xml(b)
    contexts: dict[str, tuple[date | None, date | None, dict[str, str]]] = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        dims: dict[str, str] = {}
        for m in c.iter(f"{{{XBRLDI}}}explicitMember"):
            dims[m.get("dimension", "")] = _java_trim(_string_value(m))
        start, end, instant = _child_date(c, "startDate"), _child_date(c, "endDate"), _child_date(c, "instant")
        contexts[c.get("id", "")] = (start, end if end is not None else instant, dims)
    units: dict[str, str] = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        measure = next(u.iter(f"{{{XBRLI}}}measure"), None)
        m = _java_trim(_string_value(measure)) if measure is not None else u.get("id", "")
        units[u.get("id", "")] = m[m.index(":") + 1:] if ":" in m else m
    out = []
    for e in root:
        if not isinstance(e.tag, str) or e.get("contextRef") is None:
            continue
        concept = etree.QName(e).localname
        if concepts is not None and concept not in concepts:
            continue
        c = contexts.get(e.get("contextRef"))
        if c is None or c[1] is None:
            continue
        txt = _java_trim(_string_value(e))
        if not _DECIMAL.fullmatch(txt):
            continue
        out.append(FactRow(e.prefix or "unknown", concept, units.get(e.get("unitRef", ""), "pure"), Decimal(txt), c[0], c[1],
                           None, None, form, filed, accession, dict(c[2])))
    return out


# ---------------------------------------------------------------------------------------------------- instance locator
_NOT_INSTANCE = re.compile(r"(_cal|_def|_lab|_pre|_ref)\.xml$|^FilingSummary\.xml$|^MetaLinks|\.xsd$", re.IGNORECASE)


def index_names(index_json: bytes) -> list[str]:
    """Item names of a filing's directory listing (Archives/.../index.json)."""
    items = _obj(_obj(_json(index_json)).get("directory")).get("item")
    return [as_string(_obj(i).get("name")) for i in _list(items)]


def pick_instance(names: list[str], primary_document: str | None) -> str | None:
    """Inline-XBRL filings publish an extracted instance named "*_htm.xml"; older filings ship a standalone instance
    next to the linkbases (_cal/_def/_lab/_pre.xml) and the schema (.xsd), which must be skipped."""
    if primary_document is not None:
        expected = re.sub(r"\.htm$", "_htm.xml", primary_document)
        if expected in names:
            return expected
    for n in names:
        if n.endswith("_htm.xml"):
            return n
    for n in names:
        if n.lower().endswith(".xml") and not _NOT_INSTANCE.search(n):
            return n
    return None


def conventional_instance(primary_document: str) -> str:
    """Conventional name used when no directory listing is available (e.g. fixtures)."""
    return re.sub(r"\.htm$", "_htm.xml", primary_document)
