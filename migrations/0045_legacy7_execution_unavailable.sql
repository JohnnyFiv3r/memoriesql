-- Forward restriction of legacy author-complete-unit revision 7.
-- Historical schemas and accepted meaning remain intact.
-- Atomic installation uses the existing migration transaction. Effective old-
-- caller drain is a separate deployment proof; see docs/legacy7-availability.md.

CREATE OR REPLACE FUNCTION memoriesql.activate_complete_input_v6(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    original memoriesql.semantic_tasks%ROWTYPE;
BEGIN
    IF request->>'contract_version' IS DISTINCT FROM '6' OR request->>'expected_schema_version' IS DISTINCT FROM '41' OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','binding_task_id','dispatch_policy_id','authorized_context']<>'{}'::jsonb OR
       COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR octet_length(request::text)>16384 THEN
        RAISE EXCEPTION 'invalid_complete_input_activation' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    b:=memoriesql.complete_input_authorize(c.tenant_id,(request->>'binding_task_id')::uuid,(request->>'dispatch_policy_id')::uuid);
    SELECT * INTO original FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=b.task_id;
    IF original.origin_principal_id<>c.principal_id OR NOT memoriesql.current_context_semantic_task_authorized(b.task_id,'memory.maintain','write') THEN
        RAISE EXCEPTION 'complete_input_activation_denied' USING ERRCODE='42501'; END IF;
    RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501';
END; $$;

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
 IF (e.execution_contract_revision IS NULL OR e.execution_contract_revision NOT IN (3,4,5,6,7)) OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
  WHERE d.tenant_id=t AND d.dispatch_policy_id=e.dispatch_policy_id AND d.execution_contract_revision=e.execution_contract_revision) THEN
  RAISE EXCEPTION 'source_revisiting_qualification_required' USING ERRCODE='42501'; END IF;
 IF e.execution_contract_revision=7 THEN
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
