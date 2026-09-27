-- PR-05 private durable preparation, not an available result or disclosure API.
-- M0030 owns canonical lifecycle and its authority/snapshot fence.
-- These bytes are sealed for a future qualified executor. Encoding charges are
-- explicitly not physical-storage qualification or cumulative execution work.
CREATE TABLE memoriesql.result_preparation_allocators (
 tenant_id uuid NOT NULL, workspace_id uuid NOT NULL, revision bigint NOT NULL,
 PRIMARY KEY(tenant_id,workspace_id),
 FOREIGN KEY(tenant_id,workspace_id) REFERENCES memoriesql.workspaces(tenant_id,workspace_id)
);
CREATE TABLE memoriesql.result_preparation_operations (
 tenant_id uuid NOT NULL, workspace_id uuid NOT NULL, principal_id uuid NOT NULL,
 owner_user_id uuid, run_ref uuid NOT NULL, step_key uuid NOT NULL,
 operation_ref uuid NOT NULL, ownership_ref uuid NOT NULL,
 request_fingerprint text CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
 state text NOT NULL CHECK(state IN ('reserved','sealed','discarded')),
 reservation_bytes bigint NOT NULL CHECK(reservation_bytes BETWEEN 8192 AND 67108864),
 encoded_bytes bigint NOT NULL DEFAULT 0 CHECK(encoded_bytes>=0),
 allocation_bytes bigint NOT NULL DEFAULT 0 CHECK(allocation_bytes>=0),
 new_allocation_bytes bigint NOT NULL DEFAULT 8192 CHECK(new_allocation_bytes>=8192),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,operation_ref), UNIQUE(tenant_id,run_ref,step_key),
 FOREIGN KEY(tenant_id,workspace_id,principal_id)
  REFERENCES memoriesql.workspace_memberships(tenant_id,workspace_id,principal_id),
 CHECK((state='discarded')=(request_fingerprint IS NULL)),
 CHECK(allocation_bytes<=reservation_bytes)
);
CREATE TABLE memoriesql.result_preparation_artifacts (
 tenant_id uuid NOT NULL, artifact_ref uuid NOT NULL, operation_ref uuid NOT NULL,
 artifact_sha256 text NOT NULL CHECK(artifact_sha256 ~ '^[a-f0-9]{64}$'),
 content_bytes bytea NOT NULL, witness_bytes bytea NOT NULL, dependency_bytes bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,artifact_ref), UNIQUE(tenant_id,operation_ref),
 FOREIGN KEY(tenant_id,operation_ref)
  REFERENCES memoriesql.result_preparation_operations(tenant_id,operation_ref)
);
CREATE TABLE memoriesql.result_preparation_parent_holds (
 tenant_id uuid NOT NULL, child_ref uuid NOT NULL, parent_ref uuid NOT NULL,
 parent_sha256 text NOT NULL, ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 8),
 PRIMARY KEY(tenant_id,child_ref,parent_ref), UNIQUE(tenant_id,child_ref,ordinal),
 FOREIGN KEY(tenant_id,child_ref)
  REFERENCES memoriesql.result_preparation_artifacts(tenant_id,artifact_ref),
 FOREIGN KEY(tenant_id,parent_ref)
  REFERENCES memoriesql.result_preparation_artifacts(tenant_id,artifact_ref),
 CHECK(child_ref<>parent_ref)
);
CREATE INDEX result_preparation_parent_holds_parent
 ON memoriesql.result_preparation_parent_holds(tenant_id,parent_ref);
ALTER TABLE memoriesql.result_preparation_allocators ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_allocators FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_operations FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_parent_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.result_preparation_parent_holds FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.result_preparation_allocators,memoriesql.result_preparation_operations,
 memoriesql.result_preparation_artifacts,memoriesql.result_preparation_parent_holds
 FROM PUBLIC,memoriesql_application;
-- The only deletion route is the private owned discard, never a result erasure
-- substitute. Future governed erasure must invalidate descendants and purge its
-- full protected closure; it cannot be implemented by mutating these bodies.
CREATE TRIGGER result_preparation_artifacts_no_update
 BEFORE UPDATE ON memoriesql.result_preparation_artifacts
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER result_preparation_parent_holds_no_update
 BEFORE UPDATE ON memoriesql.result_preparation_parent_holds
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.result_preparation_authority_v1()
RETURNS memoriesql.authorization_contexts LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF c.context_id IS NULL OR NOT memoriesql.relation_current_authority_v1(c.tenant_id)
  OR NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id) THEN
  RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN c;
END $$;
REVOKE ALL ON FUNCTION memoriesql.result_preparation_authority_v1() FROM PUBLIC;

CREATE FUNCTION memoriesql.result_preparation_receipt_v1(
 o memoriesql.result_preparation_operations,replayed boolean
) RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('operation_ref',o.operation_ref,'ownership_ref',o.ownership_ref,
  'state',o.state,'reservation_bytes',o.reservation_bytes,'encoded_bytes',o.encoded_bytes,
  'allocation_bytes',o.allocation_bytes,'replayed',replayed)
$$;
REVOKE ALL ON FUNCTION memoriesql.result_preparation_receipt_v1(memoriesql.result_preparation_operations,boolean) FROM PUBLIC;

CREATE FUNCTION memoriesql.reserve_result_preparation_v1(
 run_id uuid,step_id uuid,fingerprint text,capacity bigint
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; used bigint; run_used bigint;
BEGIN
 IF run_id IS NULL OR step_id IS NULL OR fingerprint IS NULL OR fingerprint !~ '^[a-f0-9]{64}$'
  OR capacity IS NULL OR capacity NOT BETWEEN 8192 AND 67108864 THEN
  RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||run_id::text||':'||step_id::text,0));
 -- One allocation lock covers reservations, sealing and discarding across runs.
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation:'||c.workspace_id::text,0));
 -- A changed allocator row makes a pre-wait RR snapshot serialize-fail, rather
 -- than using stale sums after acquiring the advisory lock. Recover/retry the
 -- same private command in a NEW authority-fenced snapshot; never execute SQL.
 INSERT INTO memoriesql.result_preparation_allocators VALUES(c.tenant_id,c.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND run_ref=run_id AND step_key=step_id;
 IF FOUND THEN
  IF o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
   OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) THEN
   RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501';
  END IF;
  -- Cleanup removes the protected binding digest. A discarded key is never a
  -- reservation replay or new execution grant, even for the original inputs.
  IF o.state='discarded' THEN
   RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501';
  END IF;
  IF o.request_fingerprint<>fingerprint OR o.reservation_bytes<>capacity THEN
   RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505';
  END IF;
  RETURN memoriesql.result_preparation_receipt_v1(o,true);
 END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id
  AND run_ref=run_id AND (workspace_id<>c.workspace_id OR principal_id<>c.principal_id
   OR owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id))) THEN
  RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes ELSE allocation_bytes END),0),
  COALESCE(sum(CASE WHEN run_ref=run_id THEN CASE WHEN state='reserved' THEN reservation_bytes ELSE new_allocation_bytes END ELSE 0 END),0)
 INTO used,run_used FROM memoriesql.result_preparation_operations
 WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id;
 IF used+capacity>536870912 OR run_used+capacity>134217728 THEN
  RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000';
 END IF;
 -- Opaque journal ownership is allocated once and is never taken over on retry.
 INSERT INTO memoriesql.result_preparation_operations(
  tenant_id,workspace_id,principal_id,owner_user_id,run_ref,step_key,operation_ref,
  ownership_ref,request_fingerprint,state,reservation_bytes)
 VALUES(c.tenant_id,c.workspace_id,c.principal_id,COALESCE(c.on_behalf_of_user_id,c.user_id),
  run_id,step_id,uuidv7(),uuidv7(),fingerprint,'reserved',capacity) RETURNING * INTO o;
 RETURN memoriesql.result_preparation_receipt_v1(o,false);
END $$;
REVOKE ALL ON FUNCTION memoriesql.reserve_result_preparation_v1(uuid,uuid,text,bigint) FROM PUBLIC;

CREATE FUNCTION memoriesql.seal_result_preparation_v1(
 op uuid,ownership uuid,artifact uuid,body bytea,witnesses bytea,dependencies bytea,
 artifact_hash text,parents jsonb
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 parent_owner memoriesql.result_preparation_operations%ROWTYPE; parent_item jsonb;
 encoded bigint; charge bigint; parent_number integer:=0; actual_hash text; old_parents jsonb;
BEGIN
 IF op IS NULL OR ownership IS NULL OR artifact IS NULL OR body IS NULL OR witnesses IS NULL
  OR dependencies IS NULL OR artifact_hash IS NULL OR artifact_hash !~ '^[a-f0-9]{64}$'
  OR jsonb_typeof(parents) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023';
 END IF;
 IF jsonb_array_length(parents)>8 THEN RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023'; END IF;
 FOR parent_item IN SELECT value FROM jsonb_array_elements(parents) LOOP
  IF jsonb_typeof(parent_item)<>'object' OR NOT(parent_item ?& ARRAY['artifact_ref','artifact_sha256'])
   OR parent_item-ARRAY['artifact_ref','artifact_sha256']<>'{}'::jsonb
   OR jsonb_typeof(parent_item->'artifact_ref')<>'string' OR jsonb_typeof(parent_item->'artifact_sha256')<>'string'
   OR COALESCE(parent_item->>'artifact_ref','') !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
   OR COALESCE(parent_item->>'artifact_sha256','') !~ '^[a-f0-9]{64}$' THEN
   RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023';
  END IF;
 END LOOP;
 IF (SELECT count(*)<>count(DISTINCT value->>'artifact_ref') FROM jsonb_array_elements(parents)) THEN
  RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023';
 END IF;
 encoded:=octet_length(body)::bigint+octet_length(witnesses)+octet_length(dependencies);
 -- Minimum encoding/control-state charge, NOT a qualified physical profile.
 charge:=encoded+8192+512*jsonb_array_length(parents);
 actual_hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
  int8send(octet_length(body)::bigint)||body||int8send(octet_length(witnesses)::bigint)||witnesses||
  int8send(octet_length(dependencies)::bigint)||dependencies),'hex');
 IF actual_hash<>artifact_hash THEN RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023'; END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF NOT FOUND OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) OR o.ownership_ref<>ownership THEN
  RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||o.run_ref::text||':'||o.step_key::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation:'||c.workspace_id::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(c.tenant_id,c.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=op FOR UPDATE;
 IF NOT FOUND OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) OR o.ownership_ref<>ownership
  OR o.state='discarded' THEN RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 IF charge>o.reservation_bytes THEN RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000'; END IF;
 IF o.state='sealed' THEN
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id AND operation_ref=op;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('artifact_ref',parent_ref,'artifact_sha256',parent_sha256) ORDER BY ordinal),'[]'::jsonb)
  INTO old_parents FROM memoriesql.result_preparation_parent_holds WHERE tenant_id=c.tenant_id AND child_ref=a.artifact_ref;
  IF a.artifact_ref<>artifact OR a.artifact_sha256<>actual_hash OR old_parents<>parents THEN
   RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
  RETURN memoriesql.result_preparation_receipt_v1(o,true);
 END IF;
 -- Parents already exist; publication cannot create a cycle or invent content.
 -- There is no ancestry-depth or result-count cap, and no disclosure/expiry claim.
 FOR parent_item IN SELECT value FROM jsonb_array_elements(parents) LOOP
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id
   AND artifact_ref=(parent_item->>'artifact_ref')::uuid;
  IF NOT FOUND OR a.artifact_ref=artifact OR a.artifact_sha256<>parent_item->>'artifact_sha256' THEN
   RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
  SELECT * INTO parent_owner FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=a.operation_ref;
  IF parent_owner.workspace_id<>c.workspace_id OR parent_owner.principal_id<>c.principal_id
   OR parent_owner.owner_user_id IS DISTINCT FROM o.owner_user_id OR parent_owner.state<>'sealed' THEN
   RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 END LOOP;
 INSERT INTO memoriesql.result_preparation_artifacts VALUES(c.tenant_id,artifact,op,actual_hash,body,witnesses,dependencies,clock_timestamp());
 FOR parent_item IN SELECT value FROM jsonb_array_elements(parents) LOOP
  parent_number:=parent_number+1;
  INSERT INTO memoriesql.result_preparation_parent_holds VALUES(c.tenant_id,artifact,
   (parent_item->>'artifact_ref')::uuid,parent_item->>'artifact_sha256',parent_number);
 END LOOP;
 UPDATE memoriesql.result_preparation_operations SET state='sealed',encoded_bytes=encoded,allocation_bytes=charge,new_allocation_bytes=charge
 WHERE tenant_id=c.tenant_id AND operation_ref=op RETURNING * INTO o;
 -- Artifact, all parent holds and private mutation receipt appear in ONE commit.
 RETURN memoriesql.result_preparation_receipt_v1(o,false);
END $$;
REVOKE ALL ON FUNCTION memoriesql.seal_result_preparation_v1(uuid,uuid,uuid,bytea,bytea,bytea,text,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.discard_result_preparation_v1(op uuid,ownership uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; artifact uuid;
BEGIN
 IF op IS NULL OR ownership IS NULL THEN RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023'; END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF NOT FOUND OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) OR o.ownership_ref<>ownership THEN
  RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||o.run_ref::text||':'||o.step_key::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation:'||c.workspace_id::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(c.tenant_id,c.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=op FOR UPDATE;
 IF NOT FOUND OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) OR o.ownership_ref<>ownership THEN
  RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 IF o.state='discarded' THEN RETURN memoriesql.result_preparation_receipt_v1(o,true); END IF;
 SELECT artifact_ref INTO artifact FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id AND operation_ref=op;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_parent_holds WHERE tenant_id=c.tenant_id AND parent_ref=artifact) THEN
  RAISE EXCEPTION 'preparation_dependency_held' USING ERRCODE='55000'; END IF;
 DELETE FROM memoriesql.result_preparation_parent_holds WHERE tenant_id=c.tenant_id AND child_ref=artifact;
 DELETE FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id AND artifact_ref=artifact;
 -- Released bodies refund retained allocation, never cumulative new allocation.
 -- The retained private noncontent journal itself is still charged.
 UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,encoded_bytes=0,allocation_bytes=8192
 WHERE tenant_id=c.tenant_id AND operation_ref=op RETURNING * INTO o;
 RETURN memoriesql.result_preparation_receipt_v1(o,false);
END $$;
REVOKE ALL ON FUNCTION memoriesql.discard_result_preparation_v1(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.reserve_result_preparation_v1(uuid,uuid,text,bigint),
 memoriesql.seal_result_preparation_v1(uuid,uuid,uuid,bytea,bytea,bytea,text,jsonb),
 memoriesql.discard_result_preparation_v1(uuid,uuid) TO memoriesql_application;
