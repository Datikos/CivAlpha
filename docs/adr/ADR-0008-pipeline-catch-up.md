# ADR-0008: Catch up a failed or missed evening pipeline run

**Status:** Accepted (2026-10-15, owner: "run 19"; implemented 2026-10-15)
**Date:** 2026-10-15
**Deciders:** David Sakhelashvili (owner)

## Context

The pipeline runs on `CIVALPHA_PIPELINE_CRON` (18:30 New York on weekdays in this installation). The worker fires a
schedule once; nothing notices when the run fails or never runs. On 2026-10-09 (job 186) the run lost the network
(timeouts at 23:07 and 23:15 UTC, DNS failures for every symbol from 23:45 UTC, the machine likely asleep): no
benchmark ETF got that session's bar, no forecasts were issued for 2026-10-09 until a manual run on 2026-10-12 14:16
UTC, and all 351 fell outside batch 2's three-day limit (`docs/research/live-test-2026-10-batch2.md`). The README listed
"a missed run while the stack is down is not caught up" as a known limitation. Batch 2's last as-of date (2026-10-16)
is issued by a run at the same hour.

## Decision

1. **The rule** (`platform/catchup.py`, `decide`): after the schedule has fired for the latest NYSE session (16:30 New
   York close), if not every active benchmark ETF (one with a bar in the last 10 days) has that session's bar, no
   `PIPELINE_RUN` is queued or running, the last one finished at least 60 minutes ago, and fewer than 6 catch-up runs
   were made for that session, the worker queues a `PIPELINE_RUN` with params `{"trigger": "catch-up", "session",
   "attempt", "reason"}`.
2. **State lives in the job table**: attempts are counted from `pipeline_job.params`, so a worker restart does not reset
   them; the worker looks at most every 5 minutes. A machine that slept through the evening catches up when it wakes.
3. **NYSE holidays** by rule (pandas holiday rules: New Year's Day (a Saturday one not observed), MLK Day, Presidents'
   Day, Good Friday, Memorial Day, Juneteenth from 2022, Independence Day, Labor Day, Thanksgiving, Christmas): a holiday
   is never a missing session. Checked against 1,957 stored XLK bars (2019-01-02 to 2026-10-14): no bar on a holiday;
   one session without a bar, 2025-01-09 (the unscheduled closure for President Carter), which is out of scope.
4. Active whenever the pipeline schedule is set; no new setting.

## Options considered

### Option A: count attempts in the job table, check every 5 minutes (chosen)
Pro: survives restarts and sleep; every attempt is visible in the job list with its reason. Con: one small query set
every 5 minutes.

### Option B: an in-memory retry timer in the worker
Pro: simplest. Con: a restart or a sleeping machine loses it, which is the failure it is meant for.

### Option C: a second cron entry (for example 06:00 New York)
Pro: no code. Con: a fixed second chance, not a catch-up; it runs even when nothing failed, and once only.

## Consequences

Easier: a lost evening run no longer needs a person on the weekend; batch 2's last date is protected.

Harder: on an unscheduled market closure the worker makes 6 useless runs (about 25 minutes each); the job list shows
catch-up runs, which are expected.

**Out of scope here:** catching up the outcomes schedule (`CIVALPHA_OUTCOMES_CRON`; the pipeline resolves outcomes
too), alerting a person when all 6 attempts fail, unscheduled closures.

## Action items

1. [x] `platform/catchup.py` (`NyseHolidays`, `is_session`, `last_session`, `decide`, `CatchUp`), wired in `worker.py`.
2. [x] Tests `backend/tests/test_catchup.py`: Friday failure caught up on Saturday, a weekend-long sleep caught up on
   Monday before the close, no catch-up when the prices arrived, before the schedule fired, on Thanksgiving, while a run is
   queued, within an hour of the last run, after 6 attempts, without a schedule; the job's params and the attempt count.
3. [x] README (schedule, known limitations), `.env.example`.
