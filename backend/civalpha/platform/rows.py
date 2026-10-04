"""API row shaping: snake_case -> camelCase keys and JSON-friendly values (dates as ISO strings, numerics as floats)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any


def camel_key(k: str) -> str:
    out, up = [], False
    for ch in k:
        if ch == "_":
            up = True
        else:
            out.append(ch.upper() if up else ch)
            up = False
    return "".join(out)


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def value(v: Any) -> Any:
    if isinstance(v, datetime):
        return iso_utc(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def camel(row: dict) -> dict:
    return {camel_key(k): value(v) for k, v in row.items()}


def camel_all(rows: list[dict]) -> list[dict]:
    return [camel(r) for r in rows]
