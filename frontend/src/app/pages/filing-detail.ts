import { httpResource } from '@angular/common/http';
import { Component, computed, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { FilingDetail } from '../core/models';
import { UI } from '../shared/ui';

@Component({
  selector: 'app-filing-detail',
  imports: [RouterLink, FormsModule, ...UI, ...FORMAT_PIPES],
  template: `
    <app-status [res]="res" what="filing" />
    @if (f(); as f) {
      <div class="crumbs">
        <a routerLink="/companies">Companies</a> /
        <a [routerLink]="['/companies', f.companySymbol]">{{ f.companySymbol }}</a> /
        <a [routerLink]="['/companies', f.companySymbol, 'filings']">Filings</a> / {{ f.formType }}
      </div>
      <div class="page-head">
        <div>
          <h1>{{ f.companySymbol }} {{ f.formType }} <span class="muted">· {{ f.periodOfReport ?? '' }}</span></h1>
          <p class="muted mono">{{ f.accessionNo }}</p>
        </div>
        @if (f.url) {
          <a class="btn" [href]="f.url" target="_blank" rel="noopener noreferrer">Open on SEC EDGAR ↗</a>
        }
      </div>

      <div class="card fact-block">
        <dl class="kv">
          <dt>Form</dt><dd>{{ f.formType }}</dd>
          <dt>Period of report</dt><dd>{{ f.periodOfReport ?? '—' }}</dd>
          <dt>Filed</dt><dd>{{ f.filedDate ?? '—' }}</dd>
          <dt>Accepted</dt><dd>{{ f.acceptedAt | utc }}</dd>
          @if (f.items) {
            <dt>Items</dt><dd>{{ f.items }}</dd>
          }
          @if (f.amendsAccession) {
            <dt>Amends</dt><dd class="mono">{{ f.amendsAccession }}</dd>
          }
          <dt>Passages / facts</dt><dd>{{ f.passages.length }} / {{ f.facts.length }}</dd>
        </dl>
      </div>

      <h2>Extracted passages</h2>
      <p class="small muted">Text passages relevant to policy exposure, with the extraction method that found them.</p>
      @if (!f.passages.length) {
        <div class="empty-box">No passages extracted from this filing.</div>
      } @else {
        <div class="filters">
          <div class="seg" role="group" aria-label="Topic">
            <button type="button" [class.on]="topic() === ''" (click)="topic.set('')">All</button>
            @for (t of topics(); track t) {
              <button type="button" [class.on]="topic() === t" (click)="topic.set(t)">{{ t | human }}</button>
            }
          </div>
        </div>
        <div class="card">
          @for (p of passages(); track p.id) {
            <div class="passage" [id]="'passage-' + p.id">
              <div class="chips" style="margin-bottom: 0.3rem">
                <strong>{{ p.section ?? 'Unlabelled section' }}</strong>
                @if (p.topic) {
                  <span class="chip">{{ p.topic | human }}</span>
                }
                <span class="chip" title="Extraction method">{{ p.extractionMethod | human }}</span>
                @if (p.extractorVersion) {
                  <span class="chip mono">{{ p.extractorVersion }}</span>
                }
                <span class="small muted">#{{ p.id }}</span>
              </div>
              <div class="passage-text">{{ p.text }}</div>
            </div>
          }
        </div>
      }

      <h2>Facts ({{ f.facts.length }})</h2>
      @if (!f.facts.length) {
        <div class="empty-box">No XBRL facts recorded for this filing.</div>
      } @else {
        <div class="filters">
          <label class="field">
            Filter concept
            <input type="search" [ngModel]="q()" (ngModelChange)="q.set($event)" placeholder="e.g. Revenue" />
          </label>
          <label class="field" style="flex-direction: row; align-items: center; gap: 0.4rem">
            <input type="checkbox" [ngModel]="dimOnly()" (ngModelChange)="dimOnly.set($event)" /> Only dimensional facts
          </label>
          <span class="small muted">{{ facts().length }} shown</span>
        </div>
        <div class="table-wrap" style="max-height: 560px">
          <table class="table compact">
            <thead>
              <tr><th>Concept</th><th class="num">Value</th><th>Unit</th><th>Period end</th><th>Dimensions</th></tr>
            </thead>
            <tbody>
              @for (x of facts(); track $index) {
                <tr>
                  <td class="mono">{{ x.concept }}</td>
                  <td class="num">{{ x.value | unitValue: x.unit }}</td>
                  <td class="small">{{ x.unit ?? '—' }}</td>
                  <td class="nowrap">{{ x.periodEnd ?? '—' }}</td>
                  <td>
                    <div class="chips">
                      @for (kv of dims(x.dimensions); track kv[0]) {
                        <span class="chip mono">{{ kv[0] }} = {{ kv[1] }}</span>
                      } @empty {
                        <span class="muted small">—</span>
                      }
                    </div>
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
export class FilingDetailPage {
  readonly id = input.required<string>();
  protected readonly topic = signal('');
  protected readonly q = signal('');
  protected readonly dimOnly = signal(false);

  protected readonly res = httpResource<FilingDetail>(() => apiUrl.filing(this.id()));
  protected readonly f = computed(() => {
    const v = valueOf(this.res);
    return v ? { ...v, passages: v.passages ?? [], facts: v.facts ?? [] } : null;
  });

  protected readonly topics = computed(() => [
    ...new Set((this.f()?.passages ?? []).map((p) => p.topic).filter((t): t is string => !!t)),
  ]);
  protected readonly passages = computed(() =>
    (this.f()?.passages ?? []).filter((p) => !this.topic() || p.topic === this.topic()),
  );
  protected readonly facts = computed(() => {
    const q = this.q().trim().toLowerCase();
    return (this.f()?.facts ?? []).filter(
      (x) =>
        (!q || x.concept.toLowerCase().includes(q)) &&
        (!this.dimOnly() || (x.dimensions && Object.keys(x.dimensions).length > 0)),
    );
  });

  protected dims(d: Record<string, string> | null | undefined): [string, string][] {
    return d ? Object.entries(d) : [];
  }
}
