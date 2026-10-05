"""Manage the research universe: add, edit, remove/restore, change ticker, delete unused companies."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date

import math

from fastapi import APIRouter
from pydantic import BaseModel

from ..errors import BadRequest, NotFound, Unavailable
from ..events.vocabulary import product_for_industry
from ..expand import MAX_PER_JOB, SECTOR_REVIEW_TAG, clean_candidates
from ..jobs import Jobs
from .. import sectors
from ..rows import camel, camel_all
from ..sec import CompanyDiscovery, SecTickerLookup
from ..sec.discovery import DEFAULT_EXCHANGES
from ..sec.profile import CompanyProfiler
from ..settings import settings
from ..sql import db
from ..universe import UniverseService

router = APIRouter(prefix="/api/admin/universe")
_lookup = SecTickerLookup()
_discovery = CompanyDiscovery()
TIINGO_FREE_PER_HOUR = 50


@router.get("")
def list_universe():
    u = UniverseService()
    companies = camel_all(db().all("""
        SELECT c.id,
               (SELECT symbol FROM ticker_history t WHERE t.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
               (SELECT string_agg(symbol, ', ' ORDER BY valid_from) FROM ticker_history t
                 WHERE t.company_id = c.id AND t.valid_to IS NOT NULL) AS former_symbols,
               (SELECT cik FROM cik_mapping m WHERE m.company_id = c.id AND m.valid_to IS NULL LIMIT 1) AS cik,
               c.name, c.sector, c.industry, c.benchmark_symbol,
               EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = c.id AND m.valid_to IS NULL) AS active,
               (SELECT max(valid_from) FROM universe_membership m WHERE m.company_id = c.id) AS member_since,
               (SELECT max(valid_to) FROM universe_membership m WHERE m.company_id = c.id) AS removed_on,
               (SELECT count(*) FROM price_bar p WHERE p.company_id = c.id) AS price_count,
               (SELECT max(trade_date) FROM price_bar p WHERE p.company_id = c.id) AS last_price_date,
               (SELECT count(*) FROM filing f WHERE f.company_id = c.id) AS filing_count,
               (SELECT count(*) FROM forecast f WHERE f.company_id = c.id) AS forecast_count
        FROM company c ORDER BY active DESC, symbol"""))
    tags = u.tags_by_company()
    for c in companies:
        if c["active"]:
            c["removedOn"] = None
        c["deletable"] = c["priceCount"] + c["filingCount"] + c["forecastCount"] == 0
        c["tags"] = tags.get(c["id"], [])
    industries = set(db().scalars("SELECT DISTINCT industry FROM company WHERE industry IS NOT NULL"))
    industries |= {"SEMICONDUCTORS", "SEMICONDUCTOR_EQUIPMENT", "CONSUMER_ELECTRONICS", "NETWORKING_HARDWARE", "AUTOS"}
    industries = sorted(industries)
    return {"universe": u.universe_name(), "companies": companies, "benchmarks": u.benchmark_symbols(),
            "sectors": sorted(db().scalars("SELECT DISTINCT sector FROM company")), "industries": industries,
            "productIndustries": [i for i in industries if product_for_industry(i) is not None],
            "tags": camel_all(u.all_tags())}


@router.get("/lookup")
def lookup(symbol: str):
    """CIK and registrant name for a ticker from SEC company_tickers.json."""
    try:
        m = _lookup.lookup(symbol)
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"SEC lookup unavailable: {e}") from e
    if m is None:
        raise NotFound(f"{symbol.upper()} is not in SEC company_tickers.json; enter the CIK manually")
    return {"symbol": m.symbol, "cik": m.cik, "name": m.name, "source": m.source}


@router.get("/enrich")
def enrich(symbol: str):
    """Everything EDGAR knows about a ticker, plus a suggested sector / benchmark ETF, to prefill the add form."""
    sym = symbol.upper().strip()
    if not sym:
        raise BadRequest("symbol is required")
    p = CompanyProfiler(db(), _lookup)
    out = camel(asdict(p.profile(sym)))
    out["priceProviderEnabled"] = settings().prices.enabled
    out["sectorBenchmarks"] = dict(sectors.SECTOR_ETF)    # every sector the form can offer, with its benchmark ETF
    return out


# ------------------------------------------------------------------ expansion (breadth)
def plan_notes(n_new: int) -> list[str]:
    """What adding `n_new` symbols means for the price provider's quota and for pipeline time."""
    out = []
    p = settings().prices
    if n_new > 0 and p.enabled and p.provider.lower() == "tiingo":
        runs = math.ceil(n_new / TIINGO_FREE_PER_HOUR)
        out.append(f"Tiingo's free plan allows {TIINGO_FREE_PER_HOUR} requests an hour, so {n_new} new symbols take about "
                   f"{runs} hourly price syncs; each sync stops at the quota and the next one continues (check the plan's "
                   f"monthly symbol limit too).")
    elif n_new > 0 and not p.enabled:
        out.append("No price provider is configured: the new companies need a prices CSV import before they get forecasts.")
    if n_new > 0:
        out.append("Every pipeline run grows with the universe (roughly 5-10 seconds per stock for the walk-forward evaluation "
                   "and the strategy lab). The first SEC ingest of a company takes from seconds to several minutes depending on "
                   "its filing history: the next pipeline run after a large batch takes hours.")
    return out


@router.get("/discover")
def discover(exchanges: str = ",".join(DEFAULT_EXCHANGES), minPublicFloat: float = 2e9, limit: int = 100):
    """Candidates from SEC data: listed on `exchanges` (comma-separated), public float at or above `minPublicFloat`
    US dollars, largest first, not yet in the database. The client sends the chosen rows back to POST /expand."""
    ex = [e for e in (x.strip() for x in exchanges.split(",")) if e]
    if not ex:
        raise BadRequest("exchanges is required, e.g. Nasdaq,NYSE")
    if minPublicFloat < 0:
        raise BadRequest("minPublicFloat must be at least 0")
    if not 1 <= limit <= MAX_PER_JOB:
        raise BadRequest(f"limit must be between 1 and {MAX_PER_JOB}")
    if not settings().sec.configured:
        raise Unavailable("SEC_USER_AGENT is not set; discovery reads SEC EDGAR")
    known = set(db().scalars("SELECT cik FROM cik_mapping"))
    try:
        d = _discovery.candidates(ex, minPublicFloat, limit, exclude_ciks=known)
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"SEC discovery unavailable: {e}") from e
    return {"candidates": [camel(asdict(c)) for c in d.candidates], "matched": d.matched, "listed": d.listed,
            "alreadyTracked": d.already_tracked, "frames": d.frames, "exchanges": ex, "minPublicFloat": minPublicFloat,
            "limit": limit, "notes": d.notes + plan_notes(len(d.candidates)), "sectorReviewTag": SECTOR_REVIEW_TAG}


class ExpandIn(BaseModel):
    candidates: list[dict] | None = None     # rows from /discover (symbol and cik are used; name is a fallback)
    tag: str | None = None                   # one tag for the whole batch, e.g. "nasdaq-large"
    tags: list[str] | None = None
    memberSince: date | None = None
    ingestSec: bool | None = False      # inline ingest blocks the worker for hours on a large batch; the pipeline does it anyway
    syncPrices: bool | None = True


@router.post("/expand")
def expand(body: ExpandIn):
    """Queues one UNIVERSE_EXPAND job that adds every candidate with a sector suggested from its SIC code."""
    cands = clean_candidates(body.candidates)
    if body.memberSince and body.memberSince > date.today():
        raise BadRequest("memberSince cannot be in the future")
    if not settings().sec.configured:
        raise Unavailable("SEC_USER_AGENT is not set; the expansion profiles each company on SEC EDGAR")
    jobs = Jobs()
    if jobs.running("UNIVERSE_EXPAND"):
        raise BadRequest("an expansion is already queued or running; wait for it to finish")
    tags = list(body.tags or []) + ([body.tag] if body.tag else [])
    params = {"candidates": cands, "tags": tags, "memberSince": body.memberSince.isoformat() if body.memberSince else None,
              "ingestSec": bool(body.ingestSec), "syncPrices": body.syncPrices is not False}
    return {"job": camel(jobs.submit("UNIVERSE_EXPAND", params)), "count": len(cands), "notes": plan_notes(len(cands))}


class AddIn(BaseModel):
    symbol: str | None = None
    name: str | None = None
    cik: str | None = None
    sector: str | None = None
    industry: str | None = None
    benchmarkSymbol: str | None = None
    memberSince: date | None = None
    ingestSec: bool | None = None
    syncPrices: bool | None = None
    tags: list[str] | None = None        # user-defined categories, e.g. ["AI", "China exposed"]


@router.post("/companies")
def add(body: AddIn):
    u = UniverseService()
    cid = u.add(body.symbol, body.name, body.cik, body.sector, body.industry, body.benchmarkSymbol, body.memberSince,
                tags=body.tags)
    sym = body.symbol.upper().strip()
    bench = body.benchmarkSymbol.upper().strip()
    jobs: list[dict] = []
    nxt: list[str] = []
    if body.syncPrices and settings().prices.enabled:
        jobs.append(camel(Jobs().submit("PRICE_SYNC", {"reason": f"{sym} added"})))
    else:
        nxt.append(f"Import daily prices for {sym} (at least ~6 months of history is needed for features).")
        if db().scalar("SELECT count(*) FROM price_bar WHERE company_id IS NULL AND symbol = :b", b=bench) == 0:
            nxt.append(f"Import prices for benchmark {bench} too (none loaded yet).")
    if body.ingestSec:
        jobs.append(camel(Jobs().submit("SEC_INGEST", {"symbol": sym, "companyId": cid})))
    else:
        nxt.append("Ingest SEC filings (Data & pipeline → SEC ingest) so fundamentals and exposures are available.")
    return {"id": cid, "symbol": sym, "jobs": jobs, "nextSteps": nxt}


class EditIn(BaseModel):
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    benchmarkSymbol: str | None = None
    tags: list[str] | None = None        # omitted: unchanged; []: cleared


@router.put("/companies/{company_id}")
def edit(company_id: int, body: EditIn):
    u = UniverseService()
    u.edit(company_id, body.name, body.sector, body.industry, body.benchmarkSymbol)
    out = {"id": company_id, "updated": True}
    if body.tags is not None:
        out["tags"] = u.set_tags(company_id, body.tags)
    return out


class TagsIn(BaseModel):
    tags: list[str] | None = None


@router.put("/companies/{company_id}/tags")
def set_tags(company_id: int, body: TagsIn):
    """Replaces a company's user-defined tags (an empty list clears them)."""
    return {"id": company_id, "tags": UniverseService().set_tags(company_id, body.tags or [])}


class EffectiveIn(BaseModel):
    effectiveDate: date | None = None


@router.post("/companies/{company_id}/remove")
def remove(company_id: int, body: EffectiveIn | None = None):
    UniverseService().remove(company_id, body.effectiveDate if body else None)
    return {"id": company_id, "active": False}


@router.post("/companies/{company_id}/restore")
def restore(company_id: int, body: EffectiveIn | None = None):
    UniverseService().restore(company_id, body.effectiveDate if body else None)
    return {"id": company_id, "active": True}


class TickerIn(BaseModel):
    symbol: str | None = None
    effectiveDate: date | None = None


@router.post("/companies/{company_id}/ticker")
def ticker(company_id: int, body: TickerIn):
    UniverseService().change_ticker(company_id, body.symbol, body.effectiveDate)
    return {"id": company_id, "symbol": (body.symbol or "").upper().strip()}


@router.delete("/companies/{company_id}")
def delete(company_id: int):
    UniverseService().delete(company_id)
    return {"id": company_id, "deleted": True}

