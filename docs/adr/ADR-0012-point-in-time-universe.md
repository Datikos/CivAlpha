# ADR-0012: A point-in-time universe from 2015, so the history holds the companies that mattered then

**Status:** Accepted (2026-10-25, owner: "1+2"; Decision 9 answered the same day: a off, b 2015-04, c weekly, d yes)
**Date:** 2026-10-25
**Deciders:** David Sakhelashvili (owner)

## Context

Measuring ADR-0011 showed that every historical study runs on about 33 companies, not 352 (BACKLOG #7):

* **Membership.** `universe_membership` holds 31 companies from 2019-01-02 and 318 from 2026-10-05, the day the universe
  was expanded with the 312 largest companies by public float (job 129). Members per year: 31 in 2020, 32 to 33 to
  2025, 34 in 2026. The models, the walk-forward, the lab and the studies count member stock-days only (57,728 in the
  signal study), so ADR-0009, ADR-0010 and ADR-0011 measured their inputs on those 33 companies; 233 CEO-change firings
  became 22 on member days.
* **Backdating today's 352 would add survivors.** Of each year's 350 largest filers by public float (SEC frames, float as
  of 30 June, counted 2026-10-25 without the expansion's scale checks), only 184 (2015), 215 (2019) and 247 (2023) are in
  today's universe, and 75 (2015), 39 (2019) and 17 (2023) are no longer listed (acquired, merged, delisted). A history
  of today's survivors would leave out every company that failed or was bought.
* **The companies that left can be priced.** Tiingo serves delisted tickers on the owner's plan: Twitter (to
  2022-10-28), Activision Blizzard (2023-10-13), Xilinx (2022-02-14), Celgene (2019-11-22) return their bars; SVB's
  metadata is missing but its bars return.
* **They need a ticker.** Tiingo's metadata (20,376 tickers, 12,574 inactive) carries no SEC identifier. Matching the
  SEC entity name to Tiingo's name gives one ticker for 113 of 155 companies that left the top 350 in 2015 to 2025, two
  or more for 9, none for 33. The filings themselves carry the trading symbol (the 10-K cover page since 2019, often
  `dei:TradingSymbol` in the XBRL before).
* **The float data has scale errors** (a small company's float tagged 1,000 times too large lands in "the top 350");
  `sec/discovery.py` already rejects them against total assets and the implied price per share.
* **A longer history is available too.** `SEC_LOOKBACK_YEARS` is 6 (filings from 2020-10), prices start 2019-01-02,
  insider data sets six years back, events from 2019. Prices, 8-K item codes, insider data sets and Federal Register and
  FOMC history all reach 2015. Sector ETFs: XLRE trades from 2015-10, XLC from 2018-06; before that, their companies
  have no benchmark and no rows.
* **Cost of a bigger history.** The nightly run takes about 27 minutes (jobs 232 and 233), about 12 of them in the
  evaluation, live forecasts, lab and studies on about 33 companies x 7.8 years. About 350 companies x 11 years is
  roughly 15 times more rows: about 3 hours a night if nothing else changes. Exposure extraction calls the language
  model on trade passages when it is configured (787 exposure rows from 151 filings so far); four more years of filings
  for about 550 companies would multiply those calls.

## Decision

1. **The rule.** Each year on the first NYSE session of April, the history universe becomes the 350 largest US filers
   by validated public float (`sec/discovery.py`'s checks against assets and implied price) among floats measured on or
   before 30 June of the previous year, the newest value per company: a float as of 30 June is in the 10-K filed by about
   March, so it is known on 1 April. First reconstitution 2015-04-01 (floats of 2014); the last before the owner's
   universe takes over, 2026-04-01.
2. **Stored as membership.** Spans in `universe_membership` under universe `history-top350`, each ending at the next
   reconstitution the company misses, at its last price (delisting) or on 2026-10-05, when the owner's universe begins;
   the owner's spans are not touched. A company of the history that is not in the owner's universe is a member of the
   past only: the pipeline, the portfolio and the live forecasts never act on it.
3. **Tickers, three ways, then checked.** A company's tickers come from the SEC index (`tickers`), else the trading symbol
   of its own filings, else a unique Tiingo name match; a ticker is kept only if its Tiingo trading dates cover the years
   the company was a member. Companies left without a ticker are counted and listed in this ADR (a residual
   survivorship bias, measured).
4. **History from 2015.** `SEC_LOOKBACK_YEARS` 6 to 11 (10-K, 10-Q and the 8-K items of ADR-0011 from 2015),
   `CIVALPHA_PRICE_HISTORY_START` 2015-01-02 (and a backward fill for symbols that already have bars), insider data sets
   from 2015, the event history of ADR-0009 from 2015. Benchmarks before their launch stay missing.
5. **A one-off job loads it.** `HISTORY_BACKFILL` (Admin, Advanced) adds the history companies, their tickers, prices,
   filings, insiders and 8-K events, resumable company by company, at the SEC and Tiingo limits; expected several hours.
6. **No language model in the history load** (Decision 9a): exposures of filings before 2020-10 come from the rules and
   XBRL only, and that boundary is written next to the exposure method counts.
7. **The nightly run keeps a budget** (Decision 9c): prices, filings, events, live forecasts, decisions and advice
   nightly; the walk-forward evaluation, the strategy lab, the doubler study, the setup playbook and signal health weekly
   (Saturday). Target: the nightly run under 45 minutes, measured after the load.
8. **Then everything is measured again**: the walk-forward, signal health, the playbook and the ablation, and the
   verdicts of ADR-0009, ADR-0010 and ADR-0011 re-recorded on the new history next to the 33-company ones.
9. **The owner decided before the load (2026-10-25), each as recommended:**
   a. language model in the history load: off (recommended) or on;
   b. history start: 2015-04 (recommended) or 2017-04;
   c. heavy studies weekly (recommended) or nightly;
   d. the live models and the recorded book train on the new history (recommended; the pinned live-test populations do
      not change) or keep the 33-company history for live use and use the new one for research only.

## Options considered

### Option A: a rule-based universe per year, including the companies that left (chosen)
Pros: no survivorship from the selection; about 10 times the stock-days; the same rule as the 2026 expansion. Cons:
ticker matching for companies that left; a long one-off load; a slower nightly run unless the studies move.

### Option B: backdate today's 352 (BACKLOG #7 as first written)
Pros: one update. Cons: survivors only; the bias is largest exactly where the events of ADR-0011 matter (distress).

### Option C: a vendor's point-in-time index membership
Pros: exact constituents. Cons: a licence, and data the owner cannot re-derive.

## Trade-off analysis

| | A | B | C |
|---|---|---|---|
| Companies per year | 350 | 352 (today's) | index size |
| Includes those that left | yes (113 to 146 of 155 matched) | no | yes |
| Cost | load time | none | licence |
| Re-derivable from public data | yes | yes | no |

## Consequences

Easier: every study, the walk-forward and the lab measure on hundreds of companies over 11 years; a verdict of "no help"
or "help" means something.

Harder: a several-hour load; the nightly run and the studies take longer; tickers of companies that left need a
three-way match; companies of the past appear on the Universe page as former members; all lab numbers, trial counts and
the deflated Sharpe change, and earlier results are not comparable without the dated notes.

Revisit: the 350 cut and the April date are fixed here; changing them is a new trial for every study.

**Out of scope here:** companies below the 350 cut, ADRs and foreign filers without a US float, intraday data.

## Action items

Phase 1 (after Decision 9):
- [x] `platform/universe_pit.py`: the yearly rule from the frames with discovery's checks; tests
      (`backend/tests/test_history_universe.py`).
- [x] Ticker matching (index, cover page, Tiingo name on the current and former names) with the date check; a
      predecessor registrant whose ticker an already stored company holds, under a similar name, counts for that company
      (Medtronic Inc for Medtronic plc); tests.
- [x] `HISTORY_BACKFILL` job (`platform/history_backfill.py`, `POST /api/admin/history/backfill`, Admin step), the
      price backfill (`PriceSyncService.backfill`), insiders and 8-K events for a given company list. The job loads from
      2015 whatever `SEC_LOOKBACK_YEARS` and `CIVALPHA_PRICE_HISTORY_START` say; the nightly run keeps its window for new
      filings (decision 4 as implemented).
- [x] Nightly budget: `CIVALPHA_STUDY_DAYS` (default `FRI` in the schedule's zone: the Friday evening run in New York,
      Saturday 02:30 in Tbilisi; a catch-up counts the day of the session it catches up); a run started by hand does
      everything; the AI decisions moved out of the lab's step so they stay nightly.

*Found while implementing (jobs 257 to 259, 2026-10-25):* 664 companies were among the 350 largest at some
reconstitution from 2015 to 2026; 337 were stored already, 258 were added (tag `history`), 11 predecessor registrants
were attached to their stored successor, and 39 are left out: 38 without a ticker that trades on their first membership
day and Yahoo (SIC code without a sector). Of the 38, some are float scale errors that passed the checks (small banks and
REITs, Publix, which has no public stock); the rest are real: successors under another name that Tiingo lists under the
new name (Google Inc for Alphabet, Walgreen for Walgreens Boots Alliance, Dow Chemical, DuPont, Johnson Controls Inc,
Avago, Bunge, Apache), and companies that ended whose ticker Tiingo no longer serves or now gives to someone else
(Monsanto's MON is a 2021 SPAC; Aetna, Anadarko, EMC, Precision Castparts, Starwood, SunTrust, Mylan, Covidien, TD
Ameritrade, Spectra, Coca-Cola Enterprises, IHS Markit, Bard, WestRock): a residual survivorship bias of about 25
companies for 1 to 4 years each, against about 3,800 company-years. Members per year (mid-year): 322 (2015), 328, 334,
338, 347, 353, 354, 353, 354, 354, 355, 357 (2026); before this ADR 31 to 34.

Phase 2:
- [x] Run the load; record companies, matches, stock-days, durations.

*The load (job 260, 2026-10-25 05:23 to 17:28 UTC, 12 hours 5 minutes):* prices 1,016,300 bars for 640 symbols in 10
minutes, none failed; filings for 629 companies in 9 hours 25 minutes at the SEC limit of 5 requests a second (21 failed:
19 requests failed after retries, most of them in one SEC outage at 07:43; DOC's 10-K instance has a text node over
libxml2's 10 MB limit, now read with `huge_tree`; ARL's instance came back empty, now logged and skipped: both fixed with
tests, the 21 re-run as job 261); insiders 528,082 more transactions from 24 more quarterly data sets; 8-K events from
19,598 8-Ks (8,859 documents fetched, none failed): CEO_DEPARTURE 1,594 and CFO_DEPARTURE 1,134 by the rule `exec-1`,
OFFICER_DIRECTOR_CHANGE 9,111, MATERIAL_AGREEMENT 7,276, AGREEMENT_TERMINATED 776, ACQUISITION_COMPLETED 763,
RESTRUCTURING 339, LISTING_NOTICE 222, IMPAIRMENT 160, AUDITOR_CHANGE 80, NON_RELIANCE 39, BANKRUPTCY 36,
CYBER_INCIDENT 4; earnings 18,924 results releases read in 3 hours 46 minutes; policy events 125 more official events for
2014 to 2018 (EXPORT_CONTROL 61, SECTION_232 25, PRESIDENTIAL 17, USTR_301 13, FOMC 9). The progress bar counted the
seven steps as equal and sat at 16% through the filings; it is weighted by these durations now (`STEP_WEIGHT`). The
owner approved reading the 11,622 item 5.02 sections of the history with Claude as ADR-0011 decision 9 did (job 262,
one Message Batch, estimate about $59), so the CEO and CFO changes are measured on the model's reading, not the rule's.
- [x] Measure again (Decision 8); dated notes in ADR-0009 to ADR-0011, the README and the research report.

**Measured again on the 2015 history (2026-10-25/26):**

| Test | 33-company history | 2015 history (this ADR) |
|---|---|---|
| Walk-forward predictions per model | 6,094 | 115,547 (37 folds) |
| BASELINE / AUGMENTED Brier skill (AUC) | −0.006 (0.505) / −0.017 (0.498) | −0.001 (0.504) / −0.001 (0.502); augmented still worse, CI excludes zero |
| AI_BOOK_21 Brier skill (AUC) | −0.025 (0.512) | −0.003 (0.505) |
| Signal-health stock-days | 57,728 | 962,175 (629 companies, 11.8 years, 45 inputs, bar \|t\| 3.26) |
| `exec_departure_63d` (ADR-0011) | NOISE, t −0.51 | **INFORMATIVE**, mean monthly IC −0.0126, t −3.58, 135 months, right sign 63%, negative in 11 of 12 years |
| Insider inputs (ADR-0010) | NOISE | NOISE: `insider_informed_net_63d` t −0.33, `insider_exec_buyers_63d` t −0.43, `insider_net_63d` t −0.14 |
| TARIFF_SHOCK / RATE_SHOCK, 21 days (ADR-0009) | NOISE (543 / 104 firings) | NOISE (6,700 / 1,859 firings) |
| CEO_DEPARTURE / CFO_DEPARTURE, 21 days | 20 / 35 firings | 539 / 860 firings: 42.7% / 46.3% beat the ETF, mean −0.5% / −0.1%; NOISE after 93 tests (63 days: −1.2% / −0.9%) |
| INSIDER_CLUSTER_SELL, 21 days | NOISE | NEGATIVE: 6,875 firings, mean −0.3% (CI −0.5% to −0.1%) |
| AGREEMENT_TERMINATED, 21 days | NOISE | SUGGESTIVE: 628 firings, +0.8% (CI +0.1% to +2.0%) |

The walk-forward took 11 minutes; signal health 26, the playbook 27, the ablation's walk-forward part about 35 (jobs 265
to 267). *Found while measuring:* the worker's 2 GB limit killed the evaluation (job 263; peak 2.83 GB measured in a
one-off container) and 5 GB killed the ablation's lab part (job 267, sampled peak 4.65 GB): `docker-compose.yml` gives
the worker 5 GB, and the strategy panel builds its feature table one day at a time without the provenance list columns
(a list of a million row dicts was the peak).
