# Pre-registered live test, batch 2: the recorded book's model on the 21-day target

Registered on 2026-10-06, before the first AI_BOOK_21 forecast was issued. Batch 1 (`live-test-2026-10.md`) is untouched:
its population, metrics and verdict rule stand as written. This document adds a second population for a third live model
and changes nothing else. Scoring code: `python -m civalpha.livetest 2`.

## 1. What is being tested

| Model | Feature-set identifier | Inputs | Algorithm | Target |
|---|---|---|---|---|
| AI_BOOK_21 | GBM_AI_39 | the 39 inputs of the recorded book (`strategies.ai.AI_FEATURES`): the 10 BASELINE inputs, 10 technical indicators, the 15-value filed report profile, 2 insider counts, 2 earnings-calendar values | gradient boosting (`strategies.ai.AiModel`), retrained at each issue on every walk-forward panel row whose 21-day label had closed | P(stock total return over 21 trading days beats its sector ETF), close(t) to close(t+21) |

This is the recorded book's input list and algorithm on the platform's forecast target. It is **not** the label the book
trades (10 days from the next close); that horizon is an open decision and gets its own batch if it is ever issued live.
Probabilities are **raw and uncalibrated**. The calibration study of 2026-10-06 showed this model is overconfident; if a
calibrated version is issued later it is a different model kind and a different batch.

## 2. The population

All `LIVE` forecasts with `model_kind = 'AI_BOOK_21'` for the **first 10 distinct as-of dates from 2026-10-05**, one per
(company, as-of date) series, taking for each series the highest version issued **within 3 calendar days after the as-of
date**. Later re-issues are not part of the test. With 351 forecastable companies that is about 3,510 forecasts. The
population closes when the 10th as-of date has been issued; the md5 fingerprint (same definition as batch 1) is then
appended to this document in a dated amendment, and nothing else changes.

Two facts recorded in advance:

* The first as-of date (2026-10-05) is issued on 2026-10-06, after its window opened, like batch 1. Later dates are
  issued by the pipeline run after each close; the issue timestamp is stored and the 3-day rule above is the only filter.
* The 10 as-of dates will span about two weeks, so their 21-day windows overlap almost entirely. The CI method is the
  company-cluster bootstrap of batch 1 (each company's forecasts are one cluster), with the same limitation stated
  there: cross-company dependence on the same dates is not captured, so the CIs are probably too narrow. The as-of-date
  block bootstrap applies from the first batch with at least 60 as-of dates.

## 3. Metrics, CIs and verdict rule

Identical to batch 1, sections 3 to 5: Brier skill versus the realized base rate as the primary metric; AUC and the
confident decile (10% largest |p - 0.5|, hit rate and mean excess minus 40 bp) as secondary; 2,000 cluster resamples with
seed 20261006; verdict INCOMPLETE below 95% resolved by 2026-11-30, else SKILL (Brier-skill CI above 0 and AUC above
0.5), HARMFUL (CI below 0), otherwise NO SKILL. The paired comparison with BASELINE/AUGMENTED on the same (company, date)
rows is reported as a secondary number where both exist.

**Pre-registered expectation.** The walk-forward (evaluation 13, 2026-10-06) gives this model Brier skill -0.025 with a
95% CI of -0.043 to -0.014 and AUC 0.513 (CI 0.481 to 0.533) on 6,028 predictions. The expected verdict is therefore
**HARMFUL or NO SKILL**; SKILL would contradict the walk-forward and would need a further batch before anything changes
in the recorded book.

## 4. The exact query

```sql
WITH dates AS (
  SELECT DISTINCT as_of_date FROM forecast
  WHERE issue_mode = 'LIVE' AND model_kind = 'AI_BOOK_21' AND as_of_date >= DATE '2026-10-05'
  ORDER BY as_of_date LIMIT 10),
scored AS (
  SELECT DISTINCT ON (f.series_key) f.id, f.series_key, f.company_id, f.symbol, f.model_kind, f.as_of_date,
         f.probability::float8 AS p, f.model_version_id, f.version, f.issued_at
  FROM forecast f JOIN dates d ON d.as_of_date = f.as_of_date
  WHERE f.issue_mode = 'LIVE' AND f.model_kind = 'AI_BOOK_21'
    AND f.issued_at < (f.as_of_date + 3)::timestamptz
  ORDER BY f.series_key, f.version DESC)
SELECT s.*, o.outcome, o.excess_return::float8 AS excess_return, o.window_end_date
FROM scored s LEFT JOIN forecast_outcome o ON o.forecast_id = s.id
ORDER BY s.as_of_date, s.company_id;
```

`backend/civalpha/livetest.py` holds this query as `BATCH2_SQL` and scores it with the same functions as batch 1
(`score_model`, `verdict`), unit-tested in `backend/tests/test_livetest.py`.

## 5. Amendments

### Amendment 1 (2026-10-18): the population is closed

The 10th as-of date (2026-10-16) was issued on 2026-10-17 07:37 UTC by a catch-up run (job 215, ADR-0008) after the
scheduled run of 2026-10-16 lost the network. Computed with `python -m civalpha.livetest 2` on 2026-10-18 12:33 UTC
(md5 of `series_key:version` pairs joined by commas in `series_key` order, the batch-1 definition):

| Model | As-of dates | Forecasts | Model versions used | Fingerprint |
|---|---|---|---|---|
| AI_BOOK_21 | 2026-10-05 to 2026-10-16 (10) | 3,159 | 17 `model_version` rows (retrained at each issue) | `52cf870d001d5ddbcde9c913acc39277` |

Facts recorded with it, all consequences of the rule in section 2, which is unchanged:

* **2026-10-09 contributes 0 forecasts.** Its scheduled run (job 186) lost the network (DNS failures from 23:45 UTC);
  all 351 forecasts were issued on 2026-10-12 14:16 UTC, after the three-day limit. The date still counts as one of the
  ten, so the population is 9 x 351 = 3,159 forecasts, not about 3,510.
* **Re-issues inside the window count.** 9 series of 2026-10-14 carry version 2 (2026-10-15 05:46 UTC) and 15 series
  of 2026-10-15 carry version 2 or 3 (2026-10-17), all within three days; the fingerprint uses those versions.
* **2026-10-16's window is open until 2026-10-19 00:00 UTC.** A pipeline run before then that changes a forecast of that
  date adds a version that belongs to the population and changes the fingerprint. No scheduled run falls in that window
  (the next is 2026-10-19 22:30 UTC) and the catch-up is idle while the prices are complete. The scorer checks this
  fingerprint (`livetest.BATCHES["2"]`); if it reports `fingerprintMatches: false` after that instant, the population
  rule stands and this amendment gets the corrected fingerprint with the reason.
* **Confirmed final on 2026-10-19 07:57 UTC**, after that window closed: no version above 1 exists for 2026-10-16 and
  the scorer reports `fingerprintMatches: true`. Outcomes resolve as the 21-trading-day windows close, from 2026-11-03
  (as-of 2026-10-05) to 2026-11-16 (as-of 2026-10-16); the verdict is due by 2026-11-30 (section 3: INCOMPLETE below
  95% resolved by then).
