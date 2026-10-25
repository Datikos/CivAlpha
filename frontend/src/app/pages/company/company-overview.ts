import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../../charts/line-chart';
import { dateMs, isoDay } from '../../charts/chart-utils';
import { LiveQuotesService, nyTime } from '../../core/live-quotes.service';
import { apiUrl, valueOf } from '../../core/api';
import { FORMAT_PIPES } from '../../core/format';
import { latestByModel } from '../../core/forecast-utils';
import { CompanyEvent, CompanyEvents, DividendProfile, EarningsProfile, ForecastSummary, GuidanceTone, InsiderProfile, InsiderQuality, MODEL_KINDS, PriceSeries } from '../../core/models';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { CompanyContext } from './company-context';

type Range = '6M' | '1Y' | '3Y' | '5Y';
const RANGE_DAYS: Record<Range, number> = { '6M': 183, '1Y': 365, '3Y': 3 * 365, '5Y': 5 * 365 + 1 };

@Component({
  selector: 'app-company-overview',
  imports: [RouterLink, LineChart, ...UI, ...VIZ, ...FORMAT_PIPES],
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
                  <p style="margin: 0.4rem 0 0"><app-lean [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" /></p>
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
                {{ p.from }} → {{ p.to }}: {{ c.symbol }} <app-delta [value]="p.stock" kind="pct" [digits]="1" />,
                {{ c.benchmarkSymbol }} <app-delta [value]="p.bench" kind="pct" [digits]="1" />,
                excess <strong><app-delta [value]="p.excess" kind="pct" [digits]="1" /></strong>. Recorded prices (fact).
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
            @if (livePoint(); as lp) {
              <p class="small muted live-note">
                The last point is today's live price, not a close: {{ c.symbol }} {{ lp.price | usd }}
                (<app-delta [value]="lp.change" kind="pct" [digits]="2" /> since yesterday's close),
                {{ c.benchmarkSymbol }} <app-delta [value]="lp.benchChange" kind="pct" [digits]="2" />,
                at {{ lp.time }} New York{{ lp.stale ? ' (stale)' : '' }}. It is replaced by the close after the evening run.
              </p>
            }
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
          <h3 style="margin: 0">Earnings <app-help text="Results announcements as the market saw them: the 8-K that made the numbers public, the stock's excess return over its sector ETF in the session the release first traded, and the guidance tone read from the press release by a keyword rule (an estimate, with the sentence it matched). The next date is estimated from past announcements." topic="earnings" label="earnings announcements" /></h3>
          @if (earn(); as e) {
            @if (e.nextEstimate.date) {
              <span class="badge" [class]="'badge tone-' + ((e.nextEstimate.tradingDays ?? 99) <= 5 ? 'warn' : 'info')" title="Estimated from past announcements">next ~{{ e.nextEstimate.date }} · {{ e.nextEstimate.tradingDays }} trading days</span>
            }
          }
        </div>
        <app-status [res]="earnings" what="earnings announcements" />
        @if (earn(); as e) {
          @if (!e.announcements.length) {
            <p class="muted">No results 8-K ingested yet. They arrive with the pipeline (SEC filings).</p>
          } @else {
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr><th>Made public</th><th>Traded in session</th><th class="num">Reaction vs ETF</th><th>Guidance (estimate)</th><th>Release</th></tr>
                </thead>
                <tbody>
                  @for (a of e.announcements; track a.acceptedAt) {
                    <tr>
                      <td class="nowrap">{{ a.acceptedAt | utc }}</td>
                      <td class="nowrap">{{ a.sessionDate ?? '—' }}</td>
                      <td class="num"><app-delta [value]="a.reaction" kind="pct" [digits]="1" /></td>
                      <td>
                        <span class="badge" [class]="'badge tone-' + toneLabel[a.guidanceTone].tone" [title]="a.guidanceText || ''">{{ toneLabel[a.guidanceTone].label }}</span>
                        @if (a.guidanceText) {
                          <div class="small muted evidence" [title]="a.guidanceText">“{{ a.guidanceText }}”</div>
                        }
                      </td>
                      <td class="nowrap small">
                        @if (a.exhibitUrl) { <a [href]="a.exhibitUrl" target="_blank" rel="noopener" title="Press release on EDGAR">EDGAR ↗</a> }
                        @if (a.exhibitDocumentUrl) { · <a [href]="a.exhibitDocumentUrl" target="_blank" rel="noopener" title="Stored copy">stored</a> }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.4rem">{{ e.note }} {{ e.announcementCount }} announcements on record.</p>
          }
        }
      </div>

      <div class="card">
        <div class="filters" style="justify-content: space-between; margin-bottom: 0.5rem">
          <h3 style="margin: 0">Insiders <app-help text="What officers, directors and 10% owners bought and sold in the open market, from their SEC Forms 4. Purchases carry information; sales mostly do not. Each open-market trade is marked: planned (a Rule 10b5-1 plan, ticked on the form since 2023), routine (the insider traded in the same month in each of the three previous years), informed (neither), or not classifiable (less than three years of history). Grants, option exercises and gifts are listed but not counted." topic="insiders" label="insider transactions" /></h3>
          @if (ins(); as i) {
            @if (i.windows['63d'].buyers + i.windows['63d'].sellers > 0) {
              <span class="badge" [class]="'badge tone-' + (i.windows['63d'].netValue > 0 ? 'good' : i.windows['63d'].netValue < 0 ? 'bad' : 'neutral')">
                {{ i.windows['63d'].netValue > 0 ? '▲ net buying' : i.windows['63d'].netValue < 0 ? '▼ net selling' : '• balanced' }} · 63 days
              </span>
            } @else {
              <span class="badge tone-neutral">quiet · 63 days</span>
            }
          }
        </div>
        <app-status [res]="insiders" what="insider transactions" />
        @if (ins(); as i) {
          @if (!i.transactionCount) {
            <p class="muted">No insider transactions loaded yet. They arrive with the pipeline (SEC Forms 4).</p>
          } @else {
            <div class="stats">
              @for (w of insiderWindows; track w.key) {
                <div class="stat" [class]="'stat tone-' + (i.windows[w.key].netValue > 0 ? 'good' : i.windows[w.key].netValue < 0 ? 'bad' : 'neutral')">
                  <div class="stat-label">Last {{ w.label }}</div>
                  <div class="stat-value"><app-delta [value]="i.windows[w.key].netValue" kind="num" [digits]="0" /><span class="unit">USD net</span></div>
                  <div class="stat-sub">{{ i.windows[w.key].buyers }} buying · {{ i.windows[w.key].sellers }} selling · {{ i.windows[w.key].buys + i.windows[w.key].sells }} trades</div>
                  <div class="stat-sub" title="Informed: neither under a Rule 10b5-1 plan nor routine (ADR-0010)">
                    informed {{ i.windows[w.key].informedBuys }} buys / {{ i.windows[w.key].informedSells }} sales ·
                    planned {{ i.windows[w.key].plannedSells }} sales · routine {{ i.windows[w.key].routineTrades }}
                  </div>
                </div>
              }
            </div>
            <div class="table-wrap" style="margin-top: 0.75rem">
              <table class="table compact">
                <thead>
                  <tr><th>Insider</th><th>Trade</th><th>Date</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Value</th><th class="num">Held after</th><th>Public</th></tr>
                </thead>
                <tbody>
                  @for (t of shownInsiders(); track t.id) {
                    <tr [class.quiet]="!t.signal">
                      <td>
                        <strong>{{ t.ownerName }}</strong>
                        @if (t.role === 'CEO_CFO') { <span class="chip tone-info" title="Chief executive or chief financial officer">CEO/CFO</span> }
                        <div class="small muted">{{ t.title || t.relationship }}</div>
                      </td>
                      <td>
                        <span class="badge" [class]="'badge tone-' + (t.signal ? (t.acquired ? 'good' : 'bad') : 'neutral')">{{ t.acquired ? '▲' : '▼' }} {{ t.codeLabel }}</span>
                        @if (t.quality; as q) {
                          <span class="chip" [class.tone-good]="q === 'INFORMED'" [title]="qualityHelp[q]">{{ qualityLabel[q] }}</span>
                        }
                      </td>
                      <td class="nowrap">{{ t.transDate }}</td>
                      <td class="num">{{ t.shares | num }}</td>
                      <td class="num">{{ t.price === null ? '—' : (t.price | usd) }}</td>
                      <td class="num">{{ t.value === null ? '—' : (t.value | usd) }}</td>
                      <td class="num">{{ t.sharesAfter === null ? '—' : (t.sharesAfter | num) }}</td>
                      <td class="nowrap small">
                        {{ t.filedDate }}
                        @if (t.url) { <a [href]="t.url" target="_blank" rel="noopener" title="Source on EDGAR">↗</a> }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            @if (i.transactions.length > INSIDERS_SHOWN) {
              <button type="button" class="btn btn-link small" (click)="allInsiders.set(!allInsiders())">
                {{ allInsiders() ? 'Show latest ' + INSIDERS_SHOWN : 'Show all ' + i.transactions.length + ' loaded' }}
              </button>
            }
            <p class="small muted" style="margin-top: 0.4rem">{{ i.note }} Latest filing available {{ i.newestAvailableAt | utc }}.</p>
          }
        }
      </div>

      <div class="card">
        <div class="filters" style="justify-content: space-between; margin-bottom: 0.5rem">
          <h3 style="margin: 0">Company events (8-K) <app-help text="Events the company had to report to the SEC on Form 8-K within four business days: a CEO or CFO leaving (read from item 5.02's text), restructuring, impairments, listing notices, auditor changes, restatements, cyber incidents, material agreements signed or ended, acquisitions completed. Each counts from the first market close after the SEC accepted the filing. Whether any of them was followed by under- or outperformance is on the Setup playbook (ADR-0011)." topic="company-events" label="company events" /></h3>
          @if (cev(); as c) {
            @if (c.lastYear['CEO_DEPARTURE'] || c.lastYear['CFO_DEPARTURE']) {
              <span class="badge tone-warn">executive change · last 12 months</span>
            }
          }
        </div>
        <app-status [res]="companyEvents" what="company events" />
        @if (cev(); as c) {
          @if (!c.events.length) {
            <p class="muted">No 8-K events stored for this company@if (c.since) {<span> since {{ c.since | utc }}</span>}. They arrive with the pipeline; the history comes from Data &amp; pipeline → Advanced → Load 8-K company events.</p>
          } @else {
            <div class="table-wrap">
              <table class="table compact">
                <thead><tr><th>Accepted</th><th>Event</th><th>Item</th><th>What the filing says</th><th>Filing</th></tr></thead>
                <tbody>
                  @for (e of shownEvents(); track e.id) {
                    <tr>
                      <td class="nowrap small">{{ e.acceptedAt | utc }}</td>
                      <td><span class="chip" [class]="'chip tone-' + eventTone(e)">{{ e.label }}</span></td>
                      <td class="mono small">{{ e.item }}</td>
                      <td class="small">{{ e.evidence ?? '—' }}</td>
                      <td class="nowrap small">
                        @if (e.url) { <a [href]="e.url" target="_blank" rel="noopener" title="Filing on EDGAR">EDGAR ↗</a> }
                        @if (e.documentUrl) { · <a [href]="e.documentUrl" target="_blank" rel="noopener">stored</a> }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            @if (c.events.length > EVENTS_SHOWN) {
              <button type="button" class="btn btn-link small" (click)="allEvents.set(!allEvents())">
                {{ allEvents() ? 'Show latest ' + EVENTS_SHOWN : 'Show all ' + c.events.length + ' loaded' }}
              </button>
            }
            <p class="small muted" style="margin-top: 0.4rem">{{ c.note }}</p>
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
              <div class="stat tone-good" title="Cash dividends with an ex-date in the last 12 months / last close">
                <div class="stat-label">Trailing 12-month yield</div>
                <div class="stat-value">{{ d.trailingYield | pct: 2 }}</div>
                <div class="small muted">
                  {{ d.ttmDividends | usd }} in {{ d.ttmPayments }} payment{{ d.ttmPayments === 1 ? '' : 's' }}
                  @if (d.ttmSpecial) { (incl. {{ d.ttmSpecial | usd }} special) }
                </div>
              </div>
              <div class="stat tone-warn" title="Latest regular payment × payments per year / last close (estimate)">
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
                12 months {{ po.periodStart }} → {{ po.periodEnd }} ({{ po.formType ?? '—' }} filed {{ po.filedDate ?? '—' }})
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
  styles: `
    .live-note { margin: 0.35rem 0 0; }
    tr.quiet td { opacity: 0.65; }
    .evidence { max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .btn-link { background: none; border: none; padding: 0.4rem 0; color: var(--accent); cursor: pointer; }
  `,
})
export class CompanyOverview {
  protected readonly ctx = inject(CompanyContext);
  private readonly live = inject(LiveQuotesService);
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

  protected readonly earnings = httpResource<EarningsProfile>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.earnings(s) : undefined;
  });
  protected readonly earn = computed(() => valueOf(this.earnings));
  protected readonly toneLabel: Record<GuidanceTone, { label: string; tone: string }> = {
    RAISED: { label: '▲ guidance raised', tone: 'good' },
    LOWERED: { label: '▼ guidance lowered', tone: 'bad' },
    MAINTAINED: { label: '• guidance maintained', tone: 'neutral' },
    PROVIDED: { label: '• outlook given', tone: 'info' },
    NONE: { label: 'no outlook language', tone: 'neutral' },
    UNKNOWN: { label: 'release not read', tone: 'neutral' },
  };
  protected readonly insiders = httpResource<InsiderProfile>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.insiders(s) : undefined;
  });

  protected readonly ins = computed(() => valueOf(this.insiders));

  protected readonly companyEvents = httpResource<CompanyEvents>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.companyEvents(s) : undefined;
  });
  protected readonly cev = computed(() => valueOf(this.companyEvents));
  protected readonly EVENTS_SHOWN = 8;
  protected readonly allEvents = signal(false);
  protected readonly shownEvents = computed(() => {
    const list = this.cev()?.events ?? [];
    return this.allEvents() ? list : list.slice(0, this.EVENTS_SHOWN);
  });

  protected eventTone(e: CompanyEvent): string {
    return e.distress || e.kind === 'CEO_DEPARTURE' || e.kind === 'CFO_DEPARTURE' || e.kind === 'AGREEMENT_TERMINATED' ? 'warn' : 'neutral';
  }

  protected readonly INSIDERS_SHOWN = 8;
  protected readonly qualityLabel: Record<InsiderQuality, string> = {
    PLANNED: 'planned',
    ROUTINE: 'routine',
    INFORMED: 'informed',
    NOT_CLASSIFIABLE: 'not classifiable',
  };
  protected readonly qualityHelp: Record<InsiderQuality, string> = {
    PLANNED: 'Made under a Rule 10b5-1 trading plan set up in advance (the box on the Form 4)',
    ROUTINE: 'The insider traded in this calendar month in each of the three previous years: a habit, not news',
    INFORMED: 'Neither planned nor routine: the trades the research finds informative (ADR-0010)',
    NOT_CLASSIFIABLE: 'Less than three years of this insider\'s history (or of the data) to tell routine from informed',
  };

  protected readonly allInsiders = signal(false);

  protected readonly insiderWindows: { key: '21d' | '63d' | '252d'; label: string }[] = [

    { key: '21d', label: '21 trading days' },

    { key: '63d', label: '63 trading days' },

    { key: '252d', label: 'year' },

  ];

  protected readonly shownInsiders = computed(() => {

    const list = this.ins()?.transactions ?? [];

    return this.allInsiders() ? list : list.slice(0, this.INSIDERS_SHOWN);

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

  /** ADR-0006: today's live quotes of the stock and its ETF, when they are newer than the last stored close. */
  protected readonly livePoint = computed(() => {
    const bars = this.windowBars();
    const p = valueOf(this.prices);
    if (!bars.length || !p) return null;
    const q = this.live.quote(p.symbol);
    const qb = this.live.quote(p.benchmarkSymbol);
    if (!q || !qb) return null;
    const day = new Date(q.quotedAt).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });   // YYYY-MM-DD
    if (day <= bars[bars.length - 1].date) return null;
    return { day, price: q.price, change: q.change, benchPrice: qb.price, benchChange: qb.change, time: nyTime(q.quotedAt),
             stale: q.stale || qb.stale };
  });

  protected readonly priceSeries = computed<LineSeries[] | null>(() => {
    const bars = this.windowBars();
    const p = valueOf(this.prices);
    if (!bars.length || !p) return null;
    const s0 = bars[0].close!;
    const b0 = bars[0].benchmarkClose!;
    const lp = this.livePoint();
    return [
      {
        key: 'stock',
        label: p.symbol,
        color: 'var(--series-1)',
        points: [...bars.map((b) => ({ x: dateMs(b.date), y: (b.close! / s0) * 100 })),
                 ...(lp ? [{ x: dateMs(lp.day), y: (lp.price / s0) * 100 }] : [])],
      },
      {
        key: 'bench',
        label: p.benchmarkSymbol,
        color: 'var(--series-2)',
        points: [...bars.map((b) => ({ x: dateMs(b.date), y: (b.benchmarkClose! / b0) * 100 })),
                 ...(lp ? [{ x: dateMs(lp.day), y: (lp.benchPrice / b0) * 100 }] : [])],
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
      stock: rs,
      bench: rb,
      excess: rs - rb,
    };
  });
}
