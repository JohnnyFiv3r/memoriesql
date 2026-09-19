-- Q-owned inspection only. No canonical writes, task creation, or inference.
-- Definer selection is necessary to choose the latest decision BEFORE authorization.
CREATE FUNCTION memoriesql.inspect_stored_bead_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms'
AS $$
DECLARE
 c memoriesql.authorization_contexts%ROWTYPE;
 b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE;
 u memoriesql.source_units%ROWTYPE; ev memoriesql.source_events%ROWTYPE;
 r memoriesql.semantic_task_receipts%ROWTYPE; ir memoriesql.idempotency_receipts%ROWTYPE;
 binding memoriesql.logical_unit_materializations%ROWTYPE;
 execution memoriesql.complete_input_executions%ROWTYPE;
 intent memoriesql.model_provider_request_intents%ROWTYPE;
 m memoriesql.entity_mentions%ROWTYPE; resolution memoriesql.entity_mention_resolutions%ROWTYPE;
 s memoriesql.bead_semantic_statements%ROWTYPE;
 support memoriesql.bead_semantic_statement_evidence%ROWTYPE;
 p memoriesql.evidence_packages%ROWTYPE;
 item jsonb; mentions jsonb:=NULL; statements jsonb:='[]'; evidence jsonb;
 decision jsonb; entities jsonb; meaning jsonb:=NULL; provenance jsonb:=NULL;
 result jsonb; bead_type jsonb; status text; lifecycle text:='thin';
 sources uuid[]:='{}'; source uuid; watermark bigint; amount integer;
 candidate_id uuid; candidates_authorized boolean;
 started timestamptz:=clock_timestamp();
 unavailable constant jsonb:='{"contract_version":1,"outcome":"unavailable","bead":null}';
 budget constant jsonb:='{"contract_version":1,"outcome":"budget_exhausted","bead":null}';
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object'
 OR request-ARRAY['contract_version','bead_id']<>'{}'::jsonb
 OR request->>'contract_version' IS DISTINCT FROM '1'
 OR octet_length(request::text)>1024 OR request->>'bead_id' IS NULL THEN
  RAISE EXCEPTION 'invalid_stored_bead_inspection' USING ERRCODE='22023';
 END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO b FROM memoriesql.beads WHERE tenant_id=c.tenant_id
  AND workspace_id=c.workspace_id AND bead_id=(request->>'bead_id')::uuid;
 IF b.bead_id IS NULL OR NOT memoriesql.current_context_event_authorized(
  b.access_scope_id,b.event_id,'memory.query','read') THEN RETURN unavailable; END IF;
 SELECT * INTO ev FROM memoriesql.source_events WHERE tenant_id=b.tenant_id AND event_id=b.event_id;
 SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 sources:=array_append(sources,ev.source_object_id);
 PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
 -- Immutable accepted identity, never newest visible version.
 SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics a
 JOIN memoriesql.bead_versions bv USING(tenant_id,bead_version_id)
 WHERE a.tenant_id=b.tenant_id AND a.bead_id=b.bead_id;
 SELECT * INTO binding FROM memoriesql.logical_unit_materializations
 WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 SELECT * INTO execution FROM memoriesql.complete_input_executions
 WHERE tenant_id=b.tenant_id AND binding_task_id=binding.task_id;
 IF execution.execution_task_id IS NOT NULL THEN
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
  WHERE q.tenant_id=b.tenant_id AND q.task_id=execution.execution_task_id;
  lifecycle:=CASE WHEN status IN ('failed_terminal','dead_letter','cancelled','policy_paused','superseded')
   THEN 'failed' ELSE 'pending' END;
 END IF;
 IF v.bead_version_id IS NOT NULL THEN
  IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id,v.workspace_id,v.access_scope_id,v.bead_version_id)
  THEN RETURN unavailable; END IF;
  SELECT * INTO r FROM memoriesql.semantic_task_receipts WHERE tenant_id=v.tenant_id AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  SELECT * INTO ir FROM memoriesql.idempotency_receipts WHERE tenant_id=r.tenant_id AND idempotency_receipt_id=r.idempotency_receipt_id;
  IF r.semantic_task_receipt_id IS NULL OR ir.status IS DISTINCT FROM 'succeeded' THEN RETURN unavailable; END IF;
  -- Accepted provenance is bound to its own receipt, never the newest source task.
  SELECT * INTO execution FROM memoriesql.complete_input_executions
   WHERE tenant_id=r.tenant_id AND execution_task_id=ir.resource_id;
  IF ir.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4')
   AND execution.execution_task_id IS NULL THEN RETURN unavailable; END IF;
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
   WHERE q.tenant_id=r.tenant_id AND q.task_id=execution.execution_task_id;
  -- Every accepted author can depend on optional context, including revisions 3/4
  -- without a classifier. Conservatively authorize every pinned source.
  FOR item IN SELECT value FROM jsonb_array_elements(execution.authorized_context) LOOP
   source:=(item->>'source_object_id')::uuid;
   PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
  END LOOP;
  SELECT statement_watermark INTO watermark FROM memoriesql.bead_statement_revisions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id;
  SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
   WHERE tenant_id=v.tenant_id AND bead_id=v.bead_id AND statement_sequence<=watermark LIMIT 33) bounded;
  IF amount>32 THEN RETURN budget; END IF;
  FOR s IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=v.tenant_id
   AND bead_id=v.bead_id AND statement_sequence<=watermark ORDER BY statement_sequence LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(s.tenant_id,s.workspace_id,s.access_scope_id,s.statement_id)
    THEN RETURN unavailable; END IF;
   -- Context is provenance, not support; still requires authorization before disclosure.
   FOREACH source IN ARRAY s.context_source_ids LOOP
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
   END LOOP;
   evidence:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id LIMIT 65) bounded;
   IF amount>64 THEN RETURN budget; END IF;
   FOR support IN SELECT * FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id ORDER BY evidence_source_unit_id LOOP
    SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id=s.tenant_id AND event_id=support.evidence_event_id;
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
    evidence:=evidence||jsonb_build_array(jsonb_build_object('event_id',support.evidence_event_id,
     'source_unit_id',support.evidence_source_unit_id,'content_sha256',support.evidence_content_hash));
   END LOOP;
   statements:=statements||jsonb_build_array(jsonb_build_object('statement_id',s.statement_id,
    'sequence',s.statement_sequence,'kind',s.statement_kind,'text',s.statement_text,
    'supersedes_statement_id',s.supersedes_statement_id,'correction_reason',s.correction_reason,'evidence',evidence));
  END LOOP;
  -- Capability derives ONLY from this accepted result's own successful apply receipt.
  IF r.task_contract_key='memory.semantic.author-complete-unit' AND
    ((r.task_contract_version=4 AND ir.operation_kind='complete_input.apply.v3') OR
     (r.task_contract_version=5 AND ir.operation_kind='complete_input.apply.v4')) THEN
   mentions:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id LIMIT 33) bounded;
   IF amount>32 THEN RETURN budget; END IF;
   FOR m IN SELECT * FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id ORDER BY entity_mention_id LOOP
    SELECT * INTO resolution FROM memoriesql.entity_mention_resolutions WHERE tenant_id=m.tenant_id AND entity_mention_id=m.entity_mention_id
     ORDER BY decision_sequence DESC LIMIT 1;
    -- Both absent and protected global decisions are unavailable. Local authored identity is separate.
    decision:=jsonb_build_object('availability','unavailable','resolution_id',NULL,'status',NULL,'entity_ids',NULL);
    IF resolution.entity_mention_resolution_id IS NOT NULL AND
      memoriesql.current_context_bead_version_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,resolution.evidence_bead_version_id)
      AND (resolution.resolved_entity_id IS NULL OR memoriesql.current_context_entity_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,resolution.resolved_entity_id)) THEN
     -- Materialize at most 65 candidates once. Never invoke the unbounded aggregate
     -- predicate or re-query the full set after the bound (including under insert races).
     SELECT COALESCE(jsonb_agg(candidate_entity_id ORDER BY candidate_ordinal),'[]') INTO entities
      FROM (SELECT candidate_entity_id,candidate_ordinal FROM memoriesql.entity_resolution_candidates
       WHERE tenant_id=m.tenant_id AND entity_mention_resolution_id=resolution.entity_mention_resolution_id
       ORDER BY candidate_ordinal LIMIT 65) bounded;
     amount:=jsonb_array_length(entities);
     -- An oversized decision stays indistinguishable from a protected/absent decision.
     IF amount<=64 THEN
      candidates_authorized:=resolution.resolution_status<>'ambiguous' OR amount>=2;
      FOR candidate_id IN SELECT value::uuid FROM jsonb_array_elements_text(entities) LOOP
       IF NOT memoriesql.current_context_entity_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,candidate_id) THEN
        candidates_authorized:=false; EXIT;
       END IF;
      END LOOP;
      IF candidates_authorized THEN
       IF resolution.resolved_entity_id IS NOT NULL THEN entities:=jsonb_build_array(resolution.resolved_entity_id); END IF;
       decision:=jsonb_build_object('availability','available','resolution_id',resolution.entity_mention_resolution_id,'status',resolution.resolution_status,'entity_ids',entities);
      END IF;
     END IF;
    END IF;
    mentions:=mentions||jsonb_build_array(jsonb_build_object('entity_mention_id',m.entity_mention_id,'surface_text',m.surface_text,
     'local_identity_state',m.local_identity_state,'local_identity_reason',m.local_identity_reason,'resolution',decision));
   END LOOP;
  END IF;
  IF v.classification_contribution IS NOT NULL THEN
   IF r.task_contract_version<>5 OR r.task_contract_key<>'memory.semantic.author-complete-unit'
    OR ir.operation_kind<>'complete_input.apply.v4' OR ir.resource_id IS DISTINCT FROM execution.execution_task_id THEN RETURN unavailable; END IF;
   SELECT * INTO intent FROM memoriesql.model_provider_request_intents WHERE tenant_id=v.tenant_id
    AND request_id=(v.classification_contribution->>'request_id')::uuid AND task_id=ir.resource_id;
   IF intent.request_id IS NULL OR intent.workspace_id<>c.workspace_id OR
    NOT memoriesql.current_context_scope_authorized(intent.access_scope_id,'memory.query','read') THEN RETURN unavailable; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(v.classification_contribution->'evidence') LOOP
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=v.tenant_id AND package_id=(item#>>'{selection,package_id}')::uuid;
    IF p.package_id IS NULL OR p.inventory_hash IS DISTINCT FROM item#>>'{selection,inventory_sha256}' THEN RETURN unavailable; END IF;
    PERFORM memoriesql.revisiting_source_authorize(p.source_object_id); sources:=array_append(sources,p.source_object_id);
   END LOOP;
   provenance:=jsonb_build_object('request_id',intent.request_id,'task_id',intent.task_id,'attempt_id',intent.attempt_id,
    'model_id',intent.model_id,'model_profile_key',intent.model_profile_key,'model_profile_revision',intent.model_profile_revision,
    'authorization_policy_revision',intent.authorization_policy_revision,'quality_policy_revision_id',intent.quality_policy_revision_id,'pricing_revision_id',intent.pricing_revision_id);
  ELSIF r.task_contract_version=5 AND r.task_contract_key='memory.semantic.author-complete-unit' THEN RETURN unavailable;
  END IF;
  SELECT jsonb_build_object('key',t.stable_key,'revision',tr.revision,'definition',tr.definition) INTO bead_type
   FROM memoriesql.bead_types t JOIN memoriesql.bead_type_revisions tr USING(bead_type_id) WHERE tr.bead_type_revision_id=v.bead_type_revision_id;
  meaning:=jsonb_build_object('bead_version_id',v.bead_version_id,'receipt_id',r.semantic_task_receipt_id,
   'task_contract_key',r.task_contract_key,'task_contract_version',r.task_contract_version,'bead_type',bead_type,
   'render_contract_revision',v.render_contract_revision,'render',CASE WHEN v.render_contract_revision=2 THEN v.render_payload END,'statements',statements,
   'mentions',mentions,'classification',v.classification_contribution,'classification_vocabulary',CASE WHEN v.classification_contribution IS NOT NULL THEN execution.classification_vocabulary END,'classification_provenance',provenance);
  lifecycle:='accepted';
 END IF;
 result:=jsonb_build_object('contract_version',1,'outcome','available','bead',jsonb_build_object(
  'bead_id',b.bead_id,'event_id',b.event_id,'source_object_id',ev.source_object_id,'source_unit_id',b.source_unit_id,
  'parent_source_unit_id',u.parent_unit_id,'parent_resolution',COALESCE(u.structure->>'parent_resolution','unknown'),
  'package',binding.package_pin,'binding_task_id',binding.task_id,'execution_task_id',execution.execution_task_id,
  'execution_status',status,'lifecycle',lifecycle,'meaning',meaning));
 IF octet_length(memoriesql.canonical_semantic_json_text(result))>262144 THEN RETURN budget; END IF;
 -- Revalidate all disclosed dependencies at the return boundary; audit writes only.
 FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
  PERFORM memoriesql.revisiting_source_authorize(source);
 END LOOP;
 IF clock_timestamp()-started>interval '2 seconds' THEN RETURN budget; END IF;
 RETURN result;
EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.inspect_stored_bead_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_stored_bead_v1(jsonb) TO memoriesql_application;

-- Reuse the existing normalized/raw storage mechanics, without worker/dispatch policy.
CREATE FUNCTION memoriesql.stored_bead_evidence_content(t uuid, request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
 p memoriesql.evidence_packages%ROWTYPE; part memoriesql.evidence_package_parts%ROWTYPE;
 raw memoriesql.source_range_capture_receipts%ROWTYPE; chunk memoriesql.captured_source_ranges%ROWTYPE;
 lineage jsonb; data bytea:=''::bytea; text_content text; segment bytea; result jsonb;
 at_pos integer:=(request->>'offset')::integer; lim integer:=(request->>'limit')::integer;
 ord integer:=(request->>'part_ordinal')::integer; n integer; next_byte bigint; end_byte bigint; k integer:=0;
BEGIN
 IF request-ARRAY['package_id','inventory_sha256','part_ordinal','representation','lineage_ordinal','offset','limit']<>'{}'::jsonb
 OR octet_length(request::text)>2048 OR at_pos IS NULL OR at_pos<0 OR ord IS NULL OR ord NOT BETWEEN 0 AND 255
 OR lim IS NULL OR lim NOT BETWEEN 1 AND 32768 OR request->>'representation' NOT IN ('normalized','raw') THEN
  RAISE EXCEPTION 'invalid_source_revisiting_read' USING ERRCODE='22023'; END IF;

 SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=t AND package_id=(request->>'package_id')::uuid;
 IF p.inventory_hash IS DISTINCT FROM request->>'inventory_sha256' OR p.sealed_receipt_id IS NULL THEN
  RAISE EXCEPTION 'source_revisiting_stale_pin' USING ERRCODE='55000'; END IF;
 SELECT * INTO part FROM memoriesql.evidence_package_parts WHERE tenant_id=t AND package_id=p.package_id AND ordinal=ord;
 IF part.part_id IS NULL THEN RAISE EXCEPTION 'source_revisiting_evidence_unavailable' USING ERRCODE='P0002'; END IF;
 IF request->>'representation'='normalized' THEN
  IF request->'lineage_ordinal' IS DISTINCT FROM 'null'::jsonb OR lim>16384 OR at_pos>=char_length(part.content) THEN
   RAISE EXCEPTION 'invalid_source_revisiting_interval' USING ERRCODE='22023'; END IF;
  text_content:=substring(part.content FROM at_pos+1 FOR lim); n:=char_length(text_content); data:=convert_to(text_content,'UTF8');
  result:=jsonb_build_object('content',text_content,'bytes_hex',NULL,'next_offset',CASE WHEN at_pos+n<char_length(part.content) THEN at_pos+n END);
 ELSE
  k:=(request->>'lineage_ordinal')::integer;
  IF k IS NULL OR k NOT BETWEEN 0 AND 15 THEN RAISE EXCEPTION 'invalid_source_revisiting_lineage' USING ERRCODE='22023'; END IF;
  lineage:=part.inventory->'lineage'->k;
  SELECT * INTO raw FROM memoriesql.source_range_capture_receipts WHERE tenant_id=t AND source_range_receipt_id=(lineage->>'source_range_receipt_id')::uuid;
  IF lineage IS NULL OR raw.source_object_id IS DISTINCT FROM p.source_object_id OR raw.source_revision_key IS DISTINCT FROM p.declaration->>'source_revision_key'
   OR raw.byte_start>(lineage->>'byte_start')::bigint OR raw.byte_end_exclusive<(lineage->>'byte_end_exclusive')::bigint THEN
   RAISE EXCEPTION 'source_revisiting_lineage_unavailable' USING ERRCODE='P0002'; END IF;
  IF lineage->>'fold_receipt_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM memoriesql.transcript_fold_outcomes o
   JOIN memoriesql.transcript_fold_receipts f USING(tenant_id,transcript_fold_receipt_id)
   WHERE o.tenant_id=t AND o.transcript_fold_receipt_id=(lineage->>'fold_receipt_id')::uuid
    AND o.outcome_ordinal=(lineage->>'fold_outcome_ordinal')::integer AND o.source_object_id=p.source_object_id
    AND f.source_revision_key=raw.source_revision_key AND f.file_identity_key=raw.file_identity_key
    AND o.byte_start<=(lineage->>'byte_start')::bigint AND o.byte_end_exclusive>=(lineage->>'byte_end_exclusive')::bigint) THEN
   RAISE EXCEPTION 'source_revisiting_fold_unavailable' USING ERRCODE='P0002'; END IF;
  next_byte:=(lineage->>'byte_start')::bigint; end_byte:=(lineage->>'byte_end_exclusive')::bigint; k:=0;
  IF end_byte-next_byte NOT BETWEEN 1 AND 262144 OR at_pos>=end_byte-next_byte THEN
   RAISE EXCEPTION 'invalid_source_revisiting_raw_interval' USING ERRCODE='22023'; END IF;
  FOR chunk IN SELECT * FROM memoriesql.captured_source_ranges WHERE tenant_id=t AND source_range_receipt_id=raw.source_range_receipt_id ORDER BY chunk_ordinal LIMIT 257 LOOP
   k:=k+1; IF k>256 THEN RAISE EXCEPTION 'source_revisiting_raw_work_bound' USING ERRCODE='54000'; END IF;
   IF chunk.byte_end_exclusive<=next_byte OR chunk.byte_start>=end_byte THEN CONTINUE; END IF;
   IF chunk.byte_start>next_byte THEN RAISE EXCEPTION 'source_revisiting_raw_gap' USING ERRCODE='P0002'; END IF;
   segment:=substring(chunk.payload_bytes FROM (next_byte-chunk.byte_start+1)::integer FOR (LEAST(chunk.byte_end_exclusive,end_byte)-next_byte)::integer);
   data:=data||segment; next_byte:=LEAST(chunk.byte_end_exclusive,end_byte);
  END LOOP;
  IF next_byte<>end_byte OR encode(sha256(data),'hex') IS DISTINCT FROM lineage->>'source_bytes_sha256' THEN
   RAISE EXCEPTION 'source_revisiting_raw_integrity' USING ERRCODE='P0002'; END IF;
  n:=LEAST(lim,octet_length(data)-at_pos);
  result:=jsonb_build_object('content',NULL,'bytes_hex',encode(substring(data FROM at_pos+1 FOR n),'hex'),
   'next_offset',CASE WHEN at_pos+n<octet_length(data) THEN at_pos+n END);
  data:=substring(data FROM at_pos+1 FOR n);
 END IF;
 RETURN result||jsonb_build_object('request',request,'inventory',part.inventory,'sha256',encode(sha256(data),'hex'));
END; $$;
REVOKE ALL ON FUNCTION memoriesql.stored_bead_evidence_content(uuid,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.read_stored_bead_evidence_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; snapshot jsonb; content jsonb;
 selection jsonb:=request->'selection'; source uuid; anchor jsonb;
 unavailable constant jsonb:='{"contract_version":1,"outcome":"unavailable","evidence":null}';
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR request-ARRAY['contract_version','bead_id','selection']<>'{}'::jsonb
 OR request->>'contract_version' IS DISTINCT FROM '1' OR octet_length(request::text)>4096
 OR jsonb_typeof(selection) IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'invalid_stored_evidence_read' USING ERRCODE='22023'; END IF;
 -- Validate the public Q bound before any integer narrowing in storage mechanics.
 IF jsonb_typeof(selection->'offset') IS DISTINCT FROM 'number'
 OR COALESCE(selection->>'offset','') !~ '^[0-9]{1,6}$' THEN
  RAISE EXCEPTION 'invalid_stored_evidence_offset' USING ERRCODE='22023'; END IF;
 IF (selection->>'offset')::integer>262143 THEN
  RAISE EXCEPTION 'invalid_stored_evidence_offset' USING ERRCODE='22023'; END IF;
 snapshot:=memoriesql.inspect_stored_bead_v1(request-'selection');
 IF snapshot->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',1,'outcome',snapshot->>'outcome','evidence',NULL); END IF;
 -- Target package is whole-unit support. Other packages require an actually persisted
 -- classification support selection; inspected context alone is not a support link.
 IF selection->>'package_id' IS DISTINCT FROM snapshot#>>'{bead,package,package_id}'
 OR selection->>'inventory_sha256' IS DISTINCT FROM snapshot#>>'{bead,package,inventory_sha256}' THEN
  SELECT value->'selection' INTO anchor FROM jsonb_array_elements(snapshot#>'{bead,meaning,classification,evidence}')
   WHERE value#>>'{selection,package_id}'=selection->>'package_id'
   AND value#>>'{selection,inventory_sha256}'=selection->>'inventory_sha256'
   AND value#>>'{selection,part_ordinal}'=selection->>'part_ordinal'
   AND value#>>'{selection,representation}'=selection->>'representation'
   AND value#>'{selection,lineage_ordinal}' IS NOT DISTINCT FROM selection->'lineage_ordinal'
   AND (selection->>'offset')::bigint >= (value#>>'{selection,offset}')::bigint
   AND (selection->>'offset')::bigint+(selection->>'limit')::bigint <=
       (value#>>'{selection,offset}')::bigint+(value#>>'{selection,limit}')::bigint LIMIT 1;
  IF anchor IS NULL THEN RETURN unavailable; END IF;
 END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT source_object_id INTO source FROM memoriesql.evidence_packages
 WHERE tenant_id=c.tenant_id AND package_id=(selection->>'package_id')::uuid;
 PERFORM memoriesql.revisiting_source_authorize(source);
 content:=memoriesql.stored_bead_evidence_content(c.tenant_id,selection);
 PERFORM memoriesql.revisiting_source_authorize(source);
 -- Reauthorize the bead and every prerequisite on every page, never cache permission.
 snapshot:=memoriesql.inspect_stored_bead_v1(request-'selection');
 IF snapshot->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',1,'outcome',snapshot->>'outcome','evidence',NULL); END IF;
 IF octet_length(memoriesql.canonical_semantic_json_text(content))>262000 THEN
  RETURN '{"contract_version":1,"outcome":"budget_exhausted","evidence":null}'::jsonb; END IF;
 RETURN jsonb_build_object('contract_version',1,'outcome','available','evidence',content);
EXCEPTION WHEN insufficient_privilege OR no_data_found THEN RETURN unavailable;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.read_stored_bead_evidence_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_stored_bead_evidence_v1(jsonb) TO memoriesql_application;
