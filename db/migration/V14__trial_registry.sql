-- Trial registry: every strategy variant and feature-set variant ever backtested on this history, one row per
-- distinct trial. The Deflated Sharpe Ratio of the strategy lab deflates for the number of trials in this table, not for
-- the number of rows in the latest run, so a variant that was tried once and dropped still counts against the survivors.
-- trial_key is strategy_key for a rule and strategy_key@feature_set for a model-based strategy (AI_GBM@GBM_AI_39), so
-- the same rule on a different input list is a different trial. Rows are upserted by trial_key: the metrics are those
-- of the latest run that included the trial; first_tested_at never moves.
CREATE TABLE trial_registry (
    id              BIGSERIAL PRIMARY KEY,
    trial_key       TEXT        NOT NULL UNIQUE,
    strategy_key    TEXT        NOT NULL,
    feature_set     TEXT,                              -- feature-set identifier (GBM_AI_39, GBM_FUND_18, ...); NULL for a rule
    family          TEXT        NOT NULL,
    description     TEXT        NOT NULL,
    first_tested_at TIMESTAMPTZ NOT NULL,
    last_tested_at  TIMESTAMPTZ NOT NULL,
    runs            INTEGER     NOT NULL DEFAULT 1,    -- lab runs that included this trial
    last_run_id     BIGINT      REFERENCES strategy_run(id),
    sharpe          DOUBLE PRECISION,
    excess_return   DOUBLE PRECISION,                  -- annualized, over equal-weight buy & hold
    excess_ci_low   DOUBLE PRECISION,
    excess_ci_high  DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    git_commit      TEXT,                              -- code that produced the latest metrics (CIVALPHA_GIT_COMMIT at build time)
    params          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    source          TEXT        NOT NULL,              -- lab | backfill | backfill-inferred | ablation
    notes           TEXT
);
CREATE INDEX trial_registry_family_idx ON trial_registry(family);
