CREATE SCHEMA memoriesql;

CREATE TABLE memoriesql.schema_migrations (
    version integer PRIMARY KEY,
    name text NOT NULL,
    sha256 text NOT NULL,
    runner_contract_version integer NOT NULL,
    applied_at timestamp with time zone NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT schema_migrations_version_positive CHECK (version > 0),
    CONSTRAINT schema_migrations_name_nonempty CHECK (btrim(name) <> ''),
    CONSTRAINT schema_migrations_sha256_format CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT schema_migrations_runner_version_positive
        CHECK (runner_contract_version > 0)
);

CREATE FUNCTION memoriesql.reject_immutable_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER schema_migrations_immutable
BEFORE UPDATE OR DELETE ON memoriesql.schema_migrations
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE VIEW memoriesql.schema_version AS
SELECT
    COALESCE(max(version), 0) AS current_version,
    count(*) AS applied_migration_count,
    max(applied_at) AS last_migrated_at
FROM memoriesql.schema_migrations;

COMMENT ON TABLE memoriesql.schema_migrations IS
    'Append-only authority for the single ordered memorieSQL migration stream.';
COMMENT ON VIEW memoriesql.schema_version IS
    'Current schema metadata derived only from the ordered migration history.';
