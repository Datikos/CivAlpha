import { Component, ElementRef, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { fmtFixed, fmtNum, fmtPct, fmtSigned, fmtSignedPct } from '../core/format';
import { Icon, IconName } from './icon';

/**
 * Semantic tones. Every tone is always paired with a glyph or a label, never color alone:
 * good = favourable / supported, bad = unfavourable, warn = caution or estimate,
 * info = neutral information, forecast = a model output, neutral = no direction.
 */
export type Tone = 'good' | 'bad' | 'warn' | 'info' | 'neutral' | 'forecast';

type Num = number | null | undefined;
const isNum = (v: Num): v is number => typeof v === 'number' && Number.isFinite(v);

/** Tone of a signed value: positive is good unless `invert`; |v| <= eps is neutral. */
export function toneOfSign(v: Num, invert = false, eps = 0): Tone {
  if (!isNum(v) || Math.abs(v) <= eps) return 'neutral';
  const up = v > 0;
  return up !== invert ? 'good' : 'bad';
}

/** Tone of a verdict sentence written by the backend. */
export function verdictTone(text: string | null | undefined): Tone {
  const t = (text ?? '').toUpperCase();
  if (!t) return 'info';
  if (t.includes('NOT SUPPORTED') || t.includes('NOT DISTINGUISHABLE') || t.includes('WORSE THAN')) return 'warn';
  if (t.includes('SUPPORTED BY')) return 'good';
  return 'info';
}

export type Lean = 'above' | 'below' | 'flat';

/**
 * Where a forecast leans. "above"/"below" only when the whole uncertainty interval sits on one
 * side of 50%; otherwise it is a coin flip as far as the model can tell.
 */
export function leanOf(p: Num, lo: Num, hi: Num): Lean {
  if (isNum(lo) && lo > 0.5) return 'above';
  if (isNum(hi) && hi < 0.5) return 'below';
  if (!isNum(lo) && !isNum(hi) && isNum(p)) return p > 0.55 ? 'above' : p < 0.45 ? 'below' : 'flat';
  return 'flat';
}

const LEAN_LABEL: Record<Lean, string> = { above: 'leans above', below: 'leans below', flat: 'coin flip' };
const LEAN_TITLE: Record<Lean, string> = {
  above: 'The whole uncertainty interval is above 50%: the model expects the stock to beat its sector ETF.',
  below: 'The whole uncertainty interval is below 50%: the model expects the stock to trail its sector ETF.',
  flat: 'The uncertainty interval straddles 50%: the model cannot tell a direction.',
};
const LEAN_TONE: Record<Lean, Tone> = { above: 'good', below: 'bad', flat: 'neutral' };

/** Signed number with a direction glyph and a tone (▲ good, ▼ bad, — neutral). */
@Component({
  selector: 'app-delta',
  template: `<span class="delta" [class]="'delta tone-' + tone()" [title]="title() || null"
    ><span class="delta-glyph" aria-hidden="true">{{ glyph() }}</span
    ><span class="sr-only">{{ srText() }}</span>{{ text() }}</span
  >`,
})
export class Delta {
  readonly value = input<Num>(null);
  /** pct: 0.03 -> +3.00% · pp: 0.03 -> +3.0 pp · num: +1,234 · fixed: +0.123 · x: 1.2× */
  readonly kind = input<'pct' | 'pp' | 'num' | 'fixed' | 'x'>('pct');
  readonly digits = input<number | undefined>(undefined);
  /** True when a negative value is the good direction (e.g. a Brier difference). */
  readonly invert = input(false);
  /** Values with |v| <= eps are shown as neutral. */
  readonly eps = input(0);
  readonly title = input('');

  protected readonly tone = computed(() => toneOfSign(this.value(), this.invert(), this.eps()));
  protected readonly glyph = computed(() => {
    const v = this.value();
    if (!isNum(v) || Math.abs(v) <= this.eps()) return '•';
    return v > 0 ? '▲' : '▼';
  });
  protected readonly srText = computed(() => {
    const t = this.tone();
    return t === 'good' ? 'favourable ' : t === 'bad' ? 'unfavourable ' : '';
  });
  protected readonly text = computed(() => {
    const v = this.value();
    const d = this.digits();
    switch (this.kind()) {
      case 'pct':
        return fmtSignedPct(v, d ?? 2);
      case 'pp':
        return isNum(v) ? `${fmtSigned(v * 100, d ?? 1)} pp` : '—';
      case 'num':
        return isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${fmtNum(Math.abs(v), d ?? 0)}` : '—';
      case 'x':
        return isNum(v) ? `${v.toFixed(d ?? 1)}×` : '—';
      default:
        return fmtSigned(v, d ?? 3);
    }
  });
}

/** Horizontal meter: value on a [min, max] scale, optional target tick, tone fill. */
@Component({
  selector: 'app-meter',
  template: `<span
    class="meter"
    [class]="'meter tone-' + toneResolved()"
    role="meter"
    [attr.aria-valuemin]="min()"
    [attr.aria-valuemax]="max()"
    [attr.aria-valuenow]="value() ?? null"
    [attr.aria-label]="label() || null"
    [title]="title() || null"
  >
    <span class="meter-fill" [style.width.%]="pct()"></span>
    @if (target() !== null && target() !== undefined) {
      <span class="meter-target" [style.left.%]="x(target()!)" [title]="targetLabel() || null"></span>
    }
  </span>`,
})
export class Meter {
  readonly value = input<Num>(null);
  readonly min = input(0);
  readonly max = input(1);
  readonly target = input<Num>(null);
  readonly targetLabel = input('');
  /** 'auto' colours by comparison with the target (or the midpoint when there is none). */
  readonly tone = input<Tone | 'auto'>('auto');
  readonly higherIsBetter = input(true);
  readonly label = input('');
  readonly title = input('');

  protected x(v: number): number {
    const span = this.max() - this.min() || 1;
    return Math.max(0, Math.min(100, ((v - this.min()) / span) * 100));
  }
  protected readonly pct = computed(() => (isNum(this.value()) ? this.x(this.value()!) : 0));
  protected readonly toneResolved = computed<Tone>(() => {
    const t = this.tone();
    if (t !== 'auto') return t;
    const v = this.value();
    if (!isNum(v)) return 'neutral';
    const ref = isNum(this.target()) ? this.target()! : (this.min() + this.max()) / 2;
    if (v === ref) return 'neutral';
    return v > ref === this.higherIsBetter() ? 'good' : 'bad';
  });
}

/**
 * Progress of a running task: a bar with the whole-number percentage and the current step. Without a total
 * (the task reports no steps) the bar is indeterminate and only the step, if any, is shown.
 */
@Component({
  selector: 'app-progress',
  template: `<span
    class="progress"
    [class.progress-indeterminate]="pct() === null"
    role="progressbar"
    aria-valuemin="0"
    aria-valuemax="100"
    [attr.aria-valuenow]="pct()"
    [attr.aria-valuetext]="pct() === null ? 'in progress' : null"
    [attr.aria-label]="label() || 'progress'"
    [title]="title()"
  >
    <span class="meter tone-info"><span class="meter-fill" [style.width.%]="pct() ?? 35"></span></span>
    <span class="progress-text">
      @if (pct() !== null) {
        <strong class="num progress-pct">{{ pct() }}%</strong>
      }
      @if (step()) {
        <span class="small muted progress-step">{{ step() }}</span>
      }
    </span>
  </span>`,
})
export class Progress {
  readonly done = input<Num>(null);
  readonly total = input<Num>(null);
  readonly step = input<string | null | undefined>(null);
  readonly label = input('');

  protected readonly pct = computed(() => {
    const d = this.done(), t = this.total();
    if (!isNum(d) || !isNum(t) || t <= 0) return null;
    return Math.max(0, Math.min(100, Math.floor((d / t) * 100)));
  });
  protected readonly title = computed(() =>
    this.pct() === null ? 'in progress (no step count reported)' : `${this.done()} of ${this.total()} steps`,
  );
}

/** Inline "table lens" bar behind a formatted number; scaled to the column's largest |value|. */
@Component({
  selector: 'app-cell-bar',
  template: `<span class="cellbar" [class]="'cellbar tone-' + toneResolved()">
    <span class="cellbar-fill" [style.width.%]="w()" aria-hidden="true"></span>
    <span class="cellbar-text">{{ text() }}</span>
  </span>`,
})
export class CellBar {
  readonly value = input<Num>(null);
  /** Largest absolute value in the column (the bar's 100%). */
  readonly max = input(1);
  readonly text = input('—');
  readonly tone = input<Tone | 'auto' | 'sign'>('auto');
  readonly invert = input(false);

  protected readonly w = computed(() => {
    const v = this.value();
    const m = Math.abs(this.max()) || 1;
    return isNum(v) ? Math.min(100, (Math.abs(v) / m) * 100) : 0;
  });
  protected readonly toneResolved = computed<Tone>(() => {
    const t = this.tone();
    if (t === 'sign') return toneOfSign(this.value(), this.invert());
    if (t === 'auto') return 'info';
    return t;
  });
}

/** A confidence interval against a zero line: the bar is good when wholly above zero, bad when wholly below. */
@Component({
  selector: 'app-range-bar',
  template: `<svg class="range-bar" [class]="'range-bar tone-' + tone()" width="96" height="12" viewBox="0 0 96 12" role="img" [attr.aria-label]="aria()">
    <line x1="2" x2="94" y1="6" y2="6" class="rb-track" />
    <line [attr.x1]="x(0)" [attr.x2]="x(0)" y1="1" y2="11" class="rb-zero" />
    @if (lo() !== null && lo() !== undefined && hi() !== null && hi() !== undefined) {
      <line [attr.x1]="x(lo()!)" [attr.x2]="x(hi()!)" y1="6" y2="6" class="rb-range" />
    }
    @if (point() !== null && point() !== undefined) {
      <circle [attr.cx]="x(point()!)" cy="6" r="3.5" class="rb-point" />
    }
  </svg>`,
})
export class RangeBar {
  readonly lo = input<Num>(null);
  readonly hi = input<Num>(null);
  readonly point = input<Num>(null);
  /** Half-width of the scale (± span maps to the bar's ends). */
  readonly span = input(1);
  readonly invert = input(false);
  readonly label = input('');

  protected x(v: number): number {
    const s = Math.abs(this.span()) || 1;
    return 48 + Math.max(-1, Math.min(1, v / s)) * 44;
  }
  protected readonly tone = computed<Tone>(() => {
    const lo = this.lo();
    const hi = this.hi();
    if (!isNum(lo) || !isNum(hi)) return 'neutral';
    if (lo > 0) return this.invert() ? 'bad' : 'good';
    if (hi < 0) return this.invert() ? 'good' : 'bad';
    return 'neutral';
  });
  protected readonly aria = computed(
    () => this.label() || `interval ${fmtFixed(this.lo(), 3)} to ${fmtFixed(this.hi(), 3)}`,
  );
}

/** Forecast lean chip: leans above / leans below / coin flip, from the interval vs 50%. */
@Component({
  selector: 'app-lean',
  template: `<span class="lean" [class]="'lean tone-' + tone()" [title]="title()"
    ><span aria-hidden="true">{{ glyph() }}</span> {{ text() }}</span
  >`,
})
export class LeanChip {
  readonly p = input<Num>(null);
  readonly lo = input<Num>(null);
  readonly hi = input<Num>(null);
  protected readonly lean = computed(() => leanOf(this.p(), this.lo(), this.hi()));
  protected readonly tone = computed(() => LEAN_TONE[this.lean()]);
  protected readonly text = computed(() => LEAN_LABEL[this.lean()]);
  protected readonly title = computed(() => LEAN_TITLE[this.lean()]);
  protected readonly glyph = computed(() => (this.lean() === 'above' ? '▲' : this.lean() === 'below' ? '▼' : '≈'));
}

let helpSeq = 0;

/**
 * A small (i) button with an explanation on hover, focus or tap, and an optional "Learn more"
 * link into the guide. The tip is position: fixed so it is never clipped by scrolling tables.
 */
@Component({
  selector: 'app-help',
  imports: [Icon, RouterLink],
  host: { class: 'help', '(mouseenter)': 'show()', '(mouseleave)': 'hide()' },
  template: `<button
      type="button"
      class="help-btn"
      [attr.aria-label]="'About ' + (label() || 'this value')"
      [attr.aria-describedby]="id"
      [attr.aria-expanded]="open()"
      (click)="toggle()"
      (focus)="show()"
      (blur)="hide()"
      (keydown.escape)="open.set(false)"
    >
      <app-icon name="info" [size]="12" />
    </button>
    <span class="help-tip" role="tooltip" [id]="id" [class.open]="open()" [style.left.px]="pos().x" [style.top.px]="pos().y">
      {{ text() }}
      @if (topic()) {
        <a routerLink="/guide" [fragment]="topic()">Learn more in the guide →</a>
      }
    </span>`,
})
export class Help {
  readonly text = input.required<string>();
  readonly label = input('');
  /** Guide anchor, e.g. "brier". */
  readonly topic = input('');
  protected readonly id = `help-${++helpSeq}`;
  protected readonly open = signal(false);
  protected readonly pos = signal({ x: 0, y: 0 });
  private readonly el = inject(ElementRef<HTMLElement>);

  protected show(): void {
    this.place();
    this.open.set(true);
  }
  protected hide(): void {
    this.open.set(false);
  }
  protected toggle(): void {
    if (this.open()) this.open.set(false);
    else this.show();
  }
  private place(): void {
    const r = (this.el.nativeElement as HTMLElement).getBoundingClientRect();
    const w = 280;
    const x = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    this.pos.set({ x, y: r.bottom + 8 });
  }
}

const VERDICT_ICON: Record<Tone, IconName> = {
  good: 'check',
  bad: 'alert',
  warn: 'alert',
  info: 'info',
  neutral: 'info',
  forecast: 'pulse',
};

/** The one-paragraph conclusion of a page, coloured by what it says. */
@Component({
  selector: 'app-verdict',
  imports: [Icon],
  template: `<div class="verdict" [class]="'verdict tone-' + tone()" role="note">
    <span class="verdict-icon" aria-hidden="true"><app-icon [name]="icon()" [size]="17" /></span>
    <div class="verdict-body">
      @if (title()) {
        <strong>{{ title() }}</strong>&ngsp;
      }
      <ng-content />
    </div>
  </div>`,
})
export class Verdict {
  readonly tone = input<Tone>('info');
  readonly title = input('Verdict.');
  protected readonly icon = computed(() => VERDICT_ICON[this.tone()]);
}

export type Area = 'forecast' | 'research' | 'strategy' | 'data' | 'help';

/** Coloured page icon next to an h1; the colour follows the sidebar section the page belongs to. */
@Component({
  selector: 'app-page-icon',
  imports: [Icon],
  template: `<span class="page-icon" [class]="'page-icon pi-' + area()" aria-hidden="true"
    ><app-icon [name]="name()" [size]="22"
  /></span>`,
})
export class PageIcon {
  readonly name = input.required<IconName>();
  readonly area = input<Area>('forecast');
}

export const VIZ = [Delta, Meter, CellBar, RangeBar, LeanChip, Help, Verdict, PageIcon, Progress] as const;

export { fmtPct, fmtSignedPct, fmtFixed, fmtNum };
