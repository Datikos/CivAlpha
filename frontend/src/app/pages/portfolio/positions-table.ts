import { LowerCasePipe } from '@angular/common';
import { Component, computed, effect, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FORMAT_PIPES } from '../../core/format';
import { AdviceAction } from '../../core/models';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { GLYPH, LABEL, LAYER_HINT, LAYER_LABEL, Position, TONE, URGENCY, nyTime, sentence } from './portfolio.model';

type Filter = 'all' | 'act' | 'hold' | 'uncovered';
type SortKey = 'urgency' | 'weight' | 'today' | 'gain' | 'symbol';

/** Every holding in one row: action, price now and today's move, value, weight against its size, the book's
 * probability, gain against cost and the review date; a row opens to show why and what would change it. */
@Component({
  selector: 'app-positions-table',
  imports: [RouterLink, LowerCasePipe, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="bar">
      <div class="chips" role="group" aria-label="Show">
        @for (f of filters; track f.key) {
          <button type="button" class="chip" [class.on]="filter() === f.key" [attr.aria-pressed]="filter() === f.key"
                  (click)="filter.set(f.key)">{{ f.label }} <span class="count">{{ count(f.key) }}</span></button>
        }
      </div>
      <label class="small sort">Sort
        <select (change)="sort.set($any($event.target).value)">
          <option value="urgency">What to do first</option>
          <option value="weight">Largest first</option>
          <option value="today">Today's move</option>
          <option value="gain">Gain vs cost</option>
          <option value="symbol">Symbol</option>
        </select>
      </label>
    </div>

    <div class="table-wrap">
      <table class="table positions">
        <thead>
          <tr>
            <th>Stock</th>
            <th>Advice <app-help text="The action for this holding, decided by the first rule that fires: check the data, trim for size, sell or add (model opinion), hold. A grey 'unproven' chip is the model's opinion while its live test is pending; the headline then stays Hold." topic="page-portfolio" label="advice" /></th>
            <th class="num">Price <app-help text="Live price (Tiingo reference price, every few minutes in market hours) with the move since yesterday's close; the advice close when there is no quote." topic="live-price" label="price" /></th>
            <th class="num">Value</th>
            <th class="w">Weight vs size <app-help text="Share of the portfolio now. The tick is the size its volatility allows: 0.04 divided by its 21-day volatility, at most 20%. Above 1.5 times the tick, or above 20%, the advice is Trim." topic="position-sizing" label="weight" /></th>
            <th class="num">p <app-help text="The book model's probability that the stock beats its sector ETF over the next 21 trading days. Sell below 0.48, add at 0.55 or more." topic="page-decisions" label="probability" /></th>
            <th class="num">Gain <app-help text="Value now minus what you paid (shares × average cost). Empty when no average cost is entered." topic="page-portfolio" label="gain" /></th>
            <th>Look again</th>
          </tr>
        </thead>
        <tbody>
          @for (p of rows(); track p.symbol) {
            <tr class="main" [class.open]="open() === p.symbol" [class.flag]="p.headline !== 'HOLD'" (click)="toggle(p.symbol)">
              <td>
                <button type="button" class="caret" [attr.aria-expanded]="open() === p.symbol" [attr.aria-label]="'Details of ' + p.symbol">{{ open() === p.symbol ? '▾' : '▸' }}</button>
                @if (p.companyId) {
                  <a [routerLink]="['/companies', p.symbol]" (click)="$event.stopPropagation()"><strong>{{ p.symbol }}</strong></a>
                } @else {
                  <strong>{{ p.symbol }}</strong>
                }
                <div class="small muted name">{{ p.name ?? 'not in the universe' }}</div>
              </td>
              <td>
                <span class="badge" [class]="'badge tone-' + tone[p.headline]" [title]="layerHint[p.layer]">{{ glyph[p.headline] }} {{ label[p.headline] }}</span>
                @if (p.opinionOnly) {
                  <div><span class="chip tone-neutral small" [title]="'The model says ' + label[p.action] + '; not the headline until the live test passes'">model: {{ label[p.action] | lowercase }}, unproven</span></div>
                }
                @if (p.tradeShares !== null && p.action !== 'HOLD') {
                  <div class="small">{{ p.tradeShares < 0 ? 'sell' : 'buy' }} <strong>{{ abs(p.tradeShares) }}</strong>@if (p.tradeUsdNow !== null) { · {{ p.tradeUsdNow | usd }} }</div>
                }
              </td>
              <td class="num">
                @if (p.price !== null) {
                  {{ p.price | usd }}
                  <div class="small">
                    @if (p.changeToday !== null) { <app-delta [value]="p.changeToday" kind="pct" [digits]="1" /> }
                    @else { <span class="muted">{{ p.priceSource === 'close' ? 'close' : 'cost' }}</span> }
                    @if (p.stale) { <span class="chip tone-warn">stale</span> }
                  </div>
                } @else { <span class="muted">—</span> }
              </td>
              <td class="num">{{ p.valueNow | usd }}<div class="small muted">{{ p.shares | num: 2 }} sh</div></td>
              <td class="w">
                <div class="wline small"><span>{{ p.weightNow | pct: 1 }}</span>@if (p.target !== null) { <span class="muted">size {{ p.target | pct: 1 }}</span> }</div>
                <app-meter [value]="p.weightNow" [max]="0.5" [target]="p.target" targetLabel="size its volatility allows"
                           [tone]="p.action === 'TRIM' ? 'warn' : 'info'" [label]="'Weight of ' + p.symbol" />
              </td>
              <td class="num">
                @if (p.probability !== null) {
                  <span class="chip" [class]="'chip tone-' + pTone(p)">{{ p.probability | pct: 0 }}</span>
                } @else { <span class="muted">—</span> }
              </td>
              <td class="num">
                @if (p.gainUsd !== null) {
                  <span [class]="p.gainUsd >= 0 ? 'pos' : 'neg'">{{ p.gainUsd >= 0 ? '+' : '−' }}{{ abs(p.gainUsd) | usd }}</span>
                  <div class="small"><app-delta [value]="p.gainPct" kind="pct" [digits]="1" /></div>
                } @else { <span class="chip tone-warn small" title="Enter the average cost on the Holdings tab to see gain or loss">no cost</span> }
              </td>
              <td class="small">{{ p.reviewOn | day }}</td>
            </tr>
            @if (open() === p.symbol) {
              <tr class="detail">
                <td colspan="8">
                  <div class="grid">
                    <div>
                      <h4>Why</h4>
                      <p class="lead">{{ sentenceOf(p) }}</p>
                      <ul class="reasons small">
                        @for (x of p.reasons; track $index) {
                          <li><span class="g" [class]="'g tone-' + tone[x.action]">{{ glyph[x.action] }}</span> <span class="chip small">{{ layerLabel[x.layer] }}</span> {{ x.text }}</li>
                        } @empty {
                          <li><span class="g tone-info">●</span> Inside its size band, probability between the thresholds: nothing to do.</li>
                        }
                      </ul>
                    </div>
                    <div>
                      <h4>What would change it before {{ p.reviewOn | day }}</h4>
                      @if (p.triggers.length) {
                        <div class="chips">
                          @for (t of p.triggers; track t.action) {
                            <span class="chip" [class]="'chip tone-' + tone[t.action]">{{ label[t.action] }} if {{ t.when }}</span>
                          }
                        </div>
                      } @else {
                        <p class="small muted">No forecast for this stock, so no triggers. Add it on the Universe page to cover it.</p>
                      }
                      <dl class="facts small">
                        @if (p.probabilityCalibrated !== null) { <dt>Calibrated p</dt><dd>{{ p.probabilityCalibrated | pct: 0 }}</dd> }
                        @if (p.rank !== null) { <dt>Rank in the universe</dt><dd>{{ p.rank }}</dd> }
                        @if (p.bookAction) { <dt>The book itself</dt><dd>{{ p.bookAction | lowercase }}</dd> }
                        <dt>Average cost</dt><dd>{{ p.avgCost > 0 ? (p.avgCost | usd) : 'not entered' }}</dd>
                        @if (p.quotedAt) { <dt>Live quote</dt><dd>{{ nyTime(p.quotedAt) }} New York{{ p.stale ? ' (stale)' : '' }}</dd> }
                        @if (p.changed && p.previousHeadline) { <dt>Changed</dt><dd>was {{ label[p.previousHeadline] | lowercase }} the day before</dd> }
                        @if (p.outcome) { <dt>Since the advice</dt><dd>{{ p.outcome.excessReturn | signedPct: 1 }} vs sector by {{ p.outcome.windowEndDate | day }}</dd> }
                      </dl>
                      @if (p.companyId) {
                        <a class="small" [routerLink]="['/companies', p.symbol]">Open {{ p.symbol }}: chart, filings, forecasts →</a>
                      }
                    </div>
                  </div>
                </td>
              </tr>
            }
          } @empty {
            <tr><td colspan="8" class="muted">No holding matches this filter.</td></tr>
          }
        </tbody>
      </table>
    </div>
  `,
  styles: `
    .bar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 0.75rem; margin-bottom: 0.6rem; }
    .chips button.chip { cursor: pointer; border: 1px solid var(--border); background: var(--surface-2); }
    .chips button.chip.on { border-color: var(--accent); background: var(--accent-soft); font-weight: 600; }
    .count { opacity: 0.7; margin-left: 0.2rem; }
    .sort select { margin-left: 0.4rem; }
    .positions tr.main { cursor: pointer; }
    .positions tr.main.flag td:first-child { box-shadow: inset 3px 0 0 var(--est-border); }
    .positions tr.open td { background: var(--surface-2); }
    .caret { border: 0; background: none; color: var(--ink-muted); cursor: pointer; padding: 0 0.3rem 0 0; font-size: 0.8rem; }
    .name { margin-left: 1.05rem; max-width: 16rem; }
    td.w, th.w { min-width: 10rem; }
    .wline { display: flex; justify-content: space-between; gap: 0.5rem; }
    tr.detail td { background: var(--surface-2); padding: 0.75rem 1rem 1rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.25rem; }
    h4 { margin: 0 0 0.35rem; font-size: 0.85rem; }
    .lead { margin: 0 0 0.5rem; }
    .reasons { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.3rem; }
    .g { color: var(--tone-ink); }
    .facts { display: grid; grid-template-columns: auto 1fr; gap: 0.2rem 0.75rem; margin: 0.6rem 0; }
    .facts dt { color: var(--ink-muted); }
    .facts dd { margin: 0; }
  `,
})
export class PositionsTable {
  readonly positions = input.required<Position[]>();
  /** A row to open from outside (the Today list links with ?open=SYM). */
  readonly openSymbol = input<string | null>(null);
  protected readonly label = LABEL;
  protected readonly tone = TONE;
  protected readonly glyph = GLYPH;
  protected readonly layerLabel = LAYER_LABEL;
  protected readonly layerHint = LAYER_HINT;
  protected readonly nyTime = nyTime;
  protected readonly sentenceOf = sentence;
  protected readonly abs = Math.abs;
  protected readonly filter = signal<Filter>('all');
  protected readonly sort = signal<SortKey>('urgency');
  protected readonly open = signal<string | null>(null);
  protected readonly filters: { key: Filter; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'act', label: 'To look at' },
    { key: 'hold', label: 'Hold' },
    { key: 'uncovered', label: 'Not covered' },
  ];

  private matches(p: Position, f: Filter): boolean {
    if (f === 'act') return p.headline !== 'HOLD' || p.opinionOnly;      // the same items as the Today list
    if (f === 'hold') return p.headline === 'HOLD';
    if (f === 'uncovered') return p.headline === 'NOT_COVERED';
    return true;
  }

  protected count(f: Filter): number {
    return this.positions().filter((p) => this.matches(p, f)).length;
  }

  protected readonly rows = computed(() => {
    const list = this.positions().filter((p) => this.matches(p, this.filter()));
    const by: Record<SortKey, (a: Position, b: Position) => number> = {
      urgency: (a, b) => URGENCY.indexOf(a.headline as AdviceAction) - URGENCY.indexOf(b.headline as AdviceAction)
        || Number(b.opinionOnly) - Number(a.opinionOnly) || b.weightNow - a.weightNow,
      weight: (a, b) => b.weightNow - a.weightNow,
      today: (a, b) => (b.changeToday ?? -9) - (a.changeToday ?? -9),
      gain: (a, b) => (b.gainPct ?? -9) - (a.gainPct ?? -9),
      symbol: (a, b) => a.symbol.localeCompare(b.symbol),
    };
    return [...list].sort(by[this.sort()]);
  });

  constructor() {
    effect(() => {
      const s = this.openSymbol();
      if (s) {
        this.open.set(s.toUpperCase());
        this.filter.set('all');
      }
    });
  }

  protected toggle(symbol: string): void {
    this.open.set(this.open() === symbol ? null : symbol);
  }

  protected pTone(p: Position): string {
    if (p.probability === null) return 'neutral';
    if (p.exitP !== null && p.probability < p.exitP) return 'bad';
    if (p.entryP !== null && p.probability >= p.entryP) return 'good';
    return 'info';
  }
}
