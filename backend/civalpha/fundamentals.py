"""Financial-report profile: growth, earnings surprise, margins, balance sheet and valuation inputs, as filed.

Computed from the XBRL facts known after each filing acceptance (DataBundle snapshots), so a value can only be
used once the 10-Q/10-K that reports it has been accepted by EDGAR; an amendment counts from its own acceptance.
Quarterly flows use features.quarterly_values_records (Q4 = fiscal year minus the three reported quarters).

Earnings surprise without analyst estimates follows the seasonal random walk of Bernard & Thomas (1989):
SUE = (X_q - X_{q-4}) / std of the previous (up to 8) such seasonal differences, with X = diluted EPS when filed
(net income otherwise). The same standardization of revenue gives a revenue surprise (Jegadeesh & Livnat, 2006).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import REVENUE_CONCEPTS, _d, quarterly_values_records

FUND_FEATURES = ["rev_accel", "sue", "sue_rev", "gross_margin", "op_margin", "op_margin_chg", "net_margin", "rd_intensity",
                 "gp_assets", "cash_assets", "liab_assets", "int_coverage", "earnings_yield", "sales_yield", "days_since_filing"]
FUND_LABELS = {
    "rev_accel": "Revenue growth acceleration (YoY growth vs prior quarter's)",
    "sue": "Earnings surprise (standardized, vs same quarter last year)",
    "sue_rev": "Revenue surprise (standardized, vs same quarter last year)",
    "gross_margin": "Gross margin, last 4 quarters",
    "op_margin": "Operating margin, last 4 quarters",
    "op_margin_chg": "Change in quarterly operating margin vs a year earlier",
    "net_margin": "Net margin, last 4 quarters",
    "rd_intensity": "R&D spending / revenue, last 4 quarters",
    "gp_assets": "Gross profit / total assets (profitability)",
    "cash_assets": "Cash / total assets",
    "liab_assets": "Total liabilities / total assets",
    "int_coverage": "Operating income / interest expense",
    "earnings_yield": "Earnings yield (net income, last 4 quarters / market cap)",
    "sales_yield": "Sales yield (revenue, last 4 quarters / market cap)",
    "days_since_filing": "Days since the latest report was filed",
}
# not model inputs: needed to compute market-cap-based features in the daily panel
PROFILE_KEYS = [f for f in FUND_FEATURES if f not in ("earnings_yield", "sales_yield", "days_since_filing")] + [
    "ttm_net_income", "ttm_revenue", "shares", "shares_date"]

SUE_HISTORY = 8
SUE_MIN_HISTORY = 4
COVERAGE_CAP = 50.0


def _prior_year(series: dict, end) -> float | None:
    """Value of the quarter ending 355-375 days before `end` (the same fiscal quarter last year)."""
    cands = [e for e in series if end - pd.Timedelta(days=375) <= e <= end - pd.Timedelta(days=355)]
    return series[cands[-1]] if cands else None


def ttm(series: dict) -> float:
    """Sum of the last four quarters, if they are four consecutive quarters (about one year apart end to end)."""
    ends = list(series)[-4:]
    if len(ends) < 4 or not (250 <= (ends[-1] - ends[0]).days <= 290):
        return np.nan
    return float(sum(series[e] for e in ends))


def seasonal_surprise(series: dict) -> float:
    diffs = []
    for e, v in series.items():
        p = _prior_year(series, e)
        if p is not None:
            diffs.append(v - p)
    if len(diffs) < SUE_MIN_HISTORY + 1:
        return np.nan
    hist = np.array(diffs[-(SUE_HISTORY + 1):-1], dtype=float)
    sd = float(np.std(hist, ddof=1))
    return float(np.clip(diffs[-1] / sd, -10.0, 10.0)) if sd > 0 else np.nan


def _yoy_growth(series: dict, end) -> float:
    p = _prior_year(series, end)
    return series[end] / p - 1.0 if p else np.nan


def _ratio(a, b) -> float:
    return float(a / b) if b and np.isfinite(a) and np.isfinite(b) and b != 0 else np.nan


def _latest_instant(recs, concept: str, taxonomy: str | None = None):
    best = None
    for r in recs:
        if r[1] != concept or r[5] != "" or _d(r[3]) is not None or (taxonomy and r[0] != taxonomy):
            continue
        end = _d(r[4])
        if best is None or end >= best[0]:
            best = (end, float(r[6]))
    return best


def profile_from_records(recs) -> dict:
    recs = list(recs)
    rev = quarterly_values_records(recs, REVENUE_CONCEPTS)
    gross = quarterly_values_records(recs, ["GrossProfit"])
    op = quarterly_values_records(recs, ["OperatingIncomeLoss"])
    net = quarterly_values_records(recs, ["NetIncomeLoss"])
    rd = quarterly_values_records(recs, ["ResearchAndDevelopmentExpense"])
    interest = quarterly_values_records(recs, ["InterestExpense"])
    eps = quarterly_values_records(recs, ["EarningsPerShareDiluted"])
    out = {k: np.nan for k in PROFILE_KEYS}
    out["shares_date"] = None

    ends = list(rev)
    if len(ends) >= 2:
        g_last, g_prev = _yoy_growth(rev, ends[-1]), _yoy_growth(rev, ends[-2])
        out["rev_accel"] = g_last - g_prev if np.isfinite(g_last) and np.isfinite(g_prev) else np.nan
    out["sue"] = seasonal_surprise(eps if len(eps) >= SUE_MIN_HISTORY + 5 else net)
    out["sue_rev"] = seasonal_surprise(rev)

    r_ttm = ttm(rev)
    out["ttm_revenue"] = r_ttm
    out["ttm_net_income"] = ttm(net)
    out["gross_margin"] = _ratio(ttm(gross), r_ttm)
    out["op_margin"] = _ratio(ttm(op), r_ttm)
    out["net_margin"] = _ratio(out["ttm_net_income"], r_ttm)
    out["rd_intensity"] = _ratio(ttm(rd), r_ttm)
    if op and ends:
        last = max(e for e in op if e in rev) if any(e in rev for e in op) else None
        if last is not None:
            p_op, p_rev = _prior_year(op, last), _prior_year(rev, last)
            if p_op is not None and p_rev:
                out["op_margin_chg"] = op[last] / rev[last] - p_op / p_rev if rev[last] else np.nan
    i_ttm = ttm(interest)
    if np.isfinite(i_ttm) and i_ttm > 0:
        out["int_coverage"] = float(np.clip(ttm(op) / i_ttm, -COVERAGE_CAP, COVERAGE_CAP))

    assets = _latest_instant(recs, "Assets")
    if assets and assets[1] > 0:
        a = assets[1]
        out["gp_assets"] = _ratio(ttm(gross), a)
        cash = _latest_instant(recs, "CashAndCashEquivalentsAtCarryingValue")
        liab = _latest_instant(recs, "Liabilities")
        out["cash_assets"] = cash[1] / a if cash else np.nan
        out["liab_assets"] = liab[1] / a if liab else np.nan
    shares = _latest_instant(recs, "EntityCommonStockSharesOutstanding")
    if shares and shares[1] > 0:
        out["shares"], out["shares_date"] = shares[1], shares[0]
    return out


def split_factor_after(actions: pd.DataFrame, company_id: int, after, upto) -> float:
    """Product of split ratios with ex-date in (after, upto]: converts a share count reported at `after` to `upto`."""
    if actions is None or actions.empty:
        return 1.0
    a = actions[(actions["company_id"] == company_id) & (actions["action_type"] == "SPLIT")]
    a = a[(a["ex_date"] > pd.Timestamp(after)) & (a["ex_date"] <= pd.Timestamp(upto))]
    return float(np.prod(a["value"].astype(float))) if len(a) else 1.0
