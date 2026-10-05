import { Component, computed, input } from '@angular/core';
import { FORMAT_PIPES, fmtPct } from '../core/format';
import { CoverageLevel } from '../core/models';
import { VIZ } from './viz';

/**
 * Abstention table: what acting only on the most confident share of forecasts would have earned.
 * One row per coverage level; the 10% row is highlighted as the usual "act only when sure" setting.
 */
@Component({
  selector: 'app-coverage-table',
  imports: [...VIZ, ...FORMAT_PIPES],
  template: `
    @if (rows().length) {
      <div class="table-wrap">
        <table class="table compact">
          <thead>
            <tr>
              <th>Act on <app-help text="The most confident share of all forecasts. 10% means only the one call in ten the model was surest about." topic="abstention" label="coverage" /></th>
              <th class="num">{{ thresholdLabel() }} <app-help [text]="thresholdHint()" topic="abstention" label="threshold" /></th>
              <th class="num">n</th>
              <th class="num">Right <app-help text="Share of those calls whose direction was correct. A coin scores 50%." topic="hit-rate" label="accuracy" /></th>
              <th class="num">Net per position <app-help [text]="'Mean excess return over the sector ETF of acting on those calls, after ' + costText() + ' per position, with a 95% interval from a bootstrap over blocks of dates.'" topic="abstention" label="net return" /></th>
            </tr>
          </thead>
          <tbody>
            @for (r of rows(); track r.coverage) {
              <tr [class.row-current]="r.coverage === 0.1">
                <td>
                  <strong>{{ r.coverage | pct: 0 }}</strong>
                  @if (r.coverage === 1) { <span class="small muted">(everything)</span> }
                </td>
                <td class="num">{{ threshold(r) }}</td>
                <td class="num">{{ r.n | num }}</td>
                <td class="num">
                  {{ r.accuracy | pct: 0 }}
                  <app-meter [value]="r.accuracy" [min]="0.3" [max]="0.7" [target]="0.5" targetLabel="coin flip" label="Accuracy" />
                </td>
                <td class="num net">
                  <app-delta [value]="r.meanNet" kind="pct" [digits]="2" />
                  <div class="ci-line">
                    <app-range-bar [lo]="r.ciLow" [hi]="r.ciHigh" [point]="r.meanNet" [span]="span()" [label]="'Net ' + (r.meanNet * 100).toFixed(2) + '%, interval ' + (r.ciLow * 100).toFixed(2) + '% to ' + (r.ciHigh * 100).toFixed(2) + '%'" />
                    <span class="small muted">{{ r.ciLow | signedPct: 2 }} to {{ r.ciHigh | signedPct: 2 }}</span>
                  </div>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <p class="small muted" style="margin-top: 0.5rem">
        {{ verdict() }}
      </p>
    } @else {
      <p class="muted">No coverage curve: the evaluation predates this feature. Re-run it from Data &amp; pipeline.</p>
    }
  `,
  styles: `
    td { white-space: nowrap; }
    td .meter { display: block; width: auto; min-width: 56px; margin: 0.3rem 0 0; }
    td.net { min-width: 180px; }
    .ci-line { display: flex; justify-content: flex-end; align-items: center; gap: 0.4rem; margin-top: 0.2rem; }
  `,
})
export class CoverageTable {
  readonly rows = input<CoverageLevel[]>([]);
  /** What one position is: "a stock hedged with its ETF" or "a stock". */
  readonly positionLabel = input('a position');

  protected readonly side = computed(() => this.rows()[0]?.side ?? 'both');
  protected readonly thresholdLabel = computed(() => (this.side() === 'long' ? 'p at least' : 'Distance from 50%'));
  protected readonly thresholdHint = computed(() =>
    this.side() === 'long'
      ? 'The lowest probability among the calls in this slice: the bar a stock had to clear to be bought.'
      : 'How far from 50% a probability had to be to get in: above it the stock is bought, below it the stock is sold against its ETF.',
  );
  protected readonly costText = computed(() => {
    const c = this.rows()[0]?.costPerPosition ?? 0;
    return `${(c * 1e4).toFixed(0)} bp of costs`;
  });
  protected readonly span = computed(() => {
    let m = 0;
    for (const r of this.rows()) m = Math.max(m, Math.abs(r.ciLow), Math.abs(r.ciHigh), Math.abs(r.meanNet));
    return m || 0.01;
  });

  protected threshold(r: CoverageLevel): string {
    return this.side() === 'long' ? fmtPct(r.minConfidence, 0) : `± ${(r.minConfidence * 100).toFixed(0)} pp`;
  }

  /** One sentence: does the 10% slice pay after costs, and does the interval allow the claim? */
  protected readonly verdict = computed(() => {
    const ten = this.rows().find((r) => r.coverage === 0.1);
    const all = this.rows().find((r) => r.coverage === 1);
    if (!ten || !all) return '';
    const paid = ten.ciLow > 0 ? 'positive after costs and the interval excludes zero' : ten.ciHigh < 0 ? 'negative after costs' : 'not distinguishable from zero';
    const better = ten.accuracy > all.accuracy ? 'higher' : ten.accuracy < all.accuracy ? 'lower' : 'the same';
    return `Acting only on the most confident 10%: accuracy ${fmtPct(ten.accuracy, 0)} against ${fmtPct(all.accuracy, 0)} on everything (${better}); the net return per position is ${paid}. Green bars sit wholly above zero.`;
  });
}
