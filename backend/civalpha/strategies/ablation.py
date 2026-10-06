"""Feature fragility study: how much the recorded book's numbers move when one input group is removed or added.

The production feature set (strategies.ai.AI_FEATURES, GBM_AI_39) is split into the groups a trader would name:
price/technical, report profile, insider, earnings reaction. Each variant drops one group; two more add the groups the
book does not use (dividends, policy events). Every variant is scored the way feature-set decisions are supposed to be
made (ground rule 1): walk-forward Brier skill and AUC on the 21-day forecast target and on the book's own 10-day
next-close label, with 95% CIs from a bootstrap over 21-day blocks of as-of dates. The lab Sharpe of the standard rule
and of the sized book on the same probabilities is reported as a secondary column and labelled "not a skill metric":
it is one trading rule on one five-year window. Each variant is a trial and is registered as such (source "ablation").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import db
from ..dividends import DIV_FEATURES
from ..earnings import EARNINGS_FEATURES
from ..evaluation import EvalConfig, ModelSpec, metric_cis, run_walk_forward
from ..features import EVENT_FEATURES, INSIDER_FEATURES, DataBundle, build_panel
from ..fundamentals import FUND_FEATURES
from ..returns import HORIZON
from . import backtest, registry, stats
from .ai import (AI_FEATURE_SET, AI_FEATURES, AI_KEY, AI_SIZED_KEY, EARNINGS_PANEL_FEATURES, INSIDER_PANEL_FEATURES, TECH_FEATURES,
                 AiConfig, attach_book_features, dataset, decide_positions, size_by_volatility, technical_features,
                 walk_forward_probabilities)
from .panel import MarketPanel
from .service import LabConfig

log = logging.getLogger(__name__)

GROUPS = {
    "price_technical": ["mom_21", "mom_63", "mom_126_21", "vol_63"] + TECH_FEATURES,
    "report_profile": ["rev_yoy", "gm_chg", "leverage"] + FUND_FEATURES,
    "insider": INSIDER_FEATURES + INSIDER_PANEL_FEATURES,
    "earnings": EARNINGS_FEATURES + EARNINGS_PANEL_FEATURES,
}
ADDITIONS = {"dividends": DIV_FEATURES, "policy_events": EVENT_FEATURES}
NOT_SKILL = "not a skill metric: one trading rule on one five-year window"


@dataclass(frozen=True)
class Variant:
    key: str
    feature_set: str
    features: tuple
    change: str          # full | drop:<group> | add:<group>


def variants() -> list[Variant]:
    out = [Variant("FULL", AI_FEATURE_SET, tuple(AI_FEATURES), "full")]
    for g, feats in GROUPS.items():
        kept = tuple(f for f in AI_FEATURES if f not in set(feats))
        out.append(Variant(f"NO_{g.upper()}", f"GBM_AI_{len(kept)}_no_{g}", kept, f"drop:{g}"))
    for g, feats in ADDITIONS.items():
        added = tuple(AI_FEATURES) + tuple(f for f in feats if f not in AI_FEATURES)
        out.append(Variant(f"PLUS_{g.upper()}", f"GBM_AI_{len(added)}_plus_{g}", added, f"add:{g}"))
    return out


def _specs(v: Variant, h: int) -> list[ModelSpec]:
    return [ModelSpec(f"{v.key}_{HORIZON}", v.feature_set, v.features, "gbm", HORIZON, "close(t)"),
            ModelSpec(f"{v.key}_{h}", v.feature_set, v.features, "gbm", h, "next close",
                      label_col=f"label_{h}", excess_col=f"excess_{h}", exec_col=f"excess_{h}")]


def walk_forward_rows(bundle: DataBundle, cfg: EvalConfig, ai_cfg: AiConfig, progress=None) -> list[dict]:
    """Walk-forward Brier skill and AUC (with CIs) of every variant at both horizons."""
    panel = attach_book_features(bundle, build_panel(bundle, sample_every=cfg.sample_every), ai_cfg, extra=list(DIV_FEATURES))
    rows = []
    for v in variants():
        if progress:
            progress(f"walk-forward {v.key}")
        wf = run_walk_forward(panel, bundle.calendar, cfg, _specs(v, ai_cfg.horizon))
        P = wf["predictions"]
        for sp in _specs(v, ai_cfg.horizon):
            if sp.kind not in wf["metrics"]:
                continue
            m = wf["metrics"][sp.kind]
            ci = metric_cis(P[P.model_kind == sp.kind], n_boot=cfg.ci_boot)
            top = next((c for c in wf["trading"][sp.kind].get("coverage", []) if abs(c["coverage"] - 0.10) < 1e-9), None)
            rows.append({"variant": v.key, "featureSet": v.feature_set, "nFeatures": len(v.features), "change": v.change,
                         "horizon": sp.horizon, "entry": sp.entry, "n": m["n"], "brierSkill": m["brierSkill"],
                         "brierSkillCiLow": ci["brierSkill"][0], "brierSkillCiHigh": ci["brierSkill"][1],
                         "auc": m["auc"], "aucCiLow": ci["auc"][0], "aucCiHigh": ci["auc"][1],
                         "top10NetExcess": None if top is None else top["meanNet"],
                         "top10CiLow": None if top is None else top["ciLow"], "top10CiHigh": None if top is None else top["ciHigh"]})
    return rows


def lab_rows(bundle: DataBundle, lab_cfg: LabConfig, progress=None, panel: MarketPanel | None = None) -> list[dict]:
    """Lab Sharpe and max drawdown of AI_GBM (standard rule) and AI_SIZED (the recorded book) on each variant's
    probabilities, computed exactly as the strategy lab does. Not a skill metric."""
    ai_cfg = lab_cfg.ai()
    mp = panel or MarketPanel.from_bundle(bundle)
    data = dataset(mp, ai_cfg)
    vol21 = technical_features(mp)["vol_21"]
    cash = mp.cash_ret.to_numpy()
    rows = []
    for v in variants():
        if progress:
            progress(f"lab {v.key}")
        wf = walk_forward_probabilities(mp, ai_cfg, data, features=list(v.features))
        oos = wf["oos_start_idx"]
        w, _ = decide_positions(wf["prob"], mp.member, ai_cfg, start_idx=oos)
        start = oos + 1
        for key, weights in ((AI_KEY, w), (AI_SIZED_KEY, size_by_volatility(w, vol21, ai_cfg))):
            res = backtest.run(weights, mp.ret, mp.cash_ret, start, lab_cfg.cost_bps_per_side)
            rows.append({"variant": v.key, "featureSet": v.feature_set, "strategyKey": key,
                         "sharpe": stats.sharpe(res.net - cash[start:]), "maxDrawdown": stats.max_drawdown(res.equity),
                         "cagr": stats.cagr(res.equity), "start": str(mp.calendar[start].date()), "end": str(mp.calendar[-1].date()),
                         "note": NOT_SKILL})
    return rows


def run_study(bundle: DataBundle, cfg: EvalConfig | None = None, lab_cfg: LabConfig | None = None, with_lab: bool = True,
              progress=None) -> dict:
    cfg = cfg or EvalConfig()
    lab_cfg = lab_cfg or LabConfig()
    wf = walk_forward_rows(bundle, cfg, lab_cfg.ai(), progress)
    lab = lab_rows(bundle, lab_cfg, progress) if with_lab else []
    res = {"dataCutoff": str(bundle.calendar[-1].date()), "runAt": datetime.now(timezone.utc).isoformat(),
           "config": {"productionFeatureSet": AI_FEATURE_SET, "groups": GROUPS, "additions": ADDITIONS,
                      "walkForward": {"horizon": cfg.horizon, "sampleEvery": cfg.sample_every, "embargo": cfg.embargo,
                                      "foldLength": cfg.fold_length, "minTrainDays": cfg.min_train_days, "ciBoot": cfg.ci_boot,
                                      "ciMethod": "bootstrap over 21-day blocks of as-of dates"},
                      "lab": {**lab_cfg.ai().params(), "costBpsPerSide": lab_cfg.cost_bps_per_side, "note": NOT_SKILL}},
           "variants": [{"key": v.key, "featureSet": v.feature_set, "nFeatures": len(v.features), "change": v.change,
                         "features": list(v.features)} for v in variants()],
           "walkForward": wf, "lab": lab}
    res["headline"] = headline(res)
    return res


def _spread(rows: list[dict], field: str) -> tuple[float, float] | None:
    vals = [r[field] for r in rows if r.get(field) is not None and np.isfinite(r[field])]
    return (min(vals), max(vals)) if vals else None


def headline(res: dict) -> str:
    wf, lab = res["walkForward"], res["lab"]
    parts = []
    for h in sorted({r["horizon"] for r in wf}):
        rows = [r for r in wf if r["horizon"] == h]
        full = next((r for r in rows if r["variant"] == "FULL"), None)
        bs, auc = _spread(rows, "brierSkill"), _spread(rows, "auc")
        if full and bs and auc:
            parts.append(f"{h}-day label: the production set ({full['featureSet']}) has Brier skill {full['brierSkill']:+.3f} "
                         f"(95% CI {full['brierSkillCiLow']:+.3f} to {full['brierSkillCiHigh']:+.3f}) and AUC {full['auc']:.3f} "
                         f"(CI {full['aucCiLow']:.3f} to {full['aucCiHigh']:.3f}); across the {len(rows)} variants Brier skill spans "
                         f"{bs[0]:+.3f} to {bs[1]:+.3f} and AUC {auc[0]:.3f} to {auc[1]:.3f}.")
    sized = [r for r in lab if r["strategyKey"] == AI_SIZED_KEY]
    if sized:
        full = next((r for r in sized if r["variant"] == "FULL"), None)
        sp = _spread(sized, "sharpe")
        if full and sp:
            parts.append(f"Lab Sharpe of the sized book ({NOT_SKILL}): {full['sharpe']:.2f} on the production set, "
                         f"{sp[0]:.2f} to {sp[1]:.2f} across the variants.")
    overlap = all(r["brierSkillCiLow"] <= 0 <= r["brierSkillCiHigh"] or r["brierSkillCiHigh"] < 0 for r in wf)
    if wf:
        parts.append("No variant has Brier skill above zero with a CI that excludes zero." if overlap else
                     "At least one variant has Brier skill above zero with a CI that excludes zero.")
    return " ".join(parts)


def study(engine, with_lab: bool = True, progress=None) -> dict:
    """Run the study on the stored data, store it, register every variant as a trial."""
    bundle = db.load_bundle(engine)
    res = run_study(bundle, with_lab=with_lab, progress=progress)
    res["id"] = db.insert_feature_ablation(engine, res)
    if res["lab"]:
        now = datetime.now(timezone.utc)
        rows = []
        for r in res["lab"]:
            rows.append({"trial_key": registry.trial_key(r["strategyKey"], r["featureSet"]), "strategy_key": r["strategyKey"],
                         "feature_set": r["featureSet"], "family": "AI",
                         "description": f"{r['strategyKey']} on {r['featureSet']} (feature ablation, {next(v['change'] for v in res['variants'] if v['key'] == r['variant'])})",
                         "tested_at": now, "run_id": None, "metrics": {"sharpe": r["sharpe"], "maxDrawdown": r["maxDrawdown"]},
                         "git_commit": registry.current_commit(), "params": {"ablationRunId": res["id"]}, "source": "ablation"})
        res["trialsRegistered"] = registry.register(engine, rows)
        res["trialsCounted"] = registry.trial_count(engine)
    return res
