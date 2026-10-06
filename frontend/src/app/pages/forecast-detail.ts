import { httpResource } from '@angular/common/http';
import { Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DivergingBars, DivergingItem } from '../charts/diverging-bars';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtFixed, fmtPct, fmtSigned } from '../core/format';
import { Factor, ForecastDetail, Provenance } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

@Component({
  selector: 'app-forecast-detail',
  imports: [RouterLink, DivergingBars, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="crumbs"><a routerLink="/forecasts/history">Forecasts</a> / #{{ id() }}</div>
    <app-status [res]="res" what="forecast" />
    @if (f(); as f) {
      <div class="page-head">
        <div class="page-title">
          <app-page-icon name="pulse" area="forecast" />
          <div>
            <h1><a [routerLink]="['/companies', f.symbol]">{{ f.symbol }}</a> vs {{ f.benchmarkSymbol }}</h1>
            <p class="muted">
              <app-model-tag [kind]="f.modelKind" /> model · {{ f.companyName }} · forecast #{{ f.id }} · version
              {{ f.version }}
              <app-issue-mode [mode]="f.issueMode" />
            </p>
          </div>
        </div>
        <div class="page-actions">
          <a routerLink="/guide" fragment="reading" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
          <a class="btn" [routerLink]="['/companies', f.symbol, 'forecasts']">Company forecast history</a>
        </div>
      </div>

      @if (newer(); as n) {
        <div class="alert alert-warn" role="status">
          This version has been superseded by
          <a [routerLink]="['/forecasts', n.id]">version {{ n.version }} (#{{ n.id }})</a>, published {{ n.issuedAt | utc }}.
        </div>
      }

      <div class="grid-2">
        <div class="card forecast-hero">
          <span class="badge badge-forecast">FORECAST</span>
          <p class="small muted" style="margin: 0.5rem 0 0.25rem">{{ f.target }}</p>
          <div class="big">{{ f.probability | pct }} <app-lean [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" /></div>
          <p style="margin: 0.25rem 0 0.5rem">
            interval <strong>{{ f.probLow | pct }} – {{ f.probHigh | pct }}</strong>
            <app-interval-bar [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" />
            <app-help text="The 10th to 90th percentile of the probability across bootstrap refits of the model. It reflects estimation uncertainty only, not everything that could go wrong." topic="interval" label="the interval" />
          </p>
          <dl class="kv">
            <dt>Horizon</dt><dd>{{ f.horizonTradingDays }} trading days (close(t) → close(t+{{ f.horizonTradingDays }}))</dd>
            <dt>Data cutoff (as of)</dt><dd>{{ f.asOf | utc }} <span class="muted small">({{ f.asOfDate }})</span></dd>
            <dt>Published</dt><dd>{{ f.issuedAt | utc }}</dd>
            <dt>Issue mode</dt>
            <dd>
              {{ f.issueMode }}
              @if (f.issueMode === 'REPLAY') {
                <div class="small muted">Replayed: computed and published after the data cutoff using only data available at the cutoff.</div>
              }
            </dd>
            @if (f.reason) {
              <dt>Reason</dt><dd>{{ f.reason }}</dd>
            }
            @if (f.uncertaintyNote) {
              <dt>Uncertainty</dt><dd>{{ f.uncertaintyNote }}</dd>
            }
          </dl>
        </div>

        <div class="card">
          <h3>Outcome</h3>
          @if (f.outcome; as o) {
            <p>
              <span class="badge" [class]="'badge tone-' + (o.outcome ? 'good' : 'bad')">
                {{ o.outcome ? '✓ Outperformed' : '✗ Did not outperform' }}
              </span>
              <span class="small muted">window ended {{ o.windowEndDate }}</span>
            </p>
            <dl class="kv">
              <dt>{{ f.symbol }} return</dt><dd><app-delta [value]="o.stockReturn" kind="pct" /></dd>
              <dt>{{ f.benchmarkSymbol }} return</dt><dd><app-delta [value]="o.benchmarkReturn" kind="pct" /></dd>
              <dt>Excess return</dt>
              <dd><strong><app-delta [value]="o.excessReturn" kind="pct" /></strong></dd>
              <dt>Brier score <app-help text="Squared gap between the probability and what happened (1 or 0). 0 is perfect, 0.25 is a coin flip, 1 is a confident miss." topic="brier" label="Brier score" /></dt>
              <dd>
                {{ o.brier | fixed: 4 }}
                <span class="badge" [class]="'badge tone-' + (o.brier < 0.25 ? 'good' : 'warn')">{{ o.brier < 0.25 ? '✓ better than a coin flip' : '≈ no better than a coin flip' }}</span>
                <app-meter [value]="0.25 - o.brier" [min]="-0.75" [max]="0.25" [target]="0" targetLabel="coin flip" [tone]="o.brier < 0.25 ? 'good' : 'warn'" label="Brier score against a coin flip" />
              </dd>
            </dl>
          } @else {
            <p class="muted">Not yet resolved — the {{ f.horizonTradingDays }}-trading-day window has not closed.</p>
          }

          @if (f.modelVersion; as mv) {
            <h3 style="margin-top: 1rem">Model version</h3>
            <dl class="kv">
              <dt>Model</dt><dd>{{ mv.modelKind | human }} #{{ mv.id }}</dd>
              <dt>Algorithm</dt><dd class="mono">{{ mv.algorithm }}</dd>
              <dt>Trained through</dt><dd>{{ mv.trainedThrough ?? '—' }}</dd>
              <dt>Training samples</dt><dd>{{ mv.nSamples | num }}</dd>
              <dt>Features</dt><dd class="small">{{ mv.featureNames.length }} — <span class="mono">{{ mv.featureNames.join(', ') }}</span></dd>
            </dl>
          }
        </div>
      </div>

      @if (f.versions.length > 1) {
        <div class="card diff-card">
          <div class="card-head">
            <h3><app-icon name="diff" [size]="16" /> What changed <app-help text="This version against an earlier one of the same series: the probability move and, factor by factor, how much each contribution moved. Contributions are in log-odds; a factor that appears or disappears is marked." topic="version" label="version diff" /></h3>
            <label class="small">
              Compare v{{ f.version }} with
              <select (change)="setCompare(+$any($event.target).value)">
                @for (v of otherVersions(); track v.id) {
                  <option [value]="v.id" [selected]="v.id === compareId()">v{{ v.version }} · {{ v.issuedAt | utc }}</option>
                }
              </select>
            </label>
          </div>
          @if (prevRes.isLoading() && !prevRes.hasValue()) {
            <p class="loading">Loading the earlier version…</p>
          } @else if (diff(); as d) {
            <div class="diff-head">
              <div class="diff-prob">
                <span class="small muted">v{{ d.prev.version }}</span>
                <span class="prob">{{ d.prev.probability | pct }}</span>
                <span class="small muted">[{{ d.prev.probLow | pct }}–{{ d.prev.probHigh | pct }}]</span>
                <app-lean [p]="d.prev.probability" [lo]="d.prev.probLow" [hi]="d.prev.probHigh" />
              </div>
              <span class="dash-arrow" aria-hidden="true">→</span>
              <div class="diff-prob">
                <span class="small muted">v{{ f.version }}</span>
                <span class="prob">{{ f.probability | pct }}</span>
                <span class="small muted">[{{ f.probLow | pct }}–{{ f.probHigh | pct }}]</span>
                <app-lean [p]="f.probability" [lo]="f.probLow" [hi]="f.probHigh" />
              </div>
              <div class="diff-sum">
                <app-delta [value]="f.probability - d.prev.probability" kind="pp" [digits]="1" />
                <div class="small muted">{{ d.reason }}</div>
              </div>
            </div>
            @if (d.rows.length) {
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr>
                      <th>Factor</th>
                      <th class="num">v{{ d.prev.version }}</th>
                      <th class="num">v{{ f.version }}</th>
                      <th class="num">Δ contribution</th>
                      <th class="num">Value before → after</th>
                      <th>Sources (this version)</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (r of d.rows; track r.feature) {
                      <tr [class.row-dim]="r.delta === 0 && r.status === 'same'">
                        <td>
                          {{ r.label }}
                          @if (r.status === 'new') { <span class="badge tone-info">new</span> }
                          @else if (r.status === 'removed') { <span class="badge tone-neutral">removed</span> }
                          <div class="small muted mono">{{ r.feature }}</div>
                        </td>
                        <td class="num">{{ r.before | signed: 3 }}</td>
                        <td class="num">{{ r.after | signed: 3 }}</td>
                        <td class="num heat" [class]="'num heat tone-' + (r.delta > 0 ? 'good' : r.delta < 0 ? 'bad' : 'neutral')" [style.--h]="r.heat">
                          <app-delta [value]="r.delta" kind="fixed" [digits]="3" />
                        </td>
                        <td class="num small">{{ r.valueBefore | fixed: 4 }} → {{ r.valueAfter | fixed: 4 }}</td>
                        <td>
                          @for (p of r.provenance; track $index) {
                            <div class="small">
                              <span class="chip">{{ p.type | human }}</span>
                              @if (internal(p); as link) {
                                <a [routerLink]="link">{{ p.label }}</a>
                              } @else if (p.url) {
                                <a [href]="p.url" target="_blank" rel="noopener noreferrer">{{ p.label }} ↗</a>
                              } @else {
                                {{ p.label }}
                              }
                            </div>
                          } @empty {
                            <span class="muted small">—</span>
                          }
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              <p class="small muted" style="margin-top: 0.4rem">
                Sorted by the size of the move. Green raised the probability, red lowered it; unchanged factors are dimmed.
                {{ d.changedFeatures }} of {{ d.rows.length }} factors moved.
              </p>
            } @else {
              <p class="muted">No factor explanation on one of the versions, so only the probabilities can be compared.</p>
            }
          } @else if (prevRes.error()) {
            <p class="muted">The earlier version could not be loaded.</p>
          }
        </div>
      }

      <h2>Key factors <app-help text="Each factor's contribution is its model coefficient times its standardized value, in log-odds. Positive pushes the probability up, negative pulls it down. Event factors are built from documented exposures and are estimates." topic="factors" label="key factors" /></h2>
      <p class="small muted">
        {{ factorNote() }}
      </p>
      @if (!factors().length) {
        <div class="empty-box">No factor explanation available for this forecast.</div>
      } @else {
        <div class="card">
          <app-diverging-bars
            [items]="factorItems()"
            unitLabel="log-odds contribution"
            [label]="'Factor contributions for forecast ' + f.id"
          />
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Factor</th>
                <th>Kind</th>
                <th class="num">Value</th>
                <th class="num">z</th>
                <th class="num">Coef.</th>
                <th class="num">Contribution</th>
                <th>Provenance</th>
              </tr>
            </thead>
            <tbody>
              @for (x of factors(); track x.feature) {
                <tr>
                  <td>{{ x.label || x.feature }}<div class="small muted mono">{{ x.feature }}</div></td>
                  <td>
                    @if (x.kind === 'EVENT_FEATURE') {
                      <span class="badge badge-estimated" title="Derived from events × estimated/reported exposure">EVENT · ESTIMATED</span>
                    } @else {
                      <span class="chip">{{ x.kind | human }}</span>
                    }
                  </td>
                  <td class="num">
                    @if (x.imputed) {
                      <span class="chip" title="No value at the cutoff; the model used its training median, so this contribution comes from the median, not from the stock">imputed · {{ x.imputation ?? 'training median' }}</span>
                    } @else {
                      {{ x.value | fixed: 4 }}
                    }
                  </td>
                  <td class="num">{{ x.z | signed: 2 }}</td>
                  <td class="num">{{ x.coefficient | signed: 3 }}</td>
                  <td class="num">
                    <strong><app-delta [value]="x.contribution" kind="fixed" [digits]="3" /></strong>
                  </td>
                  <td style="min-width: 220px">
                    @for (p of x.provenance ?? []; track $index) {
                      <div class="small">
                        <span class="chip">{{ p.type | human }}</span>
                        @if (internal(p); as link) {
                          <a [routerLink]="link">{{ p.label }}</a>
                        } @else if (p.url) {
                          <a [href]="p.url" target="_blank" rel="noopener noreferrer">{{ p.label }} ↗</a>
                        } @else {
                          {{ p.label }}
                        }
                      </div>
                    } @empty {
                      <span class="muted small">—</span>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <div class="grid-2" style="margin-top: 1.5rem">
        <div class="card">
          <h3>Sources</h3>
          @if (f.sources.length) {
            <ul class="plain">
              @for (s of f.sources; track $index) {
                <li class="small">
                  <span class="chip">{{ s.kind | human }}</span>
                  @if (s.url && s.url.startsWith('/') && !s.url.startsWith('/api/')) {
                    <a [routerLink]="s.url">{{ s.label }}</a>
                  } @else if (s.url) {
                    <a [href]="s.url" target="_blank" rel="noopener noreferrer">{{ s.label }} ↗</a>
                  } @else {
                    {{ s.label }}
                  }
                  @if (s.accessionNo) {
                    <span class="mono muted">{{ s.accessionNo }}</span>
                  }
                  @if (s.publishedAt) {
                    <span class="muted">· published {{ s.publishedAt | utc }}</span>
                  }
                </li>
              }
            </ul>
          } @else {
            <p class="muted">No sources listed.</p>
          }
        </div>

        <div class="card">
          <h3>Version chain</h3>
          @if (f.supersedesId) {
            <p class="small">
              This version supersedes <a [routerLink]="['/forecasts', f.supersedesId]">#{{ f.supersedesId }}</a>.
            </p>
          }
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>Version</th><th>Published</th><th class="num">P</th><th>Reason</th></tr></thead>
              <tbody>
                @for (v of versions(); track v.id) {
                  <tr [class.row-current]="v.id === f.id">
                    <td class="nowrap">
                      @if (v.id === f.id) {
                        <strong>v{{ v.version }}</strong> <span class="small muted">(this)</span>
                      } @else {
                        <a [routerLink]="['/forecasts', v.id]">v{{ v.version }} #{{ v.id }}</a>
                      }
                      @if (v.superseded) {
                        <span class="badge badge-superseded">superseded</span>
                      } @else {
                        <span class="badge badge-forecast">latest</span>
                      }
                    </td>
                    <td class="nowrap small">{{ v.issuedAt | utc }}</td>
                    <td class="num">{{ v.probability | pct }}</td>
                    <td class="small">{{ v.reason ?? '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <h3 style="margin-top: 1rem">Content hash</h3>
          <p class="mono small">{{ f.contentSha256 ?? '—' }}</p>
          <p class="small muted">SHA-256 of the published forecast record; any change produces a new version.</p>
        </div>
      </div>

      @if (featureList().length) {
        <details class="collapsible">
          <summary>Raw feature values ({{ featureList().length }})</summary>
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>Feature</th><th class="num">Value</th></tr></thead>
              <tbody>
                @for (kv of featureList(); track kv[0]) {
                  <tr><td class="mono">{{ kv[0] }}</td><td class="num">{{ kv[1] | fixed: 5 }}</td></tr>
                }
              </tbody>
            </table>
          </div>
        </details>
      }
    }
  `,
  styles: `
    .diff-card { border-left: 4px solid var(--series-1); }
    .diff-card h3 { display: inline-flex; align-items: center; gap: 0.4rem; }
    .diff-head { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
    .diff-prob { display: inline-flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
    .diff-prob .prob { font-size: 1.1rem; }
    .diff-sum { display: flex; flex-direction: column; }
    .diff-sum .delta { font-size: 1.1rem; font-weight: 700; }
  `,
})
export class ForecastDetailPage {
  readonly id = input.required<string>();
  protected readonly res = httpResource<ForecastDetail>(() => apiUrl.forecast(this.id()));

  protected readonly f = computed(() => {
    const v = valueOf(this.res);
    return v ? { ...v, sources: v.sources ?? [], versions: v.versions ?? [] } : null;
  });

  protected readonly factors = computed(() =>
    [...(this.f()?.explanation?.factors ?? [])].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)),
  );

  protected readonly factorNote = computed(() => {
    const ex = this.f()?.explanation;
    let s = 'Contribution = coefficient × standardized feature value, in log-odds units';
    if (ex?.intercept !== null && ex?.intercept !== undefined) s += `, added to the model intercept (${fmtFixed(ex.intercept, 3)})`;
    s += '.';
    if (ex?.baseRate !== null && ex?.baseRate !== undefined) s += ` Training base rate ${fmtPct(ex.baseRate)}.`;
    return s + ' Sorted by absolute contribution. Event features are model estimates built from documented exposures.';
  });

  protected readonly factorItems = computed<DivergingItem[]>(() =>
    this.factors().map((x) => ({
      key: x.feature,
      label: x.label || x.feature,
      value: x.contribution,
      details: [
        `value ${fmtFixed(x.value, 4)} · z ${fmtSigned(x.z, 2)}`,
        `coefficient ${fmtSigned(x.coefficient, 3)}`,
        x.kind === 'EVENT_FEATURE' ? 'event feature (model estimate)' : (x.kind ?? '').toLowerCase().replace(/_/g, ' '),
      ],
    })),
  );

  protected readonly versions = computed(() => {
    const vs = [...(this.f()?.versions ?? [])].sort((a, b) => b.version - a.version);
    const max = vs[0]?.version ?? 0;
    return vs.map((v) => ({ ...v, superseded: v.version < max }));
  });

  protected readonly newer = computed(() => {
    const f = this.f();
    if (!f) return null;
    const top = this.versions()[0];
    return top && top.version > f.version ? top : null;
  });

  protected readonly featureList = computed(() => Object.entries(this.f()?.features ?? {}));

  // ----- version diff -----
  private readonly chosenCompare = signal<number | null>(null);
  protected readonly otherVersions = computed(() => this.versions().filter((v) => v.id !== this.f()?.id));
  protected readonly compareId = computed<number | null>(() => {
    const c = this.chosenCompare();
    if (c && this.otherVersions().some((v) => v.id === c)) return c;
    const f = this.f();
    if (!f) return null;
    const prev = this.otherVersions().filter((v) => v.version < f.version).sort((a, b) => b.version - a.version)[0];
    return prev?.id ?? this.otherVersions()[0]?.id ?? null;
  });
  protected setCompare(id: number): void {
    this.chosenCompare.set(id);
  }
  protected readonly prevRes = httpResource<ForecastDetail>(() => {
    const id = this.compareId();
    return id ? apiUrl.forecast(id) : undefined;
  });

  protected readonly diff = computed(() => {
    const cur = this.f();
    const prev = valueOf(this.prevRes);
    if (!cur || !prev || prev.id === cur.id) return null;
    const a = new Map((prev.explanation?.factors ?? []).map((x) => [x.feature, x]));
    const b = new Map((cur.explanation?.factors ?? []).map((x) => [x.feature, x]));
    const rows: {
      feature: string;
      label: string;
      before: number | null;
      after: number | null;
      delta: number;
      heat: number;
      valueBefore: number | null;
      valueAfter: number | null;
      status: 'same' | 'new' | 'removed';
      provenance: Provenance[];
    }[] = [];
    if (a.size || b.size) {
      for (const feature of new Set([...a.keys(), ...b.keys()])) {
        const x: Factor | undefined = a.get(feature);
        const y: Factor | undefined = b.get(feature);
        rows.push({
          feature,
          label: y?.label || x?.label || feature,
          before: x?.contribution ?? null,
          after: y?.contribution ?? null,
          delta: (y?.contribution ?? 0) - (x?.contribution ?? 0),
          heat: 0,
          valueBefore: x?.value ?? null,
          valueAfter: y?.value ?? null,
          status: !x ? 'new' : !y ? 'removed' : 'same',
          provenance: y?.provenance ?? x?.provenance ?? [],
        });
      }
      const max = Math.max(...rows.map((r) => Math.abs(r.delta)), 1e-9);
      for (const r of rows) r.heat = Math.abs(r.delta) / max;
      rows.sort((p, q) => Math.abs(q.delta) - Math.abs(p.delta));
    }
    const newer = cur.version > prev.version ? cur : prev;
    return {
      prev,
      rows,
      changedFeatures: rows.filter((r) => Math.abs(r.delta) > 1e-6).length,
      reason: newer.reason ?? (cur.version > prev.version ? 'Re-issued without a stated reason.' : ''),
    };
  });

  /** EVENT provenance → in-app route; external links handled in the template. */
  protected internal(p: Provenance): (string | number)[] | null {
    if (p.type === 'EVENT' && p.id !== null && p.id !== undefined) return ['/events', p.id];
    if (p.url && p.url.startsWith('/') && !p.url.startsWith('/api/')) return [p.url];
    return null;
  }
}
