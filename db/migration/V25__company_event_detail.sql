-- ADR-0011 decision 9: the language model's answer for an item 5.02 section (method exec-llm-1), and the Message Batches
-- the platform submitted, so a batch is collected once, by the next pipeline run or the job, whichever comes first.
ALTER TABLE company_event ADD COLUMN detail JSONB;
CREATE TABLE llm_batch (
    id              TEXT PRIMARY KEY,                -- the Message Batch id
    purpose         TEXT        NOT NULL,            -- company_events_5_02
    model           TEXT        NOT NULL,
    request_count   INTEGER     NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    collected_at    TIMESTAMPTZ,
    result          JSONB                            -- counts per outcome when collected
);
