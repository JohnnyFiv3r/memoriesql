-- Agent source text view, contract v1 (read_source_text). An agent reads a
-- bounded span of exactly the unit text the approved query exception already
-- serves (0037/0039 search_text), only for a source ref this same run received.
-- No raw bytes, raw lineage, package/part identity or native metadata is read
-- or disclosed, and source revisiting authority is never consulted. Existing
-- delivery, disclosure, preparation, cursor, purge and audit mechanics are
-- reused. A ref delivered under a checkpoint context is refused explicitly
-- (unsupported_query), never served as standalone. Each helper-kind
-- redefinition below is the latest earlier definition with only the new kind
-- added:
--   query_steps_helper_kind_check: 0051_automatic_investigation_checkpoints.sql
--   query_steps_helper_complete_check: 0051_automatic_investigation_checkpoints.sql
--   admit_query_delivery_v1: 0051_automatic_investigation_checkpoints.sql
--   query_helper_delivery_v1: 0051_automatic_investigation_checkpoints.sql
--   purge_query_helper_step_v1: 0046_agent_sql_result_helpers.sql
--   prepare_query_helper_v1: 0046_agent_sql_result_helpers.sql
ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_kind_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_kind_check
 CHECK(kind IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint','read_source_text'));
ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_complete_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_complete_check CHECK(
 (state='complete')=(
  (kind IN ('query','reuse_result') AND helper_artifact_ref IS NULL AND result_id IS NOT NULL
   AND (purged_at IS NOT NULL OR (content_digest IS NOT NULL AND first_row IS NOT NULL AND row_count IS NOT NULL AND page_size IS NOT NULL)))
  OR (kind IN ('inspect','hydrate_source','read_source_text') AND state='complete' AND result_id IS NULL AND first_row IS NULL AND row_count IS NULL AND page_size IS NULL
   AND (purged_at IS NOT NULL OR (helper_artifact_ref IS NOT NULL AND content_digest IS NOT NULL AND helper_page_first IS NOT NULL AND helper_page_count IS NOT NULL)))
  OR (kind IN ('checkpoint','read_checkpoint') AND state='complete' AND result_id IS NULL AND helper_artifact_ref IS NULL
   AND (purged_at IS NOT NULL OR (checkpoint_ref IS NOT NULL AND content_digest IS NOT NULL)))));

CREATE OR REPLACE FUNCTION memoriesql.admit_query_delivery_v1(run uuid,step uuid,requested_kind text,
 fingerprint text,operation_ms integer,response_bytes integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 blocking memoriesql.query_deliveries%ROWTYPE; now_at timestamptz:=clock_timestamp();
 remaining jsonb; used_db bigint; used_transport bigint; used_accesses bigint;
 window_db bigint; window_transport bigint; reserve_ms bigint; reserve_bytes bigint; replay boolean;
BEGIN
 IF run IS NULL OR step IS NULL OR requested_kind NOT IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint','read_source_text')
  OR fingerprint IS NULL OR fingerprint !~ '^[a-f0-9]{64}$'
  OR operation_ms IS NULL OR operation_ms NOT BETWEEN 1 AND 30000
  OR response_bytes IS NULL OR response_bytes NOT BETWEEN 1024 AND 262144 THEN
  RAISE EXCEPTION 'invalid_query_delivery' USING ERRCODE='22023';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':query-access:'||c.workspace_id::text,0));
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=run FOR UPDATE;
 IF NOT FOUND OR NOT memoriesql.query_run_owner_v1(r,c) THEN
  RETURN jsonb_build_object('refused','unavailable');
 END IF;
 remaining:=memoriesql.query_run_remaining_v1(r);
 IF now_at>=r.expires_at OR EXISTS(SELECT 1 FROM memoriesql.query_run_closures
   WHERE tenant_id=c.tenant_id AND run_ref=run) THEN
  RETURN jsonb_build_object('refused','run_expired','remaining',remaining);
 END IF;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=c.tenant_id AND run_ref=run
  AND step_key=step FOR UPDATE;
 replay:=FOUND;
 IF replay AND (s.kind<>requested_kind OR s.request_fingerprint IS DISTINCT FROM fingerprint) THEN
  RETURN jsonb_build_object('refused','idempotency','remaining',remaining);
 END IF;
 -- One executing operation per workspace; unsettled or uncertain earlier work
 -- blocks new admission until owned settlement or recovery confirms it.
 SELECT * INTO blocking FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id
  AND workspace_id=c.workspace_id AND state<>'settled'
  ORDER BY admitted_at LIMIT 1;
 IF FOUND THEN
  RETURN jsonb_build_object('refused',CASE
    WHEN blocking.run_ref=run AND blocking.step_key=step THEN 'pending_self'
    WHEN blocking.state='settlement_pending' OR blocking.deadline<=now_at
     OR memoriesql.query_delivery_owner_gone_v1(blocking.delivery_ref) THEN 'settlement'
    ELSE 'busy' END,
   'blocking_delivery',CASE WHEN blocking.run_ref=run AND blocking.step_key=step
    THEN to_jsonb(blocking.delivery_ref) ELSE 'null'::jsonb END,
   'remaining',remaining);
 END IF;
 SELECT count(*),COALESCE(sum(COALESCE(charged_db_ms,reserved_db_ms)),0),
  COALESCE(sum(COALESCE(charged_transport,reserved_transport)),0)
  INTO used_accesses,used_db,used_transport
  FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id AND run_ref=run;
 SELECT COALESCE(sum(COALESCE(charged_db_ms,reserved_db_ms)),0),
  COALESCE(sum(COALESCE(charged_transport,reserved_transport)),0)
  INTO window_db,window_transport
  FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id
   AND admitted_at>now_at-interval '24 hours';
 IF used_accesses>=128 THEN
  RETURN jsonb_build_object('refused','accesses','remaining',remaining);
 END IF;
 reserve_ms:=LEAST(operation_ms,300000-used_db,1800000-window_db,
  floor(extract(epoch FROM (r.expires_at-now_at))*1000));
 IF reserve_ms<1 THEN RETURN jsonb_build_object('refused','time','remaining',remaining); END IF;
 -- 16 KiB of the run's transport stays reserved for terminal diagnostics.
 reserve_bytes:=LEAST(response_bytes,16777216-16384-used_transport,268435456-window_transport);
 IF reserve_bytes<1024 THEN
  RETURN jsonb_build_object('refused','transport','remaining',remaining);
 END IF;
 -- Retained query audit draws on its own workspace allowance, apart from the
 -- content allocation (owner decision 2026-10-22). Work admitted adds its audit.
 IF memoriesql.query_audit_row_bytes_v1()*(SELECT count(*) FROM memoriesql.query_audit_events
   WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id)
   >=memoriesql.query_audit_allowance_bytes_v1() THEN
  RETURN jsonb_build_object('refused','audit','remaining',remaining);
 END IF;
 IF NOT replay THEN
  INSERT INTO memoriesql.query_steps(tenant_id,run_ref,step_key,kind,request_fingerprint,state,created_at)
  VALUES(c.tenant_id,run,step,requested_kind,fingerprint,'executing',now_at) RETURNING * INTO s;
 END IF;
 INSERT INTO memoriesql.query_deliveries(tenant_id,delivery_ref,run_ref,step_key,workspace_id,
  delivery_number,state,reserved_db_ms,reserved_transport,admitted_at,deadline)
 VALUES(c.tenant_id,uuidv7(),run,step,c.workspace_id,
  COALESCE((SELECT max(delivery_number) FROM memoriesql.query_deliveries
   WHERE tenant_id=c.tenant_id AND run_ref=run AND step_key=step),0)+1,
  'reserved',reserve_ms,reserve_bytes,now_at,
  LEAST(now_at+reserve_ms*interval '1 millisecond',r.expires_at)) RETURNING * INTO d;
 RETURN jsonb_build_object('delivery_ref',d.delivery_ref,'delivery_number',d.delivery_number,
  'receipt_ref',(SELECT delivery_ref FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id
    AND run_ref=run AND step_key=step AND delivery_number=1),
  'replay',replay,'step',jsonb_build_object('state',s.state,'outcome',s.outcome,
   'error_code',s.error_code,'result_id',s.result_id,'content_digest',s.content_digest,
   'first_row',s.first_row,'row_count',s.row_count,'page_size',s.page_size),
  'reserved_db_ms',d.reserved_db_ms,'reserved_transport',d.reserved_transport,
  'deadline',to_char(d.deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  -- Remaining before this access; the executor subtracts its own settled charge.
  'remaining',remaining,'owner_lock_key',hashtextextended(c.tenant_id::text||':query-delivery:'||d.delivery_ref::text,0));
END $$;

CREATE OR REPLACE FUNCTION memoriesql.query_helper_delivery_v1(delivery uuid)
RETURNS memoriesql.query_deliveries
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 r memoriesql.query_runs%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id AND delivery_ref=delivery;
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 IF d.delivery_ref IS NULL OR r.run_ref IS NULL OR NOT memoriesql.query_run_owner_v1(r,c)
  OR s.kind NOT IN ('inspect','hydrate_source','checkpoint','read_checkpoint','read_source_text') OR s.purged_at IS NOT NULL
  OR EXISTS(SELECT 1 FROM memoriesql.query_run_closures WHERE tenant_id=c.tenant_id AND run_ref=r.run_ref) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF d.state<>'reserved' THEN RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000'; END IF;
 IF clock_timestamp()>=LEAST(d.deadline,r.expires_at) THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000'; END IF;
 RETURN d;
END $$;

CREATE OR REPLACE FUNCTION memoriesql.purge_query_helper_step_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE op memoriesql.result_preparation_operations%ROWTYPE;
BEGIN
 IF NEW.kind NOT IN ('inspect','hydrate_source','read_source_text') OR NEW.purged_at IS NULL OR OLD.purged_at IS NOT NULL THEN
  RETURN NEW; END IF;
 IF TG_WHEN='BEFORE' THEN
  NEW.helper_artifact_ref:=NULL; NEW.helper_page_first:=NULL; NEW.helper_page_count:=NULL; NEW.helper_page_cursor:=NULL;
  RETURN NEW; END IF;
 DELETE FROM memoriesql.query_helper_cursors WHERE tenant_id=NEW.tenant_id AND run_ref=NEW.run_ref;
 SELECT * INTO op FROM memoriesql.result_preparation_operations WHERE tenant_id=NEW.tenant_id
  AND run_ref=NEW.run_ref AND step_key=NEW.step_key FOR UPDATE;
 DELETE FROM memoriesql.query_disclosures x USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND x.tenant_id=d.tenant_id AND x.delivery_ref=d.delivery_ref;
 DELETE FROM memoriesql.result_preparation_artifacts WHERE tenant_id=NEW.tenant_id AND operation_ref=op.operation_ref;
 UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
  encoded_bytes=0,allocation_bytes=8192 WHERE tenant_id=NEW.tenant_id AND operation_ref=op.operation_ref;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.purge_query_helper_step_v1() FROM PUBLIC;

-- The unit text the approved query exception serves (0037, current 0039
-- population): retained unit text, else the pinned package's text only when it
-- is sealed, matches its inventory, is complete and every part is
-- producer-normalized. An identity, mixed or missing package serves nothing.
-- Labels are the population's own text labels for that served text. The
-- package read is query_unit_package_v1 as sealed unit facts define it: one
-- facts row (the gate and the sealed normalized text), the pin and the package
-- the facts were sealed from, never a part row; a pin that no longer names
-- that package refuses. So no read of this kind tracks a unit's hidden part
-- count, and unsupported text is never measured or aggregated.
CREATE FUNCTION memoriesql.query_unit_source_text_v1(t uuid,unit uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE retained text; pkg jsonb; unit_text text; text_labels text[]:='{}';
BEGIN
 SELECT su.content_text INTO retained FROM memoriesql.source_units su WHERE su.tenant_id=t AND su.source_unit_id=unit;
 IF retained IS NULL THEN pkg:=memoriesql.query_unit_package_v1(t,unit); END IF;
 unit_text:=COALESCE(retained,CASE WHEN (pkg->>'normalized')::boolean THEN pkg->>'text' END);
 IF retained IS NULL AND unit_text IS NOT NULL THEN
  text_labels:=text_labels
   ||('normalized_projection:'||(pkg->>'normalization_policy_version'))
   ||ARRAY(SELECT 'package_exclusion:'||x FROM jsonb_array_elements_text(pkg->'exclusions') x)
   ||ARRAY(SELECT 'package_unresolved:'||x FROM jsonb_array_elements_text(pkg->'unresolved_coverage') x);
 END IF;
 RETURN jsonb_build_object('text',unit_text,'labels',to_jsonb(text_labels));
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_unit_source_text_v1(uuid,uuid) FROM PUBLIC;

-- The delivering disclosure of one source ref: only the exact ref (unit and
-- content hash included) that THIS run received, under the context receipted
-- for its first delivery or an explicit origin that delivered it in this run.
-- Stricter than hydrate_source's origin; never another run's disclosure.
CREATE FUNCTION memoriesql.resolve_query_source_text_v1(delivery uuid,source jsonb,origin jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; v memoriesql.query_visible_refs%ROWTYPE;
 x memoriesql.query_disclosures%ROWTYPE;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 IF jsonb_typeof(source) IS DISTINCT FROM 'object' OR source->>'kind' IS DISTINCT FROM 'source' THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO v FROM memoriesql.query_visible_refs WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref
  AND ref_key::jsonb=source;
 IF v.first_delivery_ref IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF origin IS NULL OR origin='null'::jsonb THEN
  SELECT * INTO x FROM memoriesql.query_disclosures WHERE tenant_id=d.tenant_id AND delivery_ref=v.first_delivery_ref;
 ELSE
  -- Checkpoint contexts need the checkpoint migration; never treat one as standalone.
  IF origin#>>'{access,kind}' IS DISTINCT FROM 'standalone' THEN
   RAISE EXCEPTION 'helper_checkpoint_access_unimplemented' USING ERRCODE='0A000'; END IF;
  SELECT z.* INTO x FROM memoriesql.query_disclosures z JOIN memoriesql.query_deliveries zd
   ON zd.tenant_id=z.tenant_id AND zd.delivery_ref=z.delivery_ref
   WHERE z.tenant_id=d.tenant_id AND zd.run_ref=d.run_ref
    AND z.result_id=(origin#>>'{result,result_id}')::uuid AND z.content_digest=origin#>>'{result,content_digest}'
    AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(z.delivered_refs) AS r(ref_key) WHERE r.ref_key::jsonb=source)
   ORDER BY zd.admitted_at,zd.delivery_ref LIMIT 1;
 END IF;
 -- A ref first delivered by a helper reply (owner evidence metadata) has no
 -- delivering result to bind; v1 serves result-delivered refs only.
 IF x.delivery_ref IS NULL OR x.result_id IS NULL THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF x.access_context IS DISTINCT FROM '{"kind":"standalone"}'::jsonb THEN
  RAISE EXCEPTION 'helper_checkpoint_access_unimplemented' USING ERRCODE='0A000'; END IF;
 RETURN jsonb_build_object('result_id',x.result_id,'content_digest',x.content_digest);
END $$;
REVOKE ALL ON FUNCTION memoriesql.resolve_query_source_text_v1(uuid,jsonb,jsonb) FROM PUBLIC;

-- Bind a resolved ref to its statement and unit through the delivering result's
-- own evidence binding, then evaluate the source-text authority now: statement
-- and bead-version authorization and the 0037 bead/unit records (bead event
-- memory.query; unit event memory.query and source.read). The unit's package
-- must be exactly the one recorded in that result's closure. Callers recheck
-- the result's whole closure first; source revisiting is never consulted.
CREATE FUNCTION memoriesql.bind_query_source_text_v1(t uuid,source jsonb,result_ref uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE a memoriesql.result_preparation_artifacts%ROWTYPE; q memoriesql.query_result_creations%ROWTYPE;
 st memoriesql.bead_semantic_statements%ROWTYPE; link memoriesql.bead_semantic_statement_evidence%ROWTYPE;
 su memoriesql.source_units%ROWTYPE; body jsonb; deps jsonb; binding jsonb; saved jsonb; records jsonb;
 known timestamptz; saved_package jsonb; current_package jsonb;
BEGIN
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=t AND artifact_ref=result_ref;
 SELECT * INTO q FROM memoriesql.query_result_creations WHERE tenant_id=t AND result_id=result_ref;
 IF a.artifact_ref IS NULL OR q.result_id IS NULL THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb; deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 binding:=deps#>ARRAY['evidence_bindings',source->>'ref'];
 IF binding IS NULL OR binding->>'source_unit_id' IS DISTINCT FROM source->>'unit_ref'
  OR binding->>'content_sha256' IS DISTINCT FROM source->>'content_sha256' THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 known:=(body#>>'{frame,known_at}')::timestamptz;
 SELECT * INTO st FROM memoriesql.bead_semantic_statements WHERE tenant_id=t AND statement_id=(binding->>'statement_id')::uuid;
 SELECT * INTO link FROM memoriesql.bead_semantic_statement_evidence WHERE tenant_id=t AND statement_id=st.statement_id
  AND evidence_source_unit_id=(binding->>'source_unit_id')::uuid;
 SELECT * INTO su FROM memoriesql.source_units WHERE tenant_id=t AND source_unit_id=link.evidence_source_unit_id;
 IF st.statement_id IS NULL OR link.statement_id IS NULL OR su.source_unit_id IS NULL
  OR link.evidence_content_hash IS DISTINCT FROM su.content_hash
  OR link.evidence_content_hash IS DISTINCT FROM source->>'content_sha256'
  OR NOT memoriesql.current_context_semantic_statement_authorized(t,st.workspace_id,st.access_scope_id,st.statement_id)
  OR NOT memoriesql.current_context_bead_version_authorized(t,st.workspace_id,st.access_scope_id,st.bead_version_id) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 records:=memoriesql.query_bead_records_v1(t,st.bead_id,known);
 IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(records) WHERE value->>'kind'='statement_evidence'
   AND value->>'id'=memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(st.statement_id,su.source_unit_id))) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 saved:=convert_from(decode(deps->>'records_base64','base64'),'UTF8')::jsonb;
 SELECT value INTO saved_package FROM jsonb_array_elements(saved)
  WHERE value->>'kind'='unit_package' AND value->>'id'=su.source_unit_id::text;
 SELECT value INTO current_package FROM jsonb_array_elements(records)
  WHERE value->>'kind'='unit_package' AND value->>'id'=su.source_unit_id::text;
 IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(saved)
   WHERE value->>'kind'='source_unit' AND value->>'id'=su.source_unit_id::text)
  OR saved_package IS DISTINCT FROM current_package THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('unit_ref',su.source_unit_id,'known_at',body#>'{frame,known_at}',
  'view',body#>'{frame,view}','records',records,
  'basis',jsonb_build_object('context','{"kind":"standalone"}'::jsonb,
   'checked_at',memoriesql.relation_packet_time(clock_timestamp()),
   'valid_until',memoriesql.relation_packet_time(q.expires_at),'end_condition','standalone_expiry','hold_ref',NULL));
END $$;
REVOKE ALL ON FUNCTION memoriesql.bind_query_source_text_v1(uuid,jsonb,uuid) FROM PUBLIC;

-- read_source_text: every entry is resolved and authorized before any interval,
-- size or byte diagnostic; one refusal withholds the whole reply. Only
-- available text is measured. Limits are UTF-8 content bytes, as hydration's.
CREATE FUNCTION memoriesql.prepare_query_source_text_v1(delivery uuid,request jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; entry jsonb; item jsonb; ordinal integer;
 resolved jsonb:='[]'; bound jsonb:='[]'; checked jsonb:='[]'; frames jsonb:='[]'; records jsonb;
 slices jsonb:='[]'; visible jsonb:='[]'; bases jsonb:='[]'; slice_labels jsonb:='[]'; labels jsonb:='[]';
 unit jsonb; served text; piece text; piece_bytes bytea; start_at integer; end_at integer; total integer;
 amount bigint:=0; complete boolean:=true; frame_key jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 IF jsonb_typeof(request->'entries') IS DISTINCT FROM 'array'
  OR jsonb_array_length(request->'entries') NOT BETWEEN 1 AND 8
  OR jsonb_typeof(request->'max_bytes') IS DISTINCT FROM 'number'
  OR (request->>'max_bytes')::numeric NOT BETWEEN 1 AND 65536 THEN
  RAISE EXCEPTION 'invalid_query_helper_binding' USING ERRCODE='22023'; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(request->'entries') LOOP
  resolved:=resolved||jsonb_build_array(memoriesql.resolve_query_source_text_v1(delivery,entry->'source',entry->'origin'));
 END LOOP;
 -- Each distinct delivering result's whole closure, once per call.
 FOR item IN SELECT DISTINCT value FROM jsonb_array_elements(resolved) LOOP
  PERFORM memoriesql.check_query_result_closure_v2((item->>'result_id')::uuid,item->>'content_digest');
  checked:=checked||jsonb_build_array(item);
 END LOOP;
 FOR ordinal IN 0..jsonb_array_length(resolved)-1 LOOP
  bound:=bound||jsonb_build_array(memoriesql.bind_query_source_text_v1(d.tenant_id,
   request#>ARRAY['entries',ordinal::text,'source'],(resolved#>>ARRAY[ordinal::text,'result_id'])::uuid));
 END LOOP;
 FOR ordinal IN 0..jsonb_array_length(bound)-1 LOOP
  entry:=request->'entries'->ordinal; item:=bound->ordinal;
  start_at:=(entry->>'start')::integer; end_at:=(entry->>'end')::integer;
  IF start_at<0 OR end_at<start_at OR end_at-start_at>16384 THEN
   RAISE EXCEPTION 'invalid_query_helper_interval' USING ERRCODE='22023'; END IF;
  unit:=memoriesql.query_unit_source_text_v1(d.tenant_id,(item->>'unit_ref')::uuid);
  served:=unit->>'text';
  IF served IS NULL THEN
   -- Unsupported text is never measured: no length, interval or byte check.
   slices:=slices||jsonb_build_array(jsonb_build_object('source',entry->'source',
    'frame',jsonb_build_object('known_at',item->'known_at','view',item->'view'),'text_state','unsupported',
    'start',start_at,'end',end_at,'unit_characters',NULL,'text',NULL,'returned_sha256',NULL));
   slice_labels:=slice_labels||'[[]]'::jsonb; complete:=false;
  ELSE
   total:=char_length(served);
   IF end_at>total THEN RAISE EXCEPTION 'invalid_query_helper_interval' USING ERRCODE='22023'; END IF;
   piece:=substr(served,start_at+1,end_at-start_at);
   piece_bytes:=convert_to(piece,'UTF8');
   IF octet_length(piece_bytes)>16384 THEN RAISE EXCEPTION 'invalid_query_helper_interval' USING ERRCODE='22023'; END IF;
   amount:=amount+octet_length(piece_bytes);
   slices:=slices||jsonb_build_array(jsonb_build_object('source',entry->'source',
    'frame',jsonb_build_object('known_at',item->'known_at','view',item->'view'),'text_state','available',
    'start',start_at,'end',end_at,'unit_characters',total,'text',piece,
    'returned_sha256',encode(sha256(piece_bytes),'hex')));
   slice_labels:=slice_labels||jsonb_build_array(unit->'labels');
   labels:=labels||(unit->'labels');
  END IF;
  visible:=visible||jsonb_build_array(entry->'source');
  bases:=bases||jsonb_build_array(item->'basis');
 END LOOP;
 IF amount>(request->>'max_bytes')::bigint THEN
  RAISE EXCEPTION 'query_helper_transport_exhausted' USING ERRCODE='54000'; END IF;
 -- The same 0037 records, retained per delivering frame, are re-authorized by
 -- the existing helper read on redelivery and cursor pages.
 FOR frame_key IN SELECT DISTINCT jsonb_build_object('known_at',value->'known_at','view',value->'view')
  FROM jsonb_array_elements(bound) LOOP
  SELECT COALESCE(jsonb_agg(r ORDER BY r->>'kind',r->>'id'),'[]') INTO records FROM (SELECT DISTINCT r
   FROM jsonb_array_elements(bound) b(v),jsonb_array_elements(b.v->'records') x(r)
   WHERE b.v->'known_at'=frame_key->'known_at' AND b.v->'view'=frame_key->'view') q;
  frames:=frames||jsonb_build_array(jsonb_build_object('known_at',frame_key->'known_at','view',frame_key->'view',
   'manifest',(SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',value->'kind','id',value->'id',
    'content_sha256',memoriesql.lifecycle_hash_v1(value->'row')) ORDER BY value->>'kind',value->>'id'),'[]')
    FROM jsonb_array_elements(records)),
   'records_base64',translate(encode(convert_to(memoriesql.lifecycle_canonical_json_v1((SELECT COALESCE(jsonb_agg(
    CASE WHEN value->>'kind'='unit_package' THEN jsonb_build_object('kind',value->'kind','id',value->'id',
     'content_sha256',memoriesql.lifecycle_hash_v1(value->'row')) ELSE value END
    ORDER BY value->>'kind',value->>'id'),'[]') FROM jsonb_array_elements(records))),'UTF8'),'base64'),E'\n','')));
 END LOOP;
 SELECT COALESCE(jsonb_agg(l ORDER BY l COLLATE "C"),'[]') INTO labels
  FROM (SELECT DISTINCT value#>>'{}' AS l FROM jsonb_array_elements(labels)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'ref',value->>'unit_ref',value->>'content_sha256'),'[]') INTO visible
  FROM (SELECT DISTINCT value FROM jsonb_array_elements(visible)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value-'checked_at'),'[]') INTO bases FROM (SELECT DISTINCT ON (value-'checked_at') value
  FROM jsonb_array_elements(bases) ORDER BY value-'checked_at',value->>'checked_at') x;
 RETURN jsonb_build_object('content',jsonb_build_object('kind','read_source_text',
  'payload',jsonb_build_object('source_text',jsonb_build_object('slices',slices,'source_text_labels',labels,
   'selection_complete',complete)),'refs',visible,'access_bases',bases),
  'witnesses',jsonb_build_object('slice_labels',slice_labels),
  'dependencies',jsonb_build_object('frames',frames,'raw_sources','[]'::jsonb,'parts','[]'::jsonb,'result_pins',checked),
  'checked_at',memoriesql.relation_packet_time(clock_timestamp()));
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_source_text_v1(uuid,jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.prepare_query_helper_v1(delivery uuid,request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 x memoriesql.query_helper_cursors%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; cursor_id uuid; packet jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 IF request->>'run_ref' IS DISTINCT FROM d.run_ref::text OR request->>'step_key' IS DISTINCT FROM d.step_key::text
  OR request->>'kind' IS DISTINCT FROM s.kind THEN
  RAISE EXCEPTION 'invalid_query_helper_binding' USING ERRCODE='22023'; END IF;
 IF request->'cursor'<>'null'::jsonb THEN
  BEGIN cursor_id:=(request->>'cursor')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN cursor_id:=NULL; END;
  SELECT * INTO x FROM memoriesql.query_helper_cursors WHERE tenant_id=d.tenant_id AND cursor_ref=cursor_id;
  -- query_helper_delivery checked the full owner tuple of this exact run.
  IF x.cursor_ref IS NOT NULL AND x.run_ref=d.run_ref
   AND x.scope_sha256=memoriesql.lifecycle_hash_v1(request-ARRAY['step_key','cursor']) THEN
   SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND artifact_ref=x.artifact_ref;
   SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND operation_ref=a.operation_ref;
   packet:=memoriesql.read_query_helper_artifact_v1(delivery,o.operation_ref,o.ownership_ref);
   IF packet#>>'{content,kind}' IS DISTINCT FROM s.kind OR x.content_digest IS DISTINCT FROM encode(sha256(a.content_bytes),'hex') THEN
    RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
   RETURN packet||jsonb_build_object('page_first',x.next_item);
  END IF;
  -- Invalid cursor diagnostics never bypass requested-target authorization.
  IF s.kind='inspect' THEN PERFORM memoriesql.prepare_query_inspection_v1(delivery,request||'{"cursor":null}'::jsonb);
  ELSIF s.kind='read_source_text' THEN PERFORM memoriesql.prepare_query_source_text_v1(delivery,request||'{"cursor":null}'::jsonb);
   -- read_source_text: this run's own cursor presented with a changed request.
   IF x.cursor_ref IS NOT NULL AND x.run_ref=d.run_ref THEN RAISE EXCEPTION 'invalid_query_helper_binding' USING ERRCODE='22023'; END IF;
  ELSE PERFORM memoriesql.prepare_query_hydration_v1(delivery,request||'{"cursor":null}'::jsonb); END IF;
  RAISE EXCEPTION 'query_helper_cursor_invalid' USING ERRCODE='22023';
 END IF;
 IF s.kind='inspect' THEN packet:=memoriesql.prepare_query_inspection_v1(delivery,request);
 ELSIF s.kind='read_source_text' THEN packet:=memoriesql.prepare_query_source_text_v1(delivery,request);
 ELSE packet:=memoriesql.prepare_query_hydration_v1(delivery,request); END IF;
 RETURN jsonb_set(packet,'{witnesses,request}',request);
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_helper_v1(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_query_helper_v1(uuid,jsonb) TO memoriesql_application;
