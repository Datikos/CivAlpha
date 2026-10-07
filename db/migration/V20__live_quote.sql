-- ADR-0006: the latest intraday quote per symbol, for display only. Replaced on every poll; no history. Features,
-- forecasts, the recorded book and the portfolio advice never read this table: they work on daily closes (price_bar).
CREATE TABLE live_quote (
    symbol       TEXT PRIMARY KEY,                -- the platform's symbol (BRK.B, not the vendor's brk-b)
    company_id   BIGINT REFERENCES company(id),   -- null for benchmark ETFs and held symbols outside the universe
    price        NUMERIC NOT NULL,                -- Tiingo's tngoLast: the last IEX trade or the mid
    prev_close   NUMERIC,                         -- previous close across all exchanges
    open         NUMERIC,
    high         NUMERIC,
    low          NUMERIC,
    volume       BIGINT,                          -- IEX volume during the day
    quoted_at    TIMESTAMPTZ NOT NULL,            -- the provider's timestamp of the quote
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider     TEXT NOT NULL
);
