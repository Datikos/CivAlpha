"""Database access (read the PIT inputs, write model versions / evaluations / outcomes).

The schema is defined by the SQL migrations in db/migration. Forecast rows are written by civalpha.platform.forecasts only.
"""
from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .features import DataBundle


def _q(engine: Engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(text(sql), c, params=params)


def _dates(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df:
            df[c] = pd.to_datetime(df[c])
    return df


def _utc(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df:
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def load_bundle(engine: Engine) -> DataBundle:
    companies = _q(engine, """
        SELECT c.id, c.name, c.sector, c.industry, c.benchmark_symbol,
               (SELECT th.symbol FROM ticker_history th WHERE th.company_id = c.id ORDER BY th.valid_from DESC LIMIT 1) AS symbol
        FROM company c ORDER BY c.id""")
    stock = _q(engine, """SELECT company_id, symbol, trade_date, close::float8 AS close, volume::float8 AS volume
                          FROM price_bar WHERE company_id IS NOT NULL""")
    bench = _q(engine, "SELECT symbol, trade_date, close::float8 AS close FROM price_bar WHERE company_id IS NULL")
    actions = _dates(_q(engine, "SELECT company_id, symbol, ex_date, action_type, value::float8 AS value FROM corporate_action"), ["ex_date"])
    facts = _q(engine, """SELECT id, company_id, taxonomy, concept, unit, value::float8 AS value, period_start, period_end,
                                 dims_key, accession_no, accepted_at, form_type FROM xbrl_fact""")
    facts = _utc(_dates(facts, ["period_start", "period_end"]), ["accepted_at"])
    expo = _utc(_q(engine, """SELECT id, company_id, target_type, target_code, exposure_channel, share::float8 AS share, basis,
                                   confidence, method, available_at, passage_id, filing_id FROM company_exposure"""), ["available_at"])
    events = _utc(_q(engine, "SELECT id, category, event_type, title, published_at, evidence_status, attributes FROM policy_event"), ["published_at"])
    events["attributes"] = events["attributes"].apply(lambda a: a if isinstance(a, dict) else json.loads(a or "{}"))
    targets = _q(engine, "SELECT event_id, target_type, target_code, magnitude::float8 AS magnitude FROM event_target")
    macro = _dates(_q(engine, "SELECT series_id, obs_date, value::float8 AS value, realtime_start, realtime_end FROM macro_observation"),
                   ["obs_date", "realtime_start", "realtime_end"])
    membership = _dates(_q(engine, "SELECT company_id, valid_from, valid_to FROM universe_membership"), ["valid_from", "valid_to"])
    filings = _utc(_q(engine, "SELECT id, company_id, accession_no, form_type, items, accepted_at, source_document_id FROM filing"), ["accepted_at"])
    releases = _utc(_q(engine, "SELECT company_id, filing_id, accepted_at, guidance_tone FROM earnings_release"), ["accepted_at"])
    insiders = _utc(_dates(_q(engine, """SELECT company_id, available_at, trans_date, trans_code, acquired, shares::float8 AS shares,
                                                price::float8 AS price, owner_cik, owner_name FROM insider_transaction"""), ["trans_date"]), ["available_at"])
    if bench.empty:
        raise ValueError("No benchmark ETF prices are loaded. Update prices, or import a prices CSV that includes "
                         "the sector benchmark ETFs of your companies, before evaluating or issuing forecasts.")
    return DataBundle.build(companies, stock, bench, actions, facts, expo, events, targets, macro, membership, filings, insiders, releases)


def load_event_sources(engine: Engine) -> pd.DataFrame:
    return _utc(_q(engine, """SELECT es.event_id, es.role, sd.id AS document_id, sd.url, sd.publisher, sd.title,
                                     sd.published_at
                              FROM event_source es JOIN source_document sd ON sd.id = es.source_document_id"""), ["published_at"])


def load_passages(engine: Engine, ids: list[int]) -> pd.DataFrame:
    if not ids:
        return pd.DataFrame(columns=["id", "filing_id", "section", "accession_no", "form_type"])
    return _q(engine, """SELECT p.id, p.filing_id, p.section, f.accession_no, f.form_type
                         FROM filing_passage p JOIN filing f ON f.id = p.filing_id WHERE p.id = ANY(:ids)""", ids=list(ids))


def price_providers(engine: Engine) -> list[str]:
    return _q(engine, "SELECT DISTINCT provider FROM price_bar")["provider"].tolist()


def insert_model_version(engine: Engine, kind: str, algorithm: str, features: list[str], trained_through, training_cutoff,
                         n_samples: int, params: dict, code_version: str) -> int:
    with engine.begin() as c:
        return int(c.execute(text("""
            INSERT INTO model_version (model_kind, algorithm, feature_names, trained_through, training_cutoff, n_samples,
                                       params, code_version)
            VALUES (:k, :a, CAST(:f AS jsonb), :tt, :tc, :n, CAST(:p AS jsonb), :cv) RETURNING id"""),
            dict(k=kind, a=algorithm, f=json.dumps(features), tt=trained_through, tc=training_cutoff, n=n_samples,
                 p=json.dumps(params), cv=code_version)).scalar_one())


def insert_evaluation(engine: Engine, result: dict, data_cutoff) -> int:
    P = result["predictions"]
    with engine.begin() as c:
        eid = int(c.execute(text("""
            INSERT INTO model_evaluation (data_cutoff, config, metrics, comparison, calibration, trading, folds, verdict)
            VALUES (:dc, CAST(:cfg AS jsonb), CAST(:m AS jsonb), CAST(:cmp AS jsonb), CAST(:cal AS jsonb), CAST(:tr AS jsonb),
                    CAST(:folds AS jsonb), :v) RETURNING id"""),
            dict(dc=data_cutoff, cfg=json.dumps(result["config"]), m=json.dumps(_clean(result["metrics"])),
                 cmp=json.dumps(_clean(result["comparison"])), cal=json.dumps(_clean(result["calibration"])),
                 tr=json.dumps(_clean(result["trading"])), folds=json.dumps(_clean(result["folds"])), v=result["verdict"])).scalar_one())
        rows = [dict(e=eid, c=int(r.company_id), d=pd.Timestamp(r.as_of_date).date(), k=r.model_kind, f=int(r.fold),
                     p=float(r.probability), o=bool(r.outcome), x=float(r.excess_return)) for r in P.itertuples(index=False)]
        if rows:
            c.execute(text("""INSERT INTO backtest_prediction (evaluation_id, company_id, as_of_date, model_kind, fold, probability, outcome, excess_return)
                              VALUES (:e, :c, :d, :k, :f, :p, :o, :x)"""), rows)
    return eid


def insert_strategy_run(engine: Engine, lab: dict) -> int:
    with engine.begin() as c:
        rid = int(c.execute(text("""
            INSERT INTO strategy_run (data_cutoff, oos_start, config, summary)
            VALUES (:dc, :oos, CAST(:cfg AS jsonb), :s) RETURNING id"""),
            dict(dc=lab["dataCutoff"], oos=lab["oosStart"], cfg=json.dumps(_clean(lab["config"])), s=lab["summary"])).scalar_one())
        for r in lab["results"]:
            c.execute(text("""
                INSERT INTO strategy_result (run_id, strategy_key, family, name, description, params, metrics, equity, yearly,
                                             cost_sensitivity, verdict)
                VALUES (:run, :k, :f, :n, CAST(:d AS jsonb), CAST(:p AS jsonb), CAST(:m AS jsonb), CAST(:e AS jsonb),
                        CAST(:y AS jsonb), CAST(:cs AS jsonb), :v)"""),
                dict(run=rid, k=r["key"], f=r["family"], n=r["name"], d=json.dumps(r["description"]), p=json.dumps(_clean(r["params"])),
                     m=json.dumps(_clean(r["metrics"])), e=json.dumps(_clean(r["equity"])), y=json.dumps(_clean(r["yearly"])),
                     cs=json.dumps(_clean(r["costSensitivity"])), v=r["verdict"]))
            rows = [dict(run=rid, k=r["key"], c=t["companyId"], sym=t["symbol"], ed=t["entryDate"], xd=t["exitDate"],
                         ret=_clean(t["return"]), h=t["holdingDays"], er=t["entryReason"], xr=t["exitReason"]) for t in r["trades"]]
            if rows:
                c.execute(text("""
                    INSERT INTO strategy_trade (run_id, strategy_key, company_id, symbol, entry_date, exit_date, trade_return,
                                                holding_days, entry_reason, exit_reason)
                    VALUES (:run, :k, :c, :sym, :ed, :xd, :ret, :h, :er, :xr)"""), rows)
    return rid


def insert_doubler_study(engine: Engine, res: dict) -> int:
    with engine.begin() as c:
        return int(c.execute(text("""
            INSERT INTO doubler_study_run (data_cutoff, config, headline, result)
            VALUES (:dc, CAST(:cfg AS jsonb), :h, CAST(:r AS jsonb)) RETURNING id"""),
            dict(dc=res["dataCutoff"], cfg=json.dumps(_clean(res["config"])), h=res["headline"], r=json.dumps(_clean(res)))).scalar_one())


def insert_setup_study(engine: Engine, res: dict) -> int:
    with engine.begin() as c:
        return int(c.execute(text("""
            INSERT INTO setup_study_run (data_cutoff, config, headline, result)
            VALUES (:dc, CAST(:cfg AS jsonb), :h, CAST(:r AS jsonb)) RETURNING id"""),
            dict(dc=res["dataCutoff"], cfg=json.dumps(_clean(res["config"])), h=res["headline"], r=json.dumps(_clean(res)))).scalar_one())


def insert_time_machine(engine: Engine, res: dict) -> int:
    with engine.begin() as c:
        return int(c.execute(text("""
            INSERT INTO time_machine_run (as_of_date, data_cutoff, headline, result)
            VALUES (:d, :dc, :h, CAST(:r AS jsonb)) RETURNING id"""),
            dict(d=res["asOfDate"], dc=res["dataCutoff"], h=res["headline"], r=json.dumps(_clean(res)))).scalar_one())


def previous_ai_holdings(engine: Engine, strategy_key: str, before) -> set[int]:
    """Companies the AI held after its latest decision dated before `before` (ENTER or HOLD)."""
    df = _q(engine, """SELECT DISTINCT ON (company_id) company_id, action FROM strategy_decision
                       WHERE strategy_key = :k AND as_of_date < :d ORDER BY company_id, as_of_date DESC""", k=strategy_key, d=before)
    return set(df.loc[df["action"].isin(["ENTER", "HOLD"]), "company_id"].astype(int))


def unresolved_forecasts(engine: Engine) -> pd.DataFrame:
    return _dates(_q(engine, """SELECT f.id, f.company_id, f.benchmark_symbol, f.as_of_date, f.horizon_trading_days, f.probability::float8 AS probability
                                FROM forecast f LEFT JOIN forecast_outcome o ON o.forecast_id = f.id WHERE o.forecast_id IS NULL"""), ["as_of_date"])


def insert_outcomes(engine: Engine, rows: list[dict]) -> int:
    if not rows:
        return 0
    with engine.begin() as c:
        c.execute(text("""INSERT INTO forecast_outcome (forecast_id, window_end_date, stock_return, benchmark_return, excess_return, outcome, brier)
                          VALUES (:id, :end, :s, :b, :x, :o, :brier) ON CONFLICT (forecast_id) DO NOTHING"""), rows)
    return len(rows)


def _clean(o):
    """JSON-safe: NaN/inf -> None, numpy scalars -> python."""
    import math
    import numpy as np
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o
