# ADR-0009: Load seven years of official policy events, and only the material ones

**Status:** Accepted (2026-10-24, owner: "3"; implemented, history loaded and measured 2026-10-24)
**Date:** 2026-10-24
**Deciders:** David Sakhelashvili (owner)

## Context

The owner asked for insider information that makes the policy events "more effective". Measuring that first needs
events with a history, and there is none:

* **Every stored event is from 2026.** `policy_event` holds 754 rows, all published in 2026 and all first collected
  from 2026-10-04: the sources (`platform/events/live.py`) read the Federal Register's newest 40 documents matching
  "tariff", the Federal Reserve's monetary press RSS and news RSS, so nothing older than a few weeks ever arrived.
* **The models read 54 of them.** `pit.events_as_of` keeps `evidence_status = 'OFFICIAL'` only: 4 FOMC statements and
  50 Federal Register documents, 49 of them in October 2026. So `trade_shock` and `rate_shock` (`features.py`) are 0
  on every training day from 2019 to 2026-03. Since insider data starts (2020-07), 0 company-days had a tariff shock
  below the setup threshold (−0.03) and one day had a rate shock (measured 2026-10-24).
* **So "events make the model worse" was never tested.** The AUGMENTED-vs-BASELINE result
  (`docs/research/2026-10-forecasting-polish.md`, Brier skill −0.015 vs −0.006, CI excluding zero), the ablation row
  "plus policy events" (lab Sharpe 0.74 vs 0.91), ADR-0001's "lab noise" note and the book's choice of a model without
  event inputs (2026-10-06) measured `fedfunds_chg_x_lev` (FRED vintages, which do have history) plus two columns that
  were almost always zero.
* **The current tariff query is mostly noise.** "tariff" in the Federal Register matches more than 10,000 documents
  for 2019 to 2026 (the API's count cap): 4,574 are electricity-tariff filings of the energy regulator (FERC) and
  6,562 are Commerce's anti-dumping and countervailing-duty case notices. Of the 50 official events stored, 44 are
  such case notices (thermal paper from Korea, xanthan gum from China), each matched to every company with an
  exposure to that country at severity 0.5.
* **The headline trade actions are a small, findable set** (Federal Register API counts, 2019-01-01 to 2026-10-23):
  446 presidential proclamations and executive orders matching "tariff | duties | imports" (108 of them pass the
  existing trade keyword rule: the steel and aluminum adjustments of every year, the 2025 reciprocal tariffs, the China
  duty orders); 226 Trade Representative documents on Section 301; 361 Commerce documents on Section 232; 152 export
  control rules of the Bureau of Industry and Security naming the Entity List.
* **Rate decisions have a parser gap.** `signals.rate_decision` takes the change from "by 1/4 percentage point"; the
  2019-07-31 statement says "decided to lower the target range for the federal funds rate to 2 to 2-1/4 percent", with
  no "by", so it parses as 0 bps. A hold ("maintain the target range ... at 4-1/4 to 4-1/2 percent") does not match at
  all (3 of the 4 stored 2026 statements have no decision attributes). Statement pages back to 2019 are on
  federalreserve.gov at `/newsevents/pressreleases/monetaryYYYYMMDDa.htm` (200 for 2019-07-31, 2020-03-03,
  2020-03-15, 2022-03-16, 2024-12-18).
* **Exposures exist from 2020** (`company_exposure`: 495 rows in 2020, about 2,700 a year since, 338 companies; China
  170 companies, autos 113, semiconductors 95, steel 58), so a backfilled event can hit companies from 2020 on.

## Decision

1. **Every event gets a stream.** New nullable column `policy_event.stream` (migration `V22__event_stream.sql`), one of
   `PRESIDENTIAL`, `USTR_301`, `SECTION_232`, `EXPORT_CONTROL`, `FOMC`, `MANUAL`, `AD_CVD`, `OTHER_TRADE`; NULL for
   news-only events. `MANUAL` is an official event the owner entered on the Events page: he judged it material. `events/streams.py` classifies a Federal Register document from its metadata, first rule that applies:
   1. type Presidential Document → `PRESIDENTIAL`;
   2. agency `trade-representative-office-of-united-states` and "Section 301" in title or abstract → `USTR_301`;
   3. "Section 232" in title or abstract → `SECTION_232`;
   4. agency `industry-and-security-bureau`, type Rule, "Entity List" or "Export Administration Regulations" in title
      or abstract → `EXPORT_CONTROL`;
   5. agency `international-trade-administration` or `international-trade-commission` with "antidumping",
      "countervailing" or "five-year review" → `AD_CVD`;
   6. anything else → `OTHER_TRADE`.

   An FOMC statement is `FOMC`. Existing official events are classified from their stored source document.
2. **The models read the material streams only.** `pit.events_as_of` keeps OFFICIAL events whose stream is in
   `MODEL_STREAMS = (PRESIDENTIAL, USTR_301, SECTION_232, EXPORT_CONTROL, FOMC, MANUAL)`. `AD_CVD` and `OTHER_TRADE` stay
   stored and visible on the Events page, marked "not used by the models".
3. **One set of Federal Register queries for live and history.** `federal_register(start, end)` sends four queries
   (presidential proclamations and executive orders with "tariff | duties | imports"; Trade Representative documents;
   documents with "section 232"; Bureau of Industry and Security rules with "entity list") and keeps the documents
   whose stream is a model stream (a presidential document must also pass the existing trade keyword rule). The live run asks for the last 14 days; the backfill asks year by year,
   paginated. The old single "tariff" query goes, so new case notices and FERC filings are no longer collected.
4. **Severity by stream, fixed here before any measurement:** `PRESIDENTIAL` 1.0, `SECTION_232` 0.8, `USTR_301` 0.8,
   `EXPORT_CONTROL` 0.6 (was 0.5 for every Federal Register document). Sign stays as `features.trade_event_sign`
   (relief +1, proposed −0.5, otherwise −1). Targets stay `signals.trade_targets` on title and abstract; a presidential
   document with no target there gets them from the first 5,000 characters of its full text (`raw_text_url`).
5. **FOMC history from the rate changes.** For every change of FRED `DFEDTARU` (upper target, current values) since 2019-01-01, the
   statement of the decision day (the change's effective date minus 1 to 3 days, first page that answers 200) is
   fetched, stored and ingested as an official `RATE_DECISION`, `published_at` = 14:00 New York on the statement date
   (the release time; the emergency statements of 2020-03-03 and 2020-03-15 came out earlier or on a Sunday, so this is
   never before the true release). `rate_change_bps` is the change in the upper target against the previous decision;
   a statement whose parsed range disagrees with FRED is logged and skipped. Holds are not backfilled: their
   contribution to `rate_shock` is 0 by the formula.
6. **The rate parser is fixed for live runs too.** A hold ("maintain ... at X to Y percent") parses with 0 bps; a
   change without "by" takes its size from the previous FOMC event's upper target.
7. **A job, not a seed.** Job `EVENT_BACKFILL` (params `from`, default 2019-01-01, and `to`, default today) runs
   decisions 1, 3 and 5 through `EventService.ingest`, so dedup, evidence storage and actor records are the same as for
   live events, and a rerun stores nothing twice. It starts from the Admin page ("Load official event history") or
   `POST /api/admin/events/backfill`. The installation has nothing until the owner runs it.
8. **The test is written down before it runs.** After the backfill, in this order: the walk-forward evaluation
   (BASELINE vs AUGMENTED, 21 days), signal health for `trade_shock` and `rate_shock`, the setup playbook's
   `TARIFF_SHOCK` and `RATE_SHOCK`, and the feature ablation's "plus policy events" row. Events count as helping only
   if AUGMENTED's Brier skill minus BASELINE's is above zero with its 95% interval excluding zero; a lab Sharpe is not a
   criterion. The recorded book does not change under this ADR whatever the result; adding event inputs to it would be
   a new live test. The result and the counts per stream go into this ADR and the research report, and the documents
   that state "events were tested and did not help" get a dated note.

## Options considered

### Option A: backfill the material streams, classify everything, filter the models (chosen)
Pros: the model sees the instruments that move prices (presidential tariffs, Section 232 and 301, export controls,
rate changes) from 2019; live and training use the same rule; nothing stored is deleted. Cons: the choice of streams
is a judgment made in 2026, with hindsight about which statutory instruments mattered.

### Option B: backfill the existing "tariff" query as it is
Pros: one loop, no new rule. Cons: over 10,000 documents, most of them FERC filings and narrow case notices; each
case notice hits every company exposed to the country, so `trade_shock` would be a count of trade-case paperwork.

### Option C: backfill only FOMC decisions
Pros: about 25 events, exact sizes, no classification. Cons: `trade_shock` stays empty, and tariffs are the part of
the events the owner's portfolio question is about.

### Option D: buy an event data set (a news-analytics vendor)
Pros: broader coverage, timestamps to the second. Cons: a licence and a cost, data the owner cannot re-derive from the
official documents, and ADR-0005's licence gate (#11) before any outside user.

## Trade-off analysis

| | A: material streams | B: "tariff" as is | C: FOMC only | D: vendor |
|---|---|---|---|---|
| Events 2019 to 2026 | about 700 after the keyword rule (estimate) | over 10,000 | about 25 | vendor-dependent |
| Share that is material | high by construction | about 1 in 10 at best | all | unknown |
| Same rule live and history | yes | yes | yes | no |
| Requests for the backfill | about 40 list pages + about 100 full texts + about 50 statement pages | 10+ pages per year | about 50 | none |
| Cost | none | none | none | licence |

## Consequences

Easier: the event inputs can be judged at all; the Events page tells the owner which events the models read;
the FOMC history fixes the 0 bps parse for every future statement too.

Harder: a rule of judgment (the streams) to keep in step with how trade policy is issued: a new
statutory instrument (for example IEEPA orders published as something other than a presidential document) needs a rule.
The live Federal Register step sends four requests instead of one.

Revisit: if the evaluation finds no help, look at the exposure side before dropping events (product vocabulary:
pharmaceuticals, copper, lumber and heavy vehicles have tariffs but no exposure keyword); if it finds help, a live
test before any book change.

**Out of scope here:** news events in the models; new product keywords for exposures (they need the exposure extraction
re-run); the insider reaction to events and insider data quality (ADR-0010); any change to the recorded book.

## Action items

Phase 1, the rule and the data:
- [x] `V22__event_stream.sql`: `policy_event.stream` with a CHECK on the eight values; FOMC rows set by the migration.
- [x] `events/streams.py` (classification, severity) and tests on real document metadata shapes
      (`backend/tests/test_event_history.py`, 12 tests).
- [x] `live.py`: `federal_register(start, end)` with the four queries and pagination; live run on the last 14 days;
      `fomc_history` from `DFEDTARU`; `signals.rate_decision` hold and no-"by" fixes.
- [x] `EventService.ingest` stores the stream (and sets it when a news event is upgraded); `classify_unstreamed` for
      existing official events (also at the start of every pipeline run's policy-event step).
- [x] `pit.events_as_of` and `db.load_bundle` read the stream; `MODEL_STREAMS` filter; tests that an `AD_CVD` event
      changes no feature.
- [x] Job `EVENT_BACKFILL` (`events/backfill.py`), `POST /api/admin/events/backfill`, Admin page step, Events page
      stream chip, filter and count, event page chip, guide entries.

*Found while implementing:*
* FRED refuses `DFEDTARU` with all vintages: "3819 vintage dates ... exceeds the maximum" (HTTP 400, job 234). The
  target range is set by the FOMC and never revised, so the history reads its current values
  (`MacroService.current_values`, stored as a document, nothing in `macro_observation`).
* Three statements state a bare fraction ("to 0 to 1/4 percent", 2020-03-15; "to 1/4 to 1/2", 2022-03-16; "to 3/4 to
  1", 2022-05-04), which the number pattern did not accept; fixed and tested (job 236 read all 23).
* The tests' clock is now UTC like the containers' (`tests/conftest.py`): between local midnight and UTC midnight a
  company added "today" was not a member yet in three universe tests. Production was not affected.

Loaded 2026-10-24 (jobs 234 to 236; 2019-01-01 to 2026-10-24):

| Stream | Events | With a target | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| USTR_301 | 146 | 127 | 29 | 45 | 23 | 10 | 5 | 10 | 14 | 10 |
| EXPORT_CONTROL | 136 | 81 | 14 | 17 | 20 | 23 | 24 | 27 | 10 | 1 |
| PRESIDENTIAL | 87 | 32 | 3 | 10 | 4 | 8 | 5 | 7 | 31 | 19 |
| SECTION_232 | 73 | 27 | 9 | 16 | 12 | 6 | 2 | 3 | 18 | 7 |
| FOMC | 26 | 26 | 3 | 2 | 0 | 7 | 4 | 3 | 3 | 4 |
| AD_CVD (not read) | 43 | 37 | | | | | | | | 43 |
| OTHER_TRADE (not read) | 6 | 2 | | | | | | | | 6 |

The 23 FOMC changes match the known path (three cuts in 2019, −50 and −100 bps in March 2020, +475 bps from 2022-03 to
2023-07, −100 bps in 2024, −75 bps in 2025, +25 bps on 2026-09-16). Of the 442 trade events the models read, 267 have a
target: presidential and Section 232 actions on copper, lumber, pharmaceuticals or heavy vehicles have none, since those
products have no exposure keyword (Revisit). Since insider data starts (2020-07), `trade_shock` fell below −0.03 for 543
company-days on 98 dates (0 before the history) and `rate_shock` below −0.01 for 104 on 19 dates (7 on 1 before).

Phase 2, the measurement (decision 8):
- [x] Run the backfill; record counts per stream and per year here.
- [x] Walk-forward evaluation, signal health, setup playbook, ablation; record the verdict here and in the research
      report; dated notes in `docs/research/2026-10-forecasting-polish.md`, ADR-0001 and the README.

**Verdict (2026-10-24): the events do not help, and now that was tested.**

| Test | Result | Reads as |
|---|---|---|
| Walk-forward, 21 days (job 237, 6,094 predictions per model) | Brier skill baseline −0.006, augmented −0.017; AUC 0.505 vs 0.498 | augmented worse, 95% CI excludes zero (decision 8's rule: no help) |
| Signal health (2 tests, bar \|t\| 2.24) | `trade_shock` mean monthly IC −0.015, t −0.87 (72 months); `rate_shock` −0.028, t −1.05 (32 months) | NOISE, NOISE |
| Setup playbook, 21 days (job 241) | TARIFF_SHOCK 543 firings, 49.5% beat the ETF, mean +0.7% (CI −0.1% to +1.6%); RATE_SHOCK 104 firings, 45%, +0.5% (CI −0.9% to +2.9%) | NOISE, NOISE |
| Ablation, book's GBM (job 240) | plus policy events Brier skill −0.030 (−0.049, −0.018), AUC 0.508; without −0.025 (−0.042, −0.014), AUC 0.512 | no help |

The recorded book stays as it is (decision 8). The 2026-10-06 choice of a book model without event inputs stands, on
evidence it did not have then.

*Again on the 2015 history (ADR-0012, 2026-10-25/26):* the walk-forward on 115,547 predictions per model gives BASELINE
−0.001 (AUC 0.504) against AUGMENTED −0.001 (0.502), augmented still worse with the interval excluding zero;
TARIFF_SHOCK fired 6,700 times and RATE_SHOCK 1,859, both NOISE at 21 days; the ablation's "plus policy events" gives
Brier skill −0.0032 (−0.0043, −0.0018), AUC 0.505, against FULL −0.0030 (−0.0040, −0.0017), AUC 0.505 (job 269). The
verdict holds on 19 times the data.

**2026-10-27, pending re-measurement:** the 2015 history universe holds 26 companies whose SEC-frame public float is
far above their shares times close, most about 1,000 times (scale errors, 50 member-years; Medallion Financial, Party City,
PEDEVCO, Vintage Wine Estates, FuelCell, ...) and keeps bankrupt companies as members after their stock moved to the
over-the-counter market (SVB as SIVBQ, 2023-03 to 2024-04, closes of $0.001). The 2015-history numbers here were
measured on that universe (ADR-0012, Found while measuring).

*Found while measuring:*
* **Signal health never tested the event inputs**: its list is the AI inputs plus dividends (`signals.run_study`),
  which excludes `EVENT_FEATURES`. The two rows above were run with `features=["trade_shock", "rate_shock"]`.
* **The setup playbook's bootstrap collapsed for setups that fire on few dates.** It resampled blocks of 21
  consecutive firing dates; RATE_SHOCK fired on 19, so every resample held all of them and the 95% interval was
  "+0.3% to +0.3%" around a mean of +0.5%, which made it SUPPORTED (job 239). Blocks are now 21 calendar days over the
  span the firings cover (`setups.block_bootstrap`, study version setups-0.2.0, test
  `test_a_setup_that_fired_on_few_dates_gets_a_wide_interval`). The correction also moved two other verdicts: "Results
  day: stock jumps" (154 firings) from SUPPORTED to NOISE, and "Volume surge on a down day" to SUPPORTED. The same
  date-block pattern is in `evaluation.date_block_bootstrap` and `doublers._block_bootstrap_rate`, where the rows fall
  on hundreds of dates; not changed here (BACKLOG #27).
