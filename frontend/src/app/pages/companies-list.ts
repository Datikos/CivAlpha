import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { latestByModel } from '../core/forecast-utils';
import { CompanySummary, ForecastSummary, MODEL_KINDS } from '../core/models';
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
      <label class="field">
        Filter
        <input type="search" placeholder="Symbol, name or sector" [ngModel]="q()" (ngModelChange)="q.set($event)" />
      </label>
    </div>

    <app-status [res]="res" what="companies" />

    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">
          @if (q()) {
            No companies match “{{ q() }}”.
          } @else {
            No companies yet. Load the demo dataset on the <a routerLink="/admin">Data &amp; pipeline</a> page.
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
                <th>Baseline forecast</th>
                <th>Augmented forecast</th>
              </tr>
            </thead>
            <tbody>
              @for (r of rows(); track r.c.id) {
                <tr>
                  <td class="nowrap">
                    <a [routerLink]="['/companies', r.c.symbol]"><strong>{{ r.c.symbol }}</strong></a>
                    <app-demo-badge [show]="r.c.isDemo" />
                  </td>
                  <td>{{ r.c.name }}</td>
                  <td>{{ r.c.sector }}</td>
                  <td>{{ r.c.benchmarkSymbol }}</td>
                  <td class="mono">{{ r.c.cik ?? '—' }}</td>
                  <td class="num">
                    {{ r.c.latestClose | usd }}
                    <div class="small muted">{{ r.c.latestCloseDate ?? '' }}</div>
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
  protected readonly res = httpResource<CompanySummary[]>(() => apiUrl.companies());
  /** Full forecast summaries (interval, horizon, publication time) for the latest forecasts. */
  private readonly current = httpResource<ForecastSummary[]>(() => apiUrl.forecastsCurrent());

  protected readonly rows = computed(() => {
    const list = valueOf(this.res) ?? [];
    const cur = valueOf(this.current) ?? [];
    const q = this.q().trim().toLowerCase();
    return list
      .filter(
        (c) =>
          !q ||
          c.symbol.toLowerCase().includes(q) ||
          c.name.toLowerCase().includes(q) ||
          (c.sector ?? '').toLowerCase().includes(q),
      )
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
