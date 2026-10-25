# ADR-0011: Company events from 8-K items, stored apart from the filings

**Status:** Accepted (2026-10-25, owner: "1")
**Date:** 2026-10-25
**Deciders:** David Sakhelashvili (owner)

## Context

After ADR-0009 and ADR-0010 found no help in policy events or informed insider trades, the owner asked for more news
sources and "factor events". Of the sources checked on 2026-10-24, only one has history, exact timestamps and a link to
a company today: the 8-K, which a company must file within four business days of a listed event, with the event's
item number in the SEC's filing index.

* **What is stored.** `filing` holds 20,171 8-Ks of 351 companies since 2020-10-05, but only those carrying item 1.01,
  2.02, 7.01 or 8.01 (`sec/ingestion.py`, `RELEVANT_8K_ITEMS`). Every stored filing goes through the passage rules and
  `ExposureService.derive_for_filing`, which calls the language model when one is configured.
* **What the index lists** (the stored submissions documents, every company, 2020-10-05 to 2026-10-24, counted
  2026-10-25): 26,234 8-Ks. By item, listed / stored: 5.02 departure or appointment of officers and directors 5,006 /
  1,272; 1.01 material agreement 2,654 / 2,654; 1.02 termination of a material agreement 325 / 291; 2.01 completed
  acquisition or disposition 189 / 144; 2.05 exit or disposal costs (restructuring) 97 / 68; 2.06 material impairment
  32 / 27; 3.01 notice of delisting or failure to meet a listing rule 41 / 30; 4.01 change of auditor 13 / 2; 4.02
  non-reliance on previous financial statements 3 / 2; 1.05 material cybersecurity incident 10 / 1. The stored 5.02
  filings are the quarter that came with an earnings or press release: a biased sample.
* **Timestamps.** The index carries `acceptanceDateTime` for every filing; the earnings features already use it (an
  8-K is known at the first close at or after its acceptance).
* **What the literature says.** Announcements of CEO turnover move prices, mostly when the departure is forced, which a
  filing does not say; restructuring charges, impairments, listing notices, auditor changes and restatements are
  negative news on average. None of this has been measured on this universe.

## Decision

1. **A table of company events.** `company_event` (migration `V24__company_event.sql`): company, accession number, item,
   kind, `accepted_at` (from the index), evidence (at most 300 characters of the item's text, for kinds read from text),
   method (`ITEM` or the rule's version), source document. Unique per (accession number, kind). 8-Ks are not added to
   `filing`, so the passage rules, the exposure derivation (and its language-model calls), the corporate-action
   detector and the recorded ADR-0003 numbers do not change.
2. **Kinds from the item alone** (no document): `MATERIAL_AGREEMENT` (1.01), `AGREEMENT_TERMINATED` (1.02),
   `ACQUISITION_COMPLETED` (2.01), `BANKRUPTCY` (1.03), `RESTRUCTURING` (2.05), `IMPAIRMENT` (2.06), `LISTING_NOTICE`
   (3.01), `AUDITOR_CHANGE` (4.01), `NON_RELIANCE` (4.02), `CYBER_INCIDENT` (1.05). The last seven together are
   `DISTRESS` for the measurement, decided here, before any outcome is looked at.
3. **Kinds from the 5.02 text**, keyword rule `exec-1`, fixed here: the item 5.02 section (from its heading to the next
   item heading or the signature) is `CEO_DEPARTURE` when a departure word (resign, retire, step down, depart, terminate,
   separate, cease to serve, will not stand or seek re-election, transition from) appears within 300 characters of
   chief executive officer, CEO or principal executive officer (the item's own title, "Departure of Directors or
   Certain Officers", is not read); `CFO_DEPARTURE` the same with chief financial officer,
   CFO or principal financial officer; both can hold. A 5.02 with neither is stored as `OFFICER_DIRECTOR_CHANGE` (mostly
   directors and compensation) and is not measured. The rule's precision is checked once on 40 random `CEO_DEPARTURE`
   and 20 random `OFFICER_DIRECTOR_CHANGE` filings, labelled by reading them, and recorded here; the rule is not changed
   after the setups have run.
4. **Documents only where text is needed.** The 5.02 primary documents are fetched from EDGAR (or taken from storage
   when already stored) and kept as `SEC_FILING` documents; items of every other kind come from the stored index with
   no request.
5. **History once, then nightly.** Job `COMPANY_EVENTS_BACKFILL` (Admin page, `POST /api/admin/company-events/backfill`)
   reads every company's stored index and fetches the 5.02 documents, about 3,700 requests at the SEC limit (15 to 25
   minutes), outside the evening run. The pipeline's SEC step then adds the 8-Ks accepted in the last 30 days only.
6. **What is measured.** Setups (family `COMPANY_EVENT`, first close at or after acceptance): `CEO_DEPARTURE`,
   `CFO_DEPARTURE`, `DISTRESS_8K`, `MATERIAL_AGREEMENT`, `ACQUISITION_COMPLETED`, `AGREEMENT_TERMINATED`. Inputs for
   signal health and the ablation's additions ("plus company events"): `exec_departure_63d` (a CEO or CFO departure
   filed in the last 63 trading days, 0 or 1) and `distress_8k_63d` (distress 8-Ks in the last 63 trading days). An input
   counts only as INFORMATIVE, a setup only as SUPPORTED; an input whose events number fewer than 300 is not built and
   the count is recorded. The live models and the recorded book do not change under this ADR.
7. **The company page lists them.** A "Company events (8-K)" card with each event's kind, acceptance time, evidence and
   EDGAR link (`GET /api/companies/{symbol}/events`).

9. **Changed 2026-10-25, before any outcome was looked at: Claude reads item 5.02.** The keyword rule `exec-1` was right
   on 16 of 40 random `CEO_DEPARTURE` filings (40%, labelled by reading each section; the misses: an executive who
   "reports to" or "advises the CEO" 9, a new director who is a "former CEO" elsewhere 6, pay and clawback sections 5, a
   segment CEO 2, an executive chairman 2), below the 80% bar of decision 3. The owner chose option D with
   `claude-opus-5-5` through the Message Batches API (estimate: 4,967 sections, about 5.7M input and 1.3M output tokens,
   about $25 at batch prices; then about 70 sections a month, about $0.70). Method `exec-llm-1`: one schema-constrained
   answer per 5.02 section (`ceo_change`, `cfo_change`, `previously_reported`, `departure_type`, a quote), stored in
   `company_event.detail` (`V25__company_event_detail.sql`). A `CEO_DEPARTURE` (`CFO_DEPARTURE`) is a change of the
   company's own chief executive (financial) officer first reported in that filing, a successor's appointment included;
   a follow-up filing (`previously_reported`) is an `OFFICER_DIRECTOR_CHANGE`. The batch is tracked in `llm_batch` and
   collected by the next pipeline run or the job; new 5.02 filings are read one by one in the pipeline. Without a
   configured model the rule `exec-1` stays and the `method` column says so. Precision is checked again on a fresh
   sample before the setups run, with the same 80% bar.

## Options considered

### Option A: own table from the index, documents only for 5.02 (chosen)
Pros: the whole history from data already stored; one request per 5.02 filing; nothing else in the platform changes.
Cons: a second home for 8-K facts next to `filing` (the item codes are in both).

### Option B: add the event items to `RELEVANT_8K_ITEMS`
Pros: one table; the existing ingestion does the work. Cons: about 5,000 more filings through the passage and exposure
path (a language-model call each when configured), new exposure rows from 8-K text under ADR-0009's recorded result,
and the corporate-action detector's quiet-filing denominator changes ADR-0003's recorded recall numbers.

### Option C: item codes only, no 5.02 text
Pros: no requests at all. Cons: 5.02 is mostly directors and compensation; a CEO departure would drown in them.

### Option D: a language model to read every 5.02
Pros: better at "forced or planned". Cons: about 5,000 calls for the history and a cost per filing after; the keyword
rule is measured first, the model only if the rule's precision is poor.

## Trade-off analysis

| | A | B | C | D |
|---|---|---|---|---|
| Requests for the history | ~3,700 | ~6,000 + LLM | 0 | ~3,700 + ~5,000 LLM |
| Changes to recorded results | none | ADR-0003, ADR-0009 inputs | none | none |
| CEO/CFO departures told apart | rule | rule | no | model |
| Nightly cost | a few requests | a few + LLM | none | a few + LLM |

## Consequences

Easier: a company's material events are on its page with their timestamps; the playbook can say whether a CEO
departure or a restructuring was followed by underperformance on this universe.

Harder: six more setups raise the playbook's Bonferroni bar from 75 to 93 tests, and two more inputs raise signal
health's; the ablation counts one more trial. Two homes for 8-K items (`filing.items` for the four ingested items,
`company_event` for events).

Revisit: if the keyword rule's precision on CEO departures is below 80%, try the language model on 5.02 only (Option
D); if a setup is SUPPORTED, a live test before any model input.

**Out of scope here:** forced versus planned turnover, the 8-K exhibits (press releases), 8-K/A amendments, Tiingo
news (idea 2 of 2026-10-24) and the GDELT index (idea 3).

## Action items

Phase 1, the data:
- [x] `V24__company_event.sql`; `platform/company_events.py`: kinds from the index, the 5.02 rule, the fetch; tests
      (`backend/tests/test_company_events.py`).
- [x] Job `COMPANY_EVENTS_BACKFILL`, admin endpoint, Admin step; the pipeline's 30-day step.
- [x] Run the backfill; record the counts per kind and the rule's precision here.
- [x] Decision 9: `V25__company_event_detail.sql`, `platform/exec_changes.py` (batch submit and collect, one-by-one reads
      in the pipeline), job `COMPANY_EVENTS_CLASSIFY`, `POST /api/admin/company-events/classify`, Admin step; tests.
- [ ] Collect the batch; check the model's precision on a fresh sample (seed 77; the rule's check used seed 2026).

*Found while implementing:* the backfill (job 246, 2026-10-25, about 17 minutes) read 7,857 8-Ks of the lookback
window (from 2020-10-24), fetched 3,706 documents with no failure, and stored: OFFICER_DIRECTOR_CHANGE 3,960 (346
companies), MATERIAL_AGREEMENT 2,637 (315), CEO_DEPARTURE 657 (259, rule exec-1), CFO_DEPARTURE 444 (239, rule),
AGREEMENT_TERMINATED 321 (163), ACQUISITION_COMPLETED 186 (129), RESTRUCTURING 97 (50), LISTING_NOTICE 41 (28),
IMPAIRMENT 32 (23), AUDITOR_CHANGE 13 (13), CYBER_INCIDENT 10 (10), NON_RELIANCE 3 (3), BANKRUPTCY 0: the index counts
of the Context exactly. DISTRESS together: 196 8-Ks. The batch of decision 9 (job 247) holds 4,954 sections (13 of
4,967 stored 5.02 documents have no item text).

Phase 2, the measurement:
- [ ] `db.load_bundle` loads the events; panel matrices; the six setups; the two inputs in signal health and the
      ablation's additions (if at least 300 events each).
- [ ] Run the setup playbook, signal health and the ablation; record the verdict here.

Phase 3, the screen:
- [ ] `GET /api/companies/{symbol}/events`, the company-page card, guide entries, README, `docs/api.md`.
