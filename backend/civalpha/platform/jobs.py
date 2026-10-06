"""Background jobs on the pipeline_job table, with a persisted log.

The API inserts a job as QUEUED; the worker process claims jobs one at a time (oldest first, SKIP LOCKED) and runs
them, so long ingestion and model training never block API requests. A job that raises Problem logs its message
("FAILED: <message>"); any other exception logs its type too.

Progress: the log callback a job body receives is a JobLog, so `log.progress(done, total, step)` stores how far the
job is; the UI shows it as a percentage with the current step. Job bodies use the `Progress` counter (or `report`)
rather than calling that directly, so a plain callable (tests, scripts) works as a log too.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from .errors import Problem
from .sql import Db, db, jsonb

log = logging.getLogger("civalpha.jobs")

Log = Callable[[str], None]
ACTIVE = ("QUEUED", "RUNNING")
PROGRESS_INTERVAL = 0.5   # seconds between two progress writes that only move the counter (label changes always write)


def report(log: Log, done: int, total: int | None, step: str | None = None) -> None:
    """Stores the progress of the job behind `log` when it is a JobLog; any other callable ignores it."""
    fn = getattr(log, "progress", None)
    if fn is not None:
        fn(done, total, step)


class Progress:
    """Counts the steps of one job body.

    `Progress(log, total, "first step")` starts at 0 of `total`; `step("next step")` marks one more step done and names
    what runs now (the label stays when none is given); `at(label)` only renames the current step; `extend(n)` adds
    steps discovered on the way; `finish()` sets done = total. `child()` is a log for a nested service: its lines pass
    through and its own progress reports refine the current label ("SEC filings Apple (3/352) · 10-K 2025-10-31")
    without moving this counter.
    """

    def __init__(self, log: Log, total: int, label: str | None = None):
        self.log = log
        self.done = 0
        self.total = max(0, int(total))
        self.label = label
        self._write()

    def at(self, label: str | None) -> None:
        self.label = label
        self._write()

    def step(self, label: str | None = None) -> None:
        self.done = min(self.done + 1, self.total)
        if label is not None:
            self.label = label
        self._write()

    def extend(self, n: int) -> None:
        self.total += max(0, int(n))
        self._write()

    def finish(self) -> None:
        self.done = self.total
        self._write()

    def child(self) -> Log:
        parent = self

        def line(text: str) -> None:
            parent.log(text)

        def progress(done: int, total: int | None, step: str | None = None) -> None:
            detail = step or (f"{done}/{total}" if total else None)
            label = f"{parent.label} · {detail}" if parent.label and detail else (detail or parent.label)
            report(parent.log, parent.done, parent.total, label)

        line.progress = progress  # type: ignore[attr-defined]
        return line

    def _write(self) -> None:
        report(self.log, self.done, self.total, self.label)


class JobLog:
    """The log callback of one job: `log("text")` appends a timestamped line; `log.progress(done, total, step)` stores
    how far the job is. Counter-only updates are written at most every `interval` seconds; the first report, a new
    label or total, and completion are always written."""

    def __init__(self, jobs: "Jobs", job_id: int, job_type: str, interval: float = PROGRESS_INTERVAL):
        self.jobs = jobs
        self.job_id = job_id
        self.job_type = job_type
        self.interval = interval
        self._last: tuple[int, int | None, str | None] | None = None
        self._written_at = 0.0

    def __call__(self, text: str) -> None:
        log.info("job %s %s: %s", self.job_id, self.job_type, text)
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.jobs.db.execute("UPDATE pipeline_job SET log = log || :l WHERE id = :id", l=f"{stamp}  {text}\n", id=self.job_id)

    def progress(self, done: int, total: int | None, step: str | None = None) -> None:
        total = None if total is None else max(0, int(total))
        done = max(0, int(done)) if total is None else min(max(0, int(done)), total)
        state = (done, total, step)
        if state == self._last:
            return
        now = time.monotonic()
        counter_only = self._last is not None and (total, step) == (self._last[1], self._last[2])
        final = total is not None and done >= total
        if counter_only and not final and now - self._written_at < self.interval:
            return
        self.jobs.progress(self.job_id, done, total, step)
        self._last, self._written_at = state, now


class Jobs:
    def __init__(self, database: Db | None = None, progress_interval: float = PROGRESS_INTERVAL):
        self.db = database or db()
        self.progress_interval = progress_interval

    def submit(self, job_type: str, params: dict | None = None) -> dict:
        # started_at stays NULL until the worker claims the job: a queued job has not started
        return self.db.one("""INSERT INTO pipeline_job (job_type, status, params, started_at) VALUES (:t, 'QUEUED', CAST(:p AS jsonb), NULL)
                              RETURNING *""", t=job_type, p=jsonb(params or {}))

    def running(self, job_type: str) -> bool:
        """True when a job of this type is queued or running."""
        return bool(self.db.scalar("SELECT exists(SELECT 1 FROM pipeline_job WHERE job_type = :t AND status IN ('QUEUED', 'RUNNING'))",
                                   t=job_type))

    def get(self, job_id: int) -> dict | None:
        return self.db.one("SELECT * FROM pipeline_job WHERE id = :id", id=job_id)

    def recent(self) -> list[dict]:
        return self.db.all("SELECT * FROM pipeline_job ORDER BY id DESC LIMIT 30")

    # ------------------------------------------------------------------ worker side
    def fail_interrupted(self) -> int:
        """Jobs that were running when the worker stopped can never finish."""
        return self.db.execute("""UPDATE pipeline_job SET status = 'FAILED', finished_at = now(), log = log || 'interrupted by restart' || chr(10)
                                  WHERE status = 'RUNNING'""")

    def claim(self) -> dict | None:
        return self.db.one("""
            UPDATE pipeline_job SET status = 'RUNNING', started_at = now()
            WHERE id = (SELECT id FROM pipeline_job WHERE status = 'QUEUED' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *""")

    def progress(self, job_id: int, done: int, total: int | None, step: str | None) -> None:
        self.db.execute("UPDATE pipeline_job SET progress_done = :d, progress_total = :t, progress_step = :s WHERE id = :id",
                        d=done, t=total, s=step, id=job_id)

    def logger(self, job_id: int, job_type: str) -> JobLog:
        return JobLog(self, job_id, job_type, self.progress_interval)

    def run(self, job: dict, body: Callable[[dict, Log], None]) -> bool:
        line = self.logger(job["id"], job["job_type"])
        try:
            body(job["params"] or {}, line)
            # a finished job is complete whatever its last report said
            self.db.execute("""UPDATE pipeline_job SET status = 'SUCCEEDED', finished_at = now(), progress_done = progress_total
                               WHERE id = :id""", id=job["id"])
            return True
        except Exception as e:  # noqa: BLE001 - every failure must end the job with a readable log line
            log.exception("job %s failed", job["id"])
            # Problem, and the models' ValueError ("not enough history ..."), carry a readable message
            msg = str(e) if isinstance(e, (Problem, ValueError)) and str(e) else f"{type(e).__name__}: {e}"
            line("FAILED: " + msg)
            self.db.execute("UPDATE pipeline_job SET status = 'FAILED', finished_at = now() WHERE id = :id", id=job["id"])
            return False
