-- The language model's review of an AI decision: a second, logged opinion on the final candidates (ENTER actions).
-- Stored apart from the decision so the review can be added later and scored against outcomes without touching the
-- decision row. In advisory mode the review never changes the decision; in veto mode (CIVALPHA_LLM_REVIEW=veto) a
-- DISAGREE turns the ENTER into STAY_OUT before it is stored, and the row records that it did (veto = true).
CREATE TABLE decision_review (
    decision_id  BIGINT PRIMARY KEY REFERENCES strategy_decision(id),
    stance       TEXT        NOT NULL CHECK (stance IN ('AGREE', 'CAUTION', 'DISAGREE')),
    confidence   TEXT        NOT NULL CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    rationale    TEXT        NOT NULL,
    flags        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    brief        JSONB       NOT NULL,              -- exactly what the model was shown, so the review is auditable
    veto         BOOLEAN     NOT NULL DEFAULT false, -- true when the review changed the stored action (veto mode, DISAGREE)
    model        TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX decision_review_stance_idx ON decision_review(stance);
