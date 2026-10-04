-- Strategy lab: classic entry/exit rules and the AI decision-maker backtested on one out-of-sample window
-- (written by the ML service), plus the AI's daily decisions (written by the backend, append-only).
CREATE TABLE strategy_run (
    id           BIGSERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff  DATE        NOT NULL,
    oos_start    DATE        NOT NULL,   -- first day every strategy is scored on
    config       JSONB       NOT NULL,
    summary      TEXT        NOT NULL,
    is_demo      BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE strategy_result (
    run_id           BIGINT  NOT NULL REFERENCES strategy_run(id) ON DELETE CASCADE,
    strategy_key     TEXT    NOT NULL,
    family           TEXT    NOT NULL,   -- BENCHMARK | TREND | MEAN_REVERSION | FUNDAMENTAL | EVENT | AI
    name             TEXT    NOT NULL,
    description      JSONB   NOT NULL,   -- entry / exit rule, origin, sizing
    params           JSONB   NOT NULL,
    metrics          JSONB   NOT NULL,
    equity           JSONB   NOT NULL,   -- [{date, equity, drawdown}] sampled weekly
    yearly           JSONB   NOT NULL,
    cost_sensitivity JSONB   NOT NULL,
    verdict          TEXT    NOT NULL,
    PRIMARY KEY (run_id, strategy_key)
);

CREATE TABLE strategy_trade (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT  NOT NULL REFERENCES strategy_run(id) ON DELETE CASCADE,
    strategy_key  TEXT    NOT NULL,
    company_id    BIGINT REFERENCES company(id),   -- NULL for ETF positions
    symbol        TEXT    NOT NULL,
    entry_date    DATE    NOT NULL,
    exit_date     DATE,                            -- NULL while still open at the end of the test
    trade_return  DOUBLE PRECISION,
    holding_days  INT     NOT NULL,
    entry_reason  TEXT    NOT NULL,
    exit_reason   TEXT    NOT NULL
);
CREATE INDEX strategy_trade_run_idx ON strategy_trade(run_id, strategy_key, entry_date);

CREATE TABLE strategy_decision (
    id            BIGSERIAL PRIMARY KEY,
    company_id    BIGINT      NOT NULL REFERENCES company(id),
    as_of_date    DATE        NOT NULL,
    strategy_key  TEXT        NOT NULL,
    action        TEXT        NOT NULL CHECK (action IN ('ENTER', 'EXIT', 'HOLD', 'STAY_OUT')),
    probability   DOUBLE PRECISION NOT NULL CHECK (probability >= 0 AND probability <= 1),
    entry_p       DOUBLE PRECISION NOT NULL,
    exit_p        DOUBLE PRECISION NOT NULL,
    weight        DOUBLE PRECISION NOT NULL,
    rank          INT         NOT NULL,
    factors       JSONB       NOT NULL,
    rule_votes    JSONB       NOT NULL,
    model         JSONB       NOT NULL,
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo       BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE (company_id, as_of_date, strategy_key)
);
CREATE INDEX strategy_decision_date_idx ON strategy_decision(strategy_key, as_of_date);

-- Decisions are a record of what the AI said at the time; they are never edited (same rule as forecasts).
CREATE FUNCTION forbid_decision_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'strategy decision % is immutable', OLD.id;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER strategy_decision_immutable BEFORE UPDATE OR DELETE ON strategy_decision
    FOR EACH ROW EXECUTE FUNCTION forbid_decision_mutation();

-- The language-model explanation is stored apart so adding it later never touches the decision row.
CREATE TABLE decision_explanation (
    decision_id  BIGINT PRIMARY KEY REFERENCES strategy_decision(id),
    text         TEXT        NOT NULL,
    model        TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
