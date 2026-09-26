-- Approved forward assessed-relation lifecycle, schema 30.
-- Approval: docs/approvals/pr-03-lifecycle-2026-09-26.md. Historical SQL is untouched.
-- Lock order: tenant authority, tenant lineage, operation key, sorted type, sorted assertion.
CREATE TABLE memoriesql.assessed_relation_events (
 tenant_id uuid NOT NULL, workspace_id uuid NOT NULL, relation_event_id uuid NOT NULL,
 relation_id uuid NOT NULL, event_number integer NOT NULL CHECK(event_number>0),
 previous_event_id uuid, action text NOT NULL CHECK(action IN ('confirm','dispute','retract')),
 reason text NOT NULL CHECK(char_length(reason) BETWEEN 1 AND 1024 AND btrim(reason)<>''),
 evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='array' AND jsonb_array_length(evidence)<=8),
 normalized_command jsonb NOT NULL, authorization_context_id uuid NOT NULL,
 recorded_by_principal_id uuid NOT NULL, recorded_by_user_id uuid,
 idempotency_receipt_id uuid NOT NULL, recorded_at timestamptz NOT NULL, effective_at timestamptz, decision_context jsonb NOT NULL,
 PRIMARY KEY(tenant_id,relation_event_id), UNIQUE(tenant_id,relation_id,event_number),
 FOREIGN KEY(tenant_id,relation_id) REFERENCES memoriesql.assessed_relations(tenant_id,relation_id),
 FOREIGN KEY(tenant_id,previous_event_id) REFERENCES memoriesql.assessed_relation_events(tenant_id,relation_event_id),
 FOREIGN KEY(tenant_id,idempotency_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id),
 FOREIGN KEY(tenant_id,recorded_by_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id),
 CHECK((event_number=1)=(previous_event_id IS NULL)), CHECK(action<>'confirm' OR jsonb_array_length(evidence)>0),
 CHECK(effective_at IS NULL OR effective_at<=recorded_at)
);
ALTER TABLE memoriesql.assessed_relation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.assessed_relation_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.assessed_relation_events FROM PUBLIC, memoriesql_application;
CREATE TRIGGER assessed_relation_events_immutable BEFORE UPDATE OR DELETE ON memoriesql.assessed_relation_events
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Python canonical_json_bytes encoding, including ASCII escaping and surrogate pairs.
CREATE FUNCTION memoriesql.lifecycle_canonical_json_v1(v jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,memoriesql AS $$
DECLARE result text; ch text; n integer; i integer;
BEGIN
 CASE jsonb_typeof(v)
 WHEN 'object' THEN SELECT '{'||COALESCE(string_agg(memoriesql.lifecycle_canonical_json_v1(to_jsonb(key))||':'||CASE WHEN key='author_confidence' AND jsonb_typeof(value)='number' THEN CASE WHEN value::numeric IN (0,1) THEN (value::numeric)::integer::text||'.0' ELSE trim_scale(value::numeric)::text END ELSE memoriesql.lifecycle_canonical_json_v1(value) END,',' ORDER BY key COLLATE "C"),'')||'}' INTO result FROM jsonb_each(v);
 WHEN 'array' THEN SELECT '['||COALESCE(string_agg(memoriesql.lifecycle_canonical_json_v1(value),',' ORDER BY ord),'')||']' INTO result FROM jsonb_array_elements(v) WITH ORDINALITY e(value,ord);
 WHEN 'string' THEN
  result:='"';
  FOR i IN 1..char_length(v#>>'{}') LOOP
   ch:=substr(v#>>'{}',i,1); n:=ascii(ch);
   IF ch='"' THEN result:=result||E'\\"';
   ELSIF ch=chr(92) THEN result:=result||chr(92)||chr(92);
   ELSIF n IN (8,9,10,12,13) THEN result:=result||chr(92)||CASE n WHEN 8 THEN 'b' WHEN 9 THEN 't' WHEN 10 THEN 'n' WHEN 12 THEN 'f' ELSE 'r' END;
   ELSIF n<32 OR n>=127 THEN
    IF n<=65535 THEN result:=result||chr(92)||'u'||lpad(to_hex(n),4,'0');
    ELSE n:=n-65536; result:=result||chr(92)||'u'||to_hex(55296+n/1024)||chr(92)||'u'||to_hex(56320+n%1024); END IF;
   ELSE result:=result||ch; END IF;
  END LOOP;
  result:=result||'"';
 ELSE result:=v::text;
 END CASE;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.lifecycle_canonical_json_v1(jsonb) FROM PUBLIC;
CREATE FUNCTION memoriesql.lifecycle_hash_v1(v jsonb) RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,memoriesql AS $$ SELECT encode(sha256(convert_to(memoriesql.lifecycle_canonical_json_v1(v),'UTF8')),'hex') $$;
REVOKE ALL ON FUNCTION memoriesql.lifecycle_hash_v1(jsonb) FROM PUBLIC;

-- One identity view; no inference, actor ownership or vocabulary recency.
CREATE FUNCTION memoriesql.relation_assertions_v1(t uuid, known timestamptz)
RETURNS TABLE(kind text,relation_id uuid,workspace_id uuid,source_bead_id uuid,source_bead_version_id uuid,target_bead_id uuid,target_bead_version_id uuid,relation_type_revision_id uuid,acceptance text,acceptance_receipt_id uuid,recorded_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT 'authored',r.relation_id,r.workspace_id,r.source_bead_id,r.source_bead_version_id,r.target_bead_id,r.target_bead_version_id,r.relation_type_revision_id,'accepted',s.idempotency_receipt_id,r.recorded_at
 FROM memoriesql.bead_relations r JOIN memoriesql.bead_versions v ON v.tenant_id=r.tenant_id AND v.bead_id=r.authoring_bead_id AND v.bead_version_id=r.authoring_bead_version_id
 JOIN memoriesql.semantic_task_receipts s ON s.tenant_id=v.tenant_id AND s.semantic_task_receipt_id=v.semantic_task_receipt_id WHERE r.tenant_id=t AND r.recorded_at<=known
 UNION ALL
 SELECT 'assessed',r.relation_id,r.workspace_id,r.source_bead_id,r.source_bead_version_id,r.target_bead_id,r.target_bead_version_id,r.relation_type_revision_id,r.acceptance,s.idempotency_receipt_id,r.recorded_at
 FROM memoriesql.assessed_relations r JOIN memoriesql.idempotency_receipts s ON s.tenant_id=r.tenant_id AND s.resource_id=r.task_id AND s.operation_kind='relation_assessment.apply.v1' WHERE r.tenant_id=t AND r.recorded_at<=known
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assertions_v1(uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_corrections_v1(t uuid,k text,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 WITH RECURSIVE pins(role,bead_id) AS (
 SELECT 'source',source_bead_id FROM memoriesql.relation_assertions_v1(t,known) WHERE kind=k AND relation_id=id
 UNION SELECT 'target',target_bead_id FROM memoriesql.relation_assertions_v1(t,known) WHERE kind=k AND relation_id=id
 UNION SELECT 'basis',bead_id FROM memoriesql.assessed_relation_statements WHERE k='assessed' AND tenant_id=t AND relation_id=id AND role='basis'
 ), successors(role,pinned_bead_id,successor_bead_id,successor_bead_version_id,recorded_at) AS (
 SELECT p.role,p.bead_id,s.bead_id,s.bead_version_id,v.authored_at FROM pins p JOIN memoriesql.bead_supersessions s ON s.tenant_id=t AND s.superseded_bead_id=p.bead_id JOIN memoriesql.bead_versions v ON v.tenant_id=t AND v.bead_version_id=s.bead_version_id WHERE v.authored_at<=known
 UNION
 SELECT p.role,p.pinned_bead_id,s.bead_id,s.bead_version_id,v.authored_at FROM successors p JOIN memoriesql.bead_supersessions s ON s.tenant_id=t AND s.superseded_bead_id=p.successor_bead_id JOIN memoriesql.bead_versions v ON v.tenant_id=t AND v.bead_version_id=s.bead_version_id WHERE v.authored_at<=known
 ) SELECT COALESCE(jsonb_agg(jsonb_build_object('role',role,'pinned_bead_id',pinned_bead_id,'successor_bead_id',successor_bead_id,'successor_bead_version_id',successor_bead_version_id,'recorded_at',memoriesql.relation_packet_time(recorded_at)) ORDER BY role,pinned_bead_id,successor_bead_id,successor_bead_version_id),'[]') FROM successors
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_corrections_v1(uuid,text,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_projection_v1(t uuid,k text,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE r record; corrections jsonb; events jsonb; replacement jsonb; state text; manifest jsonb; pending boolean; latest text;
BEGIN
 SELECT * INTO r FROM memoriesql.relation_assertions_v1(t,known) WHERE kind=k AND relation_id=id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 corrections:=memoriesql.relation_corrections_v1(t,k,id,known); pending:=jsonb_array_length(corrections)>0;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('origin',origin,'event_id',event_id,'event_number',event_number,'recorded_at',memoriesql.relation_packet_time(recorded_at)) ORDER BY recorded_at,event_number NULLS LAST,event_id),'[]') INTO events FROM (
 SELECT e.origin,e.relation_event_id event_id,NULL::integer event_number,e.recorded_at FROM memoriesql.bead_relation_events e WHERE k='authored' AND e.tenant_id=t AND e.relation_id=id AND e.recorded_at<=known
 UNION ALL SELECT 'governed',e.relation_event_id,e.event_number,e.recorded_at FROM memoriesql.assessed_relation_events e WHERE k='assessed' AND e.tenant_id=t AND e.relation_id=id AND e.recorded_at<=known
 UNION ALL SELECT 'authored',e.retirement_id,NULL,e.recorded_at FROM memoriesql.relation_retirements e WHERE e.tenant_id=t AND e.retired_kind=k AND e.retired_relation_id=id AND e.recorded_at<=known) e;
 SELECT COALESCE(jsonb_agg(x ORDER BY x),'[]') INTO replacement FROM (
 SELECT replacement_relation_id x FROM memoriesql.relation_retirements WHERE tenant_id=t AND retired_kind=k AND retired_relation_id=id AND recorded_at<=known
 UNION SELECT replacement_relation_id FROM memoriesql.bead_relation_events WHERE k='authored' AND tenant_id=t AND relation_id=id AND action='supersede' AND recorded_at<=known) a;
 SELECT action INTO latest FROM (
 SELECT action,event_number::bigint seq,recorded_at,relation_event_id eid FROM memoriesql.assessed_relation_events WHERE k='assessed' AND tenant_id=t AND relation_id=id AND recorded_at<=known
 UNION ALL SELECT action,NULL,recorded_at,relation_event_id FROM memoriesql.bead_relation_events WHERE k='authored' AND tenant_id=t AND relation_id=id AND recorded_at<=known) e ORDER BY seq DESC NULLS LAST,recorded_at DESC,eid DESC LIMIT 1;
 IF k='authored' THEN
 latest:=CASE WHEN EXISTS(SELECT 1 FROM memoriesql.bead_relation_events d WHERE d.tenant_id=t AND d.relation_id=id AND d.action='dispute' AND d.recorded_at<=known AND NOT EXISTS(SELECT 1 FROM memoriesql.bead_relation_events c WHERE c.tenant_id=t AND c.relation_id=id AND c.action='confirm' AND c.recorded_at<=known AND c.recorded_at>d.recorded_at)) THEN 'dispute' ELSE NULL END;
 END IF;
 state:=CASE WHEN r.acceptance='not_accepted' THEN 'not_accepted'
 WHEN EXISTS(SELECT 1 FROM memoriesql.assessed_relation_events WHERE k='assessed' AND tenant_id=t AND relation_id=id AND action='retract' AND recorded_at<=known) OR EXISTS(SELECT 1 FROM memoriesql.bead_relation_events WHERE k='authored' AND tenant_id=t AND relation_id=id AND action='retract' AND recorded_at<=known) THEN 'retracted'
 WHEN jsonb_array_length(replacement)>0 THEN 'superseded' WHEN latest='dispute' THEN 'disputed' WHEN pending THEN 'reassessment_pending' ELSE 'active' END;
 manifest:=jsonb_build_object('projection_version',1,'tenant_id',t,'workspace_id',r.workspace_id,'relation_id',id,'acceptance_receipt_id',r.acceptance_receipt_id,'acceptance',r.acceptance,'events',events,'corrections',corrections);
 RETURN jsonb_build_object('state',state,'acceptance',r.acceptance,'acceptance_receipt_id',r.acceptance_receipt_id,'head_token',memoriesql.lifecycle_hash_v1(manifest),'head_manifest',manifest,'support_eligible',state='active' AND NOT pending,'support_reason',CASE WHEN state='active' AND NOT pending THEN NULL WHEN state='reassessment_pending' THEN 'corrected' ELSE state END,'correction_pending',pending,'corrections',corrections,'superseded_by',replacement,
 'endpoint_corrected_by',COALESCE((SELECT jsonb_agg(x ORDER BY x) FROM (SELECT DISTINCT v->'successor_bead_id' x FROM jsonb_array_elements(corrections) e(v) WHERE v->>'role'<>'basis') a),'[]'),
 'basis_corrected_by',COALESCE((SELECT jsonb_agg(x ORDER BY x) FROM (SELECT DISTINCT v->'successor_bead_id' x FROM jsonb_array_elements(corrections) e(v) WHERE v->>'role'='basis') a),'[]'));
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_projection_v1(uuid,text,uuid,timestamptz) FROM PUBLIC;
CREATE OR REPLACE FUNCTION memoriesql.assessed_relation_state_v1(t uuid,relation uuid,known timestamptz) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$ SELECT memoriesql.relation_projection_v1(t,'assessed',relation,known) $$;
CREATE OR REPLACE FUNCTION memoriesql.bead_relation_state_v1(t uuid,relation uuid,known timestamptz) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$ SELECT memoriesql.relation_projection_v1(t,'authored',relation,known) $$;

-- Complete current closure authorization independent of original activator authority.
CREATE FUNCTION memoriesql.relation_closure_authorized_v1(t uuid,k text,id uuid,known timestamptz,govern boolean DEFAULT false) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r record; pin record; e record; dependency jsonb; src uuid; ids uuid[]; a uuid; q jsonb;
BEGIN
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
    PERFORM memoriesql.revisiting_source_authorize(e.source_object_id);
   END LOOP;
  END LOOP;
  q:=memoriesql.relation_projection_v1(t,r.kind,a,known);
  FOR dependency IN SELECT value FROM jsonb_array_elements(q->'corrections') LOOP
   SELECT v.* INTO pin FROM memoriesql.bead_versions v WHERE v.tenant_id=t AND v.bead_version_id=(dependency->>'successor_bead_version_id')::uuid;
   IF NOT FOUND OR NOT memoriesql.current_context_bead_version_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) OR (govern AND NOT memoriesql.current_context_accepted_bead_maintain_authorized(t,pin.workspace_id,pin.access_scope_id,pin.bead_version_id)) THEN RETURN false; END IF;
   SELECT source_object_id INTO src FROM memoriesql.source_events WHERE tenant_id=t AND event_id=pin.event_id;
   PERFORM memoriesql.revisiting_source_authorize(src);
   FOR e IN SELECT x.*,ev.source_object_id FROM memoriesql.bead_semantic_statement_evidence x JOIN memoriesql.bead_semantic_statements st ON st.tenant_id=x.tenant_id AND st.statement_id=x.statement_id JOIN memoriesql.source_events ev ON ev.tenant_id=x.tenant_id AND ev.event_id=x.evidence_event_id WHERE st.tenant_id=t AND st.bead_version_id=pin.bead_version_id LOOP
    IF NOT memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.query','read') OR (govern AND NOT memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.maintain','read')) THEN RETURN false; END IF;
    PERFORM memoriesql.revisiting_source_authorize(e.source_object_id);
   END LOOP;
  END LOOP;
  FOR dependency IN SELECT value FROM memoriesql.assessed_relation_events g CROSS JOIN LATERAL jsonb_array_elements(g.evidence) WHERE r.kind='assessed' AND g.tenant_id=t AND g.relation_id=a AND g.recorded_at<=known UNION SELECT jsonb_build_object('statement_id',l.statement_id,'source_unit_id',l.evidence_source_unit_id,'content_hash',x.evidence_content_hash) FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind='relation_event' AND l.recorded_at<=known AND l.owner_id IN (SELECT relation_event_id FROM memoriesql.bead_relation_events WHERE r.kind='authored' AND tenant_id=t AND relation_id=a AND recorded_at<=known UNION SELECT retirement_id FROM memoriesql.relation_retirements WHERE tenant_id=t AND retired_kind=r.kind AND retired_relation_id=a AND recorded_at<=known) LOOP
   IF NOT memoriesql.assessed_lifecycle_evidence_authorized_v1(t,c.workspace_id,dependency) AND govern THEN RETURN false; END IF;
   SELECT s.*,x.evidence_event_id,x.access_scope_id evidence_scope,ev.source_object_id INTO e FROM memoriesql.bead_semantic_statements s JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=s.tenant_id AND x.statement_id=s.statement_id JOIN memoriesql.source_events ev ON ev.tenant_id=x.tenant_id AND ev.event_id=x.evidence_event_id WHERE s.tenant_id=t AND s.statement_id=(dependency->>'statement_id')::uuid AND x.evidence_source_unit_id=(dependency->>'source_unit_id')::uuid AND x.evidence_content_hash=dependency->>'content_hash';
   IF NOT FOUND OR NOT memoriesql.current_context_bead_version_authorized(t,e.workspace_id,e.access_scope_id,e.bead_version_id) OR NOT memoriesql.current_context_event_authorized(e.evidence_scope,e.evidence_event_id,'memory.query','read') THEN RETURN false; END IF;
   PERFORM memoriesql.revisiting_source_authorize(e.source_object_id);
  END LOOP;
 END LOOP;
 RETURN true;
EXCEPTION WHEN insufficient_privilege THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_closure_authorized_v1(uuid,text,uuid,timestamptz,boolean) FROM PUBLIC;

CREATE FUNCTION memoriesql.record_assessed_relation_event_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r memoriesql.assessed_relations%ROWTYPE; old memoriesql.idempotency_receipts%ROWTYPE;
 cmd jsonb; e jsonb; q jsonb; result jsonb; internal_key text; h text; action_value text; eid uuid:=uuidv7(); rid uuid:=uuidv7(); previous uuid; number integer; type_id uuid; src uuid; now_at timestamptz; effective timestamptz; err text;
 uuid_re constant text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR NOT(request ?& ARRAY['contract_version','expected_schema_version','idempotency_key','relation_id','action','reason','evidence','effective_at','expected_head_token']) OR request-ARRAY['contract_version','expected_schema_version','idempotency_key','relation_id','action','reason','evidence','effective_at','expected_head_token']<>'{}' THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 IF request->'contract_version'<>'1'::jsonb THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 IF request->'expected_schema_version'<>'30'::jsonb THEN RAISE EXCEPTION 'schema_mismatch' USING ERRCODE='22023'; END IF;
 action_value:=request->>'action';
 IF jsonb_typeof(request->'idempotency_key')<>'string' OR char_length(request->>'idempotency_key') NOT BETWEEN 1 AND 512 OR NOT memoriesql.lifecycle_nonblank_v1(request->>'idempotency_key') OR jsonb_typeof(request->'reason')<>'string' OR char_length(request->>'reason') NOT BETWEEN 1 AND 1024 OR NOT memoriesql.lifecycle_nonblank_v1(request->>'reason') OR COALESCE(action_value,'') NOT IN('confirm','dispute','retract') OR COALESCE(request->>'relation_id','')!~uuid_re OR jsonb_typeof(request->'expected_head_token')<>'string' OR COALESCE(request->>'expected_head_token','')!~'^[a-f0-9]{64}$' OR jsonb_typeof(request->'evidence')<>'array' OR (CASE WHEN jsonb_typeof(request->'evidence')='array' THEN jsonb_array_length(request->'evidence') ELSE -1 END)>8 OR (action_value='confirm' AND (CASE WHEN jsonb_typeof(request->'evidence')='array' THEN jsonb_array_length(request->'evidence') ELSE -1 END)=0) OR jsonb_typeof(request->'effective_at') NOT IN('string','null') THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
  IF jsonb_typeof(e)<>'object' OR NOT(e ?& ARRAY['statement_id','source_unit_id','content_hash']) OR e-ARRAY['statement_id','source_unit_id','content_hash']<>'{}' OR COALESCE(e->>'statement_id','')!~uuid_re OR COALESCE(e->>'source_unit_id','')!~uuid_re OR jsonb_typeof(e->'content_hash')<>'string' OR COALESCE(e->>'content_hash','')!~'^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF (SELECT count(*)<>count(DISTINCT jsonb_build_array(value->'statement_id',value->'source_unit_id')) FROM jsonb_array_elements(request->'evidence')) THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 IF request->>'effective_at' IS NOT NULL THEN
  IF request->>'effective_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
  effective:=memoriesql.lifecycle_parse_time_v1(request->>'effective_at');
  IF NOT isfinite(effective) OR extract(year FROM effective AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 END IF;
 cmd:=request||jsonb_build_object('effective_at',memoriesql.relation_packet_time(effective),'evidence',(SELECT COALESCE(jsonb_agg(value ORDER BY value->>'statement_id',value->>'source_unit_id'),'[]') FROM jsonb_array_elements(request->'evidence')));
 IF octet_length(memoriesql.lifecycle_canonical_json_v1(cmd))>16384 THEN RAISE EXCEPTION 'budget_exhausted' USING ERRCODE='54000'; END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF NOT FOUND OR c.principal_kind<>'human' THEN RAISE EXCEPTION 'unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(c.tenant_id::text||':semantic_outcome_authority:',0));
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':relation-lineage:',0));
 IF NOT memoriesql.relation_closure_authorized_v1(c.tenant_id,'assessed',(cmd->>'relation_id')::uuid,clock_timestamp(),true) THEN RAISE EXCEPTION 'unavailable' USING ERRCODE='42501'; END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(cmd->'evidence') LOOP
  IF NOT memoriesql.assessed_lifecycle_evidence_authorized_v1(c.tenant_id,c.workspace_id,e) THEN RAISE EXCEPTION 'unavailable' USING ERRCODE='42501'; END IF;
  SELECT ev.source_object_id INTO src FROM memoriesql.bead_semantic_statement_evidence x JOIN memoriesql.source_events ev ON ev.tenant_id=x.tenant_id AND ev.event_id=x.evidence_event_id WHERE x.tenant_id=c.tenant_id AND x.statement_id=(e->>'statement_id')::uuid AND x.evidence_source_unit_id=(e->>'source_unit_id')::uuid;
  PERFORM memoriesql.revisiting_source_authorize(src);
 END LOOP;
 internal_key:=memoriesql.lifecycle_hash_v1(jsonb_build_array(c.workspace_id,c.principal_id,cmd->>'idempotency_key')); h:=memoriesql.lifecycle_hash_v1(cmd);
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':assessed_relation.event.v1:'||internal_key,0));
 SELECT * INTO old FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='assessed_relation.event.v1' AND idempotency_key=internal_key;
 IF FOUND THEN
  IF old.request_hash<>h THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
  IF old.status<>'succeeded' OR old.response_receipt IS NULL OR jsonb_typeof(old.response_receipt)<>'object' OR NOT (old.response_receipt ?& ARRAY['contract_version','relation_event_id','relation_id','action','event_number','previous_event_id','recorded_at','effective_at','idempotency_receipt_id','head_token','state','support_eligible']) OR NOT EXISTS(SELECT 1 FROM memoriesql.assessed_relation_events g WHERE g.tenant_id=c.tenant_id AND g.idempotency_receipt_id=old.idempotency_receipt_id AND g.relation_event_id::text=old.response_receipt->>'relation_event_id' AND g.relation_id::text=cmd->>'relation_id' AND g.normalized_command=cmd) THEN RAISE EXCEPTION 'receipt_incomplete' USING ERRCODE='55000'; END IF;
  RETURN old.response_receipt||'{"replayed":true}';
 END IF;
 SELECT ar.* INTO r FROM memoriesql.assessed_relations ar WHERE ar.tenant_id=c.tenant_id AND ar.workspace_id=c.workspace_id AND ar.relation_id=(cmd->>'relation_id')::uuid;
 SELECT tr.relation_type_id INTO type_id FROM memoriesql.relation_type_revisions tr WHERE tr.relation_type_revision_id=r.relation_type_revision_id;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':relation-cycle:'||type_id::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':bead-relation:'||r.relation_id::text,0));
 now_at:=clock_timestamp();
 IF NOT memoriesql.relation_closure_authorized_v1(c.tenant_id,'assessed',r.relation_id,now_at,true) THEN RAISE EXCEPTION 'unavailable' USING ERRCODE='42501'; END IF;
 q:=memoriesql.relation_projection_v1(c.tenant_id,'assessed',r.relation_id,now_at);
 IF q->>'head_token'<>cmd->>'expected_head_token' THEN RAISE EXCEPTION 'head_conflict' USING ERRCODE='40001'; END IF;
 IF q->>'state' IN('not_accepted','retracted','superseded') THEN RAISE EXCEPTION 'transition_invalid' USING ERRCODE='55000'; END IF;
 IF action_value='confirm' AND (q->>'correction_pending')::boolean THEN RAISE EXCEPTION 'reassessment_required' USING ERRCODE='55000'; END IF;
 IF effective>now_at THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 SELECT relation_event_id,event_number+1 INTO previous,number FROM memoriesql.assessed_relation_events WHERE tenant_id=c.tenant_id AND relation_id=r.relation_id ORDER BY event_number DESC LIMIT 1; number:=COALESCE(number,1);
 INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at) VALUES(c.tenant_id,c.workspace_id,r.source_access_scope_id,rid,'assessed_relation.event.v1',internal_key,h,'in_progress','assessed_relation',r.relation_id,1,now_at,now_at);
 INSERT INTO memoriesql.assessed_relation_events VALUES(c.tenant_id,c.workspace_id,eid,r.relation_id,number,previous,action_value,cmd->>'reason',cmd->'evidence',cmd,c.context_id,c.principal_id,c.user_id,rid,now_at,effective,to_jsonb(c));
 q:=memoriesql.relation_projection_v1(c.tenant_id,'assessed',r.relation_id,now_at);
 result:=jsonb_build_object('contract_version',1,'relation_event_id',eid,'relation_id',r.relation_id,'action',action_value,'event_number',number,'previous_event_id',previous,'recorded_at',memoriesql.relation_packet_time(now_at),'effective_at',memoriesql.relation_packet_time(effective),'idempotency_receipt_id',rid,'head_token',q->'head_token','state',q->'state','support_eligible',q->'support_eligible');
 INSERT INTO memoriesql.outbox_events(tenant_id,workspace_id,access_scope_id,outbox_event_id,idempotency_receipt_id,aggregate_kind,aggregate_id,event_kind,payload,headers,recorded_at,available_at) VALUES(c.tenant_id,c.workspace_id,r.source_access_scope_id,uuidv7(),rid,'assessed_relation',r.relation_id,'assessed_relation.event.v1',result,'{"contract_version":1}',now_at,now_at);
 UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,updated_at=now_at,completed_at=now_at WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
 RETURN result||'{"replayed":false}';
EXCEPTION WHEN insufficient_privilege THEN RETURN '{"contract_version":1,"outcome":"refused","error":"unavailable"}';
 WHEN query_canceled OR lock_not_available OR program_limit_exceeded THEN RETURN '{"contract_version":1,"outcome":"refused","error":"budget_exhausted"}';
 WHEN invalid_parameter_value OR unique_violation OR serialization_failure OR object_not_in_prerequisite_state THEN
 GET STACKED DIAGNOSTICS err=MESSAGE_TEXT;
 IF err NOT IN('invalid_request','schema_mismatch','idempotency_conflict','head_conflict','transition_invalid','reassessment_required','receipt_incomplete') THEN RAISE; END IF;
 RETURN jsonb_build_object('contract_version',1,'outcome','refused','error',err);
 WHEN invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation THEN RETURN '{"contract_version":1,"outcome":"refused","error":"invalid_request"}';
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_assessed_relation_event_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_assessed_relation_event_v1(jsonb) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.derived_from_targets(t uuid,bead uuid,known timestamptz) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT DISTINCT a.target_bead_id FROM memoriesql.relation_assertions_v1(t,known) a JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=a.relation_type_revision_id
 WHERE a.source_bead_id=bead AND a.target_bead_id<>bead AND tr.relation_type_id='30000000-0000-4000-8000-000000000009'::uuid AND (memoriesql.relation_projection_v1(t,a.kind,a.relation_id,known)->>'support_eligible')::boolean ORDER BY 1
$$;

CREATE FUNCTION memoriesql.derivation_bead_authorize_v1(t uuid,id uuid,known timestamptz) RETURNS void
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
 PERFORM memoriesql.revisiting_source_authorize(source);
END $$;
REVOKE ALL ON FUNCTION memoriesql.derivation_bead_authorize_v1(uuid,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.qualified_bead_roots_v1(t uuid,bead uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE lineage uuid[]:='{}'; pending uuid[]:=ARRAY[bead]; current_bead uuid; next_bead uuid; roots uuid[]; a record; q jsonb; gap text; gaps jsonb:='[]'; deps jsonb:='[]'; chain uuid[]; replaced record; replacement_id uuid; covered boolean; status text:='qualified'; data jsonb;
BEGIN
 WHILE cardinality(pending)>0 LOOP
  SELECT x INTO current_bead FROM unnest(pending) x ORDER BY x LIMIT 1;
  pending:=array_remove(pending,current_bead);
  IF current_bead=ANY(lineage) THEN CONTINUE; END IF;
  IF cardinality(lineage)>=128 THEN RAISE EXCEPTION 'root_budget' USING ERRCODE='54000'; END IF;
  lineage:=array_append(lineage,current_bead);
  PERFORM memoriesql.derivation_bead_authorize_v1(t,current_bead,known);
  deps:=deps||memoriesql.relation_bead_records_v1(t,current_bead,known);
  FOR next_bead IN SELECT * FROM memoriesql.derived_from_targets(t,current_bead,known) LOOP
   IF NOT next_bead=ANY(lineage) THEN pending:=array_append(pending,next_bead); END IF;
  END LOOP;
  FOR a IN SELECT ar.* FROM memoriesql.relation_assertions_v1(t,known) ar JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=ar.relation_type_revision_id WHERE ar.source_bead_id=current_bead AND ar.acceptance='accepted' AND tr.relation_type_id='30000000-0000-4000-8000-000000000009'::uuid ORDER BY ar.kind,ar.relation_id LOOP
   IF NOT memoriesql.relation_closure_authorized_v1(t,a.kind,a.relation_id,known) THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
   q:=memoriesql.relation_projection_v1(t,a.kind,a.relation_id,known);
   deps:=deps||memoriesql.relation_assertion_records_v1(t,a.kind,a.relation_id,known);
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
     IF NOT FOUND OR NOT memoriesql.relation_closure_authorized_v1(t,replaced.kind,replacement_id,known) THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
     q:=memoriesql.relation_projection_v1(t,replaced.kind,replacement_id,known);
     deps:=deps||memoriesql.relation_assertion_records_v1(t,replaced.kind,replacement_id,known);
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
 ) SELECT array_agg(DISTINCT e.source_object_id ORDER BY e.source_object_id) INTO roots FROM earliest x JOIN memoriesql.source_events e ON e.tenant_id=t AND e.event_id=x.event_id;
 IF cardinality(roots) IS NULL OR cardinality(roots)=0 THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 FOREACH next_bead IN ARRAY roots LOOP PERFORM memoriesql.revisiting_source_authorize(next_bead); END LOOP;
 SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'relation_id',v->>'reason'),'[]') INTO gaps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(gaps)) a;
 RETURN jsonb_build_object('roots_status',status,'derivation_root_ids',CASE WHEN status='qualified' THEN to_jsonb(roots) ELSE 'null'::jsonb END,'roots_gap_relation_ids',gaps,'dependencies',deps);
END $$;
REVOKE ALL ON FUNCTION memoriesql.qualified_bead_roots_v1(uuid,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.qualified_unit_roots_v1(t uuid,unit uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE b uuid; source uuid; u record; q jsonb; roots jsonb:='[]'; gaps jsonb:='[]'; deps jsonb:='[]'; status text:='qualified'; observing boolean:=false;
BEGIN
 SELECT su.*,ev.source_object_id INTO u FROM memoriesql.source_units su JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id WHERE su.tenant_id=t AND su.source_unit_id=unit AND su.created_at<=known AND ev.recorded_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'memory.query','read') THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.revisiting_source_authorize(u.source_object_id);
 deps:=memoriesql.relation_unit_records_v1(t,unit,known);
 FOR b IN SELECT bead_id FROM memoriesql.beads WHERE tenant_id=t AND source_unit_id=unit AND created_at<=known ORDER BY bead_id LOOP
  observing:=true; q:=memoriesql.qualified_bead_roots_v1(t,b,known);
  IF q->>'roots_status'='unsupported' THEN status:='unsupported'; ELSIF q->>'roots_status'='indeterminate' AND status='qualified' THEN status:='indeterminate'; END IF;
  roots:=roots||COALESCE(NULLIF(q->'derivation_root_ids','null'),'[]'); gaps:=gaps||(q->'roots_gap_relation_ids'); deps:=deps||(q->'dependencies');
 END LOOP;
 IF NOT observing THEN roots:=jsonb_build_array(u.source_object_id); END IF;
 SELECT COALESCE(jsonb_agg(v ORDER BY v),'[]') INTO roots FROM (SELECT DISTINCT value v FROM jsonb_array_elements(roots)) a;
 SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'relation_id',v->>'reason'),'[]') INTO gaps FROM (SELECT DISTINCT value v FROM jsonb_array_elements(gaps)) a;
 IF status='qualified' AND jsonb_array_length(roots)=0 THEN RAISE EXCEPTION 'root_unavailable' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('roots_status',status,'derivation_root_ids',CASE WHEN status='qualified' THEN roots ELSE 'null'::jsonb END,'roots_gap_relation_ids',gaps,'dependencies',deps);
END $$;
REVOKE ALL ON FUNCTION memoriesql.qualified_unit_roots_v1(uuid,uuid,timestamptz) FROM PUBLIC;

-- Legacy helpers cannot encode unqualified roots and refuse, never manufacture originals.
CREATE OR REPLACE FUNCTION memoriesql.bead_derivation_roots(t uuid,bead uuid,known timestamptz) RETURNS uuid[]
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE q jsonb;
BEGIN q:=memoriesql.qualified_bead_roots_v1(t,bead,known); IF q->>'roots_status'<>'qualified' THEN RAISE EXCEPTION 'roots_unqualified' USING ERRCODE='42501'; END IF; RETURN ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(q->'derivation_root_ids')); END $$;
CREATE OR REPLACE FUNCTION memoriesql.source_unit_derivation_roots(t uuid,unit uuid,known timestamptz) RETURNS uuid[]
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE q jsonb;
BEGIN q:=memoriesql.qualified_unit_roots_v1(t,unit,known); IF q->>'roots_status'<>'qualified' THEN RAISE EXCEPTION 'roots_unqualified' USING ERRCODE='42501'; END IF; RETURN ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(q->'derivation_root_ids')); END $$;

CREATE FUNCTION memoriesql.relation_evidence_view_v3(t uuid,owner_kind_value text,owner uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE link record; q jsonb; source uuid; items jsonb:='[]'; sources jsonb:='[]'; deps jsonb:='[]';
BEGIN
 FOR link IN SELECT l.statement_id,l.evidence_source_unit_id,x.evidence_content_hash,x.access_scope_id,x.evidence_event_id FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind=owner_kind_value AND l.owner_id=owner AND l.recorded_at<=known ORDER BY l.statement_id,l.evidence_source_unit_id LOOP
 IF NOT memoriesql.current_context_event_authorized(link.access_scope_id,link.evidence_event_id,'memory.query','read') THEN RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501'; END IF;
 SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id=t AND event_id=link.evidence_event_id; PERFORM memoriesql.revisiting_source_authorize(source);
 sources:=sources||jsonb_build_array(jsonb_build_object('source',source));
 q:=memoriesql.qualified_unit_roots_v1(t,link.evidence_source_unit_id,known); deps:=deps||(q->'dependencies');
 items:=items||jsonb_build_array(jsonb_build_object('statement_id',link.statement_id,'source_unit_id',link.evidence_source_unit_id,'content_sha256',link.evidence_content_hash)|| (q-ARRAY['dependencies']));
 END LOOP;
 RETURN jsonb_build_object('items',items,'sources',sources,'dependencies',deps);
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_evidence_view_v3(uuid,text,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_events_view_v3(t uuid,k text,id uuid,known timestamptz) RETURNS jsonb
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
    SELECT source_object_id INTO src FROM memoriesql.source_events WHERE tenant_id=t AND event_id=link.evidence_event_id; PERFORM memoriesql.revisiting_source_authorize(src);
    pairs:=pairs||jsonb_build_array(jsonb_build_object('statement_id',link.statement_id,'source_unit_id',link.evidence_source_unit_id,'content_hash',link.evidence_content_hash));
   END LOOP;
  END IF;
  item:=to_jsonb(e)||jsonb_build_object('effective_at',memoriesql.relation_packet_time(e.effective_at),'recorded_at',memoriesql.relation_packet_time(e.recorded_at),'evidence',pairs);
  result:=result||jsonb_build_array(item);
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_events_view_v3(uuid,text,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.inspect_bead_relations_v3_base(request jsonb) RETURNS jsonb
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
        RETURN unavailable;
    END IF;
    SELECT e.* INTO ev FROM memoriesql.source_events AS e WHERE e.tenant_id = b.tenant_id AND e.event_id = b.event_id;
    sources := array_append(sources, ev.source_object_id);
    PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
    SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics AS a
    JOIN memoriesql.bead_versions AS bv ON bv.tenant_id = a.tenant_id AND bv.bead_version_id = a.bead_version_id
    WHERE a.tenant_id = b.tenant_id AND a.bead_id = b.bead_id AND bv.authored_at <= known;

    IF v.bead_version_id IS NOT NULL THEN
        IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id, v.workspace_id, v.access_scope_id, v.bead_version_id) THEN
            RETURN unavailable;
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
                    RETURN unavailable;
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
                RETURN unavailable;
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
                    RETURN unavailable;
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
                RETURN unavailable;
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
                RETURN unavailable;
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
                RETURN unavailable;
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
                RETURN unavailable;
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
    IF octet_length(memoriesql.canonical_semantic_json_text(result)) > 524288 THEN RETURN budget; END IF;
    -- Revalidate every disclosed source dependency at the return boundary.
    FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
        PERFORM memoriesql.revisiting_source_authorize(source);
    END LOOP;
    IF pg_catalog.clock_timestamp() - started > interval '2 seconds' THEN RETURN budget; END IF;
    RETURN result;
EXCEPTION
    WHEN insufficient_privilege THEN RETURN unavailable;
    -- A derivation lineage over its limit is reported, never truncated.
    WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.inspect_bead_relations_v3_base(jsonb) FROM PUBLIC;



CREATE FUNCTION memoriesql.relation_inspection_frame_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; response jsonb; row_item jsonb; q jsonb; roots_status text; roots jsonb; rows jsonb:='[]'; deps jsonb:='[]'; evidence jsonb; manifest jsonb; known timestamptz; snapshot_at timestamptz:=statement_timestamp(); d jsonb; r record;
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR NOT(request ?& ARRAY['contract_version','bead_id','known_at']) OR request-ARRAY['contract_version','bead_id','known_at']<>'{}' OR request->'contract_version'<>'3'::jsonb OR COALESCE(request->>'bead_id','')!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' OR jsonb_typeof(request->'known_at') NOT IN('string','null') THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}'; END IF;
 IF request->>'known_at' IS NOT NULL AND request->>'known_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}'; END IF;
 known:=CASE WHEN request->>'known_at' IS NULL THEN snapshot_at ELSE memoriesql.lifecycle_parse_time_v1(request->>'known_at') END;
 IF NOT isfinite(known) OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 OR known>snapshot_at THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}'; END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id) THEN RETURN '{"contract_version":3,"outcome":"unavailable"}'; END IF;
 response:=memoriesql.inspect_bead_relations_v3_base(request||jsonb_build_object('contract_version',2,'known_at',memoriesql.relation_packet_time(known)));
 IF response->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',3,'outcome',response->'outcome'); END IF;
 FOR row_item IN SELECT value FROM jsonb_array_elements(response->'relations') ORDER BY value->>'relation_id' LOOP
  IF NOT memoriesql.relation_closure_authorized_v1(c.tenant_id,row_item->>'kind',(row_item->>'relation_id')::uuid,known) THEN RETURN '{"contract_version":3,"outcome":"unavailable"}'; END IF;
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
 FOR d IN SELECT value FROM jsonb_array_elements(response->'relation_types') LOOP SELECT tr.relation_type_revision_id INTO r FROM memoriesql.relation_type_revisions tr JOIN memoriesql.relation_types ty ON ty.relation_type_id=tr.relation_type_id WHERE ty.type_key=d->>'key' AND tr.revision=(d->>'revision')::integer AND ty.namespace=d->>'namespace' AND (ty.tenant_id IS NULL OR (ty.tenant_id=c.tenant_id AND ty.workspace_id=c.workspace_id)); IF NOT FOUND THEN RETURN '{"contract_version":3,"outcome":"unavailable"}'; END IF; deps:=deps||jsonb_build_array(jsonb_build_object('kind','type_definition','id',r.relation_type_revision_id::text,'row',d)); END LOOP;
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
 IF octet_length(memoriesql.lifecycle_canonical_json_v1(response))>524288 THEN RETURN '{"contract_version":3,"outcome":"budget_exhausted"}'; END IF;
 IF NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN RETURN '{"contract_version":3,"outcome":"unavailable"}'; END IF;
 RETURN jsonb_build_object('response',response,'dependency_records',deps,'dependency_manifest',manifest);
EXCEPTION WHEN insufficient_privilege THEN RETURN '{"contract_version":3,"outcome":"unavailable"}';
 WHEN query_canceled OR lock_not_available OR program_limit_exceeded THEN RETURN '{"contract_version":3,"outcome":"budget_exhausted"}';
 WHEN invalid_parameter_value OR invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation THEN RETURN '{"contract_version":3,"outcome":"refused","error":"invalid_request"}';
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_inspection_frame_v1(jsonb) FROM PUBLIC;
CREATE FUNCTION memoriesql.inspect_bead_relations_v3(request jsonb) RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT COALESCE(frame->'response',frame) FROM (SELECT memoriesql.relation_inspection_frame_v1(request) frame) f
$$;
REVOKE ALL ON FUNCTION memoriesql.inspect_bead_relations_v3(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_bead_relations_v3(jsonb) TO memoriesql_application;


CREATE OR REPLACE FUNCTION memoriesql.relation_cycle_closes_v1(t uuid,fresh jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 WITH RECURSIVE assertions AS MATERIALIZED (
 SELECT a.*,tr.relation_type_id,tr.cycle_policy FROM memoriesql.relation_assertions_v1(t,clock_timestamp()) a JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=a.relation_type_revision_id WHERE a.acceptance='accepted' AND memoriesql.relation_projection_v1(t,a.kind,a.relation_id,clock_timestamp())->>'state' NOT IN('retracted','superseded')
 ), endpoints AS MATERIALIZED (
 SELECT a.kind,a.relation_id,a.relation_type_id,a.cycle_policy,s.endpoint side,s.statement_id FROM assertions a JOIN memoriesql.bead_relation_statements s ON a.kind='authored' AND s.tenant_id=t AND s.relation_id=a.relation_id
 UNION ALL SELECT a.kind,a.relation_id,a.relation_type_id,a.cycle_policy,s.role,s.statement_id FROM assertions a JOIN memoriesql.assessed_relation_statements s ON a.kind='assessed' AND s.tenant_id=t AND s.relation_id=a.relation_id AND s.role IN('source','target')
 ), edges AS MATERIALIZED (
 SELECT src.relation_type_id,src.statement_id from_id,dst.statement_id to_id,src.cycle_policy='forbidden' forbidden FROM endpoints src JOIN endpoints dst ON dst.kind=src.kind AND dst.relation_id=src.relation_id AND dst.side='target' WHERE src.side='source'
 ), starts AS (
 SELECT a.* FROM assertions a JOIN jsonb_array_elements(fresh) f(v) ON f.v->>'kind'=a.kind AND (f.v->>'relation_id')::uuid=a.relation_id
 ), reach(kind,id,type_id,statement_id,forbidden) AS (
 SELECT a.kind,a.relation_id,a.relation_type_id,e.statement_id,a.cycle_policy='forbidden' FROM starts a JOIN endpoints e ON e.kind=a.kind AND e.relation_id=a.relation_id AND e.side='target'
 UNION SELECT r.kind,r.id,r.type_id,e.to_id,r.forbidden OR e.forbidden FROM reach r JOIN edges e ON e.relation_type_id=r.type_id AND e.from_id=r.statement_id
 ) SELECT EXISTS(SELECT 1 FROM reach r JOIN endpoints e ON e.kind=r.kind AND e.relation_id=r.id AND e.side='source' AND e.statement_id=r.statement_id WHERE r.forbidden)
$$;

-- Read fence is session-owned so it precedes, rather than lives inside, the RR snapshot.
-- Adapters release it in finally, including refusals and transport failure.
CREATE FUNCTION memoriesql.acquire_relation_read_fence_v1() RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; key bigint;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF NOT FOUND THEN RAISE EXCEPTION 'read_unavailable' USING ERRCODE='42501'; END IF;
 key:=hashtextextended(c.tenant_id::text||':semantic_outcome_authority:',0);
 PERFORM pg_advisory_lock_shared(key);
 RETURN key;
EXCEPTION WHEN OTHERS THEN IF key IS NOT NULL THEN PERFORM pg_advisory_unlock_shared(key); END IF; RAISE;
END $$;
REVOKE ALL ON FUNCTION memoriesql.acquire_relation_read_fence_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.acquire_relation_read_fence_v1() TO memoriesql_application;

-- v2 consumes v3, stripping only fields its immutable contract cannot represent.
CREATE OR REPLACE FUNCTION memoriesql.inspect_bead_relations_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE result jsonb; rows jsonb:='[]'; r jsonb; evidence jsonb; events jsonb;
BEGIN
 IF request->>'contract_version'<>'2' THEN RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE='22023'; END IF;
 result:=memoriesql.inspect_bead_relations_v3(request||jsonb_build_object('contract_version',3,'known_at',request->'known_at'));
 IF result->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',2,'outcome',CASE WHEN result->>'outcome'='budget_exhausted' THEN 'budget_exhausted' ELSE 'unavailable' END); END IF;
 FOR r IN SELECT value FROM jsonb_array_elements(result->'relations') LOOP
 IF r->>'roots_status'<>'qualified' THEN RETURN '{"contract_version":2,"outcome":"unavailable"}'; END IF;
 SELECT COALESCE(jsonb_agg(value-ARRAY['roots_status','roots_gap_relation_ids']),'[]') INTO evidence FROM jsonb_array_elements(r->'evidence');
 SELECT COALESCE(jsonb_agg(value-ARRAY['event_number','previous_event_id','recorded_by_principal_id','recorded_by_user_id','idempotency_receipt_id','evidence']),'[]') INTO events FROM jsonb_array_elements(r->'events');
 rows:=rows||jsonb_build_array((r-ARRAY['acceptance','acceptance_receipt_id','head_token','support_eligible','support_reason','correction_pending','basis_corrected_by','roots_status'])||jsonb_build_object('evidence',evidence,'events',events));
 END LOOP;
 RETURN (result-'frame')||jsonb_build_object('contract_version',2,'relations',rows,'relation_tasks',(SELECT COALESCE(jsonb_agg(value-'status_known_at'),'[]') FROM jsonb_array_elements(result->'relation_tasks')));
END $$;

CREATE OR REPLACE FUNCTION memoriesql.relation_events_view_v1(t uuid,relation_kind text,relation uuid,known timestamptz) RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT COALESCE(jsonb_agg(value-ARRAY['event_number','previous_event_id','recorded_by_principal_id','recorded_by_user_id','idempotency_receipt_id','evidence'] ORDER BY ord),'[]') FROM jsonb_array_elements(memoriesql.relation_events_view_v3(t,relation_kind,relation,known)) WITH ORDINALITY e(value,ord)
$$;

CREATE OR REPLACE FUNCTION memoriesql.apply_relation_assessment_v1(
    requested_command jsonb, requested_worker_id text, requested_worker_instance_id text,
    requested_at timestamp with time zone
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    x memoriesql.relation_assessments%ROWTYPE;
    rel6 memoriesql.bead_relations%ROWTYPE; prior memoriesql.assessed_relations%ROWTYPE;
    body jsonb := requested_command->'payload';
    command_tenant_id uuid; command_workspace_id uuid; command_access_scope_id uuid;
    command_task_id uuid; command_attempt_id uuid; command_generation bigint;
    command_model_runs text[]; computed_request_hash text; new_receipt_id uuid; root_run text;
    outcome_status text; response jsonb; item jsonb; p jsonb; j jsonb; contributor jsonb;
    type_revision uuid; type_symmetric boolean; agreed boolean; forbidden uuid; named uuid[]; lock_id uuid;
    pinned_ids text[]; fresh jsonb := '[]'; relation_ids uuid[] := ARRAY[]::uuid[];
    accepted_ids uuid[] := ARRAY[]::uuid[]; statement uuid; unit uuid;
    uuid_pattern constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    database_now timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR octet_length(requested_command::text) > 1048576
       OR requested_command - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'tenant_id',
            'workspace_id', 'access_scope_id', 'task_id', 'attempt_id', 'lease_generation', 'task_kind',
            'contract_revision', 'output_contract_hash', 'semantic_result_hash', 'semantic_payload_canonical_json',
            'used_evidence_refs', 'model_run_refs', 'payload'] <> '{}'::jsonb
       OR requested_command->>'contract_version' IS DISTINCT FROM '1'
       OR requested_command->>'expected_schema_version' IS DISTINCT FROM '29'
       OR requested_command->>'task_kind' IS DISTINCT FROM 'memory.semantic.assess-relations'
       OR requested_command->>'contract_revision' IS DISTINCT FROM '1'
       OR requested_command->>'output_contract_hash' IS DISTINCT FROM '7b069836b3c71657aa5ade422e3310b4a5d203d15ebfb32bbdee2054889647b5'
       OR COALESCE(requested_command->>'semantic_result_hash', '') !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(requested_command->'semantic_payload_canonical_json') IS DISTINCT FROM 'string'
       OR octet_length(requested_command->>'semantic_payload_canonical_json') NOT BETWEEN 2 AND 131072
       OR requested_command->>'semantic_payload_canonical_json' IS DISTINCT FROM memoriesql.canonical_semantic_json_text(body)
       OR encode(sha256(convert_to(requested_command->>'semantic_payload_canonical_json', 'UTF8')), 'hex')
            IS DISTINCT FROM requested_command->>'semantic_result_hash'
       OR jsonb_typeof(body) IS DISTINCT FROM 'object'
       OR body - ARRAY['proposals', 'dispositions', 'specialist_contributions'] <> '{}'::jsonb
       OR jsonb_typeof(body->'proposals') IS DISTINCT FROM 'array' OR jsonb_array_length(body->'proposals') > 16
       OR jsonb_typeof(body->'dispositions') IS DISTINCT FROM 'array'
       OR jsonb_array_length(body->'dispositions') NOT BETWEEN 1 AND 45
       OR jsonb_typeof(body->'specialist_contributions') IS DISTINCT FROM 'array'
       OR jsonb_array_length(body->'specialist_contributions') > 16
       OR jsonb_typeof(requested_command->'used_evidence_refs') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command->'used_evidence_refs') NOT BETWEEN 1 AND 72
       OR jsonb_typeof(requested_command->'model_run_refs') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command->'model_run_refs') NOT BETWEEN 1 AND 17
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(requested_command->'model_run_refs') AS r(v)
                  WHERE jsonb_typeof(r.v) IS DISTINCT FROM 'string')
       OR EXISTS (SELECT 1 FROM unnest(ARRAY['tenant_id', 'workspace_id', 'access_scope_id', 'task_id', 'attempt_id']) AS k(key)
                  WHERE COALESCE(requested_command->>k.key, '') !~ uuid_pattern)
       OR COALESCE(requested_command->>'lease_generation', '') !~ '^[1-9][0-9]{0,17}$'
       OR btrim(COALESCE(requested_command->>'idempotency_key', '')) = ''
       OR length(requested_command->>'idempotency_key') > 512
       OR btrim(requested_worker_id) = '' OR btrim(requested_worker_instance_id) = ''
       OR requested_at IS NULL OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'relation assessment command is invalid' USING ERRCODE = '22023';
    END IF;
    command_tenant_id := (requested_command->>'tenant_id')::uuid;
    command_workspace_id := (requested_command->>'workspace_id')::uuid;
    command_access_scope_id := (requested_command->>'access_scope_id')::uuid;
    command_task_id := (requested_command->>'task_id')::uuid;
    command_attempt_id := (requested_command->>'attempt_id')::uuid;
    command_generation := (requested_command->>'lease_generation')::bigint;
    command_model_runs := ARRAY(SELECT r.v FROM jsonb_array_elements_text(requested_command->'model_run_refs') AS r(v));
    computed_request_hash := encode(sha256(convert_to(requested_command::text, 'UTF8')), 'hex');

    SELECT * INTO context_record FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR context_record.principal_kind <> 'service'
       OR context_record.tenant_id <> command_tenant_id OR context_record.workspace_id <> command_workspace_id
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(command_access_scope_id, 'memory.maintain', 'write') THEN
        RAISE EXCEPTION 'relation assessment command is outside authorization' USING ERRCODE = '42501';
    END IF;
    SELECT q.* INTO task_record FROM memoriesql.semantic_tasks AS q
    JOIN memoriesql.semantic_task_attempts AS a
      ON a.tenant_id = q.tenant_id AND a.task_id = q.task_id AND a.attempt_id = command_attempt_id
     AND a.lease_generation = command_generation AND a.claimant_principal_id = context_record.principal_id
     AND a.worker_id = requested_worker_id AND a.worker_instance_id = requested_worker_instance_id
    WHERE q.tenant_id = command_tenant_id AND q.workspace_id = command_workspace_id
      AND q.access_scope_id = command_access_scope_id AND q.task_id = command_task_id
      AND q.target_kind = 'canonical_semantics' AND q.task_kind = 'memory.semantic.assess-relations'
      AND q.contract_revision = 1;
    IF NOT FOUND
       OR NOT memoriesql.current_context_semantic_task_authorized(command_task_id, 'memory.maintain', 'write')
       OR NOT memoriesql.semantic_task_origin_authorized(command_tenant_id, command_task_id, database_now) THEN
        RAISE EXCEPTION 'relation assessment task is unavailable' USING ERRCODE = '42501';
    END IF;

    PERFORM pg_advisory_xact_lock_shared(hashtextextended(command_tenant_id::text||':semantic_outcome_authority:',0));
    PERFORM pg_advisory_xact_lock(hashtextextended(command_tenant_id::text||':relation-lineage:',0));
    database_now:=clock_timestamp();
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        command_tenant_id::text || ':relation_assessment.apply.v1:' || (requested_command->>'idempotency_key'), 0));
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(command_access_scope_id, 'memory.maintain', 'write')
       OR NOT memoriesql.current_context_scope_time_authorized(command_access_scope_id, 'write', database_now)
       OR NOT memoriesql.current_context_semantic_task_authorized(command_task_id, 'memory.maintain', 'write')
       OR NOT memoriesql.semantic_task_origin_authorized(command_tenant_id, command_task_id, database_now) THEN
        RAISE EXCEPTION 'relation assessment command is outside authorization' USING ERRCODE = '42501';
    END IF;
    -- Every pinned bead, statement and evidence source stays authorized at apply.
    PERFORM memoriesql.relation_assessment_pins_authorize(command_tenant_id, command_task_id);
    SELECT r.* INTO receipt_record FROM memoriesql.idempotency_receipts AS r
    WHERE r.tenant_id = command_tenant_id AND r.operation_kind = 'relation_assessment.apply.v1'
      AND r.idempotency_key = requested_command->>'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF receipt_record.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF receipt_record.status <> 'succeeded' OR receipt_record.response_receipt IS NULL THEN
            RAISE EXCEPTION 'relation assessment receipt is incomplete' USING ERRCODE = '55000';
        END IF;
        RETURN receipt_record.response_receipt || '{"replayed":true}'::jsonb;
    END IF;

    SELECT q.* INTO task_record FROM memoriesql.semantic_tasks AS q
    WHERE q.tenant_id = command_tenant_id AND q.task_id = command_task_id AND q.status = 'running'
      AND q.target_kind = 'canonical_semantics' AND q.lease_generation = command_generation
      AND q.lease_owner = requested_worker_id AND q.worker_instance_id = requested_worker_instance_id
      AND q.result_attempt_id IS NULL AND q.cancel_requested_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    SELECT a.* INTO attempt_record FROM memoriesql.semantic_task_attempts AS a
    WHERE a.tenant_id = command_tenant_id AND a.task_id = command_task_id AND a.attempt_id = command_attempt_id
      AND a.lease_generation = command_generation AND a.claimant_principal_id = context_record.principal_id
      AND a.worker_id = requested_worker_id AND a.worker_instance_id = requested_worker_instance_id
      AND a.status = 'running'
    FOR UPDATE;
    IF NOT FOUND OR NOT memoriesql.lock_semantic_task_outcome_authority(command_tenant_id, command_task_id) THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now OR attempt_record.deadline_at <= database_now
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_time_authorized(command_access_scope_id, 'write', database_now)
       OR memoriesql.reauthorize_semantic_task(command_tenant_id, command_task_id, command_attempt_id,
            command_generation, requested_worker_id, requested_worker_instance_id, 'outcome', requested_at) <> 'authorized' THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    IF NOT memoriesql.relation_assessment_exposure_valid(command_tenant_id, command_task_id, command_attempt_id, command_generation) THEN
        RAISE EXCEPTION 'relation_assessment_exposure_required' USING ERRCODE = '42501';
    END IF;
    SELECT ra.* INTO x FROM memoriesql.relation_assessments AS ra
    WHERE ra.tenant_id = command_tenant_id AND ra.task_id = command_task_id;
    pinned_ids := ARRAY(SELECT b.v->>'bead_id' FROM jsonb_array_elements(x.beads) AS b(v));

    -- The run tree is exactly the author's root run and one settled specialist run
    -- per contribution, each contribution exactly as its attestor recorded it.
    SELECT r.run_id INTO root_run FROM memoriesql.semantic_task_runs AS r
    WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id AND r.lease_generation = command_generation
      AND r.parent_run_id IS NULL AND r.run_status = 'running' AND r.agent_key = 'memory.semantic.relation-author';
    IF root_run IS NULL OR NOT root_run = ANY(command_model_runs)
       OR cardinality(command_model_runs) <> (SELECT count(DISTINCT r.v) FROM unnest(command_model_runs) AS r(v))
       OR cardinality(command_model_runs) <> (
            SELECT count(*) FROM memoriesql.semantic_task_runs AS r
            WHERE r.tenant_id = command_tenant_id AND r.task_id = command_task_id AND r.attempt_id = command_attempt_id
              AND r.lease_generation = command_generation)
       OR cardinality(command_model_runs) <> (
            SELECT count(*) FROM memoriesql.semantic_task_runs AS r
            WHERE r.tenant_id = command_tenant_id AND r.task_id = command_task_id AND r.attempt_id = command_attempt_id
              AND r.lease_generation = command_generation AND r.run_id = ANY(command_model_runs))
       OR EXISTS (SELECT 1 FROM memoriesql.semantic_task_runs AS r
                  WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id
                    AND r.parent_run_id IS NOT NULL
                    AND (NOT r.settled OR r.run_status <> 'succeeded' OR r.parent_run_id <> root_run
                         OR r.agent_key <> 'memory.semantic.relation-specialist'))
       OR (SELECT count(*) FROM memoriesql.semantic_task_runs AS r
           WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id AND r.parent_run_id IS NOT NULL)
          <> jsonb_array_length(body->'specialist_contributions') THEN
        RAISE EXCEPTION 'relation assessment run tree is incomplete' USING ERRCODE = '22023';
    END IF;
    FOR contributor IN SELECT value FROM jsonb_array_elements(body->'specialist_contributions') LOOP
        -- The specialist judged exactly these proposals, in this order: its attested
        -- packet hash binds them, so no proposal changes after its judgment.
        IF contributor->>'proposals_sha256' IS DISTINCT FROM memoriesql.classification_sha((
               SELECT jsonb_agg(pr.v ORDER BY pr.o) FROM jsonb_array_elements(body->'proposals') WITH ORDINALITY AS pr(v, o)
               WHERE pr.v->>'proposal_id' IN (SELECT jd.v->>'proposal_id'
                                              FROM jsonb_array_elements(contributor#>'{decision,judgments}') AS jd(v))))
           OR contributor->>'author_run_ref' IS DISTINCT FROM root_run OR NOT EXISTS (
            SELECT 1 FROM memoriesql.relation_assessment_deliveries AS d
            JOIN memoriesql.semantic_task_runs AS r ON r.tenant_id = d.tenant_id AND r.attempt_id = d.attempt_id AND r.run_id = d.run_id
            WHERE d.tenant_id = command_tenant_id AND d.task_id = command_task_id AND d.attempt_id = command_attempt_id
              AND d.lease_generation = command_generation AND d.role = 'specialist'
              AND d.request_id::text = contributor->>'request_id' AND d.run_id = contributor->>'model_run_ref'
              AND r.parent_run_id = root_run AND d.contribution = contributor) THEN
            RAISE EXCEPTION 'relation_specialist_contribution_unrecorded' USING ERRCODE = '22023';
        END IF;
    END LOOP;
    -- Every proposal is judged exactly once across the contributions, and only proposals are.
    IF (SELECT COALESCE(jsonb_agg(jd.v->>'proposal_id' ORDER BY jd.v->>'proposal_id'), '[]'::jsonb)
        FROM jsonb_array_elements(body->'specialist_contributions') AS sc(v),
             jsonb_array_elements(sc.v#>'{decision,judgments}') AS jd(v))
       IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(pr.v->>'proposal_id' ORDER BY pr.v->>'proposal_id'), '[]'::jsonb)
                         FROM jsonb_array_elements(body->'proposals') AS pr(v))
       OR (SELECT count(*) <> count(DISTINCT pr.v->>'proposal_id') FROM jsonb_array_elements(body->'proposals') AS pr(v)) THEN
        RAISE EXCEPTION 'relation_specialist_coverage_incomplete' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(requested_command->'used_evidence_refs') AS u(v)
               WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.evidence_units) AS e(v)
                                 WHERE e.v->>'source_unit_id' = u.v))
       OR (SELECT count(*) <> count(DISTINCT u.v) FROM jsonb_array_elements_text(requested_command->'used_evidence_refs') AS u(v)) THEN
        RAISE EXCEPTION 'relation assessment evidence is outside its pins' USING ERRCODE = '22023';
    END IF;

    -- Every unordered pinned pair, each bead with itself, has exactly one disposition.
    IF (SELECT jsonb_agg(jsonb_build_array(f.id, s.id) ORDER BY f.id, s.id)
        FROM unnest(pinned_ids::uuid[]) AS f(id) JOIN unnest(pinned_ids::uuid[]) AS s(id) ON f.id <= s.id)
       IS DISTINCT FROM (
        SELECT jsonb_agg(jsonb_build_array((d.v->>'first_bead_id')::uuid, (d.v->>'second_bead_id')::uuid)
                         ORDER BY (d.v->>'first_bead_id')::uuid, (d.v->>'second_bead_id')::uuid)
        FROM jsonb_array_elements(body->'dispositions') AS d(v)
        WHERE COALESCE(d.v->>'first_bead_id', '') ~ uuid_pattern AND COALESCE(d.v->>'second_bead_id', '') ~ uuid_pattern)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(body->'dispositions') AS d(v)
                  WHERE jsonb_typeof(d.v) IS DISTINCT FROM 'object'
                     OR d.v - ARRAY['first_bead_id', 'second_bead_id', 'disposition', 'abstention', 'reason'] <> '{}'::jsonb
                     OR COALESCE(d.v->>'disposition', '') NOT IN ('related', 'not_related', 'abstained', 'not_assessed')
                     OR (d.v->>'disposition' = 'abstained') IS DISTINCT FROM
                        COALESCE(d.v->>'abstention' IN ('no_fit', 'insufficient_evidence', 'ambiguous'), false)
                     OR (d.v->>'disposition' <> 'abstained' AND d.v->'abstention' IS DISTINCT FROM 'null'::jsonb)
                     OR jsonb_typeof(d.v->'reason') NOT IN ('string', 'null')
                     OR (jsonb_typeof(d.v->'reason') = 'string' AND (btrim(d.v->>'reason') = '' OR char_length(d.v->>'reason') > 1024))
                     OR (d.v->>'disposition' = 'not_assessed' AND jsonb_typeof(d.v->'reason') IS DISTINCT FROM 'string')
                     OR (d.v->>'disposition' = 'related') IS DISTINCT FROM EXISTS (
                        SELECT 1 FROM jsonb_array_elements(body->'proposals') AS pr(v)
                        WHERE LEAST((pr.v#>>'{source,bead_id}')::uuid, (pr.v#>>'{target,bead_id}')::uuid) = (d.v->>'first_bead_id')::uuid
                          AND GREATEST((pr.v#>>'{source,bead_id}')::uuid, (pr.v#>>'{target,bead_id}')::uuid) = (d.v->>'second_bead_id')::uuid)) THEN
        RAISE EXCEPTION 'relation_pair_coverage_invalid' USING ERRCODE = '22023';
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at)
    VALUES (command_tenant_id, command_workspace_id, command_access_scope_id, pg_catalog.uuidv7(),
        'relation_assessment.apply.v1', requested_command->>'idempotency_key', computed_request_hash, 'in_progress',
        'semantic_task', command_task_id, 1, database_now, database_now)
    RETURNING idempotency_receipts.idempotency_receipt_id INTO new_receipt_id;

    -- Cycle-forbidden keys are serialized per tenant and key before any write, in a
    -- fixed order, with the same lock revision-6 authorship takes.
    FOR forbidden IN
        SELECT DISTINCT r.relation_type_id FROM jsonb_array_elements(body->'proposals') AS pr(v)
        JOIN jsonb_array_elements(x.relation_vocabulary) AS d(v)
          ON d.v->>'key' = pr.v#>>'{relation_type,key}' AND d.v->'revision' = pr.v#>'{relation_type,revision}'
        JOIN memoriesql.relation_types AS ty ON ty.type_key = d.v->>'key'
         AND (ty.tenant_id IS NULL OR (ty.tenant_id = command_tenant_id AND ty.workspace_id = command_workspace_id))
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
         AND r.revision = (d.v->>'revision')::integer
        ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            command_tenant_id::text || ':relation-cycle:' || forbidden::text, 0));
    END LOOP;

    FOR lock_id IN SELECT DISTINCT (v#>>'{retires,relation_id}')::uuid FROM jsonb_array_elements(body->'proposals') x(v) WHERE jsonb_typeof(v->'retires')='object' ORDER BY 1 LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(command_tenant_id::text||':bead-relation:'||lock_id::text,0));
    END LOOP;
    FOR p IN SELECT value FROM jsonb_array_elements(body->'proposals') ORDER BY value->>'proposal_id' LOOP
        IF jsonb_typeof(p) IS DISTINCT FROM 'object'
           OR p - ARRAY['proposal_id', 'relation_type', 'source', 'target', 'basis_statements', 'evidence', 'basis',
                        'qualification', 'rationale', 'author_confidence', 'retires'] <> '{}'::jsonb
           OR COALESCE(p->>'proposal_id', '') !~ uuid_pattern
           OR jsonb_typeof(p->'relation_type') IS DISTINCT FROM 'object'
           OR (p->'relation_type') - ARRAY['key', 'revision'] <> '{}'::jsonb
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.relation_vocabulary) AS d(v)
                          WHERE d.v->>'key' = p#>>'{relation_type,key}' AND d.v->'revision' = p#>'{relation_type,revision}')
           OR jsonb_typeof(p->'source') IS DISTINCT FROM 'object' OR jsonb_typeof(p->'target') IS DISTINCT FROM 'object'
           OR (p->'source') - ARRAY['bead_id', 'bead_version_id', 'statement_ids'] <> '{}'::jsonb
           OR (p->'target') - ARRAY['bead_id', 'bead_version_id', 'statement_ids'] <> '{}'::jsonb
           OR NOT memoriesql.relation_pinned_statements_v1(x.beads, p#>>'{source,bead_id}', p#>>'{source,bead_version_id}', p#>'{source,statement_ids}')
           OR NOT memoriesql.relation_pinned_statements_v1(x.beads, p#>>'{target,bead_id}', p#>>'{target,bead_version_id}', p#>'{target,statement_ids}')
           OR jsonb_typeof(p->'basis_statements') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'basis_statements') > 8
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(p->'basis_statements') AS b(v)
                      WHERE jsonb_typeof(b.v) IS DISTINCT FROM 'object'
                         OR b.v - ARRAY['bead_id', 'bead_version_id', 'statement_id'] <> '{}'::jsonb
                         OR jsonb_typeof(b.v->'statement_id') IS DISTINCT FROM 'string'
                         OR NOT memoriesql.relation_pinned_statements_v1(x.beads, b.v->>'bead_id', b.v->>'bead_version_id',
                                                                         jsonb_build_array(b.v->'statement_id')))
           OR jsonb_typeof(p->'evidence') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'evidence') NOT BETWEEN 1 AND 8
           OR COALESCE(p->>'basis', '') NOT IN ('source_stated', 'agent_inferred')
           OR (p->>'basis' = 'source_stated' AND jsonb_array_length(p->'basis_statements') = 0)
           OR jsonb_typeof(p->'rationale') IS DISTINCT FROM 'string' OR btrim(p->>'rationale') = '' OR char_length(p->>'rationale') > 1024
           OR jsonb_typeof(p->'qualification') NOT IN ('string', 'null')
           OR (jsonb_typeof(p->'qualification') = 'string' AND (btrim(p->>'qualification') = '' OR char_length(p->>'qualification') > 1024))
           OR jsonb_typeof(p->'author_confidence') IS DISTINCT FROM 'number'
           OR (p->>'author_confidence')::numeric NOT BETWEEN 0 AND 1
           OR round((p->>'author_confidence')::numeric, 2) <> (p->>'author_confidence')::numeric
           OR jsonb_typeof(p->'retires') NOT IN ('object', 'null') THEN
            RAISE EXCEPTION 'relation_proposal_invalid' USING ERRCODE = '22023';
        END IF;
        -- Endpoint statements differ, and basis statements are separate from both.
        named := ARRAY(SELECT s.i::uuid FROM jsonb_array_elements_text(p#>'{source,statement_ids}') AS s(i))
              || ARRAY(SELECT s.i::uuid FROM jsonb_array_elements_text(p#>'{target,statement_ids}') AS s(i))
              || ARRAY(SELECT (b.v->>'statement_id')::uuid FROM jsonb_array_elements(p->'basis_statements') AS b(v));
        IF cardinality(named) <> (SELECT count(DISTINCT n) FROM unnest(named) AS n) THEN
            RAISE EXCEPTION 'relation_statements_overlap' USING ERRCODE = '22023';
        END IF;
        -- Evidence: exact existing statement-evidence pairs of the proposal's own statements.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(p->'evidence') AS e(v)
                   WHERE jsonb_typeof(e.v) IS DISTINCT FROM 'object'
                      OR e.v - ARRAY['statement_id', 'source_unit_id', 'content_hash'] <> '{}'::jsonb
                      OR COALESCE(e.v->>'statement_id', '') !~ uuid_pattern
                      OR COALESCE(e.v->>'source_unit_id', '') !~ uuid_pattern
                      OR COALESCE(e.v->>'content_hash', '') !~ '^[a-f0-9]{64}$'
                      OR NOT (e.v->>'statement_id')::uuid = ANY(named)
                      OR NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS ev
                                     WHERE ev.tenant_id = command_tenant_id AND ev.statement_id = (e.v->>'statement_id')::uuid
                                       AND ev.evidence_source_unit_id = (e.v->>'source_unit_id')::uuid
                                       AND ev.evidence_content_hash = e.v->>'content_hash'))
           OR (SELECT count(*) <> count(DISTINCT (e.v->>'statement_id', e.v->>'source_unit_id'))
               FROM jsonb_array_elements(p->'evidence') AS e(v)) THEN
            RAISE EXCEPTION 'relation_evidence_unbound' USING ERRCODE = '22023';
        END IF;
        IF EXISTS (SELECT 1 FROM memoriesql.assessed_relations AS r
                   WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p->>'proposal_id')::uuid)
           OR EXISTS (SELECT 1 FROM memoriesql.bead_relations AS r
                      WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p->>'proposal_id')::uuid) THEN
            RAISE EXCEPTION 'relation_identifier_conflict' USING ERRCODE = '22023';
        END IF;
        SELECT r.relation_type_revision_id, r.is_symmetric INTO type_revision, type_symmetric
        FROM memoriesql.relation_types AS ty
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
        WHERE ty.type_key = p#>>'{relation_type,key}' AND r.revision = (p#>>'{relation_type,revision}')::integer
          AND (ty.tenant_id IS NULL OR (ty.tenant_id = command_tenant_id AND ty.workspace_id = command_workspace_id));
        IF type_revision IS NULL THEN
            RAISE EXCEPTION 'unknown_relation_type' USING ERRCODE = '22023';
        END IF;
        -- A symmetric assertion read in either direction is the same assertion.
        IF type_symmetric AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(body->'proposals') AS o(v)
            WHERE o.v->>'proposal_id' <> p->>'proposal_id' AND o.v->'relation_type' = p->'relation_type'
              AND o.v#>>'{source,bead_id}' = p#>>'{target,bead_id}' AND o.v#>>'{target,bead_id}' = p#>>'{source,bead_id}'
              AND (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(o.v#>'{source,statement_ids}') AS s(i))
                = (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(p#>'{target,statement_ids}') AS s(i))
              AND (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(o.v#>'{target,statement_ids}') AS s(i))
                = (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(p#>'{source,statement_ids}') AS s(i))) THEN
            RAISE EXCEPTION 'relation_symmetric_duplicate' USING ERRCODE = '22023';
        END IF;
        SELECT sc.v, jd.v INTO contributor, j
        FROM jsonb_array_elements(body->'specialist_contributions') AS sc(v),
             jsonb_array_elements(sc.v#>'{decision,judgments}') AS jd(v)
        WHERE jd.v->>'proposal_id' = p->>'proposal_id';
        -- Agreement concerns the exact assertion: consistent, and the author's predicate,
        -- revision and direction among those the specialist finds warranted.
        agreed := j->>'outcome' = 'assessed' AND j->'consistent' = 'true'::jsonb AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(j->'warranted') AS w(v)
            WHERE w.v->'relation_type' = p->'relation_type' AND w.v->>'direction' = 'as_proposed');
        IF jsonb_typeof(p->'retires') = 'object' THEN
            IF (p->'retires') - ARRAY['relation_kind', 'relation_id', 'reason'] <> '{}'::jsonb
               OR COALESCE(p#>>'{retires,relation_id}', '') !~ uuid_pattern
               OR jsonb_typeof(p#>'{retires,reason}') IS DISTINCT FROM 'string'
               OR btrim(p#>>'{retires,reason}') = '' OR char_length(p#>>'{retires,reason}') > 1024
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(body->'proposals') AS o(v)
                          WHERE o.v->>'proposal_id' <> p->>'proposal_id' AND o.v->'retires' = p->'retires')
               OR EXISTS (SELECT 1 FROM memoriesql.relation_retirements AS rr
                          WHERE rr.tenant_id = command_tenant_id AND rr.retired_kind = p#>>'{retires,relation_kind}'
                            AND rr.retired_relation_id = (p#>>'{retires,relation_id}')::uuid) THEN
                RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
            END IF;
            -- Only an earlier active assertion among the pinned beads can be retired.
            IF p#>>'{retires,relation_kind}' = 'authored' THEN
                SELECT r.* INTO rel6 FROM memoriesql.bead_relations AS r
                WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p#>>'{retires,relation_id}')::uuid;
                IF rel6.relation_id IS NULL OR rel6.workspace_id <> command_workspace_id
                   OR NOT rel6.source_bead_id::text = ANY(pinned_ids) OR NOT rel6.target_bead_id::text = ANY(pinned_ids)
                   OR memoriesql.bead_relation_state_v1(command_tenant_id, rel6.relation_id, database_now)->>'state'
                      IN ('retracted', 'superseded') THEN
                    RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
                END IF;
            ELSIF p#>>'{retires,relation_kind}' = 'assessed' THEN
                SELECT r.* INTO prior FROM memoriesql.assessed_relations AS r
                WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p#>>'{retires,relation_id}')::uuid;
                IF prior.relation_id IS NULL OR prior.workspace_id <> command_workspace_id
                   OR NOT prior.source_bead_id::text = ANY(pinned_ids) OR NOT prior.target_bead_id::text = ANY(pinned_ids)
                   OR memoriesql.assessed_relation_state_v1(command_tenant_id, prior.relation_id, database_now)->>'state'
                      IN ('retracted', 'superseded', 'not_accepted') THEN
                    RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
                END IF;
            ELSE
                RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
            END IF;
        END IF;

        INSERT INTO memoriesql.assessed_relations (tenant_id, workspace_id, relation_id, task_id, attempt_id, author_run_id,
            specialist_run_id, specialist_request_id, source_access_scope_id, source_bead_id, source_bead_version_id,
            target_access_scope_id, target_bead_id, target_bead_version_id, relation_type_revision_id, basis,
            rationale_text, qualification_text, author_confidence, proposal, judgment, acceptance,
            applied_by_principal_id, recorded_at)
        SELECT command_tenant_id, command_workspace_id, (p->>'proposal_id')::uuid, command_task_id, command_attempt_id, root_run,
            contributor->>'model_run_ref', (contributor->>'request_id')::uuid,
            src.access_scope_id, src.bead_id, src.bead_version_id, dst.access_scope_id, dst.bead_id, dst.bead_version_id,
            type_revision, p->>'basis', p->>'rationale', NULLIF(p->'qualification', 'null'::jsonb) #>> '{}',
            (p->>'author_confidence')::numeric, p, j, CASE WHEN agreed THEN 'accepted' ELSE 'not_accepted' END,
            context_record.principal_id, database_now
        FROM memoriesql.accepted_bead_semantics AS src, memoriesql.accepted_bead_semantics AS dst
        WHERE src.tenant_id = command_tenant_id AND src.bead_id = (p#>>'{source,bead_id}')::uuid
          AND src.bead_version_id = (p#>>'{source,bead_version_id}')::uuid
          AND dst.tenant_id = command_tenant_id AND dst.bead_id = (p#>>'{target,bead_id}')::uuid
          AND dst.bead_version_id = (p#>>'{target,bead_version_id}')::uuid;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'relation_endpoint_unavailable' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.assessed_relation_statements (tenant_id, relation_id, role, workspace_id, access_scope_id,
            bead_id, bead_version_id, statement_id)
        SELECT command_tenant_id, (p->>'proposal_id')::uuid, n.role, st.workspace_id, st.access_scope_id,
               st.bead_id, st.bead_version_id, st.statement_id
        FROM (SELECT 'source' AS role, s.i::uuid AS id FROM jsonb_array_elements_text(p#>'{source,statement_ids}') AS s(i)
              UNION ALL SELECT 'target', s.i::uuid FROM jsonb_array_elements_text(p#>'{target,statement_ids}') AS s(i)
              UNION ALL SELECT 'basis', (b.v->>'statement_id')::uuid FROM jsonb_array_elements(p->'basis_statements') AS b(v)) AS n
        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = command_tenant_id AND st.statement_id = n.id;
        FOR statement, unit IN
            SELECT (e.v->>'statement_id')::uuid, (e.v->>'source_unit_id')::uuid FROM jsonb_array_elements(p->'evidence') AS e(v)
            ORDER BY 1, 2
        LOOP
            PERFORM memoriesql.record_semantic_evidence_link(command_tenant_id, command_workspace_id, 'assessed_relation',
                (p->>'proposal_id')::uuid, 'supports', statement, unit, database_now);
        END LOOP;
        relation_ids := array_append(relation_ids, (p->>'proposal_id')::uuid);
        IF agreed THEN
            accepted_ids := array_append(accepted_ids, (p->>'proposal_id')::uuid);
            fresh := fresh || jsonb_build_array(jsonb_build_object('kind', 'assessed', 'relation_id', p->>'proposal_id'));
            -- Same-write direction correction: the retired assertion stops counting at once.
            IF jsonb_typeof(p->'retires') = 'object' THEN
                INSERT INTO memoriesql.relation_retirements (tenant_id, workspace_id, retirement_id, retired_kind,
                    retired_relation_id, replacement_relation_id, reason_text, task_id, attempt_id, author_run_id,
                    idempotency_receipt_id, recorded_by_principal_id, recorded_at)
                VALUES (command_tenant_id, command_workspace_id, pg_catalog.uuidv7(), p#>>'{retires,relation_kind}',
                    (p#>>'{retires,relation_id}')::uuid, (p->>'proposal_id')::uuid, p#>>'{retires,reason}',
                    command_task_id, command_attempt_id, root_run, new_receipt_id, context_record.principal_id, database_now);
            END IF;
        END IF;
    END LOOP;

    -- No accepted assertion of a cycle-forbidden key closes a cycle among active
    -- assertions of both kinds once this write commits.
    IF memoriesql.relation_cycle_closes_v1(command_tenant_id, fresh) THEN
        RAISE EXCEPTION 'relation_cycle_forbidden' USING ERRCODE = '22023';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(body->'dispositions')
                ORDER BY value->>'first_bead_id', value->>'second_bead_id' LOOP
        INSERT INTO memoriesql.relation_pair_dispositions (tenant_id, workspace_id, task_id, first_access_scope_id,
            first_bead_id, first_bead_version_id, second_access_scope_id, second_bead_id, second_bead_version_id,
            disposition, abstention, reason_text, attempt_id, author_run_id, recorded_at)
        SELECT command_tenant_id, command_workspace_id, command_task_id, f.access_scope_id, f.bead_id, f.bead_version_id,
            s.access_scope_id, s.bead_id, s.bead_version_id, item->>'disposition',
            NULLIF(item->'abstention', 'null'::jsonb) #>> '{}', NULLIF(item->'reason', 'null'::jsonb) #>> '{}',
            command_attempt_id, root_run, database_now
        FROM jsonb_array_elements(x.beads) AS fb(v), jsonb_array_elements(x.beads) AS sb(v),
             memoriesql.accepted_bead_semantics AS f, memoriesql.accepted_bead_semantics AS s
        WHERE fb.v->>'bead_id' = item->>'first_bead_id' AND sb.v->>'bead_id' = item->>'second_bead_id'
          AND f.tenant_id = command_tenant_id AND f.bead_id = (fb.v->>'bead_id')::uuid
          AND f.bead_version_id = (fb.v->>'bead_version_id')::uuid
          AND s.tenant_id = command_tenant_id AND s.bead_id = (sb.v->>'bead_id')::uuid
          AND s.bead_version_id = (sb.v->>'bead_version_id')::uuid;
    END LOOP;

    outcome_status := memoriesql.record_semantic_task_outcome(
        command_tenant_id, command_task_id, command_attempt_id, command_generation, requested_worker_id,
        requested_worker_instance_id, 'succeeded', requested_command->>'semantic_result_hash',
        'semantic.application.' || command_attempt_id::text, NULL, NULL, NULL, NULL, 0, requested_at);
    IF outcome_status <> 'succeeded' THEN
        RAISE EXCEPTION 'semantic task success settlement was rejected: %', outcome_status USING ERRCODE = '40001';
    END IF;
    UPDATE memoriesql.semantic_task_runs AS r SET run_status = 'succeeded', finished_at = database_now, settled = true
    WHERE r.tenant_id = command_tenant_id AND r.task_id = command_task_id AND r.attempt_id = command_attempt_id
      AND r.lease_generation = command_generation AND r.parent_run_id IS NULL AND r.run_status = 'running'
      AND r.run_id = root_run;
    IF EXISTS (SELECT 1 FROM memoriesql.semantic_task_runs AS r
               WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id AND NOT r.settled) THEN
        RAISE EXCEPTION 'relation assessment run tree did not settle' USING ERRCODE = '22023';
    END IF;
    INSERT INTO memoriesql.outbox_events (tenant_id, workspace_id, access_scope_id, outbox_event_id, idempotency_receipt_id,
        aggregate_kind, aggregate_id, event_kind, payload, headers, recorded_at, available_at)
    VALUES (command_tenant_id, command_workspace_id, command_access_scope_id, pg_catalog.uuidv7(), new_receipt_id,
        'semantic_task', command_task_id, 'relation_assessment.apply.v1',
        jsonb_build_object('task_id', command_task_id, 'attempt_id', command_attempt_id,
            'proposal_count', cardinality(relation_ids), 'accepted_count', cardinality(accepted_ids)),
        jsonb_build_object('contract_version', 1), database_now, database_now);
    response := jsonb_build_object('contract_version', 1, 'task_id', command_task_id, 'attempt_id', command_attempt_id,
        'idempotency_receipt_id', new_receipt_id, 'relation_ids', to_jsonb(relation_ids),
        'accepted_relation_ids', to_jsonb(accepted_ids), 'task_status', 'succeeded', 'replayed', false);
    UPDATE memoriesql.idempotency_receipts AS r SET status = 'succeeded', response_receipt = response,
        updated_at = database_now, completed_at = database_now
    WHERE r.tenant_id = command_tenant_id AND r.idempotency_receipt_id = new_receipt_id;
    RETURN response;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.apply_relation_assessment_v1(jsonb, text, text, timestamp with time zone) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.apply_relation_assessment_v1(jsonb, text, text, timestamp with time zone) TO memoriesql_worker;


CREATE OR REPLACE FUNCTION memoriesql.record_relation_event_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    target memoriesql.bead_relations%ROWTYPE;
    replacement memoriesql.bead_relations%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE;
    item jsonb; response jsonb; latest uuid; lock_id uuid;
    rid uuid := pg_catalog.uuidv7();
    eid uuid := pg_catalog.uuidv7();
    action_value text := request->>'action';
    request_hash text;
    started timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'relation_id',
            'action', 'replacement_relation_id', 'reason', 'evidence', 'effective_at', 'expected_last_event_id'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1'
       OR request->>'expected_schema_version' IS DISTINCT FROM '27'
       OR octet_length(request::text) > 16384
       OR COALESCE(char_length(request->>'idempotency_key'), 0) NOT BETWEEN 1 AND 512
       OR COALESCE(action_value, '') NOT IN ('confirm', 'dispute', 'retract', 'supersede')
       OR COALESCE(btrim(request->>'reason'), '') = '' OR char_length(request->>'reason') > 1024
       OR (action_value = 'supersede')
            IS DISTINCT FROM COALESCE(jsonb_typeof(request->'replacement_relation_id') = 'string', false)
       OR request->>'replacement_relation_id' IS NOT DISTINCT FROM request->>'relation_id'
       OR COALESCE(request->>'relation_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR (request ? 'replacement_relation_id' AND jsonb_typeof(request->'replacement_relation_id') NOT IN ('string', 'null'))
       OR (jsonb_typeof(request->'replacement_relation_id') = 'string' AND request->>'replacement_relation_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (request ? 'expected_last_event_id' AND jsonb_typeof(request->'expected_last_event_id') NOT IN ('string', 'null'))
       OR (jsonb_typeof(request->'expected_last_event_id') = 'string' AND request->>'expected_last_event_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (request ? 'effective_at' AND jsonb_typeof(request->'effective_at') NOT IN ('string', 'null'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(request->'evidence') = 'array'
                                                          THEN request->'evidence' ELSE '[]'::jsonb END) AS x(v)
                  WHERE jsonb_typeof(v) IS DISTINCT FROM 'object' OR COALESCE(v->>'statement_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                     OR COALESCE(v->>'source_unit_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR COALESCE(jsonb_typeof(request->'evidence'), '') <> 'array'
       OR (CASE WHEN jsonb_typeof(request->'evidence')='array' THEN jsonb_array_length(request->'evidence') ELSE -1 END) > 8 THEN
        RAISE EXCEPTION 'invalid_relation_lifecycle_event' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR c.principal_kind <> 'human' THEN
        RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(c.tenant_id::text || ':semantic_outcome_authority:', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':relation-lineage:',0));
    SELECT * INTO target FROM memoriesql.bead_relations
    WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id AND relation_id = (request->>'relation_id')::uuid;
    IF NOT FOUND
       OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.source_access_scope_id, target.source_bead_version_id)
       OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.target_access_scope_id, target.target_bead_version_id) THEN
        RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    IF request->>'replacement_relation_id' IS NOT NULL THEN
        SELECT * INTO replacement FROM memoriesql.bead_relations
        WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id
          AND relation_id = (request->>'replacement_relation_id')::uuid;
        IF NOT FOUND
           OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, replacement.source_access_scope_id, replacement.source_bead_version_id)
           OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, replacement.target_access_scope_id, replacement.target_bead_version_id) THEN
            RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
        END IF;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
        IF NOT memoriesql.lifecycle_evidence_authorized(c.tenant_id, c.workspace_id, item) THEN
            RAISE EXCEPTION 'relation_lifecycle_evidence_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
    IF (SELECT count(*) <> count(DISTINCT value->>'statement_id') FROM jsonb_array_elements(request->'evidence')) THEN
        RAISE EXCEPTION 'invalid_relation_lifecycle_event' USING ERRCODE = '22023';
    END IF;

    IF NOT memoriesql.relation_closure_authorized_v1(c.tenant_id,'authored',target.relation_id,clock_timestamp(),true) OR (replacement.relation_id IS NOT NULL AND NOT memoriesql.relation_closure_authorized_v1(c.tenant_id,'authored',replacement.relation_id,clock_timestamp(),true)) THEN RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE='42501'; END IF;
    request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(request::text, 'UTF8')), 'hex');
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':bead_relation.event.v1:' || (request->>'idempotency_key'), 0));
    SELECT * INTO old FROM memoriesql.idempotency_receipts
    WHERE tenant_id = c.tenant_id AND operation_kind = 'bead_relation.event.v1'
      AND idempotency_key = request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash <> request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF old.status <> 'succeeded' OR old.response_receipt IS NULL THEN
            RAISE EXCEPTION 'relation lifecycle receipt is incomplete' USING ERRCODE = '55000';
        END IF;
        RETURN old.response_receipt || '{"replayed":true}'::jsonb;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':relation-cycle:'||(SELECT relation_type_id::text FROM memoriesql.relation_type_revisions WHERE relation_type_revision_id=target.relation_type_revision_id),0));
    FOR lock_id IN SELECT DISTINCT x FROM unnest(ARRAY[target.relation_id, replacement.relation_id]) AS x
                  WHERE x IS NOT NULL ORDER BY x LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            c.tenant_id::text || ':bead-relation:' || lock_id::text, 0));
    END LOOP;
    IF NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.source_access_scope_id, target.source_bead_version_id)
       OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.target_access_scope_id, target.target_bead_version_id) THEN
        RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT relation_event_id INTO latest FROM memoriesql.bead_relation_events
    WHERE tenant_id = c.tenant_id AND relation_id = target.relation_id
    ORDER BY recorded_at DESC, relation_event_id DESC LIMIT 1;
    IF latest IS DISTINCT FROM (request->>'expected_last_event_id')::uuid THEN
        RAISE EXCEPTION 'relation_lifecycle_conflict' USING ERRCODE = '40001';
    END IF;
    IF memoriesql.bead_relation_state_v1(c.tenant_id, target.relation_id, pg_catalog.clock_timestamp())->>'state' IN ('retracted','superseded') THEN
        RAISE EXCEPTION 'relation_retracted' USING ERRCODE = '55000';
    END IF;
    IF replacement.relation_id IS NOT NULL AND memoriesql.bead_relation_state_v1(
            c.tenant_id, replacement.relation_id, pg_catalog.clock_timestamp())->>'state' IN ('retracted','superseded') THEN
        RAISE EXCEPTION 'replacement_relation_retracted' USING ERRCODE = '55000';
    END IF;

    IF action_value='confirm' AND (memoriesql.relation_projection_v1(c.tenant_id,'authored',target.relation_id,clock_timestamp())->>'correction_pending')::boolean THEN
        RAISE EXCEPTION 'reassessment_required' USING ERRCODE='55000';
    END IF;
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id, operation_kind,
        idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        c.tenant_id, c.workspace_id, target.source_access_scope_id, rid, 'bead_relation.event.v1',
        request->>'idempotency_key', request_hash, 'in_progress', 'bead_relation', target.relation_id, 1, started, started
    );
    INSERT INTO memoriesql.bead_relation_events (
        tenant_id, workspace_id, relation_event_id, relation_id, action, replacement_relation_id, reason,
        origin, idempotency_receipt_id, recorded_by_principal_id, effective_at, recorded_at
    ) VALUES (
        c.tenant_id, c.workspace_id, eid, target.relation_id, action_value, replacement.relation_id,
        request->>'reason', 'governed', rid, c.principal_id,
        NULLIF(request->>'effective_at', '')::timestamp with time zone, pg_catalog.clock_timestamp()
    );
    FOR item IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
        PERFORM memoriesql.record_semantic_evidence_link(c.tenant_id, c.workspace_id, 'relation_event', eid,
            'supports', (item->>'statement_id')::uuid, (item->>'source_unit_id')::uuid, pg_catalog.clock_timestamp());
    END LOOP;
    response := jsonb_build_object('contract_version', 1, 'relation_event_id', eid,
        'relation_id', target.relation_id, 'action', action_value, 'idempotency_receipt_id', rid);
    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id, idempotency_receipt_id, aggregate_kind,
        aggregate_id, event_kind, payload, headers, recorded_at, available_at
    ) VALUES (
        c.tenant_id, c.workspace_id, target.source_access_scope_id, pg_catalog.uuidv7(), rid, 'bead_relation',
        target.relation_id, 'bead_relation.event.v1', response, '{"contract_version":1}'::jsonb,
        pg_catalog.clock_timestamp(), pg_catalog.clock_timestamp()
    );
    UPDATE memoriesql.idempotency_receipts SET status = 'succeeded', response_receipt = response,
        updated_at = pg_catalog.clock_timestamp(), completed_at = pg_catalog.clock_timestamp()
    WHERE tenant_id = c.tenant_id AND idempotency_receipt_id = rid;
    RETURN response || '{"replayed":false}'::jsonb;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.record_relation_event_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_relation_event_v1(jsonb) TO memoriesql_application;


ALTER FUNCTION memoriesql.apply_semantic_annotations(jsonb,text,text,timestamptz) RENAME TO apply_semantic_annotations_schema29;
REVOKE ALL ON FUNCTION memoriesql.apply_semantic_annotations_schema29(jsonb,text,text,timestamptz) FROM PUBLIC,memoriesql_worker,memoriesql_application;
CREATE FUNCTION memoriesql.apply_semantic_annotations(requested_command jsonb,requested_worker_id text,requested_worker_instance_id text,requested_at timestamptz)
RETURNS TABLE(task_id uuid,attempt_id uuid,idempotency_receipt_id uuid,statement_ids uuid[],bead_version_ids uuid[],task_status text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF NOT FOUND THEN RAISE EXCEPTION 'semantic annotation unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(c.tenant_id::text||':semantic_outcome_authority:',0));
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':relation-lineage:',0));
 RETURN QUERY SELECT * FROM memoriesql.apply_semantic_annotations_schema29(requested_command,requested_worker_id,requested_worker_instance_id,requested_at);
END $$;
REVOKE ALL ON FUNCTION memoriesql.apply_semantic_annotations(jsonb,text,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.apply_semantic_annotations(jsonb,text,text,timestamptz) TO memoriesql_worker;

CREATE TABLE memoriesql.assessed_relation_event_evidence (
 tenant_id uuid NOT NULL,relation_event_id uuid NOT NULL,statement_id uuid NOT NULL,source_unit_id uuid NOT NULL,content_hash text NOT NULL CHECK(content_hash~'^[a-f0-9]{64}$'),
 PRIMARY KEY(tenant_id,relation_event_id,statement_id,source_unit_id),
 FOREIGN KEY(tenant_id,relation_event_id) REFERENCES memoriesql.assessed_relation_events(tenant_id,relation_event_id),
 FOREIGN KEY(tenant_id,statement_id,source_unit_id) REFERENCES memoriesql.bead_semantic_statement_evidence(tenant_id,statement_id,evidence_source_unit_id)
);
ALTER TABLE memoriesql.assessed_relation_event_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.assessed_relation_event_evidence FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.assessed_relation_event_evidence FROM PUBLIC,memoriesql_application;
CREATE TRIGGER assessed_relation_event_evidence_immutable BEFORE UPDATE OR DELETE ON memoriesql.assessed_relation_event_evidence FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE FUNCTION memoriesql.assessed_relation_event_link_evidence_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 INSERT INTO memoriesql.assessed_relation_event_evidence SELECT NEW.tenant_id,NEW.relation_event_id,(v->>'statement_id')::uuid,(v->>'source_unit_id')::uuid,v->>'content_hash' FROM jsonb_array_elements(NEW.evidence) e(v);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.assessed_relation_event_link_evidence_v1() FROM PUBLIC;
CREATE TRIGGER assessed_relation_events_link_evidence AFTER INSERT ON memoriesql.assessed_relation_events FOR EACH ROW EXECUTE FUNCTION memoriesql.assessed_relation_event_link_evidence_v1();
-- Context rows expire; immutable decision_context preserves attribution after cleanup.

-- Direct canonical correction writes also fence lineage; entry-point fencing keeps the lock order.
CREATE FUNCTION memoriesql.fence_relation_lineage_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.tenant_id::text||':semantic_outcome_authority:',0));
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text||':relation-lineage:',0)); RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.fence_relation_lineage_v1() FROM PUBLIC;
CREATE TRIGGER aa_relation_lineage BEFORE INSERT ON memoriesql.bead_supersessions FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_relation_lineage_v1();

ALTER FUNCTION memoriesql.inspect_bead_relations_v1(jsonb) RENAME TO inspect_bead_relations_v1_schema29;
REVOKE ALL ON FUNCTION memoriesql.inspect_bead_relations_v1_schema29(jsonb) FROM PUBLIC,memoriesql_application;
CREATE FUNCTION memoriesql.inspect_bead_relations_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE projected jsonb;
BEGIN
 IF request->>'contract_version'<>'1' THEN RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE='22023'; END IF;
 projected:=memoriesql.inspect_bead_relations_v3(request||jsonb_build_object('contract_version',3,'known_at',request->'known_at'));
 IF projected->>'outcome'<>'available' THEN RETURN jsonb_build_object('contract_version',1,'outcome',CASE WHEN projected->>'outcome'='budget_exhausted' THEN 'budget_exhausted' ELSE 'unavailable' END); END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(projected->'relations') r(v) WHERE v->>'roots_status'<>'qualified' OR v->>'kind'='assessed') THEN RETURN '{"contract_version":1,"outcome":"unavailable"}'; END IF;
 RETURN memoriesql.inspect_bead_relations_v1_schema29(request||jsonb_build_object('known_at',projected->'known_at'));
END $$;
REVOKE ALL ON FUNCTION memoriesql.inspect_bead_relations_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_bead_relations_v1(jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.relation_read_frame_authorized_v1(t uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT current_setting('transaction_isolation')='repeatable read' AND EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted AND mode='ShareLock' AND objsubid=1 AND classid=((hashtextextended(t::text||':semantic_outcome_authority:',0)>>32)&4294967295)::oid AND objid=(hashtextextended(t::text||':semantic_outcome_authority:',0)&4294967295)::oid)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_read_frame_authorized_v1(uuid) FROM PUBLIC;

-- Protected provenance records used by inspection and PR-05's result closure.
-- These helpers are private: every caller must use the authority fence and one RR
-- frame; none grants authority or becomes a second lifecycle interpreter.
CREATE FUNCTION memoriesql.relation_sort_statements_v1(items jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,memoriesql AS $$
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'statement_id',value->>'bead_id',value->>'bead_version_id'),'[]') FROM jsonb_array_elements(items)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_sort_statements_v1(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_unit_records_v1(t uuid,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE u record; result jsonb;
BEGIN
 SELECT su.*,ev.source_object_id,ev.recorded_at,ev.content_hash event_content_hash INTO u FROM memoriesql.source_units su JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id WHERE su.tenant_id=t AND su.source_unit_id=id AND su.created_at<=known AND ev.recorded_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'memory.query','read') THEN RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.revisiting_source_authorize(u.source_object_id);
 result:=jsonb_build_array(
 jsonb_build_object('kind','source_unit','id',id::text,'row',jsonb_build_object('source_unit_id',id,'event_id',u.event_id,'content_sha256',u.content_hash,'created_at',memoriesql.relation_packet_time(u.created_at))),
 jsonb_build_object('kind','source_event','id',u.event_id::text,'row',jsonb_build_object('event_id',u.event_id,'source_object_id',u.source_object_id,'content_sha256',u.event_content_hash,'recorded_at',memoriesql.relation_packet_time(u.recorded_at))),
 jsonb_build_object('kind','source_object','id',u.source_object_id::text,'row',jsonb_build_object('source_object_id',u.source_object_id)));
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_unit_records_v1(uuid,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_bead_records_v1(t uuid,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE b memoriesql.beads%ROWTYPE; v record; st record; e record; result jsonb:='[]'; receipt uuid;
BEGIN
 PERFORM memoriesql.derivation_bead_authorize_v1(t,id,known);
 SELECT * INTO b FROM memoriesql.beads WHERE tenant_id=t AND bead_id=id;
 result:=jsonb_build_array(jsonb_build_object('kind','bead','id',id::text,'row',jsonb_build_object('bead_id',id,'created_at',memoriesql.relation_packet_time(b.created_at),'event_id',b.event_id,'source_unit_id',b.source_unit_id)));
 result:=result||memoriesql.relation_unit_records_v1(t,b.source_unit_id,known);
 FOR v IN SELECT bv.* FROM memoriesql.accepted_bead_semantics a JOIN memoriesql.bead_versions bv ON bv.tenant_id=a.tenant_id AND bv.bead_version_id=a.bead_version_id WHERE a.tenant_id=t AND a.bead_id=id AND bv.authored_at<=known LOOP
  SELECT idempotency_receipt_id INTO receipt FROM memoriesql.semantic_task_receipts WHERE tenant_id=t AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  result:=result||jsonb_build_array(jsonb_build_object('kind','accepted_bead','id',v.bead_version_id::text,'row',jsonb_build_object('bead_id',id,'bead_version_id',v.bead_version_id,'authored_at',memoriesql.relation_packet_time(v.authored_at),'semantic_task_receipt_id',v.semantic_task_receipt_id,'idempotency_receipt_id',receipt,'authored_by_principal_id',v.authored_by_principal_id)));
  FOR st IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=t AND bead_version_id=v.bead_version_id ORDER BY statement_id LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(t,st.workspace_id,st.access_scope_id,st.statement_id) THEN RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501'; END IF;
   result:=result||jsonb_build_array(jsonb_build_object('kind','statement','id',st.statement_id::text,'row',jsonb_build_object('statement_id',st.statement_id,'bead_id',st.bead_id,'bead_version_id',st.bead_version_id,'text',st.statement_text)));
   FOR e IN SELECT * FROM memoriesql.bead_semantic_statement_evidence WHERE tenant_id=t AND statement_id=st.statement_id ORDER BY evidence_source_unit_id LOOP
    result:=result||memoriesql.relation_unit_records_v1(t,e.evidence_source_unit_id,known)||jsonb_build_array(jsonb_build_object('kind','statement_evidence','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(st.statement_id,e.evidence_source_unit_id)),'row',jsonb_build_object('statement_id',st.statement_id,'source_unit_id',e.evidence_source_unit_id,'content_hash',e.evidence_content_hash)));
   END LOOP;
  END LOOP;
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_bead_records_v1(uuid,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_assertion_records_v1(t uuid,k text,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE r record; q jsonb; item jsonb; ev jsonb; result jsonb:='[]'; pin record; link record; ids uuid[]; a uuid; raw jsonb;
BEGIN
 IF NOT memoriesql.relation_closure_authorized_v1(t,k,id,known) THEN RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501'; END IF;
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
  result:=result||memoriesql.relation_bead_records_v1(t,r.source_bead_id,known)||memoriesql.relation_bead_records_v1(t,r.target_bead_id,known);
  FOR pin IN SELECT DISTINCT bead_id FROM memoriesql.assessed_relation_statements WHERE r.kind='assessed' AND tenant_id=t AND relation_id=a AND role='basis' LOOP result:=result||memoriesql.relation_bead_records_v1(t,pin.bead_id,known); END LOOP;
  IF r.kind='assessed' THEN
   SELECT judgment INTO raw FROM memoriesql.assessed_relations WHERE tenant_id=t AND relation_id=a;
   result:=result||jsonb_build_array(jsonb_build_object('kind','specialist_judgment','id',a::text,'row',raw));
  END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(q->'corrections') LOOP
   result:=result||jsonb_build_array(jsonb_build_object('kind','correction_pin_'||(item->>'role'),'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(a,item->'pinned_bead_id',item->'successor_bead_id',item->'successor_bead_version_id')),'row',item));
   result:=result||memoriesql.relation_bead_records_v1(t,(item->>'successor_bead_id')::uuid,known);
  END LOOP;
  FOR ev IN SELECT value FROM jsonb_array_elements(memoriesql.relation_events_view_v3(t,r.kind,a,known)) LOOP
   result:=result||jsonb_build_array(jsonb_build_object('kind','relation_event','id',ev->>'event_id','row',ev));
   FOR item IN SELECT value FROM jsonb_array_elements(ev->'evidence') LOOP
    result:=result||memoriesql.relation_unit_records_v1(t,(item->>'source_unit_id')::uuid,known)||jsonb_build_array(jsonb_build_object('kind','lifecycle_evidence','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(ev->'event_id',item->'statement_id',item->'source_unit_id')),'row',item));
    SELECT bead_id INTO pin FROM memoriesql.bead_semantic_statements WHERE tenant_id=t AND statement_id=(item->>'statement_id')::uuid;
    result:=result||memoriesql.relation_bead_records_v1(t,pin.bead_id,known);
   END LOOP;
  END LOOP;
  FOR link IN SELECT l.statement_id,l.evidence_source_unit_id,x.evidence_content_hash FROM memoriesql.semantic_evidence_links l JOIN memoriesql.bead_semantic_statement_evidence x ON x.tenant_id=l.tenant_id AND x.statement_id=l.statement_id AND x.evidence_source_unit_id=l.evidence_source_unit_id WHERE l.tenant_id=t AND l.owner_kind=CASE WHEN r.kind='authored' THEN 'relation' ELSE 'assessed_relation' END AND l.owner_id=a AND l.recorded_at<=known LOOP
   result:=result||memoriesql.relation_unit_records_v1(t,link.evidence_source_unit_id,known)||jsonb_build_array(jsonb_build_object('kind','assertion_evidence','id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(a,link.statement_id,link.evidence_source_unit_id)),'row',jsonb_build_object('statement_id',link.statement_id,'source_unit_id',link.evidence_source_unit_id,'content_hash',link.evidence_content_hash)));
  END LOOP;
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_assertion_records_v1(uuid,text,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_current_authority_v1(t uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM memoriesql.current_authorization_context() c JOIN memoriesql.authentication_credentials cr ON cr.tenant_id=c.tenant_id AND cr.credential_id=c.credential_id WHERE c.tenant_id=t AND c.expires_at>clock_timestamp() AND cr.expires_at>clock_timestamp() AND cr.status='active') AND memoriesql.current_context_has_capability('memory.query')
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_current_authority_v1(uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.lifecycle_nonblank_v1(value text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(length(btrim(value,chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0,false)
$$;
REVOKE ALL ON FUNCTION memoriesql.lifecycle_nonblank_v1(text) FROM PUBLIC;

-- Complete per-assertion SQL row for a trusted PR-05 catalog/frame bridge. It has
-- no inspection pagination or response ceiling; the caller admits its full result
-- and protected dependencies. The per-starting-bead root bound still applies.
CREATE FUNCTION memoriesql.relation_assertion_row_v1(t uuid,k text,id uuid,relative_bead uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE r record; raw jsonb; q jsonb; evidence jsonb; roots jsonb; status text; pins jsonb; result jsonb;
BEGIN
 IF known IS NULL OR NOT isfinite(known) OR known>statement_timestamp() OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 IF NOT memoriesql.relation_read_frame_authorized_v1(t) OR NOT memoriesql.relation_closure_authorized_v1(t,k,id,known) THEN RAISE EXCEPTION 'assertion_unavailable' USING ERRCODE='42501'; END IF;
 SELECT a.*,tr.relation_type_id INTO r FROM memoriesql.relation_assertions_v1(t,known) a JOIN memoriesql.relation_type_revisions tr ON tr.relation_type_revision_id=a.relation_type_revision_id WHERE a.kind=k AND a.relation_id=id;
 IF k='authored' THEN SELECT to_jsonb(a) INTO raw FROM memoriesql.bead_relations a WHERE tenant_id=t AND relation_id=id;
 ELSE SELECT to_jsonb(a) INTO raw FROM memoriesql.assessed_relations a WHERE tenant_id=t AND relation_id=id; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('role',p.role,'statement',jsonb_build_object('statement_id',s.statement_id,'bead_id',s.bead_id,'bead_version_id',s.bead_version_id,'text',s.statement_text)) ORDER BY s.statement_id,s.bead_id,s.bead_version_id),'[]') INTO pins FROM (
 SELECT endpoint role,statement_id FROM memoriesql.bead_relation_statements WHERE k='authored' AND tenant_id=t AND relation_id=id
 UNION ALL SELECT role,statement_id FROM memoriesql.assessed_relation_statements WHERE k='assessed' AND tenant_id=t AND relation_id=id) p JOIN memoriesql.bead_semantic_statements s ON s.tenant_id=t AND s.statement_id=p.statement_id;
 q:=memoriesql.relation_projection_v1(t,k,id,known);
 evidence:=memoriesql.relation_evidence_view_v3(t,CASE WHEN k='authored' THEN 'relation' ELSE 'assessed_relation' END,id,known);
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
 'events',memoriesql.relation_events_view_v3(t,k,id,known),'roots_status',status)||(q-ARRAY['head_manifest','corrections']);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.relation_assertion_row_v1(uuid,text,uuid,uuid,timestamptz) FROM PUBLIC;

-- Separately cited evidence requires exact accepted pins and current query/raw
-- read authority. Endpoint/basis maintain checks remain in closure authorization.
CREATE FUNCTION memoriesql.assessed_lifecycle_evidence_authorized_v1(t uuid,w uuid,item jsonb) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT EXISTS(
 SELECT 1 FROM memoriesql.bead_semantic_statements s JOIN memoriesql.accepted_bead_semantics a ON a.tenant_id=s.tenant_id AND a.bead_id=s.bead_id AND a.bead_version_id=s.bead_version_id JOIN memoriesql.bead_semantic_statement_evidence e ON e.tenant_id=s.tenant_id AND e.statement_id=s.statement_id WHERE s.tenant_id=t AND s.workspace_id=w AND s.statement_id=(item->>'statement_id')::uuid AND e.evidence_source_unit_id=(item->>'source_unit_id')::uuid AND e.evidence_content_hash=item->>'content_hash' AND memoriesql.current_context_bead_version_authorized(t,w,s.access_scope_id,s.bead_version_id) AND memoriesql.current_context_semantic_statement_authorized(t,w,s.access_scope_id,s.statement_id) AND memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'memory.query','read') AND memoriesql.current_context_event_authorized(e.access_scope_id,e.evidence_event_id,'source.read','read'))
$$;
REVOKE ALL ON FUNCTION memoriesql.assessed_lifecycle_evidence_authorized_v1(uuid,uuid,jsonb) FROM PUBLIC;

-- Match the aware finite datetime encoder independently of PostgreSQL's permissive
-- 24:00/leap-second parser and its narrower numeric timezone-offset range.
CREATE FUNCTION memoriesql.lifecycle_parse_time_v1(value text) RETURNS timestamptz
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE pieces text[]; result timestamptz; offset_minutes integer:=0;
BEGIN
 pieces:=regexp_match(value,'^([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,6})?(Z|([+-])([0-9]{2}):([0-9]{2}))$');
 IF pieces IS NULL OR pieces[2]::integer>23 OR pieces[3]::integer>59 OR pieces[4]::integer>59 OR COALESCE(pieces[8]::integer,0)>23 OR COALESCE(pieces[9]::integer,0)>59 THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 IF pieces[6]<>'Z' THEN offset_minutes:=(pieces[8]::integer*60+pieces[9]::integer)*CASE WHEN pieces[7]='-' THEN -1 ELSE 1 END; END IF;
 result:=((pieces[1]||'T'||pieces[2]||':'||pieces[3]||':'||pieces[4]||COALESCE(pieces[5],''))::timestamp-make_interval(mins=>offset_minutes)) AT TIME ZONE 'UTC';
 IF NOT isfinite(result) OR extract(year FROM result AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.lifecycle_parse_time_v1(text) FROM PUBLIC;

-- Narrow schema-29 bundle restatement: retain its existing common cycle check
-- and all authorship/claims/coverage rules; lock every pinned type key/revision.
CREATE OR REPLACE FUNCTION memoriesql.apply_authored_relations_v1(
    t uuid, w uuid, scope uuid, task uuid, attempt uuid, authored_bead uuid, authored_version uuid,
    receipt uuid, payload jsonb, run_ref text, principal uuid, at timestamp with time zone
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    x memoriesql.complete_input_executions%ROWTYPE;
    rel jsonb; cand jsonb; item jsonb; claim jsonb; upd jsonb; ref jsonb;
    statement uuid; unit uuid; claim_event uuid; type_revision uuid; named uuid[];
    candidate_scope uuid; candidate_version uuid; forbidden uuid;
    uuid_pattern constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    SELECT * INTO x FROM memoriesql.complete_input_executions WHERE tenant_id = t AND execution_task_id = task;
    IF x.execution_contract_revision IS DISTINCT FROM 6
       OR payload - ARRAY['annotations', 'relations', 'candidate_assessments', 'claims', 'claim_updates'] <> '{}'::jsonb
       OR jsonb_typeof(payload->'relations') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'relations') > 16
       OR jsonb_typeof(payload->'candidate_assessments') IS DISTINCT FROM 'array'
       OR jsonb_typeof(payload->'claims') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'claims') > 16
       OR jsonb_typeof(payload->'claim_updates') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'claim_updates') > 16
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'relations') AS r(v)
                  WHERE COALESCE(v->>'relation_id', '') !~ uuid_pattern OR COALESCE(v->>'candidate_bead_id', '') !~ uuid_pattern
                     OR jsonb_typeof(v->'authored_statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_typeof(v->'candidate_statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_typeof(v->'evidence') IS DISTINCT FROM 'array'
                     OR jsonb_array_length(v->'authored_statement_ids') NOT BETWEEN 1 AND 8
                     OR jsonb_array_length(v->'candidate_statement_ids') NOT BETWEEN 1 AND 8
                     OR jsonb_array_length(v->'evidence') NOT BETWEEN 1 AND 8
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'authored_statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'candidate_statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)
                     OR (SELECT count(*) <> count(DISTINCT i) FROM jsonb_array_elements_text(v->'authored_statement_ids') AS s(i))
                     OR (SELECT count(*) <> count(DISTINCT i) FROM jsonb_array_elements_text(v->'candidate_statement_ids') AS s(i)))
       OR (SELECT count(*) <> count(DISTINCT v->>'relation_id') FROM jsonb_array_elements(payload->'relations') AS r(v))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'claims') AS k(v)
                  WHERE COALESCE(v->>'claim_id', '') !~ uuid_pattern
                     OR jsonb_typeof(v->'statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_array_length(v->'statement_ids') NOT BETWEEN 1 AND 8
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)
                     OR (SELECT count(*) <> count(DISTINCT i) FROM jsonb_array_elements_text(v->'statement_ids') AS s(i))
                     OR (jsonb_typeof(v->'subject_mention_id') = 'string' AND v->>'subject_mention_id' !~ uuid_pattern))
       OR (SELECT count(*) <> count(DISTINCT v->>'claim_id') FROM jsonb_array_elements(payload->'claims') AS k(v))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'claim_updates') AS u(v)
                  WHERE COALESCE(v->>'target_claim_id', '') !~ uuid_pattern
                     OR (jsonb_typeof(v->'related_claim_id') = 'string' AND v->>'related_claim_id' !~ uuid_pattern)
                     OR jsonb_typeof(v->'basis_statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_array_length(v->'basis_statement_ids') NOT BETWEEN 1 AND 8
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'basis_statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)) THEN
        RAISE EXCEPTION 'authored_relations_invalid' USING ERRCODE = '22023';
    END IF;
    -- Fresh identifiers only; a collision is an authored error, never an adopted row.
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'relations') AS r(v)
               JOIN memoriesql.bead_relations AS existing ON existing.tenant_id = t AND existing.relation_id = (v->>'relation_id')::uuid)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'claims') AS k(v)
                  JOIN memoriesql.bead_claims AS existing ON existing.tenant_id = t AND existing.claim_id = (v->>'claim_id')::uuid) THEN
        RAISE EXCEPTION 'authored_identifier_conflict' USING ERRCODE = '22023';
    END IF;
    -- Every pinned candidate is assessed exactly once.
    IF (SELECT COALESCE(jsonb_agg(v->>'candidate_bead_id' ORDER BY v->>'candidate_bead_id'), '[]'::jsonb)
        FROM jsonb_array_elements(payload->'candidate_assessments') AS a(v))
       IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(v->>'bead_id' ORDER BY v->>'bead_id'), '[]'::jsonb)
                         FROM jsonb_array_elements(x.relation_candidates) AS c(v)) THEN
        RAISE EXCEPTION 'relation_candidate_coverage_incomplete' USING ERRCODE = '22023';
    END IF;

    -- All pinned type keys are serialized before the shared both-kind cycle check, so
    -- concurrent bundles cannot close a cycle together (locks in a fixed order).
    FOR forbidden IN
        SELECT DISTINCT r.relation_type_id
        FROM jsonb_array_elements(payload->'relations') AS p(v)
        JOIN memoriesql.relation_types AS ty ON ty.type_key = p.v#>>'{relation_type,key}'
         AND (ty.tenant_id IS NULL OR (ty.tenant_id = t AND ty.workspace_id = w))
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
         AND r.revision = (p.v#>>'{relation_type,revision}')::integer
        ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            t::text || ':relation-cycle:' || forbidden::text, 0));
    END LOOP;

    FOR rel IN SELECT value FROM jsonb_array_elements(payload->'relations') ORDER BY value->>'relation_id' LOOP
        cand := (SELECT v FROM jsonb_array_elements(x.relation_candidates) AS c(v) WHERE v->>'bead_id' = rel->>'candidate_bead_id');
        IF cand IS NULL THEN
            RAISE EXCEPTION 'relation_candidate_not_pinned' USING ERRCODE = '22023';
        END IF;
        SELECT access_scope_id, bead_version_id INTO candidate_scope, candidate_version FROM memoriesql.accepted_bead_semantics
        WHERE tenant_id = t AND bead_id = (cand->>'bead_id')::uuid AND bead_version_id = (cand->>'bead_version_id')::uuid;
        IF jsonb_typeof(rel->'relation_type') IS DISTINCT FROM 'object'
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.relation_vocabulary) AS d(v)
                          WHERE v->>'key' = rel#>>'{relation_type,key}' AND v->'revision' = rel#>'{relation_type,revision}') THEN
            RAISE EXCEPTION 'unknown_relation_type' USING ERRCODE = '22023';
        END IF;
        SELECT r.relation_type_revision_id INTO type_revision FROM memoriesql.relation_types AS ty
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
        WHERE ty.type_key = rel#>>'{relation_type,key}' AND r.revision = (rel#>>'{relation_type,revision}')::integer
          AND (ty.tenant_id IS NULL OR (ty.tenant_id = t AND ty.workspace_id = w));
        IF type_revision IS NULL OR rel->>'basis' IS NULL OR rel->>'basis' NOT IN ('source_stated', 'agent_inferred')
           OR rel->>'direction' IS NULL OR rel->>'direction' NOT IN ('from_authored', 'to_authored')
           OR jsonb_typeof(rel->'rationale') IS DISTINCT FROM 'string' OR btrim(rel->>'rationale') = '' OR char_length(rel->>'rationale') > 1024
           OR jsonb_typeof(rel->'qualification') NOT IN ('string', 'null')
           OR (jsonb_typeof(rel->'qualification') = 'string' AND (btrim(rel->>'qualification') = '' OR char_length(rel->>'qualification') > 1024))
           OR jsonb_typeof(rel->'author_confidence') IS DISTINCT FROM 'number'
           OR (rel->>'author_confidence')::numeric NOT BETWEEN 0 AND 1
           OR round((rel->>'author_confidence')::numeric, 2) <> (rel->>'author_confidence')::numeric THEN
            RAISE EXCEPTION 'authored_relations_invalid' USING ERRCODE = '22023';
        END IF;
        -- Endpoint propositions: statements of the authored version and of the pinned candidate packet.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(rel->'authored_statement_ids') AS s(i)
                   WHERE NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                     WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = authored_version)) THEN
            RAISE EXCEPTION 'relation_statement_not_authored' USING ERRCODE = '22023';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(rel->'candidate_statement_ids') AS s(i)
                   WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(cand->'statements') AS p(v) WHERE p.v->>'statement_id' = s.i)
                      OR NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                     WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = candidate_version)) THEN
            RAISE EXCEPTION 'relation_statement_not_pinned' USING ERRCODE = '22023';
        END IF;
        named := ARRAY(SELECT i::uuid FROM jsonb_array_elements_text(rel->'authored_statement_ids') AS s(i))
              || ARRAY(SELECT i::uuid FROM jsonb_array_elements_text(rel->'candidate_statement_ids') AS s(i));
        -- Evidence must already support one of the relation's named propositions.
        FOR ref IN SELECT value FROM jsonb_array_elements(rel->'evidence') LOOP
            IF jsonb_typeof(ref) IS DISTINCT FROM 'object' OR ref - ARRAY['source_unit_id', 'content_hash'] <> '{}'::jsonb
               OR COALESCE(ref->>'source_unit_id', '') !~ uuid_pattern OR COALESCE(ref->>'content_hash', '') !~ '^[a-f0-9]{64}$'
               OR NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS ev
                              WHERE ev.tenant_id = t AND ev.statement_id = ANY(named)
                                AND ev.evidence_source_unit_id = (ref->>'source_unit_id')::uuid
                                AND ev.evidence_content_hash = ref->>'content_hash') THEN
                RAISE EXCEPTION 'relation_evidence_unbound' USING ERRCODE = '22023';
            END IF;
        END LOOP;
        IF (SELECT count(*) <> count(DISTINCT (v->>'source_unit_id', v->>'content_hash'))
            FROM jsonb_array_elements(rel->'evidence') AS r(v)) THEN
            RAISE EXCEPTION 'authored_relations_invalid' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.bead_relations (
            tenant_id, workspace_id, relation_id, source_access_scope_id, source_bead_id, source_bead_version_id,
            target_access_scope_id, target_bead_id, target_bead_version_id, relation_type_revision_id, basis,
            rationale_text, qualification_text, author_confidence, authoring_bead_id, semantic_task_id,
            semantic_attempt_id, semantic_run_id, authored_by_principal_id, recorded_at
        ) SELECT
            t, w, (rel->>'relation_id')::uuid,
            CASE WHEN rel->>'direction' = 'from_authored' THEN scope ELSE candidate_scope END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN authored_bead ELSE (cand->>'bead_id')::uuid END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN authored_version ELSE candidate_version END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN candidate_scope ELSE scope END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN (cand->>'bead_id')::uuid ELSE authored_bead END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN candidate_version ELSE authored_version END,
            type_revision, rel->>'basis', rel->>'rationale', NULLIF(rel->'qualification', 'null'::jsonb) #>> '{}',
            (rel->>'author_confidence')::numeric, authored_bead, task, attempt, run_ref, principal, at;
        INSERT INTO memoriesql.bead_relation_statements (tenant_id, relation_id, endpoint, workspace_id, access_scope_id, statement_id)
        SELECT t, (rel->>'relation_id')::uuid,
               CASE WHEN rel->>'direction' = 'from_authored' THEN 'source' ELSE 'target' END, w, scope, i::uuid
        FROM jsonb_array_elements_text(rel->'authored_statement_ids') AS s(i)
        UNION ALL
        SELECT t, (rel->>'relation_id')::uuid,
               CASE WHEN rel->>'direction' = 'from_authored' THEN 'target' ELSE 'source' END, w, candidate_scope, i::uuid
        FROM jsonb_array_elements_text(rel->'candidate_statement_ids') AS s(i);
        FOR statement, unit IN
            SELECT DISTINCT ev.statement_id, ev.evidence_source_unit_id
            FROM jsonb_array_elements(rel->'evidence') AS r(v)
            JOIN memoriesql.bead_semantic_statement_evidence AS ev
              ON ev.tenant_id = t AND ev.statement_id = ANY(named)
             AND ev.evidence_source_unit_id = (r.v->>'source_unit_id')::uuid
             AND ev.evidence_content_hash = r.v->>'content_hash'
            ORDER BY 1, 2
        LOOP
            PERFORM memoriesql.record_semantic_evidence_link(t, w, 'relation', (rel->>'relation_id')::uuid,
                'supports', statement, unit, at);
        END LOOP;
    END LOOP;

    -- An assertion never references itself, and no cycle-forbidden key forms a cycle
    -- between statements among its active assertions once this write commits.
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(payload->'relations') AS p(v)
        JOIN memoriesql.bead_relation_statements AS source_side
          ON source_side.tenant_id = t AND source_side.relation_id = (p.v->>'relation_id')::uuid AND source_side.endpoint = 'source'
        JOIN memoriesql.bead_relation_statements AS target_side
          ON target_side.tenant_id = t AND target_side.relation_id = source_side.relation_id AND target_side.endpoint = 'target'
         AND target_side.statement_id = source_side.statement_id
    ) THEN
        RAISE EXCEPTION 'relation_self_reference' USING ERRCODE = '22023';
    END IF;
    -- The shared check covers both relation kinds, so an assessed assertion counts too.
    IF memoriesql.relation_cycle_closes_v1(t, (
        SELECT COALESCE(jsonb_agg(jsonb_build_object('kind', 'authored', 'relation_id', p.v->>'relation_id')), '[]'::jsonb)
        FROM jsonb_array_elements(payload->'relations') AS p(v))) THEN
        RAISE EXCEPTION 'relation_cycle_forbidden' USING ERRCODE = '22023';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(payload->'candidate_assessments') ORDER BY value->>'candidate_bead_id' LOOP
        cand := (SELECT v FROM jsonb_array_elements(x.relation_candidates) AS c(v) WHERE v->>'bead_id' = item->>'candidate_bead_id');
        SELECT access_scope_id, bead_version_id INTO candidate_scope, candidate_version FROM memoriesql.accepted_bead_semantics
        WHERE tenant_id = t AND bead_id = (cand->>'bead_id')::uuid AND bead_version_id = (cand->>'bead_version_id')::uuid;
        IF item->>'assessment' IS NULL OR item->>'assessment' NOT IN ('edge', 'no_edge', 'unassessed')
           OR (item->>'assessment' = 'edge') IS DISTINCT FROM EXISTS (
                SELECT 1 FROM jsonb_array_elements(payload->'relations') AS r(v) WHERE r.v->>'candidate_bead_id' = item->>'candidate_bead_id')
           OR jsonb_typeof(item->'reason') NOT IN ('string', 'null')
           OR (jsonb_typeof(item->'reason') = 'string' AND (btrim(item->>'reason') = '' OR char_length(item->>'reason') > 1024))
           OR (item->>'assessment' = 'unassessed' AND jsonb_typeof(item->'reason') IS DISTINCT FROM 'string') THEN
            RAISE EXCEPTION 'relation_candidate_coverage_invalid' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.relation_candidate_assessments (
            tenant_id, workspace_id, access_scope_id, authoring_bead_id, authoring_bead_version_id,
            candidate_access_scope_id, candidate_bead_id, candidate_bead_version_id, assessment, reason_text,
            semantic_task_id, semantic_attempt_id, semantic_run_id, recorded_at
        ) VALUES (
            t, w, scope, authored_bead, authored_version, candidate_scope, (cand->>'bead_id')::uuid,
            candidate_version, item->>'assessment', NULLIF(item->'reason', 'null'::jsonb) #>> '{}', task, attempt, run_ref, at
        );
    END LOOP;

    FOR claim IN SELECT value FROM jsonb_array_elements(payload->'claims') ORDER BY value->>'claim_id' LOOP
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(claim->'statement_ids') AS s(i)
                   WHERE NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                     WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = authored_version))
           OR (jsonb_typeof(claim->'subject_mention_id') = 'string' AND NOT EXISTS (
                SELECT 1 FROM memoriesql.entity_mentions AS m
                WHERE m.tenant_id = t AND m.entity_mention_id = (claim->>'subject_mention_id')::uuid
                  AND m.bead_version_id = authored_version))
           OR jsonb_typeof(claim->'subject_mention_id') NOT IN ('string', 'null')
           OR jsonb_typeof(claim->'subject') IS DISTINCT FROM 'string' OR btrim(claim->>'subject') = '' OR char_length(claim->>'subject') > 256
           OR jsonb_typeof(claim->'slot') IS DISTINCT FROM 'string' OR btrim(claim->>'slot') = '' OR char_length(claim->>'slot') > 128
           OR jsonb_typeof(claim->'value') IS DISTINCT FROM 'string' OR btrim(claim->>'value') = '' OR char_length(claim->>'value') > 1024
           OR jsonb_typeof(claim->'applicability') NOT IN ('string', 'null')
           OR (jsonb_typeof(claim->'applicability') = 'string'
               AND (btrim(claim->>'applicability') = '' OR char_length(claim->>'applicability') > 1024)) THEN
            RAISE EXCEPTION 'authored_claim_invalid' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.bead_claims (
            tenant_id, workspace_id, access_scope_id, claim_id, bead_id, bead_version_id, subject_text,
            subject_entity_mention_id, slot_text, value_text, applicability_text, semantic_task_id,
            semantic_attempt_id, semantic_run_id, authored_by_principal_id, recorded_at
        ) VALUES (
            t, w, scope, (claim->>'claim_id')::uuid, authored_bead, authored_version, claim->>'subject',
            (NULLIF(claim->'subject_mention_id', 'null'::jsonb) #>> '{}')::uuid, claim->>'slot', claim->>'value',
            NULLIF(claim->'applicability', 'null'::jsonb) #>> '{}', task, attempt, run_ref, principal, at
        );
        INSERT INTO memoriesql.bead_claim_statements (tenant_id, workspace_id, access_scope_id, claim_id, statement_id)
        SELECT t, w, scope, (claim->>'claim_id')::uuid, i::uuid FROM jsonb_array_elements_text(claim->'statement_ids') AS s(i);
    END LOOP;

    FOR upd IN SELECT value FROM jsonb_array_elements(payload->'claim_updates')
               ORDER BY value->>'target_claim_id', value->>'action', value->>'related_claim_id' LOOP
        -- The author saw only pinned candidates' claims; the target must be one of them.
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.relation_candidates) AS c(v),
                                     jsonb_array_elements(c.v->'claims') AS k(v2)
                       WHERE k.v2->>'claim_id' = upd->>'target_claim_id')
           OR upd->>'action' IS NULL OR upd->>'action' NOT IN ('supersede', 'dispute', 'reaffirm')
           OR (upd->>'action' = 'reaffirm') IS DISTINCT FROM (jsonb_typeof(upd->'related_claim_id') IS DISTINCT FROM 'string')
           OR (jsonb_typeof(upd->'related_claim_id') = 'string' AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(payload->'claims') AS k(v) WHERE k.v->>'claim_id' = upd->>'related_claim_id'))
           OR jsonb_typeof(upd->'reason') IS DISTINCT FROM 'string' OR btrim(upd->>'reason') = '' OR char_length(upd->>'reason') > 1024
           OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(upd->'basis_statement_ids') AS s(i)
                      WHERE NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                        WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = authored_version)) THEN
            RAISE EXCEPTION 'claim_update_invalid' USING ERRCODE = '22023';
        END IF;
        claim_event := pg_catalog.uuidv7();
        INSERT INTO memoriesql.bead_claim_events (
            tenant_id, workspace_id, claim_event_id, claim_id, action, related_claim_id, reason, origin,
            authoring_bead_id, authoring_bead_version_id, semantic_task_id, semantic_attempt_id, semantic_run_id,
            idempotency_receipt_id, recorded_by_principal_id, effective_at, recorded_at
        ) VALUES (
            t, w, claim_event, (upd->>'target_claim_id')::uuid, upd->>'action',
            (NULLIF(upd->'related_claim_id', 'null'::jsonb) #>> '{}')::uuid,
            upd->>'reason', 'authored', authored_bead, authored_version, task, attempt, run_ref, receipt, principal, NULL, at
        );
        FOR statement, unit IN
            SELECT ev.statement_id, ev.evidence_source_unit_id FROM memoriesql.bead_semantic_statement_evidence AS ev
            WHERE ev.tenant_id = t
              AND ev.statement_id IN (SELECT i::uuid FROM jsonb_array_elements_text(upd->'basis_statement_ids') AS s(i))
            ORDER BY 1, 2
        LOOP
            PERFORM memoriesql.record_semantic_evidence_link(t, w, 'claim_event', claim_event, 'supports', statement, unit, at);
        END LOOP;
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.apply_authored_relations_v1(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,jsonb,text,uuid,timestamptz) FROM PUBLIC;
