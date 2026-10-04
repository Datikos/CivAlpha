import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../../charts/line-chart';
import { dateMs, isoDay } from '../../charts/chart-utils';
import { apiUrl, valueOf } from '../../core/api';
import { FORMAT_PIPES, fmtSignedPct } from '../../core/format';
import { latestByModel } from '../../core/forecast-utils';
import { DividendProfile, ForecastSummary, MODEL_KINDS, PriceSeries } from '../../core/models';
import { UI } from '../../shared/ui';
import { CompanyContext } from './company-context';

type Range = '6M' | '1Y' | '3Y' | '5Y';
const RANGE_DAYS: Record<Range, number> = { '6M': 183, '1Y': 365, '3Y': 3 * 365, '5Y': 5 * 365 + 1 };

@Component({
  selector: 'app-company-overview',
  imports: [RouterLink, LineChart, ...UI, ...FORMAT_PIPES],
  template: `
    @if (ctx.detail(); as c) {
      <div class="section">
        <h2>Latest forecasts</h2>
        <app-status [res]="fc" what="forecasts" />
        @if (fc.hasValue()) {
          <div class="grid-2">
            @for (cell of latest(); track cell.kind) {
              <div class="card forecast-hero">
                <div style="display: flex; justify-content: space-between; gap: 0.5rem; flex-wrap: wrap">
                  <app-model-tag [kind]="cell.kind" />
                  @if (cell.f; as f) {
                    <span><app-issue-mode [mode]="f.issueMode" /></span>
                  }
                </div>
                @if (cell.f; as f) {
                  <p class="small muted" style="margin: 0.4rem 0">
                    P({{ f.symbol }} outperforms {{ f.benchmarkSymbol }} over {{ f.horizonTradingDays }} trading days)
                  </p>
                  <app-forecast-prob [f]="f" />
                  <p style="margin: 0.5rem 0 0">
                    <a [routerLink]="['/forecasts', f.id]">Explanation &amp; sources →</a>
                  </p>
                } @else {
                  <p class="muted">No forecast issued.</p>
                }
              </div>
            }
          </div>
        }
      </div>

      <div class="card">
        <div class="filters" style="justify-content: space-between; margin-bottom: 0.5rem">
          <div>
            <h3 style="margin: 0">Price vs benchmark (closes indexed to 100)</h3>
            @if (perf(); as p) {
              <p class="small muted" style="margin: 0">
                {{ p.from }} → {{ p.to }}: {{ c.symbol }} {{ p.stock }}, {{ c.benchmarkSymbol }} {{ p.bench }},
                excess {{ p.excess }}. Recorded prices (fact).
              </p>
            }
          </div>
          <div class="seg" role="group" aria-label="Range">
            @for (r of ranges; track r) {
              <button type="button" [class.on]="range() === r" (click)="range.set(r)" [attr.aria-pressed]="range() === r">
                {{ r }}
              </button>
            }
          </div>
        </div>
        <app-status [res]="prices" what="prices" />
        @if (prices.hasValue()) {
          @if (priceSeries(); as ps) {
            <app-line-chart
              [series]="ps"
              [refY]="100"
              refLabel="Start = 100"
              [height]="280"
              [yFormat]="fmtIndex"
              [label]="c.symbol + ' vs ' + c.benchmarkSymbol + ' indexed to 100'"
            />
          } @else {
            <p class="empty">No price history.</p>
          }
          @if (actions().length) {
            <details class="chart-table">
              <summary>Corporate actions ({{ actions().length }})</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead><tr><th>Ex-date</th><th>Type</th><th class="num">Value</th></tr></thead>
                  <tbody>
                    @for (a of actions(); track $index) {
                      <tr><td>{{ a.exDate }}</td><td>{{ a.type | human }}</td><td class="num">{{ a.value ?? '—' }}</td></tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
          }
        }
      </div>

      <div class="card">
        <div class="filters" style="justify-content: space-between; margin-bottom: 0.5rem">
          <h3 style="margin: 0">Dividends</h3>
          @if (dv(); as d) {
            <app-dividend-badge [d]="d" />
          }
        </div>
        <app-status [res]="div" what="dividends" />
        @if (dv(); as d) {
          @if (d.status === 'NONE') {
            <p class="muted">No cash dividend recorded in the price history.</p>
          } @else {
            <div class="stats">
              <div class="stat" title="Cash dividends with an ex-date in the last 12 months / last close">
                <div class="stat-label">Trailing 12-month yield</div>
                <div class="stat-value">{{ d.trailingYield | pct: 2 }}</div>
                <div class="small muted">
                  {{ d.ttmDividends | usd }} in {{ d.ttmPayments }} payment{{ d.ttmPayments === 1 ? '' : 's' }}
                  @if (d.ttmSpecial) { (incl. {{ d.ttmSpecial | usd }} special) }
                </div>
              </div>
              <div class="stat" title="Latest regular payment × payments per year / last close (estimate)">
                <div class="stat-label">Indicated yield <span class="badge badge-estimated">EST</span></div>
                <div class="stat-value">{{ d.indicatedYield | pct: 2 }}</div>
                <div class="small muted">{{ d.indicatedAnnual | usd }} a year</div>
              </div>
              <div class="stat">
                <div class="stat-label">Last ex-date</div>
                <div class="stat-value">{{ d.lastExDate }}</div>
                <div class="small muted">
                  {{ d.lastAmount | usd }} per share
                  @if (d.nextExpected) { · next ~{{ d.nextExpected }} }
                </div>
              </div>
              <div class="stat" title="Consecutive calendar years with a dividend / with a higher last regular payment than the year before">
                <div class="stat-label">Years paid / raised</div>
                <div class="stat-value">{{ d.yearsPaid }} / {{ d.yearsRaised }}</div>
                <div class="small muted">recorded since {{ d.firstExDate }}</div>
              </div>
            </div>
          }

          @if (d.payout; as po) {
            <div class="fact-block">
              <p class="small" style="margin: 0 0 0.4rem">
                <span class="badge badge-fact">FILED</span>
                Fiscal year {{ po.fiscalYearStart }} → {{ po.fiscalYearEnd }} ({{ po.formType ?? '—' }} filed {{ po.filedDate ?? '—' }})
                @if (po.sourceUrl) {
                  · <a [href]="po.sourceUrl" target="_blank" rel="noopener noreferrer">source ↗</a>
                }
              </p>
              <dl class="kv">
                <dt>Net income</dt><dd>{{ po.netIncome | usd }}</dd>
                <dt>Dividends paid</dt><dd>{{ po.dividendsPaid | usd }}</dd>
                <dt>Share buybacks</dt><dd>{{ po.buybacks | usd }}</dd>
                <dt title="Dividends paid / net income">Payout ratio</dt>
                <dd>{{ po.payoutRatio | pct }} @if (po.netIncome <= 0 && po.dividendsPaid) { <span class="small muted">not meaningful with a loss</span> }</dd>
                <dt title="(Dividends + buybacks) / net income">Total payout ratio</dt><dd>{{ po.totalPayoutRatio | pct }}</dd>
              </dl>
            </div>
          }

          @if (d.payments.length) {
            <details class="chart-table">
              <summary>Payment history ({{ d.payments.length }}, per current share)</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead><tr><th>Year</th><th class="num">Payments</th><th class="num">Total per share</th></tr></thead>
                  <tbody>
                    @for (y of annualDesc(); track y.year) {
                      <tr><td>{{ y.year }}</td><td class="num">{{ y.payments }}</td><td class="num">{{ y.total | fixed: 4 }}</td></tr>
                    }
                  </tbody>
                </table>
                <table class="table compact">
                  <thead><tr><th>Ex-date</th><th class="num">Amount</th><th></th></tr></thead>
                  <tbody>
                    @for (p of d.payments; track p.exDate) {
                      <tr>
                        <td>{{ p.exDate }}</td>
                        <td class="num">{{ p.amount | fixed: 4 }}</td>
                        <td>@if (p.special) { <span class="badge">special</span> }</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
          }
        }
      </div>

      <div class="grid-2">
        <div class="card fact-block">
          <h3>Ticker history</h3>
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>Symbol</th><th>Valid from</th><th>Valid to</th><th>Source</th></tr></thead>
              <tbody>
                @for (t of c.tickerHistory; track $index) {
                  <tr [class.row-dim]="t.validTo">
                    <td><strong>{{ t.symbol }}</strong> @if (!t.validTo) { <span class="badge">current</span> } @else { <span class="badge">prior</span> }</td>
                    <td>{{ t.validFrom ?? '—' }}</td>
                    <td>{{ t.validTo ?? '—' }}</td>
                    <td class="small">{{ t.source ?? '—' }}</td>
                  </tr>
                } @empty {
                  <tr><td colspan="4" class="muted">No ticker history recorded.</td></tr>
                }
              </tbody>
            </table>
          </div>
          <h3 style="margin-top: 1rem">CIK history</h3>
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>CIK</th><th>Valid from</th><th>Valid to</th><th>Source</th></tr></thead>
              <tbody>
                @for (k of c.cikHistory; track $index) {
                  <tr>
                    <td class="mono">{{ k.cik }}</td>
                    <td>{{ k.validFrom ?? '—' }}</td>
                    <td>{{ k.validTo ?? '—' }}</td>
                    <td class="small">{{ k.source ?? '—' }}</td>
                  </tr>
                } @empty {
                  <tr><td colspan="4" class="muted">No CIK history recorded.</td></tr>
                }
              </tbody>
            </table>
          </div>
        </div>

        <div class="card fact-block">
          <h3>Key facts <span class="badge badge-fact">FILED</span></h3>
          <p class="small muted">Latest values as filed with the SEC; each links to its source filing.</p>
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>Fact</th><th class="num">Value</th><th>Period</th><th>Form / filed</th><th></th></tr></thead>
              <tbody>
                @for (f of c.keyFacts; track $index) {
                  <tr>
                    <td>{{ f.label || f.concept }}<div class="small muted mono">{{ f.concept }}</div></td>
                    <td class="num">{{ f.value | unitValue: f.unit }}</td>
                    <td class="nowrap">
                      {{ f.fiscalPeriod ?? '' }}
                      <div class="small muted">{{ f.periodStart ? f.periodStart + ' → ' : '' }}{{ f.periodEnd ?? '' }}</div>
                    </td>
                    <td class="nowrap">{{ f.formType ?? '—' }}<div class="small muted">{{ f.filedDate ?? '' }}</div></td>
                    <td class="nowrap">
                      @if (f.sourceUrl) {
                        <a [href]="f.sourceUrl" target="_blank" rel="noopener noreferrer">source ↗</a>
                      }
                    </td>
                  </tr>
                } @empty {
                  <tr><td colspan="5" class="muted">No facts available.</td></tr>
                }
              </tbody>
            </table>
          </div>
        </div>
      </div>
    }
  `,
})
export class CompanyOverview {
  protected readonly ctx = inject(CompanyContext);
  protected readonly ranges: Range[] = ['6M', '1Y', '3Y', '5Y'];
  protected readonly range = signal<Range>('1Y');
  protected readonly fmtIndex = (v: number) => v.toFixed(1);

  private readonly fromDate = isoDay(Date.now() - RANGE_DAYS['5Y'] * 864e5);

  protected readonly prices = httpResource<PriceSeries>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.prices(s, this.fromDate) : undefined;
  });

  protected readonly fc = httpResource<ForecastSummary[]>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.forecastsHistory(s) : undefined;
  });

  protected readonly latest = computed(() => {
    const l = latestByModel(valueOf(this.fc) ?? []);
    return MODEL_KINDS.map((kind) => ({ kind, f: l[kind] ?? null }));
  });

  protected readonly div = httpResource<DividendProfile>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.dividends(s) : undefined;
  });

  protected readonly dv = computed(() => valueOf(this.div));
  protected readonly annualDesc = computed(() => [...(this.dv()?.annual ?? [])].reverse());

  protected readonly actions = computed(() => valueOf(this.prices)?.corporateActions ?? []);

  /** Bars within the selected range (relative to the last available bar), both closes present. */
  private readonly windowBars = computed(() => {
    const p = valueOf(this.prices);
    if (!p?.bars?.length) return [];
    const bars = p.bars
      .filter((b) => b.close !== null && b.benchmarkClose !== null && b.close > 0 && b.benchmarkClose > 0)
      .sort((a, b) => a.date.localeCompare(b.date));
    if (!bars.length) return [];
    const last = dateMs(bars[bars.length - 1].date);
    const start = last - RANGE_DAYS[this.range()] * 864e5;
    return bars.filter((b) => dateMs(b.date) >= start);
  });

  protected readonly priceSeries = computed<LineSeries[] | null>(() => {
    const bars = this.windowBars();
    const p = valueOf(this.prices);
    if (!bars.length || !p) return null;
    const s0 = bars[0].close!;
    const b0 = bars[0].benchmarkClose!;
    return [
      {
        key: 'stock',
        label: p.symbol,
        color: 'var(--series-1)',
        points: bars.map((b) => ({ x: dateMs(b.date), y: (b.close! / s0) * 100 })),
      },
      {
        key: 'bench',
        label: p.benchmarkSymbol,
        color: 'var(--series-2)',
        points: bars.map((b) => ({ x: dateMs(b.date), y: (b.benchmarkClose! / b0) * 100 })),
      },
    ];
  });

  protected readonly perf = computed(() => {
    const bars = this.windowBars();
    if (bars.length < 2) return null;
    const a = bars[0];
    const z = bars[bars.length - 1];
    const rs = z.close! / a.close! - 1;
    const rb = z.benchmarkClose! / a.benchmarkClose! - 1;
    return {
      from: a.date,
      to: z.date,
      stock: fmtSignedPct(rs, 1),
      bench: fmtSignedPct(rb, 1),
      excess: fmtSignedPct(rs - rb, 1),
    };
  });
}
