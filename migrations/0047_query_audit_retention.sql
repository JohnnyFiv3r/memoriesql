-- Bounded, charged retention of query-originated authorization audit rows
-- (owner decision 2026-10-22, option A, 30 days). The trusted executor names
-- its admitted delivery; the kernel writer attributes a row only while that
-- delivery is live, reserved and owned by the same principal. Attributed rows
-- are charged to the workspace allocation and deleted by the governed expiry
-- 30 days after they were recorded. Every other audit row is unchanged and
-- stays append-only. Within one delivery and statement an identical decision
-- is recorded once (C1). Row content and granularity are otherwise unchanged.

-- Heap, index and attribution bytes of one retained audit row, with margin.
-- Measured about 1.5 KB per row once expired rows' index pages are awaiting
-- reuse, so the charge also covers that steady state.
CREATE FUNCTION memoriesql.query_audit_row_bytes_v1() RETURNS bigint
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT 2048::bigint $$;
REVOKE ALL ON FUNCTION memoriesql.query_audit_row_bytes_v1() FROM PUBLIC;

-- Write-time attribution of one audit row to its query delivery. Insert-only:
-- a delivery's concurrent transactions never wait on a shared row.
-- decision_key digests the delivery, the statement time and the row without
-- its own identifiers (event id, the caller's per-call request id), so it
-- names one decision of one statement of one delivery.
CREATE TABLE memoriesql.query_audit_events (
 tenant_id uuid NOT NULL, authorization_audit_event_id uuid NOT NULL,
 workspace_id uuid NOT NULL, delivery_ref uuid NOT NULL,
 decision_key bytea NOT NULL CHECK(octet_length(decision_key)=32),
 expires_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,authorization_audit_event_id),
 UNIQUE(tenant_id,decision_key),
 -- The audit row is inserted right after its attribution, in the same call.
 FOREIGN KEY(tenant_id,authorization_audit_event_id)
  REFERENCES memoriesql.authorization_audit_events(tenant_id,authorization_audit_event_id)
  ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX query_audit_events_workspace
 ON memoriesql.query_audit_events(tenant_id,workspace_id,expires_at);
CREATE INDEX query_audit_events_due ON memoriesql.query_audit_events(expires_at);
ALTER TABLE memoriesql.query_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_audit_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_audit_events FROM PUBLIC,memoriesql_application,memoriesql_worker;

-- The delivery this transaction's audit rows belong to, or NULL. The executor
-- only names a delivery; it counts while reserved, unexpired and owned by the
-- exact current principal tuple, as helper work requires (M0046).
CREATE FUNCTION memoriesql.query_audit_delivery_v1(c memoriesql.authorization_contexts)
RETURNS memoriesql.query_deliveries
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE claim text:=current_setting('memoriesql.query_audit_delivery',true);
 d memoriesql.query_deliveries%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
BEGIN
 IF claim IS NULL OR claim !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
  RETURN NULL; END IF;
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id AND delivery_ref=claim::uuid;
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref;
 IF d.delivery_ref IS NULL OR r.run_ref IS NULL OR d.workspace_id<>c.workspace_id
  OR d.state<>'reserved' OR NOT memoriesql.query_run_owner_v1(r,c)
  OR clock_timestamp()>=LEAST(d.deadline,r.expires_at) THEN
  RETURN NULL; END IF;
 RETURN d;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_audit_delivery_v1(memoriesql.authorization_contexts) FROM PUBLIC;

-- Attribute one decision written during query work. NULL: no live delivery, so
-- the row is kept as before. false: this delivery already recorded the same
-- decision in this statement; no second row. true: the caller writes the row.
CREATE FUNCTION memoriesql.record_query_audit_v1(c memoriesql.authorization_contexts,
 event uuid,scope uuid,resource_kind text,resource uuid,capability text,permission text,
 policy uuid,decision text,reason text,recorded timestamptz) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE;
BEGIN
 d:=memoriesql.query_audit_delivery_v1(c);
 IF d.delivery_ref IS NULL THEN RETURN NULL; END IF;
 INSERT INTO memoriesql.query_audit_events VALUES(c.tenant_id,event,c.workspace_id,d.delivery_ref,
  sha256(convert_to(jsonb_build_array(d.delivery_ref,c.principal_id,c.principal_kind,
   c.on_behalf_of_user_id,c.pairing_grant_id,c.workspace_id,scope,resource_kind,resource,
   capability,permission,policy,decision,reason,recorded)::text,'UTF8')),
  recorded+interval '30 days')
 ON CONFLICT(tenant_id,decision_key) DO NOTHING;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_query_audit_v1(memoriesql.authorization_contexts,uuid,uuid,
 text,uuid,text,text,uuid,text,text,timestamptz) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.application_authorize_resource(
    requested_resource_kind text,
    requested_resource_id uuid,
    requested_capability text,
    requested_permission text,
    request_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    resource_record memoriesql.protected_resources%ROWTYPE;
    allowed boolean := false;
    policy_revision_id uuid;
    event_id uuid := pg_catalog.uuidv7();
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    SELECT resource.* INTO resource_record
    FROM memoriesql.protected_resources AS resource
    WHERE resource.tenant_id = context_record.tenant_id
      AND resource.workspace_id = context_record.workspace_id
      AND resource.resource_kind = requested_resource_kind
      AND resource.resource_id = requested_resource_id
      AND resource.status = 'active'
      AND (
          resource.resource_kind <> 'file'
          OR EXISTS (
              SELECT 1
              FROM memoriesql.file_objects AS file_object
              WHERE file_object.tenant_id = resource.tenant_id
                AND file_object.workspace_id = resource.workspace_id
                AND file_object.access_scope_id = resource.access_scope_id
                AND file_object.file_id = resource.resource_id
                AND file_object.status = 'active'
          )
      );
    IF FOUND THEN
        allowed := memoriesql.current_context_scope_authorized(
            resource_record.access_scope_id,
            requested_capability,
            requested_permission
        );
        SELECT scope.current_policy_revision_id INTO policy_revision_id
        FROM memoriesql.access_scopes AS scope
        WHERE scope.tenant_id = resource_record.tenant_id
          AND scope.access_scope_id = resource_record.access_scope_id;
    END IF;
    -- Query work is attributed, charged and expired after 30 days (0047). A
    -- decision this delivery already recorded in this statement is not
    -- written twice. Every other decision is recorded exactly as before.
    IF memoriesql.record_query_audit_v1(
        context_record,
        event_id,
        CASE WHEN allowed THEN resource_record.access_scope_id ELSE NULL END,
        requested_resource_kind,
        requested_resource_id,
        requested_capability,
        requested_permission,
        CASE WHEN allowed THEN policy_revision_id ELSE NULL END,
        CASE WHEN allowed THEN 'allowed' ELSE 'denied' END,
        CASE WHEN allowed THEN 'current_policy_allowed' ELSE 'resource_unavailable' END,
        statement_timestamp()
    ) IS FALSE THEN
        RETURN allowed;
    END IF;
    INSERT INTO memoriesql.authorization_audit_events (
        tenant_id,
        authorization_audit_event_id,
        request_id,
        authenticated_principal_id,
        principal_kind,
        on_behalf_of_user_id,
        pairing_grant_id,
        workspace_id,
        access_scope_id,
        resource_kind,
        resource_id,
        capability_key,
        permission_key,
        policy_revision_id,
        decision,
        decision_reason,
        recorded_at
    ) VALUES (
        context_record.tenant_id,
        event_id,
        request_id,
        context_record.principal_id,
        context_record.principal_kind,
        context_record.on_behalf_of_user_id,
        context_record.pairing_grant_id,
        context_record.workspace_id,
        CASE WHEN allowed THEN resource_record.access_scope_id ELSE NULL END,
        requested_resource_kind,
        requested_resource_id,
        requested_capability,
        requested_permission,
        CASE WHEN allowed THEN policy_revision_id ELSE NULL END,
        CASE WHEN allowed THEN 'allowed' ELSE 'denied' END,
        CASE WHEN allowed THEN 'current_policy_allowed' ELSE 'resource_unavailable' END,
        statement_timestamp()
    );
    RETURN allowed;
END;
$$;

-- The one exception to append-only audit: the governed expiry deletes a row
-- attributed to query work once its 30-day window has passed. Every update,
-- and every other delete, is refused exactly as reject_immutable_change does.
CREATE FUNCTION memoriesql.authorization_audit_retention_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 IF TG_OP='DELETE' AND current_setting('memoriesql.query_audit_expiry',true)='on'
  AND EXISTS(SELECT 1 FROM memoriesql.query_audit_events e
   WHERE e.tenant_id=OLD.tenant_id AND e.authorization_audit_event_id=OLD.authorization_audit_event_id
    AND e.expires_at<=clock_timestamp()) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
  USING ERRCODE = '55000';
END $$;
REVOKE ALL ON FUNCTION memoriesql.authorization_audit_retention_guard_v1() FROM PUBLIC;
DROP TRIGGER authorization_audit_events_immutable ON memoriesql.authorization_audit_events;
CREATE TRIGGER authorization_audit_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.authorization_audit_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.authorization_audit_retention_guard_v1();

-- Governed expiry, oldest first: at most max_rows attributed audit rows past
-- their 30-day window go, and with them their charge. The workspaces' charge
-- locks serialize it with reservations, which count what is retained.
CREATE FUNCTION memoriesql.expire_query_audits_v1(max_rows integer,
 only_tenant uuid DEFAULT NULL,only_workspace uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE started timestamptz:=clock_timestamp(); w record; removed bigint;
BEGIN
 IF max_rows IS NULL OR max_rows NOT BETWEEN 1 AND 65536
  OR (only_tenant IS NULL)<>(only_workspace IS NULL) THEN
  RAISE EXCEPTION 'invalid_query_cleanup' USING ERRCODE='22023';
 END IF;
 -- No temporary tables in a definer: select the same oldest due rows twice.
 FOR w IN SELECT DISTINCT due.tenant_id,due.workspace_id FROM (SELECT e.tenant_id,e.workspace_id
   FROM memoriesql.query_audit_events e WHERE e.expires_at<=started
    AND (only_tenant IS NULL OR (e.tenant_id=only_tenant AND e.workspace_id=only_workspace))
   ORDER BY e.expires_at,e.tenant_id,e.authorization_audit_event_id LIMIT max_rows) due
  ORDER BY due.tenant_id,due.workspace_id LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(w.tenant_id::text||':result-preparation:'||w.workspace_id::text,0));
  INSERT INTO memoriesql.result_preparation_allocators VALUES(w.tenant_id,w.workspace_id,1)
  ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 END LOOP;
 PERFORM set_config('memoriesql.query_audit_expiry','on',true);
 -- Attribution rows go with their audit rows (ON DELETE CASCADE).
 WITH due AS (SELECT e.tenant_id,e.authorization_audit_event_id FROM memoriesql.query_audit_events e
   WHERE e.expires_at<=started
    AND (only_tenant IS NULL OR (e.tenant_id=only_tenant AND e.workspace_id=only_workspace))
   ORDER BY e.expires_at,e.tenant_id,e.authorization_audit_event_id LIMIT max_rows)
 DELETE FROM memoriesql.authorization_audit_events a USING due
  WHERE a.tenant_id=due.tenant_id AND a.authorization_audit_event_id=due.authorization_audit_event_id;
 GET DIAGNOSTICS removed=ROW_COUNT;
 PERFORM set_config('memoriesql.query_audit_expiry','',true);
 RETURN jsonb_build_object('audit_rows',removed,
  'released_bytes',removed*memoriesql.query_audit_row_bytes_v1());
END $$;
REVOKE ALL ON FUNCTION memoriesql.expire_query_audits_v1(integer,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.expire_query_audits_v1(integer,uuid,uuid) TO memoriesql_application;

-- The caller's own workspace only, as purge_workspace_query_state_v1: it
-- removes nothing the retention window still holds.
CREATE FUNCTION memoriesql.expire_workspace_query_audits_v1(max_rows integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF c.context_id IS NULL THEN
  RAISE EXCEPTION 'query_cleanup_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN memoriesql.expire_query_audits_v1(max_rows,c.tenant_id,c.workspace_id);
END $$;
REVOKE ALL ON FUNCTION memoriesql.expire_workspace_query_audits_v1(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.expire_workspace_query_audits_v1(integer) TO memoriesql_application;

-- Noncontent retention status of the caller's workspace for the host or operator.
CREATE FUNCTION memoriesql.query_audit_retention_v1() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; now_at timestamptz:=clock_timestamp(); s record;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT count(*) AS audit_rows,count(*) FILTER (WHERE expires_at<=now_at) AS due,
  count(*) FILTER (WHERE expires_at<now_at-interval '24 hours') AS overdue,
  min(expires_at) AS next_expiry INTO s
  FROM memoriesql.query_audit_events WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id;
 RETURN jsonb_build_object('retention_days',30,'row_charge_bytes',memoriesql.query_audit_row_bytes_v1(),
  'retained',jsonb_build_object('audit_rows',s.audit_rows,
   'charged_bytes',s.audit_rows*memoriesql.query_audit_row_bytes_v1()),
  'due_rows',s.due,'overdue_rows',s.overdue,
  'next_expiry_at',to_char(s.next_expiry AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_audit_retention_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_audit_retention_v1() TO memoriesql_application;

CREATE OR REPLACE FUNCTION memoriesql.reserve_result_preparation_v1(
 run_id uuid,step_id uuid,fingerprint text,capacity bigint
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; used bigint; run_used bigint;
BEGIN
 IF run_id IS NULL OR step_id IS NULL OR fingerprint IS NULL OR fingerprint !~ '^[a-f0-9]{64}$'
  OR capacity IS NULL OR capacity NOT BETWEEN 8192 AND 67108864 THEN
  RAISE EXCEPTION 'invalid_preparation' USING ERRCODE='22023';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation-operation:'||run_id::text||':'||step_id::text,0));
 -- One allocation lock covers reservations, sealing and discarding across runs.
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation:'||c.workspace_id::text,0));
 -- A changed allocator row makes a pre-wait RR snapshot serialize-fail, rather
 -- than using stale sums after acquiring the advisory lock. Recover/retry the
 -- same private command in a NEW authority-fenced snapshot; never execute SQL.
 INSERT INTO memoriesql.result_preparation_allocators VALUES(c.tenant_id,c.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND run_ref=run_id AND step_key=step_id;
 IF FOUND THEN
  IF o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
   OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) THEN
   RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501';
  END IF;
  -- Cleanup removes the protected binding digest. A discarded key is never a
  -- reservation replay or new execution grant, even for the original inputs.
  IF o.state='discarded' THEN
   RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501';
  END IF;
  IF o.request_fingerprint<>fingerprint OR o.reservation_bytes<>capacity THEN
   RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505';
  END IF;
  RETURN memoriesql.result_preparation_receipt_v1(o,true);
 END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id
  AND run_ref=run_id AND (workspace_id<>c.workspace_id OR principal_id<>c.principal_id
   OR owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id))) THEN
  RAISE EXCEPTION 'result_preparation_unavailable' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes ELSE allocation_bytes END),0),
  COALESCE(sum(CASE WHEN run_ref=run_id THEN CASE WHEN state='reserved' THEN reservation_bytes ELSE new_allocation_bytes END ELSE 0 END),0)
 INTO used,run_used FROM memoriesql.result_preparation_operations
 WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id;
 -- Retained query audit rows are charged to the same workspace allocation.
 used:=used+memoriesql.query_audit_row_bytes_v1()*(SELECT count(*) FROM memoriesql.query_audit_events
  WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id);
 IF used+capacity>536870912 OR run_used+capacity>134217728 THEN
  RAISE EXCEPTION 'preparation_storage_exhausted' USING ERRCODE='54000';
 END IF;
 -- Opaque journal ownership is allocated once and is never taken over on retry.
 INSERT INTO memoriesql.result_preparation_operations(
  tenant_id,workspace_id,principal_id,owner_user_id,run_ref,step_key,operation_ref,
  ownership_ref,request_fingerprint,state,reservation_bytes)
 VALUES(c.tenant_id,c.workspace_id,c.principal_id,COALESCE(c.on_behalf_of_user_id,c.user_id),
  run_id,step_id,uuidv7(),uuidv7(),fingerprint,'reserved',capacity) RETURNING * INTO o;
 RETURN memoriesql.result_preparation_receipt_v1(o,false);
END $$;
