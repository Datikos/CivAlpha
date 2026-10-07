"""Read-only REST endpoints (public): meta, companies, filings, exposures, documents, forecasts, accuracy,
strategies, AI decisions and time-machine runs. Response shapes match docs/api.md."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query, Response

import numpy as np
import pandas as pd

from ...dividends import BUYBACK_CONCEPT, PAYOUT_CONCEPTS, dividend_profile, payout_from_facts
from ...earnings import announcements, next_estimate, session_excess
from ...evaluation import LIVE_TEST, live_test_verdict
from ...returns import total_return_index
from ... import pit
from ...strategies.ai import AI_RANK_SIZED_KEY, BOOK_KEY
from ..errors import BadRequest, NotFound
from ..llm import provider
from ..market import MarketDataService
from ..rows import camel, camel_all, camel_key, value
from ..settings import settings
from ..sql import db
from ..storage import DocumentStore
from ..tickers import TickerResolver
from ..universe import UniverseService

router = APIRouter(prefix="/api")

LABELS = {
    "Revenues": "Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue (ASC 606)",
    "GrossProfit": "Gross profit", "OperatingIncomeLoss": "Operating income", "NetIncomeLoss": "Net income",
    "Assets": "Total assets", "Liabilities": "Total liabilities", "LongTermDebtNoncurrent": "Long-term debt",
    "CashAndCashEquivalentsAtCarryingValue": "Cash and equivalents",
}
AI_KEY = "AI_GBM"
BOOK_KEYS = (BOOK_KEY, AI_RANK_SIZED_KEY, AI_KEY)   # the recorded book today, then the keys it was stored under before
REFERENCE = "EW_BUY_HOLD"
DISCLAIMERS = [
    "Research software. Not investment advice. No brokerage connection or order placement.",
    "Forecasts are model outputs with stated uncertainty; recorded facts and model estimates are labelled separately.",
    "Profitability is not claimed unless the cost-adjusted walk-forward evidence supports it.",
    "Market data use for model training and public display depends on your data licence.",
]


def parse_ts(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise BadRequest(f"invalid timestamp {s!r}") from e
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise BadRequest(f"invalid date {s!r}") from e


def minus_years(d: date, years: int) -> date:
    """Calendar years back, like java.time LocalDate.minusYears (29 Feb -> 28 Feb)."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def resolve(symbol: str) -> int:
    cid = TickerResolver().company_ever(symbol)
    if cid is None:
        raise NotFound(f"unknown symbol {symbol}")
    return cid


# --------------------------------------------------------------------------- meta
@router.get("/meta")
def meta():
    s = settings()
    llm = provider()
    return {
        "llmEnabled": llm.enabled,
        "llmProvider": llm.name,
        "secConfigured": s.sec.configured,
        "fredEnabled": s.fred.enabled,
        "adminTokenRequired": s.admin_token_required,
        "authMode": s.auth_mode,
        "missingBenchmarks": MarketDataService().missing_benchmarks(),
        "priceProvider": s.prices.provider.lower() if s.prices.enabled else "none",
        "dataCutoff": value(db().scalar("SELECT max(trade_date) FROM price_bar")),
        "target": "P(21-trading-day total return of the stock > total return of its sector benchmark ETF), "
                  "measured from the close of the as-of date to the close 21 trading days later",
        "disclaimers": DISCLAIMERS,
    }


# --------------------------------------------------------------------------- companies
@router.get("/companies")
def companies():
    out = camel_all(db().all("""
        SELECT c.id, (SELECT symbol FROM ticker_history t WHERE t.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
               c.name, c.sector, c.industry, c.benchmark_symbol,
               (SELECT cik FROM cik_mapping m WHERE m.company_id = c.id AND m.valid_to IS NULL LIMIT 1) AS cik,
               p.close AS latest_close, p.trade_date AS latest_close_date
        FROM company c
        LEFT JOIN LATERAL (SELECT close, trade_date FROM price_bar b WHERE b.company_id = c.id ORDER BY trade_date DESC LIMIT 1) p ON true
        ORDER BY 2"""))
    latest: dict[int, dict] = {}
    for f in db().all("""SELECT DISTINCT ON (company_id, model_kind) company_id, model_kind, id, probability, as_of_date
                         FROM forecast ORDER BY company_id, model_kind, as_of_date DESC, version DESC"""):
        latest.setdefault(f["company_id"], {})[f["model_kind"]] = {
            "id": f["id"], "probability": value(f["probability"]), "asOfDate": value(f["as_of_date"])}
    actions: dict[int, list] = {}
    for a in db().all("""SELECT company_id, ex_date, action_type, value FROM corporate_action
                         WHERE company_id IS NOT NULL AND action_type IN ('CASH_DIVIDEND', 'SPLIT')"""):
        actions.setdefault(a["company_id"], []).append((a["ex_date"], a["action_type"], a["value"]))
    closes = _recent_closes()
    tags = UniverseService().tags_by_company()
    for c in out:
        c["tags"] = tags.get(c["id"], [])
        c["latestForecasts"] = latest.get(c["id"], {})
        c["dividend"] = _dividend_summary(actions.get(c["id"], []), c["latestCloseDate"], c["latestClose"])
        own = closes.get(("c", c["id"]), [])
        bench = closes.get(("b", c["benchmarkSymbol"]), [])
        c["recentCloses"] = own
        c["change21d"] = _change(own)
        c["benchmarkChange21d"] = _change(bench)
    return out


RECENT_BARS = 22  # the last close plus 21 trading days: one forecast horizon


def _recent_closes() -> dict[tuple, list[float]]:
    """Last 22 closes per company and per benchmark ETF (ascending), for sparklines and the 21-day change."""
    out: dict[tuple, list[float]] = {}
    rows = db().all("""
        SELECT company_id, symbol, close FROM (
            SELECT company_id, symbol, close, trade_date,
                   row_number() OVER (PARTITION BY coalesce(CAST(company_id AS text), symbol) ORDER BY trade_date DESC) AS rn
            FROM price_bar
            WHERE trade_date >= (SELECT max(trade_date) FROM price_bar) - 60
        ) t WHERE rn <= :n ORDER BY trade_date""", n=RECENT_BARS)
    benchmarks = {r["benchmark_symbol"] for r in db().all("SELECT DISTINCT benchmark_symbol FROM company")}
    for r in rows:
        if r["company_id"] is not None:
            out.setdefault(("c", r["company_id"]), []).append(value(r["close"]))
        elif r["symbol"] in benchmarks:
            out.setdefault(("b", r["symbol"]), []).append(value(r["close"]))
    return out


def _change(closes: list[float]) -> float | None:
    if len(closes) < 2 or not closes[0]:
        return None
    return closes[-1] / closes[0] - 1


def _dividend_summary(actions: list, close_date: str | None, close: float | None) -> dict:
    p = dividend_profile(actions, date.fromisoformat(close_date) if close_date else date.today(), close)
    return {k: value(p[k]) for k in ("status", "frequency", "trailingYield", "indicatedYield", "lastExDate", "yearsPaid")}


def pit_facts(company_id: int, as_of: datetime, latest_period_first: bool) -> list[dict]:
    """Per (concept, unit, period, dims) the latest value accepted on/before asOf, plus the first-filed value."""
    order = "DESC" if latest_period_first else "ASC"
    return db().all(f"""
        WITH known AS (
            SELECT * FROM xbrl_fact WHERE company_id = :c AND accepted_at <= :asof AND dims_key = '' AND taxonomy = 'us-gaap'
        ), ranked AS (
            SELECT k.*,
                   row_number() OVER (PARTITION BY concept, unit, period_start, period_end ORDER BY accepted_at DESC, id DESC) AS rn,
                   first_value(value) OVER (PARTITION BY concept, unit, period_start, period_end ORDER BY accepted_at, id) AS original_value
            FROM known k
        )
        SELECT * FROM ranked WHERE rn = 1 ORDER BY period_end {order}, concept""", c=company_id, asof=as_of)


def _fact(f: dict) -> dict:
    return {
        "concept": f["concept"], "label": LABELS.get(f["concept"], f["concept"]), "value": value(f["value"]), "unit": f["unit"],
        "periodStart": value(f["period_start"]), "periodEnd": value(f["period_end"]), "fiscalPeriod": f["fiscal_period"],
        "formType": f["form_type"], "filedDate": value(f["filed_date"]), "accessionNo": f["accession_no"], "filingId": f["filing_id"],
        "sourceUrl": f["source_url"],
    }


@router.get("/companies/{symbol}")
def company(symbol: str):
    cid = resolve(symbol)
    c = camel(db().one("SELECT c.id, c.name, c.sector, c.industry, c.benchmark_symbol, c.exchange FROM company c WHERE c.id = :id",
                       id=cid))
    c["symbol"] = TickerResolver().current_symbol(cid)
    c["tags"] = UniverseService().tags_of(cid)
    c["tickerHistory"] = camel_all(db().all("SELECT symbol, valid_from, valid_to, source FROM ticker_history WHERE company_id = :id ORDER BY valid_from",
                                            id=cid))
    c["cikHistory"] = camel_all(db().all("SELECT cik, valid_from, valid_to, source FROM cik_mapping WHERE company_id = :id ORDER BY valid_from",
                                         id=cid))
    facts, seen = [], set()
    for f in pit_facts(cid, datetime.now(timezone.utc), True):
        if f["concept"] in LABELS and f["concept"] not in seen:
            seen.add(f["concept"])
            facts.append(_fact(f))
    c["keyFacts"] = facts
    return c


@router.get("/companies/{symbol}/financials")
def financials(symbol: str, asOf: str | None = None):  # noqa: N803 - query parameter name is part of the API
    cid = resolve(symbol)
    at = parse_ts(asOf)
    series: dict[str, dict] = {}
    for f in pit_facts(cid, at, False):
        concept = f["concept"]
        if concept not in LABELS:
            continue
        s = series.setdefault(concept, {"concept": concept, "label": LABELS[concept], "unit": f["unit"], "points": []})
        p = _fact(f)
        v, orig = float(f["value"]), float(f["original_value"])
        p["revised"] = v != orig
        p["originalValue"] = orig if v != orig else None
        if f["period_start"] is not None:
            days = (f["period_end"] - f["period_start"]).days
            if 100 < days < 350:
                continue  # skip year-to-date (6/9-month) values
            fp = f["fiscal_period"]
            p["fiscalPeriod"] = "FY" if days >= 350 else ("Q" if fp is None or fp == "FY" else fp)
        s["points"].append(p)
    return {"asOf": value(at), "series": list(series.values())}


@router.get("/companies/{symbol}/prices")
def company_prices(symbol: str, from_: str | None = Query(None, alias="from")):
    cid = resolve(symbol)
    start = parse_date(from_) or minus_years(date.today(), 3)
    c = db().one("SELECT benchmark_symbol FROM company WHERE id = :id", id=cid)
    bars = camel_all(db().all("""
        SELECT p.trade_date AS date, p.symbol, p.close, b.close AS benchmark_close
        FROM price_bar p LEFT JOIN price_bar b ON b.symbol = :bench AND b.trade_date = p.trade_date
        WHERE p.company_id = :id AND p.trade_date >= :from ORDER BY p.trade_date""", bench=c["benchmark_symbol"], id=cid, **{"from": start}))
    actions = camel_all(db().all("""
        SELECT ex_date, action_type AS type, value, symbol FROM corporate_action WHERE company_id = :id AND ex_date >= :from ORDER BY ex_date""",
        id=cid, **{"from": start}))
    return {"symbol": TickerResolver().current_symbol(cid), "benchmarkSymbol": c["benchmark_symbol"],
            "bars": bars, "corporateActions": actions}


@router.get("/companies/{symbol}/dividends")
def company_dividends(symbol: str):
    """Recorded cash dividends as of the latest close, plus the latest 12 months' payout as filed."""
    cid = resolve(symbol)
    last = db().one("SELECT close, trade_date FROM price_bar WHERE company_id = :id ORDER BY trade_date DESC LIMIT 1", id=cid)
    actions = [(a["ex_date"], a["action_type"], a["value"]) for a in db().all(
        "SELECT ex_date, action_type, value FROM corporate_action WHERE company_id = :id AND action_type IN ('CASH_DIVIDEND', 'SPLIT')",
        id=cid)]
    p = dividend_profile(actions, last["trade_date"] if last else date.today(), float(last["close"]) if last else None)
    wanted = PAYOUT_CONCEPTS | {BUYBACK_CONCEPT}
    payout = payout_from_facts(f for f in pit_facts(cid, datetime.now(timezone.utc), True) if f["concept"] in wanted)
    p["payout"] = None if payout is None else {k: value(v) for k, v in payout.items()}
    p["symbol"] = TickerResolver().current_symbol(cid)
    p["priceDate"] = value(last["trade_date"]) if last else None
    p["price"] = value(last["close"]) if last else None
    return p


def with_url(f: dict) -> dict:
    """The EDGAR URL, plus a link to the stored original."""
    doc_id = f.pop("sourceDocumentId", None)
    src = f.pop("sourceUrl", None)
    f["documentUrl"] = None if doc_id is None else f"/api/documents/{doc_id}"
    f["url"] = src
    return f


@router.get("/companies/{symbol}/earnings")
def company_earnings(symbol: str, limit: int = Query(16, ge=1, le=100)):
    """Earnings announcements (results 8-Ks) with the market's reaction in the session they first traded, the guidance
    tone read from each press release (estimate), and the estimated next announcement."""
    cid = resolve(symbol)
    bench = db().scalar("SELECT benchmark_symbol FROM company WHERE id = :id", id=cid)
    bbars = db().all("SELECT trade_date, close::float8 AS close FROM price_bar WHERE company_id IS NULL AND symbol = :b ORDER BY trade_date", b=bench)
    sbars = db().all("SELECT trade_date, close::float8 AS close FROM price_bar WHERE company_id = :id ORDER BY trade_date", id=cid)
    acts = pd.DataFrame(db().all("SELECT company_id, symbol, ex_date, action_type, value::float8 AS value FROM corporate_action "
                                 "WHERE company_id = :id OR (company_id IS NULL AND symbol = :b)", id=cid, b=bench),
                        columns=["company_id", "symbol", "ex_date", "action_type", "value"])
    if len(acts):
        acts["ex_date"] = pd.to_datetime(acts["ex_date"])
    cal = pd.DatetimeIndex(pd.to_datetime([b["trade_date"] for b in bbars]))
    tr_s = tr_b = None
    if len(cal) and sbars:
        tr_s = total_return_index(pd.Series([b["close"] for b in sbars], index=pd.to_datetime([b["trade_date"] for b in sbars])),
                                  acts[acts["company_id"] == cid] if len(acts) else None, cal)
        tr_b = total_return_index(pd.Series([b["close"] for b in bbars], index=cal),
                                  acts[acts["company_id"].isna()] if len(acts) else None, cal)
    filings = pd.DataFrame(db().all("SELECT id, company_id, form_type, items, accepted_at FROM filing WHERE company_id = :id AND form_type = '8-K'", id=cid),
                           columns=["id", "company_id", "form_type", "items", "accepted_at"])
    rel_rows = camel_all(db().all("""
        SELECT r.filing_id, r.accession_no, r.exhibit_name, r.guidance_tone, r.guidance_text, r.method, r.extractor_version,
               r.exhibit_document_id, sd.url AS source_url
        FROM earnings_release r LEFT JOIN source_document sd ON sd.id = r.exhibit_document_id WHERE r.company_id = :c""", c=cid))
    releases = pd.DataFrame([{"company_id": cid, "filing_id": r["filingId"], "guidance_tone": r["guidanceTone"]} for r in rel_rows],
                            columns=["company_id", "filing_id", "guidance_tone"])
    rel = {r["filingId"]: r for r in rel_rows}
    ann = announcements(filings, releases, cid, cal) if len(cal) else []
    out = []
    for a in reversed(ann):
        k = a.session_idx
        r = rel.get(a.filing_id, {})
        reaction = None
        if tr_s is not None and k < len(cal):
            x = session_excess(tr_s, tr_b, k)
            reaction = None if x != x else x
        out.append({"filingId": a.filing_id, "acceptedAt": value(a.accepted_at.to_pydatetime()),
                    "sessionDate": value(cal[k].date()) if k < len(cal) else None, "reaction": reaction,
                    "guidanceTone": r.get("guidanceTone", "UNKNOWN"), "guidanceText": r.get("guidanceText"),
                    "exhibitName": r.get("exhibitName"), "exhibitUrl": r.get("sourceUrl"),
                    "exhibitDocumentUrl": None if r.get("exhibitDocumentId") is None else f"/api/documents/{r['exhibitDocumentId']}",
                    "accessionNo": r.get("accessionNo")})
    last_idx = len(cal) - 1
    nxt = next_estimate(ann, last_idx, cal) if ann and len(cal) else None
    next_date = None
    if nxt is not None:
        pos = last_idx + nxt
        next_date = value(cal[pos].date()) if 0 <= pos < len(cal) else value((cal[-1] + pd.tseries.offsets.BDay(max(1, pos - last_idx))).date())
    return {"symbol": TickerResolver().current_symbol(cid), "asOf": value(cal[-1].date()) if len(cal) else None,
            "announcements": out[:limit], "announcementCount": len(ann), "nextEstimate": {"date": next_date, "tradingDays": nxt},
            "note": "The reaction is the stock's total return minus its sector ETF's in the session the release first traded "
                    "(a release after the close trades the next day). The guidance tone is a keyword estimate from the press "
                    "release, with the matched sentence as evidence. The next date is estimated from past announcements."}


INSIDER_PRICE_TOLERANCE = 5.0   # a reported price more than 5x off that day's close is a scale error in the filing


def _close_on_or_before(closes: dict, days: list, d) -> float | None:
    """The close on or before day `d` (a date, or an ISO string as camel-cased rows carry it)."""
    if d is None or not days:
        return None
    import bisect
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    i = bisect.bisect_right(days, d) - 1
    return closes[days[i]] if i >= 0 else None


INSIDER_CODE_LABELS = {"P": "Open-market purchase", "S": "Open-market sale", "A": "Grant or award", "M": "Option exercise",
                       "F": "Tax withholding", "G": "Gift", "D": "Disposition to issuer", "C": "Conversion", "J": "Other",
                       "X": "Option exercise (in the money)", "W": "Will or inheritance"}


@router.get("/companies/{symbol}/insiders")
def company_insiders(symbol: str, limit: int = Query(60, ge=1, le=500)):
    """Insider transactions (Forms 4) as of now: open-market buying and selling over the last 21, 63 and 252 trading days
    (distinct insiders, counts, net dollar value) and the latest transactions."""
    cid = resolve(symbol)
    last = db().one("SELECT close, trade_date FROM price_bar WHERE company_id = :id ORDER BY trade_date DESC LIMIT 1", id=cid)
    rows = camel_all(db().all("""
        SELECT t.id, t.accession_no, t.owner_name, t.owner_cik, t.relationship, t.title, t.trans_date, t.filed_date, t.available_at,
               t.trans_code, t.acquired, t.shares::float8 AS shares, t.price::float8 AS price, t.shares_after::float8 AS shares_after,
               t.ownership, t.security_title, t.source, t.source_document_id, sd.url AS source_url
        FROM insider_transaction t LEFT JOIN source_document sd ON sd.id = t.source_document_id
        WHERE t.company_id = :id ORDER BY t.available_at DESC, t.trans_date DESC, t.id DESC LIMIT :n""", id=cid, n=limit))
    closes = {c["trade_date"]: float(c["close"]) for c in db().all(
        "SELECT trade_date, close::float8 AS close FROM price_bar WHERE company_id = :id ORDER BY trade_date", id=cid)}
    close_days = sorted(closes)
    for r in rows:
        r["codeLabel"] = INSIDER_CODE_LABELS.get(r["transCode"], r["transCode"])
        r["signal"] = r["transCode"] in ("P", "S")
        ref = _close_on_or_before(closes, close_days, r["transDate"])
        p = r["price"]
        suspect = ref is not None and (p is None or p <= 0 or p > INSIDER_PRICE_TOLERANCE * ref or p < ref / INSIDER_PRICE_TOLERANCE)
        r["priceSuspect"] = bool(suspect)
        used = ref if suspect else p
        r["value"] = None if used is None else r["shares"] * used
        with_url(r)
    signals = db().all("""SELECT acquired, shares::float8 AS shares, price::float8 AS price, trans_date, available_at,
                                 coalesce(owner_cik, owner_name) AS owner
                          FROM insider_transaction WHERE company_id = :id AND trans_code IN ('P', 'S')
                            AND available_at > now() - make_interval(days => :d)""", id=cid, d=int(252 * 1.45))
    now = datetime.now(timezone.utc)
    windows = {}
    for label, days in (("21d", 21), ("63d", 63), ("252d", 252)):
        since = now - timedelta(days=int(days * 1.45))
        w = {"buys": 0, "sells": 0, "netValue": 0.0, "boughtValue": 0.0, "soldValue": 0.0}
        buyers, sellers = set(), set()
        for t in signals:
            if t["available_at"] <= since:
                continue
            ref = _close_on_or_before(closes, close_days, t["trans_date"])
            p = t["price"]
            if ref is not None and (p is None or p <= 0 or p > INSIDER_PRICE_TOLERANCE * ref or p < ref / INSIDER_PRICE_TOLERANCE):
                p = ref
            v = t["shares"] * (p or 0.0)
            if t["acquired"]:
                w["buys"] += 1; w["boughtValue"] += v; w["netValue"] += v; buyers.add(t["owner"])
            else:
                w["sells"] += 1; w["soldValue"] += v; w["netValue"] -= v; sellers.add(t["owner"])
        windows[label] = {**w, "buyers": len(buyers), "sellers": len(sellers)}
    total = db().scalar("SELECT count(*) FROM insider_transaction WHERE company_id = :id", id=cid)
    newest = db().scalar("SELECT max(available_at) FROM insider_transaction WHERE company_id = :id", id=cid)
    return {"symbol": TickerResolver().current_symbol(cid), "price": value(last["close"]) if last else None,
            "priceDate": value(last["trade_date"]) if last else None, "windows": windows, "transactions": rows,
            "transactionCount": total, "newestAvailableAt": value(newest),
            "note": "Windows count trading days as 1.45 calendar days; only open-market purchases (P) and sales (S) are signals. "
                    "Grants, exercises, tax withholding and gifts are listed for the record. A reported price more than 5x off "
                    "that day's close is a scale error in the filing: the value then uses the close (priceSuspect)."}


@router.get("/companies/{symbol}/filings")
def company_filings(symbol: str):
    cid = resolve(symbol)
    return [with_url(camel(r)) for r in db().all("""
        SELECT f.id, f.accession_no, f.form_type, f.period_of_report, f.filed_date, f.accepted_at, f.items, f.amends_accession, sd.url AS source_url, f.source_document_id,
               (SELECT count(*) FROM filing_passage p WHERE p.filing_id = f.id) AS passage_count,
               (SELECT count(*) FROM xbrl_fact x WHERE x.filing_id = f.id) AS fact_count
        FROM filing f LEFT JOIN source_document sd ON sd.id = f.source_document_id
        WHERE f.company_id = :id ORDER BY f.accepted_at DESC""", id=cid)]


# --------------------------------------------------------------------------- exposures
def _shape_exposure(r: dict) -> dict:
    m = {camel_key(k): value(r[k]) for k in ("id", "target_type", "target_code", "channel", "share", "basis", "confidence", "method",
                                              "rationale", "available_at", "period_end", "version")}
    if r["filing_id"] is not None:
        url = f"/filings/{r['filing_id']}" if r["filing_url"] is None else r["filing_url"]
        m["filing"] = {"id": r["filing_id"], "accessionNo": r["accession_no"], "formType": r["form_type"], "url": url}
    else:
        m["filing"] = None
    m["passage"] = None if r["passage_id"] is None else {"id": r["passage_id"], "section": r["passage_section"] or "", "text": r["passage_text"]}
    m["fact"] = None if r["fact_id"] is None else {"id": r["fact_id"], "concept": r["fact_concept"], "value": value(r["fact_value"]),
                                                    "dimensions": value(r["fact_dimensions"])}
    return m


@router.get("/companies/{symbol}/exposures")
def exposures(symbol: str, asOf: str | None = None):  # noqa: N803
    cid = resolve(symbol)
    at = parse_ts(asOf)
    ex = [_shape_exposure(r) for r in db().all("""
        SELECT DISTINCT ON (x.target_type, x.target_code, x.exposure_channel)
               x.id, x.target_type, x.target_code, x.exposure_channel AS channel, x.share, x.basis, x.confidence, x.method,
               x.rationale, x.available_at, x.period_end, x.version,
               f.id AS filing_id, f.accession_no, f.form_type, f.source_document_id, sd.url AS filing_url,
               p.id AS passage_id, p.section AS passage_section, p.text AS passage_text,
               xf.id AS fact_id, xf.concept AS fact_concept, xf.value AS fact_value, xf.dimensions AS fact_dimensions
        FROM company_exposure x
        LEFT JOIN filing f ON f.id = x.filing_id
        LEFT JOIN source_document sd ON sd.id = f.source_document_id
        LEFT JOIN filing_passage p ON p.id = x.passage_id
        LEFT JOIN xbrl_fact xf ON xf.id = x.xbrl_fact_id
        WHERE x.company_id = :c AND x.available_at <= :asof
        ORDER BY x.target_type, x.target_code, x.exposure_channel, x.available_at DESC, x.id DESC""", c=cid, asof=at)]
    ex.sort(key=lambda m: -(m["share"] if m["share"] is not None else -1))
    paths = camel_all(db().all("""
        WITH ex AS (
            SELECT DISTINCT ON (x.target_type, x.target_code, x.exposure_channel) x.*
            FROM company_exposure x WHERE x.company_id = :c AND x.available_at <= :asof
            ORDER BY x.target_type, x.target_code, x.exposure_channel, x.available_at DESC, x.id DESC
        )
        SELECT e.id AS event_id, e.title AS event_title, e.category AS event_category, e.published_at AS event_published_at,
               e.evidence_status, t.target_type, t.target_code, ex.id AS exposure_id, ex.exposure_channel AS channel, ex.basis,
               ex.confidence, ex.share, ex.passage_id, f.accession_no AS filing_accession_no, f.id AS filing_id
        FROM ex JOIN event_target t ON t.target_type = ex.target_type AND t.target_code = ex.target_code
        JOIN policy_event e ON e.id = t.event_id AND e.published_at <= :asof
        LEFT JOIN filing f ON f.id = ex.filing_id
        ORDER BY e.published_at DESC, e.id DESC, ex.id LIMIT 200""", c=cid, asof=at))
    return {"asOf": value(at), "exposures": ex, "paths": paths}


# --------------------------------------------------------------------------- filings and documents
@router.get("/filings/{filing_id}")
def filing(filing_id: int):
    r = db().one("""
        SELECT f.id, f.accession_no, f.form_type, f.period_of_report, f.filed_date, f.accepted_at, f.items, f.amends_accession, sd.url AS source_url, f.source_document_id,
               (SELECT symbol FROM ticker_history t WHERE t.company_id = f.company_id ORDER BY valid_from DESC LIMIT 1) AS company_symbol
        FROM filing f LEFT JOIN source_document sd ON sd.id = f.source_document_id WHERE f.id = :id""", id=filing_id)
    if r is None:
        raise NotFound(f"filing {filing_id} not found")
    f = with_url(camel(r))
    f["passages"] = camel_all(db().all("""SELECT id, section, topic, text, extraction_method, extractor_version, char_start, char_end
                                          FROM filing_passage WHERE filing_id = :id ORDER BY char_start""", id=filing_id))
    f["facts"] = camel_all(db().all("""SELECT id, concept, value, unit, period_start, period_end, dimensions, accepted_at
                                       FROM xbrl_fact WHERE filing_id = :id ORDER BY concept, period_end, dims_key LIMIT 500""", id=filing_id))
    return f


@router.get("/documents/{doc_id}")
def document(doc_id: int):
    """Serves the stored original in a sandbox (no script execution, no sniffing)."""
    store = DocumentStore()
    d = store.get(doc_id)
    if d is None:
        raise NotFound(f"document {doc_id} not found")
    body = store.content(d)
    if body is None:
        raise NotFound("document stored as metadata only")
    return Response(content=body, media_type=d.content_type or "application/octet-stream", headers={
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src * data:; style-src 'unsafe-inline' *",
        "X-Content-Type-Options": "nosniff", "X-Source-Url": d.url or "", "X-Source-Version": str(d.version),
        "Cache-Control": "private, max-age=3600"})


# --------------------------------------------------------------------------- forecasts
SUMMARY = """
    SELECT f.id, f.company_id, f.symbol, c.name AS company_name, f.benchmark_symbol, f.model_kind, f.probability, f.prob_low,
           f.prob_high, f.horizon_trading_days, f.as_of_date, f.as_of, f.issued_at, f.issue_mode, f.version, f.supersedes_id,
           f.reason,
           o.window_end_date, o.stock_return, o.benchmark_return, o.excess_return, o.outcome, o.brier
    FROM forecast f JOIN company c ON c.id = f.company_id LEFT JOIN forecast_outcome o ON o.forecast_id = f.id"""


def _summary(r: dict) -> dict:
    m = camel(r)
    end = m.pop("windowEndDate")
    outcome = {k: m.pop(k) for k in ("stockReturn", "benchmarkReturn", "excessReturn", "outcome", "brier")}
    outcome["windowEndDate"] = end
    m["outcome"] = None if end is None else outcome
    return m


@router.get("/forecasts/current")
def forecasts_current():
    return [_summary(r) for r in db().all("""
        WITH latest_date AS (SELECT max(as_of_date) AS d FROM forecast),
             latest AS (SELECT DISTINCT ON (company_id, model_kind) id FROM forecast, latest_date
                        WHERE as_of_date = latest_date.d ORDER BY company_id, model_kind, version DESC)
        """ + SUMMARY + " WHERE f.id IN (SELECT id FROM latest) ORDER BY f.symbol, f.model_kind")]


@router.get("/forecasts/history")
def forecasts_history(symbol: str | None = None, modelKind: str | None = None):  # noqa: N803
    cid = None
    if symbol and symbol.strip():
        cid = db().scalar("SELECT company_id FROM ticker_history WHERE upper(symbol) = upper(:s) ORDER BY valid_from DESC LIMIT 1", s=symbol)
        cid = -1 if cid is None else cid
    return [_summary(r) for r in db().all(SUMMARY + """
         WHERE (CAST(:c AS bigint) IS NULL OR f.company_id = :c) AND (CAST(:k AS text) IS NULL OR f.model_kind = :k)
        ORDER BY f.as_of_date DESC, f.symbol, f.model_kind, f.version DESC LIMIT 2000""", c=cid, k=modelKind or None)]


@router.get("/forecasts/{forecast_id}")
def forecast(forecast_id: int):
    r = db().one(SUMMARY + " WHERE f.id = :id", id=forecast_id)
    if r is None:
        raise NotFound(f"forecast {forecast_id} not found")
    m = _summary(r)
    m.update(camel(db().one("""SELECT target, uncertainty_note, features, explanation, sources, content_sha256, model_version_id, series_key
                               FROM forecast WHERE id = :id""", id=forecast_id)))
    series = m.pop("seriesKey")
    mv = m.pop("modelVersionId")
    m["modelVersion"] = camel(db().one("""SELECT id, model_kind, algorithm, trained_through, training_cutoff, n_samples, feature_names, code_version
                                          FROM model_version WHERE id = :id""", id=mv))
    m["versions"] = camel_all(db().all("SELECT id, version, issued_at, probability, reason, supersedes_id FROM forecast WHERE series_key = :s ORDER BY version",
                                       s=series))
    return m


# --------------------------------------------------------------------------- accuracy
@router.get("/accuracy")
def accuracy():
    ev = db().one("""SELECT id, run_at, data_cutoff, config, metrics, comparison, calibration, trading, folds, verdict
                     FROM model_evaluation ORDER BY id DESC LIMIT 1""")
    by_mode: dict[str, dict] = {}
    for r in db().all("""
        WITH latest AS (SELECT DISTINCT ON (series_key) * FROM forecast ORDER BY series_key, version DESC)
        SELECT l.issue_mode, l.model_kind, count(*) AS issued, count(o.forecast_id) AS resolved, avg(o.brier) AS brier,
               avg(CASE WHEN o.forecast_id IS NULL THEN NULL WHEN (l.probability > 0.5) = o.outcome THEN 1.0 ELSE 0.0 END) AS hit_rate,
               avg(CASE WHEN o.outcome THEN 1.0 WHEN o.forecast_id IS NOT NULL THEN 0.0 END) AS base_rate
        FROM latest l LEFT JOIN forecast_outcome o ON o.forecast_id = l.id GROUP BY l.issue_mode, l.model_kind"""):
        m = camel(r)
        by_mode.setdefault(m.pop("issueMode"), {})[m.pop("modelKind")] = m
    return {"evaluation": camel(ev) if ev else None, "issued": by_mode.get("LIVE", {}), "issuedByMode": by_mode,
            "liveTest": _live_test(by_mode.get("LIVE", {}))}


def _live_test(live: dict) -> dict:
    """The pre-registered test scored on the resolved LIVE forecasts (latest version of each series) per model."""
    return {**LIVE_TEST, "models": {kind: live_test_of(kind) for kind in live}}


def live_test_of(kind: str) -> dict:
    """One model's live-test verdict (evaluation.LIVE_TEST) on its resolved LIVE forecasts, latest version of each series.
    Also read by the portfolio advice (ADR-0004), whose model layer is graded by the book model's verdict."""
    from sklearn.metrics import roc_auc_score

    rows = db().all("""
        WITH latest AS (SELECT DISTINCT ON (series_key) * FROM forecast WHERE issue_mode = 'LIVE' AND model_kind = :k
                        ORDER BY series_key, version DESC)
        SELECT l.probability::float8 AS p, o.outcome FROM latest l JOIN forecast_outcome o ON o.forecast_id = l.id""", k=kind)
    y = [1.0 if r["outcome"] else 0.0 for r in rows]
    p = [r["p"] for r in rows]
    auc = float(roc_auc_score(y, p)) if len(set(y)) == 2 else None
    brier = float(np.mean([(a - b) ** 2 for a, b in zip(p, y)])) if rows else None
    base_rate = float(np.mean(y)) if rows else None
    return live_test_verdict(len(rows), auc, brier, base_rate)


# --------------------------------------------------------------------------- strategies, decisions, time machine
def _latest_run():
    r = db().one("SELECT id, run_at, data_cutoff, oos_start, config, summary FROM strategy_run ORDER BY id DESC LIMIT 1")
    return camel(r) if r else None


@router.get("/strategies")
def strategies():
    run = _latest_run()
    results = [] if run is None else camel_all(db().all("""
        SELECT strategy_key, family, name, description, params, metrics, equity, yearly, cost_sensitivity, verdict
        FROM strategy_result WHERE run_id = :r
        ORDER BY (metrics->>'sharpe')::float8 DESC NULLS LAST, strategy_key""", r=run["id"]))
    return {"run": run, "results": results}


@router.get("/strategies/{key}")
def strategy(key: str):
    run = _latest_run()
    if run is None:
        raise NotFound("no strategy backtest has been run yet")
    rid = run["id"]
    result = db().one("""SELECT strategy_key, family, name, description, params, metrics, equity, yearly, cost_sensitivity, verdict
                         FROM strategy_result WHERE run_id = :r AND strategy_key = :k""", r=rid, k=key)
    if result is None:
        raise NotFound(f"unknown strategy {key}")
    ref = db().one("SELECT strategy_key, name, equity FROM strategy_result WHERE run_id = :r AND strategy_key = :k", r=rid, k=REFERENCE)
    return {
        "run": run, "result": camel(result), "reference": camel(ref) if ref else None,
        "trades": camel_all(db().all("""
            SELECT company_id, symbol, entry_date, exit_date, trade_return, holding_days, entry_reason, exit_reason
            FROM strategy_trade WHERE run_id = :r AND strategy_key = :k ORDER BY entry_date DESC, symbol LIMIT 2000""", r=rid, k=key)),
        "tradeCount": db().scalar("SELECT count(*) FROM strategy_trade WHERE run_id = :r AND strategy_key = :k", r=rid, k=key),
    }


@router.get("/decisions")
def decisions(date_: str | None = Query(None, alias="date")):
    """The recorded book's decisions for one day. Days decided under an earlier book (AI_RANK_SIZED on 2026-10-05, AI_GBM
    before) are served under the key they were stored with, so the history stays readable."""
    stored = db().all("""SELECT as_of_date, strategy_key FROM strategy_decision WHERE strategy_key IN (:a, :b, :c)
                         GROUP BY as_of_date, strategy_key ORDER BY as_of_date DESC""", a=BOOK_KEYS[0], b=BOOK_KEYS[1], c=BOOK_KEYS[2])
    key_on: dict = {}
    for r in stored:                       # the recorded book first, then the key used before it existed
        k = key_on.get(r["as_of_date"])
        if k is None or BOOK_KEYS.index(r["strategy_key"]) < BOOK_KEYS.index(k):
            key_on[r["as_of_date"]] = r["strategy_key"]
    dates = list(key_on)[:60]
    d = parse_date(date_) or (dates[0] if dates else None)
    key = key_on.get(d)
    rows = [] if key is None else camel_all(db().all("""
        SELECT s.id, s.company_id, c.name,
               (SELECT symbol FROM ticker_history t WHERE t.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
               s.as_of_date, s.strategy_key, s.action, s.probability, s.entry_p, s.exit_p, s.weight, s.rank,
               s.factors, s.rule_votes, s.model, s.issued_at,
               e.text AS explanation, e.model AS explanation_model,
               r.stance AS review_stance, r.confidence AS review_confidence, r.rationale AS review_rationale,
               r.flags AS review_flags, r.veto AS review_veto, r.model AS review_model
        FROM strategy_decision s JOIN company c ON c.id = s.company_id
        LEFT JOIN decision_explanation e ON e.decision_id = s.id
        LEFT JOIN decision_review r ON r.decision_id = s.id
        WHERE s.strategy_key = :k AND s.as_of_date = :d
        ORDER BY CASE s.action WHEN 'ENTER' THEN 0 WHEN 'EXIT' THEN 1 WHEN 'HOLD' THEN 2 ELSE 3 END, s.probability DESC""", k=key, d=d))
    for row in rows:   # fold the reviewer's columns into one object, null when the decision was not reviewed
        rv = {k: row.pop("review" + k[0].upper() + k[1:]) for k in ("stance", "confidence", "rationale", "flags", "veto", "model")}
        row["review"] = rv if rv["stance"] else None
        # ADR-0002: the calibrated probability is stored inside the model JSON; null for decisions made before it existed
        row["probabilityCalibrated"] = ((row.get("model") or {}).get("calibration") or {}).get("probability")
    return {"asOfDate": value(d), "dates": [value(x) for x in dates], "decisions": rows}


@router.get("/doublers")
def doubler_study():
    """Latest doubler study (null `run` until one has been made) and the list of earlier runs."""
    runs = camel_all(db().all("SELECT id, run_at, data_cutoff, headline FROM doubler_study_run ORDER BY id DESC LIMIT 20"))
    latest = db().one("SELECT id, run_at, data_cutoff, headline, result FROM doubler_study_run ORDER BY id DESC LIMIT 1")
    return {"run": camel(latest) if latest else None, "runs": runs}


@router.get("/doublers/{run_id}")
def doubler_study_run(run_id: int):
    r = db().one("SELECT id, run_at, data_cutoff, headline, result FROM doubler_study_run WHERE id = :id", id=run_id)
    if r is None:
        raise NotFound(f"no doubler study {run_id}")
    return camel(r)


@router.get("/signals")
def signal_study():
    """Latest signal-health study (null `run` until one has been made) and the list of earlier runs."""
    runs = camel_all(db().all("SELECT id, run_at, data_cutoff, headline FROM signal_study_run ORDER BY id DESC LIMIT 20"))
    latest = db().one("SELECT id, run_at, data_cutoff, headline, result FROM signal_study_run ORDER BY id DESC LIMIT 1")
    return {"run": camel(latest) if latest else None, "runs": runs}


@router.get("/signals/{run_id}")
def signal_study_run(run_id: int):
    r = db().one("SELECT id, run_at, data_cutoff, headline, result FROM signal_study_run WHERE id = :id", id=run_id)
    if r is None:
        raise NotFound(f"no signal study {run_id}")
    return camel(r)


@router.get("/ablation")
def feature_ablation():
    """Latest feature fragility study (null `run` until one has been made) and the list of earlier runs."""
    runs = camel_all(db().all("SELECT id, run_at, data_cutoff, headline FROM feature_ablation_run ORDER BY id DESC LIMIT 20"))
    latest = db().one("SELECT id, run_at, data_cutoff, headline, result FROM feature_ablation_run ORDER BY id DESC LIMIT 1")
    return {"run": camel(latest) if latest else None, "runs": runs}


@router.get("/ablation/{run_id}")
def feature_ablation_run(run_id: int):
    r = db().one("SELECT id, run_at, data_cutoff, headline, result FROM feature_ablation_run WHERE id = :id", id=run_id)
    if r is None:
        raise NotFound(f"no feature ablation study {run_id}")
    return camel(r)


@router.get("/setups")
def setup_study():
    """Latest setup playbook (null `run` until one has been made) and the list of earlier runs."""
    runs = camel_all(db().all("SELECT id, run_at, data_cutoff, headline FROM setup_study_run ORDER BY id DESC LIMIT 20"))
    latest = db().one("SELECT id, run_at, data_cutoff, headline, result FROM setup_study_run ORDER BY id DESC LIMIT 1")
    return {"run": camel(latest) if latest else None, "runs": runs}


@router.get("/setups/{run_id}")
def setup_study_run(run_id: int):
    r = db().one("SELECT id, run_at, data_cutoff, headline, result FROM setup_study_run WHERE id = :id", id=run_id)
    if r is None:
        raise NotFound(f"no setup study {run_id}")
    return camel(r)


@router.get("/timemachine")
def time_machine_runs():
    return camel_all(db().all("SELECT id, as_of_date, run_at, data_cutoff, headline FROM time_machine_run ORDER BY id DESC LIMIT 50"))


@router.get("/timemachine/{run_id}")
def time_machine_run(run_id: int):
    r = db().one("SELECT id, as_of_date, run_at, data_cutoff, headline, result FROM time_machine_run WHERE id = :id", id=run_id)
    if r is None:
        raise NotFound(f"no time machine run {run_id}")
    return camel(r)
