-- ADR-0004: the owner's own holdings and the platform's advice on them, recorded append-only and scored.
-- A fresh installation has no portfolio: the first holding or cash amount the owner enters creates it. Every table
-- carries portfolio_id so several portfolios (ADR-0005, multi-tenant) need no reshaping of these rows.
CREATE TABLE portfolio (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    cash_usd    NUMERIC     NOT NULL DEFAULT 0 CHECK (cash_usd >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE holding (
    id            BIGSERIAL PRIMARY KEY,
    portfolio_id  BIGINT      NOT NULL REFERENCES portfolio(id) ON DELETE CASCADE,
    symbol        TEXT        NOT NULL CHECK (symbol = upper(symbol) AND length(symbol) BETWEEN 1 AND 12),
    company_id    BIGINT      REFERENCES company(id),     -- null while the symbol is not in the universe
    shares        NUMERIC     NOT NULL CHECK (shares > 0),
    avg_cost_usd  NUMERIC     NOT NULL CHECK (avg_cost_usd >= 0),
    opened_on     DATE,
    note          TEXT        CHECK (length(note) <= 500),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (portfolio_id, symbol)
);

-- One row per holding per as-of date per portfolio state: `basis` is a hash of the holdings and cash the advice was
-- computed on, so editing the portfolio during a day adds new rows instead of changing the old ones. The page and the
-- track record read the newest basis of each day.
CREATE TABLE holding_advice (
    id              BIGSERIAL PRIMARY KEY,
    portfolio_id    BIGINT      NOT NULL REFERENCES portfolio(id),
    as_of_date      DATE        NOT NULL,                -- the recorded book's decision date the advice is built on
    basis           TEXT        NOT NULL,
    symbol          TEXT        NOT NULL,
    company_id      BIGINT      REFERENCES company(id),
    shares          NUMERIC     NOT NULL,
    close           NUMERIC,                             -- null when the symbol has no price on the as-of date
    value_usd       NUMERIC     NOT NULL,                -- shares x close, or shares x average cost without a price
    weight          DOUBLE PRECISION NOT NULL,           -- value / (all holdings + cash)
    action          TEXT        NOT NULL CHECK (action IN ('NOT_COVERED', 'REVIEW', 'TRIM', 'SELL', 'ADD', 'HOLD')),
    layer           TEXT        NOT NULL CHECK (layer IN ('DATA', 'RISK', 'MODEL', 'NONE')),
    headline        TEXT        NOT NULL CHECK (headline IN ('NOT_COVERED', 'REVIEW', 'TRIM', 'SELL', 'ADD', 'HOLD')),
    rule            TEXT        NOT NULL,                -- the rule that decided `action`
    target_weight   DOUBLE PRECISION,
    trade_shares    NUMERIC,                             -- negative = sell, positive = buy; null when nothing to trade
    review_on       DATE        NOT NULL,                -- as-of + 21 trading days (weekdays; holidays not known ahead)
    triggers        JSONB       NOT NULL,                -- what would change the advice
    reasons         JSONB       NOT NULL,                -- every rule that fired, in order, with its numbers
    model           JSONB       NOT NULL,                -- decision id, probability raw and calibrated, rank, thresholds, live-test verdict
    code_version    TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (portfolio_id, as_of_date, basis, symbol)
);
CREATE INDEX holding_advice_day_idx ON holding_advice(portfolio_id, as_of_date, created_at);

CREATE FUNCTION forbid_advice_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'holding advice % is immutable', OLD.id;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER holding_advice_immutable BEFORE UPDATE OR DELETE ON holding_advice
    FOR EACH ROW EXECUTE FUNCTION forbid_advice_mutation();

-- What happened over the 21 trading days after the advice: stored apart so the advice row never changes.
CREATE TABLE holding_advice_outcome (
    advice_id         BIGINT PRIMARY KEY REFERENCES holding_advice(id),
    window_end_date   DATE    NOT NULL,
    stock_return      NUMERIC NOT NULL,
    benchmark_return  NUMERIC NOT NULL,
    excess_return     NUMERIC NOT NULL,
    resolved_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
