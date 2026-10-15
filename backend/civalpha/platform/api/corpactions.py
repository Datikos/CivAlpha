"""The owner's review of corporate-action candidates from 8-K text (ADR-0003 phase 3). Under /api/admin, so every route
needs the owner (app.required_role)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ..corpaction_review import CorporateActionReview
from ..jobs import Jobs
from ..rows import camel
from .auth import caller

router = APIRouter(prefix="/api/admin/corporate-actions")


class ReviewIn(BaseModel):
    ids: list[int]
    review: str
    note: str | None = None
    linkActionId: int | None = None


class SpinOffIn(BaseModel):
    exDate: date
    valuePerShare: float
    note: str | None = None


@router.get("/candidates")
def candidates(status: str = Query("gap"), symbol: str | None = None, limit: int = Query(500, ge=1, le=2000)):
    return CorporateActionReview().candidates(status, symbol, limit)


@router.post("/candidates/review")
def review(body: ReviewIn, request: Request):
    n = CorporateActionReview().review(caller(request), body.ids, body.review, body.note, body.linkActionId)
    return {"reviewed": n}


@router.post("/candidates/{candidate_id}/spin-off")
def spin_off(candidate_id: int, body: SpinOffIn, request: Request):
    return CorporateActionReview().add_spin_off(caller(request), candidate_id, body.exDate, body.valuePerShare, body.note)


@router.post("/detect")
def detect():
    """Queue a job that reads the 8-Ks not yet read by the keyword rule (also a step of every pipeline run)."""
    return camel(Jobs().submit("CORPACTIONS_DETECT", {}))
