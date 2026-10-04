"""Thin SQL helper over SQLAlchemy Core: named parameters (:name), dict rows, optional transactions.

Outside `transaction()` every statement commits on its own (like the Java JdbcClient). Inside it, all statements
of the current thread/task share one connection and commit together; nested `transaction()` calls join the outer one.
"""
from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .settings import settings

_conn: contextvars.ContextVar[Connection | None] = contextvars.ContextVar("civalpha_sql_conn", default=None)


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def jsonb(o: Any) -> str:
    """Serialize a parameter for CAST(:x AS jsonb)."""
    return json.dumps(o, default=_json_default)


class Db:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def transaction(self) -> Iterator["Db"]:
        if _conn.get() is not None:
            yield self
            return
        with self.engine.begin() as c:
            token = _conn.set(c)
            try:
                yield self
            finally:
                _conn.reset(token)

    @contextmanager
    def rollback_scope(self) -> Iterator["Db"]:
        """Tests: everything inside runs in one transaction that is rolled back at the end."""
        with self.engine.connect() as c:
            tx = c.begin()
            token = _conn.set(c)
            try:
                yield self
            finally:
                _conn.reset(token)
                tx.rollback()

    def _exec(self, sql: str, params, fetch: str):
        c = _conn.get()
        if c is not None:
            return _consume(c.execute(text(sql), params), fetch)
        with self.engine.begin() as conn:
            return _consume(conn.execute(text(sql), params), fetch)

    def all(self, sql: str, **params) -> list[dict]:
        return self._exec(sql, params, "all")

    def one(self, sql: str, **params) -> dict | None:
        rows = self._exec(sql, params, "all")
        return rows[0] if rows else None

    def scalar(self, sql: str, **params):
        return self._exec(sql, params, "scalar")

    def scalars(self, sql: str, **params) -> list:
        return self._exec(sql, params, "scalars")

    def execute(self, sql: str, **params) -> int:
        return self._exec(sql, params, "rowcount")

    def executemany(self, sql: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        return self._exec(sql, rows, "rowcount")


def _consume(result, fetch: str):
    if fetch == "all":
        return [dict(r) for r in result.mappings().all()]
    if fetch == "scalar":
        return result.scalar()
    if fetch == "scalars":
        return list(result.scalars().all())
    return result.rowcount


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=8, max_overflow=4)


@lru_cache(maxsize=1)
def db() -> Db:
    return Db(engine())
