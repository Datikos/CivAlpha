"""Synthetic demonstration dataset generator.

EVERYTHING produced here is synthetic. Prices, filings, XBRL facts, events and macro series are
generated so the full pipeline (CSV import -> SEC parsing -> exposure mapping -> point-in-time
features -> baseline/augmented models -> walk-forward evaluation) can run without network access
or licensed data. Company names and CIKs are real public identifiers; every number attached to
them here is invented.

The generator deliberately plants two effects so the pipeline can be verified end to end:
  * after a tariff event, companies exposed to the targeted countries/products drift below their
    sector benchmark for ~30 trading days;
  * after a policy-rate change, leveraged companies drift (rate hikes hurt, cuts help).
Accuracy metrics computed on this data therefore say nothing about real markets.

Output layout (mirrors SEC URL paths so the backend's fixture SEC source can map URLs to files):
  <out>/prices.csv, <out>/corporate_actions.csv
  <out>/sec/files/company_tickers.json
  <out>/sec/submissions/CIK##########.json
  <out>/sec/companyfacts/CIK##########.json
  <out>/sec/Archives/edgar/data/<cik>/<acc-no-dashes>/<doc>
  <out>/events/events.json, <out>/events/docs/*.html
  <out>/macro/series.json, <out>/macro/observations.csv
  <out>/MANIFEST.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260930
START = date(2019, 1, 2)
END = date(2026, 9, 30)
DEMO_FILER_PREFIX = "9999999999"  # fake filer-agent prefix: demo accession numbers never collide with EDGAR

# --------------------------------------------------------------------------- company profiles
# geo: synthetic revenue shares by reporting member; supply: (country, phrase strength 0..1)
# lev: long-term debt / assets; rev: quarterly revenue in $bn at start; g: annual growth
PROFILES = {
    "AAPL": dict(price=157, beta=1.05, lev=0.29, rev=84.0, g=0.06, div=0.22, geo={"US": .42, "EU": .24, "CN": .19, "JP": .07, "OTHER": .08}, supply=[("CN", 1.0), ("VN", .5), ("IN", .4)]),
    "MSFT": dict(price=101, beta=0.95, lev=0.20, rev=32.0, g=0.13, div=0.46, geo={"US": .51, "OTHER": .49}, supply=[]),
    "NVDA": dict(price=34, beta=1.6, lev=0.15, rev=2.2, g=0.55, div=0.04, split=("2024-06-10", 10), geo={"US": .44, "TW": .22, "CN": .17, "OTHER": .17}, supply=[("TW", 1.0)]),
    "AVGO": dict(price=25, beta=1.2, lev=0.45, rev=5.8, g=0.20, div=0.30, split=("2024-07-15", 10), geo={"US": .22, "CN": .32, "OTHER": .46}, supply=[("TW", .8), ("CN", .3)]),
    "AMD": dict(price=18, beta=1.7, lev=0.06, rev=1.6, g=0.30, div=0.0, geo={"US": .33, "CN": .22, "JP": .12, "OTHER": .33}, supply=[("TW", 1.0), ("CN", .4), ("MY", .3)]),
    "QCOM": dict(price=57, beta=1.25, lev=0.33, rev=5.0, g=0.08, div=0.62, geo={"CN": .62, "US": .05, "OTHER": .33}, supply=[("TW", .8), ("KR", .4)]),
    "TXN": dict(price=95, beta=1.1, lev=0.30, rev=3.7, g=0.02, div=0.77, geo={"US": .30, "CN": .20, "EU": .20, "OTHER": .30}, supply=[("US", .6)]),
    "INTC": dict(price=47, beta=1.0, lev=0.20, rev=17.0, g=-0.02, div=0.31, geo={"CN": .27, "US": .25, "TW": .14, "SG": .09, "OTHER": .25}, supply=[("US", .6), ("IE", .3)]),
    "AMAT": dict(price=33, beta=1.45, lev=0.25, rev=3.7, g=0.10, div=0.21, geo={"CN": .30, "TW": .21, "KR": .17, "US": .12, "OTHER": .20}, supply=[("US", .5), ("SG", .3)]),
    "MU": dict(price=32, beta=1.5, lev=0.10, rev=7.9, g=0.04, div=0.08, geo={"US": .50, "TW": .10, "CN": .11, "OTHER": .29}, supply=[("TW", .6), ("JP", .3), ("SG", .3)]),
    "LRCX": dict(price=14, beta=1.5, lev=0.30, rev=2.5, g=0.12, div=0.11, geo={"CN": .32, "KR": .20, "TW": .17, "US": .08, "OTHER": .23}, supply=[("US", .5), ("MY", .3)]),
    "ADBE": dict(price=226, beta=1.1, lev=0.15, rev=2.6, g=0.12, div=0.0, geo={"US": .54, "EU": .26, "OTHER": .20}, supply=[]),
    "CSCO": dict(price=43, beta=0.9, lev=0.18, rev=13.0, g=0.02, div=0.35, geo={"US": .58, "EU": .25, "CN": .03, "OTHER": .14}, supply=[("CN", .4), ("MX", .5)]),
    "AMZN": dict(price=76, beta=1.2, lev=0.18, rev=60.0, g=0.15, div=0.0, split=("2022-06-06", 20), split_pre_price=True, geo={"US": .70, "EU": .17, "JP": .06, "OTHER": .07}, supply=[("CN", .5)]),
    "TSLA": dict(price=21, beta=1.9, lev=0.12, rev=5.0, g=0.35, div=0.0, split=("2022-08-25", 3), geo={"US": .48, "CN": .21, "OTHER": .31}, supply=[("CN", .6), ("US", .6)]),
    "SBUX": dict(price=63, beta=0.9, lev=0.55, rev=6.5, g=0.06, div=0.40, geo={"US": .75, "CN": .09, "OTHER": .16}, supply=[]),
    "COST": dict(price=205, beta=0.75, lev=0.10, rev=35.0, g=0.08, div=0.65, geo={"US": .73, "MX": .02, "OTHER": .25}, supply=[("CN", .3)]),
    "PEP": dict(price=110, beta=0.6, lev=0.40, rev=16.0, g=0.05, div=1.05, geo={"US": .58, "MX": .06, "EU": .16, "OTHER": .20}, supply=[]),
    "MDLZ": dict(price=40, beta=0.6, lev=0.30, rev=6.5, g=0.05, div=0.30, geo={"US": .26, "EU": .38, "OTHER": .36}, supply=[]),
    "GOOGL": dict(price=52, beta=1.1, lev=0.05, rev=36.0, g=0.15, div=0.0, split=("2022-07-18", 20), geo={"US": .47, "EU": .29, "OTHER": .24}, supply=[]),
    "META": dict(price=135, beta=1.3, lev=0.05, rev=16.0, g=0.18, div=0.0, geo={"US": .43, "EU": .23, "OTHER": .34}, supply=[]),
    "NFLX": dict(price=267, beta=1.2, lev=0.45, rev=4.5, g=0.15, div=0.0, geo={"US": .45, "EU": .32, "OTHER": .23}, supply=[]),
    "AMGN": dict(price=190, beta=0.7, lev=0.50, rev=5.9, g=0.04, div=1.45, geo={"US": .73, "EU": .14, "OTHER": .13}, supply=[("PR", .5), ("IE", .3)]),
    "HON": dict(price=132, beta=0.95, lev=0.30, rev=9.0, g=0.02, div=0.82, geo={"US": .60, "EU": .17, "CN": .06, "OTHER": .17}, supply=[("MX", .4), ("CN", .3)]),
}
INDUSTRY_PRODUCTS = {  # product keys exposed via PRODUCT event targets
    "SEMICONDUCTORS": "SEMICONDUCTORS", "SEMICONDUCTOR_EQUIPMENT": "SEMICONDUCTORS",
    "CONSUMER_ELECTRONICS": "CONSUMER_ELECTRONICS", "AUTOS": "AUTOS", "NETWORKING_HARDWARE": "CONSUMER_ELECTRONICS",
}
COUNTRY_NAMES = {"US": "the United States", "CN": "China", "EU": "Europe", "JP": "Japan", "TW": "Taiwan",
                 "MX": "Mexico", "KR": "South Korea", "SG": "Singapore", "VN": "Vietnam", "IN": "India",
                 "MY": "Malaysia", "IE": "Ireland", "PR": "Puerto Rico", "OTHER": "other countries"}
# XBRL members per reporting region: standard ISO country members where possible, custom for regions
GEO_MEMBER = {"US": "country:US", "CN": "country:CN", "JP": "country:JP", "TW": "country:TW", "MX": "country:MX",
              "KR": "country:KR", "SG": "country:SG", "EU": "civdemo:EuropeMember", "OTHER": "civdemo:RestOfWorldMember"}


def load_universe(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text())


def trading_calendar(start: date, end: date) -> list[date]:
    from pandas.tseries.holiday import USFederalHolidayCalendar
    hol = set(USFederalHolidayCalendar().holidays(start, end).date)
    days = pd.bdate_range(start, end)
    # Good Friday closes US equity markets but is not a federal holiday
    gf = {(pd.Timestamp(easter(y)) - pd.Timedelta(days=2)).date() for y in range(start.year, end.year + 1)}
    return [d.date() for d in days if d.date() not in hol and d.date() not in gf]


def easter(y: int) -> date:  # anonymous Gregorian algorithm
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


# --------------------------------------------------------------------------- events (synthetic)
FOMC = [  # (decision date, change bps) — a synthetic rate path loosely shaped like a real cycle
    ("2019-03-20", 0), ("2019-07-31", -25), ("2019-09-18", -25), ("2019-10-30", -25), ("2020-03-03", -50),
    ("2020-03-15", -100), ("2021-06-16", 0), ("2022-03-16", 25), ("2022-05-04", 50), ("2022-06-15", 75),
    ("2022-07-27", 75), ("2022-09-21", 75), ("2022-11-02", 75), ("2022-12-14", 50), ("2023-02-01", 25),
    ("2023-03-22", 25), ("2023-05-03", 25), ("2023-07-26", 25), ("2024-01-31", 0), ("2024-09-18", -50),
    ("2024-11-07", -25), ("2024-12-18", -25), ("2025-06-18", 0), ("2025-09-17", -25), ("2025-10-29", -25),
    ("2025-12-10", -25), ("2026-03-18", 0), ("2026-06-17", -25), ("2026-09-16", 25),
]
TARIFFS = [  # (date, type, severity, countries, products, rate pct, short title)
    ("2019-05-10", "TARIFF_IMPOSED", 0.6, ["CN"], [], 25, "duty increase on a list of imports from China"),
    ("2019-08-23", "TARIFF_IMPOSED", 0.5, ["CN"], ["CONSUMER_ELECTRONICS"], 15, "new duties on Chinese consumer goods"),
    ("2019-12-13", "TARIFF_REDUCED", 0.4, ["CN"], ["CONSUMER_ELECTRONICS"], 7.5, "partial suspension of China duties"),
    ("2020-08-17", "EXPORT_CONTROL", 0.4, ["CN"], ["SEMICONDUCTORS"], None, "licensing requirement for chip sales to listed Chinese entities"),
    ("2022-10-07", "EXPORT_CONTROL", 0.8, ["CN"], ["SEMICONDUCTORS"], None, "advanced semiconductor export controls on China"),
    ("2023-10-17", "EXPORT_CONTROL", 0.5, ["CN"], ["SEMICONDUCTORS"], None, "expanded chip export controls"),
    ("2024-05-14", "TARIFF_IMPOSED", 0.5, ["CN"], ["SEMICONDUCTORS", "AUTOS"], 50, "higher duties on Chinese semiconductors and EVs"),
    ("2025-02-01", "TARIFF_PROPOSED", 0.5, ["MX"], ["AUTOS"], 25, "proposed duties on imports from Mexico"),
    ("2025-04-02", "TARIFF_IMPOSED", 0.9, ["CN", "EU", "VN", "JP", "TW"], ["CONSUMER_ELECTRONICS"], 34, "broad reciprocal duties on major trading partners"),
    ("2025-04-09", "TARIFF_REDUCED", 0.6, ["EU", "VN", "JP", "TW"], [], 10, "90-day pause on reciprocal duties except China"),
    ("2025-05-12", "TARIFF_REDUCED", 0.6, ["CN"], [], 30, "temporary reduction of China duties"),
    ("2025-08-06", "TARIFF_PROPOSED", 0.5, ["TW", "KR"], ["SEMICONDUCTORS"], 100, "proposed duties on imported semiconductors"),
    ("2026-03-04", "TARIFF_IMPOSED", 0.4, ["EU"], ["STEEL"], 25, "duties on European steel and aluminum"),
    ("2026-09-22", "TARIFF_IMPOSED", 0.7, ["CN", "VN"], ["CONSUMER_ELECTRONICS"], 30, "new duties on electronics assembled in China and Vietnam"),
]
EVENT_DRIFT_DAYS = 30
TARIFF_DRIFT = 0.10   # cumulative excess drift per unit severity x exposure
RATE_DRIFT = 0.05     # cumulative excess drift per 100bp x (leverage - mean leverage) x 4


def company_trade_exposure(sym: str, industry: str, countries: list[str], products: list[str]) -> float:
    p = PROFILES[sym]
    x = sum(p["geo"].get(c, 0.0) for c in countries)
    x += 0.5 * sum(s for c, s in p["supply"] if c in countries)
    if INDUSTRY_PRODUCTS.get(industry) in products:
        x += 0.3
    return min(x, 1.0)


def tariff_sign(event_type: str) -> float:
    return {"TARIFF_REDUCED": 1.0}.get(event_type, -1.0) * (0.6 if event_type == "TARIFF_PROPOSED" else 1.0)


# --------------------------------------------------------------------------- generator
@dataclass
class Gen:
    out: Path
    universe: dict
    rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(SEED))

    def run(self) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        self.cal = trading_calendar(START, END)
        self.cal_idx = {d: i for i, d in enumerate(self.cal)}
        self.companies = self.universe["companies"]
        fundamentals = self.make_fundamentals()
        self.write_prices(fundamentals)
        self.write_sec(fundamentals)
        self.write_events()
        self.write_macro()
        manifest = {
            "generator": "civalpha_ml.demo.generate", "seed": SEED, "generated_at": datetime.now(timezone.utc).isoformat(),
            "start": START.isoformat(), "end": END.isoformat(), "synthetic": True,
            "notice": "Synthetic demonstration data. Prices, filings, events and macro values are invented. "
                      "Planted effects: post-tariff drift for exposed companies; leverage-dependent drift after rate changes.",
        }
        (self.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
        return manifest

    # ---------------------------------------------------------------- fundamentals
    def make_fundamentals(self) -> dict:
        """Quarterly fundamentals per company (calendar fiscal quarters for simplicity)."""
        quarters = pd.period_range("2017Q1", "2026Q2", freq="Q")
        fund = {}
        for c in self.companies:
            p = PROFILES[c["symbol"]]
            qg = (1 + p["g"]) ** 0.25 - 1
            rev = p["rev"] * 1e9 * (1 + p["g"]) ** -2  # back off to 2017
            rows = []
            assets = rev * 4 * 1.6
            for q in quarters:
                surprise = self.rng.normal(0, 0.03)
                rev = rev * (1 + qg + surprise) * (1 + 0.02 * math.sin(q.quarter * math.pi / 2))
                gm = 0.45 + 0.1 * self.rng.normal()
                gross = rev * min(max(gm, 0.15), 0.85)
                opinc = gross - rev * (0.18 + 0.03 * self.rng.normal())
                net = opinc * 0.82
                assets = assets * (1 + qg * 0.8) + net * 0.3
                debt = assets * p["lev"] * (1 + 0.05 * self.rng.normal())
                fdate, accepted = self.filing_dates(q)
                rows.append(dict(q=q, start=q.start_time.date(), end=q.end_time.date(), revenue=rev, gross=gross,
                                 opinc=opinc, net=net, assets=assets, liabilities=assets * (p["lev"] + 0.35),
                                 debt=debt, cash=assets * 0.12, surprise=surprise, fdate=fdate, accepted=accepted))
            fund[c["symbol"]] = rows
        return fund

    def filing_dates(self, q: pd.Period) -> tuple[date, datetime]:
        """10-Q ~30-45 days after quarter end, 10-K (Q4) ~50-65 days. Acceptance time random 10:00-23:00 UTC."""
        end = q.end_time.date()
        lag = int(self.rng.integers(50, 66)) if q.quarter == 4 else int(self.rng.integers(28, 46))
        d = end + timedelta(days=lag)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        hour = int(self.rng.integers(10, 23))
        return d, datetime(d.year, d.month, d.day, hour, int(self.rng.integers(0, 60)), tzinfo=timezone.utc)

    # ---------------------------------------------------------------- prices
    def write_prices(self, fund: dict) -> None:
        n = len(self.cal)
        mkt = self.rng.normal(0.00045, 0.011, n)
        bench_syms = list(self.universe["benchmarks"].keys())
        sector_ret = {b: 0.9 * mkt + self.rng.normal(0.0, 0.0055, n) for b in bench_syms}
        rows = []
        for b in bench_syms:
            px = 60.0 * np.cumprod(1 + sector_ret[b])
            for i, d in enumerate(self.cal):
                rows.append((b, d, px[i], int(self.rng.lognormal(16, 0.3))))
        actions = []
        mean_lev = np.mean([PROFILES[c["symbol"]]["lev"] for c in self.companies])
        self.true_drift = {}
        for c in self.companies:
            sym, p = c["symbol"], PROFILES[c["symbol"]]
            drift = np.zeros(n)
            # planted tariff effect
            for (ds, et, sev, ctry, prods, _rate, _t) in TARIFFS:
                x = company_trade_exposure(sym, c["industry"], ctry, prods)
                self._add_drift(drift, ds, tariff_sign(et) * TARIFF_DRIFT * sev * x)
            # planted monetary effect: hikes hurt leveraged firms relative to sector
            for (ds, bps) in FOMC:
                self._add_drift(drift, ds, -RATE_DRIFT * (bps / 100.0) * (p["lev"] - mean_lev) * 4)
            # small fundamentals effect: revenue surprise -> drift after the filing becomes public
            for r in fund[sym]:
                fd = r["fdate"]
                if START <= fd <= END:
                    self._add_drift(drift, fd.isoformat(), 0.4 * r["surprise"], days=40)
            self.true_drift[sym] = drift
            eps = self.rng.normal(0, 0.016, n)
            ret = p["beta"] * sector_ret[c["benchmark"]] + eps + drift - (p["beta"] - 1) * 0.0004
            div_q = p["div"]
            split_date, split_ratio = (date.fromisoformat(p["split"][0]), p["split"][1]) if "split" in p else (None, 1)
            px = p["price"] * (split_ratio if split_date else 1)
            raw = []
            for i, d in enumerate(self.cal):
                if i > 0:
                    px = px * (1 + ret[i])
                if split_date and d >= split_date and (i == 0 or self.cal[i - 1] < split_date):
                    px = px / split_ratio
                    actions.append((sym, d, "SPLIT", split_ratio, d - timedelta(days=30)))
                # quarterly dividends on the 2nd trading day of Feb/May/Aug/Nov
                if div_q > 0 and d.month in (2, 5, 8, 11) and i > 0 and self.cal[i - 1].month != d.month:
                    dps = div_q / (split_ratio if split_date and d < split_date else 1)
                    dps = round(dps * (1 + 0.04 * (d.year - 2019)), 4)
                    px = px - dps
                    actions.append((sym, d, "CASH_DIVIDEND", dps, d - timedelta(days=21)))
                raw.append(px)
            for i, d in enumerate(self.cal):
                psym = "FB" if sym == "META" and d < date(2022, 6, 9) else sym
                rows.append((psym, d, raw[i], int(self.rng.lognormal(16.5, 0.4))))
        with (self.out / "prices.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
            for sym, d, close, vol in rows:
                o = close * (1 + self.rng.normal(0, 0.004))
                hi, lo = max(o, close) * (1 + abs(self.rng.normal(0, 0.005))), min(o, close) * (1 - abs(self.rng.normal(0, 0.005)))
                w.writerow([sym, d.isoformat(), f"{o:.4f}", f"{hi:.4f}", f"{lo:.4f}", f"{close:.4f}", vol])
        with (self.out / "corporate_actions.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "ex_date", "action_type", "value", "announced_at"])
            for sym, d, t, v, ann in actions:
                w.writerow([sym, d.isoformat(), t, v, f"{ann.isoformat()}T21:00:00Z"])

    def _add_drift(self, drift: np.ndarray, ds: str, total: float, days: int = EVENT_DRIFT_DAYS) -> None:
        d = date.fromisoformat(ds)
        i0 = next((i for i, cd in enumerate(self.cal) if cd > d), None)  # effect starts the next session
        if i0 is None:
            return
        i1 = min(i0 + days, len(self.cal))
        drift[i0:i1] += total / days

    # ---------------------------------------------------------------- SEC fixtures
    def write_sec(self, fund: dict) -> None:
        sec = self.out / "sec"
        (sec / "files").mkdir(parents=True, exist_ok=True)
        (sec / "submissions").mkdir(parents=True, exist_ok=True)
        (sec / "companyfacts").mkdir(parents=True, exist_ok=True)
        tickers = {}
        for k, c in enumerate(self.companies):
            tickers[str(k)] = {"cik_str": int(c["cik"]), "ticker": c["symbol"], "title": c["name"]}
        (sec / "files" / "company_tickers.json").write_text(json.dumps(tickers))
        seq = 0
        for c in self.companies:
            sym, cik = c["symbol"], c["cik"]
            cik_int = int(cik)
            filings, facts = [], {}
            prev_rows = {}
            for r in fund[sym]:
                q = r["q"]
                fdate, accepted = r["fdate"], r["accepted"]
                if fdate < date(2018, 1, 1) or fdate > END:
                    prev_rows[q] = r
                    continue
                seq += 1
                yy = fdate.strftime("%y")
                acc = f"{DEMO_FILER_PREFIX}-{yy}-{seq:06d}"
                form = "10-K" if q.quarter == 4 else "10-Q"
                doc = f"{sym.lower()}-{q.end_time.strftime('%Y%m%d')}.htm"
                fp = "FY" if form == "10-K" else f"Q{q.quarter}"
                filings.append(dict(acc=acc, form=form, fdate=fdate, accepted=accepted, report=r["end"], doc=doc, items=""))
                prior = prev_rows.get(q - 4)
                self._facts_for_filing(facts, r, prior, acc, form, fdate, q, fp, fund[sym])
                self._write_filing_doc(c, cik_int, acc, doc, form, r, q)
                if form == "10-K":
                    self._write_instance(c, cik_int, acc, doc, r, q, fund[sym])
                # 8-K item 2.02 earnings release a few days before the periodic report
                seq += 1
                k_date = fdate - timedelta(days=int(self.rng.integers(2, 6)))
                k_acc = f"{DEMO_FILER_PREFIX}-{k_date.strftime('%y')}-{seq:06d}"
                k_doc = f"{sym.lower()}-8k-{k_date.strftime('%Y%m%d')}.htm"
                k_acc_at = datetime(k_date.year, k_date.month, k_date.day, 20, 5, tzinfo=timezone.utc)
                filings.append(dict(acc=k_acc, form="8-K", fdate=k_date, accepted=k_acc_at, report=r["end"], doc=k_doc, items="2.02,9.01"))
                self._write_8k(c, cik_int, k_acc, k_doc, r, q)
                prev_rows[q] = r
            # one amended 10-K that restates revenue (exercise revised-filing handling)
            if sym in ("INTC", "SBUX"):
                tenk = [f for f in filings if f["form"] == "10-K" and f["fdate"].year == 2024][0]
                seq += 1
                a_date = tenk["fdate"] + timedelta(days=41)
                acc = f"{DEMO_FILER_PREFIX}-{a_date.strftime('%y')}-{seq:06d}"
                r = [x for x in fund[sym] if x["end"] == tenk["report"]][0]
                fy_start = date(r["end"].year, 1, 1)
                fy_rev = sum(x["revenue"] for x in fund[sym] if x["end"].year == r["end"].year)
                restated = round(fy_rev * 0.97)
                self._add_fact(facts, "us-gaap", "Revenues", "USD", restated, fy_start, r["end"], acc, "10-K/A", a_date, r["end"].year, "FY")
                doc = f"{sym.lower()}-10ka-{a_date.strftime('%Y%m%d')}.htm"
                filings.append(dict(acc=acc, form="10-K/A", fdate=a_date,
                                    accepted=datetime(a_date.year, a_date.month, a_date.day, 21, 30, tzinfo=timezone.utc),
                                    report=r["end"], doc=doc, items=""))
                self._write_html(cik_int, acc, doc, f"{c['name']} Form 10-K/A (Amendment No. 1) — DEMO", [
                    ("Explanatory Note", f"This Amendment No. 1 restates total net revenue for fiscal {r['end'].year} to "
                                         f"${restated/1e9:.2f} billion (previously ${fy_rev/1e9:.2f} billion) following a review of "
                                         f"revenue recognition for certain distributor arrangements. [Synthetic demonstration document.]")])
            sub = {
                "cik": cik, "name": c["name"], "tickers": [sym], "exchanges": ["Nasdaq"],
                "formerNames": [], "_demo": True,
                "filings": {"recent": {
                    "accessionNumber": [f["acc"] for f in filings],
                    "filingDate": [f["fdate"].isoformat() for f in filings],
                    "reportDate": [f["report"].isoformat() for f in filings],
                    "acceptanceDateTime": [f["accepted"].strftime("%Y-%m-%dT%H:%M:%S.000Z") for f in filings],
                    "form": [f["form"] for f in filings],
                    "primaryDocument": [f["doc"] for f in filings],
                    "items": [f["items"] for f in filings],
                }, "files": []},
            }
            (sec / "submissions" / f"CIK{cik}.json").write_text(json.dumps(sub))
            cf = {"cik": cik_int, "entityName": c["name"], "_demo": True, "facts": facts}
            (sec / "companyfacts" / f"CIK{cik}.json").write_text(json.dumps(cf))

    def _add_fact(self, facts, tax, concept, unit, val, start, end, acc, form, fdate, fy, fp):
        node = facts.setdefault(tax, {}).setdefault(concept, {"label": concept, "description": "demo", "units": {}})
        e = {"end": end.isoformat(), "val": val, "accn": acc, "fy": fy, "fp": fp, "form": form, "filed": fdate.isoformat()}
        if start is not None:
            e = {"start": start.isoformat(), **e}
        node["units"].setdefault(unit, []).append(e)

    def _facts_for_filing(self, facts, r, prior, acc, form, fdate, q, fp, rows):
        fy = q.year
        dur = [("Revenues", "revenue"), ("GrossProfit", "gross"), ("OperatingIncomeLoss", "opinc"), ("NetIncomeLoss", "net")]
        inst = [("Assets", "assets"), ("Liabilities", "liabilities"), ("LongTermDebtNoncurrent", "debt"),
                ("CashAndCashEquivalentsAtCarryingValue", "cash")]
        if form == "10-K":
            year_rows = [x for x in rows if x["q"].year == q.year]
            for concept, key in dur:
                self._add_fact(facts, "us-gaap", concept, "USD", round(sum(x[key] for x in year_rows)),
                               date(q.year, 1, 1), r["end"], acc, form, fdate, fy, fp)
        else:
            for concept, key in dur:
                self._add_fact(facts, "us-gaap", concept, "USD", round(r[key]), r["start"], r["end"], acc, form, fdate, fy, fp)
                if prior is not None:  # prior-year comparative, re-reported (normally unchanged)
                    self._add_fact(facts, "us-gaap", concept, "USD", round(prior[key]), prior["start"], prior["end"], acc, form, fdate, fy, fp)
        for concept, key in inst:
            self._add_fact(facts, "us-gaap", concept, "USD", round(r[key]), None, r["end"], acc, form, fdate, fy, fp)
        self._add_fact(facts, "dei", "EntityCommonStockSharesOutstanding", "shares", int(r["assets"] / 200), None, r["end"], acc, form, fdate, fy, fp)

    def _archive_dir(self, cik_int: int, acc: str) -> Path:
        d = self.out / "sec" / "Archives" / "edgar" / "data" / str(cik_int) / acc.replace("-", "")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_html(self, cik_int, acc, doc, title, sections):
        body = "".join(f"<h2>{h}</h2>\n<p>{t}</p>\n" for h, t in sections)
        html = (f"<html><head><title>{title}</title></head><body>"
                f"<p><b>SYNTHETIC DEMONSTRATION FILING — not an SEC document.</b></p><h1>{title}</h1>\n{body}</body></html>")
        (self._archive_dir(cik_int, acc) / doc).write_text(html)

    def _write_filing_doc(self, c, cik_int, acc, doc, form, r, q):
        sym, p = c["symbol"], PROFILES[c["symbol"]]
        geo_txt = "; ".join(f"{COUNTRY_NAMES[k]} {v*100:.0f}%" for k, v in p["geo"].items())
        sections = [("Item 1. Business", f"{c['name']} operates in the {c['industry'].replace('_', ' ').lower()} industry.")]
        if form == "10-K":
            risk = []
            for ctry, s in p["supply"]:
                adj = "substantially all of" if s >= 0.9 else ("a significant portion of" if s >= 0.5 else "some of")
                risk.append(f"We rely on manufacturing partners and suppliers in {COUNTRY_NAMES[ctry]} for {adj} our products; "
                            f"tariffs, export controls or other trade restrictions affecting {COUNTRY_NAMES[ctry]} could increase our costs or disrupt supply.")
            if p["geo"].get("CN", 0) >= 0.1:
                risk.append(f"A material share of our net revenue comes from customers in China, and new tariffs or export restrictions "
                            f"between the United States and China could reduce demand for our products.")
            risk.append(f"Changes in interest rates affect the cost of our long-term debt of ${r['debt']/1e9:.1f} billion and the value of our investments.")
            sections.append(("Item 1A. Risk Factors", " ".join(risk)))
            sections.append(("Item 7. Management's Discussion and Analysis — Segment Information and Geographic Data",
                             f"Net revenue by geographic area for fiscal {q.year}: {geo_txt}. Revenue is attributed to countries based on customer location."))
            sections.append(("Item 7A. Quantitative and Qualitative Disclosures About Market Risk",
                             f"Interest rate risk: we had ${r['debt']/1e9:.1f} billion of long-term debt outstanding; a 100 basis point increase in rates "
                             f"would change annual interest expense on floating-rate obligations by an immaterial amount."))
        else:
            sections.append(("Item 2. Management's Discussion and Analysis",
                             f"Net revenue for the quarter was ${r['revenue']/1e9:.2f} billion. Geographic mix was broadly consistent with the most recent annual report."))
        self._write_html(cik_int, acc, doc, f"{c['name']} Form {form} for period ended {r['end']} — DEMO", sections)

    def _write_8k(self, c, cik_int, acc, doc, r, q):
        text = (f"Item 2.02 Results of Operations and Financial Condition. {c['name']} reported net revenue of "
                f"${r['revenue']/1e9:.2f} billion for the quarter ended {r['end']}.")
        if PROFILES[c["symbol"]]["supply"]:
            text += " Management noted that the tariff environment remains a factor in sourcing costs."
        self._write_html(cik_int, acc, doc, f"{c['name']} Form 8-K — DEMO", [("Item 2.02", text)])

    def _write_instance(self, c, cik_int, acc, doc, r, q, rows):
        """Minimal XBRL instance with geographic revenue dimensions (srt:StatementGeographicalAxis)."""
        p = PROFILES[c["symbol"]]
        year_rev = sum(x["revenue"] for x in rows if x["q"].year == q.year)
        start, end = date(q.year, 1, 1), r["end"]
        ctx = [f'<xbrli:context id="FY"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{c["cik"]}</xbrli:identifier></xbrli:entity>'
               f'<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>']
        facts = [f'<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="FY" unitRef="usd" decimals="-6">{round(year_rev)}</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>']
        for k, share in p["geo"].items():
            cid = f"FY_geo_{k}"
            ctx.append(f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{c["cik"]}</xbrli:identifier>'
                       f'<xbrli:segment><xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">{GEO_MEMBER[k]}</xbrldi:explicitMember></xbrli:segment></xbrli:entity>'
                       f'<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>')
            facts.append(f'<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="{cid}" unitRef="usd" decimals="-6">{round(year_rev*share)}</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>')
        xml = ('<?xml version="1.0" encoding="utf-8"?>\n<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
               'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:us-gaap="http://fasb.org/us-gaap/2024" '
               'xmlns:srt="http://fasb.org/srt/2024" xmlns:country="http://xbrl.sec.gov/country/2024" '
               'xmlns:civdemo="http://civalpha.local/demo" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">\n'
               '<!-- SYNTHETIC DEMONSTRATION INSTANCE -->\n'
               '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>\n'
               + "\n".join(ctx) + "\n" + "\n".join(facts) + "\n</xbrli:xbrl>\n")
        (self._archive_dir(cik_int, acc) / doc.replace(".htm", "_htm.xml")).write_text(xml)

    # ---------------------------------------------------------------- events
    def write_events(self) -> None:
        ev_dir = self.out / "events"
        (ev_dir / "docs").mkdir(parents=True, exist_ok=True)
        events = []
        rate = 2.375  # mid of target range, synthetic
        for k, (ds, bps) in enumerate(FOMC):
            rate = max(0.125, rate + bps / 100)
            d = date.fromisoformat(ds)
            verb = "raised" if bps > 0 else ("lowered" if bps < 0 else "maintained")
            title = f"[DEMO] FOMC-style statement: target range {verb}" + (f" by {abs(bps)} bp" if bps else "")
            fname = f"fomc-{ds}.html"
            dissent = int(self.rng.integers(0, 2))
            text = (f"Synthetic demonstration document. The Committee decided to {('raise' if bps > 0 else 'lower' if bps < 0 else 'maintain')} "
                    f"the target range for the policy rate{f' by {abs(bps)} basis points' if bps else ''}, to {rate-0.125:.2f}–{rate+0.125:.2f} percent. "
                    f"Voting for the action: {12-dissent} members; voting against: {dissent}.")
            (ev_dir / "docs" / fname).write_text(f"<html><body><h1>{title}</h1><p>{text}</p></body></html>")
            events.append(dict(
                category="MONETARY_POLICY", event_type="RATE_DECISION", title=title, summary=text,
                actor="Federal Open Market Committee", event_date=ds, published_at=f"{ds}T18:00:00Z",
                attributes={"rate_change_bps": bps, "target_mid_pct": round(rate, 3), "votes_for": 12 - dissent, "votes_against": dissent},
                targets=[{"target_type": "INTEREST_RATE", "target_code": "US_POLICY_RATE", "magnitude": bps}],
                sources=[{"role": "OFFICIAL_PRIMARY", "publisher": "Federal Reserve Board (demo document)", "doc": f"docs/{fname}",
                          "url_hint": "https://www.federalreserve.gov/feeds/press_monetary.xml", "title": title, "published_at": f"{ds}T18:00:00Z"}],
            ))
        actors = {"TARIFF_IMPOSED": "Office of the United States Trade Representative", "TARIFF_REDUCED": "Office of the United States Trade Representative",
                  "TARIFF_PROPOSED": "Executive Office of the President", "EXPORT_CONTROL": "Bureau of Industry and Security"}
        for k, (ds, et, sev, ctry, prods, rate_pct, short) in enumerate(TARIFFS):
            title = f"[DEMO] {short[0].upper()}{short[1:]}"
            fname = f"trade-{ds}-{k}.html"
            text = (f"Synthetic demonstration document. Notice of action: {short}. Affected trading partners: "
                    f"{', '.join(COUNTRY_NAMES[c] for c in ctry)}." + (f" Affected products: {', '.join(p.lower() for p in prods)}." if prods else "")
                    + (f" Ad valorem rate: {rate_pct}%." if rate_pct else ""))
            (ev_dir / "docs" / fname).write_text(f"<html><body><h1>{title}</h1><p>{text}</p></body></html>")
            targets = [{"target_type": "COUNTRY", "target_code": c, "magnitude": rate_pct} for c in ctry]
            targets += [{"target_type": "PRODUCT", "target_code": p, "magnitude": rate_pct} for p in prods]
            pub = f"{ds}T20:30:00Z"
            sources = [{"role": "OFFICIAL_PRIMARY", "publisher": "Federal Register (demo document)", "doc": f"docs/{fname}",
                        "url_hint": "https://www.federalregister.gov/api/v1/documents.json", "title": title, "published_at": pub}]
            events.append(dict(category="TRADE_TARIFF", event_type=et, title=title, summary=text, actor=actors[et],
                               event_date=ds, published_at=pub,
                               attributes={"severity": sev, "tariff_rate_pct": rate_pct, "direction": "RELIEF" if et == "TARIFF_REDUCED" else "RESTRICTION"},
                               targets=targets, sources=sources))
        # News reports that rediscover two existing events (must deduplicate into them) and one news-only item.
        news = []
        for ds, title in [("2025-04-02", "[DEMO] Wire report: sweeping new import duties announced on major partners"),
                          ("2026-09-22", "[DEMO] Wire report: new duties on electronics assembled in China and Vietnam")]:
            fname = f"news-{ds}.html"
            (ev_dir / "docs" / fname).write_text(f"<html><body><h1>{title}</h1><p>Synthetic news item.</p></body></html>")
            news.append(dict(category="TRADE_TARIFF", event_type="TARIFF_IMPOSED", title=title, event_date=ds,
                             published_at=f"{ds}T21:10:00Z", source={"role": "NEWS_DISCOVERY", "publisher": "Demo Newswire", "doc": f"docs/{fname}", "title": title}))
        fname = "news-2026-09-25-rumor.html"
        (ev_dir / "docs" / fname).write_text("<html><body><h1>[DEMO] Report: officials weigh duties on Mexican auto parts</h1><p>Synthetic news item; no official document.</p></body></html>")
        news.append(dict(category="TRADE_TARIFF", event_type="TARIFF_PROPOSED", title="[DEMO] Report: officials weigh duties on Mexican auto parts",
                         event_date="2026-09-25", published_at="2026-09-25T15:00:00Z",
                         targets=[{"target_type": "COUNTRY", "target_code": "MX", "magnitude": None}, {"target_type": "PRODUCT", "target_code": "AUTOS", "magnitude": None}],
                         source={"role": "NEWS_DISCOVERY", "publisher": "Demo Newswire", "doc": f"docs/{fname}", "title": "[DEMO] Report: officials weigh duties on Mexican auto parts"}))
        actors_meta = [
            {"name": "Federal Open Market Committee", "actor_type": "INSTITUTION", "authority": "Federal Reserve Act, Section 12A",
             "profile_note": "Monetary policy committee; decisions published as statements with recorded votes."},
            {"name": "Office of the United States Trade Representative", "actor_type": "INSTITUTION",
             "authority": "Trade Act of 1974, Section 301", "profile_note": "Executive-branch trade agency; actions published as notices."},
            {"name": "Executive Office of the President", "actor_type": "INSTITUTION",
             "authority": "International Emergency Economic Powers Act; Trade Expansion Act of 1962, Section 232",
             "profile_note": "Issues proclamations and executive orders on trade measures."},
            {"name": "Bureau of Industry and Security", "actor_type": "INSTITUTION", "authority": "Export Control Reform Act of 2018",
             "profile_note": "Department of Commerce bureau administering export controls."},
        ]
        (ev_dir / "events.json").write_text(json.dumps({"_demo": True, "actors": actors_meta, "events": events, "news": news}, indent=1))

    # ---------------------------------------------------------------- macro
    def write_macro(self) -> None:
        mdir = self.out / "macro"
        mdir.mkdir(parents=True, exist_ok=True)
        series = [{"series_id": "FEDFUNDS", "title": "Effective federal funds rate (DEMO, synthetic)", "units": "Percent", "frequency": "Monthly"},
                  {"series_id": "CPIAUCSL", "title": "Consumer price index (DEMO, synthetic; with revisions)", "units": "Index", "frequency": "Monthly"}]
        (mdir / "series.json").write_text(json.dumps(series, indent=1))
        rows = []
        months = pd.period_range("2018-12", "2026-08", freq="M")
        rate = 2.375
        fomc = [(date.fromisoformat(d), b) for d, b in FOMC]
        cpi = 250.0
        for m in months:
            m_end = m.end_time.date()
            for d, b in fomc:
                if m.start_time.date() <= d <= m_end:
                    rate = max(0.125, rate + b / 100)
            release = (m + 1).start_time.date() + timedelta(days=0)
            while release.weekday() >= 5:
                release += timedelta(days=1)
            rows.append(("FEDFUNDS", m.start_time.date(), round(rate - 0.04, 2), release, None))
            infl = 0.002 + (0.004 if 2021 <= m.year <= 2022 else 0) + self.rng.normal(0, 0.0015)
            cpi = cpi * (1 + infl)
            first = round(cpi * (1 + self.rng.normal(0, 0.0008)), 3)
            r1 = (m + 1).start_time.date() + timedelta(days=12)
            r2 = (m + 2).start_time.date() + timedelta(days=12)
            # initial print, revised a month later (vintage-aware storage)
            rows.append(("CPIAUCSL", m.start_time.date(), first, r1, r2 - timedelta(days=1)))
            rows.append(("CPIAUCSL", m.start_time.date(), round(cpi, 3), r2, None))
        with (mdir / "observations.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["series_id", "obs_date", "value", "realtime_start", "realtime_end"])
            for sid, od, v, rs, re in rows:
                if rs > END:
                    continue
                w.writerow([sid, od.isoformat(), v, rs.isoformat(), re.isoformat() if re and re <= END else ""])


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the synthetic CivAlpha demo dataset")
    ap.add_argument("--out", default="data/demo-generated")
    ap.add_argument("--universe", default="config/universe.yml")
    a = ap.parse_args()
    m = Gen(Path(a.out), load_universe(Path(a.universe))).run()
    print(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
