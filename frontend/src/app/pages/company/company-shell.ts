import { Component, computed, effect, inject, input } from '@angular/core';
import { LiveQuotesService, nyTime } from '../../core/live-quotes.service';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { FORMAT_PIPES } from '../../core/format';
import { Icon } from '../../shared/icon';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { CompanyContext } from './company-context';

@Component({
  selector: 'app-company-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="crumbs"><a routerLink="/companies">Companies</a> / {{ symbol() }}</div>
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="building" area="research" />
        <div>
          @if (ctx.detail(); as c) {
            <h1>
              {{ c.name }} <span class="muted">({{ c.symbol }})</span>
            </h1>
            @if (live.quote(c.symbol); as q) {
              <p class="live-head">
                <span class="live-price">{{ q.price | usd }}</span>
                @if (q.change !== null) { <app-delta [value]="q.change" kind="pct" [digits]="2" /> }
                <span class="small muted">{{ live.marketOpen() ? 'live' : 'last quote' }} {{ nyTime(q.quotedAt) }} New York · vs close {{ q.prevClose | usd }}</span>
                @if (q.stale) { <span class="chip tone-warn" title="Older than 15 minutes while the market is open">stale</span> }
                <app-help text="Tiingo's reference price (the last IEX trade or the mid), refreshed every few minutes in market hours, against yesterday's close. Display only: forecasts and decisions use daily closes." topic="live-price" label="live price" />
              </p>
            }
            <p class="muted">
              {{ c.sector }}
              @if (c.industry) {
                · {{ c.industry | human }}
              }
              · benchmark <strong>{{ c.benchmarkSymbol }}</strong>
              @if (c.exchange) {
                · {{ c.exchange }}
              }
              @for (t of c.tags ?? []; track t) {
                <a class="chip tag" [routerLink]="['/companies']" [queryParams]="{ tag: t }" [title]="'Companies tagged ' + t">{{ t }}</a>
              }
            </p>
          } @else {
            <h1>{{ symbol() }}</h1>
          }
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-companies" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>
    @if (aliasNote(); as n) {
      <div class="alert alert-info">{{ n }}</div>
    }
    <app-status [res]="ctx.company" what="company" />

    <nav class="tabs" aria-label="Company sections">
      <a [routerLink]="['/companies', symbol()]" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }"
        >Overview</a
      >
      <a [routerLink]="['/companies', symbol(), 'filings']" routerLinkActive="active">Filings &amp; financials</a>
      <a [routerLink]="['/companies', symbol(), 'exposure']" routerLinkActive="active">Policy exposure</a>
      <a [routerLink]="['/companies', symbol(), 'forecasts']" routerLinkActive="active">Forecasts</a>
    </nav>
    <router-outlet />
  `,
  styles: `
    .live-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0.1rem 0 0.35rem; }
    .live-price { font-size: 1.35rem; font-weight: 600; }
  `,
})
export class CompanyShell {
  readonly symbol = input.required<string>();
  protected readonly ctx = inject(CompanyContext);
  protected readonly live = inject(LiveQuotesService);
  protected readonly nyTime = nyTime;

  constructor() {
    effect(() => this.ctx.symbol.set(this.symbol()));
  }

  protected readonly aliasNote = computed(() => {
    const c = this.ctx.detail();
    const s = this.symbol();
    if (!c || !s || c.symbol.toUpperCase() === s.toUpperCase()) return null;
    return `${s.toUpperCase()} is a historical ticker of ${c.name}; it currently trades as ${c.symbol}.`;
  });
}
