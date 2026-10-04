import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { MetaService } from './core/meta.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <a class="skip" href="#main">Skip to content</a>
    <header class="topbar">
      <div class="container topbar-inner">
        <a routerLink="/" class="brand" aria-label="CivAlpha home">
          <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 18 L9 11 L13 14 L21 5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
            <circle cx="21" cy="5" r="2.2" fill="currentColor" />
          </svg>
          CivAlpha
        </a>
        <nav class="nav" aria-label="Main">
          @for (l of links; track l.path) {
            <a
              [routerLink]="l.path"
              routerLinkActive="active"
              [routerLinkActiveOptions]="{ exact: l.exact }"
              >{{ l.label }}</a
            >
          }
        </nav>
      </div>
    </header>

    @if (meta.demo()) {
      <div class="demo-banner" role="status">
        <div class="container">
          <strong>DEMO DATA</strong> — synthetic prices, filings and events generated for demonstration. Not real
          market data.
        </div>
      </div>
    }
    @if (meta.resource.error()) {
      <div class="api-down" role="alert">
        <div class="container">
          The CivAlpha API is not reachable right now. Pages will show errors until the backend is available.
        </div>
      </div>
    }

    <main id="main" class="container main">
      <router-outlet />
    </main>

    <footer class="footer">
      <div class="container">
        @if (meta.meta(); as m) {
          <ul class="disclaimers">
            @for (d of m.disclaimers; track $index) {
              <li>{{ d }}</li>
            }
          </ul>
          <p class="muted small">
            Data cutoff {{ m.dataCutoff ?? '—' }} · SEC mode {{ m.secMode }} · LLM extraction
            {{ m.llmEnabled ? 'enabled' : 'disabled' }}
          </p>
        } @else {
          <ul class="disclaimers">
            <li>Research software. Not investment advice.</li>
          </ul>
        }
        <p class="muted small">
          Recorded facts are shown neutrally; model estimates carry an
          <span class="badge badge-estimated">ESTIMATED</span> badge; forecasts are shown as
          <span class="prob">probabilities</span> with horizon, interval and publication time.
        </p>
      </div>
    </footer>
  `,
  styles: `
    .skip {
      position: absolute;
      left: -999px;
    }
    .skip:focus {
      left: 8px;
      top: 8px;
      z-index: 10;
      background: var(--surface);
      padding: 0.3rem 0.6rem;
    }
    .topbar {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 20;
    }
    .topbar-inner {
      display: flex;
      align-items: center;
      gap: 1.25rem;
      min-height: 54px;
      flex-wrap: wrap;
    }
    .brand {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      font-weight: 700;
      font-size: 1.1rem;
      color: var(--ink);
      text-decoration: none;
    }
    .brand svg {
      color: var(--series-1);
    }
    .nav {
      display: flex;
      gap: 0.15rem;
      overflow-x: auto;
      flex: 1;
      min-width: 0;
    }
    .nav a {
      padding: 0.35rem 0.7rem;
      border-radius: 6px;
      color: var(--ink-2);
      white-space: nowrap;
      font-weight: 500;
      font-size: 0.92rem;
    }
    .nav a:hover {
      text-decoration: none;
      background: var(--surface-2);
    }
    .nav a.active {
      color: var(--ink);
      background: var(--surface-2);
    }
    @media (max-width: 640px) {
      .nav {
        flex-wrap: wrap;
        flex-basis: 100%;
        padding-bottom: 0.4rem;
      }
    }
    .demo-banner {
      background: var(--demo-bg);
      color: var(--demo-ink);
      font-size: 0.9rem;
      padding: 0.45rem 0;
      border-bottom: 1px solid var(--border);
    }
    .api-down {
      background: var(--bad-bg);
      color: var(--bad-ink);
      font-size: 0.88rem;
      padding: 0.4rem 0;
    }
    .main {
      min-height: 70vh;
      padding-bottom: 2rem;
    }
    .footer {
      border-top: 1px solid var(--border);
      background: var(--surface);
      padding: 1.25rem 0 1.5rem;
      font-size: 0.86rem;
      color: var(--ink-2);
    }
    .disclaimers {
      margin: 0 0 0.6rem;
      padding-left: 1.1rem;
    }
  `,
})
export class App {
  protected readonly meta = inject(MetaService);
  protected readonly links = [
    { path: '/', label: 'Current forecasts', exact: true },
    { path: '/companies', label: 'Companies', exact: false },
    { path: '/events', label: 'Policy events', exact: false },
    { path: '/forecasts/history', label: 'Forecast history', exact: false },
    { path: '/accuracy', label: 'Accuracy', exact: false },
    { path: '/admin', label: 'Data & pipeline', exact: false },
  ];
}
