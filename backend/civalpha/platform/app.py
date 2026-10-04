"""The CivAlpha API (FastAPI). Run: uvicorn civalpha.platform.app:app

Optional shared-secret protection: when CIVALPHA_ADMIN_TOKEN is set, /api/admin/** (any method) and every non-GET
/api request must carry it as "X-Admin-Token: <token>" or "Authorization: Bearer <token>". Read-only endpoints stay
public. Without a token everything is open, which is only safe on localhost.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import BadRequest, NotFound, Problem, Unavailable
from .settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("civalpha.api")

app = FastAPI(title="CivAlpha API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)


def protected_request(path: str, method: str) -> bool:
    if not path.startswith("/api/"):
        return False
    if path.startswith("/api/admin/"):
        return True
    return method not in ("GET", "HEAD", "OPTIONS")


@app.middleware("http")
async def admin_token(request: Request, call_next):
    token = settings().admin_token
    if token and protected_request(request.url.path, request.method):
        presented = request.headers.get("X-Admin-Token")
        auth = request.headers.get("Authorization")
        if presented is None and auth and auth.startswith("Bearer "):
            presented = auth[7:].strip()
        if presented is None or not hmac.compare_digest(token.encode(), presented.encode()):
            return JSONResponse({"error": "admin token required (X-Admin-Token header)"}, status_code=401)
    return await call_next(request)


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


if not settings().admin_token_required:
    log.warning("CIVALPHA_ADMIN_TOKEN is not set: admin and write endpoints are unauthenticated (keep the stack on localhost)")

from .api import admin, events, read, universe  # noqa: E402  (routers import the services)

app.include_router(read.router)
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(universe.router)
