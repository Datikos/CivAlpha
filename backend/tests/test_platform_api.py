"""REST API: every read endpoint answers, errors map to 400/404, admin token rules (ported Java ITs and filter test)."""
import pytest

from civalpha.platform.app import protected_request

READ_URLS = ["/api/meta", "/api/companies", "/api/companies/META", "/api/companies/FB/prices", "/api/companies/META/filings",
             "/api/companies/META/financials", "/api/companies/META/dividends", "/api/companies/META/exposures", "/api/events", "/api/events?category=TRADE_TARIFF",
             "/api/forecasts/current", "/api/forecasts/history", "/api/forecasts/history?symbol=META&modelKind=AUGMENTED",
             "/api/accuracy", "/api/admin/jobs", "/api/strategies", "/api/decisions", "/api/decisions?date=2026-01-02",
             "/api/timemachine", "/api/doublers", "/api/admin/universe", "/health"]


def test_read_endpoints_answer_without_errors(api):
    for url in READ_URLS:
        r = api.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    assert api.get("/api/companies/META").json()["symbol"] == "META"
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
