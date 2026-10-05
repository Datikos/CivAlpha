import { httpResource } from '@angular/common/http';
import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Column, ColumnChart } from '../charts/column-chart';
import { LineChart, LineSeries } from '../charts/line-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct, fmtSignedPct } from '../core/format';
import { StrategyDetailResponse } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';
import { FAMILY_LABEL, REFERENCE_KEY, equityFormat, equitySeries, supported } from './strategies';

@Component({
  selector: 'app-strategy-detail',
  imports: [RouterLink, LineChart, ColumnChart, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="crumbs"><a routerLink="/strategies">Strategy lab</a> / {{ key() }}</div>
    <app-status [res]="res" what="strategy" />

    @if (data(); as d) {
      @let s = d.result;
      @let m = s.metrics;
      <div class="page-head">
        <div class="page-title">
          <app-page-icon name="flask" area="strategy" />
          <div>
            <h1>{{ s.name }}</h1>
            <p class="muted">{{ familyLabel[s.family] ?? s.family }} · {{ s.description.origin }}</p>
          </div>
        </div>
        <div class="page-actions">
          <a routerLink="/guide" fragment="page-strategies" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        </div>
      </div>

      <app-verdict
        [tone]="s.family === 'BENCHMARK' ? 'info' : isSupported(s.verdict) ? 'good' : 'warn'"
        [title]="s.family === 'BENCHMARK' ? 'Reference.' : isSupported(s.verdict) ? 'Supported.' : 'Not supported.'"
      >{{ s.verdict }}</app-verdict>

      <div class="stats wide">
        <div class="stat" [class]="'stat tone-' + signTone(m.cagr)">
          <div class="stat-label">Annual return (CAGR) <app-help text="Compound annual growth rate after costs: what the strategy earned per year, averaged geometrically over the window." topic="cagr" label="CAGR" /></div>
          <div class="stat-value"><app-delta [value]="m.cagr" kind="pct" [digits]="1" /></div>
          <div class="stat-sub">total <app-delta [value]="m.totalReturn" kind="pct" [digits]="1" /> over {{ m.years | fixed: 1 }} years</div>
        </div>
        <div class="stat" [class]="'stat tone-' + ((m.sharpe ?? 0) >= 1 ? 'good' : (m.sharpe ?? 0) > 0 ? 'neutral' : 'bad')">
          <div class="stat-label">Sharpe / Sortino <app-help text="Return per unit of risk, annualized, over cash. Sortino counts only downside risk. Above 1 is good for one strategy, but the DSR decides whether it is luck." topic="sharpe" label="Sharpe ratio" /></div>
          <div class="stat-value">{{ m.sharpe | fixed: 2 }}<span class="unit">/ {{ m.sortino | fixed: 2 }}</span></div>
          <div class="stat-sub">volatility {{ m.volatility | pct: 1 }} a year</div>
          <app-meter [value]="m.sharpe" [min]="-1" [max]="2" [target]="1" targetLabel="1.0" label="Sharpe ratio" />
        </div>
        <div class="stat tone-bad">
          <div class="stat-label">Max drawdown <app-help text="The largest fall from a peak to the following trough, as a share of the peak. The worst stretch a holder of this strategy would have sat through." topic="max-drawdown" label="max drawdown" /></div>
          <div class="stat-value">{{ m.maxDrawdown | pct: 1 }}</div>
          <div class="stat-sub">beta {{ m.beta | fixed: 2 }} vs buy &amp; hold</div>
        </div>
        <div class="stat tone-neutral">
          <div class="stat-label">Exposure <app-help text="Average share of capital invested. Timing rules sit in cash when their signal is off, so they carry less risk and usually less return." topic="exposure" label="exposure" /></div>
          <div class="stat-value">{{ m.exposure | pct: 0 }}<span class="unit">invested</span></div>
          <app-meter [value]="m.exposure" tone="neutral" label="Exposure" />
        </div>
        <div class="stat tone-info">
          <div class="stat-label">Trading</div>
          <div class="stat-value">{{ m.trades | num }}<span class="unit">trades</span></div>
          <div class="stat-sub">win rate {{ m.winRate | pct: 0 }} · held {{ m.avgHoldingDays | fixed: 0 }} days · costs {{ m.costDragPerYear | pct: 2 }} a year</div>
        </div>
        @if (m.excessReturn !== undefined && m.excessReturn !== null) {
          <div class="stat" [class]="'stat tone-' + excessTone(m)">
            <div class="stat-label">vs buy &amp; hold <app-help text="Annualized return above equal-weight buy and hold, with a 95% block-bootstrap confidence interval. Only an interval wholly above zero counts, and even then the DSR must be at least 0.95." topic="excess" label="excess return" /></div>
            <div class="stat-value"><app-delta [value]="m.excessReturn" kind="pct" [digits]="1" /><span class="unit">a year</span></div>
            <div class="stat-sub">
              <span class="ci-cell"><app-range-bar [lo]="m.excessCiLow" [hi]="m.excessCiHigh" [point]="m.excessReturn" [span]="ciSpan(m)" /> {{ m.excessCiLow | signedPct: 1 }} to {{ m.excessCiHigh | signedPct: 1 }}</span>
              @if (m.deflatedSharpe !== null && m.deflatedSharpe !== undefined) {
                <div>DSR {{ m.deflatedSharpe | fixed: 2 }} <span class="muted">(0.95 needed)</span></div>
                <app-meter [value]="m.deflatedSharpe" [target]="0.95" [tone]="m.deflatedSharpe >= 0.95 ? 'good' : 'neutral'" label="Deflated Sharpe Ratio" />
              }
            </div>
          </div>
        }
      </div>

      <div class="card">
        <h3>The rule</h3>
        <dl class="kv">
          <dt>Enter</dt>
          <dd>{{ s.description.entry }}</dd>
          <dt>Exit</dt>
          <dd>{{ s.description.exit }}</dd>
          <dt>Sizing</dt>
          <dd>{{ sizing(s.description.sizing) }}</dd>
          <dt>Window</dt>
          <dd>{{ m.start }} to {{ m.end }} ({{ m.years | fixed: 1 }} years, out of sample)</dd>
          @if (paramList().length) {
            <dt>Parameters</dt>
            <dd class="small mono">{{ paramList().join(' · ') }}</dd>
          }
        </dl>
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
                    <td class="num"><app-cell-bar [value]="t.tradeReturn" [max]="maxTrade()" [text]="t.tradeReturn | signedPct: 1" tone="sign" /></td>
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

  protected signTone(v: number | null | undefined): string {
    return typeof v !== 'number' || v === 0 ? 'neutral' : v > 0 ? 'good' : 'bad';
  }

  protected excessTone(m: { excessCiLow?: number | null; excessCiHigh?: number | null }): string {
    if (m.excessCiLow != null && m.excessCiLow > 0) return 'good';
    if (m.excessCiHigh != null && m.excessCiHigh < 0) return 'bad';
    return 'neutral';
  }

  protected ciSpan(m: { excessCiLow?: number | null; excessCiHigh?: number | null; excessReturn?: number | null }): number {
    return Math.max(Math.abs(m.excessCiLow ?? 0), Math.abs(m.excessCiHigh ?? 0), Math.abs(m.excessReturn ?? 0), 1e-6);
  }

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

  protected readonly maxTrade = computed(() => {
    let m = 0;
    for (const t of this.data()?.trades ?? []) if (typeof t.tradeReturn === 'number') m = Math.max(m, Math.abs(t.tradeReturn));
    return m || 1;
  });

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
