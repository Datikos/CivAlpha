"""The AI strategy: a gradient-boosted model that combines every theory's indicator and decides entries/exits.

Inputs per (date, company), all known at close(t):
  * the forecasting model's features (features.build_rows): relative momentum, volatility, as-filed
    fundamentals, tariff/rate shocks through SEC-filing exposures, fed funds change x leverage;
  * the indicators behind the classic rules: distance from the 50/200-day averages, RSI(2)/RSI(14),
    Bollinger z-score, position in the 55-day Donchian channel, 12-1 momentum, 5-day reversal,
    21-day volatility, drawdown from the 52-week high;
  * the financial-report profile (fundamentals.py): revenue acceleration, earnings and revenue surprise,
    margins, R&D intensity, profitability, balance sheet, valuation, days since the latest report.

AI_FUND is the same model trained on the report profile alone; AI_DIV is AI_GBM plus the dividend signals
(dividends.DIV_FEATURES: trailing yield, change in the regular dividend, filed payout ratio), run side by side so the
backtest shows whether they add anything.

Label: the stock's total return beats its sector ETF over `horizon` days, entering at the next close
(t+1 -> t+1+horizon). That outcome is known at close(t+1+horizon), so a model refitted at index R only
uses samples with t + 1 + horizon < R (walk-forward with an expanding window, refit every `fold_length` days).

Decision rule (hysteresis keeps turnover down): ENTER when p >= entry_p and the stock ranks in the top
`max_positions`; EXIT when p < exit_p; otherwise HOLD / STAY_OUT. Each position gets 1/max_positions of
capital, the rest stays in cash, so the model also decides how much is invested.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from ..dividends import DIV_FEATURES, DIV_LABELS
from ..evaluation import EvalConfig, train_mask, walk_forward_folds
from ..features import AUGMENTED_FEATURES, FEATURE_KIND, FEATURE_LABELS
from ..fundamentals import FUND_FEATURES, FUND_LABELS
from .base import Strategy, prior_high, prior_low, rolling_std, rsi, sma
from .panel import MarketPanel

TECH_FEATURES = ["dist_sma50", "dist_sma200", "rsi2", "rsi14", "boll_z", "donchian_pos", "mom_12_1", "rev_5d", "vol_21", "dd_52w"]
AI_FEATURES = AUGMENTED_FEATURES + TECH_FEATURES + FUND_FEATURES
# the reports-only model: what the quarterly/annual filings say, nothing from prices except valuation and recency
FUND_MODEL_FEATURES = ["rev_yoy", "gm_chg", "leverage"] + FUND_FEATURES
AI_DIV_FEATURES = AI_FEATURES + DIV_FEATURES
TECH_LABELS = {
    "dist_sma50": "Distance from 50-day average", "dist_sma200": "Distance from 200-day average",
    "rsi2": "RSI(2)", "rsi14": "RSI(14)", "boll_z": "Bollinger z-score (20-day)",
    "donchian_pos": "Position in 55-day high/low channel", "mom_12_1": "12-1 month momentum",
    "rev_5d": "5-day return (reversal)", "vol_21": "21-day volatility", "dd_52w": "Drawdown from 52-week high",
}
AI_KEY = "AI_GBM"
AI_FUND_KEY = "AI_FUND"
AI_DIV_KEY = "AI_DIV"
ALGORITHM = "hist_gradient_boosting"
CODE_VERSION = "strategy-0.1.0"


@dataclass
class AiConfig:
    horizon: int = 10
    fold_length: int = 63
    min_train_days: int = 504
    entry_p: float = 0.55
    exit_p: float = 0.48
    max_positions: int = 8
    max_iter: int = 150
    learning_rate: float = 0.05
    max_depth: int = 3
    min_samples_leaf: int = 200
    l2_regularization: float = 1.0
    seed: int = 7

    def params(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


def feature_label(f: str) -> str:
    return TECH_LABELS.get(f) or FUND_LABELS.get(f) or DIV_LABELS.get(f) or FEATURE_LABELS.get(f, f)


def feature_kind(f: str) -> str:
    if f in TECH_LABELS:
        return "TECHNICAL"
    if f in DIV_LABELS:
        return "DIVIDEND"
    return "FUNDAMENTAL" if f in FUND_LABELS else FEATURE_KIND.get(f, "OTHER")


# --------------------------------------------------------------------------- dataset
def technical_features(p: MarketPanel) -> dict[str, pd.DataFrame]:
    px = p.px
    mid = sma(px, 20)
    hi, lo = prior_high(px, 55), prior_low(px, 55)
    rng = (hi - lo).replace(0.0, np.nan)
    return {
        "dist_sma50": px / sma(px, 50) - 1.0,
        "dist_sma200": px / sma(px, 200) - 1.0,
        "rsi2": rsi(px, 2),
        "rsi14": rsi(px, 14),
        "boll_z": (px - mid) / rolling_std(px, 20).replace(0.0, np.nan),
        "donchian_pos": (px - lo) / rng,
        "mom_12_1": px.shift(21) / px.shift(252) - 1.0,
        "rev_5d": px / px.shift(5) - 1.0,
        "vol_21": np.log(px).diff().rolling(21, min_periods=15).std() * np.sqrt(252),
        "dd_52w": px / px.rolling(252, min_periods=60).max() - 1.0,
    }


def dataset(p: MarketPanel, cfg: AiConfig) -> pd.DataFrame:
    """Long table (idx, company_id, features..., label, fwd_excess). Rows only for members with model features."""
    base = p.features()
    if base.empty:
        return pd.DataFrame(columns=["idx", "company_id", *AI_DIV_FEATURES, "label", "fwd_excess"])
    df = base[["idx", "company_id", *AUGMENTED_FEATURES]].copy()
    cal_pos = df["idx"].to_numpy()
    col_pos = p.px.columns.get_indexer(df["company_id"].to_numpy())
    keep = col_pos >= 0
    df, cal_pos, col_pos = df[keep].reset_index(drop=True), cal_pos[keep], col_pos[keep]
    for name, m in technical_features(p).items():
        df[name] = m.to_numpy(float)[cal_pos, col_pos]
    fund = p.fundamentals()
    for name in FUND_FEATURES:
        df[name] = fund[name].to_numpy(float)[cal_pos, col_pos]
    div = p.dividends()
    for name in DIV_FEATURES:
        df[name] = div[name].to_numpy(float)[cal_pos, col_pos]
    h = cfg.horizon
    s_fwd = p.px.shift(-(h + 1)) / p.px.shift(-1) - 1.0
    b_fwd = p.bench_px.shift(-(h + 1)) / p.bench_px.shift(-1) - 1.0
    ex = (s_fwd - b_fwd).to_numpy(float)[cal_pos, col_pos]
    df["fwd_excess"] = ex
    df["label"] = np.where(np.isnan(ex), np.nan, (ex > 0).astype(float))
    df["member"] = p.member.to_numpy(bool)[cal_pos, col_pos]
    return df[df["member"]].drop(columns="member").reset_index(drop=True)


class AiModel:
    """HistGradientBoostingClassifier that tolerates features with no values in the training window.

    Such a column (e.g. fundamentals before any filing is loaded) carries no information; it is set to 0 in
    training and prediction alike, so the trees ignore it.
    """

    def __init__(self, cfg: AiConfig):
        self.clf = HistGradientBoostingClassifier(max_iter=cfg.max_iter, learning_rate=cfg.learning_rate, max_depth=cfg.max_depth,
                                                  min_samples_leaf=cfg.min_samples_leaf, l2_regularization=cfg.l2_regularization,
                                                  early_stopping=False, random_state=cfg.seed)
        self.empty = np.zeros(0, dtype=bool)

    def _prep(self, X: np.ndarray) -> np.ndarray:
        X = np.array(X, dtype=float, copy=True)
        X[:, self.empty] = 0.0
        return X

    def fit(self, X: np.ndarray, y: np.ndarray) -> "AiModel":
        self.empty = np.isnan(X).all(axis=0)
        self.clf.fit(self._prep(X), y)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(self._prep(X))


def new_model(cfg: AiConfig) -> AiModel:
    return AiModel(cfg)


def _eval_cfg(cfg: AiConfig) -> EvalConfig:
    # sample i's label is known at close(i + horizon + 1) because entry is at the next close
    return EvalConfig(horizon=cfg.horizon + 1, embargo=cfg.horizon + 1, fold_length=cfg.fold_length,
                      min_train_days=cfg.min_train_days, sample_every=1)


# --------------------------------------------------------------------------- walk-forward
def walk_forward_probabilities(p: MarketPanel, cfg: AiConfig, data: pd.DataFrame | None = None,
                               features: list[str] = AI_FEATURES) -> dict:
    """Out-of-sample P(beat sector ETF) for every member and day after the first `min_train_days`."""
    data = dataset(p, cfg) if data is None else data
    prob = np.full((len(p.calendar), len(p.px.columns)), np.nan)
    if data.empty:
        raise ValueError("not enough history for the AI strategy")
    ecfg = _eval_cfg(cfg)
    folds = walk_forward_folds(data["idx"].to_numpy(), len(p.calendar), ecfg)
    out_folds = []
    for fold in folds:
        test = data[(data["idx"] >= fold["test_start_idx"]) & (data["idx"] <= fold["test_end_idx"])]
        train = data[train_mask(data, fold, ecfg)]
        if test.empty or len(train) < 200 or train["label"].nunique() < 2:
            continue
        m = new_model(cfg).fit(train[features].to_numpy(float), train["label"].astype(int).to_numpy())
        pr = m.predict_proba(test[features].to_numpy(float))[:, 1]
        prob[test["idx"].to_numpy(), p.px.columns.get_indexer(test["company_id"].to_numpy())] = pr
        out_folds.append({"fold": fold["fold"], "testStart": str(p.calendar[fold["test_start_idx"]].date()),
                          "testEnd": str(p.calendar[min(fold["test_end_idx"], len(p.calendar) - 1)].date()),
                          "trainEnd": str(p.calendar[int(train["idx"].max())].date()), "nTrain": int(len(train)),
                          "maxTrainLabelIdx": int(train["idx"].max()) + ecfg.horizon, "testStartIdx": fold["test_start_idx"]})
    if not out_folds:
        raise ValueError("not enough history for the AI strategy's walk-forward training")
    return {"prob": pd.DataFrame(prob, index=p.calendar, columns=p.px.columns), "folds": out_folds,
            "oos_start_idx": out_folds[0]["testStartIdx"]}


def decide_positions(prob: pd.DataFrame, member: pd.DataFrame, cfg: AiConfig, start_idx: int = 0,
                     held0: set | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the entry/exit rule day by day. Returns (weights, actions) over the whole calendar."""
    P = prob.to_numpy(float)
    M = member.reindex(index=prob.index, columns=prob.columns, fill_value=False).to_numpy(bool)
    T, N = P.shape
    W = np.zeros((T, N))
    A = np.full((T, N), "", dtype=object)
    held = np.zeros(N, dtype=bool)
    if held0:
        for c in held0:
            if c in prob.columns:
                held[prob.columns.get_loc(c)] = True
    for t in range(start_idx, T):
        p = P[t]
        valid = M[t] & np.isfinite(p)
        was = held.copy()
        held &= valid & (np.where(valid, p, -1.0) >= cfg.exit_p)
        order = np.argsort(-np.where(valid, p, -np.inf), kind="stable")
        top = [j for j in order[:cfg.max_positions] if valid[j]]
        for j in top:
            if held.sum() >= cfg.max_positions:
                break
            if not held[j] and p[j] >= cfg.entry_p:
                held[j] = True
        W[t] = np.where(held, 1.0 / cfg.max_positions, 0.0)
        A[t] = np.where(held & ~was, "ENTER", np.where(~held & was, "EXIT", np.where(held, "HOLD", "STAY_OUT")))
        A[t][~M[t] & ~was] = ""
    return pd.DataFrame(W, index=prob.index, columns=prob.columns), pd.DataFrame(A, index=prob.index, columns=prob.columns)


def ai_strategies(cfg: AiConfig, weights: pd.DataFrame, fund_weights: pd.DataFrame | None = None,
                  div_weights: pd.DataFrame | None = None) -> list[Strategy]:
    desc = dict(entry=f"Model probability ≥ {cfg.entry_p:.2f} that the stock beats its sector ETF over the next "
                      f"{cfg.horizon} days, and among the top {cfg.max_positions}",
                origin="Gradient-boosted trees over every rule's indicator plus fundamentals, tariff/rate shocks and macro; "
                       f"retrained every {cfg.fold_length} trading days on past data only")
    fixed = lambda _p: weights  # noqa: E731 - weights were computed walk-forward already
    out = [
        Strategy(AI_KEY, "AI", "AI decides (gradient boosting)", exit=f"Probability falls below {cfg.exit_p:.2f}",
                 sizing="WEIGHTS", fn=fixed, params=cfg.params(), **desc),
        Strategy(AI_KEY + "_TSTOP10", "AI", "AI decides + 10% trailing stop",
                 exit=f"Probability below {cfg.exit_p:.2f}, or the close falls 10% below its high since entry",
                 sizing="WEIGHTS", fn=fixed, params=cfg.params(), trailing_stop=0.10, **desc),
    ]
    if fund_weights is not None:
        out.append(Strategy(AI_FUND_KEY, "AI", "AI on financial reports only",
                            entry=desc["entry"], exit=f"Probability falls below {cfg.exit_p:.2f}",
                            origin="Gradient-boosted trees over the quarterly/annual report profile only: growth, earnings and "
                                   "revenue surprise, margins, balance sheet, valuation and days since the report; "
                                   f"retrained every {cfg.fold_length} trading days on past data only",
                            sizing="WEIGHTS", fn=lambda _p: fund_weights, params={**cfg.params(), "features": FUND_MODEL_FEATURES}))
    if div_weights is not None:
        out.append(Strategy(AI_DIV_KEY, "AI", "AI decides + dividend signals",
                            entry=desc["entry"], exit=f"Probability falls below {cfg.exit_p:.2f}",
                            origin=desc["origin"] + "; adds dividend yield, the change in the regular dividend (raise, cut, "
                                   "suspension) and the filed payout ratio, to compare with the same model without them",
                            sizing="WEIGHTS", fn=lambda _p: div_weights, params={**cfg.params(), "features": AI_DIV_FEATURES}))
    return out


# --------------------------------------------------------------------------- explanation
def explain(model: AiModel, x: np.ndarray, medians: np.ndarray, top: int = 5, features: list[str] = AI_FEATURES) -> list[dict]:
    """Per-feature effect: how much the probability moves if this feature were at its training median."""
    base = float(model.predict_proba(x[None, :])[0, 1])
    n = len(features)
    probe = np.repeat(x[None, :], n, axis=0)
    probe[np.arange(n), np.arange(n)] = medians
    alt = model.predict_proba(probe)[:, 1]
    contrib = base - alt
    order = np.argsort(-np.abs(contrib), kind="stable")[:top]
    return [{"feature": features[i], "label": feature_label(features[i]), "kind": feature_kind(features[i]),
             "value": None if np.isnan(x[i]) else float(x[i]), "median": None if np.isnan(medians[i]) else float(medians[i]),
             "contribution": float(contrib[i]), "direction": "UP" if contrib[i] > 0 else "DOWN"} for i in order]
