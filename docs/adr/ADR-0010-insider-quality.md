# ADR-0010: Tell informed insider trades from routine ones, and read them after a policy shock

**Status:** Accepted (2026-10-24, owner: "3", after ADR-0009; implemented and measured 2026-10-24)
**Date:** 2026-10-24
**Deciders:** David Sakhelashvili (owner)

## Context

The owner asked for insider information that makes the policy events more effective. ADR-0009 loaded the event
history this needs; this ADR is the insider half.

* **What is stored** (`insider_transaction`, V9; `platform/insiders.py`): 363,808 non-derivative Form 4 rows for 345
  companies from 2020-07 (the SEC's quarterly data sets for six years plus each company's newer Form 4 XML). Open-market
  purchases (code P) are 4,989 rows at 291 companies; open-market sales (S) 170,683 rows at 344.
* **What the models see**: `insider_net_63d` (net open-market dollars over 63 trading days / market cap) in every model,
  `insider_buyers_21d` and `insider_sellers_21d` (distinct insiders) in the GBM models, three setups (one buy, cluster
  buy, cluster sell). Every P and S counts the same.
* **What is not captured**:
  * the 10b5-1 flag. Since 2023-04-01 a Form 4 says whether the trade was made under a pre-arranged trading plan. The
    stored data sets carry it as `SUBMISSION.AFF10B5ONE` from 2023-Q1 (2023-Q2: 3,331 of 51,791 Form 4 filings ticked;
    every quarter since: 3,665 to 5,301; all issuers, not only the universe). Every stored Form 4 XML has `aff10b5One` (154 of the latest 400 ticked).
    `parse_dataset` and `parse_form4_xml` drop it. A planned sale was decided months earlier, by construction.
  * the role. `relationship` collapses to Officer > Director > TenPercentOwner; `title` is kept but not loaded
    (`db.load_bundle` reads neither). The chief executive's and chief financial officer's trades are the ones the
    literature finds most informative.
  * routine trading. Cohen, Malloy & Pomorski (2012, "Decoding Inside Information") classify an insider as routine
    when they traded in the same calendar month in each of the three previous years; routine trades predicted nothing,
    the remaining (opportunistic) trades carried the information.
* **The event link needs the filter.** With ADR-0009's history, from 2020-07 to 2026-10, `trade_shock` fell below
  −0.03 for 543 company-days on 98 dates. An open-market purchase followed within 21 trading days for 27 of them (23
  dates) and a sale for 323. After tariff relief (+0.03): 331 company-days, 22 with a purchase, 224 with a sale. After
  a rate hit (`rate_shock` < −0.01): 104, 3 with a purchase, 55 with a sale. So purchases after a shock are too few to
  test; sales are many, but mostly planned and routine sales that say nothing about the shock.

## Decision

1. **Store the 10b5-1 flag.** `insider_transaction.plan_10b5_1 BOOLEAN` (migration `V23__insider_plan_flag.sql`): TRUE
   ticked, FALSE not ticked, NULL not reported (filings before the 2023 form). Read from `AFF10B5ONE` (`1`/`true`,
   `0`/`false`, empty) and from the XML's `aff10b5One`. Rows already stored get it from the stored zips and XML
   documents (no download), in the insider step of the pipeline, for documents that still have rows filed from
   2023-04-01 with the flag NULL.
2. **Role from the title.** `CEO_CFO` when the title says chief executive, CEO, chief financial or CFO; otherwise the
   stored relationship. Computed at load time (`db.load_bundle` reads `relationship`, `title`, `plan_10b5_1`).
3. **Planned, routine, informed or not classifiable**, point in time, per trade: `PLANNED` when the 10b5-1 box is
   ticked; otherwise the owner's earlier open-market trades (available before this one) are looked at by calendar
   year. A trade is `ROUTINE` when the owner traded in the same calendar month in each of the three previous years,
   `NOT_CLASSIFIABLE` when the owner has no open-market trade in one of those three years or the data do not reach back
   three years, and `INFORMED` otherwise.
4. **Two new inputs, NaN where they cannot be known.** `insider_informed_net_63d`: net open-market dollars of `INFORMED`
   trades over 63 trading days / market cap; `insider_exec_buyers_63d`: distinct CEO/CFO with an `INFORMED` purchase
   over 63 trading days. Both are NaN before the first classifiable month (2023-07 here) and 0 after it when nothing
   qualified. They go into the signal-health list and the ablation's additions ("plus insider quality"), not into a
   nightly lab row: the ablation runs the same walk-forward on demand, and a nightly row would add one walk-forward to
   every pipeline run (changed while implementing, 2026-10-24). The live forecast models and the recorded book do not
   change under this ADR.
5. **The reaction to a shock, only if testable.** `insider_shock_response`: for a company whose `trade_shock +
   rate_shock` crossed −0.03 or +0.03 in the last 42 trading days, the `INFORMED` net dollars in the 21 trading days
   after the crossing / market cap, signed so that buying after a hit and selling after relief are positive; 0
   otherwise. It is built only if at least 300 (company, crossing) pairs since 2023-07-02 have an `INFORMED` trade in
   that window; the count is recorded here either way.
6. **Setups.** `INSIDER_INFORMED_BUY` (the first close at which an `INFORMED` purchase is public) and
   `INSIDER_INFORMED_SELL` (two or more insiders with `INFORMED` sales within 21 trading days), and, if decision 5
   holds, `SHOCK_INSIDER_DISAGREES` (an `INFORMED` purchase within 21 trading days of a hit).
7. **The test is written down before it runs.** Signal health for the new inputs (Bonferroni over every input tested),
   the setup playbook, and the ablation row "plus insider quality". An input counts as informative only with the
   signal-health grade INFORMATIVE; a setup only as SUPPORTED. Adding any of them to the recorded book is a new live
   test, not part of this ADR.
8. **The company page shows it.** Each insider trade gets badges: planned (10b5-1), routine, informed, CEO/CFO, with
   the counts of each over the 21, 63 and 252-day windows.

## Options considered

### Option A: flag, role and routine classification, then the shock reaction if the counts allow (chosen)
Pros: uses data already stored; the published classification rather than one tuned here; the shock reaction is
tested only where the sample can say something. Cons: three years of history are spent on the classification, so the
informed inputs exist from 2023-07 only (3.3 years).

### Option B: the shock reaction on all open-market trades now
Pros: no new columns; the most direct answer to the owner's question. Cons: 27 purchases after hits; the 323 sales
are mostly planned and routine, so a result would measure the calendar of compensation sales.

### Option C: the 10b5-1 flag only
Pros: one column, exact. Cons: the flag exists from 2023 only and leaves routine non-plan trading in.

### Option D: a commercial insider-sentiment data set
Pros: longer history, cleaned. Cons: a licence, data the owner cannot re-derive from the SEC's files, ADR-0005's gate.

## Trade-off analysis

| | A | B | C | D |
|---|---|---|---|---|
| New downloads | none | none | none | vendor |
| Sample for the inputs | 2023-07 to now | 2020-07 to now | 2023-04 to now | vendor |
| Planned sales removed | yes | no | yes | vendor rule |
| Routine trading removed | yes | no | no | vendor rule |
| Shock reaction testable | if ≥ 300 informed pairs | 27 purchases | no | unknown |

## Consequences

Easier: an insider sale can be read as "planned" or "routine" on the company page; the event link is tested on the
trades that could carry information.

Harder: two more inputs on the signal-health list raise its Bonferroni bar for every other input; each ablation run
counts one more trial against the deflated Sharpe.

Revisit: if no informed input is INFORMATIVE, drop them from the lab rather than keep tuning (the windows and the
three-year rule are fixed here); if one is, a live test before any book change.

**Out of scope here:** derivative transactions (options), Forms 3 and 5, joint filings' second owners, a change to
`insider_net_63d` or the live models, any change to the recorded book.

## Action items

Phase 1, the data:
- [x] `V23__insider_plan_flag.sql`; `parse_dataset` and `parse_form4_xml` read the flag; stored rows get it from the
      stored documents (`InsiderIngestionService.backfill_plan_flags`, at the start of every insider step); tests on
      data-set and XML shapes (`backend/tests/test_insider_quality.py`).
- [x] `db.load_bundle` loads relationship, title and the flag; `insider_quality.insider_role` and `classify` (pure,
      point in time; `DataBundle.insider_classes`) with tests that a later trade never changes an earlier class.

Phase 2, the inputs and the measurement:
- [x] `insider_informed_net_63d`, `insider_exec_buyers_63d` in the panel (`strategies/panel.py`), signal health and the
      ablation's additions; setups `INSIDER_INFORMED_BUY` and `INSIDER_INFORMED_SELL`.
- [x] Count the (company, crossing) pairs of decision 5: 76, below 300; `insider_shock_response` and
      `SHOCK_INSIDER_DISAGREES` are not built.
- [x] Run signal health, the setup playbook and the ablation; record the verdict here.

Phase 3, the screen:
- [x] Company page insider badges (planned, routine, informed, not classifiable; CEO/CFO) and window counts
      (`GET /api/companies/{symbol}/insiders`: `quality`, `role`, `tradingPlan`, `informedBuys`, `informedSells`,
      `plannedSells`, `routineTrades`); guide entries; README.

*Found while implementing:*
* The flag was read for 211,754 stored rows in 124 seconds from the stored zips and XML (no download); no row filed
  since 2023-04-01 is left without it. Since then 72,807 of 105,116 open-market sales (69%) and 67 of 2,462 purchases
  ticked the box.
* Classes of the open-market trades public since 2023-07-02 (data start 2020-07-02, first classifiable month 2023-07):

  | Class | Purchases | Sales |
  |---|---|---|
  | INFORMED | 194 | 7,781 |
  | ROUTINE | 210 | 2,240 |
  | PLANNED | 62 | 70,790 |
  | NOT_CLASSIFIABLE | 1,804 | 20,753 |

  46 of the informed purchases are by a CEO or CFO.
* Decision 5's count: since 2023-07-02, `trade_shock + rate_shock` crossed −0.03 for 341 company-days and +0.03 for 166;
  an informed trade followed within 21 trading days for 47 (5 purchases, 43 sales; 30 dates) and 29 (6, 24; 17 dates).
  76 pairs, below the 300 the rule asks for: the reaction to a shock is not testable on this history.
* A NULL title arrives from pandas as NaN; `insider_role` treats both as missing (test).

**Verdict (2026-10-24): the informed inputs carry no measurable information either.**

| Test | Result | Reads as |
|---|---|---|
| Signal health (job 242, 44 inputs, bar \|t\| 3.25) | `insider_informed_net_63d` mean monthly IC −0.006, t −0.29 (38 months); `insider_exec_buyers_63d` −0.044, t −0.98 (19 months); for reference `insider_net_63d` +0.0005, t 0.02 | NOISE, NOISE |
| Setup playbook, 21 days (job 243) | INSIDER_INFORMED_BUY 17 firings (30 needed); INSIDER_INFORMED_SELL 56 firings, 41% beat the ETF, mean −0.2% (CI −2.4% to +2.2%) | NOT TESTABLE, NOISE |
| Ablation, book's GBM (job 245, 21-day label) | plus insider quality Brier skill −0.025 (−0.042, −0.014), AUC 0.512; without −0.025 (−0.042, −0.014), AUC 0.512; the trial registry counts 76 trials | no change |

The recorded book and the live models are unchanged (decision 7). Revisit stands: drop the two inputs from the lab
rather than tune them; the classification stays, since it is what the company page shows.
