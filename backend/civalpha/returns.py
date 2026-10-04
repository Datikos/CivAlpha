"""Total-return indices and the forecast target.

Prices are stored raw (unadjusted). Splits and cash dividends are applied from the corporate-action table
on their ex-dates, so adjusting never needs information from after the date being computed.

Target (fixed for the MVP):
    y(t) = 1  if  TR_stock(t+21)/TR_stock(t) > TR_bench(t+21)/TR_bench(t)
where t is the as-of trading date (window starts at the close of t) and +21 counts trading days in the
benchmark calendar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON = 21


def total_return_index(closes: pd.Series, actions: pd.DataFrame | None, calendar: pd.DatetimeIndex) -> np.ndarray:
    """Total-return index aligned to `calendar` (NaN before the first bar, forward-filled on gaps).

    Daily gross return on an ex-date with split ratio R (new shares per old share) and dividend D per
    post-split share:  (close_t * R + D) / close_{t-1}.
    """
    c = closes.sort_index()
    c = c[~c.index.duplicated(keep="last")].reindex(calendar).ffill()
    vals = c.to_numpy(dtype=float)
    split = np.ones(len(calendar))
    div = np.zeros(len(calendar))
    if actions is not None and len(actions):
        pos = {d: i for i, d in enumerate(calendar)}
        for a in actions.itertuples(index=False):
            i = pos.get(pd.Timestamp(a.ex_date))
            if i is None:
                continue
            if a.action_type == "SPLIT":
                split[i] *= float(a.value)
            elif a.action_type == "CASH_DIVIDEND":
                div[i] += float(a.value)
    gross = np.full(len(calendar), np.nan)
    gross[1:] = (vals[1:] * split[1:] + div[1:]) / vals[:-1]
    out = np.full(len(calendar), np.nan)
    started = False
    level = 1.0
    for i in range(len(calendar)):
        if np.isnan(vals[i]):
            continue
        if not started:
            started, level = True, 1.0
        elif not np.isnan(gross[i]):
            level *= gross[i]
        out[i] = level
    return out


def window_return(tr: np.ndarray, start_idx: int, horizon: int = HORIZON) -> float:
    end = start_idx + horizon
    if start_idx < 0 or end >= len(tr) or np.isnan(tr[start_idx]) or np.isnan(tr[end]):
        return float("nan")
    return float(tr[end] / tr[start_idx] - 1.0)


def excess_label(tr_stock: np.ndarray, tr_bench: np.ndarray, idx: int, horizon: int = HORIZON) -> dict:
    s = window_return(tr_stock, idx, horizon)
    b = window_return(tr_bench, idx, horizon)
    ex = s - b
    return {"stock_return": s, "benchmark_return": b, "excess_return": ex,
            "label": (None if np.isnan(ex) else bool(ex > 0))}
