import { httpResource } from '@angular/common/http';
import { Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { ActionCandidate, CandidateReview, CandidateStatus, CandidatesResponse, NearbyAction } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

const KIND: Record<string, { label: string; glyph: string }> = {
  SPIN_OFF_OR_DISTRIBUTION: { label: 'Spin-off / distribution', glyph: '⑂' },
  FORWARD_SPLIT: { label: 'Stock split', glyph: '÷' },
  REVERSE_SPLIT: { label: 'Reverse split', glyph: '×' },
  SPECIAL_DIVIDEND: { label: 'Special dividend', glyph: '$' },
  MERGER_OR_DELISTING: { label: 'Merger / delisting', glyph: '⇥' },
  NONE: { label: 'Nothing', glyph: '·' },
};
const REVIEW_TONE: Record<CandidateReview, string> = { UNREVIEWED: 'warn', CONFIRMED: 'good', REJECTED: 'neutral' };
const REVIEW_LABEL: Record<CandidateReview, string> = { UNREVIEWED: '? unreviewed', CONFIRMED: '✓ confirmed', REJECTED: '✕ rejected' };
/** The keyword rule (keyword-1, ADR-0003): the words it matched are highlighted in the evidence. */
const MATCH = /(reverse (?:stock )?split|stock split|forward split|split[- ]adjusted|spin[- ]?off|separation and distribution|pro rata distribution)/gi;

/**
 * Corporate actions (ADR-0003 phase 3): the 8-Ks the keyword rule flagged as a split or spin-off with no matching
 * recorded action ("feed gaps"), with the context to decide, and the four ways to resolve one.
 */
@Component({
  selector: 'app-corporate-actions',
  imports: [FormsModule, RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="diff" area="data" />
        <div>
          <h1>Corporate actions</h1>
          <p class="muted">
            Splits and spin-offs read from 8-K text, checked against what the price feed recorded. A flagged 8-K with no
            matching action is a gap: the feed may have missed a distribution, and holders of the stock see "Check the
            data" on My portfolio for {{ data()?.portfolioDays ?? 120 }} days. Decide each one here.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-corporate-actions" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <button type="button" class="btn" (click)="detect()" [disabled]="busy()" title="Queue a job that reads the 8-Ks not yet read (also part of every pipeline run)">Read new 8-Ks now</button>
      </div>
    </div>

    @if (message(); as m) {
      <div class="alert" [class.alert-error]="m.error" [class.alert-ok]="!m.error" role="status">{{ m.text }}</div>
    }
    <app-status [res]="res" what="the candidates" />

    @if (data(); as d) {
      <div class="stats wide">
        <div class="stat" [class]="'stat tone-' + (d.counts.gapRecent ? 'warn' : 'good')">
          <div class="stat-label">Open gaps <app-help text="Flagged 8-Ks with no recorded split or spin-off from 5 days before to 120 days after, not rejected. The recent ones put holders on Check the data." topic="page-corporate-actions" label="open gaps" /></div>
          <div class="stat-value">{{ d.counts.gap }}</div>
          <div class="stat-sub">{{ d.counts.gapRecent }} from the last {{ d.portfolioDays }} days: these mark holdings "Check the data"</div>
        </div>
        <div class="stat tone-info">
          <div class="stat-label">To review</div>
          <div class="stat-value">{{ d.counts.unreviewed }}</div>
          <div class="stat-sub">flagged and not yet decided, of {{ d.counts.flagged }} flagged</div>
        </div>
        <div class="stat tone-good">
          <div class="stat-label">Decided</div>
          <div class="stat-value">{{ d.counts.confirmed + d.counts.rejected }}</div>
          <div class="stat-sub">{{ d.counts.confirmed }} confirmed · {{ d.counts.rejected }} rejected</div>
        </div>
        <div class="stat tone-neutral">
          <div class="stat-label">8-Ks read <app-help text="Every 8-K with an item where a capital change is reported (1.01, 2.01, 3.03, 5.03, 7.01, 8.01), read once by the keyword rule. Measured 2026-10-14: 81% of 68 recorded splits and spin-offs found, 0.64% of quiet 8-Ks flagged." topic="page-corporate-actions" label="8-Ks read" /></div>
          <div class="stat-value">{{ d.counts.read | num }}</div>
          <div class="stat-sub">rule {{ d.rule }}</div>
        </div>
      </div>

      <div class="bar">
        <div class="chips" role="group" aria-label="Show">
          @for (f of filters; track f.key) {
            <button type="button" class="chip" [class.on]="status() === f.key" [attr.aria-pressed]="status() === f.key" (click)="show(f.key)">
              {{ f.label }}@if (f.count(d) !== null) { <span class="count">{{ f.count(d) }}</span> }
            </button>
          }
        </div>
        <input class="search" type="search" placeholder="Symbol, e.g. GE" [ngModel]="symbolDraft" (ngModelChange)="symbolDraft = $event" (keyup.enter)="search()" aria-label="Filter by symbol" />
      </div>

      @if (selected().size) {
        <div class="bulk">
          <strong>{{ selected().size }} selected</strong>
          <input [(ngModel)]="bulkNote" maxlength="500" placeholder="why (optional), e.g. restates a completed split" aria-label="Note for the selected" />
          <button type="button" class="btn btn-sm" (click)="reviewMany('REJECTED')" [disabled]="busy()">✕ Reject selected</button>
          <button type="button" class="btn btn-sm" (click)="reviewMany('CONFIRMED')" [disabled]="busy()">✓ Confirm selected</button>
          <button type="button" class="btn btn-sm" (click)="clearSelection()">Clear</button>
        </div>
      }

      <div class="table-wrap">
        <table class="table compact cand">
          <thead>
            <tr>
              <th class="pick"><input type="checkbox" [checked]="allPicked()" (change)="pickAll($any($event.target).checked)" aria-label="Select all shown" /></th>
              <th>8-K</th>
              <th>What it says</th>
              <th>Feed and price <app-help text="Feed: splits, spin-offs and cash dividends worth at least 2% of the close that the feed recorded from 60 days before to 130 days after the 8-K (an 8-K often reports a distribution after its ex-date); link one to close the gap. Price: the largest one-day raw-close move without a recorded action from 5 days before to 120 days after; a missed distribution looks like a drop of its size, results days and news move prices too." topic="page-corporate-actions" label="feed and price" /></th>
              <th>Decision</th>
            </tr>
          </thead>
          <tbody>
            @for (c of rows(); track c.id) {
              <tr [class.recent]="c.inPortfolioWindow && !c.reconciledActionId && c.review !== 'REJECTED'">
                <td class="pick"><input type="checkbox" [checked]="selected().has(c.id)" (change)="pick(c.id, $any($event.target).checked)" [attr.aria-label]="'Select ' + c.symbol + ' ' + c.acceptedAt" /></td>
                <td class="filing">
                  <a [routerLink]="['/companies', c.symbol]"><strong>{{ c.symbol }}</strong></a>
                  <div class="small">{{ c.acceptedAt | day }}</div>
                  <div class="small"><a [routerLink]="['/filings', c.filingId]" title="Open the filing">8-K items {{ c.items }}</a></div>
                  @if (c.inPortfolioWindow && !c.reconciledActionId && c.review !== 'REJECTED') {
                    <span class="chip tone-warn small" title="Younger than the portfolio window: holders see Check the data">affects advice</span>
                  }
                </td>
                <td class="ev">
                  <span class="chip">{{ kind(c.kind).glyph }} {{ kind(c.kind).label }}</span>
                  <div class="small text">
                    @for (seg of highlight(c.evidence); track $index) {
                      @if (seg.hit) { <mark>{{ seg.t }}</mark> } @else { {{ seg.t }} }
                    }
                  </div>
                </td>
                <td class="small feed">
                  @for (a of c.nearbyActions; track a.id) {
                    <div class="act">
                      <span class="chip" [class.tone-good]="c.reconciledActionId === a.id">{{ actionText(a) }}</span>
                      @if (c.reconciledActionId !== a.id && c.review !== 'REJECTED') {
                        <button type="button" class="btn btn-sm" (click)="link(c, a)" [disabled]="busy()" title="Confirm: the feed already adjusted the prices for this event">link</button>
                      }
                    </div>
                  } @empty { <div class="muted">feed: nothing recorded</div> }
                  @if (c.largestMove; as m) {
                    <div class="mvline">largest unexplained move <span [class]="'mv tone-' + moveTone(m.move)">{{ m.move | signedPct: 1 }}</span> <span class="muted">{{ m.date | day }}</span></div>
                  }
                </td>
                <td class="decide">
                  <span class="chip" [class]="'chip tone-' + reviewTone[c.review]">{{ reviewLabel[c.review] }}</span>
                  @if (c.reconciledActionId) { <span class="chip tone-good small" title="A recorded action matches: no gap">matched</span> }
                  @if (c.reviewedAt) { <div class="small muted">{{ c.reviewedBy ?? 'owner' }} · {{ c.reviewedAt | day }}@if (c.reviewNote) { · {{ c.reviewNote }} }</div> }
                  <div class="btns">
                    @if (c.review !== 'REJECTED') { <button type="button" class="btn btn-sm" (click)="one(c, 'REJECTED')" [disabled]="busy()" title="Only a mention: no change to the shares">✕ Reject</button> }
                    @if (c.review !== 'CONFIRMED') { <button type="button" class="btn btn-sm" (click)="one(c, 'CONFIRMED')" [disabled]="busy()" title="A real event; the gap stays open until an action is recorded or linked">✓ Confirm</button> }
                    @if (!c.reconciledActionId) { <button type="button" class="btn btn-sm" (click)="openSpin(c)" [disabled]="busy()" title="The feed missed it: enter the distribution">+ Spin-off</button> }
                    @if (c.review !== 'UNREVIEWED') { <button type="button" class="btn btn-sm" (click)="one(c, 'UNREVIEWED')" [disabled]="busy()">Reopen</button> }
                  </div>
                  @if (spinFor() === c.id) {
                    <form class="spin" (ngSubmit)="saveSpin(c)">
                      <label class="field"><span class="req">Ex-date</span><input type="date" name="ex" [(ngModel)]="spin.exDate" required /></label>
                      <label class="field"><span class="req">Value per {{ c.symbol }} share (USD)</span>
                        <input type="number" name="v" step="any" min="0" [(ngModel)]="spin.value" required /></label>
                      <label class="field wide"><span>How you got it</span><input name="n" [(ngModel)]="spin.note" maxlength="500" placeholder="e.g. 1 SpinCo per 4 shares × $69.55 first close" /></label>
                      <p class="small muted">Value = what one {{ c.symbol }} share received, at the ex-date close (shares received × their first close). It is applied like a cash dividend from the next pipeline run.</p>
                      <button type="submit" class="btn btn-sm btn-primary" [disabled]="busy() || !spin.exDate || !(spin.value! > 0)">Record spin-off</button>
                      <button type="button" class="btn btn-sm" (click)="spinFor.set(null)">Cancel</button>
                    </form>
                  }
                </td>
              </tr>
            } @empty {
              <tr><td colspan="5" class="muted">{{ status() === 'gap' ? 'No open gaps: every flagged 8-K is matched to a recorded action or rejected.' : 'Nothing in this list.' }}</td></tr>
            }
          </tbody>
        </table>
      </div>
      <p class="small muted">{{ d.note }}</p>
    }
  `,
  styles: `
    .bar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.75rem; align-items: center; margin: 0.5rem 0; }
    .chips button.chip { cursor: pointer; border: 1px solid var(--border); background: var(--surface-2); }
    .chips button.chip.on { border-color: var(--accent); background: var(--accent-soft); font-weight: 600; }
    .count { opacity: 0.7; margin-left: 0.25rem; }
    .search { max-width: 12rem; }
    .bulk { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; padding: 0.5rem 0.75rem; border-radius: var(--radius); background: var(--accent-soft); margin-bottom: 0.5rem; }
    .bulk input { flex: 1; min-width: 14rem; }
    .cand td { vertical-align: top; }
    .cand tr.recent td:first-child { box-shadow: inset 3px 0 0 var(--est-border); }
    .pick { width: 1.8rem; }
    .filing { width: 9.5rem; }
    .ev { min-width: 18rem; max-width: 30rem; }
    .ev .text { color: var(--ink-2); margin-top: 0.3rem; max-height: 7.5rem; overflow: auto; }
    .feed { min-width: 15rem; }
    .mvline { margin-top: 0.35rem; }
    .mvline .muted, .mvline .mv { margin-left: 0.3rem; }
    mark { background: var(--est-bg); color: var(--est-ink); padding: 0 0.1rem; border-radius: 3px; }
    .act { display: flex; gap: 0.3rem; align-items: center; margin-bottom: 0.2rem; }
    .mv { font-weight: 600; color: var(--tone-ink); }
    .decide { min-width: 13rem; }
    .btns { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.35rem; }
    .spin { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.4rem 0.6rem; margin-top: 0.5rem; padding: 0.5rem; border-radius: var(--radius); background: var(--surface-2); }
    .spin .wide, .spin p { grid-column: 1 / -1; margin: 0; }
  `,
})
export class CorporateActionsPage {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  /** `?status=` and `?symbol=` keep the view in the address. */
  readonly statusParam = input<string | undefined>(undefined, { alias: 'status' });
  readonly symbolParam = input<string | undefined>(undefined, { alias: 'symbol' });
  protected readonly status = computed<CandidateStatus>(() => {
    const s = this.statusParam();
    return s === 'unreviewed' || s === 'confirmed' || s === 'rejected' || s === 'flagged' || s === 'all' ? s : 'gap';
  });
  protected readonly res = httpResource<CandidatesResponse>(() => apiUrl.actionCandidates(this.status(), this.symbolParam() ?? null));
  protected readonly data = computed(() => valueOf(this.res));
  protected readonly rows = computed(() => this.data()?.candidates ?? []);
  protected readonly selected = signal<Set<number>>(new Set());
  protected readonly busy = signal(false);
  protected readonly message = signal<{ text: string; error: boolean } | null>(null);
  protected readonly spinFor = signal<number | null>(null);
  protected spin = { exDate: '', value: null as number | null, note: '' };
  protected bulkNote = '';
  protected symbolDraft = '';
  protected readonly reviewTone = REVIEW_TONE;
  protected readonly reviewLabel = REVIEW_LABEL;
  protected readonly filters: { key: CandidateStatus; label: string; count: (d: CandidatesResponse) => number | null }[] = [
    { key: 'gap', label: 'Open gaps', count: (d) => d.counts.gap },
    { key: 'unreviewed', label: 'To review', count: (d) => d.counts.unreviewed },
    { key: 'confirmed', label: 'Confirmed', count: (d) => d.counts.confirmed },
    { key: 'rejected', label: 'Rejected', count: (d) => d.counts.rejected },
    { key: 'flagged', label: 'All flagged', count: (d) => d.counts.flagged },
  ];
  protected readonly allPicked = computed(() => this.rows().length > 0 && this.rows().every((r) => this.selected().has(r.id)));

  protected kind(k: string) {
    return KIND[k] ?? { label: k, glyph: '·' };
  }

  protected highlight(text: string): { t: string; hit: boolean }[] {
    return (text || '').split(MATCH).filter((t) => t !== '').map((t) => ({ t, hit: new RegExp(`^${MATCH.source}$`, 'i').test(t) }));
  }

  protected actionText(a: NearbyAction): string {
    const type = a.type === 'CASH_DIVIDEND' ? 'cash' : a.type === 'SPLIT' ? 'split' : a.type === 'SPLIT_INFO' ? 'split (info)' : 'spin-off';
    const v = a.type === 'CASH_DIVIDEND' || a.type === 'SPIN_OFF' ? `$${a.value.toFixed(2)}` : `×${a.value}`;
    const share = a.shareOfClose !== null ? ` (${(a.shareOfClose * 100).toFixed(1)}% of close)` : '';
    return `${type} ${v}${share} · ${a.exDate}`;
  }

  protected moveTone(m: number): string {
    const a = Math.abs(m);
    return a >= 0.2 ? 'bad' : a >= 0.1 ? 'warn' : 'neutral';
  }

  protected show(key: CandidateStatus): void {
    this.selected.set(new Set());
    void this.router.navigate([], { queryParams: { status: key === 'gap' ? null : key }, queryParamsHandling: 'merge' });
  }

  protected search(): void {
    void this.router.navigate([], { queryParams: { symbol: this.symbolDraft.trim().toUpperCase() || null }, queryParamsHandling: 'merge' });
  }

  protected pick(id: number, on: boolean): void {
    const s = new Set(this.selected());
    if (on) s.add(id); else s.delete(id);
    this.selected.set(s);
  }

  protected clearSelection(): void {
    this.selected.set(new Set());
  }

  protected pickAll(on: boolean): void {
    this.selected.set(on ? new Set(this.rows().map((r) => r.id)) : new Set());
  }

  protected one(c: ActionCandidate, review: CandidateReview): void {
    this.run(this.api.reviewCandidates([c.id], review), `${c.symbol} ${c.acceptedAt.slice(0, 10)}: ${REVIEW_LABEL[review].slice(2)}.`);
  }

  protected reviewMany(review: CandidateReview): void {
    const ids = [...this.selected()];
    this.run(this.api.reviewCandidates(ids, review, this.bulkNote), `${ids.length} candidates ${REVIEW_LABEL[review].slice(2)}.`, () => {
      this.selected.set(new Set());
      this.bulkNote = '';
    });
  }

  protected link(c: ActionCandidate, a: NearbyAction): void {
    this.run(this.api.reviewCandidates([c.id], 'CONFIRMED', `the feed recorded it: ${this.actionText(a)}`, a.id),
             `${c.symbol}: confirmed and matched to the feed's ${this.actionText(a)}. The gap is closed.`);
  }

  protected openSpin(c: ActionCandidate): void {
    this.spin = { exDate: '', value: null, note: '' };
    this.spinFor.set(c.id);
  }

  protected saveSpin(c: ActionCandidate): void {
    this.run(this.api.enterSpinOff(c.id, this.spin.exDate, Number(this.spin.value), this.spin.note),
             `${c.symbol}: spin-off recorded for ${this.spin.exDate}. Prices use it from the next pipeline run.`, () => this.spinFor.set(null));
  }

  protected detect(): void {
    this.run(this.api.detectCorporateActions(), 'Queued: the worker reads the new 8-Ks; reload in a minute.');
  }

  private run(obs: Observable<unknown>, ok: string, after?: () => void): void {
    this.busy.set(true);
    this.message.set(null);
    obs.subscribe({
      next: () => {
        this.busy.set(false);
        this.message.set({ text: ok, error: false });
        after?.();
        this.res.reload();
      },
      error: (e) => {
        this.busy.set(false);
        this.message.set({ text: errorMessage(e), error: true });
      },
    });
  }
}
