import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DivergingBars, DivergingItem } from '../charts/diverging-bars';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtNum, fmtSigned } from '../core/format';
import { AiDecision, DecisionAction, DecisionsResponse } from '../core/models';
import { UI } from '../shared/ui';

const ACTION_LABEL: Record<DecisionAction, string> = { ENTER: 'Enter', EXIT: 'Exit', HOLD: 'Hold', STAY_OUT: 'Stay out' };

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
  imports: [RouterLink, DivergingBars, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
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
      <a class="btn" routerLink="/strategies/AI_GBM">How it did in the backtest →</a>
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
              <span class="chip">{{ actionLabel[a] }}: {{ counts()[a] }}</span>
            }
            <span class="chip">Invested: {{ invested() | pct: 0 }}</span>
          </span>
          <app-demo-badge [show]="decisions()[0].isDemo" />
        </div>
        <p class="small muted">
          Enter when p ≥ {{ decisions()[0].entryP | pct: 0 }} and the stock ranks in the top {{ maxPositions() }}; exit when
          p &lt; {{ decisions()[0].exitP | pct: 0 }}. Each position gets 1/{{ maxPositions() }} of capital, the rest stays in
          cash. Model trained on {{ decisions()[0].model.nTrain | num }} samples through {{ decisions()[0].model.trainedThrough }}.
        </p>

        <div class="cards">
          @for (d of decisions(); track d.id) {
            <article class="card decision" [class.quiet]="d.action === 'HOLD' || d.action === 'STAY_OUT'">
              <header>
                <div>
                  <a [routerLink]="['/companies', d.symbol]"><strong>{{ d.symbol }}</strong></a>
                  <span class="small muted"> {{ d.name }}</span>
                </div>
                <span class="badge" [class]="'badge action-' + d.action">{{ actionLabel[d.action] }}</span>
              </header>
              <p class="prob">
                p = <strong>{{ d.probability | pct: 0 }}</strong>
                <span class="small muted">· rank {{ d.rank }} · weight {{ d.weight | pct: 1 }}</span>
              </p>
              <div class="meter" role="img" [attr.aria-label]="'Probability ' + (d.probability * 100).toFixed(0) + '%, exit below ' + (d.exitP * 100).toFixed(0) + '%, enter at ' + (d.entryP * 100).toFixed(0) + '%'">
                <span class="track"></span>
                <span class="tick" [style.left.%]="d.exitP * 100" title="exit threshold"></span>
                <span class="tick" [style.left.%]="d.entryP * 100" title="entry threshold"></span>
                <span class="dot" [style.left.%]="d.probability * 100"></span>
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
                    <span class="chip" [class.vote-in]="v.inPosition" [title]="v.inPosition ? 'This rule would hold the stock' : 'This rule would not hold it'">
                      {{ v.inPosition ? '✓' : '–' }} {{ v.label }}
                    </span>
                  }
                </div>
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
    .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
    .toolbar select { margin-left: 0.4rem; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }
    .decision header { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
    .decision.quiet { opacity: 0.92; }
    .prob { margin: 0.4rem 0 0.25rem; }
    .meter { position: relative; height: 18px; margin: 0 0 0.6rem; }
    .meter .track { position: absolute; left: 0; right: 0; top: 8px; height: 2px; background: var(--grid); }
    .meter .tick { position: absolute; top: 3px; width: 2px; height: 12px; margin-left: -1px; background: var(--ink-muted); }
    .meter .dot { position: absolute; top: 4px; width: 10px; height: 10px; margin-left: -5px; border-radius: 50%;
      background: var(--series-2); box-shadow: 0 0 0 2px var(--chart-surface); }
    .explain { background: var(--surface-2); border-radius: var(--radius); padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; }
    .explain p { margin: 0.25rem 0 0; }
    details summary { cursor: pointer; margin-bottom: 0.5rem; }
    .votes { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.5rem; }
    .chip.vote-in { border-color: var(--ink-2); font-weight: 600; }
    .badge.action-ENTER { background: var(--good-bg); color: var(--good-ink); }
    .badge.action-EXIT { background: var(--bad-bg); color: var(--bad-ink); }
  `,
})
export class DecisionsPage {
  protected readonly date = signal<string | null>(null);
  protected readonly res = httpResource<DecisionsResponse>(() => apiUrl.decisions(this.date()));
  protected readonly actionLabel = ACTION_LABEL;
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
}
