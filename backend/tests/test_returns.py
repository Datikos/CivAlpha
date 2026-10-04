import numpy as np
import pandas as pd

from civalpha.returns import excess_label, total_return_index, window_return


def test_split_and_dividend_are_applied_on_ex_date():
    cal = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]))
    closes = pd.Series([100.0, 102.0, 25.5, 25.0], index=cal)  # 4:1 split on the 4th
    actions = pd.DataFrame({"ex_date": pd.to_datetime(["2024-01-04", "2024-01-05"]), "action_type": ["SPLIT", "CASH_DIVIDEND"],
                            "value": [4.0, 0.5]})
    tr = total_return_index(closes, actions, cal)
    assert np.allclose(tr, [1.0, 1.02, 1.02 * (25.5 * 4 / 102), 1.02 * (25.5 * 4 / 102) * (25.5 / 25.5)])


def test_unadjusted_split_would_look_like_a_crash_without_actions():
    cal = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    tr = total_return_index(pd.Series([100.0, 25.0], index=cal), None, cal)
    assert tr[1] == 0.25


def test_label_uses_exactly_horizon_trading_days_and_is_relative():
    cal = pd.bdate_range("2024-01-01", periods=30)
    stock = np.linspace(1.0, 1.3, 30)
    bench = np.linspace(1.0, 1.1, 30)
    lab = excess_label(stock, bench, 0, horizon=21)
    assert abs(lab["stock_return"] - (stock[21] - 1)) < 1e-12
    assert lab["label"] is True
    late = excess_label(stock, bench, 10, horizon=21)  # window ends beyond data
    assert late["label"] is None and np.isnan(window_return(stock, 10, 21))


def test_gap_days_forward_fill_and_start_nan():
    cal = pd.bdate_range("2024-01-01", periods=5)
    closes = pd.Series([10.0, 11.0], index=[cal[1], cal[3]])
    tr = total_return_index(closes, None, cal)
    assert np.isnan(tr[0]) and tr[1] == 1.0 and tr[2] == 1.0 and abs(tr[3] - 1.1) < 1e-12
