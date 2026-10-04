-- Price corrections: a re-import with different values updates price_bar (version n+1) and the replaced
-- values are archived here, so every value that was ever used stays recoverable with its source document.
ALTER TABLE price_bar ADD COLUMN version INT NOT NULL DEFAULT 1;

CREATE TABLE price_bar_revision (
    id                       BIGSERIAL PRIMARY KEY,
    symbol                   TEXT        NOT NULL,
    trade_date               DATE        NOT NULL,
    company_id               BIGINT REFERENCES company(id),
    version                  INT         NOT NULL,   -- version of the replaced row
    open                     NUMERIC,
    high                     NUMERIC,
    low                      NUMERIC,
    close                    NUMERIC     NOT NULL,
    volume                   BIGINT,
    provider                 TEXT        NOT NULL,
    source_document_id       BIGINT REFERENCES source_document(id),
    ingested_at              TIMESTAMPTZ NOT NULL,   -- when the replaced value was ingested
    superseded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by_document_id BIGINT REFERENCES source_document(id),
    is_demo                  BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX price_bar_revision_key_idx ON price_bar_revision(symbol, trade_date);
