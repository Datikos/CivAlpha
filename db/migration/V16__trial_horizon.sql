-- ADR-0001: a trial's key names its label horizon (rule@featureSet@<h>d) so the same inputs on another label are another
-- trial. Every AI row registered before this migration was backtested on the 10-day next-close label.
UPDATE trial_registry SET trial_key = trial_key || '@10d' WHERE family = 'AI' AND trial_key NOT LIKE '%d';
