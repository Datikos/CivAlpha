"""The history of official policy events (ADR-0009), loaded once by the owner (job EVENT_BACKFILL) and safe to rerun:

1. official events stored before streams existed are classified (EventService.classify_unstreamed);
2. the Federal Register's material trade streams, year by year, through the same queries as the live run;
3. FOMC rate changes: every change of FRED's upper target (DFEDTARU) gives the statement of its decision day.

Everything goes through EventService.ingest, so dedup, evidence storage and actor records are those of live events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..jobs import Log, Progress
from ..macro import MacroService
from ..settings import settings
from ..sql import Db, db
from .live import LiveEventSources
from .model import EventDraft
from .service import EventService

DEFAULT_START = date(2019, 1, 1)
UPPER_TARGET = "DFEDTARU"


@dataclass
class Summary:
    classified: dict[str, int] = field(default_factory=dict)
    created: dict[str, int] = field(default_factory=dict)
    existing: int = 0
    failed: int = 0


def fomc_changes(observations: list[tuple[date, float | None]], start: date, end: date) -> list[tuple[date, float, float]]:
    """(effective date, new upper target, previous upper target) for each change of the series within [start, end]."""
    out = []
    prev = None
    for day, v in sorted((d, v) for d, v in observations if v is not None):
        if prev is not None and v != prev and start <= day <= end:
            out.append((day, v, prev))
        prev = v
    return out


class EventBackfill:
    def __init__(self, database: Db | None = None, sources: LiveEventSources | None = None, macro: MacroService | None = None):
        self.db = database or db()
        self.events = EventService(self.db)
        self.sources = sources or LiveEventSources()
        self.macro = macro or MacroService(self.db)

    def run(self, log: Log, start: date = DEFAULT_START, end: date | None = None) -> Summary:
        end = end or date.today()
        out = Summary()
        years = list(range(start.year, end.year + 1))
        p = Progress(log, 2 + len(years), "event streams")
        out.classified = self.events.classify_unstreamed(log)
        for y in years:
            p.step(f"Federal Register {y}")
            lo, hi = max(start, date(y, 1, 1)), min(end, date(y, 12, 31))
            drafts = self.sources.federal_register(lo, hi)
            self._ingest(drafts, out, log)
            log(f"Federal Register {y}: {len(drafts)} material documents")
        p.step("FOMC rate changes")
        if self.macro.fred is None or not self.macro.fred.enabled:
            log("FOMC history skipped: FRED_API_KEY is not set (the rate changes come from FRED's upper target, DFEDTARU)")
        else:
            # a year before `start`, so the first change in range has the range it changed from
            obs = self.macro.current_values(UPPER_TARGET, date(start.year - 1, 1, 1))
            changes = fomc_changes(obs, start, end)
            drafts = self.sources.fomc_history(changes, log)
            self._ingest(drafts, out, log)
            log(f"FOMC history: {len(changes)} rate changes since {start}, {len(drafts)} statements read")
        p.finish()
        made = ", ".join(f"{k} {v}" for k, v in sorted(out.created.items())) or "none"
        log(f"event history: new events by stream: {made}; {out.existing} already on record, {out.failed} failed")
        return out

    def _ingest(self, drafts: list[EventDraft], out: Summary, log: Log) -> None:
        for d in drafts:
            try:
                r = self.events.ingest(d)
            except Exception as e:  # noqa: BLE001 - one document must not stop the history
                out.failed += 1
                log(f"event {d.title[:80]!r} skipped ({type(e).__name__}: {str(e)[:120]})")
                continue
            if r.created:
                out.created[d.stream or "?"] = out.created.get(d.stream or "?", 0) + 1
            else:
                out.existing += 1


def run_job(params: dict, log: Log) -> Summary:
    start = date.fromisoformat(params["from"]) if params.get("from") else DEFAULT_START
    end = date.fromisoformat(params["to"]) if params.get("to") else None
    s = settings()
    if not s.events.federal_register_enabled:
        log("note: the live Federal Register step is off (EVENTS_FEDERAL_REGISTER_ENABLED); the history is loaded anyway")
    return EventBackfill().run(log, start, end)
