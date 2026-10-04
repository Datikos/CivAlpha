"""Policy events (trade/tariff and monetary policy): drafts, deterministic text signals, deduplication, ingestion
with evidence, and the optional live feeds (Federal Register, Federal Reserve RSS, news RSS)."""
from .dedup import Existing, find_duplicate, similarity
from .live import LiveEventSources
from .model import EventDraft, Source, Target
from .service import EventService, IngestResult
from .signals import RateDecision, rate_decision, trade_event_type, trade_targets

__all__ = ["EventDraft", "Target", "Source", "EventService", "IngestResult", "LiveEventSources", "Existing", "find_duplicate",
           "similarity", "RateDecision", "rate_decision", "trade_event_type", "trade_targets"]
