"""Catch-up of the scheduled pipeline: a failed or missed evening run is queued again until the benchmarks have the
session, never before the schedule fired, never on an NYSE holiday, at most six times, an hour apart."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from civalpha.platform.catchup import MAX_ATTEMPTS, CatchUp, decide, is_session, last_session

NY = ZoneInfo("America/New_York")
CRON = "0 30 18 * * MON-FRI"


def ny(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=NY)


def run(now, through, active=False, last=None, attempts=0, cron=CRON):
    return decide(ny(now), cron, "America/New_York", through, active, ny(last) if last else None, attempts)


def test_nyse_sessions_and_holidays():
    assert not is_session(date(2026, 11, 26)) and not is_session(date(2026, 4, 3))        # Thanksgiving, Good Friday
    assert not is_session(date(2026, 7, 3))                                                 # July 4 is a Saturday: observed Friday
    assert is_session(date(2026, 10, 12))                                                   # Columbus Day: the market is open
    assert is_session(date(2027, 12, 31)) and is_session(date(2021, 12, 31))               # a Saturday New Year is not observed
    assert not is_session(date(2023, 1, 2))                                                 # a Sunday New Year: closed Monday
    assert last_session(ny("2026-10-16T16:00")) == date(2026, 10, 15)                       # before the close: yesterday
    assert last_session(ny("2026-10-16T16:31")) == date(2026, 10, 16)
    assert last_session(ny("2026-10-18T12:00")) == date(2026, 10, 16)                       # weekend: Friday
    assert last_session(ny("2026-11-27T09:00")) == date(2026, 11, 25)                       # after Thanksgiving


def test_a_failed_friday_run_is_caught_up_over_the_weekend():
    # Friday's 18:30 run lost the network; Saturday morning the benchmarks still end on Thursday
    assert run("2026-10-17T10:00", date(2026, 10, 15), last="2026-10-16T23:40") == date(2026, 10, 16)
    # the Mac slept through the whole weekend: Monday 03:00, before Monday's close, still Friday's session
    assert run("2026-10-19T03:00", date(2026, 10, 15), last="2026-10-16T19:00", attempts=2) == date(2026, 10, 16)


def test_nothing_is_caught_up_when_it_is_not_needed():
    assert run("2026-10-17T10:00", date(2026, 10, 16), last="2026-10-16T19:00") is None          # the prices arrived
    assert run("2026-10-16T17:00", date(2026, 10, 15)) is None                                    # the schedule has not fired yet
    assert run("2026-11-26T20:00", date(2026, 11, 25), last="2026-11-25T19:00") is None          # Thanksgiving: no session
    assert run("2026-10-17T10:00", date(2026, 10, 15), active=True) is None                      # a run is queued or running
    assert run("2026-10-17T10:00", date(2026, 10, 15), last="2026-10-17T09:30") is None          # last run ended 30 min ago
    assert run("2026-10-17T10:00", date(2026, 10, 15), attempts=MAX_ATTEMPTS) is None            # six attempts made
    assert run("2026-10-17T10:00", date(2026, 10, 15), cron="-") is None                          # no schedule, no catch-up


def test_check_queues_one_run_with_its_reason_and_counts_attempts(tdb):
    for sym, last in (("XLC", "2026-10-15"), ("XLK", "2026-10-15"), ("OLD", "2026-08-01")):     # OLD: not active, ignored
        tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (NULL, :s, :d, 100, 't')",
                    s=sym, d=date.fromisoformat(last))
    tdb.execute("DELETE FROM pipeline_job")
    c = CatchUp(CRON, "America/New_York", tdb)
    job = c.check(ny("2026-10-17T10:00"))
    assert job is not None and job["job_type"] == "PIPELINE_RUN"
    assert job["params"] == {"trigger": "catch-up", "session": "2026-10-16", "attempt": 1,
                             "reason": "benchmark prices for 2026-10-16 missing after the scheduled run"}
    assert c.check(ny("2026-10-17T10:01")) is None                       # checks at most every 5 minutes
    c.next_check = None
    assert c.check(ny("2026-10-17T10:10")) is None                       # the queued run is still pending
    tdb.execute("UPDATE pipeline_job SET status = 'FAILED', started_at = :t, finished_at = :t WHERE id = :i",
                t=ny("2026-10-17T10:20"), i=job["id"])
    c.next_check = None
    assert c.check(ny("2026-10-17T10:40")) is None                       # an hour must pass after the last run
    c.next_check = None
    second = c.check(ny("2026-10-17T11:30"))
    assert second is not None and second["params"]["attempt"] == 2
