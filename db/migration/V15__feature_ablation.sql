-- Feature fragility study: the recorded book's model with one input group removed or added at a time, scored walk-forward
-- (Brier skill, AUC with CIs) on the forecast target and on the book's own label, with the lab Sharpe as a non-skill
-- column. One row per run; the variants are also registered in trial_registry (source 'ablation').
CREATE TABLE feature_ablation_run (
    id           BIGSERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff  DATE        NOT NULL,
    config       JSONB       NOT NULL,
    headline     TEXT        NOT NULL,
    result       JSONB       NOT NULL
);
