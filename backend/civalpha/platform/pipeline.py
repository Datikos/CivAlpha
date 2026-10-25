"""The pipeline: refresh prices, SEC filings, macro data and policy events, then evaluate, forecast, decide and advise on
the owner's holdings (ADR-0004)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


from .. import corpactions
from .. import service as ml
from ..strategies import doublers, setups, signals
from ..strategies import service as strategy_lab
from ..db import load_bundle
from .company_events import NIGHTLY_DAYS, CompanyEventService
from .decisions import DecisionService
from .errors import Problem
from .events import EventService, LiveEventSources
from .forecasts import ForecastService
from .jobs import Log, Progress
from .macro import MacroService
from .market import MarketDataService, PriceSyncService
from .auth import SYSTEM
from .portfolio import PortfolioService
from .earnings import EarningsIngestionService
from .insiders import InsiderIngestionService
from .sec import FilingIngestionService, SecClientFactory
from .settings import settings
from .sql import Db, db, engine
from .universe import UniverseService


def java_set(items) -> str:
    """How the Java backend printed a set or list in job logs: [A, B]."""
    return "[" + ", ".join(str(x) for x in items) + "]"


def java_map(m: dict) -> str:
    """How the Java backend printed a map in job logs: {a=1, b=2}."""
    return "{" + ", ".join(f"{k}={v}" for k, v in m.items()) + "}"


WEEKLY = "not a study day (CIVALPHA_STUDY_DAYS, ADR-0012): skipped "
AFTER_STEPS = 7   # evaluation, forecasts, outcomes, strategy lab, doubler study, setup playbook, signal health


class Pipeline:
    def __init__(self, database: Db | None = None):
        self.db = database or db()
        self.s = settings()
        self.universe = UniverseService(self.db)
        self.market = MarketDataService(self.db)
        self.macro = MacroService(self.db)
        self.sec_factory = SecClientFactory()
        self.filings = FilingIngestionService(self.db)
        self.events = EventService(self.db)
        self.forecasts = ForecastService(self.db)
        self.prices = PriceSyncService(self.db)
        self.decisions = DecisionService(self.db)

    # ------------------------------------------------------------------ configured pipeline
    def run_configured(self, log: Log, studies: bool = True) -> None:
        """`studies` False (a scheduled run on a weekday that is not a study day, ADR-0012): the walk-forward evaluation,
        the strategy lab's backtest and the doubler, setup and signal studies wait for the study day; prices, filings,
        events, live forecasts, outcomes, decisions and advice run as always."""
        companies = self.universe.companies()
        if not companies:
            raise Problem("The universe is empty. Add companies on the Universe page first.")
        # progress: imports, prices, one step per company's SEC filings plus insiders (or one skipped step), macro,
        # events, then the after-ingest steps
        sec_steps = len(companies) + 3 if self.s.sec.configured else 1      # companies, insiders, 8-K corporate actions, 8-K events
        p = Progress(log, 2 + sec_steps + 2 + AFTER_STEPS, "CSV imports")
        imports = Path(self.s.imports_dir)
        if imports.is_dir():
            for f in sorted(imports.iterdir()):
                name = f.name
                if name.startswith("prices") and name.endswith(".csv"):
                    r = self.market.import_prices(f.read_bytes(), name, "CSV import")
                    log(f"{name}: {r.inserted} inserted, {r.revised} corrected, unknown {java_set(r.unknown_symbols)}")
                elif name.startswith("corporate_actions") and name.endswith(".csv"):
                    log(f"{name}: {self.market.import_actions(f.read_bytes(), name, 'CSV import')} actions")
        else:
            log(f"no imports directory {imports} (put prices*.csv / corporate_actions*.csv there)")
        p.step("price update")
        if self.prices.enabled():
            try:
                self.prices.sync(p.child())
            except Problem as e:
                # a refresh failure (rate limit, network) must not block filings, events and models when prices are stored
                stored = self.market.latest_benchmark_date()
                if stored is None:
                    raise
                log(f"price update failed ({e}); continuing with stored prices through {stored}")
        else:
            log("no price provider configured (CIVALPHA_PRICE_PROVIDER); using imported prices only")
        if self.s.sec.configured:
            sec = self.sec_factory.configured()
            for i, c in enumerate(companies, 1):
                p.step(f"SEC filings {c['name']} ({i}/{len(companies)})")
                try:
                    self.filings.ingest(sec, c["id"], p.child())
                except Exception as e:  # noqa: BLE001 - one company's filings must not stop the run
                    log(f"SEC ingest failed for company {c['id']}: {e}")
                try:
                    EarningsIngestionService(self.db).ingest(sec, c["id"], log)
                except Exception as e:  # noqa: BLE001
                    log(f"earnings releases failed for company {c['id']}: {e}")
            p.step("insider transactions")
            try:
                InsiderIngestionService(self.db).ingest(sec, p.child())
            except Exception as e:  # noqa: BLE001
                log(f"insider transactions failed ({e}); continuing")
            p.step("8-K corporate-action candidates")
            try:   # ADR-0003: the keyword rule reads every new 8-K; the portfolio's data check reads the candidates
                r = corpactions.run(engine(), "KEYWORD", log=lambda _m: None)
                log(f"8-K corporate-action candidates: {r['classified']} new filings read, {r['flagged']} flagged")
            except Exception as e:  # noqa: BLE001 - a detector failure must not stop prices, filings or the models
                log(f"8-K corporate-action candidates skipped: {e}")
            p.step("8-K company events")
            try:   # ADR-0011: the 8-Ks of the last 30 days from the index the SEC step just refreshed
                CompanyEventService(self.db).update(sec, p.child())
                if self.s.llm.enabled:   # decision 9: Claude reads item 5.02 (a collected batch, then the new filings)
                    from .exec_changes import ExecChangeReader
                    reader = ExecChangeReader(self.db)
                    reader.collect(log)
                    reader.read_new(log, since=datetime.now(timezone.utc) - timedelta(days=NIGHTLY_DAYS))
            except Exception as e:  # noqa: BLE001
                log(f"8-K company events skipped: {e}")
        else:
            p.step("SEC filings skipped")
            log("SEC_USER_AGENT is not set (your name and contact e-mail, required by the SEC); SEC filings skipped")
        p.step("macro series")
        self.macro_series(log)
        p.step("policy events")
        self.policy_events(log)
        self.after_ingest(log, p, studies)

    def macro_series(self, log: Log) -> None:
        """FRED series, each on its own: a network failure or an outage answer (2026-10-21/22: an empty body, five runs
        failed) is logged and the run goes on with the stored observations; the models read vintages, so one missed
        update changes nothing they knew."""
        if not self.s.fred.enabled:
            log("FRED_API_KEY not set; macro series skipped")
            return
        for series in self.s.fred.series:
            name = series.strip()
            try:
                log(f"FRED {name}: {self.macro.fetch_fred(name)} observations")
            except Exception as e:  # noqa: BLE001 - a macro outage must not stop forecasts, decisions and advice
                log(f"FRED {name} failed ({type(e).__name__}: {str(e)[:120]}); continuing with the stored observations")

    def policy_events(self, log: Log, sources=None) -> None:
        """Official and news events: each source is already isolated (LiveEventSources._safe); storing each event is too.
        Official events stored before streams existed are classified first (ADR-0009)."""
        try:
            self.events.classify_unstreamed(log)
        except Exception as e:  # noqa: BLE001 - classification is repeated on the next run
            log(f"event streams not classified ({type(e).__name__}: {str(e)[:120]})")
        for d in (sources or LiveEventSources()).collect(log):
            try:
                r = self.events.ingest(d)
            except Exception as e:  # noqa: BLE001 - one event's evidence download must not stop the run
                log(f"event {d.title[:80]!r} skipped ({type(e).__name__}: {str(e)[:120]})")
                continue
            if r.created:
                log(f"event {r.event_id}: {d.title}")

    def after_ingest(self, log: Log, p: Progress | None = None, studies: bool = True) -> None:
        """The AFTER_STEPS model steps; `p`, when given, advances one step per stage; without `studies` the weekly ones
        are skipped and say so."""
        step = p.step if p is not None else (lambda label: None)
        if self.market.latest_benchmark_date() is None:
            log(f"No benchmark ETF prices are loaded ({', '.join(self.market.missing_benchmarks())}); skipping evaluation and "
                "forecasts. Import a prices CSV that includes them.")
            if p is not None:
                p.finish()
            return
        warning = self.market.require_benchmarks()
        if warning:
            log(warning)
        step("walk-forward evaluation")
        if studies:
            log("walk-forward evaluation: " + str(ml.evaluate(engine())["verdict"]))
        else:
            log(WEEKLY + "walk-forward evaluation")
        step("live forecasts")
        live = self.forecasts.issue_live(None, "Scheduled issue")
        log(f"live forecasts: {live.created} created, {live.unchanged} unchanged")
        step("outcomes")
        log("outcomes: " + java_map(ml.resolve_outcomes(engine())))
        try:   # ADR-0004: counts only; holdings and advice never go into the job log
            log("portfolio advice outcomes: " + java_map(PortfolioService(self.db, caller=SYSTEM).resolve_outcomes(lambda: load_bundle(engine()))))
        except Exception as e:  # noqa: BLE001
            log(f"portfolio advice outcomes skipped: {e}")
        step("strategy lab")
        try:
            if studies:
                log("strategy lab: " + str(strategy_lab.backtest_strategies(engine())["summary"]))
            else:
                log(WEEKLY + "strategy lab")
        except Exception as e:  # noqa: BLE001 - the lab is optional; forecasts above are already stored
            log(f"strategy lab skipped: {e}")
        try:   # the decisions train their own model (strategies.decide): nightly, whatever the lab does
            d = self.decisions.decide(None, log)
            log(f"AI decisions: {d.created} stored, {d.existing} already existed, {d.explained} explained")
        except Exception as e:  # noqa: BLE001
            log(f"AI decisions skipped: {e}")
        try:   # ADR-0004: after the decisions it reads; counts only in the log
            a = PortfolioService(self.db, caller=SYSTEM).advise_all()
            if a.as_of is not None:
                log(f"portfolio advice for {a.as_of}: {a.portfolios} portfolios, {a.created} rows stored, {a.existing} already existed")
        except Exception as e:  # noqa: BLE001
            log(f"portfolio advice skipped: {e}")
        for label, fn in (("doubler study", doublers.study), ("setup playbook", setups.study), ("signal health", signals.study)):
            step(label)
            if not studies:
                log(WEEKLY + label)
                continue
            try:
                log(f"{label}: " + str(fn(engine())["headline"]))
            except Exception as e:  # noqa: BLE001
                log(f"{label} skipped: {e}")
        if p is not None:
            p.finish()
