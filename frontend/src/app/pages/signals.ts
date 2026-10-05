import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { Job, SignalGrade, SignalRow, SignalStudyResponse, SignalStudyRun } from '../core/models';
import { createSort } from '../core/sort';
import { Icon } from '../shared/icon';
import { SortTh } from '../shared/sort-th';
import { Sparkline } from '../shared/sparkline';
import { UI } from '../shared/ui';
import { Tone, VIZ } from '../shared/viz';

const GRADE: Record<SignalGrade, { label: string; tone: Tone; glyph: string }> = {
  INFORMATIVE: { label: 'Informative', tone: 'good', glyph: '✓' },
  SUGGESTIVE: { label: 'Suggestive', tone: 'warn', glyph: '~' },
  NOISE: { label: 'Noise', tone: 'neutral', glyph: '•' },
  NOT_TESTABLE: { label: 'Too few', tone: 'neutral', glyph: '?' },
};
const GRADE_RANK: Record<SignalGrade, number> = { INFORMATIVE: 0, SUGGESTIVE: 1, NOISE: 2, NOT_TESTABLE: 3 };
const KIND_LABEL: Record<string, string> = {
  PRICE: 'Price', FUNDAMENTAL: 'Filed fundamentals', EVENT_FEATURE: 'Policy events', MACRO: 'Macro', TECHNICAL: 'Technical',
  DIVIDEND: 'Dividends', INSIDER: 'Insiders', EARNINGS: 'Earnings', OTHER: 'Other',
};

@Component({
  selector: 'app-signals',
  imports: [RouterLink, Icon, SortTh, Sparkline, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="pulse" area="strategy" />
        <div>
          <h1>Signal health</h1>
          <p class="muted">
            Does each model input still carry information, and is it fading? For every feature the AI sees, the information
            coefficient: each day, the rank correlation across the tracked stocks between the feature and the next 21 days'
            excess return over the sector ETF, averaged per month. Edges decay as others find them; this page is how the platform notices.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-signals" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a class="btn" routerLink="/accuracy">Model accuracy →</a>
      </div>
    </div>

    <div class="card run-card">
      <div class="inline-form">
        <button type="button" class="btn btn-primary" [disabled]="running()" (click)="start()">{{ running() ? 'Running…' : 'Run the study' }}</button>
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
      <p class="small muted">Takes about a minute on the stored data; it also runs at the end of every pipeline run.</p>
      @if (notice()) {
        <div class="alert" [class.alert-error]="noticeError()" role="status">{{ notice() }}</div>
      }
    </div>

    <app-status [res]="res" what="signal health" />

    @if (res.hasValue()) {
      @if (data(); as r) {
        <app-verdict [tone]="counts().INFORMATIVE ? 'good' : 'warn'">{{ r.result.headline }}</app-verdict>
        <p class="small muted">
          Study #{{ r.id }} run {{ r.runAt | utc }} · {{ r.result.start }} to {{ r.result.dataCutoff }} · {{ r.result.universeSize }} stocks ·
          {{ r.result.rows | num }} stock-days · {{ r.result.config.tests }} features tested · bar |t| ≥ {{ r.result.config.bonferroniZ | fixed: 1 }}
        </p>

        <div class="stats wide">
          <div class="stat" [class]="'stat ' + (counts().INFORMATIVE ? 'tone-good' : 'tone-warn')">
            <div class="stat-label">Informative <app-help text="Features whose mean monthly IC clears the bar corrected for every feature tested. These are the inputs the models can lean on." topic="ic" label="informative" /></div>
            <div class="stat-value">{{ counts().INFORMATIVE }}<span class="unit">of {{ testable() }} testable</span></div>
            <div class="stat-sub">{{ counts().SUGGESTIVE }} suggestive · {{ counts().NOISE }} noise</div>
          </div>
          <div class="stat" [class]="'stat ' + (decaying().length ? 'tone-bad' : 'tone-neutral')">
            <div class="stat-label">Decaying <app-help text="Features that carried information over the whole history but lost their sign in the last 12 months. The classic sign of an edge that others have found." topic="decay" label="decaying" /></div>
            <div class="stat-value">{{ decaying().length }}</div>
            <div class="stat-sub">{{ decaying().length ? decayingNames() : 'none lost its sign recently' }}</div>
          </div>
          <div class="stat tone-forecast">
            <div class="stat-label">Strongest input</div>
            @if (best(); as b) {
              <div class="stat-value">{{ b.meanIc | signed: 3 }}<span class="unit">mean IC</span></div>
              <div class="stat-sub">{{ b.label }} · t = {{ b.tStat | signed: 1 }} · right sign {{ b.signHitRate | pct: 0 }} of months</div>
            } @else {
              <div class="stat-value">—</div>
            }
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h3>Model inputs by information coefficient (21-day excess return)</h3>
            <span class="small muted">click a header to sort</span>
          </div>
          <div class="table-wrap">
            <table class="table compact lens">
              <thead>
                <tr>
                  <th sortKey="label" [sort]="sort" defaultDir="asc">Feature</th>
                  <th>Monthly IC <app-help text="One point per month: the average of that month's daily cross-sectional rank correlations between the feature and the forward excess return. Above the line, higher values went with outperformance." topic="ic" label="IC series" /></th>
                  <th class="num" sortKey="meanIc" [sort]="sort">Mean IC</th>
                  <th class="num" sortKey="icIr" [sort]="sort">IC IR <app-help text="Mean IC divided by its standard deviation across months: how steady the information is. Above 0.5 is strong for a single input." topic="ic" label="IC information ratio" /></th>
                  <th class="num" sortKey="tStat" [sort]="sort">t</th>
                  <th class="num" sortKey="signHitRate" [sort]="sort">Right sign</th>
                  <th class="num" sortKey="recentIc" [sort]="sort">Last 12m <app-help text="Mean IC over the last 12 months against the earlier months. A sign flip on an informative feature is flagged as decaying." topic="decay" label="recent IC" /></th>
                  <th sortKey="grade" [sort]="sort" defaultDir="asc">Grade</th>
                </tr>
              </thead>
              <tbody>
                @for (f of rows(); track f.feature) {
                  <tr [class.row-current]="f.grade === 'INFORMATIVE'">
                    <td>
                      <strong>{{ f.label }}</strong>
                      <div class="small muted">{{ kindLabel[f.kind] ?? f.kind }} · <span class="mono">{{ f.feature }}</span> · {{ f.months }} months</div>
                    </td>
                    <td><app-sparkline [points]="icPoints(f)" [width]="120" [height]="26" [tone]="f.meanIc !== null && f.meanIc < 0 ? 'bad' : 'good'" [label]="'Monthly IC of ' + f.label" /></td>
                    <td class="num"><app-delta [value]="f.meanIc" kind="fixed" [digits]="3" /></td>
                    <td class="num">{{ f.icIr === null ? '—' : (f.icIr | signed: 2) }}</td>
                    <td class="num">{{ f.tStat === null ? '—' : (f.tStat | signed: 1) }}</td>
                    <td class="num">
                      @if (f.signHitRate !== null) {
                        {{ f.signHitRate | pct: 0 }}
                        <app-meter [value]="f.signHitRate" [min]="0.3" [max]="0.9" [target]="0.5" targetLabel="coin flip" label="Share of months with the expected sign" />
                      } @else { — }
                    </td>
                    <td class="num">
                      @if (f.recentIc !== null) {
                        <app-delta [value]="f.recentIc" kind="fixed" [digits]="3" />
                        @if (f.earlierIc !== null) { <div class="small muted">earlier {{ f.earlierIc | signed: 3 }}</div> }
                      } @else { — }
                    </td>
                    <td>
                      <span class="badge" [class]="'badge tone-' + grade[f.grade].tone" [title]="f.verdict">{{ grade[f.grade].glyph }} {{ grade[f.grade].label }}</span>
                      @if (f.decaying) { <span class="badge tone-bad" title="Lost its sign in the last 12 months">▼ decaying</span> }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted" style="margin-top: 0.5rem">
            A negative IC is as useful as a positive one with the sign flipped; the grade judges the size, not the direction.
            Informative rows are highlighted. Hover a grade for the full verdict.
          </p>
        </div>

        <div class="card">
          <h3>How to read this</h3>
          <ul class="small notes">
            @for (d of r.result.disclaimers; track d) { <li>{{ d }}</li> }
            <li>The models already weight these inputs; this page says which weights rest on something and which on noise, and warns when a dependable input stops working.</li>
          </ul>
        </div>
      } @else {
        <div class="empty-box">No signal-health study yet. Click <strong>Run the study</strong> above (it also runs with every pipeline run).</div>
      }
    }
  `,
  styles: `
    .run-card { margin-bottom: 1rem; }
    .lens td.num { white-space: nowrap; }
    .lens td:first-child { min-width: 240px; }
    td .meter { display: block; width: auto; min-width: 56px; margin: 0.3rem 0 0; }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
    .inline-form { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end; }
  `,
})
export class SignalsPage {
  protected readonly api = inject(ApiService);
  protected readonly grade = GRADE;
  protected readonly kindLabel = KIND_LABEL;
  protected readonly selectedId = signal<number | null>(null);
  protected readonly jobId = signal<number | null>(null);
  protected readonly notice = signal('');
  protected readonly noticeError = signal(false);
  protected readonly sort = createSort('civalpha.sort.signals', { key: 'grade', dir: 'asc' });

  protected readonly res = httpResource<SignalStudyResponse>(() => apiUrl.signals());
  protected readonly runList = computed(() => valueOf(this.res)?.runs ?? []);
  protected readonly currentId = computed(() => this.selectedId() ?? this.runList()[0]?.id ?? null);
  private readonly older = httpResource<SignalStudyRun>(() => {
    const id = this.selectedId();
    return id ? apiUrl.signalRun(id) : undefined;
  });
  protected readonly data = computed<SignalStudyRun | null>(() => {
    const latest = valueOf(this.res)?.run ?? null;
    const id = this.selectedId();
    if (id && id !== latest?.id) return valueOf(this.older) ?? null;
    return latest;
  });
  private readonly all = computed(() => this.data()?.result.features ?? []);
  protected readonly rows = computed(() => {
    const getters: Record<string, (f: SignalRow) => number | string | null | undefined> = {
      label: (f) => f.label,
      meanIc: (f) => (f.meanIc === null ? null : Math.abs(f.meanIc)),
      icIr: (f) => (f.icIr === null ? null : Math.abs(f.icIr)),
      tStat: (f) => (f.tStat === null ? null : Math.abs(f.tStat)),
      signHitRate: (f) => f.signHitRate,
      recentIc: (f) => f.recentIc,
      grade: (f) => GRADE_RANK[f.grade] * 1000 - (f.tStat === null ? 0 : Math.abs(f.tStat)),
    };
    return this.sort.order(this.all(), getters);
  });
  protected readonly counts = computed(() => {
    const c: Record<SignalGrade, number> = { INFORMATIVE: 0, SUGGESTIVE: 0, NOISE: 0, NOT_TESTABLE: 0 };
    for (const f of this.all()) c[f.grade]++;
    return c;
  });
  protected readonly testable = computed(() => this.all().filter((f) => f.grade !== 'NOT_TESTABLE').length);
  protected readonly decaying = computed(() => this.all().filter((f) => f.decaying));
  protected readonly decayingNames = computed(() => this.decaying().map((f) => f.label).join(', '));
  protected readonly best = computed(() => {
    const ok = this.all().filter((f) => f.tStat !== null);
    if (!ok.length) return null;
    return ok.reduce((a, b) => (Math.abs(b.tStat ?? 0) > Math.abs(a.tStat ?? 0) ? b : a));
  });

  protected icPoints(f: SignalRow): number[] {
    return f.series.map((s) => s.ic);
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
    this.api.signalStudy().subscribe({
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
