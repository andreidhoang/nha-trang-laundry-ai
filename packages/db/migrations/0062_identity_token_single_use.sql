-- AUTHZ-LIFECYCLE-001: one provider sign-in mints at most one staff session.
--
-- `POST /internal/v1/auth/session` trades a verified OIDC ID token for an opaque session. Until
-- now the same token could be traded again and again until it expired, so a token that leaked
-- once -- a proxy log, a browser extension, a crash report carrying the callback page's memory --
-- was a way to mint fresh 24-hour sessions for its whole lifetime, each invisible to the person
-- whose sign-in it was.
--
-- The digest of the token is stored with the session it produced, and uniqueness makes the second
-- exchange fail in the database, whichever API replica receives it. No new table and no purge job:
-- the digest lives and is retained exactly as long as its session row. NULL for sessions minted
-- before this migration, which were never bound to a token.

ALTER TABLE staff_sessions
    ADD COLUMN identity_token_digest TEXT NULL
        CHECK (identity_token_digest ~ '^[0-9a-f]{64}$');

CREATE UNIQUE INDEX staff_sessions_identity_token_digest_key
    ON staff_sessions (identity_token_digest)
    WHERE identity_token_digest IS NOT NULL;
