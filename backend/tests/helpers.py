"""Small generated bundles for the model tests (random prices, no database)."""
import numpy as np
import pandas as pd

from civalpha.features import DataBundle

UTC = "UTC"


def ts(s):
    return pd.Timestamp(s, tz=UTC)


def fact(concept, value, start, end, acc, accepted, company_id=1, unit="USD", dims_key=""):
    return dict(company_id=company_id, taxonomy="us-gaap", concept=concept, unit=unit, value=float(value),
                period_start=pd.Timestamp(start) if start else pd.NaT, period_end=pd.Timestamp(end),
                dims_key=dims_key, accession_no=acc, accepted_at=ts(accepted), form_type="10-Q")


def facts_frame(rows):
    df = pd.DataFrame(rows)
    df["accepted_at"] = pd.to_datetime(df["accepted_at"], utc=True)
    return df


def make_bundle(n_days=400, n_companies=4, seed=0, events=None, targets=None, exposures=None, facts=None, macro=None, actions=None):
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2020-01-01", periods=n_days)
    bench = pd.DataFrame({"symbol": "BMK", "trade_date": cal, "close": 100 * np.cumprod(1 + rng.normal(0, 0.01, n_days))})
    stock = []
    for c in range(1, n_companies + 1):
        px = 50 * np.cumprod(1 + rng.normal(0, 0.015, n_days))
        stock.append(pd.DataFrame({"company_id": c, "symbol": f"S{c}", "trade_date": cal, "close": px}))
    stock = pd.concat(stock)
    companies = pd.DataFrame({"id": range(1, n_companies + 1), "symbol": [f"S{c}" for c in range(1, n_companies + 1)],
                              "benchmark_symbol": "BMK", "industry": "X"})
    actions = actions if actions is not None else pd.DataFrame(columns=["company_id", "symbol", "ex_date", "action_type", "value"])
    facts = facts if facts is not None else pd.DataFrame(columns=["company_id", "taxonomy", "concept", "unit", "value", "period_start",
                                                                  "period_end", "dims_key", "accession_no", "accepted_at", "form_type"])
    if len(facts) == 0:
        facts["accepted_at"] = pd.to_datetime(facts["accepted_at"], utc=True)
    exposures = exposures if exposures is not None else pd.DataFrame(columns=["id", "company_id", "target_type", "target_code", "exposure_channel",
                                                                              "share", "basis", "confidence", "method", "available_at", "passage_id", "filing_id"])
    if len(exposures) == 0:
        exposures["available_at"] = pd.to_datetime(exposures["available_at"], utc=True)
    events = events if events is not None else pd.DataFrame(columns=["id", "category", "event_type", "title", "published_at", "evidence_status", "attributes"])
    if len(events) == 0:
        events["published_at"] = pd.to_datetime(events["published_at"], utc=True)
    targets = targets if targets is not None else pd.DataFrame(columns=["event_id", "target_type", "target_code", "magnitude"])
    macro = macro if macro is not None else pd.DataFrame(columns=["series_id", "obs_date", "value", "realtime_start", "realtime_end"])
    membership = pd.DataFrame({"company_id": range(1, n_companies + 1), "valid_from": pd.Timestamp("2019-01-01"), "valid_to": pd.NaT})
    return DataBundle.build(companies, stock, bench, actions, facts, exposures, events, targets, macro, membership)
