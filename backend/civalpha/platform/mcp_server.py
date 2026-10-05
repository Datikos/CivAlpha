"""CivAlpha MCP server: Claude (or any MCP client) can read the platform and start its jobs.

Served by the API process at /mcp (streamable HTTP, stateless), so through nginx at http://localhost:8088/mcp:

    claude mcp add --transport http civalpha http://localhost:8088/mcp

Tools call the same code as the REST endpoints, in-process, and condense the answers for a language model (summaries
instead of long series). Research tools are public like the read API; job tools follow the admin-token rule: when
CIVALPHA_ADMIN_TOKEN is set, an HTTP client must send it (X-Admin-Token or Authorization: Bearer). A local stdio run
(python -m civalpha.platform.mcp_server) is trusted like any other process on the machine.
"""
from __future__ import annotations

import contextlib
import functools
import hmac
import json
import time
from datetime import date

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.routing import Route

from . import sectors
from .api import admin, events as events_api, read, universe as universe_api
from .errors import BadRequest, NotFound, Problem, Unavailable
from .jobs import Jobs
from .rows import camel
from .sec.profile import CompanyProfiler
from .settings import settings
from .sql import db
from .tickers import TickerResolver
from .universe import UniverseService

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
ACTION = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
ACTIVE = ("QUEUED", "RUNNING")
MAX_WAIT = 600
CATEGORIES = ("TRADE_TARIFF", "MONETARY_POLICY")

ABOUT = """CivAlpha is a research platform on real data for US-listed stocks: SEC filings (XBRL facts, passages and
exposures), daily prices, dividends, macro data and official trade/tariff and monetary-policy events.

It forecasts one target: the probability that a stock's total return over the next 21 trading days beats its sector
benchmark ETF (BASELINE model: prices and fundamentals; AUGMENTED: plus policy events and macro). It backtests classic
trading rules and an AI strategy on one out-of-sample window, records the AI strategy's daily ENTER / EXIT / HOLD /
STAY_OUT decisions, and can replay forecasts from a past date and score them against what happened (time machine).

This is research software and not investment advice. It places no orders. Quote the verdicts, intervals and caveats
the tools return; never claim profitability the platform itself does not support. Start with get_status."""


def require_admin(ctx) -> None:
    """Job tools: with an admin token configured, an HTTP client must present it. No transport or no HTTP headers
    (stdio, direct calls) means a local operator, who is trusted like any other process on the machine."""
    token = settings().admin_token
    if not token or ctx is None:
        return
    headers = getattr(ctx, "headers", None)
    if headers is None:
        return
    h = {str(k).lower(): v for k, v in dict(headers).items()}
    presented = h.get("x-admin-token")
    auth = h.get("authorization") or ""
    if presented is None and auth.startswith("Bearer "):
        presented = auth[7:].strip()
    if presented is None or not hmac.compare_digest(token.encode(), str(presented).encode()):
        raise ToolError("admin token required (X-Admin-Token header)")


def _domain(fn):
    """Platform errors (unknown symbol, bad date, ...) become tool errors whose message the model sees."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (NotFound, BadRequest, Problem, Unavailable, ValueError, LookupError) as e:
            raise ToolError(str(e)) from e
    return wrapper


def _date(s: str | None, name: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise ToolError(f"{name} must be a date in the form YYYY-MM-DD") from e


def _pick(d: dict, *keys: str) -> dict:
    return {k: d.get(k) for k in keys if k in d}


def _ret(bars: list[dict], days: int, key: str = "close") -> float | None:
    if len(bars) <= days or not bars[-1].get(key) or not bars[-1 - days].get(key):
        return None
    return round(bars[-1][key] / bars[-1 - days][key] - 1, 4)


def _job(j: dict, log_lines: int = 15) -> dict:
    lines = (j.get("log") or "").strip().splitlines()
    return {**_pick(j, "id", "jobType", "status", "startedAt", "finishedAt", "params"), "logTail": lines[-log_lines:]}


def _wait(job: dict, wait_seconds: int) -> dict:
    deadline = time.monotonic() + max(0, min(int(wait_seconds or 0), MAX_WAIT))
    while job.get("status") in ACTIVE and time.monotonic() < deadline:
        time.sleep(2)
        job = camel(Jobs().get(job["id"]))
    out = _job(job)
    if out["status"] in ACTIVE:
        out["note"] = f"still {out['status'].lower()}; call get_job({job['id']}) to follow it"
    return out


def status() -> dict:
    meta = read.meta()
    companies = read.companies()
    lab = read.strategies().get("run") or {}
    return {**_pick(meta, "target", "disclaimers", "dataCutoff", "secConfigured", "fredEnabled", "priceProvider", "llmEnabled",
                    "missingBenchmarks", "adminTokenRequired"),
            "companies": len(companies), "symbols": [c["symbol"] for c in companies],
            "strategyLab": _pick(lab, "runAt", "oosStart", "dataCutoff", "summary")}


def build() -> MCPServer:
    server = MCPServer(name="civalpha", title="CivAlpha", instructions=ABOUT, version="1.0.0")
    tool_read = server.tool(annotations=READ)
    tool_action = server.tool(annotations=ACTION)

    # ------------------------------------------------------------------ resources and prompts
    @server.resource("civalpha://about", name="about", description="What CivAlpha is and how to use its answers", mime_type="text/plain")
    def about() -> str:
        return ABOUT

    @server.resource("civalpha://status", name="status", description="Data sources, data cutoff and universe", mime_type="application/json")
    def status_resource() -> str:
        return json.dumps(status(), default=str)

    @server.prompt(name="investment_review", description="Review one stock with all of CivAlpha's evidence")
    def investment_review(symbol: str) -> str:
        s = symbol.upper().strip()
        return (f"Review {s} using CivAlpha. Call get_company({s}), get_financials, get_exposures, get_dividends, the latest "
                f"forecasts for {s} (get_current_forecasts, get_forecast for the explanation), get_decisions for the AI strategy, "
                "and get_accuracy plus get_strategies for how much the models and strategies can be trusted. Then summarise: "
                "what the filings say, how policy events reach the company, what the models expect and how uncertain that is, "
                "and the evidence for and against. Quote the platform's verdicts and caveats; this is research, not advice.")

    # ------------------------------------------------------------------ overview
    @tool_read
    @_domain
    def get_status() -> dict:
        """Platform overview: forecast target, disclaimers, configured data sources, latest data date, missing benchmark
        prices, universe and the latest strategy-lab summary. Call this first."""
        return status()

    @tool_read
    @_domain
    def list_companies() -> list[dict]:
        """All tracked companies with sector, industry, user-defined tags, benchmark ETF, latest close, dividend summary and
        latest forecasts. Tags are free-form categories set on the Universe page or with set_company_tags."""
        return [{**_pick(c, "symbol", "name", "sector", "industry", "tags", "benchmarkSymbol", "cik", "latestClose", "latestCloseDate", "dividend"),
                 "latestForecasts": {k: _pick(v, "probability", "asOfDate") for k, v in (c.get("latestForecasts") or {}).items()}}
                for c in read.companies()]

    @tool_read
    @_domain
    def investment_candidates() -> dict:
        """Every tracked stock ranked by the evidence CivAlpha has: latest forecast probabilities, the AI strategy's latest
        decision, which classic rules hold it, and dividend status, with the evidence on how trustworthy that is
        (model accuracy, strategy-lab verdicts). A screening aid for research, not a recommendation."""
        decisions = {d["symbol"]: d for d in read.decisions(None).get("decisions", [])}
        acc = (read.accuracy().get("evaluation") or {})
        lab = read.strategies()
        cands = []
        for c in read.companies():
            f = c.get("latestForecasts") or {}
            d = decisions.get(c["symbol"]) or {}
            cands.append({"symbol": c["symbol"], "name": c.get("name"), "sector": c.get("sector"), "tags": c.get("tags") or [],
                          "pBeatSectorAugmented": (f.get("AUGMENTED") or {}).get("probability"),
                          "pBeatSectorBaseline": (f.get("BASELINE") or {}).get("probability"),
                          "aiAction": d.get("action"), "aiProbability": d.get("probability"), "aiRank": d.get("rank"),
                          "rulesHolding": sorted(k for k, v in (d.get("ruleVotes") or {}).items() if v),
                          "dividend": _pick(c.get("dividend") or {}, "status", "trailingYield", "indicatedYield", "yearsPaid")})
        cands.sort(key=lambda x: -(x["aiProbability"] if x["aiProbability"] is not None else x["pBeatSectorAugmented"] or 0))
        return {"universeSize": len(cands), "candidates": cands,
                "evidence": {"accuracy": acc.get("verdict"),
                             "strategyLab": {"summary": (lab.get("run") or {}).get("summary"),
                                             "verdicts": [_pick(r, "strategyKey", "name", "verdict") for r in lab.get("results", [])]}},
                "disclaimers": read.DISCLAIMERS}

    # ------------------------------------------------------------------ companies
    @tool_read
    @_domain
    def get_company(symbol: str) -> dict:
        """One company: identity and ticker history, latest as-filed key facts, price performance vs its sector ETF,
        latest forecasts and the AI strategy's latest decision. `symbol` may be a former ticker (e.g. FB)."""
        c = read.company(symbol)
        bars = read.company_prices(symbol, None).get("bars") or []
        perf = {n: {"stock": _ret(bars, d), "benchmark": _ret(bars, d, "benchmarkClose")} for n, d in (("1m", 21), ("3m", 63), ("12m", 252))}
        latest = next((x.get("latestForecasts") for x in read.companies() if x["symbol"] == c["symbol"]), {})
        dec = next((d for d in read.decisions(None).get("decisions", []) if d.get("symbol") == c["symbol"]), None)
        return {**_pick(c, "symbol", "name", "sector", "industry", "tags", "benchmarkSymbol", "tickerHistory", "cikHistory"),
                "keyFacts": [_pick(f, "label", "value", "unit", "periodEnd", "fiscalPeriod", "formType", "filedDate") for f in c.get("keyFacts", [])],
                "lastClose": bars[-1] if bars else None, "returns": perf, "latestForecasts": latest,
                "aiDecision": None if dec is None else _pick(dec, "asOfDate", "action", "probability", "rank", "explanation")}

    @tool_read
    @_domain
    def get_financials(symbol: str, as_of_date: str | None = None, quarters: int = 8) -> dict:
        """As-filed financial statement series (revenue, gross profit, operating and net income, assets, liabilities,
        debt, cash) with revisions flagged. `as_of_date` (YYYY-MM-DD) shows only what had been filed by then."""
        d = _date(as_of_date, "as_of_date")
        r = read.financials(symbol, f"{d}T21:00:00Z" if d else None)
        n = max(1, min(quarters, 40))
        return {"asOf": r.get("asOf"), "series": [
            {**_pick(s, "concept", "label", "unit"),
             "points": [_pick(p, "periodEnd", "fiscalPeriod", "value", "revised", "originalValue", "formType", "filedDate") for p in s.get("points", [])][-n:]}
            for s in r.get("series", [])]}

    @tool_read
    @_domain
    def get_prices(symbol: str, from_date: str | None = None) -> dict:
        """Price summary since `from_date` (YYYY-MM-DD; default three years): first/last/high/low close, returns of the stock
        and its sector ETF over 1, 3 and 12 months, and corporate actions (splits, dividends)."""
        d = _date(from_date, "from_date")
        p = read.company_prices(symbol, d.isoformat() if d else None)
        bars = p.get("bars") or []
        closes = [b["close"] for b in bars if b.get("close") is not None]
        return {"symbol": p.get("symbol"), "benchmarkSymbol": p.get("benchmarkSymbol"), "bars": len(bars),
                "first": bars[0] if bars else None, "last": bars[-1] if bars else None,
                "high": max(closes) if closes else None, "low": min(closes) if closes else None,
                "returns": {n: {"stock": _ret(bars, k), "benchmark": _ret(bars, k, "benchmarkClose")} for n, k in (("1m", 21), ("3m", 63), ("12m", 252))},
                "corporateActions": p.get("corporateActions", [])[-20:]}

    @tool_read
    @_domain
    def get_dividends(symbol: str) -> dict:
        """Dividend profile: status (regular, special, none, cut), frequency, yield, indicated annual amount, years paid and
        raised, recent payments and the payout ratio from filings."""
        return read.company_dividends(symbol)

    @tool_read
    @_domain
    def get_exposures(symbol: str, as_of_date: str | None = None, limit: int = 20) -> dict:
        """The company's exposures to countries, products and interest rates derived from its SEC filings (basis,
        confidence, supporting passage), and recent policy events that reach it through them."""
        d = _date(as_of_date, "as_of_date")
        r = read.exposures(symbol, f"{d}T21:00:00Z" if d else None)
        n = max(1, min(limit, 100))
        return {"asOf": r.get("asOf"),
                "exposures": [{**_pick(e, "targetType", "targetCode", "channel", "share", "basis", "confidence", "method", "availableAt"),
                               "filing": (e.get("filing") or {}).get("accessionNo"),
                               "passage": ((e.get("passage") or {}).get("text") or "")[:300] or None} for e in r.get("exposures", [])[:n]],
                "eventPaths": [_pick(p, "eventId", "eventTitle", "eventCategory", "eventPublishedAt", "evidenceStatus", "targetType",
                                     "targetCode", "channel", "basis") for p in r.get("paths", [])[:n]]}

    @tool_read
    @_domain
    def list_filings(symbol: str, limit: int = 10) -> list[dict]:
        """The company's most recent SEC filings (10-K, 10-Q, 8-K, amendments) with acceptance times and links."""
        return [_pick(f, "id", "formType", "accessionNo", "periodOfReport", "filedDate", "acceptedAt", "amendsAccession", "url",
                      "passageCount", "factCount") for f in read.company_filings(symbol)[:max(1, min(limit, 50))]]

    @tool_read
    @_domain
    def get_filing(filing_id: int, max_passages: int = 8) -> dict:
        """One filing: metadata, extracted passages by topic (trade, geographic revenue, rates, debt, costs, risk) and a
        sample of its XBRL facts."""
        f = read.filing(filing_id)
        return {**_pick(f, "id", "companySymbol", "formType", "accessionNo", "periodOfReport", "acceptedAt", "url"),
                "passages": [{**_pick(p, "section", "topic"), "text": (p.get("text") or "")[:600]} for p in f.get("passages", [])[:max(0, min(max_passages, 40))]],
                "facts": [_pick(x, "concept", "value", "unit", "periodStart", "periodEnd", "dimensions") for x in f.get("facts", [])[:40]]}

    # ------------------------------------------------------------------ events
    @tool_read
    @_domain
    def list_events(category: str | None = None, limit: int = 25) -> list[dict]:
        """Recent policy events, newest first. `category`: TRADE_TARIFF or MONETARY_POLICY. NEWS_ONLY events are unconfirmed
        reports and do not feed the models until an official source is linked."""
        if category and category not in CATEGORIES:
            raise ToolError(f"category must be one of {', '.join(CATEGORIES)}")
        return [{**_pick(e, "id", "category", "eventType", "title", "eventDate", "publishedAt", "evidenceStatus", "actorName",
                         "sourceCount", "affectedCompanyCount"),
                 "targets": [f"{t['targetType']}:{t['targetCode']}" for t in e.get("targets", [])]}
                for e in events_api.list_events(category)[:max(1, min(limit, 200))]]

    @tool_read
    @_domain
    def get_event(event_id: int) -> dict:
        """One event: summary, attributes (e.g. rate change), targets, sources and the affected companies with the exposure
        path (event target -> company exposure -> filing passage)."""
        e = events_api.get_event(event_id)
        return {**_pick(e, "id", "category", "eventType", "title", "summary", "eventDate", "publishedAt", "evidenceStatus", "attributes",
                        "targets", "version"),
                "actor": _pick(e.get("actor") or {}, "name", "actorType", "authority") or None,
                "sources": [_pick(s, "role", "publisher", "title", "url", "publishedAt") for s in e.get("sources", [])],
                "affectedCompanies": [{"symbol": a["symbol"], "name": a["name"],
                                       "paths": [_pick(p, "targetType", "targetCode", "channel", "basis", "confidence", "share") for p in a["paths"]]}
                                      for a in e.get("affectedCompanies", [])]}

    # ------------------------------------------------------------------ forecasts and models
    @tool_read
    @_domain
    def get_current_forecasts() -> list[dict]:
        """Latest forecasts: probability (with 10-90% interval) that each stock beats its sector ETF over the next 21 trading
        days, per model (BASELINE = prices + fundamentals, AUGMENTED = plus policy events and macro)."""
        return [_pick(f, "id", "symbol", "benchmarkSymbol", "modelKind", "probability", "probLow", "probHigh", "asOfDate", "issueMode",
                      "version", "outcome") for f in read.forecasts_current()]

    @tool_read
    @_domain
    def get_forecast_history(symbol: str | None = None, model_kind: str | None = None, limit: int = 50) -> list[dict]:
        """Past forecasts (newest first) with the realized outcome once the 21-day window has closed."""
        return [_pick(f, "id", "symbol", "modelKind", "probability", "asOfDate", "issueMode", "version", "outcome")
                for f in read.forecasts_history(symbol, model_kind)[:max(1, min(limit, 500))]]

    @tool_read
    @_domain
    def get_forecast(forecast_id: int) -> dict:
        """One forecast with its explanation (each factor's contribution and source documents), features, sources,
        uncertainty note, model version and earlier versions of the same forecast."""
        f = read.forecast(forecast_id)
        expl = f.get("explanation") or {}
        return {**_pick(f, "id", "symbol", "benchmarkSymbol", "modelKind", "probability", "probLow", "probHigh", "asOfDate", "asOf",
                        "issueMode", "target", "uncertaintyNote", "features", "outcome", "version", "reason"),
                "explanation": {"baseRate": expl.get("baseRate"),
                                "factors": [{**_pick(x, "feature", "label", "value", "contribution", "direction"),
                                             "sources": [p.get("label") for p in x.get("provenance", [])][:5]} for x in expl.get("factors", [])]},
                "sources": [_pick(s, "kind", "label", "url") for s in f.get("sources", [])][:15],
                "modelVersion": _pick(f.get("modelVersion") or {}, "algorithm", "trainedThrough", "nSamples"),
                "versions": [_pick(v, "version", "issuedAt", "probability", "reason") for v in f.get("versions", [])]}

    @tool_read
    @_domain
    def get_accuracy() -> dict:
        """Walk-forward out-of-sample evaluation of the forecasting models (Brier score, AUC, calibration, trading
        simulation and the platform's verdict) and the realized accuracy of forecasts actually issued."""
        a = read.accuracy()
        return {"evaluation": _pick(a.get("evaluation") or {}, "runAt", "dataCutoff", "verdict", "metrics", "comparison", "trading", "config"),
                "issuedLive": a.get("issued"), "issuedByMode": a.get("issuedByMode")}

    # ------------------------------------------------------------------ strategies and AI decisions
    @tool_read
    @_domain
    def get_strategies() -> dict:
        """Strategy lab: every classic rule (trend, mean reversion, fundamental, dividend, event) and the AI strategies
        backtested on one out-of-sample window after costs, ranked by Sharpe, each with a multiple-testing-aware verdict.
        AI_CONF (abstention) and AI_SIZED (volatility sizing) act on the same probabilities as AI_GBM; run.aiCoverage is
        the abstention curve: what acting only on the AI's most confident 5%..100% of forecasts earned per position after costs."""
        r = read.strategies()
        keys = ("start", "end", "years", "cagr", "sharpe", "maxDrawdown", "exposure", "trades", "excessReturn", "excessCiLow",
                "excessCiHigh", "deflatedSharpe")
        run = r.get("run") or {}
        return {"run": {**_pick(run, "runAt", "oosStart", "dataCutoff", "summary"),
                        "aiCoverage": (run.get("config") or {}).get("aiCoverage")},
                "results": [{**_pick(s, "strategyKey", "family", "name", "verdict"), "rule": s.get("description"),
                             "metrics": _pick(s.get("metrics") or {}, *keys)} for s in r.get("results", [])]}

    @tool_read
    @_domain
    def get_strategy(key: str, trades: int = 20) -> dict:
        """One strategy (e.g. AI_GBM, MOM_12_1, SMA_50_200): rule, parameters, metrics, yearly returns, cost sensitivity and
        its latest trades."""
        r = read.strategy(key)
        s = r.get("result") or {}
        return {**_pick(s, "strategyKey", "family", "name", "description", "params", "metrics", "yearly", "costSensitivity", "verdict"),
                "tradeCount": r.get("tradeCount"),
                "latestTrades": [_pick(t, "symbol", "entryDate", "exitDate", "tradeReturn", "holdingDays", "entryReason", "exitReason")
                                 for t in r.get("trades", [])[:max(0, min(trades, 200))]]}

    @tool_read
    @_domain
    def get_decisions(as_of_date: str | None = None) -> dict:
        """The AI strategy's decisions for a trading day (default: latest): ENTER/EXIT/HOLD/STAY_OUT per stock with the model
        probability, rank, top factors, which classic rules agree, and the plain-language explanation if any. `sizing` is the
        decision layer: the volatility-scaled weight (AI_SIZED rule) and whether p clears the confident bar (AI_CONF rule)."""
        d = _date(as_of_date, "as_of_date")
        r = read.decisions(d.isoformat() if d else None)
        return {"asOfDate": r.get("asOfDate"), "availableDates": r.get("dates", [])[:10],
                "decisions": [{**_pick(x, "symbol", "name", "action", "probability", "rank", "weight", "entryP", "exitP", "explanation"),
                               "sizing": (x.get("model") or {}).get("sizing"),
                               "topFactors": [_pick(f, "label", "value", "contribution") for f in (x.get("factors") or [])[:3]],
                               "rulesHolding": sorted(k for k, v in (x.get("ruleVotes") or {}).items() if v)}
                              for x in r.get("decisions", [])]}

    # ------------------------------------------------------------------ earnings
    @tool_read
    @_domain
    def get_earnings(symbol: str, announcements: int = 8) -> dict:
        """Earnings announcements for a company (results 8-Ks, Item 2.02): when each became public, the stock's excess
        return over its sector ETF in the session it first traded, the guidance tone read from the press release
        (RAISED / LOWERED / MAINTAINED / PROVIDED / NONE, a keyword estimate with the sentence as evidence), and the
        estimated next announcement date. Former tickers resolve."""
        r = read.company_earnings(symbol, max(1, min(announcements, 100)))
        return {**_pick(r, "symbol", "asOf", "announcementCount", "nextEstimate", "note"),
                "announcements": [_pick(a, "acceptedAt", "sessionDate", "reaction", "guidanceTone", "guidanceText", "exhibitUrl") for a in r["announcements"]]}

    # ------------------------------------------------------------------ insiders
    @tool_read
    @_domain
    def get_insiders(symbol: str, transactions: int = 20) -> dict:
        """Insider transactions (SEC Forms 4) for a company: open-market buying and selling by officers, directors and 10%
        owners over the last 21, 63 and 252 trading days (distinct buyers and sellers, net dollar value) and the latest
        transactions with their code (P purchase, S sale; grants, exercises and gifts carry no signal). Former tickers resolve."""
        r = read.company_insiders(symbol, max(1, min(transactions, 200)))
        return {**_pick(r, "symbol", "windows", "transactionCount", "newestAvailableAt", "note"),
                "transactions": [_pick(t, "ownerName", "relationship", "title", "transDate", "filedDate", "transCode", "codeLabel",
                                       "acquired", "shares", "price", "value", "sharesAfter", "signal") for t in r["transactions"]]}

    # ------------------------------------------------------------------ signal health
    @tool_read
    @_domain
    def get_signal_health(decaying_only: bool = False) -> dict:
        """Signal health: for every model input (prices, filed fundamentals, policy shocks, dividends, insiders, earnings)
        the monthly information coefficient against the 21-day excess return over the sector ETF: mean IC with its
        t-statistic over months, IC information ratio, share of months with the right sign, the last 12 months against the
        earlier ones, a grade corrected for the number of features tested, and a DECAYING flag when an input lost its sign
        recently. This is how the platform notices an edge fading. Quote grades, not raw ICs."""
        r = read.signal_study()
        run = r.get("run")
        if not run:
            return {"run": None, "note": "No signal-health study yet: run_signal_study makes one (it also runs with every pipeline run)."}
        res = run["result"]
        rows = [_pick(f, "feature", "label", "kind", "months", "meanIc", "icIr", "tStat", "signHitRate", "recentIc", "earlierIc", "trend",
                      "decaying", "grade", "verdict") for f in res["features"] if not decaying_only or f["decaying"]]
        return {"runId": run["id"], "runAt": run["runAt"], "dataCutoff": res["dataCutoff"], "headline": res["headline"],
                "tests": res["config"]["tests"], "bonferroniZ": res["config"]["bonferroniZ"], "features": rows, "disclaimers": res["disclaimers"]}

    # ------------------------------------------------------------------ setup playbook
    @tool_read
    @_domain
    def get_setup_playbook(horizon: int = 21, fresh_only: bool = True) -> dict:
        """The setup playbook: catalysts and technical situations a trader waits for (earnings surprise becoming public,
        dividend raise or cut, 52-week breakout or low, golden/death cross, oversold pullback, crash, volume surge,
        tariff or rate shock), each scored on the excess return over the sector ETF that followed within `horizon`
        trading days (5, 21 or 63): how often it fired, hit rate vs the base rate, mean excess with a bootstrap interval,
        payoff asymmetry, and a verdict corrected for the number of setups tested. `today` lists which setups fired in
        the last sessions on which tracked stocks: the daily scan. Quote the verdict grades, never a base rate alone."""
        r = read.setup_study()
        run = r.get("run")
        if not run:
            return {"run": None, "note": "No setup playbook yet: run_setup_study makes one (it also runs with every pipeline run)."}
        res = run["result"]
        h = str(horizon)
        if h not in res["base"]:
            raise ToolError(f"horizon must be one of {', '.join(res['base'])}")
        rows = [{**_pick(s, "key", "family", "name", "trigger", "triggers"),
                 **_pick(s["horizons"][h], "n", "hitRate", "lift", "meanExcess", "medianExcess", "payoff", "ciLow", "ciHigh", "z", "grade", "verdict")}
                for s in res["setups"]]
        rows.sort(key=lambda x: -(x.get("z") or -99))
        today = [t for t in res["today"]["setups"] if t["stocks"]] if fresh_only else res["today"]["setups"]
        return {"runId": run["id"], "runAt": run["runAt"], "dataCutoff": res["dataCutoff"], "headline": res["headline"],
                "horizon": horizon, "base": res["base"][h], "tests": res["config"]["tests"], "bonferroniZ": res["config"]["bonferroniZ"],
                "setups": rows, "today": {"asOfDate": res["today"]["asOfDate"], "freshDays": res["today"]["freshDays"], "setups": today},
                "disclaimers": res["disclaimers"]}

    # ------------------------------------------------------------------ doubler study
    @tool_read
    @_domain
    def get_doubler_study(horizon: int = 63, episodes: int = 20) -> dict:
        """Doubler study: how often a tracked stock doubled (+100% at the best close) within `horizon` trading days (21, 42 or
        63), the list of such episodes, the profile of those stock-days, how often the point-in-time screen (volatile small
        cap on a breakout or volume spike) finds them versus chance, and which stocks the screen flags today. Rates are
        counts of history with block-bootstrap intervals, not odds for any stock; the loss rate sits next to every hit rate."""
        r = read.doubler_study()
        run = r.get("run")
        if not run:
            return {"run": None, "note": "No doubler study yet: run_doubler_study makes one (it also runs with every pipeline run)."}
        res = run.get("result") or {}
        h = (res.get("horizons") or {}).get(str(horizon))
        if h is None:
            raise Problem(f"horizon must be one of {list((res.get('horizons') or {}).keys())}")
        keys = ("n", "hits", "hitRate", "hitCiLow", "hitCiHigh", "lossRate", "medianEndReturn", "meanEndReturn", "medianMaxReturn")
        return {"runId": run.get("id"), "runAt": run.get("runAt"), "dataCutoff": res.get("dataCutoff"), "start": res.get("start"),
                "years": res.get("years"), "universeSize": res.get("universeSize"), "stockDays": res.get("stockDays"),
                "headline": res.get("headline"), "horizon": horizon, "verdict": h.get("verdict"), "lift": h.get("lift"),
                "baseRate": _pick(h.get("base") or {}, *keys), "screen": _pick(h.get("screen") or {}, *keys),
                "controlSameVolatilityNoTrigger": _pick(h.get("control") or {}, *keys),
                "byYear": h.get("byYear"), "companiesWithHits": h.get("companiesWithHits"),
                "episodes": (h.get("episodes") or [])[-max(0, min(episodes, 200)):],
                "profile": [{**_pick(f, "feature", "label", "n", "medianAll", "medianHits")} for f in h.get("profile") or []],
                "screenRule": res.get("screenRule"),
                "today": {"asOfDate": (res.get("today") or {}).get("asOfDate"),
                          "flagged": [_pick(x, "symbol", "name", "conditions", "features") for x in (res.get("today") or {}).get("stocks", []) if x.get("fires")],
                          "notFlagged": [x.get("symbol") for x in (res.get("today") or {}).get("stocks", []) if not x.get("fires")]},
                "disclaimers": res.get("disclaimers")}

    # ------------------------------------------------------------------ time machine
    @tool_read
    @_domain
    def list_time_machine_runs() -> list[dict]:
        """Past time-machine runs: forecasts made as of a past date and scored against what happened."""
        return [_pick(r, "id", "asOfDate", "runAt", "dataCutoff", "headline") for r in read.time_machine_runs()]

    @tool_read
    @_domain
    def get_time_machine_run(run_id: int, horizon: int = 21) -> dict:
        """One time-machine run: per-horizon scores (odds hit rate and Brier vs base rate, AI picks vs all stocks, 10-90% band
        coverage) and per-stock prediction vs actual for `horizon` (5, 10, 21 or 63 trading days)."""
        r = read.time_machine_run(run_id)
        res = r.get("result") or {}
        h = str(horizon)
        stocks = []
        for s in res.get("stocks", []):
            act = (s.get("actual") or {}).get(h)
            rng = (s.get("range") or {}).get(h) or {}
            stocks.append({"symbol": s.get("symbol"), "aiAction": (s.get("ai") or {}).get("action"),
                           "pBeatSector": ((s.get("odds") or {}).get(h) or {}).get("AUGMENTED"),
                           "band": [rng.get("q10"), rng.get("q90")] if rng else None,
                           "actualReturn": act and act.get("stockReturn"), "actualVsSector": act and act.get("excess")})
        return {**_pick(r, "id", "asOfDate", "runAt", "dataCutoff", "headline"), "horizons": res.get("horizons"),
                "summary": res.get("summary"), "horizon": horizon, "stocks": stocks}

    # ------------------------------------------------------------------ jobs (admin-token rule)
    @tool_read
    @_domain
    def list_jobs(ctx: Context, limit: int = 10) -> list[dict]:
        """Recent background jobs (pipeline runs, price updates, backtests, time machine...) with their status."""
        require_admin(ctx)
        return [_pick(j, "id", "jobType", "status", "startedAt", "finishedAt") for j in admin.jobs()[:max(1, min(limit, 30))]]

    @tool_read
    @_domain
    def get_job(ctx: Context, job_id: int, wait_seconds: int = 0) -> dict:
        """Status and log tail of one job; optionally wait up to `wait_seconds` (max 600) for it to finish."""
        require_admin(ctx)
        j = Jobs().get(job_id)
        if j is None:
            raise ToolError(f"job {job_id} not found")
        return _wait(camel(j), wait_seconds)

    def _action(ctx, submit, wait_seconds: int) -> dict:
        require_admin(ctx)
        return _wait(submit(), wait_seconds)

    @tool_action
    @_domain
    def run_pipeline(ctx: Context, wait_seconds: int = 0) -> dict:
        """Run the full pipeline: refresh prices, SEC filings, macro data and policy events, then evaluate the models, issue
        forecasts, run the strategy lab and record the AI's decisions. Takes several minutes; follow it with get_job."""
        return _action(ctx, admin.pipeline_run, wait_seconds)

    @tool_action
    @_domain
    def update_prices(ctx: Context, wait_seconds: int = 0) -> dict:
        """Download new daily prices, dividends and splits from the configured provider (only what is missing)."""
        return _action(ctx, admin.price_sync, wait_seconds)

    @tool_action
    @_domain
    def ingest_sec_filings(ctx: Context, symbol: str, wait_seconds: int = 0) -> dict:
        """Fetch new SEC filings for one company (XBRL facts, passages, exposures)."""
        return _action(ctx, lambda: admin.sec_ingest(admin.SecIn(symbol=symbol)), wait_seconds)

    @tool_action
    @_domain
    def add_company(ctx: Context, symbol: str, name: str | None = None, cik: str | None = None, sector: str | None = None,
                    industry: str | None = None, benchmark_symbol: str | None = None, member_since: str | None = None,
                    ingest_sec: bool = True, sync_prices: bool = True, tags: list[str] | None = None) -> dict:
        """Add a US-listed company to the research universe (same as the Universe page's "Add company"). Only `symbol` is
        required: a missing name, CIK, sector, industry or benchmark ETF is filled in from SEC EDGAR (the sector is suggested
        from the SIC code; the answer says what was filled and whether the suggestion needs review). `sector` is one of
        Technology, Health Care, Financials, Consumer Discretionary, Consumer Staples, Communication Services, Industrials,
        Energy, Materials, Utilities, Real Estate; `benchmark_symbol` defaults to that sector's ETF. `member_since`
        (YYYY-MM-DD) defaults to today. `tags` are optional user-defined categories (e.g. ["AI", "China exposed"]) that
        group stocks on the Companies page; they do not affect the models. By default an SEC ingest and a price sync are
        queued so filings and prices arrive; follow them with get_job. The company has no forecasts until the pipeline has run."""
        require_admin(ctx)
        sym = (symbol or "").upper().strip()
        if not sym:
            raise ToolError("symbol is required")
        filled: dict = {}
        notes: list[str] = []
        if not (name and cik and sector):
            p = CompanyProfiler(db(), universe_api._lookup).profile(sym)
            if p.existing:
                state = "an active member" if p.existing["active"] else "a removed member; restore it from the Universe page"
                raise ToolError(f"{p.existing['symbol']} is already in the database (company {p.existing['companyId']}, {state})")
            notes.extend(p.warnings)
            given = {"name": name, "cik": cik, "sector": sector, "industry": industry}
            found = {"name": p.name, "cik": p.cik, "sector": p.sector, "industry": p.industry}
            filled = {k: v for k, v in found.items() if v and not given[k]}
            name, cik = name or p.name, cik or p.cik
            if not sector and p.sector:
                sector, industry = p.sector, industry or p.industry
                if p.sector_confidence == "review":
                    notes.append(f"{p.sector_note}; alternatives: " + ", ".join(a["sector"] for a in p.sector_alternatives))
            if not name or not cik:
                raise ToolError(f"EDGAR has no name/CIK for {sym}: " + "; ".join(notes or ["pass name and cik yourself"]))
            if not sector:
                raise ToolError(f"no sector could be suggested for {sym}; pass sector (and benchmark_symbol): " + "; ".join(notes))
        bench = (benchmark_symbol or sectors.SECTOR_ETF.get(sector) or "").upper().strip()
        if not bench:
            raise ToolError(f"unknown sector {sector!r}; use one of {', '.join(sectors.SECTOR_ETF)} or pass benchmark_symbol")
        if not benchmark_symbol:
            filled["benchmarkSymbol"] = bench
        body = universe_api.AddIn(symbol=sym, name=name, cik=cik, sector=sector, industry=industry, benchmarkSymbol=bench,
                                  memberSince=_date(member_since, "member_since"), ingestSec=ingest_sec, syncPrices=sync_prices,
                                  tags=tags)
        out = universe_api.add(body)
        return {"id": out["id"], "symbol": out["symbol"], "name": name, "cik": cik, "sector": sector, "industry": industry,
                "benchmarkSymbol": bench, "tags": UniverseService().tags_of(out["id"]), "filledFromSec": filled, "notes": notes,
                "jobs": [_job(j, 5) for j in out["jobs"]], "nextSteps": out["nextSteps"]}

    @tool_read
    @_domain
    def discover_companies(min_public_float_usd: float = 2e9, exchanges: list[str] | None = None, limit: int = 50) -> dict:
        """Find US-listed companies not yet tracked, from SEC data alone: listed on `exchanges` (default Nasdaq and NYSE)
        with a reported public float (10-K, market value held by non-affiliates) of at least `min_public_float_usd`,
        largest first. This is the breadth lever: a signal that is invisible on 40 stocks may be measurable on 400. The
        answer carries the candidates to pass to expand_universe and notes on the price-provider quota and pipeline time."""
        r = universe_api.discover(",".join(exchanges or universe_api.DEFAULT_EXCHANGES), min_public_float_usd, limit)
        return {**_pick(r, "matched", "listed", "alreadyTracked", "frames", "notes"),
                "candidates": [_pick(c, "symbol", "name", "exchange", "publicFloat", "floatAsOf", "cik") for c in r["candidates"]]}

    @tool_action
    @_domain
    def expand_universe(ctx: Context, min_public_float_usd: float = 2e9, exchanges: list[str] | None = None, limit: int = 50,
                        tag: str | None = None, symbols: list[str] | None = None, ingest_sec: bool = False,
                        sync_prices: bool = True, wait_seconds: int = 0) -> dict:
        """Add many companies at once (the Universe page's "Expand"): the discover_companies result for these filters, or
        only the listed `symbols` among them, each with a sector suggested from its SIC code; an ambiguous code adds the
        company under the likeliest sector plus the tag "sector review". `tag` labels the whole batch (e.g. "nasdaq-large").
        One job does the adding; `sync_prices` queues a price sync, which a provider quota may spread over several runs.
        Filings arrive with the next pipeline run (hours for a large batch); `ingest_sec` ingests them inline instead, which
        blocks the worker queue for as long. Follow the job with get_job."""
        require_admin(ctx)
        d = universe_api.discover(",".join(exchanges or universe_api.DEFAULT_EXCHANGES), min_public_float_usd, limit)
        cands = d["candidates"]
        if symbols:
            want = {x.upper().strip() for x in symbols}
            cands = [c for c in cands if c["symbol"] in want]
            missing = sorted(want - {c["symbol"] for c in cands})
            if missing:
                raise ToolError(f"not among the discovered candidates: {', '.join(missing)} (use add_company for a single stock)")
        if not cands:
            raise ToolError("no new companies match these filters")
        body = universe_api.ExpandIn(candidates=cands, tag=tag, ingestSec=ingest_sec, syncPrices=sync_prices)
        out = universe_api.expand(body)
        return {"count": out["count"], "symbols": [c["symbol"] for c in cands], "notes": out["notes"],
                "job": _wait(out["job"], wait_seconds)}

    @tool_action
    @_domain
    def set_company_tags(ctx: Context, symbol: str, tags: list[str]) -> dict:
        """Replace a company's user-defined tags (free-form categories such as "AI", "China exposed" or "watch only";
        at most 20, each up to 40 characters of letters, digits, spaces and _ . & / + -). Pass an empty list to clear
        them. Tags group stocks on the Companies page and in list_companies; they never change a forecast. `symbol`
        may be a former ticker."""
        require_admin(ctx)
        cid = read.resolve(symbol)
        u = UniverseService()
        return {"symbol": TickerResolver().current_symbol(cid), "tags": u.set_tags(cid, tags)}

    @tool_action
    @_domain
    def evaluate_models(ctx: Context, wait_seconds: int = 0) -> dict:
        """Re-run the walk-forward evaluation of the forecasting models."""
        return _action(ctx, admin.evaluate, wait_seconds)

    @tool_action
    @_domain
    def issue_forecasts(ctx: Context, as_of_date: str | None = None, wait_seconds: int = 0) -> dict:
        """Issue forecasts now, or for a past as-of date (published as REPLAY, using only data known then)."""
        d = _date(as_of_date, "as_of_date")
        return _action(ctx, lambda: admin.issue_forecasts(admin.DateIn(asOfDate=d)), wait_seconds)

    @tool_action
    @_domain
    def run_strategy_backtest(ctx: Context, wait_seconds: int = 0) -> dict:
        """Backtest every strategy (classic rules and AI) and store a new strategy-lab run."""
        return _action(ctx, admin.strategy_backtest, wait_seconds)

    @tool_action
    @_domain
    def make_ai_decisions(ctx: Context, as_of_date: str | None = None, wait_seconds: int = 0) -> dict:
        """Record the AI strategy's decisions for the latest trading day (or a given date)."""
        d = _date(as_of_date, "as_of_date")
        return _action(ctx, lambda: admin.strategy_decide(admin.DateIn(asOfDate=d)), wait_seconds)

    @tool_action
    @_domain
    def ingest_earnings(ctx: Context, wait_seconds: int = 0) -> dict:
        """Read the press-release exhibit of every results 8-K not yet read and classify its guidance tone (also part of
        every pipeline run). Read them with get_earnings."""
        return _action(ctx, admin.earnings_ingest, wait_seconds)

    @tool_action
    @_domain
    def ingest_insiders(ctx: Context, wait_seconds: int = 0) -> dict:
        """Load insider transactions (Forms 4) for every tracked company from the SEC's quarterly data sets plus each
        company's recent filings (also part of every pipeline run). Read them with get_insiders."""
        return _action(ctx, admin.insider_ingest, wait_seconds)

    @tool_action
    @_domain
    def run_signal_study(ctx: Context, wait_seconds: int = 0) -> dict:
        """Run the signal-health study (monthly IC per model input) on the stored data and store it. Read it with
        get_signal_health."""
        return _action(ctx, admin.signal_study, wait_seconds)

    @tool_action
    @_domain
    def run_setup_study(ctx: Context, wait_seconds: int = 0) -> dict:
        """Run the setup playbook study on the stored prices, filings, dividends and events and store it. Read it with
        get_setup_playbook."""
        return _action(ctx, admin.setup_study, wait_seconds)

    @tool_action
    @_domain
    def run_doubler_study(ctx: Context, wait_seconds: int = 0) -> dict:
        """Run the doubler study on the stored prices and filings and store it. Read it with get_doubler_study."""
        return _action(ctx, admin.doubler_study, wait_seconds)

    @tool_action
    @_domain
    def run_time_machine(ctx: Context, as_of_date: str, wait_seconds: int = 0) -> dict:
        """Forecast as of a past date with only the data known then, then score the forecasts against what happened 5, 10,
        21 and 63 trading days later. Read the result with list_time_machine_runs / get_time_machine_run."""
        d = _date(as_of_date, "as_of_date")
        return _action(ctx, lambda: admin.time_machine(admin.DateIn(asOfDate=d)), wait_seconds)

    @tool_action
    @_domain
    def resolve_outcomes(ctx: Context, wait_seconds: int = 0) -> dict:
        """Score issued forecasts whose 21-trading-day window has closed."""
        return _action(ctx, admin.resolve_outcomes, wait_seconds)

    return server


# --------------------------------------------------------------------------- HTTP mount (inside the API process)
server = build()
_running = False


def _security() -> TransportSecuritySettings:
    s = settings().mcp
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", *s.allowed_hosts]
    origins = ["http://localhost", "http://localhost:*", "http://127.0.0.1", "http://127.0.0.1:*", *s.allowed_origins]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


@contextlib.asynccontextmanager
async def lifespan():
    """Runs the MCP transport for the lifetime of the API application (a fresh session manager each start: the SDK
    allows one run per manager)."""
    global _running
    server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=_security())
    async with server.session_manager.run():
        _running = True
        try:
            yield
        finally:
            _running = False


class _Endpoint:
    async def __call__(self, scope, receive, send):
        if not _running:
            body = b'{"error":"the MCP transport is not running yet"}'
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await server.session_manager.handle_request(scope, receive, send)


def mount(app) -> None:
    app.router.routes.append(Route("/mcp", _Endpoint(), methods=["GET", "POST", "DELETE"]))


if __name__ == "__main__":
    server.run("stdio")    # local operator: python -m civalpha.platform.mcp_server
