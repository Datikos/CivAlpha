import { httpResource } from '@angular/common/http';
import { Component, computed, effect, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { latestByModel } from '../core/forecast-utils';
import { CompanySummary, DividendStatus, ForecastSummary, MODEL_KINDS, ModelKind } from '../core/models';
import { createSort } from '../core/sort';
import { Icon } from '../shared/icon';
import { SortTh } from '../shared/sort-th';
import { Sparkline } from '../shared/sparkline';
import { UI } from '../shared/ui';
import { Lean, VIZ, leanOf } from '../shared/viz';

const MAX_COMPARE = 3;

interface Row {
  c: CompanySummary;
  fc: { kind: ModelKind; f: ForecastSummary | null; ref: { id: number; asOfDate: string } | null; lean: Lean | null }[];
  excess21d: number | null;
}

@Component({
  selector: 'app-companies',
  imports: [RouterLink, FormsModule, Icon, SortTh, Sparkline, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="building" area="research" />
        <div>
          <h1>Companies</h1>
          <p class="muted">Nasdaq universe, each compared with its sector benchmark ETF. Click a column header to sort; tick up to {{ MAX }} to compare.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-companies" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <div class="filters">
      <label class="field">
        Filter
        <input type="search" placeholder="Symbol, name, sector, industry or tag" [ngModel]="q()" (ngModelChange)="q.set($event)" />
      </label>
      <label class="field">
        Sector
        <select [ngModel]="sector()" (ngModelChange)="sector.set($event)">
          <option value="">All sectors</option>
          @for (s of sectors(); track s) {
            <option [value]="s">{{ s }}</option>
          }
        </select>
      </label>
      @if (industries().length) {
        <label class="field">
          Industry
          <select [ngModel]="industry()" (ngModelChange)="industry.set($event)">
            <option value="">All industries</option>
            @for (s of industries(); track s) {
              <option [value]="s">{{ s | human }}</option>
            }
          </select>
        </label>
      }
      @if (tags().length) {
        <label class="field">
          <span class="nowrap">Tag <app-help text="Your own categories for a stock (a theme, a watchlist), set on the Universe page. Tags only group and filter; the models never see them." topic="tags" label="tags" /></span>
          <select [ngModel]="tag()" (ngModelChange)="tag.set($event)">
            <option value="">All tags</option>
            @for (t of tags(); track t.tag) {
              <option [value]="t.tag">{{ t.tag }} ({{ t.count }})</option>
            }
          </select>
        </label>
      }
      <label class="field">
        Lean
        <select [ngModel]="lean()" (ngModelChange)="lean.set($event)">
          <option value="">Any</option>
          <option value="above">▲ Leans above (either model)</option>
          <option value="below">▼ Leans below (either model)</option>
          <option value="flat">≈ Coin flip (both models)</option>
          <option value="none">No forecast</option>
        </select>
      </label>
      <label class="field">
        Dividends
        <select [ngModel]="div()" (ngModelChange)="div.set($event)">
          <option value="">Any</option>
          <option value="REGULAR">Regular payers</option>
          <option value="NOT_REGULAR">Irregular or suspended</option>
          <option value="NONE">No dividend</option>
        </select>
      </label>
      @if (anyFilter()) {
        <button type="button" class="btn btn-sm" (click)="clearFilters()">Clear filters</button>
      }
      <span class="small muted">{{ rows().length }} of {{ all().length }} companies</span>
    </div>

    <app-status [res]="res" what="companies" />

    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">
          @if (anyFilter()) {
            No companies match the filters.
          } @else {
            No companies yet. Add one on the <a routerLink="/universe">Universe</a> page.
          }
        </div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th class="pick"><span class="sr-only">Compare</span></th>
                <th sortKey="symbol" [sort]="sort" defaultDir="asc">Symbol</th>
                <th sortKey="name" [sort]="sort" defaultDir="asc">Name</th>
                <th sortKey="sector" [sort]="sort" defaultDir="asc">Sector</th>
                <th class="num" sortKey="close" [sort]="sort">Last close</th>
                <th sortKey="excess" [sort]="sort">21 days vs sector <app-help text="The stock's return over the last 21 trading days (one forecast horizon) minus its sector ETF's return over the same days. The sparkline is the stock's raw close." topic="benchmark" label="21-day move" /></th>
                <th sortKey="yield" [sort]="sort">Dividend <app-help text="From recorded cash dividends: the payment frequency when regular, irregular or suspended otherwise, with the trailing 12-month yield." topic="reported-estimated" label="dividend status" /></th>
                <th sortKey="pBase" [sort]="sort">Baseline forecast <app-help text="Latest probability that the stock beats its sector ETF over 21 trading days, from the model that sees prices and fundamentals only." topic="models" label="baseline forecast" /></th>
                <th sortKey="pAug" [sort]="sort">Augmented forecast <app-help text="The same probability from the model that also sees policy events weighted by the company's documented exposure." topic="models" label="augmented forecast" /></th>
                <th sortKey="pBook" [sort]="sort">Book model forecast <app-help text="The same 21-day probability from the recorded book's gradient-boosted model (39 inputs: prices, technical indicators, the filed report profile, insiders, earnings). Raw and uncalibrated, no interval." topic="models" label="book model forecast" /></th>
              </tr>
            </thead>
            <tbody>
              @for (r of rows(); track r.c.id) {
                <tr [class.row-current]="picked().includes(r.c.symbol)">
                  <td class="pick">
                    <input type="checkbox" [checked]="picked().includes(r.c.symbol)" (change)="toggle(r.c.symbol)"
                      [disabled]="!picked().includes(r.c.symbol) && picked().length >= MAX" [attr.aria-label]="'Compare ' + r.c.symbol" />
                  </td>
                  <td class="nowrap">
                    <a [routerLink]="['/companies', r.c.symbol]"><strong>{{ r.c.symbol }}</strong></a>
                  </td>
                  <td>
                    {{ r.c.name }}
                    @if (r.c.tags?.length) {
                      <div class="tags" style="margin-top: 0.15rem">
                        @for (t of r.c.tags; track t) {
                          <button type="button" class="chip tag" (click)="tag.set(tag() === t ? '' : t)" [attr.aria-pressed]="tag() === t" [title]="tag() === t ? 'Show all tags' : 'Only ' + t">{{ t }}</button>
                        }
                      </div>
                    }
                  </td>
                  <td>
                    {{ r.c.sector }}
                    <div class="small muted">{{ r.c.industry ? (r.c.industry | human) + ' · ' : '' }}vs {{ r.c.benchmarkSymbol }}</div>
                  </td>
                  <td class="num">
                    {{ r.c.latestClose | usd }}
                    <div class="small muted">{{ r.c.latestCloseDate ?? '' }}</div>
                  </td>
                  <td class="nowrap">
                    @if (r.c.recentCloses?.length) {
                      <span class="move">
                        <app-sparkline [points]="r.c.recentCloses!" [label]="r.c.symbol + ' last ' + r.c.recentCloses!.length + ' closes'" />
                        <span class="move-nums">
                          <app-delta [value]="r.excess21d" kind="pct" [digits]="1" />
                          <span class="small muted">stock <app-delta [value]="r.c.change21d" kind="pct" [digits]="1" /> · {{ r.c.benchmarkSymbol }} <app-delta [value]="r.c.benchmarkChange21d" kind="pct" [digits]="1" /></span>
                        </span>
                      </span>
                    } @else {
                      <span class="muted">—</span>
                    }
                  </td>
                  <td class="nowrap">
                    <app-dividend-badge [d]="r.c.dividend" />
                    @if (r.c.dividend?.trailingYield; as y) {
                      <div class="small muted" title="Dividends over the last 12 months / last close">{{ y | pct: 2 }} yield</div>
                    }
                  </td>
                  @for (cell of r.fc; track cell.kind) {
                    <td>
                      @if (cell.f; as f) {
                        <a [routerLink]="['/forecasts', f.id]" class="plain-link">
                          <app-forecast-prob [f]="f" [withContext]="false" />
                          <div class="small nowrap" style="margin-top: 0.2rem"><app-lean [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" /></div>
                          <div class="small muted nowrap">{{ f.horizonTradingDays }} d · as of {{ f.asOfDate }}</div>
                        </a>
                      } @else if (cell.ref; as ref) {
                        <a [routerLink]="['/forecasts', ref.id]">as of {{ ref.asOfDate }} — open</a>
                      } @else {
                        <span class="muted">—</span>
                      }
                    </td>
                  }
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    }

    @if (picked().length) {
      <div class="compare-bar" role="region" aria-label="Compare selection">
        <app-icon name="columns" [size]="18" />
        <span class="chips">
          @for (s of picked(); track s) {
            <button type="button" class="chip" (click)="toggle(s)" [title]="'Remove ' + s">{{ s }} ✕</button>
          }
        </span>
        <span class="small muted">{{ picked().length < 2 ? 'pick at least two' : picked().length + ' of ' + MAX }}</span>
        <a class="btn btn-primary" [class.disabled]="picked().length < 2" [routerLink]="picked().length >= 2 ? '/compare' : null" [queryParams]="{ symbols: picked().join(',') }">Compare</a>
        <button type="button" class="btn btn-sm" (click)="picked.set([])">Clear</button>
      </div>
    }
  `,
  styles: `
    .plain-link:hover { text-decoration: none; }
    .move { display: inline-flex; align-items: center; gap: 0.5rem; }
    td:nth-child(3) { max-width: 180px; }
    .move-nums { display: inline-flex; flex-direction: column; line-height: 1.3; }
    .filters { align-items: center; }
    .filters .field { min-width: 10rem; }
    .chip { cursor: pointer; font: inherit; font-size: 0.78rem; }
    .btn.disabled { opacity: 0.5; pointer-events: none; }
  `,
})
export class CompaniesPage {
  /** `/companies?tag=AI` (from a tag chip elsewhere) opens the list filtered to that tag. */
  readonly tagParam = input<string | undefined>(undefined, { alias: 'tag' });

  protected readonly MAX = MAX_COMPARE;
  protected readonly q = signal('');
  protected readonly sector = signal('');
  protected readonly industry = signal('');
  protected readonly tag = signal('');
  protected readonly lean = signal<'' | 'above' | 'below' | 'flat' | 'none'>('');
  protected readonly div = signal<'' | DividendStatus | 'NOT_REGULAR'>('');
  protected readonly picked = signal<string[]>([]);
  protected readonly sort = createSort('civalpha.sort.companies', { key: 'symbol', dir: 'asc' });

  protected readonly res = httpResource<CompanySummary[]>(() => apiUrl.companies());
  /** Full forecast summaries (interval, horizon, publication time) for the latest forecasts. */
  private readonly current = httpResource<ForecastSummary[]>(() => apiUrl.forecastsCurrent());

  protected readonly all = computed(() => valueOf(this.res) ?? []);
  protected readonly sectors = computed(() => [...new Set(this.all().map((c) => c.sector).filter(Boolean))].sort());
  protected readonly industries = computed(() =>
    [...new Set(this.all().map((c) => c.industry).filter((x): x is string => !!x))].sort(),
  );
  /** Every tag in use with its company count, most used first. */
  protected readonly tags = computed(() => {
    const counts = new Map<string, number>();
    for (const c of this.all()) for (const t of c.tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1);
    return [...counts].map(([tag, count]) => ({ tag, count })).sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
  });
  protected readonly anyFilter = computed(() => !!(this.q() || this.sector() || this.industry() || this.tag() || this.lean() || this.div()));

  constructor() {
    effect(() => {
      const t = this.tagParam()?.trim();
      if (t) this.tag.set(t);
    });
  }

  private readonly enriched = computed<Row[]>(() => {
    const cur = valueOf(this.current) ?? [];
    const bySymbol = new Map<string, ForecastSummary[]>();
    for (const f of cur) bySymbol.set(f.symbol, [...(bySymbol.get(f.symbol) ?? []), f]);
    return this.all().map((c) => {
      const latest = latestByModel(bySymbol.get(c.symbol) ?? []);
      return {
        c,
        fc: MODEL_KINDS.map((kind) => {
          const f = latest[kind] ?? null;
          return { kind, f, ref: c.latestForecasts?.[kind] ?? null, lean: f ? leanOf(f.probability, f.probLow, f.probHigh) : null };
        }),
        excess21d:
          typeof c.change21d === 'number' && typeof c.benchmarkChange21d === 'number' ? c.change21d - c.benchmarkChange21d : null,
      };
    });
  });

  protected readonly rows = computed(() => {
    const q = this.q().trim().toLowerCase();
    const div = this.div();
    const sector = this.sector();
    const industry = this.industry();
    const tag = this.tag().toLowerCase();
    const lean = this.lean();
    const filtered = this.enriched().filter((r) => {
      const c = r.c;
      if (q) {
        const hay = [c.symbol, c.name, c.sector ?? '', c.industry ?? '', ...(c.tags ?? [])].join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (sector && c.sector !== sector) return false;
      if (industry && c.industry !== industry) return false;
      if (tag && !(c.tags ?? []).some((t) => t.toLowerCase() === tag)) return false;
      const s = c.dividend?.status;
      if (div && !(div === 'NOT_REGULAR' ? s === 'IRREGULAR' || s === 'SUSPENDED' : s === div)) return false;
      if (lean) {
        const leans = r.fc.map((x) => x.lean).filter((x): x is Lean => !!x);
        if (lean === 'none') return leans.length === 0;
        if (!leans.length) return false;
        if (lean === 'flat') return leans.every((l) => l === 'flat');
        return leans.includes(lean);
      }
      return true;
    });
    return this.sort.order(filtered, {
      symbol: (r) => r.c.symbol,
      name: (r) => r.c.name,
      sector: (r) => r.c.sector,
      close: (r) => r.c.latestClose,
      excess: (r) => r.excess21d,
      yield: (r) => r.c.dividend?.trailingYield ?? null,
      pBase: (r) => r.fc[0].f?.probability ?? null,
      pAug: (r) => r.fc[1].f?.probability ?? null,
      pBook: (r) => r.fc[2]?.f?.probability ?? null,
    });
  });

  protected toggle(symbol: string): void {
    this.picked.update((p) => (p.includes(symbol) ? p.filter((s) => s !== symbol) : p.length < MAX_COMPARE ? [...p, symbol] : p));
  }

  protected clearFilters(): void {
    this.q.set('');
    this.sector.set('');
    this.industry.set('');
    this.tag.set('');
    this.lean.set('');
    this.div.set('');
  }
}
