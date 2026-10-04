import { httpResource } from '@angular/common/http';
import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { groupByCompany } from '../core/forecast-utils';
import { MetaService } from '../core/meta.service';
import { ForecastSummary } from '../core/models';
import { UI } from '../shared/ui';

@Component({
  selector: 'app-current-forecasts',
  imports: [RouterLink, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Current forecasts</h1>
        <p class="muted">Latest published forecast per company for the most recent as-of date, both models.</p>
      </div>
      <a routerLink="/forecasts/history" class="btn">Full history</a>
    </div>

    <div class="card">
      <h3>Forecast target</h3>
      <div class="target-def">{{ meta.target() }}</div>
      <p class="muted small" style="margin: 0.5rem 0 0">
        BASELINE uses prices and filing fundamentals only; AUGMENTED adds political / policy event features
        (monetary policy, trade & tariff) weighted by each company's documented exposure.
      </p>
    </div>

    @if (summary(); as s) {
      <div class="stats">
        <div class="stat"><div class="stat-label">Companies</div><div class="stat-value">{{ s.companies }}</div></div>
        <div class="stat"><div class="stat-label">As-of date</div><div class="stat-value">{{ s.asOf }}</div></div>
        <div class="stat"><div class="stat-label">Horizon</div><div class="stat-value">{{ s.horizon }} trading days</div></div>
        <div class="stat"><div class="stat-label">Replayed forecasts</div><div class="stat-value">{{ s.replayed }}</div></div>
      </div>
    }

    <app-status [res]="res" what="current forecasts" />

    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">
          No forecasts have been issued yet. Run the pipeline on the
          <a routerLink="/admin">Data &amp; pipeline</a> page.
        </div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Company</th>
                <th>Model</th>
                <th>P(outperform) [interval]</th>
                <th>Horizon</th>
                <th>As of (data cutoff)</th>
                <th>Published</th>
                <th>Mode</th>
                <th class="num">Ver.</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (row of rows(); track row.symbol) {
                @for (cell of row.forecasts; track cell.kind; let first = $first) {
                  <tr>
                    @if (first) {
                      <td class="group" [attr.rowspan]="row.forecasts.length">
                        <a [routerLink]="['/companies', row.symbol]"><strong>{{ row.symbol }}</strong></a>
                        <div class="small muted">{{ row.companyName }}</div>
                        <div class="small muted">vs {{ row.benchmarkSymbol }}</div>
                      </td>
                    }
                    <td><app-model-tag [kind]="cell.kind" /></td>
                    @if (cell.f; as f) {
                      <td><app-forecast-prob [f]="f" [withContext]="false" /></td>
                      <td class="nowrap">{{ f.horizonTradingDays }} d</td>
                      <td class="nowrap">{{ f.asOfDate }}</td>
                      <td class="nowrap">{{ f.issuedAt | utc }}</td>
                      <td><app-issue-mode [mode]="f.issueMode" /></td>
                      <td class="num">v{{ f.version }}</td>
                      <td><a [routerLink]="['/forecasts', f.id]">Details</a></td>
                    } @else {
                      <td colspan="7" class="muted">No forecast</td>
                    }
                  </tr>
                }
              }
            </tbody>
          </table>
        </div>
      }
    }
  `,
})
export class CurrentForecastsPage {
  protected readonly meta = inject(MetaService);
  protected readonly res = httpResource<ForecastSummary[]>(() => apiUrl.forecastsCurrent());
  protected readonly rows = computed(() => groupByCompany(valueOf(this.res) ?? []));
  protected readonly summary = computed(() => {
    const list = valueOf(this.res) ?? [];
    if (!list.length) return null;
    const asOf = list.map((f) => f.asOfDate).sort().at(-1) ?? '—';
    return {
      companies: new Set(list.map((f) => f.symbol)).size,
      asOf,
      horizon: list[0].horizonTradingDays,
      replayed: list.filter((f) => f.issueMode === 'REPLAY').length,
    };
  });
}
