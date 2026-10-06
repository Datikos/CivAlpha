-- Job progress: the worker reports how far a job is (steps done / total and the current step) so the UI can show a
-- percentage. A queued job has no start time until the worker claims it (started_at used to default to the time
-- the job was submitted, which made the duration timer run while a job was still waiting).
ALTER TABLE pipeline_job
    ADD COLUMN progress_done  INT,
    ADD COLUMN progress_total INT,
    ADD COLUMN progress_step  TEXT,
    ALTER COLUMN started_at DROP DEFAULT,
    ALTER COLUMN started_at DROP NOT NULL;

UPDATE pipeline_job SET started_at = NULL WHERE status = 'QUEUED';
