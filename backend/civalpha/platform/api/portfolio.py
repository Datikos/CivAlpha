"""The caller's portfolios and the advice on them (ADR-0004, ADR-0005). Who may call is decided in app.required_role:
in token mode every method of /api/portfolio* needs the admin token when one is configured, GET included (holdings are
personal data); in accounts mode a signed-in user, who only ever sees their own portfolios (another user's portfolio id
answers 404). Symbols travel in the request body, never in the path, so access logs do not record which stocks are
held. `portfolioId` picks one of the caller's portfolios; without it, their first."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from ..portfolio import PortfolioService
from .auth import caller
from .read import parse_date

router = APIRouter(prefix="/api")


def _svc(request: Request) -> PortfolioService:
    return PortfolioService(caller=caller(request))


class PortfolioIn(BaseModel):
    name: str


class HoldingIn(BaseModel):
    symbol: str
    shares: float
    avgCostUsd: float
    openedOn: date | None = None
    note: str | None = None
    portfolioId: int | None = None


class SymbolIn(BaseModel):
    symbol: str
    portfolioId: int | None = None


class CashIn(BaseModel):
    cashUsd: float
    portfolioId: int | None = None


class AdviseIn(BaseModel):
    portfolioId: int | None = None


@router.get("/portfolios")
def portfolios(request: Request):
    return _svc(request).portfolios()


@router.post("/portfolios")
def create_portfolio(body: PortfolioIn, request: Request):
    return _svc(request).create_portfolio(body.name)


@router.get("/portfolio/holdings")
def holdings(request: Request, portfolioId: int | None = None):  # noqa: N803 - query parameter name is part of the API
    return _svc(request).holdings(portfolioId)


@router.put("/portfolio/holdings")
def set_holding(body: HoldingIn, request: Request):
    return _svc(request).set_holding(body.symbol, body.shares, body.avgCostUsd, body.openedOn, body.note, body.portfolioId)


@router.post("/portfolio/holdings/remove", status_code=204)
def remove_holding(body: SymbolIn, request: Request):
    _svc(request).remove_holding(body.symbol, body.portfolioId)
    return Response(status_code=204)


@router.put("/portfolio/cash")
def set_cash(body: CashIn, request: Request):
    return {"cashUsd": _svc(request).set_cash(body.cashUsd, body.portfolioId)}


@router.get("/portfolio/advice")
def advice(request: Request, date_: str | None = Query(None, alias="date"), portfolioId: int | None = None):  # noqa: N803
    return _svc(request).advice(parse_date(date_), portfolioId)


@router.post("/portfolio/advice")
def refresh_advice(request: Request, body: AdviseIn | None = None):
    """Advice on the book's latest decision date for the portfolio as it is now; idempotent for an unchanged portfolio."""
    s = _svc(request)
    pid = body.portfolioId if body else None
    r = s.advise(pid=pid)
    return {**s.advice(r.as_of, pid), "created": r.created, "existing": r.existing}


@router.get("/portfolio/track-record")
def track_record(request: Request, portfolioId: int | None = None):  # noqa: N803
    return _svc(request).track_record(portfolioId)
