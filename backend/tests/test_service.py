import pandas as pd
import pytest

from civalpha import db, service


def test_resolve_outcomes_with_nothing_pending_needs_no_market_data(monkeypatch):
    monkeypatch.setattr(db, "unresolved_forecasts", lambda engine: pd.DataFrame(
        columns=["id", "company_id", "benchmark_symbol", "as_of_date", "horizon_trading_days", "probability"]))

    def no_prices(engine):
        raise ValueError("no benchmark prices loaded; import prices first")

    monkeypatch.setattr(db, "load_bundle", no_prices)
    assert service.resolve_outcomes(engine=None) == {"resolved": 0, "pending": 0}


def test_resolve_outcomes_with_pending_but_no_prices_still_reports_the_cause(monkeypatch):
    monkeypatch.setattr(db, "unresolved_forecasts", lambda engine: pd.DataFrame(
        [{"id": 1, "company_id": 1, "benchmark_symbol": "XLK", "as_of_date": pd.Timestamp("2026-01-02"),
          "horizon_trading_days": 21, "probability": 0.5}]))
    monkeypatch.setattr(db, "load_bundle", lambda engine: (_ for _ in ()).throw(ValueError("no benchmark prices loaded")))
    with pytest.raises(ValueError, match="benchmark"):
        service.resolve_outcomes(engine=None)
