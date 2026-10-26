# Backlog

State on 2026-10-26: 20 open items (#7 closed by ADR-0012; #30 added) across 6 ADRs (ADR-0002, ADR-0003, ADR-0004,
ADR-0005, ADR-0008, ADR-0009); ADR-0009 to ADR-0012 are implemented and measured, the two live-test documents and
the October 2026 research report. Rows leave when their item is ticked in its ADR; a new item goes into
an ADR first, then here (rows marked "needs an ADR" are proposals that get one when started). Row numbers never move.
Sizes: S up to a day, M a few days, L a week or more.

## P0, data correctness and daily operation

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 17 | Guard against double adjustments: the pipeline flags a day whose total-return move exceeds 50%, the Corporate actions page warns when a feed action lands on a day that already has a hand-entered one, and the price sync does not store a feed `SPLIT` on a day with a hand-entered `SPIN_OFF` (a full history re-download would otherwise bring back the CTVA row deleted under #16). Measured 2026-10-20: CTVA was the only such day of 14 total-return moves beyond 50% | proposal of 2026-10-20 (needs an ADR) | — | S |
| 26 | Spin-offs the feed encodes as split factors: 18 of the 77 feed `SPLIT` rows (PFE 2020, MRK 2021, IBM 2021, T 2022, GE 2023 and 2024, FDX 2026, ...) are spin-offs. The total return is right for them, but market cap (and with it sales and earnings yield) is scaled by the factor until the next filed share count, and every earlier dividend is divided by it (yield and growth). Found 2026-10-24 under #16 | ADR-0003 Phase 3 (needs an ADR) | — | M |
| 28 | The company page's price chart compares raw closes across a split: the sector ETFs split 2-for-1 on 2025-12-05 (XLK, XLY, XLB, XLE, XLU), so AAPL shows "excess +70.6%" over the last year. Display only: the models' total-return index applies the splits | found 2026-10-24 (needs an ADR or a fix) | — | S |
| 18 | Move the evening run off 02:30 Tbilisi: 7 runs between 2026-10-09 and 2026-10-23 lost the network or started hours late while the Mac slept; the catch-up recovered each. Suggested `CIVALPHA_PIPELINE_CRON=0 0 7 * * TUE-SAT` (07:00 New York, the previous day's session) | ADR-0008 | owner (`.env`) | S |

## P1, before the live test resolves

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 3 | Score live-test batch 1 after 2026-11-03, no later than 2026-11-14 (`python -m civalpha.livetest`) | docs/research/live-test-2026-10.md §5 | outcomes resolved | S |
| 15 | Score live-test batch 2 once its windows have closed: after 2026-11-16, no later than 2026-11-30 (`python -m civalpha.livetest 2`; population pinned, fingerprint `52cf870d…`, confirmed final 2026-10-19) | docs/research/live-test-2026-10-batch2.md §3, §5 | outcomes resolved | S |
| 30 | Live test of `exec_departure_63d` (a CEO or CFO change filed in the last 63 trading days): INFORMATIVE on the 2015 history (mean monthly IC −0.0126, t −3.58, 135 months, ADR-0011/ADR-0012); pre-register a lab row and a live batch before any book change | ADR-0011 decision 6 (needs an ADR) | — | M |
| 2 | Thresholds of the book (ADR-0003 in the ADR-0002 plan), once batch 2 resolves and the calibrated lab rows have been looked at | ADR-0002 Phase 3 #2 | #15 | M |
| 13 | Corporate-action review: done for 118 of the 125 gaps (43 linked to an action, 75 rejected as mentions; CTVA's 8-K of 2026-04-14 linked on 2026-10-24). Left: PRPO's reverse-split 8-K of 2024-05-06 (nothing recorded, no price break), and 6 confirmed pending events to enter when distributed (WBD Global Linear Networks, Eaton mobility ×2, Keurig Dr Pepper coffee) | ADR-0003 phase 3 | owner | S |

## P2, after

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 29 | Event targets for the products tariffs name but exposures do not: 175 of the 442 trade events the models read have no target (copper, lumber, pharmaceuticals, heavy vehicles have no exposure keyword), so they hit nobody. The next lever for the events after ADR-0009 found no help; needs the exposure extraction re-run | ADR-0009 (Revisit; needs an ADR) | — | M |
| 27 | The date-block bootstrap of `evaluation.date_block_bootstrap` and `doublers._block_bootstrap_rate` resamples blocks of the dates that have rows; the setup playbook's version collapsed for a setup on 19 dates (ADR-0009). Both run on hundreds of dates today; move them to calendar-day blocks too, or prove they cannot collapse | ADR-0009 (Found while measuring) | — | S |
| 19 | Corporate-action matching rule v2: count a cash dividend worth at least 2% of the close on the ex-date as a match (Danaher and 3M's feed-adjusted spin-offs show as gaps today), measured on the same evaluation as v1; the dividend inputs see those two distributions as dividends of 11–14% | ADR-0003 (Revisit) | — | S |
| 20 | Jev classifier for 8-Ks: run the pre-registered comparison against the keyword rule, adopt only if it passes | ADR-0003 phase 2 | `TYPESAFE_API_KEY` (owner) | M |
| 21 | Short interest (FINRA) and filing-text diffs as model inputs | research roadmap step 4 (needs an ADR) | — | M–L |
| 22 | Purged k-fold for hyperparameters, meta-labelling, sector-neutral ranking | research roadmap step 5 (needs an ADR) | #7 to matter | L |
| 23 | Thesis journal: the owner's reasoning recorded next to the model's and scored later | research roadmap step 6 (needs an ADR) | — | M |
| 24 | Deflated-Sharpe column chart with the trial count in the Strategy lab; before/after chart in the research report | charts-over-text ideas 6–7 | — | S |
| 25 | Portfolio data: average cost for the 15 holdings (none entered) and cash (0), so gain and loss show and an Add can be sized | ADR-0004 (My portfolio) | owner | S |

## Gate before outside users

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 11 | Data licence and advice regulation answered before the first account for someone outside the owner's household | ADR-0005 Decision 11 | owner + legal advice | S |

## Low priority (owner's decision)

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 14 | Set `CIVALPHA_ADMIN_TOKEN` (empty since 2026-10-07 21:59): holdings are readable by anything that reaches the port. Low priority by the owner's decision of 2026-10-15, because the stack is bound to 127.0.0.1 (`CIVALPHA_BIND`); required before binding to another interface or switching to accounts mode | ADR-0004 Decision 8, ADR-0005 | owner | S |
