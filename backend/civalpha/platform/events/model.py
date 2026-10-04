"""A candidate policy event with exactly one piece of evidence (its source)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class Target:
    target_type: str
    target_code: str
    magnitude: float | None = None


@dataclass(frozen=True)
class Source:
    """role: OFFICIAL_PRIMARY | OFFICIAL_SUPPORTING | NEWS_DISCOVERY. content may be None (metadata only)."""
    url: str | None
    title: str | None
    publisher: str | None
    role: str | None
    published_at: datetime | None = None
    content: bytes | None = None
    content_type: str | None = None
    demo: bool = False

    @property
    def official(self) -> bool:
        return self.role is not None and self.role.startswith("OFFICIAL")


@dataclass(frozen=True)
class EventDraft:
    category: str
    event_type: str
    title: str
    summary: str | None
    event_date: date
    published_at: datetime | None
    actor_name: str | None
    attributes: dict[str, Any] | None
    targets: list[Target] | None
    source: Source | None

    Target = Target
    Source = Source
