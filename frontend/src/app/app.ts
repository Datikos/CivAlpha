import { isPlatformBrowser } from '@angular/common';
import { Component, HostListener, PLATFORM_ID, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { MetaService } from './core/meta.service';
import { NAV_GROUPS } from './core/nav';
import { ThemePref, ThemeService } from './core/theme.service';
import { CommandPalette } from './shared/command-palette';
import { Icon } from './shared/icon';

const MOBILE = '(max-width: 900px)';

/**
 * App shell: a grouped sidebar on wide screens (collapsible to icons), a top bar with a
 * drawer on narrow ones, a ⌘K palette, and a theme switch. The sidebar never scrolls
 * horizontally: every section is visible at once.
 */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Icon, CommandPalette],
  templateUrl: './app.html',
  styleUrl: './app.css',
  host: {
    '[class.rail]': 'collapsed()',
    '[class.drawer-open]': 'drawer()',
  },
})
export class App {
  protected readonly meta = inject(MetaService);
  protected readonly theme = inject(ThemeService);
  protected readonly groups = NAV_GROUPS;

  /** Sidebar reduced to an icon rail (remembered per browser). */
  protected readonly collapsed = signal(readFlag('civalpha.rail'));
  /** Mobile drawer. */
  protected readonly drawer = signal(false);
  protected readonly palette = signal(false);
  protected readonly isMac =
    isPlatformBrowser(inject(PLATFORM_ID)) && /Mac|iPhone|iPad/.test(navigator.platform);

  private readonly router = inject(Router);

  constructor() {
    this.router.events
      .pipe(filter((e) => e instanceof NavigationEnd))
      .subscribe(() => this.drawer.set(false));
    effect(() => writeFlag('civalpha.rail', this.collapsed()));
    effect(() => {
      document.body.style.overflow = this.drawer() ? 'hidden' : '';
    });
  }

  protected toggleRail(): void {
    this.collapsed.update((v) => !v);
  }

  protected setTheme(p: ThemePref): void {
    this.theme.pref.set(p);
  }

  @HostListener('document:keydown', ['$event'])
  protected onKey(e: KeyboardEvent): void {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      this.palette.update((v) => !v);
    } else if (e.key === 'Escape' && this.drawer()) {
      this.drawer.set(false);
    } else if (e.key === '/' && !this.palette() && !isTyping(e.target)) {
      e.preventDefault();
      this.palette.set(true);
    }
  }

  @HostListener('window:resize')
  protected onResize(): void {
    if (this.drawer() && !matchMedia(MOBILE).matches) this.drawer.set(false);
  }
}

function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

function writeFlag(key: string, v: boolean): void {
  try {
    if (v) localStorage.setItem(key, '1');
    else localStorage.removeItem(key);
  } catch {
    // storage unavailable: forget on reload
  }
}
