"""Strategy interface and shared indicators.

A strategy turns the panel into target weights decided at close(t). Row t may only use data known at close(t);
tests/test_strategies.py checks this for every registered strategy by changing all prices after t.

Sizing:
  SLEEVE  each member owns 1/N of capital and sits in cash while its signal is off (pure timing rules)
  EQUAL   equal weight across the names the rule selects, fully invested (selection rules)
  WEIGHTS the rule returns weights itself (AI, ETF basket)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .panel import MarketPanel

FAMILIES = ("BENCHMARK", "TREND", "MEAN_REVERSION", "FUNDAMENTAL", "EVENT", "AI")


@dataclass
class Strategy:
    key: str
    family: str
    name: str
    entry: str            # human-readable entry rule
    exit: str             # human-readable exit rule
    origin: str           # where the idea comes from
    sizing: str           # SLEEVE | EQUAL | WEIGHTS
    fn: Callable[[MarketPanel], pd.DataFrame]
    params: dict = field(default_factory=dict)
    assets: str = "STOCKS"          # STOCKS (company columns) | ETFS (benchmark symbol columns)
    trailing_stop: float | None = None

    def weights(self, panel: MarketPanel) -> pd.DataFrame:
        raw = self.fn(panel)
        if self.sizing == "WEIGHTS":
            w = raw.fillna(0.0).astype(float)
        else:
            on = raw.fillna(False).astype(bool)
            if self.assets == "STOCKS":
                on &= panel.member.reindex(index=on.index, columns=on.columns, fill_value=False)
            if self.sizing == "SLEEVE":
                n = panel.member.sum(axis=1).replace(0, np.nan)
                w = on.astype(float).div(n, axis=0).fillna(0.0)
            elif self.sizing == "EQUAL":
                k = on.sum(axis=1).replace(0, np.nan)
                w = on.astype(float).div(k, axis=0).fillna(0.0)
            else:
                raise ValueError(f"unknown sizing {self.sizing}")
        if self.trailing_stop:
            w = apply_trailing_stop(w, panel.px.reindex(columns=w.columns), self.trailing_stop)
        return w

    def describe(self) -> dict:
        return {"entry": self.entry, "exit": self.exit, "origin": self.origin, "sizing": self.sizing,
                **({"trailingStop": self.trailing_stop} if self.trailing_stop else {})}


# --------------------------------------------------------------------------- indicators (all use data <= t)
def sma(px: pd.DataFrame, n: int) -> pd.DataFrame:
    return px.rolling(n, min_periods=n).mean()


def rolling_std(px: pd.DataFrame, n: int) -> pd.DataFrame:
    return px.rolling(n, min_periods=n).std(ddof=0)


def rsi(px: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder's RSI on closes."""
    d = px.diff()
    up = d.clip(lower=0.0)
    down = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    ad = down.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(ad != 0.0, 100.0).where(au.notna())


def prior_high(px: pd.DataFrame, n: int) -> pd.DataFrame:
    """Highest close of the n days before t (excluding t)."""
    return px.shift(1).rolling(n, min_periods=n).max()


def prior_low(px: pd.DataFrame, n: int) -> pd.DataFrame:
    return px.shift(1).rolling(n, min_periods=n).min()


def period_starts(cal: pd.DatetimeIndex, freq: str) -> np.ndarray:
    """True on the first trading day of each month ('M') or week ('W'); needs only dates up to t."""
    if freq == "M":
        key = cal.year * 12 + cal.month
    elif freq == "W":
        iso = cal.isocalendar()
        key = (iso["year"] * 100 + iso["week"]).to_numpy()
    else:
        raise ValueError(freq)
    key = np.asarray(key)
    first = np.ones(len(cal), dtype=bool)
    first[1:] = key[1:] != key[:-1]
    return first


def hold_between_rebalances(picks: pd.DataFrame, rebalance: np.ndarray) -> pd.DataFrame:
    """Keep each rebalance day's selection until the next rebalance day."""
    p = picks.astype(float).where(np.repeat(rebalance[:, None], picks.shape[1], axis=1))
    return p.ffill().fillna(0.0).astype(bool)


def state_machine(enter: pd.DataFrame, exit_: pd.DataFrame, max_hold: int | None = None) -> pd.DataFrame:
    """Per column: go long when `enter` fires while flat, go flat when `exit_` fires (or after max_hold days)."""
    e = enter.fillna(False).to_numpy(bool)
    x = exit_.fillna(False).to_numpy(bool)
    T, N = e.shape
    out = np.zeros((T, N), dtype=bool)
    pos = np.zeros(N, dtype=bool)
    held = np.zeros(N, dtype=int)
    for t in range(T):
        leave = pos & (x[t] | ((held >= max_hold) if max_hold else False))
        pos = pos & ~leave
        held = np.where(pos, held + 1, 0)
        start = ~pos & e[t] & ~leave
        pos = pos | start
        held = np.where(start, 0, held)
        out[t] = pos
    return pd.DataFrame(out, index=enter.index, columns=enter.columns)


def apply_trailing_stop(w: pd.DataFrame, px: pd.DataFrame, stop: float) -> pd.DataFrame:
    """Exit overlay: close a position once its price falls `stop` below the highest close since entry.

    After a stop the name stays out until the underlying rule has switched off at least once, so the
    stop is not immediately undone by the same signal.
    """
    base = w.to_numpy(float)
    p = px.to_numpy(float)
    T, N = base.shape
    out = np.zeros_like(base)
    in_pos = np.zeros(N, dtype=bool)
    blocked = np.zeros(N, dtype=bool)
    peak = np.full(N, np.nan)
    for t in range(T):
        on = base[t] > 0
        blocked &= on                     # rule switched off -> allowed again next time
        enter = on & ~in_pos & ~blocked
        peak = np.where(enter, p[t], peak)
        in_pos = (in_pos | enter) & on
        peak = np.where(in_pos, np.fmax(peak, p[t]), np.nan)
        hit = in_pos & (p[t] <= peak * (1.0 - stop))
        blocked |= hit
        in_pos &= ~hit
        out[t] = np.where(in_pos, base[t], 0.0)
    return pd.DataFrame(out, index=w.index, columns=w.columns)
