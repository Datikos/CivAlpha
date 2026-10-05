"""Setup playbook: the situations a discretionary trader waits for, each scored on what followed.

A setup is a catalyst or a technical situation that can be recognised at the close with data known then: an earnings
surprise becoming public, a dividend raise, a 52-week breakout, a golden cross, an oversold pullback, a crash, a volume
surge, an official tariff or rate shock hitting an exposed company. Each one fires on the first day its condition holds
(edge-triggered), so a situation that persists is counted once.

For every setup and horizon the study measures the stock's excess return over its sector ETF from the next close
(t+1) to t+1+h: how often the stock beat the ETF, the mean and median excess, the payoff asymmetry (average win over
average loss) and a block-bootstrap interval over dates, next to the same numbers for all stock-days (the base rate).
Because many setups and horizons are tested on the same history, a setup is SUPPORTED only when its mean excess is
more than the Bonferroni-corrected number of bootstrap standard errors above zero; a 95% interval above zero alone is
SUGGESTIVE.

The "today" section lists which setups fired in the last `fresh_days` sessions on which stocks: a scan of the playbook
for the current close. Everything in `trigger` functions uses data at or before close(t) only (tests/test_setups.py
checks this by changing all later prices); the outcomes read later prices on purpose.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Callable

import numpy as np
import pandas as pd

from .base import prior_high, prior_low, rsi, sma
from .panel import MarketPanel

STUDY_VERSION = "setups-0.1.0"
HORIZONS = (5, 21, 63)
BLOCK = 21
N_BOOT = 1000
SEED = 11
MIN_TRIGGERS = 30
FRESH_DAYS = 5
FAMILIES = ("EARNINGS", "DIVIDEND", "TREND", "REVERSAL", "VOLUME", "EVENT")
_N = NormalDist()


@dataclass
class SetupConfig:
    horizons: tuple = HORIZONS
    fresh_days: int = FRESH_DAYS
    min_triggers: int = MIN_TRIGGERS
    sue_threshold: float = 1.0
    crash_return: float = -0.20
    volume_spike: float = 2.0
    surge_move: float = 0.03
    trade_shock: float = -0.03
    rate_shock: float = -0.01

    def params(self) -> dict:
        d = asdict(self)
        d["horizons"] = list(self.horizons)
        return d


@dataclass(frozen=True)
class Setup:
    key: str
    family: str
    name: str
    trigger: str              # human-readable condition
    origin: str               # where the idea comes from
    fn: Callable[[MarketPanel, SetupConfig], pd.DataFrame]


def edge(cond: pd.DataFrame) -> pd.DataFrame:
    """True on the first day `cond` holds (NaN counts as False)."""
    c = cond.fillna(False).astype(bool)
    return c & ~c.shift(1, fill_value=False)


# --------------------------------------------------------------------------- triggers (data <= t only)
def _earnings_beat(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    f = p.fundamentals()
    return f["new_filing"] & (f["sue"] >= cfg.sue_threshold)


def _earnings_miss(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    f = p.fundamentals()
    return f["new_filing"] & (f["sue"] <= -cfg.sue_threshold)


def _revenue_beat(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    f = p.fundamentals()
    return f["new_filing"] & (f["sue_rev"] >= cfg.sue_threshold)


def _dividend_change(p: MarketPanel, up: bool) -> pd.DataFrame:
    g = p.dividends()["div_growth"]
    prev = g.shift(1)
    changed = g.notna() & prev.notna() & (g != prev)
    return changed & ((g > 0) if up else (g < 0))


def _dividend_raise(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return _dividend_change(p, True)


def _dividend_cut(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return _dividend_change(p, False)


def _breakout_52w(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(p.px > prior_high(p.px, 252))


def _new_low_52w(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(p.px < prior_low(p.px, 252))


def _golden_cross(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(sma(p.px, 50) > sma(p.px, 200))


def _death_cross(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(sma(p.px, 50) < sma(p.px, 200))


def _oversold_pullback(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge((rsi(p.px, 2) < 10.0) & (p.px > sma(p.px, 200)))


def _crash_21d(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(p.px / p.px.shift(21) - 1.0 <= cfg.crash_return)


def _volume_surge(p: MarketPanel, cfg: SetupConfig, up: bool) -> pd.DataFrame:
    avg = p.volume.shift(1).rolling(20, min_periods=20).mean()
    spike = p.volume >= cfg.volume_spike * avg
    move = p.px / p.px.shift(1) - 1.0
    return edge(spike & ((move >= cfg.surge_move) if up else (move <= -cfg.surge_move)))


def _volume_surge_up(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return _volume_surge(p, cfg, True)


def _volume_surge_down(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return _volume_surge(p, cfg, False)


def _tariff_shock(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(p.feature_matrix("trade_shock") < cfg.trade_shock)


def _rate_shock(p: MarketPanel, cfg: SetupConfig) -> pd.DataFrame:
    return edge(p.feature_matrix("rate_shock") < cfg.rate_shock)


def setups() -> list[Setup]:
    return [
        Setup("EARNINGS_BEAT", "EARNINGS", "Earnings beat becomes public",
              "The day a quarterly or annual report is accepted with a standardized earnings surprise ≥ 1 (EPS vs the same quarter a year earlier, scaled by its usual variation)",
              "Post-earnings-announcement drift (Bernard & Thomas 1989)", _earnings_beat),
        Setup("EARNINGS_MISS", "EARNINGS", "Earnings miss becomes public",
              "The day a report is accepted with a standardized earnings surprise ≤ −1", "The mirror image of the drift", _earnings_miss),
        Setup("REVENUE_BEAT", "EARNINGS", "Revenue beat becomes public",
              "The day a report is accepted with a standardized revenue surprise ≥ 1", "Revenue surprises drift like earnings surprises (Jegadeesh & Livnat 2006)", _revenue_beat),
        Setup("DIVIDEND_RAISE", "DIVIDEND", "Dividend raised",
              "The ex-date of a regular dividend above the one paid a year earlier", "Dividend changes as management signals (Lintner 1956)", _dividend_raise),
        Setup("DIVIDEND_CUT", "DIVIDEND", "Dividend cut",
              "The ex-date of a regular dividend below the one paid a year earlier (or suspended)", "Dividend cuts as a distress signal", _dividend_cut),
        Setup("BREAKOUT_52W", "TREND", "New 52-week high",
              "First close above the highest close of the previous 252 trading days", "Breakout and 52-week-high momentum (George & Hwang 2004)", _breakout_52w),
        Setup("NEW_LOW_52W", "REVERSAL", "New 52-week low",
              "First close below the lowest close of the previous 252 trading days", "Capitulation and tax-loss selling", _new_low_52w),
        Setup("GOLDEN_CROSS", "TREND", "Golden cross",
              "The day the 50-day average closes above the 200-day average", "Classic trend following", _golden_cross),
        Setup("DEATH_CROSS", "TREND", "Death cross",
              "The day the 50-day average closes below the 200-day average", "Classic trend following, the exit side", _death_cross),
        Setup("OVERSOLD_PULLBACK", "REVERSAL", "Oversold pullback in an uptrend",
              "The day RSI(2) falls below 10 while the close is above its 200-day average", "Connors short-term mean reversion", _oversold_pullback),
        Setup("CRASH_21D", "REVERSAL", "Crash: −20% in a month",
              "The day the 21-day total return first reaches −20% or worse", "Short-term reversal after sharp falls (Lehmann 1990)", _crash_21d),
        Setup("VOLUME_SURGE_UP", "VOLUME", "Volume surge on an up day",
              "Volume at least 2× its prior 20-day average with the close up 3% or more", "Volume confirms moves (Gervais, Kaniel & Mingelgrin 2001)", _volume_surge_up),
        Setup("VOLUME_SURGE_DOWN", "VOLUME", "Volume surge on a down day",
              "Volume at least 2× its prior 20-day average with the close down 3% or more", "High-volume selloffs: capitulation or the start of a trend", _volume_surge_down),
        Setup("TARIFF_SHOCK", "EVENT", "Tariff shock hits an exposed company",
              "The day the exposure-weighted tariff shock (official events, 30-day decay) first falls below −0.03", "Event-driven: SEC-filing exposures × official trade events", _tariff_shock),
        Setup("RATE_SHOCK", "EVENT", "Rate shock hits a leveraged company",
              "The day the rate shock (rate decisions × leverage relative to the universe) first falls below −0.01", "Event-driven: FOMC decisions × balance-sheet leverage", _rate_shock),
    ]


# --------------------------------------------------------------------------- outcomes (read later prices)
def forward_excess(p: MarketPanel, h: int) -> pd.DataFrame:
    """Stock total return minus its sector ETF's from close(t+1) to close(t+1+h); NaN where not known yet."""
    s = p.px.shift(-(h + 1)) / p.px.shift(-1) - 1.0
    b = p.bench_px.shift(-(h + 1)) / p.bench_px.shift(-1) - 1.0
    return s - b


# --------------------------------------------------------------------------- statistics
def block_bootstrap(date_idx: np.ndarray, y: np.ndarray, block: int = BLOCK, n_boot: int = N_BOOT, seed: int = SEED) -> tuple[float, float, float]:
    """(2.5th, 97.5th percentile, standard error) of mean(y), resampling blocks of `block` consecutive dates."""
    if len(y) == 0:
        return float("nan"), float("nan"), float("nan")
    dates = np.unique(date_idx)
    per = pd.Series(y.astype(float)).groupby(date_idx).agg(["sum", "count"]).reindex(dates)
    s, c = per["sum"].to_numpy(), per["count"].to_numpy()
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(dates) // block)
    starts = rng.integers(0, max(1, len(dates) - block + 1), size=(n_boot, n_blocks))
    pick = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1) % len(dates)
    boots = s[pick].sum(axis=1) / c[pick].sum(axis=1)
    return float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)), float(np.std(boots, ddof=1)) if n_boot > 1 else float("nan")


def bonferroni_z(tests: int, alpha: float = 0.05) -> float:
    return float(_N.inv_cdf(1 - alpha / (2 * max(1, tests))))


def _stats(ex: np.ndarray, date_idx: np.ndarray, with_ci: bool = True) -> dict:
    n = int(len(ex))
    if n == 0:
        return {"n": 0, "hitRate": None, "meanExcess": None, "medianExcess": None, "p10": None, "p90": None,
                "avgWin": None, "avgLoss": None, "payoff": None, "ciLow": None, "ciHigh": None, "stdErr": None, "z": None,
                "hitCiLow": None, "hitCiHigh": None}
    hit = ex > 0
    wins, losses = ex[hit], ex[~hit]
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(losses.mean()) if len(losses) else None
    r = {"n": n, "hitRate": float(hit.mean()), "meanExcess": float(ex.mean()), "medianExcess": float(np.median(ex)),
         "p10": float(np.percentile(ex, 10)), "p90": float(np.percentile(ex, 90)), "avgWin": avg_win, "avgLoss": avg_loss,
         "payoff": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss not in (None, 0.0) else None}
    if with_ci:
        lo, hi, se = block_bootstrap(date_idx, ex)
        r.update({"ciLow": lo, "ciHigh": hi, "stdErr": se, "z": (r["meanExcess"] / se) if se and np.isfinite(se) and se > 0 else None})
        hlo, hhi, _ = block_bootstrap(date_idx, hit.astype(float))
        r.update({"hitCiLow": hlo, "hitCiHigh": hhi})
    return r


def _verdict(s: dict, cfg: SetupConfig, z_needed: float, tests: int) -> tuple[str, str]:
    """(verdict text, grade) with grade in SUPPORTED | SUGGESTIVE | NEGATIVE | NOISE | NOT_TESTABLE."""
    if s["n"] < cfg.min_triggers:
        return f"NOT TESTABLE: fired {s['n']} times (at least {cfg.min_triggers} needed)", "NOT_TESTABLE"
    z = s.get("z")
    if z is not None and z >= z_needed:
        return f"Beats the sector ETF after it fires: SUPPORTED by this history (z = {z:.1f}, above the {z_needed:.1f} needed after correcting for {tests} tests)", "SUPPORTED"
    if s["ciLow"] is not None and s["ciLow"] > 0:
        return f"Beats the sector ETF after it fires: SUGGESTIVE (95% interval above zero, z = {z:.1f} below the {z_needed:.1f} needed after correcting for {tests} tests)", "SUGGESTIVE"
    if s["ciHigh"] is not None and s["ciHigh"] < 0:
        return "Trails the sector ETF after it fires: the 95% interval is below zero (a setup to avoid, or to fade)", "NEGATIVE"
    return "No edge over the sector ETF after it fires: the interval includes zero", "NOISE"


# --------------------------------------------------------------------------- the study
def run_study(p: MarketPanel, cfg: SetupConfig | None = None) -> dict:
    cfg = cfg or SetupConfig()
    reg = setups()
    member = p.member.to_numpy(bool)
    T, N = member.shape
    date_idx = np.repeat(np.arange(T), N)
    tests = len(reg) * len(cfg.horizons)
    z_needed = bonferroni_z(tests)
    fires = {s.key: s.fn(p, cfg).reindex(index=p.calendar, columns=p.px.columns).fillna(False).astype(bool) & p.member for s in reg}
    outcomes = {h: forward_excess(p, h) for h in cfg.horizons}
    base: dict[str, dict] = {}
    known_by_h: dict[int, np.ndarray] = {}
    ex_by_h: dict[int, np.ndarray] = {}
    for h in cfg.horizons:
        ex = outcomes[h].to_numpy(float).ravel()
        known = np.isfinite(ex) & member.ravel()
        known_by_h[h], ex_by_h[h] = known, ex
        base[str(h)] = _stats(ex[known], date_idx[known], with_ci=False)
    rows = []
    for s in reg:
        f = fires[s.key].to_numpy(bool).ravel()
        by_h = {}
        for h in cfg.horizons:
            m = f & known_by_h[h]
            st = _stats(ex_by_h[h][m], date_idx[m])
            b = base[str(h)]
            st["lift"] = (st["hitRate"] / b["hitRate"]) if st["hitRate"] is not None and b["hitRate"] else None
            st["verdict"], st["grade"] = _verdict(st, cfg, z_needed, tests)
            by_h[str(h)] = st
        rows.append({"key": s.key, "family": s.family, "name": s.name, "trigger": s.trigger, "origin": s.origin,
                     "triggers": int(f.sum()), "horizons": by_h})

    last = T - 1
    first = max(0, last - cfg.fresh_days + 1)
    today_setups = []
    seen: set[int] = set()
    for s in reg:
        F = fires[s.key].to_numpy(bool)
        stocks = []
        for j, c in enumerate(p.px.columns):
            if not member[last, j]:
                continue
            hits = np.flatnonzero(F[first:last + 1, j])
            if len(hits):
                t = first + int(hits[-1])
                seen.add(int(c))
                stocks.append({"companyId": int(c), "symbol": p.symbols[int(c)], "name": p.names[int(c)],
                               "date": str(p.calendar[t].date()), "daysAgo": int(last - t)})
        stocks.sort(key=lambda x: (x["daysAgo"], x["symbol"]))
        today_setups.append({"key": s.key, "name": s.name, "family": s.family, "stocks": stocks})

    years = (p.calendar[-1] - p.calendar[0]).days / 365.25
    res = {"version": STUDY_VERSION, "dataCutoff": str(p.calendar[-1].date()), "start": str(p.calendar[0].date()), "years": float(years),
           "universeSize": int(member.any(axis=0).sum()), "stockDays": int(member.sum()),
           "config": {**cfg.params(), "tests": tests, "bonferroniZ": z_needed, "block": BLOCK, "nBoot": N_BOOT},
           "families": list(FAMILIES), "base": base, "setups": rows,
           "today": {"asOfDate": str(p.calendar[last].date()), "freshDays": cfg.fresh_days, "setups": today_setups,
                     "stockCount": len(seen)},
           "disclaimers": ["Research software, not investment advice. A base rate is a count of history on the stocks you track, not "
                           "the odds for any stock today.",
                           "Setups fire on nearby dates across many stocks, so the intervals resample blocks of dates; the number of "
                           "triggers is the real sample size.",
                           f"{tests} setup-horizon pairs are tested on the same history: one in twenty would clear a plain 95% interval by "
                           "luck, which is why SUPPORTED needs the corrected bar."]}
    res["headline"] = _headline(res)
    return res


def _headline(res: dict) -> str:
    h = "21" if "21" in res["base"] else next(iter(res["base"]))
    rows = res["setups"]
    supported = [(r, r["horizons"][h]) for r in rows if r["horizons"][h]["grade"] == "SUPPORTED"]
    suggestive = [r for r in rows if r["horizons"][h]["grade"] == "SUGGESTIVE"]
    negative = [r for r in rows if r["horizons"][h]["grade"] == "NEGATIVE"]
    testable = [r for r in rows if r["horizons"][h]["grade"] != "NOT_TESTABLE"]
    b = res["base"][h]
    parts = [f"{len(rows)} setups tested at {', '.join(str(x) for x in res['config']['horizons'])} trading days on {res['universeSize']} stocks over "
             f"{res['years']:.1f} years; {len(testable)} fired often enough to judge at {h} days. All stock-days beat the sector ETF "
             f"{(b['hitRate'] or 0) * 100:.0f}% of the time."]
    if supported:
        best_r, best = max(supported, key=lambda x: x[1]["z"] or 0)
        parts.append(f"Supported after correcting for {res['config']['tests']} tests: " + ", ".join(r["name"] for r, _ in supported) +
                     f". Best: {best_r['name']} fired {best['n']} times, beat the ETF {best['hitRate'] * 100:.0f}% of the time, mean "
                     f"excess {best['meanExcess'] * 100:+.1f}% over {h} days (95% CI {best['ciLow'] * 100:+.1f}% to {best['ciHigh'] * 100:+.1f}%).")
    else:
        parts.append(f"No setup beats the sector ETF once the test accounts for {res['config']['tests']} setup-horizon pairs.")
    if suggestive:
        parts.append("Suggestive but uncorrected: " + ", ".join(r["name"] for r in suggestive) + ".")
    if negative:
        parts.append("Worse than the ETF after firing: " + ", ".join(r["name"] for r in negative) + ".")
    t = res["today"]
    parts.append(f"In the last {t['freshDays']} sessions a setup fired on {t['stockCount']} tracked stocks.")
    return " ".join(parts)


def study(engine, cfg: SetupConfig | None = None) -> dict:
    """Run the study on the stored data and persist it (one row per run)."""
    from .. import db
    bundle = db.load_bundle(engine)
    res = run_study(MarketPanel.from_bundle(bundle), cfg)
    res["runId"] = db.insert_setup_study(engine, res)
    return res
