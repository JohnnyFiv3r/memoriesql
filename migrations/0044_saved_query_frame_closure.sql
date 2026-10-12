-- PR-05 saved-frame closure conformance. Coordinator allocated forward 0044.
-- Preserve the actually captured semantic frame; known_at is not a barrier
-- proving that earlier-stamped transactions were already committed.
-- The existing v2 entry point retains identity, expiry, artifact/manifest and
-- ownership checks. Only its fresh-population subset check is substituted.
-- Current authority applies to every ORIGINAL protected dependency, including
-- roots, task populations, lifecycle evidence and original replacement chains.
-- Neither discovery nor the canonical lifecycle projector changes. No new
-- grants, relation policy, timing floor, admission number or result wire shape.

-- Same immutable event representation and source gate as schema39, restricted
-- to exact event IDs carried by the retained head. Never discovers late events.
CREATE FUNCTION memoriesql.query_saved_events_v1(t uuid,k text,id uuid,known timestamptz,read_mode text,ids uuid[]) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE e record; item jsonb; result jsonb:='[]'; pairs jsonb; link record; src uuid;
BEGIN
 FOR e IN SELECT * FROM (
 SELECT g.relation_event_id event_id,g.relation_id target_id,g.action,g.replacement_relation_id related_id,g.reason,g.origin,NULL::uuid authoring_bead_id,g.effective_at,g.recorded_at,NULL::integer event_number,NULL::uuid previous_event_id,g.recorded_by_principal_id,p.user_id recorded_by_user_id,g.idempotency_receipt_id FROM memoriesql.bead_relation_events g JOIN memoriesql.principals p ON p.tenant_id=g.tenant_id AND p.principal_id=g.recorded_by_principal_id WHERE k='authored' AND g.tenant_id=t AND g.relation_id=id AND g.recorded_at<=known
 UNION ALL SELECT g.relation_event_id,g.relation_id,g.action,NULL,g.reason,'governed',NULL,g.effective_at,g.recorded_at,g.event_number,g.previous_event_id,g.recorded_by_principal_id,g.recorded_by_user_id,g.idempotency_receipt_id FROM memoriesql.assessed_relation_events g WHERE k='assessed' AND g.tenant_id=t AND g.relation_id=id AND g.recorded_at<=known
 UNION ALL SELECT x.retirement_id,x.retired_relation_id,'retire',x.replacement_relation_id,x.reason_text,'authored',NULL,NULL,x.recorded_at,NULL,NULL,x.recorded_by_principal_id,p.user_id,x.idempotency_receipt_id FROM memoriesql.relation_retirements x JOIN memoriesql.principals p ON p.tenant_id=x.tenant_id AND p.principal_id=x.recorded_by_principal_id WHERE x.tenant_id=t AND x.retired_kind=k AND x.retired_relation_id=id AND x.recorded_at<=known) hist WHERE event_id=ANY(ids) ORDER BY recorded_at,event_number NULLS LAST,event_id LOOP
  IF k='assessed' AND e.origin='governed' THEN SELECT evidence INTO pairs FROM memoriesql.assessed_relation_events WHERE tenant_id=t AND relation_event_id=e.event_id;
  ELSE
   pairs:='[]';
   FOR link IN SELECT l.statement_id,l.evidence_source_unit_id,x.evidence_content_hash,x.access_scope_id,x.evidence_event_id FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind='relation_event' AND l.owner_id=e.event_id AND l.recorded_at<=known ORDER BY l.statement_id,l.evidence_source_unit_id LOOP
    IF NOT memoriesql.current_context_event_authorized(link.access_scope_id,link.evidence_event_id,'memory.query','read') THEN RAISE EXCEPTION 'event_unavailable' USING ERRCODE='42501'; END IF;
    SELECT source_object_id INTO src FROM memoriesql.source_events WHERE tenant_id=t AND event_id=link.evidence_event_id; PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,link.access_scope_id,link.evidence_event_id,src);
    pairs:=pairs||jsonb_build_array(jsonb_build_object('statement_id',link.statement_id,'source_unit_id',link.evidence_source_unit_id,'content_hash',link.evidence_content_hash));
   END LOOP;
  END IF;
  item:=to_jsonb(e)||jsonb_build_object('effective_at',memoriesql.relation_packet_time(e.effective_at),'recorded_at',memoriesql.relation_packet_time(e.recorded_at),'evidence',pairs);
  result:=result||jsonb_build_array(item);
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_saved_events_v1(uuid,text,uuid,timestamptz,text,uuid[]) FROM PUBLIC;

-- Private: invoked only after the sealed partitions and original owner binding
-- have been checked. Unknown dependency kinds and absent canonical rows refuse.
CREATE FUNCTION memoriesql.query_saved_records_authorized_v1(records jsonb,known timestamptz)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE
 c memoriesql.authorization_contexts%ROWTYPE;
 d jsonb; row_data jsonb; expected jsonb; rebuilt jsonb:='[]'; required jsonb;
 head jsonb; heads jsonb; projection jsonb; events jsonb; event jsonb; item jsonb;
 pins jsonb; raw jsonb; event_ids uuid[];
 r record; retained_pin record; link record; task record; src record;
 mode text; relation_kind text; saved_relation_id uuid; saved_bead_id uuid; saved_unit_id uuid;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF jsonb_typeof(records) IS DISTINCT FROM 'array' OR known IS NULL
  OR NOT isfinite(known) OR known>statement_timestamp()
  OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(records) x(v)
   WHERE jsonb_typeof(v) IS DISTINCT FROM 'object'
    OR v-ARRAY['kind','id','row']<>'{}'::jsonb
    OR jsonb_typeof(v->'kind') IS DISTINCT FROM 'string'
    OR jsonb_typeof(v->'id') IS DISTINCT FROM 'string'
    OR jsonb_typeof(v->'row') IS DISTINCT FROM 'object')
  OR (SELECT count(*)<>count(DISTINCT jsonb_build_array(v->'kind',v->'id')) FROM jsonb_array_elements(records) x(v)) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 mode:=CASE WHEN memoriesql.current_context_has_capability('source.raw.read') THEN 'owner'
  WHEN c.principal_kind='agent' AND c.pairing_grant_id IS NOT NULL THEN 'agent' ELSE 'owner' END;

 -- Immutable observation/source records have the same query authority as the
 -- original population, without asking which families a NEW snapshot selects.
 FOR saved_bead_id IN SELECT DISTINCT (v->>'id')::uuid FROM jsonb_array_elements(records) x(v)
  WHERE v->>'kind'='bead' LOOP
  rebuilt:=rebuilt||memoriesql.query_bead_records_v1(c.tenant_id,saved_bead_id,known);
 END LOOP;
 FOR saved_unit_id IN SELECT DISTINCT (v->>'id')::uuid FROM jsonb_array_elements(records) x(v)
  WHERE v->>'kind'='source_unit' LOOP
  rebuilt:=rebuilt||memoriesql.query_unit_records_v1(c.tenant_id,saved_unit_id,known);
 END LOOP;

 -- Revalidate ORIGINAL relation families. No relation_projection, population
 -- preparation, new lifecycle selection or new replacement traversal is used.
 FOR d IN SELECT v FROM jsonb_array_elements(records) x(v)
  WHERE v->>'kind'='assertion_acceptance' LOOP
  saved_relation_id:=(d->>'id')::uuid; relation_kind:=d#>>'{row,kind}';
  SELECT * INTO r FROM memoriesql.relation_assertions_v1(c.tenant_id,known)
   WHERE kind=relation_kind AND relation_id=saved_relation_id;
  IF NOT FOUND OR r.workspace_id IS DISTINCT FROM c.workspace_id THEN
   RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
  END IF;
  expected:=jsonb_build_object('kind',r.kind,'relation_id',r.relation_id,'acceptance',r.acceptance,
   'acceptance_receipt_id',r.acceptance_receipt_id,'recorded_at',memoriesql.relation_packet_time(r.recorded_at),
   'source_bead_id',r.source_bead_id,'source_bead_version_id',r.source_bead_version_id,
   'target_bead_id',r.target_bead_id,'target_bead_version_id',r.target_bead_version_id,
   'relation_type_revision_id',r.relation_type_revision_id);
  IF d->'row' IS DISTINCT FROM expected THEN
   RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
  END IF;
  rebuilt:=rebuilt||jsonb_build_array(d,
   jsonb_build_object('kind','type_definition','id',r.relation_type_revision_id::text,
    'row',memoriesql.relation_type_definition_v1(r.relation_type_revision_id)));
  SELECT jsonb_build_object('idempotency_receipt_id',idempotency_receipt_id,'operation_kind',operation_kind,
   'resource_kind',resource_kind,'resource_id',resource_id,'request_hash',request_hash) INTO expected
   FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id
    AND idempotency_receipt_id=r.acceptance_receipt_id;
  rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','acceptance_receipt',
   'id',r.acceptance_receipt_id::text,'row',expected));
  required:=memoriesql.relation_bead_records_v2(c.tenant_id,r.source_bead_id,known,mode)
   ||memoriesql.relation_bead_records_v2(c.tenant_id,r.target_bead_id,known,mode);
  FOR retained_pin IN SELECT DISTINCT s.bead_id FROM memoriesql.assessed_relation_statements s
   WHERE r.kind='assessed' AND s.tenant_id=c.tenant_id AND s.relation_id=r.relation_id AND s.role='basis' LOOP
   required:=required||memoriesql.relation_bead_records_v2(c.tenant_id,retained_pin.bead_id,known,mode);
  END LOOP;
  IF r.kind='assessed' THEN
   SELECT a.judgment,to_jsonb(a) INTO expected,raw FROM memoriesql.assessed_relations a
    WHERE a.tenant_id=c.tenant_id AND a.relation_id=r.relation_id;
   rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','specialist_judgment',
    'id',r.relation_id::text,'row',expected));
   required:=required||memoriesql.relation_task_population_records_v1(
    c.tenant_id,(raw->>'task_id')::uuid,known,mode);
  ELSE
   SELECT to_jsonb(a) INTO raw FROM memoriesql.bead_relations a
    WHERE a.tenant_id=c.tenant_id AND a.relation_id=r.relation_id;
  END IF;

  SELECT v->'row' INTO head FROM jsonb_array_elements(records) x(v)
   WHERE v->>'kind'='relation_head' AND v->>'id'=r.relation_id::text;
  IF head IS NULL OR head-ARRAY['events','corrections'] IS DISTINCT FROM
   jsonb_build_object('projection_version',1,'tenant_id',c.tenant_id,'workspace_id',r.workspace_id,
    'relation_id',r.relation_id,'acceptance_receipt_id',r.acceptance_receipt_id,'acceptance',r.acceptance)
   OR jsonb_typeof(head->'events') IS DISTINCT FROM 'array'
   OR jsonb_typeof(head->'corrections') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
  END IF;
  SELECT array_agg((v->>'event_id')::uuid) INTO event_ids FROM jsonb_array_elements(head->'events') x(v);
  events:=memoriesql.query_saved_events_v1(c.tenant_id,r.kind,r.relation_id,known,mode,COALESCE(event_ids,'{}'));
  SELECT COALESCE(jsonb_agg(jsonb_build_object('origin',v->'origin','event_id',v->'event_id',
   'event_number',v->'event_number','recorded_at',v->'recorded_at')
   ORDER BY (v->>'recorded_at')::timestamptz,(v->>'event_number')::integer NULLS LAST,v->>'event_id'),'[]')
   INTO heads FROM jsonb_array_elements(events) x(v);
  IF heads IS DISTINCT FROM head->'events' THEN
   RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
  END IF;
  rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','relation_head','id',r.relation_id::text,'row',head));
  FOR event IN SELECT v FROM jsonb_array_elements(events) x(v) LOOP
   IF event->>'related_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(records) x(v)
     WHERE v->>'kind'='assertion_acceptance' AND v->>'id'=event->>'related_id'
      AND v#>>'{row,kind}'=CASE WHEN event->>'action'='retire' THEN 'assessed' ELSE r.kind END
   ) THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
   rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','relation_event','id',event->>'event_id','row',event));
   IF mode='owner' THEN
    FOR task IN SELECT a.task_id FROM memoriesql.relation_retirements a
     WHERE a.tenant_id=c.tenant_id AND a.retirement_id=(event->>'event_id')::uuid LOOP
     required:=required||memoriesql.relation_task_population_records_v1(c.tenant_id,task.task_id,known,mode);
    END LOOP;
   END IF;
   FOR item IN SELECT v FROM jsonb_array_elements(event->'evidence') x(v) LOOP
    SELECT s.* INTO retained_pin FROM memoriesql.bead_semantic_statements s
     JOIN memoriesql.bead_semantic_statement_evidence e
      ON e.tenant_id=s.tenant_id AND e.statement_id=s.statement_id
     WHERE s.tenant_id=c.tenant_id AND s.statement_id=(item->>'statement_id')::uuid
      AND e.evidence_source_unit_id=(item->>'source_unit_id')::uuid
      AND e.evidence_content_hash=item->>'content_hash';
    IF NOT FOUND OR NOT memoriesql.current_context_bead_version_authorized(
     c.tenant_id,retained_pin.workspace_id,retained_pin.access_scope_id,retained_pin.bead_version_id)
     OR NOT memoriesql.current_context_semantic_statement_authorized(
      c.tenant_id,retained_pin.workspace_id,retained_pin.access_scope_id,retained_pin.statement_id) THEN
     RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
    END IF;
    required:=required||memoriesql.relation_unit_records_v2(c.tenant_id,(item->>'source_unit_id')::uuid,known,mode)
     ||memoriesql.relation_bead_records_v2(c.tenant_id,retained_pin.bead_id,known,mode);
    rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','lifecycle_evidence',
     'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(event->'event_id',item->'statement_id',item->'source_unit_id')),'row',item));
   END LOOP;
  END LOOP;

  -- Original correction pins require their immutable accepted versions and
  -- actual supersession paths. Restrict paths to versions retained in this
  -- frame: a later protected replacement is not implicitly authorized.
  FOR item IN SELECT v FROM jsonb_array_elements(head->'corrections') x(v) LOOP
   IF NOT EXISTS(SELECT 1 FROM (
    SELECT 'source' role,r.source_bead_id bead_id
    UNION ALL SELECT 'target',r.target_bead_id
    UNION ALL SELECT 'basis',a.bead_id FROM memoriesql.assessed_relation_statements a
     WHERE r.kind='assessed' AND a.tenant_id=c.tenant_id AND a.relation_id=r.relation_id AND a.role='basis'
   ) original_pin WHERE original_pin.role=item->>'role'
    AND original_pin.bead_id=(item->>'pinned_bead_id')::uuid) OR NOT EXISTS(
    SELECT 1 FROM memoriesql.bead_versions v JOIN memoriesql.accepted_bead_semantics a
     ON a.tenant_id=v.tenant_id AND a.bead_version_id=v.bead_version_id
    WHERE v.tenant_id=c.tenant_id AND v.bead_id=(item->>'successor_bead_id')::uuid
     AND v.bead_version_id=(item->>'successor_bead_version_id')::uuid
     AND to_jsonb(memoriesql.relation_packet_time(v.authored_at))=item->'recorded_at') THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
   IF NOT EXISTS(WITH RECURSIVE path(bead_id,version_id) AS (
    SELECT (item->>'pinned_bead_id')::uuid,NULL::uuid
    UNION SELECT s.bead_id,s.bead_version_id FROM path p JOIN memoriesql.bead_supersessions s
     ON s.tenant_id=c.tenant_id AND s.superseded_bead_id=p.bead_id
     WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(records) x(v)
      WHERE v->>'kind'='accepted_bead' AND v->>'id'=s.bead_version_id::text)
   ) SELECT 1 FROM path WHERE bead_id=(item->>'successor_bead_id')::uuid
    AND version_id=(item->>'successor_bead_version_id')::uuid) THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
   required:=required||memoriesql.relation_bead_records_v2(c.tenant_id,(item->>'successor_bead_id')::uuid,known,mode);
   rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','correction_pin_'||(item->>'role'),
    'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(r.relation_id,item->'pinned_bead_id',item->'successor_bead_id',item->'successor_bead_version_id')),'row',item));
  END LOOP;
  FOR item IN SELECT v FROM jsonb_array_elements(records) x(v)
   WHERE v->>'kind'='assertion_evidence'
    AND (v->>'id')::jsonb->>0=r.relation_id::text LOOP
   SELECT l.statement_id,l.evidence_source_unit_id,x.evidence_content_hash INTO link
    FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x
     ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id
    WHERE l.tenant_id=c.tenant_id AND l.owner_kind=CASE WHEN r.kind='authored' THEN 'relation' ELSE 'assessed_relation' END
     AND l.owner_id=r.relation_id AND l.recorded_at<=known
     AND l.statement_id=(item#>>'{row,statement_id}')::uuid
     AND l.evidence_source_unit_id=(item#>>'{row,source_unit_id}')::uuid;
   IF NOT FOUND OR item->>'id' IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(
    jsonb_build_array(r.relation_id,link.statement_id,link.evidence_source_unit_id))
    OR item->'row' IS DISTINCT FROM jsonb_build_object('statement_id',link.statement_id,
     'source_unit_id',link.evidence_source_unit_id,'content_hash',link.evidence_content_hash) THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
   required:=required||memoriesql.relation_unit_records_v2(c.tenant_id,link.evidence_source_unit_id,known,mode);
   rebuilt:=rebuilt||jsonb_build_array(item);
  END LOOP;
  -- Mandatory original endpoint, task, evidence and correction dependencies
  -- cannot be removed by rewriting a manifest, even with a recomputed checksum.
  IF EXISTS(SELECT value FROM (SELECT DISTINCT value FROM jsonb_array_elements(required)) q
   EXCEPT SELECT value FROM jsonb_array_elements(records)) THEN
   RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
  END IF;
  rebuilt:=rebuilt||required;

  SELECT v->'row' INTO projection FROM jsonb_array_elements(records) x(v)
   WHERE v->>'kind'='assertion_projection' AND v->>'id'=r.relation_id::text;
  IF projection IS NOT NULL THEN
   SELECT COALESCE(jsonb_agg(jsonb_build_object('role',p.role,'statement',jsonb_build_object(
    'statement_id',s.statement_id,'bead_id',s.bead_id,'bead_version_id',s.bead_version_id,'text',s.statement_text))
    ORDER BY s.statement_id,s.bead_id,s.bead_version_id),'[]') INTO pins FROM (
    SELECT endpoint role,statement_id FROM memoriesql.bead_relation_statements
     WHERE r.kind='authored' AND tenant_id=c.tenant_id AND relation_id=r.relation_id
    UNION ALL SELECT role,statement_id FROM memoriesql.assessed_relation_statements
     WHERE r.kind='assessed' AND tenant_id=c.tenant_id AND relation_id=r.relation_id) p
    JOIN memoriesql.bead_semantic_statements s ON s.tenant_id=c.tenant_id AND s.statement_id=p.statement_id;
   expected:=projection||jsonb_build_object('kind',r.kind,'relation_id',r.relation_id,
    'relation_type',memoriesql.relation_type_pin_v1(r.relation_type_revision_id),
    'direction','outgoing',
    'source_bead_id',r.source_bead_id,'source_bead_version_id',r.source_bead_version_id,
    'target_bead_id',r.target_bead_id,'target_bead_version_id',r.target_bead_version_id,
    'source_statements',COALESCE((SELECT jsonb_agg(v->'statement' ORDER BY v#>>'{statement,statement_id}') FROM jsonb_array_elements(pins) x(v) WHERE v->>'role'='source'),'[]'::jsonb),
    'target_statements',COALESCE((SELECT jsonb_agg(v->'statement' ORDER BY v#>>'{statement,statement_id}') FROM jsonb_array_elements(pins) x(v) WHERE v->>'role'='target'),'[]'::jsonb),
    'basis_statements',COALESCE((SELECT jsonb_agg(v->'statement' ORDER BY v#>>'{statement,statement_id}') FROM jsonb_array_elements(pins) x(v) WHERE v->>'role'='basis'),'[]'::jsonb),
    'basis',raw->'basis','rationale',raw->'rationale_text','qualification',raw->'qualification_text','author_confidence',raw->'author_confidence',
    'authoring_bead_id',CASE WHEN r.kind='authored' THEN raw->'authoring_bead_id' ELSE 'null'::jsonb END,
    'task_id',CASE WHEN r.kind='assessed' THEN raw->'task_id' ELSE 'null'::jsonb END,
    'author_run_ref',CASE WHEN r.kind='authored' THEN raw->'semantic_run_id' ELSE raw->'author_run_id' END,
    'specialist_run_ref',CASE WHEN r.kind='assessed' THEN raw->'specialist_run_id' ELSE 'null'::jsonb END,
    'judgment',CASE WHEN r.kind='assessed' THEN raw->'judgment' ELSE 'null'::jsonb END,
    'recorded_at',memoriesql.relation_packet_time(r.recorded_at));
   IF expected IS DISTINCT FROM projection THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
   rebuilt:=rebuilt||jsonb_build_array(jsonb_build_object('kind','assertion_projection','id',r.relation_id::text,'row',projection));
  END IF;
 END LOOP;

 -- Roots retained in the original dependency bag remain protected. In owner
 -- mode raw capability is global, and the source/event gates use the SAME
 -- current scope/read/resource predicates; check every captured source, never
 -- a newly discovered root. Observation-only results keep query/source.read.
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(records) x(v) WHERE v->>'kind'='assertion_acceptance') THEN
  FOR src IN SELECT ev.* FROM memoriesql.source_events ev WHERE ev.tenant_id=c.tenant_id
   AND ev.event_id IN (SELECT (v->>'id')::uuid FROM jsonb_array_elements(records) x(v) WHERE v->>'kind'='source_event') LOOP
   PERFORM memoriesql.relation_read_source_authorized_v1(mode,src.access_scope_id,src.event_id,src.source_object_id);
  END LOOP;
 END IF;

 -- Original task-only pair coverage and immutable supersession records.
 FOR d IN SELECT v FROM jsonb_array_elements(records) x(v)
  WHERE v->>'kind' IN ('pair_task_population','pair_disposition','bead_supersession') LOOP
  row_data:=d->'row'; expected:=NULL;
  IF d->>'kind'='pair_task_population' THEN
   IF mode<>'owner' THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
   SELECT jsonb_build_object('task_id',a.task_id,'beads',a.beads) INTO expected
    FROM memoriesql.relation_assessments a WHERE a.tenant_id=c.tenant_id AND a.task_id=(d->>'id')::uuid;
   required:=memoriesql.relation_task_population_records_v1(c.tenant_id,(d->>'id')::uuid,known,mode);
   IF EXISTS(SELECT value FROM (SELECT DISTINCT value FROM jsonb_array_elements(required)) q
    EXCEPT SELECT value FROM jsonb_array_elements(records)) THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
  ELSIF d->>'kind'='pair_disposition' THEN
   IF mode<>'owner' THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
   SELECT jsonb_build_object('task_id',p.task_id,'first_bead_id',p.first_bead_id,
    'first_bead_version_id',p.first_bead_version_id,'second_bead_id',p.second_bead_id,
    'second_bead_version_id',p.second_bead_version_id,'disposition',p.disposition,
    'abstention',p.abstention,'reason',p.reason_text) INTO expected FROM memoriesql.relation_pair_dispositions p
    WHERE p.tenant_id=c.tenant_id AND p.task_id=(row_data->>'task_id')::uuid
     AND p.first_bead_id=(row_data->>'first_bead_id')::uuid AND p.second_bead_id=(row_data->>'second_bead_id')::uuid
     AND p.recorded_at<=known;
   IF d->>'id' IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(
    row_data->'task_id',row_data->'first_bead_id',row_data->'second_bead_id')) THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
  ELSE
   SELECT jsonb_build_object('predecessor_bead_id',s.superseded_bead_id,
    'predecessor_version_id',s.superseded_bead_version_id,'successor_bead_id',s.bead_id,
    'successor_version_id',s.bead_version_id,'reason',s.correction_reason,
    'recorded_at',memoriesql.relation_packet_time(v.authored_at)) INTO expected
    FROM memoriesql.bead_supersessions s JOIN memoriesql.bead_versions v
     ON v.tenant_id=s.tenant_id AND v.bead_version_id=s.bead_version_id
    WHERE s.tenant_id=c.tenant_id AND s.superseded_bead_version_id=(row_data->>'predecessor_version_id')::uuid
     AND s.bead_version_id=(row_data->>'successor_version_id')::uuid AND v.authored_at<=known;
   IF d->>'id' IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(
    row_data->'predecessor_version_id',row_data->'successor_version_id')) THEN
    RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
   END IF;
  END IF;
  IF expected IS NULL OR expected IS DISTINCT FROM row_data THEN
   RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
  END IF;
  rebuilt:=rebuilt||jsonb_build_array(d);
 END LOOP;
 SELECT COALESCE(jsonb_agg(v),'[]') INTO rebuilt FROM (SELECT DISTINCT v FROM jsonb_array_elements(rebuilt) x(v)) q;
 IF EXISTS(SELECT value FROM jsonb_array_elements(records)
  EXCEPT SELECT value FROM jsonb_array_elements(rebuilt)) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM memoriesql.result_preparation_authority_v1();
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_saved_records_authorized_v1(jsonb,timestamptz) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.check_query_result_closure_v2(result_ref uuid,digest text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 r memoriesql.query_result_creations%ROWTYPE;
 ident memoriesql.query_result_identities%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE;
 body jsonb; graph jsonb; deps jsonb; saved_records jsonb; saved_manifest jsonb;
 rebuilt jsonb;
 manifest_text text; records_text text; artifact_hash text;
BEGIN
 IF result_ref IS NULL OR digest IS NULL OR digest !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO r FROM memoriesql.query_result_creations
  WHERE tenant_id=c.tenant_id AND result_id=result_ref FOR SHARE;
 SELECT * INTO ident FROM memoriesql.query_result_identities
  WHERE tenant_id=c.tenant_id AND result_id=result_ref FOR SHARE;
 IF r.result_id IS NULL OR ident.result_id IS NULL OR r.content_digest<>digest
  OR clock_timestamp()>=r.expires_at OR ident.workspace_id<>c.workspace_id
  OR ident.principal_id<>c.principal_id OR ident.principal_kind<>c.principal_kind
  OR ident.user_id IS DISTINCT FROM c.user_id
  OR ident.on_behalf_of_user_id IS DISTINCT FROM c.on_behalf_of_user_id
  OR ident.pairing_grant_id IS DISTINCT FROM c.pairing_grant_id THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND operation_ref=r.operation_ref;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts
  WHERE tenant_id=c.tenant_id AND artifact_ref=result_ref FOR SHARE;
 IF o.operation_ref IS NULL OR a.artifact_ref IS NULL OR o.state<>'sealed'
  OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id)
  OR encode(sha256(a.content_bytes),'hex')<>digest THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 artifact_hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
  int8send(octet_length(a.content_bytes)::bigint)||a.content_bytes||
  int8send(octet_length(a.witness_bytes)::bigint)||a.witness_bytes||
  int8send(octet_length(a.dependency_bytes)::bigint)||a.dependency_bytes),'hex');
 IF artifact_hash<>a.artifact_sha256 THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb;
 graph:=convert_from(a.witness_bytes,'UTF8')::jsonb;
 deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 IF body->>'result_id' IS DISTINCT FROM result_ref::text
  OR body->'population_revision' IS DISTINCT FROM '2'::jsonb
  OR body#>>'{frame,view}' NOT IN ('resolved','historical')
  OR body->'witness_sha256' IS NOT NULL OR body#>'{frame,snapshot_digest}' IS NOT NULL
  OR body#>'{frame,projection_manifest_sha256}' IS NOT NULL
  OR deps->>'witness_sha256' IS DISTINCT FROM encode(sha256(a.witness_bytes),'hex')
  OR graph->>'frame_ref' IS DISTINCT FROM body#>>'{frame,frame_ref}'
  OR body#>>'{query,run_ref}' IS DISTINCT FROM o.run_ref::text
  OR body#>>'{query,step_key}' IS DISTINCT FROM o.step_key::text
  OR body->>'query_fingerprint' IS DISTINCT FROM o.request_fingerprint
  OR jsonb_typeof(deps->'manifest_base64') IS DISTINCT FROM 'string'
  OR jsonb_typeof(deps->'records_base64') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 manifest_text:=convert_from(decode(deps->>'manifest_base64','base64'),'UTF8');
 records_text:=convert_from(decode(deps->>'records_base64','base64'),'UTF8');
 saved_manifest:=manifest_text::jsonb;
 saved_records:=records_text::jsonb;
 IF jsonb_typeof(saved_manifest)<>'array' OR jsonb_typeof(saved_records)<>'array'
  OR manifest_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(saved_manifest)
  OR records_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(saved_records)
  OR encode(sha256(convert_to(manifest_text,'UTF8')),'hex') IS DISTINCT FROM deps#>>'{protected_frame,projection_manifest_sha256}'
  OR deps#>>'{protected_frame,snapshot_digest}' IS DISTINCT FROM deps#>>'{protected_frame,projection_manifest_sha256}' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
  'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
  ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]'::jsonb)
 INTO rebuilt FROM jsonb_array_elements(saved_records) x(v);
 IF rebuilt IS DISTINCT FROM saved_manifest THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 -- Reauthorize the retained original dependency frame, not a new population.
 PERFORM memoriesql.query_saved_records_authorized_v1(saved_records,
  (body#>>'{frame,known_at}')::timestamptz);
 PERFORM memoriesql.result_preparation_authority_v1();
 IF clock_timestamp()>=r.expires_at THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire
 OR invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
END $$;
