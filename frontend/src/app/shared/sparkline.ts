import { Component, computed, input } from '@angular/core';
import { Tone } from './viz';

/** Tiny line of a numeric series; tone from the last value against the first unless given. */
@Component({
  selector: 'app-sparkline',
  template: `<svg
    class="spark"
    [class]="'spark tone-' + toneResolved()"
    [attr.width]="width()"
    [attr.height]="height()"
    [attr.viewBox]="'0 0 ' + width() + ' ' + height()"
    role="img"
    [attr.aria-label]="label() || null"
  >
    @if (geometry(); as g) {
      <line class="spark-ref" [attr.x1]="0" [attr.x2]="width()" [attr.y1]="g.y0" [attr.y2]="g.y0" />
      <path class="spark-area" [attr.d]="g.area" />
      <path class="spark-line" [attr.d]="g.line" />
      <circle class="spark-dot" [attr.cx]="g.lastX" [attr.cy]="g.lastY" r="2.2" />
    }
  </svg>`,
})
export class Sparkline {
  readonly points = input<readonly number[]>([]);
  readonly width = input(84);
  readonly height = input(24);
  readonly tone = input<Tone | 'auto'>('auto');
  readonly label = input('');

  protected readonly toneResolved = computed<Tone>(() => {
    const t = this.tone();
    if (t !== 'auto') return t;
    const p = this.points();
    if (p.length < 2) return 'neutral';
    return p[p.length - 1] > p[0] ? 'good' : p[p.length - 1] < p[0] ? 'bad' : 'neutral';
  });

  protected readonly geometry = computed(() => {
    const p = this.points().filter((v) => Number.isFinite(v));
    if (p.length < 2) return null;
    const w = this.width();
    const h = this.height();
    const pad = 3;
    let lo = Math.min(...p);
    let hi = Math.max(...p);
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }
    const x = (i: number) => pad + (i / (p.length - 1)) * (w - 2 * pad);
    const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (h - 2 * pad);
    const line = p.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
    const area = `${line}L${x(p.length - 1).toFixed(1)},${(h - pad).toFixed(1)}L${x(0).toFixed(1)},${(h - pad).toFixed(1)}Z`;
    return { line, area, y0: y(p[0]), lastX: x(p.length - 1), lastY: y(p[p.length - 1]) };
  });
}
