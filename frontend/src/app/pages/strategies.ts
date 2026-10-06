import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DotWhisker, WhiskerItem } from '../charts/dot-whisker';
import { LineChart, LineSeries } from '../charts/line-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtFixed, fmtPct, fmtSignedPct } from '../core/format';
import { CoverageLevel, StrategiesResponse, StrategyResult } from '../core/models';
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

interface Finding {
  q: string;
  answer: string;
  tone: 'good' | 'warn' | 'bad' | 'neutral';
  lines: string[];
  link?: { label: string; to: string[]; fragment?: string };
}

const fmtSigned4 = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(4);

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
  imports: [RouterLink, FormsModule, LineChart, DotWhisker, Icon, SortTh, CoverageTable, ...UI, ...VIZ, ...FORMAT_PIPES],
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
        <app-verdict [tone]="runTone()">{{ shortVerdict() }}</app-verdict>
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
              <div class="stat-label">The recorded AI book <app-help text="The AI rule whose decisions are stored every day on the AI decisions page. Its return above equal-weight buy and hold, per year, with the 95% interval and the Deflated Sharpe Ratio (0.95 needed)." topic="excess" label="recorded book" /></div>
              <div class="stat-value"><app-delta [value]="h.aiExcess" kind="pct" [digits]="1" /><span class="unit">vs buy &amp; hold, a year</span></div>
              <div class="stat-sub"><a [routerLink]="['/strategies', h.aiKey]">{{ h.aiKey }}</a> · CI {{ h.aiLo | signedPct: 1 }} to {{ h.aiHi | signedPct: 1 }} · DSR {{ h.aiDsr | fixed: 2 }}</div>
            </div>
          </div>
        }

        @if (findings().length) {
          <div class="card">
            <h3>What the lab found <app-help text="One card per question the lab answers, worked out from the rows of this backtest. The badge is the short answer; the lines under it are the numbers behind it. Green means the evidence supports the idea after the honesty checks, amber that it does not or is within noise, red that it points the wrong way." topic="page-strategies" label="findings" /></h3>
            <div class="findings">
              @for (f of findings(); track f.q) {
                <article class="finding" [class]="'finding tone-' + f.tone">
                  <header>
                    <span class="badge" [class]="'badge tone-' + f.tone">{{ f.answer }}</span>
                    <h4>{{ f.q }}</h4>
                  </header>
                  <ul>
                    @for (line of f.lines; track $index) {
                      <li>{{ line }}</li>
                    }
                  </ul>
                  @if (f.link) {
                    <a class="small" [routerLink]="f.link.to" [fragment]="f.link.fragment">{{ f.link.label }} →</a>
                  }
                </article>
              }
            </div>
            <details class="full-summary">
              <summary class="small">Full text summary stored with backtest #{{ r.id }}</summary>
              <p class="small">{{ r.summary }}</p>
            </details>
          </div>
        }

        <div class="card" id="comparison">
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

        <div class="card" id="confidence">
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
          @if (r.config.aiCoverageCalibrated?.length) {
            <h4 class="sub-head">The same forecasts on calibrated probabilities <app-help text="Each fold's probabilities mapped through an isotonic curve fitted on earlier folds only, so a stated 60% is what 60% has meant so far (ADR-0002). If the surest calls only pay on raw probabilities, the edge was overconfidence choosing its own winners." topic="calibration" label="calibrated coverage" /></h4>
            <app-coverage-table [rows]="r.config.aiCoverageCalibrated!" positionLabel="a stock" />
          }
        </div>

        @if (decisionLayer(); as dl) {
          <div class="card" id="decision-layer">
            <h3>The decision layer <app-help text="Ways to act on the same probabilities. The standard rule enters at p ≥ 0.55 with equal slices; abstention waits for p ≥ 0.60; sizing keeps the standard entries but gives each position 0.04 / its annualized volatility, capped at 20%; the ranking rule replaces the weakest holding when an outsider beats it by 0.08, shown with volatility sizing alone and with a conviction tilt of (p − 0.5) / 0.05. The last row is the standard rule on a model that also sees the policy-event features (dropped on 2026-10-06 on a 10-day lab Sharpe that reversed at 21 days: a Sharpe is not a skill test); sized by volatility is the recorded book." topic="decision-layer" label="decision layer" /></h3>
            <p class="small muted">
              Same probabilities, different ways to act on them. Each dot is one rule; the dashed line is equal-weight buy &amp;
              hold. Only the excess return carries an interval: a rule has beaten the reference only when its whole whisker
              sits right of the line.
            </p>
            <div class="grid-3">
              <div>
                <h4>Excess return vs buy &amp; hold, a year <app-help text="Annualized return above equal-weight buy & hold with a 95% block-bootstrap interval. Green when the whole interval is above zero, red when wholly below, grey when it crosses." topic="excess" label="excess return" /></h4>
                <app-dot-whisker [items]="layerExcessItems()" label="Excess return of each decision-layer rule over buy and hold, with 95% interval" [refX]="0" refLabel="buy &amp; hold" betterIs="higher" [format]="signedPct1" />
              </div>
              <div>
                <h4>Sharpe ratio <app-help text="Return per unit of risk, annualized, over cash. No interval is computed for it; read it as a description of the window, not as evidence." topic="sharpe" label="Sharpe ratio" /></h4>
                <app-dot-whisker [items]="layerSharpeItems()" label="Sharpe ratio of each decision-layer rule against buy and hold" [refX]="dl.ref.sharpe" [refLabel]="'buy &amp; hold ' + (dl.ref.sharpe | fixed: 2)" [betterIs]="null" [format]="fixed2" />
              </div>
              <div>
                <h4>Max drawdown <app-help text="Largest peak-to-trough fall of the equity curve; closer to zero is better. Sizing changes this most, because it changes how much of the capital is at risk." topic="max-drawdown" label="max drawdown" /></h4>
                <app-dot-whisker [items]="layerDdItems()" label="Maximum drawdown of each decision-layer rule against buy and hold" [refX]="dl.ref.maxDd" [refLabel]="'buy &amp; hold ' + (dl.ref.maxDd | pct: 1)" [betterIs]="null" [format]="pct1" />
              </div>
            </div>
            <details class="chart-table">
              <summary>Data table ({{ dl.rows.length }} rules)</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr><th>Rule</th><th class="num">Sharpe</th><th class="num">Max DD</th><th class="num">Invested</th><th class="num">Trades</th><th class="num">Excess (95% CI)</th></tr>
                  </thead>
                  <tbody>
                    @for (x of dl.rows; track x.key) {
                      <tr>
                        <td><a [routerLink]="['/strategies', x.key]">{{ x.label }}</a><div class="small muted">{{ x.note }}</div></td>
                        <td class="num" [style.font-weight]="x.bestSharpe ? 650 : 400">{{ x.sharpe | fixed: 2 }}@if (x.bestSharpe) { <span class="badge-best" title="Best of the decision-layer rules">✓</span> }</td>
                        <td class="num" [style.font-weight]="x.bestDd ? 650 : 400">{{ x.maxDd | pct: 1 }}@if (x.bestDd) { <span class="badge-best" title="Best of the decision-layer rules">✓</span> }</td>
                        <td class="num">{{ x.exposure | pct: 0 }}</td>
                        <td class="num">{{ x.trades | num }}</td>
                        <td class="num"><app-delta [value]="x.excess" kind="pct" [digits]="1" /><div class="small muted">{{ x.excessLo | signedPct: 1 }} to {{ x.excessHi | signedPct: 1 }}</div></td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
            <p class="small muted" style="margin-top: 0.5rem">
              Sizing and abstention cannot create an edge the probabilities lack; they change how much of one is kept
              and how much pain comes with it. Differences without a "Beats buy &amp; hold" badge are still noise.
            </p>
          </div>
        }

        <div class="grid-2">
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
    .grid-3 h4 { margin: 0.25rem 0 0.35rem; font-size: 0.9rem; }
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
    .findings { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 0.75rem; }
    .finding { border: 1px solid var(--border); border-left: 4px solid var(--border); border-radius: 10px; padding: 0.75rem 0.9rem;
               background: var(--surface); display: flex; flex-direction: column; gap: 0.4rem; }
    .finding.tone-good { border-left-color: var(--good, #1baf7a); }
    .finding.tone-warn { border-left-color: var(--warn, #d9a200); }
    .finding.tone-bad { border-left-color: var(--bad, #d0453a); }
    .finding header { display: flex; flex-direction: column; align-items: flex-start; gap: 0.35rem; }
    .finding h4 { margin: 0; font-size: 0.95rem; line-height: 1.3; }
    .finding ul { margin: 0; padding-left: 1.05rem; font-size: 0.86rem; }
    .finding li + li { margin-top: 0.2rem; }
    .finding a { margin-top: auto; }
    .full-summary { margin-top: 0.9rem; }
    .full-summary p { margin: 0.5rem 0 0; max-width: 110ch; }
    .sub-head { margin: 1rem 0 0.4rem; font-size: 0.92rem; }
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
    const aiKey = this.run()?.config.bookKey ?? 'AI_SIZED';
    const ai = list.find((s) => s.strategyKey === aiKey) ?? list.find((s) => s.strategyKey === AI_KEY);
    return {
      aiKey: ai?.strategyKey ?? aiKey,
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

  /** One sentence: does anything beat buy & hold, after how many trials, and what came closest. */
  protected readonly shortVerdict = computed(() => {
    const r = this.run();
    const h = this.headline();
    if (!r || !h) return '';
    const trials = r.config.nTrials ?? r.config.nCandidates;
    const head = h.supported
      ? `${h.supported} of ${r.config.nCandidates} strategies beat equal-weight buy & hold after costs and after correcting for ${trials} trials. This is a backtest, not live evidence.`
      : `No strategy beat equal-weight buy & hold once the test accounts for the ${trials} strategy and feature-set variants tried on this history; the differences are consistent with luck.`;
    return `${head} Highest Sharpe: ${h.bestName} (${fmtFixed(h.bestSharpe, 2)}) against ${fmtFixed(h.refSharpe, 2)} for buy & hold.`;
  });

  /** The questions the lab answers, each with a short answer, a tone and the numbers behind it. Built from this run's rows. */
  protected readonly findings = computed<Finding[]>(() => {
    const r = this.run();
    const list = this.results();
    if (!r || !list.length) return [];
    const by = new Map(list.map((s) => [s.strategyKey, s.metrics]));
    const sh = (k: string) => fmtFixed(by.get(k)?.sharpe, 2);
    const dd = (k: string) => fmtPct(by.get(k)?.maxDrawdown, 1);
    const inv = (k: string) => fmtPct(by.get(k)?.exposure, 0);
    const out: Finding[] = [];
    const h = this.headline()!;
    const trials = r.config.nTrials ?? r.config.nCandidates;

    out.push({
      q: 'Does any strategy beat buy & hold?',
      answer: h.supported ? `Yes, ${h.supported}` : 'No',
      tone: h.supported ? 'good' : 'warn',
      lines: [
        `${h.supported} of ${r.config.nCandidates} active strategies pass all three checks: 3+ years, excess CI above zero, Deflated Sharpe ≥ 0.95.`,
        `The Deflated Sharpe counts ${trials} trials: every strategy and input-list variant ever backtested here.`,
        `Best Sharpe ${fmtFixed(h.bestSharpe, 2)} (${h.bestName}) vs ${fmtFixed(h.refSharpe, 2)} for buy & hold.`,
      ],
      link: { label: 'Comparison table', to: ['/strategies'], fragment: 'comparison' },
    });

    const book = r.config.bookKey ?? 'AI_SIZED';
    if (by.has(book) && by.has('EW_SIZED')) {
      const gap = (by.get(book)!.sharpe ?? 0) - (by.get('EW_SIZED')!.sharpe ?? 0);
      const mom = by.get('MOM_12_1_SIZED')?.sharpe ?? null;
      const momBetter = mom !== null && mom > (by.get(book)!.sharpe ?? 0);
      const nil = Math.abs(gap) < 0.1 || momBetter;
      out.push({
        q: 'Does the AI forecast add anything beyond position sizing?',
        answer: nil ? 'No' : `+${fmtFixed(gap, 2)} Sharpe, untested`,
        tone: nil ? 'warn' : 'neutral',
        lines: [
          `Recorded book (${book}): Sharpe ${sh(book)}, max drawdown ${dd(book)}.`,
          `Whole universe, same volatility sizing, no forecast (EW_SIZED): ${sh('EW_SIZED')}, ${dd('EW_SIZED')}.`,
          ...(mom !== null ? [`12-1 momentum, same sizing (MOM_12_1_SIZED): ${sh('MOM_12_1_SIZED')}, ${dd('MOM_12_1_SIZED')}.`] : []),
          nil ? 'The sizing explains the book; the forecast does not add to it on this window.' : 'A gap this size is within what the trial count allows by luck.',
        ],
        link: { label: book, to: ['/strategies', book] },
      });
    }

    if (by.has(AI_KEY) && by.has('AI_GBM_CAL')) {
      const raw = by.get(AI_KEY)!, cal = by.get('AI_GBM_CAL')!;
      out.push({
        q: 'Do the AI\'s thresholds mean anything once its probabilities are honest?',
        answer: 'Overconfident',
        tone: 'warn',
        lines: [
          `Raw probabilities clear the 0.55 entry often: invested ${inv(AI_KEY)} of the time, ${raw.trades} trades, Sharpe ${sh(AI_KEY)}.`,
          `Calibrated on earlier folds: invested ${inv('AI_GBM_CAL')}, ${cal.trades} trades, Sharpe ${sh('AI_GBM_CAL')}.`,
          'The thresholds were set against the model\'s overconfidence, not against information (ADR-0002).',
        ],
        link: { label: 'Calibrated rule', to: ['/strategies', 'AI_GBM_CAL'] },
      });
    }

    const cov = (c?: CoverageLevel[]) => c?.find((x) => Math.abs(x.coverage - 0.1) < 1e-9) ?? null;
    const rawTop = cov(r.config.aiCoverage), calTop = cov(r.config.aiCoverageCalibrated);
    if (rawTop) {
      const ci = (x: CoverageLevel) => `${fmtSignedPct(x.meanNet, 2)} per position (CI ${fmtSignedPct(x.ciLow, 2)} to ${fmtSignedPct(x.ciHigh, 2)})`;
      const rawPos = rawTop.ciLow > 0, calPos = calTop ? calTop.ciLow > 0 : null;
      out.push({
        q: 'Do the AI\'s most confident calls pay?',
        answer: calTop ? (calPos ? 'Yes, after costs' : rawPos ? 'Only before calibration' : 'Within noise') : rawPos ? 'Yes, raw only' : 'Within noise',
        tone: calTop ? (calPos ? 'good' : 'warn') : rawPos ? 'neutral' : 'warn',
        lines: [
          `Top 10% on raw probabilities: ${ci(rawTop)}, ${fmtPct(rawTop.accuracy, 0)} right.`,
          ...(calTop ? [`Top 10% on calibrated probabilities: ${ci(calTop)}, ${fmtPct(calTop.accuracy, 0)} right.`] : []),
          'One window, overlapping holding periods, not pre-registered: the live test is where this gets checked.',
        ],
        link: { label: 'Does confidence pay?', to: ['/strategies'], fragment: 'confidence' },
      });
    }

    const layer = this.decisionLayer();
    if (layer) {
      const better = layer.rows.filter((x) => (x.excessLo ?? -1) > 0);
      out.push({
        q: 'Does any way of acting on the forecast help?',
        answer: better.length ? `${better.length} rule${better.length > 1 ? 's' : ''} above zero` : 'Within noise',
        tone: better.length ? 'neutral' : 'warn',
        lines: [
          ...layer.rows.slice(0, 6).map((x) => `${x.short}: ${fmtFixed(x.sharpe, 2)} Sharpe · ${fmtPct(x.maxDd, 1)} drawdown`),
          better.length ? `Excess CI above zero: ${better.map((x) => x.short).join(', ')}.` : 'No rule\'s excess-return interval sits above zero.',
        ],
        link: { label: 'The decision layer', to: ['/strategies'], fragment: 'decision-layer' },
      });
    }

    const div = r.config.dividendFeatureTest;
    const withEv = by.get('AI_WITH_EVENTS');
    if ((div && div.ciLow !== undefined) || withEv) {
      const lines: string[] = [];
      if (div && div.ciLow !== undefined && div.ciHigh !== undefined && div.withoutDividends && div.withDividends) {
        lines.push(`Dividend signals: AUC ${fmtFixed(div.withoutDividends.auc, 3)} without, ${fmtFixed(div.withDividends.auc, 3)} with; Brier difference CI ${fmtSigned4(div.ciLow)} to ${fmtSigned4(div.ciHigh)}.`);
      }
      if (withEv && by.has(AI_KEY)) {
        lines.push(`Policy-event features: lab Sharpe ${sh('AI_WITH_EVENTS')} with, ${sh(AI_KEY)} without. A Sharpe, not a skill test: the walk-forward on Model accuracy decides.`);
      }
      const divHelps = div && div.ciHigh !== undefined && div.ciHigh < 0;
      out.push({
        q: 'Do extra inputs help the AI (dividends, policy events)?',
        answer: divHelps ? 'Dividends help' : 'No evidence',
        tone: divHelps ? 'good' : 'warn',
        lines,
        link: { label: 'Feature fragility', to: ['/ablation'] },
      });
    }
    return out;
  });

  /** The AI rules that share one set of probabilities and differ only in how they act on them. */
  protected readonly decisionLayer = computed(() => {
    const byKey = new Map(this.results().map((s) => [s.strategyKey, s]));
    const spec = [
      { key: AI_KEY, label: 'Standard rule', short: 'Standard rule', note: 'enter at p ≥ 0.55, equal slices' },
      { key: 'AI_CONF', label: 'Confident entries only', short: 'Confident only', note: 'abstain unless p ≥ 0.60' },
      { key: 'AI_SIZED', label: 'Sized by volatility', short: 'Sized by volatility', note: 'the recorded book: same trades, 0.04 / volatility each' },
      { key: 'AI_RANK', label: 'Book follows the ranking', short: 'Ranking book', note: 'replace the weakest holding when beaten by 0.08' },
      { key: 'AI_RANK_VOL', label: 'Ranking + volatility sizing', short: 'Ranking + vol size', note: 'the swap rule alone: no conviction tilt' },
      { key: 'AI_RANK_SIZED', label: 'Ranking + conviction sizing', short: 'Ranking + tilt', note: 'volatility size × conviction tilt' },
      { key: 'AI_WITH_EVENTS', label: 'With policy-event features', short: 'With policy events', note: 'standard rule, model also sees tariff/rate shocks' },
    ];
    const rows = spec
      .map((x) => {
        const r = byKey.get(x.key);
        const m = r?.metrics;
        return r && m
          ? { ...x, sharpe: m.sharpe, maxDd: m.maxDrawdown, exposure: m.exposure, trades: m.trades, excess: m.excessReturn, excessLo: m.excessCiLow, excessHi: m.excessCiHigh, bestSharpe: false, bestDd: false }
          : null;
      })
      .filter((x): x is NonNullable<typeof x> => x !== null);
    if (rows.length < 2) return null;
    const bestS = rows.reduce((a, b) => ((b.sharpe ?? -Infinity) > (a.sharpe ?? -Infinity) ? b : a));
    const bestD = rows.reduce((a, b) => ((b.maxDd ?? -Infinity) > (a.maxDd ?? -Infinity) ? b : a));
    bestS.bestSharpe = true;
    bestD.bestDd = true;
    const ref = byKey.get(REFERENCE_KEY)?.metrics;
    return { rows, ref: { sharpe: ref?.sharpe ?? null, maxDd: ref?.maxDrawdown ?? null } };
  });
  private layerDetails(x: { label: string; note: string; sharpe: number | null; maxDd: number | null; exposure: number | null; trades: number }): string[] {
    return [x.label, x.note, `Sharpe ${fmtFixed(x.sharpe, 2)} · max drawdown ${fmtPct(x.maxDd, 1)} · invested ${fmtPct(x.exposure, 0)} · ${x.trades} trades`];
  }
  protected readonly layerExcessItems = computed<WhiskerItem[]>(() =>
    (this.decisionLayer()?.rows ?? [])
      .filter((x) => x.excess !== null && x.excess !== undefined)
      .map((x) => ({ row: x.key, label: x.short, value: x.excess!, lo: x.excessLo, hi: x.excessHi, details: this.layerDetails(x) })),
  );
  protected readonly layerSharpeItems = computed<WhiskerItem[]>(() =>
    (this.decisionLayer()?.rows ?? []).filter((x) => x.sharpe !== null).map((x) => ({ row: x.key, label: x.short, value: x.sharpe!, details: this.layerDetails(x) })),
  );
  protected readonly layerDdItems = computed<WhiskerItem[]>(() =>
    (this.decisionLayer()?.rows ?? []).filter((x) => x.maxDd !== null).map((x) => ({ row: x.key, label: x.short, value: x.maxDd!, details: this.layerDetails(x) })),
  );
  protected readonly signedPct1 = (v: number) => fmtSignedPct(v, 1);
  protected readonly pct1 = (v: number) => fmtPct(v, 1);
  protected readonly fixed2 = (v: number) => fmtFixed(v, 2);

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
