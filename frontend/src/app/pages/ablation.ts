import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DotWhisker, WhiskerItem, WhiskerSeries } from '../charts/dot-whisker';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtFixed, fmtNum, fmtPct, fmtSigned, fmtSignedPct } from '../core/format';
import { AblationLabRow, AblationResponse, AblationRun, AblationWalkForwardRow, Job } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

/** Variant keys as the backend names them, in the order the charts show them. */
const VARIANT_LABEL: Record<string, string> = {
  FULL: 'Production set',
  NO_PRICE_TECHNICAL: 'without price/technical',
  NO_REPORT_PROFILE: 'without report profile',
  NO_INSIDER: 'without insider',
  NO_EARNINGS: 'without earnings',
  PLUS_DIVIDENDS: 'plus dividends',
  PLUS_POLICY_EVENTS: 'plus policy events',
};

const HORIZON_SERIES: WhiskerSeries[] = [
  { key: '21', label: '21-day forecast target', color: 'var(--series-1)' },
  { key: '10', label: "10-day label the book trades", color: 'var(--series-2)' },
];
const RULE_SERIES: WhiskerSeries[] = [
  { key: 'AI_GBM', label: 'Standard rule (AI_GBM)', color: 'var(--series-1)' },
  { key: 'AI_SIZED', label: 'Sized book (AI_SIZED, the recorded book)', color: 'var(--series-2)' },
];

@Component({
  selector: 'app-ablation',
  imports: [RouterLink, DotWhisker, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="diff" area="strategy" />
        <div>
          <h1>Feature fragility</h1>
          <p class="muted">
            How much the recorded book's numbers move when one group of inputs is removed or added. Every variant is scored
            the way a feature-set decision is supposed to be made: walk-forward Brier skill and AUC with 95% intervals,
            on the platform's 21-day target and on the 10-day label the book trades. The lab Sharpe of the same
            probabilities is shown beside it because it is the number that moves, and it is not a skill metric.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-ablation" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a class="btn" routerLink="/accuracy">Model accuracy →</a>
        <a class="btn" routerLink="/strategies">Strategy lab →</a>
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
      <p class="small muted">
        Seven variants, each walk-forward twice and backtested twice: about 20 minutes on the stored data. Every variant is
        registered as a trial, so the next lab run deflates for it.
      </p>
      @if (notice()) {
        <div class="alert" [class.alert-error]="noticeError()" role="status">{{ notice() }}</div>
      }
    </div>

    <app-status [res]="res" what="feature fragility study" />

    @if (res.hasValue()) {
      @if (data(); as r) {
        <app-verdict [tone]="anySkill() ? 'good' : 'warn'">{{ r.headline }}</app-verdict>
        <p class="small muted">
          Study #{{ r.id }} run {{ r.runAt | utc }} · data to {{ r.dataCutoff }} · production set {{ r.result.config.productionFeatureSet }}
          · {{ r.result.variants.length }} variants · intervals: {{ r.result.config.walkForward.ciMethod }}, {{ r.result.config.walkForward.ciBoot | num }} draws
        </p>

        @if (headline(); as h) {
          <div class="stats wide">
            <div class="stat" [class]="'stat ' + (h.fullSkillHi > 0 && h.fullSkillLo > 0 ? 'tone-good' : 'tone-warn')">
              <div class="stat-label">Production set, 10-day label <app-help text="Brier skill of the recorded book's own input list on the label it trades, with its 95% interval. Above zero with the whole interval above zero would mean the probabilities beat a base-rate forecast." topic="brier-skill" label="production set" /></div>
              <div class="stat-value">{{ h.fullSkill | signed: 3 }}<span class="unit">Brier skill</span></div>
              <div class="stat-sub">CI {{ h.fullSkillLo | signed: 3 }} to {{ h.fullSkillHi | signed: 3 }} · AUC {{ h.fullAuc | fixed: 3 }}</div>
            </div>
            <div class="stat tone-neutral">
              <div class="stat-label">Spread across the variants <app-help text="Lowest to highest AUC on the 10-day label across the seven input lists. A narrow spread inside every interval means the inputs do not change the forecast quality." topic="auc" label="spread" /></div>
              <div class="stat-value">{{ h.aucLo | fixed: 3 }} – {{ h.aucHi | fixed: 3 }}<span class="unit">AUC</span></div>
              <div class="stat-sub">Brier skill {{ h.skillLo | signed: 3 }} to {{ h.skillHi | signed: 3 }} · {{ h.aboveZero }} of {{ h.n }} intervals above zero</div>
            </div>
            @if (h.sharpeLo !== null) {
              <div class="stat tone-warn">
                <div class="stat-label">Lab Sharpe of the sized book <app-help text="The Sharpe ratio the strategy lab would print for the recorded book on each variant's probabilities. One trading rule on one five-year window: it moves with which handful of stocks a rule happened to hold, not with information." topic="sharpe" label="lab Sharpe" /></div>
                <div class="stat-value">{{ h.sharpeLo | fixed: 2 }} – {{ h.sharpeHi | fixed: 2 }}</div>
                <div class="stat-sub">{{ h.fullSharpe | fixed: 2 }} on the production set · not a skill metric</div>
              </div>
            }
          </div>
        }

        <div class="card">
          <h3>Forecast quality by input list <app-help text="Dot = walk-forward number, whisker = 95% interval from a bootstrap over 21-day blocks of as-of dates. Blue is the platform's 21-day target entered at the close; orange is the 10-day label the recorded book trades, entered at the next close. A variant has shown skill only when its whole whisker sits right of the dashed line." topic="confidence-interval" label="forecast quality" /></h3>
          <p class="small muted">
            @if (anySkill()) {
              At least one whisker sits wholly right of the line: that variant's probabilities beat a base-rate forecast on this history.
            } @else {
              Every whisker crosses or sits left of the line: no input list beats a base-rate forecast, and the groups are interchangeable.
            }
          </p>
          <div class="grid-2">
            <div>
              <h4>Brier skill vs the base-rate forecast</h4>
              <app-dot-whisker [items]="skillItems()" [series]="horizonSeries" label="Brier skill per input list and label, with 95% intervals" [refX]="0" refLabel="base-rate forecast" [format]="signed3" />
            </div>
            <div>
              <h4>AUC</h4>
              <app-dot-whisker [items]="aucItems()" [series]="horizonSeries" label="AUC per input list and label, with 95% intervals" [refX]="0.5" refLabel="no skill" [format]="fixed3" />
            </div>
          </div>
          <details class="chart-table">
            <summary>Data table ({{ r.result.walkForward.length }} rows)</summary>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr><th>Variant</th><th>Feature set</th><th class="num">Inputs</th><th>Label</th><th class="num">n</th><th class="num">Brier skill (95% CI)</th><th class="num">AUC (95% CI)</th><th class="num">Top-10% net excess (95% CI)</th></tr>
                </thead>
                <tbody>
                  @for (w of r.result.walkForward; track w.variant + w.horizon) {
                    <tr>
                      <td>{{ variantLabel(w.variant) }}</td>
                      <td class="mono">{{ w.featureSet }}</td>
                      <td class="num">{{ w.nFeatures }}</td>
                      <td>{{ w.horizon }} days from {{ w.entry }}</td>
                      <td class="num">{{ w.n | num }}</td>
                      <td class="num"><app-delta [value]="w.brierSkill" kind="fixed" [digits]="3" /><div class="small muted">{{ w.brierSkillCiLow | signed: 3 }} to {{ w.brierSkillCiHigh | signed: 3 }}</div></td>
                      <td class="num">{{ w.auc | fixed: 3 }}<div class="small muted">{{ w.aucCiLow | fixed: 3 }} to {{ w.aucCiHigh | fixed: 3 }}</div></td>
                      <td class="num">@if (w.top10NetExcess === null) { — } @else { <app-delta [value]="w.top10NetExcess" kind="pct" [digits]="2" /><div class="small muted">{{ w.top10CiLow | signedPct: 2 }} to {{ w.top10CiHigh | signedPct: 2 }}</div> }</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </details>
        </div>

        @if (r.result.lab.length) {
          <div class="card">
            <h3>Lab Sharpe of the same probabilities <span class="badge tone-warn">not a skill metric</span> <app-help text="What the strategy lab prints when each variant's probabilities are traded with the standard rule and with the recorded book's sizing: one trading rule, one five-year window, no interval. It is shown because it is the number that moves while the forecast quality does not, which is why feature-set decisions must not be made on it." topic="sharpe" label="lab Sharpe" /></h3>
            <p class="small muted">
              A Sharpe that halves while the whiskers above do not move is measuring which handful of stocks a rule happened to
              hold, not information.
            </p>
            <div class="grid-2">
              <div>
                <h4>Sharpe ratio</h4>
                <app-dot-whisker [items]="sharpeItems()" [series]="ruleSeries" label="Lab Sharpe ratio per input list, standard rule and sized book" [refX]="null" [betterIs]="null" [format]="fixed2" />
              </div>
              <div>
                <h4>Max drawdown</h4>
                <app-dot-whisker [items]="ddItems()" [series]="ruleSeries" label="Lab maximum drawdown per input list, standard rule and sized book" [refX]="null" [betterIs]="null" [format]="pct1" />
              </div>
            </div>
            <details class="chart-table">
              <summary>Data table ({{ r.result.lab.length }} rows)</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead><tr><th>Variant</th><th>Rule</th><th class="num">Sharpe</th><th class="num">Max DD</th><th class="num">CAGR</th><th>Window</th></tr></thead>
                  <tbody>
                    @for (l of r.result.lab; track l.variant + l.strategyKey) {
                      <tr>
                        <td>{{ variantLabel(l.variant) }}</td>
                        <td class="mono">{{ l.strategyKey }}</td>
                        <td class="num">{{ l.sharpe | fixed: 2 }}</td>
                        <td class="num">{{ l.maxDrawdown | pct: 1 }}</td>
                        <td class="num"><app-delta [value]="l.cagr" kind="pct" [digits]="1" /></td>
                        <td>{{ l.start }} to {{ l.end }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        }

        <div class="card">
          <h3>The variants</h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead><tr><th>Variant</th><th>Feature set</th><th class="num">Inputs</th><th>Change</th></tr></thead>
                <tbody>
                  @for (v of r.result.variants; track v.key) {
                    <tr>
                      <td>{{ variantLabel(v.key) }}</td>
                      <td class="mono">{{ v.featureSet }}</td>
                      <td class="num">{{ v.nFeatures }}</td>
                      <td class="small muted">{{ changeText(v.change, r) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </div>
          <div class="card">
            <h3>How to read this</h3>
            <ul class="small notes">
              <li>The question is not "which variant is best" but "does any variant know something". Only a whisker wholly
                right of the dashed line would say yes; overlapping whiskers around it say the inputs are interchangeable noise.</li>
              <li>The production set is the recorded book's input list ({{ r.result.config.productionFeatureSet }}). Removing the
                whole report profile ({{ groupSize(r, 'report_profile') }} of its inputs) is the biggest cut; if the skill does
                not move, the profile was not what the model used.</li>
              <li>The lab Sharpe has no interval because the lab does not compute one for a single rule on one window; its spread
                across variants is what the number is worth.</li>
              <li>Each variant counts as a trial in the trial registry, which the Deflated Sharpe Ratio on the strategy lab deflates
                for. Running this study makes every future lab verdict stricter, on purpose.</li>
              <li>This is research, not advice.</li>
            </ul>
          </div>
      } @else {
        <div class="empty-box">No feature fragility study yet. Click <strong>Run the study</strong> above; it takes about 20 minutes.</div>
      }
    }
  `,
  styles: `
    .run-card { margin-bottom: 1rem; }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
    .inline-form { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end; }
    .grid-2 h4 { margin: 0.25rem 0 0.35rem; font-size: 0.9rem; }
    h3 .badge { vertical-align: middle; margin-left: 0.3rem; }
  `,
})
export class AblationPage {
  protected readonly api = inject(ApiService);
  protected readonly horizonSeries = HORIZON_SERIES;
  protected readonly ruleSeries = RULE_SERIES;
  protected readonly selectedId = signal<number | null>(null);
  protected readonly jobId = signal<number | null>(null);
  protected readonly notice = signal('');
  protected readonly noticeError = signal(false);

  protected readonly res = httpResource<AblationResponse>(() => apiUrl.ablation());
  protected readonly runList = computed(() => valueOf(this.res)?.runs ?? []);
  protected readonly currentId = computed(() => this.selectedId() ?? this.runList()[0]?.id ?? null);
  private readonly older = httpResource<AblationRun>(() => {
    const id = this.selectedId();
    return id ? apiUrl.ablationRun(id) : undefined;
  });
  protected readonly data = computed<AblationRun | null>(() => {
    const latest = valueOf(this.res)?.run ?? null;
    const id = this.selectedId();
    if (id && id !== latest?.id) return valueOf(this.older) ?? null;
    return latest;
  });

  private readonly wf = computed(() => this.data()?.result.walkForward ?? []);
  private readonly lab = computed(() => this.data()?.result.lab ?? []);
  protected readonly anySkill = computed(() => this.wf().some((w) => w.brierSkillCiLow > 0));

  protected readonly headline = computed(() => {
    const wf = this.wf();
    if (!wf.length) return null;
    const horizons = [...new Set(wf.map((w) => w.horizon))].sort((a, b) => a - b);
    const book = horizons[0] === 21 && horizons.length > 1 ? horizons[1] : horizons[0];
    const rows = wf.filter((w) => w.horizon === book);
    const full = rows.find((w) => w.variant === 'FULL') ?? rows[0];
    const sized = this.lab().filter((l) => l.strategyKey === 'AI_SIZED');
    const sharpes = sized.map((l) => l.sharpe).filter((v): v is number => v !== null);
    return {
      fullSkill: full.brierSkill,
      fullSkillLo: full.brierSkillCiLow,
      fullSkillHi: full.brierSkillCiHigh,
      fullAuc: full.auc,
      skillLo: Math.min(...rows.map((w) => w.brierSkill)),
      skillHi: Math.max(...rows.map((w) => w.brierSkill)),
      aucLo: Math.min(...rows.map((w) => w.auc)),
      aucHi: Math.max(...rows.map((w) => w.auc)),
      aboveZero: wf.filter((w) => w.brierSkillCiLow > 0).length,
      n: wf.length,
      sharpeLo: sharpes.length ? Math.min(...sharpes) : null,
      sharpeHi: sharpes.length ? Math.max(...sharpes) : null,
      fullSharpe: sized.find((l) => l.variant === 'FULL')?.sharpe ?? null,
    };
  });

  protected variantLabel(key: string): string {
    const v = this.data()?.result.variants.find((x) => x.key === key);
    const base = VARIANT_LABEL[key] ?? key;
    return v ? `${base} (${v.nFeatures})` : base;
  }

  protected changeText(change: string, r: AblationRun): string {
    const [kind, group] = change.split(':');
    if (kind === 'full') return 'the recorded book\'s input list';
    const feats = kind === 'drop' ? r.result.config.groups[group] : r.result.config.additions[group];
    const n = feats?.length ?? 0;
    return `${kind === 'drop' ? 'drops' : 'adds'} the ${group.replace('_', '/')} group: ${n} inputs`;
  }

  protected groupSize(r: AblationRun, group: string): number {
    return r.result.config.groups[group]?.length ?? 0;
  }

  private wfDetails(w: AblationWalkForwardRow): string[] {
    return [`${w.featureSet} · ${w.nFeatures} inputs · n = ${fmtNum(w.n)}`, `top-10% net excess ${fmtSignedPct(w.top10NetExcess, 2)} (${fmtSignedPct(w.top10CiLow, 2)} to ${fmtSignedPct(w.top10CiHigh, 2)})`];
  }
  protected readonly skillItems = computed<WhiskerItem[]>(() =>
    this.wf().map((w) => ({ row: w.variant, label: this.variantLabel(w.variant), series: String(w.horizon), value: w.brierSkill, lo: w.brierSkillCiLow, hi: w.brierSkillCiHigh, details: this.wfDetails(w) })),
  );
  protected readonly aucItems = computed<WhiskerItem[]>(() =>
    this.wf().map((w) => ({ row: w.variant, label: this.variantLabel(w.variant), series: String(w.horizon), value: w.auc, lo: w.aucCiLow, hi: w.aucCiHigh, details: this.wfDetails(w) })),
  );
  private labDetails(l: AblationLabRow): string[] {
    return [`${l.featureSet} · CAGR ${fmtSignedPct(l.cagr, 1)} · ${l.start} to ${l.end}`, l.note];
  }
  protected readonly sharpeItems = computed<WhiskerItem[]>(() =>
    this.lab().filter((l) => l.sharpe !== null).map((l) => ({ row: l.variant, label: this.variantLabel(l.variant), series: l.strategyKey, value: l.sharpe!, details: this.labDetails(l) })),
  );
  protected readonly ddItems = computed<WhiskerItem[]>(() =>
    this.lab().filter((l) => l.maxDrawdown !== null).map((l) => ({ row: l.variant, label: this.variantLabel(l.variant), series: l.strategyKey, value: l.maxDrawdown!, details: this.labDetails(l) })),
  );
  protected readonly signed3 = (v: number) => fmtSigned(v, 3);
  protected readonly fixed3 = (v: number) => fmtFixed(v, 3);
  protected readonly fixed2 = (v: number) => fmtFixed(v, 2);
  protected readonly pct1 = (v: number) => fmtPct(v, 1);

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
      const t = setInterval(() => this.jobs.reload(), 5000);
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
    this.notice.set('Running the study… about 20 minutes. The page updates when it is done.');
    this.api.ablationStudy().subscribe({
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
