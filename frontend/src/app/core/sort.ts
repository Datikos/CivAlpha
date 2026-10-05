import { Signal, WritableSignal, signal } from '@angular/core';

export type SortDir = 'asc' | 'desc';

export interface SortState {
  key: string;
  dir: SortDir;
}

export type Getter<T> = (row: T) => number | string | null | undefined;

/** Column sort kept in a signal and remembered per browser under `storageKey`. */
export interface Sorter {
  readonly state: Signal<SortState>;
  toggle(key: string, defaultDir?: SortDir): void;
  set(key: string, dir: SortDir): void;
  /** Stable sort by the active key; rows without a value go last. */
  order<T>(rows: readonly T[], getters: Record<string, Getter<T>>): T[];
}

export function createSort(storageKey: string, initial: SortState): Sorter {
  const state: WritableSignal<SortState> = signal(read(storageKey) ?? initial);
  const persist = (s: SortState) => {
    state.set(s);
    try {
      localStorage.setItem(storageKey, JSON.stringify(s));
    } catch {
      // storage unavailable: the choice lasts for this page
    }
  };
  return {
    state,
    toggle(key, defaultDir = 'desc') {
      const s = state();
      persist(s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: defaultDir });
    },
    set(key, dir) {
      persist({ key, dir });
    },
    order(rows, getters) {
      const { key, dir } = state();
      const get = getters[key];
      if (!get) return [...rows];
      const sign = dir === 'asc' ? 1 : -1;
      return rows
        .map((row, i) => ({ row, i, v: get(row) }))
        .sort((a, b) => {
          const av = a.v;
          const bv = b.v;
          const aNull = av === null || av === undefined || av === '' || (typeof av === 'number' && !Number.isFinite(av));
          const bNull = bv === null || bv === undefined || bv === '' || (typeof bv === 'number' && !Number.isFinite(bv));
          if (aNull && bNull) return a.i - b.i;
          if (aNull) return 1;
          if (bNull) return -1;
          let c: number;
          if (typeof av === 'number' && typeof bv === 'number') c = av - bv;
          else c = String(av).localeCompare(String(bv), 'en', { sensitivity: 'base' });
          return c === 0 ? a.i - b.i : c * sign;
        })
        .map((x) => x.row);
    },
  };
}

function read(storageKey: string): SortState | null {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const v = JSON.parse(raw) as SortState;
    return typeof v?.key === 'string' && (v.dir === 'asc' || v.dir === 'desc') ? v : null;
  } catch {
    return null;
  }
}
