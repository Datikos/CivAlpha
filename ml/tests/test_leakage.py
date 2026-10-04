"""End-to-end look-ahead test: changing anything published after the cutoff must not change features."""
import numpy as np
import pandas as pd

from civalpha_ml import pit
from civalpha_ml.features import AUGMENTED_FEATURES, DataBundle, build_rows
from helpers import fact, facts_frame, make_bundle, ts


def _bundle(perturb_future_after=None, include_future_event=True):
    facts = facts_frame([fact("Revenues", 100 + i, f"2020-{m:02d}-01", f"2020-{m+2:02d}-28", f"A{i}", f"2020-{m+3:02d}-10T15:00Z", company_id=c)
                         for c in (1, 2, 3, 4) for i, m in enumerate((1, 4, 7, 10)) if m + 3 <= 12] +
                        [fact("Assets", 1000, None, "2020-03-31", "A0", "2020-04-10T15:00Z", company_id=c) for c in (1, 2, 3, 4)] +
                        [fact("LongTermDebtNoncurrent", 100 * c, None, "2020-03-31", "A0", "2020-04-10T15:00Z", company_id=c) for c in (1, 2, 3, 4)])
    events = pd.DataFrame({"id": [1, 2], "category": "TRADE_TARIFF", "event_type": "TARIFF_IMPOSED", "title": ["past", "future"],
                           "published_at": pd.to_datetime(["2021-01-04T20:00Z", "2021-03-01T20:00Z"], utc=True),
                           "evidence_status": "OFFICIAL", "attributes": [{"severity": 0.5}, {"severity": 0.9}]})
    targets = pd.DataFrame({"event_id": [1, 2], "target_type": "COUNTRY", "target_code": "CN", "magnitude": [25, 25]})
    exposures = pd.DataFrame({"id": [1, 2], "company_id": [1, 2], "target_type": "COUNTRY", "target_code": "CN",
                              "exposure_channel": "REVENUE", "share": [0.3, 0.1], "basis": "DIRECTLY_REPORTED", "confidence": "HIGH",
                              "method": "XBRL_DIMENSION", "available_at": pd.to_datetime(["2020-02-01", "2020-02-01"], utc=True),
                              "passage_id": None, "filing_id": None})
    if not include_future_event:
        events, targets = events[events.id == 1], targets[targets.event_id == 1]
    b = make_bundle(n_days=320, seed=1, events=events, targets=targets, exposures=exposures, facts=facts)
    if perturb_future_after is not None:
        cut = perturb_future_after
        i = int(np.searchsorted(b.calendar, cut, side="right"))
        for c in b.tr:
            b.tr[c][i:] *= 3.0  # wildly different future prices
        for s in b.bench_tr:
            b.bench_tr[s][i:] *= 0.2
    return b


def test_future_prices_events_and_exposures_do_not_leak():
    as_of_date = pd.Timestamp("2021-01-29")
    base = _bundle(include_future_event=False)
    idx = int(base.calendar.get_loc(as_of_date))
    a = pd.DataFrame(build_rows(base, idx, with_labels=False))
    future = _bundle(perturb_future_after=as_of_date)
    # add an exposure and a fact that only become available later
    future.exposures = pd.concat([future.exposures, pd.DataFrame([{**future.exposures.iloc[0].to_dict(), "id": 9, "company_id": 3,
                                                                   "share": 0.9, "available_at": ts("2021-02-15")}])])
    future._expo_snap.clear()
    future.facts = pd.concat([future.facts, facts_frame([
        fact("Revenues", 9999, "2020-10-01", "2020-12-31", "LATE", "2021-02-10T15:00Z", company_id=1),
        fact("Assets", 10, None, "2020-12-31", "LATE", "2021-02-10T15:00Z", company_id=1),
        fact("LongTermDebtNoncurrent", 9, None, "2020-12-31", "LATE", "2021-02-10T15:00Z", company_id=1)])])
    future._fund_snap.clear()
    b = pd.DataFrame(build_rows(future, idx, with_labels=False))
    pd.testing.assert_frame_equal(a[AUGMENTED_FEATURES], b[AUGMENTED_FEATURES])
    # sanity: the same inputs DO change features once they are available
    after = pd.DataFrame(build_rows(future, int(future.calendar.get_loc(pd.Timestamp("2021-03-02"))), with_labels=False))
    assert after.loc[after.company_id == 1, "leverage"].iloc[0] == 0.9
    assert after.loc[after.company_id == 3, "trade_shock"].iloc[0] < 0
    # sanity: the past tariff event does reach the exposed company
    assert a.loc[a.company_id == 1, "trade_shock"].iloc[0] < 0
    assert a.loc[a.company_id == 4, "trade_shock"].iloc[0] == 0


def test_event_published_after_cutoff_but_before_live_issue_is_used_with_age_zero():
    b = _bundle()
    d = pd.Timestamp("2021-03-01")  # event 2 published 20:00Z on this date, before the 21:00Z close cutoff
    idx = int(b.calendar.get_loc(d))
    at_close = pd.DataFrame(build_rows(b, idx, with_labels=False))
    later = pd.DataFrame(build_rows(b, idx, as_of=pit.close_ts(d) + pd.Timedelta(hours=5), with_labels=False))
    pd.testing.assert_frame_equal(at_close[AUGMENTED_FEATURES], later[AUGMENTED_FEATURES])  # same evening re-issue: identical
