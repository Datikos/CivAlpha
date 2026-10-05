import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { CompanySummary, ForecastSummary, MODEL_KINDS, ModelKind } from '../core/models';
import { ForecastTable } from '../shared/forecast-table';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

@Component({
  selector: 'app-forecast-history',
  imports: [FormsModule, RouterLink, ForecastTable, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="history" area="forecast" />
        <div>
          <h1>Forecast history</h1>
          <p class="muted">Every published forecast, all versions, newest first. Superseded versions are kept and marked.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-forecasts" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <div class="filters">
      <label class="field">
        Symbol
        <input
          list="symbols"
          [ngModel]="symbolDraft()"
          (ngModelChange)="symbolDraft.set($event)"
          (change)="applySymbol()"
          (keyup.enter)="applySymbol()"
          placeholder="All companies"
          style="width: 10rem"
        />
        <datalist id="symbols">
          @for (c of companies(); track c.symbol) {
            <option [value]="c.symbol">{{ c.name }}</option>
          }
        </datalist>
      </label>
      <label class="field">
        Model
        <select [ngModel]="model()" (ngModelChange)="setModel($event)">
          <option value="">Both models</option>
          @for (k of kinds; track k) {
            <option [value]="k">{{ k | human }}</option>
          }
        </select>
      </label>
      <label class="field">
        Status
        <select [ngModel]="status()" (ngModelChange)="status.set($event)">
          <option value="">All</option>
          <option value="resolved">Resolved</option>
          <option value="pending">Pending</option>
        </select>
      </label>
      @if (symbol() || model()) {
        <button type="button" class="btn btn-sm" (click)="clear()">Clear filters</button>
      }
      @if (res.hasValue()) {
        <span class="small muted">{{ rows().length }} forecasts</span>
      }
    </div>

    <app-status [res]="res" what="forecast history" />
    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">No forecasts match these filters.</div>
      } @else {
        <div [style.opacity]="res.isLoading() ? 0.5 : 1">
          <app-forecast-table [forecasts]="rows()" />
        </div>
      }
    }
  `,
})
export class ForecastHistoryPage {
  private readonly router = inject(Router);
  /** query params (component input binding) */
  readonly symbolParam = input<string | undefined>(undefined, { alias: 'symbol' });
  readonly modelParam = input<string | undefined>(undefined, { alias: 'modelKind' });

  protected readonly kinds = MODEL_KINDS;
  protected readonly symbol = signal('');
  protected readonly symbolDraft = signal('');
  protected readonly model = signal<ModelKind | ''>('');
  protected readonly status = signal<'' | 'resolved' | 'pending'>('');

  constructor() {
    effect(() => {
      const s = (this.symbolParam() ?? '').toUpperCase();
      const m = this.modelParam() ?? '';
      untracked(() => {
        this.symbol.set(s);
        this.symbolDraft.set(s);
        this.model.set(m === 'BASELINE' || m === 'AUGMENTED' ? m : '');
      });
    });
  }

  private readonly companiesRes = httpResource<CompanySummary[]>(() => apiUrl.companies());
  protected readonly companies = computed(() =>
    [...(valueOf(this.companiesRes) ?? [])].sort((a, b) => a.symbol.localeCompare(b.symbol)),
  );

  protected readonly res = httpResource<ForecastSummary[]>(() =>
    apiUrl.forecastsHistory(this.symbol() || null, this.model() || null),
  );

  protected readonly rows = computed(() => {
    const st = this.status();
    return (valueOf(this.res) ?? []).filter((f) => !st || (st === 'resolved' ? !!f.outcome : !f.outcome));
  });

  protected applySymbol(): void {
    const s = this.symbolDraft().trim().toUpperCase();
    if (s !== this.symbol()) this.navigate(s, this.model());
  }

  protected setModel(m: ModelKind | ''): void {
    this.navigate(this.symbol(), m);
  }

  protected clear(): void {
    this.navigate('', '');
  }

  private navigate(symbol: string, model: string): void {
    this.router.navigate([], {
      queryParams: { symbol: symbol || null, modelKind: model || null },
      replaceUrl: true,
    });
  }
}
