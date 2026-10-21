-- Forward helper operations use the existing run/delivery and preparation
-- ledgers. No raw authority, provider, deployment or policy is introduced.
-- Forward 0046 follows registered legacy7 restriction 0045 without changing it.
-- Reconciled with current AM-5 reads and original-frame reauthorization (0039/0044).
ALTER TABLE memoriesql.query_steps ADD COLUMN helper_artifact_ref uuid;
ALTER TABLE memoriesql.query_steps ADD COLUMN helper_page_first bigint CHECK(helper_page_first IS NULL OR helper_page_first>0);
ALTER TABLE memoriesql.query_steps ADD COLUMN helper_page_count integer CHECK(helper_page_count IS NULL OR helper_page_count>=0);
ALTER TABLE memoriesql.query_steps ADD COLUMN helper_page_cursor uuid;
-- Cursor steps share the original artifact. Existing run purge clears all step
-- pins in one transaction; defer this check across its per-row cleanup triggers.
ALTER TABLE memoriesql.query_steps ADD FOREIGN KEY(tenant_id,helper_artifact_ref)
 REFERENCES memoriesql.result_preparation_artifacts(tenant_id,artifact_ref)
 DEFERRABLE INITIALLY DEFERRED;
-- Find the two exact constraint subjects, rather than guessing PostgreSQL's
-- generated check names. All other historical checks remain unchanged.
DO $$ DECLARE constraint_row record; kind_column smallint; state_column smallint; result_column smallint;
BEGIN
 SELECT attnum INTO kind_column FROM pg_attribute WHERE attrelid='memoriesql.query_steps'::regclass AND attname='kind';
 SELECT attnum INTO state_column FROM pg_attribute WHERE attrelid='memoriesql.query_steps'::regclass AND attname='state';
 SELECT attnum INTO result_column FROM pg_attribute WHERE attrelid='memoriesql.query_steps'::regclass AND attname='result_id';
 FOR constraint_row IN SELECT conname FROM pg_constraint WHERE conrelid='memoriesql.query_steps'::regclass AND contype='c'
  AND (conkey=ARRAY[kind_column] OR (state_column=ANY(conkey) AND result_column=ANY(conkey))) LOOP
  EXECUTE format('ALTER TABLE memoriesql.query_steps DROP CONSTRAINT %I',constraint_row.conname);
 END LOOP;
END $$;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_kind_check
 CHECK(kind IN ('query','reuse_result','inspect','hydrate_source'));
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_complete_check CHECK(
 (state='complete')=(
  (kind IN ('query','reuse_result') AND helper_artifact_ref IS NULL AND result_id IS NOT NULL
   AND (purged_at IS NOT NULL OR (content_digest IS NOT NULL AND first_row IS NOT NULL AND row_count IS NOT NULL AND page_size IS NOT NULL)))
  OR (kind IN ('inspect','hydrate_source') AND state='complete' AND result_id IS NULL AND first_row IS NULL AND row_count IS NULL AND page_size IS NULL
   AND (purged_at IS NOT NULL OR (helper_artifact_ref IS NOT NULL AND content_digest IS NOT NULL
    AND helper_page_first IS NOT NULL AND helper_page_count IS NOT NULL)))));
ALTER TABLE memoriesql.query_disclosures ALTER COLUMN result_id DROP NOT NULL;
-- Helper cursors point to actual helper artifacts, not fabricated result IDs.
-- Ownership is the existing run's complete principal/credential-independent tuple.
CREATE TABLE memoriesql.query_helper_cursors (
 tenant_id uuid NOT NULL, cursor_ref uuid NOT NULL, run_ref uuid NOT NULL,
 artifact_ref uuid NOT NULL, content_digest text NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 scope_sha256 text NOT NULL CHECK(scope_sha256 ~ '^[a-f0-9]{64}$'),
 next_item bigint NOT NULL CHECK(next_item>1), issued_by_delivery uuid NOT NULL,
 PRIMARY KEY(tenant_id,cursor_ref),
 FOREIGN KEY(tenant_id,run_ref) REFERENCES memoriesql.query_runs(tenant_id,run_ref),
 FOREIGN KEY(tenant_id,artifact_ref) REFERENCES memoriesql.result_preparation_artifacts(tenant_id,artifact_ref),
 FOREIGN KEY(tenant_id,issued_by_delivery) REFERENCES memoriesql.query_deliveries(tenant_id,delivery_ref)
);
ALTER TABLE memoriesql.query_helper_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_helper_cursors FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_helper_cursors FROM PUBLIC,memoriesql_application;

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
 IF run IS NULL OR step IS NULL OR requested_kind NOT IN ('query','reuse_result','inspect','hydrate_source')
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
REVOKE ALL ON FUNCTION memoriesql.admit_query_delivery_v1(uuid,uuid,text,text,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.admit_query_delivery_v1(uuid,uuid,text,text,integer,integer) TO memoriesql_application;


-- One owner/current-run/deadline fence for helper reads and publication.
CREATE FUNCTION memoriesql.query_helper_delivery_v1(delivery uuid)
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
  OR s.kind NOT IN ('inspect','hydrate_source') OR s.purged_at IS NOT NULL
  OR EXISTS(SELECT 1 FROM memoriesql.query_run_closures WHERE tenant_id=c.tenant_id AND run_ref=r.run_ref) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF d.state<>'reserved' THEN RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000'; END IF;
 IF clock_timestamp()>=LEAST(d.deadline,r.expires_at) THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000'; END IF;
 RETURN d;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_helper_delivery_v1(uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.read_query_helper_artifact_v1(delivery uuid,operation uuid,ownership uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 body jsonb; deps jsonb; current_records jsonb; saved_records jsonb; record_pin jsonb; actual_record jsonb; item jsonb;
 records_text text; pinned_records jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND operation_ref=operation;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND operation_ref=operation;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=o.run_ref AND step_key=o.step_key;
 IF o.operation_ref IS NULL OR a.artifact_ref IS NULL OR o.ownership_ref IS DISTINCT FROM ownership
  OR o.run_ref<>d.run_ref OR o.state<>'sealed'
  OR (o.step_key<>d.step_key AND (s.state<>'complete' OR s.helper_artifact_ref IS DISTINCT FROM a.artifact_ref))
  OR o.request_fingerprint IS DISTINCT FROM s.request_fingerprint
  OR a.artifact_sha256 IS DISTINCT FROM encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
   int8send(octet_length(a.content_bytes)::bigint)||a.content_bytes||
   int8send(octet_length(a.witness_bytes)::bigint)||a.witness_bytes||
   int8send(octet_length(a.dependency_bytes)::bigint)||a.dependency_bytes),'hex') THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb; deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 IF body->>'kind' IS DISTINCT FROM s.kind OR jsonb_typeof(deps->'frames') IS DISTINCT FROM 'array'
  OR jsonb_typeof(deps->'raw_sources') IS DISTINCT FROM 'array'
  OR jsonb_typeof(deps->'result_pins') IS DISTINCT FROM 'array'
  OR jsonb_typeof(deps->'parts') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(deps->'frames') LOOP
  IF jsonb_typeof(item->'manifest') IS DISTINCT FROM 'array' OR jsonb_typeof(item->'records_base64') IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  records_text:=convert_from(decode(item->>'records_base64','base64'),'UTF8');
  pinned_records:=records_text::jsonb;
  IF jsonb_typeof(pinned_records) IS DISTINCT FROM 'array'
   OR records_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(pinned_records) THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  saved_records:='[]';
  FOR record_pin IN SELECT value FROM jsonb_array_elements(pinned_records) LOOP
   IF record_pin->>'kind'='unit_package' THEN
    -- This original immutable package is protected provenance. Store only its
    -- fixed-width digest; do not charge an agent for hidden raw metadata size.
    SELECT value INTO actual_record FROM jsonb_array_elements(memoriesql.query_unit_records_v1(
     d.tenant_id,(record_pin->>'id')::uuid,(item->>'known_at')::timestamptz))
     WHERE value->>'kind'='unit_package' AND value->'id'=record_pin->'id';
    IF actual_record IS NULL OR memoriesql.lifecycle_hash_v1(actual_record->'row') IS DISTINCT FROM record_pin->>'content_sha256' THEN
     RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
   ELSE actual_record:=record_pin; END IF;
   saved_records:=saved_records||jsonb_build_array(actual_record);
  END LOOP;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',value->'kind','id',value->'id',
   'content_sha256',memoriesql.lifecycle_hash_v1(value->'row')) ORDER BY value->>'kind',value->>'id'),'[]')
   INTO current_records FROM jsonb_array_elements(saved_records);
  IF current_records IS DISTINCT FROM item->'manifest' THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  -- Reauthorize exactly the retained semantic frame. A new earlier-stamped
  -- commit does not cause rediscovery, new head selection or new contributors.
  PERFORM memoriesql.query_saved_records_authorized_v1(saved_records,(item->>'known_at')::timestamptz);
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(deps->'result_pins') LOOP
  PERFORM memoriesql.check_query_result_closure_v2((item->>'result_id')::uuid,item->>'content_digest');
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(deps->'raw_sources') LOOP
  PERFORM memoriesql.revisiting_source_authorize((item#>>'{}')::uuid);
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(deps->'parts') LOOP
  IF NOT EXISTS(SELECT 1 FROM memoriesql.evidence_package_parts pp JOIN memoriesql.evidence_packages p
    ON p.tenant_id=pp.tenant_id AND p.package_id=pp.package_id
    WHERE pp.tenant_id=d.tenant_id AND pp.part_id=(item->>'part_id')::uuid
     AND p.package_id=(item->>'package_id')::uuid AND p.inventory_hash=item->>'inventory_sha256'
     AND p.sealed_receipt_id IS NOT NULL AND pp.inventory=item->'inventory') THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 END LOOP;
 PERFORM memoriesql.query_helper_delivery_v1(delivery);
 RETURN jsonb_build_object('artifact_ref',a.artifact_ref,'content',body,
  'witnesses',convert_from(a.witness_bytes,'UTF8')::jsonb,'dependencies',deps,
  'retained_bytes',o.allocation_bytes,'checked_at',memoriesql.relation_packet_time(clock_timestamp()));
END $$;
REVOKE ALL ON FUNCTION memoriesql.read_query_helper_artifact_v1(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_query_helper_artifact_v1(uuid,uuid,uuid) TO memoriesql_application;

CREATE FUNCTION memoriesql.record_query_helper_disclosure_v1(delivery uuid,artifact uuid,digest text,
 delivered jsonb,response_hash text,transport_bytes bigint,observed_ms bigint,checked timestamptz,
 page_first bigint,page_count integer,issued_cursor uuid,next_item bigint)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 c memoriesql.authorization_contexts%ROWTYPE; verified jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND artifact_ref=artifact;
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND operation_ref=a.operation_ref;
 -- The trusted executor performed the whole-closure read in this SAME
 -- authority-fenced transaction. Match M0038's receipt boundary rather than
 -- silently doing a second unmeasured population/closure pass after sizing.
 IF o.run_ref IS DISTINCT FROM d.run_ref OR o.state IS DISTINCT FROM 'sealed' OR a.artifact_ref IS NULL THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 verified:=jsonb_build_object('content',convert_from(a.content_bytes,'UTF8')::jsonb,
  'witnesses',convert_from(a.witness_bytes,'UTF8')::jsonb);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements((convert_from(a.dependency_bytes,'UTF8')::jsonb)->'result_pins') x(pin)
  LEFT JOIN memoriesql.query_result_creations q ON q.tenant_id=d.tenant_id AND q.result_id=(pin->>'result_id')::uuid
  WHERE q.result_id IS NULL OR q.content_digest IS DISTINCT FROM pin->>'content_digest' OR clock_timestamp()>=q.expires_at) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF digest IS DISTINCT FROM encode(sha256(a.content_bytes),'hex')
  OR jsonb_typeof(delivered) IS DISTINCT FROM 'array' OR response_hash IS NULL OR response_hash !~ '^[a-f0-9]{64}$'
  OR transport_bytes IS NULL OR transport_bytes<0 OR transport_bytes>d.reserved_transport
  OR observed_ms IS NULL OR observed_ms<0 OR checked IS NULL
  OR page_first IS NULL OR page_first<1 OR page_count IS NULL OR page_count<0
  OR (issued_cursor IS NULL)<>(next_item IS NULL)
  OR (next_item IS NOT NULL AND (page_count=0 OR next_item<>page_first+page_count)) THEN
  RAISE EXCEPTION 'invalid_query_helper_disclosure' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT value::jsonb FROM jsonb_array_elements_text(delivered) EXCEPT ALL
   SELECT value FROM jsonb_array_elements(verified#>'{content,refs}')) OR
  jsonb_typeof(verified#>'{content,refs}') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'invalid_query_helper_visibility' USING ERRCODE='22023'; END IF;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 IF verified#>>'{content,kind}' IS DISTINCT FROM s.kind THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF s.state='executing' THEN
  UPDATE memoriesql.query_steps SET state='complete',helper_artifact_ref=artifact,content_digest=digest,
   helper_page_first=page_first,helper_page_count=page_count,helper_page_cursor=issued_cursor,completed_at=clock_timestamp()
   WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 ELSIF s.state<>'complete' OR s.helper_artifact_ref IS DISTINCT FROM artifact OR s.content_digest IS DISTINCT FROM digest
  OR s.helper_page_first IS DISTINCT FROM page_first OR s.helper_page_count IS DISTINCT FROM page_count
  OR s.helper_page_cursor IS DISTINCT FROM issued_cursor THEN
  RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
 IF issued_cursor IS NOT NULL THEN
  INSERT INTO memoriesql.query_helper_cursors VALUES(d.tenant_id,issued_cursor,d.run_ref,artifact,digest,
   memoriesql.lifecycle_hash_v1((verified#>'{witnesses,request}')-ARRAY['step_key','cursor']),next_item,delivery)
   ON CONFLICT(tenant_id,cursor_ref) DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM memoriesql.query_helper_cursors x WHERE x.tenant_id=d.tenant_id
   AND x.cursor_ref=issued_cursor AND x.run_ref=d.run_ref AND x.artifact_ref=artifact
   AND x.content_digest=digest AND x.next_item=record_query_helper_disclosure_v1.next_item
   AND x.scope_sha256=memoriesql.lifecycle_hash_v1((verified#>'{witnesses,request}')-ARRAY['step_key','cursor'])) THEN
   RAISE EXCEPTION 'invalid_query_helper_cursor_binding' USING ERRCODE='22023'; END IF;
 END IF;
 INSERT INTO memoriesql.query_disclosures VALUES(d.tenant_id,delivery,NULL,digest,'{"kind":"standalone"}'::jsonb,
  1,0,response_hash,delivered,checked,c.credential_id);
 INSERT INTO memoriesql.query_visible_refs SELECT d.tenant_id,d.run_ref,value,delivery
  FROM jsonb_array_elements_text(delivered) ON CONFLICT DO NOTHING;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='available',charged_db_ms=observed_ms,
  charged_transport=transport_bytes,response_sha256=response_hash,settled_at=clock_timestamp()
  WHERE tenant_id=d.tenant_id AND delivery_ref=delivery;
 RETURN jsonb_build_object('state','settled');
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_query_helper_disclosure_v1(uuid,uuid,text,jsonb,text,bigint,bigint,timestamptz,bigint,integer,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_query_helper_disclosure_v1(uuid,uuid,text,jsonb,text,bigint,bigint,timestamptz,bigint,integer,uuid,bigint) TO memoriesql_application;

-- Reuse existing run expiry and allocation reclamation. There is no helper
-- lifetime or retained content outside the original run's cleanup path.
CREATE FUNCTION memoriesql.purge_query_helper_step_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE op memoriesql.result_preparation_operations%ROWTYPE;
BEGIN
 IF NEW.kind NOT IN ('inspect','hydrate_source') OR NEW.purged_at IS NULL OR OLD.purged_at IS NOT NULL THEN
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
CREATE TRIGGER query_helper_clear_pin BEFORE UPDATE OF purged_at ON memoriesql.query_steps
 FOR EACH ROW EXECUTE FUNCTION memoriesql.purge_query_helper_step_v1();
CREATE TRIGGER query_helper_purge AFTER UPDATE OF purged_at ON memoriesql.query_steps
 FOR EACH ROW EXECUTE FUNCTION memoriesql.purge_query_helper_step_v1();

-- A pin is issued only for an actual accepted statement/unit support edge.
-- Package/part provenance stays raw-authorized; a unit citation is not a part.
CREATE FUNCTION memoriesql.query_helper_evidence_pin_v1(t uuid,evidence uuid,statement_ref uuid,unit_ref uuid,part_ref uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE st memoriesql.bead_semantic_statements%ROWTYPE; su memoriesql.source_units%ROWTYPE;
 link memoriesql.bead_semantic_statement_evidence%ROWTYPE; m memoriesql.logical_unit_materializations%ROWTYPE;
 p memoriesql.evidence_packages%ROWTYPE; pp memoriesql.evidence_package_parts%ROWTYPE;
 pin jsonb; part_pin jsonb; native jsonb;
BEGIN
 SELECT * INTO st FROM memoriesql.bead_semantic_statements WHERE tenant_id=t AND statement_id=statement_ref;
 SELECT * INTO link FROM memoriesql.bead_semantic_statement_evidence WHERE tenant_id=t AND statement_id=statement_ref AND evidence_source_unit_id=unit_ref;
 SELECT * INTO su FROM memoriesql.source_units WHERE tenant_id=t AND source_unit_id=unit_ref;
 IF st.statement_id IS NULL OR link.statement_id IS NULL OR su.source_unit_id IS NULL
  OR link.evidence_content_hash<>su.content_hash
  OR NOT memoriesql.current_context_bead_version_authorized(t,st.workspace_id,st.access_scope_id,st.bead_version_id)
  OR NOT memoriesql.current_context_semantic_statement_authorized(t,st.workspace_id,st.access_scope_id,st.statement_id) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.query_unit_records_v1(t,unit_ref,clock_timestamp());
 SELECT * INTO m FROM memoriesql.logical_unit_materializations WHERE tenant_id=t AND source_unit_id=unit_ref;
 SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=t AND package_id=m.package_id;
 IF p.package_id IS NOT NULL THEN
  PERFORM memoriesql.revisiting_source_authorize(p.source_object_id);
  IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM m.package_pin->>'inventory_sha256' THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,
   'inventory_sha256',p.inventory_hash,'required_parts',m.package_pin->'required_parts',
   'required_characters',m.package_pin->'required_characters','required_utf8_bytes',m.package_pin->'required_utf8_bytes',
   'source_revision_key',p.declaration->'source_revision_key','occurrence_key',p.declaration->'occurrence_key',
   'occurrence_identity_basis',p.declaration->'occurrence_identity_basis','package_revision',p.declaration->'package_revision');
  IF part_ref IS NOT NULL THEN
   SELECT * INTO pp FROM memoriesql.evidence_package_parts WHERE tenant_id=t AND package_id=p.package_id AND part_id=part_ref;
   IF pp.part_id IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
   part_pin:=pp.inventory-ARRAY(SELECT attrs.key FROM jsonb_object_keys(pp.inventory) AS attrs(key)
    WHERE attrs.key<>ALL(ARRAY['part_id','ordinal','component_key','component_offset','parent_component_key','kind','content_sha256','characters','utf8_bytes','derivation','lineage']));
  END IF;
  IF part_pin IS NOT NULL THEN
   part_pin:=jsonb_build_object('parent_component_key',NULL)||part_pin;
   part_pin:=jsonb_set(part_pin,'{lineage}',(SELECT COALESCE(jsonb_agg(
    jsonb_build_object('fold_receipt_id',NULL,'fold_outcome_ordinal',NULL)||value),'[]') FROM jsonb_array_elements(part_pin->'lineage')));
  END IF;
  native:=jsonb_build_object('native_id',NULL,'parent_native_id',NULL,'session_native_id',NULL,
   'branch_native_id',NULL,'participant_native_id',NULL,'role',NULL,'source_order',NULL,
   'occurred_at',NULL,'occurred_at_raw',NULL,'time_precision',NULL)||(p.declaration->'native');
  IF native->'occurred_at'<>'null'::jsonb THEN
   native:=jsonb_set(native,'{occurred_at}',to_jsonb(memoriesql.relation_packet_time((native->>'occurred_at')::timestamptz))); END IF;
 ELSE
  IF part_ref IS NOT NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  native:=jsonb_build_object('native_id',NULL,'parent_native_id',NULL,'session_native_id',NULL,
   'branch_native_id',NULL,'participant_native_id',NULL,'role',NULL,'source_order',NULL,
   'occurred_at',NULL,'occurred_at_raw',NULL,'time_precision',NULL);
 END IF;
 RETURN jsonb_build_object('ref',evidence,'bead_id',st.bead_id,'bead_version_id',st.bead_version_id,
  'statement_id',st.statement_id,'unit',jsonb_build_object('event_id',link.evidence_event_id,'source_unit_id',unit_ref,'content_sha256',link.evidence_content_hash),
  'package',pin,'part',part_pin,'native',native,'qualification',p.declaration->'qualification',
  'normalization_policy_version',p.declaration->'normalization_policy_version');
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_helper_evidence_pin_v1(uuid,uuid,uuid,uuid,uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.prepare_query_inspection_v1(delivery uuid,request jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; c memoriesql.authorization_contexts%ROWTYPE;
 v memoriesql.bead_versions%ROWTYPE; receipt memoriesql.semantic_task_receipts%ROWTYPE;
 task memoriesql.semantic_tasks%ROWTYPE; ir memoriesql.idempotency_receipts%ROWTYPE;
 population jsonb; records jsonb:='[]'; neighbours jsonb; item jsonb; target jsonb; observation jsonb;
 observations jsonb:='[]'; corrections jsonb:='[]'; relations jsonb:='[]'; types jsonb:='[]'; pairs jsonb:='[]';
 units jsonb:='[]'; pins jsonb:='[]'; visible jsonb:='[]'; raw_sources jsonb:='[]'; parts jsonb:='[]';
 statements jsonb; mentions jsonb; run_label text; type_row jsonb; result_metadata jsonb; provenance jsonb;
 saved_artifact memoriesql.result_preparation_artifacts%ROWTYPE; saved_body jsonb; native_graph jsonb; original_records jsonb;
 result_pins jsonb:='[]'; bases jsonb:='[]'; page jsonb; result_pin jsonb; pin jsonb; part_row record; support record;
 known timestamptz:=(request->>'known_at')::timestamptz; view_mode text:=request->>'view'; pair jsonb;
 relation_mode text; relation_task uuid;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 -- Match current query population and retained-frame closure modes. The old v1
 -- relation wrapper always requests owner authority, even for paired agents.
 relation_mode:=CASE WHEN memoriesql.current_context_has_capability('source.raw.read') THEN 'owner'
  WHEN c.principal_kind='agent' AND c.pairing_grant_id IS NOT NULL THEN 'agent' ELSE 'owner' END;
 IF known IS NULL OR NOT isfinite(known) OR known>statement_timestamp() OR view_mode NOT IN ('resolved','historical') THEN
  RAISE EXCEPTION 'invalid_query_helper_frame' USING ERRCODE='22023'; END IF;
 -- All explicitly requested identities are authorized before population/budgets.
 FOR target IN SELECT value FROM jsonb_array_elements(request->'observation_refs') LOOP
  PERFORM memoriesql.query_bead_records_v1(c.tenant_id,(target->>'bead_id')::uuid,known);
  IF NOT EXISTS(SELECT 1 FROM memoriesql.accepted_bead_semantics a JOIN memoriesql.bead_versions b USING(tenant_id,bead_version_id)
   WHERE a.tenant_id=c.tenant_id AND a.bead_id=(target->>'bead_id')::uuid
    AND a.bead_version_id=(target->>'bead_version_id')::uuid AND b.authored_at<=known) THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  -- The population withholds a whole correction family if either neighbour is
  -- denied. Check the explicitly requested family's SAME dependencies first,
  -- so an unrelated population budget cannot mask a protected neighbour.
  FOR support IN SELECT s.bead_id,s.superseded_bead_id FROM memoriesql.bead_supersessions s
   JOIN memoriesql.bead_versions sv ON sv.tenant_id=s.tenant_id AND sv.bead_version_id=s.bead_version_id
   WHERE s.tenant_id=c.tenant_id AND sv.authored_at<=known
    AND ((s.superseded_bead_id=(target->>'bead_id')::uuid AND s.superseded_bead_version_id=(target->>'bead_version_id')::uuid)
     OR (s.bead_id=(target->>'bead_id')::uuid AND s.bead_version_id=(target->>'bead_version_id')::uuid)) LOOP
   PERFORM memoriesql.query_bead_records_v1(c.tenant_id,support.bead_id,known);
   PERFORM memoriesql.query_bead_records_v1(c.tenant_id,support.superseded_bead_id,known);
  END LOOP;
  -- Facet dependencies are authorized for ALL targets before population sizing
  -- or any more specific frame/provenance diagnostic. A denied later target
  -- cannot become a transport/storage refusal that reveals an earlier subset.
  IF request->'facets' ? 'content' AND (
   EXISTS(SELECT 1 FROM memoriesql.entity_mention_resolutions x JOIN memoriesql.entity_mentions m
    ON m.tenant_id=x.tenant_id AND m.entity_mention_id=x.entity_mention_id
    WHERE m.tenant_id=c.tenant_id AND m.bead_version_id=(target->>'bead_version_id')::uuid AND x.decided_at<=known)
   OR EXISTS(SELECT 1 FROM memoriesql.topic_mentions m WHERE m.tenant_id=c.tenant_id AND m.bead_version_id=(target->>'bead_version_id')::uuid AND m.recorded_at<=known)
   OR EXISTS(SELECT 1 FROM memoriesql.bead_topic_links m WHERE m.tenant_id=c.tenant_id AND m.bead_version_id=(target->>'bead_version_id')::uuid AND m.accepted_at<=known)) THEN
   -- An unrepresented association may itself be protected. No facet diagnostic.
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  IF request->'facets' ? 'evidence_metadata' THEN
   FOR support IN SELECT DISTINCT ev.source_object_id FROM memoriesql.bead_semantic_statements st
    JOIN memoriesql.bead_semantic_statement_evidence e ON e.tenant_id=st.tenant_id AND e.statement_id=st.statement_id
    JOIN memoriesql.source_events ev ON ev.tenant_id=e.tenant_id AND ev.event_id=e.evidence_event_id
    WHERE st.tenant_id=c.tenant_id AND st.bead_version_id=(target->>'bead_version_id')::uuid
    UNION SELECT DISTINCT p.source_object_id FROM memoriesql.bead_semantic_statements st
    JOIN memoriesql.bead_semantic_statement_evidence e ON e.tenant_id=st.tenant_id AND e.statement_id=st.statement_id
    JOIN memoriesql.logical_unit_materializations m ON m.tenant_id=e.tenant_id AND m.source_unit_id=e.evidence_source_unit_id
    JOIN memoriesql.evidence_packages p ON p.tenant_id=m.tenant_id AND p.package_id=m.package_id
    WHERE st.tenant_id=c.tenant_id AND st.bead_version_id=(target->>'bead_version_id')::uuid LOOP
    PERFORM memoriesql.revisiting_source_authorize(support.source_object_id);
   END LOOP;
  END IF;
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(request->'relation_refs') LOOP
  PERFORM memoriesql.relation_assertion_records_v2(c.tenant_id,'assessed',(item#>>'{}')::uuid,known,relation_mode);
  -- Lifecycle/pair facets retain the existing owner-only whole-task boundary.
  -- Authorize it for EVERY target before any population sizing or diagnostic.
  IF request->'facets' ? 'lifecycle' THEN
   IF NOT memoriesql.current_context_has_capability('source.raw.read') THEN
    RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
   SELECT task_id INTO relation_task FROM memoriesql.assessed_relations
    WHERE tenant_id=c.tenant_id AND relation_id=(item#>>'{}')::uuid;
   PERFORM memoriesql.relation_task_population_records_v1(c.tenant_id,relation_task,known,'owner');
   FOR support IN SELECT relation_id FROM memoriesql.assessed_relations a
    WHERE a.tenant_id=c.tenant_id AND a.task_id=relation_task AND a.recorded_at<=known LOOP
    PERFORM memoriesql.relation_assertion_records_v2(c.tenant_id,'assessed',support.relation_id,known,'owner');
   END LOOP;
  END IF;
 END LOOP;
 IF request->'result'<>'null'::jsonb THEN
  result_pin:=request#>'{result,pin}';
  PERFORM memoriesql.check_query_result_closure_v2((result_pin->>'result_id')::uuid,result_pin->>'content_digest');
  IF request->'provenance_ref'<>'null'::jsonb THEN
   SELECT * INTO saved_artifact FROM memoriesql.result_preparation_artifacts
    WHERE tenant_id=c.tenant_id AND artifact_ref=(result_pin->>'result_id')::uuid;
   original_records:=convert_from(decode((convert_from(saved_artifact.dependency_bytes,'UTF8')::jsonb)->>'records_base64','base64'),'UTF8')::jsonb;
   FOR item IN SELECT DISTINCT value#>'{row,source_object_id}' FROM jsonb_array_elements(original_records)
    WHERE value->>'kind'='source_event' LOOP
    PERFORM memoriesql.revisiting_source_authorize((item#>>'{}')::uuid);
    raw_sources:=raw_sources||jsonb_build_array(item);
   END LOOP;
  END IF;
  IF request#>>'{result,access,kind}'<>'standalone' THEN
   RAISE EXCEPTION 'helper_checkpoint_access_unimplemented' USING ERRCODE='0A000'; END IF;
  page:=memoriesql.read_query_result_page_v1(delivery,(result_pin->>'result_id')::uuid,result_pin->>'content_digest',1,1);
  result_metadata:=jsonb_build_object('result_id',result_pin->'result_id','content_digest',result_pin->'content_digest',
   'originating_run_ref',page->'originating_run_ref','created_at',page->'created_at','expires_at',page->'expires_at',
   'catalog_hash',page#>'{metadata,catalog_hash}','policy_hash',page#>'{metadata,policy_hash}',
   'result_schema',page#>'{metadata,result_schema}','frame',page#>'{metadata,wire_frame}','parents','[]'::jsonb,
   'query_ref',page#>'{metadata,query_ref}','lineage_ref',page#>'{metadata,lineage_ref}',
   'coverage',page#>'{metadata,coverage}','order_basis',page#>'{metadata,order_basis}','total_rows',page#>'{metadata,total_rows}');
  IF page#>>'{metadata,wire_frame,view}' IS DISTINCT FROM view_mode
   OR (page#>>'{metadata,wire_frame,known_at}')::timestamptz IS DISTINCT FROM known THEN
   RAISE EXCEPTION 'invalid_query_helper_frame' USING ERRCODE='22023'; END IF;
  result_pins:=jsonb_build_array(result_pin);
  bases:=jsonb_build_array(jsonb_build_object('context',request#>'{result,access}','checked_at',page->'checked_at',
   'valid_until',page->'expires_at','end_condition','standalone_expiry','hold_ref',NULL));
 END IF;
 IF request->'provenance_ref'<>'null'::jsonb THEN
  SELECT * INTO saved_artifact FROM memoriesql.result_preparation_artifacts
   WHERE tenant_id=c.tenant_id AND artifact_ref=(result_pin->>'result_id')::uuid;
  saved_body:=convert_from(saved_artifact.content_bytes,'UTF8')::jsonb;
  native_graph:=convert_from(saved_artifact.witness_bytes,'UTF8')::jsonb;
  -- The same existing UUID selector resolves a retained node or member, always
  -- under the explicit original result pin. No new lookup endpoint or row body
  -- is invented; a member reply supplies its actual typed key/frame association.
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(native_graph->'nodes')
    WHERE value->>'node_ref'=request->>'provenance_ref')
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(native_graph->'members')
    WHERE value->>'member_ref'=request->>'provenance_ref') THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  IF NOT (request->'facets' ? 'provenance') THEN native_graph:=NULL; END IF;
 END IF;
 -- A saved result's original snapshot and a new population at the same cutoff
 -- need not contain the same commits. Do not silently combine those frames.
 IF result_pin IS NOT NULL AND (jsonb_array_length(request->'observation_refs')>0 OR jsonb_array_length(request->'relation_refs')>0) THEN
  RAISE EXCEPTION 'helper_mixed_saved_frame_unrepresented' USING ERRCODE='0A000'; END IF;
 -- The closed Assertion is a content object. Until the other targeted
 -- relation-only facets have their own faithful association, explicitly refuse
 -- instead of returning unrequested assertion bodies or an empty success.
 IF (jsonb_array_length(request->'relation_refs')>0 AND NOT (request->'facets' ? 'content'))
  OR (jsonb_array_length(request->'observation_refs')>0 AND request->'facets' ? 'provenance'
   AND NOT (request->'facets' ? 'content')) THEN
  RAISE EXCEPTION 'helper_target_facet_unimplemented' USING ERRCODE='0A000'; END IF;
 population:=memoriesql.prepare_query_sql_population_v2(known,view_mode,67108864);
 FOR target IN SELECT value FROM jsonb_array_elements(request->'observation_refs') LOOP
  SELECT value INTO observation FROM jsonb_array_elements(population->'observations')
   WHERE value->>'bead_id'=target->>'bead_id' AND value->>'bead_version_id'=target->>'bead_version_id';
  IF observation IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  records:=records||memoriesql.query_bead_records_v1(c.tenant_id,(target->>'bead_id')::uuid,known);
  -- Use the canonical correction rows and retain both sides as dependencies.
  FOR item IN SELECT value FROM jsonb_array_elements(population->'observation_corrections')
   WHERE value->>'predecessor_version_id'=target->>'bead_version_id' OR value->>'successor_version_id'=target->>'bead_version_id' LOOP
   records:=records||memoriesql.query_bead_records_v1(c.tenant_id,(item->>'predecessor_bead_id')::uuid,known)
    ||memoriesql.query_bead_records_v1(c.tenant_id,(item->>'successor_bead_id')::uuid,known);
   SELECT value INTO neighbours FROM jsonb_array_elements((population->>'dependency_records_json')::jsonb)
    WHERE value->>'kind'='bead_supersession' AND value->'row'=item;
   IF neighbours IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
   records:=records||jsonb_build_array(neighbours);
   IF request->'facets' ? 'lifecycle' THEN
    corrections:=corrections||jsonb_build_array(item);
    visible:=visible||jsonb_build_array(jsonb_build_object('kind','observation','ref',item->'predecessor_bead_id','version_ref',item->'predecessor_version_id'),
     jsonb_build_object('kind','observation','ref',item->'successor_bead_id','version_ref',item->'successor_version_id'));
   END IF;
  END LOOP;
  IF request->'facets' ? 'content' THEN
   SELECT * INTO v FROM memoriesql.bead_versions WHERE tenant_id=c.tenant_id AND bead_version_id=(target->>'bead_version_id')::uuid;
   SELECT * INTO receipt FROM memoriesql.semantic_task_receipts WHERE tenant_id=c.tenant_id AND semantic_task_receipt_id=v.semantic_task_receipt_id;
   SELECT * INTO ir FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=receipt.idempotency_receipt_id;
   SELECT * INTO task FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=ir.resource_id;
   SELECT run_id INTO run_label FROM memoriesql.semantic_task_runs WHERE tenant_id=c.tenant_id AND task_id=task.task_id
    AND attempt_id=task.result_attempt_id AND parent_run_id IS NULL;
   IF task.task_id IS NULL OR task.result_attempt_id IS NULL OR run_label IS NULL THEN
    RAISE EXCEPTION 'helper_authorship_unrepresented' USING ERRCODE='0A000'; END IF;
   SELECT jsonb_build_object('key',bt.stable_key,'revision_id',tr.bead_type_revision_id,'definition',tr.definition) INTO type_row
    FROM memoriesql.bead_type_revisions tr JOIN memoriesql.bead_types bt USING(bead_type_id) WHERE tr.bead_type_revision_id=v.bead_type_revision_id;
   SELECT COALESCE(jsonb_agg(value||jsonb_build_object('sequence',value->>'sequence') ORDER BY (value->>'sequence')::bigint),'[]') INTO statements
    FROM jsonb_array_elements(population->'statements') WHERE value->>'bead_version_id'=target->>'bead_version_id';
   -- Local authored identity is not a guessed global entity resolution.
   SELECT COALESCE(jsonb_agg(jsonb_build_object('mention_id',m.entity_mention_id,'bead_id',m.bead_id,'bead_version_id',m.bead_version_id,
    'surface_text',m.surface_text,'local_state',m.local_identity_state,'local_reason',m.local_identity_reason,
    'resolution_state','unsupported','resolution_id',NULL,'recorded_at',memoriesql.relation_packet_time(m.recorded_at)) ORDER BY m.entity_mention_id),'[]') INTO mentions
    FROM memoriesql.entity_mentions m WHERE m.tenant_id=c.tenant_id AND m.bead_version_id=v.bead_version_id AND m.recorded_at<=known;
   observations:=observations||jsonb_build_array(jsonb_build_object('observation',observation,'statements',statements,
    'render',CASE WHEN v.render_contract_revision=2 THEN (v.render_payload-'contract_version')||jsonb_build_object('revision',v.render_contract_revision) END,
    'type',type_row,'authorship',jsonb_build_object('receipt_id',receipt.semantic_task_receipt_id,'task_id',task.task_id,
     'attempt_id',task.result_attempt_id,'run_ref',run_label,'task_contract_key',receipt.task_contract_key,'task_contract_version',receipt.task_contract_version),
    'mentions',mentions,'entities','[]'::jsonb,'topics','[]'::jsonb));
   visible:=visible||jsonb_build_array(jsonb_build_object('kind','observation','ref',v.bead_id,'version_ref',v.bead_version_id));
   FOR item IN SELECT value FROM jsonb_array_elements(statements) LOOP
    visible:=visible||jsonb_build_array(jsonb_build_object('kind','statement','ref',item->'statement_id','version_ref',v.bead_version_id));
   END LOOP;
  END IF;
  IF request->'facets' ? 'evidence_metadata' THEN
   FOR support IN SELECT e.* FROM memoriesql.bead_semantic_statement_evidence e JOIN memoriesql.bead_semantic_statements st
    ON st.tenant_id=e.tenant_id AND st.statement_id=e.statement_id WHERE st.tenant_id=c.tenant_id
     AND st.bead_version_id=(target->>'bead_version_id')::uuid ORDER BY st.statement_id,e.evidence_source_unit_id LOOP
    -- Package metadata/lineage is not covered by the normalized-query exception.
    PERFORM memoriesql.revisiting_source_authorize((SELECT ev.source_object_id FROM memoriesql.source_events ev
     WHERE ev.tenant_id=c.tenant_id AND ev.event_id=support.evidence_event_id));
    FOR part_row IN SELECT pp.part_id FROM (SELECT 1) anchor LEFT JOIN memoriesql.logical_unit_materializations m
     ON m.tenant_id=c.tenant_id AND m.source_unit_id=support.evidence_source_unit_id LEFT JOIN memoriesql.evidence_package_parts pp
     ON pp.tenant_id=m.tenant_id AND pp.package_id=m.package_id ORDER BY pp.ordinal LOOP
     pin:=memoriesql.query_helper_evidence_pin_v1(c.tenant_id,uuidv7(),support.statement_id,support.evidence_source_unit_id,part_row.part_id);
     pins:=pins||jsonb_build_array(pin);
     visible:=visible||jsonb_build_array(jsonb_build_object('kind','source','ref',pin->'ref','unit_ref',pin#>'{unit,source_unit_id}','content_sha256',pin#>'{unit,content_sha256}'));
     IF pin->'part'<>'null'::jsonb THEN
      SELECT parts||jsonb_build_array(jsonb_build_object('part_id',pp.part_id,'package_id',pp.package_id,
       'inventory_sha256',pin#>'{package,inventory_sha256}','inventory',pp.inventory)) INTO parts
       FROM memoriesql.evidence_package_parts pp WHERE pp.tenant_id=c.tenant_id AND pp.part_id=part_row.part_id;
     END IF;
    END LOOP;
    raw_sources:=raw_sources||jsonb_build_array((SELECT ev.source_object_id FROM memoriesql.source_events ev WHERE ev.tenant_id=c.tenant_id AND ev.event_id=support.evidence_event_id));
    SELECT value INTO item FROM jsonb_array_elements(population->'source_units') WHERE value->>'source_unit_id'=support.evidence_source_unit_id::text;
    IF item IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
    units:=units||jsonb_build_array(item);
   END LOOP;
  END IF;
 END LOOP;
 FOR target IN SELECT value FROM jsonb_array_elements(request->'relation_refs') LOOP
  SELECT value INTO item FROM jsonb_array_elements(population->'assertions') WHERE value->>'relation_id'=target#>>'{}';
  IF item IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  neighbours:=memoriesql.relation_assertion_records_v2(c.tenant_id,'assessed',(target#>>'{}')::uuid,known,relation_mode);
  IF request->'facets' ? 'content' THEN
  relations:=relations||jsonb_build_array(item||jsonb_build_object('author_confidence',item->>'author_confidence',
   'independent_root_count',CASE WHEN item->'independent_root_count'='null'::jsonb THEN 'null'::jsonb ELSE to_jsonb(item->>'independent_root_count') END,
   'events',(SELECT COALESCE(jsonb_agg(event||jsonb_build_object('event_number',CASE WHEN event->'event_number'='null'::jsonb THEN 'null'::jsonb ELSE to_jsonb(event->>'event_number') END)),'[]') FROM jsonb_array_elements(item->'events') AS events(event))));
  SELECT types||COALESCE(jsonb_agg(value->'row'),'[]') INTO types FROM jsonb_array_elements(neighbours)
   WHERE value->>'kind'='type_definition' AND value#>'{row,key}'=item#>'{relation_type,key}'
    AND value#>'{row,revision}'=item#>'{relation_type,revision}';
  END IF;
  records:=records||jsonb_build_array(jsonb_build_object('kind','assertion_projection','id',item->>'relation_id','row',item));
  records:=records||neighbours;
  IF request->'facets' ? 'lifecycle' THEN
   -- Pair coverage is currently an owner-only whole-task projection. Never
   -- substitute partial visible pairs for a withheld captured task family.
   IF NOT memoriesql.current_context_has_capability('source.raw.read') THEN
    RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
   records:=records||memoriesql.relation_task_population_records_v1(c.tenant_id,(item->>'task_id')::uuid,known,'owner');
   FOR support IN SELECT relation_id FROM memoriesql.assessed_relations a
    WHERE a.tenant_id=c.tenant_id AND a.task_id=(item->>'task_id')::uuid AND a.recorded_at<=known LOOP
    records:=records||memoriesql.relation_assertion_records_v2(c.tenant_id,'assessed',support.relation_id,known,'owner');
   END LOOP;
   FOR pair IN SELECT jsonb_build_object('task_id',p.task_id,'first_bead_id',p.first_bead_id,
    'first_bead_version_id',p.first_bead_version_id,'second_bead_id',p.second_bead_id,
    'second_bead_version_id',p.second_bead_version_id,'disposition',p.disposition,'abstention',p.abstention,'reason',p.reason_text)
    FROM memoriesql.relation_pair_dispositions p WHERE p.tenant_id=c.tenant_id
     AND p.task_id=(item->>'task_id')::uuid AND p.recorded_at<=known LOOP
    IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(population->'pairs') WHERE value=pair) THEN
     RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
    pairs:=pairs||jsonb_build_array(pair);
    records:=records||jsonb_build_array(jsonb_build_object('kind','pair_disposition',
     'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(pair->'task_id',pair->'first_bead_id',pair->'second_bead_id')),'row',pair));
   END LOOP;
   SELECT records||jsonb_build_array(jsonb_build_object('kind','pair_task_population','id',a.task_id::text,
    'row',jsonb_build_object('task_id',a.task_id,'beads',a.beads))) INTO records
    FROM memoriesql.relation_assessments a WHERE a.tenant_id=c.tenant_id AND a.task_id=(item->>'task_id')::uuid;
  END IF;
 END LOOP;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'kind',value->>'id'),'[]') INTO records FROM (SELECT DISTINCT value FROM jsonb_array_elements(records)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'task_id',value->>'first_bead_id',value->>'second_bead_id'),'[]') INTO pairs FROM (SELECT DISTINCT value FROM jsonb_array_elements(pairs)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'key',value->>'revision'),'[]') INTO types FROM (SELECT DISTINCT value FROM jsonb_array_elements(types)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'source_unit_id'),'[]') INTO units FROM (SELECT DISTINCT value FROM jsonb_array_elements(units)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'predecessor_version_id',value->>'successor_version_id'),'[]') INTO corrections FROM (SELECT DISTINCT value FROM jsonb_array_elements(corrections)) x;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'ref'),'[]') INTO visible FROM (SELECT DISTINCT value FROM jsonb_array_elements(visible)) x;
 RETURN jsonb_build_object('content',jsonb_build_object('kind','inspect','payload',jsonb_build_object('inspection',jsonb_build_object(
  'observations',observations,'corrections',corrections,'source_units',units,'result',result_metadata,'relations',relations,
  'relation_types',types,'relation_pairs',pairs,'evidence_pins',pins,'provenance',provenance)),'refs',visible,'access_bases',bases),
  'witnesses',jsonb_build_object('evidence_pins',pins,'native_provenance',native_graph),
  'dependencies',jsonb_build_object('frames',jsonb_build_array(jsonb_build_object('known_at',memoriesql.relation_packet_time(known),'view',view_mode,'manifest',(SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',value->'kind','id',value->'id','content_sha256',memoriesql.lifecycle_hash_v1(value->'row')) ORDER BY value->>'kind',value->>'id'),'[]') FROM jsonb_array_elements(records)),
    -- Retained rows keep exact SQL values (numeric author_confidence) as
    -- canonical text, as saved query frames do; private partitions admit no
    -- JSON floats.
    'records_base64',translate(encode(convert_to(memoriesql.lifecycle_canonical_json_v1((SELECT COALESCE(jsonb_agg(CASE WHEN value->>'kind'='unit_package' THEN jsonb_build_object('kind',value->'kind','id',value->'id','content_sha256',memoriesql.lifecycle_hash_v1(value->'row')) ELSE value END ORDER BY value->>'kind',value->>'id'),'[]') FROM jsonb_array_elements(records))),'UTF8'),'base64'),E'\n',''))),
   'raw_sources',raw_sources,'parts',parts,'result_pins',result_pins),
  'checked_at',memoriesql.relation_packet_time(clock_timestamp()));
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_inspection_v1(uuid,jsonb) FROM PUBLIC;

-- Resolve only previously delivered unit support, or a support ref explicitly
-- readmitted through its currently authorized original standalone result.
CREATE FUNCTION memoriesql.resolve_query_helper_evidence_v1(delivery uuid,evidence uuid,part uuid,origin jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; x memoriesql.query_disclosures%ROWTYPE;
 v memoriesql.query_visible_refs%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; q memoriesql.query_result_creations%ROWTYPE;
 binding jsonb; pin jsonb; packet jsonb; deps jsonb; basis jsonb:='[]'; root_pin jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 IF origin IS NULL OR origin='null'::jsonb THEN
  SELECT * INTO v FROM memoriesql.query_visible_refs WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref
   AND ref_key::jsonb->>'kind'='source' AND ref_key::jsonb->>'ref'=evidence::text;
  IF v.first_delivery_ref IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  SELECT * INTO x FROM memoriesql.query_disclosures WHERE tenant_id=d.tenant_id AND delivery_ref=v.first_delivery_ref;
 ELSE
  root_pin:=origin->'result';
  PERFORM memoriesql.check_query_result_closure_v2((root_pin->>'result_id')::uuid,root_pin->>'content_digest');
  IF origin#>>'{access,kind}'<>'standalone' THEN RAISE EXCEPTION 'helper_checkpoint_access_unimplemented' USING ERRCODE='0A000'; END IF;
  SELECT * INTO x FROM memoriesql.query_disclosures z WHERE z.tenant_id=d.tenant_id
   AND z.result_id=(root_pin->>'result_id')::uuid AND z.content_digest=root_pin->>'content_digest'
   AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(z.delivered_refs) AS r(ref_key)
    WHERE r.ref_key::jsonb->>'kind'='source' AND r.ref_key::jsonb->>'ref'=evidence::text) LIMIT 1;
 END IF;
 IF x.delivery_ref IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF x.result_id IS NOT NULL THEN
  PERFORM memoriesql.check_query_result_closure_v2(x.result_id,x.content_digest);
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND artifact_ref=x.result_id;
  binding:=convert_from(a.dependency_bytes,'UTF8')::jsonb#>ARRAY['evidence_bindings',evidence::text];
  IF binding IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  pin:=memoriesql.query_helper_evidence_pin_v1(d.tenant_id,evidence,(binding->>'statement_id')::uuid,(binding->>'source_unit_id')::uuid,part);
  root_pin:=jsonb_build_object('result_id',x.result_id,'content_digest',x.content_digest);
  deps:=jsonb_build_object('frames','[]'::jsonb,'raw_sources','[]'::jsonb,'parts','[]'::jsonb,'result_pins',jsonb_build_array(root_pin));
  SELECT * INTO q FROM memoriesql.query_result_creations WHERE tenant_id=d.tenant_id AND result_id=x.result_id;
  basis:=jsonb_build_array(jsonb_build_object('context',jsonb_build_object('kind','standalone'),
   'checked_at',memoriesql.relation_packet_time(clock_timestamp()),'valid_until',memoriesql.relation_packet_time(q.expires_at),
   'end_condition','standalone_expiry','hold_ref',NULL));
 ELSE
  SELECT ar.* INTO a FROM memoriesql.query_deliveries first_delivery JOIN memoriesql.query_steps s
   ON s.tenant_id=first_delivery.tenant_id AND s.run_ref=first_delivery.run_ref AND s.step_key=first_delivery.step_key
   JOIN memoriesql.result_preparation_artifacts ar ON ar.tenant_id=s.tenant_id AND ar.artifact_ref=s.helper_artifact_ref
   WHERE first_delivery.tenant_id=d.tenant_id AND first_delivery.delivery_ref=x.delivery_ref;
  SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND operation_ref=a.operation_ref;
  packet:=memoriesql.read_query_helper_artifact_v1(delivery,o.operation_ref,o.ownership_ref);
  SELECT value INTO pin FROM jsonb_array_elements(packet#>'{witnesses,evidence_pins}') WHERE value->>'ref'=evidence::text;
  IF pin IS NULL OR pin#>>'{part,part_id}' IS DISTINCT FROM part::text THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  IF pin IS DISTINCT FROM memoriesql.query_helper_evidence_pin_v1(d.tenant_id,evidence,(pin->>'statement_id')::uuid,(pin#>>'{unit,source_unit_id}')::uuid,part) THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  deps:=packet->'dependencies'; basis:=packet#>'{content,access_bases}';
 END IF;
 RETURN jsonb_build_object('pin',pin,'dependencies',deps,'access_bases',basis);
END $$;
REVOKE ALL ON FUNCTION memoriesql.resolve_query_helper_evidence_v1(uuid,uuid,uuid,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.prepare_query_hydration_v1(delivery uuid,request jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; chosen jsonb; resolved jsonb; planned jsonb:='[]';
 pin jsonb; deps jsonb; all_deps jsonb:=jsonb_build_object('frames','[]'::jsonb,'raw_sources','[]'::jsonb,'parts','[]'::jsonb,'result_pins','[]'::jsonb);
 slices jsonb:='[]'; pins jsonb:='[]'; visible jsonb:='[]'; bases jsonb:='[]'; part_row memoriesql.evidence_package_parts%ROWTYPE;
 source uuid; data jsonb; storage_request jsonb; body_bytes bytea; content text; start_at integer; end_at integer; amount bigint:=0; k text;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 -- Resolve and authorize EVERY requested dependency before size, offset or
 -- retained-byte diagnostics. A denied later selection cancels the whole reply.
 FOR chosen IN SELECT value FROM jsonb_array_elements(request->'selections') LOOP
  resolved:=memoriesql.resolve_query_helper_evidence_v1(delivery,(chosen->>'evidence_ref')::uuid,(chosen->>'part_id')::uuid,chosen->'origin');
  pin:=resolved->'pin'; deps:=resolved->'dependencies';
  SELECT p.source_object_id INTO source FROM memoriesql.evidence_packages p WHERE p.tenant_id=d.tenant_id AND p.package_id=(pin#>>'{package,package_id}')::uuid;
  PERFORM memoriesql.revisiting_source_authorize(source);
  planned:=planned||jsonb_build_array(jsonb_build_object('selection',chosen,'pin',pin));
  FOREACH k IN ARRAY ARRAY['frames','raw_sources','parts','result_pins'] LOOP
   all_deps:=jsonb_set(all_deps,ARRAY[k],(all_deps->k)||(deps->k));
  END LOOP;
  all_deps:=jsonb_set(all_deps,'{raw_sources}',(all_deps->'raw_sources')||jsonb_build_array(source));
  SELECT * INTO part_row FROM memoriesql.evidence_package_parts WHERE tenant_id=d.tenant_id
   AND package_id=(pin#>>'{package,package_id}')::uuid AND part_id=(chosen->>'part_id')::uuid;
  IF part_row.part_id IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  all_deps:=jsonb_set(all_deps,'{parts}',(all_deps->'parts')||jsonb_build_array(jsonb_build_object(
   'part_id',part_row.part_id,'package_id',part_row.package_id,'inventory_sha256',pin#>'{package,inventory_sha256}','inventory',part_row.inventory)));
  bases:=bases||resolved->'access_bases';
 END LOOP;

 FOR resolved IN SELECT value FROM jsonb_array_elements(planned) LOOP
  chosen:=resolved->'selection'; pin:=resolved->'pin'; start_at:=(chosen->>'start')::integer; end_at:=(chosen->>'end')::integer;
  IF start_at<0 OR end_at<start_at OR end_at-start_at>16384 THEN RAISE EXCEPTION 'invalid_query_helper_interval' USING ERRCODE='22023'; END IF;
  IF chosen->>'representation'='normalized_text' THEN
   IF end_at>(pin#>>'{part,characters}')::integer THEN RAISE EXCEPTION 'invalid_query_helper_interval' USING ERRCODE='22023'; END IF;
  ELSE
   data:=pin#>ARRAY['part','lineage',chosen->>'lineage_ordinal'];
   IF data IS NULL OR data='null'::jsonb OR end_at>(data->>'byte_end_exclusive')::bigint-(data->>'byte_start')::bigint THEN
    RAISE EXCEPTION 'invalid_query_helper_interval' USING ERRCODE='22023'; END IF;
  END IF;
  IF end_at=start_at THEN
   body_bytes:=''::bytea; content:=CASE WHEN chosen->>'representation'='normalized_text' THEN '' END;
  ELSE
   storage_request:=jsonb_build_object('package_id',pin#>'{package,package_id}','inventory_sha256',pin#>'{package,inventory_sha256}',
    'part_ordinal',pin#>'{part,ordinal}','representation',CASE chosen->>'representation' WHEN 'normalized_text' THEN 'normalized' ELSE 'raw' END,
    'lineage_ordinal',CASE WHEN chosen->>'representation'='raw_bytes' THEN chosen->'lineage_ordinal' ELSE 'null'::jsonb END,'offset',start_at,'limit',end_at-start_at);
   data:=memoriesql.stored_bead_evidence_content(d.tenant_id,storage_request);
   content:=data->>'content'; body_bytes:=CASE WHEN chosen->>'representation'='normalized_text' THEN convert_to(content,'UTF8') ELSE decode(data->>'bytes_hex','hex') END;
   IF (chosen->>'representation'='normalized_text' AND char_length(content)<>end_at-start_at)
    OR (chosen->>'representation'='raw_bytes' AND octet_length(body_bytes)<>end_at-start_at) THEN
    RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  END IF;
  amount:=amount+octet_length(body_bytes);
  slices:=slices||jsonb_build_array(jsonb_build_object('evidence_pin',pin,'representation',chosen->'representation',
   'start',start_at,'end',end_at,'content',content,'bytes_base64',CASE WHEN chosen->>'representation'='raw_bytes' THEN replace(encode(body_bytes,'base64'),chr(10),'') END,
   'returned_sha256',encode(sha256(body_bytes),'hex')));
  pins:=pins||jsonb_build_array(pin);
  visible:=visible||jsonb_build_array(jsonb_build_object('kind','source','ref',pin->'ref','unit_ref',pin#>'{unit,source_unit_id}','content_sha256',pin#>'{unit,content_sha256}'));
 END LOOP;
 IF amount>(request->>'max_bytes')::integer THEN RAISE EXCEPTION 'query_helper_transport_exhausted' USING ERRCODE='54000'; END IF;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'ref'),'[]') INTO visible FROM (SELECT DISTINCT value FROM jsonb_array_elements(visible)) x;
 SELECT COALESCE(jsonb_agg(value),'[]') INTO bases FROM (SELECT DISTINCT value FROM jsonb_array_elements(bases)) x;
 RETURN jsonb_build_object('content',jsonb_build_object('kind','hydrate_source','payload',jsonb_build_object('hydration',jsonb_build_object('slices',slices,'selection_complete',true)),
  'refs',visible,'access_bases',bases),'witnesses',jsonb_build_object('evidence_pins',pins),'dependencies',all_deps,
  'checked_at',memoriesql.relation_packet_time(clock_timestamp()));
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_hydration_v1(uuid,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.prepare_query_helper_v1(delivery uuid,request jsonb) RETURNS jsonb
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
  ELSE PERFORM memoriesql.prepare_query_hydration_v1(delivery,request||'{"cursor":null}'::jsonb); END IF;
  RAISE EXCEPTION 'query_helper_cursor_invalid' USING ERRCODE='22023';
 END IF;
 IF s.kind='inspect' THEN packet:=memoriesql.prepare_query_inspection_v1(delivery,request);
 ELSE packet:=memoriesql.prepare_query_hydration_v1(delivery,request); END IF;
 RETURN jsonb_set(packet,'{witnesses,request}',request);
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_query_helper_v1(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_query_helper_v1(uuid,jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.query_helper_preparation_v1(delivery uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 IF s.state='complete' THEN
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND artifact_ref=s.helper_artifact_ref;
  SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND operation_ref=a.operation_ref;
 ELSE
  SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 END IF;
 IF o.operation_ref IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('operation_ref',o.operation_ref,'ownership_ref',o.ownership_ref,'state',o.state,
  'page_first',COALESCE(s.helper_page_first,1),'page_count',s.helper_page_count,'page_cursor',s.helper_page_cursor);
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_helper_preparation_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_helper_preparation_v1(uuid) TO memoriesql_application;
