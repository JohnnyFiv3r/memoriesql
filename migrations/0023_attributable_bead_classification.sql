-- Forward-only classified authorship. Restore the pre-upgrade backup to roll back.
-- No historical SQL/contract edits, provider names, new store or inference-on-read.
ALTER TABLE memoriesql.bead_versions ADD COLUMN classification_contribution jsonb;
ALTER TABLE memoriesql.complete_input_executions ADD COLUMN classification_vocabulary jsonb;
ALTER TABLE memoriesql.complete_input_dispatch_receipts DROP CONSTRAINT complete_input_dispatch_receipts_delivery_version_check,
 ADD CONSTRAINT complete_input_dispatch_receipts_delivery_version_check CHECK(delivery_version IN (1,2,3));
CREATE FUNCTION memoriesql.classification_sha(value jsonb) RETURNS text LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog,memoriesql AS $$ SELECT encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(value),'UTF8')),'hex') $$;
REVOKE ALL ON FUNCTION memoriesql.classification_sha(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.record_classification_v1(request_id uuid, payload_hash text, packet jsonb, decision jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; i memoriesql.model_provider_request_intents%ROWTYPE;
 e memoriesql.complete_input_executions%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE;
 q memoriesql.semantic_tasks%ROWTYPE; item jsonb; actual jsonb; contribution jsonb; refs jsonb:='[]'; units integer:=0;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO i FROM memoriesql.model_provider_request_intents WHERE tenant_id=c.tenant_id AND model_provider_request_intents.request_id=record_classification_v1.request_id;
 SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND execution_task_id=i.task_id;
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 SELECT * INTO q FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=i.task_id FOR UPDATE;
 SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id;
 IF c.principal_kind IS DISTINCT FROM 'service' OR c.principal_id=a.claimant_principal_id
 OR e.execution_contract_revision IS DISTINCT FROM 5 OR i.run_role IS DISTINCT FROM 'delegate'
 OR i.parent_run_id IS DISTINCT FROM packet->>'author_run_id'
 OR i.request_payload_hash IS DISTINCT FROM payload_hash OR i.task_id::text IS DISTINCT FROM packet->>'task_id'
 OR i.attempt_id::text IS DISTINCT FROM packet->>'attempt_id' OR packet->'contract_version' IS DISTINCT FROM '1'::jsonb
 OR q.status IS DISTINCT FROM 'running' OR a.status IS DISTINCT FROM 'running' OR q.cancel_requested_at IS NOT NULL
 OR q.lease_generation IS DISTINCT FROM a.lease_generation OR q.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=e.dispatch_policy_id
     AND d.attestor_principal_id=c.principal_id AND d.status='active' AND d.expires_at>clock_timestamp() AND d.execution_contract_revision=5)
 OR NOT EXISTS(SELECT 1 FROM memoriesql.semantic_task_runs r WHERE r.tenant_id=c.tenant_id AND r.task_id=i.task_id AND r.attempt_id=i.attempt_id
     AND r.run_id=i.run_id AND r.parent_run_id=i.parent_run_id AND r.run_status='running' AND r.agent_key='memory.semantic.bead-type-classifier')
 OR NOT EXISTS(SELECT 1 FROM memoriesql.model_usage_events u WHERE u.tenant_id=c.tenant_id AND u.request_id=i.request_id AND u.outcome='succeeded')
 OR NOT memoriesql.complete_input_exposure_valid(c.tenant_id,i.task_id,i.attempt_id,a.lease_generation)
 THEN RAISE EXCEPTION 'trusted_classification_required' USING ERRCODE='42501'; END IF;
 IF packet-'contract_version'-'task_id'-'attempt_id'-'author_run_id'-'proposal'-'vocabulary'-'evidence'<>'{}'::jsonb
 OR octet_length(memoriesql.canonical_semantic_json_text(packet))>262144
 OR packet->'vocabulary' IS DISTINCT FROM e.classification_vocabulary
 OR jsonb_typeof(packet->'evidence') IS DISTINCT FROM 'array' OR jsonb_array_length(packet->'evidence') NOT BETWEEN 1 AND 8
 OR jsonb_typeof(packet#>'{proposal,annotations}') IS DISTINCT FROM 'array' OR jsonb_array_length(packet#>'{proposal,annotations}')<>1
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(packet#>'{proposal,annotations,0,statements}') st WHERE st->>'model_run_ref' IS DISTINCT FROM i.parent_run_id)
 OR packet#>>'{proposal,annotations,0,bead_id}' IS DISTINCT FROM q.input_payload#>>'{payload,bead_ids,0}'
 OR decision-'contract_version'-'outcome'-'selected_type'-'proposal_alignment'-'rationale'-'distribution'-'confidence'-'alignment_distribution'-'alignment_confidence'<>'{}'::jsonb
 OR decision->'contract_version' IS DISTINCT FROM '1'::jsonb
 OR COALESCE(decision->>'proposal_alignment','') NOT IN ('consistent','conflicting','uncertain')
 OR COALESCE(decision->>'outcome','') NOT IN ('selected','no_fit','insufficient_evidence','ambiguous')
 OR (decision->'rationale' IS DISTINCT FROM 'null'::jsonb AND (jsonb_typeof(decision->'rationale') IS DISTINCT FROM 'string' OR char_length(decision->>'rationale') NOT BETWEEN 1 AND 2048 OR btrim(decision->>'rationale')='')) OR octet_length(memoriesql.canonical_semantic_json_text(decision))>12000
 OR jsonb_typeof(decision->'distribution') IS DISTINCT FROM 'array' OR jsonb_array_length(decision->'distribution')>35
 OR jsonb_typeof(decision->'alignment_distribution') IS DISTINCT FROM 'array' OR jsonb_array_length(decision->'alignment_distribution')>3
 OR EXISTS(SELECT 1 FROM jsonb_each(decision) f WHERE f.key IN ('confidence','alignment_confidence') AND f.value<>'null'::jsonb AND (jsonb_typeof(f.value)<>'number' OR f.value::numeric NOT BETWEEN 0 AND 1))
 OR ((decision->>'outcome'='selected') IS DISTINCT FROM (jsonb_typeof(decision->'selected_type')='object'))
 OR (decision->>'outcome'<>'selected' AND decision->'selected_type' IS DISTINCT FROM 'null'::jsonb)
 THEN RAISE EXCEPTION 'classification_packet_invalid' USING ERRCODE='22023'; END IF;
 IF decision->>'outcome'='selected' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(e.classification_vocabulary) d
    WHERE d-'definition'=decision->'selected_type') THEN RAISE EXCEPTION 'classification_label_unregistered' USING ERRCODE='22023'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(decision->'distribution') LOOP
  IF item-ARRAY['type','score']<>'{}'::jsonb OR jsonb_typeof(item->'score') IS DISTINCT FROM 'number'
   OR (item->>'score')::numeric NOT BETWEEN 0 AND 1 OR (NOT EXISTS(SELECT 1 FROM jsonb_array_elements(e.classification_vocabulary) d WHERE d-'definition'=item->'type') AND COALESCE(item->>'type','') NOT IN ('no_fit','insufficient_evidence','ambiguous'))
  THEN RAISE EXCEPTION 'classification_diagnostic_invalid' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(decision->'distribution') d GROUP BY d->'type' HAVING count(*)>1) THEN
  RAISE EXCEPTION 'classification_diagnostic_invalid' USING ERRCODE='22023'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(decision->'alignment_distribution') LOOP
  IF item-ARRAY['alignment','score']<>'{}'::jsonb OR COALESCE(item->>'alignment','') NOT IN ('consistent','conflicting','uncertain')
   OR jsonb_typeof(item->'score') IS DISTINCT FROM 'number' OR (item->>'score')::numeric NOT BETWEEN 0 AND 1 THEN
   RAISE EXCEPTION 'classification_diagnostic_invalid' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(decision->'alignment_distribution') d GROUP BY d->'alignment' HAVING count(*)>1) THEN
  RAISE EXCEPTION 'classification_diagnostic_invalid' USING ERRCODE='22023'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(packet->'evidence') LOOP
  actual:=memoriesql.source_revisiting_read(c.tenant_id,i.task_id,item->'request');
  IF actual IS DISTINCT FROM item THEN RAISE EXCEPTION 'classification_evidence_mismatch' USING ERRCODE='22023'; END IF;
  -- Optional context/raw support must have been delivered to the primary, not just fetched for the classifier.
  IF NOT (item#>>'{request,representation}'='normalized' AND item#>>'{request,package_id}'=q.input_payload#>>'{payload,package,package_id}')
   AND NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d JOIN memoriesql.model_provider_request_intents p USING(tenant_id,request_id)
      WHERE p.tenant_id=c.tenant_id AND p.task_id=i.task_id AND p.attempt_id=i.attempt_id AND p.run_id=i.parent_run_id
       AND d.delivery_version=2 AND d.selection=item->'request') THEN
   RAISE EXCEPTION 'classification_support_not_author_exposed' USING ERRCODE='42501'; END IF;
  units:=units+CASE WHEN item#>>'{request,representation}'='raw' THEN octet_length(decode(item->>'bytes_hex','hex')) ELSE char_length(item->>'content') END;
  refs:=refs||jsonb_build_array(jsonb_build_object('selection',item->'request','sha256',item->'sha256'));
 END LOOP;
 IF (SELECT count(*)<>1 FROM memoriesql.model_provider_request_intents p WHERE p.tenant_id=c.tenant_id AND p.task_id=i.task_id AND p.attempt_id=i.attempt_id AND p.run_id=i.run_id)
  OR (SELECT COALESCE(sum(d.delivered_units),0)+units>262144 FROM memoriesql.complete_input_dispatch_receipts d JOIN memoriesql.model_provider_request_intents p USING(tenant_id,request_id)
       WHERE p.tenant_id=c.tenant_id AND p.task_id=i.task_id AND p.attempt_id=i.attempt_id AND p.request_id<>i.request_id)
 THEN RAISE EXCEPTION 'classification_dispatch_bound' USING ERRCODE='54000'; END IF;
 contribution:=jsonb_build_object('contract_version',1,'request_id',i.request_id,'model_run_ref',i.run_id,'author_run_ref',i.parent_run_id,
   'packet_sha256',memoriesql.classification_sha(packet),'proposal_sha256',memoriesql.classification_sha(packet->'proposal'),
   'vocabulary_sha256',memoriesql.classification_sha(packet->'vocabulary'),'evidence',refs,'decision',decision);
 IF EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d WHERE d.tenant_id=c.tenant_id AND d.request_id=i.request_id) THEN
  IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d WHERE d.tenant_id=c.tenant_id AND d.request_id=i.request_id AND d.delivery_version=3 AND d.selection=contribution
       AND d.frame_hash=memoriesql.classification_sha(packet)) THEN RAISE EXCEPTION 'classification_replay_conflict' USING ERRCODE='23505'; END IF;
 ELSE
  INSERT INTO memoriesql.complete_input_dispatch_receipts VALUES(c.tenant_id,i.request_id,memoriesql.classification_sha(packet),3,contribution,units);
 END IF;
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 IF q.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN RAISE EXCEPTION 'classification_stale_attempt' USING ERRCODE='42501'; END IF;
 RETURN true;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.record_classification_v1(uuid,text,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_classification_v1(uuid,text,jsonb,jsonb) TO memoriesql_worker;

CREATE FUNCTION memoriesql.validate_classification_acceptance(t uuid, task uuid, attempt uuid, contribution jsonb, annotations jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
DECLARE item jsonb;
BEGIN
 IF contribution IS NULL OR contribution#>>'{decision,proposal_alignment}' IS DISTINCT FROM 'consistent'
 OR contribution#>>'{decision,outcome}' IS DISTINCT FROM 'selected'
 OR contribution#>'{decision,selected_type}' IS DISTINCT FROM jsonb_build_object('key',annotations#>'{0,bead_type_key}','revision',annotations#>'{0,bead_type_revision}')
 OR contribution->>'proposal_sha256' IS DISTINCT FROM memoriesql.classification_sha(jsonb_build_object('annotations',annotations))
 OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d JOIN memoriesql.model_provider_request_intents i USING(tenant_id,request_id)
    JOIN memoriesql.semantic_task_runs r ON r.tenant_id=i.tenant_id AND r.task_id=i.task_id AND r.attempt_id=i.attempt_id AND r.run_id=i.run_id
    WHERE i.tenant_id=t AND i.task_id=task AND i.attempt_id=attempt AND d.delivery_version=3 AND d.selection=contribution
     AND i.request_id::text=contribution->>'request_id' AND r.run_status='succeeded' AND r.settled)
 THEN RAISE EXCEPTION 'classification_acceptance_required' USING ERRCODE='22023'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(contribution->'evidence') LOOP
  PERFORM memoriesql.source_revisiting_authorize(t,task,(item#>>'{selection,package_id}')::uuid);
 END LOOP;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.validate_classification_acceptance(uuid,uuid,uuid,jsonb,jsonb) FROM PUBLIC;
ALTER TABLE memoriesql.complete_input_executions
 DROP CONSTRAINT complete_input_executions_execution_contract_revision_check,
 ADD CONSTRAINT complete_input_executions_execution_contract_revision_check CHECK(execution_contract_revision IN (2,3,4,5));
ALTER TABLE memoriesql.complete_input_dispatch_policies
 DROP CONSTRAINT complete_input_dispatch_polic_execution_contract_revision_check,
 ADD CONSTRAINT complete_input_dispatch_polic_execution_contract_revision_check CHECK(execution_contract_revision IN (2,3,4,5));


CREATE OR REPLACE FUNCTION memoriesql.source_revisiting_authorize(t uuid, task uuid, selected uuid DEFAULT NULL) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
 c memoriesql.authorization_contexts%ROWTYPE; item jsonb; principal uuid;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=t AND execution_task_id=task;
 b:=memoriesql.complete_input_authorize(t,e.binding_task_id,e.dispatch_policy_id);
 IF (b.package_pin->>'required_characters')::bigint>131072 THEN
  RAISE EXCEPTION 'source_revisiting_target_budget' USING ERRCODE='54000'; END IF;
 IF (e.execution_contract_revision IS NULL OR e.execution_contract_revision NOT IN (3,4,5)) OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
  WHERE d.tenant_id=t AND d.dispatch_policy_id=e.dispatch_policy_id AND d.execution_contract_revision=e.execution_contract_revision) THEN
  RAISE EXCEPTION 'source_revisiting_qualification_required' USING ERRCODE='42501'; END IF;
 -- Tenant authority fence above, then policy rows in deterministic order. Preserve current producer authority.
 SELECT origin_principal_id INTO principal FROM memoriesql.semantic_tasks WHERE tenant_id=t AND task_id=e.binding_task_id;
 PERFORM cap.role_key FROM memoriesql.role_capabilities cap JOIN memoriesql.workspace_memberships m ON m.role_key=cap.role_key
 WHERE m.tenant_id=t AND m.workspace_id=b.workspace_id AND m.principal_id IN (principal,c.principal_id)
 AND cap.capability_key IN ('source.raw.read','memory.capture','memory.maintain') ORDER BY cap.role_key,cap.capability_key FOR SHARE OF cap;
 PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=t AND package_id=b.package_id));
 PERFORM memoriesql.complete_input_authorize(t,e.binding_task_id,e.dispatch_policy_id);
 IF selected IS NOT NULL AND selected<>b.package_id THEN
  SELECT value INTO item FROM jsonb_array_elements(e.authorized_context) WHERE (value#>>'{package,package_id}')::uuid=selected;
  IF item IS NULL OR NOT memoriesql.revisiting_context_valid(t,b.workspace_id,b.access_scope_id,b.package_id,jsonb_build_array(item)) THEN
   RAISE EXCEPTION 'source_revisiting_out_of_scope' USING ERRCODE='42501'; END IF;
 END IF;
 -- Used optional context remains authorized; unused neighbors never become mandatory coverage.
 FOR item IN SELECT value FROM jsonb_array_elements(e.authorized_context) WHERE EXISTS(
  SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d JOIN memoriesql.model_provider_request_intents i USING(tenant_id,request_id)
  JOIN memoriesql.semantic_tasks q ON q.tenant_id=i.tenant_id AND q.task_id=i.task_id
  JOIN memoriesql.semantic_task_attempts a ON a.tenant_id=i.tenant_id AND a.attempt_id=i.attempt_id
  WHERE i.tenant_id=t AND i.task_id=task AND a.lease_generation=q.lease_generation AND d.delivery_version=2
   AND d.selection->>'package_id'=value#>>'{package,package_id}') LOOP
  IF NOT memoriesql.revisiting_context_valid(t,b.workspace_id,b.access_scope_id,b.package_id,jsonb_build_array(item)) THEN
   RAISE EXCEPTION 'source_revisiting_context_unavailable' USING ERRCODE='42501'; END IF;
 END LOOP;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.activate_complete_input_v4(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE; e memoriesql.complete_input_executions%ROWTYPE;
    original memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    rid uuid:=uuidv7(); tid uuid:=uuidv7(); eid uuid; input jsonb; result jsonb; vocabulary jsonb; request_hash text; started timestamptz:=clock_timestamp();
BEGIN
    IF request->>'contract_version' IS DISTINCT FROM '4' OR request->>'expected_schema_version' IS DISTINCT FROM '23' OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','binding_task_id','dispatch_policy_id','authorized_context','classification_vocabulary']<>'{}'::jsonb OR
       COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR octet_length(request::text)>16384 THEN
        RAISE EXCEPTION 'invalid_complete_input_activation' USING ERRCODE='22023'; END IF;
    IF jsonb_typeof(request->'classification_vocabulary') IS DISTINCT FROM 'array'
       OR jsonb_array_length(request->'classification_vocabulary') NOT BETWEEN 1 AND 32
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(request->'classification_vocabulary') x GROUP BY x->>'key' HAVING count(*)<>1)
    THEN RAISE EXCEPTION 'classification_vocabulary_invalid' USING ERRCODE='22023'; END IF;
    SELECT jsonb_agg(jsonb_build_object('key',t.stable_key,'revision',r.revision,'definition',r.definition) ORDER BY x.ordinality)
      INTO vocabulary FROM jsonb_array_elements(request->'classification_vocabulary') WITH ORDINALITY x(value,ordinality)
      JOIN memoriesql.bead_types t ON t.stable_key=x.value->>'key'
      JOIN memoriesql.bead_type_revisions r ON r.bead_type_id=t.bead_type_id AND r.revision=(x.value->>'revision')::integer AND r.authorable;
    IF vocabulary IS NULL OR jsonb_array_length(vocabulary)<>jsonb_array_length(request->'classification_vocabulary') THEN
      RAISE EXCEPTION 'classification_vocabulary_unavailable' USING ERRCODE='22023'; END IF;
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
    IF (b.package_pin->>'required_characters')::bigint>131072 THEN
        RAISE EXCEPTION 'source_revisiting_target_budget' USING ERRCODE='54000'; END IF;

    PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id));
    IF NOT memoriesql.revisiting_context_valid(c.tenant_id,b.workspace_id,b.access_scope_id,b.package_id,request->'authorized_context')
      OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=5)
    THEN RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501'; END IF;
    request_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    SELECT * INTO old FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='complete_input.activate.v4' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash<>request_hash THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old.response_receipt||'{"replayed":true}'::jsonb;
    END IF;
    -- Every acknowledged key owns a shared-ledger receipt, including natural replay.
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,b.access_scope_id,rid,'complete_input.activate.v4',request->>'idempotency_key',request_hash,'in_progress','semantic_task',b.task_id,1,started,started);
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND binding_task_id=b.task_id;
    IF FOUND THEN
        IF e.execution_contract_revision<>5 OR e.classification_vocabulary IS DISTINCT FROM vocabulary OR e.authorized_context IS DISTINCT FROM request->'authorized_context' OR e.dispatch_policy_id<>(request->>'dispatch_policy_id')::uuid THEN RAISE EXCEPTION 'complete_input_activation_conflict' USING ERRCODE='23505'; END IF;
        SELECT response_receipt INTO result FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=e.activation_receipt_id;
        result:=result||jsonb_build_object('idempotency_receipt_id',rid,'replayed',true);
        PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
    
    PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id));
    IF NOT memoriesql.revisiting_context_valid(c.tenant_id,b.workspace_id,b.access_scope_id,b.package_id,request->'authorized_context')
      OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=5)
    THEN RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501'; END IF;
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
        RETURN result;
    END IF;
    IF original.status<>'policy_paused' OR original.pause_reason_code<>'complete_input_executor_unavailable' OR original.attempt_count<>0 OR original.cancel_requested_at IS NOT NULL THEN
        RAISE EXCEPTION 'complete_input_binding_not_transferable' USING ERRCODE='55000'; END IF;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id;
    INSERT INTO memoriesql.complete_input_executions VALUES(c.tenant_id,b.task_id,tid,(request->>'dispatch_policy_id')::uuid,(SELECT schema_version FROM memoriesql.source_objects WHERE tenant_id=c.tenant_id AND source_object_id=p.source_object_id),rid,5,request->'authorized_context',vocabulary);
    IF memoriesql.cancel_semantic_task(c.tenant_id,b.task_id,'complete_input.execution_transferred',clock_timestamp())<>'cancelled' THEN
        RAISE EXCEPTION 'complete_input_transfer_failed' USING ERRCODE='55000'; END IF;
    input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',5,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||tid::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',request->>'dispatch_policy_id','declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_source_revisiting_v1','authorized_context',request->'authorized_context','classification_vocabulary',vocabulary));
    SELECT q.idempotency_receipt_id INTO eid FROM memoriesql.enqueue_semantic_task(tid,'complete-input.v4:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',5,'ff9fe274fc8f9fe4d2a7d5f4e3c0f53b58e0fec4158a525e3aca484ab347e493','semantic-tasks-v1:a351bceba3c90d5e9edf2e5e3c4d98c20148fc061f635c3b23199bae4caf0e25',b.source_unit_id::text,0,input,input#>>'{evidence_manifest,manifest_id}',b.access_scope_id,started,b.task_id,started) q;
    result:=jsonb_build_object('contract_version',4,'authorized_context',request->'authorized_context','binding_task_id',b.task_id,'execution_task_id',tid,'idempotency_receipt_id',rid,'enqueue_receipt_id',eid,'package',b.package_pin,'replayed',false);
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);

    PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id));
    IF NOT memoriesql.revisiting_context_valid(c.tenant_id,b.workspace_id,b.access_scope_id,b.package_id,request->'authorized_context')
      OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=5)
    THEN RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501'; END IF;
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
    RETURN result;
END; $$;

REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v4(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.activate_complete_input_v4(jsonb) TO memoriesql_application;

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
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision' IN ('3','4','5') THEN
        RETURN octet_length(candidate::text)<=32768 AND candidate#>>'{payload,required_execution}'='trusted_source_revisiting_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb AND jsonb_array_length(candidate#>'{evidence_manifest,references}')=1
           AND jsonb_array_length(candidate#>'{payload,source_unit_ids}')=1 AND jsonb_array_length(candidate#>'{payload,bead_ids}')=1;
    END IF;
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
    IF task_record.task_kind='memory.semantic.author-complete-unit' AND task_record.contract_revision IN (2,3,4,5) THEN
        BEGIN
            PERFORM memoriesql.complete_input_authorize(requested_tenant_id,
                (task_record.input_payload#>>'{payload,binding_task_id}')::uuid,
                (task_record.input_payload#>>'{payload,dispatch_policy_id}')::uuid);
        EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                  WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence';
        END;
        IF task_record.contract_revision IN (3,4,5) THEN
            BEGIN PERFORM memoriesql.source_revisiting_authorize(requested_tenant_id,requested_task_id);
            EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                      WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence'; END;
        END IF;
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

CREATE OR REPLACE FUNCTION memoriesql.assert_complete_execution_binding() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,memoriesql AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    t memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; expected jsonb;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' OR NEW.contract_revision NOT IN (2,3,4,5) THEN RETURN NULL; END IF;
        SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=NEW.tenant_id AND execution_task_id=NEW.task_id;
    ELSE e:=NEW; END IF;
    SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=e.tenant_id AND task_id=e.execution_task_id;
    SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=e.tenant_id AND task_id=e.binding_task_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=e.tenant_id AND package_id=b.package_id;
    expected:=jsonb_build_object('task_id',t.task_id,'task_kind','memory.semantic.author-complete-unit','contract_revision',2,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||t.task_id::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',e.dispatch_policy_id,'declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_complete_input_exposure_v1'));
    IF e.execution_contract_revision IN (3,4,5) THEN
        expected:=jsonb_set(expected,'{contract_revision}',to_jsonb(e.execution_contract_revision));
        expected:=jsonb_set(expected,'{payload,required_execution}','"trusted_source_revisiting_v1"');
        expected:=jsonb_set(expected,'{payload,authorized_context}',e.authorized_context);
    END IF;
    IF e.execution_contract_revision=5 THEN expected:=jsonb_set(expected,'{payload,classification_vocabulary}',e.classification_vocabulary); END IF;
    IF e.execution_task_id IS NULL OR b.task_id IS NULL OR t.task_id IS NULL OR t.input_payload IS DISTINCT FROM expected OR t.rerun_of_task_id IS DISTINCT FROM b.task_id OR
       t.origin_principal_id IS DISTINCT FROM p.producer_principal_id OR t.access_scope_id IS DISTINCT FROM b.access_scope_id OR t.workspace_id IS DISTINCT FROM b.workspace_id OR
       t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=5 THEN 'ff9fe274fc8f9fe4d2a7d5f4e3c0f53b58e0fec4158a525e3aca484ab347e493' WHEN e.execution_contract_revision=4 THEN '8488d4fddf19e65a2eb41203d6b26d7b400d73516c044700246ab3a7e75adce5' WHEN e.execution_contract_revision=3 THEN 'c8c172146c570bde75077745d7f00afa116b5db8f1b78bca4aebada561066e2d' ELSE '5288a780557724d7c5be81afd28f50a0cc5e5b86a3c8e89ae241033e12f3f3fe' END) OR
       t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=5 THEN 'semantic-tasks-v1:a351bceba3c90d5e9edf2e5e3c4d98c20148fc061f635c3b23199bae4caf0e25' WHEN e.execution_contract_revision=4 THEN 'semantic-tasks-v1:0876addc48bcf62db405dff9d1a8b75eb497cbec739945d741d4db72aea4b6dd' WHEN e.execution_contract_revision=3 THEN 'semantic-tasks-v1:116ac71ae8ac94ad8abc3018a0577a60a5e5e543ddf3660ae7db467f09764aef' ELSE 'semantic-tasks-v1:7e8a296cf1143deb86715144ed06cceefcb28dcc9955892ba7df5a06ded8f71f' END) THEN
        RAISE EXCEPTION 'complete_execution_binding_conflict' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END; $$;

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
        ELSIF NEW.contract_revision IN (2,3,4,5) THEN
            IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=NEW.tenant_id AND e.execution_task_id=NEW.task_id) THEN
                RAISE EXCEPTION 'complete_input_activation_required' USING ERRCODE='55000'; END IF;
            IF NEW.status='succeeded' AND (NOT memoriesql.complete_input_exposure_valid(NEW.tenant_id,NEW.task_id,NEW.result_attempt_id,NEW.lease_generation) OR
                NOT EXISTS(SELECT 1 FROM memoriesql.bead_versions v JOIN memoriesql.bead_statement_revisions s USING(tenant_id,bead_version_id) JOIN memoriesql.semantic_task_receipts r ON r.tenant_id=v.tenant_id AND r.semantic_task_receipt_id=v.semantic_task_receipt_id JOIN memoriesql.idempotency_receipts i ON i.tenant_id=r.tenant_id AND i.idempotency_receipt_id=r.idempotency_receipt_id WHERE v.tenant_id=NEW.tenant_id AND v.bead_id=(NEW.input_payload#>>'{payload,bead_ids,0}')::uuid AND i.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4') AND i.resource_id=NEW.task_id)) THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        ELSE RAISE EXCEPTION 'complete_input_execution_unsupported' USING ERRCODE='55000'; END IF;
    ELSIF TG_TABLE_NAME='semantic_task_attempts' THEN
        SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
        IF t.task_kind='memory.semantic.author-complete-unit' AND (t.contract_revision NOT IN (2,3,4,5) OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t.tenant_id AND e.execution_task_id=t.task_id)) THEN
            RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
    ELSE
        SELECT u.materialization_version=1 INTO complete FROM memoriesql.beads b JOIN memoriesql.source_units u USING(tenant_id,source_unit_id) WHERE b.tenant_id=NEW.tenant_id AND b.bead_id=NEW.bead_id;
        IF complete THEN
            SELECT q.* INTO t FROM memoriesql.semantic_task_receipts r JOIN memoriesql.idempotency_receipts i USING(tenant_id,idempotency_receipt_id)
            JOIN memoriesql.semantic_tasks q ON q.tenant_id=i.tenant_id AND q.task_id=i.resource_id
            WHERE r.tenant_id=NEW.tenant_id AND r.semantic_task_receipt_id=NEW.semantic_task_receipt_id AND i.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4');
            SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=t.tenant_id AND task_id=t.task_id AND lease_generation=t.lease_generation AND status='running';
            IF t.task_id IS NULL OR t.task_kind<>'memory.semantic.author-complete-unit' OR t.contract_revision NOT IN (2,3,4,5) OR t.status<>'running' OR t.cancel_requested_at IS NOT NULL OR
               NEW.bead_id::text IS DISTINCT FROM t.input_payload#>>'{payload,bead_ids,0}' OR NEW.version<>1 OR
               NOT memoriesql.complete_input_exposure_valid(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation) OR
               memoriesql.reauthorize_semantic_task(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation,t.lease_owner,t.worker_instance_id,'hydrate',clock_timestamp())<>'authorized' THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END; $$;

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
    mention jsonb;
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
    is_complete := requested_command->>'contract_version' IN ('3','4','5','6');
    is_v2 := requested_command ->> 'contract_version' IS NOT DISTINCT FROM '2';
    is_correction := is_v2 AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM
        'memory.semantic.correct-observation';
    operation_key := CASE WHEN requested_command->>'contract_version'='6' THEN 'complete_input.apply.v4' WHEN requested_command->>'contract_version'='5' THEN 'complete_input.apply.v3' WHEN requested_command->>'contract_version'='4' THEN 'complete_input.apply.v2' WHEN is_complete THEN 'complete_input.apply.v1' WHEN is_correction THEN 'observation_correction.apply.v2'
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
            OR (requested_command->>'contract_version'='3' AND requested_command->>'expected_schema_version'='18'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit'
                AND requested_command->>'contract_revision'='2'
                AND requested_command->>'output_contract_hash'='7399c5039812e98d4d53566cb028350effb476a395a83c6fa17b8445ad9f1124')
            OR (requested_command->>'contract_version'='4' AND requested_command->>'expected_schema_version'='20'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='3'
                AND requested_command->>'output_contract_hash'='7399c5039812e98d4d53566cb028350effb476a395a83c6fa17b8445ad9f1124')
            OR (requested_command->>'contract_version'='5' AND requested_command->>'expected_schema_version'='22'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='4'
                AND requested_command->>'output_contract_hash'='0baecb82f0993a29dbc6c1afb6e41a5f3b83b8ebfe1f70315d87b5a7672d8e8c')
            OR (requested_command->>'contract_version'='6' AND requested_command->>'expected_schema_version'='23'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='5'
                AND requested_command->>'output_contract_hash'='2f54df896c41098f1d1e2eac525157f15c892e8690b277957793173a1c9039eb')
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
    IF requested_command->>'contract_version' IN ('4','5') THEN
        PERFORM memoriesql.source_revisiting_authorize(command_tenant_id,command_task_id);
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
        IF requested_command->>'contract_version'='6' THEN
            PERFORM memoriesql.validate_classification_acceptance(command_tenant_id,command_task_id,command_attempt_id,
                requested_command#>'{payload,classification}', requested_command#>'{payload,annotations}');
        ELSIF requested_command#>'{payload,classification}' IS NOT NULL THEN
            RAISE EXCEPTION 'legacy_output_does_not_support_classification' USING ERRCODE='22023';
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
            render_payload, authored_at, classification_contribution
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_bead_version_id, bead_record.bead_id, bead_record.event_id,
            bead_record.source_unit_id, current_bead_version + 1,
            bead_type_uuid, bead_type_revision_uuid, context_record.principal_id,
            new_task_receipt_id, 2, annotation -> 'render', database_now, CASE WHEN requested_command->>'contract_version'='6' THEN requested_command#>'{payload,classification}' ELSE NULL END
        );

        IF requested_command->>'contract_version' IN ('5','6') THEN
            IF jsonb_typeof(annotation->'mentions') IS DISTINCT FROM 'array'
               OR jsonb_array_length(annotation->'mentions') > 32 THEN
                RAISE EXCEPTION 'authored_mentions_required' USING ERRCODE='22023';
            END IF;
            FOR mention IN SELECT value FROM jsonb_array_elements(annotation->'mentions') LOOP
                IF jsonb_typeof(mention) IS DISTINCT FROM 'object'
                   OR mention-ARRAY['entity_mention_id','surface_text','local_identity_state','local_identity_reason']<>'{}'::jsonb
                   OR jsonb_typeof(mention->'entity_mention_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(mention->'surface_text') IS DISTINCT FROM 'string'
                   OR NOT COALESCE(mention->>'surface_text' ~ '[^[:space:]]',false)
                   OR NOT COALESCE(mention->>'local_identity_state' IN ('unresolved','ambiguous'),false)
                   OR (mention ? 'local_identity_reason' AND jsonb_typeof(mention->'local_identity_reason') NOT IN ('string','null')) THEN
                    RAISE EXCEPTION 'invalid_authored_mention' USING ERRCODE='22023';
                END IF;
                INSERT INTO memoriesql.entity_mentions (
                    tenant_id,workspace_id,access_scope_id,entity_mention_id,
                    bead_version_id,bead_id,event_id,source_unit_id,surface_text,
                    recorded_by_principal_id,recorded_at,local_identity_state,local_identity_reason
                ) VALUES (
                    command_tenant_id,command_workspace_id,command_access_scope_id,
                    (mention->>'entity_mention_id')::uuid,new_bead_version_id,
                    bead_record.bead_id,bead_record.event_id,bead_record.source_unit_id,
                    mention->>'surface_text',context_record.principal_id,database_now,
                    mention->>'local_identity_state',mention->>'local_identity_reason'
                );
            END LOOP;
        ELSIF annotation ? 'mentions' THEN
            RAISE EXCEPTION 'legacy_output_does_not_support_mentions' USING ERRCODE='22023';
        END IF;

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
        jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='6' THEN 6 WHEN requested_command->>'contract_version'='5' THEN 5 WHEN requested_command->>'contract_version'='4' THEN 4 WHEN is_complete THEN 3 WHEN is_v2 THEN 2 ELSE 1 END), database_now, database_now
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

CREATE OR REPLACE FUNCTION memoriesql.record_source_delivery_v1(request_id uuid, payload_hash text, frame jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; i memoriesql.model_provider_request_intents%ROWTYPE;
 e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
 task memoriesql.semantic_tasks%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE; part memoriesql.evidence_package_parts%ROWTYPE;
 item jsonb; selection jsonb; items jsonb:='[]'; actual jsonb; fh text; delivered integer:=0; lo integer; hi integer;
 covered int4multirange; missing int4range;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO i FROM memoriesql.model_provider_request_intents WHERE tenant_id=c.tenant_id AND model_provider_request_intents.request_id=record_source_delivery_v1.request_id;
 SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND execution_task_id=i.task_id;
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=c.tenant_id AND task_id=e.binding_task_id;
 IF c.principal_kind IS DISTINCT FROM 'service' OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
  WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=e.dispatch_policy_id AND d.attestor_principal_id=c.principal_id
   AND d.status='active' AND d.expires_at>clock_timestamp() AND d.execution_contract_revision=e.execution_contract_revision)
 OR (e.execution_contract_revision=5 AND i.run_role<>'direct_leaf')
 OR i.request_payload_hash IS DISTINCT FROM payload_hash OR i.task_id::text IS DISTINCT FROM frame->>'task_id'
 OR i.attempt_id::text IS DISTINCT FROM frame->>'attempt_id' OR frame->'package' IS DISTINCT FROM b.package_pin
 OR frame->'contract_version' IS DISTINCT FROM '2'::jsonb OR octet_length(frame::text)>264192
 OR frame-ARRAY['contract_version','task_id','attempt_id','package','window','read']<>'{}'::jsonb
 OR NOT EXISTS(SELECT 1 FROM memoriesql.model_usage_events u WHERE u.tenant_id=c.tenant_id AND u.request_id=i.request_id AND u.outcome='succeeded') THEN
  RAISE EXCEPTION 'trusted_source_delivery_required' USING ERRCODE='42501'; END IF;
 SELECT * INTO task FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=i.task_id FOR UPDATE;
 SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id;
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 IF c.principal_id=a.claimant_principal_id OR task.contract_revision NOT IN (3,4,5) OR task.status<>'running' OR a.status<>'running'
 OR task.lease_generation<>a.lease_generation OR task.cancel_requested_at IS NOT NULL
 OR task.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'source_revisiting_stale_attempt' USING ERRCODE='42501'; END IF;
 fh:=encode(sha256(convert_to(frame::text,'UTF8')),'hex');
 IF EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d WHERE d.tenant_id=c.tenant_id AND d.request_id=i.request_id) THEN
  IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d WHERE d.tenant_id=c.tenant_id AND d.request_id=i.request_id AND d.frame_hash=fh AND d.delivery_version=2) THEN
   RAISE EXCEPTION 'source_delivery_replay_conflict' USING ERRCODE='23505'; END IF;
  RETURN true;
 END IF;
 IF NULLIF(frame->'window','null') IS NOT NULL THEN
  IF NULLIF(frame->'read','null') IS NOT NULL OR frame#>>'{window,task_id}' IS DISTINCT FROM i.task_id::text
   OR frame#>>'{window,attempt_id}' IS DISTINCT FROM i.attempt_id::text OR frame#>'{window,package}' IS DISTINCT FROM b.package_pin
   OR frame#>>'{window,contract_version}' IS DISTINCT FROM '2' OR jsonb_typeof(frame#>'{window,slices}') IS DISTINCT FROM 'array'
   OR jsonb_array_length(frame#>'{window,slices}') NOT BETWEEN 1 AND 256 THEN
   RAISE EXCEPTION 'source_delivery_window_invalid' USING ERRCODE='22023'; END IF;
  items:=frame#>'{window,slices}';
  SELECT sum(char_length(value->>'content')) INTO delivered FROM jsonb_array_elements(items);
  IF delivered IS NULL OR delivered>65536 THEN RAISE EXCEPTION 'source_delivery_window_bound' USING ERRCODE='54000'; END IF;
  selection:=jsonb_build_object('package_id',b.package_id,'kind','window','intervals',
   (SELECT jsonb_agg(jsonb_build_object('part_id',value#>'{inventory,part_id}','start_character',value->'start_character','characters',char_length(value->>'content'))) FROM jsonb_array_elements(items)));
 ELSIF NULLIF(frame->'read','null') IS NOT NULL THEN
  actual:=memoriesql.source_revisiting_read(c.tenant_id,i.task_id,frame#>'{read,request}');
  IF actual IS DISTINCT FROM frame->'read' THEN RAISE EXCEPTION 'source_delivery_evidence_mismatch' USING ERRCODE='22023'; END IF;
  selection:=actual->'request';
  IF selection->>'representation'='normalized' THEN
   delivered:=char_length(actual->>'content');
   IF selection->>'package_id'=b.package_id::text THEN
    items:=jsonb_build_array(jsonb_build_object('inventory',actual->'inventory','start_character',selection->'offset','content',actual->'content'));
   END IF;
  ELSE delivered:=octet_length(decode(actual->>'bytes_hex','hex')); END IF;
 ELSE selection:=jsonb_build_object('package_id',b.package_id,'kind','no_evidence'); END IF;
 IF (SELECT count(*)>=12 OR COALESCE(sum(d.delivered_units),0)+delivered>262144 FROM memoriesql.complete_input_dispatch_receipts d
  JOIN memoriesql.model_provider_request_intents intent USING(tenant_id,request_id)
  WHERE intent.tenant_id=c.tenant_id AND intent.task_id=i.task_id AND intent.attempt_id=i.attempt_id AND d.delivery_version=2) THEN
  RAISE EXCEPTION 'source_delivery_budget_exhausted' USING ERRCODE='54000'; END IF;
 -- Persist only previously uncovered target intervals. Every dispatch still has its own accounting intent/receipt.
 FOR item IN SELECT value FROM jsonb_array_elements(items) LOOP
  SELECT * INTO part FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=b.package_id AND part_id=(item#>>'{inventory,part_id}')::uuid;
  lo:=(item->>'start_character')::integer; hi:=lo+char_length(item->>'content');
  IF part.part_id IS NULL OR part.inventory IS DISTINCT FROM item->'inventory' OR lo IS NULL OR lo<0 OR hi<=lo OR hi>(part.inventory->>'characters')::integer
   OR substring(part.content FROM lo+1 FOR hi-lo) IS DISTINCT FROM item->>'content' THEN
   RAISE EXCEPTION 'source_delivery_evidence_mismatch' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(range_agg(int4range(x.start_character,x.end_character,'[)')),'{}'::int4multirange) INTO covered
   FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=c.tenant_id AND x.attempt_id=i.attempt_id AND x.part_id=part.part_id;
  FOR missing IN SELECT unnest(int4multirange(int4range(lo,hi,'[)'))-covered) LOOP
   INSERT INTO memoriesql.complete_input_exposures VALUES(c.tenant_id,i.task_id,i.attempt_id,a.lease_generation,i.request_id,
    b.package_id,b.package_pin->>'inventory_sha256',part.part_id,lower(missing),upper(missing),
    encode(sha256(convert_to(substring(part.content FROM lower(missing)+1 FOR upper(missing)-lower(missing)),'UTF8')),'hex'),c.principal_id,e.dispatch_policy_id,clock_timestamp());
  END LOOP;
 END LOOP;
 INSERT INTO memoriesql.complete_input_dispatch_receipts VALUES(c.tenant_id,i.request_id,fh,2,selection,delivered);
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 IF task.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'source_revisiting_stale_attempt' USING ERRCODE='42501'; END IF;
 RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.complete_input_exposure_valid(t uuid, task uuid, attempt uuid, generation bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
 SELECT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e
 JOIN memoriesql.logical_unit_materializations b ON b.tenant_id=e.tenant_id AND b.task_id=e.binding_task_id
 WHERE e.tenant_id=t AND e.execution_task_id=task
 AND (e.execution_contract_revision NOT IN (3,4,5) OR (SELECT count(*)=(b.package_pin->>'required_parts')::integer FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id))
 AND NOT EXISTS(
  SELECT 1 FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id AND
   CASE WHEN e.execution_contract_revision IN (3,4,5) THEN NOT COALESCE(
    (SELECT range_agg(int4range(x.start_character,x.end_character,'[)')) @> int4range(0,(p.inventory->>'characters')::integer,'[)')
     FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
      AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id),false)
   ELSE NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
    AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id AND x.end_character=(p.inventory->>'characters')::integer) END))
$$;

INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES ('semantic-tasks-v1:a351bceba3c90d5e9edf2e5e3c4d98c20148fc061f635c3b23199bae4caf0e25','memory.semantic.author-complete-unit',5,'memoriesql.kernel','ff9fe274fc8f9fe4d2a7d5f4e3c0f53b58e0fec4158a525e3aca484ab347e493','canonical_semantics','memory.capture','capture',50,3,NULL,NULL);

CREATE OR REPLACE FUNCTION memoriesql.record_semantic_run_event(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_event_kind text,
    requested_run_id text,
    requested_parent_run_id text,
    requested_run_role text,
    requested_agent_key text,
    requested_input_contract_id text,
    requested_input_contract_revision integer,
    requested_input_contract_hash text,
    requested_output_contract_id text,
    requested_output_contract_revision integer,
    requested_output_contract_hash text,
    requested_model_profile_key text,
    requested_model_profile_revision integer,
    requested_effort_key text,
    requested_delegation_id text,
    requested_at timestamp with time zone
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    existing_run memoriesql.semantic_task_runs%ROWTYPE;
    authorization_result text;
    next_sibling_order integer;
    terminal_status text;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR requested_event_kind NOT IN (
           'run.started', 'run.succeeded', 'run.failed',
           'delegation.started', 'delegation.finished'
       )
       OR requested_run_role NOT IN ('direct_leaf', 'conductor', 'delegate')
       OR requested_run_id !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_run_id) > 128
       OR requested_agent_key !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_agent_key) > 128
       OR requested_input_contract_id
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_input_contract_id) > 128
       OR requested_output_contract_id
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_output_contract_id) > 128
       OR requested_model_profile_key
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_model_profile_key) > 128
       OR requested_effort_key
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_effort_key) > 128
       OR requested_input_contract_revision <= 0
       OR requested_output_contract_revision <= 0
       OR requested_model_profile_revision <= 0
       OR requested_input_contract_hash !~ '^[a-f0-9]{64}$'
       OR requested_output_contract_hash !~ '^[a-f0-9]{64}$'
       OR (
           requested_run_role = 'delegate'
           AND (
               requested_parent_run_id IS NULL
               OR requested_delegation_id IS NULL
               OR requested_parent_run_id = requested_run_id
           )
       )
       OR (
           requested_run_role <> 'delegate'
           AND (
               requested_parent_run_id IS NOT NULL
               OR requested_delegation_id IS NOT NULL
               OR requested_event_kind LIKE 'delegation.%'
           )
       )
       OR requested_parent_run_id IS NOT NULL AND (
           requested_parent_run_id
              !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
           OR length(requested_parent_run_id) > 128
       )
       OR requested_delegation_id IS NOT NULL AND (
           requested_delegation_id
              !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
           OR length(requested_delegation_id) > 128
       ) THEN
        RAISE EXCEPTION 'semantic run event contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running';
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      )
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running'
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR context_record.expires_at <= database_now THEN
        RETURN false;
    END IF;
    authorization_result := memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'outcome', requested_at
    );
    IF authorization_result <> 'authorized' THEN
        RETURN false;
    END IF;

    SELECT * INTO existing_run
    FROM memoriesql.semantic_task_runs AS run
    WHERE run.tenant_id = requested_tenant_id
      AND run.attempt_id = requested_attempt_id
      AND run.run_id = requested_run_id;

    IF FOUND AND (
        existing_run.task_id IS DISTINCT FROM requested_task_id
        OR existing_run.lease_generation IS DISTINCT FROM
           requested_lease_generation
        OR existing_run.parent_run_id IS DISTINCT FROM requested_parent_run_id
        OR existing_run.run_role IS DISTINCT FROM requested_run_role
        OR existing_run.agent_key IS DISTINCT FROM requested_agent_key
        OR existing_run.input_contract_id IS DISTINCT FROM
           requested_input_contract_id
        OR existing_run.input_contract_revision IS DISTINCT FROM
           requested_input_contract_revision
        OR existing_run.input_contract_hash IS DISTINCT FROM
           requested_input_contract_hash
        OR existing_run.output_contract_id IS DISTINCT FROM
           requested_output_contract_id
        OR existing_run.output_contract_revision IS DISTINCT FROM
           requested_output_contract_revision
        OR existing_run.output_contract_hash IS DISTINCT FROM
           requested_output_contract_hash
        OR existing_run.model_profile_key IS DISTINCT FROM
           requested_model_profile_key
        OR existing_run.model_profile_revision IS DISTINCT FROM
           requested_model_profile_revision
        OR existing_run.effort_key IS DISTINCT FROM requested_effort_key
        OR existing_run.delegation_id IS DISTINCT FROM requested_delegation_id
    ) THEN
        RETURN false;
    END IF;

    IF requested_event_kind = 'delegation.started' THEN
        IF FOUND THEN
            RETURN existing_run.run_status = 'delegation_started'
               AND existing_run.delegation_status = 'started';
        END IF;
        PERFORM 1 FROM memoriesql.semantic_task_runs AS parent
        WHERE parent.tenant_id = requested_tenant_id
          AND parent.attempt_id = requested_attempt_id
          AND parent.run_id = requested_parent_run_id
          AND (parent.run_role = 'conductor' OR
            (parent.run_role='direct_leaf' AND parent.run_status='running'
             AND task_record.task_kind='memory.semantic.author-complete-unit' AND task_record.contract_revision=5
             AND requested_agent_key='memory.semantic.bead-type-classifier'
             AND requested_input_contract_id='memory.semantic.bead-classification.packet' AND requested_input_contract_revision=1
             AND requested_input_contract_hash='c661867057e6a6e31950f56ec27f2be71ec1fe550ed7283f82c4a53dd65eb389'
             AND requested_output_contract_id='memory.semantic.bead-classification.decision' AND requested_output_contract_revision=1
             AND requested_output_contract_hash='c1efb5bc7652a827e63b1bdbadabc7ce19c72ca74c38abf41dd7061ecc09b802'
             AND requested_model_profile_key='classification.standard' AND requested_model_profile_revision=1
             AND requested_effort_key='standard'
             AND NOT EXISTS(SELECT 1 FROM memoriesql.semantic_task_runs child WHERE child.tenant_id=requested_tenant_id
                AND child.attempt_id=requested_attempt_id AND child.parent_run_id=requested_parent_run_id)));

        IF NOT FOUND THEN
            RETURN false;
        END IF;
        SELECT COALESCE(MAX(run.sibling_order), 0) + 1
        INTO next_sibling_order
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.attempt_id = requested_attempt_id
          AND run.parent_run_id = requested_parent_run_id;
        INSERT INTO memoriesql.semantic_task_runs (
            tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
            lease_generation, run_id, parent_run_id, delegation_id, run_role,
            sibling_order, agent_key, input_contract_id,
            input_contract_revision, input_contract_hash, output_contract_id,
            output_contract_revision, output_contract_hash, model_profile_key,
            model_profile_revision, effort_key, run_status, delegation_status,
            delegation_started_at, settled
        ) VALUES (
            task_record.tenant_id, task_record.workspace_id,
            task_record.access_scope_id, task_record.task_id,
            requested_attempt_id, requested_lease_generation, requested_run_id,
            requested_parent_run_id, requested_delegation_id, requested_run_role,
            next_sibling_order, requested_agent_key, requested_input_contract_id,
            requested_input_contract_revision, requested_input_contract_hash,
            requested_output_contract_id, requested_output_contract_revision,
            requested_output_contract_hash, requested_model_profile_key,
            requested_model_profile_revision, requested_effort_key,
            'delegation_started', 'started', database_now, false
        );
        RETURN true;
    END IF;

    IF requested_event_kind = 'run.started' THEN
        IF FOUND THEN
            IF existing_run.run_role <> 'delegate' THEN
                RETURN existing_run.run_status = 'running';
            END IF;
            IF existing_run.run_status = 'running' THEN
                RETURN true;
            END IF;
            IF existing_run.run_status <> 'delegation_started' THEN
                RETURN false;
            END IF;
            UPDATE memoriesql.semantic_task_runs
               SET run_status = 'running', started_at = database_now
             WHERE tenant_id = requested_tenant_id
               AND attempt_id = requested_attempt_id
               AND run_id = requested_run_id;
            RETURN true;
        END IF;
        IF requested_run_role = 'delegate' THEN
            RETURN false;
        END IF;
        INSERT INTO memoriesql.semantic_task_runs (
            tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
            lease_generation, run_id, run_role, sibling_order, agent_key,
            input_contract_id, input_contract_revision, input_contract_hash,
            output_contract_id, output_contract_revision, output_contract_hash,
            model_profile_key, model_profile_revision, effort_key, run_status,
            started_at, settled
        ) VALUES (
            task_record.tenant_id, task_record.workspace_id,
            task_record.access_scope_id, task_record.task_id,
            requested_attempt_id, requested_lease_generation, requested_run_id,
            requested_run_role, 1, requested_agent_key,
            requested_input_contract_id, requested_input_contract_revision,
            requested_input_contract_hash, requested_output_contract_id,
            requested_output_contract_revision, requested_output_contract_hash,
            requested_model_profile_key, requested_model_profile_revision,
            requested_effort_key, 'running', database_now, false
        );
        RETURN true;
    END IF;

    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF requested_event_kind IN ('run.succeeded', 'run.failed') THEN
        terminal_status := CASE requested_event_kind
            WHEN 'run.succeeded' THEN 'succeeded'
            ELSE 'failed'
        END;
        IF existing_run.run_status = terminal_status THEN
            RETURN true;
        END IF;
        IF existing_run.run_status <> 'running' OR existing_run.settled THEN
            RETURN false;
        END IF;
        UPDATE memoriesql.semantic_task_runs
           SET run_status = terminal_status,
               finished_at = database_now,
               settled = (run_role <> 'delegate')
         WHERE tenant_id = requested_tenant_id
           AND attempt_id = requested_attempt_id
           AND run_id = requested_run_id;
        RETURN true;
    END IF;

    IF requested_event_kind = 'delegation.finished' THEN
        IF existing_run.delegation_status = 'finished' THEN
            RETURN true;
        END IF;
        IF existing_run.run_role <> 'delegate'
           OR existing_run.run_status NOT IN ('succeeded', 'failed')
           OR existing_run.delegation_status <> 'started'
           OR existing_run.settled THEN
            RETURN false;
        END IF;
        UPDATE memoriesql.semantic_task_runs
           SET delegation_status = 'finished',
               delegation_finished_at = database_now,
               settled = true
         WHERE tenant_id = requested_tenant_id
           AND attempt_id = requested_attempt_id
           AND run_id = requested_run_id;
        RETURN true;
    END IF;
    RETURN false;
END;
$$;
