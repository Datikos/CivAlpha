import { httpResource } from '@angular/common/http';
import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Column, ColumnChart } from '../charts/column-chart';
import { LineChart, LineSeries } from '../charts/line-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct, fmtSignedPct } from '../core/format';
import { StrategyDetailResponse } from '../core/models';
import { UI } from '../shared/ui';
import { FAMILY_LABEL, REFERENCE_KEY, equityFormat, equitySeries, supported } from './strategies';

@Component({
  selector: 'app-strategy-detail',
  imports: [RouterLink, LineChart, ColumnChart, ...UI, ...FORMAT_PIPES],
  template: `
    <p class="small"><a routerLink="/strategies">← Strategy lab</a></p>
    <app-status [res]="res" what="strategy" />

    @if (data(); as d) {
      @let s = d.result;
      @let m = s.metrics;
      <div class="page-head">
        <div>
          <h1>{{ s.name }}</h1>
          <p class="muted">{{ familyLabel[s.family] ?? s.family }} · {{ s.description.origin }}</p>
        </div>
      </div>

      <div class="verdict" role="note">
        <strong>{{ s.family === 'BENCHMARK' ? 'Reference.' : isSupported(s.verdict) ? 'Supported.' : 'Not supported.' }}</strong>
        {{ s.verdict }}
      </div>

      <div class="grid-2">
        <div class="card">
          <h3>The rule</h3>
          <dl class="kv">
            <dt>Enter</dt>
            <dd>{{ s.description.entry }}</dd>
            <dt>Exit</dt>
            <dd>{{ s.description.exit }}</dd>
            <dt>Sizing</dt>
            <dd>{{ sizing(s.description.sizing) }}</dd>
            @if (paramList().length) {
              <dt>Parameters</dt>
              <dd class="small mono">{{ paramList().join(' · ') }}</dd>
            }
          </dl>
        </div>
        <div class="card">
          <h3>Results {{ m.start }} to {{ m.end }} ({{ m.years | fixed: 1 }} years)</h3>
          <dl class="kv">
            <dt>Total return</dt>
            <dd>{{ m.totalReturn | signedPct: 1 }} <span class="muted">(CAGR {{ m.cagr | signedPct: 1 }})</span></dd>
            <dt>Risk</dt>
            <dd>volatility {{ m.volatility | pct: 1 }} · max drawdown {{ m.maxDrawdown | pct: 1 }}</dd>
            <dt>Sharpe / Sortino</dt>
            <dd>{{ m.sharpe | fixed: 2 }} / {{ m.sortino | fixed: 2 }}</dd>
            <dt>Exposure</dt>
            <dd>{{ m.exposure | pct: 0 }} invested on average · beta {{ m.beta | fixed: 2 }} vs buy &amp; hold</dd>
            <dt>Trading</dt>
            <dd>
              {{ m.trades | num }} trades · win rate {{ m.winRate | pct: 0 }} · held {{ m.avgHoldingDays | fixed: 0 }} days on
              average · costs {{ m.costDragPerYear | pct: 2 }} a year
            </dd>
            @if (m.excessReturn !== undefined && m.excessReturn !== null) {
              <dt>vs buy &amp; hold</dt>
              <dd>
                {{ m.excessReturn | signedPct: 1 }} a year
                <span class="muted">(95% CI {{ m.excessCiLow | signedPct: 1 }} to {{ m.excessCiHigh | signedPct: 1 }})</span>
                @if (m.deflatedSharpe !== null && m.deflatedSharpe !== undefined) {
                  · DSR {{ m.deflatedSharpe | fixed: 2 }}
                }
              </dd>
            }
          </dl>
        </div>
      </div>

      <div class="card">
        <h3>Growth of capital</h3>
        <app-line-chart
          [series]="equity()"
          [label]="'Cumulative return of ' + s.name + ' and equal-weight buy & hold'"
          caption="Net of costs, starting in cash on the first out-of-sample day."
          [yFormat]="equityFmt"
          [refY]="1"
          refLabel="start"
        />
      </div>

      <div class="grid-2">
        <div class="card">
          <h3>Drawdown</h3>
          <app-line-chart [series]="drawdown()" label="Drawdown from the running peak" [yFormat]="pctFmt" [yMax]="0" [height]="220" />
        </div>
        <div class="card">
          <h3>Calendar-year returns</h3>
          <app-column-chart [columns]="yearly()" label="Return by calendar year" [yFormat]="pctFmt" />
          <p class="small muted">The first and last years are partial.</p>
        </div>
      </div>

      <div class="card">
        <h3>Trades ({{ d.tradeCount | num }}{{ d.tradeCount > d.trades.length ? ', latest ' + d.trades.length + ' shown' : '' }})</h3>
        @if (d.trades.length) {
          <div class="table-wrap">
            <table class="table compact">
              <thead>
                <tr><th>Symbol</th><th>Entry</th><th>Exit</th><th class="num">Return</th><th class="num">Days</th><th>Entry reason</th><th>Exit reason</th></tr>
              </thead>
              <tbody>
                @for (t of d.trades; track $index) {
                  <tr>
                    <td>
                      @if (t.companyId) {
                        <a [routerLink]="['/companies', t.symbol]">{{ t.symbol }}</a>
                      } @else {
                        {{ t.symbol }}
                      }
                    </td>
                    <td>{{ t.entryDate }}</td>
                    <td>{{ t.exitDate ?? 'open' }}</td>
                    <td class="num" [class.pos]="(t.tradeReturn ?? 0) > 0" [class.neg]="(t.tradeReturn ?? 0) < 0">{{ t.tradeReturn | signedPct: 1 }}</td>
                    <td class="num">{{ t.holdingDays }}</td>
                    <td class="small">{{ t.entryReason }}</td>
                    <td class="small">{{ t.exitReason }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted">Dates are execution dates (the close after the decision). Returns are gross, before costs.</p>
        } @else {
          <p class="muted">No trades in the window.</p>
        }
      </div>
    }
  `,
  styles: `
    .mono { font-family: var(--mono); }
    td.pos { color: var(--good-ink); }
    td.neg { color: var(--bad-ink); }
  `,
})
export class StrategyDetailPage {
  readonly key = input.required<string>();
  protected readonly res = httpResource<StrategyDetailResponse>(() => apiUrl.strategy(this.key()));
  protected readonly data = computed(() => valueOf(this.res) ?? null);
  protected readonly familyLabel = FAMILY_LABEL;
  protected readonly equityFmt = equityFormat;
  protected readonly isSupported = supported;
  protected readonly pctFmt = (v: number) => fmtPct(v, 0);

  protected sizing(s: string): string {
    return s === 'SLEEVE'
      ? 'Each stock owns 1/N of capital; cash while its signal is off'
      : s === 'EQUAL'
        ? 'Equal weight across selected stocks, fully invested'
        : 'Weights set by the strategy';
  }

  protected readonly paramList = computed(() =>
    Object.entries(this.data()?.result.params ?? {})
      .filter(([, v]) => typeof v !== 'object')
      .map(([k, v]) => `${k}=${v}`),
  );

  protected readonly equity = computed<LineSeries[]>(() => {
    const d = this.data();
    if (!d) return [];
    const out: LineSeries[] = [];
    if (d.reference && d.result.strategyKey !== REFERENCE_KEY) {
      out.push(equitySeries(d.reference.strategyKey, d.reference.name, 'var(--series-1)', d.reference));
    }
    out.push(equitySeries(d.result.strategyKey, d.result.name, d.result.strategyKey === REFERENCE_KEY ? 'var(--series-1)' : 'var(--series-2)', d.result));
    return out;
  });

  protected readonly drawdown = computed<LineSeries[]>(() => {
    const r = this.data()?.result;
    if (!r) return [];
    return [{ key: 'dd', label: 'Drawdown', color: 'var(--series-2)', points: r.equity.map((p) => ({ x: Date.parse(p.date + 'T00:00:00Z'), y: p.drawdown })) }];
  });

  protected readonly yearly = computed<Column[]>(() =>
    (this.data()?.result.yearly ?? []).map((y) => ({ key: String(y.year), label: String(y.year), value: y.return, detail: fmtSignedPct(y.return, 1) })),
  );
}
