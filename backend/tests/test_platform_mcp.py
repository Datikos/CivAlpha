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
                 "investment_candidates", "run_pipeline", "run_time_machine", "update_prices", "get_doubler_study", "run_doubler_study",
                 "add_company", "set_company_tags"):
        assert name in tools, name
    # read, safe actions and adding a company only: removals, edits and new events stay in the UI / REST API
    assert not {"add_event", "delete_company", "remove_company", "edit_company"} & set(tools)
    assert tools["add_company"]["annotations"]["readOnlyHint"] is False
    assert tools["add_company"]["inputSchema"]["required"] == ["symbol"]
    assert tools["set_company_tags"]["annotations"]["readOnlyHint"] is False
    assert tools["set_company_tags"]["inputSchema"]["required"] == ["symbol", "tags"]
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


def test_add_company_adds_to_the_universe_and_queues_its_jobs(mcp, monkeypatch):
    from civalpha.platform import settings

    err, out = call(mcp, "add_company", {"symbol": "nvda", "name": "NVIDIA Corporation", "cik": "1045810", "sector": "Technology",
                                         "industry": "SEMICONDUCTORS", "member_since": "2024-01-02", "sync_prices": False,
                                         "tags": ["AI", "ai", "Chips"]})
    assert err is None, err
    assert out["symbol"] == "NVDA" and out["cik"] == "1045810" and out["benchmarkSymbol"] == "XLK"      # ETF from the sector
    assert out["tags"] == ["AI", "Chips"]                                                               # cleaned, duplicates dropped
    assert out["filledFromSec"] == {"benchmarkSymbol": "XLK"} and out["notes"] == []
    assert [j["jobType"] for j in out["jobs"]] == ["SEC_INGEST"] and out["jobs"][0]["status"] == "QUEUED"
    assert any("prices" in step for step in out["nextSteps"])
    err, companies = call(mcp, "list_companies")
    assert err is None and "NVDA" in {c["symbol"] for c in companies["result"]}
    row = next(c for c in mcp.get("/api/admin/universe").json()["companies"] if c["symbol"] == "NVDA")
    assert row["active"] and row["cik"] == "0001045810" and row["memberSince"] == "2024-01-02"
    assert row["tags"] == ["AI", "Chips"]
    nvda = next(c for c in companies["result"] if c["symbol"] == "NVDA")
    assert nvda["tags"] == ["AI", "Chips"] and nvda["industry"] == "SEMICONDUCTORS"

    err, out = call(mcp, "set_company_tags", {"symbol": "NVDA", "tags": ["watch only", "AI"]})
    assert err is None and out == {"symbol": "NVDA", "tags": ["watch only", "AI"]}
    err, company = call(mcp, "get_company", {"symbol": "NVDA"})
    assert err is None and company["tags"] == ["AI", "watch only"]
    err, _ = call(mcp, "set_company_tags", {"symbol": "NVDA", "tags": ["bad<tag>"]})
    assert "letters, digits" in err
    err, _ = call(mcp, "set_company_tags", {"symbol": "NOPE", "tags": []})
    assert "unknown symbol" in err
    err, out = call(mcp, "set_company_tags", {"symbol": "NVDA", "tags": []})
    assert err is None and out["tags"] == []

    err, _ = call(mcp, "add_company", {"symbol": "NVDA", "name": "x", "cik": "1045810", "sector": "Technology"})
    assert "already" in err                                                             # domain error, not a crash
    err, _ = call(mcp, "add_company", {"symbol": "AMD", "name": "x", "cik": "2488", "sector": "Chips"})
    assert "unknown sector" in err and "benchmark_symbol" in err
    err, _ = call(mcp, "add_company", {"symbol": "AMD", "name": "x", "cik": "2488", "sector": "Technology", "member_since": "soon"})
    assert "YYYY-MM-DD" in err
    err, _ = call(mcp, "add_company", {"symbol": "AMD", "name": "x", "cik": "2488", "sector": "Technology", "member_since": "2999-01-01"})
    assert "future" in err

    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "s3cret")
    settings.settings.cache_clear()
    try:
        err, _ = call(mcp, "add_company", {"symbol": "AMD", "name": "x", "cik": "2488", "sector": "Technology"})
        assert err is not None and "admin token required" in err
    finally:
        monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "")
        settings.settings.cache_clear()


def test_add_company_fills_identity_and_sector_from_edgar(mcp, monkeypatch):
    """With only a ticker, the tool asks EDGAR (here a stubbed profiler) for name, CIK and a sector suggestion."""
    from civalpha.platform import mcp_server
    from civalpha.platform.sec.profile import Profile

    def fake_profile(self, symbol):
        p = Profile(symbol.upper(), cik="0000320193", name="Apple Inc.", sector="Technology", benchmark_symbol="XLK",
                    industry="CONSUMER_ELECTRONICS", sector_confidence="review", sector_note="SIC 3571 → Technology (XLK)")
        p.sector_alternatives = [{"sector": "Technology", "benchmarkSymbol": "XLK"}, {"sector": "Industrials", "benchmarkSymbol": "XLI"}]
        return p

    monkeypatch.setattr(mcp_server.CompanyProfiler, "profile", fake_profile)
    err, out = call(mcp, "add_company", {"symbol": "AAPL", "ingest_sec": False, "sync_prices": False})
    assert err is None, err
    assert out["name"] == "Apple Inc." and out["cik"] == "0000320193" and out["sector"] == "Technology" and out["benchmarkSymbol"] == "XLK"
    assert set(out["filledFromSec"]) == {"name", "cik", "sector", "industry", "benchmarkSymbol"}
    assert any("Industrials" in n for n in out["notes"])                                 # the review note names the alternatives
    assert out["jobs"] == [] and len(out["nextSteps"]) >= 2

    monkeypatch.setattr(mcp_server.CompanyProfiler, "profile", lambda self, symbol: Profile(symbol, warnings=["not in SEC company_tickers.json"]))
    err, _ = call(mcp, "add_company", {"symbol": "ZZZZ"})
    assert "no name/CIK" in err and "company_tickers" in err


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


def test_accounts_mode_tools_act_as_the_personal_tokens_user(mcp, monkeypatch):
    """ADR-0005: in accounts mode an MCP client needs a personal token; portfolio tools act on that user's portfolios,
    job tools need the owner role, and a request without a caller never reaches a tool."""
    import uuid

    from civalpha.platform import auth, settings
    from civalpha.platform.users import UserService

    monkeypatch.setenv("CIVALPHA_AUTH", "accounts")
    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 10)
    settings.settings.cache_clear()
    try:
        name = f"m{uuid.uuid4().hex[:10]}"
        u = UserService().create(auth.LOCAL, name, "Member", "MEMBER", "a long enough passphrase", must_change=False)
        token = UserService().create_token(auth.Caller("SESSION", "MEMBER", u["id"]), "mcp", 1)["token"]
        bearer = {"Authorization": f"Bearer {token}"}
        r = mcp.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=HEADERS)
        assert r.status_code == 401
        err, company = call(mcp, "get_company", {"symbol": "META"}, bearer)
        assert err is None and company["symbol"] == "META"
        err, held = call(mcp, "set_holding", {"symbol": "META", "shares": 2, "avg_cost_usd": 10}, bearer)
        assert err is None and held["symbol"] == "META"
        err, advice = call(mcp, "get_portfolio_advice", {}, bearer)
        assert err is None and advice["portfolio"]["name"] == "default"
        err, _ = call(mcp, "evaluate_models", {}, bearer)
        assert err is not None and "owner role" in err
    finally:
        monkeypatch.setenv("CIVALPHA_AUTH", "token")
        settings.settings.cache_clear()
