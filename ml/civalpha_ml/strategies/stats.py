"""Performance statistics and the multiple-testing-aware verdict.

Testing many strategies on the same history makes the best one look good by luck. The verdict therefore
uses the Deflated Sharpe Ratio (Bailey & López de Prado, 2014) of each strategy's daily return in excess of
equal-weight buy & hold, deflated for the number of strategies tried, together with a stationary block
bootstrap CI of that excess return.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from .backtest import BacktestResult

DAYS = 252
EULER_GAMMA = 0.5772156649
MIN_YEARS = 3.0
DSR_LEVEL = 0.95
_N = NormalDist()


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return float((equity / peak - 1.0).min())


def drawdown_series(equity: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return equity / peak - 1.0


def cagr(equity: np.ndarray) -> float:
    n = len(equity)
    return float(equity[-1] ** (DAYS / n) - 1.0) if n and equity[-1] > 0 else float("nan")


def sharpe(x: np.ndarray) -> float:
    sd = np.std(x, ddof=1) if len(x) > 1 else 0.0
    return float(np.mean(x) / sd * math.sqrt(DAYS)) if sd > 0 else float("nan")


def sortino(x: np.ndarray) -> float:
    down = np.minimum(x, 0.0)
    dd = math.sqrt(np.mean(down ** 2)) if len(x) else 0.0
    return float(np.mean(x) / dd * math.sqrt(DAYS)) if dd > 0 else float("nan")


def stationary_bootstrap_ci(x: np.ndarray, n_boot: int = 1000, mean_block: int = 21, seed: int = 11) -> tuple[float, float]:
    """95% CI of the annualized mean of x, resampling blocks of random (geometric) length to keep autocorrelation."""
    n = len(x)
    if n < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    pos = np.arange(n)
    jump = rng.random((n_boot, n)) < 1.0 / mean_block
    jump[:, 0] = True
    fresh = rng.integers(n, size=(n_boot, n))
    last = np.maximum.accumulate(np.where(jump, pos, 0), axis=1)   # start of the current block
    idx = (np.take_along_axis(fresh, last, axis=1) + (pos - last)) % n
    means = x[idx].mean(axis=1)
    return float(np.percentile(means, 2.5) * DAYS), float(np.percentile(means, 97.5) * DAYS)


def deflated_sharpe(x: np.ndarray, n_trials: int, trial_sharpes: list[float]) -> float:
    """Probability that the true (per-period) Sharpe of x exceeds what the best of n_trials would show by luck."""
    n = len(x)
    sd = np.std(x, ddof=1) if n > 1 else 0.0
    if n < 3 or sd == 0:
        return float("nan")
    sr = np.mean(x) / sd
    finite = [s for s in trial_sharpes if np.isfinite(s)]
    var = float(np.var(finite, ddof=1)) if len(finite) > 1 else 0.0
    if n_trials > 1 and var > 0:
        sr0 = math.sqrt(var) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1.0 / n_trials)
                                + EULER_GAMMA * _N.inv_cdf(1 - 1.0 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    z = (x - x.mean()) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return float("nan")
    return float(_N.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)))


def per_period_sharpe(x: np.ndarray) -> float:
    sd = np.std(x, ddof=1) if len(x) > 1 else 0.0
    return float(np.mean(x) / sd) if sd > 0 else float("nan")


def yearly_returns(res: BacktestResult) -> list[dict]:
    s = pd.Series(res.net, index=res.dates)
    g = (1.0 + s).groupby(s.index.year).prod() - 1.0
    return [{"year": int(y), "return": float(r)} for y, r in g.items()]


def equity_points(res: BacktestResult, every: int = 5) -> list[dict]:
    dd = drawdown_series(res.equity)
    idx = list(range(0, len(res.dates), every))
    if idx and idx[-1] != len(res.dates) - 1:
        idx.append(len(res.dates) - 1)
    return [{"date": str(res.dates[i].date()), "equity": float(res.equity[i]), "drawdown": float(dd[i])} for i in idx]


def metrics(res: BacktestResult, ref: BacktestResult, cash: np.ndarray, trades: list[dict]) -> dict:
    years = len(res.net) / DAYS
    excess_cash = res.net - cash
    var_ref = float(np.var(ref.net, ddof=1)) if len(ref.net) > 1 else 0.0
    beta = float(np.cov(res.net, ref.net, ddof=1)[0, 1] / var_ref) if var_ref > 0 else float("nan")
    alpha = float((np.mean(res.net) - beta * np.mean(ref.net)) * DAYS) if np.isfinite(beta) else float("nan")
    closed = [t for t in trades if t["exitDate"] is not None and np.isfinite(t["return"])]
    mdd = max_drawdown(res.equity)
    c = cagr(res.equity)
    return {
        "start": str(res.dates[0].date()), "end": str(res.dates[-1].date()), "years": years,
        "totalReturn": float(res.equity[-1] - 1.0), "cagr": c,
        "volatility": float(np.std(res.net, ddof=1) * math.sqrt(DAYS)) if len(res.net) > 1 else float("nan"),
        "sharpe": sharpe(excess_cash), "sortino": sortino(excess_cash), "maxDrawdown": mdd,
        "calmar": float(c / abs(mdd)) if mdd < 0 and np.isfinite(c) else float("nan"),
        "exposure": float(np.mean(res.exposure)), "beta": beta, "alpha": alpha,
        "trades": len(trades), "closedTrades": len(closed),
        "winRate": float(np.mean([t["return"] > 0 for t in closed])) if closed else float("nan"),
        "avgHoldingDays": float(np.mean([t["holdingDays"] for t in trades])) if trades else float("nan"),
        "turnoverPerYear": float(res.turnover.sum() / years) if years > 0 else float("nan"),
        "costDragPerYear": float(res.cost.sum() / years) if years > 0 else float("nan"),
    }


def verdict(m: dict, is_reference: bool) -> str:
    if is_reference:
        return "Reference portfolio"
    ok = (m["years"] >= MIN_YEARS and m.get("excessCiLow") is not None and np.isfinite(m["excessCiLow"])
          and m["excessCiLow"] > 0 and m.get("deflatedSharpe") is not None and np.isfinite(m["deflatedSharpe"])
          and m["deflatedSharpe"] >= DSR_LEVEL)
    return "Beats buy-and-hold after costs: " + ("SUPPORTED by this backtest" if ok else "NOT supported")
