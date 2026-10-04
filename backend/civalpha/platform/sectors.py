"""Suggests a sector, its SPDR sector benchmark ETF and an industry key from a company's SIC code.

SEC submissions carry the Standard Industrial Classification the registrant files under. SIC predates GICS, so the
mapping is a suggestion that the Universe page shows for review: most codes map cleanly (3674 semiconductors ->
Technology), while a few are genuinely ambiguous (7370 "computer programming, data processing" covers both Microsoft
and Meta) and are flagged `review`.
"""
from __future__ import annotations

from dataclasses import dataclass

SECTOR_ETF = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}


@dataclass(frozen=True)
class Suggestion:
    sector: str
    benchmark_symbol: str
    industry: str | None
    confidence: str          # "high" | "review"
    note: str
    # for `review` codes: the sectors the code is known to cover, most likely first (the suggestion itself included)
    alternatives: tuple[str, ...] = ()


# sectors a `review` code is known to span; keyed by exact code or by the range's lower bound
_ALTERNATIVES: dict[int, tuple[str, ...]] = {
    7370: ("Technology", "Communication Services"),
    7374: ("Technology", "Communication Services", "Financials"),
    7389: ("Industrials", "Technology", "Consumer Discretionary", "Financials"),
    8731: ("Health Care", "Technology", "Industrials"),
    3651: ("Consumer Discretionary", "Technology"),
    3669: ("Technology", "Industrials"),
    5399: ("Consumer Staples", "Consumer Discretionary"),
    3650: ("Consumer Discretionary", "Technology"),
    3700: ("Consumer Discretionary", "Industrials"),
    3800: ("Technology", "Health Care", "Industrials"),
    3860: ("Technology", "Health Care", "Consumer Discretionary"),
    5000: ("Industrials", "Technology", "Health Care", "Consumer Discretionary"),
    5100: ("Consumer Discretionary", "Consumer Staples", "Health Care"),
    7300: ("Industrials", "Technology", "Communication Services"),
    8100: ("Industrials", "Health Care", "Consumer Discretionary"),
}


# exact SIC codes first (most specific), then ranges [lo, hi] inclusive; first match wins
_EXACT: dict[int, tuple[str, str | None, str]] = {
    3674: ("Technology", "SEMICONDUCTORS", "high"),
    3559: ("Technology", "SEMICONDUCTOR_EQUIPMENT", "high"),
    3571: ("Technology", "CONSUMER_ELECTRONICS", "high"),
    3572: ("Technology", None, "high"),
    3576: ("Technology", "NETWORKING_HARDWARE", "high"),
    3577: ("Technology", None, "high"),
    3651: ("Consumer Discretionary", "CONSUMER_ELECTRONICS", "review"),
    3661: ("Technology", "NETWORKING_HARDWARE", "high"),
    3663: ("Technology", "NETWORKING_HARDWARE", "high"),
    3669: ("Technology", "NETWORKING_HARDWARE", "review"),
    3711: ("Consumer Discretionary", "AUTOS", "high"),
    3713: ("Consumer Discretionary", "AUTOS", "high"),
    3714: ("Consumer Discretionary", "AUTOS", "high"),
    3715: ("Consumer Discretionary", "AUTOS", "high"),
    3716: ("Consumer Discretionary", "AUTOS", "high"),
    3751: ("Consumer Discretionary", None, "high"),
    2834: ("Health Care", "PHARMACEUTICALS", "high"),
    2835: ("Health Care", "DIAGNOSTICS", "high"),
    2836: ("Health Care", "BIOTECH", "high"),
    8731: ("Health Care", "BIOTECH", "review"),
    8071: ("Health Care", "DIAGNOSTICS", "high"),
    5122: ("Health Care", None, "high"),
    5331: ("Consumer Staples", "RETAIL", "high"),
    5399: ("Consumer Staples", "RETAIL", "review"),
    5912: ("Consumer Staples", "RETAIL", "high"),
    5961: ("Consumer Discretionary", "ECOMMERCE", "high"),
    5812: ("Consumer Discretionary", "RESTAURANTS", "high"),
    7370: ("Technology", "SOFTWARE", "review"),
    7371: ("Technology", "SOFTWARE", "high"),
    7372: ("Technology", "SOFTWARE", "high"),
    7373: ("Technology", "SOFTWARE", "high"),
    7374: ("Technology", "SOFTWARE", "review"),
    7389: ("Industrials", None, "review"),
    7841: ("Communication Services", "STREAMING", "high"),
    4813: ("Communication Services", None, "high"),
    4899: ("Communication Services", None, "high"),
    6770: ("Financials", None, "high"),
    6798: ("Real Estate", None, "high"),
}

_RANGES: list[tuple[int, int, str, str | None, str]] = [
    (100, 999, "Consumer Staples", None, "high"),              # agriculture
    (1000, 1099, "Materials", None, "high"),                   # metal mining
    (1200, 1399, "Energy", None, "high"),                      # coal, oil & gas extraction
    (1400, 1499, "Materials", None, "high"),
    (1500, 1799, "Industrials", None, "high"),                 # construction
    (2000, 2199, "Consumer Staples", "FOOD_BEVERAGE", "high"), # food, beverages, tobacco
    (2200, 2399, "Consumer Discretionary", None, "high"),      # textiles, apparel
    (2400, 2499, "Materials", None, "high"),
    (2510, 2599, "Consumer Discretionary", None, "high"),      # furniture
    (2600, 2699, "Materials", None, "high"),                   # paper
    (2700, 2799, "Communication Services", None, "high"),      # publishing
    (2830, 2839, "Health Care", None, "high"),                 # drugs
    (2840, 2849, "Consumer Staples", None, "high"),            # soap, cosmetics
    (2800, 2899, "Materials", None, "high"),                   # chemicals
    (2900, 2999, "Energy", None, "high"),                      # petroleum refining
    (3000, 3099, "Materials", None, "high"),                   # rubber, plastics
    (3100, 3199, "Consumer Discretionary", None, "high"),      # leather
    (3200, 3399, "Materials", None, "high"),                   # glass, cement, metals
    (3400, 3499, "Industrials", None, "high"),                 # fabricated metal
    (3570, 3579, "Technology", None, "high"),                  # computers
    (3500, 3599, "Industrials", None, "high"),                 # machinery
    (3600, 3629, "Industrials", None, "high"),                 # electrical equipment
    (3630, 3639, "Consumer Discretionary", None, "high"),      # household appliances
    (3640, 3649, "Industrials", None, "high"),
    (3650, 3659, "Consumer Discretionary", None, "review"),
    (3660, 3699, "Technology", None, "high"),                  # communications equipment, electronic components
    (3720, 3769, "Industrials", None, "high"),                 # aircraft, ships, defence
    (3700, 3799, "Consumer Discretionary", None, "review"),
    (3812, 3812, "Industrials", None, "high"),
    (3826, 3826, "Health Care", None, "high"),                 # laboratory analytical instruments
    (3840, 3859, "Health Care", None, "high"),                 # medical, surgical, dental, ophthalmic
    (3800, 3839, "Technology", None, "review"),                # measuring & controlling instruments
    (3860, 3879, "Technology", None, "review"),
    (3900, 3999, "Consumer Discretionary", None, "high"),      # toys, sporting goods, jewellery
    (4000, 4799, "Industrials", None, "high"),                 # transportation
    (4810, 4899, "Communication Services", None, "high"),      # telephone, broadcasting, cable
    (4950, 4959, "Industrials", None, "high"),                 # waste management
    (4900, 4999, "Utilities", None, "high"),
    (5140, 5149, "Consumer Staples", None, "high"),            # groceries wholesale
    (5000, 5099, "Industrials", None, "review"),               # durable goods wholesale
    (5100, 5199, "Consumer Discretionary", None, "review"),
    (5400, 5499, "Consumer Staples", "RETAIL", "high"),        # food stores
    (5200, 5999, "Consumer Discretionary", "RETAIL", "high"),  # retail
    (6500, 6599, "Real Estate", None, "high"),
    (6000, 6799, "Financials", None, "high"),
    (7000, 7099, "Consumer Discretionary", None, "high"),      # hotels
    (7200, 7299, "Consumer Discretionary", None, "high"),
    (7310, 7319, "Communication Services", None, "high"),      # advertising
    (7370, 7379, "Technology", "SOFTWARE", "review"),
    (7300, 7399, "Industrials", None, "review"),               # business services
    (7500, 7599, "Consumer Discretionary", None, "high"),
    (7800, 7999, "Communication Services", None, "high"),      # motion pictures, entertainment
    (8000, 8099, "Health Care", None, "high"),                 # health services
    (8200, 8299, "Consumer Discretionary", None, "high"),      # education
    (8700, 8749, "Industrials", None, "high"),                 # engineering, accounting, management
    (8100, 8999, "Industrials", None, "review"),
]


def suggest(sic: str | int | None, description: str | None = None) -> Suggestion | None:
    """Sector / benchmark / industry suggested by a SIC code; None when the code is missing or unmapped."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    hit = _EXACT.get(code)
    key = code
    if hit is None:
        for lo, hi, sector, industry, confidence in _RANGES:
            if lo <= code <= hi:
                hit, key = (sector, industry, confidence), lo
                break
    if hit is None:
        return None
    sector, industry, confidence = hit
    label = f"SIC {code}" + (f" {description.strip()}" if description and description.strip() else "")
    note = f"{label} → {sector} ({SECTOR_ETF[sector]})"
    alternatives: tuple[str, ...] = ()
    if confidence == "review":
        alternatives = _ALTERNATIVES.get(key) or _ALTERNATIVES.get(code) or (sector,)
        if sector not in alternatives:
            alternatives = (sector, *alternatives)
        note += "; companies filing under this code sit in several sectors, pick the right one"
    return Suggestion(sector, SECTOR_ETF[sector], industry, confidence, note, alternatives)
