CREATE TABLE memoriesql.idempotency_receipts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    operation_kind text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL,
    resource_kind text,
    resource_id uuid,
    response_receipt jsonb,
    attempt_count integer NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT idempotency_receipts_pk
        PRIMARY KEY (tenant_id, idempotency_receipt_id),
    CONSTRAINT idempotency_receipts_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        idempotency_receipt_id
    ),
    CONSTRAINT idempotency_receipts_operation_key_uq
        UNIQUE (tenant_id, operation_kind, idempotency_key),
    CONSTRAINT idempotency_receipts_operation_nonempty
        CHECK (btrim(operation_kind) <> ''),
    CONSTRAINT idempotency_receipts_key_nonempty
        CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT idempotency_receipts_request_hash_nonempty
        CHECK (btrim(request_hash) <> ''),
    CONSTRAINT idempotency_receipts_status_supported CHECK (
        status IN (
            'in_progress',
            'succeeded',
            'failed_retryable',
            'failed_terminal'
        )
    ),
    CONSTRAINT idempotency_receipts_resource_pair CHECK (
        (resource_kind IS NULL AND resource_id IS NULL)
        OR (resource_kind IS NOT NULL AND resource_id IS NOT NULL)
    ),
    CONSTRAINT idempotency_receipts_resource_kind_nonempty
        CHECK (resource_kind IS NULL OR btrim(resource_kind) <> ''),
    CONSTRAINT idempotency_receipts_response_object CHECK (
        response_receipt IS NULL OR jsonb_typeof(response_receipt) = 'object'
    ),
    CONSTRAINT idempotency_receipts_attempt_positive CHECK (attempt_count > 0),
    CONSTRAINT idempotency_receipts_lease_pair CHECK (
        (lease_owner IS NULL AND lease_expires_at IS NULL)
        OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT idempotency_receipts_lease_owner_nonempty
        CHECK (lease_owner IS NULL OR btrim(lease_owner) <> ''),
    CONSTRAINT idempotency_receipts_time_order CHECK (
        updated_at >= created_at
        AND (completed_at IS NULL OR completed_at >= created_at)
    ),
    CONSTRAINT idempotency_receipts_terminal_completion CHECK (
        (status IN ('succeeded', 'failed_terminal') AND completed_at IS NOT NULL)
        OR (status IN ('in_progress', 'failed_retryable') AND completed_at IS NULL)
    )
);

CREATE INDEX idempotency_receipts_status_lease_idx
ON memoriesql.idempotency_receipts (
    tenant_id,
    status,
    lease_expires_at,
    updated_at
);

ALTER TABLE memoriesql.source_units
ADD COLUMN processing_receipt_id uuid;

ALTER TABLE memoriesql.source_units
ADD CONSTRAINT source_units_processing_receipt_fk FOREIGN KEY (
    tenant_id,
    workspace_id,
    access_scope_id,
    processing_receipt_id
) REFERENCES memoriesql.idempotency_receipts (
    tenant_id,
    workspace_id,
    access_scope_id,
    idempotency_receipt_id
) DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX source_units_processing_receipt_idx
ON memoriesql.source_units (tenant_id, processing_receipt_id)
WHERE processing_receipt_id IS NOT NULL;

CREATE TABLE memoriesql.semantic_task_receipts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    semantic_task_receipt_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    task_contract_key text NOT NULL,
    task_contract_version integer NOT NULL,
    input_hash text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT semantic_task_receipts_pk
        PRIMARY KEY (tenant_id, semantic_task_receipt_id),
    CONSTRAINT semantic_task_receipts_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        semantic_task_receipt_id
    ),
    CONSTRAINT semantic_task_receipts_dedupe_uq UNIQUE (
        tenant_id,
        idempotency_receipt_id,
        bead_id,
        task_contract_key,
        task_contract_version
    ),
    CONSTRAINT semantic_task_receipts_idempotency_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id,
        workspace_id,
        access_scope_id,
        idempotency_receipt_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT semantic_task_receipts_bead_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        bead_id
    ) REFERENCES memoriesql.beads (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        bead_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT semantic_task_receipts_contract_key_nonempty
        CHECK (btrim(task_contract_key) <> ''),
    CONSTRAINT semantic_task_receipts_contract_version_positive
        CHECK (task_contract_version > 0),
    CONSTRAINT semantic_task_receipts_input_hash_nonempty
        CHECK (btrim(input_hash) <> '')
);

CREATE INDEX semantic_task_receipts_target_idx
ON memoriesql.semantic_task_receipts (
    tenant_id,
    bead_id,
    task_contract_key,
    created_at
);

CREATE TABLE memoriesql.outbox_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    outbox_event_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    aggregate_kind text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_kind text NOT NULL,
    payload jsonb NOT NULL,
    headers jsonb NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    available_at timestamp with time zone,
    CONSTRAINT outbox_events_pk PRIMARY KEY (tenant_id, outbox_event_id),
    CONSTRAINT outbox_events_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        outbox_event_id
    ),
    CONSTRAINT outbox_events_operation_event_uq UNIQUE (
        tenant_id,
        idempotency_receipt_id,
        aggregate_kind,
        aggregate_id,
        event_kind
    ),
    CONSTRAINT outbox_events_idempotency_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id,
        workspace_id,
        access_scope_id,
        idempotency_receipt_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT outbox_events_aggregate_kind_nonempty
        CHECK (btrim(aggregate_kind) <> ''),
    CONSTRAINT outbox_events_event_kind_nonempty CHECK (btrim(event_kind) <> ''),
    CONSTRAINT outbox_events_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT outbox_events_headers_object CHECK (jsonb_typeof(headers) = 'object'),
    CONSTRAINT outbox_events_availability_order
        CHECK (available_at IS NULL OR available_at >= recorded_at)
);

CREATE INDEX outbox_events_recorded_order_idx
ON memoriesql.outbox_events (tenant_id, recorded_at, outbox_event_id);

CREATE FUNCTION memoriesql.protect_idempotency_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        OLD.tenant_id,
        OLD.workspace_id,
        OLD.access_scope_id,
        OLD.idempotency_receipt_id,
        OLD.operation_kind,
        OLD.idempotency_key,
        OLD.request_hash,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.tenant_id,
        NEW.workspace_id,
        NEW.access_scope_id,
        NEW.idempotency_receipt_id,
        NEW.operation_kind,
        NEW.idempotency_key,
        NEW.request_hash,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'idempotency receipt identity and request hash are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.status IN ('succeeded', 'failed_terminal')
       AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal idempotency receipt outcomes are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.attempt_count < OLD.attempt_count OR NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'idempotency receipt progress cannot move backward'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.require_open_idempotency_receipt()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    receipt_status text;
BEGIN
    SELECT receipt.status
      INTO receipt_status
      FROM memoriesql.idempotency_receipts AS receipt
     WHERE receipt.tenant_id = NEW.tenant_id
       AND receipt.workspace_id = NEW.workspace_id
       AND receipt.access_scope_id = NEW.access_scope_id
       AND receipt.idempotency_receipt_id = NEW.idempotency_receipt_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'idempotency receipt % must exist before child insertion',
            NEW.idempotency_receipt_id
            USING ERRCODE = '23503';
    END IF;

    IF receipt_status IN ('succeeded', 'failed_terminal') THEN
        RAISE EXCEPTION 'terminal idempotency receipt % cannot accept new children',
            NEW.idempotency_receipt_id
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.require_open_source_unit_receipt()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    receipt_status text;
BEGIN
    IF NEW.is_observation THEN
        RETURN NEW;
    END IF;

    IF NEW.parent_unit_id IS NULL THEN
        RAISE EXCEPTION 'subordinate source unit % requires a parent',
            NEW.source_unit_id
            USING ERRCODE = '23514';
    END IF;

    IF NEW.processing_receipt_id IS NULL THEN
        RAISE EXCEPTION 'subordinate source unit % requires a processing receipt',
            NEW.source_unit_id
            USING ERRCODE = '23514';
    END IF;

    SELECT receipt.status
      INTO receipt_status
      FROM memoriesql.idempotency_receipts AS receipt
     WHERE receipt.tenant_id = NEW.tenant_id
       AND receipt.workspace_id = NEW.workspace_id
       AND receipt.access_scope_id = NEW.access_scope_id
       AND receipt.idempotency_receipt_id = NEW.processing_receipt_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'processing receipt % must exist before subordinate unit insertion',
            NEW.processing_receipt_id
            USING ERRCODE = '23503';
    END IF;

    IF receipt_status IN ('succeeded', 'failed_terminal') THEN
        RAISE EXCEPTION 'terminal processing receipt % cannot accept source units',
            NEW.processing_receipt_id
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER idempotency_receipts_protect_identity
BEFORE UPDATE ON memoriesql.idempotency_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.protect_idempotency_identity();
CREATE TRIGGER idempotency_receipts_no_delete
BEFORE DELETE ON memoriesql.idempotency_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_task_receipts_immutable
BEFORE UPDATE OR DELETE ON memoriesql.semantic_task_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_task_receipts_require_open_receipt
BEFORE INSERT ON memoriesql.semantic_task_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.require_open_idempotency_receipt();
CREATE TRIGGER outbox_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.outbox_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER outbox_events_require_open_receipt
BEFORE INSERT ON memoriesql.outbox_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.require_open_idempotency_receipt();
CREATE TRIGGER source_units_require_open_processing_receipt
BEFORE INSERT ON memoriesql.source_units
FOR EACH ROW EXECUTE FUNCTION memoriesql.require_open_source_unit_receipt();

ALTER TABLE memoriesql.idempotency_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.outbox_events ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE memoriesql.idempotency_receipts IS
    'Shared mutation and durable-consumer replay contract; feature-local receipts are forbidden.';
COMMENT ON TABLE memoriesql.semantic_task_receipts IS
    'Append-only task-intent substrate only; execution, leasing, and model runtime land later.';
COMMENT ON TABLE memoriesql.outbox_events IS
    'Append-only transactional outbox substrate without dispatch or worker state.';
