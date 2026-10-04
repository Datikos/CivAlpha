import { DOCUMENT, Injectable, effect, inject, signal } from '@angular/core';

export type ThemePref = 'system' | 'light' | 'dark';

const KEY = 'civalpha.theme';

/** Light / dark / follow-the-OS preference, kept per browser and applied as html[data-theme]. */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly doc = inject(DOCUMENT);
  readonly pref = signal<ThemePref>(read());

  constructor() {
    effect(() => {
      const p = this.pref();
      const root = this.doc.documentElement;
      if (p === 'system') root.removeAttribute('data-theme');
      else root.setAttribute('data-theme', p);
      try {
        if (p === 'system') localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, p);
      } catch {
        // storage unavailable (private mode): the choice lasts for this page only
      }
    });
  }
}

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}
