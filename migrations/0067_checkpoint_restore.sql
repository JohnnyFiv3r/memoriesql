-- Restore/fork uses the existing immutable checkpoint, delivery and allocation
-- ledgers. One ordinary artifact parent hold pins the retained working source.
CREATE FUNCTION memoriesql.checkpoint_working_chain_v1(t uuid,checkpoint uuid)
RETURNS TABLE(checkpoint_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 WITH RECURSIVE chain(id) AS (
  SELECT checkpoint
  UNION SELECT h.parent_ref FROM chain c JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=t AND h.child_ref=c.id
   JOIN memoriesql.query_checkpoints p ON p.tenant_id=t AND p.checkpoint_id=h.parent_ref
 )
 SELECT id FROM chain
$$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_working_chain_v1(uuid,uuid) FROM PUBLIC;

-- Called only after authenticating the selected checkpoint's investigation.
-- Every hop stays in that investigation and points strictly backward.
CREATE FUNCTION memoriesql.verify_checkpoint_manifest_v1(cp memoriesql.query_checkpoints) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE a memoriesql.result_preparation_artifacts%ROWTYPE; manifest jsonb; parents jsonb; roots jsonb;
BEGIN
 SELECT ar.* INTO a FROM memoriesql.result_preparation_artifacts ar
  JOIN memoriesql.result_preparation_operations o USING(tenant_id,operation_ref)
  WHERE ar.tenant_id=cp.tenant_id AND ar.artifact_ref=cp.checkpoint_id
   AND ar.operation_ref=cp.operation_ref AND o.state='sealed' FOR SHARE OF ar;
 IF NOT FOUND OR cp.manifest_digest IS NULL OR encode(sha256(a.content_bytes),'hex') IS DISTINCT FROM cp.manifest_digest THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 manifest:=convert_from(a.content_bytes,'UTF8')::jsonb;
 IF manifest->>'checkpoint_id' IS DISTINCT FROM cp.checkpoint_id::text
  OR manifest->>'investigation_id' IS DISTINCT FROM cp.investigation_id::text
  OR manifest->>'branch_id' IS DISTINCT FROM cp.branch_id::text THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(CASE WHEN ar.artifact_sha256=h.parent_sha256 THEN
   jsonb_build_object('checkpoint_id',p.checkpoint_id,'manifest_digest',p.manifest_digest) END),'[]')
  INTO parents FROM memoriesql.result_preparation_parent_holds h
  LEFT JOIN memoriesql.query_checkpoints p ON p.tenant_id=h.tenant_id AND p.checkpoint_id=h.parent_ref
   AND p.investigation_id=cp.investigation_id AND p.investigation_sequence<cp.investigation_sequence
   AND p.manifest_digest IS NOT NULL
  LEFT JOIN memoriesql.result_preparation_artifacts ar ON ar.tenant_id=p.tenant_id AND ar.artifact_ref=p.checkpoint_id
  WHERE h.tenant_id=cp.tenant_id AND h.child_ref=cp.checkpoint_id;
 IF parents IS DISTINCT FROM (CASE WHEN manifest->'working_state_source'='null'::jsonb THEN '[]'::jsonb
  ELSE jsonb_build_array(manifest->'working_state_source') END) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('result',jsonb_build_object('result_id',r.result_id,
  'content_digest',r.content_digest),'frame_ref',r.frame_ref)),'[]') INTO roots
  FROM memoriesql.query_checkpoint_roots r WHERE r.tenant_id=cp.tenant_id AND r.checkpoint_id=cp.checkpoint_id;
 IF jsonb_typeof(manifest->'roots') IS DISTINCT FROM 'array'
  OR jsonb_array_length(manifest->'roots')<>jsonb_array_length(roots)
  OR NOT (roots @> manifest->'roots') THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 RETURN manifest;
END $$;
REVOKE ALL ON FUNCTION memoriesql.verify_checkpoint_manifest_v1(memoriesql.query_checkpoints) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.named_checkpoint_clock_live_v1(cp memoriesql.query_checkpoints,
 basis jsonb,from_automatic boolean DEFAULT false) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; scopes uuid[]; checked_at timestamptz;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT array_agg(DISTINCT e.access_scope_id) INTO scopes
 FROM memoriesql.query_checkpoint_roots r
 LEFT JOIN memoriesql.result_preparation_artifacts a
  ON a.tenant_id=r.tenant_id AND a.artifact_ref=r.result_id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(
  convert_from(decode((convert_from(a.dependency_bytes,'UTF8')::jsonb)->>'manifest_base64','base64'),'UTF8')::jsonb,
  '[{"kind":"source_event"}]'::jsonb)) p(pin)
 LEFT JOIN memoriesql.source_events e ON e.tenant_id=c.tenant_id AND e.workspace_id=c.workspace_id
  AND e.event_id=CASE WHEN pin->>'kind'='source_event' THEN (pin->>'id')::uuid END
 WHERE r.tenant_id=c.tenant_id
  AND r.checkpoint_id IN (SELECT checkpoint_id FROM memoriesql.checkpoint_working_chain_v1(c.tenant_id,cp.checkpoint_id))
  AND pin->>'kind'='source_event';
 PERFORM memoriesql.result_preparation_authority_v1();
 checked_at:=clock_timestamp();
 RETURN memoriesql.checkpoint_basis_live_v1(cp,basis)
  AND (NOT from_automatic OR (cp.automatic_retained AND checked_at<cp.expires_at))
  AND NOT EXISTS(SELECT 1 FROM unnest(scopes) s(scope_id)
   WHERE memoriesql.current_context_scope_time_authorized(s.scope_id,'read',checked_at) IS DISTINCT FROM true);
EXCEPTION WHEN data_exception THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION memoriesql.named_checkpoint_clock_live_v1(memoriesql.query_checkpoints,jsonb,boolean) FROM PUBLIC;

ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_kind_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_kind_check
 CHECK(kind IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint','restore_checkpoint','branch_investigation','read_source_text','save_checkpoint','resolve_save','release_save'));
ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_complete_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_complete_check CHECK(
 (state='complete')=(
  (kind IN ('query','reuse_result') AND helper_artifact_ref IS NULL AND result_id IS NOT NULL
   AND (purged_at IS NOT NULL OR (content_digest IS NOT NULL AND first_row IS NOT NULL AND row_count IS NOT NULL AND page_size IS NOT NULL)))
  OR (kind IN ('inspect','hydrate_source','read_source_text') AND state='complete' AND result_id IS NULL AND first_row IS NULL AND row_count IS NULL AND page_size IS NULL
   AND (purged_at IS NOT NULL OR (helper_artifact_ref IS NOT NULL AND content_digest IS NOT NULL AND helper_page_first IS NOT NULL AND helper_page_count IS NOT NULL)))
  OR (kind IN ('checkpoint','read_checkpoint','restore_checkpoint','branch_investigation') AND state='complete' AND result_id IS NULL AND helper_artifact_ref IS NULL
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
 IF run IS NULL OR step IS NULL OR requested_kind NOT IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint','restore_checkpoint','branch_investigation','read_source_text','save_checkpoint','resolve_save','release_save')
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
  OR s.kind NOT IN ('inspect','hydrate_source','checkpoint','read_checkpoint','restore_checkpoint','branch_investigation','read_source_text','save_checkpoint','resolve_save','release_save') OR s.purged_at IS NOT NULL
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
 inv memoriesql.query_investigations%ROWTYPE; dependency memoriesql.query_checkpoints%ROWTYPE;
 root jsonb; dependency_root record; contexts jsonb:='[]';
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
 manifest:=memoriesql.verify_checkpoint_manifest_v1(cp);
 FOR dependency IN SELECT p.* FROM memoriesql.checkpoint_working_chain_v1(c.tenant_id,cp.checkpoint_id) chain
  JOIN memoriesql.query_checkpoints p ON p.tenant_id=c.tenant_id AND p.checkpoint_id=chain.checkpoint_id
  WHERE p.checkpoint_id<>cp.checkpoint_id ORDER BY p.investigation_sequence LOOP
  PERFORM memoriesql.verify_checkpoint_manifest_v1(dependency);
 END LOOP;
 FOR dependency_root IN SELECT DISTINCT r.result_id,r.content_digest
  FROM memoriesql.checkpoint_working_chain_v1(c.tenant_id,cp.checkpoint_id) chain
  JOIN memoriesql.query_checkpoint_roots r ON r.tenant_id=c.tenant_id AND r.checkpoint_id=chain.checkpoint_id
  ORDER BY r.result_id,r.content_digest LOOP
  PERFORM memoriesql.check_query_result_bytes_v1(dependency_root.result_id,dependency_root.content_digest,true);
 END LOOP;
 FOR root IN SELECT value FROM jsonb_array_elements(manifest->'roots') WITH ORDINALITY
  ORDER BY CASE WHEN manifest->'working_state_source'='null'::jsonb THEN value#>>'{result,result_id}' END,ordinality LOOP
  contexts:=contexts||jsonb_build_array(jsonb_build_object('kind','checkpoint',
   'checkpoint_id',cp.checkpoint_id,'manifest_digest',cp.manifest_digest,'root',root->'result','basis',access->'basis'));
 END LOOP;
 WITH chain AS MATERIALIZED (
  SELECT checkpoint_id FROM memoriesql.checkpoint_working_chain_v1(c.tenant_id,cp.checkpoint_id)
 ), closure AS (
  SELECT checkpoint_id AS artifact_ref FROM chain
  UNION SELECT h.artifact_ref FROM chain JOIN memoriesql.query_checkpoint_holds h
   ON h.tenant_id=c.tenant_id AND h.checkpoint_id=chain.checkpoint_id
 ) SELECT COALESCE(sum(o.allocation_bytes),0) INTO unique_bytes FROM closure
  JOIN memoriesql.result_preparation_artifacts a ON a.tenant_id=c.tenant_id AND a.artifact_ref=closure.artifact_ref
  JOIN memoriesql.result_preparation_operations o ON o.tenant_id=a.tenant_id AND o.operation_ref=a.operation_ref;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('save_id',n.save_id,'revision',n.revision::text)
   ORDER BY n.save_id),'[]'::jsonb),COALESCE(sum(op.allocation_bytes),0) INTO holds,named_bytes
  FROM memoriesql.query_named_saves n JOIN memoriesql.result_preparation_operations op USING(tenant_id,operation_ref)
  WHERE n.tenant_id=c.tenant_id AND n.checkpoint_id=cp.checkpoint_id;
 PERFORM memoriesql.result_preparation_authority_v1();
 IF NOT (CASE WHEN access#>>'{basis,kind}'='named_save' OR manifest->'working_state_source'<>'null'::jsonb
  THEN memoriesql.named_checkpoint_clock_live_v1(cp,access->'basis')
  ELSE memoriesql.checkpoint_basis_live_v1(cp,access->'basis') END) THEN
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

CREATE OR REPLACE FUNCTION memoriesql.query_result_context_live_v1(result_ref uuid,digest text,access jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; checkpoint memoriesql.query_checkpoints%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF access='{"kind":"standalone"}'::jsonb THEN
  RETURN EXISTS(SELECT 1 FROM memoriesql.query_result_creations q WHERE q.tenant_id=c.tenant_id
   AND q.result_id=result_ref AND q.content_digest=digest AND clock_timestamp()<q.expires_at);
 END IF;
 IF access->>'kind' IS DISTINCT FROM 'checkpoint'
  OR access->'root' IS DISTINCT FROM jsonb_build_object('result_id',result_ref,'content_digest',digest) THEN
  RETURN false; END IF;
 SELECT cp.* INTO checkpoint
  FROM memoriesql.query_checkpoints cp JOIN memoriesql.query_investigations i USING(tenant_id,investigation_id)
   JOIN memoriesql.query_checkpoint_roots r USING(tenant_id,checkpoint_id)
   WHERE cp.tenant_id=c.tenant_id AND cp.checkpoint_id=(access->>'checkpoint_id')::uuid
    AND cp.manifest_digest=access->>'manifest_digest'
    AND i.owner_identity=memoriesql.checkpoint_identity_v1(c) AND r.result_id=result_ref AND r.content_digest=digest;
 IF NOT FOUND THEN RETURN false; END IF;
 RETURN CASE WHEN access#>>'{basis,kind}'='named_save'
  OR EXISTS(SELECT 1 FROM memoriesql.result_preparation_parent_holds h
   WHERE h.tenant_id=c.tenant_id AND h.child_ref=checkpoint.checkpoint_id)
  THEN memoriesql.named_checkpoint_clock_live_v1(checkpoint,access->'basis')
  ELSE memoriesql.checkpoint_basis_live_v1(checkpoint,access->'basis') END;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_result_context_live_v1(uuid,text,jsonb) FROM PUBLIC;


CREATE OR REPLACE FUNCTION memoriesql.purge_checkpoint_step_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 IF NEW.purged_at IS NULL OR OLD.purged_at IS NOT NULL THEN RETURN NEW; END IF;
 DELETE FROM memoriesql.query_disclosures x USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND x.tenant_id=d.tenant_id AND x.delivery_ref=d.delivery_ref
   AND ((NEW.kind IN ('checkpoint','read_checkpoint','restore_checkpoint','branch_investigation','save_checkpoint','resolve_save','release_save') AND x.result_id IS NULL)
    OR NOT memoriesql.query_checkpoint_context_retained_v1(x.tenant_id,x.access_context));
 DELETE FROM memoriesql.query_cursors k USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND k.tenant_id=d.tenant_id AND k.issued_by_delivery=d.delivery_ref
   AND NOT memoriesql.query_checkpoint_context_retained_v1(k.tenant_id,k.access_context);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.purge_checkpoint_step_v1() FROM PUBLIC;

-- The source checkpoint already proved original delivery. For a subset keep a
-- finding only when each reference survives in the selected roots or their
-- verified records. A source handle needs its exact selected result binding.
CREATE FUNCTION memoriesql.checkpoint_selected_findings_v1(t uuid,findings jsonb,roots jsonb) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 WITH selected AS MATERIALIZED (
  SELECT convert_from(a.dependency_bytes,'UTF8')::jsonb AS deps
  FROM jsonb_array_elements(roots) r(pin)
  JOIN memoriesql.result_preparation_artifacts a ON a.tenant_id=t AND a.artifact_ref=(pin#>>'{result,result_id}')::uuid
 ), records AS MATERIALIZED (
  SELECT v FROM selected s CROSS JOIN LATERAL jsonb_array_elements(
   convert_from(decode(s.deps->>'records_base64','base64'),'UTF8')::jsonb) x(v)
 )
 SELECT CASE WHEN jsonb_array_length(roots)=0 THEN '[]'::jsonb ELSE COALESCE((
  SELECT jsonb_agg(f ORDER BY sequence) FROM jsonb_array_elements(findings) WITH ORDINALITY cell(f,sequence)
  WHERE NOT EXISTS(
   SELECT 1 FROM (
    SELECT ref FROM jsonb_array_elements(f->'evidence_refs') refs(ref)
    UNION ALL SELECT jsonb_build_object('kind','result_fact','fact',fact)
     FROM jsonb_array_elements(f->'facts') facts(fact)
   ) anchors
   WHERE CASE ref->>'kind'
    -- The original receipt proved the row ordinal. Sealed root IDs cannot
    -- change their contents; the caller already matched each pin and digest.
    WHEN 'result_fact' THEN NOT EXISTS(SELECT 1 FROM jsonb_array_elements(roots) r(pin)
     WHERE pin#>>'{result,result_id}'=ref#>>'{fact,result_id}')
    WHEN 'observation' THEN NOT EXISTS(SELECT 1 FROM records
     WHERE v->>'kind'='accepted_bead' AND v->>'id'=ref->>'version_ref' AND v#>>'{row,bead_id}'=ref->>'ref')
    WHEN 'statement' THEN NOT EXISTS(SELECT 1 FROM records
     WHERE v->>'kind'='statement' AND v->>'id'=ref->>'ref' AND v#>>'{row,bead_version_id}'=ref->>'version_ref')
    WHEN 'source' THEN NOT EXISTS(SELECT 1 FROM selected s
     CROSS JOIN LATERAL (SELECT s.deps->'evidence_bindings'->(ref->>'ref') AS pin) exact
     WHERE exact.pin->>'source_unit_id'=ref->>'unit_ref' AND exact.pin->>'content_sha256'=ref->>'content_sha256')
    ELSE true END)
 ),'[]'::jsonb) END
$$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_selected_findings_v1(uuid,jsonb,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.prepare_restore_checkpoint_v1(delivery uuid,request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 c memoriesql.authorization_contexts%ROWTYPE; inv memoriesql.query_investigations%ROWTYPE;
 packet jsonb; source_manifest jsonb; source_pin jsonb; selected jsonb; roots jsonb; findings jsonb; pending jsonb;
 new_id uuid; branch uuid; hold_count bigint; capacity bigint; now_at timestamptz;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 IF s.kind NOT IN ('restore_checkpoint','branch_investigation')
  OR request->>'kind' IS DISTINCT FROM s.kind OR request->>'run_ref' IS DISTINCT FROM d.run_ref::text
  OR request->>'step_key' IS DISTINCT FROM d.step_key::text THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF s.state='complete' THEN
  RETURN memoriesql.read_automatic_checkpoint_v1(jsonb_build_object('checkpoint',
   jsonb_build_object('checkpoint_id',s.checkpoint_ref,'manifest_digest',s.content_digest),
   'basis','{"kind":"automatic"}'::jsonb));
 END IF;
 IF s.state<>'executing' THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||d.run_ref::text||':'||d.step_key::text,0));
 -- This read authenticates the exact incoming basis and takes the allocator
 -- and investigation locks retained through the final disclosure.
 packet:=memoriesql.read_automatic_checkpoint_v1(request->'access');
 source_manifest:=packet->'checkpoint';
 source_pin:=request#>'{access,checkpoint}';
 selected:=request->'selected_roots';
 IF jsonb_typeof(selected) IS DISTINCT FROM 'array' OR jsonb_array_length(selected)>32
  OR (SELECT count(DISTINCT p->>'result_id') FROM jsonb_array_elements(selected) ids(p))<>jsonb_array_length(selected) THEN
  RAISE EXCEPTION 'invalid_checkpoint_selection' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(jsonb_agg(r ORDER BY position),'[]'::jsonb) INTO roots
  FROM jsonb_array_elements(selected) WITH ORDINALITY picks(pin,position)
  JOIN jsonb_array_elements(source_manifest->'roots') root(r) ON r->'result'=pin;
 IF jsonb_array_length(roots)<>jsonb_array_length(selected) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF s.checkpoint_pending IS NOT NULL THEN
  IF s.checkpoint_pending->'access' IS DISTINCT FROM request->'access'
   OR s.checkpoint_pending#>'{manifest,roots}' IS DISTINCT FROM roots THEN
   RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('pending',s.checkpoint_pending->'manifest');
 END IF;
 findings:=CASE WHEN jsonb_array_length(roots)>0 AND jsonb_array_length(roots)=jsonb_array_length(source_manifest->'roots')
  THEN source_manifest->'findings'
  ELSE memoriesql.checkpoint_selected_findings_v1(c.tenant_id,source_manifest->'findings',roots) END;

 WITH RECURSIVE closure(artifact_ref) AS (
  SELECT (value#>>'{result,result_id}')::uuid FROM jsonb_array_elements(roots)
  UNION SELECT h.parent_ref FROM closure x JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=c.tenant_id AND h.child_ref=x.artifact_ref
 ) SELECT count(*) INTO hold_count FROM closure;
 -- Same checkpoint allowance with the one additional artifact-parent hold.
 capacity:=73728+512+hold_count*1024;
 IF capacity>67108864 THEN RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000'; END IF;
 PERFORM memoriesql.reserve_result_preparation_v1(d.run_ref,d.step_key,s.request_fingerprint,capacity);
 SELECT * INTO STRICT inv FROM memoriesql.query_investigations
  WHERE tenant_id=c.tenant_id AND investigation_id=(source_manifest->>'investigation_id')::uuid;
 new_id:=uuidv7(); branch:=uuidv7(); now_at:=clock_timestamp();
 pending:=jsonb_build_object('checkpoint_id',new_id,'investigation_id',inv.investigation_id,'branch_id',branch,
  'sequence','1','investigation_sequence',inv.next_sequence::text,'predecessor',NULL,'fork_origin',source_pin,
  'working_state_source',source_pin,'created_at',memoriesql.relation_packet_time(now_at),
  'automatic_expires_at',memoriesql.relation_packet_time(now_at+interval '720 hours'),
  'question',source_manifest->'question','progress',source_manifest->'progress','findings',findings,'roots',roots);
 UPDATE memoriesql.query_steps SET checkpoint_pending=jsonb_build_object('manifest',pending,'access',request->'access')
  WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 RETURN jsonb_build_object('pending',pending);
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_restore_checkpoint_v1(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_restore_checkpoint_v1(uuid,jsonb) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.publish_automatic_checkpoint_v1(delivery uuid,request jsonb,body bytea,digest text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; data jsonb; root jsonb; hash text;
 id uuid; investigation uuid; branch uuid; extra_charge bigint; held_count bigint;
 fork boolean; parents jsonb:='[]'::jsonb; source_access jsonb; source_id uuid; source_cp memoriesql.query_checkpoints%ROWTYPE;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 data:=convert_from(body,'UTF8')::jsonb;
 fork:=s.kind IN ('restore_checkpoint','branch_investigation');
 IF s.kind NOT IN ('checkpoint','restore_checkpoint','branch_investigation') OR s.state<>'executing'
  OR (CASE WHEN fork THEN s.checkpoint_pending->'manifest' ELSE s.checkpoint_pending END) IS DISTINCT FROM data
  OR body IS NULL OR octet_length(body)>32768 OR encode(sha256(body),'hex') IS DISTINCT FROM digest THEN
  RAISE EXCEPTION 'invalid_checkpoint' USING ERRCODE='22023'; END IF;
 IF fork THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(d.tenant_id::text||':result-preparation-operation:'||d.run_ref::text||':'||d.step_key::text,0));
 END IF;
 PERFORM memoriesql.checkpoint_ledger_lock_v1(d.tenant_id,d.workspace_id);
 id:=(data->>'checkpoint_id')::uuid; investigation:=(data->>'investigation_id')::uuid; branch:=(data->>'branch_id')::uuid;
 IF fork THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(d.tenant_id::text||':investigation:'||investigation::text,0));
  source_access:=s.checkpoint_pending->'access'; source_id:=(source_access#>>'{checkpoint,checkpoint_id}')::uuid;
  SELECT * INTO source_cp FROM memoriesql.query_checkpoints p WHERE p.tenant_id=d.tenant_id
   AND p.checkpoint_id=source_id AND p.investigation_id=investigation;
  IF NOT EXISTS(SELECT 1 FROM memoriesql.query_investigations i WHERE i.tenant_id=d.tenant_id
      AND i.investigation_id=investigation
      AND i.owner_identity=memoriesql.checkpoint_identity_v1(memoriesql.result_preparation_authority_v1()))
   OR source_cp.manifest_digest IS DISTINCT FROM source_access#>>'{checkpoint,manifest_digest}'
   OR request->'access' IS DISTINCT FROM source_access
   OR NOT memoriesql.checkpoint_basis_live_v1(source_cp,source_access->'basis') THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  -- A raw retry after loss of the original transaction cannot use a staged
  -- sequence that another append has already consumed.
  IF (SELECT i.next_sequence FROM memoriesql.query_investigations i WHERE i.tenant_id=d.tenant_id
      AND i.investigation_id=investigation) IS DISTINCT FROM (data->>'investigation_sequence')::bigint THEN
   RAISE EXCEPTION 'checkpoint_branch_conflict' USING ERRCODE='22023'; END IF;
  SELECT jsonb_build_array(jsonb_build_object('artifact_ref',artifact_ref,'artifact_sha256',artifact_sha256))
   INTO parents FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND artifact_ref=source_id;
  IF parents IS NULL THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  INSERT INTO memoriesql.query_investigation_branches VALUES(d.tenant_id,branch,investigation,NULL,NULL,1);
 END IF;
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 IF o.state IS DISTINCT FROM 'reserved' THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
  int8send(octet_length(body)::bigint)||body||int8send(0)||int8send(0)),'hex');
 PERFORM memoriesql.seal_result_preparation_v1(o.operation_ref,o.ownership_ref,id,body,''::bytea,''::bytea,hash,parents);
 INSERT INTO memoriesql.query_checkpoints VALUES(d.tenant_id,id,investigation,branch,(data->>'investigation_sequence')::bigint,
  digest,o.operation_ref,(data->>'created_at')::timestamptz,(data->>'automatic_expires_at')::timestamptz,true);
 FOR root IN SELECT value FROM jsonb_array_elements(data->'roots') LOOP
  INSERT INTO memoriesql.query_checkpoint_roots VALUES(d.tenant_id,id,(root#>>'{result,result_id}')::uuid,
   root#>>'{result,content_digest}',(root->>'frame_ref')::uuid);
 END LOOP;
 INSERT INTO memoriesql.query_checkpoint_holds
 WITH RECURSIVE closure(artifact_ref) AS (
  SELECT (value#>>'{result,result_id}')::uuid FROM jsonb_array_elements(data->'roots')
  UNION SELECT h.parent_ref FROM closure x JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=d.tenant_id AND h.child_ref=x.artifact_ref)
 SELECT d.tenant_id,id,artifact_ref FROM closure;
 GET DIAGNOSTICS held_count=ROW_COUNT;
 extra_charge:=32768+held_count*1024;
 IF o.allocation_bytes+extra_charge>o.reservation_bytes OR octet_length(body)+8192+512*jsonb_array_length(parents)+extra_charge>o.reservation_bytes THEN
  RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000'; END IF;
 UPDATE memoriesql.result_preparation_operations SET allocation_bytes=allocation_bytes+extra_charge,
  new_allocation_bytes=new_allocation_bytes+extra_charge WHERE tenant_id=d.tenant_id AND operation_ref=o.operation_ref;
 UPDATE memoriesql.query_investigations SET next_sequence=next_sequence+1 WHERE tenant_id=d.tenant_id AND investigation_id=investigation;
 UPDATE memoriesql.query_investigation_branches SET next_sequence=next_sequence+1,head_ref=id,head_digest=digest
  WHERE tenant_id=d.tenant_id AND branch_id=branch;
 UPDATE memoriesql.query_steps SET checkpoint_ref=id,content_digest=digest,checkpoint_pending=CASE WHEN fork THEN s.checkpoint_pending END
  WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 IF NOT fork THEN PERFORM memoriesql.prune_automatic_checkpoints_v1(d.tenant_id,investigation); END IF;
 RETURN memoriesql.read_automatic_checkpoint_v1(jsonb_build_object('checkpoint',
  jsonb_build_object('checkpoint_id',id,'manifest_digest',digest),'basis',jsonb_build_object('kind','automatic')));
END $$;
REVOKE ALL ON FUNCTION memoriesql.publish_automatic_checkpoint_v1(uuid,jsonb,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.publish_automatic_checkpoint_v1(uuid,jsonb,bytea,text) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.record_checkpoint_disclosure_v1(delivery uuid,checkpoint uuid,digest text,
 response_hash text,transport_bytes bigint,observed_ms bigint) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; c memoriesql.authorization_contexts%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE; cp memoriesql.query_checkpoints%ROWTYPE;
 source memoriesql.query_checkpoints%ROWTYPE; source_access jsonb; fork boolean;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 -- Full closure read precedes sizing in the same authority-fenced transaction.
 -- The ledger/investigation locks remain held; recheck time without a second
 -- unmeasured closure traversal after the response charge was computed.
 SELECT p.* INTO cp FROM memoriesql.query_checkpoints p JOIN memoriesql.query_investigations i
  USING(tenant_id,investigation_id) WHERE p.tenant_id=d.tenant_id AND p.checkpoint_id=checkpoint
   AND p.manifest_digest=digest AND p.automatic_retained AND clock_timestamp()<p.expires_at
   AND i.owner_identity=memoriesql.checkpoint_identity_v1(c);
 IF NOT FOUND THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF response_hash IS NULL OR response_hash !~ '^[a-f0-9]{64}$' OR transport_bytes IS NULL
  OR transport_bytes<0 OR transport_bytes>d.reserved_transport OR observed_ms IS NULL OR observed_ms<0 THEN
  RAISE EXCEPTION 'invalid_checkpoint_disclosure' USING ERRCODE='22023'; END IF;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 fork:=s.kind IN ('restore_checkpoint','branch_investigation') AND s.state='executing';
 IF (s.state='complete' OR fork) AND (s.checkpoint_ref IS DISTINCT FROM checkpoint OR s.content_digest IS DISTINCT FROM digest) THEN
  RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
 INSERT INTO memoriesql.query_disclosures VALUES(d.tenant_id,delivery,NULL,digest,
  jsonb_build_object('checkpoint',jsonb_build_object('checkpoint_id',checkpoint,'manifest_digest',digest),'basis',jsonb_build_object('kind','automatic')),
  1,0,response_hash,'[]',clock_timestamp(),c.credential_id);
 IF fork THEN
  source_access:=s.checkpoint_pending->'access';
  SELECT p.* INTO source FROM memoriesql.query_checkpoints p JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=p.tenant_id AND h.parent_ref=p.checkpoint_id
   WHERE p.tenant_id=d.tenant_id AND h.child_ref=checkpoint
    AND p.checkpoint_id=(source_access#>>'{checkpoint,checkpoint_id}')::uuid
    AND p.manifest_digest=source_access#>>'{checkpoint,manifest_digest}';
  IF NOT FOUND OR NOT memoriesql.checkpoint_basis_live_v1(source,source_access->'basis') THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  -- Only the publication itself may end the input automatic slot by pruning.
  PERFORM memoriesql.prune_automatic_checkpoints_v1(d.tenant_id,cp.investigation_id);
 END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_parent_holds h
  WHERE h.tenant_id=d.tenant_id AND h.child_ref=checkpoint)
  AND NOT memoriesql.named_checkpoint_clock_live_v1(cp,'{"kind":"automatic"}'::jsonb) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF fork AND source_access#>>'{basis,kind}'='automatic' AND clock_timestamp()>=source.expires_at THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF fork THEN PERFORM memoriesql.query_helper_delivery_v1(delivery); END IF;
 UPDATE memoriesql.query_steps SET state='complete',checkpoint_ref=checkpoint,content_digest=digest,checkpoint_pending=NULL,
  completed_at=COALESCE(completed_at,clock_timestamp()) WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='available',charged_db_ms=observed_ms,
  charged_transport=transport_bytes,response_sha256=response_hash,settled_at=clock_timestamp()
  WHERE tenant_id=d.tenant_id AND delivery_ref=delivery;
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_checkpoint_disclosure_v1(uuid,uuid,text,text,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_checkpoint_disclosure_v1(uuid,uuid,text,text,bigint,bigint) TO memoriesql_application;


-- Reap only a checkpoint with no automatic, named, or working-state consumer.
-- If it was the last working-state consumer of an older checkpoint, reevaluate
-- that predecessor using this same effective hold end. No source row is copied.
CREATE FUNCTION memoriesql.discard_checkpoint_working_chain_v1(t uuid,checkpoint uuid,ended_at timestamptz) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE cursor_id uuid:=checkpoint; parent uuid; op uuid; expires timestamptz;
BEGIN
 WHILE cursor_id IS NOT NULL LOOP
  UPDATE memoriesql.query_checkpoints cp SET manifest_digest=NULL,pruned_at=clock_timestamp()
   WHERE cp.tenant_id=t AND cp.checkpoint_id=cursor_id AND NOT cp.automatic_retained
    AND cp.manifest_digest IS NOT NULL
    AND NOT EXISTS(SELECT 1 FROM memoriesql.query_named_saves n WHERE n.tenant_id=t AND n.checkpoint_id=cursor_id)
    AND NOT EXISTS(SELECT 1 FROM memoriesql.result_preparation_parent_holds h
     WHERE h.tenant_id=t AND h.parent_ref=cursor_id)
   RETURNING operation_ref,expires_at INTO op,expires;
  IF NOT FOUND THEN RETURN; END IF;
  ended_at:=COALESCE(ended_at,LEAST(clock_timestamp(),expires));
  INSERT INTO memoriesql.query_result_cleanup_hold_ends(tenant_id,artifact_ref,last_hold_end)
   SELECT h.tenant_id,h.artifact_ref,ended_at FROM memoriesql.query_checkpoint_holds h
   WHERE h.tenant_id=t AND h.checkpoint_id=cursor_id
   ON CONFLICT(tenant_id,artifact_ref) DO UPDATE SET last_hold_end=GREATEST(
    memoriesql.query_result_cleanup_hold_ends.last_hold_end,EXCLUDED.last_hold_end);
  DELETE FROM memoriesql.query_checkpoint_holds WHERE tenant_id=t AND checkpoint_id=cursor_id;
  DELETE FROM memoriesql.query_checkpoint_roots WHERE tenant_id=t AND checkpoint_id=cursor_id;
  DELETE FROM memoriesql.result_preparation_parent_holds WHERE tenant_id=t AND child_ref=cursor_id RETURNING parent_ref INTO parent;
  DELETE FROM memoriesql.result_preparation_artifacts WHERE tenant_id=t AND artifact_ref=cursor_id;
  UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
   encoded_bytes=0,allocation_bytes=40960 WHERE tenant_id=t AND operation_ref=op;
  PERFORM memoriesql.prune_checkpoint_context_receipts_v1(t,cursor_id);
  cursor_id:=parent;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION memoriesql.discard_checkpoint_working_chain_v1(uuid,uuid,timestamptz) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.discard_unheld_checkpoint_v1(t uuid,checkpoint uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 PERFORM memoriesql.discard_checkpoint_working_chain_v1(t,checkpoint,NULL);
END $$;
REVOKE ALL ON FUNCTION memoriesql.discard_unheld_checkpoint_v1(uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.end_named_checkpoint_hold_v1(t uuid,checkpoint uuid,investigation uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE released_at timestamptz:=clock_timestamp();
BEGIN
 IF checkpoint IS NULL THEN RETURN; END IF;
 IF NOT EXISTS(SELECT 1 FROM memoriesql.query_named_saves n WHERE n.tenant_id=t AND n.checkpoint_id=checkpoint) THEN
  INSERT INTO memoriesql.query_result_cleanup_hold_ends(tenant_id,artifact_ref,last_hold_end)
   SELECT h.tenant_id,h.artifact_ref,released_at FROM memoriesql.query_checkpoint_holds h
   WHERE h.tenant_id=t AND h.checkpoint_id=checkpoint
   ON CONFLICT(tenant_id,artifact_ref) DO UPDATE SET last_hold_end=GREATEST(
    memoriesql.query_result_cleanup_hold_ends.last_hold_end,EXCLUDED.last_hold_end);
  PERFORM memoriesql.discard_checkpoint_working_chain_v1(t,checkpoint,released_at);
 END IF;
 PERFORM memoriesql.prune_checkpoint_context_receipts_v1(t,checkpoint);
 PERFORM memoriesql.prune_automatic_checkpoints_v1(t,investigation);
END $$;
REVOKE ALL ON FUNCTION memoriesql.end_named_checkpoint_hold_v1(uuid,uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.query_result_cleanup_eligible_at_v1(t uuid,result_ref uuid) RETURNS timestamptz
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 -- UNION, not an arbitrary depth/count cutoff, follows the immutable hold DAG.
 WITH RECURSIVE consumers(artifact_ref) AS (
  SELECT result_ref
  UNION SELECT h.child_ref FROM consumers c JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=t AND h.parent_ref=c.artifact_ref
 ), checkpoint_consumers(id) AS (
  SELECT h.checkpoint_id FROM consumers c JOIN memoriesql.query_checkpoint_holds h
   ON h.tenant_id=t AND h.artifact_ref=c.artifact_ref
  UNION SELECT h.child_ref FROM checkpoint_consumers x JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=t AND h.parent_ref=x.id
   JOIN memoriesql.query_checkpoints cp ON cp.tenant_id=t AND cp.checkpoint_id=h.child_ref
 ), promises AS (
  SELECT q.expires_at AS ends_at FROM consumers c JOIN memoriesql.query_result_creations q
   ON q.tenant_id=t AND q.result_id=c.artifact_ref
  UNION ALL SELECT e.last_hold_end FROM consumers c JOIN memoriesql.query_result_cleanup_hold_ends e
   ON e.tenant_id=t AND e.artifact_ref=c.artifact_ref
  UNION ALL SELECT cp.expires_at FROM checkpoint_consumers x
   JOIN memoriesql.query_checkpoints cp ON cp.tenant_id=t AND cp.checkpoint_id=x.id
   WHERE cp.automatic_retained
 )
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM memoriesql.query_result_creations WHERE tenant_id=t AND result_id=result_ref)
  OR EXISTS(SELECT 1 FROM consumers c JOIN memoriesql.result_preparation_artifacts a
   ON a.tenant_id=t AND a.artifact_ref=c.artifact_ref
   WHERE NOT EXISTS(SELECT 1 FROM memoriesql.query_result_creations q WHERE q.tenant_id=t AND q.result_id=c.artifact_ref))
  OR EXISTS(SELECT 1 FROM checkpoint_consumers x
   JOIN memoriesql.query_named_saves n ON n.tenant_id=t AND n.checkpoint_id=x.id)
  -- A private preparation with no declared result lifetime retains its explicit
  -- hold until owned release. It cannot be treated as expired by guessing time.
  THEN NULL ELSE (SELECT max(ends_at) FROM promises) END
$$;
REVOKE ALL ON FUNCTION memoriesql.query_result_cleanup_eligible_at_v1(uuid,uuid) FROM PUBLIC;
