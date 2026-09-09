-- PR-01F: durable model-run receipts and the sole non-authoritative outcome sink.
-- Provider request accounting, pricing, and canonical semantic apply remain out of
-- scope.  Every mutation below is authenticated and generation-fenced by the
-- SQL-01D task attempt that owns it.

ALTER TABLE memoriesql.semantic_task_attempts
ADD CONSTRAINT semantic_task_attempts_pr01f_fence_uq UNIQUE (
    tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
    lease_generation
);

CREATE TABLE memoriesql.semantic_task_runs (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    lease_generation bigint NOT NULL,
    run_id text NOT NULL,
    parent_run_id text,
    delegation_id text,
    run_role text NOT NULL,
    sibling_order integer NOT NULL,
    agent_key text NOT NULL,
    input_contract_id text NOT NULL,
    input_contract_revision integer NOT NULL,
    input_contract_hash text NOT NULL,
    output_contract_id text NOT NULL,
    output_contract_revision integer NOT NULL,
    output_contract_hash text NOT NULL,
    model_profile_key text NOT NULL,
    model_profile_revision integer NOT NULL,
    effort_key text NOT NULL,
    run_status text NOT NULL,
    delegation_status text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    delegation_started_at timestamp with time zone,
    delegation_finished_at timestamp with time zone,
    settled boolean NOT NULL,
    CONSTRAINT semantic_task_runs_pk PRIMARY KEY (
        tenant_id, attempt_id, run_id
    ),
    CONSTRAINT semantic_task_runs_attempt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
        lease_generation
    ) REFERENCES memoriesql.semantic_task_attempts (
        tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
        lease_generation
    ),
    CONSTRAINT semantic_task_runs_parent_fk FOREIGN KEY (
        tenant_id, attempt_id, parent_run_id
    ) REFERENCES memoriesql.semantic_task_runs (
        tenant_id, attempt_id, run_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT semantic_task_runs_delegation_uq UNIQUE (
        tenant_id, attempt_id, delegation_id
    ),
    CONSTRAINT semantic_task_runs_sibling_uq UNIQUE NULLS NOT DISTINCT (
        tenant_id, attempt_id, parent_run_id, sibling_order
    ),
    CONSTRAINT semantic_task_runs_generation_positive CHECK (
        lease_generation > 0
    ),
    CONSTRAINT semantic_task_runs_identifier_shape CHECK (
        run_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(run_id) <= 128
        AND (parent_run_id IS NULL OR (
            parent_run_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
            AND length(parent_run_id) <= 128
        ))
        AND (delegation_id IS NULL OR (
            delegation_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
            AND length(delegation_id) <= 128
        ))
    ),
    CONSTRAINT semantic_task_runs_role_supported CHECK (
        run_role IN ('direct_leaf', 'conductor', 'delegate')
    ),
    CONSTRAINT semantic_task_runs_tree_shape CHECK (
        (
            run_role IN ('direct_leaf', 'conductor')
            AND parent_run_id IS NULL
            AND delegation_id IS NULL
            AND delegation_status IS NULL
            AND delegation_started_at IS NULL
            AND delegation_finished_at IS NULL
        ) OR (
            run_role = 'delegate'
            AND parent_run_id IS NOT NULL
            AND parent_run_id <> run_id
            AND delegation_id IS NOT NULL
            AND delegation_status IS NOT NULL
            AND delegation_started_at IS NOT NULL
        )
    ),
    CONSTRAINT semantic_task_runs_sibling_positive CHECK (sibling_order > 0),
    CONSTRAINT semantic_task_runs_operational_identifiers CHECK (
        agent_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(agent_key) <= 128
        AND input_contract_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(input_contract_id) <= 128
        AND output_contract_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(output_contract_id) <= 128
        AND model_profile_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(model_profile_key) <= 128
        AND effort_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(effort_key) <= 128
    ),
    CONSTRAINT semantic_task_runs_contract_shape CHECK (
        input_contract_revision > 0
        AND output_contract_revision > 0
        AND model_profile_revision > 0
        AND input_contract_hash ~ '^[a-f0-9]{64}$'
        AND output_contract_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_task_runs_status_supported CHECK (
        run_status IN ('delegation_started', 'running', 'succeeded', 'failed')
        AND (
            delegation_status IS NULL
            OR delegation_status IN ('started', 'finished')
        )
    ),
    CONSTRAINT semantic_task_runs_lifecycle_shape CHECK (
        (run_status = 'delegation_started'
            AND started_at IS NULL AND finished_at IS NULL)
        OR (run_status = 'running'
            AND started_at IS NOT NULL AND finished_at IS NULL)
        OR (run_status IN ('succeeded', 'failed')
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
            AND finished_at >= started_at)
    ),
    CONSTRAINT semantic_task_runs_settlement_shape CHECK (
        settled = (
            run_status IN ('succeeded', 'failed')
            AND (
                run_role <> 'delegate'
                OR (
                    delegation_status = 'finished'
                    AND delegation_finished_at IS NOT NULL
                    AND delegation_finished_at >= delegation_started_at
                )
            )
        )
    )
);

CREATE UNIQUE INDEX semantic_task_runs_one_root_uq
ON memoriesql.semantic_task_runs (tenant_id, attempt_id)
WHERE parent_run_id IS NULL;

CREATE TABLE memoriesql.semantic_synthetic_outcomes (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    lease_generation bigint NOT NULL,
    result_ref text NOT NULL,
    sink_key text NOT NULL DEFAULT 'synthetic.non_authoritative',
    authority_class text NOT NULL DEFAULT 'non_authoritative',
    output_contract_hash text NOT NULL,
    output_payload jsonb NOT NULL,
    output_hash text NOT NULL,
    used_evidence_refs text[] NOT NULL,
    model_run_refs text[] NOT NULL,
    validation_results jsonb NOT NULL,
    usage_requests integer NOT NULL,
    usage_input_tokens integer NOT NULL,
    usage_output_tokens integer NOT NULL,
    usage_tool_calls integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT semantic_synthetic_outcomes_pk PRIMARY KEY (
        tenant_id, attempt_id
    ),
    CONSTRAINT semantic_synthetic_outcomes_task_uq UNIQUE (
        tenant_id, task_id
    ),
    CONSTRAINT semantic_synthetic_outcomes_result_ref_uq UNIQUE (
        tenant_id, result_ref
    ),
    CONSTRAINT semantic_synthetic_outcomes_attempt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
        lease_generation
    ) REFERENCES memoriesql.semantic_task_attempts (
        tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
        lease_generation
    ),
    CONSTRAINT semantic_synthetic_outcomes_generation_positive CHECK (
        lease_generation > 0
    ),
    CONSTRAINT semantic_synthetic_outcomes_sink_exact CHECK (
        sink_key = 'synthetic.non_authoritative'
        AND authority_class = 'non_authoritative'
    ),
    CONSTRAINT semantic_synthetic_outcomes_hash_shape CHECK (
        output_contract_hash ~ '^[a-f0-9]{64}$'
        AND output_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_synthetic_outcomes_payload_shape CHECK (
        jsonb_typeof(output_payload) = 'object'
        AND pg_catalog.octet_length(output_payload::text) <= 1048576
    ),
    CONSTRAINT semantic_synthetic_outcomes_references_shape CHECK (
        cardinality(model_run_refs) > 0
        AND cardinality(model_run_refs) <= 64
        AND cardinality(used_evidence_refs) <= 256
        AND array_position(model_run_refs, NULL) IS NULL
        AND array_position(used_evidence_refs, NULL) IS NULL
    ),
    CONSTRAINT semantic_synthetic_outcomes_validation_shape CHECK (
        jsonb_typeof(validation_results) = 'array'
        AND jsonb_array_length(validation_results) <= 128
        AND pg_catalog.octet_length(validation_results::text) <= 32768
    ),
    CONSTRAINT semantic_synthetic_outcomes_usage_nonnegative CHECK (
        usage_requests >= 0
        AND usage_input_tokens >= 0
        AND usage_output_tokens >= 0
        AND usage_tool_calls >= 0
    )
);

CREATE FUNCTION memoriesql.settle_semantic_task_runs_from_attempt()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    settled_at timestamp with time zone;
BEGIN
    IF NEW.status = OLD.status OR NEW.status NOT IN (
        'retryable_failure', 'terminal_failure', 'cancelled', 'lease_lost'
    ) THEN
        RETURN NEW;
    END IF;
    settled_at := COALESCE(NEW.finished_at, pg_catalog.clock_timestamp());
    UPDATE memoriesql.semantic_task_runs AS run
       SET run_status = 'failed',
           started_at = COALESCE(run.started_at, run.delegation_started_at),
           finished_at = COALESCE(run.finished_at, settled_at),
           delegation_status = CASE
               WHEN run.run_role = 'delegate' THEN 'finished'
               ELSE NULL
           END,
           delegation_finished_at = CASE
               WHEN run.run_role = 'delegate' THEN
                   COALESCE(run.delegation_finished_at, settled_at)
               ELSE NULL
           END,
           settled = true
     WHERE run.tenant_id = NEW.tenant_id
       AND run.task_id = NEW.task_id
       AND run.attempt_id = NEW.attempt_id
       AND run.lease_generation = NEW.lease_generation
       AND NOT run.settled;
    RETURN NEW;
END;
$$;

CREATE TRIGGER semantic_task_attempts_settle_run_tree
AFTER UPDATE OF status ON memoriesql.semantic_task_attempts
FOR EACH ROW EXECUTE FUNCTION memoriesql.settle_semantic_task_runs_from_attempt();

CREATE OR REPLACE FUNCTION memoriesql.heartbeat_semantic_task(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_lease_seconds integer,
    requested_at timestamp with time zone
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
    slot_record memoriesql.semantic_concurrency_slots%ROWTYPE;
    new_expiry timestamp with time zone;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_lease_seconds IS NULL
       OR requested_lease_seconds < 90
       OR requested_lease_seconds > 3600
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN false;
    END IF;
    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status IN ('claimed', 'running');
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    -- Preserve SQL-01D's task -> attempt -> slot lock order. PR-01F makes
    -- heartbeat lease writes monotonic so an ordinary heartbeat that finishes
    -- after cleanup retention can never re-clamp the live cleanup fence.
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND task.result_attempt_id IS NULL
      AND task.cancel_requested_at IS NULL
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      )
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    SELECT attempt.* INTO attempt_record
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status IN ('claimed', 'running')
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF task_record.concurrency_key IS NOT NULL THEN
        SELECT slot.* INTO slot_record
        FROM memoriesql.semantic_concurrency_slots AS slot
        WHERE slot.tenant_id = requested_tenant_id
          AND slot.workspace_id = task_record.workspace_id
          AND slot.concurrency_key = task_record.concurrency_key
          AND slot.task_id = requested_task_id
          AND slot.attempt_id = requested_attempt_id
          AND slot.lease_generation = requested_lease_generation
          AND slot.lease_owner = requested_worker_id
          AND slot.worker_instance_id = requested_worker_instance_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN false;
        END IF;
    END IF;

    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR attempt_record.deadline_at <= database_now
       OR task_record.cancel_requested_at IS NOT NULL
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_semantic_task_authorized(
           requested_task_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       ) THEN
        RETURN false;
    END IF;

    new_expiry := GREATEST(
        task_record.lease_expires_at,
        attempt_record.lease_expires_at,
        COALESCE(slot_record.lease_expires_at, '-infinity'::timestamptz),
        LEAST(
            attempt_record.deadline_at,
            database_now + make_interval(secs => requested_lease_seconds)
        )
    );
    UPDATE memoriesql.semantic_task_attempts
       SET heartbeat_at = database_now,
           lease_expires_at = new_expiry
     WHERE tenant_id = requested_tenant_id
       AND attempt_id = requested_attempt_id
       AND task_id = requested_task_id
       AND lease_generation = requested_lease_generation
       AND worker_id = requested_worker_id
       AND worker_instance_id = requested_worker_instance_id
       AND status IN ('claimed', 'running');
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    UPDATE memoriesql.semantic_tasks
       SET heartbeat_at = database_now,
           lease_expires_at = new_expiry,
           updated_at = GREATEST(updated_at, database_now)
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    IF task_record.concurrency_key IS NOT NULL THEN
        UPDATE memoriesql.semantic_concurrency_slots
           SET lease_expires_at = new_expiry,
               updated_at = database_now
         WHERE tenant_id = requested_tenant_id
           AND workspace_id = task_record.workspace_id
           AND concurrency_key = task_record.concurrency_key
           AND task_id = requested_task_id
           AND attempt_id = requested_attempt_id
           AND lease_generation = requested_lease_generation
           AND lease_owner = requested_worker_id
           AND worker_instance_id = requested_worker_instance_id;
    END IF;
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.retain_semantic_task_cleanup_lease(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_lease_seconds integer,
    requested_at timestamp with time zone
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
    slot_record memoriesql.semantic_concurrency_slots%ROWTYPE;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
    new_expiry timestamp with time zone;
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_lease_seconds IS NULL
       OR requested_lease_seconds < 90
       OR requested_lease_seconds > 3600
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN false;
    END IF;

    -- Authenticate the immutable fence before taking the task row. Cleanup
    -- retention cannot claim, revive, or transfer ownership.
    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running';
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    -- Preserve SQL-01D's task -> attempt -> slot lock order. Unlike an
    -- execution heartbeat, this lease is intentionally not clamped to the
    -- immutable attempt deadline: it exists only while cancelled provider work
    -- is still attached to the owning worker. Origin/resource authorization
    -- loss stops execution and outcome authority, but cannot release this
    -- control-only ownership fence while that provider work remains attached.
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND task.result_attempt_id IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    SELECT attempt.* INTO attempt_record
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running'
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF task_record.concurrency_key IS NOT NULL THEN
        SELECT slot.* INTO slot_record
        FROM memoriesql.semantic_concurrency_slots AS slot
        WHERE slot.tenant_id = requested_tenant_id
          AND slot.workspace_id = task_record.workspace_id
          AND slot.concurrency_key = task_record.concurrency_key
          AND slot.task_id = requested_task_id
          AND slot.attempt_id = requested_attempt_id
          AND slot.lease_generation = requested_lease_generation
          AND slot.lease_owner = requested_worker_id
          AND slot.worker_instance_id = requested_worker_instance_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN false;
        END IF;
    END IF;

    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR attempt_record.lease_expires_at <= database_now
       OR (
           task_record.concurrency_key IS NOT NULL
           AND slot_record.lease_expires_at <= database_now
       )
       OR context_record.expires_at <= database_now THEN
        RETURN false;
    END IF;

    new_expiry := GREATEST(
        task_record.lease_expires_at,
        attempt_record.lease_expires_at,
        COALESCE(slot_record.lease_expires_at, '-infinity'::timestamptz),
        database_now + make_interval(secs => requested_lease_seconds)
    );
    UPDATE memoriesql.semantic_task_attempts
       SET heartbeat_at = database_now,
           lease_expires_at = new_expiry
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id
       AND attempt_id = requested_attempt_id
       AND lease_generation = requested_lease_generation
       AND status = 'running';
    UPDATE memoriesql.semantic_tasks
       SET heartbeat_at = database_now,
           lease_expires_at = new_expiry,
           updated_at = GREATEST(updated_at, database_now)
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    IF task_record.concurrency_key IS NOT NULL THEN
        UPDATE memoriesql.semantic_concurrency_slots
           SET lease_expires_at = new_expiry,
               updated_at = database_now
         WHERE tenant_id = requested_tenant_id
           AND workspace_id = task_record.workspace_id
           AND concurrency_key = task_record.concurrency_key
           AND task_id = requested_task_id
           AND attempt_id = requested_attempt_id
           AND lease_generation = requested_lease_generation;
    END IF;
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.semantic_task_authorization_snapshot(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_at timestamp with time zone
)
RETURNS TABLE (
    principal_id uuid,
    delegation_id uuid,
    policy_revision integer,
    access_scope_id uuid
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    IF memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'hydrate', requested_at
    ) <> 'authorized' THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT
        task.origin_principal_id,
        task.origin_pairing_grant_id,
        revision.revision,
        task.access_scope_id
    FROM memoriesql.current_authorization_context() AS context
    JOIN memoriesql.semantic_tasks AS task
      ON task.tenant_id = context.tenant_id
     AND task.workspace_id = context.workspace_id
    JOIN memoriesql.semantic_task_attempts AS attempt
      ON attempt.tenant_id = task.tenant_id
     AND attempt.task_id = task.task_id
     AND attempt.attempt_id = requested_attempt_id
     AND attempt.lease_generation = requested_lease_generation
     AND attempt.claimant_principal_id = context.principal_id
     AND attempt.worker_id = requested_worker_id
     AND attempt.worker_instance_id = requested_worker_instance_id
     AND attempt.status = 'running'
    JOIN memoriesql.access_scopes AS scope
      ON scope.tenant_id = task.tenant_id
     AND scope.workspace_id = task.workspace_id
     AND scope.access_scope_id = task.access_scope_id
     AND scope.status = 'active'
    JOIN memoriesql.access_policy_revisions AS revision
      ON revision.tenant_id = scope.tenant_id
     AND revision.workspace_id = scope.workspace_id
     AND revision.access_scope_id = scope.access_scope_id
     AND revision.policy_revision_id = scope.current_policy_revision_id
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id;
END;
$$;

CREATE FUNCTION memoriesql.record_semantic_run_event(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_event_kind text,
    requested_run_id text,
    requested_parent_run_id text,
    requested_run_role text,
    requested_agent_key text,
    requested_input_contract_id text,
    requested_input_contract_revision integer,
    requested_input_contract_hash text,
    requested_output_contract_id text,
    requested_output_contract_revision integer,
    requested_output_contract_hash text,
    requested_model_profile_key text,
    requested_model_profile_revision integer,
    requested_effort_key text,
    requested_delegation_id text,
    requested_at timestamp with time zone
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    existing_run memoriesql.semantic_task_runs%ROWTYPE;
    authorization_result text;
    next_sibling_order integer;
    terminal_status text;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR requested_event_kind NOT IN (
           'run.started', 'run.succeeded', 'run.failed',
           'delegation.started', 'delegation.finished'
       )
       OR requested_run_role NOT IN ('direct_leaf', 'conductor', 'delegate')
       OR requested_run_id !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_run_id) > 128
       OR requested_agent_key !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_agent_key) > 128
       OR requested_input_contract_id
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_input_contract_id) > 128
       OR requested_output_contract_id
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_output_contract_id) > 128
       OR requested_model_profile_key
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_model_profile_key) > 128
       OR requested_effort_key
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
       OR length(requested_effort_key) > 128
       OR requested_input_contract_revision <= 0
       OR requested_output_contract_revision <= 0
       OR requested_model_profile_revision <= 0
       OR requested_input_contract_hash !~ '^[a-f0-9]{64}$'
       OR requested_output_contract_hash !~ '^[a-f0-9]{64}$'
       OR (
           requested_run_role = 'delegate'
           AND (
               requested_parent_run_id IS NULL
               OR requested_delegation_id IS NULL
               OR requested_parent_run_id = requested_run_id
           )
       )
       OR (
           requested_run_role <> 'delegate'
           AND (
               requested_parent_run_id IS NOT NULL
               OR requested_delegation_id IS NOT NULL
               OR requested_event_kind LIKE 'delegation.%'
           )
       )
       OR requested_parent_run_id IS NOT NULL AND (
           requested_parent_run_id
              !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
           OR length(requested_parent_run_id) > 128
       )
       OR requested_delegation_id IS NOT NULL AND (
           requested_delegation_id
              !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
           OR length(requested_delegation_id) > 128
       ) THEN
        RAISE EXCEPTION 'semantic run event contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running';
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      )
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running'
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR context_record.expires_at <= database_now THEN
        RETURN false;
    END IF;
    authorization_result := memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'outcome', requested_at
    );
    IF authorization_result <> 'authorized' THEN
        RETURN false;
    END IF;

    SELECT * INTO existing_run
    FROM memoriesql.semantic_task_runs AS run
    WHERE run.tenant_id = requested_tenant_id
      AND run.attempt_id = requested_attempt_id
      AND run.run_id = requested_run_id;

    IF FOUND AND (
        existing_run.task_id IS DISTINCT FROM requested_task_id
        OR existing_run.lease_generation IS DISTINCT FROM
           requested_lease_generation
        OR existing_run.parent_run_id IS DISTINCT FROM requested_parent_run_id
        OR existing_run.run_role IS DISTINCT FROM requested_run_role
        OR existing_run.agent_key IS DISTINCT FROM requested_agent_key
        OR existing_run.input_contract_id IS DISTINCT FROM
           requested_input_contract_id
        OR existing_run.input_contract_revision IS DISTINCT FROM
           requested_input_contract_revision
        OR existing_run.input_contract_hash IS DISTINCT FROM
           requested_input_contract_hash
        OR existing_run.output_contract_id IS DISTINCT FROM
           requested_output_contract_id
        OR existing_run.output_contract_revision IS DISTINCT FROM
           requested_output_contract_revision
        OR existing_run.output_contract_hash IS DISTINCT FROM
           requested_output_contract_hash
        OR existing_run.model_profile_key IS DISTINCT FROM
           requested_model_profile_key
        OR existing_run.model_profile_revision IS DISTINCT FROM
           requested_model_profile_revision
        OR existing_run.effort_key IS DISTINCT FROM requested_effort_key
        OR existing_run.delegation_id IS DISTINCT FROM requested_delegation_id
    ) THEN
        RETURN false;
    END IF;

    IF requested_event_kind = 'delegation.started' THEN
        IF FOUND THEN
            RETURN existing_run.run_status = 'delegation_started'
               AND existing_run.delegation_status = 'started';
        END IF;
        PERFORM 1 FROM memoriesql.semantic_task_runs AS parent
        WHERE parent.tenant_id = requested_tenant_id
          AND parent.attempt_id = requested_attempt_id
          AND parent.run_id = requested_parent_run_id
          AND parent.run_role = 'conductor';
        IF NOT FOUND THEN
            RETURN false;
        END IF;
        SELECT COALESCE(MAX(run.sibling_order), 0) + 1
        INTO next_sibling_order
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.attempt_id = requested_attempt_id
          AND run.parent_run_id = requested_parent_run_id;
        INSERT INTO memoriesql.semantic_task_runs (
            tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
            lease_generation, run_id, parent_run_id, delegation_id, run_role,
            sibling_order, agent_key, input_contract_id,
            input_contract_revision, input_contract_hash, output_contract_id,
            output_contract_revision, output_contract_hash, model_profile_key,
            model_profile_revision, effort_key, run_status, delegation_status,
            delegation_started_at, settled
        ) VALUES (
            task_record.tenant_id, task_record.workspace_id,
            task_record.access_scope_id, task_record.task_id,
            requested_attempt_id, requested_lease_generation, requested_run_id,
            requested_parent_run_id, requested_delegation_id, requested_run_role,
            next_sibling_order, requested_agent_key, requested_input_contract_id,
            requested_input_contract_revision, requested_input_contract_hash,
            requested_output_contract_id, requested_output_contract_revision,
            requested_output_contract_hash, requested_model_profile_key,
            requested_model_profile_revision, requested_effort_key,
            'delegation_started', 'started', database_now, false
        );
        RETURN true;
    END IF;

    IF requested_event_kind = 'run.started' THEN
        IF FOUND THEN
            IF existing_run.run_role <> 'delegate' THEN
                RETURN existing_run.run_status = 'running';
            END IF;
            IF existing_run.run_status = 'running' THEN
                RETURN true;
            END IF;
            IF existing_run.run_status <> 'delegation_started' THEN
                RETURN false;
            END IF;
            UPDATE memoriesql.semantic_task_runs
               SET run_status = 'running', started_at = database_now
             WHERE tenant_id = requested_tenant_id
               AND attempt_id = requested_attempt_id
               AND run_id = requested_run_id;
            RETURN true;
        END IF;
        IF requested_run_role = 'delegate' THEN
            RETURN false;
        END IF;
        INSERT INTO memoriesql.semantic_task_runs (
            tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
            lease_generation, run_id, run_role, sibling_order, agent_key,
            input_contract_id, input_contract_revision, input_contract_hash,
            output_contract_id, output_contract_revision, output_contract_hash,
            model_profile_key, model_profile_revision, effort_key, run_status,
            started_at, settled
        ) VALUES (
            task_record.tenant_id, task_record.workspace_id,
            task_record.access_scope_id, task_record.task_id,
            requested_attempt_id, requested_lease_generation, requested_run_id,
            requested_run_role, 1, requested_agent_key,
            requested_input_contract_id, requested_input_contract_revision,
            requested_input_contract_hash, requested_output_contract_id,
            requested_output_contract_revision, requested_output_contract_hash,
            requested_model_profile_key, requested_model_profile_revision,
            requested_effort_key, 'running', database_now, false
        );
        RETURN true;
    END IF;

    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF requested_event_kind IN ('run.succeeded', 'run.failed') THEN
        terminal_status := CASE requested_event_kind
            WHEN 'run.succeeded' THEN 'succeeded'
            ELSE 'failed'
        END;
        IF existing_run.run_status = terminal_status THEN
            RETURN true;
        END IF;
        IF existing_run.run_status <> 'running' OR existing_run.settled THEN
            RETURN false;
        END IF;
        UPDATE memoriesql.semantic_task_runs
           SET run_status = terminal_status,
               finished_at = database_now,
               settled = (run_role <> 'delegate')
         WHERE tenant_id = requested_tenant_id
           AND attempt_id = requested_attempt_id
           AND run_id = requested_run_id;
        RETURN true;
    END IF;

    IF requested_event_kind = 'delegation.finished' THEN
        IF existing_run.delegation_status = 'finished' THEN
            RETURN true;
        END IF;
        IF existing_run.run_role <> 'delegate'
           OR existing_run.run_status NOT IN ('succeeded', 'failed')
           OR existing_run.delegation_status <> 'started'
           OR existing_run.settled THEN
            RETURN false;
        END IF;
        UPDATE memoriesql.semantic_task_runs
           SET delegation_status = 'finished',
               delegation_finished_at = database_now,
               settled = true
         WHERE tenant_id = requested_tenant_id
           AND attempt_id = requested_attempt_id
           AND run_id = requested_run_id;
        RETURN true;
    END IF;
    RETURN false;
END;
$$;

CREATE FUNCTION memoriesql.record_synthetic_semantic_task_outcome(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_output_contract_hash text,
    requested_output_payload jsonb,
    requested_output_hash text,
    requested_used_evidence_refs text[],
    requested_model_run_refs text[],
    requested_validation_results jsonb,
    requested_usage_requests integer,
    requested_usage_input_tokens integer,
    requested_usage_output_tokens integer,
    requested_usage_tool_calls integer,
    requested_at timestamp with time zone
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    outcome_status text;
    generated_result_ref text :=
        'synthetic.outcome.' || requested_attempt_id::text;
BEGIN
    IF requested_output_contract_hash !~ '^[a-f0-9]{64}$'
       OR requested_output_hash !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(requested_output_payload) <> 'object'
       OR pg_catalog.octet_length(requested_output_payload::text) > 1048576
       OR requested_used_evidence_refs IS NULL
       OR requested_model_run_refs IS NULL
       OR cardinality(requested_model_run_refs) < 1
       OR cardinality(requested_model_run_refs) > 64
       OR cardinality(requested_used_evidence_refs) > 256
       OR array_position(requested_model_run_refs, NULL) IS NOT NULL
       OR array_position(requested_used_evidence_refs, NULL) IS NOT NULL
       OR requested_validation_results IS NULL
       OR jsonb_typeof(requested_validation_results) <> 'array'
       OR jsonb_array_length(requested_validation_results) > 128
       OR pg_catalog.octet_length(requested_validation_results::text) > 32768
       OR requested_usage_requests < 0
       OR requested_usage_input_tokens < 0
       OR requested_usage_output_tokens < 0
       OR requested_usage_tool_calls < 0
       OR requested_at IS NULL THEN
        RAISE EXCEPTION 'synthetic semantic outcome contract is invalid'
            USING ERRCODE = '22023';
    END IF;
    IF cardinality(requested_model_run_refs) <> (
        SELECT count(DISTINCT reference_id)
        FROM unnest(requested_model_run_refs) AS requested(reference_id)
    ) OR cardinality(requested_used_evidence_refs) <> (
        SELECT count(DISTINCT reference_id)
        FROM unnest(requested_used_evidence_refs) AS requested(reference_id)
    ) THEN
        RAISE EXCEPTION 'synthetic semantic outcome references are duplicated'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR context_record.principal_kind <> 'service' THEN
        RETURN 'stale_fence';
    END IF;

    -- Lock the current task -> attempt fence before inspecting the success
    -- run tree.  This matches outcome/reaper lock order and makes a terminal
    -- attempt return stale_fence instead of failing success-shape validation.
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.target_kind = 'synthetic_receipt'
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'stale_fence';
    END IF;
    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status IN ('claimed', 'running')
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'stale_fence';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM unnest(requested_used_evidence_refs) AS used(reference_id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                task_record.input_payload #> '{evidence_manifest,references}'
            ) AS declared(reference)
            WHERE declared.reference ->> 'reference_id' = used.reference_id
        )
    ) THEN
        RAISE EXCEPTION 'synthetic semantic outcome widened evidence'
            USING ERRCODE = '22023';
    END IF;
    IF cardinality(requested_model_run_refs) <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.task_id = requested_task_id
          AND run.attempt_id = requested_attempt_id
          AND run.lease_generation = requested_lease_generation
          AND run.run_id = ANY(requested_model_run_refs)
    ) OR EXISTS (
        SELECT 1
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.task_id = requested_task_id
          AND run.attempt_id = requested_attempt_id
          AND run.lease_generation = requested_lease_generation
          AND run.parent_run_id IS NOT NULL
          AND NOT run.settled
    ) OR cardinality(requested_model_run_refs) <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.task_id = requested_task_id
          AND run.attempt_id = requested_attempt_id
          AND run.lease_generation = requested_lease_generation
    ) OR 1 <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.attempt_id = requested_attempt_id
          AND run.parent_run_id IS NULL
          AND run.run_status IN ('running', 'succeeded')
          AND run.run_id = ANY(requested_model_run_refs)
    ) THEN
        RAISE EXCEPTION 'synthetic semantic outcome run tree is incomplete'
            USING ERRCODE = '22023';
    END IF;

    outcome_status := memoriesql.record_semantic_task_outcome(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'succeeded', requested_output_hash,
        generated_result_ref, NULL, NULL, NULL, NULL, 0, requested_at
    );
    IF outcome_status <> 'succeeded' THEN
        IF outcome_status <> 'stale_fence' THEN
            UPDATE memoriesql.semantic_task_runs AS run
               SET run_status = 'failed',
                   started_at = COALESCE(
                       run.started_at, run.delegation_started_at
                   ),
                   finished_at = COALESCE(
                       run.finished_at, pg_catalog.clock_timestamp()
                   ),
                   delegation_status = CASE
                       WHEN run.run_role = 'delegate' THEN 'finished'
                       ELSE NULL
                   END,
                   delegation_finished_at = CASE
                       WHEN run.run_role = 'delegate' THEN COALESCE(
                           run.delegation_finished_at,
                           pg_catalog.clock_timestamp()
                       )
                       ELSE NULL
                   END,
                   settled = true
             WHERE run.tenant_id = requested_tenant_id
               AND run.task_id = requested_task_id
               AND run.attempt_id = requested_attempt_id
               AND run.lease_generation = requested_lease_generation
               AND NOT run.settled;
        END IF;
        RETURN outcome_status;
    END IF;

    -- SQL-01E intentionally emits no root run.succeeded event.  The accepted,
    -- fenced result is the sole success boundary for the root receipt.
    UPDATE memoriesql.semantic_task_runs AS run
       SET run_status = 'succeeded',
           finished_at = pg_catalog.clock_timestamp(),
           settled = true
     WHERE run.tenant_id = requested_tenant_id
       AND run.task_id = requested_task_id
       AND run.attempt_id = requested_attempt_id
       AND run.lease_generation = requested_lease_generation
       AND run.parent_run_id IS NULL
       AND run.run_status = 'running'
       AND run.run_id = ANY(requested_model_run_refs);
    IF 1 <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.attempt_id = requested_attempt_id
          AND run.parent_run_id IS NULL
          AND run.run_status = 'succeeded'
          AND run.settled
    ) OR EXISTS (
        SELECT 1
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = requested_tenant_id
          AND run.attempt_id = requested_attempt_id
          AND NOT run.settled
    ) THEN
        RAISE EXCEPTION 'synthetic semantic outcome run tree did not settle'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO memoriesql.semantic_synthetic_outcomes (
        tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
        lease_generation, result_ref, output_contract_hash, output_payload,
        output_hash, used_evidence_refs, model_run_refs, validation_results,
        usage_requests, usage_input_tokens, usage_output_tokens,
        usage_tool_calls, created_at
    ) VALUES (
        task_record.tenant_id, task_record.workspace_id,
        task_record.access_scope_id, task_record.task_id,
        requested_attempt_id, requested_lease_generation, generated_result_ref,
        requested_output_contract_hash, requested_output_payload,
        requested_output_hash, requested_used_evidence_refs,
        requested_model_run_refs, requested_validation_results,
        requested_usage_requests, requested_usage_input_tokens,
        requested_usage_output_tokens, requested_usage_tool_calls,
        pg_catalog.clock_timestamp()
    );
    RETURN outcome_status;
END;
$$;

CREATE FUNCTION memoriesql.record_integrated_semantic_task_failure(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_result_status text,
    requested_error_code text,
    requested_error_class text,
    requested_retry_class text,
    requested_retry_after_seconds integer,
    requested_jitter_basis_points integer,
    requested_at timestamp with time zone
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    outcome_status text;
    settled_at timestamp with time zone;
    database_now timestamp with time zone;
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
BEGIN
    IF requested_result_status = 'succeeded' THEN
        RAISE EXCEPTION 'integrated semantic failure cannot settle success'
            USING ERRCODE = '22023';
    END IF;
    outcome_status := 'stale_fence';
    IF (
           requested_result_status = 'budget_exhausted'
           AND requested_error_code = 'runtime.wall_clock_limit'
       OR requested_result_status = 'cancelled'
           AND requested_error_code = 'worker.cancelled'
       )
       AND requested_error_class IS NOT NULL
       AND length(requested_error_class) <= 128
       AND requested_error_class
           ~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
       AND requested_retry_class = 'never'
       AND requested_retry_after_seconds IS NULL
       AND requested_jitter_basis_points IS NOT NULL
       AND requested_jitter_basis_points BETWEEN 0 AND 2500
       AND requested_at IS NOT NULL THEN
        SELECT * INTO context_record
        FROM memoriesql.current_authorization_context();
        IF FOUND
           AND context_record.principal_kind = 'service'
           AND memoriesql.lock_semantic_task_outcome_authority(
               requested_tenant_id, requested_task_id
           ) THEN
            SELECT task.* INTO task_record
            FROM memoriesql.semantic_tasks AS task
            WHERE task.tenant_id = requested_tenant_id
              AND task.task_id = requested_task_id
              AND task.status = 'running'
              AND task.lease_generation = requested_lease_generation
              AND task.lease_owner = requested_worker_id
              AND task.worker_instance_id = requested_worker_instance_id
              AND task.result_attempt_id IS NULL
              AND task.cancel_requested_at IS NULL
              AND memoriesql.current_context_semantic_task_authorized(
                  task.task_id, 'memory.maintain', 'read'
              )
            FOR UPDATE;
            IF FOUND THEN
                SELECT attempt.* INTO attempt_record
                FROM memoriesql.semantic_task_attempts AS attempt
                WHERE attempt.tenant_id = requested_tenant_id
                  AND attempt.task_id = requested_task_id
                  AND attempt.attempt_id = requested_attempt_id
                  AND attempt.lease_generation = requested_lease_generation
                  AND attempt.claimant_principal_id = context_record.principal_id
                  AND attempt.worker_id = requested_worker_id
                  AND attempt.worker_instance_id = requested_worker_instance_id
                  AND attempt.status = 'running'
                FOR UPDATE;
                database_now := pg_catalog.clock_timestamp();
                IF FOUND
                   AND attempt_record.deadline_at <= database_now
                   AND task_record.lease_expires_at > database_now
                   AND attempt_record.lease_expires_at > database_now
                   AND task_record.lease_expires_at > attempt_record.deadline_at
                   AND attempt_record.lease_expires_at
                       > attempt_record.deadline_at
                   AND context_record.expires_at > database_now
                   AND requested_at >= database_now - interval '5 minutes'
                   AND requested_at <= database_now + interval '5 minutes'
                   AND memoriesql.current_context_scope_time_authorized(
                       task_record.access_scope_id, 'read', database_now
                   )
                   AND memoriesql.semantic_task_origin_authorized(
                       requested_tenant_id, requested_task_id, database_now
                   ) THEN
                    PERFORM memoriesql.append_semantic_task_event(
                        requested_tenant_id, requested_task_id,
                        requested_attempt_id,
                        CASE
                            WHEN requested_result_status = 'cancelled' THEN
                                'cancelled'
                            ELSE 'failed_terminal'
                        END,
                        context_record.principal_id, requested_worker_id,
                        requested_at,
                        jsonb_build_object(
                            'result_status', requested_result_status,
                            'retry_class', 'never',
                            'error_code', requested_error_code,
                            'lease_generation', requested_lease_generation
                        )
                    );
                    UPDATE memoriesql.semantic_task_attempts
                       SET status = CASE
                               WHEN requested_result_status = 'cancelled' THEN
                                   'cancelled'
                               ELSE 'terminal_failure'
                           END,
                           finished_at = database_now,
                           result_status = requested_result_status,
                           error_code = requested_error_code,
                           error_class = requested_error_class,
                           retry_class = 'never'
                     WHERE tenant_id = requested_tenant_id
                       AND task_id = requested_task_id
                       AND attempt_id = requested_attempt_id
                       AND lease_generation = requested_lease_generation
                       AND status = 'running';
                    PERFORM memoriesql.release_semantic_task_slot(
                        requested_tenant_id, requested_task_id,
                        requested_attempt_id, database_now
                    );
                    UPDATE memoriesql.semantic_tasks
                       SET status = CASE
                               WHEN requested_result_status = 'cancelled' THEN
                                   'cancelled'
                               ELSE 'failed_terminal'
                           END,
                           lease_owner = NULL,
                           worker_instance_id = NULL,
                           lease_expires_at = NULL,
                           heartbeat_at = NULL,
                           result_attempt_id = requested_attempt_id,
                           updated_at = GREATEST(updated_at, database_now),
                           completed_at = database_now
                     WHERE tenant_id = requested_tenant_id
                       AND task_id = requested_task_id;
                    outcome_status := CASE
                        WHEN requested_result_status = 'cancelled' THEN
                            'cancelled'
                        ELSE 'failed_terminal'
                    END;
                END IF;
            END IF;
        END IF;
    END IF;
    IF outcome_status = 'stale_fence' THEN
        outcome_status := memoriesql.record_semantic_task_outcome(
            requested_tenant_id, requested_task_id, requested_attempt_id,
            requested_lease_generation, requested_worker_id,
            requested_worker_instance_id, requested_result_status, NULL, NULL,
            requested_error_code, requested_error_class,
            requested_retry_class, requested_retry_after_seconds,
            requested_jitter_basis_points, requested_at
        );
    END IF;
    IF outcome_status = 'stale_fence' THEN
        RETURN outcome_status;
    END IF;
    settled_at := pg_catalog.clock_timestamp();
    UPDATE memoriesql.semantic_task_runs AS run
       SET run_status = 'failed',
           started_at = COALESCE(run.started_at, run.delegation_started_at),
           finished_at = COALESCE(run.finished_at, settled_at),
           delegation_status = CASE
               WHEN run.run_role = 'delegate' THEN 'finished'
               ELSE NULL
           END,
           delegation_finished_at = CASE
               WHEN run.run_role = 'delegate' THEN
                   COALESCE(run.delegation_finished_at, settled_at)
               ELSE NULL
           END,
           settled = true
     WHERE run.tenant_id = requested_tenant_id
       AND run.task_id = requested_task_id
       AND run.attempt_id = requested_attempt_id
       AND run.lease_generation = requested_lease_generation
       AND NOT run.settled;
    RETURN outcome_status;
END;
$$;

DROP VIEW memoriesql.semantic_task_run_tree;
CREATE VIEW memoriesql.semantic_task_run_tree
WITH (security_invoker = true)
AS
SELECT
    run.tenant_id,
    run.workspace_id,
    run.access_scope_id,
    run.task_id,
    run.attempt_id,
    run.run_id,
    run.parent_run_id,
    run.delegation_id,
    run.run_role,
    run.sibling_order,
    run.agent_key,
    run.input_contract_id,
    run.input_contract_revision,
    run.input_contract_hash,
    run.output_contract_id,
    run.output_contract_revision,
    run.output_contract_hash,
    run.model_profile_key,
    run.model_profile_revision,
    run.effort_key,
    run.run_status AS status,
    run.delegation_status,
    run.settled,
    run.started_at,
    run.finished_at,
    run.delegation_started_at,
    run.delegation_finished_at
FROM memoriesql.semantic_task_runs AS run;

CREATE VIEW memoriesql.semantic_async_operations
WITH (security_invoker = true)
AS
SELECT
    task.tenant_id,
    task.workspace_id,
    task.access_scope_id,
    task.task_id,
    task.owning_module,
    task.task_kind,
    task.contract_revision,
    task.queue_name,
    task.target_kind,
    task.status,
    task.pause_reason_code,
    task.attempt_count,
    task.result_attempt_id,
    task.result_ref,
    task.result_hash,
    task.cancel_requested_at,
    task.created_at,
    task.updated_at,
    task.started_at,
    task.completed_at,
    COALESCE(run_counts.run_count, 0) AS run_count,
    COALESCE(run_counts.settled_run_count, 0) AS settled_run_count,
    outcome.authority_class AS outcome_authority_class
FROM memoriesql.semantic_tasks AS task
LEFT JOIN LATERAL (
    SELECT
        count(*) AS run_count,
        count(*) FILTER (WHERE run.settled) AS settled_run_count
    FROM memoriesql.semantic_task_runs AS run
    WHERE run.tenant_id = task.tenant_id
      AND run.task_id = task.task_id
      AND run.attempt_id = COALESCE(
          task.result_attempt_id,
          (
              SELECT attempt.attempt_id
              FROM memoriesql.semantic_task_attempts AS attempt
              WHERE attempt.tenant_id = task.tenant_id
                AND attempt.task_id = task.task_id
              ORDER BY attempt.attempt_number DESC
              LIMIT 1
          )
      )
) AS run_counts ON true
LEFT JOIN memoriesql.semantic_synthetic_outcomes AS outcome
  ON outcome.tenant_id = task.tenant_id
 AND outcome.task_id = task.task_id;

CREATE POLICY semantic_task_runs_read
ON memoriesql.semantic_task_runs
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = semantic_task_runs.tenant_id
          AND context.workspace_id = semantic_task_runs.workspace_id
          AND memoriesql.current_context_scope_authorized(
              semantic_task_runs.access_scope_id, 'memory.maintain', 'read'
          )
    )
);

CREATE POLICY semantic_synthetic_outcomes_read
ON memoriesql.semantic_synthetic_outcomes
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = semantic_synthetic_outcomes.tenant_id
          AND context.workspace_id = semantic_synthetic_outcomes.workspace_id
          AND memoriesql.current_context_scope_authorized(
              semantic_synthetic_outcomes.access_scope_id,
              'memory.maintain', 'read'
          )
    )
);

ALTER TABLE memoriesql.semantic_task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_synthetic_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_synthetic_outcomes FORCE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA memoriesql FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memoriesql FROM PUBLIC;

GRANT SELECT ON memoriesql.semantic_task_runs,
    memoriesql.semantic_synthetic_outcomes,
    memoriesql.semantic_task_run_tree,
    memoriesql.semantic_async_operations
TO memoriesql_application, memoriesql_worker;

GRANT EXECUTE ON FUNCTION memoriesql.record_semantic_run_event(
    uuid, uuid, uuid, bigint, text, text, text, text, text, text, text,
    text, integer, text, text, integer, text, text, integer, text, text,
    timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.semantic_task_authorization_snapshot(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.retain_semantic_task_cleanup_lease(
    uuid, uuid, uuid, bigint, text, text, integer, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.record_synthetic_semantic_task_outcome(
    uuid, uuid, uuid, bigint, text, text, text, jsonb, text, text[], text[],
    jsonb, integer, integer, integer, integer, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.record_integrated_semantic_task_failure(
    uuid, uuid, uuid, bigint, text, text, text, text, text, text, integer,
    integer, timestamp with time zone
) TO memoriesql_worker;

COMMENT ON TABLE memoriesql.semantic_task_runs IS
    'Generation-fenced, incrementally settled run and delegation receipts; bounded identifiers only, without prompts, evidence, output, or provider prose.';
COMMENT ON TABLE memoriesql.semantic_synthetic_outcomes IS
    'Typed PR-01F synthetic outputs, atomically settled with their task and explicitly non-authoritative; not canonical semantic state.';
COMMENT ON FUNCTION memoriesql.settle_semantic_task_runs_from_attempt() IS
    'Closes still-open run receipts when the owning SQL-01D attempt reaches a non-success terminal state, including lease-loss recovery.';
COMMENT ON VIEW memoriesql.semantic_task_run_tree IS
    'Actual depth-one executor run tree for each durable semantic task attempt.';
COMMENT ON VIEW memoriesql.semantic_async_operations IS
    'Minimal canonical operational state for semantic task, attempt-run, cancellation, and non-authoritative outcome visibility.';
COMMENT ON FUNCTION memoriesql.record_semantic_run_event(
    uuid, uuid, uuid, bigint, text, text, text, text, text, text, text,
    text, integer, text, text, integer, text, text, integer, text, text,
    timestamp with time zone
) IS
    'Appends one typed executor lifecycle boundary only while the current authenticated attempt fence remains valid.';
COMMENT ON FUNCTION memoriesql.semantic_task_authorization_snapshot(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) IS
    'Returns only the current origin/delegation/policy/scope tuple after exact attempt-fence reauthorization.';
COMMENT ON FUNCTION memoriesql.retain_semantic_task_cleanup_lease(
    uuid, uuid, uuid, bigint, text, text, integer, timestamp with time zone
) IS
    'Keeps one still-owned task, attempt, and concurrency slot fenced while cancelled provider work remains attached; it cannot revive or transfer a lease.';
COMMENT ON FUNCTION memoriesql.record_synthetic_semantic_task_outcome(
    uuid, uuid, uuid, bigint, text, text, text, jsonb, text, text[], text[],
    jsonb, integer, integer, integer, integer, timestamp with time zone
) IS
    'Atomically settles one complete run tree and its typed synthetic non-authoritative output; late results produce no output row.';
COMMENT ON FUNCTION memoriesql.record_integrated_semantic_task_failure(
    uuid, uuid, uuid, bigint, text, text, text, text, text, text, integer,
    integer, timestamp with time zone
) IS
    'Atomically settles a non-success attempt and closes only its still-open operational run receipts; no authored output is retained.';
