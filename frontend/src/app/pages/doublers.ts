import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct, fmtSignedPct } from '../core/format';
import {
  DoublerCondition,
  DoublerHorizon,
  DoublerRates,
  DoublerStudyResponse,
  DoublerStudyRun,
  Job,
} from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ, verdictTone } from '../shared/viz';

const CONDITIONS: { key: DoublerCondition; label: string }[] = [
  { key: 'volatile', label: 'Volatile' },
  { key: 'small', label: 'Small & cheap' },
  { key: 'breakout', label: 'Breakout' },
  { key: 'volumeSpike', label: 'Volume spike' },
];

const SHOWN_FEATURES: { key: string; label: string; fmt: (v: number) => string }[] = [
  { key: 'price', label: 'Close', fmt: (v) => `$${v.toFixed(2)}` },
  { key: 'market_cap', label: 'Market cap', fmt: (v) => (v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`) },
  { key: 'vol_60', label: '60d vol', fmt: (v) => fmtPct(v, 0) },
  { key: 'vol_rank', label: 'Vol rank', fmt: (v) => v.toFixed(2) },
  { key: 'breakout_252', label: 'vs 252d high', fmt: (v) => fmtSignedPct(v, 1) },
  { key: 'volume_ratio', label: 'Volume ×', fmt: (v) => `${v.toFixed(1)}×` },
];

@Component({
  selector: 'app-doublers',
  imports: [RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="zap" area="strategy" />
        <div>
          <h1>Doubler study</h1>
          <p class="muted">
            How often a tracked stock gained 100% or more within 21, 42 or 63 trading days, what those stock-days looked like
            beforehand, and whether a screen for that profile finds them more often than chance. Every rate is a count of
            history, shown next to how often the same stock-days lost half.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-doublers" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
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
        Takes a few seconds on the stored prices and filings; it also runs at the end of every pipeline run. The screen
        and every feature use only data known at each close; the outcomes read what followed.
      </p>
      @if (notice()) {
        <div class="alert" [class.alert-error]="noticeError()" role="status">{{ notice() }}</div>
      }
    </div>

    <app-status [res]="res" what="doubler study" />

    @if (res.hasValue()) {
      @if (data(); as r) {
        <app-verdict [tone]="verdictToneOf(r.result.headline)">{{ r.result.headline }}</app-verdict>
        <p class="small muted">
          Study #{{ r.id }} run {{ r.runAt | utc }} · {{ r.result.start }} to {{ r.result.dataCutoff }} ·
          {{ r.result.universeSize }} stocks · {{ r.result.stockDays | num }} stock-days
        </p>

        <div class="seg horizon" role="group" aria-label="Horizon">
          @for (h of r.result.config.horizons; track h) {
            <button type="button" [class.on]="h + '' === horizon()" (click)="horizon.set(h + '')" [attr.aria-pressed]="h + '' === horizon()">
              {{ h }} trading days
            </button>
          }
        </div>

        @if (hz(); as h) {
          <div class="stats">
            <div class="stat tone-neutral">
              <div class="stat-label">Base rate <app-help text="Share of all stock-days on which the stock went on to double within the horizon. This is the chance level the screen has to beat." topic="base-rate" label="base rate" /></div>
              <div class="stat-value">{{ h.base.hitRate | pct: 2 }}</div>
              <div class="stat-sub">of {{ h.base.n | num }} stock-days doubled · {{ h.episodes.length }} episodes, {{ h.companiesWithHits.length }} companies</div>
            </div>
            <div class="stat" [class]="'stat ' + (supported(h) ? 'tone-good' : 'tone-warn')">
              <div class="stat-label">When the screen fired <app-help text="Share of the screen's stock-days that doubled, with a 95% bootstrap interval. It counts only when the whole interval sits above the base rate." topic="confidence-interval" label="screen hit rate" /></div>
              <div class="stat-value">{{ h.screen.hitRate | pct: 1 }}</div>
              <div class="stat-sub">of {{ h.screen.n | num }} stock-days doubled{{ ci(h.screen) }}</div>
              <app-meter [value]="h.screen.hitRate" [max]="meterMax(h)" [target]="h.base.hitRate" targetLabel="base rate" [tone]="supported(h) ? 'good' : 'warn'" label="Screen hit rate vs base rate" />
            </div>
            <div class="stat" [class]="'stat ' + (supported(h) ? 'tone-good' : 'tone-warn')">
              <div class="stat-label">Lift over chance <app-help text="Screen hit rate divided by the base rate. 1× is chance; 6× means the screen finds doublers six times as often as a random stock-day." topic="lift" label="lift" /></div>
              <div class="stat-value">{{ h.lift === null ? '—' : (h.lift | fixed: 1) + '×' }}</div>
              <div class="stat-sub">{{ supported(h) ? '✓ interval above the base rate' : '≈ not distinguishable from chance' }}</div>
            </div>
            <div class="stat tone-bad">
              <div class="stat-label">Lost half or more <app-help text="Share of the screen's stock-days on which the stock fell 50% or more at some close within the horizon. The flip side of hunting doublers." topic="page-doublers" label="loss rate" /></div>
              <div class="stat-value">{{ h.screen.lossRate | pct: 0 }}</div>
              <div class="stat-sub">of the screen's stock-days (all stock-days: {{ h.base.lossRate | pct: 1 }})</div>
              <app-meter [value]="h.screen.lossRate" [max]="meterMax(h)" [target]="h.base.lossRate" targetLabel="all stock-days" tone="bad" label="Loss rate vs all stock-days" />
            </div>
            <div class="stat" [class]="'stat tone-' + signTone(h.screen.medianEndReturn)">
              <div class="stat-label">Median outcome</div>
              <div class="stat-value"><app-delta [value]="h.screen.medianEndReturn" kind="pct" [digits]="0" /></div>
              <div class="stat-sub">at the end of the window on the screen's stock-days</div>
            </div>
          </div>

          <div class="card">
            <h3>Screen vs chance ({{ h.horizon }} trading days, bought at the next close)</h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Stock-days</th>
                    <th class="num">N</th>
                    <th class="num" title="best close in the window ≥ 2× the entry">Doubled</th>
                    <th class="num" title="worst close in the window ≤ half the entry">Lost half</th>
                    <th class="num">Median end</th>
                    <th class="num">10th–90th pct end</th>
                    <th class="num">Median best</th>
                  </tr>
                </thead>
                <tbody>
                  @for (row of rateRows(h); track row.label) {
                    <tr [class.ref-row]="row.ref">
                      <td>{{ row.label }}</td>
                      <td class="num">{{ row.r.n | num }}</td>
                      <td class="num">
                        <app-cell-bar [value]="row.r.hitRate" [max]="rateMax(h)" [text]="row.r.hitRate | pct: 2" tone="good" />
                        <div class="small muted">{{ ci(row.r) }}</div>
                      </td>
                      <td class="num"><app-cell-bar [value]="row.r.lossRate" [max]="rateMax(h)" [text]="row.r.lossRate | pct: 1" tone="bad" /></td>
                      <td class="num"><app-delta [value]="row.r.medianEndReturn" kind="pct" [digits]="1" /></td>
                      <td class="num">{{ row.r.p10EndReturn ?? null | signedPct: 0 }} to {{ row.r.p90EndReturn ?? null | signedPct: 0 }}</td>
                      <td class="num"><app-delta [value]="row.r.medianMaxReturn" kind="pct" [digits]="1" /></td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem">
              <strong>Screen:</strong> {{ r.result.screenRule.volatile }}; {{ r.result.screenRule.small }}; trigger:
              {{ r.result.screenRule.trigger }}. <strong>Control:</strong> the same volatile, small stock-days without a
              trigger. Intervals are 95% bootstrap bands over blocks of 21 dates. {{ h.verdict }}.
            </p>
          </div>

          <div class="grid-2">
            <div class="card">
              <h3>Today's screen ({{ r.result.today.asOfDate }})</h3>
              @if (flagged().length) {
                <p class="small"><span class="badge tone-good">✓ {{ flagged().length }} stock(s)</span> meet every condition at the latest close.</p>
              } @else {
                <p class="small muted">No tracked stock meets every condition at the latest close.</p>
              }
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr>
                      <th>Stock</th>
                      @for (c of conditions; track c.key) {
                        <th class="num">{{ c.label }}</th>
                      }
                      @for (f of features; track f.key) {
                        <th class="num">{{ f.label }}</th>
                      }
                    </tr>
                  </thead>
                  <tbody>
                    @for (s of shownToday(); track s.companyId) {
                      <tr [class.fires]="s.fires">
                        <td>
                          <a [routerLink]="['/companies', s.symbol]">{{ s.symbol }}</a>
                          @if (s.fires) { <span class="badge tone-good">screen on</span> }
                        </td>
                        @for (c of conditions; track c.key) {
                          <td class="num cond" [class.on]="s.conditions[c.key]">{{ s.conditions[c.key] ? '✓' : '·' }}</td>
                        }
                        @for (f of features; track f.key) {
                          <td class="num">{{ feat(s.features[f.key], f.fmt) }}</td>
                        }
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              @if (r.result.today.stocks.length > shownToday().length || showAll()) {
                <button type="button" class="btn btn-link small" (click)="showAll.set(!showAll())">
                  {{ showAll() ? 'Show fewer' : 'Show all ' + r.result.today.stocks.length + ' stocks' }}
                </button>
              }
            </div>

            <div class="card">
              <h3>By year</h3>
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr><th>Year</th><th class="num">Stock-days</th><th class="num">Doubled</th><th class="num">Rate</th><th class="num">Lost half</th></tr>
                  </thead>
                  <tbody>
                    @for (y of h.byYear; track y.year) {
                      <tr>
                        <td>{{ y.year }}</td>
                        <td class="num">{{ y.n | num }}</td>
                        <td class="num">{{ y.hits | num }}</td>
                        <td class="num"><app-cell-bar [value]="y.hitRate" [max]="yearMax(h)" [text]="y.hitRate | pct: 2" tone="good" /></td>
                        <td class="num"><app-cell-bar [value]="y.lossRate" [max]="yearMax(h)" [text]="y.lossRate | pct: 2" tone="bad" /></td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              <p class="small muted" style="margin-top: 0.5rem">The rate swings with the market regime: judge the screen across years, not on one.</p>
            </div>
          </div>

          <div class="card">
            <h3>Episodes: every move of +100% within {{ h.horizon }} trading days</h3>
            @if (h.episodes.length) {
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr>
                      <th>Stock</th>
                      <th>First signal</th>
                      <th>Bought at close of</th>
                      <th class="num" title="trading days after the entry until the close was 2x">Days to 2×</th>
                      <th class="num">Best close</th>
                      <th class="num">End of window</th>
                      <th class="num">Worst close</th>
                      <th class="num" title="how many stock-days in this move were hits">Signal days</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (e of episodes(h); track e.symbol + e.signalDate) {
                      <tr>
                        <td><a [routerLink]="['/companies', e.symbol]">{{ e.symbol }}</a></td>
                        <td>{{ e.signalDate }}</td>
                        <td>{{ e.entryDate }}</td>
                        <td class="num">{{ e.daysToDouble ?? '—' }}</td>
                        <td class="num"><app-delta [value]="e.maxReturn" kind="pct" [digits]="0" /></td>
                        <td class="num"><app-delta [value]="e.endReturn" kind="pct" [digits]="0" /></td>
                        <td class="num"><app-delta [value]="e.maxDrawdown" kind="pct" [digits]="0" /></td>
                        <td class="num">{{ e.signalDays }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              @if (h.episodes.length > EPISODES_SHOWN) {
                <button type="button" class="btn btn-link small" (click)="allEpisodes.set(!allEpisodes())">
                  {{ allEpisodes() ? 'Show latest ' + EPISODES_SHOWN : 'Show all ' + h.episodes.length }}
                </button>
              }
            } @else {
              <p class="muted">No tracked stock doubled within {{ h.horizon }} trading days in this history.</p>
            }
          </div>

          <div class="card">
            <h3>What the doubling stock-days looked like beforehand</h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Feature (known at the close)</th>
                    <th class="num">Median, all</th>
                    <th class="num">Median, doublers</th>
                    <th class="num" title="share of stock-days in each quintile of the feature that doubled">Q1 (low)</th>
                    <th class="num">Q2</th><th class="num">Q3</th><th class="num">Q4</th><th class="num">Q5 (high)</th>
                  </tr>
                </thead>
                <tbody>
                  @for (f of h.profile; track f.feature) {
                    <tr>
                      <td>{{ f.label }} <span class="small muted">(n={{ f.n | num }})</span></td>
                      <td class="num">{{ profileValue(f.feature, f.medianAll) }}</td>
                      <td class="num">{{ profileValue(f.feature, f.medianHits) }}</td>
                      @for (q of quintiles(f); track q.quintile) {
                        <td class="num heat tone-good" [style.--h]="q.heat" [style.font-weight]="q.best ? 700 : 400">{{ q.hitRate | pct: 1 }}{{ q.best ? ' ★' : '' }}</td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem">
              Q1–Q5: hit rate by quintile of the feature over all stock-days (Q5 highest). Greener cells doubled more
              often; ★ marks the quintile with the most doublers. This is where the screen's thresholds come from, and
              where to revise them.
            </p>
          </div>

          <div class="card">
            <h3>How to read this</h3>
            <ul class="small notes">
              @for (d of r.result.disclaimers; track d) {
                <li>{{ d }}</li>
              }
              <li>The same screen trades in the <a routerLink="/strategies/DOUBLER_SCREEN">strategy lab</a> as
                DOUBLER_SCREEN (hold 63 days, 50% stop) against costs and the buy-and-hold reference.</li>
            </ul>
          </div>
        }
      } @else {
        <div class="empty-box">No doubler study yet. Click <strong>Run the study</strong> above (it also runs with every pipeline run).</div>
      }
    }
  `,
  styles: `
    .run-card { margin-bottom: 1rem; }
    .horizon { margin: 0.75rem 0 1rem; }
    .ref-row td { background: var(--surface-2); }
    tr.fires td { background: color-mix(in srgb, light-dark(#1b9c5a, #3fcf7f) 10%, transparent); }
    td.cond { color: var(--ink-muted); }
    td.cond.on { color: var(--good-ink); font-weight: 700; }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
    .btn-link { background: none; border: none; padding: 0.4rem 0; color: var(--accent); cursor: pointer; }
    .inline-form { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end; }
  `,
})
export class DoublersPage {
  protected readonly api = inject(ApiService);
  protected readonly conditions = CONDITIONS;
  protected readonly features = SHOWN_FEATURES;
  protected readonly EPISODES_SHOWN = 25;
  protected readonly verdictToneOf = verdictTone;

  protected readonly horizon = signal('63');
  protected readonly selectedId = signal<number | null>(null);
  protected readonly jobId = signal<number | null>(null);
  protected readonly notice = signal('');
  protected readonly noticeError = signal(false);
  protected readonly showAll = signal(false);
  protected readonly allEpisodes = signal(false);

  protected readonly res = httpResource<DoublerStudyResponse>(() => apiUrl.doublers());
  protected readonly runList = computed(() => valueOf(this.res)?.runs ?? []);
  protected readonly currentId = computed(() => this.selectedId() ?? this.runList()[0]?.id ?? null);
  private readonly older = httpResource<DoublerStudyRun>(() => {
    const id = this.selectedId();
    return id ? apiUrl.doublerRun(id) : undefined;
  });
  protected readonly data = computed<DoublerStudyRun | null>(() => {
    const latest = valueOf(this.res)?.run ?? null;
    const id = this.selectedId();
    if (id && id !== latest?.id) return valueOf(this.older) ?? null;
    return latest;
  });
  protected readonly hz = computed<DoublerHorizon | null>(() => this.data()?.result.horizons[this.horizon()] ?? null);
  protected readonly flagged = computed(() => (this.data()?.result.today.stocks ?? []).filter((s) => s.fires));
  protected readonly shownToday = computed(() => {
    const all = this.data()?.result.today.stocks ?? [];
    if (this.showAll()) return all;
    const on = all.filter((s) => s.fires);
    return on.length ? on : all.slice(0, 8);
  });

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
    this.api.doublerStudy().subscribe({
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

  protected supported(h: DoublerHorizon): boolean {
    return h.verdict.includes('SUPPORTED by');
  }

  protected signTone(v: number | null | undefined): string {
    return typeof v !== 'number' || v === 0 ? 'neutral' : v > 0 ? 'good' : 'bad';
  }

  protected meterMax(h: DoublerHorizon): number {
    return Math.max(h.screen.hitRate ?? 0, h.screen.lossRate ?? 0, h.control.hitRate ?? 0, h.base.lossRate ?? 0, 0.01) * 1.25;
  }

  protected rateMax(h: DoublerHorizon): number {
    return Math.max(...this.rateRows(h).flatMap((r) => [r.r.hitRate ?? 0, r.r.lossRate ?? 0]), 0.01);
  }

  protected yearMax(h: DoublerHorizon): number {
    return Math.max(...h.byYear.flatMap((y) => [y.hitRate ?? 0, y.lossRate ?? 0]), 0.01);
  }

  protected ci(r: DoublerRates): string {
    if (r.hitCiLow === null || r.hitCiLow === undefined || r.hitCiHigh === null || r.hitCiHigh === undefined) return '';
    return ` (${fmtPct(r.hitCiLow, 1)} to ${fmtPct(r.hitCiHigh, 1)})`;
  }

  protected rateRows(h: DoublerHorizon): { label: string; r: DoublerRates; ref: boolean }[] {
    return [
      { label: 'Screen fired', r: h.screen, ref: false },
      { label: 'Control: volatile & small, no trigger', r: h.control, ref: false },
      { label: 'All stock-days (base rate)', r: h.base, ref: true },
    ];
  }

  protected episodes(h: DoublerHorizon) {
    const all = [...h.episodes].reverse();
    return this.allEpisodes() ? all : all.slice(0, this.EPISODES_SHOWN);
  }

  protected feat(v: number | null | undefined, fmt: (v: number) => string): string {
    return v === null || v === undefined || !Number.isFinite(v) ? '—' : fmt(v);
  }

  protected profileValue(feature: string, v: number | null | undefined): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    const f = SHOWN_FEATURES.find((x) => x.key === feature);
    if (f) return f.fmt(v);
    if (feature === 'dollar_volume_20') return v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;
    return fmtSignedPct(v, 1);
  }

  protected quintiles(f: { byQuintile?: { quintile: number; n: number; hitRate: number | null }[] }) {
    const q = f.byQuintile ?? [];
    const best = Math.max(...q.map((x) => x.hitRate ?? -1));
    return q.map((x) => ({ ...x, best: best > 0 && x.hitRate === best, heat: best > 0 ? (x.hitRate ?? 0) / best : 0 }));
  }
}
