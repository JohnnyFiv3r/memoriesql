-- Saved-input-only refinement reuses the existing reader, invocation budget,
-- immutable result journal, authority and retention. No new role or capability.
ALTER TABLE memoriesql_query.invocations ADD COLUMN saved_input jsonb;

-- The artifact stays private to the definer. Its exact content digest,
-- context, expiry, owner identity and full closure are checked by existing v1.
CREATE FUNCTION memoriesql.saved_query_parent_v1(binding jsonb)
RETURNS memoriesql.result_preparation_artifacts
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 p memoriesql.result_preparation_artifacts%ROWTYPE;
 result_id uuid;
BEGIN
 IF jsonb_typeof(binding) IS DISTINCT FROM 'object'
  OR NOT (binding ?& ARRAY['alias','result','access'])
  OR binding-ARRAY['alias','result','access']<>'{}'::jsonb
  OR COALESCE(binding->>'alias','') !~ '^[a-z][a-z0-9_]{0,31}$'
  OR jsonb_typeof(binding->'result') IS DISTINCT FROM 'object'
  OR NOT ((binding->'result') ?& ARRAY['result_id','content_digest'])
  OR (binding->'result')-ARRAY['result_id','content_digest']<>'{}'::jsonb THEN
  RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023'; END IF;
 result_id:=(binding#>>'{result,result_id}')::uuid;
 PERFORM memoriesql.query_result_access_v1(result_id,binding#>>'{result,content_digest}',binding->'access');
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO STRICT p FROM memoriesql.result_preparation_artifacts
  WHERE tenant_id=c.tenant_id AND artifact_ref=result_id;
 RETURN p;
EXCEPTION WHEN insufficient_privilege OR no_data_found OR invalid_text_representation THEN
 RAISE EXCEPTION 'query_saved_input_unavailable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION memoriesql.saved_query_parent_v1(jsonb) FROM PUBLIC,memoriesql_application;

-- A standalone saved child uses its sealed canonical closure. Keep the live
-- standalone verifier at its existing behavior and grant boundary.
ALTER FUNCTION memoriesql.check_query_result_closure_v2(uuid,text) RENAME TO check_live_query_result_closure_v2;
REVOKE ALL ON FUNCTION memoriesql.check_live_query_result_closure_v2(uuid,text) FROM PUBLIC,memoriesql_application;

CREATE FUNCTION memoriesql.check_query_result_closure_v2(result_ref uuid,digest text) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF EXISTS(SELECT 1 FROM memoriesql.query_result_creations r
  JOIN memoriesql_query.invocations i USING (tenant_id,invocation_ref)
  WHERE r.tenant_id=c.tenant_id AND r.result_id=result_ref AND i.saved_input IS NOT NULL) THEN
  RETURN memoriesql.check_query_result_bytes_v1(result_ref,digest,false);
 END IF;
 RETURN memoriesql.check_live_query_result_closure_v2(result_ref,digest);
END $$;
REVOKE ALL ON FUNCTION memoriesql.check_query_result_closure_v2(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.check_query_result_closure_v2(uuid,text) TO memoriesql_application;

-- Preserve the existing inspection and raw-source checks. The base function
-- has already selected and authorized the exact result in the caller's access
-- context by the time the immutable result metadata supplies its parents.
ALTER FUNCTION memoriesql.prepare_query_inspection_v1(uuid,jsonb) RENAME TO prepare_live_query_inspection_v1;
REVOKE ALL ON FUNCTION memoriesql.prepare_live_query_inspection_v1(uuid,jsonb) FROM PUBLIC,memoriesql_application;

CREATE FUNCTION memoriesql.prepare_query_inspection_v1(delivery uuid,request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE packet jsonb; parents jsonb; pin uuid; c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 packet:=memoriesql.prepare_live_query_inspection_v1(delivery,request);
 pin:=(packet#>>'{content,payload,inspection,result,result_id}')::uuid;
 IF pin IS NULL THEN RETURN packet; END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT (convert_from(a.content_bytes,'UTF8')::jsonb)->'parents' INTO STRICT parents
 FROM memoriesql.result_preparation_artifacts a WHERE a.tenant_id=c.tenant_id AND a.artifact_ref=pin;
 RETURN jsonb_set(packet,'{content,payload,inspection,result,parents}',parents,false);
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_inspection_v1(uuid,jsonb) FROM PUBLIC,memoriesql_application;

-- Keep the already-qualified live path. The saved branch opens exactly the
-- same owned invocation with an empty staging map and an exact saved-input pin.
ALTER FUNCTION memoriesql.stage_query_population_v2(jsonb) RENAME TO stage_live_query_population_v2;
REVOKE ALL ON FUNCTION memoriesql.stage_live_query_population_v2(jsonb) FROM PUBLIC,memoriesql_application;

CREATE FUNCTION memoriesql.stage_query_population_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 p memoriesql.result_preparation_artifacts%ROWTYPE;
 data jsonb; answer jsonb; bytes bigint;
BEGIN
 IF NOT (request ? 'saved_input') THEN RETURN memoriesql.stage_live_query_population_v2(request); END IF;
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
 p:=memoriesql.saved_query_parent_v1(request->'saved_input');
 data:=convert_from(p.content_bytes,'UTF8')::jsonb;
 IF request->'rows' IS DISTINCT FROM '{}'::jsonb
  OR request->>'catalog_hash' IS DISTINCT FROM data->>'catalog_hash'
  OR data->>'serializer' IS DISTINCT FROM 'result-json-v1'
  OR data->>'provenance_revision' IS DISTINCT FROM 'native-bag-v1'
  OR request->>'frame_ref' IS DISTINCT FROM data#>>'{wire_frame,frame_ref}'
  OR (request->>'known_at')::timestamptz IS DISTINCT FROM (data#>>'{wire_frame,known_at}')::timestamptz
  OR (request->>'snapshot_at')::timestamptz IS DISTINCT FROM (data#>>'{wire_frame,snapshot_at}')::timestamptz
  OR request->>'scope_hash' IS DISTINCT FROM data#>>'{wire_frame,snapshot_digest}'
  OR request->>'projection_manifest_sha256' IS DISTINCT FROM data#>>'{wire_frame,snapshot_digest}' THEN
  RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023'; END IF;
 bytes:=GREATEST((request->>'saved_input_bytes')::bigint,8192+octet_length((data->'rows')::text));
 IF bytes>COALESCE((SELECT reservation_bytes FROM memoriesql.result_preparation_operations
     WHERE tenant_id=c.tenant_id AND operation_ref=(request->>'operation_ref')::uuid),0) THEN
  RAISE EXCEPTION 'query_stage_budget_exhausted' USING ERRCODE='54000'; END IF;
 answer:=memoriesql.stage_live_query_population_v2(jsonb_set(request,'{rows}', '{
  "assessed_relations":[],"relation_statements":[],"relation_evidence":[],"relation_events":[],
  "relation_event_evidence":[],"relation_types":[],"relation_pairs":[],"relation_corrections":[],
  "relation_replacements":[],"observations":[],"statements":[],"statement_sources":[],"source_units":[],"corrections":[]
 }'::jsonb));
 UPDATE memoriesql_query.invocations SET saved_input=request->'saved_input',encoded_stage_bytes=bytes
  WHERE invocation_ref=(answer->>'invocation_ref')::uuid;
 RETURN answer;
END $$;
REVOKE ALL ON FUNCTION memoriesql.stage_query_population_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.stage_query_population_v2(jsonb) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.commit_query_result_v1(op uuid,owner_ref uuid,invocation uuid,
 result_ref uuid,receipt uuid,fingerprint text,body bytea,witnesses bytea,dependencies bytea,
 artifact_hash text,digest text) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql,memoriesql_query SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 i memoriesql_query.invocations%ROWTYPE; r memoriesql.query_result_creations%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE; data jsonb; graph jsonb; deps jsonb; now_at timestamptz;
 p memoriesql.result_preparation_artifacts%ROWTYPE; parent_data jsonb; parents jsonb:='[]'::jsonb;
 source_scopes uuid[];
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF NOT FOUND OR o.ownership_ref IS DISTINCT FROM owner_ref OR o.workspace_id<>c.workspace_id
  OR o.principal_id<>c.principal_id OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id)
  OR o.state='discarded' THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 IF fingerprint IS NULL OR o.request_fingerprint IS DISTINCT FROM fingerprint THEN
  RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||o.run_ref::text||':'||o.step_key::text,0));
 SELECT * INTO r FROM memoriesql.query_result_creations WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF FOUND THEN
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id AND artifact_ref=r.result_id;
  SELECT * INTO i FROM memoriesql_query.invocations WHERE invocation_ref=r.invocation_ref;
  IF r.result_id IS DISTINCT FROM result_ref OR r.invocation_ref IS DISTINCT FROM invocation
   OR r.receipt_ref IS DISTINCT FROM receipt OR r.content_digest IS DISTINCT FROM digest
   OR a.content_bytes IS DISTINCT FROM body OR a.witness_bytes IS DISTINCT FROM witnesses
   OR (i.saved_input IS NULL
     AND (a.artifact_sha256 IS DISTINCT FROM artifact_hash OR a.dependency_bytes IS DISTINCT FROM dependencies))
   OR (i.saved_input IS NOT NULL AND (dependencies IS DISTINCT FROM convert_to(
        memoriesql.lifecycle_canonical_json_v1(jsonb_build_object('witness_sha256',encode(sha256(witnesses),'hex'))),'UTF8')
     OR artifact_hash IS DISTINCT FROM encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
       int8send(octet_length(body)::bigint)||body||int8send(octet_length(witnesses)::bigint)||witnesses||
       int8send(octet_length(dependencies)::bigint)||dependencies),'hex'))) THEN
   RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
  PERFORM memoriesql.result_preparation_authority_v1();
  RETURN memoriesql.query_result_creation_receipt_v1(r,true);
 END IF;
 SELECT * INTO i FROM memoriesql_query.invocations WHERE invocation_ref=invocation;
 IF NOT FOUND OR i.tenant_id<>c.tenant_id OR i.workspace_id<>c.workspace_id OR i.principal_id<>c.principal_id
  OR i.credential_id<>c.credential_id OR i.operation_ref<>op OR i.ownership_ref<>owner_ref
  OR i.state<>'settled' OR i.outcome IS DISTINCT FROM 'complete' OR i.observed_ms IS NULL
  OR NOT EXISTS(SELECT 1 FROM pg_stat_activity issuer WHERE issuer.pid=i.issuer_pid
    AND issuer.backend_start=i.issuer_start AND issuer.datid=(SELECT oid FROM pg_database WHERE datname=current_database())
    AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=issuer.pid AND locktype='virtualxid' AND granted AND virtualxid=i.issuer_transaction)) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 IF clock_timestamp()>=i.deadline THEN RAISE EXCEPTION 'query_result_work_exhausted' USING ERRCODE='54000'; END IF;
 IF body IS NULL OR witnesses IS NULL OR dependencies IS NULL OR result_ref IS NULL OR receipt IS NULL
  OR digest IS NULL OR digest !~ '^[a-f0-9]{64}$' OR encode(sha256(body),'hex')<>digest THEN
  RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;
 data:=convert_from(body,'UTF8')::jsonb; graph:=convert_from(witnesses,'UTF8')::jsonb; deps:=convert_from(dependencies,'UTF8')::jsonb;
 IF data->>'serializer' IS DISTINCT FROM 'result-json-v1' OR data->>'provenance_revision' IS DISTINCT FROM 'native-bag-v1'
  OR data->>'result_id' IS DISTINCT FROM result_ref::text OR data->>'query_fingerprint' IS DISTINCT FROM fingerprint
  OR data#>>'{query,run_ref}' IS DISTINCT FROM o.run_ref::text OR data#>>'{query,step_key}' IS DISTINCT FROM o.step_key::text
  OR data->>'catalog_hash' IS DISTINCT FROM i.catalog_hash OR data->>'policy_hash' IS DISTINCT FROM i.policy_hash
  OR data#>>'{frame,frame_ref}' IS DISTINCT FROM i.frame_ref::text
  OR (data#>>'{frame,known_at}')::timestamptz IS DISTINCT FROM i.known_at
  OR (data#>>'{frame,snapshot_at}')::timestamptz IS DISTINCT FROM i.snapshot_at
  OR (data->'population_revision' IS DISTINCT FROM '2'::jsonb AND (
   data#>>'{frame,projection_manifest_sha256}' IS DISTINCT FROM i.projection_manifest_sha256
   OR data->>'witness_sha256' IS DISTINCT FROM encode(sha256(witnesses),'hex')))
  OR (i.saved_input IS NULL AND data#>>'{query,intent}'='refine')
  OR (i.saved_input IS NULL AND data->'population_revision'='2'::jsonb AND (
   data->'witness_sha256' IS NOT NULL OR data#>'{frame,snapshot_digest}' IS NOT NULL
   OR data#>'{frame,projection_manifest_sha256}' IS NOT NULL
   OR deps#>>'{protected_frame,snapshot_digest}' IS DISTINCT FROM i.projection_manifest_sha256
   OR deps#>>'{protected_frame,projection_manifest_sha256}' IS DISTINCT FROM i.projection_manifest_sha256
   OR deps->>'witness_sha256' IS DISTINCT FROM encode(sha256(witnesses),'hex')))
  OR graph->>'qualification' IS DISTINCT FROM 'native-bag-v1' OR graph->>'frame_ref' IS DISTINCT FROM i.frame_ref::text
  OR graph->>'projection_manifest_sha256' IS DISTINCT FROM i.projection_manifest_sha256
  OR (i.saved_input IS NULL AND encode(sha256(decode(deps->>'manifest_base64','base64')),'hex')
   IS DISTINCT FROM i.projection_manifest_sha256)
  OR jsonb_typeof(data->'rows') IS DISTINCT FROM 'array' OR jsonb_typeof(graph->'row_provenance') IS DISTINCT FROM 'array'
  OR jsonb_array_length(data->'rows')<>jsonb_array_length(graph->'row_provenance') THEN
  RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;
 IF i.saved_input IS NOT NULL THEN
  IF data->'population_revision' IS DISTINCT FROM '2'::jsonb
   OR data#>>'{query,intent}' IS DISTINCT FROM 'refine'
   OR data#>'{query,inputs}' IS DISTINCT FROM jsonb_build_array(i.saved_input)
   OR data#>'{query,parents}' IS DISTINCT FROM jsonb_build_array(i.saved_input-'alias')
   OR data->'parents' IS DISTINCT FROM jsonb_build_array((i.saved_input->'result')||'{"edge":"refine"}'::jsonb)
   OR data#>'{admitted_program,relations}' IS DISTINCT FROM jsonb_build_array('input.'||(i.saved_input->>'alias'))
   OR data->'witness_sha256' IS NOT NULL OR data#>'{frame,snapshot_digest}' IS NOT NULL
   OR data#>'{frame,projection_manifest_sha256}' IS NOT NULL
   OR deps IS DISTINCT FROM jsonb_build_object('witness_sha256',encode(sha256(witnesses),'hex')) THEN
   RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;

  -- Take the workspace allocator/retention lock before locking the parent's rows.
  -- In the commit transaction, the original explicit access context must still
  -- admit the parent (standalone expiry and checkpoint expiry both apply here).
  PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
  p:=memoriesql.saved_query_parent_v1(i.saved_input);
  parent_data:=convert_from(p.content_bytes,'UTF8')::jsonb;
  IF data->'wire_frame' IS DISTINCT FROM parent_data->'wire_frame'
   OR data#>'{query,scope,source_refs}' IS DISTINCT FROM parent_data#>'{query,scope,source_refs}'
   OR data#>>'{query,scope,view}' IS DISTINCT FROM parent_data#>>'{frame,view}'
   OR (data#>>'{query,scope,known_at}')::timestamptz IS DISTINCT FROM i.known_at
   OR jsonb_typeof(graph->'members') IS DISTINCT FROM 'array'
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(graph->'members') m(member)
     WHERE member#>>'{parent_fact_ref,result_id}' IS DISTINCT FROM p.artifact_ref::text
      OR member->>'relation' IS DISTINCT FROM 'input.'||(i.saved_input->>'alias')
      OR member->'key_values' IS DISTINCT FROM jsonb_build_array(member#>'{parent_fact_ref,row_ref}')
      OR CASE WHEN COALESCE(member#>>'{parent_fact_ref,row_ref}','') ~ '^[1-9][0-9]{0,9}$'
       THEN (member#>>'{parent_fact_ref,row_ref}')::bigint > jsonb_array_length(parent_data->'rows')
       ELSE true END) THEN
   RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;

  parents:=jsonb_build_array(jsonb_build_object('artifact_ref',p.artifact_ref,'artifact_sha256',p.artifact_sha256));
  -- Keep the parent's complete canonical dependency records inside PostgreSQL.
  -- Result-scoped evidence handles are not reused by the child. The existing
  -- seal charges all stored bytes and the parent hold to the owned reservation.
  deps:=(convert_from(p.dependency_bytes,'UTF8')::jsonb||deps)
    ||jsonb_build_object('evidence_bindings','{}'::jsonb);
  -- The closure above proves the exact records under the authority fence.
  -- Resolve their original event scopes for a fresh lease check at seal.
  SELECT array_agg(DISTINCT e.access_scope_id) INTO source_scopes
   FROM jsonb_array_elements(convert_from(decode(deps->>'records_base64','base64'),'UTF8')::jsonb) d(pin)
   LEFT JOIN memoriesql.source_events e ON e.tenant_id=c.tenant_id AND e.workspace_id=c.workspace_id
    AND e.event_id=CASE WHEN pin->>'kind'='source_event' THEN (pin->>'id')::uuid END
   WHERE pin->>'kind'='source_event';
  dependencies:=convert_to(memoriesql.lifecycle_canonical_json_v1(deps),'UTF8');
  artifact_hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
   int8send(octet_length(body)::bigint)||body||int8send(octet_length(witnesses)::bigint)||witnesses||
   int8send(octet_length(dependencies)::bigint)||dependencies),'hex');
 END IF;
 -- The trusted qualified compiler supplies complete partitions, not the agent.
 -- A failure after sealing rolls back bodies, holds, creation and receipt alike.
 PERFORM memoriesql.seal_result_preparation_v1(op,owner_ref,result_ref,body,witnesses,dependencies,artifact_hash,parents);
 c:=memoriesql.result_preparation_authority_v1();
 now_at:=clock_timestamp();
 IF i.saved_input IS NOT NULL AND (
  memoriesql.query_result_context_live_v1(
   p.artifact_ref,i.saved_input#>>'{result,content_digest}',i.saved_input->'access') IS DISTINCT FROM true
  OR EXISTS(SELECT 1 FROM unnest(source_scopes) s(scope_id)
   WHERE memoriesql.current_context_scope_time_authorized(s.scope_id,'read',now_at) IS DISTINCT FROM true)
 ) THEN RAISE EXCEPTION 'query_saved_input_unavailable' USING ERRCODE='42501'; END IF;
 IF now_at>=i.deadline THEN RAISE EXCEPTION 'query_result_work_exhausted' USING ERRCODE='54000'; END IF;
 INSERT INTO memoriesql.query_result_creations VALUES(c.tenant_id,op,result_ref,invocation,receipt,digest,
  GREATEST(0,ceil(extract(epoch FROM (now_at-i.settled_at))*1000)::bigint),now_at,now_at+interval '720 hours') RETURNING * INTO r;
 RETURN memoriesql.query_result_creation_receipt_v1(r,false);
END $$;
