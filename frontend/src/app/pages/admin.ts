import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { getAdminToken, setAdminToken } from '../core/admin-token';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import { CompanySummary, Job } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

const ACTIVE = new Set(['RUNNING', 'PENDING', 'QUEUED', 'STARTED']);
const JOBS_SHOWN = 10;

/**
 * One primary action (run the whole pipeline) and a status strip; every single step is behind "Advanced".
 */
@Component({
  selector: 'app-admin',
  imports: [FormsModule, RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="database" area="data" />
        <div>
          <h1>Data &amp; pipeline</h1>
          <p class="muted">
            Keep the data fresh and the forecasts current. Everything runs as a background job.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-admin" class="btn btn-help"><app-icon name="help" [size]="16" /> How to use this</a>
        <a routerLink="/guide" fragment="checklist" class="btn btn-help"><app-icon name="checklist" [size]="16" /> Setup checklist</a>
      </div>
    </div>

    @if (meta.meta()?.adminTokenRequired) {
      <div class="card">
        <h3>Admin token</h3>
        <p class="small muted">
          This server requires a token for admin and write actions. It is kept for this browser tab
          only.
        </p>
        <form class="inline-form" (submit)="saveToken(); $event.preventDefault()">
          <label class="sr-only" for="admin-token">Admin token</label>
          <input
            id="admin-token"
            type="password"
            autocomplete="off"
            [ngModel]="token()"
            (ngModelChange)="token.set($event)"
            name="token"
          />
          <button type="submit" class="btn">
            {{ hasToken() ? 'Update token' : 'Save token' }}
          </button>
        </form>
      </div>
    }

    @if (notice(); as n) {
      <div class="alert" [class.alert-ok]="n.ok" [class.alert-error]="!n.ok" role="status">
        {{ n.text }}
      </div>
    }

    @if (meta.meta(); as m) {
      <div class="stats">
        <div class="stat" [class]="'stat ' + (m.dataCutoff ? 'tone-info' : 'tone-warn')">
          <div class="stat-label">Data cutoff <app-help text="The latest trading day with prices in the database. Forecasts, backtests and the time machine cannot see past it." topic="as-of" label="data cutoff" /></div>
          <div class="stat-value">{{ m.dataCutoff ?? '—' }}</div>
          <div class="small muted">latest price date</div>
        </div>
        <div class="stat" [class]="'stat ' + (priceProvider() ? 'tone-good' : 'tone-warn')">
          <div class="stat-label">Prices</div>
          <div class="stat-value">{{ priceProvider() ? m.priceProvider : 'manual' }}</div>
          <div class="small muted">
            {{ priceProvider() ? '✓ automatic provider' : 'CSV import only' }}
          </div>
        </div>
        <div class="stat" [class]="'stat ' + (m.secConfigured ? 'tone-good' : 'tone-warn')">
          <div class="stat-label">SEC EDGAR</div>
          <div class="stat-value">{{ m.secConfigured ? 'ready' : 'off' }}</div>
          <div class="small muted">
            {{ m.secConfigured ? '✓ filings & fundamentals' : 'set SEC_USER_AGENT in .env' }}
          </div>
        </div>
        <div class="stat" [class]="'stat ' + (lastPipeline() ? (lastPipeline()!.status === 'SUCCEEDED' ? 'tone-good' : lastPipeline()!.status === 'FAILED' ? 'tone-bad' : 'tone-info') : 'tone-warn')">
          <div class="stat-label">Last pipeline run</div>
          @if (lastPipeline(); as j) {
            <div class="stat-value">
              <span
                class="badge"
                [class.badge-ok]="j.status === 'SUCCEEDED'"
                [class.badge-fail]="j.status === 'FAILED'"
                [class.badge-run]="isActive(j)"
                >{{ j.status }}</span
              >
            </div>
            <div class="small muted">{{ j.finishedAt ?? j.startedAt | utc }}</div>
          } @else {
            <div class="stat-value">never</div>
            <div class="small muted">run it below</div>
          }
        </div>
      </div>
    }

    <div class="card hero">
      <div class="hero-text">
        <h2 style="margin: 0 0 0.3rem">Update everything</h2>
        <p class="muted" style="margin: 0">
          One run downloads prices, ingests new SEC filings, refreshes macro and policy data,
          re-evaluates both models, issues today's forecasts, scores closed windows and updates the
          strategy lab. Safe to run any time; nothing already loaded is repeated.
        </p>
        @if (meta.meta()?.missingBenchmarks?.length; as count) {
          <div class="alert alert-warn small" style="margin: 0.75rem 0 0">
            No prices yet for benchmark ETF{{ count > 1 ? 's' : '' }}
            {{ meta.meta()?.missingBenchmarks?.join(', ') }}; companies measured against
            {{ count > 1 ? 'them' : 'it' }} are skipped until prices arrive.
          </div>
        }
        @if (!priceProvider()) {
          <p class="small muted" style="margin: 0.75rem 0 0">
            No automatic price provider: add
            <span class="mono">CIVALPHA_PRICE_PROVIDER=yahoo</span> (no key, unofficial) or
            <span class="mono">CIVALPHA_PRICE_PROVIDER=tiingo</span> +
            <span class="mono">TIINGO_API_KEY</span> to <span class="mono">.env</span> and restart —
            or import a CSV under Advanced.
          </p>
        }
      </div>
      <div class="hero-action">
        @if (runningPipeline(); as j) {
          <button type="button" class="btn btn-primary btn-lg" disabled>
            <span class="spinner" aria-hidden="true"></span> Running… job #{{ j.id }}
          </button>
        } @else {
          <button
            type="button"
            class="btn btn-primary btn-lg"
            [disabled]="busy()"
            (click)="run('Pipeline run', api.runPipeline())"
          >
            <app-icon name="pulse" [size]="18" /> Run pipeline
          </button>
        }
      </div>
    </div>

    <details class="collapsible">
      <summary>Advanced: run a single step</summary>
      <div class="steps">
        @if (priceProvider()) {
          <div class="step">
            <div>
              <strong>Update prices</strong>
              <div class="small muted">
                Daily bars, dividends and splits for every active stock and benchmark ETF from
                {{ meta.meta()?.priceProvider }}.
              </div>
            </div>
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('Price update', api.syncPrices())"
            >
              Update
            </button>
          </div>
        }
        <div class="step">
          <div>
            <strong>Import prices from CSV</strong>
            <div class="small muted">
              Columns <span class="mono">symbol,date,open,high,low,close,volume</span>; imported
              prices are recorded facts.
            </div>
          </div>
          <div class="inline-form">
            <input
              type="file"
              accept=".csv,text/csv"
              (change)="pickFile($event)"
              aria-label="Prices CSV"
            />
            <button type="button" class="btn" [disabled]="busy() || !file()" (click)="importCsv()">
              Upload
            </button>
          </div>
        </div>
        <div class="step">
          <div>
            <strong>Ingest SEC filings for one stock</strong>
            <div class="small muted">
              10-K, 10-Q and relevant 8-K filings, XBRL fundamentals and policy-exposure passages.
            </div>
          </div>
          <form class="inline-form" (ngSubmit)="ingest()">
            <input
              name="sym"
              list="admin-symbols"
              [ngModel]="secSymbol()"
              (ngModelChange)="secSymbol.set($event)"
              placeholder="Ticker"
              required
              style="width: 8rem"
              aria-label="Ticker"
            />
            <datalist id="admin-symbols">
              @for (c of companies(); track c.symbol) {
                <option [value]="c.symbol">{{ c.name }}</option>
              }
            </datalist>
            <button type="submit" class="btn" [disabled]="busy() || !secSymbol().trim()">
              Ingest
            </button>
          </form>
        </div>
        <div class="step">
          <div>
            <strong>Issue forecasts</strong>
            <div class="small muted">
              For the latest data, or for a past as-of date (then published as REPLAY).
            </div>
          </div>
          <div class="inline-form">
            <input
              type="date"
              [ngModel]="issueDate()"
              (ngModelChange)="issueDate.set($event ?? '')"
              aria-label="As-of date"
            />
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('Forecast issue', api.issueForecasts(issueDate() || null))"
            >
              Issue
            </button>
          </div>
        </div>
        <div class="step">
          <div>
            <strong>Evaluate models</strong>
            <div class="small muted">Walk-forward evaluation of BASELINE vs AUGMENTED.</div>
          </div>
          <button
            type="button"
            class="btn"
            [disabled]="busy()"
            (click)="run('Evaluation', api.evaluate())"
          >
            Evaluate
          </button>
        </div>
        <div class="step">
          <div>
            <strong>Resolve outcomes</strong>
            <div class="small muted">Score forecasts whose 21-trading-day window has closed.</div>
          </div>
          <button
            type="button"
            class="btn"
            [disabled]="busy()"
            (click)="run('Outcome resolution', api.resolveOutcomes())"
          >
            Resolve
          </button>
        </div>
        <div class="step">
          <div>
            <strong>Strategy lab</strong>
            <div class="small muted">
              Backtest every classic rule and the AI on one window, then record today's AI
              decisions.
            </div>
          </div>
          <div class="inline-form">
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('Strategy backtest', api.backtestStrategies())"
            >
              Backtest
            </button>
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('AI decisions', api.decide())"
            >
              AI decisions
            </button>
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('Doubler study', api.doublerStudy())"
            >
              Doubler study
            </button>
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('Setup playbook', api.setupStudy())"
            >
              Setup playbook
            </button>
            <button
              type="button"
              class="btn"
              [disabled]="busy()"
              (click)="run('Signal health', api.signalStudy())"
            >
              Signal health
            </button>
          </div>
        </div>
      </div>
      <p class="small muted" style="margin: 0.75rem 0 0">
        New stocks are added on the <a routerLink="/universe">Universe</a> page, which can start
        their data load right away.
      </p>
    </details>

    <div class="page-head" style="margin-top: 1.5rem">
      <h2 style="margin: 0">Jobs</h2>
      <div class="small muted inline-form" style="align-items: center">
        @if (anyActive()) {
          <span class="badge badge-run">auto-refreshing</span>
        }
        <button type="button" class="btn btn-sm" (click)="jobs.reload()">Refresh</button>
      </div>
    </div>
    <app-status [res]="jobs" what="jobs" />
    @if (jobs.hasValue()) {
      @if (!sortedJobs().length) {
        <div class="empty-box">No jobs have run yet.</div>
      } @else {
        <div class="table-wrap">
          <table class="table compact">
            <thead>
              <tr>
                <th>#</th>
                <th>Type</th>
                <th>Status</th>
                <th>Started</th>
                <th>Finished</th>
                <th class="num">Duration</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (j of shownJobs(); track j.id) {
                <tr [class.row-current]="selected()?.id === j.id">
                  <td>{{ j.id }}</td>
                  <td>{{ j.jobType | human }}</td>
                  <td>
                    <span
                      class="badge"
                      [class.badge-ok]="j.status === 'SUCCEEDED'"
                      [class.badge-fail]="j.status === 'FAILED'"
                      [class.badge-run]="isActive(j)"
                      >{{ j.status }}</span
                    >
                  </td>
                  <td class="nowrap">{{ j.startedAt | utc }}</td>
                  <td class="nowrap">{{ j.finishedAt | utc }}</td>
                  <td class="num">{{ duration(j) }}</td>
                  <td>
                    <button
                      type="button"
                      class="btn btn-sm"
                      (click)="selectedId.set(j.id)"
                      [attr.aria-pressed]="selected()?.id === j.id"
                    >
                      Log
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        @if (sortedJobs().length > JOBS_SHOWN) {
          <p class="small" style="margin: 0.5rem 0 0">
            <button type="button" class="btn btn-sm" (click)="showAllJobs.set(!showAllJobs())">
              {{ showAllJobs() ? 'Show recent only' : 'Show all ' + sortedJobs().length + ' jobs' }}
            </button>
          </p>
        }
        @if (selected(); as j) {
          <h3 style="margin-top: 1rem">
            Job #{{ j.id }} log — {{ j.jobType | human }} ({{ j.status }})
          </h3>
          <pre class="log">{{ j.log || '(no output yet)' }}</pre>
        }
      }
    }
  `,
  styles: `
    .inline-form {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      align-items: center;
    }
    .stat .small {
      margin-top: 0.1rem;
    }
    .hero {
      display: flex;
      gap: 1.5rem;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      border-left: 4px solid var(--accent);
    }
    .hero-text {
      flex: 1 1 420px;
      min-width: 0;
    }
    .hero-action {
      flex: none;
    }
    .btn-lg {
      font-size: 1rem;
      padding: 0.7rem 1.3rem;
      border-radius: 10px;
    }
    .spinner {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 2px solid currentColor;
      border-right-color: transparent;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }
    .steps {
      display: flex;
      flex-direction: column;
    }
    .step {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
      padding: 0.75rem 0;
      border-top: 1px solid var(--grid);
    }
    .step > div:first-child {
      flex: 1 1 320px;
      min-width: 0;
    }
  `,
})
export class AdminPage {
  protected readonly api = inject(ApiService);
  protected readonly meta = inject(MetaService);

  protected readonly jobs = httpResource<Job[]>(() => apiUrl.jobs());
  private readonly companiesRes = httpResource<CompanySummary[]>(() => apiUrl.companies());
  protected readonly companies = computed(() => valueOf(this.companiesRes) ?? []);
  protected readonly priceProvider = computed(() => {
    const p = this.meta.meta()?.priceProvider;
    return !!p && p !== 'none';
  });

  protected readonly token = signal(getAdminToken());
  protected readonly hasToken = signal(!!getAdminToken());
  protected readonly busy = signal(false);
  protected readonly notice = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly issueDate = signal('');
  protected readonly secSymbol = signal('');
  protected readonly file = signal<File | null>(null);
  protected readonly selectedId = signal<number | null>(null);

  protected readonly sortedJobs = computed(() =>
    [...(valueOf(this.jobs) ?? [])].sort((a, b) => b.id - a.id),
  );
  protected readonly anyActive = computed(() => this.sortedJobs().some((j) => this.isActive(j)));
  protected readonly JOBS_SHOWN = JOBS_SHOWN;
  protected readonly showAllJobs = signal(false);
  protected readonly shownJobs = computed(() =>
    this.showAllJobs() ? this.sortedJobs() : this.sortedJobs().slice(0, JOBS_SHOWN),
  );
  protected readonly lastPipeline = computed(
    () => this.sortedJobs().find((j) => j.jobType === 'PIPELINE_RUN') ?? null,
  );
  protected readonly runningPipeline = computed(() => {
    const j = this.lastPipeline();
    return j && this.isActive(j) ? j : null;
  });
  protected readonly selected = computed(() => {
    const list = this.sortedJobs();
    const id = this.selectedId();
    return (id !== null ? list.find((j) => j.id === id) : undefined) ?? list[0] ?? null;
  });

  constructor() {
    // Poll every 3 s while any job is running.
    effect((onCleanup) => {
      if (!this.anyActive()) return;
      const t = setInterval(() => this.jobs.reload(), 3000);
      onCleanup(() => clearInterval(t));
    });
    // When jobs finish, refresh global metadata (e.g. the data cutoff after a price update).
    let wasActive = false;
    effect(() => {
      const active = this.anyActive();
      if (wasActive && !active) this.meta.resource.reload();
      wasActive = active;
    });
  }

  protected saveToken(): void {
    setAdminToken(this.token().trim());
    this.hasToken.set(!!this.token().trim());
    this.notice.set({
      ok: true,
      text: this.hasToken() ? 'Admin token saved for this tab.' : 'Admin token cleared.',
    });
    this.jobs.reload();
  }

  protected isActive(j: Job): boolean {
    return ACTIVE.has(j.status);
  }

  protected duration(j: Job): string {
    if (!j.startedAt) return '—';
    const end = j.finishedAt ? Date.parse(j.finishedAt) : Date.now();
    const s = Math.max(0, Math.round((end - Date.parse(j.startedAt)) / 1000));
    if (!Number.isFinite(s)) return '—';
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  }

  protected run(label: string, call: Observable<Job>): void {
    this.busy.set(true);
    this.notice.set(null);
    call.subscribe({
      next: (job) => {
        this.busy.set(false);
        this.notice.set({
          ok: true,
          text: `${label} started as job #${job?.id ?? '?'} (${job?.status ?? 'submitted'}).`,
        });
        if (job?.id !== undefined) this.selectedId.set(job.id);
        this.jobs.reload();
      },
      error: (e) => {
        this.busy.set(false);
        this.notice.set({ ok: false, text: `${label} failed: ${errorMessage(e)}` });
        this.jobs.reload();
      },
    });
  }

  protected ingest(): void {
    const s = this.secSymbol().trim().toUpperCase();
    if (s) this.run(`SEC ingest for ${s}`, this.api.ingestSec(s));
  }

  protected pickFile(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.file.set(input.files?.[0] ?? null);
  }

  protected importCsv(): void {
    const f = this.file();
    if (f) this.run(`Price import (${f.name})`, this.api.importPrices(f));
  }
}
