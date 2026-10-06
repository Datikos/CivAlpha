import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { LineChart, LineSeries } from '../charts/line-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct, fmtSignedPct } from '../core/format';
import { StrategiesResponse, StrategyResult } from '../core/models';
import { createSort } from '../core/sort';
import { CoverageTable } from '../shared/coverage-table';
import { Icon } from '../shared/icon';
import { SortTh } from '../shared/sort-th';
import { UI } from '../shared/ui';
import { VIZ, verdictTone } from '../shared/viz';

export const AI_KEY = 'AI_GBM';
export const REFERENCE_KEY = 'EW_BUY_HOLD';

export const FAMILY_LABEL: Record<string, string> = {
  BENCHMARK: 'Benchmark',
  TREND: 'Trend / momentum',
  MEAN_REVERSION: 'Mean reversion',
  FUNDAMENTAL: 'Fundamental',
  EVENT: 'Event',
  SPECULATIVE: 'Speculative',
  AI: 'AI',
};

export function equitySeries(key: string, label: string, color: string, r: { equity: { date: string; equity: number }[] }): LineSeries {
  return { key, label, color, points: r.equity.map((p) => ({ x: Date.parse(p.date + 'T00:00:00Z'), y: p.equity })) };
}

export function supported(verdict: string): boolean {
  return verdict.includes('SUPPORTED by');
}

/** Growth of 1 shown as cumulative return. */
export const equityFormat = (v: number) => fmtSignedPct(v - 1, 0);

interface Col {
  key: string;
  label: string;
  hint: string;
  topic: string;
  get: (r: StrategyResult) => number | null | undefined;
  fmt: (v: number) => string;
  higherIsBetter: boolean | null;
  /** how the cell is drawn */
  view: 'bar' | 'signed-bar' | 'plain' | 'ci' | 'dsr';
}

@Component({
  selector: 'app-strategies',
  imports: [RouterLink, FormsModule, LineChart, Icon, SortTh, CoverageTable, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="flask" area="strategy" />
        <div>
          <h1>Strategy lab</h1>
          <p class="muted">
            Classic entry/exit theories and the AI decision-maker, backtested on the same data, the same out-of-sample
            window and the same trading costs. Long-only; decided at the close, traded at the next close.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-strategies" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a class="btn" routerLink="/decisions">Today's AI decisions →</a>
      </div>
    </div>

    <app-status [res]="res" what="strategy results" />

    @if (res.hasValue()) {
      @if (run(); as r) {
        <app-verdict [tone]="runTone()">{{ r.summary }}</app-verdict>
        <p class="small muted">
          Backtest #{{ r.id }} run {{ r.runAt | utc }} · scored {{ r.oosStart }} to {{ r.dataCutoff }}
          · costs {{ r.config.costBpsPerSide }} bp per side on traded amount · {{ r.config.execution }}
        </p>

        @if (headline(); as h) {
          <div class="stats wide">
            <div class="stat tone-info">
              <div class="stat-label">Strategies tested</div>
              <div class="stat-value">{{ h.n }}<span class="unit">over {{ h.years | fixed: 1 }} years</span></div>
              <div class="stat-sub">{{ h.supported }} beat buy &amp; hold after the honesty checks</div>
            </div>
            <div class="stat tone-neutral">
              <div class="stat-label">Buy &amp; hold reference <app-help text="Equal-weight buy and hold of every tracked stock. Every strategy is judged against it: a rule only counts if it beats this after costs, with a confidence interval above zero and a Deflated Sharpe Ratio of at least 0.95." topic="excess" label="the reference" /></div>
              <div class="stat-value"><app-delta [value]="h.refCagr" kind="pct" [digits]="1" /><span class="unit">a year</span></div>
              <div class="stat-sub">Sharpe {{ h.refSharpe | fixed: 2 }} · max drawdown {{ h.refDd | pct: 1 }}</div>
            </div>
            <div class="stat" [class]="'stat ' + (h.bestSupported ? 'tone-good' : 'tone-warn')">
              <div class="stat-label">Highest Sharpe (active) <app-help text="Return per unit of risk, annualized, over cash. Above 1 is good for a single strategy; but a high Sharpe over a short window can still be luck, which is what the DSR checks." topic="sharpe" label="Sharpe ratio" /></div>
              <div class="stat-value">{{ h.bestSharpe | fixed: 2 }}</div>
              <div class="stat-sub"><a [routerLink]="['/strategies', h.bestKey]">{{ h.bestName }}</a> · {{ h.bestSupported ? 'beats buy & hold' : 'not supported once luck is accounted for' }}</div>
            </div>
            <div class="stat" [class]="'stat ' + (h.aiSupported ? 'tone-good' : 'tone-warn')">
              <div class="stat-label">The AI strategy</div>
              <div class="stat-value"><app-delta [value]="h.aiExcess" kind="pct" [digits]="1" /><span class="unit">vs buy &amp; hold, a year</span></div>
              <div class="stat-sub">CI {{ h.aiLo | signedPct: 1 }} to {{ h.aiHi | signedPct: 1 }} · DSR {{ h.aiDsr | fixed: 2 }}</div>
            </div>
          </div>
        }

        <div class="card">
          <div class="card-head">
            <h3>Comparison (out of sample, after costs)</h3>
            <span class="small muted">click a header to sort · tick up to {{ MAX }} to compare</span>
          </div>
          <div class="filters">
            <label class="field">
              Family
              <select [ngModel]="family()" (ngModelChange)="family.set($event)">
                <option value="">All families</option>
                @for (f of families(); track f) {
                  <option [value]="f">{{ familyLabel[f] ?? f }}</option>
                }
              </select>
            </label>
            <label class="field">
              Verdict
              <select [ngModel]="verdictFilter()" (ngModelChange)="verdictFilter.set($event)">
                <option value="">All</option>
                <option value="supported">Beats buy &amp; hold</option>
                <option value="not">Not supported</option>
                <option value="positive">Excess interval above zero</option>
              </select>
            </label>
            <label class="field">
              Search
              <input type="search" placeholder="Name" [ngModel]="q()" (ngModelChange)="q.set($event)" />
            </label>
            <span class="small muted">{{ visible().length }} of {{ results().length }}</span>
          </div>
          <div class="table-wrap">
            <table class="table compact lens">
              <thead>
                <tr>
                  <th class="pick"><span class="sr-only">Compare</span></th>
                  <th sortKey="name" [sort]="sort" defaultDir="asc">Strategy</th>
                  @for (c of cols; track c.key) {
                    <th class="num" [sortKey]="c.key" [sort]="sort">{{ c.label }}<app-help [text]="c.hint" [topic]="c.topic" [label]="c.label" /></th>
                  }
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                @for (s of visible(); track s.strategyKey) {
                  <tr [class.ref-row]="s.strategyKey === refKey" [class.row-current]="picked().includes(s.strategyKey)">
                    <td class="pick">
                      <input type="checkbox" [checked]="picked().includes(s.strategyKey)" (change)="toggle(s.strategyKey)"
                        [disabled]="!picked().includes(s.strategyKey) && picked().length >= MAX" [attr.aria-label]="'Compare ' + s.name" />
                    </td>
                    <td>
                      <a [routerLink]="['/strategies', s.strategyKey]">{{ s.name }}</a>
                      <div class="small muted">{{ familyLabel[s.family] ?? s.family }}</div>
                    </td>
                    @for (c of cols; track c.key) {
                      <td class="num" [style.font-weight]="best()[c.key] === s.strategyKey ? 650 : 400">
                        @switch (c.view) {
                          @case ('bar') {
                            <app-cell-bar [value]="c.get(s)" [max]="scale()[c.key]" [text]="cell(c, s)" [tone]="c.key === 'maxdd' ? 'bad' : c.key === 'exposure' ? 'neutral' : 'info'" />
                          }
                          @case ('signed-bar') {
                            <app-cell-bar [value]="c.get(s)" [max]="scale()[c.key]" [text]="cell(c, s)" tone="sign" />
                          }
                          @case ('ci') {
                            @if (c.get(s) !== null && c.get(s) !== undefined) {
                              <span class="ci-cell">
                                <app-range-bar [lo]="s.metrics.excessCiLow" [hi]="s.metrics.excessCiHigh" [point]="s.metrics.excessReturn" [span]="scale()['excess']" [label]="'Excess return ' + cell(c, s)" />
                                <span><app-delta [value]="c.get(s)" kind="pct" [digits]="1" /><div class="small muted">{{ ciText(s) }}</div></span>
                              </span>
                            } @else {
                              <span class="muted">—</span>
                            }
                          }
                          @case ('dsr') {
                            @if (c.get(s) !== null && c.get(s) !== undefined) {
                              {{ cell(c, s) }}
                              <app-meter [value]="c.get(s)" [target]="0.95" targetLabel="0.95 needed" [tone]="(c.get(s) ?? 0) >= 0.95 ? 'good' : 'neutral'" label="Deflated Sharpe Ratio" />
                            } @else {
                              <span class="muted">—</span>
                            }
                          }
                          @default {
                            {{ cell(c, s) }}
                          }
                        }
                        @if (best()[c.key] === s.strategyKey) {
                          <span class="badge-best" title="Best in this column">✓</span>
                        }
                      </td>
                    }
                    <td>
                      @if (s.family === 'BENCHMARK') {
                        <span class="badge tone-info">Reference</span>
                      } @else if (isSupported(s.verdict)) {
                        <span class="badge tone-good">✓ Beats buy &amp; hold</span>
                      } @else {
                        <span class="badge tone-neutral" title="{{ s.verdict }}">Not supported</span>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          @if (picked().length) {
            <div class="compare-bar" role="region" aria-label="Compare selection">
              <app-icon name="columns" [size]="18" />
              <span class="chips">
                @for (k of picked(); track k) {
                  <button type="button" class="chip" (click)="toggle(k)" [title]="'Remove'">{{ nameOf(k) }} ✕</button>
                }
              </span>
              <span class="small muted">{{ picked().length < 2 ? 'pick at least two' : picked().length + ' of ' + MAX }}</span>
              <a class="btn btn-primary" [class.disabled]="picked().length < 2" [routerLink]="picked().length >= 2 ? '/strategies/compare' : null" [queryParams]="{ keys: picked().join(',') }">Compare</a>
              <button type="button" class="btn btn-sm" (click)="picked.set([])">Clear</button>
            </div>
          }
          <p class="small muted" style="margin-top: 0.5rem">
            <span class="badge-best">✓</span> marks the best value in a column; bars are scaled to the column's largest
            value. "Excess" is the annualized return above equal-weight buy &amp; hold with a 95% block-bootstrap
            interval drawn against the zero line (green when wholly above it, red when wholly below). "DSR" is the
            Deflated Sharpe Ratio: the probability that the edge over buy &amp; hold is real after accounting for the
            {{ r.config.nTrials ?? r.config.nCandidates }} trials in the trial registry (every strategy and feature-set
            variant ever backtested on this history, {{ r.config.nCandidates }} of them in this run); the tick marks the
            0.95 needed. {{ r.config.verdictRule }}.
          </p>
        </div>

        <div class="card">
          <div class="chart-head">
            <h3>Growth of capital</h3>
            <label class="small">
              Compare with
              <select (change)="chosen.set($any($event.target).value)">
                @for (s of comparable(); track s.strategyKey) {
                  <option [value]="s.strategyKey" [selected]="s.strategyKey === compareKey()">{{ s.name }}</option>
                }
              </select>
            </label>
          </div>
          <app-line-chart
            [series]="equity()"
            label="Cumulative return of buy &amp; hold, the AI and a chosen strategy"
            caption="Cumulative net return after costs; every line starts on the same day, in cash."
            [yFormat]="equityFmt"
            [refY]="1"
            refLabel="start"
            [height]="300"
          />
        </div>

        <div class="card">
          <h3>Does confidence pay? <app-help text="The AI's own out-of-sample forecasts ranked by probability. Each row buys only the top slice at the next close and holds for the AI's horizon; costs are charged on the stock alone (buy and sell). If the surest 10% do not earn more than everything, waiting for a higher probability cannot help." topic="abstention" label="abstention" /></h3>
            <p class="small muted">
              The AI's forecasts, long only: what acting on the most confident share would have earned per position over
              {{ r.config.ai['horizon'] }} trading days, after costs. The decision layer rows in the table above test the
              same idea as strategies: <a routerLink="/strategies/AI_CONF">confident entries only</a> raises the bar to act,
              <a routerLink="/strategies/AI_SIZED">sized by volatility</a>, the book recorded on the AI decisions page, keeps
              the trades and changes the sizes, and the <a routerLink="/strategies/AI_RANK_VOL">ranking</a> rows price what
              letting a stronger candidate replace the weakest holding would cost.
            </p>
          <app-coverage-table [rows]="r.config.aiCoverage ?? []" positionLabel="a stock" />
        </div>

        <div class="grid-2">
          @if (decisionLayer(); as dl) {
            <div class="card">
              <h3>The decision layer <app-help text="Ways to act on the same probabilities. The standard rule enters at p ≥ 0.55 with equal slices; abstention waits for p ≥ 0.60; sizing keeps the standard entries but gives each position 0.04 / its annualized volatility, capped at 20%; the ranking rule replaces the weakest holding when an outsider beats it by 0.08, shown with volatility sizing alone and with a conviction tilt of (p − 0.5) / 0.05. The last row is the standard rule on a model that also sees the policy-event features, which the book dropped after this row underperformed; sized by volatility is the recorded book." topic="decision-layer" label="decision layer" /></h3>
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr><th>Rule</th><th class="num">Sharpe</th><th class="num">Max DD</th><th class="num">Invested</th><th class="num">Trades</th><th class="num">Excess</th></tr>
                  </thead>
                  <tbody>
                    @for (x of dl; track x.key) {
                      <tr>
                        <td><a [routerLink]="['/strategies', x.key]">{{ x.label }}</a><div class="small muted">{{ x.note }}</div></td>
                        <td class="num" [style.font-weight]="x.bestSharpe ? 650 : 400">{{ x.sharpe | fixed: 2 }}@if (x.bestSharpe) { <span class="badge-best" title="Best of the decision-layer rules">✓</span> }</td>
                        <td class="num" [style.font-weight]="x.bestDd ? 650 : 400">{{ x.maxDd | pct: 1 }}@if (x.bestDd) { <span class="badge-best" title="Best of the decision-layer rules">✓</span> }</td>
                        <td class="num">{{ x.exposure | pct: 0 }}</td>
                        <td class="num">{{ x.trades | num }}</td>
                        <td class="num"><app-delta [value]="x.excess" kind="pct" [digits]="1" /></td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              <p class="small muted" style="margin-top: 0.5rem">
                Sizing and abstention cannot create an edge the probabilities lack; they change how much of one is kept
                and how much pain comes with it. Differences without a "Beats buy &amp; hold" badge are still noise.
              </p>
            </div>
          }
          <div class="card">
            <h3>How to read this</h3>
            <ul class="small notes">
              <li>All strategies trade the same {{ results().length }} portfolios on identical data; the AI was trained
                walk-forward and never saw the period it is scored on.</li>
              <li>Timing rules hold each stock in its own 1/N slice and sit in cash otherwise, so their "Exposure" is below
                100%. Lower exposure usually means lower return <em>and</em> lower drawdown.</li>
              <li>With about {{ years() | fixed: 1 }} years and a small universe, a few lucky trades can dominate. Treat
                differences without a "Beats buy &amp; hold" badge as noise.</li>
              <li>This is research, not advice: a backtest is not evidence of live profitability.</li>
              <li><a routerLink="/guide" fragment="metrics">Every metric on this page, explained in the guide →</a></li>
            </ul>
          </div>
        </div>

        <div class="card">
          <h3>Cost sensitivity (CAGR) <app-help text="The same backtest re-run with different trading costs per side. Strategies that trade a lot fade fastest as costs rise; a rule that only works at zero cost is not a rule you can trade." topic="costs" label="cost sensitivity" /></h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    @for (b of r.config.costSensitivityBps; track b) {
                      <th class="num">{{ b }} bp</th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (s of results(); track s.strategyKey) {
                    <tr>
                      <td>{{ s.name }}</td>
                      @for (b of r.config.costSensitivityBps; track b) {
                        <td class="num heat" [class]="'num heat tone-' + costTone(s, b)" [style.--h]="costHeat(s, b)">
                          {{ s.costSensitivity[b + '']?.cagr ?? null | signedPct: 1 }}
                        </td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem">Colour depth follows the size of the annual return; red is a loss. High-turnover rules lose the most as costs rise.</p>
        </div>
      } @else {
        <div class="empty-box">
          No strategy backtest yet. Run it from <a routerLink="/admin">Data &amp; pipeline</a> → Run strategy backtest
          (it also runs at the end of every pipeline run).
        </div>
      }
    }
  `,
  styles: `
    .chart-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
    .chart-head select { margin-left: 0.4rem; }
    .ref-row td { background: var(--surface-2); }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
    .lens td:nth-child(2) { min-width: 168px; }
    .lens td.num { white-space: nowrap; }
    .lens .ci-cell { flex-direction: column; align-items: flex-end; gap: 0.1rem; }
    .lens .ci-cell .small { text-align: right; }
    .filters { margin-bottom: 0.75rem; align-items: center; }
    .chip { cursor: pointer; font: inherit; font-size: 0.78rem; }
    .btn.disabled { opacity: 0.5; pointer-events: none; }
  `,
})
export class StrategiesPage {
  protected readonly res = httpResource<StrategiesResponse>(() => apiUrl.strategies());
  protected readonly familyLabel = FAMILY_LABEL;
  protected readonly refKey = REFERENCE_KEY;
  protected readonly equityFmt = equityFormat;
  protected readonly isSupported = supported;

  protected readonly MAX = 3;
  protected readonly picked = signal<string[]>([]);
  protected readonly family = signal('');
  protected readonly verdictFilter = signal<'' | 'supported' | 'not' | 'positive'>('');
  protected readonly q = signal('');
  protected readonly sort = createSort('civalpha.sort.strategies', { key: 'sharpe', dir: 'desc' });

  protected readonly run = computed(() => valueOf(this.res)?.run ?? null);
  protected readonly results = computed(() => valueOf(this.res)?.results ?? []);
  protected readonly families = computed(() => [...new Set(this.results().map((s) => s.family))]);
  protected readonly visible = computed(() => {
    const fam = this.family();
    const vf = this.verdictFilter();
    const q = this.q().trim().toLowerCase();
    const list = this.results().filter((s) => {
      if (fam && s.family !== fam) return false;
      if (q && !s.name.toLowerCase().includes(q)) return false;
      if (vf === 'supported') return supported(s.verdict);
      if (vf === 'not') return s.family !== 'BENCHMARK' && !supported(s.verdict);
      if (vf === 'positive') return (s.metrics.excessCiLow ?? -1) > 0;
      return true;
    });
    const getters: Record<string, (r: StrategyResult) => number | string | null | undefined> = { name: (r) => r.name };
    for (const c of this.cols) getters[c.key] = c.get;
    return this.sort.order(list, getters);
  });

  protected toggle(key: string): void {
    this.picked.update((p) => (p.includes(key) ? p.filter((k) => k !== key) : p.length < this.MAX ? [...p, key] : p));
  }

  protected nameOf(key: string): string {
    return this.results().find((s) => s.strategyKey === key)?.name ?? key;
  }
  protected readonly years = computed(() => this.results()[0]?.metrics.years ?? 0);
  protected readonly runTone = computed(() => {
    const anySupported = this.results().some((s) => s.family !== 'BENCHMARK' && supported(s.verdict));
    return anySupported ? 'good' : verdictTone(this.run()?.summary) === 'good' ? 'good' : 'warn';
  });

  protected readonly headline = computed(() => {
    const list = this.results();
    if (!list.length) return null;
    const ref = list.find((s) => s.strategyKey === REFERENCE_KEY);
    const active = list.filter((s) => s.family !== 'BENCHMARK');
    const best = [...active].sort((a, b) => (b.metrics.sharpe ?? -Infinity) - (a.metrics.sharpe ?? -Infinity))[0];
    const ai = list.find((s) => s.strategyKey === AI_KEY);
    return {
      n: list.length,
      years: list[0].metrics.years,
      supported: active.filter((s) => supported(s.verdict)).length,
      refCagr: ref?.metrics.cagr ?? null,
      refSharpe: ref?.metrics.sharpe ?? null,
      refDd: ref?.metrics.maxDrawdown ?? null,
      bestKey: best?.strategyKey ?? '',
      bestName: best?.name ?? '—',
      bestSharpe: best?.metrics.sharpe ?? null,
      bestSupported: best ? supported(best.verdict) : false,
      aiExcess: ai?.metrics.excessReturn ?? null,
      aiLo: ai?.metrics.excessCiLow ?? null,
      aiHi: ai?.metrics.excessCiHigh ?? null,
      aiDsr: ai?.metrics.deflatedSharpe ?? null,
      aiSupported: ai ? supported(ai.verdict) : false,
    };
  });

  /** The AI rules that share one set of probabilities and differ only in how they act on them. */
  protected readonly decisionLayer = computed(() => {
    const byKey = new Map(this.results().map((s) => [s.strategyKey, s]));
    const spec = [
      { key: AI_KEY, label: 'Standard rule', note: 'enter at p ≥ 0.55, equal slices' },
      { key: 'AI_CONF', label: 'Confident entries only', note: 'abstain unless p ≥ 0.60' },
      { key: 'AI_SIZED', label: 'Sized by volatility', note: 'the recorded book: same trades, 0.04 / volatility each' },
      { key: 'AI_RANK', label: 'Book follows the ranking', note: 'replace the weakest holding when beaten by 0.08' },
      { key: 'AI_RANK_VOL', label: 'Ranking + volatility sizing', note: 'the swap rule alone: no conviction tilt' },
      { key: 'AI_RANK_SIZED', label: 'Ranking + conviction sizing', note: 'volatility size × conviction tilt' },
      { key: 'AI_WITH_EVENTS', label: 'With policy-event features', note: 'standard rule, model also sees tariff/rate shocks' },
    ];
    const rows = spec
      .map((x) => {
        const r = byKey.get(x.key);
        const m = r?.metrics;
        return r && m ? { ...x, sharpe: m.sharpe, maxDd: m.maxDrawdown, exposure: m.exposure, trades: m.trades, excess: m.excessReturn, bestSharpe: false, bestDd: false } : null;
      })
      .filter((x): x is NonNullable<typeof x> => x !== null);
    if (rows.length < 2) return null;
    const bestS = rows.reduce((a, b) => ((b.sharpe ?? -Infinity) > (a.sharpe ?? -Infinity) ? b : a));
    const bestD = rows.reduce((a, b) => ((b.maxDd ?? -Infinity) > (a.maxDd ?? -Infinity) ? b : a));
    bestS.bestSharpe = true;
    bestD.bestDd = true;
    return rows;
  });

  protected readonly cols: Col[] = [
    { key: 'cagr', label: 'CAGR', hint: 'compound annual growth, after costs', topic: 'cagr', get: (r) => r.metrics.cagr, fmt: (v) => fmtSignedPct(v, 1), higherIsBetter: true, view: 'signed-bar' },
    { key: 'sharpe', label: 'Sharpe', hint: 'return per unit of risk, annualized, over cash', topic: 'sharpe', get: (r) => r.metrics.sharpe, fmt: (v) => v.toFixed(2), higherIsBetter: true, view: 'signed-bar' },
    { key: 'maxdd', label: 'Max DD', hint: 'largest peak-to-trough fall; closer to zero is better', topic: 'max-drawdown', get: (r) => r.metrics.maxDrawdown, fmt: (v) => fmtPct(v, 1), higherIsBetter: true, view: 'bar' },
    { key: 'exposure', label: 'Exposure', hint: 'average share of capital invested', topic: 'exposure', get: (r) => r.metrics.exposure, fmt: (v) => fmtPct(v, 0), higherIsBetter: null, view: 'bar' },
    { key: 'trades', label: 'Trades', hint: 'round trips in the window', topic: 'costs', get: (r) => r.metrics.trades, fmt: (v) => v.toLocaleString('en-US'), higherIsBetter: null, view: 'plain' },
    { key: 'excess', label: 'Excess', hint: 'annualized return above equal-weight buy & hold, with a 95% confidence interval', topic: 'excess', get: (r) => r.metrics.excessReturn, fmt: (v) => fmtSignedPct(v, 1), higherIsBetter: true, view: 'ci' },
    { key: 'dsr', label: 'DSR', hint: 'Deflated Sharpe Ratio: probability the edge is real after trying many strategies (≥ 0.95 needed)', topic: 'dsr', get: (r) => r.metrics.deflatedSharpe, fmt: (v) => v.toFixed(2), higherIsBetter: true, view: 'dsr' },
  ];

  protected cell(c: Col, s: StrategyResult): string {
    const v = c.get(s);
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return c.fmt(v);
  }

  protected ciText(s: StrategyResult): string {
    const lo = s.metrics.excessCiLow;
    const hi = s.metrics.excessCiHigh;
    return lo != null && hi != null ? `${fmtSignedPct(lo, 1)} to ${fmtSignedPct(hi, 1)}` : '';
  }

  /** Largest |value| per column, for bar scaling. */
  protected readonly scale = computed<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const c of this.cols) {
      let m = 0;
      for (const s of this.results()) {
        const v = c.get(s);
        if (typeof v === 'number' && Number.isFinite(v)) m = Math.max(m, Math.abs(v));
        if (c.key === 'excess') {
          for (const e of [s.metrics.excessCiLow, s.metrics.excessCiHigh]) {
            if (typeof e === 'number' && Number.isFinite(e)) m = Math.max(m, Math.abs(e));
          }
        }
      }
      out[c.key] = m || 1;
    }
    return out;
  });

  private readonly maxCostCagr = computed(() => {
    let m = 0;
    for (const s of this.results()) {
      for (const v of Object.values(s.costSensitivity)) {
        if (typeof v.cagr === 'number' && Number.isFinite(v.cagr)) m = Math.max(m, Math.abs(v.cagr));
      }
    }
    return m || 1;
  });

  protected costTone(s: StrategyResult, bp: number): string {
    const v = s.costSensitivity[bp + '']?.cagr;
    if (typeof v !== 'number' || v === 0) return 'neutral';
    return v > 0 ? 'good' : 'bad';
  }

  protected costHeat(s: StrategyResult, bp: number): number {
    const v = s.costSensitivity[bp + '']?.cagr;
    return typeof v === 'number' ? Math.min(1, Math.abs(v) / this.maxCostCagr()) : 0;
  }

  /** strategy key holding the best value per column (benchmarks excluded where the column compares to them) */
  protected readonly best = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const c of this.cols) {
      if (c.higherIsBetter === null) continue;
      let bestKey = '';
      let bestV = -Infinity;
      for (const s of this.results()) {
        const v = c.get(s);
        if (v === null || v === undefined || !Number.isFinite(v)) continue;
        const score = c.higherIsBetter ? v : -v;
        if (score > bestV) {
          bestV = score;
          bestKey = s.strategyKey;
        }
      }
      if (bestKey) out[c.key] = bestKey;
    }
    return out;
  });

  /** Third line of the chart: any strategy other than the two fixed ones (colors follow roles, not rank). */
  protected readonly comparable = computed(() => this.results().filter((s) => s.strategyKey !== AI_KEY && s.strategyKey !== REFERENCE_KEY));
  protected readonly chosen = signal<string | null>(null);
  /** defaults to the best-ranked active rule (results arrive sorted by Sharpe) */
  protected readonly compareKey = computed(() => {
    const c = this.chosen();
    const list = this.comparable();
    if (c && list.some((s) => s.strategyKey === c)) return c;
    const active = list.filter((s) => s.family !== 'BENCHMARK');
    return (active[0] ?? list[0])?.strategyKey ?? '';
  });

  protected readonly equity = computed<LineSeries[]>(() => {
    const byKey = new Map(this.results().map((s) => [s.strategyKey, s]));
    const out: LineSeries[] = [];
    const ref = byKey.get(REFERENCE_KEY);
    if (ref) out.push(equitySeries(ref.strategyKey, ref.name, 'var(--series-1)', ref));
    const ai = byKey.get(AI_KEY);
    if (ai) out.push(equitySeries(ai.strategyKey, ai.name, 'var(--series-2)', ai));
    const other = byKey.get(this.compareKey());
    if (other) out.push(equitySeries(other.strategyKey, other.name, 'var(--series-3)', other));
    return out;
  });
}
