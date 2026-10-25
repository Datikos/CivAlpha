import { httpResource } from '@angular/common/http';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { EVENT_CATEGORIES, EventStream, MODEL_STREAMS, PolicyEvent, STREAM_LABELS } from '../core/models';
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

    @if (res.hasValue()) {
      <div class="alert alert-info small">
        <strong>{{ modelled() }}</strong> official events are read by the models@if (firstModelled(); as d) {<span>
            (from {{ d }})</span>}. <strong>{{ notModelled() }}</strong> are not: {{ newsOnly() }} news reports, unconfirmed
        until the official document arrives (their timestamps come from the feeds, and opinion and threats read like
        actions), and {{ otherStreams() }} trade documents of other streams (anti-dumping cases name a narrow product but
        would hit every company exposed to the country).
        @if (!historyLoaded()) {
          Only recent events are loaded: <a routerLink="/admin">Admin → Load official event history</a> adds the
          official ones since 2019 (ADR-0009).
        }
      </div>
    }

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
        Stream
        <select [ngModel]="stream()" (ngModelChange)="stream.set($event)">
          <option value="">Any</option>
          <option value="MODEL">Read by the models</option>
          <option value="NOT_MODEL">Not read by the models</option>
          @for (s of streams; track s) {
            <option [value]="s">{{ streamLabels[s] }}</option>
          }
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
                <th>Stream</th>
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
                    @if (e.stream; as st) {
                      <span
                        class="chip"
                        [class.tone-good]="inModels(st)"
                        [title]="inModels(st) ? 'Read by the models' : 'Stored, not read by the models'"
                        >{{ streamLabels[st] }}</span
                      >
                    } @else {
                      <span class="muted small" title="News-only events have no stream and are not read by the models">—</span>
                    }
                  </td>
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
  protected readonly stream = signal('');
  protected readonly streams = Object.keys(STREAM_LABELS) as EventStream[];
  protected readonly streamLabels = STREAM_LABELS;
  protected readonly q = signal('');
  protected readonly formOpen = signal(false);

  protected readonly res = httpResource<PolicyEvent[]>(() => apiUrl.events(this.category() || null));

  protected readonly rows = computed(() => {
    const q = this.q().trim().toLowerCase();
    const ev = this.evidence();
    return [...(valueOf(this.res) ?? [])]
      .filter((e) => !ev || e.evidenceStatus === ev)
      .filter((e) => this.streamMatches(e))
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

  private readonly all = computed(() => valueOf(this.res) ?? []);
  protected readonly modelled = computed(() => this.all().filter((e) => this.read(e)).length);
  protected readonly notModelled = computed(() => this.all().length - this.modelled());
  protected readonly newsOnly = computed(() => this.all().filter((e) => e.evidenceStatus !== 'OFFICIAL').length);
  protected readonly otherStreams = computed(() => this.notModelled() - this.newsOnly());
  protected readonly firstModelled = computed(
    () =>
      this.all()
        .filter((e) => this.read(e) && e.eventDate)
        .map((e) => e.eventDate as string)
        .sort()[0] ?? null,
  );
  /** The history is loaded once an official event older than 2026 exists (ADR-0009 loads 2019 onwards). */
  protected readonly historyLoaded = computed(() => (this.firstModelled() ?? '9999') < '2026-01-01');

  protected inModels(s: EventStream): boolean {
    return MODEL_STREAMS.includes(s);
  }

  private read(e: PolicyEvent): boolean {
    return e.evidenceStatus === 'OFFICIAL' && !!e.stream && this.inModels(e.stream);
  }

  private streamMatches(e: PolicyEvent): boolean {
    const f = this.stream();
    if (!f) return true;
    if (f === 'MODEL') return this.read(e);
    if (f === 'NOT_MODEL') return !this.read(e);
    return e.stream === f;
  }
}
