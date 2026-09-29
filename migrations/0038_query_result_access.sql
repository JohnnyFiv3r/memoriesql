-- PR-05 public standalone query/result access: trusted runs, admitted accesses,
-- disclosure receipts, delivered-evidence visibility and owner-bound cursors.
-- Forward-only. M0031/M0034 remain the sole result byte, allocation and commit
-- store; M0037's revision-2 verdict remains the whole-closure authority. No
-- checkpoint, save, saved-input refinement, inspection or hydration is added.
--
-- The approved baseline is enforced here, pinned by the baseline policy hash
-- b19f4085..., as qualification targets rather than measured capacity: a run
-- lasts 30 minutes with 128 admitted accesses, 300 s of charged database time,
-- 16 MiB transport (16 KiB kept for terminal diagnostics); an operation reserves
-- at most 30 s and a 256 KiB response; a workspace admits two active runs, one
-- executing operation and a rolling 24-hour 1,800 s / 256 MiB allowance.

CREATE TABLE memoriesql.query_runs (
 tenant_id uuid NOT NULL, run_ref uuid NOT NULL, workspace_id uuid NOT NULL,
 principal_id uuid NOT NULL, principal_kind text NOT NULL, user_id uuid,
 on_behalf_of_user_id uuid, pairing_grant_id uuid, credential_id uuid NOT NULL,
 catalog_hash text NOT NULL CHECK(catalog_hash ~ '^[a-f0-9]{64}$'),
 policy_hash text NOT NULL CHECK(policy_hash ~ '^[a-f0-9]{64}$'),
 started_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 default_known_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_ref),
 FOREIGN KEY(tenant_id,workspace_id,principal_id)
  REFERENCES memoriesql.workspace_memberships(tenant_id,workspace_id,principal_id),
 CHECK(expires_at=started_at+interval '30 minutes' AND default_known_at=started_at)
);
CREATE INDEX query_runs_workspace_active ON memoriesql.query_runs(tenant_id,workspace_id,expires_at);
-- An owner may end its own run early (trusted host only). Closing refunds
-- nothing, keeps every charge in the rolling window and cannot be reopened.
CREATE TABLE memoriesql.query_run_closures (
 tenant_id uuid NOT NULL, run_ref uuid NOT NULL, closed_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_ref),
 FOREIGN KEY(tenant_id,run_ref) REFERENCES memoriesql.query_runs(tenant_id,run_ref)
);
CREATE TABLE memoriesql.query_steps (
 tenant_id uuid NOT NULL, run_ref uuid NOT NULL, step_key uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('query','reuse_result')),
 request_fingerprint text CHECK(request_fingerprint IS NULL OR request_fingerprint ~ '^[a-f0-9]{64}$'),
 state text NOT NULL CHECK(state IN ('executing','complete','failed')),
 outcome text CHECK(outcome IS NULL OR outcome IN ('unavailable','unsupported_query',
  'invalid_request','budget_exhausted','cancelled','execution_error')),
 error_code text, result_id uuid,
 content_digest text CHECK(content_digest IS NULL OR content_digest ~ '^[a-f0-9]{64}$'),
 first_row bigint CHECK(first_row IS NULL OR first_row>0),
 row_count integer CHECK(row_count IS NULL OR row_count BETWEEN 0 AND 50),
 page_size integer CHECK(page_size IS NULL OR page_size BETWEEN 1 AND 50),
 created_at timestamptz NOT NULL, completed_at timestamptz,
 -- Expiry cleanup removes the request digest and page binding; the opaque
 -- identifiers, terminal state and timestamps remain as the tombstone.
 purged_at timestamptz,
 PRIMARY KEY(tenant_id,run_ref,step_key),
 FOREIGN KEY(tenant_id,run_ref) REFERENCES memoriesql.query_runs(tenant_id,run_ref),
 CHECK((state='executing')=(completed_at IS NULL)),
 CHECK((purged_at IS NULL)=(request_fingerprint IS NOT NULL)),
 CHECK(purged_at IS NULL OR (state<>'executing' AND content_digest IS NULL
  AND first_row IS NULL AND row_count IS NULL AND page_size IS NULL)),
 CHECK((state='complete')=(result_id IS NOT NULL AND (purged_at IS NOT NULL
  OR (content_digest IS NOT NULL AND first_row IS NOT NULL AND row_count IS NOT NULL
   AND page_size IS NOT NULL)))),
 CHECK((state='failed')=(outcome IS NOT NULL))
);
-- Every admitted access (original, retry or redelivery) is one delivery. Its
-- reservation is charged until confirmed settlement; unknown timing keeps it.
CREATE TABLE memoriesql.query_deliveries (
 tenant_id uuid NOT NULL, delivery_ref uuid NOT NULL, run_ref uuid NOT NULL,
 step_key uuid NOT NULL, workspace_id uuid NOT NULL,
 delivery_number integer NOT NULL CHECK(delivery_number>0),
 state text NOT NULL CHECK(state IN ('reserved','settled','settlement_pending')),
 reserved_db_ms integer NOT NULL CHECK(reserved_db_ms BETWEEN 1 AND 30000),
 charged_db_ms bigint CHECK(charged_db_ms IS NULL OR charged_db_ms>=0),
 reserved_transport integer NOT NULL CHECK(reserved_transport BETWEEN 1 AND 262144),
 charged_transport bigint CHECK(charged_transport IS NULL OR charged_transport>=0),
 outcome text CHECK(outcome IS NULL OR outcome IN ('available','unavailable','unsupported_query',
  'invalid_request','budget_exhausted','cancelled','execution_error','abandoned')),
 response_sha256 text CHECK(response_sha256 IS NULL OR response_sha256 ~ '^[a-f0-9]{64}$'),
 admitted_at timestamptz NOT NULL, deadline timestamptz NOT NULL, settled_at timestamptz,
 PRIMARY KEY(tenant_id,delivery_ref), UNIQUE(tenant_id,run_ref,step_key,delivery_number),
 FOREIGN KEY(tenant_id,run_ref,step_key)
  REFERENCES memoriesql.query_steps(tenant_id,run_ref,step_key),
 CHECK((state='settled')=(settled_at IS NOT NULL AND charged_db_ms IS NOT NULL
  AND charged_transport IS NOT NULL AND outcome IS NOT NULL))
);
CREATE INDEX query_deliveries_workspace_window
 ON memoriesql.query_deliveries(tenant_id,workspace_id,admitted_at);
CREATE INDEX query_deliveries_workspace_open
 ON memoriesql.query_deliveries(tenant_id,workspace_id) WHERE state<>'settled';
-- A trusted disclosure receipt binds the exact response digest, context and
-- root pin, delivered page, consuming run and the authority that was checked.
CREATE TABLE memoriesql.query_disclosures (
 tenant_id uuid NOT NULL, delivery_ref uuid NOT NULL, result_id uuid NOT NULL,
 content_digest text NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 access_context jsonb NOT NULL CHECK(access_context='{"kind":"standalone"}'::jsonb),
 first_row bigint NOT NULL CHECK(first_row>0),
 row_count integer NOT NULL CHECK(row_count BETWEEN 0 AND 50),
 response_sha256 text NOT NULL CHECK(response_sha256 ~ '^[a-f0-9]{64}$'),
 delivered_refs jsonb NOT NULL CHECK(jsonb_typeof(delivered_refs)='array'),
 checked_at timestamptz NOT NULL, credential_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,delivery_ref),
 FOREIGN KEY(tenant_id,delivery_ref) REFERENCES memoriesql.query_deliveries(tenant_id,delivery_ref)
);
CREATE TABLE memoriesql.query_visible_refs (
 tenant_id uuid NOT NULL, run_ref uuid NOT NULL, ref_key text NOT NULL,
 first_delivery_ref uuid NOT NULL,
 PRIMARY KEY(tenant_id,run_ref,ref_key),
 FOREIGN KEY(tenant_id,run_ref) REFERENCES memoriesql.query_runs(tenant_id,run_ref),
 FOREIGN KEY(tenant_id,first_delivery_ref)
  REFERENCES memoriesql.query_deliveries(tenant_id,delivery_ref)
);
-- Cursors are immutable server-side bindings; an unknown or foreign cursor is
-- invalid, never a guessable position, and never switches disclosure context.
CREATE TABLE memoriesql.query_cursors (
 tenant_id uuid NOT NULL, cursor_ref uuid NOT NULL, result_id uuid NOT NULL,
 content_digest text NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 access_context jsonb NOT NULL CHECK(access_context='{"kind":"standalone"}'::jsonb),
 next_row bigint NOT NULL CHECK(next_row>1),
 workspace_id uuid NOT NULL, principal_id uuid NOT NULL, principal_kind text NOT NULL,
 user_id uuid, on_behalf_of_user_id uuid, pairing_grant_id uuid,
 issued_by_delivery uuid NOT NULL, created_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,cursor_ref),
 FOREIGN KEY(tenant_id,issued_by_delivery)
  REFERENCES memoriesql.query_deliveries(tenant_id,delivery_ref)
);
ALTER TABLE memoriesql.query_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_run_closures ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_run_closures FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_steps FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_disclosures ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_disclosures FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_visible_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_visible_refs FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_cursors FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_run_closures FROM PUBLIC,memoriesql_application;
REVOKE ALL ON memoriesql.query_runs,memoriesql.query_steps,memoriesql.query_deliveries,
 memoriesql.query_disclosures,memoriesql.query_visible_refs,memoriesql.query_cursors
 FROM PUBLIC,memoriesql_application;
CREATE TRIGGER query_runs_no_update BEFORE UPDATE ON memoriesql.query_runs
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER query_run_closures_no_update BEFORE UPDATE ON memoriesql.query_run_closures
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER query_disclosures_no_update BEFORE UPDATE ON memoriesql.query_disclosures
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER query_visible_refs_no_update BEFORE UPDATE ON memoriesql.query_visible_refs
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER query_cursors_no_update BEFORE UPDATE ON memoriesql.query_cursors
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.query_run_owner_v1(r memoriesql.query_runs,
 c memoriesql.authorization_contexts) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT r.tenant_id=c.tenant_id AND r.workspace_id=c.workspace_id
  AND r.principal_id=c.principal_id AND r.principal_kind=c.principal_kind
  AND r.user_id IS NOT DISTINCT FROM c.user_id
  AND r.on_behalf_of_user_id IS NOT DISTINCT FROM c.on_behalf_of_user_id
  AND r.pairing_grant_id IS NOT DISTINCT FROM c.pairing_grant_id
$$;
REVOKE ALL ON FUNCTION memoriesql.query_run_owner_v1(memoriesql.query_runs,memoriesql.authorization_contexts) FROM PUBLIC;

-- Remaining allowance: settled charges, open reservations and uncertain
-- settlements all count. `excluded` omits one owned delivery so the executor can
-- state the allowance after that delivery's own exact charge. Cumulative new
-- allocation (including discarded preparations) is M0031's ledger.
CREATE FUNCTION memoriesql.query_run_remaining_v1(r memoriesql.query_runs,excluded uuid DEFAULT NULL)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT jsonb_build_object(
  'accesses',GREATEST(0,128-count(d.delivery_ref)),
  'db_ms',GREATEST(0,300000-COALESCE(sum(COALESCE(d.charged_db_ms,d.reserved_db_ms)),0)),
  'transport_bytes',GREATEST(0,16777216-COALESCE(sum(COALESCE(d.charged_transport,d.reserved_transport)),0)),
  'allocation_bytes',GREATEST(0,134217728-COALESCE((SELECT sum(CASE WHEN o.state='reserved'
     THEN o.reservation_bytes ELSE o.new_allocation_bytes END)
    FROM memoriesql.result_preparation_operations o
    WHERE o.tenant_id=r.tenant_id AND o.run_ref=r.run_ref),0)),
  'run_expires_at',to_char(r.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 FROM memoriesql.query_deliveries d WHERE d.tenant_id=r.tenant_id AND d.run_ref=r.run_ref
  AND d.delivery_ref IS DISTINCT FROM excluded
$$;
REVOKE ALL ON FUNCTION memoriesql.query_run_remaining_v1(memoriesql.query_runs,uuid) FROM PUBLIC;

-- Owned, noncontent counters of the delivery's own run, excluding that delivery.
-- Blind to later authority loss so a terminal reply can still state its budget.
CREATE FUNCTION memoriesql.query_delivery_remaining_v1(delivery uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
BEGIN
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE delivery_ref=delivery;
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref;
 IF d.delivery_ref IS NULL OR r.run_ref IS NULL THEN
  RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN memoriesql.query_run_remaining_v1(r,delivery);
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_delivery_remaining_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_delivery_remaining_v1(uuid) TO memoriesql_application;

CREATE FUNCTION memoriesql.query_run_state_v1(r memoriesql.query_runs) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT jsonb_build_object('run_ref',r.run_ref,'catalog_hash',r.catalog_hash,
  'policy_hash',r.policy_hash,
  'started_at',to_char(r.started_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'expires_at',to_char(r.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'default_known_at',to_char(r.default_known_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'remaining',memoriesql.query_run_remaining_v1(r))
$$;
REVOKE ALL ON FUNCTION memoriesql.query_run_state_v1(memoriesql.query_runs) FROM PUBLIC;

-- The trusted host starts a run after authentication. Callers cannot set the
-- tenant, principal, workspace, allowances or policy; only the pinned baseline.
CREATE FUNCTION memoriesql.start_query_run_v1(catalog text,policy text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
 now_at timestamptz:=clock_timestamp();
BEGIN
 IF catalog IS NULL OR catalog !~ '^[a-f0-9]{64}$'
  OR policy IS DISTINCT FROM 'b19f408584b5d6aac158c0d122be3c132cda9b6d14f6e6809ad05510b15b18d1' THEN
  RAISE EXCEPTION 'invalid_query_run' USING ERRCODE='22023';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':query-access:'||c.workspace_id::text,0));
 -- The host runs a bounded cleanup pass for this workspace just before (in its
 -- own read-committed transaction). Content cleanup more than 24 hours past due
 -- (missed or failed) refuses new runs until it succeeds.
 IF memoriesql.query_cleanup_overdue_v1(c.tenant_id,c.workspace_id) THEN
  RETURN jsonb_build_object('refused','cleanup');
 END IF;
 IF (SELECT count(*) FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id
     AND workspace_id=c.workspace_id AND expires_at>now_at
     AND NOT EXISTS(SELECT 1 FROM memoriesql.query_run_closures k
      WHERE k.tenant_id=query_runs.tenant_id AND k.run_ref=query_runs.run_ref))>=2 THEN
  RETURN jsonb_build_object('refused','run_admission');
 END IF;
 INSERT INTO memoriesql.query_runs VALUES(c.tenant_id,uuidv7(),c.workspace_id,c.principal_id,
  c.principal_kind,c.user_id,c.on_behalf_of_user_id,c.pairing_grant_id,c.credential_id,catalog,
  policy,now_at,now_at+interval '30 minutes',now_at) RETURNING * INTO r;
 RETURN jsonb_build_object('run',memoriesql.query_run_state_v1(r));
END $$;
REVOKE ALL ON FUNCTION memoriesql.start_query_run_v1(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.start_query_run_v1(text,text) TO memoriesql_application;

-- Liveness of the owning executor session; a held session lock proves nothing
-- stopped, while an acquirable one proves only that the owner session ended.
CREATE FUNCTION memoriesql.query_delivery_owner_gone_v1(delivery uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; key bigint; acquired boolean;
BEGIN
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE delivery_ref=delivery;
 IF NOT FOUND THEN RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501'; END IF;
 key:=hashtextextended(d.tenant_id::text||':query-delivery:'||d.delivery_ref::text,0);
 acquired:=pg_try_advisory_lock(key);
 IF acquired THEN PERFORM pg_advisory_unlock(key); END IF;
 RETURN acquired;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_delivery_owner_gone_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_delivery_owner_gone_v1(uuid) TO memoriesql_application;

-- Owner-only early close from the trusted host (never an agent wire action).
-- Refused while any of the run's deliveries is unsettled: open or uncertain
-- work is never abandoned by closing. Idempotent; the run stays charged.
CREATE FUNCTION memoriesql.close_query_run_v1(run uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
 k memoriesql.query_run_closures%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':query-access:'||c.workspace_id::text,0));
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=run FOR UPDATE;
 IF NOT FOUND OR NOT memoriesql.query_run_owner_v1(r,c) THEN
  RETURN jsonb_build_object('refused','unavailable');
 END IF;
 SELECT * INTO k FROM memoriesql.query_run_closures WHERE tenant_id=c.tenant_id AND run_ref=run;
 IF NOT FOUND THEN
  IF EXISTS(SELECT 1 FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id
   AND run_ref=run AND state<>'settled')
   OR EXISTS(SELECT 1 FROM memoriesql_query.invocations WHERE tenant_id=c.tenant_id
   AND run_ref=run AND state<>'settled') THEN
   RETURN jsonb_build_object('refused','settlement');
  END IF;
  INSERT INTO memoriesql.query_run_closures VALUES(c.tenant_id,run,clock_timestamp()) RETURNING * INTO k;
 END IF;
 RETURN jsonb_build_object('run_ref',run,
  'closed_at',to_char(k.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
END $$;
REVOKE ALL ON FUNCTION memoriesql.close_query_run_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.close_query_run_v1(uuid) TO memoriesql_application;

-- Admit exactly one access for (run,step): idempotent step identity, workspace
-- serialization, cumulative run and rolling workspace allowances. Refusals write
-- nothing and disclose no other principal's work. Uncertain earlier work blocks.
CREATE FUNCTION memoriesql.admit_query_delivery_v1(run uuid,step uuid,requested_kind text,
 fingerprint text,operation_ms integer,response_bytes integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
 s memoriesql.query_steps%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 blocking memoriesql.query_deliveries%ROWTYPE; now_at timestamptz:=clock_timestamp();
 remaining jsonb; used_db bigint; used_transport bigint; used_accesses bigint;
 window_db bigint; window_transport bigint; reserve_ms bigint; reserve_bytes bigint; replay boolean;
BEGIN
 IF run IS NULL OR step IS NULL OR requested_kind NOT IN ('query','reuse_result')
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

-- Owned settlement is blind to later authority loss, as for M0033 invocations:
-- a revocation must not trap charges or refund unknown work. `pending` keeps the
-- full reservation. The first delivery's terminal failure fixes the step outcome.
CREATE FUNCTION memoriesql.settle_query_delivery_v1(delivery uuid,terminal text,safe_error text,
 observed_ms bigint,transport_bytes bigint,response_hash text,pending boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; s memoriesql.query_steps%ROWTYPE;
BEGIN
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE delivery_ref=delivery FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501'; END IF;
 IF d.state='settled' THEN RETURN jsonb_build_object('state',d.state,'outcome',d.outcome); END IF;
 IF pending THEN
  UPDATE memoriesql.query_deliveries SET state='settlement_pending' WHERE delivery_ref=delivery;
  RETURN jsonb_build_object('state','settlement_pending');
 END IF;
 IF terminal IS NULL OR terminal NOT IN ('unavailable','unsupported_query','invalid_request',
   'budget_exhausted','cancelled','execution_error')
  OR observed_ms IS NULL OR observed_ms<0 OR transport_bytes IS NULL OR transport_bytes<0
  OR (response_hash IS NOT NULL AND response_hash !~ '^[a-f0-9]{64}$') THEN
  RAISE EXCEPTION 'invalid_query_settlement' USING ERRCODE='22023';
 END IF;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome=terminal,
  charged_db_ms=observed_ms,charged_transport=transport_bytes,response_sha256=response_hash,
  settled_at=clock_timestamp() WHERE delivery_ref=delivery;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref
  AND step_key=d.step_key FOR UPDATE;
 IF s.state='executing' THEN
  UPDATE memoriesql.query_steps SET state='failed',outcome=terminal,error_code=safe_error,
   completed_at=clock_timestamp()
   WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 END IF;
 RETURN jsonb_build_object('state','settled','outcome',terminal);
END $$;
REVOKE ALL ON FUNCTION memoriesql.settle_query_delivery_v1(uuid,text,text,bigint,bigint,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.settle_query_delivery_v1(uuid,text,text,bigint,bigint,text,boolean) TO memoriesql_application;


-- Recovery only: settle a reserved delivery whose owning executor session has
-- ended. An ended owner proves nothing about its reader: every invocation of the
-- step first passes M0033's owned cancel/settle, which confirms the identified
-- reader backend and transaction are gone. While any reader may still execute,
-- the delivery stays reserved and keeps blocking. Unknown timing and transport
-- keep the FULL reservation; recorded late reader usage beyond it is charged too.
-- The step is left for the caller to resolve from M0031/M0033/M0034 state.
CREATE FUNCTION memoriesql.abandon_query_delivery_v1(delivery uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE d memoriesql.query_deliveries%ROWTYPE; key bigint; i record; outcome jsonb; late bigint;
BEGIN
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE delivery_ref=delivery FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501'; END IF;
 IF d.state='settled' THEN RETURN true; END IF;
 key:=hashtextextended(d.tenant_id::text||':query-delivery:'||d.delivery_ref::text,0);
 IF NOT pg_try_advisory_lock(key) THEN RETURN false; END IF;
 PERFORM pg_advisory_unlock(key);
 FOR i IN SELECT invocation_ref,ownership_ref FROM memoriesql_query.invocations
          WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key
            AND state<>'settled' LOOP
  PERFORM memoriesql.cancel_relation_query_v1(i.invocation_ref,i.ownership_ref);
  outcome:=memoriesql.settle_relation_query_v1(i.invocation_ref,i.ownership_ref,'cancelled',NULL);
  IF outcome->>'state' IS DISTINCT FROM 'settled' THEN RETURN false; END IF;
 END LOOP;
 SELECT COALESCE(max(COALESCE(observed_ms,reserved_ms)),0) INTO late FROM memoriesql_query.invocations
  WHERE tenant_id=d.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='abandoned',
  charged_db_ms=GREATEST(reserved_db_ms,late),charged_transport=reserved_transport,
  settled_at=clock_timestamp() WHERE delivery_ref=delivery;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION memoriesql.abandon_query_delivery_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.abandon_query_delivery_v1(uuid) TO memoriesql_application;

-- Host recovery: abandon this workspace's reserved or uncertain deliveries whose
-- owning sessions have ended (each charged its full reservation). Steps stay
-- open for exact redelivery; nothing is rerun, refunded or disclosed here.
CREATE FUNCTION memoriesql.abandon_query_deliveries_v1() RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; d record; settled integer:=0;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 FOR d IN SELECT delivery_ref FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id
  AND workspace_id=c.workspace_id AND state<>'settled' ORDER BY admitted_at LOOP
  IF memoriesql.abandon_query_delivery_v1(d.delivery_ref) THEN settled:=settled+1; END IF;
 END LOOP;
 RETURN settled;
END $$;
REVOKE ALL ON FUNCTION memoriesql.abandon_query_deliveries_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.abandon_query_deliveries_v1() TO memoriesql_application;

-- Read one page of a standalone result ONLY after the revision-2 whole-closure
-- verdict in this same authority-fenced transaction. No write happens here; the
-- caller must record the disclosure in the same transaction before delivery.
CREATE FUNCTION memoriesql.read_query_result_page_v1(delivery uuid,result_ref uuid,digest text,
 first_row bigint,max_rows integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 r memoriesql.query_runs%ROWTYPE; q memoriesql.query_result_creations%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE; a memoriesql.result_preparation_artifacts%ROWTYPE;
 body jsonb; deps jsonb; checked timestamptz;
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
 IF clock_timestamp()>=d.deadline THEN
  RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM memoriesql.check_query_result_closure_v2(result_ref,digest);
 checked:=clock_timestamp();
 SELECT * INTO q FROM memoriesql.query_result_creations WHERE tenant_id=c.tenant_id AND result_id=result_ref;
 SELECT * INTO o FROM memoriesql.result_preparation_operations WHERE tenant_id=c.tenant_id
  AND operation_ref=q.operation_ref;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts WHERE tenant_id=c.tenant_id
  AND artifact_ref=result_ref;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb;
 deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 RETURN jsonb_build_object(
  'checked_at',to_char(checked AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
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
REVOKE ALL ON FUNCTION memoriesql.read_query_result_page_v1(uuid,uuid,text,bigint,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_query_result_page_v1(uuid,uuid,text,bigint,integer) TO memoriesql_application;

-- Which of these delivered evidence refs were already visible in this run.
CREATE FUNCTION memoriesql.query_visible_refs_v1(delivery uuid,refs text[]) RETURNS text[]
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; d memoriesql.query_deliveries%ROWTYPE;
 result text[];
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO d FROM memoriesql.query_deliveries WHERE tenant_id=c.tenant_id AND delivery_ref=delivery;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'query_delivery_unavailable' USING ERRCODE='42501';
 END IF;
 IF d.state<>'reserved' THEN
  RAISE EXCEPTION 'query_delivery_settled' USING ERRCODE='55000';
 END IF;
 SELECT COALESCE(array_agg(ref_key ORDER BY ref_key),'{}') INTO result
  FROM memoriesql.query_visible_refs WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref
   AND ref_key=ANY(refs);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_visible_refs_v1(uuid,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_visible_refs_v1(uuid,text[]) TO memoriesql_application;

-- Record one disclosure atomically with its receipt, run visibility, optional
-- cursor, completed step page and settled charges, in the SAME transaction as
-- the preceding whole-closure read. Nothing is acknowledged before commit.
CREATE FUNCTION memoriesql.record_query_disclosure_v1(delivery uuid,result_ref uuid,digest text,
 first_row bigint,row_count integer,page_size integer,delivered jsonb,response_hash text,
 transport_bytes bigint,observed_ms bigint,checked timestamptz,issued_cursor uuid,next_row bigint)
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
 INSERT INTO memoriesql.query_disclosures VALUES(c.tenant_id,delivery,result_ref,digest,
  '{"kind":"standalone"}'::jsonb,first_row,row_count,response_hash,delivered,checked,c.credential_id);
 INSERT INTO memoriesql.query_visible_refs
  SELECT c.tenant_id,d.run_ref,value,delivery FROM jsonb_array_elements_text(delivered)
  ON CONFLICT DO NOTHING;
 IF issued_cursor IS NOT NULL THEN
  INSERT INTO memoriesql.query_cursors VALUES(c.tenant_id,issued_cursor,result_ref,digest,
   '{"kind":"standalone"}'::jsonb,next_row,c.workspace_id,c.principal_id,c.principal_kind,
   c.user_id,c.on_behalf_of_user_id,c.pairing_grant_id,delivery,clock_timestamp());
 END IF;
 SELECT * INTO s FROM memoriesql.query_steps WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref
  AND step_key=d.step_key FOR UPDATE;
 IF s.state='executing' THEN
  UPDATE memoriesql.query_steps SET state='complete',result_id=result_ref,content_digest=digest,
   first_row=record_query_disclosure_v1.first_row,row_count=record_query_disclosure_v1.row_count,
   page_size=record_query_disclosure_v1.page_size,completed_at=clock_timestamp()
   WHERE tenant_id=c.tenant_id AND run_ref=d.run_ref AND step_key=d.step_key;
 ELSIF s.state<>'complete' OR s.result_id<>result_ref OR s.content_digest<>digest
  OR s.first_row<>record_query_disclosure_v1.first_row
  OR s.row_count<>record_query_disclosure_v1.row_count THEN
  RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505';
 END IF;
 UPDATE memoriesql.query_deliveries SET state='settled',outcome='available',
  charged_db_ms=observed_ms,charged_transport=transport_bytes,response_sha256=response_hash,
  settled_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND delivery_ref=delivery;
 RETURN jsonb_build_object('state','settled','remaining',memoriesql.query_run_remaining_v1(r));
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_query_disclosure_v1(uuid,uuid,text,bigint,integer,integer,jsonb,text,bigint,bigint,timestamptz,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_query_disclosure_v1(uuid,uuid,text,bigint,integer,integer,jsonb,text,bigint,bigint,timestamptz,uuid,bigint) TO memoriesql_application;

-- Resolve an owner-bound cursor. Unknown, foreign or other-context cursors share
-- one refusal; this reveals no result metadata beyond the caller's own binding.
CREATE FUNCTION memoriesql.resolve_query_cursor_v1(requested_cursor uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; k memoriesql.query_cursors%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO k FROM memoriesql.query_cursors WHERE tenant_id=c.tenant_id AND cursor_ref=requested_cursor;
 IF NOT FOUND OR k.workspace_id<>c.workspace_id OR k.principal_id<>c.principal_id
  OR k.principal_kind<>c.principal_kind OR k.user_id IS DISTINCT FROM c.user_id
  OR k.on_behalf_of_user_id IS DISTINCT FROM c.on_behalf_of_user_id
  OR k.pairing_grant_id IS DISTINCT FROM c.pairing_grant_id THEN
  RETURN NULL;
 END IF;
 RETURN jsonb_build_object('result_id',k.result_id,'content_digest',k.content_digest,
  'access_context',k.access_context,'next_row',k.next_row);
END $$;
REVOKE ALL ON FUNCTION memoriesql.resolve_query_cursor_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.resolve_query_cursor_v1(uuid) TO memoriesql_application;

-- The caller's own step preparation state, read on redelivery. A discarded
-- single-use preparation is a known execution failure of that step, never a
-- missing, denied or dependency-lost ID; lost authority still refuses here.
CREATE FUNCTION memoriesql.query_step_preparation_v1(run uuid,step uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; r memoriesql.query_runs%ROWTYPE;
 found_state text;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=c.tenant_id AND run_ref=run;
 IF NOT FOUND OR NOT memoriesql.query_run_owner_v1(r,c) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT state INTO found_state FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND run_ref=run AND step_key=step;
 RETURN found_state;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_step_preparation_v1(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_step_preparation_v1(uuid,uuid) TO memoriesql_application;

-- Owned expiry cleanup (owner Decision 1, item 3). Disclosure already ends at
-- expiry through the whole-closure verdict; cleanup then removes the content
-- and every sensitive copy: result bodies, witnesses and dependency records
-- (including query text and parameters), request and content digests, manifest
-- hashes, disclosure receipts, delivered refs and cursors. A noncontent
-- tombstone remains for 30 days (opaque run/step/result identifiers, terminal
-- state, timestamps, work charges and idempotency disposition), then is deleted
-- with its retained journal charge. Each subject is purged atomically, so an
-- interrupted pass leaves nothing partial and the next pass completes it.
-- Failures are recorded with their SQLSTATE and retried every pass.
--
-- Deadline mechanism: the database cannot run without a caller. The host runs
-- a bounded pass for the workspace before every run start, and the trusted host
-- must schedule `purge_expired_query_state_v1` for all workspaces. Content
-- cleanup more than 24 hours past due, whether missed or failed, refuses new
-- runs in that workspace and is reported by `query_cleanup_status_v1`.
CREATE TABLE memoriesql.query_run_purges (
 tenant_id uuid NOT NULL, run_ref uuid NOT NULL, purged_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_ref),
 FOREIGN KEY(tenant_id,run_ref) REFERENCES memoriesql.query_runs(tenant_id,run_ref)
);
CREATE TABLE memoriesql.query_result_purges (
 tenant_id uuid NOT NULL, result_id uuid NOT NULL, workspace_id uuid NOT NULL,
 run_ref uuid NOT NULL, operation_ref uuid NOT NULL,
 expired_at timestamptz NOT NULL, purged_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,result_id)
);
CREATE INDEX query_result_purges_run ON memoriesql.query_result_purges(tenant_id,run_ref);
CREATE TABLE memoriesql.query_purge_failures (
 tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,
 subject_kind text NOT NULL CHECK(subject_kind IN ('run','result','tombstone')),
 subject_ref uuid NOT NULL, attempts integer NOT NULL CHECK(attempts>0),
 first_failed_at timestamptz NOT NULL, last_failed_at timestamptz NOT NULL,
 error_code text NOT NULL CHECK(error_code ~ '^[0-9A-Z]{5}$'),
 PRIMARY KEY(tenant_id,subject_kind,subject_ref)
);
-- Owned cleanup deletes invocations; the foreign-key check on their staged
-- rows runs as the row table's owner, which M0033 left without builtin access.
GRANT EXECUTE ON FUNCTION pg_catalog.uuid_eq(uuid,uuid) TO memoriesql_query_view_owner;
CREATE INDEX query_result_creations_expiry ON memoriesql.query_result_creations(expires_at);
CREATE INDEX query_runs_expiry ON memoriesql.query_runs(expires_at);
ALTER TABLE memoriesql.query_run_purges ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_run_purges FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_result_purges ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_result_purges FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_purge_failures ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_purge_failures FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_run_purges,memoriesql.query_result_purges,
 memoriesql.query_purge_failures FROM PUBLIC,memoriesql_application;
CREATE TRIGGER query_run_purges_no_update BEFORE UPDATE ON memoriesql.query_run_purges
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER query_result_purges_no_update BEFORE UPDATE ON memoriesql.query_result_purges
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Purge one expired run's own sensitive copies. Ended owners' deliveries settle
-- exactly as in host recovery (confirmed reader termination, full reservation);
-- live or uncertain work keeps the run pending. A stranded preparation can no
-- longer commit (its invocations are settled and past their deadline and the
-- run admits nothing), so its reservation drops to the journal charge.
CREATE FUNCTION memoriesql.purge_query_run_v1(t uuid,run uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off AS $$
DECLARE r memoriesql.query_runs%ROWTYPE; d record; o record; s record; created uuid;
 now_at timestamptz;
BEGIN
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=t AND run_ref=run;
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':query-access:'||r.workspace_id::text,0));
 now_at:=clock_timestamp();
 IF now_at<r.expires_at OR EXISTS(SELECT 1 FROM memoriesql.query_run_purges
   WHERE tenant_id=t AND run_ref=run) THEN RETURN 'skipped'; END IF;
 FOR d IN SELECT delivery_ref FROM memoriesql.query_deliveries WHERE tenant_id=t AND run_ref=run
  AND state<>'settled' ORDER BY admitted_at LOOP
  IF NOT memoriesql.abandon_query_delivery_v1(d.delivery_ref) THEN RETURN 'pending'; END IF;
 END LOOP;
 now_at:=clock_timestamp();
 IF EXISTS(SELECT 1 FROM memoriesql_query.invocations i WHERE i.tenant_id=t AND i.run_ref=run
   AND (i.state<>'settled' OR i.deadline>now_at)) THEN RETURN 'pending'; END IF;
 FOR o IN SELECT run_ref,step_key FROM memoriesql.result_preparation_operations
  WHERE tenant_id=t AND run_ref=run ORDER BY step_key LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation-operation:'||o.run_ref::text||':'||o.step_key::text,0));
 END LOOP;
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation:'||r.workspace_id::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(t,r.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 FOR o IN SELECT * FROM memoriesql.result_preparation_operations WHERE tenant_id=t AND run_ref=run
  AND state<>'sealed' ORDER BY operation_ref FOR UPDATE LOOP
  DELETE FROM memoriesql_query.population_rows p USING memoriesql_query.invocations i
   WHERE i.tenant_id=t AND i.operation_ref=o.operation_ref AND p.invocation_ref=i.invocation_ref;
  DELETE FROM memoriesql_query.invocations WHERE tenant_id=t AND operation_ref=o.operation_ref;
  IF o.state='reserved' THEN
   UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL,
    encoded_bytes=0,allocation_bytes=8192 WHERE tenant_id=t AND operation_ref=o.operation_ref;
  END IF;
 END LOOP;
 -- Unresolved steps get their terminal disposition: a committed result makes
 -- the step complete; anything else is execution_error, never a rerun.
 FOR s IN SELECT step_key FROM memoriesql.query_steps WHERE tenant_id=t AND run_ref=run
  AND state='executing' FOR UPDATE LOOP
  SELECT cr.result_id INTO created FROM memoriesql.result_preparation_operations op
   JOIN memoriesql.query_result_creations cr ON cr.tenant_id=op.tenant_id
    AND cr.operation_ref=op.operation_ref
   WHERE op.tenant_id=t AND op.run_ref=run AND op.step_key=s.step_key;
  UPDATE memoriesql.query_steps SET state=CASE WHEN created IS NULL THEN 'failed' ELSE 'complete' END,
   outcome=CASE WHEN created IS NULL THEN 'execution_error' END,
   error_code=CASE WHEN created IS NULL THEN 'database' END,result_id=created,
   request_fingerprint=NULL,content_digest=NULL,first_row=NULL,row_count=NULL,page_size=NULL,
   completed_at=now_at,purged_at=now_at
   WHERE tenant_id=t AND run_ref=run AND step_key=s.step_key;
 END LOOP;
 UPDATE memoriesql.query_steps SET request_fingerprint=NULL,content_digest=NULL,first_row=NULL,
  row_count=NULL,page_size=NULL,purged_at=now_at
  WHERE tenant_id=t AND run_ref=run AND purged_at IS NULL;
 UPDATE memoriesql.query_deliveries SET response_sha256=NULL
  WHERE tenant_id=t AND run_ref=run AND response_sha256 IS NOT NULL;
 DELETE FROM memoriesql.query_visible_refs WHERE tenant_id=t AND run_ref=run;
 INSERT INTO memoriesql.query_run_purges VALUES(t,run,now_at);
 RETURN 'purged';
END $$;
REVOKE ALL ON FUNCTION memoriesql.purge_query_run_v1(uuid,uuid) FROM PUBLIC;

-- Purge one expired result: its body, witness and dependency records, creation
-- and identity rows, invocation hashes, receipts and cursors. Retained
-- allocation drops to the noncontent journal charge. A child or checkpoint
-- hold would keep required bytes; none can exist in this contract cut.
CREATE FUNCTION memoriesql.purge_query_result_v1(t uuid,result uuid) RETURNS text
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
 IF NOT FOUND OR now_at<c.expires_at THEN RETURN 'skipped'; END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_parent_holds
   WHERE tenant_id=t AND parent_ref=result) THEN RETURN 'held'; END IF;
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
REVOKE ALL ON FUNCTION memoriesql.purge_query_result_v1(uuid,uuid) FROM PUBLIC;

-- Delete a purged run's tombstone 30 days after its last purge, with the
-- journal rows and their retained charge. Unpurged results of the run, or
-- receipts and cursors still bound to its deliveries, keep it pending.
CREATE FUNCTION memoriesql.expire_query_tombstone_v1(t uuid,run uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off AS $$
DECLARE r memoriesql.query_runs%ROWTYPE; marker memoriesql.query_run_purges%ROWTYPE;
 last_purge timestamptz;
BEGIN
 SELECT * INTO r FROM memoriesql.query_runs WHERE tenant_id=t AND run_ref=run;
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':query-access:'||r.workspace_id::text,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':result-preparation:'||r.workspace_id::text,0));
 INSERT INTO memoriesql.result_preparation_allocators VALUES(t,r.workspace_id,1)
 ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET revision=memoriesql.result_preparation_allocators.revision+1;
 SELECT * INTO marker FROM memoriesql.query_run_purges WHERE tenant_id=t AND run_ref=run;
 IF NOT FOUND THEN RETURN 'skipped'; END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.result_preparation_operations
   WHERE tenant_id=t AND run_ref=run AND state<>'discarded')
  OR EXISTS(SELECT 1 FROM memoriesql.query_disclosures x JOIN memoriesql.query_deliveries d
   ON d.tenant_id=x.tenant_id AND d.delivery_ref=x.delivery_ref WHERE d.tenant_id=t AND d.run_ref=run)
  OR EXISTS(SELECT 1 FROM memoriesql.query_cursors x JOIN memoriesql.query_deliveries d
   ON d.tenant_id=x.tenant_id AND d.delivery_ref=x.issued_by_delivery
   WHERE d.tenant_id=t AND d.run_ref=run) THEN
  RETURN 'pending';
 END IF;
 SELECT GREATEST(marker.purged_at,max(purged_at)) INTO last_purge
  FROM memoriesql.query_result_purges WHERE tenant_id=t AND run_ref=run;
 IF clock_timestamp()<COALESCE(last_purge,marker.purged_at)+interval '30 days' THEN
  RETURN 'skipped';
 END IF;
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
REVOKE ALL ON FUNCTION memoriesql.expire_query_tombstone_v1(uuid,uuid) FROM PUBLIC;

-- One bounded cleanup pass over due subjects (optionally one workspace): runs,
-- then results, then tombstones, oldest first, with previously failing
-- subjects last so they cannot starve healthy ones. Each subject runs in its
-- own subtransaction; a failure records its SQLSTATE and the pass continues.
CREATE FUNCTION memoriesql.purge_expired_query_state_v1(max_items integer,
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
 FOR item IN
  SELECT due.* FROM (
   SELECT 1 AS phase,'run'::text AS kind,r.tenant_id,r.workspace_id,r.run_ref AS subject,
    r.expires_at AS due_at
    FROM memoriesql.query_runs r WHERE r.expires_at<=started
     AND NOT EXISTS(SELECT 1 FROM memoriesql.query_run_purges p
      WHERE p.tenant_id=r.tenant_id AND p.run_ref=r.run_ref)
   UNION ALL
   SELECT 2,'result',c.tenant_id,o.workspace_id,c.result_id,c.expires_at
    FROM memoriesql.query_result_creations c JOIN memoriesql.result_preparation_operations o
     ON o.tenant_id=c.tenant_id AND o.operation_ref=c.operation_ref
    WHERE c.expires_at<=started
   UNION ALL
   SELECT 3,'tombstone',p.tenant_id,r.workspace_id,p.run_ref,p.purged_at+interval '30 days'
    FROM memoriesql.query_run_purges p JOIN memoriesql.query_runs r
     ON r.tenant_id=p.tenant_id AND r.run_ref=p.run_ref
    WHERE p.purged_at<=started-interval '30 days'
  ) due
  LEFT JOIN memoriesql.query_purge_failures f ON f.tenant_id=due.tenant_id
   AND f.subject_kind=due.kind AND f.subject_ref=due.subject
  WHERE only_tenant IS NULL OR (due.tenant_id=only_tenant AND due.workspace_id=only_workspace)
  ORDER BY f.subject_ref IS NOT NULL,due.phase,due.due_at,due.subject
  LIMIT max_items
 LOOP
  EXIT WHEN clock_timestamp()-started>interval '5 seconds';
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
REVOKE ALL ON FUNCTION memoriesql.purge_expired_query_state_v1(integer,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.purge_expired_query_state_v1(integer,uuid,uuid) TO memoriesql_application;

-- The caller's own workspace only; any authenticated principal of the workspace
-- may trigger it, since it removes nothing that is still accessible.
CREATE FUNCTION memoriesql.purge_workspace_query_state_v1(max_items integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
 SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 IF c.context_id IS NULL THEN
  RAISE EXCEPTION 'query_cleanup_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN memoriesql.purge_expired_query_state_v1(max_items,c.tenant_id,c.workspace_id);
END $$;
REVOKE ALL ON FUNCTION memoriesql.purge_workspace_query_state_v1(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.purge_workspace_query_state_v1(integer) TO memoriesql_application;

-- Content cleanup more than 24 hours past due in one workspace.
CREATE FUNCTION memoriesql.query_cleanup_overdue_v1(t uuid,w uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM memoriesql.query_runs r WHERE r.tenant_id=t AND r.workspace_id=w
   AND r.expires_at<clock_timestamp()-interval '24 hours'
   AND NOT EXISTS(SELECT 1 FROM memoriesql.query_run_purges p
    WHERE p.tenant_id=r.tenant_id AND p.run_ref=r.run_ref))
  OR EXISTS(SELECT 1 FROM memoriesql.query_result_creations c
   JOIN memoriesql.result_preparation_operations o
    ON o.tenant_id=c.tenant_id AND o.operation_ref=c.operation_ref
   WHERE c.tenant_id=t AND o.workspace_id=w
    AND c.expires_at<clock_timestamp()-interval '24 hours')
$$;
REVOKE ALL ON FUNCTION memoriesql.query_cleanup_overdue_v1(uuid,uuid) FROM PUBLIC;

-- Noncontent cleanup status of the caller's workspace for the trusted host or
-- operator: due and overdue subjects, failures and whether admission is open.
CREATE FUNCTION memoriesql.query_cleanup_status_v1() RETURNS jsonb
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
 SELECT count(*),count(*) FILTER (WHERE x.expires_at<now_at-interval '24 hours'),
  LEAST(oldest,min(x.expires_at) FILTER (WHERE x.expires_at<now_at-interval '24 hours'))
  INTO due_results,late_results,oldest
  FROM memoriesql.query_result_creations x JOIN memoriesql.result_preparation_operations o
   ON o.tenant_id=x.tenant_id AND o.operation_ref=x.operation_ref
  WHERE x.tenant_id=c.tenant_id AND o.workspace_id=c.workspace_id AND x.expires_at<=now_at;
 SELECT count(*),count(*) FILTER (WHERE p.purged_at<now_at-interval '31 days')
  INTO due_tombstones,late_tombstones
  FROM memoriesql.query_run_purges p JOIN memoriesql.query_runs r
   ON r.tenant_id=p.tenant_id AND r.run_ref=p.run_ref
  WHERE p.tenant_id=c.tenant_id AND r.workspace_id=c.workspace_id
   AND p.purged_at<=now_at-interval '30 days';
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
REVOKE ALL ON FUNCTION memoriesql.query_cleanup_status_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.query_cleanup_status_v1() TO memoriesql_application;
