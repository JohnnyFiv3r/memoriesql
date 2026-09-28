-- PR-05 observation-family logical population for the restricted SQL reader.
-- Forward-only: M0030 remains the sole lifecycle/authority kernel and M0032's
-- nine assessed relations are consumed unchanged. No agent grant, result API or
-- entity/alias/mention/topic population is added; those remain unsupported.

-- One authorized historical frame for all fourteen prepared relations. A whole
-- observation family (accepted bead version, statements, statement evidence,
-- source units and every correction neighbour visible at the frame) is either
-- admitted or withheld; no protected counts, identifiers or neighbours escape.
CREATE FUNCTION memoriesql.prepare_query_sql_population_v2(
    requested_known_at timestamptz, requested_view text, byte_budget integer
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    snapshot_at timestamptz := statement_timestamp();
    known timestamptz; relation_part jsonb; b record; n record; st record; e record; u record;
    family_deps jsonb; family_observations jsonb; family_statements jsonb; family_sources jsonb;
    family_corrections jsonb; family_units uuid[]; unit_id uuid; successors integer;
    effective timestamptz; seen_units uuid[] := '{}';
    observations jsonb := '[]'; statements jsonb := '[]'; sources jsonb := '[]';
    units jsonb := '[]'; corrections jsonb := '[]'; deps jsonb; manifest jsonb; result jsonb;
    estimate bigint;
BEGIN
    IF byte_budget IS NULL OR byte_budget NOT BETWEEN 8192 AND 67108864
       OR requested_view IS NULL OR requested_view NOT IN ('resolved','historical') THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    known := COALESCE(requested_known_at, snapshot_at);
    IF NOT isfinite(known) OR known > snapshot_at
       OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id)
       OR NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    -- The unchanged PR-03 projection supplies the assessed relations at the same
    -- explicit cutoff in this statement; its dependencies join one manifest.
    relation_part := memoriesql.prepare_relation_sql_population_v1(known, byte_budget);
    deps := (relation_part->>'dependency_records_json')::jsonb;
    -- Native jsonb text length is an admission estimate while accumulating. The
    -- exact canonical bytes are counted once below and again by the trusted
    -- decoder; nothing is disclosed from a partial population.
    estimate := octet_length(relation_part::text);
    FOR b IN SELECT a.bead_id, a.bead_version_id, v.event_id, v.source_unit_id, v.authored_at,
                    v.bead_type_revision_id, t.stable_key, v.render_contract_revision,
                    v.render_payload, v.title, v.summary, v.detail
             FROM memoriesql.accepted_bead_semantics a
             JOIN memoriesql.bead_versions v
               ON v.tenant_id=a.tenant_id AND v.bead_version_id=a.bead_version_id
             JOIN memoriesql.bead_types t ON t.bead_type_id=v.bead_type_id
             WHERE a.tenant_id=c.tenant_id AND a.workspace_id=c.workspace_id AND v.authored_at<=known
             ORDER BY a.bead_id LOOP
        family_corrections := '[]'; successors := 0;
        BEGIN
            family_deps := memoriesql.relation_bead_records_v1(c.tenant_id,b.bead_id,known);
            -- Correction neighbours at this frame are required dependencies. A
            -- protected successor or predecessor withholds this whole family:
            -- no successor count, branch or older-as-current presentation leaks.
            FOR n IN SELECT s.bead_id AS successor_bead_id, s.bead_version_id AS successor_version_id,
                            s.superseded_bead_id, s.superseded_bead_version_id,
                            s.correction_reason, sv.authored_at
                     FROM memoriesql.bead_supersessions s
                     JOIN memoriesql.bead_versions sv
                       ON sv.tenant_id=s.tenant_id AND sv.bead_version_id=s.bead_version_id
                     WHERE s.tenant_id=c.tenant_id AND sv.authored_at<=known
                       AND ((s.superseded_bead_id=b.bead_id AND s.superseded_bead_version_id=b.bead_version_id)
                         OR (s.bead_id=b.bead_id AND s.bead_version_id=b.bead_version_id))
                     ORDER BY s.bead_id, s.superseded_bead_id LOOP
                IF n.successor_bead_id=b.bead_id THEN
                    family_deps := family_deps
                        || memoriesql.relation_bead_records_v1(c.tenant_id,n.superseded_bead_id,known);
                    family_corrections := family_corrections || jsonb_build_array(jsonb_build_object(
                        'predecessor_version_id',n.superseded_bead_version_id,
                        'successor_version_id',n.successor_version_id,
                        'predecessor_bead_id',n.superseded_bead_id,
                        'successor_bead_id',n.successor_bead_id,
                        'reason',n.correction_reason,
                        'recorded_at',memoriesql.relation_packet_time(n.authored_at)));
                ELSE
                    successors := successors + 1;
                    family_deps := family_deps
                        || memoriesql.relation_bead_records_v1(c.tenant_id,n.successor_bead_id,known);
                END IF;
                family_deps := family_deps || jsonb_build_array(jsonb_build_object(
                    'kind','bead_supersession',
                    'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(
                        n.superseded_bead_version_id,n.successor_version_id)),
                    'row',jsonb_build_object('predecessor_bead_id',n.superseded_bead_id,
                        'predecessor_version_id',n.superseded_bead_version_id,
                        'successor_bead_id',n.successor_bead_id,
                        'successor_version_id',n.successor_version_id,
                        'reason',n.correction_reason,
                        'recorded_at',memoriesql.relation_packet_time(n.authored_at))));
            END LOOP;
        EXCEPTION WHEN insufficient_privilege THEN CONTINUE;
        END;
        -- Effective time is only an actually represented instant of the bead's own
        -- source unit (or, absent unit time, its event). Imprecise stays null.
        SELECT CASE
                 WHEN su.unit_source_occurred_at IS NOT NULL THEN
                   CASE WHEN su.unit_time_precision IN ('instant','second') THEN su.unit_source_occurred_at END
                 WHEN ev.source_time_precision IN ('instant','second') THEN ev.source_occurred_at
               END
          INTO effective
          FROM memoriesql.source_units su
          JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id
         WHERE su.tenant_id=c.tenant_id AND su.source_unit_id=b.source_unit_id;
        family_observations := '[]';
        -- Resolved view withholds superseded and branched predecessors but keeps
        -- every unsuperseded successor branch; it never chooses a recency winner.
        IF requested_view='historical' OR successors=0 THEN
            family_observations := jsonb_build_array(jsonb_build_object(
                'bead_id',b.bead_id,'bead_version_id',b.bead_version_id,'event_id',b.event_id,
                'source_unit_id',b.source_unit_id,'bead_type_key',b.stable_key,
                'bead_type_revision_id',b.bead_type_revision_id,
                'title',CASE WHEN b.render_contract_revision=2 THEN b.title END,
                'summary',CASE WHEN b.render_contract_revision=2 THEN b.summary END,
                'detail',CASE WHEN b.render_contract_revision=2 THEN b.detail END,
                'render_state',CASE WHEN b.render_contract_revision=2 AND b.render_payload IS NOT NULL
                                    THEN 'present' ELSE 'unsupported' END,
                'recorded_at',memoriesql.relation_packet_time(b.authored_at),
                'effective_at',memoriesql.relation_packet_time(effective),
                'effective_basis',CASE WHEN effective IS NULL THEN 'unknown' ELSE 'source' END,
                'correction_state',CASE successors WHEN 0 THEN 'unsuperseded' WHEN 1 THEN 'superseded'
                                                   ELSE 'branched' END));
        END IF;
        family_statements := '[]'; family_sources := '[]'; family_units := ARRAY[b.source_unit_id];
        FOR st IN SELECT * FROM memoriesql.bead_semantic_statements
                  WHERE tenant_id=c.tenant_id AND bead_version_id=b.bead_version_id
                  ORDER BY statement_sequence, statement_id LOOP
            family_statements := family_statements || jsonb_build_array(jsonb_build_object(
                'statement_id',st.statement_id,'bead_id',st.bead_id,'bead_version_id',st.bead_version_id,
                'sequence',st.statement_sequence,'kind',st.statement_kind,'text',st.statement_text,
                'supersedes_statement_id',st.supersedes_statement_id,
                'correction_reason',st.correction_reason,
                'recorded_at',memoriesql.relation_packet_time(st.created_at)));
            FOR e IN SELECT * FROM memoriesql.bead_semantic_statement_evidence
                     WHERE tenant_id=c.tenant_id AND statement_id=st.statement_id
                     ORDER BY evidence_source_unit_id LOOP
                family_sources := family_sources || jsonb_build_array(jsonb_build_object(
                    'statement_id',st.statement_id,'source_unit_id',e.evidence_source_unit_id,
                    'event_id',e.evidence_event_id,'content_sha256',e.evidence_content_hash));
                family_units := family_units || e.evidence_source_unit_id;
            END LOOP;
        END LOOP;
        -- Only units with an actual support link to an admitted accepted
        -- observation enter; unlinked raw source discovery stays excluded.
        FOREACH unit_id IN ARRAY family_units LOOP
            CONTINUE WHEN unit_id = ANY(seen_units);
            seen_units := seen_units || unit_id;
            SELECT su.source_unit_id, su.content_hash, su.event_id, ev.source_object_id,
                   ev.source_type, su.content_text, su.unit_source_occurred_at,
                   su.unit_time_precision, ev.source_occurred_at, ev.source_time_precision,
                   ev.source_occurred_at_raw, ev.source_timezone, ev.recorded_at,
                   ev.actor_id, ev.actor_kind
              INTO u
              FROM memoriesql.source_units su
              JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id
             WHERE su.tenant_id=c.tenant_id AND su.source_unit_id=unit_id;
            units := units || jsonb_build_array(jsonb_build_object(
                'source_unit_id',u.source_unit_id,'content_sha256',u.content_hash,
                'event_id',u.event_id,'source_object_id',u.source_object_id,
                'source_kind',u.source_type,'package_revision_id',NULL,
                'search_text',u.content_text,
                'text_state',CASE WHEN u.content_text IS NULL THEN 'unsupported' ELSE 'available' END,
                'source_occurred_at',memoriesql.relation_packet_time(
                    COALESCE(u.unit_source_occurred_at,u.source_occurred_at)),
                'source_time_original',CASE WHEN u.unit_source_occurred_at IS NULL
                                            THEN u.source_occurred_at_raw END,
                'source_timezone',u.source_timezone,
                'source_precision',CASE
                    WHEN u.unit_source_occurred_at IS NOT NULL THEN COALESCE(u.unit_time_precision,'unknown')
                    WHEN u.source_occurred_at IS NOT NULL THEN COALESCE(u.source_time_precision,'unknown')
                    ELSE 'unknown' END,
                'recorded_at',memoriesql.relation_packet_time(u.recorded_at),
                'actor_ref',u.actor_id,'actor_role',u.actor_kind,
                'trust_label',NULL,'occurrence_ref',NULL));
        END LOOP;
        observations := observations || family_observations;
        statements := statements || family_statements;
        sources := sources || family_sources;
        corrections := corrections || family_corrections;
        deps := deps || family_deps;
        estimate := estimate + octet_length(family_observations::text)
            + octet_length(family_statements::text) + octet_length(family_sources::text)
            + octet_length(family_corrections::text) + octet_length(family_deps::text);
        IF estimate + octet_length(units::text) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]')
      INTO deps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(deps)) x;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
        'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
        ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO manifest
      FROM jsonb_array_elements(deps) x(v);
    result := jsonb_build_object(
        'population_revision',2,
        'assertions',relation_part->'assertions','corrections',relation_part->'corrections',
        'types',relation_part->'types','pairs',relation_part->'pairs',
        'observations',observations,'statements',statements,'statement_sources',sources,
        'source_units',units,'observation_corrections',corrections,
        'frame',jsonb_build_object('known_at',memoriesql.relation_packet_time(known),
            'snapshot_at',memoriesql.relation_packet_time(snapshot_at),
            'view',requested_view,
            'dependency_manifest_sha256',memoriesql.lifecycle_hash_v1(manifest),
            -- The shared PR-03 projector's own visible-dependency hash, reported
            -- only when a result actually uses a relation projection.
            'relation_manifest_sha256',relation_part#>>'{frame,dependency_manifest_sha256}'),
        'dependency_records_json',memoriesql.lifecycle_canonical_json_v1(deps),
        'dependency_manifest_json',memoriesql.lifecycle_canonical_json_v1(manifest));
    IF octet_length(result::text) + 8192 > byte_budget THEN
        RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
    END IF;
    IF NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_sql_population_v2(timestamptz,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_query_sql_population_v2(timestamptz,text,integer) TO memoriesql_application;

-- Same trusted issuer/epoch binding as M0033, for the complete fourteen-relation
-- population. Size admission uses native jsonb text so large source text is not
-- canonicalized character by character inside the operation deadline.
CREATE FUNCTION memoriesql.stage_query_population_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; sc memoriesql.authorization_contexts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE;
 ref uuid:=gen_random_uuid(); reader record; source record; item record; n bigint;
 budget integer:=(request->>'reserved_ms')::integer;
 remaining integer:=(request->>'remaining_ms')::integer; now_at timestamptz:=clock_timestamp();
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 sc:=jsonb_populate_record(NULL::memoriesql.authorization_contexts,request->'issuer_context');
 IF sc.context_id IS NULL OR sc.backend_pid IS DISTINCT FROM (request->>'issuer_pid')::integer
  OR sc.expires_at<=clock_timestamp() OR sc.tenant_id IS DISTINCT FROM c.tenant_id
  OR sc.workspace_id IS DISTINCT FROM c.workspace_id OR sc.credential_id IS DISTINCT FROM c.credential_id
  OR sc.principal_id IS DISTINCT FROM c.principal_id OR sc.user_id IS DISTINCT FROM c.user_id
  OR sc.on_behalf_of_user_id IS DISTINCT FROM c.on_behalf_of_user_id
  OR sc.pairing_grant_id IS DISTINCT FROM c.pairing_grant_id
  OR sc.membership_revision IS DISTINCT FROM c.membership_revision
  OR sc.pairing_grant_revision IS DISTINCT FROM c.pairing_grant_revision THEN
  RAISE EXCEPTION 'query_source_context_unavailable' USING ERRCODE='42501';
 END IF;
 IF budget IS NULL OR budget NOT BETWEEN 1 AND 30000 OR remaining IS NULL OR remaining NOT BETWEEN 1 AND budget THEN
  RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation:'||c.workspace_id::text,0));
 UPDATE memoriesql.result_preparation_allocators SET revision=revision+1
  WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id;
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND operation_ref=(request->>'operation_ref')::uuid FOR UPDATE;
 IF NOT FOUND OR o.state<>'reserved' OR o.ownership_ref<>(request->>'ownership_ref')::uuid
  OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) THEN
  RAISE EXCEPTION 'query_invocation_unavailable' USING ERRCODE='42501';
 END IF;
 IF EXISTS(SELECT 1 FROM memoriesql_query.invocations WHERE tenant_id=c.tenant_id
  AND (operation_ref=o.operation_ref OR (workspace_id=c.workspace_id AND state<>'settled'))) THEN
  RAISE EXCEPTION 'query_ownership_pending' USING ERRCODE='55000';
 END IF;
 SELECT a.*,r.oid role_oid INTO reader FROM pg_stat_activity a JOIN pg_roles r ON r.oid=a.usesysid
  WHERE a.pid=(request->>'reader_pid')::integer AND a.backend_start=(request->>'reader_start')::timestamptz
   AND a.datid=(SELECT oid FROM pg_database WHERE datname=current_database())
   AND r.oid=(request->>'reader_oid')::oid AND r.rolcanlogin
   AND NOT(r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication)
   AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=a.pid AND locktype='virtualxid' AND granted
       AND virtualxid=request->>'reader_transaction');
 IF NOT FOUND THEN RAISE EXCEPTION 'query_login_unavailable' USING ERRCODE='42501'; END IF;
 SELECT a.* INTO source FROM pg_stat_activity a WHERE a.pid=(request->>'issuer_pid')::integer
  AND a.backend_start=(request->>'issuer_start')::timestamptz AND a.datid=reader.datid
  AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=a.pid AND locktype='virtualxid' AND granted
      AND virtualxid=request->>'issuer_transaction');
 IF NOT FOUND OR source.pid=reader.pid THEN
  RAISE EXCEPTION 'query_source_unavailable' USING ERRCODE='42501';
 END IF;
 n:=octet_length((request->'rows')::text)+8192;
 IF n>o.reservation_bytes OR jsonb_typeof(request->'rows')<>'object' THEN
  RAISE EXCEPTION 'query_stage_budget_exhausted' USING ERRCODE='54000';
 END IF;
 INSERT INTO memoriesql_query.invocations(invocation_ref,tenant_id,workspace_id,principal_id,
  credential_id,owner_user_id,operation_ref,ownership_ref,run_ref,step_key,
  reader_oid,reader_pid,reader_start,reader_transaction,issuer_pid,issuer_start,issuer_transaction,
  frame_ref,catalog_hash,policy_hash,scope_hash,projection_manifest_sha256,known_at,snapshot_at,
  started_at,deadline,reserved_ms,encoded_stage_bytes,state)
 VALUES(ref,c.tenant_id,c.workspace_id,c.principal_id,c.credential_id,COALESCE(c.on_behalf_of_user_id,c.user_id),
  o.operation_ref,o.ownership_ref,o.run_ref,o.step_key,reader.role_oid,reader.pid,reader.backend_start,
  request->>'reader_transaction',source.pid,source.backend_start,request->>'issuer_transaction',
  (request->>'frame_ref')::uuid,request->>'catalog_hash',request->>'policy_hash',request->>'scope_hash',
  request->>'projection_manifest_sha256',(request->>'known_at')::timestamptz,(request->>'snapshot_at')::timestamptz,
  now_at,LEAST(now_at+remaining*interval '1 millisecond',c.expires_at,sc.expires_at,
   (SELECT expires_at FROM memoriesql.authentication_credentials WHERE tenant_id=c.tenant_id AND credential_id=c.credential_id)),
  budget,n,'executing');
 FOR item IN SELECT key,value FROM jsonb_each(request->'rows') LOOP
  IF item.key NOT IN ('assessed_relations','relation_statements','relation_evidence','relation_events',
   'relation_event_evidence','relation_types','relation_pairs','relation_corrections','relation_replacements',
   'observations','statements','statement_sources','source_units','corrections')
   OR jsonb_typeof(item.value)<>'array' THEN
   RAISE EXCEPTION 'invalid_query_population' USING ERRCODE='22023';
  END IF;
  INSERT INTO memoriesql_query.population_rows
   SELECT ref,item.key,ordinality,value FROM jsonb_array_elements(item.value) WITH ORDINALITY;
 END LOOP;
 IF (SELECT count(*) FROM jsonb_object_keys(request->'rows'))<>14 THEN
  RAISE EXCEPTION 'incomplete_query_population' USING ERRCODE='22023';
 END IF;
 RETURN jsonb_build_object('invocation_ref',ref,'deadline',(SELECT deadline FROM memoriesql_query.invocations WHERE invocation_ref=ref));
END $$;
REVOKE ALL ON FUNCTION memoriesql.stage_query_population_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.stage_query_population_v2(jsonb) TO memoriesql_application;

-- Exact registry catalog projections; hidden invocation and ordinal stay private.
CREATE VIEW memory_v1.observations WITH (security_barrier=true) AS SELECT
 (payload->>'bead_id')::uuid AS "bead_id",
 (payload->>'bead_version_id')::uuid AS "bead_version_id",
 (payload->>'event_id')::uuid AS "event_id",
 (payload->>'source_unit_id')::uuid AS "source_unit_id",
 (payload->>'bead_type_key')::text COLLATE "C" AS "bead_type_key",
 (payload->>'bead_type_revision_id')::uuid AS "bead_type_revision_id",
 (payload->>'title')::text COLLATE "C" AS "title",
 (payload->>'summary')::text COLLATE "C" AS "summary",
 (payload->>'detail')::text COLLATE "C" AS "detail",
 (payload->>'render_state')::text COLLATE "C" AS "render_state",
 (payload->>'recorded_at')::timestamptz AS "recorded_at",
 (payload->>'effective_at')::timestamptz AS "effective_at",
 (payload->>'effective_basis')::text COLLATE "C" AS "effective_basis",
 (payload->>'correction_state')::text COLLATE "C" AS "correction_state"
 FROM memoriesql_query.population_rows WHERE relation_name='observations';
ALTER VIEW memory_v1.observations OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.statements WITH (security_barrier=true) AS SELECT
 (payload->>'statement_id')::uuid AS "statement_id",
 (payload->>'bead_id')::uuid AS "bead_id",
 (payload->>'bead_version_id')::uuid AS "bead_version_id",
 (payload->>'sequence')::int8 AS "sequence",
 (payload->>'kind')::text COLLATE "C" AS "kind",
 (payload->>'text')::text COLLATE "C" AS "text",
 (payload->>'supersedes_statement_id')::uuid AS "supersedes_statement_id",
 (payload->>'correction_reason')::text COLLATE "C" AS "correction_reason",
 (payload->>'recorded_at')::timestamptz AS "recorded_at"
 FROM memoriesql_query.population_rows WHERE relation_name='statements';
ALTER VIEW memory_v1.statements OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.statement_sources WITH (security_barrier=true) AS SELECT
 (payload->>'statement_id')::uuid AS "statement_id",
 (payload->>'source_unit_id')::uuid AS "source_unit_id",
 (payload->>'event_id')::uuid AS "event_id",
 (payload->>'content_sha256')::text COLLATE "C" AS "content_sha256",
 (payload->>'evidence_ref')::uuid AS "evidence_ref"
 FROM memoriesql_query.population_rows WHERE relation_name='statement_sources';
ALTER VIEW memory_v1.statement_sources OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.source_units WITH (security_barrier=true) AS SELECT
 (payload->>'source_unit_id')::uuid AS "source_unit_id",
 (payload->>'content_sha256')::text COLLATE "C" AS "content_sha256",
 (payload->>'event_id')::uuid AS "event_id",
 (payload->>'source_object_id')::uuid AS "source_object_id",
 (payload->>'source_kind')::text COLLATE "C" AS "source_kind",
 (payload->>'package_revision_id')::uuid AS "package_revision_id",
 (payload->>'search_text')::text COLLATE "C" AS "search_text",
 (payload->>'text_state')::text COLLATE "C" AS "text_state",
 (payload->>'source_occurred_at')::timestamptz AS "source_occurred_at",
 (payload->>'source_time_original')::text COLLATE "C" AS "source_time_original",
 (payload->>'source_timezone')::text COLLATE "C" AS "source_timezone",
 (payload->>'source_precision')::text COLLATE "C" AS "source_precision",
 (payload->>'recorded_at')::timestamptz AS "recorded_at",
 (payload->>'actor_ref')::text COLLATE "C" AS "actor_ref",
 (payload->>'actor_role')::text COLLATE "C" AS "actor_role",
 (payload->>'trust_label')::text COLLATE "C" AS "trust_label",
 (payload->>'occurrence_ref')::uuid AS "occurrence_ref"
 FROM memoriesql_query.population_rows WHERE relation_name='source_units';
ALTER VIEW memory_v1.source_units OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.corrections WITH (security_barrier=true) AS SELECT
 (payload->>'predecessor_version_id')::uuid AS "predecessor_version_id",
 (payload->>'successor_version_id')::uuid AS "successor_version_id",
 (payload->>'predecessor_bead_id')::uuid AS "predecessor_bead_id",
 (payload->>'successor_bead_id')::uuid AS "successor_bead_id",
 (payload->>'reason')::text COLLATE "C" AS "reason",
 (payload->>'recorded_at')::timestamptz AS "recorded_at"
 FROM memoriesql_query.population_rows WHERE relation_name='corrections';
ALTER VIEW memory_v1.corrections OWNER TO memoriesql_query_view_owner;

-- Internal verdict for population-revision-2 results, within PR-03's session
-- authority fence and one RR frame. Reprojection under CURRENT authority at the
-- ORIGINAL cutoff and view is an oracle only; saved rows, counts and hashes never
-- change. One missing saved dependency refuses the whole result, aggregates too.
CREATE FUNCTION memoriesql.check_query_result_closure_v2(result_ref uuid,digest text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 r memoriesql.query_result_creations%ROWTYPE;
 ident memoriesql.query_result_identities%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE;
 body jsonb; graph jsonb; deps jsonb; saved_records jsonb; saved_manifest jsonb;
 rebuilt jsonb; current_population jsonb; current_manifest jsonb;
 manifest_text text; records_text text; artifact_hash text;
BEGIN
 IF result_ref IS NULL OR digest IS NULL OR digest !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO r FROM memoriesql.query_result_creations
  WHERE tenant_id=c.tenant_id AND result_id=result_ref FOR SHARE;
 SELECT * INTO ident FROM memoriesql.query_result_identities
  WHERE tenant_id=c.tenant_id AND result_id=result_ref FOR SHARE;
 IF r.result_id IS NULL OR ident.result_id IS NULL OR r.content_digest<>digest
  OR clock_timestamp()>=r.expires_at OR ident.workspace_id<>c.workspace_id
  OR ident.principal_id<>c.principal_id OR ident.principal_kind<>c.principal_kind
  OR ident.user_id IS DISTINCT FROM c.user_id
  OR ident.on_behalf_of_user_id IS DISTINCT FROM c.on_behalf_of_user_id
  OR ident.pairing_grant_id IS DISTINCT FROM c.pairing_grant_id THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND operation_ref=r.operation_ref;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts
  WHERE tenant_id=c.tenant_id AND artifact_ref=result_ref FOR SHARE;
 IF o.operation_ref IS NULL OR a.artifact_ref IS NULL OR o.state<>'sealed'
  OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id)
  OR encode(sha256(a.content_bytes),'hex')<>digest THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 artifact_hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
  int8send(octet_length(a.content_bytes)::bigint)||a.content_bytes||
  int8send(octet_length(a.witness_bytes)::bigint)||a.witness_bytes||
  int8send(octet_length(a.dependency_bytes)::bigint)||a.dependency_bytes),'hex');
 IF artifact_hash<>a.artifact_sha256 THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb;
 graph:=convert_from(a.witness_bytes,'UTF8')::jsonb;
 deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 IF body->>'result_id' IS DISTINCT FROM result_ref::text
  OR body->'population_revision' IS DISTINCT FROM '2'::jsonb
  OR body#>>'{frame,view}' NOT IN ('resolved','historical')
  OR body->>'witness_sha256' IS DISTINCT FROM encode(sha256(a.witness_bytes),'hex')
  OR graph->>'frame_ref' IS DISTINCT FROM body#>>'{frame,frame_ref}'
  OR body#>>'{query,run_ref}' IS DISTINCT FROM o.run_ref::text
  OR body#>>'{query,step_key}' IS DISTINCT FROM o.step_key::text
  OR body->>'query_fingerprint' IS DISTINCT FROM o.request_fingerprint
  OR jsonb_typeof(deps->'manifest_base64') IS DISTINCT FROM 'string'
  OR jsonb_typeof(deps->'records_base64') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 manifest_text:=convert_from(decode(deps->>'manifest_base64','base64'),'UTF8');
 records_text:=convert_from(decode(deps->>'records_base64','base64'),'UTF8');
 saved_manifest:=manifest_text::jsonb;
 saved_records:=records_text::jsonb;
 IF jsonb_typeof(saved_manifest)<>'array' OR jsonb_typeof(saved_records)<>'array'
  OR manifest_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(saved_manifest)
  OR records_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(saved_records)
  OR encode(sha256(convert_to(manifest_text,'UTF8')),'hex') IS DISTINCT FROM body#>>'{frame,projection_manifest_sha256}'
  OR body#>>'{frame,snapshot_digest}' IS DISTINCT FROM body#>>'{frame,projection_manifest_sha256}' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
  'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
  ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]'::jsonb)
 INTO rebuilt FROM jsonb_array_elements(saved_records) x(v);
 IF rebuilt IS DISTINCT FROM saved_manifest THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 current_population:=memoriesql.prepare_query_sql_population_v2(
  (body#>>'{frame,known_at}')::timestamptz,body#>>'{frame,view}',67108864);
 current_manifest:=(current_population->>'dependency_manifest_json')::jsonb;
 IF EXISTS(
  SELECT 1 FROM (
   SELECT value FROM jsonb_array_elements(saved_manifest)
   EXCEPT ALL
   SELECT value FROM jsonb_array_elements(current_manifest)
  ) missing
 ) THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.result_preparation_authority_v1();
 IF clock_timestamp()>=r.expires_at THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire
 OR invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION memoriesql.check_query_result_closure_v2(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.check_query_result_closure_v2(uuid,text) TO memoriesql_application;
