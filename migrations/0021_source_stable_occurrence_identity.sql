-- Explicit source-stable materialization; package v1 remains revision-bound.
-- Published migrations 0001–0020 are unchanged. No trust is provisioned.
ALTER TABLE memoriesql.evidence_producer_policies ADD COLUMN source_stable_namespace text
    CHECK (source_stable_namespace IS NULL OR length(source_stable_namespace) BETWEEN 1 AND 128);
ALTER TABLE memoriesql.logical_unit_materializations
    ADD COLUMN stable_identity_namespace text,
    ADD COLUMN stable_identity_hash text,
    ADD COLUMN stable_content_hash text,
    ADD CONSTRAINT source_stable_binding_complete CHECK (
      (stable_identity_namespace IS NULL AND stable_identity_hash IS NULL AND stable_content_hash IS NULL)
      OR (stable_identity_namespace IS NOT NULL AND stable_identity_hash IS NOT NULL AND stable_content_hash IS NOT NULL AND stable_identity_hash ~ '^[a-f0-9]{64}$' AND stable_content_hash ~ '^[a-f0-9]{64}$'));
CREATE UNIQUE INDEX source_stable_occurrence_uq ON memoriesql.logical_unit_materializations(tenant_id,stable_identity_hash) WHERE stable_identity_hash IS NOT NULL;
CREATE INDEX source_stable_event_scope ON memoriesql.logical_unit_materializations(tenant_id,event_id,stable_identity_namespace);

-- Include legacy canonical capture in the same source-mode fence. No caller can
-- opt an already populated source into a new identity interpretation.
CREATE FUNCTION memoriesql.guard_source_identity_mode() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
DECLARE ns text:=CASE WHEN NEW.materialization_version=1 THEN NEW.metadata->>'source_stable_namespace' END;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text||':materialization-source:'||NEW.source_object_id::text,0));
 IF EXISTS (SELECT 1 FROM memoriesql.source_events e WHERE e.tenant_id=NEW.tenant_id AND e.source_object_id=NEW.source_object_id
   AND (CASE WHEN e.materialization_version=1 THEN e.metadata->>'source_stable_namespace' END) IS DISTINCT FROM ns) THEN
   RAISE EXCEPTION 'source_identity_mode_transition_unsupported' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER source_identity_mode BEFORE INSERT ON memoriesql.source_events FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_source_identity_mode();
REVOKE ALL ON FUNCTION memoriesql.guard_source_identity_mode() FROM PUBLIC,memoriesql_application;

-- Reuse the released clock-aware source/role fence and require current write
-- permission as well. This helper is private to the explicit new entry point.
CREATE FUNCTION memoriesql.source_stable_authorize(source_id uuid) RETURNS memoriesql.source_objects
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
DECLARE s memoriesql.source_objects%ROWTYPE;
BEGIN
 s:=memoriesql.revisiting_source_authorize(source_id);
 PERFORM memoriesql.evidence_package_authorize(source_id,true);
 IF NOT memoriesql.current_context_scope_time_authorized(s.access_scope_id,'write',clock_timestamp()) THEN
  RAISE EXCEPTION 'source_stable_authority_unavailable' USING ERRCODE='42501'; END IF;
 RETURN s;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.source_stable_authorize(uuid) FROM PUBLIC,memoriesql_application;

-- Consistency, never identity inference. Scan at most one sealed 256-part/16-MiB
-- inventory. Hash ordered logical components, coalescing only storage fragments.
-- Raw revisions, lineage, part IDs and fragment boundaries remain in each package.
CREATE FUNCTION memoriesql.source_stable_content_hash(t uuid,p uuid) RETURNS text
LANGUAGE sql STABLE SET search_path=pg_catalog,memoriesql AS $$
 SELECT encode(sha256(convert_to(COALESCE(string_agg(component_hash,'' ORDER BY first_ordinal),''),'UTF8')),'hex')
 FROM (
   SELECT min(ordinal) AS first_ordinal,
     encode(sha256(convert_to(jsonb_build_array(inventory->'component_key',inventory->'parent_component_key',inventory->'kind',inventory->'native',
       encode(sha256(convert_to(string_agg(content,'' ORDER BY ordinal),'UTF8')),'hex'))::text,'UTF8')),'hex') AS component_hash
   FROM memoriesql.evidence_package_parts WHERE tenant_id=t AND package_id=p
   GROUP BY inventory->'component_key',inventory->'parent_component_key',inventory->'kind',inventory->'native'
 ) components;
$$;
REVOKE ALL ON FUNCTION memoriesql.source_stable_content_hash(uuid,uuid) FROM PUBLIC,memoriesql_application;

-- Preserve the historical body behind a guarded entry point. Source locking is
-- shared with v2, so legacy and stable first materialization cannot race.
ALTER FUNCTION memoriesql.materialize_logical_unit_v1(jsonb) RENAME TO materialize_logical_unit_revision_v1;
REVOKE ALL ON FUNCTION memoriesql.materialize_logical_unit_revision_v1(jsonb) FROM PUBLIC,memoriesql_application;
CREATE FUNCTION memoriesql.materialize_logical_unit_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; source_id uuid;
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT source_object_id INTO source_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    PERFORM memoriesql.evidence_package_authorize(source_id,true);
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialization-source:'||source_id::text,0));
    IF EXISTS (SELECT 1 FROM memoriesql.logical_unit_materializations b JOIN memoriesql.source_events e USING(tenant_id,event_id)
      WHERE e.tenant_id=c.tenant_id AND e.source_object_id=source_id AND b.stable_identity_namespace IS NOT NULL) THEN
      RAISE EXCEPTION 'revision_sensitive_transition_unsupported' USING ERRCODE='55000'; END IF;
    RETURN memoriesql.materialize_logical_unit_revision_v1(request);
END; $$;
REVOKE ALL ON FUNCTION memoriesql.materialize_logical_unit_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.materialize_logical_unit_v1(jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.materialize_logical_unit_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); c memoriesql.authorization_contexts%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
    policy memoriesql.evidence_producer_policies%ROWTYPE; old_receipt memoriesql.idempotency_receipts%ROWTYPE;
    frame memoriesql.source_event_materializations%ROWTYPE; binding memoriesql.logical_unit_materializations%ROWTYPE;
    parent memoriesql.source_units%ROWTYPE; e jsonb:=request->'event'; n jsonb; pin jsonb; input jsonb; result jsonb;
    stable_hash text; content_hash text; identity_namespace text; req_hash text; event_hash text; declaration_hash text; unit_identity text;
    receipt_id uuid:=uuidv7(); eid uuid:=uuidv7(); uid uuid:=uuidv7(); bid uuid:=uuidv7(); tid uuid:=uuidv7(); enqueue_receipt uuid;
    parent_id uuid:=(request->>'parent_source_unit_id')::uuid; manifest_id text; existing boolean:=false;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'materialization_requires_read_committed' USING ERRCODE='25000';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>16384 OR octet_length(request::text)>16384 OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','package_id','expected_inventory_sha256','producer_policy_id','expected_source_object_schema_version','event','parent_source_unit_id']<>'{}'::jsonb OR
       (SELECT count(*) FROM jsonb_object_keys(request))<>9 OR request->'contract_version' IS DISTINCT FROM '2'::jsonb OR request->'expected_schema_version' IS DISTINCT FROM '21'::jsonb OR
       jsonb_typeof(request->'idempotency_key') IS DISTINCT FROM 'string' OR COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR
       COALESCE(request->>'expected_inventory_sha256','')!~'^[a-f0-9]{64}$' OR
       jsonb_typeof(e) IS DISTINCT FROM 'object' OR e-ARRAY['event_key','identity_basis','source_type','native','expected_units']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(e))<>5 OR
       octet_length(memoriesql.canonical_semantic_json_text(e))>8192 OR jsonb_typeof(e->'event_key') IS DISTINCT FROM 'string' OR COALESCE(length(e->>'event_key'),0) NOT BETWEEN 1 AND 256 OR
       COALESCE(e->>'identity_basis','') NOT IN ('native','producer_assigned') OR COALESCE(e->>'source_type','') NOT IN ('transcript','document','media','relational','operational') OR
       NOT memoriesql.evidence_native_facts_valid(e->'native') OR (e->>'identity_basis'='native' AND e#>>'{native,native_id}' IS NULL) OR
       (e->>'expected_units' IS NOT NULL AND (e->>'expected_units')::integer<1) THEN
        RAISE EXCEPTION 'invalid_materialization_contract' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    s:=memoriesql.source_stable_authorize(p.source_object_id);
    -- The authenticated producer must own the attestation. A caller cannot
    -- adopt another principal's package merely by supplying a qualification ref.
    IF p.producer_principal_id IS DISTINCT FROM c.principal_id OR s.schema_version IS DISTINCT FROM (request->>'expected_source_object_schema_version')::integer THEN
        RAISE EXCEPTION 'materialization_authority_unavailable' USING ERRCODE='42501'; END IF;
    IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM request->>'expected_inventory_sha256' OR
       p.declaration#>>'{qualification,boundary}' IS DISTINCT FROM 'qualified_native_unit' OR p.declaration#>>'{qualification,physical_records}' IS DISTINCT FROM 'complete' OR
       p.declaration#>>'{qualification,normalized_input}' IS DISTINCT FROM 'complete' OR p.declaration#>>'{qualification,source_completeness}' IS DISTINCT FROM 'producer_attested' OR p.declaration#>'{qualification,unresolved_coverage}' IS DISTINCT FROM '[]'::jsonb THEN
        RAISE EXCEPTION 'materialization_input_pending_or_conflicting' USING ERRCODE='55000'; END IF;
    SELECT * INTO policy FROM memoriesql.evidence_producer_policies WHERE tenant_id=c.tenant_id AND producer_policy_id=(request->>'producer_policy_id')::uuid AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_object_id=s.source_object_id AND producer_principal_id=c.principal_id AND status='active' AND created_at<=clock_timestamp() AND expires_at>clock_timestamp() AND qualification_ref=p.declaration#>>'{qualification,qualification_ref}' AND normalization_policy_version=p.declaration->>'normalization_policy_version';
    IF policy.producer_policy_id IS NULL THEN RAISE EXCEPTION 'trusted_producer_policy_unavailable' USING ERRCODE='42501'; END IF;
    identity_namespace:=policy.source_stable_namespace;
    IF identity_namespace IS NULL THEN RAISE EXCEPTION 'source_stable_policy_unavailable' USING ERRCODE='42501'; END IF;
    IF e->>'identity_basis'<>'native' OR COALESCE(length(e#>>'{native,native_id}'),0)=0 OR p.declaration->>'occurrence_identity_basis'<>'native' THEN
        RAISE EXCEPTION 'source_stable_identity_unqualified' USING ERRCODE='55000'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialization-source:'||s.source_object_id::text,0));
    IF EXISTS (SELECT 1 FROM memoriesql.source_events se WHERE se.tenant_id=c.tenant_id AND se.source_object_id=s.source_object_id
        AND NOT EXISTS (SELECT 1 FROM memoriesql.logical_unit_materializations b WHERE b.tenant_id=se.tenant_id AND b.event_id=se.event_id AND b.stable_identity_namespace=identity_namespace)) THEN
        RAISE EXCEPTION 'source_stable_transition_unsupported' USING ERRCODE='55000'; END IF;
    stable_hash:=encode(sha256(convert_to(jsonb_build_array('source-stable-occurrence.v1',s.source_object_id,identity_namespace,p.declaration->>'occurrence_key')::text,'UTF8')),'hex');
    content_hash:=memoriesql.source_stable_content_hash(c.tenant_id,p.package_id);
    req_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    event_hash:=encode(sha256(convert_to(jsonb_build_array('source-stable-event.v1',s.source_object_id,identity_namespace,e->>'event_key')::text,'UTF8')),'hex');
    declaration_hash:=encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(e),'UTF8')),'hex');
    n:=p.declaration->'native';
    unit_identity:=encode(sha256(convert_to(jsonb_build_array(event_hash,p.declaration->>'occurrence_identity_basis',n,parent_id)::text,'UTF8')),'hex');
    -- Same authority fence as storage and queue; deterministic keyed locks only.
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialize-operation:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-event:'||event_hash,0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-occurrence:'||p.occurrence_hash,0));
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    IF clock_timestamp()>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    SELECT * INTO old_receipt FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='logical_unit.materialize.v2' AND idempotency_key=request->>'idempotency_key';
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
        IF binding.identity_hash<>unit_identity OR binding.stable_content_hash<>content_hash THEN RAISE EXCEPTION 'logical_occurrence_identity_conflict' USING ERRCODE='23505'; END IF;
        existing:=true; eid:=binding.event_id; uid:=binding.source_unit_id; bid:=binding.bead_id; tid:=binding.task_id; pin:=binding.package_pin;
    ELSE
        pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
        IF parent_id IS NOT NULL THEN
            SELECT * INTO parent FROM memoriesql.source_units WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_unit_id=parent_id AND event_id=eid AND materialization_version=1;
            IF parent.source_unit_id IS NULL OR (n->>'parent_native_id' IS NOT NULL AND parent.external_unit_id IS NOT NULL AND n->>'parent_native_id'<>parent.external_unit_id) THEN RAISE EXCEPTION 'logical_parent_identity_conflict' USING ERRCODE='23505'; END IF;
        END IF;
    END IF;
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,receipt_id,'logical_unit.materialize.v2',request->>'idempotency_key',req_hash,'in_progress','source',s.source_object_id,1,clock_timestamp(),clock_timestamp());
    IF NOT existing THEN
        IF frame.event_id IS NULL THEN
            INSERT INTO memoriesql.source_events(tenant_id,workspace_id,access_scope_id,event_id,source_object_id,source_type,source_system,installation_id,external_event_id,external_id_scope,source_identity_key,session_id,actor_id,actor_kind,source_occurred_at,source_occurred_at_raw,source_sequence,source_revision_key,parser_contract_version,observation_unit_policy_version,observation_unit_count,captured_at,source_ref,content_hash,metadata,materialization_version)
            VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,eid,s.source_object_id,e->>'source_type',s.source_system,s.installation_id,e#>>'{native,native_id}','logical-events.v1:'||s.source_object_id::text,'logical-event.v1:'||event_hash,e#>>'{native,session_native_id}',e#>>'{native,participant_native_id}',e#>>'{native,role}',(e#>>'{native,occurred_at}')::timestamptz,e#>>'{native,occurred_at_raw}',(e#>>'{native,source_order}')::bigint,p.declaration->>'source_revision_key','logical-materialization.v1','logical-materialization.v1',(e->>'expected_units')::integer,clock_timestamp(),'logical-event.v1:'||event_hash,declaration_hash,jsonb_build_object('source_stable_namespace',identity_namespace,'declaration',e,'content_hash_kind','event_declaration_sha256','independently_proven_source_complete',false),1);
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
        INSERT INTO memoriesql.logical_unit_materializations VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,p.occurrence_hash,unit_identity,eid,uid,bid,tid,p.package_id,policy.producer_policy_id,pin,receipt_id,identity_namespace,stable_hash,content_hash) RETURNING * INTO binding;
        INSERT INTO memoriesql.outbox_events(tenant_id,workspace_id,access_scope_id,outbox_event_id,idempotency_receipt_id,aggregate_kind,aggregate_id,event_kind,payload,headers,recorded_at,available_at)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uuidv7(),receipt_id,'source_unit',uid,'source_unit.materialized',jsonb_build_object('event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'package_id',p.package_id),'{}',clock_timestamp(),clock_timestamp());
    ELSE
        SELECT idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=tid;
    END IF;
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    IF clock_timestamp()>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',2,'idempotency_receipt_id',receipt_id,'initial_materialization_receipt_id',binding.initial_receipt_id,'task_enqueue_receipt_id',enqueue_receipt,'submitted_package_id',p.package_id,'bound_package',pin,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'status',CASE WHEN existing THEN 'already_exists' ELSE 'materialized' END,'replayed',false,'execution_availability','unavailable','execution_reason','complete_input_executor_unavailable','event_progress_at_commit',memoriesql.logical_event_progress_v1(c.tenant_id,eid));
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=receipt_id;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.materialize_logical_unit_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.materialize_logical_unit_v2(jsonb) TO memoriesql_application;
