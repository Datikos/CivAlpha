"""Inspectable probability model: L2 logistic regression on clipped, standardized features.

Uncertainty: the model is refit on bootstrap resamples of the training dates (whole cross-sections are
resampled together because samples on the same date are correlated). The reported interval is the
10th–90th percentile of the refits' predictions — it reflects estimation uncertainty only, not the
irreducible randomness of a 21-day return.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ALGORITHM = "logistic_regression_l2"
CODE_VERSION = "ml-0.1.0"


class LogitModel:
    def __init__(self, features: list[str], C: float = 0.5, n_boot: int = 30, seed: int = 7):
        self.features = list(features)
        self.C, self.n_boot, self.seed = C, n_boot, seed
        self.params: dict = {}

    # ---------------------------------------------------------------- fit
    def _prep(self, X: pd.DataFrame) -> np.ndarray:
        p = self.params
        a = X[self.features].astype(float).to_numpy(copy=True)
        med = np.array(p["median"])
        a = np.where(np.isnan(a), med, a)
        a = np.clip(a, np.array(p["lo"]), np.array(p["hi"]))
        return (a - np.array(p["mean"])) / np.array(p["std"])

    def fit(self, X: pd.DataFrame, y: pd.Series, groups: pd.Series | None = None) -> "LogitModel":
        a = X[self.features].astype(float)
        med = a.median().fillna(0.0)
        filled = a.fillna(med)
        lo, hi = filled.quantile(0.01), filled.quantile(0.99)
        clipped = filled.clip(lo, hi, axis=1)
        mean, std = clipped.mean(), clipped.std().replace(0, 1.0).fillna(1.0)
        self.params = {"median": med.tolist(), "lo": lo.tolist(), "hi": hi.tolist(), "mean": mean.tolist(), "std": std.tolist()}
        Z = self._prep(X)
        yv = y.astype(int).to_numpy()
        base = LogisticRegression(C=self.C, max_iter=1000).fit(Z, yv)
        self.params.update({"coef": base.coef_[0].tolist(), "intercept": float(base.intercept_[0]),
                            "base_rate": float(yv.mean()), "n_samples": int(len(yv)), "C": self.C})
        # date-block bootstrap for the uncertainty band
        rng = np.random.default_rng(self.seed)
        g = groups.to_numpy() if groups is not None else np.arange(len(yv))
        uniq = np.unique(g)
        boots = []
        for _ in range(self.n_boot):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            idx = np.concatenate([np.flatnonzero(g == u) for u in pick])
            if len(np.unique(yv[idx])) < 2:
                continue
            m = LogisticRegression(C=self.C, max_iter=1000).fit(Z[idx], yv[idx])
            boots.append([float(m.intercept_[0])] + m.coef_[0].tolist())
        self.params["bootstrap"] = boots
        return self

    # ---------------------------------------------------------------- predict
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        Z = self._prep(X)
        return 1 / (1 + np.exp(-(Z @ np.array(self.params["coef"]) + self.params["intercept"])))

    def predict_interval(self, X: pd.DataFrame, lo_q: float = 10, hi_q: float = 90) -> tuple[np.ndarray, np.ndarray]:
        Z = self._prep(X)
        B = np.array(self.params.get("bootstrap") or [[self.params["intercept"]] + self.params["coef"]])
        logits = Z @ B[:, 1:].T + B[:, 0]
        probs = 1 / (1 + np.exp(-logits))
        return np.percentile(probs, lo_q, axis=1), np.percentile(probs, hi_q, axis=1)

    def explain(self, row: pd.Series) -> dict:
        Z = self._prep(row.to_frame().T)[0]
        coef = np.array(self.params["coef"])
        contrib = coef * Z
        factors = []
        for f, v, z, c, k in zip(self.features, row[self.features].tolist(), Z, coef, contrib):
            factors.append({"feature": f, "value": None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v),
                            "z": float(z), "coefficient": float(c), "contribution": float(k),
                            "direction": "UP" if k > 0 else ("DOWN" if k < 0 else "NEUTRAL"),
                            "imputed": v is None or (isinstance(v, float) and math.isnan(v))})
        factors.sort(key=lambda x: -abs(x["contribution"]))
        return {"intercept": self.params["intercept"], "baseRate": self.params["base_rate"], "factors": factors}

    # ---------------------------------------------------------------- persistence
    def to_json(self) -> dict:
        return {"features": self.features, **self.params}

    @staticmethod
    def from_json(d: dict) -> "LogitModel":
        m = LogitModel(d["features"], C=d.get("C", 0.5))
        m.params = {k: v for k, v in d.items() if k != "features"}
        return m
