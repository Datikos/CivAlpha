import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../../core/api';
import { FORMAT_PIPES } from '../../core/format';
import { Exposure, ExposureResponse } from '../../core/models';
import { UI } from '../../shared/ui';
import { CompanyContext } from './company-context';

@Component({
  selector: 'app-company-exposure',
  imports: [RouterLink, FormsModule, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="filters card" style="margin-bottom: 1rem">
      <label class="field">
        As of
        <input type="date" [ngModel]="asOfDate()" (ngModelChange)="asOfDate.set($event ?? '')" />
      </label>
      <button type="button" class="btn btn-sm" (click)="asOfDate.set('')" [disabled]="!asOfDate()">Now</button>
      <div class="small muted" style="flex: 1; min-width: 240px">
        <span class="badge badge-fact">REPORTED</span> shares come straight from filed numbers (e.g. XBRL
        geographic segments). <span class="badge badge-estimated">ESTIMATED</span> shares are model estimates
        derived from filing text — treat them as approximate.
      </div>
    </div>

    <app-status [res]="res" what="exposures" />

    @if (data(); as d) {
      <h2 style="margin-top: 0">Exposures <span class="small muted">as of {{ d.asOf | utc }}</span></h2>
      @if (!d.exposures.length) {
        <div class="empty-box">No exposures recorded for this company as of this date.</div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Channel</th>
                <th class="num">Share</th>
                <th>Basis</th>
                <th>Confidence</th>
                <th>Method</th>
                <th>Supporting evidence</th>
                <th>Available</th>
              </tr>
            </thead>
            <tbody>
              @for (e of sorted(); track e.id) {
                <tr [id]="'exposure-' + e.id">
                  <td class="nowrap">
                    <span class="chip">{{ e.targetType | human }}</span> <strong>{{ e.targetCode }}</strong>
                  </td>
                  <td>{{ e.channel | human }}</td>
                  <td class="num">
                    @if (e.basis === 'ESTIMATED') {
                      <span title="Model estimate">≈ {{ e.share | pct }}</span>
                    } @else {
                      {{ e.share | pct }}
                    }
                  </td>
                  <td><app-basis-badge [basis]="e.basis" /></td>
                  <td>{{ e.confidence | human }}</td>
                  <td class="small">{{ e.method | human }}</td>
                  <td style="min-width: 280px">
                    @if (e.rationale) {
                      <div class="small">{{ e.rationale }}</div>
                    }
                    @if (e.passage; as p) {
                      <div class="quote">
                        @if (p.section) {
                          <strong>{{ p.section }}:</strong>
                        }
                        “{{ p.text | excerpt: 240 }}”
                      </div>
                    }
                    @if (e.fact; as f) {
                      <div class="small" style="margin-top: 0.25rem">
                        Fact <span class="mono">{{ f.concept }}</span> = {{ f.value | usd }}
                        <span class="chips" style="display: inline-flex">
                          @for (kv of dims(f.dimensions); track kv[0]) {
                            <span class="chip mono">{{ kv[0] }}: {{ kv[1] }}</span>
                          }
                        </span>
                      </div>
                    }
                    @if (e.filing; as fl) {
                      <div class="small" style="margin-top: 0.25rem">
                        <a [routerLink]="['/filings', fl.id]">{{ fl.formType }} {{ fl.accessionNo }}</a>
                        @if (fl.url) {
                          · <a [href]="fl.url" target="_blank" rel="noopener noreferrer">SEC ↗</a>
                        }
                      </div>
                    }
                  </td>
                  <td class="nowrap small">
                    {{ e.availableAt | utc }}
                    @if (e.periodEnd) {
                      <div class="muted">period end {{ e.periodEnd }}</div>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <h2>Event → target → company → evidence paths</h2>
      <p class="small muted">
        How each policy event reaches this company: the event names a target (country, sector, product or cost
        item), the company has a documented exposure to that target, and the exposure is supported by a filing
        passage or fact.
      </p>
      @if (!d.paths.length) {
        <div class="empty-box">No policy event currently connects to this company's exposures.</div>
      } @else {
        <div class="card">
          <ul class="plain">
            @for (p of d.paths; track $index) {
              <li class="path">
                <a [routerLink]="['/events', p.eventId]"><strong>{{ p.eventTitle }}</strong></a>
                <span class="chip">{{ p.eventCategory | human }}</span>
                <span class="small muted">{{ p.eventPublishedAt | utc }}</span>
                <span class="arrow" aria-hidden="true">→</span>
                <span class="chip">{{ p.targetType | human }}: {{ p.targetCode }}</span>
                <span class="arrow" aria-hidden="true">→</span>
                <span>
                  {{ ctx.detail()?.symbol }} share
                  {{ p.basis === 'ESTIMATED' ? '≈ ' : '' }}{{ p.share | pct }}
                  <app-basis-badge [basis]="p.basis" />
                  <span class="small muted">{{ p.confidence | human }} confidence</span>
                </span>
                <span class="arrow" aria-hidden="true">→</span>
                @if (byId().get(p.exposureId); as ex) {
                  @if (ex.filing; as fl) {
                    <a [routerLink]="['/filings', fl.id]" class="small">
                      {{ p.passageId ? 'passage #' + p.passageId + ' in ' : '' }}{{ fl.formType }} {{ p.filingAccessionNo ?? fl.accessionNo }}
                    </a>
                  } @else {
                    <a [routerLink]="[]" [fragment]="'exposure-' + p.exposureId" class="small">exposure #{{ p.exposureId }}</a>
                  }
                } @else {
                  <span class="small mono">{{ p.filingAccessionNo ?? 'exposure #' + p.exposureId }}</span>
                }
              </li>
            }
          </ul>
        </div>
      }
    }
  `,
})
export class CompanyExposure {
  protected readonly ctx = inject(CompanyContext);
  protected readonly asOfDate = signal('');

  protected readonly res = httpResource<ExposureResponse>(() => {
    const s = this.ctx.apiSymbol();
    if (!s) return undefined;
    return apiUrl.exposures(s, this.asOfDate() ? `${this.asOfDate()}T23:59:59Z` : null);
  });

  protected readonly data = computed(() => {
    const d = valueOf(this.res);
    return d ? { ...d, exposures: d.exposures ?? [], paths: d.paths ?? [] } : null;
  });

  protected readonly sorted = computed(() =>
    [...(this.data()?.exposures ?? [])].sort(
      (a, b) =>
        (a.basis === 'ESTIMATED' ? 1 : 0) - (b.basis === 'ESTIMATED' ? 1 : 0) || (b.share ?? 0) - (a.share ?? 0),
    ),
  );

  protected readonly byId = computed(() => new Map<number, Exposure>((this.data()?.exposures ?? []).map((e) => [e.id, e])));

  protected dims(d: Record<string, string> | null | undefined): [string, string][] {
    return d ? Object.entries(d) : [];
  }
}
