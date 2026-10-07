import { isPlatformBrowser } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, HostListener, PLATFORM_ID, computed, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { AuthService } from './core/auth.service';
import { MetaService } from './core/meta.service';
import { NAV_GROUPS, visibleNav } from './core/nav';
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
    '[class.bare]': 'bare()',
  },
})
export class App {
  protected readonly meta = inject(MetaService);
  protected readonly theme = inject(ThemeService);
  protected readonly auth = inject(AuthService);
  /** Owner-only links are hidden for a MEMBER in accounts mode; account links only exist in accounts mode. */
  protected readonly groups = computed(() =>
    visibleNav(NAV_GROUPS, this.auth.accountsMode(), this.auth.ownerView()),
  );
  /** The signed-in person shown at the foot of the sidebar (accounts mode only). */
  protected readonly user = computed(() =>
    this.auth.accountsMode() && this.auth.signedIn() ? this.auth.caller() : null,
  );
  /** The API cannot be reached (no answer or a 5xx); a 401/403 on /api/meta is a sign-in matter, not an outage. */
  protected readonly apiDown = computed(() => {
    const e = this.meta.resource.error();
    if (!e) return false;
    const status = e instanceof HttpErrorResponse ? e.status : ((e as { cause?: unknown }).cause as HttpErrorResponse | undefined)?.status;
    return status === undefined || status === 0 || status >= 500;
  });
  /** The sign-in page is shown without the navigation. */
  protected readonly bare = signal(location.pathname.startsWith('/login'));

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
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => {
        this.drawer.set(false);
        this.bare.set(e.urlAfterRedirects.startsWith('/login'));
      });
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

  protected signOut(): void {
    this.auth.logout().subscribe({
      next: () => void this.router.navigate(['/login']),
      error: () => void this.router.navigate(['/login']),
    });
  }

  @HostListener('document:keydown', ['$event'])
  protected onKey(e: KeyboardEvent): void {
    if (this.bare()) return;
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
