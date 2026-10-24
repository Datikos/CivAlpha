"""Which insider trades could carry information (ADR-0010).

* Role: CEO_CFO when the Form 4 title names the chief executive or the chief financial officer, otherwise the stored
  relationship (Officer, Director, TenPercentOwner, Other).
* Classification of each open-market trade (P or S), after Cohen, Malloy & Pomorski (2012, "Decoding Inside
  Information"), point in time: only the same insider's open-market trades in the same company that were public before
  this one are looked at, by the calendar year and month of their trade dates.
    PLANNED           the filing ticks the Rule 10b5-1 box (a pre-arranged plan; the box exists from 2023-04);
    ROUTINE           the insider traded in this calendar month in each of the three previous years;
    NOT_CLASSIFIABLE  the insider has no trade in one of those three years, or the data do not reach back to this
                      month three years earlier (the first data set is 2020-Q3, so nothing before 2023-07 is classified);
    INFORMED          otherwise.
A later trade never changes an earlier classification (tests/test_insider_quality.py).
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

YEARS = 3
PLANNED, ROUTINE, INFORMED, NOT_CLASSIFIABLE = "PLANNED", "ROUTINE", "INFORMED", "NOT_CLASSIFIABLE"
CODES = ("P", "S")
_EXEC = re.compile(r"chief\s+executive|\bceo\b|chief\s+financial|\bcfo\b", re.IGNORECASE)


def insider_role(relationship: str | None, title: str | None) -> str:
    """Missing values may arrive as None or as NaN (a NULL column read by pandas)."""
    if isinstance(title, str) and _EXEC.search(title):
        return "CEO_CFO"
    return relationship if isinstance(relationship, str) and relationship else "Other"


def data_start(insiders: pd.DataFrame) -> date | None:
    """The first day insider data exist for (the earliest availability)."""
    if insiders is None or not len(insiders):
        return None
    return pd.Timestamp(insiders["available_at"].min()).date()


def classifiable_from(start: date | None) -> date | None:
    """The first month whose trades can be classified: three years after the month the data start."""
    if start is None:
        return None
    return date(start.year + YEARS, start.month, 1)


def classify(trades: pd.DataFrame, start: date | None) -> np.ndarray:
    """PLANNED / ROUTINE / INFORMED / NOT_CLASSIFIABLE for each row of `trades` (open-market trades with company_id, owner key
    `owner`, trans_date, available_at, plan_10b5_1), in the rows' order."""
    out = np.full(len(trades), NOT_CLASSIFIABLE, dtype=object)
    if not len(trades):
        return out
    t = trades.reset_index(drop=True)
    plan = t["plan_10b5_1"].to_numpy(object) if "plan_10b5_1" in t else np.full(len(t), None, dtype=object)
    out[plan == True] = PLANNED  # noqa: E712 - None and False are both "not ticked"
    first = classifiable_from(start)
    if first is None:
        return out
    days = pd.to_datetime(t["trans_date"], errors="coerce")
    ok = days.notna().to_numpy()
    year = np.where(ok, days.dt.year.fillna(0).astype(int).to_numpy(), 0)
    month = np.where(ok, days.dt.month.fillna(0).astype(int).to_numpy(), 0)
    avail = pd.to_datetime(t["available_at"], utc=True).to_numpy("datetime64[ns]").astype(np.int64)
    first_key = first.year * 12 + first.month
    for _key, idx in t.groupby(["company_id", "owner"], sort=False).indices.items():
        order = idx[np.argsort(avail[idx], kind="stable")]
        months: dict[int, set[int]] = {}
        i = 0
        while i < len(order):
            # trades public at the same time do not see each other
            j = i
            while j < len(order) and avail[order[j]] == avail[order[i]]:
                j += 1
            batch = order[i:j]
            for r in batch:
                if out[r] == PLANNED or not ok[r] or year[r] * 12 + month[r] < first_key:
                    continue
                prev = [months.get(year[r] - k) for k in range(1, YEARS + 1)]
                if any(p is None for p in prev):
                    continue
                out[r] = ROUTINE if all(month[r] in p for p in prev) else INFORMED
            for r in batch:
                if ok[r]:
                    months.setdefault(int(year[r]), set()).add(int(month[r]))
            i = j
    return out
