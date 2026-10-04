"""Dividend profile: does a company pay, how often, how much, for how long, and how much of its earnings.

Computed from the recorded cash dividends (corporate_action, from the price provider) as of a date, so it only uses
ex-dates on or before that date. Amounts are expressed per share as of that date: a dividend paid before a SPLIT
(raw-price providers) is divided by the later split ratios, the same convention as returns.total_return_index.
SPLIT_INFO rows mean the provider already adjusted its history, so they are ignored here.

Classification:
  - a payment more than SPECIAL_MULTIPLE times the previous ones that the next payment does not sustain is a special
    dividend; it counts in trailing totals but not in the frequency, the indicated (annualized) dividend or the streaks
  - frequency from the median gap between regular ex-dates in the last FREQUENCY_LOOKBACK_DAYS
  - status: REGULAR while the next payment is not overdue, SUSPENDED once it is, IRREGULAR without a steady schedule,
    NONE when no dividend is recorded

Payout ratio and buybacks come from the latest 12-month period in the cash-flow statement as filed with the SEC
(PaymentsOfDividends* and PaymentsForRepurchaseOfCommonStock against NetIncomeLoss): usually the fiscal year of a
10-K, or trailing twelve months when a company also reports them in its 10-Q (Amazon does).
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, timedelta
from statistics import median

FREQUENCY_LOOKBACK_DAYS = 2 * 365 + 30
SPECIAL_MULTIPLE = 2.5
TOLERANCE = 1e-6

# (upper bound of the median gap in days, label, payments per year)
FREQUENCIES = [(45, "MONTHLY", 12), (120, "QUARTERLY", 4), (240, "SEMIANNUAL", 2), (400, "ANNUAL", 1)]
# in order of preference; Qualcomm files PaymentsOfOrdinaryDividends
DIVIDEND_CONCEPTS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "PaymentsOfOrdinaryDividends"]
BUYBACK_CONCEPT = "PaymentsForRepurchaseOfCommonStock"
# net income; ProfitLoss (including noncontrolling interests) when NetIncomeLoss is not filed (Broadcom from FY2025)
INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]


def _split_factor_after(splits: list[tuple[date, float]], after: date, upto: date) -> float:
    f = 1.0
    for d, ratio in splits:
        if after < d <= upto:
            f *= ratio
    return f


def _frequency(gap_days: float | None) -> tuple[str, int | None]:
    if gap_days is None:
        return "IRREGULAR", None
    for limit, label, per_year in FREQUENCIES:
        if gap_days <= limit:
            return label, per_year
    return "IRREGULAR", None


def _mark_specials(divs: list[dict]) -> None:
    """Flags outsized one-off payments: more than SPECIAL_MULTIPLE times the previous few payments and not sustained by
    the next one (a raise is). The latest payment has no successor yet, so it is special only when it also comes
    off-schedule, well before the usual gap."""
    for i, d in enumerate(divs):
        prev = [p["amount"] for p in divs[max(0, i - 4):i] if not p["special"]]
        d["special"] = False
        if len(prev) < 2 or d["amount"] <= SPECIAL_MULTIPLE * median(prev):
            continue
        if i + 1 < len(divs):
            d["special"] = d["amount"] > SPECIAL_MULTIPLE * divs[i + 1]["amount"]
        else:
            gaps = [(b["exDate"] - a["exDate"]).days for a, b in zip(divs[max(0, i - 4):i - 1], divs[max(1, i - 3):i])]
            d["special"] = bool(gaps) and (d["exDate"] - divs[i - 1]["exDate"]).days < 0.75 * median(gaps)


def _years_paid(years: set[int], as_of: date) -> int:
    """Consecutive calendar years with a dividend, counting back from this year (or last year if none yet this year)."""
    y = as_of.year if as_of.year in years else as_of.year - 1
    n = 0
    while y in years:
        n, y = n + 1, y - 1
    return n


def _years_raised(regular: list[dict], as_of: date) -> int:
    """Consecutive completed calendar years whose last regular payment was higher than the previous year's last."""
    last_of_year: dict[int, float] = {}
    for d in regular:
        last_of_year[d["exDate"].year] = d["amount"]   # sorted by date, so the last one wins
    y, n = as_of.year - 1, 0
    while y in last_of_year and y - 1 in last_of_year and last_of_year[y] > last_of_year[y - 1] * (1 + TOLERANCE):
        n, y = n + 1, y - 1
    return n


def dividend_profile(actions: Iterable[tuple[date, str, float]], as_of: date, close: float | None) -> dict:
    """`actions`: (ex_date, action_type, value) rows of one company; `close`: its price per share at `as_of`."""
    rows = [(d, t, float(v)) for d, t, v in actions if d <= as_of]
    splits = sorted((d, v) for d, t, v in rows if t == "SPLIT" and v > 0)
    divs = [{"exDate": d, "amount": v / _split_factor_after(splits, d, as_of)}
            for d, t, v in sorted(rows) if t == "CASH_DIVIDEND" and v > 0]
    out = {"asOf": as_of, "status": "NONE", "frequency": None, "paymentsPerYear": None, "lastExDate": None, "lastAmount": None,
           "nextExpected": None, "ttmDividends": 0.0, "ttmPayments": 0, "ttmSpecial": 0.0, "trailingYield": None,
           "indicatedAnnual": None, "indicatedYield": None, "yearsPaid": 0, "yearsRaised": 0,
           "firstExDate": None, "annual": [], "payments": []}
    if not divs:
        return out

    _mark_specials(divs)
    regular_recent = [d for d in divs if not d["special"] and d["exDate"] > as_of - timedelta(days=FREQUENCY_LOOKBACK_DAYS)]
    gaps = [(b["exDate"] - a["exDate"]).days for a, b in zip(regular_recent, regular_recent[1:])]
    gap = median(gaps) if gaps else None
    frequency, per_year = _frequency(gap)

    last = divs[-1]
    last_regular = regular_recent[-1] if regular_recent else None
    out.update(firstExDate=divs[0]["exDate"], lastExDate=last["exDate"], lastAmount=last["amount"])
    year_start = as_of - timedelta(days=365)
    ttm = [d for d in divs if d["exDate"] > year_start]

    if per_year is not None and last_regular is not None:
        overdue = last_regular["exDate"] + timedelta(days=max(1.5 * gap, gap + 45))
        if as_of > overdue:
            out["status"] = "SUSPENDED"
        else:
            out["status"] = "REGULAR"
            out.update(frequency=frequency, paymentsPerYear=per_year, indicatedAnnual=last_regular["amount"] * per_year,
                       nextExpected=last_regular["exDate"] + timedelta(days=round(gap)))
            # a schedule drifting by a few days can put five quarterly ex-dates in 365 days: count one year of payments
            ttm = [d for d in ttm if d["special"]] + [d for d in regular_recent[-per_year:] if d["exDate"] > year_start]
    else:
        out["status"] = "IRREGULAR" if ttm else "SUSPENDED"
    out["ttmDividends"] = sum(d["amount"] for d in ttm)
    out["ttmSpecial"] = sum(d["amount"] for d in ttm if d["special"])
    out["ttmPayments"] = len(ttm)
    if close and close > 0:
        out["trailingYield"] = out["ttmDividends"] / close
        if out["indicatedAnnual"] is not None:
            out["indicatedYield"] = out["indicatedAnnual"] / close

    years = sorted({d["exDate"].year for d in divs})
    out["yearsPaid"] = _years_paid(set(years), as_of)
    out["yearsRaised"] = _years_raised([d for d in divs if not d["special"]], as_of)
    out["annual"] = [{"year": y, "total": sum(d["amount"] for d in divs if d["exDate"].year == y),
                      "payments": sum(1 for d in divs if d["exDate"].year == y)} for y in years]
    out["payments"] = list(reversed(divs))
    return out


def payout_from_facts(facts: Iterable[dict]) -> dict | None:
    """Latest 12-month period with net income filed: dividends paid, buybacks and the payout ratio.

    `facts`: latest-known us-gaap rows (no dimensions) with concept, value, period_start, period_end and provenance.
    """
    annual: dict = {}
    for f in facts:
        if f["period_start"] is None or not 350 <= (f["period_end"] - f["period_start"]).days <= 380:
            continue
        annual.setdefault(f["period_end"], {}).setdefault(f["concept"], f)

    def first(by: dict, concepts: list[str]):
        return next((by[c] for c in concepts if c in by), None)

    with_income = [end for end, by in annual.items() if first(by, INCOME_CONCEPTS)]
    if not with_income:
        return None
    end = max(with_income)
    paid = [e for e, by in annual.items() if first(by, DIVIDEND_CONCEPTS)]
    if not first(annual[end], DIVIDEND_CONCEPTS) and any(end - timedelta(days=400) < e < end for e in paid):
        # income filed for a trailing twelve months that the dividends are not (only for fiscal years): take the
        # latest period with both rather than read "no dividends" into it
        both = [e for e in with_income if e in paid]
        end = max(both) if both else end
    by = annual[end]
    ni = first(by, INCOME_CONCEPTS)
    div = first(by, DIVIDEND_CONCEPTS)
    buy = by.get(BUYBACK_CONCEPT)
    ni_v = float(ni["value"])
    div_v = None if div is None else float(div["value"])
    buy_v = None if buy is None else float(buy["value"])
    returned = None if div_v is None and buy_v is None else (div_v or 0.0) + (buy_v or 0.0)
    src = div or ni
    return {"periodStart": ni["period_start"], "periodEnd": end, "netIncome": ni_v, "dividendsPaid": div_v,
            "buybacks": buy_v, "payoutRatio": div_v / ni_v if div_v is not None and ni_v > 0 else None,
            "totalPayoutRatio": returned / ni_v if returned is not None and ni_v > 0 else None,
            "formType": src.get("form_type"), "filedDate": src.get("filed_date"), "accessionNo": src.get("accession_no"),
            "sourceUrl": src.get("source_url")}


# --------------------------------------------------------------------------- model features
DIV_FEATURES = ["div_yield", "div_growth", "payout_ratio"]
DIV_LABELS = {
    "div_yield": "Dividend yield (cash dividends of the last 12 months / close)",
    "div_growth": "Latest regular dividend vs a year earlier (log change; suspended = −1)",
    "payout_ratio": "Dividends paid / net income, latest filed 12 months",
}
GROWTH_CAP = 1.0
PAYOUT_CAP = 3.0
PAYOUT_CONCEPTS = frozenset({*INCOME_CONCEPTS, *DIVIDEND_CONCEPTS})


def dividend_growth(profile: dict) -> float:
    """Log change of the latest regular payment vs the regular payment about a year before it (capped at ±1).

    A suspended dividend counts as the full cut (−1); without a payment a year earlier (no dividend, or one that
    started less than a year ago) it is undefined (NaN)."""
    if profile["status"] == "SUSPENDED":
        return -GROWTH_CAP
    regular = [d for d in reversed(profile["payments"]) if not d["special"]]   # payments are newest first
    if not regular:
        return math.nan
    last = regular[-1]
    prior = [d for d in regular if last["exDate"] - timedelta(days=400) <= d["exDate"] <= last["exDate"] - timedelta(days=300)]
    if not prior or prior[-1]["amount"] <= 0:
        return math.nan
    return max(-GROWTH_CAP, min(GROWTH_CAP, math.log(last["amount"] / prior[-1]["amount"])))


def payout_ratio_feature(facts: Iterable[dict]) -> float:
    """Payout ratio of the latest filed 12 months for the model: 0 when net income is positive and no dividend
    was filed, NaN with a loss (not meaningful), capped at PAYOUT_CAP."""
    p = payout_from_facts(facts)
    if p is None or p["netIncome"] <= 0:
        return math.nan
    return 0.0 if p["dividendsPaid"] is None else min(PAYOUT_CAP, max(0.0, p["payoutRatio"]))


REFRESH_DAYS = 5


def daily_dividend_features(actions: list[tuple[date, str, float]], calendar: list[date], close) -> dict[str, list[float]]:
    """div_yield and div_growth for each calendar day, from the dividends with an ex-date on or before that day (known
    at its close). `close`: price per share of each day in the same share basis as the provider's dividends.

    The profile is recomputed on every ex-date or split and every REFRESH_DAYS trading days in between, and carried
    forward otherwise (a dividend leaving the 12-month window or turning overdue shows up a few days late); it only
    ever uses earlier data. The yield divides by each day's own close."""
    actions = sorted(actions)
    divs = [a[0] for a in actions if a[1] == "CASH_DIVIDEND"]
    events = {a[0] for a in actions}
    n = len(calendar)
    yld, growth = [math.nan] * n, [math.nan] * n
    ttm, g, age = 0.0, math.nan, REFRESH_DAYS
    prev = None
    for i, d in enumerate(calendar):
        c = close[i]
        priced = c is not None and not math.isnan(c) and c > 0
        if not divs or d < divs[0]:
            yld[i] = 0.0 if priced else math.nan
            prev = d
            continue
        if age >= REFRESH_DAYS or any(prev is None or prev < e <= d for e in events):
            p = dividend_profile(actions, d, None)
            ttm, g, age = p["ttmDividends"], dividend_growth(p), 0
        age += 1
        prev = d
        yld[i] = ttm / float(c) if priced else math.nan
        growth[i] = g
    return {"div_yield": yld, "div_growth": growth}
