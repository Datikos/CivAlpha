"""Catch-up for the scheduled pipeline: when the evening run failed or never ran (the network dropped, the machine
slept), the worker runs it again so the day's prices, forecasts, decisions and advice still arrive.

The rule (`decide`, pure): after the schedule has fired for the latest NYSE session, if not every active benchmark ETF has
that session's bar or no live forecast was issued for it, no pipeline run is queued or running, the last one finished at least RETRY_MINUTES ago, and fewer
than MAX_ATTEMPTS catch-up runs were made for that session, queue a PIPELINE_RUN with trigger "catch-up". Attempts are
counted from the job table, so a worker restart does not reset them. Market holidays come from the NYSE calendar below,
so a holiday is never "missing".

On 2026-10-09 the evening run lost the network (DNS failures from 23:45 UTC) and the session's forecasts were issued only
on 2026-10-12, after batch 2's three-day limit (docs/research/live-test-2026-10-batch2.md).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
from croniter import croniter
from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
                                    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday, sunday_to_monday)

from .jobs import Jobs
from .sql import Db, db

log = logging.getLogger("civalpha.catchup")

NEW_YORK = ZoneInfo("America/New_York")
SESSION_CLOSED = time(16, 30)       # as market.sync.last_completed_session: a session counts after 16:30 New York
RETRY_MINUTES = 60
MAX_ATTEMPTS = 6
CHECK_EVERY = timedelta(minutes=5)
ACTIVE_BENCHMARK_DAYS = 10          # a benchmark without a bar for this long is not waited for (delisted, renamed)


class NyseHolidays(AbstractHolidayCalendar):
    """Full-day NYSE closures by rule. Not covered: unscheduled closures (national days of mourning, weather); on such a
    day the catch-up makes its MAX_ATTEMPTS runs and stops."""
    rules = [
        Holiday("NewYearsDay", month=1, day=1, observance=sunday_to_monday),   # a Saturday New Year is not observed
        USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("IndependenceDay", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=8)
def _holidays(year: int) -> frozenset:
    return frozenset(d.date() for d in NyseHolidays().holidays(start=f"{year - 1}-12-01", end=f"{year + 1}-01-31"))


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in _holidays(d.year)


def last_session(now: datetime) -> date:
    """The latest NYSE session that has closed at `now`."""
    ny = now.astimezone(NEW_YORK)
    d = ny.date() if ny.time() >= SESSION_CLOSED else ny.date() - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def decide(now: datetime, cron: str, zone: str, benchmarks_through: date | None, pipeline_active: bool,
           last_finished: datetime | None, attempts: int, forecasts_through: date | None = None) -> date | None:
    """The session to catch up, or None. A session is done when every active benchmark ETF has its bar
    (`benchmarks_through`, the oldest latest-bar date among them) and live forecasts were issued for it
    (`forecasts_through`): prices loaded by a separate price sync leave the forecasts, decisions and advice undone."""
    expr = (cron or "").strip()
    if not expr or expr == "-" or pipeline_active:
        return None
    session = last_session(now)
    if (benchmarks_through is not None and benchmarks_through >= session
            and forecasts_through is not None and forecasts_through >= session):
        return None
    fields = expr.split()
    prev = croniter(expr, now.astimezone(ZoneInfo(zone)), second_at_beginning=len(fields) == 6).get_prev(datetime)
    if prev < datetime.combine(session, SESSION_CLOSED, NEW_YORK):
        return None                 # the schedule has not fired for this session yet: nothing has failed
    if attempts >= MAX_ATTEMPTS:
        return None
    if last_finished is not None and now - last_finished < timedelta(minutes=RETRY_MINUTES):
        return None
    return session


class CatchUp:
    def __init__(self, cron: str, zone: str, database: Db | None = None, jobs: Jobs | None = None):
        self.cron, self.zone = cron, zone
        self.db = database or db()
        self.jobs = jobs or Jobs(self.db)
        self.next_check: datetime | None = None

    def check(self, now: datetime) -> dict | None:
        """Called by the worker loop; looks at the database at most every CHECK_EVERY. Returns the queued job or None."""
        if self.next_check is not None and now < self.next_check:
            return None
        self.next_check = now + CHECK_EVERY
        session = last_session(now)
        through = self.db.scalar("""
            SELECT min(m) FROM (SELECT symbol, max(trade_date) AS m FROM price_bar WHERE company_id IS NULL GROUP BY symbol) b
            WHERE m >= :active""", active=session - timedelta(days=ACTIVE_BENCHMARK_DAYS))
        attempts = self.db.scalar("""SELECT count(*) FROM pipeline_job WHERE job_type = 'PIPELINE_RUN'
                                     AND params->>'trigger' = 'catch-up' AND params->>'session' = :s""", s=session.isoformat())
        last_finished = self.db.scalar("SELECT max(finished_at) FROM pipeline_job WHERE job_type = 'PIPELINE_RUN'")
        forecasts = self.db.scalar("SELECT max(as_of_date) FROM forecast WHERE issue_mode = 'LIVE'")
        target = decide(now, self.cron, self.zone, through, self.jobs.running("PIPELINE_RUN"), last_finished, int(attempts),
                        forecasts)
        if target is None:
            return None
        why = ("benchmark prices" if through is None or through < target else "live forecasts") + f" for {target} missing after the scheduled run"
        job = self.jobs.submit("PIPELINE_RUN", {"trigger": "catch-up", "session": target.isoformat(), "attempt": int(attempts) + 1,
                                                "reason": why})
        log.warning("catch-up: queued pipeline run %s for session %s (attempt %d of %d)", job["id"], target, attempts + 1, MAX_ATTEMPTS)
        return job
