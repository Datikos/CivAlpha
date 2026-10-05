# CivAlpha REST API (v1)

Base path: `/api`. JSON, camelCase. Timestamps are ISO-8601 UTC (`2026-09-30T21:00:00Z`), dates are `YYYY-MM-DD`.

Three kinds of numbers are kept apart everywhere:
* **recorded facts** (filed values, prices, official event documents) — `kind: "FACT"`
* **model estimates** (estimated exposures, derived features) — `basis: "ESTIMATED"`
* **forecasts** (probabilities from a model) — always shown with horizon, interval and publication time

## Meta
`GET /api/meta`
```json
{ "llmEnabled": false, "secConfigured": true, "dataCutoff": "2026-09-30",
  "target": "P(21-trading-day total return of stock > total return of its sector benchmark ETF), measured close(t) -> close(t+21)",
  "disclaimers": ["Research software. Not investment advice.", "..."] }
```

## Companies
`GET /api/companies`
```json
[{ "id": 1, "symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology", "benchmarkSymbol": "XLK",
   "cik": "0000320193", "latestClose": 231.4, "latestCloseDate": "2026-09-30",
   "latestForecasts": { "BASELINE": {"id": 10, "probability": 0.52, "asOfDate": "2026-09-30"},
                        "AUGMENTED": {"id": 11, "probability": 0.44, "asOfDate": "2026-09-30"} },
   "dividend": { "status": "REGULAR", "frequency": "QUARTERLY", "trailingYield": 0.0044, "indicatedYield": 0.0045,
                 "lastExDate": "2026-08-11", "yearsPaid": 14 },
   "recentCloses": [228.1, 229.4, "...22 closes, oldest first"], "change21d": 0.031, "benchmarkChange21d": 0.012 }]
```
`latestForecasts` keys may be missing. `dividend` is the summary of `GET /api/companies/{symbol}/dividends`.
`recentCloses` holds the last 22 raw closes (one forecast horizon); `change21d` and `benchmarkChange21d` are the
returns from the first to the last of them, for the stock and its benchmark ETF (`null` with fewer than two closes).

`GET /api/companies/{symbol}` — symbol may be a historical ticker (e.g. `FB` resolves to META).
```json
{ "id": 6, "symbol": "META", "name": "Meta Platforms, Inc.", "sector": "Communication Services",
  "benchmarkSymbol": "XLC", "exchange": "NASDAQ",
  "tickerHistory": [{"symbol": "FB", "validFrom": "2012-05-18", "validTo": "2022-06-09", "source": "manual"},
                    {"symbol": "META", "validFrom": "2022-06-09", "validTo": null, "source": "manual"}],
  "cikHistory": [{"cik": "0001326801", "validFrom": "2012-05-18", "validTo": null, "source": "manual"}],
  "keyFacts": [{"concept": "Revenues", "label": "Revenue", "value": 1.2e11, "unit": "USD",
                "periodStart": "2025-07-01", "periodEnd": "2025-09-30", "fiscalPeriod": "Q3", "formType": "10-Q",
                "filedDate": "2025-10-30", "accessionNo": "...", "sourceUrl": "https://www.sec.gov/..."}] }
```

`GET /api/companies/{symbol}/prices?from=YYYY-MM-DD`
```json
{ "symbol": "META", "benchmarkSymbol": "XLC",
  "bars": [{"date": "2026-09-30", "symbol": "META", "close": 512.3, "benchmarkClose": 98.1}],
  "corporateActions": [{"exDate": "2024-03-01", "type": "CASH_DIVIDEND", "value": 0.5}] }
```

`GET /api/companies/{symbol}/dividends` — recorded cash dividends as of the latest close, plus the latest 12 months'
payout as filed.
```json
{ "symbol": "AAPL", "asOf": "2026-09-30", "price": 231.4, "priceDate": "2026-09-30",
  "status": "REGULAR", "frequency": "QUARTERLY", "paymentsPerYear": 4,
  "lastExDate": "2026-08-11", "lastAmount": 0.26, "nextExpected": "2026-11-10",
  "ttmDividends": 1.02, "ttmPayments": 4, "ttmSpecial": 0.0, "trailingYield": 0.0044,
  "indicatedAnnual": 1.04, "indicatedYield": 0.0045, "yearsPaid": 14, "yearsRaised": 12, "firstExDate": "2012-08-09",
  "annual": [{"year": 2025, "total": 1.02, "payments": 4}],
  "payments": [{"exDate": "2026-08-11", "amount": 0.26, "special": false}],
  "payout": { "periodStart": "2024-09-29", "periodEnd": "2025-09-27", "netIncome": 1.1e11,
              "dividendsPaid": 1.5e10, "buybacks": 9.0e10, "payoutRatio": 0.14, "totalPayoutRatio": 0.95,
              "formType": "10-K", "filedDate": "2025-10-31", "accessionNo": "...", "sourceUrl": "https://www.sec.gov/..." } }
```
* `status`: `REGULAR` (steady schedule, next payment not overdue), `IRREGULAR` (paid in the last 12 months without a
  steady schedule), `SUSPENDED` (next payment overdue), `NONE` (no dividend recorded). `frequency` (`MONTHLY`,
  `QUARTERLY`, `SEMIANNUAL`, `ANNUAL`) and the indicated values are set only when `REGULAR`.
* Amounts are per share as of `asOf` (earlier dividends restated for later splits). A payment more than 2.5× the
  previous ones that the next payment does not sustain (a raise is sustained) is `special`: it counts in
  `ttmDividends` but not in the schedule, `indicatedAnnual` or `yearsRaised`. For a regular payer `ttmDividends` is
  its last year of regular payments (`paymentsPerYear` of them) plus specials in the last 365 days.
* `indicatedAnnual` = latest regular payment × payments per year (an estimate); `trailingYield` = `ttmDividends` / price.
* `yearsPaid` / `yearsRaised`: consecutive calendar years with a dividend / with a higher last regular payment than the
  year before; both are limited by how far back prices were loaded (`firstExDate`).
* `payout` (null without a filed 12-month net income): `PaymentsOfDividendsCommonStock` (else `PaymentsOfDividends`)
  and `PaymentsForRepurchaseOfCommonStock` from the cash-flow statement for the latest filed 12-month period — usually
  the fiscal year of a 10-K, or trailing twelve months when a 10-Q reports them; ratios are null when net income ≤ 0.

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
   "items": null, "amendsAccession": null, "passageCount": 4, "factCount": 120 }]
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
              "dimensions": {"srt:StatementGeographicalAxis": "aapl:GreaterChinaMember"}} }],
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
   "affectedCompanyCount": 7, "version": 1 }]
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
               "version": 1, "contentSha256": "...", "documentUrl": "/api/documents/12"}],
  "affectedCompanies": [{"symbol": "AAPL", "name": "Apple Inc.",
       "paths": [{"targetType": "COUNTRY", "targetCode": "CN", "exposureId": 4, "basis": "DIRECTLY_REPORTED",
                  "confidence": "HIGH", "share": 0.18, "passageId": 9}]}],
  "reissuedForecasts": [{"symbol": "AAPL", "modelKind": "AUGMENTED", "id": 31, "version": 2, "issuedAt": "...",
       "probability": 0.41, "probLow": 0.35, "probHigh": 0.47,
       "previous": {"id": 22, "probability": 0.47, "probLow": 0.41, "probHigh": 0.53}}],
  "forecastShift": [{"symbol": "AAPL", "modelKind": "AUGMENTED",
       "before": {"id": 22, "asOfDate": "2025-04-01", "probability": 0.47},
       "after": {"id": 31, "asOfDate": "2025-04-02", "probability": 0.41}}] }
```
`reissuedForecasts` lists the forecasts published because this event arrived (an official event re-issues every exposed
company; `previous` is the version each one superseded). `forecastShift` compares, for every exposed company and model,
the last forecast before the event date with the first one on or after it, whatever caused the re-issue.
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
  "reason": "scheduled issue",
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
{ "evaluation": { "id": 1, "runAt": "...", "dataCutoff": "2026-09-30",
    "config": {"horizon": 21, "sampleEvery": 5, "embargo": 21, "foldLength": 63, "minTrainDays": 504, "costBpsPerSide": 10},
    "metrics": {"BASELINE": {"n": 3000, "brier": 0.249, "logLoss": 0.69, "auc": 0.52, "accuracy": 0.51, "baseRate": 0.49, "brierSkill": 0.002},
                "AUGMENTED": {"...": 0}},
    "comparison": {"brierDiff": -0.004, "ciLow": -0.007, "ciHigh": -0.001, "aucDiff": 0.03, "note": "negative brierDiff = augmented better"},
    "calibration": {"BASELINE": [{"binLow": 0.0, "binHigh": 0.1, "meanPredicted": 0.07, "observedRate": 0.1, "count": 12}], "AUGMENTED": []},
    "trading": {"BASELINE": {"periods": 30, "meanGross": 0.002, "meanNet": 0.001, "tStatNet": 0.4, "hitRate": 0.52,
                             "annualizedNet": 0.012, "sharpeNet": 0.2, "avgPositions": 12.0, "turnoverCostPerPeriod": 0.004,
                             "coverage": [{"coverage": 0.1, "n": 300, "minConfidence": 0.08, "accuracy": 0.53, "brier": 0.26,
                                           "meanGross": 0.004, "meanNet": 0.0, "ciLow": -0.006, "ciHigh": 0.006,
                                           "costPerPosition": 0.004, "side": "both"}]},
                "AUGMENTED": {}},
    "folds": [{"fold": 0, "testStart": "2021-01-04", "testEnd": "2021-03-31", "nTrain": 900, "nTest": 300,
               "brier": {"BASELINE": 0.25, "AUGMENTED": 0.24}}],
    "verdict": "..." },
  "issued": {"BASELINE": {"issued": 50, "resolved": 24, "brier": 0.25, "hitRate": 0.5, "baseRate": 0.48},
             "AUGMENTED": {"issued": 50, "resolved": 24, "brier": 0.24, "hitRate": 0.55}} }
```
`issued` covers LIVE forecasts only; `issuedByMode` has the same statistics for `LIVE` and `REPLAY` (replayed = published
after its data cutoff, reconstructed point-in-time). `evaluation` is null before the first evaluation run.

`trading.<model>.coverage` is the abstention curve: the out-of-sample forecasts ranked by confidence (`side` `both`:
|p − 0.5|, long when p > 0.5 and short when below). Each row keeps the top `coverage` share (0.05, 0.1, 0.2, 0.3, 0.5, 1.0):
`minConfidence` is the bar to get in, `accuracy` the share of direction calls that were right, `meanNet` the mean excess
return over the sector ETF per position after `costPerPosition` (4 legs × costBpsPerSide), with a 95% CI from a bootstrap
over 21-day blocks of dates. The verdict quotes the 10% row.

## Strategies

`GET /api/strategies` — latest strategy backtest: every classic rule, the benchmarks and the AI, scored on the same
out-of-sample window after costs. Results are sorted by Sharpe.
```json
{ "run": {"id": 3, "runAt": "2026-10-04T05:10:00Z", "dataCutoff": "2026-09-30", "oosStart": "2021-07-01",
          "summary": "13 strategies backtested on the same out-of-sample window ...",
          "config": {"costBpsPerSide": 10, "costSensitivityBps": [0, 10, 25], "reference": "EW_BUY_HOLD", "nCandidates": 11,
                     "execution": "...", "verdictRule": "...", "ai": {"horizon": 10, "entry_p": 0.55, "exit_p": 0.48, "...": "..."},
                     "aiFolds": ["..."],
                     "aiCoverage": [{"coverage": 0.1, "n": 3300, "minConfidence": 0.58, "accuracy": 0.52, "brier": 0.25,
                                     "meanGross": 0.003, "meanNet": 0.001, "ciLow": -0.004, "ciHigh": 0.006,
                                     "costPerPosition": 0.002, "side": "long"}],
                     "dividendFeatureTest": {"rows": 32675, "dates": 1307, "baseRate": 0.49,
                                             "withoutDividends": {"brier": 0.2579, "logLoss": 0.711, "auc": 0.512},
                                             "withDividends": {"brier": 0.2578, "logLoss": 0.710, "auc": 0.510},
                                             "brierDiff": -0.0001, "ciLow": -0.0009, "ciHigh": 0.0008, "note": "..."}}},
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
Families: `BENCHMARK`, `TREND`, `MEAN_REVERSION`, `FUNDAMENTAL`, `EVENT`, `SPECULATIVE`, `AI`. `DOUBLER_SCREEN` (family `SPECULATIVE`) trades the doubler study's screen: hold 63 days with a 50% stop. Report-based strategies are `QUALITY_GROWTH`, `PEAD_SUE`, `VALUE_EY`, `GROSS_PROFIT` and `AI_FUND` (the AI on the financial-report profile only); decision factors of kind `FUNDAMENTAL` come from that profile. `DIV_YIELD` holds the 5 highest dividend yields; `AI_DIV` is `AI_GBM` plus the dividend signals (yield, change in the regular dividend, filed payout ratio). The decision layer is tested on `AI_GBM`'s own probabilities: `AI_CONF` (abstention) enters at p ≥ `confident_entry_p` (0.60) and exits below `confident_exit_p` (0.50); `AI_SIZED` keeps `AI_GBM`'s entries and exits but sizes each position as `vol_budget` (0.04) / annualized 21-day volatility, capped at `max_weight` (0.20) and at 100% in total. `config.aiCoverage` is the abstention curve of the AI's own out-of-sample forecasts, long only (`side` `long`: ranked by p, `minConfidence` is the lowest p in the slice), costs on 2 legs; same fields as `trading.<model>.coverage` on `/api/accuracy`. `config.dividendFeatureTest` compares the out-of-sample forecasts of the two models on the same rows: `brierDiff` = Brier(with) − Brier(without), negative when the dividend signals help, with a 95% CI from a bootstrap over 21-day blocks of dates. `excess*` compare daily net returns with
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
     "model": {"algorithm": "hist_gradient_boosting", "trainedThrough": "2026-09-15", "nTrain": 31250, "horizon": 10, "...": "...",
               "sizing": {"vol21": 0.31, "sizedWeight": 0.129, "confident": true, "volBudget": 0.04, "maxWeight": 0.2, "confidentEntryP": 0.6}},
     "issuedAt": "2026-09-30T22:05:00Z",
     "explanation": "The model ...", "explanationModel": "anthropic:claude-opus-5-5"}
  ] }
```
`action` is `ENTER`, `EXIT`, `HOLD` or `STAY_OUT` (relative to the previous stored decision). `contribution` is the change
in probability compared with the feature at its training median. `explanation` is written by the language model only for
ENTER/EXIT when `CIVALPHA_LLM_PROVIDER=anthropic`; it is null otherwise and never changes the decision. Decision rows are
append-only (UPDATE/DELETE are rejected by the database).

## Doubler study

`GET /api/doublers` — the latest study (`run`, null before the first one) and the list of earlier `runs`
(`id`, `runAt`, `dataCutoff`, `headline`). `GET /api/doublers/{id}` — one run (404 if unknown).

The study asks, for every member stock-day `t` and horizon `h` in 21, 42 and 63 trading days: bought at the close of
`t+1`, did the stock reach +100% at any close up to `t+1+h` (`hit`), and did it fall to −50% (`lost`)? Everything in
`screen`/`today` and every feature uses data at or before close(`t`); the outcomes read later prices.
```json
{ "run": { "id": 1, "runAt": "2026-10-05T05:43:08Z", "dataCutoff": "2026-10-02", "headline": "Over 7.7 years and 25 stocks, ...",
  "result": {
    "start": "2019-01-02", "dataCutoff": "2026-10-02", "years": 7.7, "universeSize": 25, "stockDays": 48265,
    "config": {"horizons": [21, 42, 63], "threshold": 1.0, "loss": 0.5, "volRankMin": 0.7, "maxMarketCap": 2e9, "maxPrice": 20, "...": "..."},
    "horizons": {"63": {
      "base":    {"n": 46665, "hits": 495, "hitRate": 0.0106, "lossRate": 0.0076, "medianEndReturn": 0.047, "meanEndReturn": 0.072, "medianMaxReturn": 0.124},
      "screen":  {"n": 140, "hits": 13, "hitRate": 0.093, "hitCiLow": 0.008, "hitCiHigh": 0.175, "lossRate": 0.157, "medianEndReturn": -0.088, "p10EndReturn": -0.4, "p90EndReturn": 0.6},
      "control": {"n": 1140, "hitRate": 0.071, "...": "same volatile, small stock-days without a trigger"},
      "lift": 8.75, "verdict": "Finds doublers more often than chance: NOT supported (interval includes the base rate)",
      "byYear": [{"year": 2020, "n": 6117, "hits": 116, "hitRate": 0.019, "lossRate": 0.003}],
      "episodes": [{"companyId": 25, "symbol": "BDSX", "signalDate": "2026-04-21", "entryDate": "2026-04-22", "lastSignalDate": "2026-06-17",
                    "signalDays": 31, "daysToDouble": 45, "doubledOn": "2026-06-26", "maxReturn": 1.12, "endReturn": 0.93, "maxDrawdown": -0.15}],
      "companiesWithHits": ["AMD", "BDSX", "..."],
      "profile": [{"feature": "vol_60", "label": "Realized volatility, 60 days (annualized)", "n": 45665, "medianAll": 0.32, "medianHits": 0.73,
                   "byQuintile": [{"quintile": 1, "n": 9133, "hitRate": 0.0, "low": 0.08, "high": 0.21}]}]}},
    "today": {"asOfDate": "2026-10-02", "stocks": [{"companyId": 25, "symbol": "BDSX", "name": "...", "fires": false,
              "conditions": {"volatile": true, "small": false, "breakout": false, "volumeSpike": false},
              "features": {"vol_60": 0.9, "vol_rank": 1.0, "breakout_252": -0.3, "volume_ratio": 0.8, "dollar_volume_20": 1.2e7, "market_cap": 3.1e9, "price": 29.5, "ret_21": 0.05, "dd_52w": -0.3}}]},
    "screenRule": {"volatile": "...", "small": "...", "trigger": "..."}, "featureLabels": {"vol_60": "..."}, "disclaimers": ["..."] } },
  "runs": [{"id": 1, "runAt": "...", "dataCutoff": "2026-10-02", "headline": "..."}] }
```
* `base` counts every member stock-day whose window has closed; `screen` the stock-days the screen fired on; `control` the
  volatile, small stock-days without a trigger. `hitCi*` are 95% bootstrap intervals over blocks of 21 dates (nearby days
  share most of their window). `lift` = screen hit rate / base rate.
* An episode is one move per company: a run of hit stock-days (gaps up to 5 days bridged), with the first signal day,
  the entry at the next close and the day the close first reached 2× that entry.
* `profile` is the median of each feature on doubling stock-days vs all, and the hit rate by pooled quintile.
* The verdict is SUPPORTED only when the screen fired on at least 30 stock-days and the interval lies above the base rate.
  It is not investment advice; the loss rate sits next to every hit rate for a reason.

## Time machine

`GET /api/timemachine` — latest 50 runs: `id`, `asOfDate`, `runAt`, `dataCutoff`, `headline`.

`GET /api/timemachine/{id}` — one run. Everything under `result.stocks[]` except `actual` and `path` was decided at the
close of `asOfDate` with only the data known then; `actual` and `path` are the facts that followed.
```json
{ "id": 4, "asOfDate": "2025-06-30", "runAt": "2026-10-04T06:38:48Z", "dataCutoff": "2026-10-02", "headline": "...",
  "result": {
    "horizons": [5, 10, 21, 63],
    "stocks": [
      {"companyId": 1, "symbol": "AAPL", "name": "Apple Inc.", "benchmarkSymbol": "XLK", "closeAsOf": 205.17,
       "odds": {"21": {"BASELINE": 0.48, "AUGMENTED": 0.46}},
       "range": {"21": {"q10": -0.07, "q50": 0.01, "q90": 0.09, "naiveQ10": -0.08, "naiveQ50": 0.01, "naiveQ90": 0.11}},
       "ai": {"action": "ENTER", "probability": 0.57, "rank": 2, "weight": 0.125, "factors": ["..."]},
       "actual": {"21": {"stockReturn": 0.05, "benchmarkReturn": 0.03, "excess": 0.02, "beat": true,
                         "execReturn": 0.04, "execBenchmarkReturn": 0.03, "endDate": "2025-07-30"}, "63": null},
       "path": [{"date": "2025-06-30", "stock": 0.0, "benchmark": 0.0}]}
    ],
    "summary": {"21": {"resolved": 24, "endDate": "2025-07-30",
                       "odds": {"AUGMENTED": {"n": 24, "hitRate": 0.54, "brier": 0.26, "brierBaseRate": 0.262, "auc": 0.55, "topMinusBottomExcess": 0.01}},
                       "range": {"n": 24, "coverage": 0.92, "naiveCoverage": 0.96, "target": 0.8, "medianAbsError": 0.04,
                                 "naiveMedianAbsError": 0.05, "avgWidth": 0.15, "naiveWidth": 0.19},
                       "ai": {"universeReturn": 0.051, "picks": ["AAPL", "QCOM"], "picksReturn": 0.039, "excessVsUniverse": -0.012, "picksBeatSector": 1}}},
    "models": {"odds21": {"nTrain": 1650, "baseRate": 0.49, "trainedThrough": "2025-05-30"}, "...": "..."} } }
```
* `odds`: the BASELINE and AUGMENTED logistic models, refitted per horizon on labels resolved by the as-of close.
* `ai`: the AI strategy's decision that day, starting with no holdings. Returns are measured from the next close (`execReturn`).
* `range`: 10/50/90% quantiles of the total return (quantile gradient boosting); `naive*` are the unconditional
  training quantiles.
* `actual[h]` is `null` while that horizon has not passed.

## Admin / pipeline
When the server sets `CIVALPHA_ADMIN_TOKEN`, every `/api/admin/**` request and every non-GET `/api` request
(e.g. `POST /api/events`) must send `X-Admin-Token: <token>` (or `Authorization: Bearer <token>`); otherwise the
response is `401`. `GET /api/meta` reports `adminTokenRequired`.

* `GET /api/admin/jobs` → `[{"id":1,"jobType":"PIPELINE_RUN","status":"SUCCEEDED","log":"...","startedAt":"...","finishedAt":"..."}]`
* `POST /api/admin/pipeline/run` → job — refresh prices, SEC filings, macro data and events, then evaluate, issue
  forecasts, run the strategy lab and record the AI's decisions. Fails if the universe is empty.
* `POST /api/admin/forecasts/issue` body `{"asOfDate": "2026-09-30"}` (optional) → job
* `POST /api/admin/evaluate` → job
* `POST /api/admin/outcomes/resolve` → job
* `POST /api/admin/timemachine` body `{"asOfDate": "2025-06-30"}` → job — forecast as of that past close with only the
  data known then, then score it against what followed (400 for today or a future date)
* `POST /api/admin/strategies/backtest` → job — backtest every strategy and store a new run (also part of every pipeline run)
* `POST /api/admin/doublers/study` → job — run the doubler study and store it (also part of every pipeline run)
* `POST /api/admin/strategies/decide` body `{"asOfDate": "2026-09-30"}` (optional) → job — store the AI's decisions for
  that trading day and explain ENTER/EXIT actions when a language model is configured
* `POST /api/admin/sec/ingest` body `{"symbol":"AAPL"}` → job
* `POST /api/admin/prices/import` multipart `file` (CSV `symbol,date,open,high,low,close,volume`) → job

## Universe management (admin)
* `GET /api/admin/universe` → `{"universe":"nasdaq-core","companies":[{"id":1,"symbol":"AAPL","formerSymbols":null,"cik":"0000320193",
  "name":"Apple Inc.","sector":"Technology","industry":"CONSUMER_ELECTRONICS","benchmarkSymbol":"XLK","active":true,
  "memberSince":"2019-01-02","removedOn":null,"priceCount":1950,"lastPriceDate":"2026-10-02","filingCount":70,"forecastCount":12,
  "deletable":false,"tags":["AI","core"]}],"benchmarks":["XLK"],"sectors":["Technology"],"industries":["SEMICONDUCTORS"],"productIndustries":["SEMICONDUCTORS"],
  "tags":[{"tag":"AI","count":3},{"tag":"core","count":1}]}` — `tags` are user-defined categories (a theme, a watchlist); the top-level list counts the companies carrying each
* `GET /api/admin/universe/lookup?symbol=BDSX` → `{"symbol","cik","name","source"}` from SEC company_tickers.json (404 if unknown)
* `GET /api/admin/universe/enrich?symbol=NVDA` → `{"symbol","cik","name","source","exchange","sic","sicDescription","formerNames",
  "sector","benchmarkSymbol","industry","sectorConfidence":"high"|"review"|null,"sectorNote","existing":{"companyId","symbol","active"}|null,
  "sectorAlternatives":[{"sector","benchmarkSymbol"},…],"sectorBenchmarks":{"Technology":"XLK",…},"warnings":[...],"priceProviderEnabled"}` — everything EDGAR knows about a ticker plus a sector / benchmark ETF suggested from its SIC
  code, to prefill the add form. Never fails for an unknown ticker or an unreachable source: missing fields are null and explained in `warnings`.
* `POST /api/admin/universe/companies` body `{"symbol","name","cik","sector","industry","benchmarkSymbol","memberSince","ingestSec","syncPrices","tags"}`
  → `{"id","symbol","jobs":[job,…],"nextSteps":[…]}`; `ingestSec` queues a SEC_INGEST job, `syncPrices` a PRICE_SYNC job (when a price provider is configured)
* `PUT /api/admin/universe/companies/{id}` body `{"name","sector","industry","benchmarkSymbol","tags"}` (omitted fields unchanged; `tags` replaces the whole list)
* `PUT /api/admin/universe/companies/{id}/tags` body `{"tags":["AI","China exposed"]}` → `{"id","tags"}` — replaces the company's tags (`[]` clears).
  Tags are trimmed, at most 20 per company and 40 characters each (letters, digits, spaces and `_ . & / + -`); a tag already in use on
  another company keeps that spelling whatever case is sent, so a tag is one tag universe-wide. `GET /api/companies` and
  `GET /api/companies/{symbol}` return each company's `tags` (and `industry`).
* `POST /api/admin/universe/companies/{id}/remove` / `/restore` body `{"effectiveDate"}` (optional, default today)
* `POST /api/admin/universe/companies/{id}/ticker` body `{"symbol","effectiveDate"}`
* `DELETE /api/admin/universe/companies/{id}` — only while no data is attached (400 otherwise)
* `POST /api/admin/prices/sync` → job — download prices from the configured provider (`GET /api/meta` → `priceProvider`)

Errors are `{"error": "explanation"}` with status 400 (invalid request) or 404 (unknown id/symbol).

## MCP (Model Context Protocol)

`POST /mcp` (Streamable HTTP, stateless, JSON responses) serves the same data and actions to MCP clients. Through the UI
it is `http://localhost:8088/mcp`; it answers `503` until the MCP transport has started with the API. The endpoint
rejects a `Host` or `Origin` that is not localhost/127.0.0.1 unless listed in `CIVALPHA_MCP_ALLOWED_HOSTS` /
`CIVALPHA_MCP_ALLOWED_ORIGINS` (DNS-rebinding protection). Job tools follow the admin-token rule above: send
`X-Admin-Token` (or `Authorization: Bearer`) with the MCP requests. A local stdio run
(`python -m civalpha.platform.mcp_server`) is trusted like any local process.

Tools call the same code as the REST endpoints and return condensed JSON (also as `structuredContent`; lists as
`{"result": [...]}`). A 400/404 error becomes a tool error with the same message, e.g.
`Error executing tool get_company: unknown symbol NOPE`.

| Tool | REST equivalent | Returns / notes |
|---|---|---|
| `get_status` | `GET /api/meta` + companies + strategy run | target, disclaimers, sources, data cutoff, symbols, strategy-lab summary |
| `list_companies` | `GET /api/companies` | with sector, industry and user-defined `tags` |
| `get_company(symbol)` | `GET /api/companies/{symbol}` (+ prices, decisions) | key facts, 1/3/12-month returns vs sector ETF, latest forecasts, AI decision; former tickers resolve |
| `get_financials(symbol, as_of_date?, quarters?)` | `GET /api/companies/{symbol}/financials` | last `quarters` points per series (default 8) |
| `get_prices(symbol, from_date?)` | `GET /api/companies/{symbol}/prices` | summary: first/last/high/low, returns, corporate actions |
| `get_dividends(symbol)` | `GET /api/companies/{symbol}/dividends` | |
| `list_filings(symbol, limit?)` / `get_filing(filing_id, max_passages?)` | `GET /api/companies/{symbol}/filings`, `GET /api/filings/{id}` | passages truncated to 600 characters |
| `get_exposures(symbol, as_of_date?, limit?)` | `GET /api/companies/{symbol}/exposures` | |
| `list_events(category?, limit?)` / `get_event(event_id)` | `GET /api/events`, `GET /api/events/{id}` | `category`: TRADE_TARIFF or MONETARY_POLICY |
| `get_current_forecasts` / `get_forecast_history(symbol?, model_kind?, limit?)` / `get_forecast(forecast_id)` | `GET /api/forecasts/*` | |
| `get_accuracy` | `GET /api/accuracy` | |
| `get_strategies` / `get_strategy(key, trades?)` | `GET /api/strategies`, `GET /api/strategies/{key}` | no equity curves |
| `get_decisions(as_of_date?)` | `GET /api/decisions` | top 3 factors and the rules holding each stock |
| `list_time_machine_runs` / `get_time_machine_run(run_id, horizon?)` | `GET /api/timemachine`, `GET /api/timemachine/{id}` | per-stock prediction vs actual for one horizon |
| `get_doubler_study(horizon?, episodes?)` | `GET /api/doublers` | base rate, screen vs control with intervals, latest episodes, profile medians, stocks flagged today |
| `investment_candidates` | — | see below |
| `list_jobs(limit?)` / `get_job(job_id, wait_seconds?)` | `GET /api/admin/jobs` | admin |
| `run_pipeline`, `update_prices`, `ingest_sec_filings(symbol)`, `evaluate_models`, `issue_forecasts(as_of_date?)`, `run_strategy_backtest`, `make_ai_decisions(as_of_date?)`, `run_time_machine(as_of_date)`, `run_doubler_study`, `resolve_outcomes` | `POST /api/admin/**` | admin; each takes `wait_seconds?` (max 600) and returns the job with its log tail |
| `add_company(symbol, name?, cik?, sector?, industry?, benchmark_symbol?, member_since?, ingest_sec?, sync_prices?, tags?)` | `POST /api/admin/universe/companies` (+ `GET .../enrich`) | admin; missing name / CIK / sector / industry / benchmark come from EDGAR (`filledFromSec`, `notes` on a sector that needs review); `ingest_sec` and `sync_prices` default to true and return the queued jobs; `nextSteps` lists what still has to be done by hand |
| `set_company_tags(symbol, tags)` | `PUT /api/admin/universe/companies/{id}/tags` | admin; replaces the company's user-defined tags (`[]` clears); former tickers resolve |

`investment_candidates` returns:
```json
{ "universeSize": 25,
  "candidates": [{ "symbol": "PEP", "name": "PepsiCo, Inc.", "sector": "Consumer Staples",
      "pBeatSectorAugmented": 0.53, "pBeatSectorBaseline": 0.51,
      "aiAction": "ENTER", "aiProbability": 0.57, "aiRank": 1, "rulesHolding": ["SMA_50_200", "QUALITY_GROWTH"],
      "dividend": {"status": "REGULAR", "trailingYield": 0.031, "indicatedYield": 0.032, "yearsPaid": 8} }],
  "evidence": {"accuracy": "Walk-forward, 4650 out-of-sample predictions per model. ...",
               "strategyLab": {"summary": "17 strategies backtested ...", "verdicts": [{"strategyKey": "AI_GBM", "name": "...", "verdict": "Beats buy-and-hold after costs: NOT supported"}]}},
  "disclaimers": ["Research software. Not investment advice. ...", "..."] }
```
Candidates are sorted by the AI probability (else the AUGMENTED forecast probability). Resources: `civalpha://about`
(text) and `civalpha://status` (JSON, same as `get_status`). Prompt: `investment_review(symbol)`.
