# ADR-0006: Live prices for display, polled by the worker, never read by the models

**Status:** Accepted (2026-10-07; the owner has a Tiingo Power plan and chose option 1)
**Date:** 2026-10-07
**Deciders:** David Sakhelashvili (owner)

## Context

Prices are daily closes only. `PriceSyncService.sync` (`platform/market/sync.py`) asks Tiingo's end-of-day endpoint once
per symbol, pausing 0.3 s, and runs when someone starts it or when `CIVALPHA_PIPELINE_CRON` is set (it is `-` in this
installation). On 2026-10-07 all 363 symbols were current to the close of 2026-10-06. The owner has the Tiingo Power plan
(10,000 requests an hour, 100,000 a day, 40 GB a month), so a full daily sync takes minutes, not the 8 hours of the
free plan.

Every model, the recorded book and the portfolio advice work on closes (`returns.HORIZON` = 21 trading days, close to
close). A price from the middle of the day must not enter them: it would break the point-in-time rule every forecast
is built on. What an intraday price adds is display: what a portfolio is worth now, the move since the close, and what
an advised trade costs at today's price.

Tiingo's IEX endpoint, measured 2026-10-07 at 12:55 New York with this installation's key:
`GET https://api.tiingo.com/iex/?tickers=msft,aapl,xlk,bdsx,brk-b` answered in 0.63 s with 1,562 bytes, timestamps
seconds old. Since IEX's policy change of 2025-02-01, `last`, `bidPrice`, `askPrice` and their timestamps are null
without an IEX exchange agreement; `tngoLast` (Tiingo's reference price: the last trade or the mid), `prevClose`
(consolidated), `open`, `high`, `low`, `volume` and `timestamp` are filled. A thinly traded stock's quote can be minutes
old (BDSX: 9 minutes, 490 shares on IEX). The same call without `tickers` returns every symbol and did not finish in
60 s.

The worker (`platform/worker.py`) runs one queued job at a time; a pipeline run takes about 20 minutes.

## Decision

1. **Daily closes on a schedule.** The owner sets `CIVALPHA_PIPELINE_CRON` (for example `0 30 17 * * MON-FRI`, 17:30
   New York). No code change; the price sync already resumes where it stopped.
2. **Live quotes are polled by a thread in the worker**, not by a queued job and not on a request: every
   `CIVALPHA_QUOTES_SECONDS` (default 300; `0` turns it off; values below 60 are raised to 60) between 09:25 and 16:10
   New York on weekdays, when the price provider is Tiingo. One request per 100 symbols (`tickers=`), 30 s timeout.
   Symbols: the active universe, the benchmark ETFs and every held symbol (so a holding outside the universe has a
   price too). About 4 requests per poll and 320 a day.
3. **Latest only.** Table `live_quote` (migration V20): one row per symbol (`price` = `tngoLast`, `prev_close`, `open`,
   `high`, `low`, `volume`, `quoted_at`, `fetched_at`, `provider`), replaced on every poll; symbols no longer tracked are
   deleted. No history is kept.
4. **The models never read it.** Only the portfolio read path does. Features, forecasts, the book, the advice rules and
   the outcome resolution keep reading `price_bar`.
5. **What the portfolio shows.** Each advice row gains `live` (`price`, `prevClose`, `change` = price / prevClose - 1,
   `valueUsd`, `quotedAt`, `stale`); the response gains `live` totals (`totalUsd` with cash, `changeUsd` against the
   previous close, `asOf`). A quote is stale when it is older than 15 minutes while the market is open. Advised share
   counts stay on the close the advice was made on; the page shows their cost at the live price beside them. The page
   reloads the advice every 60 s while quotes are fresh.
6. **Privacy.** Tiingo receives the list of tickers, held ones mixed in with the 363 tracked ones, never shares or
   values. A held symbol outside the universe is identifiable in that list; this is written in the README.

## Options considered

### Option A: a thread in the worker (chosen)
Pro: no new container; independent of the job queue; one place for outbound market calls. Con: shares the worker's
process; a worker restart pauses quotes for that time.

### Option B: a queued job every 5 minutes
Pro: uses the existing scheduler. Con: waits behind a 20-minute pipeline run; about 80 job rows a day in the job list.

### Option C: fetch on page load
Pro: requests only when someone looks. Con: an outbound call on the request path (latency, failure modes on the page),
and it tells Tiingo exactly which stocks a person holds.

### Option D: a separate `quotes` container
Pro: full isolation. Con: one more container and image to run for a loop of a few lines.

## Consequences

**Easier:** the portfolio page shows a value now and the move today without touching the models.

**Harder:** the worker has a second activity to watch in its log; reviewers hold one more rule: nothing but the
portfolio read path reads `live_quote`.

**Revisit:** the 300 s interval if quotes are needed faster; a market-holiday calendar (polls on a holiday return the
previous day's quotes, marked by their timestamp).

**Out of scope here:** intraday history and charts, streaming (WebSocket), alerts, the
IEX exchange agreement for `last` and bid/ask, showing live data to people outside the owner's household (BACKLOG #11).

## Amendment (2026-10-07, the owner: "there is no live prices")

The owner looked for live prices on the research pages, not only on My portfolio. `GET /api/quotes` serves the latest
quote of every active universe member and benchmark ETF (symbols held outside the universe are left out, so in
accounts mode no user learns what another holds); the Companies list shows a Price column (live price, move today,
the last close under it, sortable) and the company page header shows the live price against yesterday's close. Both
reload every 60 s in market hours (`core/live-quotes.service.ts`). Test
`test_public_quotes_leave_out_symbols_held_outside_the_universe`.

## Action items

1. [x] `db/migration/V20__live_quote.sql`.
2. [x] `platform/market/quotes.py`: Tiingo IEX client (batches, parsing), `QuoteService.poll`, market window; worker
   thread; setting `CIVALPHA_QUOTES_SECONDS`; `.env.example`, `docker-compose.yml`.
3. [x] `PortfolioService.advice`: `live` per row and totals.
4. [x] Tests: parsing with null `last`, batching, the window, replace-and-delete, stale flag, totals with and without
   quotes.
5. [x] Portfolio page: value now, move today, quote age, stale chip, 60 s refresh; guide entry.
6. [x] README, `docs/api.md`, BACKLOG.
   *Verified 2026-10-07 17:01 UTC (13:01 New York) on the live stack:* V20 applied; the worker's first poll stored 363
   quotes in 3 s; 3 were older than 15 minutes (ET and WBD at the previous close, BDSX 15 minutes old); the portfolio
   page shows the value now, the move today and the stale marks. Tests: `backend/tests/test_quotes.py` (12).
