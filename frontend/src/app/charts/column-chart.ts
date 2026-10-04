import { Component, computed, input, signal } from '@angular/core';
import { clamp, columnPath, hostWidth, niceTicks } from './chart-utils';

export interface Column {
  key: string;
  /** short x label, e.g. "Q3 24" */
  label: string;
  value: number;
  /** first-filed value when a later filing revised it */
  originalValue?: number | null;
  detail?: string;
}

/**
 * Single-series column chart (one color, slot 1). Columns <= 24px with a rounded data-end,
 * square at the zero baseline; handles negative values. Revised values show a hairline tick
 * at the originally filed level. Per-column hover/focus tooltip and a data-table twin.
 */
@Component({
  selector: 'app-column-chart',
  template: `
    <figure class="chart" [attr.aria-label]="label()">
      @if (!columns().length) {
        <p class="empty">No values.</p>
      } @else {
        @if (hasRevised()) {
          <div class="legend" aria-hidden="true">
            <span class="legend-item"><span class="key-rect" [style.background]="color()"></span>As currently reported</span>
            <span class="legend-item"><span class="key-line key-orig"></span>Originally filed (revised later)</span>
          </div>
        }
        <div class="chart-plot" (pointerleave)="hover.set(null)">
          <svg
            [attr.width]="w()"
            [attr.height]="height()"
            role="img"
            [attr.aria-label]="label() + '. Use left and right arrow keys to read values.'"
            tabindex="0"
            (keydown)="onKey($event)"
            (focus)="hover() === null && hover.set(columns().length - 1)"
            (blur)="hover.set(null)"
          >
            @for (t of scale().ticks; track t) {
              <line class="grid" [attr.x1]="m.left" [attr.x2]="w() - m.right" [attr.y1]="y(t)" [attr.y2]="y(t)" />
              <text class="tick" [attr.x]="m.left - 8" [attr.y]="y(t)" dy="0.32em" text-anchor="end">{{ yFormat()(t) }}</text>
            }
            @for (b of bars(); track b.key; let i = $index) {
              <rect
                class="hit"
                [attr.x]="b.bandX"
                [attr.y]="m.top"
                [attr.width]="b.bandW"
                [attr.height]="height() - m.top - m.bottom"
                (pointerenter)="hover.set(i)"
              />
              <path class="col" [class.col-active]="hover() === i" [attr.d]="b.d" [attr.fill]="color()" />
              @if (b.origY !== null) {
                <line class="orig-tick" [attr.x1]="b.x - 3" [attr.x2]="b.x + b.bw + 3" [attr.y1]="b.origY" [attr.y2]="b.origY" />
              }
              @if (b.showLabel) {
                <text class="tick" [attr.x]="b.x + b.bw / 2" [attr.y]="height() - m.bottom + 16" text-anchor="middle">
                  {{ b.label }}
                </text>
              }
            }
            <line class="axis" [attr.x1]="m.left" [attr.x2]="w() - m.right" [attr.y1]="y(0)" [attr.y2]="y(0)" />
          </svg>
          @if (hoverInfo(); as h) {
            <div class="tooltip" [style.left.px]="h.left" [style.top.px]="m.top">
              <div class="tooltip-title">{{ h.c.label }}</div>
              <div class="tooltip-row"><strong>{{ yFormat()(h.c.value) }}</strong></div>
              @if (h.c.originalValue !== null && h.c.originalValue !== undefined) {
                <div class="tooltip-row muted">originally filed {{ yFormat()(h.c.originalValue) }}</div>
              }
              @if (h.c.detail) {
                <div class="tooltip-row muted">{{ h.c.detail }}</div>
              }
            </div>
          }
        </div>
      }
    </figure>
  `,
})
export class ColumnChart {
  readonly columns = input.required<Column[]>();
  readonly label = input('Column chart');
  readonly height = input(200);
  readonly color = input('var(--series-1)');
  readonly yFormat = input<(v: number) => string>((v) => v.toLocaleString('en-US'));

  protected readonly m = { top: 10, right: 8, bottom: 26, left: 60 };
  protected readonly w = hostWidth(360);
  protected readonly hover = signal<number | null>(null);

  protected readonly hasRevised = computed(() =>
    this.columns().some((c) => c.originalValue !== null && c.originalValue !== undefined),
  );

  protected readonly scale = computed(() => {
    let lo = 0;
    let hi = 0;
    for (const c of this.columns()) {
      lo = Math.min(lo, c.value, c.originalValue ?? 0);
      hi = Math.max(hi, c.value, c.originalValue ?? 0);
    }
    return niceTicks(lo, hi, 4);
  });

  protected y(v: number): number {
    const { lo, hi } = this.scale();
    const span = hi - lo || 1;
    return this.height() - this.m.bottom - ((v - lo) / span) * (this.height() - this.m.top - this.m.bottom);
  }

  protected readonly bars = computed(() => {
    const cols = this.columns();
    const n = cols.length;
    const inner = this.w() - this.m.left - this.m.right;
    const band = inner / Math.max(1, n);
    const bw = Math.max(2, Math.min(24, band * 0.62));
    const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(inner / 48))));
    const y0 = this.y(0);
    return cols.map((c, i) => {
      const bandX = this.m.left + i * band;
      const x = bandX + (band - bw) / 2;
      return {
        key: c.key,
        label: c.label,
        bandX,
        bandW: band,
        x,
        bw,
        d: columnPath(x, bw, y0, this.y(c.value)),
        origY: c.originalValue !== null && c.originalValue !== undefined ? this.y(c.originalValue) : null,
        showLabel: (n - 1 - i) % every === 0,
      };
    });
  });

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    if (i === null) return null;
    const c = this.columns()[i];
    const b = this.bars()[i];
    if (!c || !b) return null;
    const tipW = 190;
    const left = b.x + b.bw + 8 + tipW > this.w() ? Math.max(0, b.x - 8 - tipW) : b.x + b.bw + 8;
    return { c, left };
  });

  protected onKey(ev: KeyboardEvent): void {
    const n = this.columns().length;
    if (!n) return;
    const cur = this.hover() ?? n - 1;
    if (ev.key === 'ArrowLeft') this.hover.set(clamp(cur - 1, 0, n - 1));
    else if (ev.key === 'ArrowRight') this.hover.set(clamp(cur + 1, 0, n - 1));
    else if (ev.key === 'Escape') this.hover.set(null);
    else return;
    ev.preventDefault();
  }
}
