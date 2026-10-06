import numpy as np
import pandas as pd

from civalpha import integrity
from civalpha.features import DataBundle
from helpers import make_bundle


def _bundle_with_drop(action_type: str | None):
    """Company 1 loses 84% of its raw close on one day; optionally a corporate action explains it."""
    b = make_bundle(n_days=120, n_companies=2, seed=3)
    cal = b.calendar
    day = cal[80]
    closes = pd.Series(b.close[1], index=cal)
    closes.loc[cal[80]:] *= 0.16
    stock = pd.concat([pd.DataFrame({"company_id": 1, "symbol": "S1", "trade_date": cal, "close": closes.to_numpy()}),
                       pd.DataFrame({"company_id": 2, "symbol": "S2", "trade_date": cal, "close": b.close[2]})])
    bench = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": b.bench_tr["BMK"] * 100})
    actions = pd.DataFrame(columns=["company_id", "symbol", "ex_date", "action_type", "value"])
    if action_type:
        actions = pd.DataFrame([{"company_id": 1, "symbol": "S1", "ex_date": day, "action_type": action_type,
                                 "value": float(closes.iloc[79] * 0.84 * 1.0)}])
    return DataBundle.build(b.companies, stock, bench, actions, b.facts, b.exposures, b.events, b.targets, b.macro, b.membership), day


def test_unadjusted_drop_is_flagged_three_ways():
    b, day = _bundle_with_drop(None)
    df = integrity.scan(b)
    flags = set(df[df["company_id"] == 1]["flag"])
    assert flags == {"RET", "VOL21", "UNEXPLAINED"}
    assert df[(df["flag"] == "UNEXPLAINED")]["date"].tolist() == [str(day.date())]
    assert df[df["flag"] == "VOL21"]["value"].iloc[0] > 2.0
    assert df[df["company_id"] == 2].empty


def test_recorded_spin_off_clears_every_flag():
    b, day = _bundle_with_drop("SPIN_OFF")
    df = integrity.scan(b)
    assert df.empty, df.to_string()
    v = integrity.vol21(b.tr[1], b.calendar)
    assert np.nanmax(v.to_numpy()) < 1.0
