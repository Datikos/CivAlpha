import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import {
  AccuracyResponse,
  AiDecision,
  CompanySummary,
  DecisionsResponse,
  ForecastSummary,
  Job,
  jobPct,
  ModelKind,
  StrategiesResponse,
} from '../core/models';
import { Icon } from '../shared/icon';
import { Sparkline } from '../shared/sparkline';
import { UI } from '../shared/ui';
import { Lean, VIZ, leanOf, verdictTone } from '../shared/viz';

const WELCOME_KEY = 'civalpha.welcomed';
const ACTIVE = new Set(['RUNNING', 'PENDING', 'QUEUED', 'STARTED']);
const DAY = 86_400_000;

interface LeanChange {
  symbol: string;
  model: ModelKind;
  before: ForecastSummary;
  after: ForecastSummary;
  fromLean: Lean;
  toLean: Lean;
}

interface Mover {
  c: CompanySummary;
  excess: number;
}

@Component({
  selector: 'app-dashboard',
  imports: [RouterLink, Icon, Sparkline, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="grid" area="forecast" />
        <div>
          <h1>Dashboard</h1>
          <p class="muted">What changed since the last run: forecasts that flipped, windows that closed, the AI's moves, and the market against its benchmarks.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-dashboard" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a routerLink="/forecasts" class="btn">All current forecasts</a>
      </div>
    </div>

    @if (showWelcome()) {
      <div class="card welcome">
        <div class="welcome-text">
          <h3>New here? Start with the guide.</h3>
          <p class="muted">A five-minute tour of what the numbers mean, which page answers which question, and how to run the pipeline.</p>
        </div>
        <div class="welcome-actions">
          <a routerLink="/guide" class="btn btn-primary"><app-icon name="book" [size]="16" /> Open the guide</a>
          <button type="button" class="btn" (click)="dismissWelcome()">Not now</button>
        </div>
      </div>
    }

    <div class="stats wide">
      <div class="stat" [class]="'stat ' + pipeline().tone">
        <div class="stat-label">Pipeline</div>
        <div class="stat-value">{{ pipeline().headline }}</div>
        <div class="stat-sub">{{ pipeline().detail }} · <a routerLink="/admin">Data &amp; pipeline</a></div>
      </div>
      <div class="stat tone-forecast">
        <div class="stat-label">Forecasts <app-help text="The latest as-of date with published forecasts, and how the leans split. A lean needs the whole interval on one side of 50%." topic="lean" label="forecasts" /></div>
        @if (fc(); as f) {
          <div class="stat-value">{{ f.companies }}<span class="unit">companies as of {{ f.asOf }}</span></div>
          <div class="lean-key small" style="margin-top: 0.3rem">
            <span class="lean tone-good">▲ {{ f.above }}</span>
            <span class="lean tone-neutral">≈ {{ f.flat }}</span>
            <span class="lean tone-bad">▼ {{ f.below }}</span>
            <span class="muted">{{ f.replayed ? f.replayed + ' replayed' : 'all live' }}</span>
          </div>
        } @else {
          <div class="stat-value muted">none yet</div>
          <div class="stat-sub">run the pipeline to issue forecasts</div>
        }
      </div>
      <div class="stat" [class]="'stat ' + (ai() ? (ai()!.enter.length ? 'tone-good' : 'tone-neutral') : 'tone-neutral')">
        <div class="stat-label">AI strategy <app-help text="Today's actions of the gradient-boosted strategy: Enter means buy at the next close, Exit means sell. Recorded for research only." topic="page-decisions" label="AI strategy" /></div>
        @if (ai(); as a) {
          <div class="stat-value">{{ a.enter.length }}<span class="unit">enter · {{ a.exit.length }} exit · {{ a.hold }} hold</span></div>
          <div class="stat-sub">{{ a.invested | pct: 0 }} invested as of {{ a.asOf }} · <a routerLink="/decisions">decisions</a></div>
        } @else {
          <div class="stat-value muted">none yet</div>
        }
      </div>
      <div class="stat" [class]="'stat ' + evidence().tone">
        <div class="stat-label">Evidence <app-help text="The two honest verdicts: does the model have measurable skill out of sample, and does any strategy beat buy and hold after costs and after accounting for luck." topic="page-accuracy" label="evidence" /></div>
        <div class="stat-value">{{ evidence().headline }}</div>
        <div class="stat-sub">{{ evidence().detail }}</div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-head">
          <h3>Leans that changed <app-help text="Forecasts whose lean (above, below or coin flip) differs between the newest as-of date and the one before it. Grey arrows show the probability move." topic="lean" label="lean changes" /></h3>
          <span class="small muted">
            @if (dates(); as d) { {{ d.prev }} → {{ d.latest }} }
            · <a routerLink="/forecasts">all forecasts</a>
          </span>
        </div>
        @if (!history.hasValue()) {
          <app-status [res]="history" what="forecast history" />
        } @else if (!dates()) {
          <p class="muted">Only one as-of date so far; changes appear after the next pipeline run.</p>
        } @else if (!leanChanges().length) {
          <p class="muted">No lean changed between {{ dates()!.prev }} and {{ dates()!.latest }}.</p>
        } @else {
          <ul class="dash-list">
            @for (x of leanChanges(); track x.symbol + x.model) {
              <li>
                <a class="sym" [routerLink]="['/companies', x.symbol]">{{ x.symbol }}</a>
                <app-model-tag [kind]="x.model" />
                <span class="grow"></span>
                <app-lean [p]="x.before.probability" [lo]="x.before.probLow" [hi]="x.before.probHigh" />
                <span class="dash-arrow" aria-hidden="true">→</span>
                <app-lean [p]="x.after.probability" [lo]="x.after.probLow" [hi]="x.after.probHigh" />
                <span class="small nowrap">{{ x.before.probability | pct: 0 }} → <a [routerLink]="['/forecasts', x.after.id]">{{ x.after.probability | pct: 0 }}</a></span>
              </li>
            }
          </ul>
        }
        @if (bigMoves().length) {
          <h3 style="margin-top: 1rem">Largest probability moves</h3>
          <ul class="dash-list">
            @for (x of bigMoves(); track x.symbol + x.model) {
              <li>
                <a class="sym" [routerLink]="['/companies', x.symbol]">{{ x.symbol }}</a>
                <app-model-tag [kind]="x.model" />
                <span class="grow"></span>
                <span class="small">{{ x.before.probability | pct: 1 }} → <a [routerLink]="['/forecasts', x.after.id]">{{ x.after.probability | pct: 1 }}</a></span>
                <app-delta [value]="x.after.probability - x.before.probability" kind="pp" [digits]="1" />
              </li>
            }
          </ul>
        }
      </div>

      <div class="card">
        <div class="card-head">
          <h3>Windows that closed <app-help text="Forecasts whose 21-trading-day window ended recently, scored against what happened. Brier below 0.25 beats a coin flip." topic="brier" label="resolved forecasts" /></h3>
          <span class="small muted">last 10 days · <a routerLink="/accuracy">accuracy</a></span>
        </div>
        @if (resolved(); as r) {
          @if (!r.rows.length) {
            <p class="muted">No forecast window closed in the last 10 days.</p>
          } @else {
            <p class="small" style="margin: 0 0 0.5rem">
              <span class="badge" [class]="'badge tone-' + (r.hitRate > 0.5 ? 'good' : r.hitRate < 0.5 ? 'bad' : 'neutral')">{{ r.hits }} of {{ r.rows.length }} right</span>
              mean Brier {{ r.brier | fixed: 3 }}
              <span class="badge" [class]="'badge tone-' + (r.brier < 0.25 ? 'good' : 'warn')">{{ r.brier < 0.25 ? '✓ beats a coin flip' : '≈ no better than a coin flip' }}</span>
            </p>
            <ul class="dash-list">
              @for (f of r.rows; track f.id) {
                <li>
                  <a class="sym" [routerLink]="['/companies', f.symbol]">{{ f.symbol }}</a>
                  <app-model-tag [kind]="f.modelKind" />
                  <span class="small muted">p {{ f.probability | pct: 0 }}</span>
                  <span class="grow"></span>
                  <span class="badge" [class]="'badge tone-' + (f.outcome!.outcome ? 'good' : 'bad')">{{ f.outcome!.outcome ? '✓ outperformed' : '✗ underperformed' }}</span>
                  <app-delta [value]="f.outcome!.excessReturn" kind="pct" [digits]="1" />
                  <a class="small" [routerLink]="['/forecasts', f.id]" [title]="'Brier ' + f.outcome!.brier">Brier {{ f.outcome!.brier | fixed: 2 }}</a>
                </li>
              }
            </ul>
            @if (r.more) {
              <p class="small muted" style="margin: 0.5rem 0 0">{{ r.more }} more on the <a routerLink="/forecasts/history">history page</a>.</p>
            }
          }
        } @else {
          <app-status [res]="history" what="forecast history" />
        }
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-head">
          <h3>AI moves today</h3>
          <span class="small muted">@if (ai(); as a) { as of {{ a.asOf }} · } <a routerLink="/decisions">all decisions</a></span>
        </div>
        @if (ai(); as a) {
          @if (!a.enter.length && !a.exit.length) {
            <p class="muted">No entries or exits: {{ a.hold }} held, {{ a.out }} stayed out.</p>
          } @else {
            <ul class="dash-list">
              @for (d of a.enter; track d.id) {
                <li>
                  <span class="badge tone-good">▲ Enter</span>
                  <a class="sym" [routerLink]="['/companies', d.symbol]">{{ d.symbol }}</a>
                  <span class="small muted grow">{{ d.name }}</span>
                  <span class="prob tone-good">p = {{ d.probability | pct: 0 }}</span>
                  <span class="small muted">rank {{ d.rank }}</span>
                </li>
              }
              @for (d of a.exit; track d.id) {
                <li>
                  <span class="badge tone-bad">▼ Exit</span>
                  <a class="sym" [routerLink]="['/companies', d.symbol]">{{ d.symbol }}</a>
                  <span class="small muted grow">{{ d.name }}</span>
                  <span class="prob tone-bad">p = {{ d.probability | pct: 0 }}</span>
                </li>
              }
            </ul>
          }
          @if (a.held.length) {
            <p class="small muted" style="margin: 0.6rem 0 0">Holding: {{ a.held.join(', ') }}</p>
          }
        } @else {
          <app-status [res]="decisionsRes" what="AI decisions" />
          @if (decisionsRes.hasValue()) {
            <p class="muted">No AI decisions yet.</p>
          }
        }
      </div>

      <div class="card">
        <div class="card-head">
          <h3>Movers vs benchmark <app-help text="Each stock's return over the last 21 trading days minus its sector ETF's return over the same days. The sparkline is the stock's raw close." topic="benchmark" label="movers" /></h3>
          <span class="small muted">21 trading days · <a routerLink="/companies">all companies</a></span>
        </div>
        <app-status [res]="companiesRes" what="companies" />
        @if (movers(); as m) {
          <div class="movers">
            <div>
              <div class="small muted" style="margin-bottom: 0.3rem">Ahead of the sector</div>
              <ul class="dash-list">
                @for (x of m.up; track x.c.id) {
                  <li>
                    <a class="sym" [routerLink]="['/companies', x.c.symbol]">{{ x.c.symbol }}</a>
                    <app-sparkline [points]="x.c.recentCloses ?? []" [width]="70" [height]="20" [label]="x.c.symbol + ' last 22 closes'" />
                    <span class="grow"></span>
                    <app-delta [value]="x.excess" kind="pct" [digits]="1" />
                  </li>
                }
              </ul>
            </div>
            <div>
              <div class="small muted" style="margin-bottom: 0.3rem">Behind the sector</div>
              <ul class="dash-list">
                @for (x of m.down; track x.c.id) {
                  <li>
                    <a class="sym" [routerLink]="['/companies', x.c.symbol]">{{ x.c.symbol }}</a>
                    <app-sparkline [points]="x.c.recentCloses ?? []" [width]="70" [height]="20" [label]="x.c.symbol + ' last 22 closes'" />
                    <span class="grow"></span>
                    <app-delta [value]="x.excess" kind="pct" [digits]="1" />
                  </li>
                }
              </ul>
            </div>
          </div>
        }
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-head">
          <h3>Model accuracy</h3>
          <span class="small"><a routerLink="/accuracy">open →</a></span>
        </div>
        @if (accuracy(); as a) {
          <app-verdict [tone]="verdictToneOf(a.verdict)">{{ a.verdict }}</app-verdict>
        } @else if (accuracyRes.hasValue()) {
          <p class="muted">No evaluation yet.</p>
        }
      </div>
      <div class="card">
        <div class="card-head">
          <h3>Strategy lab</h3>
          <span class="small"><a routerLink="/strategies">open →</a></span>
        </div>
        @if (strategies(); as s) {
          <app-verdict [tone]="s.supported ? 'good' : 'warn'">{{ s.run.summary }}</app-verdict>
        } @else if (strategiesRes.hasValue()) {
          <p class="muted">No backtest yet.</p>
        }
      </div>
    </div>
  `,
  styles: `
    .movers { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }
    .prob.tone-good, .prob.tone-bad { color: var(--tone-ink); background: var(--tone-bg); border-color: var(--tone-mark); }
    .verdict { margin-bottom: 0; font-size: 0.95rem; }
  `,
})
export class DashboardPage {
  protected readonly meta = inject(MetaService);
  protected readonly verdictToneOf = verdictTone;
  protected readonly showWelcome = signal(readWelcome());

  protected readonly history = httpResource<ForecastSummary[]>(() => apiUrl.forecastsHistory());
  protected readonly companiesRes = httpResource<CompanySummary[]>(() => apiUrl.companies());
  protected readonly decisionsRes = httpResource<DecisionsResponse>(() => apiUrl.decisions());
  protected readonly jobsRes = httpResource<Job[]>(() => apiUrl.jobs());
  protected readonly accuracyRes = httpResource<AccuracyResponse>(() => apiUrl.accuracy());
  protected readonly strategiesRes = httpResource<StrategiesResponse>(() => apiUrl.strategies());

  /** Latest version per (symbol, model, as-of date). */
  private readonly latestVersions = computed(() => {
    const out = new Map<string, ForecastSummary>();
    for (const f of valueOf(this.history) ?? []) {
      const k = `${f.symbol}|${f.modelKind}|${f.asOfDate}`;
      const cur = out.get(k);
      if (!cur || f.version > cur.version) out.set(k, f);
    }
    return out;
  });

  protected readonly dates = computed(() => {
    const ds = [...new Set((valueOf(this.history) ?? []).map((f) => f.asOfDate))].sort();
    if (ds.length < 2) return null;
    return { latest: ds[ds.length - 1], prev: ds[ds.length - 2] };
  });

  protected readonly fc = computed(() => {
    const list = valueOf(this.history) ?? [];
    if (!list.length) return null;
    const asOf = list.map((f) => f.asOfDate).sort().at(-1)!;
    const latest = [...this.latestVersions().values()].filter((f) => f.asOfDate === asOf);
    let above = 0;
    let below = 0;
    let flat = 0;
    for (const f of latest) {
      const l = leanOf(f.probability, f.probLow, f.probHigh);
      if (l === 'above') above++;
      else if (l === 'below') below++;
      else flat++;
    }
    return {
      asOf,
      companies: new Set(latest.map((f) => f.symbol)).size,
      above,
      below,
      flat,
      replayed: latest.filter((f) => f.issueMode === 'REPLAY').length,
    };
  });

  private readonly pairs = computed<LeanChange[]>(() => {
    const d = this.dates();
    if (!d) return [];
    const out: LeanChange[] = [];
    for (const [k, after] of this.latestVersions()) {
      if (!k.endsWith(`|${d.latest}`)) continue;
      const before = this.latestVersions().get(`${after.symbol}|${after.modelKind}|${d.prev}`);
      if (!before) continue;
      out.push({
        symbol: after.symbol,
        model: after.modelKind,
        before,
        after,
        fromLean: leanOf(before.probability, before.probLow, before.probHigh),
        toLean: leanOf(after.probability, after.probLow, after.probHigh),
      });
    }
    return out;
  });

  protected readonly leanChanges = computed(() =>
    this.pairs()
      .filter((x) => x.fromLean !== x.toLean)
      .sort((a, b) => Math.abs(b.after.probability - b.before.probability) - Math.abs(a.after.probability - a.before.probability)),
  );

  protected readonly bigMoves = computed(() =>
    this.pairs()
      .filter((x) => x.fromLean === x.toLean)
      .sort((a, b) => Math.abs(b.after.probability - b.before.probability) - Math.abs(a.after.probability - a.before.probability))
      .slice(0, 5)
      .filter((x) => Math.abs(x.after.probability - x.before.probability) >= 0.01),
  );

  protected readonly resolved = computed(() => {
    const list = valueOf(this.history);
    if (!list) return null;
    const ended = list.filter((f) => f.outcome?.windowEndDate);
    if (!ended.length) return { rows: [], hits: 0, hitRate: 0, brier: 0, more: 0 };
    const last = ended.map((f) => f.outcome!.windowEndDate).sort().at(-1)!;
    const from = new Date(Date.parse(last + 'T00:00:00Z') - 10 * DAY).toISOString().slice(0, 10);
    const recent = ended
      .filter((f) => f.outcome!.windowEndDate >= from)
      .sort((a, b) => b.outcome!.windowEndDate.localeCompare(a.outcome!.windowEndDate) || a.symbol.localeCompare(b.symbol));
    const hits = recent.filter((f) => f.probability > 0.5 === f.outcome!.outcome).length;
    const brier = recent.reduce((s, f) => s + f.outcome!.brier, 0) / recent.length;
    return { rows: recent.slice(0, 12), hits, hitRate: hits / recent.length, brier, more: Math.max(0, recent.length - 12) };
  });

  protected readonly ai = computed(() => {
    const r = valueOf(this.decisionsRes);
    if (!r?.decisions?.length) return null;
    const by = (a: string) => r.decisions.filter((d) => d.action === a);
    return {
      asOf: r.asOfDate,
      enter: by('ENTER') as AiDecision[],
      exit: by('EXIT') as AiDecision[],
      hold: by('HOLD').length,
      out: by('STAY_OUT').length,
      held: by('HOLD').map((d) => d.symbol),
      invested: r.decisions.reduce((s, d) => s + d.weight, 0),
    };
  });

  protected readonly movers = computed(() => {
    const list = (valueOf(this.companiesRes) ?? [])
      .filter((c) => typeof c.change21d === 'number' && typeof c.benchmarkChange21d === 'number')
      .map<Mover>((c) => ({ c, excess: c.change21d! - c.benchmarkChange21d! }))
      .sort((a, b) => b.excess - a.excess);
    if (!list.length) return null;
    return { up: list.slice(0, 5), down: list.slice(-5).reverse() };
  });

  protected readonly pipeline = computed(() => {
    const jobs = valueOf(this.jobsRes) ?? [];
    const m = this.meta.meta();
    const running = jobs.filter((j) => ACTIVE.has(j.status));
    const last = [...jobs].sort((a, b) => b.id - a.id)[0];
    if (running.length) {
      const active = running.find((j) => j.status === 'RUNNING') ?? running[0];
      const pct = jobPct(active);
      const what = humanJob(active.jobType) + (pct !== null ? ` ${pct}%` : '');
      const more = running.length > 1 ? ` · ${running.length - 1} more queued` : '';
      return { tone: 'tone-info', headline: pct !== null ? `${pct}%` : 'running', detail: `${what}${more}` };
    }
    if (!last) return { tone: 'tone-warn', headline: 'never run', detail: 'run the pipeline to load data' };
    const cutoff = m?.dataCutoff ? `data to ${m.dataCutoff}` : 'no prices yet';
    if (last.status === 'FAILED') return { tone: 'tone-bad', headline: 'last job failed', detail: `${humanJob(last.jobType)} · ${cutoff}` };
    return { tone: 'tone-good', headline: cutoff, detail: `last job ${humanJob(last.jobType)} succeeded` };
  });

  protected readonly accuracy = computed(() => valueOf(this.accuracyRes)?.evaluation ?? null);
  protected readonly strategies = computed(() => {
    const r = valueOf(this.strategiesRes);
    if (!r?.run) return null;
    return { run: r.run, supported: r.results.some((s) => s.family !== 'BENCHMARK' && s.verdict.includes('SUPPORTED by')) };
  });

  protected readonly evidence = computed(() => {
    const a = this.accuracy();
    const s = this.strategies();
    if (!a && !s) return { tone: 'tone-neutral', headline: 'not yet', detail: 'run the pipeline to evaluate' };
    const modelOk = a ? verdictTone(a.verdict) === 'good' : false;
    const stratOk = s?.supported ?? false;
    const n = (modelOk ? 1 : 0) + (stratOk ? 1 : 0);
    return {
      tone: n === 2 ? 'tone-good' : n === 1 ? 'tone-info' : 'tone-warn',
      headline: n === 0 ? 'not supported' : n === 1 ? 'partly supported' : 'supported',
      detail: `model skill ${a ? (modelOk ? '✓' : '✗') : '—'} · a strategy beats buy & hold ${s ? (stratOk ? '✓' : '✗') : '—'}`,
    };
  });

  protected dismissWelcome(): void {
    this.showWelcome.set(false);
    try {
      localStorage.setItem(WELCOME_KEY, '1');
    } catch {
      // storage unavailable
    }
  }
}

function humanJob(t: string): string {
  const s = t.replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function readWelcome(): boolean {
  try {
    return localStorage.getItem(WELCOME_KEY) !== '1';
  } catch {
    return true;
  }
}
