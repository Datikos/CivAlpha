import { httpResource } from '@angular/common/http';
import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { PolicyEventDetail } from '../core/models';
import { UI } from '../shared/ui';

@Component({
  selector: 'app-event-detail',
  imports: [RouterLink, ...UI, ...FORMAT_PIPES],
  template: `
    <div class="crumbs"><a routerLink="/events">Policy events</a> / #{{ id() }}</div>
    <app-status [res]="res" what="event" />
    @if (e(); as e) {
      <div class="page-head">
        <div>
          <h1>{{ e.title }}</h1>
          <p class="muted">
            {{ e.category | human }} · {{ e.eventType | human }}{{ e.actorName ? ' · ' + e.actorName : '' }} ·
            <app-evidence-badge [status]="e.evidenceStatus" />
          </p>
        </div>
      </div>

      <div class="grid-2">
        <div class="card fact-block">
          <h3>Summary</h3>
          <p>{{ e.summary || 'No summary provided.' }}</p>
          <dl class="kv">
            <dt>Event date</dt><dd>{{ e.eventDate ?? '—' }}</dd>
            <dt>Published</dt><dd>{{ e.publishedAt | utc }}</dd>
            <dt>First seen</dt><dd>{{ e.firstSeenAt | utc }}</dd>
            <dt>Record version</dt><dd>v{{ e.version }}</dd>
            <dt>Targets</dt>
            <dd>
              <div class="chips">
                @for (t of e.targets; track $index) {
                  <span class="chip"
                    >{{ t.targetType | human }}: {{ t.targetCode
                    }}{{ t.magnitude !== null && t.magnitude !== undefined ? ' (' + t.magnitude + ')' : '' }}</span
                  >
                } @empty {
                  —
                }
              </div>
            </dd>
          </dl>
        </div>
        <div class="card">
          <h3>Attributes</h3>
          @if (attrs().length) {
            <dl class="kv">
              @for (a of attrs(); track a[0]) {
                <dt class="mono">{{ a[0] }}</dt>
                <dd>{{ a[1] }}</dd>
              }
            </dl>
          } @else {
            <p class="muted">No attributes.</p>
          }
        </div>
      </div>

      @if (e.actor; as a) {
        <h2>Actor: {{ a.name }}</h2>
        <div class="card">
          <p class="note">
            Profile lists documented statements, votes, authority and actions only; it does not infer intelligence,
            private thoughts or motives.
          </p>
          <dl class="kv" style="margin-bottom: 0.75rem">
            <dt>Type</dt><dd>{{ a.actorType | human }}</dd>
            <dt>Authority</dt><dd>{{ a.authority || '—' }}</dd>
            <dt>Affiliation</dt><dd>{{ a.affiliation || '—' }}</dd>
            @if (a.profileNote) {
              <dt>Note</dt><dd>{{ a.profileNote }}</dd>
            }
          </dl>
          <h3>Public decision record</h3>
          @if (a.records?.length) {
            <div class="table-wrap">
              <table class="table compact">
                <thead><tr><th>Date</th><th>Type</th><th>Record</th><th>Source</th></tr></thead>
                <tbody>
                  @for (r of a.records; track $index) {
                    <tr>
                      <td class="nowrap">{{ r.occurredAt | utc }}</td>
                      <td><span class="chip">{{ r.recordType | human }}</span></td>
                      <td>{{ r.summary }}</td>
                      <td>
                        @if (r.source?.url) {
                          <a [href]="r.source!.url" target="_blank" rel="noopener noreferrer">{{ r.source!.title || 'source' }} ↗</a>
                        } @else {
                          <span class="muted">—</span>
                        }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          } @else {
            <p class="muted">No documented records.</p>
          }
        </div>
      }

      <h2>Sources ({{ e.sources.length }})</h2>
      @if (!e.sources.length) {
        <div class="empty-box">No sources attached.</div>
      } @else {
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Role / type</th>
                <th>Publisher</th>
                <th>Published</th>
                <th>Ingested</th>
                <th>Ver.</th>
                <th>Stored copy</th>
              </tr>
            </thead>
            <tbody>
              @for (s of e.sources; track s.id) {
                <tr>
                  <td style="min-width: 220px">
                    @if (s.url) {
                      <a [href]="s.url" target="_blank" rel="noopener noreferrer">{{ s.title || s.url }} ↗</a>
                    } @else {
                      {{ s.title || '—' }}
                    }
                    @if (s.accessionNo) {
                      <div class="small mono">{{ s.accessionNo }}</div>
                    }
                    @if (s.url) {
                      <div class="small muted mono">{{ s.url }}</div>
                    }
                  </td>
                  <td class="small">
                    {{ s.role | human }}
                    <div class="muted">{{ s.sourceType | human }}</div>
                  </td>
                  <td>{{ s.publisher || '—' }}</td>
                  <td class="nowrap small">{{ s.publishedAt | utc }}</td>
                  <td class="nowrap small">{{ s.ingestedAt | utc }}</td>
                  <td class="num">{{ s.version ?? '—' }}</td>
                  <td class="nowrap small">
                    @if (s.documentUrl) {
                      <a [href]="s.documentUrl" target="_blank" rel="noopener">stored copy</a>
                    }
                    @if (s.contentSha256) {
                      <div class="muted mono" [title]="'SHA-256 ' + s.contentSha256">sha256 {{ s.contentSha256 | hash: 10 }}</div>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <h2>Affected companies ({{ e.affectedCompanies.length }})</h2>
      @if (!e.affectedCompanies.length) {
        <div class="empty-box">No company in the universe has a documented exposure to this event's targets.</div>
      } @else {
        <div class="card">
          <ul class="plain">
            @for (c of e.affectedCompanies; track c.symbol) {
              <li>
                <div>
                  <a [routerLink]="['/companies', c.symbol]"><strong>{{ c.symbol }}</strong></a> {{ c.name }}
                  · <a [routerLink]="['/companies', c.symbol, 'exposure']" class="small">exposure</a>
                </div>
                @for (p of c.paths; track $index) {
                  <div class="path small" style="margin-top: 0.25rem">
                    <span class="muted">event</span>
                    <span class="arrow" aria-hidden="true">→</span>
                    <span class="chip">{{ p.targetType | human }}: {{ p.targetCode }}</span>
                    <span class="arrow" aria-hidden="true">→</span>
                    <span>share {{ p.basis === 'ESTIMATED' ? '≈ ' : '' }}{{ p.share | pct }}</span>
                    <app-basis-badge [basis]="p.basis" />
                    <span class="muted">{{ p.confidence | human }} confidence</span>
                    <span class="arrow" aria-hidden="true">→</span>
                    <a [routerLink]="['/companies', c.symbol, 'exposure']" [fragment]="'exposure-' + p.exposureId">
                      {{ p.passageId ? 'passage #' + p.passageId : 'exposure #' + p.exposureId }}
                    </a>
                  </div>
                }
              </li>
            }
          </ul>
        </div>
      }
    }
  `,
})
export class EventDetailPage {
  readonly id = input.required<string>();
  protected readonly res = httpResource<PolicyEventDetail>(() => apiUrl.event(this.id()));

  protected readonly e = computed(() => {
    const v = valueOf(this.res);
    return v
      ? {
          ...v,
          targets: v.targets ?? [],
          sources: v.sources ?? [],
          affectedCompanies: (v.affectedCompanies ?? []).map((c) => ({ ...c, paths: c.paths ?? [] })),
        }
      : null;
  });

  protected readonly attrs = computed(() =>
    Object.entries(this.e()?.attributes ?? {}).map(
      ([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)] as [string, string],
    ),
  );
}
