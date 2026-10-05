"""Earnings announcements: the session a release trades in, point-in-time features, the catalyst-calendar estimate,
the exhibit finder, the guidance keyword rules, and the setups."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from civalpha import pit
from civalpha.earnings import Announcement, announcements, features_at, next_estimate, session_excess, session_index
from civalpha.features import DataBundle
from civalpha.platform.earnings import classify_guidance, exhibit_from_headers, guess_exhibit, release_text
from civalpha.strategies import setups
from civalpha.strategies.panel import MarketPanel
from helpers import make_bundle


def _filings(rows):
    """rows: (id, company_id, form, items, accepted_at ISO UTC)"""
    return pd.DataFrame([{"id": i, "company_id": c, "accession_no": f"acc-{i}", "form_type": f, "items": it,
                          "accepted_at": pd.Timestamp(t, tz="UTC"), "source_document_id": None} for i, c, f, it, t in rows])


def test_release_trades_in_the_first_session_at_or_after_its_acceptance():
    b = make_bundle(n_days=300, n_companies=1, seed=2)
    cal = b.calendar
    d = cal[100]
    pre_open = f"{d.date()}T11:30:00Z"      # 7:30 New York, before the open: trades that day
    after_close = f"{d.date()}T21:05:00Z"   # 17:05 New York, after the close: trades the next day
    f = _filings([(1, 1, "8-K", "2.02,9.01", pre_open), (2, 1, "8-K", "2.02", after_close), (3, 1, "8-K", "7.01", pre_open),
                  (4, 1, "10-Q", "", pre_open), (5, 2, "8-K", "2.02", pre_open)])
    ann = announcements(f, None, 1, cal)
    assert [a.session_idx for a in ann] == [100, 101] and [a.filing_id for a in ann] == [1, 2]
    assert announcements(f, None, 3, cal) == [] and announcements(pd.DataFrame(), None, 1, cal) == []
    # the session is decided in New York time, summer and winter alike (16:15 New York is after the close either way)
    july = pd.bdate_range("2026-07-01", periods=30)
    jan = pd.bdate_range("2026-01-05", periods=30)
    assert session_index(july, pd.Timestamp("2026-07-08T20:15:00Z")) == july.get_loc(pd.Timestamp("2026-07-09"))   # 16:15 EDT
    assert session_index(july, pd.Timestamp("2026-07-08T19:45:00Z")) == july.get_loc(pd.Timestamp("2026-07-08"))   # 15:45 EDT
    assert session_index(jan, pd.Timestamp("2026-01-07T20:15:00Z")) == jan.get_loc(pd.Timestamp("2026-01-07"))     # 15:15 EST
    assert session_index(jan, pd.Timestamp("2026-01-07T21:15:00Z")) == jan.get_loc(pd.Timestamp("2026-01-08"))     # 16:15 EST
    assert session_index(july, pd.Timestamp("2026-07-11T12:00:00Z")) == july.get_loc(pd.Timestamp("2026-07-13"))   # a Saturday
    assert session_index(july, pd.Timestamp("2026-09-01T12:00:00Z")) == len(july)                                   # after the calendar
    tr_s = np.ones(300)
    tr_s[101:] = 1.10                                                   # +10% in session 101
    tr_b = np.ones(300)
    tr_b[101:] = 1.02
    assert session_excess(tr_s, tr_b, 101) == pytest.approx(0.08)
    assert np.isnan(session_excess(tr_s, tr_b, 0)) and np.isnan(session_excess(tr_s, tr_b, 300))


def test_features_are_point_in_time_and_the_next_date_is_estimated():
    b = make_bundle(n_days=600, n_companies=1, seed=2)
    cal = b.calendar
    rows = [(i + 1, 1, "8-K", "2.02", f"{cal[k].date()}T21:05:00Z") for i, k in enumerate((50, 113, 176, 239, 302, 365))]
    f = _filings(rows)
    rel = pd.DataFrame([{"company_id": 1, "filing_id": 6, "accepted_at": f["accepted_at"].iloc[-1], "guidance_tone": "RAISED"},
                        {"company_id": 1, "filing_id": 5, "accepted_at": f["accepted_at"].iloc[-2], "guidance_tone": "LOWERED"}])
    ann = announcements(f, rel, 1, cal)
    assert [a.session_idx for a in ann] == [51, 114, 177, 240, 303, 366] and ann[-1].guidance == "RAISED" and ann[0].guidance == "UNKNOWN"
    tr_s, tr_b = np.ones(600), np.ones(600)
    tr_s[366:] = 1.06                                                   # +6% reaction in session 366
    # the close of the announcement day: the after-close release is not known yet
    x = features_at(ann, tr_s, tr_b, 365, pit.close_ts(cal[365]).value, cal)
    assert x["earn_react_last"] == 0.0 and x["guidance_last"] == -1.0 and x["days_since_earnings"] == 62
    x = features_at(ann, tr_s, tr_b, 366, pit.close_ts(cal[366]).value, cal)
    assert x["earn_react_last"] == pytest.approx(0.06) and x["guidance_last"] == 1.0 and x["days_since_earnings"] == 0
    x = features_at(ann, tr_s, tr_b, 366 + 64, pit.close_ts(cal[366 + 64]).value, cal)
    assert x["earn_react_last"] == 0.0 and x["guidance_last"] == 0.0 and x["days_since_earnings"] == 64  # the reaction has aged out
    assert features_at([], tr_s, tr_b, 100, pit.close_ts(cal[100]).value, cal) == {"earn_react_last": 0.0, "guidance_last": 0.0,
                                                                                    "days_since_earnings": 130.0, "days_to_earnings_est": 130.0}
    # next estimate: a year after the announcement that followed the same announcement last year
    from civalpha.earnings import trading_days_until
    est = next_estimate(ann, 366, cal)
    anchor = ann[1]                                                      # ~a year before the latest (session 114 vs 366)
    expected = trading_days_until(cal, ann[2].accepted_at + pd.Timedelta(days=365)) - 366
    assert est == expected and 60 < est < 80                             # ~63 trading days ahead: the quarterly rhythm
    assert next_estimate(ann[:1], 60, cal) == trading_days_until(cal, ann[0].accepted_at + pd.Timedelta(days=91)) - 60
    assert next_estimate(ann, len(cal) - 1, cal) == 0                    # long overdue: 0, never negative
    short = cal[:370]                                                    # a calendar that ends just after the latest: extended with business days
    assert 60 < next_estimate(ann, 369, short) < 80
    assert next_estimate([], 10, cal) is None


HEADERS = """<pre>&lt;ACCEPTANCE-DATETIME&gt;20261001161515
&lt;TYPE&gt;8-K
&lt;SEQUENCE&gt;1
&lt;FILENAME&gt;nke-20261001.htm
&lt;TYPE&gt;EX-99.1
&lt;SEQUENCE&gt;2
&lt;FILENAME&gt;q1fy27exhibit991er.htm
&lt;DESCRIPTION&gt;EX-99.1
&lt;TYPE&gt;EX-101.SCH
&lt;FILENAME&gt;nke-20261001.xsd</pre>"""


def test_exhibit_is_found_in_the_headers_or_guessed_from_names():
    assert exhibit_from_headers(HEADERS) == "q1fy27exhibit991er.htm"
    assert exhibit_from_headers("<TYPE>EX-99.2\n<FILENAME>a.htm\n<TYPE>EX-99.1\n<FILENAME>b.htm") == "b.htm"
    assert exhibit_from_headers("<TYPE>EX-99.2\n<FILENAME>a.htm") == "a.htm"
    assert exhibit_from_headers("<TYPE>8-K\n<FILENAME>cover.htm") is None
    assert guess_exhibit(["tsla-20261002.htm", "exhibit991111111.htm", "R1.htm", "report.css"]) == "exhibit991111111.htm"
    assert guess_exhibit(["a-ex99_1.htm", "a.htm"]) == "a-ex99_1.htm" and guess_exhibit(["a.htm"]) is None


def test_guidance_tone_from_press_release_sentences():
    assert classify_guidance("Outlook. The company raised its full-year revenue guidance to $10 billion.").tone == "RAISED"
    assert classify_guidance("We are increasing our fiscal 2027 outlook. Forward-looking statements: we may raise guidance.").tone == "RAISED"
    g = classify_guidance("Due to tariffs the company lowered its fiscal year 2026 guidance for EPS. Revenue rose 5%.")
    assert g.tone == "LOWERED" and "lowered its fiscal year 2026 guidance" in g.evidence
    assert classify_guidance("The company reaffirmed its 2026 outlook and reported record revenue.").tone == "MAINTAINED"
    assert classify_guidance("Outlook: Revenues are expected to decline high-single digits in fiscal 2027.").tone == "PROVIDED"
    assert classify_guidance("Full-year guidance is above the prior range given in March.").tone == "RAISED"
    assert classify_guidance("Net income was $1.2 billion. Cash from operations rose.") == classify_guidance("") and classify_guidance("").tone == "NONE"
    # raised beats maintained when both appear; boilerplate after the forward-looking heading is ignored
    assert classify_guidance("We reaffirmed our outlook for margins and raised our revenue guidance.").tone == "RAISED"
    assert classify_guidance("Results. FORWARD-LOOKING STATEMENTS We may lower guidance in the future.").tone == "NONE"
    # verbs about dividends, buybacks, cash targets or the conference call are not outlook statements
    assert classify_guidance("We maintain our target of reaching a net cash neutral position over time.").tone == "NONE"
    assert classify_guidance("As a result, we are raising our target common stock dividend by 11 percent.").tone == "NONE"
    assert classify_guidance("Microsoft will provide forward-looking guidance on its earnings conference call.").tone == "NONE"
    # noun-first phrasing and headline run-ons
    assert classify_guidance("•2023 reported EPS guidance raised $1.00 to a range of $9.70 to $9.90.").tone == "RAISED"
    assert classify_guidance("•Revenue guidance reaffirmed to be between $58.0 and $61.0 billion.").tone == "MAINTAINED"
    assert classify_guidance("Full-year guidance range was lowered to reflect currency.").tone == "LOWERED"
    g = classify_guidance("com; (317) 617-0983 (Investors) Lilly reports second-quarter 2026 results, raises full-year guidance.")
    assert g.tone == "RAISED" and g.evidence.startswith("Lilly reports")
    html = "<html><body><h1>Q1 results</h1><p>NIKE&nbsp;expects revenue to decline in fiscal 2027.</p><script>x()</script></body></html>"
    assert "NIKE expects revenue to decline in fiscal 2027." in release_text(html) and classify_guidance(release_text(html)).tone == "PROVIDED"


def test_panel_matrices_and_setups_follow_the_announcements():
    n = 700
    b = make_bundle(n_days=n, n_companies=2, seed=3)
    cal = b.calendar
    rows = [(i + 1, 1, "8-K", "2.02", f"{cal[k].date()}T21:05:00Z") for i, k in enumerate((200, 263, 326, 389, 452))]
    f = _filings(rows)
    rel = pd.DataFrame([{"company_id": 1, "filing_id": 5, "accepted_at": f["accepted_at"].iloc[-1], "guidance_tone": "LOWERED"}])
    closes = {c: np.full(n, 100.0) for c in (1, 2)}
    closes[1][453:] = 108.0                                            # +8% in the session after the 5th release
    stock = pd.concat([pd.DataFrame({"company_id": c, "symbol": f"S{c}", "trade_date": cal, "close": closes[c]}) for c in (1, 2)])
    bench = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": 100.0})
    bundle = DataBundle.build(b.companies, stock, bench, None, b.facts, b.exposures, b.events, b.targets, b.macro,
                              b.membership.assign(valid_from=pd.Timestamp("2019-01-01")), filings=f, releases=rel)
    p = MarketPanel.from_bundle(bundle)
    e = p.earnings()
    assert e["announcement"][1].to_numpy().nonzero()[0].tolist() == [201, 264, 327, 390, 453]
    assert e["reaction"].iat[453, 0] == pytest.approx(0.08) and np.isnan(e["reaction"].iat[452, 0])
    assert e["earn_react_last"].iat[460, 0] == pytest.approx(0.08) and e["earn_react_last"].iat[453 + 70, 0] == 0.0
    assert e["guidance_last"].iat[460, 0] == -1.0 and e["days_since_earnings"].iat[460, 0] == 7
    assert (e["days_to_earnings_est"][2] == 130).all() and 0 < e["days_to_earnings_est"].iat[460, 0] < 130
    cfg = setups.SetupConfig()
    reg = {s.key: s for s in setups.setups()}
    assert reg["EARNINGS_REACTION_UP"].fn(p, cfg)[1].to_numpy().nonzero()[0].tolist() == [453]
    assert not reg["EARNINGS_REACTION_DOWN"].fn(p, cfg)[1].any()
    assert reg["GUIDANCE_LOWERED"].fn(p, cfg)[1].to_numpy().nonzero()[0].tolist() == [453]
    assert not reg["GUIDANCE_RAISED"].fn(p, cfg)[1].any()
    pre = reg["PRE_EARNINGS"].fn(p, cfg)[1].to_numpy().nonzero()[0].tolist()
    assert pre and all(e["days_to_earnings_est"].iat[k, 0] <= 5 for k in pre)
    assert {s.family for s in setups.setups()} <= set(setups.FAMILIES)
