-- ADR-0003 phase 3: the owner reviews corporate-action candidates on the Corporate actions page. Who reviewed, when and
-- why are kept on the candidate (the audit trail keeps every change as well). Expand only: existing rows keep NULLs.
ALTER TABLE corporate_action_candidate ADD COLUMN reviewed_at TIMESTAMPTZ;
ALTER TABLE corporate_action_candidate ADD COLUMN reviewed_by BIGINT REFERENCES app_user(id);
ALTER TABLE corporate_action_candidate ADD COLUMN review_note TEXT CHECK (length(review_note) <= 500);
