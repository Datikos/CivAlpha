"""Policy events: list, detail, and adding a sourced event (which re-issues affected forecasts when it is official)."""
from __future__ import annotations

import ipaddress
import logging
import socket
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from ..errors import BadRequest, NotFound
from ..events import EventDraft, EventService, Source, Target
from ..forecasts import ForecastService
from ..rows import camel, camel_all, value
from ..sql import db

router = APIRouter(prefix="/api")
log = logging.getLogger("civalpha.api.events")

CATEGORIES = {"MONETARY_POLICY", "TRADE_TARIFF"}
LIST_SQL = """
    SELECT e.id, e.category, e.event_type, e.title, e.event_date, e.published_at, e.first_seen_at, e.evidence_status,
           a.name AS actor_name, e.version, e.summary, e.attributes,
           (SELECT count(*) FROM event_source s WHERE s.event_id = e.id) AS source_count,
           (SELECT count(DISTINCT x.company_id) FROM event_target t JOIN company_exposure x
              ON x.target_type = t.target_type AND x.target_code = t.target_code WHERE t.event_id = e.id) AS affected_company_count
    FROM policy_event e LEFT JOIN policy_actor a ON a.id = e.actor_id"""


@router.get("/events")
def list_events(category: str | None = None):
    events = camel_all(db().all(LIST_SQL + " WHERE (CAST(:cat AS text) IS NULL OR e.category = :cat) ORDER BY e.published_at DESC",
                                cat=category or None))
    targets: dict[int, list] = {}
    for t in camel_all(db().all("SELECT event_id, target_type, target_code, magnitude FROM event_target ORDER BY id")):
        targets.setdefault(t.pop("eventId"), []).append(t)
    for e in events:
        e.pop("summary", None)
        e.pop("attributes", None)
        e["targets"] = targets.get(e["id"], [])
    return events


@router.get("/events/{event_id}")
def get_event(event_id: int):
    r = db().one(LIST_SQL + " WHERE e.id = :id", id=event_id)
    if r is None:
        raise NotFound(f"event {event_id} not found")
    e = camel(r)
    e["targets"] = camel_all(db().all("SELECT target_type, target_code, magnitude, note FROM event_target WHERE event_id = :id ORDER BY id",
                                      id=event_id))
    actor_id = db().scalar("SELECT actor_id FROM policy_event WHERE id = :id", id=event_id)
    if actor_id is not None:
        actor = camel(db().one("SELECT id, name, actor_type, authority, affiliation, profile_note FROM policy_actor WHERE id = :a", a=actor_id))
        actor["records"] = [{
            "recordType": r["record_type"], "occurredAt": value(r["occurred_at"]), "summary": r["summary"],
            "source": {"id": r["doc_id"], "title": r["title"] or "",
                       "url": r["url"]},
        } for r in db().all("""
            SELECT r.record_type, r.occurred_at, r.summary, sd.id AS doc_id, sd.url, sd.title
            FROM actor_record r JOIN source_document sd ON sd.id = r.source_document_id
            WHERE r.actor_id = :a ORDER BY r.occurred_at DESC LIMIT 25""", a=actor_id)]
        e["actor"] = actor
    else:
        e["actor"] = None
    sources = []
    for r in db().all("""
        SELECT sd.id, s.role, sd.source_type, sd.publisher, sd.title, sd.url, sd.accession_no, sd.published_at, sd.ingested_at,
               sd.version, sd.content_sha256, sd.storage_path
        FROM event_source s JOIN source_document sd ON sd.id = s.source_document_id WHERE s.event_id = :id ORDER BY s.linked_at""",
            id=event_id):
        m = camel(r)
        path = m.pop("storagePath")
        m["documentUrl"] = None if path is None else f"/api/documents/{m['id']}"
        sources.append(m)
    e["sources"] = sources
    affected: dict[str, dict] = {}
    for r in db().all("""
        SELECT DISTINCT ON (x.company_id, x.target_type, x.target_code, x.exposure_channel)
               x.company_id, c.name, (SELECT symbol FROM ticker_history th WHERE th.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
               t.target_type, t.target_code, x.id AS exposure_id, x.exposure_channel AS channel, x.basis, x.confidence, x.share, x.passage_id
        FROM event_target t
        JOIN company_exposure x ON x.target_type = t.target_type AND x.target_code = t.target_code
        JOIN policy_event e ON e.id = t.event_id
        JOIN company c ON c.id = x.company_id
        WHERE t.event_id = :id AND x.available_at <= greatest(e.published_at, now())
        ORDER BY x.company_id, x.target_type, x.target_code, x.exposure_channel, x.available_at DESC""", id=event_id):
        co = affected.setdefault(r["symbol"], {"symbol": r["symbol"], "name": r["name"], "paths": []})
        p = camel(r)
        for k in ("companyId", "name", "symbol"):
            p.pop(k)
        co["paths"].append(p)
    e["affectedCompanies"] = list(affected.values())
    e["reissuedForecasts"] = _reissued_forecasts(event_id)
    e["forecastShift"] = _forecast_shift(event_id)
    return e


def _reissued_forecasts(event_id: int) -> list[dict]:
    """Forecasts published because of this event (an official event re-issues every exposed company), next to the version they replaced."""
    return [{
        "symbol": r["symbol"], "modelKind": r["model_kind"], "id": r["id"], "version": r["version"], "issuedAt": value(r["issued_at"]),
        "probability": value(r["probability"]), "probLow": value(r["prob_low"]), "probHigh": value(r["prob_high"]),
        "previous": None if r["prev_id"] is None else {
            "id": r["prev_id"], "probability": value(r["prev_probability"]), "probLow": value(r["prev_low"]), "probHigh": value(r["prev_high"])},
    } for r in db().all("""
        SELECT f.id, f.symbol, f.model_kind, f.version, f.issued_at, f.probability, f.prob_low, f.prob_high,
               p.id AS prev_id, p.probability AS prev_probability, p.prob_low AS prev_low, p.prob_high AS prev_high
        FROM forecast f LEFT JOIN forecast p ON p.id = f.supersedes_id
        WHERE f.reason LIKE :pat ORDER BY f.symbol, f.model_kind, f.version""", pat=f"New evidence: event #{event_id} (%")]


def _forecast_shift(event_id: int) -> list[dict]:
    """For every exposed company and model: the last forecast before the event date and the first one on or after it."""
    return [{
        "symbol": r["symbol"], "modelKind": r["model_kind"],
        "before": {"id": r["before_id"], "asOfDate": value(r["before_date"]), "probability": value(r["before_p"])},
        "after": {"id": r["after_id"], "asOfDate": value(r["after_date"]), "probability": value(r["after_p"])},
    } for r in db().all("""
        WITH aff AS (SELECT DISTINCT x.company_id FROM event_target t JOIN company_exposure x
                       ON x.target_type = t.target_type AND x.target_code = t.target_code WHERE t.event_id = :id),
             ev AS (SELECT event_date AS d FROM policy_event WHERE id = :id),
             before AS (SELECT DISTINCT ON (f.company_id, f.model_kind) f.company_id, f.model_kind, f.symbol, f.id, f.probability, f.as_of_date
                        FROM forecast f, ev WHERE f.company_id IN (SELECT company_id FROM aff) AND f.as_of_date < ev.d
                        ORDER BY f.company_id, f.model_kind, f.as_of_date DESC, f.version DESC),
             after AS (SELECT DISTINCT ON (f.company_id, f.model_kind) f.company_id, f.model_kind, f.id, f.probability, f.as_of_date
                       FROM forecast f, ev WHERE f.company_id IN (SELECT company_id FROM aff) AND f.as_of_date >= ev.d
                       ORDER BY f.company_id, f.model_kind, f.as_of_date ASC, f.version DESC)
        SELECT b.symbol, b.model_kind, b.id AS before_id, b.probability AS before_p, b.as_of_date AS before_date,
               a.id AS after_id, a.probability AS after_p, a.as_of_date AS after_date
        FROM before b JOIN after a ON a.company_id = b.company_id AND a.model_kind = b.model_kind
        ORDER BY b.symbol, b.model_kind""", id=event_id)]


# --------------------------------------------------------------------------- create
class TargetIn(BaseModel):
    targetType: str
    targetCode: str
    magnitude: float | None = None


class SourceIn(BaseModel):
    url: str | None = None
    title: str | None = None
    publisher: str | None = None
    role: str | None = None


class EventIn(BaseModel):
    category: str | None = None
    eventType: str | None = None
    title: str | None = None
    summary: str | None = None
    eventDate: date | None = None
    publishedAt: datetime | None = None
    actorName: str | None = None
    attributes: dict[str, Any] | None = None
    targets: list[TargetIn] | None = None
    source: SourceIn | None = None


def public_host(host: str | None) -> bool:
    """Refuse to fetch from loopback/private/link-local addresses (SSRF guard); the URL is still recorded."""
    if not host:
        return False
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved
                or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))):
            return False
    return True


def _fetch(url: str) -> tuple[bytes | None, str | None]:
    try:
        if not public_host(urlparse(url).hostname):
            return None, None
        with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(20.0, connect=10.0)) as c:
            r = c.get(url, headers={"User-Agent": "CivAlpha research"})
        if r.status_code == 200 and len(r.content) < 20_000_000:
            return r.content, r.headers.get("Content-Type", "application/octet-stream")
    except Exception:  # noqa: BLE001 - stored as metadata-only evidence; the URL is still retained
        pass
    return None, None


@router.post("/events")
def create_event(body: EventIn):
    src = body.source
    if src is None or not src.url or not src.url.startswith(("http://", "https://")) or len(src.url) < 9:
        raise BadRequest("source.url (http/https) is required: every event must link to its original evidence")
    if body.category not in CATEGORIES:
        raise BadRequest(f"category must be one of {sorted(CATEGORIES)}")
    if not body.title or not body.title.strip() or body.eventDate is None or body.publishedAt is None:
        raise BadRequest("title, eventDate and publishedAt are required")
    content, content_type = _fetch(src.url)
    draft = EventDraft(
        category=body.category, event_type=body.eventType or "OTHER", title=body.title, summary=body.summary,
        event_date=body.eventDate, published_at=body.publishedAt, actor_name=body.actorName, attributes=body.attributes,
        targets=[Target(t.targetType, t.targetCode, t.magnitude) for t in body.targets or []],
        source=Source(url=src.url, title=src.title or body.title, publisher=src.publisher or urlparse(src.url).hostname,
                      role=src.role or "OFFICIAL_PRIMARY", published_at=body.publishedAt, content=content,
                      content_type=content_type))
    events = EventService()
    res = events.ingest(draft)
    reissued: list[int] = []
    official = db().scalar("SELECT evidence_status = 'OFFICIAL' FROM policy_event WHERE id = :id", id=res.event_id)
    if official and (res.created or res.upgraded):
        affected = events.affected_companies(res.event_id)
        if affected and db().scalar("SELECT exists(SELECT 1 FROM forecast)"):
            try:
                reissued = ForecastService().issue_live(affected, f"New evidence: event #{res.event_id} ({body.title})").ids
            except Exception as e:  # noqa: BLE001 - the event is stored either way
                log.warning("re-issuing forecasts after event %s failed: %s", res.event_id, e)
    return {"event": get_event(res.event_id), "deduplicated": res.deduplicated, "reissuedForecastIds": reissued}
