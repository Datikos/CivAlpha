import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FORMAT_PIPES } from '../core/format';
import { compareForecastsDesc } from '../core/forecast-utils';
import { ForecastSummary } from '../core/models';
import { UI } from './ui';
import { VIZ } from './viz';

/** Forecast list (all versions) with supersession and resolved outcomes. */
@Component({
  selector: 'app-forecast-table',
  imports: [RouterLink, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            @if (showCompany()) {
              <th>Company</th>
            }
            <th>Model</th>
            <th>P(outperform) [interval] <app-help text="The probability that the stock beats its sector ETF over the horizon, with its uncertainty interval against the 50% midline." topic="interval" label="probability" /></th>
            <th>Lean <app-help text="Above or below only when the whole interval is on one side of 50%; otherwise a coin flip." topic="lean" label="lean" /></th>
            <th>Horizon</th>
            <th>As of</th>
            <th>Published</th>
            <th>Mode</th>
            <th>Version</th>
            <th>Outcome <app-help text="Filled in once the window has closed: did the stock beat its ETF, by how much (excess return), and how good the probability was (Brier score, lower is better, 0.25 is a coin flip)." topic="brier" label="outcome" /></th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (r of rows(); track r.f.id) {
            <tr [class.row-dim]="r.superseded">
              @if (showCompany()) {
                <td class="nowrap">
                  <a [routerLink]="['/companies', r.f.symbol]"><strong>{{ r.f.symbol }}</strong></a>
                  <div class="small muted">vs {{ r.f.benchmarkSymbol }}</div>
                </td>
              }
              <td><app-model-tag [kind]="r.f.modelKind" /></td>
              <td><app-forecast-prob [f]="r.f" [withContext]="false" /></td>
              <td><app-lean [p]="r.f.probability" [lo]="r.f.probLow" [hi]="r.f.probHigh" /></td>
              <td class="nowrap">{{ r.f.horizonTradingDays }} d</td>
              <td class="nowrap">{{ r.f.asOfDate }}</td>
              <td class="nowrap">{{ r.f.issuedAt | utc }}</td>
              <td><app-issue-mode [mode]="r.f.issueMode" /></td>
              <td class="nowrap">
                v{{ r.f.version }}
                @if (r.superseded) {
                  <span class="badge badge-superseded" title="A newer version exists for this as-of date">superseded</span>
                }
                @if (r.f.reason) {
                  <div class="small muted">{{ r.f.reason }}</div>
                }
              </td>
              <td class="nowrap">
                @if (r.f.outcome; as o) {
                  <span class="badge" [class.tone-good]="o.outcome" [class.tone-bad]="!o.outcome">
                    {{ o.outcome ? '✓ Outperformed' : '✗ Underperformed' }}
                  </span>
                  <div class="small">
                    excess <app-delta [value]="o.excessReturn" kind="pct" /> · Brier
                    <span [title]="o.brier < 0.25 ? 'Better than a coin flip' : 'No better than a coin flip'">{{ o.brier | fixed: 3 }}</span>
                  </div>
                  <div class="small muted">window end {{ o.windowEndDate }}</div>
                } @else {
                  <span class="muted small">pending</span>
                }
              </td>
              <td><a [routerLink]="['/forecasts', r.f.id]">Details</a></td>
            </tr>
          }
        </tbody>
      </table>
    </div>
  `,
})
export class ForecastTable {
  readonly forecasts = input.required<ForecastSummary[]>();
  readonly showCompany = input(true);

  protected readonly rows = computed(() => {
    const list = [...this.forecasts()].sort(compareForecastsDesc);
    const maxVer = new Map<string, number>();
    const key = (f: ForecastSummary) => `${f.symbol}|${f.modelKind}|${f.asOfDate}`;
    for (const f of list) maxVer.set(key(f), Math.max(maxVer.get(key(f)) ?? 0, f.version));
    return list.map((f) => ({ f, superseded: f.version < (maxVer.get(key(f)) ?? f.version) }));
  });
}
