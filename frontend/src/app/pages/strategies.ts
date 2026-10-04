import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../charts/line-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct, fmtSignedPct } from '../core/format';
import { StrategiesResponse, StrategyResult } from '../core/models';
import { UI } from '../shared/ui';

export const AI_KEY = 'AI_GBM';
export const REFERENCE_KEY = 'EW_BUY_HOLD';

export const FAMILY_LABEL: Record<string, string> = {
  BENCHMARK: 'Benchmark',
  TREND: 'Trend / momentum',
  MEAN_REVERSION: 'Mean reversion',
  FUNDAMENTAL: 'Fundamental',
  EVENT: 'Event',
  AI: 'AI',
};

export function equitySeries(key: string, label: string, color: string, r: { equity: { date: string; equity: number }[] }): LineSeries {
  return { key, label, color, points: r.equity.map((p) => ({ x: Date.parse(p.date + 'T00:00:00Z'), y: p.equity })) };
}

export function supported(verdict: string): boolean {
  return verdict.includes('SUPPORTED by');
}

/** Growth of 1 shown as cumulative return. */
export const equityFormat = (v: number) => fmtSignedPct(v - 1, 0);

interface Col {
  label: string;
  hint: string;
  get: (r: StrategyResult) => number | null | undefined;
  fmt: (v: number) => string;
  higherIsBetter: boolean | null;
}

@Component({
  selector: 'app-strategies',
  imports: [RouterLink, LineChart, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Strategy lab</h1>
        <p class="muted">
          Classic entry/exit theories and the AI decision-maker, backtested on the same data, the same out-of-sample
          window and the same trading costs. Long-only; decided at the close, traded at the next close.
        </p>
      </div>
      <a class="btn" routerLink="/decisions">Today's AI decisions →</a>
    </div>

    <app-status [res]="res" what="strategy results" />

    @if (res.hasValue()) {
      @if (run(); as r) {
        <div class="verdict" role="note"><strong>Verdict.</strong> {{ r.summary }}</div>
        <p class="small muted">
          Backtest #{{ r.id }} run {{ r.runAt | utc }} · scored {{ r.oosStart }} to {{ r.dataCutoff }}
          · costs {{ r.config.costBpsPerSide }} bp per side on traded amount · {{ r.config.execution }}
        </p>

        <div class="card">
          <h3>Comparison (out of sample, after costs)</h3>
          <div class="table-wrap">
            <table class="table compact">
              <thead>
                <tr>
                  <th>Strategy</th>
                  @for (c of cols; track c.label) {
                    <th class="num" [title]="c.hint">{{ c.label }}</th>
                  }
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                @for (s of results(); track s.strategyKey) {
                  <tr [class.ref-row]="s.strategyKey === refKey">
                    <td>
                      <a [routerLink]="['/strategies', s.strategyKey]">{{ s.name }}</a>
                      <div class="small muted">{{ familyLabel[s.family] ?? s.family }}</div>
                    </td>
                    @for (c of cols; track c.label) {
                      <td class="num" [style.font-weight]="best()[c.label] === s.strategyKey ? 700 : 400">
                        {{ cell(c, s) }}{{ best()[c.label] === s.strategyKey ? ' ✓' : '' }}
                      </td>
                    }
                    <td>
                      @if (s.family === 'BENCHMARK') {
                        <span class="badge">Reference</span>
                      } @else if (isSupported(s.verdict)) {
                        <span class="badge badge-ok">Beats buy &amp; hold</span>
                      } @else {
                        <span class="badge" title="{{ s.verdict }}">Not supported</span>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted" style="margin-top: 0.5rem">
            ✓ marks the best value in a column. "Excess" is the annualized return above equal-weight buy &amp; hold with a
            95% block-bootstrap interval. "DSR" is the Deflated Sharpe Ratio: the probability that the edge over buy &amp;
            hold is real after accounting for testing {{ r.config.nCandidates }} strategies. {{ r.config.verdictRule }}.
          </p>
        </div>

        <div class="card">
          <div class="chart-head">
            <h3>Growth of capital</h3>
            <label class="small">
              Compare with
              <select (change)="chosen.set($any($event.target).value)">
                @for (s of comparable(); track s.strategyKey) {
                  <option [value]="s.strategyKey" [selected]="s.strategyKey === compareKey()">{{ s.name }}</option>
                }
              </select>
            </label>
          </div>
          <app-line-chart
            [series]="equity()"
            label="Cumulative return of buy &amp; hold, the AI and a chosen strategy"
            caption="Cumulative net return after costs; every line starts on the same day, in cash."
            [yFormat]="equityFmt"
            [refY]="1"
            refLabel="start"
            [height]="300"
          />
        </div>

        <div class="grid-2">
          <div class="card">
            <h3>Cost sensitivity (CAGR)</h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    @for (b of r.config.costSensitivityBps; track b) {
                      <th class="num">{{ b }} bp</th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (s of results(); track s.strategyKey) {
                    <tr>
                      <td>{{ s.name }}</td>
                      @for (b of r.config.costSensitivityBps; track b) {
                        <td class="num">{{ s.costSensitivity[b + '']?.cagr ?? null | signedPct: 1 }}</td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem">High-turnover rules lose the most as costs rise.</p>
          </div>

          <div class="card">
            <h3>How to read this</h3>
            <ul class="small notes">
              <li>All strategies trade the same {{ results().length }} portfolios on identical data; the AI was trained
                walk-forward and never saw the period it is scored on.</li>
              <li>Timing rules hold each stock in its own 1/N slice and sit in cash otherwise, so their "Exposure" is below
                100%. Lower exposure usually means lower return <em>and</em> lower drawdown.</li>
              <li>With about {{ years() | fixed: 1 }} years and a small universe, a few lucky trades can dominate. Treat
                differences without a "Beats buy &amp; hold" badge as noise.</li>
              <li>This is research, not advice: a backtest is not evidence of live profitability.</li>
            </ul>
          </div>
        </div>
      } @else {
        <div class="empty-box">
          No strategy backtest yet. Run it from <a routerLink="/admin">Data &amp; pipeline</a> → Run strategy backtest
          (it also runs at the end of every pipeline run).
        </div>
      }
    }
  `,
  styles: `
    .chart-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
    .chart-head select { margin-left: 0.4rem; }
    .ref-row td { background: var(--surface-2); }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
  `,
})
export class StrategiesPage {
  protected readonly res = httpResource<StrategiesResponse>(() => apiUrl.strategies());
  protected readonly familyLabel = FAMILY_LABEL;
  protected readonly refKey = REFERENCE_KEY;
  protected readonly equityFmt = equityFormat;
  protected readonly isSupported = supported;

  protected readonly run = computed(() => valueOf(this.res)?.run ?? null);
  protected readonly results = computed(() => valueOf(this.res)?.results ?? []);
  protected readonly years = computed(() => this.results()[0]?.metrics.years ?? 0);

  protected readonly cols: Col[] = [
    { label: 'CAGR', hint: 'compound annual growth, after costs', get: (r) => r.metrics.cagr, fmt: (v) => fmtSignedPct(v, 1), higherIsBetter: true },
    { label: 'Sharpe', hint: 'annualized, over cash', get: (r) => r.metrics.sharpe, fmt: (v) => v.toFixed(2), higherIsBetter: true },
    { label: 'Max DD', hint: 'largest peak-to-trough fall', get: (r) => r.metrics.maxDrawdown, fmt: (v) => fmtPct(v, 1), higherIsBetter: true },
    { label: 'Exposure', hint: 'average share of capital invested', get: (r) => r.metrics.exposure, fmt: (v) => fmtPct(v, 0), higherIsBetter: null },
    { label: 'Trades', hint: 'round trips in the window', get: (r) => r.metrics.trades, fmt: (v) => v.toLocaleString('en-US'), higherIsBetter: null },
    { label: 'Excess', hint: 'annualized vs equal-weight buy & hold, 95% CI', get: (r) => r.metrics.excessReturn, fmt: (v) => fmtSignedPct(v, 1), higherIsBetter: true },
    { label: 'DSR', hint: 'Deflated Sharpe Ratio of the excess return (≥ 0.95 needed)', get: (r) => r.metrics.deflatedSharpe, fmt: (v) => v.toFixed(2), higherIsBetter: true },
  ];

  protected cell(c: Col, s: StrategyResult): string {
    const v = c.get(s);
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    if (c.label === 'Excess' && s.metrics.excessCiLow != null && s.metrics.excessCiHigh != null) {
      return `${c.fmt(v)} (${fmtSignedPct(s.metrics.excessCiLow, 1)} to ${fmtSignedPct(s.metrics.excessCiHigh, 1)})`;
    }
    return c.fmt(v);
  }

  /** strategy key holding the best value per column (benchmarks excluded where the column compares to them) */
  protected readonly best = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const c of this.cols) {
      if (c.higherIsBetter === null) continue;
      let bestKey = '';
      let bestV = -Infinity;
      for (const s of this.results()) {
        const v = c.get(s);
        if (v === null || v === undefined || !Number.isFinite(v)) continue;
        const score = c.higherIsBetter ? v : -v;
        if (score > bestV) {
          bestV = score;
          bestKey = s.strategyKey;
        }
      }
      if (bestKey) out[c.label] = bestKey;
    }
    return out;
  });

  /** Third line of the chart: any strategy other than the two fixed ones (colors follow roles, not rank). */
  protected readonly comparable = computed(() => this.results().filter((s) => s.strategyKey !== AI_KEY && s.strategyKey !== REFERENCE_KEY));
  protected readonly chosen = signal<string | null>(null);
  /** defaults to the best-ranked active rule (results arrive sorted by Sharpe) */
  protected readonly compareKey = computed(() => {
    const c = this.chosen();
    const list = this.comparable();
    if (c && list.some((s) => s.strategyKey === c)) return c;
    const active = list.filter((s) => s.family !== 'BENCHMARK');
    return (active[0] ?? list[0])?.strategyKey ?? '';
  });

  protected readonly equity = computed<LineSeries[]>(() => {
    const byKey = new Map(this.results().map((s) => [s.strategyKey, s]));
    const out: LineSeries[] = [];
    const ref = byKey.get(REFERENCE_KEY);
    if (ref) out.push(equitySeries(ref.strategyKey, ref.name, 'var(--series-1)', ref));
    const ai = byKey.get(AI_KEY);
    if (ai) out.push(equitySeries(ai.strategyKey, ai.name, 'var(--series-2)', ai));
    const other = byKey.get(this.compareKey());
    if (other) out.push(equitySeries(other.strategyKey, other.name, 'var(--series-3)', other));
    return out;
  });
}
