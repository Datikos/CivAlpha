"""Background worker: runs queued jobs one at a time and the optional schedules. Run: python -m civalpha.platform.worker

Schedules (CIVALPHA_PIPELINE_CRON / CIVALPHA_OUTCOMES_CRON, "-" or empty = off) are cron expressions evaluated in
CIVALPHA_SCHEDULE_ZONE. Six fields mean seconds first (the format used before, e.g. "0 30 22 * * MON-FRI"); five
fields are standard cron. A scheduled run is skipped while a job of the same type is queued or running.

A second thread polls live quotes in market hours (ADR-0006, CIVALPHA_QUOTES_SECONDS); it is independent of the job queue.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from .errors import Problem
from .jobs import Jobs
from .market.quotes import run_poller
from .settings import settings
from .tasks import TASKS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("civalpha.worker")

POLL_SECONDS = 1.0
_stop = False


class Schedule:
    def __init__(self, job_type: str, expr: str, zone: str):
        self.job_type = job_type
        self.zone = ZoneInfo(zone)
        expr = (expr or "").strip()
        self.enabled = bool(expr) and expr != "-"
        if self.enabled:
            fields = expr.split()
            self.iter = croniter(expr, datetime.now(self.zone), second_at_beginning=len(fields) == 6)
            self.next = self.iter.get_next(datetime)

    def due(self, now: datetime) -> bool:
        if not self.enabled or now < self.next:
            return False
        while self.next <= now:
            self.next = self.iter.get_next(datetime)
        return True


def schedules() -> list[Schedule]:
    s = settings().schedule
    out = [Schedule("PIPELINE_RUN", s.pipeline_cron, s.zone), Schedule("RESOLVE_OUTCOMES", s.outcomes_cron, s.zone)]
    for x in out:
        if x.enabled:
            log.info("schedule %s: next run %s", x.job_type, x.next.isoformat())
    return out


def _unknown(params: dict, log) -> None:
    raise Problem("unknown job type")


def run_forever() -> None:
    jobs = Jobs()
    n = jobs.fail_interrupted()
    if n:
        log.warning("%d job(s) were interrupted by a restart and marked FAILED", n)
    sched = schedules()
    quotes_stop = threading.Event()
    threading.Thread(target=run_poller, args=(quotes_stop,), name="live-quotes", daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: (globals().__setitem__("_stop", True), quotes_stop.set()))
    log.info("worker ready")
    while not _stop:
        for s in sched:
            if s.due(datetime.now(s.zone)) and not jobs.running(s.job_type):
                jobs.submit(s.job_type, {"trigger": "schedule"})
        job = jobs.claim()
        if job is None:
            time.sleep(POLL_SECONDS)
            continue
        jobs.run(job, TASKS.get(job["job_type"], _unknown))


if __name__ == "__main__":
    run_forever()
