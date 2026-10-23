-- Request headroom for the repair round (guard audit P1-f D2, owner decision
-- 2026-10-23): published contract revisions with one more request.
--
-- Part 1, memory.semantic.author-complete-unit revision 8. Revision 2 spends its
-- whole request ceiling (8) on up to eight inspection windows, so a unit that
-- uses them all has no request left for the evidence-free repair round of
-- guard audit P1-f D1. Revision 8 is revision 2 with request_limit 9. Its
-- inputs, output contract, exposure, apply and render semantics are revision
-- 2's; only the ceiling and the identifiers that pin it are new.
--
-- This migration admits revision 8 beside revision 2:
-- - the execution revision CHECKs accept 8;
-- - an admission policy pins the revision-8 task contract and registry;
-- - activate_complete_input_v7 activates revision 8, as v1 does revision 2,
--   and only under a dispatch policy provisioned for revision 8;
-- - every installed function that treats revision 2 specially is restated
--   with revision 8 (apply command contract version 9, operation
--   complete_input.apply.v7) handled exactly like it, and nothing else
--   changed. Each restatement is generated from the installed text and held
--   to it by tests/unit/test_repair_headroom_migration.py.
--
-- Revision-2 tasks, executions, dispatch policies and receipts keep their pins
-- and their budget: nothing moves them to revision 8. A deployment opts in by
-- provisioning a revision-8 dispatch policy and activating with v7.
-- Forward-only; this file's number is provisional and is assigned at landing.

ALTER TABLE memoriesql.complete_input_executions
    DROP CONSTRAINT complete_input_executions_execution_contract_revision_check,
    ADD CONSTRAINT complete_input_executions_execution_contract_revision_check
        CHECK (execution_contract_revision IN (2, 3, 4, 5, 6, 7, 8));
ALTER TABLE memoriesql.complete_input_dispatch_policies
    DROP CONSTRAINT complete_input_dispatch_polic_execution_contract_revision_check,
    ADD CONSTRAINT complete_input_dispatch_polic_execution_contract_revision_check
        CHECK (execution_contract_revision IN (2, 3, 4, 5, 6, 7, 8));

INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES ('semantic-tasks-v1:e97805481c3507604e33f40351f681631490e087ecb3ea9174b740afd8475e02','memory.semantic.author-complete-unit',8,'memoriesql.kernel','6238dcd66fa6da02d29e71848086b64be9eda1867070369224287ce6eb356416','canonical_semantics','memory.capture','capture',50,3,NULL,NULL);

-- From 0052_partial_observation_acceptance.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.apply_semantic_annotations_schema29(
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
    is_v2 boolean;
    is_complete boolean;
    is_correction boolean;
    operation_key text;
    target_pin jsonb;
    annotation jsonb;
    mention jsonb;
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
    withheld_beads text[];
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    is_complete := requested_command->>'contract_version' IN ('3','4','5','6','7','8','9');
    is_v2 := requested_command ->> 'contract_version' IS NOT DISTINCT FROM '2';
    is_correction := is_v2 AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM
        'memory.semantic.correct-observation';
    operation_key := CASE WHEN requested_command->>'contract_version'='9' THEN 'complete_input.apply.v7' WHEN requested_command->>'contract_version'='8' THEN 'complete_input.apply.v6' WHEN requested_command->>'contract_version'='7' THEN 'complete_input.apply.v5' WHEN requested_command->>'contract_version'='6' THEN 'complete_input.apply.v4' WHEN requested_command->>'contract_version'='5' THEN 'complete_input.apply.v3' WHEN requested_command->>'contract_version'='4' THEN 'complete_input.apply.v2' WHEN is_complete THEN 'complete_input.apply.v1' WHEN is_correction THEN 'observation_correction.apply.v2'
                          WHEN is_v2 THEN 'initial_observations.apply.v2'
                          ELSE 'semantic_annotations.apply' END;
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 4194304
       OR NOT (
            (requested_command ->> 'contract_version' IS NOT DISTINCT FROM '1'
             AND requested_command ->> 'expected_schema_version' IS NOT DISTINCT FROM '11'
             AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM 'memory.semantic.author-observations'
             AND requested_command ->> 'contract_revision' IS NOT DISTINCT FROM '1'
             AND requested_command ->> 'output_contract_hash' IS NOT DISTINCT FROM
                 'cfaf34db7deb7eb6e91169420b7ffd6fc7580292f658afa8c5c0c60eb9b597b4')
            OR (requested_command->>'contract_version'='3' AND requested_command->>'expected_schema_version'='18'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit'
                AND requested_command->>'contract_revision'='2'
                AND requested_command->>'output_contract_hash'='7399c5039812e98d4d53566cb028350effb476a395a83c6fa17b8445ad9f1124')
            OR (requested_command->>'contract_version'='4' AND requested_command->>'expected_schema_version'='20'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='3'
                AND requested_command->>'output_contract_hash'='7399c5039812e98d4d53566cb028350effb476a395a83c6fa17b8445ad9f1124')
            OR (requested_command->>'contract_version'='5' AND requested_command->>'expected_schema_version'='22'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='4'
                AND requested_command->>'output_contract_hash'='0baecb82f0993a29dbc6c1afb6e41a5f3b83b8ebfe1f70315d87b5a7672d8e8c')
            OR (requested_command->>'contract_version'='6' AND requested_command->>'expected_schema_version'='23'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='5'
                AND requested_command->>'output_contract_hash'='2f54df896c41098f1d1e2eac525157f15c892e8690b277957793173a1c9039eb')
            OR (requested_command->>'contract_version'='7' AND requested_command->>'expected_schema_version'='27'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='6'
                AND requested_command->>'output_contract_hash'='7873968a84f6e7279040e45045a4948068c0cde6fcfdb68b417698ad330e0cfd')
            OR (requested_command->>'contract_version'='8' AND requested_command->>'expected_schema_version'='41'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='7'
                AND requested_command->>'output_contract_hash'='360953819cc83cc29ccaaa629e4d505aab2e289c0618dd6e42f0a7cc9213c638')
            OR (requested_command->>'contract_version'='9' AND requested_command->>'expected_schema_version'='53'
                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='8'
                AND requested_command->>'output_contract_hash'='7399c5039812e98d4d53566cb028350effb476a395a83c6fa17b8445ad9f1124')
            OR (is_v2
             AND requested_command ->> 'expected_schema_version' IS NOT DISTINCT FROM '15'
             AND requested_command ->> 'contract_revision' IS NOT DISTINCT FROM '2'
             AND ((requested_command ->> 'task_kind' IS NOT DISTINCT FROM 'memory.semantic.author-observations'
                   AND requested_command ->> 'output_contract_hash' IS NOT DISTINCT FROM
                       '3ba45185860773e367a43c8596ff8d354fd56442a389a4f95bc7a2c448c6d43c')
                  OR (is_correction AND requested_command ->> 'output_contract_hash' IS NOT DISTINCT FROM
                       'e6a5e62a3fc5669a1c6de044edd5e21f555a85e01789f11c6e762abb7ec6cb1b')))
       )
       OR requested_command ->> 'semantic_result_hash' !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(requested_command -> 'semantic_payload_canonical_json')
            IS DISTINCT FROM 'string'
       OR pg_catalog.octet_length(
            requested_command ->> 'semantic_payload_canonical_json'
          ) NOT BETWEEN 2 AND (CASE WHEN requested_command->>'contract_version'='7' THEN 32768 ELSE 12000 END)
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
            command_tenant_id::text || ':' || operation_key || ':'
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
    IF requested_command->>'contract_version' IN ('4','5','6','7','8') THEN
        PERFORM memoriesql.source_revisiting_authorize(command_tenant_id,command_task_id);
    END IF;
    IF is_complete THEN
        PERFORM memoriesql.complete_input_authorize(command_tenant_id,
            (task_record.input_payload#>>'{payload,binding_task_id}')::uuid,
            (task_record.input_payload#>>'{payload,dispatch_policy_id}')::uuid);
    END IF;
    SELECT * INTO receipt_record
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = command_tenant_id
      AND receipt.operation_kind = operation_key
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
    IF is_complete AND (
        NOT memoriesql.complete_input_exposure_valid(command_tenant_id,command_task_id,command_attempt_id,command_generation)
        OR jsonb_array_length(requested_command#>'{payload,annotations}')<>1
        OR requested_command#>>'{payload,annotations,0,expected_bead_version}' IS DISTINCT FROM '0'
        OR requested_command#>>'{payload,annotations,0,event_id}' IS DISTINCT FROM task_record.input_payload#>>'{payload,event_id}'
        OR requested_command#>>'{payload,annotations,0,source_unit_id}' IS DISTINCT FROM task_record.input_payload#>>'{payload,source_unit_ids,0}'
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(requested_command#>'{payload,annotations,0,statements}') q WHERE q->>'statement_kind'='correction')
    ) THEN RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
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
    -- Withheld beads (recorded for this attempt by
    -- record_withheld_semantic_output_v1) cover their part of the task; they
    -- are never accepted, and no withheld bead may also be annotated.
    withheld_beads := ARRAY(
        SELECT withheld.item_id::text
        FROM memoriesql.semantic_withheld_output_items AS withheld
        WHERE withheld.tenant_id = command_tenant_id
          AND withheld.attempt_id = command_attempt_id
          AND withheld.item_kind = 'bead'
    );
    IF jsonb_array_length(requested_command #> '{payload,annotations}')
          + cardinality(withheld_beads) <>
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
              AND requested.value <> ALL(withheld_beads)
       )
       OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                requested_command #> '{payload,annotations}'
            ) AS annotation(value)
            WHERE annotation.value ->> 'bead_id' = ANY(withheld_beads)
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

    -- Explicit v2 input/output identity and prior-target pins. There is no
    -- comparison to a global head, unrelated source revision or branch winner.
    IF is_v2 THEN
        IF NOT memoriesql.semantic_task_input_reference_safe(task_record.input_payload, 32768)
           OR jsonb_array_length(requested_command -> 'used_evidence_refs') NOT BETWEEN 1 AND 8
           OR (SELECT count(*) <> count(DISTINCT value)
               FROM jsonb_array_elements_text(requested_command -> 'used_evidence_refs'))
           OR (SELECT COALESCE(sum(char_length(unit.content_text)),0) > 4096
                       OR COALESCE(sum(octet_length(memoriesql.canonical_semantic_json_text(to_jsonb(unit.content_text))) - 2),0) > 4096
               FROM memoriesql.source_units AS unit
               WHERE unit.tenant_id=command_tenant_id
                 AND unit.workspace_id=command_workspace_id
                 AND unit.access_scope_id=command_access_scope_id
                 AND unit.source_unit_id::text IN (SELECT jsonb_array_elements_text(requested_command -> 'used_evidence_refs'))) THEN
            RAISE EXCEPTION 'v2 observation evidence exceeds the existing bounded envelope'
                USING ERRCODE = '22023';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(requested_command #> '{payload,annotations}') AS item(value)
            WHERE value ->> 'expected_bead_version' IS DISTINCT FROM '0'
               OR value ->> 'event_id' IS DISTINCT FROM task_record.input_payload #>> '{payload,event_id}'
               OR value ->> 'source_unit_id' NOT IN (
                   SELECT jsonb_array_elements_text(task_record.input_payload #> '{payload,source_unit_ids}')
               )
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(value -> 'statements') AS s(value)
                          WHERE s.value ->> 'statement_kind' = 'correction')
        ) THEN
            RAISE EXCEPTION 'v2 requires complete initial semantics for distinct beads'
                USING ERRCODE = '22023';
        END IF;
        IF is_correction THEN
            IF requested_command #> '{payload,supersedes}' IS DISTINCT FROM
                    task_record.input_payload #> '{payload,supersedes}'
               OR NOT memoriesql.semantic_task_input_reference_safe(task_record.input_payload, 32768)
               OR jsonb_array_length(requested_command #> '{payload,annotations}') <> 1
               OR COALESCE(btrim(requested_command #>> '{payload,correction_reason}'), '') = ''
               OR char_length(requested_command #>> '{payload,correction_reason}') > 1024 THEN
                RAISE EXCEPTION 'correction targets do not match the authorized task'
                    USING ERRCODE = '22023';
            END IF;
            FOR target_pin IN SELECT value FROM jsonb_array_elements(
                requested_command #> '{payload,supersedes}'
            ) ORDER BY value ->> 'bead_id'
            LOOP
                IF NOT EXISTS (
                    SELECT 1 FROM memoriesql.accepted_bead_semantics AS accepted
                    WHERE accepted.tenant_id = command_tenant_id
                      AND accepted.workspace_id = command_workspace_id
                      AND accepted.access_scope_id = command_access_scope_id
                      AND accepted.bead_id = (target_pin ->> 'bead_id')::uuid
                      AND accepted.bead_version_id = (target_pin ->> 'bead_version_id')::uuid
                ) OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                    command_tenant_id, command_workspace_id, command_access_scope_id,
                    (target_pin ->> 'bead_version_id')::uuid
                ) THEN
                    RAISE EXCEPTION 'supersession target unavailable or stale'
                        USING ERRCODE = '42501';
                END IF;
            END LOOP;
            annotation := requested_command #> '{payload,annotations,0}';
            IF NOT EXISTS (
                SELECT 1 FROM memoriesql.source_units AS unit
                WHERE unit.tenant_id = command_tenant_id
                  AND unit.workspace_id = command_workspace_id
                  AND unit.access_scope_id = command_access_scope_id
                  AND unit.event_id = (annotation ->> 'event_id')::uuid
                  AND unit.source_unit_id = (annotation ->> 'source_unit_id')::uuid
                  AND unit.is_observation
            ) OR NOT memoriesql.current_context_event_authorized(
                command_access_scope_id, (annotation ->> 'event_id')::uuid,
                'memory.maintain', 'read'
            ) THEN
                RAISE EXCEPTION 'correction evidence anchor is unavailable'
                    USING ERRCODE = '42501';
            END IF;
            -- PK conflict rejects any preexisting ID; never adopt or edit it.
            INSERT INTO memoriesql.beads (
                tenant_id, workspace_id, access_scope_id, bead_id,
                event_id, source_unit_id, created_at, origin_kind
            ) VALUES (
                command_tenant_id, command_workspace_id, command_access_scope_id,
                (annotation ->> 'bead_id')::uuid, (annotation ->> 'event_id')::uuid,
                (annotation ->> 'source_unit_id')::uuid, database_now, 'correction'
            );
        END IF;
    END IF;

    new_receipt_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status,
        resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        command_tenant_id, command_workspace_id, command_access_scope_id,
        new_receipt_id, operation_key,
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
           OR (is_v2 AND NOT is_correction AND bead_record.origin_kind <> 'initial')
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
        IF is_correction AND (
            NOT memoriesql.current_context_scope_time_authorized(
                command_access_scope_id, 'read', database_now
            ) OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(requested_command #> '{payload,supersedes}') AS pin(value)
                WHERE NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                    command_tenant_id, command_workspace_id, command_access_scope_id,
                    (pin.value ->> 'bead_version_id')::uuid
                )
            )
        ) THEN
            RAISE EXCEPTION 'supersession target authorization expired' USING ERRCODE = '42501';
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
        IF requested_command->>'contract_version'='6' THEN
            PERFORM memoriesql.validate_classification_acceptance(command_tenant_id,command_task_id,command_attempt_id,
                requested_command#>'{payload,classification}', requested_command#>'{payload,annotations}');
        ELSIF requested_command#>'{payload,classification}' IS NOT NULL THEN
            RAISE EXCEPTION 'legacy_output_does_not_support_classification' USING ERRCODE='22023';
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
            render_payload, authored_at, classification_contribution
        ) VALUES (
            command_tenant_id, command_workspace_id, command_access_scope_id,
            new_bead_version_id, bead_record.bead_id, bead_record.event_id,
            bead_record.source_unit_id, current_bead_version + 1,
            bead_type_uuid, bead_type_revision_uuid, context_record.principal_id,
            new_task_receipt_id, 2, annotation -> 'render', database_now, CASE WHEN requested_command->>'contract_version'='6' THEN requested_command#>'{payload,classification}' ELSE NULL END
        );

        IF requested_command->>'contract_version' IN ('5','6','7','8') THEN
            IF jsonb_typeof(annotation->'mentions') IS DISTINCT FROM 'array'
               OR jsonb_array_length(annotation->'mentions') > 32 THEN
                RAISE EXCEPTION 'authored_mentions_required' USING ERRCODE='22023';
            END IF;
            FOR mention IN SELECT value FROM jsonb_array_elements(annotation->'mentions') LOOP
                IF jsonb_typeof(mention) IS DISTINCT FROM 'object'
                   OR mention-ARRAY['entity_mention_id','surface_text','local_identity_state','local_identity_reason']<>'{}'::jsonb
                   OR jsonb_typeof(mention->'entity_mention_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(mention->'surface_text') IS DISTINCT FROM 'string'
                   OR NOT COALESCE(mention->>'surface_text' ~ '[^[:space:]]',false)
                   OR NOT COALESCE(mention->>'local_identity_state' IN ('unresolved','ambiguous'),false)
                   OR (mention ? 'local_identity_reason' AND jsonb_typeof(mention->'local_identity_reason') NOT IN ('string','null')) THEN
                    RAISE EXCEPTION 'invalid_authored_mention' USING ERRCODE='22023';
                END IF;
                INSERT INTO memoriesql.entity_mentions (
                    tenant_id,workspace_id,access_scope_id,entity_mention_id,
                    bead_version_id,bead_id,event_id,source_unit_id,surface_text,
                    recorded_by_principal_id,recorded_at,local_identity_state,local_identity_reason
                ) VALUES (
                    command_tenant_id,command_workspace_id,command_access_scope_id,
                    (mention->>'entity_mention_id')::uuid,new_bead_version_id,
                    bead_record.bead_id,bead_record.event_id,bead_record.source_unit_id,
                    mention->>'surface_text',context_record.principal_id,database_now,
                    mention->>'local_identity_state',mention->>'local_identity_reason'
                );
            END LOOP;
        ELSIF annotation ? 'mentions' THEN
            RAISE EXCEPTION 'legacy_output_does_not_support_mentions' USING ERRCODE='22023';
        END IF;

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

    -- Revision 6: claims, relations, coverage and claim judgments join the same
    -- acceptance transaction, attributed to the authoring root run.
    IF requested_command->>'contract_version'='7' THEN
        PERFORM memoriesql.apply_authored_relations_v1(
            command_tenant_id, command_workspace_id, command_access_scope_id, command_task_id,
            command_attempt_id, (requested_command#>>'{payload,annotations,0,bead_id}')::uuid,
            (requested_command#>>'{payload,annotations,0,bead_version_id}')::uuid, new_receipt_id,
            requested_command->'payload',
            (SELECT run.run_id FROM memoriesql.semantic_task_runs AS run
             WHERE run.tenant_id = command_tenant_id AND run.attempt_id = command_attempt_id
               AND run.parent_run_id IS NULL AND run.run_status = 'running'
               AND run.run_id = ANY(command_model_runs)),
            context_record.principal_id, database_now);
    ELSIF requested_command->'payload' ?| ARRAY['relations','candidate_assessments','claims','claim_updates'] THEN
        RAISE EXCEPTION 'legacy_output_does_not_support_relations' USING ERRCODE='22023';
    END IF;

    IF is_correction THEN
        INSERT INTO memoriesql.bead_supersessions (
            tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id,
            superseded_bead_id, superseded_bead_version_id, correction_reason
        ) SELECT command_tenant_id, command_workspace_id, command_access_scope_id,
                 (requested_command #>> '{payload,annotations,0,bead_id}')::uuid,
                 (requested_command #>> '{payload,annotations,0,bead_version_id}')::uuid,
                 (pin.value ->> 'bead_id')::uuid, (pin.value ->> 'bead_version_id')::uuid,
                 requested_command #>> '{payload,correction_reason}'
          FROM jsonb_array_elements(requested_command #> '{payload,supersedes}') AS pin(value);
    END IF;

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
        CASE WHEN is_v2 OR is_complete THEN operation_key ELSE 'semantic_annotations.applied' END,
        jsonb_build_object(
            'task_id', command_task_id,
            'attempt_id', command_attempt_id,
            'statement_count', cardinality(returned_statement_ids),
            'bead_version_count', cardinality(returned_bead_version_ids)
        ),
        jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='9' THEN 9 WHEN requested_command->>'contract_version'='8' THEN 8 WHEN requested_command->>'contract_version'='7' THEN 7 WHEN requested_command->>'contract_version'='6' THEN 6 WHEN requested_command->>'contract_version'='5' THEN 5 WHEN requested_command->>'contract_version'='4' THEN 4 WHEN is_complete THEN 3 WHEN is_v2 THEN 2 ELSE 1 END), database_now, database_now
    );
    response := jsonb_build_object(
        'task_id', command_task_id,
        'attempt_id', command_attempt_id,
        'statement_ids', to_jsonb(returned_statement_ids),
        'bead_version_ids', to_jsonb(returned_bead_version_ids),
        'task_status', 'succeeded'
    );
    IF is_v2 THEN
        response := response || jsonb_build_object(
            'contract_version', 2, 'operation', operation_key,
            'bead_ids', (SELECT jsonb_agg(value -> 'bead_id' ORDER BY value ->> 'bead_id')
                         FROM jsonb_array_elements(requested_command #> '{payload,annotations}')),
            'supersedes', CASE WHEN is_correction THEN requested_command #> '{payload,supersedes}'
                              ELSE '[]'::jsonb END
        );
    END IF;
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

-- From 0041_authored_statement_kinds.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.assert_complete_execution_binding() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,memoriesql AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    t memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; expected jsonb;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' OR NEW.contract_revision NOT IN (2,3,4,5,6,7,8) THEN RETURN NULL; END IF;
        SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=NEW.tenant_id AND execution_task_id=NEW.task_id;
    ELSE e:=NEW; END IF;
    SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=e.tenant_id AND task_id=e.execution_task_id;
    SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=e.tenant_id AND task_id=e.binding_task_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=e.tenant_id AND package_id=b.package_id;
    expected:=jsonb_build_object('task_id',t.task_id,'task_kind','memory.semantic.author-complete-unit','contract_revision',2,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||t.task_id::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',e.dispatch_policy_id,'declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_complete_input_exposure_v1'));
    IF e.execution_contract_revision IN (3,4,5,6,7) THEN
        expected:=jsonb_set(expected,'{contract_revision}',to_jsonb(e.execution_contract_revision));
        expected:=jsonb_set(expected,'{payload,required_execution}','"trusted_source_revisiting_v1"');
        expected:=jsonb_set(expected,'{payload,authorized_context}',e.authorized_context);
    END IF;
    IF e.execution_contract_revision=5 THEN expected:=jsonb_set(expected,'{payload,classification_vocabulary}',e.classification_vocabulary); END IF;
    IF e.execution_contract_revision=6 THEN
        expected:=jsonb_set(jsonb_set(expected,'{payload,relation_candidates}',e.relation_candidates),'{payload,relation_vocabulary}',e.relation_vocabulary);
    END IF;
    IF e.execution_contract_revision=8 THEN expected:=jsonb_set(expected,'{contract_revision}','8'); END IF;
    IF e.execution_task_id IS NULL OR b.task_id IS NULL OR t.task_id IS NULL OR t.input_payload IS DISTINCT FROM expected OR t.rerun_of_task_id IS DISTINCT FROM b.task_id OR
       t.origin_principal_id IS DISTINCT FROM p.producer_principal_id OR t.access_scope_id IS DISTINCT FROM b.access_scope_id OR t.workspace_id IS DISTINCT FROM b.workspace_id OR
       t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=8 THEN '6238dcd66fa6da02d29e71848086b64be9eda1867070369224287ce6eb356416' WHEN e.execution_contract_revision=7 THEN '7ece3a75f9677667e0ef3f003c71ecfe131603cbc39fe94bb2ec2603daa3495e' WHEN e.execution_contract_revision=6 THEN '6308137bf480b96ac56fef5e93de23bcd1967077c8c6952209ded979c1b4ed6e' WHEN e.execution_contract_revision=5 THEN 'ff9fe274fc8f9fe4d2a7d5f4e3c0f53b58e0fec4158a525e3aca484ab347e493' WHEN e.execution_contract_revision=4 THEN '8488d4fddf19e65a2eb41203d6b26d7b400d73516c044700246ab3a7e75adce5' WHEN e.execution_contract_revision=3 THEN 'c8c172146c570bde75077745d7f00afa116b5db8f1b78bca4aebada561066e2d' ELSE '5288a780557724d7c5be81afd28f50a0cc5e5b86a3c8e89ae241033e12f3f3fe' END) OR
       t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=8 THEN 'semantic-tasks-v1:e97805481c3507604e33f40351f681631490e087ecb3ea9174b740afd8475e02' WHEN e.execution_contract_revision=7 THEN 'semantic-tasks-v1:5b00443fe1872092d8bac8f20eab7ffd9412e53cf541471331e4aef5f7d51b0c' WHEN e.execution_contract_revision=6 THEN 'semantic-tasks-v1:4caf4a3a8fe55ab9f56a73620e9e8839660bc8097cf6f6189230338d1ca88d40' WHEN e.execution_contract_revision=5 THEN 'semantic-tasks-v1:a351bceba3c90d5e9edf2e5e3c4d98c20148fc061f635c3b23199bae4caf0e25' WHEN e.execution_contract_revision=4 THEN 'semantic-tasks-v1:0876addc48bcf62db405dff9d1a8b75eb497cbec739945d741d4db72aea4b6dd' WHEN e.execution_contract_revision=3 THEN 'semantic-tasks-v1:116ac71ae8ac94ad8abc3018a0577a60a5e5e543ddf3660ae7db467f09764aef' ELSE 'semantic-tasks-v1:7e8a296cf1143deb86715144ed06cceefcb28dcc9955892ba7df5a06ded8f71f' END) THEN
        RAISE EXCEPTION 'complete_execution_binding_conflict' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END; $$;

-- From 0051_supervised_reapproval.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.consume_supervised_dispatch(i jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE; route jsonb; ordinal bigint; prior_count bigint; c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended((i->>'tenant_id')||':semantic_outcome_authority:',0));
 SELECT * INTO q FROM memoriesql.supervised_qualification_head((i->>'tenant_id')::uuid,(i->>'semantic_task_id')::uuid);
 IF NOT FOUND THEN
  IF i ? 'supervision' OR COALESCE(i->>'dispatch_boundary','single_inference')<>'single_inference' THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
  RETURN;
 END IF;
 IF q.configuration IS DISTINCT FROM i->'supervision' OR q.origin_principal_id IS DISTINCT FROM (i->>'origin_principal_id')::uuid
 OR q.workspace_id IS DISTINCT FROM (i->>'workspace_id')::uuid OR q.access_scope_id IS DISTINCT FROM (i->>'access_scope_id')::uuid
 OR NOT memoriesql.supervised_task_open(q.tenant_id,q.task_id)
 OR NOT COALESCE((i->>'task_kind'='memory.semantic.author-complete-unit' AND (i->>'task_contract_revision')::integer IN (2,3,4,5,6,7,8))
   OR (i->>'task_kind'='memory.semantic.assess-relations' AND (i->>'task_contract_revision')::integer=1),false)
 THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'supervised_requires_read_committed'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(q.tenant_id::text||':supervised-dispatch:'||q.qualification_id::text,0));
 SELECT r.value,r.ordinality INTO route,ordinal FROM jsonb_array_elements(q.configuration->'routes') WITH ORDINALITY r
 WHERE r.value#>>'{reference,profile_key}'=i->>'model_profile_key'
 AND r.value#>'{reference,revision}'=i->'model_profile_revision';
 IF route IS NULL OR route->'target' IS DISTINCT FROM i->'target'
 OR route->>'qualification_revision' IS DISTINCT FROM i->>'qualification_revision'
 OR route->'observable_dispatch_allowance' IS DISTINCT FROM '1'::jsonb
 -- A relation task may qualify a second managed turn for its specialist.
 OR COALESCE(i->>'dispatch_boundary','single_inference') IS DISTINCT FROM (CASE WHEN ordinal=1 THEN 'managed_turn'
     WHEN i->>'task_kind'='memory.semantic.assess-relations' AND route#>>'{target,boundary_kind}'='managed_turn' THEN 'managed_turn'
     ELSE 'single_inference' END)
 OR (ordinal=1 AND route#>>'{target,boundary_kind}' IS DISTINCT FROM 'managed_turn')
 OR (ordinal=2 AND COALESCE(i->>'dispatch_boundary','single_inference')='single_inference'
     AND route#>'{target,transport_retries_disabled}' IS DISTINCT FROM 'true'::jsonb)
 THEN RAISE EXCEPTION 'supervised_route_mismatch' USING ERRCODE='42501'; END IF;
 -- Count all attempts, including uncertain/unstarted/failed intents. Never refund.
 SELECT count(*) INTO prior_count FROM memoriesql.model_provider_request_intents
 WHERE tenant_id=q.tenant_id AND supervised_qualification_id=q.qualification_id;
 IF prior_count<>ordinal-1 OR EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents WHERE request_id=(i->>'request_id')::uuid)
 THEN RAISE EXCEPTION 'supervised_dispatch_allowance_consumed' USING ERRCODE='55000'; END IF;
 IF prior_count>0 AND NOT memoriesql.supervised_acceptance_valid(q.tenant_id,q.task_id)
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_unsettled_or_stop_reached' USING ERRCODE='55000'; END IF;
 -- Intents under an approval this one superseded stay charged there; any other is outside approval.
 IF EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents r WHERE r.tenant_id=q.tenant_id AND r.task_id=q.task_id AND r.supervised_qualification_id IS DISTINCT FROM q.qualification_id
  AND NOT EXISTS(SELECT 1 FROM memoriesql.model_supervised_qualifications earlier WHERE earlier.tenant_id=r.tenant_id AND earlier.task_id=r.task_id
   AND earlier.qualification_id=r.supervised_qualification_id AND earlier.status='revoked'))
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_outside_approval' USING ERRCODE='55000'; END IF;
 -- Hard-bounded follow-up reservations must also fit the remaining reported stop allowance.
 IF ordinal=2 AND EXISTS(SELECT 1 FROM memoriesql.model_dispatch_usage_fold_v2 f WHERE f.tenant_id=q.tenant_id AND f.supervised_qualification_id=q.qualification_id
  AND (f.input_tokens+(i->>'max_input_tokens')::bigint>(q.configuration->>'reported_input_token_stop')::bigint
   OR f.output_tokens+(i->>'max_output_tokens')::bigint>(q.configuration->>'reported_generated_token_stop')::bigint
   OR (q.configuration->>'reported_cash_stop_microunits' IS NOT NULL AND f.incremental_cash_exposure_microunits+(i->>'conservative_reservation_microunits')::bigint>(q.configuration->>'reported_cash_stop_microunits')::bigint)))
 THEN RAISE EXCEPTION 'supervised_followup_exceeds_remaining_allowance' USING ERRCODE='55000'; END IF;
END; $$;

-- From 0041_authored_statement_kinds.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.guard_complete_unit_execution() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
DECLARE complete boolean; t memoriesql.semantic_tasks%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' THEN RETURN NEW; END IF;
        IF NEW.contract_revision=1 THEN
            IF TG_OP='INSERT' THEN NEW.status:='policy_paused'; NEW.pause_reason_code:='complete_input_executor_unavailable'; END IF;
            IF NEW.status NOT IN ('policy_paused','cancelled','failed_terminal') OR NEW.attempt_count<>0 OR NEW.lease_generation<>0 OR
               (NEW.status='policy_paused' AND NEW.pause_reason_code IS DISTINCT FROM 'complete_input_executor_unavailable') THEN
                RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
        ELSIF NEW.contract_revision IN (2,3,4,5,6,7,8) THEN
            IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=NEW.tenant_id AND e.execution_task_id=NEW.task_id) THEN
                RAISE EXCEPTION 'complete_input_activation_required' USING ERRCODE='55000'; END IF;
            IF NEW.status='succeeded' AND (NOT memoriesql.complete_input_exposure_valid(NEW.tenant_id,NEW.task_id,NEW.result_attempt_id,NEW.lease_generation) OR
                NOT EXISTS(SELECT 1 FROM memoriesql.bead_versions v JOIN memoriesql.bead_statement_revisions s USING(tenant_id,bead_version_id) JOIN memoriesql.semantic_task_receipts r ON r.tenant_id=v.tenant_id AND r.semantic_task_receipt_id=v.semantic_task_receipt_id JOIN memoriesql.idempotency_receipts i ON i.tenant_id=r.tenant_id AND i.idempotency_receipt_id=r.idempotency_receipt_id WHERE v.tenant_id=NEW.tenant_id AND v.bead_id=(NEW.input_payload#>>'{payload,bead_ids,0}')::uuid AND i.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5','complete_input.apply.v6','complete_input.apply.v7') AND i.resource_id=NEW.task_id)) THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        ELSE RAISE EXCEPTION 'complete_input_execution_unsupported' USING ERRCODE='55000'; END IF;
    ELSIF TG_TABLE_NAME='semantic_task_attempts' THEN
        SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
        IF t.task_kind='memory.semantic.author-complete-unit' AND (t.contract_revision NOT IN (2,3,4,5,6,7,8) OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t.tenant_id AND e.execution_task_id=t.task_id)) THEN
            RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
    ELSE
        SELECT u.materialization_version=1 INTO complete FROM memoriesql.beads b JOIN memoriesql.source_units u USING(tenant_id,source_unit_id) WHERE b.tenant_id=NEW.tenant_id AND b.bead_id=NEW.bead_id;
        IF complete THEN
            SELECT q.* INTO t FROM memoriesql.semantic_task_receipts r JOIN memoriesql.idempotency_receipts i USING(tenant_id,idempotency_receipt_id)
            JOIN memoriesql.semantic_tasks q ON q.tenant_id=i.tenant_id AND q.task_id=i.resource_id
            WHERE r.tenant_id=NEW.tenant_id AND r.semantic_task_receipt_id=NEW.semantic_task_receipt_id AND i.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5','complete_input.apply.v6','complete_input.apply.v7');
            SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=t.tenant_id AND task_id=t.task_id AND lease_generation=t.lease_generation AND status='running';
            IF t.task_id IS NULL OR t.task_kind<>'memory.semantic.author-complete-unit' OR t.contract_revision NOT IN (2,3,4,5,6,7,8) OR t.status<>'running' OR t.cancel_requested_at IS NOT NULL OR
               NEW.bead_id::text IS DISTINCT FROM t.input_payload#>>'{payload,bead_ids,0}' OR NEW.version<>1 OR
               NOT memoriesql.complete_input_exposure_valid(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation) OR
               memoriesql.reauthorize_semantic_task(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation,t.lease_owner,t.worker_instance_id,'hydrate',clock_timestamp())<>'authorized' THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END; $$;

-- From 0043_inspection_withholding.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.inspect_stored_bead_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms'
AS $$
DECLARE
 c memoriesql.authorization_contexts%ROWTYPE;
 b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE;
 u memoriesql.source_units%ROWTYPE; ev memoriesql.source_events%ROWTYPE;
 r memoriesql.semantic_task_receipts%ROWTYPE; ir memoriesql.idempotency_receipts%ROWTYPE;
 binding memoriesql.logical_unit_materializations%ROWTYPE;
 execution memoriesql.complete_input_executions%ROWTYPE;
 intent memoriesql.model_provider_request_intents%ROWTYPE;
 m memoriesql.entity_mentions%ROWTYPE; resolution memoriesql.entity_mention_resolutions%ROWTYPE;
 s memoriesql.bead_semantic_statements%ROWTYPE;
 support memoriesql.bead_semantic_statement_evidence%ROWTYPE;
 p memoriesql.evidence_packages%ROWTYPE;
 item jsonb; mentions jsonb:=NULL; statements jsonb:='[]'; evidence jsonb;
 decision jsonb; entities jsonb; meaning jsonb:=NULL; provenance jsonb:=NULL;
 result jsonb; bead_type jsonb; status text; lifecycle text:='thin';
 sources uuid[]:='{}'; source uuid; watermark bigint; amount integer;
 candidate_id uuid; candidates_authorized boolean;
 started timestamptz:=clock_timestamp();
 unavailable constant jsonb:='{"contract_version":1,"outcome":"unavailable","bead":null}';
 budget constant jsonb:='{"contract_version":1,"outcome":"budget_exhausted","bead":null}';
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object'
 OR request-ARRAY['contract_version','bead_id']<>'{}'::jsonb
 OR request->>'contract_version' IS DISTINCT FROM '1'
 OR octet_length(request::text)>1024 OR request->>'bead_id' IS NULL THEN
  RAISE EXCEPTION 'invalid_stored_bead_inspection' USING ERRCODE='22023';
 END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO b FROM memoriesql.beads WHERE tenant_id=c.tenant_id
  AND workspace_id=c.workspace_id AND bead_id=(request->>'bead_id')::uuid;
 IF b.bead_id IS NULL OR NOT memoriesql.current_context_event_authorized(
  b.access_scope_id,b.event_id,'memory.query','read') THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO ev FROM memoriesql.source_events WHERE tenant_id=b.tenant_id AND event_id=b.event_id;
 SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 sources:=array_append(sources,ev.source_object_id);
 PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
 -- Immutable accepted identity, never newest visible version.
 SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics a
 JOIN memoriesql.bead_versions bv USING(tenant_id,bead_version_id)
 WHERE a.tenant_id=b.tenant_id AND a.bead_id=b.bead_id;
 -- Owner decision 8 of 2026-10-06: inspection withholds exactly what the query
 -- population withholds. An accepted note's family, the note and every
 -- supersession neighbour of its accepted version, must pass the population's
 -- own reads at this instant; otherwise the note reads as one that does not
 -- exist.
 IF v.bead_version_id IS NOT NULL THEN
  PERFORM memoriesql.query_bead_records_v1(b.tenant_id,b.bead_id,clock_timestamp());
  FOR candidate_id IN SELECT CASE WHEN n.bead_id=b.bead_id THEN n.superseded_bead_id ELSE n.bead_id END
    FROM memoriesql.bead_supersessions n
    JOIN memoriesql.bead_versions nv ON nv.tenant_id=n.tenant_id AND nv.bead_version_id=n.bead_version_id
    WHERE n.tenant_id=b.tenant_id AND nv.authored_at<=clock_timestamp()
     AND ((n.superseded_bead_id=b.bead_id AND n.superseded_bead_version_id=v.bead_version_id)
       OR (n.bead_id=b.bead_id AND n.bead_version_id=v.bead_version_id)) LOOP
   PERFORM memoriesql.query_bead_records_v1(b.tenant_id,candidate_id,clock_timestamp());
  END LOOP;
 END IF;
 SELECT * INTO binding FROM memoriesql.logical_unit_materializations
 WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 SELECT * INTO execution FROM memoriesql.complete_input_executions
 WHERE tenant_id=b.tenant_id AND binding_task_id=binding.task_id;
 IF execution.execution_task_id IS NOT NULL THEN
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
  WHERE q.tenant_id=b.tenant_id AND q.task_id=execution.execution_task_id;
  lifecycle:=CASE WHEN status IN ('failed_terminal','dead_letter','cancelled','policy_paused','superseded')
   THEN 'failed' ELSE 'pending' END;
 END IF;
 IF v.bead_version_id IS NOT NULL THEN
  IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id,v.workspace_id,v.access_scope_id,v.bead_version_id)
  THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  SELECT * INTO r FROM memoriesql.semantic_task_receipts WHERE tenant_id=v.tenant_id AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  SELECT * INTO ir FROM memoriesql.idempotency_receipts WHERE tenant_id=r.tenant_id AND idempotency_receipt_id=r.idempotency_receipt_id;
  IF r.semantic_task_receipt_id IS NULL OR ir.status IS DISTINCT FROM 'succeeded' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  -- Accepted provenance is bound to its own receipt, never the newest source task.
  SELECT * INTO execution FROM memoriesql.complete_input_executions
   WHERE tenant_id=r.tenant_id AND execution_task_id=ir.resource_id;
  IF ir.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5','complete_input.apply.v6','complete_input.apply.v7')
   AND execution.execution_task_id IS NULL THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
   WHERE q.tenant_id=r.tenant_id AND q.task_id=execution.execution_task_id;
  -- Every accepted author can depend on optional context, including revisions 3/4
  -- without a classifier. Conservatively authorize every pinned source.
  FOR item IN SELECT value FROM jsonb_array_elements(execution.authorized_context) LOOP
   source:=(item->>'source_object_id')::uuid;
   PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
  END LOOP;
  SELECT statement_watermark INTO watermark FROM memoriesql.bead_statement_revisions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id;
  -- Owner decision 6 of 2026-10-06 (pair 4): authorize every statement under
  -- the watermark, and every source its evidence and context name, before any
  -- budget, so a note the reader cannot read never reports its size.
  IF EXISTS(SELECT 1 FROM memoriesql.bead_semantic_statements q WHERE q.tenant_id=v.tenant_id
    AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
    AND NOT memoriesql.current_context_semantic_statement_authorized(q.tenant_id,q.workspace_id,q.access_scope_id,q.statement_id))
   THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM memoriesql.bead_semantic_statements q, unnest(q.context_source_ids) cs(unit_id)
    WHERE q.tenant_id=v.tenant_id AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
     AND NOT EXISTS(SELECT 1 FROM memoriesql.source_units cu WHERE cu.tenant_id=q.tenant_id AND cu.source_unit_id=cs.unit_id))
   THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  FOR source IN SELECT DISTINCT o.source_object_id FROM memoriesql.bead_semantic_statements q
    JOIN LATERAL (
     SELECT e.source_object_id FROM memoriesql.bead_semantic_statement_evidence x
      JOIN memoriesql.source_events e ON e.tenant_id=x.tenant_id AND e.event_id=x.evidence_event_id
      WHERE x.tenant_id=q.tenant_id AND x.statement_id=q.statement_id
     UNION ALL
     SELECT e.source_object_id FROM unnest(q.context_source_ids) cs(unit_id)
      JOIN memoriesql.source_units cu ON cu.tenant_id=q.tenant_id AND cu.source_unit_id=cs.unit_id
      JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id) o ON true
    WHERE q.tenant_id=v.tenant_id AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
    ORDER BY 1 LOOP
   PERFORM memoriesql.revisiting_source_authorize(source);
  END LOOP;
  -- Owner decision 6 of 2026-10-06 (pair 4): the classification contribution's
  -- checks, moved unchanged from after the mentions, deny before any budget.
  IF v.classification_contribution IS NOT NULL THEN
   IF r.task_contract_version<>5 OR r.task_contract_key<>'memory.semantic.author-complete-unit'
    OR ir.operation_kind<>'complete_input.apply.v4' OR ir.resource_id IS DISTINCT FROM execution.execution_task_id THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   SELECT * INTO intent FROM memoriesql.model_provider_request_intents WHERE tenant_id=v.tenant_id
    AND request_id=(v.classification_contribution->>'request_id')::uuid AND task_id=ir.resource_id;
   IF intent.request_id IS NULL OR intent.workspace_id<>c.workspace_id OR
    NOT memoriesql.current_context_scope_authorized(intent.access_scope_id,'memory.query','read') THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(v.classification_contribution->'evidence') LOOP
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=v.tenant_id AND package_id=(item#>>'{selection,package_id}')::uuid;
    IF p.package_id IS NULL OR p.inventory_hash IS DISTINCT FROM item#>>'{selection,inventory_sha256}' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    PERFORM memoriesql.revisiting_source_authorize(p.source_object_id); sources:=array_append(sources,p.source_object_id);
   END LOOP;
   provenance:=jsonb_build_object('request_id',intent.request_id,'task_id',intent.task_id,'attempt_id',intent.attempt_id,
    'model_id',intent.model_id,'model_profile_key',intent.model_profile_key,'model_profile_revision',intent.model_profile_revision,
    'authorization_policy_revision',intent.authorization_policy_revision,'quality_policy_revision_id',intent.quality_policy_revision_id,'pricing_revision_id',intent.pricing_revision_id);
  ELSIF r.task_contract_version=5 AND r.task_contract_key='memory.semantic.author-complete-unit' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
   WHERE tenant_id=v.tenant_id AND bead_id=v.bead_id AND statement_sequence<=watermark LIMIT 33) bounded;
  IF amount>32 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
  FOR s IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=v.tenant_id
   AND bead_id=v.bead_id AND statement_sequence<=watermark ORDER BY statement_sequence LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(s.tenant_id,s.workspace_id,s.access_scope_id,s.statement_id)
    THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   -- Context is provenance, not support; still requires authorization before disclosure.
   FOREACH source IN ARRAY s.context_source_ids LOOP
    -- A context source is a unit of the statement's own evidence. Authorize
    -- its event's source object, as evidence is; missing or unreadable reads
    -- exactly as an unknown note.
    SELECT e.source_object_id INTO source FROM memoriesql.source_units cu
     JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id
     WHERE cu.tenant_id=s.tenant_id AND cu.source_unit_id=source;
    IF NOT FOUND THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
   END LOOP;
   evidence:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id LIMIT 65) bounded;
   IF amount>64 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
   FOR support IN SELECT * FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id ORDER BY evidence_source_unit_id LOOP
    SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id=s.tenant_id AND event_id=support.evidence_event_id;
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
    evidence:=evidence||jsonb_build_array(jsonb_build_object('event_id',support.evidence_event_id,
     'source_unit_id',support.evidence_source_unit_id,'content_sha256',support.evidence_content_hash));
   END LOOP;
   statements:=statements||jsonb_build_array(jsonb_build_object('statement_id',s.statement_id,
    'sequence',s.statement_sequence,'kind',s.statement_kind,'text',s.statement_text,
    'supersedes_statement_id',s.supersedes_statement_id,'correction_reason',s.correction_reason,'evidence',evidence));
  END LOOP;
  -- Capability derives ONLY from this accepted result's own successful apply receipt.
  IF r.task_contract_key='memory.semantic.author-complete-unit' AND
    ((r.task_contract_version=4 AND ir.operation_kind='complete_input.apply.v3') OR
     (r.task_contract_version=5 AND ir.operation_kind='complete_input.apply.v4') OR
     (r.task_contract_version=6 AND ir.operation_kind='complete_input.apply.v5') OR
     (r.task_contract_version=7 AND ir.operation_kind='complete_input.apply.v6')) THEN
   mentions:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id LIMIT 33) bounded;
   IF amount>32 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
   FOR m IN SELECT * FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id ORDER BY entity_mention_id LOOP
    SELECT * INTO resolution FROM memoriesql.entity_mention_resolutions WHERE tenant_id=m.tenant_id AND entity_mention_id=m.entity_mention_id
     ORDER BY decision_sequence DESC LIMIT 1;
    -- Both absent and protected global decisions are unavailable. Local authored identity is separate.
    decision:=jsonb_build_object('availability','unavailable','resolution_id',NULL,'status',NULL,'entity_ids',NULL);
    IF resolution.entity_mention_resolution_id IS NOT NULL AND
      memoriesql.current_context_bead_version_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,resolution.evidence_bead_version_id)
      AND (resolution.resolved_entity_id IS NULL OR memoriesql.current_context_entity_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,resolution.resolved_entity_id)) THEN
     -- Materialize at most 65 candidates once. Never invoke the unbounded aggregate
     -- predicate or re-query the full set after the bound (including under insert races).
     SELECT COALESCE(jsonb_agg(candidate_entity_id ORDER BY candidate_ordinal),'[]') INTO entities
      FROM (SELECT candidate_entity_id,candidate_ordinal FROM memoriesql.entity_resolution_candidates
       WHERE tenant_id=m.tenant_id AND entity_mention_resolution_id=resolution.entity_mention_resolution_id
       ORDER BY candidate_ordinal LIMIT 65) bounded;
     amount:=jsonb_array_length(entities);
     -- An oversized decision stays indistinguishable from a protected/absent decision.
     IF amount<=64 THEN
      candidates_authorized:=resolution.resolution_status<>'ambiguous' OR amount>=2;
      FOR candidate_id IN SELECT value::uuid FROM jsonb_array_elements_text(entities) LOOP
       IF NOT memoriesql.current_context_entity_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,candidate_id) THEN
        candidates_authorized:=false; EXIT;
       END IF;
      END LOOP;
      IF candidates_authorized THEN
       IF resolution.resolved_entity_id IS NOT NULL THEN entities:=jsonb_build_array(resolution.resolved_entity_id); END IF;
       decision:=jsonb_build_object('availability','available','resolution_id',resolution.entity_mention_resolution_id,'status',resolution.resolution_status,'entity_ids',entities);
      END IF;
     END IF;
    END IF;
    mentions:=mentions||jsonb_build_array(jsonb_build_object('entity_mention_id',m.entity_mention_id,'surface_text',m.surface_text,
     'local_identity_state',m.local_identity_state,'local_identity_reason',m.local_identity_reason,'resolution',decision));
   END LOOP;
  END IF;
  SELECT jsonb_build_object('key',t.stable_key,'revision',tr.revision,'definition',tr.definition) INTO bead_type
   FROM memoriesql.bead_types t JOIN memoriesql.bead_type_revisions tr USING(bead_type_id) WHERE tr.bead_type_revision_id=v.bead_type_revision_id;
  meaning:=jsonb_build_object('bead_version_id',v.bead_version_id,'receipt_id',r.semantic_task_receipt_id,
   'task_contract_key',r.task_contract_key,'task_contract_version',r.task_contract_version,'bead_type',bead_type,
   'render_contract_revision',v.render_contract_revision,'render',CASE WHEN v.render_contract_revision=2 THEN v.render_payload END,'statements',statements,
   'mentions',mentions,'classification',v.classification_contribution,'classification_vocabulary',CASE WHEN v.classification_contribution IS NOT NULL THEN execution.classification_vocabulary END,'classification_provenance',provenance);
  lifecycle:='accepted';
 END IF;
 result:=jsonb_build_object('contract_version',1,'outcome','available','bead',jsonb_build_object(
  'bead_id',b.bead_id,'event_id',b.event_id,'source_object_id',ev.source_object_id,'source_unit_id',b.source_unit_id,
  'parent_source_unit_id',u.parent_unit_id,'parent_resolution',COALESCE(u.structure->>'parent_resolution','unknown'),
  'package',binding.package_pin,'binding_task_id',binding.task_id,'execution_task_id',execution.execution_task_id,
  'execution_status',status,'lifecycle',lifecycle,'meaning',meaning));
 -- Revalidate all disclosed dependencies at the return boundary; audit writes only.
 FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
  PERFORM memoriesql.revisiting_source_authorize(source);
 END LOOP;
 -- Owner decision 6 of 2026-10-06 (pair 4): budgets only after every
 -- authorization.
 IF octet_length(memoriesql.canonical_semantic_json_text(result))>262144 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
 IF clock_timestamp()-started>interval '2 seconds' THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
 RETURN result;
EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;
 WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;

-- From 0041_authored_statement_kinds.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.reauthorize_semantic_task(
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
       OR requested_phase IS NULL OR requested_at IS NULL
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
    IF task_record.task_kind='memory.semantic.author-complete-unit' AND task_record.contract_revision IN (2,3,4,5,6,7,8) THEN
        BEGIN
            PERFORM memoriesql.complete_input_authorize(requested_tenant_id,
                (task_record.input_payload#>>'{payload,binding_task_id}')::uuid,
                (task_record.input_payload#>>'{payload,dispatch_policy_id}')::uuid);
        EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                  WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence';
        END;
        IF task_record.contract_revision IN (3,4,5,6,7) THEN
            BEGIN PERFORM memoriesql.source_revisiting_authorize(requested_tenant_id,requested_task_id);
            EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                      WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence'; END;
        END IF;
        -- Pinned relation candidates stay under the worker's current maintain authority.
        IF task_record.contract_revision=6 THEN
            BEGIN PERFORM memoriesql.relation_candidates_authorize(requested_tenant_id,requested_task_id);
            EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused'; END;
        END IF;
        -- Recheck the attempt after the authority fence wait, including expiry.
        IF NOT EXISTS(SELECT 1 FROM memoriesql.semantic_tasks q JOIN memoriesql.semantic_task_attempts a USING(tenant_id,task_id)
           WHERE q.tenant_id=requested_tenant_id AND q.task_id=requested_task_id AND a.attempt_id=requested_attempt_id
           AND q.lease_generation=requested_lease_generation AND a.lease_generation=requested_lease_generation
           AND q.status='running' AND a.status IN ('claimed','running') AND q.lease_expires_at>clock_timestamp()
           AND a.deadline_at>clock_timestamp() AND (requested_phase='outcome' OR q.cancel_requested_at IS NULL)) THEN RETURN 'stale_fence'; END IF;
    END IF;
    -- A relation task stays under its origin's and the worker's authority over every
    -- pinned bead, statement and evidence source, and its attestor policy stays active.
    IF task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision=1 THEN
        BEGIN PERFORM memoriesql.relation_assessment_pins_authorize(requested_tenant_id,requested_task_id);
        EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                  WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence'; END;
        IF NOT EXISTS(SELECT 1 FROM memoriesql.semantic_tasks q JOIN memoriesql.semantic_task_attempts a USING(tenant_id,task_id)
           WHERE q.tenant_id=requested_tenant_id AND q.task_id=requested_task_id AND a.attempt_id=requested_attempt_id
           AND q.lease_generation=requested_lease_generation AND a.lease_generation=requested_lease_generation
           AND q.status='running' AND a.status IN ('claimed','running') AND q.lease_expires_at>clock_timestamp()
           AND a.deadline_at>clock_timestamp() AND (requested_phase='outcome' OR q.cancel_requested_at IS NULL)) THEN RETURN 'stale_fence'; END IF;
    END IF;
    RETURN 'authorized';
END;
$$;

-- From 0020_author_controlled_source_revisiting.sql: revision 8 handled exactly like revision 2.
CREATE OR REPLACE FUNCTION memoriesql.record_complete_input_exposure_v1(request_id uuid, payload_hash text, frame jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; e memoriesql.complete_input_executions%ROWTYPE;
    b memoriesql.logical_unit_materializations%ROWTYPE; i memoriesql.model_provider_request_intents%ROWTYPE;
    t memoriesql.semantic_tasks%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE;
    part memoriesql.evidence_package_parts%ROWTYPE; item jsonb; lo integer; hi integer; prior_end integer; fh text;
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO i FROM memoriesql.model_provider_request_intents WHERE tenant_id=c.tenant_id AND model_provider_request_intents.request_id=record_complete_input_exposure_v1.request_id;
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND execution_task_id=i.task_id;
    b:=memoriesql.complete_input_authorize(c.tenant_id,e.binding_task_id,e.dispatch_policy_id);
    IF e.execution_contract_revision NOT IN (2,8) OR c.principal_kind IS DISTINCT FROM 'service' OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
       WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=e.dispatch_policy_id AND d.attestor_principal_id=c.principal_id AND d.status='active' AND d.expires_at>clock_timestamp()) OR
       i.request_payload_hash IS DISTINCT FROM payload_hash OR i.task_id::text IS DISTINCT FROM frame->>'task_id' OR i.attempt_id::text IS DISTINCT FROM frame->>'attempt_id' OR
       b.package_pin IS DISTINCT FROM frame->'package' OR frame->>'contract_version' IS DISTINCT FROM '1' OR
       jsonb_typeof(frame->'slices') IS DISTINCT FROM 'array' OR jsonb_array_length(frame->'slices') NOT BETWEEN 1 AND 256 OR
       octet_length(frame::text)>262144 OR (SELECT sum(char_length(value->>'content')) FROM jsonb_array_elements(frame->'slices'))>16384 OR
       NOT EXISTS(SELECT 1 FROM memoriesql.model_usage_events u WHERE u.tenant_id=c.tenant_id AND u.request_id=i.request_id AND u.outcome='succeeded') THEN
        RAISE EXCEPTION 'trusted_exposure_required' USING ERRCODE='42501'; END IF;
    -- Task-row fence serializes with cancellation and reaping; authority is
    -- checked again after the wait. The old attempt cannot attest a new attempt.
    SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=i.task_id FOR UPDATE;
    SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id;
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,e.binding_task_id,e.dispatch_policy_id);
    IF c.principal_id=a.claimant_principal_id THEN RAISE EXCEPTION 'separate_dispatch_attestor_required' USING ERRCODE='42501'; END IF;
    IF t.status<>'running' OR a.status<>'running' OR t.lease_generation<>a.lease_generation OR t.cancel_requested_at IS NOT NULL OR
       t.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN RAISE EXCEPTION 'stale_exposure_attempt' USING ERRCODE='40001'; END IF;
    fh:=encode(sha256(convert_to(frame::text,'UTF8')),'hex');
    IF EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts r WHERE r.tenant_id=c.tenant_id AND r.request_id=i.request_id) THEN
        IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts r WHERE r.tenant_id=c.tenant_id AND r.request_id=i.request_id AND r.frame_hash=fh) THEN
            RAISE EXCEPTION 'exposure_replay_conflict' USING ERRCODE='23505'; END IF;
        RETURN true;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(frame->'slices') LOOP
        SELECT * INTO part FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=b.package_id AND part_id=(item#>>'{inventory,part_id}')::uuid;
        lo:=(item->>'start_character')::integer; hi:=lo+char_length(item->>'content');
        IF part.part_id IS NULL OR part.inventory IS DISTINCT FROM item->'inventory' OR lo IS NULL OR lo<0 OR hi<=lo OR hi>(part.inventory->>'characters')::integer OR
           substring(part.content FROM lo+1 FOR hi-lo) IS DISTINCT FROM item->>'content' THEN RAISE EXCEPTION 'exposure_evidence_mismatch' USING ERRCODE='22023'; END IF;
        SELECT COALESCE(max(end_character),0) INTO prior_end FROM memoriesql.complete_input_exposures WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id AND part_id=part.part_id;
        IF prior_end<>lo THEN RAISE EXCEPTION 'exposure_omitted_or_replayed_interval' USING ERRCODE='22023'; END IF;
        IF EXISTS(SELECT 1 FROM memoriesql.evidence_package_parts earlier WHERE earlier.tenant_id=c.tenant_id AND earlier.package_id=b.package_id AND earlier.ordinal=part.ordinal-1 AND
            NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=c.tenant_id AND x.attempt_id=i.attempt_id AND x.part_id=earlier.part_id AND x.end_character=(earlier.inventory->>'characters')::integer)) THEN
            RAISE EXCEPTION 'exposure_inventory_order_mismatch' USING ERRCODE='22023'; END IF;
        INSERT INTO memoriesql.complete_input_exposures VALUES(c.tenant_id,i.task_id,i.attempt_id,a.lease_generation,i.request_id,b.package_id,b.package_pin->>'inventory_sha256',part.part_id,lo,hi,encode(sha256(convert_to(item->>'content','UTF8')),'hex'),c.principal_id,e.dispatch_policy_id,clock_timestamp());
    END LOOP;
    INSERT INTO memoriesql.complete_input_dispatch_receipts VALUES(c.tenant_id,i.request_id,fh);
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,e.binding_task_id,e.dispatch_policy_id);
    RETURN true;
END; $$;

-- From 0020_author_controlled_source_revisiting.sql activate_complete_input_v1: revision 8's activation.
CREATE FUNCTION memoriesql.activate_complete_input_v7(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE; e memoriesql.complete_input_executions%ROWTYPE;
    original memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    rid uuid:=uuidv7(); tid uuid:=uuidv7(); eid uuid; input jsonb; result jsonb; request_hash text; started timestamptz:=clock_timestamp();
BEGIN
    IF request->>'contract_version' IS DISTINCT FROM '7' OR request->>'expected_schema_version' IS DISTINCT FROM '53' OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','binding_task_id','dispatch_policy_id']<>'{}'::jsonb OR
       COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR octet_length(request::text)>16384 THEN
        RAISE EXCEPTION 'invalid_complete_input_activation' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    b:=memoriesql.complete_input_authorize(c.tenant_id,(request->>'binding_task_id')::uuid,(request->>'dispatch_policy_id')::uuid);
    SELECT * INTO original FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=b.task_id;
    IF original.origin_principal_id<>c.principal_id OR NOT memoriesql.current_context_semantic_task_authorized(b.task_id,'memory.maintain','write') THEN
        RAISE EXCEPTION 'complete_input_activation_denied' USING ERRCODE='42501'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':complete-input-activate:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':complete-input-binding:'||b.task_id::text,0));
    -- Lock order: authority fence, operation key, binding key, original task.
    -- Cancellation may win any preceding wait; never transfer from stale state.
    SELECT * INTO original FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=b.task_id FOR UPDATE;
    b:=memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
    -- Revision 8 runs only under a dispatch policy provisioned for it.
    IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=8) THEN
        RAISE EXCEPTION 'complete_input_activation_denied' USING ERRCODE='42501'; END IF;
    request_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    SELECT * INTO old FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='complete_input.activate.v7' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash<>request_hash THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old.response_receipt||'{"replayed":true}'::jsonb;
    END IF;
    -- Every acknowledged key owns a shared-ledger receipt, including natural replay.
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,b.access_scope_id,rid,'complete_input.activate.v7',request->>'idempotency_key',request_hash,'in_progress','semantic_task',b.task_id,1,started,started);
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND binding_task_id=b.task_id;
    IF FOUND THEN
        IF e.execution_contract_revision<>8 OR e.dispatch_policy_id<>(request->>'dispatch_policy_id')::uuid THEN RAISE EXCEPTION 'complete_input_activation_conflict' USING ERRCODE='23505'; END IF;
        SELECT response_receipt INTO result FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=e.activation_receipt_id;
        result:=result||jsonb_build_object('idempotency_receipt_id',rid,'replayed',true);
        PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
        UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
        RETURN result;
    END IF;
    IF original.status<>'policy_paused' OR original.pause_reason_code<>'complete_input_executor_unavailable' OR original.attempt_count<>0 OR original.cancel_requested_at IS NOT NULL THEN
        RAISE EXCEPTION 'complete_input_binding_not_transferable' USING ERRCODE='55000'; END IF;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id;
    INSERT INTO memoriesql.complete_input_executions(tenant_id,binding_task_id,execution_task_id,dispatch_policy_id,source_schema_version,activation_receipt_id,execution_contract_revision) VALUES(c.tenant_id,b.task_id,tid,(request->>'dispatch_policy_id')::uuid,(SELECT schema_version FROM memoriesql.source_objects WHERE tenant_id=c.tenant_id AND source_object_id=p.source_object_id),rid,8);
    IF memoriesql.cancel_semantic_task(c.tenant_id,b.task_id,'complete_input.execution_transferred',clock_timestamp())<>'cancelled' THEN
        RAISE EXCEPTION 'complete_input_transfer_failed' USING ERRCODE='55000'; END IF;
    input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',8,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||tid::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',request->>'dispatch_policy_id','declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_complete_input_exposure_v1'));
    SELECT q.idempotency_receipt_id INTO eid FROM memoriesql.enqueue_semantic_task(tid,'complete-input.v7:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',8,'6238dcd66fa6da02d29e71848086b64be9eda1867070369224287ce6eb356416','semantic-tasks-v1:e97805481c3507604e33f40351f681631490e087ecb3ea9174b740afd8475e02',b.source_unit_id::text,0,input,input#>>'{evidence_manifest,manifest_id}',b.access_scope_id,started,b.task_id,started) q;
    result:=jsonb_build_object('contract_version',7,'binding_task_id',b.task_id,'execution_task_id',tid,'idempotency_receipt_id',rid,'enqueue_receipt_id',eid,'package',b.package_pin,'replayed',false);
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v7(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.activate_complete_input_v7(jsonb) TO memoriesql_application;
