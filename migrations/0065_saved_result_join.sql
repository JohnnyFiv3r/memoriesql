-- Same-frame saved-result joins reuse the restricted reader and the owned
-- result allocator. Old invocations still store the exact single-binding object.
CREATE FUNCTION memoriesql.saved_query_inputs_v1(payload jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF jsonb_typeof(payload)='object' THEN RETURN jsonb_build_array(payload); END IF;
 IF jsonb_typeof(payload) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023'; END IF;
 IF jsonb_array_length(payload)<>2 OR EXISTS(
   SELECT 1 FROM jsonb_array_elements(payload) input(b)
   WHERE NOT COALESCE((b#>>'{result,result_id}') ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',false)
  ) OR (SELECT count(DISTINCT b->>'alias')<>2 OR count(DISTINCT b#>>'{result,result_id}')<>2
     FROM jsonb_array_elements(payload) input(b)) THEN
  RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023'; END IF;
 RETURN payload;
END $$;
REVOKE ALL ON FUNCTION memoriesql.saved_query_inputs_v1(jsonb) FROM PUBLIC,memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.stage_query_population_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 p memoriesql.result_preparation_artifacts%ROWTYPE;
 binding jsonb; parent_frame jsonb; data jsonb; answer jsonb; bytes bigint:=8192;
BEGIN
 IF NOT (request ? 'saved_input') THEN RETURN memoriesql.stage_live_query_population_v2(request); END IF;
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
 FOR binding IN SELECT value FROM jsonb_array_elements(memoriesql.saved_query_inputs_v1(request->'saved_input')) LOOP
  p:=memoriesql.saved_query_parent_v1(binding);
  data:=convert_from(p.content_bytes,'UTF8')::jsonb;
  IF request->'rows' IS DISTINCT FROM '{}'::jsonb
   OR request->>'catalog_hash' IS DISTINCT FROM data->>'catalog_hash'
   OR data->>'serializer' IS DISTINCT FROM 'result-json-v1'
   OR data->>'provenance_revision' IS DISTINCT FROM 'native-bag-v1'
   OR request->>'frame_ref' IS DISTINCT FROM data#>>'{wire_frame,frame_ref}'
   OR (request->>'known_at')::timestamptz IS DISTINCT FROM (data#>>'{wire_frame,known_at}')::timestamptz
   OR (request->>'snapshot_at')::timestamptz IS DISTINCT FROM (data#>>'{wire_frame,snapshot_at}')::timestamptz
   OR request->>'scope_hash' IS DISTINCT FROM data#>>'{wire_frame,snapshot_digest}'
   OR request->>'projection_manifest_sha256' IS DISTINCT FROM data#>>'{wire_frame,snapshot_digest}'
   OR (parent_frame IS NOT NULL AND parent_frame IS DISTINCT FROM data->'wire_frame') THEN
   RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023'; END IF;
  parent_frame:=data->'wire_frame';
  bytes:=bytes+octet_length((data->'rows')::text);
 END LOOP;
 bytes:=GREATEST((request->>'saved_input_bytes')::bigint,bytes);
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
CREATE OR REPLACE FUNCTION memoriesql.commit_query_result_v1(op uuid,owner_ref uuid,invocation uuid,
 result_ref uuid,receipt uuid,fingerprint text,body bytea,witnesses bytea,dependencies bytea,
 artifact_hash text,digest text) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql,memoriesql_query SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 i memoriesql_query.invocations%ROWTYPE; r memoriesql.query_result_creations%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE; data jsonb; graph jsonb; deps jsonb; now_at timestamptz;
 p memoriesql.result_preparation_artifacts%ROWTYPE; parent_data jsonb; parents jsonb:='[]'::jsonb;
 binding jsonb; bindings jsonb; expected_access jsonb; expected_parents jsonb;
 relations jsonb; common_dependencies jsonb; parent_dependencies jsonb;
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
  bindings:=memoriesql.saved_query_inputs_v1(i.saved_input);
  SELECT jsonb_agg(b-'alias' ORDER BY b#>>'{result,result_id}'),
   jsonb_agg('input.'||(b->>'alias') ORDER BY (b->>'alias') COLLATE "C")
   INTO expected_access,relations FROM jsonb_array_elements(bindings) input(b);
  IF data->'population_revision' IS DISTINCT FROM '2'::jsonb
   OR data#>>'{query,intent}' IS DISTINCT FROM 'refine'
   OR data#>'{query,inputs}' IS DISTINCT FROM bindings
   OR jsonb_typeof(data#>'{query,parents}') IS DISTINCT FROM 'array'
   OR (SELECT jsonb_agg(entry ORDER BY entry#>>'{result,result_id}')
       FROM jsonb_array_elements(data#>'{query,parents}') parent(entry)) IS DISTINCT FROM expected_access
   OR data#>'{admitted_program,relations}' IS DISTINCT FROM relations
   OR (jsonb_array_length(bindings)=2 AND graph->'searched_relations' IS DISTINCT FROM relations)
   OR data->'witness_sha256' IS NOT NULL OR data#>'{frame,snapshot_digest}' IS NOT NULL
   OR data#>'{frame,projection_manifest_sha256}' IS NOT NULL
   OR deps IS DISTINCT FROM jsonb_build_object('witness_sha256',encode(sha256(witnesses),'hex'))
   OR (data#>>'{query,scope,known_at}')::timestamptz IS DISTINCT FROM i.known_at
   OR jsonb_typeof(graph->'members') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;

  SELECT jsonb_agg((entry->'result')||jsonb_build_object('edge',
   CASE WHEN jsonb_array_length(bindings)=1 THEN 'refine' ELSE 'join' END) ORDER BY ordinality)
   INTO expected_parents FROM jsonb_array_elements(data#>'{query,parents}') WITH ORDINALITY parent(entry,ordinality);
  IF data->'parents' IS DISTINCT FROM expected_parents OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(graph->'members') m(member)
    WHERE ((relations ? (member->>'relation')) IS DISTINCT FROM true)
  ) THEN RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;

  -- Lock the workspace's retention allocator first and reauthorize the exact
  -- pin and explicit access context of every parent under the existing fence.
  PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
  FOR binding IN SELECT value FROM jsonb_array_elements(bindings) LOOP
   p:=memoriesql.saved_query_parent_v1(binding);
   parent_data:=convert_from(p.content_bytes,'UTF8')::jsonb;
   IF data->'wire_frame' IS DISTINCT FROM parent_data->'wire_frame'
    OR data#>'{query,scope,source_refs}' IS DISTINCT FROM parent_data#>'{query,scope,source_refs}'
    OR data#>>'{query,scope,view}' IS DISTINCT FROM parent_data#>>'{frame,view}'
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(graph->'members') m(member)
      WHERE member->>'relation'='input.'||(binding->>'alias') AND (
       member#>>'{parent_fact_ref,result_id}' IS DISTINCT FROM p.artifact_ref::text
       OR member->'key_values' IS DISTINCT FROM jsonb_build_array(member#>'{parent_fact_ref,row_ref}')
       OR CASE WHEN COALESCE(member#>>'{parent_fact_ref,row_ref}','') ~ '^[1-9][0-9]{0,9}$'
        THEN (member#>>'{parent_fact_ref,row_ref}')::bigint > jsonb_array_length(parent_data->'rows')
        ELSE true END)) THEN
    RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;
   parents:=parents||jsonb_build_array(jsonb_build_object('artifact_ref',p.artifact_ref,'artifact_sha256',p.artifact_sha256));

   -- Same-frame refinements descend from one whole canonical population.
   -- Share one exact protected partition. Per-result witnesses and evidence
   -- handles are deliberately excluded from this equivalence and inheritance.
   parent_dependencies:=convert_from(p.dependency_bytes,'UTF8')::jsonb
    -ARRAY['witness_sha256','evidence_bindings'];
   IF common_dependencies IS NOT NULL AND common_dependencies IS DISTINCT FROM parent_dependencies THEN
    RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;
   common_dependencies:=parent_dependencies;
  END LOOP;
  deps:=common_dependencies||deps||jsonb_build_object('evidence_bindings','{}'::jsonb);
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
 IF bindings IS NOT NULL AND (
  EXISTS(SELECT 1 FROM jsonb_array_elements(bindings) input(b)
   WHERE memoriesql.query_result_context_live_v1(
    (b#>>'{result,result_id}')::uuid,b#>>'{result,content_digest}',b->'access') IS DISTINCT FROM true)
  OR EXISTS(SELECT 1 FROM unnest(source_scopes) s(scope_id)
   WHERE memoriesql.current_context_scope_time_authorized(s.scope_id,'read',now_at) IS DISTINCT FROM true)
 ) THEN RAISE EXCEPTION 'query_saved_input_unavailable' USING ERRCODE='42501'; END IF;
 IF now_at>=i.deadline THEN RAISE EXCEPTION 'query_result_work_exhausted' USING ERRCODE='54000'; END IF;
 INSERT INTO memoriesql.query_result_creations VALUES(c.tenant_id,op,result_ref,invocation,receipt,digest,
  GREATEST(0,ceil(extract(epoch FROM (now_at-i.settled_at))*1000)::bigint),now_at,now_at+interval '720 hours') RETURNING * INTO r;
 RETURN memoriesql.query_result_creation_receipt_v1(r,false);
END $$;
