# ADR-0004: Advise on the owner's own holdings with risk rules and a graded model opinion, recorded and scored

**Status:** Accepted (2026-10-07, "start it now for the one portfolio"; implemented 2026-10-07)
**Date:** 2026-10-07
**Deciders:** David Sakhelashvili (owner)

## Context

The owner wants to enter the stocks he holds and have the platform say, per holding, what to do (buy more, sell part,
sell all, keep) and for how long. Today the platform advises nobody's real portfolio. It records one book, `AI_SIZED`
(`strategies/ai.py`, `BOOK_KEY`), an 8-slot model portfolio that starts from cash, and stores its daily ENTER / EXIT /
HOLD / STAY_OUT in the append-only `decision` table (`platform/decisions.py`).

What the platform can say about a stock, and how well, on 2026-10-07:

* **One forecast, one horizon.** The probability that the stock's total return over the next 21 trading days beats its
  sector ETF (`company.benchmark_symbol`; `returns.HORIZON` = 21, ADR-0001). The book's model is issued live per symbol
  and as-of date as kind `AI_BOOK_21` (`service.BOOK_KIND`, GBM on `GBM_AI_39`). Nothing is forecast beyond 21 trading
  days and nothing about the absolute return.
* **No shown skill.** Walk-forward AUC of `AI_BOOK_21` 0.513, Brier skill -0.025 (raw), -0.007 after isotonic
  calibration (ADR-0002). The pre-registered live test (`evaluation.LIVE_TEST`: at least 500 resolved live forecasts,
  AUC >= 0.53, Brier below the base-rate Brier) is `PENDING`; batch 1 is scored after 2026-11-03, verdict by
  2026-11-14 (`docs/BACKLOG.md` #3). Lab run 19 (21 days): `EW_SIZED` 0.71 Sharpe against `AI_SIZED` 0.72, so the
  volatility sizing travels, the AI's ranking adds nothing measurable.
* **Rules that need no forecast skill exist already.** The book's thresholds (`AiConfig.entry_p` 0.55, `exit_p` 0.48),
  the volatility size `ai.sized_weight` = min(`max_weight` 0.20, `vol_budget` 0.04 / annualized 21-day volatility), the
  integrity scan (`civalpha.integrity`, one-day moves beyond 50%), the reviewer brief's `LARGE_MOVE` 0.30 flag, the
  unreviewed rows of `corporate_action_candidate` (ADR-0003) and the next earnings date (`earnings_release`,
  `days_to_earnings_est`). CTVA showed on 2026-10-01 what an unflagged spin-off does to every number: an 84% "loss"
  for four days.
* **Outcomes can be resolved the same way as forecasts.** `service.resolve_outcomes` calls `returns.excess_label`
  (stock total return against the sector ETF over the horizon) for every unresolved live forecast.
* **Access.** `platform/app.py` `protected_request`: with `CIVALPHA_ADMIN_TOKEN` set, `/api/admin/**` and every
  non-GET `/api` call need the token; every GET is public. Compose binds to 127.0.0.1 by default. A holding list is
  personal financial data; under this rule it would be readable by anyone who reaches the port.
* **The universe.** 352 companies (public float >= $25B plus the original ~40). A holding outside it has no prices, no
  features and no forecast until the owner adds the company (`/api/admin/universe`, MCP `add_company`).
* The MCP server says "research software and not investment advice; it places no orders". That stays true.

## Decision

1. **The owner enters his holdings; the platform starts empty.** Migration `V18__portfolio.sql` adds `holding`
   (`id`, `symbol`, `company_id` null when the symbol is not in the universe, `shares` numeric > 0, `avg_cost_usd`
   numeric >= 0, `opened_on` date, `note`, `created_at`, `updated_at`; one row per symbol, unique on `symbol`) and
   `portfolio` (`id`, `name`, `cash_usd` numeric >= 0, `updated_at`; one row named `default` until ADR-0005). USD only,
   long only, one portfolio. No seed rows, no
   demo portfolio, no brokerage import.
2. **Every holding gets exactly one action per trading day**, from this list, decided in this order (first match wins):

   | Action | When | Layer |
   |---|---|---|
   | `NOT_COVERED` | the symbol is not in the universe or has no price on the as-of date | data |
   | `REVIEW` | an integrity break, a one-day move beyond `LARGE_MOVE` (0.30) in the last 45 calendar days with no recorded corporate action, or an `UNREVIEWED` corporate-action candidate in the last 120 days | data |
   | `TRIM` | weight above `max_weight` (0.20) of portfolio value, or above 1.5 x its volatility size `sized_weight` | risk |
   | `SELL` | `AI_BOOK_21` probability below `exit_p` (0.48) | model |
   | `ADD` | probability at or above `entry_p` (0.55) and weight below 0.67 x its volatility size | model |
   | `HOLD` | none of the above | — |

   Portfolio value = sum of shares x close on the as-of date + `cash_usd`. A `TRIM` names the target weight
   (min of 0.20 and the volatility size) and the shares to sell to reach it; an `ADD` names the shares to buy, limited
   by `cash_usd`. The 1.5x / 0.67x band and the 0.48 / 0.55 gap are the hysteresis that keeps the advice from flipping
   daily, the same idea as the book's.
3. **"How long" is answered with the horizon and the trigger, nothing else.** Every advice row carries `review_on` =
   as-of + 21 trading days (`HORIZON`) and the condition that would change it ("sell if p < 0.48", "trim if weight >
   0.20"). The advice is recomputed every trading day; a change against the previous day's action is shown first. The
   platform does not state holding periods beyond 21 trading days, price targets or stop prices.
4. **Each layer carries its evidence grade, and the model layer's grade follows the live test.** `data` and `risk`
   actions are risk control (sizing, concentration, broken data): they do not claim to raise returns and are labelled
   so. `model` actions carry the raw and calibrated probability, the rank among the universe, and the live-test verdict
   of `AI_BOOK_21`. While that verdict is not `PASS`, a `SELL` or `ADD` is shown as **"model opinion, unproven"**
   and the headline action is `HOLD` with the model opinion beside it. *(Open question 1 below: the owner may choose
   to make the model layer headline from day one.)*
5. **Buy ideas come from the book, not from a second rule.** The page lists the recorded book's current holdings and
   ENTER decisions that the owner does not hold, with the book's volatility size and the same evidence grade. No other
   screen produces buy ideas.
6. **Advice is recorded append-only and scored.** Table `holding_advice` (V18): as-of date, symbol, the holding as it
   was (shares, value, weight), action, layer, the rule that fired, target weight, shares to trade, `review_on`, model
   block (probability raw and calibrated, rank, thresholds, decision id, live-test verdict), code version; unique on
   (portfolio, as-of, `basis`, symbol), where `basis` hashes the holdings and cash; a trigger rejects UPDATE and DELETE like `decision`'s. Table `holding_advice_outcome` (V18) stores
   the resolved 21-day stock return, sector-ETF return and excess return per advice row, filled by the existing
   outcome resolution through `returns.excess_label`. The page shows, per action, the count and the mean excess return
   with a 95% bootstrap CI once 30 rows have resolved. Rows of one portfolio are correlated; the page calls this a track
   record, not a test.
7. **When advice is made.** As the last pipeline step after the decisions (issue forecasts, decide, advise), and by a
   "Refresh advice" button (`POST /api/portfolio/advice`), both idempotent per as-of date. Reading the page computes
   nothing.
8. **Holdings are private.** `protected_request` treats `/api/portfolio/**` as protected for every method, GET
   included. The MCP portfolio tools follow the same token rule. Holdings, values and advice are never put into a
   language-model prompt (the reviewer brief stays per symbol), never written to a log line at INFO (job logs carry
   counts; symbols travel in request bodies, so access logs record no holding), never exported.
   Without `CIVALPHA_ADMIN_TOKEN` the README warns that the portfolio is readable by anyone who reaches the port.
9. **Surfaces.** API: `GET/PUT /api/portfolio/holdings`, `POST /api/portfolio/holdings/remove` (symbols in the body,
   never in the path, so access logs do not record them), `PUT /api/portfolio/cash`,
   `GET /api/portfolio/advice?date=`, `POST /api/portfolio/advice`, `GET /api/portfolio/track-record`. MCP:
   `get_portfolio_advice`, `set_holding`, `remove_holding`. UI: a "Portfolio" page (holdings editor; one card per
   holding with the action chip, weight vs target bar, probability with the live-test grade, review date, the trigger;
   buy ideas; track-record chart) with its guide entry in `guide-content.ts`.

## Options considered

### Option A: mirror the book
Advice = the difference between the owner's holdings and `AI_SIZED`'s 8 positions.
Pro: no new rules; one source of truth. Con: tells him to sell everything the book does not hold, on a model with
AUC 0.513 and a pending live test; ignores his cash, his sizes and his reasons for holding.

### Option B: risk rules plus a graded model opinion, recorded and scored (chosen)
Pro: the parts that work without forecast skill (sizing, concentration, broken data) act today; the model speaks with
its evidence attached and earns the headline only by passing the pre-registered test; every piece of advice gets a
track record. Con: most days most holdings say `HOLD`; two layers to explain on the page.

### Option C: a language model reviews the portfolio
Pro: reads filings and news-like text, explains in prose. Con: no measured skill, not reproducible, sends personal
holdings off the machine, and the Anthropic key had no credit on 2026-10-06. The existing per-symbol reviewer already
covers the text side without the portfolio.

### Option D: mean-variance optimiser
Pro: the textbook answer for weights. Con: needs expected returns and covariances; the platform has a 21-day
beat-the-sector probability, not an expected return, so the optimiser would amplify noise into large weight changes.

### Option E: do nothing
Pro: no personal data stored, no risk of advice being followed. Con: the owner cannot use the platform on the
question he actually has.

## Trade-off analysis

| | A mirror book | B rules + graded model | C LLM review | D optimiser |
|---|---|---|---|---|
| Works without forecast skill | no | yes (risk layer) | no | no |
| Turnover it would advise | high (8 slots) | low (bands, hysteresis) | unknown | high |
| Uses the owner's sizes and cash | no | yes | partly | yes |
| Measurable track record | yes | yes | weak (prose) | yes |
| Personal data leaves the machine | no | no | yes | no |
| New code | S | M | M | L |

## Consequences

**Easier:** the owner sees, per holding, a concrete action, the shares to trade, the date it is next worth looking at,
and how much the platform's evidence supports it. Once the live test resolves, the model layer's grade changes by
itself, with no code change. Every advice row is auditable and scored.

**Harder:** a third append-only store to keep (decision, decision_review, holding_advice); the outcome resolver runs
over two tables. Reviewers hold two rules: nothing on the portfolio path reaches an LLM prompt or an INFO log, and
`/api/portfolio/**` stays protected on GET. Holdings outside the universe need the owner to add the company first.
The universe membership dates (BACKLOG #7) do not matter here: advice uses only live forecasts.

**Revisit:** the 1.5x / 0.67x bands and the 30-row threshold after three months of rows; the headline rule of
Decision 4 when `AI_BOOK_21`'s live test returns `PASS` or `FAIL` (2026-11-14); ADR-0003 thresholds (BACKLOG #2)
change `entry_p` / `exit_p` here too, since the advice reads them from `AiConfig`.

**Out of scope here (decided 2026-10-07):** taxes and tax lots (the owner's jurisdiction is not modelled), brokerage
import or sync, several portfolios or users, currencies other than USD, short positions, options, placing orders,
price targets and stop-losses, holding periods beyond 21 trading days, an LLM reading the portfolio.

## Action items

### Phase 1: store
1. [x] `db/migration/V18__portfolio.sql`: `portfolio`, `holding`, `holding_advice` (+ append-only trigger),
   `holding_advice_outcome`.
   *Found while implementing:* `portfolio` is a table with `id` and `name`, not a single row, and every table carries
   `portfolio_id`, so several portfolios (ADR-0005, multi-tenant) need no reshaping. The single portfolio is the row
   named `default`, created by the owner's first holding or cash entry. `holding_advice` is unique on (portfolio,
   as-of, `basis`, symbol): `basis` hashes the holdings and cash, so an edit during a day adds rows instead of being
   refused by the append-only trigger; the page and the track record read the day's newest basis.
2. [x] `platform/app.py`: `protected_request` covers `/api/portfolio/**` on every method; the frontend interceptor
   (`core/admin-token.ts`) sends the token on portfolio reads too. Tests `test_portfolio_is_protected_on_every_method`,
   `test_portfolio_api_needs_the_token_and_round_trips`.

### Phase 2: advice
3. [x] `platform/portfolio.py`: `advise_holding` (the rules, pure) and `PortfolioService` (holdings, `advise`,
   `advice`, `ideas`, `resolve_outcomes`, `track_record`).
   *Found while implementing:* the model inputs come from the recorded book's decision rows (`strategy_decision`, one
   per universe member per day, served by `/api/decisions`), not from the `AI_BOOK_21` forecast rows: the decision row
   carries the probability the book acts on, its rank, thresholds, calibrated probability and the 21-day volatility it
   sized with, so the advice and the book never disagree on a number. The grade is still `AI_BOOK_21`'s live-test
   verdict (same model, `GBM_AI_39`, issued live on the 21-day target); `read.live_test_of(kind)` now serves both the
   Accuracy page and the advice. The REVIEW rule reads raw closes and corporate actions of the held companies only
   (no full-bundle integrity scan on the request path) and the decision row's `vol21` against
   `integrity.VOL_THRESHOLD` (2.0). `review_on` is as-of + 21 weekdays: exchange holidays ahead are not stored, so the
   window can end a day or two later. The earnings-date note of the Context was not built: Decision 2 has no earnings
   rule, and the setup playbook grades the results-day setups mostly as noise.
4. [x] Tests in `backend/tests/test_portfolio.py` (18): `test_concentration_trims_to_the_volatility_size`,
   `test_over_risk_size_trims_below_the_cap`, `test_broken_series_reviews_before_risk_and_model`,
   `test_sell_is_shown_as_opinion_while_the_live_test_is_pending`, `test_add_is_limited_by_cash`,
   `test_hysteresis_band_holds_between_add_and_trim`, `test_not_covered_symbol_is_valued_at_cost`,
   `test_advice_idempotent_per_portfolio_state`, `test_advice_rows_are_append_only`,
   `test_removed_holding_leaves_the_page_but_not_the_record`, `test_buy_ideas_are_the_books_positions_not_held`, and others.
5. [x] Advice after the decisions in the pipeline (`platform/pipeline.py`, its own step guard) and in the decisions job
   (`platform/tasks.py`); outcome resolution of `holding_advice` in the pipeline's outcomes step, loading the bundle
   only when a row is pending; test `test_advice_outcome_resolves_after_21_trading_days`. Logs carry counts only.

### Phase 3: surfaces
6. [x] API in `platform/api/portfolio.py` (Decision 9); `docs/api.md` section "Portfolio".
7. [x] MCP tools `get_portfolio_advice`, `refresh_portfolio_advice`, `set_holding`, `remove_holding`.
   *Found while implementing:* the refresh is its own action tool so `get_portfolio_advice` stays read-only.
8. [x] `frontend/src/app/pages/portfolio.ts` (`/portfolio`, menu "My portfolio" under Strategy, icon `wallet`):
   tiles, the live-test banner, one card per holding, buy ideas, holdings editor with cash, track-record chart
   (`app-dot-whisker`); guide entry `page-portfolio`.

### Phase 4: docs
9. [x] README: decision list, data-model row, section "My portfolio", MCP tools, the admin-token rule and the privacy
   warning, the tests list, the limitation note.

## Open questions for the owner

Answered 2026-10-07 by "start it now" on the recommendation: 1 (a), the headline stays `HOLD` until the live test
passes; 2 (a), reading holdings needs the admin token. Several portfolios are the next decision (ADR-0005).
