import { httpResource } from '@angular/common/http';
import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable } from 'rxjs';
import { ApiService, apiUrl, errorMessage, valueOf } from '../../core/api';
import { MetaService } from '../../core/meta.service';
import { AdviceResponse, HoldingRequest, HoldingsResponse, PortfolioSummary, TrackRecordResponse } from '../../core/models';
import { allocation, positions, sectors, todo } from './portfolio.model';

/**
 * State of the My portfolio page (one instance per page): the chosen portfolio (`?p=`), its holdings, advice and
 * track record, the write actions, and a 60 s reload of the advice while the market is open (ADR-0006 live prices).
 */
@Injectable()
export class PortfolioStore {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly meta = inject(MetaService);

  /** The chosen portfolio id from `?p=`; null = the caller's first one. Set by the page. */
  readonly selectedId = signal<number | null>(null);

  readonly portfoliosRes = httpResource<PortfolioSummary[]>(() => apiUrl.portfolios());
  readonly holdingsRes = httpResource<HoldingsResponse>(() => apiUrl.portfolioHoldings(this.selectedId()));
  readonly adviceRes = httpResource<AdviceResponse>(() => apiUrl.portfolioAdvice(null, this.selectedId()));
  readonly trackRes = httpResource<TrackRecordResponse>(() => apiUrl.portfolioTrackRecord(this.selectedId()));

  readonly portfolios = computed(() => valueOf(this.portfoliosRes) ?? []);
  readonly holdings = computed(() => valueOf(this.holdingsRes));
  readonly advice = computed(() => valueOf(this.adviceRes));
  readonly track = computed(() => valueOf(this.trackRes));
  readonly shownId = computed(() => this.selectedId() ?? this.holdings()?.portfolio?.id ?? null);
  readonly cash = computed(() => this.holdings()?.cashUsd ?? 0);

  readonly positions = computed(() => positions(this.advice(), this.holdings()?.holdings ?? []));
  readonly todo = computed(() => todo(this.positions()));
  readonly allocation = computed(() => allocation(this.positions(), this.cash()));
  readonly sectors = computed(() => sectors(this.positions()));
  /** Value now: live prices where there is a quote, the advice close otherwise, plus cash. */
  readonly valueNow = computed(() => this.positions().reduce((s, p) => s + p.valueNow, 0) + this.cash());
  readonly costBasis = computed(() => {
    const known = this.positions().filter((p) => p.avgCost > 0);
    return { known: known.length, missing: this.positions().length - known.length,
             cost: known.reduce((s, p) => s + p.costBasis, 0), value: known.reduce((s, p) => s + p.valueNow, 0) };
  });
  readonly overCap = computed(() => this.positions().filter((p) => p.weightNow > 0.2));

  readonly busy = signal(false);
  readonly message = signal<{ text: string; error: boolean } | null>(null);

  constructor() {
    const timer = setInterval(() => {
      if (this.advice()?.live?.marketOpen && !this.busy()) this.adviceRes.reload();
    }, 60_000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  saveHolding(h: HoldingRequest, after?: () => void): void {
    this.run(this.api.setHolding({ ...h, portfolioId: this.selectedId() }),
             `${h.symbol} saved. Press Refresh advice to advise on the portfolio as it is now.`, after);
  }

  removeHolding(symbol: string): void {
    if (!confirm(`Remove ${symbol} from your holdings? Its past advice stays recorded.`)) return;
    this.run(this.api.removeHolding(symbol, this.selectedId()), `${symbol} removed.`);
  }

  saveCash(cash: number, after?: () => void): void {
    this.run(this.api.setCash(cash, this.selectedId()), 'Cash saved. Press Refresh advice to size additions with it.', after);
  }

  refresh(): void {
    this.run(this.api.refreshAdvice(this.selectedId()), 'Advice refreshed for the portfolio as it is now.');
  }

  choose(id: number | string | null): void {
    void this.router.navigate([], { queryParams: { p: id || null }, queryParamsHandling: 'merge' });
  }

  createPortfolio(name: string, after?: () => void): void {
    this.run(this.api.createPortfolio(name), `Portfolio "${name}" created. Enter its holdings on the Holdings tab.`, (p) => {
      after?.();
      this.choose((p as PortfolioSummary).id);
    });
  }

  private run(obs: Observable<unknown>, ok: string, after?: (v: unknown) => void): void {
    this.busy.set(true);
    this.message.set(null);
    obs.subscribe({
      next: (v) => {
        this.busy.set(false);
        this.message.set({ text: ok, error: false });
        after?.(v);
        this.portfoliosRes.reload();
        this.holdingsRes.reload();
        this.adviceRes.reload();
        this.trackRes.reload();
      },
      error: (e) => {
        this.busy.set(false);
        const text = errorMessage(e);
        this.message.set({
          text: text.includes('401') && this.meta.meta()?.authMode !== 'accounts'
            ? 'The admin token is required: enter it on the Data & pipeline page.' : text,
          error: true,
        });
      },
    });
  }
}
