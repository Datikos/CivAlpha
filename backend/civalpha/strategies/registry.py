"""The trial registry: every strategy variant and feature-set variant ever backtested, so the Deflated Sharpe Ratio
deflates for what was actually tried on this history (db/migration/V14__trial_registry.sql).

A trial is a (strategy rule, input list) pair. Rules without a model are their own trial (MOM_12_1); a model-based
strategy is keyed by rule and feature set (AI_GBM@GBM_AI_39), so dropping three inputs creates a new trial and the old one
stays on the books, while the same rule on the same inputs under a new row name (AI_WITH_EVENTS, AI_NO_EVENTS) stays one trial. The lab registers every candidate of each run (register_run); studies that try further feature sets
register theirs with source "ablation". `python -m civalpha.strategies.registry backfill` registers the runs stored before
the registry existed, inferring the feature set of the AI rows that did not record one (see _infer_feature_set).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..dividends import DIV_FEATURES
from .ai import (AI_CAL_KEY, AI_DIV_FEATURES, AI_EVENTS_FEATURES, AI_FEATURE_SET, AI_FEATURES, AI_CONF_KEY, AI_DIV_KEY, AI_FUND_KEY,
                 AI_KEY, AI_RANK_KEY, AI_RANK_SIZED_KEY, AI_RANK_VOL_KEY, AI_SIZED_CAL_KEY, AI_SIZED_KEY, AI_WITH_EVENTS_KEY,
                 FUND_MODEL_FEATURES)

FUND_FEATURE_SET = f"GBM_FUND_{len(FUND_MODEL_FEATURES)}"
DIV_FEATURE_SET = f"GBM_AI_DIV_{len(AI_DIV_FEATURES)}"
EVENTS_FEATURE_SET = f"GBM_AI_{len(AI_EVENTS_FEATURES)}"       # AI_GBM's list before 2026-10-06; the AI_WITH_EVENTS row today
# which AI strategy rows run on which input list today (rows acting on AI_GBM's probabilities share its feature set)
AI_FEATURE_SETS = {AI_KEY: AI_FEATURE_SET, AI_KEY + "_TSTOP10": AI_FEATURE_SET, AI_CONF_KEY: AI_FEATURE_SET,
                   AI_SIZED_KEY: AI_FEATURE_SET, AI_RANK_KEY: AI_FEATURE_SET, AI_RANK_SIZED_KEY: AI_FEATURE_SET,
                   AI_RANK_VOL_KEY: AI_FEATURE_SET, AI_FUND_KEY: FUND_FEATURE_SET, AI_DIV_KEY: DIV_FEATURE_SET,
                   AI_WITH_EVENTS_KEY: EVENTS_FEATURE_SET, AI_CAL_KEY: AI_FEATURE_SET, AI_SIZED_CAL_KEY: AI_FEATURE_SET}


# rows that are the standard AI rule under another name: AI_NO_EVENTS (run 15 only) was AI_GBM on today's 39 inputs, and
# AI_WITH_EVENTS is AI_GBM on the 42 inputs it had before 2026-10-06. Same rule, same thresholds, same inputs: one trial.
ALIASES = {"AI_NO_EVENTS": AI_KEY, AI_WITH_EVENTS_KEY: AI_KEY}


def trial_key(strategy_key: str, feature_set: str | None, horizon: int | None = None) -> str:
    """rule, or rule@featureSet@<h>d for a model-based row: the same inputs on another label are another trial (ADR-0001)."""
    rule = ALIASES.get(strategy_key, strategy_key)
    if not feature_set:
        return rule
    return f"{rule}@{feature_set}@{int(horizon)}d" if horizon else f"{rule}@{feature_set}"


def current_commit() -> str | None:
    """The code's git commit: CIVALPHA_GIT_COMMIT (set at image build) or `git rev-parse HEAD` when run from a checkout."""
    env = os.environ.get("CIVALPHA_GIT_COMMIT")
    if env and env != "unknown":
        return env
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5).stdout.strip() or None
    except Exception:  # noqa: BLE001 - no git in the container
        return None


def feature_set_of(result: dict) -> str | None:
    """Feature-set identifier of one lab result: from the recorded input list when the row has one, else by strategy key."""
    feats = (result.get("params") or {}).get("features")
    if feats:
        return _identifier(list(feats))
    if result.get("family") == "AI":
        return AI_FEATURE_SETS.get(result["key"], AI_FEATURE_SET)
    return None


def _identifier(feats: list[str]) -> str:
    """Identifier of an input list by its content, so the same list under two strategy names is one feature set: the
    report-only list is GBM_FUND_n, a list with the dividend features is GBM_AI_DIV_n, anything else GBM_AI_n (the
    standard list with or without the three policy-event features, told apart by n)."""
    fs = set(feats)
    if fs == set(FUND_MODEL_FEATURES):
        return f"GBM_FUND_{len(fs)}"
    if set(DIV_FEATURES) <= fs:
        return f"GBM_AI_DIV_{len(fs)}"
    return f"GBM_AI_{len(fs)}"


def planned_trial_keys(results_keys: list[tuple[str, str]], horizon: int | None = None) -> list[str]:
    """Trial keys of a run about to be scored: (strategy key, family) pairs -> keys, benchmarks excluded."""
    return [trial_key(k, AI_FEATURE_SETS.get(k) if fam == "AI" else None, horizon if fam == "AI" else None)
            for k, fam in results_keys if fam != "BENCHMARK"]


def horizon_of(result: dict) -> int | None:
    h = (result.get("params") or {}).get("horizon")
    return int(h) if h is not None and result.get("family") == "AI" else None


# --------------------------------------------------------------------------- store
_UPSERT = """
INSERT INTO trial_registry (trial_key, strategy_key, feature_set, family, description, first_tested_at, last_tested_at, runs,
                            last_run_id, sharpe, excess_return, excess_ci_low, excess_ci_high, max_drawdown, git_commit, params,
                            source, notes)
VALUES (:tk, :sk, :fs, :fam, :d, :t, :t, 1, :run, :sh, :ex, :lo, :hi, :dd, :git, CAST(:p AS jsonb), :src, :notes)
ON CONFLICT (trial_key) DO UPDATE SET
    last_tested_at = GREATEST(trial_registry.last_tested_at, EXCLUDED.last_tested_at),
    first_tested_at = LEAST(trial_registry.first_tested_at, EXCLUDED.first_tested_at),
    runs = trial_registry.runs + 1,
    last_run_id = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.last_run_id ELSE trial_registry.last_run_id END,
    sharpe = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.sharpe ELSE trial_registry.sharpe END,
    excess_return = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.excess_return ELSE trial_registry.excess_return END,
    excess_ci_low = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.excess_ci_low ELSE trial_registry.excess_ci_low END,
    excess_ci_high = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.excess_ci_high ELSE trial_registry.excess_ci_high END,
    max_drawdown = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.max_drawdown ELSE trial_registry.max_drawdown END,
    git_commit = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.git_commit ELSE trial_registry.git_commit END,
    params = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.params ELSE trial_registry.params END,
    source = CASE WHEN EXCLUDED.last_tested_at >= trial_registry.last_tested_at THEN EXCLUDED.source ELSE trial_registry.source END,
    description = EXCLUDED.description"""


def _num(x):
    import math
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else float(x)


def register(engine: Engine, rows: list[dict]) -> int:
    """Upsert trials. Each row: trial_key, strategy_key, feature_set, family, description, tested_at, run_id, metrics
    (sharpe, excessReturn, excessCiLow, excessCiHigh, maxDrawdown), git_commit, params, source, notes."""
    if not rows:
        return 0
    with engine.begin() as c:
        for r in rows:
            m = r.get("metrics") or {}
            c.execute(text(_UPSERT), dict(
                tk=r["trial_key"], sk=r["strategy_key"], fs=r.get("feature_set"), fam=r["family"], d=r.get("description") or "",
                t=r["tested_at"], run=r.get("run_id"), sh=_num(m.get("sharpe")), ex=_num(m.get("excessReturn")),
                lo=_num(m.get("excessCiLow")), hi=_num(m.get("excessCiHigh")), dd=_num(m.get("maxDrawdown")),
                git=r.get("git_commit"), p=json.dumps(r.get("params") or {}, default=str), src=r.get("source", "lab"), notes=r.get("notes")))
    return len(rows)


def register_run(engine: Engine, run_id: int, lab: dict, tested_at: datetime | None = None, git_commit: str | None = None,
                 source: str = "lab") -> int:
    """Register every non-benchmark result of a lab run (run_lab output)."""
    tested_at = tested_at or datetime.now(timezone.utc)
    git_commit = git_commit or current_commit()
    rows = []
    for r in lab["results"]:
        if r["family"] == "BENCHMARK":
            continue
        fs = feature_set_of(r)
        rows.append({"trial_key": trial_key(r["key"], fs, horizon_of(r)), "strategy_key": r["key"], "feature_set": fs, "family": r["family"],
                     "description": _describe(r), "tested_at": tested_at, "run_id": run_id, "metrics": r["metrics"],
                     "git_commit": git_commit, "params": _slim(r.get("params")), "source": source})
    return register(engine, rows)


def _describe(r: dict) -> str:
    d = r.get("description")
    if isinstance(d, dict):
        return " ".join(str(d.get(k) or "") for k in ("name", "entry", "exit") if d.get(k)).strip() or r.get("name", r["key"])
    return str(d or r.get("name") or r["key"])


def _slim(params: dict | None) -> dict:
    return {k: v for k, v in (params or {}).items() if k != "features"}


def trial_count(engine: Engine) -> int:
    """Trials scored on lab Sharpe so far: the number the Deflated Sharpe Ratio deflates for."""
    with engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM trial_registry WHERE sharpe IS NOT NULL AND family <> 'BENCHMARK'")).scalar_one())


def trial_count_including(engine: Engine, keys: list[str]) -> int:
    """Registry trials plus those of `keys` not registered yet: the count a run about to be scored must deflate for."""
    with engine.connect() as c:
        known = {row[0] for row in c.execute(text("SELECT trial_key FROM trial_registry WHERE sharpe IS NOT NULL AND family <> 'BENCHMARK'"))}
    return len(known | set(keys))


def registry_frame(engine: Engine) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(text("SELECT trial_key, family, feature_set, first_tested_at, last_tested_at, runs, sharpe, excess_return, "
                                "excess_ci_low, excess_ci_high, max_drawdown, git_commit, source, notes FROM trial_registry "
                                "ORDER BY first_tested_at, trial_key"), c)


# --------------------------------------------------------------------------- backfill
def _infer_feature_set(key: str, run_rows: pd.DataFrame, params: dict) -> tuple[str | None, str | None]:
    """Feature set of a stored AI row. Rows that recorded their input list are exact. AI_GBM and the rows acting on its
    probabilities never recorded theirs; AI_DIV, which did, is AI_GBM's list plus the three dividend features, so
    AI_GBM's size in that run is AI_DIV's size minus three (inferred). Runs without an AI_DIV row take the next run's value."""
    feats = params.get("features")
    if feats:
        return _identifier(list(feats)), None
    div = run_rows[run_rows["strategy_key"] == AI_DIV_KEY]
    if len(div):
        n_div = len((div.iloc[0]["params"] or {}).get("features") or [])
        if n_div:
            return f"GBM_AI_{n_div - len(DIV_FEATURES)}", f"feature set inferred: AI_DIV recorded {n_div} inputs = AI_GBM's + {len(DIV_FEATURES)} dividend features"
    return None, "feature set unknown: no row of this run recorded its input list"


def backfill(engine: Engine, commits: list[tuple[datetime, str]] | None = None) -> int:
    """Register every result of every stored lab run. `commits`: (committed_at, sha) pairs so each run is tagged with the
    latest commit before it ran (an inference: the worker image may have lagged the checkout); None leaves git_commit empty."""
    with engine.connect() as c:
        runs = pd.read_sql(text("SELECT id, run_at FROM strategy_run ORDER BY id"), c)
        res = pd.read_sql(text("SELECT run_id, strategy_key, family, name, description, params, metrics FROM strategy_result ORDER BY run_id, strategy_key"), c)
    for col in ("params", "metrics", "description"):
        res[col] = res[col].apply(lambda v: v if isinstance(v, (dict, list)) or v is None else json.loads(v))
    rows = []
    pending_unknown: list[dict] = []
    for run in runs.itertuples(index=False):
        rr = res[res["run_id"] == run.id]
        run_at = pd.Timestamp(run.run_at).to_pydatetime()
        git = None
        if commits:
            before = [sha for t, sha in commits if t <= run_at]
            git = before[-1] if before else None
        for r in rr.itertuples(index=False):
            if r.family == "BENCHMARK":
                continue
            params = r.params or {}
            notes = None
            if r.family == "AI":
                fs, notes = _infer_feature_set(r.strategy_key, rr, params)
            else:
                fs = None
            h = int(params["horizon"]) if r.family == "AI" and params.get("horizon") is not None else None
            row = {"trial_key": trial_key(r.strategy_key, fs, h), "strategy_key": r.strategy_key, "feature_set": fs, "family": r.family,
                   "description": _describe({"description": r.description, "name": r.name, "key": r.strategy_key}),
                   "tested_at": run_at, "run_id": int(run.id), "metrics": r.metrics or {}, "git_commit": git, "params": _slim(params),
                   "source": "backfill-inferred" if notes else "backfill",
                   "notes": (notes + "; " if notes else "") + ("git commit inferred from commit time" if git else "")}
            if fs is None and r.family == "AI":
                pending_unknown.append(row)
            else:
                rows.append(row)
    # AI rows whose run had no AI_DIV row: take the feature set the same key had in the next run that could be inferred
    by_key_time = sorted([(r["tested_at"], r["strategy_key"], r["feature_set"]) for r in rows if r["family"] == "AI"])
    for row in pending_unknown:
        later = [fs for t, k, fs in by_key_time if k == row["strategy_key"] and t >= row["tested_at"]]
        fs = later[0] if later else AI_FEATURE_SETS.get(row["strategy_key"])
        row["feature_set"] = fs
        row["trial_key"] = trial_key(row["strategy_key"], fs, (row.get("params") or {}).get("horizon"))
        row["notes"] = "feature set inferred from the next run that recorded one; " + (row["notes"] or "")
        rows.append(row)
    return register(engine, rows)


def main(argv: list[str]) -> None:
    from ..platform.sql import engine
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "backfill":
        commits = None
        if len(argv) > 2:   # a file with lines "<iso committed_at> <sha>" from: git log --format='%cI %H'
            commits = []
            for line in open(argv[2]):
                t, sha = line.split()
                commits.append((datetime.fromisoformat(t).astimezone(timezone.utc), sha))
            commits.sort()
        print(f"registered {backfill(engine(), commits)} rows")
    df = registry_frame(engine())
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    pd.set_option("display.max_colwidth", 60)
    print(df.to_string(index=False))
    print(f"trials counted for the deflated Sharpe: {trial_count(engine())}")


if __name__ == "__main__":
    main(sys.argv)
