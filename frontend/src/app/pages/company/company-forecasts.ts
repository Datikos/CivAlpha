import { httpResource } from '@angular/common/http';
import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../../charts/line-chart';
import { dateMs } from '../../charts/chart-utils';
import { apiUrl, valueOf } from '../../core/api';
import { FORMAT_PIPES, fmtPct } from '../../core/format';
import { compareForecastsDesc } from '../../core/forecast-utils';
import { ForecastSummary, MODEL_KINDS, modelLabel } from '../../core/models';
import { ForecastTable } from '../../shared/forecast-table';
import { UI, modelColor } from '../../shared/ui';
import { CompanyContext } from './company-context';

@Component({
  selector: 'app-company-forecasts',
  imports: [RouterLink, LineChart, ForecastTable, ...UI, ...FORMAT_PIPES],
  template: `
    <app-status [res]="res" what="forecast history" />
    @if (res.hasValue()) {
      @if (!list().length) {
        <div class="empty-box">No forecasts issued for this company yet.</div>
      } @else {
        <div class="card">
          <h3>Probability of outperforming over time</h3>
          <p class="small muted">
            Latest version per as-of date; shaded band = uncertainty interval. Horizon
            {{ list()[0].horizonTradingDays }} trading days. Reference line at 50%.
          </p>
          <app-line-chart
            [series]="series()"
            [yMin]="0"
            [yMax]="1"
            [refY]="0.5"
            [height]="260"
            [yFormat]="pct"
            label="Forecast probability over time, baseline vs augmented"
          />
        </div>
        @if (resolved(); as r) {
          <div class="stats">
            @for (s of r; track s.kind) {
              <div class="stat">
                <div class="stat-label">{{ s.kind | human }} resolved</div>
                <div class="stat-value">{{ s.n }}</div>
                <div class="small muted">hit rate {{ s.hit | pct }} · mean Brier {{ s.brier | fixed: 3 }}</div>
              </div>
            }
          </div>
        }
        <h2>All versions</h2>
        <app-forecast-table [forecasts]="list()" [showCompany]="false" />
        <p class="small muted" style="margin-top: 0.5rem">
          <a [routerLink]="['/forecasts/history']" [queryParams]="{ symbol: ctx.apiSymbol() }">Open in global history →</a>
        </p>
      }
    }
  `,
})
export class CompanyForecasts {
  protected readonly ctx = inject(CompanyContext);
  protected readonly pct = (v: number) => fmtPct(v, 0);

  protected readonly res = httpResource<ForecastSummary[]>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.forecastsHistory(s) : undefined;
  });

  protected readonly list = computed(() => [...(valueOf(this.res) ?? [])].sort(compareForecastsDesc));

  protected readonly series = computed<LineSeries[]>(() =>
    MODEL_KINDS.map((kind) => {
      const latestPerDay = new Map<string, ForecastSummary>();
      for (const f of this.list()) {
        if (f.modelKind !== kind) continue;
        if (!latestPerDay.has(f.asOfDate)) latestPerDay.set(f.asOfDate, f); // list is newest-version first
      }
      return {
        key: kind,
        label: modelLabel(kind),
        color: modelColor(kind),
        points: [...latestPerDay.values()].map((f) => ({
          x: dateMs(f.asOfDate),
          y: f.probability,
          lo: f.probLow,
          hi: f.probHigh,
        })),
      };
    }).filter((s) => s.points.length > 0),
  );

  protected readonly resolved = computed(() => {
    const out = MODEL_KINDS.map((kind) => {
      // one forecast per as-of date (latest version), so revisions are not double-counted
      const seen = new Set<string>();
      const r = this.list().filter((f) => {
        if (f.modelKind !== kind || seen.has(f.asOfDate)) return false;
        seen.add(f.asOfDate);
        return !!f.outcome;
      });
      if (!r.length) return null;
      const hits = r.filter((f) => (f.probability >= 0.5) === f.outcome!.outcome).length;
      return {
        kind,
        n: r.length,
        hit: hits / r.length,
        brier: r.reduce((a, f) => a + f.outcome!.brier, 0) / r.length,
      };
    }).filter((x) => x !== null);
    return out.length ? out : null;
  });
}
