# Backlog

State on 2026-10-06 (evening): 6 open items across 1 ADR and the October 2026 research report. Rows leave when their item is
ticked in its ADR; a new item goes into an ADR first, then here. Sizes: S up to a day, M a few days, L a week or more.

## P1, before the live test resolves (2026-11-03)

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 2 | ADR-0002: calibration (isotonic on past folds) in the lab and the daily decision; what the 0.55 / 0.60 thresholds then mean | ADR-0001 Phase 3 #1 | — | M |
| 3 | Score live-test batch 1 after 2026-11-03, no later than 2026-11-14 (`python -m civalpha.livetest`) | docs/research/live-test-2026-10.md §5 | outcomes resolved | S |

## P2, after

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 4 | Frontend guide text that still says 10 days | ADR-0001 Phase 2 #3 | the ablation page in progress to land | S |
| 5 | Sizing guard: a stock with an unexplained raw-close move beyond 50% and no corporate action is quarantined from sizing | research report §Open | decision: changes the recorded book | S |
| 6 | Append batch 2's fingerprint when its 10th as-of date is issued | docs/research/live-test-2026-10-batch2.md §5 | 10 as-of dates | S |
| 7 | Backdate universe membership for the 312 companies added on 2026-10-05 | research report §Open | owner's call (survivorship bias) | M |
