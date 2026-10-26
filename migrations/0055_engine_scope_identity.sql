-- Engine identity within its engine scope (Core Memory engine work package,
-- second slice).
--
-- Migration 0054 made a record key, chunk id or event id unique in its tenant.
-- A writer allowed into one engine scope could then take another scope's key
-- before that scope delivered it, and the scope's own record would be refused
-- from then on. The engine derives these ids from event keys that include the
-- scope, so one id in two scopes names two different things. Every engine
-- table is now keyed by its scope's source as well: a key, chunk or event in
-- one scope never meets one in another.
--
-- Stored records keep their exact bytes. engine_chunks, engine_event_completions
-- and engine_type_decisions gain source_object_id, copied from the engine record
-- or event each row already references; the new foreign keys prove every copy.
-- Each immutability trigger is dropped only for that copy and recreated as it
-- was, as 0019 did. Requests and answers keep their shapes, so
-- memoriesql.engine-intake.v1 keeps expected_schema_version 54. New receipts use
-- engine_record.accept.v2, keyed by the scope's source id and the record key.

ALTER TABLE memoriesql.engine_chunks ADD COLUMN source_object_id uuid;
ALTER TABLE memoriesql.engine_event_completions ADD COLUMN source_object_id uuid;
ALTER TABLE memoriesql.engine_type_decisions ADD COLUMN source_object_id uuid;

DROP TRIGGER engine_chunks_immutable ON memoriesql.engine_chunks;
UPDATE memoriesql.engine_chunks AS chunk
   SET source_object_id = held.source_object_id
  FROM memoriesql.engine_records AS held
 WHERE held.tenant_id = chunk.tenant_id
   AND held.record_key = chunk.record_key;
CREATE TRIGGER engine_chunks_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_chunks
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

DROP TRIGGER engine_event_completions_immutable ON memoriesql.engine_event_completions;
UPDATE memoriesql.engine_event_completions AS completion
   SET source_object_id = held.source_object_id
  FROM memoriesql.engine_events AS held
 WHERE held.tenant_id = completion.tenant_id
   AND held.event_id = completion.event_id;
CREATE TRIGGER engine_event_completions_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_event_completions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

DROP TRIGGER engine_type_decisions_immutable ON memoriesql.engine_type_decisions;
UPDATE memoriesql.engine_type_decisions AS decision
   SET source_object_id = held.source_object_id
  FROM memoriesql.engine_records AS held
 WHERE held.tenant_id = decision.tenant_id
   AND held.record_key = decision.record_key;
CREATE TRIGGER engine_type_decisions_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_type_decisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

ALTER TABLE memoriesql.engine_chunks ALTER COLUMN source_object_id SET NOT NULL;
ALTER TABLE memoriesql.engine_event_completions ALTER COLUMN source_object_id SET NOT NULL;
ALTER TABLE memoriesql.engine_type_decisions ALTER COLUMN source_object_id SET NOT NULL;

-- References into the tenant-wide keys go first, then the keys themselves.
DROP INDEX memoriesql.engine_type_decisions_one_final_uq;
ALTER TABLE memoriesql.engine_type_decisions
    DROP CONSTRAINT engine_type_decisions_tenant_id_chunk_id_fkey,
    DROP CONSTRAINT engine_type_decisions_tenant_id_record_key_fkey,
    DROP CONSTRAINT engine_type_decisions_pkey;
ALTER TABLE memoriesql.engine_event_completions
    DROP CONSTRAINT engine_event_completions_tenant_id_completed_by_record_key_fkey,
    DROP CONSTRAINT engine_event_completions_tenant_id_event_id_fkey,
    DROP CONSTRAINT engine_event_completions_pkey;
ALTER TABLE memoriesql.engine_chunks
    DROP CONSTRAINT engine_chunks_tenant_id_event_id_fkey,
    DROP CONSTRAINT engine_chunks_tenant_id_record_key_fkey,
    DROP CONSTRAINT engine_chunks_tenant_id_event_id_ordinal_key,
    DROP CONSTRAINT engine_chunks_pkey;
ALTER TABLE memoriesql.engine_records
    DROP CONSTRAINT engine_records_tenant_id_event_id_fkey,
    DROP CONSTRAINT engine_records_pkey;
ALTER TABLE memoriesql.engine_events DROP CONSTRAINT engine_events_pkey;

ALTER TABLE memoriesql.engine_events
    ADD CONSTRAINT engine_events_pkey
        PRIMARY KEY (tenant_id, source_object_id, event_id);
ALTER TABLE memoriesql.engine_records
    ADD CONSTRAINT engine_records_pkey
        PRIMARY KEY (tenant_id, source_object_id, record_key),
    ADD CONSTRAINT engine_records_event_fkey
        FOREIGN KEY (tenant_id, source_object_id, event_id)
        REFERENCES memoriesql.engine_events (tenant_id, source_object_id, event_id);
ALTER TABLE memoriesql.engine_chunks
    ADD CONSTRAINT engine_chunks_pkey
        PRIMARY KEY (tenant_id, source_object_id, chunk_id),
    ADD CONSTRAINT engine_chunks_ordinal_key
        UNIQUE (tenant_id, source_object_id, event_id, ordinal),
    ADD CONSTRAINT engine_chunks_event_fkey
        FOREIGN KEY (tenant_id, source_object_id, event_id)
        REFERENCES memoriesql.engine_events (tenant_id, source_object_id, event_id),
    ADD CONSTRAINT engine_chunks_record_fkey
        FOREIGN KEY (tenant_id, source_object_id, record_key)
        REFERENCES memoriesql.engine_records (tenant_id, source_object_id, record_key);
ALTER TABLE memoriesql.engine_event_completions
    ADD CONSTRAINT engine_event_completions_pkey
        PRIMARY KEY (tenant_id, source_object_id, event_id),
    ADD CONSTRAINT engine_event_completions_event_fkey
        FOREIGN KEY (tenant_id, source_object_id, event_id)
        REFERENCES memoriesql.engine_events (tenant_id, source_object_id, event_id),
    ADD CONSTRAINT engine_event_completions_record_fkey
        FOREIGN KEY (tenant_id, source_object_id, completed_by_record_key)
        REFERENCES memoriesql.engine_records (tenant_id, source_object_id, record_key);
ALTER TABLE memoriesql.engine_type_decisions
    ADD CONSTRAINT engine_type_decisions_pkey
        PRIMARY KEY (tenant_id, source_object_id, chunk_id, decision_ordinal),
    ADD CONSTRAINT engine_type_decisions_chunk_fkey
        FOREIGN KEY (tenant_id, source_object_id, chunk_id)
        REFERENCES memoriesql.engine_chunks (tenant_id, source_object_id, chunk_id),
    ADD CONSTRAINT engine_type_decisions_record_fkey
        FOREIGN KEY (tenant_id, source_object_id, record_key)
        REFERENCES memoriesql.engine_records (tenant_id, source_object_id, record_key);
CREATE UNIQUE INDEX engine_type_decisions_one_final_uq
    ON memoriesql.engine_type_decisions (tenant_id, source_object_id, chunk_id)
    WHERE status <> 'pending';

-- The intake functions keep their signatures and grants; each lookup and
-- insert now names the record's scope.

CREATE OR REPLACE FUNCTION memoriesql.guard_engine_type_upgrade()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.decision_ordinal = 1 AND NOT EXISTS (
        SELECT 1 FROM memoriesql.engine_type_decisions AS first
         WHERE first.tenant_id = NEW.tenant_id
           AND first.source_object_id = NEW.source_object_id
           AND first.chunk_id = NEW.chunk_id
           AND first.decision_ordinal = 0
           AND first.status = 'pending'
    ) THEN
        RAISE EXCEPTION 'only a pending engine chunk type can be decided later'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.engine_accept_chunk_v1(
    authority memoriesql.authorization_contexts,
    source memoriesql.source_objects,
    item jsonb,
    digest text
)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
<<chunk_intake>>
DECLARE
    event_info jsonb := item -> 'event';
    chunk jsonb := item #> '{memory,chunk}';
    fields record;
    event_id uuid;
    event_hash text;
    content_hash text;
    chunk_count bigint;
    chunk_id uuid;
    ordinal bigint;
    byte_start bigint;
    byte_end bigint;
    char_start bigint;
    char_end bigint;
    chunk_text text;
    chunk_hash text;
    existing_event memoriesql.engine_events%ROWTYPE;
    present integer;
    contiguous boolean;
    whole_hash text;
    receipt_id uuid := pg_catalog.uuidv7();
    recorded_at timestamptz := pg_catalog.clock_timestamp();
    completes boolean := false;
BEGIN
    IF jsonb_typeof(event_info) IS DISTINCT FROM 'object'
       OR jsonb_typeof(chunk) IS DISTINCT FROM 'object' THEN
        RETURN memoriesql.engine_rejected('a chunk record needs its event and memory.chunk');
    END IF;
    event_id := memoriesql.engine_json_uuid(event_info -> 'event_id');
    event_hash := memoriesql.engine_json_hex64(event_info -> 'event_hash');
    content_hash := memoriesql.engine_json_hex64(event_info -> 'content_hash');
    chunk_count := memoriesql.engine_json_int(event_info -> 'chunk_count', 1, 1048576);
    IF event_id IS NULL OR event_hash IS NULL OR content_hash IS NULL
       OR chunk_count IS NULL
       OR event_info #>> '{key,scope}' IS DISTINCT FROM source.external_object_id
       OR memoriesql.engine_json_text(event_info #> '{key,source_ns}', 4096) IS NULL
       OR memoriesql.engine_json_text(event_info #> '{key,external_id}', 4096) IS NULL THEN
        RETURN memoriesql.engine_rejected('the event needs event_id, its key, hashes and chunk_count');
    END IF;
    chunk_id := memoriesql.engine_json_uuid(chunk -> 'chunk_id');
    ordinal := memoriesql.engine_json_int(chunk -> 'ordinal', 0, chunk_count - 1);
    byte_start := memoriesql.engine_json_int(chunk -> 'byte_start', 0, 9007199254740991);
    byte_end := memoriesql.engine_json_int(chunk -> 'byte_end', 0, 9007199254740991);
    char_start := memoriesql.engine_json_int(chunk -> 'char_start', 0, 9007199254740991);
    char_end := memoriesql.engine_json_int(chunk -> 'char_end', 0, 9007199254740991);
    chunk_hash := memoriesql.engine_json_hex64(chunk -> 'content_hash');
    IF jsonb_typeof(chunk -> 'text') = 'string' THEN
        chunk_text := chunk ->> 'text';
    END IF;
    IF chunk_id IS NULL OR item ->> 'record_key' <> 'chunk:' || chunk_id::text
       OR ordinal IS NULL OR byte_start IS NULL OR byte_end IS NULL
       OR char_start IS NULL OR char_end IS NULL OR chunk_hash IS NULL
       OR chunk_text IS NULL OR chunk_text = '' THEN
        RETURN memoriesql.engine_rejected('the chunk needs chunk_id, ordinal, ranges, text and content_hash');
    END IF;
    IF encode(sha256(convert_to(chunk_text, 'UTF8')), 'hex') <> chunk_hash
       OR byte_end - byte_start <> octet_length(chunk_text)
       OR char_end - char_start <> char_length(chunk_text) THEN
        RETURN memoriesql.engine_rejected('the chunk text does not match its hash and ranges');
    END IF;
    SELECT * INTO fields FROM memoriesql.engine_decision_fields_v1(
        item #> '{memory,type}', item -> 'decision', item -> 'catalogs'
    );
    IF fields.problem IS NOT NULL THEN
        RETURN memoriesql.engine_rejected(fields.problem);
    END IF;

    -- The scope's writer lock, held since this record was authorized, orders
    -- the chunks of an event, so exactly one of them sees itself complete
    -- the event. An event id names an event within its engine scope only.
    SELECT * INTO existing_event
      FROM memoriesql.engine_events AS engine_event
     WHERE engine_event.tenant_id = authority.tenant_id
       AND engine_event.source_object_id = source.source_object_id
       AND engine_event.event_id = chunk_intake.event_id;
    IF FOUND AND existing_event.event_info <> event_info THEN
        RETURN memoriesql.engine_rejected('the event differs from the one its earlier chunks carried');
    END IF;
    IF EXISTS (
        SELECT 1 FROM memoriesql.engine_chunks AS held
         WHERE held.tenant_id = authority.tenant_id
           AND held.source_object_id = source.source_object_id
           AND held.event_id = chunk_intake.event_id
           AND held.ordinal = chunk_intake.ordinal
    ) THEN
        RETURN memoriesql.engine_rejected(format(
            'another chunk already holds ordinal %s of its event', ordinal
        ));
    END IF;
    SELECT count(*)::integer INTO present
      FROM memoriesql.engine_chunks AS held
     WHERE held.tenant_id = authority.tenant_id
       AND held.source_object_id = source.source_object_id
       AND held.event_id = chunk_intake.event_id;
    IF present + 1 = chunk_count THEN
        SELECT bool_and(
                   parts.ordinal = parts.position
                   AND parts.byte_start = COALESCE(parts.previous_byte_end, 0)
                   AND parts.char_start = COALESCE(parts.previous_char_end, 0)
               ),
               encode(sha256(convert_to(
                   string_agg(parts.content_text, '' ORDER BY parts.ordinal), 'UTF8'
               )), 'hex')
          INTO contiguous, whole_hash
          FROM (
              SELECT every_part.*,
                     row_number() OVER (ORDER BY every_part.ordinal) - 1 AS position,
                     lag(every_part.byte_end) OVER (ORDER BY every_part.ordinal) AS previous_byte_end,
                     lag(every_part.char_end) OVER (ORDER BY every_part.ordinal) AS previous_char_end
                FROM (
                    SELECT held.ordinal::bigint AS ordinal, held.byte_start, held.byte_end,
                           held.char_start, held.char_end, held.content_text
                      FROM memoriesql.engine_chunks AS held
                     WHERE held.tenant_id = authority.tenant_id
                       AND held.source_object_id = source.source_object_id
                       AND held.event_id = chunk_intake.event_id
                    UNION ALL
                    SELECT chunk_intake.ordinal, chunk_intake.byte_start,
                           chunk_intake.byte_end, chunk_intake.char_start,
                           chunk_intake.char_end, chunk_text
                ) AS every_part
          ) AS parts;
        IF NOT contiguous OR whole_hash <> content_hash THEN
            RETURN memoriesql.engine_rejected('the chunks of the event do not reproduce its content');
        END IF;
        completes := true;
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status, resource_kind,
        resource_id, response_receipt, attempt_count, lease_owner,
        lease_expires_at, created_at, updated_at, completed_at
    ) VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id, receipt_id,
        'engine_record.accept.v2',
        source.source_object_id::text || ':' || (item ->> 'record_key'),
        digest, 'in_progress',
        'engine_chunk', chunk_id, NULL, 1, NULL, NULL, recorded_at, recorded_at, NULL
    );
    IF existing_event.event_id IS NULL THEN
        INSERT INTO memoriesql.engine_events VALUES (
            authority.tenant_id, source.workspace_id, source.access_scope_id,
            source.source_object_id, event_id, event_info, event_hash, content_hash,
            chunk_count, recorded_at
        );
    END IF;
    INSERT INTO memoriesql.engine_records (
        tenant_id, workspace_id, access_scope_id, source_object_id, record_key,
        record_kind, chunk_id, event_id, record, record_sha256,
        idempotency_receipt_id, accepted_by_principal_id, accepted_at
    ) VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id,
        source.source_object_id, item ->> 'record_key', 'chunk', chunk_id, event_id,
        item, digest, receipt_id, authority.principal_id, recorded_at
    );
    INSERT INTO memoriesql.engine_chunks VALUES (
        authority.tenant_id, chunk_id, event_id, ordinal, byte_start, byte_end,
        char_start, char_end, chunk_text, chunk_hash, item ->> 'record_key',
        source.source_object_id
    );
    INSERT INTO memoriesql.engine_type_decisions VALUES (
        authority.tenant_id, chunk_id, 0, fields.status, fields.bead_type_id,
        fields.bead_type_revision_id, fields.reason, fields.method,
        fields.binding_ref, fields.binding_rev, fields.rule_profile_id,
        fields.rule_profile_rev, fields.rule_id, fields.decision_id,
        fields.decided_at, fields.type_catalog_id, fields.type_catalog_hash,
        item ->> 'record_key', recorded_at, source.source_object_id
    );
    IF completes THEN
        INSERT INTO memoriesql.engine_event_completions VALUES (
            authority.tenant_id, event_id, item ->> 'record_key', recorded_at,
            source.source_object_id
        );
    END IF;
    INSERT INTO memoriesql.outbox_events VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id,
        pg_catalog.uuidv7(), receipt_id, 'engine_chunk', chunk_id,
        'engine_chunk.accepted',
        jsonb_build_object('record_key', item ->> 'record_key', 'event_id', event_id,
                           'event_complete', completes,
                           'source_object_id', source.source_object_id),
        '{"contract_version":1}'::jsonb, recorded_at, NULL
    );
    UPDATE memoriesql.idempotency_receipts
       SET status = 'succeeded',
           response_receipt = jsonb_build_object(
               'outcome', 'accepted', 'record_key', item ->> 'record_key'
           ),
           updated_at = recorded_at,
           completed_at = recorded_at
     WHERE tenant_id = authority.tenant_id
       AND idempotency_receipt_id = receipt_id;
    RETURN jsonb_build_object('outcome', 'accepted');
END chunk_intake;
$$;

CREATE OR REPLACE FUNCTION memoriesql.engine_accept_type_decision_v1(
    authority memoriesql.authorization_contexts,
    source memoriesql.source_objects,
    item jsonb,
    digest text
)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
<<decision_intake>>
DECLARE
    chunk_id uuid := memoriesql.engine_json_uuid(item -> 'chunk_id');
    event_id uuid := memoriesql.engine_json_uuid(item -> 'event_id');
    held_event memoriesql.engine_events%ROWTYPE;
    fields record;
    first_status text;
    receipt_id uuid := pg_catalog.uuidv7();
    recorded_at timestamptz := pg_catalog.clock_timestamp();
BEGIN
    IF chunk_id IS NULL OR event_id IS NULL
       OR item ->> 'record_key' <> 'type:' || chunk_id::text THEN
        RETURN memoriesql.engine_rejected('a type decision needs chunk_id and event_id');
    END IF;
    SELECT engine_event.* INTO held_event
      FROM memoriesql.engine_chunks AS held
      JOIN memoriesql.engine_events AS engine_event
        ON engine_event.tenant_id = held.tenant_id
       AND engine_event.source_object_id = held.source_object_id
       AND engine_event.event_id = held.event_id
     WHERE held.tenant_id = authority.tenant_id
       AND held.source_object_id = source.source_object_id
       AND held.chunk_id = decision_intake.chunk_id;
    IF NOT FOUND THEN
        RETURN memoriesql.engine_rejected(
            'no accepted chunk has this chunk_id in this engine scope'
        );
    END IF;
    IF held_event.event_id <> decision_intake.event_id THEN
        RETURN memoriesql.engine_rejected('the type decision does not match its chunk');
    END IF;
    IF item #>> '{outcome,status}' = 'pending' THEN
        RETURN memoriesql.engine_rejected('a pending outcome is not a decision');
    END IF;
    SELECT * INTO fields FROM memoriesql.engine_decision_fields_v1(
        item -> 'outcome', item -> 'decision', item -> 'catalogs'
    );
    IF fields.problem IS NOT NULL THEN
        RETURN memoriesql.engine_rejected(fields.problem);
    END IF;
    SELECT first.status INTO first_status
      FROM memoriesql.engine_type_decisions AS first
     WHERE first.tenant_id = authority.tenant_id
       AND first.source_object_id = source.source_object_id
       AND first.chunk_id = decision_intake.chunk_id
       AND first.decision_ordinal = 0;
    IF first_status <> 'pending' OR EXISTS (
        SELECT 1 FROM memoriesql.engine_type_decisions AS later
         WHERE later.tenant_id = authority.tenant_id
           AND later.source_object_id = source.source_object_id
           AND later.chunk_id = decision_intake.chunk_id
           AND later.decision_ordinal = 1
    ) THEN
        RETURN memoriesql.engine_rejected('the chunk already has a final type');
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status, resource_kind,
        resource_id, response_receipt, attempt_count, lease_owner,
        lease_expires_at, created_at, updated_at, completed_at
    ) VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id, receipt_id,
        'engine_record.accept.v2',
        source.source_object_id::text || ':' || (item ->> 'record_key'),
        digest, 'in_progress',
        'engine_chunk', chunk_id, NULL, 1, NULL, NULL, recorded_at, recorded_at, NULL
    );
    INSERT INTO memoriesql.engine_records (
        tenant_id, workspace_id, access_scope_id, source_object_id, record_key,
        record_kind, chunk_id, event_id, record, record_sha256,
        idempotency_receipt_id, accepted_by_principal_id, accepted_at
    ) VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id,
        source.source_object_id, item ->> 'record_key', 'type_decision', chunk_id,
        event_id, item, digest, receipt_id, authority.principal_id, recorded_at
    );
    INSERT INTO memoriesql.engine_type_decisions VALUES (
        authority.tenant_id, chunk_id, 1, fields.status, fields.bead_type_id,
        fields.bead_type_revision_id, fields.reason, fields.method,
        fields.binding_ref, fields.binding_rev, fields.rule_profile_id,
        fields.rule_profile_rev, fields.rule_id, fields.decision_id,
        fields.decided_at, fields.type_catalog_id, fields.type_catalog_hash,
        item ->> 'record_key', recorded_at, source.source_object_id
    );
    INSERT INTO memoriesql.outbox_events VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id,
        pg_catalog.uuidv7(), receipt_id, 'engine_chunk', chunk_id,
        'engine_type.decided',
        jsonb_build_object('record_key', item ->> 'record_key', 'event_id', event_id,
                           'source_object_id', source.source_object_id),
        '{"contract_version":1}'::jsonb, recorded_at, NULL
    );
    UPDATE memoriesql.idempotency_receipts
       SET status = 'succeeded',
           response_receipt = jsonb_build_object(
               'outcome', 'accepted', 'record_key', item ->> 'record_key'
           ),
           updated_at = recorded_at,
           completed_at = recorded_at
     WHERE tenant_id = authority.tenant_id
       AND idempotency_receipt_id = receipt_id;
    RETURN jsonb_build_object('outcome', 'accepted');
END decision_intake;
$$;

CREATE OR REPLACE FUNCTION memoriesql.engine_accept_record_v1(
    authority memoriesql.authorization_contexts,
    item jsonb
)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
<<record_intake>>
DECLARE
    record_kind text;
    record_key text;
    engine_scope text;
    chunk_ref uuid;
    source memoriesql.source_objects%ROWTYPE;
    existing memoriesql.engine_records%ROWTYPE;
    digest text;
BEGIN
    IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN
        RETURN memoriesql.engine_rejected('the record is not a JSON object');
    END IF;
    IF item ->> 'contract' IS DISTINCT FROM 'core_memory.record.v1' THEN
        RETURN memoriesql.engine_rejected('only core_memory.record.v1 records are accepted');
    END IF;
    record_kind := item ->> 'record_kind';
    record_key := memoriesql.engine_json_text(item -> 'record_key', 128);
    engine_scope := memoriesql.engine_json_text(item -> 'scope', 4096);
    IF record_kind IS NULL OR record_kind NOT IN ('chunk', 'type_decision') THEN
        RETURN memoriesql.engine_rejected(format(
            'record kind %s is not accepted yet', COALESCE(record_kind, '(none)')
        ));
    END IF;
    chunk_ref := memoriesql.engine_json_uuid(CASE record_kind
        WHEN 'chunk' THEN item #> '{memory,chunk,chunk_id}'
        ELSE item -> 'chunk_id'
    END);
    IF record_key IS NULL OR engine_scope IS NULL OR chunk_ref IS NULL THEN
        RETURN memoriesql.engine_rejected('the record needs record_key, scope and its chunk_id');
    END IF;

    source := memoriesql.engine_scope_source_v1(authority, engine_scope);
    IF source.source_object_id IS NULL THEN
        RETURN memoriesql.engine_retryable(format(
            'engine scope %s is not enrolled in this workspace', engine_scope
        ));
    END IF;
    IF NOT memoriesql.current_context_source_authorized(
           source.access_scope_id, source.source_object_id,
           'engine.records.deliver', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           source.access_scope_id, 'write', pg_catalog.clock_timestamp()
       ) THEN
        RETURN memoriesql.engine_retryable(format(
            'this credential may not deliver to engine scope %s', engine_scope
        ));
    END IF;

    -- Already held when the batch locked its scopes; taken here as well for a
    -- scope that became deliverable after that. It orders every check and
    -- write below against the scope's other writers. Nothing else is locked,
    -- and every key and id below is looked up within this scope only.
    PERFORM memoriesql.engine_lock_scope_v1(authority, source.source_object_id);
    digest := encode(sha256(convert_to(item::text, 'UTF8')), 'hex');
    SELECT held.* INTO existing
      FROM memoriesql.engine_records AS held
     WHERE held.tenant_id = authority.tenant_id
       AND held.source_object_id = source.source_object_id
       AND held.record_key = record_intake.record_key;
    IF FOUND THEN
        IF existing.record_sha256 = digest THEN
            RETURN jsonb_build_object('outcome', 'already_accepted');
        END IF;
        -- Only a caller who may read the scope is shown the stored record.
        IF memoriesql.current_context_source_authorized(
               source.access_scope_id, source.source_object_id, 'source.read', 'read'
           )
           AND memoriesql.current_context_scope_time_authorized(
               source.access_scope_id, 'read', pg_catalog.clock_timestamp()
           ) THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'stored', existing.record);
        END IF;
        RETURN jsonb_build_object('outcome', 'conflict');
    END IF;

    IF record_kind = 'chunk' THEN
        RETURN memoriesql.engine_accept_chunk_v1(authority, source, item, digest);
    END IF;
    RETURN memoriesql.engine_accept_type_decision_v1(authority, source, item, digest);
END record_intake;
$$;
