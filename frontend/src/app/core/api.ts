import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AddCompanyRequest,
  AddCompanyResponse,
  AdviceResponse,
  AppUser,
  AuthMe,
  EditUserRequest,
  NewApiToken,
  NewUserRequest,
  PortfolioSummary,
  Holding,
  HoldingRequest,
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
  signals: () => `${BASE}/signals`,
  signalRun: (id: number | string) => `${BASE}/signals/${enc(String(id))}`,
  ablation: () => `${BASE}/ablation`,
  ablationRun: (id: number | string) => `${BASE}/ablation/${enc(String(id))}`,
  setups: () => `${BASE}/setups`,
  setupRun: (id: number | string) => `${BASE}/setups/${enc(String(id))}`,
  doublers: () => `${BASE}/doublers`,
  doublerRun: (id: number | string) => `${BASE}/doublers/${enc(String(id))}`,
  timeMachineRuns: () => `${BASE}/timemachine`,
  timeMachineRun: (id: number | string) => `${BASE}/timemachine/${enc(String(id))}`,
  jobs: () => `${BASE}/admin/jobs`,
  universe: () => `${BASE}/admin/universe`,
  portfolios: () => `${BASE}/portfolios`,
  portfolioHoldings: (portfolioId?: number | null) =>
    withQuery(`${BASE}/portfolio/holdings`, { portfolioId: idParam(portfolioId) }),
  portfolioAdvice: (date?: string | null, portfolioId?: number | null) =>
    withQuery(`${BASE}/portfolio/advice`, { date, portfolioId: idParam(portfolioId) }),
  portfolioTrackRecord: (portfolioId?: number | null) =>
    withQuery(`${BASE}/portfolio/track-record`, { portfolioId: idParam(portfolioId) }),
  authMe: () => `${BASE}/auth/me`,
  authTokens: () => `${BASE}/auth/tokens`,
  adminUsers: () => `${BASE}/admin/users`,
  adminAudit: (limit?: number | null, userId?: number | null) =>
    withQuery(`${BASE}/admin/audit`, { limit: idParam(limit), userId: idParam(userId) }),
};

function idParam(v: number | null | undefined): string | null {
  return v === null || v === undefined ? null : String(v);
}

/** Adds portfolioId to a write body only when one is chosen, so the backend's default (the first portfolio) applies otherwise. */
function withPortfolio<T extends object>(body: T, portfolioId?: number | null): T & { portfolioId?: number } {
  return portfolioId === null || portfolioId === undefined ? body : { ...body, portfolioId };
}

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

  signalStudy(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/signals/study`, {});
  }

  setupStudy(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/setups/study`, {});
  }

  ablationStudy(): Observable<Job> {
    return this.http.post<Job>(`${BASE}/admin/ablation/study`, {});
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

  /** Enters or replaces one holding of the owner's portfolio (ADR-0004). */
  setHolding(body: HoldingRequest): Observable<Holding> {
    return this.http.put<Holding>(`${BASE}/portfolio/holdings`, body);
  }

  /** The symbol goes in the body, so access logs do not record which stocks are held. */
  removeHolding(symbol: string, portfolioId?: number | null): Observable<unknown> {
    return this.http.post(`${BASE}/portfolio/holdings/remove`, withPortfolio({ symbol }, portfolioId));
  }

  setCash(cashUsd: number, portfolioId?: number | null): Observable<{ cashUsd: number }> {
    return this.http.put<{ cashUsd: number }>(`${BASE}/portfolio/cash`, withPortfolio({ cashUsd }, portfolioId));
  }

  /** Advice on the book's latest decision date for the portfolio as it is now. */
  refreshAdvice(portfolioId?: number | null): Observable<AdviceResponse> {
    return this.http.post<AdviceResponse>(`${BASE}/portfolio/advice`, withPortfolio({}, portfolioId));
  }

  /** A new, empty portfolio of the caller (ADR-0005); 400 for a duplicate name or more than 20. */
  createPortfolio(name: string): Observable<PortfolioSummary> {
    return this.http.post<PortfolioSummary>(`${BASE}/portfolios`, { name });
  }

  // ---------- sign-in and the caller's own account (ADR-0005) ----------

  login(username: string, password: string): Observable<AuthMe> {
    return this.http.post<AuthMe>(`${BASE}/auth/login`, { username, password });
  }

  logout(): Observable<{ signedIn: false }> {
    return this.http.post<{ signedIn: false }>(`${BASE}/auth/logout`, {});
  }

  /** Stops every other session and every personal token of the user. */
  changePassword(currentPassword: string, newPassword: string): Observable<AuthMe> {
    return this.http.post<AuthMe>(`${BASE}/auth/password`, { currentPassword, newPassword });
  }

  /** The answer carries the token itself, shown this once. */
  createToken(name: string, days: number): Observable<NewApiToken> {
    return this.http.post<NewApiToken>(`${BASE}/auth/tokens`, { name, days });
  }

  revokeToken(id: number): Observable<unknown> {
    return this.http.post(`${BASE}/auth/tokens/${id}/revoke`, {});
  }

  // ---------- the owner's account management (ADR-0005) ----------

  createUser(body: NewUserRequest): Observable<AppUser> {
    return this.http.post<AppUser>(`${BASE}/admin/users`, body);
  }

  editUser(id: number, body: EditUserRequest): Observable<AppUser> {
    return this.http.put<AppUser>(`${BASE}/admin/users/${id}`, body);
  }

  /** A new first password: the user must change it at the next sign-in; their sessions and tokens stop. */
  resetUserPassword(id: number, password: string): Observable<AppUser> {
    return this.http.post<AppUser>(`${BASE}/admin/users/${id}/password`, { password });
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

/** The backend's own sentence (`{"error": "..."}`) when there is one, verbatim; otherwise errorMessage. */
export function backendError(err: unknown): string {
  if (err instanceof HttpErrorResponse && err.status !== 0) {
    const body = err.error as { error?: unknown } | null;
    if (body && typeof body === 'object' && typeof body.error === 'string' && body.error) return body.error;
  }
  return errorMessage(err);
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
