"""REST API: every read endpoint answers, errors map to 400/404, admin token rules (ported Java ITs and filter test)."""
import pytest

from civalpha.platform.app import protected_request

READ_URLS = ["/api/meta", "/api/companies", "/api/companies/META", "/api/companies/FB/prices", "/api/companies/META/filings",
             "/api/companies/META/financials", "/api/companies/META/dividends", "/api/companies/META/exposures", "/api/events", "/api/events?category=TRADE_TARIFF",
             "/api/forecasts/current", "/api/forecasts/history", "/api/forecasts/history?symbol=META&modelKind=AUGMENTED",
             "/api/accuracy", "/api/admin/jobs", "/api/strategies", "/api/decisions", "/api/decisions?date=2026-01-02",
             "/api/timemachine", "/api/doublers", "/api/admin/universe", "/health", "/api/companies/META/insiders",
             "/api/companies/META/earnings", "/api/setups"]


def test_read_endpoints_answer_without_errors(api):
    for url in READ_URLS:
        r = api.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    assert api.get("/api/companies/META").json()["symbol"] == "META"
    meta = next(c for c in api.get("/api/companies").json() if c["symbol"] == "META")
    assert isinstance(meta["recentCloses"], list) and "change21d" in meta and "benchmarkChange21d" in meta
    event = api.get("/api/events").json()
    if event:
        detail = api.get(f"/api/events/{event[0]['id']}").json()
        assert isinstance(detail["reissuedForecasts"], list) and isinstance(detail["forecastShift"], list)
    assert api.get("/api/companies/FB/prices").json()["symbol"] == "META"     # former ticker resolves
    for url in ("/api/companies/NOPE", "/api/strategies/NOPE", "/api/timemachine/999999", "/api/doublers/999999", "/api/forecasts/999999",
                "/api/filings/999999", "/api/events/999999", "/api/documents/999999"):
        assert api.get(url).status_code == 404, url
    assert api.get("/api/companies/NOPE").json() == {"error": "unknown symbol NOPE"}


def test_writes_are_validated_before_any_work_starts(api):
    r = api.post("/api/admin/timemachine", json={"asOfDate": "2999-01-01"})
    assert r.status_code == 400 and "past date" in r.json()["error"]
    # an event without original evidence is rejected
    r = api.post("/api/events", json={"category": "TRADE_TARIFF", "title": "x", "eventDate": "2026-01-01",
                                      "publishedAt": "2026-01-01T00:00:00Z"})
    assert r.status_code == 400 and "source.url" in r.json()["error"]
    assert api.post("/api/admin/sec/ingest", json={"symbol": "NOPE"}).status_code == 404
    assert api.post("/api/admin/forecasts/issue", json={"asOfDate": "not-a-date"}).status_code == 400


def test_admin_actions_queue_jobs_for_the_worker(api):
    job = api.post("/api/admin/evaluate").json()
    assert job["jobType"] == "EVALUATE" and job["status"] == "QUEUED"
    assert job["startedAt"] is None and job["progressDone"] is None and job["progressTotal"] is None   # not started yet
    assert any(j["id"] == job["id"] for j in api.get("/api/admin/jobs").json())


def test_admin_token_rules():
    assert protected_request("/api/admin/jobs", "GET")
    assert protected_request("/api/events", "POST")
    assert not protected_request("/api/events", "GET")
    assert not protected_request("/index.html", "POST")


def test_admin_token_is_enforced_when_configured(api, monkeypatch):
    from civalpha.platform import settings

    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "s3cret")
    settings.settings.cache_clear()
    assert api.get("/api/admin/jobs").status_code == 401
    assert api.get("/api/admin/jobs", headers={"X-Admin-Token": "s3cret"}).status_code == 200
    assert api.get("/api/admin/jobs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert api.post("/api/events", json={}).status_code == 401
    assert api.get("/api/meta").status_code == 200
    assert api.get("/api/meta").json()["adminTokenRequired"] is True


def test_company_tags_round_trip_through_the_api(api):
    row = next(c for c in api.get("/api/admin/universe").json()["companies"] if c["symbol"] == "META")
    r = api.put(f"/api/admin/universe/companies/{row['id']}", json={"tags": ["AI", " ai", "China exposed"]})
    assert r.status_code == 200 and r.json()["tags"] == ["AI", "China exposed"]
    universe = api.get("/api/admin/universe").json()
    assert next(c for c in universe["companies"] if c["id"] == row["id"])["tags"] == ["AI", "China exposed"]
    assert {"tag": "AI", "count": 1} in universe["tags"]
    public = next(c for c in api.get("/api/companies").json() if c["symbol"] == "META")
    assert public["tags"] == ["AI", "China exposed"] and public["industry"] == "INTERNET"
    assert api.get("/api/companies/FB").json()["tags"] == ["AI", "China exposed"]
    # a name-only edit leaves the tags alone; the tags endpoint replaces them; invalid tags are a 400
    assert api.put(f"/api/admin/universe/companies/{row['id']}", json={"name": "Meta Platforms, Inc."}).status_code == 200
    assert api.get("/api/companies/META").json()["tags"] == ["AI", "China exposed"]
    r = api.put(f"/api/admin/universe/companies/{row['id']}/tags", json={"tags": ["watch only"]})
    assert r.status_code == 200 and r.json()["tags"] == ["watch only"]
    r = api.put(f"/api/admin/universe/companies/{row['id']}/tags", json={"tags": ["no<html>"]})
    assert r.status_code == 400 and "letters, digits" in r.json()["error"]
    assert api.put("/api/admin/universe/companies/999999/tags", json={"tags": []}).status_code == 404
    assert api.put(f"/api/admin/universe/companies/{row['id']}/tags", json={"tags": []}).json()["tags"] == []


def test_decisions_endpoint_serves_the_stored_calibrated_probability(api):
    """ADR-0002: the calibrated probability lives in the stored model JSON and is served as probabilityCalibrated."""
    from civalpha.platform.decisions import DecisionService
    from civalpha.platform.llm import LlmProvider   # the base provider is the no-op one: no explanation, no review

    cid = next(c["id"] for c in api.get("/api/companies").json() if c["symbol"] == "META")
    base = {"companyId": cid, "symbol": "META", "name": "Meta Platforms, Inc.", "strategyKey": "AI_SIZED", "action": "HOLD",
            "probability": 0.61, "entryP": 0.55, "exitP": 0.48, "weight": 0.1, "rank": 1, "maxPositions": 8,
            "factors": [], "ruleVotes": {}}
    with_cal = {**base, "asOfDate": "2025-12-30",
                "model": {"horizon": 21, "calibration": {"method": "isotonic", "fittedOn": "backtest_prediction AI_BOOK_21 of evaluation 1",
                                                          "n": 6028, "probability": 0.503}}}
    without = {**base, "asOfDate": "2025-12-29", "model": {"horizon": 10}}
    DecisionService(llm=LlmProvider(), review_mode="off").persist_all([with_cal, without], lambda _m: None)
    d = api.get("/api/decisions?date=2025-12-30").json()["decisions"]
    assert d[0]["probability"] == 0.61 and d[0]["probabilityCalibrated"] == 0.503
    assert d[0]["model"]["calibration"]["fittedOn"].endswith("evaluation 1")
    old = api.get("/api/decisions?date=2025-12-29").json()["decisions"]
    assert old[0]["probabilityCalibrated"] is None              # decisions stored before ADR-0002 have none
