"""Records policy events with their evidence.

Every event keeps a link to at least one stored source document. Reports of an event already on record are attached
to it instead of creating a duplicate; an official document arriving for a news-only event upgrades its evidence
status (new event version).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..errors import BadRequest
from ..sql import Db, db, jsonb
from ..storage import DocumentStore, NewDocument, SourceDocument
from . import dedup
from .model import EventDraft, Target


@dataclass(frozen=True)
class IngestResult:
    event_id: int
    deduplicated: bool
    upgraded: bool
    created: bool


def now() -> datetime:
    return datetime.now(timezone.utc)


def _split(s: str | None, sep: str) -> frozenset[str]:
    if s is None:
        return frozenset()
    parts = s.split(sep)
    while len(parts) > 1 and parts[-1] == "":   # String.split drops trailing empty strings
        parts.pop()
    return frozenset(parts)


def _java_str(o: Any) -> str:
    """String concatenation of a JSON value as Java renders it."""
    if o is None:
        return "null"
    if isinstance(o, bool):
        return "true" if o else "false"
    return str(o)


def _votes(attrs: dict | None) -> str:
    if attrs is None or "votes_for" not in attrs:
        return ""
    return f" (recorded vote: {_java_str(attrs.get('votes_for'))} for, {_java_str(attrs.get('votes_against', 0))} against)"


class EventService:
    def __init__(self, database: Db | None = None, documents: DocumentStore | None = None):
        self.db = database or db()
        self.docs = documents or DocumentStore(self.db)

    def ingest(self, d: EventDraft, demo: bool = False) -> IngestResult:
        if d.source is None or d.source.url is None:
            raise BadRequest("an event needs a source URL (original evidence)")
        with self.db.transaction():
            s = d.source
            doc = self.docs.store(NewDocument("OFFICIAL_EVENT" if s.official else "NEWS", s.publisher, s.url, None, s.title,
                                              s.published_at, s.content_type, s.content, demo or s.demo))
            dup = dedup.find_duplicate(d, self._nearby(d.category, d.event_date))
            if dup is not None:
                self._link_source(dup, doc.id, s.role)
                upgraded = False
                status = self.db.scalar("SELECT evidence_status FROM policy_event WHERE id = :id", id=dup)
                if s.official and status == "NEWS_ONLY":
                    self.db.execute("""
                        UPDATE policy_event SET evidence_status = 'OFFICIAL', published_at = :p, version = version + 1,
                               attributes = attributes || CAST(:a AS jsonb) WHERE id = :id""",
                                    p=d.published_at, a=jsonb(d.attributes or {}), id=dup)
                    self._insert_targets(dup, d.targets)
                    self._record_actor(dup, d, doc, demo)
                    upgraded = True
                return IngestResult(dup, True, upgraded, False)
            actor = None if d.actor_name is None else self.actor_id(d.actor_name)
            event_id = self.db.scalar("""
                INSERT INTO policy_event (category, event_type, title, summary, actor_id, event_date, published_at, evidence_status,
                                          attributes, dedup_key, is_demo)
                VALUES (:c, :t, :title, :s, :actor, :d, :p, :status, CAST(:a AS jsonb), :key, :demo) RETURNING id""",
                c=d.category, t=d.event_type, title=d.title, s=d.summary, actor=actor, d=d.event_date, p=d.published_at,
                status="OFFICIAL" if s.official else "NEWS_ONLY", a=jsonb(d.attributes or {}),
                key=f"{d.category}|{d.event_date.isoformat()}|{' '.join(dedup.tokens_java_order(d.title))}", demo=demo)
            self._insert_targets(event_id, d.targets)
            self._link_source(event_id, doc.id, s.role)
            if s.official:
                self._record_actor(event_id, d, doc, demo)
            return IngestResult(event_id, False, False, True)

    def _nearby(self, category: str, day: date) -> list[dedup.Existing]:
        rows = self.db.all("""
            SELECT e.id, e.category, e.event_type, e.title, e.event_date,
                   (SELECT string_agg(t.target_type || ':' || t.target_code, ',') FROM event_target t WHERE t.event_id = e.id) AS targets,
                   (SELECT string_agg(sd.url, ' ') FROM event_source es JOIN source_document sd ON sd.id = es.source_document_id
                     WHERE es.event_id = e.id) AS urls
            FROM policy_event e WHERE e.category = :c AND e.event_date BETWEEN :from AND :to""",
                           c=category, **{"from": day - timedelta(days=7), "to": day + timedelta(days=7)})
        return [dedup.Existing(r["id"], r["category"], r["event_type"], r["title"], r["event_date"],
                               _split(r["targets"], ","), _split(r["urls"], " ")) for r in rows]

    def _insert_targets(self, event_id: int, targets: list[Target] | None) -> None:
        if targets is None:
            return
        for t in targets:
            exists = self.db.scalar("SELECT count(*) FROM event_target WHERE event_id = :e AND target_type = :t AND target_code = :c",
                                    e=event_id, t=t.target_type, c=t.target_code)
            if exists == 0:
                self.db.execute("INSERT INTO event_target (event_id, target_type, target_code, magnitude) VALUES (:e, :t, :c, :m)",
                                e=event_id, t=t.target_type, c=t.target_code,
                                m=None if t.magnitude is None else float(t.magnitude))

    def _link_source(self, event_id: int, doc_id: int, role: str | None) -> None:
        self.db.execute("INSERT INTO event_source (event_id, source_document_id, role) VALUES (:e, :d, :r) ON CONFLICT DO NOTHING",
                        e=event_id, d=doc_id, r=role)

    def _record_actor(self, event_id: int, d: EventDraft, doc: SourceDocument, demo: bool) -> None:
        """Public decision profile: documented actions only (the official document itself), never inferred traits."""
        if d.actor_name is None:
            return
        actor = self.actor_id(d.actor_name)
        self.db.execute("UPDATE policy_event SET actor_id = :a WHERE id = :e AND actor_id IS NULL", a=actor, e=event_id)
        self.db.execute("""
            INSERT INTO actor_record (actor_id, record_type, occurred_at, summary, source_document_id, is_demo)
            VALUES (:a, :t, :at, :s, :d, :demo)""",
                        a=actor, t="VOTE" if d.category == "MONETARY_POLICY" else "ACTION", at=d.published_at,
                        s=d.title + _votes(d.attributes), d=doc.id, demo=demo)

    def actor_id(self, name: str) -> int:
        existing = self.db.scalar("SELECT id FROM policy_actor WHERE name = :n", n=name)
        if existing is not None:
            return existing
        return self.db.scalar("INSERT INTO policy_actor (name, actor_type) VALUES (:n, 'INSTITUTION') RETURNING id", n=name)

    def upsert_actor(self, name: str, actor_type: str | None, authority: str | None, affiliation: str | None,
                     note: str | None) -> None:
        actor = self.actor_id(name)
        self.db.execute("UPDATE policy_actor SET actor_type = :t, authority = :a, affiliation = :f, profile_note = :n WHERE id = :id",
                        t=actor_type, a=authority, f=affiliation, n=note, id=actor)

    def affected_companies(self, event_id: int) -> list[int]:
        """Companies whose current exposures match the event's targets."""
        return self.db.scalars("""
            SELECT DISTINCT x.company_id FROM event_target t
            JOIN company_exposure x ON x.target_type = t.target_type AND x.target_code = t.target_code
            WHERE t.event_id = :e ORDER BY 1""", e=event_id)
