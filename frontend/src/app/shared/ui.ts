import { Component, computed, input } from '@angular/core';
import { errorMessage } from '../core/api';
import { fmtPct, fmtUtc, humanize } from '../core/format';
import { DividendSummary, ForecastSummary, ModelKind } from '../core/models';
import { leanOf } from './viz';

/** LIVE vs REPLAY issue mode. */
@Component({
  selector: 'app-issue-mode',
  template: `@if (mode() === 'REPLAY') {
      <span class="badge badge-replay" title="Replayed: published after the data cutoff, reconstructed point-in-time"
        >replayed: published after data cutoff</span
      >
    } @else if (mode()) {
      <span class="badge badge-live" title="Issued live, before the outcome window">{{ mode() }}</span>
    }`,
})
export class IssueModeBadge {
  readonly mode = input<string | null | undefined>(null);
}

/** DIRECTLY_REPORTED (recorded fact) vs ESTIMATED (model estimate). */
@Component({
  selector: 'app-basis-badge',
  template: `@if (basis() === 'ESTIMATED') {
      <span class="badge badge-estimated" title="Model estimate — not directly reported in a filing">ESTIMATED</span>
    } @else if (basis() === 'DIRECTLY_REPORTED') {
      <span class="badge badge-fact" title="Directly reported in an SEC filing">REPORTED</span>
    } @else if (basis()) {
      <span class="badge">{{ basis() }}</span>
    }`,
})
export class BasisBadge {
  readonly basis = input<string | null | undefined>(null);
}

/** OFFICIAL vs NEWS_ONLY evidence for events. */
@Component({
  selector: 'app-evidence-badge',
  template: `@if (status() === 'OFFICIAL') {
      <span class="badge badge-official" title="Backed by an official primary document">OFFICIAL</span>
    } @else if (status() === 'NEWS_ONLY') {
      <span class="badge badge-news" title="Only news reports so far; no official document yet">NEWS ONLY</span>
    } @else if (status()) {
      <span class="badge">{{ status() }}</span>
    }`,
})
export class EvidenceBadge {
  readonly status = input<string | null | undefined>(null);
}

/** Dividend payer status from the recorded cash dividends: frequency when regular. */
@Component({
  selector: 'app-dividend-badge',
  template: `@switch (d()?.status) {
      @case ('REGULAR') {
        <span class="badge badge-ok" [title]="'Pays ' + freq() + ' dividends; last ex-date ' + d()!.lastExDate">{{ freq() }}</span>
      }
      @case ('IRREGULAR') {
        <span class="badge" title="Paid a dividend in the last 12 months, but not on a steady schedule">irregular</span>
      }
      @case ('SUSPENDED') {
        <span class="badge badge-fail" [title]="'The next payment is overdue; last ex-date ' + d()!.lastExDate">suspended</span>
      }
      @case ('NONE') {
        <span class="muted small" title="No cash dividend recorded in the price history">none</span>
      }
      @default {
        <span class="muted">—</span>
      }
    }`,
})
export class DividendBadge {
  readonly d = input<DividendSummary | null | undefined>(null);
  protected readonly freq = computed(() => humanize(this.d()?.frequency).toLowerCase());
}

/** Model identity: a line key in the series color + name (text never wears the series color). */
@Component({
  selector: 'app-model-tag',
  template: `<span class="model-tag"
    ><span class="key-line" [style.background]="color()"></span>{{ label() }}</span
  >`,
})
export class ModelTag {
  readonly kind = input.required<ModelKind | string>();
  protected readonly color = computed(() => modelColor(this.kind()));
  protected readonly label = computed(() => humanize(this.kind()));
}

export function modelColor(kind: ModelKind | string): string {
  return kind === 'AUGMENTED' ? 'var(--series-2)' : kind === 'AI_BOOK_21' ? 'var(--series-3)' : 'var(--series-1)';
}

/** Minimal view of a resource for status display. */
export interface ResourceLike {
  error(): unknown;
  isLoading(): boolean;
  hasValue(): boolean;
}

/** Loading / error state for an httpResource (loading text only until a first value exists). */
@Component({
  selector: 'app-status',
  template: `@if (res().error()) {
      <div class="alert alert-error" role="alert">
        <strong>{{ what() ? 'Could not load ' + what() + '.' : 'Request failed.' }}</strong>
        {{ message() }}
        @if (retry()) {
          <button type="button" class="btn btn-sm" style="margin-left: 0.5rem" (click)="retry()!()">Retry</button>
        }
      </div>
    } @else if (res().isLoading() && !res().hasValue()) {
      <p class="loading" aria-live="polite">Loading{{ what() ? ' ' + what() : '' }}…</p>
    }`,
})
export class StatusMessage {
  readonly res = input.required<ResourceLike>();
  readonly what = input<string>('');
  readonly retry = input<(() => void) | null>(null);
  protected readonly message = computed(() => errorMessage(this.res().error()));
}

/** Tiny interval meter: track 0–100%, interval segment, point estimate. */
@Component({
  selector: 'app-interval-bar',
  template: `<svg
    class="interval-bar"
    [class]="'interval-bar tone-' + tone()"
    width="88"
    height="12"
    viewBox="0 0 88 12"
    role="img"
    [attr.aria-label]="aria()"
  >
    <line x1="2" x2="86" y1="6" y2="6" class="ib-track" />
    <line x1="44" x2="44" y1="2" y2="10" class="ib-mid" />
    @if (lo() !== null && hi() !== null) {
      <line [attr.x1]="x(lo()!)" [attr.x2]="x(hi()!)" y1="6" y2="6" class="ib-range" />
    }
    <circle [attr.cx]="x(p())" cy="6" r="4" class="ib-point" />
  </svg>`,
})
export class IntervalBar {
  readonly p = input.required<number>();
  readonly lo = input<number | null>(null);
  readonly hi = input<number | null>(null);
  protected x(v: number): number {
    return 2 + Math.max(0, Math.min(1, v)) * 84;
  }
  /** The bar is tinted by where the interval sits relative to 50%. */
  protected readonly tone = computed(() => {
    const l = leanOf(this.p(), this.lo(), this.hi());
    return l === 'above' ? 'good' : l === 'below' ? 'bad' : 'forecast';
  });
  protected readonly aria = computed(
    () =>
      `Probability ${fmtPct(this.p())}` +
      (this.lo() !== null ? `, interval ${fmtPct(this.lo())} to ${fmtPct(this.hi())}` : ''),
  );
}

/**
 * A forecast probability. Never shown bare: always with interval, and (unless the
 * surrounding table has dedicated columns) horizon and publication time.
 */
@Component({
  selector: 'app-forecast-prob',
  imports: [IntervalBar],
  template: `<span class="fprob">
    <span class="prob">{{ pct(f().probability) }}</span>
    <span class="prob-int" title="Uncertainty interval">
      [{{ pct(f().probLow) }}–{{ pct(f().probHigh) }}]
    </span>
    <app-interval-bar [p]="f().probability" [lo]="f().probLow" [hi]="f().probHigh" />
    @if (withContext()) {
      <span class="prob-ctx">
        {{ f().horizonTradingDays }} trading days · as of {{ f().asOfDate }} · published
        {{ utc(f().issuedAt) }}
      </span>
    }
  </span>`,
})
export class ForecastProb {
  readonly f = input.required<ForecastSummary>();
  readonly withContext = input(true);
  protected pct = fmtPct;
  protected utc = fmtUtc;
}

export const UI = [
  IssueModeBadge,
  BasisBadge,
  EvidenceBadge,
  ModelTag,
  StatusMessage,
  IntervalBar,
  ForecastProb,
  DividendBadge,
] as const;
