# ADR-0002: Calibrate the book's probabilities before judging its thresholds

**Status:** Accepted (2026-10-06; implemented 2026-10-06)
**Date:** 2026-10-06
**Deciders:** David Sakhelashvili (owner)

## Context

The recorded book acts on raw probabilities from a gradient-boosted model: enter at p >= 0.55 within the top 8, exit
below 0.48, "confident" at 0.60 (`strategies/ai.py`, `AiConfig`). The calibration study of 2026-10-06
(`civalpha/calibration.py` on evaluation 12, `docs/research/2026-10-forecasting-polish.md` section 8) measured what
those probabilities are worth:

* The model is overconfident, not informative. On the 21-day target its probabilities have a standard deviation of
  0.091 while the logistic models' have 0.033; its reliability bins run (0.26 -> 0.44 observed), (0.36 -> 0.48),
  (0.64 -> 0.47), (0.72 -> 0.53).
* An isotonic map fitted on earlier out-of-sample folds only and applied forward improves its Brier score with a CI
  that excludes zero (Brier skill -0.030 -> -0.007, difference CI -0.0085 to -0.0028) by pulling almost every
  probability into 0.48 to 0.52 (spread 0.091 -> 0.038). AUC does not improve (0.503 -> 0.491). The most confident
  decile's hit rate falls from 53% to 50%: once the spread is honest there is no confident decile.
* For the logistic models calibration changes nothing distinguishable from zero.
* Since ADR-0001 the book trains on the 21-day target, so the map fitted on the walk-forward's `AI_BOOK_21` rows is a
  map for the model the book uses (same inputs, algorithm, horizon; the book's label starts one session later).

The thresholds were therefore chosen on numbers that do not mean what they say: a raw 0.60 is an observed 0.47 to 0.53.
Nothing on the platform shows a calibrated number next to a raw one, so nobody could see this on a page.

## Decision

1. **The strategy lab backtests the book on calibrated probabilities as new rows, next to the raw ones.** `AI_GBM_CAL`
   and `AI_SIZED_CAL` use the same thresholds (0.55 / 0.48), the same cap and sizing, and probabilities mapped fold by
   fold through an isotonic curve fitted on the out-of-sample predictions of earlier folds only (purged: a prior row
   counts only if its label window closed before the fold's test block starts; the first 3 folds have no map and stay
   raw). The abstention curve is reported for both.
2. **Thresholds do not change in this ADR.** The calibrated rows show what 0.55 and 0.60 mean once probabilities are
   honest. Changing thresholds, or switching the recorded book to a calibrated row, is a new trial and a new decision.
3. **The daily decision carries a calibrated probability next to the raw one.** `decisions_at` adds
   `model.calibration = {method: "isotonic", fittedOn: "backtest_prediction AI_BOOK_21 of evaluation <id>", n, p}`
   and `probabilityCalibrated`; the action is still taken on the raw probability. The map is fitted on the latest
   evaluation's out-of-sample `AI_BOOK_21` rows (all of them: they all precede the decision date). Without an
   evaluation the fields are null.
4. **The walk-forward reports every model after calibration too.** `model_evaluation.metrics[kind].calibrated` holds
   Brier, Brier skill, AUC, ECE and the confident decile after the same forward isotonic map (`calibration.study_model`),
   with the Brier-difference CI. The verdict adds one sentence for the book's model.
5. **Live forecasts stay raw.** Batch 2 of the live test pre-registered raw `AI_BOOK_21` probabilities; a calibrated live
   kind, if ever wanted, is a new model kind and a new batch.
6. The calibrated lab rows are trials and go into the registry (`AI_GBM_CAL@GBM_AI_39@21d`, `AI_SIZED_CAL@...`).

## Options considered

### Option A: calibrate in the lab and the decision, keep thresholds and the recorded book (chosen)
Pros: the evidence appears next to the current numbers without changing what is traded; one afternoon; every new row
is registered. Cons: two rows more in a lab that already has 28; the decisions page needs a column.

### Option B: calibrate and re-tune the thresholds in one step
Pros: ends with a usable book. Cons: tuning thresholds on the same window the calibration is judged on is the selection
effect ADR-0001 and the registry exist to stop; the honest result of calibration is likely "the thresholds trigger
rarely", which has to be seen first.

### Option C: switch the recorded book to the calibrated rule now
Pros: trades on honest numbers. Cons: with calibrated probabilities in 0.48 to 0.52 the book would sit in cash almost
every day; that is a decision for the owner after seeing the row, not a side effect.

### Option D: calibrate the live AI_BOOK_21 forecasts
Pros: better Brier on the live test. Cons: batch 2 is pre-registered on raw probabilities; changing them mid-batch is
exactly what pre-registration forbids.

## Trade-off analysis

| | A (chosen) | B | C | D |
|---|---|---|---|---|
| Changes the recorded book | no | yes | yes | no |
| New trials | 2 | 2 + every threshold tried | 1 | 0 |
| Touches a pre-registration | no | no | no | yes |
| Shows what the thresholds mean | yes | hidden by re-tuning | yes, by trading it | no |
| Size | M | M | S | S |

## Consequences

Easier: the strategies page and `get_strategies` show raw and calibrated rows side by side; `get_accuracy` shows every
model's calibrated numbers; the decisions page can show both probabilities.

Harder: the lab run grows by two walk-forward passes of the decision rule (seconds, the probabilities are reused);
reviewers must not quote a calibrated Sharpe as the book's; the decision JSON gains a nested `calibration` object that
older decisions do not have.

Revisit: when batch 2 of the live test resolves, compare its raw Brier skill with what the calibrated map would have
given; if the lab's calibrated row sits in cash most of the time, decide thresholds in ADR-0003 with the registry count
in hand.

**Out of scope here:** threshold changes, switching the recorded book, calibrating live forecasts, the decisions page
column (frontend work in progress by another session; backlog).

## Action items

Phase 1, code (2026-10-06):
- [x] `strategies/ai.py`: `calibrated_probabilities(prob, data, folds, cfg)`, forward isotonic on purged prior folds;
      strategies `AI_GBM_CAL`, `AI_SIZED_CAL`; `AI_FEATURE_SETS` in the registry.
- [x] `strategies/service.py`: lab runs the two rows and a calibrated abstention curve; summary sentence; `decide`
      loads the latest evaluation's `AI_BOOK_21` rows and `decisions_at` adds `probabilityCalibrated` and
      `model.calibration`.
- [x] `evaluation.py`: `metrics[kind].calibrated` from `calibration.study_model`; verdict sentence.
- [x] `db.py`: `load_backtest_predictions(engine, kind)`.
- [x] Tests: calibration uses prior folds only in the lab; lab has the two rows; decision carries both probabilities;
      evaluation reports calibrated metrics.
- [x] Docs: README (strategy lab table, decision layer, forecasting), `docs/api.md`, MCP descriptions.

Phase 2, data (2026-10-06):
- [x] Rebuild api/worker; run the lab (run 20, job 168, 5.6 minutes; registry 73 trials) and `evaluate_models`
      (evaluation 16, job 169). *Found while implementing:*

      | Row (21-day label, run 20) | Sharpe | Max DD | Invested | Trades | Excess vs buy & hold |
      |---|---|---|---|---|---|
      | AI_GBM (raw) | 0.79 | -40.1% | 98% | 166 | +2.2% |
      | AI_GBM_CAL | 0.62 | -27.1% | 44% | 68 | -8.7% |
      | AI_SIZED (raw, the book) | 0.72 | -27.9% | 90% | 166 | -5.2% |
      | AI_SIZED_CAL | 0.60 | -20.0% | 40% | 68 | -11.8% |

      With honest probabilities the 0.55 entry clears on 44% of days instead of 98% and trades drop from 166 to 68:
      the thresholds were calibrated to overconfidence, not to information. The calibrated abstention curve says the
      same: the raw top-10% coverage row read +1.73% per position (CI +0.25% to +2.92%); calibrated it is +0.42%
      (CI -1.46% to +1.98%), within noise. The walk-forward's calibrated metrics for AI_BOOK_21 match the research
      study (Brier skill -0.025 -> -0.007, CI of the difference -0.0085 to -0.0028, AUC 0.491, spread 0.091 -> 0.038,
      confident decile 50%). Decisions from the next trading day carry `probabilityCalibrated` fitted on evaluation
      16's 6,028 rows.

Phase 3, later:
- [ ] Decisions page: show the calibrated probability and the calibration note (after the ablation page lands).
- [ ] ADR-0003 thresholds, after batch 2 resolves.
