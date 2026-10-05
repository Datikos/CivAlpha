"""The pipeline: refresh prices, SEC filings, macro data and policy events, then evaluate, forecast and decide."""
from __future__ import annotations

from pathlib import Path


from .. import service as ml
from ..strategies import doublers, setups
from ..strategies import service as strategy_lab
from .decisions import DecisionService
from .errors import Problem
from .events import EventService, LiveEventSources
from .forecasts import ForecastService
from .jobs import Log
from .macro import MacroService
from .market import MarketDataService, PriceSyncService
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
    def run_configured(self, log: Log) -> None:
        if not self.universe.companies():
            raise Problem("The universe is empty. Add companies on the Universe page first.")
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
        if self.prices.enabled():
            try:
                self.prices.sync(log)
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
            for c in self.universe.companies():
                try:
                    self.filings.ingest(sec, c["id"], log)
                except Exception as e:  # noqa: BLE001 - one company's filings must not stop the run
                    log(f"SEC ingest failed for company {c['id']}: {e}")
        else:
            log("SEC_USER_AGENT is not set (your name and contact e-mail, required by the SEC); SEC filings skipped")
        if self.s.fred.enabled:
            for series in self.s.fred.series:
                log(f"FRED {series}: {self.macro.fetch_fred(series.strip())} observations")
        else:
            log("FRED_API_KEY not set; macro series skipped")
        for d in LiveEventSources().collect(log):
            r = self.events.ingest(d)
            if r.created:
                log(f"event {r.event_id}: {d.title}")
        self.after_ingest(log)

    def after_ingest(self, log: Log) -> None:
        if self.market.latest_benchmark_date() is None:
            log(f"No benchmark ETF prices are loaded ({', '.join(self.market.missing_benchmarks())}); skipping evaluation and "
                "forecasts. Import a prices CSV that includes them.")
            return
        warning = self.market.require_benchmarks()
        if warning:
            log(warning)
        log("walk-forward evaluation: " + str(ml.evaluate(engine())["verdict"]))
        live = self.forecasts.issue_live(None, "Scheduled issue")
        log(f"live forecasts: {live.created} created, {live.unchanged} unchanged")
        log("outcomes: " + java_map(ml.resolve_outcomes(engine())))
        try:
            log("strategy lab: " + str(strategy_lab.backtest_strategies(engine())["summary"]))
            d = self.decisions.decide(None, log)
            log(f"AI decisions: {d.created} stored, {d.existing} already existed, {d.explained} explained")
        except Exception as e:  # noqa: BLE001 - the lab is optional; forecasts above are already stored
            log(f"strategy lab skipped: {e}")
        try:
            log("doubler study: " + str(doublers.study(engine())["headline"]))
        except Exception as e:  # noqa: BLE001
            log(f"doubler study skipped: {e}")
        try:
            log("setup playbook: " + str(setups.study(engine())["headline"]))
        except Exception as e:  # noqa: BLE001
            log(f"setup playbook skipped: {e}")
