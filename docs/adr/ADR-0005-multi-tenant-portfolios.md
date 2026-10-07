# ADR-0005: Several people, each with their own portfolio, behind real sign-in

**Status:** Accepted (2026-10-07, owner chose "1b, 2b")
**Date:** 2026-10-07
**Deciders:** David Sakhelashvili (owner); legal advice before anyone outside the owner's household is invited (see
Context, last point)

## Context

ADR-0004 (implemented 2026-10-07) gives one portfolio advice. The owner wants the platform multi-tenant: several people,
each entering their own holdings and getting their own advice.

What exists on 2026-10-07:

* **One credential.** `CIVALPHA_ADMIN_TOKEN` (`platform/app.py`, `protected_request`) guards `/api/admin/**`, every
  non-GET call and `/api/portfolio/**`. Whoever holds it can do everything; nothing records who did what. Without it
  everything is open, so Compose binds to 127.0.0.1 by default.
* **One portfolio.** `PortfolioService.portfolio_id` reads the row named `default`. Every portfolio table already carries
  `portfolio_id` (V18), so the data needs no reshaping, only an owner per portfolio and a scope on every query.
* **Shared research.** Universe, prices, filings, forecasts, the recorded book and its decisions are one dataset the
  owner operates (jobs, universe edits). The advice of every portfolio reads the same decision rows.
* **MCP.** `require_admin` checks the same shared token; an assistant acting for a person needs a credential of that
  person.
* **Outside constraints.** The price feed's licence decides whether its data may be shown to other people (the API's
  own disclaimer says so). Personalised buy and sell advice given to other people can be a regulated activity
  depending on the country. Neither is a code question; both must be answered before the first outside user.

## Decision

Answered by the owner on 2026-10-07: sign-in with username and password in CivAlpha (option B, over the recommended
Keycloak), and outside users are intended (open question 2, answer b).

1. **Two roles.** `OWNER` (jobs, universe, events, user management, everything there is today) and `MEMBER` (every
   research page read-only, their own portfolios, their own account). `CIVALPHA_ADMIN_TOKEN` stays valid as `OWNER`
   in both modes (scripts, stdio MCP, the bootstrap).
2. **An explicit mode.** `CIVALPHA_AUTH=token` (default, today's behaviour) or `accounts`. In `accounts` mode every
   `/api` and `/mcp` request needs a caller: a session cookie, a personal token or the admin token. Anonymous callers
   reach only `/health`, `GET /api/meta` (the sign-in page needs `authMode`) and `POST /api/auth/login|logout`. The
   reason is the data licence: with outside users nothing is shown to someone who has not signed in.
3. **One rule table.** `app.required_role(path, method, mode)` returns `NONE`, `USER` or `OWNER` and the existing
   middleware enforces it; routes do not carry their own checks. In `accounts` mode: `/api/admin/**` and every
   non-GET outside `/api/portfolio*` and `/api/auth/**` need `OWNER`; everything else under `/api` and `/mcp` needs a
   signed-in user. A caller who must change their password reaches only `/api/auth/me|password|logout` (403 otherwise).
4. **Passwords.** `hashlib.scrypt` (standard library, no new dependency): n = 2^15, r = 8, p = 1, 32-byte key, 16-byte
   random salt, stored as `scrypt$n$r$p$salt$hash`; about 32 MB and 50-100 ms per check, paid only at sign-in and
   password change. Policy: 12 to 200 characters, not containing the username. The owner creates every account with
   a first password; the user must change it at first sign-in. No self sign-up, no e-mail, so no self-service reset:
   the owner sets a new first password.
5. **Sessions.** A random 32-byte token in the cookie `civalpha_session` (HttpOnly, SameSite=Strict, Path=/, `Secure`
   unless `CIVALPHA_COOKIE_SECURE=false` for plain-http local use); the table `user_session` stores its SHA-256 only.
   Idle expiry 12 hours, absolute expiry 7 days. `last_seen_at` is written at most every 5 minutes. The lookup joins
   `app_user.status`, so a disabled user is refused on the next request. A cookie-authenticated non-GET must carry
   `X-CivAlpha-Request: 1` (403 otherwise); token and admin-token callers need not.
6. **Sign-in limits in the database** (the api runs 2 workers): 5 consecutive failures lock the account for 15 minutes;
   20 failures from one address in 15 minutes answer 429 for that address (`login_attempt`). The address is nginx's
   `X-Real-IP` (the api port is not published); a reverse proxy in front of nginx must pass the client address on.
   Unknown user, wrong password and locked account all answer the same 401, and an unknown user still costs one
   scrypt check. A failed sign-in is logged with the user id when one matched, never with the typed username.
7. **Personal tokens** for assistants and scripts: `cvt_` + 32 random bytes, shown once, SHA-256 stored, prefix kept
   for display, 1 to 90 days, revocable; sent as `Authorization: Bearer`. A password change or reset and disabling the
   user revoke every session (except the one changing its own password) and every token at once.
8. **Tenant isolation on the data.** Migration `V19__accounts.sql` (expand only): `app_user`, `user_session`,
   `api_token`, `login_attempt`, `audit_event`, `portfolio.owner_user_id` (nullable) and portfolio names unique per
   owner (the global unique name of V18 is dropped). `PortfolioService(caller)` scopes every statement with
   `owner_user_id IS NOT DISTINCT FROM :caller` (NULL is the installation's own portfolio in `token` mode); another
   user's portfolio id answers 404, never 403. Several portfolios per user (`GET/POST /api/portfolios`; the other
   portfolio endpoints take `portfolioId`, default the caller's first). Creating the first `OWNER` account assigns
   every portfolio without an owner to it.
9. **Audit.** `audit_event` (time, actor user id and kind, action, target, portfolio, before and after JSON) for every
   holding, cash and portfolio write and every user, password and token change. Password hashes and token values
   never enter it, a response or a log line.
10. **Bootstrap.** In `token` mode, with the admin token, the owner creates the first `OWNER` account
    (`POST /api/admin/users`, or `python -m civalpha.platform.users create-owner`), then sets `CIVALPHA_AUTH=accounts`.
11. **Go-live gate for outside users.** No account is created for anyone outside the owner's household until the
    data-licence and advice-regulation questions are answered (BACKLOG #11). The build does not wait for it.

**What option B gives up against C, now out of scope:** multi-factor sign-in, self-service password reset (no mail),
federation with another identity provider, self sign-up.

## Options considered

### Option A: owner-issued personal tokens only
Each person gets a long random token from the owner and pastes it once per browser tab, like the admin token today.
Pro: smallest change, no passwords, no new container. Con: token handling is the user's burden, no MFA, a lost token
needs the owner, and a browser tab is not a sign-in.

### Option B: username and password in the API (chosen by the owner, 2026-10-07)
argon2id hashes, login endpoint rate-limited per account and per address, session cookie. Pro: familiar UX, no new
container. Con: the platform stores passwords and owns reset, lockout and MFA; one more secret store to defend.

### Option C: OIDC with Keycloak in the stack (recommended, not chosen)
Pro: no passwords in CivAlpha, MFA and lockout come with the IdP, the owner's standing rule "OIDC for operators mapped
to a few roles", later federation (Google, a bank's IdP) without code. Con: one more container (about 512 MB RAM),
a realm to import and back up, the API must validate tokens.

### Option D: an external IdP only (Google sign-in)
Pro: no container. Con: needs internet and a registered client; an account the owner does not control decides access.

## Trade-off analysis

| | A tokens | B passwords | C Keycloak | D Google |
|---|---|---|---|---|
| Passwords stored by CivAlpha | no | yes | no | no |
| MFA | no | to build | yes | yes |
| New container | no | no | yes | no |
| Works offline | yes | yes | yes | no |
| UX for a non-technical user | poor | good | good | good |
| Code in CivAlpha | S | M | M | M |

## Consequences

**Easier:** each person sees only their own portfolios; every write has an actor; assistants act as a person, not as
the owner.

**Harder:** a sign-in screen and a session on every page; route-role rules to keep correct on every new endpoint
(one table, one test per route); password hashes and sessions to protect, and resets that only the owner can do; cross-tenant refusal tests on every portfolio change.

**Revisit:** the data licence and the advice question before the first outside user; the NOT NULL contract of
`portfolio.owner_user_id` once the owner has claimed `default`.

**Out of scope here:** multi-factor sign-in, self-service password reset, federation, self sign-up, billing, per-user universes or books, sharing a portfolio between users,
deleting a user's data, public internet exposure (stays behind the owner's network or a reverse proxy with TLS).

## Action items

### Phase 1: identity
1. [x] `db/migration/V19__accounts.sql` (Decision 8 tables).
2. [x] `platform/auth.py`: scrypt, policy, sessions, tokens, sign-in limits, callers; `platform/users.py` (accounts,
   audit, CLI `create-owner`); settings `CIVALPHA_AUTH`, `CIVALPHA_COOKIE_SECURE`; `.env.example`.
3. [x] `app.required_role` + middleware (CSRF header, must-change-password); `api/auth.py`, `/api/admin/users`,
   `/api/admin/audit`; `meta.authMode`.

### Phase 2: tenancy
4. [x] `PortfolioService(caller)`, `/api/portfolios`, `portfolioId` on the portfolio endpoints, audit writes, pipeline
   advises every portfolio of an active user.
5. [x] MCP: `mcp_server.caller_of` (built on `auth.caller_from`); research tools need a caller in `accounts` mode, job tools `OWNER`, portfolio tools
   act as the caller.
6. [x] Tests: role table per route, anonymous 401, MEMBER on admin 403, missing CSRF header 403, cross-tenant read and
   write 404, lockout, per-address 429, revoked token, disabled user with a live cookie, password change revokes
   sessions and tokens, must-change-password gate (`backend/tests/test_accounts.py`, 38; MCP in
   `test_platform_mcp.py`).
   *Found while implementing:* a sign-in request that already carries a session cookie needs the CSRF header like any
   other cookie write; the browser always sends it. The middleware resolves the caller off the event loop
   (`run_in_threadpool`) and touches the database only when a cookie or a personal token is presented. The portfolio
   service refuses an anonymous caller itself, so a route that forgets the role table cannot fall back to the
   installation's portfolios.

### Phase 3: UI and docs
7. [x] Sign-in page, guard, user menu, Account page (password, tokens), Access page (users, audit), portfolio switcher
   (`frontend/src/app/pages/login.ts`, `account.ts`, `access.ts`, `core/auth.service.ts`, `core/auth.guard.ts`).
   *Found while implementing:* the browser stops sending a stored admin token once a person is signed in with a
   password, since the backend lets the admin token win and would act as the installation, not as that person.
   `GET /api/meta` joined the routes open during a forced password change, so the app shell loads.
8. [x] README (security, accounts, bootstrap, gaps), `docs/api.md`, guide entries, BACKLOG.
   *Verified 2026-10-07 on the live stack with a temporary accounts-mode run:* forced password change, CSRF refusal,
   member refused on `/api/admin`, another user's portfolio 404, lockout after 5 failures, anonymous `/portfolio`
   sent to the sign-in page. *Found:* through Docker Desktop every sign-in arrives from the gateway address
   (`172.27.0.1`), so the per-address limit is shared by everyone there (README Security).

## Open questions for the owner

Answered 2026-10-07: 1 (b) username and password; 2 (b) outside users, behind the go-live gate of Decision 11.
