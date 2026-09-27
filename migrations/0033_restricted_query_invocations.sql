-- Trusted invocation preparation/settlement only; no available result API.
-- M0030 owns lifecycle/authority; M0032 owns the canonical prepared population.
DO $roles$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='memoriesql_query_view_owner') THEN
  CREATE ROLE memoriesql_query_view_owner NOLOGIN NOSUPERUSER NOBYPASSRLS
   NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='memoriesql_query_view_owner'
  AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication))
  OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='memoriesql_query_view_owner'::regrole) THEN
  RAISE EXCEPTION 'incompatible_query_owner';
 END IF;
END $roles$;

CREATE SCHEMA memoriesql_query;
CREATE SCHEMA memory_v1;
REVOKE ALL ON SCHEMA memoriesql_query,memory_v1 FROM PUBLIC;
GRANT USAGE ON SCHEMA memoriesql_query,memory_v1 TO memoriesql_query_view_owner;

CREATE TABLE memoriesql_query.invocations (
 invocation_ref uuid PRIMARY KEY, tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,
 principal_id uuid NOT NULL, credential_id uuid NOT NULL, owner_user_id uuid,
 operation_ref uuid NOT NULL, ownership_ref uuid NOT NULL,
 run_ref uuid NOT NULL, step_key uuid NOT NULL,
 reader_oid oid NOT NULL, reader_pid integer NOT NULL, reader_start timestamptz NOT NULL,
 reader_transaction text NOT NULL, issuer_pid integer NOT NULL,
 issuer_start timestamptz NOT NULL, issuer_transaction text NOT NULL,
 frame_ref uuid NOT NULL, catalog_hash text NOT NULL, policy_hash text NOT NULL,
 scope_hash text NOT NULL, projection_manifest_sha256 text NOT NULL,
 known_at timestamptz NOT NULL, snapshot_at timestamptz NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(), deadline timestamptz NOT NULL,
 reserved_ms integer NOT NULL CHECK(reserved_ms BETWEEN 1 AND 30000),
 observed_ms bigint CHECK(observed_ms>=0),
 encoded_stage_bytes bigint NOT NULL CHECK(encoded_stage_bytes>=0),
 state text NOT NULL CHECK(state IN ('executing','cancellation_requested','settlement_pending','settled')),
 outcome text CHECK(outcome IN ('complete','unavailable','budget_exhausted','cancelled','execution_error')),
 settled_at timestamptz,
 UNIQUE(tenant_id,operation_ref),
 FOREIGN KEY(tenant_id,operation_ref) REFERENCES memoriesql.result_preparation_operations(tenant_id,operation_ref),
 CHECK(reader_transaction ~ '^[0-9]+/[0-9]+$' AND issuer_transaction ~ '^[0-9]+/[0-9]+$'),
 CHECK(catalog_hash ~ '^[a-f0-9]{64}$' AND policy_hash ~ '^[a-f0-9]{64}$'
  AND scope_hash ~ '^[a-f0-9]{64}$' AND projection_manifest_sha256 ~ '^[a-f0-9]{64}$'),
 CHECK((state='settled')=(outcome IS NOT NULL) AND (state='settled')=(settled_at IS NOT NULL))
);
CREATE TABLE memoriesql_query.population_rows (
 invocation_ref uuid NOT NULL REFERENCES memoriesql_query.invocations(invocation_ref),
 relation_name text NOT NULL, ordinal bigint NOT NULL CHECK(ordinal>0), payload jsonb NOT NULL,
 PRIMARY KEY(invocation_ref,relation_name,ordinal), CHECK(jsonb_typeof(payload)='object')
);
CREATE INDEX query_invocations_active_reader ON memoriesql_query.invocations(reader_pid)
 WHERE state='executing';
ALTER TABLE memoriesql_query.invocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql_query.invocations FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql_query.population_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql_query.population_rows FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql_query.population_rows OWNER TO memoriesql_query_view_owner;
REVOKE ALL ON memoriesql_query.invocations,memoriesql_query.population_rows
 FROM PUBLIC,memoriesql_application;
CREATE TRIGGER query_population_immutable BEFORE UPDATE ON memoriesql_query.population_rows
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Only the trusted issuer can create/withdraw a binding. No GUC supplies scope.
-- virtualxid binds even a READ ONLY transaction without assigning a write XID,
-- and can be observed after BEGIN/SET utilities but before the RR data snapshot.
CREATE FUNCTION memoriesql_query.invocation_visible_v1(ref uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query,memoriesql
 SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM memoriesql_query.invocations i
  JOIN pg_roles r ON r.oid=i.reader_oid
  JOIN memoriesql.authentication_credentials cr ON cr.tenant_id=i.tenant_id AND cr.credential_id=i.credential_id
  JOIN pg_stat_activity reader ON reader.pid=i.reader_pid AND reader.backend_start=i.reader_start
  JOIN pg_stat_activity issuer ON issuer.pid=i.issuer_pid AND issuer.backend_start=i.issuer_start
 WHERE i.invocation_ref=ref AND i.state='executing' AND i.deadline>clock_timestamp()
   AND current_setting('transaction_read_only')='on'
   AND current_setting('transaction_isolation')='repeatable read'
   AND cr.status='active' AND cr.expires_at>clock_timestamp()
   AND r.rolname=SESSION_USER AND NOT r.rolsuper AND NOT r.rolbypassrls
   AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
   AND i.reader_pid=pg_backend_pid() AND reader.datid=issuer.datid
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=i.reader_pid AND locktype='virtualxid'
       AND granted AND virtualxid=i.reader_transaction)
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=i.issuer_pid AND locktype='virtualxid'
       AND granted AND virtualxid=i.issuer_transaction))
$$;
REVOKE ALL ON FUNCTION memoriesql_query.invocation_visible_v1(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql_query.invocation_visible_v1(uuid) TO memoriesql_query_view_owner;
CREATE FUNCTION memoriesql_query.active_invocation_v1() RETURNS uuid
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query
 SET row_security=off AS $$
 SELECT min(invocation_ref::text)::uuid FROM memoriesql_query.invocations
 WHERE reader_pid=pg_backend_pid() AND state='executing'
  AND memoriesql_query.invocation_visible_v1(invocation_ref) HAVING count(*)=1
$$;
REVOKE ALL ON FUNCTION memoriesql_query.active_invocation_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql_query.active_invocation_v1() TO memoriesql_query_view_owner;
CREATE POLICY invocation_scope ON memoriesql_query.population_rows
 USING(invocation_ref=(SELECT memoriesql_query.active_invocation_v1()));

CREATE FUNCTION memoriesql.stage_relation_query_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; sc memoriesql.authorization_contexts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE;
 ref uuid:=gen_random_uuid(); reader record; source record; item record; n bigint;
 budget integer:=(request->>'reserved_ms')::integer;
 remaining integer:=(request->>'remaining_ms')::integer; now_at timestamptz:=clock_timestamp();
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 sc:=jsonb_populate_record(NULL::memoriesql.authorization_contexts,request->'issuer_context');
 IF sc.context_id IS NULL OR sc.backend_pid IS DISTINCT FROM (request->>'issuer_pid')::integer
  OR sc.expires_at<=clock_timestamp() OR sc.tenant_id IS DISTINCT FROM c.tenant_id
  OR sc.workspace_id IS DISTINCT FROM c.workspace_id OR sc.credential_id IS DISTINCT FROM c.credential_id
  OR sc.principal_id IS DISTINCT FROM c.principal_id OR sc.user_id IS DISTINCT FROM c.user_id
  OR sc.on_behalf_of_user_id IS DISTINCT FROM c.on_behalf_of_user_id
  OR sc.pairing_grant_id IS DISTINCT FROM c.pairing_grant_id
  OR sc.membership_revision IS DISTINCT FROM c.membership_revision
  OR sc.pairing_grant_revision IS DISTINCT FROM c.pairing_grant_revision THEN
  RAISE EXCEPTION 'query_source_context_unavailable' USING ERRCODE='42501';
 END IF;
 IF budget IS NULL OR budget NOT BETWEEN 1 AND 30000 OR remaining IS NULL OR remaining NOT BETWEEN 1 AND budget THEN
  RAISE EXCEPTION 'invalid_query_invocation' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':result-preparation:'||c.workspace_id::text,0));
 UPDATE memoriesql.result_preparation_allocators SET revision=revision+1
  WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id;
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND operation_ref=(request->>'operation_ref')::uuid FOR UPDATE;
 IF NOT FOUND OR o.state<>'reserved' OR o.ownership_ref<>(request->>'ownership_ref')::uuid
  OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) THEN
  RAISE EXCEPTION 'query_invocation_unavailable' USING ERRCODE='42501';
 END IF;
 IF EXISTS(SELECT 1 FROM memoriesql_query.invocations WHERE tenant_id=c.tenant_id
  AND (operation_ref=o.operation_ref OR (workspace_id=c.workspace_id AND state<>'settled'))) THEN
  RAISE EXCEPTION 'query_ownership_pending' USING ERRCODE='55000';
 END IF;
 SELECT a.*,r.oid role_oid INTO reader FROM pg_stat_activity a JOIN pg_roles r ON r.oid=a.usesysid
  WHERE a.pid=(request->>'reader_pid')::integer AND a.backend_start=(request->>'reader_start')::timestamptz
   AND a.datid=(SELECT oid FROM pg_database WHERE datname=current_database())
   AND r.oid=(request->>'reader_oid')::oid AND r.rolcanlogin
   AND NOT(r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication)
   AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=a.pid AND locktype='virtualxid' AND granted
       AND virtualxid=request->>'reader_transaction');
 IF NOT FOUND THEN RAISE EXCEPTION 'query_login_unavailable' USING ERRCODE='42501'; END IF;
 SELECT a.* INTO source FROM pg_stat_activity a WHERE a.pid=(request->>'issuer_pid')::integer
  AND a.backend_start=(request->>'issuer_start')::timestamptz AND a.datid=reader.datid
  AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=a.pid AND locktype='virtualxid' AND granted
      AND virtualxid=request->>'issuer_transaction');
 IF NOT FOUND OR source.pid=reader.pid THEN
  RAISE EXCEPTION 'query_source_unavailable' USING ERRCODE='42501';
 END IF;
 n:=octet_length(memoriesql.lifecycle_canonical_json_v1(request->'rows'))+8192;
 IF n>o.reservation_bytes OR jsonb_typeof(request->'rows')<>'object' THEN
  RAISE EXCEPTION 'query_stage_budget_exhausted' USING ERRCODE='54000';
 END IF;
 INSERT INTO memoriesql_query.invocations(invocation_ref,tenant_id,workspace_id,principal_id,
  credential_id,owner_user_id,operation_ref,ownership_ref,run_ref,step_key,
  reader_oid,reader_pid,reader_start,reader_transaction,issuer_pid,issuer_start,issuer_transaction,
  frame_ref,catalog_hash,policy_hash,scope_hash,projection_manifest_sha256,known_at,snapshot_at,
  started_at,deadline,reserved_ms,encoded_stage_bytes,state)
 VALUES(ref,c.tenant_id,c.workspace_id,c.principal_id,c.credential_id,COALESCE(c.on_behalf_of_user_id,c.user_id),
  o.operation_ref,o.ownership_ref,o.run_ref,o.step_key,reader.role_oid,reader.pid,reader.backend_start,
  request->>'reader_transaction',source.pid,source.backend_start,request->>'issuer_transaction',
  (request->>'frame_ref')::uuid,request->>'catalog_hash',request->>'policy_hash',request->>'scope_hash',
  request->>'projection_manifest_sha256',(request->>'known_at')::timestamptz,(request->>'snapshot_at')::timestamptz,
  now_at,LEAST(now_at+remaining*interval '1 millisecond',c.expires_at,sc.expires_at,
   (SELECT expires_at FROM memoriesql.authentication_credentials WHERE tenant_id=c.tenant_id AND credential_id=c.credential_id)),
  budget,n,'executing');
 FOR item IN SELECT key,value FROM jsonb_each(request->'rows') LOOP
  IF item.key NOT IN ('assessed_relations','relation_statements','relation_evidence','relation_events',
   'relation_event_evidence','relation_types','relation_pairs','relation_corrections','relation_replacements')
   OR jsonb_typeof(item.value)<>'array' THEN
   RAISE EXCEPTION 'invalid_query_population' USING ERRCODE='22023';
  END IF;
  INSERT INTO memoriesql_query.population_rows
   SELECT ref,item.key,ordinality,value FROM jsonb_array_elements(item.value) WITH ORDINALITY;
 END LOOP;
 IF (SELECT count(*) FROM jsonb_object_keys(request->'rows'))<>9 THEN
  RAISE EXCEPTION 'incomplete_query_population' USING ERRCODE='22023';
 END IF;
 RETURN jsonb_build_object('invocation_ref',ref,'deadline',(SELECT deadline FROM memoriesql_query.invocations WHERE invocation_ref=ref));
END $$;
REVOKE ALL ON FUNCTION memoriesql.stage_relation_query_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.stage_relation_query_v1(jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.check_relation_query_v1(ref uuid,owner_ref uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query,memoriesql
 SET row_security=off AS $$
 SELECT EXISTS(SELECT 1 FROM memoriesql_query.invocations i
  JOIN memoriesql.authentication_credentials cr ON cr.tenant_id=i.tenant_id AND cr.credential_id=i.credential_id
  JOIN pg_stat_activity reader ON reader.pid=i.reader_pid AND reader.backend_start=i.reader_start AND reader.usesysid=i.reader_oid
  JOIN pg_stat_activity issuer ON issuer.pid=i.issuer_pid AND issuer.backend_start=i.issuer_start AND issuer.datid=reader.datid
  WHERE invocation_ref=ref AND ownership_ref=owner_ref AND i.state='executing'
   AND i.deadline>clock_timestamp() AND cr.status='active' AND cr.expires_at>clock_timestamp()
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=i.reader_pid AND locktype='virtualxid' AND granted AND virtualxid=i.reader_transaction)
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=i.issuer_pid AND locktype='virtualxid' AND granted AND virtualxid=i.issuer_transaction))
$$;
REVOKE ALL ON FUNCTION memoriesql.check_relation_query_v1(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.check_relation_query_v1(uuid,uuid) TO memoriesql_application;

-- Blind owned cleanup is permitted after authority loss; it discloses no data.
-- Never cancel a PID reused by another backend or a later transaction on it.
CREATE FUNCTION memoriesql.cancel_relation_query_v1(ref uuid,owner_ref uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE i memoriesql_query.invocations%ROWTYPE; stopped boolean:=false;
BEGIN
 SELECT * INTO i FROM memoriesql_query.invocations WHERE invocation_ref=ref AND ownership_ref=owner_ref FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'query_ownership_unavailable' USING ERRCODE='42501'; END IF;
 IF i.state='settled' THEN RETURN false; END IF;
 UPDATE memoriesql_query.invocations SET state='cancellation_requested' WHERE invocation_ref=ref;
 SELECT pg_cancel_backend(a.pid) INTO stopped FROM pg_stat_activity a
  WHERE a.pid=i.reader_pid AND a.backend_start=i.reader_start AND a.usesysid=i.reader_oid
   AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=a.pid AND locktype='virtualxid' AND granted AND virtualxid=i.reader_transaction);
 RETURN COALESCE(stopped,false);
END $$;
REVOKE ALL ON FUNCTION memoriesql.cancel_relation_query_v1(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.cancel_relation_query_v1(uuid,uuid) TO memoriesql_application;

CREATE FUNCTION memoriesql.settle_relation_query_v1(ref uuid,owner_ref uuid,terminal text,observed bigint) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query
 SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE i memoriesql_query.invocations%ROWTYPE; live boolean;
BEGIN
 IF terminal IS NULL OR terminal NOT IN ('complete','unavailable','budget_exhausted','cancelled','execution_error')
  OR observed<0 THEN RAISE EXCEPTION 'invalid_query_settlement' USING ERRCODE='22023'; END IF;
 SELECT * INTO i FROM memoriesql_query.invocations WHERE invocation_ref=ref AND ownership_ref=owner_ref FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'query_ownership_unavailable' USING ERRCODE='42501'; END IF;
 IF i.state<>'settled' THEN
  SELECT EXISTS(SELECT 1 FROM pg_stat_activity a WHERE a.pid=i.reader_pid AND a.backend_start=i.reader_start
   AND a.usesysid=i.reader_oid AND EXISTS(SELECT 1 FROM pg_locks WHERE pid=a.pid AND locktype='virtualxid'
      AND granted AND virtualxid=i.reader_transaction)) INTO live;
  IF live THEN
   UPDATE memoriesql_query.invocations SET state='settlement_pending',observed_ms=GREATEST(observed_ms,observed)
    WHERE invocation_ref=ref;
  ELSE
   DELETE FROM memoriesql_query.population_rows WHERE invocation_ref=ref;
   UPDATE memoriesql_query.invocations SET state='settled',outcome=terminal,
    observed_ms=GREATEST(observed_ms,observed),settled_at=clock_timestamp() WHERE invocation_ref=ref;
  END IF;
 END IF;
 SELECT * INTO i FROM memoriesql_query.invocations WHERE invocation_ref=ref;
 RETURN jsonb_build_object('invocation_ref',ref,'state',i.state,'outcome',i.outcome,
  'reserved_ms',i.reserved_ms,'observed_ms',i.observed_ms,
  'charged_ms',CASE WHEN i.state='settled' THEN COALESCE(i.observed_ms,i.reserved_ms) ELSE GREATEST(i.reserved_ms,COALESCE(i.observed_ms,0)) END);
END $$;
REVOKE ALL ON FUNCTION memoriesql.settle_relation_query_v1(uuid,uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.settle_relation_query_v1(uuid,uuid,text,bigint) TO memoriesql_application;

CREATE FUNCTION memoriesql.recover_relation_query_v1(op uuid,owner_ref uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query
 SET row_security=off AS $$
DECLARE i memoriesql_query.invocations%ROWTYPE;
BEGIN
 SELECT * INTO i FROM memoriesql_query.invocations WHERE operation_ref=op AND ownership_ref=owner_ref;
 IF NOT FOUND THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('invocation_ref',i.invocation_ref,'state',i.state,'outcome',i.outcome,
  'reserved_ms',i.reserved_ms,'observed_ms',i.observed_ms,
  'charged_ms',CASE WHEN i.state='settled' THEN COALESCE(i.observed_ms,i.reserved_ms) ELSE GREATEST(i.reserved_ms,COALESCE(i.observed_ms,0)) END);
END $$;
REVOKE ALL ON FUNCTION memoriesql.recover_relation_query_v1(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.recover_relation_query_v1(uuid,uuid) TO memoriesql_application;

CREATE FUNCTION memoriesql.query_invocation_requires_settlement_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql_query SET row_security=off AS $$
BEGIN
 IF NEW.state<>OLD.state AND EXISTS(SELECT 1 FROM memoriesql_query.invocations
  WHERE tenant_id=OLD.tenant_id AND operation_ref=OLD.operation_ref AND state<>'settled') THEN
  RAISE EXCEPTION 'query_settlement_pending' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.query_invocation_requires_settlement_v1() FROM PUBLIC;
CREATE TRIGGER preparation_requires_query_settlement BEFORE UPDATE ON memoriesql.result_preparation_operations
 FOR EACH ROW EXECUTE FUNCTION memoriesql.query_invocation_requires_settlement_v1();

-- Preserve only the pre-existing PUBLIC closure for trusted product roles.
-- Private lifecycle helpers stay private. Reader grants require a reviewed exact
-- manifest at opt-in; never automatically admit whatever the database contains.
DO $procedures$
DECLARE p record;
BEGIN
 FOR p IN SELECT DISTINCT f.oid::regprocedure signature FROM pg_proc f
  JOIN pg_namespace n ON n.oid=f.pronamespace
  CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) a
  WHERE n.nspname IN ('pg_catalog','information_schema','public','memoriesql','memoriesql_query')
   AND a.grantee=0 AND a.privilege_type='EXECUTE' LOOP
  EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO memoriesql_application,memoriesql_worker',p.signature);
 END LOOP;
END $procedures$;
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA pg_catalog,information_schema,public,memoriesql,memoriesql_query FROM PUBLIC;
REVOKE UPDATE ON pg_catalog.pg_settings FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
DO $database$
BEGIN
 EXECUTE format('REVOKE CREATE,TEMP ON DATABASE %I FROM PUBLIC',current_database());
 EXECUTE format('GRANT TEMP ON DATABASE %I TO memoriesql_application,memoriesql_worker',current_database());
END $database$;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE memoriesql_query_view_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Exact registry catalog projections; hidden invocation and ordinal stay private.
CREATE VIEW memory_v1.assessed_relations WITH (security_barrier=true) AS SELECT
 (payload->>'relation_id')::uuid AS "relation_id",
 (payload->>'task_id')::uuid AS "task_id",
 (payload->>'type_key')::text COLLATE "C" AS "type_key",
 (payload->>'type_revision')::int8 AS "type_revision",
 (payload->>'source_bead_id')::uuid AS "source_bead_id",
 (payload->>'source_bead_version_id')::uuid AS "source_bead_version_id",
 (payload->>'target_bead_id')::uuid AS "target_bead_id",
 (payload->>'target_bead_version_id')::uuid AS "target_bead_version_id",
 (payload->>'basis')::text COLLATE "C" AS "basis",
 (payload->>'rationale')::text COLLATE "C" AS "rationale",
 (payload->>'qualification')::text COLLATE "C" AS "qualification",
 (payload->>'author_confidence')::numeric AS "author_confidence",
 (payload->>'author_run_ref')::text COLLATE "C" AS "author_run_ref",
 (payload->>'specialist_run_ref')::text COLLATE "C" AS "specialist_run_ref",
 (payload->>'acceptance_receipt_id')::uuid AS "acceptance_receipt_id",
 (payload->>'recorded_at')::timestamptz AS "recorded_at",
 (payload->>'state')::text COLLATE "C" AS "state",
 (payload->>'head_token')::text COLLATE "C" AS "head_token",
 (payload->>'support_eligible')::bool AS "support_eligible",
 (payload->>'support_reason')::text COLLATE "C" AS "support_reason",
 (payload->>'correction_pending')::bool AS "correction_pending",
 (payload->>'roots_status')::text COLLATE "C" AS "roots_status",
 (payload->>'independent_root_count')::int8 AS "independent_root_count"
 FROM memoriesql_query.population_rows WHERE relation_name='assessed_relations';
ALTER VIEW memory_v1.assessed_relations OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_statements WITH (security_barrier=true) AS SELECT
 (payload->>'relation_id')::uuid AS "relation_id",
 (payload->>'role')::text COLLATE "C" AS "role",
 (payload->>'statement_id')::uuid AS "statement_id",
 (payload->>'bead_id')::uuid AS "bead_id",
 (payload->>'bead_version_id')::uuid AS "bead_version_id",
 (payload->>'text')::text COLLATE "C" AS "text"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_statements';
ALTER VIEW memory_v1.relation_statements OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_evidence WITH (security_barrier=true) AS SELECT
 (payload->>'relation_id')::uuid AS "relation_id",
 (payload->>'statement_id')::uuid AS "statement_id",
 (payload->>'source_unit_id')::uuid AS "source_unit_id",
 (payload->>'content_sha256')::text COLLATE "C" AS "content_sha256",
 (payload->>'evidence_ref')::uuid AS "evidence_ref",
 (payload->>'roots_status')::text COLLATE "C" AS "roots_status"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_evidence';
ALTER VIEW memory_v1.relation_evidence OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_events WITH (security_barrier=true) AS SELECT
 (payload->>'event_id')::uuid AS "event_id",
 (payload->>'relation_id')::uuid AS "relation_id",
 (payload->>'action')::text COLLATE "C" AS "action",
 (payload->>'related_relation_id')::uuid AS "related_relation_id",
 (payload->>'reason')::text COLLATE "C" AS "reason",
 (payload->>'origin')::text COLLATE "C" AS "origin",
 (payload->>'authoring_bead_id')::uuid AS "authoring_bead_id",
 (payload->>'effective_at')::timestamptz AS "effective_at",
 (payload->>'recorded_at')::timestamptz AS "recorded_at",
 (payload->>'event_number')::int8 AS "event_number",
 (payload->>'previous_event_id')::uuid AS "previous_event_id",
 (payload->>'recorded_by_principal_id')::uuid AS "recorded_by_principal_id",
 (payload->>'recorded_by_user_id')::uuid AS "recorded_by_user_id",
 (payload->>'idempotency_receipt_id')::uuid AS "idempotency_receipt_id"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_events';
ALTER VIEW memory_v1.relation_events OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_event_evidence WITH (security_barrier=true) AS SELECT
 (payload->>'event_id')::uuid AS "event_id",
 (payload->>'statement_id')::uuid AS "statement_id",
 (payload->>'source_unit_id')::uuid AS "source_unit_id",
 (payload->>'bead_id')::uuid AS "bead_id",
 (payload->>'bead_version_id')::uuid AS "bead_version_id",
 (payload->>'statement_text')::text COLLATE "C" AS "statement_text",
 (payload->>'content_sha256')::text COLLATE "C" AS "content_sha256",
 (payload->>'evidence_ref')::uuid AS "evidence_ref"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_event_evidence';
ALTER VIEW memory_v1.relation_event_evidence OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_types WITH (security_barrier=true) AS SELECT
 (payload->>'type_key')::text COLLATE "C" AS "type_key",
 (payload->>'type_revision')::int8 AS "type_revision",
 (payload->>'namespace')::text COLLATE "C" AS "namespace",
 (payload->>'label')::text COLLATE "C" AS "label",
 (payload->>'definition')::text COLLATE "C" AS "definition",
 (payload->>'endpoint_rule')::text COLLATE "C" AS "endpoint_rule",
 (payload->>'forward_reading')::text COLLATE "C" AS "forward_reading",
 (payload->>'inverse_reading')::text COLLATE "C" AS "inverse_reading",
 (payload->>'symmetric')::bool AS "symmetric",
 (payload->>'evidence_expectation')::text COLLATE "C" AS "evidence_expectation",
 (payload->>'example')::text COLLATE "C" AS "example",
 (payload->>'counterexample')::text COLLATE "C" AS "counterexample",
 (payload->>'cycle_policy')::text COLLATE "C" AS "cycle_policy"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_types';
ALTER VIEW memory_v1.relation_types OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_pairs WITH (security_barrier=true) AS SELECT
 (payload->>'task_id')::uuid AS "task_id",
 (payload->>'first_bead_id')::uuid AS "first_bead_id",
 (payload->>'second_bead_id')::uuid AS "second_bead_id",
 (payload->>'first_bead_version_id')::uuid AS "first_bead_version_id",
 (payload->>'second_bead_version_id')::uuid AS "second_bead_version_id",
 (payload->>'disposition')::text COLLATE "C" AS "disposition",
 (payload->>'abstention')::text COLLATE "C" AS "abstention",
 (payload->>'reason')::text COLLATE "C" AS "reason"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_pairs';
ALTER VIEW memory_v1.relation_pairs OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_corrections WITH (security_barrier=true) AS SELECT
 (payload->>'relation_id')::uuid AS "relation_id",
 (payload->>'role')::text COLLATE "C" AS "role",
 (payload->>'correcting_bead_id')::uuid AS "correcting_bead_id",
 (payload->>'correcting_bead_version_id')::uuid AS "correcting_bead_version_id"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_corrections';
ALTER VIEW memory_v1.relation_corrections OWNER TO memoriesql_query_view_owner;
CREATE VIEW memory_v1.relation_replacements WITH (security_barrier=true) AS SELECT
 (payload->>'relation_id')::uuid AS "relation_id",
 (payload->>'replacement_relation_id')::uuid AS "replacement_relation_id"
 FROM memoriesql_query.population_rows WHERE relation_name='relation_replacements';
ALTER VIEW memory_v1.relation_replacements OWNER TO memoriesql_query_view_owner;
