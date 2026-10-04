"""Background jobs on the pipeline_job table, with a persisted log.

The API inserts a job as QUEUED; the worker process claims jobs one at a time (oldest first, SKIP LOCKED) and runs
them, so long ingestion and model training never block API requests. A job that raises Problem logs its message
("FAILED: <message>"); any other exception logs its type too.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from .errors import Problem
from .sql import Db, db, jsonb

log = logging.getLogger("civalpha.jobs")

Log = Callable[[str], None]
ACTIVE = ("QUEUED", "RUNNING")


class Jobs:
    def __init__(self, database: Db | None = None):
        self.db = database or db()

    def submit(self, job_type: str, params: dict | None = None) -> dict:
        return self.db.one("INSERT INTO pipeline_job (job_type, status, params) VALUES (:t, 'QUEUED', CAST(:p AS jsonb)) RETURNING *",
                           t=job_type, p=jsonb(params or {}))

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

    def logger(self, job_id: int, job_type: str) -> Log:
        def line(text: str) -> None:
            log.info("job %s %s: %s", job_id, job_type, text)
            stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self.db.execute("UPDATE pipeline_job SET log = log || :l WHERE id = :id", l=f"{stamp}  {text}\n", id=job_id)
        return line

    def run(self, job: dict, body: Callable[[dict, Log], None]) -> bool:
        line = self.logger(job["id"], job["job_type"])
        try:
            body(job["params"] or {}, line)
            self.db.execute("UPDATE pipeline_job SET status = 'SUCCEEDED', finished_at = now() WHERE id = :id", id=job["id"])
            return True
        except Exception as e:  # noqa: BLE001 - every failure must end the job with a readable log line
            log.exception("job %s failed", job["id"])
            # Problem, and the models' ValueError ("not enough history ..."), carry a readable message
            msg = str(e) if isinstance(e, (Problem, ValueError)) and str(e) else f"{type(e).__name__}: {e}"
            line("FAILED: " + msg)
            self.db.execute("UPDATE pipeline_job SET status = 'FAILED', finished_at = now() WHERE id = :id", id=job["id"])
            return False
