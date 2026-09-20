-- Declared evidence scopes reuse packages, stable bindings and existing execution.
-- Historical migrations, native identities and published payloads are unchanged.
ALTER TABLE memoriesql.evidence_producer_policies ADD COLUMN allow_declared_scopes boolean NOT NULL DEFAULT false;
ALTER TABLE memoriesql.logical_unit_materializations ADD COLUMN scope_declaration jsonb;
CREATE INDEX declared_scope_package_binding ON memoriesql.logical_unit_materializations(tenant_id,package_id) WHERE scope_declaration IS NOT NULL;

CREATE FUNCTION memoriesql.declared_scope_valid(scope jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_typeof(scope)='object'
 AND scope-ARRAY['scope_id','boundary_basis','episode_completeness','amends_source_unit_id','amendment_reason']='{}'::jsonb
 AND (SELECT count(*) FROM jsonb_object_keys(scope))=5
 AND scope->>'scope_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
 AND jsonb_typeof(scope->'boundary_basis')='string' AND length(btrim(scope->>'boundary_basis')) BETWEEN 1 AND 512
 AND scope->>'episode_completeness'='unknown'
 AND ((scope->'amends_source_unit_id'='null'::jsonb AND scope->'amendment_reason'='null'::jsonb)
 OR (scope->>'amends_source_unit_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
 AND jsonb_typeof(scope->'amendment_reason')='string' AND length(btrim(scope->>'amendment_reason')) BETWEEN 1 AND 512)),false);
$$;
REVOKE ALL ON FUNCTION memoriesql.declared_scope_valid(jsonb) FROM PUBLIC,memoriesql_application;
ALTER TABLE memoriesql.logical_unit_materializations ADD CONSTRAINT declared_scope_shape
 CHECK(scope_declaration IS NULL OR (memoriesql.declared_scope_valid(scope_declaration) AND stable_identity_hash IS NOT NULL));

-- Unresolved native boundary/source completeness remains truthful in v1 packages.
-- Complete physical records and normalized scope coverage are producer assertions.
CREATE FUNCTION memoriesql.declared_scope_package_ready(d jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(d->>'occurrence_identity_basis'='producer_assigned'
 AND jsonb_strip_nulls(d->'native')='{}'::jsonb
 AND d#>>'{qualification,topology}'='unknown'
 AND d#>>'{qualification,boundary}'='unresolved'
 AND d#>>'{qualification,source_completeness}'='unresolved'
 AND d#>>'{qualification,physical_records}'='complete'
 AND d#>>'{qualification,normalized_input}'='complete'
 AND d#>'{qualification,unresolved_coverage}'='[]'::jsonb,false);
$$;
REVOKE ALL ON FUNCTION memoriesql.declared_scope_package_ready(jsonb) FROM PUBLIC,memoriesql_application;

-- Compare ordered retained membership, not receipt/chunk IDs or text similarity.
-- Adjacent slices in one immutable file revision coalesce across storage splits.
CREATE FUNCTION memoriesql.declared_scope_membership_hash(t uuid,p uuid) RETURNS text
LANGUAGE sql STABLE SET search_path=pg_catalog,memoriesql AS $$
 WITH slices AS (
  SELECT row_number() OVER(ORDER BY part.ordinal,x.ordinality) n,
    r.source_revision_key,r.file_identity_key,(x.value->>'byte_start')::bigint lo,(x.value->>'byte_end_exclusive')::bigint hi
  FROM memoriesql.evidence_package_parts part
  CROSS JOIN LATERAL jsonb_array_elements(part.inventory->'lineage') WITH ORDINALITY x
  JOIN memoriesql.source_range_capture_receipts r ON r.tenant_id=t AND r.source_range_receipt_id=(x.value->>'source_range_receipt_id')::uuid
  WHERE part.tenant_id=t AND part.package_id=p
 ), boundaries AS (
  SELECT *, CASE WHEN lag(hi) OVER(ORDER BY n)=lo AND lag(file_identity_key) OVER(ORDER BY n)=file_identity_key
   AND lag(source_revision_key) OVER(ORDER BY n)=source_revision_key THEN 0 ELSE 1 END new_group FROM slices
 ), numbered AS (SELECT *,sum(new_group) OVER(ORDER BY n) g FROM boundaries),
 runs AS (SELECT g,min(n) first_n,source_revision_key,file_identity_key,min(lo) lo,max(hi) hi
  FROM numbered GROUP BY g,source_revision_key,file_identity_key)
 SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(jsonb_build_array(source_revision_key,file_identity_key,lo,hi) ORDER BY first_n),'[]'::jsonb)::text,'UTF8')),'hex') FROM runs;
$$;
REVOKE ALL ON FUNCTION memoriesql.declared_scope_membership_hash(uuid,uuid) FROM PUBLIC,memoriesql_application;

CREATE FUNCTION memoriesql.materialize_declared_scope_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); c memoriesql.authorization_contexts%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
    policy memoriesql.evidence_producer_policies%ROWTYPE; old_receipt memoriesql.idempotency_receipts%ROWTYPE;
    frame memoriesql.source_event_materializations%ROWTYPE; binding memoriesql.logical_unit_materializations%ROWTYPE;
    e jsonb; scope jsonb:=request->'scope'; carry jsonb:=request->'carry_forward'; original memoriesql.evidence_packages%ROWTYPE; n jsonb; pin jsonb; input jsonb; result jsonb;
    stable_hash text; content_hash text; identity_namespace text; req_hash text; event_hash text; declaration_hash text; unit_identity text;
    receipt_id uuid:=uuidv7(); eid uuid:=uuidv7(); uid uuid:=uuidv7(); bid uuid:=uuidv7(); tid uuid:=uuidv7(); enqueue_receipt uuid;
    parent_id uuid:=NULL; manifest_id text; existing boolean:=false;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'materialization_requires_read_committed' USING ERRCODE='25000';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>16384 OR octet_length(request::text)>16384 OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','package_id','expected_inventory_sha256','producer_policy_id','expected_source_object_schema_version','source_type','scope','carry_forward']<>'{}'::jsonb OR
       (SELECT count(*) FROM jsonb_object_keys(request))<>10 OR request->'contract_version' IS DISTINCT FROM '3'::jsonb OR request->'expected_schema_version' IS DISTINCT FROM '25'::jsonb OR
       jsonb_typeof(request->'idempotency_key') IS DISTINCT FROM 'string' OR COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR
       COALESCE(request->>'expected_inventory_sha256','')!~'^[a-f0-9]{64}$' OR
       COALESCE(request->>'source_type','') NOT IN ('transcript','document','media','relational','operational') OR
       NOT memoriesql.declared_scope_valid(scope) OR
       (carry<>'null'::jsonb AND (jsonb_typeof(carry) IS DISTINCT FROM 'object' OR
         carry-ARRAY['original_package_id','correspondence_basis']<>'{}'::jsonb OR
         (SELECT count(*) FROM jsonb_object_keys(carry))<>2 OR
         COALESCE(carry->>'original_package_id','')!~'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' OR
         jsonb_typeof(carry->'correspondence_basis') IS DISTINCT FROM 'string' OR
         COALESCE(length(btrim(carry->>'correspondence_basis')),0) NOT BETWEEN 1 AND 512)) THEN
        RAISE EXCEPTION 'invalid_declared_scope_contract' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    s:=memoriesql.source_stable_authorize(p.source_object_id);
    -- The authenticated producer must own the attestation. A caller cannot
    -- adopt another principal's package merely by supplying a qualification ref.
    IF p.producer_principal_id IS DISTINCT FROM c.principal_id OR s.schema_version IS DISTINCT FROM (request->>'expected_source_object_schema_version')::integer THEN
        RAISE EXCEPTION 'materialization_authority_unavailable' USING ERRCODE='42501'; END IF;
    IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM request->>'expected_inventory_sha256' OR
       NOT memoriesql.declared_scope_package_ready(p.declaration) THEN
        RAISE EXCEPTION 'materialization_input_pending_or_conflicting' USING ERRCODE='55000'; END IF;
    SELECT * INTO policy FROM memoriesql.evidence_producer_policies WHERE tenant_id=c.tenant_id AND producer_policy_id=(request->>'producer_policy_id')::uuid AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_object_id=s.source_object_id AND producer_principal_id=c.principal_id AND status='active' AND created_at<=clock_timestamp() AND expires_at>clock_timestamp() AND qualification_ref=p.declaration#>>'{qualification,qualification_ref}' AND normalization_policy_version=p.declaration->>'normalization_policy_version';
    IF policy.producer_policy_id IS NULL OR NOT policy.allow_declared_scopes THEN RAISE EXCEPTION 'trusted_producer_policy_unavailable' USING ERRCODE='42501'; END IF;
    identity_namespace:=policy.source_stable_namespace;
    IF identity_namespace IS NULL THEN RAISE EXCEPTION 'source_stable_policy_unavailable' USING ERRCODE='42501'; END IF;
    IF p.declaration->>'occurrence_key' IS DISTINCT FROM 'declared-scope.'||(scope->>'scope_id') OR
       p.declaration#>>'{qualification,boundary_basis}' IS DISTINCT FROM scope->>'boundary_basis' THEN
        RAISE EXCEPTION 'declared_scope_package_mismatch' USING ERRCODE='55000'; END IF;
    e:=jsonb_build_object('event_key',p.declaration->>'occurrence_key','identity_basis','producer_assigned',
        'source_type',request->>'source_type','native',p.declaration->'native','expected_units',1);
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialization-source:'||s.source_object_id::text,0));
    IF memoriesql.source_identity_mode_conflicts(c.tenant_id,s.source_object_id,identity_namespace) THEN
        RAISE EXCEPTION 'source_stable_transition_unsupported' USING ERRCODE='55000'; END IF;
    stable_hash:=encode(sha256(convert_to(jsonb_build_array('declared-scope-occurrence.v1',s.source_object_id,identity_namespace,p.declaration->>'occurrence_key')::text,'UTF8')),'hex');
    content_hash:=memoriesql.source_stable_content_hash(c.tenant_id,p.package_id);
    req_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    event_hash:=encode(sha256(convert_to(jsonb_build_array('declared-scope-event.v1',s.source_object_id,identity_namespace,e->>'event_key')::text,'UTF8')),'hex');
    declaration_hash:=encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(e),'UTF8')),'hex');
    n:=p.declaration->'native';
    unit_identity:=encode(sha256(convert_to(jsonb_build_array(event_hash,p.declaration->>'occurrence_identity_basis',n,parent_id)::text,'UTF8')),'hex');
    -- Same authority fence as storage and queue; deterministic keyed locks only.
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialize-operation:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-event:'||event_hash,0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-occurrence:'||p.occurrence_hash,0));
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    IF clock_timestamp()>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    SELECT * INTO old_receipt FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='logical_unit.materialize.v3' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old_receipt.request_hash<>req_hash OR old_receipt.workspace_id<>c.workspace_id THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old_receipt.response_receipt||jsonb_build_object('replayed',true);
    END IF;
    SELECT * INTO frame FROM memoriesql.source_event_materializations WHERE tenant_id=c.tenant_id AND event_identity_hash=event_hash;
    IF FOUND THEN
        IF frame.declaration_hash<>declaration_hash THEN RAISE EXCEPTION 'logical_event_identity_conflict' USING ERRCODE='23505'; END IF;
        eid:=frame.event_id;
    END IF;
    SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=c.tenant_id AND stable_identity_hash=stable_hash;
    IF FOUND THEN
        IF binding.scope_declaration IS DISTINCT FROM scope THEN
            RAISE EXCEPTION 'declared_scope_identity_conflict' USING ERRCODE='23505'; END IF;
        SELECT * INTO original FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=binding.package_id;
        IF original.declaration->>'source_revision_key'=p.declaration->>'source_revision_key' THEN
            IF memoriesql.declared_scope_membership_hash(c.tenant_id,original.package_id) IS DISTINCT FROM memoriesql.declared_scope_membership_hash(c.tenant_id,p.package_id) OR carry<>'null'::jsonb THEN
                RAISE EXCEPTION 'declared_scope_membership_conflict' USING ERRCODE='23505'; END IF;
        ELSIF carry='null'::jsonb OR carry->>'original_package_id' IS DISTINCT FROM binding.package_id::text THEN
            RAISE EXCEPTION 'declared_scope_correspondence_required' USING ERRCODE='55000';
        END IF;
        IF binding.identity_hash<>unit_identity OR binding.stable_content_hash<>content_hash THEN RAISE EXCEPTION 'logical_occurrence_identity_conflict' USING ERRCODE='23505'; END IF;
        existing:=true; eid:=binding.event_id; uid:=binding.source_unit_id; bid:=binding.bead_id; tid:=binding.task_id; pin:=binding.package_pin;
    ELSE
        IF carry<>'null'::jsonb THEN RAISE EXCEPTION 'declared_scope_carry_forward_without_binding' USING ERRCODE='55000'; END IF;
        IF scope->>'amends_source_unit_id' IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM memoriesql.logical_unit_materializations previous
            JOIN memoriesql.evidence_packages previous_package ON previous_package.tenant_id=previous.tenant_id AND previous_package.package_id=previous.package_id
            WHERE previous.tenant_id=c.tenant_id AND previous.workspace_id=c.workspace_id AND previous.access_scope_id=s.access_scope_id
              AND previous.source_unit_id=(scope->>'amends_source_unit_id')::uuid AND previous.scope_declaration IS NOT NULL
              AND previous_package.source_object_id=s.source_object_id AND previous.stable_identity_namespace=identity_namespace
        ) THEN RAISE EXCEPTION 'declared_scope_amendment_unavailable' USING ERRCODE='42501'; END IF;
        pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
    END IF;
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,receipt_id,'logical_unit.materialize.v3',request->>'idempotency_key',req_hash,'in_progress','source',s.source_object_id,1,clock_timestamp(),clock_timestamp());
    IF NOT existing THEN
        IF frame.event_id IS NULL THEN
            INSERT INTO memoriesql.source_events(tenant_id,workspace_id,access_scope_id,event_id,source_object_id,source_type,source_system,installation_id,external_event_id,external_id_scope,source_identity_key,session_id,actor_id,actor_kind,source_occurred_at,source_occurred_at_raw,source_sequence,source_revision_key,parser_contract_version,observation_unit_policy_version,observation_unit_count,captured_at,source_ref,content_hash,metadata,materialization_version)
            VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,eid,s.source_object_id,e->>'source_type',s.source_system,s.installation_id,e#>>'{native,native_id}','logical-events.v1:'||s.source_object_id::text,'logical-event.v1:'||event_hash,e#>>'{native,session_native_id}',e#>>'{native,participant_native_id}',e#>>'{native,role}',(e#>>'{native,occurred_at}')::timestamptz,e#>>'{native,occurred_at_raw}',(e#>>'{native,source_order}')::bigint,p.declaration->>'source_revision_key','logical-materialization.v1','logical-materialization.v1',(e->>'expected_units')::integer,clock_timestamp(),'logical-event.v1:'||event_hash,declaration_hash,jsonb_build_object('source_stable_namespace',identity_namespace,'declared_evidence_scope',scope,'declaration',e,'content_hash_kind','event_declaration_sha256','independently_proven_source_complete',false),1);
            INSERT INTO memoriesql.source_event_materializations(tenant_id,event_id,event_identity_hash,declaration,declaration_hash,initial_receipt_id) VALUES(c.tenant_id,eid,event_hash,e,declaration_hash,receipt_id);
        END IF;
        INSERT INTO memoriesql.source_units(tenant_id,workspace_id,access_scope_id,source_unit_id,event_id,parent_unit_id,unit_kind,is_observation,external_unit_id,unit_ordinal,unit_source_occurred_at,content_hash,structure,hydration_ref,schema_version,created_at,processing_receipt_id,materialization_version)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uid,eid,parent_id,'logical_unit',true,n->>'native_id',(n->>'source_order')::bigint,(n->>'occurred_at')::timestamptz,p.inventory_hash,jsonb_build_object('native',n,'occurrence_identity_basis',p.declaration->>'occurrence_identity_basis','parent_resolution',CASE WHEN parent_id IS NOT NULL THEN 'canonical' WHEN n->>'parent_native_id' IS NOT NULL THEN 'native_identity_only' ELSE 'unknown' END,'content_hash_kind','sealed_inventory_sha256'),'evidence-package.'||p.package_id::text,17,clock_timestamp(),receipt_id,1);
        INSERT INTO memoriesql.beads(tenant_id,workspace_id,access_scope_id,bead_id,event_id,source_unit_id,created_at) VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,bid,eid,uid,clock_timestamp());
        manifest_id:='evidence-package.'||p.package_id::text;
        input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',1,'target_reference',uid,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
            'evidence_manifest',jsonb_build_object('manifest_id',manifest_id,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',manifest_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
            'payload',jsonb_build_object('source_object_id',s.source_object_id,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'materialization_receipt_id',receipt_id,'producer_policy_id',policy.producer_policy_id,'package',pin,'required_execution','trusted_complete_input_exposure_validation','execution_availability','unavailable'));
        SELECT q.idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.enqueue_semantic_task(tid,'complete-unit.v1:'||p.occurrence_hash,'memoriesql.kernel','memory.semantic.author-complete-unit',1,'8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c','complete-unit-bindings-v1:8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c',uid::text,0,input,manifest_id,s.access_scope_id,started,NULL,started) q;
        INSERT INTO memoriesql.logical_unit_materializations VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,p.occurrence_hash,unit_identity,eid,uid,bid,tid,p.package_id,policy.producer_policy_id,pin,receipt_id,identity_namespace,stable_hash,content_hash,scope) RETURNING * INTO binding;
        INSERT INTO memoriesql.outbox_events(tenant_id,workspace_id,access_scope_id,outbox_event_id,idempotency_receipt_id,aggregate_kind,aggregate_id,event_kind,payload,headers,recorded_at,available_at)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uuidv7(),receipt_id,'source_unit',uid,'source_unit.materialized',jsonb_build_object('event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'package_id',p.package_id),'{}',clock_timestamp(),clock_timestamp());
    ELSE
        SELECT idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=tid;
    END IF;
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    IF clock_timestamp()>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',3,'scope',scope,'carry_forward',carry,'idempotency_receipt_id',receipt_id,'initial_materialization_receipt_id',binding.initial_receipt_id,'task_enqueue_receipt_id',enqueue_receipt,'submitted_package_id',p.package_id,'bound_package',pin,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'status',CASE WHEN existing THEN 'already_exists' ELSE 'materialized' END,'replayed',false,'execution_availability','unavailable','execution_reason','complete_input_executor_unavailable','event_progress_at_commit',memoriesql.logical_event_progress_v1(c.tenant_id,eid));
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=receipt_id;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.materialize_declared_scope_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.materialize_declared_scope_v1(jsonb) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.complete_input_authorize(t uuid, binding_id uuid, dispatch_id uuid)
RETURNS memoriesql.logical_unit_materializations LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE b memoriesql.logical_unit_materializations%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    c memoriesql.authorization_contexts%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'complete_input_requires_read_committed' USING ERRCODE='25000'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=t AND workspace_id=c.workspace_id AND task_id=binding_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=t AND package_id=b.package_id;
    s:=memoriesql.evidence_package_authorize(p.source_object_id,false);
    IF c.tenant_id IS DISTINCT FROM t OR c.expires_at<=clock_timestamp() OR b.task_id IS NULL OR
       NOT memoriesql.semantic_task_origin_capability_authorized(t,binding_id,'source.raw.read',clock_timestamp()) OR
       NOT memoriesql.semantic_task_origin_authorized(t,binding_id,clock_timestamp()) OR
       NOT memoriesql.current_context_scope_time_authorized(b.access_scope_id,'read',clock_timestamp()) OR
       p.sealed_receipt_id IS NULL OR b.package_pin->>'inventory_sha256' IS DISTINCT FROM p.inventory_hash OR
       ((b.scope_declaration IS NULL AND p.declaration#>>'{qualification,boundary}' IS DISTINCT FROM 'qualified_native_unit') OR (b.scope_declaration IS NOT NULL AND NOT memoriesql.declared_scope_package_ready(p.declaration))) OR
       p.declaration#>>'{qualification,physical_records}' IS DISTINCT FROM 'complete' OR
       p.declaration#>>'{qualification,normalized_input}' IS DISTINCT FROM 'complete' OR
       (b.scope_declaration IS NULL AND p.declaration#>>'{qualification,source_completeness}' IS DISTINCT FROM 'producer_attested') OR
       p.declaration#>'{qualification,unresolved_coverage}' IS DISTINCT FROM '[]'::jsonb OR
       NOT EXISTS(SELECT 1 FROM memoriesql.evidence_producer_policies q WHERE q.tenant_id=t AND q.producer_policy_id=b.producer_policy_id
          AND (b.scope_declaration IS NULL OR q.allow_declared_scopes) AND q.status='active' AND q.created_at<=clock_timestamp() AND q.expires_at>clock_timestamp()
          AND q.source_object_id=p.source_object_id AND q.producer_principal_id=p.producer_principal_id
          AND q.qualification_ref=p.declaration#>>'{qualification,qualification_ref}' AND q.normalization_policy_version=p.declaration->>'normalization_policy_version') OR
       NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=t AND d.dispatch_policy_id=dispatch_id
          AND d.workspace_id=b.workspace_id AND d.access_scope_id=b.access_scope_id AND d.source_object_id=p.source_object_id
          AND d.status='active' AND d.created_at<=clock_timestamp() AND d.expires_at>clock_timestamp()) THEN
        RAISE EXCEPTION 'complete_input_authority_unavailable' USING ERRCODE='42501'; END IF;
    IF EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t AND e.binding_task_id=binding_id AND e.source_schema_version<>s.schema_version) THEN
        RAISE EXCEPTION 'complete_input_source_changed' USING ERRCODE='55000'; END IF;
    RETURN b;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.complete_input_authorize(uuid,uuid,uuid) FROM PUBLIC;


-- Versioned projection; v1 native readiness is preserved, never relabeled.
CREATE FUNCTION memoriesql.inspect_scoped_evidence_package_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE result jsonb; binding memoriesql.logical_unit_materializations%ROWTYPE; t uuid;
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR octet_length(request::text)>4096 OR
    request-ARRAY['contract_version','operation','package_id']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(request))<>3 OR
    request->'contract_version' IS DISTINCT FROM '2'::jsonb OR request->>'operation' IS DISTINCT FROM 'inspect' THEN
  RAISE EXCEPTION 'invalid_scope_inspection' USING ERRCODE='22023'; END IF;
 result:=memoriesql.read_evidence_package_v1(request||'{"contract_version":1}'::jsonb);
 SELECT tenant_id INTO t FROM memoriesql.current_authorization_context();
 SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=t AND package_id=(request->>'package_id')::uuid AND scope_declaration IS NOT NULL;
 RETURN jsonb_build_object('contract_version',2,'package',result,'scope_readiness',CASE WHEN binding.package_id IS NULL THEN 'not_materialized' ELSE 'materialized_declared_scope' END,
 'scope_binding',CASE WHEN binding.package_id IS NULL THEN NULL ELSE jsonb_build_object('scope',binding.scope_declaration,'original_package_id',binding.package_id,'source_unit_id',binding.source_unit_id,'initial_materialization_receipt_id',binding.initial_receipt_id,'identity_basis','declared_scope') END);
END; $$;
REVOKE ALL ON FUNCTION memoriesql.inspect_scoped_evidence_package_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_scoped_evidence_package_v2(jsonb) TO memoriesql_application;
