"""ADR-0005: who is calling. Callers, password hashing, sessions, personal tokens, sign-in limits and the audit trail.

A request's caller is, in this order: the admin token (OWNER, both modes); in CIVALPHA_AUTH=accounts mode a personal
token (`Authorization: Bearer cvt_...`) or the session cookie; in token mode without an admin token configured, the
local operator (OWNER, everything is open as before); otherwise anonymous. `app.required_role` decides what each
caller may reach.

Secrets kept apart: passwords are stored as scrypt hashes, session cookies and personal tokens as SHA-256 digests.
None of them is ever returned, audited or logged; a failed sign-in is logged with the matched user id or "unknown
user", never with what was typed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .errors import BadRequest, TooManyRequests, Unauthorized
from .settings import settings
from .sql import Db, db, jsonb

log = logging.getLogger("civalpha.auth")

SCRYPT_N = 2 ** 15          # with r = 8: 32 MB and about 50-100 ms per check, paid at sign-in and password change only
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
PASSWORD_MIN = 12
PASSWORD_MAX = 200
SESSION_COOKIE = "civalpha_session"
SESSION_IDLE = timedelta(hours=12)
SESSION_MAX = timedelta(days=7)
SEEN_EVERY = timedelta(minutes=5)      # last_seen_at / last_used_at are written at most this often
LOCK_AFTER = 5                         # consecutive failures that lock an account...
LOCK_FOR = timedelta(minutes=15)       # ...for this long
IP_LIMIT = 20                          # failed sign-ins from one address...
IP_WINDOW = timedelta(minutes=15)      # ...within this window answer 429
TOKEN_PREFIX = "cvt_"
TOKEN_MAX_DAYS = 90
CSRF_HEADER = "x-civalpha-request"     # a cookie-authenticated write must carry it (value "1")
ROLES = ("OWNER", "MEMBER")


@dataclass(frozen=True)
class Caller:
    kind: str                       # LOCAL | ADMIN_TOKEN | SESSION | API_TOKEN | SYSTEM | ANONYMOUS
    role: str | None                # OWNER | MEMBER | None (anonymous)
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    must_change_password: bool = False
    session_id: int | None = None

    @property
    def signed_in(self) -> bool:
        return self.role is not None

    @property
    def owner(self) -> bool:
        return self.role == "OWNER"

    @property
    def audit_kind(self) -> str:
        return "USER" if self.user_id is not None else ("ADMIN_TOKEN" if self.kind == "ADMIN_TOKEN" else
                                                          "SYSTEM" if self.kind == "SYSTEM" else "LOCAL")

    def public(self) -> dict:
        return {"kind": self.kind, "role": self.role, "userId": self.user_id, "username": self.username,
                "displayName": self.display_name, "mustChangePassword": self.must_change_password}


LOCAL = Caller("LOCAL", "OWNER")              # token mode without an admin token, stdio MCP, CLI
ADMIN = Caller("ADMIN_TOKEN", "OWNER")
SYSTEM = Caller("SYSTEM", "OWNER")            # the pipeline
ANONYMOUS = Caller("ANONYMOUS", None)


# --------------------------------------------------------------------------- passwords
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        want = _unb64(h)
        got = hashlib.scrypt((password or "").encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(want),
                             maxmem=SCRYPT_MAXMEM)
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Checked against when the username matched nobody, so an unknown user costs the same time as a wrong password."""
    return hash_password(secrets.token_urlsafe(16))


def check_password_policy(password: str, username: str) -> None:
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise BadRequest(f"password: {PASSWORD_MIN} to {PASSWORD_MAX} characters")
    if username and username.lower() in password.lower():
        raise BadRequest("password: must not contain the username")


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- resolving a caller
def caller_from(headers: dict, cookie: str | None = None, database: Db | None = None) -> Caller:
    """The caller behind a request's headers (keys lower-case) and session cookie. Touches the database only in
    accounts mode and only when a personal token or a cookie is presented."""
    s = settings()
    presented = headers.get("x-admin-token")
    auth = headers.get("authorization") or ""
    bearer = auth[7:].strip() if auth.startswith("Bearer ") else None
    if s.admin_token:
        for v in (presented, bearer):
            if v and hmac.compare_digest(s.admin_token.encode(), v.encode()):
                return ADMIN
    if s.accounts:
        d = database or db()
        if bearer and bearer.startswith(TOKEN_PREFIX):
            c = token_caller(bearer, d)
            if c is not None:
                return c
        if cookie:
            c = session_caller(cookie, d)
            if c is not None:
                return c
        return ANONYMOUS
    return ANONYMOUS if s.admin_token else LOCAL


def session_caller(raw: str, database: Db) -> Caller | None:
    row = database.one("""
        SELECT s.id, s.user_id, s.last_seen_at, u.username, u.display_name, u.role, u.must_change_password
        FROM user_session s JOIN app_user u ON u.id = s.user_id
        WHERE s.token_sha256 = :h AND s.revoked_at IS NULL AND s.expires_at > now() AND s.last_seen_at > now() - :idle
          AND u.status = 'ACTIVE'""", h=_sha(raw), idle=SESSION_IDLE)
    if row is None:
        return None
    if row["last_seen_at"] < _now() - SEEN_EVERY:
        database.execute("UPDATE user_session SET last_seen_at = now() WHERE id = :id", id=row["id"])
    return Caller("SESSION", row["role"], row["user_id"], row["username"], row["display_name"], row["must_change_password"], row["id"])


def token_caller(raw: str, database: Db) -> Caller | None:
    row = database.one("""
        SELECT t.id, t.user_id, t.last_used_at, u.username, u.display_name, u.role
        FROM api_token t JOIN app_user u ON u.id = t.user_id
        WHERE t.token_sha256 = :h AND t.revoked_at IS NULL AND t.expires_at > now() AND u.status = 'ACTIVE'""", h=_sha(raw))
    if row is None:
        return None
    if row["last_used_at"] is None or row["last_used_at"] < _now() - SEEN_EVERY:
        database.execute("UPDATE api_token SET last_used_at = now() WHERE id = :id", id=row["id"])
    # a token is issued to someone who already changed their first password; it never carries the must-change gate
    return Caller("API_TOKEN", row["role"], row["user_id"], row["username"], row["display_name"])


# --------------------------------------------------------------------------- signing in and out
def login(username: str, password: str, ip: str, database: Db | None = None) -> tuple[Caller, str]:
    """Checks the password and opens a session; returns the caller and the raw cookie value. Unknown user, wrong
    password, a locked or disabled account all raise the same Unauthorized."""
    if not settings().accounts:
        raise BadRequest("sign-in is off: this installation runs with CIVALPHA_AUTH=token")
    d = database or db()
    if d.scalar("SELECT count(*) FROM login_attempt WHERE ip = :ip AND NOT ok AND at > now() - :w", ip=ip, w=IP_WINDOW) >= IP_LIMIT:
        log.warning("sign-in refused: too many failures from one address")
        raise TooManyRequests(f"too many failed sign-ins from this address; try again in {int(IP_WINDOW.total_seconds() // 60)} minutes")
    u = d.one("SELECT id, password_hash, status, locked_until FROM app_user WHERE username = :u", u=(username or "").strip().lower())
    ok = verify_password(password or "", u["password_hash"] if u else _dummy_hash())
    locked = u is not None and u["locked_until"] is not None and u["locked_until"] > _now()
    if u is None or not ok or locked or u["status"] != "ACTIVE":
        with d.transaction():
            d.execute("INSERT INTO login_attempt (ip, user_id, ok) VALUES (:ip, :u, false)", ip=ip, u=u and u["id"])
            if u is not None and not ok and not locked:
                d.execute("""
                    UPDATE app_user SET
                        failed_logins = CASE WHEN locked_until IS NOT NULL AND locked_until <= now() THEN 1 ELSE failed_logins + 1 END,
                        locked_until = CASE WHEN (CASE WHEN locked_until IS NOT NULL AND locked_until <= now() THEN 1 ELSE failed_logins + 1 END) >= :n
                                            THEN now() + :lock ELSE NULL END
                    WHERE id = :id""", id=u["id"], n=LOCK_AFTER, lock=LOCK_FOR)
        log.info("sign-in failed: %s", f"user {u['id']}" if u else "unknown user")
        raise Unauthorized("wrong username or password")
    raw = secrets.token_urlsafe(32)
    with d.transaction():
        d.execute("UPDATE app_user SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = :id", id=u["id"])
        d.execute("INSERT INTO login_attempt (ip, user_id, ok) VALUES (:ip, :u, true)", ip=ip, u=u["id"])
        d.execute("INSERT INTO user_session (user_id, token_sha256, expires_at) VALUES (:u, :h, now() + :max)",
                  u=u["id"], h=_sha(raw), max=SESSION_MAX)
        # housekeeping on the sign-in path, which is rare: expired sessions and old attempts
        d.execute("DELETE FROM user_session WHERE expires_at < now() - interval '1 day' OR revoked_at < now() - interval '1 day'")
        d.execute("DELETE FROM login_attempt WHERE at < now() - interval '1 day'")
    log.info("sign-in: user %s", u["id"])
    return session_caller(raw, d), raw


def logout(raw: str | None, database: Db | None = None) -> None:
    if raw:
        (database or db()).execute("UPDATE user_session SET revoked_at = now() WHERE token_sha256 = :h AND revoked_at IS NULL", h=_sha(raw))


def revoke_credentials(user_id: int, database: Db, keep_session: int | None = None) -> None:
    """Every session (except `keep_session`) and every personal token of one user stop at once."""
    database.execute("UPDATE user_session SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL AND id IS DISTINCT FROM :k",
                     u=user_id, k=keep_session)
    database.execute("UPDATE api_token SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL", u=user_id)


def new_token() -> tuple[str, str, str]:
    """(raw token shown once, its SHA-256, the display prefix)."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, _sha(raw), raw[:len(TOKEN_PREFIX) + 6]


def client_ip(headers: dict, peer: str | None) -> str:
    """nginx's X-Real-IP (the api port is not published, so only nginx sets it); the peer address otherwise."""
    return (headers.get("x-real-ip") or peer or "unknown").strip()[:64]


# --------------------------------------------------------------------------- audit
def record(database: Db, caller: Caller, action: str, target_type: str, target_id=None, before: dict | None = None,
           after: dict | None = None, portfolio_id: int | None = None) -> None:
    """One audit row. Callers pass shapes without secrets: never a password hash, a token or a cookie value."""
    database.execute("""
        INSERT INTO audit_event (actor_user_id, actor_kind, action, target_type, target_id, portfolio_id, before, after)
        VALUES (:u, :k, :a, :t, :ti, :p, CAST(:b AS jsonb), CAST(:af AS jsonb))""",
        u=caller.user_id, k=caller.audit_kind, a=action, t=target_type, ti=None if target_id is None else str(target_id),
        p=portfolio_id, b=None if before is None else jsonb(before), af=None if after is None else jsonb(after))

