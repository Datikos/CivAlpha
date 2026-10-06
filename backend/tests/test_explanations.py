import numpy as np
import pandas as pd

from civalpha.model import LogitModel
from civalpha.strategies.ai import AiConfig, explain, new_model


def _fit_gbm(seed=0, n=600):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    X[rng.random(n) < 0.3, 1] = np.nan             # feature b is often missing, and missingness carries signal
    y = ((X[:, 0] + np.where(np.isnan(X[:, 1]), 1.0, 0.0) + rng.normal(scale=0.5, size=n)) > 0.5).astype(int)
    cfg = AiConfig(min_samples_leaf=20, max_iter=50)
    return new_model(cfg).fit(X, y), X


def test_gbm_explanation_marks_missing_inputs_as_imputed_and_keeps_their_contribution():
    model, X = _fit_gbm()
    medians = np.nanmedian(X, axis=0)
    x = np.array([0.2, np.nan, -0.3])
    out = explain(model, x, medians, top=3, features=["a", "b", "c"])
    b = next(f for f in out if f["feature"] == "b")
    assert b["value"] is None and b["imputed"] is True
    assert "missing-value branch" in b["imputation"]
    assert b["contribution"] != 0.0                 # absence is information; it is reported, not hidden
    for f in out:
        if f["feature"] != "b":
            assert f["imputed"] is False and f["imputation"] is None and f["value"] is not None


def test_gbm_explanation_names_the_all_missing_column_rule():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, 2))
    X[:, 1] = np.nan                                # no training values at all: set to 0 and ignored
    y = (X[:, 0] > 0).astype(int)
    model = new_model(AiConfig(min_samples_leaf=20, max_iter=30)).fit(X, y)
    out = explain(model, np.array([0.5, np.nan]), np.array([0.0, np.nan]), top=2, features=["a", "b"])
    b = next(f for f in out if f["feature"] == "b")
    assert b["imputed"] and b["imputation"].startswith("constant 0") and b["contribution"] == 0.0


def test_logit_explanation_marks_imputed_median():
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"a": rng.normal(size=400), "b": rng.normal(size=400)})
    y = pd.Series((df["a"] + 0.3 * df["b"] > 0).astype(int))
    m = LogitModel(["a", "b"], n_boot=2).fit(df, y)
    row = pd.Series({"a": 1.0, "b": np.nan})
    f = {x["feature"]: x for x in m.explain(row)["factors"]}
    assert f["b"]["imputed"] is True and f["b"]["value"] is None and f["b"]["imputation"] == "training median"
    assert f["a"]["imputed"] is False and f["a"]["imputation"] is None
