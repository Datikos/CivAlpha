import { Component, computed, effect, inject, input } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Icon } from '../../shared/icon';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { CompanyContext } from './company-context';

@Component({
  selector: 'app-company-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Icon, ...UI, ...VIZ],
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
            <p class="muted">
              {{ c.sector }} · benchmark <strong>{{ c.benchmarkSymbol }}</strong>
              @if (c.exchange) {
                · {{ c.exchange }}
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
})
export class CompanyShell {
  readonly symbol = input.required<string>();
  protected readonly ctx = inject(CompanyContext);

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
