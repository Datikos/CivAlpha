# Backlog

State on 2026-10-19: 7 open items across 3 ADRs (ADR-0002, ADR-0003, ADR-0005) and the October 2026 research report. Rows leave when their item is
ticked in its ADR; a new item goes into an ADR first, then here. Sizes: S up to a day, M a few days, L a week or more.

## P1, before the live test resolves (2026-11-03)

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 2 | ADR-0003: thresholds, once batch 2 of the live test resolves and the calibrated lab rows have been looked at | ADR-0002 Phase 3 #2 | batch 2 resolved | M |
| 3 | Score live-test batch 1 after 2026-11-03, no later than 2026-11-14 (`python -m civalpha.livetest`) | docs/research/live-test-2026-10.md §5 | outcomes resolved | S |
| 15 | Score live-test batch 2 once its windows have closed: after 2026-11-16, no later than 2026-11-30 (`python -m civalpha.livetest 2`; population pinned, fingerprint `52cf870d…`) | docs/research/live-test-2026-10-batch2.md §3, §5 | outcomes resolved | S |

## P2, after

| # | Task | Ref | Needs | Size |
|---|---|---|---|---|
| 7 | Backdate universe membership for the 312 companies added on 2026-10-05 | research report §Open | owner's call (survivorship bias) | M |
| 11 | Go-live gate: data licence and advice regulation answered before the first account for someone outside the owner's household | ADR-0005 Decision 11 | owner + legal advice | S |
| 13 | Review the 125 feed gaps on the Corporate actions page (most are mentions; link the feed's action where it adjusted the prices) | ADR-0003 phase 3 | owner | S |
| 14 | Set `CIVALPHA_ADMIN_TOKEN` (empty since 2026-10-07 21:59): holdings are readable by anything that reaches the port. Low priority by the owner's decision of 2026-10-15, because the stack is bound to 127.0.0.1 (`CIVALPHA_BIND`); required before binding to another interface or switching to accounts mode | ADR-0004 Decision 8, ADR-0005 | owner | S |
