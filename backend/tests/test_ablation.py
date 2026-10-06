import numpy as np
import pandas as pd

from civalpha.evaluation import EvalConfig, metric_cis
from civalpha.strategies import ablation
from civalpha.strategies.ai import AI_FEATURES
from civalpha.strategies.service import LabConfig
from helpers import make_bundle


def test_variants_cover_every_group_once_and_start_from_the_production_set():
    vs = {v.key: v for v in ablation.variants()}
    assert vs["FULL"].features == tuple(AI_FEATURES) and vs["FULL"].feature_set == "GBM_AI_39"
    dropped = sum(len(AI_FEATURES) - len(vs[f"NO_{g.upper()}"].features) for g in ablation.GROUPS)
    assert dropped == len(AI_FEATURES)                         # the four groups partition the production set
    for g, feats in ablation.GROUPS.items():
        assert not set(feats) & set(vs[f"NO_{g.upper()}"].features)
    for g, feats in ablation.ADDITIONS.items():
        assert set(feats) <= set(vs[f"PLUS_{g.upper()}"].features) and not set(feats) & set(AI_FEATURES)
    assert len({v.feature_set for v in vs.values()}) == len(vs)   # every variant has its own identifier


def test_metric_cis_cover_the_point_estimate_and_shrink_with_more_data():
    rng = np.random.default_rng(0)
    n = 4000
    y = rng.integers(0, 2, n).astype(float)
    p = np.clip(0.5 + 0.2 * (y - 0.5) + rng.normal(0, 0.15, n), 0.01, 0.99)   # an informed model
    df = pd.DataFrame({"probability": p, "outcome": y, "train_base_rate": 0.5, "idx": np.repeat(np.arange(n // 10), 10)})
    ci = metric_cis(df, n_boot=200)
    assert ci["brierSkill"][0] > 0 and ci["auc"][0] > 0.5
    small = metric_cis(df.iloc[:400], n_boot=200)
    assert (small["auc"][1] - small["auc"][0]) > (ci["auc"][1] - ci["auc"][0])


def test_study_reports_every_variant_on_the_target_with_cis_and_a_spread():
    b = make_bundle(n_days=900, n_companies=8, seed=5)
    res = ablation.run_study(b, EvalConfig(min_train_days=300, ci_boot=50), LabConfig(), with_lab=False)
    wf = pd.DataFrame(res["walkForward"])
    assert set(wf["variant"]) == {v.key for v in ablation.variants()}
    assert set(wf["horizon"]) == {21}                        # ADR-0001: one target; a second row only if the book's horizon differs
    assert len(ablation._specs(ablation.variants()[0], 10)) == 2
    assert wf[["brierSkillCiLow", "brierSkillCiHigh", "aucCiLow", "aucCiHigh"]].notna().all().all()
    assert (wf["brierSkillCiLow"] <= wf["brierSkill"]).all() and (wf["brierSkill"] <= wf["brierSkillCiHigh"]).all()
    assert "Brier skill spans" in res["headline"] and "No variant has Brier skill above zero" in res["headline"]
    assert res["lab"] == [] and res["config"]["lab"]["note"].startswith("not a skill metric")
    # on noise, no variant looks skilled
    assert (wf["auc"] - 0.5).abs().max() < 0.1
