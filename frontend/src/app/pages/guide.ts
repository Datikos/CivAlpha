import { httpResource } from '@angular/common/http';
import { Component, DestroyRef, afterNextRender, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES, fmtPct } from '../core/format';
import { MetaService } from '../core/meta.service';
import {
  AccuracyResponse,
  CompanySummary,
  DecisionsResponse,
  DoublerStudyResponse,
  ForecastSummary,
  StrategiesResponse,
} from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ, leanOf } from '../shared/viz';
import { FAQ, GLOSSARY, METRICS, PAGES, SECTIONS, SHORTCUTS } from './guide-content';

interface Step {
  id: string;
  title: string;
  detail: string;
  done: boolean | null;
  link: string;
  linkLabel: string;
  fragment?: string;
}

@Component({
  selector: 'app-guide',
  imports: [RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="book" area="help" />
        <div>
          <h1>Guide</h1>
          <p class="muted">
            How to use CivAlpha and how to read every number on it. Each section is linked from the
            <span class="help-btn inline" aria-hidden="true"><app-icon name="info" [size]="11" /></span> tips across the platform.
          </p>
        </div>
      </div>
    </div>

    <div class="guide">
      <nav class="toc" aria-label="Guide sections">
        @for (s of sections; track s.id) {
          <a [routerLink]="[]" [fragment]="s.id" [class.active]="active() === s.id" (click)="jump(s.id, $event)">
            <app-icon [name]="s.icon" [size]="16" /> <span>{{ s.title }}</span>
          </a>
        }
      </nav>

      <div class="guide-body">
        <!-- ============ START ============ -->
        <section id="start" class="gsec">
          <h2><app-icon name="compass" /> What CivAlpha does</h2>
          <p class="lead">
            CivAlpha is a research platform on real data for US-listed stocks. It gathers SEC filings, daily prices,
            dividends, macro series and official policy events, then asks one narrow question about every tracked
            stock and keeps score of its own answers.
          </p>
          <div class="grid-3 gcards">
            <div class="card gcard">
              <span class="gnum">1</span>
              <h3>It collects evidence</h3>
              <p class="small">Filings with their XBRL facts and passages, recorded prices, dividends, FRED macro vintages,
                Federal Register and Fed statements. Every value links to its source document.</p>
            </div>
            <div class="card gcard">
              <span class="gnum">2</span>
              <h3>It forecasts one target</h3>
              <p class="small">The probability that a stock's total return over the next 21 trading days beats its
                sector benchmark ETF. Two models: one on prices and fundamentals, one that also sees policy events.</p>
            </div>
            <div class="card gcard">
              <span class="gnum">3</span>
              <h3>It keeps score, honestly</h3>
              <p class="small">Walk-forward accuracy, backtests of classic rules and an AI strategy after costs, a time
                machine for any past date. The verdicts say "not supported" whenever the evidence is thin.</p>
            </div>
          </div>
          <div class="card">
            <h3>The target, as the backend states it <app-help text="Every forecast on the platform answers exactly this question. Nothing forecasts a price level." topic="target" label="the target" /></h3>
            <div class="target-def">{{ meta.target() }}</div>
            <p class="small muted" style="margin: 0.5rem 0 0">
              Measured from the close of the as-of date to the close 21 trading days later. Total return includes dividends.
              The sector ETF is chosen per company on the Universe page.
            </p>
          </div>
          <app-verdict tone="warn" title="Research, not advice.">
            CivAlpha has no brokerage connection and places no orders. It claims no profitability unless its own
            cost-adjusted, out-of-sample evidence supports it, and it labels model estimates separately from recorded
            facts everywhere.
          </app-verdict>
        </section>

        <!-- ============ CHECKLIST ============ -->
        <section id="checklist" class="gsec">
          <h2><app-icon name="checklist" /> Setup checklist</h2>
          <p class="muted">Read live from this installation. Green rows are done; follow the link on the others.</p>
          <div class="card">
            <div class="progress-row">
              <app-meter [value]="doneCount()" [max]="steps().length" tone="good" label="Setup progress" />
              <span class="small muted nowrap">{{ doneCount() }} of {{ steps().length }} done</span>
            </div>
            <ol class="steps">
              @for (s of steps(); track s.id) {
                <li class="step" [class]="'step ' + (s.done === null ? 'pending' : s.done ? 'done' : 'todo')">
                  <span class="step-mark" aria-hidden="true">
                    @if (s.done === null) { … } @else if (s.done) { <app-icon name="check" [size]="14" /> } @else { {{ $index + 1 }} }
                  </span>
                  <div class="step-text">
                    <strong>{{ s.title }}</strong>
                    <div class="small muted">{{ s.detail }}</div>
                  </div>
                  <a class="btn btn-sm" [routerLink]="s.link" [fragment]="s.fragment">{{ s.linkLabel }}</a>
                </li>
              }
            </ol>
          </div>
        </section>

        <!-- ============ READING ============ -->
        <section id="reading" class="gsec">
          <h2><app-icon name="pulse" /> Reading a forecast</h2>
          <p class="muted">Move the sliders. The display below is exactly what the forecast tables show.</p>
          <div class="card explorer">
            <div class="explorer-controls">
              <label class="field">
                Probability: <strong>{{ pct(demoP()) }}</strong>
                <input type="range" min="20" max="80" step="1" [value]="demoP() * 100" (input)="demoP.set(+$any($event.target).value / 100)" aria-label="Probability" />
              </label>
              <label class="field">
                Interval half-width: <strong>{{ pct(demoW(), 0) }}</strong>
                <input type="range" min="0" max="25" step="1" [value]="demoW() * 100" (input)="demoW.set(+$any($event.target).value / 100)" aria-label="Interval half-width" />
              </label>
            </div>
            <div class="explorer-demo">
              <span class="fprob">
                <span class="prob">{{ pct(demoP()) }}</span>
                <span class="prob-int">[{{ pct(demoLo()) }}–{{ pct(demoHi()) }}]</span>
                <app-interval-bar [p]="demoP()" [lo]="demoLo()" [hi]="demoHi()" />
              </span>
              <app-lean [p]="demoP()" [lo]="demoLo()" [hi]="demoHi()" />
            </div>
            <dl class="kv explorer-legend">
              <dt><span class="prob">{{ pct(demoP()) }}</span></dt>
              <dd>The point estimate: the model's probability that the stock beats its sector ETF over 21 trading days. Purple always means "a model output".</dd>
              <dt><span class="prob-int">[{{ pct(demoLo()) }}–{{ pct(demoHi()) }}]</span></dt>
              <dd>The uncertainty interval: the 10th to 90th percentile across bootstrap refits. The tiny bar draws it against the 50% midline.</dd>
              <dt><app-lean [p]="demoP()" [lo]="demoLo()" [hi]="demoHi()" /></dt>
              <dd>{{ demoExplain() }}</dd>
            </dl>
          </div>
          <div class="grid-2">
            <div class="card">
              <h3>What a forecast is not</h3>
              <ul class="small notes">
                <li>Not a price target and not a return forecast. It is a probability of one binary event.</li>
                <li>Not a recommendation. 56% means "slightly more likely than not", and the interval usually says the model is unsure even of that.</li>
                <li>Not certain even when it leans. A lean says the interval excludes 50%, not that the outcome is known.</li>
              </ul>
            </div>
            <div class="card">
              <h3>Where to look next</h3>
              <ul class="small notes">
                <li><strong>Details</strong> on any forecast lists the factors that moved it, each linked to its filing, event or price source.</li>
                <li><strong>Accuracy</strong> tells you whether such probabilities have been worth anything out of sample.</li>
                <li><strong>Forecast history</strong> shows the same forecast's earlier versions and, once resolved, its outcome.</li>
              </ul>
            </div>
          </div>
        </section>

        <!-- ============ COLOURS ============ -->
        <section id="colors" class="gsec">
          <h2><app-icon name="palette" /> Colours, badges and glyphs</h2>
          <p class="muted">Colour is never the only signal: every coloured element also carries a glyph or a word.</p>
          <div class="grid-2">
            <div class="card">
              <h3>Tones</h3>
              <table class="table compact legend-table">
                <tbody>
                  <tr><td><app-delta [value]="0.034" kind="pct" [digits]="1" /></td><td>Green with ▲: favourable, above the reference, supported.</td></tr>
                  <tr><td><app-delta [value]="-0.021" kind="pct" [digits]="1" /></td><td>Red with ▼: unfavourable, below the reference, a loss or a drawdown.</td></tr>
                  <tr><td><span class="badge tone-warn">≈ not supported</span></td><td>Amber: caution. An estimate, an unsupported claim, or a result that cannot be told from luck.</td></tr>
                  <tr><td><span class="prob">52.3%</span></td><td>Purple: a model output (a forecast, an interval, a decision probability).</td></tr>
                  <tr><td><span class="badge badge-fact">REPORTED</span> <span class="badge badge-fact">FILED</span></td><td>Grey: a recorded fact taken straight from a filing or price history.</td></tr>
                  <tr><td><span class="badge badge-estimated">ESTIMATED</span></td><td>Amber dashed: a value the platform inferred, with lower confidence.</td></tr>
                  <tr><td><span class="badge badge-live">LIVE</span> <span class="badge badge-replay">replayed</span></td><td>Green LIVE: published before its window. Blue replayed: reconstructed after the fact.</td></tr>
                  <tr><td><span class="badge badge-official">OFFICIAL</span> <span class="badge badge-news">NEWS ONLY</span></td><td>An event backed by a primary document, or by press reports only (kept out of the models).</td></tr>
                </tbody>
              </table>
            </div>
            <div class="card">
              <h3>Shapes</h3>
              <table class="table compact legend-table">
                <tbody>
                  <tr><td><span class="meter-demo"><app-meter [value]="0.62" [target]="0.5" targetLabel="reference" label="Example meter" /></span></td><td><strong>Meter.</strong> A value on a scale; the dark tick is the reference it must beat (a coin flip, the base rate, a required threshold). Green when it does.</td></tr>
                  <tr><td><span class="cellbar-demo"><app-cell-bar [value]="0.7" [max]="1" text="+12.4%" tone="sign" /></span></td><td><strong>Cell bar.</strong> In a table, the bar behind a number is scaled to the column's largest value, so the eye can rank without reading.</td></tr>
                  <tr><td><app-range-bar [lo]="0.02" [hi]="0.09" [point]="0.055" [span]="0.1" /> <app-range-bar [lo]="-0.05" [hi]="0.04" [point]="-0.01" [span]="0.1" /></td><td><strong>Interval against zero.</strong> Green when the whole 95% interval is above the line, red when wholly below, grey when it crosses zero and the effect may be nothing.</td></tr>
                  <tr><td><span class="heat-demo"><span class="heat tone-good" style="--h: 0.15">0.4%</span><span class="heat tone-good" style="--h: 0.5">1.2%</span><span class="heat tone-good" style="--h: 1">2.9% ★</span></span></td><td><strong>Heat cells.</strong> Deeper colour, larger value. ★ marks the largest in the row.</td></tr>
                  <tr><td><span class="badge-best">✓</span></td><td><strong>Best in column.</strong> The best value among the rows of a comparison table.</td></tr>
                  <tr><td><app-model-tag kind="BASELINE" /> <app-model-tag kind="AUGMENTED" /></td><td><strong>Model keys.</strong> Blue is BASELINE, orange is AUGMENTED, on every chart and table.</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- ============ TOUR ============ -->
        <section id="tour" class="gsec">
          <h2><app-icon name="book" /> Page by page</h2>
          <p class="muted">Each page answers one question. The sidebar groups them: Forecasts, Research, Strategy, Data.</p>
          <div class="page-cards">
            @for (p of pages; track p.id) {
              <article class="card pcard" [id]="p.id">
                <header>
                  <app-page-icon [name]="p.icon" [area]="p.area" />
                  <div>
                    <h3><a [routerLink]="p.path">{{ p.title }}</a></h3>
                    <p class="small question">{{ p.question }}</p>
                  </div>
                </header>
                <ul class="small notes">
                  @for (r of p.read; track $index) {
                    <li>{{ r }}</li>
                  }
                </ul>
                @if (p.caution) {
                  <p class="small caution"><app-icon name="alert" [size]="14" /> {{ p.caution }}</p>
                }
                <a class="btn btn-sm" [routerLink]="p.path">Open {{ p.title }} →</a>
              </article>
            }
          </div>
        </section>

        <!-- ============ METRICS ============ -->
        <section id="metrics" class="gsec">
          <h2><app-icon name="calculator" /> The metrics, explained</h2>
          <div class="card explorer">
            <h3>Try the Brier score</h3>
            <p class="small muted">The score of one forecast is the squared gap between its probability and what happened.</p>
            <div class="explorer-controls">
              <label class="field">
                Forecast probability: <strong>{{ pct(brierP(), 0) }}</strong>
                <input type="range" min="5" max="95" step="1" [value]="brierP() * 100" (input)="brierP.set(+$any($event.target).value / 100)" aria-label="Forecast probability" />
              </label>
              <div class="field">
                What happened
                <div class="seg" role="group" aria-label="Outcome">
                  <button type="button" [class.on]="brierY() === 1" (click)="brierY.set(1)">Outperformed</button>
                  <button type="button" [class.on]="brierY() === 0" (click)="brierY.set(0)">Did not</button>
                </div>
              </div>
            </div>
            <div class="brier-out">
              <div class="brier-formula mono">({{ brierP().toFixed(2) }} − {{ brierY() }})² = <strong>{{ brier().toFixed(4) }}</strong></div>
              <span class="badge" [class]="'badge tone-' + (brier() < 0.25 ? 'good' : brier() === 0.25 ? 'neutral' : 'bad')">
                {{ brier() < 0.25 ? '✓ better than a coin flip (0.25)' : brier() === 0.25 ? '≈ exactly a coin flip' : '✗ worse than a coin flip (0.25)' }}
              </span>
              <app-meter [value]="1 - brier()" [max]="1" [target]="0.75" targetLabel="coin flip" [tone]="brier() < 0.25 ? 'good' : 'bad'" label="Brier score" />
              <p class="small muted">A confident 90% that misses scores 0.81; a timid 55% that misses scores 0.20. Over many forecasts the average is the Brier score on the Accuracy page.</p>
            </div>
          </div>
          <div class="metric-cards">
            @for (m of metrics; track m.id) {
              <article class="card mcard" [id]="m.id">
                <h3>{{ m.name }}</h3>
                <p class="mshort">{{ m.short }}</p>
                <dl class="kv small">
                  <dt>What</dt><dd>{{ m.what }}</dd>
                  <dt>Good</dt><dd>{{ m.good }}</dd>
                  <dt>Where</dt><dd>{{ m.where }}</dd>
                </dl>
              </article>
            }
          </div>
        </section>

        <!-- ============ WORKFLOWS ============ -->
        <section id="workflows" class="gsec">
          <h2><app-icon name="zap" /> Common tasks</h2>
          <div class="grid-2">
            <div class="card">
              <h3>Track a new stock</h3>
              <ol class="small flow">
                <li>Open <a routerLink="/universe">Universe</a> (or press <kbd>⌘K</kbd>, type the ticker, choose "Add to the universe").</li>
                <li>Click <strong>Look up on SEC</strong> to fill the name and CIK; pick the sector and its ETF.</li>
                <li>Leave "member since" as today unless you really held it earlier.</li>
                <li>Let the SEC ingest start, then run <strong>Update prices</strong> on Data &amp; pipeline. Forecasts appear once about six months of prices exist.</li>
              </ol>
            </div>
            <div class="card">
              <h3>Refresh everything after the close</h3>
              <ol class="small flow">
                <li>Open <a routerLink="/admin">Data &amp; pipeline</a> and click <strong>Run pipeline</strong>.</li>
                <li>Watch the jobs table; the log opens for the running job.</li>
                <li>When it finishes, <a routerLink="/">Current forecasts</a>, <a routerLink="/decisions">AI decisions</a> and the <a routerLink="/strategies">Strategy lab</a> are up to date.</li>
                <li>To automate, set <span class="mono">CIVALPHA_PIPELINE_CRON</span> in <span class="mono">.env</span>.</li>
              </ol>
            </div>
            <div class="card">
              <h3>Decide whether to trust a model</h3>
              <ol class="small flow">
                <li>Read the verdict on <a routerLink="/accuracy">Accuracy</a>. Green means supported; amber means not yet.</li>
                <li>Check the Brier tile against 0.25 and the AUC tile against 0.5.</li>
                <li>Look at the reliability diagram: are the dots near the diagonal?</li>
                <li>Only then look at the trading simulation, and only trust a t-statistic above 2.</li>
              </ol>
            </div>
            <div class="card">
              <h3>Check a strategy you believe in</h3>
              <ol class="small flow">
                <li>Find it in the <a routerLink="/strategies">Strategy lab</a> table; the family column groups trend, mean reversion, fundamental, event and AI rules.</li>
                <li>Read the Excess column: is the whole interval above zero? Is the DSR at least 0.95?</li>
                <li>Open the strategy for its drawdown, calendar years and every trade.</li>
                <li>Check cost sensitivity: does it survive 20 or 30 bp per side?</li>
              </ol>
            </div>
            <div class="card">
              <h3>Replay a past date</h3>
              <ol class="small flow">
                <li>Open the <a routerLink="/timemachine">Time machine</a>, pick a date at least 63 trading days ago, click <strong>Go back and forecast</strong>.</li>
                <li>Switch horizons with the segmented control; read the three score tiles.</li>
                <li>Click any row to see that stock's real path inside its forecast band.</li>
              </ol>
            </div>
            <div class="card">
              <h3>Record a policy event</h3>
              <ol class="small flow">
                <li>Open <a routerLink="/events">Policy events</a> and expand <strong>Add sourced event</strong>.</li>
                <li>Give the category, date, severity and the URL of the official document.</li>
                <li>Affected companies, found through their documented exposures, get a new forecast version.</li>
              </ol>
            </div>
            <div class="card">
              <h3>Ask Claude about the data</h3>
              <ol class="small flow">
                <li>The API speaks the Model Context Protocol at <span class="mono">/mcp</span>.</li>
                <li>In Claude Code: <span class="mono">claude mcp add --transport http civalpha http://localhost:8088/mcp</span></li>
                <li>Then ask for a company's evidence, the current forecasts, or to run the pipeline. Verdicts and caveats travel with every answer.</li>
              </ol>
            </div>
          </div>
        </section>

        <!-- ============ GLOSSARY ============ -->
        <section id="glossary" class="gsec">
          <h2><app-icon name="search" /> Glossary</h2>
          <label class="field glossary-search">
            <span class="sr-only">Filter the glossary</span>
            <input type="search" placeholder="Filter terms…" [value]="q()" (input)="q.set($any($event.target).value)" />
          </label>
          <div class="card">
            <dl class="glossary">
              @for (t of terms(); track t.id) {
                <div class="term" [id]="t.id">
                  <dt>{{ t.term }}</dt>
                  <dd>{{ t.def }}</dd>
                </div>
              } @empty {
                <p class="muted">No term matches “{{ q() }}”.</p>
              }
            </dl>
          </div>
        </section>

        <!-- ============ FAQ ============ -->
        <section id="faq" class="gsec">
          <h2><app-icon name="help" /> Questions</h2>
          @for (f of faq; track $index) {
            <details class="collapsible">
              <summary>{{ f.q }}</summary>
              <p class="small" style="margin: 0">{{ f.a }}</p>
            </details>
          }
        </section>

        <!-- ============ SHORTCUTS ============ -->
        <section id="shortcuts" class="gsec">
          <h2><app-icon name="keyboard" /> Keyboard shortcuts</h2>
          <div class="card">
            <table class="table compact">
              <tbody>
                @for (s of shortcuts; track $index) {
                  <tr>
                    <td class="nowrap">
                      @for (k of s.keys; track $index) {
                        <kbd>{{ k }}</kbd>
                      }
                    </td>
                    <td>{{ s.what }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="small muted">The theme switch at the bottom of the sidebar follows the system by default. The sidebar collapses to icons with the arrow next to it.</p>
        </section>
      </div>
    </div>
  `,
  styles: `
    .guide { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 2rem; align-items: start; }
    .toc { position: sticky; top: 1rem; display: flex; flex-direction: column; gap: 2px; }
    .toc a { display: flex; align-items: center; gap: 0.55rem; padding: 0.42rem 0.6rem; border-radius: var(--radius-sm); color: var(--ink-2); font-size: 0.88rem; font-weight: 500; }
    .toc a:hover { background: var(--surface-2); text-decoration: none; color: var(--ink); }
    .toc a.active { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .gsec { scroll-margin-top: 1rem; margin-bottom: 2.5rem; }
    .gsec > h2 { display: flex; align-items: center; gap: 0.5rem; margin-top: 0; font-size: 1.35rem; }
    .gsec > h2 app-icon { color: var(--accent); }
    .lead { font-size: 1.05rem; max-width: 72ch; }
    .gcards { margin-bottom: 1rem; }
    .gcard { position: relative; padding-top: 1.3rem; }
    .gcard h3 { margin-top: 0.3rem; }
    .gcard p { margin: 0; }
    .gnum { position: absolute; top: 0.8rem; right: 1rem; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; font-size: 0.8rem; background: var(--accent-soft); color: var(--accent); }
    .progress-row { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
    .steps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
    .step { display: flex; align-items: center; gap: 0.9rem; padding: 0.65rem 0; border-top: 1px solid var(--grid); }
    .step-text { flex: 1; min-width: 0; }
    .step-mark { flex: none; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 0.8rem; font-weight: 700; background: var(--surface-3); color: var(--ink-2); }
    .step.done .step-mark { background: var(--good-bg); color: var(--good-ink); }
    .step.done strong { color: var(--ink-2); }
    .step.todo .step-mark { background: var(--est-bg); color: var(--est-ink); }
    .explorer-controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 1rem; }
    .explorer-controls input[type='range'] { width: 100%; accent-color: var(--accent); padding: 0; border: none; box-shadow: none; }
    .explorer-demo { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; padding: 0.9rem 1rem; border-radius: var(--radius-sm); background: var(--surface-2); margin-bottom: 0.9rem; }
    .explorer-demo .prob { font-size: 1.3rem; }
    .explorer-legend dt { align-self: start; }
    .legend-table td:first-child { white-space: nowrap; width: 1%; padding-right: 1rem; }
    .meter-demo { display: inline-block; width: 90px; }
    .cellbar-demo { display: inline-block; width: 110px; }
    .heat-demo { display: inline-flex; gap: 2px; }
    .heat-demo .heat { padding: 0.1rem 0.5rem; border-radius: 4px; font-variant-numeric: tabular-nums; }
    .page-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); gap: 1rem; }
    .pcard { margin: 0; display: flex; flex-direction: column; gap: 0.6rem; scroll-margin-top: 1rem; }
    .pcard header { display: flex; gap: 0.8rem; align-items: flex-start; }
    .pcard h3 { margin: 0.1rem 0 0.1rem; }
    .pcard .question { margin: 0; color: var(--ink-2); font-style: italic; }
    .pcard .notes { flex: 1; }
    .pcard .btn { align-self: flex-start; }
    .caution { display: flex; gap: 0.4rem; align-items: flex-start; margin: 0; padding: 0.5rem 0.65rem; border-radius: var(--radius-sm); background: var(--est-bg); color: var(--est-ink); }
    .caution app-icon { flex: none; margin-top: 0.15rem; }
    .notes { margin: 0; padding-left: 1.1rem; }
    .notes li + li { margin-top: 0.35rem; }
    .brier-out { display: grid; gap: 0.6rem; max-width: 520px; }
    .brier-formula { font-size: 1.1rem; }
    .metric-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: 1rem; }
    .mcard { margin: 0; scroll-margin-top: 1rem; }
    .mcard h3 { margin-bottom: 0.1rem; }
    .mshort { color: var(--ink-2); font-style: italic; margin: 0 0 0.6rem; }
    .flow { margin: 0; padding-left: 1.2rem; }
    .flow li + li { margin-top: 0.4rem; }
    .glossary-search { max-width: 320px; margin-bottom: 0.75rem; }
    .glossary { margin: 0; }
    .term { padding: 0.6rem 0; border-top: 1px solid var(--grid); scroll-margin-top: 1rem; }
    .term:first-child { border-top: none; padding-top: 0; }
    .term dt { font-weight: 650; }
    .term dd { margin: 0.15rem 0 0; color: var(--ink-2); font-size: 0.92rem; }
    kbd { font: inherit; font-size: 0.78rem; font-weight: 600; background: var(--surface-2); border: 1px solid var(--border-strong); border-bottom-width: 2px; border-radius: 5px; padding: 0 0.45em; margin-right: 0.25em; }
    .help-btn.inline { width: 15px; height: 15px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--surface-3); color: var(--ink-muted); vertical-align: middle; }
    @media (max-width: 900px) {
      .guide { grid-template-columns: 1fr; gap: 1rem; }
      .toc { position: static; flex-direction: row; flex-wrap: nowrap; overflow-x: auto; gap: 0.3rem; padding-bottom: 0.4rem; margin-bottom: 0.5rem; }
      .toc a { flex: none; white-space: nowrap; border: 1px solid var(--border); }
      .gsec { scroll-margin-top: calc(var(--topbar-h, 54px) + 0.5rem); }
    }
  `,
})
export class GuidePage {
  protected readonly meta = inject(MetaService);
  protected readonly sections = SECTIONS;
  protected readonly pages = PAGES;
  protected readonly metrics = METRICS;
  protected readonly faq = FAQ;
  protected readonly shortcuts = SHORTCUTS;
  protected readonly pct = fmtPct;

  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly fragment = toSignal(this.route.fragment, { initialValue: null });
  protected readonly active = signal('start');

  // ----- live checklist -----
  private readonly companies = httpResource<CompanySummary[]>(() => apiUrl.companies());
  private readonly forecasts = httpResource<ForecastSummary[]>(() => apiUrl.forecastsCurrent());
  private readonly accuracy = httpResource<AccuracyResponse>(() => apiUrl.accuracy());
  private readonly strategies = httpResource<StrategiesResponse>(() => apiUrl.strategies());
  private readonly decisions = httpResource<DecisionsResponse>(() => apiUrl.decisions());
  private readonly doublers = httpResource<DoublerStudyResponse>(() => apiUrl.doublers());

  protected readonly steps = computed<Step[]>(() => {
    const m = this.meta.meta();
    const known = <T>(r: { hasValue(): boolean; error(): unknown }, f: () => T): T | null =>
      r.hasValue() ? f() : r.error() ? false as unknown as T : null;
    const nCompanies = valueOf(this.companies)?.length;
    return [
      {
        id: 'sec',
        title: 'SEC EDGAR access configured',
        detail: 'SEC_USER_AGENT in .env with your name and e-mail. No key needed; it unlocks filings, XBRL facts and exposures.',
        done: m ? m.secConfigured : null,
        link: '/admin',
        linkLabel: 'Data & pipeline',
      },
      {
        id: 'prices',
        title: 'Daily prices configured',
        detail: 'CIVALPHA_PRICE_PROVIDER=tiingo with TIINGO_API_KEY (or yahoo, or CSV imports). Prices drive every forecast and backtest.',
        done: m ? !!m.priceProvider && m.priceProvider !== 'none' : null,
        link: '/admin',
        linkLabel: 'Data & pipeline',
      },
      {
        id: 'universe',
        title: 'Stocks added to the universe',
        detail: nCompanies ? `${nCompanies} tracked. 20 to 50 is a sensible size.` : 'Add tickers with their sector ETF. Nothing runs on an empty universe.',
        done: known(this.companies, () => (nCompanies ?? 0) > 0),
        link: '/universe',
        linkLabel: 'Universe',
      },
      {
        id: 'cutoff',
        title: 'Prices and filings loaded',
        detail: m?.dataCutoff ? `Data cutoff ${m.dataCutoff}.` : 'Run the pipeline once to download prices and ingest filings.',
        done: m ? !!m.dataCutoff : null,
        link: '/admin',
        linkLabel: 'Run pipeline',
      },
      {
        id: 'forecasts',
        title: 'Forecasts issued',
        detail: 'The pipeline issues forecasts once about six months of prices exist for a stock and its benchmark ETF.',
        done: known(this.forecasts, () => (valueOf(this.forecasts)?.length ?? 0) > 0),
        link: '/forecasts',
        linkLabel: 'Current forecasts',
      },
      {
        id: 'accuracy',
        title: 'Models evaluated',
        detail: 'The walk-forward evaluation needs about two years of history. It tells you whether to trust the probabilities.',
        done: known(this.accuracy, () => !!valueOf(this.accuracy)?.evaluation),
        link: '/accuracy',
        linkLabel: 'Accuracy',
      },
      {
        id: 'strategies',
        title: 'Strategy lab backtested',
        detail: 'Classic rules and the AI strategy on one out-of-sample window after costs.',
        done: known(this.strategies, () => !!valueOf(this.strategies)?.run),
        link: '/strategies',
        linkLabel: 'Strategy lab',
      },
      {
        id: 'decisions',
        title: 'AI decisions recorded',
        detail: 'Today\'s ENTER / EXIT / HOLD / STAY OUT per stock, with the factors behind each.',
        done: known(this.decisions, () => (valueOf(this.decisions)?.decisions?.length ?? 0) > 0),
        link: '/decisions',
        linkLabel: 'AI decisions',
      },
      {
        id: 'doublers',
        title: 'Doubler study run',
        detail: 'How often a tracked stock doubled, and whether the screen beats chance.',
        done: known(this.doublers, () => !!valueOf(this.doublers)?.run),
        link: '/doublers',
        linkLabel: 'Doubler study',
      },
    ];
  });
  protected readonly doneCount = computed(() => this.steps().filter((s) => s.done === true).length);

  // ----- forecast explorer -----
  protected readonly demoP = signal(0.56);
  protected readonly demoW = signal(0.04);
  protected readonly demoLo = computed(() => Math.max(0, this.demoP() - this.demoW()));
  protected readonly demoHi = computed(() => Math.min(1, this.demoP() + this.demoW()));
  protected readonly demoExplain = computed(() => {
    const l = leanOf(this.demoP(), this.demoLo(), this.demoHi());
    const p = fmtPct(this.demoP());
    if (l === 'above') return `The whole interval is above 50%, so the chip says "leans above": the model expects outperformance, with ${p} as its best estimate. Widen the interval and watch the lean disappear.`;
    if (l === 'below') return `The whole interval is below 50%, so the chip says "leans below": the model expects the stock to trail its ETF. Widen the interval and watch the lean disappear.`;
    return `The interval straddles 50%, so the chip says "coin flip": ${p} is the best estimate, but the model cannot rule out the other direction. Narrow the interval or move the probability to see a lean.`;
  });

  // ----- Brier explorer -----
  protected readonly brierP = signal(0.65);
  protected readonly brierY = signal<0 | 1>(1);
  protected readonly brier = computed(() => (this.brierP() - this.brierY()) ** 2);

  // ----- glossary -----
  protected readonly q = signal('');
  protected readonly terms = computed(() => {
    const q = this.q().trim().toLowerCase();
    if (!q) return GLOSSARY;
    return GLOSSARY.filter((t) => t.term.toLowerCase().includes(q) || t.def.toLowerCase().includes(q));
  });

  constructor() {
    // Scroll to the fragment once the section exists (content is rendered after the route resolves).
    effect(() => {
      const f = this.fragment();
      if (!f) return;
      setTimeout(() => this.scrollTo(f, false), 60);
    });
    afterNextRender(() => {
      if (typeof IntersectionObserver === 'undefined') return;
      const io = new IntersectionObserver(
        (entries) => {
          for (const e of entries) if (e.isIntersecting) this.active.set((e.target as HTMLElement).id);
        },
        { rootMargin: '-10% 0px -75% 0px', threshold: 0 },
      );
      for (const s of SECTIONS) {
        const el = document.getElementById(s.id);
        if (el) io.observe(el);
      }
      this.destroyRef.onDestroy(() => io.disconnect());
    });
  }

  protected jump(id: string, ev: Event): void {
    ev.preventDefault();
    history.replaceState(null, '', `/guide#${id}`);
    this.scrollTo(id);
  }

  private scrollTo(id: string, smooth = true): void {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    const sec = el.closest('section');
    if (sec?.id) this.active.set(sec.id);
  }
}
