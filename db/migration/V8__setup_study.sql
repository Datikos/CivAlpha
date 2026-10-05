-- Setup playbook: catalysts and technical situations (earnings surprise, dividend raise, breakout, golden cross,
-- oversold pullback, crash, volume surge, tariff/rate shock), each scored on the excess return over the sector ETF
-- that followed, with base rates and a multiple-testing-aware verdict, plus which setups fired in the last sessions.
-- Written by the ML service; one row per run.
CREATE TABLE setup_study_run (
    id           BIGSERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff  DATE        NOT NULL,
    config       JSONB       NOT NULL,
    headline     TEXT        NOT NULL,
    result       JSONB       NOT NULL
);
