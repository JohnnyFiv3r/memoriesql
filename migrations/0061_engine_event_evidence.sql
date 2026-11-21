-- First binding of a complete Core Memory v2 normalized event to one accepted
-- canonical evidence unit. No producer text or claim is copied into this map.
CREATE TABLE memoriesql.engine_event_evidence (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    engine_workspace text NOT NULL,
    engine_event_id uuid NOT NULL,
    event_sha256 text NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, source_object_id, engine_workspace, engine_event_id),
    UNIQUE (tenant_id, source_unit_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, source_object_id)
        REFERENCES memoriesql.source_objects
            (tenant_id, workspace_id, access_scope_id, source_object_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, event_id, source_unit_id)
        REFERENCES memoriesql.source_units
            (tenant_id, workspace_id, access_scope_id, event_id, source_unit_id)
);

CREATE TABLE memoriesql.engine_chunk_evidence (
    tenant_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    engine_workspace text NOT NULL,
    engine_event_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    ordinal integer NOT NULL,
    span jsonb NOT NULL,
    PRIMARY KEY (tenant_id, source_object_id, engine_workspace, chunk_id),
    UNIQUE (tenant_id, source_object_id, engine_workspace, engine_event_id, ordinal),
    FOREIGN KEY (tenant_id, source_object_id, engine_workspace, engine_event_id)
        REFERENCES memoriesql.engine_event_evidence
);

ALTER TABLE memoriesql.engine_event_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_event_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_chunk_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.engine_chunk_evidence FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.engine_event_evidence, memoriesql.engine_chunk_evidence FROM PUBLIC;

CREATE TRIGGER engine_event_evidence_immutable
BEFORE UPDATE OR DELETE ON memoriesql.engine_event_evidence
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER engine_chunk_evidence_immutable
BEFORE UPDATE OR DELETE ON memoriesql.engine_chunk_evidence
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.engine_evidence_source_v1(
    authority memoriesql.authorization_contexts, source_id uuid
) RETURNS memoriesql.source_objects
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE enrolled memoriesql.source_objects%ROWTYPE;
BEGIN
    IF authority.tenant_id IS NULL OR authority.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO enrolled FROM memoriesql.source_objects
     WHERE tenant_id = authority.tenant_id AND workspace_id = authority.workspace_id
       AND source_object_id = source_id AND source_system = 'core-memory'
       AND object_kind = 'engine_scope' AND installation_key = '';
    IF NOT FOUND
       OR NOT memoriesql.current_context_source_authorized(
           enrolled.access_scope_id, source_id, 'source.read', 'read')
       OR NOT memoriesql.current_context_source_authorized(
           enrolled.access_scope_id, source_id, 'memory.query', 'read')
       OR NOT memoriesql.current_context_scope_time_authorized(
           enrolled.access_scope_id, 'read', clock_timestamp()) THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN enrolled;
END $$;
REVOKE ALL ON FUNCTION memoriesql.engine_evidence_source_v1(
    memoriesql.authorization_contexts, uuid
) FROM PUBLIC;

CREATE FUNCTION memoriesql.bind_engine_event_evidence_v1(request jsonb) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    enrolled memoriesql.source_objects%ROWTYPE;
    prior memoriesql.engine_event_evidence%ROWTYPE;
    source_id uuid := memoriesql.engine_json_uuid(request -> 'source_object_id');
    workspace_key text := memoriesql.engine_json_text(request -> 'engine_workspace', 128);
    engine_id uuid := memoriesql.engine_json_uuid(request -> 'engine_event_id');
    unit_id uuid := memoriesql.engine_json_uuid(request -> 'source_unit_id');
    claim_id uuid := memoriesql.engine_json_uuid(request -> 'statement_id');
    event_digest text := memoriesql.engine_json_hex64(request -> 'event_sha256');
    chunk_spans jsonb := request -> 'chunks';
    canonical_event_id uuid;
    accepted_version_id uuid;
    held_spans jsonb;
    normalized bytea;
    part bytea;
    span jsonb;
    chunk_id uuid;
    start_byte integer := 0;
    end_byte integer;
    start_char integer := 0;
    end_char integer;
    chunk_digest text;
    ordinal integer := 0;
    max_bytes constant integer := 16777216;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'engine evidence requires read committed' USING ERRCODE = '25001';
    END IF;
    SELECT * INTO authority FROM memoriesql.current_authorization_context();
    enrolled := memoriesql.engine_evidence_source_v1(authority, source_id);
    IF (memoriesql.engine_deliverable_source_v1(
            authority, enrolled.external_object_id)).source_object_id IS DISTINCT FROM source_id THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended(
        authority.tenant_id::text || ':semantic_outcome_authority:', 0));
    PERFORM memoriesql.engine_lock_scope_v1(authority, source_id);
    SELECT * INTO authority FROM memoriesql.current_authorization_context();
    enrolled := memoriesql.engine_evidence_source_v1(authority, source_id);
    IF (memoriesql.engine_deliverable_source_v1(
            authority, enrolled.external_object_id)).source_object_id IS DISTINCT FROM source_id THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR NOT (request - ARRAY['source_object_id','engine_workspace','engine_event_id',
            'source_unit_id','statement_id','event_sha256','chunks'] = '{}'::jsonb)
       OR pg_column_size(request) > 262144
       OR workspace_key IS NULL OR engine_id IS NULL OR unit_id IS NULL
       OR claim_id IS NULL OR event_digest IS NULL
       OR jsonb_typeof(chunk_spans) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid engine event evidence' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(chunk_spans) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'invalid engine event evidence' USING ERRCODE = '22023';
    END IF;

    -- The native event identity carries the producer workspace as well as the
    -- event UUID. Matching text in a different event cannot qualify a pin.
    SELECT ev.event_id, accepted.bead_version_id INTO canonical_event_id, accepted_version_id
      FROM memoriesql.bead_semantic_statement_evidence AS evidence
      JOIN memoriesql.bead_semantic_statements AS st
        USING (tenant_id, workspace_id, access_scope_id, statement_id)
      JOIN memoriesql.accepted_bead_semantics AS accepted
        ON accepted.tenant_id = st.tenant_id AND accepted.bead_version_id = st.bead_version_id
      JOIN memoriesql.source_events AS ev
        ON (ev.tenant_id, ev.workspace_id, ev.access_scope_id, ev.event_id)
         = (evidence.tenant_id, evidence.workspace_id, evidence.access_scope_id,
            evidence.evidence_event_id)
     WHERE evidence.tenant_id = authority.tenant_id
       AND evidence.workspace_id = authority.workspace_id
       AND evidence.access_scope_id = enrolled.access_scope_id
       AND evidence.statement_id = claim_id AND evidence.evidence_source_unit_id = unit_id
       AND ev.source_object_id = source_id
       AND ev.external_event_id = 'core-memory.v2:' || workspace_key || ':' || engine_id::text;
    IF NOT FOUND OR NOT memoriesql.current_context_semantic_statement_authorized(
            authority.tenant_id, authority.workspace_id, enrolled.access_scope_id, claim_id)
       OR NOT memoriesql.current_context_bead_version_authorized(
            authority.tenant_id, authority.workspace_id, enrolled.access_scope_id, accepted_version_id) THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO prior FROM memoriesql.engine_event_evidence
     WHERE tenant_id = authority.tenant_id AND source_object_id = source_id
       AND engine_workspace = workspace_key AND engine_event_id = engine_id;
    IF FOUND THEN
        SELECT jsonb_agg(held.span ORDER BY held.ordinal) INTO held_spans
          FROM memoriesql.engine_chunk_evidence AS held
         WHERE held.tenant_id = authority.tenant_id AND held.source_object_id = source_id
           AND held.engine_workspace = workspace_key AND held.engine_event_id = engine_id;
        IF prior.source_unit_id <> unit_id OR prior.event_sha256 <> event_digest
           OR held_spans IS DISTINCT FROM chunk_spans THEN
            RAISE EXCEPTION 'engine event evidence conflict' USING ERRCODE = '23505';
        END IF;
        RETURN 'already_bound';
    END IF;

    normalized := convert_to(memoriesql.query_unit_source_text_v1(
        authority.tenant_id, unit_id) ->> 'text', 'UTF8');
    IF normalized IS NULL OR octet_length(normalized) > max_bytes
       OR encode(sha256(normalized), 'hex') <> event_digest THEN
        RAISE EXCEPTION 'engine event evidence does not match normalized source'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO memoriesql.engine_event_evidence VALUES (
        authority.tenant_id, authority.workspace_id, enrolled.access_scope_id,
        source_id, workspace_key, engine_id, event_digest, canonical_event_id, unit_id
    );
    FOR span IN SELECT value FROM jsonb_array_elements(chunk_spans) LOOP
        IF jsonb_typeof(span) IS DISTINCT FROM 'object'
           OR span - ARRAY['chunk_id','byte_start','byte_end','char_start','char_end',
                            'chunk_sha256'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid engine chunk span' USING ERRCODE = '22023';
        END IF;
        chunk_id := memoriesql.engine_json_uuid(span -> 'chunk_id');
        chunk_digest := memoriesql.engine_json_hex64(span -> 'chunk_sha256');
        end_byte := memoriesql.engine_json_int(
            span -> 'byte_end', start_byte + 1, octet_length(normalized));
        end_char := memoriesql.engine_json_int(span -> 'char_end', start_char + 1, max_bytes);
        IF chunk_id IS NULL OR chunk_digest IS NULL OR end_byte IS NULL OR end_char IS NULL
           OR span -> 'byte_start' IS DISTINCT FROM to_jsonb(start_byte)
           OR span -> 'char_start' IS DISTINCT FROM to_jsonb(start_char) THEN
            RAISE EXCEPTION 'invalid engine chunk span' USING ERRCODE = '22023';
        END IF;
        part := substring(normalized FROM start_byte + 1 FOR end_byte - start_byte);
        -- convert_from enforces UTF-8 boundaries, including for a valid hash
        -- of an interval that stops in the middle of a code point.
        IF encode(sha256(part), 'hex') <> chunk_digest
           OR char_length(convert_from(part, 'UTF8')) <> end_char - start_char THEN
            RAISE EXCEPTION 'engine chunk span does not match normalized source'
                USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.engine_chunk_evidence VALUES (
            authority.tenant_id, source_id, workspace_key, engine_id, chunk_id, ordinal, span
        );
        start_byte := end_byte;
        start_char := end_char;
        ordinal := ordinal + 1;
    END LOOP;
    IF start_byte <> octet_length(normalized) THEN
        RAISE EXCEPTION 'incomplete engine event evidence' USING ERRCODE = '22023';
    END IF;
    PERFORM memoriesql.engine_evidence_source_v1(authority, source_id);
    IF (memoriesql.engine_deliverable_source_v1(
            authority, enrolled.external_object_id)).source_object_id IS DISTINCT FROM source_id THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN 'bound';
EXCEPTION
    WHEN character_not_in_repertoire THEN
        RAISE EXCEPTION 'invalid UTF-8 engine chunk span' USING ERRCODE = '22023';
    WHEN unique_violation THEN
        RAISE EXCEPTION 'engine event evidence conflict' USING ERRCODE = '23505';
END $$;
REVOKE ALL ON FUNCTION memoriesql.bind_engine_event_evidence_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.bind_engine_event_evidence_v1(jsonb) TO memoriesql_application;

-- Internal lifecycle seam. No application grant. In particular the source
-- unit and pin returned here cannot mint a result-delivered source ref.
CREATE FUNCTION memoriesql.resolve_engine_chunk_evidence_v1(uuid, text, uuid, uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    enrolled memoriesql.source_objects%ROWTYPE;
    binding jsonb;
BEGIN
    SELECT * INTO authority FROM memoriesql.current_authorization_context();
    enrolled := memoriesql.engine_evidence_source_v1(authority, $1);
    SELECT chunk.span || jsonb_build_object(
        'event_id', event.event_id, 'source_unit_id', event.source_unit_id,
        'content_sha256', unit.content_hash)
      INTO binding
      FROM memoriesql.engine_chunk_evidence AS chunk
      JOIN memoriesql.engine_event_evidence AS event
        USING (tenant_id, source_object_id, engine_workspace, engine_event_id)
      JOIN memoriesql.source_units AS unit
        ON (unit.tenant_id, unit.workspace_id, unit.access_scope_id, unit.event_id, unit.source_unit_id)
         = (event.tenant_id, event.workspace_id, event.access_scope_id, event.event_id, event.source_unit_id)
     WHERE chunk.tenant_id = authority.tenant_id AND chunk.source_object_id = $1
       AND chunk.engine_workspace = $2 AND chunk.engine_event_id = $3 AND chunk.chunk_id = $4
       AND event.workspace_id = authority.workspace_id
       AND event.access_scope_id = enrolled.access_scope_id
       AND memoriesql.current_context_event_authorized(
           event.access_scope_id, event.event_id, 'source.read', 'read')
       AND memoriesql.current_context_event_authorized(
           event.access_scope_id, event.event_id, 'memory.query', 'read');
    IF binding IS NULL THEN
        RAISE EXCEPTION 'engine evidence is unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN binding;
END $$;
REVOKE ALL ON FUNCTION memoriesql.resolve_engine_chunk_evidence_v1(uuid, text, uuid, uuid) FROM PUBLIC;
