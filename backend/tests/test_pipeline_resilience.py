"""A failing macro or event source is logged and the pipeline goes on (2026-10-21/22: FRED answered with an empty body and
five runs failed before forecasts, decisions and advice)."""
import json
from types import SimpleNamespace

from civalpha.platform.pipeline import Pipeline


def bare_pipeline(fetch, ingest=None) -> Pipeline:
    p = Pipeline.__new__(Pipeline)            # no database: only the two steps under test
    p.s = SimpleNamespace(fred=SimpleNamespace(enabled=True, series=["FEDFUNDS", " CPIAUCSL"]))
    p.macro = SimpleNamespace(fetch_fred=fetch)
    p.events = SimpleNamespace(ingest=ingest or (lambda d: SimpleNamespace(created=True, event_id=7)))
    return p


def test_a_fred_outage_is_logged_and_the_other_series_still_load():
    def fetch(name):
        if name == "FEDFUNDS":
            json.loads("")                     # what an empty outage body does: JSONDecodeError
        return 683
    lines = []
    bare_pipeline(fetch).macro_series(lines.append)
    assert lines[0].startswith("FRED FEDFUNDS failed (JSONDecodeError") and "continuing" in lines[0]
    assert lines[1] == "FRED CPIAUCSL: 683 observations"


def test_one_event_that_cannot_be_stored_does_not_stop_the_rest():
    drafts = [SimpleNamespace(title="Tariff notice"), SimpleNamespace(title="FOMC statement")]

    def ingest(d):
        if d.title == "Tariff notice":
            raise ConnectionError("Name or service not known")
        return SimpleNamespace(created=True, event_id=42)
    lines = []
    bare_pipeline(lambda n: 1, ingest).policy_events(lines.append, sources=SimpleNamespace(collect=lambda log: drafts))
    assert lines == ["event 'Tariff notice' skipped (ConnectionError: Name or service not known)", "event 42: FOMC statement"]
