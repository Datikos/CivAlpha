# ADR-0003: Find corporate actions the price feed missed, from 8-K text, with a keyword rule and Jev

**Status:** Accepted (2026-10-06; phase 1 implemented 2026-10-06; phase 2 waits for a TypeSafe API key)
**Date:** 2026-10-06
**Deciders:** David Sakhelashvili (owner)

## Context

The total-return index (`returns.total_return_index`) is only as right as the corporate-action table. The price feed
(Tiingo) reports splits and cash dividends; it reported nothing when Corteva distributed Vylor one-for-one on
2026-10-01, so CTVA's 84% "loss" entered every feature and the sizing for four days until it was entered by hand
(`docs/research/2026-10-forecasting-polish.md` section 3). The integrity scan (`civalpha.integrity`) catches such a
break only when the one-day move exceeds 50%. A spin-off worth 10% to 30% of the parent passes it silently.

The announcement is public before the price breaks: Corteva's 8-K of 2026-09-15 stated the one-for-one distribution
two weeks ahead. Evidence from the stored data on 2026-10-06:

* 20,009 8-Ks are stored with their main document (`filing`, `source_document`, HTML, from 2020-10-05); 8,507 carry an
  item where a capital change is reported (1.01, 2.01, 3.03, 5.03, 7.01, 8.01). Median main document about 1,330
  tokens, 90th percentile about 3,000.
* 68 recorded actions since 2021-01-05 have 8-Ks in the 90 days before their ex-date (67 `SPLIT`, 1 `SPIN_OFF`). A
  keyword rule ("stock split", "reverse split", "spin-off", "separation and distribution", "pro rata distribution")
  finds at least one mentioning 8-K for 55 of them (81%). Misses include splits announced only in an exhibit or a
  proxy (TSLA 2022, CMG 2024, SHOP 2022).
* Several `SPLIT` rows are spin-offs the feed encoded as a fractional factor (MRK 1.048 on 2021-06-03, the Organon
  distribution; HON 1.061 on 2025-10-30, Solstice; FWONA 1.018 and 1.026 in 2023). The feed sometimes adjusts a
  spin-off and sometimes does not; nothing on the platform says which.

Jev (TypeSafe AI, model `jev-1.13.0`) answers typed questions about a text with probabilities: `noul` (yes/no),
`choice`, `score`. Documented facts (docs.typesafe.ai, 2026-10-06): $0.042 per million input tokens, output free; 32k
tokens of state; 80 requests/s; versions can be pinned; it reads dates as text, cannot count or compare numbers, and
loses accuracy when the state carries unrelated text; customer data is not used for training. No `TYPESAFE_API_KEY`
exists in this installation.

## Decision

1. **A detector reads every 8-K carrying item 1.01, 2.01, 3.03, 5.03, 7.01 or 8.01** (`civalpha/corpactions.py`). It
   strips the HTML, drops the cover page and the signature block, keeps the item sections, and caps the state at 6,000
   tokens. Only classification is asked of it: no ratios, no dates.
2. **Two methods, both stored, the keyword rule always.** `KEYWORD` (the regular expression above, free, deterministic)
   runs on every filing. `JEV` runs when `TYPESAFE_API_KEY` is set, pinned to `CIVALPHA_JEV_MODEL` (default
   `jev-1.13.0`), with two questions on the item text: a `choice` of kind (`spin_off_or_distribution`, `forward_split`,
   `reverse_split`, `special_dividend`, `merger_or_delisting`, `none`) and a `noul` "This filing announces or completes a
   change to the shares a holder owns: a split, a reverse split, a spin-off or a distribution of another company's
   shares." The response's versioned model id is stored with every answer.
3. **Results are candidates, never corporate actions.** Table `corporate_action_candidate` (migration V17): filing,
   company, accepted time, method, model version, kind, probability, the recorded corporate action it reconciles with
   (same company, `SPLIT`/`SPLIT_INFO`/`SPIN_OFF` with ex-date in [accepted - 5 days, accepted + 120 days]) and a review
   status (`UNREVIEWED`, `CONFIRMED`, `REJECTED`). Nothing is written to `corporate_action`: the value of a
   distribution (shares received x their first close) is entered by the owner, as for CTVA.
4. **Feed gaps are the output.** A candidate with kind other than `none`, probability at least 0.5 (Jev) or a keyword
   hit, and no reconciling action is a feed gap: listed by `python -m civalpha.corpactions gaps` and by the integrity
   scan.
5. **The evaluation is fixed now, before Jev is called once.** On the stored data:
   * *Event recall*: of the 68 known actions above, the share with at least one flagged 8-K in the 90 days before the
     ex-date. Keyword baseline: 55 of 68.
   * *Flag rate on quiet filings*: the share of 8-Ks flagged among those of companies with no recorded split or
     spin-off within 180 days either side. Every such flag is either a detector error or a feed gap; both lists are
     printed for review, and the review decides which.
   * CIs: bootstrap over companies, 2,000 draws, seed 20261006.
   * **Jev is adopted** (daily runs, a pipeline step) only if its event recall is at least the keyword rule's and its
     flag rate on quiet filings is not higher by more than 1 percentage point. Otherwise the keyword rule stays alone and
     the Jev code stays off.
6. **Cost bound.** Back-run: 8,507 filings x at most 6,000 tokens is at most 51M tokens, at most $2.15; expected about
   $0.55 at the median size. Daily: new 8-Ks only, a few dozen. Requests are batched one filing per call, 8 at a time.
7. **Failure is quiet.** Without a key, or on an API error, the keyword result stands and the error is logged; the
   detector never blocks ingestion, the forecasts or the book.
8. The detector changes nothing in the forecaster, the recorded book or the sizing. A sizing guard that uses feed
   gaps is a separate decision (backlog).

## Options considered

### Option A: keyword rule plus Jev, measured against each other, candidates reviewed by the owner (chosen)
Pros: the free rule ships today; Jev has to beat a stated baseline on a stated test before it runs daily; nothing
synthetic enters the price data. Cons: one more external service and key; the owner reviews a list.

### Option B: keyword rule only
Pros: no dependency, deterministic. Cons: misses actions phrased differently ("distribute all of the outstanding
shares of", "one share of X for every two shares"); 81% recall is the measured ceiling of the current list.

### Option C: Claude (the configured language model) as the classifier
Pros: already configured, better at long or indirect text. Cons: about 100x the price per token, slower, and the
Anthropic key had no credit on 2026-10-06; the platform's reviewer already uses it for ENTER candidates.

### Option D: auto-write corporate actions from the detector
Pros: no review step. Cons: the value of a distribution needs the new company's first close, which no classifier
extracts reliably (Jev's documented weakness on numbers and dates); a wrong action corrupts every downstream number
silently. Nothing synthetic enters the product.

## Trade-off analysis

| | A (chosen) | B | C | D |
|---|---|---|---|---|
| Recall on known actions | measured, must be >= B | 55/68 | unmeasured | depends |
| New dependency | TypeSafe key | none | none new | TypeSafe |
| Cost of the back-run | <= $2.15 | 0 | ~$100+ | <= $2.15 |
| Writes price-affecting data | no | no | no | yes |
| Ships without a key | yes (keyword half) | yes | no | no |

## Consequences

Easier: a spin-off the feed misses shows up as a feed gap the day its 8-K is accepted, not when the price breaks; the
integrity scan gains a cause column; the CTVA class of error has a detector with a measured recall.

Harder: an external key to provision and rotate; a review list to work through once (the back-run) and then a few
items a month; the candidate table grows by about two rows per relevant 8-K.

Revisit: if Jev fails the adoption test, retry with the next pinned version, or try the exhibit text (EX-99.1) for the
misses; if the flag rate on quiet filings is dominated by real feed gaps, the feed needs a second source for actions.

**Out of scope here:** using feed gaps in sizing (backlog), exhibit (EX-99.1) parsing, proxy statements, special
dividends' amounts, any change to the forecaster.

## Action items

Phase 1, without a key (2026-10-06):
- [ ] `civalpha/corpactions.py`: item-text extraction, keyword method, Jev client (`platform/jev.py`, pinned model,
      timeouts, quiet failure), reconciliation, evaluation (recall, flag rate, CIs), CLI `study` / `gaps` / `run`.
- [ ] Migration `V17__corporate_action_candidate.sql`.
- [ ] Settings: `TYPESAFE_API_KEY`, `CIVALPHA_JEV_MODEL`; `.env.example`, `docker-compose.yml`.
- [ ] Tests: extraction on real 8-K structure, keyword hits and misses, reconciliation window, Jev request shape and
      failure path with a stub transport, evaluation arithmetic.
- [ ] Run the keyword baseline on the stored data; record recall, flag rate and the feed-gap list here.
- [ ] Docs: README, `docs/api.md` if an endpoint is added, integrity section.

Phase 2, needs `TYPESAFE_API_KEY` (owner):
- [ ] Run Jev on the evaluation set; record recall, flag rate, CIs, cost; apply the adoption rule of decision 5.
- [ ] If adopted: pipeline step for new 8-Ks; the integrity scan shows feed gaps.

Phase 3:
- [ ] Review the feed-gap list; enter the confirmed distributions as `SPIN_OFF` actions.
