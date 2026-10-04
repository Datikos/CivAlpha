"""Country/product vocabulary shared by exposure extraction and event target extraction.

Patterns are ASCII case-insensitive with ASCII \\b, like java.util.regex with CASE_INSENSITIVE.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_FLAGS = re.IGNORECASE | re.ASCII

COUNTRIES: dict[str, re.Pattern] = {code: re.compile(rx, _FLAGS) for code, rx in (
    ("CN", r"china|chinese|people's republic of china|prc\b"),
    ("TW", r"taiwan"),
    ("HK", r"hong kong"),
    ("MX", r"mexic"),
    ("CA", r"canad"),
    ("JP", r"japan"),
    ("KR", r"south korea|korea\b|korean"),
    ("VN", r"vietnam|viet nam"),
    ("IN", r"\bindia\b|indian"),
    ("MY", r"malaysia"),
    ("SG", r"singapore"),
    ("IE", r"ireland|irish"),
    ("DE", r"germany|german\b"),
    ("EU", r"european union|\beurope\b|european"),
    ("PR", r"puerto rico"),
    ("US", r"united states|\bu\.s\.(?! dollar)"),
)}

PRODUCTS: dict[str, re.Pattern] = {code: re.compile(rx, _FLAGS) for code, rx in (
    ("SEMICONDUCTORS", r"semiconductor|\bchips?\b|integrated circuit|foundr"),
    ("STEEL", r"steel|aluminum|aluminium"),
    ("AUTOS", r"automotive|electric vehicle|\bvehicles?\b|auto parts"),
    ("CONSUMER_ELECTRONICS", r"smartphone|consumer electronic|personal computer|laptop|electronics"),
)}

_INDUSTRY_PRODUCT = {
    "SEMICONDUCTORS": "SEMICONDUCTORS", "SEMICONDUCTOR_EQUIPMENT": "SEMICONDUCTORS",
    "CONSUMER_ELECTRONICS": "CONSUMER_ELECTRONICS", "NETWORKING_HARDWARE": "CONSUMER_ELECTRONICS",
    "AUTOS": "AUTOS",
}


def product_for_industry(industry: str | None) -> str | None:
    """Industry key (universe config) -> product key used by PRODUCT event targets."""
    return None if industry is None else _INDUSTRY_PRODUCT.get(industry)


@dataclass(frozen=True)
class GeoMatch:
    code: str
    exact: bool


def geo_member(member: str | None) -> GeoMatch | None:
    """Maps an XBRL geographic member to a country/region code. Standard members look like "country:CN";
    company-specific members are matched by name (e.g. aapl:GreaterChinaMember -> CN, flagged approximate)."""
    if member is None:
        return None
    if member.startswith("country:"):
        return GeoMatch(member[len("country:"):].upper(), True)
    local = member[member.index(":") + 1:] if ":" in member else member
    m = re.sub(r"([a-z])([A-Z])", r"\1 \2", local.replace("Member", "")).lower()
    if "rest of" in m or "other" in m or "americas" in m or "international" in m:
        return None
    for code, p in COUNTRIES.items():
        if p.search(m):
            return GeoMatch(code, False)
    return None
