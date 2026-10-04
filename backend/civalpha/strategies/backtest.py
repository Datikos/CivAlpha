"""Daily long-only backtest engine.

Timing: weights decided at close(t) are traded at close(t+1) and earn returns from t+1 to t+2 onward,
the same "enter at the next close" convention as the forecast evaluation. Holdings drift with prices
between closes; every day the book is traded back to its target and the cost is charged on the actual
traded amount, sum |target - drifted weight| x cost_bps_per_side. Idle capital earns the cash return.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    dates: pd.DatetimeIndex
    net: np.ndarray          # daily net return
    gross: np.ndarray
    cost: np.ndarray
    turnover: np.ndarray     # traded weight per day
    exposure: np.ndarray     # invested fraction after trading
    equity: np.ndarray       # cumulative net growth of 1


def run(weights: pd.DataFrame, returns: pd.DataFrame, cash: pd.Series, start_idx: int,
        cost_bps_per_side: float = 10.0) -> BacktestResult:
    """Simulate from calendar index `start_idx` (first day a trade can execute) to the end, starting in cash."""
    W = weights.reindex(index=returns.index, columns=returns.columns, fill_value=0.0).to_numpy(float)
    R = returns.to_numpy(float)
    C = cash.reindex(returns.index).fillna(0.0).to_numpy(float)
    T, N = W.shape
    c = cost_bps_per_side / 1e4
    held = np.zeros(N)
    out = {k: np.zeros(T - start_idx) for k in ("net", "gross", "cost", "turnover", "exposure")}
    for k, s in enumerate(range(start_idx, T)):
        r = np.nan_to_num(R[s])
        g = 0.0
        if k > 0:  # day s return accrues to what was held after the close of s-1
            g = float(held @ r + (1.0 - held.sum()) * C[s])
        drifted = held * (1.0 + r) / (1.0 + g) if k > 0 else held
        target = W[s - 1]              # decided at close(s-1), executed at close(s)
        to = float(np.abs(target - drifted).sum())
        cost = c * to
        out["gross"][k] = g
        out["cost"][k] = cost
        out["net"][k] = (1.0 + g) * (1.0 - cost) - 1.0
        out["turnover"][k] = to
        out["exposure"][k] = float(target.sum())
        held = target
    dates = returns.index[start_idx:]
    return BacktestResult(dates=dates, equity=np.cumprod(1.0 + out["net"]), **out)


def trades(weights: pd.DataFrame, px: pd.DataFrame, start_idx: int, entry_label: str, exit_label: str,
           trailing_stop: float | None = None) -> list[dict]:
    """Round trips per asset from the decision matrix. Dates are execution dates (the close after the decision)."""
    W = weights.reindex(columns=px.columns, fill_value=0.0).to_numpy(float) > 0
    P = px.to_numpy(float)
    cal = px.index
    T = len(cal)
    out = []
    for j, col in enumerate(px.columns):
        on = W[:, j]
        pj = P[:, j]
        t = max(start_idx - 1, 0)
        entry = None
        peak = np.nan
        while t < T - 1:
            if on[t] and entry is None:
                entry, peak = t + 1, pj[t + 1]
            elif entry is not None:
                peak = np.fmax(peak, pj[t])
                if not on[t]:
                    stopped = trailing_stop is not None and pj[t] <= peak * (1.0 - trailing_stop)
                    out.append(_trade(col, cal, pj, entry, t + 1, entry_label, "Trailing stop" if stopped else exit_label))
                    entry = None
            t += 1
        if entry is not None:
            out.append(_trade(col, cal, pj, entry, T - 1, entry_label, "Still open", closed=False))
    return out


def _trade(col, cal, p, i, j, entry_label, exit_label, closed=True) -> dict:
    r = p[j] / p[i] - 1.0 if np.isfinite(p[i]) and p[i] != 0 and np.isfinite(p[j]) else np.nan
    return {"asset": col, "entryDate": cal[i].date(), "exitDate": cal[j].date() if closed else None,
            "return": float(r), "holdingDays": int(j - i), "entryReason": entry_label, "exitReason": exit_label}
