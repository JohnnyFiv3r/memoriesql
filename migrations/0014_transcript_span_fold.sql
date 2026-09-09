-- PR-02M exact-turn-first fold over immutable schema-13 source ranges.
-- Fold results remain evidence records and create no source event, source unit,
-- bead, semantic task, claim, relation, or model-authored content.

CREATE TABLE memoriesql.transcript_fold_receipts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    transcript_fold_receipt_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    source_revision_key text NOT NULL,
    file_identity_key text NOT NULL,
    file_identity jsonb NOT NULL,
    capability_id text NOT NULL,
    connector_id text NOT NULL,
    adapter_profile_version text,
    observed_source_format_version text,
    byte_start bigint NOT NULL,
    byte_end_exclusive bigint NOT NULL,
    start_record_index bigint NOT NULL,
    end_record_index bigint NOT NULL,
    complete_record_count integer NOT NULL,
    source_bytes_sha256 text NOT NULL,
    outcomes_sha256 text NOT NULL,
    outcome_count smallint NOT NULL,
    exact_turn_count smallint NOT NULL,
    transcript_span_count smallint NOT NULL,
    policy_disposition_count smallint NOT NULL,
    checkpoint_key text NOT NULL,
    checkpoint_sequence bigint NOT NULL,
    legacy_migration_cursor_sha256 text,
    legacy_migration_target_byte_end_exclusive bigint,
    legacy_migration_target_record_index bigint,
    folded_by_principal_id uuid NOT NULL,
    folded_at timestamp with time zone NOT NULL,
    CONSTRAINT transcript_fold_receipts_pk PRIMARY KEY (
        tenant_id, transcript_fold_receipt_id
    ),
    CONSTRAINT transcript_fold_receipts_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, transcript_fold_receipt_id
    ),
    CONSTRAINT transcript_fold_receipts_idempotency_uq UNIQUE (
        tenant_id, idempotency_receipt_id
    ),
    CONSTRAINT transcript_fold_receipts_natural_uq UNIQUE (
        tenant_id, source_object_id, source_revision_key, file_identity_key,
        byte_start, byte_end_exclusive
    ),
    CONSTRAINT transcript_fold_receipts_checkpoint_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        checkpoint_key, checkpoint_sequence
    ),
    CONSTRAINT transcript_fold_receipts_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT transcript_fold_receipts_idempotency_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT transcript_fold_receipts_principal_fk FOREIGN KEY (
        tenant_id, folded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT transcript_fold_receipts_identity_shape CHECK (
        btrim(source_revision_key) <> ''
        AND length(source_revision_key) <= 512
        AND btrim(file_identity_key) <> ''
        AND length(file_identity_key) <= 256
        AND jsonb_typeof(file_identity) = 'object'
        AND capability_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(capability_id) <= 128
        AND connector_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(connector_id) <= 128
        AND (
            adapter_profile_version IS NULL
            OR length(adapter_profile_version) BETWEEN 1 AND 128
        )
        AND (
            observed_source_format_version IS NULL
            OR length(observed_source_format_version) BETWEEN 1 AND 128
        )
    ),
    CONSTRAINT transcript_fold_receipts_range_shape CHECK (
        byte_start >= 0
        AND byte_end_exclusive > byte_start
        AND start_record_index >= 0
        AND end_record_index >= start_record_index
        AND complete_record_count = end_record_index - start_record_index
    ),
    CONSTRAINT transcript_fold_receipts_hash_shape CHECK (
        source_bytes_sha256 ~ '^[a-f0-9]{64}$'
        AND outcomes_sha256 ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT transcript_fold_receipts_count_shape CHECK (
        outcome_count BETWEEN 1 AND 256
        AND exact_turn_count >= 0
        AND transcript_span_count >= 0
        AND policy_disposition_count >= 0
        AND outcome_count = exact_turn_count
            + transcript_span_count + policy_disposition_count
    ),
    CONSTRAINT transcript_fold_receipts_checkpoint_shape CHECK (
        checkpoint_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(checkpoint_key) <= 128
        AND checkpoint_sequence > 0
    ),
    CONSTRAINT transcript_fold_receipts_legacy_migration_shape CHECK (
        (
            legacy_migration_cursor_sha256 IS NULL
            AND legacy_migration_target_byte_end_exclusive IS NULL
            AND legacy_migration_target_record_index IS NULL
        )
        OR (
            legacy_migration_cursor_sha256 IS NOT NULL
            AND legacy_migration_target_byte_end_exclusive IS NOT NULL
            AND legacy_migration_target_record_index IS NOT NULL
            AND legacy_migration_cursor_sha256 ~ '^[a-f0-9]{64}$'
            AND legacy_migration_target_byte_end_exclusive >= byte_end_exclusive
            AND legacy_migration_target_record_index >= end_record_index
        )
    )
);

CREATE TABLE memoriesql.transcript_fold_outcomes (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    transcript_fold_receipt_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    outcome_ordinal smallint NOT NULL,
    outcome_kind text NOT NULL,
    byte_start bigint NOT NULL,
    byte_end_exclusive bigint NOT NULL,
    start_record_index bigint NOT NULL,
    end_record_index bigint NOT NULL,
    source_bytes_sha256 text NOT NULL,
    exact_envelope_sha256 text,
    disposition_code text,
    scope_reason text,
    adapter_profile_version text,
    folded_at timestamp with time zone NOT NULL,
    CONSTRAINT transcript_fold_outcomes_pk PRIMARY KEY (
        tenant_id, transcript_fold_receipt_id, outcome_ordinal
    ),
    CONSTRAINT transcript_fold_outcomes_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        transcript_fold_receipt_id, outcome_ordinal
    ),
    CONSTRAINT transcript_fold_outcomes_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, transcript_fold_receipt_id
    ) REFERENCES memoriesql.transcript_fold_receipts (
        tenant_id, workspace_id, access_scope_id, transcript_fold_receipt_id
    ),
    CONSTRAINT transcript_fold_outcomes_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT transcript_fold_outcomes_kind_shape CHECK (
        outcome_kind IN ('exact_turn', 'transcript_span', 'policy_disposition')
        AND (
            outcome_kind = 'exact_turn'
            AND exact_envelope_sha256 ~ '^[a-f0-9]{64}$'
            AND disposition_code IS NULL
            AND scope_reason IS NULL
            OR outcome_kind = 'transcript_span'
            AND exact_envelope_sha256 IS NULL
            AND disposition_code IS NULL
            AND scope_reason IS NULL
            OR outcome_kind = 'policy_disposition'
            AND exact_envelope_sha256 IS NULL
            AND disposition_code ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
            AND length(disposition_code) <= 128
            AND (
                scope_reason IS NULL
                OR scope_reason IN ('off_date', 'missing_source_time', 'cross_midnight')
            )
        )
    ),
    CONSTRAINT transcript_fold_outcomes_range_shape CHECK (
        outcome_ordinal BETWEEN 0 AND 255
        AND byte_start >= 0
        AND byte_end_exclusive > byte_start
        AND start_record_index >= 0
        AND end_record_index >= start_record_index
        AND (
            outcome_kind = 'policy_disposition'
            OR end_record_index > start_record_index
        )
        AND source_bytes_sha256 ~ '^[a-f0-9]{64}$'
        AND (
            adapter_profile_version IS NULL
            OR length(adapter_profile_version) BETWEEN 1 AND 128
        )
    )
);

CREATE TABLE memoriesql.transcript_fold_exact_turns (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    transcript_fold_receipt_id uuid NOT NULL,
    outcome_ordinal smallint NOT NULL,
    source_object_id uuid NOT NULL,
    envelope_canonical_json text NOT NULL,
    envelope_sha256 text NOT NULL,
    folded_at timestamp with time zone NOT NULL,
    CONSTRAINT transcript_fold_exact_turns_pk PRIMARY KEY (
        tenant_id, transcript_fold_receipt_id, outcome_ordinal
    ),
    CONSTRAINT transcript_fold_exact_turns_outcome_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        transcript_fold_receipt_id, outcome_ordinal
    ) REFERENCES memoriesql.transcript_fold_outcomes (
        tenant_id, workspace_id, access_scope_id,
        transcript_fold_receipt_id, outcome_ordinal
    ),
    CONSTRAINT transcript_fold_exact_turns_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT transcript_fold_exact_turns_envelope_shape CHECK (
        octet_length(envelope_canonical_json) BETWEEN 2 AND 1048576
        AND envelope_sha256 ~ '^[a-f0-9]{64}$'
        AND envelope_sha256 = encode(
            pg_catalog.sha256(pg_catalog.convert_to(envelope_canonical_json, 'UTF8')),
            'hex'
        )
        AND jsonb_typeof(envelope_canonical_json::jsonb) = 'object'
        AND envelope_canonical_json::jsonb ->> 'envelope_kind' = 'conversation_event'
        AND envelope_canonical_json::jsonb ->> 'source_type' = 'transcript'
    )
);

CREATE TABLE memoriesql.transcript_fold_outcome_ranges (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    transcript_fold_receipt_id uuid NOT NULL,
    outcome_ordinal smallint NOT NULL,
    lineage_ordinal smallint NOT NULL,
    source_object_id uuid NOT NULL,
    source_range_receipt_id uuid NOT NULL,
    receipt_byte_start bigint NOT NULL,
    receipt_byte_end_exclusive bigint NOT NULL,
    receipt_payload_sha256 text NOT NULL,
    byte_start bigint NOT NULL,
    byte_end_exclusive bigint NOT NULL,
    source_bytes_sha256 text NOT NULL,
    CONSTRAINT transcript_fold_outcome_ranges_pk PRIMARY KEY (
        tenant_id, transcript_fold_receipt_id,
        outcome_ordinal, lineage_ordinal
    ),
    CONSTRAINT transcript_fold_outcome_ranges_outcome_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        transcript_fold_receipt_id, outcome_ordinal
    ) REFERENCES memoriesql.transcript_fold_outcomes (
        tenant_id, workspace_id, access_scope_id,
        transcript_fold_receipt_id, outcome_ordinal
    ),
    CONSTRAINT transcript_fold_outcome_ranges_source_range_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ) REFERENCES memoriesql.source_range_capture_receipts (
        tenant_id, workspace_id, access_scope_id, source_range_receipt_id
    ),
    CONSTRAINT transcript_fold_outcome_ranges_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT transcript_fold_outcome_ranges_shape CHECK (
        outcome_ordinal BETWEEN 0 AND 255
        AND lineage_ordinal BETWEEN 0 AND 1023
        AND receipt_byte_start >= 0
        AND receipt_byte_end_exclusive > receipt_byte_start
        AND byte_start >= receipt_byte_start
        AND byte_end_exclusive > byte_start
        AND byte_end_exclusive <= receipt_byte_end_exclusive
        AND receipt_payload_sha256 ~ '^[a-f0-9]{64}$'
        AND source_bytes_sha256 ~ '^[a-f0-9]{64}$'
    )
);

CREATE INDEX transcript_fold_receipts_source_order_idx
ON memoriesql.transcript_fold_receipts (
    tenant_id, source_object_id, source_revision_key,
    file_identity_key, byte_start, byte_end_exclusive
);

CREATE INDEX transcript_fold_outcomes_source_order_idx
ON memoriesql.transcript_fold_outcomes (
    tenant_id, source_object_id, byte_start, byte_end_exclusive
);

CREATE TRIGGER transcript_fold_receipts_immutable
BEFORE UPDATE OR DELETE ON memoriesql.transcript_fold_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE TRIGGER transcript_fold_outcomes_immutable
BEFORE UPDATE OR DELETE ON memoriesql.transcript_fold_outcomes
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE TRIGGER transcript_fold_exact_turns_immutable
BEFORE UPDATE OR DELETE ON memoriesql.transcript_fold_exact_turns
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE TRIGGER transcript_fold_outcome_ranges_immutable
BEFORE UPDATE OR DELETE ON memoriesql.transcript_fold_outcome_ranges
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.read_transcript_fold_window(
    requested_source_object_id uuid,
    requested_source_revision_key text,
    requested_file_identity_key text,
    requested_byte_start bigint,
    requested_max_bytes integer,
    request_id uuid,
    confirmation text
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
    range_record memoriesql.source_range_capture_receipts%ROWTYPE;
    durable_end bigint;
    window_end bigint;
    expected_offset bigint;
    receipt_payload bytea;
    slice_payload bytea;
    slice_start bigint;
    slice_end bigint;
    response_payload bytea := ''::bytea;
    response_ranges jsonb := '[]'::jsonb;
    response_file_identity jsonb;
    response_connector_id text;
    response_connector_version text;
    response_source_format text;
    response_captured_at timestamp with time zone;
BEGIN
    IF requested_source_object_id IS NULL
       OR btrim(requested_source_revision_key) = ''
       OR length(requested_source_revision_key) > 512
       OR btrim(requested_file_identity_key) = ''
       OR length(requested_file_identity_key) > 256
       OR requested_byte_start < 0
       OR requested_max_bytes NOT BETWEEN 1 AND 67108864
       OR request_id IS NULL
       OR confirmation <> 'confirm_local_transcript_fold' THEN
        RETURN NULL;
    END IF;

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR NOT memoriesql.application_authorize_resource(
            'source', requested_source_object_id,
            'source.raw.inspect', 'read', request_id
       )
       OR NOT memoriesql.application_authorize_resource(
            'source', requested_source_object_id,
            'source.raw.read', 'read', request_id
       ) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO workspace_record
    FROM memoriesql.workspaces AS workspace
    WHERE workspace.tenant_id = context_record.tenant_id
      AND workspace.workspace_id = context_record.workspace_id;
    IF context_record.principal_kind <> 'human'
       OR workspace_record.authority_mode <> 'personal_local' THEN
        RETURN NULL;
    END IF;
    PERFORM pg_catalog.set_config(
        'memoriesql.raw_inspection_request_id', request_id::text, true
    );
    PERFORM pg_catalog.set_config(
        'memoriesql.raw_detail_request_id', request_id::text, true
    );

    SELECT max(receipt.byte_end_exclusive) INTO durable_end
    FROM memoriesql.source_range_capture_receipts AS receipt
    WHERE receipt.tenant_id = context_record.tenant_id
      AND receipt.workspace_id = context_record.workspace_id
      AND receipt.source_object_id = requested_source_object_id
      AND receipt.source_revision_key = requested_source_revision_key
      AND receipt.file_identity_key = requested_file_identity_key;
    durable_end := COALESCE(durable_end, 0);
    IF requested_byte_start > durable_end THEN
        RAISE EXCEPTION 'transcript_fold_source_gap' USING ERRCODE = '40001';
    END IF;
    IF requested_byte_start = durable_end THEN
        RETURN jsonb_build_object(
            'status', 'end_of_durable_ranges',
            'source_object_id', requested_source_object_id,
            'source_revision_key', requested_source_revision_key,
            'file_identity', NULL,
            'file_identity_key', requested_file_identity_key,
            'connector_id', NULL,
            'observed_connector_version', NULL,
            'observed_source_format_version', NULL,
            'byte_start', requested_byte_start,
            'byte_end_exclusive', requested_byte_start,
            'durable_end_offset', durable_end,
            'payload_hex', '',
            'payload_sha256', encode(pg_catalog.sha256(''::bytea), 'hex'),
            'ranges', '[]'::jsonb,
            'first_captured_at', NULL
        );
    END IF;

    window_end := LEAST(durable_end, requested_byte_start + requested_max_bytes);
    expected_offset := requested_byte_start;
    FOR range_record IN
        SELECT receipt.*
        FROM memoriesql.source_range_capture_receipts AS receipt
        WHERE receipt.tenant_id = context_record.tenant_id
          AND receipt.workspace_id = context_record.workspace_id
          AND receipt.source_object_id = requested_source_object_id
          AND receipt.source_revision_key = requested_source_revision_key
          AND receipt.file_identity_key = requested_file_identity_key
          AND receipt.byte_start < window_end
          AND receipt.byte_end_exclusive > requested_byte_start
        ORDER BY receipt.byte_start, receipt.byte_end_exclusive
    LOOP
        slice_start := GREATEST(range_record.byte_start, requested_byte_start);
        slice_end := LEAST(range_record.byte_end_exclusive, window_end);
        IF slice_start <> expected_offset THEN
            RAISE EXCEPTION 'transcript_fold_source_gap' USING ERRCODE = '40001';
        END IF;
        IF response_file_identity IS NULL THEN
            response_file_identity := range_record.file_identity;
            response_connector_id := range_record.connector_id;
            response_connector_version := range_record.observed_connector_version;
            response_source_format := range_record.observed_source_format_version;
            response_captured_at := range_record.captured_at;
        ELSIF response_file_identity IS DISTINCT FROM range_record.file_identity
           OR response_connector_id IS DISTINCT FROM range_record.connector_id
           OR response_connector_version IS DISTINCT FROM
                range_record.observed_connector_version
           OR response_source_format IS DISTINCT FROM
                range_record.observed_source_format_version THEN
            RAISE EXCEPTION 'transcript_fold_source_profile_drift'
                USING ERRCODE = '40001';
        END IF;
        SELECT decode(
            string_agg(encode(chunk.payload_bytes, 'hex'), ''
                ORDER BY chunk.chunk_ordinal),
            'hex'
        ) INTO receipt_payload
        FROM memoriesql.captured_source_ranges AS chunk
        WHERE chunk.tenant_id = range_record.tenant_id
          AND chunk.source_range_receipt_id =
              range_record.source_range_receipt_id;
        IF receipt_payload IS NULL
           OR octet_length(receipt_payload) <> range_record.payload_byte_count
           OR encode(pg_catalog.sha256(receipt_payload), 'hex')
                IS DISTINCT FROM range_record.payload_sha256 THEN
            RAISE EXCEPTION 'transcript_fold_source_integrity'
                USING ERRCODE = '55000';
        END IF;
        slice_payload := substring(
            receipt_payload
            FROM (slice_start - range_record.byte_start + 1)::integer
            FOR (slice_end - slice_start)::integer
        );
        response_payload := response_payload || slice_payload;
        response_ranges := response_ranges || jsonb_build_array(
            jsonb_build_object(
                'source_range_receipt_id', range_record.source_range_receipt_id,
                'receipt_byte_start', range_record.byte_start,
                'receipt_byte_end_exclusive', range_record.byte_end_exclusive,
                'receipt_payload_sha256', range_record.payload_sha256,
                'byte_start', slice_start,
                'byte_end_exclusive', slice_end,
                'source_bytes_sha256',
                    encode(pg_catalog.sha256(slice_payload), 'hex')
            )
        );
        expected_offset := slice_end;
        EXIT WHEN expected_offset = window_end;
    END LOOP;
    IF expected_offset <> window_end
       OR octet_length(response_payload) <> window_end - requested_byte_start THEN
        RAISE EXCEPTION 'transcript_fold_source_gap' USING ERRCODE = '40001';
    END IF;
    RETURN jsonb_build_object(
        'status', 'data',
        'source_object_id', requested_source_object_id,
        'source_revision_key', requested_source_revision_key,
        'file_identity', response_file_identity,
        'file_identity_key', requested_file_identity_key,
        'connector_id', response_connector_id,
        'observed_connector_version', response_connector_version,
        'observed_source_format_version', response_source_format,
        'byte_start', requested_byte_start,
        'byte_end_exclusive', window_end,
        'durable_end_offset', durable_end,
        'payload_hex', encode(response_payload, 'hex'),
        'payload_sha256', encode(pg_catalog.sha256(response_payload), 'hex'),
        'ranges', response_ranges,
        'first_captured_at', response_captured_at
    );
END;
$$;

CREATE FUNCTION memoriesql.commit_transcript_fold(
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
    workspace_record memoriesql.workspaces%ROWTYPE;
    source_record memoriesql.source_objects%ROWTYPE;
    existing_idempotency memoriesql.idempotency_receipts%ROWTYPE;
    latest_fold memoriesql.transcript_fold_receipts%ROWTYPE;
    retained_receipt memoriesql.source_range_capture_receipts%ROWTYPE;
    capture_checkpoint memoriesql.capture_checkpoints%ROWTYPE;
    outcome jsonb;
    lineage jsonb;
    exact_turn jsonb;
    span jsonb;
    disposition jsonb;
    lineage_payload bytea;
    outcome_payload bytea;
    aggregate_payload bytea := ''::bytea;
    command_tenant_id uuid;
    command_workspace_id uuid;
    command_access_scope_id uuid;
    command_source_object_id uuid;
    command_start bigint;
    command_end bigint;
    command_start_record bigint;
    command_end_record bigint;
    expected_byte bigint;
    expected_record bigint;
    expected_lineage_byte bigint;
    outcome_start bigint;
    outcome_end bigint;
    outcome_start_record bigint;
    outcome_end_record bigint;
    outcome_ordinal integer := 0;
    lineage_ordinal integer;
    exact_count integer := 0;
    span_count integer := 0;
    disposition_count integer := 0;
    new_receipt_id uuid;
    computed_request_hash text;
    computed_checkpoint_hash text;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
    response jsonb;
    fold_request_id uuid := pg_catalog.uuidv7();
    reauthorization_request_id uuid;
    legacy_migration_cursor_sha256 text;
    legacy_migration_target_byte bigint;
    legacy_migration_target_record bigint;
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 16777216
       OR requested_command ->> 'contract_version' <> '1'
       OR requested_command ->> 'expected_schema_version' <> '14'
       OR requested_command ->> 'operation' <> 'commit_transcript_fold'
       OR jsonb_typeof(requested_command -> 'binding') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'file_identity') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'outcomes') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command -> 'outcomes') NOT BETWEEN 1 AND 256
       OR jsonb_typeof(requested_command -> 'checkpoint') IS DISTINCT FROM 'object'
       OR requested_command - ARRAY[
            'contract_version', 'expected_schema_version', 'operation',
            'idempotency_key', 'binding', 'capability_id', 'connector_id',
            'adapter_profile_version', 'observed_source_format_version',
            'source_revision_key', 'file_identity', 'file_identity_key',
            'byte_start', 'byte_end_exclusive', 'start_record_index',
            'end_record_index', 'complete_record_count',
            'source_bytes_sha256', 'outcomes_sha256', 'outcomes',
            'checkpoint', 'legacy_cursor'
          ] <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_object_keys(requested_command)) <> 22
       OR (requested_command -> 'binding') - ARRAY[
            'tenant_id', 'workspace_id', 'access_scope_id', 'source_object_id',
            'expected_source_object_schema_version'
          ] <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_object_keys(
            requested_command -> 'binding')) <> 5
       OR (requested_command -> 'file_identity') - ARRAY[
            'platform', 'device', 'inode', 'birth_time_ns'
          ] <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_object_keys(
            requested_command -> 'file_identity')) <> 4
       OR (requested_command -> 'checkpoint') - ARRAY[
            'checkpoint_key', 'expected_sequence', 'next_sequence',
            'checkpoint_hash'
          ] <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_object_keys(
            requested_command -> 'checkpoint')) <> 4
       OR btrim(requested_command ->> 'idempotency_key') = ''
       OR length(requested_command ->> 'idempotency_key') > 512
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'transcript fold contract is invalid'
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
    command_start_record :=
        (requested_command ->> 'start_record_index')::bigint;
    command_end_record :=
        (requested_command ->> 'end_record_index')::bigint;

    computed_checkpoint_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(
        concat(
            requested_command #>> '{checkpoint,checkpoint_key}', ':',
            requested_command #>> '{checkpoint,expected_sequence}', ':',
            requested_command #>> '{checkpoint,next_sequence}', ':',
            command_source_object_id, ':',
            requested_command ->> 'source_revision_key', ':',
            requested_command ->> 'file_identity_key', ':',
            command_start, ':', command_end, ':',
            command_start_record, ':', command_end_record, ':',
            requested_command ->> 'source_bytes_sha256', ':',
            requested_command ->> 'outcomes_sha256'
        ), 'UTF8'
    )), 'hex');

    IF requested_command #>> '{binding,expected_source_object_schema_version}' <> '1'
       OR requested_command ->> 'capability_id'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_command ->> 'capability_id') > 128
       OR requested_command ->> 'connector_id'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_command ->> 'connector_id') > 128
       OR (
            requested_command -> 'adapter_profile_version' <> 'null'::jsonb
            AND length(requested_command ->> 'adapter_profile_version')
                NOT BETWEEN 1 AND 128
          )
       OR (
            requested_command -> 'observed_source_format_version' <> 'null'::jsonb
            AND length(requested_command ->> 'observed_source_format_version')
                NOT BETWEEN 1 AND 128
          )
       OR btrim(requested_command ->> 'source_revision_key') = ''
       OR length(requested_command ->> 'source_revision_key') > 512
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
       OR command_start_record < 0
       OR command_end_record < command_start_record
       OR (requested_command ->> 'complete_record_count')::bigint
            <> command_end_record - command_start_record
       OR (requested_command ->> 'complete_record_count')::bigint
            NOT BETWEEN 0 AND 1000000
       OR requested_command ->> 'source_bytes_sha256' !~ '^[a-f0-9]{64}$'
       OR requested_command ->> 'outcomes_sha256' !~ '^[a-f0-9]{64}$'
       OR requested_command ->> 'outcomes_sha256' IS DISTINCT FROM encode(
            pg_catalog.sha256(pg_catalog.convert_to(
                memoriesql.canonical_semantic_json_text(
                    requested_command -> 'outcomes'
                ),
                'UTF8'
            )),
            'hex'
          )
       OR requested_command #>> '{checkpoint,checkpoint_key}'
            !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR (requested_command #>> '{checkpoint,expected_sequence}')::bigint < 0
       OR (requested_command #>> '{checkpoint,next_sequence}')::bigint
            <> (requested_command #>> '{checkpoint,expected_sequence}')::bigint + 1
       OR requested_command #>> '{checkpoint,checkpoint_hash}'
            IS DISTINCT FROM computed_checkpoint_hash THEN
        RAISE EXCEPTION 'transcript fold contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    computed_request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(
        requested_command::text, 'UTF8'
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
        RAISE EXCEPTION 'transcript fold is outside authorization'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO workspace_record
    FROM memoriesql.workspaces AS workspace
    WHERE workspace.tenant_id = context_record.tenant_id
      AND workspace.workspace_id = context_record.workspace_id;
    IF context_record.principal_kind <> 'human'
       OR workspace_record.authority_mode <> 'personal_local'
       OR NOT memoriesql.application_authorize_resource(
            'source', command_source_object_id,
            'source.raw.inspect', 'read', fold_request_id
       )
       OR NOT memoriesql.application_authorize_resource(
            'source', command_source_object_id,
            'source.raw.read', 'read', fold_request_id
       ) THEN
        RAISE EXCEPTION 'transcript fold requires personal-local raw authority'
            USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.set_config(
        'memoriesql.raw_inspection_request_id', fold_request_id::text, true
    );
    PERFORM pg_catalog.set_config(
        'memoriesql.raw_detail_request_id', fold_request_id::text, true
    );
    SELECT source.* INTO source_record
    FROM memoriesql.source_objects AS source
    WHERE source.tenant_id = command_tenant_id
      AND source.workspace_id = command_workspace_id
      AND source.access_scope_id = command_access_scope_id
      AND source.source_object_id = command_source_object_id
      AND source.schema_version =
          (requested_command #>>
            '{binding,expected_source_object_schema_version}')::integer;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'source object is unavailable' USING ERRCODE = '42501';
    END IF;

    expected_byte := command_start;
    expected_record := command_start_record;
    FOR outcome IN
        SELECT item.value
        FROM jsonb_array_elements(requested_command -> 'outcomes')
            WITH ORDINALITY AS item(value, ordinal)
        ORDER BY item.ordinal
    LOOP
        outcome_start := (outcome ->> 'byte_start')::bigint;
        outcome_end := (outcome ->> 'byte_end_exclusive')::bigint;
        outcome_start_record := (outcome ->> 'start_record_index')::bigint;
        outcome_end_record := (outcome ->> 'end_record_index')::bigint;
        exact_turn := outcome -> 'exact_turn';
        span := outcome -> 'transcript_span';
        disposition := outcome -> 'policy_disposition';
        IF jsonb_typeof(outcome) IS DISTINCT FROM 'object'
           OR outcome - ARRAY[
                'outcome_kind', 'byte_start', 'byte_end_exclusive',
                'start_record_index', 'end_record_index',
                'source_bytes_sha256', 'lineage', 'exact_turn',
                'transcript_span', 'policy_disposition'
              ] <> '{}'::jsonb
           OR (SELECT count(*) FROM jsonb_object_keys(outcome)) <> 10
           OR jsonb_typeof(outcome -> 'outcome_kind') IS DISTINCT FROM 'string'
           OR jsonb_typeof(outcome -> 'byte_start') IS DISTINCT FROM 'number'
           OR jsonb_typeof(outcome -> 'byte_end_exclusive')
                IS DISTINCT FROM 'number'
           OR jsonb_typeof(outcome -> 'start_record_index')
                IS DISTINCT FROM 'number'
           OR jsonb_typeof(outcome -> 'end_record_index')
                IS DISTINCT FROM 'number'
           OR jsonb_typeof(outcome -> 'source_bytes_sha256')
                IS DISTINCT FROM 'string'
           OR outcome_start <> expected_byte
           OR outcome_start_record <> expected_record
           OR outcome_end <= outcome_start
           OR outcome_end_record < outcome_start_record
           OR outcome ->> 'source_bytes_sha256' !~ '^[a-f0-9]{64}$'
           OR jsonb_typeof(outcome -> 'lineage') IS DISTINCT FROM 'array'
           OR jsonb_array_length(outcome -> 'lineage') NOT BETWEEN 1 AND 1024
           OR outcome ->> 'outcome_kind' NOT IN (
                'exact_turn', 'transcript_span', 'policy_disposition'
              ) THEN
            RAISE EXCEPTION 'transcript fold outcome is invalid'
                USING ERRCODE = '22023';
        END IF;
        IF outcome ->> 'outcome_kind' = 'exact_turn' THEN
            IF jsonb_typeof(exact_turn) IS DISTINCT FROM 'object'
               OR span <> 'null'::jsonb
               OR disposition <> 'null'::jsonb
               OR exact_turn - ARRAY[
                    'envelope_canonical_json', 'envelope_sha256'
                  ] <> '{}'::jsonb
               OR (SELECT count(*) FROM jsonb_object_keys(exact_turn)) <> 2
               OR jsonb_typeof(exact_turn -> 'envelope_canonical_json')
                    IS DISTINCT FROM 'string'
               OR jsonb_typeof(exact_turn -> 'envelope_sha256')
                    IS DISTINCT FROM 'string'
               OR octet_length(exact_turn ->> 'envelope_canonical_json')
                    NOT BETWEEN 2 AND 1048576
               OR exact_turn ->> 'envelope_sha256'
                    IS DISTINCT FROM encode(pg_catalog.sha256(
                        pg_catalog.convert_to(
                            exact_turn ->> 'envelope_canonical_json', 'UTF8'
                        )
                    ), 'hex')
               OR exact_turn ->> 'envelope_canonical_json'
                    IS DISTINCT FROM memoriesql.canonical_semantic_json_text(
                        (exact_turn ->> 'envelope_canonical_json')::jsonb
                    )
               OR (exact_turn ->> 'envelope_canonical_json')::jsonb
                    ->> 'envelope_kind' <> 'conversation_event'
               OR outcome_end_record <= outcome_start_record THEN
                RAISE EXCEPTION 'transcript fold exact turn is invalid'
                    USING ERRCODE = '22023';
            END IF;
            exact_count := exact_count + 1;
        ELSIF outcome ->> 'outcome_kind' = 'transcript_span' THEN
            IF exact_turn <> 'null'::jsonb
               OR jsonb_typeof(span) IS DISTINCT FROM 'object'
               OR disposition <> 'null'::jsonb
               OR span - ARRAY[
                    'transcript_span_version', 'source_object_id',
                    'source_revision_key', 'file_identity_key',
                    'byte_start', 'byte_end_exclusive',
                    'start_record_index', 'end_record_index',
                    'source_bytes_sha256', 'topology_status',
                    'content_storage'
                  ] <> '{}'::jsonb
               OR (SELECT count(*) FROM jsonb_object_keys(span)) <> 11
               OR span ->> 'transcript_span_version' IS DISTINCT FROM '1'
               OR span ->> 'source_object_id'
                    IS DISTINCT FROM command_source_object_id::text
               OR span ->> 'source_revision_key'
                    IS DISTINCT FROM requested_command ->> 'source_revision_key'
               OR span ->> 'file_identity_key'
                    IS DISTINCT FROM requested_command ->> 'file_identity_key'
               OR (span ->> 'byte_start')::bigint IS DISTINCT FROM outcome_start
               OR (span ->> 'byte_end_exclusive')::bigint
                    IS DISTINCT FROM outcome_end
               OR (span ->> 'start_record_index')::bigint
                    IS DISTINCT FROM outcome_start_record
               OR (span ->> 'end_record_index')::bigint
                    IS DISTINCT FROM outcome_end_record
               OR span ->> 'source_bytes_sha256'
                    IS DISTINCT FROM outcome ->> 'source_bytes_sha256'
               OR span ->> 'topology_status' IS DISTINCT FROM 'unknown'
               OR span ->> 'content_storage'
                    IS DISTINCT FROM 'retained_source_ranges'
               OR outcome_end_record <= outcome_start_record THEN
                RAISE EXCEPTION 'transcript span contract is invalid'
                    USING ERRCODE = '22023';
            END IF;
            span_count := span_count + 1;
        ELSE
            IF exact_turn <> 'null'::jsonb
               OR span <> 'null'::jsonb
               OR jsonb_typeof(disposition) IS DISTINCT FROM 'object'
               OR disposition - ARRAY[
                    'disposition_code', 'scope_reason'
                  ] <> '{}'::jsonb
               OR (SELECT count(*) FROM jsonb_object_keys(disposition)) <> 2
               OR jsonb_typeof(disposition -> 'disposition_code')
                    IS DISTINCT FROM 'string'
               OR jsonb_typeof(disposition -> 'scope_reason') NOT IN (
                    'string', 'null'
                  )
               OR disposition ->> 'disposition_code'
                    !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
               OR (
                    disposition -> 'scope_reason' <> 'null'::jsonb
                    AND disposition ->> 'scope_reason' NOT IN (
                        'off_date', 'missing_source_time', 'cross_midnight'
                    )
                  ) THEN
                RAISE EXCEPTION 'transcript fold disposition is invalid'
                    USING ERRCODE = '22023';
            END IF;
            disposition_count := disposition_count + 1;
        END IF;

        expected_lineage_byte := outcome_start;
        outcome_payload := ''::bytea;
        lineage_ordinal := 0;
        FOR lineage IN
            SELECT item.value
            FROM jsonb_array_elements(outcome -> 'lineage')
                WITH ORDINALITY AS item(value, ordinal)
            ORDER BY item.ordinal
        LOOP
            IF jsonb_typeof(lineage) IS DISTINCT FROM 'object'
               OR lineage - ARRAY[
                    'source_range_receipt_id', 'receipt_byte_start',
                    'receipt_byte_end_exclusive', 'receipt_payload_sha256',
                    'byte_start', 'byte_end_exclusive', 'source_bytes_sha256'
                  ] <> '{}'::jsonb
               OR (SELECT count(*) FROM jsonb_object_keys(lineage)) <> 7 THEN
                RAISE EXCEPTION 'transcript fold lineage is invalid'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO retained_receipt
            FROM memoriesql.source_range_capture_receipts AS receipt
            WHERE receipt.tenant_id = command_tenant_id
              AND receipt.workspace_id = command_workspace_id
              AND receipt.access_scope_id = command_access_scope_id
              AND receipt.source_range_receipt_id =
                  (lineage ->> 'source_range_receipt_id')::uuid;
            IF NOT FOUND
               OR retained_receipt.source_object_id <> command_source_object_id
               OR retained_receipt.source_revision_key <>
                    requested_command ->> 'source_revision_key'
               OR retained_receipt.file_identity_key <>
                    requested_command ->> 'file_identity_key'
               OR retained_receipt.file_identity IS DISTINCT FROM
                    requested_command -> 'file_identity'
               OR retained_receipt.connector_id <>
                    requested_command ->> 'connector_id'
               OR retained_receipt.observed_source_format_version
                    IS DISTINCT FROM NULLIF(
                        requested_command ->> 'observed_source_format_version', ''
                    )
               OR (
                    exact_count > 0
                    AND (
                        retained_receipt.observed_connector_version IS NULL
                        OR requested_command ->> 'adapter_profile_version'
                            IS DISTINCT FROM pg_catalog.concat(
                                retained_receipt.connector_id, '@',
                                retained_receipt.observed_connector_version, ':',
                                retained_receipt.observed_source_format_version
                            )
                    )
               )
               OR retained_receipt.byte_start <>
                    (lineage ->> 'receipt_byte_start')::bigint
               OR retained_receipt.byte_end_exclusive <>
                    (lineage ->> 'receipt_byte_end_exclusive')::bigint
               OR retained_receipt.payload_sha256 <>
                    lineage ->> 'receipt_payload_sha256'
               OR (lineage ->> 'byte_start')::bigint <> expected_lineage_byte
               OR (lineage ->> 'byte_start')::bigint < retained_receipt.byte_start
               OR (lineage ->> 'byte_end_exclusive')::bigint
                    > retained_receipt.byte_end_exclusive
               OR (lineage ->> 'byte_end_exclusive')::bigint
                    <= (lineage ->> 'byte_start')::bigint THEN
                RAISE EXCEPTION 'transcript fold lineage is invalid'
                    USING ERRCODE = '22023';
            END IF;
            SELECT substring(
                decode(string_agg(encode(chunk.payload_bytes, 'hex'), ''
                    ORDER BY chunk.chunk_ordinal), 'hex')
                FROM (
                    (lineage ->> 'byte_start')::bigint
                    - retained_receipt.byte_start + 1
                )::integer
                FOR (
                    (lineage ->> 'byte_end_exclusive')::bigint
                    - (lineage ->> 'byte_start')::bigint
                )::integer
            ) INTO lineage_payload
            FROM memoriesql.captured_source_ranges AS chunk
            WHERE chunk.tenant_id = retained_receipt.tenant_id
              AND chunk.source_range_receipt_id =
                  retained_receipt.source_range_receipt_id;
            IF lineage_payload IS NULL
               OR encode(pg_catalog.sha256(lineage_payload), 'hex')
                    IS DISTINCT FROM lineage ->> 'source_bytes_sha256' THEN
                RAISE EXCEPTION 'transcript fold lineage integrity mismatch'
                    USING ERRCODE = '22023';
            END IF;
            outcome_payload := outcome_payload || lineage_payload;
            expected_lineage_byte :=
                (lineage ->> 'byte_end_exclusive')::bigint;
            lineage_ordinal := lineage_ordinal + 1;
        END LOOP;
        IF expected_lineage_byte <> outcome_end
           OR encode(pg_catalog.sha256(outcome_payload), 'hex')
                IS DISTINCT FROM outcome ->> 'source_bytes_sha256' THEN
            RAISE EXCEPTION 'transcript fold outcome integrity mismatch'
                USING ERRCODE = '22023';
        END IF;
        aggregate_payload := aggregate_payload || outcome_payload;
        expected_byte := outcome_end;
        expected_record := outcome_end_record;
        outcome_ordinal := outcome_ordinal + 1;
    END LOOP;
    IF expected_byte <> command_end
       OR expected_record <> command_end_record
       OR encode(pg_catalog.sha256(aggregate_payload), 'hex')
            IS DISTINCT FROM requested_command ->> 'source_bytes_sha256' THEN
        RAISE EXCEPTION 'transcript fold aggregate integrity mismatch'
            USING ERRCODE = '22023';
    END IF;
    IF exact_count > 0
       AND (
            requested_command -> 'adapter_profile_version' = 'null'::jsonb
            OR requested_command -> 'observed_source_format_version' = 'null'::jsonb
            OR requested_command ->> 'adapter_profile_version' NOT IN (
                'memoriesql.codex-passive@2:codex.rollout.paginated.v1',
                'memoriesql.codex-passive@2:codex.rollout.legacy-vscode.v1',
                'memoriesql.claude-code@1:claude-code.session-jsonl.v2.1.231'
            )
       ) THEN
        RAISE EXCEPTION 'transcript fold exact turn is unqualified'
            USING ERRCODE = '22023';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        command_tenant_id::text || ':transcript-fold:'
        || (requested_command #>> '{checkpoint,checkpoint_key}'), 0
    ));
    SELECT * INTO existing_idempotency
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.operation_kind = 'transcript_fold.commit'
      AND receipt.idempotency_key = requested_command ->> 'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF existing_idempotency.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF existing_idempotency.status <> 'succeeded'
           OR existing_idempotency.response_receipt IS NULL THEN
            RAISE EXCEPTION 'transcript fold receipt is incomplete'
                USING ERRCODE = '55000';
        END IF;
        RETURN existing_idempotency.response_receipt
            || jsonb_build_object('replayed', true);
    END IF;

    SELECT * INTO latest_fold
    FROM memoriesql.transcript_fold_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.workspace_id = command_workspace_id
      AND receipt.access_scope_id = command_access_scope_id
      AND receipt.checkpoint_key =
          requested_command #>> '{checkpoint,checkpoint_key}'
    ORDER BY receipt.checkpoint_sequence DESC
    LIMIT 1
    FOR UPDATE;
    IF FOUND THEN
        IF latest_fold.checkpoint_sequence <>
                (requested_command #>> '{checkpoint,expected_sequence}')::bigint
           OR latest_fold.byte_end_exclusive <> command_start
           OR latest_fold.end_record_index <> command_start_record
           OR latest_fold.source_object_id <> command_source_object_id
           OR latest_fold.source_revision_key <>
                requested_command ->> 'source_revision_key'
           OR latest_fold.file_identity_key <>
                requested_command ->> 'file_identity_key'
           OR requested_command -> 'legacy_cursor' <> 'null'::jsonb THEN
            RAISE EXCEPTION 'transcript_fold_checkpoint_conflict'
                USING ERRCODE = '40001';
        END IF;
        IF latest_fold.legacy_migration_target_byte_end_exclusive IS NOT NULL
           AND latest_fold.byte_end_exclusive <
                latest_fold.legacy_migration_target_byte_end_exclusive THEN
            IF jsonb_array_length(requested_command -> 'outcomes') <> 1
               OR requested_command #>> '{outcomes,0,outcome_kind}'
                    <> 'policy_disposition'
               OR requested_command #>>
                    '{outcomes,0,policy_disposition,disposition_code}'
                    <> 'fold.legacy-cursor-receipt-migration'
               OR command_end >
                    latest_fold.legacy_migration_target_byte_end_exclusive
               OR command_end_record >
                    latest_fold.legacy_migration_target_record_index THEN
                RAISE EXCEPTION 'transcript_fold_legacy_migration_incomplete'
                    USING ERRCODE = '40001';
            END IF;
            legacy_migration_cursor_sha256 :=
                latest_fold.legacy_migration_cursor_sha256;
            legacy_migration_target_byte :=
                latest_fold.legacy_migration_target_byte_end_exclusive;
            legacy_migration_target_record :=
                latest_fold.legacy_migration_target_record_index;
        END IF;
    ELSIF requested_command -> 'legacy_cursor' = 'null'::jsonb THEN
        IF (requested_command #>> '{checkpoint,expected_sequence}')::bigint <> 0
           OR command_start <> 0
           OR command_start_record <> 0 THEN
            RAISE EXCEPTION 'transcript_fold_checkpoint_conflict'
                USING ERRCODE = '40001';
        END IF;
    ELSE
        IF jsonb_typeof(requested_command -> 'legacy_cursor')
                IS DISTINCT FROM 'object'
           OR requested_command #>> '{legacy_cursor,cursor_version}'
                NOT IN ('1', '2')
           OR requested_command #>> '{legacy_cursor,cursor_sha256}'
                !~ '^[a-f0-9]{64}$'
           OR (requested_command #>>
                '{legacy_cursor,acknowledged_byte_offset}')::bigint < command_end
           OR (requested_command #>>
                '{legacy_cursor,acknowledged_record_index}')::bigint
                < command_end_record
           OR (requested_command #>>
                '{legacy_cursor,checkpoint_sequence}')::bigint
                <> (requested_command #>>
                    '{checkpoint,expected_sequence}')::bigint
           OR command_start <> 0
           OR command_start_record <> 0
           OR jsonb_array_length(requested_command -> 'outcomes') <> 1
           OR requested_command #>> '{outcomes,0,outcome_kind}'
                <> 'policy_disposition'
           OR requested_command #>>
                '{outcomes,0,policy_disposition,disposition_code}'
                <> 'fold.legacy-cursor-receipt-migration' THEN
            RAISE EXCEPTION 'transcript fold legacy cursor is invalid'
                USING ERRCODE = '22023';
        END IF;
        IF (requested_command #>>
                '{legacy_cursor,checkpoint_sequence}')::bigint > 0 THEN
            SELECT * INTO capture_checkpoint
            FROM memoriesql.capture_checkpoints AS checkpoint
            WHERE checkpoint.tenant_id = command_tenant_id
              AND checkpoint.workspace_id = command_workspace_id
              AND checkpoint.access_scope_id = command_access_scope_id
              AND checkpoint.checkpoint_key =
                  requested_command #>> '{checkpoint,checkpoint_key}';
            IF NOT FOUND
               OR capture_checkpoint.checkpoint_sequence <>
                    (requested_command #>>
                        '{legacy_cursor,checkpoint_sequence}')::bigint
               OR (
                    requested_command #>
                        '{legacy_cursor,last_receipt_event_id}' <> 'null'::jsonb
                    AND capture_checkpoint.last_event_id <>
                        (requested_command #>>
                            '{legacy_cursor,last_receipt_event_id}')::uuid
                  ) THEN
                RAISE EXCEPTION 'transcript_fold_legacy_receipt_conflict'
                    USING ERRCODE = '40001';
            END IF;
        END IF;
        legacy_migration_cursor_sha256 :=
            requested_command #>> '{legacy_cursor,cursor_sha256}';
        legacy_migration_target_byte := (requested_command #>>
            '{legacy_cursor,acknowledged_byte_offset}')::bigint;
        legacy_migration_target_record := (requested_command #>>
            '{legacy_cursor,acknowledged_record_index}')::bigint;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM memoriesql.transcript_fold_receipts AS prior
        WHERE prior.tenant_id = command_tenant_id
          AND prior.source_object_id = command_source_object_id
          AND prior.source_revision_key = requested_command ->> 'source_revision_key'
          AND prior.file_identity_key = requested_command ->> 'file_identity_key'
          AND prior.byte_start < command_end
          AND prior.byte_end_exclusive > command_start
    ) THEN
        RAISE EXCEPTION 'transcript_fold_overlap' USING ERRCODE = '23505';
    END IF;

    database_now := pg_catalog.clock_timestamp();
    reauthorization_request_id := pg_catalog.uuidv7();
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
        RAISE EXCEPTION 'transcript fold is outside current authorization'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO workspace_record
    FROM memoriesql.workspaces AS workspace
    WHERE workspace.tenant_id = context_record.tenant_id
      AND workspace.workspace_id = context_record.workspace_id;
    IF context_record.principal_kind <> 'human'
       OR workspace_record.authority_mode <> 'personal_local'
       OR NOT memoriesql.application_authorize_resource(
            'source', command_source_object_id,
            'source.raw.inspect', 'read', reauthorization_request_id
       )
       OR NOT memoriesql.application_authorize_resource(
            'source', command_source_object_id,
            'source.raw.read', 'read', reauthorization_request_id
       ) THEN
        RAISE EXCEPTION 'transcript fold lost current raw authority'
            USING ERRCODE = '42501';
    END IF;
    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, 'transcript_fold.commit',
        requested_command ->> 'idempotency_key', computed_request_hash,
        'in_progress', 'source', command_source_object_id, 1,
        database_now, database_now
    );
    INSERT INTO memoriesql.transcript_fold_receipts (
        tenant_id, workspace_id, access_scope_id,
        transcript_fold_receipt_id, idempotency_receipt_id, source_object_id,
        source_revision_key, file_identity_key, file_identity, capability_id,
        connector_id, adapter_profile_version, observed_source_format_version,
        byte_start, byte_end_exclusive, start_record_index, end_record_index,
        complete_record_count, source_bytes_sha256, outcomes_sha256,
        outcome_count, exact_turn_count, transcript_span_count,
        policy_disposition_count, checkpoint_key, checkpoint_sequence,
        legacy_migration_cursor_sha256,
        legacy_migration_target_byte_end_exclusive,
        legacy_migration_target_record_index,
        folded_by_principal_id, folded_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, new_receipt_id, command_source_object_id,
        requested_command ->> 'source_revision_key',
        requested_command ->> 'file_identity_key',
        requested_command -> 'file_identity',
        requested_command ->> 'capability_id',
        requested_command ->> 'connector_id',
        NULLIF(requested_command ->> 'adapter_profile_version', ''),
        NULLIF(requested_command ->> 'observed_source_format_version', ''),
        command_start, command_end, command_start_record, command_end_record,
        (requested_command ->> 'complete_record_count')::integer,
        requested_command ->> 'source_bytes_sha256',
        requested_command ->> 'outcomes_sha256',
        jsonb_array_length(requested_command -> 'outcomes'),
        exact_count, span_count, disposition_count,
        requested_command #>> '{checkpoint,checkpoint_key}',
        (requested_command #>> '{checkpoint,next_sequence}')::bigint,
        legacy_migration_cursor_sha256,
        legacy_migration_target_byte,
        legacy_migration_target_record,
        context_record.principal_id, database_now
    );

    outcome_ordinal := 0;
    FOR outcome IN
        SELECT item.value
        FROM jsonb_array_elements(requested_command -> 'outcomes')
            WITH ORDINALITY AS item(value, ordinal)
        ORDER BY item.ordinal
    LOOP
        INSERT INTO memoriesql.transcript_fold_outcomes (
            tenant_id, workspace_id, access_scope_id,
            transcript_fold_receipt_id, source_object_id, outcome_ordinal,
            outcome_kind, byte_start, byte_end_exclusive,
            start_record_index, end_record_index, source_bytes_sha256,
            exact_envelope_sha256, disposition_code, scope_reason,
            adapter_profile_version, folded_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_receipt_id, command_source_object_id, outcome_ordinal,
            outcome ->> 'outcome_kind',
            (outcome ->> 'byte_start')::bigint,
            (outcome ->> 'byte_end_exclusive')::bigint,
            (outcome ->> 'start_record_index')::bigint,
            (outcome ->> 'end_record_index')::bigint,
            outcome ->> 'source_bytes_sha256',
            NULLIF(outcome #>> '{exact_turn,envelope_sha256}', ''),
            NULLIF(outcome #>> '{policy_disposition,disposition_code}', ''),
            NULLIF(outcome #>> '{policy_disposition,scope_reason}', ''),
            NULLIF(requested_command ->> 'adapter_profile_version', ''),
            database_now
        );
        IF outcome ->> 'outcome_kind' = 'exact_turn' THEN
            INSERT INTO memoriesql.transcript_fold_exact_turns (
                tenant_id, workspace_id, access_scope_id,
                transcript_fold_receipt_id, outcome_ordinal,
                source_object_id, envelope_canonical_json,
                envelope_sha256, folded_at
            ) VALUES (
                command_tenant_id, command_workspace_id, command_access_scope_id,
                new_receipt_id, outcome_ordinal, command_source_object_id,
                outcome #>> '{exact_turn,envelope_canonical_json}',
                outcome #>> '{exact_turn,envelope_sha256}', database_now
            );
        END IF;
        lineage_ordinal := 0;
        FOR lineage IN
            SELECT item.value
            FROM jsonb_array_elements(outcome -> 'lineage')
                WITH ORDINALITY AS item(value, ordinal)
            ORDER BY item.ordinal
        LOOP
            INSERT INTO memoriesql.transcript_fold_outcome_ranges (
                tenant_id, workspace_id, access_scope_id,
                transcript_fold_receipt_id, outcome_ordinal,
                lineage_ordinal, source_object_id, source_range_receipt_id,
                receipt_byte_start, receipt_byte_end_exclusive,
                receipt_payload_sha256, byte_start, byte_end_exclusive,
                source_bytes_sha256
            ) VALUES (
                command_tenant_id, command_workspace_id, command_access_scope_id,
                new_receipt_id, outcome_ordinal, lineage_ordinal,
                command_source_object_id,
                (lineage ->> 'source_range_receipt_id')::uuid,
                (lineage ->> 'receipt_byte_start')::bigint,
                (lineage ->> 'receipt_byte_end_exclusive')::bigint,
                lineage ->> 'receipt_payload_sha256',
                (lineage ->> 'byte_start')::bigint,
                (lineage ->> 'byte_end_exclusive')::bigint,
                lineage ->> 'source_bytes_sha256'
            );
            lineage_ordinal := lineage_ordinal + 1;
        END LOOP;
        outcome_ordinal := outcome_ordinal + 1;
    END LOOP;

    response := jsonb_build_object(
        'transcript_fold_receipt_id', new_receipt_id,
        'idempotency_receipt_id', new_receipt_id,
        'source_object_id', command_source_object_id,
        'source_revision_key', requested_command ->> 'source_revision_key',
        'file_identity_key', requested_command ->> 'file_identity_key',
        'byte_start', command_start,
        'byte_end_exclusive', command_end,
        'start_record_index', command_start_record,
        'end_record_index', command_end_record,
        'complete_record_count',
            (requested_command ->> 'complete_record_count')::integer,
        'source_bytes_sha256', requested_command ->> 'source_bytes_sha256',
        'outcomes_sha256', requested_command ->> 'outcomes_sha256',
        'exact_turn_count', exact_count,
        'transcript_span_count', span_count,
        'policy_disposition_count', disposition_count,
        'checkpoint_key', requested_command #>> '{checkpoint,checkpoint_key}',
        'checkpoint_sequence',
            (requested_command #>> '{checkpoint,next_sequence}')::bigint,
        'authority_principal_id', context_record.principal_id,
        'folded_at', database_now,
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

CREATE FUNCTION memoriesql.list_transcript_fold_inbox(
    requested_source_object_id uuid
)
RETURNS SETOF jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    request_id uuid := pg_catalog.uuidv7();
BEGIN
    IF requested_source_object_id IS NULL THEN
        RETURN;
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR NOT memoriesql.application_authorize_resource(
            'source', requested_source_object_id,
            'source.read', 'read', request_id
       ) THEN
        RETURN;
    END IF;
    PERFORM pg_catalog.set_config(
        'memoriesql.transcript_fold_inbox_request_id', request_id::text, true
    );
    RETURN QUERY
    SELECT jsonb_build_object(
        'transcript_fold_receipt_id', outcome.transcript_fold_receipt_id,
        'outcome_ordinal', outcome.outcome_ordinal,
        'source_object_id', outcome.source_object_id,
        'outcome_kind', outcome.outcome_kind,
        'byte_start', outcome.byte_start,
        'byte_end_exclusive', outcome.byte_end_exclusive,
        'start_record_index', outcome.start_record_index,
        'end_record_index', outcome.end_record_index,
        'source_bytes_sha256', outcome.source_bytes_sha256,
        'exact_envelope_sha256', outcome.exact_envelope_sha256,
        'disposition_code', outcome.disposition_code,
        'adapter_profile_version', outcome.adapter_profile_version,
        'folded_at', outcome.folded_at
    )
    FROM memoriesql.transcript_fold_outcomes AS outcome
    WHERE outcome.tenant_id = context_record.tenant_id
      AND outcome.workspace_id = context_record.workspace_id
      AND outcome.source_object_id = requested_source_object_id
    ORDER BY outcome.byte_start, outcome.outcome_ordinal;
END;
$$;

CREATE POLICY transcript_fold_receipts_inbox
ON memoriesql.transcript_fold_receipts
FOR SELECT
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.read', 'read'
    )
    AND NULLIF(pg_catalog.current_setting(
        'memoriesql.transcript_fold_inbox_request_id', true
    ), '') IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.authorization_audit_events AS audit
          ON audit.tenant_id = context.tenant_id
         AND audit.workspace_id = context.workspace_id
         AND audit.authenticated_principal_id = context.principal_id
         AND audit.request_id::text = pg_catalog.current_setting(
            'memoriesql.transcript_fold_inbox_request_id', true
         )
         AND audit.resource_kind = 'source'
         AND audit.resource_id = transcript_fold_receipts.source_object_id
         AND audit.capability_key = 'source.read'
         AND audit.permission_key = 'read'
         AND audit.decision = 'allowed'
         AND audit.recorded_at >= pg_catalog.transaction_timestamp()
    )
);

CREATE POLICY transcript_fold_outcomes_inbox
ON memoriesql.transcript_fold_outcomes
FOR SELECT
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.read', 'read'
    )
    AND NULLIF(pg_catalog.current_setting(
        'memoriesql.transcript_fold_inbox_request_id', true
    ), '') IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.authorization_audit_events AS audit
          ON audit.tenant_id = context.tenant_id
         AND audit.workspace_id = context.workspace_id
         AND audit.authenticated_principal_id = context.principal_id
         AND audit.request_id::text = pg_catalog.current_setting(
            'memoriesql.transcript_fold_inbox_request_id', true
         )
         AND audit.resource_kind = 'source'
         AND audit.resource_id = transcript_fold_outcomes.source_object_id
         AND audit.capability_key = 'source.read'
         AND audit.permission_key = 'read'
         AND audit.decision = 'allowed'
         AND audit.recorded_at >= pg_catalog.transaction_timestamp()
    )
);

ALTER TABLE memoriesql.transcript_fold_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_outcomes FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_exact_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_exact_turns FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_outcome_ranges ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.transcript_fold_outcome_ranges FORCE ROW LEVEL SECURITY;

REVOKE ALL ON memoriesql.transcript_fold_receipts FROM PUBLIC;
REVOKE ALL ON memoriesql.transcript_fold_outcomes FROM PUBLIC;
REVOKE ALL ON memoriesql.transcript_fold_exact_turns FROM PUBLIC;
REVOKE ALL ON memoriesql.transcript_fold_outcome_ranges FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.read_transcript_fold_window(
    uuid, text, text, bigint, integer, uuid, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.commit_transcript_fold(
    jsonb, timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.list_transcript_fold_inbox(uuid) FROM PUBLIC;

GRANT SELECT ON memoriesql.transcript_fold_receipts,
    memoriesql.transcript_fold_outcomes,
    memoriesql.transcript_fold_exact_turns,
    memoriesql.transcript_fold_outcome_ranges
TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.read_transcript_fold_window(
    uuid, text, text, bigint, integer, uuid, text
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.commit_transcript_fold(
    jsonb, timestamptz
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.list_transcript_fold_inbox(uuid)
TO memoriesql_application;

COMMENT ON TABLE memoriesql.transcript_fold_receipts IS
    'Immutable exact-turn-first fold receipts over retained source ranges; no semantic rows are created.';
COMMENT ON TABLE memoriesql.transcript_fold_outcomes IS
    'Content-free exact-turn, transcript-span, and policy outcome index with complete record coverage.';
COMMENT ON TABLE memoriesql.transcript_fold_exact_turns IS
    'Raw-protected unchanged canonical exact-turn envelopes retained for later adoption, not applied as memories.';
COMMENT ON TABLE memoriesql.transcript_fold_outcome_ranges IS
    'Exact byte-slice lineage from each fold outcome to immutable schema-13 source-range receipts.';
COMMENT ON FUNCTION memoriesql.read_transcript_fold_window(
    uuid, text, text, bigint, integer, uuid, text
) IS 'Returns owner-confirmed bounded retained bytes for deterministic local folding; raw access is audited.';
COMMENT ON FUNCTION memoriesql.commit_transcript_fold(jsonb, timestamptz) IS
    'Atomically records evidence-only fold outcomes, lineage, idempotency, and replacement adapter-cursor progress.';
COMMENT ON FUNCTION memoriesql.list_transcript_fold_inbox(uuid) IS
    'Returns content-free authorized fold dispositions and bounds for one source.';
