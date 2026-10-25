"""The HISTORY_BACKFILL job (ADR-0012): the history universe and its data from 2015, once, resumable.

Steps, in order (params `steps` runs a subset; every step skips what is already stored, so a rerun resumes):
  universe  the yearly top 350 by public float (universe_pit), companies that are not yet in the database added with
            their ticker, sector and benchmark, and membership spans of the universe `history-top350`;
  prices    bars from HISTORY_START for every benchmark ETF and every company with a membership span, up to the first
            stored bar (or to the last trading day of a company that stopped trading);
  filings   10-K, 10-Q and the ingested 8-Ks of the last LOOKBACK_YEARS years, without the language model (ADR-0012
            decision 6: exposures of these filings come from the rules and XBRL only);
  insiders  the SEC insider data sets from 2014 for every company (stored data sets are re-read, not downloaded again);
  events8k  the 8-K company events of ADR-0011 from HISTORY_START;
  earnings  the press-release exhibit of every results 8-K not yet read;
  policy    the official policy events of ADR-0009 from 2015.
Companies of the past are members of the past only: the pipeline, the portfolio and the live forecasts never act on
them; the models learn from them (ADR-0012 decision 9d).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from ..corpactions import to_text
from . import sectors
from .company_events import CompanyEventService
from .earnings import EarningsIngestionService
from .exposure import ExposureService
from .insiders import InsiderIngestionService
from .jobs import Log, Progress
from .llm import LlmProvider
from .market import PriceSyncService
from .market.sync import Target
from .sec import FilingIngestionService, SecClientFactory
from .sec.client import SecClient, archive_url, submissions_url
from .sec.parsers import parse_submissions
from .settings import settings
from .sql import Db, db
from .storage import DocumentStore, NewDocument
from .tickers import TickerResolver, pad_cik
from .universe import EPOCH, UniverseService
from .universe_pit import (FIRST_YEAR, HISTORY_UNIVERSE, MIN_NAME_SIMILARITY, OWNER_START, TickerMatch, clip, cover_symbol,
                           name_similarity, norm_name, reconstitute, spans)

HISTORY_START = date(2015, 1, 2)
LOOKBACK_YEARS = 12              # 2026 - 12 = 2014: the first universe (2015-04) has a year of filings behind it
HISTORY_TAG = "history"
STEPS = ("universe", "prices", "filings", "insiders", "events8k", "earnings", "policy")
# minutes of the first load (job 260, 2026-10-25), rounded: what the progress bar is weighted by
STEP_WEIGHT = {"universe": 6, "prices": 10, "filings": 640, "insiders": 20, "events8k": 30, "earnings": 120, "policy": 5}
TIINGO = "https://api.tiingo.com/tiingo/"
STALE_DAYS = 10                  # a Tiingo ticker whose last bar is older than this has stopped trading
MAX_NAME_HITS = 5                # Tiingo tickers tried per name (a name can carry several: share classes, relistings)


def merge_spans(company_spans: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Overlapping or touching spans as one (a predecessor's and a successor's years of the same stock)."""
    out: list[list[date]] = []
    for a, b in sorted(company_spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


@dataclass
class UniverseSummary:
    years: dict[int, int] = field(default_factory=dict)
    known: int = 0
    added: int = 0
    tickers: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    spans: int = 0


class Tiingo:
    """The two Tiingo calls the history needs: every ticker's metadata (names, for matching) and one ticker's dates."""

    def __init__(self, token: str, http: httpx.Client | None = None):
        self.h = {"Authorization": "Token " + token}
        self.http = http or httpx.Client(timeout=180)
        self._meta: list[dict] | None = None

    def all_meta(self) -> list[dict]:
        if self._meta is None:
            r = self.http.get(TIINGO + "fundamentals/meta", headers=self.h)
            self._meta = r.json() if r.status_code == 200 else []
        return self._meta

    def dates(self, ticker: str) -> tuple[str | None, date | None, date | None]:
        """(name, first bar, last bar), or Nones when Tiingo does not know the ticker."""
        r = self.http.get(TIINGO + f"daily/{ticker.lower()}", headers=self.h)
        if r.status_code != 200:
            return None, None, None
        m = r.json()
        d = lambda s: date.fromisoformat(s[:10]) if s else None  # noqa: E731
        return m.get("name"), d(m.get("startDate")), d(m.get("endDate"))


class HistoryBackfill:
    def __init__(self, database: Db | None = None, sec: SecClient | None = None, tiingo: Tiingo | None = None,
                 docs: DocumentStore | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        self.sec = sec or SecClientFactory().configured()
        self.tiingo = tiingo or Tiingo(settings().prices.tiingo_api_key)
        self.tickers = TickerResolver(self.db)
        self.universe_svc = UniverseService(self.db)
        self.symbols: set[str] = set()

    # ---------------------------------------------------------------- the companies the steps work on
    def companies(self) -> list[dict]:
        """Every company with a membership span (the owner's and the history's), with the symbol to fetch prices under
        and the last trading day of one that stopped trading; only `self.symbols` when given (a rerun of the failed)."""
        rows = self.db.all("""
            SELECT c.id, c.name, c.benchmark_symbol,
                   (SELECT th.symbol FROM ticker_history th WHERE th.company_id = c.id ORDER BY th.valid_from DESC LIMIT 1) AS symbol,
                   (SELECT th.valid_to FROM ticker_history th WHERE th.company_id = c.id ORDER BY th.valid_from DESC LIMIT 1) AS ticker_to
            FROM company c WHERE EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = c.id) ORDER BY c.id""")
        return [r for r in rows if not self.symbols or (r["symbol"] or "").upper() in self.symbols]

    def run(self, log: Log, steps: tuple[str, ...] = STEPS) -> dict:
        """The progress bar moves by each step's share of a first load's time (STEP_WEIGHT), not one sixth each."""
        out: dict = {}
        p = Progress(log, sum(STEP_WEIGHT[s] for s in steps), "history")
        for s in steps:
            p.at(f"history: {s}")
            out[s] = getattr(self, "step_" + s)(p.child())
            for _ in range(STEP_WEIGHT[s]):
                p.step()
        p.finish()
        return out

    # ---------------------------------------------------------------- universe
    def step_universe(self, log: Log) -> UniverseSummary:
        out = UniverseSummary()
        rec = reconstitute(self.sec.get)
        out.years = {y: len(m) for y, m in sorted(rec.members.items())}
        log("history universe: members per reconstitution " + ", ".join(f"{y} {n}" for y, n in out.years.items()))
        by_cik = {pad_cik(r["cik"]): int(r["company_id"]) for r in self.db.all("SELECT cik, company_id FROM cik_mapping")}
        all_spans = spans(rec.members, OWNER_START.year)
        meta_by_name: dict[str, list[dict]] = {}
        for m in self.tiingo.all_meta():
            meta_by_name.setdefault(norm_name(m.get("name")), []).append(m)
        by_company: dict[int, list] = {}
        for i, (cik, company_spans) in enumerate(sorted(all_spans.items()), 1):
            if i % 25 == 0:
                log(f"history universe: {i}/{len(all_spans)} companies")
            cid = by_cik.get(cik)
            last_day = None
            if cid is None:
                cid, last_day, why = self._add_company(cik, rec.names.get(cik, ""), company_spans, meta_by_name, out)
                if cid is None:
                    out.skipped.setdefault(why, []).append(f"{cik} {rec.names.get(cik, '')}")
                    continue
            else:
                out.known += 1
            by_company.setdefault(cid, []).extend(clip(company_spans, last_day))
        for cid, company_spans in by_company.items():
            kept = merge_spans(company_spans)
            with self.db.transaction():
                self.db.execute("DELETE FROM universe_membership WHERE universe = :u AND company_id = :c", u=HISTORY_UNIVERSE, c=cid)
                for a, b in kept:
                    self.db.execute("""INSERT INTO universe_membership (universe, company_id, valid_from, valid_to)
                                       VALUES (:u, :c, :f, :t)""", u=HISTORY_UNIVERSE, c=cid, f=a, t=b)
            out.spans += len(kept)
        skipped = ", ".join(f"{k} {len(v)}" for k, v in sorted(out.skipped.items())) or "none"
        log(f"history universe: {len(all_spans)} companies ({out.known} already stored, {out.added} added); tickers by "
            + (", ".join(f"{k} {v}" for k, v in sorted(out.tickers.items())) or "none") + f"; skipped: {skipped}; {out.spans} spans")
        for why, names in sorted(out.skipped.items()):
            log(f"history universe: skipped ({why}): " + "; ".join(names[:60]) + (" ..." if len(names) > 60 else ""))
        return out

    def _add_company(self, cik: str, frame_name: str, company_spans, meta_by_name, out: UniverseSummary):
        """(company id, last trading day or None, reason when skipped)."""
        url = submissions_url(cik)
        body = self.sec.get(url)
        if body is None:
            return None, None, "no SEC index"
        self.docs.store(NewDocument("SEC_API", "SEC EDGAR", url, None, f"Submissions index CIK{cik}", None, "application/json", body))
        sub = parse_submissions(body)
        name = sub.name or frame_name
        sug = sectors.suggest(sub.sic, sub.sic_description)
        if sug is None:
            return None, None, "no sector for the SIC code"
        first = min(a for a, _ in company_spans)
        names = [name, *sub.former_names]
        m = self._ticker(cik, names, sub, first, meta_by_name)
        if m.ticker is None:
            return None, None, "no ticker"
        stopped = m.end if m.end is not None and m.end < date.today() - timedelta(days=STALE_DAYS) else None
        valid_to = stopped + timedelta(days=1) if stopped else None
        holder = self.db.one("""SELECT th.company_id, c.name FROM ticker_history th JOIN company c ON c.id = th.company_id
                                WHERE th.symbol = :s AND (th.valid_to IS NULL OR th.valid_to > :f)
                                  AND (CAST(:t AS date) IS NULL OR th.valid_from < :t) LIMIT 1""",
                             s=m.ticker.upper(), f=EPOCH, t=valid_to)
        if holder is not None:
            # a merger or re-domicile: the stock went on under a new SEC registrant that is already stored (Medtronic Inc ->
            # Medtronic plc, the old Walt Disney Co -> today's): the predecessor's years count for the successor
            if max(name_similarity(n, holder["name"]) for n in names) >= MIN_NAME_SIMILARITY:
                out.tickers["successor"] = out.tickers.get("successor", 0) + 1
                return int(holder["company_id"]), stopped, ""
            return None, None, "ticker held by another stored company"
        with self.db.transaction():
            cid = self.db.scalar("INSERT INTO company (name, sector, industry, benchmark_symbol) VALUES (:n, :s, :i, :b) RETURNING id",
                                 n=name, s=sug.sector, i=sug.industry, b=sug.benchmark_symbol)
            self.db.execute("INSERT INTO cik_mapping (company_id, cik, valid_from, source) VALUES (:c, :k, :f, 'history')",
                            c=cid, k=cik, f=EPOCH)
            self.db.execute("""INSERT INTO ticker_history (company_id, symbol, valid_from, valid_to, source)
                               VALUES (:c, :s, :f, :t, :src)""", c=cid, s=m.ticker.upper(), f=EPOCH, t=valid_to, src=f"history:{m.how}")
            self.universe_svc.set_tags(cid, [HISTORY_TAG])
        out.tickers[m.how] = out.tickers.get(m.how, 0) + 1
        out.added += 1
        return cid, stopped, ""

    def _ticker(self, cik: str, names: list[str], sub, first_member: date, meta_by_name) -> TickerMatch:
        """The index's ticker, else the latest 10-K cover page's, else a Tiingo name match on the current or a former
        name; each checked against Tiingo: a similar name, and trading on the company's first membership day. Several
        name matches are each checked; the first that trades then is taken."""
        tried: list[tuple[str, str]] = [(t, "index") for t in sub.tickers[:2]]
        cover = self._cover_symbol(cik, sub)
        if cover:
            tried.append((cover, "cover"))
        for n in names:
            for hit in meta_by_name.get(norm_name(n), [])[:MAX_NAME_HITS]:
                if hit.get("ticker"):
                    tried.append((hit["ticker"], "name"))
        seen: set[str] = set()
        for t, how in tried:
            if t.lower() in seen:
                continue
            seen.add(t.lower())
            tname, start, end = self.tiingo.dates(t)
            if tname is None or start is None:
                continue
            if how != "name" and max(name_similarity(n, tname) for n in names) < MIN_NAME_SIMILARITY:
                continue
            if start <= first_member + timedelta(days=31) and (end is None or end >= first_member):
                return TickerMatch(t.upper(), how, tname, start, end)
        return TickerMatch(None, "none")

    def _cover_symbol(self, cik: str, sub) -> str | None:
        tenk = [f for f in sub.filings if f.form == "10-K" and f.primary_document]
        if not tenk:
            return None
        f = max(tenk, key=lambda x: x.accepted_at)
        body = self.sec.get(archive_url(cik, f.accession_no, f.primary_document))
        return cover_symbol(to_text(body)) if body else None

    # ---------------------------------------------------------------- data
    def step_prices(self, log: Log):
        targets = [(Target(None, b), None) for b in self.universe_svc.benchmark_symbols()]
        for c in self.companies():
            end = c["ticker_to"] - timedelta(days=1) if c["ticker_to"] else None
            targets.append((Target(int(c["id"]), c["symbol"]), end))
        return PriceSyncService(self.db).backfill(targets, HISTORY_START, log)

    def step_filings(self, log: Log):
        svc = FilingIngestionService(self.db, exposures=ExposureService(self.db, llm=LlmProvider()), lookback_years=LOOKBACK_YEARS)
        cs = self.companies()
        p = Progress(log, len(cs), "history filings")
        failed = 0
        for i, c in enumerate(cs, 1):
            p.step(f"history filings {c['name']} ({i}/{len(cs)})")
            try:
                svc.ingest(self.sec, int(c["id"]), p.child())
            except Exception as e:  # noqa: BLE001 - one company must not stop the rest
                failed += 1
                log(f"history filings: {c['symbol']} failed ({type(e).__name__}: {str(e)[:120]})")
        log(f"history filings: {len(cs)} companies, {failed} failed")
        return {"companies": len(cs), "failed": failed}

    def step_insiders(self, log: Log):
        r = InsiderIngestionService(self.db, lookback_years=LOOKBACK_YEARS).ingest(self.sec, log, companies=self.companies(), reread=True)
        return {"datasets": r.datasets, "filings": r.filings, "transactions": r.transactions}

    def step_events8k(self, log: Log):
        r = CompanyEventService(self.db).update(self.sec, log, since=HISTORY_START, with_pages=True, companies=self.companies())
        return {"filings": r.filings, "events": r.events, "fetched": r.fetched, "failed": r.failed}

    def step_earnings(self, log: Log):
        svc = EarningsIngestionService(self.db)
        total = failed = 0
        for c in self.companies():
            try:
                total += svc.ingest(self.sec, int(c["id"]), lambda _l: None).releases
            except Exception as e:  # noqa: BLE001
                failed += 1
                log(f"history earnings: {c['symbol']} failed ({str(e)[:120]})")
        log(f"history earnings: {total} releases read, {failed} companies failed")
        return {"releases": total, "failed": failed}

    def step_policy(self, log: Log):
        from .events.backfill import EventBackfill
        s = EventBackfill(self.db).run(log, start=date(FIRST_YEAR, 1, 1) - timedelta(days=365))
        return {"created": s.created, "existing": s.existing}


def run_job(params: dict, log: Log) -> None:
    steps = tuple(s for s in (params.get("steps") or STEPS) if s in STEPS)
    h = HistoryBackfill()
    h.symbols = {str(x).upper() for x in params.get("symbols") or []}
    if h.symbols:
        log("history: only " + ", ".join(sorted(h.symbols)))
    out = h.run(log, steps)
    u = out.get("universe")
    if u is not None:
        log("history universe summary: " + json.dumps({"years": u.years, "known": u.known, "added": u.added, "tickers": u.tickers,
                                                       "skipped": {k: len(v) for k, v in u.skipped.items()}, "spans": u.spans}))
