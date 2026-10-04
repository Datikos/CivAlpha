"""Ingests one company's SEC filings: filing index (submissions), XBRL facts (companyfacts), primary documents
(source-linked passages), XBRL instances of annual reports (dimensional geographic revenue), then derives exposures.
Idempotent; each fetched resource is stored as a versioned source document."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from ..errors import Problem
from ..exposure import ExposureService
from ..jobs import Log
from ..settings import settings
from ..sql import Db, db, jsonb
from ..storage import DocumentStore, NewDocument
from ..tickers import TickerResolver
from ..universe import UniverseService
from . import passages as passage_extractor
from .client import SecClient, archive_url, company_facts_url, submissions_url
from .parsers import (CONCEPTS, FactRow, FilingMeta, conventional_instance, end_of_day_new_york, index_names, parse_company_facts,
                      parse_submissions, parse_submissions_page, parse_xbrl_instance, pick_instance)

log = logging.getLogger("civalpha.sec")

PERIODIC = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})
RELEVANT_8K_ITEMS = frozenset({"1.01", "2.02", "7.01", "8.01"})
INSTANCE_CONCEPTS = frozenset({"RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"})


@dataclass(frozen=True)
class Result:
    symbol: str
    filings: int
    facts: int
    passages: int
    exposures: int


def _minus_years(d: date, years: int) -> date:
    """LocalDate.minusYears: Feb 29 falls back to Feb 28."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def _relevant_8k(items: str | None) -> bool:
    if items is None:
        return False
    return any(i.strip() in RELEVANT_8K_ITEMS for i in items.split(","))


class FilingIngestionService:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, tickers: TickerResolver | None = None,
                 universe: UniverseService | None = None, exposures: ExposureService | None = None, lookback_years: int | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.tickers = tickers or TickerResolver(self.db)
        self.universe = universe or UniverseService(self.db)
        self.exposures = exposures or ExposureService(self.db)
        self.lookback_years = lookback_years if lookback_years is not None else settings().sec.lookback_years

    def ingest(self, sec: SecClient, company_id: int, demo: bool, log_line: Log) -> Result:
        cik = self.tickers.cik_of(company_id)
        if cik is None:
            raise Problem(f"no CIK for company {company_id}")
        symbol = self.tickers.current_symbol(company_id)
        # 1. filing index
        sub_url = submissions_url(cik)
        sub_bytes = sec.get(sub_url)
        if sub_bytes is None:
            raise Problem(f"SEC submissions not found for CIK {cik}")
        self.docs.store(NewDocument("SEC_API", "SEC EDGAR", sub_url, None, f"Submissions index CIK{cik}", None, "application/json",
                                    sub_bytes, demo))
        sub = parse_submissions(sub_bytes)
        if sub.tickers:
            ch = self.universe.record_observed_ticker(cik, sub.tickers[0], date.today(), "SEC submissions")
            if ch is not None:
                log_line(f"{symbol}: ticker change observed {ch}")
        min_date = date(1990, 1, 1) if demo else _minus_years(date.today(), self.lookback_years)
        everything: list[FilingMeta] = list(sub.filings)
        seen = {f.accession_no for f in everything}
        # "recent" holds at most ~1000 filings; older ones live in paged files that overlap the lookback window
        for page in sub.older_pages:
            if page.filing_to is not None and page.filing_to < min_date:
                continue
            page_url = "https://data.sec.gov/submissions/" + page.name
            body = sec.get(page_url)
            if body is None:
                log_line(f"{symbol}: submissions page {page.name} not found")
                continue
            self.docs.store(NewDocument("SEC_API", "SEC EDGAR", page_url, None, f"Submissions page {page.name}", None,
                                        "application/json", body, demo))
            for f in parse_submissions_page(body):
                if f.accession_no not in seen:
                    seen.add(f.accession_no)
                    everything.append(f)
        wanted = sorted((f for f in everything
                         if f.filing_date is not None and f.filing_date >= min_date
                         and (f.form in PERIODIC or (f.form == "8-K" and _relevant_8k(f.items)))),
                        key=lambda f: f.accepted_at)
        filing_ids: dict[str, int] = {}
        new_filings = 0
        for f in wanted:
            existing = self.db.scalar("SELECT id FROM filing WHERE accession_no = :a", a=f.accession_no)
            if existing is not None:
                filing_ids[f.accession_no] = existing
                continue
            amends = self._amended_accession(company_id, f) if f.is_amendment else None
            filing_ids[f.accession_no] = self.db.scalar("""
                INSERT INTO filing (company_id, cik, accession_no, form_type, period_of_report, filed_date, accepted_at,
                                    primary_document, items, amends_accession, is_demo)
                VALUES (:c, :cik, :a, :form, :p, :fd, :at, :doc, :items, :amends, :demo) RETURNING id""",
                c=company_id, cik=cik, a=f.accession_no, form=f.form, p=f.report_date, fd=f.filing_date, at=f.accepted_at,
                doc=f.primary_document, items=f.items, amends=amends, demo=demo)
            new_filings += 1
        # 2. XBRL facts (point-in-time key: EDGAR acceptance of the reporting filing)
        by_acc = {f.accession_no: f for f in everything}
        cf_url = company_facts_url(cik)
        facts = 0
        cf = sec.get(cf_url)
        if cf is not None:
            self.docs.store(NewDocument("SEC_API", "SEC EDGAR", cf_url, None, f"XBRL company facts CIK{cik}", None, "application/json",
                                        cf, demo))
            rows = [r for r in parse_company_facts(cf, CONCEPTS) if r.filed >= min_date]
            facts = self.insert_facts(company_id, rows, by_acc, filing_ids, cf_url, demo)
        # 3. documents, passages, instance facts, exposures
        passages = expo = 0
        for f in wanted:
            fid = filing_ids[f.accession_no]
            has_doc = self.db.scalar("SELECT source_document_id IS NOT NULL FROM filing WHERE id = :id", id=fid)
            if not has_doc and f.primary_document is not None and f.primary_document.strip():
                url = archive_url(cik, f.accession_no, f.primary_document)
                html = sec.get(url)
                if html is not None:
                    d = self.docs.store(NewDocument("SEC_FILING", "SEC EDGAR", url, f.accession_no, f"{symbol} {f.form} {f.report_date}",
                                                    f.accepted_at, "text/html", html, demo))
                    self.db.execute("UPDATE filing SET source_document_id = :d WHERE id = :id", d=d.id, id=fid)
                    passages += self._insert_passages(fid, html.decode("utf-8", errors="replace"), demo)
                if f.is_annual:
                    inst = archive_url(cik, f.accession_no, self._instance_name(sec, cik, f))
                    xml = sec.get(inst)
                    if xml is not None:
                        self.docs.store(NewDocument("SEC_FILING", "SEC EDGAR", inst, f.accession_no, f"{symbol} {f.form} XBRL instance",
                                                    f.accepted_at, "application/xml", xml, demo))
                        dim_facts = parse_xbrl_instance(xml, INSTANCE_CONCEPTS, f.accession_no, f.form, f.filing_date)
                        facts += self.insert_facts(company_id, dim_facts, by_acc, filing_ids, inst, demo)
                expo += self.exposures.derive_for_filing(fid)
        log_line(f"{symbol}: {new_filings} new filings, {facts} facts, {passages} passages, {expo} exposures ({sec.mode()} mode)")
        return Result(symbol, new_filings, facts, passages, expo)

    def _instance_name(self, sec: SecClient, cik: str, f: FilingMeta) -> str:
        """XBRL instance file name: from the filing's directory listing when available, else the inline-XBRL convention."""
        index = sec.get(archive_url(cik, f.accession_no, "index.json"))
        if index is not None:
            try:
                picked = pick_instance(index_names(index), f.primary_document)
                if picked is not None:
                    return picked
            except Exception as e:  # noqa: BLE001 - an unreadable listing falls back to the convention
                log.warning("unreadable filing index for %s: %r", f.accession_no, e)
        return conventional_instance(f.primary_document)

    def _amended_accession(self, company_id: int, f: FilingMeta) -> str | None:
        """Best-effort link from an amendment to the original filing: same base form and period, filed earlier."""
        return self.db.scalar("""
            SELECT accession_no FROM filing WHERE company_id = :c AND form_type = :form AND period_of_report = :p
              AND accepted_at < :at ORDER BY accepted_at DESC LIMIT 1""",
            c=company_id, form=f.form.replace("/A", ""), p=f.report_date, at=f.accepted_at)

    def insert_facts(self, company_id: int, rows: list[FactRow], by_acc: dict[str, FilingMeta], filing_ids: dict[str, int],
                     url: str, demo: bool) -> int:
        batch = []
        for r in rows:
            f = by_acc.get(r.accession_no)
            accepted = f.accepted_at if f is not None else end_of_day_new_york(r.filed)   # conservative fallback
            batch.append(dict(c=company_id, f=filing_ids.get(r.accession_no), a=r.accession_no, tax=r.taxonomy, con=r.concept,
                              u=r.unit, v=r.value, ps=r.period_start, pe=r.period_end, fy=r.fiscal_year, fp=r.fiscal_period,
                              form=r.form, fd=r.filed, at=accepted, dims=jsonb(r.dimensions or {}), dk=r.dims_key, url=url, demo=demo))
        if not batch:
            return 0
        n = self.db.executemany("""
            INSERT INTO xbrl_fact (company_id, filing_id, accession_no, taxonomy, concept, unit, value, period_start, period_end,
                fiscal_year, fiscal_period, form_type, filed_date, accepted_at, dimensions, dims_key, source_url, is_demo)
            VALUES (:c, :f, :a, :tax, :con, :u, :v, :ps, :pe, :fy, :fp, :form, :fd, :at, CAST(:dims AS jsonb), :dk, :url, :demo)
            ON CONFLICT (accession_no, taxonomy, concept, unit, period_start, period_end, dims_key) DO NOTHING""", batch)
        return max(0, n)

    def _insert_passages(self, filing_id: int, html: str, demo: bool) -> int:
        n = 0
        for p in passage_extractor.extract(html):
            self.db.execute("""
                INSERT INTO filing_passage (filing_id, section, topic, text, char_start, char_end, extraction_method, extractor_version, is_demo)
                VALUES (:f, :s, :t, :x, :cs, :ce, 'RULE_KEYWORD', :v, :demo)""",
                f=filing_id, s=p.section, t=p.topic, x=p.text, cs=p.char_start, ce=p.char_end, v=passage_extractor.VERSION, demo=demo)
            n += 1
        log.debug("filing %s: %s passages", filing_id, n)
        return n
