import { httpResource } from '@angular/common/http';
import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { FORMAT_PIPES } from '../core/format';
import { ModelKind, PolicyEventDetail } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

@Component({
  selector: 'app-event-detail',
  imports: [RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="crumbs"><a routerLink="/events">Policy events</a> / #{{ id() }}</div>
    <app-status [res]="res" what="event" />
    @if (e(); as e) {
      <div class="page-head">
        <div class="page-title">
          <app-page-icon name="landmark" area="research" />
          <div>
            <h1>{{ e.title }}</h1>
            <p class="muted">
              {{ e.category | human }} · {{ e.eventType | human }}{{ e.actorName ? ' · ' + e.actorName : '' }} ·
              <app-evidence-badge [status]="e.evidenceStatus" />
            </p>
          </div>
        </div>
        <div class="page-actions">
          <a routerLink="/guide" fragment="page-events" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
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

      <h2>Forecast impact <app-help text="Two views of what this event did to the forecasts. Re-issued: forecasts published because the event arrived, each next to the version it replaced. Before and after: for every exposed company, the last forecast before the event date against the first one on or after it, whatever caused the re-issue." topic="event-impact" label="forecast impact" /></h2>
      @if (impact(); as im) {
        @if (!im.reissued.length && !im.shift.length) {
          <div class="empty-box">
            No forecast has moved on account of this event yet.
            @if (e.evidenceStatus !== 'OFFICIAL') { News-only events stay out of the models until an official document is linked. }
            @else if (!e.affectedCompanies.length) { No tracked company has a documented exposure to its targets. }
            @else { Forecasts dated after {{ e.eventDate }} will appear here once issued. }
          </div>
        } @else {
          <div class="stats wide">
            @for (s of im.summary; track s.model + s.source) {
              <div class="stat" [class]="'stat tone-' + (s.n ? (s.mean > 0 ? 'good' : s.mean < 0 ? 'bad' : 'neutral') : 'neutral')">
                <div class="stat-label"><app-model-tag [kind]="s.model" /> · {{ s.source }}</div>
                <div class="stat-value"><app-delta [value]="s.mean" kind="pp" [digits]="1" /><span class="unit">mean move</span></div>
                <div class="stat-sub">{{ s.n }} forecasts · {{ s.up }} up · {{ s.down }} down · {{ s.flat }} unchanged</div>
              </div>
            }
          </div>
          <div class="grid-2">
            @if (im.reissued.length) {
              <div class="card">
                <h3>Re-issued because of this event ({{ im.reissued.length }})</h3>
                <div class="table-wrap">
                  <table class="table compact">
                    <thead><tr><th>Company</th><th>Model</th><th class="num">Before</th><th class="num">After</th><th class="num">Move</th><th></th></tr></thead>
                    <tbody>
                      @for (r of im.reissued; track r.id) {
                        <tr>
                          <td><a [routerLink]="['/companies', r.symbol]">{{ r.symbol }}</a></td>
                          <td><app-model-tag [kind]="r.modelKind" /></td>
                          <td class="num">{{ r.previous?.probability | pct: 1 }}</td>
                          <td class="num"><span class="prob">{{ r.probability | pct: 1 }}</span></td>
                          <td class="num"><app-delta [value]="r.previous ? r.probability - r.previous.probability : null" kind="pp" [digits]="1" /></td>
                          <td class="small nowrap"><a [routerLink]="['/forecasts', r.id]">v{{ r.version }} · what changed</a></td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
              </div>
            }
            @if (im.shift.length) {
              <div class="card">
                <h3>Before and after the event date ({{ e.eventDate }})</h3>
                <div class="table-wrap">
                  <table class="table compact">
                    <thead><tr><th>Company</th><th>Model</th><th class="num">Before</th><th class="num">After</th><th class="num">Move</th></tr></thead>
                    <tbody>
                      @for (r of im.shift; track r.symbol + r.modelKind) {
                        <tr>
                          <td><a [routerLink]="['/companies', r.symbol]">{{ r.symbol }}</a></td>
                          <td><app-model-tag [kind]="r.modelKind" /></td>
                          <td class="num"><a [routerLink]="['/forecasts', r.before.id]">{{ r.before.probability | pct: 1 }}</a><div class="small muted">{{ r.before.asOfDate }}</div></td>
                          <td class="num"><a [routerLink]="['/forecasts', r.after.id]">{{ r.after.probability | pct: 1 }}</a><div class="small muted">{{ r.after.asOfDate }}</div></td>
                          <td class="num"><app-delta [value]="r.after.probability - r.before.probability" kind="pp" [digits]="1" /></td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
                <p class="small muted" style="margin-top: 0.4rem">Includes everything that happened between the two dates, not only this event.</p>
              </div>
            }
          </div>
        }
      }

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

  protected readonly impact = computed(() => {
    const e = this.e();
    if (!e) return null;
    const reissued = e.reissuedForecasts ?? [];
    const shift = e.forecastShift ?? [];
    const summary: { model: ModelKind; source: string; n: number; mean: number; up: number; down: number; flat: number }[] = [];
    const add = (model: ModelKind, source: string, moves: number[]) => {
      if (!moves.length) return;
      summary.push({
        model,
        source,
        n: moves.length,
        mean: moves.reduce((s, v) => s + v, 0) / moves.length,
        up: moves.filter((v) => v > 0.0005).length,
        down: moves.filter((v) => v < -0.0005).length,
        flat: moves.filter((v) => Math.abs(v) <= 0.0005).length,
      });
    };
    for (const model of ['BASELINE', 'AUGMENTED'] as ModelKind[]) {
      add(model, 're-issued', reissued.filter((r) => r.modelKind === model && r.previous).map((r) => r.probability - r.previous!.probability));
    }
    if (!reissued.length) {
      for (const model of ['BASELINE', 'AUGMENTED'] as ModelKind[]) {
        add(model, 'before → after', shift.filter((r) => r.modelKind === model).map((r) => r.after.probability - r.before.probability));
      }
    }
    return { reissued, shift, summary };
  });

  protected readonly attrs = computed(() =>
    Object.entries(this.e()?.attributes ?? {}).map(
      ([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)] as [string, string],
    ),
  );
}
