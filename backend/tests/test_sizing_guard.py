"""ADR-0007: the sizing guard. A raw-close move beyond 50% with no corporate action that day keeps the stock out of the
recorded book for 21 trading days: no entry, a holding exits; a recorded action on that day lifts it; smaller moves do
not trigger it; the live decision says why."""
import numpy as np
import pandas as pd

from civalpha.features import DataBundle
from civalpha.strategies.ai import GUARD_DAYS, AiConfig, decide_positions, guard_mask, unexplained_breaks
from civalpha.strategies.panel import MarketPanel
from civalpha.strategies.service import LabConfig, decisions_at
from helpers import make_bundle


def bundle_with(closes: np.ndarray, actions: pd.DataFrame | None = None, start: str = "2018-01-01") -> DataBundle:
    n, k = closes.shape
    cal = pd.bdate_range(start, periods=n)
    stock = pd.concat([pd.DataFrame({"company_id": c + 1, "symbol": f"S{c+1}", "trade_date": cal, "close": closes[:, c]}) for c in range(k)])
    b = make_bundle(n_days=n, n_companies=k)
    bench = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": 100 * np.cumprod(1 + np.random.default_rng(1).normal(0, 0.01, n))})
    return DataBundle.build(b.companies, stock, bench, actions, b.facts, b.exposures, b.events, b.targets, b.macro,
                            b.membership.assign(valid_from=pd.Timestamp("2017-01-01")))


def test_breaks_need_a_big_move_without_a_recorded_action():
    rng = np.random.default_rng(3)
    n = 120
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, (n, 3)), axis=0)
    closes[60:, 0] *= 0.35          # S1: -65%, nothing recorded (a missed spin-off or a crash)
    closes[60:, 1] *= 0.35          # S2: -65%, the spin-off is recorded that day
    closes[60:, 2] *= 0.60          # S3: -40%, below the threshold
    cal = pd.bdate_range("2018-01-01", periods=n)
    acts = pd.DataFrame([{"company_id": 2, "symbol": "S2", "ex_date": cal[60], "action_type": "SPIN_OFF",
                          "value": float(closes[59, 1] - closes[60, 1])}])
    p = MarketPanel.from_bundle(bundle_with(closes, acts))
    brk, move = unexplained_breaks(p)
    assert brk[1].sum() == 1 and bool(brk.at[cal[60], 1]) and move.at[cal[60], 1] < -0.6
    assert brk[2].sum() == 0 and brk[3].sum() == 0
    g = guard_mask(brk)
    assert g[1].iloc[60:60 + GUARD_DAYS].all() and not g[1].iloc[60 + GUARD_DAYS:].any() and not g[1].iloc[:60].any()


def test_a_guarded_stock_cannot_enter_and_a_holding_exits():
    cal = pd.bdate_range("2024-01-01", periods=6)
    prob = pd.DataFrame(0.70, index=cal, columns=[1, 2])
    member = pd.DataFrame(True, index=cal, columns=[1, 2])
    blocked = pd.DataFrame(False, index=cal, columns=[1, 2])
    blocked.loc[cal[2]:cal[4], 1] = True
    cfg = AiConfig(max_positions=2)
    w, a = decide_positions(prob, member, cfg, held0={1}, blocked=blocked)
    assert list(a[1]) == ["HOLD", "HOLD", "EXIT", "STAY_OUT", "STAY_OUT", "ENTER"]
    assert (w.loc[cal[2]:cal[4], 1] == 0).all() and w.at[cal[5], 1] > 0
    assert list(a[2]) == ["ENTER", "HOLD", "HOLD", "HOLD", "HOLD", "HOLD"]          # the other stock is untouched
    w0, _ = decide_positions(prob, member, cfg, held0={1})
    assert (w0[1] > 0).all()                                                         # without the guard it stays


def test_the_live_decision_says_why_a_stock_is_out():
    b = make_bundle(n_days=520, n_companies=4, seed=6)
    closes = np.column_stack([b.close[c] for c in (1, 2, 3, 4)])
    closes[-3:, 0] *= 0.3                                                            # S1 breaks 3 days before the end
    b2 = bundle_with(closes, start=str(b.calendar[0].date()))
    out = {d["companyId"]: d for d in decisions_at(b2, len(b2.calendar) - 1, LabConfig(max_positions=2), held={1})}
    g = out[1]["model"]["guard"]
    assert out[1]["action"] == "EXIT" and out[1]["weight"] == 0
    assert g["blocked"] and g["breakDate"] == str(b2.calendar[-3].date()) and g["move"] < -0.6 and g["until"] > g["breakDate"]
    assert all(out[c]["model"]["guard"] is None for c in (2, 3, 4))
