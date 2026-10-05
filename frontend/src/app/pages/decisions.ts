import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { DivergingBars, DivergingItem } from '../charts/diverging-bars';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtNum, fmtSigned } from '../core/format';
import { AiDecision, DecisionAction, DecisionsResponse } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

const ACTION_LABEL: Record<DecisionAction, string> = { ENTER: 'Enter', EXIT: 'Exit', HOLD: 'Hold', STAY_OUT: 'Stay out' };
const ACTION_TONE: Record<DecisionAction, string> = { ENTER: 'good', EXIT: 'bad', HOLD: 'info', STAY_OUT: 'neutral' };
const ACTION_GLYPH: Record<DecisionAction, string> = { ENTER: '▲', EXIT: '▼', HOLD: '●', STAY_OUT: '○' };
const ACTION_HINT: Record<DecisionAction, string> = {
  ENTER: 'Buy at the next close: the probability is at or above the entry threshold and the stock ranks high enough.',
  EXIT: 'Sell at the next close: the probability fell below the exit threshold.',
  HOLD: 'Keep the position: still above the exit threshold.',
  STAY_OUT: 'No position: the probability is below the entry threshold or the stock ranks too low.',
};

const RULE_LABEL: Record<string, string> = {
  SMA_50_200: 'Golden cross',
  SMA_50_200_TSTOP10: 'Golden cross + stop',
  MOM_12_1: '12-1 momentum',
  DONCHIAN_55_20: 'Donchian breakout',
  RSI2_SMA200: 'RSI(2) pullback',
  BOLLINGER_20_2: 'Bollinger bounce',
  REVERSAL_5D: '5-day reversal',
  QUALITY_GROWTH: 'Quality & growth',
  PEAD_SUE: 'Earnings-surprise drift',
  VALUE_EY: 'Value (earnings yield)',
  GROSS_PROFIT: 'Gross profitability',
  EVENT_AVOID: 'Event avoidance',
};

@Component({
  selector: 'app-decisions',
  imports: [RouterLink, DivergingBars, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="sparkles" area="strategy" />
        <div>
          <h1>AI decisions</h1>
          <p class="muted">
            What the AI strategy decides for each stock after the close. A gradient-boosted model combines every classic
            rule's indicator with the quarterly-report profile (growth, earnings surprise, margins, balance sheet, valuation),
            tariff/rate shocks and macro, and estimates the probability that the stock
            beats its sector ETF over the next {{ horizon() }} trading days. The decision comes from fixed thresholds;
            nobody overrides it.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-decisions" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a class="btn" routerLink="/strategies/AI_GBM">How it did in the backtest →</a>
      </div>
    </div>

    <app-status [res]="res" what="decisions" />

    @if (res.hasValue()) {
      @if (decisions().length) {
        <div class="toolbar">
          <label class="small">
            Date
            <select (change)="date.set($any($event.target).value)">
              @for (d of dates(); track d) {
                <option [value]="d" [selected]="d === asOf()">{{ d }}</option>
              }
            </select>
          </label>
          <span class="chips">
            @for (a of actionOrder; track a) {
              <span class="chip" [class]="'chip tone-' + actionTone[a]" [title]="actionHint[a]">{{ actionGlyph[a] }} {{ actionLabel[a] }}: {{ counts()[a] }}</span>
            }
          </span>
        </div>

        <div class="stats wide">
          <div class="stat tone-good">
            <div class="stat-label">Invested <app-help text="Share of capital in positions. Each position is 1/N of capital, so N positions mean N/N invested and the rest in cash." topic="exposure" label="invested share" /></div>
            <div class="stat-value">{{ invested() | pct: 0 }}<span class="unit">of capital</span></div>
            <div class="stat-sub">{{ positions() }} of {{ maxPositions() }} slots filled · rest in cash@if (sizedInvested() !== null) { · sized by volatility: {{ sizedInvested() | pct: 0 }} }</div>
            <app-meter [value]="invested()" tone="good" label="Invested share" />
          </div>
          <div class="stat tone-forecast">
            <div class="stat-label">Thresholds <app-help text="The AI buys when its probability is at least the entry threshold and the stock ranks in the top N; it sells when the probability drops below the exit threshold. The gap between the two avoids churn." topic="page-decisions" label="thresholds" /></div>
            <div class="stat-value">≥ {{ decisions()[0].entryP | pct: 0 }}<span class="unit">enter · exit &lt; {{ decisions()[0].exitP | pct: 0 }}</span></div>
            <div class="stat-sub">top {{ maxPositions() }} by probability, 1/{{ maxPositions() }} of capital each</div>
          </div>
          <div class="stat tone-info">
            <div class="stat-label">Model</div>
            <div class="stat-value">{{ decisions()[0].model.nTrain | num }}<span class="unit">training samples</span></div>
            <div class="stat-sub">trained through {{ decisions()[0].model.trainedThrough }} · {{ horizon() }}-day horizon</div>
          </div>
        </div>

        <div class="cards">
          @for (d of decisions(); track d.id) {
            <article class="card decision" [class.quiet]="d.action === 'HOLD' || d.action === 'STAY_OUT'">
              <header>
                <div>
                  <a [routerLink]="['/companies', d.symbol]"><strong>{{ d.symbol }}</strong></a>
                  <span class="small muted"> {{ d.name }}</span>
                </div>
                <span class="badge" [class]="'badge tone-' + actionTone[d.action]" [title]="actionHint[d.action]">{{ actionGlyph[d.action] }} {{ actionLabel[d.action] }}</span>
              </header>
              <p class="prob-line">
                <span class="prob" [class]="'prob tone-' + actionTone[d.action]">p = {{ d.probability | pct: 0 }}</span>
                <span class="small muted">rank {{ d.rank }} · weight {{ d.weight | pct: 1 }}</span>
                @if (d.model.sizing; as sz) {
                  @if (d.weight > 0) {
                    <span class="small muted" [title]="'Volatility-sized weight: ' + sz.volBudget + ' / annualized 21-day volatility' + (sz.vol21 === null ? ' (unknown, equal slice kept)' : ' of ' + (sz.vol21 * 100).toFixed(0) + '%') + ', at most ' + (sz.maxWeight * 100).toFixed(0) + '%'">· sized {{ sz.sizedWeight | pct: 1 }}</span>
                  }
                  @if (sz.confident) {
                    <span class="chip tone-good" [title]="'The probability clears the ' + (sz.confidentEntryP * 100).toFixed(0) + '% bar of the confident-entries rule'">✓ confident</span>
                  }
                }
              </p>
              <div class="meter-zoned" role="img" [attr.aria-label]="'Probability ' + (d.probability * 100).toFixed(0) + '%, exit below ' + (d.exitP * 100).toFixed(0) + '%, enter at ' + (d.entryP * 100).toFixed(0) + '%'">
                <span class="zone zone-exit" [style.width.%]="d.exitP * 100" title="exit zone"></span>
                <span class="zone zone-hold" [style.left.%]="d.exitP * 100" [style.width.%]="(d.entryP - d.exitP) * 100" title="hold zone"></span>
                <span class="zone zone-enter" [style.left.%]="d.entryP * 100" [style.width.%]="(1 - d.entryP) * 100" title="enter zone"></span>
                <span class="tick" [style.left.%]="d.exitP * 100" title="exit threshold"></span>
                <span class="tick" [style.left.%]="d.entryP * 100" title="entry threshold"></span>
                <span class="dot" [class]="'dot tone-' + actionTone[d.action]" [style.left.%]="d.probability * 100"></span>
              </div>
              <div class="zone-key small muted">
                <span>exit &lt; {{ d.exitP | pct: 0 }}</span>
                <span>hold</span>
                <span>enter ≥ {{ d.entryP | pct: 0 }}</span>
              </div>

              @if (d.explanation) {
                <div class="explain">
                  <div class="small muted">AI-written explanation — does not affect the decision ({{ d.explanationModel }})</div>
                  <p>{{ d.explanation }}</p>
                </div>
              }

              <details [open]="d.action === 'ENTER' || d.action === 'EXIT'">
                <summary class="small">Why: top factors and rule votes</summary>
                <app-diverging-bars
                  [items]="factorItems(d)"
                  [label]="'Factors moving the probability for ' + d.symbol"
                  posLabel="Raises probability"
                  negLabel="Lowers probability"
                  unitLabel="probability points"
                  [format]="ppFmt"
                />
                <div class="votes">
                  @for (v of votes(d); track v.key) {
                    <span class="chip" [class.tone-good]="v.inPosition" [class.vote-in]="v.inPosition" [title]="v.inPosition ? 'This rule would hold the stock' : 'This rule would not hold it'">
                      {{ v.inPosition ? '✓' : '–' }} {{ v.label }}
                    </span>
                  }
                </div>
                <p class="small muted" style="margin: 0.5rem 0 0">{{ votesFor(d) }} of {{ votes(d).length }} classic rules would hold {{ d.symbol }}.</p>
              </details>
            </article>
          }
        </div>
      } @else {
        <div class="empty-box">
          No AI decisions yet. Run <a routerLink="/admin">Data &amp; pipeline</a> → AI decisions (also part of every pipeline run).
        </div>
      }
    }
  `,
  styles: `
    .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
    .toolbar select { margin-left: 0.4rem; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }
    .decision header { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
    .decision.quiet { opacity: 0.92; }
    .prob-line { display: flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; margin: 0.5rem 0 0.35rem; }
    .prob-line .prob { font-size: 1.05rem; }
    .prob.tone-good, .prob.tone-bad, .prob.tone-info, .prob.tone-neutral { color: var(--tone-ink); background: var(--tone-bg); border-color: var(--tone-mark); }
    .meter-zoned { position: relative; height: 18px; margin: 0; }
    .meter-zoned .zone { position: absolute; top: 7px; height: 5px; border-radius: 3px; }
    .zone-exit { left: 0; background: color-mix(in srgb, var(--div-neg) 35%, var(--surface-3)); }
    .zone-hold { background: var(--surface-3); }
    .zone-enter { background: color-mix(in srgb, light-dark(#1b9c5a, #3fcf7f) 40%, var(--surface-3)); }
    .meter-zoned .tick { position: absolute; top: 3px; width: 2px; height: 13px; margin-left: -1px; background: var(--ink-muted); border-radius: 1px; }
    .meter-zoned .dot { position: absolute; top: 4px; width: 11px; height: 11px; margin-left: -5.5px; border-radius: 50%;
      background: var(--tone-mark); box-shadow: 0 0 0 2px var(--surface); }
    .zone-key { display: flex; justify-content: space-between; margin: 0.1rem 0 0.6rem; font-size: 0.72rem; }
    .explain { background: var(--surface-2); border-radius: var(--radius); padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; }
    .explain p { margin: 0.25rem 0 0; }
    details summary { cursor: pointer; margin-bottom: 0.5rem; }
    .votes { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.5rem; }
    .chip.vote-in { font-weight: 600; }
  `,
})
export class DecisionsPage {
  private readonly route = inject(ActivatedRoute);
  /** Chosen trading day; a `?date=` query parameter deep-links one (null = latest). */
  protected readonly date = signal<string | null>(this.route.snapshot.queryParamMap.get('date'));
  protected readonly res = httpResource<DecisionsResponse>(() => apiUrl.decisions(this.date()));
  protected readonly actionLabel = ACTION_LABEL;
  protected readonly actionTone = ACTION_TONE;
  protected readonly actionGlyph = ACTION_GLYPH;
  protected readonly actionHint = ACTION_HINT;
  protected readonly actionOrder: DecisionAction[] = ['ENTER', 'EXIT', 'HOLD', 'STAY_OUT'];
  protected readonly ppFmt = (v: number) => fmtSigned(v * 100, 1);

  protected readonly decisions = computed<AiDecision[]>(() => valueOf(this.res)?.decisions ?? []);
  protected readonly dates = computed(() => valueOf(this.res)?.dates ?? []);
  protected readonly asOf = computed(() => valueOf(this.res)?.asOfDate ?? null);
  protected readonly horizon = computed(() => this.decisions()[0]?.model.horizon ?? 10);
  protected readonly maxPositions = computed(() => {
    const d = this.decisions().find((x) => x.weight > 0);
    return d ? Math.round(1 / d.weight) : 8;
  });
  protected readonly invested = computed(() => this.decisions().reduce((s, d) => s + d.weight, 0));
  protected readonly positions = computed(() => this.decisions().filter((d) => d.weight > 0).length);
  /** What the volatility-sized rule would have invested today, or null for decisions stored before sizing existed. */
  protected readonly sizedInvested = computed(() => {
    const list = this.decisions();
    if (!list.length || !list.some((d) => d.model.sizing)) return null;
    return list.reduce((s, d) => s + (d.model.sizing?.sizedWeight ?? 0), 0);
  });
  protected readonly counts = computed(() => {
    const c: Record<DecisionAction, number> = { ENTER: 0, EXIT: 0, HOLD: 0, STAY_OUT: 0 };
    for (const d of this.decisions()) c[d.action]++;
    return c;
  });

  protected factorItems(d: AiDecision): DivergingItem[] {
    return d.factors.map((f) => ({
      key: f.feature,
      label: f.label,
      value: f.contribution,
      details: [
        `value ${f.value === null ? 'missing' : fmtNum(f.value, 3)}`,
        `typical ${f.median === null ? '—' : fmtNum(f.median, 3)}`,
      ],
    }));
  }

  protected votes(d: AiDecision): { key: string; label: string; inPosition: boolean }[] {
    return Object.entries(d.ruleVotes ?? {}).map(([key, inPosition]) => ({ key, label: RULE_LABEL[key] ?? key, inPosition }));
  }

  protected votesFor(d: AiDecision): number {
    return this.votes(d).filter((v) => v.inPosition).length;
  }
}
