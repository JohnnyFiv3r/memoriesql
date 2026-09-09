CREATE TABLE memoriesql.semantic_task_admission_policies (
    semantic_registry_hash text NOT NULL,
    task_kind text NOT NULL,
    contract_revision integer NOT NULL,
    owning_module text NOT NULL,
    task_contract_hash text NOT NULL,
    target_kind text NOT NULL,
    required_capability text NOT NULL,
    queue_name text NOT NULL,
    base_priority integer NOT NULL,
    max_attempts integer NOT NULL,
    concurrency_key text,
    concurrency_limit integer,
    CONSTRAINT semantic_task_admission_policies_pk PRIMARY KEY (
        semantic_registry_hash, task_kind, contract_revision
    ),
    CONSTRAINT semantic_task_admission_policies_exact_uq UNIQUE (
        semantic_registry_hash, task_kind, contract_revision,
        owning_module, task_contract_hash, target_kind, required_capability,
        queue_name, base_priority, max_attempts
    ),
    CONSTRAINT semantic_task_admission_policies_queue_uq UNIQUE (
        semantic_registry_hash, task_kind, contract_revision, queue_name
    ),
    CONSTRAINT semantic_task_admission_registry_hash_nonempty CHECK (
        btrim(semantic_registry_hash) <> ''
    ),
    CONSTRAINT semantic_task_admission_kind_nonempty CHECK (
        btrim(task_kind) <> ''
    ),
    CONSTRAINT semantic_task_admission_revision_positive CHECK (
        contract_revision > 0
    ),
    CONSTRAINT semantic_task_admission_module_nonempty CHECK (
        btrim(owning_module) <> ''
    ),
    CONSTRAINT semantic_task_admission_contract_hash_shape CHECK (
        task_contract_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_task_admission_target_kind_nonempty CHECK (
        btrim(target_kind) <> ''
    ),
    CONSTRAINT semantic_task_admission_capability_nonempty CHECK (
        btrim(required_capability) <> ''
    ),
    CONSTRAINT semantic_task_admission_queue_supported CHECK (
        queue_name IN ('interactive', 'capture', 'continuity')
    ),
    CONSTRAINT semantic_task_admission_attempt_limit_positive CHECK (
        max_attempts > 0
    ),
    CONSTRAINT semantic_task_admission_concurrency_shape CHECK (
        (concurrency_key IS NULL AND concurrency_limit IS NULL)
        OR (
            concurrency_key IS NOT NULL
            AND btrim(concurrency_key) <> ''
            AND concurrency_limit IS NOT NULL
            AND concurrency_limit > 0
            AND concurrency_limit <= 1024
        )
    )
);

CREATE TABLE memoriesql.semantic_worker_claim_policies (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    pairing_grant_id uuid NOT NULL,
    semantic_registry_hash text NOT NULL,
    task_kind text NOT NULL,
    contract_revision integer NOT NULL,
    queue_name text NOT NULL,
    CONSTRAINT semantic_worker_claim_policies_pk PRIMARY KEY (
        tenant_id, workspace_id, principal_id, pairing_grant_id,
        semantic_registry_hash, task_kind, contract_revision, queue_name
    ),
    CONSTRAINT semantic_worker_claim_policies_membership_fk FOREIGN KEY (
        tenant_id, workspace_id, principal_id
    ) REFERENCES memoriesql.workspace_memberships (
        tenant_id, workspace_id, principal_id
    ),
    CONSTRAINT semantic_worker_claim_policies_pairing_fk FOREIGN KEY (
        tenant_id, workspace_id, pairing_grant_id
    ) REFERENCES memoriesql.pairing_grants (
        tenant_id, workspace_id, pairing_grant_id
    ),
    CONSTRAINT semantic_worker_claim_policies_admission_fk FOREIGN KEY (
        semantic_registry_hash, task_kind, contract_revision, queue_name
    ) REFERENCES memoriesql.semantic_task_admission_policies (
        semantic_registry_hash, task_kind, contract_revision, queue_name
    ),
    CONSTRAINT semantic_worker_claim_policies_queue_supported CHECK (
        queue_name IN ('interactive', 'capture', 'continuity')
    )
);

CREATE FUNCTION memoriesql.semantic_retry_class_for_status(
    requested_result_status text
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    CASE requested_result_status
        WHEN 'succeeded' THEN RETURN NULL;
        WHEN 'unavailable' THEN RETURN 'transient';
        WHEN 'budget_exhausted' THEN RETURN 'never';
        WHEN 'cancelled' THEN RETURN 'never';
        WHEN 'invalid_output' THEN RETURN 'never';
        WHEN 'stale_input' THEN RETURN 'stale';
        WHEN 'policy_paused' THEN RETURN 'policy';
        WHEN 'failed' THEN RETURN 'never';
        ELSE
            RAISE EXCEPTION 'unsupported semantic result status: %',
                requested_result_status
                USING ERRCODE = '22023';
    END CASE;
END;
$$;

CREATE TABLE memoriesql.semantic_tasks (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    owning_module text NOT NULL,
    task_kind text NOT NULL,
    contract_revision integer NOT NULL,
    task_contract_hash text NOT NULL,
    semantic_registry_hash text NOT NULL,
    target_kind text NOT NULL,
    target_reference text NOT NULL,
    expected_target_revision integer NOT NULL,
    input_payload jsonb NOT NULL,
    input_hash text NOT NULL,
    evidence_manifest_id text NOT NULL,
    evidence_manifest_hash text NOT NULL,
    origin_principal_id uuid NOT NULL,
    origin_pairing_grant_id uuid,
    required_capability text NOT NULL,
    accepted_policy_revision_id uuid NOT NULL,
    queue_name text NOT NULL,
    base_priority integer NOT NULL,
    available_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    pause_reason_code text,
    attempt_count integer NOT NULL,
    max_attempts integer NOT NULL,
    lease_owner text,
    worker_instance_id text,
    lease_generation bigint NOT NULL,
    lease_expires_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    cancel_requested_at timestamp with time zone,
    cancel_requested_by uuid,
    cancel_reason text,
    result_attempt_id uuid,
    result_ref text,
    result_hash text,
    rerun_of_task_id uuid,
    superseded_by_task_id uuid,
    concurrency_key text,
    concurrency_limit integer,
    last_event_sequence bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    CONSTRAINT semantic_tasks_pk PRIMARY KEY (tenant_id, task_id),
    CONSTRAINT semantic_tasks_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, task_id
    ),
    CONSTRAINT semantic_tasks_idempotency_uq UNIQUE (
        tenant_id, idempotency_receipt_id
    ),
    CONSTRAINT semantic_tasks_idempotency_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT semantic_tasks_scope_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id
    ) REFERENCES memoriesql.access_scopes (
        tenant_id, workspace_id, access_scope_id
    ),
    CONSTRAINT semantic_tasks_policy_revision_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, accepted_policy_revision_id
    ) REFERENCES memoriesql.access_policy_revisions (
        tenant_id, workspace_id, access_scope_id, policy_revision_id
    ),
    CONSTRAINT semantic_tasks_origin_principal_fk FOREIGN KEY (
        tenant_id, origin_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT semantic_tasks_origin_pairing_fk FOREIGN KEY (
        tenant_id, workspace_id, origin_pairing_grant_id
    ) REFERENCES memoriesql.pairing_grants (
        tenant_id, workspace_id, pairing_grant_id
    ),
    CONSTRAINT semantic_tasks_admission_policy_fk FOREIGN KEY (
        semantic_registry_hash, task_kind, contract_revision,
        owning_module, task_contract_hash, target_kind, required_capability,
        queue_name, base_priority, max_attempts
    ) REFERENCES memoriesql.semantic_task_admission_policies (
        semantic_registry_hash, task_kind, contract_revision,
        owning_module, task_contract_hash, target_kind, required_capability,
        queue_name, base_priority, max_attempts
    ),
    CONSTRAINT semantic_tasks_rerun_fk FOREIGN KEY (
        tenant_id, rerun_of_task_id
    ) REFERENCES memoriesql.semantic_tasks (tenant_id, task_id),
    CONSTRAINT semantic_tasks_superseded_fk FOREIGN KEY (
        tenant_id, superseded_by_task_id
    ) REFERENCES memoriesql.semantic_tasks (tenant_id, task_id),
    CONSTRAINT semantic_tasks_module_nonempty CHECK (btrim(owning_module) <> ''),
    CONSTRAINT semantic_tasks_kind_nonempty CHECK (btrim(task_kind) <> ''),
    CONSTRAINT semantic_tasks_contract_revision_positive CHECK (contract_revision > 0),
    CONSTRAINT semantic_tasks_contract_hash_shape CHECK (
        task_contract_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_tasks_registry_hash_nonempty CHECK (
        btrim(semantic_registry_hash) <> ''
    ),
    CONSTRAINT semantic_tasks_target_kind_nonempty CHECK (btrim(target_kind) <> ''),
    CONSTRAINT semantic_tasks_target_reference_nonempty CHECK (
        btrim(target_reference) <> ''
    ),
    CONSTRAINT semantic_tasks_target_revision_nonnegative CHECK (
        expected_target_revision >= 0
    ),
    CONSTRAINT semantic_tasks_input_object CHECK (jsonb_typeof(input_payload) = 'object'),
    CONSTRAINT semantic_tasks_input_hash_shape CHECK (input_hash ~ '^[a-f0-9]{64}$'),
    CONSTRAINT semantic_tasks_manifest_id_nonempty CHECK (
        btrim(evidence_manifest_id) <> ''
    ),
    CONSTRAINT semantic_tasks_manifest_hash_shape CHECK (
        evidence_manifest_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_tasks_capability_nonempty CHECK (
        btrim(required_capability) <> ''
    ),
    CONSTRAINT semantic_tasks_queue_supported CHECK (
        queue_name IN ('interactive', 'capture', 'continuity')
    ),
    CONSTRAINT semantic_tasks_status_supported CHECK (
        status IN (
            'queued', 'running', 'retry_wait', 'policy_paused', 'succeeded',
            'cancelled', 'failed_terminal', 'dead_letter', 'superseded'
        )
    ),
    CONSTRAINT semantic_tasks_pause_shape CHECK (
        (status = 'policy_paused' AND pause_reason_code IS NOT NULL
            AND length(pause_reason_code) <= 128
            AND pause_reason_code
                ~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$')
        OR (status <> 'policy_paused' AND pause_reason_code IS NULL)
    ),
    CONSTRAINT semantic_tasks_attempt_count_nonnegative CHECK (attempt_count >= 0),
    CONSTRAINT semantic_tasks_attempt_limit_positive CHECK (max_attempts > 0),
    CONSTRAINT semantic_tasks_attempt_limit_respected CHECK (
        attempt_count <= max_attempts
    ),
    CONSTRAINT semantic_tasks_lease_generation_nonnegative CHECK (
        lease_generation >= 0
    ),
    CONSTRAINT semantic_tasks_lease_shape CHECK (
        (
            status = 'running'
            AND lease_owner IS NOT NULL
            AND worker_instance_id IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND heartbeat_at IS NOT NULL
            AND result_attempt_id IS NULL
        )
        OR (
            status <> 'running'
            AND lease_owner IS NULL
            AND worker_instance_id IS NULL
            AND lease_expires_at IS NULL
            AND heartbeat_at IS NULL
        )
    ),
    CONSTRAINT semantic_tasks_lease_owner_nonempty CHECK (
        lease_owner IS NULL OR btrim(lease_owner) <> ''
    ),
    CONSTRAINT semantic_tasks_worker_instance_nonempty CHECK (
        worker_instance_id IS NULL OR btrim(worker_instance_id) <> ''
    ),
    CONSTRAINT semantic_tasks_cancel_shape CHECK (
        (
            cancel_requested_at IS NULL
            AND cancel_requested_by IS NULL
            AND cancel_reason IS NULL
        ) OR (
            cancel_requested_at IS NOT NULL
            AND cancel_requested_by IS NOT NULL
            AND cancel_reason IS NOT NULL
            AND length(cancel_reason) <= 128
            AND cancel_reason ~
                '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
        )
    ),
    CONSTRAINT semantic_tasks_result_hash_shape CHECK (
        result_hash IS NULL OR result_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_tasks_result_ref_operational CHECK (
        result_ref IS NULL OR (
            status = 'succeeded'
            AND length(result_ref) <= 512
            AND result_ref
                ~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
        )
    ),
    CONSTRAINT semantic_tasks_concurrency_shape CHECK (
        (concurrency_key IS NULL AND concurrency_limit IS NULL)
        OR (
            concurrency_key IS NOT NULL
            AND btrim(concurrency_key) <> ''
            AND concurrency_limit IS NOT NULL
            AND concurrency_limit > 0
            AND concurrency_limit <= 1024
        )
    ),
    CONSTRAINT semantic_tasks_event_sequence_positive CHECK (last_event_sequence > 0),
    CONSTRAINT semantic_tasks_time_order CHECK (
        updated_at >= created_at
        AND available_at >= created_at
        AND (started_at IS NULL OR started_at >= created_at)
        AND (completed_at IS NULL OR completed_at >= created_at)
    ),
    CONSTRAINT semantic_tasks_terminal_completion CHECK (
        (
            status IN (
                'succeeded', 'cancelled', 'failed_terminal',
                'dead_letter', 'superseded'
            )
            AND completed_at IS NOT NULL
        ) OR (
            status IN ('queued', 'running', 'retry_wait', 'policy_paused')
            AND completed_at IS NULL
        )
    ),
    CONSTRAINT semantic_tasks_result_shape CHECK (
        (status = 'succeeded' AND result_attempt_id IS NOT NULL AND result_hash IS NOT NULL)
        OR (status <> 'succeeded' AND result_hash IS NULL)
    ),
    CONSTRAINT semantic_tasks_uuidv7 CHECK (
        pg_catalog.uuid_extract_version(task_id) = 7
    )
);

CREATE TABLE memoriesql.semantic_task_attempts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    attempt_number integer NOT NULL,
    lease_generation bigint NOT NULL,
    claimant_principal_id uuid NOT NULL,
    worker_id text NOT NULL,
    worker_instance_id text NOT NULL,
    status text NOT NULL,
    executor_contract_version integer NOT NULL,
    task_contract_revision integer NOT NULL,
    task_contract_hash text NOT NULL,
    input_hash text NOT NULL,
    evidence_manifest_hash text NOT NULL,
    authorization_policy_revision_id uuid NOT NULL,
    claimed_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    heartbeat_at timestamp with time zone NOT NULL,
    lease_expires_at timestamp with time zone NOT NULL,
    deadline_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    result_status text,
    result_hash text,
    result_ref text,
    error_code text,
    error_class text,
    retry_class text,
    retry_after timestamp with time zone,
    CONSTRAINT semantic_task_attempts_pk PRIMARY KEY (tenant_id, attempt_id),
    CONSTRAINT semantic_task_attempts_task_number_uq UNIQUE (
        tenant_id, task_id, attempt_number
    ),
    CONSTRAINT semantic_task_attempts_task_generation_uq UNIQUE (
        tenant_id, task_id, lease_generation
    ),
    CONSTRAINT semantic_task_attempts_task_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, task_id
    ) REFERENCES memoriesql.semantic_tasks (
        tenant_id, workspace_id, access_scope_id, task_id
    ),
    CONSTRAINT semantic_task_attempts_claimant_principal_fk FOREIGN KEY (
        tenant_id, claimant_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT semantic_task_attempts_policy_revision_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        authorization_policy_revision_id
    ) REFERENCES memoriesql.access_policy_revisions (
        tenant_id, workspace_id, access_scope_id, policy_revision_id
    ),
    CONSTRAINT semantic_task_attempts_number_positive CHECK (attempt_number > 0),
    CONSTRAINT semantic_task_attempts_generation_positive CHECK (lease_generation > 0),
    CONSTRAINT semantic_task_attempts_worker_nonempty CHECK (btrim(worker_id) <> ''),
    CONSTRAINT semantic_task_attempts_instance_nonempty CHECK (
        btrim(worker_instance_id) <> ''
    ),
    CONSTRAINT semantic_task_attempts_status_supported CHECK (
        status IN (
            'claimed', 'running', 'succeeded', 'retryable_failure',
            'terminal_failure', 'cancelled', 'lease_lost'
        )
    ),
    CONSTRAINT semantic_task_attempts_executor_version_positive CHECK (
        executor_contract_version > 0
    ),
    CONSTRAINT semantic_task_attempts_contract_revision_positive CHECK (
        task_contract_revision > 0
    ),
    CONSTRAINT semantic_task_attempts_contract_hash_shape CHECK (
        task_contract_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_task_attempts_input_hash_shape CHECK (
        input_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_task_attempts_manifest_hash_shape CHECK (
        evidence_manifest_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_task_attempts_result_hash_shape CHECK (
        result_hash IS NULL OR result_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT semantic_task_attempts_result_ref_operational CHECK (
        result_ref IS NULL OR (
            status = 'succeeded'
            AND length(result_ref) <= 512
            AND result_ref
                ~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
        )
    ),
    CONSTRAINT semantic_task_attempts_error_code_operational CHECK (
        error_code IS NULL OR (
            length(error_code) <= 128
            AND error_code
                ~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
        )
    ),
    CONSTRAINT semantic_task_attempts_error_class_operational CHECK (
        error_class IS NULL OR (
            length(error_class) <= 128
            AND error_class
                ~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
        )
    ),
    CONSTRAINT semantic_task_attempts_retry_supported CHECK (
        retry_class IS NULL OR retry_class IN ('never', 'transient', 'policy', 'stale')
    ),
    CONSTRAINT semantic_task_attempts_result_retry_exact CHECK (
        (result_status IS NULL AND retry_class IS NULL)
        OR retry_class IS NOT DISTINCT FROM
            memoriesql.semantic_retry_class_for_status(result_status)
    ),
    CONSTRAINT semantic_task_attempts_time_order CHECK (
        heartbeat_at >= claimed_at
        AND lease_expires_at > heartbeat_at
        AND deadline_at >= claimed_at
        AND (started_at IS NULL OR started_at >= claimed_at)
        AND (finished_at IS NULL OR finished_at >= claimed_at)
        AND (retry_after IS NULL OR retry_after >= claimed_at)
    ),
    CONSTRAINT semantic_task_attempts_terminal_shape CHECK (
        (
            status IN ('claimed', 'running')
            AND finished_at IS NULL
            AND result_status IS NULL
            AND result_hash IS NULL
            AND error_code IS NULL
            AND error_class IS NULL
            AND retry_class IS NULL
            AND retry_after IS NULL
        ) OR (
            status = 'succeeded'
            AND result_status = 'succeeded'
            AND finished_at IS NOT NULL
            AND result_hash IS NOT NULL
            AND error_code IS NULL
            AND error_class IS NULL
            AND retry_class IS NULL
            AND retry_after IS NULL
        ) OR (
            status IN (
                'retryable_failure', 'terminal_failure', 'cancelled', 'lease_lost'
            )
            AND finished_at IS NOT NULL
            AND result_status IS NOT NULL
            AND result_status <> 'succeeded'
            AND result_hash IS NULL
            AND error_code IS NOT NULL
            AND retry_class IS NOT NULL
        )
    ),
    CONSTRAINT semantic_task_attempts_uuidv7 CHECK (
        pg_catalog.uuid_extract_version(attempt_id) = 7
    )
);

CREATE TABLE memoriesql.semantic_task_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    event_sequence bigint NOT NULL,
    event_id uuid NOT NULL,
    attempt_id uuid,
    event_kind text NOT NULL,
    actor_principal_id uuid,
    worker_id text,
    occurred_at timestamp with time zone NOT NULL,
    metadata jsonb NOT NULL,
    CONSTRAINT semantic_task_events_pk PRIMARY KEY (
        tenant_id, task_id, event_sequence
    ),
    CONSTRAINT semantic_task_events_id_uq UNIQUE (tenant_id, event_id),
    CONSTRAINT semantic_task_events_task_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, task_id
    ) REFERENCES memoriesql.semantic_tasks (
        tenant_id, workspace_id, access_scope_id, task_id
    ),
    CONSTRAINT semantic_task_events_attempt_fk FOREIGN KEY (
        tenant_id, attempt_id
    ) REFERENCES memoriesql.semantic_task_attempts (tenant_id, attempt_id),
    CONSTRAINT semantic_task_events_actor_fk FOREIGN KEY (
        tenant_id, actor_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT semantic_task_events_sequence_positive CHECK (event_sequence > 0),
    CONSTRAINT semantic_task_events_kind_nonempty CHECK (btrim(event_kind) <> ''),
    CONSTRAINT semantic_task_events_worker_nonempty CHECK (
        worker_id IS NULL OR btrim(worker_id) <> ''
    ),
    CONSTRAINT semantic_task_events_metadata_object CHECK (
        jsonb_typeof(metadata) = 'object'
    ),
    CONSTRAINT semantic_task_events_uuidv7 CHECK (
        pg_catalog.uuid_extract_version(event_id) = 7
    )
);

CREATE TABLE memoriesql.semantic_concurrency_slots (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    concurrency_key text NOT NULL,
    slot_number integer NOT NULL,
    task_id uuid,
    attempt_id uuid,
    lease_generation bigint,
    lease_owner text,
    worker_instance_id text,
    lease_expires_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT semantic_concurrency_slots_pk PRIMARY KEY (
        tenant_id, workspace_id, concurrency_key, slot_number
    ),
    CONSTRAINT semantic_concurrency_slots_workspace_fk FOREIGN KEY (
        tenant_id, workspace_id
    ) REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    CONSTRAINT semantic_concurrency_slots_attempt_fk FOREIGN KEY (
        tenant_id, attempt_id
    ) REFERENCES memoriesql.semantic_task_attempts (tenant_id, attempt_id),
    CONSTRAINT semantic_concurrency_slots_key_nonempty CHECK (
        btrim(concurrency_key) <> ''
    ),
    CONSTRAINT semantic_concurrency_slots_number_positive CHECK (slot_number > 0),
    CONSTRAINT semantic_concurrency_slots_lease_shape CHECK (
        (
            task_id IS NULL
            AND attempt_id IS NULL
            AND lease_generation IS NULL
            AND lease_owner IS NULL
            AND worker_instance_id IS NULL
            AND lease_expires_at IS NULL
        ) OR (
            task_id IS NOT NULL
            AND attempt_id IS NOT NULL
            AND lease_generation IS NOT NULL
            AND lease_owner IS NOT NULL
            AND btrim(lease_owner) <> ''
            AND worker_instance_id IS NOT NULL
            AND btrim(worker_instance_id) <> ''
            AND lease_expires_at IS NOT NULL
        )
    )
);

CREATE TABLE memoriesql.semantic_module_queue_controls (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    owning_module text NOT NULL,
    state text NOT NULL,
    revision integer NOT NULL,
    reason_code text NOT NULL,
    changed_by_principal_id uuid NOT NULL,
    changed_at timestamp with time zone NOT NULL,
    CONSTRAINT semantic_module_queue_controls_pk PRIMARY KEY (
        tenant_id, workspace_id, owning_module
    ),
    CONSTRAINT semantic_module_queue_controls_workspace_fk FOREIGN KEY (
        tenant_id, workspace_id
    ) REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    CONSTRAINT semantic_module_queue_controls_actor_fk FOREIGN KEY (
        tenant_id, changed_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT semantic_module_queue_controls_module_nonempty CHECK (
        btrim(owning_module) <> ''
    ),
    CONSTRAINT semantic_module_queue_controls_state_supported CHECK (
        state IN ('enabled', 'draining', 'disabled')
    ),
    CONSTRAINT semantic_module_queue_controls_revision_positive CHECK (revision > 0),
    CONSTRAINT semantic_module_queue_controls_reason_code CHECK (
        length(reason_code) <= 128
        AND reason_code ~
            '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
    )
);

CREATE FUNCTION memoriesql.semantic_queue_json_safe(
    candidate jsonb,
    maximum_bytes integer
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    item record;
BEGIN
    IF candidate IS NULL
       OR maximum_bytes <= 0
       OR octet_length(candidate::text) > maximum_bytes THEN
        RETURN false;
    END IF;

    IF jsonb_typeof(candidate) = 'object' THEN
        FOR item IN SELECT key, value FROM jsonb_each(candidate)
        LOOP
            IF lower(item.key) IN (
                'body', 'content', 'source_text', 'normalized_text',
                'transcript', 'document_text', 'media_text', 'prompt',
                'system_prompt', 'model_output', 'chain_of_thought', 'prose'
            ) THEN
                RETURN false;
            END IF;
            IF NOT memoriesql.semantic_queue_json_safe(item.value, maximum_bytes) THEN
                RETURN false;
            END IF;
        END LOOP;
    ELSIF jsonb_typeof(candidate) = 'array' THEN
        FOR item IN SELECT value FROM jsonb_array_elements(candidate)
        LOOP
            IF NOT memoriesql.semantic_queue_json_safe(item.value, maximum_bytes) THEN
                RETURN false;
            END IF;
        END LOOP;
    ELSIF jsonb_typeof(candidate) = 'string'
          AND length(candidate #>> '{}') > 512 THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.semantic_queue_reference_text_safe(
    candidate text,
    maximum_characters integer
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT candidate IS NOT NULL
       AND maximum_characters > 0
       AND length(candidate) BETWEEN 1 AND maximum_characters
       AND candidate ~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$'
$$;

CREATE FUNCTION memoriesql.semantic_queue_payload_reference_scalar_safe(
    candidate jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    item record;
    element record;
    value_kind text;
BEGIN
    IF jsonb_typeof(candidate) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(candidate)) > 32 THEN
        RETURN false;
    END IF;

    FOR item IN SELECT key, value FROM jsonb_each(candidate)
    LOOP
        IF item.key !~ '^[a-z][a-z0-9_]{0,63}$' THEN
            RETURN false;
        END IF;
        value_kind := jsonb_typeof(item.value);
        IF value_kind IN ('null', 'boolean', 'number') THEN
            CONTINUE;
        ELSIF value_kind = 'string' THEN
            IF item.key !~ '(^|_)(id|key|kind|type|status|mode|code|reference|hash|outcome)$'
               OR NOT memoriesql.semantic_queue_reference_text_safe(
                   item.value #>> '{}', 128
               ) THEN
                RETURN false;
            END IF;
        ELSIF value_kind = 'array' THEN
            IF item.key !~ '(^|_)(ids|keys|kinds|types|statuses|modes|codes|references|hashes|outcomes)$'
               OR jsonb_array_length(item.value) > 64 THEN
                RETURN false;
            END IF;
            FOR element IN SELECT value FROM jsonb_array_elements(item.value)
            LOOP
                value_kind := jsonb_typeof(element.value);
                IF value_kind IN ('null', 'boolean', 'number') THEN
                    CONTINUE;
                END IF;
                IF value_kind <> 'string'
                   OR NOT memoriesql.semantic_queue_reference_text_safe(
                       element.value #>> '{}', 128
                   ) THEN
                    RETURN false;
                END IF;
            END LOOP;
        ELSE
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.semantic_task_input_reference_safe(
    candidate jsonb,
    maximum_bytes integer
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    manifest jsonb;
    evidence_references jsonb;
    reference_item jsonb;
    budget jsonb;
    budget_item record;
BEGIN
    IF candidate IS NULL
       OR maximum_bytes <= 0
       OR octet_length(candidate::text) > maximum_bytes
       OR jsonb_typeof(candidate) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(candidate)) <> 9
       OR EXISTS (
           SELECT 1
           FROM jsonb_object_keys(candidate) AS input_key(key)
           WHERE input_key.key <> ALL (ARRAY[
               'task_id', 'task_kind', 'contract_revision',
               'target_reference', 'expected_target_revision',
               'evidence_manifest', 'requested_effort_key',
               'requested_budget', 'payload'
           ]::text[])
       ) THEN
        RETURN false;
    END IF;

    IF NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'task_id', 36
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'task_kind', 128
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'target_reference', 512
       )
       OR jsonb_typeof(candidate -> 'contract_revision') <> 'number'
       OR candidate ->> 'contract_revision' !~ '^[0-9]+$'
       OR (candidate ->> 'contract_revision')::numeric < 1
       OR jsonb_typeof(candidate -> 'expected_target_revision') <> 'number'
       OR candidate ->> 'expected_target_revision' !~ '^[0-9]+$'
       OR jsonb_typeof(candidate -> 'requested_effort_key')
            NOT IN ('null', 'string')
       OR (
           jsonb_typeof(candidate -> 'requested_effort_key') = 'string'
           AND NOT memoriesql.semantic_queue_reference_text_safe(
               candidate ->> 'requested_effort_key', 128
           )
       ) THEN
        RETURN false;
    END IF;

    manifest := candidate -> 'evidence_manifest';
    IF jsonb_typeof(manifest) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(manifest)) <> 3
       OR EXISTS (
           SELECT 1
           FROM jsonb_object_keys(manifest) AS manifest_key(key)
           WHERE manifest_key.key <> ALL (
               ARRAY['manifest_id', 'revision', 'references']::text[]
           )
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           manifest ->> 'manifest_id', 512
       )
       OR jsonb_typeof(manifest -> 'revision') <> 'number'
       OR manifest ->> 'revision' !~ '^[0-9]+$'
       OR (manifest ->> 'revision')::numeric < 1
       OR jsonb_typeof(manifest -> 'references') <> 'array'
       OR jsonb_array_length(manifest -> 'references') > 256 THEN
        RETURN false;
    END IF;
    evidence_references := manifest -> 'references';
    FOR reference_item IN
        SELECT value FROM jsonb_array_elements(evidence_references)
    LOOP
        IF jsonb_typeof(reference_item) <> 'object' THEN
            RETURN false;
        END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(reference_item)) <> 3
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(reference_item) AS reference_key(key)
               WHERE reference_key.key <> ALL (
                   ARRAY[
                       'reference_id', 'content_hash', 'declared_characters'
                   ]::text[]
               )
           )
           OR NOT memoriesql.semantic_queue_reference_text_safe(
               reference_item ->> 'reference_id', 512
           )
           OR reference_item ->> 'content_hash' !~ '^[a-f0-9]{64}$'
           OR jsonb_typeof(reference_item -> 'declared_characters') <> 'number'
           OR reference_item ->> 'declared_characters' !~ '^[0-9]+$' THEN
            RETURN false;
        END IF;
    END LOOP;
    IF (
        SELECT count(*) <> count(DISTINCT item ->> 'reference_id')
        FROM jsonb_array_elements(evidence_references) AS item
    ) THEN
        RETURN false;
    END IF;

    budget := candidate -> 'requested_budget';
    IF jsonb_typeof(budget) NOT IN ('null', 'object') THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(budget) = 'object' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(budget)) <> 13
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(budget) AS budget_key(key)
               WHERE budget_key.key <> ALL (ARRAY[
                   'wall_clock_seconds', 'request_limit',
                   'input_token_limit', 'output_token_limit',
                   'total_token_limit', 'tool_call_limit',
                   'cost_safety_limit_microusd', 'max_delegate_calls',
                   'max_parallel_delegates', 'evidence_item_limit',
                   'hydrated_character_limit', 'output_retries', 'tool_retries'
               ]::text[])
           ) THEN
            RETURN false;
        END IF;
        FOR budget_item IN SELECT key, value FROM jsonb_each(budget)
        LOOP
            IF budget_item.key = 'cost_safety_limit_microusd'
               AND jsonb_typeof(budget_item.value) = 'null' THEN
                CONTINUE;
            END IF;
            IF jsonb_typeof(budget_item.value) <> 'number'
               OR budget_item.value #>> '{}' !~ '^[0-9]+$' THEN
                RETURN false;
            END IF;
        END LOOP;
        IF (budget ->> 'wall_clock_seconds')::numeric < 1
           OR (budget ->> 'request_limit')::numeric < 1
           OR (budget ->> 'input_token_limit')::numeric < 1
           OR (budget ->> 'output_token_limit')::numeric < 1
           OR (budget ->> 'total_token_limit')::numeric < 1
           OR (budget ->> 'evidence_item_limit')::numeric < 1
           OR (budget ->> 'hydrated_character_limit')::numeric < 1
           OR (budget ->> 'total_token_limit')::numeric
                < (budget ->> 'input_token_limit')::numeric
           OR (budget ->> 'total_token_limit')::numeric
                < (budget ->> 'output_token_limit')::numeric
           OR (budget ->> 'max_parallel_delegates')::numeric
                > (budget ->> 'max_delegate_calls')::numeric THEN
            RETURN false;
        END IF;
    END IF;

    RETURN memoriesql.semantic_queue_payload_reference_scalar_safe(
        candidate -> 'payload'
    );
END;
$$;

ALTER TABLE memoriesql.semantic_tasks
ADD CONSTRAINT semantic_tasks_input_safe CHECK (
    memoriesql.semantic_task_input_reference_safe(input_payload, 32768)
);
ALTER TABLE memoriesql.semantic_task_events
ADD CONSTRAINT semantic_task_events_metadata_safe CHECK (
    memoriesql.semantic_queue_json_safe(metadata, 4096)
);

CREATE FUNCTION memoriesql.protect_semantic_task_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        OLD.tenant_id, OLD.workspace_id, OLD.access_scope_id, OLD.task_id,
        OLD.idempotency_receipt_id, OLD.owning_module, OLD.task_kind,
        OLD.contract_revision, OLD.task_contract_hash,
        OLD.semantic_registry_hash, OLD.target_kind, OLD.target_reference,
        OLD.expected_target_revision, OLD.input_payload, OLD.input_hash,
        OLD.evidence_manifest_id, OLD.evidence_manifest_hash,
        OLD.origin_principal_id, OLD.origin_pairing_grant_id,
        OLD.required_capability, OLD.accepted_policy_revision_id,
        OLD.queue_name, OLD.base_priority, OLD.max_attempts,
        OLD.rerun_of_task_id, OLD.concurrency_key, OLD.concurrency_limit,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.tenant_id, NEW.workspace_id, NEW.access_scope_id, NEW.task_id,
        NEW.idempotency_receipt_id, NEW.owning_module, NEW.task_kind,
        NEW.contract_revision, NEW.task_contract_hash,
        NEW.semantic_registry_hash, NEW.target_kind, NEW.target_reference,
        NEW.expected_target_revision, NEW.input_payload, NEW.input_hash,
        NEW.evidence_manifest_id, NEW.evidence_manifest_hash,
        NEW.origin_principal_id, NEW.origin_pairing_grant_id,
        NEW.required_capability, NEW.accepted_policy_revision_id,
        NEW.queue_name, NEW.base_priority, NEW.max_attempts,
        NEW.rerun_of_task_id, NEW.concurrency_key, NEW.concurrency_limit,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'semantic task identity and input are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.attempt_count < OLD.attempt_count
       OR NEW.lease_generation < OLD.lease_generation
       OR NEW.last_event_sequence < OLD.last_event_sequence
       OR NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'semantic task operational progress cannot move backward'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'semantic task start time is immutable once recorded'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.completed_at IS NOT NULL
       AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
        RAISE EXCEPTION 'semantic task completion time is immutable once recorded'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.status <> NEW.status AND NOT (
        (OLD.status = 'queued' AND NEW.status IN (
            'running', 'cancelled', 'superseded', 'policy_paused'
        ))
        OR (OLD.status = 'running' AND NEW.status IN (
            'succeeded', 'retry_wait', 'policy_paused', 'cancelled',
            'failed_terminal', 'dead_letter', 'superseded'
        ))
        OR (OLD.status = 'retry_wait' AND NEW.status IN (
            'queued', 'cancelled', 'superseded', 'policy_paused'
        ))
        OR (OLD.status = 'policy_paused' AND NEW.status IN (
            'queued', 'cancelled', 'failed_terminal'
        ))
    ) THEN
        RAISE EXCEPTION 'unsupported semantic task transition % -> %',
            OLD.status, NEW.status
            USING ERRCODE = '23514';
    END IF;

    IF OLD.status IN (
        'succeeded', 'cancelled', 'failed_terminal', 'dead_letter', 'superseded'
    ) AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal semantic task state is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.require_semantic_task_admission_schedule()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    PERFORM 1
    FROM memoriesql.semantic_task_admission_policies AS policy
    WHERE policy.semantic_registry_hash = NEW.semantic_registry_hash
      AND policy.task_kind = NEW.task_kind
      AND policy.contract_revision = NEW.contract_revision
      AND policy.owning_module = NEW.owning_module
      AND policy.task_contract_hash = NEW.task_contract_hash
      AND policy.target_kind = NEW.target_kind
      AND policy.required_capability = NEW.required_capability
      AND policy.queue_name = NEW.queue_name
      AND policy.base_priority = NEW.base_priority
      AND policy.max_attempts = NEW.max_attempts
      AND policy.concurrency_key IS NOT DISTINCT FROM NEW.concurrency_key
      AND policy.concurrency_limit IS NOT DISTINCT FROM NEW.concurrency_limit;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'semantic task scheduling policy is unavailable'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.protect_semantic_task_attempt()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        OLD.tenant_id, OLD.workspace_id, OLD.access_scope_id, OLD.task_id,
        OLD.attempt_id, OLD.attempt_number, OLD.lease_generation,
        OLD.claimant_principal_id, OLD.worker_id, OLD.worker_instance_id,
        OLD.executor_contract_version,
        OLD.task_contract_revision, OLD.task_contract_hash, OLD.input_hash,
        OLD.evidence_manifest_hash, OLD.authorization_policy_revision_id,
        OLD.claimed_at, OLD.deadline_at
    ) IS DISTINCT FROM ROW(
        NEW.tenant_id, NEW.workspace_id, NEW.access_scope_id, NEW.task_id,
        NEW.attempt_id, NEW.attempt_number, NEW.lease_generation,
        NEW.claimant_principal_id, NEW.worker_id, NEW.worker_instance_id,
        NEW.executor_contract_version,
        NEW.task_contract_revision, NEW.task_contract_hash, NEW.input_hash,
        NEW.evidence_manifest_hash, NEW.authorization_policy_revision_id,
        NEW.claimed_at, NEW.deadline_at
    ) THEN
        RAISE EXCEPTION 'semantic task attempt identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.status IN (
        'succeeded', 'retryable_failure', 'terminal_failure',
        'cancelled', 'lease_lost'
    ) AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal semantic task attempt is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.status <> NEW.status AND NOT (
        (OLD.status = 'claimed' AND NEW.status IN (
            'running', 'succeeded', 'retryable_failure', 'terminal_failure',
            'cancelled', 'lease_lost'
        ))
        OR (OLD.status = 'running' AND NEW.status IN (
            'succeeded', 'retryable_failure', 'terminal_failure',
            'cancelled', 'lease_lost'
        ))
    ) THEN
        RAISE EXCEPTION 'unsupported semantic attempt transition % -> %',
            OLD.status, NEW.status
            USING ERRCODE = '23514';
    END IF;
    IF NEW.heartbeat_at < OLD.heartbeat_at
       OR NEW.lease_expires_at < OLD.lease_expires_at THEN
        RAISE EXCEPTION 'semantic task attempt lease cannot move backward'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER semantic_tasks_protect_state
BEFORE UPDATE ON memoriesql.semantic_tasks
FOR EACH ROW EXECUTE FUNCTION memoriesql.protect_semantic_task_state();
CREATE TRIGGER semantic_tasks_require_admission_schedule
BEFORE INSERT ON memoriesql.semantic_tasks
FOR EACH ROW EXECUTE FUNCTION memoriesql.require_semantic_task_admission_schedule();
CREATE TRIGGER semantic_tasks_no_delete
BEFORE DELETE ON memoriesql.semantic_tasks
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_task_attempts_protect_state
BEFORE UPDATE ON memoriesql.semantic_task_attempts
FOR EACH ROW EXECUTE FUNCTION memoriesql.protect_semantic_task_attempt();
CREATE TRIGGER semantic_task_attempts_no_delete
BEFORE DELETE ON memoriesql.semantic_task_attempts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_task_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.semantic_task_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_module_controls_no_delete
BEFORE DELETE ON memoriesql.semantic_module_queue_controls
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_task_admission_policies_immutable
BEFORE UPDATE OR DELETE ON memoriesql.semantic_task_admission_policies
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_worker_claim_policies_immutable
BEFORE UPDATE OR DELETE ON memoriesql.semantic_worker_claim_policies
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.semantic_task_origin_authorized(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_checked_at timestamp with time zone
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.semantic_tasks AS task
        JOIN memoriesql.principals AS principal
          ON principal.tenant_id = task.tenant_id
         AND principal.principal_id = task.origin_principal_id
         AND principal.status = 'active'
        JOIN memoriesql.users AS origin_user
          ON origin_user.tenant_id = principal.tenant_id
         AND origin_user.user_id = principal.owner_user_id
         AND origin_user.status = 'active'
        JOIN memoriesql.workspace_memberships AS membership
          ON membership.tenant_id = task.tenant_id
         AND membership.workspace_id = task.workspace_id
         AND membership.principal_id = task.origin_principal_id
         AND membership.status = 'active'
        JOIN memoriesql.role_capabilities AS role_capability
          ON role_capability.role_key = membership.role_key
         AND role_capability.capability_key = task.required_capability
        JOIN memoriesql.access_scopes AS scope
          ON scope.tenant_id = task.tenant_id
         AND scope.workspace_id = task.workspace_id
         AND scope.access_scope_id = task.access_scope_id
         AND scope.status = 'active'
        WHERE requested_checked_at IS NOT NULL
          AND task.tenant_id = requested_tenant_id
          AND task.task_id = requested_task_id
          AND (
              task.origin_pairing_grant_id IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM memoriesql.pairing_grants AS origin_pairing
                  JOIN memoriesql.pairing_grant_revisions AS origin_revision
                    ON origin_revision.tenant_id = origin_pairing.tenant_id
                   AND origin_revision.workspace_id = origin_pairing.workspace_id
                   AND origin_revision.pairing_grant_id =
                       origin_pairing.pairing_grant_id
                  WHERE origin_pairing.tenant_id = task.tenant_id
                    AND origin_pairing.workspace_id = task.workspace_id
                    AND origin_pairing.pairing_grant_id =
                        task.origin_pairing_grant_id
                    AND origin_pairing.paired_principal_id =
                        task.origin_principal_id
                    AND origin_revision.revision = (
                        SELECT max(latest.revision)
                        FROM memoriesql.pairing_grant_revisions AS latest
                        WHERE latest.tenant_id = origin_pairing.tenant_id
                          AND latest.pairing_grant_id =
                              origin_pairing.pairing_grant_id
                    )
                    AND origin_revision.status = 'active'
                    AND origin_revision.expires_at > requested_checked_at
                    AND task.required_capability =
                        ANY(origin_revision.allowed_capabilities)
                    AND task.access_scope_id =
                        ANY(origin_revision.allowed_access_scope_ids)
              )
          )
          AND (
              (
                  scope.mode = 'owner_private'
                  AND (
                      (
                          principal.principal_kind = 'human'
                          AND principal.user_id = scope.owner_user_id
                          AND task.origin_pairing_grant_id IS NULL
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM memoriesql.pairing_grants AS pairing
                          JOIN memoriesql.pairing_grant_revisions AS revision
                            ON revision.tenant_id = pairing.tenant_id
                           AND revision.workspace_id = pairing.workspace_id
                           AND revision.pairing_grant_id = pairing.pairing_grant_id
                          WHERE pairing.tenant_id = task.tenant_id
                            AND pairing.workspace_id = task.workspace_id
                            AND pairing.pairing_grant_id = task.origin_pairing_grant_id
                            AND pairing.paired_principal_id = task.origin_principal_id
                            AND pairing.on_behalf_of_user_id = scope.owner_user_id
                            AND revision.revision = (
                                SELECT max(latest.revision)
                                FROM memoriesql.pairing_grant_revisions AS latest
                                WHERE latest.tenant_id = pairing.tenant_id
                                  AND latest.pairing_grant_id = pairing.pairing_grant_id
                            )
                            AND revision.status = 'active'
                            AND revision.expires_at > requested_checked_at
                            AND task.required_capability = ANY(revision.allowed_capabilities)
                            AND task.access_scope_id = ANY(revision.allowed_access_scope_ids)
                      )
                  )
              )
              OR (
                  scope.mode = 'explicit'
                  AND EXISTS (
                      SELECT 1
                      FROM memoriesql.access_grants AS grant_record
                      JOIN memoriesql.access_grant_revisions AS revision
                        ON revision.tenant_id = grant_record.tenant_id
                       AND revision.workspace_id = grant_record.workspace_id
                       AND revision.access_scope_id = grant_record.access_scope_id
                       AND revision.grant_id = grant_record.grant_id
                      WHERE grant_record.tenant_id = task.tenant_id
                        AND grant_record.workspace_id = task.workspace_id
                        AND grant_record.access_scope_id = task.access_scope_id
                        AND grant_record.target_principal_id = task.origin_principal_id
                        AND revision.revision = (
                            SELECT max(latest.revision)
                            FROM memoriesql.access_grant_revisions AS latest
                            WHERE latest.tenant_id = revision.tenant_id
                              AND latest.grant_id = revision.grant_id
                        )
                        AND revision.status = 'active'
                        AND revision.valid_from <= requested_checked_at
                        AND (
                            revision.expires_at IS NULL
                            OR revision.expires_at > requested_checked_at
                        )
                        AND 'read' = ANY(revision.permission_keys)
                  )
              )
              OR scope.mode = 'workspace'
          )
    )
$$;

CREATE FUNCTION memoriesql.semantic_task_origin_authorized(
    requested_tenant_id uuid,
    requested_task_id uuid
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT memoriesql.semantic_task_origin_authorized(
        requested_tenant_id,
        requested_task_id,
        pg_catalog.clock_timestamp()
    )
$$;

CREATE FUNCTION memoriesql.current_context_semantic_task_authorized(
    requested_task_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.semantic_tasks AS task
          ON task.tenant_id = context.tenant_id
         AND task.workspace_id = context.workspace_id
         AND task.task_id = requested_task_id
        WHERE memoriesql.current_context_scope_authorized(
            task.access_scope_id,
            requested_capability,
            requested_permission
        )
    )
$$;

CREATE FUNCTION memoriesql.current_context_scope_time_authorized(
    requested_access_scope_id uuid,
    requested_permission text,
    requested_checked_at timestamp with time zone
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT requested_checked_at IS NOT NULL
       AND EXISTS (
            SELECT 1
            FROM memoriesql.authorization_contexts AS context
            JOIN memoriesql.access_scopes AS scope
              ON scope.tenant_id = context.tenant_id
             AND scope.workspace_id = context.workspace_id
             AND scope.access_scope_id = requested_access_scope_id
             AND scope.status = 'active'
            WHERE context.context_id::text = pg_catalog.current_setting(
                      'memoriesql.authorization_context_id', true
                  )
              AND context.backend_pid = pg_catalog.pg_backend_pid()
              AND context.transaction_id = pg_catalog.txid_current()
              AND context.expires_at > requested_checked_at
              AND (
                  scope.mode <> 'explicit'
                  OR EXISTS (
                      SELECT 1
                      FROM memoriesql.access_grants AS grant_record
                      JOIN memoriesql.access_grant_revisions AS revision
                        ON revision.tenant_id = grant_record.tenant_id
                       AND revision.workspace_id = grant_record.workspace_id
                       AND revision.access_scope_id = grant_record.access_scope_id
                       AND revision.grant_id = grant_record.grant_id
                      WHERE grant_record.tenant_id = context.tenant_id
                        AND grant_record.workspace_id = context.workspace_id
                        AND grant_record.access_scope_id = requested_access_scope_id
                        AND grant_record.target_principal_id = context.principal_id
                        AND revision.revision = (
                            SELECT max(latest.revision)
                            FROM memoriesql.access_grant_revisions AS latest
                            WHERE latest.tenant_id = revision.tenant_id
                              AND latest.grant_id = revision.grant_id
                        )
                        AND revision.status = 'active'
                        AND revision.valid_from <= requested_checked_at
                        AND (
                            revision.expires_at IS NULL
                            OR revision.expires_at > requested_checked_at
                        )
                        AND requested_permission = ANY(revision.permission_keys)
                  )
              )
       )
$$;

CREATE FUNCTION memoriesql.current_context_semantic_task_origin_authorized(
    requested_task_id uuid,
    requested_checked_at timestamp with time zone
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        WHERE requested_checked_at IS NOT NULL
          AND memoriesql.current_context_semantic_task_authorized(
              requested_task_id, 'memory.maintain', 'read'
          )
          AND memoriesql.semantic_task_origin_authorized(
              context.tenant_id, requested_task_id, requested_checked_at
          )
    )
$$;

CREATE FUNCTION memoriesql.lock_semantic_task_outcome_authority(
    requested_tenant_id uuid,
    requested_task_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR context_record.tenant_id <> requested_tenant_id THEN
        RETURN false;
    END IF;
    PERFORM 1
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.workspace_id = context_record.workspace_id
      AND task.task_id = requested_task_id;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    -- Every authority mutation covered by the triggers below takes this key
    -- exclusively. Outcome settlement and expired-task recovery hold it shared
    -- through their final reauthorization and all durable writes, giving
    -- revocation only two serial positions: before the final check or after the
    -- queue transition commits.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(
            requested_tenant_id::text || ':semantic_outcome_authority:',
            0
        )
    );
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    affected_tenant_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        affected_tenant_id := OLD.tenant_id;
    ELSE
        affected_tenant_id := NEW.tenant_id;
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            affected_tenant_id::text || ':semantic_outcome_authority:',
            0
        )
    );
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER authentication_credentials_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.authentication_credentials
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER users_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.users
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER principals_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.principals
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER workspaces_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.workspaces
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER workspace_memberships_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.workspace_memberships
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER access_scopes_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.access_scopes
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER access_grants_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.access_grants
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER access_grant_revisions_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.access_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER pairing_grants_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.pairing_grants
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER pairing_grant_revisions_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.pairing_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER protected_resources_outcome_fence
BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.protected_resources
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();

CREATE FUNCTION memoriesql.append_semantic_task_event(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_event_kind text,
    requested_actor_principal_id uuid,
    requested_worker_id text,
    requested_occurred_at timestamp with time zone,
    requested_metadata jsonb
)
RETURNS bigint
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    task_record memoriesql.semantic_tasks%ROWTYPE;
    next_sequence bigint;
BEGIN
    IF btrim(requested_event_kind) = ''
       OR NOT memoriesql.semantic_queue_json_safe(requested_metadata, 4096) THEN
        RAISE EXCEPTION 'semantic task event metadata is invalid'
            USING ERRCODE = '22023';
    END IF;
    UPDATE memoriesql.semantic_tasks
       SET last_event_sequence = last_event_sequence + 1,
           updated_at = GREATEST(
               updated_at,
               pg_catalog.statement_timestamp()
           )
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id
     RETURNING * INTO task_record;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'semantic task % is unavailable', requested_task_id
            USING ERRCODE = '42501';
    END IF;
    next_sequence := task_record.last_event_sequence;
    INSERT INTO memoriesql.semantic_task_events (
        tenant_id, workspace_id, access_scope_id, task_id,
        event_sequence, event_id, attempt_id, event_kind,
        actor_principal_id, worker_id, occurred_at, metadata
    ) VALUES (
        task_record.tenant_id, task_record.workspace_id,
        task_record.access_scope_id, task_record.task_id,
        next_sequence, pg_catalog.uuidv7(), requested_attempt_id,
        requested_event_kind, requested_actor_principal_id,
        requested_worker_id, requested_occurred_at, requested_metadata
    );
    RETURN next_sequence;
END;
$$;

CREATE FUNCTION memoriesql.enqueue_semantic_task(
    requested_task_id uuid,
    requested_idempotency_key text,
    requested_owning_module text,
    requested_task_kind text,
    requested_contract_revision integer,
    requested_task_contract_hash text,
    requested_semantic_registry_hash text,
    requested_target_reference text,
    requested_expected_target_revision integer,
    requested_input_payload jsonb,
    requested_evidence_manifest_id text,
    requested_access_scope_id uuid,
    requested_available_at timestamp with time zone,
    requested_rerun_of_task_id uuid,
    requested_at timestamp with time zone
)
RETURNS TABLE (
    task_id uuid,
    idempotency_receipt_id uuid,
    input_hash text,
    replayed boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    admission_policy memoriesql.semantic_task_admission_policies%ROWTYPE;
    scope_record memoriesql.access_scopes%ROWTYPE;
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    existing_task memoriesql.semantic_tasks%ROWTYPE;
    computed_input_hash text;
    computed_evidence_manifest_hash text;
    computed_request_hash text;
    new_receipt_id uuid;
    module_state text;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'authenticated authorization context is required'
            USING ERRCODE = '42501';
    END IF;
    IF pg_catalog.uuid_extract_version(requested_task_id) <> 7 THEN
        RAISE EXCEPTION 'semantic task ID must be UUIDv7'
            USING ERRCODE = '22023';
    END IF;
    IF btrim(requested_idempotency_key) = ''
       OR btrim(requested_owning_module) = ''
       OR btrim(requested_task_kind) = ''
       OR requested_contract_revision <= 0
       OR requested_task_contract_hash !~ '^[a-f0-9]{64}$'
       OR btrim(requested_semantic_registry_hash) = ''
       OR btrim(requested_target_reference) = ''
       OR requested_expected_target_revision < 0
       OR NOT memoriesql.semantic_task_input_reference_safe(
            requested_input_payload, 32768
          )
       OR requested_input_payload ->> 'task_id' <> requested_task_id::text
       OR requested_input_payload ->> 'task_kind' <> requested_task_kind
       OR COALESCE((requested_input_payload ->> 'contract_revision')::integer, 0)
            <> requested_contract_revision
       OR requested_input_payload ->> 'target_reference'
            IS DISTINCT FROM requested_target_reference
       OR COALESCE(
            (requested_input_payload ->> 'expected_target_revision')::integer,
            -1
          ) <> requested_expected_target_revision
       OR btrim(requested_evidence_manifest_id) = ''
       OR pg_catalog.jsonb_typeof(
            requested_input_payload -> 'evidence_manifest'
          ) IS DISTINCT FROM 'object'
       OR requested_input_payload #>> '{evidence_manifest,manifest_id}'
            IS DISTINCT FROM requested_evidence_manifest_id
       OR requested_available_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'semantic task enqueue contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    SELECT policy.* INTO admission_policy
    FROM memoriesql.semantic_task_admission_policies AS policy
    WHERE policy.semantic_registry_hash = requested_semantic_registry_hash
      AND policy.task_kind = requested_task_kind
      AND policy.contract_revision = requested_contract_revision
      AND policy.owning_module = requested_owning_module
      AND policy.task_contract_hash = requested_task_contract_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'semantic task enqueue policy is unavailable'
            USING ERRCODE = '42501';
    END IF;

    IF requested_rerun_of_task_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM memoriesql.semantic_tasks AS prior
        WHERE prior.tenant_id = context_record.tenant_id
          AND prior.workspace_id = context_record.workspace_id
          AND prior.access_scope_id = requested_access_scope_id
          AND prior.task_id = requested_rerun_of_task_id
          AND prior.task_kind = requested_task_kind
          AND (
              prior.status IN (
                  'succeeded', 'cancelled', 'failed_terminal',
                  'dead_letter', 'superseded'
              )
              OR (
                  prior.status = 'policy_paused'
                  AND prior.attempt_count >= prior.max_attempts
              )
          )
    ) THEN
        RAISE EXCEPTION 'rerun source task is unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO scope_record
    FROM memoriesql.access_scopes AS scope
    WHERE scope.tenant_id = context_record.tenant_id
      AND scope.workspace_id = context_record.workspace_id
      AND scope.access_scope_id = requested_access_scope_id
      AND scope.status = 'active'
      AND scope.current_policy_revision_id IS NOT NULL;
    IF NOT FOUND OR NOT memoriesql.current_context_scope_authorized(
        requested_access_scope_id, admission_policy.required_capability, 'read'
    ) THEN
        RAISE EXCEPTION 'semantic task enqueue resource is unavailable'
            USING ERRCODE = '42501';
    END IF;

    computed_input_hash := encode(
        pg_catalog.sha256(pg_catalog.convert_to(requested_input_payload::text, 'UTF8')),
        'hex'
    );
    computed_evidence_manifest_hash := encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                (requested_input_payload -> 'evidence_manifest')::text,
                'UTF8'
            )
        ),
        'hex'
    );
    computed_request_hash := encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                jsonb_build_object(
                    'owning_module', requested_owning_module,
                    'task_kind', requested_task_kind,
                    'contract_revision', requested_contract_revision,
                    'task_contract_hash', requested_task_contract_hash,
                    'semantic_registry_hash', requested_semantic_registry_hash,
                    'target_kind', admission_policy.target_kind,
                    'target_reference', requested_target_reference,
                    'expected_target_revision', requested_expected_target_revision,
                    'input_hash', computed_input_hash,
                    'evidence_manifest_id', requested_evidence_manifest_id,
                    'evidence_manifest_hash', computed_evidence_manifest_hash,
                    'required_capability', admission_policy.required_capability,
                    'access_scope_id', requested_access_scope_id,
                    'queue_name', admission_policy.queue_name,
                    'base_priority', admission_policy.base_priority,
                    'available_at', requested_available_at,
                    'max_attempts', admission_policy.max_attempts,
                    'concurrency_key', admission_policy.concurrency_key,
                    'concurrency_limit', admission_policy.concurrency_limit,
                    'rerun_of_task_id', requested_rerun_of_task_id
                )::text,
                'UTF8'
            )
        ),
        'hex'
    );

    -- Serialize one tenant-scoped operation/key before inspecting the shared
    -- receipt. A concurrent first enqueue must wait and replay the committed
    -- receipt instead of racing the unique constraint.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            context_record.tenant_id::text
                || ':semantic_task.enqueue:'
                || requested_idempotency_key,
            0
        )
    );

    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_scope_authorized(
           requested_access_scope_id, admission_policy.required_capability, 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           requested_access_scope_id, 'read', database_now
       ) THEN
        RAISE EXCEPTION 'semantic task enqueue resource is unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO receipt_record
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = context_record.tenant_id
      AND receipt.operation_kind = 'semantic_task.enqueue'
      AND receipt.idempotency_key = requested_idempotency_key
    FOR UPDATE;

    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_scope_authorized(
           requested_access_scope_id, admission_policy.required_capability, 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           requested_access_scope_id, 'read', database_now
       ) THEN
        RAISE EXCEPTION 'semantic task enqueue resource is unavailable'
            USING ERRCODE = '42501';
    END IF;
    IF FOUND THEN
        IF receipt_record.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict'
                USING ERRCODE = '23505';
        END IF;
        SELECT * INTO existing_task
        FROM memoriesql.semantic_tasks AS task_record
        WHERE task_record.tenant_id = context_record.tenant_id
          AND task_record.idempotency_receipt_id =
              receipt_record.idempotency_receipt_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'idempotency receipt has no semantic task'
                USING ERRCODE = '55000';
        END IF;
        RETURN QUERY SELECT
            existing_task.task_id,
            receipt_record.idempotency_receipt_id,
            existing_task.input_hash,
            true;
        RETURN;
    END IF;

    -- The requested schedule is part of the idempotency fingerprint. An equal
    -- replay may legitimately carry the original schedule after it has passed;
    -- only genuinely new work must not request availability before its audit
    -- time.
    IF requested_available_at < requested_at THEN
        RAISE EXCEPTION 'semantic task enqueue contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    -- A committed equal-input receipt replays even after module admission
    -- closes. Only genuinely new work shares this module fence while state
    -- transitions take it exclusively. This also serializes the
    -- implicit-enabled (absent row) case without another control authority.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(
            context_record.tenant_id::text
                || ':' || context_record.workspace_id::text
                || ':semantic_module:' || requested_owning_module,
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    SELECT * INTO scope_record
    FROM memoriesql.access_scopes AS scope
    WHERE scope.tenant_id = context_record.tenant_id
      AND scope.workspace_id = context_record.workspace_id
      AND scope.access_scope_id = requested_access_scope_id
      AND scope.status = 'active'
      AND scope.current_policy_revision_id IS NOT NULL;
    IF NOT FOUND
       OR context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_scope_authorized(
           requested_access_scope_id, admission_policy.required_capability, 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           requested_access_scope_id, 'read', database_now
       ) THEN
        RAISE EXCEPTION 'semantic task enqueue resource is unavailable'
            USING ERRCODE = '42501';
    END IF;
    SELECT control.state INTO module_state
    FROM memoriesql.semantic_module_queue_controls AS control
    WHERE control.tenant_id = context_record.tenant_id
      AND control.workspace_id = context_record.workspace_id
      AND control.owning_module = requested_owning_module;
    IF COALESCE(module_state, 'enabled') <> 'enabled' THEN
        RAISE EXCEPTION 'semantic task module is not accepting work'
            USING ERRCODE = '55000';
    END IF;

    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, response_receipt, attempt_count,
        lease_owner, lease_expires_at, created_at, updated_at, completed_at
    ) VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_access_scope_id, new_receipt_id, 'semantic_task.enqueue',
        requested_idempotency_key, computed_request_hash, 'in_progress',
        NULL, NULL, NULL, 1, NULL, NULL, database_now, database_now, NULL
    );

    INSERT INTO memoriesql.semantic_tasks (
        tenant_id, workspace_id, access_scope_id, task_id,
        idempotency_receipt_id, owning_module, task_kind, contract_revision,
        task_contract_hash, semantic_registry_hash, target_kind,
        target_reference, expected_target_revision, input_payload, input_hash,
        evidence_manifest_id, evidence_manifest_hash, origin_principal_id,
        origin_pairing_grant_id, required_capability,
        accepted_policy_revision_id, queue_name, base_priority, available_at,
        status, attempt_count, max_attempts, lease_generation,
        concurrency_key, concurrency_limit, rerun_of_task_id,
        last_event_sequence,
        created_at, updated_at
    ) VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_access_scope_id, requested_task_id, new_receipt_id,
        requested_owning_module, requested_task_kind, requested_contract_revision,
        requested_task_contract_hash, requested_semantic_registry_hash,
        admission_policy.target_kind, requested_target_reference,
        requested_expected_target_revision, requested_input_payload,
        computed_input_hash, requested_evidence_manifest_id,
        computed_evidence_manifest_hash, context_record.principal_id,
        context_record.pairing_grant_id, admission_policy.required_capability,
        scope_record.current_policy_revision_id, admission_policy.queue_name,
        admission_policy.base_priority,
        GREATEST(requested_available_at, database_now), 'queued', 0,
        admission_policy.max_attempts, 0, admission_policy.concurrency_key,
        admission_policy.concurrency_limit, requested_rerun_of_task_id,
        1, database_now, database_now
    );
    IF admission_policy.concurrency_key IS NOT NULL THEN
        INSERT INTO memoriesql.semantic_concurrency_slots (
            tenant_id, workspace_id, concurrency_key, slot_number, updated_at
        )
        SELECT
            context_record.tenant_id, context_record.workspace_id,
            admission_policy.concurrency_key, generated.slot_number, database_now
        FROM generate_series(
            1, admission_policy.concurrency_limit
        ) AS generated(slot_number)
        ON CONFLICT DO NOTHING;
    END IF;

    INSERT INTO memoriesql.semantic_task_events (
        tenant_id, workspace_id, access_scope_id, task_id,
        event_sequence, event_id, attempt_id, event_kind,
        actor_principal_id, worker_id, occurred_at, metadata
    ) VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_access_scope_id, requested_task_id, 1, pg_catalog.uuidv7(),
        NULL, 'enqueued', context_record.principal_id, NULL, requested_at,
        jsonb_build_object(
            'queue_name', admission_policy.queue_name,
            'task_kind', requested_task_kind,
            'contract_revision', requested_contract_revision
        )
    );
    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id,
        idempotency_receipt_id, aggregate_kind, aggregate_id, event_kind,
        payload, headers, recorded_at, available_at
    ) VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_access_scope_id, pg_catalog.uuidv7(), new_receipt_id,
        'semantic_task', requested_task_id, 'semantic_task.enqueued',
        jsonb_build_object('task_id', requested_task_id),
        jsonb_build_object('queue_name', admission_policy.queue_name),
        database_now, database_now
    );
    PERFORM pg_catalog.pg_notify('memoriesql_semantic_tasks', requested_task_id::text);

    UPDATE memoriesql.idempotency_receipts AS receipt
       SET status = 'succeeded',
           response_receipt = jsonb_build_object(
               'task_id', requested_task_id,
               'input_hash', computed_input_hash
           ),
           updated_at = database_now,
           completed_at = database_now
     WHERE receipt.tenant_id = context_record.tenant_id
       AND receipt.idempotency_receipt_id = new_receipt_id;

    RETURN QUERY SELECT
        requested_task_id, new_receipt_id, computed_input_hash, false;
END;
$$;

CREATE FUNCTION memoriesql.claim_semantic_task(
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_lease_seconds integer,
    requested_deadline_seconds integer,
    requested_executor_contract_version integer,
    requested_at timestamp with time zone
)
RETURNS TABLE (
    tenant_id uuid,
    workspace_id uuid,
    access_scope_id uuid,
    task_id uuid,
    attempt_id uuid,
    attempt_number integer,
    lease_generation bigint,
    task_kind text,
    contract_revision integer,
    task_contract_hash text,
    semantic_registry_hash text,
    queue_name text,
    target_kind text,
    target_reference text,
    expected_target_revision integer,
    input_hash text,
    evidence_manifest_id text,
    evidence_manifest_hash text,
    origin_principal_id uuid,
    origin_pairing_grant_id uuid,
    required_capability text,
    accepted_policy_revision_id uuid,
    deadline_at timestamp with time zone,
    lease_expires_at timestamp with time zone
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    candidate memoriesql.semantic_tasks%ROWTYPE;
    candidate_task_id uuid;
    candidate_owning_module text;
    skipped_task_ids uuid[] := ARRAY[]::uuid[];
    new_attempt_id uuid;
    new_attempt_number integer;
    new_generation bigint;
    chosen_slot integer;
    chosen_deadline timestamp with time zone;
    chosen_lease_expiry timestamp with time zone;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR NOT memoriesql.current_context_has_capability('memory.maintain') THEN
        RAISE EXCEPTION 'bounded service worker context is required'
            USING ERRCODE = '42501';
    END IF;
    IF requested_worker_id IS NULL
       OR btrim(requested_worker_id) = ''
       OR requested_worker_instance_id IS NULL
       OR btrim(requested_worker_instance_id) = ''
       OR requested_lease_seconds IS NULL
       OR requested_lease_seconds < 90
       OR requested_lease_seconds > 3600
       OR requested_deadline_seconds IS NULL
       OR requested_deadline_seconds < requested_lease_seconds
       OR requested_deadline_seconds > 86400
       OR requested_executor_contract_version IS NULL
       OR requested_executor_contract_version <= 0
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'semantic task claim contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    LOOP
        -- Identify a candidate without locking its row. The module admission
        -- key must be acquired before the task row to share module control's
        -- module-key -> task-row order.
        SELECT task.task_id, task.owning_module
        INTO candidate_task_id, candidate_owning_module
        FROM memoriesql.semantic_tasks AS task
        LEFT JOIN memoriesql.semantic_module_queue_controls AS control
          ON control.tenant_id = task.tenant_id
         AND control.workspace_id = task.workspace_id
         AND control.owning_module = task.owning_module
        WHERE task.tenant_id = context_record.tenant_id
          AND task.workspace_id = context_record.workspace_id
          AND task.status IN ('queued', 'retry_wait')
          AND task.available_at <= database_now
          AND task.cancel_requested_at IS NULL
          AND task.attempt_count < task.max_attempts
          AND task.lease_owner IS NULL
          AND task.worker_instance_id IS NULL
          AND task.lease_expires_at IS NULL
          AND task.heartbeat_at IS NULL
          AND NOT task.task_id = ANY(skipped_task_ids)
          AND EXISTS (
              SELECT 1
              FROM memoriesql.semantic_worker_claim_policies AS worker_policy
              WHERE worker_policy.tenant_id = context_record.tenant_id
                AND worker_policy.workspace_id = context_record.workspace_id
                AND worker_policy.principal_id = context_record.principal_id
                AND worker_policy.pairing_grant_id =
                    context_record.pairing_grant_id
                AND worker_policy.semantic_registry_hash =
                    task.semantic_registry_hash
                AND worker_policy.task_kind = task.task_kind
                AND worker_policy.contract_revision = task.contract_revision
                AND worker_policy.queue_name = task.queue_name
          )
          AND COALESCE(control.state, 'enabled') = 'enabled'
          AND (
              task.concurrency_key IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM memoriesql.semantic_concurrency_slots AS available_slot
                  WHERE available_slot.tenant_id = task.tenant_id
                    AND available_slot.workspace_id = task.workspace_id
                    AND available_slot.concurrency_key = task.concurrency_key
                    AND available_slot.slot_number <= task.concurrency_limit
                    AND available_slot.task_id IS NULL
              )
              AND (
                  SELECT count(*)
                  FROM memoriesql.semantic_concurrency_slots AS occupied_slot
                  WHERE occupied_slot.tenant_id = task.tenant_id
                    AND occupied_slot.workspace_id = task.workspace_id
                    AND occupied_slot.concurrency_key = task.concurrency_key
                    AND occupied_slot.task_id IS NOT NULL
              ) < task.concurrency_limit
          )
          AND memoriesql.current_context_scope_authorized(
              task.access_scope_id, 'memory.maintain', 'read'
          )
          AND memoriesql.current_context_scope_time_authorized(
              task.access_scope_id, 'read', database_now
          )
          AND memoriesql.semantic_task_origin_authorized(
              task.tenant_id, task.task_id, database_now
          )
        ORDER BY
            task.base_priority::bigint
            + LEAST(
                4294967296::numeric,
                floor(
                    EXTRACT(EPOCH FROM (database_now - task.created_at)) / 30
                )
              )::bigint DESC,
            CASE task.queue_name
                WHEN 'interactive' THEN 3
                WHEN 'capture' THEN 2
                ELSE 1
            END DESC,
            task.available_at,
            task.created_at,
            task.task_id
        LIMIT 1;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        skipped_task_ids := pg_catalog.array_append(
            skipped_task_ids, candidate_task_id
        );

        PERFORM pg_catalog.pg_advisory_xact_lock_shared(
            pg_catalog.hashtextextended(
                context_record.tenant_id::text
                    || ':' || context_record.workspace_id::text
                    || ':semantic_module:' || candidate_owning_module,
                0
            )
        );

        -- Admission is now stable against draining/disabled transitions. Lock
        -- the task second, then refresh every authority and time seam before
        -- any attempt or concurrency slot can be consumed.
        database_now := pg_catalog.clock_timestamp();
        SELECT * INTO context_record
        FROM memoriesql.current_authorization_context();
        IF NOT FOUND
           OR context_record.principal_kind <> 'service'
           OR context_record.expires_at <= database_now
           OR NOT memoriesql.current_context_has_capability(
               'memory.maintain'
           ) THEN
            RAISE EXCEPTION 'bounded service worker context is required'
                USING ERRCODE = '42501';
        END IF;
        IF requested_at < database_now - interval '5 minutes'
           OR requested_at > database_now + interval '5 minutes' THEN
            RAISE EXCEPTION 'semantic task claim contract is invalid'
                USING ERRCODE = '22023';
        END IF;

        SELECT task.* INTO candidate
        FROM memoriesql.semantic_tasks AS task
        LEFT JOIN memoriesql.semantic_module_queue_controls AS control
          ON control.tenant_id = task.tenant_id
         AND control.workspace_id = task.workspace_id
         AND control.owning_module = task.owning_module
        WHERE task.tenant_id = context_record.tenant_id
          AND task.workspace_id = context_record.workspace_id
          AND task.task_id = candidate_task_id
          AND task.owning_module = candidate_owning_module
          AND task.status IN ('queued', 'retry_wait')
          AND task.available_at <= database_now
          AND task.cancel_requested_at IS NULL
          AND task.attempt_count < task.max_attempts
          AND task.lease_owner IS NULL
          AND task.worker_instance_id IS NULL
          AND task.lease_expires_at IS NULL
          AND task.heartbeat_at IS NULL
          AND COALESCE(control.state, 'enabled') = 'enabled'
          AND memoriesql.current_context_scope_authorized(
              task.access_scope_id, 'memory.maintain', 'read'
          )
          AND memoriesql.current_context_scope_time_authorized(
              task.access_scope_id, 'read', database_now
          )
          AND memoriesql.semantic_task_origin_authorized(
              task.tenant_id, task.task_id, database_now
          )
          AND EXISTS (
              SELECT 1
              FROM memoriesql.semantic_worker_claim_policies AS worker_policy
              WHERE worker_policy.tenant_id = context_record.tenant_id
                AND worker_policy.workspace_id = context_record.workspace_id
                AND worker_policy.principal_id = context_record.principal_id
                AND worker_policy.pairing_grant_id =
                    context_record.pairing_grant_id
                AND worker_policy.semantic_registry_hash =
                    task.semantic_registry_hash
                AND worker_policy.task_kind = task.task_kind
                AND worker_policy.contract_revision = task.contract_revision
                AND worker_policy.queue_name = task.queue_name
          )
        FOR UPDATE OF task SKIP LOCKED;
        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        database_now := pg_catalog.clock_timestamp();
        SELECT * INTO context_record
        FROM memoriesql.current_authorization_context();
        IF NOT FOUND
           OR context_record.principal_kind <> 'service'
           OR context_record.expires_at <= database_now
           OR NOT memoriesql.current_context_has_capability(
               'memory.maintain'
           ) THEN
            RAISE EXCEPTION 'bounded service worker context is required'
                USING ERRCODE = '42501';
        END IF;
        IF requested_at < database_now - interval '5 minutes'
           OR requested_at > database_now + interval '5 minutes' THEN
            RAISE EXCEPTION 'semantic task claim contract is invalid'
                USING ERRCODE = '22023';
        END IF;
        IF candidate.status NOT IN ('queued', 'retry_wait')
           OR candidate.available_at > database_now
           OR candidate.cancel_requested_at IS NOT NULL
           OR candidate.attempt_count >= candidate.max_attempts
           OR candidate.lease_owner IS NOT NULL
           OR candidate.worker_instance_id IS NOT NULL
           OR candidate.lease_expires_at IS NOT NULL
           OR candidate.heartbeat_at IS NOT NULL
           OR COALESCE(
               (
                   SELECT control.state
                   FROM memoriesql.semantic_module_queue_controls AS control
                   WHERE control.tenant_id = candidate.tenant_id
                     AND control.workspace_id = candidate.workspace_id
                     AND control.owning_module = candidate.owning_module
               ),
               'enabled'
           ) <> 'enabled'
           OR NOT memoriesql.current_context_scope_authorized(
               candidate.access_scope_id, 'memory.maintain', 'read'
           )
           OR NOT memoriesql.current_context_scope_time_authorized(
               candidate.access_scope_id, 'read', database_now
           )
           OR NOT memoriesql.semantic_task_origin_authorized(
               candidate.tenant_id, candidate.task_id, database_now
           )
           OR NOT EXISTS (
               SELECT 1
               FROM memoriesql.semantic_worker_claim_policies AS worker_policy
               WHERE worker_policy.tenant_id = context_record.tenant_id
                 AND worker_policy.workspace_id = context_record.workspace_id
                 AND worker_policy.principal_id = context_record.principal_id
                 AND worker_policy.pairing_grant_id =
                     context_record.pairing_grant_id
                 AND worker_policy.semantic_registry_hash =
                     candidate.semantic_registry_hash
                 AND worker_policy.task_kind = candidate.task_kind
                 AND worker_policy.contract_revision =
                     candidate.contract_revision
                 AND worker_policy.queue_name = candidate.queue_name
           ) THEN
            CONTINUE;
        END IF;

        chosen_slot := NULL;
        IF candidate.concurrency_key IS NOT NULL THEN
            -- Serialize the occupied-count decision per shared key. Tasks with
            -- different declared limits may coexist, but every new claimant
            -- must respect its own cap across all occupied slots.
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    candidate.tenant_id::text
                        || ':' || candidate.workspace_id::text
                        || ':semantic_concurrency:'
                        || candidate.concurrency_key,
                    0
                )
            );

            -- The shared-key lock may wait behind another claimant. Refresh
            -- both the clock and every credential-derived claim seam before
            -- creating slots or consuming an attempt.
            database_now := pg_catalog.clock_timestamp();
            SELECT * INTO context_record
            FROM memoriesql.current_authorization_context();
            IF NOT FOUND
               OR context_record.principal_kind <> 'service'
               OR context_record.expires_at <= database_now
               OR NOT memoriesql.current_context_has_capability(
                   'memory.maintain'
               ) THEN
                RAISE EXCEPTION 'bounded service worker context is required'
                    USING ERRCODE = '42501';
            END IF;
            IF requested_at < database_now - interval '5 minutes'
               OR requested_at > database_now + interval '5 minutes' THEN
                RAISE EXCEPTION 'semantic task claim contract is invalid'
                    USING ERRCODE = '22023';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM memoriesql.semantic_worker_claim_policies AS worker_policy
                WHERE worker_policy.tenant_id = context_record.tenant_id
                  AND worker_policy.workspace_id = context_record.workspace_id
                  AND worker_policy.principal_id = context_record.principal_id
                  AND worker_policy.pairing_grant_id =
                      context_record.pairing_grant_id
                  AND worker_policy.semantic_registry_hash =
                      candidate.semantic_registry_hash
                  AND worker_policy.task_kind = candidate.task_kind
                  AND worker_policy.contract_revision =
                      candidate.contract_revision
                  AND worker_policy.queue_name = candidate.queue_name
            )
               -- The shared module key remains held while claim waits on the
               -- concurrency key. Recheck the stable state with other seams.
               OR COALESCE(
                   (
                       SELECT control.state
                       FROM memoriesql.semantic_module_queue_controls AS control
                       WHERE control.tenant_id = candidate.tenant_id
                         AND control.workspace_id = candidate.workspace_id
                         AND control.owning_module = candidate.owning_module
                   ),
                   'enabled'
               ) <> 'enabled'
               OR NOT memoriesql.current_context_scope_authorized(
                   candidate.access_scope_id, 'memory.maintain', 'read'
               )
               OR NOT memoriesql.current_context_scope_time_authorized(
                   candidate.access_scope_id, 'read', database_now
               )
               OR NOT memoriesql.semantic_task_origin_authorized(
                   candidate.tenant_id, candidate.task_id, database_now
               ) THEN
                CONTINUE;
            END IF;
            INSERT INTO memoriesql.semantic_concurrency_slots (
                tenant_id, workspace_id, concurrency_key, slot_number, updated_at
            )
            SELECT
                candidate.tenant_id, candidate.workspace_id,
                candidate.concurrency_key, generated.slot_number, database_now
            FROM generate_series(
                1, candidate.concurrency_limit
            ) AS generated(slot_number)
            ON CONFLICT DO NOTHING;

            IF (
                SELECT count(*)
                FROM memoriesql.semantic_concurrency_slots AS occupied_slot
                WHERE occupied_slot.tenant_id = candidate.tenant_id
                  AND occupied_slot.workspace_id = candidate.workspace_id
                  AND occupied_slot.concurrency_key = candidate.concurrency_key
                  AND occupied_slot.task_id IS NOT NULL
            ) >= candidate.concurrency_limit THEN
                CONTINUE;
            END IF;

            SELECT slot.slot_number INTO chosen_slot
            FROM memoriesql.semantic_concurrency_slots AS slot
            WHERE slot.tenant_id = candidate.tenant_id
              AND slot.workspace_id = candidate.workspace_id
              AND slot.concurrency_key = candidate.concurrency_key
              AND slot.slot_number <= candidate.concurrency_limit
              AND slot.task_id IS NULL
            ORDER BY slot.slot_number
            FOR UPDATE SKIP LOCKED
            LIMIT 1;
            IF NOT FOUND THEN
                CONTINUE;
            END IF;
        END IF;

        IF candidate.status = 'retry_wait' THEN
            UPDATE memoriesql.semantic_tasks AS retry_task
               SET status = 'queued',
                   updated_at = GREATEST(retry_task.updated_at, database_now)
             WHERE retry_task.tenant_id = candidate.tenant_id
               AND retry_task.task_id = candidate.task_id;
            PERFORM memoriesql.append_semantic_task_event(
                candidate.tenant_id, candidate.task_id, NULL, 'retry_ready',
                context_record.principal_id, requested_worker_id, requested_at,
                jsonb_build_object('available_at', candidate.available_at)
            );
        END IF;

        new_attempt_id := pg_catalog.uuidv7();
        new_attempt_number := candidate.attempt_count + 1;
        new_generation := candidate.lease_generation + 1;
        chosen_lease_expiry := database_now
            + make_interval(secs => requested_lease_seconds);
        chosen_deadline := database_now
            + make_interval(secs => requested_deadline_seconds);

        UPDATE memoriesql.semantic_tasks AS claimed_task
           SET status = 'running',
               attempt_count = new_attempt_number,
               lease_owner = requested_worker_id,
               worker_instance_id = requested_worker_instance_id,
               lease_generation = new_generation,
               lease_expires_at = chosen_lease_expiry,
               heartbeat_at = database_now,
               result_attempt_id = NULL,
               result_ref = NULL,
               result_hash = NULL,
               started_at = COALESCE(claimed_task.started_at, database_now),
               updated_at = GREATEST(claimed_task.updated_at, database_now)
         WHERE claimed_task.tenant_id = candidate.tenant_id
           AND claimed_task.task_id = candidate.task_id;

        INSERT INTO memoriesql.semantic_task_attempts (
            tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
            attempt_number, lease_generation, claimant_principal_id,
            worker_id, worker_instance_id,
            status, executor_contract_version, task_contract_revision,
            task_contract_hash, input_hash, evidence_manifest_hash,
            authorization_policy_revision_id, claimed_at, heartbeat_at,
            lease_expires_at, deadline_at
        ) VALUES (
            candidate.tenant_id, candidate.workspace_id, candidate.access_scope_id,
            candidate.task_id, new_attempt_id, new_attempt_number, new_generation,
            context_record.principal_id, requested_worker_id,
            requested_worker_instance_id, 'claimed',
            requested_executor_contract_version, candidate.contract_revision,
            candidate.task_contract_hash, candidate.input_hash,
            candidate.evidence_manifest_hash,
            candidate.accepted_policy_revision_id, database_now, database_now,
            chosen_lease_expiry, chosen_deadline
        );

        IF chosen_slot IS NOT NULL THEN
            UPDATE memoriesql.semantic_concurrency_slots
               SET task_id = candidate.task_id,
                   attempt_id = new_attempt_id,
                   lease_generation = new_generation,
                   lease_owner = requested_worker_id,
                   worker_instance_id = requested_worker_instance_id,
                   lease_expires_at = chosen_lease_expiry,
                   updated_at = database_now
             WHERE semantic_concurrency_slots.tenant_id = candidate.tenant_id
               AND semantic_concurrency_slots.workspace_id = candidate.workspace_id
               AND semantic_concurrency_slots.concurrency_key =
                   candidate.concurrency_key
               AND semantic_concurrency_slots.slot_number = chosen_slot;
        END IF;

        PERFORM memoriesql.append_semantic_task_event(
            candidate.tenant_id, candidate.task_id, new_attempt_id, 'claimed',
            context_record.principal_id, requested_worker_id, requested_at,
            jsonb_build_object(
                'attempt_number', new_attempt_number,
                'lease_generation', new_generation,
                'queue_name', candidate.queue_name,
                'slot_number', chosen_slot
            )
        );

        RETURN QUERY SELECT
            candidate.tenant_id, candidate.workspace_id,
            candidate.access_scope_id, candidate.task_id, new_attempt_id,
            new_attempt_number, new_generation, candidate.task_kind,
            candidate.contract_revision, candidate.task_contract_hash,
            candidate.semantic_registry_hash, candidate.queue_name,
            candidate.target_kind, candidate.target_reference,
            candidate.expected_target_revision, candidate.input_hash,
            candidate.evidence_manifest_id, candidate.evidence_manifest_hash,
            candidate.origin_principal_id, candidate.origin_pairing_grant_id,
            candidate.required_capability, candidate.accepted_policy_revision_id,
            chosen_deadline, chosen_lease_expiry;
        RETURN;
    END LOOP;
    RETURN;
END;
$$;

CREATE FUNCTION memoriesql.start_semantic_task_attempt(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
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
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN false;
    END IF;
    -- Authenticate the immutable attempt fence without taking a row lock. A
    -- different paired service must fail before it can hold the task row and
    -- delay the real claimant or expiry recovery.
    database_now := pg_catalog.clock_timestamp();
    PERFORM 1
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = requested_tenant_id
      AND attempt.task_id = requested_task_id
      AND attempt.attempt_id = requested_attempt_id
      AND attempt.lease_generation = requested_lease_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'claimed'
      AND attempt.lease_expires_at > database_now
      AND attempt.deadline_at > database_now;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    -- The reaper owns the same task -> attempt lock order. Lock the task before
    -- changing attempt state so startup at the lease boundary cannot deadlock
    -- against task-first expiry recovery.
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
      AND memoriesql.current_context_scope_authorized(
          task.access_scope_id, 'memory.maintain', 'read'
      )
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    -- statement_timestamp() is fixed for the command and may be stale after a
    -- row-lock wait. Refresh from the database wall clock before accepting the
    -- lease/deadline or recording started_at.
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at IS NULL
       OR task_record.lease_expires_at <= database_now
       OR task_record.cancel_requested_at IS NOT NULL
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
           task_record.access_scope_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       ) THEN
        RETURN false;
    END IF;
    UPDATE memoriesql.semantic_task_attempts AS attempt
       SET status = 'running', started_at = database_now
     WHERE attempt.tenant_id = requested_tenant_id
       AND attempt.task_id = requested_task_id
       AND attempt.attempt_id = requested_attempt_id
       AND attempt.lease_generation = requested_lease_generation
       AND attempt.claimant_principal_id = context_record.principal_id
       AND attempt.worker_id = requested_worker_id
       AND attempt.worker_instance_id = requested_worker_instance_id
       AND attempt.status = 'claimed'
       AND attempt.lease_expires_at > database_now
       AND attempt.deadline_at > database_now;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM memoriesql.append_semantic_task_event(
        requested_tenant_id, requested_task_id, requested_attempt_id, 'started',
        context_record.principal_id, requested_worker_id, requested_at,
        jsonb_build_object('lease_generation', requested_lease_generation)
    );
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.heartbeat_semantic_task(
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
    attempt_deadline timestamp with time zone;
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
    -- Lease mutation follows task -> attempt -> slot, matching expiry recovery.
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
    SELECT attempt.deadline_at INTO attempt_deadline
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
        PERFORM 1
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
       OR attempt_deadline <= database_now
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
    new_expiry := LEAST(
        attempt_deadline,
        GREATEST(
            task_record.lease_expires_at,
            database_now + make_interval(secs => requested_lease_seconds)
        )
    );
    UPDATE memoriesql.semantic_task_attempts
       SET heartbeat_at = database_now, lease_expires_at = new_expiry
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
           SET lease_expires_at = new_expiry, updated_at = database_now
         WHERE tenant_id = requested_tenant_id
           AND workspace_id = task_record.workspace_id
           AND concurrency_key = task_record.concurrency_key
           AND task_id = requested_task_id
           AND attempt_id = requested_attempt_id;
    END IF;
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.reauthorize_semantic_task(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_phase text,
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
    source_revision integer;
    database_now timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR context_record.expires_at <= database_now
       OR requested_phase NOT IN ('hydrate', 'outcome')
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN 'invalid_phase';
    END IF;
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    JOIN memoriesql.semantic_task_attempts AS attempt
      ON attempt.tenant_id = task.tenant_id
     AND attempt.task_id = task.task_id
     AND attempt.attempt_id = requested_attempt_id
     AND attempt.lease_generation = requested_lease_generation
     AND attempt.claimant_principal_id = context_record.principal_id
     AND attempt.worker_id = requested_worker_id
     AND attempt.worker_instance_id = requested_worker_instance_id
     AND attempt.status IN ('claimed', 'running')
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'running'
      AND task.lease_generation = requested_lease_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND task.lease_expires_at > database_now
      AND attempt.deadline_at > database_now
      AND (
          requested_phase = 'outcome'
          OR task.cancel_requested_at IS NULL
      )
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      );
    IF NOT FOUND THEN
        RETURN 'stale_fence';
    END IF;
    IF NOT memoriesql.current_context_scope_time_authorized(
        task_record.access_scope_id, 'read', database_now
    ) THEN
        RETURN 'stale_fence';
    END IF;
    IF NOT memoriesql.semantic_task_origin_authorized(
        requested_tenant_id, requested_task_id, database_now
    ) THEN
        RETURN 'policy_paused';
    END IF;
    IF task_record.evidence_manifest_id IS DISTINCT FROM
           task_record.input_payload #>> '{evidence_manifest,manifest_id}'
       OR task_record.evidence_manifest_hash IS DISTINCT FROM encode(
           pg_catalog.sha256(
               pg_catalog.convert_to(
                   (task_record.input_payload -> 'evidence_manifest')::text,
                   'UTF8'
               )
           ),
           'hex'
       ) THEN
        RETURN 'stale_evidence';
    END IF;

    IF task_record.target_kind = 'source_object' THEN
        SELECT source.schema_version INTO source_revision
        FROM memoriesql.source_objects AS source
        JOIN memoriesql.protected_resources AS resource
          ON resource.tenant_id = source.tenant_id
         AND resource.workspace_id = source.workspace_id
         AND resource.access_scope_id = source.access_scope_id
         AND resource.resource_kind = 'source'
         AND resource.resource_id = source.source_object_id
         AND resource.status = 'active'
        WHERE source.tenant_id = task_record.tenant_id
          AND source.workspace_id = task_record.workspace_id
          AND source.access_scope_id = task_record.access_scope_id
          AND source.source_object_id::text = task_record.target_reference;
        IF NOT FOUND THEN
            RETURN 'evidence_unavailable';
        END IF;
        IF task_record.expected_target_revision > 0
           AND source_revision <> task_record.expected_target_revision THEN
            RETURN 'stale_evidence';
        END IF;
    END IF;
    RETURN 'authorized';
END;
$$;

CREATE FUNCTION memoriesql.hydrate_semantic_task_input(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_at timestamp with time zone
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authorization_result text;
    hydrated_input jsonb;
BEGIN
    authorization_result := memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'hydrate', requested_at
    );
    IF authorization_result <> 'authorized' THEN
        RETURN NULL;
    END IF;
    WITH locked_input AS MATERIALIZED (
        SELECT task.input_payload
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
         AND attempt.status IN ('claimed', 'running')
        WHERE context.principal_kind = 'service'
          AND task.tenant_id = requested_tenant_id
          AND task.task_id = requested_task_id
          AND task.status = 'running'
          AND task.lease_generation = requested_lease_generation
          AND task.lease_owner = requested_worker_id
          AND task.worker_instance_id = requested_worker_instance_id
          AND task.cancel_requested_at IS NULL
        FOR UPDATE OF task
    )
    SELECT locked_input.input_payload INTO hydrated_input
    FROM locked_input
    WHERE memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'hydrate', requested_at
    ) = 'authorized';
    RETURN hydrated_input;
END;
$$;

CREATE FUNCTION memoriesql.release_semantic_task_slot(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_at timestamp with time zone
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    UPDATE memoriesql.semantic_concurrency_slots
       SET task_id = NULL,
           attempt_id = NULL,
           lease_generation = NULL,
           lease_owner = NULL,
           worker_instance_id = NULL,
           lease_expires_at = NULL,
           updated_at = requested_at
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id
       AND attempt_id = requested_attempt_id;
END;
$$;

CREATE FUNCTION memoriesql.record_semantic_task_outcome(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_result_status text,
    requested_result_hash text,
    requested_result_ref text,
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
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_deadline timestamp with time zone;
    authorization_result text;
    task_status text;
    attempt_status text;
    event_kind text;
    retry_delay_seconds integer;
    retry_at timestamp with time zone;
    terminal_at timestamp with time zone;
    effective_result_status text;
    effective_error_code text;
    effective_error_class text;
    effective_retry_class text;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN 'stale_fence';
    END IF;
    IF requested_result_status IS NULL
       OR requested_result_status NOT IN (
        'succeeded', 'unavailable', 'budget_exhausted', 'cancelled',
        'invalid_output', 'stale_input', 'policy_paused', 'failed'
    ) THEN
        RAISE EXCEPTION 'semantic task outcome contract is invalid'
            USING ERRCODE = '22023';
    END IF;
    IF requested_retry_class IS DISTINCT FROM
           memoriesql.semantic_retry_class_for_status(requested_result_status)
       OR requested_result_hash IS NOT NULL
         AND requested_result_hash !~ '^[a-f0-9]{64}$'
       OR requested_result_status = 'succeeded'
         AND requested_result_hash IS NULL
       OR requested_result_status <> 'succeeded'
         AND requested_result_hash IS NOT NULL
       OR requested_result_status = 'succeeded'
         AND (
             requested_error_code IS NOT NULL
             OR requested_error_class IS NOT NULL
             OR requested_retry_class IS NOT NULL
             OR requested_retry_after_seconds IS NOT NULL
         )
       OR requested_result_status <> 'succeeded'
         AND (
             requested_error_code IS NULL
             OR requested_retry_class IS NULL
         )
       OR requested_result_status <> 'succeeded'
         AND requested_result_ref IS NOT NULL
       OR requested_result_ref IS NOT NULL
         AND (
             length(requested_result_ref) > 512
             OR requested_result_ref
                !~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
         )
       OR requested_error_code IS NOT NULL
         AND (
             length(requested_error_code) > 128
             OR requested_error_code
                !~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
         )
       OR requested_error_class IS NOT NULL
         AND (
             length(requested_error_class) > 128
             OR requested_error_class
                !~ '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
         )
       OR requested_retry_after_seconds IS NOT NULL
         AND requested_retry_after_seconds < 0
       OR requested_jitter_basis_points IS NULL
       OR requested_jitter_basis_points < 0
       OR requested_jitter_basis_points > 2500 THEN
        RAISE EXCEPTION 'semantic task outcome contract is invalid'
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
      AND attempt.status IN ('claimed', 'running');
    IF NOT FOUND THEN
        RETURN 'stale_fence';
    END IF;
    -- Outcome settlement follows task -> attempt, matching expiry recovery.
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
        RETURN 'stale_fence';
    END IF;
    SELECT attempt.deadline_at INTO attempt_deadline
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
    IF NOT memoriesql.lock_semantic_task_outcome_authority(
        requested_tenant_id, requested_task_id
    ) THEN
        RETURN 'stale_fence';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR attempt_deadline <= database_now
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_semantic_task_authorized(
           requested_task_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       ) THEN
        RETURN 'stale_fence';
    END IF;

    effective_result_status := requested_result_status;
    effective_error_code := requested_error_code;
    effective_error_class := requested_error_class;
    authorization_result := memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'outcome', requested_at
    );

    IF task_record.cancel_requested_at IS NOT NULL
       AND task_record.cancel_reason = 'evidence.stale' THEN
        effective_result_status := 'stale_input';
        effective_error_code := 'evidence.stale';
        effective_error_class := 'evidence';
    ELSIF task_record.cancel_requested_at IS NOT NULL THEN
        effective_result_status := 'cancelled';
        effective_error_code := 'cancelled.late_result_discarded';
        effective_error_class := 'cancellation';
    ELSIF authorization_result = 'policy_paused' THEN
        effective_result_status := 'policy_paused';
        effective_error_code := 'authorization.policy_paused';
        effective_error_class := 'authorization';
    ELSIF authorization_result = 'evidence_unavailable' THEN
        effective_result_status := 'unavailable';
        effective_error_code := 'evidence.unavailable';
        effective_error_class := 'evidence';
    ELSIF authorization_result = 'stale_evidence' THEN
        effective_result_status := 'stale_input';
        effective_error_code := 'evidence.stale';
        effective_error_class := 'evidence';
    ELSIF authorization_result <> 'authorized' THEN
        RETURN 'stale_fence';
    END IF;

    effective_retry_class := memoriesql.semantic_retry_class_for_status(
        effective_result_status
    );
    IF effective_result_status = 'succeeded' THEN
        task_status := 'succeeded';
        attempt_status := 'succeeded';
        event_kind := 'succeeded';
    ELSIF effective_result_status = 'cancelled' THEN
        task_status := 'cancelled';
        attempt_status := 'cancelled';
        event_kind := 'cancelled';
    ELSIF effective_result_status = 'policy_paused' THEN
        task_status := 'policy_paused';
        attempt_status := 'terminal_failure';
        event_kind := 'policy_paused';
    ELSIF effective_result_status = 'stale_input' THEN
        task_status := 'superseded';
        attempt_status := 'terminal_failure';
        event_kind := 'superseded';
    ELSIF effective_retry_class = 'transient' THEN
        attempt_status := 'retryable_failure';
        IF task_record.attempt_count >= task_record.max_attempts THEN
            task_status := 'dead_letter';
            event_kind := 'dead_lettered';
        ELSE
            task_status := 'retry_wait';
            event_kind := 'retry_scheduled';
        END IF;
    ELSE
        task_status := 'failed_terminal';
        attempt_status := 'terminal_failure';
        event_kind := 'failed_terminal';
    END IF;

    retry_at := NULL;
    IF task_status = 'retry_wait' THEN
        retry_delay_seconds := LEAST(
            900,
            (
                5 * power(
                    2::numeric,
                    LEAST(task_record.attempt_count - 1, 8)
                )
            )::integer
        );
        retry_delay_seconds := LEAST(
            900,
            retry_delay_seconds
            + floor(
                retry_delay_seconds * requested_jitter_basis_points / 10000.0
              )::integer
        );
        IF requested_retry_after_seconds IS NOT NULL THEN
            retry_delay_seconds := GREATEST(
                retry_delay_seconds,
                LEAST(900, requested_retry_after_seconds)
            );
        END IF;
        retry_at := database_now + make_interval(secs => retry_delay_seconds);
    END IF;
    terminal_at := CASE
        WHEN task_status IN ('retry_wait', 'policy_paused') THEN NULL
        ELSE database_now
    END;

    PERFORM memoriesql.append_semantic_task_event(
        requested_tenant_id, requested_task_id, requested_attempt_id, event_kind,
        context_record.principal_id, requested_worker_id, requested_at,
        jsonb_strip_nulls(jsonb_build_object(
            'result_status', effective_result_status,
            'retry_class', effective_retry_class,
            'retry_at', retry_at,
            'error_code', effective_error_code,
            'lease_generation', requested_lease_generation
        ))
    );

    UPDATE memoriesql.semantic_task_attempts
       SET status = attempt_status,
           finished_at = database_now,
           result_status = effective_result_status,
           result_hash = CASE
               WHEN task_status = 'succeeded' THEN requested_result_hash
               ELSE NULL
           END,
           result_ref = CASE
               WHEN task_status = 'succeeded' THEN requested_result_ref
               ELSE NULL
           END,
           error_code = CASE
               WHEN task_status = 'succeeded' THEN NULL
               ELSE COALESCE(effective_error_code, 'semantic.failed')
           END,
           error_class = CASE
               WHEN task_status = 'succeeded' THEN NULL
               ELSE COALESCE(effective_error_class, 'semantic')
           END,
           retry_class = CASE
               WHEN task_status = 'succeeded' THEN NULL
               ELSE COALESCE(effective_retry_class, 'never')
           END,
           retry_after = retry_at
     WHERE tenant_id = requested_tenant_id
       AND attempt_id = requested_attempt_id;

    PERFORM memoriesql.release_semantic_task_slot(
        requested_tenant_id, requested_task_id, requested_attempt_id, database_now
    );
    UPDATE memoriesql.semantic_tasks
       SET status = task_status,
           pause_reason_code = CASE
               WHEN task_status = 'policy_paused' THEN
                   COALESCE(effective_error_code, 'policy.paused')
               ELSE NULL
           END,
           available_at = COALESCE(retry_at, available_at),
           lease_owner = NULL,
           worker_instance_id = NULL,
           lease_expires_at = NULL,
           heartbeat_at = NULL,
           result_attempt_id = requested_attempt_id,
           result_ref = CASE
               WHEN task_status = 'succeeded' THEN requested_result_ref
               ELSE NULL
           END,
           result_hash = CASE
               WHEN task_status = 'succeeded' THEN requested_result_hash
               ELSE NULL
           END,
           updated_at = GREATEST(updated_at, database_now),
           completed_at = terminal_at
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    RETURN task_status;
END;
$$;

CREATE FUNCTION memoriesql.reap_expired_semantic_tasks(
    requested_worker_id text,
    requested_limit integer,
    requested_at timestamp with time zone
)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    next_status text;
    next_event text;
    next_available_at timestamp with time zone;
    outcome_result_status text;
    outcome_error_code text;
    outcome_error_class text;
    outcome_retry_class text;
    origin_authorized boolean;
    reaped_count integer := 0;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    IF requested_worker_id IS NULL
       OR btrim(requested_worker_id) = ''
       OR requested_limit IS NULL
       OR requested_limit <= 0
       OR requested_limit > 1000
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'semantic task reaper contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR NOT memoriesql.current_context_has_capability('memory.maintain') THEN
        RAISE EXCEPTION 'bounded service reaper context is required'
            USING ERRCODE = '42501';
    END IF;

    -- Authority mutations take this tenant key exclusively. Acquire the shared
    -- side before any task row so origin revocation cannot commit between the
    -- final origin check and the recovery transition.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(
            context_record.tenant_id::text || ':semantic_outcome_authority:',
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_has_capability('memory.maintain')
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'bounded service reaper context is required'
            USING ERRCODE = '42501';
    END IF;

    FOR task_record IN
        SELECT task.*
        FROM memoriesql.semantic_tasks AS task
        WHERE task.tenant_id = context_record.tenant_id
          AND task.workspace_id = context_record.workspace_id
          AND task.status = 'running'
          AND task.lease_expires_at <= database_now
          AND memoriesql.current_context_scope_authorized(
              task.access_scope_id, 'memory.maintain', 'read'
          )
        ORDER BY task.lease_expires_at, task.task_id
        FOR UPDATE SKIP LOCKED
        LIMIT requested_limit
    LOOP
        -- Reauthorize each locked task before the first batch mutation. A
        -- mid-batch loss of authority aborts and rolls back the whole batch.
        database_now := pg_catalog.clock_timestamp();
        SELECT * INTO context_record
        FROM memoriesql.current_authorization_context();
        IF NOT FOUND
           OR context_record.principal_kind <> 'service'
           OR context_record.expires_at <= database_now
           OR NOT memoriesql.current_context_has_capability('memory.maintain')
           OR requested_at < database_now - interval '5 minutes'
           OR requested_at > database_now + interval '5 minutes'
           OR NOT memoriesql.current_context_semantic_task_authorized(
               task_record.task_id, 'memory.maintain', 'read'
           )
           OR NOT memoriesql.current_context_scope_authorized(
               task_record.access_scope_id, 'memory.maintain', 'read'
           )
           OR NOT memoriesql.current_context_scope_time_authorized(
               task_record.access_scope_id, 'read', database_now
           ) THEN
            RAISE EXCEPTION 'bounded service reaper context is required'
                USING ERRCODE = '42501';
        END IF;

        origin_authorized := memoriesql.semantic_task_origin_authorized(
            task_record.tenant_id, task_record.task_id, database_now
        );

        IF task_record.cancel_requested_at IS NOT NULL
           AND task_record.cancel_reason = 'evidence.stale' THEN
            next_status := 'superseded';
            next_event := 'superseded';
            next_available_at := task_record.available_at;
        ELSIF task_record.cancel_requested_at IS NOT NULL THEN
            next_status := 'cancelled';
            next_event := 'cancelled';
            next_available_at := task_record.available_at;
        ELSIF task_record.attempt_count >= task_record.max_attempts THEN
            next_status := 'dead_letter';
            next_event := 'dead_lettered';
            next_available_at := task_record.available_at;
        ELSIF NOT origin_authorized THEN
            next_status := 'policy_paused';
            next_event := 'policy_paused';
            next_available_at := task_record.available_at;
        ELSE
            next_status := 'retry_wait';
            next_event := 'retry_scheduled';
            next_available_at := database_now + make_interval(
                secs => LEAST(
                    900,
                    (
                        5 * power(
                            2::numeric,
                            LEAST(task_record.attempt_count - 1, 8)
                        )
                    )::integer
                )
            );
        END IF;
        outcome_result_status := CASE next_status
            WHEN 'superseded' THEN 'stale_input'
            WHEN 'cancelled' THEN 'cancelled'
            WHEN 'policy_paused' THEN 'policy_paused'
            ELSE 'unavailable'
        END;
        outcome_error_code := CASE next_status
            WHEN 'superseded' THEN 'evidence.stale'
            WHEN 'policy_paused' THEN 'authorization.policy_paused'
            ELSE 'worker.lease_expired'
        END;
        outcome_error_class := CASE next_status
            WHEN 'superseded' THEN 'evidence'
            WHEN 'policy_paused' THEN 'authorization'
            ELSE 'worker'
        END;
        outcome_retry_class := memoriesql.semantic_retry_class_for_status(
            outcome_result_status
        );

        PERFORM memoriesql.append_semantic_task_event(
            task_record.tenant_id, task_record.task_id,
            (
                SELECT attempt.attempt_id
                FROM memoriesql.semantic_task_attempts AS attempt
                WHERE attempt.tenant_id = task_record.tenant_id
                  AND attempt.task_id = task_record.task_id
                  AND attempt.lease_generation = task_record.lease_generation
            ),
            'heartbeat_missed', context_record.principal_id,
            requested_worker_id, requested_at,
            jsonb_build_object('lease_generation', task_record.lease_generation)
        );
        PERFORM memoriesql.append_semantic_task_event(
            task_record.tenant_id, task_record.task_id,
            (
                SELECT attempt.attempt_id
                FROM memoriesql.semantic_task_attempts AS attempt
                WHERE attempt.tenant_id = task_record.tenant_id
                  AND attempt.task_id = task_record.task_id
                  AND attempt.lease_generation = task_record.lease_generation
            ),
            next_event, context_record.principal_id, requested_worker_id,
            requested_at,
            jsonb_strip_nulls(jsonb_build_object(
                'reason_code', outcome_error_code,
                'result_status', outcome_result_status,
                'retry_class', outcome_retry_class,
                'available_at', CASE
                    WHEN next_status = 'retry_wait' THEN next_available_at
                    ELSE NULL
                END
            ))
        );

        UPDATE memoriesql.semantic_task_attempts
           SET status = 'lease_lost',
               finished_at = database_now,
               result_status = outcome_result_status,
               error_code = outcome_error_code,
               error_class = outcome_error_class,
               retry_class = outcome_retry_class,
               retry_after = CASE
                   WHEN next_status = 'retry_wait' THEN next_available_at
                   ELSE NULL
               END
         WHERE tenant_id = task_record.tenant_id
           AND task_id = task_record.task_id
           AND lease_generation = task_record.lease_generation
           AND status IN ('claimed', 'running');

        PERFORM memoriesql.release_semantic_task_slot(
            task_record.tenant_id, task_record.task_id,
            (
                SELECT attempt.attempt_id
                FROM memoriesql.semantic_task_attempts AS attempt
                WHERE attempt.tenant_id = task_record.tenant_id
                  AND attempt.task_id = task_record.task_id
                  AND attempt.lease_generation = task_record.lease_generation
            ),
            database_now
        );
        UPDATE memoriesql.semantic_tasks
           SET status = next_status,
               pause_reason_code = CASE
                   WHEN next_status = 'policy_paused'
                       THEN outcome_error_code
                   ELSE NULL
               END,
               available_at = next_available_at,
               lease_owner = NULL,
               worker_instance_id = NULL,
               lease_expires_at = NULL,
               heartbeat_at = NULL,
               result_attempt_id = (
                   SELECT attempt.attempt_id
                   FROM memoriesql.semantic_task_attempts AS attempt
                   WHERE attempt.tenant_id = task_record.tenant_id
                     AND attempt.task_id = task_record.task_id
                     AND attempt.lease_generation = task_record.lease_generation
               ),
               result_ref = NULL,
               result_hash = NULL,
               updated_at = GREATEST(updated_at, database_now),
               completed_at = CASE
                   WHEN next_status IN ('cancelled', 'dead_letter', 'superseded')
                       THEN database_now
                   ELSE NULL
               END
         WHERE tenant_id = task_record.tenant_id
           AND task_id = task_record.task_id;
        reaped_count := reaped_count + 1;
    END LOOP;
    RETURN reaped_count;
END;
$$;

CREATE FUNCTION memoriesql.cancel_semantic_task(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_reason text,
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
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR requested_reason IS NULL
       OR length(requested_reason) > 128
       OR requested_reason !~
          '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN 'unavailable';
    END IF;
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      )
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'unavailable';
    END IF;

    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_semantic_task_authorized(
           requested_task_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       ) THEN
        RETURN 'unavailable';
    END IF;
    IF task_record.status IN (
        'succeeded', 'cancelled', 'failed_terminal', 'dead_letter', 'superseded'
    ) THEN
        RETURN task_record.status;
    END IF;
    IF task_record.cancel_requested_at IS NOT NULL THEN
        RETURN 'cancel_requested';
    END IF;

    UPDATE memoriesql.semantic_tasks
       SET cancel_requested_at = database_now,
           cancel_requested_by = context_record.principal_id,
           cancel_reason = requested_reason,
           updated_at = GREATEST(updated_at, database_now)
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    PERFORM memoriesql.append_semantic_task_event(
        requested_tenant_id, requested_task_id, NULL,
        'cancel_requested', context_record.principal_id, NULL, requested_at,
        jsonb_build_object('reason_code', requested_reason)
    );
    PERFORM pg_catalog.pg_notify('memoriesql_semantic_tasks', requested_task_id::text);

    IF task_record.status = 'running' THEN
        RETURN 'cancel_requested';
    END IF;
    PERFORM memoriesql.append_semantic_task_event(
        requested_tenant_id, requested_task_id, NULL, 'cancelled',
        context_record.principal_id, NULL, requested_at,
        jsonb_build_object('reason_code', requested_reason)
    );
    UPDATE memoriesql.semantic_tasks
       SET status = 'cancelled',
           pause_reason_code = NULL,
           lease_owner = NULL,
           worker_instance_id = NULL,
           lease_expires_at = NULL,
           heartbeat_at = NULL,
           result_hash = NULL,
           updated_at = GREATEST(updated_at, database_now),
           completed_at = database_now
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    RETURN 'cancelled';
END;
$$;

CREATE FUNCTION memoriesql.set_semantic_module_queue_state(
    requested_owning_module text,
    requested_state text,
    requested_reason_code text,
    cancel_running boolean,
    requested_at timestamp with time zone
)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    prior_state text;
    changed_count integer := 0;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR NOT memoriesql.current_context_has_capability('memory.maintain')
       OR btrim(requested_owning_module) = ''
       OR requested_state NOT IN ('enabled', 'draining', 'disabled')
       OR cancel_running IS NULL
       OR requested_reason_code IS NULL
       OR length(requested_reason_code) > 128
       OR requested_reason_code !~
          '^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$'
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'semantic module queue control is invalid'
            USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            context_record.tenant_id::text
                || ':' || context_record.workspace_id::text
                || ':semantic_module:' || requested_owning_module,
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_has_capability('memory.maintain') THEN
        RAISE EXCEPTION 'semantic module queue control is invalid'
            USING ERRCODE = '42501';
    END IF;
    SELECT control.state INTO prior_state
    FROM memoriesql.semantic_module_queue_controls AS control
    WHERE control.tenant_id = context_record.tenant_id
      AND control.workspace_id = context_record.workspace_id
      AND control.owning_module = requested_owning_module;
    INSERT INTO memoriesql.semantic_module_queue_controls (
        tenant_id, workspace_id, owning_module, state, revision,
        reason_code, changed_by_principal_id, changed_at
    ) VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_owning_module, requested_state, 1,
        requested_reason_code, context_record.principal_id, database_now
    )
    ON CONFLICT (tenant_id, workspace_id, owning_module) DO UPDATE
       SET state = EXCLUDED.state,
           revision = semantic_module_queue_controls.revision + 1,
           reason_code = EXCLUDED.reason_code,
           changed_by_principal_id = EXCLUDED.changed_by_principal_id,
           changed_at = EXCLUDED.changed_at;

    IF requested_state = 'disabled' THEN
        FOR task_record IN
            SELECT task.*
            FROM memoriesql.semantic_tasks AS task
            WHERE task.tenant_id = context_record.tenant_id
              AND task.workspace_id = context_record.workspace_id
              AND task.owning_module = requested_owning_module
              AND task.status IN ('queued', 'retry_wait')
              AND memoriesql.current_context_scope_authorized(
                  task.access_scope_id, 'memory.maintain', 'read'
              )
            FOR UPDATE
        LOOP
            database_now := pg_catalog.clock_timestamp();
            IF context_record.expires_at <= database_now
               OR requested_at < database_now - interval '5 minutes'
               OR requested_at > database_now + interval '5 minutes'
               OR NOT memoriesql.current_context_scope_authorized(
                   task_record.access_scope_id, 'memory.maintain', 'read'
               )
               OR NOT memoriesql.current_context_scope_time_authorized(
                   task_record.access_scope_id, 'read', database_now
               ) THEN
                CONTINUE;
            END IF;
            PERFORM memoriesql.append_semantic_task_event(
                task_record.tenant_id, task_record.task_id, NULL,
                'policy_paused', context_record.principal_id, NULL,
                requested_at,
                jsonb_build_object('reason_code', 'module.disabled')
            );
            UPDATE memoriesql.semantic_tasks
               SET status = 'policy_paused',
                   pause_reason_code = 'module.disabled',
                   updated_at = GREATEST(updated_at, database_now)
             WHERE tenant_id = task_record.tenant_id
               AND task_id = task_record.task_id;
            changed_count := changed_count + 1;
        END LOOP;
        IF cancel_running THEN
            FOR task_record IN
                SELECT task.*
                FROM memoriesql.semantic_tasks AS task
                WHERE task.tenant_id = context_record.tenant_id
                  AND task.workspace_id = context_record.workspace_id
                  AND task.owning_module = requested_owning_module
                  AND task.status = 'running'
                  AND task.cancel_requested_at IS NULL
                  AND memoriesql.current_context_scope_authorized(
                      task.access_scope_id, 'memory.maintain', 'read'
                  )
                FOR UPDATE
            LOOP
                database_now := pg_catalog.clock_timestamp();
                IF context_record.expires_at <= database_now
                   OR requested_at < database_now - interval '5 minutes'
                   OR requested_at > database_now + interval '5 minutes'
                   OR NOT memoriesql.current_context_scope_authorized(
                       task_record.access_scope_id, 'memory.maintain', 'read'
                   )
                   OR NOT memoriesql.current_context_scope_time_authorized(
                       task_record.access_scope_id, 'read', database_now
                   ) THEN
                    CONTINUE;
                END IF;
                UPDATE memoriesql.semantic_tasks
                   SET cancel_requested_at = database_now,
                       cancel_requested_by = context_record.principal_id,
                       cancel_reason = 'module.disabled',
                       updated_at = GREATEST(updated_at, database_now)
                 WHERE tenant_id = task_record.tenant_id
                   AND task_id = task_record.task_id;
                PERFORM memoriesql.append_semantic_task_event(
                    task_record.tenant_id, task_record.task_id,
                    task_record.result_attempt_id, 'cancel_requested',
                    context_record.principal_id, NULL, requested_at,
                    jsonb_build_object('reason_code', 'module.disabled')
                );
                PERFORM pg_catalog.pg_notify(
                    'memoriesql_semantic_tasks', task_record.task_id::text
                );
                changed_count := changed_count + 1;
            END LOOP;
        END IF;
    ELSIF requested_state = 'enabled' THEN
        FOR task_record IN
            SELECT task.*
            FROM memoriesql.semantic_tasks AS task
            WHERE task.tenant_id = context_record.tenant_id
              AND task.workspace_id = context_record.workspace_id
              AND task.owning_module = requested_owning_module
              AND task.status = 'policy_paused'
              AND task.pause_reason_code = 'module.disabled'
              AND memoriesql.current_context_scope_authorized(
                  task.access_scope_id, 'memory.maintain', 'read'
              )
            FOR UPDATE
        LOOP
            database_now := pg_catalog.clock_timestamp();
            IF context_record.expires_at <= database_now
               OR requested_at < database_now - interval '5 minutes'
               OR requested_at > database_now + interval '5 minutes'
               OR NOT memoriesql.current_context_scope_authorized(
                   task_record.access_scope_id, 'memory.maintain', 'read'
               )
               OR NOT memoriesql.current_context_scope_time_authorized(
                   task_record.access_scope_id, 'read', database_now
               )
               OR NOT memoriesql.semantic_task_origin_authorized(
                   task_record.tenant_id, task_record.task_id, database_now
               ) THEN
                CONTINUE;
            END IF;
            PERFORM memoriesql.append_semantic_task_event(
                task_record.tenant_id, task_record.task_id, NULL, 'resumed',
                context_record.principal_id, NULL, requested_at,
                jsonb_build_object('reason_code', requested_reason_code)
            );
            UPDATE memoriesql.semantic_tasks
               SET status = 'queued',
                   pause_reason_code = NULL,
                   available_at = GREATEST(available_at, database_now),
                   updated_at = GREATEST(updated_at, database_now)
             WHERE tenant_id = task_record.tenant_id
               AND task_id = task_record.task_id;
            PERFORM pg_catalog.pg_notify(
                'memoriesql_semantic_tasks', task_record.task_id::text
            );
            changed_count := changed_count + 1;
        END LOOP;
        IF prior_state = 'draining' THEN
            FOR task_record IN
                SELECT task.*
                FROM memoriesql.semantic_tasks AS task
                WHERE task.tenant_id = context_record.tenant_id
                  AND task.workspace_id = context_record.workspace_id
                  AND task.owning_module = requested_owning_module
                  AND task.status IN ('queued', 'retry_wait')
                  AND task.available_at <= database_now
                  AND task.cancel_requested_at IS NULL
                  AND task.attempt_count < task.max_attempts
                  AND memoriesql.current_context_scope_authorized(
                      task.access_scope_id, 'memory.maintain', 'read'
                  )
            LOOP
                database_now := pg_catalog.clock_timestamp();
                IF context_record.expires_at <= database_now
                   OR NOT memoriesql.current_context_scope_authorized(
                       task_record.access_scope_id, 'memory.maintain', 'read'
                   )
                   OR NOT memoriesql.current_context_scope_time_authorized(
                       task_record.access_scope_id, 'read', database_now
                   ) THEN
                    CONTINUE;
                END IF;
                PERFORM pg_catalog.pg_notify(
                    'memoriesql_semantic_tasks', task_record.task_id::text
                );
            END LOOP;
        END IF;
    END IF;
    RETURN changed_count;
END;
$$;

CREATE FUNCTION memoriesql.resume_semantic_task(
    requested_tenant_id uuid,
    requested_task_id uuid,
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
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_semantic_task_authorized(
           requested_task_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.semantic_task_origin_authorized(
           requested_tenant_id, requested_task_id, database_now
       ) THEN
        RETURN false;
    END IF;
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'policy_paused'
      AND task.attempt_count < task.max_attempts
      AND COALESCE(
          (
              SELECT control.state
              FROM memoriesql.semantic_module_queue_controls AS control
              WHERE control.tenant_id = task.tenant_id
                AND control.workspace_id = task.workspace_id
                AND control.owning_module = task.owning_module
          ),
          'enabled'
      ) = 'enabled';
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    -- Module control owns the exclusive form of this key before it changes
    -- admission and then locks tasks. Take the shared form first so resume has
    -- the same module-key -> task-row order and a stable admission decision.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(
            task_record.tenant_id::text
                || ':' || task_record.workspace_id::text
                || ':semantic_module:' || task_record.owning_module,
            0
        )
    );
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND task.status = 'policy_paused'
      AND task.attempt_count < task.max_attempts
      AND COALESCE(
          (
              SELECT control.state
              FROM memoriesql.semantic_module_queue_controls AS control
              WHERE control.tenant_id = task.tenant_id
                AND control.workspace_id = task.workspace_id
                AND control.owning_module = task.owning_module
          ),
          'enabled'
      ) = 'enabled'
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    -- A revocation or expiry may commit while this call waits for the task row.
    -- Refresh wall-clock time and revalidate both caller and origin authority
    -- after the lock before changing the durable queue state.
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_semantic_task_authorized(
           requested_task_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       )
       OR NOT memoriesql.semantic_task_origin_authorized(
           requested_tenant_id, requested_task_id, database_now
       )
       OR COALESCE(
           (
               SELECT control.state
               FROM memoriesql.semantic_module_queue_controls AS control
               WHERE control.tenant_id = task_record.tenant_id
                 AND control.workspace_id = task_record.workspace_id
                 AND control.owning_module = task_record.owning_module
           ),
           'enabled'
       ) <> 'enabled' THEN
        RETURN false;
    END IF;
    PERFORM memoriesql.append_semantic_task_event(
        requested_tenant_id, requested_task_id, NULL, 'resumed',
        context_record.principal_id, NULL, requested_at,
        jsonb_build_object('reason_code', 'policy.restored')
    );
    UPDATE memoriesql.semantic_tasks
       SET status = 'queued',
           pause_reason_code = NULL,
           available_at = GREATEST(available_at, database_now),
           updated_at = GREATEST(updated_at, database_now)
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    PERFORM pg_catalog.pg_notify('memoriesql_semantic_tasks', requested_task_id::text);
    RETURN true;
END;
$$;

CREATE FUNCTION memoriesql.retire_semantic_task_evidence(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_disposition text,
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
    next_status text;
    next_event text;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR requested_disposition IS NULL
       OR requested_disposition NOT IN ('deleted', 'stale')
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RETURN 'unavailable';
    END IF;
    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = requested_tenant_id
      AND task.task_id = requested_task_id
      AND memoriesql.current_context_semantic_task_authorized(
          task.task_id, 'memory.maintain', 'read'
      )
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'unavailable';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes'
       OR NOT memoriesql.current_context_semantic_task_authorized(
           requested_task_id, 'memory.maintain', 'read'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
           task_record.access_scope_id, 'read', database_now
       ) THEN
        RETURN 'unavailable';
    END IF;
    IF task_record.status = 'running' THEN
        IF task_record.cancel_requested_at IS NOT NULL
           AND (
               task_record.cancel_reason = 'evidence.stale'
               OR requested_disposition = 'deleted'
           ) THEN
            RETURN 'cancel_requested';
        END IF;
        UPDATE memoriesql.semantic_tasks
           SET cancel_requested_at = database_now,
               cancel_requested_by = context_record.principal_id,
               cancel_reason = 'evidence.' || requested_disposition,
               updated_at = GREATEST(updated_at, database_now)
         WHERE tenant_id = requested_tenant_id
           AND task_id = requested_task_id;
        PERFORM memoriesql.append_semantic_task_event(
            requested_tenant_id, requested_task_id, NULL, 'cancel_requested',
            context_record.principal_id, NULL, requested_at,
            jsonb_build_object(
                'reason_code', 'evidence.' || requested_disposition
            )
        );
        PERFORM pg_catalog.pg_notify(
            'memoriesql_semantic_tasks', requested_task_id::text
        );
        RETURN 'cancel_requested';
    END IF;
    IF task_record.status NOT IN ('queued', 'retry_wait', 'policy_paused') THEN
        RETURN task_record.status;
    END IF;
    next_status := CASE
        WHEN requested_disposition = 'deleted' THEN 'cancelled'
        ELSE 'superseded'
    END;
    next_event := CASE
        WHEN requested_disposition = 'deleted' THEN 'cancelled'
        ELSE 'superseded'
    END;
    PERFORM memoriesql.append_semantic_task_event(
        requested_tenant_id, requested_task_id, NULL, next_event,
        context_record.principal_id, NULL, requested_at,
        jsonb_build_object(
            'reason_code', 'evidence.' || requested_disposition
        )
    );
    UPDATE memoriesql.semantic_tasks
       SET status = next_status,
           pause_reason_code = NULL,
           lease_owner = NULL,
           worker_instance_id = NULL,
           lease_expires_at = NULL,
           heartbeat_at = NULL,
           result_hash = NULL,
           updated_at = GREATEST(updated_at, database_now),
           completed_at = database_now
     WHERE tenant_id = requested_tenant_id
       AND task_id = requested_task_id;
    RETURN next_status;
END;
$$;

CREATE INDEX semantic_tasks_ready_claim_idx
ON memoriesql.semantic_tasks (
    tenant_id, workspace_id, status, queue_name, available_at,
    base_priority DESC, created_at, task_id
)
WHERE status IN ('queued', 'retry_wait');
CREATE INDEX semantic_tasks_active_lease_idx
ON memoriesql.semantic_tasks (
    tenant_id, workspace_id, lease_expires_at, task_id
)
WHERE status = 'running';
CREATE INDEX semantic_tasks_target_idx
ON memoriesql.semantic_tasks (
    tenant_id, workspace_id, target_kind, target_reference,
    expected_target_revision, created_at
);
CREATE INDEX semantic_tasks_origin_idx
ON memoriesql.semantic_tasks (
    tenant_id, origin_principal_id, origin_pairing_grant_id, status, created_at
);
CREATE INDEX semantic_tasks_dead_letter_idx
ON memoriesql.semantic_tasks (
    tenant_id, workspace_id, queue_name, task_kind, completed_at, task_id
)
WHERE status = 'dead_letter';
CREATE INDEX semantic_task_attempts_task_history_idx
ON memoriesql.semantic_task_attempts (
    tenant_id, task_id, attempt_number DESC, attempt_id
);
CREATE INDEX semantic_task_attempts_active_lease_idx
ON memoriesql.semantic_task_attempts (
    tenant_id, lease_expires_at, task_id, attempt_id
)
WHERE status IN ('claimed', 'running');
CREATE INDEX semantic_task_events_order_idx
ON memoriesql.semantic_task_events (
    tenant_id, task_id, event_sequence, event_id
);
CREATE INDEX semantic_concurrency_slots_lease_idx
ON memoriesql.semantic_concurrency_slots (
    tenant_id, workspace_id, lease_expires_at, concurrency_key, slot_number
)
WHERE task_id IS NOT NULL;

CREATE VIEW memoriesql.semantic_tasks_ready
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
    task.base_priority,
    task.base_priority::bigint + LEAST(
        4294967296::numeric,
        floor(
            EXTRACT(EPOCH FROM (statement_timestamp() - task.created_at)) / 30
        )
    )::bigint AS effective_priority,
    task.available_at,
    task.attempt_count,
    task.max_attempts,
    task.created_at
FROM memoriesql.semantic_tasks AS task
LEFT JOIN memoriesql.semantic_module_queue_controls AS control
  ON control.tenant_id = task.tenant_id
 AND control.workspace_id = task.workspace_id
 AND control.owning_module = task.owning_module
WHERE task.status IN ('queued', 'retry_wait')
  AND task.available_at <= statement_timestamp()
  AND task.cancel_requested_at IS NULL
  AND memoriesql.current_context_semantic_task_origin_authorized(
      task.task_id, statement_timestamp()
  )
  AND COALESCE(control.state, 'enabled') = 'enabled';

CREATE VIEW memoriesql.semantic_tasks_active
WITH (security_invoker = true)
AS
SELECT
    task.tenant_id,
    task.workspace_id,
    task.access_scope_id,
    task.task_id,
    task.owning_module,
    task.task_kind,
    task.queue_name,
    task.attempt_count,
    task.lease_owner,
    task.worker_instance_id,
    task.lease_generation,
    task.heartbeat_at,
    task.lease_expires_at,
    task.cancel_requested_at,
    task.started_at
FROM memoriesql.semantic_tasks AS task
WHERE task.status = 'running';

CREATE VIEW memoriesql.semantic_task_dead_letters
WITH (security_invoker = true)
AS
SELECT
    task.tenant_id,
    task.workspace_id,
    task.access_scope_id,
    task.task_id,
    task.owning_module,
    task.task_kind,
    task.queue_name,
    task.attempt_count,
    task.max_attempts,
    task.result_attempt_id,
    task.completed_at
FROM memoriesql.semantic_tasks AS task
WHERE task.status = 'dead_letter';

CREATE VIEW memoriesql.semantic_queue_health
WITH (security_invoker = true)
AS
SELECT
    task.tenant_id,
    task.workspace_id,
    task.queue_name,
    task.task_kind,
    count(*) FILTER (
        WHERE task.status IN ('queued', 'retry_wait')
          AND task.available_at <= statement_timestamp()
          AND task.cancel_requested_at IS NULL
          AND memoriesql.current_context_semantic_task_origin_authorized(
              task.task_id, statement_timestamp()
          )
          AND COALESCE(control.state, 'enabled') = 'enabled'
    ) AS ready_count,
    count(*) FILTER (WHERE task.status = 'running') AS running_count,
    count(*) FILTER (WHERE task.status = 'policy_paused') AS paused_count,
    count(*) FILTER (WHERE task.status = 'dead_letter') AS dead_letter_count,
    count(*) FILTER (
        WHERE task.status = 'running'
          AND task.lease_expires_at <= statement_timestamp()
    ) AS expired_lease_count,
    min(task.created_at) FILTER (
        WHERE task.status IN ('queued', 'retry_wait')
          AND task.available_at <= statement_timestamp()
          AND task.cancel_requested_at IS NULL
          AND memoriesql.current_context_semantic_task_origin_authorized(
              task.task_id, statement_timestamp()
          )
          AND COALESCE(control.state, 'enabled') = 'enabled'
    ) AS oldest_ready_at
FROM memoriesql.semantic_tasks AS task
LEFT JOIN memoriesql.semantic_module_queue_controls AS control
  ON control.tenant_id = task.tenant_id
 AND control.workspace_id = task.workspace_id
 AND control.owning_module = task.owning_module
GROUP BY task.tenant_id, task.workspace_id, task.queue_name, task.task_kind;

CREATE VIEW memoriesql.semantic_task_attempt_history
WITH (security_invoker = true)
AS
SELECT
    attempt.tenant_id,
    attempt.workspace_id,
    attempt.access_scope_id,
    attempt.task_id,
    attempt.attempt_id,
    attempt.attempt_number,
    attempt.lease_generation,
    attempt.claimant_principal_id,
    attempt.worker_id,
    attempt.worker_instance_id,
    attempt.status,
    attempt.result_status,
    attempt.error_code,
    attempt.error_class,
    attempt.retry_class,
    attempt.retry_after,
    attempt.claimed_at,
    attempt.started_at,
    attempt.finished_at
FROM memoriesql.semantic_task_attempts AS attempt;

CREATE VIEW memoriesql.semantic_task_run_tree
WITH (security_invoker = true)
AS
SELECT
    attempt.tenant_id,
    attempt.workspace_id,
    attempt.access_scope_id,
    attempt.task_id,
    attempt.attempt_id,
    attempt.attempt_id AS run_id,
    NULL::uuid AS parent_run_id,
    'receipt_only_attempt'::text AS run_role,
    attempt.attempt_number AS sibling_order,
    attempt.status,
    attempt.claimed_at,
    attempt.finished_at
FROM memoriesql.semantic_task_attempts AS attempt;

-- The catalog is migration-owned and has no product-role table privileges.
-- This policy exists only so SECURITY DEFINER queue functions can read the
-- immutable allowlist while FORCE ROW LEVEL SECURITY remains universal.
CREATE POLICY semantic_task_admission_policies_definer_read
ON memoriesql.semantic_task_admission_policies
FOR SELECT
USING (true);
CREATE POLICY semantic_worker_claim_policies_definer_read
ON memoriesql.semantic_worker_claim_policies
FOR SELECT
USING (true);
CREATE POLICY semantic_tasks_read
ON memoriesql.semantic_tasks
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_scope_authorized(
        access_scope_id, 'memory.maintain', 'read'
    )
);
CREATE POLICY semantic_task_attempts_read
ON memoriesql.semantic_task_attempts
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_semantic_task_authorized(
        task_id, 'memory.maintain', 'read'
    )
);
CREATE POLICY semantic_task_events_read
ON memoriesql.semantic_task_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_semantic_task_authorized(
        task_id, 'memory.maintain', 'read'
    )
);
CREATE POLICY semantic_concurrency_slots_read
ON memoriesql.semantic_concurrency_slots
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = semantic_concurrency_slots.tenant_id
          AND context.workspace_id = semantic_concurrency_slots.workspace_id
          AND memoriesql.current_context_has_capability('memory.maintain')
    )
);
CREATE POLICY semantic_module_queue_controls_read
ON memoriesql.semantic_module_queue_controls
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = semantic_module_queue_controls.tenant_id
          AND context.workspace_id = semantic_module_queue_controls.workspace_id
          AND memoriesql.current_context_has_capability('memory.maintain')
    )
);

ALTER TABLE memoriesql.semantic_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_admission_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_admission_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_worker_claim_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_worker_claim_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_concurrency_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_concurrency_slots FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_module_queue_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_module_queue_controls FORCE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA memoriesql FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memoriesql FROM PUBLIC;

GRANT SELECT ON memoriesql.semantic_tasks TO memoriesql_application;
GRANT SELECT (
    tenant_id, workspace_id, access_scope_id, task_id, owning_module,
    task_kind, contract_revision, task_contract_hash, semantic_registry_hash,
    target_kind, target_reference, expected_target_revision, input_hash,
    evidence_manifest_id, evidence_manifest_hash, origin_principal_id,
    origin_pairing_grant_id, required_capability, accepted_policy_revision_id,
    queue_name, base_priority, available_at, status, pause_reason_code,
    attempt_count, max_attempts, lease_owner, worker_instance_id,
    lease_generation, lease_expires_at, heartbeat_at, cancel_requested_at,
    result_attempt_id, result_ref, result_hash, rerun_of_task_id,
    superseded_by_task_id, concurrency_key, concurrency_limit,
    last_event_sequence, created_at, updated_at, started_at, completed_at
) ON memoriesql.semantic_tasks TO memoriesql_worker;
GRANT SELECT ON memoriesql.semantic_task_attempts
    TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.semantic_task_events
    TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.semantic_concurrency_slots
    TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.semantic_module_queue_controls
    TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.semantic_tasks_ready,
    memoriesql.semantic_tasks_active,
    memoriesql.semantic_task_dead_letters,
    memoriesql.semantic_queue_health,
    memoriesql.semantic_task_attempt_history,
    memoriesql.semantic_task_run_tree
TO memoriesql_application, memoriesql_worker;

GRANT EXECUTE ON FUNCTION
    memoriesql.current_context_semantic_task_origin_authorized(
        uuid, timestamp with time zone
    )
TO memoriesql_application, memoriesql_worker;

GRANT EXECUTE ON FUNCTION memoriesql.enqueue_semantic_task(
    uuid, text, text, text, integer, text, text, text, integer,
    jsonb, text, uuid,
    timestamp with time zone, uuid,
    timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.cancel_semantic_task(
    uuid, uuid, text, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.set_semantic_module_queue_state(
    text, text, text, boolean, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.resume_semantic_task(
    uuid, uuid, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.retire_semantic_task_evidence(
    uuid, uuid, text, timestamp with time zone
) TO memoriesql_application;

GRANT EXECUTE ON FUNCTION memoriesql.claim_semantic_task(
    text, text, integer, integer, integer,
    timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.start_semantic_task_attempt(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.heartbeat_semantic_task(
    uuid, uuid, uuid, bigint, text, text, integer,
    timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.reauthorize_semantic_task(
    uuid, uuid, uuid, bigint, text, text, text,
    timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.hydrate_semantic_task_input(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.record_semantic_task_outcome(
    uuid, uuid, uuid, bigint, text, text, text, text, text, text, text,
    text, integer, integer, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.reap_expired_semantic_tasks(
    text, integer, timestamp with time zone
) TO memoriesql_worker;

COMMENT ON TABLE memoriesql.semantic_tasks IS
    'One durable logical semantic task; identity/input are immutable and scheduling state is generation-fenced.';
COMMENT ON COLUMN memoriesql.semantic_tasks.input_payload IS
    'Contract-bounded scalar/ID input only; raw source bodies, prompts, and model prose are rejected.';
COMMENT ON TABLE memoriesql.semantic_task_attempts IS
    'Lease attempt history bound to its authenticated claimant principal; identity is immutable and terminal outcomes cannot be rewritten.';
COMMENT ON TABLE memoriesql.semantic_task_events IS
    'Append-only bounded lifecycle audit without source bodies or model prose.';
COMMENT ON TABLE memoriesql.semantic_concurrency_slots IS
    'Optional Postgres-leased cross-process concurrency slots renewed and released with task fences.';
COMMENT ON VIEW memoriesql.semantic_task_run_tree IS
    'Receipt-only attempt roots in PR-01D; PR-01F owns model run-tree persistence and may extend this projection.';
COMMENT ON FUNCTION memoriesql.hydrate_semantic_task_input(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) IS
    'Separate post-claim input hydration seam that returns data only after claimant-principal and origin reauthorization.';
COMMENT ON FUNCTION memoriesql.claim_semantic_task(
    text, text, integer, integer, integer,
    timestamp with time zone
) IS
    'Metadata-only SKIP LOCKED claim constrained by credential-derived worker policy, with an atomic generation lease and optional concurrency slot.';
