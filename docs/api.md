# CivAlpha REST API (v1)

Base path: `/api`. JSON, camelCase. Timestamps are ISO-8601 UTC (`2026-09-30T21:00:00Z`), dates are `YYYY-MM-DD`.
Every record that came from seeded synthetic data has `isDemo: true` — the UI must badge it.

Three kinds of numbers are kept apart everywhere:
* **recorded facts** (filed values, prices, official event documents) — `kind: "FACT"`
* **model estimates** (estimated exposures, derived features) — `basis: "ESTIMATED"`
* **forecasts** (probabilities from a model) — always shown with horizon, interval and publication time

## Meta
`GET /api/meta`
```json
{ "demoDataPresent": true, "llmEnabled": false, "secMode": "fixture", "dataCutoff": "2026-09-30",
  "target": "P(21-trading-day total return of stock > total return of its sector benchmark ETF), measured close(t) -> close(t+21)",
  "disclaimers": ["Research software. Not investment advice.", "..."] }
```

## Companies
`GET /api/companies`
```json
[{ "id": 1, "symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology", "benchmarkSymbol": "XLK",
   "cik": "0000320193", "isDemo": true, "latestClose": 231.4, "latestCloseDate": "2026-09-30",
   "latestForecasts": { "BASELINE": {"id": 10, "probability": 0.52, "asOfDate": "2026-09-30"},
                        "AUGMENTED": {"id": 11, "probability": 0.44, "asOfDate": "2026-09-30"} } }]
```
`latestForecasts` keys may be missing.

`GET /api/companies/{symbol}` — symbol may be a historical ticker (e.g. `FB` resolves to META).
```json
{ "id": 6, "symbol": "META", "name": "Meta Platforms, Inc.", "sector": "Communication Services",
  "benchmarkSymbol": "XLC", "exchange": "NASDAQ", "isDemo": true,
  "tickerHistory": [{"symbol": "FB", "validFrom": "2012-05-18", "validTo": "2022-06-09", "source": "seed"},
                    {"symbol": "META", "validFrom": "2022-06-09", "validTo": null, "source": "seed"}],
  "cikHistory": [{"cik": "0001326801", "validFrom": "2012-05-18", "validTo": null, "source": "seed"}],
  "keyFacts": [{"concept": "Revenues", "label": "Revenue", "value": 1.2e11, "unit": "USD",
                "periodStart": "2025-07-01", "periodEnd": "2025-09-30", "fiscalPeriod": "Q3", "formType": "10-Q",
                "filedDate": "2025-10-30", "accessionNo": "...", "sourceUrl": "https://www.sec.gov/..."}] }
```

`GET /api/companies/{symbol}/prices?from=YYYY-MM-DD`
```json
{ "symbol": "META", "benchmarkSymbol": "XLC", "isDemo": true,
  "bars": [{"date": "2026-09-30", "symbol": "META", "close": 512.3, "benchmarkClose": 98.1}],
  "corporateActions": [{"exDate": "2024-03-01", "type": "CASH_DIVIDEND", "value": 0.5}] }
```

`GET /api/companies/{symbol}/financials?asOf=ISO-timestamp` — point-in-time view: only values filed on/before `asOf` (default now).
```json
{ "asOf": "2024-01-01T00:00:00Z",
  "series": [{ "concept": "Revenues", "label": "Revenue", "unit": "USD",
     "points": [{"periodStart": "2023-07-01", "periodEnd": "2023-09-30", "fiscalPeriod": "Q3", "value": 1.0e11,
                 "accessionNo": "...", "formType": "10-Q", "filedDate": "2023-10-30",
                 "revised": false, "originalValue": null, "sourceUrl": "..."}] }] }
```
`revised: true` means a later filing (still on/before `asOf`) replaced the originally filed value; `originalValue` is the first-filed number.

`GET /api/companies/{symbol}/filings`
```json
[{ "id": 3, "accessionNo": "0000320193-24-000123", "formType": "10-K", "periodOfReport": "2024-09-28",
   "filedDate": "2024-11-01", "acceptedAt": "2024-11-01T10:01:36Z", "url": "https://www.sec.gov/Archives/...",
   "items": null, "amendsAccession": null, "passageCount": 4, "factCount": 120, "isDemo": true }]
```

`GET /api/filings/{id}`
```json
{ "...all filing fields above...": "",
  "companySymbol": "AAPL",
  "passages": [{"id": 9, "section": "Item 1A. Risk Factors", "topic": "TRADE", "text": "...",
                "extractionMethod": "RULE_KEYWORD", "extractorVersion": "rules-v1"}],
  "facts": [{"concept": "Revenues", "value": 1, "unit": "USD", "periodEnd": "2024-09-28",
             "dimensions": {"srt:StatementGeographicalAxis": "country:CN"}}] }
```

## Exposure
`GET /api/companies/{symbol}/exposures?asOf=ISO`
```json
{ "asOf": "2026-09-30T21:00:00Z",
  "exposures": [{ "id": 4, "targetType": "COUNTRY", "targetCode": "CN", "channel": "REVENUE", "share": 0.18,
     "basis": "DIRECTLY_REPORTED", "confidence": "HIGH", "method": "XBRL_DIMENSION",
     "rationale": "Net sales attributed to Greater China / total net sales", "availableAt": "2025-11-01T10:01:36Z",
     "periodEnd": "2025-09-27",
     "filing": {"id": 3, "accessionNo": "...", "formType": "10-K", "url": "..."},
     "passage": {"id": 9, "section": "...", "text": "..."},
     "fact": {"id": 77, "concept": "RevenueFromContractWithCustomerExcludingAssessedTax", "value": 6.6e10,
              "dimensions": {"srt:StatementGeographicalAxis": "aapl:GreaterChinaMember"}},
     "isDemo": true }],
  "paths": [{ "eventId": 5, "eventTitle": "...", "eventCategory": "TRADE_TARIFF", "eventPublishedAt": "...",
              "targetType": "COUNTRY", "targetCode": "CN", "exposureId": 4, "basis": "DIRECTLY_REPORTED",
              "confidence": "HIGH", "share": 0.18, "passageId": 9, "filingAccessionNo": "..." }] }
```
A path reads: event → target (country/sector/product/cost) → company exposure → supporting filing passage/fact.

## Events
`GET /api/events?category=MONETARY_POLICY|TRADE_TARIFF`
```json
[{ "id": 5, "category": "TRADE_TARIFF", "eventType": "TARIFF_IMPOSED", "title": "...", "eventDate": "2025-04-02",
   "publishedAt": "2025-04-02T20:00:00Z", "firstSeenAt": "...", "evidenceStatus": "OFFICIAL", "actorName": "USTR",
   "targets": [{"targetType": "COUNTRY", "targetCode": "CN", "magnitude": 25}], "sourceCount": 2,
   "affectedCompanyCount": 7, "version": 1, "isDemo": true }]
```
`GET /api/events/{id}`
```json
{ "...list fields...": "", "summary": "...", "attributes": {"severity": 0.8},
  "actor": {"id": 2, "name": "USTR", "actorType": "INSTITUTION", "authority": "...", "affiliation": null,
            "profileNote": "...",
            "records": [{"recordType": "ACTION", "occurredAt": "...", "summary": "...",
                         "source": {"id": 1, "url": "...", "title": "..."}}]},
  "sources": [{"id": 12, "role": "OFFICIAL_PRIMARY", "sourceType": "OFFICIAL_EVENT", "publisher": "Federal Register",
               "title": "...", "url": "https://...", "accessionNo": null, "publishedAt": "...", "ingestedAt": "...",
               "version": 1, "contentSha256": "...", "documentUrl": "/api/documents/12", "isDemo": true}],
  "affectedCompanies": [{"symbol": "AAPL", "name": "Apple Inc.",
       "paths": [{"targetType": "COUNTRY", "targetCode": "CN", "exposureId": 4, "basis": "DIRECTLY_REPORTED",
                  "confidence": "HIGH", "share": 0.18, "passageId": 9}]}] }
```
`POST /api/events` — add a sourced event (a source URL is mandatory). Body:
```json
{ "category": "TRADE_TARIFF", "eventType": "TARIFF_IMPOSED", "title": "...", "summary": "...",
  "eventDate": "2026-09-30", "publishedAt": "2026-09-30T18:00:00Z", "actorName": "USTR",
  "attributes": {"severity": 0.7, "tariff_rate_pct": 25},
  "targets": [{"targetType": "COUNTRY", "targetCode": "CN", "magnitude": 25}],
  "source": {"url": "https://...", "title": "...", "publisher": "USTR", "role": "OFFICIAL_PRIMARY"} }
```
Response: `{ "event": {...detail...}, "deduplicated": false, "reissuedForecastIds": [21, 22] }`

`GET /api/documents/{id}` — the stored original document bytes.

## Forecasts
Forecast summary object:
```json
{ "id": 11, "companyId": 1, "symbol": "AAPL", "companyName": "Apple Inc.", "benchmarkSymbol": "XLK",
  "modelKind": "AUGMENTED", "probability": 0.44, "probLow": 0.38, "probHigh": 0.50,
  "horizonTradingDays": 21, "asOfDate": "2026-09-30", "asOf": "2026-09-30T21:00:00Z",
  "issuedAt": "2026-10-03T12:00:00Z", "issueMode": "LIVE", "version": 1, "supersedesId": null,
  "reason": "scheduled issue", "isDemo": true,
  "outcome": null }
```
`outcome` when resolved: `{"windowEndDate": "...", "stockReturn": 0.03, "benchmarkReturn": 0.01, "excessReturn": 0.02, "outcome": true, "brier": 0.31}`

`GET /api/forecasts/current` — latest version per company and model for the most recent as-of date. Array of summaries.

`GET /api/forecasts/history?symbol=AAPL&modelKind=AUGMENTED` — all forecasts (all versions), newest first.

`GET /api/forecasts/{id}`
```json
{ "...summary fields...": "",
  "target": "P(stock 21-trading-day total return > XLK total return), close(t) -> close(t+21)",
  "uncertaintyNote": "80% bootstrap interval across 30 refits; trained on 4,812 samples",
  "features": {"mom_21": 0.012, "trade_shock": -0.08},
  "explanation": { "intercept": 0.01, "baseRate": 0.49,
     "factors": [{ "feature": "trade_shock", "label": "Tariff shock x exposure", "value": -0.08, "z": -1.9,
                   "coefficient": 0.21, "contribution": -0.40, "direction": "DOWN",
                   "kind": "EVENT_FEATURE",
                   "provenance": [{"type": "EVENT", "id": 5, "label": "...", "url": "/events/5"},
                                  {"type": "PASSAGE", "id": 9, "label": "10-K Item 7 ...", "url": "https://..."}] }] },
  "sources": [{"label": "...", "kind": "EVENT|FILING|PRICES|MACRO", "url": "...", "accessionNo": null, "publishedAt": "..."}],
  "modelVersion": {"id": 3, "modelKind": "AUGMENTED", "algorithm": "logistic_regression_l2", "trainedThrough": "2026-08-28",
                   "nSamples": 4812, "featureNames": ["..."]},
  "versions": [{"id": 11, "version": 1, "issuedAt": "...", "probability": 0.44, "reason": "..."}],
  "contentSha256": "..." }
```
`contribution` is in log-odds units (coefficient × standardized value).

## Accuracy
`GET /api/accuracy`
```json
{ "evaluation": { "id": 1, "runAt": "...", "dataCutoff": "2026-09-30", "isDemo": true,
    "config": {"horizon": 21, "sampleEvery": 5, "embargo": 21, "foldLength": 63, "minTrainDays": 504, "costBpsPerSide": 10},
    "metrics": {"BASELINE": {"n": 3000, "brier": 0.249, "logLoss": 0.69, "auc": 0.52, "accuracy": 0.51, "baseRate": 0.49, "brierSkill": 0.002},
                "AUGMENTED": {"...": 0}},
    "comparison": {"brierDiff": -0.004, "ciLow": -0.007, "ciHigh": -0.001, "aucDiff": 0.03, "note": "negative brierDiff = augmented better"},
    "calibration": {"BASELINE": [{"binLow": 0.0, "binHigh": 0.1, "meanPredicted": 0.07, "observedRate": 0.1, "count": 12}], "AUGMENTED": []},
    "trading": {"BASELINE": {"periods": 30, "meanGross": 0.002, "meanNet": 0.001, "tStatNet": 0.4, "hitRate": 0.52,
                             "annualizedNet": 0.012, "sharpeNet": 0.2, "avgPositions": 12.0, "turnoverCostPerPeriod": 0.004},
                "AUGMENTED": {}},
    "folds": [{"fold": 0, "testStart": "2021-01-04", "testEnd": "2021-03-31", "nTrain": 900, "nTest": 300,
               "brier": {"BASELINE": 0.25, "AUGMENTED": 0.24}}],
    "verdict": "..." },
  "issued": {"BASELINE": {"issued": 50, "resolved": 24, "brier": 0.25, "hitRate": 0.5, "baseRate": 0.48},
             "AUGMENTED": {"issued": 50, "resolved": 24, "brier": 0.24, "hitRate": 0.55}} }
```
`issued` covers LIVE forecasts only; `issuedByMode` has the same statistics for `LIVE` and `REPLAY` (replayed = published
after its data cutoff, reconstructed point-in-time). `evaluation` is null before the first evaluation run.

## Strategies

`GET /api/strategies` — latest strategy backtest: every classic rule, the benchmarks and the AI, scored on the same
out-of-sample window after costs. Results are sorted by Sharpe.
```json
{ "run": {"id": 3, "runAt": "2026-10-04T05:10:00Z", "dataCutoff": "2026-09-30", "oosStart": "2021-07-01", "isDemo": false,
          "summary": "13 strategies backtested on the same out-of-sample window ...",
          "config": {"costBpsPerSide": 10, "costSensitivityBps": [0, 10, 25], "reference": "EW_BUY_HOLD", "nCandidates": 11,
                     "execution": "...", "verdictRule": "...", "ai": {"horizon": 10, "entry_p": 0.55, "exit_p": 0.48, "...": "..."},
                     "aiFolds": ["..."]}},
  "results": [
    {"strategyKey": "SMA_50_200", "family": "TREND", "name": "Golden cross (50/200-day average)",
     "description": {"entry": "...", "exit": "...", "origin": "...", "sizing": "SLEEVE"}, "params": {"fast": 50, "slow": 200},
     "metrics": {"start": "2021-07-01", "end": "2026-09-30", "years": 5.2, "totalReturn": 0.41, "cagr": 0.068, "volatility": 0.11,
                 "sharpe": 0.52, "sortino": 0.71, "maxDrawdown": -0.14, "calmar": 0.49, "exposure": 0.58, "beta": 0.55, "alpha": 0.01,
                 "trades": 96, "closedTrades": 84, "winRate": 0.45, "avgHoldingDays": 61, "turnoverPerYear": 2.1,
                 "costDragPerYear": 0.0021, "excessReturn": -0.02, "excessCiLow": -0.08, "excessCiHigh": 0.04,
                 "informationRatio": -0.3, "deflatedSharpe": 0.12},
     "equity": [{"date": "2021-07-02", "equity": 0.999, "drawdown": -0.001}],
     "yearly": [{"year": 2022, "return": -0.05}],
     "costSensitivity": {"0": {"cagr": 0.07, "sharpe": 0.55}, "10": {"cagr": 0.068, "sharpe": 0.52}, "25": {"cagr": 0.064, "sharpe": 0.48}},
     "verdict": "Beats buy-and-hold after costs: NOT supported"}
  ] }
```
Families: `BENCHMARK`, `TREND`, `MEAN_REVERSION`, `FUNDAMENTAL`, `EVENT`, `AI`. Report-based strategies are `QUALITY_GROWTH`, `PEAD_SUE`, `VALUE_EY`, `GROSS_PROFIT` and `AI_FUND` (the AI on the financial-report profile only); decision factors of kind `FUNDAMENTAL` come from that profile. `excess*` compare daily net returns with
`EW_BUY_HOLD` (annualized, 95% stationary block-bootstrap CI). `deflatedSharpe` is the Deflated Sharpe Ratio of that excess,
deflated for `nCandidates` strategies. A verdict says SUPPORTED only with at least 3 years out of sample, an excess CI above
0 and DSR ≥ 0.95. `equity` is sampled weekly. `run` is null and `results` empty before the first backtest.

`GET /api/strategies/{key}` — one result plus `reference` (the `EW_BUY_HOLD` equity curve), `trades` (latest 2000 round
trips: `companyId`, `symbol`, `entryDate`, `exitDate` (null = still open), `tradeReturn` (gross), `holdingDays`,
`entryReason`, `exitReason`; dates are execution dates) and `tradeCount`. 404 for an unknown key or before the first run.

`GET /api/decisions?date=2026-09-30` — the AI strategy's decisions for one trading day (default: the latest).
```json
{ "asOfDate": "2026-09-30", "dates": ["2026-09-30", "2026-09-29"],
  "decisions": [
    {"id": 812, "companyId": 3, "symbol": "NVDA", "name": "NVIDIA Corporation", "asOfDate": "2026-09-30", "strategyKey": "AI_GBM",
     "action": "ENTER", "probability": 0.61, "entryP": 0.55, "exitP": 0.48, "weight": 0.125, "rank": 1,
     "factors": [{"feature": "mom_12_1", "label": "12-1 month momentum", "kind": "TECHNICAL", "value": 0.42, "median": 0.11,
                  "contribution": 0.031, "direction": "UP"}],
     "ruleVotes": {"SMA_50_200": true, "RSI2_SMA200": false, "...": "..."},
     "model": {"algorithm": "hist_gradient_boosting", "trainedThrough": "2026-09-15", "nTrain": 31250, "horizon": 10, "...": "..."},
     "issuedAt": "2026-09-30T22:05:00Z", "isDemo": false,
     "explanation": "The model ...", "explanationModel": "anthropic:claude-opus-5-5"}
  ] }
```
`action` is `ENTER`, `EXIT`, `HOLD` or `STAY_OUT` (relative to the previous stored decision). `contribution` is the change
in probability compared with the feature at its training median. `explanation` is written by the language model only for
ENTER/EXIT when `CIVALPHA_LLM_PROVIDER=anthropic`; it is null otherwise and never changes the decision. Decision rows are
append-only (UPDATE/DELETE are rejected by the database).

## Admin / pipeline
When the server sets `CIVALPHA_ADMIN_TOKEN`, every `/api/admin/**` request and every non-GET `/api` request
(e.g. `POST /api/events`) must send `X-Admin-Token: <token>` (or `Authorization: Bearer <token>`); otherwise the
response is `401`. `GET /api/meta` reports `adminTokenRequired`.

* `GET /api/admin/jobs` → `[{"id":1,"jobType":"DEMO_LOAD","status":"SUCCEEDED","log":"...","startedAt":"...","finishedAt":"..."}]`
* `POST /api/admin/demo/load` → job — loads the synthetic demo dataset and runs the full pipeline.
* `POST /api/admin/pipeline/run` → job — ingest configured sources, evaluate, issue forecasts.
* `POST /api/admin/forecasts/issue` body `{"asOfDate": "2026-09-30"}` (optional) → job
* `POST /api/admin/evaluate` → job
* `POST /api/admin/outcomes/resolve` → job
* `POST /api/admin/strategies/backtest` → job — backtest every strategy and store a new run (also part of every pipeline run)
* `POST /api/admin/strategies/decide` body `{"asOfDate": "2026-09-30"}` (optional) → job — store the AI's decisions for
  that trading day and explain ENTER/EXIT actions when a language model is configured
* `POST /api/admin/sec/ingest` body `{"symbol":"AAPL"}` → job
* `POST /api/admin/prices/import` multipart `file` (CSV `symbol,date,open,high,low,close,volume`) → job

## Universe management (admin)
* `GET /api/admin/universe` → `{"universe":"nasdaq-core","companies":[{"id":1,"symbol":"AAPL","formerSymbols":null,"cik":"0000320193",
  "name":"Apple Inc.","sector":"Technology","industry":"CONSUMER_ELECTRONICS","benchmarkSymbol":"XLK","isDemo":false,"active":true,
  "memberSince":"2019-01-02","removedOn":null,"priceCount":1950,"lastPriceDate":"2026-10-02","filingCount":70,"forecastCount":12,
  "deletable":false}],"benchmarks":["XLK"],"sectors":["Technology"],"industries":["SEMICONDUCTORS"],"productIndustries":["SEMICONDUCTORS"]}`
* `GET /api/admin/universe/lookup?symbol=BDSX` → `{"symbol","cik","name","source"}` from SEC company_tickers.json (404 if unknown)
* `POST /api/admin/universe/companies` body `{"symbol","name","cik","sector","industry","benchmarkSymbol","memberSince","ingestSec"}`
  → `{"id","symbol","nextSteps":[...],"job"?}`
* `PUT /api/admin/universe/companies/{id}` body `{"name","sector","industry","benchmarkSymbol"}` (omitted fields unchanged)
* `POST /api/admin/universe/companies/{id}/remove` / `/restore` body `{"effectiveDate"}` (optional, default today)
* `POST /api/admin/universe/companies/{id}/ticker` body `{"symbol","effectiveDate"}`
* `DELETE /api/admin/universe/companies/{id}` — only while no data is attached (400 otherwise)
* `POST /api/admin/universe/seed` — add companies from config/universe.yml whose CIK is not in the database
* `POST /api/admin/prices/sync` → job — download prices from the configured provider (`GET /api/meta` → `priceProvider`)

Errors are `{"error": "explanation"}` with status 400 (invalid request) or 404 (unknown id/symbol).
