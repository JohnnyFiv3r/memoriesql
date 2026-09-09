-- PR-02L durable evidence before interpretation.
-- Approved source bytes become immutable local evidence and advance a byte
-- checkpoint without creating source events, units, beads, or semantic tasks.

INSERT INTO memoriesql.capabilities (capability_key, description)
VALUES (
    'source.raw.inspect',
    'Inspect explicitly authorized local raw source evidence.'
), (
    'source.raw.read',
    'Read exact explicitly confirmed local raw source evidence.'
);

INSERT INTO memoriesql.role_capabilities (role_key, capability_key)
VALUES
    ('personal_owner', 'source.raw.inspect'),
    ('personal_owner', 'source.raw.read');

CREATE TABLE memoriesql.source_range_capture_receipts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_range_receipt_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    source_revision_key text NOT NULL,
    file_identity_key text NOT NULL,
    file_identity jsonb NOT NULL,
    capability_id text NOT NULL,
    connector_id text NOT NULL,
    observed_connector_version text,
    observed_source_format_version text,
    capture_surface text NOT NULL,
    byte_start bigint NOT NULL,
    byte_end_exclusive bigint NOT NULL,
    payload_byte_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    chunk_count smallint NOT NULL,
    capture_policy_id text NOT NULL,
    retention_policy_ref text NOT NULL,
    interpretation_status text NOT NULL,
    checkpoint_key text NOT NULL,
    checkpoint_sequence bigint NOT NULL,
    captured_by_principal_id uuid NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    CONSTRAINT source_range_capture_receipts_pk PRIMARY KEY (
        tenant_id, source_range_receipt_id
    ),
    CONSTRAINT source_range_capture_receipts_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ),
    CONSTRAINT source_range_capture_receipts_idempotency_uq UNIQUE (
        tenant_id, idempotency_receipt_id
    ),
    CONSTRAINT source_range_capture_receipts_natural_uq UNIQUE (
        tenant_id, source_object_id, source_revision_key, file_identity_key,
        byte_start, byte_end_exclusive
    ),
    CONSTRAINT source_range_capture_receipts_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT source_range_capture_receipts_idempotency_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT source_range_capture_receipts_principal_fk FOREIGN KEY (
        tenant_id, captured_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT source_range_capture_receipts_revision_nonempty CHECK (
        btrim(source_revision_key) <> '' AND length(source_revision_key) <= 512
    ),
    CONSTRAINT source_range_capture_receipts_file_identity CHECK (
        btrim(file_identity_key) <> ''
        AND length(file_identity_key) <= 256
        AND jsonb_typeof(file_identity) = 'object'
    ),
    CONSTRAINT source_range_capture_receipts_connector_shape CHECK (
        capability_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(capability_id) <= 128
        AND connector_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(connector_id) <= 128
        AND (
            observed_connector_version IS NULL
            OR length(observed_connector_version) BETWEEN 1 AND 128
        )
        AND (
            observed_source_format_version IS NULL
            OR length(observed_source_format_version) BETWEEN 1 AND 128
        )
    ),
    CONSTRAINT source_range_capture_receipts_surface_supported CHECK (
        capture_surface IN (
            'artifact', 'live_hook', 'harness', 'browser', 'import',
            'explicit_checkpoint', 'synthetic'
        )
    ),
    CONSTRAINT source_range_capture_receipts_byte_range CHECK (
        byte_start >= 0
        AND byte_end_exclusive > byte_start
        AND byte_end_exclusive - byte_start = payload_byte_count
        AND payload_byte_count BETWEEN 1 AND 262144
    ),
    CONSTRAINT source_range_capture_receipts_hash_shape CHECK (
        payload_sha256 ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT source_range_capture_receipts_chunk_count CHECK (
        chunk_count BETWEEN 1 AND 256
    ),
    CONSTRAINT source_range_capture_receipts_policy_shape CHECK (
        capture_policy_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(capture_policy_id) <= 128
        AND btrim(retention_policy_ref) <> ''
        AND length(retention_policy_ref) <= 256
    ),
    CONSTRAINT source_range_capture_receipts_pending CHECK (
        interpretation_status = 'pending_interpretation'
    ),
    CONSTRAINT source_range_capture_receipts_checkpoint_shape CHECK (
        checkpoint_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(checkpoint_key) <= 128
        AND checkpoint_sequence > 0
    )
);

CREATE TABLE memoriesql.captured_source_ranges (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_range_chunk_id uuid NOT NULL,
    source_range_receipt_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    source_revision_key text NOT NULL,
    file_identity_key text NOT NULL,
    chunk_ordinal smallint NOT NULL,
    byte_start bigint NOT NULL,
    byte_end_exclusive bigint NOT NULL,
    payload_byte_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    payload_bytes bytea NOT NULL,
    capture_policy_id text NOT NULL,
    retention_policy_ref text NOT NULL,
    captured_by_principal_id uuid NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    CONSTRAINT captured_source_ranges_pk PRIMARY KEY (
        tenant_id, source_range_chunk_id
    ),
    CONSTRAINT captured_source_ranges_receipt_order_uq UNIQUE (
        tenant_id, source_range_receipt_id, chunk_ordinal
    ),
    CONSTRAINT captured_source_ranges_source_range_uq UNIQUE (
        tenant_id, source_object_id, source_revision_key, file_identity_key,
        byte_start, byte_end_exclusive
    ),
    CONSTRAINT captured_source_ranges_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ) REFERENCES memoriesql.source_range_capture_receipts (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ),
    CONSTRAINT captured_source_ranges_idempotency_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT captured_source_ranges_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT captured_source_ranges_principal_fk FOREIGN KEY (
        tenant_id, captured_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT captured_source_ranges_revision_nonempty CHECK (
        btrim(source_revision_key) <> '' AND length(source_revision_key) <= 512
    ),
    CONSTRAINT captured_source_ranges_file_identity_nonempty CHECK (
        btrim(file_identity_key) <> '' AND length(file_identity_key) <= 256
    ),
    CONSTRAINT captured_source_ranges_ordinal CHECK (
        chunk_ordinal BETWEEN 0 AND 255
    ),
    CONSTRAINT captured_source_ranges_byte_integrity CHECK (
        byte_start >= 0
        AND byte_end_exclusive > byte_start
        AND byte_end_exclusive - byte_start = payload_byte_count
        AND payload_byte_count = octet_length(payload_bytes)
        AND payload_byte_count BETWEEN 1 AND 65536
    ),
    CONSTRAINT captured_source_ranges_hash_integrity CHECK (
        payload_sha256 ~ '^[a-f0-9]{64}$'
        AND payload_sha256 = encode(pg_catalog.sha256(payload_bytes), 'hex')
    ),
    CONSTRAINT captured_source_ranges_policy_shape CHECK (
        capture_policy_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(capture_policy_id) <= 128
        AND btrim(retention_policy_ref) <> ''
        AND length(retention_policy_ref) <= 256
    )
);

CREATE TABLE memoriesql.source_byte_checkpoint_advances (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    checkpoint_advance_id uuid NOT NULL,
    checkpoint_key text NOT NULL,
    checkpoint_sequence bigint NOT NULL,
    checkpoint_hash text NOT NULL,
    source_object_id uuid NOT NULL,
    source_revision_key text NOT NULL,
    file_identity_key text NOT NULL,
    byte_start bigint NOT NULL,
    byte_end_exclusive bigint NOT NULL,
    source_range_receipt_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    advanced_by_principal_id uuid NOT NULL,
    advanced_at timestamp with time zone NOT NULL,
    CONSTRAINT source_byte_checkpoint_advances_pk PRIMARY KEY (
        tenant_id, checkpoint_advance_id
    ),
    CONSTRAINT source_byte_checkpoint_advances_order_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        checkpoint_key, checkpoint_sequence
    ),
    CONSTRAINT source_byte_checkpoint_advances_receipt_uq UNIQUE (
        tenant_id, source_range_receipt_id
    ),
    CONSTRAINT source_byte_checkpoint_advances_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT source_byte_checkpoint_advances_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ) REFERENCES memoriesql.source_range_capture_receipts (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ),
    CONSTRAINT source_byte_checkpoint_advances_idempotency_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT source_byte_checkpoint_advances_principal_fk FOREIGN KEY (
        tenant_id, advanced_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT source_byte_checkpoint_advances_key_shape CHECK (
        checkpoint_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(checkpoint_key) <= 128
        AND checkpoint_sequence > 0
        AND checkpoint_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT source_byte_checkpoint_advances_identity_shape CHECK (
        btrim(source_revision_key) <> ''
        AND length(source_revision_key) <= 512
        AND btrim(file_identity_key) <> ''
        AND length(file_identity_key) <= 256
    ),
    CONSTRAINT source_byte_checkpoint_advances_byte_range CHECK (
        byte_start >= 0 AND byte_end_exclusive > byte_start
    )
);

CREATE INDEX source_range_capture_receipts_source_order_idx
ON memoriesql.source_range_capture_receipts (
    tenant_id, source_object_id, source_revision_key,
    file_identity_key, byte_start, byte_end_exclusive
);

CREATE INDEX captured_source_ranges_source_order_idx
ON memoriesql.captured_source_ranges (
    tenant_id, source_object_id, source_revision_key,
    file_identity_key, byte_start, byte_end_exclusive
);

CREATE INDEX source_byte_checkpoint_advances_latest_idx
ON memoriesql.source_byte_checkpoint_advances (
    tenant_id, workspace_id, access_scope_id,
    checkpoint_key, checkpoint_sequence DESC
);

CREATE TRIGGER source_range_capture_receipts_immutable
BEFORE UPDATE OR DELETE ON memoriesql.source_range_capture_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE TRIGGER captured_source_ranges_immutable
BEFORE UPDATE OR DELETE ON memoriesql.captured_source_ranges
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE TRIGGER source_byte_checkpoint_advances_immutable
BEFORE UPDATE OR DELETE ON memoriesql.source_byte_checkpoint_advances
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.capture_source_range(
    requested_command jsonb,
    requested_at timestamp with time zone
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    source_record memoriesql.source_objects%ROWTYPE;
    existing_range memoriesql.source_range_capture_receipts%ROWTYPE;
    existing_idempotency memoriesql.idempotency_receipts%ROWTYPE;
    latest_checkpoint memoriesql.source_byte_checkpoint_advances%ROWTYPE;
    chunk jsonb;
    chunk_payload bytea;
    command_tenant_id uuid;
    command_workspace_id uuid;
    command_access_scope_id uuid;
    command_source_object_id uuid;
    new_receipt_id uuid;
    command_start bigint;
    command_end bigint;
    command_byte_count integer;
    expected_chunk_start bigint;
    expected_ordinal integer := 0;
    computed_request_hash text;
    computed_range_hash text;
    concatenated_hex text := '';
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
    response jsonb;
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 786432
       OR requested_command ->> 'contract_version' <> '1'
       OR requested_command ->> 'expected_schema_version' <> '13'
       OR requested_command ->> 'operation' <> 'capture_source_range'
       OR jsonb_typeof(requested_command -> 'binding') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'file_identity') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'chunks') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command -> 'chunks') NOT BETWEEN 1 AND 256
       OR jsonb_typeof(requested_command -> 'policies') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'checkpoint') IS DISTINCT FROM 'object'
       OR requested_command - ARRAY[
            'contract_version', 'expected_schema_version', 'operation',
            'idempotency_key', 'binding', 'capability_id', 'connector_id',
            'observed_connector_version', 'capture_surface',
            'source_revision_key', 'file_identity', 'file_identity_key',
            'observed_source_format_version', 'byte_start',
            'byte_end_exclusive', 'payload_byte_count', 'payload_sha256',
            'chunks', 'policies', 'checkpoint'
          ] <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_object_keys(requested_command)) <> 20
       OR (requested_command -> 'binding') - ARRAY[
            'tenant_id', 'workspace_id', 'access_scope_id', 'source_object_id',
            'expected_source_object_schema_version'
          ] <> '{}'::jsonb
       OR (
            SELECT count(*)
            FROM jsonb_object_keys(requested_command -> 'binding')
          ) <> 5
       OR (requested_command -> 'file_identity') - ARRAY[
            'platform', 'device', 'inode', 'birth_time_ns'
          ] <> '{}'::jsonb
       OR (
            SELECT count(*)
            FROM jsonb_object_keys(requested_command -> 'file_identity')
          ) <> 4
       OR (requested_command -> 'policies') - ARRAY[
            'capture_policy_id', 'retention_policy_ref'
          ] <> '{}'::jsonb
       OR (
            SELECT count(*)
            FROM jsonb_object_keys(requested_command -> 'policies')
          ) <> 2
       OR (requested_command -> 'checkpoint') - ARRAY[
            'checkpoint_key', 'expected_sequence', 'next_sequence',
            'expected_byte_offset', 'next_byte_offset', 'file_identity_key',
            'checkpoint_hash'
          ] <> '{}'::jsonb
       OR (
            SELECT count(*)
            FROM jsonb_object_keys(requested_command -> 'checkpoint')
          ) <> 7
       OR btrim(requested_command ->> 'idempotency_key') = ''
       OR length(requested_command ->> 'idempotency_key') > 512
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'source-range capture contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    command_tenant_id := (requested_command #>> '{binding,tenant_id}')::uuid;
    command_workspace_id := (requested_command #>> '{binding,workspace_id}')::uuid;
    command_access_scope_id :=
        (requested_command #>> '{binding,access_scope_id}')::uuid;
    command_source_object_id :=
        (requested_command #>> '{binding,source_object_id}')::uuid;
    command_start := (requested_command ->> 'byte_start')::bigint;
    command_end := (requested_command ->> 'byte_end_exclusive')::bigint;
    command_byte_count := (requested_command ->> 'payload_byte_count')::integer;

    IF requested_command #>> '{binding,expected_source_object_schema_version}' <> '1'
       OR requested_command ->> 'capability_id'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_command ->> 'capability_id') > 128
       OR requested_command ->> 'connector_id'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_command ->> 'connector_id') > 128
       OR (
            requested_command -> 'observed_connector_version' <> 'null'::jsonb
            AND length(requested_command ->> 'observed_connector_version')
                NOT BETWEEN 1 AND 128
          )
       OR (
            requested_command -> 'observed_source_format_version' <> 'null'::jsonb
            AND length(requested_command ->> 'observed_source_format_version')
                NOT BETWEEN 1 AND 128
          )
       OR btrim(requested_command ->> 'source_revision_key') = ''
       OR length(requested_command ->> 'source_revision_key') > 512
       OR btrim(requested_command ->> 'file_identity_key') = ''
       OR length(requested_command ->> 'file_identity_key') > 256
       OR length(requested_command #>> '{file_identity,platform}')
            NOT BETWEEN 1 AND 32
       OR (requested_command #>> '{file_identity,device}')::bigint < 0
       OR (requested_command #>> '{file_identity,inode}')::bigint < 0
       OR (
            requested_command #> '{file_identity,birth_time_ns}' <> 'null'::jsonb
            AND (requested_command #>> '{file_identity,birth_time_ns}')::bigint < 0
          )
       OR requested_command ->> 'file_identity_key' IS DISTINCT FROM concat(
            requested_command #>> '{file_identity,platform}', ':',
            requested_command #>> '{file_identity,device}', ':',
            requested_command #>> '{file_identity,inode}', ':',
            COALESCE(
                requested_command #>> '{file_identity,birth_time_ns}', 'unknown'
            )
          )
       OR command_start < 0
       OR command_end <= command_start
       OR command_byte_count NOT BETWEEN 1 AND 262144
       OR command_end - command_start <> command_byte_count
       OR requested_command ->> 'payload_sha256' !~ '^[a-f0-9]{64}$'
       OR requested_command ->> 'capture_surface' NOT IN (
            'artifact', 'live_hook', 'harness', 'browser', 'import',
            'explicit_checkpoint', 'synthetic'
          )
       OR requested_command #>> '{policies,capture_policy_id}'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_command #>> '{policies,capture_policy_id}') > 128
       OR btrim(requested_command #>> '{policies,retention_policy_ref}') = ''
       OR length(requested_command #>> '{policies,retention_policy_ref}') > 256
       OR requested_command #>> '{checkpoint,checkpoint_key}'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_command #>> '{checkpoint,checkpoint_key}') > 128
       OR (requested_command #>> '{checkpoint,expected_sequence}')::bigint < 0
       OR (requested_command #>> '{checkpoint,next_sequence}')::bigint
            <> (requested_command #>> '{checkpoint,expected_sequence}')::bigint + 1
       OR (requested_command #>> '{checkpoint,expected_byte_offset}')::bigint
            <> command_start
       OR (requested_command #>> '{checkpoint,next_byte_offset}')::bigint
            <> command_end
       OR requested_command #>> '{checkpoint,file_identity_key}'
            IS DISTINCT FROM requested_command ->> 'file_identity_key'
       OR requested_command #>> '{checkpoint,checkpoint_hash}'
            !~ '^[a-f0-9]{64}$'
       OR requested_command #>> '{checkpoint,checkpoint_hash}'
            IS DISTINCT FROM encode(pg_catalog.sha256(pg_catalog.convert_to(
                concat(
                    requested_command #>> '{checkpoint,checkpoint_key}', ':',
                    requested_command #>> '{checkpoint,expected_sequence}', ':',
                    requested_command #>> '{checkpoint,next_sequence}', ':',
                    command_start, ':', command_end, ':',
                    requested_command ->> 'file_identity_key', ':',
                    requested_command ->> 'payload_sha256'
                ),
                'UTF8'
            )), 'hex') THEN
        RAISE EXCEPTION 'source-range capture contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    expected_chunk_start := command_start;
    FOR chunk IN
        SELECT item.value
        FROM jsonb_array_elements(requested_command -> 'chunks')
            WITH ORDINALITY AS item(value, ordinal)
        ORDER BY item.ordinal
    LOOP
        IF jsonb_typeof(chunk) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(chunk)) <> 6
           OR chunk - ARRAY[
                'chunk_ordinal', 'byte_start', 'byte_end_exclusive',
                'payload_byte_count', 'payload_sha256', 'payload_hex'
              ] <> '{}'::jsonb
           OR (chunk ->> 'chunk_ordinal')::integer <> expected_ordinal
           OR (chunk ->> 'byte_start')::bigint <> expected_chunk_start
           OR (chunk ->> 'byte_end_exclusive')::bigint
                <= (chunk ->> 'byte_start')::bigint
           OR (chunk ->> 'payload_byte_count')::integer NOT BETWEEN 1 AND 65536
           OR (chunk ->> 'byte_end_exclusive')::bigint
                - (chunk ->> 'byte_start')::bigint
                <> (chunk ->> 'payload_byte_count')::integer
           OR chunk ->> 'payload_sha256' !~ '^[a-f0-9]{64}$'
           OR chunk ->> 'payload_hex' !~ '^(?:[a-f0-9]{2})+$'
           OR length(chunk ->> 'payload_hex')
                <> (chunk ->> 'payload_byte_count')::integer * 2 THEN
            RAISE EXCEPTION 'source-range chunk contract is invalid'
                USING ERRCODE = '22023';
        END IF;
        chunk_payload := decode(chunk ->> 'payload_hex', 'hex');
        IF octet_length(chunk_payload)
                <> (chunk ->> 'payload_byte_count')::integer
           OR encode(pg_catalog.sha256(chunk_payload), 'hex')
                IS DISTINCT FROM chunk ->> 'payload_sha256' THEN
            RAISE EXCEPTION 'source-range chunk integrity mismatch'
                USING ERRCODE = '22023';
        END IF;
        concatenated_hex := concatenated_hex || (chunk ->> 'payload_hex');
        expected_chunk_start := (chunk ->> 'byte_end_exclusive')::bigint;
        expected_ordinal := expected_ordinal + 1;
    END LOOP;
    computed_range_hash := encode(
        pg_catalog.sha256(decode(concatenated_hex, 'hex')), 'hex'
    );
    IF expected_chunk_start <> command_end
       OR expected_ordinal <> jsonb_array_length(requested_command -> 'chunks')
       OR length(concatenated_hex) <> command_byte_count * 2
       OR computed_range_hash IS DISTINCT FROM requested_command ->> 'payload_sha256' THEN
        RAISE EXCEPTION 'source-range aggregate integrity mismatch'
            USING ERRCODE = '22023';
    END IF;
    computed_request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(
        (requested_command - 'chunks')::text, 'UTF8'
    )), 'hex');

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.tenant_id <> command_tenant_id
       OR context_record.workspace_id <> command_workspace_id
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source-range capture is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT source.* INTO source_record
    FROM memoriesql.source_objects AS source
    WHERE source.tenant_id = command_tenant_id
      AND source.workspace_id = command_workspace_id
      AND source.access_scope_id = command_access_scope_id
      AND source.source_object_id = command_source_object_id
      AND source.schema_version =
          (requested_command #>> '{binding,expected_source_object_schema_version}')::integer;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'source object is unavailable' USING ERRCODE = '42501';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        command_tenant_id::text || ':source-range:'
        || command_source_object_id::text || ':'
        || (requested_command ->> 'source_revision_key') || ':'
        || (requested_command ->> 'file_identity_key'),
        0
    ));
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source-range capture is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT range_receipt.* INTO existing_range
    FROM memoriesql.source_range_capture_receipts AS range_receipt
    WHERE range_receipt.tenant_id = command_tenant_id
      AND range_receipt.source_object_id = command_source_object_id
      AND range_receipt.source_revision_key =
          requested_command ->> 'source_revision_key'
      AND range_receipt.file_identity_key = requested_command ->> 'file_identity_key'
      AND range_receipt.byte_start = command_start
      AND range_receipt.byte_end_exclusive = command_end;
    IF FOUND THEN
        IF existing_range.payload_sha256 <>
              requested_command ->> 'payload_sha256'
           OR existing_range.payload_byte_count <> command_byte_count THEN
            RAISE EXCEPTION 'source_range_conflict' USING ERRCODE = '23505';
        END IF;
        IF existing_range.workspace_id <> command_workspace_id
           OR existing_range.access_scope_id <> command_access_scope_id
           OR existing_range.file_identity <>
              requested_command -> 'file_identity'
           OR existing_range.capability_id <>
              requested_command ->> 'capability_id'
           OR existing_range.connector_id <>
              requested_command ->> 'connector_id'
           OR existing_range.observed_connector_version IS DISTINCT FROM
              requested_command ->> 'observed_connector_version'
           OR existing_range.observed_source_format_version IS DISTINCT FROM
              requested_command ->> 'observed_source_format_version'
           OR existing_range.capture_surface <>
              requested_command ->> 'capture_surface'
           OR existing_range.capture_policy_id <>
              requested_command #>> '{policies,capture_policy_id}'
           OR existing_range.retention_policy_ref <>
              requested_command #>> '{policies,retention_policy_ref}'
           OR existing_range.checkpoint_key <>
              requested_command #>> '{checkpoint,checkpoint_key}'
           OR existing_range.checkpoint_sequence <>
              (requested_command #>> '{checkpoint,next_sequence}')::bigint
        THEN
            RAISE EXCEPTION 'source_range_replay_conflict'
                USING ERRCODE = '23505';
        END IF;

        SELECT * INTO existing_idempotency
        FROM memoriesql.idempotency_receipts AS receipt
        WHERE receipt.tenant_id = command_tenant_id
          AND receipt.operation_kind = 'source_range.capture'
          AND receipt.idempotency_key =
              requested_command ->> 'idempotency_key'
        FOR UPDATE;
        IF FOUND THEN
            IF existing_idempotency.request_hash <> computed_request_hash
               OR existing_idempotency.status <> 'succeeded'
               OR existing_idempotency.response_receipt IS NULL
               OR existing_idempotency.response_receipt
                    ->> 'source_range_receipt_id'
                    IS DISTINCT FROM existing_range.source_range_receipt_id::text
               OR existing_idempotency.response_receipt
                    ->> 'authority_principal_id'
                    IS DISTINCT FROM context_record.principal_id::text THEN
                RAISE EXCEPTION 'idempotency_conflict'
                    USING ERRCODE = '23505';
            END IF;
            RETURN existing_idempotency.response_receipt
                || jsonb_build_object('replayed', true);
        END IF;

        new_receipt_id := pg_catalog.uuidv7();
        response := jsonb_build_object(
            'source_range_receipt_id', existing_range.source_range_receipt_id,
            'idempotency_receipt_id', new_receipt_id,
            'source_object_id', existing_range.source_object_id,
            'source_revision_key', existing_range.source_revision_key,
            'file_identity_key', existing_range.file_identity_key,
            'byte_start', existing_range.byte_start,
            'byte_end_exclusive', existing_range.byte_end_exclusive,
            'payload_byte_count', existing_range.payload_byte_count,
            'payload_sha256', existing_range.payload_sha256,
            'chunk_count', existing_range.chunk_count,
            'checkpoint_key', existing_range.checkpoint_key,
            'checkpoint_sequence', existing_range.checkpoint_sequence,
            'interpretation_status', existing_range.interpretation_status,
            'authority_principal_id', context_record.principal_id,
            'captured_at', database_now,
            'already_exists', true,
            'replayed', false,
            'durable', true
        );
        INSERT INTO memoriesql.idempotency_receipts (
            tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
            operation_kind, idempotency_key, request_hash, status,
            resource_kind, resource_id, response_receipt, attempt_count,
            created_at, updated_at, completed_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_receipt_id, 'source_range.capture',
            requested_command ->> 'idempotency_key', computed_request_hash,
            'succeeded', 'source', command_source_object_id, response, 1,
            database_now, database_now, database_now
        );
        RETURN response;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM memoriesql.source_range_capture_receipts AS prior
        WHERE prior.tenant_id = command_tenant_id
          AND prior.source_object_id = command_source_object_id
          AND prior.source_revision_key = requested_command ->> 'source_revision_key'
          AND prior.file_identity_key = requested_command ->> 'file_identity_key'
          AND prior.byte_start < command_end
          AND prior.byte_end_exclusive > command_start
    ) THEN
        RAISE EXCEPTION 'source_range_overlap' USING ERRCODE = '23505';
    END IF;

    SELECT * INTO latest_checkpoint
    FROM memoriesql.source_byte_checkpoint_advances AS checkpoint
    WHERE checkpoint.tenant_id = command_tenant_id
      AND checkpoint.workspace_id = command_workspace_id
      AND checkpoint.access_scope_id = command_access_scope_id
      AND checkpoint.checkpoint_key =
          requested_command #>> '{checkpoint,checkpoint_key}'
    ORDER BY checkpoint.checkpoint_sequence DESC
    LIMIT 1
    FOR UPDATE;
    IF FOUND THEN
        IF latest_checkpoint.checkpoint_sequence <>
              (requested_command #>> '{checkpoint,expected_sequence}')::bigint
           OR latest_checkpoint.byte_end_exclusive <> command_start
           OR latest_checkpoint.source_object_id <> command_source_object_id
           OR latest_checkpoint.source_revision_key <>
              requested_command ->> 'source_revision_key'
           OR latest_checkpoint.file_identity_key <>
              requested_command ->> 'file_identity_key' THEN
            RAISE EXCEPTION 'source_range_gap_or_checkpoint_conflict'
                USING ERRCODE = '40001';
        END IF;
    ELSIF (requested_command #>> '{checkpoint,expected_sequence}')::bigint <> 0 THEN
        RAISE EXCEPTION 'source_range_gap_or_checkpoint_conflict'
            USING ERRCODE = '40001';
    END IF;

    SELECT * INTO existing_idempotency
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.operation_kind = 'source_range.capture'
      AND receipt.idempotency_key = requested_command ->> 'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF existing_idempotency.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF existing_idempotency.status <> 'succeeded'
           OR existing_idempotency.response_receipt IS NULL THEN
            RAISE EXCEPTION 'source-range receipt is incomplete'
                USING ERRCODE = '55000';
        END IF;
        RETURN existing_idempotency.response_receipt
            || jsonb_build_object('replayed', true);
    END IF;

    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, 'source_range.capture',
        requested_command ->> 'idempotency_key', computed_request_hash,
        'in_progress', 'source', command_source_object_id, 1,
        database_now, database_now
    );

    INSERT INTO memoriesql.source_range_capture_receipts (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id,
        idempotency_receipt_id, source_object_id, source_revision_key,
        file_identity_key, file_identity, capability_id, connector_id,
        observed_connector_version, observed_source_format_version,
        capture_surface, byte_start, byte_end_exclusive, payload_byte_count,
        payload_sha256, chunk_count, capture_policy_id, retention_policy_ref,
        interpretation_status, checkpoint_key, checkpoint_sequence,
        captured_by_principal_id, captured_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, new_receipt_id, command_source_object_id,
        requested_command ->> 'source_revision_key',
        requested_command ->> 'file_identity_key',
        requested_command -> 'file_identity',
        requested_command ->> 'capability_id', requested_command ->> 'connector_id',
        NULLIF(requested_command ->> 'observed_connector_version', ''),
        NULLIF(requested_command ->> 'observed_source_format_version', ''),
        requested_command ->> 'capture_surface', command_start, command_end,
        command_byte_count, requested_command ->> 'payload_sha256',
        jsonb_array_length(requested_command -> 'chunks'),
        requested_command #>> '{policies,capture_policy_id}',
        requested_command #>> '{policies,retention_policy_ref}',
        'pending_interpretation',
        requested_command #>> '{checkpoint,checkpoint_key}',
        (requested_command #>> '{checkpoint,next_sequence}')::bigint,
        context_record.principal_id, database_now
    );

    FOR chunk IN
        SELECT item.value
        FROM jsonb_array_elements(requested_command -> 'chunks')
            WITH ORDINALITY AS item(value, ordinal)
        ORDER BY item.ordinal
    LOOP
        chunk_payload := decode(chunk ->> 'payload_hex', 'hex');
        INSERT INTO memoriesql.captured_source_ranges (
            tenant_id, workspace_id, access_scope_id, source_range_chunk_id,
            source_range_receipt_id, idempotency_receipt_id, source_object_id,
            source_revision_key, file_identity_key, chunk_ordinal, byte_start,
            byte_end_exclusive, payload_byte_count, payload_sha256,
            payload_bytes, capture_policy_id, retention_policy_ref,
            captured_by_principal_id, captured_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            pg_catalog.uuidv7(), new_receipt_id, new_receipt_id,
            command_source_object_id, requested_command ->> 'source_revision_key',
            requested_command ->> 'file_identity_key',
            (chunk ->> 'chunk_ordinal')::smallint,
            (chunk ->> 'byte_start')::bigint,
            (chunk ->> 'byte_end_exclusive')::bigint,
            (chunk ->> 'payload_byte_count')::integer,
            chunk ->> 'payload_sha256', chunk_payload,
            requested_command #>> '{policies,capture_policy_id}',
            requested_command #>> '{policies,retention_policy_ref}',
            context_record.principal_id, database_now
        );
    END LOOP;

    INSERT INTO memoriesql.source_byte_checkpoint_advances (
        tenant_id, workspace_id, access_scope_id, checkpoint_advance_id,
        checkpoint_key, checkpoint_sequence, checkpoint_hash,
        source_object_id, source_revision_key, file_identity_key,
        byte_start, byte_end_exclusive, source_range_receipt_id,
        idempotency_receipt_id, advanced_by_principal_id, advanced_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        pg_catalog.uuidv7(),
        requested_command #>> '{checkpoint,checkpoint_key}',
        (requested_command #>> '{checkpoint,next_sequence}')::bigint,
        requested_command #>> '{checkpoint,checkpoint_hash}',
        command_source_object_id, requested_command ->> 'source_revision_key',
        requested_command ->> 'file_identity_key', command_start, command_end,
        new_receipt_id, new_receipt_id, context_record.principal_id, database_now
    );

    response := jsonb_build_object(
        'source_range_receipt_id', new_receipt_id,
        'idempotency_receipt_id', new_receipt_id,
        'source_object_id', command_source_object_id,
        'source_revision_key', requested_command ->> 'source_revision_key',
        'file_identity_key', requested_command ->> 'file_identity_key',
        'byte_start', command_start,
        'byte_end_exclusive', command_end,
        'payload_byte_count', command_byte_count,
        'payload_sha256', requested_command ->> 'payload_sha256',
        'chunk_count', jsonb_array_length(requested_command -> 'chunks'),
        'checkpoint_key', requested_command #>> '{checkpoint,checkpoint_key}',
        'checkpoint_sequence',
            (requested_command #>> '{checkpoint,next_sequence}')::bigint,
        'interpretation_status', 'pending_interpretation',
        'authority_principal_id', context_record.principal_id,
        'captured_at', database_now,
        'already_exists', false,
        'replayed', false,
        'durable', true
    );
    UPDATE memoriesql.idempotency_receipts AS receipt
       SET status = 'succeeded', response_receipt = response,
           updated_at = database_now, completed_at = database_now
     WHERE receipt.tenant_id = command_tenant_id
       AND receipt.idempotency_receipt_id = new_receipt_id;
    RETURN response;
END;
$$;

CREATE FUNCTION memoriesql.inspect_captured_source_range(
    requested_source_range_receipt_id uuid,
    request_id uuid,
    diagnostic_context jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    workspace_record memoriesql.workspaces%ROWTYPE;
    receipt_record memoriesql.source_range_capture_receipts%ROWTYPE;
BEGIN
    IF request_id IS NULL
       OR jsonb_typeof(diagnostic_context) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(diagnostic_context::text) > 8192
       OR diagnostic_context - ARRAY[
            'parser_profile_version', 'diagnostic_code',
            'recognized_record_type', 'failing_field_name', 'json_pointer',
            'byte_offset', 'record_index'
          ] <> '{}'::jsonb THEN
        RETURN NULL;
    END IF;
    IF (
            diagnostic_context ? 'parser_profile_version'
            AND (
                jsonb_typeof(diagnostic_context -> 'parser_profile_version')
                    IS DISTINCT FROM 'string'
                OR length(diagnostic_context ->> 'parser_profile_version')
                    NOT BETWEEN 1 AND 128
            )
       ) OR (
            diagnostic_context ? 'diagnostic_code'
            AND (
                jsonb_typeof(diagnostic_context -> 'diagnostic_code')
                    IS DISTINCT FROM 'string'
                OR diagnostic_context ->> 'diagnostic_code'
                    !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
                OR length(diagnostic_context ->> 'diagnostic_code') > 128
            )
       ) OR (
            diagnostic_context ? 'recognized_record_type'
            AND (
                jsonb_typeof(diagnostic_context -> 'recognized_record_type')
                    IS DISTINCT FROM 'string'
                OR length(diagnostic_context ->> 'recognized_record_type')
                    NOT BETWEEN 1 AND 128
            )
       ) OR (
            diagnostic_context ? 'failing_field_name'
            AND (
                jsonb_typeof(diagnostic_context -> 'failing_field_name')
                    IS DISTINCT FROM 'string'
                OR length(diagnostic_context ->> 'failing_field_name')
                    NOT BETWEEN 1 AND 256
            )
       ) OR (
            diagnostic_context ? 'json_pointer'
            AND (
                jsonb_typeof(diagnostic_context -> 'json_pointer')
                    IS DISTINCT FROM 'string'
                OR length(diagnostic_context ->> 'json_pointer')
                    NOT BETWEEN 1 AND 512
            )
       ) OR (
            diagnostic_context ? 'byte_offset'
            AND (
                jsonb_typeof(diagnostic_context -> 'byte_offset')
                    IS DISTINCT FROM 'number'
                OR diagnostic_context ->> 'byte_offset' !~ '^[0-9]+$'
            )
       ) OR (
            diagnostic_context ? 'record_index'
            AND (
                jsonb_typeof(diagnostic_context -> 'record_index')
                    IS DISTINCT FROM 'number'
                OR diagnostic_context ->> 'record_index' !~ '^[0-9]+$'
            )
       ) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    SELECT * INTO receipt_record
    FROM memoriesql.source_range_capture_receipts AS receipt
    WHERE receipt.tenant_id = context_record.tenant_id
      AND receipt.workspace_id = context_record.workspace_id
      AND receipt.source_range_receipt_id = requested_source_range_receipt_id;
    IF NOT FOUND OR NOT memoriesql.application_authorize_resource(
        'source', receipt_record.source_object_id,
        'source.raw.inspect', 'read', request_id
    ) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO workspace_record
    FROM memoriesql.workspaces AS workspace
    WHERE workspace.tenant_id = context_record.tenant_id
      AND workspace.workspace_id = context_record.workspace_id;
    IF context_record.principal_kind <> 'human'
       OR workspace_record.authority_mode <> 'personal_local'
       OR (
            diagnostic_context ? 'byte_offset'
            AND (
                (diagnostic_context ->> 'byte_offset')::bigint
                    < receipt_record.byte_start
                OR (diagnostic_context ->> 'byte_offset')::bigint
                    >= receipt_record.byte_end_exclusive
            )
       )
       OR (
            diagnostic_context ? 'record_index'
            AND (diagnostic_context ->> 'record_index')::bigint < 0
       ) THEN
        RETURN NULL;
    END IF;
    PERFORM pg_catalog.set_config(
        'memoriesql.raw_inspection_request_id', request_id::text, true
    );
    RETURN jsonb_build_object(
        'source_range_receipt_id', receipt_record.source_range_receipt_id,
        'source_object_id', receipt_record.source_object_id,
        'source_revision_key', receipt_record.source_revision_key,
        'file_identity_key', receipt_record.file_identity_key,
        'byte_start', receipt_record.byte_start,
        'byte_end_exclusive', receipt_record.byte_end_exclusive,
        'payload_byte_count', receipt_record.payload_byte_count,
        'payload_sha256', receipt_record.payload_sha256,
        'chunk_count', receipt_record.chunk_count,
        'connector_id', receipt_record.connector_id,
        'observed_connector_version', receipt_record.observed_connector_version,
        'observed_source_format_version',
            receipt_record.observed_source_format_version,
        'parser_profile_version', diagnostic_context ->> 'parser_profile_version',
        'diagnostic_code', diagnostic_context ->> 'diagnostic_code',
        'recognized_record_type', diagnostic_context ->> 'recognized_record_type',
        'failing_field_name', diagnostic_context ->> 'failing_field_name',
        'json_pointer', diagnostic_context ->> 'json_pointer',
        'byte_offset', diagnostic_context -> 'byte_offset',
        'record_index', diagnostic_context -> 'record_index',
        'interpretation_status', receipt_record.interpretation_status,
        'raw_bytes_available', receipt_record.chunk_count > 0
    );
END;
$$;

CREATE FUNCTION memoriesql.read_captured_source_range_bytes(
    requested_source_range_receipt_id uuid,
    request_id uuid,
    confirmation text
)
RETURNS SETOF jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    workspace_record memoriesql.workspaces%ROWTYPE;
    receipt_record memoriesql.source_range_capture_receipts%ROWTYPE;
BEGIN
    IF request_id IS NULL
       OR confirmation <> 'confirm_local_raw_disclosure' THEN
        RETURN;
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    SELECT * INTO receipt_record
    FROM memoriesql.source_range_capture_receipts AS receipt
    WHERE receipt.tenant_id = context_record.tenant_id
      AND receipt.workspace_id = context_record.workspace_id
      AND receipt.source_range_receipt_id = requested_source_range_receipt_id;
    IF NOT FOUND OR NOT memoriesql.application_authorize_resource(
        'source', receipt_record.source_object_id,
        'source.raw.read', 'read', request_id
    ) THEN
        RETURN;
    END IF;
    SELECT * INTO workspace_record
    FROM memoriesql.workspaces AS workspace
    WHERE workspace.tenant_id = context_record.tenant_id
      AND workspace.workspace_id = context_record.workspace_id;
    IF context_record.principal_kind <> 'human'
       OR workspace_record.authority_mode <> 'personal_local' THEN
        RETURN;
    END IF;
    PERFORM pg_catalog.set_config(
        'memoriesql.raw_detail_request_id', request_id::text, true
    );
    RETURN QUERY
    SELECT jsonb_build_object(
        'source_range_receipt_id', chunk.source_range_receipt_id,
        'chunk_ordinal', chunk.chunk_ordinal,
        'byte_start', chunk.byte_start,
        'byte_end_exclusive', chunk.byte_end_exclusive,
        'payload_sha256', chunk.payload_sha256,
        'payload_hex', encode(chunk.payload_bytes, 'hex')
    )
    FROM memoriesql.captured_source_ranges AS chunk
    WHERE chunk.tenant_id = receipt_record.tenant_id
      AND chunk.source_range_receipt_id = receipt_record.source_range_receipt_id
    ORDER BY chunk.chunk_ordinal;
END;
$$;

CREATE POLICY source_range_capture_receipts_inspection
ON memoriesql.source_range_capture_receipts
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.raw.inspect', 'read'
    )
    AND pg_catalog.current_setting(
        'memoriesql.raw_inspection_request_id', true
    ) IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.authorization_audit_events AS audit
          ON audit.tenant_id = context.tenant_id
         AND audit.workspace_id = context.workspace_id
         AND audit.authenticated_principal_id = context.principal_id
         AND audit.request_id::text = pg_catalog.current_setting(
                'memoriesql.raw_inspection_request_id', true
             )
         AND audit.resource_kind = 'source'
         AND audit.resource_id =
             source_range_capture_receipts.source_object_id
         AND audit.capability_key = 'source.raw.inspect'
         AND audit.permission_key = 'read'
         AND audit.decision = 'allowed'
         AND audit.recorded_at >= pg_catalog.transaction_timestamp()
    )
);

CREATE POLICY captured_source_ranges_inspection
ON memoriesql.captured_source_ranges
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.raw.read', 'read'
    )
    AND pg_catalog.current_setting(
        'memoriesql.raw_detail_request_id', true
    ) IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.authorization_audit_events AS audit
          ON audit.tenant_id = context.tenant_id
         AND audit.workspace_id = context.workspace_id
         AND audit.authenticated_principal_id = context.principal_id
         AND audit.request_id::text = pg_catalog.current_setting(
                'memoriesql.raw_detail_request_id', true
             )
         AND audit.resource_kind = 'source'
         AND audit.resource_id = captured_source_ranges.source_object_id
         AND audit.capability_key = 'source.raw.read'
         AND audit.permission_key = 'read'
         AND audit.decision = 'allowed'
         AND audit.recorded_at >= pg_catalog.transaction_timestamp()
    )
);

CREATE POLICY source_byte_checkpoint_advances_inspection
ON memoriesql.source_byte_checkpoint_advances
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.raw.inspect', 'read'
    )
    AND pg_catalog.current_setting(
        'memoriesql.raw_inspection_request_id', true
    ) IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.authorization_audit_events AS audit
          ON audit.tenant_id = context.tenant_id
         AND audit.workspace_id = context.workspace_id
         AND audit.authenticated_principal_id = context.principal_id
         AND audit.request_id::text = pg_catalog.current_setting(
                'memoriesql.raw_inspection_request_id', true
             )
         AND audit.resource_kind = 'source'
         AND audit.resource_id =
             source_byte_checkpoint_advances.source_object_id
         AND audit.capability_key = 'source.raw.inspect'
         AND audit.permission_key = 'read'
         AND audit.decision = 'allowed'
         AND audit.recorded_at >= pg_catalog.transaction_timestamp()
    )
);

ALTER TABLE memoriesql.source_range_capture_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_range_capture_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.captured_source_ranges ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.captured_source_ranges FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_byte_checkpoint_advances ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_byte_checkpoint_advances FORCE ROW LEVEL SECURITY;

REVOKE ALL ON memoriesql.source_range_capture_receipts FROM PUBLIC;
REVOKE ALL ON memoriesql.captured_source_ranges FROM PUBLIC;
REVOKE ALL ON memoriesql.source_byte_checkpoint_advances FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.capture_source_range(jsonb, timestamptz)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.inspect_captured_source_range(
    uuid, uuid, jsonb
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.read_captured_source_range_bytes(
    uuid, uuid, text
) FROM PUBLIC;

GRANT SELECT ON memoriesql.source_range_capture_receipts,
    memoriesql.captured_source_ranges,
    memoriesql.source_byte_checkpoint_advances
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.capture_source_range(jsonb, timestamptz)
TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_captured_source_range(
    uuid, uuid, jsonb
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.read_captured_source_range_bytes(
    uuid, uuid, text
) TO memoriesql_application;

COMMENT ON TABLE memoriesql.source_range_capture_receipts IS
    'Content-free durable receipts for bounded raw source-range capture.';
COMMENT ON TABLE memoriesql.captured_source_ranges IS
    'Immutable bounded local raw evidence; excluded from ordinary inbox, recall, telemetry, logs, support, and evidence reports.';
COMMENT ON TABLE memoriesql.source_byte_checkpoint_advances IS
    'Append-only source-byte cursor advances bound to durable raw-capture receipts.';
COMMENT ON FUNCTION memoriesql.capture_source_range(jsonb, timestamptz) IS
    'Authorize, integrity-check, deduplicate, persist, receipt, and checkpoint approved source bytes without interpreting them.';
COMMENT ON FUNCTION memoriesql.inspect_captured_source_range(uuid, uuid, jsonb) IS
    'Audited personal-owner metadata inspection without raw disclosure.';
COMMENT ON FUNCTION memoriesql.read_captured_source_range_bytes(uuid, uuid, text) IS
    'Audited source.raw.read, separately confirmed personal-local disclosure of exact retained bytes.';
