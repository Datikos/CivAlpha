import { httpResource } from '@angular/common/http';
import { Component, WritableSignal, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { ApiService, apiUrl, backendError, valueOf } from '../core/api';
import { AuthService, PASSWORD_MAX, PASSWORD_MIN } from '../core/auth.service';
import { FORMAT_PIPES } from '../core/format';
import { ApiToken, NewApiToken } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

interface Message {
  text: string;
  error: boolean;
}

type TokenState = 'active' | 'revoked' | 'expired';
const TOKEN_TONE: Record<TokenState, string> = { active: 'good', revoked: 'neutral', expired: 'warn' };
const TOKEN_GLYPH: Record<TokenState, string> = { active: '●', revoked: '✕', expired: '○' };

@Component({
  selector: 'app-account',
  imports: [FormsModule, RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="user" area="data" />
        <div>
          <h1>My account</h1>
          <p class="muted">
            Your password and the personal tokens that let an AI assistant or a script read CivAlpha as you.
            Accounts are created by the owner of this installation.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-account" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <app-status [res]="auth.resource" what="your account" />

    @if (auth.authMode() === 'token') {
      <app-verdict tone="info" title="Accounts are off on this installation.">
        It runs in token mode: one shared admin token protects admin and portfolio actions, and there is nobody to
        sign in as. The owner turns accounts on with <code>CIVALPHA_AUTH=accounts</code> after creating the first
        owner account on <a routerLink="/access">Access</a>.
      </app-verdict>
    } @else if (auth.caller(); as c) {
      <div class="stats">
        <div class="stat tone-info">
          <div class="stat-label">Signed in as <app-help text="Your username is fixed; the owner can change the display name." topic="page-account" label="signed in as" /></div>
          <div class="stat-value">{{ c.displayName || c.username || '—' }}</div>
          <div class="stat-sub">{{ c.username ? '@' + c.username : 'no user name' }} · {{ kindLabel[c.kind] }}</div>
        </div>
        <div class="stat" [class]="'stat tone-' + (c.role === 'OWNER' ? 'warn' : 'info')">
          <div class="stat-label">Role <app-help text="OWNER runs jobs, edits the universe and events and manages accounts. MEMBER reads every research page and keeps their own portfolios." topic="roles" label="role" /></div>
          <div class="stat-value">{{ c.role === 'OWNER' ? '★' : '●' }} {{ c.role }}</div>
          <div class="stat-sub">{{ c.role === 'OWNER' ? 'everything, including Access' : 'research read-only, your own portfolios' }}</div>
        </div>
        <div class="stat" [class]="'stat tone-' + (c.mustChangePassword ? 'bad' : 'good')">
          <div class="stat-label">Password <app-help text="12 to 200 characters, not containing your username. Changing it signs out every other session and stops every personal token." topic="page-account" label="password" /></div>
          <div class="stat-value">{{ c.mustChangePassword ? '⚠ to change' : '✓ your own' }}</div>
          <div class="stat-sub">{{ c.mustChangePassword ? 'set by the owner: choose yours' : 'chosen by you' }}</div>
        </div>
      </div>

      @if (c.kind !== 'SESSION') {
        <div class="alert alert-info small">
          This browser reaches CivAlpha with {{ kindLabel[c.kind] }}, not a password sign-in, so there is no password
          or personal token to manage here. <a routerLink="/login">Sign in</a> with your username to manage them.
        </div>
      } @else {
        <section class="card">
          <h2><app-icon name="lock" [size]="18" /> Change password <app-help text="Passwords are 12 to 200 characters and must not contain your username. Every other session and every personal token stop when it changes; this one stays signed in." topic="page-account" label="change password" /></h2>
          @if (c.mustChangePassword) {
            <app-verdict tone="warn" title="Choose your own password before you continue.">
              The owner set a first password for you. Until you replace it, every other page answers "change your password first".
            </app-verdict>
          }
          <form class="form-grid" (ngSubmit)="changePassword()">
            <label class="field">
              <span class="req">Current password</span>
              <input name="current" type="password" [(ngModel)]="pw.current" required autocomplete="current-password" />
            </label>
            <label class="field">
              <span class="req">New password</span>
              <input name="next" type="password" [(ngModel)]="pw.next" required autocomplete="new-password" [attr.maxlength]="max" />
              <span class="small" [class]="'small hint tone-' + (pw.next ? (lengthOk() ? 'good' : 'bad') : 'neutral')">
                {{ pw.next ? (lengthOk() ? '✓' : '✕') : '○' }} {{ pw.next.length }} characters (12 to 200)
              </span>
              @if (containsUsername()) {
                <span class="small hint tone-bad">✕ must not contain your username</span>
              }
            </label>
            <label class="field">
              <span class="req">Repeat the new password</span>
              <input name="repeat" type="password" [(ngModel)]="pw.repeat" required autocomplete="new-password" />
              @if (pw.repeat) {
                <span class="small" [class]="'small hint tone-' + (pw.repeat === pw.next ? 'good' : 'bad')">
                  {{ pw.repeat === pw.next ? '✓ matches' : '✕ does not match' }}
                </span>
              }
            </label>
            <div class="field">
              <span>&nbsp;</span>
              <button type="submit" class="btn btn-primary" [disabled]="busy() || !pw.current || !lengthOk() || containsUsername() || pw.repeat !== pw.next">Change password</button>
            </div>
          </form>
          @if (pwMessage(); as m) {
            <div class="alert small" [class.alert-error]="m.error" [class.alert-ok]="!m.error" role="status">{{ m.error ? '✕' : '✓' }} {{ m.text }}</div>
          }
        </section>

        @if (!c.mustChangePassword) {
          <section class="card">
            <h2><app-icon name="key" [size]="18" /> Personal tokens <app-help text="A token lets an AI assistant or a script call CivAlpha as you, with your role, for 1 to 90 days. It is shown once when created; only its prefix is kept for display. Revoke it when you stop using it." topic="personal-token" label="personal tokens" /></h2>
            <p class="small muted">
              For AI assistants and scripts: the assistant sends the token as <code>Authorization: Bearer cvt_…</code> and
              sees what you see. To connect Claude Code over MCP:
            </p>
            <pre class="cmd"><code>{{ mcpCommand() }}</code></pre>

            @if (created(); as t) {
              <div class="once tone-warn" role="status">
                <div class="once-head">
                  <strong>⚠ Copy it now: it is not shown again.</strong>
                  <button type="button" class="btn btn-sm" (click)="copy(t.token)"><app-icon name="copy" [size]="14" /> {{ copied() ? 'Copied' : 'Copy' }}</button>
                </div>
                <code class="token">{{ t.token }}</code>
                <div class="small muted">"{{ t.name }}" · expires {{ t.expiresAt | utc }}</div>
                <button type="button" class="btn btn-sm" (click)="created.set(null)">I have stored it</button>
              </div>
            }

            <form class="form-grid" (ngSubmit)="createToken()">
              <label class="field">
                <span class="req">Name</span>
                <input name="tname" [(ngModel)]="tokenDraft.name" required maxlength="100" placeholder="e.g. Claude Desktop" />
              </label>
              <label class="field">
                <span class="req">Valid for (days) <app-help text="1 to 90 days. A shorter life limits the damage if the token leaks." topic="personal-token" label="token life" /></span>
                <input name="tdays" type="number" min="1" max="90" step="1" [(ngModel)]="tokenDraft.days" required />
              </label>
              <div class="field">
                <span>&nbsp;</span>
                <button type="submit" class="btn btn-primary" [disabled]="busy() || !tokenDraft.name.trim() || !daysOk()"><app-icon name="plus" [size]="15" /> Create token</button>
              </div>
            </form>
            @if (tokenMessage(); as m) {
              <div class="alert small" [class.alert-error]="m.error" [class.alert-ok]="!m.error" role="status">{{ m.error ? '✕' : '✓' }} {{ m.text }}</div>
            }

            <app-status [res]="tokensRes" what="your tokens" />
            @if (tokens(); as list) {
              @if (list.length) {
                <div class="table-wrap">
                  <table class="table compact">
                    <thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th>Expires</th><th>Status</th><th></th></tr></thead>
                    <tbody>
                      @for (t of list; track t.id) {
                        <tr [class.dim]="stateOf(t) !== 'active'">
                          <td><strong>{{ t.name }}</strong></td>
                          <td class="mono">{{ t.prefix }}…</td>
                          <td class="nowrap">{{ t.createdAt | utc }}</td>
                          <td class="nowrap">{{ t.lastUsedAt ? (t.lastUsedAt | utc) : 'never' }}</td>
                          <td class="nowrap">{{ t.expiresAt | utc }}</td>
                          <td><span class="chip" [class]="'chip tone-' + tokenTone[stateOf(t)]">{{ tokenGlyph[stateOf(t)] }} {{ stateOf(t) }}</span></td>
                          <td class="actions">
                            @if (t.active) {
                              <button type="button" class="btn btn-sm" (click)="revoke(t)" [disabled]="busy()">Revoke</button>
                            }
                          </td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
              } @else {
                <div class="empty-box">No personal tokens. Create one above when an assistant or a script needs to read CivAlpha as you.</div>
              }
            }
          </section>
        }
      }
    }
  `,
  styles: `
    section.card { margin-bottom: 1rem; }
    section.card h2 { display: flex; align-items: center; gap: 0.4rem; }
    .hint { color: var(--tone-ink); }
    .cmd { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 0.55rem 0.75rem; overflow-x: auto; font-size: 0.82rem; }
    .once {
      margin: 0.75rem 0 1rem; padding: 0.75rem; display: grid; gap: 0.45rem;
      border: 1px solid var(--tone-mark); border-left-width: 4px; border-radius: var(--radius);
      background: var(--tone-bg);
    }
    .once strong { color: var(--tone-ink); }
    .once-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
    .token { font-size: 0.9rem; word-break: break-all; background: var(--surface); padding: 0.4rem 0.6rem; border-radius: var(--radius-sm); }
    .once .btn { justify-self: start; }
    tr.dim td { opacity: 0.65; }
    section.card .table-wrap, section.card .empty-box { margin-top: 1rem; }
    .actions { text-align: right; white-space: nowrap; }
  `,
})
export class AccountPage {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly auth = inject(AuthService);
  protected readonly max = PASSWORD_MAX;
  protected readonly tokenTone = TOKEN_TONE;
  protected readonly tokenGlyph = TOKEN_GLYPH;
  protected readonly kindLabel: Record<string, string> = {
    SESSION: 'password sign-in',
    API_TOKEN: 'a personal token',
    ADMIN_TOKEN: 'the admin token',
    LOCAL: 'local access',
  };

  /** Only a password sign-in has tokens to list, and not while the password must change first. */
  protected readonly tokensRes = httpResource<ApiToken[]>(() =>
    this.auth.accountsMode() && this.auth.hasAccount() && !this.auth.mustChangePassword() ? apiUrl.authTokens() : undefined,
  );
  protected readonly tokens = computed(() => valueOf(this.tokensRes));

  protected readonly busy = signal(false);
  protected readonly pwMessage = signal<Message | null>(null);
  protected readonly tokenMessage = signal<Message | null>(null);
  protected readonly created = signal<NewApiToken | null>(null);
  protected readonly copied = signal(false);
  protected pw = { current: '', next: '', repeat: '' };
  protected tokenDraft = { name: '', days: 30 };

  protected readonly mcpCommand = computed(
    () =>
      `claude mcp add --transport http civalpha ${location.origin}/mcp --header "Authorization: Bearer ${this.created()?.token ?? '<token>'}"`,
  );

  protected lengthOk(): boolean {
    return this.pw.next.length >= PASSWORD_MIN && this.pw.next.length <= PASSWORD_MAX;
  }

  protected containsUsername(): boolean {
    const u = this.auth.caller()?.username?.toLowerCase();
    return !!u && this.pw.next.toLowerCase().includes(u);
  }

  protected daysOk(): boolean {
    const d = Number(this.tokenDraft.days);
    return Number.isInteger(d) && d >= 1 && d <= 90;
  }

  protected stateOf(t: ApiToken): TokenState {
    if (t.revokedAt) return 'revoked';
    return t.active ? 'active' : 'expired';
  }

  protected changePassword(): void {
    const forced = this.auth.mustChangePassword();
    this.run(
      this.auth.changePassword(this.pw.current, this.pw.next),
      this.pwMessage,
      'Password changed. Every other session and every personal token has stopped.',
      () => {
        this.pw = { current: '', next: '', repeat: '' };
        this.tokensRes.reload();
        if (forced) void this.router.navigateByUrl('/');
      },
    );
  }

  protected createToken(): void {
    this.copied.set(false);
    this.run(
      this.api.createToken(this.tokenDraft.name.trim(), Number(this.tokenDraft.days)),
      this.tokenMessage,
      'Token created.',
      (t) => {
        this.created.set(t as NewApiToken);
        this.tokenDraft = { name: '', days: 30 };
        this.tokensRes.reload();
      },
    );
  }

  protected revoke(t: ApiToken): void {
    if (!confirm(`Revoke "${t.name}"? Whatever uses it stops working at once.`)) return;
    this.run(this.api.revokeToken(t.id), this.tokenMessage, `"${t.name}" revoked.`, () => this.tokensRes.reload());
  }

  protected copy(text: string): void {
    navigator.clipboard?.writeText(text).then(
      () => this.copied.set(true),
      () => this.copied.set(false),
    );
  }

  private run(
    obs: Observable<unknown>,
    target: WritableSignal<Message | null>,
    ok: string,
    after?: (v: unknown) => void,
  ): void {
    this.busy.set(true);
    target.set(null);
    obs.subscribe({
      next: (v) => {
        this.busy.set(false);
        target.set({ text: ok, error: false });
        after?.(v);
      },
      error: (e) => {
        this.busy.set(false);
        target.set({ text: backendError(e), error: true });
      },
    });
  }
}
