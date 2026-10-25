"""Admin endpoints: every action is queued as a background job (run by the worker) and returns the job row."""
from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from ..errors import BadRequest, NotFound
from ..jobs import Jobs
from ..rows import camel, camel_all
from ..settings import settings
from ..tickers import TickerResolver

router = APIRouter(prefix="/api/admin")


class DateIn(BaseModel):
    asOfDate: date | None = None


class SecIn(BaseModel):
    symbol: str


def _submit(job_type: str, params: dict | None = None) -> dict:
    return camel(Jobs().submit(job_type, params))


@router.get("/jobs")
def jobs():
    return camel_all(Jobs().recent())


@router.post("/pipeline/run")
def pipeline_run():
    return _submit("PIPELINE_RUN")


@router.post("/evaluate")
def evaluate():
    return _submit("EVALUATE")


@router.post("/strategies/backtest")
def strategy_backtest():
    return _submit("STRATEGY_BACKTEST")


@router.post("/strategies/decide")
def strategy_decide(body: DateIn | None = None):
    d = body.asOfDate if body else None
    return _submit("STRATEGY_DECIDE", {"asOfDate": d.isoformat()} if d else {})


@router.post("/doublers/study")
def doubler_study():
    return _submit("DOUBLER_STUDY")


@router.post("/signals/study")
def signal_study():
    return _submit("SIGNAL_STUDY")


@router.post("/ablation/study")
def feature_ablation():
    return _submit("FEATURE_ABLATION")


@router.post("/setups/study")
def setup_study():
    return _submit("SETUP_STUDY")


@router.post("/timemachine")
def time_machine(body: DateIn | None = None):
    if body is None or body.asOfDate is None:
        raise BadRequest("asOfDate is required")
    if body.asOfDate >= date.today():
        raise BadRequest("pick a past date: the time machine compares a forecast with what followed")
    return _submit("TIME_MACHINE", {"asOfDate": body.asOfDate.isoformat()})


@router.post("/forecasts/issue")
def issue_forecasts(body: DateIn | None = None):
    d = body.asOfDate if body else None
    return _submit("ISSUE_FORECASTS", {"asOfDate": d.isoformat()} if d else {})


@router.post("/prices/sync")
def price_sync():
    return _submit("PRICE_SYNC")


@router.post("/outcomes/resolve")
def resolve_outcomes():
    return _submit("RESOLVE_OUTCOMES")


@router.post("/sec/ingest")
def sec_ingest(body: SecIn):
    cid = TickerResolver().company_ever(body.symbol)
    if cid is None:
        raise NotFound("unknown symbol")
    return _submit("SEC_INGEST", {"symbol": body.symbol, "companyId": cid})


@router.post("/earnings/ingest")
def earnings_ingest():
    """Read the press-release exhibit of every results 8-K not yet read and classify its guidance tone."""
    return _submit("EARNINGS_INGEST")


@router.post("/insiders/ingest")
def insider_ingest():
    """Load insider transactions (SEC Forms 4) for every tracked company: the quarterly data sets plus recent filings."""
    return _submit("INSIDER_INGEST")


class EventBackfillIn(BaseModel):
    start: date | None = Field(None, alias="from")
    to: date | None = None


@router.post("/events/backfill")
def event_backfill(body: EventBackfillIn | None = None):
    """ADR-0009: load the official policy events since 2019 (or `from`): material Federal Register streams and FOMC rate
    changes. Safe to rerun; events already on record are not stored twice."""
    b = body or EventBackfillIn()
    if b.start is not None and b.to is not None and b.start > b.to:
        raise BadRequest("from must not be after to")
    return _submit("EVENT_BACKFILL", {"from": None if b.start is None else b.start.isoformat(), "to": None if b.to is None else b.to.isoformat()})


@router.post("/company-events/backfill")
def company_events_backfill(body: EventBackfillIn | None = None):
    """ADR-0011: company events from every 8-K since `from` (default: the SEC lookback window), read from the stored filing
    indexes; item 5.02 documents are fetched from EDGAR. Safe to rerun."""
    b = body or EventBackfillIn()
    return _submit("COMPANY_EVENTS_BACKFILL", {"from": None if b.start is None else b.start.isoformat()})


@router.post("/company-events/classify")
def company_events_classify():
    """ADR-0011 decision 9: Claude reads the item 5.02 sections not yet read (one Message Batch at half price), or collects
    the batch already submitted. Needs a configured language model."""
    if not settings().llm.enabled:
        raise BadRequest("no language model is configured (CIVALPHA_LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY)")
    return _submit("COMPANY_EVENTS_CLASSIFY")


@router.post("/prices/import")
async def price_import(file: UploadFile = File(...), provider: str = Form("CSV upload")):
    name = file.filename or "upload.csv"
    uploads = Path(settings().documents_dir).parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / f"{uuid.uuid4().hex}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)[-80:]}"
    path.write_bytes(await file.read())     # the worker imports it and deletes it
    return _submit("PRICE_IMPORT", {"file": name, "path": str(path), "provider": provider})
