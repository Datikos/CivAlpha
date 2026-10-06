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


def test_book_model_is_issued_live_on_the_21_day_target_with_flagged_inputs():
    import numpy as np
    import pandas as pd

    from civalpha import pit
    from civalpha.features import build_panel, build_rows
    from civalpha.service import BOOK_KIND, book_forecasts
    from civalpha.strategies.ai import AI_FEATURES
    from helpers import make_bundle

    b = make_bundle(n_days=900, n_companies=6, seed=5)
    panel = build_panel(b, sample_every=7)
    idx = len(b.calendar) - 1
    rows = pd.DataFrame(build_rows(b, idx, as_of=pit.close_ts(b.calendar[idx]), with_provenance=True, with_labels=False))
    book = book_forecasts(b, panel, rows, idx)
    assert book["kind"] == BOOK_KIND and book["features"] == list(AI_FEATURES) and book["featureSet"] == "GBM_AI_39"
    assert len(book["probability"]) == len(rows) and np.all((book["probability"] > 0) & (book["probability"] < 1))
    # trained only on labels that had closed by idx
    assert book["trainedThrough"] <= b.calendar[idx - 21].date()
    f = book["factors"][0]
    assert {"feature", "label", "kind", "value", "median", "contribution", "imputed", "imputation"} <= set(f[0])
    # no filings in the synthetic bundle: every report-profile input is missing and says so
    missing = [x for x in f if x["imputed"]]
    assert all(x["value"] is None and x["imputation"] for x in missing)
    assert book["modelParams"]["calibration"].startswith("none")
