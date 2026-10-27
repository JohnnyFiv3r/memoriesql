-- Engine record intake privacy (fixes P-1 to P-3 of the post-merge review of
-- migration 0055).
--
-- Delivering to an engine scope now needs everything reading it needs:
-- engine.records.deliver with a write grant and window AND source.read with a
-- read grant and window on the scope's source. One predicate decides both
-- which scopes a batch locks and whether a record is read. Every other case,
-- a scope that is not enrolled at all included, gets one answer before any
-- record of the scope is consulted or locked. So a deliverer learns nothing
-- about a scope it may not read, not even whether it exists, and every
-- outcome about held records goes to a caller who may read them.
--
-- Reads page with an opaque cursor that names the last record returned within
-- its engine scope. The global record_seq stays internal, so other scopes' and
-- tenants' acceptances never change a cursor or a page.
--
-- Forward-only. 0055 is unchanged; its tables, grants, capability, chunk and
-- type-decision paths stay as they are.

-- The enrolled source standing for `engine_scope` in the caller's workspace,
-- only while the caller may both deliver to it and read it. Anything else,
-- whether not enrolled, not granted or outside a window, is the same NULL.
CREATE FUNCTION memoriesql.engine_deliverable_source_v1(
    authority memoriesql.authorization_contexts,
    engine_scope text
)
RETURNS memoriesql.source_objects
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT enrolled.*
      FROM memoriesql.engine_scope_source_v1(authority, engine_scope) AS enrolled
     WHERE enrolled.source_object_id IS NOT NULL
       AND memoriesql.current_context_source_authorized(
           enrolled.access_scope_id, enrolled.source_object_id,
           'engine.records.deliver', 'write'
       )
       AND memoriesql.current_context_scope_time_authorized(
           enrolled.access_scope_id, 'write', pg_catalog.clock_timestamp()
       )
       AND memoriesql.current_context_source_authorized(
           enrolled.access_scope_id, enrolled.source_object_id, 'source.read', 'read'
       )
       AND memoriesql.current_context_scope_time_authorized(
           enrolled.access_scope_id, 'read', pg_catalog.clock_timestamp()
       )
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

    -- One answer for every scope this caller may not both deliver to and
    -- read, a scope that is not enrolled included, given before any record of
    -- the scope is consulted or locked.
    source := memoriesql.engine_deliverable_source_v1(authority, engine_scope);
    IF source.source_object_id IS NULL THEN
        RETURN memoriesql.engine_retryable(
            'the engine scope is not enrolled, or this credential may not deliver to and read it'
        );
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
        -- Every deliverer may read the scope, so it is shown the held record.
        RETURN jsonb_build_object('outcome', 'conflict', 'stored', existing.record);
    END IF;

    IF record_kind = 'chunk' THEN
        RETURN memoriesql.engine_accept_chunk_v1(authority, source, item, digest);
    END IF;
    RETURN memoriesql.engine_accept_type_decision_v1(authority, source, item, digest);
END record_intake;
$$;

-- Accepts one batch of engine records in delivery order and answers one
-- outcome per record: accepted, already_accepted, conflict (with the stored
-- record), rejected (final) or retryable. After the first retryable record,
-- the rest are answered retryable unread, because the engine resends them in
-- order and a later record may depend on an earlier one.
CREATE OR REPLACE FUNCTION memoriesql.accept_engine_records_v1(request jsonb)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET lock_timeout = '500ms'
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    writable uuid;
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
       OR request -> 'expected_schema_version' IS DISTINCT FROM '55'::jsonb
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
    -- acceptance order and a reader's cursor never passes a record that
    -- commits later. Only scopes this caller may deliver to and read are
    -- locked, by the same predicate each record is answered by, so a batch
    -- never holds back or waits on any other scope, enrolled or not. They are
    -- locked in source order before any record is read, so two batches never
    -- wait on each other in a cycle.
    FOR writable IN
        SELECT DISTINCT resolved.source_object_id
          FROM (
              SELECT DISTINCT record ->> 'scope' AS engine_scope
                FROM jsonb_array_elements(request -> 'records') AS record
               WHERE jsonb_typeof(record -> 'scope') = 'string'
          ) AS named
          CROSS JOIN LATERAL memoriesql.engine_deliverable_source_v1(
              authority, named.engine_scope
          ) AS resolved
         WHERE resolved.source_object_id IS NOT NULL
         ORDER BY 1
    LOOP
        PERFORM memoriesql.engine_lock_scope_v1(authority, writable);
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
-- engine's conformance suite, paged by an opaque cursor. Needs source.read on
-- the scope's source.
CREATE OR REPLACE FUNCTION memoriesql.read_engine_records_v1(request jsonb)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    source memoriesql.source_objects%ROWTYPE;
    after_key text;
    after_seq bigint := 0;
    page_limit bigint;
    page jsonb;
    last_key text;
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL
       OR authority.expires_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'engine records are unavailable' USING ERRCODE = '42501';
    END IF;
    page_limit := memoriesql.engine_json_int(COALESCE(request -> 'limit', '256'::jsonb), 1, 1024);
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request -> 'contract_version' IS DISTINCT FROM '1'::jsonb
       OR memoriesql.engine_json_text(request -> 'engine_scope', 4096) IS NULL
       OR page_limit IS NULL
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
    -- The cursor names the last record a page returned, within this engine
    -- scope only. It is examined only once the caller may read the scope, so
    -- every cursor gets the same answer for a scope it may not.
    IF jsonb_typeof(request -> 'after') = 'string' THEN
        after_key := memoriesql.engine_json_text(request -> 'after', 128);
        SELECT held.record_seq INTO after_seq
          FROM memoriesql.engine_records AS held
         WHERE held.tenant_id = authority.tenant_id
           AND held.source_object_id = source.source_object_id
           AND held.record_key = after_key;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'invalid engine record read' USING ERRCODE = '22023';
        END IF;
    ELSIF request ? 'after' AND jsonb_typeof(request -> 'after') <> 'null' THEN
        RAISE EXCEPTION 'invalid engine record read' USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(selected.record ORDER BY selected.record_seq), '[]'::jsonb),
           (array_agg(selected.record_key ORDER BY selected.record_seq DESC))[1]
      INTO page, last_key
      FROM (
          SELECT held.record, held.record_seq, held.record_key
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
        'next', COALESCE(last_key, after_key)
    );
END;
$$;

REVOKE ALL ON FUNCTION memoriesql.engine_deliverable_source_v1(memoriesql.authorization_contexts, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.engine_accept_record_v1(memoriesql.authorization_contexts, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.accept_engine_records_v1(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.read_engine_records_v1(jsonb) FROM PUBLIC;
