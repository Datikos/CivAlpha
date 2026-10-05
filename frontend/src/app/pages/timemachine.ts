import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { LinePoint, LineChart, LineSeries } from '../charts/line-chart';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtSignedPct } from '../core/format';
import { Job, TimeMachineRun, TimeMachineRunSummary, TmStock } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

const DAY = 86_400_000;
const toX = (d: string) => Date.parse(d + 'T00:00:00Z');

function isoDaysAgo(n: number): string {
  return new Date(Date.now() - n * DAY).toISOString().slice(0, 10);
}

@Component({
  selector: 'app-time-machine',
  imports: [FormsModule, RouterLink, LineChart, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="hourglass" area="strategy" />
        <div>
          <h1>Time machine</h1>
          <p class="muted">
            Go back to a past close and forecast with only the data that existed then — the beat-the-sector odds, the AI's
            buy decisions and a 10–90% return band — then see what actually happened 5, 10, 21 and 63 trading days later.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-timemachine" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <div class="card run-card">
      <form class="inline-form" (ngSubmit)="start()">
        <label class="field">
          As-of date
          <input type="date" name="asOf" [max]="yesterday" [ngModel]="asOf()" (ngModelChange)="asOf.set($event ?? '')" required />
        </label>
        <button type="submit" class="btn btn-primary" [disabled]="running() || !asOf()">
          {{ running() ? 'Running…' : 'Go back and forecast' }}
        </button>
        @if (runList().length) {
          <label class="field">
            Earlier runs
            <select (change)="selectedId.set(+$any($event.target).value)">
              @for (r of runList(); track r.id) {
                <option [value]="r.id" [selected]="r.id === currentId()">{{ r.asOfDate }} (run {{ r.runAt | utc }})</option>
              }
            </select>
          </label>
        }
      </form>
      <p class="small muted">
        Takes about a minute. Models are retrained on outcomes that had resolved by that close; later filings, events and
        prices are invisible to them. Pick a date at least 63 trading days ago to see every horizon.
      </p>
      @if (notice()) {
        <div class="alert" [class.alert-error]="noticeError()" role="status">{{ notice() }}</div>
      }
    </div>

    <app-status [res]="runs" what="time machine runs" />

    @if (run.hasValue() && data(); as r) {
      <app-verdict tone="info" [title]="'As of ' + r.asOfDate + '.'">{{ r.headline }}</app-verdict>
      <p class="small muted">
        Run {{ r.runAt | utc }} with data through {{ r.dataCutoff }}
      </p>

      <div class="horizon-row">
        <div class="seg" role="group" aria-label="Horizon">
          @for (h of r.result.horizons; track h) {
            <button type="button" [class.on]="h + '' === horizon()" (click)="horizon.set(h + '')" [attr.aria-pressed]="h + '' === horizon()">
              {{ h }} trading days
            </button>
          }
        </div>
        @if (sum(); as s) {
          <span class="small muted">{{ s.resolved ? s.resolved + ' outcomes known (to ' + s.endDate + ')' : 'outcome not known yet' }}</span>
        }
      </div>

      @if (sum(); as s) {
        <div class="stats wide">
          <div class="stat" [class]="'stat ' + (s.odds?.AUGMENTED ? ((s.odds?.AUGMENTED?.hitRate ?? 0) > 0.5 ? 'tone-good' : 'tone-warn') : 'tone-neutral')">
            <div class="stat-label">Beat-the-sector odds <app-help text="Share of stocks where the probability pointed the right way (above 50% and the stock beat its ETF, or below and it did not). A coin would score about 50%." topic="hit-rate" label="hit rate" /></div>
            @if (s.odds?.AUGMENTED; as o) {
              <div class="stat-value">{{ o.hitRate | pct: 0 }}<span class="unit">of {{ o.n }} calls right</span></div>
              <app-meter [value]="o.hitRate" [target]="0.5" targetLabel="coin flip" label="Hit rate" />
              <div class="stat-sub">
                Brier {{ o.brier | fixed: 3 }} vs {{ o.brierBaseRate | fixed: 3 }} base rate
                <span [class]="'badge tone-' + (o.brier < o.brierBaseRate ? 'good' : 'warn')">{{ o.brier < o.brierBaseRate ? '✓ better' : '≈ no better' }}</span>
                · AUC {{ o.auc | fixed: 2 }} · top 5 − bottom 5 <app-delta [value]="o.topMinusBottomExcess" kind="pct" [digits]="1" />
                @if (s.odds?.BASELINE; as b) {
                  <div>Baseline model: {{ b.hitRate | pct: 0 }} right, Brier {{ b.brier | fixed: 3 }}</div>
                }
              </div>
            } @else {
              <div class="stat-value muted">Not known yet</div>
            }
          </div>
          <div class="stat" [class]="'stat ' + aiTone(s)">
            <div class="stat-label">AI buy decisions <app-help text="What the AI strategy would have bought at the next close, equal weight, against the return of every tracked stock over the same window." topic="page-decisions" label="AI picks" /></div>
            @if (s.ai; as a) {
              @if (a.picks.length) {
                <div class="stat-value"><app-delta [value]="a.picksReturn" kind="pct" [digits]="1" /><span class="unit">picks</span></div>
                <div class="stat-sub">
                  all stocks <app-delta [value]="a.universeReturn" kind="pct" [digits]="1" /> · picks vs all
                  <app-delta [value]="(a.picksReturn ?? 0) - a.universeReturn" kind="pct" [digits]="1" />
                  <div>{{ a.picks.join(', ') }} · {{ a.picksBeatSector }} of {{ a.picks.length }} beat their sector</div>
                </div>
              } @else {
                <div class="stat-value muted">Bought nothing</div>
                <div class="stat-sub">all stocks <app-delta [value]="a.universeReturn" kind="pct" [digits]="1" /></div>
              }
            } @else {
              <div class="stat-value muted">Not known yet</div>
            }
          </div>
          <div class="stat" [class]="'stat ' + (s.range ? (s.range.coverage >= s.range.target - 0.05 ? 'tone-good' : 'tone-warn') : 'tone-neutral')">
            <div class="stat-label">10–90% return band <app-help text="For each stock the model gave a band that should contain the actual return 80% of the time. Coverage near 80% with a narrower band than the naive one is what a useful band looks like." topic="coverage" label="band coverage" /></div>
            @if (s.range; as g) {
              <div class="stat-value">{{ g.coverage | pct: 0 }}<span class="unit">of actual returns inside</span></div>
              <app-meter [value]="g.coverage" [target]="g.target" [targetLabel]="'target ' + (g.target * 100).toFixed(0) + '%'" label="Band coverage" />
              <div class="stat-sub">
                naive band {{ g.naiveCoverage | pct: 0 }} inside · width {{ g.avgWidth | pct: 1 }} vs naive {{ g.naiveWidth | pct: 1 }}
                <div>median miss {{ g.medianAbsError | pct: 1 }} vs {{ g.naiveMedianAbsError | pct: 1 }} naive</div>
              </div>
            } @else {
              <div class="stat-value muted">Not known yet</div>
            }
          </div>
        </div>
      }

      @if (chartStock(); as cs) {
        <div class="card">
          <div class="chart-head">
            <h3>{{ cs.symbol }}: forecast vs what happened</h3>
            <span class="small muted">click a row below to switch stock</span>
          </div>
          <app-line-chart
            [series]="chart()"
            [label]="'Cumulative return of ' + cs.symbol + ' and its sector ETF after ' + r.asOfDate + ', with the forecast band'"
            caption="Total return from the as-of close. The band is the 10–90% forecast made that day; dots mark each horizon."
            [yFormat]="pctFmt"
            [refY]="0"
            refLabel="as-of close"
          />
        </div>
      }

      <div class="card">
        <h3>Stock by stock ({{ horizon() }} trading days)</h3>
        <div class="table-wrap">
          <table class="table compact">
            <thead>
              <tr>
                <th>Stock</th>
                <th>AI</th>
                <th class="num">P(beat sector)</th>
                <th class="num">Actual vs sector</th>
                <th>Call</th>
                <th class="num">Forecast band</th>
                <th class="num">Actual return</th>
                <th>In band</th>
              </tr>
            </thead>
            <tbody>
              @for (x of rows(); track x.s.companyId) {
                <tr [class.selected]="x.s.companyId === chartStock()?.companyId" (click)="chartId.set(x.s.companyId)" tabindex="0"
                    (keydown.enter)="chartId.set(x.s.companyId)">
                  <td><a [routerLink]="['/companies', x.s.symbol]" (click)="$event.stopPropagation()">{{ x.s.symbol }}</a></td>
                  <td>
                    @if (x.s.ai) {
                      <span class="badge" [class]="'badge tone-' + (x.s.ai.action === 'ENTER' ? 'good' : 'neutral')">{{ x.s.ai.action === 'ENTER' ? '▲ Buy' : '○ Out' }}</span>
                    }
                  </td>
                  <td class="num">
                    @if (x.p !== undefined) {
                      <app-cell-bar [value]="x.p" [max]="1" [text]="x.p | pct: 0" [tone]="x.p > 0.5 ? 'good' : x.p < 0.5 ? 'bad' : 'neutral'" />
                    }
                  </td>
                  <td class="num"><app-delta [value]="x.a?.excess" kind="pct" [digits]="1" /></td>
                  <td>
                    @if (x.a && x.p !== undefined) {
                      <span class="badge" [class]="'badge tone-' + (x.right ? 'good' : 'bad')">{{ x.right ? '✓ right' : '✗ wrong' }}</span>
                    }
                  </td>
                  <td class="num">
                    @if (x.g) {
                      {{ x.g.q10 | signedPct: 1 }} to {{ x.g.q90 | signedPct: 1 }}
                    }
                  </td>
                  <td class="num"><app-delta [value]="x.a?.stockReturn" kind="pct" [digits]="1" /></td>
                  <td>
                    @if (x.a && x.g) {
                      <span [class]="x.inside ? 'good' : 'bad'" [title]="x.inside ? 'Actual return inside the forecast band' : 'Actual return outside the band'">{{ x.inside ? '✓' : '✗' }}</span>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        <p class="small muted">
          P(beat sector) is the AUGMENTED model's probability that the stock beats its sector ETF over this horizon; a call is
          right when p &gt; 50% and it did, or p &lt; 50% and it did not. One date gives one outcome per stock, so luck
          dominates — the <a routerLink="/accuracy">walk-forward accuracy</a> and the <a routerLink="/strategies">strategy lab</a>
          hold the multi-year evidence.
        </p>
      </div>
    } @else if (runs.hasValue() && !runList().length) {
      <div class="empty-box">No time machine runs yet. Pick a past date above.</div>
    }
  `,
  styles: `
    .run-card .inline-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.75rem; }
    .horizon-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; margin: 0.75rem 0 1rem; }
    .chart-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
    tr.selected td { background: var(--surface-2); }
    tbody tr { cursor: pointer; }
    .good { color: var(--good-ink); font-weight: 700; }
    .bad { color: var(--bad-ink); font-weight: 700; }
    .stat-sub .badge { margin-left: 0.25em; }
  `,
})
export class TimeMachinePage {
  private readonly api = inject(ApiService);
  protected readonly yesterday = isoDaysAgo(1);
  protected readonly asOf = signal(isoDaysAgo(180));
  protected readonly horizon = signal('21');
  protected readonly selectedId = signal<number | null>(null);
  protected readonly chartId = signal<number | null>(null);
  protected readonly jobId = signal<number | null>(null);
  protected readonly notice = signal('');
  protected readonly noticeError = signal(false);
  protected readonly pctFmt = (v: number) => fmtSignedPct(v, 0);

  protected readonly runs = httpResource<TimeMachineRunSummary[]>(() => apiUrl.timeMachineRuns());
  protected readonly runList = computed(() => valueOf(this.runs) ?? []);
  protected readonly currentId = computed(() => this.selectedId() ?? this.runList()[0]?.id ?? null);
  protected readonly run = httpResource<TimeMachineRun>(() => {
    const id = this.currentId();
    return id ? apiUrl.timeMachineRun(id) : undefined;
  });
  protected readonly data = computed(() => valueOf(this.run) ?? null);

  private readonly jobs = httpResource<Job[]>(() => (this.jobId() ? apiUrl.jobs() : undefined));
  protected readonly running = computed(() => {
    const id = this.jobId();
    if (!id) return false;
    const j = (valueOf(this.jobs) ?? []).find((x) => x.id === id);
    return !j || j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING';
  });

  constructor() {
    effect((onCleanup) => {
      if (!this.running()) return;
      const t = setInterval(() => this.jobs.reload(), 3000);
      onCleanup(() => clearInterval(t));
    });
    effect(() => {
      const id = this.jobId();
      const j = (valueOf(this.jobs) ?? []).find((x) => x.id === id);
      if (!id || !j || j.status === 'RUNNING' || j.status === 'QUEUED' || j.status === 'PENDING') return;
      const lines = (j.log ?? '').trim().split('\n');
      this.noticeError.set(j.status === 'FAILED');
      this.notice.set(j.status === 'FAILED' ? lines[lines.length - 1] : '');
      this.jobId.set(null);
      if (j.status !== 'FAILED') {
        this.selectedId.set(null);
        this.chartId.set(null);
        this.runs.reload();
      }
    });
  }

  protected start(): void {
    const d = this.asOf();
    if (!d) return;
    this.noticeError.set(false);
    this.notice.set(`Going back to ${d}…`);
    this.api.timeMachine(d).subscribe({
      next: (job) => {
        this.jobId.set(job.id);
        this.jobs.reload();
      },
      error: (e) => {
        this.noticeError.set(true);
        this.notice.set(errorMessage(e));
      },
    });
  }

  protected readonly sum = computed(() => this.data()?.result.summary[this.horizon()] ?? null);

  protected aiTone(s: { ai?: { picks: string[]; picksReturn?: number; universeReturn: number } }): string {
    const a = s.ai;
    if (!a || !a.picks.length || a.picksReturn === undefined) return 'tone-neutral';
    return a.picksReturn > a.universeReturn ? 'tone-good' : 'tone-bad';
  }

  protected readonly rows = computed(() => {
    const h = this.horizon();
    return (this.data()?.result.stocks ?? []).map((s) => {
      const p = s.odds?.[h]?.AUGMENTED;
      const a = s.actual?.[h] ?? null;
      const g = s.range?.[h];
      return {
        s,
        p,
        a,
        g,
        right: !!a && p !== undefined && p > 0.5 === a.beat,
        inside: !!a && !!g && g.q10 <= a.stockReturn && a.stockReturn <= g.q90,
      };
    });
  });

  protected readonly chartStock = computed<TmStock | null>(() => {
    const stocks = this.data()?.result.stocks ?? [];
    const id = this.chartId();
    return stocks.find((s) => s.companyId === id) ?? stocks.find((s) => s.ai?.action === 'ENTER') ?? stocks[0] ?? null;
  });

  protected readonly chart = computed<LineSeries[]>(() => {
    const s = this.chartStock();
    const r = this.data();
    if (!s || !r) return [];
    const out: LineSeries[] = [
      { key: 'stock', label: `${s.symbol} actual`, color: 'var(--series-1)', points: s.path.map((p) => ({ x: toX(p.date), y: p.stock })) },
    ];
    if (s.path.some((p) => p.benchmark !== null)) {
      out.push({
        key: 'bench',
        label: `${s.benchmarkSymbol} (sector ETF)`,
        color: 'var(--series-3)',
        points: s.path.filter((p) => p.benchmark !== null).map((p) => ({ x: toX(p.date), y: p.benchmark as number })),
      });
    }
    const band: LinePoint[] = s.path.length ? [{ x: toX(s.path[0].date), y: 0, lo: 0, hi: 0 }] : [];
    for (const h of r.result.horizons) {
      const g = s.range?.[String(h)];
      const end = s.actual?.[String(h)]?.endDate ?? s.path[h]?.date;
      if (g && end) band.push({ x: toX(end), y: g.q50, lo: g.q10, hi: g.q90 });
    }
    if (band.length > 1) out.push({ key: 'forecast', label: 'Forecast median (10–90% band)', color: 'var(--series-2)', points: band });
    return out;
  });
}
