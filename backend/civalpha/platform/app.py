"""The CivAlpha API (FastAPI). Run: uvicorn civalpha.platform.app:app

Access (ADR-0005, `required_role`): CIVALPHA_AUTH=token (default) keeps the shared secret: when CIVALPHA_ADMIN_TOKEN is
set, /api/admin/** and /api/portfolio* (any method, GET included: holdings are personal data, ADR-0004) and every
non-GET /api request must carry it as "X-Admin-Token: <token>" or "Authorization: Bearer <token>"; other reads stay
public; without a token everything is open, which is only safe on localhost. CIVALPHA_AUTH=accounts requires a
signed-in user (session cookie), a personal token or the admin token on every /api and /mcp request except sign-in.

/mcp serves the same data and actions to AI assistants over the Model Context Protocol (civalpha.platform.mcp_server);
its management tools apply the same token rule.
"""
from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import auth
from .errors import BadRequest, Forbidden, NotFound, Problem, TooManyRequests, Unauthorized, Unavailable
from .settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("civalpha.api")



@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    from .mcp_server import lifespan as mcp_lifespan

    async with mcp_lifespan():
        yield


app = FastAPI(title="CivAlpha API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
              lifespan=lifespan)


SAFE = ("GET", "HEAD", "OPTIONS")
ANONYMOUS_OK = {("GET", "/api/meta"), ("GET", "/api/auth/me"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout")}
MUST_CHANGE_ALLOWED = {"/api/meta", "/api/auth/me", "/api/auth/password", "/api/auth/logout"}


def required_role(path: str, method: str, accounts: bool) -> str:
    """ADR-0005: the one rule table. NONE (anyone), USER (any signed-in caller) or OWNER, decided on the path and method.

    token mode (as before): /api/admin/**, /api/portfolio* and every non-GET /api call need the owner (the admin token,
    or nobody when no token is configured); everything else is public; /mcp tools check the token themselves.
    accounts mode: only /health, GET /api/meta, GET /api/auth/me and POST /api/auth/login|logout answer anonymously;
    /api/admin/** and every non-GET outside /api/portfolio* and /api/auth/** need OWNER; the rest of /api and /mcp
    needs a signed-in user."""
    api = path.startswith("/api/")
    mcp = path == "/mcp" or path.startswith("/mcp/")
    if not (api or mcp):
        return "NONE"
    if not accounts:
        if mcp or path.startswith("/api/auth/"):
            return "NONE"
        if path.startswith("/api/admin/") or path.startswith("/api/portfolio"):
            return "OWNER"
        return "NONE" if method in SAFE else "OWNER"
    if (method, path) in ANONYMOUS_OK:
        return "NONE"
    if mcp:
        return "USER"
    if path.startswith("/api/admin/"):
        return "OWNER"
    if path.startswith("/api/auth/") or path.startswith("/api/portfolio"):
        return "USER"
    return "USER" if method in SAFE else "OWNER"


def protected_request(path: str, method: str) -> bool:
    """token mode: does the request need the admin token?"""
    return required_role(path, method, accounts=False) == "OWNER"


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path, method = request.url.path, request.method
    if not (path.startswith("/api/") or path == "/mcp" or path.startswith("/mcp/")):
        return await call_next(request)
    s = settings()
    headers = {k.lower(): v for k, v in request.headers.items()}
    cookie = request.cookies.get(auth.SESSION_COOKIE) if s.accounts else None
    bearer_token = (headers.get("authorization") or "").startswith("Bearer " + auth.TOKEN_PREFIX)
    if s.accounts and (cookie or bearer_token):
        caller = await run_in_threadpool(auth.caller_from, headers, cookie)    # one indexed lookup, off the event loop
    else:
        caller = auth.caller_from(headers)                                     # no database access
    request.state.caller = caller
    need = required_role(path, method, s.accounts)
    if need != "NONE" and not caller.signed_in:
        msg = "sign in required" if s.accounts else "admin token required (X-Admin-Token header)"
        return JSONResponse({"error": msg}, status_code=401)
    if need == "OWNER" and not caller.owner:
        return JSONResponse({"error": "this needs the owner role"}, status_code=403)
    if caller.kind == "SESSION":
        if caller.must_change_password and path not in MUST_CHANGE_ALLOWED:
            return JSONResponse({"error": "change your password first", "code": "PASSWORD_CHANGE_REQUIRED"}, status_code=403)
        if method not in SAFE and headers.get(auth.CSRF_HEADER) != "1":
            return JSONResponse({"error": "missing X-CivAlpha-Request header"}, status_code=403)
    return await call_next(request)


@app.exception_handler(Unauthorized)
async def _unauthorized(_: Request, e: Unauthorized):
    return JSONResponse({"error": str(e)}, status_code=401)


@app.exception_handler(Forbidden)
async def _forbidden(_: Request, e: Forbidden):
    return JSONResponse({"error": str(e)}, status_code=403)


@app.exception_handler(TooManyRequests)
async def _too_many(_: Request, e: TooManyRequests):
    return JSONResponse({"error": str(e)}, status_code=429)


@app.exception_handler(NotFound)
async def _not_found(_: Request, e: NotFound):
    return JSONResponse({"error": str(e)}, status_code=404)


@app.exception_handler(BadRequest)
async def _bad_request(_: Request, e: BadRequest):
    return JSONResponse({"error": str(e)}, status_code=400)


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, e: RequestValidationError):
    first = e.errors()[0] if e.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse({"error": f"invalid request: {where} {first.get('msg', '')}".strip()}, status_code=400)


@app.exception_handler(Unavailable)
async def _unavailable(_: Request, e: Unavailable):
    return JSONResponse({"error": str(e)}, status_code=503)


@app.exception_handler(Problem)
async def _problem(_: Request, e: Problem):
    return JSONResponse({"error": str(e)}, status_code=409)


@app.get("/health")
def health():
    return {"status": "ok"}


if settings().accounts:
    log.info("CIVALPHA_AUTH=accounts: every /api and /mcp request needs a signed-in user, a personal token or the admin token")
elif not settings().admin_token_required:
    log.warning("CIVALPHA_ADMIN_TOKEN is not set: admin and write endpoints are unauthenticated (keep the stack on localhost)")

from .api import admin, auth as auth_api, events, portfolio, read, universe  # noqa: E402  (routers import the services)
from . import mcp_server  # noqa: E402

app.include_router(read.router)
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(universe.router)
app.include_router(portfolio.router)
app.include_router(auth_api.router)
app.include_router(auth_api.admin_router)
mcp_server.mount(app)
