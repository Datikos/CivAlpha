import { httpResource } from '@angular/common/http';
import { Component, computed, inject, input } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../charts/line-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtFixed, fmtNum, fmtPct, fmtSignedPct } from '../core/format';
import { StrategiesResponse, StrategyResult } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';
import { FAMILY_LABEL, REFERENCE_KEY, equityFormat, equitySeries, supported } from './strategies';

const MAX = 3;

interface Row {
  label: string;
  help?: string;
  topic?: string;
  values: (number | null | undefined)[];
  texts: string[];
  kind: 'signed' | 'plain';
  best: number;
}

function bestIndex(values: (number | null | undefined)[], higherIsBetter: boolean | null): number {
  if (higherIsBetter === null) return -1;
  let idx = -1;
  let bv = higherIsBetter ? -Infinity : Infinity;
  values.forEach((v, i) => {
    if (typeof v !== 'number' || !Number.isFinite(v)) return;
    if (higherIsBetter ? v > bv : v < bv) {
      bv = v;
      idx = i;
    }
  });
  return idx;
}

@Component({
  selector: 'app-strategy-compare',
  imports: [RouterLink, LineChart, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="crumbs"><a routerLink="/strategies">Strategy lab</a> / compare</div>
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="columns" area="strategy" />
        <div>
          <h1>Compare strategies</h1>
          <p class="muted">Up to {{ MAX }} strategies from the same backtest side by side. The best value in a row is bold with a ✓.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-compare" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <app-status [res]="res" what="strategy results" />

    <div class="filters">
      <span class="chips">
        @for (s of chosen(); track s.strategyKey) {
          <button type="button" class="chip" (click)="remove(s.strategyKey)" [title]="'Remove'">{{ s.name }} ✕</button>
        }
      </span>
      @if (keys().length < MAX && others().length) {
        <label class="field">
          <span class="sr-only">Add a strategy</span>
          <select (change)="add($any($event.target).value); $any($event.target).value = ''">
            <option value="">Add a strategy…</option>
            @for (s of others(); track s.strategyKey) {
              <option [value]="s.strategyKey">{{ s.name }}</option>
            }
          </select>
        </label>
      }
    </div>

    @if (res.hasValue()) {
      @if (chosen().length < 2) {
        <div class="empty-box">Pick at least two strategies: add them above, or tick them on the <a routerLink="/strategies">Strategy lab</a>.</div>
      } @else {
        <div class="card">
          <h3>Growth of capital</h3>
          <app-line-chart [series]="equity()" label="Cumulative return of the compared strategies and buy & hold" caption="Net of costs, starting in cash on the first out-of-sample day." [yFormat]="equityFmt" [refY]="1" refLabel="start" [height]="300" />
        </div>

        <div class="table-wrap">
          <table class="table compare-grid">
            <thead>
              <tr>
                <th></th>
                @for (s of chosen(); track s.strategyKey) {
                  <th class="col-head">
                    <a [routerLink]="['/strategies', s.strategyKey]">{{ s.name }}</a>
                    <div class="small muted" style="font-weight: 400">{{ familyLabel[s.family] ?? s.family }}</div>
                  </th>
                }
              </tr>
            </thead>
            <tbody>
              <tr>
                <td class="row-label">Verdict</td>
                @for (s of chosen(); track s.strategyKey) {
                  <td>
                    @if (s.family === 'BENCHMARK') { <span class="badge tone-info">Reference</span> }
                    @else if (isSupported(s.verdict)) { <span class="badge tone-good">✓ Beats buy &amp; hold</span> }
                    @else { <span class="badge tone-neutral">Not supported</span> }
                    <div class="small muted" style="margin-top: 0.25rem">{{ s.verdict }}</div>
                  </td>
                }
              </tr>
              @for (r of rows(); track r.label) {
                <tr>
                  <td class="row-label">{{ r.label }}@if (r.help) { <app-help [text]="r.help" [topic]="r.topic ?? ''" [label]="r.label" /> }</td>
                  @for (v of r.values; track $index) {
                    <td class="num" [class.best]="r.best === $index">
                      @if (r.kind === 'signed') {
                        <app-delta [value]="v" kind="pct" [digits]="1" />
                      } @else {
                        {{ r.texts[$index] }}
                      }
                      @if (r.best === $index) { <span class="badge-best" title="Best in this row">✓</span> }
                    </td>
                  }
                </tr>
              }
              <tr>
                <td class="row-label">Enter</td>
                @for (s of chosen(); track s.strategyKey) { <td class="small">{{ s.description.entry }}</td> }
              </tr>
              <tr>
                <td class="row-label">Exit</td>
                @for (s of chosen(); track s.strategyKey) { <td class="small">{{ s.description.exit }}</td> }
              </tr>
            </tbody>
          </table>
        </div>

        <div class="card" style="margin-top: 1rem">
          <h3>Calendar-year returns</h3>
          <div class="table-wrap">
            <table class="table compact">
              <thead>
                <tr><th>Year</th>@for (s of chosen(); track s.strategyKey) { <th class="num">{{ s.name }}</th> }</tr>
              </thead>
              <tbody>
                @for (y of years(); track y) {
                  <tr>
                    <td>{{ y }}</td>
                    @for (s of chosen(); track s.strategyKey) {
                      <td class="num heat" [class]="'num heat tone-' + yearTone(s, y)" [style.--h]="yearHeat(s, y)">
                        {{ yearValue(s, y) | signedPct: 1 }}
                      </td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted" style="margin-top: 0.4rem">The first and last years are partial.</p>
        </div>
      }
    }
  `,
  styles: `
    .chip { cursor: pointer; font: inherit; font-size: 0.8rem; }
    .compare-grid td.num { text-align: right; }
  `,
})
export class StrategyComparePage {
  readonly keys_ = input<string>('', { alias: 'keys' });
  protected readonly MAX = MAX;
  protected readonly familyLabel = FAMILY_LABEL;
  protected readonly isSupported = supported;
  protected readonly equityFmt = equityFormat;
  private readonly router = inject(Router);

  protected readonly res = httpResource<StrategiesResponse>(() => apiUrl.strategies());
  protected readonly results = computed(() => valueOf(this.res)?.results ?? []);
  protected readonly keys = computed(() => {
    const out: string[] = [];
    for (const k of (this.keys_() ?? '').split(',')) {
      const t = k.trim();
      if (t && !out.includes(t) && out.length < MAX) out.push(t);
    }
    return out;
  });
  protected readonly chosen = computed(() => this.keys().map((k) => this.results().find((s) => s.strategyKey === k)).filter((s): s is StrategyResult => !!s));
  protected readonly others = computed(() => this.results().filter((s) => !this.keys().includes(s.strategyKey)));

  protected readonly equity = computed<LineSeries[]>(() => {
    const colors = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'];
    const out = this.chosen().map((s, i) => equitySeries(s.strategyKey, s.name, colors[i], s));
    const ref = this.results().find((s) => s.strategyKey === REFERENCE_KEY);
    if (ref && !this.keys().includes(REFERENCE_KEY) && out.length < 3) out.push(equitySeries(ref.strategyKey, ref.name, 'var(--axis)', ref));
    return out;
  });

  protected readonly rows = computed<Row[]>(() => {
    const list = this.chosen();
    const m = (f: (r: StrategyResult) => number | null | undefined) => list.map(f);
    const row = (label: string, values: (number | null | undefined)[], fmt: (v: number) => string, higherIsBetter: boolean | null, kind: Row['kind'] = 'plain', help?: string, topic?: string): Row => ({
      label,
      values,
      texts: values.map((v) => (typeof v === 'number' && Number.isFinite(v) ? fmt(v) : '—')),
      kind,
      best: bestIndex(values, higherIsBetter),
      help,
      topic,
    });
    return [
      row('CAGR', m((r) => r.metrics.cagr), (v) => fmtSignedPct(v, 1), true, 'signed', 'Compound annual growth after costs.', 'cagr'),
      row('Total return', m((r) => r.metrics.totalReturn), (v) => fmtSignedPct(v, 1), true, 'signed'),
      row('Sharpe', m((r) => r.metrics.sharpe), (v) => fmtFixed(v, 2), true, 'plain', 'Return per unit of risk, annualized, over cash.', 'sharpe'),
      row('Sortino', m((r) => r.metrics.sortino), (v) => fmtFixed(v, 2), true),
      row('Max drawdown', m((r) => r.metrics.maxDrawdown), (v) => fmtPct(v, 1), true, 'plain', 'Largest peak-to-trough fall; closer to zero is better.', 'max-drawdown'),
      row('Volatility', m((r) => r.metrics.volatility), (v) => fmtPct(v, 1), false),
      row('Exposure', m((r) => r.metrics.exposure), (v) => fmtPct(v, 0), null, 'plain', 'Average share of capital invested.', 'exposure'),
      row('Beta vs buy & hold', m((r) => r.metrics.beta), (v) => fmtFixed(v, 2), null),
      row('Trades', m((r) => r.metrics.trades), (v) => fmtNum(v), null),
      row('Win rate', m((r) => r.metrics.winRate), (v) => fmtPct(v, 0), true),
      row('Avg. holding days', m((r) => r.metrics.avgHoldingDays), (v) => fmtNum(v, 0), null),
      row('Cost drag a year', m((r) => r.metrics.costDragPerYear), (v) => fmtPct(v, 2), false, 'plain', 'Trading costs as a share of capital per year.', 'costs'),
      row('Excess vs buy & hold', m((r) => r.metrics.excessReturn), (v) => fmtSignedPct(v, 1), true, 'signed', 'Annualized return above equal-weight buy and hold.', 'excess'),
      row('Excess CI low', m((r) => r.metrics.excessCiLow), (v) => fmtSignedPct(v, 1), true, 'signed', 'Lower end of the 95% interval; above zero is what counts.', 'confidence-interval'),
      row('Excess CI high', m((r) => r.metrics.excessCiHigh), (v) => fmtSignedPct(v, 1), null, 'signed'),
      row('Deflated Sharpe Ratio', m((r) => r.metrics.deflatedSharpe), (v) => fmtFixed(v, 2), true, 'plain', 'Probability the edge is real after trying many strategies; 0.95 needed.', 'dsr'),
    ];
  });

  protected readonly years = computed(() => [...new Set(this.chosen().flatMap((s) => s.yearly.map((y) => y.year)))].sort());
  private readonly maxYear = computed(() => Math.max(...this.chosen().flatMap((s) => s.yearly.map((y) => Math.abs(y.return))), 0.01));
  protected yearValue(s: StrategyResult, y: number): number | null {
    return s.yearly.find((r) => r.year === y)?.return ?? null;
  }
  protected yearTone(s: StrategyResult, y: number): string {
    const v = this.yearValue(s, y);
    return v === null || v === 0 ? 'neutral' : v > 0 ? 'good' : 'bad';
  }
  protected yearHeat(s: StrategyResult, y: number): number {
    const v = this.yearValue(s, y);
    return v === null ? 0 : Math.min(1, Math.abs(v) / this.maxYear());
  }

  protected add(key: string): void {
    if (!key) return;
    this.navigate([...this.keys(), key].slice(0, MAX));
  }
  protected remove(key: string): void {
    this.navigate(this.keys().filter((k) => k !== key));
  }
  private navigate(keys: string[]): void {
    this.router.navigate([], { queryParams: { keys: keys.join(',') || null }, queryParamsHandling: 'merge' });
  }
}
