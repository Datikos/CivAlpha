# ADR-0007: The book does not size a stock whose price moved more than 50% without an explanation

**Status:** Accepted (2026-10-15, the owner chose option a; implemented 2026-10-15)
**Date:** 2026-10-15
**Deciders:** David Sakhelashvili (owner)

## Context

On 2026-10-01 Corteva distributed Vylor one-for-one; the price feed recorded nothing, CTVA's raw close fell from 77.65
to 12.57 (−84%), and for four days every feature and the recorded book's volatility size treated it as a crash
(`docs/research/2026-10-forecasting-polish.md` §3). The fix was manual: a `SPIN_OFF` action entered on 2026-10-05.
BACKLOG #5 asked for a guard so the next such break does not reach the book's sizing.

What exists on 2026-10-15:

* `civalpha.integrity.scan` flags a raw-close move beyond 50% on a day with no recorded corporate action
  (`UNEXPLAINED`). On the stored data (363 series, 2019-01-02 to 2026-10-14) it finds 12, all real market events, none
  a feed error now that CTVA's action is recorded: PCG 2019-01-14 −52% and 2019-01-24 +75% (bankruptcy), PRPO 2019,
  2020 and 2021 (four), TRGP 2020-03-09 −53%, HOOD 2021-08-04 +50%, CAR 2021-11-02 +108%, CVNA 2023-06-08 +56%,
  CRCL 2025-06-05 +168% (IPO week), AGIO 2025-11-19 −51%.
* The 8-K detector (ADR-0003) warns of a distribution weeks before the price breaks, but most of its flags are
  mentions (125 open gaps on 2026-10-14, none with a broken price).
* The portfolio advice (ADR-0004) already marks a holding "Check the data" after a raw move of 30% or more with no
  action in the last 45 days. The recorded book (`AI_SIZED`) has no such rule.
* `decide_positions` (`strategies/ai.py`) is the one place the book's entries and exits are decided, in the lab
  backtest and in the daily decision (`strategies/service.decisions_at`).

## Decision

1. **A break** is a trading day on which a stock's raw close moved more than `GUARD_MOVE` = 50% from the previous
   close and no corporate action of that company (any type) is recorded on that day.
2. **The guard:** for `GUARD_DAYS` = 21 trading days from a break (the break day and the 20 after it), the recorded
   book treats the stock as not available: no entry, and a holding exits at that close (weight 0). It lifts when an
   action is recorded for the break day (a `SPIN_OFF` entered on the Corporate actions page, or one the feed delivers
   late) or when the 21 days have passed.
3. **Where:** `decide_positions(..., blocked=...)` takes the guard mask; the lab's `AI_SIZED` (the recorded book) and the
   daily decision use it. Every other lab row keeps its rule unchanged. A new lab row `AI_SIZED_NOGUARD` is the book
   without the guard, so the lab shows what the guard costs; it is registered as a trial.
4. **Shown:** a guarded stock's daily decision carries `model.guard` (`blocked`, `breakDate`, `move`, `until`), and the
   decisions page says why it is out.
5. Text gaps from the 8-K detector do not trigger the guard (option b, not chosen).

## Options considered

### Option a: unexplained moves beyond 50% only (chosen)
Pro: exactly the CTVA signature; measured history has 12 such days in 7 years across 363 series. Con: a missed
distribution smaller than 50% of the price (most spin-offs are 10–25%) is not caught; real crashes and surges also
block the stock for 21 days.

### Option b: a, plus open 8-K gaps from the last 120 days
Pro: catches smaller distributions from their announcement. Con: most flags are mentions; FDX, SPGI, AMRZ and ETN would
leave the book until reviewed.

### Option c: no guard
Pro: no change to the recorded book. Con: the next missed distribution sizes the book on a false crash, as CTVA did.

## Consequences

Easier: a feed error of CTVA's size cannot reach the book's sizing; the owner clears it by recording the action.

Harder: the book also stays out of a stock for 21 days after a genuine move beyond 50%; reviewers keep one more input
to `decide_positions`.

Revisit: the 50% threshold once the Corporate actions review list is worked through (option b becomes cheaper); the
lab's measured cost (`AI_SIZED` against `AI_SIZED_NOGUARD`) after each lab run.

**Out of scope here:** changing other lab rows, the forecasters or their features; repairing prices.

## Action items

1. [x] `strategies/ai.py`: `GUARD_MOVE`, `GUARD_DAYS`, `unexplained_breaks(panel)`, `guard_mask(breaks)`,
   `decide_positions(..., blocked=None)`; row `AI_SIZED_NOGUARD`; registry feature set.
   *Found while implementing:* the guard must not be folded into membership: `decide_positions` gives non-members a
   blank action, and a blank in the live decision breaks the `strategy_decision.action` check. A guarded stock stays a
   member and gets `STAY_OUT` or `EXIT`.
2. [x] `strategies/service.py`: the lab's `AI_SIZED` and the daily decision use the guard; `model.guard` on decisions.
3. [x] Tests `backend/tests/test_sizing_guard.py` (3): breaks need a move beyond 50% and no action that day (a recorded
   `SPIN_OFF` and a 40% move do not trigger), the 21-day mask, a guarded holding exits and cannot re-enter until the
   mask ends, the live decision carries `model.guard`.
4. [x] Decisions page card ("Sizing guard: out until …"), guide, README, `docs/api.md`; BACKLOG #5 removed.
5. [x] Lab run 34 (2026-10-15, data to 2026-10-14, out of sample from 2021-07-07, 10 bp a side; trial registry 74):

   | Row | Sharpe | CAGR | Max drawdown | Excess vs buy-and-hold, 95% CI | Trades |
   |---|---|---|---|---|---|
   | `AI_SIZED` (the book, guarded) | 0.695 | 16.5% | −25.9% | −5.7% (−16.1% to +4.1%) | 168 |
   | `AI_SIZED_NOGUARD` | 0.664 | 15.9% | −27.9% | −6.3% (−16.8% to +3.6%) | 167 |

   The guard fired twice on members in that window: CAR +108% on 2021-11-02 (a short squeeze) and AGIO −51% on
   2025-11-19; 42 member-days blocked. The 0.03 Sharpe between the rows rests on those two events and is within noise:
   the guard neither costs nor earns measurably. Its value is the case it was built for, which the history no longer
   contains (CTVA's action is recorded).
