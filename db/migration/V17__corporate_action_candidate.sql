-- ADR-0003: corporate actions detected in 8-K text, one row per (filing, method). A candidate is never a corporate
-- action: the owner confirms it and enters the action (with its value) by hand. `reconciled_action_id` is the recorded
-- split or spin-off of the same company with ex-date in [accepted - 5 days, accepted + 120 days]; a candidate with a kind
-- other than NONE and no reconciled action is a feed gap.
CREATE TABLE corporate_action_candidate (
    id                    BIGSERIAL PRIMARY KEY,
    filing_id             BIGINT      NOT NULL REFERENCES filing(id),
    company_id            BIGINT      NOT NULL REFERENCES company(id),
    accepted_at           TIMESTAMPTZ NOT NULL,
    method                TEXT        NOT NULL CHECK (method IN ('KEYWORD', 'JEV')),
    model                 TEXT,                               -- versioned model id for JEV, rule version for KEYWORD
    kind                  TEXT        NOT NULL CHECK (kind IN ('SPIN_OFF_OR_DISTRIBUTION', 'FORWARD_SPLIT', 'REVERSE_SPLIT',
                                                               'SPECIAL_DIVIDEND', 'MERGER_OR_DELISTING', 'NONE')),
    probability           DOUBLE PRECISION,                   -- JEV: P(the filing changes the shares a holder owns); KEYWORD: null
    evidence              TEXT,                               -- KEYWORD: the matched phrase with context; JEV: top choice probabilities
    reconciled_action_id  BIGINT      REFERENCES corporate_action(id),
    review                TEXT        NOT NULL DEFAULT 'UNREVIEWED' CHECK (review IN ('UNREVIEWED', 'CONFIRMED', 'REJECTED')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (filing_id, method)
);
CREATE INDEX corporate_action_candidate_gap_idx ON corporate_action_candidate (kind, reconciled_action_id);
