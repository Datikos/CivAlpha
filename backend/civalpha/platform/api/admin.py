"""Admin endpoints: every action is queued as a background job (run by the worker) and returns the job row."""
from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

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


@router.post("/demo/load")
def demo_load():
    return _submit("DEMO_LOAD")


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


@router.post("/prices/import")
async def price_import(file: UploadFile = File(...), provider: str = Form("CSV upload")):
    name = file.filename or "upload.csv"
    uploads = Path(settings().documents_dir).parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / f"{uuid.uuid4().hex}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)[-80:]}"
    path.write_bytes(await file.read())     # the worker imports it and deletes it
    return _submit("PRICE_IMPORT", {"file": name, "path": str(path), "provider": provider})
