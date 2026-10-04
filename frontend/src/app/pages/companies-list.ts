import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { latestByModel } from '../core/forecast-utils';
import { CompanySummary, DividendStatus, ForecastSummary, MODEL_KINDS } from '../core/models';
import { UI } from '../shared/ui';

@Component({
  selector: 'app-companies',
  imports: [RouterLink, FormsModule, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Companies</h1>
        <p class="muted">Nasdaq universe, each compared with its sector benchmark ETF.</p>
      </div>
      <div class="filters">
        <label class="field">
          Dividends
          <select [ngModel]="div()" (ngModelChange)="div.set($event)">
            <option value="">Any</option>
            <option value="REGULAR">Regular payers</option>
            <option value="NOT_REGULAR">Irregular or suspended</option>
            <option value="NONE">No dividend</option>
          </select>
        </label>
        <label class="field">
          Filter
          <input type="search" placeholder="Symbol, name or sector" [ngModel]="q()" (ngModelChange)="q.set($event)" />
        </label>
      </div>
    </div>

    <app-status [res]="res" what="companies" />

    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">
          @if (q() || div()) {
            No companies match the filters.
          } @else {
            No companies yet. Add one on the <a routerLink="/universe">Universe</a> page.
          }
        </div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                <th>Sector</th>
                <th>Benchmark</th>
                <th>CIK</th>
                <th class="num">Last close</th>
                <th>Dividend</th>
                <th>Baseline forecast</th>
                <th>Augmented forecast</th>
              </tr>
            </thead>
            <tbody>
              @for (r of rows(); track r.c.id) {
                <tr>
                  <td class="nowrap">
                    <a [routerLink]="['/companies', r.c.symbol]"><strong>{{ r.c.symbol }}</strong></a>
                  </td>
                  <td>{{ r.c.name }}</td>
                  <td>{{ r.c.sector }}</td>
                  <td>{{ r.c.benchmarkSymbol }}</td>
                  <td class="mono">{{ r.c.cik ?? '—' }}</td>
                  <td class="num">
                    {{ r.c.latestClose | usd }}
                    <div class="small muted">{{ r.c.latestCloseDate ?? '' }}</div>
                  </td>
                  <td class="nowrap">
                    <app-dividend-badge [d]="r.c.dividend" />
                    @if (r.c.dividend?.trailingYield; as y) {
                      <div class="small muted" title="Dividends over the last 12 months / last close">{{ y | pct: 2 }} yield</div>
                    }
                  </td>
                  @for (cell of r.fc; track cell.kind) {
                    <td>
                      @if (cell.f; as f) {
                        <a [routerLink]="['/forecasts', f.id]" class="plain-link">
                          <app-forecast-prob [f]="f" />
                        </a>
                      } @else if (cell.ref; as ref) {
                        <a [routerLink]="['/forecasts', ref.id]">as of {{ ref.asOfDate }} — open</a>
                      } @else {
                        <span class="muted">—</span>
                      }
                    </td>
                  }
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    }
  `,
  styles: `
    .plain-link:hover {
      text-decoration: none;
    }
  `,
})
export class CompaniesPage {
  protected readonly q = signal('');
  protected readonly div = signal<'' | DividendStatus | 'NOT_REGULAR'>('');
  protected readonly res = httpResource<CompanySummary[]>(() => apiUrl.companies());
  /** Full forecast summaries (interval, horizon, publication time) for the latest forecasts. */
  private readonly current = httpResource<ForecastSummary[]>(() => apiUrl.forecastsCurrent());

  protected readonly rows = computed(() => {
    const list = valueOf(this.res) ?? [];
    const cur = valueOf(this.current) ?? [];
    const q = this.q().trim().toLowerCase();
    const div = this.div();
    return list
      .filter(
        (c) =>
          !q ||
          c.symbol.toLowerCase().includes(q) ||
          c.name.toLowerCase().includes(q) ||
          (c.sector ?? '').toLowerCase().includes(q),
      )
      .filter((c) => {
        const s = c.dividend?.status;
        return !div || (div === 'NOT_REGULAR' ? s === 'IRREGULAR' || s === 'SUSPENDED' : s === div);
      })
      .sort((a, b) => a.symbol.localeCompare(b.symbol))
      .map((c) => {
        const latest = latestByModel(cur.filter((f) => f.symbol === c.symbol));
        return {
          c,
          fc: MODEL_KINDS.map((kind) => ({
            kind,
            f: latest[kind] ?? null,
            ref: c.latestForecasts?.[kind] ?? null,
          })),
        };
      });
  });
}
