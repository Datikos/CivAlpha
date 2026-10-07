import { httpResource } from '@angular/common/http';
import { Observable } from 'rxjs';
import { Component, computed, inject, input, signal } from '@angular/core';
import { LowerCasePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { DotWhisker, WhiskerItem } from '../charts/dot-whisker';
import { ApiService, apiUrl, errorMessage, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtSignedPct } from '../core/format';
import { MetaService } from '../core/meta.service';
import {
  AdviceAction,
  AdviceLayer,
  AdviceResponse,
  HoldingsResponse,
  PortfolioSummary,
  TrackRecordResponse,
} from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

const LABEL: Record<AdviceAction, string> = {
  NOT_COVERED: 'Not covered',
  REVIEW: 'Check the data',
  TRIM: 'Trim',
  SELL: 'Sell',
  ADD: 'Add',
  HOLD: 'Hold',
};
const TONE: Record<AdviceAction, string> = {
  NOT_COVERED: 'neutral',
  REVIEW: 'warn',
  TRIM: 'warn',
  SELL: 'bad',
  ADD: 'good',
  HOLD: 'info',
};
const GLYPH: Record<AdviceAction, string> = { NOT_COVERED: '○', REVIEW: '⚠', TRIM: '◐', SELL: '▼', ADD: '▲', HOLD: '●' };
const LAYER_LABEL: Record<AdviceLayer, string> = {
  DATA: 'data check',
  RISK: 'risk rule',
  MODEL: 'model opinion',
  NONE: 'no rule fired',
};
const LAYER_HINT: Record<AdviceLayer, string> = {
  DATA: 'The numbers for this stock are missing or look broken. Nothing else is advised until that is resolved.',
  RISK: 'Sizing and concentration: a rule that needs no forecast skill. It does not claim to raise returns, it limits how much one stock can hurt.',
  MODEL: 'The book model\'s forecast of beating the sector ETF over 21 trading days. Shown as the headline only after the model passes its pre-registered live test.',
  NONE: 'Nothing fired: the position is inside its size band and the probability is between the exit and entry thresholds.',
};

@Component({
  selector: 'app-portfolio',
  imports: [FormsModule, LowerCasePipe, RouterLink, DotWhisker, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="wallet" area="strategy" />
        <div>
          <h1>My portfolio</h1>
          <p class="muted">
            Enter the stocks you hold and the platform gives each one an action for today: check the data, trim, sell,
            add or hold, with the shares to trade, the date it is next worth looking at and what would change it. Risk
            rules act now; the model's opinion is shown beside them and becomes the headline only after the model passes
            its pre-registered live test. Research software, not investment advice: it places no orders.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-portfolio" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <button type="button" class="btn btn-primary" (click)="refresh()" [disabled]="busy() || !holdingsCount()">
          {{ busy() ? 'Working…' : 'Refresh advice' }}
        </button>
      </div>
    </div>

    <div class="switcher">
      <label class="field">
        <span>Portfolio <app-help text="Each portfolio has its own holdings, cash, advice and track record. In accounts mode each person sees only their own portfolios and can keep up to 20." topic="page-portfolio" label="portfolio" /></span>
        @if (portfolios().length) {
          <select (change)="choose($any($event.target).value)" [disabled]="busy()">
            @for (p of portfolios(); track p.id) {
              <option [value]="p.id" [selected]="p.id === shownId()">{{ p.name }} · {{ p.holdings }} holding{{ p.holdings === 1 ? '' : 's' }}</option>
            }
          </select>
        } @else {
          <span class="none small muted">none yet: the first holding or cash entry creates "default"</span>
        }
      </label>
      @if (newOpen()) {
        <form class="new-form" (ngSubmit)="createPortfolio()">
          <label class="field">
            <span class="req">Name of the new portfolio</span>
            <input name="pname" [(ngModel)]="newName" required maxlength="100" placeholder="e.g. Pension" />
          </label>
          <button type="submit" class="btn btn-primary" [disabled]="busy() || !newName.trim()">Create</button>
          <button type="button" class="btn" (click)="newOpen.set(false)">Cancel</button>
        </form>
      } @else {
        <button type="button" class="btn" (click)="newOpen.set(true)" [disabled]="busy()"><app-icon name="plus" [size]="15" /> New portfolio</button>
      }
    </div>
    <app-status [res]="portfoliosRes" what="your portfolios" />

    @if (meta.meta(); as m) {
      @if (m.authMode === 'token' && !m.adminTokenRequired) {
      <div class="alert alert-warn small">
        <strong>No admin token is set on this installation.</strong> Anyone who can reach this address can read your holdings.
        Set <code>CIVALPHA_ADMIN_TOKEN</code> and keep the stack on localhost (see the README).
      </div>
      }
    }
    @if (message(); as m) {
      <div class="alert" [class.alert-error]="m.error" [class.alert-ok]="!m.error" role="status">{{ m.text }}</div>
    }
    <app-status [res]="holdingsRes" what="your holdings" />

    @if (advice(); as a) {
      @if (a.advice.length) {
        <div class="stats wide">
          <div class="stat tone-info">
            <div class="stat-label">Portfolio value <app-help text="Holdings at the close of the advice date plus your cash. A stock outside the universe has no price and is counted at your average cost." topic="page-portfolio" label="portfolio value" /></div>
            <div class="stat-value">{{ a.totalUsd | usd }}</div>
            <div class="stat-sub">{{ a.investedUsd | usd }} in {{ a.advice.length }} holdings · {{ a.cashUsd | usd }} cash</div>
            <app-meter [value]="a.totalUsd ? a.investedUsd / a.totalUsd : 0" tone="info" label="Invested share" />
          </div>
          <div class="stat" [class]="'stat tone-' + (actNow() ? 'warn' : 'good')">
            <div class="stat-label">To act on <app-help text="Holdings whose headline is not Hold: data to check, a trim for size, or (once the model is proven) a sell or add." topic="page-portfolio" label="actions" /></div>
            <div class="stat-value">{{ actNow() }}<span class="unit">of {{ a.advice.length }}</span></div>
            <div class="stat-sub">
              @for (k of order; track k) {
                @if (counts()[k]) { <span>{{ glyph[k] }} {{ label[k] }} {{ counts()[k] }} </span> }
              }
            </div>
          </div>
          <div class="stat" [class]="'stat tone-' + (a.liveTest.verdict === 'PASS' ? 'good' : a.liveTest.verdict === 'FAIL' ? 'bad' : 'warn')">
            <div class="stat-label">Model evidence <app-help text="The pre-registered live test of the book model: at least 500 resolved live forecasts, AUC at least 0.53 and a Brier score below the base rate. Until it passes, Sell and Add are shown as the model's opinion and the headline stays Hold." topic="page-accuracy" label="live test" /></div>
            <div class="stat-value">{{ a.liveTest.verdict }}<span class="unit">live test</span></div>
            <div class="stat-sub">{{ a.liveTest.resolved }} of {{ a.liveTest.minResolved }} forecasts resolved@if (a.liveTest.auc !== null) { · AUC {{ a.liveTest.auc | fixed: 3 }} }</div>
            <app-progress [done]="a.liveTest.resolved" [total]="a.liveTest.minResolved" label="Resolved forecasts" />
          </div>
          <div class="stat" [class]="'stat tone-' + (a.stale ? 'warn' : 'info')">
            <div class="stat-label">Advice date <app-help text="The recorded book's decision date the advice uses. The advice is made after every pipeline run and when you press Refresh advice." topic="page-portfolio" label="advice date" /></div>
            <div class="stat-value">{{ a.asOfDate | day }}</div>
            <div class="stat-sub">@if (a.stale) { newer decisions exist ({{ a.latestDecisionDate }}): refresh } @else { next review by {{ a.advice[0].reviewOn | day }} }</div>
          </div>
        </div>

        @if (a.liveTest.verdict !== 'PASS') {
          <app-verdict tone="warn" title="The model is not proven yet.">
            Its Sell and Add opinions are shown on each card, but the headline stays Hold until the live test passes
            ({{ a.liveTest.resolved }} of {{ a.liveTest.minResolved }} forecasts resolved so far). Trims and data checks act now:
            they limit risk and need no forecast skill.
          </app-verdict>
        }
        @if (a.missing.length) {
          <div class="alert alert-info small">No advice yet for {{ a.missing.join(', ') }}: press Refresh advice.</div>
        }

        <div class="cards">
          @for (r of a.advice; track r.symbol) {
            <article class="card advice" [class.quiet]="r.headline === 'HOLD'">
              <header>
                <div>
                  @if (r.companyId) {
                    <a [routerLink]="['/companies', r.symbol]"><strong>{{ r.symbol }}</strong></a>
                  } @else {
                    <strong>{{ r.symbol }}</strong>
                  }
                  <span class="small muted"> {{ r.name ?? '' }}</span>
                </div>
                <span class="badge" [class]="'badge tone-' + tone[r.headline]" [title]="layerHint[r.layer]">{{ glyph[r.headline] }} {{ label[r.headline] }}</span>
              </header>
              <p class="small line">
                <span class="chip" [class]="'chip tone-' + (r.layer === 'MODEL' ? (r.model.proven ? 'good' : 'neutral') : r.layer === 'NONE' ? 'info' : 'warn')" [title]="layerHint[r.layer]">{{ layerLabel[r.layer] }}</span>
                @if (r.changed) {
                  <span class="chip tone-warn" title="The headline differs from the previous advice date">changed from {{ label[r.previousHeadline!] | lowercase }}</span>
                }
                @if (r.headline !== r.action) {
                  <span class="chip tone-neutral" [title]="'The model says ' + label[r.action] + '; shown as an opinion until the live test passes'">model says {{ label[r.action] | lowercase }}, unproven</span>
                }
              </p>

              @if (r.tradeShares !== null && (r.headline !== 'HOLD' || r.action !== 'HOLD')) {
                <p class="trade" [class]="'trade tone-' + tone[r.action]">
                  {{ r.tradeShares < 0 ? 'Sell' : 'Buy' }} <strong>{{ abs(r.tradeShares) | num }}</strong> shares
                  @if (r.close !== null) { ≈ {{ abs(r.tradeShares) * r.close | usd }} at {{ r.close | usd }} }
                  @if (r.headline !== r.action) { <span class="small muted">(if you follow the opinion)</span> }
                </p>
              }

              <div class="size">
                <div class="size-head small">
                  <span>weight {{ r.weight | pct: 1 }}</span>
                  @if (r.targetWeight !== null) { <span class="muted">target {{ r.targetWeight | pct: 1 }} · cap 20%</span> }
                </div>
                <app-meter [value]="r.weight" [max]="0.3" [target]="r.targetWeight" targetLabel="volatility size (0.04 / 21-day volatility, at most 20%)"
                           [tone]="r.action === 'TRIM' ? 'warn' : 'info'" [label]="'Weight of ' + r.symbol" />
              </div>

              @if (r.model.probability !== undefined) {
                <p class="small prob-line">
                  <span class="chip" [class]="'chip tone-' + (r.model.probability! < r.model.exitP! ? 'bad' : r.model.probability! >= r.model.entryP! ? 'good' : 'info')"
                        [title]="'Probability of beating the sector ETF over the next 21 trading days; exit below ' + pctText(r.model.exitP!) + ', enter at ' + pctText(r.model.entryP!)">p = {{ r.model.probability | pct: 0 }}</span>
                  @if (r.model.probabilityCalibrated !== null && r.model.probabilityCalibrated !== undefined) {
                    <span class="muted" title="What this raw probability has meant out of sample (ADR-0002)">≈ {{ r.model.probabilityCalibrated | pct: 0 }} calibrated</span>
                  }
                  <span class="muted">rank {{ r.model.rank }} · book: {{ r.model.bookAction | lowercase }}</span>
                </p>
              }

              <ul class="reasons small">
                @for (x of r.reasons; track $index) {
                  <li [class]="'tone-' + tone[x.action]"><span class="glyph">{{ glyph[x.action] }}</span> {{ x.text }}</li>
                } @empty {
                  <li class="tone-info"><span class="glyph">●</span> Inside its size band, probability between the thresholds: nothing to do.</li>
                }
              </ul>

              <div class="small when">
                <app-icon name="hourglass" [size]="14" /> Look again by <strong>{{ r.reviewOn | day }}</strong>
                @if (r.triggers.length) { or sooner if: }
              </div>
              @if (r.triggers.length) {
                <div class="chips">
                  @for (t of r.triggers; track t.action) {
                    <span class="chip" [class]="'chip tone-' + tone[t.action]">{{ label[t.action] }} if {{ t.when }}</span>
                  }
                </div>
              }
              @if (r.outcome; as o) {
                <p class="small muted">Since: {{ signed(o.excessReturn) }} against the sector ETF by {{ o.windowEndDate | day }}.</p>
              }
            </article>
          }
        </div>

        @if (a.ideas.length) {
          <section class="card ideas">
            <h2>Buy ideas from the book <app-help text="The recorded book's positions (Enter and Hold) that you do not own, sized as the book sizes them: 0.04 / 21-day volatility, at most 20% of your portfolio. They carry the same evidence grade as the model's opinions." topic="page-decisions" label="buy ideas" /></h2>
            <p class="small muted">The AI_SIZED book holds these today. @if (a.liveTest.verdict === 'PASS') { The model has passed its live test. } @else { The model is unproven: read them as opinions. }</p>
            <div class="table-wrap">
              <table class="table compact">
                <thead><tr><th>Stock</th><th>Book</th><th class="num">p</th><th class="num">Calibrated</th><th class="num">Rank</th><th class="num">Size</th><th class="num">Amount</th></tr></thead>
                <tbody>
                  @for (i of a.ideas; track i.symbol) {
                    <tr>
                      <td><a [routerLink]="['/companies', i.symbol]"><strong>{{ i.symbol }}</strong></a><span class="small muted name">{{ i.name }}</span></td>
                      <td>{{ i.bookAction | lowercase }}</td>
                      <td class="num">{{ i.probability | pct: 0 }}</td>
                      <td class="num">{{ i.probabilityCalibrated | pct: 0 }}</td>
                      <td class="num">{{ i.rank }}</td>
                      <td class="num">{{ i.targetWeight | pct: 1 }}</td>
                      <td class="num">{{ i.amountUsd | usd }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </section>
        }
      } @else if (holdingsCount()) {
        <div class="empty-box">
          No advice yet. Press <strong>Refresh advice</strong>; it needs the AI decisions of a trading day
          (<a routerLink="/decisions">AI decisions</a>, also made by every pipeline run).
        </div>
      }
    }

    <section class="card">
      <h2>Holdings <app-help text="One row per stock, in US dollars, long positions only. Edits take effect at the next Refresh advice; the advice already given stays recorded." topic="page-portfolio" label="holdings" /></h2>
      <form class="form-grid" (ngSubmit)="save()">
        <label class="field">
          <span class="req">Symbol</span>
          <input name="symbol" [(ngModel)]="draft.symbol" required placeholder="e.g. MSFT" autocomplete="off" />
        </label>
        <label class="field">
          <span class="req">Shares</span>
          <input name="shares" type="number" min="0" step="any" [(ngModel)]="draft.shares" required />
        </label>
        <label class="field">
          <span class="req">Average cost (USD)</span>
          <input name="cost" type="number" min="0" step="any" [(ngModel)]="draft.avgCostUsd" required />
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
          <button type="submit" class="btn btn-primary" [disabled]="busy() || !draft.symbol.trim() || !(draft.shares! > 0)">Save holding</button>
        </div>
      </form>

      @if (holdings(); as h) {
        @if (h.holdings.length) {
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>Stock</th><th class="num">Shares</th><th class="num">Avg cost</th><th class="num">Cost basis</th><th>Bought</th><th>Note</th><th></th></tr></thead>
              <tbody>
                @for (x of h.holdings; track x.symbol) {
                  <tr>
                    <td><strong>{{ x.symbol }}</strong><span class="small muted name">{{ x.name ?? 'not in the universe' }}</span></td>
                    <td class="num">{{ x.shares | num: 2 }}</td>
                    <td class="num">{{ x.avgCostUsd | usd }}</td>
                    <td class="num">{{ x.shares * x.avgCostUsd | usd }}</td>
                    <td>{{ x.openedOn | day }}</td>
                    <td class="small">{{ x.note }}</td>
                    <td class="actions">
                      <button type="button" class="btn btn-sm" (click)="edit(x.symbol)">Edit</button>
                      <button type="button" class="btn btn-sm" (click)="remove(x.symbol)" [disabled]="busy()">Remove</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <div class="empty-box">No holdings yet. Enter the first one above.</div>
        }
        <form class="cash" (ngSubmit)="saveCash()">
          <label class="field">
            <span>Cash (USD) <app-help text="Cash you could invest. An Add is limited to it; it also counts in the portfolio value the weights are measured against." topic="page-portfolio" label="cash" /></span>
            <input name="cash" type="number" min="0" step="any" [(ngModel)]="cashDraft" [placeholder]="h.cashUsd" />
          </label>
          <button type="submit" class="btn" [disabled]="busy() || cashDraft === null">Save cash</button>
          <span class="small muted">now {{ h.cashUsd | usd }}</span>
        </form>
      }
    </section>

    @if (track(); as t) {
      @if (t.actions.some(hasRows)) {
        <section class="card">
          <h2>Track record <app-help text="What happened over the 21 trading days after each piece of advice: the stock's total return minus its sector ETF's. Sell and Trim score well when it is negative. A mean and its interval appear once 30 rows of an action have resolved." topic="page-portfolio" label="track record" /></h2>
          @if (trackItems().length) {
            <app-dot-whisker [items]="trackItems()" [refX]="0" refLabel="no difference" [betterIs]="null" [format]="pctFmt" label="Excess return after the advice, by action" />
          }
          <table class="table compact">
            <thead><tr><th>Action</th><th>Layer</th><th class="num">Advised</th><th class="num">Resolved</th><th class="num">Mean excess</th><th class="num">95% interval</th></tr></thead>
            <tbody>
              @for (r of t.actions; track r.action) {
                @if (r.advised) {
                  <tr>
                    <td><span class="chip" [class]="'chip tone-' + tone[r.action]">{{ glyph[r.action] }} {{ label[r.action] }}</span></td>
                    <td class="small">{{ layerLabel[r.layer] }}</td>
                    <td class="num">{{ r.advised }}</td>
                    <td class="num">{{ r.resolved }}</td>
                    <td class="num">{{ r.meanExcess === null ? 'below ' + t.minResolved : signed(r.meanExcess) }}</td>
                    <td class="num">{{ r.ci ? signed(r.ci[0]) + ' to ' + signed(r.ci[1]) : '—' }}</td>
                  </tr>
                }
              }
            </tbody>
          </table>
          <p class="small muted">{{ t.note }}</p>
        </section>
      }
    }
    @if (advice(); as a) { <p class="small muted">{{ a.note }}</p> }
  `,
  styles: `
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; margin: 1rem 0; }
    .advice header { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
    .advice.quiet { opacity: 0.94; }
    .line, .prob-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.4rem; margin: 0.4rem 0; }
    .trade { margin: 0.4rem 0; padding: 0.35rem 0.6rem; border-left: 3px solid var(--tone-mark); background: var(--tone-bg); border-radius: var(--radius); }
    .size { margin: 0.5rem 0; }
    .size-head { display: flex; justify-content: space-between; margin-bottom: 0.2rem; }
    .reasons { list-style: none; padding: 0; margin: 0.5rem 0; display: grid; gap: 0.3rem; }
    .reasons li { padding-left: 1.3rem; position: relative; }
    .reasons .glyph { position: absolute; left: 0; color: var(--tone-mark); }
    .when { display: flex; align-items: center; gap: 0.35rem; margin: 0.4rem 0 0.3rem; }
    .ideas { margin-bottom: 1rem; }
    .cash { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.75rem; margin-top: 1rem; }
    .actions { white-space: nowrap; text-align: right; }
    .actions .btn + .btn { margin-left: 0.3rem; }
    .switcher { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.75rem; margin: 0 0 1rem; }
    .switcher select { min-width: 14rem; }
    .switcher .none { padding: 0.45rem 0; }
    .new-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.5rem; }
    section.card { margin-bottom: 1rem; }
    td .name { margin-left: 0.4rem; }
  `,
})
export class PortfolioPage {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly meta = inject(MetaService);
  /** `?p=<id>`: the chosen portfolio, bound by the router; without it the backend uses the caller's first one. */
  readonly p = input<string | undefined>(undefined);
  protected readonly selectedId = computed(() => {
    const n = Number(this.p());
    return this.p() && Number.isInteger(n) && n > 0 ? n : null;
  });
  protected readonly portfoliosRes = httpResource<PortfolioSummary[]>(() => apiUrl.portfolios());
  protected readonly holdingsRes = httpResource<HoldingsResponse>(() => apiUrl.portfolioHoldings(this.selectedId()));
  protected readonly adviceRes = httpResource<AdviceResponse>(() => apiUrl.portfolioAdvice(null, this.selectedId()));
  protected readonly trackRes = httpResource<TrackRecordResponse>(() => apiUrl.portfolioTrackRecord(this.selectedId()));
  protected readonly portfolios = computed(() => valueOf(this.portfoliosRes) ?? []);
  /** The portfolio on screen: the chosen one, else the one the backend answered with (the caller's first). */
  protected readonly shownId = computed(() => this.selectedId() ?? this.holdings()?.portfolio?.id ?? null);
  protected readonly newOpen = signal(false);
  protected newName = '';
  protected readonly label = LABEL;
  protected readonly tone = TONE;
  protected readonly glyph = GLYPH;
  protected readonly layerLabel = LAYER_LABEL;
  protected readonly layerHint = LAYER_HINT;
  protected readonly order: AdviceAction[] = ['REVIEW', 'TRIM', 'SELL', 'ADD', 'NOT_COVERED', 'HOLD'];
  protected readonly busy = signal(false);
  protected readonly message = signal<{ text: string; error: boolean } | null>(null);
  protected draft = { symbol: '', shares: null as number | null, avgCostUsd: null as number | null, openedOn: '', note: '' };
  protected cashDraft: number | null = null;
  protected readonly pctFmt = (v: number) => fmtSignedPct(v, 1);

  protected readonly holdings = computed(() => valueOf(this.holdingsRes));
  protected readonly advice = computed(() => valueOf(this.adviceRes));
  protected readonly track = computed(() => valueOf(this.trackRes));
  protected readonly holdingsCount = computed(() => this.holdings()?.holdings.length ?? 0);
  protected readonly counts = computed(() => {
    const c: Record<AdviceAction, number> = { NOT_COVERED: 0, REVIEW: 0, TRIM: 0, SELL: 0, ADD: 0, HOLD: 0 };
    for (const r of this.advice()?.advice ?? []) c[r.headline]++;
    return c;
  });
  protected readonly actNow = computed(() => (this.advice()?.advice ?? []).filter((r) => r.headline !== 'HOLD').length);
  protected readonly trackItems = computed<WhiskerItem[]>(() =>
    (this.track()?.actions ?? [])
      .filter((r) => r.meanExcess !== null)
      .map((r) => ({
        row: r.action,
        label: LABEL[r.action],
        value: r.meanExcess!,
        lo: r.ci?.[0] ?? null,
        hi: r.ci?.[1] ?? null,
        details: [`${r.resolved} resolved of ${r.advised} advised`, LAYER_LABEL[r.layer]],
      })),
  );

  protected hasRows = (r: { advised: number }) => r.advised > 0;
  protected abs = Math.abs;
  protected signed = (v: number) => fmtSignedPct(v, 1);
  protected pctText = (v: number) => `${Math.round(v * 100)}%`;

  protected save(): void {
    const sym = this.draft.symbol.trim().toUpperCase();
    this.run(
      this.api.setHolding({
        symbol: sym,
        shares: Number(this.draft.shares),
        avgCostUsd: Number(this.draft.avgCostUsd ?? 0),
        openedOn: this.draft.openedOn || null,
        note: this.draft.note.trim() || null,
        portfolioId: this.selectedId(),
      }),
      `${sym} saved. Press Refresh advice to advise on the portfolio as it is now.`,
      () => (this.draft = { symbol: '', shares: null, avgCostUsd: null, openedOn: '', note: '' }),
    );
  }

  protected edit(symbol: string): void {
    const x = this.holdings()?.holdings.find((h) => h.symbol === symbol);
    if (x) this.draft = { symbol: x.symbol, shares: x.shares, avgCostUsd: x.avgCostUsd, openedOn: x.openedOn ?? '', note: x.note ?? '' };
  }

  protected remove(symbol: string): void {
    if (!confirm(`Remove ${symbol} from your holdings? Its past advice stays recorded.`)) return;
    this.run(this.api.removeHolding(symbol, this.selectedId()), `${symbol} removed.`);
  }

  protected saveCash(): void {
    this.run(this.api.setCash(Number(this.cashDraft), this.selectedId()), 'Cash saved.', () => (this.cashDraft = null));
  }

  protected refresh(): void {
    this.run(this.api.refreshAdvice(this.selectedId()), 'Advice refreshed.');
  }

  protected choose(id: string): void {
    void this.router.navigate([], { queryParams: { p: id || null }, queryParamsHandling: 'merge' });
  }

  protected createPortfolio(): void {
    const name = this.newName.trim();
    this.run(this.api.createPortfolio(name), `Portfolio "${name}" created. Enter its holdings below.`, (p) => {
      this.newName = '';
      this.newOpen.set(false);
      this.choose(String((p as PortfolioSummary).id));
    });
  }

  private run(obs: Observable<unknown>, ok: string, after?: (v: unknown) => void): void {
    this.busy.set(true);
    this.message.set(null);
    obs.subscribe({
      next: (v) => {
        this.busy.set(false);
        this.message.set({ text: ok, error: false });
        after?.(v);
        this.portfoliosRes.reload();
        this.holdingsRes.reload();
        this.adviceRes.reload();
        this.trackRes.reload();
      },
      error: (e) => {
        this.busy.set(false);
        const text = errorMessage(e);
        this.message.set({
          text:
            text.includes('401') && this.meta.meta()?.authMode !== 'accounts'
              ? 'The admin token is required: enter it on the Data & pipeline page.'
              : text,
          error: true,
        });
      },
    });
  }
}
