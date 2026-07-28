-- Immutable versioned configuration. Applied only by the migration identity.

CREATE TABLE configuration_versions (
    id UUID PRIMARY KEY,
    config_type TEXT NOT NULL CHECK (config_type ~ '^[A-Z][A-Z0-9_]{1,62}$'),
    version INTEGER NOT NULL CHECK (version > 0),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    payload JSONB NOT NULL,
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    published_by UUID NULL,
    published_at TIMESTAMPTZ NULL,
    CHECK (
        (lifecycle = 'DRAFT' AND published_by IS NULL AND published_at IS NULL)
        OR (lifecycle IN ('PUBLISHED', 'RETIRED') AND published_by IS NOT NULL AND published_at IS NOT NULL)
    ),
    UNIQUE (config_type, version)
);

CREATE INDEX configuration_versions_published_idx
    ON configuration_versions (config_type, version DESC)
    WHERE lifecycle = 'PUBLISHED';

CREATE FUNCTION reject_published_configuration_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.lifecycle IN ('PUBLISHED', 'RETIRED') THEN
        RAISE EXCEPTION 'published configuration versions are immutable; create a new version';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER configuration_versions_immutable_after_publication
    BEFORE UPDATE OR DELETE ON configuration_versions
    FOR EACH ROW EXECUTE FUNCTION reject_published_configuration_mutation();
