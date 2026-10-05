import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { EVENT_CATEGORIES, PolicyEvent } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';
import { EventForm } from './event-form';

@Component({
  selector: 'app-events',
  imports: [RouterLink, FormsModule, EventForm, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="landmark" area="research" />
        <div>
          <h1>Political &amp; policy events</h1>
          <p class="muted">US monetary policy and trade / tariff actions, each backed by stored source documents.</p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-events" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <details class="collapsible" [open]="formOpen()" (toggle)="formOpen.set($any($event.target).open)">
      <summary>Add sourced event</summary>
      @if (formOpen()) {
        <app-event-form (created)="res.reload()" />
      }
    </details>

    <div class="filters">
      <label class="field">
        Category
        <select [ngModel]="category()" (ngModelChange)="category.set($event)">
          <option value="">All categories</option>
          @for (c of categories; track c) {
            <option [value]="c">{{ c | human }}</option>
          }
        </select>
      </label>
      <label class="field">
        Evidence
        <select [ngModel]="evidence()" (ngModelChange)="evidence.set($event)">
          <option value="">Any</option>
          <option value="OFFICIAL">Official</option>
          <option value="NEWS_ONLY">News only</option>
        </select>
      </label>
      <label class="field">
        Search
        <input type="search" [ngModel]="q()" (ngModelChange)="q.set($event)" placeholder="Title, actor, target" />
      </label>
      @if (res.hasValue()) {
        <span class="small muted">{{ rows().length }} events</span>
      }
    </div>

    <app-status [res]="res" what="events" />
    @if (res.hasValue()) {
      @if (!rows().length) {
        <div class="empty-box">No events match the current filters.</div>
      } @else {
        <div class="table-wrap" [style.opacity]="res.isLoading() ? 0.5 : 1">
          <table class="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Event</th>
                <th>Category</th>
                <th>Evidence</th>
                <th>Targets</th>
                <th class="num">Sources</th>
                <th class="num">Companies</th>
                <th>Published / first seen</th>
              </tr>
            </thead>
            <tbody>
              @for (e of rows(); track e.id) {
                <tr>
                  <td class="nowrap">{{ e.eventDate ?? '—' }}</td>
                  <td style="min-width: 260px">
                    <a [routerLink]="['/events', e.id]"><strong>{{ e.title }}</strong></a>
                    <div class="small muted">
                      {{ e.eventType | human }}{{ e.actorName ? ' · ' + e.actorName : '' }}
                      @if (e.version > 1) {
                        · v{{ e.version }}
                      }
                    </div>
                  </td>
                  <td class="nowrap">{{ e.category | human }}</td>
                  <td><app-evidence-badge [status]="e.evidenceStatus" /></td>
                  <td>
                    <div class="chips">
                      @for (t of e.targets; track $index) {
                        <span class="chip"
                          >{{ t.targetType | human }}: {{ t.targetCode
                          }}{{ t.magnitude !== null && t.magnitude !== undefined ? ' (' + t.magnitude + ')' : '' }}</span
                        >
                      } @empty {
                        <span class="muted small">—</span>
                      }
                    </div>
                  </td>
                  <td class="num">{{ e.sourceCount }}</td>
                  <td class="num">{{ e.affectedCompanyCount }}</td>
                  <td class="small nowrap">
                    {{ e.publishedAt | utc }}
                    <div class="muted">seen {{ e.firstSeenAt | utc }}</div>
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
export class EventsPage {
  protected readonly categories = EVENT_CATEGORIES;
  protected readonly category = signal('');
  protected readonly evidence = signal('');
  protected readonly q = signal('');
  protected readonly formOpen = signal(false);

  protected readonly res = httpResource<PolicyEvent[]>(() => apiUrl.events(this.category() || null));

  protected readonly rows = computed(() => {
    const q = this.q().trim().toLowerCase();
    const ev = this.evidence();
    return [...(valueOf(this.res) ?? [])]
      .filter((e) => !ev || e.evidenceStatus === ev)
      .filter(
        (e) =>
          !q ||
          e.title.toLowerCase().includes(q) ||
          (e.actorName ?? '').toLowerCase().includes(q) ||
          (e.targets ?? []).some((t) => t.targetCode.toLowerCase().includes(q)),
      )
      .map((e) => ({ ...e, targets: e.targets ?? [] }))
      .sort(
        (a, b) =>
          (b.eventDate ?? '').localeCompare(a.eventDate ?? '') ||
          (b.publishedAt ?? '').localeCompare(a.publishedAt ?? ''),
      );
  });
}
