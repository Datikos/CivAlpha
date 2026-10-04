"""Manage the research universe: add, edit, remove/restore, change ticker, delete unused companies."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from ..errors import NotFound, Unavailable
from ..events.vocabulary import product_for_industry
from ..jobs import Jobs
from ..rows import camel, camel_all
from ..sec import SecTickerLookup
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
    for c in companies:
        if c["active"]:
            c["removedOn"] = None
        c["deletable"] = c["priceCount"] + c["filingCount"] + c["forecastCount"] == 0
    industries = set(db().scalars("SELECT DISTINCT industry FROM company WHERE industry IS NOT NULL"))
    industries |= {"SEMICONDUCTORS", "SEMICONDUCTOR_EQUIPMENT", "CONSUMER_ELECTRONICS", "NETWORKING_HARDWARE", "AUTOS"}
    industries = sorted(industries)
    return {"universe": u.universe_name(), "companies": companies, "benchmarks": u.benchmark_symbols(),
            "sectors": sorted(db().scalars("SELECT DISTINCT sector FROM company")), "industries": industries,
            "productIndustries": [i for i in industries if product_for_industry(i) is not None]}


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


class AddIn(BaseModel):
    symbol: str | None = None
    name: str | None = None
    cik: str | None = None
    sector: str | None = None
    industry: str | None = None
    benchmarkSymbol: str | None = None
    memberSince: date | None = None
    ingestSec: bool | None = None


@router.post("/companies")
def add(body: AddIn):
    u = UniverseService()
    cid = u.add(body.symbol, body.name, body.cik, body.sector, body.industry, body.benchmarkSymbol, body.memberSince)
    sym = body.symbol.upper().strip()
    bench = body.benchmarkSymbol.upper().strip()
    out: dict = {"id": cid, "symbol": sym}
    nxt = [f"Import daily prices for {sym} (at least ~6 months of history is needed for features)."]
    if u.benchmark_symbols() and db().scalar("SELECT count(*) FROM price_bar WHERE company_id IS NULL AND symbol = :b", b=bench) == 0:
        nxt.append(f"Import prices for benchmark {bench} too (none loaded yet).")
    if body.ingestSec:
        out["job"] = camel(Jobs().submit("SEC_INGEST", {"symbol": body.symbol, "companyId": cid}))
    else:
        nxt.append("Ingest SEC filings (Data & pipeline → SEC ingest) so fundamentals and exposures are available.")
    out["nextSteps"] = nxt
    return out


class EditIn(BaseModel):
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    benchmarkSymbol: str | None = None


@router.put("/companies/{company_id}")
def edit(company_id: int, body: EditIn):
    UniverseService().edit(company_id, body.name, body.sector, body.industry, body.benchmarkSymbol)
    return {"id": company_id, "updated": True}


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

