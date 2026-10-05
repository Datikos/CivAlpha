import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct, fmtSignedPct } from '../core/format';
import { Job, SetupFamily, SetupGrade, SetupRow, SetupStats, SetupStudyResponse, SetupStudyRun } from '../core/models';
import { createSort } from '../core/sort';
import { Icon } from '../shared/icon';
import { SortTh } from '../shared/sort-th';
import { UI } from '../shared/ui';
import { Tone, VIZ, verdictTone } from '../shared/viz';

export const FAMILY_LABEL: Record<SetupFamily, string> = {
  EARNINGS: 'Earnings',
  DIVIDEND: 'Dividend',
  TREND: 'Trend',
  REVERSAL: 'Reversal',
  VOLUME: 'Volume',
  EVENT: 'Policy event',
};

const GRADE: Record<SetupGrade, { label: string; tone: Tone; glyph: string }> = {
  SUPPORTED: { label: 'Supported', tone: 'good', glyph: '✓' },
  SUGGESTIVE: { label: 'Suggestive', tone: 'warn', glyph: '~' },
  NEGATIVE: { label: 'Trails the ETF', tone: 'bad', glyph: '✗' },
  NOISE: { label: 'Noise', tone: 'neutral', glyph: '•' },
  NOT_TESTABLE: { label: 'Too few', tone: 'neutral', glyph: '?' },
};

const GRADE_RANK: Record<SetupGrade, number> = { SUPPORTED: 0, SUGGESTIVE: 1, NEGATIVE: 2, NOISE: 3, NOT_TESTABLE: 4 };

@Component({
  selector: 'app-playbook',
  imports: [RouterLink, Icon, SortTh, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="zap" area="strategy" />
        <div>
          <h1>Setup playbook</h1>
          <p class="muted">
            The situations a trader waits for, each scored on what followed: an earnings beat becoming public, a dividend
            raise, a new 52-week high, a golden cross, an oversold pullback, a crash, a volume surge, a tariff or rate
            shock. Every number is the excess return over the sector ETF from the next close, next to the base rate of
            all stock-days.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-playbook" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a class="btn" routerLink="/strategies">Strategy lab →</a>
      </div>
    </div>

    <div class="card run-card">
      <div class="inline-form">
        <button type="button" class="btn btn-primary" [disabled]="running()" (click)="start()">
          {{ running() ? 'Running…' : 'Run the study' }}
        </button>
        @if (runList().length > 1) {
          <label class="field">
            Earlier runs
            <select (change)="selectedId.set(+$any($event.target).value)">
              @for (r of runList(); track r.id) {
                <option [value]="r.id" [selected]="r.id === currentId()">data to {{ r.dataCutoff }} (run {{ r.runAt | utc }})</option>
              }
            </select>
          </label>
        }
      </div>
      <p class="small muted">
        Takes a few seconds on the stored prices, filings, dividends and events; it also runs at the end of every pipeline
        run. Every trigger uses only data known at the close; the outcomes read what followed.
      </p>
      @if (notice()) {
        <div class="alert" [class.alert-error]="noticeError()" role="status">{{ notice() }}</div>
      }
    </div>

    <app-status [res]="res" what="setup playbook" />

    @if (res.hasValue()) {
      @if (data(); as r) {
        <app-verdict [tone]="headlineTone()">{{ r.result.headline }}</app-verdict>
        <p class="small muted">
          Study #{{ r.id }} run {{ r.runAt | utc }} · {{ r.result.start }} to {{ r.result.dataCutoff }} ·
          {{ r.result.universeSize }} stocks · {{ r.result.stockDays | num }} stock-days · {{ r.result.config.tests }} setup-horizon pairs tested
        </p>

        <div class="seg horizon" role="group" aria-label="Horizon">
          @for (h of r.result.config.horizons; track h) {
            <button type="button" [class.on]="h + '' === horizon()" (click)="horizon.set(h + '')" [attr.aria-pressed]="h + '' === horizon()">
              {{ h }} trading days
            </button>
          }
        </div>

        @if (base(); as b) {
          <div class="stats wide">
            <div class="stat tone-neutral">
              <div class="stat-label">Base rate <app-help text="Over every tracked stock-day, how often the stock beat its sector ETF over the horizon, and the mean excess. The chance level every setup has to beat." topic="base-rate" label="base rate" /></div>
              <div class="stat-value">{{ b.hitRate | pct: 0 }}</div>
              <div class="stat-sub">beat the ETF · mean excess <app-delta [value]="b.meanExcess" kind="pct" [digits]="2" /> · {{ b.n | num }} stock-days</div>
            </div>
            <div class="stat" [class]="'stat ' + (counts().SUPPORTED ? 'tone-good' : 'tone-warn')">
              <div class="stat-label">Supported <app-help text="Setups whose mean excess clears the bar corrected for every setup-horizon pair tested on this history. Only these count as a base rate worth acting on." topic="page-playbook" label="supported" /></div>
              <div class="stat-value">{{ counts().SUPPORTED }}<span class="unit">of {{ testable() }} testable</span></div>
              <div class="stat-sub">{{ counts().SUGGESTIVE }} suggestive · {{ counts().NEGATIVE }} trail the ETF · bar z ≥ {{ r.result.config.bonferroniZ | fixed: 1 }}</div>
            </div>
            <div class="stat tone-forecast">
              <div class="stat-label">Best setup at {{ horizon() }} days</div>
              @if (best(); as x) {
                <div class="stat-value">{{ x.stats.hitRate | pct: 0 }}<span class="unit">beat the ETF</span></div>
                <div class="stat-sub">{{ x.row.name }} · fired {{ x.stats.n | num }}× · mean <app-delta [value]="x.stats.meanExcess" kind="pct" [digits]="1" /> · payoff {{ x.stats.payoff | fixed: 2 }}</div>
              } @else {
                <div class="stat-value">—</div>
                <div class="stat-sub">no setup fired often enough</div>
              }
            </div>
            <div class="stat tone-info">
              <div class="stat-label">Firing now <app-help text="Tracked stocks on which at least one setup fired in the last five sessions. A scan of the playbook, not a signal: check the setup's grade." topic="setup" label="firing now" /></div>
              <div class="stat-value">{{ r.result.today.stockCount }}<span class="unit">stocks</span></div>
              <div class="stat-sub">in the last {{ r.result.today.freshDays }} sessions to {{ r.result.today.asOfDate }}</div>
            </div>
          </div>
        }

        <div class="card">
          <div class="card-head">
            <h3>Setups at {{ horizon() }} trading days, bought at the next close</h3>
            <span class="small muted">click a header to sort</span>
          </div>
          <div class="table-wrap">
            <table class="table compact lens">
              <thead>
                <tr>
                  <th sortKey="name" [sort]="sort" defaultDir="asc">Setup</th>
                  <th class="num" sortKey="n" [sort]="sort">Fired <app-help text="How many times the setup fired on a tracked stock, and how many of those firings have a known outcome. Under the minimum, the row is not judged." topic="setup" label="triggers" /></th>
                  <th class="num" sortKey="hitRate" [sort]="sort">Beat the ETF <app-help text="Share of firings after which the stock beat its sector ETF over the horizon, against the base rate tick." topic="hit-rate" label="hit rate" /></th>
                  <th class="num" sortKey="meanExcess" [sort]="sort">Mean excess <app-help text="Mean excess return over the ETF per firing with a 95% block-bootstrap interval drawn against zero." topic="confidence-interval" label="mean excess" /></th>
                  <th class="num" sortKey="payoff" [sort]="sort">Payoff <app-help text="Average win divided by the average loss. Above 1, wins are bigger than losses." topic="payoff" label="payoff" /></th>
                  <th class="num" sortKey="z" [sort]="sort">z <app-help text="Mean excess divided by its bootstrap standard error. The grade needs z above the corrected bar for every pair tested." topic="t-stat" label="z-score" /></th>
                  <th sortKey="grade" [sort]="sort" defaultDir="asc">Grade</th>
                </tr>
              </thead>
              <tbody>
                @for (x of rows(); track x.row.key) {
                  <tr [class.row-current]="x.stats.grade === 'SUPPORTED'">
                    <td>
                      <strong>{{ x.row.name }}</strong>
                      <div class="small muted">{{ familyLabel[x.row.family] }} · {{ x.row.trigger }}</div>
                    </td>
                    <td class="num">
                      {{ x.row.triggers | num }}
                      @if (x.stats.n !== x.row.triggers) {
                        <div class="small muted" [title]="'Firings whose outcome window has closed; the rest are too recent'">{{ x.stats.n | num }} scored</div>
                      }
                    </td>
                    <td class="num">
                      @if (x.stats.hitRate !== null) {
                        {{ x.stats.hitRate | pct: 0 }}
                        <app-meter [value]="x.stats.hitRate" [min]="0.3" [max]="0.7" [target]="base()?.hitRate ?? 0.5" targetLabel="base rate" label="Hit rate vs base rate" />
                      } @else { <span class="muted">—</span> }
                    </td>
                    <td class="num net">
                      @if (x.stats.meanExcess !== null) {
                        <app-delta [value]="x.stats.meanExcess" kind="pct" [digits]="1" />
                        <div class="ci-line">
                          <app-range-bar [lo]="x.stats.ciLow" [hi]="x.stats.ciHigh" [point]="x.stats.meanExcess" [span]="span()" [label]="'Mean excess ' + fmtPctOf(x.stats.meanExcess) + ', interval ' + fmtPctOf(x.stats.ciLow) + ' to ' + fmtPctOf(x.stats.ciHigh)" />
                          <span class="small muted">{{ x.stats.ciLow | signedPct: 1 }} to {{ x.stats.ciHigh | signedPct: 1 }}</span>
                        </div>
                      } @else { <span class="muted">—</span> }
                    </td>
                    <td class="num">{{ x.stats.payoff === null ? '—' : (x.stats.payoff | fixed: 2) + '×' }}</td>
                    <td class="num">{{ x.stats.z === null ? '—' : (x.stats.z | signed: 1) }}</td>
                    <td>
                      <span class="badge" [class]="'badge tone-' + grade[x.stats.grade].tone" [title]="x.stats.verdict">{{ grade[x.stats.grade].glyph }} {{ grade[x.stats.grade].label }}</span>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted" style="margin-top: 0.5rem">
            Supported rows are highlighted. The meter in "Beat the ETF" is drawn against the base rate tick; the bar in
            "Mean excess" is the 95% interval against the zero line (green when wholly above it). Hover a grade for the
            full verdict.
          </p>
        </div>

        <div class="grid-2">
          <div class="card">
            <h3>Firing now ({{ r.result.today.asOfDate }}, last {{ r.result.today.freshDays }} sessions)</h3>
            @if (firing().length) {
              <div class="table-wrap">
                <table class="table compact">
                  <thead><tr><th>Setup</th><th>Grade at {{ horizon() }}d</th><th>Stocks</th></tr></thead>
                  <tbody>
                    @for (t of firing(); track t.key) {
                      <tr>
                        <td><strong>{{ t.name }}</strong><div class="small muted">{{ familyLabel[t.family] }}</div></td>
                        <td><span class="badge" [class]="'badge tone-' + grade[t.grade].tone">{{ grade[t.grade].glyph }} {{ grade[t.grade].label }}</span></td>
                        <td>
                          <span class="chips">
                            @for (s of t.stocks; track s.companyId) {
                              <a class="chip" [routerLink]="['/companies', s.symbol]" [title]="s.name + ' · fired ' + s.date">{{ s.symbol }}@if (s.daysAgo) { <span class="muted">·{{ s.daysAgo }}d</span> }</a>
                            }
                          </span>
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              <p class="small muted" style="margin-top: 0.5rem">"·2d" means the setup fired two sessions before the latest close. A scan, not advice.</p>
            } @else {
              <p class="muted">No setup fired on a tracked stock in the last {{ r.result.today.freshDays }} sessions.</p>
            }
          </div>

          <div class="card">
            <h3>How to read this</h3>
            <ul class="small notes">
              @for (d of r.result.disclaimers; track d) {
                <li>{{ d }}</li>
              }
              <li>A setup with a 45% hit rate and a payoff of 2 earns more than one with a 60% hit rate and a payoff of 0.5: read the two columns together.</li>
              <li>The same ideas trade as rules in the <a routerLink="/strategies">Strategy lab</a> (post-earnings drift, golden cross, RSI pullback, event avoidance) against costs and buy &amp; hold.</li>
            </ul>
          </div>
        </div>
      } @else {
        <div class="empty-box">No setup playbook yet. Click <strong>Run the study</strong> above (it also runs with every pipeline run).</div>
      }
    }
  `,
  styles: `
    .run-card { margin-bottom: 1rem; }
    .horizon { margin: 0.75rem 0 1rem; }
    .lens td.num { white-space: nowrap; }
    .lens td:first-child { min-width: 220px; }
    td .meter { display: block; width: auto; min-width: 56px; margin: 0.3rem 0 0; }
    td.net { min-width: 180px; }
    .ci-line { display: flex; justify-content: flex-end; align-items: center; gap: 0.4rem; margin-top: 0.2rem; }
    .chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .chip { text-decoration: none; }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
    .inline-form { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end; }
  `,
})
export class PlaybookPage {
  protected readonly api = inject(ApiService);
  protected readonly familyLabel = FAMILY_LABEL;
  protected readonly grade = GRADE;
  protected readonly horizon = signal('21');
  protected readonly selectedId = signal<number | null>(null);
  protected readonly jobId = signal<number | null>(null);
  protected readonly notice = signal('');
  protected readonly noticeError = signal(false);
  protected readonly sort = createSort('civalpha.sort.playbook', { key: 'z', dir: 'desc' });

  protected readonly res = httpResource<SetupStudyResponse>(() => apiUrl.setups());
  protected readonly runList = computed(() => valueOf(this.res)?.runs ?? []);
  protected readonly currentId = computed(() => this.selectedId() ?? this.runList()[0]?.id ?? null);
  private readonly older = httpResource<SetupStudyRun>(() => {
    const id = this.selectedId();
    return id ? apiUrl.setupRun(id) : undefined;
  });
  protected readonly data = computed<SetupStudyRun | null>(() => {
    const latest = valueOf(this.res)?.run ?? null;
    const id = this.selectedId();
    if (id && id !== latest?.id) return valueOf(this.older) ?? null;
    return latest;
  });
  protected readonly base = computed(() => this.data()?.result.base[this.horizon()] ?? null);
  protected readonly headlineTone = computed<Tone>(() => (this.counts().SUPPORTED ? 'good' : verdictTone(this.data()?.result.headline) === 'good' ? 'good' : 'warn'));

  /** Every setup with its stats at the chosen horizon. */
  private readonly all = computed(() => {
    const h = this.horizon();
    return (this.data()?.result.setups ?? []).map((row) => ({ row, stats: row.horizons[h] })).filter((x): x is { row: SetupRow; stats: SetupStats } => !!x.stats);
  });
  protected readonly rows = computed(() => {
    const getters: Record<string, (x: { row: SetupRow; stats: SetupStats }) => number | string | null | undefined> = {
      name: (x) => x.row.name,
      n: (x) => x.stats.n,
      hitRate: (x) => x.stats.hitRate,
      meanExcess: (x) => x.stats.meanExcess,
      payoff: (x) => x.stats.payoff,
      z: (x) => x.stats.z,
      grade: (x) => GRADE_RANK[x.stats.grade],
    };
    return this.sort.order(this.all(), getters);
  });
  protected readonly counts = computed(() => {
    const c: Record<SetupGrade, number> = { SUPPORTED: 0, SUGGESTIVE: 0, NEGATIVE: 0, NOISE: 0, NOT_TESTABLE: 0 };
    for (const x of this.all()) c[x.stats.grade]++;
    return c;
  });
  protected readonly testable = computed(() => this.all().filter((x) => x.stats.grade !== 'NOT_TESTABLE').length);
  protected readonly best = computed(() => {
    const ok = this.all().filter((x) => x.stats.grade !== 'NOT_TESTABLE' && x.stats.z !== null);
    if (!ok.length) return null;
    return ok.reduce((a, b) => ((b.stats.z ?? -Infinity) > (a.stats.z ?? -Infinity) ? b : a));
  });
  protected readonly span = computed(() => {
    let m = 0;
    for (const x of this.all()) {
      for (const v of [x.stats.ciLow, x.stats.ciHigh, x.stats.meanExcess]) if (typeof v === 'number') m = Math.max(m, Math.abs(v));
    }
    return m || 0.01;
  });
  /** Setups that fired in the last sessions, with their grade at the chosen horizon. */
  protected readonly firing = computed(() => {
    const d = this.data();
    if (!d) return [];
    const h = this.horizon();
    const byKey = new Map(d.result.setups.map((s) => [s.key, s]));
    return d.result.today.setups
      .filter((t) => t.stocks.length)
      .map((t) => ({ ...t, grade: byKey.get(t.key)?.horizons[h]?.grade ?? ('NOT_TESTABLE' as SetupGrade) }))
      .sort((a, b) => GRADE_RANK[a.grade] - GRADE_RANK[b.grade]);
  });

  protected fmtPctOf(v: number | null): string {
    return v === null ? '—' : fmtSignedPct(v, 1);
  }

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
      const t = setInterval(() => this.jobs.reload(), 2000);
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
        this.res.reload();
      }
    });
  }

  protected start(): void {
    this.noticeError.set(false);
    this.notice.set('Running the study…');
    this.api.setupStudy().subscribe({
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
}
