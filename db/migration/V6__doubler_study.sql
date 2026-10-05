-- Doubler study: how often a stock doubled within 21/42/63 trading days, the profile of those stock-days and
-- whether a point-in-time screen finds them more often than chance. Written by the ML service; one row per run.
CREATE TABLE doubler_study_run (
    id           BIGSERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff  DATE        NOT NULL,
    config       JSONB       NOT NULL,
    headline     TEXT        NOT NULL,
    result       JSONB       NOT NULL
);
