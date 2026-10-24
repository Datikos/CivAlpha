-- ADR-0009: every official policy event belongs to a stream; the models read the material streams only (pit.MODEL_STREAMS).
-- Expand only: news-only events keep NULL, and official trade events are classified from their stored source document
-- by EventService.classify_unstreamed (the pipeline's policy-event step and the EVENT_BACKFILL job), not here.
ALTER TABLE policy_event ADD COLUMN stream TEXT CHECK (stream IN ('PRESIDENTIAL', 'USTR_301', 'SECTION_232', 'EXPORT_CONTROL',
                                                                  'FOMC', 'MANUAL', 'AD_CVD', 'OTHER_TRADE'));
UPDATE policy_event SET stream = 'FOMC' WHERE category = 'MONETARY_POLICY' AND evidence_status = 'OFFICIAL';
