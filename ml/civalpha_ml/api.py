"""HTTP API of the ML service (called by the backend only; not exposed publicly)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import service
from .db import engine_from_env
from .evaluation import EvalConfig

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="CivAlpha ML", version="0.1.0")
_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = engine_from_env()
    return _engine


class GenerateRequest(BaseModel):
    out_dir: str = "/data/demo"
    universe: str = os.environ.get("UNIVERSE_FILE", "/config/universe.yml")


class EvaluateRequest(BaseModel):
    sample_every: int = 7
    fold_length: int = 63
    min_train_days: int = 504
    cost_bps_per_side: float = 10.0
    signal_band: float = 0.03


class ForecastRequest(BaseModel):
    as_of: str | None = None
    as_of_dates: list[str] | None = None
    company_ids: list[int] | None = None
    model_kinds: list[str] | None = None
    n_boot: int = 30


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/demo/generate")
def generate(req: GenerateRequest):
    from .demo.generate import Gen, load_universe
    return Gen(Path(req.out_dir), load_universe(Path(req.universe))).run()


@app.post("/evaluate")
def evaluate(req: EvaluateRequest):
    try:
        return service.evaluate(engine(), EvalConfig(**req.model_dump()))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/forecasts")
def forecasts(req: ForecastRequest):
    try:
        return service.issue(engine(), req.as_of, req.as_of_dates, req.company_ids, req.model_kinds, req.n_boot)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/outcomes/resolve")
def outcomes():
    return service.resolve_outcomes(engine())
