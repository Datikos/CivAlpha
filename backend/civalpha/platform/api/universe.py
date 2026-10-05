"""Manage the research universe: add, edit, remove/restore, change ticker, delete unused companies."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from ..errors import BadRequest, NotFound, Unavailable
from ..events.vocabulary import product_for_industry
from ..jobs import Jobs
from .. import sectors
from ..rows import camel, camel_all
from ..sec import SecTickerLookup
from ..sec.profile import CompanyProfiler
from ..settings import settings
from ..sql import db
from ..universe import UniverseService

router = APIRouter(prefix="/api/admin/universe")
_lookup = SecTickerLookup()


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

