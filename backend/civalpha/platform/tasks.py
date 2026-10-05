"""Job bodies run by the worker, keyed by job type. Each takes the job's params and a log callback."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from .. import service as ml
from .. import timemachine
from ..strategies import doublers
from ..strategies import service as strategy_lab
from .decisions import DecisionService
from .errors import Problem
from .forecasts import ForecastService
from .jobs import Log
from .market import MarketDataService, PriceSyncService
from .pipeline import Pipeline, java_map, java_set
from .sec import FilingIngestionService, SecClientFactory
from .sql import engine
from .tickers import TickerResolver
from .universe import UniverseService


def _date(params: dict, key: str = "asOfDate") -> date | None:
    v = params.get(key)
    return date.fromisoformat(v) if v else None


def _benchmarks(log: Log) -> None:
    warning = MarketDataService().require_benchmarks()
    if warning:
        log(warning)


def pipeline_run(params: dict, log: Log) -> None:
    Pipeline().run_configured(log)


def evaluate(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(ml.evaluate(engine())["verdict"]))


def strategy_backtest(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(strategy_lab.backtest_strategies(engine())["summary"]))


def strategy_decide(params: dict, log: Log) -> None:
    _benchmarks(log)
    r = DecisionService().decide(_date(params), log)
    log(f"AI decisions: {r.created} stored, {r.existing} already existed, {r.explained} explained")


def doubler_study(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(doublers.study(engine())["headline"]))


def time_machine(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(timemachine.run(engine(), params["asOfDate"])["headline"]))


def issue_forecasts(params: dict, log: Log) -> None:
    _benchmarks(log)
    d = _date(params)
    fs = ForecastService()
    r = fs.issue_live(None, "Manual issue") if d is None else fs.issue_at(d, f"Manual issue for {d}")
    log(f"{r.created} created, {r.unchanged} unchanged")


def _require_universe() -> None:
    if not UniverseService().any_companies():
        raise Problem("The universe is empty. Add companies on the Universe page first.")


def price_sync(params: dict, log: Log) -> None:
    _require_universe()
    PriceSyncService().sync(log)


def resolve_outcomes(params: dict, log: Log) -> None:
    log(java_map(ml.resolve_outcomes(engine())))


def sec_ingest(params: dict, log: Log) -> None:
    cid = params.get("companyId") or TickerResolver().company_ever(params["symbol"])
    if cid is None:
        raise Problem(f"unknown symbol {params.get('symbol')}")
    FilingIngestionService().ingest(SecClientFactory().configured(), int(cid), log)


def price_import(params: dict, log: Log) -> None:
    path = Path(params["path"])
    try:
        _require_universe()    # symbols resolve through the universe's ticker history
        market = MarketDataService()
        r = market.import_prices(path.read_bytes(), params.get("file") or "upload.csv", params.get("provider") or "CSV upload")
        log(f"{r.rows} rows, {r.inserted} inserted, {r.unchanged} unchanged, {r.revised} corrected (previous values archived), "
            f"unknown symbols {java_set(r.unknown_symbols)}")
        missing = market.missing_benchmarks()
        if missing:
            log(f"Note: no prices yet for benchmark ETF(s) {', '.join(missing)}; import them too before evaluating or issuing forecasts.")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


TASKS = {
    "PIPELINE_RUN": pipeline_run,
    "EVALUATE": evaluate,
    "STRATEGY_BACKTEST": strategy_backtest,
    "STRATEGY_DECIDE": strategy_decide,
    "TIME_MACHINE": time_machine,
    "DOUBLER_STUDY": doubler_study,
    "ISSUE_FORECASTS": issue_forecasts,
    "PRICE_SYNC": price_sync,
    "RESOLVE_OUTCOMES": resolve_outcomes,
    "SEC_INGEST": sec_ingest,
    "PRICE_IMPORT": price_import,
}
