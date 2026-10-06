"""Price-series integrity scan: the checks behind docs/research/2026-10-forecasting-polish.md, task 3.

Three flags per company, all on the data the models actually see (the total-return index of returns.py, which has splits,
dividends and spin-offs applied):
  * VOL21     annualized 21-day volatility of the total-return index above `vol_threshold` (2.0 = 200%);
  * RET       a one-day total return beyond +-`ret_threshold` (0.5 = 50%);
  * UNEXPLAINED a one-day move of the RAW close beyond the threshold with no corporate action recorded on that day:
              the signature of a split, spin-off or bad tick the price feed did not adjust for (CTVA, 2026-10-01).

Run against the database with `python -m civalpha.integrity`. A real crash also trips VOL21 and RET; UNEXPLAINED is the
one that calls for a data fix.
"""
from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd

from .features import DataBundle

VOL_THRESHOLD = 2.0
RET_THRESHOLD = 0.5
VOL_WINDOW = 21
VOL_MIN_PERIODS = 15


def vol21(tr: np.ndarray, calendar: pd.DatetimeIndex) -> pd.Series:
    """Annualized rolling 21-day standard deviation of log total returns, as strategies.ai.technical_features computes it."""
    s = pd.Series(np.diff(np.log(tr)), index=calendar[1:])
    return s.rolling(VOL_WINDOW, min_periods=VOL_MIN_PERIODS).std() * math.sqrt(252)


def scan(bundle: DataBundle, vol_threshold: float = VOL_THRESHOLD, ret_threshold: float = RET_THRESHOLD) -> pd.DataFrame:
    """One row per flag: flag, symbol, company_id, date (or first..last for VOL21), value, actions (types on that day)."""
    comp = bundle.companies.set_index("id")
    acts = bundle.actions
    rows = []

    def actions_on(cid: int, day: pd.Timestamp) -> str:
        if acts is None or len(acts) == 0:
            return ""
        a = acts[(acts["company_id"] == cid) & (pd.to_datetime(acts["ex_date"]) == day)]
        return ",".join(sorted(a["action_type"].astype(str)))

    for cid, tr in bundle.tr.items():
        sym = str(comp.loc[cid, "symbol"]) if cid in comp.index else str(cid)
        rets = pd.Series(tr[1:] / tr[:-1] - 1.0, index=bundle.calendar[1:])
        for day, r in rets[rets.abs() > ret_threshold].items():
            rows.append({"flag": "RET", "symbol": sym, "company_id": cid, "date": str(day.date()), "value": float(r),
                         "actions": actions_on(cid, day)})
        v = vol21(tr, bundle.calendar)
        hv = v[v > vol_threshold]
        if len(hv):
            rows.append({"flag": "VOL21", "symbol": sym, "company_id": cid, "date": f"{hv.index.min().date()}..{hv.index.max().date()}",
                         "value": float(hv.max()), "actions": f"{len(hv)} days"})
        raw = bundle.close.get(cid)
        if raw is not None:
            rr = pd.Series(raw[1:] / raw[:-1] - 1.0, index=bundle.calendar[1:])
            for day, r in rr[rr.abs() > ret_threshold].items():
                if not actions_on(cid, day):
                    rows.append({"flag": "UNEXPLAINED", "symbol": sym, "company_id": cid, "date": str(day.date()),
                                 "value": float(r), "actions": ""})
    out = pd.DataFrame(rows, columns=["flag", "symbol", "company_id", "date", "value", "actions"])
    return out.sort_values(["flag", "date", "symbol"]).reset_index(drop=True)


def main() -> None:
    from . import db
    from .platform.sql import engine
    bundle = db.load_bundle(engine())
    df = scan(bundle)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 1000)
    print(df.to_string(index=False) if len(df) else "no flags")
    print(f"companies scanned: {len(bundle.tr)}, calendar end: {bundle.calendar[-1].date()}", file=sys.stderr)


if __name__ == "__main__":
    main()
