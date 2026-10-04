"""MCP server: tool catalogue and annotations, response shaping, job waiting, visible errors, HTTP gate."""
import asyncio
import json

import httpx
import pytest
from mcp.client import Client
from mcp.types import LATEST_PROTOCOL_VERSION

from civalpha.platform import mcp_server
from civalpha.platform.mcp_server import Api, build, create_app

BARS = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": 100.0 + i, "benchmarkClose": 50.0 + i * 0.25} for i in range(300)]


def fake_api(routes: dict, calls: list | None = None) -> Api:
    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        if calls is not None:
            calls.append((key, dict(request.url.params), request.content.decode() or None, request.headers.get("x-admin-token")))
        r = routes.get(key)
        if callable(r):
            r = r(request)
        if r is None:
            return httpx.Response(404, json={"error": f"unknown path {request.url.path}"})
        status, body = r if isinstance(r, tuple) else (200, r)
        return httpx.Response(status, json=body)
    return Api("http://api.test", token="", transport=httpx.MockTransport(handler))


def call(server, tool: str, args: dict | None = None):
    async def run():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})
    return asyncio.run(run())


def tools(server):
    async def run():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    return asyncio.run(run())


def payload(result) -> dict | list:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_tool_catalogue_marks_reads_and_actions():
    ts = {t.name: t for t in tools(build(fake_api({})))}
    actions = {"run_pipeline", "update_prices", "ingest_sec_filings", "evaluate_models", "issue_forecasts", "run_strategy_backtest",
               "make_ai_decisions", "run_time_machine", "resolve_outcomes"}
    assert actions <= set(ts) and len(ts) == 29
    for name, t in ts.items():
        assert t.description and len(t.description) > 40, name
        assert t.annotations.read_only_hint is (name not in actions), name
        assert t.annotations.destructive_hint is False


def test_company_profile_condenses_prices_and_joins_forecasts_and_decision():
    api = fake_api({
        "GET /api/companies/FB": {"symbol": "META", "name": "Meta Platforms, Inc.", "sector": "Communication Services",
                                  "benchmarkSymbol": "XLC", "tickerHistory": [{"symbol": "FB"}, {"symbol": "META"}], "keyFacts": []},
        "GET /api/companies/FB/prices": {"bars": BARS},
        "GET /api/companies": [{"symbol": "META", "latestForecasts": {"AUGMENTED": {"probability": 0.55, "asOfDate": "2026-10-02"}}}],
        "GET /api/decisions": {"decisions": [{"symbol": "META", "action": "ENTER", "probability": 0.58, "rank": 1, "asOfDate": "2026-10-02"}]},
    })
    p = payload(call(build(api), "company_profile", {"symbol": "FB"}))
    assert p["symbol"] == "META" and p["lastClose"]["close"] == 399.0
    assert p["returns"]["1m"]["stock"] == round(399 / 378 - 1, 4)
    assert p["returns"]["12m"]["benchmark"] == round((50 + 299 * 0.25) / (50 + 47 * 0.25) - 1, 4)
    assert p["latestForecasts"]["AUGMENTED"]["probability"] == 0.55
    assert p["aiDecision"]["action"] == "ENTER"
    assert "bars" not in json.dumps(p)                      # 300 raw bars are not sent to the model


def test_actions_queue_jobs_and_can_wait_for_them(monkeypatch):
    monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
    calls, polls = [], {"n": 0}

    def jobs(_):
        polls["n"] += 1
        status = "RUNNING" if polls["n"] < 3 else "SUCCEEDED"
        return [{"id": 7, "jobType": "TIME_MACHINE", "status": status, "log": "a\nb\nAs of the close of 2026-06-30 ..."}]
    api = fake_api({"POST /api/admin/timemachine": {"id": 7, "jobType": "TIME_MACHINE", "status": "QUEUED", "log": ""},
                    "GET /api/admin/jobs": jobs}, calls)
    r = payload(call(build(api), "run_time_machine", {"as_of_date": "2026-06-30", "wait_seconds": 60}))
    assert r["status"] == "SUCCEEDED" and r["logTail"][-1].startswith("As of the close")
    assert calls[0][0] == "POST /api/admin/timemachine" and json.loads(calls[0][2]) == {"asOfDate": "2026-06-30"}
    # without waiting the queued job comes back at once, with a hint
    r = payload(call(build(api), "run_time_machine", {"as_of_date": "2026-06-30"}))
    assert r["status"] == "QUEUED" and "job_status(7)" in r["note"]


def test_errors_reach_the_model_with_their_message():
    r = call(build(fake_api({})), "company_profile", {"symbol": "NOPE"})
    assert r.is_error and "unknown path /api/companies/NOPE" in r.content[0].text
    r = call(build(fake_api({})), "run_time_machine", {"as_of_date": "June 30"})
    assert r.is_error and "as_of_date must be a date" in r.content[0].text

    def down(_):
        raise httpx.ConnectError("refused")
    dead = Api("http://api.test", token="", transport=httpx.MockTransport(down))
    r = call(build(dead), "platform_status")
    assert r.is_error and "cannot reach the CivAlpha API" in r.content[0].text


def test_api_client_sends_the_admin_token():
    calls = []
    api = fake_api({"GET /api/admin/jobs": []}, calls)
    api.http.headers["X-Admin-Token"] = "s3cret"
    call(build(api), "recent_jobs")
    assert calls[-1][3] == "s3cret"


def _rpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "localhost:8088",
           "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION}
INIT = {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}


def test_http_endpoint_enforces_the_admin_token_and_host_and_serves_tools():
    from starlette.testclient import TestClient

    app = create_app(build(fake_api({"GET /api/admin/jobs": [{"id": 1, "jobType": "PIPELINE_RUN", "status": "SUCCEEDED"}]})), token="s3cret")
    with TestClient(app) as c:
        assert c.post("/mcp", json=_rpc("initialize", INIT), headers=HEADERS).status_code == 401
        assert c.post("/mcp", json=_rpc("initialize", INIT), headers={**HEADERS, "Host": "evil.example", "X-Admin-Token": "s3cret"}).status_code in (403, 421)
        ok = {**HEADERS, "Authorization": "Bearer s3cret"}
        r = c.post("/mcp", json=_rpc("initialize", INIT), headers=ok)
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "civalpha"
        r = c.post("/mcp", json=_rpc("tools/call", {"name": "recent_jobs", "arguments": {}}, 2), headers=ok)
        assert r.status_code == 200
        assert json.loads(r.json()["result"]["content"][0]["text"])[0]["jobType"] == "PIPELINE_RUN"
