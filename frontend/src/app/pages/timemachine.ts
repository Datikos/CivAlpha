import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { LinePoint, LineChart, LineSeries } from '../charts/line-chart';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtSignedPct } from '../core/format';
import { Job, TimeMachineRun, TimeMachineRunSummary, TmStock } from '../core/models';
import { UI } from '../shared/ui';

const DAY = 86_400_000;
const toX = (d: string) => Date.parse(d + 'T00:00:00Z');

function isoDaysAgo(n: number): string {
  return new Date(Date.now() - n * DAY).toISOString().slice(0, 10);
}

@Component({
  selector: 'app-time-machine',
  imports: [FormsModule, RouterLink, LineChart, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Time machine</h1>
        <p class="muted">
          Go back to a past close and forecast with only the data that existed then — the beat-the-sector odds, the AI's
          buy decisions and a 10–90% return band — then see what actually happened 5, 10, 21 and 63 trading days later.
        </p>
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
      <div class="verdict" role="note"><strong>As of {{ r.asOfDate }}.</strong> {{ r.headline }}</div>
      <p class="small muted">
        Run {{ r.runAt | utc }} with data through {{ r.dataCutoff }}
      </p>

      <div class="chips horizon" role="group" aria-label="Horizon">
        @for (h of r.result.horizons; track h) {
          <button type="button" class="chip" [class.active]="h + '' === horizon()" (click)="horizon.set(h + '')"
                  [attr.aria-pressed]="h + '' === horizon()">
            {{ h }} trading days
          </button>
        }
        @if (sum(); as s) {
          <span class="small muted">{{ s.resolved ? s.resolved + ' outcomes known (to ' + s.endDate + ')' : 'outcome not known yet' }}</span>
        }
      </div>

      @if (sum(); as s) {
        <div class="grid-3">
          <div class="card">
            <h3>Beat-the-sector odds</h3>
            @if (s.odds?.AUGMENTED; as o) {
              <p class="big">{{ o.hitRate | pct: 0 }} <span class="small muted">of {{ o.n }} calls right</span></p>
              <dl class="kv small">
                <dt>Brier</dt><dd>{{ o.brier | fixed: 3 }} <span class="muted">vs {{ o.brierBaseRate | fixed: 3 }} base rate (lower is better)</span></dd>
                <dt>AUC</dt><dd>{{ o.auc | fixed: 2 }}</dd>
                <dt>Top 5 − bottom 5</dt><dd>{{ o.topMinusBottomExcess | signedPct: 1 }} excess return</dd>
                @if (s.odds?.BASELINE; as b) {
                  <dt>Baseline model</dt><dd>{{ b.hitRate | pct: 0 }} right, Brier {{ b.brier | fixed: 3 }}</dd>
                }
              </dl>
            } @else {
              <p class="muted">Not known yet.</p>
            }
          </div>
          <div class="card">
            <h3>AI buy decisions</h3>
            @if (s.ai; as a) {
              @if (a.picks.length) {
                <p class="big">{{ a.picksReturn ?? null | signedPct: 1 }} <span class="small muted">picks vs {{ a.universeReturn | signedPct: 1 }} all stocks</span></p>
                <dl class="kv small">
                  <dt>Picked</dt><dd>{{ a.picks.join(', ') }}</dd>
                  <dt>Beat their sector</dt><dd>{{ a.picksBeatSector }} of {{ a.picks.length }}</dd>
                </dl>
              } @else {
                <p class="muted">The AI bought nothing that day (all stocks {{ a.universeReturn | signedPct: 1 }}).</p>
              }
              <p class="small muted">Bought at the next close, equal weight.</p>
            } @else {
              <p class="muted">Not known yet.</p>
            }
          </div>
          <div class="card">
            <h3>10–90% return band</h3>
            @if (s.range; as g) {
              <p class="big">{{ g.coverage | pct: 0 }} <span class="small muted">of actual returns inside (target 80%)</span></p>
              <dl class="kv small">
                <dt>Naive band</dt><dd>{{ g.naiveCoverage | pct: 0 }} inside, width {{ g.naiveWidth | pct: 1 }} vs model {{ g.avgWidth | pct: 1 }}</dd>
                <dt>Median miss</dt><dd>{{ g.medianAbsError | pct: 1 }} <span class="muted">vs {{ g.naiveMedianAbsError | pct: 1 }} naive</span></dd>
              </dl>
            } @else {
              <p class="muted">Not known yet.</p>
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
                      <span class="badge" [class.badge-ok]="x.s.ai.action === 'ENTER'">{{ x.s.ai.action === 'ENTER' ? 'Buy' : 'Out' }}</span>
                    }
                  </td>
                  <td class="num">{{ x.p ?? null | pct: 0 }}</td>
                  <td class="num">{{ x.a?.excess ?? null | signedPct: 1 }}</td>
                  <td>
                    @if (x.a && x.p !== undefined) {
                      <span [class.good]="x.right" [class.bad]="!x.right">{{ x.right ? '✓ right' : '✗ wrong' }}</span>
                    }
                  </td>
                  <td class="num">
                    @if (x.g) {
                      {{ x.g.q10 | signedPct: 1 }} to {{ x.g.q90 | signedPct: 1 }}
                    }
                  </td>
                  <td class="num">{{ x.a?.stockReturn ?? null | signedPct: 1 }}</td>
                  <td>
                    @if (x.a && x.g) {
                      <span [class.good]="x.inside" [class.bad]="!x.inside">{{ x.inside ? '✓' : '✗' }}</span>
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
    .horizon { margin: 0.75rem 0; align-items: center; }
    .horizon .chip { cursor: pointer; font: inherit; font-size: 0.85rem; padding: 0.15rem 0.7rem; }
    .horizon .chip.active { border-color: var(--ink); font-weight: 700; }
    .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }
    .big { font-size: 1.6rem; font-weight: 700; margin: 0.25rem 0 0.5rem; }
    .big .small { font-weight: 400; }
    .chart-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
    tr.selected td { background: var(--surface-2); }
    tbody tr { cursor: pointer; }
    .good { color: var(--good-ink); }
    .bad { color: var(--bad-ink); }
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
