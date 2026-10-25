-- ADR-0011: company events from 8-K item codes (the SEC filing index) and, for item 5.02, from the item's text. Kept apart
-- from `filing`, so the passage rules, the exposure derivation and the corporate-action detector see no new 8-Ks.
CREATE TABLE company_event (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT      NOT NULL REFERENCES company(id),
    accession_no        TEXT        NOT NULL,
    item                TEXT        NOT NULL,            -- the 8-K item the kind comes from (1.01, 5.02, ...)
    kind                TEXT        NOT NULL CHECK (kind IN ('MATERIAL_AGREEMENT', 'AGREEMENT_TERMINATED', 'ACQUISITION_COMPLETED',
                                     'BANKRUPTCY', 'RESTRUCTURING', 'IMPAIRMENT', 'LISTING_NOTICE', 'AUDITOR_CHANGE', 'NON_RELIANCE',
                                     'CYBER_INCIDENT', 'CEO_DEPARTURE', 'CFO_DEPARTURE', 'OFFICER_DIRECTOR_CHANGE')),
    accepted_at         TIMESTAMPTZ NOT NULL,            -- EDGAR acceptance (the index's acceptanceDateTime): the availability time
    filed_date          DATE        NOT NULL,
    method              TEXT        NOT NULL,            -- ITEM, or the text rule's version (exec-1)
    evidence            TEXT        CHECK (length(evidence) <= 300),
    source_document_id  BIGINT      REFERENCES source_document(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (accession_no, kind)
);
CREATE INDEX company_event_company_idx ON company_event(company_id, accepted_at);
CREATE INDEX company_event_kind_idx ON company_event(kind, accepted_at);
