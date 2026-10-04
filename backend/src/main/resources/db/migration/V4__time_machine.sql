-- Time machine: forecasts made as of a past close with only the data known then (models, AI decisions, return
-- bands), stored together with the outcomes that followed. Written by the ML service; one row per run.
CREATE TABLE time_machine_run (
    id           BIGSERIAL PRIMARY KEY,
    as_of_date   DATE        NOT NULL,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff  DATE        NOT NULL,   -- latest price date when the run was made (later outcomes were unknown then)
    headline     TEXT        NOT NULL,
    result       JSONB       NOT NULL,
    is_demo      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX time_machine_run_date_idx ON time_machine_run(as_of_date);
