-- Signal health: the monthly information coefficient of every model input against the 21-day excess return over the
-- sector ETF, with a multiple-testing-aware grade and a decay flag. Written by the ML service; one row per run.
CREATE TABLE signal_study_run (
    id           BIGSERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff  DATE        NOT NULL,
    config       JSONB       NOT NULL,
    headline     TEXT        NOT NULL,
    result       JSONB       NOT NULL
);
