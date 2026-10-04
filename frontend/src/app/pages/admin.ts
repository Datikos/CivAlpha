import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable } from 'rxjs';
import { getAdminToken, setAdminToken } from '../core/admin-token';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import { CompanySummary, Job } from '../core/models';
import { UI } from '../shared/ui';

const ACTIVE = new Set(['RUNNING', 'PENDING', 'QUEUED', 'STARTED']);

@Component({
  selector: 'app-admin',
  imports: [FormsModule, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Data &amp; pipeline</h1>
        <p class="muted">Load data, run the pipeline, evaluate models and issue forecasts. Jobs run in the background.</p>
      </div>
    </div>

    @if (meta.meta()?.adminTokenRequired) {
      <div class="card" style="margin-bottom: 1rem">
        <h3>Admin token</h3>
        <p class="small muted">This server requires a token for admin and write actions. It is kept for this browser tab only.</p>
        <form class="actions" (submit)="saveToken(); $event.preventDefault()">
          <label class="sr-only" for="admin-token">Admin token</label>
          <input id="admin-token" type="password" autocomplete="off" [ngModel]="token()" (ngModelChange)="token.set($event)" name="token" />
          <button type="submit" class="btn">{{ hasToken() ? 'Update token' : 'Save token' }}</button>
        </form>
      </div>
    }

    @if (meta.meta()?.missingBenchmarks?.length; as count) {
      <div class="alert" role="note">
        @if (meta.meta()?.dataCutoff) {
          No prices yet for benchmark ETF{{ count > 1 ? 's' : '' }} {{ meta.meta()?.missingBenchmarks?.join(', ') }}:
          companies measured against {{ count > 1 ? 'them' : 'it' }} are left out of evaluation and forecasts.
        } @else {
          No price data yet. Load the demo dataset, or import a prices CSV that includes the stocks and the benchmark ETFs
          ({{ meta.meta()?.missingBenchmarks?.join(', ') }}), before evaluating or issuing forecasts.
        }
      </div>
    }

    @if (notice(); as n) {
      <div class="alert" [class.alert-ok]="n.ok" [class.alert-error]="!n.ok" role="status">{{ n.text }}</div>
    }

    <div class="grid-3">
      <div class="card">
        <h3>Pipeline</h3>
        <div class="actions">
          <button type="button" class="btn btn-primary" [disabled]="busy()" (click)="run('Demo load', api.loadDemo())">
            Load demo dataset
          </button>
          <p class="small muted">Synthetic prices, filings and events + full pipeline. Everything it creates is badged DEMO.</p>
          <button type="button" class="btn" [disabled]="busy()" (click)="run('Pipeline run', api.runPipeline())">Run pipeline</button>
          <p class="small muted">Ingest configured sources ({{ meta.meta()?.secMode ?? '…' }} SEC mode), evaluate, issue forecasts.</p>
          <button type="button" class="btn" [disabled]="busy()" (click)="run('Evaluation', api.evaluate())">Evaluate models</button>
          <p class="small muted">Walk-forward evaluation of BASELINE vs AUGMENTED.</p>
          <button type="button" class="btn" [disabled]="busy()" (click)="run('Outcome resolution', api.resolveOutcomes())">
            Resolve outcomes
          </button>
          <p class="small muted">Score forecasts whose 21-trading-day window has closed.</p>
        </div>
      </div>

      <div class="card">
        <h3>Issue forecasts</h3>
        <label class="field">
          As-of date (optional — defaults to latest data)
          <input type="date" [ngModel]="issueDate()" (ngModelChange)="issueDate.set($event ?? '')" />
        </label>
        <button
          type="button"
          class="btn"
          style="margin-top: 0.6rem"
          [disabled]="busy()"
          (click)="run('Forecast issue', api.issueForecasts(issueDate() || null))"
        >
          Issue forecasts
        </button>
        <p class="small muted" style="margin-top: 0.5rem">
          Forecasts for a past as-of date are published as REPLAY (after the data cutoff).
        </p>

        <h3 style="margin-top: 1.25rem">SEC ingest</h3>
        <form class="inline-form" (ngSubmit)="ingest()">
          <input
            name="sym"
            list="admin-symbols"
            [ngModel]="secSymbol()"
            (ngModelChange)="secSymbol.set($event)"
            placeholder="Symbol, e.g. AAPL"
            required
            style="width: 12rem"
          />
          <datalist id="admin-symbols">
            @for (c of companies(); track c.symbol) {
              <option [value]="c.symbol">{{ c.name }}</option>
            }
          </datalist>
          <button type="submit" class="btn" [disabled]="busy() || !secSymbol().trim()">Ingest filings</button>
        </form>
      </div>

      <div class="card">
        <h3>Import prices (CSV)</h3>
        <p class="small muted">
          Columns: <span class="mono">symbol,date,open,high,low,close,volume</span>. Imported prices are treated as
          recorded facts.
        </p>
        <input type="file" accept=".csv,text/csv" (change)="pickFile($event)" />
        <div style="margin-top: 0.6rem">
          <button type="button" class="btn" [disabled]="busy() || !file()" (click)="importCsv()">
            Upload {{ file()?.name ?? '' }}
          </button>
        </div>
      </div>
    </div>

    <div class="page-head" style="margin-top: 2rem">
      <h2 style="margin: 0">Jobs</h2>
      <div class="small muted">
        @if (anyActive()) {
          <span class="badge badge-run">auto-refreshing every 3 s</span>
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
              <tr><th>#</th><th>Type</th><th>Status</th><th>Started</th><th>Finished</th><th class="num">Duration</th><th></th></tr>
            </thead>
            <tbody>
              @for (j of sortedJobs(); track j.id) {
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
                    <button type="button" class="btn btn-sm" (click)="selectedId.set(j.id)" [attr.aria-pressed]="selected()?.id === j.id">
                      Log
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        @if (selected(); as j) {
          <h3 style="margin-top: 1rem">Job #{{ j.id }} log — {{ j.jobType | human }} ({{ j.status }})</h3>
          <pre class="log">{{ j.log || '(no output yet)' }}</pre>
        }
      }
    }
  `,
  styles: `
    .actions .btn { margin-top: 0.25rem; }
    .actions p { margin: 0.2rem 0 0.6rem; }
    .inline-form { display: flex; gap: 0.5rem; flex-wrap: wrap; }
  `,
})
export class AdminPage {
  protected readonly api = inject(ApiService);
  protected readonly meta = inject(MetaService);

  protected readonly jobs = httpResource<Job[]>(() => apiUrl.jobs());
  private readonly companiesRes = httpResource<CompanySummary[]>(() => apiUrl.companies());
  protected readonly companies = computed(() => valueOf(this.companiesRes) ?? []);

  protected readonly token = signal(getAdminToken());
  protected readonly hasToken = signal(!!getAdminToken());
  protected readonly busy = signal(false);
  protected readonly notice = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly issueDate = signal('');
  protected readonly secSymbol = signal('');
  protected readonly file = signal<File | null>(null);
  protected readonly selectedId = signal<number | null>(null);

  protected readonly sortedJobs = computed(() => [...(valueOf(this.jobs) ?? [])].sort((a, b) => b.id - a.id));
  protected readonly anyActive = computed(() => this.sortedJobs().some((j) => this.isActive(j)));
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
    // When jobs finish, refresh global metadata (e.g. the demo-data flag after a demo load).
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
    this.notice.set({ ok: true, text: this.hasToken() ? 'Admin token saved for this tab.' : 'Admin token cleared.' });
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
        this.notice.set({ ok: true, text: `${label} started as job #${job?.id ?? '?'} (${job?.status ?? 'submitted'}).` });
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
