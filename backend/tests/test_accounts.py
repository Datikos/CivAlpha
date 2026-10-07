"""ADR-0005: accounts mode. The role table, sign-in, the CSRF header, the must-change-password gate, lockout and the
per-address limit, personal tokens, revocation, cross-tenant refusal and an audit trail without secrets."""
import uuid
from datetime import date

import pytest

from civalpha.platform.app import required_role

PW = "correct horse battery staple"
W = {"X-CivAlpha-Request": "1"}
ADMIN = {"X-Admin-Token": "adm1n-t0ken"}


# ------------------------------------------------------------------ the rule table
@pytest.mark.parametrize("path, method, accounts, want", [
    ("/health", "GET", True, "NONE"),
    ("/api/meta", "GET", True, "NONE"),
    ("/api/auth/login", "POST", True, "NONE"),
    ("/api/auth/me", "GET", True, "NONE"),
    ("/api/auth/password", "POST", True, "USER"),
    ("/api/auth/tokens", "POST", True, "USER"),
    ("/api/companies", "GET", True, "USER"),
    ("/api/decisions", "GET", True, "USER"),
    ("/api/portfolio/holdings", "GET", True, "USER"),
    ("/api/portfolio/holdings", "PUT", True, "USER"),
    ("/api/portfolios", "POST", True, "USER"),
    ("/api/events", "POST", True, "OWNER"),
    ("/api/admin/jobs", "GET", True, "OWNER"),
    ("/api/admin/users", "POST", True, "OWNER"),
    ("/mcp", "POST", True, "USER"),
    ("/index.html", "GET", True, "NONE"),
    # token mode is today's behaviour
    ("/api/companies", "GET", False, "NONE"),
    ("/api/events", "POST", False, "OWNER"),
    ("/api/admin/users", "GET", False, "OWNER"),
    ("/api/portfolio/holdings", "GET", False, "OWNER"),
    ("/api/portfolios", "GET", False, "OWNER"),
    ("/api/auth/login", "POST", False, "NONE"),
    ("/mcp", "POST", False, "NONE"),
])
def test_role_table(path, method, accounts, want):
    assert required_role(path, method, accounts) == want


def test_a_misspelt_mode_refuses_to_start(monkeypatch):
    from civalpha.platform.settings import load

    monkeypatch.setenv("CIVALPHA_AUTH", "acounts")
    with pytest.raises(ValueError, match="CIVALPHA_AUTH"):
        load()


# ------------------------------------------------------------------ accounts mode through the API
@pytest.fixture
def acc(api, monkeypatch):
    from civalpha.platform import auth, settings

    monkeypatch.setenv("CIVALPHA_AUTH", "accounts")
    monkeypatch.setenv("CIVALPHA_COOKIE_SECURE", "false")     # the test client speaks plain http
    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", ADMIN["X-Admin-Token"])
    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 10)           # fast hashes in tests; stored params are read back
    settings.settings.cache_clear()
    return api


def _user(role="MEMBER", must_change=False) -> str:
    from civalpha.platform import auth
    from civalpha.platform.users import UserService

    name = f"u{uuid.uuid4().hex[:10]}"
    UserService().create(auth.ADMIN, name, name.title(), role, PW, must_change=must_change)
    return name


def _client(name: str, pw: str = PW):
    from fastapi.testclient import TestClient

    from civalpha.platform.app import app

    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": pw}, headers={"X-Real-IP": f"10.1.{uuid.uuid4().int % 250}.1"})
    assert r.status_code == 200, r.text
    return c


def test_anonymous_reaches_only_sign_in(acc):
    assert acc.get("/api/companies").status_code == 401
    assert acc.get("/api/companies").json() == {"error": "sign in required"}
    assert acc.get("/api/meta").json()["authMode"] == "accounts"
    assert acc.get("/api/auth/me").json() == {"authMode": "accounts", "signedIn": False, "caller": None}
    assert acc.post("/mcp", json={}).status_code == 401
    assert acc.get("/health").status_code == 200


def test_first_sign_in_must_change_the_password_with_the_csrf_header(acc):
    name = _user(must_change=True)
    c = _client(name)
    r = c.get("/api/companies")
    assert r.status_code == 403 and r.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert c.get("/api/meta").status_code == 200                 # the app shell still loads
    body = {"currentPassword": PW, "newPassword": "a brand new passphrase"}
    assert c.post("/api/auth/password", json=body).status_code == 403            # no CSRF header
    assert c.post("/api/auth/password", json={**body, "newPassword": "short"}, headers=W).status_code == 400
    assert c.post("/api/auth/password", json={**body, "newPassword": f"x{name}xxxxxxxx"}, headers=W).status_code == 400
    r = c.post("/api/auth/password", json=body, headers=W)
    assert r.status_code == 200 and r.json()["caller"]["mustChangePassword"] is False
    assert c.get("/api/companies").status_code == 200


def test_session_cookie_flags(acc, monkeypatch):
    from civalpha.platform import settings

    name = _user()
    r = acc.post("/api/auth/login", json={"username": name, "password": PW})
    cookie = r.headers["set-cookie"]
    assert "civalpha_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=604800" in cookie
    assert "Secure" not in cookie                                    # CIVALPHA_COOKIE_SECURE=false in this fixture
    monkeypatch.setenv("CIVALPHA_COOKIE_SECURE", "true")
    settings.settings.cache_clear()
    # the client now holds a session cookie, so this write needs the CSRF header like any other
    assert acc.post("/api/auth/login", json={"username": name, "password": PW}).status_code == 403
    assert "Secure" in acc.post("/api/auth/login", json={"username": name, "password": PW}, headers=W).headers["set-cookie"]
    assert acc.post("/api/auth/logout").json() == {"signedIn": False}


def test_member_cannot_reach_owner_routes(acc):
    c = _client(_user())
    assert c.get("/api/companies").status_code == 200
    assert c.get("/api/admin/jobs").status_code == 403
    assert c.get("/api/admin/users").status_code == 403
    assert c.post("/api/events", json={}, headers=W).status_code == 403
    assert c.post("/api/admin/evaluate", headers=W).status_code == 403
    owner = _client(_user("OWNER"))
    assert owner.get("/api/admin/users").status_code == 200
    assert acc.get("/api/admin/users", headers=ADMIN).status_code == 200         # the admin token acts as OWNER


def test_five_failures_lock_the_account_with_the_same_answer(acc):
    from civalpha.platform.sql import db

    name = _user()
    ip = {"X-Real-IP": "10.9.9.9"}
    for _ in range(5):
        r = acc.post("/api/auth/login", json={"username": name, "password": "wrong password!!"}, headers=ip)
        assert r.status_code == 401 and r.json() == {"error": "wrong username or password"}
    r = acc.post("/api/auth/login", json={"username": name, "password": PW}, headers=ip)     # right password, locked
    assert r.status_code == 401 and r.json() == {"error": "wrong username or password"}
    unknown = acc.post("/api/auth/login", json={"username": "nobody-here", "password": PW}, headers=ip)
    assert unknown.status_code == 401 and unknown.json() == r.json()
    db().execute("UPDATE app_user SET locked_until = now() - interval '1 second' WHERE username = :u", u=name)
    assert acc.post("/api/auth/login", json={"username": name, "password": PW}, headers=ip).status_code == 200


def test_one_address_is_limited_after_twenty_failures(acc):
    ip = {"X-Real-IP": f"10.8.{uuid.uuid4().int % 250}.8"}
    for _ in range(20):
        assert acc.post("/api/auth/login", json={"username": "nobody-here", "password": "x" * 12}, headers=ip).status_code == 401
    r = acc.post("/api/auth/login", json={"username": "nobody-here", "password": "x" * 12}, headers=ip)
    assert r.status_code == 429 and "too many" in r.json()["error"]
    name = _user()
    assert acc.post("/api/auth/login", json={"username": name, "password": PW}, headers={"X-Real-IP": "10.7.7.7"}).status_code == 200


def test_cross_tenant_reads_and_writes_answer_404(acc):
    a, b = _client(_user()), _client(_user())
    r = a.put("/api/portfolio/holdings", json={"symbol": "META", "shares": 3, "avgCostUsd": 100}, headers=W)
    assert r.status_code == 200
    pid = a.get("/api/portfolios").json()[0]["id"]
    assert b.get("/api/portfolios").json() == []
    assert b.get(f"/api/portfolio/holdings?portfolioId={pid}").status_code == 404
    assert b.get(f"/api/portfolio/advice?portfolioId={pid}").status_code == 404
    assert b.put("/api/portfolio/holdings", json={"symbol": "META", "shares": 1, "avgCostUsd": 1, "portfolioId": pid}, headers=W).status_code == 404
    assert b.post("/api/portfolio/holdings/remove", json={"symbol": "META", "portfolioId": pid}, headers=W).status_code == 404
    assert b.put("/api/portfolio/cash", json={"cashUsd": 5, "portfolioId": pid}, headers=W).status_code == 404
    assert b.get("/api/portfolio/holdings").json()["holdings"] == []
    assert [h["symbol"] for h in a.get("/api/portfolio/holdings").json()["holdings"]] == ["META"]
    second = a.post("/api/portfolios", json={"name": "Pension"}, headers=W).json()
    assert a.get(f"/api/portfolio/holdings?portfolioId={second['id']}").json()["holdings"] == []
    assert a.post("/api/portfolios", json={"name": "Pension"}, headers=W).status_code == 400


def test_personal_token_works_without_a_cookie_and_stops_when_revoked(acc):
    from fastapi.testclient import TestClient

    from civalpha.platform.app import app
    from civalpha.platform.sql import db

    c = _client(_user())
    t = c.post("/api/auth/tokens", json={"name": "assistant", "days": 30}, headers=W).json()
    assert t["token"].startswith("cvt_") and t["prefix"] == t["token"][:10]
    assert db().scalar("SELECT count(*) FROM api_token WHERE token_sha256 = :t", t=t["token"]) == 0     # only the digest is stored
    assert "token" not in c.get("/api/auth/tokens").json()[0]
    bare = TestClient(app)
    bearer = {"Authorization": f"Bearer {t['token']}"}
    assert bare.get("/api/portfolios", headers=bearer).status_code == 200
    assert bare.put("/api/portfolio/cash", json={"cashUsd": 10}, headers=bearer).status_code == 200      # no CSRF header needed
    assert c.post("/api/auth/tokens", json={"name": "too long", "days": 91}, headers=W).status_code == 400
    assert c.post(f"/api/auth/tokens/{t['id']}/revoke", headers=W).status_code == 204
    assert bare.get("/api/portfolios", headers=bearer).status_code == 401


def test_disabled_user_with_a_live_cookie_is_refused(acc):
    name = _user()
    c = _client(name)
    assert c.get("/api/companies").status_code == 200
    users = acc.get("/api/admin/users", headers=ADMIN).json()
    uid = next(u["id"] for u in users if u["username"] == name)
    assert "passwordHash" not in users[0]
    assert acc.put(f"/api/admin/users/{uid}", json={"status": "DISABLED"}, headers=ADMIN).status_code == 200
    assert c.get("/api/companies").status_code == 401
    assert acc.post("/api/auth/login", json={"username": name, "password": PW}).status_code == 401


def test_password_change_stops_other_sessions_and_tokens(acc):
    name = _user()
    one, two = _client(name), _client(name)
    t = one.post("/api/auth/tokens", json={"name": "script", "days": 7}, headers=W).json()["token"]
    r = one.post("/api/auth/password", json={"currentPassword": PW, "newPassword": "another long passphrase"}, headers=W)
    assert r.status_code == 200
    assert one.get("/api/companies").status_code == 200          # the session that changed it stays
    assert two.get("/api/companies").status_code == 401
    assert acc.get("/api/companies", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_owner_reset_forces_a_new_first_password(acc):
    name = _user()
    c = _client(name)
    uid = next(u["id"] for u in acc.get("/api/admin/users", headers=ADMIN).json() if u["username"] == name)
    r = acc.post(f"/api/admin/users/{uid}/password", json={"password": "temporary passphrase"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["mustChangePassword"] is True
    assert c.get("/api/companies").status_code == 401
    fresh = _client(name, "temporary passphrase")
    assert fresh.get("/api/companies").json()["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_the_last_owner_stays(acc):
    owner = _client(_user("OWNER"))
    me = owner.get("/api/auth/me").json()["caller"]
    assert owner.put(f"/api/admin/users/{me['userId']}", json={"status": "DISABLED"}, headers=W).status_code == 403   # not yourself


def test_audit_trail_records_actors_and_no_secrets(acc):
    from civalpha.platform.sql import db

    name = _user()
    c = _client(name)
    c.put("/api/portfolio/holdings", json={"symbol": "INTC", "shares": 2, "avgCostUsd": 20}, headers=W)
    c.put("/api/portfolio/holdings", json={"symbol": "INTC", "shares": 4, "avgCostUsd": 21}, headers=W)
    raw = c.post("/api/auth/tokens", json={"name": "x", "days": 1}, headers=W).json()["token"]
    rows = acc.get("/api/admin/audit?limit=1000", headers=ADMIN).json()
    mine = [r for r in rows if r["actorUsername"] == name]
    assert {r["action"] for r in mine} >= {"HOLDING_SET", "TOKEN_CREATED"}
    edit = next(r for r in mine if r["action"] == "HOLDING_SET" and r["before"])
    assert edit["before"]["shares"] == 2 and edit["after"]["shares"] == 4
    dump = str(db().all("SELECT before, after FROM audit_event"))
    assert "scrypt$" not in dump and raw not in dump and "passwordHash" not in dump      # the 10-character prefix only


# ------------------------------------------------------------------ the service layer
def test_first_owner_takes_the_installations_portfolios(tdb):
    from civalpha.platform import auth
    from civalpha.platform.portfolio import PortfolioService
    from civalpha.platform.users import UserService

    if tdb.scalar("SELECT count(*) FROM app_user WHERE role = 'OWNER'"):
        pytest.skip("an owner already exists in this database")
    PortfolioService(tdb).set_cash(100)                         # token mode: the installation's own portfolio
    u = UserService(tdb).create(auth.LOCAL, "first-owner", "First", "OWNER", PW, must_change=False)
    assert tdb.scalar("SELECT count(*) FROM portfolio WHERE owner_user_id IS NULL") == 0
    mine = PortfolioService(tdb, caller=auth.Caller("SESSION", "OWNER", u["id"])).portfolios()
    assert len(mine) == 1 and mine[0]["cashUsd"] == 100


def test_anonymous_has_no_portfolio_service(tdb):
    from civalpha.platform import auth
    from civalpha.platform.errors import Unauthorized
    from civalpha.platform.portfolio import PortfolioService

    with pytest.raises(Unauthorized):
        PortfolioService(tdb, caller=auth.ANONYMOUS)


def test_pipeline_advises_active_users_only(tdb):
    from civalpha.platform import auth
    from civalpha.platform.errors import Forbidden
    from civalpha.platform.portfolio import PortfolioService
    from civalpha.platform.users import UserService

    users = UserService(tdb)
    a = users.create(auth.LOCAL, "active-one", "A", "MEMBER", PW)
    b = users.create(auth.LOCAL, "gone-one", "B", "MEMBER", PW)
    cid = tdb.scalar("SELECT id FROM company LIMIT 1")
    day = date(2026, 10, 5)
    book = {} if cid is None else {cid: {"id": 1, "strategyKey": "AI_SIZED", "companyId": cid, "symbol": "X", "action": "HOLD",
                                         "probability": 0.5, "probabilityCalibrated": None, "rank": 1, "entryP": 0.55, "exitP": 0.48,
                                         "vol21": 0.3}}
    kw = dict(live_test_fn=lambda: {"verdict": "PENDING"}, decisions_fn=lambda d: (day, book))
    for u in (a, b):
        PortfolioService(tdb, caller=auth.Caller("SESSION", "MEMBER", u["id"]), **kw).set_holding("ZZZZ", 1, 1)
    users.update(auth.LOCAL, b["id"], status="DISABLED")
    with pytest.raises(Forbidden):
        PortfolioService(tdb, **kw).advise_all()
    r = PortfolioService(tdb, caller=auth.SYSTEM, **kw).advise_all()
    advised = set(tdb.scalars("SELECT DISTINCT p.owner_user_id FROM holding_advice h JOIN portfolio p ON p.id = h.portfolio_id"))
    assert a["id"] in advised and b["id"] not in advised and r.created >= 1
