import { httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Column, ColumnChart } from '../../charts/column-chart';
import { apiUrl, valueOf } from '../../core/api';
import { FORMAT_PIPES, fmtByUnit } from '../../core/format';
import { FinancialPoint, FinancialSeries, Filing, Financials } from '../../core/models';
import { UI } from '../../shared/ui';
import { CompanyContext } from './company-context';

type Freq = 'Q' | 'FY';

interface SeriesView {
  s: FinancialSeries;
  points: FinancialPoint[];
  columns: Column[];
  revisedCount: number;
  fmt: (v: number) => string;
}

@Component({
  selector: 'app-company-filings',
  imports: [RouterLink, FormsModule, ColumnChart, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="filters card" style="margin-bottom: 1rem">
      <label class="field">
        Point-in-time view: as of
        <input type="date" [ngModel]="asOfDate()" (ngModelChange)="asOfDate.set($event ?? '')" />
      </label>
      <button type="button" class="btn btn-sm" (click)="asOfDate.set('')" [disabled]="!asOfDate()">Now</button>
      <div class="small muted" style="flex: 1; min-width: 220px">
        Shows only values filed on or before the chosen date (end of day UTC). Later restatements are hidden;
        values replaced by a later filing up to that date are marked <span class="badge badge-revised">REVISED</span>
        with the originally filed number.
      </div>
    </div>

    <h2 style="margin-top: 0">Financial trends</h2>
    <div class="filters">
      <div class="seg" role="group" aria-label="Period frequency">
        <button type="button" [class.on]="freq() === 'Q'" (click)="freq.set('Q')" [attr.aria-pressed]="freq() === 'Q'">Quarterly</button>
        <button type="button" [class.on]="freq() === 'FY'" (click)="freq.set('FY')" [attr.aria-pressed]="freq() === 'FY'">Annual</button>
      </div>
      @if (fin.hasValue()) {
        <span class="small muted">Financials as of {{ fin.value()?.asOf | utc }}</span>
      }
    </div>
    <app-status [res]="fin" what="financials" />
    @if (fin.hasValue()) {
      @if (!views().length) {
        <div class="empty-box">No {{ freq() === 'Q' ? 'quarterly' : 'annual' }} values filed as of this date.</div>
      }
      <div class="grid-2" [style.opacity]="fin.isLoading() ? 0.5 : 1">
        @for (v of views(); track v.s.concept) {
          <div class="card fact-block">
            <h3 style="margin-bottom: 0.1rem">
              {{ v.s.label || v.s.concept }}
              @if (v.revisedCount) {
                <span class="badge badge-revised">{{ v.revisedCount }} REVISED</span>
              }
            </h3>
            <div class="small muted mono" style="margin-bottom: 0.4rem">{{ v.s.concept }} · {{ v.s.unit }}</div>
            <app-column-chart [columns]="v.columns" [yFormat]="v.fmt" [label]="v.s.label + ' by period'" />
            <details class="chart-table">
              <summary>Values &amp; sources ({{ v.points.length }})</summary>
              <div class="table-wrap">
                <table class="table compact">
                  <thead>
                    <tr><th>Period</th><th class="num">Value</th><th>Form</th><th>Filed</th><th></th></tr>
                  </thead>
                  <tbody>
                    @for (p of v.points.slice().reverse(); track $index) {
                      <tr>
                        <td class="nowrap">{{ p.fiscalPeriod ?? '' }} {{ p.periodEnd }}</td>
                        <td class="num">
                          {{ p.value | unitValue: v.s.unit }}
                          @if (p.revised) {
                            <div class="small">
                              <span class="badge badge-revised">REVISED</span>
                              orig. {{ p.originalValue | unitValue: v.s.unit }}
                            </div>
                          }
                        </td>
                        <td>{{ p.formType ?? '—' }}</td>
                        <td class="nowrap">{{ p.filedDate ?? '—' }}</td>
                        <td>
                          @if (p.sourceUrl) {
                            <a [href]="p.sourceUrl" target="_blank" rel="noopener noreferrer">source ↗</a>
                          }
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        }
      </div>
    }

    <h2>Filings</h2>
    <app-status [res]="filings" what="filings" />
    @if (filings.hasValue()) {
      @if (!sortedFilings().length) {
        <div class="empty-box">No filings ingested for this company.</div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Form</th>
                <th>Period</th>
                <th>Filed</th>
                <th>Accepted (UTC)</th>
                <th>Accession</th>
                <th class="num">Passages</th>
                <th class="num">Facts</th>
                <th>Links</th>
              </tr>
            </thead>
            <tbody>
              @for (f of sortedFilings(); track f.id) {
                <tr [class.row-dim]="afterAsOf(f)">
                  <td class="nowrap">
                    <a [routerLink]="['/filings', f.id]"><strong>{{ f.formType }}</strong></a>
                    <app-demo-badge [show]="f.isDemo" />
                    @if (f.amendsAccession) {
                      <div class="small muted">amends {{ f.amendsAccession }}</div>
                    }
                    @if (f.items) {
                      <div class="small muted">items {{ f.items }}</div>
                    }
                  </td>
                  <td class="nowrap">{{ f.periodOfReport ?? '—' }}</td>
                  <td class="nowrap">
                    {{ f.filedDate ?? '—' }}
                    @if (afterAsOf(f)) {
                      <div class="small muted">after as-of date</div>
                    }
                  </td>
                  <td class="nowrap">{{ f.acceptedAt | utc }}</td>
                  <td class="mono">{{ f.accessionNo }}</td>
                  <td class="num">{{ f.passageCount ?? '—' }}</td>
                  <td class="num">{{ f.factCount ?? '—' }}</td>
                  <td class="nowrap">
                    <a [routerLink]="['/filings', f.id]">details</a>
                    @if (f.url) {
                      · <a [href]="f.url" target="_blank" rel="noopener noreferrer">SEC ↗</a>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    }
  `,
})
export class CompanyFilings {
  protected readonly ctx = inject(CompanyContext);
  protected readonly asOfDate = signal<string>('');
  protected readonly freq = signal<Freq>('Q');

  private readonly asOfIso = computed(() => (this.asOfDate() ? `${this.asOfDate()}T23:59:59Z` : null));

  protected readonly filings = httpResource<Filing[]>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.filings(s) : undefined;
  });

  protected readonly fin = httpResource<Financials>(() => {
    const s = this.ctx.apiSymbol();
    return s ? apiUrl.financials(s, this.asOfIso()) : undefined;
  });

  protected readonly sortedFilings = computed(() =>
    [...(valueOf(this.filings) ?? [])].sort(
      (a, b) => (b.filedDate ?? '').localeCompare(a.filedDate ?? '') || b.id - a.id,
    ),
  );

  protected afterAsOf(f: Filing): boolean {
    const d = this.asOfDate();
    return !!d && !!f.filedDate && f.filedDate > d;
  }

  protected readonly views = computed<SeriesView[]>(() => {
    const fin = valueOf(this.fin);
    if (!fin) return [];
    const freq = this.freq();
    return (fin.series ?? [])
      .map((s) => {
        const byEnd = new Map<string, FinancialPoint>();
        for (const p of s.points ?? []) {
          const isFy = (p.fiscalPeriod ?? '').toUpperCase() === 'FY';
          if ((freq === 'FY') !== isFy) continue;
          byEnd.set(p.periodEnd, p);
        }
        const points = [...byEnd.values()].sort((a, b) => a.periodEnd.localeCompare(b.periodEnd));
        const fmt = (v: number) => fmtByUnit(v, s.unit);
        const columns: Column[] = points.map((p) => ({
          key: p.periodEnd,
          label: freq === 'FY' ? `FY${p.periodEnd.slice(2, 4)}` : `${p.fiscalPeriod ?? ''} ${p.periodEnd.slice(2, 4)}`.trim(),
          value: p.value,
          originalValue: p.revised ? p.originalValue : null,
          detail: `period end ${p.periodEnd} · ${p.formType ?? ''} filed ${p.filedDate ?? '—'}`,
        }));
        return { s, points, columns, revisedCount: points.filter((p) => p.revised).length, fmt };
      })
      .filter((v) => v.points.length > 0);
  });
}
