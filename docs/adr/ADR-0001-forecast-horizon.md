# ADR-0001: One forecast horizon for the live models, the recorded book and the lab

**Status:** Accepted (2026-10-06; implemented 2026-10-06)
**Date:** 2026-10-06
**Deciders:** David Sakhelashvili (owner)

## Context

The platform states one target: the probability that a stock's total return over the next 21 trading days beats its
sector ETF, from the close of the as-of date (`returns.HORIZON = 21`, README "Target"). Until today that was true only
for the two live logistic models and the walk-forward. The strategy lab's AI rows and the recorded book (AI_SIZED, the
AI decisions page) trained a gradient-boosted model on a different label: the excess return from the **next** close
over the following **10** trading days (`strategies/ai.py`, `AiConfig.horizon = 10`, label `px.shift(-(h+1)) /
px.shift(-1)`). The decisions stored for 2026-10-05 carry `model.horizon = 10`.

Evidence gathered on 2026-10-06 (`docs/research/2026-10-forecasting-polish.md`):

* Walk-forward of the book's model (GBM_AI_39) on the same folds, evaluation 13: on the 21-day target Brier skill
  -0.025 (95% CI -0.043 to -0.014), AUC 0.513 (0.481 to 0.533); on its own 10-day next-close label Brier skill
  -0.021 (-0.033 to -0.012), AUC 0.504 (0.485 to 0.519). The two labels are not distinguishable in skill.
* The feature ablation study (run 1): AUC 0.505 to 0.523 across seven input lists on 21 days, 0.501 to 0.506 on 10
  days; every CI overlaps every other.
* The live test batch 1 (`docs/research/live-test-2026-10.md`) and batch 2 (`live-test-2026-10-batch2.md`,
  AI_BOOK_21) are both on the 21-day target. There is no live record of the 10-day label and no outcome resolver for
  a next-close window (`service.resolve_outcomes` measures close(t) to close(t+h)).
* Running one horizon live and another in the lab made every comparison between them suspect: a lab Sharpe of a
  10-day book could not be set against a 21-day forecast's Brier skill, and the pre-registration had to say so.

Defects found on the way: the strategies page said "next 10 days" while every other page said 21; `decisions.model.book
.features` stored a sentence ("no policy-event features") instead of the feature-set identifier.

## Decision

1. **The recorded book's model trains on the platform's 21-day target.** `AiConfig.horizon` defaults to 21, the label
   stays "entered at the next close" (t+1 to t+22), so the lab, the decisions page, the walk-forward row `AI_BOOK_21`
   and the live forecasts `AI_BOOK_21` all describe one model on one target.
2. Entry and exit thresholds (0.55 / 0.48), the top-8 cap, the confident bar (0.60) and the volatility sizing (0.04,
   20%, no leverage) are unchanged. Changing them is a new trial and belongs to the calibration work (ADR-0002, to be
   written).
3. A trial's key names its label: `AI_GBM@GBM_AI_39@21d`. The 10-day trials stay in the registry as `...@10d` (migration
   V16 renames them), so the Deflated Sharpe Ratio keeps counting them.
4. The walk-forward scores the book's model once (`AI_BOOK_21`); the ablation study scores each variant once. The
   `AI_BOOK_10` row is dropped from new runs; evaluations 12 and 13 keep it as history.
5. Stored decisions carry `model.horizon = 21` and `model.book.featureSet = "GBM_AI_39"` from the first decision after
   this change. Decisions dated 2026-10-05 and earlier keep `horizon = 10` (append-only table). The book's holdings
   carry over: the first 21-day decision seeds from the last 10-day book's positions, as any model refit does.
6. The 10-day label is not issued live and gets no live-test batch. If it is ever wanted again it is a new trial and
   a new batch.

## Options considered

### Option A: retrain the book on the 21-day target (chosen)
Pros: one target, one live test, one walk-forward, fewer rebalances; the walk-forward shows nothing lost (AUC 0.513
vs 0.504, within noise). Cons: the recorded book changes and its 10-day decision record stops; the lab's AI rows become
new trials; one afternoon of work.

### Option B: change the platform's target to 10 days
Pros: live outcomes resolve in two weeks instead of four, which would speed up the live test. Cons: the live logistic
models, the outcome resolver, batch 2 and every page that says 21 days change; batch 1 stays at 21 days so two targets
coexist for a month anyway; more trades per year for every rule; a week of work.

### Option C: keep both as first-class targets
Pros: honest about the two uses (forecast page vs trading book). Cons: every table, verdict and pre-registration
doubles; the forecast target statement needs a second sentence everywhere; nothing in the evidence says the 10-day
label is better.

### Option D: do nothing
Pros: no change to the recorded book. Cons: the lab keeps backtesting a horizon the live test never scores; batch 2
measures a model the book does not trade.

## Trade-off analysis

| | A: book to 21 days | B: target to 10 days | C: both | D: nothing |
|---|---|---|---|---|
| Comparable lab vs live numbers | yes | yes | only within each horizon | no |
| Live test resolves in | 4 weeks | 2 weeks | both | 4 weeks |
| Code touched | ai.py default, registry key, docs | models, resolver, pages, batch 2 | everything, twice | none |
| Recorded book changes | yes (model refit) | yes (model refit) | yes | no |
| Trials added to the registry | the AI rows at 21d | the AI rows at 10d already exist | both | none |
| Size | M | L | L | — |

## Consequences

Easier: one number per model per metric; batch 2 scores the model the book trades; the summary sentence on the
strategies page and the decisions page say the same horizon.

Harder: the lab's AI Sharpes are new trials and start from scratch on the registry (the 10-day ones stay as history);
reviewers must check that a quoted AI number names its horizon until the 10-day rows age out of conversation; the
decisions record has a visible seam on 2026-10-06 (`model.horizon` 10 before, 21 after).

Revisit: if batch 2 (AI_BOOK_21) resolves SKILL while the lab's 21-day book shows no edge, or if the live test ever
needs faster resolution, reopen B.

**Out of scope here:** calibration and thresholds (ADR-0002), a sizing guard for unexplained price moves, backdating
universe membership, the ablation page.

## Action items

Phase 1, code (2026-10-06):
- [x] `strategies/ai.py`: `AiConfig.horizon = 21`; docstring; `book_model_specs` returns one spec when the book's
      horizon equals the target.
- [x] `strategies/ablation.py`: one spec per variant at the book's horizon.
- [x] `strategies/registry.py`: trial key `rule@featureSet@<h>d` for AI rows; `feature_set_of` reads `params.horizon`.
- [x] `db/migration/V16__trial_horizon.sql`: rename existing AI trial keys to `...@10d`.
- [x] `strategies/service.py`: `decisions_at` stores `book.featureSet` and the horizon.
- [x] Tests: `test_evaluation` (book spec at 21 days), `test_ablation`, `test_registry`, `test_strategies`.
- [x] Docs: README strategy lab text (21 days), `docs/api.md`, MCP tool descriptions, module docstrings.

Phase 2, data (2026-10-06):
- [x] Rebuild api/worker, run the strategy lab (new 21-day AI trials registered), run `evaluate_models`.
      *Found while implementing (lab run 19, job 165; evaluation 15, job 166; registry 59 trials, 10 new at 21d):*
      the AI rows move a lot between labels while the walk-forward does not. AI_SIZED Sharpe 0.98 (10d) -> 0.72 (21d),
      max drawdown -26.6% -> -27.9%; AI_GBM 0.91 -> 0.79; AI_GBM_TSTOP10 0.91 -> 0.22; AI_CONF 0.47 -> 0.70. With the
      policy-event features added back the standard rule is now 0.89 against 0.79 without them, the reverse of the
      10-day result that dropped them on 2026-10-06 morning: that decision was lab noise, as ground rule 1 says.
      EW_SIZED 0.71 against AI_SIZED 0.72: the sizing explains the book. Walk-forward AI_BOOK_21 unchanged (Brier
      skill -0.025, AUC 0.513). The AI's own top-10% coverage at 21 days: 55% right, +1.73% per position after costs
      (CI +0.25% to +2.92%), the first coverage row with a CI above zero; one window, 4,204 calls, not pre-registered.
- [ ] Run the feature ablation at 21 days (18 minutes) and compare with run 1. *Queued as job 167 on 2026-10-06.*
- [ ] Frontend guide text that still says 10 days (`guide-content.ts`): after the ablation page in progress lands.

Phase 3, later:
- [ ] ADR-0002 calibration: isotonic maps on past folds in the lab and in the daily decision; what 0.55 / 0.60 then mean.
