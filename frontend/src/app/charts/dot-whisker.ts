import { Component, computed, input, signal } from '@angular/core';
import { clamp, hostWidth, niceTicks } from './chart-utils';

export interface WhiskerItem {
  /** row key; several items may share a row when they belong to different series */
  row: string;
  /** row label (the first item of a row names it) */
  label: string;
  /** series key; omit for a single-series chart */
  series?: string;
  value: number;
  /** 95% interval; omit when none was measured (the point is drawn alone) */
  lo?: number | null;
  hi?: number | null;
  /** extra tooltip lines */
  details?: string[];
}

export interface WhiskerSeries {
  key: string;
  label: string;
  color: string;
}

/**
 * Horizontal dot-and-whisker chart: one row per thing compared, a dot at the point estimate and a whisker over its
 * 95% interval, against a reference line (zero, 0.5, the benchmark). This is the chart for "is the interval on one
 * side of the line?", the question every verdict on the platform asks. Single-series charts colour each interval by
 * its answer (green wholly on the better side, red wholly on the worse side, grey when it crosses the line or no
 * interval was measured); multi-series charts keep one colour per series and leave the answer to the tooltip.
 * Per-row hover/focus tooltip, arrow keys walk the rows.
 */
@Component({
  selector: 'app-dot-whisker',
  template: `
    <figure class="chart" [attr.aria-label]="label()">
      @if (series().length > 1 || refLabel()) {
        <div class="legend" aria-hidden="true">
          @if (series().length > 1) {
            @for (s of series(); track s.key) {
              <span class="legend-item"><span class="key-dot" [style.background]="s.color"></span>{{ s.label }}</span>
            }
          } @else if (hasIntervals()) {
            <span class="legend-item"><span class="key-dot" style="background: var(--good-ink)"></span>{{ betterIs() === 'lower' ? 'wholly below' : 'wholly above' }} the line</span>
            <span class="legend-item"><span class="key-dot" style="background: var(--ink-muted)"></span>crosses it</span>
            <span class="legend-item"><span class="key-dot" style="background: var(--bad-ink)"></span>wholly on the wrong side</span>
          }
          @if (refX() !== null && refLabel()) {
            <span class="legend-item legend-ref"><span class="key-line key-ref"></span>{{ refLabel() }}</span>
          }
        </div>
      }
      @if (!items().length) {
        <p class="empty">No values.</p>
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
            @for (t of ticks(); track t.v) {
              <line class="grid" [attr.x1]="x(t.v)" [attr.x2]="x(t.v)" [attr.y1]="m.top" [attr.y2]="h() - m.bottom" />
              @if (t.labelled) {
                <text class="tick" [attr.x]="x(t.v)" [attr.y]="h() - m.bottom + 14" text-anchor="middle">{{ format()(t.v) }}</text>
              }
            }
            @if (refX() !== null) {
              <line class="ref" [attr.x1]="x(refX()!)" [attr.x2]="x(refX()!)" [attr.y1]="m.top - 2" [attr.y2]="h() - m.bottom" stroke-dasharray="4 3" />
            }
            @for (r of rows(); track r.key; let i = $index) {
              <rect class="hit" x="0" [attr.y]="r.y" [attr.width]="w()" [attr.height]="r.h" (pointerenter)="hover.set(i)" />
              @if (hover() === i) {
                <rect class="row-hover" x="0" [attr.y]="r.y" [attr.width]="w()" [attr.height]="r.h" />
              }
              <text class="row-label" [attr.x]="labelW() - 10" [attr.y]="r.y + r.h / 2" dy="0.32em" text-anchor="end">
                <title>{{ r.label }}</title>{{ r.short }}
              </text>
              @for (p of r.points; track p.key) {
                @if (p.xLo !== null && p.xHi !== null) {
                  <line class="whisker" [attr.x1]="p.xLo" [attr.x2]="p.xHi" [attr.y1]="p.y" [attr.y2]="p.y" [attr.stroke]="p.color" />
                  <line class="whisker" [attr.x1]="p.xLo" [attr.x2]="p.xLo" [attr.y1]="p.y - 4" [attr.y2]="p.y + 4" [attr.stroke]="p.color" />
                  <line class="whisker" [attr.x1]="p.xHi" [attr.x2]="p.xHi" [attr.y1]="p.y - 4" [attr.y2]="p.y + 4" [attr.stroke]="p.color" />
                }
                <circle class="dot" [class.dot-active]="hover() === i" [attr.cx]="p.x" [attr.cy]="p.y" r="4.5" [attr.fill]="p.color" />
              }
              @if (r.points.length === 1) {
                <text class="value-label" [attr.x]="r.points[0].xText" [attr.y]="r.points[0].y" dy="0.32em" text-anchor="start">{{ format()(r.points[0].value) }}</text>
              }
            }
          </svg>
          @if (hoverInfo(); as t) {
            <div class="tooltip" [style.left.px]="t.left" [style.top.px]="t.top">
              <div class="tooltip-title">{{ t.row.label }}</div>
              @for (p of t.row.points; track p.key) {
                <div class="tooltip-row">
                  @if (series().length > 1) { <span class="key-dot" [style.background]="p.color"></span><span class="muted">{{ p.seriesLabel }}</span> }
                  <strong>{{ format()(p.value) }}</strong>
                  @if (p.lo !== null && p.hi !== null) { <span class="muted">{{ format()(p.lo) }} to {{ format()(p.hi) }}</span> }
                </div>
                @if (p.answer) { <div class="tooltip-row muted">{{ p.answer }}</div> }
                @for (d of p.details; track $index) { <div class="tooltip-row muted">{{ d }}</div> }
              }
            </div>
          }
        </div>
      }
    </figure>
  `,
  styles: `
    .whisker { stroke-width: 2; stroke-linecap: round; }
    .key-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
  `,
})
export class DotWhisker {
  readonly items = input.required<WhiskerItem[]>();
  /** Series in legend order; a single-series chart leaves this empty. */
  readonly series = input<WhiskerSeries[]>([]);
  readonly label = input('Dot and whisker chart');
  /** Reference line (0, 0.5, a benchmark); null for none. */
  readonly refX = input<number | null>(null);
  readonly refLabel = input('');
  /** Which side of the reference is the good one; null when neither is (no colouring by answer). */
  readonly betterIs = input<'higher' | 'lower' | null>('higher');
  readonly format = input<(v: number) => string>((v) => v.toFixed(3));
  /** Pin the axis to a range; otherwise it fits the data and the reference. */
  readonly xMin = input<number | null>(null);
  readonly xMax = input<number | null>(null);

  protected readonly m = { top: 8, right: 64, bottom: 24 };
  protected readonly w = hostWidth(560);
  protected readonly hover = signal<number | null>(null);

  protected readonly labelW = computed(() => Math.round(Math.min(230, Math.max(100, this.w() * 0.42))));
  protected readonly hasIntervals = computed(() => this.items().some((i) => isNum(i.lo) && isNum(i.hi)));

  private readonly rowKeys = computed(() => {
    const out: { key: string; label: string }[] = [];
    for (const it of this.items()) if (!out.some((r) => r.key === it.row)) out.push({ key: it.row, label: it.label });
    return out;
  });
  private readonly seriesKeys = computed(() => (this.series().length ? this.series().map((s) => s.key) : ['']));
  protected readonly rowH = computed(() => (this.seriesKeys().length > 1 ? 18 + 14 * this.seriesKeys().length : 30));
  protected readonly h = computed(() => this.m.top + this.rowKeys().length * this.rowH() + this.m.bottom);

  protected readonly scale = computed(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const it of this.items()) {
      for (const v of [it.value, it.lo, it.hi]) {
        if (isNum(v)) {
          lo = Math.min(lo, v);
          hi = Math.max(hi, v);
        }
      }
    }
    const ref = this.refX();
    if (ref !== null) {
      lo = Math.min(lo, ref);
      hi = Math.max(hi, ref);
    }
    if (!Number.isFinite(lo)) {
      lo = 0;
      hi = 1;
    }
    const pad = (hi - lo || 1) * 0.08;
    const xm = this.xMin();
    const xM = this.xMax();
    return niceTicks(xm ?? lo - pad, xM ?? hi + pad, 5);
  });

  /** Every grid tick, labelled only as densely as the formatted labels fit (about 64px each). */
  protected readonly ticks = computed(() => {
    const ticks = this.scale().ticks;
    const plotW = this.w() - this.m.right - this.labelW();
    const every = Math.max(1, Math.ceil(ticks.length / Math.max(1, Math.floor(plotW / 64))));
    return ticks.map((v, i) => ({ v, labelled: i % every === 0 }));
  });

  protected x(v: number): number {
    const { lo, hi } = this.scale();
    const x0 = this.labelW();
    const x1 = this.w() - this.m.right;
    return x0 + ((v - lo) / (hi - lo || 1)) * (x1 - x0);
  }

  protected readonly rows = computed(() => {
    const maxChars = Math.max(10, Math.floor((this.labelW() - 14) / 6.6));
    const keys = this.seriesKeys();
    const rowH = this.rowH();
    const multi = keys.length > 1;
    const byRow = new Map<string, WhiskerItem[]>();
    for (const it of this.items()) byRow.set(it.row, [...(byRow.get(it.row) ?? []), it]);
    return this.rowKeys().map((r, i) => {
      const y = this.m.top + i * rowH;
      const points = (byRow.get(r.key) ?? []).map((it) => {
        const si = Math.max(0, keys.indexOf(it.series ?? ''));
        const lo = isNum(it.lo) ? it.lo : null;
        const hi = isNum(it.hi) ? it.hi : null;
        const s = this.series()[si];
        return {
          key: it.series ?? 'one',
          seriesLabel: s?.label ?? '',
          value: it.value,
          lo,
          hi,
          x: this.x(it.value),
          xLo: lo === null ? null : this.x(lo),
          xHi: hi === null ? null : this.x(hi),
          xText: this.x(hi ?? it.value) + 8,
          y: multi ? y + 9 + 14 * si + 7 : y + rowH / 2,
          color: multi ? (s?.color ?? 'var(--series-1)') : this.answerColor(it.value, lo, hi),
          answer: this.answerText(lo, hi),
          details: it.details ?? [],
        };
      });
      return { key: r.key, label: r.label, short: r.label.length > maxChars ? `${r.label.slice(0, maxChars - 1)}…` : r.label, y, h: rowH, points };
    });
  });

  /** Where the interval sits against the reference line, in words. */
  private answerText(lo: number | null, hi: number | null): string {
    const ref = this.refX();
    if (ref === null || lo === null || hi === null) return '';
    if (lo > ref) return `interval wholly above ${this.format()(ref)}`;
    if (hi < ref) return `interval wholly below ${this.format()(ref)}`;
    return `interval crosses ${this.format()(ref)}: not distinguishable from it`;
  }

  private answerColor(value: number, lo: number | null, hi: number | null): string {
    const ref = this.refX();
    const better = this.betterIs();
    if (better === null) return 'var(--series-1)';          // no side is the good one: identity colour, no verdict
    if (ref === null || lo === null || hi === null) return 'var(--ink-muted)';
    if (lo > ref) return better === 'higher' ? 'var(--good-ink)' : 'var(--bad-ink)';
    if (hi < ref) return better === 'lower' ? 'var(--good-ink)' : 'var(--bad-ink)';
    return 'var(--ink-muted)';
  }

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    if (i === null) return null;
    const row = this.rows()[i];
    if (!row) return null;
    const left = clamp(this.labelW(), 0, Math.max(0, this.w() - 250));
    return { row, left, top: row.y + row.h + 2 };
  });

  protected onKey(ev: KeyboardEvent): void {
    const n = this.rows().length;
    if (!n) return;
    const cur = this.hover() ?? 0;
    if (ev.key === 'ArrowUp') this.hover.set(clamp(cur - 1, 0, n - 1));
    else if (ev.key === 'ArrowDown') this.hover.set(clamp(cur + 1, 0, n - 1));
    else if (ev.key === 'Escape') this.hover.set(null);
    else return;
    ev.preventDefault();
  }
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}
