"""ADR-0004: the owner's holdings and the platform's advice on each of them.

Every holding gets one action per trading day, decided by the first rule that fires (`advise_holding`):

  NOT_COVERED  the symbol is not in the universe, has no close on the as-of date, or the book made no decision on it;
  REVIEW       the data is suspect: 21-day volatility above integrity.VOL_THRESHOLD, a raw one-day move beyond
               LARGE_MOVE in the last REVIEW_PRICE_DAYS with no corporate action that day, or an unreconciled
               corporate-action candidate (ADR-0003) in the last CANDIDATE_DAYS;
  TRIM         weight above min(max_weight, TRIM_BAND x volatility size): sell down to the volatility size;
  SELL         the book's probability is below exit_p;
  ADD          probability at or above entry_p and weight below ADD_BAND x volatility size: buy up to it, within cash;
  HOLD         nothing fired.

DATA and RISK actions are risk control and need no forecast skill. MODEL actions (SELL, ADD) are shown as the headline
only once the book model (`service.BOOK_KIND`) passes the pre-registered live test; until then the headline is HOLD
and the model's action is shown beside it as an unproven opinion.

The advice reads the recorded book's decision rows (`strategy_decision`, one per universe member per day): probability,
calibrated probability, rank, thresholds and 21-day volatility are the book's own. Rows are append-only; `basis` hashes
the portfolio state so an edit during the day adds rows instead of changing them. Holdings, values and advice never go
into a language-model prompt or an INFO log line.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from .. import integrity
from ..returns import HORIZON
from ..strategies.ai import AiConfig, sized_weight
from .decisions import LARGE_MOVE
from . import auth
from .auth import LOCAL, Caller
from .errors import BadRequest, Forbidden, NotFound, Unauthorized
from .rows import camel, camel_all, value
from .sql import Db, db, jsonb
from .tickers import TickerResolver

CODE_VERSION = "portfolio-advice-1"
DEFAULT_PORTFOLIO = "default"      # the portfolio a user's first holding or cash entry creates
TRIM_BAND = 1.5                     # trim when the weight exceeds this multiple of the volatility size...
ADD_BAND = 0.67                     # ...add when it is below this multiple; the gap keeps the advice from flipping daily
REVIEW_PRICE_DAYS = 45              # calendar days of raw closes checked for an unexplained move
CANDIDATE_DAYS = 120                # calendar days a corporate-action candidate keeps a holding under REVIEW
JEV_FLAG_P = 0.5
MIN_TRACK_ROWS = 30                 # resolved rows per action before the track record shows a mean and interval
TRACK_BOOT = 2000
TRACK_SEED = 20261007
MAX_HOLDINGS = 200
MAX_PORTFOLIOS = 20
RESOLVE_AFTER_DAYS = 29             # 21 trading days span at least 29 calendar days: no bundle load before a row can resolve
ACTIONS = ("NOT_COVERED", "REVIEW", "TRIM", "SELL", "ADD", "HOLD")
LAYER = {"NOT_COVERED": "DATA", "REVIEW": "DATA", "TRIM": "RISK", "SELL": "MODEL", "ADD": "MODEL", "HOLD": "NONE"}
NOTE = ("Research software, not investment advice; it places no orders. DATA and RISK actions are risk control "
        "(sizing, concentration, broken data) and do not claim to raise returns. MODEL actions rest on a forecast of "
        "whether the stock beats its sector ETF over the next 21 trading days, graded by the pre-registered live test. "
        "Nothing is said beyond 21 trading days.")


# --------------------------------------------------------------------------- the rules (pure, no database)
def review_date(as_of: date, horizon: int = HORIZON) -> date:
    """as_of + `horizon` weekdays: the end of the forecast window. Exchange holidays are not known ahead, so the window
    can end a day or two later than this."""
    return (pd.Timestamp(as_of) + pd.offsets.BDay(horizon)).date()


def volatility_size(vol21: float | None, cfg: AiConfig) -> float:
    """The book's size for one position (ai.sized_weight): min(max_weight, vol_budget / vol21); the equal slice
    1 / max_positions when the volatility is unknown, as the book does."""
    if vol21 is None or not math.isfinite(vol21) or vol21 <= 0:
        return 1.0 / cfg.max_positions
    return sized_weight(1.0, vol21, cfg)


def holding_value(shares: float, close: float | None, avg_cost: float) -> float:
    """Shares at the as-of close; at average cost when there is no close (a symbol outside the universe)."""
    return shares * (close if close is not None else avg_cost)


def advise_holding(h: dict, close: float | None, decision: dict | None, flags: list[dict], total: float, cash: float,
                   live_test: dict, cfg: AiConfig, as_of: date) -> dict:
    """One holding's advice. `h` has symbol, companyId, shares, avgCostUsd; `decision` is the book's row for the stock
    (probability, probabilityCalibrated, rank, entryP, exitP, action, id, strategyKey, vol21) or None; `flags` are the
    data flags found for it; `total` is the portfolio value (holdings + cash)."""
    shares = float(h["shares"])
    val = holding_value(shares, close, float(h["avgCostUsd"]))
    weight = val / total if total > 0 else 0.0
    reasons: list[dict] = []

    def fired(rule: str, action: str, text: str, **numbers) -> None:
        reasons.append({"rule": rule, "action": action, "layer": LAYER[action], "text": text, **numbers})

    if h.get("companyId") is None:
        fired("NOT_IN_UNIVERSE", "NOT_COVERED", f"{h['symbol']} is not in the universe: no prices, no forecast. Add the company "
              "on the Universe page and the next advice covers it.")
    elif close is None:
        fired("NO_PRICE", "NOT_COVERED", f"No close for {h['symbol']} on {as_of}; valued at average cost.")
    elif decision is None:
        fired("NO_DECISION", "NOT_COVERED", f"The book made no decision on {h['symbol']} for {as_of} (not a member that day).")
    for f in flags:
        fired(f["rule"], "REVIEW", f["text"], **{k: v for k, v in f.items() if k not in ("rule", "text")})

    model: dict = {"liveTest": live_test, "proven": live_test.get("verdict") == "PASS"}
    target = None
    triggers: list[dict] = []
    if decision is not None and close is not None:
        vol = decision.get("vol21")
        target = volatility_size(vol, cfg)
        cap = min(cfg.max_weight, TRIM_BAND * target)
        floor_w = ADD_BAND * target
        p = float(decision["probability"])
        entry_p, exit_p = float(decision["entryP"]), float(decision["exitP"])
        model.update({"decisionId": decision.get("id"), "decisionKey": decision.get("strategyKey"), "bookAction": decision.get("action"),
                      "probability": p, "probabilityCalibrated": decision.get("probabilityCalibrated"),
                      "rank": decision.get("rank"), "entryP": entry_p, "exitP": exit_p, "vol21": vol, "horizon": HORIZON})
        if weight > cap:
            fired("OVER_CAP" if weight > cfg.max_weight else "OVER_RISK_SIZE", "TRIM",
                  f"Weight {weight:.1%} is above {cap:.1%} (the {cfg.max_weight:.0%} cap or {TRIM_BAND}x its volatility size "
                  f"{target:.1%}); trim to {target:.1%}.", weight=weight, limit=cap, target=target)
        if p < exit_p:
            fired("MODEL_EXIT", "SELL", f"The book's probability {p:.2f} is below its exit threshold {exit_p:.2f}.",
                  probability=p, threshold=exit_p)
        elif p >= entry_p and weight < floor_w:
            fired("MODEL_ADD", "ADD", f"The book's probability {p:.2f} clears its entry threshold {entry_p:.2f} and the weight "
                  f"{weight:.1%} is below {floor_w:.1%} ({ADD_BAND}x its volatility size {target:.1%}).",
                  probability=p, threshold=entry_p, weight=weight, target=target)
        triggers = [
            {"action": "SELL", "when": f"probability below {exit_p:.2f}", "now": round(p, 4)},
            {"action": "TRIM", "when": f"weight above {cap:.1%}", "now": round(weight, 4)},
            {"action": "ADD", "when": f"probability at least {entry_p:.2f} and weight below {floor_w:.1%}", "now": round(weight, 4)},
        ]

    first = min(reasons, key=lambda r: ACTIONS.index(r["action"])) if reasons else None
    action = first["action"] if first else "HOLD"
    rule = first["rule"] if first else "NONE"
    trade = None
    if action == "TRIM":
        trade = -float(math.floor((val - target * total) / close)) if close else None
    elif action == "SELL":
        trade = -shares
    elif action == "ADD":
        want = max(0.0, (target - weight) * total)
        trade = float(math.floor(min(want, cash) / close))
        if trade < 1:
            reasons.append({"rule": "NO_CASH", "action": "ADD", "layer": "MODEL",
                            "text": f"Cash ${cash:,.0f} buys no whole share at ${close:,.2f}; enter cash to size the addition."})
    if trade is not None and trade == 0:
        trade = None
    headline = action
    if LAYER[action] == "MODEL" and not model["proven"]:
        headline = "HOLD"
    return {"symbol": h["symbol"], "companyId": h.get("companyId"), "shares": shares, "close": close, "valueUsd": val,
            "weight": weight, "action": action, "layer": LAYER[action], "headline": headline, "rule": rule,
            "targetWeight": target, "tradeShares": trade, "reviewOn": review_date(as_of).isoformat(), "triggers": triggers,
            "reasons": reasons, "model": model}


def basis_of(holdings: list[dict], cash: float) -> str:
    """A short hash of the portfolio state the advice is computed on."""
    key = "|".join(f"{h['symbol']}:{Decimal(str(h['shares'])).normalize()}:{Decimal(str(h['avgCostUsd'])).normalize()}"
                   for h in sorted(holdings, key=lambda x: x["symbol"]))
    return hashlib.sha256(f"{key}|cash:{Decimal(str(cash)).normalize()}".encode()).hexdigest()[:16]


def cluster_ci(values: np.ndarray, clusters: np.ndarray, n_boot: int = TRACK_BOOT, seed: int = TRACK_SEED) -> list[float]:
    """95% bootstrap interval of the mean, resampling whole symbols (one holding's daily rows overlap in time)."""
    rng = np.random.default_rng(seed)
    keys = np.unique(clusters)
    groups = [values[clusters == k] for k in keys]
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        means.append(float(np.concatenate([groups[i] for i in pick]).mean()))
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


# --------------------------------------------------------------------------- the store and the daily run
@dataclass
class AdviseResult:
    as_of: date | None = None
    created: int = 0
    existing: int = 0
    portfolios: int = 0


class PortfolioService:
    """One caller's portfolios (ADR-0005): every statement is scoped to `owner_user_id IS NOT DISTINCT FROM` the caller's
    user id. The installation's own portfolios (owner NULL) belong to the local operator and the admin token. A
    portfolio id of someone else answers 404. The pipeline uses SYSTEM, which may advise every portfolio."""

    def __init__(self, database: Db | None = None, cfg: AiConfig | None = None, live_test_fn=None, decisions_fn=None,
                 caller: Caller = LOCAL):
        if not caller.signed_in:
            raise Unauthorized("sign in required")
        self.db = database or db()
        self.cfg = cfg or AiConfig()
        self.live_test_fn = live_test_fn or _book_live_test
        self.decisions_fn = decisions_fn or _book_decisions
        self.caller = caller
        self.owner = caller.user_id

    # ---------------------------------------------------------------- portfolios
    def portfolios(self) -> list[dict]:
        return camel_all(self.db.all("""
            SELECT p.id, p.name, p.cash_usd, p.created_at, p.updated_at, count(h.id) AS holdings
            FROM portfolio p LEFT JOIN holding h ON h.portfolio_id = p.id
            WHERE p.owner_user_id IS NOT DISTINCT FROM :o GROUP BY p.id ORDER BY p.id""", o=self.owner))

    def create_portfolio(self, name: str) -> dict:
        name = (name or "").strip()
        if not 1 <= len(name) <= 60:
            raise BadRequest("name: 1 to 60 characters")
        if len(self.portfolios()) >= MAX_PORTFOLIOS:
            raise BadRequest(f"at most {MAX_PORTFOLIOS} portfolios")
        with self.db.transaction():
            pid = self.db.scalar("INSERT INTO portfolio (name, owner_user_id) VALUES (:n, :o) ON CONFLICT DO NOTHING RETURNING id",
                                 n=name, o=self.owner)
            if pid is None:
                raise BadRequest(f"you already have a portfolio named {name}")
            auth.record(self.db, self.caller, "PORTFOLIO_CREATED", "portfolio", pid, after={"name": name}, portfolio_id=pid)
        return next(p for p in self.portfolios() if p["id"] == pid)

    def portfolio_id(self, pid: int | None = None, create: bool = False) -> int | None:
        """The caller's portfolio `pid` (404 when it is not theirs), or their first one; `create` makes a portfolio named
        "default" when they have none."""
        if pid is not None:
            if self.db.scalar("SELECT 1 FROM portfolio WHERE id = :p AND owner_user_id IS NOT DISTINCT FROM :o", p=pid, o=self.owner) is None:
                raise NotFound(f"portfolio {pid} not found")
            return pid
        found = self.db.scalar("SELECT min(id) FROM portfolio WHERE owner_user_id IS NOT DISTINCT FROM :o", o=self.owner)
        if found is None and create:
            self.db.execute("INSERT INTO portfolio (name, owner_user_id) VALUES (:n, :o) ON CONFLICT DO NOTHING", n=DEFAULT_PORTFOLIO, o=self.owner)
            found = self.db.scalar("SELECT min(id) FROM portfolio WHERE owner_user_id IS NOT DISTINCT FROM :o", o=self.owner)
        return found

    def _name(self, pid: int | None) -> dict | None:
        return None if pid is None else camel(self.db.one("SELECT id, name FROM portfolio WHERE id = :p", p=pid))

    # ---------------------------------------------------------------- holdings
    def holdings(self, pid: int | None = None) -> dict:
        pid = self.portfolio_id(pid)
        if pid is None:
            return {"portfolio": None, "cashUsd": 0.0, "holdings": []}
        return {"portfolio": self._name(pid), **self._state(pid)}

    def _state(self, pid: int) -> dict:
        cash = self.db.scalar("SELECT cash_usd FROM portfolio WHERE id = :p", p=pid)
        rows = camel_all(self.db.all("""
            SELECT h.symbol, h.company_id, c.name, h.shares, h.avg_cost_usd, h.opened_on, h.note, h.created_at, h.updated_at
            FROM holding h LEFT JOIN company c ON c.id = h.company_id WHERE h.portfolio_id = :p ORDER BY h.symbol""", p=pid))
        return {"cashUsd": float(cash), "holdings": rows}

    def set_holding(self, symbol: str, shares, avg_cost, opened_on: date | None = None, note: str | None = None,
                    pid: int | None = None) -> dict:
        sym = _symbol(symbol)
        shares, avg_cost = _number(shares, "shares", positive=True), _number(avg_cost, "avgCostUsd")
        if note is not None and len(note) > 500:
            raise BadRequest("note: at most 500 characters")
        if opened_on is not None and opened_on > date.today():
            raise BadRequest("openedOn: a date in the future")
        cid = TickerResolver(self.db).company_ever(sym)
        with self.db.transaction():
            pid = self.portfolio_id(pid, create=True)
            n = self.db.scalar("SELECT count(*) FROM holding WHERE portfolio_id = :p AND symbol <> :s", p=pid, s=sym)
            if n >= MAX_HOLDINGS:
                raise BadRequest(f"at most {MAX_HOLDINGS} holdings")
            before = self._holding(pid, sym)
            self.db.execute("""
                INSERT INTO holding (portfolio_id, symbol, company_id, shares, avg_cost_usd, opened_on, note)
                VALUES (:p, :s, :c, :sh, :ac, :o, :n)
                ON CONFLICT (portfolio_id, symbol) DO UPDATE SET company_id = EXCLUDED.company_id, shares = EXCLUDED.shares,
                    avg_cost_usd = EXCLUDED.avg_cost_usd, opened_on = EXCLUDED.opened_on, note = EXCLUDED.note, updated_at = now()""",
                p=pid, s=sym, c=cid, sh=shares, ac=avg_cost, o=opened_on, n=(note or None))
            auth.record(self.db, self.caller, "HOLDING_SET", "holding", sym, before=before, after=self._holding(pid, sym), portfolio_id=pid)
        return next(h for h in self._state(pid)["holdings"] if h["symbol"] == sym)

    def _holding(self, pid: int, sym: str) -> dict | None:
        r = self.db.one("SELECT symbol, shares, avg_cost_usd, opened_on, note FROM holding WHERE portfolio_id = :p AND symbol = :s", p=pid, s=sym)
        return None if r is None else camel(r)

    def remove_holding(self, symbol: str, pid: int | None = None) -> None:
        sym = _symbol(symbol)
        with self.db.transaction():
            pid = self.portfolio_id(pid)
            before = None if pid is None else self._holding(pid, sym)
            if before is None:
                raise NotFound(f"no holding {sym}")
            self.db.execute("DELETE FROM holding WHERE portfolio_id = :p AND symbol = :s", p=pid, s=sym)
            auth.record(self.db, self.caller, "HOLDING_REMOVED", "holding", sym, before=before, portfolio_id=pid)

    def set_cash(self, cash, pid: int | None = None) -> float:
        cash = _number(cash, "cashUsd")
        with self.db.transaction():
            pid = self.portfolio_id(pid, create=True)
            before = float(self.db.scalar("SELECT cash_usd FROM portfolio WHERE id = :p", p=pid))
            self.db.execute("UPDATE portfolio SET cash_usd = :c, updated_at = now() WHERE id = :p", c=cash, p=pid)
            auth.record(self.db, self.caller, "CASH_SET", "portfolio", pid, before={"cashUsd": before}, after={"cashUsd": cash},
                        portfolio_id=pid)
        return cash

    # ---------------------------------------------------------------- advice
    def advise(self, as_of: date | None = None, pid: int | None = None) -> AdviseResult:
        """Advice for every holding of one of the caller's portfolios (default: the first) on the book's latest decision
        date (or `as_of`); idempotent per portfolio state."""
        pid = self.portfolio_id(pid)
        return AdviseResult() if pid is None else self._advise(pid, as_of)

    def advise_all(self, as_of: date | None = None) -> AdviseResult:
        """The pipeline (SYSTEM): every portfolio of an active user, and the installation's own ones."""
        if self.caller.kind != "SYSTEM":
            raise Forbidden("only the pipeline advises every portfolio")
        total = AdviseResult()
        for pid in self.db.scalars("""SELECT p.id FROM portfolio p LEFT JOIN app_user u ON u.id = p.owner_user_id
                                      WHERE p.owner_user_id IS NULL OR u.status = 'ACTIVE' ORDER BY p.id"""):
            r = self._advise(pid, as_of)
            total.as_of = r.as_of or total.as_of
            total.created += r.created
            total.existing += r.existing
            total.portfolios += 1 if r.as_of else 0
        return total

    def _advise(self, pid: int, as_of: date | None) -> AdviseResult:
        state = self._state(pid)
        holdings = state["holdings"]
        for h in holdings:     # a company added to the universe after the holding was entered is picked up here
            if h["companyId"] is None:
                h["companyId"] = TickerResolver(self.db).company_ever(h["symbol"])
                if h["companyId"] is not None:
                    self.db.execute("UPDATE holding SET company_id = :c WHERE portfolio_id = :p AND symbol = :s",
                                    c=h["companyId"], p=pid, s=h["symbol"])
        if not holdings:
            return AdviseResult()
        day, decisions = self.decisions_fn(as_of)
        if day is None:
            return AdviseResult()
        rows = self.compute(holdings, state["cashUsd"], day, decisions)
        basis = basis_of(holdings, state["cashUsd"])
        r = AdviseResult(as_of=day)
        for a in rows:
            if self._insert(pid, day, basis, a):
                r.created += 1
            else:
                r.existing += 1
        return r

    def compute(self, holdings: list[dict], cash: float, day: date, decisions: dict[int, dict]) -> list[dict]:
        cids = [h["companyId"] for h in holdings if h["companyId"] is not None]
        closes = self._closes(cids, day)
        flags = self._flags(cids, day, decisions)
        live = self.live_test_fn()
        values = [holding_value(float(h["shares"]), closes.get(h["companyId"]), float(h["avgCostUsd"])) for h in holdings]
        total = sum(values) + cash
        return [advise_holding(h, closes.get(h["companyId"]), decisions.get(h["companyId"]), flags.get(h["companyId"], []),
                               total, cash, live, self.cfg, day) for h in holdings]

    def _closes(self, cids: list[int], day: date) -> dict[int, float]:
        if not cids:
            return {}
        return {r["company_id"]: float(r["close"]) for r in self.db.all(
            "SELECT company_id, close FROM price_bar WHERE trade_date = :d AND company_id = ANY(:c)", d=day, c=cids)}

    def _flags(self, cids: list[int], day: date, decisions: dict[int, dict]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        if not cids:
            return out
        for cid in cids:
            vol = (decisions.get(cid) or {}).get("vol21")
            if vol is not None and vol > integrity.VOL_THRESHOLD:
                out.setdefault(cid, []).append({"rule": "BROKEN_VOLATILITY", "vol21": vol,
                                                "text": f"21-day volatility {vol:.0%} annualized is above {integrity.VOL_THRESHOLD:.0%}: "
                                                        "usually a broken price series, not a market move. Check the price chart."})
        bars = self.db.all("""SELECT company_id, trade_date, close::float8 AS close FROM price_bar
                              WHERE company_id = ANY(:c) AND trade_date BETWEEN :f AND :d ORDER BY company_id, trade_date""",
                           c=cids, f=day - timedelta(days=REVIEW_PRICE_DAYS), d=day)
        acts = {(r["company_id"], r["ex_date"]) for r in self.db.all(
            "SELECT company_id, ex_date FROM corporate_action WHERE company_id = ANY(:c) AND ex_date BETWEEN :f AND :d",
            c=cids, f=day - timedelta(days=REVIEW_PRICE_DAYS), d=day)}
        prev: dict[int, float] = {}
        for b in bars:
            cid, px = b["company_id"], b["close"]
            last = prev.get(cid)
            if last and last > 0 and abs(px / last - 1.0) >= LARGE_MOVE and (cid, b["trade_date"]) not in acts:
                out.setdefault(cid, []).append({"rule": "UNEXPLAINED_MOVE", "date": b["trade_date"].isoformat(), "move": px / last - 1.0,
                                                "text": f"The close moved {px / last - 1.0:+.0%} on {b['trade_date']} with no corporate "
                                                        "action recorded: a split or spin-off the price feed missed would look like this."})
            prev[cid] = px
        for c in self.db.all("""SELECT company_id, kind, method, accepted_at, filing_id FROM corporate_action_candidate
                                WHERE company_id = ANY(:c) AND kind <> 'NONE' AND reconciled_action_id IS NULL AND review <> 'REJECTED'
                                  AND accepted_at::date BETWEEN :f AND :d AND (method = 'KEYWORD' OR probability >= :jp)""",
                             c=cids, f=day - timedelta(days=CANDIDATE_DAYS), d=day, jp=JEV_FLAG_P):
            out.setdefault(c["company_id"], []).append({
                "rule": "CORPORATE_ACTION_GAP", "filingId": c["filing_id"], "kind": c["kind"],
                "text": f"An 8-K of {c['accepted_at'].date()} reads like a {c['kind'].lower().replace('_', ' ')} ({c['method'].lower()}) "
                        "and no matching corporate action is recorded. Confirm or reject it before trusting the numbers."})
        return out

    def _insert(self, pid: int, day: date, basis: str, a: dict) -> bool:
        return self.db.execute("""
            INSERT INTO holding_advice (portfolio_id, as_of_date, basis, symbol, company_id, shares, close, value_usd, weight,
                                        action, layer, headline, rule, target_weight, trade_shares, review_on, triggers, reasons,
                                        model, code_version)
            VALUES (:p, :d, :b, :s, :c, :sh, :cl, :v, :w, :a, :l, :hl, :r, :t, :ts, :ro, CAST(:tr AS jsonb), CAST(:re AS jsonb),
                    CAST(:m AS jsonb), :cv)
            ON CONFLICT (portfolio_id, as_of_date, basis, symbol) DO NOTHING""",
            p=pid, d=day, b=basis, s=a["symbol"], c=a["companyId"], sh=a["shares"], cl=a["close"], v=a["valueUsd"], w=a["weight"],
            a=a["action"], l=a["layer"], hl=a["headline"], r=a["rule"], t=a["targetWeight"], ts=a["tradeShares"],
            ro=date.fromisoformat(a["reviewOn"]), tr=jsonb(a["triggers"]), re=jsonb(a["reasons"]), m=jsonb(a["model"]),
            cv=CODE_VERSION) > 0

    # ---------------------------------------------------------------- reads
    def advice(self, day: date | None = None, pid: int | None = None) -> dict:
        """The newest advice of one day (default: the latest day) for the current holdings of one of the caller's
        portfolios, the change against the day before, the book's buy ideas and the portfolio totals."""
        pid = self.portfolio_id(pid)
        state = self._state(pid) if pid is not None else {"cashUsd": 0.0, "holdings": []}
        dates = [] if pid is None else self.db.scalars(
            "SELECT DISTINCT as_of_date FROM holding_advice WHERE portfolio_id = :p ORDER BY as_of_date DESC LIMIT 60", p=pid)
        d = day or (dates[0] if dates else None)
        rows = [] if d is None else self._day_rows(pid, d)
        held = {h["symbol"] for h in state["holdings"]}
        rows = [r for r in rows if r["symbol"] in held]
        prev_day = next((x for x in dates if d is not None and x < d), None)
        prev = {r["symbol"]: r["headline"] for r in self._day_rows(pid, prev_day)} if prev_day else {}
        for r in rows:
            r["previousHeadline"] = prev.get(r["symbol"])
            r["changed"] = r["symbol"] in prev and prev[r["symbol"]] != r["headline"]
        rows.sort(key=lambda r: (not r["changed"], ACTIONS.index(r["headline"]), -r["weight"]))
        latest_day, _ = self.decisions_fn(None)
        invested = sum(r["valueUsd"] for r in rows)
        total = invested + state["cashUsd"]
        return {"portfolio": self._name(pid), "asOfDate": value(d), "dates": [value(x) for x in dates], "latestDecisionDate": value(latest_day),
                "stale": d is not None and latest_day is not None and d < latest_day,
                "cashUsd": state["cashUsd"], "investedUsd": invested, "totalUsd": total,
                "missing": sorted(held - {r["symbol"] for r in rows}), "advice": rows,
                "ideas": self.ideas(d, held, total) if d else [], "liveTest": self.live_test_fn(), "note": NOTE}

    def _day_rows(self, pid: int | None, d: date | None) -> list[dict]:
        if pid is None or d is None:
            return []
        rows = camel_all(self.db.all("""
            WITH newest AS (SELECT basis FROM holding_advice WHERE portfolio_id = :p AND as_of_date = :d ORDER BY created_at DESC, id DESC LIMIT 1)
            SELECT a.id, a.as_of_date, a.symbol, a.company_id, c.name, a.shares, a.close, a.value_usd, a.weight, a.action, a.layer,
                   a.headline, a.rule, a.target_weight, a.trade_shares, a.review_on, a.triggers, a.reasons, a.model, a.created_at,
                   o.excess_return, o.window_end_date
            FROM holding_advice a LEFT JOIN company c ON c.id = a.company_id LEFT JOIN holding_advice_outcome o ON o.advice_id = a.id
            WHERE a.portfolio_id = :p AND a.as_of_date = :d AND a.basis = (SELECT basis FROM newest)""", p=pid, d=d))
        for r in rows:
            r["outcome"] = None if r["windowEndDate"] is None else {"excessReturn": r["excessReturn"], "windowEndDate": r["windowEndDate"]}
            del r["excessReturn"], r["windowEndDate"]
        return rows

    def ideas(self, d: date, held: set[str], total: float) -> list[dict]:
        """The recorded book's positions on day `d` (ENTER and HOLD) that the owner does not hold, sized as the book sizes them."""
        _, decisions = self.decisions_fn(d)
        live = self.live_test_fn()
        out = []
        for x in decisions.values():
            if x["action"] not in ("ENTER", "HOLD") or x["symbol"] in held:
                continue
            w = volatility_size(x.get("vol21"), self.cfg)
            out.append({"symbol": x["symbol"], "name": x.get("name"), "bookAction": x["action"], "probability": x["probability"],
                        "probabilityCalibrated": x.get("probabilityCalibrated"), "rank": x.get("rank"), "targetWeight": w,
                        "amountUsd": w * total if total > 0 else None, "proven": live.get("verdict") == "PASS"})
        return sorted(out, key=lambda r: -r["probability"])

    def resolve_outcomes(self, bundle_fn) -> dict:
        """Stores the 21-day excess return after every advice row whose window has closed. `bundle_fn` loads the data
        bundle (total-return indexes); it is only called when something is pending."""
        pending = self.db.all("""
            SELECT a.id, a.company_id, a.as_of_date, c.benchmark_symbol FROM holding_advice a JOIN company c ON c.id = a.company_id
            LEFT JOIN holding_advice_outcome o ON o.advice_id = a.id
            WHERE o.advice_id IS NULL AND a.as_of_date <= :cut""", cut=date.today() - timedelta(days=RESOLVE_AFTER_DAYS))
        if not pending:
            return {"resolved": 0, "pending": 0}
        from ..returns import excess_label

        bundle = bundle_fn()
        out = []
        for a in pending:
            d = pd.Timestamp(a["as_of_date"])
            if d not in bundle.calendar or a["company_id"] not in bundle.tr or a["benchmark_symbol"] not in bundle.bench_tr:
                continue
            idx = int(bundle.calendar.get_loc(d))
            lab = excess_label(bundle.tr[a["company_id"]], bundle.bench_tr[a["benchmark_symbol"]], idx, HORIZON)
            if lab["label"] is None:
                continue
            out.append({"id": a["id"], "end": bundle.calendar[idx + HORIZON].date(), "s": float(lab["stock_return"]),
                        "b": float(lab["benchmark_return"]), "x": float(lab["excess_return"])})
        n = self.db.executemany("""INSERT INTO holding_advice_outcome (advice_id, window_end_date, stock_return, benchmark_return, excess_return)
                                   VALUES (:id, :end, :s, :b, :x) ON CONFLICT DO NOTHING""", out)
        return {"resolved": n, "pending": len(pending) - n}

    def track_record(self, pid: int | None = None) -> dict:
        """Resolved advice per action: mean excess return over the sector ETF in the 21 trading days that followed, with a
        95% interval bootstrapped over symbols once MIN_TRACK_ROWS rows have resolved. One row per holding and day (the
        day's newest basis); the windows overlap, so this is a track record, not an independent test."""
        pid = self.portfolio_id(pid)
        rows = [] if pid is None else self.db.all("""
            WITH newest AS (SELECT DISTINCT ON (as_of_date) as_of_date, basis FROM holding_advice WHERE portfolio_id = :p
                            ORDER BY as_of_date, created_at DESC, id DESC)
            SELECT a.action, a.headline, a.symbol, o.excess_return::float8 AS x
            FROM holding_advice a JOIN newest n ON n.as_of_date = a.as_of_date AND n.basis = a.basis
            LEFT JOIN holding_advice_outcome o ON o.advice_id = a.id WHERE a.portfolio_id = :p""", p=pid)
        out = []
        for act in ACTIONS:
            sub = [r for r in rows if r["action"] == act]
            res = [r for r in sub if r["x"] is not None]
            m = {"action": act, "layer": LAYER[act], "advised": len(sub), "resolved": len(res), "meanExcess": None, "ci": None}
            if len(res) >= MIN_TRACK_ROWS:
                x = np.array([r["x"] for r in res])
                m["meanExcess"] = float(x.mean())
                m["ci"] = cluster_ci(x, np.array([r["symbol"] for r in res]))
            out.append(m)
        return {"minResolved": MIN_TRACK_ROWS, "horizon": HORIZON, "actions": out,
                "note": "Excess return = the stock's total return minus its sector ETF's over the 21 trading days after the "
                        "advice. SELL rows score well when this is negative. Rows of one portfolio overlap in time and "
                        "share stocks; read it as a track record, not a test."}


def _symbol(s: str) -> str:
    sym = (s or "").strip().upper()
    if not sym or len(sym) > 12 or not all(ch.isalnum() or ch in ".-" for ch in sym):
        raise BadRequest("symbol: 1 to 12 letters, digits, '.' or '-'")
    return sym


def _number(v, name: str, positive: bool = False) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise BadRequest(f"{name}: a number") from None
    if not math.isfinite(x) or x < 0 or (positive and x == 0) or x > 1e12:
        raise BadRequest(f"{name}: {'greater than zero' if positive else 'zero or more'}")
    return x


def _book_live_test() -> dict:
    from ..service import BOOK_KIND
    from ..evaluation import LIVE_TEST
    from .api.read import live_test_of

    return {"kind": BOOK_KIND, "minResolved": LIVE_TEST["minResolved"], "minAuc": LIVE_TEST["minAuc"], **live_test_of(BOOK_KIND)}


def _book_decisions(day: date | None) -> tuple[date | None, dict[int, dict]]:
    """The recorded book's decisions of one day (default: the latest), keyed by company, with the 21-day volatility the
    book sized with. Served exactly as /api/decisions serves them."""
    from .api.read import decisions

    res = decisions(day.isoformat() if day else None)
    d = date.fromisoformat(res["asOfDate"]) if res["asOfDate"] else None
    out = {}
    for x in res["decisions"]:
        sizing = (x.get("model") or {}).get("sizing") or {}
        out[x["companyId"]] = {**x, "vol21": sizing.get("vol21")}
    return d, out
