"""End-to-end flows: the synthetic demo and the configured (live/credentialed) pipeline."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .. import service as ml
from ..strategies import service as strategy_lab
from .decisions import DecisionService
from .errors import Problem
from .events import EventDraft, EventService, LiveEventSources, Source, Target
from .forecasts import ForecastService
from .jobs import Log
from .macro import MacroService, parse_csv
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

    def demo_present(self) -> bool:
        return bool(self.db.scalar("SELECT exists(SELECT 1 FROM company WHERE is_demo)"))

    # ------------------------------------------------------------------ demo
    def load_demo(self, log: Log) -> None:
        if self.db.scalar("SELECT exists(SELECT 1 FROM company WHERE NOT is_demo)"):
            raise Problem("This database already holds real (non-demo) data, so the synthetic demo will not be mixed in. "
                          "To try the demo, start from an empty database: docker compose down -v && docker compose up -d")
        from ..demo.generate import Gen, load_universe

        d = Path(self.s.demo_dir)
        log(f"generating synthetic demo dataset in {d}")
        Gen(d, load_universe(Path(self.s.universe_file))).run()
        log(f"universe: {self.universe.load(True)} companies created")
        pr = self.market.import_prices((d / "prices.csv").read_bytes(), "demo/prices.csv", "DEMO synthetic CSV", True)
        log(f"prices: {pr.rows} rows, {pr.inserted} inserted, {pr.unchanged} unchanged, {pr.revised} corrected, "
            f"unknown symbols {java_set(pr.unknown_symbols)}")
        log("corporate actions: " + str(self.market.import_actions((d / "corporate_actions.csv").read_bytes(),
                                                                   "demo/corporate_actions.csv", "DEMO synthetic CSV", True)))
        for s in json.loads((d / "macro" / "series.json").read_text()):
            self.macro.upsert_series(s.get("series_id"), s.get("title"), s.get("units"), s.get("frequency"), "DEMO synthetic")
        log("macro observations (vintages): " + str(self.macro.insert(parse_csv((d / "macro" / "observations.csv").read_text()), True)))
        sec = self.sec_factory.fixture(d / "sec")
        for c in self.universe.companies():
            self.filings.ingest(sec, c["id"], True, log)
        self._load_demo_events(d / "events", log)
        self.after_ingest(log, True)

    def _load_demo_events(self, d: Path, log: Log) -> None:
        root = json.loads((d / "events.json").read_text())
        for a in root.get("actors", []):
            self.events.upsert_actor(a.get("name"), a.get("actor_type"), a.get("authority"), None, a.get("profile_note"))
        created = merged = 0
        for e in root.get("events", []):
            r = self.events.ingest(self._draft(d, e, (e.get("sources") or [{}])[0]), True)
            if r.created:
                created += 1
            else:
                merged += 1
        for n in root.get("news", []):
            r = self.events.ingest(self._draft(d, n, n.get("source") or {}), True)
            log(f"news item '{n.get('title')}' -> {'merged into existing' if r.deduplicated else 'new NEWS_ONLY'} event {r.event_id}")
        log(f"events: {created} created, {merged} merged")

    @staticmethod
    def _draft(d: Path, e: dict, s: dict) -> EventDraft:
        targets = [Target(t.get("target_type"), t.get("target_code"),
                          float(t["magnitude"]) if isinstance(t.get("magnitude"), (int, float)) else None) for t in e.get("targets", [])]
        pub = datetime.fromisoformat(e["published_at"].replace("Z", "+00:00"))
        doc = s.get("doc")
        return EventDraft(category=e.get("category"), event_type=e.get("event_type"), title=e.get("title"), summary=e.get("summary"),
                          event_date=date.fromisoformat(e["event_date"]), published_at=pub, actor_name=e.get("actor"),
                          attributes=dict(e.get("attributes") or {}), targets=targets,
                          source=Source(url=f"demo://events/{doc}", title=s.get("title"), publisher=s.get("publisher"), role=s.get("role"),
                                        published_at=pub, content=(d / doc).read_bytes(), content_type="text/html", demo=True))

    # ------------------------------------------------------------------ configured pipeline
    def run_configured(self, log: Log) -> None:
        log(f"universe: {self.universe.load(False)} companies created")
        imports = Path(self.s.imports_dir)
        if imports.is_dir():
            for f in sorted(imports.iterdir()):
                name = f.name
                if name.startswith("prices") and name.endswith(".csv"):
                    r = self.market.import_prices(f.read_bytes(), name, "CSV import", False)
                    log(f"{name}: {r.inserted} inserted, {r.revised} corrected, unknown {java_set(r.unknown_symbols)}")
                elif name.startswith("corporate_actions") and name.endswith(".csv"):
                    log(f"{name}: {self.market.import_actions(f.read_bytes(), name, 'CSV import', False)} actions")
        else:
            log(f"no imports directory {imports} (put prices*.csv / corporate_actions*.csv there)")
        if self.prices.enabled() and self.demo_present():
            log("price provider configured, but this is the demo database: skipping real price download")
        elif self.prices.enabled():
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
        if self.s.sec.live:
            sec = self.sec_factory.configured()
            for c in self.universe.companies():
                try:
                    self.filings.ingest(sec, c["id"], False, log)
                except Exception as e:  # noqa: BLE001 - one company's filings must not stop the run
                    log(f"SEC ingest failed for company {c['id']}: {e}")
        else:
            log(f"SEC mode is '{self.s.sec.mode}'; set CIVALPHA_SEC_MODE=live and SEC_USER_AGENT to fetch EDGAR")
        if self.s.fred.enabled:
            for series in self.s.fred.series:
                log(f"FRED {series}: {self.macro.fetch_fred(series.strip())} observations")
        else:
            log("FRED_API_KEY not set; macro series skipped")
        for d in LiveEventSources().collect(log):
            r = self.events.ingest(d, False)
            if r.created:
                log(f"event {r.event_id}: {d.title}")
        self.after_ingest(log, False)

    def after_ingest(self, log: Log, demo: bool) -> None:
        if self.market.latest_benchmark_date() is None:
            log(f"No benchmark ETF prices are loaded ({', '.join(self.market.missing_benchmarks())}); skipping evaluation and "
                "forecasts. Import a prices CSV that includes them.")
            return
        warning = self.market.require_benchmarks()
        if warning:
            log(warning)
        log("walk-forward evaluation: " + str(ml.evaluate(engine())["verdict"]))
        if demo:
            dates = self.replay_dates(self.s.replay_months)
            r = self.forecasts.issue_replay(dates, "Demo replay: issued with data available at each historical cutoff")
            log(f"replayed forecasts for {len(dates)} month-end dates: {r.created} created")
        live = self.forecasts.issue_live(None, "Scheduled issue")
        log(f"live forecasts: {live.created} created, {live.unchanged} unchanged")
        log("outcomes: " + java_map(ml.resolve_outcomes(engine())))
        try:
            log("strategy lab: " + str(strategy_lab.backtest_strategies(engine())["summary"]))
            d = self.decisions.decide(None, log)
            log(f"AI decisions: {d.created} stored, {d.existing} already existed, {d.explained} explained")
        except Exception as e:  # noqa: BLE001 - the lab is optional; forecasts above are already stored
            log(f"strategy lab skipped: {e}")

    def replay_dates(self, months: int) -> list[date]:
        """Last trading day of each of the previous N months (from benchmark bars)."""
        rows = self.db.scalars("""SELECT max(trade_date) FROM price_bar WHERE company_id IS NULL
                                  GROUP BY date_trunc('month', trade_date) ORDER BY 1 DESC LIMIT :n""", n=months + 1)
        return sorted(rows[1:])
