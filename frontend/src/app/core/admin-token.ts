import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injector, inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { MetaService } from './meta.service';

const KEY = 'civalpha.adminToken';

/** Admin token kept for the browser tab only (sessionStorage); empty when storage is unavailable. */
export function getAdminToken(): string {
  try {
    return sessionStorage.getItem(KEY) ?? '';
  } catch {
    return '';
  }
}

export function setAdminToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the token simply is not remembered */
  }
}

/** Adds X-Admin-Token to admin, portfolio and write requests, mirroring the backend's protected_request rules
 * (the portfolio needs it on reads too: holdings are personal data, ADR-0004). Not while a person is signed in with a
 * password (accounts mode): the backend lets the admin token win over the session cookie, so sending it would act as
 * the installation instead of that person and reach the portfolios of no owner (ADR-0005). */
export const adminTokenInterceptor: HttpInterceptorFn = (req, next) => {
  const injector = inject(Injector);
  const stored = getAdminToken();
  const token = stored && !signedInWithPassword(injector) ? stored : '';
  const isApi = req.url.startsWith('/api/');
  const write = !SAFE.includes(req.method);
  const privateRead = req.url.startsWith('/api/admin/') || req.url.startsWith('/api/portfolio');
  const needs = isApi && (privateRead || write);
  const headers: Record<string, string> = {};
  if (token && needs) headers['X-Admin-Token'] = token;
  // The backend refuses a cookie-authenticated write without it (ADR-0005): a cross-site form cannot set a header.
  if (isApi && write) headers['X-CivAlpha-Request'] = '1';
  return next(Object.keys(headers).length ? req.clone({ setHeaders: headers }) : req);
};

const SAFE = ['GET', 'HEAD', 'OPTIONS'];

function signedInWithPassword(injector: Injector): boolean {
  const auth = injector.get(AuthService);
  return auth.accountsMode() && auth.hasAccount();
}
/** Calls whose 401 is an answer for the page that made them, never a reason to leave it. */
const NO_REDIRECT = ['/api/auth/login', '/api/auth/me'];

/**
 * Accounts mode (ADR-0005): a 401 means the session ended, so go to /login and come back afterwards; a 403
 * PASSWORD_CHANGE_REQUIRED means the user must choose their own password first, so go to /account. Token mode keeps
 * the raw 401 (the pages say "enter the admin token"). Services are looked up lazily: AuthService and MetaService
 * load through HttpClient, which runs this interceptor.
 */
export const authRedirectInterceptor: HttpInterceptorFn = (req, next) => {
  const injector = inject(Injector);
  return next(req).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && req.url.startsWith('/api/')) handle(err, req.url.split('?')[0], injector);
      return throwError(() => err);
    }),
  );
};

function handle(err: HttpErrorResponse, path: string, injector: Injector): void {
  const router = injector.get(Router);
  const here = router.url;
  if (err.status === 401 && !NO_REDIRECT.includes(path)) {
    const auth = injector.get(AuthService);
    const mode = auth.authMode() ?? injector.get(MetaService).meta()?.authMode;
    if (mode !== 'accounts' || here.startsWith('/login')) return;
    auth.reload();
    void router.navigate(['/login'], { queryParams: { next: here } });
  } else if (err.status === 403 && (err.error as { code?: string } | null)?.code === 'PASSWORD_CHANGE_REQUIRED') {
    if (here.startsWith('/account')) return;
    injector.get(AuthService).reload();
    void router.navigate(['/account']);
  }
}
