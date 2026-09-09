-- PR-02 canonical capture and rich semantic application: deterministic capture
-- acceptance, evidence-bound atomic statements, and one immutable authored
-- render whose clauses remain inspectably pinned to the active statement fold.

CREATE FUNCTION memoriesql.authored_bead_render_text(
    candidate jsonb,
    requested_section text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT CASE requested_section
        WHEN 'title' THEN candidate #>> '{title,text}'
        WHEN 'summary' THEN (
            SELECT string_agg(clause.value ->> 'text', ' ' ORDER BY clause.ordinality)
            FROM jsonb_array_elements(candidate -> 'summary')
                WITH ORDINALITY AS clause(value, ordinality)
        )
        WHEN 'detail' THEN NULLIF((
            SELECT string_agg(
                clause.value ->> 'text', E'\n\n' ORDER BY clause.ordinality
            )
            FROM jsonb_array_elements(candidate -> 'detail')
                WITH ORDINALITY AS clause(value, ordinality)
        ), '')
        ELSE NULL
    END
$$;

CREATE FUNCTION memoriesql.authored_render_text_nonempty(candidate text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT btrim(
        candidate,
        U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'
    ) <> ''
$$;

CREATE FUNCTION memoriesql.authored_bead_render_safe(candidate jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    clause jsonb;
    omission jsonb;
BEGIN
    IF candidate IS NULL OR jsonb_typeof(candidate) IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(candidate)) <> 4
       OR EXISTS (
            SELECT 1 FROM jsonb_object_keys(candidate) AS key(value)
            WHERE key.value <> ALL (ARRAY[
                'title', 'summary', 'detail', 'omissions'
            ]::text[])
       ) THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(candidate -> 'title') IS DISTINCT FROM 'object'
       OR jsonb_typeof(candidate -> 'summary') IS DISTINCT FROM 'array'
       OR jsonb_array_length(candidate -> 'summary') NOT BETWEEN 1 AND 3
       OR jsonb_typeof(candidate -> 'detail') IS DISTINCT FROM 'array'
       OR jsonb_array_length(candidate -> 'detail') > 8
       OR jsonb_typeof(candidate -> 'omissions') IS DISTINCT FROM 'array'
       OR jsonb_array_length(candidate -> 'omissions') > 64 THEN
        RETURN false;
    END IF;

    FOR clause IN
        SELECT candidate -> 'title'
        UNION ALL
        SELECT item.value FROM jsonb_array_elements(candidate -> 'summary') AS item(value)
        UNION ALL
        SELECT item.value FROM jsonb_array_elements(candidate -> 'detail') AS item(value)
    LOOP
        IF jsonb_typeof(clause) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(clause)) <> 2
           OR EXISTS (
                SELECT 1 FROM jsonb_object_keys(clause) AS key(value)
                WHERE key.value <> ALL (ARRAY['text', 'statement_ids']::text[])
           )
           OR jsonb_typeof(clause -> 'text') IS DISTINCT FROM 'string'
           OR NOT memoriesql.authored_render_text_nonempty(clause ->> 'text')
           OR char_length(clause ->> 'text') > 2048
           OR jsonb_typeof(clause -> 'statement_ids') IS DISTINCT FROM 'array'
           OR jsonb_array_length(clause -> 'statement_ids') NOT BETWEEN 1 AND 64
           OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(clause -> 'statement_ids') AS item(value)
                WHERE item.value !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR EXISTS (
                SELECT item.value
                FROM jsonb_array_elements_text(clause -> 'statement_ids') AS item(value)
                GROUP BY item.value HAVING count(*) <> 1
           ) THEN
            RETURN false;
        END IF;
    END LOOP;

    IF char_length(candidate #>> '{title,text}') > 240
       OR char_length(memoriesql.authored_bead_render_text(candidate, 'summary')) > 2048
       OR char_length(memoriesql.authored_bead_render_text(candidate, 'detail')) > 8192 THEN
        RETURN false;
    END IF;

    FOR omission IN
        SELECT item.value FROM jsonb_array_elements(candidate -> 'omissions') AS item(value)
    LOOP
        IF jsonb_typeof(omission) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(omission)) <> 2
           OR EXISTS (
                SELECT 1 FROM jsonb_object_keys(omission) AS key(value)
                WHERE key.value <> ALL (ARRAY['statement_id', 'reason']::text[])
           )
           OR omission ->> 'statement_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(omission -> 'reason') IS DISTINCT FROM 'string'
           OR NOT memoriesql.authored_render_text_nonempty(
               omission ->> 'reason'
           )
           OR char_length(omission ->> 'reason') > 512 THEN
            RETURN false;
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT item.value ->> 'statement_id'
        FROM jsonb_array_elements(candidate -> 'omissions') AS item(value)
        GROUP BY item.value ->> 'statement_id' HAVING count(*) <> 1
    ) OR EXISTS (
        WITH clauses AS (
            SELECT candidate -> 'title' AS value
            UNION ALL
            SELECT item.value FROM jsonb_array_elements(candidate -> 'summary') AS item(value)
            UNION ALL
            SELECT item.value FROM jsonb_array_elements(candidate -> 'detail') AS item(value)
        )
        SELECT 1
        FROM clauses
        CROSS JOIN LATERAL jsonb_array_elements_text(
            clauses.value -> 'statement_ids'
        ) AS included(value)
        JOIN jsonb_array_elements(candidate -> 'omissions') AS omitted(value)
          ON omitted.value ->> 'statement_id' = included.value
    ) THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$;

ALTER TABLE memoriesql.bead_versions
    ADD COLUMN render_contract_revision smallint NOT NULL DEFAULT 1,
    ADD COLUMN render_payload jsonb,
    ADD COLUMN title text GENERATED ALWAYS AS (
        render_payload #>> '{title,text}'
    ) STORED,
    ADD COLUMN summary text GENERATED ALWAYS AS (
        memoriesql.authored_bead_render_text(render_payload, 'summary')
    ) STORED,
    ADD COLUMN detail text GENERATED ALWAYS AS (
        memoriesql.authored_bead_render_text(render_payload, 'detail')
    ) STORED,
    ADD CONSTRAINT bead_versions_render_payload_shape CHECK (
        render_payload IS NULL
        OR memoriesql.authored_bead_render_safe(render_payload)
    ),
    ADD CONSTRAINT bead_versions_render_contract_revision_supported CHECK (
        render_contract_revision IN (1, 2)
    ),
    ADD CONSTRAINT bead_versions_render_contract_consistent CHECK (
        (
            render_contract_revision = 1
            AND render_payload IS NULL
        ) OR (
            render_contract_revision = 2
            AND (
                semantic_task_receipt_id IS NULL
                OR render_payload IS NOT NULL
            )
        )
    );

-- Rows authored before rich renders existed remain explicit revision-1 legacy
-- versions. Every subsequent insert defaults to revision 2; the governed apply
-- function also supplies revision 2 explicitly and must provide its render.
ALTER TABLE memoriesql.bead_versions
    ALTER COLUMN render_contract_revision SET DEFAULT 2;

-- PostgreSQL expands SELECT * when a view is created. Recreate the current
-- version view so the appended render columns are part of the public row shape.
CREATE OR REPLACE VIEW memoriesql.current_bead_versions
WITH (security_invoker = true) AS
SELECT DISTINCT ON (version_record.tenant_id, version_record.bead_id)
       version_record.*
FROM memoriesql.bead_versions AS version_record
ORDER BY version_record.tenant_id,
         version_record.bead_id,
         version_record.version DESC;

INSERT INTO memoriesql.semantic_task_admission_policies (
    semantic_registry_hash, task_kind, contract_revision, owning_module,
    task_contract_hash, target_kind, required_capability, queue_name,
    base_priority, max_attempts, concurrency_key, concurrency_limit
) VALUES (
    'semantic-tasks-v1:ec2c9544c3eab504c9276e3f600ad656cbcc606dfa64c9f08aebdb6fb2e01bd4',
    'memory.semantic.author-observations', 1, 'memoriesql.kernel',
    'df6b7094bdc407ea10464a1b9ff9020cbbc99d57160a3b86d7702d57db89f92e',
    'canonical_semantics', 'memory.capture', 'capture', 50, 3,
    'canonical-observation-authoring', 8
);

-- Canonical authoring remains within the general 32 KiB queue envelope.
CREATE OR REPLACE FUNCTION memoriesql.semantic_task_input_reference_safe(
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
    payload jsonb;
    effective_maximum_bytes integer;
BEGIN
    effective_maximum_bytes := maximum_bytes;
    IF candidate IS NULL
       OR maximum_bytes <= 0
       OR octet_length(candidate::text) > effective_maximum_bytes
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

    payload := candidate -> 'payload';
    IF candidate ->> 'task_kind' = 'memory.semantic.author-observations'
       AND candidate ->> 'contract_revision' = '1' THEN
        IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 3
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(payload) AS payload_key(key)
               WHERE payload_key.key <> ALL (
                   ARRAY['event_id', 'bead_ids', 'source_unit_ids']::text[]
               )
           )
           OR payload ->> 'event_id' !~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(payload -> 'bead_ids') <> 'array'
           OR jsonb_typeof(payload -> 'source_unit_ids') <> 'array'
           OR jsonb_array_length(payload -> 'bead_ids') NOT BETWEEN 1 AND 8
           OR jsonb_array_length(payload -> 'bead_ids') <>
                jsonb_array_length(payload -> 'source_unit_ids')
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(payload -> 'bead_ids') AS item(value)
               WHERE jsonb_typeof(item.value) <> 'string'
                  OR item.value #>> '{}' !~
                     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(payload -> 'source_unit_ids')
                    AS item(value)
               WHERE jsonb_typeof(item.value) <> 'string'
                  OR item.value #>> '{}' !~
                     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR (
               SELECT count(*) <> count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(payload -> 'bead_ids') AS item(value)
           )
           OR (
               SELECT count(*) <> count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(payload -> 'source_unit_ids')
                    AS item(value)
           ) THEN
            RETURN false;
        END IF;
        RETURN true;
    END IF;
    RETURN memoriesql.semantic_queue_payload_reference_scalar_safe(payload);
END;
$$;

ALTER TABLE memoriesql.source_units
ADD CONSTRAINT source_units_scope_content_hash_uq UNIQUE (
    tenant_id, workspace_id, access_scope_id, event_id,
    source_unit_id, content_hash
);

CREATE TABLE memoriesql.accepted_source_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    acceptance_hash text NOT NULL,
    capture_receipt_id uuid NOT NULL,
    semantic_task_id uuid NOT NULL,
    accepted_at timestamp with time zone NOT NULL,
    CONSTRAINT accepted_source_events_pk PRIMARY KEY (tenant_id, event_id),
    CONSTRAINT accepted_source_events_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, event_id
    ),
    CONSTRAINT accepted_source_events_event_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id
    ) REFERENCES memoriesql.source_events (
        tenant_id, workspace_id, access_scope_id, event_id
    ),
    CONSTRAINT accepted_source_events_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, capture_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT accepted_source_events_task_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, semantic_task_id
    ) REFERENCES memoriesql.semantic_tasks (
        tenant_id, workspace_id, access_scope_id, task_id
    ),
    CONSTRAINT accepted_source_events_hash_shape CHECK (
        acceptance_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE TABLE memoriesql.capture_checkpoints (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    checkpoint_key text NOT NULL,
    checkpoint_sequence bigint NOT NULL,
    checkpoint_hash text NOT NULL,
    last_event_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    advanced_by_principal_id uuid NOT NULL,
    advanced_at timestamp with time zone NOT NULL,
    CONSTRAINT capture_checkpoints_pk PRIMARY KEY (
        tenant_id, workspace_id, access_scope_id, checkpoint_key
    ),
    CONSTRAINT capture_checkpoints_scope_event_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, last_event_id
    ) REFERENCES memoriesql.source_events (
        tenant_id, workspace_id, access_scope_id, event_id
    ),
    CONSTRAINT capture_checkpoints_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ) REFERENCES memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id
    ),
    CONSTRAINT capture_checkpoints_principal_fk FOREIGN KEY (
        tenant_id, advanced_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT capture_checkpoints_key_shape CHECK (
        checkpoint_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(checkpoint_key) <= 128
    ),
    CONSTRAINT capture_checkpoints_sequence_positive CHECK (
        checkpoint_sequence > 0
    ),
    CONSTRAINT capture_checkpoints_hash_shape CHECK (
        checkpoint_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE TABLE memoriesql.bead_semantic_statements (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    statement_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    statement_sequence bigint NOT NULL,
    statement_kind text NOT NULL,
    statement_text text NOT NULL,
    context_source_ids uuid[] NOT NULL,
    authored_by_principal_id uuid NOT NULL,
    semantic_task_id uuid NOT NULL,
    semantic_attempt_id uuid NOT NULL,
    semantic_run_id text NOT NULL,
    supersedes_statement_id uuid,
    correction_reason text,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT bead_semantic_statements_pk PRIMARY KEY (
        tenant_id, statement_id
    ),
    CONSTRAINT bead_semantic_statements_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, statement_id
    ),
    CONSTRAINT bead_semantic_statements_sequence_uq UNIQUE (
        tenant_id, bead_id, statement_sequence
    ),
    CONSTRAINT bead_semantic_statements_bead_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ),
    CONSTRAINT bead_semantic_statements_author_fk FOREIGN KEY (
        tenant_id, authored_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT bead_semantic_statements_run_fk FOREIGN KEY (
        tenant_id, semantic_attempt_id, semantic_run_id
    ) REFERENCES memoriesql.semantic_task_runs (
        tenant_id, attempt_id, run_id
    ),
    CONSTRAINT bead_semantic_statements_supersedes_fk FOREIGN KEY (
        tenant_id, supersedes_statement_id
    ) REFERENCES memoriesql.bead_semantic_statements (
        tenant_id, statement_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT bead_semantic_statements_one_supersession_uq UNIQUE (
        tenant_id, supersedes_statement_id
    ),
    CONSTRAINT bead_semantic_statements_sequence_positive CHECK (
        statement_sequence > 0
    ),
    CONSTRAINT bead_semantic_statements_kind_supported CHECK (
        statement_kind IN (
            'observation', 'context', 'qualification', 'correction'
        )
    ),
    CONSTRAINT bead_semantic_statements_text_shape CHECK (
        btrim(statement_text) <> '' AND length(statement_text) <= 8192
    ),
    CONSTRAINT bead_semantic_statements_context_shape CHECK (
        cardinality(context_source_ids) <= 64
        AND array_position(context_source_ids, NULL) IS NULL
    ),
    CONSTRAINT bead_semantic_statements_run_shape CHECK (
        semantic_run_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(semantic_run_id) <= 128
    ),
    CONSTRAINT bead_semantic_statements_correction_shape CHECK (
        (
            statement_kind = 'correction'
            AND supersedes_statement_id IS NOT NULL
            AND correction_reason IS NOT NULL
            AND btrim(correction_reason) <> ''
            AND length(correction_reason) <= 1024
        ) OR (
            statement_kind <> 'correction'
            AND supersedes_statement_id IS NULL
            AND correction_reason IS NULL
        )
    )
);

CREATE TABLE memoriesql.bead_semantic_statement_evidence (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    statement_id uuid NOT NULL,
    evidence_event_id uuid NOT NULL,
    evidence_source_unit_id uuid NOT NULL,
    evidence_content_hash text NOT NULL,
    linked_at timestamp with time zone NOT NULL,
    CONSTRAINT bead_semantic_statement_evidence_pk PRIMARY KEY (
        tenant_id, statement_id, evidence_source_unit_id
    ),
    CONSTRAINT bead_semantic_statement_evidence_statement_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, statement_id
    ) REFERENCES memoriesql.bead_semantic_statements (
        tenant_id, workspace_id, access_scope_id, statement_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT bead_semantic_statement_evidence_source_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_event_id,
        evidence_source_unit_id, evidence_content_hash
    ) REFERENCES memoriesql.source_units (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, content_hash
    ),
    CONSTRAINT bead_semantic_statement_evidence_hash_shape CHECK (
        evidence_content_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE TABLE memoriesql.bead_statement_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    statement_watermark bigint NOT NULL,
    semantic_task_receipt_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT bead_statement_revisions_pk PRIMARY KEY (
        tenant_id, bead_version_id
    ),
    CONSTRAINT bead_statement_revisions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT bead_statement_revisions_bead_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ),
    CONSTRAINT bead_statement_revisions_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, semantic_task_receipt_id
    ) REFERENCES memoriesql.semantic_task_receipts (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, semantic_task_receipt_id
    ),
    CONSTRAINT bead_statement_revisions_watermark_positive CHECK (
        statement_watermark > 0
    )
);

CREATE FUNCTION memoriesql.assert_semantic_statement_has_evidence()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM memoriesql.bead_semantic_statement_evidence AS evidence
        WHERE evidence.tenant_id = NEW.tenant_id
          AND evidence.statement_id = NEW.statement_id
    ) THEN
        RAISE EXCEPTION 'semantic statement % requires evidence', NEW.statement_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER bead_semantic_statements_require_evidence
AFTER INSERT ON memoriesql.bead_semantic_statements
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_semantic_statement_has_evidence();

CREATE FUNCTION memoriesql.canonical_semantic_json_string(candidate text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    result text := '"';
    character text;
    codepoint integer;
    supplementary integer;
    slash text := pg_catalog.chr(92);
BEGIN
    FOR position IN 1..pg_catalog.char_length(candidate)
    LOOP
        character := pg_catalog.substr(candidate, position, 1);
        codepoint := pg_catalog.ascii(character);
        IF character = '"' THEN
            result := result || slash || '"';
        ELSIF codepoint = 92 THEN
            result := result || slash || slash;
        ELSIF codepoint = 8 THEN
            result := result || slash || 'b';
        ELSIF codepoint = 12 THEN
            result := result || slash || 'f';
        ELSIF codepoint = 10 THEN
            result := result || slash || 'n';
        ELSIF codepoint = 13 THEN
            result := result || slash || 'r';
        ELSIF codepoint = 9 THEN
            result := result || slash || 't';
        ELSIF codepoint < 32 THEN
            result := result || slash || 'u'
                || pg_catalog.lpad(pg_catalog.to_hex(codepoint), 4, '0');
        ELSIF codepoint < 128 THEN
            result := result || character;
        ELSIF codepoint <= 65535 THEN
            result := result || slash || 'u'
                || pg_catalog.lpad(pg_catalog.to_hex(codepoint), 4, '0');
        ELSE
            supplementary := codepoint - 65536;
            result := result || slash || 'u'
                || pg_catalog.lpad(
                    pg_catalog.to_hex(55296 + supplementary / 1024), 4, '0'
                )
                || slash || 'u'
                || pg_catalog.lpad(
                    pg_catalog.to_hex(56320 + supplementary % 1024), 4, '0'
                );
        END IF;
    END LOOP;
    RETURN result || '"';
END;
$$;

CREATE FUNCTION memoriesql.canonical_semantic_json_text(candidate jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    result text;
BEGIN
    CASE jsonb_typeof(candidate)
        WHEN 'object' THEN
            SELECT '{' || COALESCE(
                pg_catalog.string_agg(
                    memoriesql.canonical_semantic_json_string(entry.key)
                        || ':'
                        || memoriesql.canonical_semantic_json_text(entry.value),
                    ',' ORDER BY entry.key COLLATE "C"
                ),
                ''
            ) || '}'
              INTO result
              FROM pg_catalog.jsonb_each(candidate) AS entry(key, value);
        WHEN 'array' THEN
            SELECT '[' || COALESCE(
                pg_catalog.string_agg(
                    memoriesql.canonical_semantic_json_text(entry.value),
                    ',' ORDER BY entry.ordinality
                ),
                ''
            ) || ']'
              INTO result
              FROM pg_catalog.jsonb_array_elements(candidate)
                   WITH ORDINALITY AS entry(value, ordinality);
        WHEN 'string' THEN
            result := memoriesql.canonical_semantic_json_string(
                candidate #>> '{}'
            );
        ELSE
            result := candidate::text;
    END CASE;
    RETURN result;
END;
$$;

CREATE FUNCTION memoriesql.current_context_semantic_statement_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_statement_id uuid
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
        JOIN memoriesql.bead_semantic_statements AS statement
          ON statement.tenant_id = context.tenant_id
         AND statement.workspace_id = context.workspace_id
         AND statement.tenant_id = requested_tenant_id
         AND statement.workspace_id = requested_workspace_id
         AND statement.access_scope_id = requested_access_scope_id
         AND statement.statement_id = requested_statement_id
        WHERE memoriesql.current_context_event_authorized(
                  statement.access_scope_id, statement.event_id,
                  'memory.query', 'read'
              )
          AND EXISTS (
                SELECT 1
                FROM memoriesql.bead_semantic_statement_evidence AS evidence
                WHERE evidence.tenant_id = statement.tenant_id
                  AND evidence.workspace_id = statement.workspace_id
                  AND evidence.access_scope_id = statement.access_scope_id
                  AND evidence.statement_id = statement.statement_id
          )
          AND NOT EXISTS (
                SELECT 1
                FROM memoriesql.bead_semantic_statement_evidence AS evidence
                WHERE evidence.tenant_id = statement.tenant_id
                  AND evidence.workspace_id = statement.workspace_id
                  AND evidence.access_scope_id = statement.access_scope_id
                  AND evidence.statement_id = statement.statement_id
                  AND NOT memoriesql.current_context_event_authorized(
                      evidence.access_scope_id, evidence.evidence_event_id,
                      'memory.query', 'read'
                  )
          )
    )
$$;

CREATE OR REPLACE FUNCTION memoriesql.current_context_bead_version_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_bead_version_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT EXISTS (
               SELECT 1
               FROM memoriesql.current_authorization_context() AS context
               JOIN memoriesql.bead_versions AS version_record
                 ON version_record.tenant_id = context.tenant_id
                AND version_record.workspace_id = context.workspace_id
                AND version_record.access_scope_id = requested_access_scope_id
                AND version_record.bead_version_id = requested_bead_version_id
               WHERE context.tenant_id = requested_tenant_id
                 AND context.workspace_id = requested_workspace_id
                 AND memoriesql.current_context_event_authorized(
                         version_record.access_scope_id,
                         version_record.event_id,
                         'source.read',
                         'read'
                     )
           )
       AND NOT EXISTS (
               SELECT 1
               FROM memoriesql.bead_semantic_statements AS statement
               WHERE statement.tenant_id = requested_tenant_id
                 AND statement.workspace_id = requested_workspace_id
                 AND statement.access_scope_id = requested_access_scope_id
                 AND statement.bead_version_id = requested_bead_version_id
                 AND NOT memoriesql.current_context_semantic_statement_authorized(
                     statement.tenant_id, statement.workspace_id,
                     statement.access_scope_id, statement.statement_id
                 )
           )
$$;

CREATE FUNCTION memoriesql.current_context_bead_revision_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_bead_id uuid,
    requested_statement_watermark bigint
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
        JOIN memoriesql.bead_semantic_statements AS statement
          ON statement.tenant_id = context.tenant_id
         AND statement.workspace_id = context.workspace_id
         AND statement.tenant_id = requested_tenant_id
         AND statement.workspace_id = requested_workspace_id
         AND statement.access_scope_id = requested_access_scope_id
         AND statement.bead_id = requested_bead_id
         AND statement.statement_sequence <= requested_statement_watermark
    )
       AND NOT EXISTS (
            SELECT 1
            FROM memoriesql.bead_semantic_statements AS statement
            WHERE statement.tenant_id = requested_tenant_id
              AND statement.workspace_id = requested_workspace_id
              AND statement.access_scope_id = requested_access_scope_id
              AND statement.bead_id = requested_bead_id
              AND statement.statement_sequence <= requested_statement_watermark
              AND NOT memoriesql.current_context_semantic_statement_authorized(
                  statement.tenant_id, statement.workspace_id,
                  statement.access_scope_id, statement.statement_id
              )
       )
$$;

CREATE OR REPLACE FUNCTION memoriesql.current_context_bead_version_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_bead_version_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT EXISTS (
               SELECT 1
               FROM memoriesql.current_authorization_context() AS context
               JOIN memoriesql.bead_versions AS version_record
                 ON version_record.tenant_id = context.tenant_id
                AND version_record.workspace_id = context.workspace_id
                AND version_record.access_scope_id = requested_access_scope_id
                AND version_record.bead_version_id = requested_bead_version_id
               WHERE context.tenant_id = requested_tenant_id
                 AND context.workspace_id = requested_workspace_id
                 AND memoriesql.current_context_event_authorized(
                         version_record.access_scope_id,
                         version_record.event_id,
                         'source.read',
                         'read'
                     )
           )
       AND (
            NOT EXISTS (
                SELECT 1
                FROM memoriesql.bead_statement_revisions AS revision
                WHERE revision.tenant_id = requested_tenant_id
                  AND revision.bead_version_id = requested_bead_version_id
            )
            OR EXISTS (
                SELECT 1
                FROM memoriesql.bead_statement_revisions AS revision
                WHERE revision.tenant_id = requested_tenant_id
                  AND revision.workspace_id = requested_workspace_id
                  AND revision.access_scope_id = requested_access_scope_id
                  AND revision.bead_version_id = requested_bead_version_id
                  AND memoriesql.current_context_bead_revision_authorized(
                      revision.tenant_id, revision.workspace_id,
                      revision.access_scope_id, revision.bead_id,
                      revision.statement_watermark
                  )
            )
       )
$$;

CREATE TRIGGER accepted_source_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.accepted_source_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_semantic_statements_immutable
BEFORE UPDATE OR DELETE ON memoriesql.bead_semantic_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_semantic_statement_evidence_immutable
BEFORE UPDATE OR DELETE ON memoriesql.bead_semantic_statement_evidence
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_statement_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.bead_statement_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.accept_source_event(
    requested_command jsonb,
    requested_at timestamp with time zone
)
RETURNS TABLE (
    event_id uuid,
    source_unit_ids uuid[],
    bead_ids uuid[],
    semantic_task_id uuid,
    idempotency_receipt_id uuid,
    checkpoint_sequence bigint,
    acceptance_status text,
    replayed boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    source_record memoriesql.source_objects%ROWTYPE;
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    accepted_record memoriesql.accepted_source_events%ROWTYPE;
    checkpoint_record memoriesql.capture_checkpoints%ROWTYPE;
    unit_item jsonb;
    detail_item jsonb;
    canonical_units jsonb;
    command_tenant_id uuid;
    command_workspace_id uuid;
    command_access_scope_id uuid;
    command_source_object_id uuid;
    command_event_id uuid;
    command_task_id uuid;
    new_receipt_id uuid;
    new_bead_id uuid;
    computed_request_hash text;
    computed_acceptance_hash text;
    existing_event_id uuid;
    returned_task_id uuid;
    returned_unit_ids uuid[];
    returned_bead_ids uuid[];
    returned_checkpoint_sequence bigint;
    observation_count integer;
    advances_current_content boolean;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
    response jsonb;
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 4194304
       OR requested_command ->> 'contract_version' <> '1'
       OR requested_command ->> 'expected_schema_version' <> '11'
       OR jsonb_typeof(requested_command -> 'event') IS DISTINCT FROM 'object'
       OR jsonb_typeof(requested_command -> 'units') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command -> 'units') NOT BETWEEN 1 AND 256
       OR jsonb_typeof(requested_command -> 'semantic_task')
            IS DISTINCT FROM 'object'
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'source event acceptance contract is invalid'
            USING ERRCODE = '22023';
    END IF;

    command_tenant_id := (requested_command ->> 'tenant_id')::uuid;
    command_workspace_id := (requested_command ->> 'workspace_id')::uuid;
    command_access_scope_id := (requested_command ->> 'access_scope_id')::uuid;
    command_source_object_id := (requested_command ->> 'source_object_id')::uuid;
    command_event_id := (requested_command #>> '{event,event_id}')::uuid;
    command_task_id := (requested_command #>> '{semantic_task,task_id}')::uuid;
    IF btrim(requested_command ->> 'idempotency_key') = ''
       OR btrim(requested_command #>> '{semantic_task,idempotency_key}') = ''
       OR requested_command #>> '{event,content_hash}' !~ '^[a-f0-9]{64}$'
       OR pg_catalog.uuid_extract_version(command_task_id) <> 7 THEN
        RAISE EXCEPTION 'source event acceptance contract is invalid'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
           OR item.value ->> 'content_hash' !~ '^[a-f0-9]{64}$'
           OR COALESCE(jsonb_typeof(item.value -> 'content_text'), 'null')
                NOT IN ('null', 'string')
           OR COALESCE(jsonb_typeof(item.value -> 'hydration_ref'), 'null')
                NOT IN ('null', 'string')
           OR (
                NULLIF(item.value ->> 'content_text', '') IS NULL
                AND NULLIF(item.value ->> 'hydration_ref', '') IS NULL
           )
           OR (
                (item.value ->> 'is_observation')::boolean
                AND NULLIF(item.value ->> 'content_text', '') IS NULL
           )
           OR (
                jsonb_typeof(item.value -> 'content_text') = 'string'
                AND encode(
                    pg_catalog.sha256(pg_catalog.convert_to(
                        item.value ->> 'content_text', 'UTF8'
                    )),
                    'hex'
                ) IS DISTINCT FROM item.value ->> 'content_hash'
           )
    ) THEN
        RAISE EXCEPTION 'source unit content hash mismatch or is unavailable'
            USING ERRCODE = '22023';
    END IF;
    SELECT count(*)::integer INTO observation_count
    FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
    WHERE (item.value ->> 'is_observation')::boolean;
    IF observation_count NOT BETWEEN 1 AND 8 THEN
        RAISE EXCEPTION
            'source event requires between one and eight observation units'
            USING ERRCODE = '22023';
    END IF;
    IF (
        SELECT COALESCE(sum(pg_catalog.char_length(
            item.value ->> 'content_text'
        )), 0)
        FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
        WHERE (item.value ->> 'is_observation')::boolean
    ) > 4096 THEN
        RAISE EXCEPTION
            'source observation content exceeds authoring hydration limit'
            USING ERRCODE = '22023';
    END IF;
    IF (
        SELECT COALESCE(sum(pg_catalog.octet_length(
            memoriesql.canonical_semantic_json_string(
                item.value ->> 'content_text'
            )
        ) - 2), 0)
        FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
        WHERE (item.value ->> 'is_observation')::boolean
    ) > 4096 THEN
        RAISE EXCEPTION
            'source observation content exceeds authoring token envelope'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
        WHERE (item.value ->> 'is_observation')::boolean
          AND CASE requested_command #>> '{event,source_type}'
              WHEN 'transcript' THEN item.value ->> 'unit_kind' <> 'turn'
              WHEN 'document' THEN
                  item.value ->> 'unit_kind' NOT IN ('section', 'chunk')
              WHEN 'media' THEN item.value ->> 'unit_kind' <> 'media_window'
              WHEN 'relational' THEN
                  item.value ->> 'unit_kind' <> 'record_change'
              WHEN 'operational' THEN
                  item.value ->> 'unit_kind' <> 'record_change'
              ELSE true
          END
    ) THEN
        RAISE EXCEPTION 'observation unit kind is invalid for its source type'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.tenant_id <> command_tenant_id
       OR context_record.workspace_id <> command_workspace_id
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.capture', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source event acceptance is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT jsonb_agg(
               (item.value - 'source_unit_id' - 'parent_unit_id')
               || jsonb_build_object(
                   'parent_external_unit_id', parent.value ->> 'external_unit_id'
               )
               ORDER BY (item.value ->> 'unit_ordinal')::bigint,
                        item.value ->> 'external_unit_id'
           )
      INTO canonical_units
      FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
      LEFT JOIN LATERAL (
          SELECT candidate.value
          FROM jsonb_array_elements(requested_command -> 'units')
              AS candidate(value)
          WHERE candidate.value ->> 'source_unit_id' =
              item.value ->> 'parent_unit_id'
      ) AS parent ON true;
    computed_request_hash := encode(
        pg_catalog.sha256(pg_catalog.convert_to(requested_command::text, 'UTF8')),
        'hex'
    );
    computed_acceptance_hash := encode(
        pg_catalog.sha256(pg_catalog.convert_to(jsonb_build_object(
            'source_object_id', command_source_object_id,
            'source_object_schema_version',
                requested_command -> 'expected_source_object_schema_version',
            'event', (requested_command -> 'event') - 'event_id' - 'captured_at',
            'units', canonical_units
        )::text, 'UTF8')),
        'hex'
    );

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            command_tenant_id::text || ':source_event.accept:'
                || (requested_command ->> 'idempotency_key'),
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.capture', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source event acceptance is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO receipt_record
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.operation_kind = 'source_event.accept'
      AND receipt.idempotency_key = requested_command ->> 'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF receipt_record.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF receipt_record.status <> 'succeeded'
           OR receipt_record.response_receipt IS NULL THEN
            RAISE EXCEPTION 'source event receipt is incomplete'
                USING ERRCODE = '55000';
        END IF;
        response := receipt_record.response_receipt;
        RETURN QUERY SELECT
            (response ->> 'event_id')::uuid,
            ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                response -> 'source_unit_ids'
            ) AS item(value)),
            ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                response -> 'bead_ids'
            ) AS item(value)),
            (response ->> 'semantic_task_id')::uuid,
            receipt_record.idempotency_receipt_id,
            NULLIF(response ->> 'checkpoint_sequence', '')::bigint,
            response ->> 'status',
            true;
        RETURN;
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.jsonb_build_array(
                'source_event.accept.natural_identity',
                command_tenant_id,
                requested_command #>> '{event,source_system}',
                requested_command #>> '{event,external_id_scope}',
                requested_command #>> '{event,source_identity_key}'
            )::text,
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.capture', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source event acceptance is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT source.* INTO source_record
    FROM memoriesql.source_objects AS source
    JOIN memoriesql.protected_resources AS resource
      ON resource.tenant_id = source.tenant_id
     AND resource.workspace_id = source.workspace_id
     AND resource.access_scope_id = source.access_scope_id
     AND resource.resource_kind = 'source'
     AND resource.resource_id = source.source_object_id
     AND resource.status = 'active'
    WHERE source.tenant_id = command_tenant_id
      AND source.workspace_id = command_workspace_id
      AND source.access_scope_id = command_access_scope_id
      AND source.source_object_id = command_source_object_id
      AND source.source_system = requested_command #>> '{event,source_system}'
      AND source.installation_id IS NOT DISTINCT FROM
          NULLIF(requested_command #>> '{event,installation_id}', '')
      AND source.schema_version =
          (requested_command ->> 'expected_source_object_schema_version')::integer
    FOR UPDATE OF source
    FOR KEY SHARE OF resource;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'source object is unavailable' USING ERRCODE = '42501';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.capture', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source event acceptance is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT source_event.event_id INTO existing_event_id
    FROM memoriesql.source_events AS source_event
    WHERE source_event.tenant_id = command_tenant_id
      AND source_event.source_system = requested_command #>> '{event,source_system}'
      AND source_event.external_id_scope =
          requested_command #>> '{event,external_id_scope}'
      AND source_event.source_identity_key =
          requested_command #>> '{event,source_identity_key}'
    FOR SHARE;
    IF FOUND THEN
        SELECT * INTO accepted_record
        FROM memoriesql.accepted_source_events AS accepted
        WHERE accepted.tenant_id = command_tenant_id
          AND accepted.event_id = existing_event_id;
        IF NOT FOUND
           OR accepted_record.acceptance_hash <> computed_acceptance_hash THEN
            RAISE EXCEPTION 'source_identity_conflict' USING ERRCODE = '23505';
        END IF;
        command_event_id := existing_event_id;
        returned_task_id := accepted_record.semantic_task_id;
    ELSIF EXISTS (
        SELECT 1 FROM memoriesql.source_events AS collision
        WHERE collision.tenant_id = command_tenant_id
          AND collision.event_id = command_event_id
    ) THEN
        RAISE EXCEPTION 'source_identity_conflict' USING ERRCODE = '23505';
    END IF;

    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, attempt_count,
        created_at, updated_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, 'source_event.accept',
        requested_command ->> 'idempotency_key', computed_request_hash,
        'in_progress', 'source_event', command_event_id, 1,
        database_now, database_now
    );

    IF existing_event_id IS NULL THEN
        INSERT INTO memoriesql.source_events (
            tenant_id, workspace_id, access_scope_id, event_id,
            source_object_id, source_type, source_system, installation_id,
            external_event_id, external_id_scope, source_identity_key,
            session_id, actor_id, actor_kind, source_occurred_at,
            source_occurred_end_at, source_occurred_at_raw, source_timezone,
            source_time_precision, source_sequence, source_revision_key,
            parser_contract_version, observation_unit_policy_version,
            observation_unit_count, captured_at, source_ref, content_hash,
            metadata
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            command_event_id, command_source_object_id,
            requested_command #>> '{event,source_type}',
            requested_command #>> '{event,source_system}',
            NULLIF(requested_command #>> '{event,installation_id}', ''),
            NULLIF(requested_command #>> '{event,external_event_id}', ''),
            requested_command #>> '{event,external_id_scope}',
            requested_command #>> '{event,source_identity_key}',
            NULLIF(requested_command #>> '{event,session_id}', ''),
            requested_command #>> '{event,actor_id}',
            requested_command #>> '{event,actor_kind}',
            NULLIF(requested_command #>> '{event,source_occurred_at}', '')::timestamptz,
            NULLIF(requested_command #>> '{event,source_occurred_end_at}', '')::timestamptz,
            NULLIF(requested_command #>> '{event,source_occurred_at_raw}', ''),
            NULLIF(requested_command #>> '{event,source_timezone}', ''),
            NULLIF(requested_command #>> '{event,source_time_precision}', ''),
            NULLIF(requested_command #>> '{event,source_sequence}', '')::bigint,
            NULLIF(requested_command #>> '{event,source_revision_key}', ''),
            requested_command #>> '{event,parser_contract_version}',
            requested_command #>> '{event,observation_unit_policy_version}',
            observation_count,
            (requested_command #>> '{event,captured_at}')::timestamptz,
            requested_command #>> '{event,source_ref}',
            requested_command #>> '{event,content_hash}',
            COALESCE((SELECT jsonb_object_agg(value ->> 'key', value ->> 'value')
                FROM jsonb_array_elements(
                    requested_command #> '{event,attributes}'
                ) AS attribute(value)), '{}'::jsonb)
        );

        IF requested_command #>> '{event,source_type}' = 'document' THEN
            INSERT INTO memoriesql.document_revisions (
                tenant_id, workspace_id, access_scope_id, event_id,
                document_version, mime_type, source_title, content_hash
            ) VALUES (
                command_tenant_id, command_workspace_id,
                command_access_scope_id, command_event_id,
                requested_command #>> '{event,document_revision,document_version}',
                requested_command #>> '{event,document_revision,mime_type}',
                NULLIF(requested_command #>>
                    '{event,document_revision,source_title}', ''),
                requested_command #>> '{event,content_hash}'
            );
        END IF;

        FOR unit_item IN
            SELECT item.value
            FROM jsonb_array_elements(requested_command -> 'units') AS item(value)
            ORDER BY (item.value ->> 'unit_ordinal')::bigint,
                     item.value ->> 'source_unit_id'
        LOOP
            detail_item := unit_item -> 'detail';
            INSERT INTO memoriesql.source_units (
                tenant_id, workspace_id, access_scope_id, source_unit_id,
                event_id, parent_unit_id, unit_kind, is_observation,
                external_unit_id, unit_ordinal, unit_source_occurred_at,
                unit_source_occurred_end_at, unit_time_precision,
                time_start_seconds, time_end_seconds, content_text,
                content_hash, structure, hydration_ref, schema_version,
                created_at, processing_receipt_id
            ) VALUES (
                command_tenant_id, command_workspace_id,
                command_access_scope_id,
                (unit_item ->> 'source_unit_id')::uuid, command_event_id,
                NULLIF(unit_item ->> 'parent_unit_id', '')::uuid,
                unit_item ->> 'unit_kind',
                (unit_item ->> 'is_observation')::boolean,
                unit_item ->> 'external_unit_id',
                (unit_item ->> 'unit_ordinal')::bigint,
                NULLIF(unit_item ->> 'unit_source_occurred_at', '')::timestamptz,
                NULLIF(unit_item ->> 'unit_source_occurred_end_at', '')::timestamptz,
                NULLIF(unit_item ->> 'unit_time_precision', ''),
                NULLIF(unit_item ->> 'time_start_seconds', '')::numeric,
                NULLIF(unit_item ->> 'time_end_seconds', '')::numeric,
                NULLIF(unit_item ->> 'content_text', ''),
                unit_item ->> 'content_hash',
                COALESCE((SELECT jsonb_object_agg(value ->> 'key', value ->> 'value')
                    FROM jsonb_array_elements(unit_item -> 'structure')
                        AS attribute(value)), '{}'::jsonb),
                NULLIF(unit_item ->> 'hydration_ref', ''),
                (unit_item ->> 'schema_version')::integer,
                database_now, new_receipt_id
            );

            CASE requested_command #>> '{event,source_type}'
                WHEN 'transcript' THEN
                    INSERT INTO memoriesql.conversation_turns (
                        tenant_id, workspace_id, access_scope_id, event_id,
                        source_unit_id, conversation_id, session_id, branch_id,
                        turn_id, participant_id, participant_role
                    ) VALUES (
                        command_tenant_id, command_workspace_id,
                        command_access_scope_id, command_event_id,
                        (unit_item ->> 'source_unit_id')::uuid,
                        detail_item ->> 'conversation_id',
                        detail_item ->> 'session_id',
                        NULLIF(detail_item ->> 'branch_id', ''),
                        detail_item ->> 'turn_id',
                        NULLIF(detail_item ->> 'participant_id', ''),
                        detail_item ->> 'participant_role'
                    );
                WHEN 'document' THEN
                    INSERT INTO memoriesql.document_segments (
                        tenant_id, workspace_id, access_scope_id, event_id,
                        source_unit_id, unit_kind, page_number, char_start,
                        char_end, section_path
                    ) VALUES (
                        command_tenant_id, command_workspace_id,
                        command_access_scope_id, command_event_id,
                        (unit_item ->> 'source_unit_id')::uuid,
                        unit_item ->> 'unit_kind',
                        NULLIF(detail_item ->> 'page_number', '')::integer,
                        NULLIF(detail_item ->> 'char_start', '')::bigint,
                        NULLIF(detail_item ->> 'char_end', '')::bigint,
                        ARRAY(SELECT value FROM jsonb_array_elements_text(
                            detail_item -> 'section_path'
                        ) AS item(value))
                    );
                WHEN 'media' THEN
                    INSERT INTO memoriesql.media_segments (
                        tenant_id, workspace_id, access_scope_id, event_id,
                        source_unit_id, unit_kind, track_id, speaker_ref,
                        transcript_language, time_start_seconds, time_end_seconds
                    ) VALUES (
                        command_tenant_id, command_workspace_id,
                        command_access_scope_id, command_event_id,
                        (unit_item ->> 'source_unit_id')::uuid,
                        unit_item ->> 'unit_kind',
                        NULLIF(detail_item ->> 'track_id', ''),
                        NULLIF(detail_item ->> 'speaker_ref', ''),
                        NULLIF(detail_item ->> 'transcript_language', ''),
                        (unit_item ->> 'time_start_seconds')::numeric,
                        (unit_item ->> 'time_end_seconds')::numeric
                    );
                ELSE
                    INSERT INTO memoriesql.record_events (
                        tenant_id, workspace_id, access_scope_id, event_id,
                        source_unit_id, source_type, record_system,
                        record_object_type, record_object_key, record_action,
                        effective_at, changed_fields
                    ) VALUES (
                        command_tenant_id, command_workspace_id,
                        command_access_scope_id, command_event_id,
                        (unit_item ->> 'source_unit_id')::uuid,
                        requested_command #>> '{event,source_type}',
                        detail_item ->> 'record_system',
                        detail_item ->> 'record_object_type',
                        detail_item ->> 'record_object_key',
                        detail_item ->> 'record_action',
                        NULLIF(detail_item ->> 'effective_at', '')::timestamptz,
                        COALESCE((SELECT jsonb_object_agg(
                                value ->> 'key', value ->> 'value'
                            )
                            FROM jsonb_array_elements(
                                detail_item -> 'changed_fields'
                            ) AS attribute(value)), '{}'::jsonb)
                    );
            END CASE;

            IF (unit_item ->> 'is_observation')::boolean THEN
                new_bead_id := pg_catalog.uuidv7();
                INSERT INTO memoriesql.beads (
                    tenant_id, workspace_id, access_scope_id, bead_id,
                    event_id, source_unit_id, created_at
                ) VALUES (
                    command_tenant_id, command_workspace_id,
                    command_access_scope_id, new_bead_id, command_event_id,
                    (unit_item ->> 'source_unit_id')::uuid, database_now
                );
            END IF;
        END LOOP;

        SELECT NOT EXISTS (
            SELECT 1
            FROM memoriesql.source_events AS candidate
            WHERE candidate.tenant_id = command_tenant_id
              AND candidate.source_object_id = command_source_object_id
              AND candidate.event_id <> command_event_id
              AND (
                  (
                      NULLIF(requested_command #>>
                          '{event,source_sequence}', '')::bigint IS NOT NULL
                      AND candidate.source_sequence >
                          (requested_command #>>
                              '{event,source_sequence}')::bigint
                  )
                  OR (
                      NULLIF(requested_command #>>
                          '{event,source_sequence}', '')::bigint IS NULL
                      AND (
                          candidate.source_sequence IS NOT NULL
                          OR candidate.captured_at >
                              (requested_command #>>
                                  '{event,captured_at}')::timestamptz
                          OR (
                              candidate.captured_at =
                                  (requested_command #>>
                                      '{event,captured_at}')::timestamptz
                              AND candidate.event_id > command_event_id
                          )
                      )
                  )
              )
        ) INTO advances_current_content;
        UPDATE memoriesql.source_objects
           SET current_content_hash = CASE WHEN advances_current_content
                   THEN requested_command #>> '{event,content_hash}'
                   ELSE current_content_hash
               END,
               metadata = CASE WHEN advances_current_content
                   THEN COALESCE((SELECT jsonb_object_agg(
                       value ->> 'key', value ->> 'value'
                   ) FROM jsonb_array_elements(
                       requested_command #> '{event,attributes}'
                   ) AS attribute(value)), '{}'::jsonb)
                   ELSE metadata
               END,
               last_observed_at = GREATEST(
                   last_observed_at,
                   (requested_command #>> '{event,captured_at}')::timestamptz
               )
         WHERE tenant_id = command_tenant_id
           AND source_object_id = command_source_object_id;

        SELECT array_agg(unit.source_unit_id ORDER BY unit.unit_ordinal),
               array_agg(bead.bead_id ORDER BY unit.unit_ordinal)
          INTO returned_unit_ids, returned_bead_ids
          FROM memoriesql.source_units AS unit
          LEFT JOIN memoriesql.beads AS bead
            ON bead.tenant_id = unit.tenant_id
           AND bead.source_unit_id = unit.source_unit_id
         WHERE unit.tenant_id = command_tenant_id
           AND unit.event_id = command_event_id
           AND unit.is_observation;

    ELSE
        SELECT array_agg(unit.source_unit_id ORDER BY unit.unit_ordinal),
               array_agg(bead.bead_id ORDER BY unit.unit_ordinal)
          INTO returned_unit_ids, returned_bead_ids
          FROM memoriesql.source_units AS unit
          LEFT JOIN memoriesql.beads AS bead
            ON bead.tenant_id = unit.tenant_id
           AND bead.source_unit_id = unit.source_unit_id
         WHERE unit.tenant_id = command_tenant_id
           AND unit.event_id = command_event_id
           AND unit.is_observation;
    END IF;

    IF requested_command -> 'checkpoint' <> 'null'::jsonb THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                command_tenant_id::text || ':' || command_workspace_id::text
                    || ':capture-checkpoint:'
                    || (requested_command #>> '{checkpoint,checkpoint_key}'),
                0
            )
        );
        SELECT * INTO checkpoint_record
        FROM memoriesql.capture_checkpoints AS checkpoint
        WHERE checkpoint.tenant_id = command_tenant_id
          AND checkpoint.workspace_id = command_workspace_id
          AND checkpoint.access_scope_id = command_access_scope_id
          AND checkpoint.checkpoint_key =
              requested_command #>> '{checkpoint,checkpoint_key}'
        FOR UPDATE;
        IF FOUND THEN
            IF checkpoint_record.checkpoint_sequence IS DISTINCT FROM
                NULLIF(requested_command #>>
                    '{checkpoint,expected_sequence}', '')::bigint THEN
                RAISE EXCEPTION 'checkpoint_conflict' USING ERRCODE = '40001';
            END IF;
            IF (requested_command #>>
                    '{checkpoint,next_sequence}')::bigint <=
                    checkpoint_record.checkpoint_sequence THEN
                RAISE EXCEPTION 'checkpoint_conflict' USING ERRCODE = '40001';
            END IF;
            UPDATE memoriesql.capture_checkpoints
               SET checkpoint_sequence =
                       (requested_command #>>
                           '{checkpoint,next_sequence}')::bigint,
                   checkpoint_hash =
                       requested_command #>> '{checkpoint,checkpoint_hash}',
                   last_event_id = command_event_id,
                   idempotency_receipt_id = new_receipt_id,
                   advanced_by_principal_id = context_record.principal_id,
                   advanced_at = database_now
             WHERE tenant_id = command_tenant_id
               AND workspace_id = command_workspace_id
               AND access_scope_id = command_access_scope_id
               AND checkpoint_key =
                   requested_command #>> '{checkpoint,checkpoint_key}';
        ELSE
            IF NULLIF(requested_command #>>
                   '{checkpoint,expected_sequence}', '')::bigint
               NOT IN (0)
               OR (requested_command #>>
                       '{checkpoint,next_sequence}')::bigint <= 0 THEN
                RAISE EXCEPTION 'checkpoint_conflict' USING ERRCODE = '40001';
            END IF;
            INSERT INTO memoriesql.capture_checkpoints (
                tenant_id, workspace_id, access_scope_id, checkpoint_key,
                checkpoint_sequence, checkpoint_hash, last_event_id,
                idempotency_receipt_id, advanced_by_principal_id, advanced_at
            ) VALUES (
                command_tenant_id, command_workspace_id,
                command_access_scope_id,
                requested_command #>> '{checkpoint,checkpoint_key}',
                (requested_command #>> '{checkpoint,next_sequence}')::bigint,
                requested_command #>> '{checkpoint,checkpoint_hash}',
                command_event_id, new_receipt_id,
                context_record.principal_id, database_now
            );
        END IF;
        returned_checkpoint_sequence :=
            (requested_command #>> '{checkpoint,next_sequence}')::bigint;
    END IF;

    database_now := pg_catalog.clock_timestamp();
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.capture', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_source_authorized(
            command_access_scope_id, command_source_object_id,
            'memory.capture', 'write'
       ) THEN
        RAISE EXCEPTION 'source event acceptance is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    IF existing_event_id IS NULL THEN
        SELECT enqueued.task_id INTO returned_task_id
        FROM memoriesql.enqueue_semantic_task(
            command_task_id,
            requested_command #>> '{semantic_task,idempotency_key}',
            'memoriesql.kernel',
            'memory.semantic.author-observations', 1,
            'df6b7094bdc407ea10464a1b9ff9020cbbc99d57160a3b86d7702d57db89f92e',
            'semantic-tasks-v1:ec2c9544c3eab504c9276e3f600ad656cbcc606dfa64c9f08aebdb6fb2e01bd4',
            command_event_id::text,
            (requested_command ->> 'expected_source_object_schema_version')::integer,
            jsonb_build_object(
                'task_id', command_task_id::text,
                'task_kind', 'memory.semantic.author-observations',
                'contract_revision', 1,
                'target_reference', command_event_id::text,
                'expected_target_revision',
                    (requested_command ->>
                        'expected_source_object_schema_version')::integer,
                'evidence_manifest', jsonb_build_object(
                    'manifest_id', 'capture.event.' || command_event_id::text,
                    'revision', 1,
                    'references', (
                        SELECT jsonb_agg(jsonb_build_object(
                            'reference_id', unit.source_unit_id::text,
                            'content_hash', unit.content_hash,
                            'declared_characters', COALESCE(
                                char_length(unit.content_text), 0
                            )
                        ) ORDER BY unit.unit_ordinal)
                        FROM memoriesql.source_units AS unit
                        WHERE unit.tenant_id = command_tenant_id
                          AND unit.event_id = command_event_id
                          AND unit.is_observation
                    )
                ),
                'requested_effort_key', NULL,
                'requested_budget', NULL,
                'payload', jsonb_build_object(
                    'event_id', command_event_id::text,
                    'bead_ids', to_jsonb(returned_bead_ids),
                    'source_unit_ids', to_jsonb(returned_unit_ids)
                )
            ),
            'capture.event.' || command_event_id::text,
            command_access_scope_id,
            GREATEST(requested_at, database_now),
            NULL,
            requested_at
        ) AS enqueued;

        INSERT INTO memoriesql.accepted_source_events (
            tenant_id, workspace_id, access_scope_id, event_id,
            acceptance_hash, capture_receipt_id, semantic_task_id, accepted_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            command_event_id, computed_acceptance_hash, new_receipt_id,
            returned_task_id, database_now
        );
    END IF;

    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id,
        idempotency_receipt_id, aggregate_kind, aggregate_id, event_kind,
        payload, headers, recorded_at, available_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        pg_catalog.uuidv7(), new_receipt_id, 'source_event', command_event_id,
        'source_event.accepted',
        jsonb_build_object(
            'event_id', command_event_id,
            'semantic_task_id', returned_task_id
        ),
        jsonb_build_object('contract_version', 1), database_now, database_now
    );
    response := jsonb_build_object(
        'event_id', command_event_id,
        'source_unit_ids', to_jsonb(returned_unit_ids),
        'bead_ids', to_jsonb(returned_bead_ids),
        'semantic_task_id', returned_task_id,
        'checkpoint_sequence', returned_checkpoint_sequence,
        'status', CASE WHEN existing_event_id IS NULL
            THEN 'accepted' ELSE 'already_exists' END
    );
    UPDATE memoriesql.idempotency_receipts AS receipt
       SET status = 'succeeded', response_receipt = response,
           updated_at = database_now, completed_at = database_now
     WHERE receipt.tenant_id = command_tenant_id
       AND receipt.idempotency_receipt_id = new_receipt_id;

    RETURN QUERY SELECT command_event_id, returned_unit_ids,
        returned_bead_ids, returned_task_id, new_receipt_id,
        returned_checkpoint_sequence,
        CASE WHEN existing_event_id IS NULL
            THEN 'accepted' ELSE 'already_exists' END,
        false;
END;
$$;

CREATE FUNCTION memoriesql.apply_semantic_annotations(
    requested_command jsonb,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_at timestamp with time zone
)
RETURNS TABLE (
    task_id uuid,
    attempt_id uuid,
    idempotency_receipt_id uuid,
    statement_ids uuid[],
    bead_version_ids uuid[],
    task_status text,
    replayed boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    bead_record memoriesql.beads%ROWTYPE;
    prior_statement memoriesql.bead_semantic_statements%ROWTYPE;
    annotation jsonb;
    statement jsonb;
    evidence jsonb;
    command_tenant_id uuid;
    command_workspace_id uuid;
    command_access_scope_id uuid;
    command_task_id uuid;
    command_attempt_id uuid;
    command_generation bigint;
    command_model_runs text[];
    computed_request_hash text;
    new_receipt_id uuid;
    new_task_receipt_id uuid;
    new_bead_version_id uuid;
    bead_type_uuid uuid;
    bead_type_revision_uuid uuid;
    current_bead_version integer;
    next_statement_sequence bigint;
    statement_uuid uuid;
    evidence_event_uuid uuid;
    outcome_status text;
    returned_statement_ids uuid[] := ARRAY[]::uuid[];
    returned_bead_version_ids uuid[] := ARRAY[]::uuid[];
    response jsonb;
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 4194304
       OR requested_command ->> 'contract_version' <> '1'
       OR requested_command ->> 'expected_schema_version' <> '11'
       OR requested_command ->> 'task_kind' <>
            'memory.semantic.author-observations'
       OR requested_command ->> 'contract_revision' <> '1'
       OR requested_command ->> 'output_contract_hash' <>
            'cfaf34db7deb7eb6e91169420b7ffd6fc7580292f658afa8c5c0c60eb9b597b4'
       OR requested_command ->> 'semantic_result_hash' !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(requested_command -> 'semantic_payload_canonical_json')
            IS DISTINCT FROM 'string'
       OR pg_catalog.octet_length(
            requested_command ->> 'semantic_payload_canonical_json'
          ) NOT BETWEEN 2 AND 12000
       OR requested_command ->> 'semantic_payload_canonical_json'
            IS DISTINCT FROM memoriesql.canonical_semantic_json_text(
                requested_command #> '{payload}'
            )
       OR encode(
            pg_catalog.sha256(pg_catalog.convert_to(
                requested_command ->> 'semantic_payload_canonical_json',
                'UTF8'
            )),
            'hex'
          ) IS DISTINCT FROM requested_command ->> 'semantic_result_hash'
       OR jsonb_typeof(requested_command #> '{payload,annotations}')
            IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command #> '{payload,annotations}')
            NOT BETWEEN 1 AND 8
       OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                requested_command #> '{payload,annotations}'
            ) AS annotation(value)
            WHERE jsonb_typeof(annotation.value -> 'statements')
                    IS DISTINCT FROM 'array'
               OR jsonb_array_length(annotation.value -> 'statements')
                    NOT BETWEEN 1 AND 64
               OR NOT memoriesql.authored_bead_render_safe(
                    annotation.value -> 'render'
               )
       )
       OR jsonb_typeof(requested_command -> 'used_evidence_refs')
            IS DISTINCT FROM 'array'
       OR jsonb_typeof(requested_command -> 'model_run_refs')
            IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command -> 'model_run_refs')
            NOT BETWEEN 1 AND 64
       OR btrim(requested_command ->> 'idempotency_key') = ''
       OR btrim(requested_worker_id) = ''
       OR btrim(requested_worker_instance_id) = ''
       OR requested_at IS NULL
       OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'semantic annotation command is invalid'
            USING ERRCODE = '22023';
    END IF;
    command_tenant_id := (requested_command ->> 'tenant_id')::uuid;
    command_workspace_id := (requested_command ->> 'workspace_id')::uuid;
    command_access_scope_id := (requested_command ->> 'access_scope_id')::uuid;
    command_task_id := (requested_command ->> 'task_id')::uuid;
    command_attempt_id := (requested_command ->> 'attempt_id')::uuid;
    command_generation := (requested_command ->> 'lease_generation')::bigint;
    command_model_runs := ARRAY(SELECT value
        FROM jsonb_array_elements_text(
            requested_command -> 'model_run_refs'
        ) AS item(value));
    computed_request_hash := encode(
        pg_catalog.sha256(pg_catalog.convert_to(requested_command::text, 'UTF8')),
        'hex'
    );

    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR context_record.tenant_id <> command_tenant_id
       OR context_record.workspace_id <> command_workspace_id
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.maintain', 'write'
       ) THEN
        RAISE EXCEPTION 'semantic annotation command is outside authorization'
            USING ERRCODE = '42501';
    END IF;

    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    JOIN memoriesql.semantic_task_attempts AS attempt
      ON attempt.tenant_id = task.tenant_id
     AND attempt.task_id = task.task_id
     AND attempt.attempt_id = command_attempt_id
     AND attempt.lease_generation = command_generation
     AND attempt.claimant_principal_id = context_record.principal_id
     AND attempt.worker_id = requested_worker_id
     AND attempt.worker_instance_id = requested_worker_instance_id
    WHERE task.tenant_id = command_tenant_id
      AND task.workspace_id = command_workspace_id
      AND task.access_scope_id = command_access_scope_id
      AND task.task_id = command_task_id
      AND task.target_kind = 'canonical_semantics'
      AND task.task_kind = requested_command ->> 'task_kind'
      AND task.contract_revision =
          (requested_command ->> 'contract_revision')::integer;
    IF NOT FOUND
       OR NOT memoriesql.current_context_semantic_task_authorized(
            command_task_id, 'memory.maintain', 'write'
       )
       OR NOT memoriesql.semantic_task_origin_authorized(
            command_tenant_id, command_task_id, database_now
       ) THEN
        RAISE EXCEPTION 'semantic annotation task is unavailable'
            USING ERRCODE = '42501';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            command_tenant_id::text || ':semantic_annotations.apply:'
                || (requested_command ->> 'idempotency_key'),
            0
        )
    );
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(
            command_access_scope_id, 'memory.maintain', 'write'
       )
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR NOT memoriesql.current_context_semantic_task_authorized(
            command_task_id, 'memory.maintain', 'write'
       )
       OR NOT memoriesql.semantic_task_origin_authorized(
            command_tenant_id, command_task_id, database_now
       ) THEN
        RAISE EXCEPTION 'semantic annotation command is outside authorization'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO receipt_record
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.operation_kind = 'semantic_annotations.apply'
      AND receipt.idempotency_key = requested_command ->> 'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF receipt_record.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF receipt_record.status <> 'succeeded'
           OR receipt_record.response_receipt IS NULL THEN
            RAISE EXCEPTION 'semantic annotation receipt is incomplete'
                USING ERRCODE = '55000';
        END IF;
        response := receipt_record.response_receipt;
        RETURN QUERY SELECT
            (response ->> 'task_id')::uuid,
            (response ->> 'attempt_id')::uuid,
            receipt_record.idempotency_receipt_id,
            ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                response -> 'statement_ids'
            ) AS item(value)),
            ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                response -> 'bead_version_ids'
            ) AS item(value)),
            'succeeded'::text,
            true;
        RETURN;
    END IF;

    SELECT task.* INTO task_record
    FROM memoriesql.semantic_tasks AS task
    WHERE task.tenant_id = command_tenant_id
      AND task.task_id = command_task_id
      AND task.status = 'running'
      AND task.target_kind = 'canonical_semantics'
      AND task.lease_generation = command_generation
      AND task.lease_owner = requested_worker_id
      AND task.worker_instance_id = requested_worker_instance_id
      AND task.result_attempt_id IS NULL
      AND task.cancel_requested_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    SELECT attempt.* INTO attempt_record
    FROM memoriesql.semantic_task_attempts AS attempt
    WHERE attempt.tenant_id = command_tenant_id
      AND attempt.task_id = command_task_id
      AND attempt.attempt_id = command_attempt_id
      AND attempt.lease_generation = command_generation
      AND attempt.claimant_principal_id = context_record.principal_id
      AND attempt.worker_id = requested_worker_id
      AND attempt.worker_instance_id = requested_worker_instance_id
      AND attempt.status = 'running'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    IF NOT memoriesql.lock_semantic_task_outcome_authority(
        command_tenant_id, command_task_id
    ) THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now
       OR attempt_record.deadline_at <= database_now
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_time_authorized(
            command_access_scope_id, 'write', database_now
       )
       OR memoriesql.reauthorize_semantic_task(
            command_tenant_id, command_task_id, command_attempt_id,
            command_generation, requested_worker_id,
            requested_worker_instance_id, 'outcome', requested_at
       ) <> 'authorized' THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    IF cardinality(command_model_runs) <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.task_id = command_task_id
          AND run.attempt_id = command_attempt_id
          AND run.lease_generation = command_generation
          AND run.run_id = ANY(command_model_runs)
    ) OR cardinality(command_model_runs) <> (
        SELECT count(*)
        FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.task_id = command_task_id
          AND run.attempt_id = command_attempt_id
          AND run.lease_generation = command_generation
    ) OR EXISTS (
        SELECT 1 FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND run.parent_run_id IS NOT NULL
          AND NOT run.settled
    ) OR 1 <> (
        SELECT count(*) FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND run.parent_run_id IS NULL
          AND run.run_status = 'running'
          AND run.run_id = ANY(command_model_runs)
    ) THEN
        RAISE EXCEPTION 'semantic annotation run tree is incomplete'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(requested_command #> '{payload,annotations}') <>
          jsonb_array_length(task_record.input_payload #> '{payload,bead_ids}')
       OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
                task_record.input_payload #> '{payload,bead_ids}'
            ) AS requested(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    requested_command #> '{payload,annotations}'
                ) AS annotation(value)
                WHERE annotation.value ->> 'bead_id' = requested.value
            )
       )
       OR EXISTS (
            SELECT annotation.value ->> 'bead_id'
            FROM jsonb_array_elements(
                requested_command #> '{payload,annotations}'
            ) AS annotation(value)
            GROUP BY annotation.value ->> 'bead_id'
            HAVING count(*) <> 1
       ) THEN
        RAISE EXCEPTION 'semantic annotation result does not cover its task'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(
            requested_command -> 'used_evidence_refs'
        ) AS used(value)
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                task_record.input_payload #> '{evidence_manifest,references}'
            ) AS declared(value)
            WHERE declared.value ->> 'reference_id' = used.value
        )
    ) THEN
        RAISE EXCEPTION 'evidence_hash_mismatch' USING ERRCODE = '22023';
    END IF;

    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, 'semantic_annotations.apply',
        requested_command ->> 'idempotency_key', computed_request_hash,
        'in_progress', 'semantic_task', command_task_id, 1,
        database_now, database_now
    );

    FOR annotation IN
        SELECT item.value
        FROM jsonb_array_elements(
            requested_command #> '{payload,annotations}'
        ) AS item(value)
        ORDER BY item.value ->> 'bead_id'
    LOOP
        SELECT * INTO bead_record
        FROM memoriesql.beads AS bead
        WHERE bead.tenant_id = command_tenant_id
          AND bead.workspace_id = command_workspace_id
          AND bead.access_scope_id = command_access_scope_id
          AND bead.bead_id = (annotation ->> 'bead_id')::uuid
          AND bead.event_id = (annotation ->> 'event_id')::uuid
          AND bead.source_unit_id = (annotation ->> 'source_unit_id')::uuid
        FOR SHARE;
        IF NOT FOUND
           OR NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(
                    task_record.input_payload #> '{payload,bead_ids}'
                ) AS requested(value)
                WHERE requested.value = annotation ->> 'bead_id'
           ) THEN
            RAISE EXCEPTION 'semantic annotation bead is unavailable'
                USING ERRCODE = '42501';
        END IF;
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                command_tenant_id::text || ':bead-semantic:'
                    || bead_record.bead_id::text,
                0
            )
        );
        database_now := pg_catalog.clock_timestamp();
        IF context_record.expires_at <= database_now
           OR NOT memoriesql.current_context_scope_authorized(
                command_access_scope_id, 'memory.maintain', 'write'
           )
           OR NOT memoriesql.current_context_scope_time_authorized(
                command_access_scope_id, 'write', database_now
           )
           OR NOT memoriesql.current_context_semantic_task_authorized(
                command_task_id, 'memory.maintain', 'write'
           )
           OR NOT memoriesql.semantic_task_origin_authorized(
                command_tenant_id, command_task_id, database_now
           )
           OR memoriesql.reauthorize_semantic_task(
                command_tenant_id, command_task_id, command_attempt_id,
                command_generation, requested_worker_id,
                requested_worker_instance_id, 'outcome', database_now
           ) <> 'authorized' THEN
            RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
        END IF;
        SELECT COALESCE(max(version.version), 0)::integer
          INTO current_bead_version
          FROM memoriesql.bead_versions AS version
         WHERE version.tenant_id = command_tenant_id
           AND version.bead_id = bead_record.bead_id;
        IF current_bead_version <>
            (annotation ->> 'expected_bead_version')::integer THEN
            RAISE EXCEPTION 'bead_version_conflict' USING ERRCODE = '40001';
        END IF;
        IF current_bead_version = 0 AND (
            NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    annotation -> 'statements'
                ) AS initial_statement(value)
                WHERE initial_statement.value ->> 'statement_kind' =
                    'observation'
            ) OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    annotation -> 'statements'
                ) AS initial_statement(value)
                WHERE initial_statement.value ->> 'statement_kind' =
                        'observation'
                  AND NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            initial_statement.value -> 'evidence'
                        ) AS initial_evidence(value)
                        WHERE initial_evidence.value ->> 'source_unit_id' =
                            bead_record.source_unit_id::text
                  )
            )
        ) THEN
            RAISE EXCEPTION
                'initial semantic version requires a self-evidenced observation'
                USING ERRCODE = '22023';
        END IF;
        SELECT bead_type.bead_type_id, revision.bead_type_revision_id
          INTO bead_type_uuid, bead_type_revision_uuid
          FROM memoriesql.bead_types AS bead_type
          JOIN memoriesql.bead_type_revisions AS revision
            ON revision.bead_type_id = bead_type.bead_type_id
           AND revision.revision =
               (annotation ->> 'bead_type_revision')::integer
           AND revision.authorable
         WHERE bead_type.stable_key = annotation ->> 'bead_type_key';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'bead type revision is unavailable'
                USING ERRCODE = '22023';
        END IF;
        new_bead_version_id := (annotation ->> 'bead_version_id')::uuid;
        new_task_receipt_id := pg_catalog.uuidv7();
        INSERT INTO memoriesql.semantic_task_receipts (
            tenant_id, workspace_id, access_scope_id,
            semantic_task_receipt_id, idempotency_receipt_id, event_id,
            source_unit_id, bead_id, task_contract_key,
            task_contract_version, input_hash, created_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_task_receipt_id, new_receipt_id, bead_record.event_id,
            bead_record.source_unit_id, bead_record.bead_id,
            task_record.task_kind, task_record.contract_revision,
            task_record.input_hash, database_now
        );
        INSERT INTO memoriesql.bead_versions (
            tenant_id, workspace_id, access_scope_id, bead_version_id,
            bead_id, event_id, source_unit_id, version, bead_type_id,
            bead_type_revision_id, authored_by_principal_id,
            semantic_task_receipt_id, render_contract_revision,
            render_payload, authored_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_bead_version_id, bead_record.bead_id, bead_record.event_id,
            bead_record.source_unit_id, current_bead_version + 1,
            bead_type_uuid, bead_type_revision_uuid, context_record.principal_id,
            new_task_receipt_id, 2, annotation -> 'render', database_now
        );

        SELECT COALESCE(max(existing.statement_sequence), 0)
          INTO next_statement_sequence
          FROM memoriesql.bead_semantic_statements AS existing
         WHERE existing.tenant_id = command_tenant_id
           AND existing.bead_id = bead_record.bead_id;
        FOR statement IN
            SELECT item.value
            FROM jsonb_array_elements(annotation -> 'statements') AS item(value)
        LOOP
            next_statement_sequence := next_statement_sequence + 1;
            statement_uuid := (statement ->> 'statement_id')::uuid;
            IF statement ->> 'model_run_ref' <> ALL(command_model_runs) THEN
                RAISE EXCEPTION 'semantic statement run is unavailable'
                    USING ERRCODE = '22023';
            END IF;
            IF statement ->> 'statement_kind' = 'correction' THEN
                SELECT * INTO prior_statement
                FROM memoriesql.bead_semantic_statements AS prior
                WHERE prior.tenant_id = command_tenant_id
                  AND prior.statement_id =
                      (statement ->> 'supersedes_statement_id')::uuid
                  AND prior.bead_id = bead_record.bead_id
                FOR SHARE;
                IF NOT FOUND OR EXISTS (
                    SELECT 1
                    FROM memoriesql.bead_semantic_statements AS superseding
                    WHERE superseding.tenant_id = command_tenant_id
                      AND superseding.supersedes_statement_id =
                          prior_statement.statement_id
                ) THEN
                    RAISE EXCEPTION 'statement_supersession_conflict'
                        USING ERRCODE = '40001';
                END IF;
            END IF;
            INSERT INTO memoriesql.bead_semantic_statements (
                tenant_id, workspace_id, access_scope_id, statement_id,
                bead_id, bead_version_id, event_id, source_unit_id,
                statement_sequence, statement_kind, statement_text,
                context_source_ids, authored_by_principal_id,
                semantic_task_id, semantic_attempt_id, semantic_run_id,
                supersedes_statement_id, correction_reason, created_at
            ) VALUES (
                command_tenant_id, command_workspace_id,
                command_access_scope_id, statement_uuid, bead_record.bead_id,
                new_bead_version_id, bead_record.event_id,
                bead_record.source_unit_id, next_statement_sequence,
                statement ->> 'statement_kind', statement ->> 'statement_text',
                ARRAY(SELECT value::uuid FROM jsonb_array_elements_text(
                    statement -> 'context_source_ids'
                ) AS item(value)),
                context_record.principal_id, command_task_id,
                command_attempt_id, statement ->> 'model_run_ref',
                NULLIF(statement ->> 'supersedes_statement_id', '')::uuid,
                NULLIF(statement ->> 'correction_reason', ''), database_now
            );

            FOR evidence IN
                SELECT item.value
                FROM jsonb_array_elements(statement -> 'evidence') AS item(value)
            LOOP
                SELECT unit.event_id INTO evidence_event_uuid
                FROM memoriesql.source_units AS unit
                WHERE unit.tenant_id = command_tenant_id
                  AND unit.workspace_id = command_workspace_id
                  AND unit.access_scope_id = command_access_scope_id
                  AND unit.source_unit_id =
                      (evidence ->> 'source_unit_id')::uuid
                  AND unit.content_hash = evidence ->> 'content_hash'
                  AND memoriesql.current_context_event_authorized(
                      unit.access_scope_id, unit.event_id,
                      'memory.maintain', 'read'
                  );
                IF NOT FOUND OR NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        task_record.input_payload #> '{evidence_manifest,references}'
                    ) AS declared(value)
                    WHERE declared.value ->> 'reference_id' =
                              evidence ->> 'source_unit_id'
                      AND declared.value ->> 'content_hash' =
                              evidence ->> 'content_hash'
                ) OR NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(
                        requested_command -> 'used_evidence_refs'
                    ) AS used(value)
                    WHERE used.value = evidence ->> 'source_unit_id'
                ) THEN
                    RAISE EXCEPTION 'evidence_hash_mismatch'
                        USING ERRCODE = '22023';
                END IF;
                INSERT INTO memoriesql.bead_semantic_statement_evidence (
                    tenant_id, workspace_id, access_scope_id, statement_id,
                    evidence_event_id, evidence_source_unit_id,
                    evidence_content_hash, linked_at
                ) VALUES (
                    command_tenant_id, command_workspace_id,
                    command_access_scope_id, statement_uuid,
                    evidence_event_uuid,
                    (evidence ->> 'source_unit_id')::uuid,
                    evidence ->> 'content_hash', database_now
                );
            END LOOP;
            IF EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(
                    statement -> 'context_source_ids'
                ) AS context_source(value)
                WHERE NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(
                        statement -> 'evidence'
                    ) AS linked(value)
                    WHERE linked.value ->> 'source_unit_id' =
                        context_source.value
                )
            ) THEN
                RAISE EXCEPTION 'context source lacks statement evidence'
                    USING ERRCODE = '22023';
            END IF;
            returned_statement_ids :=
                array_append(returned_statement_ids, statement_uuid);
        END LOOP;
        INSERT INTO memoriesql.bead_statement_revisions (
            tenant_id, workspace_id, access_scope_id, bead_version_id,
            bead_id, event_id, source_unit_id, statement_watermark,
            semantic_task_receipt_id, created_at
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_bead_version_id, bead_record.bead_id, bead_record.event_id,
            bead_record.source_unit_id, next_statement_sequence,
            new_task_receipt_id, database_now
        );
        returned_bead_version_ids :=
            array_append(returned_bead_version_ids, new_bead_version_id);
    END LOOP;

    outcome_status := memoriesql.record_semantic_task_outcome(
        command_tenant_id, command_task_id, command_attempt_id,
        command_generation, requested_worker_id, requested_worker_instance_id,
        'succeeded', requested_command ->> 'semantic_result_hash',
        'semantic.application.' || command_attempt_id::text,
        NULL, NULL, NULL, NULL, 0, requested_at
    );
    IF outcome_status <> 'succeeded' THEN
        RAISE EXCEPTION 'semantic task success settlement was rejected: %',
            outcome_status USING ERRCODE = '40001';
    END IF;
    UPDATE memoriesql.semantic_task_runs AS run
       SET run_status = 'succeeded', finished_at = database_now, settled = true
     WHERE run.tenant_id = command_tenant_id
       AND run.task_id = command_task_id
       AND run.attempt_id = command_attempt_id
       AND run.lease_generation = command_generation
       AND run.parent_run_id IS NULL
       AND run.run_status = 'running'
       AND run.run_id = ANY(command_model_runs);
    IF 1 <> (
        SELECT count(*) FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND run.parent_run_id IS NULL
          AND run.run_status = 'succeeded'
          AND run.settled
    ) OR EXISTS (
        SELECT 1 FROM memoriesql.semantic_task_runs AS run
        WHERE run.tenant_id = command_tenant_id
          AND run.attempt_id = command_attempt_id
          AND NOT run.settled
    ) THEN
        RAISE EXCEPTION 'semantic annotation run tree did not settle'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id,
        idempotency_receipt_id, aggregate_kind, aggregate_id, event_kind,
        payload, headers, recorded_at, available_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        pg_catalog.uuidv7(), new_receipt_id, 'semantic_task', command_task_id,
        'semantic_annotations.applied',
        jsonb_build_object(
            'task_id', command_task_id,
            'attempt_id', command_attempt_id,
            'statement_count', cardinality(returned_statement_ids),
            'bead_version_count', cardinality(returned_bead_version_ids)
        ),
        jsonb_build_object('contract_version', 1), database_now, database_now
    );
    response := jsonb_build_object(
        'task_id', command_task_id,
        'attempt_id', command_attempt_id,
        'statement_ids', to_jsonb(returned_statement_ids),
        'bead_version_ids', to_jsonb(returned_bead_version_ids),
        'task_status', 'succeeded'
    );
    UPDATE memoriesql.idempotency_receipts AS receipt
       SET status = 'succeeded', response_receipt = response,
           updated_at = database_now, completed_at = database_now
     WHERE receipt.tenant_id = command_tenant_id
       AND receipt.idempotency_receipt_id = new_receipt_id;

    RETURN QUERY SELECT command_task_id, command_attempt_id, new_receipt_id,
        returned_statement_ids, returned_bead_version_ids,
        'succeeded'::text, false;
END;
$$;

CREATE POLICY accepted_source_events_read
ON memoriesql.accepted_source_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_event_authorized(
        access_scope_id, event_id, 'source.read', 'read'
    )
);
CREATE POLICY capture_checkpoints_read
ON memoriesql.capture_checkpoints
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_scope_authorized(
        access_scope_id, 'memory.capture', 'read'
    )
);
DROP POLICY bead_versions_read ON memoriesql.bead_versions;
CREATE POLICY bead_versions_read
ON memoriesql.bead_versions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_event_authorized(
        access_scope_id, event_id, 'source.read', 'read'
    ) AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, bead_version_id
    )
);
CREATE POLICY bead_semantic_statements_read
ON memoriesql.bead_semantic_statements
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_semantic_statement_authorized(
        tenant_id, workspace_id, access_scope_id, statement_id
    )
);
CREATE POLICY bead_semantic_statement_evidence_read
ON memoriesql.bead_semantic_statement_evidence
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_semantic_statement_authorized(
        tenant_id, workspace_id, access_scope_id, statement_id
    )
);
CREATE POLICY bead_statement_revisions_read
ON memoriesql.bead_statement_revisions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_event_authorized(
        access_scope_id, event_id, 'memory.query', 'read'
    ) AND memoriesql.current_context_bead_revision_authorized(
        tenant_id, workspace_id, access_scope_id, bead_id,
        statement_watermark
    )
);

CREATE FUNCTION memoriesql.assert_bead_render_covers_active_statements()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    pinned_watermark bigint;
BEGIN
    IF NEW.semantic_task_receipt_id IS NULL
       OR NEW.render_contract_revision = 1 THEN
        RETURN NULL;
    END IF;
    SELECT revision.statement_watermark
      INTO pinned_watermark
      FROM memoriesql.bead_statement_revisions AS revision
     WHERE revision.tenant_id = NEW.tenant_id
       AND revision.bead_version_id = NEW.bead_version_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'authored bead render requires a statement watermark'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        WITH active AS (
            SELECT statement.statement_id
            FROM memoriesql.bead_semantic_statements AS statement
            WHERE statement.tenant_id = NEW.tenant_id
              AND statement.bead_id = NEW.bead_id
              AND statement.statement_sequence <= pinned_watermark
              AND NOT EXISTS (
                    SELECT 1
                    FROM memoriesql.bead_semantic_statements AS correction
                    WHERE correction.tenant_id = statement.tenant_id
                      AND correction.bead_id = statement.bead_id
                      AND correction.statement_sequence <= pinned_watermark
                      AND correction.supersedes_statement_id = statement.statement_id
              )
        ), clauses AS (
            SELECT NEW.render_payload -> 'title' AS value
            UNION ALL
            SELECT item.value
            FROM jsonb_array_elements(NEW.render_payload -> 'summary') AS item(value)
            UNION ALL
            SELECT item.value
            FROM jsonb_array_elements(NEW.render_payload -> 'detail') AS item(value)
        ), classified AS (
            SELECT included.value::uuid AS statement_id
            FROM clauses
            CROSS JOIN LATERAL jsonb_array_elements_text(
                clauses.value -> 'statement_ids'
            ) AS included(value)
            UNION
            SELECT (item.value ->> 'statement_id')::uuid
            FROM jsonb_array_elements(NEW.render_payload -> 'omissions') AS item(value)
        ), mismatch AS (
            (SELECT statement_id FROM active EXCEPT SELECT statement_id FROM classified)
            UNION ALL
            (SELECT statement_id FROM classified EXCEPT SELECT statement_id FROM active)
        )
        SELECT 1 FROM mismatch
    ) THEN
        RAISE EXCEPTION 'authored bead render coverage does not match the active statement fold'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER bead_versions_require_render_coverage
AFTER INSERT ON memoriesql.bead_versions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_bead_render_covers_active_statements();

ALTER TABLE memoriesql.accepted_source_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.accepted_source_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.capture_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.capture_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_semantic_statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_semantic_statements FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_semantic_statement_evidence
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_semantic_statement_evidence
    FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_statement_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_statement_revisions FORCE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA memoriesql FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memoriesql FROM PUBLIC;

GRANT SELECT ON memoriesql.accepted_source_events,
    memoriesql.capture_checkpoints,
    memoriesql.bead_semantic_statements,
    memoriesql.bead_semantic_statement_evidence,
    memoriesql.bead_statement_revisions
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.accept_source_event(
    jsonb, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.apply_semantic_annotations(
    jsonb, text, text, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_semantic_statement_authorized(
    uuid, uuid, uuid, uuid
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_bead_version_authorized(
    uuid, uuid, uuid, uuid
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_bead_revision_authorized(
    uuid, uuid, uuid, uuid, bigint
) TO memoriesql_application, memoriesql_worker;

COMMENT ON TABLE memoriesql.accepted_source_events IS
    'Exact command-content seal for one atomically accepted source event and its canonical authoring task.';
COMMENT ON TABLE memoriesql.capture_checkpoints IS
    'Minimal internal monotonic checkpoint advanced only inside the source-event acceptance transaction; PR-02A owns the public cursor contract.';
COMMENT ON TABLE memoriesql.bead_semantic_statements IS
    'Append-only evidence-bound atomic authored meaning; correction uses explicit supersession and prior statements remain immutable.';
COMMENT ON TABLE memoriesql.bead_semantic_statement_evidence IS
    'Exact source-unit content hashes supporting one atomic semantic statement.';
COMMENT ON TABLE memoriesql.bead_statement_revisions IS
    'Immutable statement watermark pinned to an authored bead version.';
COMMENT ON COLUMN memoriesql.bead_versions.render_payload IS
    'Strict authored rich-render clauses, statement coverage, and explicit omissions; generated title, summary, and detail cannot drift from it.';
COMMENT ON COLUMN memoriesql.bead_versions.render_contract_revision IS
    'Revision 1 identifies preserved pre-PR-02 versions without authored renders; revision 2 requires the rich render for receipt-backed semantic versions.';
COMMENT ON FUNCTION memoriesql.accept_source_event(
    jsonb, timestamp with time zone
) IS
    'Atomically accepts one normalized internal source event, units, thin beads, optional checkpoint, canonical authoring enqueue, outbox, and unified receipt without model work.';
COMMENT ON FUNCTION memoriesql.apply_semantic_annotations(
    jsonb, text, text, timestamp with time zone
) IS
    'Atomically applies fenced evidence-bound statements, a coverage-bound rich render, explicit supersession, watermarks, task success, run settlement, outbox, and unified receipt; any rejected result rolls back all canonical semantics.';
COMMENT ON FUNCTION memoriesql.current_context_semantic_statement_authorized(
    uuid, uuid, uuid, uuid
) IS
    'Fail-closed read authorization for a semantic statement and every evidence event it cites.';
COMMENT ON FUNCTION memoriesql.current_context_bead_version_authorized(
    uuid, uuid, uuid, uuid
) IS
    'Fail-closed read authorization for every semantic statement and evidence event authored into a bead version; legacy structural versions without canonical statements retain event authorization.';
COMMENT ON FUNCTION memoriesql.current_context_bead_revision_authorized(
    uuid, uuid, uuid, uuid, bigint
) IS
    'Fail-closed provenance authorization for every statement and evidence event at a bead revision watermark.';
