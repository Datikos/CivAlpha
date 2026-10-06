"""Universe expansion: add many SEC-discovered companies at once (the UNIVERSE_EXPAND job).

Each candidate is profiled from EDGAR (sector, benchmark ETF and industry suggested from its SIC code) and added as a
member from today with the chosen tag. An ambiguous SIC code still adds the company, under the most likely sector, with
the extra tag SECTOR_REVIEW_TAG so the Universe page can be used to confirm it; a company whose SIC code maps to no
sector is skipped and listed in the log. Prices arrive through one PRICE_SYNC job (the provider's quota decides how many
symbols each run can fetch; the rest follow on later runs). Filings arrive with the next pipeline run, which ingests
every member; an inline ingest is optional because a first ingest takes from seconds to minutes per company (a mega-cap
has a decade of 10-Ks and 10-Qs to parse) and blocks the worker queue meanwhile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .errors import BadRequest
from .jobs import Jobs, Log, Progress
from .sec.profile import CompanyProfiler
from .sql import Db, db
from .universe import UniverseService, normalize_tags

SECTOR_REVIEW_TAG = "sector review"
MAX_PER_JOB = 1000


@dataclass
class Expansion:
    added: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    review: int = 0
    price_job: int | None = None


def clean_candidates(raw) -> list[dict]:
    """Validates the candidate list a client sends back from a discovery: symbol and CIK are required."""
    if not isinstance(raw, list) or not raw:
        raise BadRequest("candidates is required: the list returned by the discovery")
    if len(raw) > MAX_PER_JOB:
        raise BadRequest(f"at most {MAX_PER_JOB} companies per expansion")
    out, seen = [], set()
    for c in raw:
        c = c if isinstance(c, dict) else {}
        sym = str(c.get("symbol") or "").upper().strip()
        cik = str(c.get("cik") or "").strip()
        if not sym or not cik:
            raise BadRequest("every candidate needs a symbol and a cik")
        if cik in seen:
            continue
        seen.add(cik)
        out.append({"symbol": sym, "cik": cik, "name": str(c.get("name") or "").strip() or None})
    return out


class UniverseExpansion:
    def __init__(self, database: Db | None = None, universe: UniverseService | None = None,
                 profiler: CompanyProfiler | None = None, jobs: Jobs | None = None):
        self.db = database or db()
        self.universe = universe or UniverseService(self.db)
        self.profiler = profiler or CompanyProfiler(self.db)
        self.jobs = jobs or Jobs(self.db)

    def run(self, params: dict, log: Log, ingest=None) -> Expansion:
        """`ingest(company_id, log)` is called for every added company when params["ingestSec"] is true."""
        cands = clean_candidates(params.get("candidates"))
        tags = normalize_tags(params.get("tags") or ([params["tag"]] if params.get("tag") else []))
        since = date.fromisoformat(params["memberSince"]) if params.get("memberSince") else None
        out = Expansion()
        log(f"expanding the universe by up to {len(cands)} companies" + (f", tagged {', '.join(tags)}" if tags else ""))
        p = Progress(log, len(cands))
        for i, c in enumerate(cands, 1):
            p.at(f"adding {c['symbol']} ({i}/{len(cands)})")
            try:
                self._add_one(c, tags, since, out, log)
            except Exception as e:  # noqa: BLE001 - one company must not stop the rest
                out.skipped.append({**c, "reason": str(e)})
                log(f"{c['symbol']}: skipped ({e})")
            p.step()
        log(f"added {len(out.added)} companies ({out.review} with the sector marked for review), skipped {len(out.skipped)}")
        if out.added and params.get("syncPrices", True):
            job = self.jobs.submit("PRICE_SYNC", {"reason": f"universe expanded by {len(out.added)} companies"})
            out.price_job = int(job["id"])
            log(f"queued price sync (job #{out.price_job}); a provider quota may spread the download over several runs")
        if out.added and params.get("ingestSec", False) and ingest is not None:
            log(f"ingesting SEC filings for {len(out.added)} companies (seconds to minutes each; the price sync waits meanwhile)")
            p.extend(len(out.added))
            for i, a in enumerate(out.added, 1):
                p.at(f"SEC filings {a['symbol']} ({i}/{len(out.added)})")
                try:
                    ingest(a["companyId"], p.child())
                except Exception as e:  # noqa: BLE001
                    log(f"{a['symbol']}: SEC ingest failed ({e}); the next pipeline run retries it")
                p.step()
        elif out.added:
            log("SEC filings for the new companies arrive with the next pipeline run (Data & pipeline -> Run pipeline)")
        p.finish()
        return out

    def _add_one(self, c: dict, tags: list[str], since: date | None, out: Expansion, log: Log) -> None:
        p = self.profiler.profile(c["symbol"])
        if p.existing:
            out.skipped.append({**c, "reason": "already in the database"})
            log(f"{c['symbol']}: already in the database as {p.existing['symbol']}")
            return
        if p.sector is None or p.benchmark_symbol is None:
            why = "; ".join(p.warnings) or "no sector could be suggested from its SIC code"
            out.skipped.append({**c, "reason": why})
            log(f"{c['symbol']}: skipped ({why})")
            return
        cik = p.cik or c["cik"]
        name = p.name or c.get("name") or c["symbol"]
        extra = [SECTOR_REVIEW_TAG] if p.sector_confidence == "review" else []
        cid = self.universe.add(c["symbol"], name, cik, p.sector, p.industry, p.benchmark_symbol, since, tags=tags + extra)
        out.review += bool(extra)
        out.added.append({"companyId": cid, "symbol": c["symbol"], "name": name, "sector": p.sector,
                          "benchmarkSymbol": p.benchmark_symbol, "review": bool(extra)})
        log(f"{c['symbol']}: added as {p.sector} ({p.benchmark_symbol})" + (" - sector needs review" if extra else ""))
