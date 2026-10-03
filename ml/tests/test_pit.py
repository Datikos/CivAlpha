"""Point-in-time joins: nothing published after the as-of time may be visible."""
import numpy as np
import pandas as pd

from civalpha_ml import pit
from civalpha_ml.features import DataBundle, fundamentals_from_facts
from helpers import fact, facts_frame, ts


def test_fact_filed_after_as_of_is_invisible():
    f = facts_frame([fact("Revenues", 100, "2023-01-01", "2023-03-31", "A1", "2023-05-01T14:00:00Z"),
                     fact("Revenues", 120, "2023-04-01", "2023-06-30", "A2", "2023-08-01T14:00:00Z")])
    known = pit.facts_as_of(f, ts("2023-07-15"))
    assert list(known["value"]) == [100.0]


def test_acceptance_after_close_is_available_next_day_only():
    f = facts_frame([fact("Revenues", 100, "2023-01-01", "2023-03-31", "A1", "2023-05-01T22:30:00Z")])  # after 21:00 UTC cutoff
    assert pit.facts_as_of(f, pit.close_ts("2023-05-01")).empty
    assert len(pit.facts_as_of(f, pit.close_ts("2023-05-02"))) == 1


def test_revised_filing_replaces_value_only_after_amendment_acceptance():
    f = facts_frame([
        fact("Revenues", 1000, "2023-01-01", "2023-12-31", "ORIG-10K", "2024-02-20T15:00:00Z"),
        fact("Revenues", 970, "2023-01-01", "2023-12-31", "AMEND-10KA", "2024-04-01T15:00:00Z"),
    ])
    before = pit.facts_as_of(f, ts("2024-03-15"))
    assert before["value"].tolist() == [1000.0] and not before["revised"].iloc[0]
    after = pit.facts_as_of(f, ts("2024-04-02"))
    assert after["value"].tolist() == [970.0]
    assert after["revised"].iloc[0] and after["original_value"].iloc[0] == 1000.0
    assert after["accession_no"].iloc[0] == "AMEND-10KA"


def test_comparative_restatement_in_later_10q_is_point_in_time():
    # Q1 2023 first reported 100; the Q1 2024 10-Q re-reports the Q1 2023 comparative as 95
    f = facts_frame([fact("Revenues", 100, "2023-01-01", "2023-03-31", "Q1-23", "2023-05-01T14:00:00Z"),
                     fact("Revenues", 95, "2023-01-01", "2023-03-31", "Q1-24", "2024-05-01T14:00:00Z")])
    assert pit.facts_as_of(f, ts("2023-12-31"))["value"].tolist() == [100.0]
    assert pit.facts_as_of(f, ts("2024-06-01"))["value"].tolist() == [95.0]


def test_macro_vintages():
    m = pd.DataFrame({"series_id": ["CPI", "CPI", "CPI"],
                      "obs_date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-02-01"]),
                      "value": [300.0, 301.0, 302.0],
                      "realtime_start": pd.to_datetime(["2024-02-13", "2024-03-13", "2024-03-13"]),
                      "realtime_end": pd.to_datetime(["2024-03-12", None, None])})
    assert pit.macro_as_of(m, "CPI", ts("2024-02-01")).empty                      # not yet published
    assert pit.macro_as_of(m, "CPI", ts("2024-02-20")).tolist() == [300.0]        # first print
    assert pit.macro_as_of(m, "CPI", ts("2024-03-20")).tolist() == [301.0, 302.0]  # revised + new obs


def test_universe_membership_is_point_in_time():
    mem = pd.DataFrame({"company_id": [1, 2], "valid_from": pd.to_datetime(["2020-01-01", "2022-01-01"]),
                        "valid_to": pd.to_datetime(["2023-01-01", None])})
    assert pit.members_as_of(mem, "2021-06-01") == {1}
    assert pit.members_as_of(mem, "2022-06-01") == {1, 2}
    assert pit.members_as_of(mem, "2023-01-01") == {2}  # valid_to is exclusive


def test_events_need_official_evidence_and_publication():
    ev = pd.DataFrame({"id": [1, 2, 3], "category": "TRADE_TARIFF", "event_type": "TARIFF_IMPOSED", "title": "x",
                       "published_at": pd.to_datetime(["2024-01-01T20:00Z", "2024-01-05T20:00Z", "2024-01-02T20:00Z"], utc=True),
                       "evidence_status": ["OFFICIAL", "OFFICIAL", "NEWS_ONLY"], "attributes": [{}, {}, {}]})
    assert pit.events_as_of(ev, ts("2024-01-03"))["id"].tolist() == [1]


def test_exposures_latest_version_available():
    ex = pd.DataFrame({"id": [1, 2], "company_id": 1, "target_type": "COUNTRY", "target_code": "CN", "exposure_channel": "REVENUE",
                       "share": [0.2, 0.3], "available_at": pd.to_datetime(["2023-02-01", "2024-02-01"], utc=True)})
    assert pit.exposures_as_of(ex, ts("2023-06-01"))["share"].tolist() == [0.2]
    assert pit.exposures_as_of(ex, ts("2024-06-01"))["share"].tolist() == [0.3]


def test_last_trading_date_respects_close_cutoff():
    cal = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    assert pit.last_trading_date_at(cal, "2024-01-03T20:00:00Z") == pd.Timestamp("2024-01-02")
    assert pit.last_trading_date_at(cal, "2024-01-03T21:00:00Z") == pd.Timestamp("2024-01-03")
    assert pit.last_trading_date_at(cal, "2024-01-01T00:00:00Z") is None


def test_incremental_fundamental_snapshots_match_direct_pit_query():
    rng = np.random.default_rng(3)
    rows = []
    q_ends = pd.date_range("2021-03-31", periods=10, freq="QE")
    for i, end in enumerate(q_ends):
        start = (end - pd.offsets.QuarterBegin(startingMonth=1)).normalize()
        acc_time = end + pd.Timedelta(days=35, hours=15)
        rows.append(fact("Revenues", 100 + i * 5 + rng.normal(), start, end, f"A{i}", acc_time))
        rows.append(fact("GrossProfit", 40 + i, start, end, f"A{i}", acc_time))
        rows.append(fact("Assets", 1000 + i, None, end, f"A{i}", acc_time))
        rows.append(fact("LongTermDebtNoncurrent", 300, None, end, f"A{i}", acc_time))
        if i >= 4:  # comparative re-reported with a revision
            prev = rows[(i - 4) * 4]
            rows.append({**prev, "value": prev["value"] * 0.98, "accession_no": f"A{i}", "accepted_at": ts(acc_time)})
    f = facts_frame(rows)
    b = DataBundle(companies=None, calendar=None, tr={}, bench_tr={}, facts=f, exposures=None, events=None, targets=None,
                   macro=None, membership=None)
    times, snaps = b._fund_snapshots(1)
    assert len(times) == 10
    for t, snap in zip(times, snaps):
        direct = fundamentals_from_facts(pit.facts_as_of(f, pd.Timestamp(t, tz="UTC")))
        for k in ("rev_yoy", "gm_chg", "leverage"):
            assert (np.isnan(direct[k]) and np.isnan(snap[k])) or abs(direct[k] - snap[k]) < 1e-12
    # the revision of a comparative is used once it is filed
    last = snaps[-1]
    assert not np.isnan(last["rev_yoy"])
