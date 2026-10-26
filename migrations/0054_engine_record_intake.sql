-- Core Memory engine record intake (engine work package, first slice).
--
-- The Core Memory engine delivers versioned records: one per deterministic
-- chunk of a source event, and later one type decision for a chunk it first
-- delivered as pending. This intake keeps them exactly as delivered, under
-- shared-ledger receipts, and authors nothing: no statements, renders, runs,
-- rationale or confidence are created, and no semantic task is queued.
--
-- Engine chunks are not mapped onto PR-01 source units. A transcript
-- observation unit must be a whole turn there, while an engine chunk may
-- split a turn; inventing turn boundaries is refused. The records keep their
-- own tables, verified against their own hashes.
--
-- Each engine scope is one enrolled exact source (source_system
-- 'core-memory', object_kind 'engine_scope', no installation), so its records
-- share that source's explicit access scope, grants and revocation.

INSERT INTO memoriesql.capabilities (capability_key, description)
VALUES (
    'engine.records.deliver',
    'Deliver Core Memory engine records into an enrolled engine scope.'
);

INSERT INTO memoriesql.role_capabilities (role_key, capability_key)
VALUES
    ('personal_owner', 'engine.records.deliver'),
    ('background_service', 'engine.records.deliver');

CREATE TABLE memoriesql.engine_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    event_id uuid NOT NULL,
    event_info jsonb NOT NULL,
    event_hash text NOT NULL,
    content_hash text NOT NULL,
    chunk_count integer NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, source_object_id)
        REFERENCES memoriesql.source_objects (tenant_id, source_object_id),
    CHECK (jsonb_typeof(event_info) = 'object'),
    CHECK (event_hash ~ '^[0-9a-f]{64}$' AND content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (chunk_count BETWEEN 1 AND 1048576)
);

CREATE TABLE memoriesql.engine_records (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    record_seq bigint GENERATED ALWAYS AS IDENTITY,
    record_key text NOT NULL,
    record_kind text NOT NULL,
    chunk_id uuid NOT NULL,
    event_id uuid NOT NULL,
    record jsonb NOT NULL,
    record_sha256 text NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    accepted_by_principal_id uuid NOT NULL,
    accepted_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, record_key),
    UNIQUE (record_seq),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, idempotency_receipt_id)
        REFERENCES memoriesql.idempotency_receipts (
            tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
        ),
    FOREIGN KEY (tenant_id, event_id)
        REFERENCES memoriesql.engine_events (tenant_id, event_id),
    FOREIGN KEY (tenant_id, accepted_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CHECK (record_kind IN ('chunk', 'type_decision')),
    CHECK (record_key = CASE record_kind WHEN 'chunk' THEN 'chunk:' ELSE 'type:' END
                        || chunk_id::text),
    CHECK (jsonb_typeof(record) = 'object' AND record ->> 'record_key' = record_key),
    CHECK (record_sha256 = encode(sha256(convert_to(record::text, 'UTF8')), 'hex'))
);

CREATE INDEX engine_records_source_order_idx
    ON memoriesql.engine_records (tenant_id, source_object_id, record_seq);

CREATE TABLE memoriesql.engine_chunks (
    tenant_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    event_id uuid NOT NULL,
    ordinal integer NOT NULL,
    byte_start bigint NOT NULL,
    byte_end bigint NOT NULL,
    char_start bigint NOT NULL,
    char_end bigint NOT NULL,
    content_text text NOT NULL,
    content_hash text NOT NULL,
    record_key text NOT NULL,
    PRIMARY KEY (tenant_id, chunk_id),
    UNIQUE (tenant_id, event_id, ordinal),
    FOREIGN KEY (tenant_id, event_id)
        REFERENCES memoriesql.engine_events (tenant_id, event_id),
    FOREIGN KEY (tenant_id, record_key)
        REFERENCES memoriesql.engine_records (tenant_id, record_key),
    CHECK (ordinal >= 0 AND byte_start >= 0 AND char_start >= 0),
    CHECK (byte_end - byte_start = octet_length(content_text)),
    CHECK (char_end - char_start = char_length(content_text)),
    CHECK (content_text <> ''),
    CHECK (content_hash = encode(sha256(convert_to(content_text, 'UTF8')), 'hex'))
);

-- Inserted when the last chunk of an event arrives and the chunks, in order,
-- are contiguous and reproduce the event's content hash.
CREATE TABLE memoriesql.engine_event_completions (
    tenant_id uuid NOT NULL,
    event_id uuid NOT NULL,
    completed_by_record_key text NOT NULL,
    completed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, event_id)
        REFERENCES memoriesql.engine_events (tenant_id, event_id),
    FOREIGN KEY (tenant_id, completed_by_record_key)
        REFERENCES memoriesql.engine_records (tenant_id, record_key)
);

-- Ordinal 0 is the outcome the chunk record carried. Ordinal 1 is the one
-- later type decision allowed for a chunk delivered as pending. A final
-- outcome is never replaced.
CREATE TABLE memoriesql.engine_type_decisions (
    tenant_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    decision_ordinal smallint NOT NULL,
    status text NOT NULL,
    bead_type_id uuid,
    bead_type_revision_id uuid,
    reason text,
    method text,
    binding_ref text,
    binding_rev text,
    rule_profile_id text,
    rule_profile_rev integer,
    rule_id text,
    decision_id uuid,
    decided_at timestamptz,
    type_catalog_id text,
    type_catalog_hash text,
    record_key text NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, chunk_id, decision_ordinal),
    FOREIGN KEY (tenant_id, chunk_id)
        REFERENCES memoriesql.engine_chunks (tenant_id, chunk_id),
    FOREIGN KEY (tenant_id, record_key)
        REFERENCES memoriesql.engine_records (tenant_id, record_key),
    FOREIGN KEY (bead_type_id, bead_type_revision_id)
        REFERENCES memoriesql.bead_type_revisions (bead_type_id, bead_type_revision_id),
    CHECK (decision_ordinal IN (0, 1)),
    CHECK (status IN ('assigned', 'unassigned', 'pending')),
    CHECK (decision_ordinal = 0 OR status <> 'pending'),
    CHECK ((status = 'assigned') = (bead_type_revision_id IS NOT NULL)),
    CHECK ((bead_type_id IS NULL) = (bead_type_revision_id IS NULL)),
    CHECK (
        (status = 'assigned' AND reason IS NULL)
        OR (status = 'unassigned'
            AND reason IN ('no_fit', 'insufficient_evidence', 'ambiguous'))
        OR (status = 'pending' AND reason IN (
            'timeout', 'provider_error', 'capacity', 'unsupported_input',
            'no_classifier'
        ))
    ),
    CHECK ((status = 'pending') = (decision_id IS NULL)),
    CHECK ((decision_id IS NULL) = (decided_at IS NULL)),
    CHECK ((decision_id IS NULL) = (method IS NULL)),
    CHECK (
        method IS NULL
        OR (method = 'model' AND binding_ref IS NOT NULL
            AND binding_rev ~ '^[0-9a-f]{64}$'
            AND rule_profile_id IS NULL AND rule_profile_rev IS NULL
            AND rule_id IS NULL)
        OR (method = 'rule' AND binding_ref IS NULL AND binding_rev IS NULL
            AND rule_profile_id IS NOT NULL AND rule_profile_rev > 0
            AND rule_id IS NOT NULL)
    ),
    CHECK (status <> 'assigned' OR type_catalog_id = 'memoriesql.bead_types'),
    CHECK (type_catalog_hash IS NULL OR type_catalog_hash ~ '^[0-9a-f]{64}$')
);

CREATE UNIQUE INDEX engine_type_decisions_one_final_uq
    ON memoriesql.engine_type_decisions (tenant_id, chunk_id)
    WHERE status <> 'pending';

CREATE FUNCTION memoriesql.guard_engine_type_upgrade()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.decision_ordinal = 1 AND NOT EXISTS (
        SELECT 1 FROM memoriesql.engine_type_decisions AS first
         WHERE first.tenant_id = NEW.tenant_id
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

CREATE TRIGGER engine_type_decisions_upgrade_guard
BEFORE INSERT ON memoriesql.engine_type_decisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_engine_type_upgrade();

CREATE TRIGGER engine_events_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER engine_records_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_records
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER engine_chunks_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_chunks
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER engine_event_completions_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_event_completions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER engine_type_decisions_immutable BEFORE UPDATE OR DELETE ON memoriesql.engine_type_decisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Definer functions own every read and write; roles receive no table grants.
ALTER TABLE memoriesql.engine_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_records FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_event_completions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_event_completions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_type_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_type_decisions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.engine_events, memoriesql.engine_records,
    memoriesql.engine_chunks, memoriesql.engine_event_completions,
    memoriesql.engine_type_decisions FROM PUBLIC;

-- Strict readers for delivered JSON. Each returns NULL for anything but a
-- well-formed value, so callers report one precise rejection.
CREATE FUNCTION memoriesql.engine_json_uuid(value jsonb)
RETURNS uuid
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) = 'string'
         AND value #>> '{}' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        THEN (value #>> '{}')::uuid
    END
$$;

CREATE FUNCTION memoriesql.engine_json_hex64(value jsonb)
RETURNS text
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) = 'string' AND value #>> '{}' ~ '^[0-9a-f]{64}$'
        THEN value #>> '{}'
    END
$$;

CREATE FUNCTION memoriesql.engine_json_text(value jsonb, max_chars integer)
RETURNS text
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) = 'string'
         AND btrim(value #>> '{}') <> ''
         AND char_length(value #>> '{}') <= max_chars
        THEN value #>> '{}'
    END
$$;

CREATE FUNCTION memoriesql.engine_json_int(value jsonb, low bigint, high bigint)
RETURNS bigint
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
    -- Nested so the cast runs only on numbers.
    SELECT CASE WHEN jsonb_typeof(value) = 'number' THEN
        CASE
            WHEN (value #>> '{}')::numeric = trunc((value #>> '{}')::numeric)
             AND (value #>> '{}')::numeric BETWEEN low AND high
            THEN (value #>> '{}')::numeric::bigint
        END
    END
$$;

CREATE FUNCTION memoriesql.engine_json_time(value jsonb)
RETURNS timestamptz
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    IF jsonb_typeof(value) IS DISTINCT FROM 'string'
       OR value #>> '{}' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$' THEN
        RETURN NULL;
    END IF;
    RETURN (value #>> '{}')::timestamptz;
EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN
    RETURN NULL;
END;
$$;

CREATE FUNCTION memoriesql.engine_rejected(reason text)
RETURNS jsonb
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT jsonb_build_object('outcome', 'rejected', 'reason', reason)
$$;

-- The engine retries a record answered this way, and every record after it
-- in the batch, after the delay. Used when the cause is configuration that
-- an operator can fix, so no record is lost to it.
CREATE FUNCTION memoriesql.engine_retryable(reason text)
RETURNS jsonb
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT jsonb_build_object(
        'outcome', 'retryable', 'retry_after_ms', 60000, 'reason', reason
    )
$$;

-- Reads a type outcome with its decision provenance. `problem` is set, and
-- nothing else is, when the outcome or its provenance is malformed or names
-- a type that is not a current governed bead type.
CREATE FUNCTION memoriesql.engine_decision_fields_v1(
    outcome jsonb,
    decision jsonb,
    catalogs jsonb,
    OUT problem text,
    OUT status text,
    OUT bead_type_id uuid,
    OUT bead_type_revision_id uuid,
    OUT reason text,
    OUT method text,
    OUT binding_ref text,
    OUT binding_rev text,
    OUT rule_profile_id text,
    OUT rule_profile_rev integer,
    OUT rule_id text,
    OUT decision_id uuid,
    OUT decided_at timestamptz,
    OUT type_catalog_id text,
    OUT type_catalog_hash text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    type_key text;
    type_revision bigint;
BEGIN
    IF jsonb_typeof(outcome) IS DISTINCT FROM 'object' THEN
        problem := 'the type outcome is not an object';
        RETURN;
    END IF;
    status := outcome ->> 'status';
    IF status = 'assigned' THEN
        type_key := memoriesql.engine_json_text(outcome #> '{type_ref,key}', 128);
        type_revision := memoriesql.engine_json_int(outcome #> '{type_ref,rev}', 1, 2147483647);
        SELECT governed.bead_type_id, governed.bead_type_revision_id
          INTO bead_type_id, bead_type_revision_id
          FROM memoriesql.current_bead_type_revisions AS governed
         WHERE governed.stable_key = type_key
           AND governed.revision = type_revision;
        IF bead_type_revision_id IS NULL THEN
            problem := format(
                'type %s revision %s is not a current memoriesQL bead type',
                COALESCE(type_key, '?'), COALESCE(type_revision::text, '?')
            );
            RETURN;
        END IF;
        type_catalog_id := catalogs #>> '{type,id}';
        IF type_catalog_id IS DISTINCT FROM 'memoriesql.bead_types' THEN
            problem := format(
                'type catalog %s is not memoriesql.bead_types',
                COALESCE(type_catalog_id, '(none)')
            );
            RETURN;
        END IF;
    ELSIF status = 'unassigned' THEN
        reason := outcome ->> 'reason';
        IF reason IS NULL OR reason NOT IN ('no_fit', 'insufficient_evidence', 'ambiguous') THEN
            problem := 'unassigned outcomes need a no_fit, insufficient_evidence or ambiguous reason';
            RETURN;
        END IF;
    ELSIF status = 'pending' THEN
        reason := outcome ->> 'reason';
        IF reason IS NULL OR reason NOT IN (
            'timeout', 'provider_error', 'capacity', 'unsupported_input', 'no_classifier'
        ) THEN
            problem := 'pending outcomes need a known retryable reason';
            RETURN;
        END IF;
    ELSE
        problem := format('unknown type status %s', COALESCE(status, '(none)'));
        RETURN;
    END IF;

    IF catalogs IS NOT NULL AND catalogs ? 'type' THEN
        type_catalog_id := catalogs #>> '{type,id}';
        type_catalog_hash := memoriesql.engine_json_hex64(catalogs #> '{type,hash}');
        IF type_catalog_hash IS NULL THEN
            problem := 'the type catalog needs a SHA-256 hash';
            RETURN;
        END IF;
    END IF;

    IF status = 'pending' THEN
        IF decision IS NOT NULL AND jsonb_typeof(decision) <> 'null' THEN
            problem := 'a pending outcome carries no decision';
        END IF;
        RETURN;
    END IF;
    IF jsonb_typeof(decision) IS DISTINCT FROM 'object' THEN
        problem := 'a final outcome needs its decision provenance';
        RETURN;
    END IF;
    decision_id := memoriesql.engine_json_uuid(decision -> 'decision_id');
    decided_at := memoriesql.engine_json_time(decision -> 'decided_at');
    method := decision ->> 'method';
    IF decision_id IS NULL OR decided_at IS NULL THEN
        problem := 'the decision needs a decision_id and decided_at';
        RETURN;
    END IF;
    IF method = 'model' THEN
        binding_ref := memoriesql.engine_json_text(decision -> 'binding_ref', 256);
        binding_rev := memoriesql.engine_json_hex64(decision -> 'binding_rev');
        IF binding_ref IS NULL OR binding_rev IS NULL THEN
            problem := 'a model decision needs binding_ref and a SHA-256 binding_rev';
        END IF;
    ELSIF method = 'rule' THEN
        rule_profile_id := memoriesql.engine_json_text(decision #> '{profile,id}', 256);
        rule_profile_rev := memoriesql.engine_json_int(decision #> '{profile,rev}', 1, 2147483647);
        rule_id := memoriesql.engine_json_text(decision -> 'rule_id', 256);
        IF rule_profile_id IS NULL OR rule_profile_rev IS NULL OR rule_id IS NULL THEN
            problem := 'a rule decision needs its profile id, profile rev and rule_id';
        END IF;
    ELSE
        problem := format('unknown decision method %s', COALESCE(method, '(none)'));
    END IF;
END;
$$;

-- The enrolled source standing for `engine_scope` in the caller's workspace.
CREATE FUNCTION memoriesql.engine_scope_source_v1(
    authority memoriesql.authorization_contexts,
    engine_scope text
)
RETURNS memoriesql.source_objects
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT source.*
      FROM memoriesql.source_objects AS source
      JOIN memoriesql.protected_resources AS resource
        ON resource.tenant_id = source.tenant_id
       AND resource.workspace_id = source.workspace_id
       AND resource.access_scope_id = source.access_scope_id
       AND resource.resource_kind = 'source'
       AND resource.resource_id = source.source_object_id
       AND resource.status = 'active'
      JOIN memoriesql.access_scopes AS scope
        ON scope.tenant_id = source.tenant_id
       AND scope.access_scope_id = source.access_scope_id
       AND scope.status = 'active'
     WHERE source.tenant_id = authority.tenant_id
       AND source.workspace_id = authority.workspace_id
       AND source.source_system = 'core-memory'
       AND source.installation_key = ''
       AND source.object_kind = 'engine_scope'
       AND source.external_object_id = engine_scope
$$;

CREATE FUNCTION memoriesql.engine_accept_chunk_v1(
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

    -- One event lock orders the chunks of an event, so exactly one of them
    -- sees itself complete the event.
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        authority.tenant_id::text || ':engine_event:' || event_id::text, 0
    ));
    SELECT * INTO existing_event
      FROM memoriesql.engine_events AS engine_event
     WHERE engine_event.tenant_id = authority.tenant_id
       AND engine_event.event_id = chunk_intake.event_id;
    IF FOUND AND (existing_event.source_object_id <> source.source_object_id
                  OR existing_event.event_info <> event_info) THEN
        RETURN memoriesql.engine_rejected('the event differs from the one its earlier chunks carried');
    END IF;
    IF EXISTS (
        SELECT 1 FROM memoriesql.engine_chunks AS held
         WHERE held.tenant_id = authority.tenant_id
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
        'engine_record.accept.v1', item ->> 'record_key', digest, 'in_progress',
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
        char_start, char_end, chunk_text, chunk_hash, item ->> 'record_key'
    );
    INSERT INTO memoriesql.engine_type_decisions VALUES (
        authority.tenant_id, chunk_id, 0, fields.status, fields.bead_type_id,
        fields.bead_type_revision_id, fields.reason, fields.method,
        fields.binding_ref, fields.binding_rev, fields.rule_profile_id,
        fields.rule_profile_rev, fields.rule_id, fields.decision_id,
        fields.decided_at, fields.type_catalog_id, fields.type_catalog_hash,
        item ->> 'record_key', recorded_at
    );
    IF completes THEN
        INSERT INTO memoriesql.engine_event_completions VALUES (
            authority.tenant_id, event_id, item ->> 'record_key', recorded_at
        );
    END IF;
    INSERT INTO memoriesql.outbox_events VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id,
        pg_catalog.uuidv7(), receipt_id, 'engine_chunk', chunk_id,
        'engine_chunk.accepted',
        jsonb_build_object('record_key', item ->> 'record_key', 'event_id', event_id,
                           'event_complete', completes),
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

CREATE FUNCTION memoriesql.engine_accept_type_decision_v1(
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
       AND engine_event.event_id = held.event_id
     WHERE held.tenant_id = authority.tenant_id
       AND held.chunk_id = decision_intake.chunk_id;
    IF NOT FOUND THEN
        RETURN memoriesql.engine_rejected('no accepted chunk has this chunk_id');
    END IF;
    IF held_event.source_object_id <> source.source_object_id
       OR held_event.event_id <> decision_intake.event_id THEN
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
       AND first.chunk_id = decision_intake.chunk_id
       AND first.decision_ordinal = 0;
    IF first_status <> 'pending' OR EXISTS (
        SELECT 1 FROM memoriesql.engine_type_decisions AS later
         WHERE later.tenant_id = authority.tenant_id
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
        'engine_record.accept.v1', item ->> 'record_key', digest, 'in_progress',
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
        item ->> 'record_key', recorded_at
    );
    INSERT INTO memoriesql.outbox_events VALUES (
        authority.tenant_id, source.workspace_id, source.access_scope_id,
        pg_catalog.uuidv7(), receipt_id, 'engine_chunk', chunk_id,
        'engine_type.decided',
        jsonb_build_object('record_key', item ->> 'record_key', 'event_id', event_id),
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

CREATE FUNCTION memoriesql.engine_accept_record_v1(
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

    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        authority.tenant_id::text || ':engine_chunk:' || chunk_ref::text, 0
    ));
    digest := encode(sha256(convert_to(item::text, 'UTF8')), 'hex');
    SELECT held.* INTO existing
      FROM memoriesql.engine_records AS held
     WHERE held.tenant_id = authority.tenant_id
       AND held.record_key = record_intake.record_key;
    IF FOUND THEN
        IF existing.source_object_id <> source.source_object_id THEN
            RETURN memoriesql.engine_rejected('the record key belongs to another engine scope');
        END IF;
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

-- Accepts one batch of engine records in delivery order and answers one
-- outcome per record: accepted, already_accepted, conflict (with the stored
-- record when the caller may read it), rejected (final) or retryable. After
-- the first retryable record,
-- the rest are answered retryable unread, because the engine resends them in
-- order and a later record may depend on an earlier one.
CREATE FUNCTION memoriesql.accept_engine_records_v1(request jsonb)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET lock_timeout = '500ms'
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    engine_scope text;
    item jsonb;
    outcome jsonb;
    deferred jsonb;
    results jsonb := '[]'::jsonb;
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL
       OR authority.expires_at <= pg_catalog.clock_timestamp()
       OR NOT memoriesql.current_context_has_capability('engine.records.deliver') THEN
        RAISE EXCEPTION 'engine record intake is unavailable' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(request) AS key)
          IS DISTINCT FROM ARRAY['contract_version', 'expected_schema_version', 'records']
       OR request -> 'contract_version' IS DISTINCT FROM '1'::jsonb
       OR request -> 'expected_schema_version' IS DISTINCT FROM '54'::jsonb
       OR jsonb_typeof(request -> 'records') IS DISTINCT FROM 'array'
       OR jsonb_array_length(request -> 'records') NOT BETWEEN 1 AND 256
       OR octet_length(request::text) > 16777216 THEN
        RAISE EXCEPTION 'invalid engine record batch' USING ERRCODE = '22023';
    END IF;
    -- Revocation takes this fence exclusively, so no record is accepted
    -- under authority that a committed revocation removed.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(pg_catalog.hashtextextended(
        authority.tenant_id::text || ':semantic_outcome_authority:', 0
    ));
    IF NOT EXISTS (
        SELECT 1 FROM memoriesql.authentication_credentials AS credential
         WHERE credential.tenant_id = authority.tenant_id
           AND credential.credential_id = authority.credential_id
           AND credential.principal_id = authority.principal_id
           AND credential.status = 'active'
           AND credential.expires_at > pg_catalog.clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'engine record intake is unavailable' USING ERRCODE = '42501';
    END IF;
    -- Writers to one engine scope take turns, so a scope's records commit in
    -- record_seq order and a reader's cursor never passes a record that
    -- commits later. Every scope is locked, in sorted order, before any
    -- record is read, so two batches never wait on each other in a cycle.
    FOR engine_scope IN
        SELECT DISTINCT record ->> 'scope'
          FROM jsonb_array_elements(request -> 'records') AS record
         WHERE jsonb_typeof(record -> 'scope') = 'string'
         ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            authority.tenant_id::text || ':engine_scope:' || engine_scope, 0
        ));
    END LOOP;
    FOR item IN SELECT value FROM jsonb_array_elements(request -> 'records') LOOP
        IF deferred IS NOT NULL THEN
            results := results || jsonb_build_array(deferred);
            CONTINUE;
        END IF;
        outcome := memoriesql.engine_accept_record_v1(authority, item);
        IF outcome ->> 'outcome' = 'retryable' THEN
            deferred := outcome;
        END IF;
        results := results || jsonb_build_array(outcome);
    END LOOP;
    RETURN jsonb_build_object('contract_version', 1, 'results', results);
END;
$$;

-- Records of one engine scope in acceptance order, for consumers and for the
-- engine's conformance suite. Needs source.read on the scope's source.
CREATE FUNCTION memoriesql.read_engine_records_v1(request jsonb)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    source memoriesql.source_objects%ROWTYPE;
    after_seq bigint;
    page_limit bigint;
    page jsonb;
    last_seq bigint;
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL
       OR authority.expires_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'engine records are unavailable' USING ERRCODE = '42501';
    END IF;
    after_seq := memoriesql.engine_json_int(COALESCE(request -> 'after', '0'::jsonb), 0, 9223372036854775807);
    page_limit := memoriesql.engine_json_int(COALESCE(request -> 'limit', '256'::jsonb), 1, 1024);
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request -> 'contract_version' IS DISTINCT FROM '1'::jsonb
       OR memoriesql.engine_json_text(request -> 'engine_scope', 4096) IS NULL
       OR after_seq IS NULL OR page_limit IS NULL
       OR EXISTS (
           SELECT 1 FROM jsonb_object_keys(request) AS key
            WHERE key NOT IN ('contract_version', 'engine_scope', 'after', 'limit')
       ) THEN
        RAISE EXCEPTION 'invalid engine record read' USING ERRCODE = '22023';
    END IF;
    source := memoriesql.engine_scope_source_v1(authority, request ->> 'engine_scope');
    IF source.source_object_id IS NULL
       OR NOT memoriesql.current_context_source_authorized(
           source.access_scope_id, source.source_object_id, 'source.read', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           source.access_scope_id, 'read', pg_catalog.clock_timestamp()
       ) THEN
        RAISE EXCEPTION 'engine records are unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(jsonb_agg(selected.record ORDER BY selected.record_seq), '[]'::jsonb),
           max(selected.record_seq)
      INTO page, last_seq
      FROM (
          SELECT held.record, held.record_seq
            FROM memoriesql.engine_records AS held
           WHERE held.tenant_id = authority.tenant_id
             AND held.source_object_id = source.source_object_id
             AND held.record_seq > after_seq
           ORDER BY held.record_seq
           LIMIT page_limit
      ) AS selected;
    RETURN jsonb_build_object(
        'contract_version', 1,
        'records', page,
        'next', COALESCE(last_seq, after_seq)
    );
END;
$$;

-- The governed bead types as an engine vocabulary: the engine loads this and
-- never registers or shadows a type of its own.
CREATE FUNCTION memoriesql.engine_vocabulary_v1()
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL
       OR authority.expires_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'the engine vocabulary is unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object(
        'id', 'memoriesql.bead_types',
        'types', (
            SELECT jsonb_agg(jsonb_build_object(
                       'key', governed.stable_key,
                       'rev', governed.revision,
                       'definition', governed.definition
                   ) ORDER BY governed.stable_key)
              FROM memoriesql.current_bead_type_revisions AS governed
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION memoriesql.guard_engine_type_upgrade() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_json_uuid(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_json_hex64(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_json_text(jsonb, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_json_int(jsonb, bigint, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_json_time(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_rejected(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_retryable(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_decision_fields_v1(jsonb, jsonb, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_scope_source_v1(memoriesql.authorization_contexts, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_accept_chunk_v1(
    memoriesql.authorization_contexts, memoriesql.source_objects, jsonb, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_accept_type_decision_v1(
    memoriesql.authorization_contexts, memoriesql.source_objects, jsonb, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_accept_record_v1(memoriesql.authorization_contexts, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.accept_engine_records_v1(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.read_engine_records_v1(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_vocabulary_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.accept_engine_records_v1(jsonb) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.read_engine_records_v1(jsonb) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.engine_vocabulary_v1() TO memoriesql_application;

COMMENT ON TABLE memoriesql.engine_records IS
    'Core Memory engine records exactly as delivered, one per record_key. First writer wins: a different record under a held key is a conflict, never a replacement.';
COMMENT ON TABLE memoriesql.engine_chunks IS
    'Engine chunk text, verified against its hash and byte/character ranges. Engine chunks are not PR-01 source units and author no meaning.';
COMMENT ON TABLE memoriesql.engine_type_decisions IS
    'The type outcome each engine chunk record carried, and the one later decision allowed for a pending chunk. Types are governed bead types; nothing is relabeled.';
