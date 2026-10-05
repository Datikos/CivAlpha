"""Signal health: does each model input still carry information, and is it fading?

For every feature the AI strategy sees (prices, filed fundamentals, policy shocks, dividends, insiders, earnings) the
study computes the information coefficient (IC): on each day, the Spearman rank correlation across the member stocks
between the feature at the close and the stock's excess return over its sector ETF from close(t+1) to close(t+1+h);
then the mean of the daily ICs within each month. Cross-sectional by day, so a market-wide move on that day cannot
masquerade as a signal (pooling days would let the benchmark's common path do exactly that). A positive IC means
higher values went with outperformance; a negative one is as useful, with the sign flipped.

Per feature: the mean monthly IC with a t-statistic over months (Newey-West standard error with HAC_LAGS lags, because a
feature window and an outcome window both straddle month ends and neighbouring months are not independent), the IC
information ratio (mean / standard deviation),
the share of months with the expected sign, and the last 12 months against the earlier months. A feature is
INFORMATIVE when its mean IC clears the Bonferroni-corrected bar for the number of features tested, SUGGESTIVE when
only |t| >= 2, otherwise NOISE; it is flagged DECAYING when the last 12 months lost the sign the full history had.
Edges decay as others find them; this is how the platform notices.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

from ..dividends import DIV_FEATURES
from .ai import AI_FEATURES, AiConfig, dataset, feature_kind, feature_label
from .panel import MarketPanel

STUDY_VERSION = "signals-0.1.0"
HORIZON = 21
MIN_STOCKS_PER_DAY = 5
MIN_DAYS_PER_MONTH = 10
MIN_MONTHS = 12
RECENT_MONTHS = 12
HAC_LAGS = 3                  # months of dependence covered by the standard error (a quarter: 63-day windows)
_N = NormalDist()


@dataclass
class SignalConfig:
    horizon: int = HORIZON
    min_stocks_per_day: int = MIN_STOCKS_PER_DAY
    min_days_per_month: int = MIN_DAYS_PER_MONTH
    min_months: int = MIN_MONTHS
    recent_months: int = RECENT_MONTHS
    hac_lags: int = HAC_LAGS

    def params(self) -> dict:
        return asdict(self)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Rank correlation; NaN when either side has no variation or fewer than 3 pairs."""
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return float("nan")
    rx = pd.Series(x[ok]).rank().to_numpy()
    ry = pd.Series(y[ok]).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def daily_ic(df: pd.DataFrame, feature: str, min_stocks: int) -> pd.Series:
    """Cross-sectional IC per calendar index: Spearman across the stocks with the feature and a known outcome that day."""
    d = df[[feature, "fwd_excess", "idx"]].dropna()
    if d.empty:
        return pd.Series(dtype=float)
    out = {}
    for idx, g in d.groupby("idx"):
        if len(g) < min_stocks:
            continue
        ic = spearman(g[feature].to_numpy(float), g["fwd_excess"].to_numpy(float))
        if np.isfinite(ic):
            out[int(idx)] = ic
    return pd.Series(out, dtype=float)


def monthly_ic(df: pd.DataFrame, feature: str, calendar: pd.DatetimeIndex, min_stocks: int, min_days: int = MIN_DAYS_PER_MONTH) -> list[dict]:
    """Mean daily cross-sectional IC per calendar month (months with fewer than `min_days` daily ICs are skipped)."""
    daily = daily_ic(df, feature, min_stocks)
    if daily.empty:
        return []
    months = calendar[daily.index.to_numpy()].to_period("M")
    out = []
    for m, g in daily.groupby(months):
        if len(g) < min_days:
            continue
        out.append({"month": str(m), "ic": float(g.mean()), "n": int(len(g))})
    return out


def hac_tstat(x: np.ndarray, lags: int) -> float:
    """t-statistic of mean(x) with a Newey-West (Bartlett-kernel) standard error over `lags` autocorrelations."""
    m = len(x)
    if m < 3:
        return float("nan")
    d = x - x.mean()
    s = float(d @ d) / m
    for l in range(1, min(lags, m - 1) + 1):
        s += 2.0 * (1.0 - l / (lags + 1)) * float(d[l:] @ d[:-l]) / m
    if s <= 0:
        return float("nan")
    return float(x.mean() / np.sqrt(s / m))


def summarize(series: list[dict], cfg: SignalConfig, z_needed: float, tests: int) -> dict:
    ics = np.array([s["ic"] for s in series], dtype=float)
    m = len(ics)
    if m < cfg.min_months:
        return {"months": m, "meanIc": float(ics.mean()) if m else None, "stdIc": None, "icIr": None, "tStat": None,
                "signHitRate": None, "recentIc": None, "earlierIc": None, "trend": None, "decaying": False,
                "grade": "NOT_TESTABLE", "verdict": f"NOT TESTABLE: {m} months with enough rows (at least {cfg.min_months} needed)"}
    mean, sd = float(ics.mean()), float(ics.std(ddof=1))
    t = hac_tstat(ics, cfg.hac_lags) if sd > 0 else float("nan")
    sign = 1.0 if mean >= 0 else -1.0
    recent = ics[-cfg.recent_months:]
    earlier = ics[:-cfg.recent_months] if m > cfg.recent_months else np.array([])
    recent_mean = float(recent.mean())
    earlier_mean = float(earlier.mean()) if len(earlier) else None
    decaying = bool(len(earlier) >= cfg.recent_months and abs(t) >= 2 and sign * recent_mean < 0)
    if np.isfinite(t) and abs(t) >= z_needed:
        grade, verdict = "INFORMATIVE", (f"Carries information: INFORMATIVE (|t| = {abs(t):.1f}, above the {z_needed:.1f} needed after "
                                         f"correcting for {tests} features)")
    elif np.isfinite(t) and abs(t) >= 2:
        grade, verdict = "SUGGESTIVE", f"SUGGESTIVE: |t| = {abs(t):.1f} is above 2 but below the {z_needed:.1f} needed after correcting for {tests} features"
    else:
        grade, verdict = "NOISE", "No measurable information: the mean monthly IC is within noise"
    if decaying:
        verdict += f"; DECAYING: the last {cfg.recent_months} months lost the sign the history had"
    return {"months": m, "meanIc": mean, "stdIc": sd, "icIr": (mean / sd) if sd > 0 else None, "tStat": float(t) if np.isfinite(t) else None,
            "signHitRate": float((np.sign(ics) == sign).mean()), "recentIc": recent_mean, "earlierIc": earlier_mean,
            "trend": (recent_mean - earlier_mean) if earlier_mean is not None else None, "decaying": decaying,
            "grade": grade, "verdict": verdict}


def run_study(p: MarketPanel, cfg: SignalConfig | None = None, features: list[str] | None = None) -> dict:
    cfg = cfg or SignalConfig()
    features = features or (AI_FEATURES + [f for f in DIV_FEATURES if f not in AI_FEATURES])
    df = dataset(p, AiConfig(horizon=cfg.horizon))
    tests = len(features)
    z_needed = float(_N.inv_cdf(1 - 0.025 / max(1, tests)))
    rows = []
    for f in features:
        if f not in df:
            continue
        series = monthly_ic(df, f, p.calendar, cfg.min_stocks_per_day, cfg.min_days_per_month)
        s = summarize(series, cfg, z_needed, tests)
        rows.append({"feature": f, "label": feature_label(f), "kind": feature_kind(f), "series": series, **s})
    order = {"INFORMATIVE": 0, "SUGGESTIVE": 1, "NOISE": 2, "NOT_TESTABLE": 3}
    rows.sort(key=lambda r: (order[r["grade"]], -(abs(r["tStat"]) if r["tStat"] is not None else -1)))
    years = (p.calendar[-1] - p.calendar[0]).days / 365.25
    member = p.member.to_numpy(bool)
    res = {"version": STUDY_VERSION, "dataCutoff": str(p.calendar[-1].date()), "start": str(p.calendar[0].date()), "years": float(years),
           "universeSize": int(member.any(axis=0).sum()), "rows": int(df["fwd_excess"].notna().sum()),
           "config": {**cfg.params(), "tests": tests, "bonferroniZ": z_needed}, "features": rows,
           "disclaimers": ["Research software, not investment advice. An IC is a count of history on the stocks you track.",
                           "Each day's IC is a correlation across the stocks tracked that day; a month averages its days, and neighbouring "
                           "months share most of their windows, so the t-statistic uses an autocorrelation-robust standard error.",
                           f"{tests} features are tested on the same history: a plain |t| of 2 is cleared by one in twenty by luck, which "
                           "is why INFORMATIVE needs the corrected bar."]}
    res["headline"] = _headline(res)
    return res


def _headline(res: dict) -> str:
    rows = res["features"]
    inf = [r for r in rows if r["grade"] == "INFORMATIVE"]
    sug = [r for r in rows if r["grade"] == "SUGGESTIVE"]
    dec = [r for r in rows if r["decaying"]]
    testable = [r for r in rows if r["grade"] != "NOT_TESTABLE"]
    parts = [f"{len(rows)} model inputs tested on {res['universeSize']} stocks over {res['years']:.1f} years ({res['rows']:,} stock-days, "
             f"{res['config']['horizon']}-day excess return over the sector ETF); {len(testable)} have enough months to judge."]
    if inf:
        best = inf[0]
        parts.append(f"Carry information after correcting for {res['config']['tests']} features: " + ", ".join(r["label"] for r in inf) +
                     f". Strongest: {best['label']} (mean monthly IC {best['meanIc']:+.3f}, t = {best['tStat']:+.1f}, right sign in "
                     f"{best['signHitRate']:.0%} of months).")
    else:
        parts.append(f"No input carries information once the test accounts for {res['config']['tests']} features.")
    if sug:
        parts.append("Suggestive but uncorrected: " + ", ".join(r["label"] for r in sug) + ".")
    if dec:
        parts.append("Decaying (lost their sign in the last 12 months): " + ", ".join(r["label"] for r in dec) + ".")
    return " ".join(parts)


def study(engine, cfg: SignalConfig | None = None) -> dict:
    """Run the study on the stored data and persist it (one row per run)."""
    from .. import db
    bundle = db.load_bundle(engine)
    res = run_study(MarketPanel.from_bundle(bundle), cfg)
    res["runId"] = db.insert_signal_study(engine, res)
    return res
