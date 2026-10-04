"""Deterministic extraction of event attributes from official text (no LLM required)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .model import Target
from .vocabulary import COUNTRIES, PRODUCTS

# ASCII \w, \d, \s, \b and ASCII-only case folding, as in java.util.regex with CASE_INSENSITIVE
_RANGE = re.compile(
    r"(raise|lower|maintain|increase|decrease|reduce|cut)\w*\s+the\s+target\s+range\s+for\s+the\s+federal\s+funds\s+rate"
    r"(?:\s+by\s+(\d+(?:/\d+)?)\s+(?:basis\s+points?|percentage\s+points?))?"
    r"[^.]*?\bto\s+(\d+(?:[-‐-—\s]\d/\d)?(?:\.\d+)?)\s+to\s+(\d+(?:[-‐-—\s]\d/\d)?(?:\.\d+)?)\s+percent",
    re.IGNORECASE | re.ASCII)
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE | re.ASCII)


@dataclass(frozen=True)
class RateDecision:
    change_bps: int
    lower: float
    upper: float


def _java_round(v: float) -> int:
    """Math.round: half up (toward positive infinity), not banker's rounding."""
    return math.floor(v + 0.5)


def rate_decision(text: str) -> RateDecision | None:
    """Parses an FOMC statement sentence like "decided to lower the target range ... by 1/4 percentage point to 4 to 4-1/4 percent"."""
    m = _RANGE.search(text.replace("\n", " "))
    if m is None:
        return None
    verb = m.group(1).lower()
    lo, hi = fraction(m.group(3)), fraction(m.group(4))
    bps = 0
    if m.group(2) is not None:
        v = fraction(m.group(2))
        bps = _java_round(v * 100 if "percentage point" in m.group(0).lower() else v)
    if verb.startswith(("lower", "decrease", "reduce", "cut")):
        bps = -abs(bps)
    if verb.startswith("maintain"):
        bps = 0
    return RateDecision(bps, lo, hi)


def fraction(s: str) -> float:
    s = re.sub(r"[‐-—]", "-", s.strip())
    if "/" in s:
        v = 0.0
        for part in re.split(r"[- ]", s):
            if "/" in part:
                num, den = part.split("/")[:2]
                v += float(num) / float(den)
            elif part.strip():
                v += float(part)
        return v
    return float(s)


def trade_targets(text: str) -> list[Target]:
    """Country and product targets mentioned in trade text, with the largest percentage as magnitude."""
    magnitude: float | None = None
    for pm in _PERCENT.finditer(text):
        v = float(pm.group(1))
        if v <= 200 and (magnitude is None or v > magnitude):
            magnitude = v
    out: list[Target] = []
    for code, p in COUNTRIES.items():
        if code != "US" and p.search(text):
            out.append(Target("COUNTRY", code, magnitude))
    for code, p in PRODUCTS.items():
        if p.search(text):
            out.append(Target("PRODUCT", code, magnitude))
    return out


def trade_event_type(text: str) -> str:
    t = text.lower()
    if "export control" in t or "entity list" in t:
        return "EXPORT_CONTROL"
    if any(k in t for k in ("exclusion", "suspend", "reduc", "pause", "remov")):
        return "TARIFF_REDUCED"
    if any(k in t for k in ("request for comment", "proposed", "hearing")):
        return "TARIFF_PROPOSED"
    return "TARIFF_IMPOSED"
