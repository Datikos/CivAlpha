"""ADR-0005: accounts (created by the owner), password changes, personal tokens and the audit list.

CLI for the first owner, run where the api runs:
    docker compose exec api python -m civalpha.platform.users create-owner <username> [display name]
It asks for the password twice (or reads one line from stdin when not on a terminal). Creating the first OWNER assigns
every portfolio without an owner to it.
"""
from __future__ import annotations

import getpass
import re
import sys
from datetime import timedelta

from . import auth
from .auth import Caller
from .errors import BadRequest, NotFound
from .rows import camel, camel_all
from .sql import Db, db

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
PUBLIC = "id, username, display_name, email, role, status, must_change_password, locked_until, last_login_at, created_at, updated_at"


def _shape(u: dict | None) -> dict | None:
    """A user as the audit trail and the API see it: never the password hash."""
    return None if u is None else {k: v for k, v in camel(u).items() if k != "passwordHash"}


class UserService:
    def __init__(self, database: Db | None = None):
        self.db = database or db()

    # ---------------------------------------------------------------- the owner manages accounts
    def list(self) -> list[dict]:
        return camel_all(self.db.all(f"SELECT {PUBLIC} FROM app_user ORDER BY role, username"))

    def get(self, user_id: int) -> dict:
        u = self.db.one(f"SELECT {PUBLIC} FROM app_user WHERE id = :id", id=user_id)
        if u is None:
            raise NotFound(f"user {user_id} not found")
        return u

    def create(self, actor: Caller, username: str, display_name: str, role: str, password: str, email: str | None = None,
               must_change: bool = True) -> dict:
        name = (username or "").strip().lower()
        if not USERNAME.match(name):
            raise BadRequest("username: 3 to 40 characters, lower-case letters, digits, '.', '_' or '-', starting with a letter or digit")
        if role not in auth.ROLES:
            raise BadRequest(f"role: one of {', '.join(auth.ROLES)}")
        display = (display_name or "").strip()
        if not 1 <= len(display) <= 100:
            raise BadRequest("displayName: 1 to 100 characters")
        if email is not None and len(email) > 200:
            raise BadRequest("email: at most 200 characters")
        auth.check_password_policy(password, name)
        with self.db.transaction():
            if self.db.scalar("SELECT 1 FROM app_user WHERE username = :u", u=name):
                raise BadRequest(f"username {name} is taken")
            first_owner = role == "OWNER" and not self.db.scalar("SELECT 1 FROM app_user WHERE role = 'OWNER'")
            uid = self.db.scalar("""INSERT INTO app_user (username, display_name, email, role, password_hash, must_change_password)
                                    VALUES (:u, :d, :e, :r, :h, :m) RETURNING id""",
                                 u=name, d=display, e=(email or None), r=role, h=auth.hash_password(password), m=must_change)
            if first_owner:     # the installation's own portfolios (token mode) belong to the first owner
                self.db.execute("UPDATE portfolio SET owner_user_id = :u WHERE owner_user_id IS NULL", u=uid)
            after = self.get(uid)
            auth.record(self.db, actor, "USER_CREATED", "user", uid, after=_shape(after))
        return _shape(after)

    def update(self, actor: Caller, user_id: int, display_name: str | None = None, email: str | None = None,
               role: str | None = None, status: str | None = None) -> dict:
        with self.db.transaction():
            before = self.get(user_id)
            if role is not None and role not in auth.ROLES:
                raise BadRequest(f"role: one of {', '.join(auth.ROLES)}")
            if status is not None and status not in ("ACTIVE", "DISABLED"):
                raise BadRequest("status: ACTIVE or DISABLED")
            if display_name is not None and not 1 <= len(display_name.strip()) <= 100:
                raise BadRequest("displayName: 1 to 100 characters")
            losing_owner = before["role"] == "OWNER" and before["status"] == "ACTIVE" and (
                (role is not None and role != "OWNER") or status == "DISABLED")
            if losing_owner and self.db.scalar(
                    "SELECT count(*) FROM app_user WHERE role = 'OWNER' AND status = 'ACTIVE' AND id <> :id", id=user_id) == 0:
                raise BadRequest("the last active owner cannot be demoted or disabled")
            self.db.execute("""
                UPDATE app_user SET display_name = COALESCE(:d, display_name), email = COALESCE(:e, email),
                    role = COALESCE(:r, role), status = COALESCE(:s, status), updated_at = now() WHERE id = :id""",
                d=display_name.strip() if display_name else None, e=email, r=role, s=status, id=user_id)
            if status == "DISABLED" or (role is not None and role != before["role"]):
                auth.revoke_credentials(user_id, self.db)
            after = self.get(user_id)
            auth.record(self.db, actor, "USER_UPDATED", "user", user_id, before=_shape(before), after=_shape(after))
        return _shape(after)

    def reset_password(self, actor: Caller, user_id: int, password: str) -> dict:
        """The owner sets a new first password (there is no e-mail reset): the user changes it at the next sign-in, and
        every session and token of the user stops."""
        with self.db.transaction():
            u = self.get(user_id)
            auth.check_password_policy(password, u["username"])
            self.db.execute("""UPDATE app_user SET password_hash = :h, must_change_password = true, failed_logins = 0,
                               locked_until = NULL, updated_at = now() WHERE id = :id""", h=auth.hash_password(password), id=user_id)
            auth.revoke_credentials(user_id, self.db)
            auth.record(self.db, actor, "PASSWORD_RESET", "user", user_id)
        return _shape(self.get(user_id))

    # ---------------------------------------------------------------- a user's own account
    def change_password(self, caller: Caller, current: str, new: str) -> None:
        if caller.user_id is None:
            raise BadRequest("only a signed-in user has a password to change")
        with self.db.transaction():
            u = self.db.one("SELECT username, password_hash FROM app_user WHERE id = :id", id=caller.user_id)
            if u is None or not auth.verify_password(current or "", u["password_hash"]):
                raise BadRequest("currentPassword: wrong password")
            auth.check_password_policy(new, u["username"])
            if current == new:
                raise BadRequest("newPassword: must differ from the current one")
            self.db.execute("""UPDATE app_user SET password_hash = :h, must_change_password = false, updated_at = now()
                               WHERE id = :id""", h=auth.hash_password(new), id=caller.user_id)
            auth.revoke_credentials(caller.user_id, self.db, keep_session=caller.session_id)
            auth.record(self.db, caller, "PASSWORD_CHANGED", "user", caller.user_id)

    def tokens(self, caller: Caller) -> list[dict]:
        return camel_all(self.db.all("""
            SELECT id, name, prefix, created_at, last_used_at, expires_at, revoked_at,
                   (revoked_at IS NULL AND expires_at > now()) AS active
            FROM api_token WHERE user_id = :u ORDER BY created_at DESC""", u=_user(caller)))

    def create_token(self, caller: Caller, name: str, days: int) -> dict:
        uid = _user(caller)
        name = (name or "").strip()
        if not 1 <= len(name) <= 60:
            raise BadRequest("name: 1 to 60 characters")
        if not isinstance(days, int) or not 1 <= days <= auth.TOKEN_MAX_DAYS:
            raise BadRequest(f"days: 1 to {auth.TOKEN_MAX_DAYS}")
        raw, digest, prefix = auth.new_token()
        with self.db.transaction():
            tid = self.db.scalar("""INSERT INTO api_token (user_id, name, token_sha256, prefix, expires_at)
                                    VALUES (:u, :n, :h, :p, now() + :d) RETURNING id""", u=uid, n=name, h=digest, p=prefix,
                                 d=timedelta(days=days))
            auth.record(self.db, caller, "TOKEN_CREATED", "token", tid, after={"name": name, "prefix": prefix, "days": days})
        row = next(t for t in self.tokens(caller) if t["id"] == tid)
        return {**row, "token": raw}     # the only time the token is shown

    def revoke_token(self, caller: Caller, token_id: int) -> None:
        with self.db.transaction():
            n = self.db.execute("UPDATE api_token SET revoked_at = now() WHERE id = :id AND user_id = :u AND revoked_at IS NULL",
                                id=token_id, u=_user(caller))
            if n == 0:
                raise NotFound(f"token {token_id} not found")
            auth.record(self.db, caller, "TOKEN_REVOKED", "token", token_id)

    # ---------------------------------------------------------------- audit
    def audit(self, limit: int = 200, user_id: int | None = None) -> list[dict]:
        return camel_all(self.db.all("""
            SELECT e.id, e.at, e.actor_user_id, a.username AS actor_username, e.actor_kind, e.action, e.target_type,
                   e.target_id, e.portfolio_id, e.before, e.after
            FROM audit_event e LEFT JOIN app_user a ON a.id = e.actor_user_id
            WHERE CAST(:u AS bigint) IS NULL OR e.actor_user_id = :u
            ORDER BY e.at DESC, e.id DESC LIMIT :n""", u=user_id, n=max(1, min(limit, 1000))))


def _user(caller: Caller) -> int:
    if caller.user_id is None:
        raise BadRequest("personal tokens belong to a signed-in user")
    return caller.user_id


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] != "create-owner":
        print(__doc__)
        raise SystemExit(2)
    username = argv[1]
    display = " ".join(argv[2:]) or username
    if sys.stdin.isatty():
        pw = getpass.getpass("Password (12 or more characters): ")
        if pw != getpass.getpass("Repeat: "):
            raise SystemExit("passwords differ")
    else:
        pw = sys.stdin.readline().rstrip("\n")
    u = UserService().create(auth.LOCAL, username, display, "OWNER", pw, must_change=False)   # typed by the owner
    print(f"created OWNER {u['username']} (id {u['id']}). "
          "Set CIVALPHA_AUTH=accounts and restart the api to require sign-in.")


if __name__ == "__main__":
    main(sys.argv[1:])
