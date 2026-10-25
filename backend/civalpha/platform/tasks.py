"""Job bodies run by the worker, keyed by job type. Each takes the job's params and a log callback."""
from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from .. import service as ml
from .. import timemachine
from ..strategies import doublers, setups, signals
from ..strategies import service as strategy_lab
from .decisions import DecisionService
from .errors import Problem
from .expand import UniverseExpansion
from .earnings import EarningsIngestionService
from .insiders import InsiderIngestionService
from .forecasts import ForecastService
from .jobs import Log, Progress
from .market import MarketDataService, PriceSyncService
from .pipeline import Pipeline, java_map, java_set
from .auth import SYSTEM
from .portfolio import PortfolioService
from .sec import FilingIngestionService, SecClientFactory
from .settings import settings
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


def study_day(params: dict, now: datetime | None = None) -> bool:
    """ADR-0012: a run started by hand always runs the studies; a scheduled or catch-up run only on CIVALPHA_STUDY_DAYS
    (in the schedule's zone; FRI by default: the Friday evening run in New York, Saturday morning in Tbilisi)."""
    if params.get("trigger") not in ("schedule", "catch-up"):
        return True
    s = settings().schedule
    days = {d.strip().upper()[:3] for d in (s.study_days or "").split(",") if d.strip()}
    if params.get("session"):            # a catch-up: the day of the session it catches up, not of the retry
        day = date.fromisoformat(params["session"])
    else:
        day = (now or datetime.now(ZoneInfo(s.zone))).astimezone(ZoneInfo(s.zone)).date()
    return day.strftime("%a").upper() in days


def pipeline_run(params: dict, log: Log) -> None:
    studies = study_day(params)
    if not studies:
        log("scheduled run on a weekday: the evaluation, the lab and the studies run on the study day (ADR-0012)")
    Pipeline().run_configured(log, studies)


def evaluate(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(ml.evaluate(engine())["verdict"]))


def strategy_backtest(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(strategy_lab.backtest_strategies(engine())["summary"]))


def strategy_decide(params: dict, log: Log) -> None:
    _benchmarks(log)
    r = DecisionService().decide(_date(params), log)
    log(f"AI decisions: {r.created} stored, {r.existing} already existed, {r.explained} explained, {r.reviewed} reviewed, {r.vetoed} vetoed")
    a = PortfolioService(caller=SYSTEM).advise_all()     # ADR-0004: the advice reads these decisions; counts only in the log
    if a.as_of is not None:
        log(f"portfolio advice for {a.as_of}: {a.portfolios} portfolios, {a.created} rows stored, {a.existing} already existed")


def corpactions_detect(params: dict, log: Log) -> None:
    """ADR-0003: read the 8-Ks not yet read by the keyword rule and store split and spin-off candidates."""
    from .. import corpactions
    from .sql import engine

    r = corpactions.run(engine(), "KEYWORD", log=lambda _m: None)
    log(f"8-K corporate-action candidates: {r['classified']} new filings read, {r['flagged']} flagged")


def doubler_study(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(doublers.study(engine())["headline"]))


def signal_study(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(signals.study(engine())["headline"]))


def feature_ablation(params: dict, log: Log) -> None:
    from ..strategies import ablation
    _benchmarks(log)
    res = ablation.study(engine(), with_lab=bool(params.get("withLab", True)), progress=log)
    log(str(res["headline"]))
    if "trialsCounted" in res:
        log(f"trial registry: {res['trialsRegistered']} variants registered, {res['trialsCounted']} trials counted for the deflated Sharpe")


def setup_study(params: dict, log: Log) -> None:
    _benchmarks(log)
    log(str(setups.study(engine())["headline"]))


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


def earnings_ingest(params: dict, log: Log) -> None:
    _require_universe()
    sec = SecClientFactory().configured()
    svc = EarningsIngestionService()
    svc.reclassify(log)
    total = 0
    companies = UniverseService().companies()
    p = Progress(log, len(companies))
    for i, c in enumerate(companies, 1):
        p.at(f"earnings releases {c['name']} ({i}/{len(companies)})")
        try:
            total += svc.ingest(sec, int(c["id"]), log).releases
        except Exception as e:  # noqa: BLE001
            log(f"earnings releases failed for company {c['id']}: {e}")
        p.step()
    log(f"earnings releases: {total} read")


def insider_ingest(params: dict, log: Log) -> None:
    _require_universe()
    r = InsiderIngestionService().ingest(SecClientFactory().configured(), log)
    log(f"insiders: {r.datasets} data sets, {r.filings} recent Form 4 filings, {r.transactions} transactions stored")


def event_backfill(params: dict, log: Log) -> None:
    """ADR-0009: the official policy events since 2019 (material Federal Register streams, FOMC rate changes)."""
    from .events import backfill
    backfill.run_job(params, log)


def company_events_backfill(params: dict, log: Log) -> None:
    """ADR-0011: company events from every 8-K of the lookback window."""
    _require_universe()
    from .company_events import backfill_job
    backfill_job(params, log)


def company_events_classify(params: dict, log: Log) -> None:
    """ADR-0011 decision 9: Claude reads item 5.02 (collects an ended batch, submits what is left)."""
    from .exec_changes import classify_job
    classify_job(params, log)


def history_backfill(params: dict, log: Log) -> None:
    """ADR-0012: the point-in-time universe and its data from 2015 (resumable; params `steps` runs a subset)."""
    from .history_backfill import run_job
    run_job(params, log)


def universe_expand(params: dict, log: Log) -> None:
    sec = SecClientFactory().configured()
    ingest = FilingIngestionService()
    r = UniverseExpansion().run(params, log, ingest=lambda cid, lg: ingest.ingest(sec, int(cid), lg))
    if not r.added:
        raise Problem("no company could be added; see the log above")


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
    "CORPACTIONS_DETECT": corpactions_detect,
    "TIME_MACHINE": time_machine,
    "DOUBLER_STUDY": doubler_study,
    "SETUP_STUDY": setup_study,
    "SIGNAL_STUDY": signal_study,
    "FEATURE_ABLATION": feature_ablation,
    "ISSUE_FORECASTS": issue_forecasts,
    "PRICE_SYNC": price_sync,
    "RESOLVE_OUTCOMES": resolve_outcomes,
    "SEC_INGEST": sec_ingest,
    "PRICE_IMPORT": price_import,
    "UNIVERSE_EXPAND": universe_expand,
    "INSIDER_INGEST": insider_ingest,
    "EARNINGS_INGEST": earnings_ingest,
    "EVENT_BACKFILL": event_backfill,
    "COMPANY_EVENTS_BACKFILL": company_events_backfill,
    "COMPANY_EVENTS_CLASSIFY": company_events_classify,
    "HISTORY_BACKFILL": history_backfill,
}
