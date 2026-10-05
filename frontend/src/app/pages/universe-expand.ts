import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, errorMessage } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import { DiscoverResponse, DiscoveredCompany, ExpandResponse } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

/** Public-float thresholds offered, in US dollars. */
const FLOATS: { value: number; label: string }[] = [
  { value: 5e8, label: '$500 million' },
  { value: 1e9, label: '$1 billion' },
  { value: 2e9, label: '$2 billion' },
  { value: 5e9, label: '$5 billion' },
  { value: 1e10, label: '$10 billion' },
  { value: 2.5e10, label: '$25 billion' },
  { value: 1e11, label: '$100 billion' },
];
const LIMITS = [25, 50, 100, 200, 500];
const EXCHANGES = ['Nasdaq', 'NYSE', 'CBOE'];

function money(v: number): string {
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)} T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)} B`;
  return `$${(v / 1e6).toFixed(0)} M`;
}

/**
 * Breadth: find many companies at once from SEC data (exchange list + reported public float), preview them, and
 * add the lot in one background job. The preview is what gets added: the rows are sent back as they were shown.
 */
@Component({
  selector: 'app-universe-expand',
  imports: [FormsModule, RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <section class="card" aria-labelledby="expand-title">
      <h3 id="expand-title">Expand the universe <app-help text="A signal too small to see on 40 stocks may be measurable on 400. Candidates come from SEC data only: every company listed on the chosen exchanges, ranked by the public float its last 10-K reported. Each one is added with a sector suggested from its SIC code." topic="breadth" label="breadth" /></h3>
      <p class="small muted">
        Pick the exchanges and a minimum size, preview the largest companies not yet tracked, then add them all with one
        tag. Public float is the value of shares held by non-affiliates as stated in the latest 10-K, so it lags the
        market by up to a year.
      </p>
      <form class="form-grid expand-form" (ngSubmit)="preview()">
        <fieldset class="field">
          <legend>Exchanges</legend>
          <div class="checks">
            @for (x of exchanges; track x) {
              <label><input type="checkbox" [checked]="picked().includes(x)" (change)="toggle(x)" /> {{ x }}</label>
            }
          </div>
        </fieldset>
        <label class="field">
          Public float at least
          <select [ngModel]="minFloat()" (ngModelChange)="minFloat.set(+$event)" name="minFloat">
            @for (f of floats; track f.value) {
              <option [value]="f.value">{{ f.label }}</option>
            }
          </select>
        </label>
        <label class="field">
          At most
          <select [ngModel]="limit()" (ngModelChange)="limit.set(+$event)" name="limit">
            @for (n of limits; track n) {
              <option [value]="n">{{ n }} companies</option>
            }
          </select>
        </label>
        <label class="field">
          Tag for the batch
          <input name="tag" [ngModel]="tag()" (ngModelChange)="tag.set($event)" placeholder="e.g. large caps" maxlength="40" />
        </label>
        <div class="field actions">
          <button type="submit" class="btn btn-primary" [disabled]="busy() || !picked().length">
            <app-icon name="search" [size]="16" /> {{ busy() && !result() ? 'Searching SEC…' : 'Preview' }}
          </button>
        </div>
      </form>

      @if (error(); as e) {
        <div class="alert alert-error small" style="margin-top: 0.5rem">{{ e }}</div>
      }

      @if (result(); as r) {
        <div class="summary">
          <div class="stats wide">
            <div class="stat tone-info">
              <div class="stat-label">Listed on {{ r.exchanges.join(', ') }}</div>
              <div class="stat-value">{{ r.listed | num }}</div>
              <div class="stat-sub">companies with a ticker in SEC's exchange list</div>
            </div>
            <div class="stat tone-forecast">
              <div class="stat-label">Public float ≥ {{ moneyOf(r.minPublicFloat) }}</div>
              <div class="stat-value">{{ r.matched | num }}</div>
              <div class="stat-sub">{{ r.alreadyTracked | num }} already tracked · frames {{ r.frames.join(', ') }}</div>
            </div>
            <div class="stat tone-good">
              <div class="stat-label">Ready to add</div>
              <div class="stat-value">{{ r.candidates.length | num }}</div>
              <div class="stat-sub">largest first, one ticker per company</div>
            </div>
          </div>
          @for (n of r.notes; track n) {
            <div class="alert alert-warn small" style="margin: 0.4rem 0 0">{{ n }}</div>
          }
          @if (r.candidates.length) {
            <div class="table-wrap" style="margin-top: 0.75rem; max-height: 420px; overflow: auto">
              <table class="table compact">
                <thead>
                  <tr><th>#</th><th>Ticker</th><th>Company</th><th>Exchange</th><th class="num">Public float</th><th>As of</th></tr>
                </thead>
                <tbody>
                  @for (c of r.candidates; track c.cik; let i = $index) {
                    <tr>
                      <td class="muted">{{ i + 1 }}</td>
                      <td><span class="mono">{{ c.symbol }}</span></td>
                      <td>{{ c.name }}</td>
                      <td>{{ c.exchange }}</td>
                      <td class="num"><app-cell-bar [value]="c.publicFloat" [max]="maxFloat()" [text]="moneyOf(c.publicFloat)" /></td>
                      <td class="nowrap small muted">{{ c.floatAsOf ?? '—' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <div class="confirm">
              <label class="small"><input type="checkbox" [checked]="ingestSec()" (change)="ingestSec.set(!ingestSec())" /> ingest SEC filings now (seconds to minutes each; otherwise the next pipeline run does it)</label>
              <label class="small"><input type="checkbox" [checked]="syncPrices()" (change)="syncPrices.set(!syncPrices())" /> download prices</label>
              <button type="button" class="btn btn-primary" (click)="expand()" [disabled]="busy()">
                <app-icon name="globe" [size]="16" /> Add {{ r.candidates.length }} {{ r.candidates.length === 1 ? 'company' : 'companies' }}
              </button>
              <span class="small muted">An ambiguous SIC code adds the company under its likeliest sector with the tag "{{ r.sectorReviewTag }}"; filter the table below by that tag to confirm them.</span>
            </div>
          } @else {
            <p class="muted" style="margin-top: 0.5rem">Nothing new matches: every company this large on these exchanges is already tracked, or the threshold is too high.</p>
          }
        </div>
      }

      @if (queued(); as q) {
        <div class="alert alert-ok" role="status" style="margin-top: 0.75rem">
          Expansion of {{ q.count }} companies queued as job #{{ q.job.id }}. Follow it on <a routerLink="/admin">Data &amp; pipeline</a>;
          the companies appear in the table below as they are added.
          @if (q.notes.length) {
            <ul class="small" style="margin: 0.4rem 0 0">
              @for (n of q.notes; track n) { <li>{{ n }}</li> }
            </ul>
          }
        </div>
      }
    </section>
  `,
  styles: `
    .expand-form { align-items: end; }
    .expand-form fieldset { border: 0; padding: 0; margin: 0; }
    .expand-form legend { font-size: 0.8rem; color: var(--ink-muted); margin-bottom: 0.25rem; }
    .checks { display: flex; gap: 0.75rem; flex-wrap: wrap; padding: 0.45rem 0; }
    .checks label { display: inline-flex; gap: 0.3rem; align-items: center; }
    .actions { display: flex; align-items: flex-end; }
    .summary { margin-top: 0.75rem; }
    .confirm { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; margin-top: 0.75rem; }
  `,
})
export class UniverseExpand {
  private readonly api = inject(ApiService);
  protected readonly meta = inject(MetaService);
  /** Fired when an expansion job is queued, so the page can refresh its table. */
  readonly expanded = output<ExpandResponse>();
  /** `/universe?discover=1` runs the preview with the default filters right away. */
  readonly autoPreview = input(false);

  protected readonly exchanges = EXCHANGES;
  protected readonly floats = FLOATS;
  protected readonly limits = LIMITS;
  protected readonly picked = signal<string[]>(['Nasdaq', 'NYSE']);
  protected readonly minFloat = signal(2e9);
  protected readonly limit = signal(100);
  protected readonly tag = signal('');
  protected readonly ingestSec = signal(false);
  protected readonly syncPrices = signal(true);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly result = signal<DiscoverResponse | null>(null);
  protected readonly queued = signal<ExpandResponse | null>(null);
  protected readonly maxFloat = computed(() => Math.max(1, ...(this.result()?.candidates ?? []).map((c) => c.publicFloat)));

  constructor() {
    effect(() => {
      if (this.autoPreview() && !this.result() && !this.busy()) this.preview();
    });
  }

  protected moneyOf(v: number): string {
    return money(v);
  }

  protected toggle(x: string): void {
    this.picked.update((p) => (p.includes(x) ? p.filter((e) => e !== x) : [...p, x]));
  }

  protected preview(): void {
    if (!this.picked().length) return;
    this.busy.set(true);
    this.error.set(null);
    this.queued.set(null);
    this.result.set(null);
    this.api.discoverCompanies(this.picked(), this.minFloat(), this.limit()).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.result.set(r);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(`Discovery failed: ${errorMessage(e)}`);
      },
    });
  }

  protected expand(): void {
    const r = this.result();
    if (!r || !r.candidates.length) return;
    this.busy.set(true);
    this.error.set(null);
    const rows: DiscoveredCompany[] = r.candidates;
    this.api.expandUniverse({ candidates: rows, tag: this.tag().trim() || null, ingestSec: this.ingestSec(), syncPrices: this.syncPrices() }).subscribe({
      next: (q) => {
        this.busy.set(false);
        this.result.set(null);
        this.queued.set(q);
        this.expanded.emit(q);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(`Expansion failed: ${errorMessage(e)}`);
      },
    });
  }
}
