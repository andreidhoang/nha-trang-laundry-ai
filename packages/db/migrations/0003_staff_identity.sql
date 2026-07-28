-- Named staff identity, database-authoritative roles, and revocable opaque sessions.

CREATE TABLE staff_users (
    id UUID PRIMARY KEY,
    oidc_subject TEXT NOT NULL UNIQUE CHECK (length(oidc_subject) BETWEEN 1 AND 255),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    email TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DISABLED')),
    authorization_version BIGINT NOT NULL DEFAULT 1 CHECK (authorization_version > 0),
    created_at TIMESTAMPTZ NOT NULL,
    disabled_at TIMESTAMPTZ NULL
);

CREATE TABLE staff_role_assignments (
    id UUID PRIMARY KEY,
    staff_user_id UUID NOT NULL REFERENCES staff_users(id),
    role TEXT NOT NULL CHECK (role IN (
        'OWNER_ADMIN', 'OPS_APPROVER', 'OPERATOR', 'DRIVER', 'ACCOUNTANT', 'AUDITOR'
    )),
    assigned_at TIMESTAMPTZ NOT NULL,
    assigned_by UUID NULL REFERENCES staff_users(id),
    revoked_at TIMESTAMPTZ NULL,
    revoked_by UUID NULL REFERENCES staff_users(id),
    UNIQUE (staff_user_id, role)
);

CREATE INDEX staff_role_assignments_active_idx
    ON staff_role_assignments (staff_user_id, role)
    WHERE revoked_at IS NULL;

CREATE TABLE staff_sessions (
    id UUID PRIMARY KEY,
    staff_user_id UUID NOT NULL REFERENCES staff_users(id),
    secret_hash TEXT NOT NULL UNIQUE CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
    authorization_version BIGINT NOT NULL CHECK (authorization_version > 0),
    mfa_verified BOOLEAN NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    idle_expires_at TIMESTAMPTZ NOT NULL,
    absolute_expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL,
    CHECK (idle_expires_at <= absolute_expires_at),
    CHECK (last_seen_at >= issued_at)
);

CREATE INDEX staff_sessions_active_idx
    ON staff_sessions (staff_user_id, absolute_expires_at)
    WHERE revoked_at IS NULL;
