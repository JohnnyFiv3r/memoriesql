-- Claim history and derived state depend on the evidence for a withdrawal.
-- A related claim's retraction can also remove a superseder or open dispute. A
-- dispute resolved before the known time no longer depends on either side.
CREATE FUNCTION memoriesql.require_claim_withdrawal_evidence_v1(
    t uuid, w uuid, inspected_bead uuid, known timestamptz
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    dependency record;
BEGIN
    FOR dependency IN
        WITH inspected_claims AS (
            SELECT c.claim_id
            FROM memoriesql.bead_claims AS c
            WHERE c.tenant_id = t AND c.workspace_id = w
              AND c.bead_id = inspected_bead AND c.recorded_at <= known
        ), withdrawal_targets AS (
            SELECT c.claim_id FROM inspected_claims AS c
            UNION
            SELECT e.related_claim_id
            FROM inspected_claims AS c
            JOIN memoriesql.bead_claim_events AS e ON e.claim_id = c.claim_id
            WHERE e.tenant_id = t AND e.workspace_id = w AND e.recorded_at <= known
              AND (e.action = 'supersede' OR (e.action = 'dispute' AND NOT EXISTS (
                    SELECT 1 FROM memoriesql.bead_claim_events AS r
                    WHERE r.tenant_id = t AND r.workspace_id = w AND r.action = 'resolve_dispute'
                      AND r.recorded_at BETWEEN e.recorded_at AND known
                      AND ((r.claim_id = e.claim_id AND r.related_claim_id = e.related_claim_id)
                        OR (r.claim_id = e.related_claim_id AND r.related_claim_id = e.claim_id)))))
            UNION
            SELECT e.claim_id
            FROM inspected_claims AS c
            JOIN memoriesql.bead_claim_events AS e ON e.related_claim_id = c.claim_id
            WHERE e.tenant_id = t AND e.workspace_id = w AND e.recorded_at <= known
              AND e.action = 'dispute' AND NOT EXISTS (
                    SELECT 1 FROM memoriesql.bead_claim_events AS r
                    WHERE r.tenant_id = t AND r.workspace_id = w AND r.action = 'resolve_dispute'
                      AND r.recorded_at BETWEEN e.recorded_at AND known
                      AND ((r.claim_id = e.claim_id AND r.related_claim_id = e.related_claim_id)
                        OR (r.claim_id = e.related_claim_id AND r.related_claim_id = e.claim_id)))
        )
        SELECT DISTINCT s.statement_id, s.access_scope_id,
            statement_event.source_object_id AS statement_source_id,
            evidence_event.source_object_id AS evidence_source_id
        FROM withdrawal_targets AS c
        JOIN memoriesql.bead_claim_events AS e
          ON e.tenant_id = t AND e.workspace_id = w AND e.claim_id = c.claim_id
        JOIN memoriesql.semantic_evidence_links AS l
          ON l.tenant_id = e.tenant_id AND l.workspace_id = e.workspace_id
         AND l.owner_kind = 'claim_event' AND l.owner_id = e.claim_event_id
        JOIN memoriesql.bead_semantic_statements AS s
          ON s.tenant_id = l.tenant_id AND s.workspace_id = l.workspace_id
         AND s.statement_id = l.statement_id
        JOIN memoriesql.bead_semantic_statement_evidence AS x
          ON x.tenant_id = l.tenant_id AND x.workspace_id = l.workspace_id
         AND x.statement_id = l.statement_id AND x.evidence_source_unit_id = l.evidence_source_unit_id
        JOIN memoriesql.source_events AS statement_event
          ON statement_event.tenant_id = s.tenant_id AND statement_event.event_id = s.event_id
        JOIN memoriesql.source_events AS evidence_event
          ON evidence_event.tenant_id = x.tenant_id AND evidence_event.event_id = x.evidence_event_id
        -- The event and its evidence commit together. Authorize all links
        -- even at a cutoff between their separately recorded timestamps.
        WHERE e.action = 'retract' AND e.recorded_at <= known
        ORDER BY s.statement_id, s.access_scope_id, statement_source_id, evidence_source_id
    LOOP
        IF NOT memoriesql.current_context_semantic_statement_authorized(
            t, w, dependency.access_scope_id, dependency.statement_id)
        THEN
            RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
        END IF;
        PERFORM memoriesql.revisiting_source_authorize(dependency.statement_source_id);
        IF dependency.evidence_source_id <> dependency.statement_source_id THEN
            PERFORM memoriesql.revisiting_source_authorize(dependency.evidence_source_id);
        END IF;
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.require_claim_withdrawal_evidence_v1(uuid, uuid, uuid, timestamptz)
    FROM PUBLIC;

-- Preserve the frame and its rollback behavior. Authorize withdrawal evidence
-- before the legacy reader can return claim history, state or its size budget.
CREATE OR REPLACE FUNCTION memoriesql.inspect_bead_relations_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    projected jsonb;
    c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    IF request->>'contract_version' <> '1' THEN
        RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE = '22023';
    END IF;
    projected := memoriesql.inspect_bead_relations_v3(
        request || jsonb_build_object('contract_version', 3, 'known_at', request->'known_at'));
    IF projected->>'outcome' <> 'available' THEN
        RETURN jsonb_build_object('contract_version', 1, 'outcome', CASE
            WHEN projected->>'outcome' = 'budget_exhausted' THEN 'budget_exhausted'
            ELSE 'unavailable' END);
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(projected->'relations') AS r(v)
        WHERE v->>'roots_status' <> 'qualified' OR v->>'kind' = 'assessed'
    ) THEN
        RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    PERFORM memoriesql.require_claim_withdrawal_evidence_v1(
        c.tenant_id, c.workspace_id, (request->>'bead_id')::uuid,
        (projected->>'known_at')::timestamptz);
    projected := memoriesql.inspect_bead_relations_v1_schema29(
        request || jsonb_build_object('known_at', projected->'known_at'));
    IF projected->>'outcome' = 'budget_exhausted' THEN
        RAISE EXCEPTION 'bead_relations_budget' USING ERRCODE = '54000';
    ELSIF projected->>'outcome' <> 'available' THEN
        RAISE EXCEPTION 'bead_relations_unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN projected;
EXCEPTION
    WHEN insufficient_privilege THEN RETURN '{"contract_version":1,"outcome":"unavailable"}';
    WHEN program_limit_exceeded THEN RETURN '{"contract_version":1,"outcome":"budget_exhausted"}';
END;
$$;
