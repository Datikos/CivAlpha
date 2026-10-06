# Pre-registered live test of the forecast models, batch 1 (October 2026)

Registered on 2026-10-06 at 10:10 UTC, before any live forecast had resolved (0 of 1,404 outcomes known at the time).
The first outcome window closes on 2026-11-02. This document fixes what will be measured, how, and what the result will
be called, so the verdict cannot be chosen after the data are in. Scoring code: `backend/civalpha/livetest.py`
(`python -m civalpha.livetest`); its constants mirror this document and must not be changed without a dated amendment
section here.

## 1. What is being tested

The two live forecast models of the platform, as they were when the forecasts were issued:

| Model | Feature-set identifier | Inputs | Algorithm | Target |
|---|---|---|---|---|
| BASELINE | LOGIT_BASELINE_10 | 10 features: `mom_21, mom_63, mom_126_21, vol_63, rev_yoy, gm_chg, leverage, insider_net_63d, earn_react_last, guidance_last` | L2 logistic regression (`civalpha/model.py`) | P(stock total return over 21 trading days beats its sector ETF), window close(t) to close(t+21) |
| AUGMENTED | LOGIT_AUGMENTED_13 | the 10 above plus `trade_shock, rate_shock, fedfunds_chg_x_lev` | same | same |

These are **not** the model behind the AI decisions page / strategy lab (gradient boosting, 39 features, 10-day label).
That model issues no live forecasts and is not part of this test.

## 2. The population: exactly which forecasts

All `LIVE` forecasts for as-of dates **2026-10-02** and **2026-10-05**, one per (company, model, as-of date) series, taking
for each series the **highest version issued before 2026-10-06 11:00 UTC**. Versions issued at or after that instant
(any later re-issue) are not part of the test. That gives 351 companies x 2 dates = **702 forecasts per model, 1,404 in
total**, pinned by this fingerprint (md5 of `series_key:version` pairs joined by commas in `series_key` order):

| Model | Forecasts | Model versions used | Fingerprint |
|---|---|---|---|
| BASELINE | 702 | `model_version` 11 (as-of 10-02) and 13 (as-of 10-05), both 10 features | `73af3afc82c5eeb529e0739e9373ac90` |
| AUGMENTED | 702 | `model_version` 12 and 14, both 13 features | `0010f38379eec1e1489b32b9b4cf34f3` |

Two facts about this population, recorded now so they cannot be used as excuses later:

* **The forecasts were published after their window had started.** The 2026-10-02 series were first issued on
  2026-10-04 (a Saturday) and re-issued on 2026-10-05 at 23:32 UTC after a feature-set change (7 to 10 features); the
  version scored is that last one. The 2026-10-05 series were issued on 2026-10-06 at 03:57 UTC. Nobody could have
  traded at the close that starts the outcome window. The forecast-quality metrics (Brier, AUC) are unaffected; the
  trading metric in section 3 is therefore a hypothetical, labelled as such.
* **The two models are almost the same forecast in this batch.** Across the 702 pairs, the AUGMENTED and BASELINE
  probabilities differ by at most 0.0064 (mean 0.00025). Whatever verdict one model receives, the other will almost
  certainly receive too; the paired comparison in section 3 is declared in advance to be uninformative for this batch.

## 3. Metrics

All metrics are computed per model on the **resolved** forecasts of the population (an unresolved forecast is one with
no `forecast_outcome` row; see section 5 for what happens if some never resolve). `p` is the stored probability,
`y` is 1 if the stock beat its sector ETF over the 21-day window and 0 otherwise, `x` is the stock's excess total
return over the ETF for that window, all from `forecast_outcome`.

**Primary metric: Brier skill versus the base rate.**
`Brier = mean((p - y)^2)`; `r = mean(y)` over the same resolved forecasts; `reference = r (1 - r)`;
`Brier skill = 1 - Brier / reference`. Positive means the probabilities beat the best constant forecast. Note that
the reference uses the realized base rate, which is the hardest constant to beat, so a model that spreads its
probabilities without information will score **below** zero, not at zero. That is intended.

**Secondary metrics.**
1. AUC of `p` against `y`.
2. Confident decile: the 10% of resolved forecasts (70 of 702) with the largest `|p - 0.5|`, ties broken by stable
   order. Reported: the `|p - 0.5|` needed to get in, the hit rate (`(p > 0.5) == y`), and the mean net excess
   return `sign(p - 0.5) * x - 0.0040`, where 0.0040 is 4 legs (stock and ETF hedge, in and out) at 10 bp per side,
   matching the walk-forward coverage curve. This is a hypothetical, as explained in section 2.
3. AUGMENTED minus BASELINE Brier on the same resolved (company, date) rows, with its CI.

The code's existing point check on the accuracy page (`evaluation.LIVE_TEST`: at least 500 resolved, AUC >= 0.53,
Brier below the base-rate Brier) stays as displayed, but **this document's verdict rule is the authoritative one**.
Both will be reported.

## 4. Confidence intervals

The protocol asked for a bootstrap over as-of dates. This batch has **two** as-of dates one session apart, whose
outcome windows share 20 of 21 sessions, so resampling dates is degenerate and would understate uncertainty to the
point of meaninglessness. The pre-registered method is therefore a **cluster bootstrap by company**: each company's
two forecasts form one cluster (351 clusters per model), clusters are drawn with replacement 2,000 times with seed
20261006, and the 95% CI of each metric is its 2.5th and 97.5th percentile over the resamples. The same clusters and
seed are used for the paired difference.

Known limitation, stated now: company clustering does not capture dependence **across** companies on the same date.
Excess returns are sector-relative, which removes the market factor but not all co-movement, so the CIs are probably
too narrow. The verdict thresholds are not adjusted for this; the limitation is simply disclosed. Expected precision:
with about 351 effective observations and probabilities close to 0.5, the Brier-skill CI will be roughly +-0.02
wide. The walk-forward's BASELINE skill of -0.006 is **not** detectable in this batch; a skill of +0.02 or worse than
-0.02 is.

Once later batches exist with at least 10 distinct as-of dates, the unit of resampling becomes the as-of date in blocks
of 21 sessions, as in the walk-forward. That change applies to later batches only; batch 1 keeps the rule above.

## 5. Verdict rule, per model

| Verdict | Condition |
|---|---|
| INCOMPLETE | fewer than 95% of the 702 forecasts (fewer than 667) have resolved by 2026-11-14 |
| SKILL | the 95% CI of Brier skill lies entirely above 0 **and** the point AUC is above 0.5 |
| HARMFUL | the 95% CI of Brier skill lies entirely below 0 |
| NO SKILL | anything else |

Forecasts that cannot resolve (delisting, missing prices) are reported by count and symbol; if 95% resolve the
verdict stands on those that did. The verdict is computed once, after the last window closes (2026-11-03) and
outcomes have been resolved, and no later than 2026-11-14.

**Pre-registered expectation.** From the walk-forward (BASELINE Brier skill -0.006, AUC 0.506; AUGMENTED -0.015,
AUC 0.501) the expected outcome is NO SKILL for both, with HARMFUL possible. A SKILL verdict here would be surprising
and would need a second, independent batch before anything changes in the recorded book or the live models.

## 6. The exact query

```sql
WITH scored AS (
  SELECT DISTINCT ON (series_key) id, series_key, company_id, symbol, model_kind, as_of_date, probability::float8 AS p,
         model_version_id, version, issued_at
  FROM forecast
  WHERE issue_mode = 'LIVE' AND model_kind IN ('BASELINE', 'AUGMENTED')
    AND as_of_date IN ('2026-10-02', '2026-10-05')
    AND issued_at < TIMESTAMPTZ '2026-10-06 11:00:00+00'
  ORDER BY series_key, version DESC)
SELECT s.*, o.outcome, o.excess_return::float8 AS excess_return, o.window_end_date
FROM scored s LEFT JOIN forecast_outcome o ON o.forecast_id = s.id
ORDER BY s.model_kind, s.as_of_date, s.company_id;
```

The metrics in section 3, the bootstrap in section 4 and the verdict in section 5 are implemented, line for line, in
`backend/civalpha/livetest.py` (`score_model`, `paired_difference`, `verdict`), unit-tested in
`backend/tests/test_livetest.py`. Run on 2026-10-06 against the live database it printed `fingerprintMatches: true`,
702 issued and 0 resolved per model, verdict PENDING for both.

## 7. What this test does not cover

* The AI decisions book (AI_SIZED): a different model, feature set and horizon. Its live record is the append-only
  `strategy_decision` table and needs its own pre-registration once enough decisions have resolved.
* Whether the forecasts can be traded profitably: the trading metric here is hypothetical (section 2).
* SPCX (company 30), the one universe member without a forecast on either date (79 price bars, below the 127-session
  minimum history), and any company added after 2026-10-05.
