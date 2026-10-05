import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { groupByCompany } from '../core/forecast-utils';
import { MetaService } from '../core/meta.service';
import { ForecastSummary } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ, leanOf } from '../shared/viz';

const WELCOME_KEY = 'civalpha.welcomed';

@Component({
  selector: 'app-current-forecasts',
  imports: [RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="pulse" area="forecast" />
        <div>
          <h1>Current forecasts</h1>
          <p class="muted">Latest published forecast per company for the most recent as-of date, both models.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-forecasts" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a routerLink="/forecasts/history" class="btn">Full history</a>
      </div>
    </div>

    @if (showWelcome()) {
      <div class="card welcome">
        <div class="welcome-text">
          <h3>New here? Start with the guide.</h3>
          <p class="muted">
            A five-minute tour of what the numbers mean, which page answers which question, and how to run the
            pipeline. Every <span class="help-btn" style="display: inline-flex; vertical-align: middle"><app-icon name="info" [size]="12" /></span>
            on the platform opens a short explanation.
          </p>
        </div>
        <div class="welcome-actions">
          <a routerLink="/guide" class="btn btn-primary"><app-icon name="book" [size]="16" /> Open the guide</a>
          <button type="button" class="btn" (click)="dismissWelcome()">Not now</button>
        </div>
      </div>
    }

    <div class="card">
      <h3>
        Forecast target
        <app-help text="One question for every stock: will its total return over the next 21 trading days beat its sector ETF? The answer is a probability, never a price target." topic="target" label="the forecast target" />
      </h3>
      <div class="target-def">{{ meta.target() }}</div>
      <p class="muted small" style="margin: 0.5rem 0 0">
        <app-model-tag kind="BASELINE" /> uses prices and filing fundamentals only;
        <app-model-tag kind="AUGMENTED" /> adds political / policy event features (monetary policy, trade &amp; tariff)
        weighted by each company's documented exposure.
      </p>
    </div>

    @if (summary(); as s) {
      <div class="stats wide">
        <div class="stat tone-info">
          <div class="stat-label">Companies</div>
          <div class="stat-value">{{ s.companies }}</div>
          <div class="stat-sub">with a forecast as of {{ s.asOf }}</div>
        </div>
        <div class="stat tone-forecast">
          <div class="stat-label">Horizon <app-help text="Forecasts look 21 trading days ahead (about one calendar month), from the close of the as-of date to the close 21 sessions later." topic="horizon" label="horizon" /></div>
          <div class="stat-value">{{ s.horizon }}<span class="unit">trading days</span></div>
          <div class="stat-sub">measured close to close</div>
        </div>
        <div class="stat" [class]="'stat ' + (s.replayed ? 'tone-warn' : 'tone-good')">
          <div class="stat-label">Issue mode <app-help text="LIVE forecasts were published before their outcome window opened. REPLAY forecasts were computed later from data available at the cutoff, so they are an honest reconstruction but not a live call." topic="live-replay" label="issue mode" /></div>
          <div class="stat-value">{{ s.replayed ? s.replayed + ' replayed' : 'all live' }}</div>
          <div class="stat-sub">{{ s.replayed ? 'of ' + s.total + ' forecasts' : s.total + ' forecasts published before their window' }}</div>
        </div>
        <div class="stat tone-neutral">
          <div class="stat-label">Where they lean <app-help text="A forecast leans above or below only when its whole uncertainty interval sits on one side of 50%. Otherwise the model cannot tell a direction and the forecast is a coin flip." topic="lean" label="forecast lean" /></div>
          <div class="stat-value">{{ s.flat }}<span class="unit">of {{ s.total }} are coin flips</span></div>
          <div class="lean-strip" role="img" [attr.aria-label]="s.above + ' lean above, ' + s.flat + ' coin flips, ' + s.below + ' lean below'">
            <span class="tone-good" [style.flex-grow]="s.above"></span>
            <span class="tone-neutral" [style.flex-grow]="s.flat"></span>
            <span class="tone-bad" [style.flex-grow]="s.below"></span>
          </div>
          <div class="lean-key small">
            <span class="lean tone-good">▲ {{ s.above }} above</span>
            <span class="lean tone-neutral">≈ {{ s.flat }} coin flip</span>
            <span class="lean tone-bad">▼ {{ s.below }} below</span>
          </div>
        </div>
      </div>
    }

    <app-status [res]="res" what="current forecasts" />

    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">
          No forecasts have been issued yet. Run the pipeline on the
          <a routerLink="/admin">Data &amp; pipeline</a> page.
        </div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Company</th>
                <th>Model <app-help text="Two models forecast every stock. BASELINE sees prices and filed fundamentals; AUGMENTED also sees policy events weighted by the company's documented exposure. Comparing them shows whether the event data helps." topic="models" label="models" /></th>
                <th>P(outperform) [interval] <app-help text="The probability that the stock beats its sector ETF, with a 10th to 90th percentile interval from bootstrap refits. The tiny bar shows the interval against the 50% midline." topic="interval" label="probability and interval" /></th>
                <th>Lean</th>
                <th>As of (data cutoff)</th>
                <th>Published</th>
                <th>Mode</th>
                <th class="num">Ver.</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (row of rows(); track row.symbol) {
                @for (cell of row.forecasts; track cell.kind; let first = $first) {
                  <tr>
                    @if (first) {
                      <td class="group" [attr.rowspan]="row.forecasts.length">
                        <a [routerLink]="['/companies', row.symbol]"><strong>{{ row.symbol }}</strong></a>
                        <div class="small muted">{{ row.companyName }}</div>
                        <div class="small muted">vs {{ row.benchmarkSymbol }}</div>
                      </td>
                    }
                    <td><app-model-tag [kind]="cell.kind" /></td>
                    @if (cell.f; as f) {
                      <td><app-forecast-prob [f]="f" [withContext]="false" /></td>
                      <td><app-lean [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" /></td>
                      <td class="nowrap">{{ f.asOfDate }}</td>
                      <td class="nowrap">{{ f.issuedAt | utc }}</td>
                      <td><app-issue-mode [mode]="f.issueMode" /></td>
                      <td class="num">v{{ f.version }}</td>
                      <td><a [routerLink]="['/forecasts', f.id]">Details</a></td>
                    } @else {
                      <td colspan="7" class="muted">No forecast</td>
                    }
                  </tr>
                }
              }
            </tbody>
          </table>
        </div>
      }
    }
  `,
})
export class CurrentForecastsPage {
  protected readonly meta = inject(MetaService);
  protected readonly res = httpResource<ForecastSummary[]>(() => apiUrl.forecastsCurrent());
  protected readonly rows = computed(() => groupByCompany(valueOf(this.res) ?? []));
  protected readonly showWelcome = signal(readWelcome());

  protected readonly summary = computed(() => {
    const list = valueOf(this.res) ?? [];
    if (!list.length) return null;
    const asOf = list.map((f) => f.asOfDate).sort().at(-1) ?? '—';
    let above = 0;
    let below = 0;
    let flat = 0;
    for (const f of list) {
      const l = leanOf(f.probability, f.probLow, f.probHigh);
      if (l === 'above') above++;
      else if (l === 'below') below++;
      else flat++;
    }
    return {
      companies: new Set(list.map((f) => f.symbol)).size,
      asOf,
      horizon: list[0].horizonTradingDays,
      replayed: list.filter((f) => f.issueMode === 'REPLAY').length,
      total: list.length,
      above,
      below,
      flat,
    };
  });

  protected dismissWelcome(): void {
    this.showWelcome.set(false);
    try {
      localStorage.setItem(WELCOME_KEY, '1');
    } catch {
      // storage unavailable: show again next time
    }
  }
}

function readWelcome(): boolean {
  try {
    return localStorage.getItem(WELCOME_KEY) !== '1';
  } catch {
    return true;
  }
}
