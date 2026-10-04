import { Component, computed, input, signal } from '@angular/core';
import { fmtNum, fmtPct } from '../core/format';
import { hostWidth } from './chart-utils';

export interface ReliabilityPoint {
  binLow: number;
  binHigh: number;
  meanPredicted: number | null;
  observedRate: number | null;
  count: number;
}

export interface ReliabilitySeries {
  key: string;
  label: string;
  color: string;
  bins: ReliabilityPoint[];
}

/**
 * Reliability (calibration) diagram: mean predicted probability (x) vs observed frequency (y)
 * per bin, with the diagonal of perfect calibration. Per-point hover/focus tooltips.
 */
@Component({
  selector: 'app-reliability-chart',
  template: `
    <figure class="chart" [attr.aria-label]="label()">
      <div class="legend" aria-hidden="true">
        @for (s of series(); track s.key) {
          <span class="legend-item"><span class="key-line" [style.background]="s.color"></span>{{ s.label }}</span>
        }
        <span class="legend-item"><span class="key-line key-ref"></span>Perfect calibration</span>
      </div>
      <div class="chart-plot" (pointerleave)="hover.set(null)">
        <svg [attr.width]="size() + m.left + m.right" [attr.height]="size() + m.top + m.bottom" role="img" [attr.aria-label]="label()">
          @for (t of ticks; track t) {
            <line class="grid" [attr.x1]="m.left" [attr.x2]="m.left + size()" [attr.y1]="y(t)" [attr.y2]="y(t)" />
            <text class="tick" [attr.x]="m.left - 8" [attr.y]="y(t)" dy="0.32em" text-anchor="end">{{ pct(t, 0) }}</text>
            <text class="tick" [attr.x]="x(t)" [attr.y]="m.top + size() + 18" text-anchor="middle">{{ pct(t, 0) }}</text>
          }
          <line class="axis" [attr.x1]="m.left" [attr.x2]="m.left + size()" [attr.y1]="y(0)" [attr.y2]="y(0)" />
          <line class="ref" [attr.x1]="x(0)" [attr.y1]="y(0)" [attr.x2]="x(1)" [attr.y2]="y(1)" />
          <text class="axis-title" [attr.x]="m.left + size() / 2" [attr.y]="m.top + size() + 36" text-anchor="middle">
            Mean predicted probability
          </text>
          <text
            class="axis-title"
            [attr.transform]="'translate(14,' + (m.top + size() / 2) + ') rotate(-90)'"
            text-anchor="middle"
          >
            Observed outperformance rate
          </text>
          @for (s of plotted(); track s.key) {
            <path class="line" [attr.d]="s.d" [attr.stroke]="s.color" />
            @for (p of s.pts; track $index) {
              <circle
                [attr.cx]="p.cx"
                [attr.cy]="p.cy"
                r="4"
                class="dot"
                [class.dot-active]="hover()?.s === s.key && hover()?.i === $index"
                [attr.fill]="s.color"
              />
              <circle
                class="hit"
                [attr.cx]="p.cx"
                [attr.cy]="p.cy"
                r="12"
                tabindex="0"
                [attr.aria-label]="s.label + ': predicted ' + pct(p.b.meanPredicted) + ', observed ' + pct(p.b.observedRate) + ', n=' + p.b.count"
                (pointerenter)="hover.set({ s: s.key, i: $index })"
                (focus)="hover.set({ s: s.key, i: $index })"
                (blur)="hover.set(null)"
              />
            }
          }
        </svg>
        @if (hoverInfo(); as h) {
          <div class="tooltip" [style.left.px]="h.left" [style.top.px]="h.top">
            <div class="tooltip-title">
              <span class="key-line" [style.background]="h.color"></span> {{ h.label }} · bin {{ pct(h.b.binLow, 0) }}–{{ pct(h.b.binHigh, 0) }}
            </div>
            <div class="tooltip-row"><strong>{{ pct(h.b.observedRate) }}</strong> <span class="muted">observed</span></div>
            <div class="tooltip-row"><strong>{{ pct(h.b.meanPredicted) }}</strong> <span class="muted">mean predicted</span></div>
            <div class="tooltip-row"><strong>{{ num(h.b.count) }}</strong> <span class="muted">samples</span></div>
          </div>
        }
      </div>
    </figure>
  `,
})
export class ReliabilityChart {
  readonly series = input.required<ReliabilitySeries[]>();
  readonly label = input('Reliability diagram');

  protected readonly m = { top: 10, right: 16, bottom: 44, left: 64 };
  protected readonly ticks = [0, 0.2, 0.4, 0.6, 0.8, 1];
  private readonly w = hostWidth(420);
  protected readonly size = computed(() => Math.max(180, Math.min(380, this.w() - this.m.left - this.m.right)));
  protected readonly hover = signal<{ s: string; i: number } | null>(null);
  protected pct = fmtPct;
  protected num = fmtNum;

  protected x(v: number): number {
    return this.m.left + v * this.size();
  }
  protected y(v: number): number {
    return this.m.top + (1 - v) * this.size();
  }

  protected readonly plotted = computed(() =>
    this.series().map((s) => {
      const pts = s.bins
        .filter((b) => b.count > 0 && b.meanPredicted !== null && b.observedRate !== null)
        .sort((a, b) => a.meanPredicted! - b.meanPredicted!)
        .map((b) => ({ b, cx: this.x(b.meanPredicted!), cy: this.y(b.observedRate!) }));
      return {
        key: s.key,
        label: s.label,
        color: s.color,
        pts,
        d: pts.map((p, i) => `${i ? 'L' : 'M'}${p.cx.toFixed(1)},${p.cy.toFixed(1)}`).join(''),
      };
    }),
  );

  protected readonly hoverInfo = computed(() => {
    const h = this.hover();
    if (!h) return null;
    const s = this.plotted().find((p) => p.key === h.s);
    const p = s?.pts[h.i];
    if (!s || !p) return null;
    const full = this.size() + this.m.left + this.m.right;
    const left = p.cx + 14 + 200 > full ? Math.max(0, p.cx - 214) : p.cx + 14;
    return { label: s.label, color: s.color, b: p.b, left, top: Math.max(0, p.cy - 40) };
  });
}
