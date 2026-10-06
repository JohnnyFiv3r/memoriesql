-- Inspection withholds what queries withhold, and a refusal leaves no trace.
--
-- Stored-bead inspection (inspect_stored_bead_v1, latest installed in 0041) and
-- its evidence reader (read_stored_bead_evidence_v1, latest installed in 0024)
-- are restated from their installed text with exactly the edits that
-- tests/unit/test_inspection_withholding_migration.py lists. They implement the
-- owner's decisions of 2026-10-06:
-- - Decision 8, consistent withholding: the query population withholds a whole
--   note family (a note and the supersession neighbours of its accepted
--   version) when any member is unreadable. Inspection never consulted those
--   neighbours, so it returned a note that queries withheld. An accepted note
--   now must pass the population's own reads, for itself and for every
--   neighbour, or it reads exactly as a note that does not exist. The evidence
--   reader inspects the note before and after each page, so it withholds
--   alike. The published record shapes are unchanged.
-- - Decision 6 (pairs 3 and 4):
--   - A denial after the first authorized source returned normally, so its
--     "allowed" audit rows persisted where an unknown note writes none. Every
--     denial and every budget now raises inside the function's block, which
--     rolls its audit rows back; so do the evidence reader's refusals after
--     its first inspection, and its budget. Audit rows persist only for a read
--     that succeeds. The evidence reader's handler now maps every program
--     limit inside it to budget_exhausted, including the raw chunk work bound
--     that previously escaped as an unmapped error.
--   - Budgets were decided before the note was authorized, so an unreadable
--     note could report its size. Every statement under the watermark, every
--     source its evidence and context name, and the classification
--     contribution's checks (moved unchanged) now run before the first budget.
--     The response-size and time budgets follow the return boundary's
--     revalidation.
-- - The approved context-source fix: a context source is a unit of the
--   statement's own evidence, but inspection authorized its unit ID as a source
--   object, so every note with a context source inspected as unavailable. It is
--   now authorized through its event's source object, as evidence is. A missing
--   or unreadable context source reads exactly as an unknown note.
--
-- Relation inspection (inspect_bead_relations_v3_base,
-- relation_inspection_frame_v1, inspect_bead_relations_v2 and
-- inspect_bead_relations_v1, latest installed in 0030) is restated too, for
-- decision 6:
-- - Pair 3: every refusal after the first audit write now raises inside its
--   function's block. That covers the base's late denials; the frame's
--   closure, type and current-authority checks; and the wrappers' root and
--   legacy checks. The wrappers gain handlers that return their unchanged
--   refusal records.
-- - Pair 4: the base's response-size and time budgets move to the frame's
--   end, after every check. The time budget now covers the whole inspection,
--   two seconds from the statement's start, not only the base.
-- Decision 7 already holds there. Every endpoint and basis bead of an assessed
-- relation is pinned to its task, and inspection authorizes both beads of every
-- pinned pair that involves the inspected bead.
--
-- Forward-only; migrations 0001-0042 keep their bytes.

CREATE OR REPLACE FUNCTION memoriesql.inspect_stored_bead_v1(request jsonb) RETURNS jsonb
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
  b.access_scope_id,b.event_id,'memory.query','read') THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO ev FROM memoriesql.source_events WHERE tenant_id=b.tenant_id AND event_id=b.event_id;
 SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 sources:=array_append(sources,ev.source_object_id);
 PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
 -- Immutable accepted identity, never newest visible version.
 SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics a
 JOIN memoriesql.bead_versions bv USING(tenant_id,bead_version_id)
 WHERE a.tenant_id=b.tenant_id AND a.bead_id=b.bead_id;
 -- Owner decision 8 of 2026-10-06: inspection withholds exactly what the query
 -- population withholds. An accepted note's family, the note and every
 -- supersession neighbour of its accepted version, must pass the population's
 -- own reads at this instant; otherwise the note reads as one that does not
 -- exist.
 IF v.bead_version_id IS NOT NULL THEN
  PERFORM memoriesql.query_bead_records_v1(b.tenant_id,b.bead_id,clock_timestamp());
  FOR candidate_id IN SELECT CASE WHEN n.bead_id=b.bead_id THEN n.superseded_bead_id ELSE n.bead_id END
    FROM memoriesql.bead_supersessions n
    JOIN memoriesql.bead_versions nv ON nv.tenant_id=n.tenant_id AND nv.bead_version_id=n.bead_version_id
    WHERE n.tenant_id=b.tenant_id AND nv.authored_at<=clock_timestamp()
     AND ((n.superseded_bead_id=b.bead_id AND n.superseded_bead_version_id=v.bead_version_id)
       OR (n.bead_id=b.bead_id AND n.bead_version_id=v.bead_version_id)) LOOP
   PERFORM memoriesql.query_bead_records_v1(b.tenant_id,candidate_id,clock_timestamp());
  END LOOP;
 END IF;
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
  THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  SELECT * INTO r FROM memoriesql.semantic_task_receipts WHERE tenant_id=v.tenant_id AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  SELECT * INTO ir FROM memoriesql.idempotency_receipts WHERE tenant_id=r.tenant_id AND idempotency_receipt_id=r.idempotency_receipt_id;
  IF r.semantic_task_receipt_id IS NULL OR ir.status IS DISTINCT FROM 'succeeded' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  -- Accepted provenance is bound to its own receipt, never the newest source task.
  SELECT * INTO execution FROM memoriesql.complete_input_executions
   WHERE tenant_id=r.tenant_id AND execution_task_id=ir.resource_id;
  IF ir.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5','complete_input.apply.v6')
   AND execution.execution_task_id IS NULL THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
   WHERE q.tenant_id=r.tenant_id AND q.task_id=execution.execution_task_id;
  -- Every accepted author can depend on optional context, including revisions 3/4
  -- without a classifier. Conservatively authorize every pinned source.
  FOR item IN SELECT value FROM jsonb_array_elements(execution.authorized_context) LOOP
   source:=(item->>'source_object_id')::uuid;
   PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
  END LOOP;
  SELECT statement_watermark INTO watermark FROM memoriesql.bead_statement_revisions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id;
  -- Owner decision 6 of 2026-10-06 (pair 4): authorize every statement under
  -- the watermark, and every source its evidence and context name, before any
  -- budget, so a note the reader cannot read never reports its size.
  IF EXISTS(SELECT 1 FROM memoriesql.bead_semantic_statements q WHERE q.tenant_id=v.tenant_id
    AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
    AND NOT memoriesql.current_context_semantic_statement_authorized(q.tenant_id,q.workspace_id,q.access_scope_id,q.statement_id))
   THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM memoriesql.bead_semantic_statements q, unnest(q.context_source_ids) cs(unit_id)
    WHERE q.tenant_id=v.tenant_id AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
     AND NOT EXISTS(SELECT 1 FROM memoriesql.source_units cu WHERE cu.tenant_id=q.tenant_id AND cu.source_unit_id=cs.unit_id))
   THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  FOR source IN SELECT DISTINCT o.source_object_id FROM memoriesql.bead_semantic_statements q
    JOIN LATERAL (
     SELECT e.source_object_id FROM memoriesql.bead_semantic_statement_evidence x
      JOIN memoriesql.source_events e ON e.tenant_id=x.tenant_id AND e.event_id=x.evidence_event_id
      WHERE x.tenant_id=q.tenant_id AND x.statement_id=q.statement_id
     UNION ALL
     SELECT e.source_object_id FROM unnest(q.context_source_ids) cs(unit_id)
      JOIN memoriesql.source_units cu ON cu.tenant_id=q.tenant_id AND cu.source_unit_id=cs.unit_id
      JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id) o ON true
    WHERE q.tenant_id=v.tenant_id AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
    ORDER BY 1 LOOP
   PERFORM memoriesql.revisiting_source_authorize(source);
  END LOOP;
  -- Owner decision 6 of 2026-10-06 (pair 4): the classification contribution's
  -- checks, moved unchanged from after the mentions, deny before any budget.
  IF v.classification_contribution IS NOT NULL THEN
   IF r.task_contract_version<>5 OR r.task_contract_key<>'memory.semantic.author-complete-unit'
    OR ir.operation_kind<>'complete_input.apply.v4' OR ir.resource_id IS DISTINCT FROM execution.execution_task_id THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   SELECT * INTO intent FROM memoriesql.model_provider_request_intents WHERE tenant_id=v.tenant_id
    AND request_id=(v.classification_contribution->>'request_id')::uuid AND task_id=ir.resource_id;
   IF intent.request_id IS NULL OR intent.workspace_id<>c.workspace_id OR
    NOT memoriesql.current_context_scope_authorized(intent.access_scope_id,'memory.query','read') THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(v.classification_contribution->'evidence') LOOP
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=v.tenant_id AND package_id=(item#>>'{selection,package_id}')::uuid;
    IF p.package_id IS NULL OR p.inventory_hash IS DISTINCT FROM item#>>'{selection,inventory_sha256}' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    PERFORM memoriesql.revisiting_source_authorize(p.source_object_id); sources:=array_append(sources,p.source_object_id);
   END LOOP;
   provenance:=jsonb_build_object('request_id',intent.request_id,'task_id',intent.task_id,'attempt_id',intent.attempt_id,
    'model_id',intent.model_id,'model_profile_key',intent.model_profile_key,'model_profile_revision',intent.model_profile_revision,
    'authorization_policy_revision',intent.authorization_policy_revision,'quality_policy_revision_id',intent.quality_policy_revision_id,'pricing_revision_id',intent.pricing_revision_id);
  ELSIF r.task_contract_version=5 AND r.task_contract_key='memory.semantic.author-complete-unit' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
   WHERE tenant_id=v.tenant_id AND bead_id=v.bead_id AND statement_sequence<=watermark LIMIT 33) bounded;
  IF amount>32 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
  FOR s IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=v.tenant_id
   AND bead_id=v.bead_id AND statement_sequence<=watermark ORDER BY statement_sequence LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(s.tenant_id,s.workspace_id,s.access_scope_id,s.statement_id)
    THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   -- Context is provenance, not support; still requires authorization before disclosure.
   FOREACH source IN ARRAY s.context_source_ids LOOP
    -- A context source is a unit of the statement's own evidence. Authorize
    -- its event's source object, as evidence is; missing or unreadable reads
    -- exactly as an unknown note.
    SELECT e.source_object_id INTO source FROM memoriesql.source_units cu
     JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id
     WHERE cu.tenant_id=s.tenant_id AND cu.source_unit_id=source;
    IF NOT FOUND THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
   END LOOP;
   evidence:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id LIMIT 65) bounded;
   IF amount>64 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
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
     (r.task_contract_version=5 AND ir.operation_kind='complete_input.apply.v4') OR
     (r.task_contract_version=6 AND ir.operation_kind='complete_input.apply.v5') OR
     (r.task_contract_version=7 AND ir.operation_kind='complete_input.apply.v6')) THEN
   mentions:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id LIMIT 33) bounded;
   IF amount>32 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
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
 -- Revalidate all disclosed dependencies at the return boundary; audit writes only.
 FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
  PERFORM memoriesql.revisiting_source_authorize(source);
 END LOOP;
 -- Owner decision 6 of 2026-10-06 (pair 4): budgets only after every
 -- authorization.
 IF octet_length(memoriesql.canonical_semantic_json_text(result))>262144 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
 IF clock_timestamp()-started>interval '2 seconds' THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
 RETURN result;
EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;
 WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.read_stored_bead_evidence_v1(request jsonb) RETURNS jsonb
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
 IF snapshot->>'outcome'='budget_exhausted' THEN RAISE EXCEPTION 'stored_evidence_budget' USING ERRCODE='54000';
 ELSIF snapshot->>'outcome'<>'available' THEN RAISE EXCEPTION 'stored_evidence_unavailable' USING ERRCODE='42501'; END IF;
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
  IF anchor IS NULL THEN RAISE EXCEPTION 'stored_evidence_unavailable' USING ERRCODE='42501'; END IF;
 END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT source_object_id INTO source FROM memoriesql.evidence_packages
 WHERE tenant_id=c.tenant_id AND package_id=(selection->>'package_id')::uuid;
 PERFORM memoriesql.revisiting_source_authorize(source);
 content:=memoriesql.stored_bead_evidence_content(c.tenant_id,selection);
 PERFORM memoriesql.revisiting_source_authorize(source);
 -- Reauthorize the bead and every prerequisite on every page, never cache permission.
 snapshot:=memoriesql.inspect_stored_bead_v1(request-'selection');
 IF snapshot->>'outcome'='budget_exhausted' THEN RAISE EXCEPTION 'stored_evidence_budget' USING ERRCODE='54000';
 ELSIF snapshot->>'outcome'<>'available' THEN RAISE EXCEPTION 'stored_evidence_unavailable' USING ERRCODE='42501'; END IF;
 IF octet_length(memoriesql.canonical_semantic_json_text(content))>262000 THEN
  RAISE EXCEPTION 'stored_evidence_budget' USING ERRCODE='54000'; END IF;
 RETURN jsonb_build_object('contract_version',1,'outcome','available','evidence',content);
EXCEPTION WHEN insufficient_privilege OR no_data_found THEN RETURN unavailable;
 WHEN program_limit_exceeded THEN RETURN '{"contract_version":1,"outcome":"budget_exhausted","evidence":null}'::jsonb;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.inspect_bead_relations_v3_base(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms'
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE; ev memoriesql.source_events%ROWTYPE;
    rel memoriesql.bead_relations%ROWTYPE; ar memoriesql.assessed_relations%ROWTYPE;
    link record; row_item record;
    known timestamp with time zone; amount integer;
    relations jsonb := '[]'; dispositions jsonb := '[]'; assessments jsonb := '[]'; tasks jsonb := '[]';
    types jsonb; type_ids uuid[] := '{}'; statements jsonb; evidence jsonb; events jsonb; state jsonb; result jsonb;
    roots uuid[]; sources uuid[] := '{}'; source uuid;
    started timestamp with time zone := pg_catalog.clock_timestamp();
    unavailable constant jsonb := '{"contract_version":2,"outcome":"unavailable"}';
    budget constant jsonb := '{"contract_version":2,"outcome":"budget_exhausted"}';
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'bead_id', 'known_at'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '2'
       OR octet_length(request::text) > 1024
       OR COALESCE(request->>'bead_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR (request ? 'known_at' AND jsonb_typeof(request->'known_at') NOT IN ('string', 'null')) THEN
        RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE = '22023';
    END IF;
    -- A future known time reads as now; history is never extrapolated.
    known := LEAST(COALESCE((request->>'known_at')::timestamp with time zone, started), started);
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT bead.* INTO b FROM memoriesql.beads AS bead
    WHERE bead.tenant_id = c.tenant_id AND bead.workspace_id = c.workspace_id AND bead.bead_id = (request->>'bead_id')::uuid;
    IF b.bead_id IS NULL OR NOT memoriesql.current_context_event_authorized(b.access_scope_id, b.event_id, 'memory.query', 'read') THEN
        RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT e.* INTO ev FROM memoriesql.source_events AS e WHERE e.tenant_id = b.tenant_id AND e.event_id = b.event_id;
    sources := array_append(sources, ev.source_object_id);
    PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
    SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics AS a
    JOIN memoriesql.bead_versions AS bv ON bv.tenant_id = a.tenant_id AND bv.bead_version_id = a.bead_version_id
    WHERE a.tenant_id = b.tenant_id AND a.bead_id = b.bead_id AND bv.authored_at <= known;

    IF v.bead_version_id IS NOT NULL THEN
        IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id, v.workspace_id, v.access_scope_id, v.bead_version_id) THEN
            RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
        END IF;

        -- Revision-6 authored relations with this bead as an endpoint.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relations AS r
            WHERE r.tenant_id = v.tenant_id AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id)
              AND r.recorded_at <= known LIMIT 65) AS bounded;
        
        FOR rel IN SELECT r.* FROM memoriesql.bead_relations AS r
                   WHERE r.tenant_id = v.tenant_id AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id)
                     AND r.recorded_at <= known ORDER BY r.recorded_at, r.relation_id LOOP
            statements := '[]';
            FOR link IN SELECT rs.endpoint AS side, st.* FROM memoriesql.bead_relation_statements AS rs
                        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = rs.tenant_id AND st.statement_id = rs.statement_id
                        WHERE rs.tenant_id = rel.tenant_id AND rs.relation_id = rel.relation_id
                        ORDER BY rs.endpoint, st.statement_sequence LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.bead_version_id)
                   OR NOT memoriesql.current_context_semantic_statement_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.statement_id) THEN
                    RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
                END IF;
                statements := statements || jsonb_build_array(jsonb_build_object('side', link.side,
                    'statement', jsonb_build_object('statement_id', link.statement_id, 'bead_id', link.bead_id,
                        'bead_version_id', link.bead_version_id, 'text', link.statement_text)));
            END LOOP;
            evidence := memoriesql.relation_evidence_view_v3(rel.tenant_id, 'relation', rel.relation_id, known);
            sources := sources || ARRAY(SELECT (x.v->>'source')::uuid FROM jsonb_array_elements(evidence->'sources') AS x(v));
            SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relation_events AS e
                WHERE e.tenant_id = rel.tenant_id AND e.relation_id = rel.relation_id AND e.recorded_at <= known LIMIT 65) AS bounded;
            
            events := memoriesql.relation_events_view_v3(rel.tenant_id, 'authored', rel.relation_id, known);
            state := memoriesql.bead_relation_state_v1(rel.tenant_id, rel.relation_id, known);
            IF NOT memoriesql.relation_dependencies_readable_v1(rel.tenant_id, 'authored', rel.relation_id, state, known) THEN
                RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
            END IF;
            type_ids := array_append(type_ids, rel.relation_type_revision_id);
            relations := relations || jsonb_build_array(jsonb_build_object(
                'kind', 'authored', 'relation_id', rel.relation_id,
                'relation_type', memoriesql.relation_type_pin_v1(rel.relation_type_revision_id),
                'direction', CASE WHEN rel.source_bead_id = v.bead_id THEN 'outgoing' ELSE 'incoming' END,
                'source_bead_id', rel.source_bead_id, 'source_bead_version_id', rel.source_bead_version_id,
                'target_bead_id', rel.target_bead_id, 'target_bead_version_id', rel.target_bead_version_id,
                'source_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'source'), '[]'::jsonb),
                'target_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'target'), '[]'::jsonb),
                'basis_statements', '[]'::jsonb,
                'basis', rel.basis, 'rationale', rel.rationale_text, 'qualification', rel.qualification_text,
                'author_confidence', rel.author_confidence, 'evidence', evidence->'items',
                'independent_root_count', NULL,
                'authoring_bead_id', rel.authoring_bead_id, 'task_id', NULL, 'author_run_ref', rel.semantic_run_id,
                'specialist_run_ref', NULL, 'judgment', NULL,
                'recorded_at', memoriesql.relation_packet_time(rel.recorded_at),
                'state', state->'state', 'superseded_by', state->'superseded_by',
                'endpoint_corrected_by', state->'endpoint_corrected_by', 'events', events));
        END LOOP;

        -- Assessed proposals with this bead as an endpoint or a basis statement's bead.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.assessed_relations AS r
            WHERE r.tenant_id = v.tenant_id AND r.recorded_at <= known
              AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id OR EXISTS (
                    SELECT 1 FROM memoriesql.assessed_relation_statements AS s
                    WHERE s.tenant_id = r.tenant_id AND s.relation_id = r.relation_id AND s.bead_id = v.bead_id))
            LIMIT 65) AS bounded;
        
        FOR ar IN SELECT r.* FROM memoriesql.assessed_relations AS r
                  WHERE r.tenant_id = v.tenant_id AND r.recorded_at <= known
                    AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id OR EXISTS (
                          SELECT 1 FROM memoriesql.assessed_relation_statements AS s
                          WHERE s.tenant_id = r.tenant_id AND s.relation_id = r.relation_id AND s.bead_id = v.bead_id))
                  ORDER BY r.recorded_at, r.relation_id LOOP
            statements := '[]';
            FOR link IN SELECT rs.role AS side, st.* FROM memoriesql.assessed_relation_statements AS rs
                        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = rs.tenant_id AND st.statement_id = rs.statement_id
                        WHERE rs.tenant_id = ar.tenant_id AND rs.relation_id = ar.relation_id
                        ORDER BY rs.role, st.bead_id, st.statement_sequence LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.bead_version_id)
                   OR NOT memoriesql.current_context_semantic_statement_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.statement_id) THEN
                    RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
                END IF;
                statements := statements || jsonb_build_array(jsonb_build_object('side', link.side,
                    'statement', jsonb_build_object('statement_id', link.statement_id, 'bead_id', link.bead_id,
                        'bead_version_id', link.bead_version_id, 'text', link.statement_text)));
            END LOOP;
            evidence := memoriesql.relation_evidence_view_v3(ar.tenant_id, 'assessed_relation', ar.relation_id, known);
            sources := sources || ARRAY(SELECT (x.v->>'source')::uuid FROM jsonb_array_elements(evidence->'sources') AS x(v));
            events := memoriesql.relation_events_view_v3(ar.tenant_id, 'assessed', ar.relation_id, known);
            state := memoriesql.assessed_relation_state_v1(ar.tenant_id, ar.relation_id, known);
            IF NOT memoriesql.relation_dependencies_readable_v1(ar.tenant_id, 'assessed', ar.relation_id, state, known) THEN
                RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
            END IF;
            type_ids := array_append(type_ids, ar.relation_type_revision_id);
            type_ids := type_ids || ARRAY(
                SELECT r.relation_type_revision_id FROM jsonb_array_elements(ar.judgment->'warranted') AS w(v)
                JOIN memoriesql.relation_types AS ty ON ty.type_key = w.v#>>'{relation_type,key}'
                 AND (ty.tenant_id IS NULL OR (ty.tenant_id = ar.tenant_id AND ty.workspace_id = ar.workspace_id))
                JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
                 AND r.revision = (w.v#>>'{relation_type,revision}')::integer);
            relations := relations || jsonb_build_array(jsonb_build_object(
                'kind', 'assessed', 'relation_id', ar.relation_id,
                'relation_type', memoriesql.relation_type_pin_v1(ar.relation_type_revision_id),
                'direction', CASE WHEN ar.source_bead_id = v.bead_id AND ar.target_bead_id = v.bead_id THEN 'internal'
                                  WHEN ar.source_bead_id = v.bead_id THEN 'outgoing'
                                  WHEN ar.target_bead_id = v.bead_id THEN 'incoming' ELSE 'basis' END,
                'source_bead_id', ar.source_bead_id, 'source_bead_version_id', ar.source_bead_version_id,
                'target_bead_id', ar.target_bead_id, 'target_bead_version_id', ar.target_bead_version_id,
                'source_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'source'), '[]'::jsonb),
                'target_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'target'), '[]'::jsonb),
                'basis_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                              WHERE s.v->>'side' = 'basis'), '[]'::jsonb),
                'basis', ar.basis, 'rationale', ar.rationale_text, 'qualification', ar.qualification_text,
                'author_confidence', ar.author_confidence, 'evidence', evidence->'items',
                'independent_root_count', NULL,
                'authoring_bead_id', NULL, 'task_id', ar.task_id, 'author_run_ref', ar.author_run_id,
                'specialist_run_ref', ar.specialist_run_id, 'judgment', ar.judgment,
                'recorded_at', memoriesql.relation_packet_time(ar.recorded_at),
                'state', state->'state', 'superseded_by', state->'superseded_by',
                'endpoint_corrected_by', state->'endpoint_corrected_by', 'events', events));
        END LOOP;

        -- Pair coverage involving this bead, from every task that pinned it.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.relation_pair_dispositions AS d
            WHERE d.tenant_id = v.tenant_id AND (d.first_bead_id = v.bead_id OR d.second_bead_id = v.bead_id)
              AND d.recorded_at <= known LIMIT 65) AS bounded;
        
        FOR row_item IN SELECT d.* FROM memoriesql.relation_pair_dispositions AS d
                        WHERE d.tenant_id = v.tenant_id AND (d.first_bead_id = v.bead_id OR d.second_bead_id = v.bead_id)
                          AND d.recorded_at <= known ORDER BY d.recorded_at, d.task_id, d.first_bead_id, d.second_bead_id LOOP
            IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.first_access_scope_id, row_item.first_bead_version_id)
               OR NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.second_access_scope_id, row_item.second_bead_version_id) THEN
                RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
            END IF;
            dispositions := dispositions || jsonb_build_array(jsonb_build_object(
                'task_id', row_item.task_id, 'first_bead_id', row_item.first_bead_id,
                'first_bead_version_id', row_item.first_bead_version_id, 'second_bead_id', row_item.second_bead_id,
                'second_bead_version_id', row_item.second_bead_version_id, 'disposition', row_item.disposition,
                'abstention', row_item.abstention, 'reason', row_item.reason_text));
        END LOOP;

        -- Revision-6 coverage recorded by this bead's own authorship.
        FOR row_item IN SELECT a.* FROM memoriesql.relation_candidate_assessments AS a
                        WHERE a.tenant_id = v.tenant_id AND a.authoring_bead_id = v.bead_id AND a.recorded_at <= known
                        ORDER BY a.candidate_bead_id LOOP
            IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.candidate_access_scope_id, row_item.candidate_bead_version_id) THEN
                RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
            END IF;
            assessments := assessments || jsonb_build_array(jsonb_build_object(
                'candidate_bead_id', row_item.candidate_bead_id, 'candidate_bead_version_id', row_item.candidate_bead_version_id,
                'assessment', row_item.assessment, 'reason', row_item.reason_text));
        END LOOP;

        -- Relation tasks whose subject is this bead, with their current visible status.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.relation_assessments AS ra
            WHERE ra.tenant_id = v.tenant_id AND ra.subject_bead_id = v.bead_id AND ra.created_at <= known LIMIT 17) AS bounded;
        
        FOR row_item IN SELECT ra.*, q.status AS task_status, q.completed_at AS task_completed_at,
                               COALESCE(q.pause_reason_code, (SELECT at.error_code FROM memoriesql.semantic_task_attempts AS at
                                                              WHERE at.tenant_id = q.tenant_id AND at.task_id = q.task_id
                                                              ORDER BY at.attempt_id DESC LIMIT 1)) AS task_reason
                        FROM memoriesql.relation_assessments AS ra
                        JOIN memoriesql.semantic_tasks AS q ON q.tenant_id = ra.tenant_id AND q.task_id = ra.task_id
                        WHERE ra.tenant_id = v.tenant_id AND ra.subject_bead_id = v.bead_id AND ra.created_at <= known
                        ORDER BY ra.created_at, ra.task_id LOOP
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(row_item.beads) AS pb(v)
                       JOIN memoriesql.accepted_bead_semantics AS a
                         ON a.tenant_id = row_item.tenant_id AND a.bead_id = (pb.v->>'bead_id')::uuid
                       WHERE NOT memoriesql.current_context_bead_version_authorized(a.tenant_id, a.workspace_id,
                                 a.access_scope_id, a.bead_version_id)) THEN
                RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
            END IF;
            tasks := tasks || jsonb_build_array(jsonb_build_object(
                'task_id', row_item.task_id, 'subject_bead_version_id', row_item.subject_bead_version_id,
                'candidate_bead_ids', to_jsonb(row_item.candidate_bead_ids),
                'reconsiders_task_id', row_item.reconsiders_task_id, 'status', row_item.task_status,
                'status_reason', row_item.task_reason,
                'activated_at', memoriesql.relation_packet_time(row_item.created_at),
                'completed_at', memoriesql.relation_packet_time(row_item.task_completed_at)));
        END LOOP;
    END IF;

    SELECT COALESCE(jsonb_agg(memoriesql.relation_type_definition_v1(ids.id) ORDER BY ids.id), '[]'::jsonb) INTO types
    FROM (SELECT DISTINCT unnest(type_ids) AS id) AS ids;
    result := jsonb_build_object('contract_version', 2, 'outcome', 'available', 'bead_id', b.bead_id,
        'known_at', memoriesql.relation_packet_time(known), 'relation_types', types, 'relations', relations,
        'pair_dispositions', dispositions, 'candidate_assessments', assessments, 'relation_tasks', tasks);
    -- Owner decision 6 of 2026-10-06 (pair 4): the frame decides the response
    -- size and time budgets, after its own checks.
    -- Revalidate every disclosed source dependency at the return boundary.
    FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
        PERFORM memoriesql.revisiting_source_authorize(source);
    END LOOP;
    RETURN result;
EXCEPTION
    WHEN insufficient_privilege THEN RETURN unavailable;
    -- A derivation lineage over its limit is reported, never truncated.
    WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.relation_inspection_frame_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; response jsonb; row_item jsonb; q jsonb; roots_status text; roots jsonb; rows jsonb:='[]'; deps jsonb:='[]'; evidence jsonb; manifest jsonb; known timestamptz; snapshot_at timestamptz:=statement_timestamp(); d jsonb; r record;
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR NOT(request ?& ARRAY['contract_version','bead_id','known_at']) OR request-ARRAY['contract_version','bead_id','known_at']<>'{}' OR request->'contract_version'<>'3'::jsonb OR COALESCE(request->>'bead_id','')!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' OR jsonb_typeof(request->'known_at') NOT IN('string','null') THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}'; END IF;
 IF request->>'known_at' IS NOT NULL AND request->>'known_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}'; END IF;
 known:=CASE WHEN request->>'known_at' IS NULL THEN snapshot_at ELSE memoriesql.lifecycle_parse_time_v1(request->>'known_at') END;
 IF NOT isfinite(known) OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 OR known>snapshot_at THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}'; END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id) THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF;
 response:=memoriesql.inspect_bead_relations_v3_base(request||jsonb_build_object('contract_version',2,'known_at',memoriesql.relation_packet_time(known)));
 IF response->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',3,'outcome',response->'outcome'); END IF;
 FOR row_item IN SELECT value FROM jsonb_array_elements(response->'relations') ORDER BY value->>'relation_id' LOOP
  IF NOT memoriesql.relation_closure_authorized_v1(c.tenant_id,row_item->>'kind',(row_item->>'relation_id')::uuid,known) THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF;
  evidence:=memoriesql.relation_evidence_view_v3(c.tenant_id,CASE WHEN row_item->>'kind'='authored' THEN 'relation' ELSE 'assessed_relation' END,(row_item->>'relation_id')::uuid,known);
  deps:=deps||(evidence->'dependencies')||memoriesql.relation_assertion_records_v1(c.tenant_id,row_item->>'kind',(row_item->>'relation_id')::uuid,known);
  row_item:=memoriesql.relation_assertion_row_v1(c.tenant_id,row_item->>'kind',(row_item->>'relation_id')::uuid,(request->>'bead_id')::uuid,known);
  rows:=rows||jsonb_build_array(row_item);
 END LOOP;
 response:=response||jsonb_build_object('contract_version',3,'relations',rows,
 'relation_tasks',(SELECT COALESCE(jsonb_agg(value||jsonb_build_object('status_known_at',memoriesql.relation_packet_time(snapshot_at)) ORDER BY value->>'task_id'),'[]') FROM jsonb_array_elements(response->'relation_tasks')),
 'relation_types',(SELECT COALESCE(jsonb_agg(value ORDER BY value->>'key',(value->>'revision')::integer),'[]') FROM jsonb_array_elements(response->'relation_types')),
 'pair_dispositions',(SELECT COALESCE(jsonb_agg(value ORDER BY value->>'task_id',value->>'first_bead_id',value->>'second_bead_id'),'[]') FROM jsonb_array_elements(response->'pair_dispositions')));
 -- Retain each disclosed record class independently; clocks label selection only.
 deps:=deps||memoriesql.relation_bead_records_v1(c.tenant_id,(request->>'bead_id')::uuid,known);
 FOR d IN SELECT value FROM jsonb_array_elements(response->'relations') LOOP deps:=deps||jsonb_build_array(jsonb_build_object('kind','assertion_projection','id',d->>'relation_id','row',d)); END LOOP;
 FOR d IN SELECT value FROM jsonb_array_elements(response->'relation_types') LOOP SELECT tr.relation_type_revision_id INTO r FROM memoriesql.relation_type_revisions tr JOIN memoriesql.relation_types ty ON ty.relation_type_id=tr.relation_type_id WHERE ty.type_key=d->>'key' AND tr.revision=(d->>'revision')::integer AND ty.namespace=d->>'namespace' AND (ty.tenant_id IS NULL OR (ty.tenant_id=c.tenant_id AND ty.workspace_id=c.workspace_id)); IF NOT FOUND THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF; deps:=deps||jsonb_build_array(jsonb_build_object('kind','type_definition','id',r.relation_type_revision_id::text,'row',d)); END LOOP;
 FOR d IN SELECT value FROM jsonb_array_elements(response->'pair_dispositions') LOOP deps:=deps||jsonb_build_array(jsonb_build_object('kind','pair_disposition','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(d->'task_id',d->'first_bead_id',d->'second_bead_id')),'row',d)); END LOOP;
 FOR d IN SELECT value FROM jsonb_array_elements(response->'candidate_assessments') LOOP deps:=deps||jsonb_build_array(jsonb_build_object('kind','candidate_assessment','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(request->'bead_id',d->'candidate_bead_id',d->'candidate_bead_version_id')),'row',d)); END LOOP;
 FOR d IN SELECT value FROM jsonb_array_elements(response->'relation_tasks') LOOP deps:=deps||jsonb_build_array(jsonb_build_object('kind','task_status','id',d->>'task_id','row',d)); END LOOP;
 -- Coverage and task-only candidates are protected dependencies even when
 -- no assertion was warranted. Retain their exact accepted records as well.
 FOR r IN SELECT DISTINCT bead_id FROM (
 SELECT (v->>'first_bead_id')::uuid bead_id FROM jsonb_array_elements(response->'pair_dispositions') x(v)
 UNION SELECT (v->>'second_bead_id')::uuid FROM jsonb_array_elements(response->'pair_dispositions') x(v)
 UNION SELECT (v->>'candidate_bead_id')::uuid FROM jsonb_array_elements(response->'candidate_assessments') x(v)
 UNION SELECT candidate::text::uuid FROM jsonb_array_elements(response->'relation_tasks') x(v) CROSS JOIN LATERAL jsonb_array_elements_text(v->'candidate_bead_ids') c(candidate)
 ) closure LOOP deps:=deps||memoriesql.relation_bead_records_v1(c.tenant_id,r.bead_id,known); END LOOP;
 SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO deps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(deps)) x;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id','content_sha256',memoriesql.lifecycle_hash_v1(v->'row')) ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO manifest FROM jsonb_array_elements(deps) x(v);
 response:=response||jsonb_build_object('frame',jsonb_build_object('known_at',memoriesql.relation_packet_time(known),'snapshot_at',memoriesql.relation_packet_time(snapshot_at),'dependency_manifest_sha256',memoriesql.lifecycle_hash_v1(manifest)));
 IF NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF;
 -- Owner decision 6 of 2026-10-06 (pair 4): the response size and time
 -- budgets, the base's included, only after every check.
 IF octet_length(memoriesql.lifecycle_canonical_json_v1(response))>524288 THEN RAISE EXCEPTION 'bead_relations_budget' USING ERRCODE='54000'; END IF;
 IF clock_timestamp()-snapshot_at>interval '2 seconds' THEN RAISE EXCEPTION 'bead_relations_budget' USING ERRCODE='54000'; END IF;
 RETURN jsonb_build_object('response',response,'dependency_records',deps,'dependency_manifest',manifest);
EXCEPTION WHEN insufficient_privilege THEN RETURN '{"contract_version":3,"outcome":"unavailable"}';
 WHEN query_canceled OR lock_not_available OR program_limit_exceeded THEN RETURN '{"contract_version":3,"outcome":"budget_exhausted"}';
 WHEN invalid_parameter_value OR invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}';
END $$;

CREATE OR REPLACE FUNCTION memoriesql.inspect_bead_relations_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE result jsonb; rows jsonb:='[]'; r jsonb; evidence jsonb; events jsonb;
BEGIN
 IF request->>'contract_version'<>'2' THEN RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE='22023'; END IF;
 result:=memoriesql.inspect_bead_relations_v3(request||jsonb_build_object('contract_version',3,'known_at',request->'known_at'));
 IF result->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',2,'outcome',CASE WHEN result->>'outcome'='budget_exhausted' THEN 'budget_exhausted' ELSE 'unavailable' END); END IF;
 FOR r IN SELECT value FROM jsonb_array_elements(result->'relations') LOOP
 -- Owner decision 6 of 2026-10-06 (pair 3): a refusal after the frame's
 -- success rolls back its audit rows.
 IF r->>'roots_status'<>'qualified' THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(value-ARRAY['roots_status','roots_gap_relation_ids']),'[]') INTO evidence FROM jsonb_array_elements(r->'evidence');
 SELECT COALESCE(jsonb_agg(value-ARRAY['event_number','previous_event_id','recorded_by_principal_id','recorded_by_user_id','idempotency_receipt_id','evidence']),'[]') INTO events FROM jsonb_array_elements(r->'events');
 rows:=rows||jsonb_build_array((r-ARRAY['acceptance','acceptance_receipt_id','head_token','support_eligible','support_reason','correction_pending','basis_corrected_by','roots_status'])||jsonb_build_object('evidence',evidence,'events',events));
 END LOOP;
 RETURN (result-'frame')||jsonb_build_object('contract_version',2,'relations',rows,'relation_tasks',(SELECT COALESCE(jsonb_agg(value-'status_known_at'),'[]') FROM jsonb_array_elements(result->'relation_tasks')));
EXCEPTION WHEN insufficient_privilege THEN RETURN '{"contract_version":2,"outcome":"unavailable"}';
END $$;

CREATE OR REPLACE FUNCTION memoriesql.inspect_bead_relations_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE projected jsonb;
BEGIN
 IF request->>'contract_version'<>'1' THEN RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE='22023'; END IF;
 projected:=memoriesql.inspect_bead_relations_v3(request||jsonb_build_object('contract_version',3,'known_at',request->'known_at'));
 IF projected->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',1,'outcome',CASE WHEN projected->>'outcome'='budget_exhausted' THEN 'budget_exhausted' ELSE 'unavailable' END); END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(projected->'relations') r(v) WHERE v->>'roots_status'<>'qualified' OR v->>'kind'='assessed') THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF;
 -- Owner decision 6 of 2026-10-06 (pair 3): a refusal after the frame's
 -- success rolls back its audit rows, the legacy reader's included.
 projected:=memoriesql.inspect_bead_relations_v1_schema29(request||jsonb_build_object('known_at',projected->'known_at'));
 IF projected->>'outcome'='budget_exhausted' THEN RAISE EXCEPTION 'bead_relations_budget' USING ERRCODE='54000';
 ELSIF projected->>'outcome'<>'available' THEN RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE='42501'; END IF;
 RETURN projected;
EXCEPTION WHEN insufficient_privilege THEN RETURN '{"contract_version":1,"outcome":"unavailable"}';
 WHEN program_limit_exceeded THEN RETURN '{"contract_version":1,"outcome":"budget_exhausted"}';
END $$;
