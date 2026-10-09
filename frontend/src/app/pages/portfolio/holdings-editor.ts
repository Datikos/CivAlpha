import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { FORMAT_PIPES } from '../../core/format';
import { Holding } from '../../core/models';
import { UI } from '../../shared/ui';
import { VIZ } from '../../shared/viz';
import { PortfolioStore } from './portfolio.store';

interface Draft {
  symbol: string;
  shares: number | null;
  avgCostUsd: number | null;
  openedOn: string;
  note: string;
}

const EMPTY: Draft = { symbol: '', shares: null, avgCostUsd: null, openedOn: '', note: '' };

/** Holdings and cash of the chosen portfolio, edited in place. A row without an average cost is flagged: gain and
 * loss need it. Saving does not re-advise; Refresh advice does. */
@Component({
  selector: 'app-holdings-editor',
  imports: [FormsModule, RouterLink, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    @if (missingCost(); as n) {
      <div class="alert alert-warn small">
        <strong>{{ n }} of {{ rows().length }} holdings have no average cost</strong>, so the page cannot show gain or loss for them.
        Click <em>Edit</em> on a row and enter what you paid per share.
      </div>
    }

    <form class="add" (ngSubmit)="add()">
      <label class="field">
        <span class="req">Symbol</span>
        <input name="symbol" [(ngModel)]="draft.symbol" required placeholder="e.g. MSFT" autocomplete="off" />
      </label>
      <label class="field">
        <span class="req">Shares</span>
        <input name="shares" type="number" min="0" step="any" [(ngModel)]="draft.shares" required />
      </label>
      <label class="field">
        <span>Average cost (USD) <app-help text="What you paid per share, fees included. Used for gain and loss only; the advice does not depend on it." topic="page-portfolio" label="average cost" /></span>
        <input name="cost" type="number" min="0" step="any" [(ngModel)]="draft.avgCostUsd" />
      </label>
      <label class="field">
        <span>Bought on</span>
        <input name="opened" type="date" [(ngModel)]="draft.openedOn" />
      </label>
      <label class="field wide">
        <span>Note</span>
        <input name="note" [(ngModel)]="draft.note" maxlength="500" placeholder="why you hold it (optional)" />
      </label>
      <div class="field">
        <span>&nbsp;</span>
        <button type="submit" class="btn btn-primary" [disabled]="store.busy() || !draft.symbol.trim() || !(draft.shares! > 0)">
          {{ exists(draft.symbol) ? 'Update ' + draft.symbol.toUpperCase() : 'Add holding' }}
        </button>
      </div>
    </form>
    @if (exists(draft.symbol)) {
      <p class="small muted">{{ draft.symbol.toUpperCase() }} is already held: saving replaces its shares, cost, date and note.</p>
    }

    <div class="table-wrap">
      <table class="table compact">
        <thead>
          <tr><th>Stock</th><th class="num">Shares</th><th class="num">Avg cost</th><th class="num">Paid</th><th>Bought</th><th>Note</th><th></th></tr>
        </thead>
        <tbody>
          @for (h of rows(); track h.symbol) {
            @if (editing() === h.symbol) {
              <tr class="editing">
                <td><strong>{{ h.symbol }}</strong></td>
                <td class="num"><input type="number" min="0" step="any" [(ngModel)]="edit.shares" [attr.aria-label]="'Shares of ' + h.symbol" /></td>
                <td class="num"><input type="number" min="0" step="any" [(ngModel)]="edit.avgCostUsd" [attr.aria-label]="'Average cost of ' + h.symbol" /></td>
                <td class="num muted">{{ (edit.shares ?? 0) * (edit.avgCostUsd ?? 0) | usd }}</td>
                <td><input type="date" [(ngModel)]="edit.openedOn" [attr.aria-label]="'Bought on, ' + h.symbol" /></td>
                <td><input [(ngModel)]="edit.note" maxlength="500" [attr.aria-label]="'Note on ' + h.symbol" /></td>
                <td class="actions">
                  <button type="button" class="btn btn-sm btn-primary" (click)="saveEdit(h.symbol)" [disabled]="store.busy() || !(edit.shares! > 0)">Save</button>
                  <button type="button" class="btn btn-sm" (click)="editing.set(null)">Cancel</button>
                </td>
              </tr>
            } @else {
              <tr [class.nocost]="!h.avgCostUsd">
                <td>
                  <strong>{{ h.symbol }}</strong>
                  <span class="small muted name">{{ h.name ?? 'not in the universe' }}</span>
                  @if (!h.companyId) {
                    <a class="small" routerLink="/universe" [queryParams]="{ add: h.symbol }" title="No prices or forecast until the company is in the universe"> add to universe →</a>
                  }
                </td>
                <td class="num">{{ h.shares | num: 2 }}</td>
                <td class="num">
                  @if (h.avgCostUsd) { {{ h.avgCostUsd | usd }} } @else { <span class="chip tone-warn">not entered</span> }
                </td>
                <td class="num">{{ h.avgCostUsd ? (h.shares * h.avgCostUsd | usd) : '—' }}</td>
                <td>{{ h.openedOn | day }}</td>
                <td class="small">{{ h.note }}</td>
                <td class="actions">
                  <button type="button" class="btn btn-sm" (click)="startEdit(h)" [disabled]="store.busy()">Edit</button>
                  <button type="button" class="btn btn-sm" (click)="store.removeHolding(h.symbol)" [disabled]="store.busy()">Remove</button>
                </td>
              </tr>
            }
          } @empty {
            <tr><td colspan="7" class="muted">No holdings yet. Enter the first one above: symbol, shares and what you paid.</td></tr>
          }
        </tbody>
      </table>
    </div>

    <form class="cash" (ngSubmit)="saveCash()">
      <label class="field">
        <span>Cash (USD) <app-help text="Cash you could invest. An Add is limited to it, and it counts in the portfolio value the weights are measured against." topic="page-portfolio" label="cash" /></span>
        <input name="cash" type="number" min="0" step="any" [(ngModel)]="cash" [placeholder]="store.cash()" />
      </label>
      <button type="submit" class="btn" [disabled]="store.busy() || cash === null">Save cash</button>
      <span class="small muted">now {{ store.cash() | usd }}@if (!store.cash()) { · without cash an Add cannot be sized }</span>
    </form>
  `,
  styles: `
    .add { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.6rem 0.75rem; align-items: end; margin-bottom: 0.4rem; }
    .add .wide { grid-column: span 2; }
    tr.nocost td:first-child { box-shadow: inset 3px 0 0 var(--est-border); }
    tr.editing td { background: var(--accent-soft); }
    tr.editing input { width: 100%; min-width: 5rem; }
    .name { margin-left: 0.4rem; }
    .actions { white-space: nowrap; text-align: right; }
    .actions .btn + .btn { margin-left: 0.3rem; }
    .cash { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.75rem; margin-top: 1rem; }
  `,
})
export class HoldingsEditor {
  protected readonly store = inject(PortfolioStore);
  protected draft: Draft = { ...EMPTY };
  protected edit: Draft = { ...EMPTY };
  protected cash: number | null = null;
  protected readonly editing = signal<string | null>(null);
  protected readonly rows = computed(() => this.store.holdings()?.holdings ?? []);
  protected readonly missingCost = computed(() => this.rows().filter((h) => !h.avgCostUsd).length);

  protected exists(symbol: string): boolean {
    const s = symbol.trim().toUpperCase();
    return !!s && this.rows().some((h) => h.symbol === s);
  }

  protected add(): void {
    const d = this.draft;
    this.store.saveHolding({ symbol: d.symbol.trim().toUpperCase(), shares: Number(d.shares), avgCostUsd: Number(d.avgCostUsd ?? 0),
                             openedOn: d.openedOn || null, note: d.note.trim() || null }, () => (this.draft = { ...EMPTY }));
  }

  protected startEdit(h: Holding): void {
    this.edit = { symbol: h.symbol, shares: h.shares, avgCostUsd: h.avgCostUsd || null, openedOn: h.openedOn ?? '', note: h.note ?? '' };
    this.editing.set(h.symbol);
  }

  protected saveEdit(symbol: string): void {
    const e = this.edit;
    this.store.saveHolding({ symbol, shares: Number(e.shares), avgCostUsd: Number(e.avgCostUsd ?? 0), openedOn: e.openedOn || null,
                             note: e.note.trim() || null }, () => this.editing.set(null));
  }

  protected saveCash(): void {
    this.store.saveCash(Number(this.cash), () => (this.cash = null));
  }
}
