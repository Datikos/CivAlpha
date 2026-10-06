import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DotWhisker, WhiskerItem, WhiskerSeries } from '../charts/dot-whisker';
import { LineChart, LineSeries } from '../charts/line-chart';
import { ReliabilityChart, ReliabilitySeries } from '../charts/reliability-chart';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtFixed, fmtNum, fmtPct, fmtSigned, fmtSignedPct } from '../core/format';
import { AccuracyResponse, ForecastSummary, LiveTestModel, MODEL_KINDS, ModelKind, ModelMetrics, TradingStats, modelLabel } from '../core/models';
import { CoverageTable } from '../shared/coverage-table';
import { Icon } from '../shared/icon';
import { UI, modelColor } from '../shared/ui';
import { VIZ, verdictTone } from '../shared/viz';

interface MetricRow {
  key: string;
  label: string;
  hint: string;
  topic: string;
  values: string[];
  raw: (number | null | undefined)[];
  /** index of the better model, or -1 */
  better: number;
  /** optional meter: scale and target */
  meter?: { min: number; max: number; target: number | null; higherIsBetter: boolean };
  /** show as a signed delta (sign colouring) */
  signed?: 'pct' | 'fixed';
  invert?: boolean;
}

function row<T>(
  key: string,
  label: string,
  hint: string,
  topic: string,
  get: (k: ModelKind) => T | undefined | null,
  fmt: (v: T) => string,
  higherIsBetter: boolean | null,
  extra: Partial<MetricRow> = {},
): MetricRow {
  const raw = MODEL_KINDS.map((k) => get(k));
  const values = raw.map((v) => (v === undefined || v === null ? '—' : fmt(v)));
  let better = -1;
  if (higherIsBetter !== null) {
    const nums = raw.map((v, i) => (typeof v === 'number' && Number.isFinite(v) ? { v, i } : null)).filter((x) => x !== null);
    if (nums.length > 1 && new Set(nums.map((x) => x.v)).size > 1) {
      better = nums.reduce((b, x) => ((x.v > b.v) === higherIsBetter ? x : b)).i;
    }
  }
  return { key, label, hint, topic, values, raw: raw as (number | null | undefined)[], better, ...extra };
}

@Component({
  selector: 'app-accuracy',
  imports: [RouterLink, FormsModule, ReliabilityChart, LineChart, DotWhisker, Icon, CoverageTable, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="target" area="forecast" />
        <div>
          <h1>Model accuracy</h1>
          <p class="muted">
            Walk-forward out-of-sample evaluation of BASELINE vs AUGMENTED, plus realized accuracy of forecasts
            actually published.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-accuracy" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
        <a class="btn" routerLink="/ablation">Feature fragility →</a>
      </div>
    </div>

    <app-status [res]="res" what="accuracy results" />

    @if (res.hasValue()) {
      @if (ev(); as e) {
        @if (e.verdict) {
          <app-verdict [tone]="verdictToneOf(e.verdict)">{{ e.verdict }}</app-verdict>
        }
        <p class="small muted">
          Evaluation #{{ e.id }} run {{ e.runAt | utc }} · data cutoff {{ e.dataCutoff ?? '—' }}
          · horizon {{ e.config.horizon }} trading days · sample every {{ e.config.sampleEvery }} days · embargo
          {{ e.config.embargo }} days · fold length {{ e.config.foldLength }} days · min. training
          {{ e.config.minTrainDays }} days · costs {{ e.config.costBpsPerSide }} bps per side
        </p>

        @if (modelRows().length) {
          <div class="card">
            <h3>{{ modelRows().length }} models on the same folds <app-help text="Every model scored in the same walk-forward: same test blocks, same purge. The two live logistic models forecast the platform's 21-day target. The gradient-boosted model behind the AI decisions page is scored on the same target, which it has also traded since ADR-0001 (2026-10-06); evaluations before that also show the 10-day label it traded then. The feature-set identifier names the exact input list." topic="models" label="models side by side" /></h3>
            <p class="small muted">
              Dot = the out-of-sample number, whisker = its 95% interval from a bootstrap over 21-day blocks of as-of dates.
              A model has shown skill only when the whole whisker sits on the good side of the dashed line.
            </p>
            <div class="grid-3">
              <div>
                <h4>Brier skill <app-help text="1 minus Brier score over the Brier score of always forecasting the training base rate. Above zero beats that naive forecast; the interval must sit wholly above zero to count." topic="brier-skill" label="Brier skill" /></h4>
                <app-dot-whisker [items]="skillItems()" label="Brier skill per model with 95% interval" [refX]="0" refLabel="base-rate forecast" betterIs="higher" [format]="signed3" />
              </div>
              <div>
                <h4>AUC <app-help text="How well the model ranks outperformers above underperformers. 0.5 is no skill, 1.0 is perfect ranking." topic="auc" label="AUC" /></h4>
                <app-dot-whisker [items]="aucItems()" label="AUC per model with 95% interval" [refX]="0.5" refLabel="no skill" betterIs="higher" [format]="fixed3" />
              </div>
              <div>
                <h4>Top-10% net excess per position <app-help text="What acting only on the surest 10% of the model's forecasts would have earned per position after costs, with its bootstrap interval. The confident slice is where a usable edge would show first." topic="abstention" label="top-10% net excess" /></h4>
                <app-dot-whisker [items]="top10Items()" label="Net excess return per position of the top-10% most confident forecasts, with 95% interval" [refX]="0" refLabel="zero" betterIs="higher" [format]="signedPct2" />
              </div>
            </div>
            @if (!hasModelCis()) {
              <p class="small muted" style="margin-top: 0.5rem">
                This evaluation stored no intervals for Brier skill and AUC (kept since 2026-10-06); re-run the evaluation on
                the Data &amp; pipeline page to see the whiskers.
              </p>
            }
            <details class="chart-table">
              <summary>Data table ({{ modelRows().length }} models)</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr>
                      <th>Model</th><th>Feature set</th><th>Label</th><th class="num">n</th>
                      <th class="num">Brier skill (95% CI)</th><th class="num">AUC (95% CI)</th><th class="num">Top-10% net excess (95% CI)</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (m of modelRows(); track m.kind) {
                      <tr>
                        <td class="mono">{{ m.kind }}</td>
                        <td><span class="mono">{{ m.featureSet }}</span><div class="small muted">{{ m.nFeatures }} inputs · {{ m.algorithm }}</div></td>
                        <td>{{ m.horizon }} days from {{ m.entry }}</td>
                        <td class="num">{{ m.n | num }}</td>
                        <td class="num"><app-delta [value]="m.brierSkill" kind="fixed" [digits]="3" />@if (m.skillCi) { <div class="small muted">{{ m.skillCi[0] | signed: 3 }} to {{ m.skillCi[1] | signed: 3 }}</div> }</td>
                        <td class="num">{{ m.auc | fixed: 3 }}@if (m.aucCi) { <div class="small muted">{{ m.aucCi[0] | fixed: 3 }} to {{ m.aucCi[1] | fixed: 3 }}</div> }</td>
                        <td class="num">@if (m.top10 === null) { — } @else { <app-delta [value]="m.top10" kind="pct" [digits]="2" /><div class="small muted">{{ m.top10Lo | signedPct: 2 }} to {{ m.top10Hi | signedPct: 2 }}</div> }</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        }

        @if (calRows().length) {
          <div class="card">
            <h3>What the probabilities are worth after calibration <app-help text="Each model's out-of-sample probabilities mapped through an isotonic curve fitted on earlier folds only, so a stated 60% becomes what 60% has meant so far (ADR-0002). The first three folds have no map and are left out of both sides, so raw and calibrated are compared on the same rows. A Brier change whose whole whisker sits left of zero means the model was overconfident: calibration fixed its scores without adding information." topic="calibration" label="calibration" /></h3>
            <p class="small muted">
              Honest probabilities cannot know more than the model does. If calibration helps the Brier score but the spread
              collapses and the confident decile falls to a coin flip, the confidence was the overconfidence.
            </p>
            <div class="grid-3">
              <div>
                <h4>Brier change from calibration <app-help text="Brier(calibrated) minus Brier(raw) on the same rows with a 95% interval from a bootstrap over 21-day blocks of as-of dates. Left of zero: calibration helped." topic="brier" label="Brier change" /></h4>
                <app-dot-whisker [items]="calDiffItems()" label="Change in Brier score from calibration per model, with 95% interval" [refX]="0" refLabel="no change" betterIs="lower" [format]="signed4" />
              </div>
              <div>
                <h4>Spread of the probabilities <app-help text="Standard deviation of the probabilities before and after the map. A spread that shrinks towards zero says the model's confidence was not backed by outcomes." topic="calibration" label="spread" /></h4>
                <app-dot-whisker [items]="calSpreadItems()" [series]="calSeries" label="Standard deviation of the probabilities per model, raw and calibrated" [refX]="null" [betterIs]="null" [format]="fixed3" />
              </div>
              <div>
                <h4>Confident decile hit rate <app-help text="How often the 10% of forecasts farthest from 50% were right, raw and calibrated, on the same rows. 50% is a coin flip." topic="abstention" label="confident decile" /></h4>
                <app-dot-whisker [items]="calHitItems()" [series]="calSeries" label="Hit rate of the most confident 10% per model, raw and calibrated" [refX]="0.5" refLabel="coin flip" [betterIs]="null" [format]="pct0" />
              </div>
            </div>
            @if (calRel(); as rel) {
              <div class="cal-rel">
                <div class="chart-head">
                  <h4>Reliability before and after <app-help text="Mean predicted probability against the observed rate per bin, for one model, raw and calibrated on the same rows. Dots on the diagonal are honest." topic="calibration" label="reliability before and after" /></h4>
                  <label class="field small">Model
                    <select [ngModel]="calPickKey()" (ngModelChange)="calPick.set($event)">
                      @for (r of calRows(); track r.kind) { <option [value]="r.kind">{{ r.label }}</option> }
                    </select>
                  </label>
                </div>
                <app-reliability-chart [series]="rel" label="Reliability diagram, raw vs calibrated" />
              </div>
            }
            <details class="chart-table">
              <summary>Data table ({{ calRows().length }} models, same rows before and after)</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr><th>Model</th><th class="num">n</th><th class="num">Brier skill</th><th class="num">AUC</th><th class="num">Spread</th><th class="num">ECE</th><th class="num">Confident 10% hit</th><th class="num">Confident 10% net excess</th><th class="num">Brier change (95% CI)</th></tr>
                  </thead>
                  <tbody>
                    @for (r of calRows(); track r.kind) {
                      <tr>
                        <td>{{ r.label }}<div class="small muted">{{ r.c.method }}</div></td>
                        <td class="num">{{ r.c.n | num }}</td>
                        <td class="num">{{ r.c.rawBrierSkill | signed: 3 }} → {{ r.c.brierSkill | signed: 3 }}</td>
                        <td class="num">{{ r.c.rawAuc | fixed: 3 }} → {{ r.c.auc | fixed: 3 }}</td>
                        <td class="num">{{ r.c.rawSpread | fixed: 3 }} → {{ r.c.spread | fixed: 3 }}</td>
                        <td class="num">{{ r.c.rawEce | fixed: 3 }} → {{ r.c.ece | fixed: 3 }}</td>
                        <td class="num">{{ r.c.rawConfidentHitRate | pct: 0 }} → {{ r.c.confidentHitRate | pct: 0 }}</td>
                        <td class="num">{{ r.c.rawConfidentMeanNetExcess | signedPct: 2 }} → {{ r.c.confidentMeanNetExcess | signedPct: 2 }}</td>
                        <td class="num"><app-delta [value]="r.c.brierDiff" kind="fixed" [digits]="4" [invert]="true" /><div class="small muted">{{ r.c.brierDiffCi95[0] | signed: 4 }} to {{ r.c.brierDiffCi95[1] | signed: 4 }}</div></td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        }

        @if (headline(); as h) {
          <div class="stats wide">
            <div class="stat" [class]="'stat ' + (h.brierTone)">
              <div class="stat-label">Best Brier score <app-help text="Mean squared error of the probabilities, out of sample. 0 is perfect, 0.25 is what always saying 50% scores. Lower is better." topic="brier" label="Brier score" /></div>
              <div class="stat-value">{{ h.brier | fixed: 4 }}</div>
              <div class="stat-sub"><app-model-tag [kind]="h.brierModel" /> · vs 0.25 for a coin flip</div>
              <app-meter [value]="0.25 - h.brier" [min]="-0.05" [max]="0.1" [target]="0" targetLabel="coin flip" [tone]="h.brierTone === 'tone-good' ? 'good' : 'warn'" label="Brier score against a coin flip" />
            </div>
            <div class="stat" [class]="'stat ' + h.aucTone">
              <div class="stat-label">Best AUC <app-help text="How well the model ranks outperformers above underperformers. 0.5 is no skill, 1.0 is perfect ranking." topic="auc" label="AUC" /></div>
              <div class="stat-value">{{ h.auc | fixed: 3 }}</div>
              <div class="stat-sub"><app-model-tag [kind]="h.aucModel" /> · 0.5 = no discrimination</div>
              <app-meter [value]="h.auc" [min]="0.4" [max]="0.7" [target]="0.5" targetLabel="no skill" label="AUC" />
            </div>
            <div class="stat" [class]="'stat ' + h.cmpTone">
              <div class="stat-label">Does the event data help? <app-help text="The difference in Brier score between the AUGMENTED and the BASELINE model with a bootstrap 95% confidence interval. Negative means the event data helps; the interval must exclude zero to count." topic="confidence-interval" label="augmented vs baseline" /></div>
              <div class="stat-value">{{ h.cmpText }}</div>
              <div class="stat-sub">Brier difference <app-delta [value]="h.brierDiff" kind="fixed" [digits]="4" [invert]="true" /> · CI {{ h.ciLow | signed: 4 }} to {{ h.ciHigh | signed: 4 }}</div>
            </div>
            <div class="stat" [class]="'stat ' + h.tradeTone">
              <div class="stat-label">Simulated trading, net <app-help text="Mean return per rebalance period after costs when the out-of-sample forecasts are turned into long/short positions. Only a t-statistic above 2 would make it distinguishable from zero." topic="t-stat" label="net return" /></div>
              <div class="stat-value"><app-delta [value]="h.meanNet" kind="pct" [digits]="2" /></div>
              <div class="stat-sub">per period · t = {{ h.tStat | signed: 2 }} {{ h.tStat !== null && h.tStat !== undefined && Math.abs(h.tStat) >= 2 ? '(significant)' : '(not distinguishable from zero)' }}</div>
            </div>
          </div>
        }

        <div class="grid-2">
          <div class="card">
            <h3>Walk-forward metrics (out of sample)</h3>
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Metric</th>
                    @for (k of kinds; track k) {
                      <th class="num"><app-model-tag [kind]="k" /><div class="small muted mono">{{ featureSetOf(k) }}</div></th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (r of metricRows(); track r.key) {
                    <tr>
                      <td>
                        {{ r.label }}<app-help [text]="r.hint" [topic]="r.topic" [label]="r.label" />
                        <div class="small muted">{{ r.hint }}</div>
                      </td>
                      @for (v of r.values; track $index) {
                        <td class="num" [style.font-weight]="r.better === $index ? 650 : 400">
                          @if (r.signed) {
                            <app-delta [value]="r.raw[$index]" [kind]="r.signed" [digits]="r.signed === 'fixed' ? 4 : 1" [invert]="!!r.invert" />
                          } @else {
                            {{ v }}
                          }
                          @if (r.better === $index) {
                            <span class="badge-best" title="Better of the two">✓</span>
                          }
                          @if (r.meter && r.raw[$index] !== null && r.raw[$index] !== undefined) {
                            <app-meter [value]="r.raw[$index]" [min]="r.meter.min" [max]="r.meter.max" [target]="r.meter.target" [higherIsBetter]="r.meter.higherIsBetter" [label]="r.label" />
                          }
                        </td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 0.5rem"><span class="badge-best">✓</span> marks the better value; differences may not be significant — see the comparison.</p>
          </div>

          <div class="card">
            <h3>Augmented vs baseline</h3>
            @if (e.comparison; as c) {
              <dl class="kv">
                <dt>Brier difference</dt>
                <dd>
                  <strong><app-delta [value]="c.brierDiff" kind="fixed" [digits]="4" [invert]="true" /></strong>
                  <span class="muted"> (95% CI {{ c.ciLow | signed: 4 }} to {{ c.ciHigh | signed: 4 }})</span>
                </dd>
                <dt>Significance</dt>
                <dd>
                  @if (c.ciHigh < 0) {
                    <span class="badge tone-good">✓ Augmented better (CI excludes 0)</span>
                  } @else if (c.ciLow > 0) {
                    <span class="badge tone-bad">✗ Augmented worse (CI excludes 0)</span>
                  } @else {
                    <span class="badge tone-neutral">≈ No significant difference (CI includes 0)</span>
                  }
                </dd>
                @if (c.foldsCompared) {
                  <dt>Folds won</dt>
                  <dd>
                    {{ c.foldsAugmentedBetter }} of {{ c.foldsCompared }}
                    <app-meter class="meter-inline" [value]="c.foldsAugmentedBetter ?? 0" [max]="c.foldsCompared" [target]="c.foldsCompared / 2" targetLabel="half" label="Folds won by augmented" />
                    @if (c.signTestP !== null && c.signTestP !== undefined) {
                      <span class="small muted">(sign test p = {{ c.signTestP | fixed: 2 }})</span>
                    }
                  </dd>
                }
                @if (c.aucDiff !== null && c.aucDiff !== undefined) {
                  <dt>AUC difference</dt><dd><app-delta [value]="c.aucDiff" kind="fixed" [digits]="3" /></dd>
                }
              </dl>
              <div class="ci-plot" role="img"
                [attr.aria-label]="'Brier difference ' + c.brierDiff + ', 95% interval ' + c.ciLow + ' to ' + c.ciHigh">
                <span class="ci-zero"></span>
                <span class="ci-range" [class]="'ci-range tone-' + ciTone(c)" [style.left.%]="ciX(c.ciLow)" [style.width.%]="ciX(c.ciHigh) - ciX(c.ciLow)"></span>
                <span class="ci-point" [class]="'ci-point tone-' + ciTone(c)" [style.left.%]="ciX(c.brierDiff)"></span>
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
            <h3>Reliability diagram <app-help text="Each dot is a bin of forecasts. The x-axis is what the model said, the y-axis is how often it happened. Dots on the diagonal mean the probabilities are honest; above it the model is under-confident, below it over-confident." topic="calibration" label="reliability diagram" /></h3>
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
                  @for (r of tradingRows(); track r.key) {
                    <tr>
                      <td>{{ r.label }}<app-help [text]="r.hint" [topic]="r.topic" [label]="r.label" /><div class="small muted">{{ r.hint }}</div></td>
                      @for (v of r.values; track $index) {
                        <td class="num">
                          @if (r.signed) {
                            <app-delta [value]="r.raw[$index]" [kind]="r.signed" [digits]="r.signed === 'pct' ? 3 : 2" />
                          } @else {
                            {{ v }}
                          }
                          @if (r.meter && r.raw[$index] !== null && r.raw[$index] !== undefined) {
                            <app-meter [value]="r.raw[$index]" [min]="r.meter.min" [max]="r.meter.max" [target]="r.meter.target" [label]="r.label" />
                          }
                        </td>
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

        <h2>Act only when confident <app-help text="A trader does not act on every stock every day. This ranks the out-of-sample forecasts by how far they sit from 50% and shows what trading only the surest slice would have earned per position, after costs." topic="abstention" label="abstention" /></h2>
        <p class="small muted">
          Each row keeps only the most confident share of all forecasts: long the stock against its ETF when p is above
          50%, short when below, entered at the next close and held {{ e.config.horizon }} trading days. Costs are
          charged on four legs (stock and hedge, in and out). The 10% row is highlighted.
        </p>
        @for (k of kinds; track k) {
          <div class="card">
            <h3><app-model-tag [kind]="k" /></h3>
            <app-coverage-table [rows]="e.trading[k]?.coverage ?? []" positionLabel="a stock hedged with its ETF" />
          </div>
        }

        <h2>Folds <app-help text="The evaluation walks forward through time in blocks (folds). Each fold trains only on samples whose outcome was known before the block began, then scores the block. A model that wins most folds is more convincing than one that wins the average." topic="walk-forward" label="folds" /></h2>
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
                    <td class="num heat" [class]="'num heat tone-' + foldTone(fd.brier)" [style.--h]="foldHeat(fd.brier)">
                      <app-delta [value]="foldDiffValue(fd.brier)" kind="fixed" [digits]="4" [invert]="true" />
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted" style="margin-top: 0.4rem">Green cells: the augmented model was better in that fold; red: worse. Colour depth follows the size of the difference.</p>
        } @else {
          <p class="muted">No folds recorded.</p>
        }
      } @else {
        <div class="empty-box">
          No evaluation has been run yet. Run it from <a routerLink="/admin">Data &amp; pipeline</a> (Evaluate models).
        </div>
      }

      <h2>Accuracy over time <app-help text="Resolved forecasts grouped by the month their window closed. The Brier line should stay below the 0.25 coin-flip line and the hit rate above 50% for the models to be worth anything; a few months prove little." topic="accuracy-over-time" label="accuracy over time" /></h2>
      <p class="small muted">
        Each month: the forecasts whose 21-day window closed that month, scored against what happened.
        <label style="margin-left: 0.5rem"><input type="checkbox" [checked]="includeReplay()" (change)="includeReplay.set($any($event.target).checked)" /> include replayed forecasts</label>
      </p>
      @if (monthly(); as mo) {
        @if (!mo.months.length) {
          <p class="muted">No resolved {{ includeReplay() ? '' : 'live ' }}forecasts yet.</p>
        } @else {
          <div class="grid-2">
            <div class="card">
              <h3>Brier score by month</h3>
              <app-line-chart [series]="mo.brierSeries" label="Mean Brier score per month, baseline vs augmented" [yFormat]="fixed3" [refY]="0.25" refLabel="coin flip" [height]="220" />
            </div>
            <div class="card">
              <h3>Hit rate by month</h3>
              <app-line-chart [series]="mo.hitSeries" label="Hit rate per month, baseline vs augmented" [yFormat]="pct0" [refY]="0.5" refLabel="coin flip" [yMin]="0" [yMax]="1" [height]="220" />
            </div>
          </div>
          <details class="chart-table">
            <summary>Data table ({{ mo.months.length }} months)</summary>
            <div class="table-wrap">
              <table class="table compact">
                <thead><tr><th>Month</th>@for (k of kinds; track k) { <th class="num">n {{ k | human }}</th><th class="num">Brier</th><th class="num">Hit rate</th> }</tr></thead>
                <tbody>
                  @for (m of mo.months; track m.month) {
                    <tr>
                      <td>{{ m.month }}</td>
                      @for (k of kinds; track k) {
                        <td class="num">{{ m.byModel[k]?.n ?? 0 }}</td>
                        <td class="num heat" [class]="'num heat tone-' + ((m.byModel[k]?.brier ?? 0.25) < 0.25 ? 'good' : 'bad')" [style.--h]="m.byModel[k] ? Math.min(1, Math.abs((m.byModel[k]!.brier - 0.25) / 0.1)) : 0">{{ m.byModel[k]?.brier | fixed: 4 }}</td>
                        <td class="num">{{ m.byModel[k]?.hitRate | pct: 0 }}</td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </details>
          @if (mo.months.length < 3) {
            <p class="small muted">Fewer than three months of resolved forecasts: a trend cannot be read yet.</p>
          }
        }
      } @else {
        <app-status [res]="historyRes" what="forecast history" />
      }

      @if (liveTest(); as lt) {
        <div class="card live-test">
          <h3>Pre-registered live test <app-help text="A bar for the live forecasts that was written down before any of them resolved, so the verdict cannot be adjusted to fit the data. Per model: at least the stated number of resolved forecasts, AUC at or above the stated level, and a Brier score below the base-rate Brier (what always forecasting the realized hit rate would score). PENDING until enough forecasts resolve." topic="confidence-interval" label="pre-registered test" /></h3>
          <p class="small muted">Registered {{ lt.registeredOn }}: {{ lt.rule }}</p>
          <div class="table-wrap">
            <table class="table compact">
              <thead>
                <tr><th>Model</th><th class="num">Resolved</th><th class="num">AUC</th><th class="num">Brier</th><th class="num">Base-rate Brier</th><th>Verdict</th></tr>
              </thead>
              <tbody>
                @for (r of liveTestRows(); track r.kind) {
                  <tr>
                    <td><app-model-tag [kind]="r.kind" /></td>
                    <td class="num">
                      {{ r.v.resolved | num }} / {{ lt.minResolved | num }}
                      <app-meter [value]="r.v.progress" label="Resolved share of the required sample" />
                    </td>
                    <td class="num">{{ r.v.auc | fixed: 3 }} <span class="small muted">≥ {{ lt.minAuc | fixed: 2 }}</span></td>
                    <td class="num">{{ r.v.brier | fixed: 4 }}</td>
                    <td class="num">{{ r.v.baseRateBrier | fixed: 4 }}</td>
                    <td>
                      <span class="badge" [class]="'badge tone-' + (r.v.verdict === 'PASS' ? 'good' : r.v.verdict === 'FAIL' ? 'bad' : 'neutral')">{{ r.v.verdict }}</span>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
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
              <tr><th>Mode</th><th>Model</th><th class="num">Issued</th><th class="num">Resolved</th><th class="num">Brier</th><th class="num">Hit rate <app-help text="Share of resolved forecasts that called the direction right (probability above 50% and the stock outperformed, or below and it did not)." topic="hit-rate" label="hit rate" /></th></tr>
            </thead>
            <tbody>
              @for (r of issuedRows(); track r.mode + r.kind) {
                <tr>
                  <td><app-issue-mode [mode]="r.mode" /></td>
                  <td><app-model-tag [kind]="r.kind" /></td>
                  <td class="num">{{ r.v.issued | num }}</td>
                  <td class="num">{{ r.v.resolved | num }}</td>
                  <td class="num">{{ r.v.brier | fixed: 4 }}</td>
                  <td class="num">
                    {{ r.v.hitRate | pct }}
                    @if (r.v.hitRate !== null) {
                      <app-meter [value]="r.v.hitRate" [target]="0.5" targetLabel="coin flip" label="Hit rate" />
                    }
                  </td>
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
    .ci-range { height: 6px; margin-top: -3px; border-radius: 3px; background: var(--tone-mark); opacity: 0.5; }
    .ci-point { width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; background: var(--tone-mark); box-shadow: 0 0 0 2px var(--chart-surface); }
    .ci-axis { display: flex; justify-content: space-between; }
    td .meter { display: block; width: auto; margin: 0.3rem 0 0; }
    .grid-3 h4, .cal-rel h4 { margin: 0.25rem 0 0.35rem; font-size: 0.9rem; }
    .cal-rel { margin-top: 1rem; max-width: 640px; }
    .cal-rel .chart-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
    .cal-rel .chart-head .field { flex-direction: row; align-items: center; gap: 0.5rem; }
  `,
})
export class AccuracyPage {
  protected readonly kinds = MODEL_KINDS;

  protected featureSetOf(k: string): string {
    return this.ev()?.config.models?.[k]?.featureSet ?? '';
  }

  /** Every model of the evaluation, live ones first, with the numbers the charts and the table show. */
  protected readonly modelRows = computed(() => {
    const e = this.ev();
    if (!e) return [];
    const live = MODEL_KINDS as readonly string[];
    const kinds = [...live.filter((k) => !!e.metrics[k]), ...Object.keys(e.metrics).filter((k) => !live.includes(k))];
    return kinds.map((k) => {
      const m = e.metrics[k]!;
      const spec = e.config.models?.[k];
      const horizon = spec?.horizon ?? e.config.horizon;
      const top = (e.trading[k]?.coverage ?? []).find((c) => Math.abs(c.coverage - 0.1) < 1e-9);
      return {
        kind: k,
        label: k.startsWith('AI_BOOK') ? `Book, ${horizon}-day${live.includes(k) ? ' (live)' : ''}` : `${modelLabel(k)} (live)`,
        featureSet: spec?.featureSet ?? '',
        nFeatures: spec?.nFeatures ?? 0,
        algorithm: spec?.algorithm ?? '',
        horizon,
        entry: spec?.entry ?? '',
        n: m.n,
        brierSkill: m.brierSkill,
        skillCi: m.ci?.brierSkill ?? null,
        auc: m.auc,
        aucCi: m.ci?.auc ?? null,
        top10: top ? top.meanNet : null,
        top10Lo: top ? top.ciLow : null,
        top10Hi: top ? top.ciHigh : null,
        calibrated: m.calibrated ?? null,
      };
    });
  });
  protected readonly hasModelCis = computed(() => this.modelRows().some((m) => !!m.skillCi));
  private modelDetails(m: { featureSet: string; nFeatures: number; algorithm: string; horizon: number; entry: string; n: number }): string[] {
    return [`${m.featureSet} · ${m.nFeatures} inputs · ${m.algorithm}`, `label: ${m.horizon} trading days from ${m.entry}`, `n = ${fmtNum(m.n)}`];
  }
  protected readonly skillItems = computed<WhiskerItem[]>(() =>
    this.modelRows().map((m) => ({ row: m.kind, label: m.label, value: m.brierSkill, lo: m.skillCi?.[0], hi: m.skillCi?.[1], details: this.modelDetails(m) })),
  );
  protected readonly aucItems = computed<WhiskerItem[]>(() =>
    this.modelRows().map((m) => ({ row: m.kind, label: m.label, value: m.auc, lo: m.aucCi?.[0], hi: m.aucCi?.[1], details: this.modelDetails(m) })),
  );
  protected readonly top10Items = computed<WhiskerItem[]>(() =>
    this.modelRows()
      .filter((m) => m.top10 !== null)
      .map((m) => ({ row: m.kind, label: m.label, value: m.top10!, lo: m.top10Lo, hi: m.top10Hi, details: this.modelDetails(m) })),
  );
  /** Models with a stored calibration block (ADR-0002), live ones first, as the models chart orders them. */
  protected readonly calRows = computed(() => this.modelRows().flatMap((m) => (m.calibrated ? [{ kind: m.kind, label: m.label, c: m.calibrated }] : [])));
  protected readonly calSeries: WhiskerSeries[] = [
    { key: 'raw', label: 'Raw', color: 'var(--series-1)' },
    { key: 'cal', label: 'Calibrated on earlier folds', color: 'var(--series-2)' },
  ];
  protected readonly calDiffItems = computed<WhiskerItem[]>(() =>
    this.calRows().map((r) => ({ row: r.kind, label: r.label, value: r.c.brierDiff, lo: r.c.brierDiffCi95[0], hi: r.c.brierDiffCi95[1], details: [`${fmtNum(r.c.n)} rows · ${r.c.method}`, `Brier skill ${fmtSigned(r.c.rawBrierSkill ?? null, 3)} → ${fmtSigned(r.c.brierSkill, 3)}`] })),
  );
  protected readonly calSpreadItems = computed<WhiskerItem[]>(() =>
    this.calRows().flatMap((r) => [
      { row: r.kind, label: r.label, series: 'raw', value: r.c.rawSpread, details: [`ECE ${fmtFixed(r.c.rawEce, 3)}`] },
      { row: r.kind, label: r.label, series: 'cal', value: r.c.spread, details: [`ECE ${fmtFixed(r.c.ece, 3)}`] },
    ]),
  );
  protected readonly calHitItems = computed<WhiskerItem[]>(() =>
    this.calRows().flatMap((r) =>
      r.c.rawConfidentHitRate === undefined
        ? []
        : [
            { row: r.kind, label: r.label, series: 'raw', value: r.c.rawConfidentHitRate, details: [`net excess ${fmtSignedPct(r.c.rawConfidentMeanNetExcess ?? null, 2)} per position`] },
            { row: r.kind, label: r.label, series: 'cal', value: r.c.confidentHitRate, details: [`net excess ${fmtSignedPct(r.c.confidentMeanNetExcess, 2)} per position`] },
          ],
    ),
  );
  /** Which model's reliability diagram to show before and after: the book's model when evaluated, else the first. */
  protected readonly calPick = signal<string | null>(null);
  protected readonly calPickKey = computed(() => {
    const rows = this.calRows();
    const pick = this.calPick();
    return rows.some((r) => r.kind === pick) ? pick : (rows.find((r) => r.kind.startsWith('AI_BOOK'))?.kind ?? rows[0]?.kind ?? null);
  });
  protected readonly calRel = computed<ReliabilitySeries[] | null>(() => {
    const rows = this.calRows();
    const r = rows.find((x) => x.kind === this.calPickKey());
    if (!r || !r.c.reliability || !r.c.rawReliability) return null;
    return [
      { key: 'raw', label: `${r.label}, raw`, color: 'var(--series-1)', bins: r.c.rawReliability },
      { key: 'cal', label: `${r.label}, calibrated`, color: 'var(--series-2)', bins: r.c.reliability },
    ];
  });
  protected readonly signed4 = (v: number) => fmtSigned(v, 4);
  protected readonly pct0 = (v: number) => fmtPct(v, 0);
  protected readonly signed3 = (v: number) => fmtSigned(v, 3);
  protected readonly signedPct2 = (v: number) => fmtSignedPct(v, 2);
  protected readonly Math = Math;
  protected readonly verdictToneOf = verdictTone;
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

  /** The four numbers that summarize the evaluation. */
  protected readonly headline = computed(() => {
    const e = this.ev();
    if (!e) return null;
    const m = e.metrics;
    const pick = (key: 'brier' | 'auc', best: 'min' | 'max') => {
      let model: ModelKind = 'BASELINE';
      let val: number | null = null;
      for (const k of MODEL_KINDS) {
        const v = m[k]?.[key];
        if (typeof v !== 'number') continue;
        if (val === null || (best === 'min' ? v < val : v > val)) {
          val = v;
          model = k;
        }
      }
      return { model, val };
    };
    const brier = pick('brier', 'min');
    const auc = pick('auc', 'max');
    const c = e.comparison;
    const t = e.trading;
    let tradeBest: Partial<TradingStats> | undefined;
    for (const k of MODEL_KINDS) {
      const v = t[k];
      if (v && (tradeBest === undefined || (v.meanNet ?? -Infinity) > (tradeBest.meanNet ?? -Infinity))) tradeBest = v;
    }
    const tStat = tradeBest?.tStatNet ?? null;
    const meanNet = tradeBest?.meanNet ?? null;
    if (brier.val === null || auc.val === null) return null;
    return {
      brier: brier.val,
      brierModel: brier.model,
      brierTone: brier.val < 0.25 ? 'tone-good' : 'tone-warn',
      auc: auc.val,
      aucModel: auc.model,
      aucTone: auc.val > 0.52 ? 'tone-good' : 'tone-warn',
      brierDiff: c?.brierDiff ?? null,
      ciLow: c?.ciLow ?? null,
      ciHigh: c?.ciHigh ?? null,
      cmpText: !c ? '—' : c.ciHigh < 0 ? 'Yes' : c.ciLow > 0 ? 'No, it hurts' : 'Not measurably',
      cmpTone: !c ? 'tone-neutral' : c.ciHigh < 0 ? 'tone-good' : c.ciLow > 0 ? 'tone-bad' : 'tone-neutral',
      meanNet,
      tStat,
      tradeTone: meanNet !== null && tStat !== null && tStat > 2 ? 'tone-good' : 'tone-warn',
    };
  });

  protected readonly metricRows = computed<MetricRow[]>(() => {
    const m = this.ev()?.metrics ?? {};
    const g = (key: Exclude<keyof ModelMetrics, 'ci' | 'calibrated'>) => (k: ModelKind) => m[k]?.[key];
    return [
      row('n', 'n', 'out-of-sample predictions', 'walk-forward', g('n'), (v) => fmtNum(v), null),
      row('brier', 'Brier score', 'lower is better; 0.25 = coin flip', 'brier', g('brier'), (v) => fmtFixed(v, 4), false, {
        meter: { min: 0.2, max: 0.3, target: 0.25, higherIsBetter: false },
      }),
      row('skill', 'Brier skill', 'vs base-rate forecast; > 0 beats it', 'brier-skill', g('brierSkill'), (v) => fmtSigned(v, 4), true, { signed: 'fixed' }),
      row('logloss', 'Log loss', 'lower is better', 'log-loss', g('logLoss'), (v) => fmtFixed(v, 4), false),
      row('auc', 'AUC', '0.5 = no discrimination', 'auc', g('auc'), (v) => fmtFixed(v, 3), true, {
        meter: { min: 0.4, max: 0.7, target: 0.5, higherIsBetter: true },
      }),
      row('acc', 'Accuracy', 'at 50% threshold', 'hit-rate', g('accuracy'), (v) => fmtPct(v), true, {
        meter: { min: 0.4, max: 0.6, target: 0.5, higherIsBetter: true },
      }),
      row('base', 'Base rate', 'share of outperformers', 'base-rate', g('baseRate'), (v) => fmtPct(v), null),
    ];
  });

  protected readonly tradingRows = computed<MetricRow[]>(() => {
    const t = this.ev()?.trading ?? {};
    const g = (key: Exclude<keyof TradingStats, 'coverage'>) => (k: ModelKind) => t[k]?.[key];
    return [
      row('periods', 'Periods', 'rebalances', 'walk-forward', g('periods'), (v) => fmtNum(v), null),
      row('gross', 'Mean gross', 'per period, before costs', 'costs', g('meanGross'), (v) => fmtSignedPct(v, 3), true, { signed: 'pct' }),
      row('net', 'Mean net', 'per period, after costs', 'costs', g('meanNet'), (v) => fmtSignedPct(v, 3), true, { signed: 'pct' }),
      row('t', 't-stat (net)', 'mean net / standard error; |t| ≥ 2 is significant', 't-stat', g('tStatNet'), (v) => fmtSigned(v, 2), true, { signed: 'fixed' }),
      row('hit', 'Hit rate', 'periods with net > 0', 'hit-rate', g('hitRate'), (v) => fmtPct(v), true, {
        meter: { min: 0.3, max: 0.7, target: 0.5, higherIsBetter: true },
      }),
      row('sharpe', 'Sharpe (net)', 'annualized', 'sharpe', g('sharpeNet'), (v) => fmtSigned(v, 2), true, { signed: 'fixed' }),
      row('ann', 'Annualized net', 'compounded', 'cagr', g('annualizedNet'), (v) => fmtSignedPct(v, 2), true, { signed: 'pct' }),
      row('pos', 'Avg. positions', 'per period', 'exposure', g('avgPositions'), (v) => fmtNum(v, 1), null),
      row('cost', 'Cost per period', 'turnover × costs', 'costs', g('turnoverCostPerPeriod'), (v) => fmtPct(v, 3), null),
    ];
  });

  protected readonly relSeries = computed<ReliabilitySeries[]>(() => {
    const cal = this.ev()?.calibration ?? {};
    return MODEL_KINDS.filter((k) => (cal[k] ?? []).length > 0).map((k) => ({
      key: k,
      label: modelLabel(k),
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
  protected readonly liveTest = computed(() => valueOf(this.res)?.liveTest ?? null);
  protected readonly liveTestRows = computed(() => {
    const lt = this.liveTest();
    return lt ? (Object.entries(lt.models) as [ModelKind, LiveTestModel][]).map(([kind, v]) => ({ kind, v })) : [];
  });

  // ----- accuracy over time -----
  protected readonly includeReplay = signal(false);
  protected readonly historyRes = httpResource<ForecastSummary[]>(() => apiUrl.forecastsHistory());
  protected readonly fixed3 = (v: number) => fmtFixed(v, 3);
  protected readonly monthly = computed(() => {
    const list = valueOf(this.historyRes);
    if (!list) return null;
    const replay = this.includeReplay();
    const acc = new Map<string, Record<string, { n: number; brier: number; hits: number }>>();
    for (const f of list) {
      if (!f.outcome || (!replay && f.issueMode !== 'LIVE')) continue;
      const month = f.outcome.windowEndDate.slice(0, 7);
      const by = acc.get(month) ?? {};
      const m = by[f.modelKind] ?? { n: 0, brier: 0, hits: 0 };
      m.n++;
      m.brier += f.outcome.brier;
      if (f.probability > 0.5 === f.outcome.outcome) m.hits++;
      by[f.modelKind] = m;
      acc.set(month, by);
    }
    const months = [...acc.keys()].sort().map((month) => {
      const byModel: Partial<Record<ModelKind, { n: number; brier: number; hitRate: number }>> = {};
      for (const k of MODEL_KINDS) {
        const m = acc.get(month)![k];
        if (m) byModel[k] = { n: m.n, brier: m.brier / m.n, hitRate: m.hits / m.n };
      }
      return { month, byModel };
    });
    const series = (pick: (v: { brier: number; hitRate: number }) => number): LineSeries[] =>
      MODEL_KINDS.map((k) => ({
        key: k,
        label: modelLabel(k),
        color: modelColor(k),
        points: months.filter((m) => m.byModel[k]).map((m) => ({ x: Date.parse(m.month + '-15T00:00:00Z'), y: pick(m.byModel[k]!) })),
      })).filter((s) => s.points.length);
    return { months, brierSeries: series((v) => v.brier), hitSeries: series((v) => v.hitRate) };
  });

  /** Largest |fold difference|, for heat scaling. */
  private readonly maxFoldDiff = computed(() => {
    let m = 0;
    for (const fd of this.ev()?.folds ?? []) {
      const d = this.foldDiffValue(fd.brier);
      if (d !== null) m = Math.max(m, Math.abs(d));
    }
    return m || 1;
  });

  protected ciX(v: number): number {
    const c = this.ev()?.comparison;
    const span = Math.max(Math.abs(c?.ciLow ?? 0), Math.abs(c?.ciHigh ?? 0), Math.abs(c?.brierDiff ?? 0), 1e-6) * 1.15;
    return 50 + (v / span) * 46;
  }

  protected ciTone(c: { ciLow: number; ciHigh: number }): string {
    return c.ciHigh < 0 ? 'good' : c.ciLow > 0 ? 'bad' : 'neutral';
  }

  protected foldDiffValue(b: Partial<Record<ModelKind, number>>): number | null {
    const a = b['AUGMENTED'];
    const base = b['BASELINE'];
    return typeof a === 'number' && typeof base === 'number' ? a - base : null;
  }

  protected foldTone(b: Partial<Record<ModelKind, number>>): string {
    const d = this.foldDiffValue(b);
    if (d === null || d === 0) return 'neutral';
    return d < 0 ? 'good' : 'bad';
  }

  protected foldHeat(b: Partial<Record<ModelKind, number>>): number {
    const d = this.foldDiffValue(b);
    return d === null ? 0 : Math.min(1, Math.abs(d) / this.maxFoldDiff());
  }
}
