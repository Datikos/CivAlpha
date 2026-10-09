import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { LowerCasePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { FORMAT_PIPES } from '../../core/format';
import { Icon } from '../../shared/icon';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { HoldingsEditor } from './holdings-editor';
import { BuyIdeas, TrackRecord } from './ideas-and-record';
import { GLYPH, LABEL, LAYER_HINT, LAYER_LABEL, TONE, nyTime } from './portfolio.model';
import { PortfolioStore } from './portfolio.store';
import { PositionsTable } from './positions-table';

type Tab = 'positions' | 'ideas' | 'holdings' | 'record';

/**
 * My portfolio (ADR-0004/0005/0006): the summary in one row, what to do today, then tabs for the positions, the
 * book's buy ideas, the holdings editor and the track record. `?p=` picks the portfolio, `?tab=` the tab.
 */
@Component({
  selector: 'app-portfolio',
  providers: [PortfolioStore],
  imports: [FormsModule, LowerCasePipe, RouterLink, Icon, PositionsTable, HoldingsEditor, BuyIdeas, TrackRecord, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="wallet" area="strategy" />
        <div>
          <h1>My portfolio</h1>
          <p class="muted">What to do with each stock you hold today, how much, and when to look again. Research software, not investment advice: it places no orders.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-portfolio" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <button type="button" class="btn btn-primary" (click)="s.refresh()" [disabled]="s.busy() || !holdingsCount()"
                title="Advise on the portfolio as it is now, on the book's latest decision date">
          {{ s.busy() ? 'Working…' : 'Refresh advice' }}
        </button>
      </div>
    </div>

    <div class="pbar">
      @if (s.portfolios().length) {
        <label class="small">Portfolio
          <select (change)="s.choose($any($event.target).value)" [disabled]="s.busy()">
            @for (p of s.portfolios(); track p.id) {
              <option [value]="p.id" [selected]="p.id === s.shownId()">{{ p.name }} · {{ p.holdings }} holding{{ p.holdings === 1 ? '' : 's' }}</option>
            }
          </select>
        </label>
      }
      @if (newOpen()) {
        <form class="inline" (ngSubmit)="create()">
          <input name="pname" [(ngModel)]="newName" required maxlength="60" placeholder="Name, e.g. Pension" aria-label="Name of the new portfolio" />
          <button type="submit" class="btn btn-sm btn-primary" [disabled]="s.busy() || !newName.trim()">Create</button>
          <button type="button" class="btn btn-sm" (click)="newOpen.set(false)">Cancel</button>
        </form>
      } @else {
        <button type="button" class="btn btn-sm" (click)="newOpen.set(true)" [disabled]="s.busy()"><app-icon name="plus" [size]="14" /> New portfolio</button>
      }
      @if (s.advice(); as a) {
        @if (a.asOfDate) {
          <span class="small muted status">
            Advice on the close of <strong>{{ a.asOfDate | day }}</strong>
            @if (a.stale) { · <span class="chip tone-warn">newer decisions exist ({{ a.latestDecisionDate | day }}): Refresh advice</span> }
            @if (a.live; as lv) { · prices {{ lv.marketOpen ? 'live' : 'at the last quote' }}, {{ nyTime(lv.asOf) }} New York }
          </span>
        }
      }
    </div>

    @if (s.meta.meta(); as m) {
      @if (m.authMode === 'token' && !m.adminTokenRequired) {
        <div class="alert alert-warn small">
          <strong>No admin token is set:</strong> anyone who can reach this address can read your holdings. Set
          <code>CIVALPHA_ADMIN_TOKEN</code> in <code>.env</code> and restart the api.
        </div>
      }
    }
    @if (s.message(); as m) {
      <div class="alert" [class.alert-error]="m.error" [class.alert-ok]="!m.error" role="status">{{ m.text }}</div>
    }
    <app-status [res]="s.holdingsRes" what="your holdings" />

    @if (s.holdings() && !holdingsCount()) {
      <div class="empty-box">
        No holdings yet. Enter each stock you own below (symbol, shares, what you paid) and your cash, then press
        <strong>Refresh advice</strong>.
      </div>
      <section class="card"><app-holdings-editor /></section>
    } @else if (s.positions().length) {
      <!-- the summary, one row -->
      <div class="summary">
        <div class="stat" [class]="'stat tone-' + ((live()?.changeUsd ?? 0) >= 0 ? 'good' : 'bad')">
          <div class="stat-label">Value now <app-help text="Holdings at the live price where there is a quote (the advice close otherwise) plus cash. Today's change is against yesterday's close." topic="live-price" label="value now" /></div>
          <div class="stat-value">{{ s.valueNow() | usd }}</div>
          <div class="stat-sub">
            @if (live(); as lv) {
              <span [class]="lv.changeUsd >= 0 ? 'pos' : 'neg'">{{ lv.changeUsd >= 0 ? '▲' : '▼' }} {{ abs(lv.changeUsd) | usd }} today ({{ lv.changeUsd / (s.valueNow() - lv.changeUsd) | signedPct: 2 }})</span>
            }
            <div>{{ s.valueNow() - s.cash() | usd }} in stocks · {{ s.cash() | usd }} cash</div>
            @if (s.costBasis(); as c) {
              @if (c.known) {
                <div>gain <span [class]="c.value >= c.cost ? 'pos' : 'neg'">{{ c.value - c.cost | usd }} ({{ (c.value - c.cost) / c.cost | signedPct: 1 }})</span>@if (c.missing) { on {{ c.known }} of {{ c.known + c.missing }} }</div>
              } @else {
                <div><a [routerLink]="[]" [queryParams]="{ tab: 'holdings' }" queryParamsHandling="merge">enter what you paid</a> to see gain and loss</div>
              }
            }
          </div>
        </div>

        <div class="stat alloc-tile" [class]="'stat alloc-tile tone-' + (s.overCap().length ? 'warn' : 'info')">
          <div class="stat-label">Where the money is <app-help text="Share of the portfolio per holding, coloured by its advice: amber trim, red sell, blue hold, grey not covered or cash. One stock above 20% is the concentration the Trim rule limits." topic="position-sizing" label="allocation" /></div>
          <div class="alloc" role="img" [attr.aria-label]="allocLabel()">
            @for (g of s.allocation(); track g.key) {
              <span class="seg" [class]="'seg t-' + g.tone" [style.flex-grow]="g.weight" [title]="g.title">
                @if (g.weight >= 0.08) { <span class="seg-l">{{ g.label }} {{ g.weight | pct: 0 }}</span> }
              </span>
            }
          </div>
          <div class="stat-sub">
            @if (s.overCap().length) {
              <strong>{{ s.overCap().length }} above the 20% cap:</strong>
              @for (p of s.overCap(); track p.symbol) { {{ p.symbol }} {{ p.weightNow | pct: 0 }}{{ $last ? '' : ', ' }} }
            } @else { no stock above the 20% cap }
            @if (hasSectors()) {
              <div class="sectors" [title]="sectorTitle()">
                @for (x of s.sectors().slice(0, 2); track x.sector) { <span>{{ x.sector }} {{ x.weight | pct: 0 }}</span> }
                @if (s.sectors().length > 2) { <span class="muted">+{{ s.sectors().length - 2 }} more</span> }
              </div>
            }
          </div>
        </div>

        <div class="stat" [class]="'stat tone-' + (s.todo().length ? 'warn' : 'good')">
          <div class="stat-label">To do today <app-help text="Holdings whose advice is not Hold, plus the model's unproven opinions (grey). Trims and data checks need no forecast skill; Sell and Add wait for the live test." topic="page-portfolio" label="to do" /></div>
          <div class="stat-value">{{ s.todo().length }}<span class="unit">item{{ s.todo().length === 1 ? '' : 's' }} to look at</span></div>
          <div class="stat-sub">
            @for (k of kinds(); track k.key) { <span class="chip" [class]="'chip tone-' + k.tone">{{ k.glyph }} {{ k.label }} {{ k.n }}</span> }
            @if (!s.todo().length) { every holding is inside its size band }
          </div>
        </div>

        @if (s.advice(); as a) {
          <div class="stat" [class]="'stat tone-' + (a.liveTest.verdict === 'PASS' ? 'good' : a.liveTest.verdict === 'FAIL' ? 'bad' : 'warn')">
            <div class="stat-label">Can the model be trusted? <app-help text="The pre-registered live test of the book model: at least 500 resolved live forecasts, AUC at least 0.53 and a Brier score below the base rate. Until it passes, Sell and Add stay opinions and the headline stays Hold." topic="page-accuracy" label="live test" /></div>
            <div class="stat-value">{{ a.liveTest.verdict === 'PASS' ? 'Yes' : a.liveTest.verdict === 'FAIL' ? 'No' : 'Not yet' }}<span class="unit">live test {{ a.liveTest.verdict | lowercase }}</span></div>
            <div class="stat-sub">{{ a.liveTest.resolved }} of {{ a.liveTest.minResolved }} forecasts resolved · next review {{ a.advice[0]?.reviewOn | day }}</div>
            <app-progress [done]="a.liveTest.resolved" [total]="a.liveTest.minResolved" label="Resolved forecasts" />
          </div>
        }
      </div>

      <!-- what to do today -->
      <section class="card today">
        <h2>Today <span class="small muted">· most urgent first</span></h2>
        @if (s.todo().length) {
          <ul>
            @for (t of s.todo(); track t.symbol) {
              <li [class.opinion]="t.opinionOnly">
                <span class="badge" [class]="'badge tone-' + t.tone" [title]="layerHint[t.layer]">{{ glyph[t.action] }} {{ label[t.action] }}</span>
                <a [routerLink]="[]" [queryParams]="{ tab: 'positions', open: t.symbol }" queryParamsHandling="merge" class="sym" title="Open this row in Positions"><strong>{{ t.symbol }}</strong></a>
                <span class="txt">{{ t.text }}</span>
                <span class="chip small" [title]="layerHint[t.layer]">{{ t.opinionOnly ? 'unproven opinion' : layerLabel[t.layer] }}</span>
              </li>
            }
          </ul>
        } @else {
          <p class="muted">Nothing to do today: every holding is inside its size band and no data looks broken.</p>
        }
      </section>

      <nav class="tabs" aria-label="Portfolio sections">
        @for (t of tabs(); track t.key) {
          <a [routerLink]="[]" [queryParams]="{ tab: t.key }" queryParamsHandling="merge" [class.active]="current() === t.key">{{ t.label }}@if (t.n !== null) { <span class="n">{{ t.n }}</span> }</a>
        }
      </nav>

      @switch (current()) {
        @case ('positions') { <app-positions-table [positions]="s.positions()" [openSymbol]="open() ?? null" /> }
        @case ('ideas') { <app-buy-ideas /> }
        @case ('holdings') { <app-holdings-editor /> }
        @case ('record') { <app-track-record /> }
      }
    } @else if (s.holdings()) {
      <div class="alert alert-info">
        No advice yet for this portfolio. Press <strong>Refresh advice</strong>; it needs the AI decisions of a trading day
        (made by every pipeline run, see <a routerLink="/decisions">AI decisions</a>).
      </div>
      <section class="card"><app-holdings-editor /></section>
    }

    @if (s.advice(); as a) { <p class="small muted foot">{{ a.note }}</p> }
  `,
  styles: `
    .pbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem 0.9rem; margin: -0.25rem 0 1rem; }
    .pbar select { margin-left: 0.4rem; }
    .pbar .inline { display: flex; gap: 0.4rem; align-items: center; }
    .pbar .status { margin-left: auto; }
    .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 0.9rem; margin-bottom: 1rem; }
    .summary .stat-sub div { margin-top: 0.15rem; }
    .alloc { display: flex; height: 26px; border-radius: 6px; overflow: hidden; margin: 0.35rem 0 0.45rem; gap: 2px; }
    .seg { min-width: 3px; display: flex; align-items: center; overflow: hidden; }
    .seg-l { font-size: 0.7rem; font-weight: 600; padding: 0 0.35rem; white-space: nowrap; color: #fff; text-shadow: 0 1px 1px rgba(0,0,0,0.35); }
    .t-warn { background: color-mix(in srgb, var(--est-border) 85%, transparent); }
    .t-bad { background: var(--div-neg); }
    .t-good { background: var(--series-3); }
    .t-info { background: var(--series-1); }
    .t-neutral { background: color-mix(in srgb, var(--ink-muted) 60%, transparent); }
    .t-cash { background: var(--surface-3); }
    .t-cash .seg-l { color: var(--ink); text-shadow: none; }
    .sectors { display: flex; flex-wrap: wrap; gap: 0.1rem 0.6rem; margin-top: 0.3rem; font-size: 0.78rem; }
    .today h2 { margin: 0 0 0.5rem; font-size: 1rem; }
    .today ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.45rem; }
    .today li { display: grid; grid-template-columns: 9.5rem 4.5rem 1fr auto; gap: 0.6rem; align-items: baseline; }
    .today li.opinion { opacity: 0.8; }
    .today .badge { justify-self: start; }
    @media (max-width: 760px) { .today li { grid-template-columns: auto 1fr; } .today .txt { grid-column: 1 / -1; } }
    .tabs .n { margin-left: 0.35rem; font-size: 0.75rem; padding: 0 0.4rem; border-radius: 999px; background: var(--surface-3); }
    section.card { margin-bottom: 1rem; }
    .foot { margin-top: 1rem; }
  `,
})
export class PortfolioPage {
  protected readonly s = inject(PortfolioStore);
  /** `?p=<id>`: the chosen portfolio. */
  readonly p = input<string | undefined>(undefined);
  /** `?tab=`: positions (default), ideas, holdings, record. */
  readonly tab = input<string | undefined>(undefined);
  /** `?open=SYM`: the Positions row to open (links from the Today list). */
  readonly open = input<string | undefined>(undefined);
  protected readonly newOpen = signal(false);
  protected newName = '';
  protected readonly label = LABEL;
  protected readonly glyph = GLYPH;
  protected readonly layerLabel = LAYER_LABEL;
  protected readonly layerHint = LAYER_HINT;
  protected readonly nyTime = nyTime;
  protected readonly abs = Math.abs;

  constructor() {
    effect(() => {
      const n = Number(this.p());
      this.s.selectedId.set(this.p() && Number.isInteger(n) && n > 0 ? n : null);
    });
  }

  protected readonly current = computed<Tab>(() => {
    const t = this.tab();
    return t === 'ideas' || t === 'holdings' || t === 'record' ? t : 'positions';
  });
  protected readonly holdingsCount = computed(() => this.s.holdings()?.holdings.length ?? 0);
  protected readonly live = computed(() => this.s.advice()?.live ?? null);
  /** Advice rows carry the sector since 2026-10-09; older api builds do not, then the sector line stays hidden. */
  protected readonly hasSectors = computed(() => this.s.positions().some((p) => p.sector));
  protected readonly kinds = computed(() => {
    const m = new Map<string, { key: string; label: string; glyph: string; tone: string; n: number }>();
    for (const t of this.s.todo()) {
      const key = t.opinionOnly ? 'opinion' : t.action;
      const e = m.get(key) ?? { key, label: t.opinionOnly ? 'opinion' : LABEL[t.action], glyph: t.opinionOnly ? '◌' : GLYPH[t.action], tone: t.opinionOnly ? 'neutral' : TONE[t.action], n: 0 };
      e.n++;
      m.set(key, e);
    }
    return [...m.values()];
  });
  protected readonly tabs = computed(() => [
    { key: 'positions', label: 'Positions', n: this.s.positions().length },
    { key: 'ideas', label: 'Buy ideas', n: this.s.advice()?.ideas.length ?? 0 },
    { key: 'holdings', label: 'Holdings & cash', n: null },
    { key: 'record', label: 'Track record', n: null },
  ]);
  protected readonly sectorTitle = computed(() =>
    'By sector: ' + this.s.sectors().map((x) => `${x.sector} ${(x.weight * 100).toFixed(0)}% (${x.symbols.join(', ')})`).join('; '));
  protected readonly allocLabel = computed(() =>
    'Allocation: ' + this.s.allocation().map((g) => `${g.label} ${(g.weight * 100).toFixed(0)}%`).join(', '));

  protected create(): void {
    const name = this.newName.trim();
    this.s.createPortfolio(name, () => {
      this.newName = '';
      this.newOpen.set(false);
    });
  }
}
