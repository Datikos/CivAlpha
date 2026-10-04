"""Orchestration of evaluation, forecast generation and outcome resolution."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import db, pit
from .evaluation import EvalConfig, run_walk_forward
from .features import FEATURE_KIND, FEATURE_LABELS, FEATURES, MIN_HISTORY, DataBundle, build_panel, build_rows
from .model import ALGORITHM, CODE_VERSION, LogitModel
from .returns import HORIZON, excess_label

log = logging.getLogger(__name__)

TARGET = ("P(total return of {sym} over the next {h} trading days > total return of sector benchmark {bench}), "
          "measured from the close of {d} to the close {h} trading days later")


def _is_demo(bundle: DataBundle) -> bool:
    return bool(bundle.companies["is_demo"].any()) if "is_demo" in bundle.companies else False


# --------------------------------------------------------------------------- evaluation
def evaluate(engine, cfg: EvalConfig | None = None) -> dict:
    cfg = cfg or EvalConfig()
    bundle = db.load_bundle(engine)
    panel = build_panel(bundle, sample_every=cfg.sample_every)
    result = run_walk_forward(panel, bundle.calendar, cfg)
    eid = db.insert_evaluation(engine, result, bundle.calendar[-1].date(), _is_demo(bundle))
    return {"evaluationId": eid, "verdict": result["verdict"], "metrics": db._clean(result["metrics"])}


# --------------------------------------------------------------------------- forecasts
def issue(engine, as_of: str | None = None, as_of_dates: list[str] | None = None, company_ids: list[int] | None = None,
          kinds: list[str] | None = None, n_boot: int = 30) -> dict:
    """Compute forecasts. Either a single cutoff `as_of` (timestamp; default now) or several historical
    as-of dates (replay: cutoff = close of each date). Returns payloads; the backend persists them."""
    kinds = kinds or ["BASELINE", "AUGMENTED"]
    bundle = db.load_bundle(engine)
    sources = db.load_event_sources(engine)
    providers = db.price_providers(engine)
    if as_of_dates:
        cutoffs = [pit.close_ts(pd.Timestamp(d)) for d in as_of_dates]
    else:
        cutoffs = [pit.to_utc(as_of) if as_of else pd.Timestamp(datetime.now(timezone.utc))]
    panel = build_panel(bundle)
    out = []
    for cutoff in cutoffs:
        d = pit.last_trading_date_at(bundle.calendar, cutoff)
        if d is None:
            continue
        idx = int(bundle.calendar.get_loc(d))
        # live cutoff is never earlier than the close of the as-of date
        cutoff = max(cutoff, pit.close_ts(d))
        train = panel[(panel["idx"] + HORIZON <= idx) & panel["label"].notna()]
        if len(train) < 200:
            log.warning("not enough labelled history before %s (%d samples)", d.date(), len(train))
            continue
        rows = pd.DataFrame(build_rows(bundle, idx, as_of=cutoff, company_ids=company_ids, with_provenance=True, with_labels=False))
        if rows.empty:
            continue
        for kind in kinds:
            model = LogitModel(FEATURES[kind], n_boot=n_boot).fit(train, train["label"].astype(bool), groups=train["idx"])
            trained_through = bundle.calendar[int(train["idx"].max())].date()
            mv_id = db.insert_model_version(engine, kind, ALGORITHM, FEATURES[kind], trained_through, cutoff.to_pydatetime(),
                                            len(train), model.to_json(), CODE_VERSION, _is_demo(bundle))
            p = model.predict(rows)
            lo, hi = model.predict_interval(rows)
            for i, r in rows.iterrows():
                out.append(_payload(bundle, engine, sources, providers, kind, mv_id, model, r, float(p[i]), float(lo[i]), float(hi[i]),
                                    d, cutoff, len(train), trained_through))
    return {"forecasts": out}


def _payload(bundle, engine, sources, providers, kind, mv_id, model, r, p, lo, hi, d, cutoff, n_train, trained_through) -> dict:
    comp = bundle.companies.set_index("id").loc[int(r["company_id"])]
    expl = model.explain(r[model.features])
    ev_prov = r["_event_provenance"] if kind == "AUGMENTED" else []
    passage_ids = sorted({x["passage_id"] for e in ev_prov for x in e.get("exposures", []) if x.get("passage_id")})
    passages = db.load_passages(engine, passage_ids).set_index("id") if passage_ids else None
    filings = bundle.filings.set_index("accession_no") if len(bundle.filings) else None
    fact_filings = []
    for acc in r["_fact_accessions"]:
        if filings is not None and acc in filings.index:
            f = filings.loc[acc]
            fact_filings.append({"type": "FILING", "id": int(f["id"]), "label": f"{f['form_type']} {acc}", "url": f"/filings/{int(f['id'])}",
                                 "publishedAt": pd.Timestamp(f["accepted_at"]).isoformat()})
    for f in expl["factors"]:
        f["label"] = FEATURE_LABELS.get(f["feature"], f["feature"])
        f["kind"] = FEATURE_KIND.get(f["feature"], "OTHER")
        prov = []
        if f["kind"] == "PRICE":
            prov.append({"type": "PRICES", "id": None, "label": f"Daily closes of {comp['symbol']} and {comp['benchmark_symbol']} through {d.date()}", "url": None})
        elif f["kind"] == "FUNDAMENTAL":
            prov.extend(fact_filings)
        elif f["kind"] == "MACRO":
            prov.append({"type": "MACRO", "id": None, "label": f"FEDFUNDS as published on {cutoff.date()} (3-month change {r['_fedfunds_chg']:+.2f} pp)"
                         if not pd.isna(r["_fedfunds_chg"]) else "FEDFUNDS not available at cutoff", "url": None})
        else:
            for e in ev_prov:
                if e["feature"] != f["feature"]:
                    continue
                prov.append({"type": "EVENT", "id": e["event_id"], "label": f"{e['title']} (feature contribution {e['contribution']:+.3f})",
                             "url": f"/events/{e['event_id']}", "publishedAt": e["published_at"]})
                for x in e.get("exposures", []):
                    lab = f"Exposure {x['target']} ({x['basis']}, {x['confidence']}" + (f", share {x['share']:.0%})" if x["share"] is not None else ")")
                    if x.get("passage_id") and passages is not None and x["passage_id"] in passages.index:
                        ps = passages.loc[x["passage_id"]]
                        prov.append({"type": "PASSAGE", "id": int(x["passage_id"]), "label": f"{lab} — {ps['form_type']} {ps['accession_no']}: {ps['section']}",
                                     "url": f"/filings/{int(ps['filing_id'])}"})
                    else:
                        prov.append({"type": "EXPOSURE", "id": x["id"], "label": lab, "url": None})
            if f["feature"] == "rate_shock":
                prov.extend(fact_filings[:1])
        f["provenance"] = prov
    # source list: distinct documents behind the forecast
    src = []
    for e in ev_prov:
        for s in sources[sources["event_id"] == e["event_id"]].itertuples(index=False):
            src.append({"kind": "EVENT", "label": f"{s.publisher}: {s.title}", "url": f"/api/documents/{s.document_id}" if s.is_demo else s.url,
                        "accessionNo": None, "publishedAt": pd.Timestamp(s.published_at).isoformat() if not pd.isna(s.published_at) else None})
    src.extend({"kind": "FILING", "label": x["label"], "url": x["url"], "accessionNo": x["label"].split(" ")[-1],
                "publishedAt": x["publishedAt"]} for x in fact_filings)
    src.append({"kind": "PRICES", "label": f"Daily prices ({', '.join(providers)}) for {comp['symbol']} and {comp['benchmark_symbol']} through {d.date()}",
                "url": None, "accessionNo": None, "publishedAt": None})
    if kind == "AUGMENTED":
        src.append({"kind": "MACRO", "label": "FEDFUNDS (vintage as published at cutoff)", "url": None, "accessionNo": None, "publishedAt": None})
    seen, uniq = set(), []
    for s in src:
        k = (s["kind"], s["label"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    feats = {k: (None if pd.isna(r[k]) else float(r[k])) for k in model.features}
    return db._clean({
        "companyId": int(r["company_id"]), "symbol": comp["symbol"], "benchmarkSymbol": comp["benchmark_symbol"],
        "modelKind": kind, "modelVersionId": mv_id, "probability": p, "probLow": min(lo, p), "probHigh": max(hi, p),
        "horizonTradingDays": HORIZON, "asOfDate": str(d.date()), "asOf": cutoff.isoformat(),
        "target": TARGET.format(sym=comp["symbol"], h=HORIZON, bench=comp["benchmark_symbol"], d=d.date()),
        "uncertaintyNote": (f"Interval = 10th–90th percentile across {len(model.params.get('bootstrap', []))} bootstrap refits "
                            f"(estimation uncertainty only). Trained on {n_train} labelled samples through {trained_through}; "
                            f"single 21-day outcomes remain close to a coin flip."),
        "features": feats, "explanation": expl, "sources": uniq,
    })


# --------------------------------------------------------------------------- outcomes
def resolve_outcomes(engine) -> dict:
    pending = db.unresolved_forecasts(engine)
    if pending.empty:
        return {"resolved": 0, "pending": 0}  # nothing to do; works before any prices are loaded
    bundle = db.load_bundle(engine)
    rows = []
    for f in pending.itertuples(index=False):
        d = pd.Timestamp(f.as_of_date)
        if d not in bundle.calendar or f.company_id not in bundle.tr or f.benchmark_symbol not in bundle.bench_tr:
            continue
        idx = int(bundle.calendar.get_loc(d))
        lab = excess_label(bundle.tr[f.company_id], bundle.bench_tr[f.benchmark_symbol], idx, int(f.horizon_trading_days))
        if lab["label"] is None:
            continue
        y = 1.0 if lab["label"] else 0.0
        rows.append(dict(id=int(f.id), end=bundle.calendar[idx + int(f.horizon_trading_days)].date(), s=lab["stock_return"],
                         b=lab["benchmark_return"], x=lab["excess_return"], o=bool(lab["label"]), brier=(float(f.probability) - y) ** 2))
    n = db.insert_outcomes(engine, rows)
    return {"resolved": n, "pending": int(len(pending) - n)}
