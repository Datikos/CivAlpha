import { httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import { CompanyProfile, UniverseCompany, UniverseResponse } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';

interface Draft {
  symbol: string;
  name: string;
  cik: string;
  sector: string;
  industry: string;
  benchmarkSymbol: string;
  memberSince: string;
}

interface Notice {
  ok: boolean;
  text: string;
  steps?: string[];
  jobs?: boolean;
}

const TICKER = /^[A-Za-z0-9.\-]{1,10}$/;

@Component({
  selector: 'app-universe',
  imports: [FormsModule, RouterLink, Icon, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Universe</h1>
        <p class="muted">
          Stocks the platform tracks and forecasts. Changes take effect immediately and are kept as
          history: removing a stock ends its membership on a date, so past forecasts and backtests
          are unaffected.
        </p>
      </div>
    </div>

    @if (notice(); as n) {
      <div class="alert" [class.alert-ok]="n.ok" [class.alert-error]="!n.ok" role="status">
        {{ n.text }}
        @if (n.steps?.length) {
          <ul class="small" style="margin: 0.4rem 0 0">
            @for (s of n.steps; track s) {
              <li>{{ s }}</li>
            }
          </ul>
        }
        @if (n.jobs) {
          <div class="small" style="margin-top: 0.4rem">
            Follow progress on <a routerLink="/admin">Data &amp; pipeline</a>.
          </div>
        }
      </div>
    }

    <section class="card add-card" aria-labelledby="add-title">
      <h3 id="add-title">Add a stock</h3>
      <p class="small muted">
        Type the ticker. Name, CIK, exchange, sector and benchmark ETF are filled in from SEC EDGAR
        for you to review, then one click adds the stock and starts loading its data.
      </p>
      <form class="ticker-form" (ngSubmit)="lookup()">
        <label class="sr-only" for="add-ticker">Ticker</label>
        <input
          id="add-ticker"
          class="ticker-input"
          name="ticker"
          [ngModel]="ticker()"
          (ngModelChange)="ticker.set($event)"
          placeholder="Ticker, e.g. NVDA"
          autocomplete="off"
          autocapitalize="characters"
          spellcheck="false"
          [disabled]="looking()"
        />
        <button type="submit" class="btn btn-primary" [disabled]="looking() || !validTicker()">
          <app-icon name="search" [size]="16" />
          {{ looking() ? 'Looking up…' : 'Look up' }}
        </button>
      </form>

      @if (looking()) {
        <div class="skeleton" aria-live="polite" aria-label="Looking up on SEC EDGAR">
          <div class="skeleton-line"></div>
          <div class="skeleton-line"></div>
          <div class="skeleton-line"></div>
        </div>
      }

      @if (profile(); as p) {
        <div class="review" [class.review-estimated]="p.sectorConfidence === 'review'">
          <div class="review-head">
            <div>
              <div class="review-name">
                {{ p.name || 'Unknown company' }}
                <span class="mono review-sym">{{ p.symbol }}</span>
              </div>
              <div class="small muted">
                @if (p.exchange) {
                  {{ p.exchange }} ·
                }
                @if (p.cik) {
                  CIK {{ p.cik }}
                } @else {
                  no CIK found
                }
                @if (p.sicDescription) {
                  · {{ p.sicDescription }} (SIC {{ p.sic }})
                }
                @if (p.formerNames.length) {
                  · formerly {{ p.formerNames.join(', ') }}
                }
              </div>
            </div>
            @if (p.source) {
              <span class="badge badge-fact" [title]="p.source">SEC EDGAR</span>
            }
          </div>

          @for (w of p.warnings; track w) {
            <div class="alert alert-warn small" style="margin: 0.5rem 0 0">{{ w }}</div>
          }
          @if (p.existing; as ex) {
            <div class="alert alert-info" style="margin: 0.5rem 0 0">
              Already in the database as
              <a [routerLink]="['/companies', ex.symbol]"
                ><strong>{{ ex.symbol }}</strong></a
              >{{
                ex.active
                  ? ' (active member).'
                  : ' — removed from the universe; restore it in the table below.'
              }}
            </div>
          }
          @if (p.sectorNote) {
            <p class="small" style="margin: 0.6rem 0 0">
              <span class="badge badge-estimated">SUGGESTED</span> {{ p.sectorNote }}
            </p>
          }
          <div class="sector-pick" role="group" aria-label="Sector">
            @for (a of sectorChoices(); track a.sector) {
              <button
                type="button"
                class="sector-chip"
                [class.on]="draft.sector === a.sector"
                [class.hint]="p.sectorConfidence === 'review' && a.sector === p.sector"
                (click)="pickSector(a.sector, a.benchmarkSymbol)"
                [attr.aria-pressed]="draft.sector === a.sector"
              >
                {{ a.sector }} <span class="mono chip-etf">{{ a.benchmarkSymbol }}</span>
              </button>
            }
            @if (!allSectors() && hasMoreSectors()) {
              <button type="button" class="sector-chip more" (click)="allSectors.set(true)">
                Other sector…
              </button>
            }
          </div>

          <form class="form-grid review-form" (ngSubmit)="add()">
            <label class="field wide">
              <span class="req">Company name</span>
              <input name="name" [(ngModel)]="draft.name" required />
            </label>
            <label class="field">
              <span class="req">SEC CIK</span>
              <input
                name="cik"
                [(ngModel)]="draft.cik"
                required
                inputmode="numeric"
                placeholder="e.g. 320193"
              />
            </label>
            <label class="field">
              <span class="req">Sector</span>
              <input name="sector" [(ngModel)]="draft.sector" required list="u-sectors" />
            </label>
            <label class="field">
              <span class="req">Sector benchmark ETF</span>
              <input
                name="bench"
                [(ngModel)]="draft.benchmarkSymbol"
                required
                list="u-benchmarks"
                placeholder="e.g. XLK"
              />
            </label>
            <label class="field">
              Industry (optional)
              <input name="industry" [(ngModel)]="draft.industry" list="u-industries" />
            </label>
            <label class="field">
              Member since
              <input name="since" type="date" [(ngModel)]="draft.memberSince" />
            </label>
            <div class="wide options">
              <label class="small">
                <input
                  type="checkbox"
                  name="ingest"
                  [ngModel]="ingestSec()"
                  (ngModelChange)="ingestSec.set($event)"
                />
                Ingest SEC filings now
                @if (meta.meta(); as m) {
                  @if (!m.secConfigured) {
                    <span class="muted"
                      >(SEC EDGAR not configured: set SEC_USER_AGENT in .env)</span
                    >
                  }
                }
              </label>
              <label class="small">
                <input
                  type="checkbox"
                  name="sync"
                  [ngModel]="syncPrices()"
                  (ngModelChange)="syncPrices.set($event)"
                  [disabled]="!priceProvider()"
                />
                Download price history now
                @if (!priceProvider()) {
                  <span class="muted"
                    >(no price provider configured; import a CSV on Data &amp; pipeline)</span
                  >
                }
              </label>
            </div>
            <div class="wide actions">
              <button type="submit" class="btn btn-primary" [disabled]="busy() || !!p.existing">
                Add {{ draft.symbol || p.symbol }}
              </button>
              <button type="button" class="btn" (click)="reset()">Cancel</button>
              <span class="small muted">
                Member since defaults to today: an earlier date would put the stock into backtests
                for periods when it was not actually picked.
              </span>
            </div>
          </form>
        </div>
      }

      <datalist id="u-sectors">
        @for (s of data()?.sectors ?? []; track s) {
          <option [value]="s"></option>
        }
      </datalist>
      <datalist id="u-industries">
        @for (s of data()?.industries ?? []; track s) {
          <option [value]="s"></option>
        }
      </datalist>
      <datalist id="u-benchmarks">
        @for (s of data()?.benchmarks ?? []; track s) {
          <option [value]="s"></option>
        }
      </datalist>
    </section>

    @if (res.error()) {
      <div class="alert alert-error">{{ err(res.error()) }}</div>
    }

    <div class="table-wrap" style="margin-top: 1rem">
      <table class="table compact">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Company</th>
            <th>Sector / industry</th>
            <th>Benchmark</th>
            <th>Status</th>
            <th class="num">Prices</th>
            <th class="num">Filings</th>
            <th class="num">Forecasts</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          @for (c of companies(); track c.id) {
            <tr [class.muted]="!c.active">
              <td>
                <a [routerLink]="['/companies', c.symbol]" class="mono">{{ c.symbol }}</a>
                @if (c.formerSymbols) {
                  <div class="small muted">was {{ c.formerSymbols }}</div>
                }
              </td>
              <td>
                @if (editing() === c.id) {
                  <input [(ngModel)]="edit.name" name="e-name" />
                } @else {
                  {{ c.name }}
                  <div class="small muted mono">CIK {{ c.cik }}</div>
                }
              </td>
              <td>
                @if (editing() === c.id) {
                  <input
                    [(ngModel)]="edit.sector"
                    name="e-sector"
                    list="u-sectors"
                    style="width: 9rem"
                  />
                  <input
                    [(ngModel)]="edit.industry"
                    name="e-industry"
                    list="u-industries"
                    style="width: 9rem"
                  />
                } @else {
                  {{ c.sector }}
                  @if (c.industry) {
                    <div class="small muted">{{ c.industry }}</div>
                  }
                }
              </td>
              <td>
                @if (editing() === c.id) {
                  <input
                    [(ngModel)]="edit.benchmarkSymbol"
                    name="e-bench"
                    list="u-benchmarks"
                    style="width: 5rem"
                  />
                } @else {
                  <span class="mono">{{ c.benchmarkSymbol }}</span>
                }
              </td>
              <td class="nowrap">
                @if (c.active) {
                  <span class="badge badge-ok">active</span>
                  <div class="small muted">since {{ c.memberSince }}</div>
                } @else if (c.removedOn) {
                  <span class="badge">removed</span>
                  <div class="small muted">on {{ c.removedOn }}</div>
                } @else {
                  <span class="badge">not a member</span>
                }
              </td>
              <td class="num nowrap">
                {{ c.priceCount | num }}
                @if (c.lastPriceDate) {
                  <div class="small muted">to {{ c.lastPriceDate }}</div>
                }
              </td>
              <td class="num">{{ c.filingCount | num }}</td>
              <td class="num">{{ c.forecastCount | num }}</td>
              <td class="row-actions">
                @if (editing() === c.id) {
                  <button
                    type="button"
                    class="btn btn-sm"
                    [disabled]="busy()"
                    (click)="saveEdit(c)"
                  >
                    Save
                  </button>
                  <button type="button" class="btn btn-sm" (click)="editing.set(null)">
                    Cancel
                  </button>
                } @else {
                  <button
                    type="button"
                    class="btn btn-sm"
                    [disabled]="busy()"
                    (click)="startEdit(c)"
                  >
                    Edit
                  </button>
                  <button type="button" class="btn btn-sm" [disabled]="busy()" (click)="ticker$(c)">
                    Ticker…
                  </button>
                  @if (c.active) {
                    <button
                      type="button"
                      class="btn btn-sm"
                      [disabled]="busy()"
                      (click)="remove(c)"
                    >
                      Remove
                    </button>
                  } @else {
                    <button
                      type="button"
                      class="btn btn-sm"
                      [disabled]="busy()"
                      (click)="restore(c)"
                    >
                      Restore
                    </button>
                  }
                  @if (c.deletable) {
                    <button
                      type="button"
                      class="btn btn-sm"
                      [disabled]="busy()"
                      (click)="del(c)"
                      title="Only possible while no data is attached"
                    >
                      Delete
                    </button>
                  }
                }
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="9" class="muted">
                No stocks yet. Type a ticker above to add the first one.
              </td>
            </tr>
          }
        </tbody>
      </table>
    </div>
    <p class="small muted" style="margin-top: 0.5rem">
      Features need about six months of price history before the first forecast is issued.
    </p>
  `,
  styles: `
    .add-card > h3 {
      margin-bottom: 0.2rem;
    }
    .ticker-form {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      align-items: center;
      margin: 0.75rem 0 0;
    }
    .ticker-input {
      width: 14rem;
      font-family: var(--mono);
      font-size: 1.05rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 0.5rem 0.75rem;
    }
    .ticker-input::placeholder {
      text-transform: none;
      font-family: var(--sans);
      font-weight: 400;
      letter-spacing: 0;
      font-size: 0.92rem;
    }
    .skeleton {
      margin-top: 1rem;
      max-width: 520px;
    }
    .review {
      margin-top: 1rem;
      padding: 1rem 1.1rem;
      border: 1px solid var(--border);
      border-left: 4px solid var(--axis);
      border-radius: var(--radius-sm);
      background: var(--surface-2);
    }
    .review-estimated {
      border-left-style: dashed;
      border-left-color: var(--est-border);
    }
    .review-head {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .review-name {
      font-size: 1.1rem;
      font-weight: 650;
      letter-spacing: -0.01em;
    }
    .review-sym {
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--ink-2);
      margin-left: 0.3rem;
    }
    .review-form {
      margin-top: 1rem;
    }
    .options {
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem 1.5rem;
    }
    .options label {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      flex-wrap: wrap;
    }
    .actions {
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.6rem;
    }
    .sector-pick {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-top: 0.6rem;
    }
    .sector-chip {
      font: inherit;
      font-size: 0.82rem;
      font-weight: 550;
      cursor: pointer;
      padding: 0.3rem 0.7rem;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      background: var(--surface);
      color: var(--ink-2);
      transition:
        background-color 0.15s,
        border-color 0.15s,
        color 0.15s;
    }
    .sector-chip:hover {
      border-color: var(--axis);
      color: var(--ink);
    }
    .sector-chip.hint {
      border-style: dashed;
      border-color: var(--est-border);
    }
    .sector-chip.on {
      background: var(--accent-soft);
      border-color: var(--accent);
      color: var(--accent);
    }
    .sector-chip.more {
      border-style: dashed;
      color: var(--ink-muted);
    }
    .chip-etf {
      font-size: 0.72rem;
      opacity: 0.75;
      margin-left: 0.15rem;
    }
    .row-actions {
      white-space: nowrap;
    }
    .row-actions .btn {
      margin-right: 0.25rem;
    }
    tr.muted td {
      opacity: 0.7;
    }
    td.nowrap,
    td.nowrap .small {
      white-space: nowrap;
    }
  `,
})
export class UniversePage {
  /** `/universe?add=NVDA` (from the command palette) looks the ticker up right away. */
  readonly addParam = input<string | undefined>(undefined, { alias: 'add' });

  private readonly api = inject(ApiService);
  protected readonly meta = inject(MetaService);

  protected readonly res = httpResource<UniverseResponse>(() => apiUrl.universe());
  protected readonly data = computed(() => valueOf(this.res));
  protected readonly companies = computed(() => this.data()?.companies ?? []);
  protected readonly priceProvider = computed(() => {
    const p = this.meta.meta()?.priceProvider;
    return !!p && p !== 'none';
  });

  protected readonly busy = signal(false);
  protected readonly looking = signal(false);
  protected readonly notice = signal<Notice | null>(null);
  protected readonly editing = signal<number | null>(null);
  protected readonly profile = signal<CompanyProfile | null>(null);
  protected readonly ticker = signal('');
  protected readonly validTicker = computed(() => TICKER.test(this.ticker().trim()));
  protected draft: Draft = this.emptyDraft();
  /** Data loads to start on add; signals so the checkboxes follow the configured providers. */
  protected readonly ingestSec = signal(false);
  protected readonly syncPrices = signal(false);
  /** Sector chips: the candidates for an ambiguous SIC code (or the one suggestion), or every sector on demand. */
  protected readonly allSectors = signal(false);
  protected readonly sectorChoices = computed(() => {
    const p = this.profile();
    if (!p) return [];
    const all = Object.entries(p.sectorBenchmarks ?? {}).map(([sector, benchmarkSymbol]) => ({
      sector,
      benchmarkSymbol,
    }));
    if (this.allSectors()) return all;
    if (p.sectorAlternatives?.length) return p.sectorAlternatives;
    return p.sector && p.benchmarkSymbol
      ? [{ sector: p.sector, benchmarkSymbol: p.benchmarkSymbol }]
      : all;
  });
  protected readonly hasMoreSectors = computed(
    () => Object.keys(this.profile()?.sectorBenchmarks ?? {}).length > this.sectorChoices().length,
  );
  protected edit = { name: '', sector: '', industry: '', benchmarkSymbol: '' };

  constructor() {
    effect(() => {
      const sym = this.addParam()?.trim();
      if (sym && TICKER.test(sym)) {
        this.ticker.set(sym.toUpperCase());
        this.lookup();
      }
    });
  }

  protected err(e: unknown): string {
    return errorMessage(e);
  }

  protected lookup(): void {
    const symbol = this.ticker().trim().toUpperCase();
    if (!TICKER.test(symbol)) return;
    this.looking.set(true);
    this.notice.set(null);
    this.profile.set(null);
    this.api.enrichSymbol(symbol).subscribe({
      next: (p) => {
        this.looking.set(false);
        this.allSectors.set(false);
        this.profile.set(p);
        this.ingestSec.set(this.meta.meta()?.secConfigured ?? true);
        this.syncPrices.set(p.priceProviderEnabled || this.priceProvider());
        this.draft = {
          symbol: p.symbol,
          name: p.name ?? '',
          cik: (p.cik ?? '').replace(/^0+/, ''),
          sector: p.sector ?? '',
          industry: p.industry ?? '',
          benchmarkSymbol: p.benchmarkSymbol ?? '',
          memberSince: '',
        };
      },
      error: (e) => {
        this.looking.set(false);
        this.notice.set({ ok: false, text: `Lookup failed: ${errorMessage(e)}` });
      },
    });
  }

  protected pickSector(sector: string, benchmarkSymbol: string): void {
    this.draft.sector = sector;
    this.draft.benchmarkSymbol = benchmarkSymbol;
  }

  protected reset(): void {
    this.allSectors.set(false);
    this.profile.set(null);
    this.ticker.set('');
    this.draft = this.emptyDraft();
  }

  protected add(): void {
    const d = this.draft;
    this.mutate(
      'Add',
      this.api.addCompany({
        symbol: d.symbol.trim(),
        name: d.name.trim(),
        cik: d.cik.trim(),
        sector: d.sector.trim(),
        industry: d.industry.trim() || null,
        benchmarkSymbol: d.benchmarkSymbol.trim(),
        memberSince: d.memberSince || null,
        ingestSec: this.ingestSec(),
        syncPrices: this.syncPrices(),
      }),
      (r) => {
        const resp = r as {
          symbol: string;
          nextSteps: string[];
          jobs: { id: number; jobType: string }[];
        };
        this.reset();
        const started = resp.jobs.map(
          (j) => `${j.jobType === 'PRICE_SYNC' ? 'price download' : 'SEC ingest'} (job #${j.id})`,
        );
        return {
          text:
            `${resp.symbol} added.` + (started.length ? ` Started ${started.join(' and ')}.` : ''),
          steps: resp.nextSteps,
          jobs: started.length > 0,
        };
      },
    );
  }

  protected startEdit(c: UniverseCompany): void {
    this.edit = {
      name: c.name,
      sector: c.sector,
      industry: c.industry ?? '',
      benchmarkSymbol: c.benchmarkSymbol,
    };
    this.editing.set(c.id);
  }

  protected saveEdit(c: UniverseCompany): void {
    this.mutate('Edit', this.api.editCompany(c.id, { ...this.edit }), () => {
      this.editing.set(null);
      return { text: `${c.symbol} updated.` };
    });
  }

  protected remove(c: UniverseCompany): void {
    const when = prompt(
      `Remove ${c.symbol} from the universe as of (YYYY-MM-DD, empty = today)?`,
      '',
    );
    if (when === null) return;
    this.mutate('Remove', this.api.removeCompany(c.id, when.trim() || null), () => ({
      text: `${c.symbol} removed. Its history, past forecasts and backtests are kept.`,
    }));
  }

  protected restore(c: UniverseCompany): void {
    this.mutate('Restore', this.api.restoreCompany(c.id), () => ({
      text: `${c.symbol} is active again from today.`,
    }));
  }

  protected ticker$(c: UniverseCompany): void {
    const sym = prompt(`New ticker for ${c.name} (currently ${c.symbol}):`, '');
    if (!sym?.trim()) return;
    const when = prompt('Effective from (YYYY-MM-DD, empty = today):', '');
    if (when === null) return;
    this.mutate(
      'Ticker change',
      this.api.changeTicker(c.id, sym.trim(), when.trim() || null),
      () => ({
        text: `${c.symbol} → ${sym.trim().toUpperCase()} recorded; earlier data stays under ${c.symbol}.`,
      }),
    );
  }

  protected del(c: UniverseCompany): void {
    if (
      !confirm(
        `Permanently delete ${c.symbol} (${c.name})? This is only allowed because no data is attached.`,
      )
    )
      return;
    this.mutate('Delete', this.api.deleteCompany(c.id), () => ({ text: `${c.symbol} deleted.` }));
  }

  private emptyDraft(): Draft {
    return {
      symbol: '',
      name: '',
      cik: '',
      sector: '',
      industry: '',
      benchmarkSymbol: '',
      memberSince: '',
    };
  }

  private mutate(
    label: string,
    call: Observable<unknown>,
    ok: (r: unknown) => Notice | Omit<Notice, 'ok'>,
  ): void {
    this.busy.set(true);
    this.notice.set(null);
    call.subscribe({
      next: (r) => {
        this.busy.set(false);
        this.notice.set({ ok: true, ...ok(r) });
        this.res.reload();
        this.meta.resource.reload();
      },
      error: (e) => {
        this.busy.set(false);
        this.notice.set({ ok: false, text: `${label} failed: ${errorMessage(e)}` });
      },
    });
  }
}
