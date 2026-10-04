-- CivAlpha works on real data only: the synthetic demo dataset is gone, and with it the is_demo flag.
-- A database that still holds demo rows must be recreated (docker compose down -v) instead of migrated.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM company WHERE is_demo) OR EXISTS (SELECT 1 FROM source_document WHERE is_demo) THEN
        RAISE EXCEPTION 'this database holds the synthetic demo dataset; start from an empty database (docker compose down -v) and add companies on the Universe page';
    END IF;
END $$;

ALTER TABLE company           DROP COLUMN is_demo;
ALTER TABLE source_document   DROP COLUMN is_demo;
ALTER TABLE filing            DROP COLUMN is_demo;
ALTER TABLE xbrl_fact         DROP COLUMN is_demo;
ALTER TABLE filing_passage    DROP COLUMN is_demo;
ALTER TABLE company_exposure  DROP COLUMN is_demo;
ALTER TABLE price_bar         DROP COLUMN is_demo;
ALTER TABLE price_bar_revision DROP COLUMN is_demo;
ALTER TABLE corporate_action  DROP COLUMN is_demo;
ALTER TABLE macro_observation DROP COLUMN is_demo;
ALTER TABLE policy_event      DROP COLUMN is_demo;
ALTER TABLE actor_record      DROP COLUMN is_demo;
ALTER TABLE model_version     DROP COLUMN is_demo;
ALTER TABLE forecast          DROP COLUMN is_demo;
ALTER TABLE model_evaluation  DROP COLUMN is_demo;
ALTER TABLE strategy_run      DROP COLUMN is_demo;
ALTER TABLE strategy_decision DROP COLUMN is_demo;
ALTER TABLE time_machine_run  DROP COLUMN is_demo;
