"""Earnings announcements from 8-K filings (Item 2.02, results of operations), as the market sees them.

The acceptance time of the 8-K is the moment the results became public; the 10-Q or 10-K with the same numbers follows
days or weeks later, which is why the earnings surprise in the financial-report profile lags the real event. From the
acceptance time the platform derives, point-in-time:
  * the announcement session, decided in New York time: a release accepted at or before 16:00 trades that day (or the
    next trading day if that day is not one), one accepted after 16:00 trades the next trading day; and the stock's
    excess return over its sector ETF in that session;
  * the time since the last announcement and an estimate of the next one (a year after the announcement that followed
    the same announcement last year, else 91 calendar days after the last), the catalyst calendar;
  * the guidance tone of the last release (RAISED, LOWERED, MAINTAINED, PROVIDED, NONE), extracted from the press-release
    exhibit by keyword rules (platform/earnings.py), an ESTIMATED value.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

EARNINGS_FEATURES = ["earn_react_last", "guidance_last"]
EARNINGS_LABELS = {
    "earn_react_last": "Excess return vs the sector ETF in the last earnings announcement session (0 if none in 63 days)",
    "guidance_last": "Guidance tone in the last earnings release: +1 raised, -1 lowered, 0 otherwise (keyword rule, estimate)",
    "days_since_earnings": "Trading days since the last earnings announcement (capped at 130)",
    "days_to_earnings_est": "Trading days until the next earnings announcement, estimated from past ones (capped at 130)",
}
REACTION_WINDOW = 63         # trading days an announcement reaction stays a feature
NEW_YORK = "America/New_York"
CLOSE_LOCAL = pd.Timedelta(hours=16)
DAYS_CAP = 130
TONE_VALUE = {"RAISED": 1.0, "LOWERED": -1.0, "MAINTAINED": 0.0, "PROVIDED": 0.0, "NONE": 0.0, "UNKNOWN": 0.0}
RESULTS_ITEM = "2.02"


def is_results_8k(form_type: str | None, items: str | None) -> bool:
    return (form_type or "") == "8-K" and RESULTS_ITEM in {i.strip() for i in (items or "").split(",")}


@dataclass
class Announcement:
    accepted_at: pd.Timestamp     # UTC
    session_idx: int              # calendar index of the session in which the news traded (first close >= acceptance)
    filing_id: int | None = None
    guidance: str = "UNKNOWN"


def session_index(calendar: pd.DatetimeIndex, accepted_at: pd.Timestamp) -> int:
    """Calendar index of the session in which news accepted at `accepted_at` (UTC) first trades, by New York time:
    at or before 16:00 on a trading day is that day's session, later (or a non-trading day) is the next trading day.
    len(calendar) when that is after the last day on the calendar."""
    local = accepted_at.tz_convert(NEW_YORK)
    day = pd.Timestamp(local.date())
    if local - local.normalize() > CLOSE_LOCAL:
        day = day + pd.Timedelta(days=1)
    return int(np.searchsorted(calendar.values, np.datetime64(day), side="left"))


def announcements(filings: pd.DataFrame, releases: pd.DataFrame, company_id: int, calendar: pd.DatetimeIndex) -> list[Announcement]:
    """The company's results announcements in acceptance order, with their session index on `calendar` (see session_index)."""
    if filings is None or len(filings) == 0 or "items" not in filings:
        return []
    f = filings[(filings["company_id"] == company_id) & (filings["form_type"] == "8-K")]
    if f.empty:
        return []
    f = f[f["items"].apply(lambda x: is_results_8k("8-K", x)).astype(bool)].sort_values("accepted_at", kind="stable")
    tone = {}
    if releases is not None and len(releases):
        r = releases[releases["company_id"] == company_id]
        tone = dict(zip(r["filing_id"], r["guidance_tone"]))
    out = []
    for row in f.itertuples(index=False):
        t = pd.Timestamp(row.accepted_at)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        k = session_index(calendar, t)
        out.append(Announcement(t, k, int(row.id) if hasattr(row, "id") else None, tone.get(getattr(row, "id", None), "UNKNOWN")))
    return out


def session_excess(tr_s: np.ndarray, tr_b: np.ndarray, k: int) -> float:
    """Stock total return minus the ETF's from close(k-1) to close(k)."""
    if k <= 0 or k >= len(tr_s) or not (np.isfinite(tr_s[k]) and np.isfinite(tr_s[k - 1]) and np.isfinite(tr_b[k]) and np.isfinite(tr_b[k - 1])):
        return float("nan")
    return float(tr_s[k] / tr_s[k - 1] - tr_b[k] / tr_b[k - 1])


def next_estimate(past: list[Announcement], idx: int, calendar: pd.DatetimeIndex) -> int | None:
    """Trading days from idx to the estimated next announcement, never negative (0 = overdue).

    Companies report on a yearly rhythm, so the estimate is a year after the announcement that followed the same
    announcement last year: find the past announcement closest to a year before the latest (within 35 days), take the
    first one after it, add a year. Without that history, 91 days after the latest. None without any announcement."""
    if not past:
        return None
    latest = past[-1]
    year = pd.Timedelta(days=365)
    target = latest.accepted_at - year
    est = None
    prior = [a for a in past[:-1] if abs((a.accepted_at - target).days) <= 35]
    if prior:
        anchor = min(prior, key=lambda a: abs((a.accepted_at - target).days))
        after = [b for b in past if anchor.accepted_at + pd.Timedelta(days=20) < b.accepted_at < latest.accepted_at - pd.Timedelta(days=20)]
        if after:
            est = after[0].accepted_at + year
    if est is None or est < latest.accepted_at + pd.Timedelta(days=30):
        est = latest.accepted_at + pd.Timedelta(days=91)
    return max(0, trading_days_until(calendar, est) - idx)


def trading_days_until(calendar: pd.DatetimeIndex, when: pd.Timestamp) -> int:
    """Calendar index of the first trading day at or after `when`; beyond the calendar, extended with business days."""
    day = np.datetime64(when.tz_convert(NEW_YORK).date())
    k = int(np.searchsorted(calendar.values, day, side="left"))
    if k < len(calendar):
        return k
    last = calendar[-1].date()
    return len(calendar) - 1 + int(np.busday_count(last, when.tz_convert(NEW_YORK).date()))


def features_at(ann: list[Announcement], tr_s: np.ndarray, tr_b: np.ndarray, idx: int, as_of_ns: int, calendar: pd.DatetimeIndex) -> dict:
    """earn_react_last, guidance_last, days_since_earnings, days_to_earnings_est at calendar index idx (as-of close)."""
    past = [a for a in ann if a.accepted_at.value <= as_of_ns and a.session_idx <= idx]
    out = {"earn_react_last": 0.0, "guidance_last": 0.0, "days_since_earnings": float(DAYS_CAP), "days_to_earnings_est": float(DAYS_CAP)}
    if not past:
        return out
    last = past[-1]
    since = idx - last.session_idx
    out["days_since_earnings"] = float(min(DAYS_CAP, since))
    if since <= REACTION_WINDOW:
        r = session_excess(tr_s, tr_b, last.session_idx)
        out["earn_react_last"] = 0.0 if np.isnan(r) else r
        out["guidance_last"] = TONE_VALUE.get(last.guidance, 0.0)
    nxt = next_estimate(past, idx, calendar)
    out["days_to_earnings_est"] = float(max(0, min(DAYS_CAP, nxt))) if nxt is not None else float(DAYS_CAP)
    return out
