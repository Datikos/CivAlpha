"""MCP server: tools answer over Streamable HTTP, errors become tool errors, management tools follow the admin-token
rule, and the endpoint rejects foreign Host/Origin headers (DNS rebinding)."""
import json

import pytest
from fastapi.testclient import TestClient

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def mcp(api):
    """A client whose lifespan is running (the MCP transport starts with the application), on the API test database."""
    from civalpha.platform.app import app

    with TestClient(app, base_url="http://localhost") as c:
        yield c


def rpc(c, method, params=None, headers=None):
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers={**HEADERS, **(headers or {})})
    assert r.status_code == 200, (method, r.status_code, r.text[:300])
    return r.json()["result"]


def call(c, name, arguments=None, headers=None):
    """(error text or None, parsed result)."""
    res = rpc(c, "tools/call", {"name": name, "arguments": arguments or {}}, headers)
    text = res["content"][0]["text"] if res["content"] else ""
    if res["isError"]:
        return text, None
    return None, res.get("structuredContent") or json.loads(text)


def test_tools_resources_and_prompts_are_listed(mcp):
    tools = {t["name"]: t for t in rpc(mcp, "tools/list")["tools"]}
    for name in ("get_status", "get_company", "get_exposures", "get_forecast", "get_accuracy", "get_strategies", "get_decisions",
                 "investment_candidates", "run_pipeline", "run_time_machine", "update_prices", "get_doubler_study", "run_doubler_study"):
        assert name in tools, name
    # read and safe actions only: universe changes and new events stay in the UI / REST API
    assert not {"add_company", "add_event", "delete_company"} & set(tools)
    assert tools["get_company"]["annotations"]["readOnlyHint"] is True
    assert tools["run_pipeline"]["annotations"]["readOnlyHint"] is False
    assert not any(t["annotations"]["destructiveHint"] for t in tools.values())
    assert "ctx" not in tools["run_pipeline"]["inputSchema"].get("properties", {})      # the context is injected, not an argument
    assert {str(r["uri"]) for r in rpc(mcp, "resources/list")["resources"]} == {"civalpha://about", "civalpha://status"}
    assert "not investment advice" in rpc(mcp, "resources/read", {"uri": "civalpha://about"})["contents"][0]["text"]
    prompt = rpc(mcp, "prompts/get", {"name": "investment_review", "arguments": {"symbol": "meta"}})
    assert "META" in prompt["messages"][0]["content"]["text"]


def test_research_tools_answer_on_the_test_universe(mcp):
    err, status = call(mcp, "get_status")
    assert err is None and status["disclaimers"] and "21-trading-day" in status["target"]
    err, companies = call(mcp, "list_companies")
    assert err is None and {c["symbol"] for c in companies["result"]} == {"META", "INTC"}
    err, company = call(mcp, "get_company", {"symbol": "FB"})
    assert err is None and company["symbol"] == "META"          # former ticker resolves
    for name, args in (("get_financials", {"symbol": "META"}), ("get_prices", {"symbol": "META", "from_date": "2024-01-01"}),
                       ("get_dividends", {"symbol": "META"}), ("list_filings", {"symbol": "META"}), ("get_exposures", {"symbol": "META"}),
                       ("list_events", {}), ("list_events", {"category": "TRADE_TARIFF"}), ("get_current_forecasts", {}),
                       ("get_forecast_history", {"symbol": "META", "model_kind": "AUGMENTED"}), ("get_accuracy", {}),
                       ("get_strategies", {}), ("get_decisions", {}), ("get_decisions", {"as_of_date": "2026-01-02"}),
                       ("list_time_machine_runs", {}), ("get_doubler_study", {})):
        err, _ = call(mcp, name, args)
        assert err is None, (name, err)
    err, out = call(mcp, "investment_candidates")
    assert err is None
    assert out["universeSize"] == 2 and len(out["candidates"]) == 2 and out["disclaimers"]
    assert {c["symbol"] for c in out["candidates"]} == {"META", "INTC"}
    assert "accuracy" in out["evidence"] and "strategyLab" in out["evidence"]


def test_domain_errors_become_tool_errors(mcp):
    err, _ = call(mcp, "get_company", {"symbol": "NOPE"})
    assert err == "Error executing tool get_company: unknown symbol NOPE"
    err, _ = call(mcp, "get_forecast", {"forecast_id": 999999})
    assert "not found" in err
    err, _ = call(mcp, "get_prices", {"symbol": "META", "from_date": "yesterday"})
    assert "YYYY-MM-DD" in err
    err, _ = call(mcp, "list_events", {"category": "WEATHER"})
    assert "category must be one of" in err
    err, _ = call(mcp, "run_time_machine", {"as_of_date": "2999-01-01"})
    assert "past date" in err
    err, _ = call(mcp, "ingest_sec_filings", {"symbol": "NOPE"})
    assert "unknown symbol" in err


def test_management_tools_queue_jobs_and_follow_the_admin_token_rule(mcp, monkeypatch):
    from civalpha.platform import settings

    err, job = call(mcp, "evaluate_models")                 # no token configured: open, like the REST API
    assert err is None and job["jobType"] == "EVALUATE" and job["status"] == "QUEUED"
    err, same = call(mcp, "get_job", {"job_id": job["id"]})
    assert err is None and same["id"] == job["id"]
    err, jobs = call(mcp, "list_jobs")
    assert err is None and any(j["id"] == job["id"] for j in jobs["result"])

    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "s3cret")
    settings.settings.cache_clear()
    try:
        err, _ = call(mcp, "evaluate_models")
        assert err is not None and "admin token required" in err
        err, _ = call(mcp, "list_jobs", headers={"X-Admin-Token": "wrong"})
        assert err is not None and "admin token required" in err
        err, _ = call(mcp, "list_jobs", headers={"X-Admin-Token": "s3cret"})
        assert err is None
        err, _ = call(mcp, "list_jobs", headers={"Authorization": "Bearer s3cret"})
        assert err is None
        err, company = call(mcp, "get_company", {"symbol": "META"})     # research stays public
        assert err is None and company["symbol"] == "META"
    finally:
        monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "")
        settings.settings.cache_clear()


def test_stdio_trusts_the_operator_but_http_needs_the_token(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    from civalpha.platform import settings
    from civalpha.platform.mcp_server import require_admin

    class Ctx:
        def __init__(self, headers):
            self.headers = headers

    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "s3cret")
    settings.settings.cache_clear()
    try:
        require_admin(None)                     # direct call, no transport
        require_admin(Ctx(None))                # stdio: no HTTP headers
        require_admin(Ctx({"x-admin-token": "s3cret"}))
        with pytest.raises(ToolError):
            require_admin(Ctx({}))
        with pytest.raises(ToolError):
            require_admin(Ctx({"authorization": "Bearer nope"}))
    finally:
        monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "")
        settings.settings.cache_clear()


def test_foreign_host_and_origin_are_rejected(mcp):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    assert mcp.post("/mcp", json=body, headers={**HEADERS, "Host": "evil.example"}).status_code == 421
    assert mcp.post("/mcp", json=body, headers={**HEADERS, "Origin": "http://evil.example"}).status_code in (403, 421)
    assert mcp.post("/mcp", json=body, headers={**HEADERS, "Host": "127.0.0.1:8088"}).status_code == 200


def test_endpoint_answers_503_before_the_transport_starts(api):
    assert api.post("/mcp", json={}, headers=HEADERS).status_code == 503
