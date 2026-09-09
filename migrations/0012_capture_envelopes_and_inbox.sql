-- PR-02A versioned capture envelopes, durable adapter receipts, and authorized
-- inbox reads. Canonical evidence, thin beads, tasks, outbox, idempotency, and
-- checkpoint authority remain owned by memoriesql.accept_source_event().

CREATE TABLE memoriesql.capture_deliveries (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    envelope_event_id uuid NOT NULL,
    event_id uuid,
    event_kind text NOT NULL,
    supersedes_event_id uuid,
    supersedes_source_identity_key text,
    semantic_task_id uuid,
    envelope_kind text NOT NULL,
    envelope_schema_version integer NOT NULL,
    envelope_hash text NOT NULL,
    request_hash text NOT NULL,
    source_identity jsonb NOT NULL,
    conversation_identity jsonb,
    participant_identities jsonb NOT NULL,
    client_identity jsonb,
    project_hint jsonb,
    capture_surface text NOT NULL,
    source_locator jsonb,
    parser_receipt jsonb NOT NULL,
    policy_references jsonb NOT NULL,
    final_status text NOT NULL,
    checkpoint_sequence bigint,
    authority_principal_id uuid NOT NULL,
    delivered_at timestamp with time zone NOT NULL,
    CONSTRAINT capture_deliveries_pk PRIMARY KEY (tenant_id, delivery_id),
    CONSTRAINT capture_deliveries_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, delivery_id
    ),
    CONSTRAINT capture_deliveries_receipt_uq UNIQUE (
        tenant_id, idempotency_receipt_id
    ),
    CONSTRAINT capture_deliveries_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT capture_deliveries_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT capture_deliveries_event_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id
    ) REFERENCES memoriesql.source_events (
        tenant_id, workspace_id, access_scope_id, event_id
    ),
    CONSTRAINT capture_deliveries_supersedes_event_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, supersedes_event_id
    ) REFERENCES memoriesql.source_events (
        tenant_id, workspace_id, access_scope_id, event_id
    ),
    CONSTRAINT capture_deliveries_task_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, semantic_task_id
    ) REFERENCES memoriesql.semantic_tasks (
        tenant_id, workspace_id, access_scope_id, task_id
    ),
    CONSTRAINT capture_deliveries_principal_fk FOREIGN KEY (
        tenant_id, authority_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT capture_deliveries_envelope_kind_supported CHECK (
        envelope_kind IN ('source_event', 'conversation_event')
    ),
    CONSTRAINT capture_deliveries_event_kind_supported CHECK (
        event_kind IN (
            'source_observed', 'source_amended', 'source_deleted',
            'turn_finalized', 'turn_amended'
        )
    ),
    CONSTRAINT capture_deliveries_supersession_shape CHECK (
        (
            event_kind IN ('source_amended', 'source_deleted', 'turn_amended')
            AND supersedes_event_id IS NOT NULL
            AND btrim(supersedes_source_identity_key) <> ''
        ) OR (
            event_kind NOT IN ('source_amended', 'source_deleted', 'turn_amended')
            AND supersedes_event_id IS NULL
            AND supersedes_source_identity_key IS NULL
        )
    ),
    CONSTRAINT capture_deliveries_schema_version_supported CHECK (
        envelope_schema_version = 1
    ),
    CONSTRAINT capture_deliveries_hash_shapes CHECK (
        envelope_hash ~ '^[a-f0-9]{64}$'
        AND request_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT capture_deliveries_identity_object CHECK (
        jsonb_typeof(source_identity) = 'object'
    ),
    CONSTRAINT capture_deliveries_conversation_object CHECK (
        conversation_identity IS NULL
        OR jsonb_typeof(conversation_identity) = 'object'
    ),
    CONSTRAINT capture_deliveries_participants_array CHECK (
        jsonb_typeof(participant_identities) = 'array'
    ),
    CONSTRAINT capture_deliveries_client_object CHECK (
        client_identity IS NULL OR jsonb_typeof(client_identity) = 'object'
    ),
    CONSTRAINT capture_deliveries_project_object CHECK (
        project_hint IS NULL OR jsonb_typeof(project_hint) = 'object'
    ),
    CONSTRAINT capture_deliveries_locator_object CHECK (
        source_locator IS NULL OR jsonb_typeof(source_locator) = 'object'
    ),
    CONSTRAINT capture_deliveries_parser_object CHECK (
        jsonb_typeof(parser_receipt) = 'object'
    ),
    CONSTRAINT capture_deliveries_policy_object CHECK (
        jsonb_typeof(policy_references) = 'object'
    ),
    CONSTRAINT capture_deliveries_surface_supported CHECK (
        capture_surface IN (
            'artifact', 'live_hook', 'harness', 'browser', 'import',
            'explicit_checkpoint', 'synthetic'
        )
    ),
    CONSTRAINT capture_deliveries_status_supported CHECK (
        final_status IN (
            'duplicate', 'quarantined', 'accepted', 'amended', 'deleted'
        )
    ),
    CONSTRAINT capture_deliveries_checkpoint_positive CHECK (
        checkpoint_sequence IS NULL OR checkpoint_sequence > 0
    ),
    CONSTRAINT capture_deliveries_result_shape CHECK (
        (final_status IN ('quarantined', 'deleted') AND semantic_task_id IS NULL)
        OR (
            final_status IN ('duplicate', 'accepted', 'amended')
            AND event_id IS NOT NULL
            AND semantic_task_id IS NOT NULL
        )
    )
);

CREATE INDEX capture_deliveries_source_order_idx
ON memoriesql.capture_deliveries (
    tenant_id, source_object_id, delivered_at DESC, delivery_id DESC
);

CREATE TABLE memoriesql.capture_delivery_status_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    status_event_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    status_sequence smallint NOT NULL,
    ingress_status text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    CONSTRAINT capture_delivery_status_events_pk PRIMARY KEY (
        tenant_id, status_event_id
    ),
    CONSTRAINT capture_delivery_status_events_order_uq UNIQUE (
        tenant_id, delivery_id, status_sequence
    ),
    CONSTRAINT capture_delivery_status_events_delivery_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, delivery_id
    ) REFERENCES memoriesql.capture_deliveries (
        tenant_id, workspace_id, access_scope_id, delivery_id
    ),
    CONSTRAINT capture_delivery_status_events_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ) REFERENCES memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id
    ),
    CONSTRAINT capture_delivery_status_events_sequence_positive CHECK (
        status_sequence > 0
    ),
    CONSTRAINT capture_delivery_status_events_status_supported CHECK (
        ingress_status IN (
            'detected', 'received', 'duplicate', 'quarantined', 'accepted',
            'amended', 'deleted'
        )
    )
);

CREATE TRIGGER capture_deliveries_immutable
BEFORE UPDATE OR DELETE ON memoriesql.capture_deliveries
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE TRIGGER capture_delivery_status_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.capture_delivery_status_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.capture_submission_receipt(
    requested_delivery_id uuid,
    requested_replayed boolean
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT jsonb_build_object(
        'delivery_id', delivery.delivery_id,
        'event_id', delivery.event_id,
        'idempotency_receipt_id', delivery.idempotency_receipt_id,
        'operation_id', receipt.idempotency_key,
        'request_hash', delivery.request_hash,
        'checkpoint_sequence', delivery.checkpoint_sequence,
        'ingress_status', delivery.final_status,
        'authority_principal_id', delivery.authority_principal_id,
        'replayed', requested_replayed,
        'durable', true
    )
    FROM memoriesql.current_authorization_context() AS context
    JOIN memoriesql.capture_deliveries AS delivery
      ON delivery.tenant_id = context.tenant_id
     AND delivery.workspace_id = context.workspace_id
     AND delivery.delivery_id = requested_delivery_id
    JOIN memoriesql.idempotency_receipts AS receipt
      ON receipt.tenant_id = delivery.tenant_id
     AND receipt.idempotency_receipt_id = delivery.idempotency_receipt_id
    WHERE memoriesql.current_context_source_authorized(
        delivery.access_scope_id, delivery.source_object_id,
        'memory.capture', 'write'
    )
$$;

CREATE FUNCTION memoriesql.submit_capture_envelope(
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
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    acceptance record;
    delivery_record memoriesql.capture_deliveries%ROWTYPE;
    envelope jsonb;
    binding jsonb;
    inner_command jsonb;
    command_tenant_id uuid;
    command_workspace_id uuid;
    command_access_scope_id uuid;
    command_source_object_id uuid;
    command_event_id uuid;
    linked_event_id uuid;
    delivery_id uuid;
    computed_request_hash text;
    final_status text;
    control_operation boolean;
    envelope_actor_id text := 'unknown';
    envelope_actor_kind text := 'unknown';
    observation_count integer;
    participant_match_count integer;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
    response jsonb;
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 4194304
       OR requested_command ->> 'contract_version' <> '1'
       OR requested_command ->> 'expected_schema_version' <> '12'
       OR jsonb_typeof(requested_command -> 'envelope') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'binding') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'envelope_canonical_json')
            IS DISTINCT FROM 'string'
       OR requested_command ->> 'envelope_hash' !~ '^[a-f0-9]{64}$'
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'capture envelope contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    envelope := requested_command -> 'envelope';
    binding := requested_command -> 'binding';
    IF envelope ->> 'schema_version' <> '1'
       OR envelope ->> 'envelope_kind'
            NOT IN ('source_event', 'conversation_event')
       OR jsonb_typeof(envelope -> 'source_identity') IS DISTINCT FROM 'object'
       OR jsonb_typeof(envelope -> 'parser_receipt') IS DISTINCT FROM 'object'
       OR jsonb_typeof(envelope -> 'policies') IS DISTINCT FROM 'object'
       OR jsonb_typeof(envelope -> 'units') IS DISTINCT FROM 'array'
       OR requested_command ->> 'envelope_canonical_json' IS NULL
       OR pg_catalog.octet_length(
            requested_command ->> 'envelope_canonical_json'
          ) > 1048576
       OR (requested_command ->> 'envelope_canonical_json')::jsonb
            IS DISTINCT FROM envelope
       OR encode(pg_catalog.sha256(pg_catalog.convert_to(
            requested_command ->> 'envelope_canonical_json', 'UTF8'
          )), 'hex') IS DISTINCT FROM requested_command ->> 'envelope_hash'
       OR btrim(requested_command ->> 'idempotency_key') = '' THEN
        RAISE EXCEPTION 'capture envelope contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    command_tenant_id := (binding ->> 'tenant_id')::uuid;
    command_workspace_id := (binding ->> 'workspace_id')::uuid;
    command_access_scope_id := (binding ->> 'access_scope_id')::uuid;
    command_source_object_id := (binding ->> 'source_object_id')::uuid;
    command_event_id := (envelope ->> 'event_id')::uuid;
    IF pg_catalog.uuid_extract_version(command_event_id) <> 7 THEN
        RAISE EXCEPTION 'capture envelope event_id must be UUIDv7'
            USING ERRCODE = '22023';
    END IF;
    control_operation := envelope #>> '{parser_receipt,status}' = 'error'
        OR envelope ->> 'event_kind' = 'source_deleted';
    IF envelope #>> '{parser_receipt,status}' = 'complete'
       AND (
            jsonb_typeof(
                envelope #> '{parser_receipt,source_items_seen}'
            ) IS DISTINCT FROM 'number'
            OR jsonb_typeof(
                envelope #> '{parser_receipt,source_items_emitted}'
            ) IS DISTINCT FROM 'number'
            OR jsonb_typeof(
                envelope #> '{parser_receipt,source_items_quarantined}'
            ) IS DISTINCT FROM 'number'
            OR jsonb_typeof(
                envelope #> '{parser_receipt,known_omissions}'
            ) IS DISTINCT FROM 'array'
            OR (envelope #>> '{parser_receipt,source_items_seen}')::bigint
                NOT BETWEEN 0 AND 1000000
            OR (envelope #>> '{parser_receipt,source_items_emitted}')::bigint
                NOT BETWEEN 0 AND 1000000
            OR (envelope #>> '{parser_receipt,source_items_quarantined}')::bigint
                NOT BETWEEN 0 AND 1000000
            OR (envelope #>> '{parser_receipt,source_items_emitted}')::bigint
                IS DISTINCT FROM
                (envelope #>> '{parser_receipt,source_items_seen}')::bigint
            OR (envelope #>> '{parser_receipt,source_items_quarantined}')::bigint
                IS DISTINCT FROM 0
            OR jsonb_array_length(
                envelope #> '{parser_receipt,known_omissions}'
            ) IS DISTINCT FROM 0
       ) THEN
        RAISE EXCEPTION 'complete parser coverage is not fully accounted'
            USING ERRCODE = '22023';
    END IF;
    IF envelope ->> 'envelope_kind' = 'conversation_event' THEN
        IF jsonb_typeof(envelope -> 'participants') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'conversation participants are invalid'
                USING ERRCODE = '22023';
        END IF;
        SELECT count(*)::integer,
               min(unit.value #>> '{detail,participant_id}')
          INTO observation_count, envelope_actor_id
          FROM jsonb_array_elements(envelope -> 'units') AS unit(value)
         WHERE COALESCE((unit.value ->> 'is_observation')::boolean, false);
        IF observation_count <> 1 OR envelope_actor_id IS NULL THEN
            RAISE EXCEPTION 'conversation requires one attributed observation'
                USING ERRCODE = '22023';
        END IF;
        SELECT count(*)::integer, min(participant.value ->> 'kind')
          INTO participant_match_count, envelope_actor_kind
          FROM jsonb_array_elements(envelope -> 'participants') AS participant(value)
         WHERE participant.value ->> 'participant_id' = envelope_actor_id;
        IF participant_match_count <> 1 OR envelope_actor_kind IS NULL THEN
            RAISE EXCEPTION 'conversation observation participant is invalid'
                USING ERRCODE = '22023';
        END IF;
    END IF;

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
        RAISE EXCEPTION 'capture envelope is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT source.* INTO source_record
    FROM memoriesql.source_objects AS source
    WHERE source.tenant_id = command_tenant_id
      AND source.workspace_id = command_workspace_id
      AND source.access_scope_id = command_access_scope_id
      AND source.source_object_id = command_source_object_id
      AND source.source_system = envelope #>> '{source_identity,source_product}'
      AND source.installation_id IS NOT DISTINCT FROM
          NULLIF(envelope #>> '{source_identity,installation_id}', '')
      AND source.object_kind = envelope #>> '{source_identity,source_object_kind}'
      AND source.external_object_id =
          envelope #>> '{source_identity,source_object_stable_id}';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'source object is unavailable' USING ERRCODE = '42501';
    END IF;

    IF envelope ->> 'event_kind'
            IN ('source_amended', 'turn_amended', 'source_deleted') THEN
        SELECT event_record.event_id INTO linked_event_id
        FROM memoriesql.source_events AS event_record
        WHERE event_record.tenant_id = command_tenant_id
          AND event_record.workspace_id = command_workspace_id
          AND event_record.access_scope_id = command_access_scope_id
          AND event_record.source_object_id = command_source_object_id
          AND event_record.source_identity_key =
              envelope ->> 'supersedes_source_identity_key';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'source_identity_conflict' USING ERRCODE = '23505';
        END IF;
    END IF;

    IF control_operation THEN
        IF requested_command -> 'canonical_accept_command' <> 'null'::jsonb
           OR requested_command -> 'cursor' <> 'null'::jsonb
           OR jsonb_array_length(envelope -> 'units') <> 0 THEN
            RAISE EXCEPTION 'capture control receipt cannot accept evidence or cursor'
                USING ERRCODE = '22023';
        END IF;
        computed_request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(
            requested_command::text, 'UTF8'
        )), 'hex');
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            command_tenant_id::text || ':capture_envelope.control:'
                || (requested_command ->> 'idempotency_key'),
            0
        ));
        database_now := pg_catalog.clock_timestamp();
        IF context_record.expires_at <= database_now
           OR NOT memoriesql.current_context_source_authorized(
                command_access_scope_id, command_source_object_id,
                'memory.capture', 'write'
           ) THEN
            RAISE EXCEPTION 'capture envelope is outside authorization'
                USING ERRCODE = '42501';
        END IF;
        SELECT * INTO receipt_record
        FROM memoriesql.idempotency_receipts AS receipt
        WHERE receipt.tenant_id = command_tenant_id
          AND receipt.operation_kind = 'capture_envelope.control'
          AND receipt.idempotency_key = requested_command ->> 'idempotency_key'
        FOR UPDATE;
        IF FOUND THEN
            IF receipt_record.request_hash <> computed_request_hash THEN
                RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
            END IF;
            SELECT * INTO delivery_record
            FROM memoriesql.capture_deliveries AS delivery
            WHERE delivery.tenant_id = command_tenant_id
              AND delivery.idempotency_receipt_id =
                  receipt_record.idempotency_receipt_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'capture envelope receipt is incomplete'
                    USING ERRCODE = '55000';
            END IF;
            RETURN memoriesql.capture_submission_receipt(
                delivery_record.delivery_id, true
            );
        END IF;

        IF envelope ->> 'event_kind' = 'source_deleted' THEN
            final_status := 'deleted';
        ELSE
            final_status := 'quarantined';
        END IF;

        delivery_id := pg_catalog.uuidv7();
        INSERT INTO memoriesql.idempotency_receipts (
            tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
            operation_kind, idempotency_key, request_hash, status,
            resource_kind, resource_id, attempt_count, created_at, updated_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            delivery_id, 'capture_envelope.control',
            requested_command ->> 'idempotency_key', computed_request_hash,
            'in_progress', 'source', command_source_object_id, 1,
            database_now, database_now
        );
        INSERT INTO memoriesql.capture_deliveries (
            tenant_id, workspace_id, access_scope_id, delivery_id,
            source_object_id, idempotency_receipt_id, envelope_event_id,
            event_id, event_kind, supersedes_event_id,
            supersedes_source_identity_key, semantic_task_id, envelope_kind,
            envelope_schema_version, envelope_hash, request_hash,
            source_identity, conversation_identity, participant_identities,
            client_identity, project_hint, capture_surface, source_locator,
            parser_receipt, policy_references, final_status,
            checkpoint_sequence, authority_principal_id, delivered_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            delivery_id, command_source_object_id, delivery_id, command_event_id,
            linked_event_id, envelope ->> 'event_kind', linked_event_id,
            NULLIF(envelope ->> 'supersedes_source_identity_key', ''),
            NULL, envelope ->> 'envelope_kind',
            (envelope ->> 'schema_version')::integer,
            requested_command ->> 'envelope_hash', computed_request_hash,
            envelope -> 'source_identity',
            NULLIF(envelope -> 'conversation', 'null'::jsonb),
            COALESCE(envelope -> 'participants', '[]'::jsonb),
            NULLIF(envelope -> 'client', 'null'::jsonb),
            NULLIF(envelope -> 'project_hint', 'null'::jsonb),
            envelope ->> 'capture_surface',
            NULLIF(envelope -> 'source_locator', 'null'::jsonb),
            envelope -> 'parser_receipt', envelope -> 'policies', final_status,
            NULL, context_record.principal_id, database_now
        );
        INSERT INTO memoriesql.capture_delivery_status_events (
            tenant_id, workspace_id, access_scope_id, status_event_id,
            delivery_id, source_object_id, status_sequence,
            ingress_status, occurred_at
        ) VALUES
            (command_tenant_id, command_workspace_id, command_access_scope_id,
             pg_catalog.uuidv7(), delivery_id, command_source_object_id,
             1, 'detected', database_now),
            (command_tenant_id, command_workspace_id, command_access_scope_id,
             pg_catalog.uuidv7(), delivery_id, command_source_object_id,
             2, 'received', database_now),
            (command_tenant_id, command_workspace_id, command_access_scope_id,
             pg_catalog.uuidv7(), delivery_id, command_source_object_id,
             3, final_status, database_now);
        INSERT INTO memoriesql.outbox_events (
            tenant_id, workspace_id, access_scope_id, outbox_event_id,
            idempotency_receipt_id, aggregate_kind, aggregate_id, event_kind,
            payload, headers, recorded_at, available_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            pg_catalog.uuidv7(), delivery_id, 'capture_delivery', delivery_id,
            'capture_delivery.' || final_status,
            jsonb_build_object(
                'delivery_id', delivery_id, 'source_object_id', command_source_object_id
            ),
            jsonb_build_object('contract_version', 1), database_now, database_now
        );
        response := memoriesql.capture_submission_receipt(delivery_id, false);
        UPDATE memoriesql.idempotency_receipts AS receipt
           SET status = 'succeeded', response_receipt = response,
               updated_at = database_now, completed_at = database_now
         WHERE receipt.tenant_id = command_tenant_id
           AND receipt.idempotency_receipt_id = delivery_id;
        RETURN response;
    END IF;

    IF jsonb_typeof(requested_command -> 'canonical_accept_command')
            IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'cursor') NOT IN ('object', 'null')
       OR requested_command #>> '{canonical_accept_command,tenant_id}'
            <> binding ->> 'tenant_id'
       OR requested_command #>> '{canonical_accept_command,workspace_id}'
            <> binding ->> 'workspace_id'
       OR requested_command #>> '{canonical_accept_command,access_scope_id}'
            <> binding ->> 'access_scope_id'
       OR requested_command #>> '{canonical_accept_command,source_object_id}'
            <> binding ->> 'source_object_id'
       OR requested_command
              #>> '{canonical_accept_command,expected_source_object_schema_version}'
            IS DISTINCT FROM binding ->> 'expected_source_object_schema_version'
       OR requested_command #>> '{canonical_accept_command,idempotency_key}'
            IS DISTINCT FROM requested_command ->> 'idempotency_key'
       OR requested_command #>> '{canonical_accept_command,event,event_id}'
            <> envelope ->> 'event_id'
       OR requested_command #>> '{canonical_accept_command,event,source_type}'
            IS DISTINCT FROM envelope ->> 'source_type'
       OR requested_command #>> '{canonical_accept_command,event,source_system}'
            IS DISTINCT FROM envelope #>> '{source_identity,source_product}'
       OR requested_command #>> '{canonical_accept_command,event,installation_id}'
            IS DISTINCT FROM envelope #>> '{source_identity,installation_id}'
       OR requested_command #>> '{canonical_accept_command,event,external_event_id}'
            IS DISTINCT FROM envelope ->> 'external_event_id'
       OR requested_command #>> '{canonical_accept_command,event,external_id_scope}'
            IS DISTINCT FROM envelope #>> '{source_identity,external_id_scope}'
       OR requested_command #>> '{canonical_accept_command,event,source_identity_key}'
            IS DISTINCT FROM envelope #>> '{source_identity,source_identity_key}'
       OR requested_command #>> '{canonical_accept_command,event,session_id}'
            IS DISTINCT FROM envelope #>> '{conversation,session_id}'
       OR requested_command #>> '{canonical_accept_command,event,actor_id}'
            IS DISTINCT FROM envelope_actor_id
       OR requested_command #>> '{canonical_accept_command,event,actor_kind}'
            IS DISTINCT FROM envelope_actor_kind
       OR requested_command #>> '{canonical_accept_command,event,source_occurred_at}'
            IS DISTINCT FROM envelope ->> 'source_occurred_at'
       OR requested_command
              #>> '{canonical_accept_command,event,source_occurred_end_at}'
            IS DISTINCT FROM envelope ->> 'source_occurred_end_at'
       OR requested_command #>> '{canonical_accept_command,event,source_occurred_at_raw}'
            IS DISTINCT FROM envelope ->> 'source_occurred_at_raw'
       OR requested_command #>> '{canonical_accept_command,event,source_timezone}'
            IS DISTINCT FROM envelope ->> 'source_timezone'
       OR requested_command #>> '{canonical_accept_command,event,source_time_precision}'
            IS DISTINCT FROM envelope ->> 'source_time_precision'
       OR requested_command #>> '{canonical_accept_command,event,source_sequence}'
            IS DISTINCT FROM envelope ->> 'source_sequence'
       OR requested_command #>> '{canonical_accept_command,event,source_revision_key}'
            IS DISTINCT FROM envelope ->> 'source_revision_key'
       OR requested_command #>> '{canonical_accept_command,event,parser_contract_version}'
            IS DISTINCT FROM (
                (envelope #>> '{parser_receipt,parser_id}') || '.'
                || (envelope #>> '{parser_receipt,parser_version}')
            )
       OR requested_command
              #>> '{canonical_accept_command,event,observation_unit_policy_version}'
            IS DISTINCT FROM envelope #>> '{policies,observation_unit_policy_version}'
       OR requested_command #>> '{canonical_accept_command,event,captured_at}'
            IS DISTINCT FROM envelope ->> 'captured_at'
       OR requested_command #>> '{canonical_accept_command,event,source_ref}'
            IS DISTINCT FROM (
                'source:' || (envelope #>> '{source_identity,source_product}')
                || ':' || (
                    envelope #>> '{source_identity,source_object_stable_id}'
                )
            )
       OR requested_command #>> '{canonical_accept_command,event,content_hash}'
            IS DISTINCT FROM envelope ->> 'content_hash'
       OR requested_command -> 'canonical_accept_command' -> 'units'
            IS DISTINCT FROM envelope -> 'units' THEN
        RAISE EXCEPTION 'capture envelope canonical binding is invalid'
            USING ERRCODE = '22023';
    END IF;
    IF requested_command -> 'cursor' <> 'null'::jsonb AND (
        (requested_command #>> '{cursor,next_sequence}')::bigint
            <> (requested_command #>> '{cursor,expected_sequence}')::bigint + 1
        OR requested_command #>> '{canonical_accept_command,checkpoint,checkpoint_key}'
            <> requested_command #>> '{cursor,checkpoint_key}'
        OR requested_command
              #>> '{canonical_accept_command,checkpoint,expected_sequence}'
            <> requested_command #>> '{cursor,expected_sequence}'
        OR requested_command #>> '{canonical_accept_command,checkpoint,next_sequence}'
            <> requested_command #>> '{cursor,next_sequence}'
        OR requested_command #>> '{canonical_accept_command,checkpoint,checkpoint_hash}'
            <> requested_command #>> '{cursor,checkpoint_hash}'
    ) THEN
        RAISE EXCEPTION 'capture cursor contract is invalid' USING ERRCODE = '22023';
    END IF;

    inner_command := (requested_command -> 'canonical_accept_command')
        || jsonb_build_object(
            'capture_envelope_hash', requested_command ->> 'envelope_hash'
        );
    SELECT * INTO acceptance
    FROM memoriesql.accept_source_event(inner_command, requested_at);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'canonical source acceptance returned no receipt'
            USING ERRCODE = '55000';
    END IF;
    SELECT receipt.request_hash INTO computed_request_hash
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.idempotency_receipt_id = acceptance.idempotency_receipt_id;

    SELECT * INTO delivery_record
    FROM memoriesql.capture_deliveries AS delivery
    WHERE delivery.tenant_id = command_tenant_id
      AND delivery.idempotency_receipt_id = acceptance.idempotency_receipt_id;
    IF FOUND THEN
        RETURN memoriesql.capture_submission_receipt(
            delivery_record.delivery_id, true
        );
    END IF;

    final_status := CASE
        WHEN acceptance.acceptance_status = 'already_exists' THEN 'duplicate'
        WHEN envelope ->> 'event_kind' IN ('source_amended', 'turn_amended')
            THEN 'amended'
        ELSE 'accepted'
    END;
    delivery_id := acceptance.idempotency_receipt_id;
    INSERT INTO memoriesql.capture_deliveries (
        tenant_id, workspace_id, access_scope_id, delivery_id,
        source_object_id, idempotency_receipt_id, envelope_event_id,
        event_id, event_kind, supersedes_event_id,
        supersedes_source_identity_key, semantic_task_id, envelope_kind,
        envelope_schema_version, envelope_hash, request_hash,
        source_identity, conversation_identity, participant_identities,
        client_identity, project_hint, capture_surface, source_locator,
        parser_receipt, policy_references, final_status,
        checkpoint_sequence, authority_principal_id, delivered_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        delivery_id, command_source_object_id, acceptance.idempotency_receipt_id,
        command_event_id, acceptance.event_id, envelope ->> 'event_kind',
        linked_event_id,
        NULLIF(envelope ->> 'supersedes_source_identity_key', ''),
        acceptance.semantic_task_id,
        envelope ->> 'envelope_kind', (envelope ->> 'schema_version')::integer,
        requested_command ->> 'envelope_hash', computed_request_hash,
        envelope -> 'source_identity',
        NULLIF(envelope -> 'conversation', 'null'::jsonb),
        COALESCE(envelope -> 'participants', '[]'::jsonb),
        NULLIF(envelope -> 'client', 'null'::jsonb),
        NULLIF(envelope -> 'project_hint', 'null'::jsonb),
        envelope ->> 'capture_surface',
        NULLIF(envelope -> 'source_locator', 'null'::jsonb),
        envelope -> 'parser_receipt', envelope -> 'policies', final_status,
        acceptance.checkpoint_sequence, context_record.principal_id, database_now
    );
    INSERT INTO memoriesql.capture_delivery_status_events (
        tenant_id, workspace_id, access_scope_id, status_event_id,
        delivery_id, source_object_id, status_sequence,
        ingress_status, occurred_at
    ) VALUES
        (command_tenant_id, command_workspace_id, command_access_scope_id,
         pg_catalog.uuidv7(), delivery_id, command_source_object_id,
         1, 'detected', database_now),
        (command_tenant_id, command_workspace_id, command_access_scope_id,
         pg_catalog.uuidv7(), delivery_id, command_source_object_id,
         2, 'received', database_now),
        (command_tenant_id, command_workspace_id, command_access_scope_id,
         pg_catalog.uuidv7(), delivery_id, command_source_object_id,
         3, final_status, database_now);
    RETURN memoriesql.capture_submission_receipt(delivery_id, acceptance.replayed);
END;
$$;

CREATE FUNCTION memoriesql.read_capture_inbox(
    requested_source_object_id uuid
)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT jsonb_build_object(
        'delivery_id', delivery.delivery_id,
        'event_id', delivery.event_id,
        'source_object_id', delivery.source_object_id,
        'envelope_kind', delivery.envelope_kind,
        'source_product', delivery.source_identity ->> 'source_product',
        'source_identity_key', delivery.source_identity ->> 'source_identity_key',
        'ingress_status', delivery.final_status,
        'materialization_status', CASE
            WHEN delivery.event_id IS NULL THEN 'none'
            WHEN EXISTS (
                SELECT 1 FROM memoriesql.beads AS bead
                WHERE bead.tenant_id = delivery.tenant_id
                  AND bead.event_id = delivery.event_id
            ) AND NOT EXISTS (
                SELECT 1
                FROM memoriesql.beads AS bead
                WHERE bead.tenant_id = delivery.tenant_id
                  AND bead.event_id = delivery.event_id
                  AND NOT EXISTS (
                      SELECT 1 FROM memoriesql.bead_versions AS version
                      WHERE version.tenant_id = bead.tenant_id
                        AND version.event_id = bead.event_id
                        AND version.bead_id = bead.bead_id
                  )
            ) THEN 'ready'
            ELSE 'thin_bead'
        END,
        'enrichment_status', CASE
            WHEN task.task_id IS NULL THEN 'not_applicable'
            WHEN task.status = 'succeeded' THEN 'ready'
            WHEN task.status IN (
                'cancelled', 'failed_terminal', 'dead_letter', 'superseded'
            ) THEN 'enrichment_failed'
            ELSE 'enrichment_pending'
        END,
        'checkpoint_sequence', delivery.checkpoint_sequence,
        'delivered_at', delivery.delivered_at
    )
    FROM memoriesql.current_authorization_context() AS context
    JOIN memoriesql.capture_deliveries AS delivery
      ON delivery.tenant_id = context.tenant_id
     AND delivery.workspace_id = context.workspace_id
     AND delivery.source_object_id = requested_source_object_id
    LEFT JOIN memoriesql.semantic_tasks AS task
      ON task.tenant_id = delivery.tenant_id
     AND task.task_id = delivery.semantic_task_id
    WHERE memoriesql.current_context_source_authorized(
        delivery.access_scope_id, delivery.source_object_id,
        'source.read', 'read'
    )
    ORDER BY delivery.delivered_at DESC, delivery.delivery_id DESC
$$;

CREATE FUNCTION memoriesql.read_capture_status_history(
    requested_delivery_id uuid
)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT jsonb_build_object(
        'delivery_id', status.delivery_id,
        'status_sequence', status.status_sequence,
        'ingress_status', status.ingress_status,
        'occurred_at', status.occurred_at
    )
    FROM memoriesql.current_authorization_context() AS context
    JOIN memoriesql.capture_delivery_status_events AS status
      ON status.tenant_id = context.tenant_id
     AND status.workspace_id = context.workspace_id
     AND status.delivery_id = requested_delivery_id
    WHERE memoriesql.current_context_source_authorized(
        status.access_scope_id, status.source_object_id,
        'source.read', 'read'
    )
    ORDER BY status.status_sequence
$$;

CREATE FUNCTION memoriesql.read_capture_delivery_detail(
    requested_delivery_id uuid
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT jsonb_build_object(
        'delivery_id', delivery.delivery_id,
        'event_kind', delivery.event_kind,
        'supersedes_event_id', delivery.supersedes_event_id,
        'source_locator', delivery.source_locator,
        'parser_receipt', delivery.parser_receipt,
        'source_identity', delivery.source_identity,
        'conversation', delivery.conversation_identity,
        'participants', delivery.participant_identities,
        'client', delivery.client_identity,
        'project_hint', delivery.project_hint,
        'policies', delivery.policy_references
    )
    FROM memoriesql.current_authorization_context() AS context
    JOIN memoriesql.capture_deliveries AS delivery
      ON delivery.tenant_id = context.tenant_id
     AND delivery.workspace_id = context.workspace_id
     AND delivery.delivery_id = requested_delivery_id
    WHERE memoriesql.current_context_source_authorized(
              delivery.access_scope_id, delivery.source_object_id,
              'source.read', 'read'
          )
      AND memoriesql.current_context_source_authorized(
              delivery.access_scope_id, delivery.source_object_id,
              'memory.inspect', 'read'
          )
$$;

CREATE POLICY capture_deliveries_diagnostic_read
ON memoriesql.capture_deliveries
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.read', 'read'
    )
    AND memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'memory.inspect', 'read'
    )
);

CREATE POLICY capture_delivery_status_events_read
ON memoriesql.capture_delivery_status_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.read', 'read'
    )
);

ALTER TABLE memoriesql.capture_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.capture_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.capture_delivery_status_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.capture_delivery_status_events FORCE ROW LEVEL SECURITY;

REVOKE ALL ON memoriesql.capture_deliveries,
    memoriesql.capture_delivery_status_events FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.capture_submission_receipt(uuid, boolean),
    memoriesql.submit_capture_envelope(jsonb, timestamp with time zone),
    memoriesql.read_capture_inbox(uuid),
    memoriesql.read_capture_status_history(uuid),
    memoriesql.read_capture_delivery_detail(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION memoriesql.submit_capture_envelope(
    jsonb, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.read_capture_inbox(uuid),
    memoriesql.read_capture_status_history(uuid),
    memoriesql.read_capture_delivery_detail(uuid)
TO memoriesql_application, memoriesql_worker;

COMMENT ON TABLE memoriesql.capture_deliveries IS
    'Immutable SQL-13 envelope receipt and sensitive parser/source detail layered over the canonical PR-02 acceptance receipt.';
COMMENT ON TABLE memoriesql.capture_delivery_status_events IS
    'Append-only detected, received, and terminal ingress status history for one capture delivery.';
COMMENT ON FUNCTION memoriesql.submit_capture_envelope(
    jsonb, timestamp with time zone
) IS
    'Validates a versioned capture envelope, delegates evidence acceptance and checkpoint CAS to accept_source_event, and atomically records its inbox receipt; parser errors and deletion controls create no bead and advance no cursor.';
COMMENT ON FUNCTION memoriesql.read_capture_inbox(uuid) IS
    'Returns authorized sanitized ingress, thin-bead, and enrichment state without source locator or parser diagnostic detail.';
COMMENT ON FUNCTION memoriesql.read_capture_status_history(uuid) IS
    'Returns authorized append-only detected, received, and terminal capture status history without sensitive detail.';
COMMENT ON FUNCTION memoriesql.read_capture_delivery_detail(uuid) IS
    'Returns source locator, parser diagnostics, and identity detail only when both source.read and memory.inspect authorize the source.';
