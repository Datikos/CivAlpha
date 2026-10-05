import { httpResource } from '@angular/common/http';
import { Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../charts/line-chart';
import { dateMs, isoDay } from '../charts/chart-utils';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtByUnit, fmtPct, fmtSignedPct, fmtUsd, humanize } from '../core/format';
import { latestByModel } from '../core/forecast-utils';
import {
  AiDecision,
  CompanyDetail,
  CompanySummary,
  DecisionsResponse,
  DividendProfile,
  ForecastSummary,
  KeyFact,
  ModelKind,
  PriceSeries,
} from '../core/models';
import { Icon } from '../shared/icon';
import { Sparkline } from '../shared/sparkline';
import { UI } from '../shared/ui';
import { Tone, VIZ, leanOf } from '../shared/viz';

const MAX = 3;
const YEAR_AGO = isoDay(Date.now() - 366 * 864e5);

interface Slot {
  symbol: () => string | null;
  detail: ReturnType<typeof httpResource<CompanyDetail>>;
  prices: ReturnType<typeof httpResource<PriceSeries>>;
  fc: ReturnType<typeof httpResource<ForecastSummary[]>>;
  div: ReturnType<typeof httpResource<DividendProfile>>;
}

type CellKind = 'text' | 'pct' | 'signed' | 'prob' | 'badge' | 'usd' | 'fact' | 'spark';

interface Cell {
  kind: CellKind;
  text?: string;
  sub?: string;
  value?: number | null;
  tone?: Tone;
  f?: ForecastSummary | null;
  link?: (string | number)[];
  points?: number[];
  unit?: string | null;
}

interface Row {
  label: string;
  help?: string;
  topic?: string;
  cells: Cell[];
  /** index of the best cell, or -1 */
  best: number;
}

function best(values: (number | null | undefined)[], higherIsBetter: boolean | null): number {
  if (higherIsBetter === null) return -1;
  let idx = -1;
  let bv = higherIsBetter ? -Infinity : Infinity;
  values.forEach((v, i) => {
    if (typeof v !== 'number' || !Number.isFinite(v)) return;
    if (higherIsBetter ? v > bv : v < bv) {
      bv = v;
      idx = i;
    }
  });
  return values.filter((v) => typeof v === 'number').length > 1 ? idx : -1;
}

@Component({
  selector: 'app-compare',
  imports: [RouterLink, FormsModule, LineChart, Icon, Sparkline, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="columns" area="research" />
        <div>
          <h1>Compare companies</h1>
          <p class="muted">Up to {{ MAX }} stocks side by side. The best value in a row is bold with a ✓; strategy comparisons live in the <a routerLink="/strategies/compare">Strategy lab</a>.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-compare" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <div class="filters">
      <span class="chips">
        @for (s of symbols(); track s) {
          <button type="button" class="chip" (click)="remove(s)" [title]="'Remove ' + s">{{ s }} ✕</button>
        }
      </span>
      @if (symbols().length < MAX) {
        <form class="inline-form" (ngSubmit)="add()">
          <label class="field">
            <span class="sr-only">Add a company</span>
            <input list="cmp-symbols" placeholder="Add a ticker…" [ngModel]="draft()" (ngModelChange)="draft.set($event)" name="draft" style="width: 11rem" />
            <datalist id="cmp-symbols">
              @for (c of companies(); track c.symbol) {
                <option [value]="c.symbol">{{ c.name }}</option>
              }
            </datalist>
          </label>
          <button type="submit" class="btn btn-sm" [disabled]="!draft().trim()">Add</button>
        </form>
      }
    </div>

    @if (symbols().length < 2) {
      <div class="empty-box">Pick at least two companies: add tickers above, or tick them on the <a routerLink="/companies">Companies</a> page.</div>
    } @else {
      <div class="card">
        <h3>Price vs benchmark, one year (closes indexed to 100)</h3>
        @if (chart().length) {
          <app-line-chart [series]="chart()" [refY]="100" refLabel="start = 100" [height]="280" [yFormat]="fmtIndex" label="Indexed closes of the compared companies over one year" />
        } @else {
          <p class="muted">Loading prices…</p>
        }
      </div>

      <div class="table-wrap">
        <table class="table compare-grid">
          <thead>
            <tr>
              <th></th>
              @for (s of slots(); track $index) {
                <th class="col-head">
                  @if (s.detail.hasValue()) {
                    <a [routerLink]="['/companies', s.detail.value()!.symbol]">{{ s.detail.value()!.symbol }}</a>
                    <div class="small muted" style="font-weight: 400">{{ s.detail.value()!.name }}</div>
                  } @else if (s.detail.error()) {
                    <span class="badge tone-bad">{{ s.symbol() }}: not found</span>
                  } @else {
                    {{ s.symbol() }} <span class="small muted">loading…</span>
                  }
                </th>
              }
            </tr>
          </thead>
          <tbody>
            @for (r of rows(); track r.label) {
              <tr>
                <td class="row-label">{{ r.label }}@if (r.help) { <app-help [text]="r.help" [topic]="r.topic ?? ''" [label]="r.label" /> }</td>
                @for (c of r.cells; track $index) {
                  <td [class.best]="r.best === $index">
                    @switch (c.kind) {
                      @case ('pct') {
                        @if (c.value !== null && c.value !== undefined) { {{ c.value | pct: 1 }} } @else { <span class="muted">—</span> }
                      }
                      @case ('signed') {
                        <app-delta [value]="c.value" kind="pct" [digits]="1" />
                      }
                      @case ('usd') {
                        {{ c.value | usd }}
                      }
                      @case ('prob') {
                        @if (c.f; as f) {
                          <a [routerLink]="['/forecasts', f.id]" class="plain-link">
                            <app-forecast-prob [f]="f" [withContext]="false" />
                            <div class="small" style="margin-top: 0.2rem"><app-lean [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" /> <span class="muted">as of {{ f.asOfDate }}</span></div>
                          </a>
                        } @else { <span class="muted">—</span> }
                      }
                      @case ('badge') {
                        @if (c.text) { <span class="badge" [class]="'badge tone-' + (c.tone ?? 'neutral')">{{ c.text }}</span> } @else { <span class="muted">—</span> }
                        @if (c.sub) { <div class="small muted">{{ c.sub }}</div> }
                      }
                      @case ('spark') {
                        @if (c.points?.length) {
                          <app-sparkline [points]="c.points!" /> <app-delta [value]="c.value" kind="pct" [digits]="1" />
                          @if (c.sub) { <div class="small muted">{{ c.sub }}</div> }
                        } @else { <span class="muted">—</span> }
                      }
                      @default {
                        {{ c.text ?? '—' }}
                        @if (c.sub) { <div class="small muted">{{ c.sub }}</div> }
                      }
                    }
                    @if (r.best === $index) { <span class="badge-best" title="Best in this row">✓</span> }
                  </td>
                }
              </tr>
            }
          </tbody>
        </table>
      </div>
      <p class="small muted" style="margin-top: 0.5rem">
        Forecast probabilities are model outputs (purple); prices, dividends and filed facts are recorded (grey). "Best" is
        judged per row: higher return, higher probability, more resolved hits, lower Brier, higher yield. Filed facts show the
        latest value of each concept reported by every compared company.
      </p>
    }
  `,
  styles: `
    .chip { cursor: pointer; font: inherit; font-size: 0.8rem; }
    .inline-form { display: flex; gap: 0.4rem; align-items: flex-end; }
    .plain-link:hover { text-decoration: none; }
    .compare-grid td { vertical-align: top; }
  `,
})
export class ComparePage {
  /** Query parameter: comma-separated tickers. */
  readonly symbols_ = input<string>('', { alias: 'symbols' });
  protected readonly MAX = MAX;
  protected readonly draft = signal('');
  protected readonly fmtIndex = (v: number) => v.toFixed(0);
  private readonly router = inject(Router);

  protected readonly symbols = computed(() => {
    const out: string[] = [];
    for (const s of (this.symbols_() ?? '').split(',')) {
      const u = s.trim().toUpperCase();
      if (u && !out.includes(u) && out.length < MAX) out.push(u);
    }
    return out;
  });

  private readonly companiesRes = httpResource<CompanySummary[]>(() => apiUrl.companies());
  protected readonly companies = computed(() => valueOf(this.companiesRes) ?? []);
  private readonly decisionsRes = httpResource<DecisionsResponse>(() => apiUrl.decisions());

  private slot(i: number): Slot {
    const symbol = () => this.symbols()[i] ?? null;
    return {
      symbol,
      detail: httpResource<CompanyDetail>(() => (symbol() ? apiUrl.company(symbol()!) : undefined)),
      prices: httpResource<PriceSeries>(() => (symbol() ? apiUrl.prices(symbol()!, YEAR_AGO) : undefined)),
      fc: httpResource<ForecastSummary[]>(() => (symbol() ? apiUrl.forecastsHistory(symbol()!) : undefined)),
      div: httpResource<DividendProfile>(() => (symbol() ? apiUrl.dividends(symbol()!) : undefined)),
    };
  }
  private readonly allSlots: Slot[] = [this.slot(0), this.slot(1), this.slot(2)];
  protected readonly slots = computed(() => this.allSlots.slice(0, this.symbols().length));

  protected readonly chart = computed<LineSeries[]>(() => {
    const colors = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'];
    const out: LineSeries[] = [];
    this.slots().forEach((s, i) => {
      const p = valueOf(s.prices);
      if (!p?.bars?.length) return;
      const bars = p.bars.filter((b) => b.close !== null && b.close > 0).sort((a, b) => a.date.localeCompare(b.date));
      if (!bars.length) return;
      const s0 = bars[0].close!;
      out.push({ key: p.symbol, label: p.symbol, color: colors[i], points: bars.map((b) => ({ x: dateMs(b.date), y: (b.close! / s0) * 100 })) });
    });
    return out;
  });

  protected readonly rows = computed<Row[]>(() => {
    const slots = this.slots();
    const details = slots.map((s) => valueOf(s.detail) ?? null);
    const summaries = slots.map((s) => this.companies().find((c) => c.symbol === (valueOf(s.detail)?.symbol ?? s.symbol())) ?? null);
    const decisions = valueOf(this.decisionsRes)?.decisions ?? [];
    const rows: Row[] = [];
    const push = (label: string, cells: Cell[], higherIsBetter: boolean | null, help?: string, topic?: string) =>
      rows.push({ label, cells, help, topic, best: best(cells.map((c) => c.value), higherIsBetter) });

    push('Sector · benchmark', details.map((d) => ({ kind: 'text', text: d ? `${d.sector}` : undefined, sub: d ? `vs ${d.benchmarkSymbol}${d.exchange ? ' · ' + d.exchange : ''}` : undefined })), null);
    push('Last close', summaries.map((c) => ({ kind: 'usd', value: c?.latestClose ?? null, sub: c?.latestCloseDate ?? undefined })), null);
    push(
      '1-year return vs benchmark',
      slots.map((s) => {
        const p = valueOf(s.prices);
        const bars = (p?.bars ?? []).filter((b) => b.close && b.benchmarkClose).sort((a, b) => a.date.localeCompare(b.date));
        if (bars.length < 2) return { kind: 'signed', value: null };
        const a = bars[0];
        const z = bars[bars.length - 1];
        const rs = z.close! / a.close! - 1;
        const rb = z.benchmarkClose! / a.benchmarkClose! - 1;
        return { kind: 'signed', value: rs - rb, sub: `stock ${fmtSignedPct(rs, 1)} · ${p!.benchmarkSymbol} ${fmtSignedPct(rb, 1)}` } as Cell;
      }),
      true,
      'Total price return over the last year minus the sector ETF\'s. Recorded prices.',
      'benchmark',
    );
    push(
      '21 days vs benchmark',
      summaries.map((c) => ({
        kind: 'spark',
        points: c?.recentCloses ?? [],
        value: typeof c?.change21d === 'number' && typeof c?.benchmarkChange21d === 'number' ? c.change21d - c.benchmarkChange21d : null,
        sub: c && typeof c.change21d === 'number' ? `stock ${fmtSignedPct(c.change21d, 1)} · ETF ${fmtSignedPct(c.benchmarkChange21d, 1)}` : undefined,
      })),
      true,
      'One forecast horizon of recorded closes: the stock minus its sector ETF.',
      'horizon',
    );
    for (const kind of ['BASELINE', 'AUGMENTED'] as ModelKind[]) {
      const cells = slots.map<Cell>((s) => {
        const f = latestByModel(valueOf(s.fc) ?? [])[kind] ?? null;
        return { kind: 'prob', f, value: f?.probability ?? null };
      });
      push(`${humanize(kind)} forecast`, cells, true, `Latest probability of beating the sector ETF over 21 trading days from the ${kind} model, with its lean.`, 'models');
    }
    push(
      'Resolved forecasts',
      slots.map((s) => {
        const done = (valueOf(s.fc) ?? []).filter((f) => f.outcome && f.issueMode === 'LIVE');
        if (!done.length) return { kind: 'text', text: 'none yet', value: null } as Cell;
        const hits = done.filter((f) => f.probability > 0.5 === f.outcome!.outcome).length;
        const brier = done.reduce((t, f) => t + f.outcome!.brier, 0) / done.length;
        return { kind: 'text', text: `${hits} of ${done.length} right`, sub: `mean Brier ${brier.toFixed(3)}`, value: hits / done.length } as Cell;
      }),
      true,
      'Live forecasts whose window has closed: how many called the direction right, and their mean Brier score (lower is better).',
      'hit-rate',
    );
    push(
      'AI decision',
      slots.map((s) => {
        const sym = valueOf(s.detail)?.symbol ?? s.symbol();
        const d: AiDecision | undefined = decisions.find((x) => x.symbol === sym);
        if (!d) return { kind: 'badge', value: null } as Cell;
        const tone: Tone = d.action === 'ENTER' ? 'good' : d.action === 'EXIT' ? 'bad' : d.action === 'HOLD' ? 'info' : 'neutral';
        return { kind: 'badge', text: humanize(d.action), tone, sub: `p = ${fmtPct(d.probability, 0)} · rank ${d.rank} · ${d.asOfDate}`, value: d.probability } as Cell;
      }),
      true,
      'The AI strategy\'s latest action and its 10-day probability.',
      'page-decisions',
    );
    push(
      'Dividend',
      slots.map((s) => {
        const d = valueOf(s.div);
        if (!d) return { kind: 'text', value: null } as Cell;
        const status = d.status === 'REGULAR' ? humanize(d.frequency) : humanize(d.status);
        return { kind: 'text', text: status, value: d.trailingYield ?? null, sub: d.trailingYield ? `${fmtPct(d.trailingYield, 2)} trailing yield · ${fmtUsd(d.ttmDividends)} a year` : undefined } as Cell;
      }),
      true,
      'Recorded cash dividends over the last 12 months.',
      'reported-estimated',
    );
    // filed facts reported by every compared company
    const factSets = details.map((d) => new Map((d?.keyFacts ?? []).map((f) => [f.concept, f])));
    if (details.every(Boolean) && factSets.length) {
      const common = [...factSets[0].keys()].filter((k) => factSets.every((m) => m.has(k))).slice(0, 8);
      for (const concept of common) {
        const facts = factSets.map((m) => m.get(concept) as KeyFact);
        push(
          facts[0].label || concept,
          facts.map((f) => ({ kind: 'text', text: fmtByUnit(f.value, f.unit), sub: `${f.fiscalPeriod ?? ''} ${f.periodEnd ?? ''} · ${f.formType ?? ''}`.trim(), value: f.value, unit: f.unit })),
          null,
        );
      }
    }
    return rows;
  });

  protected add(): void {
    const s = this.draft().trim().toUpperCase();
    if (!s) return;
    this.draft.set('');
    this.navigate([...this.symbols().filter((x) => x !== s), s].slice(0, MAX));
  }

  protected remove(symbol: string): void {
    this.navigate(this.symbols().filter((s) => s !== symbol));
  }

  private navigate(symbols: string[]): void {
    this.router.navigate([], { queryParams: { symbols: symbols.join(',') || null }, queryParamsHandling: 'merge' });
  }
}
