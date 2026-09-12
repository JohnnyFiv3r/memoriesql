-- Forward execution admission and trusted dispatch evidence. Schema-17 inputs,
-- binding-time availability and receipts remain immutable. No policies seeded.
CREATE TABLE memoriesql.complete_input_dispatch_policies (
    tenant_id uuid NOT NULL,
    dispatch_policy_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    attestor_principal_id uuid NOT NULL,
    approved_by_principal_id uuid NOT NULL,
    qualification_evidence_sha256 text NOT NULL CHECK (qualification_evidence_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    status text NOT NULL CHECK (status IN ('active','revoked')),
    PRIMARY KEY(tenant_id,dispatch_policy_id),
    FOREIGN KEY(tenant_id,workspace_id,access_scope_id,source_object_id) REFERENCES memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id),
    FOREIGN KEY(tenant_id,attestor_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id),
    FOREIGN KEY(tenant_id,approved_by_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id)
);
CREATE TABLE memoriesql.complete_input_executions (
    tenant_id uuid NOT NULL,
    binding_task_id uuid NOT NULL,
    execution_task_id uuid NOT NULL,
    dispatch_policy_id uuid NOT NULL,
    source_schema_version integer NOT NULL,
    activation_receipt_id uuid NOT NULL,
    PRIMARY KEY(tenant_id,binding_task_id),
    UNIQUE(tenant_id,execution_task_id),
    FOREIGN KEY(tenant_id,binding_task_id) REFERENCES memoriesql.semantic_tasks(tenant_id,task_id),
    FOREIGN KEY(tenant_id,execution_task_id) REFERENCES memoriesql.semantic_tasks(tenant_id,task_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(tenant_id,dispatch_policy_id) REFERENCES memoriesql.complete_input_dispatch_policies(tenant_id,dispatch_policy_id),
    FOREIGN KEY(tenant_id,activation_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id)
);
CREATE TABLE memoriesql.complete_input_exposures (
    tenant_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    lease_generation bigint NOT NULL,
    request_id uuid NOT NULL,
    package_id uuid NOT NULL,
    inventory_hash text NOT NULL,
    part_id uuid NOT NULL,
    start_character integer NOT NULL,
    end_character integer NOT NULL CHECK(end_character > start_character AND start_character >= 0),
    content_sha256 text NOT NULL,
    attestor_principal_id uuid NOT NULL,
    dispatch_policy_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY(tenant_id,attempt_id,part_id,start_character),
    FOREIGN KEY(tenant_id,request_id) REFERENCES memoriesql.model_provider_request_intents(tenant_id,request_id),
    FOREIGN KEY(tenant_id,task_id) REFERENCES memoriesql.complete_input_executions(tenant_id,execution_task_id),
    FOREIGN KEY(tenant_id,package_id) REFERENCES memoriesql.evidence_packages(tenant_id,package_id)
);
-- Acknowledged windows have one receipt per actual returned provider request.
CREATE TABLE memoriesql.complete_input_dispatch_receipts (
    tenant_id uuid NOT NULL,
    request_id uuid NOT NULL,
    frame_hash text NOT NULL,
    PRIMARY KEY(tenant_id,request_id),
    FOREIGN KEY(tenant_id,request_id) REFERENCES memoriesql.model_provider_request_intents(tenant_id,request_id)
);
CREATE FUNCTION memoriesql.guard_dispatch_policy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' AND OLD.status='active' AND NEW.status='revoked' AND
       to_jsonb(OLD)-'status'=to_jsonb(NEW)-'status' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'dispatch_policy_immutable' USING ERRCODE='55000';
END; $$;
CREATE TRIGGER complete_dispatch_policy_immutable BEFORE UPDATE OR DELETE ON memoriesql.complete_input_dispatch_policies FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_dispatch_policy();
CREATE TRIGGER complete_dispatch_authority_fence BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.complete_input_dispatch_policies FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER complete_execution_immutable BEFORE UPDATE OR DELETE ON memoriesql.complete_input_executions FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER complete_exposure_immutable BEFORE UPDATE OR DELETE ON memoriesql.complete_input_exposures FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER complete_dispatch_receipt_immutable BEFORE UPDATE OR DELETE ON memoriesql.complete_input_dispatch_receipts FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
ALTER TABLE memoriesql.complete_input_dispatch_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_dispatch_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_executions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_exposures ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_exposures FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_dispatch_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.complete_input_dispatch_receipts FORCE ROW LEVEL SECURITY;

-- Fresh snapshots are mandatory at each sensitive operation and after locks.
CREATE FUNCTION memoriesql.complete_input_authorize(t uuid, binding_id uuid, dispatch_id uuid)
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
       p.declaration#>>'{qualification,boundary}' IS DISTINCT FROM 'qualified_native_unit' OR
       p.declaration#>>'{qualification,physical_records}' IS DISTINCT FROM 'complete' OR
       p.declaration#>>'{qualification,normalized_input}' IS DISTINCT FROM 'complete' OR
       p.declaration#>>'{qualification,source_completeness}' IS DISTINCT FROM 'producer_attested' OR
       p.declaration#>'{qualification,unresolved_coverage}' IS DISTINCT FROM '[]'::jsonb OR
       NOT EXISTS(SELECT 1 FROM memoriesql.evidence_producer_policies q WHERE q.tenant_id=t AND q.producer_policy_id=b.producer_policy_id
          AND q.status='active' AND q.created_at<=clock_timestamp() AND q.expires_at>clock_timestamp()
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

CREATE FUNCTION memoriesql.activate_complete_input_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE; e memoriesql.complete_input_executions%ROWTYPE;
    original memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    rid uuid:=uuidv7(); tid uuid:=uuidv7(); eid uuid; input jsonb; result jsonb; request_hash text; started timestamptz:=clock_timestamp();
BEGIN
    IF request->>'contract_version' IS DISTINCT FROM '1' OR request->>'expected_schema_version' IS DISTINCT FROM '18' OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','binding_task_id','dispatch_policy_id']<>'{}'::jsonb OR
       COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR octet_length(request::text)>16384 THEN
        RAISE EXCEPTION 'invalid_complete_input_activation' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    b:=memoriesql.complete_input_authorize(c.tenant_id,(request->>'binding_task_id')::uuid,(request->>'dispatch_policy_id')::uuid);
    SELECT * INTO original FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=b.task_id;
    IF original.origin_principal_id<>c.principal_id OR NOT memoriesql.current_context_semantic_task_authorized(b.task_id,'memory.maintain','write') THEN
        RAISE EXCEPTION 'complete_input_activation_denied' USING ERRCODE='42501'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':complete-input-activate:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':complete-input-binding:'||b.task_id::text,0));
    -- Lock order: authority fence, operation key, binding key, original task.
    -- Cancellation may win any preceding wait; never transfer from stale state.
    SELECT * INTO original FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=b.task_id FOR UPDATE;
    b:=memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
    request_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    SELECT * INTO old FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='complete_input.activate.v1' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash<>request_hash THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old.response_receipt||'{"replayed":true}'::jsonb;
    END IF;
    -- Every acknowledged key owns a shared-ledger receipt, including natural replay.
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,b.access_scope_id,rid,'complete_input.activate.v1',request->>'idempotency_key',request_hash,'in_progress','semantic_task',b.task_id,1,started,started);
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND binding_task_id=b.task_id;
    IF FOUND THEN
        IF e.dispatch_policy_id<>(request->>'dispatch_policy_id')::uuid THEN RAISE EXCEPTION 'complete_input_activation_conflict' USING ERRCODE='23505'; END IF;
        SELECT response_receipt INTO result FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=e.activation_receipt_id;
        result:=result||jsonb_build_object('idempotency_receipt_id',rid,'replayed',true);
        PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
        UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
        RETURN result;
    END IF;
    IF original.status<>'policy_paused' OR original.pause_reason_code<>'complete_input_executor_unavailable' OR original.attempt_count<>0 OR original.cancel_requested_at IS NOT NULL THEN
        RAISE EXCEPTION 'complete_input_binding_not_transferable' USING ERRCODE='55000'; END IF;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id;
    INSERT INTO memoriesql.complete_input_executions VALUES(c.tenant_id,b.task_id,tid,(request->>'dispatch_policy_id')::uuid,(SELECT schema_version FROM memoriesql.source_objects WHERE tenant_id=c.tenant_id AND source_object_id=p.source_object_id),rid);
    IF memoriesql.cancel_semantic_task(c.tenant_id,b.task_id,'complete_input.execution_transferred',clock_timestamp())<>'cancelled' THEN
        RAISE EXCEPTION 'complete_input_transfer_failed' USING ERRCODE='55000'; END IF;
    input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',2,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||tid::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',request->>'dispatch_policy_id','declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_complete_input_exposure_v1'));
    SELECT q.idempotency_receipt_id INTO eid FROM memoriesql.enqueue_semantic_task(tid,'complete-input.v1:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',2,'5288a780557724d7c5be81afd28f50a0cc5e5b86a3c8e89ae241033e12f3f3fe','semantic-tasks-v1:7e8a296cf1143deb86715144ed06cceefcb28dcc9955892ba7df5a06ded8f71f',b.source_unit_id::text,0,input,input#>>'{evidence_manifest,manifest_id}',b.access_scope_id,started,b.task_id,started) q;
    result:=jsonb_build_object('contract_version',1,'binding_task_id',b.task_id,'execution_task_id',tid,'idempotency_receipt_id',rid,'enqueue_receipt_id',eid,'package',b.package_pin,'replayed',false);
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.activate_complete_input_v1(jsonb) TO memoriesql_application;

-- Reader v2 changes transport granularity only. Full retained parts, at most
-- eight/512KiB text + 64KiB metadata. Worst-case canonical escaping < 4MiB.
CREATE FUNCTION memoriesql.read_complete_evidence_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    result jsonb; items jsonb; n integer:=(request->>'next_ordinal')::integer; lim integer:=(request->>'limit')::integer;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN RAISE EXCEPTION 'complete_input_requires_read_committed' USING ERRCODE='25000'; END IF;
    IF request->>'contract_version' IS DISTINCT FROM '2' OR n IS NULL OR n NOT BETWEEN 0 AND 255 OR lim IS NULL OR lim NOT BETWEEN 1 AND 8 OR
       request-ARRAY['contract_version','package_id','inventory_sha256','next_ordinal','limit']<>'{}'::jsonb OR octet_length(request::text)>2048 THEN
        RAISE EXCEPTION 'invalid_complete_evidence_read' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    PERFORM memoriesql.evidence_package_authorize(p.source_object_id,false);
    IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM request->>'inventory_sha256' OR n>=p.part_count THEN
        RAISE EXCEPTION 'complete_evidence_pin_unavailable' USING ERRCODE='55000'; END IF;
    SELECT jsonb_agg(jsonb_build_object('inventory',inventory,'content',content) ORDER BY ordinal) INTO items
    FROM (SELECT ordinal,inventory,content FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=p.package_id AND ordinal>=n ORDER BY ordinal LIMIT lim) bounded;
    result:=jsonb_build_object('contract_version',2,'package_id',p.package_id,'inventory_sha256',p.inventory_hash,'parts',items,'next_ordinal',CASE WHEN n+jsonb_array_length(items)<p.part_count THEN n+jsonb_array_length(items) END);
    IF octet_length(result::text)>4194304 THEN RAISE EXCEPTION 'complete_evidence_response_bound' USING ERRCODE='54000'; END IF;
    PERFORM memoriesql.evidence_package_authorize(p.source_object_id,false);
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.read_complete_evidence_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_complete_evidence_v2(jsonb) TO memoriesql_application,memoriesql_worker;

-- Only an independently provisioned dispatch attestor may persist coverage.
-- The trusted adapter inspects the actual request messages after the provider
-- returns, not reader calls or self-reported model evidence. No table grants.
CREATE FUNCTION memoriesql.record_complete_input_exposure_v1(request_id uuid, payload_hash text, frame jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; e memoriesql.complete_input_executions%ROWTYPE;
    b memoriesql.logical_unit_materializations%ROWTYPE; i memoriesql.model_provider_request_intents%ROWTYPE;
    t memoriesql.semantic_tasks%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE;
    part memoriesql.evidence_package_parts%ROWTYPE; item jsonb; lo integer; hi integer; prior_end integer; fh text;
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO i FROM memoriesql.model_provider_request_intents WHERE tenant_id=c.tenant_id AND model_provider_request_intents.request_id=record_complete_input_exposure_v1.request_id;
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND execution_task_id=i.task_id;
    b:=memoriesql.complete_input_authorize(c.tenant_id,e.binding_task_id,e.dispatch_policy_id);
    IF c.principal_kind IS DISTINCT FROM 'service' OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
       WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=e.dispatch_policy_id AND d.attestor_principal_id=c.principal_id AND d.status='active' AND d.expires_at>clock_timestamp()) OR
       i.request_payload_hash IS DISTINCT FROM payload_hash OR i.task_id::text IS DISTINCT FROM frame->>'task_id' OR i.attempt_id::text IS DISTINCT FROM frame->>'attempt_id' OR
       b.package_pin IS DISTINCT FROM frame->'package' OR frame->>'contract_version' IS DISTINCT FROM '1' OR
       jsonb_typeof(frame->'slices') IS DISTINCT FROM 'array' OR jsonb_array_length(frame->'slices') NOT BETWEEN 1 AND 256 OR
       octet_length(frame::text)>262144 OR (SELECT sum(char_length(value->>'content')) FROM jsonb_array_elements(frame->'slices'))>16384 OR
       NOT EXISTS(SELECT 1 FROM memoriesql.model_usage_events u WHERE u.tenant_id=c.tenant_id AND u.request_id=i.request_id AND u.outcome='succeeded') THEN
        RAISE EXCEPTION 'trusted_exposure_required' USING ERRCODE='42501'; END IF;
    -- Task-row fence serializes with cancellation and reaping; authority is
    -- checked again after the wait. The old attempt cannot attest a new attempt.
    SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=i.task_id FOR UPDATE;
    SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id;
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,e.binding_task_id,e.dispatch_policy_id);
    IF c.principal_id=a.claimant_principal_id THEN RAISE EXCEPTION 'separate_dispatch_attestor_required' USING ERRCODE='42501'; END IF;
    IF t.status<>'running' OR a.status<>'running' OR t.lease_generation<>a.lease_generation OR t.cancel_requested_at IS NOT NULL OR
       t.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN RAISE EXCEPTION 'stale_exposure_attempt' USING ERRCODE='40001'; END IF;
    fh:=encode(sha256(convert_to(frame::text,'UTF8')),'hex');
    IF EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts r WHERE r.tenant_id=c.tenant_id AND r.request_id=i.request_id) THEN
        IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts r WHERE r.tenant_id=c.tenant_id AND r.request_id=i.request_id AND r.frame_hash=fh) THEN
            RAISE EXCEPTION 'exposure_replay_conflict' USING ERRCODE='23505'; END IF;
        RETURN true;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(frame->'slices') LOOP
        SELECT * INTO part FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=b.package_id AND part_id=(item#>>'{inventory,part_id}')::uuid;
        lo:=(item->>'start_character')::integer; hi:=lo+char_length(item->>'content');
        IF part.part_id IS NULL OR part.inventory IS DISTINCT FROM item->'inventory' OR lo IS NULL OR lo<0 OR hi<=lo OR hi>(part.inventory->>'characters')::integer OR
           substring(part.content FROM lo+1 FOR hi-lo) IS DISTINCT FROM item->>'content' THEN RAISE EXCEPTION 'exposure_evidence_mismatch' USING ERRCODE='22023'; END IF;
        SELECT COALESCE(max(end_character),0) INTO prior_end FROM memoriesql.complete_input_exposures WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id AND part_id=part.part_id;
        IF prior_end<>lo THEN RAISE EXCEPTION 'exposure_omitted_or_replayed_interval' USING ERRCODE='22023'; END IF;
        IF EXISTS(SELECT 1 FROM memoriesql.evidence_package_parts earlier WHERE earlier.tenant_id=c.tenant_id AND earlier.package_id=b.package_id AND earlier.ordinal=part.ordinal-1 AND
            NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=c.tenant_id AND x.attempt_id=i.attempt_id AND x.part_id=earlier.part_id AND x.end_character=(earlier.inventory->>'characters')::integer)) THEN
            RAISE EXCEPTION 'exposure_inventory_order_mismatch' USING ERRCODE='22023'; END IF;
        INSERT INTO memoriesql.complete_input_exposures VALUES(c.tenant_id,i.task_id,i.attempt_id,a.lease_generation,i.request_id,b.package_id,b.package_pin->>'inventory_sha256',part.part_id,lo,hi,encode(sha256(convert_to(item->>'content','UTF8')),'hex'),c.principal_id,e.dispatch_policy_id,clock_timestamp());
    END LOOP;
    INSERT INTO memoriesql.complete_input_dispatch_receipts VALUES(c.tenant_id,i.request_id,fh);
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,e.binding_task_id,e.dispatch_policy_id);
    RETURN true;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.record_complete_input_exposure_v1(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_complete_input_exposure_v1(uuid,text,jsonb) TO memoriesql_worker;

CREATE FUNCTION memoriesql.complete_input_exposure_valid(t uuid, task uuid, attempt uuid, generation bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
    SELECT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e JOIN memoriesql.logical_unit_materializations b ON b.tenant_id=e.tenant_id AND b.task_id=e.binding_task_id
    WHERE e.tenant_id=t AND e.execution_task_id=task AND NOT EXISTS(
       SELECT 1 FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id AND NOT EXISTS(
          SELECT 1 FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
          AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id AND x.end_character=(p.inventory->>'characters')::integer)))
$$;
REVOKE ALL ON FUNCTION memoriesql.complete_input_exposure_valid(uuid,uuid,uuid,bigint) FROM PUBLIC;

CREATE FUNCTION memoriesql.semantic_task_origin_capability_authorized(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_capability text,
    requested_checked_at timestamp with time zone
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.semantic_tasks AS task
        JOIN memoriesql.principals AS principal
          ON principal.tenant_id = task.tenant_id
         AND principal.principal_id = task.origin_principal_id
         AND principal.status = 'active'
        JOIN memoriesql.users AS origin_user
          ON origin_user.tenant_id = principal.tenant_id
         AND origin_user.user_id = principal.owner_user_id
         AND origin_user.status = 'active'
        JOIN memoriesql.workspace_memberships AS membership
          ON membership.tenant_id = task.tenant_id
         AND membership.workspace_id = task.workspace_id
         AND membership.principal_id = task.origin_principal_id
         AND membership.status = 'active'
        JOIN memoriesql.role_capabilities AS role_capability
          ON role_capability.role_key = membership.role_key
         AND role_capability.capability_key = requested_capability
        JOIN memoriesql.access_scopes AS scope
          ON scope.tenant_id = task.tenant_id
         AND scope.workspace_id = task.workspace_id
         AND scope.access_scope_id = task.access_scope_id
         AND scope.status = 'active'
        WHERE requested_checked_at IS NOT NULL
          AND task.tenant_id = requested_tenant_id
          AND task.task_id = requested_task_id
          AND (
              task.origin_pairing_grant_id IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM memoriesql.pairing_grants AS origin_pairing
                  JOIN memoriesql.pairing_grant_revisions AS origin_revision
                    ON origin_revision.tenant_id = origin_pairing.tenant_id
                   AND origin_revision.workspace_id = origin_pairing.workspace_id
                   AND origin_revision.pairing_grant_id =
                       origin_pairing.pairing_grant_id
                  WHERE origin_pairing.tenant_id = task.tenant_id
                    AND origin_pairing.workspace_id = task.workspace_id
                    AND origin_pairing.pairing_grant_id =
                        task.origin_pairing_grant_id
                    AND origin_pairing.paired_principal_id =
                        task.origin_principal_id
                    AND origin_revision.revision = (
                        SELECT max(latest.revision)
                        FROM memoriesql.pairing_grant_revisions AS latest
                        WHERE latest.tenant_id = origin_pairing.tenant_id
                          AND latest.pairing_grant_id =
                              origin_pairing.pairing_grant_id
                    )
                    AND origin_revision.status = 'active'
                    AND origin_revision.expires_at > requested_checked_at
                    AND requested_capability =
                        ANY(origin_revision.allowed_capabilities)
                    AND task.access_scope_id =
                        ANY(origin_revision.allowed_access_scope_ids)
              )
          )
          AND (
              (
                  scope.mode = 'owner_private'
                  AND (
                      (
                          principal.principal_kind = 'human'
                          AND principal.user_id = scope.owner_user_id
                          AND task.origin_pairing_grant_id IS NULL
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM memoriesql.pairing_grants AS pairing
                          JOIN memoriesql.pairing_grant_revisions AS revision
                            ON revision.tenant_id = pairing.tenant_id
                           AND revision.workspace_id = pairing.workspace_id
                           AND revision.pairing_grant_id = pairing.pairing_grant_id
                          WHERE pairing.tenant_id = task.tenant_id
                            AND pairing.workspace_id = task.workspace_id
                            AND pairing.pairing_grant_id = task.origin_pairing_grant_id
                            AND pairing.paired_principal_id = task.origin_principal_id
                            AND pairing.on_behalf_of_user_id = scope.owner_user_id
                            AND revision.revision = (
                                SELECT max(latest.revision)
                                FROM memoriesql.pairing_grant_revisions AS latest
                                WHERE latest.tenant_id = pairing.tenant_id
                                  AND latest.pairing_grant_id = pairing.pairing_grant_id
                            )
                            AND revision.status = 'active'
                            AND revision.expires_at > requested_checked_at
                            AND requested_capability = ANY(revision.allowed_capabilities)
                            AND task.access_scope_id = ANY(revision.allowed_access_scope_ids)
                      )
                  )
              )
              OR (
                  scope.mode = 'explicit'
                  AND EXISTS (
                      SELECT 1
                      FROM memoriesql.access_grants AS grant_record
                      JOIN memoriesql.access_grant_revisions AS revision
                        ON revision.tenant_id = grant_record.tenant_id
                       AND revision.workspace_id = grant_record.workspace_id
                       AND revision.access_scope_id = grant_record.access_scope_id
                       AND revision.grant_id = grant_record.grant_id
                      WHERE grant_record.tenant_id = task.tenant_id
                        AND grant_record.workspace_id = task.workspace_id
                        AND grant_record.access_scope_id = task.access_scope_id
                        AND grant_record.target_principal_id = task.origin_principal_id
                        AND revision.revision = (
                            SELECT max(latest.revision)
                            FROM memoriesql.access_grant_revisions AS latest
                            WHERE latest.tenant_id = revision.tenant_id
                              AND latest.grant_id = revision.grant_id
                        )
                        AND revision.status = 'active'
                        AND revision.valid_from <= requested_checked_at
                        AND (
                            revision.expires_at IS NULL
                            OR revision.expires_at > requested_checked_at
                        )
                        AND 'read' = ANY(revision.permission_keys)
                  )
              )
              OR scope.mode = 'workspace'
          )
    )
$$;
REVOKE ALL ON FUNCTION memoriesql.semantic_task_origin_capability_authorized(uuid,uuid,text,timestamptz) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.reauthorize_semantic_task(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_phase text,
    requested_at timestamp with time zone
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    source_revision integer;
    database_now timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR context_record.expires_at <= database_now
       OR requested_phase NOT IN ('hydrate', 'outcome')
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN 'invalid_phase';
    END IF;
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    JOIN memoriesql.semantic_task_attempts AS attempt
      ON attempt.tenant_id = task.tenant_id
     AND attempt.task_id = task.task_id
     AND attempt.attempt_id = requested_attempt_id
     AND attempt.lease_generation = requested_lease_generation
     AND attempt.claimant_principal_id = context_record.principal_id
     AND attempt.worker_id = requested_worker_id
     AND attempt.worker_instance_id = requested_worker_instance_id
     AND attempt.status IN ('claimed', 'running')
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND task.lease_expires_at > database_now
      AND attempt.deadline_at > database_now
      AND (
          requested_phase = 'outcome'
          OR task.cancel_requested_at IS NULL
      )
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      );
    IF NOT FOUND THEN
        RETURN 'stale_fence';
    END IF;
    IF NOT memoriesql.current_context_scope_time_authorized(
        task_record.access_scope_id, 'read', database_now
    ) THEN
        RETURN 'stale_fence';
    END IF;
    IF NOT memoriesql.semantic_task_origin_authorized(
        requested_tenant_id, requested_task_id, database_now
    ) THEN
        RETURN 'policy_paused';
    END IF;
    IF task_record.evidence_manifest_id IS DISTINCT FROM
           task_record.input_payload #>> '{evidence_manifest,manifest_id}'
       OR task_record.evidence_manifest_hash IS DISTINCT FROM encode(
           pg_catalog.sha256(
               pg_catalog.convert_to(
                   (task_record.input_payload -> 'evidence_manifest')::text,
                   'UTF8'
               )
           ),
           'hex'
       ) THEN
        RETURN 'stale_evidence';
    END IF;

    IF task_record.target_kind = 'source_object' THEN
        SELECT source.schema_version INTO source_revision
        FROM memoriesql.source_objects AS source
        JOIN memoriesql.protected_resources AS resource
          ON resource.tenant_id = source.tenant_id
         AND resource.workspace_id = source.workspace_id
         AND resource.access_scope_id = source.access_scope_id
         AND resource.resource_kind = 'source'
         AND resource.resource_id = source.source_object_id
         AND resource.status = 'active'
        WHERE source.tenant_id = task_record.tenant_id
          AND source.workspace_id = task_record.workspace_id
          AND source.access_scope_id = task_record.access_scope_id
          AND source.source_object_id::text = task_record.target_reference;
        IF NOT FOUND THEN
            RETURN 'evidence_unavailable';
        END IF;
        IF task_record.expected_target_revision > 0
           AND source_revision <> task_record.expected_target_revision THEN
            RETURN 'stale_evidence';
        END IF;
    END IF;
    IF task_record.task_kind='memory.semantic.author-complete-unit' AND task_record.contract_revision=2 THEN
        BEGIN
            PERFORM memoriesql.complete_input_authorize(requested_tenant_id,
                (task_record.input_payload#>>'{payload,binding_task_id}')::uuid,
                (task_record.input_payload#>>'{payload,dispatch_policy_id}')::uuid);
        EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                  WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence';
        END;
        -- Recheck the attempt after the authority fence wait, including expiry.
        IF NOT EXISTS(SELECT 1 FROM memoriesql.semantic_tasks q JOIN memoriesql.semantic_task_attempts a USING(tenant_id,task_id)
           WHERE q.tenant_id=requested_tenant_id AND q.task_id=requested_task_id AND a.attempt_id=requested_attempt_id
           AND q.lease_generation=requested_lease_generation AND a.lease_generation=requested_lease_generation
           AND q.status='running' AND a.status IN ('claimed','running') AND q.lease_expires_at>clock_timestamp()
           AND a.deadline_at>clock_timestamp() AND (requested_phase='outcome' OR q.cancel_requested_at IS NULL)) THEN RETURN 'stale_fence'; END IF;
    END IF;
    RETURN 'authorized';
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.semantic_task_input_reference_safe(
    candidate jsonb,
    maximum_bytes integer
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    manifest jsonb;
    evidence_references jsonb;
    reference_item jsonb;
    budget jsonb;
    budget_item record;
    payload jsonb;
    effective_maximum_bytes integer;
BEGIN
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision'='2' THEN
        RETURN octet_length(candidate::text)<=32768 AND candidate#>>'{payload,required_execution}'='trusted_complete_input_exposure_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb AND jsonb_array_length(candidate#>'{evidence_manifest,references}')=1
           AND jsonb_array_length(candidate#>'{payload,source_unit_ids}')=1 AND jsonb_array_length(candidate#>'{payload,bead_ids}')=1;
    END IF;
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' THEN RETURN maximum_bytes>0 AND octet_length(candidate::text)<=maximum_bytes AND memoriesql.complete_unit_input_valid(candidate); END IF;
    effective_maximum_bytes := maximum_bytes;
    IF candidate IS NULL
       OR maximum_bytes <= 0
       OR octet_length(candidate::text) > effective_maximum_bytes
       OR jsonb_typeof(candidate) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(candidate)) <> 9
       OR EXISTS (
           SELECT 1
           FROM jsonb_object_keys(candidate) AS input_key(key)
           WHERE input_key.key <> ALL (ARRAY[
               'task_id', 'task_kind', 'contract_revision',
               'target_reference', 'expected_target_revision',
               'evidence_manifest', 'requested_effort_key',
               'requested_budget', 'payload'
           ]::text[])
       ) THEN
        RETURN false;
    END IF;

    IF NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'task_id', 36
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'task_kind', 128
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'target_reference', 512
       )
       OR jsonb_typeof(candidate -> 'contract_revision') <> 'number'
       OR candidate ->> 'contract_revision' !~ '^[0-9]+$'
       OR (candidate ->> 'contract_revision')::numeric < 1
       OR jsonb_typeof(candidate -> 'expected_target_revision') <> 'number'
       OR candidate ->> 'expected_target_revision' !~ '^[0-9]+$'
       OR jsonb_typeof(candidate -> 'requested_effort_key')
            NOT IN ('null', 'string')
       OR (
           jsonb_typeof(candidate -> 'requested_effort_key') = 'string'
           AND NOT memoriesql.semantic_queue_reference_text_safe(
               candidate ->> 'requested_effort_key', 128
           )
       ) THEN
        RETURN false;
    END IF;

    manifest := candidate -> 'evidence_manifest';
    IF jsonb_typeof(manifest) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(manifest)) <> 3
       OR EXISTS (
           SELECT 1
           FROM jsonb_object_keys(manifest) AS manifest_key(key)
           WHERE manifest_key.key <> ALL (
               ARRAY['manifest_id', 'revision', 'references']::text[]
           )
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           manifest ->> 'manifest_id', 512
       )
       OR jsonb_typeof(manifest -> 'revision') <> 'number'
       OR manifest ->> 'revision' !~ '^[0-9]+$'
       OR (manifest ->> 'revision')::numeric < 1
       OR jsonb_typeof(manifest -> 'references') <> 'array'
       OR jsonb_array_length(manifest -> 'references') > 256 THEN
        RETURN false;
    END IF;
    evidence_references := manifest -> 'references';
    FOR reference_item IN
        SELECT value FROM jsonb_array_elements(evidence_references)
    LOOP
        IF jsonb_typeof(reference_item) <> 'object' THEN
            RETURN false;
        END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(reference_item)) <> 3
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(reference_item) AS reference_key(key)
               WHERE reference_key.key <> ALL (
                   ARRAY[
                       'reference_id', 'content_hash', 'declared_characters'
                   ]::text[]
               )
           )
           OR NOT memoriesql.semantic_queue_reference_text_safe(
               reference_item ->> 'reference_id', 512
           )
           OR reference_item ->> 'content_hash' !~ '^[a-f0-9]{64}$'
           OR jsonb_typeof(reference_item -> 'declared_characters') <> 'number'
           OR reference_item ->> 'declared_characters' !~ '^[0-9]+$' THEN
            RETURN false;
        END IF;
    END LOOP;
    IF (
        SELECT count(*) <> count(DISTINCT item ->> 'reference_id')
        FROM jsonb_array_elements(evidence_references) AS item
    ) THEN
        RETURN false;
    END IF;

    budget := candidate -> 'requested_budget';
    IF jsonb_typeof(budget) NOT IN ('null', 'object') THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(budget) = 'object' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(budget)) <> 13
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(budget) AS budget_key(key)
               WHERE budget_key.key <> ALL (ARRAY[
                   'wall_clock_seconds', 'request_limit',
                   'input_token_limit', 'output_token_limit',
                   'total_token_limit', 'tool_call_limit',
                   'cost_safety_limit_microusd', 'max_delegate_calls',
                   'max_parallel_delegates', 'evidence_item_limit',
                   'hydrated_character_limit', 'output_retries', 'tool_retries'
               ]::text[])
           ) THEN
            RETURN false;
        END IF;
        FOR budget_item IN SELECT key, value FROM jsonb_each(budget)
        LOOP
            IF budget_item.key = 'cost_safety_limit_microusd'
               AND jsonb_typeof(budget_item.value) = 'null' THEN
                CONTINUE;
            END IF;
            IF jsonb_typeof(budget_item.value) <> 'number'
               OR budget_item.value #>> '{}' !~ '^[0-9]+$' THEN
                RETURN false;
            END IF;
        END LOOP;
        IF (budget ->> 'wall_clock_seconds')::numeric < 1
           OR (budget ->> 'request_limit')::numeric < 1
           OR (budget ->> 'input_token_limit')::numeric < 1
           OR (budget ->> 'output_token_limit')::numeric < 1
           OR (budget ->> 'total_token_limit')::numeric < 1
           OR (budget ->> 'evidence_item_limit')::numeric < 1
           OR (budget ->> 'hydrated_character_limit')::numeric < 1
           OR (budget ->> 'total_token_limit')::numeric
                < (budget ->> 'input_token_limit')::numeric
           OR (budget ->> 'total_token_limit')::numeric
                < (budget ->> 'output_token_limit')::numeric
           OR (budget ->> 'max_parallel_delegates')::numeric
                > (budget ->> 'max_delegate_calls')::numeric THEN
            RETURN false;
        END IF;
    END IF;

    IF candidate ->> 'contract_revision' = '2'
       AND candidate ->> 'task_kind' IN ('memory.semantic.author-observations', 'memory.semantic.correct-observation')
       AND (jsonb_array_length(evidence_references) NOT BETWEEN 1 AND 8
            OR (SELECT COALESCE(sum((value ->> 'declared_characters')::numeric), 0)
                FROM jsonb_array_elements(evidence_references)) > 4096) THEN
        RETURN false;
    END IF;
    payload := candidate -> 'payload';
    IF candidate ->> 'task_kind' = 'memory.semantic.author-observations'
       AND candidate ->> 'contract_revision' IN ('1', '2') THEN
        IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 3
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(payload) AS payload_key(key)
               WHERE payload_key.key <> ALL (
                   ARRAY['event_id', 'bead_ids', 'source_unit_ids']::text[]
               )
           )
           OR payload ->> 'event_id' !~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(payload -> 'bead_ids') <> 'array'
           OR jsonb_typeof(payload -> 'source_unit_ids') <> 'array'
           OR jsonb_array_length(payload -> 'bead_ids') NOT BETWEEN 1 AND 8
           OR jsonb_array_length(payload -> 'bead_ids') <>
                jsonb_array_length(payload -> 'source_unit_ids')
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(payload -> 'bead_ids') AS item(value)
               WHERE jsonb_typeof(item.value) <> 'string'
                  OR item.value #>> '{}' !~
                     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(payload -> 'source_unit_ids')
                    AS item(value)
               WHERE jsonb_typeof(item.value) <> 'string'
                  OR item.value #>> '{}' !~
                     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR (
               SELECT count(*) <> count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(payload -> 'bead_ids') AS item(value)
           )
           OR (
               SELECT count(*) <> count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(payload -> 'source_unit_ids')
                    AS item(value)
           ) THEN
            RETURN false;
        END IF;
        RETURN true;
    END IF;
    IF candidate ->> 'task_kind' = 'memory.semantic.correct-observation'
       AND candidate ->> 'contract_revision' = '2' THEN
        IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 4
           OR NOT memoriesql.semantic_task_input_reference_safe(
                jsonb_set(jsonb_set(candidate, '{task_kind}',
                    '"memory.semantic.author-observations"'::jsonb),
                    '{payload}', payload - 'supersedes'), maximum_bytes)
           OR jsonb_array_length(payload -> 'bead_ids') <> 1
           OR jsonb_typeof(payload -> 'supersedes') IS DISTINCT FROM 'array'
           OR jsonb_array_length(payload -> 'supersedes') NOT BETWEEN 1 AND 8 THEN
            RETURN false;
        END IF;
        FOR reference_item IN SELECT value FROM jsonb_array_elements(payload -> 'supersedes')
        LOOP
            IF jsonb_typeof(reference_item) IS DISTINCT FROM 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(reference_item)) <> 2
               OR COALESCE(reference_item ->> 'bead_id', '') !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR COALESCE(reference_item ->> 'bead_version_id', '') !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR reference_item ->> 'bead_id' = payload #>> '{bead_ids,0}' THEN
                RETURN false;
            END IF;
        END LOOP;
        RETURN (SELECT count(*) = count(DISTINCT value ->> 'bead_id')
                FROM jsonb_array_elements(payload -> 'supersedes'));
    END IF;
    RETURN memoriesql.semantic_queue_payload_reference_scalar_safe(payload);
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.apply_semantic_annotations(
    requested_command jsonb,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_at timestamp with time zone
)
RETURNS TABLE (
    task_id uuid,
    attempt_id uuid,
    idempotency_receipt_id uuid,
    statement_ids uuid[],
    bead_version_ids uuid[],
    task_status text,
    replayed boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    bead_record memoriesql.beads%ROWTYPE;
    prior_statement memoriesql.bead_semantic_statements%ROWTYPE;
    is_v2 boolean;
    is_complete boolean;
    is_correction boolean;
    operation_key text;
    target_pin jsonb;
    annotation jsonb;
    statement jsonb;
    evidence jsonb;
    command_tenant_id uuid;
    command_workspace_id uuid;
    command_access_scope_id uuid;
    command_task_id uuid;
    command_attempt_id uuid;
    command_generation bigint;
    command_model_runs text[];
    computed_request_hash text;
    new_receipt_id uuid;
    new_task_receipt_id uuid;
    new_bead_version_id uuid;
    bead_type_uuid uuid;
    bead_type_revision_uuid uuid;
    current_bead_version integer;
    next_statement_sequence bigint;
    statement_uuid uuid;
    evidence_event_uuid uuid;
    outcome_status text;
    returned_statement_ids uuid[] := ARRAY[]::uuid[];
    returned_bead_version_ids uuid[] := ARRAY[]::uuid[];
    response jsonb;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    is_complete := requested_command->>'contract_version' IS NOT DISTINCT FROM '3';
    is_v2 := requested_command ->> 'contract_version' IS NOT DISTINCT FROM '2';
    is_correction := is_v2 AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM
        'memory.semantic.correct-observation';
    operation_key := CASE WHEN is_complete THEN 'complete_input.apply.v1' WHEN is_correction THEN 'observation_correction.apply.v2'
                          WHEN is_v2 THEN 'initial_observations.apply.v2'
                          ELSE 'semantic_annotations.apply' END;
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 4194304
       OR NOT (
            (requested_command ->> 'contract_version' IS NOT DISTINCT FROM '1'
             AND requested_command ->> 'expected_schema_version' IS NOT DISTINCT FROM '11'
             AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM 'memory.semantic.author-observations'
             AND requested_command ->> 'contract_revision' IS NOT DISTINCT FROM '1'
             AND requested_command ->> 'output_contract_hash' IS NOT DISTINCT FROM
                 'cfaf34db7deb7eb6e91169420b7ffd6fc7580292f658afa8c5c0c60eb9b597b4')
            OR (is_complete AND requested_command->>'expected_schema_version'='18'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit'
                AND requested_command->>'contract_revision'='2'
                AND requested_command->>'output_contract_hash'='7399c5039812e98d4d53566cb028350effb476a395a83c6fa17b8445ad9f1124')
            OR (is_v2
             AND requested_command ->> 'expected_schema_version' IS NOT DISTINCT FROM '15'
             AND requested_command ->> 'contract_revision' IS NOT DISTINCT FROM '2'
             AND ((requested_command ->> 'task_kind' IS NOT DISTINCT FROM 'memory.semantic.author-observations'
                   AND requested_command ->> 'output_contract_hash' IS NOT DISTINCT FROM
                       '3ba45185860773e367a43c8596ff8d354fd56442a389a4f95bc7a2c448c6d43c')
                  OR (is_correction AND requested_command ->> 'output_contract_hash' IS NOT DISTINCT FROM
                       'e6a5e62a3fc5669a1c6de044edd5e21f555a85e01789f11c6e762abb7ec6cb1b')))
       )
       OR requested_command ->> 'semantic_result_hash' !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(requested_command -> 'semantic_payload_canonical_json')
            IS DISTINCT FROM 'string'
       OR pg_catalog.octet_length(
            requested_command ->> 'semantic_payload_canonical_json'
          ) NOT BETWEEN 2 AND 12000
       OR requested_command ->> 'semantic_payload_canonical_json'
            IS DISTINCT FROM memoriesql.canonical_semantic_json_text(
                requested_command #> '{payload}'
            )
       OR encode(
            pg_catalog.sha256(pg_catalog.convert_to(
                requested_command ->> 'semantic_payload_canonical_json',
                'UTF8'
            )),
            'hex'
          ) IS DISTINCT FROM requested_command ->> 'semantic_result_hash'
       OR jsonb_typeof(requested_command #> '{payload,annotations}')
            IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command #> '{payload,annotations}')
            NOT BETWEEN 1 AND 8
       OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                requested_command #> '{payload,annotations}'
            ) AS annotation(value)
            WHERE jsonb_typeof(annotation.value -> 'statements')
                    IS DISTINCT FROM 'array'
               OR jsonb_array_length(annotation.value -> 'statements')
                    NOT BETWEEN 1 AND 64
               OR NOT memoriesql.authored_bead_render_safe(
                    annotation.value -> 'render'
               )
       )
       OR jsonb_typeof(requested_command -> 'used_evidence_refs')
            IS DISTINCT FROM 'array'
       OR jsonb_typeof(requested_command -> 'model_run_refs')
            IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command -> 'model_run_refs')
            NOT BETWEEN 1 AND 64
       OR btrim(requested_command ->> 'idempotency_key') = ''
       OR btrim(requested_worker_id) = ''
       OR btrim(requested_worker_instance_id) = ''
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'semantic annotation command is invalid'
            USING ERRCODE = '22023';
    END IF;
    command_tenant_id := (requested_command ->> 'tenant_id')::uuid;
    command_workspace_id := (requested_command ->> 'workspace_id')::uuid;
    command_access_scope_id := (requested_command ->> 'access_scope_id')::uuid;
    command_task_id := (requested_command ->> 'task_id')::uuid;
    command_attempt_id := (requested_command ->> 'attempt_id')::uuid;
    command_generation := (requested_command ->> 'lease_generation')::bigint;
    command_model_runs := ARRAY(SELECT value
        FROM jsonb_array_elements_text(
            requested_command -> 'model_run_refs'
        ) AS item(value));
    computed_request_hash := encode(
        pg_catalog.sha256(pg_catalog.convert_to(requested_command::text, 'UTF8')),
        'hex'
    );

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR context_record.tenant_id <> command_tenant_id
       OR context_record.workspace_id <> command_workspace_id
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.maintain', 'write'
       ) THEN
        RAISE EXCEPTION 'semantic annotation command is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    JOIN memoriesql.semantic_task_attempts AS attempt
      ON attempt.tenant_id = task.tenant_id
     AND attempt.task_id = task.task_id
     AND attempt.attempt_id = command_attempt_id
     AND attempt.lease_generation = command_generation
     AND attempt.claimant_principal_id = context_record.principal_id
     AND attempt.worker_id = requested_worker_id
     AND attempt.worker_instance_id = requested_worker_instance_id
    WHERE task.tenant_id = command_tenant_id
      AND task.workspace_id = command_workspace_id
      AND task.access_scope_id = command_access_scope_id
      AND task.task_id = command_task_id
      AND task.target_kind = 'canonical_semantics'
      AND task.task_kind = requested_command ->> 'task_kind'
      AND task.contract_revision =
          (requested_command ->> 'contract_revision')::integer;
    IF NOT FOUND
       OR NOT memoriesql.current_context_semantic_task_authorized(
            command_task_id, 'memory.maintain', 'write'
       )
       OR NOT memoriesql.semantic_task_origin_authorized(
            command_tenant_id, command_task_id, database_now
       ) THEN
        RAISE EXCEPTION 'semantic annotation task is unavailable'
            USING ERRCODE = '42501';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            command_tenant_id::text || ':' || operation_key || ':'
                || (requested_command ->> 'idempotency_key'),
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.maintain', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_semantic_task_authorized(
            command_task_id, 'memory.maintain', 'write'
       )
       OR NOT memoriesql.semantic_task_origin_authorized(
            command_tenant_id, command_task_id, database_now
       ) THEN
        RAISE EXCEPTION 'semantic annotation command is outside authorization'
            USING ERRCODE = '42501';
    END IF;
    IF is_complete THEN
        PERFORM memoriesql.complete_input_authorize(command_tenant_id,
            (task_record.input_payload#>>'{payload,binding_task_id}')::uuid,
            (task_record.input_payload#>>'{payload,dispatch_policy_id}')::uuid);
    END IF;
    SELECT * INTO receipt_record
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.operation_kind = operation_key
      AND receipt.idempotency_key = requested_command ->> 'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF receipt_record.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF receipt_record.status <> 'succeeded'
           OR receipt_record.response_receipt IS NULL THEN
            RAISE EXCEPTION 'semantic annotation receipt is incomplete'
                USING ERRCODE = '55000';
        END IF;
        response := receipt_record.response_receipt;
        RETURN QUERY SELECT
            (response ->> 'task_id')::uuid,
            (response ->> 'attempt_id')::uuid,
            receipt_record.idempotency_receipt_id,
            ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                response -> 'statement_ids'
            ) AS item(value)),
            ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                response -> 'bead_version_ids'
            ) AS item(value)),
            'succeeded'::text,
            true;
        RETURN;
    END IF;

    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = command_tenant_id
      AND task.task_id = command_task_id
      AND task.status = 'running'
      AND task.target_kind = 'canonical_semantics'
      AND task.lease_generation = command_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND task.result_attempt_id IS NULL
      AND task.cancel_requested_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    SELECT attempt.* INTO attempt_record
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = command_tenant_id
      AND attempt.task_id = command_task_id
      AND attempt.attempt_id = command_attempt_id
      AND attempt.lease_generation = command_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    IF NOT memoriesql.lock_semantic_task_outcome_authority(
        command_tenant_id, command_task_id
    ) THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR attempt_record.deadline_at <= database_now
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR memoriesql.reauthorize_semantic_task(
            command_tenant_id, command_task_id, command_attempt_id,
            command_generation, requested_worker_id,
            requested_worker_instance_id, 'outcome', requested_at
       ) <> 'authorized' THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    IF is_complete AND (
        NOT memoriesql.complete_input_exposure_valid(command_tenant_id,command_task_id,command_attempt_id,command_generation)
        OR jsonb_array_length(requested_command#>'{payload,annotations}')<>1
        OR requested_command#>>'{payload,annotations,0,expected_bead_version}' IS DISTINCT FROM '0'
        OR requested_command#>>'{payload,annotations,0,event_id}' IS DISTINCT FROM task_record.input_payload#>>'{payload,event_id}'
        OR requested_command#>>'{payload,annotations,0,source_unit_id}' IS DISTINCT FROM task_record.input_payload#>>'{payload,source_unit_ids,0}'
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(requested_command#>'{payload,annotations,0,statements}') q WHERE q->>'statement_kind'='correction')
    ) THEN RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
    IF cardinality(command_model_runs) <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.task_id = command_task_id
          AND run.attempt_id = command_attempt_id
          AND run.lease_generation = command_generation
          AND run.run_id = ANY(command_model_runs)
    ) OR cardinality(command_model_runs) <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.task_id = command_task_id
          AND run.attempt_id = command_attempt_id
          AND run.lease_generation = command_generation
    ) OR EXISTS (
        SELECT 1 FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND run.parent_run_id IS NOT NULL
          AND NOT run.settled
    ) OR 1 <> (
        SELECT count(*) FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND run.parent_run_id IS NULL
          AND run.run_status = 'running'
          AND run.run_id = ANY(command_model_runs)
    ) THEN
        RAISE EXCEPTION 'semantic annotation run tree is incomplete'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(requested_command #> '{payload,annotations}') <>
          jsonb_array_length(task_record.input_payload #> '{payload,bead_ids}')
       OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
                task_record.input_payload #> '{payload,bead_ids}'
            ) AS requested(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    requested_command #> '{payload,annotations}'
                ) AS annotation(value)
                WHERE annotation.value ->> 'bead_id' = requested.value
            )
       )
       OR EXISTS (
            SELECT annotation.value ->> 'bead_id'
            FROM jsonb_array_elements(
                requested_command #> '{payload,annotations}'
            ) AS annotation(value)
            GROUP BY annotation.value ->> 'bead_id'
            HAVING count(*) <> 1
       ) THEN
        RAISE EXCEPTION 'semantic annotation result does not cover its task'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(
            requested_command -> 'used_evidence_refs'
        ) AS used(value)
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                task_record.input_payload #> '{evidence_manifest,references}'
            ) AS declared(value)
            WHERE declared.value ->> 'reference_id' = used.value
        )
    ) THEN
        RAISE EXCEPTION 'evidence_hash_mismatch' USING ERRCODE = '22023';
    END IF;

    -- Explicit v2 input/output identity and prior-target pins. There is no
    -- comparison to a global head, unrelated source revision or branch winner.
    IF is_v2 THEN
        IF NOT memoriesql.semantic_task_input_reference_safe(task_record.input_payload, 32768)
           OR jsonb_array_length(requested_command -> 'used_evidence_refs') NOT BETWEEN 1 AND 8
           OR (SELECT count(*) <> count(DISTINCT value)
               FROM jsonb_array_elements_text(requested_command -> 'used_evidence_refs'))
           OR (SELECT COALESCE(sum(char_length(unit.content_text)),0) > 4096
                       OR COALESCE(sum(octet_length(memoriesql.canonical_semantic_json_text(to_jsonb(unit.content_text))) - 2),0) > 4096
               FROM memoriesql.source_units AS unit
               WHERE unit.tenant_id=command_tenant_id
                 AND unit.workspace_id=command_workspace_id
                 AND unit.access_scope_id=command_access_scope_id
                 AND unit.source_unit_id::text IN (SELECT jsonb_array_elements_text(requested_command -> 'used_evidence_refs'))) THEN
            RAISE EXCEPTION 'v2 observation evidence exceeds the existing bounded envelope'
                USING ERRCODE = '22023';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(requested_command #> '{payload,annotations}') AS item(value)
            WHERE value ->> 'expected_bead_version' IS DISTINCT FROM '0'
               OR value ->> 'event_id' IS DISTINCT FROM task_record.input_payload #>> '{payload,event_id}'
               OR value ->> 'source_unit_id' NOT IN (
                   SELECT jsonb_array_elements_text(task_record.input_payload #> '{payload,source_unit_ids}')
               )
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(value -> 'statements') AS s(value)
                          WHERE s.value ->> 'statement_kind' = 'correction')
        ) THEN
            RAISE EXCEPTION 'v2 requires complete initial semantics for distinct beads'
                USING ERRCODE = '22023';
        END IF;
        IF is_correction THEN
            IF requested_command #> '{payload,supersedes}' IS DISTINCT FROM
                    task_record.input_payload #> '{payload,supersedes}'
               OR NOT memoriesql.semantic_task_input_reference_safe(task_record.input_payload, 32768)
               OR jsonb_array_length(requested_command #> '{payload,annotations}') <> 1
               OR COALESCE(btrim(requested_command #>> '{payload,correction_reason}'), '') = ''
               OR char_length(requested_command #>> '{payload,correction_reason}') > 1024 THEN
                RAISE EXCEPTION 'correction targets do not match the authorized task'
                    USING ERRCODE = '22023';
            END IF;
            FOR target_pin IN SELECT value FROM jsonb_array_elements(
                requested_command #> '{payload,supersedes}'
            ) ORDER BY value ->> 'bead_id'
            LOOP
                IF NOT EXISTS (
                    SELECT 1 FROM memoriesql.accepted_bead_semantics AS accepted
                    WHERE accepted.tenant_id = command_tenant_id
                      AND accepted.workspace_id = command_workspace_id
                      AND accepted.access_scope_id = command_access_scope_id
                      AND accepted.bead_id = (target_pin ->> 'bead_id')::uuid
                      AND accepted.bead_version_id = (target_pin ->> 'bead_version_id')::uuid
                ) OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                    command_tenant_id, command_workspace_id, command_access_scope_id,
                    (target_pin ->> 'bead_version_id')::uuid
                ) THEN
                    RAISE EXCEPTION 'supersession target unavailable or stale'
                        USING ERRCODE = '42501';
                END IF;
            END LOOP;
            annotation := requested_command #> '{payload,annotations,0}';
            IF NOT EXISTS (
                SELECT 1 FROM memoriesql.source_units AS unit
                WHERE unit.tenant_id = command_tenant_id
                  AND unit.workspace_id = command_workspace_id
                  AND unit.access_scope_id = command_access_scope_id
                  AND unit.event_id = (annotation ->> 'event_id')::uuid
                  AND unit.source_unit_id = (annotation ->> 'source_unit_id')::uuid
                  AND unit.is_observation
            ) OR NOT memoriesql.current_context_event_authorized(
                command_access_scope_id, (annotation ->> 'event_id')::uuid,
                'memory.maintain', 'read'
            ) THEN
                RAISE EXCEPTION 'correction evidence anchor is unavailable'
                    USING ERRCODE = '42501';
            END IF;
            -- PK conflict rejects any preexisting ID; never adopt or edit it.
            INSERT INTO memoriesql.beads (
                tenant_id, workspace_id, access_scope_id, bead_id,
                event_id, source_unit_id, created_at, origin_kind
            ) VALUES (
                command_tenant_id, command_workspace_id, command_access_scope_id,
                (annotation ->> 'bead_id')::uuid, (annotation ->> 'event_id')::uuid,
                (annotation ->> 'source_unit_id')::uuid, database_now, 'correction'
            );
        END IF;
    END IF;

    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, operation_key,
        requested_command ->> 'idempotency_key', computed_request_hash,
        'in_progress', 'semantic_task', command_task_id, 1,
        database_now, database_now
    );

    FOR annotation IN
        SELECT item.value
        FROM jsonb_array_elements(
            requested_command #> '{payload,annotations}'
        ) AS item(value)
        ORDER BY item.value ->> 'bead_id'
    LOOP
        SELECT * INTO bead_record
        FROM memoriesql.beads AS bead
        WHERE bead.tenant_id = command_tenant_id
          AND bead.workspace_id = command_workspace_id
          AND bead.access_scope_id = command_access_scope_id
          AND bead.bead_id = (annotation ->> 'bead_id')::uuid
          AND bead.event_id = (annotation ->> 'event_id')::uuid
          AND bead.source_unit_id = (annotation ->> 'source_unit_id')::uuid
        FOR SHARE;
        IF NOT FOUND
           OR (is_v2 AND NOT is_correction AND bead_record.origin_kind <> 'initial')
           OR NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(
                    task_record.input_payload #> '{payload,bead_ids}'
                ) AS requested(value)
                WHERE requested.value = annotation ->> 'bead_id'
           ) THEN
            RAISE EXCEPTION 'semantic annotation bead is unavailable'
                USING ERRCODE = '42501';
        END IF;
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                command_tenant_id::text || ':bead-semantic:'
                    || bead_record.bead_id::text,
                0
            )
        );
        database_now := pg_catalog.clock_timestamp();
        IF context_record.expires_at <= database_now
           OR NOT memoriesql.current_context_scope_authorized(
                command_access_scope_id, 'memory.maintain', 'write'
           )
           OR NOT memoriesql.current_context_scope_time_authorized(
                command_access_scope_id, 'write', database_now
           )
           OR NOT memoriesql.current_context_semantic_task_authorized(
                command_task_id, 'memory.maintain', 'write'
           )
           OR NOT memoriesql.semantic_task_origin_authorized(
                command_tenant_id, command_task_id, database_now
           )
           OR memoriesql.reauthorize_semantic_task(
                command_tenant_id, command_task_id, command_attempt_id,
                command_generation, requested_worker_id,
                requested_worker_instance_id, 'outcome', database_now
           ) <> 'authorized' THEN
            RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
        END IF;
        IF is_correction AND (
            NOT memoriesql.current_context_scope_time_authorized(
                command_access_scope_id, 'read', database_now
            ) OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(requested_command #> '{payload,supersedes}') AS pin(value)
                WHERE NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                    command_tenant_id, command_workspace_id, command_access_scope_id,
                    (pin.value ->> 'bead_version_id')::uuid
                )
            )
        ) THEN
            RAISE EXCEPTION 'supersession target authorization expired' USING ERRCODE = '42501';
        END IF;
        SELECT COALESCE(max(version.version), 0)::integer
          INTO current_bead_version
          FROM memoriesql.bead_versions AS version
         WHERE version.tenant_id = command_tenant_id
           AND version.bead_id = bead_record.bead_id;
        IF current_bead_version <>
            (annotation ->> 'expected_bead_version')::integer THEN
            RAISE EXCEPTION 'bead_version_conflict' USING ERRCODE = '40001';
        END IF;
        IF current_bead_version = 0 AND (
            NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    annotation -> 'statements'
                ) AS initial_statement(value)
                WHERE initial_statement.value ->> 'statement_kind' =
                    'observation'
            ) OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    annotation -> 'statements'
                ) AS initial_statement(value)
                WHERE initial_statement.value ->> 'statement_kind' =
                        'observation'
                  AND NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            initial_statement.value -> 'evidence'
                        ) AS initial_evidence(value)
                        WHERE initial_evidence.value ->> 'source_unit_id' =
                            bead_record.source_unit_id::text
                  )
            )
        ) THEN
            RAISE EXCEPTION
                'initial semantic version requires a self-evidenced observation'
                USING ERRCODE = '22023';
        END IF;
        SELECT bead_type.bead_type_id, revision.bead_type_revision_id
          INTO bead_type_uuid, bead_type_revision_uuid
          FROM memoriesql.bead_types AS bead_type
          JOIN memoriesql.bead_type_revisions AS revision
            ON revision.bead_type_id = bead_type.bead_type_id
           AND revision.revision =
               (annotation ->> 'bead_type_revision')::integer
           AND revision.authorable
         WHERE bead_type.stable_key = annotation ->> 'bead_type_key';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'bead type revision is unavailable'
                USING ERRCODE = '22023';
        END IF;
        new_bead_version_id := (annotation ->> 'bead_version_id')::uuid;
        new_task_receipt_id := pg_catalog.uuidv7();
        INSERT INTO memoriesql.semantic_task_receipts (
            tenant_id, workspace_id, access_scope_id,
            semantic_task_receipt_id, idempotency_receipt_id, event_id,
            source_unit_id, bead_id, task_contract_key,
            task_contract_version, input_hash, created_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_task_receipt_id, new_receipt_id, bead_record.event_id,
            bead_record.source_unit_id, bead_record.bead_id,
            task_record.task_kind, task_record.contract_revision,
            task_record.input_hash, database_now
        );
        INSERT INTO memoriesql.bead_versions (
            tenant_id, workspace_id, access_scope_id, bead_version_id,
            bead_id, event_id, source_unit_id, version, bead_type_id,
            bead_type_revision_id, authored_by_principal_id,
            semantic_task_receipt_id, render_contract_revision,
            render_payload, authored_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_bead_version_id, bead_record.bead_id, bead_record.event_id,
            bead_record.source_unit_id, current_bead_version + 1,
            bead_type_uuid, bead_type_revision_uuid, context_record.principal_id,
            new_task_receipt_id, 2, annotation -> 'render', database_now
        );

        SELECT COALESCE(max(existing.statement_sequence), 0)
          INTO next_statement_sequence
          FROM memoriesql.bead_semantic_statements AS existing
         WHERE existing.tenant_id = command_tenant_id
           AND existing.bead_id = bead_record.bead_id;
        FOR statement IN
            SELECT item.value
            FROM jsonb_array_elements(annotation -> 'statements') AS item(value)
        LOOP
            next_statement_sequence := next_statement_sequence + 1;
            statement_uuid := (statement ->> 'statement_id')::uuid;
            IF statement ->> 'model_run_ref' <> ALL(command_model_runs) THEN
                RAISE EXCEPTION 'semantic statement run is unavailable'
                    USING ERRCODE = '22023';
            END IF;
            IF statement ->> 'statement_kind' = 'correction' THEN
                SELECT * INTO prior_statement
                FROM memoriesql.bead_semantic_statements AS prior
                WHERE prior.tenant_id = command_tenant_id
                  AND prior.statement_id =
                      (statement ->> 'supersedes_statement_id')::uuid
                  AND prior.bead_id = bead_record.bead_id
                FOR SHARE;
                IF NOT FOUND OR EXISTS (
                    SELECT 1
                    FROM memoriesql.bead_semantic_statements AS superseding
                    WHERE superseding.tenant_id = command_tenant_id
                      AND superseding.supersedes_statement_id =
                          prior_statement.statement_id
                ) THEN
                    RAISE EXCEPTION 'statement_supersession_conflict'
                        USING ERRCODE = '40001';
                END IF;
            END IF;
            INSERT INTO memoriesql.bead_semantic_statements (
                tenant_id, workspace_id, access_scope_id, statement_id,
                bead_id, bead_version_id, event_id, source_unit_id,
                statement_sequence, statement_kind, statement_text,
                context_source_ids, authored_by_principal_id,
                semantic_task_id, semantic_attempt_id, semantic_run_id,
                supersedes_statement_id, correction_reason, created_at
            ) VALUES (
                command_tenant_id, command_workspace_id,
                command_access_scope_id, statement_uuid, bead_record.bead_id,
                new_bead_version_id, bead_record.event_id,
                bead_record.source_unit_id, next_statement_sequence,
                statement ->> 'statement_kind', statement ->> 'statement_text',
                ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                    statement -> 'context_source_ids'
                ) AS item(value)),
                context_record.principal_id, command_task_id,
                command_attempt_id, statement ->> 'model_run_ref',
                NULLIF(statement ->> 'supersedes_statement_id', '')::uuid,
                NULLIF(statement ->> 'correction_reason', ''), database_now
            );

            FOR evidence IN
                SELECT item.value
                FROM jsonb_array_elements(statement -> 'evidence') AS item(value)
            LOOP
                SELECT unit.event_id INTO evidence_event_uuid
                FROM memoriesql.source_units AS unit
                WHERE unit.tenant_id = command_tenant_id
                  AND unit.workspace_id = command_workspace_id
                  AND unit.access_scope_id = command_access_scope_id
                  AND unit.source_unit_id =
                      (evidence ->> 'source_unit_id')::uuid
                  AND unit.content_hash = evidence ->> 'content_hash'
                  AND memoriesql.current_context_event_authorized(
                      unit.access_scope_id, unit.event_id,
                      'memory.maintain', 'read'
                  );
                IF NOT FOUND OR NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        task_record.input_payload #> '{evidence_manifest,references}'
                    ) AS declared(value)
                    WHERE declared.value ->> 'reference_id' =
                              evidence ->> 'source_unit_id'
                      AND declared.value ->> 'content_hash' =
                              evidence ->> 'content_hash'
                ) OR NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(
                        requested_command -> 'used_evidence_refs'
                    ) AS used(value)
                    WHERE used.value = evidence ->> 'source_unit_id'
                ) THEN
                    RAISE EXCEPTION 'evidence_hash_mismatch'
                        USING ERRCODE = '22023';
                END IF;
                INSERT INTO memoriesql.bead_semantic_statement_evidence (
                    tenant_id, workspace_id, access_scope_id, statement_id,
                    evidence_event_id, evidence_source_unit_id,
                    evidence_content_hash, linked_at
                ) VALUES (
                    command_tenant_id, command_workspace_id,
                    command_access_scope_id, statement_uuid,
                    evidence_event_uuid,
                    (evidence ->> 'source_unit_id')::uuid,
                    evidence ->> 'content_hash', database_now
                );
            END LOOP;
            IF EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(
                    statement -> 'context_source_ids'
                ) AS context_source(value)
                WHERE NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(
                        statement -> 'evidence'
                    ) AS linked(value)
                    WHERE linked.value ->> 'source_unit_id' =
                        context_source.value
                )
            ) THEN
                RAISE EXCEPTION 'context source lacks statement evidence'
                    USING ERRCODE = '22023';
            END IF;
            returned_statement_ids :=
                array_append(returned_statement_ids, statement_uuid);
        END LOOP;
        INSERT INTO memoriesql.bead_statement_revisions (
            tenant_id, workspace_id, access_scope_id, bead_version_id,
            bead_id, event_id, source_unit_id, statement_watermark,
            semantic_task_receipt_id, created_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_bead_version_id, bead_record.bead_id, bead_record.event_id,
            bead_record.source_unit_id, next_statement_sequence,
            new_task_receipt_id, database_now
        );
        returned_bead_version_ids :=
            array_append(returned_bead_version_ids, new_bead_version_id);
    END LOOP;

    IF is_correction THEN
        INSERT INTO memoriesql.bead_supersessions (
            tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id,
            superseded_bead_id, superseded_bead_version_id, correction_reason
        ) SELECT command_tenant_id, command_workspace_id, command_access_scope_id,
                 (requested_command #>> '{payload,annotations,0,bead_id}')::uuid,
                 (requested_command #>> '{payload,annotations,0,bead_version_id}')::uuid,
                 (pin.value ->> 'bead_id')::uuid, (pin.value ->> 'bead_version_id')::uuid,
                 requested_command #>> '{payload,correction_reason}'
          FROM jsonb_array_elements(requested_command #> '{payload,supersedes}') AS pin(value);
    END IF;

    outcome_status := memoriesql.record_semantic_task_outcome(
        command_tenant_id, command_task_id, command_attempt_id,
        command_generation, requested_worker_id, requested_worker_instance_id,
        'succeeded', requested_command ->> 'semantic_result_hash',
        'semantic.application.' || command_attempt_id::text,
        NULL, NULL, NULL, NULL, 0, requested_at
    );
    IF outcome_status <> 'succeeded' THEN
        RAISE EXCEPTION 'semantic task success settlement was rejected: %',
            outcome_status USING ERRCODE = '40001';
    END IF;
    UPDATE memoriesql.semantic_task_runs AS run
       SET run_status = 'succeeded', finished_at = database_now, settled = true
     WHERE run.tenant_id = command_tenant_id
       AND run.task_id = command_task_id
       AND run.attempt_id = command_attempt_id
       AND run.lease_generation = command_generation
       AND run.parent_run_id IS NULL
       AND run.run_status = 'running'
       AND run.run_id = ANY(command_model_runs);
    IF 1 <> (
        SELECT count(*) FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND run.parent_run_id IS NULL
          AND run.run_status = 'succeeded'
          AND run.settled
    ) OR EXISTS (
        SELECT 1 FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND NOT run.settled
    ) THEN
        RAISE EXCEPTION 'semantic annotation run tree did not settle'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id,
        idempotency_receipt_id, aggregate_kind, aggregate_id, event_kind,
        payload, headers, recorded_at, available_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        pg_catalog.uuidv7(), new_receipt_id, 'semantic_task', command_task_id,
        CASE WHEN is_v2 OR is_complete THEN operation_key ELSE 'semantic_annotations.applied' END,
        jsonb_build_object(
            'task_id', command_task_id,
            'attempt_id', command_attempt_id,
            'statement_count', cardinality(returned_statement_ids),
            'bead_version_count', cardinality(returned_bead_version_ids)
        ),
        jsonb_build_object('contract_version', CASE WHEN is_complete THEN 3 WHEN is_v2 THEN 2 ELSE 1 END), database_now, database_now
    );
    response := jsonb_build_object(
        'task_id', command_task_id,
        'attempt_id', command_attempt_id,
        'statement_ids', to_jsonb(returned_statement_ids),
        'bead_version_ids', to_jsonb(returned_bead_version_ids),
        'task_status', 'succeeded'
    );
    IF is_v2 THEN
        response := response || jsonb_build_object(
            'contract_version', 2, 'operation', operation_key,
            'bead_ids', (SELECT jsonb_agg(value -> 'bead_id' ORDER BY value ->> 'bead_id')
                         FROM jsonb_array_elements(requested_command #> '{payload,annotations}')),
            'supersedes', CASE WHEN is_correction THEN requested_command #> '{payload,supersedes}'
                              ELSE '[]'::jsonb END
        );
    END IF;
    UPDATE memoriesql.idempotency_receipts AS receipt
       SET status = 'succeeded', response_receipt = response,
           updated_at = database_now, completed_at = database_now
     WHERE receipt.tenant_id = command_tenant_id
       AND receipt.idempotency_receipt_id = new_receipt_id;

    RETURN QUERY SELECT command_task_id, command_attempt_id, new_receipt_id,
        returned_statement_ids, returned_bead_version_ids,
        'succeeded'::text, false;
END;
$$;

CREATE FUNCTION memoriesql.assert_complete_execution_binding() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,memoriesql AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    t memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; expected jsonb;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' OR NEW.contract_revision<>2 THEN RETURN NULL; END IF;
        SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=NEW.tenant_id AND execution_task_id=NEW.task_id;
    ELSE e:=NEW; END IF;
    SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=e.tenant_id AND task_id=e.execution_task_id;
    SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=e.tenant_id AND task_id=e.binding_task_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=e.tenant_id AND package_id=b.package_id;
    expected:=jsonb_build_object('task_id',t.task_id,'task_kind','memory.semantic.author-complete-unit','contract_revision',2,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||t.task_id::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',e.dispatch_policy_id,'declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_complete_input_exposure_v1'));
    IF e.execution_task_id IS NULL OR b.task_id IS NULL OR t.task_id IS NULL OR t.input_payload IS DISTINCT FROM expected OR t.rerun_of_task_id IS DISTINCT FROM b.task_id OR
       t.origin_principal_id IS DISTINCT FROM p.producer_principal_id OR t.access_scope_id IS DISTINCT FROM b.access_scope_id OR t.workspace_id IS DISTINCT FROM b.workspace_id OR
       t.task_contract_hash IS DISTINCT FROM '5288a780557724d7c5be81afd28f50a0cc5e5b86a3c8e89ae241033e12f3f3fe' OR
       t.semantic_registry_hash IS DISTINCT FROM 'semantic-tasks-v1:7e8a296cf1143deb86715144ed06cceefcb28dcc9955892ba7df5a06ded8f71f' THEN
        RAISE EXCEPTION 'complete_execution_binding_conflict' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER complete_execution_task_binding AFTER INSERT ON memoriesql.semantic_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_complete_execution_binding();
CREATE CONSTRAINT TRIGGER complete_execution_binding_ready AFTER INSERT ON memoriesql.complete_input_executions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_complete_execution_binding();

CREATE OR REPLACE FUNCTION memoriesql.guard_complete_unit_execution() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
DECLARE complete boolean; t memoriesql.semantic_tasks%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' THEN RETURN NEW; END IF;
        IF NEW.contract_revision=1 THEN
            IF TG_OP='INSERT' THEN NEW.status:='policy_paused'; NEW.pause_reason_code:='complete_input_executor_unavailable'; END IF;
            IF NEW.status NOT IN ('policy_paused','cancelled','failed_terminal') OR NEW.attempt_count<>0 OR NEW.lease_generation<>0 OR
               (NEW.status='policy_paused' AND NEW.pause_reason_code IS DISTINCT FROM 'complete_input_executor_unavailable') THEN
                RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
        ELSIF NEW.contract_revision=2 THEN
            IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=NEW.tenant_id AND e.execution_task_id=NEW.task_id) THEN
                RAISE EXCEPTION 'complete_input_activation_required' USING ERRCODE='55000'; END IF;
            IF NEW.status='succeeded' AND (NOT memoriesql.complete_input_exposure_valid(NEW.tenant_id,NEW.task_id,NEW.result_attempt_id,NEW.lease_generation) OR
                NOT EXISTS(SELECT 1 FROM memoriesql.bead_versions v JOIN memoriesql.bead_statement_revisions s USING(tenant_id,bead_version_id) JOIN memoriesql.semantic_task_receipts r ON r.tenant_id=v.tenant_id AND r.semantic_task_receipt_id=v.semantic_task_receipt_id JOIN memoriesql.idempotency_receipts i ON i.tenant_id=r.tenant_id AND i.idempotency_receipt_id=r.idempotency_receipt_id WHERE v.tenant_id=NEW.tenant_id AND v.bead_id=(NEW.input_payload#>>'{payload,bead_ids,0}')::uuid AND i.operation_kind='complete_input.apply.v1' AND i.resource_id=NEW.task_id)) THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        ELSE RAISE EXCEPTION 'complete_input_execution_unsupported' USING ERRCODE='55000'; END IF;
    ELSIF TG_TABLE_NAME='semantic_task_attempts' THEN
        SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
        IF t.task_kind='memory.semantic.author-complete-unit' AND (t.contract_revision<>2 OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t.tenant_id AND e.execution_task_id=t.task_id)) THEN
            RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
    ELSE
        SELECT u.materialization_version=1 INTO complete FROM memoriesql.beads b JOIN memoriesql.source_units u USING(tenant_id,source_unit_id) WHERE b.tenant_id=NEW.tenant_id AND b.bead_id=NEW.bead_id;
        IF complete THEN
            SELECT q.* INTO t FROM memoriesql.semantic_task_receipts r JOIN memoriesql.idempotency_receipts i USING(tenant_id,idempotency_receipt_id)
            JOIN memoriesql.semantic_tasks q ON q.tenant_id=i.tenant_id AND q.task_id=i.resource_id
            WHERE r.tenant_id=NEW.tenant_id AND r.semantic_task_receipt_id=NEW.semantic_task_receipt_id AND i.operation_kind='complete_input.apply.v1';
            SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=t.tenant_id AND task_id=t.task_id AND lease_generation=t.lease_generation AND status='running';
            IF t.task_id IS NULL OR t.task_kind<>'memory.semantic.author-complete-unit' OR t.contract_revision<>2 OR t.status<>'running' OR t.cancel_requested_at IS NOT NULL OR
               NEW.bead_id::text IS DISTINCT FROM t.input_payload#>>'{payload,bead_ids,0}' OR NEW.version<>1 OR
               NOT memoriesql.complete_input_exposure_valid(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation) OR
               memoriesql.reauthorize_semantic_task(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation,t.lease_owner,t.worker_instance_id,'hydrate',clock_timestamp())<>'authorized' THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.assert_complete_unit_binding() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
DECLARE binding memoriesql.logical_unit_materializations%ROWTYPE; task memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; u memoriesql.source_units%ROWTYPE; pin jsonb;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' OR NEW.contract_revision<>1 THEN RETURN NULL; END IF;
        SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
    ELSE binding:=NEW; END IF;
    IF binding.source_unit_id IS NULL THEN RAISE EXCEPTION 'complete_unit_binding_required' USING ERRCODE='23514'; END IF;
    SELECT * INTO task FROM memoriesql.semantic_tasks WHERE tenant_id=binding.tenant_id AND task_id=binding.task_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=binding.tenant_id AND package_id=binding.package_id;
    SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=binding.tenant_id AND source_unit_id=binding.source_unit_id;
    pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
    IF p.sealed_receipt_id IS NULL OR binding.package_pin IS DISTINCT FROM pin OR binding.occurrence_hash IS DISTINCT FROM p.occurrence_hash OR u.materialization_version IS DISTINCT FROM 1 OR u.event_id IS DISTINCT FROM binding.event_id OR u.content_hash IS DISTINCT FROM p.inventory_hash OR u.processing_receipt_id IS DISTINCT FROM binding.initial_receipt_id OR
       task.task_kind IS DISTINCT FROM 'memory.semantic.author-complete-unit' OR task.workspace_id IS DISTINCT FROM binding.workspace_id OR task.access_scope_id IS DISTINCT FROM binding.access_scope_id OR NOT memoriesql.complete_unit_input_valid(task.input_payload) OR
       task.input_payload->'payload' IS DISTINCT FROM jsonb_build_object('source_object_id',p.source_object_id,'event_id',binding.event_id,'source_unit_id',binding.source_unit_id,'bead_id',binding.bead_id,'materialization_receipt_id',binding.initial_receipt_id,'producer_policy_id',binding.producer_policy_id,'package',pin,'required_execution','trusted_complete_input_exposure_validation','execution_availability','unavailable') THEN
        RAISE EXCEPTION 'complete_unit_binding_conflict' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END; $$;
INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES (
'semantic-tasks-v1:7e8a296cf1143deb86715144ed06cceefcb28dcc9955892ba7df5a06ded8f71f','memory.semantic.author-complete-unit',2,'memoriesql.kernel','5288a780557724d7c5be81afd28f50a0cc5e5b86a3c8e89ae241033e12f3f3fe','canonical_semantics','memory.capture','capture',50,3,NULL,NULL);
CREATE FUNCTION memoriesql.validate_complete_input_acceptance(t uuid,task uuid,attempt uuid,generation bigint,worker text,instance text) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
BEGIN
    IF memoriesql.reauthorize_semantic_task(t,task,attempt,generation,worker,instance,'hydrate',clock_timestamp())<>'authorized' THEN RETURN false; END IF;
    RETURN memoriesql.complete_input_exposure_valid(t,task,attempt,generation);
END; $$;
REVOKE ALL ON FUNCTION memoriesql.validate_complete_input_acceptance(uuid,uuid,uuid,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.validate_complete_input_acceptance(uuid,uuid,uuid,bigint,text,text) TO memoriesql_worker;
