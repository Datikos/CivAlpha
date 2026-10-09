import { LowerCasePipe } from '@angular/common';
import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DotWhisker, WhiskerItem } from '../../charts/dot-whisker';
import { FORMAT_PIPES, fmtSignedPct } from '../../core/format';
import { LiveQuotesService } from '../../core/live-quotes.service';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { GLYPH, LABEL, LAYER_LABEL, TONE } from './portfolio.model';
import { PortfolioStore } from './portfolio.store';

/** The recorded book's positions the owner does not hold, sized as the book sizes them, with today's price. */
@Component({
  selector: 'app-buy-ideas',
  imports: [RouterLink, LowerCasePipe, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    @if (store.advice(); as a) {
      <p class="small muted">
        The AI_SIZED book holds these and you do not. Size = 0.04 / 21-day volatility, at most 20% of your portfolio
        ({{ store.valueNow() | usd }} now).
        @if (a.liveTest.verdict === 'PASS') { The model has passed its live test. } @else { The model is unproven: read them as opinions, not instructions. }
      </p>
      <div class="table-wrap">
        <table class="table compact">
          <thead><tr><th>Stock</th><th>Book</th><th class="num">Price now</th><th class="num">p</th><th class="num">Calibrated</th><th class="num">Rank</th><th class="num">Size</th><th class="num">Amount</th><th class="num">≈ Shares</th></tr></thead>
          <tbody>
            @for (i of a.ideas; track i.symbol) {
              <tr>
                <td><a [routerLink]="['/companies', i.symbol]"><strong>{{ i.symbol }}</strong></a><span class="small muted name">{{ i.name }}</span></td>
                <td>{{ i.bookAction | lowercase }}</td>
                <td class="num">
                  @if (live.quote(i.symbol); as q) { {{ q.price | usd }} @if (q.change !== null) { <app-delta [value]="q.change" kind="pct" [digits]="1" /> } }
                  @else { <span class="muted">—</span> }
                </td>
                <td class="num">{{ i.probability | pct: 0 }}</td>
                <td class="num">{{ i.probabilityCalibrated | pct: 0 }}</td>
                <td class="num">{{ i.rank }}</td>
                <td class="num">{{ i.targetWeight | pct: 1 }}</td>
                <td class="num">{{ amount(i.targetWeight) | usd }}</td>
                <td class="num">{{ shares(i.symbol, i.targetWeight) }}</td>
              </tr>
            } @empty {
              <tr><td colspan="9" class="muted">The book holds nothing you do not already own.</td></tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
  styles: `.name { margin-left: 0.4rem; }`,
})
export class BuyIdeas {
  protected readonly store = inject(PortfolioStore);
  protected readonly live = inject(LiveQuotesService);

  protected amount(w: number): number {
    return w * this.store.valueNow();
  }

  protected shares(symbol: string, w: number): string {
    const q = this.live.quote(symbol);
    return q && q.price > 0 ? String(Math.floor(this.amount(w) / q.price)) : '—';
  }
}

/** What happened after each kind of advice: mean excess return over the sector ETF in the following 21 trading days. */
@Component({
  selector: 'app-track-record',
  imports: [DotWhisker, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    @if (store.track(); as t) {
      @if (items().length) {
        <app-dot-whisker [items]="items()" [refX]="0" refLabel="no difference" [betterIs]="null" [format]="fmt" label="Excess return after the advice, by action" />
      } @else {
        <p class="small muted">
          Every piece of advice is scored 21 trading days after it was given. A mean and its interval appear once
          {{ t.minResolved }} rows of an action have resolved; until then the table counts what is waiting.
        </p>
      }
      <table class="table compact">
        <thead><tr><th>Action</th><th>Layer</th><th class="num">Advised</th><th class="num">Resolved</th><th>Progress</th><th class="num">Mean excess</th><th class="num">95% interval</th></tr></thead>
        <tbody>
          @for (r of t.actions; track r.action) {
            @if (r.advised) {
              <tr>
                <td><span class="chip" [class]="'chip tone-' + tone[r.action]">{{ glyph[r.action] }} {{ label[r.action] }}</span></td>
                <td class="small">{{ layer[r.layer] }}</td>
                <td class="num">{{ r.advised }}</td>
                <td class="num">{{ r.resolved }}</td>
                <td><app-progress [done]="r.resolved" [total]="t.minResolved" [label]="'Resolved ' + label[r.action]" /></td>
                <td class="num">{{ r.meanExcess === null ? '—' : fmt(r.meanExcess) }}</td>
                <td class="num">{{ r.ci ? fmt(r.ci[0]) + ' to ' + fmt(r.ci[1]) : '—' }}</td>
              </tr>
            }
          }
        </tbody>
      </table>
      <p class="small muted">{{ t.note }}</p>
    }
  `,
})
export class TrackRecord {
  protected readonly store = inject(PortfolioStore);
  protected readonly label = LABEL;
  protected readonly tone = TONE;
  protected readonly glyph = GLYPH;
  protected readonly layer = LAYER_LABEL;
  protected readonly fmt = (v: number) => fmtSignedPct(v, 1);
  protected readonly items = computed<WhiskerItem[]>(() =>
    (this.store.track()?.actions ?? [])
      .filter((r) => r.meanExcess !== null)
      .map((r) => ({ row: r.action, label: LABEL[r.action], value: r.meanExcess!, lo: r.ci?.[0] ?? null, hi: r.ci?.[1] ?? null,
                     details: [`${r.resolved} resolved of ${r.advised} advised`, LAYER_LABEL[r.layer]] })),
  );
}
