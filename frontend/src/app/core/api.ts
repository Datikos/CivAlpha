import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AddCompanyRequest,
  AddCompanyResponse,
  DiscoverResponse,
  ExpandRequest,
  ExpandResponse,
  CompanyProfile,
  Job,
  ModelKind,
  NewEventRequest,
  NewEventResponse,
  SecMatch,
} from './models';

/** All calls are relative to /api (nginx / dev-server proxy forwards them to the backend). */
const BASE = '/api';
const enc = encodeURIComponent;

function withQuery(path: string, query: Record<string, string | null | undefined>): string {
  let params = new HttpParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== null && v !== undefined && v !== '') params = params.set(k, v);
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

/** URL builders for GET endpoints (used with httpResource). */
export const apiUrl = {
  meta: () => `${BASE}/meta`,
  companies: () => `${BASE}/companies`,
  company: (symbol: string) => `${BASE}/companies/${enc(symbol)}`,
  prices: (symbol: string, from?: string | null) =>
    withQuery(`${BASE}/companies/${enc(symbol)}/prices`, { from }),
  financials: (symbol: string, asOf?: string | null) =>
    withQuery(`${BASE}/companies/${enc(symbol)}/financials`, { asOf }),
  dividends: (symbol: string) => `${BASE}/companies/${enc(symbol)}/dividends`,
  insiders: (symbol: string) => `${BASE}/companies/${enc(symbol)}/insiders`,
  earnings: (symbol: string) => `${BASE}/companies/${enc(symbol)}/earnings`,
  filings: (symbol: string) => `${BASE}/companies/${enc(symbol)}/filings`,
  filing: (id: number | string) => `${BASE}/filings/${enc(String(id))}`,
  exposures: (symbol: string, asOf?: string | null) =>
    withQuery(`${BASE}/companies/${enc(symbol)}/exposures`, { asOf }),
  events: (category?: string | null) => withQuery(`${BASE}/events`, { category }),
  event: (id: number | string) => `${BASE}/events/${enc(String(id))}`,
  forecastsCurrent: () => `${BASE}/forecasts/current`,
  forecastsHistory: (symbol?: string | null, modelKind?: ModelKind | '' | null) =>
    withQuery(`${BASE}/forecasts/history`, { symbol, modelKind }),
  forecast: (id: number | string) => `${BASE}/forecasts/${enc(String(id))}`,
  accuracy: () => `${BASE}/accuracy`,
  strategies: () => `${BASE}/strategies`,
  strategy: (key: string) => `${BASE}/strategies/${enc(key)}`,
  decisions: (date?: string | null) => withQuery(`${BASE}/decisions`, { date }),
  setups: () => `${BASE}/setups`,
  setupRun: (id: number | string) => `${BASE}/setups/${enc(String(id))}`,
  doublers: () => `${BASE}/doublers`,
  doublerRun: (id: number | string) => `${BASE}/doublers/${enc(String(id))}`,
  timeMachineRuns: () => `${BASE}/timemachine`,
  timeMachineRun: (id: number | string) => `${BASE}/timemachine/${enc(String(id))}`,
  jobs: () => `${BASE}/admin/jobs`,
  universe: () => `${BASE}/admin/universe`,
};

/** Mutating calls. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  createEvent(body: NewEventRequest): Observable<NewEventResponse> {
    return this.http.post<NewEventResponse>(`${BASE}/events`, body);
  }

  runPipeline(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/pipeline/run`, {});
  }

  evaluate(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/evaluate`, {});
  }

  issueForecasts(asOfDate?: string | null): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/forecasts/issue`, asOfDate ? { asOfDate } : {});
  }

  backtestStrategies(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/strategies/backtest`, {});
  }

  decide(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/strategies/decide`, {});
  }

  setupStudy(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/setups/study`, {});
  }

  doublerStudy(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/doublers/study`, {});
  }

  timeMachine(asOfDate: string): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/timemachine`, { asOfDate });
  }

  resolveOutcomes(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/outcomes/resolve`, {});
  }

  ingestSec(symbol: string): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/sec/ingest`, { symbol });
  }

  syncPrices(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/prices/sync`, {});
  }

  lookupSymbol(symbol: string): Observable<SecMatch> {
    return this.http.get<SecMatch>(withQuery(`${BASE}/admin/universe/lookup`, { symbol }));
  }

  /** Name, CIK, exchange and a suggested sector / benchmark for a ticker, from SEC EDGAR. */
  enrichSymbol(symbol: string): Observable<CompanyProfile> {
    return this.http.get<CompanyProfile>(withQuery(`${BASE}/admin/universe/enrich`, { symbol }));
  }

  /** Candidates for a universe expansion: listed on `exchanges`, public float at least `minPublicFloat` US dollars. */
  discoverCompanies(exchanges: string[], minPublicFloat: number, limit: number): Observable<DiscoverResponse> {
    return this.http.get<DiscoverResponse>(
      withQuery(`${BASE}/admin/universe/discover`, { exchanges: exchanges.join(','), minPublicFloat: String(minPublicFloat), limit: String(limit) }),
    );
  }

  /** Queues one job that adds every candidate (sector from its SIC code) and starts its data loads. */
  expandUniverse(body: ExpandRequest): Observable<ExpandResponse> {
    return this.http.post<ExpandResponse>(`${BASE}/admin/universe/expand`, body);
  }

  addCompany(body: AddCompanyRequest): Observable<AddCompanyResponse> {
    return this.http.post<AddCompanyResponse>(`${BASE}/admin/universe/companies`, body);
  }

  editCompany(
    id: number,
    body: { name?: string; sector?: string; industry?: string; benchmarkSymbol?: string; tags?: string[] },
  ): Observable<unknown> {
    return this.http.put(`${BASE}/admin/universe/companies/${id}`, body);
  }

  /** Replaces a company's user-defined tags; an empty list clears them. */
  setCompanyTags(id: number, tags: string[]): Observable<{ id: number; tags: string[] }> {
    return this.http.put<{ id: number; tags: string[] }>(`${BASE}/admin/universe/companies/${id}/tags`, { tags });
  }

  removeCompany(id: number, effectiveDate?: string | null): Observable<unknown> {
    return this.http.post(
      `${BASE}/admin/universe/companies/${id}/remove`,
      effectiveDate ? { effectiveDate } : {},
    );
  }

  restoreCompany(id: number, effectiveDate?: string | null): Observable<unknown> {
    return this.http.post(
      `${BASE}/admin/universe/companies/${id}/restore`,
      effectiveDate ? { effectiveDate } : {},
    );
  }

  changeTicker(id: number, symbol: string, effectiveDate?: string | null): Observable<unknown> {
    return this.http.post(`${BASE}/admin/universe/companies/${id}/ticker`, {
      symbol,
      effectiveDate: effectiveDate || null,
    });
  }

  deleteCompany(id: number): Observable<unknown> {
    return this.http.delete(`${BASE}/admin/universe/companies/${id}`);
  }

  importPrices(file: File): Observable<Job> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<Job>(`${BASE}/admin/prices/import`, form);
  }
}

/** Human-readable message for an HTTP / resource error. */
export function errorMessage(err: unknown): string {
  if (!err) return '';
  if (err instanceof HttpErrorResponse) {
    if (err.status === 0) {
      return 'Cannot reach the CivAlpha API (/api). The backend may be down or still starting.';
    }
    const body = err.error as { message?: string; error?: string; detail?: string } | string | null;
    let detail = '';
    if (typeof body === 'string') detail = body.length < 300 ? body : '';
    else if (body) detail = body.message ?? body.detail ?? body.error ?? '';
    if (err.status === 502 || err.status === 503 || err.status === 504) {
      return `The CivAlpha backend is unavailable (HTTP ${err.status}). It may be down or still starting.`;
    }
    if (err.status === 404) return `Not found (404)${detail ? `: ${detail}` : '.'}`;
    return `API error ${err.status} ${err.statusText || ''}${detail ? `: ${detail}` : ''}`.trim();
  }
  if (err instanceof Error) {
    const cause = (err as Error & { cause?: unknown }).cause;
    if (cause) return errorMessage(cause);
    return err.message;
  }
  return String(err);
}

/**
 * Safe read of a resource value: `value()` throws while a resource is in the error state,
 * so computed views go through this helper.
 */
export function valueOf<T>(r: {
  hasValue(): boolean;
  value(): T;
}): Exclude<T, undefined> | undefined {
  return r.hasValue() ? (r.value() as Exclude<T, undefined>) : undefined;
}
