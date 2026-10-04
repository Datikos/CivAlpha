import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import { UniverseCompany, UniverseResponse } from '../core/models';
import { UI } from '../shared/ui';

interface Draft {
  symbol: string;
  name: string;
  cik: string;
  sector: string;
  industry: string;
  benchmarkSymbol: string;
  memberSince: string;
  ingestSec: boolean;
}

const EMPTY: Draft = { symbol: '', name: '', cik: '', sector: '', industry: '', benchmarkSymbol: '', memberSince: '', ingestSec: false };

@Component({
  selector: 'app-universe',
  imports: [FormsModule, RouterLink, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Universe</h1>
        <p class="muted">
          Stocks the platform tracks and forecasts. Changes take effect immediately and are kept as history: removing a
          stock ends its membership on a date, so past forecasts and backtests are unaffected.
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
      </div>
    }

    <details class="card" [open]="addOpen()" (toggle)="addOpen.set($any($event.target).open)">
      <summary><strong>Add a stock</strong></summary>
      <form class="add-form" (ngSubmit)="add()">
        <div class="row">
          <label class="field">
            Ticker
            <input name="symbol" [(ngModel)]="draft.symbol" required placeholder="e.g. BDSX" autocomplete="off" />
          </label>
          <button type="button" class="btn" [disabled]="busy() || !draft.symbol.trim()" (click)="lookup()">Look up on SEC</button>
        </div>
        <div class="row">
          <label class="field wide">
            Company name
            <input name="name" [(ngModel)]="draft.name" required />
          </label>
          <label class="field">
            SEC CIK
            <input name="cik" [(ngModel)]="draft.cik" required inputmode="numeric" placeholder="e.g. 320193" />
          </label>
        </div>
        <div class="row">
          <label class="field">
            Sector
            <input name="sector" [(ngModel)]="draft.sector" required list="u-sectors" />
          </label>
          <label class="field">
            Industry (optional)
            <input name="industry" [(ngModel)]="draft.industry" list="u-industries" />
          </label>
          <label class="field">
            Sector benchmark ETF
            <input name="bench" [(ngModel)]="draft.benchmarkSymbol" required list="u-benchmarks" placeholder="e.g. XLV" />
          </label>
          <label class="field">
            Member since
            <input name="since" type="date" [(ngModel)]="draft.memberSince" />
          </label>
        </div>
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
        <p class="small muted">
          Member since defaults to today, which is the honest choice: an earlier date puts the stock into backtests
          for periods when it was not actually picked, which biases results toward stocks you already know did well.
          Industries {{ (data()?.productIndustries ?? []).join(', ') }} are matched to product-level trade events.
        </p>
        <label class="small">
          <input type="checkbox" name="ingest" [(ngModel)]="draft.ingestSec" /> Ingest SEC filings right away
          @if (meta.meta(); as m) {
            (SEC EDGAR {{ m.secConfigured ? 'configured' : 'not configured: set SEC_USER_AGENT in .env' }})
          }
        </label>
        <div style="margin-top: 0.6rem">
          <button type="submit" class="btn btn-primary" [disabled]="busy()">Add stock</button>
        </div>
      </form>
    </details>

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
                  <input [(ngModel)]="edit.sector" name="e-sector" list="u-sectors" style="width: 9rem" />
                  <input [(ngModel)]="edit.industry" name="e-industry" list="u-industries" style="width: 9rem" />
                } @else {
                  {{ c.sector }}
                  @if (c.industry) {
                    <div class="small muted">{{ c.industry }}</div>
                  }
                }
              </td>
              <td>
                @if (editing() === c.id) {
                  <input [(ngModel)]="edit.benchmarkSymbol" name="e-bench" list="u-benchmarks" style="width: 5rem" />
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
                  <button type="button" class="btn" [disabled]="busy()" (click)="saveEdit(c)">Save</button>
                  <button type="button" class="btn" (click)="editing.set(null)">Cancel</button>
                } @else {
                  <button type="button" class="btn" [disabled]="busy()" (click)="startEdit(c)">Edit</button>
                  <button type="button" class="btn" [disabled]="busy()" (click)="ticker(c)">Ticker…</button>
                  @if (c.active) {
                    <button type="button" class="btn" [disabled]="busy()" (click)="remove(c)">Remove</button>
                  } @else {
                    <button type="button" class="btn" [disabled]="busy()" (click)="restore(c)">Restore</button>
                  }
                  @if (c.deletable) {
                    <button type="button" class="btn" [disabled]="busy()" (click)="del(c)" title="Only possible while no data is attached">
                      Delete
                    </button>
                  }
                }
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="9" class="muted">
                No stocks yet. Add one above: look it up on SEC EDGAR by ticker, then fill in the remaining fields.
              </td>
            </tr>
          }
        </tbody>
      </table>
    </div>
    <p class="small muted" style="margin-top: 0.5rem">
      After adding a stock: update prices (Data &amp; pipeline → Update prices, or import a CSV) and ingest its SEC filings.
      Features need about six months of price history before the first forecast.
    </p>
  `,
  styles: `
    .add-form { margin-top: 0.75rem; }
    .row { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end; margin-bottom: 0.6rem; }
    .row-actions { white-space: nowrap; }
    .row-actions .btn { padding: 0.2rem 0.55rem; margin-right: 0.25rem; }
    summary { cursor: pointer; }
    tr.muted td { opacity: 0.7; }
    td.nowrap, td.nowrap .small { white-space: nowrap; }
  `,
})
export class UniversePage {
  private readonly api = inject(ApiService);
  protected readonly meta = inject(MetaService);

  protected readonly res = httpResource<UniverseResponse>(() => apiUrl.universe());
  protected readonly data = computed(() => valueOf(this.res));
  protected readonly companies = computed(() => this.data()?.companies ?? []);

  protected readonly busy = signal(false);
  protected readonly addOpen = signal(false);
  protected readonly notice = signal<{ ok: boolean; text: string; steps?: string[] } | null>(null);
  protected readonly editing = signal<number | null>(null);
  protected draft: Draft = { ...EMPTY };
  protected edit = { name: '', sector: '', industry: '', benchmarkSymbol: '' };

  protected err(e: unknown): string {
    return errorMessage(e);
  }

  protected lookup(): void {
    const symbol = this.draft.symbol.trim();
    this.busy.set(true);
    this.api.lookupSymbol(symbol).subscribe({
      next: (m) => {
        this.draft.cik = m.cik.replace(/^0+/, '');
        this.draft.name = m.name;
        this.busy.set(false);
        this.notice.set({ ok: true, text: `${m.symbol}: ${m.name}, CIK ${m.cik} (${m.source}).` });
      },
      error: (e) => {
        this.busy.set(false);
        this.notice.set({ ok: false, text: `Lookup failed: ${errorMessage(e)}` });
      },
    });
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
        ingestSec: d.ingestSec,
      }),
      (r) => {
        const resp = r as { symbol: string; nextSteps: string[]; job?: { id: number } };
        this.draft = { ...EMPTY };
        this.addOpen.set(false);
        return {
          text: `${resp.symbol} added.${resp.job ? ` SEC ingest started (job #${resp.job.id}).` : ''} Next:`,
          steps: resp.nextSteps,
        };
      },
    );
  }

  protected startEdit(c: UniverseCompany): void {
    this.edit = { name: c.name, sector: c.sector, industry: c.industry ?? '', benchmarkSymbol: c.benchmarkSymbol };
    this.editing.set(c.id);
  }

  protected saveEdit(c: UniverseCompany): void {
    this.mutate('Edit', this.api.editCompany(c.id, { ...this.edit }), () => {
      this.editing.set(null);
      return { text: `${c.symbol} updated.` };
    });
  }

  protected remove(c: UniverseCompany): void {
    const when = prompt(`Remove ${c.symbol} from the universe as of (YYYY-MM-DD, empty = today)?`, '');
    if (when === null) return;
    this.mutate('Remove', this.api.removeCompany(c.id, when.trim() || null), () => ({
      text: `${c.symbol} removed. Its history, past forecasts and backtests are kept.`,
    }));
  }

  protected restore(c: UniverseCompany): void {
    this.mutate('Restore', this.api.restoreCompany(c.id), () => ({ text: `${c.symbol} is active again from today.` }));
  }

  protected ticker(c: UniverseCompany): void {
    const sym = prompt(`New ticker for ${c.name} (currently ${c.symbol}):`, '');
    if (!sym?.trim()) return;
    const when = prompt('Effective from (YYYY-MM-DD, empty = today):', '');
    if (when === null) return;
    this.mutate('Ticker change', this.api.changeTicker(c.id, sym.trim(), when.trim() || null), () => ({
      text: `${c.symbol} → ${sym.trim().toUpperCase()} recorded; earlier data stays under ${c.symbol}.`,
    }));
  }

  protected del(c: UniverseCompany): void {
    if (!confirm(`Permanently delete ${c.symbol} (${c.name})? This is only allowed because no data is attached.`)) return;
    this.mutate('Delete', this.api.deleteCompany(c.id), () => ({ text: `${c.symbol} deleted.` }));
  }

  private mutate(label: string, call: Observable<unknown>, ok: (r: unknown) => { text: string; steps?: string[] }): void {
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
