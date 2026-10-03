import { DestroyRef, ElementRef, afterNextRender, inject, signal } from '@angular/core';

/** Width of the host element, tracked with a ResizeObserver (charts render at real pixel size). */
export function hostWidth(initial = 640) {
  const el = inject(ElementRef).nativeElement as HTMLElement;
  const destroyRef = inject(DestroyRef);
  const width = signal(initial);
  afterNextRender(() => {
    const measure = () => {
      const w = Math.floor(el.getBoundingClientRect().width);
      if (w > 0 && w !== width()) width.set(w);
    };
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(() => measure());
      ro.observe(el);
      destroyRef.onDestroy(() => ro.disconnect());
    }
  });
  return width;
}

/** Clean, round tick values covering [min, max]. */
export function niceTicks(min: number, max: number, count = 5): { ticks: number[]; lo: number; hi: number } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { ticks: [0, 1], lo: 0, hi: 1 };
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step * 1e-9; v += step) ticks.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return { ticks, lo, hi };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function dateMs(iso: string): number {
  return Date.parse(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
}

export function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Evenly spaced date ticks with a label granularity chosen by span. */
export function timeTicks(min: number, max: number, count = 5): { v: number; label: string }[] {
  if (!(max > min)) return [{ v: min, label: fmtTick(min, 0) }];
  const span = max - min;
  const out: { v: number; label: string }[] = [];
  for (let i = 0; i < count; i++) {
    const v = min + (span * i) / (count - 1);
    out.push({ v, label: fmtTick(v, span) });
  }
  return out;
}

function fmtTick(ms: number, span: number): string {
  const d = new Date(ms);
  const day = 864e5;
  if (span > 3 * 365 * day) return String(d.getUTCFullYear());
  if (span > 120 * day) return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** Bar with a 4px rounded data-end and a square baseline end (vertical bars). */
export function columnPath(x: number, w: number, yBase: number, yEnd: number, radius = 4): string {
  const h = Math.abs(yEnd - yBase);
  const r = Math.min(radius, w / 2, h);
  if (h < 0.5) return `M${x},${yBase}h${w}`;
  if (yEnd < yBase) {
    // grows upward: round the top
    return `M${x},${yBase}V${yEnd + r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd + r}V${yBase}Z`;
  }
  return `M${x},${yBase}V${yEnd - r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd - r}V${yBase}Z`;
}

/** Horizontal bar with a 4px rounded data-end, square at the baseline (xBase). */
export function rowBarPath(xBase: number, xEnd: number, y: number, h: number, radius = 4): string {
  const w = Math.abs(xEnd - xBase);
  const r = Math.min(radius, h / 2, w);
  if (w < 0.5) return `M${xBase},${y}v${h}`;
  if (xEnd > xBase) {
    return `M${xBase},${y}H${xEnd - r}Q${xEnd},${y} ${xEnd},${y + r}V${y + h - r}Q${xEnd},${y + h} ${xEnd - r},${y + h}H${xBase}Z`;
  }
  return `M${xBase},${y}H${xEnd + r}Q${xEnd},${y} ${xEnd},${y + r}V${y + h - r}Q${xEnd},${y + h} ${xEnd + r},${y + h}H${xBase}Z`;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
