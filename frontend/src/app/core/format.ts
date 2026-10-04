import { Pipe, PipeTransform } from '@angular/core';

type Num = number | null | undefined;

const isNum = (v: Num): v is number => typeof v === 'number' && Number.isFinite(v);

/** 0.523 -> "52.3%" */
export function fmtPct(v: Num, digits = 1): string {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : '—';
}

/** 0.03 -> "+3.00%" (returns, differences) */
export function fmtSignedPct(v: Num, digits = 2): string {
  if (!isNum(v)) return '—';
  const s = (v * 100).toFixed(digits);
  return `${v > 0 ? '+' : v < 0 ? '' : '±'}${s}%`;
}

/** Plain number with thousands separators. */
export function fmtNum(v: Num, digits = 0): string {
  if (!isNum(v)) return '—';
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Compact: 1.2e11 -> "120.0B" */
export function fmtCompact(v: Num, digits = 1): string {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  const sign = v < 0 ? '−' : '';
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(digits)}T`;
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(digits)}B`;
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(digits)}M`;
  if (a >= 1e4) return `${sign}${(a / 1e3).toFixed(digits)}K`;
  return `${sign}${a.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
}

/** USD with B/M/K suffix for large values, cents for small ones. */
export function fmtUsd(v: Num, digits = 1): string {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  const sign = v < 0 ? '−' : '';
  if (a >= 1e4) return `${sign}$${fmtCompact(a, digits)}`;
  return `${sign}$${a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Format a filed XBRL value by its unit. */
export function fmtByUnit(v: Num, unit: string | null | undefined): string {
  if (!isNum(v)) return '—';
  const u = (unit ?? '').toUpperCase();
  if (u === 'USD') return fmtUsd(v);
  if (u.startsWith('USD/') || u.startsWith('USD-PER')) return `$${v.toFixed(2)}`;
  if (u === 'SHARES') return `${fmtCompact(v)} sh`;
  if (u === 'PURE' || u === 'RATIO') return Math.abs(v) <= 1 ? fmtPct(v) : fmtNum(v, 2);
  return `${fmtCompact(v)}${unit ? ' ' + unit : ''}`;
}

export function fmtFixed(v: Num, digits = 3): string {
  return isNum(v) ? v.toFixed(digits) : '—';
}

export function fmtSigned(v: Num, digits = 3): string {
  if (!isNum(v)) return '—';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}`;
}

/** ISO timestamp -> "2026-10-03 12:00 UTC" (always UTC so publication times are unambiguous). */
export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
}

export function fmtDate(s: string | null | undefined): string {
  return s ? s.slice(0, 10) : '—';
}

/** "TRADE_TARIFF" -> "Trade tariff" */
export function humanize(s: string | null | undefined): string {
  if (!s) return '—';
  const t = s.replace(/_/g, ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function shortHash(s: string | null | undefined, n = 12): string {
  return s ? (s.length > n ? `${s.slice(0, n)}…` : s) : '—';
}

export function excerpt(s: string | null | undefined, n = 220): string {
  if (!s) return '';
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length > n ? `${t.slice(0, n).trimEnd()}…` : t;
}

@Pipe({ name: 'pct' })
export class PctPipe implements PipeTransform {
  transform(v: Num, digits = 1): string {
    return fmtPct(v, digits);
  }
}

@Pipe({ name: 'signedPct' })
export class SignedPctPipe implements PipeTransform {
  transform(v: Num, digits = 2): string {
    return fmtSignedPct(v, digits);
  }
}

@Pipe({ name: 'num' })
export class NumPipe implements PipeTransform {
  transform(v: Num, digits = 0): string {
    return fmtNum(v, digits);
  }
}

@Pipe({ name: 'fixed' })
export class FixedPipe implements PipeTransform {
  transform(v: Num, digits = 3): string {
    return fmtFixed(v, digits);
  }
}

@Pipe({ name: 'signed' })
export class SignedPipe implements PipeTransform {
  transform(v: Num, digits = 3): string {
    return fmtSigned(v, digits);
  }
}

@Pipe({ name: 'usd' })
export class UsdPipe implements PipeTransform {
  transform(v: Num, digits = 1): string {
    return fmtUsd(v, digits);
  }
}

@Pipe({ name: 'unitValue' })
export class UnitValuePipe implements PipeTransform {
  transform(v: Num, unit: string | null | undefined): string {
    return fmtByUnit(v, unit);
  }
}

@Pipe({ name: 'utc' })
export class UtcPipe implements PipeTransform {
  transform(v: string | null | undefined): string {
    return fmtUtc(v);
  }
}

@Pipe({ name: 'day' })
export class DayPipe implements PipeTransform {
  transform(v: string | null | undefined): string {
    return fmtDate(v);
  }
}

@Pipe({ name: 'human' })
export class HumanizePipe implements PipeTransform {
  transform(v: string | null | undefined): string {
    return humanize(v);
  }
}

@Pipe({ name: 'excerpt' })
export class ExcerptPipe implements PipeTransform {
  transform(v: string | null | undefined, n = 220): string {
    return excerpt(v, n);
  }
}

@Pipe({ name: 'hash' })
export class HashPipe implements PipeTransform {
  transform(v: string | null | undefined, n = 12): string {
    return shortHash(v, n);
  }
}

export const FORMAT_PIPES = [
  PctPipe,
  SignedPctPipe,
  NumPipe,
  FixedPipe,
  SignedPipe,
  UsdPipe,
  UnitValuePipe,
  UtcPipe,
  DayPipe,
  HumanizePipe,
  ExcerptPipe,
  HashPipe,
] as const;
