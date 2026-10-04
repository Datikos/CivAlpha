import { HttpInterceptorFn } from '@angular/common/http';

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

/** Adds X-Admin-Token to admin and write requests, mirroring the backend's AdminTokenFilter rules. */
export const adminTokenInterceptor: HttpInterceptorFn = (req, next) => {
  const token = getAdminToken();
  const isApi = req.url.startsWith('/api/');
  const needs = isApi && (req.url.startsWith('/api/admin/') || !['GET', 'HEAD', 'OPTIONS'].includes(req.method));
  return next(token && needs ? req.clone({ setHeaders: { 'X-Admin-Token': token } }) : req);
};
