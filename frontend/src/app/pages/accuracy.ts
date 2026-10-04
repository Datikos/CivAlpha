import { httpResource } from '@angular/common/http';
import { Component, computed } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ReliabilityChart, ReliabilitySeries } from '../charts/reliability-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtFixed, fmtNum, fmtPct, fmtSigned, fmtSignedPct } from '../core/format';
import { AccuracyResponse, MODEL_KINDS, ModelKind, ModelMetrics, TradingStats } from '../core/models';
import { UI, modelColor } from '../shared/ui';

interface MetricRow {
  label: string;
  hint: string;
  values: string[];
  /** index of the better model, or -1 */
  better: number;
}

function row<T>(
  label: string,
  hint: string,
  get: (k: ModelKind) => T | undefined | null,
  fmt: (v: T) => string,
  higherIsBetter: boolean | null,
): MetricRow {
  const raw = MODEL_KINDS.map((k) => get(k));
  const values = raw.map((v) => (v === undefined || v === null ? '—' : fmt(v)));
  let better = -1;
  if (higherIsBetter !== null && typeof raw[0] === 'number' && typeof raw[1] === 'number' && raw[0] !== raw[1]) {
    better = (raw[1] > raw[0]) === higherIsBetter ? 1 : 0;
  }
  return { label, hint, values, better };
}

@Component({
  selector: 'app-accuracy',
  imports: [RouterLink, ReliabilityChart, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div>
        <h1>Model accuracy</h1>
        <p class="muted">
          Walk-forward out-of-sample evaluation of BASELINE vs AUGMENTED, plus realized accuracy of forecasts
          actually published.
        </p>
      </div>
    </div>

    <app-status [res]="res" what="accuracy results" />

    @if (res.hasValue()) {
      @if (ev(); as e) {
        @if (e.verdict) {
          <div class="verdict" role="note"><strong>Verdict.</strong> {{ e.verdict }}</div>
        }
        <p class="small muted">
          Evaluation #{{ e.id }} run {{ e.runAt | utc }} · data cutoff {{ e.dataCutoff ?? '—' }}
          <app-demo-badge [show]="e.isDemo" />
          · horizon {{ e.config.horizon }} trading days · sample every {{ e.config.sampleEvery }} days · embargo
          {{ e.config.embargo }} days · fold length {{ e.config.foldLength }} days · min. training
          {{ e.config.minTrainDays }} days · costs {{ e.config.costBpsPerSide }} bps per side
        </p>

        <div class="grid-2">
          <div class="card">
            <h3>Walk-forward metrics (out of sample)</h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Metric</th>
                    @for (k of kinds; track k) {
                      <th class="num"><app-model-tag [kind]="k" /></th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (r of metricRows(); track r.label) {
                    <tr>
                      <td>{{ r.label }}<div class="small muted">{{ r.hint }}</div></td>
                      @for (v of r.values; track $index) {
                        <td class="num" [style.font-weight]="r.better === $index ? 700 : 400">
                          {{ v }}{{ r.better === $index ? ' ✓' : '' }}
                        </td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem">✓ marks the better value; differences may not be significant — see the comparison.</p>
          </div>

          <div class="card">
            <h3>Augmented vs baseline</h3>
            @if (e.comparison; as c) {
              <dl class="kv">
                <dt>Brier difference</dt>
                <dd>
                  <strong>{{ c.brierDiff | signed: 4 }}</strong>
                  <span class="muted"> (95% CI {{ c.ciLow | signed: 4 }} to {{ c.ciHigh | signed: 4 }})</span>
                </dd>
                <dt>Significance</dt>
                <dd>
                  @if (c.ciHigh < 0) {
                    <span class="badge badge-ok">Augmented better (CI excludes 0)</span>
                  } @else if (c.ciLow > 0) {
                    <span class="badge badge-fail">Augmented worse (CI excludes 0)</span>
                  } @else {
                    <span class="badge">No significant difference (CI includes 0)</span>
                  }
                </dd>
                @if (c.foldsCompared) {
                  <dt>Folds won</dt>
                  <dd>
                    {{ c.foldsAugmentedBetter }} of {{ c.foldsCompared }}
                    @if (c.signTestP !== null && c.signTestP !== undefined) {
                      <span class="small muted">(sign test p = {{ c.signTestP | fixed: 2 }})</span>
                    }
                  </dd>
                }
                @if (c.aucDiff !== null && c.aucDiff !== undefined) {
                  <dt>AUC difference</dt><dd>{{ c.aucDiff | signed: 3 }}</dd>
                }
              </dl>
              <div class="ci-plot" role="img"
                [attr.aria-label]="'Brier difference ' + c.brierDiff + ', 95% interval ' + c.ciLow + ' to ' + c.ciHigh">
                <span class="ci-zero"></span>
                <span class="ci-range" [style.left.%]="ciX(c.ciLow)" [style.width.%]="ciX(c.ciHigh) - ciX(c.ciLow)"></span>
                <span class="ci-point" [style.left.%]="ciX(c.brierDiff)"></span>
              </div>
              <div class="small muted ci-axis"><span>← augmented better</span><span>0</span><span>augmented worse →</span></div>
              <p class="small muted" style="margin-top: 0.5rem">{{ c.note ?? 'Negative Brier difference = augmented better.' }}</p>
            } @else {
              <p class="muted">No comparison available.</p>
            }
          </div>
        </div>

        <div class="grid-2" style="margin-top: 1rem">
          <div class="card">
            <h3>Reliability diagram</h3>
            <p class="small muted">Out-of-sample predicted probability vs observed outperformance rate, per bin.</p>
            @if (relSeries().length) {
              <app-reliability-chart [series]="relSeries()" label="Reliability diagram, baseline vs augmented" />
              <details class="chart-table">
                <summary>Data table</summary>
                <div class="table-wrap">
                  <table class="table compact">
                    <thead><tr><th>Model</th><th>Bin</th><th class="num">Mean predicted</th><th class="num">Observed</th><th class="num">n</th></tr></thead>
                    <tbody>
                      @for (s of relSeries(); track s.key) {
                        @for (b of s.bins; track $index) {
                          <tr>
                            <td>{{ s.label }}</td>
                            <td>{{ b.binLow | pct: 0 }}–{{ b.binHigh | pct: 0 }}</td>
                            <td class="num">{{ b.meanPredicted | pct }}</td>
                            <td class="num">{{ b.observedRate | pct }}</td>
                            <td class="num">{{ b.count | num }}</td>
                          </tr>
                        }
                      }
                    </tbody>
                  </table>
                </div>
              </details>
            } @else {
              <p class="muted">No calibration data.</p>
            }
          </div>

          <div class="card">
            <h3>Trading simulation (after costs)</h3>
            <p class="small muted">
              Out-of-sample forecasts turned into positions every {{ e.config.sampleEvery }} trading days,
              charged {{ e.config.costBpsPerSide }} bps per side. Returns are per period unless annualized.
            </p>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Statistic</th>
                    @for (k of kinds; track k) {
                      <th class="num"><app-model-tag [kind]="k" /></th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (r of tradingRows(); track r.label) {
                    <tr>
                      <td>{{ r.label }}<div class="small muted">{{ r.hint }}</div></td>
                      @for (v of r.values; track $index) {
                        <td class="num">{{ v }}</td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem">
              Simulated, not realized. |t| &lt; 2 means the net mean return is not distinguishable from zero.
            </p>
          </div>
        </div>

        <h2>Folds</h2>
        @if (e.folds.length) {
          <div class="table-wrap">
            <table class="table compact">
              <thead>
                <tr>
                  <th>Fold</th><th>Test window</th><th class="num">Train n</th><th class="num">Test n</th>
                  @for (k of kinds; track k) {
                    <th class="num">Brier {{ k | human }}</th>
                  }
                  <th class="num">Diff (aug − base)</th>
                </tr>
              </thead>
              <tbody>
                @for (fd of e.folds; track fd.fold) {
                  <tr>
                    <td>{{ fd.fold }}</td>
                    <td class="nowrap">{{ fd.testStart }} → {{ fd.testEnd }}</td>
                    <td class="num">{{ fd.nTrain | num }}</td>
                    <td class="num">{{ fd.nTest | num }}</td>
                    @for (k of kinds; track k) {
                      <td class="num">{{ fd.brier[k] | fixed: 4 }}</td>
                    }
                    <td class="num">{{ foldDiff(fd.brier) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="muted">No folds recorded.</p>
        }
      } @else {
        <div class="empty-box">
          No evaluation has been run yet. Run it from <a routerLink="/admin">Data &amp; pipeline</a> (Evaluate, or
          load the demo dataset).
        </div>
      }

      <h2>Issued forecasts — realized accuracy</h2>
      <p class="small muted">
        Forecasts published by the platform and later resolved against outcomes. LIVE forecasts were published before
        their outcome window; REPLAY forecasts were computed later from data available at their cutoff and are shown separately.
      </p>
      @if (issuedRows().length) {
        <div class="table-wrap">
          <table class="table compact">
            <thead>
              <tr><th>Mode</th><th>Model</th><th class="num">Issued</th><th class="num">Resolved</th><th class="num">Brier</th><th class="num">Hit rate</th></tr>
            </thead>
            <tbody>
              @for (r of issuedRows(); track r.mode + r.kind) {
                <tr>
                  <td><app-issue-mode [mode]="r.mode" /></td>
                  <td><app-model-tag [kind]="r.kind" /></td>
                  <td class="num">{{ r.v.issued | num }}</td>
                  <td class="num">{{ r.v.resolved | num }}</td>
                  <td class="num">{{ r.v.brier | fixed: 4 }}</td>
                  <td class="num">{{ r.v.hitRate | pct }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        @if (fewResolved()) {
          <p class="small muted" style="margin-top: 0.5rem">
            Few forecasts have resolved so far; realized accuracy is very noisy at this sample size.
          </p>
        }
      } @else {
        <p class="muted">No issued forecasts yet.</p>
      }
    }
  `,
  styles: `
    .ci-plot { position: relative; height: 28px; margin-top: 0.75rem; }
    .ci-plot span { position: absolute; top: 50%; }
    .ci-zero { left: 50%; width: 1px; height: 28px; margin-top: -14px; background: var(--ink-muted); }
    .ci-range { height: 4px; margin-top: -2px; border-radius: 2px; background: var(--series-2); opacity: 0.5; }
    .ci-point { width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; background: var(--series-2); box-shadow: 0 0 0 2px var(--chart-surface); }
    .ci-axis { display: flex; justify-content: space-between; }
  `,
})
export class AccuracyPage {
  protected readonly kinds = MODEL_KINDS;
  protected readonly res = httpResource<AccuracyResponse>(() => apiUrl.accuracy());

  protected readonly ev = computed(() => {
    const e = valueOf(this.res)?.evaluation;
    if (!e) return null;
    return {
      ...e,
      config: e.config ?? { horizon: 21, sampleEvery: 0, embargo: 0, foldLength: 0, minTrainDays: 0, costBpsPerSide: 0 },
      metrics: e.metrics ?? {},
      calibration: e.calibration ?? {},
      trading: e.trading ?? {},
      folds: e.folds ?? [],
    };
  });

  protected readonly metricRows = computed<MetricRow[]>(() => {
    const m = this.ev()?.metrics ?? {};
    const g = (key: keyof ModelMetrics) => (k: ModelKind) => m[k]?.[key];
    return [
      row('n', 'out-of-sample predictions', g('n'), (v) => fmtNum(v), null),
      row('Brier score', 'lower is better', g('brier'), (v) => fmtFixed(v, 4), false),
      row('Brier skill', 'vs base-rate forecast; > 0 beats it', g('brierSkill'), (v) => fmtSigned(v, 4), true),
      row('Log loss', 'lower is better', g('logLoss'), (v) => fmtFixed(v, 4), false),
      row('AUC', '0.5 = no discrimination', g('auc'), (v) => fmtFixed(v, 3), true),
      row('Accuracy', 'at 50% threshold', g('accuracy'), (v) => fmtPct(v), true),
      row('Base rate', 'share of outperformers', g('baseRate'), (v) => fmtPct(v), null),
    ];
  });

  protected readonly tradingRows = computed<MetricRow[]>(() => {
    const t = this.ev()?.trading ?? {};
    const g = (key: keyof TradingStats) => (k: ModelKind) => t[k]?.[key];
    return [
      row('Periods', 'rebalances', g('periods'), (v) => fmtNum(v), null),
      row('Mean gross', 'per period, before costs', g('meanGross'), (v) => fmtSignedPct(v, 3), true),
      row('Mean net', 'per period, after costs', g('meanNet'), (v) => fmtSignedPct(v, 3), true),
      row('t-stat (net)', 'mean net / standard error', g('tStatNet'), (v) => fmtSigned(v, 2), true),
      row('Hit rate', 'periods with net > 0', g('hitRate'), (v) => fmtPct(v), true),
      row('Sharpe (net)', 'annualized', g('sharpeNet'), (v) => fmtSigned(v, 2), true),
      row('Annualized net', 'compounded', g('annualizedNet'), (v) => fmtSignedPct(v, 2), true),
      row('Avg. positions', 'per period', g('avgPositions'), (v) => fmtNum(v, 1), null),
      row('Cost per period', 'turnover × costs', g('turnoverCostPerPeriod'), (v) => fmtPct(v, 3), null),
    ];
  });

  protected readonly relSeries = computed<ReliabilitySeries[]>(() => {
    const cal = this.ev()?.calibration ?? {};
    return MODEL_KINDS.filter((k) => (cal[k] ?? []).length > 0).map((k) => ({
      key: k,
      label: k === 'BASELINE' ? 'Baseline' : 'Augmented',
      color: modelColor(k),
      bins: cal[k] ?? [],
    }));
  });

  protected readonly issuedRows = computed(() => {
    const r = valueOf(this.res);
    const byMode = r?.issuedByMode ?? (r?.issued ? { LIVE: r.issued } : {});
    return (['LIVE', 'REPLAY'] as const).flatMap((mode) => {
      const iss = byMode[mode] ?? {};
      return MODEL_KINDS.filter((k) => !!iss[k]).map((k) => ({ mode, kind: k, v: iss[k]! }));
    });
  });

  protected readonly fewResolved = computed(() => this.issuedRows().some((r) => r.mode === 'LIVE' && r.v.resolved < 30));

  protected ciX(v: number): number {
    const c = this.ev()?.comparison;
    const span = Math.max(Math.abs(c?.ciLow ?? 0), Math.abs(c?.ciHigh ?? 0), Math.abs(c?.brierDiff ?? 0), 1e-6) * 1.15;
    return 50 + (v / span) * 46;
  }

  protected foldDiff(b: Partial<Record<ModelKind, number>>): string {
    const a = b['AUGMENTED'];
    const base = b['BASELINE'];
    return typeof a === 'number' && typeof base === 'number' ? fmtSigned(a - base, 4) : '—';
  }
}
