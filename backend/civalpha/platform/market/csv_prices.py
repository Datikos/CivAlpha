"""CSV parsing for prices (symbol,date,open,high,low,close,volume) and corporate actions
(symbol,ex_date,action_type,value[,announced_at])."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

_DECIMAL = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")
_LONG = re.compile(r"[+-]?\d+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINES = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class PriceBarRow:
    symbol: str
    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None


@dataclass(frozen=True)
class ActionRow:
    symbol: str
    ex_date: date
    type: str               # SPLIT | SPLIT_INFO | CASH_DIVIDEND
    value: Decimal
    announced_at: datetime | None


def parse_decimal(s: str) -> Decimal:
    """Strict decimal literal (like java.math.BigDecimal(String)): no NaN, infinities or digit separators."""
    if not _DECIMAL.fullmatch(s):
        raise ValueError(f"not a decimal number: '{s}'")
    return Decimal(s)


def parse_date(s: str) -> date:
    """Strict ISO yyyy-MM-dd (like LocalDate.parse)."""
    if not _DATE.fullmatch(s):
        raise ValueError(f"Text '{s}' could not be parsed as a date (yyyy-MM-dd)")
    return date.fromisoformat(s)


def _long(s: str) -> int:
    if not _LONG.fullmatch(s):
        raise ValueError(f'For input string: "{s}"')
    return int(s)


def _offset_datetime(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"Text '{s}' could not be parsed: an offset (e.g. Z or +00:00) is required")
    return dt


def _text(data: bytes | str) -> str:
    return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data


def _lines(data: bytes | str) -> list[str]:
    text = _text(data)
    if text == "":
        raise ValueError("empty CSV")
    lines = _LINES.split(text)
    if lines and lines[-1] == "":
        lines.pop()   # a trailing newline does not start another line
    if not lines:
        raise ValueError("empty CSV")
    return lines


def _header(line: str) -> dict[str, int]:
    cols = line.replace("﻿", "").split(",")
    return {c.strip().lower(): i for i, c in enumerate(cols)}


def _dec(c: list[str], i: int | None) -> Decimal | None:
    return None if i is None or i >= len(c) or c[i].strip() == "" else parse_decimal(c[i].strip())


def parse_bars(data: bytes | str) -> list[PriceBarRow]:
    lines = _lines(data)
    h = _header(lines[0])
    for req in ("symbol", "date", "close"):
        if req not in h:
            raise ValueError(f"price CSV needs column '{req}'")
    out = []
    for line in lines[1:]:
        if line.strip() == "":
            continue
        c = line.split(",")
        vol = h.get("volume")
        out.append(PriceBarRow(c[h["symbol"]].strip().upper(), parse_date(c[h["date"]].strip()),
                               _dec(c, h.get("open")), _dec(c, h.get("high")), _dec(c, h.get("low")), _dec(c, h.get("close")),
                               _long(c[vol].strip()) if vol is not None and c[vol].strip() != "" else None))
    return out


def parse_actions(data: bytes | str) -> list[ActionRow]:
    lines = _lines(data)
    h = _header(lines[0])
    out = []
    for line in lines[1:]:
        if line.strip() == "":
            continue
        c = line.split(",")
        ann = c[h["announced_at"]].strip() if "announced_at" in h else ""
        out.append(ActionRow(c[h["symbol"]].strip().upper(), parse_date(c[h["ex_date"]].strip()), c[h["action_type"]].strip(),
                             parse_decimal(c[h["value"]].strip()), None if ann == "" else _offset_datetime(ann)))
    return out
