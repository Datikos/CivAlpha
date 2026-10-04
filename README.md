# CivAlpha

CivAlpha collects US monetary-policy and trade/tariff events and links them to Nasdaq-listed companies using
evidence from SEC filings. It forecasts one target and shows the reasons for each forecast, the original
sources, the uncertainty, and how accurate the forecasts turned out to be.

**Target.** The probability that a stock's total return over the next **21 trading days** beats the total
return of its sector benchmark ETF, measured from the close of the as-of date `t` to the close of `t+21`.

This is research software. It has no brokerage connection and places no orders. It claims no
profitability unless the cost-adjusted walk-forward evidence supports it. It works on real data only.

## Quick start

Requires Docker with Compose v2. The stack uses about 0.8 GB of RAM while idle and needs no GPU.

1. Configure the sources in `.env`:
   ```bash
   cp .env.example .env
   ```
   * **SEC filings:** `SEC_USER_AGENT="Your Name you@example.com"`. The SEC requires you to identify yourself;
     no key is needed.
   * **Daily prices:** `CIVALPHA_PRICE_PROVIDER=tiingo` plus `TIINGO_API_KEY` (free key at tiingo.com).
     `CIVALPHA_PRICE_PROVIDER=yahoo` needs no key but uses an unofficial API, so personal research only.
   * **Optional:**
     * `FRED_API_KEY` (free) for macro series;
     * `EVENTS_FEDERAL_REGISTER_ENABLED=true` and `EVENTS_FED_RSS_ENABLED=true` for official trade and rate events;
     * `EVENTS_NEWS_FEEDS` for news discovery;
     * `CIVALPHA_LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`.
2. Start the stack:
   ```bash
   docker compose up -d --build    # postgres, api, worker, frontend
   open http://localhost:8088
   ```
3. On the **Universe** page, add the stocks to track. **Look up on SEC** fills in the CIK and name; then pick a
   sector, an industry and the sector benchmark ETF (for example `XLK` for technology).
4. On **Data & pipeline**, click **Run pipeline**. That one job:
   * downloads prices, dividends and splits for every stock and benchmark ETF;
   * ingests SEC filings (XBRL facts, passages, exposures);
   * fetches macro data and policy events;
   * evaluates the models, issues forecasts, runs the strategy lab and records the AI's decisions.

   Later runs, from the button or the optional schedule, only fetch what is new.

Other commands:

```bash
docker compose logs -f api worker   # follow logs
docker compose down                 # stop (data is kept in volumes)
docker compose down -v              # stop and delete the database and stored documents
```

## Architecture

```
            ┌──────────── nginx + Angular (frontend :8088) ────────────┐
            │  pages: forecasts, companies, filings, exposure, events, │
            │  strategies, AI decisions, time machine, accuracy, admin │
            └───────────────────────────┬──────────────────────────────┘
                                        │ /api
┌───────────────────────────────────────▼──────────────────────────────┐
│ api — Python (FastAPI): REST API + MCP endpoint (/mcp) for AI        │
│       assistants, admin token, queues jobs; applies migrations       │
└───────────────────────────────────────┬──────────────────────────────┘
                                        │ pipeline_job table (queue + log)
┌───────────────────────────────────────▼──────────────────────────────┐
│ worker — Python, same image: runs jobs one at a time + schedules     │
│  ingestion: SEC EDGAR client (UA + ≤10 rps) · XBRL /                 │
│  companyfacts parsers · passages · exposures · prices (CSV, Tiingo,  │
│  Yahoo) · FRED/ALFRED · Federal Register / Fed RSS / news · dedup    │
│  models: point-in-time features · logistic models · walk-forward     │
│  evaluation · strategy lab · time machine · optional LLM (Claude)    │
└───────────────────────────────────────┬──────────────────────────────┘
                                        │ SQL
                         ┌──────────────▼──────────────┐
                         │ PostgreSQL 16               │
                         └─────────────────────────────┘
  volume civdata:/data
    documents/  original sources (content-addressed)
    imports/    CSV drop folder (prices*.csv, corporate_actions*.csv)
    uploads/    CSV uploads waiting for the worker
```

One Python codebase (`backend/civalpha`) runs as two processes from one image: `api` answers requests and queues
work; `worker` runs the queued jobs (ingestion, training, backtests) so long steps never slow the API down. There is
no Kafka, Redis, graph database or Kubernetes. Forecasts and AI decisions are append-only (database triggers).

## Data model (PostgreSQL, `db/migration`)

| Area | Tables | Notes |
|---|---|---|
| Provenance | `source_document` | URL or accession number, publication time, ingestion time, version, SHA-256, and the stored file path. New bytes for the same locator create version n+1 linked to version n. |
| Identity | `company`, `ticker_history`, `cik_mapping`, `universe_membership` | All are time-ranged `[valid_from, valid_to)`. FB→META resolves to one company, and a ticker change seen in SEC data closes the old span. |
| Filings | `filing`, `xbrl_fact`, `filing_passage` | `accepted_at` (EDGAR acceptance time) is the availability time. Each fact keeps its accession, filed date and XBRL dimensions (for example `srt:StatementGeographicalAxis`). Each passage keeps its section, topic, character offsets and extractor version. |
| Market | `price_bar` (raw closes, versioned), `price_bar_revision`, `corporate_action` | Adjustments come only from splits and dividends on their ex-dates. |
| Macro | `macro_series`, `macro_observation` | ALFRED vintages (`realtime_start`/`realtime_end`). |
| Events | `policy_event`, `event_source`, `event_target`, `policy_actor`, `actor_record` | Every event links to stored evidence. `evidence_status` is `OFFICIAL` or `NEWS_ONLY`. Actor profiles hold documented actions and votes only. |
| Exposure | `company_exposure` | Each exposure records a target (country, product or interest rate), a channel and a share. `basis` is `DIRECTLY_REPORTED` or `ESTIMATED`, with a confidence and a method (`XBRL_DIMENSION`, `XBRL_RATIO`, `RULE_KEYWORD`, `SECTOR_MAP`, `LLM`). Each one links to a filing plus a passage or fact, and becomes available at `available_at` (the filing's acceptance time). |
| Models | `model_version`, `forecast`, `forecast_outcome`, `model_evaluation`, `backtest_prediction` | A database trigger rejects `UPDATE` and `DELETE` on `forecast`. Outcomes are stored in a separate table. |
| Strategies | `strategy_run`, `strategy_result`, `strategy_trade`, `strategy_decision`, `decision_explanation` | Backtest runs, decisions and explanations are written by the worker; decisions are append-only (trigger); language-model explanations live in their own table. |

How an event is linked to a company:
**event → target (country, sector, product or cost) → company exposure → supporting filing passage or XBRL fact**.

## Forecasting

* **Baseline model:** prices and filed fundamentals. Features are 1-, 3- and 6-month returns relative to the
  benchmark, relative volatility, year-over-year revenue growth, the change in gross margin, and long-term debt
  divided by assets.
* **Augmented model:** the baseline features plus:
  * `trade_shock`: Σ over recent official tariff events of sign × severity × decay × exposure weight.
  * `rate_shock`: Σ over recent rate decisions of −Δrate × decay × leverage relative to the universe.
  * `fedfunds_chg_x_lev`: FEDFUNDS as published at the cutoff.
* **Model:** L2 logistic regression on clipped, standardized features.
  * The explanation is each feature's coefficient × its standardized value (log-odds), and each factor links to
    its sources.
  * The interval is the 10th–90th percentile across 30 date-block bootstrap refits. It reflects estimation
    uncertainty only.
* **No look-ahead.** All inputs for as-of time `T` are read point-in-time (`backend/civalpha/pit.py`):
  * prices for dates ≤ T;
  * facts and exposures accepted ≤ T, with the latest acceptance winning, so an amendment counts only after it
    is accepted;
  * events officially published ≤ T;
  * macro vintages that were current on T;
  * universe membership on T.
* **Walk-forward evaluation:**
  * Test blocks of 63 trading days. Each model is trained only on samples whose 21-day label window closed
    before its block starts.
  * Reports Brier score, Brier skill, log loss, AUC, accuracy, reliability bins, and the augmented-minus-baseline
    Brier difference with a bootstrap CI.
  * Includes a long/short simulation entered at the next close, charged 10 bp per side per leg (configurable).
    The verdict says "Profitability claim: NOT supported" unless at least 36 periods have mean net return > 0 and
    t > 2.
* **Versioning:**
  * A forecast series is one (company, model, as-of date).
  * When new evidence arrives (for example `POST /api/events` with an official source), the affected companies
    are re-issued. A changed result becomes version n+1 with `supersedes_id`; an identical result is skipped.
  * `issue_mode` is `LIVE` or `REPLAY`. `REPLAY` means the forecast was published after its data cutoff, and the
    UI labels it.

## Strategy lab: AI entry/exit vs classic theories

**Data & pipeline → Run strategy backtest** (also the last step of every pipeline run) backtests these long-only
strategies on the same data, the same out-of-sample window and the same costs. The results are on the **Strategies** page.

| Family | Strategies |
|---|---|
| Benchmark | Equal-weight buy & hold (the reference every verdict compares against); sector ETF basket |
| Trend / momentum | 50/200-day golden cross (with and without a 10% trailing stop); 12-1 month momentum, top 5 monthly; Donchian 55/20 breakout |
| Mean reversion | RSI(2) pullback above the 200-day average; Bollinger band (20, 2σ) bounce; weekly 5-day reversal, bottom 5 |
| Fundamental / event | Quality & growth screen on as-filed XBRL data; post-earnings-announcement drift (earnings surprise ≥ 1, hold 60 days); value (top 5 earnings yield); gross profitability (top 5 gross profit / assets); dividend yield (top 5); stepping aside from tariff/rate shocks using SEC-filing exposures |
| AI | Gradient-boosted trees that combine every rule's indicator with the financial-report profile, event shocks and macro (with and without a 10% trailing stop); the same model on the financial-report profile alone (`AI_FUND`); the same model plus dividend signals (`AI_DIV`), compared with it on the same out-of-sample forecasts |

* **The AI decides.** The model estimates the probability that a stock beats its sector ETF over the next 10 trading days.
  It enters when p ≥ 0.55 and the stock ranks in the top 8, and exits when p < 0.48. It is retrained every 63 trading days,
  walk-forward, only on outcomes known before each refit.
* **Financial reports.** `backend/civalpha/fundamentals.py` turns the XBRL facts of every 10-Q/10-K into a profile:
  * **Growth:** revenue growth acceleration.
  * **Surprise:** earnings surprise and revenue surprise, standardized against the same quarter a year earlier. This
    is a seasonal random walk with no analyst estimates (Bernard & Thomas).
  * **Margins:** gross, operating and net margins over the last four quarters, and the change in operating margin.
  * **Spending and profitability:** R&D intensity, and gross profit ÷ assets.
  * **Balance sheet:** cash ÷ assets, liabilities ÷ assets, interest coverage.
  * **Valuation:** earnings and sales yield, using filed shares adjusted for later splits × the close.
  * **Recency:** days since the report.

  Each value is used only after the report's EDGAR acceptance time; an amendment counts from its own acceptance.
* **Same rules for everyone.** Every strategy is decided at the close and traded at the next close. Costs are 10 bp per
  side on the amount actually traded, and idle cash earns the realized fed funds rate. All strategies start in cash on
  the AI's first out-of-sample day.
* **An honest verdict.** A strategy counts as beating buy & hold only if all three hold:
  * it has at least 3 years out of sample;
  * the 95% block-bootstrap CI of its excess return is above zero;
  * its **Deflated Sharpe Ratio** is at least 0.95. The DSR corrects for having tried many strategies on the same history.
* **AI decisions page.** **Data & pipeline → AI decisions** stores today's ENTER / EXIT / HOLD / STAY OUT for every stock
  and shows the following for each one:
  * the factors that moved the probability;
  * which classic rules agree.

  With `CIVALPHA_LLM_PROVIDER=anthropic`, Claude writes a short plain-language explanation for ENTER/EXIT actions. The
  explanation is stored separately and never changes the decision.

Code: `backend/civalpha/strategies/` (`rules.py`, `ai.py`, `backtest.py`, `stats.py`). `backend/tests/test_strategies.py` covers:
* look-ahead, for every strategy;
* the AI's training purge;
* cost accounting;
* rule behavior on planted trends and mean reversion;
* pure noise never producing a "winner".

## Time machine: forecast from a past date and check it against the facts

On the **Time machine** page, pick a past date and click **Go back and forecast**. CivAlpha rebuilds what it would have
said at that close, using only the prices, filings, events and macro data known then. Models are retrained only on
outcomes that had resolved by that day. It produces:
* each stock's odds of beating its sector ETF over 5, 10, 21 and 63 trading days;
* the AI strategy's buy decisions;
* a 10–90% band for each stock's return.

It then compares these with what actually happened:
* hit rate and Brier score against the base rate;
* the AI's picks against all stocks;
* how many actual returns fell inside the band, against a naive band.

A chart shows the real price path inside the forecast band. One date is one draw, so use the walk-forward accuracy and the
strategy lab for evidence. The code is `backend/civalpha/timemachine.py`; `backend/tests/test_timemachine.py` checks that
changing every later price leaves the predictions untouched.

## Sources and credentials

| Source | Default | To enable | Notes |
|---|---|---|---|
| SEC EDGAR (submissions, companyfacts, filing documents, XBRL instances) | off until configured | `SEC_USER_AGENT="Your Name you@example.com"` | No key needed. The client refuses to start without a User-Agent that includes an e-mail and caps requests at ≤10/s (default 5), with gzip and backoff on 429/503. Fetches the last `SEC_LOOKBACK_YEARS` years. |
| Daily prices and corporate actions (automatic) | off | `CIVALPHA_PRICE_PROVIDER=yahoo` (no key; unofficial, personal research only) or `tiingo` + `TIINGO_API_KEY` (free key) | Runs with every pipeline run and on **Update prices**. Downloads are incremental, re-checking the last week of bars so corrections are versioned. Bars are stored under the ticker valid on each date. |
| Daily prices and corporate actions | CSV importer | Upload in Admin, or drop `prices*.csv` / `corporate_actions*.csv` into the `civdata` volume under `/data/imports` and run the pipeline | Columns: `symbol,date,open,high,low,close,volume` and `symbol,ex_date,action_type(SPLIT\|CASH_DIVIDEND),value,announced_at`. Use raw (unadjusted) prices. Vendor adapters implement `PriceProvider` (`backend/civalpha/platform/market/providers.py`). |
| FRED / ALFRED | off | `FRED_API_KEY` (free) | Fetches every vintage of `FEDFUNDS` and `CPIAUCSL`. |
| Federal Register API (official trade notices) | off | `EVENTS_FEDERAL_REGISTER_ENABLED=true` | No key needed. Event targets are extracted with deterministic rules. |
| Federal Reserve monetary press RSS (FOMC statements) | off | `EVENTS_FED_RSS_ENABLED=true` | Parses the rate change from the statement text. |
| News RSS (discovery only) | off | `EVENTS_NEWS_FEEDS=url1,url2` | Stays `NEWS_ONLY` and is excluded from features until an official document is linked. |
| LLM-assisted exposure extraction | off | `CIVALPHA_LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` (model `CIVALPHA_LLM_MODEL`, default `claude-opus-5-5`) | Optional. Output is schema-constrained and stored as `ESTIMATED` with confidence no higher than MEDIUM. Any failure means no hints, never a failed ingest. |

With the sources configured, run **Data & pipeline → Run pipeline** (`POST /api/admin/pipeline/run`).

**Licensing.** Being able to reach a market-data API does not give you the right to train models on its data or
display it publicly. Check your vendor licence before connecting a provider or publishing derived output. Keep
keys in `.env`, which git ignores, and never in source control.

## Managing the universe

The database is the source of truth for which stocks are tracked, and the **Universe** page is how you change it.

The **Universe** page (`/api/admin/universe`) supports these actions:

* **Add** a stock by ticker. **Look up on SEC** fills in the CIK and registrant name from SEC's
  `company_tickers.json`. You then pick a sector, an optional industry, a sector benchmark ETF and
  a *member since* date.
  * The *member since* date defaults to today. An earlier date puts the stock into backtests for periods when it
    wasn't actually selected, which biases results toward stocks already known to have done well.
  * Optionally, SEC ingest for the new stock starts right away.
* **Remove** ends membership on a date. **Restore** opens a new membership span. Past forecasts, backtests and
  point-in-time features still treat the stock as a member for the dates it was one.
* **Edit** changes name, sector, industry or benchmark. **Ticker…** records a symbol change from a date, and
  earlier prices stay under the old symbol.
* **Delete** is allowed only while no data (prices, filings, forecasts) is attached, for example after a mistyped
  addition. Otherwise use Remove.

A newly added stock gets forecasts once it has about six months of price history; **Update prices** downloads it.
If you choose a benchmark ETF that is new to the universe, its prices are downloaded too.

## AI assistants (MCP)

The API also speaks the [Model Context Protocol](https://modelcontextprotocol.io) at `http://localhost:8088/mcp`
(Streamable HTTP), so Claude Desktop, Claude Code, claude.ai or any MCP client can read the research data, manage
the universe and the pipeline, and ask where the evidence points. The server is `backend/civalpha/platform/mcp_server.py`.

* **Research tools (read-only, public):** `get_status`, `list_companies`, `get_company`, `get_financials`,
  `get_prices`, `get_dividends`, `list_filings`, `get_filing`, `get_exposures`, `list_events`, `get_event`,
  `get_current_forecasts`, `get_forecast_history`, `get_forecast`, `get_accuracy`, `get_strategies`, `get_strategy`,
  `get_decisions`, `list_time_machine_runs`, `get_time_machine_run` — the same shapes as the REST API.
* **`investment_candidates`:** the universe ranked by the AI strategy's latest decision and probability, with the
  current forecasts and intervals, the dividend profile, the accuracy record and the strategy-lab verdicts, plus the
  disclaimers. It hands the assistant the evidence and its uncertainty; it does not output a bare "buy" list.
* **Management tools (admin):** `get_universe`, `lookup_company`, `add_company`, `edit_company`, `remove_company`,
  `restore_company`, `change_ticker`, `delete_company` (needs `confirm`), `add_event`, and the jobs
  `run_pipeline`, `sync_prices`, `ingest_sec_filings`, `issue_forecasts`, `evaluate_models`, `resolve_outcomes`,
  `backtest_strategies`, `decide_strategy`, `run_time_machine`, with `list_jobs` / `get_job` to follow their logs.
  When `CIVALPHA_ADMIN_TOKEN` is set they need the same token as the REST API, sent as the `X-Admin-Token` header
  (or `Authorization: Bearer`) on the MCP connection.
* Resources `civalpha://about` (what is measured, verdict rule, disclaimers) and `civalpha://status`; prompt
  `investment_review` walks through accuracy → candidates → the evidence behind each name.

Connect from Claude Code:

```bash
claude mcp add --transport http civalpha http://localhost:8088/mcp
# with an admin token:
claude mcp add --transport http civalpha http://localhost:8088/mcp --header "X-Admin-Token: <token>"
```

For a client that launches servers over stdio, run `python -m civalpha.platform.mcp_server` from `backend/` with
the same environment as the `api` service (`DATABASE_URL` pointing at the published database, `CIVALPHA_DOCUMENTS_DIR`);
over stdio the operator who launches the process is trusted for the management tools.

The endpoint rejects requests whose `Host` is not localhost (DNS-rebinding protection). If the UI is published
on another interface, list the host names clients use in `CIVALPHA_MCP_ALLOWED_HOSTS` (and browser origins in
`CIVALPHA_MCP_ALLOWED_ORIGINS`), and set the admin token first.

## Security and scheduling

* **Admin token.** Set `CIVALPHA_ADMIN_TOKEN` to require a shared secret for `/api/admin/**` (any method) and
  for every non-GET `/api` request, such as adding an event. Clients send it as `X-Admin-Token: <token>` or
  `Authorization: Bearer <token>`. Read-only pages stay public. The **Data & pipeline** page asks for the token
  and keeps it for the browser tab only. Without a token everything is open, which is why the UI binds to
  `127.0.0.1` by default (`CIVALPHA_BIND`).
* **Schedule.** `CIVALPHA_PIPELINE_CRON` and `CIVALPHA_OUTCOMES_CRON` take cron expressions evaluated by the worker
  in `CIVALPHA_SCHEDULE_ZONE` (default `America/New_York`). Six fields mean seconds first
  (`sec min hour day month weekday`); five fields are standard cron. `-` turns a schedule off, which is the default.
  Example: `0 30 22 * * MON-FRI` runs the pipeline after the US close. Scheduled runs are logged like manual jobs
  and are skipped while a job of the same type is queued or running.

## Environment variables

All variables are listed with comments in `.env.example`. The main ones are `CIVALPHA_PORT` (8088),
`CIVALPHA_BIND`, `CIVALPHA_ADMIN_TOKEN`, `CIVALPHA_PIPELINE_CRON`, `CIVALPHA_OUTCOMES_CRON`, `POSTGRES_PASSWORD`,
`SEC_USER_AGENT`, `SEC_MAX_RPS`, `SEC_LOOKBACK_YEARS`, `CIVALPHA_PRICE_PROVIDER`, `TIINGO_API_KEY`, `FRED_API_KEY`,
`EVENTS_*`, `CIVALPHA_LLM_PROVIDER`, `CIVALPHA_LLM_MODEL`, `ANTHROPIC_API_KEY`, `CIVALPHA_MCP_ALLOWED_HOSTS` and
`CIVALPHA_MCP_ALLOWED_ORIGINS`. The stocks themselves are managed
on the Universe page; 20–50 symbols is a sensible size.

## Troubleshooting

* **"No benchmark ETF prices are loaded"**
  * Evaluation and forecasts compare each stock with its sector benchmark ETF (`XLK`, `XLY`, `XLP`, `XLC`, `XLV`,
    `XLI` for a typical Nasdaq universe).
  * Fix: run **Update prices** (with a price provider configured), or import a prices CSV that includes those ETFs.
  * The Data & pipeline page lists any benchmarks that are still missing.
* **"The universe is empty"**
  * Add companies on the Universe page before running the pipeline or importing prices.
* **"price sync failed" / "request limit reached"**
  * The provider refused the requests (for example Tiingo's free plan allows 50 requests an hour). The pipeline
    continues with the prices already stored; try again later.

## Tests

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt && pytest
```

The suite covers:
* the models: point-in-time joins, revisions, vintages, leakage, returns, walk-forward and costs, strategies,
  fundamentals and the time machine;
* the platform: SEC parsers (incl. XXE), passage rules, dedup, FOMC parsing, ticker resolution, price providers
  and sync;
* a Testcontainers PostgreSQL suite (needs Docker) for migrations, ticker changes, revised filings, price
  versioning, forecast and decision immutability, API smoke tests and the MCP server (tools, token rule, host check).

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`. It runs the
Python tests (and fails if the database tests could not run), the Angular production build, and a
`docker compose config` check.

## Database migrations

`db/migration/V<n>__<name>.sql` files are applied in order by `python -m civalpha.platform.migrate`, which the `api`
service runs on start. It uses Flyway's history table and checksums, so databases created before the move to
Python continue without any manual step. Never edit an applied migration; add the next `V<n+1>` file.

## Layout

```
backend/   Python: civalpha/ (models, strategies, time machine) and civalpha/platform/ (API, MCP server, worker, ingestion)
db/        migration/ (SQL schema migrations)
frontend/  Angular UI (served by nginx, proxies /api and /mcp)
docs/      api.md (REST contract and MCP tools)
```

## Known limitations / next steps

* **Prices.** The Yahoo and Tiingo adapters were built against their documented response formats but could not
  be exercised against the live services from the build environment. The trading calendar comes from benchmark
  bars. Yahoo's closes are split-adjusted: a newly reported split triggers a full re-download of that symbol, so
  stored bars stay consistent. Corrections are versioned: the replaced
  values move to `price_bar_revision`. Features always use the latest corrected prices, while forecasts
  already issued keep the feature values they were computed with.
* **SEC.**
  * Older submission pages (`filings.files`) are followed only when they overlap the lookback window.
  * The XBRL instance is located from the filing's `index.json`, falling back to the `*_htm.xml` naming
    convention.
  * The passage rules (`rules-v1`) are keyword-based. Precision should be measured on real 10-Ks before relying
    on `RULE_KEYWORD` exposures.
* **Events.** Event severity for auto-extracted trade notices defaults to 0.5. Calibrating severity and
  handling effective dates and exclusions needs more work.
* **Modeling.**
  * Only two simple linear models exist.
  * Next steps: sector-neutral cross-sectional ranking, purged k-fold with an embargo for hyperparameters, and
    proper significance testing across many universes.
  * Live, real-data accuracy can only build up over time.
* **Operations.**
  * Admin protection is a single shared token. Real multi-user access needs proper authentication and roles.
  * The scheduler runs inside the worker, so a missed run while the stack is down is not caught up.
