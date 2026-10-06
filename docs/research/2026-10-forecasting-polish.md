# Forecasting layer polish, October 2026

Session of 2026-10-06, data cutoff 2026-10-05, 352 companies. Purpose: make the evidence about the existing forecaster
honest and reproducible, and remove the selection effects of the week. No new alpha was sought. The recorded book
(AI_SIZED) and the live forecast models were not changed. Every number below comes from a command that was run; the
command and its output are quoted or named.

## 1. Model parity: there are two forecasters, not one

| Component | Feature set | Algorithm | Label | File |
|---|---|---|---|---|
| Strategy lab AI_GBM, AI_SIZED book | GBM_AI_39 (39 inputs, no policy events since 2026-10-06) | gradient boosting | 10 trading days, close(t+1) to close(t+11) | `backend/civalpha/strategies/ai.py` |
| AI decisions page | GBM_AI_39, retrained daily on all history | gradient boosting | 10 days | `backend/civalpha/strategies/service.py` |
| Live BASELINE forecasts | LOGIT_BASELINE_10 | L2 logistic regression | 21 trading days, close(t) to close(t+21) | `backend/civalpha/features.py`, `backend/civalpha/service.py` |
| Live AUGMENTED forecasts | LOGIT_AUGMENTED_13 | L2 logistic regression | 21 days | same |
| Walk-forward BASELINE / AUGMENTED | LOGIT_BASELINE_10 / LOGIT_AUGMENTED_13 | L2 logistic regression | 21 days | `backend/civalpha/evaluation.py` |

Findings:
* The lab text "beats its sector ETF over the next 10 days" is correct: `AiConfig.horizon = 10`. The stated 21-day
  target applies to the logistic forecasts only. Until this session the recorded book traded a label the walk-forward
  never measured.
* The feature change of 2026-10-06 (three policy-event inputs dropped) touched only the gradient-boosted model, and was
  made on lab Sharpe (0.74 to 0.91), which ground rule 1 forbids. Live AUGMENTED still carries those inputs.
* The 702 BASELINE live forecasts are two model versions: the 2026-10-02 series were first issued on a 7-input model
  and superseded on 2026-10-05 by the 10-input one; the 2026-10-05 series were issued once.
* Decision: the walk-forward now scores the book's model on both labels (section 4).

## 2. Pre-registered live test

`docs/research/live-test-2026-10.md`, committed as dbf5ee3 at 10:13 UTC with 0 of 1,404 outcomes resolved. Scoring code
`backend/civalpha/livetest.py`, 6 unit tests. Run against the live database:

```
"fingerprintMatches": true
BASELINE:  issued 702, resolved 0, verdict PENDING
AUGMENTED: issued 702, resolved 0, verdict PENDING
```

Fixed: the population (highest version of each series issued before 2026-10-06 11:00 UTC, md5-fingerprinted), Brier
skill versus the realized base rate as the primary metric, AUC and the confident decile as secondary, a company-cluster
bootstrap (the two as-of dates are one session apart, so a date bootstrap is degenerate), the verdict rule (SKILL /
NO SKILL / HARMFUL / INCOMPLETE), the exact SQL, and the expectation: NO SKILL for both. Two facts recorded in advance:
the forecasts were published after their windows opened, so the trading metric is hypothetical; and AUGMENTED and
BASELINE probabilities differ by at most 0.0064 across the 702 pairs, so the paired comparison cannot inform.

## 3. Data integrity

**CTVA.** Cause: Corteva distributed one Vylor (VYLR) share per CTVA share before the open on 2026-10-01 (8-K of
2026-09-15 and 2026-10-01). Tiingo carries no adjustment (split factor 1.0, no dividend). VYLR closed at 68.26 on its
first day. Fix: a `SPIN_OFF` corporate action worth 68.26 per share on 2026-10-01, treated by the total-return index
like a cash dividend (commit 1992304). The dividend features ignore it.

```
date        close   TR ret before  TR ret after   vol21 before  vol21 after
2026-09-30    77.65  -0.0028        -0.0028        0.255         0.255
2026-10-01    12.57  -0.8381        +0.0410        6.290         0.259
2026-10-02    11.92  -0.0517        -0.0517        6.277         0.289
2026-10-05    12.39  +0.0394        +0.0394        6.289         0.329
```

The decisions and forecasts stored for CTVA on 2026-10-02 and 2026-10-05 were computed on the broken series and are
append-only; they stand, and the live test already pins them.

**Universe scan** (`python -m civalpha.integrity`, on the adjusted index, after the fix): 26 companies with 21-day
volatility above 200% at some point (14 of them in March 2020; PRPO, PCG, BDSX, CVNA, CRCL's IPO week; AGIO 2025-11,
FISV 2025-10, CV 2026-01); 13 one-day total returns beyond 50%, 12 of them with no recorded action that day (PCG 2019,
TRGP 2020-03-09, PRPO, HOOD, CAR, CVNA, CRCL, AGIO 2025-11-19). They match known market events; AGIO was not checked
against a filing. None were changed. CTVA no longer appears.

**Null values with contributions.** The logistic models fill a missing input with the training median; the factor now
says `imputed: true, imputation: "training median"`. The gradient-boosted model fills nothing: the trees route missing
values on a learned branch, so the contribution is the effect of having no value; a column with no training values is
set to 0 and ignored. Both explanation outputs now carry the flag and the note, and both pages show it. The 351
decisions stored for 2026-10-05 contain 197 factors with a null value and a non-zero contribution; they are append-only
and keep their shape. Tests: `backend/tests/test_explanations.py` (3), `test_integrity.py` (2), `test_returns.py` (+1).

## 4. Walk-forward on the production feature set

`evaluate_models` now runs four models on the same folds and names each by its feature set (commit 7bf4722). Job 156
(3 minutes, evaluation 12) produced the first four-model run; its fold-by-fold sign test came out empty because the
per-fold dictionary now has four entries, fixed in a3a29bb and re-run as job 160 (evaluation 13, 3.4 minutes). Verdict
block of evaluation 13 as stored:

```
Walk-forward, 6028 out-of-sample predictions per model. Brier skill vs base rate: baseline (LOGIT_BASELINE_10) -0.006,
augmented (LOGIT_AUGMENTED_13) -0.015; AUC baseline 0.506, augmented 0.501. AI_BOOK_21 (GBM_AI_39, the recorded book's
inputs, 21-day label entered at close(t)): Brier skill -0.025, AUC 0.513 on 6028 predictions. AI_BOOK_10 (GBM_AI_39, the
recorded book's inputs, 10-day label entered at next close): Brier skill -0.021, AUC 0.504 on 6061 predictions. The
augmented model is WORSE than the baseline (95% CI excludes zero). Long/short simulation after 10 bp per side per leg:
Augmented better in 7 of 21 folds (sign test p = 0.19). Long/short simulation after 10 bp per side per leg: mean net
-1.60% per 21-day period over 62 periods (t = -1.54). Profitability claim: NOT supported by this evidence.
```

| Model | Feature set | Label | n | Brier skill | AUC | Top-10% net excess (CI) |
|---|---|---|---|---|---|---|
| BASELINE | LOGIT_BASELINE_10 | 21d | 6,028 | -0.006 | 0.506 | -2.63% (-4.71% to -1.56%) |
| AUGMENTED | LOGIT_AUGMENTED_13 | 21d | 6,028 | -0.015 | 0.501 | -2.13% (-4.51% to -0.36%) |
| AI_BOOK_21 | GBM_AI_39 | 21d | 6,028 | -0.025 | 0.513 | +0.69% (-1.04% to +1.87%) |
| AI_BOOK_10 | GBM_AI_39 | 10d, next close | 6,061 | -0.021 | 0.504 | -0.36% (-1.18% to +0.48%) |

The book's model ranks slightly better than the logistic models on the 21-day target (AUC 0.513) and worse on Brier
skill: it spreads its probabilities further than its information warrants (section 8). On the label it actually trades
it is at AUC 0.504. The accuracy page shows the feature-set identifier under each column and a table for the book's
model; `get_accuracy` returns `config.models` with the exact input list of every model.

## 5. Trial registry

Table `trial_registry` (migration V14, `backend/civalpha/strategies/registry.py`, commit 8434426): one row per
(strategy rule, input list) ever backtested, with first and last test date, runs, Sharpe, excess-return CI, max drawdown,
git commit and source. Every lab run registers its candidates; the Deflated Sharpe Ratio deflates for the registry count.

Backfill of runs 1-17 (`python -m civalpha.strategies.registry backfill`): 317 result rows collapsed into 36 trials. The
AI rows never recorded their input list, so their feature set was inferred from the AI_DIV row of the same run, which did
(AI_DIV's list is AI_GBM's plus the three dividend features): 35 inputs on 2026-10-04 and the morning of 2026-10-05, 42
from the evening of 2026-10-05, 39 from 2026-10-06 09:19 UTC. Rows with the same inputs under another name count once
(AI_NO_EVENTS of run 15 is AI_GBM@GBM_AI_39; AI_WITH_EVENTS is AI_GBM@GBM_AI_42). Rows are marked `backfill-inferred`
with the reason; the git commit of backfilled rows is the latest commit before the run and is marked as inferred too.
Trials never stored (thresholds or margins tried and discarded without a lab run) cannot be counted: the registry is a
floor.

Lab run 18 (job 157, 5.7 minutes, commit 659f60e recorded on every row) deflates for 37 trials, 25 of them in the run.
The new summary sentence as stored:

> No strategy beat buy & hold once the test accounts for the 37 trials in the trial registry (every strategy and
> feature-set variant backtested on this history, 25 of them in this run); differences are consistent with luck.

| Strategy | DSR, run 17 (24 trials) | DSR, run 18 (37 trials) |
|---|---|---|
| MOM_12_1 | 0.545 | 0.476 |
| AI_DIV | 0.164 | 0.126 |
| AI_GBM | 0.109 | 0.080 |
| AI_SIZED | 0.015 | 0.010 |

No verdict changed: nothing was supported before and nothing is now.

## 6. Feature fragility

`run_feature_ablation` (commit e7fa17c; job 158, 18.5 minutes; `GET /api/ablation`). The production list GBM_AI_39 minus
each of four groups, plus dividends, plus policy events; each scored walk-forward on both labels with CIs from a bootstrap
over 21-day blocks of as-of dates, and the lab Sharpe of the same probabilities under the standard rule and the sized book.

| Variant | Inputs | 21d Brier skill (CI) | 21d AUC (CI) | 10d Brier skill (CI) | 10d AUC (CI) | AI_GBM Sharpe* | AI_SIZED Sharpe* / max DD |
|---|---|---|---|---|---|---|---|
| FULL (production) | 39 | -0.025 (-0.043, -0.014) | 0.513 (0.481, 0.533) | -0.021 (-0.033, -0.012) | 0.504 (0.485, 0.519) | 0.91 | 0.98 / -26.6% |
| no price/technical | 25 | -0.019 (-0.037, -0.008) | 0.523 (0.495, 0.544) | -0.019 (-0.028, -0.012) | 0.506 (0.492, 0.518) | 0.92 | 0.91 / -30.2% |
| no report profile | 21 | -0.022 (-0.036, -0.013) | 0.505 (0.473, 0.526) | -0.017 (-0.023, -0.012) | 0.501 (0.485, 0.510) | 0.59 | 0.49 / -35.8% |
| no insider | 36 | -0.024 (-0.044, -0.014) | 0.514 (0.479, 0.534) | -0.020 (-0.033, -0.012) | 0.506 (0.486, 0.522) | 0.67 | 0.72 / -30.6% |
| no earnings | 35 | -0.024 (-0.042, -0.015) | 0.515 (0.485, 0.532) | -0.022 (-0.033, -0.013) | 0.501 (0.482, 0.518) | 0.61 | 0.66 / -29.4% |
| plus dividends | 42 | -0.022 (-0.044, -0.010) | 0.518 (0.481, 0.541) | -0.022 (-0.033, -0.012) | 0.504 (0.486, 0.523) | 0.96 | 0.91 / -27.5% |
| plus policy events | 42 | -0.027 (-0.047, -0.017) | 0.512 (0.477, 0.532) | -0.020 (-0.032, -0.012) | 0.506 (0.485, 0.521) | 0.74 | 0.79 / -29.9% |

*not a skill metric: one trading rule on one five-year window.

Headline as stored: "10-day label: the production set (GBM_AI_39) has Brier skill -0.021 (95% CI -0.033 to -0.012) and AUC
0.504 (CI 0.485 to 0.519); across the 7 variants Brier skill spans -0.022 to -0.017 and AUC 0.501 to 0.506. 21-day label:
... Brier skill spans -0.027 to -0.019 and AUC 0.505 to 0.523. Lab Sharpe of the sized book: 0.98 on the production set,
0.49 to 0.98 across the variants. No variant has Brier skill above zero with a CI that excludes zero."

Reading: forecast quality does not move. Every skill CI overlaps every other, no variant is above zero, and removing the
whole report profile (18 of 39 inputs) changes AUC on the book's own label from 0.504 to 0.501. The lab Sharpe of the
same probabilities moves from 0.49 to 0.98. A number that halves when the forecast quality does not change is measuring
which handful of stocks a rule happened to hold, not information. This is the quantitative form of ground rule 1. The 14
variant rows (7 variants x 2 rules) went into the trial registry, which now counts 49 trials; the next lab run deflates
for that.

## 7. Sizing without forecasting

Two rows added to the lab (commit 659f60e): EW_SIZED, the whole universe under the book's sizing (0.04 / 21-day
volatility, 20% cap, no leverage; with 352 names the budget always exceeds 100%, so this is inverse-volatility weighting,
fully invested, family BENCHMARK), and MOM_12_1_SIZED, the 12-1 momentum top 5 under the same sizing. Run 18:

| Row | Sharpe | Max DD | CAGR | Invested | Excess vs buy & hold (95% CI) | DSR |
|---|---|---|---|---|---|---|
| EW_BUY_HOLD | 0.79 | -38.9% | +22.1% | 100% | reference | |
| EW_SIZED | 0.71 | -34.2% | +17.7% | 100% | -4.5% (-9.2% to -0.4%) | reference |
| AI_SIZED (the book) | 0.98 | -26.6% | +23.4% | 83% | -0.0% (-11.8% to +10.0%) | 0.010 |
| MOM_12_1 | 1.12 | -41.6% | +51.0% | 100% | +26.6% (+7.0% to +45.9%) | 0.476 |
| MOM_12_1_SIZED | 1.29 | -16.4% | +26.2% | 50% | +1.6% (-10.9% to +13.7%) | 0.018 |

Reading: AI_SIZED's 0.98 is not reproduced by sizing the whole universe (0.71), so the pre-registered "nil" sentence does
not apply as written. But the same sizing on a 12-1 momentum screen gives 1.29 with a drawdown of -16.4%, better than the
book on both counts. On this window the sizing is the part that travels; the AI's selection is not shown to beat a
classic screen under it. The summary sentence stored with run 18 says the +0.27 gap to EW_SIZED "is what the AI's
forecast adds"; commit dbb9237 changes that wording to "the most the AI's selection can be credited with" and adds the
momentum comparison. The stored sentence of run 18 keeps the old wording; the next lab run carries the new one.

## 8. Calibration

`python -m civalpha.calibration 12` (commit 9d6c8b6). Isotonic map fitted on folds 0..k-1, applied to fold k; folds 0-2
left out on both sides; excess is the label window minus 40 bp.

| Model | n | Brier before -> after | Skill before -> after | Brier diff 95% CI | ECE | Spread of p | Top-10% hit | Top-10% net excess | AUC |
|---|---|---|---|---|---|---|---|---|---|
| BASELINE | 5,164 | 0.2510 -> 0.2512 | -0.004 -> -0.005 | -0.0006 to +0.0007 | 0.016 -> 0.026 | 0.033 -> 0.026 | 50% -> 49% | -2.41% -> -2.60% | 0.507 -> 0.495 |
| AUGMENTED | 5,164 | 0.2513 -> 0.2504 | -0.005 -> -0.002 | -0.0017 to +0.0001 | 0.018 -> 0.018 | 0.039 -> 0.013 | 50% -> 48% | -2.41% -> -2.48% | 0.507 -> 0.490 |
| AI_BOOK_21 | 5,164 | 0.2575 -> 0.2518 | -0.030 -> -0.007 | -0.0085 to -0.0028 | 0.074 -> 0.033 | 0.091 -> 0.038 | 53% -> 50% | +0.12% -> -0.55% | 0.503 -> 0.491 |
| AI_BOOK_10 | 5,197 | 0.2554 -> 0.2508 | -0.022 -> -0.004 | -0.0072 to -0.0026 | 0.061 -> 0.015 | 0.078 -> 0.031 | 53% -> 48% | -0.54% -> -1.08% | 0.500 -> 0.497 |

Reliability of the book's model before calibration, 21-day label (mean predicted, observed rate, count):
(0.26, 0.44, 144), (0.36, 0.48, 688), (0.46, 0.51, 1980), (0.54, 0.50, 1854), (0.64, 0.47, 456), (0.72, 0.53, 34).
After: (0.36, 0.55, 113), (0.48, 0.50, 3738), (0.52, 0.47, 1297).

Reading: the gradient-boosted model is overconfident, not informative. Calibration improves its Brier score with a CI
that excludes zero, by pulling almost every probability back to 0.48 to 0.52. Nothing is gained in ranking (AUC falls
slightly) and the confident decile's hit rate drops to a coin flip, because once the spread is honest there is no
confident decile. For the logistic models calibration changes nothing distinguishable from zero. Nothing was applied to
the live models.

## What moved

| Claim or number | Before this session | After | Why |
|---|---|---|---|
| "The target is 21 trading days" | stated for everything | true for the live logistic forecasts; the recorded book trades a 10-day next-close label | code audit, section 1 |
| Lab AI_GBM / live BASELINE | treated as one model | two models: GBM_AI_39 vs LOGIT_BASELINE_10 | section 1 |
| Walk-forward models | 2 | 4, each named by feature set | section 4 |
| CTVA vol21 | 6.29 | 0.26 | SPIN_OFF action, section 3 |
| Strategies the DSR deflates for | 24 | 37 in run 18; 49 after the ablation study | trial registry, sections 5 and 6 |
| MOM_12_1 deflated Sharpe | 0.545 | 0.476 | 37 trials |
| AI_SIZED deflated Sharpe | 0.015 | 0.010 | 37 trials |
| AI_SIZED Sharpe 0.98 vs buy & hold 0.79 | credited to the AI's decision layer | sizing on a 12-1 momentum screen gives 1.29 / -16.4%; the whole universe under the same sizing 0.71 | section 7 |
| Book model's probabilities | shown as is | overconfident: isotonic on past folds cuts their spread from 0.09 to 0.04 and the confident decile to a coin flip | section 8 |
| Factors with a null value and a contribution | shown as such | marked `imputed` with how the value entered the model | section 3 |
| Live test | three point thresholds in code | a dated document with a fingerprinted population, metrics, CI method, verdict rule and expectation | section 2 |

Verdicts that did not move: nothing beat buy & hold before and nothing does now; the augmented model is still worse than
the baseline; profitability is still not supported; no live forecast has resolved.

## Open

* **Live test.** Score batch 1 after 2026-11-03 and no later than 2026-11-14 with `python -m civalpha.livetest`; expected
  NO SKILL for both models. The code's point check on the accuracy page is a display, the document is the rule.
* **The book's horizon.** Decided the same evening: ADR-0001 moves the book to the 21-day target. Lab run 19 at 21 days
  gives AI_SIZED Sharpe 0.72 (was 0.98 at 10 days) and EW_SIZED 0.71; the event-feature comparison reverses (0.89 with
  against 0.79 without). The numbers of sections 5 to 7 above are the 10-day ones and stay as the record of that day.
* **Sizing guard.** An unexplained raw-close move beyond 50% (CTVA on 2026-10-01) still enters the sizing as volatility.
  `python -m civalpha.integrity` detects it; nothing acts on it. Acting would change the book.
* **Ablation page.** Done 2026-10-06, after this report: the Feature fragility page (`/ablation`) draws the study as
  dot-and-whisker charts, and the Accuracy page draws the four walk-forward models the same way (per-model CIs are
  stored since evaluation 14).
* **Lab summary wording.** Run 18's stored sentence credits the +0.27 Sharpe gap to the AI; the code now says it is an
  upper bound and names the momentum comparison. The next lab run carries it.
* **Backfilled registry rows** are inferences (feature set from the AI_DIV row, git commit from commit time) and say so.
* **Unrecorded trials.** Thresholds, margins or feature lists tried without a stored lab run are not in the registry.
  From now on every lab run and ablation registers itself; the historical count is a floor.
* **Universe history.** 312 of 352 companies have membership since 2026-10-05, so every history-based number above rests
  on the original ~40 names. Backdating membership is survivorship-biased and is the owner's call.
* **Decision page feature identifier.** Stored decisions say `features: "no policy-event features"`; new decisions should
  carry `GBM_AI_39`. Not changed, because the decision payload is the recorded book's.

## Commands run

| Task | Command | Result |
|---|---|---|
| 1 | import check of HORIZON, AiConfig, FEATURES; SQL on model_version, forecast, strategy_decision | table in section 1 |
| 2 | `python -m civalpha.livetest` in the api container; `pytest tests/test_livetest.py` | fingerprints match, PENDING; 6 passed |
| 3 | SEC EDGAR 8-K 0001193125-26-414490 and -391369; Tiingo CTVA/VYLR bars; `INSERT INTO corporate_action ... SPIN_OFF 68.26`; `python -m civalpha.integrity` | section 3; `pytest` 237 passed |
| 4 | MCP `evaluate_models` jobs 156 and 160 | evaluations 12 and 13 |
| 5 | `python -m civalpha.platform.migrate`; `python -m civalpha.strategies.registry backfill commits.txt`; MCP `run_strategy_backtest` job 157 | 36 trials, then 37; run 18 |
| 6 | `POST /api/admin/ablation/study` job 158; `GET /api/ablation` | section 6; 49 trials |
| 7 | part of run 18 | section 7 |
| 8 | `python -m civalpha.calibration 12` | section 8 |
| all | `backend/.venv/bin/pytest` | 250 passed at dbb9237 |

Commits, one per task: dbf5ee3 (2), 1992304 (3), 7bf4722 + a3a29bb (4), 8434426 (5), e7fa17c (6), 659f60e + dbb9237 (7),
9d6c8b6 (8). The CTVA corporate action and the registry backfill are data changes in the database, not in git.
