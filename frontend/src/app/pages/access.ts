import { httpResource } from '@angular/common/http';
import { JsonPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { ApiService, apiUrl, backendError, valueOf } from '../core/api';
import { AuthService, PASSWORD_MAX, PASSWORD_MIN } from '../core/auth.service';
import { FORMAT_PIPES } from '../core/format';
import { MetaService } from '../core/meta.service';
import { AppUser, AuditEvent, UserRole } from '../core/models';
import { Icon } from '../shared/icon';
import { UI } from '../shared/ui';
import { VIZ } from '../shared/viz';

const USERNAME = /^[a-z0-9][a-z0-9._-]{2,39}$/;
/** No look-alike characters (0/O, 1/l/I), so a password read aloud or copied from paper survives. */
const PASSWORD_CHARS = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#%+=';

/** A random 16-character first password from the browser's cryptographic generator. */
export function generatePassword(length = 16): string {
  const out: string[] = [];
  const buf = new Uint32Array(1);
  const limit = Math.floor(0x1_0000_0000 / PASSWORD_CHARS.length) * PASSWORD_CHARS.length;
  while (out.length < length) {
    crypto.getRandomValues(buf);
    if (buf[0] < limit) out.push(PASSWORD_CHARS[buf[0] % PASSWORD_CHARS.length]);
  }
  return out.join('');
}

const ACTION_TONE: Record<string, string> = {
  USER_CREATED: 'good',
  USER_UPDATED: 'info',
  PASSWORD_RESET: 'warn',
  PASSWORD_CHANGED: 'info',
  TOKEN_CREATED: 'good',
  TOKEN_REVOKED: 'warn',
  PORTFOLIO_CREATED: 'good',
  HOLDING_SET: 'info',
  HOLDING_REMOVED: 'warn',
  CASH_SET: 'info',
};
const ACTION_GLYPH: Record<string, string> = {
  USER_CREATED: '+',
  USER_UPDATED: '✎',
  PASSWORD_RESET: '⟲',
  PASSWORD_CHANGED: '✓',
  TOKEN_CREATED: '+',
  TOKEN_REVOKED: '✕',
  PORTFOLIO_CREATED: '+',
  HOLDING_SET: '✎',
  HOLDING_REMOVED: '−',
  CASH_SET: '✎',
};

interface Message {
  text: string;
  error: boolean;
}

@Component({
  selector: 'app-access',
  imports: [FormsModule, JsonPipe, RouterLink, Icon, ...UI, ...VIZ, ...FORMAT_PIPES],
  template: `
    <div class="page-head">
      <div class="page-title">
        <app-page-icon name="shield" area="data" />
        <div>
          <h1>Access</h1>
          <p class="muted">
            Who may sign in and with which role, and the record of every account, password, token and portfolio
            change. Only the owner sees this page.
          </p>
        </div>
      </div>
      <div class="page-actions">
        <a routerLink="/guide" fragment="page-access" class="btn btn-help"><app-icon name="help" [size]="16" /> How to read this</a>
      </div>
    </div>

    <app-verdict tone="warn" title="Outside users:">
      do not create accounts for people outside your household until the data-licence and advice-regulation questions
      are answered (BACKLOG #11).
    </app-verdict>

    @if (tokenMode()) {
      <div class="alert alert-info small">
        <strong>This installation runs in token mode.</strong> This page works with the admin token: it is how the first
        owner account is created. After creating your owner account set <code>CIVALPHA_AUTH=accounts</code> and restart the api.
        @if (meta.meta()?.adminTokenRequired) { Enter the admin token on <a routerLink="/admin">Data &amp; pipeline</a> first. }
      </div>
    }
    @if (message(); as m) {
      <div class="alert small" [class.alert-error]="m.error" [class.alert-ok]="!m.error" role="status">{{ m.error ? '✕' : '✓' }} {{ m.text }}</div>
    }

    <app-status [res]="usersRes" what="the accounts" />
    @if (users(); as list) {
      <div class="stats">
        <div class="stat tone-info">
          <div class="stat-label">Accounts <app-help text="Every account on this installation, active or disabled. Accounts are never deleted, so the audit trail keeps its names." topic="page-access" label="accounts" /></div>
          <div class="stat-value">{{ list.length }}</div>
          <div class="stat-sub">{{ counts().active }} active · {{ counts().disabled }} disabled</div>
        </div>
        <div class="stat" [class]="'stat tone-' + (counts().owners ? 'good' : 'warn')">
          <div class="stat-label">Owners <app-help text="OWNER runs jobs, edits the universe and events and manages accounts. The last active owner cannot be demoted or disabled." topic="roles" label="owners" /></div>
          <div class="stat-value">{{ counts().owners ? '★' : '⚠' }} {{ counts().owners }}</div>
          <div class="stat-sub">{{ counts().members }} member{{ counts().members === 1 ? '' : 's' }}</div>
        </div>
        <div class="stat" [class]="'stat tone-' + (counts().attention ? 'warn' : 'good')">
          <div class="stat-label">Need attention <app-help text="Accounts locked after 5 failed sign-ins (for 15 minutes) or still on the first password the owner set." topic="page-access" label="need attention" /></div>
          <div class="stat-value">{{ counts().attention ? '⚠' : '✓' }} {{ counts().attention }}</div>
          <div class="stat-sub">{{ counts().locked }} locked · {{ counts().mustChange }} on a first password</div>
        </div>
      </div>

      <section class="card">
        <h2><app-icon name="users" [size]="18" /> Accounts</h2>
        @if (list.length) {
          <div class="table-wrap">
            <table class="table compact">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role <app-help text="Changing a role signs the user out everywhere and stops their tokens." topic="roles" label="role" /></th>
                  <th>Status <app-help text="DISABLED cannot sign in. Locked: 5 failed sign-ins in a row lock the account for 15 minutes; the chip shows until when." topic="page-access" label="status" /></th><th>Password</th><th>Last sign-in</th><th><span class="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                @for (u of list; track u.id) {
                  <tr [class.dim]="u.status === 'DISABLED'">
                    <td>
                      <strong>{{ u.username }}</strong>@if (u.id === auth.caller()?.userId) { <span class="small muted"> (you)</span> }
                      <div class="small">{{ u.displayName }}@if (u.email) { <span class="muted"> · {{ u.email }}</span> }</div>
                    </td>
                    <td>
                      <label class="sr-only" [for]="'role-' + u.id">Role of {{ u.username }}</label>
                      <select #roleSel [id]="'role-' + u.id" [value]="u.role" (change)="setRole(u, roleSel)" [disabled]="busy()">
                        <option value="OWNER">★ OWNER</option>
                        <option value="MEMBER">● MEMBER</option>
                      </select>
                    </td>
                    <td>
                      <span class="chip" [class]="'chip tone-' + (u.status === 'ACTIVE' ? 'good' : 'neutral')">{{ u.status === 'ACTIVE' ? '●' : '○' }} {{ u.status }}</span>
                      @if (isLocked(u)) {
                        <div><span class="chip tone-bad" title="5 failed sign-ins in a row; the lock ends by itself, or set a new first password to clear it">✕ locked until {{ u.lockedUntil | utc }}</span></div>
                      }
                    </td>
                    <td>
                      @if (u.mustChangePassword) {
                        <span class="chip tone-warn" title="Still on the first password the owner set; the user must choose their own at the next sign-in">⚠ first password</span>
                      } @else {
                        <span class="chip tone-good" title="Chosen by the user">✓ own</span>
                      }
                    </td>
                    <td class="nowrap small">{{ u.lastLoginAt ? (u.lastLoginAt | utc) : 'never' }}</td>
                    <td class="actions">
                      <button type="button" class="btn btn-sm" (click)="toggleStatus(u)" [disabled]="busy()">{{ u.status === 'ACTIVE' ? 'Disable' : 'Enable' }}</button>
                      <button type="button" class="btn btn-sm" (click)="openReset(u)" [disabled]="busy()">Set new first password</button>
                    </td>
                  </tr>
                  @if (resetFor() === u.id) {
                    <tr class="reset-row">
                      <td colspan="6">
                        <form class="inline-form" (ngSubmit)="reset(u)">
                          <label class="field">
                            <span class="req">New first password for {{ u.username }}</span>
                            <input name="resetPw" type="text" [(ngModel)]="resetPassword" required autocomplete="off" spellcheck="false" [attr.maxlength]="max" />
                          </label>
                          <button type="button" class="btn btn-sm" (click)="resetPassword = generate()">Generate</button>
                          <button type="submit" class="btn btn-sm btn-primary" [disabled]="busy() || !passwordOk(resetPassword, u.username)">Set password</button>
                          <button type="button" class="btn btn-sm" (click)="resetFor.set(null)">Cancel</button>
                        </form>
                        <p class="small muted">
                          {{ passwordHint(resetPassword, u.username) }} Hand it over yourself. It stops every session and token of {{ u.username }},
                          clears a lock, and {{ u.username }} must choose their own password at the next sign-in.
                        </p>
                      </td>
                    </tr>
                  }
                }
              </tbody>
            </table>
          </div>
        } @else {
          <div class="empty-box">No accounts yet. Create the first one below with the role OWNER: it takes over every portfolio entered so far.</div>
        }
      </section>
    }

    <section class="card">
      <h2><app-icon name="plus" [size]="18" /> Create an account <app-help text="The person signs in with this username and the first password you hand over, then must choose their own. Usernames are 3 to 40 lower-case letters, digits, '.', '_' or '-'. There is no self sign-up." topic="page-access" label="create an account" /></h2>
      <form class="form-grid" (ngSubmit)="create()">
        <label class="field">
          <span class="req">Username</span>
          <input name="username" [(ngModel)]="draft.username" required maxlength="40" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="e.g. anna" />
          @if (draft.username && !usernameOk()) {
            <span class="small hint tone-bad">✕ 3 to 40 lower-case letters, digits, '.', '_' or '-'</span>
          }
        </label>
        <label class="field">
          <span class="req">Display name</span>
          <input name="displayName" [(ngModel)]="draft.displayName" required maxlength="100" placeholder="e.g. Anna" />
        </label>
        <label class="field">
          <span>E-mail</span>
          <input name="email" type="email" [(ngModel)]="draft.email" maxlength="200" placeholder="optional, for your records" />
        </label>
        <label class="field">
          <span class="req">Role <app-help text="MEMBER: every research page read-only, their own portfolios and account. OWNER: everything, including this page." topic="roles" label="role" /></span>
          <select name="role" [(ngModel)]="draft.role">
            <option value="MEMBER">● MEMBER</option>
            <option value="OWNER">★ OWNER</option>
          </select>
        </label>
        <label class="field wide">
          <span class="req">First password <app-help text="12 to 200 characters, not containing the username. The user must replace it at the first sign-in. Generate makes a random 16-character one and shows it so you can hand it over." topic="page-access" label="first password" /></span>
          <span class="pw-line">
            <input name="password" type="text" [(ngModel)]="draft.password" required autocomplete="off" spellcheck="false" [attr.maxlength]="max" />
            <button type="button" class="btn btn-sm" (click)="draft.password = generate()">Generate</button>
          </span>
          <span class="small" [class]="'small hint tone-' + (draft.password ? (passwordOk(draft.password, draft.username) ? 'good' : 'bad') : 'neutral')">
            {{ draft.password ? (passwordOk(draft.password, draft.username) ? '✓' : '✕') : '○' }} {{ passwordHint(draft.password, draft.username) }}
          </span>
        </label>
        <div class="field">
          <button type="submit" class="btn btn-primary" [disabled]="busy() || !usernameOk() || !draft.displayName.trim() || !passwordOk(draft.password, draft.username)">Create account</button>
        </div>
      </form>
    </section>

    <section class="card">
      <h2>
        <app-icon name="history" [size]="18" /> Audit trail
        <app-help text="Every account, password, token, portfolio, holding and cash change, newest first: who did it, to what, and the values before and after. Password hashes and token values never enter it." topic="page-access" label="audit trail" />
      </h2>
      <div class="filters">
        <label class="field">
          <span>Done by</span>
          <select [ngModel]="auditUser()" (ngModelChange)="auditUser.set($event)">
            <option [ngValue]="null">everyone</option>
            @for (u of users() ?? []; track u.id) {
              <option [ngValue]="u.id">{{ u.username }}</option>
            }
          </select>
        </label>
        <span class="small muted">the newest {{ auditLimit }} entries</span>
      </div>
      <app-status [res]="auditRes" what="the audit trail" />
      @if (audit(); as rows) {
        @if (rows.length) {
          <div class="table-wrap">
            <table class="table compact">
              <thead><tr><th>Time</th><th>Done by</th><th>Action</th><th>Target</th><th class="num">Portfolio</th><th>Change</th></tr></thead>
              <tbody>
                @for (e of rows; track e.id) {
                  <tr>
                    <td class="nowrap small">{{ e.at | utc }}</td>
                    <td>@if (e.actorUsername) { <strong>{{ e.actorUsername }}</strong> } @else { <span class="muted">{{ e.actorKind }}</span> }</td>
                    <td><span class="chip" [class]="'chip tone-' + (actionTone[e.action] ?? 'neutral')">{{ actionGlyph[e.action] ?? '•' }} {{ e.action }}</span></td>
                    <td class="small">{{ e.targetType ?? '—' }}@if (e.targetId !== null) { #{{ e.targetId }} }</td>
                    <td class="num">{{ e.portfolioId ?? '—' }}</td>
                    <td>
                      @if (e.before !== null || e.after !== null) {
                        <details>
                          <summary class="small">before / after</summary>
                          <div class="ba">
                            <div><span class="small muted">before</span><pre>{{ e.before | json }}</pre></div>
                            <div><span class="small muted">after</span><pre>{{ e.after | json }}</pre></div>
                          </div>
                        </details>
                      } @else {
                        <span class="muted">—</span>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <div class="empty-box">No changes recorded{{ auditUser() !== null ? ' for this user' : '' }} yet.</div>
        }
      }
    </section>
  `,
  styles: `
    section.card { margin: 1rem 0; }
    section.card h2 { display: flex; align-items: center; gap: 0.4rem; }
    .hint { color: var(--tone-ink); }
    .pw-line { display: flex; gap: 0.5rem; align-items: center; }
    .pw-line input { flex: 1; font-family: var(--mono, monospace); }
    .inline-form { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: flex-end; }
    .inline-form input { font-family: var(--mono, monospace); min-width: 16rem; }
    .reset-row td { background: var(--surface-2); }
    tr.dim td { opacity: 0.65; }
    .actions { text-align: right; white-space: nowrap; }
    .actions .btn + .btn { margin-left: 0.3rem; }
    td .chip + div, td div > .chip { margin-top: 0.25rem; }
    select { padding: 0.2rem 0.4rem; }
    details summary { cursor: pointer; }
    .ba { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.5rem; margin-top: 0.35rem; }
    .ba pre { margin: 0.2rem 0 0; font-size: 0.75rem; max-height: 14rem; overflow: auto; background: var(--surface-2); padding: 0.4rem; border-radius: var(--radius-sm); }
  `,
})
export class AccessPage {
  private readonly api = inject(ApiService);
  protected readonly auth = inject(AuthService);
  protected readonly meta = inject(MetaService);
  protected readonly max = PASSWORD_MAX;
  protected readonly auditLimit = 200;
  protected readonly actionTone = ACTION_TONE;
  protected readonly actionGlyph = ACTION_GLYPH;

  protected readonly usersRes = httpResource<AppUser[]>(() => apiUrl.adminUsers());
  protected readonly auditUser = signal<number | null>(null);
  protected readonly auditRes = httpResource<AuditEvent[]>(() => apiUrl.adminAudit(this.auditLimit, this.auditUser()));
  protected readonly users = computed(() => valueOf(this.usersRes));
  protected readonly audit = computed(() => valueOf(this.auditRes));
  protected readonly tokenMode = computed(() => (this.auth.authMode() ?? this.meta.meta()?.authMode) === 'token');

  protected readonly busy = signal(false);
  protected readonly message = signal<Message | null>(null);
  protected readonly resetFor = signal<number | null>(null);
  protected resetPassword = '';
  protected draft = { username: '', displayName: '', email: '', role: 'MEMBER' as UserRole, password: '' };

  protected readonly counts = computed(() => {
    const list = this.users() ?? [];
    const locked = list.filter((u) => this.isLocked(u)).length;
    const mustChange = list.filter((u) => u.status === 'ACTIVE' && u.mustChangePassword).length;
    return {
      active: list.filter((u) => u.status === 'ACTIVE').length,
      disabled: list.filter((u) => u.status === 'DISABLED').length,
      owners: list.filter((u) => u.role === 'OWNER' && u.status === 'ACTIVE').length,
      members: list.filter((u) => u.role === 'MEMBER').length,
      locked,
      mustChange,
      attention: list.filter((u) => this.isLocked(u) || (u.status === 'ACTIVE' && u.mustChangePassword)).length,
    };
  });

  protected generate = () => generatePassword(16);

  protected isLocked(u: AppUser): boolean {
    return !!u.lockedUntil && new Date(u.lockedUntil).getTime() > Date.now();
  }

  protected usernameOk(): boolean {
    return USERNAME.test(this.draft.username.trim());
  }

  protected passwordOk(pw: string, username: string): boolean {
    const u = username.trim().toLowerCase();
    return pw.length >= PASSWORD_MIN && pw.length <= PASSWORD_MAX && !(u && pw.toLowerCase().includes(u));
  }

  protected passwordHint(pw: string, username: string): string {
    const u = username.trim().toLowerCase();
    if (u && pw && pw.toLowerCase().includes(u)) return 'It must not contain the username.';
    return `${pw.length} characters (12 to 200).`;
  }

  protected setRole(u: AppUser, select: HTMLSelectElement): void {
    const role = select.value as UserRole;
    if (role === u.role) return;
    if (!confirm(`Make ${u.username} ${role}? Their sessions and tokens stop; they sign in again.`)) {
      select.value = u.role;
      return;
    }
    this.run(this.api.editUser(u.id, { role }), `${u.username} is now ${role}.`, undefined, () => (select.value = u.role));
  }

  protected toggleStatus(u: AppUser): void {
    const status = u.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE';
    const text =
      status === 'DISABLED'
        ? `Disable ${u.username}? Their sessions and tokens stop at once and they cannot sign in.`
        : `Enable ${u.username}? They can sign in again with their password.`;
    if (!confirm(text)) return;
    this.run(this.api.editUser(u.id, { status }), `${u.username} is ${status === 'ACTIVE' ? 'enabled' : 'disabled'}.`);
  }

  protected openReset(u: AppUser): void {
    this.resetPassword = '';
    this.resetFor.set(this.resetFor() === u.id ? null : u.id);
  }

  protected reset(u: AppUser): void {
    const pw = this.resetPassword;
    this.run(
      this.api.resetUserPassword(u.id, pw),
      `New first password set for ${u.username}: ${pw} (shown this once; hand it over yourself).`,
      () => {
        this.resetFor.set(null);
        this.resetPassword = '';
      },
    );
  }

  protected create(): void {
    const pw = this.draft.password;
    const name = this.draft.username.trim();
    this.run(
      this.api.createUser({
        username: name,
        displayName: this.draft.displayName.trim(),
        role: this.draft.role,
        password: pw,
        email: this.draft.email.trim() || null,
      }),
      `${name} created with the first password ${pw} (hand it over yourself; they must change it at the first sign-in).`,
      () => (this.draft = { username: '', displayName: '', email: '', role: 'MEMBER', password: '' }),
    );
  }

  private run(obs: Observable<unknown>, ok: string, after?: () => void, onError?: () => void): void {
    this.busy.set(true);
    this.message.set(null);
    obs.subscribe({
      next: () => {
        this.busy.set(false);
        this.message.set({ text: ok, error: false });
        after?.();
        this.usersRes.reload();
        this.auditRes.reload();
      },
      error: (e) => {
        this.busy.set(false);
        this.message.set({ text: backendError(e), error: true });
        onError?.();
      },
    });
  }
}
