import { httpResource } from '@angular/common/http';
import { DestroyRef, Injectable, computed, inject } from '@angular/core';
import { apiUrl, valueOf } from './api';
import { LiveQuoteRow, LiveQuotesResponse } from './models';

/**
 * ADR-0006: the latest live quote of every universe member and benchmark ETF, shared by the research pages. Reloads
 * every 60 s while the US market is open. Display only: forecasts, the book and the advice use daily closes.
 */
@Injectable({ providedIn: 'root' })
export class LiveQuotesService {
  readonly res = httpResource<LiveQuotesResponse>(() => apiUrl.quotes());
  readonly data = computed(() => valueOf(this.res));
  readonly bySymbol = computed(() => {
    const m = new Map<string, LiveQuoteRow>();
    for (const q of this.data()?.quotes ?? []) m.set(q.symbol.toUpperCase(), q);
    return m;
  });
  readonly marketOpen = computed(() => this.data()?.marketOpen ?? false);

  private readonly timer = setInterval(() => {
    if (this.marketOpen()) this.res.reload();
  }, 60_000);

  constructor() {
    inject(DestroyRef).onDestroy(() => clearInterval(this.timer));
  }

  quote(symbol: string | null | undefined): LiveQuoteRow | undefined {
    return symbol ? this.bySymbol().get(symbol.toUpperCase()) : undefined;
  }
}

/** "13:05" in New York time, for a quote's timestamp. */
export function nyTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' });
}
