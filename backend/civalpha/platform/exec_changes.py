"""Claude reads item 5.02 of an 8-K (ADR-0011 decision 9, method exec-llm-1).

The keyword rule exec-1 was right on 16 of 40 CEO departures; the model answers four questions per section instead
(schema-constrained): did the company's own CEO change, did its CFO change, was the change already reported, how did the
person leave. The history goes as one Message Batch (half price), tracked in `llm_batch` and collected by the next
pipeline run or the job; new filings are read one by one in the pipeline. Any failure leaves the filing as it was (the
rule's rows), so ingestion never depends on the model.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..corpactions import to_text
from .company_events import EXEC_ITEM, OTHER_5_02, _TITLE, item_section
from .errors import Problem
from .jobs import Log
from .settings import settings
from .sql import Db, db, jsonb
from .storage import DocumentStore

log = logging.getLogger("civalpha.exec_changes")

METHOD = "exec-llm-1"
PURPOSE = "company_events_5_02"
MAX_CHARS = 12_000           # longer sections are pay plans; the change, when there is one, is stated first
MAX_TOKENS = 2_000
DEPARTURE_TYPES = ("RETIREMENT", "RESIGNATION", "TERMINATION", "DEATH_OR_ILLNESS", "NOT_STATED", "NONE")
SCHEMA = {
    "type": "object",
    "properties": {"ceo_change": {"type": "boolean"}, "cfo_change": {"type": "boolean"},
                   "previously_reported": {"type": "boolean"},
                   "departure_type": {"type": "string", "enum": list(DEPARTURE_TYPES)},
                   "evidence": {"type": "string"}},
    "required": ["ceo_change", "cfo_change", "previously_reported", "departure_type", "evidence"],
    "additionalProperties": False,
}
INSTRUCTIONS = (
    "You read item 5.02 of a Form 8-K that {company} filed with the SEC. Answer from the text only.\n"
    "ceo_change: true only if the company's own chief executive officer (or principal executive officer, or a co-CEO) "
    "is leaving, has left, will leave, or is replaced; the appointment of a new CEO counts, an interim one too. False for "
    "the CEO of a segment, division or subsidiary, for an executive chairman who is not the CEO, for someone who reports "
    "to or advises the CEO, for a director who used to be a CEO elsewhere, and for pay, bonus, equity or employment terms "
    "of a CEO who stays.\n"
    "cfo_change: the same for the company's own chief financial officer (or principal financial officer).\n"
    "previously_reported: true if the text says the change was already announced or reported earlier and this filing "
    "only adds details (terms, dates, compensation).\n"
    "departure_type: how the person who leaves is leaving (RETIREMENT, RESIGNATION, TERMINATION, DEATH_OR_ILLNESS), "
    "NOT_STATED if the text does not say, NONE when neither change is true.\n"
    "evidence: the sentence that shows the change, quoted, at most 250 characters; empty when neither change is true.\n\n"
    "<item_5_02>\n{section}\n</item_5_02>")


@dataclass
class Outcome:
    read: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    failed: int = 0


def section_of(body: bytes | None) -> str:
    return _TITLE.sub(" ", item_section(to_text(body)))[:MAX_CHARS].strip() if body else ""


def request_params(model: str, company: str, section: str) -> dict:
    return {"model": model, "max_tokens": MAX_TOKENS,
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": INSTRUCTIONS.format(company=company, section=section)}]}


def parse(message) -> dict | None:
    """The schema-valid answer of a response, or None (refusal, truncation, invalid JSON)."""
    if getattr(message, "stop_reason", None) in ("refusal", "max_tokens"):
        return None
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()
    try:
        a = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(a, dict) or a.get("departure_type") not in DEPARTURE_TYPES:
        return None
    return {"ceo_change": bool(a.get("ceo_change")), "cfo_change": bool(a.get("cfo_change")),
            "previously_reported": bool(a.get("previously_reported")), "departure_type": a["departure_type"],
            "evidence": str(a.get("evidence") or "")[:300]}


def kinds_of(answer: dict) -> list[str]:
    if answer["previously_reported"]:
        return [OTHER_5_02]
    k = [kind for kind, on in (("CEO_DEPARTURE", answer["ceo_change"]), ("CFO_DEPARTURE", answer["cfo_change"])) if on]
    return k or [OTHER_5_02]


class ExecChangeReader:
    def __init__(self, database: Db | None = None, docs: DocumentStore | None = None, client=None, model: str | None = None):
        self.db = database or db()
        self.docs = docs or DocumentStore(self.db)
        llm = settings().llm
        if client is None:
            if not llm.enabled:
                raise Problem("No language model is configured (CIVALPHA_LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY)")
            import anthropic
            client = anthropic.Anthropic(api_key=llm.api_key)
        self.client = client
        self.model = model or llm.model

    # ---------------------------------------------------------------- what is left to read
    def pending(self, since=None) -> list[dict]:
        """5.02 filings not read by the model and not in an uncollected batch (oldest first)."""
        return self.db.all("""
            SELECT DISTINCT ON (e.accession_no) e.accession_no, e.company_id, e.accepted_at, e.filed_date, e.source_document_id,
                   c.name AS company
            FROM company_event e JOIN company c ON c.id = e.company_id
            WHERE e.item = :item AND e.source_document_id IS NOT NULL
              AND (CAST(:since AS timestamptz) IS NULL OR e.accepted_at >= :since)
              AND NOT EXISTS (SELECT 1 FROM company_event x WHERE x.accession_no = e.accession_no AND x.method = :m)
              AND NOT EXISTS (SELECT 1 FROM llm_batch b WHERE b.collected_at IS NULL AND b.purpose = :p
                              AND jsonb_exists(b.result -> 'accessions', e.accession_no))
            ORDER BY e.accession_no, e.accepted_at""", item=EXEC_ITEM, since=since, m=METHOD, p=PURPOSE)

    def _section(self, row: dict) -> str:
        doc = self.docs.get(row["source_document_id"])
        return section_of(self.docs.content(doc) if doc is not None else None)

    # ---------------------------------------------------------------- history: one batch
    def submit(self, log_line: Log) -> str | None:
        rows = [r for r in self.pending() if r["source_document_id"]]
        requests, accessions = [], []
        for r in rows:
            sec = self._section(r)
            if not sec:
                continue
            requests.append({"custom_id": r["accession_no"], "params": request_params(self.model, r["company"], sec)})
            accessions.append(r["accession_no"])
        if not requests:
            log_line("5.02 by the model: nothing left to read")
            return None
        batch = self.client.messages.batches.create(requests=requests)
        self.db.execute("""INSERT INTO llm_batch (id, purpose, model, request_count, result)
                           VALUES (:i, :p, :m, :n, CAST(:r AS jsonb))""",
                        i=batch.id, p=PURPOSE, m=self.model, n=len(requests), r=jsonb({"accessions": accessions}))
        log_line(f"5.02 by the model: batch {batch.id} submitted with {len(requests)} sections ({self.model}, half price); "
                 "the next pipeline run or this job collects it")
        return batch.id

    def collect(self, log_line: Log) -> Outcome:
        """Stores the answers of every ended, uncollected batch."""
        out = Outcome()
        for b in self.db.all("SELECT id FROM llm_batch WHERE purpose = :p AND collected_at IS NULL ORDER BY submitted_at", p=PURPOSE):
            status = self.client.messages.batches.retrieve(b["id"])
            if status.processing_status != "ended":
                log_line(f"5.02 by the model: batch {b['id']} still {status.processing_status}")
                continue
            counts = {"succeeded": 0, "unreadable": 0, "errored": 0, "canceled": 0, "expired": 0}
            for res in self.client.messages.batches.results(b["id"]):
                kind = res.result.type
                if kind != "succeeded":
                    counts[kind] = counts.get(kind, 0) + 1
                    continue
                answer = parse(res.result.message)
                if answer is None:
                    counts["unreadable"] += 1
                    continue
                counts["succeeded"] += 1
                self._store(res.custom_id, answer, out)
            self.db.execute("UPDATE llm_batch SET collected_at = now(), result = result || CAST(:r AS jsonb) WHERE id = :i",
                            i=b["id"], r=jsonb({"counts": counts}))
            out.failed += sum(v for k, v in counts.items() if k != "succeeded")
            log_line(f"5.02 by the model: batch {b['id']} collected: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        return out

    # ---------------------------------------------------------------- new filings: one call each
    def read_new(self, log_line: Log, since) -> Outcome:
        out = Outcome()
        for r in self.pending(since=since):
            sec = self._section(r)
            if not sec:
                continue
            try:
                answer = parse(self.client.messages.create(**request_params(self.model, r["company"], sec)))
            except Exception as e:  # noqa: BLE001 - the rule's rows stay; the filing is read on a later run
                log.warning("5.02 read failed for %s: %s", r["accession_no"], e)
                answer = None
            if answer is None:
                out.failed += 1
                continue
            self._store(r["accession_no"], answer, out)
        if out.read or out.failed:
            log_line(f"5.02 by the model: {out.read} new sections read, {out.failed} left for a later run")
        return out

    def _store(self, accession: str, answer: dict, out: Outcome) -> None:
        """Replaces the filing's 5.02 rows (the rule's) with the model's."""
        base = self.db.one("""SELECT company_id, accepted_at, filed_date, source_document_id FROM company_event
                              WHERE accession_no = :a AND item = :i LIMIT 1""", a=accession, i=EXEC_ITEM)
        if base is None:
            return
        with self.db.transaction():
            self.db.execute("DELETE FROM company_event WHERE accession_no = :a AND item = :i", a=accession, i=EXEC_ITEM)
            for kind in kinds_of(answer):
                self.db.execute("""
                    INSERT INTO company_event (company_id, accession_no, item, kind, accepted_at, filed_date, method, evidence,
                                               source_document_id, detail)
                    VALUES (:c, :a, :i, :k, :at, :fd, :m, :ev, :d, CAST(:det AS jsonb))""",
                                c=base["company_id"], a=accession, i=EXEC_ITEM, k=kind, at=base["accepted_at"],
                                fd=base["filed_date"], m=METHOD, ev=answer["evidence"] or None, d=base["source_document_id"],
                                det=jsonb({**answer, "model": self.model}))
                out.kinds[kind] = out.kinds.get(kind, 0) + 1
        out.read += 1


def classify_job(params: dict, log: Log) -> None:
    """ADR-0011 decision 9: collect any ended batch, then submit what is left (nothing when all is read)."""
    r = ExecChangeReader()
    got = r.collect(log)
    if got.read:
        log("5.02 by the model: " + ", ".join(f"{k} {v}" for k, v in sorted(got.kinds.items())))
    if not r.db.scalar("SELECT exists(SELECT 1 FROM llm_batch WHERE purpose = :p AND collected_at IS NULL)", p=PURPOSE):
        r.submit(log)
