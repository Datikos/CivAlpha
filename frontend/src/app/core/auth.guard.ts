import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';

/**
 * Route guard (ADR-0005). Token mode: always allowed (the backend checks the admin token per call). Accounts mode:
 * waits for /api/auth/me, then sends an anonymous visitor to /login?next=…, a user who must change the password to
 * /account, and a MEMBER away from owner-only routes (`data: { ownerOnly: true }`) to the dashboard. When /me
 * cannot be loaded the route opens and the page shows the API error.
 */
export const authGuard: CanActivateFn = (route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.ready().pipe(
    map((me) => {
      if (!me || me.authMode !== 'accounts') return true;
      if (!me.signedIn || !me.caller) return router.createUrlTree(['/login'], { queryParams: { next: state.url } });
      if (me.caller.mustChangePassword && !state.url.startsWith('/account')) return router.createUrlTree(['/account']);
      if (route.data['ownerOnly'] && me.caller.role !== 'OWNER') return router.createUrlTree(['/']);
      return true;
    }),
  );
};
