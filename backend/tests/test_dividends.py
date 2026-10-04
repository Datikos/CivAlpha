"""Dividend profile: frequency, status, specials, split adjustment, streaks and the filed payout ratio."""
from datetime import date, timedelta

import pytest

import math

from civalpha.dividends import (daily_dividend_features, dividend_growth, dividend_profile, payout_from_facts,
                                payout_ratio_feature)


def quarterly(first: date, n: int, amount: float = 0.25, step: float = 0.0):
    return [(first + timedelta(days=91 * i), "CASH_DIVIDEND", amount + step * (i // 4)) for i in range(n)]


def test_no_dividends_is_none():
    p = dividend_profile([(date(2025, 6, 1), "SPLIT", 4.0)], date(2026, 1, 1), 100.0)
    assert p["status"] == "NONE" and p["frequency"] is None and p["ttmDividends"] == 0 and p["trailingYield"] is None


def test_regular_quarterly_payer():
    acts = quarterly(date(2023, 1, 10), 12)          # last ex-date 2025-10-07
    p = dividend_profile(acts, date(2025, 11, 1), 50.0)
    assert p["status"] == "REGULAR" and p["frequency"] == "QUARTERLY" and p["paymentsPerYear"] == 4
    assert p["ttmPayments"] == 4 and p["ttmDividends"] == pytest.approx(1.0)
    assert p["trailingYield"] == pytest.approx(0.02) and p["indicatedYield"] == pytest.approx(0.02)
    assert p["nextExpected"] == date(2026, 1, 6)
    assert p["yearsPaid"] == 3 and p["payments"][0]["exDate"] == date(2025, 10, 7)


@pytest.mark.parametrize("gap,label", [(30, "MONTHLY"), (182, "SEMIANNUAL"), (365, "ANNUAL")])
def test_frequency_from_the_median_gap(gap, label):
    acts = [(date(2023, 1, 5) + timedelta(days=gap * i), "CASH_DIVIDEND", 1.0) for i in range(30) if gap * i < 1000]
    as_of = acts[-1][0] + timedelta(days=5)
    assert dividend_profile(acts, as_of, 10.0)["frequency"] == label


def test_overdue_payment_means_suspended():
    acts = quarterly(date(2022, 1, 10), 8)           # last ex-date 2023-10-09
    p = dividend_profile(acts, date(2024, 6, 1), 50.0)
    assert p["status"] == "SUSPENDED" and p["indicatedYield"] is None and p["ttmPayments"] == 2


def test_only_dates_up_to_as_of_count():
    acts = quarterly(date(2023, 1, 10), 12)
    p = dividend_profile(acts, date(2023, 1, 9), 50.0)
    assert p["status"] == "NONE"


def test_special_dividend_counts_in_trailing_total_but_not_the_schedule():
    acts = quarterly(date(2024, 1, 10), 8) + [(date(2025, 6, 20), "CASH_DIVIDEND", 3.0)]
    p = dividend_profile(acts, date(2025, 11, 1), 100.0)
    assert p["status"] == "REGULAR" and p["frequency"] == "QUARTERLY"
    assert p["ttmSpecial"] == pytest.approx(3.0) and p["ttmDividends"] == pytest.approx(4.0)
    assert p["indicatedAnnual"] == pytest.approx(1.0)


def test_a_sustained_jump_is_a_raise_not_a_special():
    acts = quarterly(date(2024, 1, 10), 8, amount=0.01) + quarterly(date(2026, 1, 8), 3, amount=0.25)
    p = dividend_profile(acts, date(2026, 8, 1), 100.0)
    assert not any(x["special"] for x in p["payments"])
    assert p["status"] == "REGULAR" and p["indicatedAnnual"] == pytest.approx(1.0)


def test_an_old_special_does_not_break_the_raise_streak():
    acts = quarterly(date(2021, 1, 10), 20, amount=0.20, step=0.01) + [(date(2022, 12, 28), "CASH_DIVIDEND", 15.0)]
    p = dividend_profile(acts, date(2025, 11, 1), 40.0)
    assert [x["exDate"] for x in p["payments"] if x["special"]] == [date(2022, 12, 28)]
    assert p["yearsRaised"] == 3


def test_a_drifting_schedule_counts_one_year_of_regular_payments():
    acts = [(date(2025, 10, 3), "CASH_DIVIDEND", 0.41)] + [(d, "CASH_DIVIDEND", 0.42) for d in
                                                           (date(2026, 1, 2), date(2026, 4, 3), date(2026, 7, 3), date(2026, 10, 2))]
    p = dividend_profile(acts, date(2026, 10, 2), 50.0)
    assert p["ttmPayments"] == 4 and p["ttmDividends"] == pytest.approx(1.68)


def test_dividends_before_a_split_are_restated_per_current_share():
    acts = quarterly(date(2024, 1, 10), 8, amount=1.0)
    acts = [(d, t, v if d < date(2025, 3, 1) else v / 4) for d, t, v in acts] + [(date(2025, 3, 1), "SPLIT", 4.0)]
    p = dividend_profile(acts, date(2025, 11, 1), 25.0)
    assert {round(x["amount"], 6) for x in p["payments"]} == {0.25}
    assert p["ttmDividends"] == pytest.approx(1.0) and p["frequency"] == "QUARTERLY"
    # SPLIT_INFO: the provider already adjusted the history
    info = [(d, "SPLIT_INFO" if t == "SPLIT" else t, v) for d, t, v in acts]
    assert dividend_profile(info, date(2025, 11, 1), 25.0)["payments"][-1]["amount"] == pytest.approx(1.0)


def test_years_raised_counts_completed_years_with_a_higher_last_payment():
    acts = quarterly(date(2021, 1, 10), 20, amount=0.20, step=0.01)   # raise every four payments
    p = dividend_profile(acts, date(2025, 11, 1), 40.0)
    assert p["yearsPaid"] == 5 and p["yearsRaised"] == 3   # 2022, 2023, 2024 vs the year before


def fy(concept, value, end, form="10-K"):
    return {"concept": concept, "value": value, "period_start": end - timedelta(days=364), "period_end": end,
            "form_type": form, "filed_date": end + timedelta(days=40), "accession_no": f"A-{end}", "source_url": "u"}


def test_payout_uses_the_latest_fiscal_year_with_net_income():
    end, prev = date(2025, 9, 30), date(2024, 9, 30)
    facts = [fy("NetIncomeLoss", 100.0, end), fy("PaymentsOfDividends", 40.0, end), fy("PaymentsForRepurchaseOfCommonStock", 30.0, end),
             fy("NetIncomeLoss", 90.0, prev), fy("PaymentsOfDividends", 35.0, prev),
             {**fy("PaymentsOfDividends", 20.0, end), "period_start": end - timedelta(days=180)}]   # year-to-date: ignored
    p = payout_from_facts(facts)
    assert p["periodEnd"] == end and p["payoutRatio"] == pytest.approx(0.4) and p["totalPayoutRatio"] == pytest.approx(0.7)


def test_payout_is_not_meaningful_with_a_loss():
    end = date(2025, 12, 31)
    p = payout_from_facts([fy("NetIncomeLoss", -5.0, end), fy("PaymentsOfDividends", 2.0, end)])
    assert p["payoutRatio"] is None and p["dividendsPaid"] == 2.0
    assert payout_from_facts([fy("PaymentsOfDividends", 2.0, end)]) is None


def test_payout_falls_back_to_profit_loss_and_ordinary_dividends():
    end = date(2025, 11, 2)
    facts = [fy("NetIncomeLoss", 5.0, end - timedelta(days=364)), fy("PaymentsOfDividends", 9.0, end - timedelta(days=364)),
             fy("ProfitLoss", 20.0, end), fy("PaymentsOfOrdinaryDividends", 10.0, end)]
    p = payout_from_facts(facts)
    assert p["periodEnd"] == end and p["netIncome"] == 20.0 and p["payoutRatio"] == pytest.approx(0.5)


def test_trailing_twelve_month_income_is_not_read_as_no_dividends():
    fy_end, ttm_end = date(2025, 12, 31), date(2026, 6, 30)
    facts = [fy("NetIncomeLoss", 100.0, fy_end), fy("PaymentsOfDividends", 30.0, fy_end), fy("NetIncomeLoss", 120.0, ttm_end, "10-Q")]
    assert payout_from_facts(facts)["periodEnd"] == fy_end
    # a company that has never filed dividends takes its latest 12 months
    assert payout_from_facts(facts[2:])["periodEnd"] == ttm_end


def test_payout_feature_is_zero_without_dividends_and_nan_with_a_loss():
    end = date(2025, 12, 31)
    assert payout_ratio_feature([fy("NetIncomeLoss", 50.0, end)]) == 0.0
    assert math.isnan(payout_ratio_feature([fy("NetIncomeLoss", -5.0, end), fy("PaymentsOfDividends", 2.0, end)]))
    assert payout_ratio_feature([fy("NetIncomeLoss", 1.0, end), fy("PaymentsOfDividends", 9.0, end)]) == 3.0   # capped
    assert math.isnan(payout_ratio_feature([]))


def test_dividend_growth_from_raise_cut_and_suspension():
    raised = dividend_profile(quarterly(date(2023, 1, 10), 12, amount=0.20, step=0.02), date(2025, 11, 1), 40.0)
    assert dividend_growth(raised) == pytest.approx(math.log(0.24 / 0.22))
    cut = quarterly(date(2023, 1, 10), 8) + quarterly(date(2025, 1, 6), 4, amount=0.10)
    assert dividend_growth(dividend_profile(cut, date(2025, 11, 1), 40.0)) == pytest.approx(math.log(0.10 / 0.25))
    assert dividend_growth(dividend_profile(quarterly(date(2022, 1, 10), 8), date(2024, 6, 1), 40.0)) == -1.0
    assert math.isnan(dividend_growth(dividend_profile(quarterly(date(2025, 3, 10), 3), date(2025, 11, 1), 40.0)))
    assert math.isnan(dividend_growth(dividend_profile([], date(2025, 11, 1), 40.0)))


def test_daily_features_match_the_profile_and_ignore_later_dividends():
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(700) if (date(2024, 1, 1) + timedelta(days=k)).weekday() < 5]
    close = [50.0] * len(days)
    acts = quarterly(date(2024, 2, 5), 7)
    f = daily_dividend_features(acts, days, close)
    assert f["div_yield"][0] == 0.0 and math.isnan(f["div_growth"][0])     # before the first dividend
    ex_days = [days.index(a[0]) for a in acts if a[0] in days]
    assert len(ex_days) >= 5
    for i in ex_days:                                                      # recomputed on every ex-date
        assert f["div_yield"][i] == pytest.approx(dividend_profile(acts, days[i], 50.0)["trailingYield"])
        assert f["div_yield"][i + 3] == f["div_yield"][i]                  # carried forward between refreshes
    cut = 300
    later = [a for a in acts if a[0] <= days[cut]] + [(days[cut] + timedelta(days=30), "CASH_DIVIDEND", 5.0)]
    g = daily_dividend_features(later, days, close)
    assert f["div_yield"][:cut + 1] == g["div_yield"][:cut + 1]
