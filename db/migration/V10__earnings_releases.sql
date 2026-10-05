-- Earnings releases: the press-release exhibit (EX-99.1) of every results 8-K (Item 2.02) and its guidance tone by
-- keyword rules (an ESTIMATED value with the matched sentence as evidence). The announcement time itself is the 8-K's
-- acceptance time on the filing row; the market's reaction is computed from prices, not stored.
CREATE TABLE earnings_release (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT      NOT NULL REFERENCES company(id),
    filing_id           BIGINT      NOT NULL UNIQUE REFERENCES filing(id),
    accession_no        TEXT        NOT NULL,
    accepted_at         TIMESTAMPTZ NOT NULL,
    exhibit_name        TEXT,
    exhibit_document_id BIGINT      REFERENCES source_document(id),
    guidance_tone       TEXT        NOT NULL,    -- RAISED | LOWERED | MAINTAINED | PROVIDED | NONE | UNKNOWN (no exhibit found)
    guidance_text       TEXT,                    -- the sentence the rule matched
    method              TEXT        NOT NULL,    -- RULE_KEYWORD
    extractor_version   TEXT        NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX earnings_release_company_idx ON earnings_release(company_id, accepted_at);
