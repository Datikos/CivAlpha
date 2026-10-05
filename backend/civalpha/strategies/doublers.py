"""Doubler study: how often a stock doubles within a short window, what those stock-days looked like beforehand,
and whether a point-in-time screen for that profile finds them more often than chance.

Target. For every member stock-day t and horizon h: the stock is bought at the next close (t+1, the platform's
execution convention) and the window runs to close(t+1+h). `max_return` is the best close in the window over the
entry price, `max_drawdown` the worst, `end_return` the last. A hit is max_return >= threshold (+100%); a loss is
max_drawdown <= -loss (-50%). Both can be true in one window: a stock can double and then halve.

Everything under `screen_features` and `screen` uses data at or before close(t) only (tests/test_doublers.py
checks this by changing all later prices); the outcomes read later prices on purpose, that is what they score.

What the study reports, per horizon:
  * base rate   hits per member stock-day over the whole history, by year, and the list of episodes (runs of
                consecutive hit-days per company, with the day the double was reached);
  * profile     median of each screen feature on hit stock-days vs all stock-days, and the hit rate by pooled quintile;
  * screen      hit rate, loss rate and the return distribution on the stock-days the screen fires, the same for
                a control group with the same volatility rank but no trigger, and the lift over the base rate with
                a bootstrap interval over blocks of dates (neighbouring days share most of their window).

A small universe of mostly large caps will show a base rate near zero: that is the honest answer and the reason the
screen's sample size is reported next to every rate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .panel import MarketPanel

STUDY_VERSION = "doublers-0.1.0"
HORIZONS = (21, 42, 63)
PROFILE_FEATURES = ["vol_60", "vol_rank", "breakout_252", "volume_ratio", "dollar_volume_20", "market_cap", "price", "ret_21", "dd_52w"]
FEATURE_LABELS = {
    "vol_60": "Realized volatility, 60 days (annualized)",
    "vol_rank": "Volatility rank in the universe that day (0-1)",
    "breakout_252": "Close vs the prior 252-day high",
    "volume_ratio": "Volume / its 20-day average",
    "dollar_volume_20": "Average dollar volume, 20 days",
    "market_cap": "Market cap (latest filed shares x close)",
    "price": "Close (unadjusted)",
    "ret_21": "21-day return",
    "dd_52w": "Drawdown from the 52-week high",
}
BLOCK = 21
N_BOOT = 1000
SEED = 11
MIN_SIGNALS = 30


@dataclass
class DoublerConfig:
    horizons: tuple = HORIZONS
    threshold: float = 1.0        # +100% at the best close in the window
    loss: float = 0.5             # -50% at the worst close in the window
    vol_window: int = 60
    vol_rank_min: float = 0.7     # top 30% of the universe by realized volatility that day
    breakout_days: int = 252
    breakout_tolerance: float = 0.02   # within 2% of the prior 252-day high, or above it
    volume_spike: float = 2.0     # today's volume at least 2x its 20-day average
    max_market_cap: float = 2e9   # unknown market cap (no filing yet) does not disqualify
    max_price: float = 20.0
    hold: int = 63                # strategy-lab entry: hold this many days...
    stop: float = 0.5             # ...unless the close falls this far below its high since entry

    def params(self) -> dict:
        d = asdict(self)
        d["horizons"] = list(self.horizons)
        return d


# --------------------------------------------------------------------------- outcomes (read later prices)
def forward_outcomes(px: pd.DataFrame, h: int, threshold: float, loss: float) -> dict[str, pd.DataFrame]:
    """Per (t, company): entry at close(t+1); best, worst and last close over t+2..t+1+h relative to the entry."""
    entry = px.shift(-1)
    rev = px.iloc[::-1]
    fwd_max = rev.rolling(h, min_periods=h).max().iloc[::-1].shift(-2)
    fwd_min = rev.rolling(h, min_periods=h).min().iloc[::-1].shift(-2)
    end = px.shift(-(h + 1))
    max_ret = fwd_max / entry - 1.0
    max_dd = fwd_min / entry - 1.0
    end_ret = end / entry - 1.0
    known = max_ret.notna() & max_dd.notna() & end_ret.notna()
    return {"max_return": max_ret, "max_drawdown": max_dd, "end_return": end_ret, "known": known,
            "hit": (max_ret >= threshold) & known, "lost": (max_dd <= -loss) & known}


# --------------------------------------------------------------------------- screen (data <= t only)
def screen_features(p: MarketPanel, cfg: DoublerConfig) -> dict[str, pd.DataFrame]:
    px = p.px
    logret = np.log(px).diff()
    vol = logret.rolling(cfg.vol_window, min_periods=max(20, cfg.vol_window * 2 // 3)).std() * np.sqrt(252)
    vol_rank = vol.where(p.member).rank(axis=1, pct=True)
    prior_high = px.shift(1).rolling(cfg.breakout_days, min_periods=60).max()
    v = p.volume
    vol_avg = v.shift(1).rolling(20, min_periods=10).mean()
    dollar = (p.close * v).rolling(20, min_periods=10).mean()
    mcap = p.fundamentals()["market_cap"]
    return {
        "vol_60": vol,
        "vol_rank": vol_rank,
        "breakout_252": px / prior_high - 1.0,
        "volume_ratio": v / vol_avg.replace(0.0, np.nan),
        "dollar_volume_20": dollar,
        "market_cap": mcap,
        "price": p.close,
        "ret_21": px / px.shift(21) - 1.0,
        "dd_52w": px / px.rolling(252, min_periods=60).max() - 1.0,
    }


def screen_conditions(f: dict[str, pd.DataFrame], cfg: DoublerConfig) -> dict[str, pd.DataFrame]:
    """The screen's parts as boolean matrices (NaN inputs count as not met, except an unknown market cap)."""
    return {
        "volatile": (f["vol_rank"] >= cfg.vol_rank_min).fillna(False),
        "breakout": (f["breakout_252"] >= -cfg.breakout_tolerance).fillna(False),
        "volumeSpike": (f["volume_ratio"] >= cfg.volume_spike).fillna(False),
        "small": ((f["market_cap"] <= cfg.max_market_cap) | f["market_cap"].isna()) & (f["price"] <= cfg.max_price).fillna(False),
    }


def screen(p: MarketPanel, cfg: DoublerConfig | None = None, feats: dict | None = None) -> pd.DataFrame:
    """True where the screen fires at close(t): volatile and small, with a breakout or a volume spike as the trigger."""
    cfg = cfg or DoublerConfig()
    c = screen_conditions(feats or screen_features(p, cfg), cfg)
    return (c["volatile"] & c["small"] & (c["breakout"] | c["volumeSpike"]) & p.member).astype(bool)


# --------------------------------------------------------------------------- statistics
def _block_bootstrap_rate(date_idx: np.ndarray, y: np.ndarray, block: int = BLOCK, n_boot: int = N_BOOT, seed: int = SEED) -> tuple[float, float]:
    """95% CI of mean(y) resampling blocks of `block` consecutive dates (rows on nearby dates overlap in time)."""
    if len(y) == 0:
        return float("nan"), float("nan")
    dates = np.unique(date_idx)
    per = pd.Series(y.astype(float)).groupby(date_idx).agg(["sum", "count"]).reindex(dates)
    s, c = per["sum"].to_numpy(), per["count"].to_numpy()
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(dates) // block)
    starts = rng.integers(0, max(1, len(dates) - block + 1), size=(n_boot, n_blocks))
    pick = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1) % len(dates)
    boots = s[pick].sum(axis=1) / c[pick].sum(axis=1)
    return float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def _rates(mask: np.ndarray, out: dict, date_idx: np.ndarray, with_ci: bool = True) -> dict:
    """Hit / loss rates and the return distribution over the stock-days selected by `mask`."""
    n = int(mask.sum())
    if n == 0:
        return {"n": 0, "hits": 0, "hitRate": None, "lossRate": None, "medianEndReturn": None, "meanEndReturn": None,
                "medianMaxReturn": None, "hitCiLow": None, "hitCiHigh": None}
    hit, lost = out["hit"][mask], out["lost"][mask]
    end, mx = out["end_return"][mask], out["max_return"][mask]
    r = {"n": n, "hits": int(hit.sum()), "hitRate": float(hit.mean()), "lossRate": float(lost.mean()),
         "medianEndReturn": float(np.median(end)), "meanEndReturn": float(np.mean(end)), "medianMaxReturn": float(np.median(mx)),
         "p10EndReturn": float(np.percentile(end, 10)), "p90EndReturn": float(np.percentile(end, 90))}
    if with_ci:
        r["hitCiLow"], r["hitCiHigh"] = _block_bootstrap_rate(date_idx[mask], hit)
    return r


def _episodes(hit: pd.DataFrame, px: pd.DataFrame, out: dict, p: MarketPanel, h: int, threshold: float,
              merge_gap: int = 5) -> list[dict]:
    """One episode per company and move: a run of hit stock-days (gaps of up to `merge_gap` days are bridged), with the
    first signal day, the entry at the next close, and when the double was reached from that entry."""
    H = hit.to_numpy(bool)
    P = px.to_numpy(float)
    cal = p.calendar
    eps = []
    for j, c in enumerate(px.columns):
        col = H[:, j]
        edges = np.flatnonzero(np.diff(np.r_[0, col.astype(int), 0]))
        runs = list(zip(edges[0::2], edges[1::2]))             # [start, end) of each run of hit days
        merged: list[list[int]] = []
        for a, b in runs:
            if merged and a - merged[-1][1] <= merge_gap:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        for start, end in merged:
            entry_i = start + 1
            entry = P[entry_i, j]
            window = P[entry_i + 1: entry_i + 1 + h, j]
            reached = int(np.argmax(window / entry - 1.0 >= threshold)) + 1 if len(window) else None
            eps.append({"companyId": int(c), "symbol": p.symbols.get(int(c), str(c)), "signalDate": str(cal[start].date()),
                        "entryDate": str(cal[entry_i].date()), "signalDays": int(col[start:end].sum()),
                        "lastSignalDate": str(cal[end - 1].date()),
                        "daysToDouble": reached, "doubledOn": str(cal[entry_i + reached].date()) if reached else None,
                        "maxReturn": float(out["max_return"].iat[start, j]), "endReturn": float(out["end_return"].iat[start, j]),
                        "maxDrawdown": float(out["max_drawdown"].iat[start, j])})
    eps.sort(key=lambda e: e["signalDate"])
    return eps


def _profile(feats: dict, hit: np.ndarray, known: np.ndarray) -> list[dict]:
    rows = []
    for name in PROFILE_FEATURES:
        x = feats[name].to_numpy(float).ravel()
        ok = known & np.isfinite(x)
        if ok.sum() < 20:
            rows.append({"feature": name, "label": FEATURE_LABELS[name], "n": int(ok.sum())})
            continue
        xs, hs = x[ok], hit[ok]
        q = np.quantile(xs, [0.2, 0.4, 0.6, 0.8])
        bins = np.searchsorted(q, xs, side="right")
        by_q = [{"quintile": k + 1, "n": int((bins == k).sum()), "hitRate": float(hs[bins == k].mean()) if (bins == k).any() else None,
                 "low": float(xs[bins == k].min()) if (bins == k).any() else None, "high": float(xs[bins == k].max()) if (bins == k).any() else None}
                for k in range(5)]
        rows.append({"feature": name, "label": FEATURE_LABELS[name], "n": int(ok.sum()),
                     "medianAll": float(np.median(xs)), "medianHits": float(np.median(xs[hs])) if hs.any() else None,
                     "byQuintile": by_q})
    return rows


# --------------------------------------------------------------------------- the study
def run_study(p: MarketPanel, cfg: DoublerConfig | None = None) -> dict:
    cfg = cfg or DoublerConfig()
    feats = screen_features(p, cfg)
    conds = screen_conditions(feats, cfg)
    fires = screen(p, cfg, feats)
    member = p.member.to_numpy(bool)
    T, N = member.shape
    date_idx = np.repeat(np.arange(T), N)
    year = np.repeat(p.calendar.year.to_numpy(), N)
    fired = fires.to_numpy(bool).ravel()
    volatile_only = (conds["volatile"] & conds["small"] & ~(conds["breakout"] | conds["volumeSpike"]) & p.member).to_numpy(bool).ravel()

    horizons: dict = {}
    for h in cfg.horizons:
        o = forward_outcomes(p.px, h, cfg.threshold, cfg.loss)
        flat = {k: v.to_numpy().ravel() for k, v in o.items()}
        known = flat["known"] & member.ravel()
        base = _rates(known, flat, date_idx, with_ci=False)
        by_year = []
        for y in sorted(set(year[known].tolist())):
            m = known & (year == y)
            r = _rates(m, flat, date_idx, with_ci=False)
            by_year.append({"year": int(y), "n": r["n"], "hits": r["hits"], "hitRate": r["hitRate"], "lossRate": r["lossRate"]})
        sig = _rates(known & fired, flat, date_idx)
        ctl = _rates(known & volatile_only, flat, date_idx)
        lift = (sig["hitRate"] / base["hitRate"]) if sig["hitRate"] is not None and base["hitRate"] else None
        hit_m = o["hit"] & p.member
        horizons[str(h)] = {
            "horizon": h, "base": base, "byYear": by_year, "screen": sig, "control": ctl, "lift": lift,
            "profile": _profile(feats, flat["hit"], known),
            "episodes": _episodes(hit_m, p.px, o, p, h, cfg.threshold),
            "companiesWithHits": sorted({p.symbols.get(int(c), str(c)) for c in hit_m.columns[hit_m.any(axis=0)]}),
            "verdict": _verdict(sig, base),
        }

    last = len(p.calendar) - 1
    today = []
    for j, c in enumerate(p.px.columns):
        if not member[last, j]:
            continue
        met = {k: bool(v.iat[last, j]) for k, v in conds.items()}
        today.append({"companyId": int(c), "symbol": p.symbols[int(c)], "name": p.names[int(c)], "fires": bool(fires.iat[last, j]),
                      "conditions": met, "features": {k: _f(feats[k].iat[last, j]) for k in PROFILE_FEATURES}})
    today.sort(key=lambda r: (not r["fires"], -(r["features"]["vol_rank"] or 0.0)))

    years = (p.calendar[-1] - p.calendar[0]).days / 365.25
    res = {"version": STUDY_VERSION, "dataCutoff": str(p.calendar[-1].date()), "start": str(p.calendar[0].date()), "years": float(years),
           "universeSize": int(member.any(axis=0).sum()), "stockDays": int(member.sum()), "config": cfg.params(),
           "featureLabels": FEATURE_LABELS, "horizons": horizons,
           "today": {"asOfDate": str(p.calendar[last].date()), "stocks": today},
           "screenRule": {"volatile": f"60-day realized volatility in the top {100 - cfg.vol_rank_min * 100:.0f}% of the universe that day",
                          "small": f"market cap ≤ {cfg.max_market_cap / 1e9:g}B (or no filing yet) and close ≤ ${cfg.max_price:g}",
                          "trigger": f"close within {cfg.breakout_tolerance * 100:g}% of the prior {cfg.breakout_days}-day high, "
                                     f"or volume ≥ {cfg.volume_spike:g}x its 20-day average"},
           "disclaimers": ["Research software, not investment advice. A doubling is rare and its mirror image, a halving, is as common "
                           "on the same stock-days; rates here are counts of history, not odds for any stock today.",
                           "Stock-days on nearby dates share most of their window: the confidence intervals resample blocks of dates, "
                           "and the episode counts are the real sample size.",
                           "The universe only holds the stocks you track: without delisted names the base rate is biased upward."]}
    res["headline"] = _headline(res)
    return res


def _f(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def _verdict(sig: dict, base: dict) -> str:
    if sig["n"] < MIN_SIGNALS:
        return f"NOT TESTABLE: the screen fired on {sig['n']} stock-days (at least {MIN_SIGNALS} needed)"
    if base["hitRate"] is None or base["hitRate"] == 0:
        return "NOT TESTABLE: no stock in the universe doubled within this horizon"
    if sig["hitCiLow"] is not None and sig["hitCiLow"] > base["hitRate"]:
        return "Finds doublers more often than chance: SUPPORTED by this history (95% interval above the base rate)"
    return "Finds doublers more often than chance: NOT supported (interval includes the base rate)"


def _headline(res: dict) -> str:
    h = res["horizons"][str(max(res["config"]["horizons"]))]
    b, s = h["base"], h["screen"]
    parts = [f"Over {res['years']:.1f} years and {res['universeSize']} stocks, a stock doubled within {h['horizon']} trading days on "
             f"{(b['hitRate'] or 0) * 100:.2f}% of stock-days ({len(h['episodes'])} episodes, {len(h['companiesWithHits'])} companies)."]
    if s["n"]:
        lift = f" (lift {h['lift']:.1f}x)" if h["lift"] is not None else ""
        parts.append(f"The screen fired on {s['n']:,} stock-days; {s['hitRate'] * 100:.1f}% of those doubled{lift} and "
                     f"{s['lossRate'] * 100:.0f}% lost half or more.")
    else:
        parts.append("The screen never fired on this universe.")
    parts.append(h["verdict"] + ".")
    return " ".join(parts)


def study(engine, cfg: DoublerConfig | None = None) -> dict:
    """Run the study on the stored data and persist it (one row per run)."""
    from .. import db
    bundle = db.load_bundle(engine)
    res = run_study(MarketPanel.from_bundle(bundle), cfg)
    res["runId"] = db.insert_doubler_study(engine, res)
    return res
