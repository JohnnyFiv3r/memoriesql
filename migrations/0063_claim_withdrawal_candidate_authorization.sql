-- Revision-6 authorship carries immutable claim states in its pinned candidates.
-- Apply current origin and worker authority to withdrawal evidence that can
-- affect those states before hydrating the task input or dispatching it again.
CREATE OR REPLACE FUNCTION memoriesql.relation_candidates_authorize(t uuid, task uuid)
RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    e memoriesql.complete_input_executions%ROWTYPE;
    item jsonb;
    a memoriesql.accepted_bead_semantics%ROWTYPE;
    activated_through timestamptz;
    checked timestamptz := clock_timestamp();
    dependency record;
BEGIN
    SELECT * INTO e FROM memoriesql.complete_input_executions
    WHERE tenant_id = t AND execution_task_id = task;
    IF e.execution_contract_revision IS DISTINCT FROM 6 THEN RETURN; END IF;

    -- The activation receipt completes after its candidate packet is frozen.
    -- It bounds the dependency check for persisted packets, including those
    -- created before this migration. The packet has no snapshot timestamp.
    -- During overlap, this upper bound can include a withdrawal recorded after
    -- the packet was captured; uncertain dependencies still require authority.
    SELECT r.completed_at INTO activated_through
    FROM memoriesql.idempotency_receipts AS r
    WHERE r.tenant_id = t AND r.idempotency_receipt_id = e.activation_receipt_id
      AND r.status = 'succeeded';

    FOR item IN SELECT value FROM jsonb_array_elements(e.relation_candidates) LOOP
        SELECT * INTO a FROM memoriesql.accepted_bead_semantics
        WHERE tenant_id = t AND bead_id = (item->>'bead_id')::uuid
          AND bead_version_id = (item->>'bead_version_id')::uuid;
        IF a.bead_id IS NULL OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(
            t, a.workspace_id, a.access_scope_id, a.bead_version_id)
           OR NOT memoriesql.relation_assessment_origin_bead_authorized(
               t, task, a.workspace_id, a.access_scope_id, a.bead_version_id, checked)
        THEN
            RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE = '42501';
        END IF;
        IF jsonb_array_length(item->'claims') = 0 THEN CONTINUE; END IF;
        IF activated_through IS NULL THEN
            RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE = '42501';
        END IF;

        FOR dependency IN
            WITH pinned AS (
                SELECT p.claim_id, p.state_at_activation
                FROM jsonb_to_recordset(item->'claims') AS p(claim_id uuid, state_at_activation text)
            ), compared AS (
                SELECT p.claim_id, p.state_at_activation, c.related_claim_id AS other,
                       c.action, c.recorded_at
                FROM pinned AS p
                JOIN memoriesql.bead_claim_events AS c ON c.claim_id = p.claim_id
                WHERE c.tenant_id = t AND c.workspace_id = a.workspace_id
                  AND c.action IN ('supersede', 'dispute') AND c.recorded_at <= activated_through
                UNION ALL
                SELECT p.claim_id, p.state_at_activation, c.claim_id, c.action, c.recorded_at
                FROM pinned AS p
                JOIN memoriesql.bead_claim_events AS c ON c.related_claim_id = p.claim_id
                WHERE c.tenant_id = t AND c.workspace_id = a.workspace_id
                  AND c.action = 'dispute' AND c.recorded_at <= activated_through
            ), withdrawal_targets AS (
                SELECT p.claim_id FROM pinned AS p WHERE p.state_at_activation = 'retracted'
                UNION
                SELECT c.other FROM compared AS c
                WHERE (c.action = 'supersede' AND c.state_at_activation IN ('current', 'disputed'))
                   OR (c.action = 'dispute' AND c.state_at_activation = 'current' AND NOT EXISTS (
                        SELECT 1 FROM memoriesql.bead_claim_events AS resolution
                        WHERE resolution.tenant_id = t AND resolution.workspace_id = a.workspace_id
                          AND resolution.action = 'resolve_dispute'
                          AND resolution.recorded_at BETWEEN c.recorded_at AND activated_through
                          AND ((resolution.claim_id = c.claim_id AND resolution.related_claim_id = c.other)
                            OR (resolution.claim_id = c.other AND resolution.related_claim_id = c.claim_id))
                   ))
            )
            SELECT DISTINCT l.statement_id, l.evidence_source_unit_id, x.evidence_content_hash,
                statement.access_scope_id AS statement_scope_id, statement.event_id AS statement_event_id,
                x.access_scope_id AS evidence_scope_id, x.evidence_event_id,
                statement_event.source_object_id AS statement_source_id,
                evidence_event.source_object_id AS evidence_source_id
            FROM withdrawal_targets AS target
            JOIN memoriesql.bead_claim_events AS event
              ON event.tenant_id = t AND event.workspace_id = a.workspace_id
             AND event.claim_id = target.claim_id
            JOIN memoriesql.semantic_evidence_links AS l
              ON l.tenant_id = t AND l.workspace_id = a.workspace_id
             AND l.owner_kind = 'claim_event' AND l.owner_id = event.claim_event_id
            JOIN memoriesql.bead_semantic_statements AS statement
              ON statement.tenant_id = t AND statement.workspace_id = a.workspace_id
             AND statement.statement_id = l.statement_id
            JOIN memoriesql.bead_semantic_statement_evidence AS x
              ON x.tenant_id = t AND x.workspace_id = a.workspace_id
             AND x.statement_id = l.statement_id AND x.evidence_source_unit_id = l.evidence_source_unit_id
            JOIN memoriesql.source_events AS statement_event
              ON statement_event.tenant_id = t AND statement_event.event_id = statement.event_id
            JOIN memoriesql.source_events AS evidence_event
              ON evidence_event.tenant_id = t AND evidence_event.event_id = x.evidence_event_id
            WHERE event.action = 'retract' AND event.recorded_at <= activated_through
            ORDER BY l.statement_id, l.evidence_source_unit_id, x.evidence_content_hash,
                statement_scope_id, statement_event_id, evidence_scope_id, x.evidence_event_id,
                statement_source_id, evidence_source_id
        LOOP
            IF NOT memoriesql.lifecycle_evidence_authorized(t, a.workspace_id, jsonb_build_object(
                'statement_id', dependency.statement_id,
                'source_unit_id', dependency.evidence_source_unit_id,
                'content_hash', dependency.evidence_content_hash))
               OR NOT memoriesql.relation_assessment_origin_event_authorized(
                   t, task, dependency.statement_scope_id, dependency.statement_event_id,
                   'memory.maintain', checked)
               OR NOT memoriesql.relation_assessment_origin_event_authorized(
                   t, task, dependency.evidence_scope_id, dependency.evidence_event_id,
                   'memory.maintain', checked)
               OR NOT memoriesql.relation_assessment_origin_source_authorized(
                   t, task, dependency.statement_source_id, checked)
               OR NOT memoriesql.relation_assessment_origin_source_authorized(
                   t, task, dependency.evidence_source_id, checked)
            THEN
                RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE = '42501';
            END IF;
            PERFORM memoriesql.revisiting_source_authorize(dependency.statement_source_id);
            IF dependency.evidence_source_id <> dependency.statement_source_id THEN
                PERFORM memoriesql.revisiting_source_authorize(dependency.evidence_source_id);
            END IF;
        END LOOP;
    END LOOP;
END;
$$;

-- Delivery maps every non-authorized recheck to a stale attempt, which hides why a
-- revision-6 task was paused. When the pause is the worker's candidate authority,
-- surface that refusal instead; any other cause keeps the stale-attempt outcome.
CREATE OR REPLACE FUNCTION memoriesql.authorize_source_delivery_v1(t uuid, task uuid, attempt uuid, generation bigint, worker text, instance text, package uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
 outcome text;
BEGIN
 outcome := memoriesql.reauthorize_semantic_task(t,task,attempt,generation,worker,instance,'hydrate',clock_timestamp());
 IF outcome <> 'authorized' THEN
  IF outcome = 'policy_paused' THEN PERFORM memoriesql.relation_candidates_authorize(t,task); END IF;
  RAISE EXCEPTION 'source_revisiting_stale_attempt' USING ERRCODE='42501'; END IF;
 PERFORM memoriesql.source_revisiting_authorize(t,task,package);
 outcome := memoriesql.reauthorize_semantic_task(t,task,attempt,generation,worker,instance,'hydrate',clock_timestamp());
 IF outcome <> 'authorized' THEN
  IF outcome = 'policy_paused' THEN PERFORM memoriesql.relation_candidates_authorize(t,task); END IF;
  RAISE EXCEPTION 'source_revisiting_stale_attempt' USING ERRCODE='42501'; END IF;
 RETURN true;
END; $$;
