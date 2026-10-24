-- ADR-0010: whether a Form 4 says the trade was made under a Rule 10b5-1 trading plan (the checkbox mandatory since
-- 2023-04-01; the SEC data sets' SUBMISSION.AFF10B5ONE, the XML's aff10b5One). NULL: not reported. Expand only: stored
-- rows get the flag from their stored source documents (InsiderIngestionService.backfill_plan_flags), not here.
ALTER TABLE insider_transaction ADD COLUMN plan_10b5_1 BOOLEAN;
