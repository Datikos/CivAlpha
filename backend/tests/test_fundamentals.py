"""Financial-report profile: hand-checked metrics, point-in-time by filing acceptance, split-adjusted valuation."""
import numpy as np
import pandas as pd
import pytest

from civalpha.features import DataBundle
from civalpha.fundamentals import profile_from_records
from civalpha.strategies.ai import FUND_MODEL_FEATURES, AiConfig, dataset
from civalpha.strategies.panel import MarketPanel
from civalpha.strategies.rules import rule_strategies
from helpers import fact, facts_frame, make_bundle

DIFFS = [1, 3, 1, 3, 1, 3, 1, 3, 10]      # seasonal net-income differences; the last one is the surprise


def quarter(k):
    end = pd.Timestamp("2019-03-31") + pd.offsets.QuarterEnd(k)
    start = (end - pd.offsets.QuarterBegin(startingMonth=1)).normalize()
    return start, end


def report_facts(company_id=1, n=13, accept_hour=15, shares=1000.0):
    ni = [10.0] * 4
    for d in DIFFS[: n - 4]:
        ni.append(ni[-4] + d)
    rows = []
    for k in range(n):
        start, end = quarter(k)
        acc, at = f"Q{company_id}-{k}", end + pd.Timedelta(days=35, hours=accept_hour)
        rev = 100.0 + 5 * k
        rows += [fact("Revenues", rev, start, end, acc, at, company_id),
                 fact("NetIncomeLoss", ni[k], start, end, acc, at, company_id),
                 fact("GrossProfit", rev * 0.4, start, end, acc, at, company_id),
                 fact("OperatingIncomeLoss", rev * 0.2, start, end, acc, at, company_id),
                 fact("InterestExpense", 2.0, start, end, acc, at, company_id),
                 fact("Assets", 1000.0, None, end, acc, at, company_id),
                 fact("Liabilities", 400.0, None, end, acc, at, company_id),
                 fact("CashAndCashEquivalentsAtCarryingValue", 150.0, None, end, acc, at, company_id),
                 {**fact("EntityCommonStockSharesOutstanding", shares, None, end, acc, at, company_id, unit="shares"), "taxonomy": "dei"}]
    return rows


def records(rows):
    f = facts_frame(rows)
    cols = ["taxonomy", "concept", "unit", "period_start", "period_end", "dims_key", "value", "accession_no", "accepted_at"]
    return list(f[cols].itertuples(index=False, name=None))


def test_profile_matches_hand_computed_values():
    p = profile_from_records(records(report_facts()))
    prior = np.array(DIFFS[:-1], dtype=float)
    assert p["sue"] == pytest.approx(10 / np.std(prior, ddof=1))            # 9.354
    revs = [100.0 + 5 * k for k in range(13)]
    assert p["ttm_revenue"] == pytest.approx(sum(revs[-4:]))
    assert p["gross_margin"] == pytest.approx(0.4)
    assert p["op_margin"] == pytest.approx(0.2)
    assert p["op_margin_chg"] == pytest.approx(0.0)
    assert p["gp_assets"] == pytest.approx(0.4 * sum(revs[-4:]) / 1000)
    assert p["cash_assets"] == pytest.approx(0.15) and p["liab_assets"] == pytest.approx(0.4)
    assert p["int_coverage"] == pytest.approx(0.2 * sum(revs[-4:]) / 8.0)
    g_last, g_prev = revs[12] / revs[8] - 1, revs[11] / revs[7] - 1
    assert p["rev_accel"] == pytest.approx(g_last - g_prev)
    assert p["shares"] == 1000.0
    # too little history for a surprise
    assert np.isnan(profile_from_records(records(report_facts(n=8)))["sue"])


def _bundle(rows, n_days=900, actions=None):
    b = make_bundle(n_days=n_days, n_companies=2, seed=2, facts=facts_frame(rows))
    if actions is not None:
        stock = pd.concat([pd.DataFrame({"company_id": c, "symbol": f"S{c}", "trade_date": b.calendar, "close": 50.0}) for c in (1, 2)])
        bench = pd.DataFrame({"symbol": "BMK", "trade_date": b.calendar, "close": 100.0})
        b = DataBundle.build(b.companies, stock, bench, actions, b.facts, b.exposures, b.events, b.targets, b.macro, b.membership)
    return b


def _day_index(cal, ts):
    """Calendar index of the first trading day whose close is at or after `ts`."""
    closes = cal.tz_localize("UTC") + pd.Timedelta(hours=21)
    return int(np.searchsorted(closes, ts, side="left"))


def test_a_report_counts_only_from_its_acceptance_and_flags_the_day():
    rows = report_facts(n=12)
    p = MarketPanel.from_bundle(_bundle(rows))
    f = p.fundamentals()
    _, end = quarter(11)
    accepted = (end + pd.Timedelta(days=35, hours=15)).tz_localize("UTC")
    i = _day_index(p.calendar, accepted)
    assert f["new_filing"].iat[i, 0] and not f["new_filing"].iat[i - 1, 0]
    assert f["days_since_filing"].iat[i, 0] == pytest.approx(0.25)          # accepted 15:00, close 21:00 UTC
    assert f["days_since_filing"].iat[i - 1, 0] > 80                         # previous quarter's report
    # a report accepted after the close is only visible the next trading day
    late = report_facts(n=12, accept_hour=23)
    fl = MarketPanel.from_bundle(_bundle(late)).fundamentals()
    i_late = _day_index(p.calendar, (end + pd.Timedelta(days=35, hours=23)).tz_localize("UTC"))
    assert i_late == i + 1 and fl["new_filing"].iat[i_late, 0] and not fl["new_filing"].iat[i, 0]


def test_later_reports_and_amendments_never_change_earlier_days():
    base = report_facts(n=12)
    _, end = quarter(11)
    amended = base + [{**r, "value": r["value"] * 3, "accession_no": "AMEND", "accepted_at": pd.Timestamp(end + pd.Timedelta(days=200), tz="UTC")}
                      for r in base if r["concept"] in ("Revenues", "NetIncomeLoss")]
    a = MarketPanel.from_bundle(_bundle(base)).fundamentals()
    b = MarketPanel.from_bundle(_bundle(amended + report_facts(n=13)[-9:])).fundamentals()
    cut = _day_index(a["sue"].index, (end + pd.Timedelta(days=36)).tz_localize("UTC"))
    for k in ("sue", "sue_rev", "gross_margin", "rev_accel", "earnings_yield", "days_since_filing"):
        pd.testing.assert_frame_equal(a[k].iloc[:cut], b[k].iloc[:cut], check_names=False)
    assert not a["sue"].iloc[-1:].equals(b["sue"].iloc[-1:])                 # but they do count once accepted


def test_market_cap_uses_filed_shares_adjusted_for_later_splits():
    rows = report_facts(n=12, shares=1000.0)
    _, end = quarter(11)
    ex = (end + pd.Timedelta(days=60)).normalize()
    actions = pd.DataFrame({"company_id": [1], "symbol": ["S1"], "ex_date": [ex], "action_type": ["SPLIT"], "value": [4.0]})
    p = MarketPanel.from_bundle(_bundle(rows, actions=actions))
    ey = p.fundamentals()["earnings_yield"][1]
    ttm_ni = profile_from_records(records(rows))["ttm_net_income"]
    before = ey[(ey.index > end + pd.Timedelta(days=40)) & (ey.index < ex)].iloc[-1]
    after = ey[ey.index >= ex].iloc[0]
    assert before == pytest.approx(ttm_ni / (50.0 * 1000))
    assert after == pytest.approx(ttm_ni / (50.0 * 4000))                    # raw close unchanged here, shares x4


def test_post_earnings_drift_rule_buys_on_the_surprise_day_and_holds_60_days():
    p = MarketPanel.from_bundle(_bundle(report_facts(n=13)))
    s = next(x for x in rule_strategies() if x.key == "PEAD_SUE")
    on = s.weights(p)[1].to_numpy() > 0
    _, end = quarter(12)
    i = _day_index(p.calendar, (end + pd.Timedelta(days=35, hours=15)).tz_localize("UTC"))
    assert on[i] and not on[i - 1]
    assert on[i:i + 61].all() and not on[i + 61]


def test_reports_only_features_reach_the_ai_dataset_point_in_time():
    p = MarketPanel.from_bundle(_bundle(report_facts(n=13)))
    d = dataset(p, AiConfig())
    assert set(FUND_MODEL_FEATURES) <= set(d.columns)
    row = d[d["company_id"] == 1].iloc[-1]
    assert row["sue"] == pytest.approx(p.fundamentals()["sue"][1].iloc[int(row["idx"])])
    assert np.isfinite(row["gross_margin"])
