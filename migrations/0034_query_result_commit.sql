-- PR-05 private immutable bag results. No available result or disclosure API.
-- M31 remains the sole byte/hold/allocation store; M33 owns work/settlement.
CREATE TABLE memoriesql.query_result_creations (
 tenant_id uuid NOT NULL, operation_ref uuid NOT NULL, result_id uuid NOT NULL,
 invocation_ref uuid NOT NULL, receipt_ref uuid NOT NULL,
 content_digest text NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 publication_ms bigint NOT NULL CHECK(publication_ms>=0),
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,result_id), UNIQUE(tenant_id,operation_ref), UNIQUE(receipt_ref),
 UNIQUE(invocation_ref),
 FOREIGN KEY(tenant_id,result_id) REFERENCES memoriesql.result_preparation_artifacts(tenant_id,artifact_ref) ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,operation_ref) REFERENCES memoriesql.result_preparation_operations(tenant_id,operation_ref),
 FOREIGN KEY(invocation_ref) REFERENCES memoriesql_query.invocations(invocation_ref),
 CHECK(expires_at=created_at+interval '720 hours')
);
ALTER TABLE memoriesql.query_result_creations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_result_creations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_result_creations FROM PUBLIC,memoriesql_application;
CREATE TRIGGER query_result_creations_no_update BEFORE UPDATE ON memoriesql.query_result_creations
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.query_result_creation_receipt_v1(r memoriesql.query_result_creations,replayed boolean)
RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('state','committed','result_id',r.result_id,'content_digest',r.content_digest,
  'receipt_ref',r.receipt_ref,'invocation_ref',r.invocation_ref,
  'created_at',to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'expires_at',to_char(r.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'publication_ms',r.publication_ms,'replayed',replayed)
$$;
REVOKE ALL ON FUNCTION memoriesql.query_result_creation_receipt_v1(memoriesql.query_result_creations,boolean) FROM PUBLIC;

-- Set timeout on the NEXT client statement. Changing it from within an already
-- executing function does not provide independent enforcement for that query.
CREATE FUNCTION memoriesql.query_result_commit_budget_v1(op uuid,owner_ref uuid,invocation uuid)
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql,memoriesql_query SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; i memoriesql_query.invocations%ROWTYPE; remaining integer;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT q.* INTO i FROM memoriesql_query.invocations q JOIN memoriesql.result_preparation_operations o
  ON o.tenant_id=q.tenant_id AND o.operation_ref=q.operation_ref
  WHERE q.invocation_ref=invocation AND q.tenant_id=c.tenant_id AND q.workspace_id=c.workspace_id
   AND q.principal_id=c.principal_id AND q.ownership_ref=owner_ref AND q.operation_ref=op
   AND o.owner_user_id IS NOT DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) AND o.state<>'discarded';
 IF NOT FOUND THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 -- Committed redelivery is bounded bookkeeping, never another SELECT/grant.
 IF EXISTS(SELECT 1 FROM memoriesql.query_result_creations WHERE tenant_id=c.tenant_id AND operation_ref=op) THEN RETURN 2500; END IF;
 remaining:=floor(extract(epoch FROM(i.deadline-clock_timestamp()))*1000)::integer;
 IF remaining<1 THEN RAISE EXCEPTION 'query_result_work_exhausted' USING ERRCODE='54000'; END IF;
 RETURN LEAST(remaining,2500);
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_result_commit_budget_v1(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_result_commit_budget_v1(uuid,uuid,uuid) TO memoriesql_application;

CREATE FUNCTION memoriesql.commit_query_result_v1(op uuid,owner_ref uuid,invocation uuid,
 result_ref uuid,receipt uuid,fingerprint text,body bytea,witnesses bytea,dependencies bytea,
 artifact_hash text,digest text) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql,memoriesql_query SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 i memoriesql_query.invocations%ROWTYPE; r memoriesql.query_result_creations%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE; data jsonb; graph jsonb; deps jsonb; now_at timestamptz;
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
  IF r.result_id IS DISTINCT FROM result_ref OR r.invocation_ref IS DISTINCT FROM invocation
   OR r.receipt_ref IS DISTINCT FROM receipt OR r.content_digest IS DISTINCT FROM digest
   OR a.artifact_sha256 IS DISTINCT FROM artifact_hash OR a.content_bytes IS DISTINCT FROM body
   OR a.witness_bytes IS DISTINCT FROM witnesses OR a.dependency_bytes IS DISTINCT FROM dependencies THEN
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
  OR data#>>'{frame,projection_manifest_sha256}' IS DISTINCT FROM i.projection_manifest_sha256
  OR data->>'witness_sha256' IS DISTINCT FROM encode(sha256(witnesses),'hex')
  OR graph->>'qualification' IS DISTINCT FROM 'native-bag-v1' OR graph->>'frame_ref' IS DISTINCT FROM i.frame_ref::text
  OR graph->>'projection_manifest_sha256' IS DISTINCT FROM i.projection_manifest_sha256
  OR encode(sha256(decode(deps->>'manifest_base64','base64')),'hex') IS DISTINCT FROM i.projection_manifest_sha256
  OR jsonb_typeof(data->'rows') IS DISTINCT FROM 'array' OR jsonb_typeof(graph->'row_provenance') IS DISTINCT FROM 'array'
  OR jsonb_array_length(data->'rows')<>jsonb_array_length(graph->'row_provenance') THEN
  RAISE EXCEPTION 'invalid_query_result' USING ERRCODE='22023'; END IF;
 -- The trusted qualified compiler supplies complete partitions, not the agent.
 -- A failure after sealing rolls back bodies, holds, creation and receipt alike.
 PERFORM memoriesql.seal_result_preparation_v1(op,owner_ref,result_ref,body,witnesses,dependencies,artifact_hash,'[]'::jsonb);
 c:=memoriesql.result_preparation_authority_v1();
 now_at:=clock_timestamp();
 IF now_at>=i.deadline THEN RAISE EXCEPTION 'query_result_work_exhausted' USING ERRCODE='54000'; END IF;
 INSERT INTO memoriesql.query_result_creations VALUES(c.tenant_id,op,result_ref,invocation,receipt,digest,
  GREATEST(0,ceil(extract(epoch FROM (now_at-i.settled_at))*1000)::bigint),now_at,now_at+interval '720 hours') RETURNING * INTO r;
 RETURN memoriesql.query_result_creation_receipt_v1(r,false);
END $$;
REVOKE ALL ON FUNCTION memoriesql.commit_query_result_v1(uuid,uuid,uuid,uuid,uuid,text,bytea,bytea,bytea,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.commit_query_result_v1(uuid,uuid,uuid,uuid,uuid,text,bytea,bytea,bytea,text,text) TO memoriesql_application;

CREATE FUNCTION memoriesql.recover_query_result_v1(op uuid,owner_ref uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 r memoriesql.query_result_creations%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF NOT FOUND OR o.ownership_ref IS DISTINCT FROM owner_ref OR o.workspace_id<>c.workspace_id
  OR o.principal_id<>c.principal_id OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id)
  OR o.state='discarded' THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM memoriesql.query_result_creations WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF FOUND THEN
  PERFORM memoriesql.result_preparation_authority_v1();
  RETURN memoriesql.query_result_creation_receipt_v1(r,true);
 END IF;
 RETURN jsonb_build_object('state','settlement_pending');
END $$;
REVOKE ALL ON FUNCTION memoriesql.recover_query_result_v1(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.recover_query_result_v1(uuid,uuid) TO memoriesql_application;
