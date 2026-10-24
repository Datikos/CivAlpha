"""Streams of official policy events (ADR-0009): which statutory instrument a document belongs to, how severe a
trade action of that stream counts, and which streams the models read (`pit.MODEL_STREAMS`).

A Federal Register document is classified from its own metadata (type, agencies, title, abstract); the first rule that
applies wins. The rules and severities were fixed in ADR-0009 before any measurement.
"""
from __future__ import annotations

import re

from ...pit import MODEL_STREAMS

STREAMS = ("PRESIDENTIAL", "USTR_301", "SECTION_232", "EXPORT_CONTROL", "FOMC", "MANUAL", "AD_CVD", "OTHER_TRADE")
SEVERITY = {"PRESIDENTIAL": 1.0, "SECTION_232": 0.8, "USTR_301": 0.8, "EXPORT_CONTROL": 0.6}
DEFAULT_SEVERITY = 0.5            # trade documents of the other streams (stored, not read by the models)

USTR = "trade-representative-office-of-united-states"
BIS = "industry-and-security-bureau"
ITA = "international-trade-administration"
ITC = "international-trade-commission"

_FLAGS = re.IGNORECASE | re.ASCII
_301 = re.compile(r"section\s+301", _FLAGS)
_232 = re.compile(r"section\s+232", _FLAGS)
_EXPORT = re.compile(r"entity\s+list|export\s+administration\s+regulations", _FLAGS)
_CASE = re.compile(r"antidumping|anti-dumping|countervailing|five-year review|sunset review", _FLAGS)


def agency_slugs(doc: dict) -> set[str]:
    """Agency slugs of a Federal Register document record (`agencies` objects carry `slug`; older records only `name`)."""
    out = set()
    for a in doc.get("agencies") or []:
        if isinstance(a, dict):
            slug = a.get("slug") or re.sub(r"[^a-z0-9]+", "-", str(a.get("name") or "").lower()).strip("-")
            if slug:
                out.add(slug)
    return out


def classify(doc: dict) -> str:
    """The stream of a Federal Register document record (as the documents API returns it)."""
    text = f"{doc.get('title') or ''} {doc.get('abstract') or ''}"
    kind = str(doc.get("type") or "")
    agencies = agency_slugs(doc)
    if kind in ("Presidential Document", "PRESDOCU"):
        return "PRESIDENTIAL"
    if USTR in agencies and _301.search(text):
        return "USTR_301"
    if _232.search(text):
        return "SECTION_232"
    if BIS in agencies and kind in ("Rule", "RULE") and _EXPORT.search(text):
        return "EXPORT_CONTROL"
    if agencies & {ITA, ITC} and _CASE.search(text):
        return "AD_CVD"
    return "OTHER_TRADE"


def severity(stream: str) -> float:
    return SEVERITY.get(stream, DEFAULT_SEVERITY)


def modelled(stream: str | None) -> bool:
    return stream in MODEL_STREAMS
