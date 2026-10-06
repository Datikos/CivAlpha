"""Corporate actions the price feed missed, found in 8-K text (ADR-0003).

Every 8-K carrying an item where a capital change is reported is read once per method:
  * KEYWORD: a fixed regular expression (rule `keyword-1`), free and deterministic, always run;
  * JEV: TypeSafe AI's Jev answers two questions about the item text (a choice of kind, a yes/no probability), run when
    TYPESAFE_API_KEY is set, pinned to CIVALPHA_JEV_MODEL.
Results go to `corporate_action_candidate`, reconciled with the recorded splits and spin-offs. A flagged candidate with no
reconciling action is a feed gap. Nothing is ever written to `corporate_action`: the owner confirms a gap and enters the
action with its value.

    python -m civalpha.corpactions run [keyword|jev] [--limit N]   # classify filings not yet classified by that method
    python -m civalpha.corpactions study [keyword|jev]             # the pre-registered evaluation of ADR-0003
    python -m civalpha.corpactions gaps [keyword|jev]              # flagged filings with no recorded action
"""
from __future__ import annotations

import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

RELEVANT_ITEMS = ("1.01", "2.01", "3.03", "5.03", "7.01", "8.01")
ACTION_TYPES = ("SPLIT", "SPLIT_INFO", "SPIN_OFF")
KEYWORD_RULE = "keyword-1"
MAX_STATE_CHARS = 24_000            # about 6,000 tokens: Jev loses accuracy on unrelated text, so the state stays short
RECONCILE_BEFORE_DAYS, RECONCILE_AFTER_DAYS = 5, 120
EVENT_WINDOW_BEFORE_DAYS, EVENT_WINDOW_AFTER_DAYS = 90, 3
QUIET_DAYS = 180
EVAL_SINCE = "2021-01-05"           # 90 days after the first stored 8-K (2020-10-05)
JEV_FLAG_P = 0.5
N_BOOT, SEED = 2000, 20261006
JEV_PRICE_PER_M_TOKENS = 0.042

# order matters: a reverse split is also a "stock split"
KEYWORDS = [
    (re.compile(r"\breverse (stock )?split\b", re.I), "REVERSE_SPLIT"),
    (re.compile(r"\b(stock split|forward split|split[- ]adjusted)\b", re.I), "FORWARD_SPLIT"),
    (re.compile(r"\b(spin[- ]?off|separation and distribution|pro rata distribution)\b", re.I), "SPIN_OFF_OR_DISTRIBUTION"),
]
KINDS = ("SPIN_OFF_OR_DISTRIBUTION", "FORWARD_SPLIT", "REVERSE_SPLIT", "SPECIAL_DIVIDEND", "MERGER_OR_DELISTING", "NONE")

JEV_QUESTIONS = {
    "kind": {
        "type": "choice",
        "instructions": "What change to its shares does the company announce or complete in this SEC filing?",
        "criteria": {
            "spin_off_or_distribution": "A spin-off or separation: shares of another company are distributed to the company's own shareholders",
            "forward_split": "A forward stock split: each share becomes several shares",
            "reverse_split": "A reverse stock split: several shares are combined into one",
            "special_dividend": "A special or one-time cash dividend",
            "merger_or_delisting": "A merger, acquisition or delisting in which the shares are exchanged or cancelled",
            "none": "None of these: the filing does not change the shares a holder owns",
        },
    },
    "changes_shares": {
        "type": "noul",
        "instructions": "This filing announces or completes a change to the shares a holder owns: a split, a reverse split, "
                        "a spin-off or a distribution of another company's shares.",
    },
}


# --------------------------------------------------------------------------- text
def to_text(raw: bytes | str) -> str:
    s = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t\r\f\v ]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


_ITEM = re.compile(r"\bItem\s+\d{1,2}\.\d{2}\b", re.I)
_SIGNATURE = re.compile(r"\bSIGNATURES?\b")


def item_text(text: str, max_chars: int = MAX_STATE_CHARS) -> str:
    """The item sections of an 8-K: from the first "Item x.xx" heading to the signature block, capped. The cover page
    (registrant, checkboxes, the securities table) and the signature carry no decision and only distract a classifier."""
    m = _ITEM.search(text)
    body = text[m.start():] if m else text
    sig = _SIGNATURE.search(body)
    if sig and sig.start() > 200:
        body = body[:sig.start()]
    return body[:max_chars].strip()


# --------------------------------------------------------------------------- classifiers
@dataclass(frozen=True)
class Classified:
    kind: str
    probability: float | None
    model: str
    evidence: str

    @property
    def flagged(self) -> bool:
        return self.kind != "NONE" and (self.probability is None or self.probability >= JEV_FLAG_P)


def keyword_classify(text: str) -> Classified:
    for rx, kind in KEYWORDS:
        m = rx.search(text)
        if m:
            a, b = max(0, m.start() - 120), min(len(text), m.end() + 120)
            return Classified(kind, None, KEYWORD_RULE, text[a:b].replace("\n", " "))
    return Classified("NONE", None, KEYWORD_RULE, "")


def jev_classify(client, text: str) -> Classified:
    r = client.ask(text, JEV_QUESTIONS)
    kind_ans, noul = r.answers["kind"], r.answers["changes_shares"]
    kind = str(kind_ans.get("choice") or "none").upper()
    if kind not in KINDS:
        kind = "NONE"
    probs = kind_ans.get("probabilities") or {}
    top = sorted(probs.items(), key=lambda kv: -float(kv[1]))[:3]
    return Classified(kind, float(noul.get("noul")), r.model, json.dumps({"kind": dict(top), "tokens": r.input_tokens}))


# --------------------------------------------------------------------------- reconciliation and evaluation
def reconcile(cands: pd.DataFrame, actions: pd.DataFrame) -> pd.Series:
    """For each candidate (company_id, accepted_at) the id of the first recorded split or spin-off of that company with
    ex-date in [accepted - 5 days, accepted + 120 days], else None."""
    out = pd.Series([None] * len(cands), index=cands.index, dtype=object)
    if actions.empty or cands.empty:
        return out
    acts = actions[actions["action_type"].isin(ACTION_TYPES)].copy()
    acts["ex_date"] = pd.to_datetime(acts["ex_date"])
    by = {c: g.sort_values("ex_date") for c, g in acts.groupby("company_id")}
    for i, c in cands.iterrows():
        g = by.get(c["company_id"])
        if g is None:
            continue
        t = pd.Timestamp(c["accepted_at"]).tz_localize(None) if pd.Timestamp(c["accepted_at"]).tzinfo else pd.Timestamp(c["accepted_at"])
        hit = g[(g["ex_date"] >= t.normalize() - pd.Timedelta(days=RECONCILE_BEFORE_DAYS)) &
                (g["ex_date"] <= t.normalize() + pd.Timedelta(days=RECONCILE_AFTER_DAYS))]
        if len(hit):
            out[i] = int(hit.iloc[0]["id"])
    return out


def _naive(ts) -> pd.Series:
    s = pd.to_datetime(ts, utc=True)
    return s.dt.tz_convert(None) if hasattr(s, "dt") else s.tz_convert(None)


def evaluation_events(actions: pd.DataFrame, all_filings: pd.DataFrame) -> pd.DataFrame:
    """The fixed event set of ADR-0003: splits and spin-offs since EVAL_SINCE with at least one 8-K (any items) of the
    company in the 90 days before the ex-date (to 3 days after)."""
    acts = actions[actions["action_type"].isin(("SPLIT", "SPIN_OFF")) & actions["company_id"].notna()].copy()
    acts["ex_date"] = pd.to_datetime(acts["ex_date"])
    acts = acts[acts["ex_date"] >= pd.Timestamp(EVAL_SINCE)]
    fl = all_filings.assign(t=_naive(all_filings["accepted_at"]))
    keep = []
    for a in acts.itertuples():
        w = fl[(fl["company_id"] == a.company_id) & (fl["t"] >= a.ex_date - pd.Timedelta(days=EVENT_WINDOW_BEFORE_DAYS)) &
               (fl["t"] <= a.ex_date + pd.Timedelta(days=EVENT_WINDOW_AFTER_DAYS))]
        keep.append(len(w) > 0)
    return acts[np.array(keep, dtype=bool)].reset_index(drop=True) if len(acts) else acts


def quiet_mask(cands: pd.DataFrame, actions: pd.DataFrame) -> np.ndarray:
    """Filings of companies with no recorded split or spin-off within 180 days either side."""
    acts = actions[actions["action_type"].isin(ACTION_TYPES)].copy()
    acts["ex_date"] = pd.to_datetime(acts["ex_date"])
    by = {c: g["ex_date"].to_numpy() for c, g in acts.groupby("company_id")}
    t = _naive(cands["accepted_at"]).to_numpy()
    q = np.ones(len(cands), dtype=bool)
    for i, (c, ti) in enumerate(zip(cands["company_id"].to_numpy(), t)):
        ex = by.get(c)
        if ex is not None and np.any(np.abs(ex - ti) <= np.timedelta64(QUIET_DAYS, "D")):
            q[i] = False
    return q


def _cluster_ci(values: np.ndarray, clusters: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED) -> list[float]:
    if len(values) == 0:
        return [float("nan"), float("nan")]
    uniq = np.unique(clusters)
    groups = [np.flatnonzero(clusters == u) for u in uniq]
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), size=len(groups))
        idx = np.concatenate([groups[i] for i in pick])
        boots.append(float(values[idx].mean()))
    return [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]


def evaluate(cands: pd.DataFrame, events: pd.DataFrame, actions: pd.DataFrame, n_boot: int = N_BOOT) -> dict:
    """cands: one method's candidates (company_id, accepted_at, flagged, filing_id, kind, evidence)."""
    t = _naive(cands["accepted_at"])
    hit = np.zeros(len(events), dtype=bool)
    missed = []
    for i, e in enumerate(events.itertuples()):
        w = cands[(cands["company_id"] == e.company_id) & (t >= e.ex_date - pd.Timedelta(days=EVENT_WINDOW_BEFORE_DAYS)) &
                  (t <= e.ex_date + pd.Timedelta(days=EVENT_WINDOW_AFTER_DAYS))]
        hit[i] = bool(w["flagged"].any())
        if not hit[i]:
            missed.append({"symbol": e.symbol, "exDate": str(e.ex_date.date()), "type": e.action_type, "value": float(e.value),
                           "relevant8Ks": int(len(w))})
    q = quiet_mask(cands, actions)
    quiet = cands[q]
    flags = quiet["flagged"].to_numpy(bool)
    return {
        "events": int(len(events)), "eventsFlagged": int(hit.sum()),
        "recall": float(hit.mean()) if len(hit) else float("nan"),
        "recallCi95": _cluster_ci(hit.astype(float), events["company_id"].to_numpy(), n_boot) if len(hit) else None,
        "missed": missed,
        "quietFilings": int(len(quiet)), "quietFlagged": int(flags.sum()),
        "quietFlagRate": float(flags.mean()) if len(flags) else float("nan"),
        "quietFlagRateCi95": _cluster_ci(flags.astype(float), quiet["company_id"].to_numpy(), n_boot) if len(flags) else None,
        "quietFlaggedList": quiet[flags][["filing_id", "symbol", "accepted_at", "kind", "evidence"]].assign(
            accepted_at=lambda d: d["accepted_at"].astype(str)).to_dict("records"),
    }


def adoption(keyword: dict, jev: dict) -> dict:
    """Decision 5 of ADR-0003: Jev runs daily only if its event recall is at least the keyword rule's and its flag rate on
    quiet filings is not higher by more than 1 percentage point."""
    ok = jev["recall"] >= keyword["recall"] and jev["quietFlagRate"] <= keyword["quietFlagRate"] + 0.01
    return {"adopt": bool(ok), "recall": [keyword["recall"], jev["recall"]],
            "quietFlagRate": [keyword["quietFlagRate"], jev["quietFlagRate"]]}


# --------------------------------------------------------------------------- database
def load_filings(engine, relevant_only: bool = True) -> pd.DataFrame:
    from sqlalchemy import text
    sql = """SELECT f.id AS filing_id, f.company_id, f.accepted_at, f.items, f.accession_no, sd.storage_path, sd.url,
                    (SELECT th.symbol FROM ticker_history th WHERE th.company_id = f.company_id ORDER BY th.valid_from DESC LIMIT 1) AS symbol
             FROM filing f JOIN source_document sd ON sd.id = f.source_document_id WHERE f.form_type = '8-K'"""
    with engine.connect() as c:
        df = pd.read_sql(text(sql), c)
    df["accepted_at"] = pd.to_datetime(df["accepted_at"], utc=True)
    if relevant_only:
        items = df["items"].fillna("").map(lambda s: {i.strip() for i in s.split(",")})
        df = df[items.map(lambda s: bool(s & set(RELEVANT_ITEMS)))]
    return df.reset_index(drop=True)


def load_actions(engine) -> pd.DataFrame:
    from sqlalchemy import text
    with engine.connect() as c:
        return pd.read_sql(text("SELECT id, company_id, symbol, ex_date, action_type, value::float8 AS value FROM corporate_action "
                                "WHERE company_id IS NOT NULL"), c)


def load_candidates(engine, method: str) -> pd.DataFrame:
    from sqlalchemy import text
    with engine.connect() as c:
        df = pd.read_sql(text("""SELECT c.*, (SELECT th.symbol FROM ticker_history th WHERE th.company_id = c.company_id
                                  ORDER BY th.valid_from DESC LIMIT 1) AS symbol FROM corporate_action_candidate c WHERE method = :m"""),
                         c, params={"m": method})
    df["accepted_at"] = pd.to_datetime(df["accepted_at"], utc=True)
    df["flagged"] = (df["kind"] != "NONE") & (df["probability"].isna() | (df["probability"] >= JEV_FLAG_P))
    return df


def read_item_text(documents_dir: str | Path, storage_path: str | None) -> str | None:
    if not storage_path:
        return None
    try:
        return item_text(to_text((Path(documents_dir) / storage_path).read_bytes()))
    except OSError:
        return None


_UPSERT = """
INSERT INTO corporate_action_candidate (filing_id, company_id, accepted_at, method, model, kind, probability, evidence, reconciled_action_id)
VALUES (:f, :c, :t, :m, :model, :k, :p, :e, :r)
ON CONFLICT (filing_id, method) DO UPDATE SET model = EXCLUDED.model, kind = EXCLUDED.kind, probability = EXCLUDED.probability,
    evidence = EXCLUDED.evidence, reconciled_action_id = EXCLUDED.reconciled_action_id, created_at = now()"""


def run(engine, method: str = "KEYWORD", limit: int | None = None, documents_dir: str | None = None, client=None,
        log=print, workers: int = 8) -> dict:
    """Classify the relevant 8-Ks not yet classified by `method` and store the candidates. JEV errors fall back to nothing
    stored for that filing (the keyword row stands); they are counted and logged, never raised."""
    from sqlalchemy import text

    from .platform.settings import settings
    method = method.upper()
    if method == "JEV":     # decided before any database or file work: without a key there is nothing to do
        from .platform.jev import JevClient
        client = client or JevClient()
        if not client.enabled:
            log("JEV skipped: TYPESAFE_API_KEY is not set (ADR-0003 phase 2)")
            return {"method": method, "classified": 0, "skipped": True, "errors": 0}
    documents_dir = documents_dir or settings().documents_dir
    filings = load_filings(engine)
    with engine.connect() as c:
        done = {r[0] for r in c.execute(text("SELECT filing_id FROM corporate_action_candidate WHERE method = :m"), {"m": method})}
    todo = filings[~filings["filing_id"].isin(done)]
    if limit:
        todo = todo.head(limit)
    actions = load_actions(engine)
    texts = [read_item_text(documents_dir, p) for p in todo["storage_path"]]
    errors, results = 0, [None] * len(todo)

    def one(i: int):
        t = texts[i]
        if t is None:
            return None
        return keyword_classify(t) if method == "KEYWORD" else jev_classify(client, t)

    if method == "JEV":
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(one, i): i for i in range(len(todo))}
            for f, i in futs.items():
                try:
                    results[i] = f.result()
                except Exception as e:  # noqa: BLE001 - the keyword row stands; count and move on
                    errors += 1
                    if errors <= 5:
                        log(f"JEV error on filing {int(todo.iloc[i]['filing_id'])}: {e}")
    else:
        results = [one(i) for i in range(len(todo))]
    rows = todo.assign(res=results)
    rows = rows[rows["res"].notna()]
    rec = reconcile(rows[["company_id", "accepted_at"]], actions)
    payload = [dict(f=int(r.filing_id), c=int(r.company_id), t=r.accepted_at.to_pydatetime(), m=method, model=r.res.model,
                    k=r.res.kind, p=r.res.probability, e=r.res.evidence[:1000], r=rec[i])
               for i, r in zip(rows.index, rows.itertuples())]
    if payload:
        with engine.begin() as c:
            c.execute(text(_UPSERT), payload)
    tokens = sum(len(t) / 4 for t in texts if t)
    out = {"method": method, "classified": len(payload), "missingText": int(sum(t is None for t in texts)), "errors": errors,
           "flagged": int(sum(1 for r in results if r is not None and r.flagged))}
    if method == "JEV":
        out["estimatedCostUsd"] = round(tokens / 1e6 * JEV_PRICE_PER_M_TOKENS, 4)
    log(json.dumps(out))
    return out


def study(engine, method: str = "KEYWORD") -> dict:
    method = method.upper()
    actions = load_actions(engine)
    events = evaluation_events(actions, load_filings(engine, relevant_only=False))
    res = {"method": method, **evaluate(load_candidates(engine, method), events, actions)}
    if method == "JEV":
        res["adoption"] = adoption(evaluate(load_candidates(engine, "KEYWORD"), events, actions), res)
    return res


def gaps(engine, method: str = "KEYWORD") -> pd.DataFrame:
    c = load_candidates(engine, method.upper())
    g = c[c["flagged"] & c["reconciled_action_id"].isna()]
    return g[["filing_id", "symbol", "accepted_at", "kind", "probability", "review", "evidence"]].sort_values("accepted_at")


def main(argv: list[str]) -> None:
    from .platform.sql import engine
    cmd = argv[1] if len(argv) > 1 else "study"
    method = (argv[2] if len(argv) > 2 and not argv[2].startswith("--") else "keyword").upper()
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    if cmd == "run":
        run(engine(), method, limit)
    elif cmd == "study":
        r = study(engine(), method)
        show = {k: v for k, v in r.items() if k != "quietFlaggedList"}
        print(json.dumps(show, indent=2, default=str))
        print(f"quiet filings flagged ({len(r['quietFlaggedList'])}), first 40:")
        for x in r["quietFlaggedList"][:40]:
            print(f"  {x['accepted_at'][:10]} {x['symbol']:6s} {x['kind']:26s} filing {x['filing_id']}: {x['evidence'][:150]}")
    elif cmd == "gaps":
        pd.set_option("display.width", 250)
        pd.set_option("display.max_rows", 500)
        pd.set_option("display.max_colwidth", 110)
        print(gaps(engine(), method).to_string(index=False))
    else:
        raise SystemExit(f"unknown command {cmd}: run | study | gaps")


if __name__ == "__main__":
    main(sys.argv)
