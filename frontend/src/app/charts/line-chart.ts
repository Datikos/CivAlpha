import { Component, computed, input, signal } from '@angular/core';
import { clamp, hostWidth, isoDay, niceTicks, timeTicks } from './chart-utils';

export interface LinePoint {
  /** epoch ms (UTC day) */
  x: number;
  y: number;
  lo?: number | null;
  hi?: number | null;
}

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  points: LinePoint[];
}

interface Margins {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/**
 * Multi-series time line chart (one y-axis). 2px lines, optional 10% interval band,
 * crosshair + tooltip listing every series, keyboard navigation, legend for >= 2 series,
 * selective end labels, and a data-table twin.
 */
@Component({
  selector: 'app-line-chart',
  template: `
    <figure class="chart" [attr.aria-label]="label()">
      @if (series().length > 1) {
        <div class="legend" aria-hidden="true">
          @for (s of series(); track s.key) {
            <span class="legend-item"
              ><span class="key-line" [style.background]="s.color"></span>{{ s.label }}</span
            >
          }
          @if (refY() !== null && refLabel()) {
            <span class="legend-item legend-ref"><span class="key-line key-ref"></span>{{ refLabel() }}</span>
          }
        </div>
      }
      @if (empty()) {
        <p class="empty">No data to chart.</p>
      } @else {
        <div class="chart-plot" (pointerleave)="hover.set(null)">
          <svg
            [attr.width]="w()"
            [attr.height]="height()"
            [attr.viewBox]="'0 0 ' + w() + ' ' + height()"
            role="img"
            [attr.aria-label]="label() + '. Use left and right arrow keys to read values.'"
            tabindex="0"
            (pointermove)="onMove($event)"
            (keydown)="onKey($event)"
            (focus)="onFocus()"
            (blur)="hover.set(null)"
          >
            @for (t of yTicks(); track t) {
              <line class="grid" [attr.x1]="m().left" [attr.x2]="w() - m().right" [attr.y1]="y(t)" [attr.y2]="y(t)" />
              <text class="tick" [attr.x]="m().left - 8" [attr.y]="y(t)" dy="0.32em" text-anchor="end">
                {{ yFormat()(t) }}
              </text>
            }
            <line
              class="axis"
              [attr.x1]="m().left"
              [attr.x2]="w() - m().right"
              [attr.y1]="height() - m().bottom"
              [attr.y2]="height() - m().bottom"
            />
            @for (t of xTicks(); track t.v; let first = $first; let last = $last) {
              <text
                class="tick"
                [attr.x]="x(t.v)"
                [attr.y]="height() - m().bottom + 18"
                [attr.text-anchor]="first ? 'start' : last ? 'end' : 'middle'"
              >
                {{ t.label }}
              </text>
            }
            @if (refY() !== null) {
              <line class="ref" [attr.x1]="m().left" [attr.x2]="w() - m().right" [attr.y1]="y(refY()!)" [attr.y2]="y(refY()!)" />
            }
            @for (b of bands(); track b.key) {
              <path class="band" [attr.d]="b.d" [attr.fill]="b.color" />
            }
            @for (p of paths(); track p.key) {
              <path class="line" [attr.d]="p.d" [attr.stroke]="p.color" />
            }
            @for (e of endLabels(); track e.key) {
              <circle [attr.cx]="e.x" [attr.cy]="e.y" r="4" class="dot" [attr.fill]="e.color" />
              <text class="end-label" [attr.x]="e.x + 8" [attr.y]="e.y" dy="0.32em">{{ e.text }}</text>
            }
            @if (hoverInfo(); as h) {
              <line class="crosshair" [attr.x1]="h.px" [attr.x2]="h.px" [attr.y1]="m().top" [attr.y2]="height() - m().bottom" />
              @for (r of h.rows; track r.key) {
                @if (r.y !== null) {
                  <circle [attr.cx]="h.px" [attr.cy]="y(r.y)" r="4" class="dot" [attr.fill]="r.color" />
                }
              }
            }
          </svg>
          @if (hoverInfo(); as h) {
            <div class="tooltip" [style.left.px]="h.tipLeft" [style.top.px]="m().top">
              <div class="tooltip-title">{{ h.date }}</div>
              @for (r of h.rows; track r.key) {
                <div class="tooltip-row">
                  <span class="key-line" [style.background]="r.color"></span>
                  <strong>{{ r.y === null ? '—' : yFormat()(r.y) }}</strong>
                  @if (r.lo !== null && r.hi !== null) {
                    <span class="muted">[{{ yFormat()(r.lo) }}–{{ yFormat()(r.hi) }}]</span>
                  }
                  <span class="muted">{{ r.label }}</span>
                </div>
              }
            </div>
          }
        </div>
        <details class="chart-table" (toggle)="tableOpen.set($any($event.target).open)">
          <summary>Data table</summary>
          @if (tableOpen()) {
            <div class="table-wrap">
              <table class="table compact">
                <thead>
                  <tr>
                    <th>Date</th>
                    @for (s of series(); track s.key) {
                      <th class="num">{{ s.label }}</th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (row of tableRows(); track row.x) {
                    <tr>
                      <td>{{ row.date }}</td>
                      @for (v of row.values; track $index) {
                        <td class="num">{{ v }}</td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </details>
      }
      @if (caption()) {
        <figcaption class="muted small">{{ caption() }}</figcaption>
      }
    </figure>
  `,
})
export class LineChart {
  readonly series = input.required<LineSeries[]>();
  readonly label = input('Line chart');
  readonly caption = input('');
  readonly height = input(260);
  readonly yFormat = input<(v: number) => string>((v) => v.toLocaleString('en-US', { maximumFractionDigits: 1 }));
  readonly yMin = input<number | null>(null);
  readonly yMax = input<number | null>(null);
  readonly refY = input<number | null>(null);
  readonly refLabel = input('');
  readonly endLabelsEnabled = input(true);

  protected readonly w = hostWidth();
  protected readonly hover = signal<number | null>(null);
  protected readonly tableOpen = signal(false);

  protected readonly empty = computed(() => this.series().every((s) => s.points.length === 0));

  private readonly sorted = computed(() =>
    this.series().map((s) => ({ ...s, points: [...s.points].sort((a, b) => a.x - b.x) })),
  );

  protected readonly xs = computed(() => {
    const set = new Set<number>();
    for (const s of this.sorted()) for (const p of s.points) set.add(p.x);
    return [...set].sort((a, b) => a - b);
  });

  private readonly lookup = computed(() => this.sorted().map((s) => new Map(s.points.map((p) => [p.x, p]))));

  private readonly showEnd = computed(() => this.endLabelsEnabled() && this.w() >= 480 && this.series().length <= 4);

  protected readonly m = computed<Margins>(() => ({
    top: 12,
    right: this.showEnd() ? 112 : 16,
    bottom: 28,
    left: 56,
  }));

  private readonly yScale = computed(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of this.series())
      for (const p of s.points) {
        lo = Math.min(lo, p.y, p.lo ?? p.y);
        hi = Math.max(hi, p.y, p.hi ?? p.y);
      }
    const ref = this.refY();
    if (ref !== null) {
      lo = Math.min(lo, ref);
      hi = Math.max(hi, ref);
    }
    if (this.yMin() !== null) lo = this.yMin()!;
    if (this.yMax() !== null) hi = this.yMax()!;
    const nt = niceTicks(lo, hi, Math.max(3, Math.round((this.height() - 40) / 50)));
    const dlo = this.yMin() ?? nt.lo;
    const dhi = this.yMax() ?? nt.hi;
    return { lo: dlo, hi: dhi, ticks: nt.ticks.filter((t) => t >= dlo - 1e-9 && t <= dhi + 1e-9) };
  });

  protected readonly yTicks = computed(() => this.yScale().ticks);

  protected readonly xTicks = computed(() => {
    const xs = this.xs();
    if (!xs.length) return [];
    return timeTicks(xs[0], xs[xs.length - 1], this.w() < 480 ? 3 : 5);
  });

  protected x(v: number): number {
    const xs = this.xs();
    const m = this.m();
    const lo = xs[0] ?? 0;
    const hi = xs[xs.length - 1] ?? 1;
    const span = hi - lo || 1;
    return m.left + ((v - lo) / span) * (this.w() - m.left - m.right);
  }

  protected y(v: number): number {
    const { lo, hi } = this.yScale();
    const m = this.m();
    const span = hi - lo || 1;
    return this.height() - m.bottom - ((v - lo) / span) * (this.height() - m.top - m.bottom);
  }

  protected readonly paths = computed(() =>
    this.sorted().map((s) => ({
      key: s.key,
      color: s.color,
      d: s.points.map((p, i) => `${i ? 'L' : 'M'}${this.x(p.x).toFixed(1)},${this.y(p.y).toFixed(1)}`).join(''),
    })),
  );

  protected readonly bands = computed(() =>
    this.sorted()
      .filter((s) => s.points.some((p) => p.lo != null && p.hi != null))
      .map((s) => {
        const pts = s.points.filter((p) => p.lo != null && p.hi != null);
        const top = pts.map((p, i) => `${i ? 'L' : 'M'}${this.x(p.x).toFixed(1)},${this.y(p.hi!).toFixed(1)}`).join('');
        const bottom = [...pts]
          .reverse()
          .map((p) => `L${this.x(p.x).toFixed(1)},${this.y(p.lo!).toFixed(1)}`)
          .join('');
        return { key: s.key, color: s.color, d: `${top}${bottom}Z` };
      }),
  );

  protected readonly endLabels = computed(() => {
    if (!this.showEnd()) return [];
    const out = this.sorted()
      .filter((s) => s.points.length)
      .map((s) => {
        const p = s.points[s.points.length - 1];
        return {
          key: s.key,
          color: s.color,
          x: this.x(p.x),
          y: this.y(p.y),
          text: `${s.label} ${this.yFormat()(p.y)}`,
        };
      });
    // Converging ends: don't stack labels; the legend + tooltip carry identity instead.
    const ys = out.map((o) => o.y).sort((a, b) => a - b);
    for (let i = 1; i < ys.length; i++) if (ys[i] - ys[i - 1] < 15) return [];
    return out;
  });

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    const xs = this.xs();
    if (i === null || !xs.length) return null;
    const xv = xs[clamp(i, 0, xs.length - 1)];
    const px = this.x(xv);
    const lookups = this.lookup();
    const rows = this.sorted().map((s, si) => {
      const p = lookups[si].get(xv);
      return {
        key: s.key,
        label: s.label,
        color: s.color,
        y: p ? p.y : null,
        lo: p?.lo ?? null,
        hi: p?.hi ?? null,
      };
    });
    const tipW = 210;
    const tipLeft = px + 12 + tipW > this.w() ? Math.max(0, px - 12 - tipW) : px + 12;
    return { px, date: isoDay(xv), rows, tipLeft };
  });

  protected readonly tableRows = computed(() => {
    const lookups = this.lookup();
    const fmt = this.yFormat();
    return [...this.xs()].reverse().map((xv) => ({
      x: xv,
      date: isoDay(xv),
      values: lookups.map((l) => {
        const p = l.get(xv);
        if (!p) return '—';
        return p.lo != null && p.hi != null ? `${fmt(p.y)} [${fmt(p.lo)}–${fmt(p.hi)}]` : fmt(p.y);
      }),
    }));
  });

  protected onMove(ev: PointerEvent): void {
    const xs = this.xs();
    if (!xs.length) return;
    const rect = (ev.currentTarget as SVGElement).getBoundingClientRect();
    const px = ev.clientX - rect.left;
    const m = this.m();
    const lo = xs[0];
    const hi = xs[xs.length - 1];
    const v = lo + ((px - m.left) / Math.max(1, this.w() - m.left - m.right)) * (hi - lo);
    // binary search nearest
    let a = 0;
    let b = xs.length - 1;
    while (b - a > 1) {
      const mid = (a + b) >> 1;
      if (xs[mid] < v) a = mid;
      else b = mid;
    }
    this.hover.set(Math.abs(xs[a] - v) <= Math.abs(xs[b] - v) ? a : b);
  }

  protected onFocus(): void {
    if (this.hover() === null) this.hover.set(this.xs().length - 1);
  }

  protected onKey(ev: KeyboardEvent): void {
    const n = this.xs().length;
    if (!n) return;
    const cur = this.hover() ?? n - 1;
    const step = ev.shiftKey ? 10 : 1;
    if (ev.key === 'ArrowLeft') this.hover.set(clamp(cur - step, 0, n - 1));
    else if (ev.key === 'ArrowRight') this.hover.set(clamp(cur + step, 0, n - 1));
    else if (ev.key === 'Home') this.hover.set(0);
    else if (ev.key === 'End') this.hover.set(n - 1);
    else if (ev.key === 'Escape') this.hover.set(null);
    else return;
    ev.preventDefault();
  }
}
