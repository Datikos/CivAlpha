import { Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { backendError } from '../core/api';
import { AuthService } from '../core/auth.service';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

/** Only a path inside the app may follow sign-in: never another site. */
function safeNext(next: string | null | undefined): string {
  return next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/login') ? next : '/';
}

@Component({
  selector: 'app-login',
  imports: [FormsModule, RouterLink, Icon, ...UI, ...VIZ],
  template: `
    <div class="login-wrap">
      <section class="card login">
        <div class="login-head">
          <span class="brand-mark" aria-hidden="true"
            ><svg width="22" height="22" viewBox="0 0 24 24">
              <path d="M3 18 L9 11 L13 14 L21 5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
              <circle cx="21" cy="5" r="2.2" fill="currentColor" /></svg
          ></span>
          <div>
            <h1>Sign in to CivAlpha</h1>
            <p class="small muted">Research software on US-listed stocks. Not investment advice.</p>
          </div>
        </div>

        <app-status [res]="auth.resource" what="the sign-in state" />

        @if (auth.authMode() === 'token') {
          <app-verdict tone="info" title="Sign-in is off on this installation.">
            It runs in token mode: research pages are open, and admin and portfolio actions need the admin token
            (entered on Data &amp; pipeline).
          </app-verdict>
          <p><a routerLink="/" class="btn btn-primary">Go to the dashboard</a></p>
        } @else if (auth.authMode() === 'accounts') {
          @if (auth.signedIn() && auth.caller(); as c) {
            <div class="alert alert-ok small" role="status">
              ✓ Signed in as <strong>{{ c.displayName || c.username }}</strong>.
              <a [routerLink]="next()">Continue</a>
            </div>
          }
          <form class="login-form" (ngSubmit)="submit()">
            <label class="field">
              <span class="req">Username</span>
              <input name="username" [(ngModel)]="username" required autocomplete="username" autocapitalize="none" spellcheck="false" />
            </label>
            <label class="field">
              <span class="req">Password</span>
              <input name="password" type="password" [(ngModel)]="password" required autocomplete="current-password" />
            </label>
            @if (error(); as e) {
              <div class="alert alert-error small" role="alert">✕ {{ e }}</div>
            }
            <button type="submit" class="btn btn-primary" [disabled]="busy() || !username.trim() || !password">
              <app-icon name="lock" [size]="16" /> {{ busy() ? 'Signing in…' : 'Sign in' }}
            </button>
          </form>
          <ul class="small muted notes">
            <li>Forgot your password? Ask the owner of this installation to set a new one.</li>
            <li>Accounts are created by the owner; there is no sign-up.</li>
            <li>
              5 wrong passwords in a row lock the account for 15 minutes.
              <app-help text="20 failed sign-ins from one address within 15 minutes make that address wait. A session ends after 12 hours without use and after 7 days at most." label="sign-in limits" />
            </li>
          </ul>
        }
        @if (auth.authMode() === 'token' || auth.signedIn()) {
          <p class="small"><a routerLink="/guide" fragment="page-login"><app-icon name="help" [size]="14" /> How sign-in works</a></p>
        }
      </section>
    </div>
  `,
  styles: `
    .login-wrap { display: flex; justify-content: center; padding: 3rem 0 1rem; }
    .login { width: min(100%, 420px); }
    .login-head { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
    .login-head h1 { margin: 0; font-size: 1.3rem; }
    .login-head p { margin: 0.15rem 0 0; }
    .brand-mark {
      display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px; flex: none;
      border-radius: 11px; color: #fff; background: var(--series-1);
    }
    .login-form { display: grid; gap: 0.8rem; margin-top: 0.5rem; }
    .login-form .btn { justify-content: center; }
    .notes { padding-left: 1.1rem; margin: 1rem 0 0.5rem; display: grid; gap: 0.25rem; }
    a app-icon { vertical-align: -2px; }
  `,
})
export class LoginPage {
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  /** Where to go after signing in (`?next=`), bound by the router. */
  readonly nextParam = input<string | undefined>(undefined, { alias: 'next' });
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected username = '';
  protected password = '';

  protected next(): string {
    return safeNext(this.nextParam());
  }

  protected submit(): void {
    this.busy.set(true);
    this.error.set(null);
    this.auth.login(this.username.trim(), this.password).subscribe({
      next: () => {
        this.busy.set(false);
        this.password = '';
        void this.router.navigateByUrl(this.next());
      },
      error: (e) => {
        this.busy.set(false);
        this.password = '';
        this.error.set(backendError(e));
      },
    });
  }
}
