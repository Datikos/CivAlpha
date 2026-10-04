import { httpResource } from '@angular/common/http';
import { Injectable, computed, signal } from '@angular/core';
import { apiUrl, valueOf } from '../../core/api';
import { CompanyDetail } from '../../core/models';

/** Shared state for /companies/:symbol and its child tabs (provided on that route). */
@Injectable()
export class CompanyContext {
  /** Symbol as it appears in the URL (may be a historical ticker, e.g. FB). */
  readonly symbol = signal<string | null>(null);

  readonly company = httpResource<CompanyDetail>(() => {
    const s = this.symbol();
    return s ? apiUrl.company(s) : undefined;
  });

  readonly detail = computed(() => valueOf(this.company) ?? null);

  /**
   * Symbol for the per-company sub-resources: the resolved current ticker once the company
   * has loaded (so historical tickers work everywhere), the URL symbol if that lookup failed,
   * and null while it is still loading.
   */
  readonly apiSymbol = computed(() => {
    const d = this.detail();
    if (d) return d.symbol;
    return this.company.error() ? this.symbol() : null;
  });
}
