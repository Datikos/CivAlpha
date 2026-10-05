"""Classic entry/exit rules, all long-only and vectorized over (date x company)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import pit
from . import doublers
from .base import (Strategy, hold_between_rebalances, period_starts, prior_high, prior_low, rolling_std, rsi, sma,
                   state_machine)
from .panel import MarketPanel


# --------------------------------------------------------------------------- benchmarks
def _all_members(p: MarketPanel) -> pd.DataFrame:
    return p.member.copy()


def _sector_etfs(p: MarketPanel) -> pd.DataFrame:
    """Hold each sector ETF in proportion to how many universe members it benchmarks that day."""
    counts = pd.DataFrame(0.0, index=p.calendar, columns=p.etf_ret.columns)
    for c, sym in p.benchmark_of.items():
        counts[sym] += p.member[c].astype(float)
    tot = counts.sum(axis=1).replace(0, np.nan)
    return counts.div(tot, axis=0).fillna(0.0)


# --------------------------------------------------------------------------- trend / momentum
def _sma_cross(p: MarketPanel, fast: int = 50, slow: int = 200) -> pd.DataFrame:
    return sma(p.px, fast) > sma(p.px, slow)


def _momentum_12_1(p: MarketPanel, top: int = 5) -> pd.DataFrame:
    mom = p.px.shift(21) / p.px.shift(252) - 1.0
    ranks = mom.where(p.member).rank(axis=1, ascending=False, method="first")
    return hold_between_rebalances(ranks <= top, period_starts(p.calendar, "M"))


def _donchian(p: MarketPanel, entry: int = 55, exit_: int = 20) -> pd.DataFrame:
    return state_machine(p.px > prior_high(p.px, entry), p.px < prior_low(p.px, exit_))


# --------------------------------------------------------------------------- mean reversion
def _rsi2(p: MarketPanel, level: float = 10.0) -> pd.DataFrame:
    enter = (rsi(p.px, 2) < level) & (p.px > sma(p.px, 200))
    return state_machine(enter, p.px > sma(p.px, 5))


def _bollinger(p: MarketPanel, n: int = 20, k: float = 2.0, max_hold: int = 15) -> pd.DataFrame:
    mid = sma(p.px, n)
    lower = mid - k * rolling_std(p.px, n)
    return state_machine(p.px < lower, p.px >= mid, max_hold=max_hold)


def _reversal_5d(p: MarketPanel, bottom: int = 5) -> pd.DataFrame:
    r5 = p.px / p.px.shift(5) - 1.0
    ranks = r5.where(p.member).rank(axis=1, ascending=True, method="first")
    return hold_between_rebalances(ranks <= bottom, period_starts(p.calendar, "W"))


# --------------------------------------------------------------------------- fundamental / event
def _quality_growth(p: MarketPanel) -> pd.DataFrame:
    """Revenue growing, gross margin not shrinking, leverage at or below the universe median (as filed at t).

    Fundamentals only change when a filing is accepted, so the as-filed values are looked up per day with
    DataBundle.fundamentals_at (point-in-time). Missing gross margin (not reported) or missing long-term
    debt (none reported) do not disqualify a company; missing revenue growth does.
    """
    b = p.bundle
    out = pd.DataFrame(False, index=p.calendar, columns=p.px.columns)
    for i, d in enumerate(p.calendar):
        as_of = pit.close_ts(d)
        members = [c for c in p.px.columns if p.member.iat[i, p.px.columns.get_loc(c)]]
        if not members:
            continue
        f = {c: b.fundamentals_at(c, as_of) for c in members}
        levs = [v["leverage"] for v in f.values() if not np.isnan(v["leverage"])]
        med = float(np.median(levs)) if levs else np.nan
        for c, v in f.items():
            ok = v["rev_yoy"] > 0 and not (v["gm_chg"] < 0)
            if not np.isnan(med) and not np.isnan(v["leverage"]):
                ok = ok and v["leverage"] <= med
            out.iat[i, out.columns.get_loc(c)] = bool(ok)
    return out


def _pead(p: MarketPanel, threshold: float = 1.0, hold: int = 60) -> pd.DataFrame:
    """Post-earnings-announcement drift: buy on the day a report with a large positive earnings surprise
    becomes public, hold for `hold` trading days."""
    f = p.fundamentals()
    enter = f["new_filing"] & (f["sue"] >= threshold)
    never = pd.DataFrame(False, index=enter.index, columns=enter.columns)
    return state_machine(enter, never, max_hold=hold)


# --------------------------------------------------------------------------- speculative
def _doubler_screen(p: MarketPanel, cfg: doublers.DoublerConfig = doublers.DoublerConfig()) -> pd.DataFrame:
    """The doubler study's screen as a trading rule: buy when it fires, hold `cfg.hold` days (the stop is an overlay)."""
    never = pd.DataFrame(False, index=p.calendar, columns=p.px.columns)
    return state_machine(doublers.screen(p, cfg), never, max_hold=cfg.hold)


def _top_by(p: MarketPanel, score: pd.DataFrame, top: int, require_positive: bool = False) -> pd.DataFrame:
    s = score.where(p.member)
    if require_positive:
        s = s.where(s > 0)
    ranks = s.rank(axis=1, ascending=False, method="first")
    return hold_between_rebalances(ranks <= top, period_starts(p.calendar, "M"))


def _value_ey(p: MarketPanel, top: int = 5) -> pd.DataFrame:
    return _top_by(p, p.fundamentals()["earnings_yield"], top, require_positive=True)


def _gross_profitability(p: MarketPanel, top: int = 5) -> pd.DataFrame:
    return _top_by(p, p.fundamentals()["gp_assets"], top)


def _dividend_yield(p: MarketPanel, top: int = 5) -> pd.DataFrame:
    return _top_by(p, p.dividends()["div_yield"], top, require_positive=True)


def _event_avoid(p: MarketPanel, trade_threshold: float = 0.03, rate_threshold: float = 0.01) -> pd.DataFrame:
    """Hold every member, except while a recent official tariff or rate decision hurts it more than a threshold.

    Uses the same exposure-weighted, 30-day-decaying shock features as the forecasting model, so the
    company stays out roughly until the shock has faded.
    """
    trade = p.feature_matrix("trade_shock")
    rate = p.feature_matrix("rate_shock")
    hurt = (trade < -trade_threshold) | (rate < -rate_threshold)
    return p.member & ~hurt.fillna(False)


# --------------------------------------------------------------------------- registry
def rule_strategies() -> list[Strategy]:
    return [
        Strategy("EW_BUY_HOLD", "BENCHMARK", "Equal-weight buy & hold",
                 "Own every universe member in equal weight", "Never (rebalanced to equal weight daily)",
                 "The default an active rule has to beat", "EQUAL", _all_members),
        Strategy("SECTOR_ETFS", "BENCHMARK", "Sector ETF basket",
                 "Own the sector benchmark ETFs, weighted by how many universe members each covers", "Never",
                 "Passive alternative: the sector benchmarks themselves", "WEIGHTS", _sector_etfs, assets="ETFS"),
        Strategy("SMA_50_200", "TREND", "Golden cross (50/200-day average)",
                 "50-day average closes above the 200-day average", "50-day average falls below the 200-day average",
                 "Classic trend following (golden cross / death cross)", "SLEEVE", _sma_cross, {"fast": 50, "slow": 200}),
        Strategy("SMA_50_200_TSTOP10", "TREND", "Golden cross + 10% trailing stop",
                 "50-day average closes above the 200-day average", "Death cross, or the close falls 10% below its high since entry",
                 "Trend following with a protective trailing stop", "SLEEVE", _sma_cross, {"fast": 50, "slow": 200},
                 trailing_stop=0.10),
        Strategy("MOM_12_1", "TREND", "12-1 month momentum (top 5)",
                 "Monthly: buy the 5 stocks with the best return from 12 months to 1 month ago",
                 "Sold at the next monthly rebalance if no longer in the top 5",
                 "Jegadeesh & Titman (1993) cross-sectional momentum", "EQUAL", _momentum_12_1, {"top": 5, "rebalance": "monthly"}),
        Strategy("DONCHIAN_55_20", "TREND", "Donchian breakout (55/20, turtle)",
                 "Close above the highest close of the previous 55 days", "Close below the lowest close of the previous 20 days",
                 "Turtle trading system (Dennis & Eckhardt), close-only version", "SLEEVE", _donchian, {"entry": 55, "exit": 20}),
        Strategy("RSI2_SMA200", "MEAN_REVERSION", "RSI(2) pullback in an uptrend",
                 "RSI(2) below 10 while the close is above its 200-day average", "Close above its 5-day average",
                 "Connors short-term mean reversion", "SLEEVE", _rsi2, {"rsi": 2, "level": 10, "trend": 200, "exit": 5}),
        Strategy("BOLLINGER_20_2", "MEAN_REVERSION", "Bollinger band bounce (20, 2σ)",
                 "Close below the lower band (20-day average minus 2 standard deviations)",
                 "Close back at the 20-day average, or after 15 trading days",
                 "Bollinger bands, mean-reversion use", "SLEEVE", _bollinger, {"n": 20, "k": 2, "maxHold": 15}),
        Strategy("REVERSAL_5D", "MEAN_REVERSION", "Weekly short-term reversal (bottom 5)",
                 "Weekly: buy the 5 stocks with the worst 5-day return", "Sold at the next weekly rebalance",
                 "Short-term reversal effect (Jegadeesh 1990; Lehmann 1990)", "EQUAL", _reversal_5d, {"bottom": 5, "rebalance": "weekly"}),
        Strategy("QUALITY_GROWTH", "FUNDAMENTAL", "Quality & growth filter (SEC filings)",
                 "Latest filing shows revenue growth > 0, gross margin not down year over year, leverage ≤ universe median",
                 "A newer filing fails the filter", "Quality/growth screens using as-filed XBRL data", "EQUAL", _quality_growth),
        Strategy("PEAD_SUE", "FUNDAMENTAL", "Post-earnings drift (earnings surprise)",
                 "The day a quarterly/annual report becomes public with a standardized earnings surprise ≥ 1 "
                 "(EPS vs the same quarter last year, scaled by its usual variation)", "After 60 trading days",
                 "Post-earnings-announcement drift (Bernard & Thomas 1989)", "SLEEVE", _pead, {"sueThreshold": 1.0, "holdDays": 60}),
        Strategy("VALUE_EY", "FUNDAMENTAL", "Value: highest earnings yield (top 5)",
                 "Monthly: buy the 5 stocks with the highest positive earnings yield (net income of the last 4 reported "
                 "quarters / market cap)", "Sold at the next monthly rebalance if no longer in the top 5",
                 "Value investing (Basu 1977; Fama & French 1992)", "EQUAL", _value_ey, {"top": 5, "rebalance": "monthly"}),
        Strategy("GROSS_PROFIT", "FUNDAMENTAL", "Profitability: gross profit / assets (top 5)",
                 "Monthly: buy the 5 stocks with the highest gross profit (last 4 quarters) per dollar of assets",
                 "Sold at the next monthly rebalance if no longer in the top 5",
                 "Gross profitability premium (Novy-Marx 2013)", "EQUAL", _gross_profitability, {"top": 5, "rebalance": "monthly"}),
        Strategy("DIV_YIELD", "FUNDAMENTAL", "Dividend yield (top 5)",
                 "Monthly: buy the 5 stocks with the highest dividend yield (cash dividends with an ex-date in the last "
                 "12 months / close)", "Sold at the next monthly rebalance if no longer in the top 5",
                 "High-dividend-yield investing (dividend yield and returns: Litzenberger & Ramaswamy 1979)",
                 "EQUAL", _dividend_yield, {"top": 5, "rebalance": "monthly"}),
        Strategy("DOUBLER_SCREEN", "SPECULATIVE", "Doubler screen (volatile small cap on a trigger)",
                 "Close ≤ $20 and market cap ≤ $2B (if filed), 60-day volatility in the top 30% of the universe, and a trigger: "
                 "close within 2% of the prior 252-day high, or volume ≥ 2x its 20-day average",
                 "After 63 trading days, or when the close falls 50% below its high since entry",
                 "The profile of past short-period doublers (doubler study); a lottery-ticket rule, kept to measure its cost",
                 "SLEEVE", _doubler_screen, {"maxPrice": 20, "maxMarketCap": 2e9, "volRankMin": 0.7, "breakoutDays": 252,
                                             "volumeSpike": 2.0, "holdDays": 63}, trailing_stop=0.5),
        Strategy("EVENT_AVOID", "EVENT", "Avoid tariff / rate-shock stocks",
                 "Own every member", "Step aside while a recent official tariff or rate decision hits the company "
                 "(trade shock < −0.03 or rate shock < −0.01); return once it fades",
                 "Event-driven risk avoidance using SEC-filing exposures", "SLEEVE", _event_avoid,
                 {"tradeThreshold": 0.03, "rateThreshold": 0.01}),
    ]


def rule_votes(panel: MarketPanel, idx: int, strategies: list[Strategy]) -> dict[int, dict[str, bool]]:
    """What each non-benchmark rule says about every company at calendar index idx (in = would hold)."""
    out: dict[int, dict[str, bool]] = {c: {} for c in panel.company_ids}
    for s in strategies:
        if s.family == "BENCHMARK" or s.assets != "STOCKS":
            continue
        w = s.weights(panel)
        for c in panel.company_ids:
            out[c][s.key] = bool(w.iat[idx, w.columns.get_loc(c)] > 0)
    return out
