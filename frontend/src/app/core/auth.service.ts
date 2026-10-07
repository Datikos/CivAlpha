import { httpResource } from '@angular/common/http';
import { Injectable, computed, inject } from '@angular/core';
import { toObservable } from '@angular/core/rxjs-interop';
import { Observable, filter, map, take, tap } from 'rxjs';
import { ApiService, apiUrl } from './api';
import { MetaService } from './meta.service';
import { AuthMe } from './models';

/** Password policy of the backend (ADR-0005): 12 to 200 characters, not containing the username. */
export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 200;

/**
 * Who is calling (ADR-0005). In `token` mode there is no sign-in UI: the caller is LOCAL, ADMIN_TOKEN or nobody.
 * In `accounts` mode the session cookie decides; sign-in, sign-out and a password change write the answer straight
 * into the resource (it has the /me shape), so a guard never reads a stale value while a reload is in flight.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private readonly metaService = inject(MetaService);

  readonly resource = httpResource<AuthMe>(() => apiUrl.authMe());

  readonly me = computed(() => (this.resource.hasValue() ? this.resource.value() : null));
  readonly authMode = computed(() => this.me()?.authMode ?? null);
  readonly accountsMode = computed(() => this.authMode() === 'accounts');
  readonly signedIn = computed(() => this.me()?.signedIn ?? false);
  readonly caller = computed(() => this.me()?.caller ?? null);
  readonly isOwner = computed(() => this.caller()?.role === 'OWNER');
  readonly mustChangePassword = computed(() => this.caller()?.mustChangePassword ?? false);
  /** A person signed in with a password (not a token): only they have a password and personal tokens. */
  readonly hasAccount = computed(() => this.caller()?.kind === 'SESSION');
  /** Owner-only pages and links are hidden only for a MEMBER in accounts mode. */
  readonly ownerView = computed(() => !this.accountsMode() || this.isOwner());

  /** The settled answer of /me: the value, or null when it failed (the API is down). Emits once the load is over. */
  private readonly settled$ = toObservable(
    computed(() => {
      const st = this.resource.status();
      if (st === 'error') return null;
      return st === 'resolved' || st === 'local' ? this.me() : undefined;
    }),
  );

  /** Waits for /me to finish loading (used by the route guard). */
  ready(): Observable<AuthMe | null> {
    return this.settled$.pipe(
      filter((v): v is AuthMe | null => v !== undefined),
      take(1),
    );
  }

  login(username: string, password: string): Observable<AuthMe> {
    return this.api.login(username, password).pipe(tap((me) => this.settle(me)));
  }

  logout(): Observable<AuthMe> {
    const mode = this.authMode() ?? 'accounts';
    return this.api.logout().pipe(
      map(() => ({ authMode: mode, signedIn: false, caller: null }) as AuthMe),
      tap((me) => this.resource.set(me)),
    );
  }

  changePassword(currentPassword: string, newPassword: string): Observable<AuthMe> {
    return this.api.changePassword(currentPassword, newPassword).pipe(tap((me) => this.settle(me)));
  }

  /** Stores the new caller; /api/meta answers 403 while a password must change, so it is fetched again if it failed. */
  private settle(me: AuthMe): void {
    this.resource.set(me);
    if (this.metaService.resource.error()) this.metaService.resource.reload();
  }

  reload(): void {
    this.resource.reload();
  }
}
