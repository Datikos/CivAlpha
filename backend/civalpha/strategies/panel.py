"""Daily market panel for strategies.

Matrices are indexed by the trading calendar (rows) and company id (columns). `px` holds the total-return
index from returns.total_return_index (splits and dividends applied), so it can be used as an adjusted
price for indicators: only ratios of it are ever used. Row t of every matrix is known at close(t).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import pit
from ..dividends import daily_dividend_features
from ..earnings import DAYS_CAP, REACTION_WINDOW, TONE_VALUE, next_estimate, session_excess
from ..features import INSIDER_WINDOW, MIN_HISTORY, DataBundle, build_rows
from ..fundamentals import FUND_FEATURES, PROFILE_KEYS


@dataclass
class MarketPanel:
    bundle: DataBundle
    calendar: pd.DatetimeIndex
    px: pd.DataFrame          # total-return index per company (NaN before the first bar)
    ret: pd.DataFrame         # daily simple returns, 0 where unknown
    member: pd.DataFrame      # bool: universe member on that date and priced
    bench_px: pd.DataFrame    # each company's sector benchmark TR index (same shape as px)
    etf_px: pd.DataFrame      # benchmark ETF TR index (columns = symbols)
    etf_ret: pd.DataFrame     # daily returns of each benchmark ETF
    cash_ret: pd.Series       # daily return of cash (realized FEDFUNDS / 252, else 0)
    close: pd.DataFrame       # raw (unadjusted) close per company, forward-filled
    volume: pd.DataFrame      # shares traded per day, NaN when the provider gave none
    symbols: dict[int, str]
    names: dict[int, str]
    benchmark_of: dict[int, str]
    _features: pd.DataFrame | None = field(default=None, repr=False)
    _fund: dict | None = field(default=None, repr=False)
    _div: dict | None = field(default=None, repr=False)
    _ins: dict | None = field(default=None, repr=False)
    _earn: dict | None = field(default=None, repr=False)

    @staticmethod
    def from_bundle(bundle: DataBundle) -> "MarketPanel":
        cal = bundle.calendar
        comp = bundle.companies.set_index("id")
        cids = sorted(c for c in bundle.tr if c in comp.index and comp.loc[c, "benchmark_symbol"] in bundle.bench_tr)
        px = pd.DataFrame({c: bundle.tr[c] for c in cids}, index=cal, dtype=float)
        bench_px = pd.DataFrame({c: bundle.bench_tr[comp.loc[c, "benchmark_symbol"]] for c in cids}, index=cal, dtype=float)
        ret = px.pct_change(fill_method=None).fillna(0.0)
        etf = pd.DataFrame(bundle.bench_tr, index=cal, dtype=float)
        etf_ret = etf.pct_change(fill_method=None).fillna(0.0)
        member = pd.DataFrame(False, index=cal, columns=cids)
        for m in bundle.membership.itertuples(index=False):
            c = int(m.company_id)
            if c not in member.columns:
                continue
            lo = pd.Timestamp(m.valid_from)
            hi = pd.Timestamp(m.valid_to) if not pd.isna(m.valid_to) else None
            mask = (cal >= lo) & ((cal < hi) if hi is not None else True)
            member.loc[mask, c] = True
        member &= px.notna()
        close = pd.DataFrame({c: bundle.close.get(c, np.full(len(cal), np.nan)) for c in cids}, index=cal, dtype=float)
        volume = pd.DataFrame({c: bundle.volume.get(c, np.full(len(cal), np.nan)) for c in cids}, index=cal, dtype=float)
        return MarketPanel(bundle=bundle, calendar=cal, px=px, ret=ret, member=member, bench_px=bench_px, etf_px=etf, etf_ret=etf_ret,
                           cash_ret=_cash_returns(bundle.macro, cal), close=close, volume=volume,
                           symbols={c: str(comp.loc[c, "symbol"]) for c in cids},
                           names={c: str(comp.loc[c, "name"]) if "name" in comp.columns else str(comp.loc[c, "symbol"]) for c in cids},
                           benchmark_of={c: str(comp.loc[c, "benchmark_symbol"]) for c in cids})

    @property
    def company_ids(self) -> list[int]:
        return list(self.px.columns)

    def features(self, step: int = 1) -> pd.DataFrame:
        """Point-in-time model features (features.build_rows) for every trading day, long format.

        `step` > 1 computes every step-th day and carries values forward to the following days; that only
        ever uses earlier data, so it stays point-in-time. Cached on the panel.
        """
        if self._features is None:
            rows = []
            for idx in range(MIN_HISTORY, len(self.calendar), step):
                rows.extend(build_rows(self.bundle, idx, with_labels=False))
            f = pd.DataFrame(rows)
            if step > 1 and not f.empty:
                f = _carry_forward(f, len(self.calendar), step)
            self._features = f
        return self._features

    def use_features(self, rows: pd.DataFrame) -> None:
        """Adopt feature rows already built with features.build_rows (for example the walk-forward panel, sampled every
        7 days), so the lab's dataset can be assembled on exactly those (idx, company_id) rows without rebuilding them."""
        self._features = rows.reset_index(drop=True)

    def feature_matrix(self, name: str) -> pd.DataFrame:
        """One feature as a (date x company) matrix, NaN where not computed."""
        f = self.features()
        if f.empty:
            return pd.DataFrame(np.nan, index=self.calendar, columns=self.px.columns)
        m = f.pivot(index="idx", columns="company_id", values=name)
        m.index = self.calendar[m.index.to_numpy()]
        return m.reindex(index=self.calendar, columns=self.px.columns).astype(float)


    def fundamentals(self) -> dict[str, pd.DataFrame]:
        """Report-based metrics as filed at each close (date x company), plus 'new_filing' (a report became public)
        and 'market_cap' (latest filed share count, split-adjusted, times the raw close; NaN before any filing).

        Each day uses the latest snapshot accepted at or before close(t). Valuation uses the latest filed share
        count, adjusted for splits after its report date, times the raw close of day t. Cached on the panel.
        """
        if self._fund is None:
            self._fund = _fundamental_matrices(self)
        return self._fund

    def earnings(self) -> dict[str, pd.DataFrame]:
        """Earnings-announcement signals at each close (date x company): 'announcement' (True in the session a results 8-K
        first traded), 'reaction' (that session's excess return over the ETF, NaN elsewhere), earn_react_last, guidance_last,
        days_since_earnings, days_to_earnings_est. Cached on the panel."""
        if self._earn is None:
            self._earn = _earnings_matrices(self)
        return self._earn

    def insiders(self) -> dict[str, pd.DataFrame]:
        """Insider signals at each close (date x company), from Form 4 trades available at or before that close:
        insider_buyers_21d / insider_sellers_21d (distinct insiders with open-market purchases / sales that became public in
        the last 21 trading days), insider_net_63d (net open-market dollar value over 63 trading days / market cap; 0 when
        nothing was filed). Cached on the panel."""
        if self._ins is None:
            self._ins = _insider_matrices(self)
        return self._ins

    def dividends(self) -> dict[str, pd.DataFrame]:
        """Dividend signals at each close (date x company): div_yield and div_growth from the cash dividends with an
        ex-date on or before that day (dividends.daily_dividend_features), payout_ratio as filed. Cached on the panel."""
        if self._div is None:
            self._div = _dividend_matrices(self)
        return self._div


INSIDER_SHORT_WINDOW = 21


def _earnings_matrices(p: "MarketPanel") -> dict[str, pd.DataFrame]:
    cal = p.calendar
    cids = list(p.px.columns)
    T, N = len(cal), len(cids)
    ann = np.zeros((T, N), dtype=bool)
    react = np.full((T, N), np.nan)
    last = np.zeros((T, N))
    guide = np.zeros((T, N))
    since = np.full((T, N), float(DAYS_CAP))
    to_next = np.full((T, N), float(DAYS_CAP))
    for j, c in enumerate(cids):
        events = p.bundle.announcements(c)
        tr_s, tr_b = p.bundle.tr.get(c), p.bundle.bench_tr.get(p.benchmark_of[c])
        if not events or tr_s is None or tr_b is None:
            continue
        for i, a in enumerate(events):
            k = a.session_idx
            if k >= T:
                continue
            ann[k, j] = True
            r = session_excess(tr_s, tr_b, k)
            react[k, j] = r
            end = events[i + 1].session_idx if i + 1 < len(events) else T
            end = min(end, T)
            rng = np.arange(k, end)
            since[rng, j] = np.minimum(DAYS_CAP, rng - k)
            win = rng[rng - k <= REACTION_WINDOW]
            last[win, j] = 0.0 if np.isnan(r) else r
            guide[win, j] = TONE_VALUE.get(a.guidance, 0.0)
            nxt = next_estimate(events[:i + 1], k, cal)
            if nxt is not None:
                to_next[rng, j] = np.clip(k + nxt - rng, 0, DAYS_CAP)
    mk = lambda a: pd.DataFrame(a, index=cal, columns=cids)  # noqa: E731
    return {"announcement": mk(ann), "reaction": mk(react), "earn_react_last": mk(last), "guidance_last": mk(guide),
            "days_since_earnings": mk(since), "days_to_earnings_est": mk(to_next)}


def _insider_matrices(p: "MarketPanel") -> dict[str, pd.DataFrame]:
    cal = p.calendar
    cids = list(p.px.columns)
    T, N = len(cal), len(cids)
    close_ns = np.array([pit.close_ts(d).value for d in cal], dtype=np.int64)
    buyers = np.zeros((T, N))
    sellers = np.zeros((T, N))
    net = np.zeros((T, N))
    for j, c in enumerate(cids):
        times, value, is_buy, owners = p.bundle.insider_trades(c)
        if not len(times):
            continue
        # a trade is known at the first close at or after it became available
        at = np.searchsorted(close_ns, times, side="left")
        ok = at < T
        at, value, is_buy, owners = at[ok], value[ok], is_buy[ok], owners[ok]
        daily = np.zeros(T)
        np.add.at(daily, at, value)
        cum = np.cumsum(daily)
        net[:, j] = cum - np.concatenate([np.zeros(INSIDER_WINDOW), cum[:-INSIDER_WINDOW]])
        for t in np.unique(at):
            lo = t
            hi = min(T, t + INSIDER_SHORT_WINDOW)
            # every close from t to t + window - 1 sees this trade in its trailing window
            for s in range(lo, hi):
                w = (at > s - INSIDER_SHORT_WINDOW) & (at <= s)
                buyers[s, j] = len(set(owners[w & is_buy]))
                sellers[s, j] = len(set(owners[w & ~is_buy]))
    mcap = p.fundamentals()["market_cap"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        net_rel = np.where(mcap > 0, net / mcap, np.nan)
    return {"insider_buyers_21d": pd.DataFrame(buyers, index=cal, columns=cids),
            "insider_sellers_21d": pd.DataFrame(sellers, index=cal, columns=cids),
            "insider_net_63d": pd.DataFrame(net_rel, index=cal, columns=cids)}


def _fundamental_matrices(p: "MarketPanel") -> dict[str, pd.DataFrame]:
    cal = p.calendar
    cids = list(p.px.columns)
    T, N = len(cal), len(cids)
    cal_ns = np.array([pit.close_ts(d).value for d in cal], dtype=np.int64)
    mats = {k: np.full((T, N), np.nan) for k in [*FUND_FEATURES, "payout_ratio", "market_cap"]}
    new = np.zeros((T, N), dtype=bool)
    actions = p.bundle.actions
    for j, c in enumerate(cids):
        times, snaps = p.bundle._fund_snapshots(c)
        if not len(times):
            continue
        idx = np.searchsorted(times, cal_ns, side="right") - 1
        ok = idx >= 0
        si = idx[ok]
        vals = {k: np.array([s.get(k, np.nan) if s.get(k) is not None else np.nan for s in snaps], dtype=float)
                for k in PROFILE_KEYS if k != "shares_date"}
        for k in mats:
            if k in vals:
                mats[k][ok, j] = vals[k][si]
        mats["days_since_filing"][ok, j] = (cal_ns[ok] - times[si]) / 86_400e9
        new[1:, j] = (idx[1:] != idx[:-1]) & (idx[1:] >= 0)
        # valuation: market cap = raw close x filed shares, adjusted for splits after the shares' report date
        close = p.bundle.close.get(c)
        if close is None:
            continue
        sdates = np.array([np.datetime64(s["shares_date"]) if s.get("shares_date") is not None else np.datetime64("NaT")
                           for s in snaps], dtype="datetime64[ns]")
        sh = np.full(T, np.nan)
        sh[ok] = vals["shares"][si]
        sd = np.full(T, np.datetime64("NaT"), dtype="datetime64[ns]")
        sd[ok] = sdates[si]
        factor = np.ones(T)
        if actions is not None and len(actions):
            spl = actions[(actions["company_id"] == c) & (actions["action_type"] == "SPLIT")]
            calv = cal.values
            for ex, ratio in zip(pd.to_datetime(spl["ex_date"]).values, spl["value"].astype(float)):
                factor = np.where((calv >= ex) & (sd < ex), factor * ratio, factor)
        mcap = close * sh * factor
        mats["market_cap"][:, j] = np.where(mcap > 0, mcap, np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            mats["earnings_yield"][:, j] = np.where(mcap > 0, vals["ttm_net_income"][np.maximum(idx, 0)] / mcap, np.nan)
            mats["sales_yield"][:, j] = np.where(mcap > 0, vals["ttm_revenue"][np.maximum(idx, 0)] / mcap, np.nan)
        mats["earnings_yield"][~ok, j] = np.nan
        mats["sales_yield"][~ok, j] = np.nan
    out = {k: pd.DataFrame(v, index=cal, columns=cids) for k, v in mats.items()}
    out["new_filing"] = pd.DataFrame(new, index=cal, columns=cids)
    return out


def _dividend_matrices(p: "MarketPanel") -> dict[str, pd.DataFrame]:
    cal = p.calendar
    days = [d.date() for d in cal]
    acts = p.bundle.actions
    out = {k: pd.DataFrame(np.nan, index=cal, columns=p.px.columns) for k in ("div_yield", "div_growth")}
    for c in p.px.columns:
        close = p.bundle.close.get(c)
        if close is None:
            continue
        a = acts[(acts["company_id"] == c) & acts["action_type"].isin(["CASH_DIVIDEND", "SPLIT"])] if len(acts) else acts
        rows = [(pd.Timestamp(d).date(), t, float(v)) for d, t, v in zip(a["ex_date"], a["action_type"], a["value"])] if len(a) else []
        f = daily_dividend_features(rows, days, close)
        for k, v in f.items():
            out[k][c] = v
    out["payout_ratio"] = p.fundamentals()["payout_ratio"]
    return out


def _carry_forward(f: pd.DataFrame, n: int, step: int) -> pd.DataFrame:
    out = [f]
    for k in range(1, step):
        g = f.copy()
        g["idx"] = g["idx"] + k
        out.append(g[g["idx"] < n])
    return pd.concat(out, ignore_index=True).sort_values(["idx", "company_id"], kind="stable").reset_index(drop=True)


def _cash_returns(macro: pd.DataFrame, cal: pd.DatetimeIndex) -> pd.Series:
    """Realized cash yield: the latest published FEDFUNDS value per month, as a daily rate.

    This is a realized return that accrues to idle capital, not a model input, so using final values is fine.
    """
    zero = pd.Series(0.0, index=cal)
    if macro is None or macro.empty:
        return zero
    s = macro[macro["series_id"] == "FEDFUNDS"]
    if s.empty:
        return zero
    latest = s.sort_values(["obs_date", "realtime_start"]).groupby("obs_date").tail(1).set_index("obs_date")["value"].astype(float)
    latest.index = pd.to_datetime(latest.index)
    rate = latest.reindex(latest.index.union(cal)).sort_index().ffill().reindex(cal).fillna(0.0)
    return rate / 100.0 / 252.0

