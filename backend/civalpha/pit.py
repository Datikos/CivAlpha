"""Point-in-time (PIT) selectors.

Every function here answers "what could the system have known at `as_of`?". They are pure functions over
pandas frames so the same semantics are used for training, walk-forward evaluation and live forecasts.

Availability rules
  prices          bar for trade date d is available at close(d) = d 21:00 UTC (after the US close)
  xbrl facts      accepted_at (EDGAR acceptance time) <= as_of; latest acceptance wins per fact key
  exposures       available_at (acceptance time of the supporting filing) <= as_of
  events          published_at (official publication time) <= as_of, evidence_status == OFFICIAL
  macro           ALFRED vintages: realtime_start <= as_of date <= realtime_end (null = still current)
  universe        valid_from <= as_of date < valid_to (null = open)
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pandas as pd

CLOSE_HOUR_UTC = 21  # data cutoff for a trading date: after the 16:00 New York close in both EST and EDT


def close_ts(d: date | pd.Timestamp) -> pd.Timestamp:
    d = pd.Timestamp(d).date()
    return pd.Timestamp(datetime.combine(d, time(CLOSE_HOUR_UTC, 0), tzinfo=timezone.utc))


def to_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("us")  # stored timestamps have microsecond precision


FACT_KEY = ["company_id", "taxonomy", "concept", "unit", "period_start", "period_end", "dims_key"]


def facts_as_of(facts: pd.DataFrame, as_of) -> pd.DataFrame:
    """Latest known value of every fact key, using only filings accepted on/before `as_of`.

    Adds `original_value` (first-filed value of the key, still PIT) and `revised` flag so revisions by
    amendments or later comparatives are visible without leaking later restatements.
    """
    as_of = to_utc(as_of)
    known = facts[facts["accepted_at"] <= as_of]
    if known.empty:
        return known.assign(original_value=pd.Series(dtype=float), revised=pd.Series(dtype=bool))
    known = known.sort_values("accepted_at", kind="stable")
    key = [k for k in FACT_KEY if k in known.columns]
    grouped = known.groupby(key, dropna=False, sort=False)
    latest = grouped.tail(1).copy()
    first = grouped["value"].first()
    latest = latest.join(first.rename("original_value"), on=key)
    latest["revised"] = latest["value"] != latest["original_value"]
    return latest.reset_index(drop=True)


def exposures_as_of(exposures: pd.DataFrame, as_of) -> pd.DataFrame:
    """Most recent exposure per (company, target, channel) available at `as_of`."""
    as_of = to_utc(as_of)
    known = exposures[exposures["available_at"] <= as_of].sort_values(["available_at", "id"], kind="stable")
    return known.groupby(["company_id", "target_type", "target_code", "exposure_channel"], sort=False).tail(1)


def events_as_of(events: pd.DataFrame, as_of, lookback_days: int | None = None, official_only: bool = True) -> pd.DataFrame:
    as_of = to_utc(as_of)
    m = events["published_at"] <= as_of
    if lookback_days is not None:
        m &= events["published_at"] > as_of - pd.Timedelta(days=lookback_days)
    if official_only:
        m &= events["evidence_status"] == "OFFICIAL"
    return events[m]


def macro_as_of(macro: pd.DataFrame, series_id: str, as_of) -> pd.Series:
    """The series as it was published on the as-of date (one value per observation date)."""
    d = pd.Timestamp(to_utc(as_of).date())
    s = macro[macro["series_id"] == series_id]
    m = (s["realtime_start"] <= d) & (s["realtime_end"].isna() | (s["realtime_end"] >= d))
    v = s[m].sort_values(["obs_date", "realtime_start"]).groupby("obs_date").tail(1)
    return v.set_index("obs_date")["value"].astype(float)


def members_as_of(membership: pd.DataFrame, as_of_date) -> set[int]:
    d = pd.Timestamp(as_of_date)
    m = (membership["valid_from"] <= d) & (membership["valid_to"].isna() | (membership["valid_to"] > d))
    return set(membership.loc[m, "company_id"].astype(int))


def last_trading_date_at(calendar: pd.DatetimeIndex, as_of) -> pd.Timestamp | None:
    """Latest trading date whose close is at or before `as_of`."""
    as_of = to_utc(as_of)
    cutoffs = calendar.tz_localize("UTC") + pd.Timedelta(hours=CLOSE_HOUR_UTC)
    i = cutoffs.searchsorted(as_of, side="right") - 1
    return calendar[i] if i >= 0 else None
