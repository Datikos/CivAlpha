"""Forecast store: issue forecasts and keep them immutable and versioned.

A forecast series is (company, model, as-of trading date). Re-issuing a series with changed inputs or output creates
version n+1 linked to version n; an identical re-issue is skipped. Database triggers reject UPDATE/DELETE on forecast.

The identity of a forecast's content is a SHA-256 over a canonical JSON form. It is byte-for-byte the form the Java
backend wrote (Jackson, keys sorted, numbers rounded to 6 decimals and printed like Java's Double.toString), so
forecasts stored before the move keep matching and an unchanged re-issue is still recognised as unchanged.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .. import service as ml
from .sql import Db, db, engine, jsonb
from .storage import sha256

HASH_KEYS = ("companyId", "modelKind", "asOfDate", "probability", "probLow", "probHigh", "features", "explanation", "sources", "target")


# --------------------------------------------------------------------------- Java-compatible canonical JSON
def java_double(x: float) -> str:
    """Java's Double.toString for a finite double (shortest round-trip digits)."""
    if x == 0.0:
        return "-0.0" if math.copysign(1.0, x) < 0 else "0.0"
    d = Decimal(repr(x)).normalize()
    sign, digits, exp = d.as_tuple()
    neg = "-" if sign else ""
    if 1e-3 <= abs(x) < 1e7:
        s = format(abs(d), "f")
        return neg + (s if "." in s else s + ".0")
    ds = "".join(map(str, digits))
    e10 = exp + len(ds) - 1
    return f"{neg}{ds[0]}.{ds[1:] or '0'}E{e10}"


def _canonical(o):
    if isinstance(o, dict):
        return {str(k): _canonical(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canonical(v) for v in o]
    if isinstance(o, float) and not isinstance(o, bool):
        return math.floor(o * 1e6 + 0.5) / 1e6      # Math.round(d * 1e6) / 1e6
    return o


def _write(o, out: list[str]) -> None:
    if o is None:
        out.append("null")
    elif o is True:
        out.append("true")
    elif o is False:
        out.append("false")
    elif isinstance(o, int):
        out.append(str(o))
    elif isinstance(o, float):
        out.append(java_double(o))
    elif isinstance(o, str):
        out.append(json.dumps(o, ensure_ascii=False))
    elif isinstance(o, dict):
        out.append("{")
        for i, k in enumerate(sorted(o)):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _write(o[k], out)
        out.append("}")
    elif isinstance(o, list):
        out.append("[")
        for i, v in enumerate(o):
            if i:
                out.append(",")
            _write(v, out)
        out.append("]")
    else:
        raise TypeError(f"cannot hash {type(o).__name__}")


def content_hash(p: dict) -> str:
    """Hash of everything a reader sees, excluding the model-version row id (an identical refit is not new evidence)."""
    c = {k: _canonical(p.get(k)) for k in HASH_KEYS}
    out: list[str] = []
    _write(c, out)
    return sha256("".join(out).encode("utf-8"))


# --------------------------------------------------------------------------- store
@dataclass
class IssueResult:
    created: int = 0
    unchanged: int = 0
    ids: list[int] = field(default_factory=list)


class ForecastService:
    def __init__(self, database: Db | None = None, issue_fn=None):
        self.db = database or db()
        # the ML issue function returns forecast payloads; injected in tests
        self.issue_fn = issue_fn or (lambda **kw: ml.issue(engine(), **kw)["forecasts"])

    def issue_live(self, company_ids: list[int] | None, reason: str) -> IssueResult:
        """Live issue: data cutoff = now; the window starts at the latest close at or before now."""
        now = datetime.now(timezone.utc).isoformat()
        return self.persist_all(self.issue_fn(as_of=now, company_ids=company_ids), "LIVE", reason)

    def issue_replay(self, dates: list[date], reason: str) -> IssueResult:
        """Replay issue: cutoff = close of each historical date; published now and labelled REPLAY."""
        return self.persist_all(self.issue_fn(as_of_dates=[d.isoformat() for d in dates]), "REPLAY", reason)

    def issue_at(self, as_of_date: date, reason: str) -> IssueResult:
        mode = "REPLAY" if as_of_date < date.today() - timedelta(days=7) else "LIVE"
        return self.persist_all(self.issue_fn(as_of_dates=[as_of_date.isoformat()]), mode, reason)

    def persist_all(self, payloads: list[dict], mode: str, reason: str) -> IssueResult:
        r = IssueResult()
        for p in payloads:
            fid = self.persist(json.loads(json.dumps(p)), mode, reason)   # JSON round trip: same value types as the wire format
            if fid is None:
                r.unchanged += 1
            else:
                r.created += 1
                r.ids.append(fid)
        return r

    def persist(self, p: dict, mode: str, reason: str) -> int | None:
        """Returns the new forecast id, or None if identical to the latest version of its series."""
        company_id = int(p["companyId"])
        kind = p["modelKind"]
        as_of_date = p["asOfDate"]
        series = f"{company_id}|{kind}|{as_of_date}"
        digest = content_hash(p)
        with self.db.transaction():
            prev = self.db.one("SELECT id, version, content_sha256 FROM forecast WHERE series_key = :s ORDER BY version DESC LIMIT 1", s=series)
            if prev is not None and prev["content_sha256"] == digest:
                return None
            version = prev["version"] + 1 if prev else 1
            demo = bool(self.db.scalar("SELECT is_demo FROM company WHERE id = :c", c=company_id))
            return self.db.scalar("""
                INSERT INTO forecast (company_id, symbol, benchmark_symbol, model_kind, model_version_id, target, horizon_trading_days,
                    as_of, as_of_date, issue_mode, probability, prob_low, prob_high, uncertainty_note, features, explanation, sources,
                    series_key, version, supersedes_id, reason, content_sha256, is_demo)
                VALUES (:c, :sym, :bench, :kind, :mv, :target, :h, :asof, :d, :mode, :p, :lo, :hi, :note,
                    CAST(:features AS jsonb), CAST(:expl AS jsonb), CAST(:sources AS jsonb), :series, :v, :sup, :reason, :hash, :demo)
                RETURNING id""",
                c=company_id, sym=p.get("symbol"), bench=p.get("benchmarkSymbol"), kind=kind, mv=int(p["modelVersionId"]),
                target=p.get("target"), h=int(p["horizonTradingDays"]), asof=datetime.fromisoformat(p["asOf"]),
                d=date.fromisoformat(as_of_date), mode=mode, p=_num(p.get("probability")), lo=_num(p.get("probLow")),
                hi=_num(p.get("probHigh")), note=p.get("uncertaintyNote"), features=jsonb(p.get("features")),
                expl=jsonb(p.get("explanation")), sources=jsonb(p.get("sources")), series=series, v=version,
                sup=prev["id"] if prev else None,
                reason=f"{reason} (supersedes v{version - 1})" if version > 1 else reason, hash=digest, demo=demo)


def _num(o):
    return None if o is None else float(o)
