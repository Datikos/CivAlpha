import { Component, computed, input, signal } from '@angular/core';
import { clamp, hostWidth, rowBarPath } from './chart-utils';

export interface DivergingItem {
  key: string;
  label: string;
  value: number;
  /** extra tooltip lines */
  details?: string[];
}

/**
 * Horizontal diverging bars around zero (blue = raises, red = lowers). Bars <= 16px thick,
 * rounded data-end, square at zero; value at the tip in text ink; per-row hover/focus tooltip.
 */
@Component({
  selector: 'app-diverging-bars',
  template: `
    <figure class="chart" [attr.aria-label]="label()">
      <div class="legend" aria-hidden="true">
        <span class="legend-item"><span class="key-rect" style="background: var(--div-pos)"></span>{{ posLabel() }}</span>
        <span class="legend-item"><span class="key-rect" style="background: var(--div-neg)"></span>{{ negLabel() }}</span>
      </div>
      @if (!items().length) {
        <p class="empty">No factors.</p>
      } @else {
        <div class="chart-plot" (pointerleave)="hover.set(null)">
          <svg
            [attr.width]="w()"
            [attr.height]="h()"
            role="img"
            [attr.aria-label]="label() + '. Use up and down arrow keys to read values.'"
            tabindex="0"
            (keydown)="onKey($event)"
            (focus)="hover() === null && hover.set(0)"
            (blur)="hover.set(null)"
          >
            <line class="axis" [attr.x1]="x0()" [attr.x2]="x0()" [attr.y1]="4" [attr.y2]="h() - 4" />
            @for (r of rows(); track r.key; let i = $index) {
              <rect class="hit" x="0" [attr.y]="r.y" [attr.width]="w()" [attr.height]="rowH" (pointerenter)="hover.set(i)" />
              @if (hover() === i) {
                <rect class="row-hover" x="0" [attr.y]="r.y" [attr.width]="w()" [attr.height]="rowH" />
              }
              <text class="row-label" [attr.x]="labelW() - 10" [attr.y]="r.y + rowH / 2" dy="0.32em" text-anchor="end">
                <title>{{ r.full }}</title>{{ r.short }}
              </text>
              <path [attr.d]="r.d" [attr.fill]="r.value >= 0 ? 'var(--div-pos)' : 'var(--div-neg)'" />
              <text
                class="value-label"
                [attr.x]="r.value >= 0 ? r.xEnd + 6 : r.xEnd - 6"
                [attr.y]="r.y + rowH / 2"
                dy="0.32em"
                [attr.text-anchor]="r.value >= 0 ? 'start' : 'end'"
              >
                {{ format()(r.value) }}
              </text>
            }
          </svg>
          @if (hoverInfo(); as t) {
            <div class="tooltip" [style.left.px]="t.left" [style.top.px]="t.top">
              <div class="tooltip-title">{{ t.item.label }}</div>
              <div class="tooltip-row"><strong>{{ format()(t.item.value) }}</strong> <span class="muted">{{ unitLabel() }}</span></div>
              @for (d of t.item.details ?? []; track $index) {
                <div class="tooltip-row muted">{{ d }}</div>
              }
            </div>
          }
        </div>
      }
    </figure>
  `,
})
export class DivergingBars {
  readonly items = input.required<DivergingItem[]>();
  readonly label = input('Diverging bar chart');
  readonly posLabel = input('Raises probability');
  readonly negLabel = input('Lowers probability');
  readonly unitLabel = input('');
  readonly format = input<(v: number) => string>((v) => (v > 0 ? '+' : '') + v.toFixed(2));

  protected readonly rowH = 30;
  private readonly barH = 16;
  protected readonly w = hostWidth(640);
  protected readonly hover = signal<number | null>(null);

  protected readonly h = computed(() => this.items().length * this.rowH + 8);
  protected readonly labelW = computed(() => Math.round(Math.min(240, Math.max(110, this.w() * 0.36))));
  private readonly pad = 56;
  private readonly maxAbs = computed(() => Math.max(1e-9, ...this.items().map((i) => Math.abs(i.value))));
  protected readonly x0 = computed(() => this.labelW() + this.pad + (this.w() - this.labelW() - 2 * this.pad) / 2);
  private readonly half = computed(() => (this.w() - this.labelW() - 2 * this.pad) / 2);

  protected readonly rows = computed(() => {
    const maxChars = Math.max(10, Math.floor((this.labelW() - 14) / 6.6));
    return this.items().map((it, i) => {
      const y = 4 + i * this.rowH;
      const xEnd = this.x0() + (it.value / this.maxAbs()) * this.half();
      return {
        key: it.key,
        value: it.value,
        y,
        xEnd,
        full: it.label,
        short: it.label.length > maxChars ? `${it.label.slice(0, maxChars - 1)}…` : it.label,
        d: rowBarPath(this.x0(), xEnd, y + (this.rowH - this.barH) / 2, this.barH),
      };
    });
  });

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    if (i === null) return null;
    const item = this.items()[i];
    const r = this.rows()[i];
    if (!item || !r) return null;
    const left = clamp(this.x0() - 110, 0, Math.max(0, this.w() - 240));
    return { item, left, top: r.y + this.rowH + 2 };
  });

  protected onKey(ev: KeyboardEvent): void {
    const n = this.items().length;
    if (!n) return;
    const cur = this.hover() ?? 0;
    if (ev.key === 'ArrowUp') this.hover.set(clamp(cur - 1, 0, n - 1));
    else if (ev.key === 'ArrowDown') this.hover.set(clamp(cur + 1, 0, n - 1));
    else if (ev.key === 'Escape') this.hover.set(null);
    else return;
    ev.preventDefault();
  }
}
