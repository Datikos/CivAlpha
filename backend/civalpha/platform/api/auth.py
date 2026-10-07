"""Sign-in, the caller's own account, and the owner's account management (ADR-0005). The role each route needs is
decided once, in app.required_role; the handlers read the caller the middleware resolved."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from .. import auth
from ..auth import Caller
from ..errors import Forbidden
from ..settings import settings
from ..users import UserService

router = APIRouter(prefix="/api/auth")
admin_router = APIRouter(prefix="/api/admin")


def caller(request: Request) -> Caller:
    """The caller the middleware resolved; anonymous when there is none (never the local operator by default)."""
    return getattr(request.state, "caller", auth.ANONYMOUS)


def _me(c: Caller) -> dict:
    return {"authMode": settings().auth_mode, "signedIn": c.signed_in, "caller": c.public() if c.signed_in else None}


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    currentPassword: str
    newPassword: str


class TokenIn(BaseModel):
    name: str
    days: int = 30


class UserIn(BaseModel):
    username: str
    displayName: str
    role: str = "MEMBER"
    password: str
    email: str | None = None


class UserEditIn(BaseModel):
    displayName: str | None = None
    email: str | None = None
    role: str | None = None
    status: str | None = None


class ResetIn(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    headers = {k.lower(): v for k, v in request.headers.items()}
    c, raw = auth.login(body.username, body.password, auth.client_ip(headers, request.client.host if request.client else None))
    response.set_cookie(auth.SESSION_COOKIE, raw, max_age=int(auth.SESSION_MAX.total_seconds()), path="/", httponly=True,
                        samesite="strict", secure=settings().cookie_secure)
    return _me(c)


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=settings().cookie_secure)
    return {"signedIn": False}


@router.get("/me")
def me(request: Request):
    return _me(caller(request))


@router.post("/password")
def change_password(body: PasswordIn, request: Request):
    c = caller(request)
    UserService().change_password(c, body.currentPassword, body.newPassword)
    return _me(auth.Caller(c.kind, c.role, c.user_id, c.username, c.display_name, False, c.session_id))


@router.get("/tokens")
def tokens(request: Request):
    return UserService().tokens(caller(request))


@router.post("/tokens")
def create_token(body: TokenIn, request: Request):
    return UserService().create_token(caller(request), body.name, body.days)


@router.post("/tokens/{token_id}/revoke", status_code=204)
def revoke_token(token_id: int, request: Request):
    UserService().revoke_token(caller(request), token_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- the owner's account management
@admin_router.get("/users")
def users():
    return UserService().list()


@admin_router.post("/users")
def create_user(body: UserIn, request: Request):
    return UserService().create(caller(request), body.username, body.displayName, body.role, body.password, body.email)


@admin_router.put("/users/{user_id}")
def edit_user(user_id: int, body: UserEditIn, request: Request):
    c = caller(request)
    if c.user_id == user_id and (body.status == "DISABLED" or (body.role and body.role != c.role)):
        raise Forbidden("you cannot disable or demote your own account; ask another owner")
    return UserService().update(c, user_id, body.displayName, body.email, body.role, body.status)


@admin_router.post("/users/{user_id}/password")
def reset_password(user_id: int, body: ResetIn, request: Request):
    return UserService().reset_password(caller(request), user_id, body.password)


@admin_router.get("/audit")
def audit(limit: int = Query(200, ge=1, le=1000), userId: int | None = None):  # noqa: N803
    return UserService().audit(limit, userId)
