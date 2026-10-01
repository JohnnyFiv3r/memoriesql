-- PR-05 agent relation reads (owner decision AM-5, 2026-09-28).
-- docs/relation-assessment.md: reads use current memory.query and source-read
-- authority over their entire disclosed dependency closure. PR-03's read kernel
-- required raw revisiting authority instead. This migration adds ONE read mode
-- to that single kernel: 'owner' keeps every existing check exactly, and
-- 'agent' substitutes source.read on the same event wherever the owner's read
-- requires raw revisiting authority over a source object. Nothing else is
-- relaxed: memory.query closure checks, whole-family withholding, the lifecycle
-- projection and root computation are shared, and governed writes stay owner
-- mode. Every earlier function becomes a wrapper over its mode-threaded body,
-- so no second lifecycle interpreter exists. Agents never receive lifecycle
-- history or pair coverage, root identities, raw bytes, lineage or revisiting.
-- Forward-only; migrations 0001-0038 keep their bytes.

-- The single substitution point. 'owner' is exactly the previous gate.
CREATE FUNCTION memoriesql.relation_read_source_authorized_v1(read_mode text,scope uuid,event uuid,source uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 IF read_mode='owner' THEN
  PERFORM memoriesql.revisiting_source_authorize(source);
 ELSIF read_mode='agent' THEN
  -- The (scope, event, source) triple must name one event of the caller's own
  -- tenant and workspace, so a mis-threaded gate withholds instead of authorizing.
  IF scope IS NULL OR event IS NULL OR source IS NULL
     OR NOT EXISTS(SELECT 1 FROM memoriesql.current_authorization_context() c
        JOIN memoriesql.source_events ev ON ev.tenant_id=c.tenant_id AND ev.workspace_id=c.workspace_id
        WHERE ev.event_id=event AND ev.access_scope_id=scope AND ev.source_object_id=source)
     OR NOT memoriesql.current_context_event_authorized(scope,event,'source.read','read') THEN
   RAISE EXCEPTION 'relation_source_unavailable' USING ERRCODE='42501';
  END IF;
 ELSE
  RAISE EXCEPTION 'invalid_relation_read_mode' USING ERRCODE='22023';
 END IF;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_read_source_authorized_v1(text,uuid,uuid,uuid) FROM PUBLIC;

-- Mode-threaded bodies: the installed bodies with only the read mode added.

-- From 0030_assessed_relation_lifecycle.sql: relation_unit_records_v1.
CREATE FUNCTION memoriesql.relation_unit_records_v2(t uuid,id uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE u record; result jsonb;
BEGIN
 SELECT su.*,ev.source_object_id,ev.recorded_at,ev.content_hash event_content_hash INTO u FROM memoriesql.source_units su JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id WHERE su.tenant_id=t AND su.source_unit_id=id AND su.created_at<=known AND ev.recorded_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'memory.query','read') THEN RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,u.access_scope_id,u.event_id,u.source_object_id);
 result:=jsonb_build_array(
 jsonb_build_object('kind','source_unit','id',id::text,'row',jsonb_build_object('source_unit_id',id,'event_id',u.event_id,'content_sha256',u.content_hash,'created_at',memoriesql.relation_packet_time(u.created_at))),
 jsonb_build_object('kind','source_event','id',u.event_id::text,'row',jsonb_build_object('event_id',u.event_id,'source_object_id',u.source_object_id,'content_sha256',u.event_content_hash,'recorded_at',memoriesql.relation_packet_time(u.recorded_at))),
 jsonb_build_object('kind','source_object','id',u.source_object_id::text,'row',jsonb_build_object('source_object_id',u.source_object_id)));
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_unit_records_v2(uuid,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: relation_bead_records_v1.
CREATE FUNCTION memoriesql.relation_bead_records_v2(t uuid,id uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE b memoriesql.beads%ROWTYPE; v record; st record; e record; result jsonb:='[]'; receipt uuid;
BEGIN
 PERFORM memoriesql.derivation_bead_authorize_v2(t,id,known,read_mode);
 SELECT * INTO b FROM memoriesql.beads WHERE tenant_id=t AND bead_id=id;
 result:=jsonb_build_array(jsonb_build_object('kind','bead','id',id::text,'row',jsonb_build_object('bead_id',id,'created_at',memoriesql.relation_packet_time(b.created_at),'event_id',b.event_id,'source_unit_id',b.source_unit_id)));
 result:=result||memoriesql.relation_unit_records_v2(t,b.source_unit_id,known,read_mode);
 FOR v IN SELECT bv.* FROM memoriesql.accepted_bead_semantics a JOIN memoriesql.bead_versions bv ON bv.tenant_id=a.tenant_id AND bv.bead_version_id=a.bead_version_id WHERE a.tenant_id=t AND a.bead_id=id AND bv.authored_at<=known LOOP
  SELECT idempotency_receipt_id INTO receipt FROM memoriesql.semantic_task_receipts WHERE tenant_id=t AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  result:=result||jsonb_build_array(jsonb_build_object('kind','accepted_bead','id',v.bead_version_id::text,'row',jsonb_build_object('bead_id',id,'bead_version_id',v.bead_version_id,'authored_at',memoriesql.relation_packet_time(v.authored_at),'semantic_task_receipt_id',v.semantic_task_receipt_id,'idempotency_receipt_id',receipt,'authored_by_principal_id',v.authored_by_principal_id)));
  FOR st IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=t AND bead_version_id=v.bead_version_id ORDER BY statement_id LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(t,st.workspace_id,st.access_scope_id,st.statement_id) THEN RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501'; END IF;
   result:=result||jsonb_build_array(jsonb_build_object('kind','statement','id',st.statement_id::text,'row',jsonb_build_object('statement_id',st.statement_id,'bead_id',st.bead_id,'bead_version_id',st.bead_version_id,'text',st.statement_text)));
   FOR e IN SELECT * FROM memoriesql.bead_semantic_statement_evidence WHERE tenant_id=t AND statement_id=st.statement_id ORDER BY evidence_source_unit_id LOOP
    result:=result||memoriesql.relation_unit_records_v2(t,e.evidence_source_unit_id,known,read_mode)||jsonb_build_array(jsonb_build_object('kind','statement_evidence','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(st.statement_id,e.evidence_source_unit_id)),'row',jsonb_build_object('statement_id',st.statement_id,'source_unit_id',e.evidence_source_unit_id,'content_hash',e.evidence_content_hash)));
   END LOOP;
  END LOOP;
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_bead_records_v2(uuid,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: derivation_bead_authorize_v1.
CREATE FUNCTION memoriesql.derivation_bead_authorize_v2(t uuid,id uuid,known timestamptz,read_mode text) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE b memoriesql.beads%ROWTYPE; v record; source uuid;
BEGIN
 SELECT * INTO b FROM memoriesql.beads WHERE tenant_id=t AND bead_id=id AND created_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(b.access_scope_id,b.event_id,'memory.query','read') THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 FOR v IN SELECT a.* FROM memoriesql.accepted_bead_semantics a WHERE a.tenant_id=t AND a.bead_id=id LOOP
 IF NOT memoriesql.current_context_bead_version_authorized(t,v.workspace_id,v.access_scope_id,v.bead_version_id) THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 END LOOP;
 SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id=t AND event_id=b.event_id;
 IF source IS NULL THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,b.access_scope_id,b.event_id,source);
END $$;
REVOKE ALL ON FUNCTION memoriesql.derivation_bead_authorize_v2(uuid,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: qualified_unit_roots_v1.
CREATE FUNCTION memoriesql.qualified_unit_roots_v2(t uuid,unit uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE b uuid; source uuid; u record; q jsonb; roots jsonb:='[]'; gaps jsonb:='[]'; deps jsonb:='[]'; status text:='qualified'; observing boolean:=false;
BEGIN
 SELECT su.*,ev.source_object_id INTO u FROM memoriesql.source_units su JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id WHERE su.tenant_id=t AND su.source_unit_id=unit AND su.created_at<=known AND ev.recorded_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'memory.query','read') THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,u.access_scope_id,u.event_id,u.source_object_id);
 deps:=memoriesql.relation_unit_records_v2(t,unit,known,read_mode);
 FOR b IN SELECT bead_id FROM memoriesql.beads WHERE tenant_id=t AND source_unit_id=unit AND created_at<=known ORDER BY bead_id LOOP
  observing:=true; q:=memoriesql.qualified_bead_roots_v2(t,b,known,read_mode);
  IF q->>'roots_status'='unsupported' THEN status:='unsupported'; ELSIF q->>'roots_status'='indeterminate' AND status='qualified' THEN status:='indeterminate'; END IF;
  roots:=roots||COALESCE(NULLIF(q->'derivation_root_ids','null'),'[]'); gaps:=gaps||(q->'roots_gap_relation_ids'); deps:=deps||(q->'dependencies');
 END LOOP;
 IF NOT observing THEN roots:=jsonb_build_array(u.source_object_id); END IF;
 SELECT COALESCE(jsonb_agg(v ORDER BY v),'[]') INTO roots FROM (SELECT DISTINCT value v FROM jsonb_array_elements(roots)) a;
 SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'relation_id',v->>'reason'),'[]') INTO gaps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(gaps)) a;
 IF status='qualified' AND jsonb_array_length(roots)=0 THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('roots_status',status,'derivation_root_ids',CASE WHEN status='qualified' THEN roots ELSE 'null'::jsonb END,'roots_gap_relation_ids',gaps,'dependencies',deps);
END $$;
REVOKE ALL ON FUNCTION memoriesql.qualified_unit_roots_v2(uuid,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: qualified_bead_roots_v1.
CREATE FUNCTION memoriesql.qualified_bead_roots_v2(t uuid,bead uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE root_events jsonb; root_event jsonb; lineage uuid[]:='{}'; pending uuid[]:=ARRAY[bead]; current_bead uuid; next_bead uuid; roots uuid[]; a record; q jsonb; gap text; gaps jsonb:='[]'; deps jsonb:='[]'; chain uuid[]; replaced record; replacement_id uuid; covered boolean; status text:='qualified'; data jsonb;
BEGIN
 WHILE cardinality(pending)>0 LOOP
  SELECT x INTO current_bead FROM unnest(pending) x ORDER BY x LIMIT 1;
  pending:=array_remove(pending,current_bead);
  IF current_bead=ANY(lineage) THEN CONTINUE; END IF;
  IF cardinality(lineage)>=128 THEN RAISE EXCEPTION 'root_budget' USING ERRCODE='54000'; END IF;
  lineage:=array_append(lineage,current_bead);
  PERFORM memoriesql.derivation_bead_authorize_v2(t,current_bead,known,read_mode);
  deps:=deps||memoriesql.relation_bead_records_v2(t,current_bead,known,read_mode);
  FOR next_bead IN SELECT * FROM memoriesql.derived_from_targets(t,current_bead,known) LOOP
   IF NOT next_bead=ANY(lineage) THEN pending:=array_append(pending,next_bead); END IF;
  END LOOP;
  FOR a IN SELECT ar.* FROM memoriesql.relation_assertions_v1(t,known) ar JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=ar.relation_type_revision_id WHERE ar.source_bead_id=current_bead AND ar.acceptance='accepted' AND tr.relation_type_id='30000000-0000-4000-8000-000000000009'::uuid ORDER BY ar.kind,ar.relation_id LOOP
   IF NOT memoriesql.relation_closure_authorized_v2(t,a.kind,a.relation_id,known,false,read_mode) THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
   q:=memoriesql.relation_projection_v1(t,a.kind,a.relation_id,known);
   deps:=deps||memoriesql.relation_assertion_records_v2(t,a.kind,a.relation_id,known,read_mode);
   gap:=NULL;
   IF (q->>'support_eligible')::boolean THEN
    IF a.source_bead_id=a.target_bead_id THEN gap:='statement_roots_unsupported'; END IF;
   ELSIF q->>'state'='disputed' THEN gap:='disputed';
   ELSIF q->>'state'='reassessment_pending' THEN gap:='corrected';
   ELSIF q->>'state'='retracted' THEN gap:='withdrawn';
   ELSIF q->>'state'='superseded' THEN
    chain:=ARRAY[a.relation_id]; covered:=false;
    LOOP
     IF jsonb_array_length(q->'superseded_by')<>1 THEN EXIT; END IF;
     replacement_id:=(q#>>'{superseded_by,0}')::uuid;
     IF replacement_id=ANY(chain) THEN EXIT; END IF;
     chain:=array_append(chain,replacement_id);
     SELECT ar.*,tr.relation_type_id INTO replaced FROM memoriesql.relation_assertions_v1(t,known) ar JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=ar.relation_type_revision_id WHERE ar.relation_id=replacement_id;
     IF NOT FOUND OR NOT memoriesql.relation_closure_authorized_v2(t,replaced.kind,replacement_id,known,false,read_mode) THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
     q:=memoriesql.relation_projection_v1(t,replaced.kind,replacement_id,known);
     deps:=deps||memoriesql.relation_assertion_records_v2(t,replaced.kind,replacement_id,known,read_mode);
     IF q->>'state'='superseded' THEN CONTINUE; END IF;
     covered:=(q->>'support_eligible')::boolean AND replaced.source_bead_id=a.source_bead_id AND replaced.target_bead_id<>a.source_bead_id AND replaced.relation_type_id='30000000-0000-4000-8000-000000000009'::uuid; EXIT;
    END LOOP;
    IF NOT covered THEN gap:='replacement_gap'; END IF;
   END IF;
   IF gap IS NOT NULL THEN
    gaps:=gaps||jsonb_build_array(jsonb_build_object('kind',a.kind,'relation_id',a.relation_id,'reason',gap));
    IF gap='statement_roots_unsupported' THEN status:='unsupported'; ELSIF status='qualified' THEN status:='indeterminate'; END IF;
   END IF;
  END LOOP;
 END LOOP;
 -- Complete SCC computation over the admitted set, with immutable creation-time tie break.
 WITH RECURSIVE edge(source_id,target_id) AS MATERIALIZED (
 SELECT l.id,d.target FROM unnest(lineage) l(id) CROSS JOIN LATERAL memoriesql.derived_from_targets(t,l.id,known) d(target)
 ), reach(from_id,to_id) AS (
 SELECT id,id FROM unnest(lineage) l(id) UNION SELECT r.from_id,e.target_id FROM reach r JOIN edge e ON e.source_id=r.to_id
 ), bottom AS (
 SELECT r.from_id FROM reach r LEFT JOIN reach back ON back.from_id=r.to_id AND back.to_id=r.from_id GROUP BY r.from_id HAVING bool_and(back.from_id IS NOT NULL)
 ), earliest AS (
 SELECT DISTINCT ON (m.from_id) b.event_id FROM bottom m JOIN reach r ON r.from_id=m.from_id JOIN memoriesql.beads b ON b.tenant_id=t AND b.bead_id=r.to_id ORDER BY m.from_id,b.created_at,b.bead_id
 ) SELECT array_agg(DISTINCT e.source_object_id ORDER BY e.source_object_id),COALESCE(jsonb_agg(DISTINCT jsonb_build_object('scope',e.access_scope_id,'event',e.event_id,'source',e.source_object_id)),'[]') INTO roots,root_events FROM earliest x JOIN memoriesql.source_events e ON e.tenant_id=t AND e.event_id=x.event_id;
 IF cardinality(roots) IS NULL OR cardinality(roots)=0 THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 IF read_mode='owner' THEN
  FOREACH next_bead IN ARRAY roots LOOP PERFORM memoriesql.revisiting_source_authorize(next_bead); END LOOP;
 ELSE
  -- AM-5: source.read on every root's primary event; root identities stay undisclosed.
  FOR root_event IN SELECT value FROM jsonb_array_elements(root_events) LOOP
   PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,(root_event->>'scope')::uuid,(root_event->>'event')::uuid,(root_event->>'source')::uuid);
  END LOOP;
 END IF;
 SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'relation_id',v->>'reason'),'[]') INTO gaps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(gaps)) a;
 RETURN jsonb_build_object('roots_status',status,'derivation_root_ids',CASE WHEN status='qualified' THEN to_jsonb(roots) ELSE 'null'::jsonb END,'roots_gap_relation_ids',gaps,'dependencies',deps);
END $$;
REVOKE ALL ON FUNCTION memoriesql.qualified_bead_roots_v2(uuid,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: relation_closure_authorized_v1.
CREATE FUNCTION memoriesql.relation_closure_authorized_v2(t uuid,k text,id uuid,known timestamptz,govern boolean,read_mode text) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r record; pin record; e record; dependency jsonb; src uuid; src_scope uuid; ids uuid[]; a uuid; q jsonb;
BEGIN
 -- AM-5: the agent read mode never authorizes a governed write.
 IF govern AND read_mode IS DISTINCT FROM 'owner' THEN RETURN false; END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF c.tenant_id IS DISTINCT FROM t OR NOT memoriesql.relation_current_authority_v1(t) THEN RETURN false; END IF;
 SELECT * INTO r FROM memoriesql.relation_assertions_v1(t,known) WHERE kind=k AND relation_id=id;
 IF NOT FOUND OR r.workspace_id<>c.workspace_id OR (govern AND c.principal_kind<>'human') THEN RETURN false; END IF;
 -- Replacement chains retain every intermediate assertion, with cycle protection.
 WITH RECURSIVE closure(kind,id) AS (
 SELECT k,id UNION SELECT x.kind,x.replacement_relation_id FROM closure p JOIN (SELECT retired_kind,'assessed'::text kind,retired_relation_id, replacement_relation_id FROM memoriesql.relation_retirements WHERE tenant_id=t AND recorded_at<=known UNION SELECT 'authored','authored',relation_id,replacement_relation_id FROM memoriesql.bead_relation_events WHERE tenant_id=t AND action='supersede' AND recorded_at<=known) x ON x.retired_kind=p.kind AND x.retired_relation_id=p.id
 ) SELECT array_agg(DISTINCT closure.id ORDER BY closure.id) INTO ids FROM closure;
 FOREACH a IN ARRAY ids LOOP
  SELECT * INTO r FROM memoriesql.relation_assertions_v1(t,known) WHERE relation_id=a;
  IF NOT FOUND OR r.workspace_id<>c.workspace_id THEN RETURN false; END IF;
  FOR pin IN SELECT s.* FROM memoriesql.bead_semantic_statements s WHERE s.tenant_id=t AND s.statement_id IN (
   SELECT statement_id FROM memoriesql.bead_relation_statements WHERE r.kind='authored' AND tenant_id=t AND relation_id=a
   UNION SELECT statement_id FROM memoriesql.assessed_relation_statements WHERE r.kind='assessed' AND tenant_id=t AND relation_id=a) LOOP
   IF NOT memoriesql.current_context_bead_version_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) OR NOT memoriesql.current_context_semantic_statement_authorized(t,pin.workspace_id,pin.access_scope_id,pin.statement_id) THEN RETURN false; END IF;
   IF govern THEN
    IF a=id AND pin.bead_version_id IN (r.source_bead_version_id,r.target_bead_version_id) THEN
     IF NOT memoriesql.lifecycle_bead_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) THEN RETURN false; END IF;
    ELSIF NOT memoriesql.current_context_accepted_bead_maintain_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) THEN RETURN false; END IF;
   END IF;
   FOR e IN SELECT x.*,ev.source_object_id FROM memoriesql.bead_semantic_statement_evidence x JOIN memoriesql.source_events ev ON ev.tenant_id=x.tenant_id AND ev.event_id=x.evidence_event_id WHERE x.tenant_id=t AND x.statement_id=pin.statement_id LOOP
    IF NOT memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.query','read') OR (govern AND NOT memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.maintain','read')) THEN RETURN false; END IF;
    PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,e.access_scope_id,e.evidence_event_id,e.source_object_id);
   END LOOP;
  END LOOP;
  q:=memoriesql.relation_projection_v1(t,r.kind,a,known);
  FOR dependency IN SELECT value FROM jsonb_array_elements(q->'corrections') LOOP
   SELECT v.* INTO pin FROM memoriesql.bead_versions v WHERE v.tenant_id=t AND v.bead_version_id=(dependency->>'successor_bead_version_id')::uuid;
   IF NOT FOUND OR NOT memoriesql.current_context_bead_version_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) OR (govern AND NOT memoriesql.current_context_accepted_bead_maintain_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id)) THEN RETURN false; END IF;
   SELECT source_object_id,access_scope_id INTO src,src_scope FROM memoriesql.source_events WHERE tenant_id=t AND event_id=pin.event_id;
   PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,src_scope,pin.event_id,src);
   FOR e IN SELECT x.*,ev.source_object_id FROM memoriesql.bead_semantic_statement_evidence x JOIN memoriesql.bead_semantic_statements st ON st.tenant_id=x.tenant_id AND st.statement_id=x.statement_id JOIN memoriesql.source_events ev ON ev.tenant_id=x.tenant_id AND ev.event_id=x.evidence_event_id WHERE st.tenant_id=t AND st.bead_version_id=pin.bead_version_id LOOP
    IF NOT memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.query','read') OR (govern AND NOT memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.maintain','read')) THEN RETURN false; END IF;
    PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,e.access_scope_id,e.evidence_event_id,e.source_object_id);
   END LOOP;
  END LOOP;
  FOR dependency IN SELECT value FROM memoriesql.assessed_relation_events g CROSS JOIN LATERAL jsonb_array_elements(g.evidence) WHERE r.kind='assessed' AND g.tenant_id=t AND g.relation_id=a AND g.recorded_at<=known UNION SELECT jsonb_build_object('statement_id',l.statement_id,'source_unit_id',l.evidence_source_unit_id,'content_hash',x.evidence_content_hash) FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind='relation_event' AND l.recorded_at<=known AND l.owner_id IN (SELECT relation_event_id FROM memoriesql.bead_relation_events WHERE r.kind='authored' AND tenant_id=t AND relation_id=a AND recorded_at<=known UNION SELECT retirement_id FROM memoriesql.relation_retirements WHERE tenant_id=t AND retired_kind=r.kind AND retired_relation_id=a AND recorded_at<=known) LOOP
   IF NOT memoriesql.assessed_lifecycle_evidence_authorized_v1(t,c.workspace_id,dependency) AND govern THEN RETURN false; END IF;
   SELECT s.*,x.evidence_event_id,x.access_scope_id evidence_scope,ev.source_object_id INTO e FROM memoriesql.bead_semantic_statements s JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=s.tenant_id AND x.statement_id=s.statement_id JOIN memoriesql.source_events ev ON ev.tenant_id=x.tenant_id AND ev.event_id=x.evidence_event_id WHERE s.tenant_id=t AND s.statement_id=(dependency->>'statement_id')::uuid AND x.evidence_source_unit_id=(dependency->>'source_unit_id')::uuid AND x.evidence_content_hash=dependency->>'content_hash';
   IF NOT FOUND OR NOT memoriesql.current_context_bead_version_authorized(t,e.workspace_id,e.access_scope_id,e.bead_version_id) OR NOT memoriesql.current_context_event_authorized(e.evidence_scope,e.evidence_event_id,'memory.query','read') THEN RETURN false; END IF;
   PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,e.evidence_scope,e.evidence_event_id,e.source_object_id);
  END LOOP;
 END LOOP;
 RETURN true;
EXCEPTION WHEN insufficient_privilege THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_closure_authorized_v2(uuid,text,uuid,timestamptz,boolean,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: relation_evidence_view_v3.
CREATE FUNCTION memoriesql.relation_evidence_view_v4(t uuid,owner_kind_value text,owner uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE link record; q jsonb; source uuid; items jsonb:='[]'; sources jsonb:='[]'; deps jsonb:='[]';
BEGIN
 FOR link IN SELECT l.statement_id,l.evidence_source_unit_id,x.evidence_content_hash,x.access_scope_id,x.evidence_event_id FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind=owner_kind_value AND l.owner_id=owner AND l.recorded_at<=known ORDER BY l.statement_id,l.evidence_source_unit_id LOOP
 IF NOT memoriesql.current_context_event_authorized(link.access_scope_id,link.evidence_event_id,'memory.query','read') THEN RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501'; END IF;
 SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id=t AND event_id=link.evidence_event_id; PERFORM memoriesql.relation_read_source_authorized_v1(read_mode,link.access_scope_id,link.evidence_event_id,source);
 sources:=sources||jsonb_build_array(jsonb_build_object('source',source));
 q:=memoriesql.qualified_unit_roots_v2(t,link.evidence_source_unit_id,known,read_mode); deps:=deps||(q->'dependencies');
 items:=items||jsonb_build_array(jsonb_build_object('statement_id',link.statement_id,'source_unit_id',link.evidence_source_unit_id,'content_sha256',link.evidence_content_hash)|| (q-ARRAY['dependencies']));
 END LOOP;
 RETURN jsonb_build_object('items',items,'sources',sources,'dependencies',deps);
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_evidence_view_v4(uuid,text,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: relation_events_view_v3.
CREATE FUNCTION memoriesql.relation_events_view_v4(t uuid,k text,id uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE e record; item jsonb; result jsonb:='[]'; pairs jsonb; link record; src uuid;
BEGIN
 FOR e IN SELECT * FROM (
 SELECT g.relation_event_id event_id,g.relation_id target_id,g.action,g.replacement_relation_id related_id,g.reason,g.origin,NULL::uuid authoring_bead_id,g.effective_at,g.recorded_at,NULL::integer event_number,NULL::uuid previous_event_id,g.recorded_by_principal_id,p.user_id recorded_by_user_id,g.idempotency_receipt_id FROM memoriesql.bead_relation_events g JOIN memoriesql.principals p ON p.tenant_id=g.tenant_id AND p.principal_id=g.recorded_by_principal_id WHERE k='authored' AND g.tenant_id=t AND g.relation_id=id AND g.recorded_at<=known
 UNION ALL SELECT g.relation_event_id,g.relation_id,g.action,NULL,g.reason,'governed',NULL,g.effective_at,g.recorded_at,g.event_number,g.previous_event_id,g.recorded_by_principal_id,g.recorded_by_user_id,g.idempotency_receipt_id FROM memoriesql.assessed_relation_events g WHERE k='assessed' AND g.tenant_id=t AND g.relation_id=id AND g.recorded_at<=known
 UNION ALL SELECT x.retirement_id,x.retired_relation_id,'retire',x.replacement_relation_id,x.reason_text,'authored',NULL,NULL,x.recorded_at,NULL,NULL,x.recorded_by_principal_id,p.user_id,x.idempotency_receipt_id FROM memoriesql.relation_retirements x JOIN memoriesql.principals p ON p.tenant_id=x.tenant_id AND p.principal_id=x.recorded_by_principal_id WHERE x.tenant_id=t AND x.retired_kind=k AND x.retired_relation_id=id AND x.recorded_at<=known) hist ORDER BY recorded_at,event_number NULLS LAST,event_id LOOP
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
REVOKE ALL ON FUNCTION memoriesql.relation_events_view_v4(uuid,text,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: relation_assertion_records_v1.
CREATE FUNCTION memoriesql.relation_assertion_records_v2(t uuid,k text,id uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE r record; q jsonb; item jsonb; ev jsonb; result jsonb:='[]'; pin record; link record; ids uuid[]; a uuid; raw jsonb;
BEGIN
 IF NOT memoriesql.relation_closure_authorized_v2(t,k,id,known,false,read_mode) THEN RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501'; END IF;
 WITH RECURSIVE closure(kind,id) AS (
 SELECT k,id UNION SELECT x.kind,x.replacement_relation_id FROM closure p JOIN (
 SELECT retired_kind,'assessed'::text kind,retired_relation_id,replacement_relation_id FROM memoriesql.relation_retirements WHERE tenant_id=t AND recorded_at<=known
 UNION SELECT 'authored','authored',relation_id,replacement_relation_id FROM memoriesql.bead_relation_events WHERE tenant_id=t AND action='supersede' AND recorded_at<=known) x ON x.retired_kind=p.kind AND x.retired_relation_id=p.id
 ) SELECT array_agg(DISTINCT closure.id ORDER BY closure.id) INTO ids FROM closure;
 FOREACH a IN ARRAY ids LOOP
  SELECT * INTO r FROM memoriesql.relation_assertions_v1(t,known) WHERE relation_id=a;
  q:=memoriesql.relation_projection_v1(t,r.kind,a,known);
  result:=result||jsonb_build_array(
   jsonb_build_object('kind','assertion_acceptance','id',a::text,'row',jsonb_build_object('kind',r.kind,'relation_id',a,'acceptance',r.acceptance,'acceptance_receipt_id',r.acceptance_receipt_id,'recorded_at',memoriesql.relation_packet_time(r.recorded_at),'source_bead_id',r.source_bead_id,'source_bead_version_id',r.source_bead_version_id,'target_bead_id',r.target_bead_id,'target_bead_version_id',r.target_bead_version_id,'relation_type_revision_id',r.relation_type_revision_id)),
   jsonb_build_object('kind','relation_head','id',a::text,'row',q->'head_manifest'),
   jsonb_build_object('kind','type_definition','id',r.relation_type_revision_id::text,'row',memoriesql.relation_type_definition_v1(r.relation_type_revision_id)));
  SELECT jsonb_build_object('idempotency_receipt_id',idempotency_receipt_id,'operation_kind',operation_kind,'resource_kind',resource_kind,'resource_id',resource_id,'request_hash',request_hash) INTO raw FROM memoriesql.idempotency_receipts WHERE tenant_id=t AND idempotency_receipt_id=r.acceptance_receipt_id;
  result:=result||jsonb_build_array(jsonb_build_object('kind','acceptance_receipt','id',r.acceptance_receipt_id::text,'row',raw));
  result:=result||memoriesql.relation_bead_records_v2(t,r.source_bead_id,known,read_mode)||memoriesql.relation_bead_records_v2(t,r.target_bead_id,known,read_mode);
  FOR pin IN SELECT DISTINCT bead_id FROM memoriesql.assessed_relation_statements WHERE r.kind='assessed' AND tenant_id=t AND relation_id=a AND role='basis' LOOP result:=result||memoriesql.relation_bead_records_v2(t,pin.bead_id,known,read_mode); END LOOP;
  IF r.kind='assessed' THEN
   SELECT judgment INTO raw FROM memoriesql.assessed_relations WHERE tenant_id=t AND relation_id=a;
   result:=result||jsonb_build_array(jsonb_build_object('kind','specialist_judgment','id',a::text,'row',raw));
  END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(q->'corrections') LOOP
   result:=result||jsonb_build_array(jsonb_build_object('kind','correction_pin_'||(item->>'role'),'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(a,item->'pinned_bead_id',item->'successor_bead_id',item->'successor_bead_version_id')),'row',item));
   result:=result||memoriesql.relation_bead_records_v2(t,(item->>'successor_bead_id')::uuid,known,read_mode);
  END LOOP;
  FOR ev IN SELECT value FROM jsonb_array_elements(memoriesql.relation_events_view_v4(t,r.kind,a,known,read_mode)) LOOP
   result:=result||jsonb_build_array(jsonb_build_object('kind','relation_event','id',ev->>'event_id','row',ev));
   FOR item IN SELECT value FROM jsonb_array_elements(ev->'evidence') LOOP
    result:=result||memoriesql.relation_unit_records_v2(t,(item->>'source_unit_id')::uuid,known,read_mode)||jsonb_build_array(jsonb_build_object('kind','lifecycle_evidence','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(ev->'event_id',item->'statement_id',item->'source_unit_id')),'row',item));
    SELECT bead_id INTO pin FROM memoriesql.bead_semantic_statements WHERE tenant_id=t AND statement_id=(item->>'statement_id')::uuid;
    result:=result||memoriesql.relation_bead_records_v2(t,pin.bead_id,known,read_mode);
   END LOOP;
  END LOOP;
  FOR link IN SELECT l.statement_id,l.evidence_source_unit_id,x.evidence_content_hash FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind=CASE WHEN r.kind='authored' THEN 'relation' ELSE 'assessed_relation' END AND l.owner_id=a AND l.recorded_at<=known LOOP
   result:=result||memoriesql.relation_unit_records_v2(t,link.evidence_source_unit_id,known,read_mode)||jsonb_build_array(jsonb_build_object('kind','assertion_evidence','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(a,link.statement_id,link.evidence_source_unit_id)),'row',jsonb_build_object('statement_id',link.statement_id,'source_unit_id',link.evidence_source_unit_id,'content_hash',link.evidence_content_hash)));
  END LOOP;
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_assertion_records_v2(uuid,text,uuid,timestamptz,text) FROM PUBLIC;

-- From 0030_assessed_relation_lifecycle.sql: relation_assertion_row_v1.
CREATE FUNCTION memoriesql.relation_assertion_row_v2(t uuid,k text,id uuid,relative_bead uuid,known timestamptz,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE r record; raw jsonb; q jsonb; evidence jsonb; roots jsonb; status text; pins jsonb; result jsonb;
BEGIN
 IF known IS NULL OR NOT isfinite(known) OR known>statement_timestamp() OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 IF NOT memoriesql.relation_read_frame_authorized_v1(t) OR NOT memoriesql.relation_closure_authorized_v2(t,k,id,known,false,read_mode) THEN RAISE EXCEPTION 'assertion_unavailable' USING ERRCODE='42501'; END IF;
 SELECT a.*,tr.relation_type_id INTO r FROM memoriesql.relation_assertions_v1(t,known) a JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=a.relation_type_revision_id WHERE a.kind=k AND a.relation_id=id;
 IF k='authored' THEN SELECT to_jsonb(a) INTO raw FROM memoriesql.bead_relations a WHERE tenant_id=t AND relation_id=id;
 ELSE SELECT to_jsonb(a) INTO raw FROM memoriesql.assessed_relations a WHERE tenant_id=t AND relation_id=id; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('role',p.role,'statement',jsonb_build_object('statement_id',s.statement_id,'bead_id',s.bead_id,'bead_version_id',s.bead_version_id,'text',s.statement_text)) ORDER BY s.statement_id,s.bead_id,s.bead_version_id),'[]') INTO pins FROM (
 SELECT endpoint role,statement_id FROM memoriesql.bead_relation_statements WHERE k='authored' AND tenant_id=t AND relation_id=id
 UNION ALL SELECT role,statement_id FROM memoriesql.assessed_relation_statements WHERE k='assessed' AND tenant_id=t AND relation_id=id) p JOIN memoriesql.bead_semantic_statements s ON s.tenant_id=t AND s.statement_id=p.statement_id;
 q:=memoriesql.relation_projection_v1(t,k,id,known);
 evidence:=memoriesql.relation_evidence_view_v4(t,CASE WHEN k='authored' THEN 'relation' ELSE 'assessed_relation' END,id,known,read_mode);
 status:=CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(evidence->'items') e(v) WHERE v->>'roots_status'='unsupported') THEN 'unsupported' WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(evidence->'items') e(v) WHERE v->>'roots_status'='indeterminate') THEN 'indeterminate' ELSE 'qualified' END;
 IF (q->>'support_eligible')::boolean AND r.source_bead_id=r.target_bead_id AND r.relation_type_id='30000000-0000-4000-8000-000000000009'::uuid THEN status:='unsupported'; END IF;
 SELECT COALESCE(jsonb_agg(v ORDER BY v),'[]') INTO roots FROM (SELECT DISTINCT root.value v FROM jsonb_array_elements(evidence->'items') e(v) CROSS JOIN LATERAL jsonb_array_elements(COALESCE(NULLIF(e.v->'derivation_root_ids','null'),'[]')) root) x;
 result:=jsonb_build_object('kind',k,'relation_id',id,'relation_type',memoriesql.relation_type_pin_v1(r.relation_type_revision_id),
 'direction',CASE WHEN r.source_bead_id=relative_bead AND r.target_bead_id=relative_bead THEN 'internal' WHEN relative_bead IS NULL OR r.source_bead_id=relative_bead THEN 'outgoing' WHEN r.target_bead_id=relative_bead THEN 'incoming' ELSE 'basis' END,
 'source_bead_id',r.source_bead_id,'source_bead_version_id',r.source_bead_version_id,'target_bead_id',r.target_bead_id,'target_bead_version_id',r.target_bead_version_id,
 'source_statements',COALESCE((SELECT jsonb_agg(v->'statement' ORDER BY v#>>'{statement,statement_id}') FROM jsonb_array_elements(pins) e(v) WHERE v->>'role'='source'),'[]'),
 'target_statements',COALESCE((SELECT jsonb_agg(v->'statement' ORDER BY v#>>'{statement,statement_id}') FROM jsonb_array_elements(pins) e(v) WHERE v->>'role'='target'),'[]'),
 'basis_statements',COALESCE((SELECT jsonb_agg(v->'statement' ORDER BY v#>>'{statement,statement_id}') FROM jsonb_array_elements(pins) e(v) WHERE v->>'role'='basis'),'[]'),
 'basis',raw->'basis','rationale',raw->'rationale_text','qualification',raw->'qualification_text','author_confidence',raw->'author_confidence','evidence',evidence->'items',
 'independent_root_count',CASE WHEN status='qualified' THEN to_jsonb(jsonb_array_length(roots)) ELSE 'null'::jsonb END,
 'authoring_bead_id',CASE WHEN k='authored' THEN raw->'authoring_bead_id' ELSE 'null'::jsonb END,
 'task_id',CASE WHEN k='assessed' THEN raw->'task_id' ELSE 'null'::jsonb END,
 'author_run_ref',CASE WHEN k='authored' THEN raw->'semantic_run_id' ELSE raw->'author_run_id' END,
 'specialist_run_ref',CASE WHEN k='assessed' THEN raw->'specialist_run_id' ELSE 'null'::jsonb END,
 'judgment',CASE WHEN k='assessed' THEN raw->'judgment' ELSE 'null'::jsonb END,'recorded_at',memoriesql.relation_packet_time(r.recorded_at),
 'events',memoriesql.relation_events_view_v4(t,k,id,known,read_mode),'roots_status',status)||(q-ARRAY['head_manifest','corrections']);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_assertion_row_v2(uuid,text,uuid,uuid,timestamptz,text) FROM PUBLIC;

-- From 0032_relation_sql_population.sql: prepare_relation_sql_population_v1.
CREATE FUNCTION memoriesql.prepare_relation_sql_population_v2(
    requested_known_at timestamptz, byte_budget integer
,read_mode text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    known timestamptz; snapshot_at timestamptz := statement_timestamp();
    r record; task record; pin record; pair record; d jsonb; item jsonb;
    assertion jsonb; family jsonb; family_deps jsonb; task_pairs jsonb;
    assertions jsonb := '[]'; corrections jsonb := '[]'; types jsonb := '[]';
    pairs jsonb := '[]'; deps jsonb := '[]'; manifest jsonb; result jsonb;
    families jsonb := '[]'; needs jsonb; kept jsonb; disclosed uuid[];
BEGIN
    IF byte_budget IS NULL OR byte_budget NOT BETWEEN 8192 AND 67108864 THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    known := COALESCE(requested_known_at, snapshot_at);
    IF NOT isfinite(known) OR known > snapshot_at
       OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id)
       OR NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    -- Discovery withholds an entire unreadable assertion family, including all
    -- counts, corrections, type pins, history and root gaps. Nothing is paginated.
    FOR r IN SELECT * FROM memoriesql.relation_assertions_v1(c.tenant_id,known)
             WHERE kind='assessed' AND acceptance='accepted'
               AND workspace_id=c.workspace_id ORDER BY relation_id LOOP
        BEGIN
            assertion := memoriesql.relation_assertion_row_v2(
                c.tenant_id,'assessed',r.relation_id,NULL,known,read_mode);
            -- AM-5: lifecycle history (events and their evidence) stays owner-only.
            IF read_mode='agent' THEN
                assertion := assertion || jsonb_build_object('events','[]'::jsonb);
            END IF;
            family_deps := memoriesql.relation_assertion_records_v2(
                c.tenant_id,'assessed',r.relation_id,known,read_mode);
            family := memoriesql.relation_evidence_view_v4(
                c.tenant_id,'assessed_relation',r.relation_id,known,read_mode);
            family_deps := family_deps || (family->'dependencies');
            item := memoriesql.relation_projection_v1(c.tenant_id,'assessed',r.relation_id,known);
            -- Retain the complete assertion itself: the narrower acceptance/head
            -- records alone do not bind rationale, qualification or confidence.
            family_deps := family_deps || jsonb_build_array(jsonb_build_object(
                'kind','assertion_projection','id',r.relation_id::text,'row',assertion));
        EXCEPTION WHEN insufficient_privilege THEN CONTINUE;
        END;
        -- The other assessed relations this family's records name: its
        -- replacement chain, and derived_from relations on its root lineage.
        SELECT COALESCE(jsonb_agg(DISTINCT dep#>'{row,relation_id}'),'[]') INTO needs
            FROM jsonb_array_elements(family_deps) x(dep)
            WHERE dep->>'kind'='assertion_acceptance' AND dep#>>'{row,kind}'='assessed'
              AND (dep#>>'{row,relation_id}')::uuid<>r.relation_id;
        families := families || jsonb_build_array(jsonb_build_object(
            'relation_id',r.relation_id,'needs',needs,'assertion',assertion,
            'correction',jsonb_build_object('relation_id',r.relation_id,'pins',item->'corrections'),
            'type',memoriesql.relation_type_definition_v1(r.relation_type_revision_id),
            'deps',family_deps));
        IF octet_length(memoriesql.lifecycle_canonical_json_v1(families)) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    -- AM-5: a family is disclosed only with every assessed relation its records
    -- name, so no visible row shows a withheld relation's identity, state or
    -- history (as a replacement or through root status). Repeat until stable.
    IF read_mode='agent' THEN
        LOOP
            SELECT array_agg((f->>'relation_id')::uuid) INTO disclosed
                FROM jsonb_array_elements(families) f;
            SELECT COALESCE(jsonb_agg(f ORDER BY f->>'relation_id'),'[]') INTO kept
                FROM jsonb_array_elements(families) f
                WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(f->'needs') n(id)
                    WHERE NOT n.id::uuid=ANY(COALESCE(disclosed,'{}')));
            EXIT WHEN jsonb_array_length(kept)=jsonb_array_length(families);
            families := kept;
        END LOOP;
    END IF;
    SELECT COALESCE(jsonb_agg(f->'assertion' ORDER BY f->>'relation_id'),'[]'),
           COALESCE(jsonb_agg(f->'correction' ORDER BY f->>'relation_id'),'[]'),
           COALESCE(jsonb_agg(f->'type' ORDER BY f->>'relation_id'),'[]')
        INTO assertions, corrections, types FROM jsonb_array_elements(families) f;
    SELECT COALESCE(jsonb_agg(dep),'[]') INTO deps
        FROM jsonb_array_elements(families) f CROSS JOIN LATERAL jsonb_array_elements(f->'deps') x(dep);
    -- Pair coverage belongs to a captured task, including tasks with no accepted
    -- assertion. Authorize its entire captured bead population and all recorded
    -- proposals before exposing even a not_assessed pair. No task-status claim.
    -- AM-5: pair coverage stays owner-only for agents.
    FOR task IN SELECT a.* FROM memoriesql.relation_assessments a
                WHERE read_mode='owner' AND a.tenant_id=c.tenant_id AND a.workspace_id=c.workspace_id
                  AND a.created_at<=known AND EXISTS(
                    SELECT 1 FROM memoriesql.relation_pair_dispositions p
                    WHERE p.tenant_id=a.tenant_id AND p.task_id=a.task_id AND p.recorded_at<=known)
                ORDER BY a.task_id LOOP
        BEGIN
            family_deps := '[]'; task_pairs := '[]';
            FOR d IN SELECT value FROM jsonb_array_elements(task.beads) LOOP
                SELECT v.* INTO pin FROM memoriesql.bead_versions v
                WHERE v.tenant_id=c.tenant_id AND v.bead_version_id=(d->>'bead_version_id')::uuid;
                IF NOT FOUND OR pin.authored_at>known OR NOT memoriesql.current_context_bead_version_authorized(
                    c.tenant_id,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) THEN
                    RAISE EXCEPTION 'pair_unavailable' USING ERRCODE='42501';
                END IF;
                family_deps := family_deps || memoriesql.relation_bead_records_v2(c.tenant_id,pin.bead_id,known,read_mode);
            END LOOP;
            FOR r IN SELECT * FROM memoriesql.assessed_relations a
                     WHERE a.tenant_id=c.tenant_id AND a.task_id=task.task_id AND a.recorded_at<=known LOOP
                family_deps := family_deps || memoriesql.relation_assertion_records_v2(c.tenant_id,'assessed',r.relation_id,known,read_mode);
            END LOOP;
            FOR pair IN SELECT p.* FROM memoriesql.relation_pair_dispositions p
                        WHERE p.tenant_id=c.tenant_id AND p.task_id=task.task_id AND p.recorded_at<=known
                        ORDER BY p.first_bead_id,p.second_bead_id LOOP
                item := jsonb_build_object('task_id',pair.task_id,
                    'first_bead_id',pair.first_bead_id,'first_bead_version_id',pair.first_bead_version_id,
                    'second_bead_id',pair.second_bead_id,'second_bead_version_id',pair.second_bead_version_id,
                    'disposition',pair.disposition,'abstention',pair.abstention,'reason',pair.reason_text);
                task_pairs := task_pairs || jsonb_build_array(item);
                family_deps := family_deps || jsonb_build_array(jsonb_build_object('kind','pair_disposition',
                    'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(pair.task_id,pair.first_bead_id,pair.second_bead_id)),
                    'row',item));
            END LOOP;
            family_deps := family_deps || jsonb_build_array(jsonb_build_object('kind','pair_task_population',
                'id',task.task_id::text,'row',jsonb_build_object('task_id',task.task_id,'beads',task.beads)));
        EXCEPTION WHEN insufficient_privilege THEN CONTINUE;
        END;
        pairs := pairs || task_pairs; deps := deps || family_deps;
        IF octet_length(memoriesql.lifecycle_canonical_json_v1(
            jsonb_build_array(assertions,corrections,types,pairs,deps))) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    SELECT COALESCE(jsonb_agg(v ORDER BY v->>'key',(v->>'revision')::integer),'[]') INTO types
        FROM (SELECT DISTINCT value v FROM jsonb_array_elements(types)) x;
    SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO deps
        FROM (SELECT DISTINCT value v FROM jsonb_array_elements(deps)) x;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
        'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
        ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO manifest
        FROM jsonb_array_elements(deps) x(v);
    result := jsonb_build_object('assertions',assertions,'corrections',corrections,'types',types,'pairs',pairs,
        'frame',jsonb_build_object('known_at',memoriesql.relation_packet_time(known),
            'snapshot_at',memoriesql.relation_packet_time(snapshot_at),
            'dependency_manifest_sha256',memoriesql.lifecycle_hash_v1(manifest)),
        'dependency_records_json',memoriesql.lifecycle_canonical_json_v1(deps),
        'dependency_manifest_json',memoriesql.lifecycle_canonical_json_v1(manifest));
    IF octet_length(result::text) + 8192 > byte_budget THEN
        RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
    END IF;
    IF NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_relation_sql_population_v2(timestamptz,integer,text) FROM PUBLIC;

-- Earlier names keep their signatures, grants and owner semantics.

CREATE OR REPLACE FUNCTION memoriesql.relation_unit_records_v1(t uuid,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_unit_records_v2(t,id,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_bead_records_v1(t uuid,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_bead_records_v2(t,id,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.derivation_bead_authorize_v1(t uuid,id uuid,known timestamptz) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 PERFORM memoriesql.derivation_bead_authorize_v2(t,id,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.qualified_unit_roots_v1(t uuid,unit uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.qualified_unit_roots_v2(t,unit,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.qualified_bead_roots_v1(t uuid,bead uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.qualified_bead_roots_v2(t,bead,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_closure_authorized_v1(t uuid,k text,id uuid,known timestamptz,govern boolean DEFAULT false) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_closure_authorized_v2(t,k,id,known,govern,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_evidence_view_v3(t uuid,owner_kind_value text,owner uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_evidence_view_v4(t,owner_kind_value,owner,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_events_view_v3(t uuid,k text,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_events_view_v4(t,k,id,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_assertion_records_v1(t uuid,k text,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_assertion_records_v2(t,k,id,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_assertion_row_v1(t uuid,k text,id uuid,relative_bead uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 RETURN memoriesql.relation_assertion_row_v2(t,k,id,relative_bead,known,'owner');
END $$;

CREATE OR REPLACE FUNCTION memoriesql.prepare_relation_sql_population_v1(
    requested_known_at timestamptz, byte_budget integer
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off
AS $$
BEGIN
 RETURN memoriesql.prepare_relation_sql_population_v2(requested_known_at,byte_budget,'owner');
END $$;

-- PR-05's population entry (from 0037_observation_sql_population.sql) selects the relation read mode.
CREATE OR REPLACE FUNCTION memoriesql.prepare_query_sql_population_v2(
    requested_known_at timestamptz, requested_view text, byte_budget integer
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    snapshot_at timestamptz := statement_timestamp();
    known timestamptz; relation_part jsonb; b record; n record; st record; e record; u record;
    family_deps jsonb; family_observations jsonb; family_statements jsonb; family_sources jsonb;
    family_corrections jsonb; family_units uuid[]; unit_id uuid; successors integer;
    effective timestamptz; seen_units uuid[] := '{}';
    observations jsonb := '[]'; statements jsonb := '[]'; sources jsonb := '[]';
    units jsonb := '[]'; corrections jsonb := '[]'; deps jsonb; manifest jsonb; result jsonb;
    estimate bigint; relation_mode text; pkg jsonb; unit_text text; text_labels text[] := '{}';
BEGIN
    IF byte_budget IS NULL OR byte_budget NOT BETWEEN 8192 AND 67108864
       OR requested_view IS NULL OR requested_view NOT IN ('resolved','historical') THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    known := COALESCE(requested_known_at, snapshot_at);
    IF NOT isfinite(known) OR known > snapshot_at
       OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id)
       OR NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    -- PR-03's single projection supplies the assessed relations at the same
    -- explicit cutoff in this statement; its dependencies join one manifest.
    -- A caller with source.raw.read reads them as before (owner). A paired
    -- agent reads them under AM-5 (agent): memory.query and source.read over
    -- each relation's whole disclosed dependency closure, with lifecycle
    -- history and pair coverage withheld as owner-only. AM-5 names paired
    -- agents only: any other caller keeps the earlier gate, which withholds
    -- every relation without raw source authority, and the frame names no mode.
    relation_mode := CASE
        WHEN memoriesql.current_context_has_capability('source.raw.read') THEN 'owner'
        WHEN c.principal_kind='agent' AND c.pairing_grant_id IS NOT NULL THEN 'agent'
    END;
    relation_part := memoriesql.prepare_relation_sql_population_v2(
        known, byte_budget, COALESCE(relation_mode,'owner'));
    deps := (relation_part->>'dependency_records_json')::jsonb;
    -- Native jsonb text length is an admission estimate while accumulating. The
    -- exact canonical bytes are counted once below and again by the trusted
    -- decoder; nothing is disclosed from a partial population.
    estimate := octet_length(relation_part::text);
    FOR b IN SELECT a.bead_id, a.bead_version_id, v.event_id, v.source_unit_id, v.authored_at,
                    v.bead_type_revision_id, t.stable_key, v.render_contract_revision,
                    v.render_payload, v.title, v.summary, v.detail
             FROM memoriesql.accepted_bead_semantics a
             JOIN memoriesql.bead_versions v
               ON v.tenant_id=a.tenant_id AND v.bead_version_id=a.bead_version_id
             JOIN memoriesql.bead_types t ON t.bead_type_id=v.bead_type_id
             WHERE a.tenant_id=c.tenant_id AND a.workspace_id=c.workspace_id AND v.authored_at<=known
             ORDER BY a.bead_id LOOP
        family_corrections := '[]'; successors := 0;
        BEGIN
            family_deps := memoriesql.query_bead_records_v1(c.tenant_id,b.bead_id,known);
            -- Correction neighbours at this frame are required dependencies. A
            -- protected successor or predecessor withholds this whole family:
            -- no successor count, branch or older-as-current presentation leaks.
            FOR n IN SELECT s.bead_id AS successor_bead_id, s.bead_version_id AS successor_version_id,
                            s.superseded_bead_id, s.superseded_bead_version_id,
                            s.correction_reason, sv.authored_at
                     FROM memoriesql.bead_supersessions s
                     JOIN memoriesql.bead_versions sv
                       ON sv.tenant_id=s.tenant_id AND sv.bead_version_id=s.bead_version_id
                     WHERE s.tenant_id=c.tenant_id AND sv.authored_at<=known
                       AND ((s.superseded_bead_id=b.bead_id AND s.superseded_bead_version_id=b.bead_version_id)
                         OR (s.bead_id=b.bead_id AND s.bead_version_id=b.bead_version_id))
                     ORDER BY s.bead_id, s.superseded_bead_id LOOP
                IF n.successor_bead_id=b.bead_id THEN
                    family_deps := family_deps
                        || memoriesql.query_bead_records_v1(c.tenant_id,n.superseded_bead_id,known);
                    family_corrections := family_corrections || jsonb_build_array(jsonb_build_object(
                        'predecessor_version_id',n.superseded_bead_version_id,
                        'successor_version_id',n.successor_version_id,
                        'predecessor_bead_id',n.superseded_bead_id,
                        'successor_bead_id',n.successor_bead_id,
                        'reason',n.correction_reason,
                        'recorded_at',memoriesql.relation_packet_time(n.authored_at)));
                ELSE
                    successors := successors + 1;
                    family_deps := family_deps
                        || memoriesql.query_bead_records_v1(c.tenant_id,n.successor_bead_id,known);
                END IF;
                family_deps := family_deps || jsonb_build_array(jsonb_build_object(
                    'kind','bead_supersession',
                    'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(
                        n.superseded_bead_version_id,n.successor_version_id)),
                    'row',jsonb_build_object('predecessor_bead_id',n.superseded_bead_id,
                        'predecessor_version_id',n.superseded_bead_version_id,
                        'successor_bead_id',n.successor_bead_id,
                        'successor_version_id',n.successor_version_id,
                        'reason',n.correction_reason,
                        'recorded_at',memoriesql.relation_packet_time(n.authored_at))));
            END LOOP;
        EXCEPTION WHEN insufficient_privilege THEN CONTINUE;
        END;
        -- Effective time is only an actually represented instant of the bead's own
        -- source unit (or, absent unit time, its event). Imprecise stays null.
        SELECT CASE
                 WHEN su.unit_source_occurred_at IS NOT NULL THEN
                   CASE WHEN su.unit_time_precision IN ('instant','second') THEN su.unit_source_occurred_at END
                 WHEN ev.source_time_precision IN ('instant','second') THEN ev.source_occurred_at
               END
          INTO effective
          FROM memoriesql.source_units su
          JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id
         WHERE su.tenant_id=c.tenant_id AND su.source_unit_id=b.source_unit_id;
        family_observations := '[]';
        -- Resolved view withholds superseded and branched predecessors but keeps
        -- every unsuperseded successor branch; it never chooses a recency winner.
        IF requested_view='historical' OR successors=0 THEN
            family_observations := jsonb_build_array(jsonb_build_object(
                'bead_id',b.bead_id,'bead_version_id',b.bead_version_id,'event_id',b.event_id,
                'source_unit_id',b.source_unit_id,'bead_type_key',b.stable_key,
                'bead_type_revision_id',b.bead_type_revision_id,
                'title',CASE WHEN b.render_contract_revision=2 THEN b.title END,
                'summary',CASE WHEN b.render_contract_revision=2 THEN b.summary END,
                'detail',CASE WHEN b.render_contract_revision=2 THEN b.detail END,
                'render_state',CASE WHEN b.render_contract_revision=2 AND b.render_payload IS NOT NULL
                                    THEN 'present' ELSE 'unsupported' END,
                'recorded_at',memoriesql.relation_packet_time(b.authored_at),
                'effective_at',memoriesql.relation_packet_time(effective),
                'effective_basis',CASE WHEN effective IS NULL THEN 'unknown' ELSE 'source' END,
                'correction_state',CASE successors WHEN 0 THEN 'unsuperseded' WHEN 1 THEN 'superseded'
                                                   ELSE 'branched' END));
        END IF;
        family_statements := '[]'; family_sources := '[]'; family_units := ARRAY[b.source_unit_id];
        FOR st IN SELECT * FROM memoriesql.bead_semantic_statements
                  WHERE tenant_id=c.tenant_id AND bead_version_id=b.bead_version_id
                  ORDER BY statement_sequence, statement_id LOOP
            family_statements := family_statements || jsonb_build_array(jsonb_build_object(
                'statement_id',st.statement_id,'bead_id',st.bead_id,'bead_version_id',st.bead_version_id,
                'sequence',st.statement_sequence,'kind',st.statement_kind,'text',st.statement_text,
                'supersedes_statement_id',st.supersedes_statement_id,
                'correction_reason',st.correction_reason,
                'recorded_at',memoriesql.relation_packet_time(st.created_at)));
            FOR e IN SELECT * FROM memoriesql.bead_semantic_statement_evidence
                     WHERE tenant_id=c.tenant_id AND statement_id=st.statement_id
                     ORDER BY evidence_source_unit_id LOOP
                family_sources := family_sources || jsonb_build_array(jsonb_build_object(
                    'statement_id',st.statement_id,'source_unit_id',e.evidence_source_unit_id,
                    'event_id',e.evidence_event_id,'content_sha256',e.evidence_content_hash));
                family_units := family_units || e.evidence_source_unit_id;
            END LOOP;
        END LOOP;
        -- Only units with an actual support link to an admitted accepted
        -- observation enter; unlinked raw source discovery stays excluded.
        FOREACH unit_id IN ARRAY family_units LOOP
            CONTINUE WHEN unit_id = ANY(seen_units);
            seen_units := seen_units || unit_id;
            SELECT su.source_unit_id, su.content_hash, su.event_id, ev.source_object_id,
                   ev.source_type, su.content_text, su.unit_source_occurred_at,
                   su.unit_time_precision, ev.source_occurred_at, ev.source_time_precision,
                   ev.source_occurred_at_raw, ev.source_timezone, ev.recorded_at,
                   ev.actor_id, ev.actor_kind
              INTO u
              FROM memoriesql.source_units su
              JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id
             WHERE su.tenant_id=c.tenant_id AND su.source_unit_id=unit_id;
            -- A materialized unit's text is its pinned package's normalized
            -- projection, labelled with its version and declared limits; an
            -- identity (raw) or mixed package leaves only the citation.
            pkg := memoriesql.query_unit_package_v1(c.tenant_id,unit_id);
            unit_text := COALESCE(u.content_text,
                CASE WHEN (pkg->>'normalized')::boolean THEN pkg->>'text' END);
            IF u.content_text IS NULL AND unit_text IS NOT NULL THEN
                text_labels := text_labels
                    || ('normalized_projection:'||(pkg->>'normalization_policy_version'))
                    || ARRAY(SELECT 'package_exclusion:'||x
                             FROM jsonb_array_elements_text(pkg->'exclusions') x)
                    || ARRAY(SELECT 'package_unresolved:'||x
                             FROM jsonb_array_elements_text(pkg->'unresolved_coverage') x);
            END IF;
            units := units || jsonb_build_array(jsonb_build_object(
                'source_unit_id',u.source_unit_id,'content_sha256',u.content_hash,
                'event_id',u.event_id,'source_object_id',u.source_object_id,
                'source_kind',u.source_type,'package_revision_id',pkg->>'package_id',
                'search_text',unit_text,
                'text_state',CASE WHEN unit_text IS NULL THEN 'unsupported' ELSE 'available' END,
                'source_occurred_at',memoriesql.relation_packet_time(
                    COALESCE(u.unit_source_occurred_at,u.source_occurred_at)),
                'source_time_original',CASE WHEN u.unit_source_occurred_at IS NULL
                                            THEN u.source_occurred_at_raw END,
                'source_timezone',u.source_timezone,
                'source_precision',CASE
                    WHEN u.unit_source_occurred_at IS NOT NULL THEN COALESCE(u.unit_time_precision,'unknown')
                    WHEN u.source_occurred_at IS NOT NULL THEN COALESCE(u.source_time_precision,'unknown')
                    ELSE 'unknown' END,
                'recorded_at',memoriesql.relation_packet_time(u.recorded_at),
                'actor_ref',u.actor_id,'actor_role',u.actor_kind,
                'trust_label',NULL,'occurrence_ref',NULL));
        END LOOP;
        observations := observations || family_observations;
        statements := statements || family_statements;
        sources := sources || family_sources;
        corrections := corrections || family_corrections;
        deps := deps || family_deps;
        estimate := estimate + octet_length(family_observations::text)
            + octet_length(family_statements::text) + octet_length(family_sources::text)
            + octet_length(family_corrections::text) + octet_length(family_deps::text);
        IF estimate + octet_length(units::text) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]')
      INTO deps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(deps)) x;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
        'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
        ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO manifest
      FROM jsonb_array_elements(deps) x(v);
    result := jsonb_build_object(
        'population_revision',2,
        'assertions',relation_part->'assertions','corrections',relation_part->'corrections',
        'types',relation_part->'types','pairs',relation_part->'pairs',
        'observations',observations,'statements',statements,'statement_sources',sources,
        'source_units',units,'observation_corrections',corrections,
        'frame',jsonb_build_object('known_at',memoriesql.relation_packet_time(known),
            'snapshot_at',memoriesql.relation_packet_time(snapshot_at),
            'view',requested_view,
            'dependency_manifest_sha256',memoriesql.lifecycle_hash_v1(manifest),
            -- The shared PR-03 projector's own visible-dependency hash, reported
            -- only when a result actually uses a relation projection.
            'relation_manifest_sha256',relation_part#>>'{frame,dependency_manifest_sha256}',
            -- The caller's OWN capabilities (never data existence) and the
            -- relation read mode they select. Missing authority and owner-only
            -- relation history are disclosed as gaps.
            'relation_raw_authority',memoriesql.current_context_has_capability('source.raw.read'),
            'relation_read_mode',relation_mode,
            'source_read_authority',memoriesql.current_context_has_capability('source.read'),
            -- Labels of served package text: its normalized projection version
            -- and the package's own declared coverage limits.
            'source_text_labels',COALESCE((SELECT jsonb_agg(x ORDER BY x COLLATE "C")
                FROM (SELECT DISTINCT l AS x FROM unnest(text_labels) l) d),'[]'::jsonb)),
        'dependency_records_json',memoriesql.lifecycle_canonical_json_v1(deps),
        'dependency_manifest_json',memoriesql.lifecycle_canonical_json_v1(manifest));
    IF octet_length(result::text) + 8192 > byte_budget THEN
        RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
    END IF;
    IF NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    RETURN result;
END $$;
