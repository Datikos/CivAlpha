-- Insider transactions (SEC Forms 4): open-market purchases and sales (and other non-derivative transactions) by
-- officers, directors and 10% owners, from the SEC's quarterly insider-transactions data sets plus the Form 4 XML of
-- filings newer than the latest data set. available_at is the end of the filing day in New York (the data sets carry
-- no acceptance time), which is never earlier than the true acceptance: conservative for point-in-time use.
CREATE TABLE insider_transaction (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT      NOT NULL REFERENCES company(id),
    accession_no        TEXT        NOT NULL,
    trans_sk            TEXT        NOT NULL,            -- row key inside the filing (data set key or XML row index)
    owner_cik           TEXT,
    owner_name          TEXT        NOT NULL,
    relationship        TEXT        NOT NULL,            -- Director | Officer | TenPercentOwner | Other
    title               TEXT,
    trans_date          DATE        NOT NULL,
    filed_date          DATE        NOT NULL,
    available_at        TIMESTAMPTZ NOT NULL,
    trans_code          TEXT        NOT NULL,            -- P purchase, S sale, A grant, M exercise, F tax, G gift, ...
    acquired            BOOLEAN     NOT NULL,
    shares              NUMERIC     NOT NULL,
    price               NUMERIC,
    shares_after        NUMERIC,
    ownership           TEXT,                            -- D direct | I indirect
    security_title      TEXT,
    source              TEXT        NOT NULL,            -- data set name (2025q2_form345) or 'form4-xml'
    source_document_id  BIGINT      REFERENCES source_document(id),
    UNIQUE (accession_no, trans_sk)
);
CREATE INDEX insider_transaction_company_idx ON insider_transaction(company_id, available_at);
