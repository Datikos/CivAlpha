"""Background jobs: a queued job has not started, and a job body's progress reports are stored for the UI."""
from civalpha.platform.jobs import Jobs, Progress, report


def _row(jobs, job_id):
    r = jobs.get(job_id)
    return r["status"], r["progress_done"], r["progress_total"], r["progress_step"]


def test_queued_job_has_no_start_time_until_the_worker_claims_it(tdb):
    jobs = Jobs(tdb)
    j = jobs.submit("EVALUATE")
    assert j["status"] == "QUEUED" and j["started_at"] is None and j["finished_at"] is None
    assert (j["progress_done"], j["progress_total"], j["progress_step"]) == (None, None, None)
    claimed = jobs.claim()
    assert claimed["id"] == j["id"] and claimed["status"] == "RUNNING" and claimed["started_at"] is not None


def test_progress_reports_are_stored_and_a_finished_job_is_complete(tdb):
    jobs = Jobs(tdb, progress_interval=0)
    j = jobs.submit("PIPELINE_RUN")
    seen = []

    def body(params, log):
        p = Progress(log, 4, "CSV imports")
        seen.append(_row(jobs, j["id"]))
        p.step("SEC filings Apple (1/2)")
        seen.append(_row(jobs, j["id"]))
        child = p.child()                       # a nested service refines the label, never the counter
        child.progress(3, 10, "AAPL: 10-K 2025-09-27 (3/10)")
        child("nested line")
        seen.append(_row(jobs, j["id"]))
        p.step("SEC filings Intel (2/2)")       # done = 2 of 4; the job then returns before finishing its count

    assert jobs.run(j, body)
    assert seen == [
        ("QUEUED", 0, 4, "CSV imports"),        # run() does not claim; the status is the worker's business
        ("QUEUED", 1, 4, "SEC filings Apple (1/2)"),
        ("QUEUED", 1, 4, "SEC filings Apple (1/2) · AAPL: 10-K 2025-09-27 (3/10)"),
    ]
    assert _row(jobs, j["id"]) == ("SUCCEEDED", 4, 4, "SEC filings Intel (2/2)")
    assert "nested line" in jobs.get(j["id"])["log"]


def test_failed_job_keeps_the_progress_it_reached(tdb):
    jobs = Jobs(tdb, progress_interval=0)
    j = jobs.submit("PRICE_SYNC")

    def body(params, log):
        p = Progress(log, 10)
        p.step("prices AAPL (2/10)")
        raise ValueError("rate limited")

    assert not jobs.run(j, body)
    assert _row(jobs, j["id"]) == ("FAILED", 1, 10, "prices AAPL (2/10)")
    assert "FAILED: rate limited" in jobs.get(j["id"])["log"]


def test_counter_only_updates_are_throttled_but_labels_and_completion_always_write(tdb):
    jobs = Jobs(tdb, progress_interval=3600)
    j = jobs.submit("PRICE_SYNC")
    log = jobs.logger(j["id"], j["job_type"])
    log.progress(0, 100, "prices")
    log.progress(1, 100, "prices")              # same label, too soon: dropped
    assert _row(jobs, j["id"])[1:] == (0, 100, "prices")
    log.progress(2, 100, "prices MSFT")         # new label: written
    assert _row(jobs, j["id"])[1:] == (2, 100, "prices MSFT")
    log.progress(100, 100, "prices MSFT")       # completion: written
    assert _row(jobs, j["id"])[1:] == (100, 100, "prices MSFT")


def test_plain_callables_work_as_logs_and_ignore_progress():
    lines = []
    p = Progress(lines.append, 3, "first")
    p.step("second")
    p.extend(2)
    child = p.child()
    child.progress(1, 2)
    child("hello")
    report(lines.append, 1, 2, "x")
    p.finish()
    assert lines == ["hello"] and (p.done, p.total) == (5, 5)
