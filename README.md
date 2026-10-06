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
   sector, an industry and the sector benchmark ETF (for example `XLK` for technology). Optional **tags** are your own
   categories (a theme such as `AI`, a watchlist such as `core`) for filtering the **Companies** page; the models never see them.
   **Expand the universe** adds many at once: every company listed on Nasdaq or NYSE with a public float above a threshold,
   from SEC data alone, each with a sector suggested from its SIC code. Breadth matters: an edge that is invisible on 40
   stocks can be measurable on 400. Mind the price provider's quota (Tiingo's free plan fetches about 50 symbols an hour;
   each sync continues where the last one stopped) and that every pipeline run grows with the universe.
4. On **Data & pipeline**, click **Run pipeline**. That one job:
   * downloads prices, dividends and splits for every stock and benchmark ETF;
   * ingests SEC filings (XBRL facts, passages, exposures);
   * fetches macro data and policy events;
   * evaluates the models, issues forecasts, runs the strategy lab and records the AI's decisions.

   Later runs, from the button or the optional schedule, only fetch what is new.
5. Open **Guide** in the sidebar (`/guide`) for a tour of every page, a live setup checklist, an explanation of
   each metric and badge, and a glossary. Every (i) icon on the platform opens a short explanation that links back
   into the guide.

The **Dashboard** (`/`) summarizes what changed since the last run: leans that flipped between the two newest as-of
dates, forecast windows that closed in the last ten days with their scores, the AI's entries and exits, and the
21-trading-day movers against their sector ETFs. Tables on **Companies** and the **Strategy lab** sort by any column
(remembered per browser) and let you tick up to three rows to open a side-by-side **Compare** view. A forecast page
shows what changed against any earlier version, factor by factor; an event page shows which forecasts it re-issued
and how every exposed company's forecast moved across the event date; the **Accuracy** page charts Brier score and
hit rate per month as live forecasts resolve.

Other commands:

```bash
docker compose logs -f api worker   # follow logs
docker compose down                 # stop (data is kept in volumes)
docker compose down -v              # stop and delete the database and stored documents
```

## Decisions

Design decisions with alternatives are written down before they are built, in `docs/adr/`; open items are ordered in
`docs/BACKLOG.md`.

| ADR | Title | Status |
|---|---|---|
| ADR-0001 | One forecast horizon for the live models, the recorded book and the lab (21 trading days) | Accepted, implemented 2026-10-06 |
| ADR-0002 | Calibrate the book's probabilities before judging its thresholds | Accepted, implemented 2026-10-06 |

## Architecture

```
            ┌──────────── nginx + Angular (frontend :8088) ────────────┐
            │  pages: dashboard, forecasts, companies, compare, filings,│
            │  exposure, events, strategies, AI decisions, time machine,│
            │  doublers, admin, guide (manual, glossary, checklist)    │
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

* **Baseline model:** prices, filed fundamentals and insider trades. Features are 1-, 3- and 6-month returns relative
  to the benchmark, relative volatility, year-over-year revenue growth, the change in gross margin, long-term debt
  divided by assets, insiders' net open-market buying over 63 trading days as a share of market cap, the market's
  reaction to the last earnings announcement, and the last release's guidance tone.
* **Augmented model:** the baseline features plus:
  * `trade_shock`: Σ over recent official tariff events of sign × severity × decay × exposure weight.
  * `rate_shock`: Σ over recent rate decisions of −Δrate × decay × leverage relative to the universe.
  * `fedfunds_chg_x_lev`: FEDFUNDS as published at the cutoff.
* **Book model (`AI_BOOK_21`, since 2026-10-06):** the recorded book's input list (`GBM_AI_39`: the baseline features
  plus the technical indicators, the filed report profile, insider counts and the earnings calendar) and its gradient-
  boosted algorithm, issued live on the same 21-day target so the book's model has a live record next to the two
  logistic models. Raw, uncalibrated point probabilities with no interval; the walk-forward and the calibration study
  (`docs/research/2026-10-forecasting-polish.md`) say what to expect of them. Pre-registered as batch 2 of the live
  test (`docs/research/live-test-2026-10-batch2.md`). This is not the label the book trades (10 days from the next close).
* **Model:** L2 logistic regression on clipped, standardized features (the two logistic models).
  * The explanation is each feature's coefficient × its standardized value (log-odds), and each factor links to
    its sources. A missing input is filled with the training median before standardizing, and the factor is marked
    `imputed` so a contribution is never shown next to an empty value. (The strategy lab's gradient-boosted model does
    not fill missing values: its trees route them on a learned branch, and its explanation marks such a factor `imputed`
    with that note, since the contribution measures the effect of having no value.)
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
    Brier difference with a bootstrap CI; and, per model, the same numbers after an isotonic map fitted on earlier folds
    (`metrics.<model>.calibrated`, ADR-0002, with the raw numbers on the same rows and both reliability diagrams): how much of a model's Brier score is overconfidence rather than information. The Accuracy page draws it: the Brier change per model with its interval, the spread and the confident-decile hit rate raw against calibrated, and one model's reliability diagram before and after. Since 2026-10-06 every model's Brier skill and AUC also carry a 95% CI from a
    bootstrap over 21-day blocks of as-of dates (`metrics.<kind>.ci`); the Accuracy page draws all evaluated models
    (the two live logistic models and the recorded book's gradient-boosted model on both of its labels) as
    dot-and-whisker charts for Brier skill, AUC and the top-10% net excess, each against its bar.
  * Includes a long/short simulation entered at the next close, charged 10 bp per side per leg (configurable).
    The verdict says "Profitability claim: NOT supported" unless at least 36 periods have mean net return > 0 and
    t > 2.
  * Includes an abstention curve: the forecasts ranked by confidence (distance from 50%), and for the top 5%, 10%,
    20%, 30%, 50% and 100% the accuracy and the mean excess return per position after costs with a date-block
    bootstrap CI. A trader does not act on every stock every day; this shows whether the model's surest calls are
    worth more than the rest. The verdict quotes the 10% row.
* **Versioning:**
  * A forecast series is one (company, model, as-of date).
  * When new evidence arrives (for example `POST /api/events` with an official source), the affected companies
    are re-issued. A changed result becomes version n+1 with `supersedes_id`; an identical result is skipped.
  * `issue_mode` is `LIVE` or `REPLAY`. `REPLAY` means the forecast was published after its data cutoff, and the
    UI labels it.

## Earnings announcements (8-K Item 2.02)

Results become public with an 8-K, not with the 10-Q that follows weeks later, so the platform reads the 8-K's EDGAR
acceptance time as the event: a release before the open trades that day, one after the close trades the next day, and
that session's excess return over the sector ETF is the market's reaction. The press-release exhibit (EX-99.1, found
through the filing's index headers) is stored and classified by keyword rules into a guidance tone (raised, lowered,
maintained, outlook given, none) with the matched sentence as evidence; it is an ESTIMATED value. From past
announcements the platform also estimates the next one: a year after the announcement that followed the same
announcement last year. The session is decided in New York time (a 16:15 release trades the next day in summer and
winter alike), unlike the platform's fixed 21:00 UTC as-of cutoff, which treats a summer after-close filing as known
at that day's close; see the limitations.

The forecasting models get the last reaction (zero after 63 trading days) and the last guidance tone; the AI strategy
also sees trading days since the last announcement and until the estimated next one; the playbook tests results-day
jumps and drops, guidance raised or lowered, and "earnings due within a week"; every company page shows the record.
Code: `backend/civalpha/earnings.py` (point-in-time logic), `backend/civalpha/platform/earnings.py` (ingestion and rules);
`backend/tests/test_earnings.py`.

## Insider transactions (SEC Forms 4)

What officers, directors and 10% owners buy and sell in their own company is public within two business days on Form 4.
The pipeline loads them from two SEC sources: the quarterly insider-transactions data sets for the lookback window (one
zip per quarter, every filer at once) and each company's Form 4 XML filings newer than the latest data set, so the signal
does not lag a quarter. Only non-derivative transactions are stored; open-market purchases (P) and sales (S) are the
signal, grants, exercises, tax withholding and gifts are kept for the record. A filing becomes available at the end of
its filing day in New York, which is never earlier than the true acceptance time.

They feed three places: the forecasting models get `insider_net_63d` (net open-market buying over 63 trading days as a
share of market cap; zero when nothing was filed, because silence is information); the AI strategy also sees the number
of distinct insiders buying and selling in the last 21 trading days; the setup playbook tests an insider purchase, cluster
buying (two or more insiders) and cluster selling (three or more); and every company page shows the trades. Code:
`backend/civalpha/platform/insiders.py`; `backend/tests/test_platform_insiders.py`.

## Signal health: which inputs carry information, and which are fading

The **Signal health** page (`/signals`, also the last step of every pipeline run) computes, for every feature the AI
strategy sees, the information coefficient: on each day the Spearman rank correlation across the tracked stocks
between the feature and the next 21 trading days' excess return over the sector ETF, averaged per month (cross-sectional
by day, so a market-wide move cannot pose as a signal). Each input gets its mean IC with an autocorrelation-robust
t-statistic over months, the IC information ratio (mean over standard deviation), the share of months with the
expected sign, the last 12 months against the earlier ones, and a grade corrected for the number of features tested.
An input that carried information over the whole history but lost its sign in the last 12 months is flagged
DECAYING: edges decay as others find them, and this is how the platform notices. Code:
`backend/civalpha/strategies/signals.py`; `backend/tests/test_signals.py`.

## Calibration study (research only)

`python -m civalpha.calibration [evaluation_id]` (from `backend/` with its virtualenv, or inside the api container) takes
the stored walk-forward predictions of one evaluation and maps each fold's probabilities through an isotonic curve fitted
on the earlier folds only. It prints, per model, the reliability bins, Brier, Brier skill, AUC, expected calibration
error and the most confident decile's hit rate and net excess before and after, with a block-bootstrap CI of the Brier
difference. The first three folds have no map and are left out on both sides. It changes nothing: the live models are
not calibrated. The first run (2026-10-06) is in `docs/research/2026-10-forecasting-polish.md`.

## Feature fragility: how much the numbers move when an input group goes

**MCP `run_feature_ablation` / `POST /api/admin/ablation/study`** takes the recorded book's input list (`GBM_AI_39`) and
scores it walk-forward six more times: without the price/technical group, without the report profile, without the
insider inputs, without the earnings inputs, and with the dividend or the policy-event features added. Each variant gets
Brier skill and AUC with 95% CIs (bootstrap over 21-day blocks of as-of dates) on the 21-day forecast target and on the
book's own label if it ever differs from the target again (ADR-0001), the way feature-set decisions are meant to be made. The lab Sharpe of `AI_GBM` and
`AI_SIZED` on the same probabilities is a secondary column marked "not a skill metric". Every variant is a trial and
lands in the trial registry, so the deflated Sharpe knows about it. Results: `GET /api/ablation`, MCP
`get_feature_ablation`, and the **Feature fragility** page (`/ablation`, with a "Run the study" button): Brier skill and
AUC per input list as dot-and-whisker charts on both labels against the base-rate and no-skill lines, the lab Sharpe and
max drawdown of the same probabilities beside them under a "not a skill metric" badge, the variants and the data tables.
`docs/research/2026-10-forecasting-polish.md` has the first run.

## Setup playbook: what a trader waits for, with base rates

A discretionary trader does not forecast every stock every day; they wait for a situation. The **Playbook** page
(`/playbook`, also the last step of every pipeline run) scores the situations the platform can recognise at the close
from data known then, each on the excess return over the sector ETF that followed within 5, 21 and 63 trading days:

| Family | Setups |
|---|---|
| Insider | an insider's open-market purchase becoming public; cluster buying (two or more insiders in 21 trading days); cluster selling (three or more) |
| Earnings (announcement) | results day with the stock up or down 5% or more against its ETF; guidance raised or lowered (keyword estimate); earnings due within a week (estimated date) |
| Earnings | an earnings beat or miss becoming public (standardized surprise ≥ 1 or ≤ −1 on the acceptance day); a revenue beat |
| Dividend | a regular dividend raised or cut against a year earlier (on the ex-date) |
| Trend | first close at a new 52-week high; golden cross; death cross |
| Reversal | first close at a new 52-week low; RSI(2) oversold above the 200-day average; a −20% month |
| Volume | volume at 2× its 20-day average on a ±3% day |
| Event | an official tariff or rate shock first hitting an exposed or leveraged company (the model's shock features) |

Every setup fires once per episode and is reported with how often it fired, the hit rate against the base rate of all
stock-days, the mean and median excess with a block-bootstrap interval, the payoff asymmetry (average win over average
loss), and a verdict: SUPPORTED only when the mean excess clears the Bonferroni-corrected bar for the 45 setup-horizon
pairs tested, SUGGESTIVE when only the plain 95% interval is above zero. The page also lists which setups fired in the
last five sessions on which stocks: the daily scan. Code: `backend/civalpha/strategies/setups.py`;
`backend/tests/test_setups.py` checks look-ahead per setup, edge triggering and that noise supports nothing.

## Strategy lab: AI entry/exit vs classic theories

**Data & pipeline → Run strategy backtest** (also the last step of every pipeline run) backtests these long-only
strategies on the same data, the same out-of-sample window and the same costs. The results are on the **Strategies** page.

| Family | Strategies |
|---|---|
| Benchmark | Equal-weight buy & hold (the reference every verdict compares against); sector ETF basket; the whole universe under the recorded book's volatility sizing (`EW_SIZED`: 0.04 / 21-day volatility, 20% cap, no leverage, so inverse-volatility weighting, fully invested), to show what sizing earns without any forecast |
| Trend / momentum | 50/200-day golden cross (with and without a 10% trailing stop); 12-1 month momentum, top 5 monthly, equal-weight and under the book's volatility sizing (`MOM_12_1_SIZED`); Donchian 55/20 breakout |
| Mean reversion | RSI(2) pullback above the 200-day average; Bollinger band (20, 2σ) bounce; weekly 5-day reversal, bottom 5 |
| Speculative | Doubler screen: volatile, small, cheap stock on a breakout or volume spike; hold 63 days, 50% stop |
| Fundamental / event | Quality & growth screen on as-filed XBRL data; post-earnings-announcement drift (earnings surprise ≥ 1, hold 60 days); value (top 5 earnings yield); gross profitability (top 5 gross profit / assets); dividend yield (top 5); stepping aside from tariff/rate shocks using SEC-filing exposures |
| AI | Gradient-boosted trees that combine every rule's indicator with the financial-report profile, event shocks and macro (with and without a 10% trailing stop); the same model on the financial-report profile alone (`AI_FUND`); the same model plus dividend signals (`AI_DIV`), compared with it on the same out-of-sample forecasts |
| AI, decision layer | The same probabilities as `AI_GBM`, acted on differently: `AI_CONF` abstains unless p ≥ 0.60 (exit below 0.50); `AI_SIZED` keeps the standard entries and exits but sizes each position by volatility (0.04 / annualized 21-day volatility, at most 20%, no leverage); `AI_GBM_CAL` and `AI_SIZED_CAL` apply the same thresholds to probabilities calibrated fold by fold on earlier folds (ADR-0002), to show how often honest probabilities clear them |

* **The AI decides.** The model estimates the probability that a stock beats its sector ETF over the next 21 trading days,
  the platform's forecast target (ADR-0001; it trained on a 10-day label until 2026-10-06).
  It enters when p ≥ 0.55 and the stock ranks in the top 8, and exits when p < 0.48. It is retrained every 63 trading days,
  walk-forward, only on outcomes known before each refit.
* **The decision layer.** Traders earn most of their keep between the forecast and the position: whether to act at all,
  how much to buy, when to leave. The lab tests each piece on the same probabilities. `AI_CONF` raises the bar to act
  (abstention). `AI_SIZED` keeps the trades and sizes them by volatility (volatility targeting). The run also records
  the AI's **abstention curve**: its out-of-sample forecasts ranked by probability, and what buying only the top 5% to
  100% earned per position after costs, with a bootstrap CI. The AI decisions page shows each stock's sized weight and
  whether it clears the confident bar.
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
    The number it deflates for is the size of the **trial registry** (`trial_registry`, `civalpha/strategies/registry.py`):
    one row per strategy variant and feature-set variant ever backtested (`AI_GBM@GBM_AI_39`, `AI_GBM@GBM_AI_42`, ...),
    with its date, Sharpe, excess-return CI and the git commit (`docker compose build --build-arg GIT_COMMIT=$(git rev-parse HEAD) api`
    puts the commit into the image; the `GIT_COMMIT=... docker compose build` form did not reach the build on this machine). Every lab run registers its candidates; a variant that was tried once and dropped
    keeps counting. The registry is a floor: trials that were never stored cannot be counted. `python -m
    civalpha.strategies.registry` prints it.
* **AI decisions page.** **Data & pipeline → AI decisions** stores today's ENTER / EXIT / HOLD / STAY OUT for every stock
  and shows the following for each one:
  * the factors that moved the probability;
  * which classic rules agree.

  With `CIVALPHA_LLM_PROVIDER=anthropic`, Claude writes a short plain-language explanation for ENTER/EXIT actions. The
  explanation is stored separately and never changes the decision.

  Every decision also carries `probabilityCalibrated` and `model.calibration`: the raw probability mapped through an
  isotonic curve fitted on the latest walk-forward's out-of-sample `AI_BOOK_21` rows, i.e. what that probability has
  meant so far (ADR-0002). The action is taken on the raw probability.

  The book recorded here is `AI_SIZED`: fixed entry (p ≥ 0.55, top 8) and exit (p < 0.48) thresholds, each position sized
  as 0.04 / annualized 21-day volatility, capped at 20%, no leverage. The model behind it leaves out the policy-event
  features (tariff and rate shocks, fed funds × leverage) since the lab showed the book does better without them. The
  Strategy lab backtests it next to the standard rule (`AI_GBM`), a replacement rule that lets a stronger candidate take
  the weakest holding's slot (`AI_RANK`, `AI_RANK_VOL`, `AI_RANK_SIZED` with a conviction tilt) and the model with the
  event features added back (`AI_WITH_EVENTS`), so the price of each decision-layer idea stays visible.

  Claude can also act as a reviewer of the entry candidates (`CIVALPHA_LLM_REVIEW`): it reads a brief of what the
  platform knows about the stock (factors, recent prices and corporate actions, results announcements, insider activity,
  key filed facts) and records AGREE / CAUTION / DISAGREE with a rationale and flags. `advisory` (default) logs the review
  and leaves the decision alone; `veto` stores a DISAGREE as STAY_OUT; `off` disables it. The brief is stored with every
  review so the reviewer can be scored once outcomes resolve.

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

## Doubler study: how often a stock doubles in a short period

**Doubler study** (also the last step of every pipeline run; `POST /api/admin/doublers/study`) asks a blunt question of
the stored history: for every tracked stock and trading day, bought at the next close, did the stock reach +100% at any
close within 21, 42 or 63 trading days, and did it fall to −50%? It reports:

* the base rate (hits per stock-day) over the whole history and by year, with every episode (the first signal day,
  the entry and the day the close first reached 2×);
* what the doubling stock-days looked like beforehand: median volatility, breakout distance, volume, market cap, price
  and drawdown versus all stock-days, and the hit rate by quintile of each;
* a point-in-time screen for that profile (volatile, small and cheap, on a breakout or volume spike): its hit rate and
  loss rate with block-bootstrap intervals, next to a control group of the same volatile stock-days without a trigger,
  and the lift over chance; the verdict says SUPPORTED only when the interval lies above the base rate;
* which tracked stocks the screen flags at the latest close.

The same screen trades in the strategy lab as `DOUBLER_SCREEN` (family Speculative: hold 63 days, 50% stop), so its
cost after fees shows next to the other rules. The code is `backend/civalpha/strategies/doublers.py`;
`backend/tests/test_doublers.py` checks that changing every later price leaves the screen untouched. A doubling is
rare, a halving is as common on the same stock-days, and a universe without delisted names overstates the base rate:
the page says so.

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
| LLM review of AI entries | advisory | `CIVALPHA_LLM_REVIEW=off|advisory|veto` (needs the LLM above) | A schema-constrained second opinion on each ENTER, stored with the brief it saw. `veto` turns a DISAGREE into STAY_OUT; any failure means no review, never a missed decision. |

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
(Streamable HTTP), so Claude Code, Claude Desktop or any MCP client can read the research data, start the pipeline's
jobs and ask where the evidence points. The server is `backend/civalpha/platform/mcp_server.py`; it runs inside the
`api` service and calls the same code as the REST endpoints.

* **Research tools (read-only, public):** `get_status`, `list_companies`, `get_company`, `get_financials`,
  `get_prices`, `get_dividends`, `list_filings`, `get_filing`, `get_exposures`, `list_events`, `get_event`,
  `get_current_forecasts`, `get_forecast_history`, `get_forecast`, `get_accuracy`, `get_strategies`, `get_strategy`,
  `get_decisions`, `list_time_machine_runs`, `get_time_machine_run`. Answers are condensed for a language model
  (for example price summaries instead of every daily bar).
* **`investment_candidates`:** every tracked stock with its latest forecast probabilities, the AI strategy's decision,
  the classic rules that hold it and its dividend status, plus the evidence on how far to trust that (model accuracy
  verdict, strategy-lab verdicts) and the disclaimers. A screening aid, not a buy list.
* **Job tools:** `run_pipeline`, `update_prices`, `ingest_sec_filings`, `evaluate_models`, `issue_forecasts`,
  `run_strategy_backtest`, `make_ai_decisions`, `run_time_machine`, `resolve_outcomes`, with `list_jobs` / `get_job`
  to follow them. Each accepts `wait_seconds` (up to 600) to wait for the job to finish. When `CIVALPHA_ADMIN_TOKEN`
  is set they need the same token as the REST API, sent as the `X-Admin-Token` header (or `Authorization: Bearer`).
* **`add_company(symbol, ...)`:** adds a stock to the research universe like the Universe page does. Only the ticker is
  required; name, CIK, sector, industry and benchmark ETF are filled in from SEC EDGAR when omitted (the answer says
  what was filled and when the sector suggestion needs review). It queues an SEC ingest and a price sync unless told
  not to, and takes optional `tags`. Same admin-token rule as the job tools.
* **`set_company_tags(symbol, tags)`:** replaces a stock's user-defined tags (a theme, a watchlist), so an assistant
  can categorize the universe; `list_companies` and `get_company` return them. Removing or editing companies otherwise
  and adding events stay in the UI and the REST API.
* Resources `civalpha://about` and `civalpha://status`; prompt `investment_review(symbol)` walks through the evidence
  for one stock.

Connect from Claude Code:

```bash
claude mcp add --transport http civalpha http://localhost:8088/mcp
# with an admin token:
claude mcp add --transport http civalpha http://localhost:8088/mcp --header "X-Admin-Token: <token>"
# or as a local stdio process (trusted like any local program), from backend/ with its virtualenv:
claude mcp add civalpha -- backend/.venv/bin/python -m civalpha.platform.mcp_server
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
`EVENTS_*`, `CIVALPHA_LLM_PROVIDER`, `CIVALPHA_LLM_MODEL`, `CIVALPHA_LLM_REVIEW`, `ANTHROPIC_API_KEY`, `CIVALPHA_MCP_ALLOWED_HOSTS` and
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
docs/      api.md (REST contract and MCP tools); research/ (dated research notes: the pre-registered live test, the
           October 2026 forecasting-polish report)
```

## Known limitations / next steps

* **Prices.** The Yahoo and Tiingo adapters were built against their documented response formats but could not
  be exercised against the live services from the build environment. The trading calendar comes from benchmark
  bars. Yahoo's closes are split-adjusted: a newly reported split triggers a full re-download of that symbol, so
  stored bars stay consistent. Neither provider reports spin-offs: the parent's close drops by the value distributed
  and nothing in the feed explains it (CTVA lost 84% on 2026-10-01 when it distributed Vylor one-for-one). Such a
  distribution is recorded by hand as a `SPIN_OFF` corporate action whose value is the per-share market value of the
  shares received at the ex-date close; the total-return index treats it like a cash dividend. `python -m
  civalpha.integrity` scans every series for 21-day volatility above 200%, one-day total returns beyond 50%, and
  raw-close moves beyond 50% with no recorded action (the signature of a missing adjustment). Corrections are versioned: the replaced
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
    proper significance testing across many universes. Signal health now tracks each input's IC over time, but the
    models do not yet drop or reweight decaying inputs automatically.
  * Live, real-data accuracy can only build up over time.
* **Breadth.**
  * Universe expansion ranks companies by the public float of their last 10-K (SEC XBRL frames), which lags the market
    by up to a year; it is a size screen, not a tradability screen. Filers' scale errors (a float tagged 1,000× too big)
    are caught by comparing with total assets and with the price per share the float implies; a float tagged too small
    simply fails the screen. Expect each pipeline run to take roughly 5-10 seconds
    more per stock: the feature builder and the AI's walk-forward training are not yet vectorized across companies.
* **Point-in-time cutoff.** The as-of cutoff of a trading date is 21:00 UTC, which is 17:00 New York in summer: a
  filing accepted between 16:00 and 17:00 on a summer day counts as known at that day's close. The earnings module
  decides sessions in New York time; the other features still use the fixed cutoff.
* **Operations.**
  * Admin protection is a single shared token. Real multi-user access needs proper authentication and roles.
  * The scheduler runs inside the worker, so a missed run while the stack is down is not caught up.
