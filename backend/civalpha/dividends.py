"""Dividend profile: does a company pay, how often, how much, for how long, and how much of its earnings.

Computed from the recorded cash dividends (corporate_action, from the price provider) as of a date, so it only uses
ex-dates on or before that date. Amounts are expressed per share as of that date: a dividend paid before a SPLIT
(raw-price providers) is divided by the later split ratios, the same convention as returns.total_return_index.
SPLIT_INFO rows mean the provider already adjusted its history, so they are ignored here.

Classification:
  - a payment more than SPECIAL_MULTIPLE times the median of the other payments in the lookback is a special dividend;
    it counts in trailing totals but not in the frequency or the indicated (annualized) dividend
  - frequency from the median gap between regular ex-dates in the last FREQUENCY_LOOKBACK_DAYS
  - status: REGULAR while the next payment is not overdue, SUSPENDED once it is, IRREGULAR without a steady schedule,
    NONE when no dividend is recorded

Payout ratio and buybacks come from the latest fiscal-year cash-flow statement as filed with the SEC
(PaymentsOfDividends* and PaymentsForRepurchaseOfCommonStock against NetIncomeLoss).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from statistics import median

FREQUENCY_LOOKBACK_DAYS = 2 * 365 + 30
SPECIAL_MULTIPLE = 2.5
TOLERANCE = 1e-6

# (upper bound of the median gap in days, label, payments per year)
FREQUENCIES = [(45, "MONTHLY", 12), (120, "QUARTERLY", 4), (240, "SEMIANNUAL", 2), (400, "ANNUAL", 1)]
DIVIDEND_CONCEPTS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]
BUYBACK_CONCEPT = "PaymentsForRepurchaseOfCommonStock"


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

    recent = [d for d in divs if d["exDate"] > as_of - timedelta(days=FREQUENCY_LOOKBACK_DAYS)]
    _mark_specials(recent)
    for d in divs:
        d.setdefault("special", False)
    regular_recent = [d for d in recent if not d["special"]]
    gaps = [(b["exDate"] - a["exDate"]).days for a, b in zip(regular_recent, regular_recent[1:])]
    gap = median(gaps) if gaps else None
    frequency, per_year = _frequency(gap)

    last = divs[-1]
    last_regular = regular_recent[-1] if regular_recent else None
    out.update(firstExDate=divs[0]["exDate"], lastExDate=last["exDate"], lastAmount=last["amount"])
    year_start = as_of - timedelta(days=365)
    ttm = [d for d in divs if d["exDate"] > year_start]
    out["ttmDividends"] = sum(d["amount"] for d in ttm)
    out["ttmSpecial"] = sum(d["amount"] for d in ttm if d["special"])
    out["ttmPayments"] = len(ttm)

    if per_year is not None and last_regular is not None:
        overdue = last_regular["exDate"] + timedelta(days=max(1.5 * gap, gap + 45))
        if as_of > overdue:
            out["status"] = "SUSPENDED"
        else:
            out["status"] = "REGULAR"
            out.update(frequency=frequency, paymentsPerYear=per_year, indicatedAnnual=last_regular["amount"] * per_year,
                       nextExpected=last_regular["exDate"] + timedelta(days=round(gap)))
    else:
        out["status"] = "IRREGULAR" if ttm else "SUSPENDED"
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
    """Latest fiscal year with net income filed: dividends paid, buybacks and the payout ratio.

    `facts`: latest-known us-gaap rows (no dimensions) with concept, value, period_start, period_end and provenance.
    """
    annual: dict = {}
    for f in facts:
        if f["period_start"] is None or not 350 <= (f["period_end"] - f["period_start"]).days <= 380:
            continue
        annual.setdefault(f["period_end"], {}).setdefault(f["concept"], f)
    years = [end for end, by in annual.items() if "NetIncomeLoss" in by]
    if not years:
        return None
    end = max(years)
    by = annual[end]
    ni = by["NetIncomeLoss"]
    div = next((by[c] for c in DIVIDEND_CONCEPTS if c in by), None)
    buy = by.get(BUYBACK_CONCEPT)
    ni_v = float(ni["value"])
    div_v = None if div is None else float(div["value"])
    buy_v = None if buy is None else float(buy["value"])
    returned = None if div_v is None and buy_v is None else (div_v or 0.0) + (buy_v or 0.0)
    src = div or ni
    return {"fiscalYearStart": ni["period_start"], "fiscalYearEnd": end, "netIncome": ni_v, "dividendsPaid": div_v,
            "buybacks": buy_v, "payoutRatio": div_v / ni_v if div_v is not None and ni_v > 0 else None,
            "totalPayoutRatio": returned / ni_v if returned is not None and ni_v > 0 else None,
            "formType": src.get("form_type"), "filedDate": src.get("filed_date"), "accessionNo": src.get("accession_no"),
            "sourceUrl": src.get("source_url")}
