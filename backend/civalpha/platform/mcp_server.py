"""CivAlpha MCP server: lets Claude (or any MCP client) read the platform and start its jobs.

Runs as the `mcp` service (streamable HTTP, stateless) behind nginx at http://localhost:8088/mcp:

    claude mcp add --transport http civalpha http://localhost:8088/mcp

It is a thin client of the REST API (CIVALPHA_API_URL, default http://api:8000), so it sees exactly what the UI
sees. Read tools condense API responses for a language model (summaries instead of long series). Action tools queue
the same jobs as the Data & pipeline page and can optionally wait for them. When CIVALPHA_ADMIN_TOKEN is set, every
MCP request must carry it (X-Admin-Token or Authorization: Bearer), like the admin API.

Run: uvicorn civalpha.platform.mcp_server:app --host 0.0.0.0 --port 8001
"""
from __future__ import annotations

import hmac
import os
import time
from datetime import date
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .settings import settings

API_URL = os.environ.get("CIVALPHA_API_URL", "http://api:8000").rstrip("/")
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
ACTION = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
ACTIVE = ("QUEUED", "RUNNING")
MAX_WAIT = 600

INSTRUCTIONS = """CivAlpha is a research platform on real data for US-listed stocks: SEC filings (XBRL facts, passages,
exposures), daily prices, macro data and official trade/tariff and monetary-policy events. It forecasts the probability
that a stock beats its sector benchmark ETF over 21 trading days, backtests classic and AI trading strategies, records
the AI strategy's daily ENTER/EXIT/HOLD/STAY_OUT decisions, and can replay forecasts from a past date (time machine).
It is research software, not investment advice: always quote the verdicts and uncertainty the tools return, and do
not claim profitability the platform itself does not support. Start with platform_status. Action tools queue
background jobs; use job_status to follow them, or pass wait_seconds to wait for the result."""


class ApiError(ToolError):
    """An error whose message is shown to the model (API errors, bad arguments, API unreachable)."""


class Api:
    """REST client for the CivAlpha API (sends the admin token when one is configured)."""

    def __init__(self, base_url: str = API_URL, token: str | None = None, transport: httpx.BaseTransport | None = None):
        token = settings().admin_token if token is None else token
        headers = {"X-Admin-Token": token} if token else {}
        self.http = httpx.Client(base_url=base_url, headers=headers, timeout=httpx.Timeout(120.0, connect=10.0), transport=transport)

    def _check(self, r: httpx.Response) -> Any:
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("error") or body.get("detail") or r.text
            except ValueError:
                msg = r.text
            raise ApiError(f"CivAlpha API {r.status_code}: {msg}")
        return r.json()

    def _send(self, method: str, path: str, **kw) -> Any:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ApiError(f"cannot reach the CivAlpha API at {self.http.base_url} ({type(e).__name__}); is the stack running?") from e
        return self._check(r)

    def get(self, path: str, **params) -> Any:
        return self._send("GET", path, params={k: v for k, v in params.items() if v not in (None, "")})

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._send("POST", path, json=body or {})


def _date(s: str | None, name: str) -> str | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as e:
        raise ApiError(f"{name} must be a date like 2026-06-30") from e


def _pick(d: dict, *keys: str) -> dict:
    return {k: d.get(k) for k in keys if k in d}


def _ret(bars: list[dict], days: int) -> float | None:
    if len(bars) <= days or not bars[-1].get("close") or not bars[-1 - days].get("close"):
        return None
    return round(bars[-1]["close"] / bars[-1 - days]["close"] - 1, 4)


def _job(j: dict, log_lines: int = 15) -> dict:
    lines = (j.get("log") or "").strip().splitlines()
    return {**_pick(j, "id", "jobType", "status", "startedAt", "finishedAt", "params"), "logTail": lines[-log_lines:]}


def build(api: Api | None = None) -> MCPServer:
    api = api or Api()
    server = MCPServer(name="civalpha", title="CivAlpha", instructions=INSTRUCTIONS, version="1.0.0")

    def wait(job: dict, wait_seconds: int) -> dict:
        deadline = time.monotonic() + max(0, min(int(wait_seconds or 0), MAX_WAIT))
        while job.get("status") in ACTIVE and time.monotonic() < deadline:
            time.sleep(2)
            job = next((j for j in api.get("/api/admin/jobs") if j["id"] == job["id"]), job)
        out = _job(job)
        if out["status"] in ACTIVE:
            out["note"] = f"still {out['status'].lower()}; call job_status({job['id']}) to follow it"
        return out

    # ------------------------------------------------------------------ overview
    @server.tool(annotations=READ)
    def platform_status() -> dict:
        """Platform overview: configured data sources, latest data date, missing benchmark prices, universe size,
        latest strategy-lab summary and the most recent background jobs. Call this first."""
        meta = api.get("/api/meta")
        companies = api.get("/api/companies")
        lab = api.get("/api/strategies").get("run") or {}
        jobs = api.get("/api/admin/jobs")[:5]
        return {"meta": _pick(meta, "dataCutoff", "secConfigured", "fredEnabled", "priceProvider", "llmEnabled", "missingBenchmarks",
                              "adminTokenRequired", "target", "disclaimers"),
                "companies": len(companies), "symbols": [c["symbol"] for c in companies],
                "strategyLab": _pick(lab, "runAt", "oosStart", "dataCutoff", "summary"),
                "recentJobs": [_pick(j, "id", "jobType", "status", "startedAt", "finishedAt") for j in jobs]}

    @server.tool(annotations=READ)
    def list_companies() -> list[dict]:
        """All tracked companies with sector, benchmark ETF, latest close and latest forecast probabilities."""
        return [{**_pick(c, "symbol", "name", "sector", "benchmarkSymbol", "cik", "latestClose", "latestCloseDate"),
                 "latestForecasts": {k: _pick(v, "probability", "asOfDate") for k, v in (c.get("latestForecasts") or {}).items()}}
                for c in api.get("/api/companies")]

    # ------------------------------------------------------------------ companies
    @server.tool(annotations=READ)
    def company_profile(symbol: str) -> dict:
        """One company: identity and ticker history, latest as-filed key facts, price performance vs its sector ETF,
        latest forecasts and the AI strategy's latest decision for it. `symbol` may be a former ticker (e.g. FB)."""
        c = api.get(f"/api/companies/{symbol}")
        p = api.get(f"/api/companies/{symbol}/prices")
        bars = p.get("bars") or []
        bench = [{"close": b.get("benchmarkClose")} for b in bars]
        perf = {f"{n}": {"stock": _ret(bars, d), "benchmark": _ret(bench, d)} for n, d in (("1m", 21), ("3m", 63), ("12m", 252))}
        latest = next((f for f in [x for x in api.get("/api/companies") if x["symbol"] == c["symbol"]]), {}).get("latestForecasts", {})
        dec = next((d for d in api.get("/api/decisions").get("decisions", []) if d.get("symbol") == c["symbol"]), None)
        return {**_pick(c, "symbol", "name", "sector", "industry", "benchmarkSymbol", "tickerHistory", "cikHistory"),
                "keyFacts": [_pick(f, "label", "value", "unit", "periodEnd", "fiscalPeriod", "formType", "filedDate") for f in c.get("keyFacts", [])],
                "lastClose": bars[-1] if bars else None, "returns": perf, "latestForecasts": latest,
                "aiDecision": None if dec is None else _pick(dec, "asOfDate", "action", "probability", "rank", "explanation")}

    @server.tool(annotations=READ)
    def company_financials(symbol: str, as_of: str | None = None, quarters: int = 8) -> dict:
        """As-filed financial statement series (revenue, gross profit, operating and net income, assets, liabilities,
        debt, cash) with revisions flagged. `as_of` (YYYY-MM-DD) shows only what had been filed by then."""
        r = api.get(f"/api/companies/{symbol}/financials", asOf=f"{_date(as_of, 'as_of')}T21:00:00Z" if as_of else None)
        return {"asOf": r.get("asOf"), "series": [
            {**_pick(s, "concept", "label", "unit"),
             "points": [_pick(p, "periodEnd", "fiscalPeriod", "value", "revised", "originalValue", "formType", "filedDate")
                        for p in s.get("points", [])][-max(1, min(quarters, 40)):]} for s in r.get("series", [])]}

    @server.tool(annotations=READ)
    def company_exposures(symbol: str, as_of: str | None = None, limit: int = 20) -> dict:
        """The company's exposures to countries, products and interest rates derived from its SEC filings (with basis,
        confidence and the supporting passage), and recent policy events that reach it through those exposures."""
        r = api.get(f"/api/companies/{symbol}/exposures", asOf=f"{_date(as_of, 'as_of')}T21:00:00Z" if as_of else None)
        n = max(1, min(limit, 100))
        return {"asOf": r.get("asOf"),
                "exposures": [{**_pick(e, "targetType", "targetCode", "channel", "share", "basis", "confidence", "method", "availableAt"),
                               "filing": (e.get("filing") or {}).get("accessionNo"),
                               "passage": ((e.get("passage") or {}).get("text") or "")[:300] or None} for e in r.get("exposures", [])[:n]],
                "eventPaths": [_pick(p, "eventId", "eventTitle", "eventCategory", "eventPublishedAt", "evidenceStatus", "targetType",
                                     "targetCode", "channel", "basis") for p in r.get("paths", [])[:n]]}

    @server.tool(annotations=READ)
    def company_filings(symbol: str, limit: int = 10) -> list[dict]:
        """The company's most recent SEC filings (10-K, 10-Q, 8-K, amendments) with acceptance times and links."""
        return [_pick(f, "id", "formType", "accessionNo", "periodOfReport", "filedDate", "acceptedAt", "amendsAccession", "url",
                      "passageCount", "factCount") for f in api.get(f"/api/companies/{symbol}/filings")[:max(1, min(limit, 50))]]

    @server.tool(annotations=READ)
    def filing_detail(filing_id: int, max_passages: int = 8) -> dict:
        """One filing: metadata, extracted passages by topic (trade, geographic revenue, rates, debt, costs, risk) and
        a sample of its XBRL facts."""
        f = api.get(f"/api/filings/{filing_id}")
        return {**_pick(f, "id", "companySymbol", "formType", "accessionNo", "periodOfReport", "acceptedAt", "url"),
                "passages": [{**_pick(p, "section", "topic"), "text": (p.get("text") or "")[:600]}
                             for p in f.get("passages", [])[:max(0, min(max_passages, 40))]],
                "facts": [_pick(x, "concept", "value", "unit", "periodStart", "periodEnd", "dimensions") for x in f.get("facts", [])[:40]]}

    # ------------------------------------------------------------------ events
    @server.tool(annotations=READ)
    def list_events(category: str | None = None, limit: int = 25) -> list[dict]:
        """Recent policy events, newest first. `category`: TRADE_TARIFF or MONETARY_POLICY. NEWS_ONLY events are
        unconfirmed reports and do not feed the models until an official source is linked."""
        return [{**_pick(e, "id", "category", "eventType", "title", "eventDate", "publishedAt", "evidenceStatus", "actorName",
                         "sourceCount", "affectedCompanyCount"),
                 "targets": [f"{t['targetType']}:{t['targetCode']}" for t in e.get("targets", [])]}
                for e in api.get("/api/events", category=category)[:max(1, min(limit, 200))]]

    @server.tool(annotations=READ)
    def event_detail(event_id: int) -> dict:
        """One event: summary, attributes (e.g. rate change), targets, sources and the companies it affects with the
        exposure path (event target -> company exposure -> filing passage)."""
        e = api.get(f"/api/events/{event_id}")
        return {**_pick(e, "id", "category", "eventType", "title", "summary", "eventDate", "publishedAt", "evidenceStatus", "attributes",
                        "targets", "version"),
                "actor": _pick(e.get("actor") or {}, "name", "actorType", "authority") or None,
                "sources": [_pick(s, "role", "publisher", "title", "url", "publishedAt") for s in e.get("sources", [])],
                "affectedCompanies": [{"symbol": a["symbol"], "name": a["name"],
                                       "paths": [_pick(p, "targetType", "targetCode", "channel", "basis", "confidence", "share") for p in a["paths"]]}
                                      for a in e.get("affectedCompanies", [])]}

    # ------------------------------------------------------------------ forecasts and models
    @server.tool(annotations=READ)
    def current_forecasts() -> list[dict]:
        """Latest forecasts: probability (with 10-90% interval) that each stock beats its sector ETF over the next 21
        trading days, per model (BASELINE = prices + fundamentals, AUGMENTED = plus policy events and macro)."""
        return [_pick(f, "id", "symbol", "benchmarkSymbol", "modelKind", "probability", "probLow", "probHigh", "asOfDate", "issueMode",
                      "version", "outcome") for f in api.get("/api/forecasts/current")]

    @server.tool(annotations=READ)
    def forecast_history(symbol: str | None = None, model_kind: str | None = None, limit: int = 50) -> list[dict]:
        """Past forecasts (newest first) with their realized outcome once the 21-day window has closed."""
        return [_pick(f, "id", "symbol", "modelKind", "probability", "asOfDate", "issueMode", "version", "outcome")
                for f in api.get("/api/forecasts/history", symbol=symbol, modelKind=model_kind)[:max(1, min(limit, 500))]]

    @server.tool(annotations=READ)
    def forecast_detail(forecast_id: int) -> dict:
        """One forecast with its explanation (each factor's contribution and its source documents), features, sources,
        uncertainty note, model version and earlier versions of the same forecast."""
        f = api.get(f"/api/forecasts/{forecast_id}")
        expl = f.get("explanation") or {}
        return {**_pick(f, "id", "symbol", "benchmarkSymbol", "modelKind", "probability", "probLow", "probHigh", "asOfDate", "asOf",
                        "issueMode", "target", "uncertaintyNote", "features", "outcome", "version", "reason"),
                "explanation": {"baseRate": expl.get("baseRate"),
                                "factors": [{**_pick(x, "feature", "label", "value", "contribution", "direction"),
                                             "sources": [p.get("label") for p in x.get("provenance", [])][:5]} for x in expl.get("factors", [])]},
                "sources": [_pick(s, "kind", "label", "url") for s in f.get("sources", [])][:15],
                "modelVersion": _pick(f.get("modelVersion") or {}, "algorithm", "trainedThrough", "nSamples"),
                "versions": [_pick(v, "version", "issuedAt", "probability", "reason") for v in f.get("versions", [])]}

    @server.tool(annotations=READ)
    def model_accuracy() -> dict:
        """Walk-forward out-of-sample evaluation of the forecasting models (Brier score, AUC, calibration, trading
        simulation and the platform's verdict) and the realized accuracy of forecasts actually issued."""
        a = api.get("/api/accuracy")
        ev = a.get("evaluation") or {}
        return {"evaluation": _pick(ev, "runAt", "dataCutoff", "verdict", "metrics", "comparison", "trading", "config"),
                "issuedLive": a.get("issued"), "issuedByMode": a.get("issuedByMode")}

    # ------------------------------------------------------------------ strategies and AI decisions
    @server.tool(annotations=READ)
    def strategy_results() -> dict:
        """Strategy lab: every classic rule (trend, mean reversion, fundamental, event) and the AI strategies backtested
        on one out-of-sample window after costs, ranked by Sharpe, each with a multiple-testing-aware verdict."""
        r = api.get("/api/strategies")
        keys = ("start", "end", "years", "cagr", "sharpe", "maxDrawdown", "exposure", "trades", "excessReturn", "excessCiLow",
                "excessCiHigh", "deflatedSharpe")
        return {"run": _pick(r.get("run") or {}, "runAt", "oosStart", "dataCutoff", "summary"),
                "results": [{**_pick(s, "strategyKey", "family", "name", "verdict"), "rule": s.get("description"),
                             "metrics": _pick(s.get("metrics") or {}, *keys)} for s in r.get("results", [])]}

    @server.tool(annotations=READ)
    def strategy_detail(key: str, trades: int = 20) -> dict:
        """One strategy (e.g. AI_GBM, MOM_12_1, SMA_50_200): rule, parameters, metrics, yearly returns, cost
        sensitivity and its latest trades."""
        r = api.get(f"/api/strategies/{key}")
        s = r.get("result") or {}
        return {**_pick(s, "strategyKey", "family", "name", "description", "params", "metrics", "yearly", "costSensitivity", "verdict"),
                "tradeCount": r.get("tradeCount"),
                "latestTrades": [_pick(t, "symbol", "entryDate", "exitDate", "tradeReturn", "holdingDays", "entryReason", "exitReason")
                                 for t in r.get("trades", [])[:max(0, min(trades, 200))]]}

    @server.tool(annotations=READ)
    def ai_decisions(date: str | None = None) -> dict:
        """The AI strategy's decisions for a trading day (default: latest): ENTER/EXIT/HOLD/STAY_OUT per stock with the
        model probability, rank, top factors, which classic rules agree, and the plain-language explanation if any."""
        r = api.get("/api/decisions", date=_date(date, "date"))
        return {"asOfDate": r.get("asOfDate"), "availableDates": r.get("dates", [])[:10],
                "decisions": [{**_pick(d, "symbol", "name", "action", "probability", "rank", "weight", "entryP", "exitP", "explanation"),
                               "topFactors": [_pick(f, "label", "value", "contribution") for f in (d.get("factors") or [])[:3]],
                               "rulesHolding": sorted(k for k, v in (d.get("ruleVotes") or {}).items() if v)}
                              for d in r.get("decisions", [])]}

    # ------------------------------------------------------------------ time machine
    @server.tool(annotations=READ)
    def time_machine_runs() -> list[dict]:
        """Past time-machine runs (forecasts made as of a past date and scored against what happened)."""
        return [_pick(r, "id", "asOfDate", "runAt", "dataCutoff", "headline") for r in api.get("/api/timemachine")]

    @server.tool(annotations=READ)
    def time_machine_result(run_id: int, horizon: int = 21) -> dict:
        """One time-machine run: per-horizon scores (odds hit rate and Brier vs base rate, AI picks vs all stocks,
        10-90% band coverage) and per-stock prediction vs actual for `horizon` (5, 10, 21 or 63 trading days)."""
        r = api.get(f"/api/timemachine/{run_id}")
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

    # ------------------------------------------------------------------ jobs
    @server.tool(annotations=READ)
    def recent_jobs(limit: int = 10) -> list[dict]:
        """Recent background jobs (pipeline runs, price updates, backtests, time machine...) with status."""
        return [_pick(j, "id", "jobType", "status", "startedAt", "finishedAt") for j in api.get("/api/admin/jobs")[:max(1, min(limit, 30))]]

    @server.tool(annotations=READ)
    def job_status(job_id: int, wait_seconds: int = 0) -> dict:
        """Status and log tail of one job; optionally wait up to `wait_seconds` (max 600) for it to finish."""
        job = next((j for j in api.get("/api/admin/jobs") if j["id"] == job_id), None)
        if job is None:
            raise ApiError(f"job {job_id} is not among the 30 most recent jobs")
        return wait(job, wait_seconds)

    # ------------------------------------------------------------------ actions (queue background jobs)
    def action(path: str, body: dict | None, wait_seconds: int) -> dict:
        return wait(api.post(path, body), wait_seconds)

    @server.tool(annotations=ACTION)
    def run_pipeline(wait_seconds: int = 0) -> dict:
        """Run the full pipeline: refresh prices, SEC filings, macro data and policy events, then evaluate the models,
        issue forecasts, run the strategy lab and record the AI's decisions. Takes several minutes."""
        return action("/api/admin/pipeline/run", None, wait_seconds)

    @server.tool(annotations=ACTION)
    def update_prices(wait_seconds: int = 0) -> dict:
        """Download new daily prices, dividends and splits from the configured provider (only what is missing)."""
        return action("/api/admin/prices/sync", None, wait_seconds)

    @server.tool(annotations=ACTION)
    def ingest_sec_filings(symbol: str, wait_seconds: int = 0) -> dict:
        """Fetch new SEC filings for one company (XBRL facts, passages, exposures)."""
        return action("/api/admin/sec/ingest", {"symbol": symbol}, wait_seconds)

    @server.tool(annotations=ACTION)
    def evaluate_models(wait_seconds: int = 0) -> dict:
        """Re-run the walk-forward evaluation of the forecasting models."""
        return action("/api/admin/evaluate", None, wait_seconds)

    @server.tool(annotations=ACTION)
    def issue_forecasts(as_of_date: str | None = None, wait_seconds: int = 0) -> dict:
        """Issue forecasts now, or for a past as-of date (published as REPLAY, using only data known then)."""
        d = _date(as_of_date, "as_of_date")
        return action("/api/admin/forecasts/issue", {"asOfDate": d} if d else None, wait_seconds)

    @server.tool(annotations=ACTION)
    def run_strategy_backtest(wait_seconds: int = 0) -> dict:
        """Backtest every strategy (classic rules and AI) and store a new strategy-lab run."""
        return action("/api/admin/strategies/backtest", None, wait_seconds)

    @server.tool(annotations=ACTION)
    def make_ai_decisions(as_of_date: str | None = None, wait_seconds: int = 0) -> dict:
        """Record the AI strategy's decisions for the latest trading day (or a given date)."""
        d = _date(as_of_date, "as_of_date")
        return action("/api/admin/strategies/decide", {"asOfDate": d} if d else None, wait_seconds)

    @server.tool(annotations=ACTION)
    def run_time_machine(as_of_date: str, wait_seconds: int = 0) -> dict:
        """Forecast as of a past date with only the data known then, then score the forecasts against what happened
        5, 10, 21 and 63 trading days later. Read the result with time_machine_runs / time_machine_result."""
        return action("/api/admin/timemachine", {"asOfDate": _date(as_of_date, "as_of_date")}, wait_seconds)

    @server.tool(annotations=ACTION)
    def resolve_outcomes(wait_seconds: int = 0) -> dict:
        """Score issued forecasts whose 21-trading-day window has closed."""
        return action("/api/admin/outcomes/resolve", None, wait_seconds)

    return server


# --------------------------------------------------------------------------- ASGI app
def _allowed_hosts() -> list[str]:
    extra = [h.strip() for h in os.environ.get("CIVALPHA_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "mcp", "mcp:*", *extra]


class AdminTokenGate:
    """With CIVALPHA_ADMIN_TOKEN set, MCP requests need the token too (the server itself holds admin rights)."""

    def __init__(self, inner, token: str):
        self.inner, self.token = inner, token

    async def __call__(self, scope, receive, send):
        if self.token and scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            presented = headers.get("x-admin-token")
            auth = headers.get("authorization", "")
            if presented is None and auth.startswith("Bearer "):
                presented = auth[7:].strip()
            if presented is None or not hmac.compare_digest(self.token.encode(), presented.encode()):
                body = b'{"error":"admin token required (X-Admin-Token header)"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.inner(scope, receive, send)


def create_app(server: MCPServer | None = None, token: str | None = None):
    server = server or build()
    hosts = _allowed_hosts()
    inner = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                                     allowed_origins=[f"http://{h}" for h in hosts if "*" in h or ":" not in h]))
    return AdminTokenGate(inner, settings().admin_token if token is None else token)


app = create_app()
