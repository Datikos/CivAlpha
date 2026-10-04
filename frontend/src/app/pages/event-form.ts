import { Component, inject, output, signal } from '@angular/core';
import { FormsModule, NgForm } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, errorMessage } from '../core/api';
import { EVENT_CATEGORIES, NewEventRequest, NewEventResponse, PolicyEventTarget } from '../core/models';
import { FORMAT_PIPES } from '../core/format';

interface TargetRow {
  targetType: string;
  targetCode: string;
  magnitude: number | null;
}

const EVENT_TYPES: Record<string, string[]> = {
  TRADE_TARIFF: [
    'TARIFF_IMPOSED',
    'TARIFF_INCREASED',
    'TARIFF_REDUCED',
    'TARIFF_REMOVED',
    'TARIFF_PAUSED',
    'EXPORT_CONTROL',
    'TRADE_AGREEMENT',
  ],
  MONETARY_POLICY: ['RATE_HIKE', 'RATE_CUT', 'RATE_HOLD', 'QT_CHANGE', 'QE_CHANGE', 'FORWARD_GUIDANCE'],
};

/** "Add sourced event" form. A source URL is mandatory. */
@Component({
  selector: 'app-event-form',
  imports: [FormsModule, RouterLink, ...FORMAT_PIPES],
  template: `
    <form #f="ngForm" (ngSubmit)="submit(f)" novalidate>
      <p class="small muted">
        Every event must cite a source document. Prefer the official primary source (Federal Register, USTR,
        Federal Reserve press release); news-only events are marked as such.
      </p>
      <div class="form-grid">
        <label class="field">
          <span class="req">Category</span>
          <select name="category" [(ngModel)]="m.category" required>
            @for (c of categories; track c) {
              <option [value]="c">{{ c | human }}</option>
            }
          </select>
        </label>
        <label class="field">
          <span class="req">Event type</span>
          <input name="eventType" [(ngModel)]="m.eventType" required list="event-types" placeholder="TARIFF_IMPOSED" />
          <datalist id="event-types">
            @for (t of types(m.category); track t) {
              <option [value]="t"></option>
            }
          </datalist>
        </label>
        <label class="field">
          <span class="req">Event date</span>
          <input type="date" name="eventDate" [(ngModel)]="m.eventDate" required />
        </label>
        <label class="field">
          <span class="req">Published at (UTC)</span>
          <input type="datetime-local" name="publishedAt" [(ngModel)]="m.publishedAt" required />
        </label>
        <label class="field">
          <span class="req">Actor</span>
          <input name="actorName" [(ngModel)]="m.actorName" required placeholder="USTR, FOMC…" />
        </label>
        <label class="field wide">
          <span class="req">Title</span>
          <input name="title" [(ngModel)]="m.title" required maxlength="300" />
        </label>
        <label class="field wide">
          Summary
          <textarea name="summary" [(ngModel)]="m.summary" rows="3"></textarea>
        </label>
      </div>

      <h3 style="margin-top: 1rem">Targets</h3>
      <div class="table-wrap">
        <table class="table compact">
          <thead><tr><th>Type</th><th>Code</th><th>Magnitude</th><th></th></tr></thead>
          <tbody>
            @for (t of targets(); track $index; let i = $index) {
              <tr>
                <td>
                  <select [name]="'tt' + i" [(ngModel)]="t.targetType">
                    @for (tt of targetTypes; track tt) {
                      <option [value]="tt">{{ tt | human }}</option>
                    }
                  </select>
                </td>
                <td><input [name]="'tc' + i" [(ngModel)]="t.targetCode" placeholder="CN" style="width: 9rem" /></td>
                <td><input type="number" step="any" [name]="'tm' + i" [(ngModel)]="t.magnitude" style="width: 7rem" /></td>
                <td><button type="button" class="btn btn-sm" (click)="removeTarget(i)" aria-label="Remove target">Remove</button></td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <button type="button" class="btn btn-sm" style="margin-top: 0.5rem" (click)="addTarget()">+ Add target</button>

      <div class="form-grid" style="margin-top: 1rem">
        <label class="field wide">
          Attributes (JSON object)
          <textarea name="attributes" [(ngModel)]="m.attributes" rows="2" class="mono"></textarea>
        </label>
      </div>

      <h3 style="margin-top: 1rem">Source</h3>
      <div class="form-grid">
        <label class="field wide">
          <span class="req">Source URL</span>
          <input
            type="url"
            name="sourceUrl"
            [(ngModel)]="m.sourceUrl"
            required
            pattern="https?://.+"
            placeholder="https://www.federalregister.gov/…"
            #srcUrl="ngModel"
          />
          @if (srcUrl.invalid && (srcUrl.touched || f.submitted)) {
            <span class="neg small">A source URL (http/https) is required.</span>
          }
        </label>
        <label class="field">
          Source title
          <input name="sourceTitle" [(ngModel)]="m.sourceTitle" />
        </label>
        <label class="field">
          Publisher
          <input name="publisher" [(ngModel)]="m.publisher" placeholder="Federal Register" />
        </label>
        <label class="field">
          Role
          <select name="role" [(ngModel)]="m.role">
            @for (r of roles; track r) {
              <option [value]="r">{{ r | human }}</option>
            }
          </select>
        </label>
      </div>

      @if (formError()) {
        <div class="alert alert-error" role="alert">{{ formError() }}</div>
      }
      @if (result(); as r) {
        <div class="alert alert-ok" role="status">
          {{ r.deduplicated ? 'Matched an existing event (deduplicated); the source was attached to it.' : 'Event added.' }}
          <a [routerLink]="['/events', r.event.id]">Open event #{{ r.event.id }}</a>.
          @if (r.reissuedForecastIds.length) {
            Re-issued forecasts:
            @for (id of r.reissuedForecastIds; track id; let last = $last) {
              <a [routerLink]="['/forecasts', id]">#{{ id }}</a>{{ last ? '' : ', ' }}
            }
          } @else {
            No forecasts were re-issued.
          }
        </div>
      }
      <div style="margin-top: 1rem; display: flex; gap: 0.5rem">
        <button type="submit" class="btn btn-primary" [disabled]="busy()">{{ busy() ? 'Submitting…' : 'Add event' }}</button>
        <button type="button" class="btn" (click)="reset(f)" [disabled]="busy()">Reset</button>
      </div>
    </form>
  `,
})
export class EventForm {
  private readonly api = inject(ApiService);
  readonly created = output<NewEventResponse>();

  protected readonly categories = EVENT_CATEGORIES;
  protected readonly targetTypes = ['COUNTRY', 'SECTOR', 'PRODUCT', 'COST', 'RATE'];
  protected readonly roles = ['OFFICIAL_PRIMARY', 'OFFICIAL_SECONDARY', 'NEWS'];

  protected m = this.blank();
  protected readonly targets = signal<TargetRow[]>([{ targetType: 'COUNTRY', targetCode: '', magnitude: null }]);
  protected readonly busy = signal(false);
  protected readonly formError = signal<string | null>(null);
  protected readonly result = signal<NewEventResponse | null>(null);

  private blank() {
    const now = new Date();
    return {
      category: 'TRADE_TARIFF',
      eventType: '',
      title: '',
      summary: '',
      eventDate: now.toISOString().slice(0, 10),
      publishedAt: now.toISOString().slice(0, 16),
      actorName: '',
      attributes: '{"severity": 0.5}',
      sourceUrl: '',
      sourceTitle: '',
      publisher: '',
      role: 'OFFICIAL_PRIMARY',
    };
  }

  protected types(cat: string): string[] {
    return EVENT_TYPES[cat] ?? [];
  }

  protected addTarget(): void {
    this.targets.update((t) => [...t, { targetType: 'COUNTRY', targetCode: '', magnitude: null }]);
  }

  protected removeTarget(i: number): void {
    this.targets.update((t) => t.filter((_, j) => j !== i));
  }

  protected reset(f: NgForm): void {
    this.m = this.blank();
    f.resetForm(this.m);
    this.targets.set([{ targetType: 'COUNTRY', targetCode: '', magnitude: null }]);
    this.formError.set(null);
    this.result.set(null);
  }

  protected submit(f: NgForm): void {
    this.formError.set(null);
    this.result.set(null);
    if (f.invalid) {
      this.formError.set('Please fill in all required fields, including a valid source URL.');
      return;
    }
    let attributes: Record<string, unknown> = {};
    if (this.m.attributes.trim()) {
      try {
        const parsed: unknown = JSON.parse(this.m.attributes);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object');
        attributes = parsed as Record<string, unknown>;
      } catch {
        this.formError.set('Attributes must be a JSON object, e.g. {"severity": 0.7, "tariff_rate_pct": 25}.');
        return;
      }
    }
    const targets: PolicyEventTarget[] = this.targets()
      .filter((t) => t.targetCode.trim())
      .map((t) => ({
        targetType: t.targetType,
        targetCode: t.targetCode.trim().toUpperCase(),
        magnitude: t.magnitude === null || (t.magnitude as unknown) === '' ? null : Number(t.magnitude),
      }));
    const pub = this.m.publishedAt.length === 16 ? `${this.m.publishedAt}:00Z` : `${this.m.publishedAt}Z`;
    const body: NewEventRequest = {
      category: this.m.category,
      eventType: this.m.eventType.trim().toUpperCase(),
      title: this.m.title.trim(),
      summary: this.m.summary.trim(),
      eventDate: this.m.eventDate,
      publishedAt: pub,
      actorName: this.m.actorName.trim(),
      attributes,
      targets,
      source: {
        url: this.m.sourceUrl.trim(),
        title: this.m.sourceTitle.trim() || this.m.title.trim(),
        publisher: this.m.publisher.trim() || this.m.actorName.trim(),
        role: this.m.role,
      },
    };
    this.busy.set(true);
    this.api.createEvent(body).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.result.set({ ...r, reissuedForecastIds: r.reissuedForecastIds ?? [] });
        this.created.emit(r);
      },
      error: (e) => {
        this.busy.set(false);
        this.formError.set(errorMessage(e));
      },
    });
  }
}
