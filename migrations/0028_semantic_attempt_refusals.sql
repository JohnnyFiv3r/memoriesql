-- Forward-only record of why canonical apply refused an attempt's authored output.
-- Restore the pre-upgrade backup to roll back. No historical SQL/contract edits or inference.

-- A refused canonical apply rolls back entirely, and the worker then settles the
-- attempt as invalid output with the code worker.canonical_apply_refused. The
-- database's reason, such as "bead type revision is unavailable", used to be lost.
-- The worker now records the refusal's SQLSTATE and primary message once per
-- attempt, while it still holds the attempt's lease and before it settles it.
CREATE TABLE memoriesql.semantic_attempt_refusals (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    sqlstate text NOT NULL CHECK (sqlstate ~ '^[0-9A-Z]{5}$'),
    message text NOT NULL CHECK (
        char_length(message) BETWEEN 1 AND 512 AND message !~ '[[:cntrl:]]'
    ),
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, attempt_id),
    FOREIGN KEY (tenant_id, attempt_id)
        REFERENCES memoriesql.semantic_task_attempts (tenant_id, attempt_id)
);
CREATE TRIGGER semantic_attempt_refusals_immutable BEFORE UPDATE OR DELETE ON memoriesql.semantic_attempt_refusals
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
ALTER TABLE memoriesql.semantic_attempt_refusals ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_attempt_refusals FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.semantic_attempt_refusals FROM PUBLIC;

-- Record one refusal for the caller's own running attempt. The fence, the lease,
-- deadline and context-expiry checks and the task -> attempt lock order match
-- settlement's, so a worker whose lease lapsed records nothing. A second record for
-- the same attempt changes nothing.
CREATE FUNCTION memoriesql.record_semantic_attempt_refusal(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_sqlstate text,
    requested_message text,
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
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    IF requested_sqlstate IS NULL
       OR requested_sqlstate !~ '^[0-9A-Z]{5}$'
       OR requested_message IS NULL
       OR char_length(requested_message) NOT BETWEEN 1 AND 512
       OR requested_message ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'semantic attempt refusal is invalid'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
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
      AND task.result_attempt_id IS NULL
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
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR attempt_record.deadline_at <= database_now
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       ) THEN
        RETURN false;
    END IF;
    INSERT INTO memoriesql.semantic_attempt_refusals (
        tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
        sqlstate, message, recorded_at
    ) VALUES (
        requested_tenant_id, task_record.workspace_id, task_record.access_scope_id,
        requested_task_id, requested_attempt_id, requested_sqlstate,
        requested_message, pg_catalog.clock_timestamp()
    ) ON CONFLICT (tenant_id, attempt_id) DO NOTHING;
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.record_semantic_attempt_refusal(
    uuid, uuid, uuid, bigint, text, text, text, text, timestamp with time zone
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_semantic_attempt_refusal(
    uuid, uuid, uuid, bigint, text, text, text, text, timestamp with time zone
) TO memoriesql_worker;

COMMENT ON TABLE memoriesql.semantic_attempt_refusals IS
    'Why canonical apply refused an attempt''s authored output: the SQLSTATE and primary message, recorded once by the leased worker before settlement.';
