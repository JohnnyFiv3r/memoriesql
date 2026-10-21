-- Automatic checkpoints under the existing agent-sql-results.v1 contract.
-- No named saves, restore, saved-input SQL or new authority is introduced.
CREATE TABLE memoriesql.query_investigations (
 tenant_id uuid NOT NULL, investigation_id uuid NOT NULL, workspace_id uuid NOT NULL,
 owner_identity jsonb NOT NULL, next_sequence bigint NOT NULL CHECK(next_sequence>0),
 PRIMARY KEY(tenant_id,investigation_id)
);
CREATE TABLE memoriesql.query_investigation_branches (
 tenant_id uuid NOT NULL, branch_id uuid NOT NULL, investigation_id uuid NOT NULL,
 head_ref uuid, head_digest text, next_sequence bigint NOT NULL CHECK(next_sequence>0),
 PRIMARY KEY(tenant_id,branch_id),
 FOREIGN KEY(tenant_id,investigation_id) REFERENCES memoriesql.query_investigations
);
CREATE TABLE memoriesql.query_checkpoints (
 tenant_id uuid NOT NULL, checkpoint_id uuid NOT NULL, investigation_id uuid NOT NULL,
 branch_id uuid NOT NULL, investigation_sequence bigint NOT NULL,
 manifest_digest text CHECK(manifest_digest ~ '^[a-f0-9]{64}$'),
 operation_ref uuid NOT NULL, created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, automatic_retained boolean NOT NULL DEFAULT true,
 pruned_at timestamptz,
 PRIMARY KEY(tenant_id,checkpoint_id), UNIQUE(tenant_id,investigation_id,investigation_sequence),
 FOREIGN KEY(tenant_id,investigation_id) REFERENCES memoriesql.query_investigations,
 FOREIGN KEY(tenant_id,branch_id) REFERENCES memoriesql.query_investigation_branches,
 FOREIGN KEY(tenant_id,operation_ref) REFERENCES memoriesql.result_preparation_operations,
 CHECK(expires_at=created_at+interval '720 hours'),
 -- A pruned or expired slot keeps no manifest digest; its prune time starts the
 -- 30-day noncontent tombstone that ends with its publishing run's tombstone.
 CHECK((manifest_digest IS NOT NULL)=automatic_retained AND (pruned_at IS NULL)=automatic_retained)
);
CREATE INDEX query_checkpoints_operation ON memoriesql.query_checkpoints(tenant_id,operation_ref);
CREATE TABLE memoriesql.query_checkpoint_roots (
 tenant_id uuid NOT NULL, checkpoint_id uuid NOT NULL, result_id uuid NOT NULL,
 content_digest text NOT NULL, frame_ref uuid NOT NULL,
 PRIMARY KEY(tenant_id,checkpoint_id,result_id),
 FOREIGN KEY(tenant_id,checkpoint_id) REFERENCES memoriesql.query_checkpoints
);
CREATE TABLE memoriesql.query_checkpoint_holds (
 tenant_id uuid NOT NULL, checkpoint_id uuid NOT NULL, artifact_ref uuid NOT NULL,
 PRIMARY KEY(tenant_id,checkpoint_id,artifact_ref),
 FOREIGN KEY(tenant_id,checkpoint_id) REFERENCES memoriesql.query_checkpoints,
 FOREIGN KEY(tenant_id,artifact_ref) REFERENCES memoriesql.result_preparation_artifacts
);
CREATE INDEX query_checkpoint_holds_artifact ON memoriesql.query_checkpoint_holds(tenant_id,artifact_ref);
-- One charged control record per retained artifact, not a new disclosure route.
-- Record actual early releases, and natural expiry rather than delayed GC time.
CREATE TABLE memoriesql.query_result_cleanup_hold_ends (
 tenant_id uuid NOT NULL, artifact_ref uuid NOT NULL, last_hold_end timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,artifact_ref),
 FOREIGN KEY(tenant_id,artifact_ref) REFERENCES memoriesql.result_preparation_artifacts ON DELETE CASCADE
);
ALTER TABLE memoriesql.query_result_cleanup_hold_ends ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_result_cleanup_hold_ends FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_result_cleanup_hold_ends FROM PUBLIC,memoriesql_application;
ALTER TABLE memoriesql.query_investigations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_investigations FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_investigation_branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_investigation_branches FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_checkpoint_roots ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_checkpoint_roots FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_checkpoint_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_checkpoint_holds FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_investigations,memoriesql.query_investigation_branches,
 memoriesql.query_checkpoints,memoriesql.query_checkpoint_roots,memoriesql.query_checkpoint_holds
 FROM PUBLIC,memoriesql_application;
ALTER TABLE memoriesql.query_steps ADD COLUMN checkpoint_ref uuid;
ALTER TABLE memoriesql.query_steps ADD COLUMN checkpoint_pending jsonb;
ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_kind_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_kind_check
 CHECK(kind IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint'));
ALTER TABLE memoriesql.query_steps DROP CONSTRAINT query_steps_helper_complete_check;
ALTER TABLE memoriesql.query_steps ADD CONSTRAINT query_steps_helper_complete_check CHECK(
 (state='complete')=(
  (kind IN ('query','reuse_result') AND helper_artifact_ref IS NULL AND result_id IS NOT NULL
   AND (purged_at IS NOT NULL OR (content_digest IS NOT NULL AND first_row IS NOT NULL AND row_count IS NOT NULL AND page_size IS NOT NULL)))
  OR (kind IN ('inspect','hydrate_source') AND state='complete' AND result_id IS NULL AND first_row IS NULL AND row_count IS NULL AND page_size IS NULL
   AND (purged_at IS NOT NULL OR (helper_artifact_ref IS NOT NULL AND content_digest IS NOT NULL AND helper_page_first IS NOT NULL AND helper_page_count IS NOT NULL)))
  OR (kind IN ('checkpoint','read_checkpoint') AND state='complete' AND result_id IS NULL AND helper_artifact_ref IS NULL
   AND (purged_at IS NOT NULL OR (checkpoint_ref IS NOT NULL AND content_digest IS NOT NULL)))));
-- Only explicit, validated contexts can populate either receipt/cursor.
DO $$ DECLARE item record;
BEGIN
 FOR item IN SELECT conrelid::regclass AS relation,conname FROM pg_constraint
  WHERE conrelid IN ('memoriesql.query_cursors'::regclass,'memoriesql.query_disclosures'::regclass)
  AND contype='c' AND pg_get_constraintdef(oid) LIKE '%access_context%' LOOP
  EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',item.relation,item.conname);
 END LOOP;
END $$;

ALTER TABLE memoriesql.query_cursors ADD CONSTRAINT query_cursors_access_context_check CHECK(COALESCE(
 access_context='{"kind":"standalone"}'::jsonb OR
 (access_context->>'kind'='checkpoint' AND access_context ?& ARRAY['kind','checkpoint_id','manifest_digest','root','basis']
  AND access_context-ARRAY['kind','checkpoint_id','manifest_digest','root','basis']='{}'::jsonb
  AND access_context->'basis'='{"kind":"automatic"}'::jsonb),false));
ALTER TABLE memoriesql.query_disclosures ADD CONSTRAINT query_disclosures_access_context_check CHECK(COALESCE(
 access_context='{"kind":"standalone"}'::jsonb OR
 (access_context->>'kind'='checkpoint' AND access_context ?& ARRAY['kind','checkpoint_id','manifest_digest','root','basis']
  AND access_context-ARRAY['kind','checkpoint_id','manifest_digest','root','basis']='{}'::jsonb
  AND access_context->'basis'='{"kind":"automatic"}'::jsonb) OR
 (access_context ?& ARRAY['checkpoint','basis'] AND access_context-ARRAY['checkpoint','basis']='{}'::jsonb
  AND access_context->'basis'='{"kind":"automatic"}'::jsonb),false));

-- The checkpoint a receipt or cursor context names (either receipt shape), or NULL.
CREATE FUNCTION memoriesql.query_checkpoint_context_ref_v1(access jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN access->>'kind'='checkpoint' THEN access->>'checkpoint_id'
  ELSE access#>>'{checkpoint,checkpoint_id}' END
$$;
REVOKE ALL ON FUNCTION memoriesql.query_checkpoint_context_ref_v1(jsonb) FROM PUBLIC;
CREATE INDEX query_disclosures_checkpoint_context ON memoriesql.query_disclosures
 (tenant_id,memoriesql.query_checkpoint_context_ref_v1(access_context))
 WHERE memoriesql.query_checkpoint_context_ref_v1(access_context) IS NOT NULL;
CREATE INDEX query_cursors_checkpoint_context ON memoriesql.query_cursors
 (tenant_id,memoriesql.query_checkpoint_context_ref_v1(access_context))
 WHERE memoriesql.query_checkpoint_context_ref_v1(access_context) IS NOT NULL;
CREATE INDEX query_cursors_issued_by ON memoriesql.query_cursors(tenant_id,issued_by_delivery);

CREATE FUNCTION memoriesql.checkpoint_identity_v1(c memoriesql.authorization_contexts)
RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('workspace_id',c.workspace_id,'principal_id',c.principal_id,
  'principal_kind',c.principal_kind,'user_id',c.user_id,
  'on_behalf_of_user_id',c.on_behalf_of_user_id,'pairing_grant_id',c.pairing_grant_id)
$$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_identity_v1(memoriesql.authorization_contexts) FROM PUBLIC;

CREATE FUNCTION memoriesql.checkpoint_ledger_lock_v1(t uuid,w uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation:'||w::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(t,w,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
END $$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_ledger_lock_v1(uuid,uuid) FROM PUBLIC;

-- The retained verifier below is private: no caller may select an expiry bypass.
-- Only a valid explicit checkpoint context calls it with retained=true.

CREATE FUNCTION memoriesql.read_automatic_checkpoint_v1(access jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; cp memoriesql.query_checkpoints%ROWTYPE;
 inv memoriesql.query_investigations%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; root record; contexts jsonb:='[]';
 manifest jsonb; unique_bytes bigint;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF access->'basis' IS DISTINCT FROM '{"kind":"automatic"}'::jsonb THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
 SELECT * INTO cp FROM memoriesql.query_checkpoints WHERE tenant_id=c.tenant_id
  AND checkpoint_id=(access#>>'{checkpoint,checkpoint_id}')::uuid;
 SELECT * INTO inv FROM memoriesql.query_investigations WHERE tenant_id=c.tenant_id AND investigation_id=cp.investigation_id;
 IF cp.checkpoint_id IS NULL OR inv.owner_identity IS DISTINCT FROM memoriesql.checkpoint_identity_v1(c) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':investigation:'||cp.investigation_id::text,0));
 SELECT * INTO cp FROM memoriesql.query_checkpoints WHERE tenant_id=c.tenant_id AND checkpoint_id=cp.checkpoint_id FOR SHARE;
 IF NOT cp.automatic_retained OR clock_timestamp()>=cp.expires_at
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
 PERFORM memoriesql.result_preparation_authority_v1();
 IF clock_timestamp()>=cp.expires_at THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('checkpoint',manifest,'root_contexts',contexts,
  'retention',jsonb_build_object('automatic_until',memoriesql.relation_packet_time(cp.expires_at),
   'automatic_retained',true,'named_holds','[]'::jsonb,'retained',true,'hold_ref',cp.checkpoint_id,
   'unique_bytes',unique_bytes::text,'context_end','automatic_expiry_or_prune'));
END $$;
REVOKE ALL ON FUNCTION memoriesql.read_automatic_checkpoint_v1(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.query_result_access_v1(result_ref uuid,digest text,access jsonb) RETURNS jsonb
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
  'valid_until',packet#>'{retention,automatic_until}','end_condition','checkpoint_expiry_or_prune','hold_ref',access->'checkpoint_id');
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_result_access_v1(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_result_access_v1(uuid,text,jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.prune_automatic_checkpoints_v1(t uuid,investigation uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE cp record;
BEGIN
 FOR cp IN SELECT q.* FROM memoriesql.query_checkpoints q WHERE tenant_id=t
  AND investigation_id=investigation AND automatic_retained AND
  (expires_at<=clock_timestamp() OR investigation_sequence IN (
   SELECT investigation_sequence FROM memoriesql.query_checkpoints WHERE tenant_id=t
    AND investigation_id=investigation AND automatic_retained ORDER BY investigation_sequence DESC OFFSET 20))
  ORDER BY investigation_sequence FOR UPDATE LOOP
  UPDATE memoriesql.query_checkpoints SET automatic_retained=false,manifest_digest=NULL,
   pruned_at=clock_timestamp() WHERE tenant_id=t AND checkpoint_id=cp.checkpoint_id;
  DELETE FROM memoriesql.query_checkpoint_holds WHERE tenant_id=t AND checkpoint_id=cp.checkpoint_id;
  DELETE FROM memoriesql.query_checkpoint_roots WHERE tenant_id=t AND checkpoint_id=cp.checkpoint_id;
  DELETE FROM memoriesql.result_preparation_artifacts WHERE tenant_id=t AND artifact_ref=cp.checkpoint_id;
  -- Retain charged noncontent identity/lineage/control stubs, never a free audit.
  UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
   encoded_bytes=0,allocation_bytes=40960 WHERE tenant_id=t AND operation_ref=cp.operation_ref;
  -- Receipts and cursors bound to the released context carry its manifest
  -- digest. Those of runs already purged go now; a live run's go at its purge.
  DELETE FROM memoriesql.query_disclosures x USING memoriesql.query_deliveries d,memoriesql.query_run_purges p
   WHERE x.tenant_id=t AND memoriesql.query_checkpoint_context_ref_v1(x.access_context)=cp.checkpoint_id::text
    AND d.tenant_id=x.tenant_id AND d.delivery_ref=x.delivery_ref AND p.tenant_id=d.tenant_id AND p.run_ref=d.run_ref;
  DELETE FROM memoriesql.query_cursors k USING memoriesql.query_deliveries d,memoriesql.query_run_purges p
   WHERE k.tenant_id=t AND memoriesql.query_checkpoint_context_ref_v1(k.access_context)=cp.checkpoint_id::text
    AND d.tenant_id=k.tenant_id AND d.delivery_ref=k.issued_by_delivery AND p.tenant_id=d.tenant_id AND p.run_ref=d.run_ref;
 END LOOP;
 -- Without a retained checkpoint the investigation has ended: no head digest
 -- remains in its control rows, which leave with its last stub's tombstone.
 IF NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoints WHERE tenant_id=t
  AND investigation_id=investigation AND automatic_retained) THEN
  UPDATE memoriesql.query_investigation_branches SET head_ref=NULL,head_digest=NULL
   WHERE tenant_id=t AND investigation_id=investigation AND head_ref IS NOT NULL;
 END IF;
END $$;
REVOKE ALL ON FUNCTION memoriesql.prune_automatic_checkpoints_v1(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.expire_automatic_checkpoints_v1(max_items integer,t uuid,w uuid,deadline timestamptz)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE item record; n integer:=0;
BEGIN
 FOR item IN SELECT DISTINCT i.tenant_id,i.workspace_id,i.investigation_id FROM memoriesql.query_investigations i
  JOIN memoriesql.query_checkpoints cp USING(tenant_id,investigation_id)
  WHERE cp.automatic_retained AND cp.expires_at<=clock_timestamp()
   AND (t IS NULL OR (i.tenant_id=t AND i.workspace_id=w)) ORDER BY i.tenant_id,i.workspace_id,i.investigation_id LOOP
  EXIT WHEN n>=max_items OR clock_timestamp()>=deadline;
  PERFORM memoriesql.checkpoint_ledger_lock_v1(item.tenant_id,item.workspace_id);
  PERFORM pg_advisory_xact_lock(hashtextextended(item.tenant_id::text||':investigation:'||item.investigation_id::text,0));
  PERFORM memoriesql.prune_automatic_checkpoints_v1(item.tenant_id,item.investigation_id);
  n:=n+1;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION memoriesql.expire_automatic_checkpoints_v1(integer,uuid,uuid,timestamptz) FROM PUBLIC;

CREATE FUNCTION memoriesql.checkpoint_refs_visible_v1(delivery uuid,roots jsonb,findings jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; item jsonb; key text; found_ref boolean;
BEGIN
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE delivery_ref=delivery;
 FOR item IN SELECT value FROM jsonb_array_elements(findings) AS ff(f),
  LATERAL jsonb_array_elements(COALESCE(f->'evidence_refs','[]')||COALESCE(
   (SELECT jsonb_agg(jsonb_build_object('kind','result_fact','fact',fact))
    FROM jsonb_array_elements(f->'facts') AS x(fact)),'[]')) AS refs(value) LOOP
  key:=memoriesql.lifecycle_canonical_json_v1(item);
  -- The current run must actually have received this ref. Presence in a stored
  -- result/manifest, a guessed ID or another run's receipt is not visibility.
  IF NOT EXISTS(SELECT 1 FROM memoriesql.query_visible_refs v WHERE v.tenant_id=d.tenant_id
   AND v.run_ref=d.run_ref AND v.ref_key=key) THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  SELECT EXISTS(SELECT 1 FROM memoriesql.query_disclosures receipt
   JOIN memoriesql.query_deliveries rd ON rd.tenant_id=receipt.tenant_id AND rd.delivery_ref=receipt.delivery_ref
   WHERE receipt.tenant_id=d.tenant_id AND rd.run_ref=d.run_ref AND receipt.delivered_refs @> jsonb_build_array(key)
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(roots) root
    WHERE receipt.result_id=(root#>>'{result,result_id}')::uuid AND receipt.content_digest=root#>>'{result,content_digest}')) INTO found_ref;
  IF NOT found_ref THEN
   -- A receipted inspector may expose witnesses beyond delivered result rows.
   -- Require the exact selected root pin AND every inspected protected frame to
   -- belong to its retained closure; unrelated direct targets cannot launder refs.
   SELECT EXISTS(SELECT 1 FROM memoriesql.query_disclosures receipt
    JOIN memoriesql.query_deliveries rd ON rd.tenant_id=receipt.tenant_id AND rd.delivery_ref=receipt.delivery_ref
    JOIN memoriesql.query_steps s ON s.tenant_id=rd.tenant_id AND s.run_ref=rd.run_ref AND s.step_key=rd.step_key
    JOIN memoriesql.result_preparation_artifacts helper ON helper.tenant_id=s.tenant_id AND helper.artifact_ref=s.helper_artifact_ref
    CROSS JOIN LATERAL (SELECT convert_from(helper.dependency_bytes,'UTF8')::jsonb AS deps) h
    WHERE receipt.tenant_id=d.tenant_id AND rd.run_ref=d.run_ref AND receipt.result_id IS NULL
     AND receipt.delivered_refs @> jsonb_build_array(key)
     AND EXISTS(SELECT 1 FROM jsonb_array_elements(roots) root
      JOIN memoriesql.result_preparation_artifacts ar ON ar.tenant_id=d.tenant_id
       AND ar.artifact_ref=(root#>>'{result,result_id}')::uuid
      WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(h.deps->'result_pins') pin
       WHERE pin->>'result_id'=root#>>'{result,result_id}' AND pin->>'content_digest'=root#>>'{result,content_digest}')
       AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(h.deps->'frames') frame,
        LATERAL jsonb_array_elements(frame->'manifest') record_pin
        WHERE NOT (convert_from(decode((convert_from(ar.dependency_bytes,'UTF8')::jsonb)->>'manifest_base64','base64'),'UTF8')::jsonb @> jsonb_build_array(record_pin))))) INTO found_ref;
  END IF;
  IF NOT found_ref THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION memoriesql.checkpoint_refs_visible_v1(uuid,jsonb,jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.prepare_automatic_checkpoint_v1(delivery uuid,request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 c memoriesql.authorization_contexts%ROWTYPE; inv memoriesql.query_investigations%ROWTYPE;
 branch memoriesql.query_investigation_branches%ROWTYPE; root jsonb; manifest jsonb;
 roots jsonb:='[]'; a memoriesql.result_preparation_artifacts%ROWTYPE; now_at timestamptz;
 investigation uuid; branch_id_value uuid; id uuid; lock_id uuid; op jsonb; hold_count bigint; capacity bigint;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 IF request->>'kind' IS DISTINCT FROM s.kind OR request->>'run_ref' IS DISTINCT FROM d.run_ref::text
  OR request->>'step_key' IS DISTINCT FROM d.step_key::text THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF s.kind='read_checkpoint' THEN
  RETURN memoriesql.read_automatic_checkpoint_v1(request->'access'); END IF;
 IF s.kind<>'checkpoint' THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF s.state='complete' THEN
  RETURN memoriesql.read_automatic_checkpoint_v1(jsonb_build_object('checkpoint',
   jsonb_build_object('checkpoint_id',s.checkpoint_ref,'manifest_digest',s.content_digest),'basis',jsonb_build_object('kind','automatic')));
 END IF;
 IF s.checkpoint_pending IS NOT NULL THEN RETURN jsonb_build_object('pending',s.checkpoint_pending); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||d.run_ref::text||':'||d.step_key::text,0));
 PERFORM memoriesql.checkpoint_ledger_lock_v1(c.tenant_id,c.workspace_id);
 investigation:=COALESCE((request->>'investigation_id')::uuid,uuidv7());
 branch_id_value:=COALESCE((request->>'branch_id')::uuid,uuidv7());
 -- Acquire all involved investigation locks in one total order, including the
 -- input checkpoint contexts. The workspace ledger serializes retention/GC too.
 FOR lock_id IN SELECT id_value FROM (
  SELECT investigation AS id_value UNION
  SELECT cp.investigation_id FROM jsonb_array_elements(request->'roots') rr
   JOIN memoriesql.query_checkpoints cp ON cp.tenant_id=c.tenant_id AND cp.checkpoint_id=(rr#>>'{access,checkpoint_id}')::uuid
  ) locks ORDER BY id_value LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':investigation:'||lock_id::text,0));
 END LOOP;
 FOR root IN SELECT value FROM jsonb_array_elements(request->'roots') LOOP
  PERFORM memoriesql.query_result_access_v1((root#>>'{result,result_id}')::uuid,
   root#>>'{result,content_digest}',root->'access');
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id AND artifact_ref=(root#>>'{result,result_id}')::uuid;
  roots:=roots||jsonb_build_array(jsonb_build_object('result',root->'result',
   'frame_ref',convert_from(a.content_bytes,'UTF8')::jsonb#>'{wire_frame,frame_ref}'));
 END LOOP;
 PERFORM memoriesql.checkpoint_refs_visible_v1(delivery,request->'roots',request->'findings');
 IF request->>'investigation_id' IS NULL THEN
  IF request->>'branch_id' IS NOT NULL OR request->'expected_head'<>'null'::jsonb THEN
   RAISE EXCEPTION 'invalid_checkpoint' USING ERRCODE='22023'; END IF;
 ELSE
  SELECT * INTO inv FROM memoriesql.query_investigations WHERE tenant_id=c.tenant_id AND investigation_id=investigation FOR UPDATE;
  SELECT * INTO branch FROM memoriesql.query_investigation_branches WHERE tenant_id=c.tenant_id AND branch_id=branch_id_value FOR UPDATE;
  IF inv.owner_identity IS DISTINCT FROM memoriesql.checkpoint_identity_v1(c)
   OR branch.investigation_id IS DISTINCT FROM investigation THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  -- With no retained, unexpired checkpoint the investigation has ended; an
  -- expired head is never continued or rescued by its old pin.
  IF NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp WHERE cp.tenant_id=c.tenant_id
   AND cp.investigation_id=investigation AND cp.automatic_retained AND clock_timestamp()<cp.expires_at) THEN
   RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
  IF jsonb_build_object('checkpoint_id',branch.head_ref,'manifest_digest',branch.head_digest) IS DISTINCT FROM request->'expected_head' THEN
   RAISE EXCEPTION 'checkpoint_branch_conflict' USING ERRCODE='22023'; END IF;
 END IF;
 -- Reserve all new manifest, hold, index and persistent control-state overhead
 -- before publication; existing root bytes stay under their one ledger owner.
 WITH RECURSIVE closure(artifact_ref) AS (
  SELECT (value#>>'{result,result_id}')::uuid FROM jsonb_array_elements(request->'roots')
  UNION SELECT h.parent_ref FROM closure x JOIN memoriesql.result_preparation_parent_holds h
   ON h.tenant_id=c.tenant_id AND h.child_ref=x.artifact_ref)
 SELECT count(*) INTO hold_count FROM closure;
 capacity:=73728+hold_count*1024;
 IF capacity>67108864 THEN RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000'; END IF;
 op:=memoriesql.reserve_result_preparation_v1(d.run_ref,d.step_key,s.request_fingerprint,capacity);
 IF request->>'investigation_id' IS NULL THEN
  INSERT INTO memoriesql.query_investigations VALUES(c.tenant_id,investigation,c.workspace_id,memoriesql.checkpoint_identity_v1(c),1) RETURNING * INTO inv;
  INSERT INTO memoriesql.query_investigation_branches VALUES(c.tenant_id,branch_id_value,investigation,NULL,NULL,1) RETURNING * INTO branch;
 END IF;
 now_at:=clock_timestamp(); id:=uuidv7();
 manifest:=jsonb_build_object('checkpoint_id',id,'investigation_id',investigation,'branch_id',branch_id_value,
  'sequence',branch.next_sequence::text,'investigation_sequence',inv.next_sequence::text,
  'predecessor',request->'expected_head','fork_origin',NULL,'working_state_source',NULL,
  'created_at',memoriesql.relation_packet_time(now_at),'automatic_expires_at',memoriesql.relation_packet_time(now_at+interval '720 hours'),
  'question',request->'question','progress',request->'progress','findings',request->'findings','roots',roots);
 UPDATE memoriesql.query_steps SET checkpoint_pending=manifest WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 RETURN jsonb_build_object('pending',manifest);
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_automatic_checkpoint_v1(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_automatic_checkpoint_v1(uuid,jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.publish_automatic_checkpoint_v1(delivery uuid,request jsonb,body bytea,digest text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; data jsonb; root jsonb; hash text;
 id uuid; investigation uuid; branch uuid; extra_charge bigint; held_count bigint;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 data:=convert_from(body,'UTF8')::jsonb;
 IF s.kind<>'checkpoint' OR s.state<>'executing' OR s.checkpoint_pending IS DISTINCT FROM data
  OR body IS NULL OR octet_length(body)>32768 OR encode(sha256(body),'hex') IS DISTINCT FROM digest THEN
  RAISE EXCEPTION 'invalid_checkpoint' USING ERRCODE='22023'; END IF;
 PERFORM memoriesql.checkpoint_ledger_lock_v1(d.tenant_id,d.workspace_id);
 id:=(data->>'checkpoint_id')::uuid; investigation:=(data->>'investigation_id')::uuid; branch:=(data->>'branch_id')::uuid;
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 IF o.state IS DISTINCT FROM 'reserved' THEN RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
  int8send(octet_length(body)::bigint)||body||int8send(0)||int8send(0)),'hex');
 PERFORM memoriesql.seal_result_preparation_v1(o.operation_ref,o.ownership_ref,id,body,''::bytea,''::bytea,hash,'[]'::jsonb);
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
 IF o.allocation_bytes+extra_charge>o.reservation_bytes OR octet_length(body)+8192+extra_charge>o.reservation_bytes THEN
  RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000'; END IF;
 UPDATE memoriesql.result_preparation_operations SET allocation_bytes=allocation_bytes+extra_charge,
  new_allocation_bytes=new_allocation_bytes+extra_charge WHERE tenant_id=d.tenant_id AND operation_ref=o.operation_ref;
 UPDATE memoriesql.query_investigations SET next_sequence=next_sequence+1 WHERE tenant_id=d.tenant_id AND investigation_id=investigation;
 UPDATE memoriesql.query_investigation_branches SET next_sequence=next_sequence+1,head_ref=id,head_digest=digest
  WHERE tenant_id=d.tenant_id AND branch_id=branch;
 UPDATE memoriesql.query_steps SET checkpoint_ref=id,content_digest=digest,checkpoint_pending=NULL
  WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 PERFORM memoriesql.prune_automatic_checkpoints_v1(d.tenant_id,investigation);
 RETURN memoriesql.read_automatic_checkpoint_v1(jsonb_build_object('checkpoint',
  jsonb_build_object('checkpoint_id',id,'manifest_digest',digest),'basis',jsonb_build_object('kind','automatic')));
END $$;
REVOKE ALL ON FUNCTION memoriesql.publish_automatic_checkpoint_v1(uuid,jsonb,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.publish_automatic_checkpoint_v1(uuid,jsonb,bytea,text) TO memoriesql_application;

CREATE FUNCTION memoriesql.record_checkpoint_disclosure_v1(delivery uuid,checkpoint uuid,digest text,
 response_hash text,transport_bytes bigint,observed_ms bigint) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; c memoriesql.authorization_contexts%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery); c:=memoriesql.result_preparation_authority_v1();
 -- Full closure read precedes sizing in the same authority-fenced transaction.
 -- The ledger/investigation locks remain held; recheck time without a second
 -- unmeasured closure traversal after the response charge was computed.
 IF NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp JOIN memoriesql.query_investigations i
  USING(tenant_id,investigation_id) WHERE cp.tenant_id=d.tenant_id AND cp.checkpoint_id=checkpoint
   AND cp.manifest_digest=digest AND cp.automatic_retained AND clock_timestamp()<cp.expires_at
   AND i.owner_identity=memoriesql.checkpoint_identity_v1(c)) THEN
  RAISE EXCEPTION 'checkpoint_unavailable' USING ERRCODE='42501'; END IF;
 IF response_hash IS NULL OR response_hash !~ '^[a-f0-9]{64}$' OR transport_bytes IS NULL
  OR transport_bytes<0 OR transport_bytes>d.reserved_transport OR observed_ms IS NULL OR observed_ms<0 THEN
  RAISE EXCEPTION 'invalid_checkpoint_disclosure' USING ERRCODE='22023'; END IF;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key FOR UPDATE;
 IF s.state='complete' AND (s.checkpoint_ref IS DISTINCT FROM checkpoint OR s.content_digest IS DISTINCT FROM digest) THEN
  RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
 INSERT INTO memoriesql.query_disclosures VALUES(d.tenant_id,delivery,NULL,digest,
  jsonb_build_object('checkpoint',jsonb_build_object('checkpoint_id',checkpoint,'manifest_digest',digest),'basis',jsonb_build_object('kind','automatic')),
  1,0,response_hash,'[]',clock_timestamp(),c.credential_id);
 UPDATE memoriesql.query_steps SET state='complete',checkpoint_ref=checkpoint,content_digest=digest,
  completed_at=COALESCE(completed_at,clock_timestamp()) WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='available',charged_db_ms=observed_ms,
  charged_transport=transport_bytes,response_sha256=response_hash,settled_at=clock_timestamp()
  WHERE tenant_id=d.tenant_id AND delivery_ref=delivery;
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_checkpoint_disclosure_v1(uuid,uuid,text,text,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_checkpoint_disclosure_v1(uuid,uuid,text,text,bigint,bigint) TO memoriesql_application;

CREATE FUNCTION memoriesql.clear_checkpoint_step_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.purged_at IS NOT NULL THEN NEW.checkpoint_pending:=NULL; NEW.checkpoint_ref:=NULL; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.clear_checkpoint_step_v1() FROM PUBLIC;
CREATE TRIGGER query_checkpoint_clear_pin BEFORE UPDATE OF purged_at ON memoriesql.query_steps
 FOR EACH ROW EXECUTE FUNCTION memoriesql.clear_checkpoint_step_v1();

-- A checkpoint/read receipt only served its run, and the run's tombstone keeps
-- no digest. Receipts and cursors bound to a released checkpoint context go
-- too; a retained context keeps them until its own prune or expiry.
CREATE FUNCTION memoriesql.query_checkpoint_context_retained_v1(t uuid,access jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT memoriesql.query_checkpoint_context_ref_v1(access) IS NULL OR EXISTS(
  SELECT 1 FROM memoriesql.query_checkpoints cp WHERE cp.tenant_id=t
   AND cp.checkpoint_id=memoriesql.query_checkpoint_context_ref_v1(access)::uuid AND cp.automatic_retained)
$$;
REVOKE ALL ON FUNCTION memoriesql.query_checkpoint_context_retained_v1(uuid,jsonb) FROM PUBLIC;
CREATE FUNCTION memoriesql.purge_checkpoint_step_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 IF NEW.purged_at IS NULL OR OLD.purged_at IS NOT NULL THEN RETURN NEW; END IF;
 DELETE FROM memoriesql.query_disclosures x USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND x.tenant_id=d.tenant_id AND x.delivery_ref=d.delivery_ref
   AND ((NEW.kind IN ('checkpoint','read_checkpoint') AND x.result_id IS NULL)
    OR NOT memoriesql.query_checkpoint_context_retained_v1(x.tenant_id,x.access_context));
 DELETE FROM memoriesql.query_cursors k USING memoriesql.query_deliveries d
  WHERE d.tenant_id=NEW.tenant_id AND d.run_ref=NEW.run_ref AND d.step_key=NEW.step_key
   AND k.tenant_id=d.tenant_id AND k.issued_by_delivery=d.delivery_ref
   AND NOT memoriesql.query_checkpoint_context_retained_v1(k.tenant_id,k.access_context);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.purge_checkpoint_step_v1() FROM PUBLIC;
CREATE TRIGGER query_checkpoint_purge AFTER UPDATE OF purged_at ON memoriesql.query_steps
 FOR EACH ROW EXECUTE FUNCTION memoriesql.purge_checkpoint_step_v1();

-- One tombstone due time for expiry, the sweep and status. NULL while the run
-- still retains content, a live checkpoint, a receipt or a cursor, or while a
-- checkpoint retained beside one of its pruned slots still needs that
-- noncontent lineage stub (bounded: that checkpoint ends within 30 days of the
-- prune). Otherwise 30 days after its last run purge, result purge or prune.
CREATE FUNCTION memoriesql.query_tombstone_due_at_v1(t uuid,run uuid) RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT CASE WHEN EXISTS(SELECT 1 FROM memoriesql.result_preparation_operations
   WHERE tenant_id=t AND run_ref=run AND state<>'discarded')
  OR EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp JOIN memoriesql.result_preparation_operations op
   ON op.tenant_id=cp.tenant_id AND op.operation_ref=cp.operation_ref
   WHERE cp.tenant_id=t AND op.run_ref=run AND cp.automatic_retained)
  OR EXISTS(SELECT 1 FROM memoriesql.query_checkpoints s JOIN memoriesql.result_preparation_operations op
   ON op.tenant_id=s.tenant_id AND op.operation_ref=s.operation_ref
   JOIN memoriesql.query_checkpoints live ON live.tenant_id=s.tenant_id
    AND live.investigation_id=s.investigation_id AND live.automatic_retained AND live.created_at<=s.pruned_at
   WHERE s.tenant_id=t AND op.run_ref=run AND NOT s.automatic_retained)
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

CREATE FUNCTION memoriesql.query_result_cleanup_eligible_at_v1(t uuid,result_ref uuid) RETURNS timestamptz
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
  -- A private preparation with no declared result lifetime retains its explicit
  -- hold until owned release. It cannot be treated as expired by guessing time.
  THEN NULL ELSE (SELECT max(ends_at) FROM promises) END
$$;
REVOKE ALL ON FUNCTION memoriesql.query_result_cleanup_eligible_at_v1(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.record_query_hold_end_v1() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE parent uuid; ends_at timestamptz; now_at timestamptz:=clock_timestamp();
BEGIN
 IF TG_TABLE_NAME='query_checkpoint_holds' THEN
  parent:=OLD.artifact_ref;
  SELECT LEAST(now_at,cp.expires_at) INTO ends_at FROM memoriesql.query_checkpoints cp
   WHERE cp.tenant_id=OLD.tenant_id AND cp.checkpoint_id=OLD.checkpoint_id;
 ELSE
  parent:=OLD.parent_ref;
  ends_at:=LEAST(now_at,COALESCE(memoriesql.query_result_cleanup_eligible_at_v1(OLD.tenant_id,OLD.child_ref),now_at));
 END IF;
 INSERT INTO memoriesql.query_result_cleanup_hold_ends VALUES(OLD.tenant_id,parent,COALESCE(ends_at,now_at))
 ON CONFLICT(tenant_id,artifact_ref) DO UPDATE SET last_hold_end=
  GREATEST(memoriesql.query_result_cleanup_hold_ends.last_hold_end,EXCLUDED.last_hold_end);
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_query_hold_end_v1() FROM PUBLIC;
CREATE TRIGGER query_checkpoint_hold_end AFTER DELETE ON memoriesql.query_checkpoint_holds
 FOR EACH ROW EXECUTE FUNCTION memoriesql.record_query_hold_end_v1();
CREATE TRIGGER query_parent_hold_end AFTER DELETE ON memoriesql.result_preparation_parent_holds
 FOR EACH ROW EXECUTE FUNCTION memoriesql.record_query_hold_end_v1();

CREATE OR REPLACE FUNCTION memoriesql.query_cleanup_overdue_v1(t uuid,w uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM memoriesql.query_runs r WHERE r.tenant_id=t AND r.workspace_id=w
   AND r.expires_at<clock_timestamp()-interval '24 hours'
   AND NOT EXISTS(SELECT 1 FROM memoriesql.query_run_purges p WHERE p.tenant_id=r.tenant_id AND p.run_ref=r.run_ref))
  OR EXISTS(SELECT 1 FROM memoriesql.query_result_creations c JOIN memoriesql.result_preparation_operations o
   ON o.tenant_id=c.tenant_id AND o.operation_ref=c.operation_ref WHERE c.tenant_id=t AND o.workspace_id=w
    AND memoriesql.query_result_cleanup_eligible_at_v1(c.tenant_id,c.result_id)<clock_timestamp()-interval '24 hours')
  OR EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp JOIN memoriesql.query_investigations i USING(tenant_id,investigation_id)
   WHERE cp.tenant_id=t AND i.workspace_id=w AND cp.automatic_retained AND cp.expires_at<clock_timestamp()-interval '24 hours')
$$;

CREATE OR REPLACE FUNCTION memoriesql.query_cleanup_status_v1() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; now_at timestamptz:=clock_timestamp();
 due_runs bigint; due_results bigint; due_tombstones bigint; late_runs bigint; late_results bigint;
 late_tombstones bigint; oldest timestamptz; failures bigint; failed_at timestamptz; failed_code text;
 purged timestamptz;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT count(*) FILTER (WHERE true),count(*) FILTER (WHERE r.expires_at<now_at-interval '24 hours'),
  min(r.expires_at) FILTER (WHERE r.expires_at<now_at-interval '24 hours')
  INTO due_runs,late_runs,oldest
  FROM memoriesql.query_runs r WHERE r.tenant_id=c.tenant_id AND r.workspace_id=c.workspace_id
   AND r.expires_at<=now_at AND NOT EXISTS(SELECT 1 FROM memoriesql.query_run_purges p
    WHERE p.tenant_id=r.tenant_id AND p.run_ref=r.run_ref);
 SELECT count(*),count(*) FILTER (WHERE memoriesql.query_result_cleanup_eligible_at_v1(x.tenant_id,x.result_id)<now_at-interval '24 hours'),
  LEAST(oldest,min(memoriesql.query_result_cleanup_eligible_at_v1(x.tenant_id,x.result_id)) FILTER (WHERE memoriesql.query_result_cleanup_eligible_at_v1(x.tenant_id,x.result_id)<now_at-interval '24 hours'))
  INTO due_results,late_results,oldest
  FROM memoriesql.query_result_creations x JOIN memoriesql.result_preparation_operations o
   ON o.tenant_id=x.tenant_id AND o.operation_ref=x.operation_ref
  WHERE x.tenant_id=c.tenant_id AND o.workspace_id=c.workspace_id AND memoriesql.query_result_cleanup_eligible_at_v1(x.tenant_id,x.result_id)<=now_at;
 SELECT count(*),count(*) FILTER (WHERE x.due_at<now_at-interval '24 hours')
  INTO due_tombstones,late_tombstones
  FROM (SELECT memoriesql.query_tombstone_due_at_v1(p.tenant_id,p.run_ref) AS due_at
   FROM memoriesql.query_run_purges p JOIN memoriesql.query_runs r
    ON r.tenant_id=p.tenant_id AND r.run_ref=p.run_ref
   WHERE p.tenant_id=c.tenant_id AND r.workspace_id=c.workspace_id
    AND p.purged_at<=now_at-interval '30 days') x
  WHERE x.due_at<=now_at;
 SELECT count(*),max(last_failed_at) INTO failures,failed_at FROM memoriesql.query_purge_failures
  WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id;
 SELECT error_code INTO failed_code FROM memoriesql.query_purge_failures
  WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id
  ORDER BY last_failed_at DESC,subject_ref LIMIT 1;
 SELECT max(x) INTO purged FROM (
  SELECT max(p.purged_at) x FROM memoriesql.query_run_purges p JOIN memoriesql.query_runs r
   ON r.tenant_id=p.tenant_id AND r.run_ref=p.run_ref
   WHERE p.tenant_id=c.tenant_id AND r.workspace_id=c.workspace_id
  UNION ALL
  SELECT max(purged_at) FROM memoriesql.query_result_purges
   WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id) latest;
 RETURN jsonb_build_object(
  'checked_at',to_char(now_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'cleanup_sla_hours',24,'tombstone_days',30,
  'due',jsonb_build_object('runs',due_runs,'results',due_results,'tombstones',due_tombstones),
  'overdue',jsonb_build_object('runs',late_runs,'results',late_results,'tombstones',late_tombstones),
  'oldest_overdue_due_at',to_char(oldest AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'failures',jsonb_build_object('count',failures,
   'last_failed_at',to_char(failed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'last_error_code',failed_code),
  'last_purged_at',to_char(purged AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'admission',CASE WHEN memoriesql.query_cleanup_overdue_v1(c.tenant_id,c.workspace_id)
   THEN 'blocked' ELSE 'open' END);
END $$;

CREATE FUNCTION memoriesql.query_result_context_live_v1(result_ref uuid,digest text,access jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 IF access='{"kind":"standalone"}'::jsonb THEN
  RETURN EXISTS(SELECT 1 FROM memoriesql.query_result_creations q WHERE q.tenant_id=c.tenant_id
   AND q.result_id=result_ref AND q.content_digest=digest AND clock_timestamp()<q.expires_at);
 END IF;
 RETURN access->>'kind'='checkpoint' AND access->'basis'='{"kind":"automatic"}'::jsonb
  AND access->'root'=jsonb_build_object('result_id',result_ref,'content_digest',digest)
  AND EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp JOIN memoriesql.query_investigations i USING(tenant_id,investigation_id)
   JOIN memoriesql.query_checkpoint_roots r USING(tenant_id,checkpoint_id)
   WHERE cp.tenant_id=c.tenant_id AND cp.checkpoint_id=(access->>'checkpoint_id')::uuid
    AND cp.manifest_digest=access->>'manifest_digest' AND cp.automatic_retained AND clock_timestamp()<cp.expires_at
    AND i.owner_identity=memoriesql.checkpoint_identity_v1(c) AND r.result_id=result_ref AND r.content_digest=digest);
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_result_context_live_v1(uuid,text,jsonb) FROM PUBLIC;

-- Forward replacements keep existing standalone behavior and historical SQL intact.
CREATE OR REPLACE FUNCTION memoriesql.check_query_result_bytes_v1(result_ref uuid,digest text,retained boolean)
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
  OR (NOT retained AND clock_timestamp()>=r.expires_at) OR ident.workspace_id<>c.workspace_id
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
 IF NOT retained AND clock_timestamp()>=r.expires_at THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire
 OR invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION memoriesql.check_query_result_bytes_v1(uuid,text,boolean) FROM PUBLIC;

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
 IF run IS NULL OR step IS NULL OR requested_kind NOT IN ('query','reuse_result','inspect','hydrate_source','checkpoint','read_checkpoint')
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
  OR s.kind NOT IN ('inspect','hydrate_source','checkpoint','read_checkpoint') OR s.purged_at IS NOT NULL
  OR EXISTS(SELECT 1 FROM memoriesql.query_run_closures WHERE tenant_id=c.tenant_id AND run_ref=r.run_ref) THEN
  RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF d.state<>'reserved' THEN RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000'; END IF;
 IF clock_timestamp()>=LEAST(d.deadline,r.expires_at) THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000'; END IF;
 RETURN d;
END $$;

CREATE FUNCTION memoriesql.read_query_result_page_v2(delivery uuid,result_ref uuid,digest text,
 first_row bigint,max_rows integer,access jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 r memoriesql.query_runs%ROWTYPE; q memoriesql.query_result_creations%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 body jsonb; deps jsonb; checked timestamptz; basis jsonb;
BEGIN
 IF first_row IS NULL OR first_row<1 OR max_rows IS NULL OR max_rows NOT BETWEEN 1 AND 50 THEN
  RAISE EXCEPTION 'invalid_query_page' USING ERRCODE='22023';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id AND delivery_ref=delivery;
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref;
 IF d.delivery_ref IS NULL OR r.run_ref IS NULL OR NOT memoriesql.query_run_owner_v1(r,c) THEN
  RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501';
 END IF;
 -- Recovery already settled this access; the exact redelivery discloses.
 IF d.state<>'reserved' THEN
  RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000';
 END IF;
 -- A passed deadline is a work limit, as at disclosure, never missing authority.
 IF clock_timestamp()>=d.deadline THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000';
 END IF;
 basis:=memoriesql.query_result_access_v1(result_ref,digest,access);
 checked:=clock_timestamp();
 SELECT * INTO q FROM memoriesql.query_result_creations WHERE tenant_id=c.tenant_id AND result_id=result_ref;
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id
  AND operation_ref=q.operation_ref;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id
  AND artifact_ref=result_ref;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb;
 deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 RETURN jsonb_build_object(
  'access_bases',jsonb_build_array(basis),'checked_at',to_char(checked AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'created_at',to_char(q.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'expires_at',to_char(q.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'originating_run_ref',o.run_ref,'retained_bytes',o.allocation_bytes,
  'remaining_before',memoriesql.query_run_remaining_v1(r,delivery),
  'total_rows',jsonb_array_length(body->'rows'),
  'metadata',body-'rows',
  'rows',COALESCE((SELECT jsonb_agg(value ORDER BY ordinality) FROM jsonb_array_elements(body->'rows')
    WITH ORDINALITY WHERE ordinality BETWEEN first_row AND first_row+max_rows-1),'[]'::jsonb),
  'evidence',jsonb_build_object('bindings',deps->'evidence_bindings',
    'logical_rows',jsonb_build_object(
     'memory_v1.statements',deps#>'{logical_rows,memory_v1.statements}',
     'memory_v1.relation_statements',deps#>'{logical_rows,memory_v1.relation_statements}',
     'memory_v1.observations',deps#>'{logical_rows,memory_v1.observations}',
     'memory_v1.assessed_relations',deps#>'{logical_rows,memory_v1.assessed_relations}')));
END $$;
REVOKE ALL ON FUNCTION memoriesql.read_query_result_page_v2(uuid,uuid,text,bigint,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_query_result_page_v2(uuid,uuid,text,bigint,integer,jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.record_query_disclosure_v2(delivery uuid,result_ref uuid,digest text,
 first_row bigint,row_count integer,page_size integer,delivered jsonb,response_hash text,
 transport_bytes bigint,observed_ms bigint,checked timestamptz,issued_cursor uuid,next_row bigint,access jsonb)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 r memoriesql.query_runs%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
BEGIN
 IF result_ref IS NULL OR digest IS NULL OR digest !~ '^[a-f0-9]{64}$'
  OR first_row IS NULL OR first_row<1 OR row_count IS NULL OR row_count NOT BETWEEN 0 AND 50
  OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 50 OR row_count>page_size
  OR jsonb_typeof(delivered)<>'array' OR response_hash IS NULL OR response_hash !~ '^[a-f0-9]{64}$'
  OR transport_bytes IS NULL OR transport_bytes<0 OR observed_ms IS NULL OR observed_ms<0
  OR checked IS NULL OR (issued_cursor IS NULL)<>(next_row IS NULL)
  OR (next_row IS NOT NULL AND next_row<>first_row+row_count) THEN
  RAISE EXCEPTION 'invalid_query_disclosure' USING ERRCODE='22023';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id
  AND delivery_ref=delivery FOR UPDATE;
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref FOR UPDATE;
 IF d.delivery_ref IS NULL OR r.run_ref IS NULL
  OR NOT memoriesql.query_run_owner_v1(r,c) OR transport_bytes>d.reserved_transport THEN
  RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501';
 END IF;
 -- Recovery already settled this access while it ran (its owner ended). The
 -- committed result stays; its disclosure belongs to the exact redelivery.
 IF d.state<>'reserved' THEN
  RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000';
 END IF;
 IF clock_timestamp()>=d.deadline THEN
  RAISE EXCEPTION 'query_delivery_work_exhausted' USING ERRCODE='54000';
 END IF;
 IF NOT COALESCE(memoriesql.query_result_context_live_v1(result_ref,digest,access),false) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 INSERT INTO memoriesql.query_disclosures VALUES(c.tenant_id,delivery,result_ref,digest,
  access,first_row,row_count,response_hash,delivered,checked,c.credential_id);
 INSERT INTO memoriesql.query_visible_refs
  SELECT c.tenant_id,d.run_ref,value,delivery FROM jsonb_array_elements_text(delivered)
  ON CONFLICT DO NOTHING;
 IF issued_cursor IS NOT NULL THEN
  INSERT INTO memoriesql.query_cursors VALUES(c.tenant_id,issued_cursor,result_ref,digest,
   access,next_row,c.workspace_id,c.principal_id,c.principal_kind,
   c.user_id,c.on_behalf_of_user_id,c.pairing_grant_id,delivery,clock_timestamp());
 END IF;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref
  AND step_key=d.step_key FOR UPDATE;
 IF s.state='executing' THEN
  UPDATE memoriesql.query_steps SET state='complete',result_id=result_ref,content_digest=digest,
   first_row=record_query_disclosure_v2.first_row,row_count=record_query_disclosure_v2.row_count,
   page_size=record_query_disclosure_v2.page_size,completed_at=clock_timestamp()
   WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 ELSIF s.state<>'complete' OR s.result_id<>result_ref OR s.content_digest<>digest
  OR s.first_row<>record_query_disclosure_v2.first_row
  OR s.row_count<>record_query_disclosure_v2.row_count THEN
  RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505';
 END IF;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='available',
  charged_db_ms=observed_ms,charged_transport=transport_bytes,response_sha256=response_hash,
  settled_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND delivery_ref=delivery;
 RETURN jsonb_build_object('state','settled','remaining',memoriesql.query_run_remaining_v1(r));
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_query_disclosure_v2(uuid,uuid,text,bigint,integer,integer,jsonb,text,bigint,bigint,timestamptz,uuid,bigint,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_query_disclosure_v2(uuid,uuid,text,bigint,integer,integer,jsonb,text,bigint,bigint,timestamptz,uuid,bigint,jsonb) TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.prepare_query_inspection_v1(delivery uuid,request jsonb)
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
  PERFORM memoriesql.query_result_access_v1((result_pin->>'result_id')::uuid,result_pin->>'content_digest',request#>'{result,access}');
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
  page:=memoriesql.read_query_result_page_v2(delivery,(result_pin->>'result_id')::uuid,result_pin->>'content_digest',1,1,request#>'{result,access}');
  result_metadata:=jsonb_build_object('result_id',result_pin->'result_id','content_digest',result_pin->'content_digest',
   'originating_run_ref',page->'originating_run_ref','created_at',page->'created_at','expires_at',page->'expires_at',
   'catalog_hash',page#>'{metadata,catalog_hash}','policy_hash',page#>'{metadata,policy_hash}',
   'result_schema',page#>'{metadata,result_schema}','frame',page#>'{metadata,wire_frame}','parents','[]'::jsonb,
   'query_ref',page#>'{metadata,query_ref}','lineage_ref',page#>'{metadata,lineage_ref}',
   'coverage',page#>'{metadata,coverage}','order_basis',page#>'{metadata,order_basis}','total_rows',page#>'{metadata,total_rows}');
  IF page#>>'{metadata,wire_frame,view}' IS DISTINCT FROM view_mode
   OR (page#>>'{metadata,wire_frame,known_at}')::timestamptz IS DISTINCT FROM known THEN
   RAISE EXCEPTION 'invalid_query_helper_frame' USING ERRCODE='22023'; END IF;
  result_pins:=jsonb_build_array(result_pin||jsonb_build_object('access',request#>'{result,access}'));
  bases:=page->'access_bases';
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

CREATE OR REPLACE FUNCTION memoriesql.read_query_helper_artifact_v1(delivery uuid,operation uuid,ownership uuid)
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
  BEGIN
   records_text:=convert_from(decode(item->>'records_base64','base64'),'UTF8');
   pinned_records:=records_text::jsonb;
  EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire THEN
   RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501';
  END;
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
  PERFORM memoriesql.query_result_access_v1((item->>'result_id')::uuid,item->>'content_digest',COALESCE(item->'access','{"kind":"standalone"}'::jsonb));
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

CREATE OR REPLACE FUNCTION memoriesql.record_query_helper_disclosure_v1(delivery uuid,artifact uuid,digest text,
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
  WHERE q.result_id IS NULL OR q.content_digest IS DISTINCT FROM pin->>'content_digest'
   OR NOT COALESCE(memoriesql.query_result_context_live_v1(q.result_id,q.content_digest,COALESCE(pin->'access','{"kind":"standalone"}'::jsonb)),false)) THEN
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

CREATE OR REPLACE FUNCTION memoriesql.resolve_query_helper_evidence_v1(delivery uuid,evidence uuid,part uuid,origin jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; x memoriesql.query_disclosures%ROWTYPE;
 v memoriesql.query_visible_refs%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; q memoriesql.query_result_creations%ROWTYPE;
 binding jsonb; pin jsonb; packet jsonb; deps jsonb; basis jsonb:='[]'; root_pin jsonb; effective_access jsonb;
BEGIN
 d:=memoriesql.query_helper_delivery_v1(delivery);
 IF origin IS NULL OR origin='null'::jsonb THEN
  SELECT * INTO v FROM memoriesql.query_visible_refs WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref
   AND ref_key::jsonb->>'kind'='source' AND ref_key::jsonb->>'ref'=evidence::text;
  IF v.first_delivery_ref IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  SELECT * INTO x FROM memoriesql.query_disclosures WHERE tenant_id=d.tenant_id AND delivery_ref=v.first_delivery_ref;
 ELSE
  root_pin:=origin->'result';
  PERFORM memoriesql.query_result_access_v1((root_pin->>'result_id')::uuid,root_pin->>'content_digest',origin->'access');
  SELECT * INTO x FROM memoriesql.query_disclosures z WHERE z.tenant_id=d.tenant_id
   AND z.result_id=(root_pin->>'result_id')::uuid AND z.content_digest=root_pin->>'content_digest'
   AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(z.delivered_refs) AS r(ref_key)
    WHERE r.ref_key::jsonb->>'kind'='source' AND r.ref_key::jsonb->>'ref'=evidence::text) LIMIT 1;
 END IF;
 IF x.delivery_ref IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
 IF x.result_id IS NOT NULL THEN
  effective_access:=CASE WHEN origin IS NULL OR origin='null'::jsonb THEN x.access_context ELSE origin->'access' END;
  basis:=jsonb_build_array(memoriesql.query_result_access_v1(x.result_id,x.content_digest,effective_access));
  SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=d.tenant_id AND artifact_ref=x.result_id;
  binding:=convert_from(a.dependency_bytes,'UTF8')::jsonb#>ARRAY['evidence_bindings',evidence::text];
  IF binding IS NULL THEN RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END IF;
  pin:=memoriesql.query_helper_evidence_pin_v1(d.tenant_id,evidence,(binding->>'statement_id')::uuid,(binding->>'source_unit_id')::uuid,part);
  root_pin:=jsonb_build_object('result_id',x.result_id,'content_digest',x.content_digest,'access',effective_access);
  deps:=jsonb_build_object('frames','[]'::jsonb,'raw_sources','[]'::jsonb,'parts','[]'::jsonb,'result_pins',jsonb_build_array(root_pin));
  SELECT * INTO q FROM memoriesql.query_result_creations WHERE tenant_id=d.tenant_id AND result_id=x.result_id;

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

CREATE OR REPLACE FUNCTION memoriesql.purge_query_result_v1(t uuid,result uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off AS $$
DECLARE c memoriesql.query_result_creations%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
 now_at timestamptz;
BEGIN
 SELECT * INTO c FROM memoriesql.query_result_creations WHERE tenant_id=t AND result_id=result;
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=t AND operation_ref=c.operation_ref;
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':query-access:'||o.workspace_id::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation-operation:'||o.run_ref::text||':'||o.step_key::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation:'||o.workspace_id::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(t,o.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 SELECT * INTO c FROM memoriesql.query_result_creations WHERE tenant_id=t AND result_id=result FOR UPDATE;
 now_at:=clock_timestamp();
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 IF memoriesql.query_result_cleanup_eligible_at_v1(t,result) IS NULL
  OR memoriesql.query_result_cleanup_eligible_at_v1(t,result)>now_at THEN RETURN 'held'; END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_parent_holds WHERE tenant_id=t AND parent_ref=result)
  OR EXISTS(SELECT 1 FROM memoriesql.query_checkpoint_holds WHERE tenant_id=t AND artifact_ref=result)
  THEN RETURN 'held'; END IF;
 DELETE FROM memoriesql.query_cursors WHERE tenant_id=t AND result_id=result;
 DELETE FROM memoriesql.query_disclosures WHERE tenant_id=t AND result_id=result;
 DELETE FROM memoriesql.result_preparation_parent_holds WHERE tenant_id=t AND child_ref=result;
 DELETE FROM memoriesql.result_preparation_artifacts WHERE tenant_id=t AND artifact_ref=result;
 DELETE FROM memoriesql_query.population_rows p USING memoriesql_query.invocations i
  WHERE i.tenant_id=t AND i.operation_ref=o.operation_ref AND p.invocation_ref=i.invocation_ref;
 DELETE FROM memoriesql_query.invocations WHERE tenant_id=t AND operation_ref=o.operation_ref;
 UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
  encoded_bytes=0,allocation_bytes=8192 WHERE tenant_id=t AND operation_ref=o.operation_ref;
 INSERT INTO memoriesql.query_result_purges
  VALUES(t,result,o.workspace_id,o.run_ref,o.operation_ref,c.expires_at,now_at);
 RETURN 'purged';
END $$;

CREATE OR REPLACE FUNCTION memoriesql.expire_query_tombstone_v1(t uuid,run uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off AS $$
DECLARE r memoriesql.query_runs%ROWTYPE; marker memoriesql.query_run_purges%ROWTYPE;
 due timestamptz; item uuid; investigations uuid[]:='{}';
BEGIN
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=t AND run_ref=run;
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':query-access:'||r.workspace_id::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation:'||r.workspace_id::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(t,r.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 SELECT * INTO marker FROM memoriesql.query_run_purges WHERE tenant_id=t AND run_ref=run;
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 -- Retention locks of every investigation whose stub this tombstone may end.
 FOR item IN SELECT DISTINCT cp.investigation_id FROM memoriesql.query_checkpoints cp
  JOIN memoriesql.result_preparation_operations op ON op.tenant_id=cp.tenant_id AND op.operation_ref=cp.operation_ref
  WHERE cp.tenant_id=t AND op.run_ref=run ORDER BY cp.investigation_id LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':investigation:'||item::text,0));
  investigations:=investigations||item;
 END LOOP;
 due:=memoriesql.query_tombstone_due_at_v1(t,run);
 IF due IS NULL THEN RETURN 'pending'; END IF;
 IF clock_timestamp()<due THEN RETURN 'skipped'; END IF;
 -- Noncontent stubs end with this tombstone, then the control rows of any
 -- investigation left without a checkpoint; their charges leave the ledger.
 DELETE FROM memoriesql.query_checkpoints cp USING memoriesql.result_preparation_operations op
  WHERE op.tenant_id=t AND op.run_ref=run AND cp.tenant_id=t AND cp.operation_ref=op.operation_ref;
 DELETE FROM memoriesql.query_investigation_branches b WHERE b.tenant_id=t
  AND b.investigation_id=ANY(investigations) AND NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp
   WHERE cp.tenant_id=t AND cp.investigation_id=b.investigation_id);
 DELETE FROM memoriesql.query_investigations i WHERE i.tenant_id=t
  AND i.investigation_id=ANY(investigations) AND NOT EXISTS(SELECT 1 FROM memoriesql.query_checkpoints cp
   WHERE cp.tenant_id=t AND cp.investigation_id=i.investigation_id);
 DELETE FROM memoriesql.query_result_purges WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.query_visible_refs WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.query_deliveries WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.query_steps WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.query_run_closures WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql_query.population_rows p USING memoriesql_query.invocations i
  WHERE i.tenant_id=t AND i.run_ref=run AND p.invocation_ref=i.invocation_ref;
 DELETE FROM memoriesql_query.invocations WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.result_preparation_operations WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.query_purge_failures WHERE tenant_id=t AND subject_ref=run;
 DELETE FROM memoriesql.query_run_purges WHERE tenant_id=t AND run_ref=run;
 DELETE FROM memoriesql.query_runs WHERE tenant_id=t AND run_ref=run;
 RETURN 'purged';
END $$;

CREATE OR REPLACE FUNCTION memoriesql.purge_expired_query_state_v1(max_items integer,
 only_tenant uuid DEFAULT NULL,only_workspace uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE item record; outcome text; code text; started timestamptz:=clock_timestamp();
 runs integer:=0; results integer:=0; tombstones integer:=0; pending integer:=0; failed integer:=0;
BEGIN
 IF max_items IS NULL OR max_items NOT BETWEEN 1 AND 256
  OR (only_tenant IS NULL)<>(only_workspace IS NULL) THEN
  RAISE EXCEPTION 'invalid_query_cleanup' USING ERRCODE='22023';
 END IF;
 PERFORM memoriesql.expire_automatic_checkpoints_v1(max_items,only_tenant,only_workspace,started+interval '5 seconds');
 FOR item IN
  SELECT due.* FROM (
   SELECT 1 AS phase,'run'::text AS kind,r.tenant_id,r.workspace_id,r.run_ref AS subject,
    r.expires_at AS due_at
    FROM memoriesql.query_runs r WHERE r.expires_at<=started
     AND NOT EXISTS(SELECT 1 FROM memoriesql.query_run_purges p
      WHERE p.tenant_id=r.tenant_id AND p.run_ref=r.run_ref)
   UNION ALL
   SELECT 2,'result',c.tenant_id,o.workspace_id,c.result_id,
    memoriesql.query_result_cleanup_eligible_at_v1(c.tenant_id,c.result_id)
    FROM memoriesql.query_result_creations c JOIN memoriesql.result_preparation_operations o
     ON o.tenant_id=c.tenant_id AND o.operation_ref=c.operation_ref
    WHERE memoriesql.query_result_cleanup_eligible_at_v1(c.tenant_id,c.result_id)<=started
   UNION ALL
   SELECT 3,'tombstone',p.tenant_id,r.workspace_id,p.run_ref,
    memoriesql.query_tombstone_due_at_v1(p.tenant_id,p.run_ref)
    FROM memoriesql.query_run_purges p JOIN memoriesql.query_runs r
     ON r.tenant_id=p.tenant_id AND r.run_ref=p.run_ref
    WHERE p.purged_at<=started-interval '30 days'
     AND memoriesql.query_tombstone_due_at_v1(p.tenant_id,p.run_ref)<=started
  ) due
  LEFT JOIN memoriesql.query_purge_failures f ON f.tenant_id=due.tenant_id
   AND f.subject_kind=due.kind AND f.subject_ref=due.subject
  WHERE only_tenant IS NULL OR (due.tenant_id=only_tenant AND due.workspace_id=only_workspace)
  ORDER BY f.subject_ref IS NOT NULL,due.phase,due.due_at,due.subject
 LOOP
  EXIT WHEN runs+results+tombstones>=max_items
   OR clock_timestamp()-started>interval '5 seconds';
  BEGIN
   outcome:=CASE item.kind
    WHEN 'run' THEN memoriesql.purge_query_run_v1(item.tenant_id,item.subject)
    WHEN 'result' THEN memoriesql.purge_query_result_v1(item.tenant_id,item.subject)
    ELSE memoriesql.expire_query_tombstone_v1(item.tenant_id,item.subject) END;
   IF outcome='purged' THEN
    CASE item.kind WHEN 'run' THEN runs:=runs+1; WHEN 'result' THEN results:=results+1;
     ELSE tombstones:=tombstones+1; END CASE;
    DELETE FROM memoriesql.query_purge_failures WHERE tenant_id=item.tenant_id
     AND subject_kind=item.kind AND subject_ref=item.subject;
   ELSIF outcome IN ('pending','held') THEN
    pending:=pending+1;
   END IF;
  EXCEPTION WHEN OTHERS THEN
   GET STACKED DIAGNOSTICS code=RETURNED_SQLSTATE;
   failed:=failed+1;
   INSERT INTO memoriesql.query_purge_failures VALUES(item.tenant_id,item.workspace_id,item.kind,
    item.subject,1,clock_timestamp(),clock_timestamp(),code)
   ON CONFLICT(tenant_id,subject_kind,subject_ref) DO UPDATE
    SET attempts=memoriesql.query_purge_failures.attempts+1,
     last_failed_at=EXCLUDED.last_failed_at,error_code=EXCLUDED.error_code;
  END;
 END LOOP;
 RETURN jsonb_build_object('purged',jsonb_build_object('runs',runs,'results',results,
  'tombstones',tombstones),'pending',pending,'failed',failed);
END $$;

