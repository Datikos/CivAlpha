-- ADR-0005: accounts, sessions, personal tokens, sign-in limits, audit, and portfolios owned per user.
-- Expand only for existing rows: portfolios keep working with owner_user_id NULL (the installation's own portfolio in
-- CIVALPHA_AUTH=token mode); creating the first OWNER account assigns them to it.
CREATE TABLE app_user (
    id                     BIGSERIAL PRIMARY KEY,
    username               TEXT        NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9][a-z0-9._-]{2,39}$'),
    display_name           TEXT        NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
    email                  TEXT        CHECK (length(email) <= 200),
    role                   TEXT        NOT NULL CHECK (role IN ('OWNER', 'MEMBER')),
    status                 TEXT        NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    password_hash          TEXT        NOT NULL,          -- scrypt$n$r$p$salt$hash; never returned, logged or audited
    must_change_password   BOOLEAN     NOT NULL DEFAULT true,
    failed_logins          INT         NOT NULL DEFAULT 0,
    locked_until           TIMESTAMPTZ,
    last_login_at          TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The cookie carries a random token; only its SHA-256 is stored.
CREATE TABLE user_session (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES app_user(id),
    token_sha256   TEXT        NOT NULL UNIQUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ NOT NULL,                  -- absolute expiry; idle expiry is last_seen_at + 12 hours
    revoked_at     TIMESTAMPTZ
);
CREATE INDEX user_session_user_idx ON user_session(user_id);

CREATE TABLE api_token (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES app_user(id),
    name           TEXT        NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    token_sha256   TEXT        NOT NULL UNIQUE,
    prefix         TEXT        NOT NULL,                  -- first characters, to recognise a token in the list
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at   TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ NOT NULL,
    revoked_at     TIMESTAMPTZ
);
CREATE INDEX api_token_user_idx ON api_token(user_id);

-- Sign-in attempts, for the per-address limit (the api runs several workers, so the count lives here).
CREATE TABLE login_attempt (
    id        BIGSERIAL PRIMARY KEY,
    ip        TEXT        NOT NULL,
    user_id   BIGINT      REFERENCES app_user(id),         -- null when the username matched nobody
    ok        BOOLEAN     NOT NULL,
    at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX login_attempt_ip_idx ON login_attempt(ip, at);

-- Who changed what: holdings, cash, portfolios, accounts, passwords, tokens. Never a password hash or token value.
CREATE TABLE audit_event (
    id             BIGSERIAL PRIMARY KEY,
    at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id  BIGINT      REFERENCES app_user(id),
    actor_kind     TEXT        NOT NULL CHECK (actor_kind IN ('USER', 'ADMIN_TOKEN', 'LOCAL', 'SYSTEM')),
    action         TEXT        NOT NULL,
    target_type    TEXT        NOT NULL,
    target_id      TEXT,
    portfolio_id   BIGINT,
    before         JSONB,
    after          JSONB
);
CREATE INDEX audit_event_at_idx ON audit_event(at DESC);
CREATE INDEX audit_event_portfolio_idx ON audit_event(portfolio_id, at DESC);

ALTER TABLE portfolio ADD COLUMN owner_user_id BIGINT REFERENCES app_user(id);
ALTER TABLE portfolio DROP CONSTRAINT portfolio_name_key;
CREATE UNIQUE INDEX portfolio_owner_name_idx ON portfolio (COALESCE(owner_user_id, 0), name);
CREATE INDEX portfolio_owner_idx ON portfolio(owner_user_id);
