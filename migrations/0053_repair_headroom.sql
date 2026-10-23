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
--
-- Part 2, memory.semantic.assess-relations revision 2. Revision 1 spends its
-- two requests on the author and the batched specialist, so an author output
-- that misses a pair's disposition has no request left for a repair round.
-- Revision 2 is revision 1 with request_limit 3, for one evidence-free author
-- repair frame (custody ruling RULING-P1F-D2-RELATION-REPAIR-AUTHOR-FRAME,
-- R-1 to R-7). Packets, output contract, deliveries, apply and the run tree
-- (author root plus one specialist run) are revision 1's; its author agent is
-- memory.semantic.relation-author.v2. This migration admits it beside
-- revision 1:
-- - an admission policy pins the revision-2 task contract and registry;
-- - activate_relation_assessment_v2 activates revision 2, as v1 does revision 1;
-- - apply_relation_assessment_v1 accepts command version 2 (schema 53) for a
--   revision-2 task and its author agent, and nothing else changed;
-- - record_relation_delivery_v1, record_semantic_run_event,
--   reauthorize_semantic_task, consume_supervised_dispatch and
--   semantic_task_input_reference_safe handle revision 2 exactly like
--   revision 1. semantic_task_input_reference_safe also admits
--   author-complete-unit revision 8's input exactly like revision 2's.
-- Revision-1 tasks keep their pins and their two requests; nothing moves them.
--
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
INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES ('semantic-tasks-v1:acb8198184ab7920a2ad6fce484a14d4ad03bd4eea6418b7fd62b16a2c5bab34','memory.semantic.assess-relations',2,'memoriesql.kernel','d79977ebc0c64cd0448857b46020fe7a79659ba55c32de6c038041a6d1bfcc2d','canonical_semantics','memory.maintain','capture',50,1,NULL,NULL);

-- From 0052_partial_observation_acceptance.sql: the new revisions handled exactly like the ones they extend.
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

-- From 0041_authored_statement_kinds.sql: the new revisions handled exactly like the ones they extend.
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

-- From 0051_supervised_reapproval.sql: the new revisions handled exactly like the ones they extend.
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
   OR (i->>'task_kind'='memory.semantic.assess-relations' AND (i->>'task_contract_revision')::integer IN (1,2)),false)
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

-- From 0041_authored_statement_kinds.sql: the new revisions handled exactly like the ones they extend.
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

-- From 0043_inspection_withholding.sql: the new revisions handled exactly like the ones they extend.
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

-- From 0041_authored_statement_kinds.sql: the new revisions handled exactly like the ones they extend.
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
    IF task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision IN (1,2) THEN
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

-- From 0020_author_controlled_source_revisiting.sql: the new revisions handled exactly like the ones they extend.
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

-- From 0041_authored_statement_kinds.sql: the new revisions handled exactly like the ones they extend.
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
    -- A relation task carries pinned bead packets (<=131,072 canonical bytes), vocabulary
    -- (<=32,768) and an optional recorded disagreement; evidence content is never inlined.
    IF candidate->>'task_kind'='memory.semantic.assess-relations' THEN
        RETURN candidate->>'contract_revision' IN ('1','2') AND octet_length(candidate::text)<=262144
           AND candidate#>>'{payload,required_execution}'='trusted_relation_assessment_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb
           AND jsonb_typeof(candidate#>'{evidence_manifest,references}')='array'
           AND jsonb_array_length(candidate#>'{evidence_manifest,references}') BETWEEN 1 AND 72
           AND jsonb_typeof(candidate#>'{payload,beads}')='array'
           AND jsonb_array_length(candidate#>'{payload,beads}') BETWEEN 1 AND 9
           AND jsonb_typeof(candidate#>'{payload,relation_vocabulary}')='array'
           AND jsonb_array_length(candidate#>'{payload,relation_vocabulary}') BETWEEN 1 AND 32;
    END IF;
    -- Revision 6 carries pinned candidate packets (<=65,536 canonical bytes) and
    -- vocabulary (<=32,768) beside the unchanged revisiting envelope.
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision'='6' THEN
        RETURN octet_length(candidate::text)<=196608 AND candidate#>>'{payload,required_execution}'='trusted_source_revisiting_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb AND jsonb_array_length(candidate#>'{evidence_manifest,references}')=1
           AND jsonb_array_length(candidate#>'{payload,source_unit_ids}')=1 AND jsonb_array_length(candidate#>'{payload,bead_ids}')=1
           AND jsonb_typeof(candidate#>'{payload,relation_candidates}')='array' AND jsonb_array_length(candidate#>'{payload,relation_candidates}')<=8
           AND jsonb_typeof(candidate#>'{payload,relation_vocabulary}')='array'
           AND jsonb_array_length(candidate#>'{payload,relation_vocabulary}') BETWEEN 1 AND 32;
    END IF;
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision' IN ('3','4','5','7') THEN
        RETURN octet_length(candidate::text)<=32768 AND candidate#>>'{payload,required_execution}'='trusted_source_revisiting_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb AND jsonb_array_length(candidate#>'{evidence_manifest,references}')=1
           AND jsonb_array_length(candidate#>'{payload,source_unit_ids}')=1 AND jsonb_array_length(candidate#>'{payload,bead_ids}')=1;
    END IF;
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision' IN ('2','8') THEN
        RETURN octet_length(candidate::text)<=32768 AND candidate#>>'{payload,required_execution}'='trusted_complete_input_exposure_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb AND jsonb_array_length(candidate#>'{evidence_manifest,references}')=1
           AND jsonb_array_length(candidate#>'{payload,source_unit_ids}')=1 AND jsonb_array_length(candidate#>'{payload,bead_ids}')=1;
    END IF;
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' THEN RETURN maximum_bytes>0 AND octet_length(candidate::text)<=maximum_bytes AND memoriesql.complete_unit_input_valid(candidate); END IF;
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

    IF candidate ->> 'contract_revision' = '2'
       AND candidate ->> 'task_kind' IN ('memory.semantic.author-observations', 'memory.semantic.correct-observation')
       AND (jsonb_array_length(evidence_references) NOT BETWEEN 1 AND 8
            OR (SELECT COALESCE(sum((value ->> 'declared_characters')::numeric), 0)
                FROM jsonb_array_elements(evidence_references)) > 4096) THEN
        RETURN false;
    END IF;
    payload := candidate -> 'payload';
    IF candidate ->> 'task_kind' = 'memory.semantic.author-observations'
       AND candidate ->> 'contract_revision' IN ('1', '2') THEN
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
    IF candidate ->> 'task_kind' = 'memory.semantic.correct-observation'
       AND candidate ->> 'contract_revision' = '2' THEN
        IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 4
           OR NOT memoriesql.semantic_task_input_reference_safe(
                jsonb_set(jsonb_set(candidate, '{task_kind}',
                    '"memory.semantic.author-observations"'::jsonb),
                    '{payload}', payload - 'supersedes'), maximum_bytes)
           OR jsonb_array_length(payload -> 'bead_ids') <> 1
           OR jsonb_typeof(payload -> 'supersedes') IS DISTINCT FROM 'array'
           OR jsonb_array_length(payload -> 'supersedes') NOT BETWEEN 1 AND 8 THEN
            RETURN false;
        END IF;
        FOR reference_item IN SELECT value FROM jsonb_array_elements(payload -> 'supersedes')
        LOOP
            IF jsonb_typeof(reference_item) IS DISTINCT FROM 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(reference_item)) <> 2
               OR COALESCE(reference_item ->> 'bead_id', '') !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR COALESCE(reference_item ->> 'bead_version_id', '') !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR reference_item ->> 'bead_id' = payload #>> '{bead_ids,0}' THEN
                RETURN false;
            END IF;
        END LOOP;
        RETURN (SELECT count(*) = count(DISTINCT value ->> 'bead_id')
                FROM jsonb_array_elements(payload -> 'supersedes'));
    END IF;
    RETURN memoriesql.semantic_queue_payload_reference_scalar_safe(payload);
END;
$$;

-- From 0030_assessed_relation_lifecycle.sql: the new revisions handled exactly like the ones they extend.
CREATE OR REPLACE FUNCTION memoriesql.apply_relation_assessment_v1(
    requested_command jsonb, requested_worker_id text, requested_worker_instance_id text,
    requested_at timestamp with time zone
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    task_record memoriesql.semantic_tasks%ROWTYPE;
    attempt_record memoriesql.semantic_task_attempts%ROWTYPE;
    receipt_record memoriesql.idempotency_receipts%ROWTYPE;
    x memoriesql.relation_assessments%ROWTYPE;
    rel6 memoriesql.bead_relations%ROWTYPE; prior memoriesql.assessed_relations%ROWTYPE;
    body jsonb := requested_command->'payload';
    command_tenant_id uuid; command_workspace_id uuid; command_access_scope_id uuid;
    command_task_id uuid; command_attempt_id uuid; command_generation bigint;
    command_model_runs text[]; computed_request_hash text; new_receipt_id uuid; root_run text;
    outcome_status text; response jsonb; item jsonb; p jsonb; j jsonb; contributor jsonb;
    type_revision uuid; type_symmetric boolean; agreed boolean; forbidden uuid; named uuid[]; lock_id uuid;
    pinned_ids text[]; fresh jsonb := '[]'; relation_ids uuid[] := ARRAY[]::uuid[];
    accepted_ids uuid[] := ARRAY[]::uuid[]; statement uuid; unit uuid;
    uuid_pattern constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    database_now timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR octet_length(requested_command::text) > 1048576
       OR requested_command - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'tenant_id',
            'workspace_id', 'access_scope_id', 'task_id', 'attempt_id', 'lease_generation', 'task_kind',
            'contract_revision', 'output_contract_hash', 'semantic_result_hash', 'semantic_payload_canonical_json',
            'used_evidence_refs', 'model_run_refs', 'payload'] <> '{}'::jsonb
       OR NOT ((requested_command->>'contract_version' IS NOT DISTINCT FROM '1'
                AND requested_command->>'expected_schema_version' IS NOT DISTINCT FROM '29'
                AND requested_command->>'contract_revision' IS NOT DISTINCT FROM '1')
            OR (requested_command->>'contract_version' IS NOT DISTINCT FROM '2'
                AND requested_command->>'expected_schema_version' IS NOT DISTINCT FROM '53'
                AND requested_command->>'contract_revision' IS NOT DISTINCT FROM '2'))
       OR requested_command->>'task_kind' IS DISTINCT FROM 'memory.semantic.assess-relations'
       OR requested_command->>'output_contract_hash' IS DISTINCT FROM '7b069836b3c71657aa5ade422e3310b4a5d203d15ebfb32bbdee2054889647b5'
       OR COALESCE(requested_command->>'semantic_result_hash', '') !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(requested_command->'semantic_payload_canonical_json') IS DISTINCT FROM 'string'
       OR octet_length(requested_command->>'semantic_payload_canonical_json') NOT BETWEEN 2 AND 131072
       OR requested_command->>'semantic_payload_canonical_json' IS DISTINCT FROM memoriesql.canonical_semantic_json_text(body)
       OR encode(sha256(convert_to(requested_command->>'semantic_payload_canonical_json', 'UTF8')), 'hex')
            IS DISTINCT FROM requested_command->>'semantic_result_hash'
       OR jsonb_typeof(body) IS DISTINCT FROM 'object'
       OR body - ARRAY['proposals', 'dispositions', 'specialist_contributions'] <> '{}'::jsonb
       OR jsonb_typeof(body->'proposals') IS DISTINCT FROM 'array' OR jsonb_array_length(body->'proposals') > 16
       OR jsonb_typeof(body->'dispositions') IS DISTINCT FROM 'array'
       OR jsonb_array_length(body->'dispositions') NOT BETWEEN 1 AND 45
       OR jsonb_typeof(body->'specialist_contributions') IS DISTINCT FROM 'array'
       OR jsonb_array_length(body->'specialist_contributions') > 16
       OR jsonb_typeof(requested_command->'used_evidence_refs') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command->'used_evidence_refs') NOT BETWEEN 1 AND 72
       OR jsonb_typeof(requested_command->'model_run_refs') IS DISTINCT FROM 'array'
       OR jsonb_array_length(requested_command->'model_run_refs') NOT BETWEEN 1 AND 17
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(requested_command->'model_run_refs') AS r(v)
                  WHERE jsonb_typeof(r.v) IS DISTINCT FROM 'string')
       OR EXISTS (SELECT 1 FROM unnest(ARRAY['tenant_id', 'workspace_id', 'access_scope_id', 'task_id', 'attempt_id']) AS k(key)
                  WHERE COALESCE(requested_command->>k.key, '') !~ uuid_pattern)
       OR COALESCE(requested_command->>'lease_generation', '') !~ '^[1-9][0-9]{0,17}$'
       OR btrim(COALESCE(requested_command->>'idempotency_key', '')) = ''
       OR length(requested_command->>'idempotency_key') > 512
       OR btrim(requested_worker_id) = '' OR btrim(requested_worker_instance_id) = ''
       OR requested_at IS NULL OR requested_at < database_now - interval '5 minutes'
       OR requested_at > database_now + interval '5 minutes' THEN
        RAISE EXCEPTION 'relation assessment command is invalid' USING ERRCODE = '22023';
    END IF;
    command_tenant_id := (requested_command->>'tenant_id')::uuid;
    command_workspace_id := (requested_command->>'workspace_id')::uuid;
    command_access_scope_id := (requested_command->>'access_scope_id')::uuid;
    command_task_id := (requested_command->>'task_id')::uuid;
    command_attempt_id := (requested_command->>'attempt_id')::uuid;
    command_generation := (requested_command->>'lease_generation')::bigint;
    command_model_runs := ARRAY(SELECT r.v FROM jsonb_array_elements_text(requested_command->'model_run_refs') AS r(v));
    computed_request_hash := encode(sha256(convert_to(requested_command::text, 'UTF8')), 'hex');

    SELECT * INTO context_record FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR context_record.principal_kind <> 'service'
       OR context_record.tenant_id <> command_tenant_id OR context_record.workspace_id <> command_workspace_id
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(command_access_scope_id, 'memory.maintain', 'write') THEN
        RAISE EXCEPTION 'relation assessment command is outside authorization' USING ERRCODE = '42501';
    END IF;
    SELECT q.* INTO task_record FROM memoriesql.semantic_tasks AS q
    JOIN memoriesql.semantic_task_attempts AS a
      ON a.tenant_id = q.tenant_id AND a.task_id = q.task_id AND a.attempt_id = command_attempt_id
     AND a.lease_generation = command_generation AND a.claimant_principal_id = context_record.principal_id
     AND a.worker_id = requested_worker_id AND a.worker_instance_id = requested_worker_instance_id
    WHERE q.tenant_id = command_tenant_id AND q.workspace_id = command_workspace_id
      AND q.access_scope_id = command_access_scope_id AND q.task_id = command_task_id
      AND q.target_kind = 'canonical_semantics' AND q.task_kind = 'memory.semantic.assess-relations'
      AND q.contract_revision = (requested_command->>'contract_revision')::integer;
    IF NOT FOUND
       OR NOT memoriesql.current_context_semantic_task_authorized(command_task_id, 'memory.maintain', 'write')
       OR NOT memoriesql.semantic_task_origin_authorized(command_tenant_id, command_task_id, database_now) THEN
        RAISE EXCEPTION 'relation assessment task is unavailable' USING ERRCODE = '42501';
    END IF;

    PERFORM pg_advisory_xact_lock_shared(hashtextextended(command_tenant_id::text||':semantic_outcome_authority:',0));
    PERFORM pg_advisory_xact_lock(hashtextextended(command_tenant_id::text||':relation-lineage:',0));
    database_now:=clock_timestamp();
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        command_tenant_id::text || ':relation_assessment.apply.v1:' || (requested_command->>'idempotency_key'), 0));
    database_now := pg_catalog.clock_timestamp();
    IF context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_authorized(command_access_scope_id, 'memory.maintain', 'write')
       OR NOT memoriesql.current_context_scope_time_authorized(command_access_scope_id, 'write', database_now)
       OR NOT memoriesql.current_context_semantic_task_authorized(command_task_id, 'memory.maintain', 'write')
       OR NOT memoriesql.semantic_task_origin_authorized(command_tenant_id, command_task_id, database_now) THEN
        RAISE EXCEPTION 'relation assessment command is outside authorization' USING ERRCODE = '42501';
    END IF;
    -- Every pinned bead, statement and evidence source stays authorized at apply.
    PERFORM memoriesql.relation_assessment_pins_authorize(command_tenant_id, command_task_id);
    SELECT r.* INTO receipt_record FROM memoriesql.idempotency_receipts AS r
    WHERE r.tenant_id = command_tenant_id AND r.operation_kind = 'relation_assessment.apply.v1'
      AND r.idempotency_key = requested_command->>'idempotency_key'
    FOR UPDATE;
    IF FOUND THEN
        IF receipt_record.request_hash <> computed_request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF receipt_record.status <> 'succeeded' OR receipt_record.response_receipt IS NULL THEN
            RAISE EXCEPTION 'relation assessment receipt is incomplete' USING ERRCODE = '55000';
        END IF;
        RETURN receipt_record.response_receipt || '{"replayed":true}'::jsonb;
    END IF;

    SELECT q.* INTO task_record FROM memoriesql.semantic_tasks AS q
    WHERE q.tenant_id = command_tenant_id AND q.task_id = command_task_id AND q.status = 'running'
      AND q.target_kind = 'canonical_semantics' AND q.lease_generation = command_generation
      AND q.lease_owner = requested_worker_id AND q.worker_instance_id = requested_worker_instance_id
      AND q.result_attempt_id IS NULL AND q.cancel_requested_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    SELECT a.* INTO attempt_record FROM memoriesql.semantic_task_attempts AS a
    WHERE a.tenant_id = command_tenant_id AND a.task_id = command_task_id AND a.attempt_id = command_attempt_id
      AND a.lease_generation = command_generation AND a.claimant_principal_id = context_record.principal_id
      AND a.worker_id = requested_worker_id AND a.worker_instance_id = requested_worker_instance_id
      AND a.status = 'running'
    FOR UPDATE;
    IF NOT FOUND OR NOT memoriesql.lock_semantic_task_outcome_authority(command_tenant_id, command_task_id) THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    database_now := pg_catalog.clock_timestamp();
    IF task_record.lease_expires_at <= database_now OR attempt_record.deadline_at <= database_now
       OR context_record.expires_at <= database_now
       OR NOT memoriesql.current_context_scope_time_authorized(command_access_scope_id, 'write', database_now)
       OR memoriesql.reauthorize_semantic_task(command_tenant_id, command_task_id, command_attempt_id,
            command_generation, requested_worker_id, requested_worker_instance_id, 'outcome', requested_at) <> 'authorized' THEN
        RAISE EXCEPTION 'stale_fence' USING ERRCODE = '40001';
    END IF;
    IF NOT memoriesql.relation_assessment_exposure_valid(command_tenant_id, command_task_id, command_attempt_id, command_generation) THEN
        RAISE EXCEPTION 'relation_assessment_exposure_required' USING ERRCODE = '42501';
    END IF;
    SELECT ra.* INTO x FROM memoriesql.relation_assessments AS ra
    WHERE ra.tenant_id = command_tenant_id AND ra.task_id = command_task_id;
    pinned_ids := ARRAY(SELECT b.v->>'bead_id' FROM jsonb_array_elements(x.beads) AS b(v));

    -- The run tree is exactly the author's root run and one settled specialist run
    -- per contribution, each contribution exactly as its attestor recorded it.
    SELECT r.run_id INTO root_run FROM memoriesql.semantic_task_runs AS r
    WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id AND r.lease_generation = command_generation
      AND r.parent_run_id IS NULL AND r.run_status = 'running'
      AND r.agent_key = CASE WHEN (requested_command->>'contract_revision')::integer=2 THEN 'memory.semantic.relation-author.v2' ELSE 'memory.semantic.relation-author' END;
    IF root_run IS NULL OR NOT root_run = ANY(command_model_runs)
       OR cardinality(command_model_runs) <> (SELECT count(DISTINCT r.v) FROM unnest(command_model_runs) AS r(v))
       OR cardinality(command_model_runs) <> (
            SELECT count(*) FROM memoriesql.semantic_task_runs AS r
            WHERE r.tenant_id = command_tenant_id AND r.task_id = command_task_id AND r.attempt_id = command_attempt_id
              AND r.lease_generation = command_generation)
       OR cardinality(command_model_runs) <> (
            SELECT count(*) FROM memoriesql.semantic_task_runs AS r
            WHERE r.tenant_id = command_tenant_id AND r.task_id = command_task_id AND r.attempt_id = command_attempt_id
              AND r.lease_generation = command_generation AND r.run_id = ANY(command_model_runs))
       OR EXISTS (SELECT 1 FROM memoriesql.semantic_task_runs AS r
                  WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id
                    AND r.parent_run_id IS NOT NULL
                    AND (NOT r.settled OR r.run_status <> 'succeeded' OR r.parent_run_id <> root_run
                         OR r.agent_key <> 'memory.semantic.relation-specialist'))
       OR (SELECT count(*) FROM memoriesql.semantic_task_runs AS r
           WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id AND r.parent_run_id IS NOT NULL)
          <> jsonb_array_length(body->'specialist_contributions') THEN
        RAISE EXCEPTION 'relation assessment run tree is incomplete' USING ERRCODE = '22023';
    END IF;
    FOR contributor IN SELECT value FROM jsonb_array_elements(body->'specialist_contributions') LOOP
        -- The specialist judged exactly these proposals, in this order: its attested
        -- packet hash binds them, so no proposal changes after its judgment.
        IF contributor->>'proposals_sha256' IS DISTINCT FROM memoriesql.classification_sha((
               SELECT jsonb_agg(pr.v ORDER BY pr.o) FROM jsonb_array_elements(body->'proposals') WITH ORDINALITY AS pr(v, o)
               WHERE pr.v->>'proposal_id' IN (SELECT jd.v->>'proposal_id'
                                              FROM jsonb_array_elements(contributor#>'{decision,judgments}') AS jd(v))))
           OR contributor->>'author_run_ref' IS DISTINCT FROM root_run OR NOT EXISTS (
            SELECT 1 FROM memoriesql.relation_assessment_deliveries AS d
            JOIN memoriesql.semantic_task_runs AS r ON r.tenant_id = d.tenant_id AND r.attempt_id = d.attempt_id AND r.run_id = d.run_id
            WHERE d.tenant_id = command_tenant_id AND d.task_id = command_task_id AND d.attempt_id = command_attempt_id
              AND d.lease_generation = command_generation AND d.role = 'specialist'
              AND d.request_id::text = contributor->>'request_id' AND d.run_id = contributor->>'model_run_ref'
              AND r.parent_run_id = root_run AND d.contribution = contributor) THEN
            RAISE EXCEPTION 'relation_specialist_contribution_unrecorded' USING ERRCODE = '22023';
        END IF;
    END LOOP;
    -- Every proposal is judged exactly once across the contributions, and only proposals are.
    IF (SELECT COALESCE(jsonb_agg(jd.v->>'proposal_id' ORDER BY jd.v->>'proposal_id'), '[]'::jsonb)
        FROM jsonb_array_elements(body->'specialist_contributions') AS sc(v),
             jsonb_array_elements(sc.v#>'{decision,judgments}') AS jd(v))
       IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(pr.v->>'proposal_id' ORDER BY pr.v->>'proposal_id'), '[]'::jsonb)
                         FROM jsonb_array_elements(body->'proposals') AS pr(v))
       OR (SELECT count(*) <> count(DISTINCT pr.v->>'proposal_id') FROM jsonb_array_elements(body->'proposals') AS pr(v)) THEN
        RAISE EXCEPTION 'relation_specialist_coverage_incomplete' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(requested_command->'used_evidence_refs') AS u(v)
               WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.evidence_units) AS e(v)
                                 WHERE e.v->>'source_unit_id' = u.v))
       OR (SELECT count(*) <> count(DISTINCT u.v) FROM jsonb_array_elements_text(requested_command->'used_evidence_refs') AS u(v)) THEN
        RAISE EXCEPTION 'relation assessment evidence is outside its pins' USING ERRCODE = '22023';
    END IF;

    -- Every unordered pinned pair, each bead with itself, has exactly one disposition.
    IF (SELECT jsonb_agg(jsonb_build_array(f.id, s.id) ORDER BY f.id, s.id)
        FROM unnest(pinned_ids::uuid[]) AS f(id) JOIN unnest(pinned_ids::uuid[]) AS s(id) ON f.id <= s.id)
       IS DISTINCT FROM (
        SELECT jsonb_agg(jsonb_build_array((d.v->>'first_bead_id')::uuid, (d.v->>'second_bead_id')::uuid)
                         ORDER BY (d.v->>'first_bead_id')::uuid, (d.v->>'second_bead_id')::uuid)
        FROM jsonb_array_elements(body->'dispositions') AS d(v)
        WHERE COALESCE(d.v->>'first_bead_id', '') ~ uuid_pattern AND COALESCE(d.v->>'second_bead_id', '') ~ uuid_pattern)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(body->'dispositions') AS d(v)
                  WHERE jsonb_typeof(d.v) IS DISTINCT FROM 'object'
                     OR d.v - ARRAY['first_bead_id', 'second_bead_id', 'disposition', 'abstention', 'reason'] <> '{}'::jsonb
                     OR COALESCE(d.v->>'disposition', '') NOT IN ('related', 'not_related', 'abstained', 'not_assessed')
                     OR (d.v->>'disposition' = 'abstained') IS DISTINCT FROM
                        COALESCE(d.v->>'abstention' IN ('no_fit', 'insufficient_evidence', 'ambiguous'), false)
                     OR (d.v->>'disposition' <> 'abstained' AND d.v->'abstention' IS DISTINCT FROM 'null'::jsonb)
                     OR jsonb_typeof(d.v->'reason') NOT IN ('string', 'null')
                     OR (jsonb_typeof(d.v->'reason') = 'string' AND (btrim(d.v->>'reason') = '' OR char_length(d.v->>'reason') > 1024))
                     OR (d.v->>'disposition' = 'not_assessed' AND jsonb_typeof(d.v->'reason') IS DISTINCT FROM 'string')
                     OR (d.v->>'disposition' = 'related') IS DISTINCT FROM EXISTS (
                        SELECT 1 FROM jsonb_array_elements(body->'proposals') AS pr(v)
                        WHERE LEAST((pr.v#>>'{source,bead_id}')::uuid, (pr.v#>>'{target,bead_id}')::uuid) = (d.v->>'first_bead_id')::uuid
                          AND GREATEST((pr.v#>>'{source,bead_id}')::uuid, (pr.v#>>'{target,bead_id}')::uuid) = (d.v->>'second_bead_id')::uuid)) THEN
        RAISE EXCEPTION 'relation_pair_coverage_invalid' USING ERRCODE = '22023';
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (tenant_id, workspace_id, access_scope_id, idempotency_receipt_id,
        operation_kind, idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at)
    VALUES (command_tenant_id, command_workspace_id, command_access_scope_id, pg_catalog.uuidv7(),
        'relation_assessment.apply.v1', requested_command->>'idempotency_key', computed_request_hash, 'in_progress',
        'semantic_task', command_task_id, 1, database_now, database_now)
    RETURNING idempotency_receipts.idempotency_receipt_id INTO new_receipt_id;

    -- Cycle-forbidden keys are serialized per tenant and key before any write, in a
    -- fixed order, with the same lock revision-6 authorship takes.
    FOR forbidden IN
        SELECT DISTINCT r.relation_type_id FROM jsonb_array_elements(body->'proposals') AS pr(v)
        JOIN jsonb_array_elements(x.relation_vocabulary) AS d(v)
          ON d.v->>'key' = pr.v#>>'{relation_type,key}' AND d.v->'revision' = pr.v#>'{relation_type,revision}'
        JOIN memoriesql.relation_types AS ty ON ty.type_key = d.v->>'key'
         AND (ty.tenant_id IS NULL OR (ty.tenant_id = command_tenant_id AND ty.workspace_id = command_workspace_id))
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
         AND r.revision = (d.v->>'revision')::integer
        ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            command_tenant_id::text || ':relation-cycle:' || forbidden::text, 0));
    END LOOP;

    FOR lock_id IN SELECT DISTINCT (v#>>'{retires,relation_id}')::uuid FROM jsonb_array_elements(body->'proposals') x(v) WHERE jsonb_typeof(v->'retires')='object' ORDER BY 1 LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(command_tenant_id::text||':bead-relation:'||lock_id::text,0));
    END LOOP;
    FOR p IN SELECT value FROM jsonb_array_elements(body->'proposals') ORDER BY value->>'proposal_id' LOOP
        IF jsonb_typeof(p) IS DISTINCT FROM 'object'
           OR p - ARRAY['proposal_id', 'relation_type', 'source', 'target', 'basis_statements', 'evidence', 'basis',
                        'qualification', 'rationale', 'author_confidence', 'retires'] <> '{}'::jsonb
           OR COALESCE(p->>'proposal_id', '') !~ uuid_pattern
           OR jsonb_typeof(p->'relation_type') IS DISTINCT FROM 'object'
           OR (p->'relation_type') - ARRAY['key', 'revision'] <> '{}'::jsonb
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.relation_vocabulary) AS d(v)
                          WHERE d.v->>'key' = p#>>'{relation_type,key}' AND d.v->'revision' = p#>'{relation_type,revision}')
           OR jsonb_typeof(p->'source') IS DISTINCT FROM 'object' OR jsonb_typeof(p->'target') IS DISTINCT FROM 'object'
           OR (p->'source') - ARRAY['bead_id', 'bead_version_id', 'statement_ids'] <> '{}'::jsonb
           OR (p->'target') - ARRAY['bead_id', 'bead_version_id', 'statement_ids'] <> '{}'::jsonb
           OR NOT memoriesql.relation_pinned_statements_v1(x.beads, p#>>'{source,bead_id}', p#>>'{source,bead_version_id}', p#>'{source,statement_ids}')
           OR NOT memoriesql.relation_pinned_statements_v1(x.beads, p#>>'{target,bead_id}', p#>>'{target,bead_version_id}', p#>'{target,statement_ids}')
           OR jsonb_typeof(p->'basis_statements') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'basis_statements') > 8
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(p->'basis_statements') AS b(v)
                      WHERE jsonb_typeof(b.v) IS DISTINCT FROM 'object'
                         OR b.v - ARRAY['bead_id', 'bead_version_id', 'statement_id'] <> '{}'::jsonb
                         OR jsonb_typeof(b.v->'statement_id') IS DISTINCT FROM 'string'
                         OR NOT memoriesql.relation_pinned_statements_v1(x.beads, b.v->>'bead_id', b.v->>'bead_version_id',
                                                                         jsonb_build_array(b.v->'statement_id')))
           OR jsonb_typeof(p->'evidence') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'evidence') NOT BETWEEN 1 AND 8
           OR COALESCE(p->>'basis', '') NOT IN ('source_stated', 'agent_inferred')
           OR (p->>'basis' = 'source_stated' AND jsonb_array_length(p->'basis_statements') = 0)
           OR jsonb_typeof(p->'rationale') IS DISTINCT FROM 'string' OR btrim(p->>'rationale') = '' OR char_length(p->>'rationale') > 1024
           OR jsonb_typeof(p->'qualification') NOT IN ('string', 'null')
           OR (jsonb_typeof(p->'qualification') = 'string' AND (btrim(p->>'qualification') = '' OR char_length(p->>'qualification') > 1024))
           OR jsonb_typeof(p->'author_confidence') IS DISTINCT FROM 'number'
           OR (p->>'author_confidence')::numeric NOT BETWEEN 0 AND 1
           OR round((p->>'author_confidence')::numeric, 2) <> (p->>'author_confidence')::numeric
           OR jsonb_typeof(p->'retires') NOT IN ('object', 'null') THEN
            RAISE EXCEPTION 'relation_proposal_invalid' USING ERRCODE = '22023';
        END IF;
        -- Endpoint statements differ, and basis statements are separate from both.
        named := ARRAY(SELECT s.i::uuid FROM jsonb_array_elements_text(p#>'{source,statement_ids}') AS s(i))
              || ARRAY(SELECT s.i::uuid FROM jsonb_array_elements_text(p#>'{target,statement_ids}') AS s(i))
              || ARRAY(SELECT (b.v->>'statement_id')::uuid FROM jsonb_array_elements(p->'basis_statements') AS b(v));
        IF cardinality(named) <> (SELECT count(DISTINCT n) FROM unnest(named) AS n) THEN
            RAISE EXCEPTION 'relation_statements_overlap' USING ERRCODE = '22023';
        END IF;
        -- Evidence: exact existing statement-evidence pairs of the proposal's own statements.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(p->'evidence') AS e(v)
                   WHERE jsonb_typeof(e.v) IS DISTINCT FROM 'object'
                      OR e.v - ARRAY['statement_id', 'source_unit_id', 'content_hash'] <> '{}'::jsonb
                      OR COALESCE(e.v->>'statement_id', '') !~ uuid_pattern
                      OR COALESCE(e.v->>'source_unit_id', '') !~ uuid_pattern
                      OR COALESCE(e.v->>'content_hash', '') !~ '^[a-f0-9]{64}$'
                      OR NOT (e.v->>'statement_id')::uuid = ANY(named)
                      OR NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS ev
                                     WHERE ev.tenant_id = command_tenant_id AND ev.statement_id = (e.v->>'statement_id')::uuid
                                       AND ev.evidence_source_unit_id = (e.v->>'source_unit_id')::uuid
                                       AND ev.evidence_content_hash = e.v->>'content_hash'))
           OR (SELECT count(*) <> count(DISTINCT (e.v->>'statement_id', e.v->>'source_unit_id'))
               FROM jsonb_array_elements(p->'evidence') AS e(v)) THEN
            RAISE EXCEPTION 'relation_evidence_unbound' USING ERRCODE = '22023';
        END IF;
        IF EXISTS (SELECT 1 FROM memoriesql.assessed_relations AS r
                   WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p->>'proposal_id')::uuid)
           OR EXISTS (SELECT 1 FROM memoriesql.bead_relations AS r
                      WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p->>'proposal_id')::uuid) THEN
            RAISE EXCEPTION 'relation_identifier_conflict' USING ERRCODE = '22023';
        END IF;
        SELECT r.relation_type_revision_id, r.is_symmetric INTO type_revision, type_symmetric
        FROM memoriesql.relation_types AS ty
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
        WHERE ty.type_key = p#>>'{relation_type,key}' AND r.revision = (p#>>'{relation_type,revision}')::integer
          AND (ty.tenant_id IS NULL OR (ty.tenant_id = command_tenant_id AND ty.workspace_id = command_workspace_id));
        IF type_revision IS NULL THEN
            RAISE EXCEPTION 'unknown_relation_type' USING ERRCODE = '22023';
        END IF;
        -- A symmetric assertion read in either direction is the same assertion.
        IF type_symmetric AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(body->'proposals') AS o(v)
            WHERE o.v->>'proposal_id' <> p->>'proposal_id' AND o.v->'relation_type' = p->'relation_type'
              AND o.v#>>'{source,bead_id}' = p#>>'{target,bead_id}' AND o.v#>>'{target,bead_id}' = p#>>'{source,bead_id}'
              AND (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(o.v#>'{source,statement_ids}') AS s(i))
                = (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(p#>'{target,statement_ids}') AS s(i))
              AND (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(o.v#>'{target,statement_ids}') AS s(i))
                = (SELECT array_agg(s.i ORDER BY s.i) FROM jsonb_array_elements_text(p#>'{source,statement_ids}') AS s(i))) THEN
            RAISE EXCEPTION 'relation_symmetric_duplicate' USING ERRCODE = '22023';
        END IF;
        SELECT sc.v, jd.v INTO contributor, j
        FROM jsonb_array_elements(body->'specialist_contributions') AS sc(v),
             jsonb_array_elements(sc.v#>'{decision,judgments}') AS jd(v)
        WHERE jd.v->>'proposal_id' = p->>'proposal_id';
        -- Agreement concerns the exact assertion: consistent, and the author's predicate,
        -- revision and direction among those the specialist finds warranted.
        agreed := j->>'outcome' = 'assessed' AND j->'consistent' = 'true'::jsonb AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(j->'warranted') AS w(v)
            WHERE w.v->'relation_type' = p->'relation_type' AND w.v->>'direction' = 'as_proposed');
        IF jsonb_typeof(p->'retires') = 'object' THEN
            IF (p->'retires') - ARRAY['relation_kind', 'relation_id', 'reason'] <> '{}'::jsonb
               OR COALESCE(p#>>'{retires,relation_id}', '') !~ uuid_pattern
               OR jsonb_typeof(p#>'{retires,reason}') IS DISTINCT FROM 'string'
               OR btrim(p#>>'{retires,reason}') = '' OR char_length(p#>>'{retires,reason}') > 1024
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(body->'proposals') AS o(v)
                          WHERE o.v->>'proposal_id' <> p->>'proposal_id' AND o.v->'retires' = p->'retires')
               OR EXISTS (SELECT 1 FROM memoriesql.relation_retirements AS rr
                          WHERE rr.tenant_id = command_tenant_id AND rr.retired_kind = p#>>'{retires,relation_kind}'
                            AND rr.retired_relation_id = (p#>>'{retires,relation_id}')::uuid) THEN
                RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
            END IF;
            -- Only an earlier active assertion among the pinned beads can be retired.
            IF p#>>'{retires,relation_kind}' = 'authored' THEN
                SELECT r.* INTO rel6 FROM memoriesql.bead_relations AS r
                WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p#>>'{retires,relation_id}')::uuid;
                IF rel6.relation_id IS NULL OR rel6.workspace_id <> command_workspace_id
                   OR NOT rel6.source_bead_id::text = ANY(pinned_ids) OR NOT rel6.target_bead_id::text = ANY(pinned_ids)
                   OR memoriesql.bead_relation_state_v1(command_tenant_id, rel6.relation_id, database_now)->>'state'
                      IN ('retracted', 'superseded') THEN
                    RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
                END IF;
            ELSIF p#>>'{retires,relation_kind}' = 'assessed' THEN
                SELECT r.* INTO prior FROM memoriesql.assessed_relations AS r
                WHERE r.tenant_id = command_tenant_id AND r.relation_id = (p#>>'{retires,relation_id}')::uuid;
                IF prior.relation_id IS NULL OR prior.workspace_id <> command_workspace_id
                   OR NOT prior.source_bead_id::text = ANY(pinned_ids) OR NOT prior.target_bead_id::text = ANY(pinned_ids)
                   OR memoriesql.assessed_relation_state_v1(command_tenant_id, prior.relation_id, database_now)->>'state'
                      IN ('retracted', 'superseded', 'not_accepted') THEN
                    RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
                END IF;
            ELSE
                RAISE EXCEPTION 'relation_retirement_invalid' USING ERRCODE = '22023';
            END IF;
        END IF;

        INSERT INTO memoriesql.assessed_relations (tenant_id, workspace_id, relation_id, task_id, attempt_id, author_run_id,
            specialist_run_id, specialist_request_id, source_access_scope_id, source_bead_id, source_bead_version_id,
            target_access_scope_id, target_bead_id, target_bead_version_id, relation_type_revision_id, basis,
            rationale_text, qualification_text, author_confidence, proposal, judgment, acceptance,
            applied_by_principal_id, recorded_at)
        SELECT command_tenant_id, command_workspace_id, (p->>'proposal_id')::uuid, command_task_id, command_attempt_id, root_run,
            contributor->>'model_run_ref', (contributor->>'request_id')::uuid,
            src.access_scope_id, src.bead_id, src.bead_version_id, dst.access_scope_id, dst.bead_id, dst.bead_version_id,
            type_revision, p->>'basis', p->>'rationale', NULLIF(p->'qualification', 'null'::jsonb) #>> '{}',
            (p->>'author_confidence')::numeric, p, j, CASE WHEN agreed THEN 'accepted' ELSE 'not_accepted' END,
            context_record.principal_id, database_now
        FROM memoriesql.accepted_bead_semantics AS src, memoriesql.accepted_bead_semantics AS dst
        WHERE src.tenant_id = command_tenant_id AND src.bead_id = (p#>>'{source,bead_id}')::uuid
          AND src.bead_version_id = (p#>>'{source,bead_version_id}')::uuid
          AND dst.tenant_id = command_tenant_id AND dst.bead_id = (p#>>'{target,bead_id}')::uuid
          AND dst.bead_version_id = (p#>>'{target,bead_version_id}')::uuid;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'relation_endpoint_unavailable' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.assessed_relation_statements (tenant_id, relation_id, role, workspace_id, access_scope_id,
            bead_id, bead_version_id, statement_id)
        SELECT command_tenant_id, (p->>'proposal_id')::uuid, n.role, st.workspace_id, st.access_scope_id,
               st.bead_id, st.bead_version_id, st.statement_id
        FROM (SELECT 'source' AS role, s.i::uuid AS id FROM jsonb_array_elements_text(p#>'{source,statement_ids}') AS s(i)
              UNION ALL SELECT 'target', s.i::uuid FROM jsonb_array_elements_text(p#>'{target,statement_ids}') AS s(i)
              UNION ALL SELECT 'basis', (b.v->>'statement_id')::uuid FROM jsonb_array_elements(p->'basis_statements') AS b(v)) AS n
        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = command_tenant_id AND st.statement_id = n.id;
        FOR statement, unit IN
            SELECT (e.v->>'statement_id')::uuid, (e.v->>'source_unit_id')::uuid FROM jsonb_array_elements(p->'evidence') AS e(v)
            ORDER BY 1, 2
        LOOP
            PERFORM memoriesql.record_semantic_evidence_link(command_tenant_id, command_workspace_id, 'assessed_relation',
                (p->>'proposal_id')::uuid, 'supports', statement, unit, database_now);
        END LOOP;
        relation_ids := array_append(relation_ids, (p->>'proposal_id')::uuid);
        IF agreed THEN
            accepted_ids := array_append(accepted_ids, (p->>'proposal_id')::uuid);
            fresh := fresh || jsonb_build_array(jsonb_build_object('kind', 'assessed', 'relation_id', p->>'proposal_id'));
            -- Same-write direction correction: the retired assertion stops counting at once.
            IF jsonb_typeof(p->'retires') = 'object' THEN
                INSERT INTO memoriesql.relation_retirements (tenant_id, workspace_id, retirement_id, retired_kind,
                    retired_relation_id, replacement_relation_id, reason_text, task_id, attempt_id, author_run_id,
                    idempotency_receipt_id, recorded_by_principal_id, recorded_at)
                VALUES (command_tenant_id, command_workspace_id, pg_catalog.uuidv7(), p#>>'{retires,relation_kind}',
                    (p#>>'{retires,relation_id}')::uuid, (p->>'proposal_id')::uuid, p#>>'{retires,reason}',
                    command_task_id, command_attempt_id, root_run, new_receipt_id, context_record.principal_id, database_now);
            END IF;
        END IF;
    END LOOP;

    -- No accepted assertion of a cycle-forbidden key closes a cycle among active
    -- assertions of both kinds once this write commits.
    IF memoriesql.relation_cycle_closes_v1(command_tenant_id, fresh) THEN
        RAISE EXCEPTION 'relation_cycle_forbidden' USING ERRCODE = '22023';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(body->'dispositions')
                ORDER BY value->>'first_bead_id', value->>'second_bead_id' LOOP
        INSERT INTO memoriesql.relation_pair_dispositions (tenant_id, workspace_id, task_id, first_access_scope_id,
            first_bead_id, first_bead_version_id, second_access_scope_id, second_bead_id, second_bead_version_id,
            disposition, abstention, reason_text, attempt_id, author_run_id, recorded_at)
        SELECT command_tenant_id, command_workspace_id, command_task_id, f.access_scope_id, f.bead_id, f.bead_version_id,
            s.access_scope_id, s.bead_id, s.bead_version_id, item->>'disposition',
            NULLIF(item->'abstention', 'null'::jsonb) #>> '{}', NULLIF(item->'reason', 'null'::jsonb) #>> '{}',
            command_attempt_id, root_run, database_now
        FROM jsonb_array_elements(x.beads) AS fb(v), jsonb_array_elements(x.beads) AS sb(v),
             memoriesql.accepted_bead_semantics AS f, memoriesql.accepted_bead_semantics AS s
        WHERE fb.v->>'bead_id' = item->>'first_bead_id' AND sb.v->>'bead_id' = item->>'second_bead_id'
          AND f.tenant_id = command_tenant_id AND f.bead_id = (fb.v->>'bead_id')::uuid
          AND f.bead_version_id = (fb.v->>'bead_version_id')::uuid
          AND s.tenant_id = command_tenant_id AND s.bead_id = (sb.v->>'bead_id')::uuid
          AND s.bead_version_id = (sb.v->>'bead_version_id')::uuid;
    END LOOP;

    outcome_status := memoriesql.record_semantic_task_outcome(
        command_tenant_id, command_task_id, command_attempt_id, command_generation, requested_worker_id,
        requested_worker_instance_id, 'succeeded', requested_command->>'semantic_result_hash',
        'semantic.application.' || command_attempt_id::text, NULL, NULL, NULL, NULL, 0, requested_at);
    IF outcome_status <> 'succeeded' THEN
        RAISE EXCEPTION 'semantic task success settlement was rejected: %', outcome_status USING ERRCODE = '40001';
    END IF;
    UPDATE memoriesql.semantic_task_runs AS r SET run_status = 'succeeded', finished_at = database_now, settled = true
    WHERE r.tenant_id = command_tenant_id AND r.task_id = command_task_id AND r.attempt_id = command_attempt_id
      AND r.lease_generation = command_generation AND r.parent_run_id IS NULL AND r.run_status = 'running'
      AND r.run_id = root_run;
    IF EXISTS (SELECT 1 FROM memoriesql.semantic_task_runs AS r
               WHERE r.tenant_id = command_tenant_id AND r.attempt_id = command_attempt_id AND NOT r.settled) THEN
        RAISE EXCEPTION 'relation assessment run tree did not settle' USING ERRCODE = '22023';
    END IF;
    INSERT INTO memoriesql.outbox_events (tenant_id, workspace_id, access_scope_id, outbox_event_id, idempotency_receipt_id,
        aggregate_kind, aggregate_id, event_kind, payload, headers, recorded_at, available_at)
    VALUES (command_tenant_id, command_workspace_id, command_access_scope_id, pg_catalog.uuidv7(), new_receipt_id,
        'semantic_task', command_task_id, 'relation_assessment.apply.v1',
        jsonb_build_object('task_id', command_task_id, 'attempt_id', command_attempt_id,
            'proposal_count', cardinality(relation_ids), 'accepted_count', cardinality(accepted_ids)),
        jsonb_build_object('contract_version', 1), database_now, database_now);
    response := jsonb_build_object('contract_version', 1, 'task_id', command_task_id, 'attempt_id', command_attempt_id,
        'idempotency_receipt_id', new_receipt_id, 'relation_ids', to_jsonb(relation_ids),
        'accepted_relation_ids', to_jsonb(accepted_ids), 'task_status', 'succeeded', 'replayed', false);
    UPDATE memoriesql.idempotency_receipts AS r SET status = 'succeeded', response_receipt = response,
        updated_at = database_now, completed_at = database_now
    WHERE r.tenant_id = command_tenant_id AND r.idempotency_receipt_id = new_receipt_id;
    RETURN response;
END;
$$;

-- From 0029_relation_assessment.sql: the new revisions handled exactly like the ones they extend.
CREATE OR REPLACE FUNCTION memoriesql.record_relation_delivery_v1(request_id uuid, payload_hash text, packet jsonb, decision jsonb)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE; i memoriesql.model_provider_request_intents%ROWTYPE;
    x memoriesql.relation_assessments%ROWTYPE; q memoriesql.semantic_tasks%ROWTYPE;
    a memoriesql.semantic_task_attempts%ROWTYPE; run memoriesql.semantic_task_runs%ROWTYPE;
    item jsonb; expected jsonb := '[]'; delivered_refs jsonb := '[]'; attested jsonb;
    delivered bigint := 0; packet_role text := packet->>'role';
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT pi.* INTO i FROM memoriesql.model_provider_request_intents AS pi
    WHERE pi.tenant_id = c.tenant_id AND pi.request_id = record_relation_delivery_v1.request_id;
    SELECT ra.* INTO x FROM memoriesql.relation_assessments AS ra WHERE ra.tenant_id = c.tenant_id AND ra.task_id = i.task_id;
    SELECT st.* INTO q FROM memoriesql.semantic_tasks AS st WHERE st.tenant_id = c.tenant_id AND st.task_id = i.task_id FOR UPDATE;
    SELECT sa.* INTO a FROM memoriesql.semantic_task_attempts AS sa WHERE sa.tenant_id = c.tenant_id AND sa.attempt_id = i.attempt_id;
    SELECT r.* INTO run FROM memoriesql.semantic_task_runs AS r
    WHERE r.tenant_id = c.tenant_id AND r.attempt_id = i.attempt_id AND r.run_id = i.run_id;
    IF c.principal_kind IS DISTINCT FROM 'service' OR c.principal_id = a.claimant_principal_id
       OR x.task_id IS NULL OR q.task_kind IS DISTINCT FROM 'memory.semantic.assess-relations' OR q.contract_revision NOT IN (1,2)
       OR i.request_payload_hash IS DISTINCT FROM payload_hash
       OR i.task_id::text IS DISTINCT FROM packet->>'task_id' OR i.attempt_id::text IS DISTINCT FROM packet->>'attempt_id'
       OR q.status IS DISTINCT FROM 'running' OR a.status IS DISTINCT FROM 'running' OR q.cancel_requested_at IS NOT NULL
       OR q.lease_generation IS DISTINCT FROM a.lease_generation
       OR q.lease_expires_at <= pg_catalog.clock_timestamp() OR a.deadline_at <= pg_catalog.clock_timestamp()
       OR NOT EXISTS (SELECT 1 FROM memoriesql.relation_assessment_dispatch_policies AS d
                      WHERE d.tenant_id = c.tenant_id AND d.dispatch_policy_id = x.dispatch_policy_id
                        AND d.attestor_principal_id = c.principal_id AND d.status = 'active'
                        AND d.expires_at > pg_catalog.clock_timestamp())
       OR NOT EXISTS (SELECT 1 FROM memoriesql.model_usage_events AS u
                      WHERE u.tenant_id = c.tenant_id AND u.request_id = i.request_id AND u.outcome = 'succeeded')
       OR run.run_status IS DISTINCT FROM 'running'
       OR (packet_role = 'author' AND (i.run_role IS DISTINCT FROM 'direct_leaf' OR run.parent_run_id IS NOT NULL
                                       OR run.agent_key IS DISTINCT FROM CASE WHEN q.contract_revision=2 THEN 'memory.semantic.relation-author.v2' ELSE 'memory.semantic.relation-author' END))
       OR (packet_role = 'specialist' AND (i.run_role IS DISTINCT FROM 'delegate' OR run.parent_run_id IS NULL
                                           OR run.agent_key IS DISTINCT FROM 'memory.semantic.relation-specialist'
                                           OR i.parent_run_id IS DISTINCT FROM packet->>'author_run_id'))
       OR COALESCE(packet_role, '') NOT IN ('author', 'specialist') THEN
        RAISE EXCEPTION 'trusted_relation_delivery_required' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(packet) IS DISTINCT FROM 'object' OR packet->'contract_version' IS DISTINCT FROM '1'::jsonb
       OR octet_length(memoriesql.canonical_semantic_json_text(packet)) > 1048576
       OR (packet_role = 'author' AND (packet - ARRAY['contract_version', 'role', 'task_id', 'attempt_id', 'subject_bead_id',
               'beads', 'relation_vocabulary', 'evidence', 'reconsideration'] <> '{}'::jsonb
            OR packet->'reconsideration' IS DISTINCT FROM COALESCE(x.reconsideration, 'null'::jsonb)
            OR decision IS NOT NULL))
       OR (packet_role = 'specialist' AND (packet - ARRAY['contract_version', 'role', 'task_id', 'attempt_id', 'author_run_id',
               'subject_bead_id', 'beads', 'relation_vocabulary', 'evidence', 'proposals'] <> '{}'::jsonb
            OR jsonb_typeof(packet->'proposals') IS DISTINCT FROM 'array'
            OR jsonb_array_length(packet->'proposals') NOT BETWEEN 1 AND 16
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(packet->'proposals') AS p(v)
                       WHERE jsonb_typeof(p.v) IS DISTINCT FROM 'object'
                          OR COALESCE(p.v->>'proposal_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
            OR (SELECT count(*) <> count(DISTINCT p.v->>'proposal_id') FROM jsonb_array_elements(packet->'proposals') AS p(v))))
       OR packet->>'subject_bead_id' IS DISTINCT FROM x.subject_bead_id::text
       OR packet->'beads' IS DISTINCT FROM x.beads
       OR packet->'relation_vocabulary' IS DISTINCT FROM x.relation_vocabulary
       OR jsonb_typeof(packet->'evidence') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'relation_packet_invalid' USING ERRCODE = '22023';
    END IF;
    -- Re-derive every excerpt under this attestor's own authority; the packet must
    -- carry exactly the pinned evidence, in unit order, byte for byte.
    FOR item IN SELECT value FROM jsonb_array_elements(x.evidence_units) ORDER BY value->>'source_unit_id' LOOP
        PERFORM memoriesql.revisiting_source_authorize((item->>'source_object_id')::uuid);
        expected := expected || jsonb_build_array(memoriesql.relation_evidence_content_v1(c.tenant_id, item));
    END LOOP;
    IF packet->'evidence' IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'relation_evidence_mismatch' USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('source_unit_id', e.v->>'source_unit_id', 'content_hash', e.v->>'content_hash',
               'sha256', memoriesql.classification_sha(e.v)) ORDER BY e.v->>'source_unit_id'), '[]'::jsonb),
           COALESCE(sum(COALESCE(char_length(e.v->>'content'), 0)
                + COALESCE((SELECT sum(char_length(p.v->>'content')) FROM jsonb_array_elements(e.v->'parts') AS p(v)), 0)), 0)
    INTO delivered_refs, delivered FROM jsonb_array_elements(expected) AS e(v);
    IF delivered > 131072 THEN
        RAISE EXCEPTION 'relation_evidence_budget' USING ERRCODE = '54000';
    END IF;
    IF packet_role = 'specialist' THEN
        IF jsonb_typeof(decision) IS DISTINCT FROM 'object' OR decision - ARRAY['contract_version', 'judgments'] <> '{}'::jsonb
           OR decision->'contract_version' IS DISTINCT FROM '1'::jsonb
           OR jsonb_typeof(decision->'judgments') IS DISTINCT FROM 'array'
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(decision->'judgments') AS j(v)
                      WHERE NOT memoriesql.relation_judgment_valid_v1(j.v, x.relation_vocabulary)) THEN
            RAISE EXCEPTION 'relation_specialist_decision_invalid' USING ERRCODE = '22023';
        END IF;
        IF octet_length(memoriesql.canonical_semantic_json_text(decision)) > 65536 THEN
            RAISE EXCEPTION 'relation_specialist_batch_oversized' USING ERRCODE = '54000';
        END IF;
        -- Every proposal in the batch is judged exactly once, and nothing else is.
        IF (SELECT COALESCE(jsonb_agg(j.v->>'proposal_id' ORDER BY j.v->>'proposal_id'), '[]'::jsonb)
            FROM jsonb_array_elements(decision->'judgments') AS j(v))
           IS DISTINCT FROM (SELECT jsonb_agg(p.v->>'proposal_id' ORDER BY p.v->>'proposal_id')
                             FROM jsonb_array_elements(packet->'proposals') AS p(v)) THEN
            RAISE EXCEPTION 'relation_specialist_batch_incomplete' USING ERRCODE = '22023';
        END IF;
        attested := jsonb_build_object('contract_version', 1, 'request_id', i.request_id, 'model_run_ref', i.run_id,
            'author_run_ref', i.parent_run_id, 'packet_sha256', memoriesql.classification_sha(packet),
            'proposals_sha256', memoriesql.classification_sha(packet->'proposals'),
            'vocabulary_sha256', memoriesql.classification_sha(packet->'relation_vocabulary'), 'decision', decision);
    ELSIF decision IS NOT NULL THEN
        RAISE EXCEPTION 'relation_packet_invalid' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM memoriesql.relation_assessment_deliveries AS d
               WHERE d.tenant_id = c.tenant_id AND d.request_id = i.request_id) THEN
        IF NOT EXISTS (SELECT 1 FROM memoriesql.relation_assessment_deliveries AS d
                       WHERE d.tenant_id = c.tenant_id AND d.request_id = i.request_id
                         AND d.packet_sha256 = memoriesql.classification_sha(packet)
                         AND d.contribution IS NOT DISTINCT FROM attested) THEN
            RAISE EXCEPTION 'relation_delivery_replay_conflict' USING ERRCODE = '23505';
        END IF;
    ELSE
        INSERT INTO memoriesql.relation_assessment_deliveries (tenant_id, request_id, task_id, attempt_id, lease_generation,
            run_id, role, packet_sha256, evidence, delivered_characters, contribution, attestor_principal_id, recorded_at)
        VALUES (c.tenant_id, i.request_id, i.task_id, i.attempt_id, a.lease_generation, i.run_id, packet_role,
            memoriesql.classification_sha(packet), delivered_refs, delivered, attested, c.principal_id, pg_catalog.clock_timestamp());
    END IF;
    PERFORM memoriesql.relation_assessment_pins_authorize(c.tenant_id, i.task_id);
    IF q.lease_expires_at <= pg_catalog.clock_timestamp() OR a.deadline_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'relation_delivery_stale_attempt' USING ERRCODE = '42501';
    END IF;
    RETURN true;
END; $$;

-- From 0029_relation_assessment.sql: the new revisions handled exactly like the ones they extend.
CREATE OR REPLACE FUNCTION memoriesql.record_semantic_run_event(
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
       OR requested_at IS NULL
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
          AND (parent.run_role = 'conductor' OR
            (parent.run_role='direct_leaf' AND parent.run_status='running'
             AND task_record.task_kind='memory.semantic.author-complete-unit' AND task_record.contract_revision=5
             AND requested_agent_key='memory.semantic.bead-type-classifier'
             AND requested_input_contract_id='memory.semantic.bead-classification.packet' AND requested_input_contract_revision=1
             AND requested_input_contract_hash='c661867057e6a6e31950f56ec27f2be71ec1fe550ed7283f82c4a53dd65eb389'
             AND requested_output_contract_id='memory.semantic.bead-classification.decision' AND requested_output_contract_revision=1
             AND requested_output_contract_hash='c1efb5bc7652a827e63b1bdbadabc7ce19c72ca74c38abf41dd7061ecc09b802'
             AND requested_model_profile_key='classification.standard' AND requested_model_profile_revision=1
             AND requested_effort_key='standard'
             AND NOT EXISTS(SELECT 1 FROM memoriesql.semantic_task_runs child WHERE child.tenant_id=requested_tenant_id
                AND child.attempt_id=requested_attempt_id AND child.parent_run_id=requested_parent_run_id))
            -- One runtime-scheduled relation specialist after the author, never a delegation tool.
            OR (parent.run_role='direct_leaf' AND parent.run_status='running'
             AND task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision IN (1,2)
             AND requested_agent_key='memory.semantic.relation-specialist'
             AND requested_input_contract_id='memory.semantic.relation-assessment.packet' AND requested_input_contract_revision=1
             AND requested_input_contract_hash='50ab104aa74104f5229be113b3666fc56f6a544629c5e5221d3b89d7ae4c11dc'
             AND requested_output_contract_id='memory.semantic.relation-assessment.decision' AND requested_output_contract_revision=1
             AND requested_output_contract_hash='d9d2912d3aeef02ea5db097c670ef0fbec77fee0a9cb3dd069271294d98f73b6'
             AND requested_model_profile_key='relation-specialist.standard' AND requested_model_profile_revision=1
             AND requested_effort_key='standard'
             AND NOT EXISTS(SELECT 1 FROM memoriesql.semantic_task_runs child WHERE child.tenant_id=requested_tenant_id
                AND child.attempt_id=requested_attempt_id AND child.parent_run_id=requested_parent_run_id)));

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

-- From 0029_relation_assessment.sql activate_relation_assessment_v1: relation revision 2's activation.
CREATE FUNCTION memoriesql.activate_relation_assessment_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, memoriesql SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE; subject memoriesql.accepted_bead_semantics%ROWTYPE;
    accepted memoriesql.accepted_bead_semantics%ROWTYPE; old memoriesql.idempotency_receipts%ROWTYPE;
    prior memoriesql.relation_assessments%ROWTYPE; resolved memoriesql.relation_type_revisions%ROWTYPE;
    rid uuid := pg_catalog.uuidv7(); tid uuid := pg_catalog.uuidv7(); eid uuid;
    item jsonb; pinned jsonb := '[]'; vocabulary jsonb := '[]'; units jsonb; input jsonb; result jsonb;
    reconsideration jsonb; request_hash text; candidates uuid[]; declared bigint;
    started timestamp with time zone := pg_catalog.clock_timestamp();
    uuid_pattern constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request->>'contract_version' IS DISTINCT FROM '2' OR request->>'expected_schema_version' IS DISTINCT FROM '53'
       OR request - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'subject_bead_id',
                          'candidate_bead_ids', 'relation_vocabulary', 'dispatch_policy_id', 'reconsiders_task_id'] <> '{}'::jsonb
       OR COALESCE(length(request->>'idempotency_key'), 0) NOT BETWEEN 1 AND 512 OR octet_length(request::text) > 16384
       OR COALESCE(request->>'subject_bead_id', '') !~ uuid_pattern
       OR COALESCE(request->>'dispatch_policy_id', '') !~ uuid_pattern
       OR (jsonb_typeof(request->'reconsiders_task_id') IS DISTINCT FROM 'null'
           AND COALESCE(request->>'reconsiders_task_id', '') !~ uuid_pattern)
       OR jsonb_typeof(request->'candidate_bead_ids') IS DISTINCT FROM 'array'
       OR jsonb_array_length(request->'candidate_bead_ids') > 8
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(request->'candidate_bead_ids') AS x(v)
                  WHERE jsonb_typeof(x.v) IS DISTINCT FROM 'string' OR x.v#>>'{}' !~ uuid_pattern
                     OR x.v#>>'{}' = request->>'subject_bead_id')
       OR (SELECT count(*) <> count(DISTINCT x.i) FROM jsonb_array_elements_text(request->'candidate_bead_ids') AS x(i))
       OR jsonb_typeof(request->'relation_vocabulary') IS DISTINCT FROM 'array'
       OR jsonb_array_length(request->'relation_vocabulary') NOT BETWEEN 1 AND 32
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(request->'relation_vocabulary') AS x(v)
                  WHERE jsonb_typeof(x.v) IS DISTINCT FROM 'object' OR x.v - ARRAY['key', 'revision'] <> '{}'::jsonb
                     OR jsonb_typeof(x.v->'key') IS DISTINCT FROM 'string' OR x.v->>'revision' !~ '^[1-9][0-9]{0,8}$')
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(request->'relation_vocabulary') AS x(v)
                  GROUP BY x.v->>'key' HAVING count(*) <> 1) THEN
        RAISE EXCEPTION 'invalid_relation_assessment_activation' USING ERRCODE = '22023';
    END IF;
    candidates := ARRAY(SELECT x.i::uuid FROM jsonb_array_elements_text(request->'candidate_bead_ids') WITH ORDINALITY AS x(i, o) ORDER BY x.o);
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF c.tenant_id IS NULL OR c.expires_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'relation_assessment_activation_denied' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':relation-assessment-activate:' || (request->>'idempotency_key'), 0));

    -- Every pinned bead is an accepted version the activator may maintain.
    SELECT a.* INTO subject FROM memoriesql.accepted_bead_semantics AS a
    WHERE a.tenant_id = c.tenant_id AND a.workspace_id = c.workspace_id AND a.bead_id = (request->>'subject_bead_id')::uuid;
    IF subject.bead_id IS NULL OR NOT memoriesql.lifecycle_bead_authorized(
            c.tenant_id, subject.workspace_id, subject.access_scope_id, subject.bead_version_id) THEN
        RAISE EXCEPTION 'relation_assessment_subject_unavailable' USING ERRCODE = '42501';
    END IF;
    FOR item IN SELECT to_jsonb(x.id) FROM unnest(candidates) AS x(id) LOOP
        SELECT a.* INTO accepted FROM memoriesql.accepted_bead_semantics AS a
        WHERE a.tenant_id = c.tenant_id AND a.workspace_id = c.workspace_id AND a.bead_id = (item#>>'{}')::uuid;
        IF accepted.bead_id IS NULL OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                c.tenant_id, accepted.workspace_id, accepted.access_scope_id, accepted.bead_version_id) THEN
            RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM memoriesql.relation_assessment_dispatch_policies AS d
                   WHERE d.tenant_id = c.tenant_id AND d.dispatch_policy_id = (request->>'dispatch_policy_id')::uuid
                     AND d.workspace_id = c.workspace_id AND d.status = 'active' AND d.expires_at > pg_catalog.clock_timestamp()) THEN
        RAISE EXCEPTION 'relation_assessment_policy_unavailable' USING ERRCODE = '42501';
    END IF;

    request_hash := encode(sha256(convert_to(request::text, 'UTF8')), 'hex');
    SELECT r.* INTO old FROM memoriesql.idempotency_receipts AS r
    WHERE r.tenant_id = c.tenant_id AND r.operation_kind = 'relation_assessment.activate.v2'
      AND r.idempotency_key = request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash <> request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        RETURN old.response_receipt || '{"replayed":true}'::jsonb;
    END IF;

    -- Exact latest active revisions only; the task never mints vocabulary.
    FOR item IN SELECT value FROM jsonb_array_elements(request->'relation_vocabulary') ORDER BY value->>'key' LOOP
        resolved := memoriesql.relation_type_active_revision(c.tenant_id, c.workspace_id, item->>'key', (item->>'revision')::integer);
        IF resolved.relation_type_revision_id IS NULL THEN
            RAISE EXCEPTION 'unknown_relation_type' USING ERRCODE = '22023';
        END IF;
        vocabulary := vocabulary || jsonb_build_array(jsonb_build_object('key', item->>'key', 'revision', resolved.revision,
            'namespace', (SELECT ty.namespace FROM memoriesql.relation_types AS ty WHERE ty.relation_type_id = resolved.relation_type_id),
            'label', resolved.display_label, 'definition', resolved.definition, 'endpoint_rule', resolved.endpoint_rule,
            'evidence_expectation', resolved.evidence_expectation, 'example', resolved.example,
            'counterexample', resolved.counterexample, 'cycle_policy', resolved.cycle_policy,
            'forward_reading', resolved.forward_reading, 'inverse_reading', resolved.inverse_reading,
            'symmetric', resolved.is_symmetric));
    END LOOP;
    IF octet_length(memoriesql.canonical_semantic_json_text(vocabulary)) > 32768 THEN
        RAISE EXCEPTION 'relation_vocabulary_budget' USING ERRCODE = '54000';
    END IF;

    pinned := jsonb_build_array(memoriesql.relation_assessment_bead_packet_v1(c.tenant_id, c.workspace_id, subject.bead_id, 'subject'));
    FOR item IN SELECT to_jsonb(x.id) FROM unnest(candidates) WITH ORDINALITY AS x(id, o) ORDER BY x.o LOOP
        pinned := pinned || jsonb_build_array(
            memoriesql.relation_assessment_bead_packet_v1(c.tenant_id, c.workspace_id, (item#>>'{}')::uuid, 'candidate'));
    END LOOP;
    IF octet_length(memoriesql.canonical_semantic_json_text(pinned)) > 131072 THEN
        RAISE EXCEPTION 'relation_assessment_budget' USING ERRCODE = '54000';
    END IF;
    units := memoriesql.relation_evidence_units_v1(c.tenant_id, pinned);
    SELECT COALESCE(sum((u.v->>'declared_characters')::bigint), 0) INTO declared FROM jsonb_array_elements(units) AS u(v);
    IF jsonb_array_length(units) > 72 OR declared > 131072 THEN
        RAISE EXCEPTION 'relation_evidence_budget' USING ERRCODE = '54000';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(units) LOOP
        PERFORM memoriesql.revisiting_source_authorize((item->>'source_object_id')::uuid);
    END LOOP;

    IF jsonb_typeof(request->'reconsiders_task_id') = 'string' THEN
        SELECT ra.* INTO prior FROM memoriesql.relation_assessments AS ra
        WHERE ra.tenant_id = c.tenant_id AND ra.task_id = (request->>'reconsiders_task_id')::uuid FOR SHARE;
        -- An applied assessment of the same subject with at least one proposal left
        -- unaccepted. Every bead it pinned is pinned again at its immutable accepted
        -- version, so the carried disagreement names only statements this task pins
        -- and authorizes; new candidates and current vocabulary revisions may join
        -- when evidence or understanding changed. No count limits it: each
        -- reconsideration is its own explicit, bounded activation, a reconsideration
        -- may itself be reconsidered, and nothing activates one automatically.
        IF prior.task_id IS NULL OR prior.workspace_id <> c.workspace_id
           OR prior.subject_bead_id <> subject.bead_id
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(prior.beads) AS earlier(v)
                      WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(pinned) AS again(v)
                                        WHERE again.v->>'bead_id' = earlier.v->>'bead_id'
                                          AND again.v->>'bead_version_id' = earlier.v->>'bead_version_id'))
           OR NOT EXISTS (SELECT 1 FROM memoriesql.semantic_tasks AS q
                          WHERE q.tenant_id = c.tenant_id AND q.task_id = prior.task_id AND q.status = 'succeeded')
           OR NOT EXISTS (SELECT 1 FROM memoriesql.assessed_relations AS r
                          WHERE r.tenant_id = c.tenant_id AND r.task_id = prior.task_id AND r.acceptance = 'not_accepted') THEN
            RAISE EXCEPTION 'relation_reconsideration_unavailable' USING ERRCODE = '55000';
        END IF;
        SELECT jsonb_build_object('reconsiders_task_id', prior.task_id,
                   'proposals', jsonb_agg(r.proposal ORDER BY r.relation_id),
                   'judgments', jsonb_agg(r.judgment ORDER BY r.relation_id))
        INTO reconsideration FROM memoriesql.assessed_relations AS r
        WHERE r.tenant_id = c.tenant_id AND r.task_id = prior.task_id AND r.acceptance = 'not_accepted';
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (tenant_id, workspace_id, access_scope_id, idempotency_receipt_id, operation_kind,
        idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at)
    VALUES (c.tenant_id, c.workspace_id, subject.access_scope_id, rid, 'relation_assessment.activate.v2',
        request->>'idempotency_key', request_hash, 'in_progress', 'semantic_task', tid, 1, started, started);
    input := jsonb_build_object('task_id', tid, 'task_kind', 'memory.semantic.assess-relations', 'contract_revision', 2,
        'target_reference', subject.bead_id, 'expected_target_revision', 0,
        'requested_effort_key', NULL, 'requested_budget', NULL,
        'evidence_manifest', jsonb_build_object('manifest_id', 'relation-assessment.' || tid::text, 'revision', 1,
            'references', (SELECT jsonb_agg(jsonb_build_object('reference_id', u.v->>'source_unit_id',
                               'content_hash', u.v->>'content_hash',
                               'declared_characters', (u.v->>'declared_characters')::integer) ORDER BY u.v->>'source_unit_id')
                           FROM jsonb_array_elements(units) AS u(v))),
        'payload', jsonb_build_object('subject_bead_id', subject.bead_id, 'beads', pinned,
            'relation_vocabulary', vocabulary, 'dispatch_policy_id', request->>'dispatch_policy_id',
            'reconsideration', reconsideration, 'required_execution', 'trusted_relation_assessment_v1'));
    SELECT q.idempotency_receipt_id INTO eid FROM memoriesql.enqueue_semantic_task(tid,
        'relation-assessment.v2:' || tid::text, 'memoriesql.kernel', 'memory.semantic.assess-relations', 2,
        'd79977ebc0c64cd0448857b46020fe7a79659ba55c32de6c038041a6d1bfcc2d', 'semantic-tasks-v1:acb8198184ab7920a2ad6fce484a14d4ad03bd4eea6418b7fd62b16a2c5bab34', subject.bead_id::text, 0, input,
        input#>>'{evidence_manifest,manifest_id}', subject.access_scope_id, started, NULL, started) AS q;
    INSERT INTO memoriesql.relation_assessments (tenant_id, workspace_id, access_scope_id, task_id, subject_bead_id,
        subject_bead_version_id, candidate_bead_ids, beads, relation_vocabulary, evidence_units, reconsideration,
        dispatch_policy_id, reconsiders_task_id, origin_principal_id, activation_receipt_id, created_at)
    VALUES (c.tenant_id, c.workspace_id, subject.access_scope_id, tid, subject.bead_id, subject.bead_version_id,
        candidates, pinned, vocabulary, units, reconsideration, (request->>'dispatch_policy_id')::uuid,
        prior.task_id, c.principal_id, rid, started);
    result := jsonb_build_object('contract_version', 2, 'task_id', tid, 'subject_bead_id', subject.bead_id,
        'subject_bead_version_id', subject.bead_version_id, 'candidate_bead_ids', to_jsonb(candidates),
        'reconsiders_task_id', prior.task_id, 'idempotency_receipt_id', rid, 'enqueue_receipt_id', eid,
        'replayed', false);
    -- Recheck every authority at the return boundary; never cache permission.
    PERFORM memoriesql.relation_assessment_pins_authorize(c.tenant_id, tid);
    UPDATE memoriesql.idempotency_receipts AS r SET status = 'succeeded', response_receipt = result,
        completed_at = pg_catalog.clock_timestamp(), updated_at = pg_catalog.clock_timestamp()
    WHERE r.tenant_id = c.tenant_id AND r.idempotency_receipt_id = rid;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.activate_relation_assessment_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.activate_relation_assessment_v2(jsonb) TO memoriesql_application;
