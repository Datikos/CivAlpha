"""The AI strategy: a gradient-boosted model that combines every theory's indicator and decides entries/exits.

Inputs per (date, company), all known at close(t):
  * the BASELINE forecasting model's features (features.build_rows): relative momentum, volatility, as-filed
    fundamentals, insider net buying, the last earnings reaction and guidance tone. The policy-event features (tariff
    and rate shocks through SEC-filing exposures, fed funds change x leverage) are left out since 2026-10-06: the lab
    showed the book does better without them (AI_WITH_EVENTS keeps them for comparison);
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

Four variants test the decision layer on the same probabilities, the way a discretionary trader works:
  * AI_CONF (abstention): enter only at a higher probability (confident_entry_p) and exit at a higher one
    (confident_exit_p); it trades less and sits in cash more.
  * AI_SIZED (position sizing): the same entries and exits as AI_GBM, but each position is sized by its
    21-day volatility (size_by_volatility): vol_budget / vol_21 of capital, capped at max_weight, total
    capped at 1. A calm stock gets more, a volatile one less, and the book shrinks when markets get wild.
  * AI_RANK (the book follows the ranking): the standard thresholds plus a replacement rule. When the book is
    full and a stock outside it clears entry_p and beats the weakest holding's probability by swap_margin, the
    weakest holding is sold and the newcomer bought. Without it the book is path-dependent: a holding drifting at
    p = 0.50 keeps its slot while a p = 0.65 candidate waits for an exit.
  * AI_RANK_SIZED (ranking + conviction sizing): AI_RANK's positions sized by volatility and then tilted by
    conviction (size_by_conviction): the volatility size times (p - 0.5) / (entry_p - 0.5), so a p = 0.65 name
    gets three times the slice of a p = 0.55 name before the max_weight cap.
  * AI_RANK_VOL: AI_RANK sized by volatility only, so the lab can separate the swap rule from the tilt.

The book the platform records daily is BOOK_KEY (AI_SIZED since 2026-10-06; AI_RANK_SIZED on 2026-10-05, AI_GBM
before): the lab priced the replacement rule at about 0.07 Sharpe and the conviction tilt at a further 0.03 with a
worse drawdown, so the recorded book keeps the standard thresholds and sizes by volatility.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from ..dividends import DIV_FEATURES, DIV_LABELS
from ..earnings import EARNINGS_LABELS
from ..evaluation import EvalConfig, train_mask, walk_forward_folds
from ..features import AUGMENTED_FEATURES, BASELINE_FEATURES, EVENT_FEATURES, FEATURE_KIND, FEATURE_LABELS
from ..fundamentals import FUND_FEATURES, FUND_LABELS
from .base import Strategy, prior_high, prior_low, rolling_std, rsi, sma
from .panel import MarketPanel

TECH_FEATURES = ["dist_sma50", "dist_sma200", "rsi2", "rsi14", "boll_z", "donchian_pos", "mom_12_1", "rev_5d", "vol_21", "dd_52w"]
INSIDER_PANEL_FEATURES = ["insider_buyers_21d", "insider_sellers_21d"]     # insider_net_63d already comes with AUGMENTED_FEATURES
EARNINGS_PANEL_FEATURES = ["days_since_earnings", "days_to_earnings_est"]  # earn_react_last and guidance_last come with AUGMENTED_FEATURES
INSIDER_LABELS = {"insider_buyers_21d": "Insiders buying in the open market, last 21 days (distinct)",
                  "insider_sellers_21d": "Insiders selling in the open market, last 21 days (distinct)"}
AI_FEATURES = BASELINE_FEATURES + TECH_FEATURES + FUND_FEATURES + INSIDER_PANEL_FEATURES + EARNINGS_PANEL_FEATURES
# the same model with the tariff / rate-shock / fed-funds x leverage features (the AUGMENTED forecast model's additions)
AI_EVENTS_FEATURES = AI_FEATURES + EVENT_FEATURES
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
AI_CONF_KEY = "AI_CONF"
AI_SIZED_KEY = "AI_SIZED"
AI_RANK_KEY = "AI_RANK"
AI_RANK_SIZED_KEY = "AI_RANK_SIZED"
AI_RANK_VOL_KEY = "AI_RANK_VOL"         # AI_RANK sized by volatility only (tilt fixed at 1): isolates the swap rule from the tilt
AI_WITH_EVENTS_KEY = "AI_WITH_EVENTS"   # AI_GBM trained with the policy-event features too: is AUGMENTED's weakness in the book too?
BOOK_KEY = AI_SIZED_KEY             # the variant whose decisions are recorded every day
RANK_SWAP_MARGIN = 0.08
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
    confident_entry_p: float = 0.60   # AI_CONF: a higher bar to enter...
    confident_exit_p: float = 0.50    # ...and to stay
    vol_budget: float = 0.04          # AI_SIZED: weight = vol_budget / annualized 21-day volatility
    max_weight: float = 0.20          # AI_SIZED: cap per position
    swap_margin: float | None = None  # AI_RANK: replace the weakest holding when a candidate beats its p by this much (None = never)
    tilt_floor: float = 0.5           # AI_RANK_SIZED: conviction tilt (p - 0.5) / (entry_p - 0.5) is clipped to [tilt_floor, tilt_cap]
    tilt_cap: float = 3.0
    max_iter: int = 150
    learning_rate: float = 0.05
    max_depth: int = 3
    min_samples_leaf: int = 200
    l2_regularization: float = 1.0
    seed: int = 7

    def params(self) -> dict:
        return {k: v for k, v in asdict(self).items()}

    def confident(self) -> "AiConfig":
        return AiConfig(**{**asdict(self), "entry_p": self.confident_entry_p, "exit_p": self.confident_exit_p})

    def ranked(self) -> "AiConfig":
        """The same thresholds with the replacement rule switched on (AI_RANK, AI_RANK_SIZED, the recorded book)."""
        return AiConfig(**{**asdict(self), "swap_margin": RANK_SWAP_MARGIN})


def feature_label(f: str) -> str:
    return TECH_LABELS.get(f) or FUND_LABELS.get(f) or DIV_LABELS.get(f) or INSIDER_LABELS.get(f) or EARNINGS_LABELS.get(f) or FEATURE_LABELS.get(f, f)


def feature_kind(f: str) -> str:
    if f in TECH_LABELS:
        return "TECHNICAL"
    if f in DIV_LABELS:
        return "DIVIDEND"
    if f in INSIDER_LABELS:
        return "INSIDER"
    if f in EARNINGS_PANEL_FEATURES:
        return "EARNINGS"
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
    ins = p.insiders()
    for name in INSIDER_PANEL_FEATURES:
        df[name] = ins[name].to_numpy(float)[cal_pos, col_pos]
    earn = p.earnings()
    for name in EARNINGS_PANEL_FEATURES:
        df[name] = earn[name].to_numpy(float)[cal_pos, col_pos]
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
    """Run the entry/exit rule day by day. Returns (weights, actions) over the whole calendar.

    Order within a day: exits (p below exit_p, or no longer a member / no probability), then entries into free slots from
    the top of the ranking, then, with cfg.swap_margin set, replacements: once the book is full, the weakest holding leaves
    when the best remaining outsider clears entry_p and beats it by swap_margin, repeated while that holds. A replaced
    holding is an EXIT like any other; the live decision marks it as replaced because its p is still at or above exit_p."""
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
        if cfg.swap_margin is not None:
            # the book is full (or nothing else qualifies): let the best outsider replace the weakest holding while it
            # beats it by the margin, best outsider first
            outsiders = [j for j in order if valid[j] and not held[j] and p[j] >= cfg.entry_p]
            while held.sum() >= cfg.max_positions and outsiders:
                held_idx = np.flatnonzero(held)
                weakest = held_idx[np.argmin(p[held_idx])]
                if p[outsiders[0]] < p[weakest] + cfg.swap_margin:
                    break
                held[weakest] = False
                held[outsiders.pop(0)] = True
        W[t] = np.where(held, 1.0 / cfg.max_positions, 0.0)
        A[t] = np.where(held & ~was, "ENTER", np.where(~held & was, "EXIT", np.where(held, "HOLD", "STAY_OUT")))
        A[t][~M[t] & ~was] = ""
    return pd.DataFrame(W, index=prob.index, columns=prob.columns), pd.DataFrame(A, index=prob.index, columns=prob.columns)


def _vol_slices(weights: pd.DataFrame, vol: pd.DataFrame, cfg: AiConfig) -> pd.DataFrame:
    """vol_budget / vol per held name, capped at max_weight; a held name without a volatility estimate keeps its equal slice."""
    held = weights > 0
    v = vol.reindex(index=weights.index, columns=weights.columns).astype(float)
    sized = (cfg.vol_budget / v.where(v > 0)).clip(upper=cfg.max_weight)
    return sized.where(sized.notna(), 1.0 / cfg.max_positions).where(held, 0.0)


def _no_leverage(sized: pd.DataFrame) -> pd.DataFrame:
    tot = sized.sum(axis=1)
    return sized.div(tot.where(tot > 1.0, 1.0), axis=0)


def size_by_volatility(weights: pd.DataFrame, vol: pd.DataFrame, cfg: AiConfig) -> pd.DataFrame:
    """Replace the equal 1/max_positions slices with volatility-scaled sizes.

    A held name gets vol_budget / vol of capital (vol = annualized 21-day volatility known at that close), capped at
    max_weight, so riskier names get less and the invested total falls when volatility rises. If the total would
    exceed 1 every position is scaled down (no leverage). A held name without a volatility estimate keeps its
    equal slice. Names the decision rule does not hold stay at 0, so entries and exits are unchanged.
    """
    return _no_leverage(_vol_slices(weights, vol, cfg))


def conviction_tilt(p, cfg: AiConfig):
    """How much more than a marginal entry (p = entry_p) a name deserves: (p - 0.5) / (entry_p - 0.5), clipped.

    1.0 at the entry threshold, 3.0 at p = 0.65 for the default 0.55 threshold; never below tilt_floor so a holding that
    drifted towards the exit still carries some capital, never above tilt_cap. Works on scalars and DataFrames."""
    return np.clip((p - 0.5) / (cfg.entry_p - 0.5), cfg.tilt_floor, cfg.tilt_cap)


def size_by_conviction(weights: pd.DataFrame, vol: pd.DataFrame, prob: pd.DataFrame, cfg: AiConfig) -> pd.DataFrame:
    """Volatility sizes (size_by_volatility) tilted by conviction: each held name's slice is multiplied by
    conviction_tilt(p), capped again at max_weight, and the book is scaled down when it would exceed 1 (no leverage).
    A held name without a probability that day keeps its volatility size (tilt 1)."""
    held = weights > 0
    pr = prob.reindex(index=weights.index, columns=weights.columns).astype(float)
    tilt = pd.DataFrame(conviction_tilt(pr, cfg), index=weights.index, columns=weights.columns).where(pr.notna(), 1.0)
    sized = (_vol_slices(weights, vol, cfg) * tilt).clip(upper=cfg.max_weight).where(held, 0.0)
    return _no_leverage(sized)


def sized_weight(weight: float, vol: float, cfg: AiConfig) -> float:
    """One position's volatility-scaled size (see size_by_volatility), before the no-leverage cap."""
    if weight <= 0:
        return 0.0
    if not np.isfinite(vol) or vol <= 0:
        return float(weight)
    return float(min(cfg.max_weight, cfg.vol_budget / vol))


def conviction_weight(weight: float, vol: float, p: float, cfg: AiConfig) -> float:
    """One position's conviction-tilted volatility size (see size_by_conviction), before the no-leverage cap."""
    if weight <= 0:
        return 0.0
    tilt = float(conviction_tilt(p, cfg)) if np.isfinite(p) else 1.0
    return float(min(cfg.max_weight, sized_weight(weight, vol, cfg) * tilt))


def ai_strategies(cfg: AiConfig, weights: pd.DataFrame, fund_weights: pd.DataFrame | None = None,
                  div_weights: pd.DataFrame | None = None, conf_weights: pd.DataFrame | None = None,
                  sized_weights: pd.DataFrame | None = None, rank_weights: pd.DataFrame | None = None,
                  rank_sized_weights: pd.DataFrame | None = None, rank_vol_weights: pd.DataFrame | None = None,
                  events_weights: pd.DataFrame | None = None) -> list[Strategy]:
    desc = dict(entry=f"Model probability ≥ {cfg.entry_p:.2f} that the stock beats its sector ETF over the next "
                      f"{cfg.horizon} days, and among the top {cfg.max_positions}",
                origin="Gradient-boosted trees over every rule's indicator plus the report profile, insider activity and the "
                       f"last earnings reaction; retrained every {cfg.fold_length} trading days on past data only")
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
    if conf_weights is not None:
        out.append(Strategy(AI_CONF_KEY, "AI", "AI decides, confident entries only",
                            entry=f"Model probability ≥ {cfg.confident_entry_p:.2f} that the stock beats its sector ETF over the "
                                  f"next {cfg.horizon} days, and among the top {cfg.max_positions}",
                            exit=f"Probability falls below {cfg.confident_exit_p:.2f}",
                            origin="The same model and probabilities as AI_GBM with a higher bar to act (abstention): it waits "
                                   "in cash unless the model is more sure, the way a trader passes on marginal setups",
                            sizing="WEIGHTS", fn=lambda _p: conf_weights,
                            params={**cfg.params(), "entry_p": cfg.confident_entry_p, "exit_p": cfg.confident_exit_p}))
    if sized_weights is not None:
        out.append(Strategy(AI_SIZED_KEY, "AI", "AI decides, sized by volatility",
                            entry=desc["entry"], exit=f"Probability falls below {cfg.exit_p:.2f}",
                            origin="The same entries and exits as AI_GBM; each position is sized by its 21-day volatility "
                                   f"({cfg.vol_budget:.2f} / annualized volatility, at most {cfg.max_weight:.0%} of capital, "
                                   "no leverage) so every position carries about the same risk and the book shrinks "
                                   "when markets turn volatile (volatility targeting). This is the book recorded on the "
                                   "AI decisions page",
                            sizing="WEIGHTS", fn=lambda _p: sized_weights, params=cfg.params()))
    rcfg = cfg.ranked()
    swap = (f"Probability falls below {cfg.exit_p:.2f}, or a stock outside the book clears {cfg.entry_p:.2f} and beats this "
            f"holding's probability by {rcfg.swap_margin:.2f} (replacement)")
    if rank_weights is not None:
        out.append(Strategy(AI_RANK_KEY, "AI", "AI decides, book follows its ranking",
                            entry=desc["entry"] + f", or beats the weakest holding's probability by {rcfg.swap_margin:.2f}",
                            exit=swap,
                            origin="The same model and thresholds as AI_GBM plus a replacement rule, so the book tracks today's "
                                   "ranking instead of the order in which stocks happened to cross the entry threshold; the "
                                   "margin keeps it from churning on noise",
                            sizing="WEIGHTS", fn=lambda _p: rank_weights, params=rcfg.params()))
    if rank_sized_weights is not None:
        out.append(Strategy(AI_RANK_SIZED_KEY, "AI", "AI decides, ranking + conviction sizing",
                            entry=desc["entry"] + f", or beats the weakest holding's probability by {rcfg.swap_margin:.2f}",
                            exit=swap,
                            origin="AI_RANK's positions sized by volatility (as AI_SIZED) and tilted by conviction: the size is "
                                   f"multiplied by (p - 0.5) / ({cfg.entry_p:.2f} - 0.5), between {cfg.tilt_floor:g}x and "
                                   f"{cfg.tilt_cap:g}x, then capped at {cfg.max_weight:.0%} with no leverage",
                            sizing="WEIGHTS", fn=lambda _p: rank_sized_weights, params=rcfg.params()))
    if rank_vol_weights is not None:
        out.append(Strategy(AI_RANK_VOL_KEY, "AI", "AI decides, ranking + volatility sizing",
                            entry=desc["entry"] + f", or beats the weakest holding's probability by {rcfg.swap_margin:.2f}",
                            exit=swap,
                            origin="AI_RANK's positions sized by volatility exactly as AI_SIZED, with no conviction tilt, so the "
                                   "gap to AI_SIZED is the price of the replacement rule alone and the gap to AI_RANK_SIZED is "
                                   "the effect of the tilt alone",
                            sizing="WEIGHTS", fn=lambda _p: rank_vol_weights, params={**rcfg.params(), "tilt_floor": 1.0, "tilt_cap": 1.0}))
    if events_weights is not None:
        out.append(Strategy(AI_WITH_EVENTS_KEY, "AI", "AI decides + policy-event features",
                            entry=desc["entry"], exit=f"Probability falls below {cfg.exit_p:.2f}",
                            origin="The standard rule on a model that also sees the tariff shock, rate shock and fed-funds x "
                                   "leverage features (the ones the AUGMENTED forecast model adds and that make it worse); the "
                                   "book dropped them on 2026-10-06 after this row underperformed AI_GBM without them",
                            sizing="WEIGHTS", fn=lambda _p: events_weights, params={**cfg.params(), "features": AI_EVENTS_FEATURES}))
    return out


# --------------------------------------------------------------------------- explanation
def explain(model: AiModel, x: np.ndarray, medians: np.ndarray, top: int = 5, features: list[str] = AI_FEATURES) -> list[dict]:
    """Per-feature effect: how much the probability moves if this feature were at its training median.

    A missing input (NaN) is reported with value None, imputed=True and an `imputation` note; its contribution is real
    (the trees treat absence as information), so it is kept rather than zeroed."""
    base = float(model.predict_proba(x[None, :])[0, 1])
    n = len(features)
    probe = np.repeat(x[None, :], n, axis=0)
    probe[np.arange(n), np.arange(n)] = medians
    alt = model.predict_proba(probe)[:, 1]
    contrib = base - alt
    order = np.argsort(-np.abs(contrib), kind="stable")[:top]
    missing = np.isnan(x)
    return [{"feature": features[i], "label": feature_label(features[i]), "kind": feature_kind(features[i]),
             "value": None if missing[i] else float(x[i]), "median": None if np.isnan(medians[i]) else float(medians[i]),
             "contribution": float(contrib[i]), "direction": "UP" if contrib[i] > 0 else "DOWN",
             "imputed": bool(missing[i]), "imputation": _imputation(model, i) if missing[i] else None} for i in order]


def _imputation(model: AiModel, i: int) -> str:
    """How a missing value entered the model, so the explanation never shows a null value next to a non-zero contribution.
    Missing values are not filled before training: HistGradientBoostingClassifier learns, per split, which branch the
    missing values take (native handling), so the contribution of a missing feature is the effect of *having no value*
    against the training median. A feature with no values at all in the training window is set to 0 and ignored."""
    if i < len(model.empty) and model.empty[i]:
        return "constant 0: the feature had no values in the training window"
    return "missing-value branch: the trees learned where samples without this value go; the contribution is the effect of having no value, versus the training median"
