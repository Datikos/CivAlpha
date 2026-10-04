"""Decides whether a draft describes an event already on record (e.g. a news wire re-reporting an official notice).

Deterministic rules, in order:
  1. a shared source URL;
  2. monetary policy: same category, same event type and same decision date (one decision per meeting);
  3. within +/-3 days and same category: title similarity >= 0.5, or similarity >= 0.2 with
     overlapping targets, or (draft without targets) similarity >= 0.3 within +/-2 days.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .model import EventDraft


@dataclass(frozen=True)
class Existing:
    id: int
    category: str
    event_type: str
    title: str
    event_date: date
    target_keys: frozenset[str] = field(default_factory=frozenset)
    source_urls: frozenset[str] = field(default_factory=frozenset)


STOP = frozenset({"the", "a", "an", "of", "on", "and", "to", "for", "in", "new", "from", "with",
                  "by", "report", "wire", "announced", "announces", "says", "demo", "is", "are", "at", "its", "as", "notice", "statement"})


def find_duplicate(d: EventDraft, existing: list[Existing]) -> int | None:
    d_targets = set() if d.targets is None else {f"{t.target_type}:{t.target_code}" for t in d.targets}
    best: int | None = None
    best_score = 0.0
    for e in existing:
        if d.source is not None and d.source.url is not None and d.source.url in e.source_urls:
            return e.id
        if e.category != d.category:
            continue
        days = abs((d.event_date - e.event_date).days)
        if d.category == "MONETARY_POLICY" and days == 0 and e.event_type == d.event_type:
            return e.id
        if days > 3:
            continue
        sim = similarity(d.title, e.title)
        match = (sim >= 0.5
                 or (sim >= 0.2 and bool(d_targets) and jaccard(d_targets, set(e.target_keys)) >= 0.5)
                 or (sim >= 0.3 and not d_targets and days <= 2))
        if match and sim > best_score:
            best = e.id
            best_score = sim
    return best


def similarity(a: str | None, b: str | None) -> float:
    return jaccard(tokens(a), tokens(b))


def _token_stream(s: str | None) -> list[str]:
    if s is None:
        return []
    return [_stem(t) for t in re.split(r"\s+", re.sub(r"[^a-z0-9 ]", " ", s.lower())) if len(t) > 1 and t not in STOP]


def tokens(s: str | None) -> set[str]:
    return set(_token_stream(s))


def tokens_java_order(s: str | None) -> list[str]:
    """The tokens in the iteration order of the Java HashSet the original code collected them into, so the stored
    dedup_key ("category|date|tokens") is the same string the Java backend wrote."""
    return _java_hash_set_order(_token_stream(s))


def _stem(t: str) -> str:
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("es") and len(t) > 4:
        return t[:-2]
    if t.endswith("s") and len(t) > 3:
        return t[:-1]
    return t


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _java_string_hash(s: str) -> int:
    h = 0
    for c in s:   # tokens are ASCII [a-z0-9], so characters are UTF-16 code units
        h = (31 * h + ord(c)) & 0xFFFFFFFF
    return h


def _java_hash_set_order(items: list[str]) -> list[str]:
    """java.util.HashSet iteration order (default capacity 16, load factor 0.75): by bucket of the final table,
    insertion order within a bucket (resizes keep the relative order)."""
    unique: list[str] = []
    seen: set[str] = set()
    for t in items:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    cap = 16
    while len(unique) > cap * 3 // 4:
        cap *= 2

    def bucket(t: str) -> int:
        h = _java_string_hash(t)
        return (h ^ (h >> 16)) & (cap - 1)

    return [t for _, _, t in sorted((bucket(t), i, t) for i, t in enumerate(unique))]
