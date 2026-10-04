"""Feature generation for the baseline and the political-event-augmented model.

All features for as-of trading date t are computed from data available at close(t) (see pit.py).
Fundamental and exposure inputs only change when a filing is accepted, so they are pre-computed at each
acceptance time ("snapshots") and looked up with a right-closed search — the lookup can never return a
snapshot accepted after the as-of time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import pit
from .returns import HORIZON, excess_label, total_return_index

BASELINE_FEATURES = ["mom_21", "mom_63", "mom_126_21", "vol_63", "rev_yoy", "gm_chg", "leverage"]
EVENT_FEATURES = ["trade_shock", "rate_shock", "fedfunds_chg_x_lev"]
AUGMENTED_FEATURES = BASELINE_FEATURES + EVENT_FEATURES
FEATURES = {"BASELINE": BASELINE_FEATURES, "AUGMENTED": AUGMENTED_FEATURES}

FEATURE_LABELS = {
    "mom_21": "1-month return vs sector benchmark",
    "mom_63": "3-month return vs sector benchmark",
    "mom_126_21": "6-month return vs benchmark, skipping last month",
    "vol_63": "3-month volatility of relative returns",
    "rev_yoy": "Latest quarterly revenue growth, year over year (as filed)",
    "gm_chg": "Change in gross margin vs same quarter last year (as filed)",
    "leverage": "Long-term debt / total assets (as filed)",
    "trade_shock": "Recent tariff/trade actions x company exposure",
    "rate_shock": "Recent policy-rate decisions x relative leverage",
    "fedfunds_chg_x_lev": "3-month change in fed funds rate (vintage as published) x relative leverage",
}
FEATURE_KIND = {f: "PRICE" for f in ["mom_21", "mom_63", "mom_126_21", "vol_63"]}
FEATURE_KIND.update({f: "FUNDAMENTAL" for f in ["rev_yoy", "gm_chg", "leverage"]})
FEATURE_KIND.update({"trade_shock": "EVENT_FEATURE", "rate_shock": "EVENT_FEATURE", "fedfunds_chg_x_lev": "MACRO"})

REVENUE_CONCEPTS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]
EVENT_LOOKBACK_DAYS = 90
EVENT_DECAY_DAYS = 30.0
DEFAULT_SHARE = {"HIGH": 0.30, "MEDIUM": 0.15, "LOW": 0.05}
CONFIDENCE_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}
MIN_HISTORY = 127


# --------------------------------------------------------------------------- bundle
@dataclass
class DataBundle:
    companies: pd.DataFrame          # id, symbol, name, benchmark_symbol, industry
    calendar: pd.DatetimeIndex       # trading dates (from benchmark bars)
    tr: dict[int, np.ndarray]        # company_id -> total-return index aligned to calendar
    bench_tr: dict[str, np.ndarray]  # benchmark symbol -> total-return index
    facts: pd.DataFrame
    exposures: pd.DataFrame
    events: pd.DataFrame             # id, category, event_type, title, published_at, evidence_status, attributes
    targets: pd.DataFrame            # event_id, target_type, target_code, magnitude
    macro: pd.DataFrame
    membership: pd.DataFrame
    filings: pd.DataFrame = field(default_factory=pd.DataFrame)  # id, accession_no, form_type, accepted_at, url
    _fund_snap: dict = field(default_factory=dict)
    _expo_snap: dict = field(default_factory=dict)

    @staticmethod
    def build(companies, stock_prices, bench_prices, actions, facts, exposures, events, targets, macro,
              membership, filings=None) -> "DataBundle":
        cal = pd.DatetimeIndex(sorted(pd.to_datetime(bench_prices["trade_date"]).unique()))
        actions = actions.copy() if actions is not None else pd.DataFrame(columns=["company_id", "symbol", "ex_date", "action_type", "value"])
        if len(actions):
            actions["ex_date"] = pd.to_datetime(actions["ex_date"])
        tr = {}
        sp = stock_prices.copy()
        sp["trade_date"] = pd.to_datetime(sp["trade_date"])
        for cid, g in sp.groupby("company_id"):
            a = actions[actions["company_id"] == cid] if "company_id" in actions else actions.iloc[0:0]
            tr[int(cid)] = total_return_index(g.set_index("trade_date")["close"].astype(float), a, cal)
        bp = bench_prices.copy()
        bp["trade_date"] = pd.to_datetime(bp["trade_date"])
        btr = {}
        for sym, g in bp.groupby("symbol"):
            a = actions[(actions["symbol"] == sym) & actions["company_id"].isna()] if len(actions) else None
            btr[sym] = total_return_index(g.set_index("trade_date")["close"].astype(float), a, cal)
        return DataBundle(companies=companies, calendar=cal, tr=tr, bench_tr=btr, facts=facts, exposures=exposures,
                          events=events, targets=targets, macro=macro, membership=membership,
                          filings=filings if filings is not None else pd.DataFrame())

    # ---------------------------------------------------------------- snapshots
    def fundamentals_at(self, company_id: int, as_of: pd.Timestamp) -> dict:
        times, snaps = self._fund_snapshots(company_id)
        i = np.searchsorted(times, as_of.value, side="right") - 1 if len(times) else -1
        return snaps[i] if i >= 0 else {"rev_yoy": np.nan, "gm_chg": np.nan, "leverage": np.nan, "_facts": []}

    def _fund_snapshots(self, company_id: int):
        """Fundamentals after each acceptance time, maintained incrementally.

        Equivalent to fundamentals_from_facts(pit.facts_as_of(facts, t)) at every acceptance time t
        (verified in tests) but linear in the number of facts.
        """
        if company_id not in self._fund_snap:
            f = self.facts[self.facts["company_id"] == company_id].sort_values("accepted_at", kind="stable")
            times_ns, snaps, state = [], [], {}
            cols = ["taxonomy", "concept", "unit", "period_start", "period_end", "dims_key", "value", "accession_no", "accepted_at"]
            recs = list(f[cols].itertuples(index=False, name=None))
            i = 0
            while i < len(recs):
                t = recs[i][8]
                while i < len(recs) and recs[i][8] == t:
                    r = recs[i]
                    state[r[:6]] = r  # later acceptance replaces the earlier value of the same key
                    i += 1
                times_ns.append(pd.Timestamp(t).value)
                snaps.append(fundamentals_from_records(state.values()))
            self._fund_snap[company_id] = (np.array(times_ns, dtype=np.int64), snaps)
        return self._fund_snap[company_id]

    def exposures_at(self, company_id: int, as_of: pd.Timestamp) -> pd.DataFrame:
        if company_id not in self._expo_snap:
            e = self.exposures[self.exposures["company_id"] == company_id]
            times_ns = np.array([t.value for t in sorted(pd.to_datetime(e["available_at"].unique(), utc=True))], dtype=np.int64) if len(e) else np.array([], dtype=np.int64)
            snaps = [list(pit.exposures_as_of(e, pd.Timestamp(t, tz="UTC")).itertuples(index=False)) for t in times_ns]
            self._expo_snap[company_id] = (times_ns, snaps)
        times, snaps = self._expo_snap[company_id]
        i = np.searchsorted(times, as_of.value, side="right") - 1 if len(times) else -1
        return snaps[i] if i >= 0 else []

    def targets_by_event(self) -> dict:
        if not hasattr(self, "_targets_map"):
            m: dict = {}
            for t in self.targets.itertuples(index=False):
                m.setdefault(int(t.event_id), set()).add((t.target_type, t.target_code))
            self._targets_map = m
        return self._targets_map


# --------------------------------------------------------------------------- fundamentals
# Pure-python implementation over fact tuples (taxonomy, concept, unit, period_start, period_end,
# dims_key, value, accession_no, accepted_at): it runs once per filing acceptance per company.
def _d(x):
    if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NaT:
        return None
    return pd.Timestamp(x)


def quarterly_values_records(recs, concepts: list[str]) -> dict:
    """3-month values by period_end; Q4 derived as FY minus the three reported quarters when needed."""
    rank = {c: i for i, c in enumerate(concepts)}
    best: dict = {}
    annual: dict = {}
    for r in recs:
        if r[1] not in rank or r[5] != "":
            continue
        start, end = _d(r[3]), _d(r[4])
        if start is None:
            continue
        days = (end - start).days
        if 80 <= days <= 100:
            cur = best.get(end)
            if cur is None or rank[r[1]] < cur[0]:
                best[end] = (rank[r[1]], float(r[6]))
        elif 350 <= days <= 380:
            cur = annual.get(end)
            if cur is None or rank[r[1]] < cur[0]:
                annual[end] = (rank[r[1]], float(r[6]), start)
    out = {e: v for e, (_, v) in best.items()}
    for end, (_, val, start) in annual.items():
        if end in out:
            continue
        inside = [v for e, v in out.items() if start < e < end - pd.Timedelta(days=60)]
        if len(inside) == 3:
            out[end] = val - sum(inside)
    return dict(sorted(out.items()))


def quarterly_values(pit_facts: pd.DataFrame, concepts: list[str]) -> pd.Series:
    recs = pit_facts[["taxonomy", "concept", "unit", "period_start", "period_end", "dims_key", "value"]].itertuples(index=False, name=None)
    return pd.Series(quarterly_values_records(recs, concepts), dtype=float)


def _yoy(series: dict):
    if len(series) < 2:
        return None, None
    ends = list(series)
    last_end = ends[-1]
    prior = [e for e in ends if last_end - pd.Timedelta(days=375) <= e <= last_end - pd.Timedelta(days=355)]
    return (series[last_end], series[prior[-1]]) if prior else (series[last_end], None)


def fundamentals_from_records(recs) -> dict:
    recs = list(recs)
    rev = quarterly_values_records(recs, REVENUE_CONCEPTS)
    gross = quarterly_values_records(recs, ["GrossProfit"])
    out = {"rev_yoy": np.nan, "gm_chg": np.nan, "leverage": np.nan, "_facts": []}
    last, prior = _yoy(rev)
    if last is not None and prior:
        out["rev_yoy"] = last / prior - 1.0
    gm = {e: gross[e] / rev[e] for e in rev if e in gross and rev[e]}
    g_last, g_prior = _yoy(gm)
    if g_last is not None and g_prior is not None:
        out["gm_chg"] = g_last - g_prior
    debt, assets = {}, {}
    latest_acc: dict = {}
    for r in recs:
        end = _d(r[4])
        if r[5] == "" and _d(r[3]) is None:
            if r[1] == "LongTermDebtNoncurrent":
                debt[end] = float(r[6])
            elif r[1] == "Assets":
                assets[end] = float(r[6])
        if len(r) > 7 and (r[1] not in latest_acc or end >= latest_acc[r[1]][0]):
            latest_acc[r[1]] = (end, str(r[7]))
    common = sorted(set(debt) & set(assets))
    if common and assets[common[-1]] > 0:
        out["leverage"] = debt[common[-1]] / assets[common[-1]]
    out["_facts"] = sorted({v[1] for v in latest_acc.values()})[-3:]
    return out


def fundamentals_from_facts(pf: pd.DataFrame) -> dict:
    cols = ["taxonomy", "concept", "unit", "period_start", "period_end", "dims_key", "value", "accession_no", "accepted_at"]
    return fundamentals_from_records(pf[cols].itertuples(index=False, name=None))


# --------------------------------------------------------------------------- price features
def price_features(tr_s: np.ndarray, tr_b: np.ndarray, i: int) -> dict:
    def rel(a, b):
        if a < 0 or np.isnan(tr_s[a]) or np.isnan(tr_s[b]) or np.isnan(tr_b[a]) or np.isnan(tr_b[b]):
            return np.nan
        return math.log(tr_s[b] / tr_s[a]) - math.log(tr_b[b] / tr_b[a])
    out = {"mom_21": rel(i - 21, i), "mom_63": rel(i - 63, i), "mom_126_21": rel(i - 126, i - 21)}
    if i >= 63:
        s = np.diff(np.log(tr_s[i - 63:i + 1])) - np.diff(np.log(tr_b[i - 63:i + 1]))
        out["vol_63"] = float(np.nanstd(s) * math.sqrt(252)) if np.isfinite(s).sum() > 40 else np.nan
    else:
        out["vol_63"] = np.nan
    return out


# --------------------------------------------------------------------------- event features
def exposure_weight(row) -> float:
    share = row.share if row.share is not None and not (isinstance(row.share, float) and math.isnan(row.share)) else DEFAULT_SHARE.get(row.confidence, 0.05)
    return float(share) * CONFIDENCE_WEIGHT.get(row.confidence, 0.4)


def trade_event_sign(event_type: str, attributes: dict) -> float:
    if event_type == "TARIFF_REDUCED" or attributes.get("direction") == "RELIEF":
        return 1.0
    return -0.5 if event_type == "TARIFF_PROPOSED" else -1.0


def event_features(bundle: DataBundle, company_id: int, as_of: pd.Timestamp, rel_leverage: float,
                   with_provenance: bool = False, events: list | None = None,
                   decay_ref: pd.Timestamp | None = None) -> tuple[dict, list]:
    """Event features. `as_of` decides which events/exposures are known; decay is measured from `decay_ref`
    (the close of the as-of trading date, as in training) so a re-issue later the same evening without new
    evidence yields identical features. Events published after that close count with age 0."""
    decay_ref = as_of if decay_ref is None else decay_ref
    if events is None:
        events = list(pit.events_as_of(bundle.events, as_of, EVENT_LOOKBACK_DAYS).itertuples(index=False))
    expo = bundle.exposures_at(company_id, as_of)
    tmap = bundle.targets_by_event()
    trade, rate, prov = 0.0, 0.0, []
    for e in events:
        age = max(0.0, (decay_ref - e.published_at).total_seconds() / 86400.0)
        decay = math.exp(-age / EVENT_DECAY_DAYS)
        attrs = e.attributes or {}
        if e.category == "TRADE_TARIFF":
            keys = tmap.get(int(e.id), set())
            matched = [r for r in expo if (r.target_type, r.target_code) in keys]
            if not matched:
                continue
            w = min(1.0, sum(exposure_weight(r) for r in matched))
            contrib = trade_event_sign(e.event_type, attrs) * float(attrs.get("severity", 0.5)) * decay * w
            trade += contrib
            if with_provenance:
                prov.append({"feature": "trade_shock", "event_id": int(e.id), "title": e.title, "contribution": contrib,
                             "published_at": e.published_at.isoformat(),
                             "exposures": [{"id": int(r.id), "target": f"{r.target_type}:{r.target_code}", "basis": r.basis,
                                            "confidence": r.confidence, "share": None if pd.isna(r.share) else float(r.share),
                                            "passage_id": None if pd.isna(r.passage_id) else int(r.passage_id),
                                            "filing_id": None if pd.isna(r.filing_id) else int(r.filing_id)} for r in matched]})
        elif e.category == "MONETARY_POLICY":
            bps = float(attrs.get("rate_change_bps", 0) or 0)
            if bps == 0 or np.isnan(rel_leverage):
                continue
            contrib = -(bps / 100.0) * decay * rel_leverage
            rate += contrib
            if with_provenance:
                prov.append({"feature": "rate_shock", "event_id": int(e.id), "title": e.title, "contribution": contrib,
                             "published_at": e.published_at.isoformat(), "rate_change_bps": bps})
    return {"trade_shock": trade, "rate_shock": rate}, prov


def fedfunds_change(bundle: DataBundle, as_of: pd.Timestamp) -> float:
    s = pit.macro_as_of(bundle.macro, "FEDFUNDS", as_of)
    if len(s) < 4:
        return np.nan
    return float(s.iloc[-1] - s.iloc[-4])


# --------------------------------------------------------------------------- panel
def build_rows(bundle: DataBundle, idx: int, as_of: pd.Timestamp | None = None, company_ids=None,
               with_provenance: bool = False, with_labels: bool = True) -> list[dict]:
    """Feature rows for all universe members at calendar index `idx` (as-of = close of that day unless given)."""
    d = bundle.calendar[idx]
    as_of = pit.close_ts(d) if as_of is None else pit.to_utc(as_of)
    # Always build every member: cross-sectional references (mean leverage) must not depend on which
    # companies a caller asked for, or a targeted re-issue would see different features than training did.
    # membership is judged at the cutoff: for backtests that is the close of d; for a live issue it is "now",
    # so a stock added (or removed) today is included (or excluded) immediately
    members = pit.members_as_of(bundle.membership, pd.Timestamp(as_of.date()))
    comp = bundle.companies.set_index("id")
    rows = []
    for cid in sorted(members):
        if cid not in bundle.tr or cid not in comp.index:
            continue
        tr_s = bundle.tr[cid]
        bsym = comp.loc[cid, "benchmark_symbol"]
        tr_b = bundle.bench_tr.get(bsym)
        if tr_b is None or idx < MIN_HISTORY or np.isnan(tr_s[idx - MIN_HISTORY + 1]):
            continue
        f = price_features(tr_s, tr_b, idx)
        fund = bundle.fundamentals_at(cid, as_of)
        f.update({k: fund[k] for k in ("rev_yoy", "gm_chg", "leverage")})
        row = {"company_id": cid, "as_of_date": d, "idx": idx, "benchmark_symbol": bsym, **f,
               "_fact_accessions": fund.get("_facts", [])}
        if with_labels:
            lab = excess_label(tr_s, tr_b, idx)
            ex_exec = excess_label(tr_s, tr_b, idx + 1)  # trading simulation: enter at next close
            row.update(lab)
            row["exec_excess_return"] = ex_exec["excess_return"]
        rows.append(row)
    # cross-sectional leverage reference for rate interactions (members at this date only)
    levs = [r["leverage"] for r in rows if not np.isnan(r["leverage"])]
    mean_lev = float(np.mean(levs)) if levs else np.nan
    ff = fedfunds_change(bundle, as_of)
    ref = pit.close_ts(d)
    known = pit.events_as_of(bundle.events, as_of)
    events = list(known[known["published_at"] > ref - pd.Timedelta(days=EVENT_LOOKBACK_DAYS)].itertuples(index=False))
    if company_ids is not None:
        wanted = set(company_ids)
        rows = [r for r in rows if r["company_id"] in wanted]
    for r in rows:
        rel = r["leverage"] - mean_lev if not np.isnan(r["leverage"]) else np.nan
        ef, prov = event_features(bundle, r["company_id"], as_of, rel, with_provenance, events, decay_ref=pit.close_ts(d))
        r.update(ef)
        r["fedfunds_chg_x_lev"] = ff * rel if not (np.isnan(ff) or np.isnan(rel)) else np.nan
        r["_event_provenance"] = prov
        r["_fedfunds_chg"] = ff
        r["_mean_leverage"] = mean_lev
    return rows


def build_panel(bundle: DataBundle, sample_every: int = 7, start_idx: int = MIN_HISTORY) -> pd.DataFrame:
    rows = []
    for idx in range(start_idx, len(bundle.calendar), sample_every):
        rows.extend(build_rows(bundle, idx))
    return pd.DataFrame(rows)
