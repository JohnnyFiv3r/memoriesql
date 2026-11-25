-- Owner-scoped names retain existing checkpoint artifacts and their existing
-- root closures. Revision receipts use the ordinary run/step delivery ledger.
CREATE TABLE memoriesql.query_named_saves (
 tenant_id uuid NOT NULL,
 investigation_id uuid NOT NULL,
 save_id uuid NOT NULL,
 name text COLLATE "C" NOT NULL CHECK(name ~ '^[a-z][a-z0-9_-]{0,63}$'),
 revision bigint NOT NULL CHECK(revision>0),
 checkpoint_id uuid,
 recorded_at timestamptz NOT NULL,
 operation_ref uuid NOT NULL,
 PRIMARY KEY(tenant_id,save_id),
 UNIQUE(tenant_id,investigation_id,name),
 FOREIGN KEY(tenant_id,investigation_id) REFERENCES memoriesql.query_investigations ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,checkpoint_id) REFERENCES memoriesql.query_checkpoints,
 FOREIGN KEY(tenant_id,operation_ref) REFERENCES memoriesql.result_preparation_operations
);
CREATE INDEX query_named_saves_checkpoint ON memoriesql.query_named_saves(tenant_id,checkpoint_id)
 WHERE checkpoint_id IS NOT NULL;
CREATE INDEX query_named_saves_operation ON memoriesql.query_named_saves(tenant_id,operation_ref);
ALTER TABLE memoriesql.query_named_saves ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_named_saves FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_named_saves FROM PUBLIC,memoriesql_application;

-- Pruning an automatic slot may leave its artifact retained by explicit names.
DO $$ DECLARE constraint_name text;
BEGIN
 SELECT conname INTO STRICT constraint_name FROM pg_constraint
  WHERE conrelid='memoriesql.query_checkpoints'::regclass AND contype='c'
   AND pg_get_constraintdef(oid) LIKE '%manifest_digest IS NOT NULL%automatic_retained%';
 EXECUTE format('ALTER TABLE memoriesql.query_checkpoints DROP CONSTRAINT %I',constraint_name);
END $$;
ALTER TABLE memoriesql.query_checkpoints ADD CONSTRAINT query_checkpoint_retention_state CHECK(
 (pruned_at IS NULL)=automatic_retained AND (NOT automatic_retained OR manifest_digest IS NOT NULL));

ALTER TABLE memoriesql.query_steps ADD COLUMN named_save_ref uuid;
ALTER TABLE memoriesql.query_steps ADD COLUMN named_save_revision bigint CHECK(named_save_revision>0);
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_named_save_step_pair
 CHECK((named_save_ref IS NULL)=(named_save_revision IS NULL));

CREATE FUNCTION memoriesql.valid_checkpoint_basis_v1(basis jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(basis='{"kind":"automatic"}'::jsonb OR
  (basis->>'kind'='named_save' AND basis-ARRAY['kind','save_id','revision']='{}'::jsonb
   AND basis->>'save_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
   AND jsonb_typeof(basis->'revision')='string'
   AND basis->>'revision' ~ '^[1-9][0-9]{0,18}$'
   AND (length(basis->>'revision')<19 OR (basis->>'revision') COLLATE "C"<='9223372036854775807')),false)
$$;
REVOKE ALL ON FUNCTION memoriesql.valid_checkpoint_basis_v1(jsonb) FROM PUBLIC,memoriesql_application;

-- Physical retention and access use the same exact basis match. Automatic
-- slots remain physical holders until cleanup runs, even after their deadline.
CREATE FUNCTION memoriesql.checkpoint_basis_retained_v1(cp memoriesql.query_checkpoints,basis jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT CASE WHEN basis='{"kind":"automatic"}'::jsonb THEN cp.automatic_retained
  WHEN memoriesql.valid_checkpoint_basis_v1(basis) THEN EXISTS(
   SELECT 1 FROM memoriesql.query_named_saves n
   WHERE n.tenant_id=cp.tenant_id AND n.investigation_id=cp.investigation_id
    AND n.checkpoint_id=cp.checkpoint_id AND n.save_id=(basis->>'save_id')::uuid
    AND n.revision=(basis->>'revision')::bigint)
  ELSE false END
$$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_basis_retained_v1(memoriesql.query_checkpoints,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.checkpoint_basis_live_v1(cp memoriesql.query_checkpoints,basis jsonb) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT COALESCE(cp.manifest_digest IS NOT NULL AND memoriesql.checkpoint_basis_retained_v1(cp,basis)
  AND (basis->>'kind'<>'automatic' OR clock_timestamp()<cp.expires_at),false)
$$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_basis_live_v1(memoriesql.query_checkpoints,jsonb) FROM PUBLIC;

ALTER TABLE memoriesql.query_cursors DROP CONSTRAINT query_cursors_access_context_check;
ALTER TABLE memoriesql.query_disclosures DROP CONSTRAINT query_disclosures_access_context_check;
ALTER TABLE memoriesql.query_cursors ADD CONSTRAINT query_cursors_access_context_check CHECK(COALESCE(
 access_context='{"kind":"standalone"}'::jsonb OR
 (access_context->>'kind'='checkpoint' AND access_context ?& ARRAY['kind','checkpoint_id','manifest_digest','root','basis']
  AND access_context-ARRAY['kind','checkpoint_id','manifest_digest','root','basis']='{}'::jsonb
  AND memoriesql.valid_checkpoint_basis_v1(access_context->'basis')),false));
ALTER TABLE memoriesql.query_disclosures ADD CONSTRAINT query_disclosures_access_context_check CHECK(COALESCE(
 access_context='{"kind":"standalone"}'::jsonb OR
 (access_context->>'kind'='checkpoint' AND access_context ?& ARRAY['kind','checkpoint_id','manifest_digest','root','basis']
  AND access_context-ARRAY['kind','checkpoint_id','manifest_digest','root','basis']='{}'::jsonb
  AND memoriesql.valid_checkpoint_basis_v1(access_context->'basis')) OR
 (access_context ?& ARRAY['checkpoint','basis'] AND access_context-ARRAY['checkpoint','basis']='{}'::jsonb
  AND memoriesql.valid_checkpoint_basis_v1(access_context->'basis')),false));

ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_kind_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_kind_check
 CHECK(kind IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint','read_source_text','save_checkpoint','resolve_save','release_save'));
ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_complete_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_complete_check CHECK(
 (state='complete')=(
  (kind IN ('query','reuse_result') AND helper_artifact_ref IS NULL AND result_id IS NOT NULL
   AND (purged_at IS NOT NULL OR (content_digest IS NOT NULL AND first_row IS NOT NULL AND row_count IS NOT NULL AND page_size IS NOT NULL)))
  OR (kind IN ('inspect','hydrate_source','read_source_text') AND state='complete' AND result_id IS NULL AND first_row IS NULL AND row_count IS NULL AND page_size IS NULL
   AND (purged_at IS NOT NULL OR (helper_artifact_ref IS NOT NULL AND content_digest IS NOT NULL AND helper_page_first IS NOT NULL AND helper_page_count IS NOT NULL)))
  OR (kind IN ('checkpoint','read_checkpoint') AND state='complete' AND result_id IS NULL AND helper_artifact_ref IS NULL
   AND (purged_at IS NOT NULL OR (checkpoint_ref IS NOT NULL AND content_digest IS NOT NULL)))
  OR (kind IN ('save_checkpoint','resolve_save','release_save') AND state='complete'
   AND result_id IS NULL AND helper_artifact_ref IS NULL AND checkpoint_ref IS NULL AND content_digest IS NULL
   AND (purged_at IS NOT NULL OR (named_save_ref IS NOT NULL AND named_save_revision IS NOT NULL)))));

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
 IF run IS NULL OR step IS NULL OR requested_kind NOT IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint','read_source_text','save_checkpoint','resolve_save','release_save')
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
  OR s.kind NOT IN ('inspect','hydrate_source','checkpoint','read_checkpoint','read_source_text','save_checkpoint','resolve_save','release_save') OR s.purged_at IS NOT NULL
  OR EXISTS(SELECT 1 FROM memoriesql.query_run_closures WHERE tenant_id=c.tenant_id AND run_ref=r.run_ref) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF d.state<>'reserved' THEN RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000'; END IF;
 IF clock_timestamp()>=LEAST(d.deadline,r.expires_at) THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000'; END IF;
 RETURN d;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_helper_delivery_v1(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.read_automatic_checkpoint_v1(access jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; cp memoriesql.query_checkpoints%ROWTYPE;
 inv memoriesql.query_investigations%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; root record; contexts jsonb:='[]';
 manifest jsonb; unique_bytes bigint; holds jsonb; named_bytes bigint;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF NOT memoriesql.valid_checkpoint_basis_v1(access->'basis') THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
 SELECT * INTO cp FROM memoriesql.query_checkpoints WHERE tenant_id=c.tenant_id
  AND checkpoint_id=(access#>>'{checkpoint,checkpoint_id}')::uuid;
 SELECT * INTO inv FROM memoriesql.query_investigations WHERE tenant_id=c.tenant_id AND investigation_id=cp.investigation_id;
 IF cp.checkpoint_id IS NULL OR inv.owner_identity IS DISTINCT FROM memoriesql.checkpoint_identity_v1(c) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':investigation:'||cp.investigation_id::text,0));
 SELECT * INTO cp FROM memoriesql.query_checkpoints WHERE tenant_id=c.tenant_id AND checkpoint_id=cp.checkpoint_id FOR SHARE;
 IF NOT memoriesql.checkpoint_basis_live_v1(cp,access->'basis')
  OR cp.manifest_digest IS DISTINCT FROM access#>>'{checkpoint,manifest_digest}' THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id AND artifact_ref=cp.checkpoint_id;
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id AND operation_ref=cp.operation_ref;
 IF a.artifact_ref IS NULL OR o.state IS DISTINCT FROM 'sealed' OR a.operation_ref<>cp.operation_ref
  OR encode(sha256(a.content_bytes),'hex') IS DISTINCT FROM cp.manifest_digest THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 manifest:=convert_from(a.content_bytes,'UTF8')::jsonb;
 IF manifest->>'checkpoint_id' IS DISTINCT FROM cp.checkpoint_id::text
  OR manifest->>'investigation_id' IS DISTINCT FROM cp.investigation_id::text
  OR manifest->>'branch_id' IS DISTINCT FROM cp.branch_id::text THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 FOR root IN SELECT * FROM memoriesql.query_checkpoint_roots WHERE tenant_id=c.tenant_id
  AND checkpoint_id=cp.checkpoint_id ORDER BY result_id LOOP
  PERFORM memoriesql.check_query_result_bytes_v1(root.result_id,root.content_digest,true);
  contexts:=contexts||jsonb_build_array(jsonb_build_object('kind','checkpoint',
   'checkpoint_id',cp.checkpoint_id,'manifest_digest',cp.manifest_digest,
   'root',jsonb_build_object('result_id',root.result_id,'content_digest',root.content_digest),'basis',access->'basis'));
 END LOOP;
 IF jsonb_array_length(manifest->'roots')<>jsonb_array_length(contexts) OR EXISTS(
  SELECT 1 FROM jsonb_array_elements(manifest->'roots') x(pin)
  WHERE NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoint_roots r WHERE r.tenant_id=c.tenant_id
   AND r.checkpoint_id=cp.checkpoint_id AND r.result_id=(pin#>>'{result,result_id}')::uuid
   AND r.content_digest=pin#>>'{result,content_digest}' AND r.frame_ref=(pin->>'frame_ref')::uuid)) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(sum(x.allocation_bytes),0)+o.allocation_bytes INTO unique_bytes
  FROM memoriesql.result_preparation_operations x JOIN memoriesql.result_preparation_artifacts ar
   ON ar.tenant_id=x.tenant_id AND ar.operation_ref=x.operation_ref
  WHERE ar.tenant_id=c.tenant_id AND ar.artifact_ref IN (SELECT h.artifact_ref
   FROM memoriesql.query_checkpoint_holds h WHERE h.tenant_id=c.tenant_id AND h.checkpoint_id=cp.checkpoint_id);
 SELECT COALESCE(jsonb_agg(jsonb_build_object('save_id',n.save_id,'revision',n.revision::text)
   ORDER BY n.save_id),'[]'::jsonb),COALESCE(sum(op.allocation_bytes),0) INTO holds,named_bytes
  FROM memoriesql.query_named_saves n JOIN memoriesql.result_preparation_operations op USING(tenant_id,operation_ref)
  WHERE n.tenant_id=c.tenant_id AND n.checkpoint_id=cp.checkpoint_id;
 PERFORM memoriesql.result_preparation_authority_v1();
 IF NOT memoriesql.checkpoint_basis_live_v1(cp,access->'basis') THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('checkpoint',manifest,'root_contexts',contexts,
  'retention',jsonb_build_object('automatic_until',manifest->'automatic_expires_at',
   'automatic_retained',cp.automatic_retained AND
    (access#>>'{basis,kind}'='automatic' OR clock_timestamp()<cp.expires_at),
   'named_holds',holds,'retained',true,
   'hold_ref',CASE WHEN access#>>'{basis,kind}'='named_save' THEN access#>>'{basis,save_id}' ELSE cp.checkpoint_id::text END,
   'unique_bytes',(unique_bytes+named_bytes)::text,
   'context_end',CASE WHEN access#>>'{basis,kind}'='named_save' THEN 'last_save_release' ELSE 'automatic_expiry_or_prune' END));
END $$;
REVOKE ALL ON FUNCTION memoriesql.read_automatic_checkpoint_v1(jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.query_result_access_v1(result_ref uuid,digest text,access jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE packet jsonb; q memoriesql.query_result_creations%ROWTYPE; cp memoriesql.query_checkpoints%ROWTYPE;
BEGIN
 IF access='{"kind":"standalone"}'::jsonb THEN
  PERFORM memoriesql.check_query_result_bytes_v1(result_ref,digest,false);
  SELECT * INTO q FROM memoriesql.query_result_creations WHERE tenant_id=(memoriesql.result_preparation_authority_v1()).tenant_id AND result_id=result_ref;
  RETURN jsonb_build_object('context',access,'checked_at',memoriesql.relation_packet_time(clock_timestamp()),
   'valid_until',memoriesql.relation_packet_time(q.expires_at),'end_condition','standalone_expiry','hold_ref',NULL);
 END IF;
 IF access->>'kind' IS DISTINCT FROM 'checkpoint' OR access->'root' IS DISTINCT FROM
  jsonb_build_object('result_id',result_ref,'content_digest',digest) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 packet:=memoriesql.read_automatic_checkpoint_v1(jsonb_build_object('checkpoint',
  jsonb_build_object('checkpoint_id',access->'checkpoint_id','manifest_digest',access->'manifest_digest'),'basis',access->'basis'));
 IF NOT (packet->'root_contexts' @> jsonb_build_array(access)) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('context',access,'checked_at',memoriesql.relation_packet_time(clock_timestamp()),
  'valid_until',CASE WHEN access#>>'{basis,kind}'='automatic' THEN packet#>'{retention,automatic_until}' END,
  'end_condition',CASE WHEN access#>>'{basis,kind}'='automatic' THEN 'checkpoint_expiry_or_prune' ELSE 'save_release' END,
  'hold_ref',packet#>'{retention,hold_ref}');
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_result_access_v1(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_result_access_v1(uuid,text,jsonb) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.query_result_context_live_v1(result_ref uuid,digest text,access jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF access='{"kind":"standalone"}'::jsonb THEN
  RETURN EXISTS(SELECT 1 FROM memoriesql.query_result_creations q WHERE q.tenant_id=c.tenant_id
   AND q.result_id=result_ref AND q.content_digest=digest AND clock_timestamp()<q.expires_at);
 END IF;
 RETURN access->>'kind'='checkpoint'
  AND access->'root'=jsonb_build_object('result_id',result_ref,'content_digest',digest)
  AND EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp JOIN memoriesql.query_investigations i USING(tenant_id,investigation_id)
   JOIN memoriesql.query_checkpoint_roots r USING(tenant_id,checkpoint_id)
   WHERE cp.tenant_id=c.tenant_id AND cp.checkpoint_id=(access->>'checkpoint_id')::uuid
    AND cp.manifest_digest=access->>'manifest_digest'
    AND memoriesql.checkpoint_basis_live_v1(cp,access->'basis')
    AND i.owner_identity=memoriesql.checkpoint_identity_v1(c) AND r.result_id=result_ref AND r.content_digest=digest);
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_result_context_live_v1(uuid,text,jsonb) FROM PUBLIC;


CREATE OR REPLACE FUNCTION memoriesql.query_checkpoint_context_retained_v1(t uuid,access jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT memoriesql.query_checkpoint_context_ref_v1(access) IS NULL OR EXISTS(
  SELECT 1 FROM memoriesql.query_checkpoints cp WHERE cp.tenant_id=t
   AND cp.checkpoint_id=memoriesql.query_checkpoint_context_ref_v1(access)::uuid
   AND memoriesql.checkpoint_basis_retained_v1(cp,access->'basis'))
$$;

CREATE FUNCTION memoriesql.prune_checkpoint_context_receipts_v1(t uuid,checkpoint uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 DELETE FROM memoriesql.query_disclosures x USING memoriesql.query_deliveries d,memoriesql.query_run_purges p
  WHERE x.tenant_id=t AND memoriesql.query_checkpoint_context_ref_v1(x.access_context)=checkpoint::text
   AND d.tenant_id=x.tenant_id AND d.delivery_ref=x.delivery_ref AND p.tenant_id=d.tenant_id AND p.run_ref=d.run_ref
   AND NOT memoriesql.query_checkpoint_context_retained_v1(t,x.access_context);
 DELETE FROM memoriesql.query_cursors k USING memoriesql.query_deliveries d,memoriesql.query_run_purges p
  WHERE k.tenant_id=t AND memoriesql.query_checkpoint_context_ref_v1(k.access_context)=checkpoint::text
   AND d.tenant_id=k.tenant_id AND d.delivery_ref=k.issued_by_delivery AND p.tenant_id=d.tenant_id AND p.run_ref=d.run_ref
   AND NOT memoriesql.query_checkpoint_context_retained_v1(t,k.access_context);
END $$;
REVOKE ALL ON FUNCTION memoriesql.prune_checkpoint_context_receipts_v1(uuid,uuid) FROM PUBLIC;

-- Called under the workspace allocator and the checkpoint's investigation lock.
CREATE FUNCTION memoriesql.discard_unheld_checkpoint_v1(t uuid,checkpoint uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE op uuid;
BEGIN
 UPDATE memoriesql.query_checkpoints cp SET manifest_digest=NULL,pruned_at=clock_timestamp()
  WHERE cp.tenant_id=t AND cp.checkpoint_id=checkpoint AND NOT cp.automatic_retained
   AND cp.manifest_digest IS NOT NULL
   AND NOT EXISTS(SELECT 1 FROM memoriesql.query_named_saves n
    WHERE n.tenant_id=t AND n.checkpoint_id=checkpoint)
  RETURNING operation_ref INTO op;
 IF NOT FOUND THEN RETURN; END IF;
 DELETE FROM memoriesql.query_checkpoint_holds WHERE tenant_id=t AND checkpoint_id=checkpoint;
 DELETE FROM memoriesql.query_checkpoint_roots WHERE tenant_id=t AND checkpoint_id=checkpoint;
 DELETE FROM memoriesql.result_preparation_artifacts WHERE tenant_id=t AND artifact_ref=checkpoint;
 UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
  encoded_bytes=0,allocation_bytes=40960 WHERE tenant_id=t AND operation_ref=op;
END $$;
REVOKE ALL ON FUNCTION memoriesql.discard_unheld_checkpoint_v1(uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.prune_automatic_checkpoints_v1(t uuid,investigation uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE cp record;
BEGIN
 FOR cp IN SELECT q.checkpoint_id FROM memoriesql.query_checkpoints q WHERE tenant_id=t
  AND investigation_id=investigation AND automatic_retained
  AND (expires_at<=clock_timestamp() OR investigation_sequence IN (
   SELECT investigation_sequence FROM memoriesql.query_checkpoints WHERE tenant_id=t
    AND investigation_id=investigation AND automatic_retained ORDER BY investigation_sequence DESC OFFSET 20))
  ORDER BY investigation_sequence FOR UPDATE LOOP
  UPDATE memoriesql.query_checkpoints SET automatic_retained=false,pruned_at=clock_timestamp()
   WHERE tenant_id=t AND checkpoint_id=cp.checkpoint_id;
  PERFORM memoriesql.discard_unheld_checkpoint_v1(t,cp.checkpoint_id);
  PERFORM memoriesql.prune_checkpoint_context_receipts_v1(t,cp.checkpoint_id);
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoints WHERE tenant_id=t
  AND investigation_id=investigation AND manifest_digest IS NOT NULL) THEN
  UPDATE memoriesql.query_investigation_branches SET head_ref=NULL,head_digest=NULL
   WHERE tenant_id=t AND investigation_id=investigation AND head_ref IS NOT NULL;
  -- A truly ended investigation cannot be appended to. Its inactive names
  -- can now leave custody with the existing charged run/lineage tombstones.
  DELETE FROM memoriesql.query_named_saves WHERE tenant_id=t AND investigation_id=investigation
   AND checkpoint_id IS NULL;
 END IF;
END $$;

CREATE FUNCTION memoriesql.end_named_checkpoint_hold_v1(t uuid,checkpoint uuid,investigation uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 IF checkpoint IS NULL THEN RETURN; END IF;
 IF NOT EXISTS(SELECT 1 FROM memoriesql.query_named_saves n WHERE n.tenant_id=t AND n.checkpoint_id=checkpoint) THEN
  -- Automatic expiry may precede this release. Preserve the true last hold
  -- end on the existing ledger before deleting the checkpoint's closure rows.
  INSERT INTO memoriesql.query_result_cleanup_hold_ends(tenant_id,artifact_ref,last_hold_end)
   SELECT h.tenant_id,h.artifact_ref,clock_timestamp() FROM memoriesql.query_checkpoint_holds h
   WHERE h.tenant_id=t AND h.checkpoint_id=checkpoint
   ON CONFLICT(tenant_id,artifact_ref) DO UPDATE SET last_hold_end=GREATEST(
    memoriesql.query_result_cleanup_hold_ends.last_hold_end,EXCLUDED.last_hold_end);
  PERFORM memoriesql.discard_unheld_checkpoint_v1(t,checkpoint);
 END IF;
 PERFORM memoriesql.prune_checkpoint_context_receipts_v1(t,checkpoint);
 PERFORM memoriesql.prune_automatic_checkpoints_v1(t,investigation);
END $$;
REVOKE ALL ON FUNCTION memoriesql.end_named_checkpoint_hold_v1(uuid,uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.clear_checkpoint_step_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.purged_at IS NOT NULL THEN
  NEW.checkpoint_pending:=NULL; NEW.checkpoint_ref:=NULL;
  NEW.named_save_ref:=NULL; NEW.named_save_revision:=NULL;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.clear_checkpoint_step_v1() FROM PUBLIC;
CREATE OR REPLACE FUNCTION memoriesql.purge_checkpoint_step_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 IF NEW.purged_at IS NULL OR OLD.purged_at IS NOT NULL THEN RETURN NEW; END IF;
 DELETE FROM memoriesql.query_disclosures x USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND x.tenant_id=d.tenant_id AND x.delivery_ref=d.delivery_ref
   AND ((NEW.kind IN ('checkpoint','read_checkpoint','save_checkpoint','resolve_save','release_save') AND x.result_id IS NULL)
    OR NOT memoriesql.query_checkpoint_context_retained_v1(x.tenant_id,x.access_context));
 DELETE FROM memoriesql.query_cursors k USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND k.tenant_id=d.tenant_id AND k.issued_by_delivery=d.delivery_ref
   AND NOT memoriesql.query_checkpoint_context_retained_v1(k.tenant_id,k.access_context);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.purge_checkpoint_step_v1() FROM PUBLIC;
CREATE OR REPLACE FUNCTION memoriesql.query_tombstone_due_at_v1(t uuid,run uuid) RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT CASE WHEN EXISTS(SELECT 1 FROM memoriesql.result_preparation_operations
   WHERE tenant_id=t AND run_ref=run AND state<>'discarded')
  OR EXISTS(SELECT 1 FROM memoriesql.query_named_saves n JOIN memoriesql.result_preparation_operations op
   USING(tenant_id,operation_ref) WHERE n.tenant_id=t AND op.run_ref=run)
  OR EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp JOIN memoriesql.result_preparation_operations op
   ON op.tenant_id=cp.tenant_id AND op.operation_ref=cp.operation_ref
   WHERE cp.tenant_id=t AND op.run_ref=run AND cp.manifest_digest IS NOT NULL)
  OR EXISTS(SELECT 1 FROM memoriesql.query_checkpoints s JOIN memoriesql.result_preparation_operations op
   ON op.tenant_id=s.tenant_id AND op.operation_ref=s.operation_ref
   JOIN memoriesql.query_checkpoints live ON live.tenant_id=s.tenant_id
    AND live.investigation_id=s.investigation_id AND live.manifest_digest IS NOT NULL AND live.created_at<=s.pruned_at
   WHERE s.tenant_id=t AND op.run_ref=run AND s.manifest_digest IS NULL)
  OR EXISTS(SELECT 1 FROM memoriesql.query_disclosures x JOIN memoriesql.query_deliveries d
   ON d.tenant_id=x.tenant_id AND d.delivery_ref=x.delivery_ref WHERE d.tenant_id=t AND d.run_ref=run)
  OR EXISTS(SELECT 1 FROM memoriesql.query_cursors x JOIN memoriesql.query_deliveries d
   ON d.tenant_id=x.tenant_id AND d.delivery_ref=x.issued_by_delivery WHERE d.tenant_id=t AND d.run_ref=run)
  THEN NULL ELSE (
   SELECT GREATEST(p.purged_at,
    (SELECT max(purged_at) FROM memoriesql.query_result_purges WHERE tenant_id=t AND run_ref=run),
    (SELECT max(s.pruned_at) FROM memoriesql.query_checkpoints s JOIN memoriesql.result_preparation_operations op
      ON op.tenant_id=s.tenant_id AND op.operation_ref=s.operation_ref WHERE s.tenant_id=t AND op.run_ref=run))
    +interval '30 days'
   FROM memoriesql.query_run_purges p WHERE p.tenant_id=t AND p.run_ref=run) END
$$;
REVOKE ALL ON FUNCTION memoriesql.query_tombstone_due_at_v1(uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.query_result_cleanup_eligible_at_v1(t uuid,result_ref uuid) RETURNS timestamptz
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 -- UNION, not an arbitrary depth/count cutoff, follows the immutable hold DAG.
 WITH RECURSIVE consumers(artifact_ref) AS (
  SELECT result_ref
  UNION SELECT h.child_ref FROM consumers c JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=t AND h.parent_ref=c.artifact_ref
 ), promises AS (
  SELECT q.expires_at AS ends_at FROM consumers c JOIN memoriesql.query_result_creations q
   ON q.tenant_id=t AND q.result_id=c.artifact_ref
  UNION ALL SELECT e.last_hold_end FROM consumers c JOIN memoriesql.query_result_cleanup_hold_ends e
   ON e.tenant_id=t AND e.artifact_ref=c.artifact_ref
  UNION ALL SELECT cp.expires_at FROM consumers c JOIN memoriesql.query_checkpoint_holds h
   ON h.tenant_id=t AND h.artifact_ref=c.artifact_ref
   JOIN memoriesql.query_checkpoints cp ON cp.tenant_id=t AND cp.checkpoint_id=h.checkpoint_id
   WHERE cp.automatic_retained
 )
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM memoriesql.query_result_creations WHERE tenant_id=t AND result_id=result_ref)
  OR EXISTS(SELECT 1 FROM consumers c JOIN memoriesql.result_preparation_artifacts a
   ON a.tenant_id=t AND a.artifact_ref=c.artifact_ref
   WHERE NOT EXISTS(SELECT 1 FROM memoriesql.query_result_creations q WHERE q.tenant_id=t AND q.result_id=c.artifact_ref))
  OR EXISTS(SELECT 1 FROM consumers c JOIN memoriesql.query_checkpoint_holds h
   ON h.tenant_id=t AND h.artifact_ref=c.artifact_ref
   JOIN memoriesql.query_named_saves n ON n.tenant_id=t AND n.checkpoint_id=h.checkpoint_id)
  -- A private preparation with no declared result lifetime retains its explicit
  -- hold until owned release. It cannot be treated as expired by guessing time.
  THEN NULL ELSE (SELECT max(ends_at) FROM promises) END
$$;
REVOKE ALL ON FUNCTION memoriesql.query_result_cleanup_eligible_at_v1(uuid,uuid) FROM PUBLIC;


CREATE FUNCTION memoriesql.prepare_named_checkpoint_v1(delivery uuid,request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; c memoriesql.authorization_contexts%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE; n memoriesql.query_named_saves%ROWTYPE;
 inv memoriesql.query_investigations%ROWTYPE; cp memoriesql.query_checkpoints%ROWTYPE;
 investigation uuid; target uuid; old_checkpoint uuid; name_value text:=request->>'name';
 expected bigint; revision_value bigint; id uuid; operation uuid; access jsonb; packet jsonb; binding jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 IF s.kind NOT IN ('save_checkpoint','resolve_save','release_save')
  OR request->>'kind' IS DISTINCT FROM s.kind OR request->>'run_ref' IS DISTINCT FROM d.run_ref::text
  OR request->>'step_key' IS DISTINCT FROM d.step_key::text THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF name_value IS NULL OR name_value COLLATE "C" !~ '^[a-z][a-z0-9_-]{0,63}$' THEN
  RAISE EXCEPTION 'invalid_checkpoint_name' USING ERRCODE='22023'; END IF;

 -- For the noncontent management replay the current authenticated run/step
 -- still governs identity. An old release never reads the name's new target.
 IF s.state='complete' AND s.kind='release_save' THEN
  RETURN jsonb_build_object('released',jsonb_build_object('save_id',s.named_save_ref,
   'revision',s.named_save_revision::text,'state','released'));
 END IF;
 IF s.kind IN ('save_checkpoint','release_save') AND s.state='executing' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||d.run_ref::text||':'||d.step_key::text,0));
  IF request->>'expected_save_revision' IS NOT NULL THEN
   expected:=(request->>'expected_save_revision')::bigint;
   IF expected<1 OR expected::text<>request->>'expected_save_revision' THEN
    RAISE EXCEPTION 'invalid_checkpoint_revision' USING ERRCODE='22023'; END IF;
  END IF;
 END IF;
 PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
 IF s.kind='save_checkpoint' THEN
  target:=(request#>>'{access,checkpoint,checkpoint_id}')::uuid;
  SELECT cp_row.investigation_id INTO investigation FROM memoriesql.query_checkpoints cp_row
   WHERE cp_row.tenant_id=c.tenant_id AND cp_row.checkpoint_id=target;
 ELSE
  investigation:=(request->>'investigation_id')::uuid;
 END IF;
 SELECT * INTO inv FROM memoriesql.query_investigations WHERE tenant_id=c.tenant_id AND investigation_id=investigation;
 IF inv.owner_identity IS DISTINCT FROM memoriesql.checkpoint_identity_v1(c) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':investigation:'||investigation::text,0));
 SELECT * INTO n FROM memoriesql.query_named_saves WHERE tenant_id=c.tenant_id
  AND investigation_id=investigation AND name=name_value FOR UPDATE;

 IF s.state='complete' OR s.kind='resolve_save' THEN
  IF n.checkpoint_id IS NULL OR (s.state='complete' AND
   (s.named_save_ref IS DISTINCT FROM n.save_id OR s.named_save_revision IS DISTINCT FROM n.revision))
   OR (s.kind='save_checkpoint' AND n.checkpoint_id IS DISTINCT FROM target) THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 ELSE
  IF s.kind='save_checkpoint' THEN
   -- Check the exact requested basis and entire source closure before a CAS
   -- result can reveal any registered name. Never rescue an expired target.
   PERFORM memoriesql.read_automatic_checkpoint_v1(request->'access');
  ELSIF n.save_id IS NULL THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501';
  END IF;
  IF n.revision IS DISTINCT FROM expected OR n.revision=9223372036854775807
   OR (s.kind='release_save' AND n.checkpoint_id IS NULL) THEN
   RAISE EXCEPTION 'checkpoint_save_conflict' USING ERRCODE='22023'; END IF;
  old_checkpoint:=n.checkpoint_id;
  revision_value:=COALESCE(n.revision,0)+1;
  id:=COALESCE(n.save_id,uuidv7());
  IF s.kind='save_checkpoint' THEN
   operation:=(memoriesql.reserve_result_preparation_v1(d.run_ref,d.step_key,s.request_fingerprint,8192)->>'operation_ref')::uuid;
   -- Charge the new save revision and its bounded control record. An owned
   -- release reuses that allocation and the admitted receipt slot, even when
   -- the name's protected bytes are revoked or content allocation is full.
   UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
    allocation_bytes=8192 WHERE tenant_id=d.tenant_id AND operation_ref=operation;
  END IF;
  IF n.save_id IS NULL THEN
   INSERT INTO memoriesql.query_named_saves VALUES(c.tenant_id,investigation,id,name_value,
    revision_value,target,clock_timestamp(),operation) RETURNING * INTO n;
  ELSE
   UPDATE memoriesql.query_named_saves SET revision=revision_value,checkpoint_id=target,recorded_at=clock_timestamp()
    WHERE tenant_id=c.tenant_id AND save_id=id RETURNING * INTO n;
  END IF;
  IF old_checkpoint IS DISTINCT FROM target THEN
   PERFORM memoriesql.end_named_checkpoint_hold_v1(c.tenant_id,old_checkpoint,investigation);
  ELSE
   PERFORM memoriesql.prune_checkpoint_context_receipts_v1(c.tenant_id,target);
  END IF;
 END IF;

 IF s.state='executing' THEN
  UPDATE memoriesql.query_steps SET named_save_ref=n.save_id,named_save_revision=n.revision
   WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 END IF;
 IF s.kind='release_save' THEN
  RETURN jsonb_build_object('released',jsonb_build_object('save_id',n.save_id,'revision',n.revision::text,'state','released'));
 END IF;

 SELECT * INTO cp FROM memoriesql.query_checkpoints WHERE tenant_id=c.tenant_id AND checkpoint_id=n.checkpoint_id;
 access:=jsonb_build_object('checkpoint',jsonb_build_object('checkpoint_id',cp.checkpoint_id,'manifest_digest',cp.manifest_digest),
  'basis',jsonb_build_object('kind','named_save','save_id',n.save_id,'revision',n.revision::text));
 packet:=memoriesql.read_automatic_checkpoint_v1(access);
 binding:=jsonb_build_object('save_id',n.save_id,'investigation_id',investigation,'name',n.name,'revision',n.revision::text,
  'checkpoint',access->'checkpoint','state','active','recorded_at',memoriesql.relation_packet_time(n.recorded_at));
 RETURN jsonb_build_object('save',binding,'retention',packet->'retention')
  ||CASE WHEN s.kind='resolve_save' THEN jsonb_build_object('access',access) ELSE '{}'::jsonb END;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
 RAISE EXCEPTION 'invalid_checkpoint_binding' USING ERRCODE='22023';
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_named_checkpoint_v1(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_named_checkpoint_v1(uuid,jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.record_named_checkpoint_disclosure_v1(delivery uuid,access jsonb,response_hash text,
 transport_bytes bigint,observed_ms bigint) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; c memoriesql.authorization_contexts%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE; cp memoriesql.query_checkpoints%ROWTYPE; inv memoriesql.query_investigations%ROWTYPE;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 IF response_hash IS NULL OR response_hash !~ '^[a-f0-9]{64}$' OR transport_bytes IS NULL
  OR transport_bytes<0 OR transport_bytes>d.reserved_transport OR observed_ms IS NULL OR observed_ms<0 THEN
  RAISE EXCEPTION 'invalid_checkpoint_disclosure' USING ERRCODE='22023'; END IF;
 IF s.kind='release_save' THEN
  IF access IS NOT NULL OR s.named_save_ref IS NULL OR s.named_save_revision IS NULL THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 ELSE
  IF s.kind NOT IN ('read_checkpoint','save_checkpoint','resolve_save')
   OR access#>>'{basis,kind}' IS DISTINCT FROM 'named_save' THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
  SELECT * INTO cp FROM memoriesql.query_checkpoints WHERE tenant_id=c.tenant_id
   AND checkpoint_id=(access#>>'{checkpoint,checkpoint_id}')::uuid;
  SELECT * INTO inv FROM memoriesql.query_investigations WHERE tenant_id=c.tenant_id AND investigation_id=cp.investigation_id;
  IF inv.owner_identity IS DISTINCT FROM memoriesql.checkpoint_identity_v1(c)
   OR cp.manifest_digest IS DISTINCT FROM access#>>'{checkpoint,manifest_digest}'
   OR NOT memoriesql.checkpoint_basis_live_v1(cp,access->'basis')
   OR (s.kind IN ('save_checkpoint','resolve_save') AND (
    s.named_save_ref::text IS DISTINCT FROM access#>>'{basis,save_id}'
    OR s.named_save_revision::text IS DISTINCT FROM access#>>'{basis,revision}')) THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  IF s.kind='read_checkpoint' THEN
   IF s.state='complete' AND (s.checkpoint_ref IS DISTINCT FROM cp.checkpoint_id OR s.content_digest IS DISTINCT FROM cp.manifest_digest) THEN
    RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
   UPDATE memoriesql.query_steps SET checkpoint_ref=cp.checkpoint_id,content_digest=cp.manifest_digest
    WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
  END IF;
  INSERT INTO memoriesql.query_disclosures VALUES(d.tenant_id,delivery,NULL,cp.manifest_digest,
   access,1,0,response_hash,'[]',clock_timestamp(),c.credential_id);
 END IF;
 IF clock_timestamp()>=d.deadline THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000'; END IF;
 UPDATE memoriesql.query_steps SET state='complete',completed_at=COALESCE(completed_at,clock_timestamp())
  WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='available',charged_db_ms=observed_ms,
  charged_transport=transport_bytes,response_sha256=response_hash,settled_at=clock_timestamp()
  WHERE tenant_id=d.tenant_id AND delivery_ref=delivery;
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_named_checkpoint_disclosure_v1(uuid,jsonb,text,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_named_checkpoint_disclosure_v1(uuid,jsonb,text,bigint,bigint) TO memoriesql_application;
