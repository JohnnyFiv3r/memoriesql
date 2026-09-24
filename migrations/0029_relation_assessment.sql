-- Forward-only relation assessment after acceptance (schema 29).
-- Restore the pre-upgrade backup to roll back. No historical SQL/contract edits or inference.
-- Revision-6 claims, relations, coverage and their guarantees are unchanged; the
-- follow-up task has its own append-only storage beside them and never changes a bead.

-- The attestor that records what each relation provider request actually received.
-- A trusted operator provisions it, as for complete-input dispatch; no public
-- function creates one, and only its status may change, from active to revoked.
CREATE TABLE memoriesql.relation_assessment_dispatch_policies (
    tenant_id uuid NOT NULL,
    dispatch_policy_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    attestor_principal_id uuid NOT NULL,
    approved_by_principal_id uuid NOT NULL,
    qualification_evidence_sha256 text NOT NULL CHECK (qualification_evidence_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL CHECK (expires_at > created_at),
    status text NOT NULL CHECK (status IN ('active', 'revoked')),
    PRIMARY KEY (tenant_id, dispatch_policy_id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    FOREIGN KEY (tenant_id, attestor_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id),
    FOREIGN KEY (tenant_id, approved_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id)
);

-- One activation: the subject's accepted version, explicit candidates, exact
-- vocabulary revisions and the evidence units behind every pinned statement.
CREATE TABLE memoriesql.relation_assessments (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    task_id uuid NOT NULL,
    subject_bead_id uuid NOT NULL,
    subject_bead_version_id uuid NOT NULL,
    candidate_bead_ids uuid[] NOT NULL CHECK (cardinality(candidate_bead_ids) <= 8),
    beads jsonb NOT NULL CHECK (jsonb_typeof(beads) = 'array' AND jsonb_array_length(beads) BETWEEN 1 AND 9),
    relation_vocabulary jsonb NOT NULL CHECK (jsonb_typeof(relation_vocabulary) = 'array'
        AND jsonb_array_length(relation_vocabulary) BETWEEN 1 AND 32),
    evidence_units jsonb NOT NULL CHECK (jsonb_typeof(evidence_units) = 'array'
        AND jsonb_array_length(evidence_units) BETWEEN 1 AND 72),
    reconsideration jsonb,
    dispatch_policy_id uuid NOT NULL,
    reconsiders_task_id uuid,
    origin_principal_id uuid NOT NULL,
    activation_receipt_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, task_id),
    -- Each assessment is reconsidered at most once, by an explicitly linked task.
    UNIQUE (tenant_id, reconsiders_task_id),
    CHECK ((reconsiders_task_id IS NULL) = (reconsideration IS NULL)),
    CHECK (reconsiders_task_id IS DISTINCT FROM task_id),
    FOREIGN KEY (tenant_id, task_id) REFERENCES memoriesql.semantic_tasks (tenant_id, task_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, subject_bead_id, subject_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, dispatch_policy_id)
        REFERENCES memoriesql.relation_assessment_dispatch_policies (tenant_id, dispatch_policy_id),
    FOREIGN KEY (tenant_id, reconsiders_task_id) REFERENCES memoriesql.relation_assessments (tenant_id, task_id),
    FOREIGN KEY (tenant_id, origin_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id),
    FOREIGN KEY (tenant_id, activation_receipt_id) REFERENCES memoriesql.idempotency_receipts (tenant_id, idempotency_receipt_id)
);
CREATE INDEX relation_assessments_subject_idx ON memoriesql.relation_assessments (tenant_id, subject_bead_id);

-- What one provider request actually received, recorded by the policy's attestor
-- after the request, with the specialist's decision as an attributable contribution.
CREATE TABLE memoriesql.relation_assessment_deliveries (
    tenant_id uuid NOT NULL,
    request_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    lease_generation bigint NOT NULL,
    run_id text NOT NULL,
    role text NOT NULL CHECK (role IN ('author', 'specialist')),
    packet_sha256 text NOT NULL CHECK (packet_sha256 ~ '^[a-f0-9]{64}$'),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
    delivered_characters integer NOT NULL CHECK (delivered_characters BETWEEN 0 AND 131072),
    contribution jsonb,
    attestor_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, request_id),
    CHECK ((role = 'specialist') = (contribution IS NOT NULL)),
    CHECK (contribution IS NULL OR octet_length(contribution::text) <= 131072),
    FOREIGN KEY (tenant_id, request_id) REFERENCES memoriesql.model_provider_request_intents (tenant_id, request_id),
    FOREIGN KEY (tenant_id, task_id) REFERENCES memoriesql.relation_assessments (tenant_id, task_id),
    FOREIGN KEY (tenant_id, attempt_id, run_id) REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    FOREIGN KEY (tenant_id, attestor_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id)
);
CREATE INDEX relation_assessment_deliveries_attempt_idx
    ON memoriesql.relation_assessment_deliveries (tenant_id, attempt_id, role);

-- Every proposal of an applied relation task, exactly as proposed, with the
-- specialist's judgment. Only accepted proposals are assertions; an unaccepted
-- proposal keeps both contributions and never counts as a relation.
CREATE TABLE memoriesql.assessed_relations (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    author_run_id text NOT NULL,
    specialist_run_id text NOT NULL,
    specialist_request_id uuid NOT NULL,
    source_access_scope_id uuid NOT NULL,
    source_bead_id uuid NOT NULL,
    source_bead_version_id uuid NOT NULL,
    target_access_scope_id uuid NOT NULL,
    target_bead_id uuid NOT NULL,
    target_bead_version_id uuid NOT NULL,
    relation_type_revision_id uuid NOT NULL REFERENCES memoriesql.relation_type_revisions (relation_type_revision_id),
    basis text NOT NULL CHECK (basis IN ('source_stated', 'agent_inferred')),
    rationale_text text NOT NULL CHECK (btrim(rationale_text) <> '' AND char_length(rationale_text) <= 1024),
    -- Material conditions, scope and hedging of the assertion.
    qualification_text text CHECK (qualification_text IS NULL OR (btrim(qualification_text) <> '' AND char_length(qualification_text) <= 1024)),
    author_confidence numeric(3, 2) NOT NULL CHECK (author_confidence BETWEEN 0 AND 1),
    proposal jsonb NOT NULL CHECK (jsonb_typeof(proposal) = 'object' AND octet_length(proposal::text) <= 16384),
    judgment jsonb NOT NULL CHECK (jsonb_typeof(judgment) = 'object' AND octet_length(judgment::text) <= 16384),
    acceptance text NOT NULL CHECK (acceptance IN ('accepted', 'not_accepted')),
    applied_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, relation_id),
    CHECK (author_run_id <> specialist_run_id),
    FOREIGN KEY (tenant_id, task_id) REFERENCES memoriesql.relation_assessments (tenant_id, task_id),
    FOREIGN KEY (tenant_id, workspace_id, source_access_scope_id, source_bead_id, source_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, workspace_id, target_access_scope_id, target_bead_id, target_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, attempt_id, author_run_id) REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    FOREIGN KEY (tenant_id, attempt_id, specialist_run_id) REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    FOREIGN KEY (tenant_id, specialist_request_id) REFERENCES memoriesql.relation_assessment_deliveries (tenant_id, request_id),
    FOREIGN KEY (tenant_id, applied_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id)
);
CREATE INDEX assessed_relations_source_idx ON memoriesql.assessed_relations (tenant_id, source_bead_id);
CREATE INDEX assessed_relations_target_idx ON memoriesql.assessed_relations (tenant_id, target_bead_id);
CREATE INDEX assessed_relations_task_idx ON memoriesql.assessed_relations (tenant_id, task_id);

-- Endpoint and basis statements. They already exist in accepted versions; the
-- task never adds one. A basis statement carries the assertion's attribution.
CREATE TABLE memoriesql.assessed_relation_statements (
    tenant_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('source', 'target', 'basis')),
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    statement_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, relation_id, statement_id),
    FOREIGN KEY (tenant_id, relation_id) REFERENCES memoriesql.assessed_relations (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, statement_id)
        REFERENCES memoriesql.bead_semantic_statements (tenant_id, workspace_id, access_scope_id, statement_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
);

-- The author's disposition of every unordered pair of pinned beads, each bead with
-- itself included. Not assessed stays valid and is never unrelated.
CREATE TABLE memoriesql.relation_pair_dispositions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    task_id uuid NOT NULL,
    first_access_scope_id uuid NOT NULL,
    first_bead_id uuid NOT NULL,
    first_bead_version_id uuid NOT NULL,
    second_access_scope_id uuid NOT NULL,
    second_bead_id uuid NOT NULL,
    second_bead_version_id uuid NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('related', 'not_related', 'abstained', 'not_assessed')),
    abstention text CHECK (abstention IS NULL OR abstention IN ('no_fit', 'insufficient_evidence', 'ambiguous')),
    reason_text text CHECK (reason_text IS NULL OR (btrim(reason_text) <> '' AND char_length(reason_text) <= 1024)),
    attempt_id uuid NOT NULL,
    author_run_id text NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, task_id, first_bead_id, second_bead_id),
    CHECK (first_bead_id <= second_bead_id),
    CHECK ((disposition = 'abstained') = (abstention IS NOT NULL)),
    CHECK (disposition <> 'not_assessed' OR reason_text IS NOT NULL),
    FOREIGN KEY (tenant_id, task_id) REFERENCES memoriesql.relation_assessments (tenant_id, task_id),
    FOREIGN KEY (tenant_id, workspace_id, first_access_scope_id, first_bead_id, first_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, workspace_id, second_access_scope_id, second_bead_id, second_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, attempt_id, author_run_id) REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id)
);
CREATE INDEX relation_pair_dispositions_first_idx ON memoriesql.relation_pair_dispositions (tenant_id, first_bead_id);
CREATE INDEX relation_pair_dispositions_second_idx ON memoriesql.relation_pair_dispositions (tenant_id, second_bead_id);

-- An authored retirement: an accepted assertion replaced an earlier active one of
-- either kind in the same apply, for example with the opposite direction. It is
-- final, and the retired assertion no longer counts for cycles or roots.
CREATE TABLE memoriesql.relation_retirements (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    retirement_id uuid NOT NULL,
    retired_kind text NOT NULL CHECK (retired_kind IN ('authored', 'assessed')),
    retired_relation_id uuid NOT NULL,
    replacement_relation_id uuid NOT NULL,
    reason_text text NOT NULL CHECK (btrim(reason_text) <> '' AND char_length(reason_text) <= 1024),
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    author_run_id text NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, retirement_id),
    UNIQUE (tenant_id, retired_kind, retired_relation_id),
    CHECK (retired_relation_id <> replacement_relation_id),
    FOREIGN KEY (tenant_id, replacement_relation_id) REFERENCES memoriesql.assessed_relations (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, task_id) REFERENCES memoriesql.relation_assessments (tenant_id, task_id),
    FOREIGN KEY (tenant_id, attempt_id, author_run_id) REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    FOREIGN KEY (tenant_id, idempotency_receipt_id) REFERENCES memoriesql.idempotency_receipts (tenant_id, idempotency_receipt_id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id)
);
CREATE INDEX relation_retirements_replacement_idx ON memoriesql.relation_retirements (tenant_id, replacement_relation_id);

-- Assessed assertions cite exact statement-evidence pairs like authored ones.
ALTER TABLE memoriesql.semantic_evidence_links DROP CONSTRAINT semantic_evidence_links_owner_kind_check,
    ADD CONSTRAINT semantic_evidence_links_owner_kind_check
    CHECK (owner_kind IN ('relation', 'claim_event', 'relation_event', 'assessed_relation'));

CREATE FUNCTION memoriesql.guard_relation_assessment_policy() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, memoriesql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' OR to_jsonb(NEW) - 'status' IS DISTINCT FROM to_jsonb(OLD) - 'status'
       OR OLD.status <> 'active' OR NEW.status <> 'revoked' THEN
        RAISE EXCEPTION 'relation_assessment_policy_immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.guard_relation_assessment_policy() FROM PUBLIC;
CREATE TRIGGER relation_assessment_policy_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_assessment_dispatch_policies
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_relation_assessment_policy();
CREATE TRIGGER relation_assessment_policy_authority_fence BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.relation_assessment_dispatch_policies
FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER relation_assessments_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_assessments
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_assessment_deliveries_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_assessment_deliveries
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER assessed_relations_immutable BEFORE UPDATE OR DELETE ON memoriesql.assessed_relations
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER assessed_relation_statements_immutable BEFORE UPDATE OR DELETE ON memoriesql.assessed_relation_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_pair_dispositions_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_pair_dispositions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_retirements_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_retirements
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Definer functions own every read and write; roles receive no table grants.
ALTER TABLE memoriesql.relation_assessment_dispatch_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_assessment_dispatch_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_assessments FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_assessment_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_assessment_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.assessed_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.assessed_relations FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.assessed_relation_statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.assessed_relation_statements FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_pair_dispositions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_pair_dispositions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_retirements ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_retirements FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.relation_assessment_dispatch_policies, memoriesql.relation_assessments,
    memoriesql.relation_assessment_deliveries, memoriesql.assessed_relations,
    memoriesql.assessed_relation_statements, memoriesql.relation_pair_dispositions,
    memoriesql.relation_retirements FROM PUBLIC, memoriesql_application, memoriesql_worker;

COMMENT ON TABLE memoriesql.assessed_relations IS
    'Every proposal of an applied relation task with its specialist judgment. Only accepted proposals are assertions. Not causal truth; author confidence is diagnostic.';
COMMENT ON TABLE memoriesql.relation_pair_dispositions IS
    'The author''s disposition of every pinned bead pair, self-pairs included. Not assessed is never unrelated, and coverage never searches other beads.';
COMMENT ON TABLE memoriesql.relation_retirements IS
    'Authored, final retirements of earlier assertions by an accepted replacement in the same apply.';

-- One pinned accepted bead exactly as an author and specialist see it: meaning,
-- clocks, mentions, lineage and statements with their evidence pins. Revision 1
-- carries no tracked claims. Callers authorize first.
CREATE FUNCTION memoriesql.relation_assessment_bead_packet_v1(t uuid, w uuid, bead uuid, bead_role text)
RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT (memoriesql.relation_candidate_packet_v1(t, w, bead) - 'claims')
        || jsonb_build_object('role', bead_role)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_bead_packet_v1(uuid, uuid, uuid, text) FROM PUBLIC;

-- The distinct evidence units behind every pinned statement, each with how its
-- exact content is delivered: an observation unit's text, or every normalized part
-- of a complete unit's sealed package. Pins name whole units; there is no narrower
-- excerpt. A unit whose content cannot be delivered refuses the activation.
CREATE FUNCTION memoriesql.relation_evidence_units_v1(t uuid, pinned jsonb)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    unit record; u memoriesql.source_units%ROWTYPE; m memoriesql.logical_unit_materializations%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; origin uuid; result jsonb := '[]';
BEGIN
    FOR unit IN
        SELECT DISTINCT (e.v->>'source_unit_id')::uuid AS id, e.v->>'content_hash' AS content_hash
        FROM jsonb_array_elements(pinned) AS b(v), jsonb_array_elements(b.v->'statements') AS s(v),
             jsonb_array_elements(s.v->'evidence') AS e(v)
        ORDER BY 1
    LOOP
        SELECT su.* INTO u FROM memoriesql.source_units AS su WHERE su.tenant_id = t AND su.source_unit_id = unit.id;
        IF u.source_unit_id IS NULL OR u.content_hash IS DISTINCT FROM unit.content_hash THEN
            RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
        END IF;
        SELECT ev.source_object_id INTO origin FROM memoriesql.source_events AS ev
        WHERE ev.tenant_id = t AND ev.event_id = u.event_id;
        IF u.content_text IS NOT NULL THEN
            result := result || jsonb_build_array(jsonb_build_object(
                'source_unit_id', u.source_unit_id, 'content_hash', u.content_hash,
                'representation', 'observation_text', 'declared_characters', char_length(u.content_text),
                'source_object_id', origin, 'access_scope_id', u.access_scope_id, 'event_id', u.event_id,
                'package_id', NULL));
        ELSE
            SELECT lm.* INTO m FROM memoriesql.logical_unit_materializations AS lm
            WHERE lm.tenant_id = t AND lm.source_unit_id = u.source_unit_id;
            SELECT ep.* INTO p FROM memoriesql.evidence_packages AS ep
            WHERE ep.tenant_id = t AND ep.package_id = m.package_id;
            IF p.package_id IS NULL OR p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM u.content_hash THEN
                RAISE EXCEPTION 'relation_evidence_undeliverable' USING ERRCODE = '55000';
            END IF;
            result := result || jsonb_build_array(jsonb_build_object(
                'source_unit_id', u.source_unit_id, 'content_hash', u.content_hash,
                'representation', 'sealed_package', 'declared_characters', p.character_count,
                'source_object_id', p.source_object_id, 'access_scope_id', u.access_scope_id, 'event_id', u.event_id,
                'package_id', p.package_id));
        END IF;
    END LOOP;
    RETURN result;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_evidence_units_v1(uuid, jsonb) FROM PUBLIC;

-- The exact content of one pinned evidence unit, integrity-checked against its pins.
-- Callers authorize the current context over the unit's source first.
CREATE FUNCTION memoriesql.relation_evidence_content_v1(t uuid, unit jsonb)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE u memoriesql.source_units%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; parts jsonb;
BEGIN
    SELECT su.* INTO u FROM memoriesql.source_units AS su
    WHERE su.tenant_id = t AND su.source_unit_id = (unit->>'source_unit_id')::uuid;
    IF u.content_hash IS DISTINCT FROM unit->>'content_hash' THEN
        RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
    END IF;
    IF unit->>'representation' = 'observation_text' THEN
        IF u.content_text IS NULL
           OR encode(sha256(convert_to(u.content_text, 'UTF8')), 'hex') IS DISTINCT FROM u.content_hash THEN
            RAISE EXCEPTION 'relation_evidence_integrity' USING ERRCODE = 'P0002';
        END IF;
        RETURN jsonb_build_object('source_unit_id', u.source_unit_id, 'content_hash', u.content_hash,
            'representation', 'observation_text', 'content', u.content_text, 'parts', '[]'::jsonb);
    END IF;
    SELECT ep.* INTO p FROM memoriesql.evidence_packages AS ep
    WHERE ep.tenant_id = t AND ep.package_id = (unit->>'package_id')::uuid;
    IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM u.content_hash THEN
        RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('ordinal', part.ordinal,
               'content_sha256', part.inventory->>'content_sha256', 'content', part.content) ORDER BY part.ordinal), '[]'::jsonb)
    INTO parts FROM memoriesql.evidence_package_parts AS part
    WHERE part.tenant_id = t AND part.package_id = p.package_id;
    IF jsonb_array_length(parts) <> p.part_count OR p.part_count < 1 THEN
        RAISE EXCEPTION 'relation_evidence_integrity' USING ERRCODE = 'P0002';
    END IF;
    RETURN jsonb_build_object('source_unit_id', u.source_unit_id, 'content_hash', u.content_hash,
        'representation', 'sealed_package', 'content', NULL, 'parts', parts);
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_evidence_content_v1(uuid, jsonb) FROM PUBLIC;


-- The activating origin's own current authority over one scope. A relation task
-- pins beads, statements and evidence that can sit outside its own scope, so the
-- origin is checked over every scope it pins, never through the worker's or the
-- attestor's authority. An exact copy of semantic_task_origin_capability_authorized
-- with the scope as a parameter.
CREATE FUNCTION memoriesql.relation_assessment_origin_scope_authorized(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_access_scope_id uuid,
    requested_capability text,
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
         AND role_capability.capability_key = requested_capability
        JOIN memoriesql.access_scopes AS scope
          ON scope.tenant_id = task.tenant_id
         AND scope.workspace_id = task.workspace_id
         AND scope.access_scope_id = requested_access_scope_id
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
                    AND requested_capability =
                        ANY(origin_revision.allowed_capabilities)
                    AND requested_access_scope_id =
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
                            AND requested_capability = ANY(revision.allowed_capabilities)
                            AND requested_access_scope_id = ANY(revision.allowed_access_scope_ids)
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
                        AND grant_record.access_scope_id = requested_access_scope_id
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
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_origin_scope_authorized(uuid, uuid, uuid, text, timestamp with time zone) FROM PUBLIC;
-- The origin's authority over one evidence event: its source stays an active
-- protected resource and the origin holds the capability over the event's scope.
CREATE FUNCTION memoriesql.relation_assessment_origin_event_authorized(
    requested_tenant_id uuid, requested_task_id uuid, requested_access_scope_id uuid,
    requested_event_id uuid, requested_capability text, requested_checked_at timestamp with time zone
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT EXISTS (
        SELECT 1 FROM memoriesql.source_events AS event_record
        JOIN memoriesql.semantic_tasks AS task
          ON task.tenant_id = event_record.tenant_id AND task.task_id = requested_task_id
         AND task.workspace_id = event_record.workspace_id
        JOIN memoriesql.protected_resources AS resource
          ON resource.tenant_id = event_record.tenant_id AND resource.workspace_id = event_record.workspace_id
         AND resource.access_scope_id = event_record.access_scope_id AND resource.resource_kind = 'source'
         AND resource.resource_id = event_record.source_object_id AND resource.status = 'active'
        WHERE event_record.tenant_id = requested_tenant_id
          AND event_record.access_scope_id = requested_access_scope_id
          AND event_record.event_id = requested_event_id
          AND memoriesql.relation_assessment_origin_scope_authorized(requested_tenant_id, requested_task_id,
              requested_access_scope_id, requested_capability, requested_checked_at)
    )
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_origin_event_authorized(uuid, uuid, uuid, uuid, text, timestamp with time zone) FROM PUBLIC;

-- The origin's raw-source authority over one evidence source, over the source's own scope.
CREATE FUNCTION memoriesql.relation_assessment_origin_source_authorized(
    requested_tenant_id uuid, requested_task_id uuid, requested_source_object_id uuid,
    requested_checked_at timestamp with time zone
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT EXISTS (
        SELECT 1 FROM memoriesql.source_objects AS source
        JOIN memoriesql.semantic_tasks AS task
          ON task.tenant_id = source.tenant_id AND task.task_id = requested_task_id
         AND task.workspace_id = source.workspace_id
        JOIN memoriesql.protected_resources AS resource
          ON resource.tenant_id = source.tenant_id AND resource.workspace_id = source.workspace_id
         AND resource.access_scope_id = source.access_scope_id AND resource.resource_kind = 'source'
         AND resource.resource_id = source.source_object_id AND resource.status = 'active'
        WHERE source.tenant_id = requested_tenant_id AND source.source_object_id = requested_source_object_id
          AND memoriesql.relation_assessment_origin_scope_authorized(requested_tenant_id, requested_task_id,
              source.access_scope_id, 'source.raw.read', requested_checked_at)
    )
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_origin_source_authorized(uuid, uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- The origin's maintain authority over one pinned accepted bead version, its
-- statements and their evidence events. An exact copy of
-- current_context_accepted_bead_maintain_authorized with the origin's event check
-- in place of the current context's.
CREATE FUNCTION memoriesql.relation_assessment_origin_bead_authorized(
    requested_tenant_id uuid, requested_task_id uuid, requested_workspace_id uuid,
    requested_access_scope_id uuid, requested_bead_version_id uuid,
    requested_checked_at timestamp with time zone
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT EXISTS (
        SELECT 1 FROM memoriesql.accepted_bead_semantics AS accepted
        JOIN memoriesql.bead_versions AS version
          ON version.tenant_id = accepted.tenant_id
         AND version.bead_version_id = accepted.bead_version_id
        WHERE accepted.tenant_id = requested_tenant_id
          AND accepted.workspace_id = requested_workspace_id
          AND accepted.access_scope_id = requested_access_scope_id
          AND accepted.bead_version_id = requested_bead_version_id
          AND memoriesql.relation_assessment_origin_event_authorized(
              requested_tenant_id, requested_task_id, version.access_scope_id, version.event_id, 'memory.maintain', requested_checked_at
          )
          AND NOT EXISTS (
              SELECT 1 FROM memoriesql.bead_statement_revisions AS revision
              JOIN memoriesql.bead_semantic_statements AS statement
                ON statement.tenant_id = revision.tenant_id
               AND statement.bead_id = revision.bead_id
               AND statement.statement_sequence <= revision.statement_watermark
              WHERE revision.tenant_id = version.tenant_id
                AND revision.bead_version_id = version.bead_version_id
                AND (
                    NOT memoriesql.relation_assessment_origin_event_authorized(
                        requested_tenant_id, requested_task_id, statement.access_scope_id, statement.event_id, 'memory.maintain', requested_checked_at
                    ) OR NOT EXISTS (
                        SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS evidence
                        WHERE evidence.tenant_id = statement.tenant_id
                          AND evidence.statement_id = statement.statement_id
                    ) OR EXISTS (
                        SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS evidence
                        WHERE evidence.tenant_id = statement.tenant_id
                          AND evidence.statement_id = statement.statement_id
                          AND NOT memoriesql.relation_assessment_origin_event_authorized(
                              requested_tenant_id, requested_task_id, evidence.access_scope_id, evidence.evidence_event_id, 'memory.maintain', requested_checked_at
                          )
                    )
                )
          )
    );
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_origin_bead_authorized(uuid, uuid, uuid, uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- Every check a relation task's work depends on, for two principals separately.
-- The current context (worker, attestor or activator) needs maintain authority over
-- every pinned bead, statement and evidence event and raw-source authority over every
-- evidence source. The activating origin keeps the same authority over the same
-- pins, whatever the current context holds, and raw-source authority over the task's
-- scope as for complete-input execution. The attestor policy stays active. A refusal
-- is 42501 and pauses the task; beads are never touched.
CREATE FUNCTION memoriesql.relation_assessment_pins_authorize(t uuid, task uuid)
RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    x memoriesql.relation_assessments%ROWTYPE; item jsonb; accepted memoriesql.accepted_bead_semantics%ROWTYPE;
    checked timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    SELECT ra.* INTO x FROM memoriesql.relation_assessments AS ra WHERE ra.tenant_id = t AND ra.task_id = task;
    IF x.task_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM memoriesql.relation_assessment_dispatch_policies AS d
        WHERE d.tenant_id = t AND d.dispatch_policy_id = x.dispatch_policy_id AND d.workspace_id = x.workspace_id
          AND d.status = 'active' AND d.expires_at > checked) THEN
        RAISE EXCEPTION 'relation_assessment_policy_unavailable' USING ERRCODE = '42501';
    END IF;
    IF NOT memoriesql.semantic_task_origin_capability_authorized(t, task, 'source.raw.read', checked) THEN
        RAISE EXCEPTION 'relation_assessment_origin_unavailable' USING ERRCODE = '42501';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(x.beads) LOOP
        SELECT a.* INTO accepted FROM memoriesql.accepted_bead_semantics AS a
        WHERE a.tenant_id = t AND a.bead_id = (item->>'bead_id')::uuid
          AND a.bead_version_id = (item->>'bead_version_id')::uuid;
        IF accepted.bead_id IS NULL OR accepted.workspace_id <> x.workspace_id
           OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                t, accepted.workspace_id, accepted.access_scope_id, accepted.bead_version_id) THEN
            RAISE EXCEPTION 'relation_assessment_bead_unavailable' USING ERRCODE = '42501';
        END IF;
        IF NOT memoriesql.relation_assessment_origin_bead_authorized(
                t, task, accepted.workspace_id, accepted.access_scope_id, accepted.bead_version_id, checked) THEN
            RAISE EXCEPTION 'relation_assessment_origin_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
    FOR item IN SELECT value FROM jsonb_array_elements(x.evidence_units) LOOP
        IF NOT memoriesql.current_context_event_authorized(
                (item->>'access_scope_id')::uuid, (item->>'event_id')::uuid, 'memory.maintain', 'read') THEN
            RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
        END IF;
        PERFORM memoriesql.revisiting_source_authorize((item->>'source_object_id')::uuid);
        IF NOT memoriesql.relation_assessment_origin_event_authorized(
                t, task, (item->>'access_scope_id')::uuid, (item->>'event_id')::uuid, 'memory.maintain', checked)
           OR NOT memoriesql.relation_assessment_origin_source_authorized(
                t, task, (item->>'source_object_id')::uuid, checked) THEN
            RAISE EXCEPTION 'relation_assessment_origin_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_pins_authorize(uuid, uuid) FROM PUBLIC;

-- An assessed proposal's lifecycle as of a known time. An unaccepted proposal is
-- never an assertion. Retirement by an accepted replacement is final; a correction
-- of an endpoint bead after the assertion leaves it pending reassessment.
CREATE FUNCTION memoriesql.assessed_relation_state_v1(t uuid, relation uuid, known timestamp with time zone)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    WITH rel AS (
        SELECT * FROM memoriesql.assessed_relations AS r
        WHERE r.tenant_id = t AND r.relation_id = relation AND r.recorded_at <= known
    ), retired AS (
        SELECT x.replacement_relation_id AS id FROM memoriesql.relation_retirements AS x
        WHERE x.tenant_id = t AND x.retired_kind = 'assessed' AND x.retired_relation_id = relation
          AND x.recorded_at <= known
    ), corrections AS (
        SELECT s.bead_id FROM rel
        JOIN memoriesql.bead_supersessions AS s
          ON s.tenant_id = t AND s.superseded_bead_id IN (rel.source_bead_id, rel.target_bead_id)
        JOIN memoriesql.bead_versions AS v ON v.tenant_id = t AND v.bead_version_id = s.bead_version_id
        WHERE v.authored_at <= known AND v.authored_at > rel.recorded_at
    )
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM rel) THEN NULL ELSE jsonb_build_object(
        'state', CASE
            WHEN (SELECT acceptance FROM rel) = 'not_accepted' THEN 'not_accepted'
            WHEN EXISTS (SELECT 1 FROM retired) THEN 'superseded'
            WHEN EXISTS (SELECT 1 FROM corrections) THEN 'reassessment_pending'
            ELSE 'active' END,
        'superseded_by', COALESCE((SELECT jsonb_agg(id ORDER BY id) FROM retired), '[]'::jsonb),
        'endpoint_corrected_by', COALESCE((SELECT jsonb_agg(DISTINCT bead_id ORDER BY bead_id) FROM corrections), '[]'::jsonb)
    ) END
$$;
REVOKE ALL ON FUNCTION memoriesql.assessed_relation_state_v1(uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- One shared cycle check over both relation kinds. True when a fresh assertion of a
-- cycle-forbidden key, as the store stands now, is reachable back from its own target
-- statements through active assertions of the same key. Retracted, superseded,
-- retired and unaccepted assertions never count. Callers hold the per-tenant,
-- per-key lock before writing.
CREATE FUNCTION memoriesql.relation_cycle_closes_v1(t uuid, fresh jsonb)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    WITH RECURSIVE assertions AS MATERIALIZED (
        SELECT 'authored'::text AS kind, r.relation_id, tr.relation_type_id
        FROM memoriesql.bead_relations AS r
        JOIN memoriesql.relation_type_revisions AS tr ON tr.relation_type_revision_id = r.relation_type_revision_id
        WHERE r.tenant_id = t AND tr.cycle_policy = 'forbidden'
          AND NOT EXISTS (SELECT 1 FROM memoriesql.bead_relation_events AS e
                          WHERE e.tenant_id = t AND e.relation_id = r.relation_id AND e.action IN ('retract', 'supersede'))
          AND NOT EXISTS (SELECT 1 FROM memoriesql.relation_retirements AS x
                          WHERE x.tenant_id = t AND x.retired_kind = 'authored' AND x.retired_relation_id = r.relation_id)
        UNION ALL
        SELECT 'assessed'::text, r.relation_id, tr.relation_type_id
        FROM memoriesql.assessed_relations AS r
        JOIN memoriesql.relation_type_revisions AS tr ON tr.relation_type_revision_id = r.relation_type_revision_id
        WHERE r.tenant_id = t AND tr.cycle_policy = 'forbidden' AND r.acceptance = 'accepted'
          AND NOT EXISTS (SELECT 1 FROM memoriesql.relation_retirements AS x
                          WHERE x.tenant_id = t AND x.retired_kind = 'assessed' AND x.retired_relation_id = r.relation_id)
    ), endpoints AS MATERIALIZED (
        SELECT a.kind, a.relation_id, a.relation_type_id, s.endpoint AS side, s.statement_id
        FROM assertions AS a
        JOIN memoriesql.bead_relation_statements AS s ON a.kind = 'authored' AND s.tenant_id = t AND s.relation_id = a.relation_id
        UNION ALL
        SELECT a.kind, a.relation_id, a.relation_type_id, s.role, s.statement_id
        FROM assertions AS a
        JOIN memoriesql.assessed_relation_statements AS s
          ON a.kind = 'assessed' AND s.tenant_id = t AND s.relation_id = a.relation_id AND s.role IN ('source', 'target')
    ), edges AS MATERIALIZED (
        SELECT src.relation_type_id, src.statement_id AS from_statement, dst.statement_id AS to_statement
        FROM endpoints AS src
        JOIN endpoints AS dst ON dst.kind = src.kind AND dst.relation_id = src.relation_id AND dst.side = 'target'
        WHERE src.side = 'source'
    ), starts AS (
        SELECT a.kind, a.relation_id, a.relation_type_id FROM assertions AS a
        JOIN jsonb_array_elements(fresh) AS f(v) ON f.v->>'kind' = a.kind AND (f.v->>'relation_id')::uuid = a.relation_id
    ), reach(origin_kind, origin, relation_type_id, statement_id) AS (
        SELECT s.kind, s.relation_id, s.relation_type_id, e.statement_id FROM starts AS s
        JOIN endpoints AS e ON e.kind = s.kind AND e.relation_id = s.relation_id AND e.side = 'target'
        UNION
        SELECT r.origin_kind, r.origin, r.relation_type_id, e.to_statement FROM reach AS r
        JOIN edges AS e ON e.relation_type_id = r.relation_type_id AND e.from_statement = r.statement_id
    )
    SELECT EXISTS (
        SELECT 1 FROM reach AS r
        JOIN endpoints AS e ON e.kind = r.origin_kind AND e.relation_id = r.origin
         AND e.side = 'source' AND e.statement_id = r.statement_id)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_cycle_closes_v1(uuid, jsonb) FROM PUBLIC;

-- True when the pinned beads include this exact bead version and every named
-- statement belongs to it.
CREATE FUNCTION memoriesql.relation_pinned_statements_v1(pinned jsonb, bead text, version text, statements jsonb)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog AS $$
BEGIN
    IF jsonb_typeof(statements) IS DISTINCT FROM 'array' OR jsonb_array_length(statements) NOT BETWEEN 1 AND 8
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(statements) AS s(v) WHERE jsonb_typeof(s.v) IS DISTINCT FROM 'string') THEN
        RETURN false;
    END IF;
    RETURN COALESCE((SELECT count(*) = count(DISTINCT s.i) FROM jsonb_array_elements_text(statements) AS s(i))
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(pinned) AS b(v)
                   WHERE b.v->>'bead_id' = bead AND b.v->>'bead_version_id' = version
                     AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(statements) AS s(i)
                                     WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(b.v->'statements') AS p(w)
                                                       WHERE p.w->>'statement_id' = s.i))), false);
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_pinned_statements_v1(jsonb, text, text, jsonb) FROM PUBLIC;

-- Exact restatements: retirements end an authored relation, assessed derived_from
-- feeds bead-level roots and one cycle check covers both kinds.
CREATE OR REPLACE FUNCTION memoriesql.bead_relation_state_v1(t uuid, relation uuid, known timestamp with time zone)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    WITH rel AS (
        SELECT * FROM memoriesql.bead_relations WHERE tenant_id = t AND relation_id = relation AND recorded_at <= known
    ), ev AS (
        SELECT e.* FROM memoriesql.bead_relation_events AS e
        WHERE e.tenant_id = t AND e.relation_id = relation AND e.recorded_at <= known
    ), replacements AS (
        SELECT e.replacement_relation_id AS id FROM ev AS e WHERE e.action = 'supersede'
        UNION
        -- An accepted replacement retired it through a relation task; also final.
        SELECT x.replacement_relation_id FROM memoriesql.relation_retirements AS x
        WHERE x.tenant_id = t AND x.retired_kind = 'authored' AND x.retired_relation_id = relation
          AND x.recorded_at <= known
    ), corrections AS (
        SELECT s.bead_id, v.authored_at FROM rel
        JOIN memoriesql.bead_supersessions AS s
          ON s.tenant_id = t AND s.superseded_bead_id IN (rel.source_bead_id, rel.target_bead_id)
        JOIN memoriesql.bead_versions AS v ON v.tenant_id = t AND v.bead_version_id = s.bead_version_id
        WHERE v.authored_at <= known
    ), open_dispute AS (
        SELECT 1 FROM ev AS d WHERE d.action = 'dispute' AND NOT EXISTS (
            SELECT 1 FROM ev AS c WHERE c.action = 'confirm' AND c.recorded_at >= d.recorded_at)
    ), pending AS (
        SELECT 1 FROM corrections AS c WHERE NOT EXISTS (
            SELECT 1 FROM ev AS e WHERE e.action IN ('confirm', 'retract', 'supersede')
              AND e.recorded_at >= c.authored_at)
    )
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM rel) THEN NULL ELSE jsonb_build_object(
        'state', CASE
            WHEN EXISTS (SELECT 1 FROM ev WHERE action = 'retract') THEN 'retracted'
            WHEN EXISTS (SELECT 1 FROM replacements) THEN 'superseded'
            WHEN EXISTS (SELECT 1 FROM open_dispute) THEN 'disputed'
            WHEN EXISTS (SELECT 1 FROM pending) THEN 'reassessment_pending'
            ELSE 'active' END,
        'superseded_by', COALESCE((SELECT jsonb_agg(id ORDER BY id) FROM replacements), '[]'::jsonb),
        'endpoint_corrected_by', COALESCE((SELECT jsonb_agg(DISTINCT bead_id) FROM corrections), '[]'::jsonb)
    ) END
$$;
CREATE OR REPLACE FUNCTION memoriesql.derived_from_targets(t uuid, bead uuid, known timestamp with time zone)
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT r.target_bead_id FROM memoriesql.bead_relations AS r
    JOIN memoriesql.relation_type_revisions AS tr ON tr.relation_type_revision_id = r.relation_type_revision_id
    WHERE r.tenant_id = t AND r.source_bead_id = bead AND r.recorded_at <= known
      AND tr.relation_type_id = '30000000-0000-4000-8000-000000000009'
      AND memoriesql.bead_relation_state_v1(t, r.relation_id, known)->>'state' NOT IN ('retracted', 'superseded')
    UNION
    -- Accepted assessed derived_from between different beads feeds the same bead-level
    -- roots; statement-level roots within one bead are not computed.
    SELECT r.target_bead_id FROM memoriesql.assessed_relations AS r
    JOIN memoriesql.relation_type_revisions AS tr ON tr.relation_type_revision_id = r.relation_type_revision_id
    WHERE r.tenant_id = t AND r.source_bead_id = bead AND r.target_bead_id <> bead AND r.recorded_at <= known
      AND tr.relation_type_id = '30000000-0000-4000-8000-000000000009'
      AND memoriesql.assessed_relation_state_v1(t, r.relation_id, known)->>'state'
          NOT IN ('not_accepted', 'superseded', 'retracted')
$$;
CREATE OR REPLACE FUNCTION memoriesql.apply_authored_relations_v1(
    t uuid, w uuid, scope uuid, task uuid, attempt uuid, authored_bead uuid, authored_version uuid,
    receipt uuid, payload jsonb, run_ref text, principal uuid, at timestamp with time zone
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    x memoriesql.complete_input_executions%ROWTYPE;
    rel jsonb; cand jsonb; item jsonb; claim jsonb; upd jsonb; ref jsonb;
    statement uuid; unit uuid; claim_event uuid; type_revision uuid; named uuid[];
    candidate_scope uuid; candidate_version uuid; forbidden uuid;
    uuid_pattern constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    SELECT * INTO x FROM memoriesql.complete_input_executions WHERE tenant_id = t AND execution_task_id = task;
    IF x.execution_contract_revision IS DISTINCT FROM 6
       OR payload - ARRAY['annotations', 'relations', 'candidate_assessments', 'claims', 'claim_updates'] <> '{}'::jsonb
       OR jsonb_typeof(payload->'relations') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'relations') > 16
       OR jsonb_typeof(payload->'candidate_assessments') IS DISTINCT FROM 'array'
       OR jsonb_typeof(payload->'claims') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'claims') > 16
       OR jsonb_typeof(payload->'claim_updates') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'claim_updates') > 16
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'relations') AS r(v)
                  WHERE COALESCE(v->>'relation_id', '') !~ uuid_pattern OR COALESCE(v->>'candidate_bead_id', '') !~ uuid_pattern
                     OR jsonb_typeof(v->'authored_statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_typeof(v->'candidate_statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_typeof(v->'evidence') IS DISTINCT FROM 'array'
                     OR jsonb_array_length(v->'authored_statement_ids') NOT BETWEEN 1 AND 8
                     OR jsonb_array_length(v->'candidate_statement_ids') NOT BETWEEN 1 AND 8
                     OR jsonb_array_length(v->'evidence') NOT BETWEEN 1 AND 8
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'authored_statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'candidate_statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)
                     OR (SELECT count(*) <> count(DISTINCT i) FROM jsonb_array_elements_text(v->'authored_statement_ids') AS s(i))
                     OR (SELECT count(*) <> count(DISTINCT i) FROM jsonb_array_elements_text(v->'candidate_statement_ids') AS s(i)))
       OR (SELECT count(*) <> count(DISTINCT v->>'relation_id') FROM jsonb_array_elements(payload->'relations') AS r(v))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'claims') AS k(v)
                  WHERE COALESCE(v->>'claim_id', '') !~ uuid_pattern
                     OR jsonb_typeof(v->'statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_array_length(v->'statement_ids') NOT BETWEEN 1 AND 8
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)
                     OR (SELECT count(*) <> count(DISTINCT i) FROM jsonb_array_elements_text(v->'statement_ids') AS s(i))
                     OR (jsonb_typeof(v->'subject_mention_id') = 'string' AND v->>'subject_mention_id' !~ uuid_pattern))
       OR (SELECT count(*) <> count(DISTINCT v->>'claim_id') FROM jsonb_array_elements(payload->'claims') AS k(v))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'claim_updates') AS u(v)
                  WHERE COALESCE(v->>'target_claim_id', '') !~ uuid_pattern
                     OR (jsonb_typeof(v->'related_claim_id') = 'string' AND v->>'related_claim_id' !~ uuid_pattern)
                     OR jsonb_typeof(v->'basis_statement_ids') IS DISTINCT FROM 'array'
                     OR jsonb_array_length(v->'basis_statement_ids') NOT BETWEEN 1 AND 8
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v->'basis_statement_ids') AS s(i) WHERE s.i !~ uuid_pattern)) THEN
        RAISE EXCEPTION 'authored_relations_invalid' USING ERRCODE = '22023';
    END IF;
    -- Fresh identifiers only; a collision is an authored error, never an adopted row.
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'relations') AS r(v)
               JOIN memoriesql.bead_relations AS existing ON existing.tenant_id = t AND existing.relation_id = (v->>'relation_id')::uuid)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'claims') AS k(v)
                  JOIN memoriesql.bead_claims AS existing ON existing.tenant_id = t AND existing.claim_id = (v->>'claim_id')::uuid) THEN
        RAISE EXCEPTION 'authored_identifier_conflict' USING ERRCODE = '22023';
    END IF;
    -- Every pinned candidate is assessed exactly once.
    IF (SELECT COALESCE(jsonb_agg(v->>'candidate_bead_id' ORDER BY v->>'candidate_bead_id'), '[]'::jsonb)
        FROM jsonb_array_elements(payload->'candidate_assessments') AS a(v))
       IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(v->>'bead_id' ORDER BY v->>'bead_id'), '[]'::jsonb)
                         FROM jsonb_array_elements(x.relation_candidates) AS c(v)) THEN
        RAISE EXCEPTION 'relation_candidate_coverage_incomplete' USING ERRCODE = '22023';
    END IF;

    -- Cycle-forbidden keys are serialized per tenant and key before any write, so
    -- concurrent bundles cannot close a cycle together (locks in a fixed order).
    FOR forbidden IN
        SELECT DISTINCT r.relation_type_id
        FROM jsonb_array_elements(payload->'relations') AS p(v)
        JOIN memoriesql.relation_types AS ty ON ty.type_key = p.v#>>'{relation_type,key}'
         AND (ty.tenant_id IS NULL OR (ty.tenant_id = t AND ty.workspace_id = w))
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
         AND r.revision = (p.v#>>'{relation_type,revision}')::integer AND r.cycle_policy = 'forbidden'
        ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            t::text || ':relation-cycle:' || forbidden::text, 0));
    END LOOP;

    FOR rel IN SELECT value FROM jsonb_array_elements(payload->'relations') ORDER BY value->>'relation_id' LOOP
        cand := (SELECT v FROM jsonb_array_elements(x.relation_candidates) AS c(v) WHERE v->>'bead_id' = rel->>'candidate_bead_id');
        IF cand IS NULL THEN
            RAISE EXCEPTION 'relation_candidate_not_pinned' USING ERRCODE = '22023';
        END IF;
        SELECT access_scope_id, bead_version_id INTO candidate_scope, candidate_version FROM memoriesql.accepted_bead_semantics
        WHERE tenant_id = t AND bead_id = (cand->>'bead_id')::uuid AND bead_version_id = (cand->>'bead_version_id')::uuid;
        IF jsonb_typeof(rel->'relation_type') IS DISTINCT FROM 'object'
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.relation_vocabulary) AS d(v)
                          WHERE v->>'key' = rel#>>'{relation_type,key}' AND v->'revision' = rel#>'{relation_type,revision}') THEN
            RAISE EXCEPTION 'unknown_relation_type' USING ERRCODE = '22023';
        END IF;
        SELECT r.relation_type_revision_id INTO type_revision FROM memoriesql.relation_types AS ty
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
        WHERE ty.type_key = rel#>>'{relation_type,key}' AND r.revision = (rel#>>'{relation_type,revision}')::integer
          AND (ty.tenant_id IS NULL OR (ty.tenant_id = t AND ty.workspace_id = w));
        IF type_revision IS NULL OR rel->>'basis' IS NULL OR rel->>'basis' NOT IN ('source_stated', 'agent_inferred')
           OR rel->>'direction' IS NULL OR rel->>'direction' NOT IN ('from_authored', 'to_authored')
           OR jsonb_typeof(rel->'rationale') IS DISTINCT FROM 'string' OR btrim(rel->>'rationale') = '' OR char_length(rel->>'rationale') > 1024
           OR jsonb_typeof(rel->'qualification') NOT IN ('string', 'null')
           OR (jsonb_typeof(rel->'qualification') = 'string' AND (btrim(rel->>'qualification') = '' OR char_length(rel->>'qualification') > 1024))
           OR jsonb_typeof(rel->'author_confidence') IS DISTINCT FROM 'number'
           OR (rel->>'author_confidence')::numeric NOT BETWEEN 0 AND 1
           OR round((rel->>'author_confidence')::numeric, 2) <> (rel->>'author_confidence')::numeric THEN
            RAISE EXCEPTION 'authored_relations_invalid' USING ERRCODE = '22023';
        END IF;
        -- Endpoint propositions: statements of the authored version and of the pinned candidate packet.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(rel->'authored_statement_ids') AS s(i)
                   WHERE NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                     WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = authored_version)) THEN
            RAISE EXCEPTION 'relation_statement_not_authored' USING ERRCODE = '22023';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(rel->'candidate_statement_ids') AS s(i)
                   WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(cand->'statements') AS p(v) WHERE p.v->>'statement_id' = s.i)
                      OR NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                     WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = candidate_version)) THEN
            RAISE EXCEPTION 'relation_statement_not_pinned' USING ERRCODE = '22023';
        END IF;
        named := ARRAY(SELECT i::uuid FROM jsonb_array_elements_text(rel->'authored_statement_ids') AS s(i))
              || ARRAY(SELECT i::uuid FROM jsonb_array_elements_text(rel->'candidate_statement_ids') AS s(i));
        -- Evidence must already support one of the relation's named propositions.
        FOR ref IN SELECT value FROM jsonb_array_elements(rel->'evidence') LOOP
            IF jsonb_typeof(ref) IS DISTINCT FROM 'object' OR ref - ARRAY['source_unit_id', 'content_hash'] <> '{}'::jsonb
               OR COALESCE(ref->>'source_unit_id', '') !~ uuid_pattern OR COALESCE(ref->>'content_hash', '') !~ '^[a-f0-9]{64}$'
               OR NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS ev
                              WHERE ev.tenant_id = t AND ev.statement_id = ANY(named)
                                AND ev.evidence_source_unit_id = (ref->>'source_unit_id')::uuid
                                AND ev.evidence_content_hash = ref->>'content_hash') THEN
                RAISE EXCEPTION 'relation_evidence_unbound' USING ERRCODE = '22023';
            END IF;
        END LOOP;
        IF (SELECT count(*) <> count(DISTINCT (v->>'source_unit_id', v->>'content_hash'))
            FROM jsonb_array_elements(rel->'evidence') AS r(v)) THEN
            RAISE EXCEPTION 'authored_relations_invalid' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.bead_relations (
            tenant_id, workspace_id, relation_id, source_access_scope_id, source_bead_id, source_bead_version_id,
            target_access_scope_id, target_bead_id, target_bead_version_id, relation_type_revision_id, basis,
            rationale_text, qualification_text, author_confidence, authoring_bead_id, semantic_task_id,
            semantic_attempt_id, semantic_run_id, authored_by_principal_id, recorded_at
        ) SELECT
            t, w, (rel->>'relation_id')::uuid,
            CASE WHEN rel->>'direction' = 'from_authored' THEN scope ELSE candidate_scope END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN authored_bead ELSE (cand->>'bead_id')::uuid END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN authored_version ELSE candidate_version END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN candidate_scope ELSE scope END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN (cand->>'bead_id')::uuid ELSE authored_bead END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN candidate_version ELSE authored_version END,
            type_revision, rel->>'basis', rel->>'rationale', NULLIF(rel->'qualification', 'null'::jsonb) #>> '{}',
            (rel->>'author_confidence')::numeric, authored_bead, task, attempt, run_ref, principal, at;
        INSERT INTO memoriesql.bead_relation_statements (tenant_id, relation_id, endpoint, workspace_id, access_scope_id, statement_id)
        SELECT t, (rel->>'relation_id')::uuid,
               CASE WHEN rel->>'direction' = 'from_authored' THEN 'source' ELSE 'target' END, w, scope, i::uuid
        FROM jsonb_array_elements_text(rel->'authored_statement_ids') AS s(i)
        UNION ALL
        SELECT t, (rel->>'relation_id')::uuid,
               CASE WHEN rel->>'direction' = 'from_authored' THEN 'target' ELSE 'source' END, w, candidate_scope, i::uuid
        FROM jsonb_array_elements_text(rel->'candidate_statement_ids') AS s(i);
        FOR statement, unit IN
            SELECT DISTINCT ev.statement_id, ev.evidence_source_unit_id
            FROM jsonb_array_elements(rel->'evidence') AS r(v)
            JOIN memoriesql.bead_semantic_statement_evidence AS ev
              ON ev.tenant_id = t AND ev.statement_id = ANY(named)
             AND ev.evidence_source_unit_id = (r.v->>'source_unit_id')::uuid
             AND ev.evidence_content_hash = r.v->>'content_hash'
            ORDER BY 1, 2
        LOOP
            PERFORM memoriesql.record_semantic_evidence_link(t, w, 'relation', (rel->>'relation_id')::uuid,
                'supports', statement, unit, at);
        END LOOP;
    END LOOP;

    -- An assertion never references itself, and no cycle-forbidden key forms a cycle
    -- between statements among its active assertions once this write commits.
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(payload->'relations') AS p(v)
        JOIN memoriesql.bead_relation_statements AS source_side
          ON source_side.tenant_id = t AND source_side.relation_id = (p.v->>'relation_id')::uuid AND source_side.endpoint = 'source'
        JOIN memoriesql.bead_relation_statements AS target_side
          ON target_side.tenant_id = t AND target_side.relation_id = source_side.relation_id AND target_side.endpoint = 'target'
         AND target_side.statement_id = source_side.statement_id
    ) THEN
        RAISE EXCEPTION 'relation_self_reference' USING ERRCODE = '22023';
    END IF;
    -- The shared check covers both relation kinds, so an assessed assertion counts too.
    IF memoriesql.relation_cycle_closes_v1(t, (
        SELECT COALESCE(jsonb_agg(jsonb_build_object('kind', 'authored', 'relation_id', p.v->>'relation_id')), '[]'::jsonb)
        FROM jsonb_array_elements(payload->'relations') AS p(v))) THEN
        RAISE EXCEPTION 'relation_cycle_forbidden' USING ERRCODE = '22023';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(payload->'candidate_assessments') ORDER BY value->>'candidate_bead_id' LOOP
        cand := (SELECT v FROM jsonb_array_elements(x.relation_candidates) AS c(v) WHERE v->>'bead_id' = item->>'candidate_bead_id');
        SELECT access_scope_id, bead_version_id INTO candidate_scope, candidate_version FROM memoriesql.accepted_bead_semantics
        WHERE tenant_id = t AND bead_id = (cand->>'bead_id')::uuid AND bead_version_id = (cand->>'bead_version_id')::uuid;
        IF item->>'assessment' IS NULL OR item->>'assessment' NOT IN ('edge', 'no_edge', 'unassessed')
           OR (item->>'assessment' = 'edge') IS DISTINCT FROM EXISTS (
                SELECT 1 FROM jsonb_array_elements(payload->'relations') AS r(v) WHERE r.v->>'candidate_bead_id' = item->>'candidate_bead_id')
           OR jsonb_typeof(item->'reason') NOT IN ('string', 'null')
           OR (jsonb_typeof(item->'reason') = 'string' AND (btrim(item->>'reason') = '' OR char_length(item->>'reason') > 1024))
           OR (item->>'assessment' = 'unassessed' AND jsonb_typeof(item->'reason') IS DISTINCT FROM 'string') THEN
            RAISE EXCEPTION 'relation_candidate_coverage_invalid' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.relation_candidate_assessments (
            tenant_id, workspace_id, access_scope_id, authoring_bead_id, authoring_bead_version_id,
            candidate_access_scope_id, candidate_bead_id, candidate_bead_version_id, assessment, reason_text,
            semantic_task_id, semantic_attempt_id, semantic_run_id, recorded_at
        ) VALUES (
            t, w, scope, authored_bead, authored_version, candidate_scope, (cand->>'bead_id')::uuid,
            candidate_version, item->>'assessment', NULLIF(item->'reason', 'null'::jsonb) #>> '{}', task, attempt, run_ref, at
        );
    END LOOP;

    FOR claim IN SELECT value FROM jsonb_array_elements(payload->'claims') ORDER BY value->>'claim_id' LOOP
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(claim->'statement_ids') AS s(i)
                   WHERE NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                     WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = authored_version))
           OR (jsonb_typeof(claim->'subject_mention_id') = 'string' AND NOT EXISTS (
                SELECT 1 FROM memoriesql.entity_mentions AS m
                WHERE m.tenant_id = t AND m.entity_mention_id = (claim->>'subject_mention_id')::uuid
                  AND m.bead_version_id = authored_version))
           OR jsonb_typeof(claim->'subject_mention_id') NOT IN ('string', 'null')
           OR jsonb_typeof(claim->'subject') IS DISTINCT FROM 'string' OR btrim(claim->>'subject') = '' OR char_length(claim->>'subject') > 256
           OR jsonb_typeof(claim->'slot') IS DISTINCT FROM 'string' OR btrim(claim->>'slot') = '' OR char_length(claim->>'slot') > 128
           OR jsonb_typeof(claim->'value') IS DISTINCT FROM 'string' OR btrim(claim->>'value') = '' OR char_length(claim->>'value') > 1024
           OR jsonb_typeof(claim->'applicability') NOT IN ('string', 'null')
           OR (jsonb_typeof(claim->'applicability') = 'string'
               AND (btrim(claim->>'applicability') = '' OR char_length(claim->>'applicability') > 1024)) THEN
            RAISE EXCEPTION 'authored_claim_invalid' USING ERRCODE = '22023';
        END IF;
        INSERT INTO memoriesql.bead_claims (
            tenant_id, workspace_id, access_scope_id, claim_id, bead_id, bead_version_id, subject_text,
            subject_entity_mention_id, slot_text, value_text, applicability_text, semantic_task_id,
            semantic_attempt_id, semantic_run_id, authored_by_principal_id, recorded_at
        ) VALUES (
            t, w, scope, (claim->>'claim_id')::uuid, authored_bead, authored_version, claim->>'subject',
            (NULLIF(claim->'subject_mention_id', 'null'::jsonb) #>> '{}')::uuid, claim->>'slot', claim->>'value',
            NULLIF(claim->'applicability', 'null'::jsonb) #>> '{}', task, attempt, run_ref, principal, at
        );
        INSERT INTO memoriesql.bead_claim_statements (tenant_id, workspace_id, access_scope_id, claim_id, statement_id)
        SELECT t, w, scope, (claim->>'claim_id')::uuid, i::uuid FROM jsonb_array_elements_text(claim->'statement_ids') AS s(i);
    END LOOP;

    FOR upd IN SELECT value FROM jsonb_array_elements(payload->'claim_updates')
               ORDER BY value->>'target_claim_id', value->>'action', value->>'related_claim_id' LOOP
        -- The author saw only pinned candidates' claims; the target must be one of them.
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(x.relation_candidates) AS c(v),
                                     jsonb_array_elements(c.v->'claims') AS k(v2)
                       WHERE k.v2->>'claim_id' = upd->>'target_claim_id')
           OR upd->>'action' IS NULL OR upd->>'action' NOT IN ('supersede', 'dispute', 'reaffirm')
           OR (upd->>'action' = 'reaffirm') IS DISTINCT FROM (jsonb_typeof(upd->'related_claim_id') IS DISTINCT FROM 'string')
           OR (jsonb_typeof(upd->'related_claim_id') = 'string' AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(payload->'claims') AS k(v) WHERE k.v->>'claim_id' = upd->>'related_claim_id'))
           OR jsonb_typeof(upd->'reason') IS DISTINCT FROM 'string' OR btrim(upd->>'reason') = '' OR char_length(upd->>'reason') > 1024
           OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(upd->'basis_statement_ids') AS s(i)
                      WHERE NOT EXISTS (SELECT 1 FROM memoriesql.bead_semantic_statements AS st
                                        WHERE st.tenant_id = t AND st.statement_id = s.i::uuid AND st.bead_version_id = authored_version)) THEN
            RAISE EXCEPTION 'claim_update_invalid' USING ERRCODE = '22023';
        END IF;
        claim_event := pg_catalog.uuidv7();
        INSERT INTO memoriesql.bead_claim_events (
            tenant_id, workspace_id, claim_event_id, claim_id, action, related_claim_id, reason, origin,
            authoring_bead_id, authoring_bead_version_id, semantic_task_id, semantic_attempt_id, semantic_run_id,
            idempotency_receipt_id, recorded_by_principal_id, effective_at, recorded_at
        ) VALUES (
            t, w, claim_event, (upd->>'target_claim_id')::uuid, upd->>'action',
            (NULLIF(upd->'related_claim_id', 'null'::jsonb) #>> '{}')::uuid,
            upd->>'reason', 'authored', authored_bead, authored_version, task, attempt, run_ref, receipt, principal, NULL, at
        );
        FOR statement, unit IN
            SELECT ev.statement_id, ev.evidence_source_unit_id FROM memoriesql.bead_semantic_statement_evidence AS ev
            WHERE ev.tenant_id = t
              AND ev.statement_id IN (SELECT i::uuid FROM jsonb_array_elements_text(upd->'basis_statement_ids') AS s(i))
            ORDER BY 1, 2
        LOOP
            PERFORM memoriesql.record_semantic_evidence_link(t, w, 'claim_event', claim_event, 'supports', statement, unit, at);
        END LOOP;
    END LOOP;
END;
$$;

-- Separately authorized activation of one relation task. It pins the subject's
-- accepted version, explicitly supplied candidates' accepted versions, exact latest
-- active relation-type revisions and the evidence behind every pinned statement,
-- then enqueues the task. A different request under the same key conflicts. The
-- activator needs maintain authority over every pinned bead, write authority over the
-- subject's scope and raw-source authority over every evidence source, and must keep
-- it: every later check repeats it for the activating origin as well as for the
-- worker or attestor. A reconsideration carries the earlier task's recorded
-- disagreement and pins the same beads and vocabulary.
CREATE FUNCTION memoriesql.activate_relation_assessment_v1(request jsonb) RETURNS jsonb
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
       OR request->>'contract_version' IS DISTINCT FROM '1' OR request->>'expected_schema_version' IS DISTINCT FROM '29'
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
    WHERE r.tenant_id = c.tenant_id AND r.operation_kind = 'relation_assessment.activate.v1'
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
        -- Bounded: an applied assessment of the same pins, never itself a reconsideration,
        -- reconsidered at most once and only for proposals left unaccepted.
        IF prior.task_id IS NULL OR prior.workspace_id <> c.workspace_id OR prior.reconsiders_task_id IS NOT NULL
           OR prior.subject_bead_id <> subject.bead_id OR prior.beads IS DISTINCT FROM pinned
           OR prior.relation_vocabulary IS DISTINCT FROM vocabulary
           OR NOT EXISTS (SELECT 1 FROM memoriesql.semantic_tasks AS q
                          WHERE q.tenant_id = c.tenant_id AND q.task_id = prior.task_id AND q.status = 'succeeded')
           OR EXISTS (SELECT 1 FROM memoriesql.relation_assessments AS ra
                      WHERE ra.tenant_id = c.tenant_id AND ra.reconsiders_task_id = prior.task_id)
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
    VALUES (c.tenant_id, c.workspace_id, subject.access_scope_id, rid, 'relation_assessment.activate.v1',
        request->>'idempotency_key', request_hash, 'in_progress', 'semantic_task', tid, 1, started, started);
    input := jsonb_build_object('task_id', tid, 'task_kind', 'memory.semantic.assess-relations', 'contract_revision', 1,
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
        'relation-assessment.v1:' || tid::text, 'memoriesql.kernel', 'memory.semantic.assess-relations', 1,
        '804fb7f351db5924cbe1409391965fcc23899a06761f56613ef6786141260373', 'semantic-tasks-v1:a331612010e79e75b7fe8851d322b462b0e575c85103259e5a383aeaadb5ca89', subject.bead_id::text, 0, input,
        input#>>'{evidence_manifest,manifest_id}', subject.access_scope_id, started, NULL, started) AS q;
    INSERT INTO memoriesql.relation_assessments (tenant_id, workspace_id, access_scope_id, task_id, subject_bead_id,
        subject_bead_version_id, candidate_bead_ids, beads, relation_vocabulary, evidence_units, reconsideration,
        dispatch_policy_id, reconsiders_task_id, origin_principal_id, activation_receipt_id, created_at)
    VALUES (c.tenant_id, c.workspace_id, subject.access_scope_id, tid, subject.bead_id, subject.bead_version_id,
        candidates, pinned, vocabulary, units, reconsideration, (request->>'dispatch_policy_id')::uuid,
        prior.task_id, c.principal_id, rid, started);
    result := jsonb_build_object('contract_version', 1, 'task_id', tid, 'subject_bead_id', subject.bead_id,
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
REVOKE ALL ON FUNCTION memoriesql.activate_relation_assessment_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.activate_relation_assessment_v1(jsonb) TO memoriesql_application;

-- The worker's fenced read of every pinned evidence unit's exact content, after
-- rechecking the task fence and every pinned authority. Never truncated: over the
-- bound it is refused. Reading creates no exposure; the attestor records delivery.
CREATE FUNCTION memoriesql.read_relation_assessment_evidence_v1(
    t uuid, task uuid, attempt uuid, generation bigint, worker text, instance text
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE x memoriesql.relation_assessments%ROWTYPE; item jsonb; excerpt jsonb; result jsonb := '[]'; delivered bigint := 0;
BEGIN
    IF memoriesql.reauthorize_semantic_task(t, task, attempt, generation, worker, instance, 'hydrate',
            pg_catalog.clock_timestamp()) <> 'authorized' THEN
        RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT ra.* INTO x FROM memoriesql.relation_assessments AS ra WHERE ra.tenant_id = t AND ra.task_id = task;
    FOR item IN SELECT value FROM jsonb_array_elements(x.evidence_units) ORDER BY value->>'source_unit_id' LOOP
        excerpt := memoriesql.relation_evidence_content_v1(t, item);
        delivered := delivered + COALESCE(char_length(excerpt->>'content'), 0)
            + COALESCE((SELECT sum(char_length(p.v->>'content')) FROM jsonb_array_elements(excerpt->'parts') AS p(v)), 0);
        result := result || jsonb_build_array(excerpt);
    END LOOP;
    IF delivered > 131072 THEN
        RAISE EXCEPTION 'relation_evidence_budget' USING ERRCODE = '54000';
    END IF;
    IF memoriesql.reauthorize_semantic_task(t, task, attempt, generation, worker, instance, 'hydrate',
            pg_catalog.clock_timestamp()) <> 'authorized' THEN
        RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.read_relation_assessment_evidence_v1(uuid, uuid, uuid, bigint, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_relation_assessment_evidence_v1(uuid, uuid, uuid, bigint, text, text) TO memoriesql_worker;

-- Immediately before each provider dispatch: the fence is live, the task is not
-- cancelled and every pinned authority still holds.
CREATE FUNCTION memoriesql.authorize_relation_delivery_v1(
    t uuid, task uuid, attempt uuid, generation bigint, worker text, instance text
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
BEGIN
    IF memoriesql.reauthorize_semantic_task(t, task, attempt, generation, worker, instance, 'hydrate',
            pg_catalog.clock_timestamp()) <> 'authorized' THEN
        RAISE EXCEPTION 'relation_delivery_unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN true;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.authorize_relation_delivery_v1(uuid, uuid, uuid, bigint, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.authorize_relation_delivery_v1(uuid, uuid, uuid, bigint, text, text) TO memoriesql_worker;

-- True when a judgment has the specialist contract's exact shape.
CREATE FUNCTION memoriesql.relation_judgment_valid_v1(j jsonb, vocabulary jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, memoriesql AS $$
    -- A missing or malformed field makes the whole judgment invalid, never unknown.
    SELECT COALESCE(jsonb_typeof(j) = 'object'
       AND j - ARRAY['proposal_id', 'outcome', 'consistent', 'warranted', 'abstention', 'rationale'] = '{}'::jsonb
       AND COALESCE(j->>'proposal_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false)
       AND jsonb_typeof(j->'rationale') = 'string' AND btrim(j->>'rationale') <> '' AND char_length(j->>'rationale') <= 2048
       AND jsonb_typeof(j->'warranted') = 'array' AND jsonb_array_length(j->'warranted') <= 32
       AND COALESCE(CASE j->>'outcome'
            WHEN 'assessed' THEN jsonb_typeof(j->'consistent') = 'boolean' AND j->'abstention' = 'null'::jsonb
            WHEN 'abstained' THEN j->'consistent' = 'null'::jsonb AND jsonb_array_length(j->'warranted') = 0
                 AND j->>'abstention' IN ('no_fit', 'insufficient_evidence', 'ambiguous')
            END, false)
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(j->'warranted') AS w(v)
                       WHERE jsonb_typeof(w.v) IS DISTINCT FROM 'object'
                          OR w.v - ARRAY['relation_type', 'direction'] <> '{}'::jsonb
                          OR COALESCE(w.v->>'direction', '') NOT IN ('as_proposed', 'reversed')
                          OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(vocabulary) AS d(v)
                                         WHERE d.v->>'key' = w.v#>>'{relation_type,key}'
                                           AND d.v->'revision' = w.v#>'{relation_type,revision}'
                                           AND (w.v->'relation_type') - ARRAY['key', 'revision'] = '{}'::jsonb))
       AND (SELECT count(*) = count(DISTINCT (w.v#>>'{relation_type,key}', w.v->>'direction'))
            FROM jsonb_array_elements(j->'warranted') AS w(v)), false)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_judgment_valid_v1(jsonb, jsonb) FROM PUBLIC;

-- The attestor records what one provider request actually received: the exact
-- pinned beads, definitions and evidence, re-derived from storage, and for the
-- specialist the proposals and its decision as an attributable contribution. An
-- incomplete batch is refused; an oversized one is refused, never truncated.
CREATE FUNCTION memoriesql.record_relation_delivery_v1(request_id uuid, payload_hash text, packet jsonb, decision jsonb)
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
       OR x.task_id IS NULL OR q.task_kind IS DISTINCT FROM 'memory.semantic.assess-relations' OR q.contract_revision <> 1
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
                                       OR run.agent_key IS DISTINCT FROM 'memory.semantic.relation-author'))
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
REVOKE ALL ON FUNCTION memoriesql.record_relation_delivery_v1(uuid, text, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_relation_delivery_v1(uuid, text, jsonb, jsonb) TO memoriesql_worker;

-- Acceptance requires the author's recorded delivery of the full pinned packet for
-- the attempt's root run, and any supervised usage still within its reported stops.
CREATE FUNCTION memoriesql.relation_assessment_exposure_valid(t uuid, task uuid, attempt uuid, generation bigint)
RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    -- Answers only within the caller's own tenant.
    SELECT COALESCE(t = (SELECT c.tenant_id FROM memoriesql.current_authorization_context() AS c), false)
       AND memoriesql.supervised_acceptance_valid(t, task) AND EXISTS (
        SELECT 1 FROM memoriesql.relation_assessment_deliveries AS d
        JOIN memoriesql.semantic_task_runs AS r ON r.tenant_id = d.tenant_id AND r.attempt_id = d.attempt_id AND r.run_id = d.run_id
        WHERE d.tenant_id = t AND d.task_id = task AND d.attempt_id = attempt AND d.lease_generation = generation
          AND d.role = 'author' AND r.parent_run_id IS NULL)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_assessment_exposure_valid(uuid, uuid, uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.relation_assessment_exposure_valid(uuid, uuid, uuid, bigint) TO memoriesql_worker;

-- Fenced canonical apply of one relation task. Proposals, specialist judgments,
-- pair dispositions and retirements commit in one transaction with the task
-- receipt; no bead, statement or revision-6 row changes. Code accepts a proposal
-- only when its recorded specialist judgment finds the exact assertion consistent
-- and warrants the author's predicate, revision and direction. Every refusal of
-- authored content is a data error (22023) so the attempt settles as invalid output.
CREATE FUNCTION memoriesql.apply_relation_assessment_v1(
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
    type_revision uuid; type_symmetric boolean; agreed boolean; forbidden uuid; named uuid[];
    pinned_ids text[]; fresh jsonb := '[]'; relation_ids uuid[] := ARRAY[]::uuid[];
    accepted_ids uuid[] := ARRAY[]::uuid[]; statement uuid; unit uuid;
    uuid_pattern constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR octet_length(requested_command::text) > 1048576
       OR requested_command - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'tenant_id',
            'workspace_id', 'access_scope_id', 'task_id', 'attempt_id', 'lease_generation', 'task_kind',
            'contract_revision', 'output_contract_hash', 'semantic_result_hash', 'semantic_payload_canonical_json',
            'used_evidence_refs', 'model_run_refs', 'payload'] <> '{}'::jsonb
       OR requested_command->>'contract_version' IS DISTINCT FROM '1'
       OR requested_command->>'expected_schema_version' IS DISTINCT FROM '29'
       OR requested_command->>'task_kind' IS DISTINCT FROM 'memory.semantic.assess-relations'
       OR requested_command->>'contract_revision' IS DISTINCT FROM '1'
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
      AND q.contract_revision = 1;
    IF NOT FOUND
       OR NOT memoriesql.current_context_semantic_task_authorized(command_task_id, 'memory.maintain', 'write')
       OR NOT memoriesql.semantic_task_origin_authorized(command_tenant_id, command_task_id, database_now) THEN
        RAISE EXCEPTION 'relation assessment task is unavailable' USING ERRCODE = '42501';
    END IF;

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
      AND r.parent_run_id IS NULL AND r.run_status = 'running' AND r.agent_key = 'memory.semantic.relation-author';
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
         AND r.revision = (d.v->>'revision')::integer AND r.cycle_policy = 'forbidden'
        ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            command_tenant_id::text || ':relation-cycle:' || forbidden::text, 0));
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
                      IN ('superseded', 'not_accepted') THEN
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
REVOKE ALL ON FUNCTION memoriesql.apply_relation_assessment_v1(jsonb, text, text, timestamp with time zone) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.apply_relation_assessment_v1(jsonb, text, text, timestamp with time zone) TO memoriesql_worker;

-- Exact restatements: the relation task joins reauthorization, the input envelope,
-- specialist run admission and supervised dispatch; earlier revisions keep their meaning.
-- Reauthorization and run events also refuse a NULL phase or time for every task.
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
    IF task_record.task_kind='memory.semantic.author-complete-unit' AND task_record.contract_revision IN (2,3,4,5,6) THEN
        BEGIN
            PERFORM memoriesql.complete_input_authorize(requested_tenant_id,
                (task_record.input_payload#>>'{payload,binding_task_id}')::uuid,
                (task_record.input_payload#>>'{payload,dispatch_policy_id}')::uuid);
        EXCEPTION WHEN insufficient_privilege THEN RETURN 'policy_paused';
                  WHEN object_not_in_prerequisite_state THEN RETURN 'stale_evidence';
        END;
        IF task_record.contract_revision IN (3,4,5,6) THEN
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
        RETURN candidate->>'contract_revision'='1' AND octet_length(candidate::text)<=262144
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
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision' IN ('3','4','5') THEN
        RETURN octet_length(candidate::text)<=32768 AND candidate#>>'{payload,required_execution}'='trusted_source_revisiting_v1'
           AND candidate->>'expected_target_revision'='0' AND candidate->'requested_budget'='null'::jsonb
           AND candidate->'requested_effort_key'='null'::jsonb AND jsonb_array_length(candidate#>'{evidence_manifest,references}')=1
           AND jsonb_array_length(candidate#>'{payload,source_unit_ids}')=1 AND jsonb_array_length(candidate#>'{payload,bead_ids}')=1;
    END IF;
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision'='2' THEN
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
             AND task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision=1
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
CREATE OR REPLACE FUNCTION memoriesql.consume_supervised_dispatch(i jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE; route jsonb; ordinal bigint; prior_count bigint; c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended((i->>'tenant_id')||':semantic_outcome_authority:',0));
 SELECT * INTO q FROM memoriesql.model_supervised_qualifications
 WHERE tenant_id=(i->>'tenant_id')::uuid AND task_id=(i->>'semantic_task_id')::uuid;
 IF NOT FOUND THEN
  IF i ? 'supervision' OR COALESCE(i->>'dispatch_boundary','single_inference')<>'single_inference' THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
  RETURN;
 END IF;
 IF q.configuration IS DISTINCT FROM i->'supervision' OR q.origin_principal_id IS DISTINCT FROM (i->>'origin_principal_id')::uuid
 OR q.workspace_id IS DISTINCT FROM (i->>'workspace_id')::uuid OR q.access_scope_id IS DISTINCT FROM (i->>'access_scope_id')::uuid
 OR NOT memoriesql.supervised_task_open(q.tenant_id,q.task_id)
 OR NOT COALESCE((i->>'task_kind'='memory.semantic.author-complete-unit' AND (i->>'task_contract_revision')::integer IN (2,3,4,5,6))
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
 IF EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents WHERE tenant_id=q.tenant_id AND task_id=q.task_id AND supervised_qualification_id IS DISTINCT FROM q.qualification_id)
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_outside_approval' USING ERRCODE='55000'; END IF;
 -- Hard-bounded follow-up reservations must also fit the remaining reported stop allowance.
 IF ordinal=2 AND EXISTS(SELECT 1 FROM memoriesql.model_dispatch_usage_fold_v2 f WHERE f.tenant_id=q.tenant_id AND f.supervised_qualification_id=q.qualification_id
  AND (f.input_tokens+(i->>'max_input_tokens')::bigint>(q.configuration->>'reported_input_token_stop')::bigint
   OR f.output_tokens+(i->>'max_output_tokens')::bigint>(q.configuration->>'reported_generated_token_stop')::bigint
   OR (q.configuration->>'reported_cash_stop_microunits' IS NOT NULL AND f.incremental_cash_exposure_microunits+(i->>'conservative_reservation_microunits')::bigint>(q.configuration->>'reported_cash_stop_microunits')::bigint)))
 THEN RAISE EXCEPTION 'supervised_followup_exceeds_remaining_allowance' USING ERRCODE='55000'; END IF;
END; $$;

-- Exact restatement: the revision-1 read lists authored retirements and requires every
-- replacement's beads to be readable.
CREATE OR REPLACE FUNCTION memoriesql.inspect_bead_relations_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms'
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE;
    ev memoriesql.source_events%ROWTYPE;
    r memoriesql.semantic_task_receipts%ROWTYPE; ir memoriesql.idempotency_receipts%ROWTYPE;
    cl memoriesql.bead_claims%ROWTYPE; rel memoriesql.bead_relations%ROWTYPE;
    other memoriesql.bead_versions%ROWTYPE; s memoriesql.bead_semantic_statements%ROWTYPE;
    link record; row_item record;
    known timestamp with time zone; capable boolean := false;
    claims jsonb := '[]'; relations jsonb := '[]'; assessments jsonb := '[]'; judgments jsonb := '[]';
    source_statements jsonb; target_statements jsonb; statements jsonb; evidence jsonb; events jsonb;
    state jsonb; source_time jsonb; rtype jsonb; result jsonb; roots uuid[];
    sources uuid[] := '{}'; source uuid; amount integer;
    started timestamp with time zone := pg_catalog.clock_timestamp();
    unavailable constant jsonb := '{"contract_version":1,"outcome":"unavailable"}';
    budget constant jsonb := '{"contract_version":1,"outcome":"budget_exhausted"}';
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'bead_id', 'known_at'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1'
       OR octet_length(request::text) > 1024
       OR COALESCE(request->>'bead_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR (request ? 'known_at' AND jsonb_typeof(request->'known_at') NOT IN ('string', 'null')) THEN
        RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE = '22023';
    END IF;
    -- A future known time reads as now; history is never extrapolated.
    known := LEAST(COALESCE((request->>'known_at')::timestamp with time zone, started), started);
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO b FROM memoriesql.beads
    WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id AND bead_id = (request->>'bead_id')::uuid;
    IF b.bead_id IS NULL OR NOT memoriesql.current_context_event_authorized(
            b.access_scope_id, b.event_id, 'memory.query', 'read') THEN
        RETURN unavailable;
    END IF;
    SELECT * INTO ev FROM memoriesql.source_events WHERE tenant_id = b.tenant_id AND event_id = b.event_id;
    sources := array_append(sources, ev.source_object_id);
    PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
    SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics AS a
    JOIN memoriesql.bead_versions AS bv ON bv.tenant_id = a.tenant_id AND bv.bead_version_id = a.bead_version_id
    WHERE a.tenant_id = b.tenant_id AND a.bead_id = b.bead_id AND bv.authored_at <= known;

    IF v.bead_version_id IS NOT NULL THEN
        IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id, v.workspace_id, v.access_scope_id, v.bead_version_id) THEN
            RETURN unavailable;
        END IF;
        SELECT * INTO r FROM memoriesql.semantic_task_receipts
        WHERE tenant_id = v.tenant_id AND semantic_task_receipt_id = v.semantic_task_receipt_id;
        SELECT * INTO ir FROM memoriesql.idempotency_receipts
        WHERE tenant_id = r.tenant_id AND idempotency_receipt_id = r.idempotency_receipt_id;
        -- Capability derives only from the accepted result's own revision-6 apply receipt.
        capable := r.task_contract_key = 'memory.semantic.author-complete-unit' AND r.task_contract_version = 6
                   AND ir.operation_kind = 'complete_input.apply.v5' AND ir.status = 'succeeded';
        source_time := memoriesql.bead_source_clock_v1(v.tenant_id, v.bead_id);

        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_claims
            WHERE tenant_id = v.tenant_id AND bead_id = v.bead_id AND recorded_at <= known LIMIT 17) AS bounded;
        IF amount > 16 THEN RETURN budget; END IF;
        FOR cl IN SELECT * FROM memoriesql.bead_claims
                  WHERE tenant_id = v.tenant_id AND bead_id = v.bead_id AND recorded_at <= known ORDER BY claim_id LOOP
            statements := '[]';
            FOR s IN SELECT st.* FROM memoriesql.bead_claim_statements AS cs
                     JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = cs.tenant_id AND st.statement_id = cs.statement_id
                     WHERE cs.tenant_id = cl.tenant_id AND cs.claim_id = cl.claim_id ORDER BY st.statement_sequence LOOP
                IF NOT memoriesql.current_context_semantic_statement_authorized(s.tenant_id, s.workspace_id, s.access_scope_id, s.statement_id) THEN
                    RETURN unavailable;
                END IF;
                statements := statements || jsonb_build_array(jsonb_build_object('statement_id', s.statement_id, 'text', s.statement_text));
            END LOOP;
            -- History explains the derived state: own events plus incoming disputes and
            -- resolutions recorded on the competing claim.
            SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_claim_events
                WHERE tenant_id = cl.tenant_id AND recorded_at <= known
                  AND (claim_id = cl.claim_id OR (related_claim_id = cl.claim_id AND action IN ('dispute', 'resolve_dispute')))
                LIMIT 65) AS bounded;
            IF amount > 64 THEN RETURN budget; END IF;
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'event_id', e.claim_event_id, 'target_id', e.claim_id, 'action', e.action,
                'related_id', e.related_claim_id, 'reason', e.reason,
                'origin', e.origin, 'authoring_bead_id', e.authoring_bead_id,
                'effective_at', memoriesql.relation_packet_time(e.effective_at),
                'recorded_at', memoriesql.relation_packet_time(e.recorded_at)) ORDER BY e.recorded_at, e.claim_event_id), '[]'::jsonb)
            INTO events FROM memoriesql.bead_claim_events AS e
            WHERE e.tenant_id = cl.tenant_id AND e.recorded_at <= known
              AND (e.claim_id = cl.claim_id OR (e.related_claim_id = cl.claim_id AND e.action IN ('dispute', 'resolve_dispute')));
            -- Named related claims are disclosed only when their beads are readable.
            FOR row_item IN
                SELECT DISTINCT k.tenant_id, k.workspace_id, k.access_scope_id, k.bead_version_id
                FROM memoriesql.bead_claims AS k
                WHERE k.tenant_id = cl.tenant_id AND k.claim_id IN (
                    SELECT e.related_claim_id FROM memoriesql.bead_claim_events AS e
                    WHERE e.tenant_id = cl.tenant_id AND e.recorded_at <= known AND e.related_claim_id IS NOT NULL
                      AND (e.claim_id = cl.claim_id OR e.related_claim_id = cl.claim_id)
                    UNION SELECT e.claim_id FROM memoriesql.bead_claim_events AS e
                    WHERE e.tenant_id = cl.tenant_id AND e.recorded_at <= known AND e.related_claim_id = cl.claim_id)
            LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                        row_item.access_scope_id, row_item.bead_version_id) THEN
                    RETURN unavailable;
                END IF;
            END LOOP;
            state := memoriesql.bead_claim_state_v1(cl.tenant_id, cl.claim_id, known);
            -- Correction beads named by the derived state are disclosed only when readable.
            FOR row_item IN
                SELECT a.tenant_id, a.workspace_id, a.access_scope_id, a.bead_version_id
                FROM jsonb_array_elements_text(state->'origin_corrected_by') AS x(id)
                JOIN memoriesql.accepted_bead_semantics AS a ON a.tenant_id = cl.tenant_id AND a.bead_id = x.id::uuid
            LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                        row_item.access_scope_id, row_item.bead_version_id) THEN
                    RETURN unavailable;
                END IF;
            END LOOP;
            claims := claims || jsonb_build_array(jsonb_build_object(
                'claim_id', cl.claim_id, 'bead_id', cl.bead_id, 'bead_version_id', cl.bead_version_id,
                'subject', cl.subject_text, 'subject_mention_id', cl.subject_entity_mention_id,
                'slot', cl.slot_text, 'value', cl.value_text, 'applicability', cl.applicability_text,
                'statements', statements, 'source_time', source_time,
                'recorded_at', memoriesql.relation_packet_time(cl.recorded_at),
                'state', state->'state', 'superseded_by', state->'superseded_by',
                'disputed_with', state->'disputed_with', 'origin_corrected_by', state->'origin_corrected_by',
                'events', events));
        END LOOP;

        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relations
            WHERE tenant_id = v.tenant_id AND (source_bead_id = v.bead_id OR target_bead_id = v.bead_id)
              AND recorded_at <= known LIMIT 65) AS bounded;
        IF amount > 64 THEN RETURN budget; END IF;
        FOR rel IN SELECT * FROM memoriesql.bead_relations
                   WHERE tenant_id = v.tenant_id AND (source_bead_id = v.bead_id OR target_bead_id = v.bead_id)
                     AND recorded_at <= known ORDER BY recorded_at, relation_id LOOP
            SELECT * INTO other FROM memoriesql.bead_versions WHERE tenant_id = rel.tenant_id
              AND bead_version_id = CASE WHEN rel.source_bead_id = v.bead_id THEN rel.target_bead_version_id ELSE rel.source_bead_version_id END;
            IF other.bead_version_id IS NULL OR NOT memoriesql.current_context_bead_version_authorized(
                    other.tenant_id, other.workspace_id, other.access_scope_id, other.bead_version_id) THEN
                RETURN unavailable;
            END IF;
            source_statements := '[]'; target_statements := '[]';
            FOR link IN SELECT rs.endpoint, st.* FROM memoriesql.bead_relation_statements AS rs
                        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = rs.tenant_id AND st.statement_id = rs.statement_id
                        WHERE rs.tenant_id = rel.tenant_id AND rs.relation_id = rel.relation_id
                        ORDER BY rs.endpoint, st.statement_sequence LOOP
                IF NOT memoriesql.current_context_semantic_statement_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.statement_id) THEN
                    RETURN unavailable;
                END IF;
                IF link.endpoint = 'source' THEN
                    source_statements := source_statements || jsonb_build_array(jsonb_build_object('statement_id', link.statement_id, 'text', link.statement_text));
                ELSE
                    target_statements := target_statements || jsonb_build_array(jsonb_build_object('statement_id', link.statement_id, 'text', link.statement_text));
                END IF;
            END LOOP;
            evidence := '[]';
            FOR link IN SELECT l.statement_id, l.evidence_source_unit_id, x.evidence_content_hash, x.access_scope_id, x.evidence_event_id
                        FROM memoriesql.semantic_evidence_links AS l
                        JOIN memoriesql.bead_semantic_statement_evidence AS x
                          ON x.tenant_id = l.tenant_id AND x.statement_id = l.statement_id AND x.evidence_source_unit_id = l.evidence_source_unit_id
                        WHERE l.tenant_id = rel.tenant_id AND l.owner_kind = 'relation' AND l.owner_id = rel.relation_id
                          AND l.recorded_at <= known
                        ORDER BY l.statement_id, l.evidence_source_unit_id LOOP
                IF NOT memoriesql.current_context_event_authorized(link.access_scope_id, link.evidence_event_id, 'memory.query', 'read') THEN
                    RETURN unavailable;
                END IF;
                SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id = rel.tenant_id AND event_id = link.evidence_event_id;
                PERFORM memoriesql.revisiting_source_authorize(source); sources := array_append(sources, source);
                roots := memoriesql.source_unit_derivation_roots(rel.tenant_id, link.evidence_source_unit_id, known);
                FOREACH source IN ARRAY roots LOOP
                    PERFORM memoriesql.revisiting_source_authorize(source); sources := array_append(sources, source);
                END LOOP;
                evidence := evidence || jsonb_build_array(jsonb_build_object(
                    'statement_id', link.statement_id, 'source_unit_id', link.evidence_source_unit_id,
                    'content_sha256', link.evidence_content_hash, 'derivation_root_ids', to_jsonb(roots)));
            END LOOP;
            SELECT jsonb_build_object('key', ty.type_key, 'revision', tr.revision, 'namespace', ty.namespace,
                       'label', tr.display_label, 'endpoint_rule', tr.endpoint_rule,
                       'cycle_policy', tr.cycle_policy, 'forward_reading', tr.forward_reading,
                       'inverse_reading', tr.inverse_reading, 'symmetric', tr.is_symmetric)
            INTO rtype FROM memoriesql.relation_type_revisions AS tr
            JOIN memoriesql.relation_types AS ty ON ty.relation_type_id = tr.relation_type_id
            WHERE tr.relation_type_revision_id = rel.relation_type_revision_id;
            SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relation_events
                WHERE tenant_id = rel.tenant_id AND relation_id = rel.relation_id AND recorded_at <= known LIMIT 65) AS bounded;
            IF amount > 64 THEN RETURN budget; END IF;
            -- Governed events and authored retirements explain the derived state.
            events := memoriesql.relation_events_view_v1(rel.tenant_id, 'authored', rel.relation_id, known);
            state := memoriesql.bead_relation_state_v1(rel.tenant_id, rel.relation_id, known);
            -- Every named replacement relation and correction bead must be readable too:
            -- both endpoints of each replacement, and each correcting bead.
            FOR row_item IN
                SELECT DISTINCT v2.tenant_id, v2.workspace_id, v2.access_scope_id, v2.bead_version_id
                FROM memoriesql.bead_relation_events AS e
                JOIN memoriesql.bead_relations AS r2 ON r2.tenant_id = e.tenant_id AND r2.relation_id = e.replacement_relation_id
                JOIN memoriesql.bead_versions AS v2 ON v2.tenant_id = r2.tenant_id
                 AND v2.bead_version_id IN (r2.source_bead_version_id, r2.target_bead_version_id)
                WHERE e.tenant_id = rel.tenant_id AND e.relation_id = rel.relation_id AND e.recorded_at <= known
                  AND e.replacement_relation_id IS NOT NULL
                UNION
                SELECT v3.tenant_id, v3.workspace_id, v3.access_scope_id, v3.bead_version_id
                FROM memoriesql.relation_retirements AS x
                JOIN memoriesql.assessed_relation_statements AS s3
                  ON s3.tenant_id = x.tenant_id AND s3.relation_id = x.replacement_relation_id
                JOIN memoriesql.bead_versions AS v3 ON v3.tenant_id = s3.tenant_id AND v3.bead_version_id = s3.bead_version_id
                WHERE x.tenant_id = rel.tenant_id AND x.retired_kind = 'authored'
                  AND x.retired_relation_id = rel.relation_id AND x.recorded_at <= known
                UNION
                SELECT a.tenant_id, a.workspace_id, a.access_scope_id, a.bead_version_id
                FROM jsonb_array_elements_text(state->'endpoint_corrected_by') AS x(id)
                JOIN memoriesql.accepted_bead_semantics AS a ON a.tenant_id = rel.tenant_id AND a.bead_id = x.id::uuid
            LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                        row_item.access_scope_id, row_item.bead_version_id) THEN
                    RETURN unavailable;
                END IF;
            END LOOP;
            relations := relations || jsonb_build_array(jsonb_build_object(
                'relation_id', rel.relation_id, 'relation_type', rtype,
                'direction', CASE WHEN rel.source_bead_id = v.bead_id THEN 'outgoing' ELSE 'incoming' END,
                'source_bead_id', rel.source_bead_id, 'source_bead_version_id', rel.source_bead_version_id,
                'target_bead_id', rel.target_bead_id, 'target_bead_version_id', rel.target_bead_version_id,
                'authoring_bead_id', rel.authoring_bead_id, 'basis', rel.basis, 'rationale', rel.rationale_text,
                'qualification', rel.qualification_text, 'author_confidence', rel.author_confidence,
                'source_statements', source_statements, 'target_statements', target_statements,
                'evidence', evidence,
                'independent_root_count', memoriesql.evidence_independent_root_count(rel.tenant_id, 'relation', rel.relation_id, known),
                'recorded_at', memoriesql.relation_packet_time(rel.recorded_at),
                'state', state->'state', 'superseded_by', state->'superseded_by',
                'endpoint_corrected_by', state->'endpoint_corrected_by', 'events', events));
        END LOOP;

        FOR row_item IN SELECT * FROM memoriesql.relation_candidate_assessments
                        WHERE tenant_id = v.tenant_id AND authoring_bead_id = v.bead_id AND recorded_at <= known
                        ORDER BY candidate_bead_id LOOP
            IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.candidate_access_scope_id, row_item.candidate_bead_version_id) THEN
                RETURN unavailable;
            END IF;
            assessments := assessments || jsonb_build_array(jsonb_build_object(
                'candidate_bead_id', row_item.candidate_bead_id, 'candidate_bead_version_id', row_item.candidate_bead_version_id,
                'assessment', row_item.assessment, 'reason', row_item.reason_text));
        END LOOP;

        FOR row_item IN SELECT e.*, k.bead_id AS target_bead_id, k.workspace_id AS target_workspace_id,
                               k.access_scope_id AS target_scope_id, k.bead_version_id AS target_version_id
                        FROM memoriesql.bead_claim_events AS e
                        JOIN memoriesql.bead_claims AS k ON k.tenant_id = e.tenant_id AND k.claim_id = e.claim_id
                        WHERE e.tenant_id = v.tenant_id AND e.authoring_bead_id = v.bead_id AND e.origin = 'authored'
                          AND e.recorded_at <= known
                        ORDER BY e.claim_id, e.action, e.claim_event_id LOOP
            IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.target_workspace_id,
                    row_item.target_scope_id, row_item.target_version_id) THEN
                RETURN unavailable;
            END IF;
            judgments := judgments || jsonb_build_array(jsonb_build_object(
                'event_id', row_item.claim_event_id, 'target_claim_id', row_item.claim_id,
                'target_bead_id', row_item.target_bead_id, 'action', row_item.action,
                'related_claim_id', row_item.related_claim_id, 'reason', row_item.reason));
        END LOOP;
    END IF;

    result := jsonb_build_object('contract_version', 1, 'outcome', 'available', 'bead_id', b.bead_id,
        'known_at', memoriesql.relation_packet_time(known), 'relations_capable', capable,
        'claims', claims, 'relations', relations, 'candidate_assessments', assessments,
        'authored_claim_events', judgments);
    IF octet_length(memoriesql.canonical_semantic_json_text(result)) > 262144 THEN RETURN budget; END IF;
    -- Revalidate every disclosed source dependency at the return boundary.
    FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
        PERFORM memoriesql.revisiting_source_authorize(source);
    END LOOP;
    IF pg_catalog.clock_timestamp() - started > interval '2 seconds' THEN RETURN budget; END IF;
    RETURN result;
EXCEPTION
    WHEN insufficient_privilege THEN RETURN unavailable;
    -- A derivation lineage over its limit is reported, never truncated.
    WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;

-- Exact pin and full definition of one relation type revision.
CREATE FUNCTION memoriesql.relation_type_pin_v1(revision_id uuid)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT jsonb_build_object('key', ty.type_key, 'revision', tr.revision)
    FROM memoriesql.relation_type_revisions AS tr
    JOIN memoriesql.relation_types AS ty ON ty.relation_type_id = tr.relation_type_id
    WHERE tr.relation_type_revision_id = revision_id
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_type_pin_v1(uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.relation_type_definition_v1(revision_id uuid)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT jsonb_build_object('key', ty.type_key, 'revision', tr.revision, 'namespace', ty.namespace,
        'label', tr.display_label, 'definition', tr.definition, 'endpoint_rule', tr.endpoint_rule,
        'evidence_expectation', tr.evidence_expectation, 'example', tr.example, 'counterexample', tr.counterexample,
        'cycle_policy', tr.cycle_policy, 'forward_reading', tr.forward_reading, 'inverse_reading', tr.inverse_reading,
        'symmetric', tr.is_symmetric)
    FROM memoriesql.relation_type_revisions AS tr
    JOIN memoriesql.relation_types AS ty ON ty.relation_type_id = tr.relation_type_id
    WHERE tr.relation_type_revision_id = revision_id
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_type_definition_v1(uuid) FROM PUBLIC;

-- An assertion's evidence as known at a time: each exact statement-evidence pair
-- with its derivation roots, every evidence event query-readable and every source
-- and root raw-readable. Any unreadable item raises 42501.
CREATE FUNCTION memoriesql.relation_evidence_view_v1(t uuid, owner_kind_value text, owner uuid, known timestamp with time zone)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE link record; roots uuid[]; source uuid; items jsonb := '[]'; sources jsonb := '[]';
BEGIN
    FOR link IN SELECT l.statement_id, l.evidence_source_unit_id, x.evidence_content_hash, x.access_scope_id, x.evidence_event_id
                FROM memoriesql.semantic_evidence_links AS l
                JOIN memoriesql.bead_semantic_statement_evidence AS x
                  ON x.tenant_id = l.tenant_id AND x.statement_id = l.statement_id AND x.evidence_source_unit_id = l.evidence_source_unit_id
                WHERE l.tenant_id = t AND l.owner_kind = owner_kind_value AND l.owner_id = owner AND l.recorded_at <= known
                ORDER BY l.statement_id, l.evidence_source_unit_id LOOP
        IF NOT memoriesql.current_context_event_authorized(link.access_scope_id, link.evidence_event_id, 'memory.query', 'read') THEN
            RAISE EXCEPTION 'relation_evidence_unavailable' USING ERRCODE = '42501';
        END IF;
        SELECT e.source_object_id INTO source FROM memoriesql.source_events AS e WHERE e.tenant_id = t AND e.event_id = link.evidence_event_id;
        PERFORM memoriesql.revisiting_source_authorize(source);
        sources := sources || jsonb_build_array(jsonb_build_object('source', source));
        roots := memoriesql.source_unit_derivation_roots(t, link.evidence_source_unit_id, known);
        FOREACH source IN ARRAY roots LOOP
            PERFORM memoriesql.revisiting_source_authorize(source);
            sources := sources || jsonb_build_array(jsonb_build_object('source', source));
        END LOOP;
        items := items || jsonb_build_array(jsonb_build_object(
            'statement_id', link.statement_id, 'source_unit_id', link.evidence_source_unit_id,
            'content_sha256', link.evidence_content_hash, 'derivation_root_ids', to_jsonb(roots)));
    END LOOP;
    RETURN jsonb_build_object('items', items, 'sources', sources);
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_evidence_view_v1(uuid, text, uuid, timestamp with time zone) FROM PUBLIC;

-- An assertion's lifecycle history as known at a time: governed events of an authored
-- relation and authored retirements of either kind, each explaining its derived state.
CREATE FUNCTION memoriesql.relation_events_view_v1(t uuid, relation_kind text, relation uuid, known timestamp with time zone)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'event_id', e.event_id, 'target_id', e.target_id, 'action', e.action, 'related_id', e.related_id,
        'reason', e.reason, 'origin', e.origin, 'authoring_bead_id', NULL,
        'effective_at', memoriesql.relation_packet_time(e.effective_at),
        'recorded_at', memoriesql.relation_packet_time(e.recorded_at)) ORDER BY e.recorded_at, e.event_id), '[]'::jsonb)
    FROM (
        SELECT g.relation_event_id AS event_id, g.relation_id AS target_id, g.action, g.replacement_relation_id AS related_id,
               g.reason, g.origin, g.effective_at, g.recorded_at
        FROM memoriesql.bead_relation_events AS g
        WHERE relation_kind = 'authored' AND g.tenant_id = t AND g.relation_id = relation AND g.recorded_at <= known
        UNION ALL
        SELECT x.retirement_id, x.retired_relation_id, 'retire', x.replacement_relation_id, x.reason_text, 'authored',
               NULL::timestamp with time zone, x.recorded_at
        FROM memoriesql.relation_retirements AS x
        WHERE x.tenant_id = t AND x.retired_kind = relation_kind AND x.retired_relation_id = relation AND x.recorded_at <= known
    ) AS e
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_events_view_v1(uuid, text, uuid, timestamp with time zone) FROM PUBLIC;

-- Every bead an assertion's derived state names must be readable: each endpoint and
-- basis bead of a replacement, of either kind, and each correcting bead.
CREATE FUNCTION memoriesql.relation_dependencies_readable_v1(
    t uuid, relation_kind text, relation uuid, state jsonb, known timestamp with time zone
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM (
            SELECT v2.tenant_id, v2.workspace_id, v2.access_scope_id, v2.bead_version_id
            FROM memoriesql.bead_relation_events AS e
            JOIN memoriesql.bead_relations AS r2 ON r2.tenant_id = e.tenant_id AND r2.relation_id = e.replacement_relation_id
            JOIN memoriesql.bead_versions AS v2 ON v2.tenant_id = r2.tenant_id
             AND v2.bead_version_id IN (r2.source_bead_version_id, r2.target_bead_version_id)
            WHERE relation_kind = 'authored' AND e.tenant_id = t AND e.relation_id = relation
              AND e.recorded_at <= known AND e.replacement_relation_id IS NOT NULL
            UNION
            SELECT v3.tenant_id, v3.workspace_id, v3.access_scope_id, v3.bead_version_id
            FROM memoriesql.relation_retirements AS x
            JOIN memoriesql.assessed_relation_statements AS s3 ON s3.tenant_id = x.tenant_id AND s3.relation_id = x.replacement_relation_id
            JOIN memoriesql.bead_versions AS v3 ON v3.tenant_id = s3.tenant_id AND v3.bead_version_id = s3.bead_version_id
            WHERE x.tenant_id = t AND x.retired_kind = relation_kind AND x.retired_relation_id = relation AND x.recorded_at <= known
            UNION
            SELECT a.tenant_id, a.workspace_id, a.access_scope_id, a.bead_version_id
            FROM jsonb_array_elements_text(state->'endpoint_corrected_by') AS c(id)
            JOIN memoriesql.accepted_bead_semantics AS a ON a.tenant_id = t AND a.bead_id = c.id::uuid
        ) AS dependency
        WHERE NOT memoriesql.current_context_bead_version_authorized(dependency.tenant_id, dependency.workspace_id,
                  dependency.access_scope_id, dependency.bead_version_id))
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_dependencies_readable_v1(uuid, text, uuid, jsonb, timestamp with time zone) FROM PUBLIC;

-- Relations of both kinds around one accepted bead as known at a time: assertions
-- where it is an endpoint or supplies a basis statement, proposals left unaccepted
-- with both contributions, pair coverage, revision-6 candidate assessments and the
-- status of the relation tasks whose subject it is. Relation types are returned once
-- with their full pinned definitions. Every disclosed bead, statement and evidence
-- item is reauthorized; any unreadable dependency makes the whole read unavailable.
CREATE FUNCTION memoriesql.inspect_bead_relations_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms'
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE; ev memoriesql.source_events%ROWTYPE;
    rel memoriesql.bead_relations%ROWTYPE; ar memoriesql.assessed_relations%ROWTYPE;
    link record; row_item record;
    known timestamp with time zone; amount integer;
    relations jsonb := '[]'; dispositions jsonb := '[]'; assessments jsonb := '[]'; tasks jsonb := '[]';
    types jsonb; type_ids uuid[] := '{}'; statements jsonb; evidence jsonb; events jsonb; state jsonb; result jsonb;
    roots uuid[]; sources uuid[] := '{}'; source uuid;
    started timestamp with time zone := pg_catalog.clock_timestamp();
    unavailable constant jsonb := '{"contract_version":2,"outcome":"unavailable"}';
    budget constant jsonb := '{"contract_version":2,"outcome":"budget_exhausted"}';
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'bead_id', 'known_at'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '2'
       OR octet_length(request::text) > 1024
       OR COALESCE(request->>'bead_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR (request ? 'known_at' AND jsonb_typeof(request->'known_at') NOT IN ('string', 'null')) THEN
        RAISE EXCEPTION 'invalid_bead_relations_inspection' USING ERRCODE = '22023';
    END IF;
    -- A future known time reads as now; history is never extrapolated.
    known := LEAST(COALESCE((request->>'known_at')::timestamp with time zone, started), started);
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT bead.* INTO b FROM memoriesql.beads AS bead
    WHERE bead.tenant_id = c.tenant_id AND bead.workspace_id = c.workspace_id AND bead.bead_id = (request->>'bead_id')::uuid;
    IF b.bead_id IS NULL OR NOT memoriesql.current_context_event_authorized(b.access_scope_id, b.event_id, 'memory.query', 'read') THEN
        RETURN unavailable;
    END IF;
    SELECT e.* INTO ev FROM memoriesql.source_events AS e WHERE e.tenant_id = b.tenant_id AND e.event_id = b.event_id;
    sources := array_append(sources, ev.source_object_id);
    PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
    SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics AS a
    JOIN memoriesql.bead_versions AS bv ON bv.tenant_id = a.tenant_id AND bv.bead_version_id = a.bead_version_id
    WHERE a.tenant_id = b.tenant_id AND a.bead_id = b.bead_id AND bv.authored_at <= known;

    IF v.bead_version_id IS NOT NULL THEN
        IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id, v.workspace_id, v.access_scope_id, v.bead_version_id) THEN
            RETURN unavailable;
        END IF;

        -- Revision-6 authored relations with this bead as an endpoint.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relations AS r
            WHERE r.tenant_id = v.tenant_id AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id)
              AND r.recorded_at <= known LIMIT 65) AS bounded;
        IF amount > 64 THEN RETURN budget; END IF;
        FOR rel IN SELECT r.* FROM memoriesql.bead_relations AS r
                   WHERE r.tenant_id = v.tenant_id AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id)
                     AND r.recorded_at <= known ORDER BY r.recorded_at, r.relation_id LOOP
            statements := '[]';
            FOR link IN SELECT rs.endpoint AS side, st.* FROM memoriesql.bead_relation_statements AS rs
                        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = rs.tenant_id AND st.statement_id = rs.statement_id
                        WHERE rs.tenant_id = rel.tenant_id AND rs.relation_id = rel.relation_id
                        ORDER BY rs.endpoint, st.statement_sequence LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.bead_version_id)
                   OR NOT memoriesql.current_context_semantic_statement_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.statement_id) THEN
                    RETURN unavailable;
                END IF;
                statements := statements || jsonb_build_array(jsonb_build_object('side', link.side,
                    'statement', jsonb_build_object('statement_id', link.statement_id, 'bead_id', link.bead_id,
                        'bead_version_id', link.bead_version_id, 'text', link.statement_text)));
            END LOOP;
            evidence := memoriesql.relation_evidence_view_v1(rel.tenant_id, 'relation', rel.relation_id, known);
            sources := sources || ARRAY(SELECT (x.v->>'source')::uuid FROM jsonb_array_elements(evidence->'sources') AS x(v));
            SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relation_events AS e
                WHERE e.tenant_id = rel.tenant_id AND e.relation_id = rel.relation_id AND e.recorded_at <= known LIMIT 65) AS bounded;
            IF amount > 64 THEN RETURN budget; END IF;
            events := memoriesql.relation_events_view_v1(rel.tenant_id, 'authored', rel.relation_id, known);
            state := memoriesql.bead_relation_state_v1(rel.tenant_id, rel.relation_id, known);
            IF NOT memoriesql.relation_dependencies_readable_v1(rel.tenant_id, 'authored', rel.relation_id, state, known) THEN
                RETURN unavailable;
            END IF;
            type_ids := array_append(type_ids, rel.relation_type_revision_id);
            relations := relations || jsonb_build_array(jsonb_build_object(
                'kind', 'authored', 'relation_id', rel.relation_id,
                'relation_type', memoriesql.relation_type_pin_v1(rel.relation_type_revision_id),
                'direction', CASE WHEN rel.source_bead_id = v.bead_id THEN 'outgoing' ELSE 'incoming' END,
                'source_bead_id', rel.source_bead_id, 'source_bead_version_id', rel.source_bead_version_id,
                'target_bead_id', rel.target_bead_id, 'target_bead_version_id', rel.target_bead_version_id,
                'source_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'source'), '[]'::jsonb),
                'target_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'target'), '[]'::jsonb),
                'basis_statements', '[]'::jsonb,
                'basis', rel.basis, 'rationale', rel.rationale_text, 'qualification', rel.qualification_text,
                'author_confidence', rel.author_confidence, 'evidence', evidence->'items',
                'independent_root_count', memoriesql.evidence_independent_root_count(rel.tenant_id, 'relation', rel.relation_id, known),
                'authoring_bead_id', rel.authoring_bead_id, 'task_id', NULL, 'author_run_ref', rel.semantic_run_id,
                'specialist_run_ref', NULL, 'judgment', NULL,
                'recorded_at', memoriesql.relation_packet_time(rel.recorded_at),
                'state', state->'state', 'superseded_by', state->'superseded_by',
                'endpoint_corrected_by', state->'endpoint_corrected_by', 'events', events));
        END LOOP;

        -- Assessed proposals with this bead as an endpoint or a basis statement's bead.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.assessed_relations AS r
            WHERE r.tenant_id = v.tenant_id AND r.recorded_at <= known
              AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id OR EXISTS (
                    SELECT 1 FROM memoriesql.assessed_relation_statements AS s
                    WHERE s.tenant_id = r.tenant_id AND s.relation_id = r.relation_id AND s.bead_id = v.bead_id))
            LIMIT 65) AS bounded;
        IF amount > 64 THEN RETURN budget; END IF;
        FOR ar IN SELECT r.* FROM memoriesql.assessed_relations AS r
                  WHERE r.tenant_id = v.tenant_id AND r.recorded_at <= known
                    AND (r.source_bead_id = v.bead_id OR r.target_bead_id = v.bead_id OR EXISTS (
                          SELECT 1 FROM memoriesql.assessed_relation_statements AS s
                          WHERE s.tenant_id = r.tenant_id AND s.relation_id = r.relation_id AND s.bead_id = v.bead_id))
                  ORDER BY r.recorded_at, r.relation_id LOOP
            statements := '[]';
            FOR link IN SELECT rs.role AS side, st.* FROM memoriesql.assessed_relation_statements AS rs
                        JOIN memoriesql.bead_semantic_statements AS st ON st.tenant_id = rs.tenant_id AND st.statement_id = rs.statement_id
                        WHERE rs.tenant_id = ar.tenant_id AND rs.relation_id = ar.relation_id
                        ORDER BY rs.role, st.bead_id, st.statement_sequence LOOP
                IF NOT memoriesql.current_context_bead_version_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.bead_version_id)
                   OR NOT memoriesql.current_context_semantic_statement_authorized(link.tenant_id, link.workspace_id, link.access_scope_id, link.statement_id) THEN
                    RETURN unavailable;
                END IF;
                statements := statements || jsonb_build_array(jsonb_build_object('side', link.side,
                    'statement', jsonb_build_object('statement_id', link.statement_id, 'bead_id', link.bead_id,
                        'bead_version_id', link.bead_version_id, 'text', link.statement_text)));
            END LOOP;
            evidence := memoriesql.relation_evidence_view_v1(ar.tenant_id, 'assessed_relation', ar.relation_id, known);
            sources := sources || ARRAY(SELECT (x.v->>'source')::uuid FROM jsonb_array_elements(evidence->'sources') AS x(v));
            events := memoriesql.relation_events_view_v1(ar.tenant_id, 'assessed', ar.relation_id, known);
            state := memoriesql.assessed_relation_state_v1(ar.tenant_id, ar.relation_id, known);
            IF NOT memoriesql.relation_dependencies_readable_v1(ar.tenant_id, 'assessed', ar.relation_id, state, known) THEN
                RETURN unavailable;
            END IF;
            type_ids := array_append(type_ids, ar.relation_type_revision_id);
            type_ids := type_ids || ARRAY(
                SELECT r.relation_type_revision_id FROM jsonb_array_elements(ar.judgment->'warranted') AS w(v)
                JOIN memoriesql.relation_types AS ty ON ty.type_key = w.v#>>'{relation_type,key}'
                 AND (ty.tenant_id IS NULL OR (ty.tenant_id = ar.tenant_id AND ty.workspace_id = ar.workspace_id))
                JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
                 AND r.revision = (w.v#>>'{relation_type,revision}')::integer);
            relations := relations || jsonb_build_array(jsonb_build_object(
                'kind', 'assessed', 'relation_id', ar.relation_id,
                'relation_type', memoriesql.relation_type_pin_v1(ar.relation_type_revision_id),
                'direction', CASE WHEN ar.source_bead_id = v.bead_id AND ar.target_bead_id = v.bead_id THEN 'internal'
                                  WHEN ar.source_bead_id = v.bead_id THEN 'outgoing'
                                  WHEN ar.target_bead_id = v.bead_id THEN 'incoming' ELSE 'basis' END,
                'source_bead_id', ar.source_bead_id, 'source_bead_version_id', ar.source_bead_version_id,
                'target_bead_id', ar.target_bead_id, 'target_bead_version_id', ar.target_bead_version_id,
                'source_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'source'), '[]'::jsonb),
                'target_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                               WHERE s.v->>'side' = 'target'), '[]'::jsonb),
                'basis_statements', COALESCE((SELECT jsonb_agg(s.v->'statement') FROM jsonb_array_elements(statements) AS s(v)
                                              WHERE s.v->>'side' = 'basis'), '[]'::jsonb),
                'basis', ar.basis, 'rationale', ar.rationale_text, 'qualification', ar.qualification_text,
                'author_confidence', ar.author_confidence, 'evidence', evidence->'items',
                'independent_root_count', memoriesql.evidence_independent_root_count(ar.tenant_id, 'assessed_relation', ar.relation_id, known),
                'authoring_bead_id', NULL, 'task_id', ar.task_id, 'author_run_ref', ar.author_run_id,
                'specialist_run_ref', ar.specialist_run_id, 'judgment', ar.judgment,
                'recorded_at', memoriesql.relation_packet_time(ar.recorded_at),
                'state', state->'state', 'superseded_by', state->'superseded_by',
                'endpoint_corrected_by', state->'endpoint_corrected_by', 'events', events));
        END LOOP;

        -- Pair coverage involving this bead, from every task that pinned it.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.relation_pair_dispositions AS d
            WHERE d.tenant_id = v.tenant_id AND (d.first_bead_id = v.bead_id OR d.second_bead_id = v.bead_id)
              AND d.recorded_at <= known LIMIT 65) AS bounded;
        IF amount > 64 THEN RETURN budget; END IF;
        FOR row_item IN SELECT d.* FROM memoriesql.relation_pair_dispositions AS d
                        WHERE d.tenant_id = v.tenant_id AND (d.first_bead_id = v.bead_id OR d.second_bead_id = v.bead_id)
                          AND d.recorded_at <= known ORDER BY d.recorded_at, d.task_id, d.first_bead_id, d.second_bead_id LOOP
            IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.first_access_scope_id, row_item.first_bead_version_id)
               OR NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.second_access_scope_id, row_item.second_bead_version_id) THEN
                RETURN unavailable;
            END IF;
            dispositions := dispositions || jsonb_build_array(jsonb_build_object(
                'task_id', row_item.task_id, 'first_bead_id', row_item.first_bead_id,
                'first_bead_version_id', row_item.first_bead_version_id, 'second_bead_id', row_item.second_bead_id,
                'second_bead_version_id', row_item.second_bead_version_id, 'disposition', row_item.disposition,
                'abstention', row_item.abstention, 'reason', row_item.reason_text));
        END LOOP;

        -- Revision-6 coverage recorded by this bead's own authorship.
        FOR row_item IN SELECT a.* FROM memoriesql.relation_candidate_assessments AS a
                        WHERE a.tenant_id = v.tenant_id AND a.authoring_bead_id = v.bead_id AND a.recorded_at <= known
                        ORDER BY a.candidate_bead_id LOOP
            IF NOT memoriesql.current_context_bead_version_authorized(row_item.tenant_id, row_item.workspace_id,
                    row_item.candidate_access_scope_id, row_item.candidate_bead_version_id) THEN
                RETURN unavailable;
            END IF;
            assessments := assessments || jsonb_build_array(jsonb_build_object(
                'candidate_bead_id', row_item.candidate_bead_id, 'candidate_bead_version_id', row_item.candidate_bead_version_id,
                'assessment', row_item.assessment, 'reason', row_item.reason_text));
        END LOOP;

        -- Relation tasks whose subject is this bead, with their current visible status.
        SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.relation_assessments AS ra
            WHERE ra.tenant_id = v.tenant_id AND ra.subject_bead_id = v.bead_id AND ra.created_at <= known LIMIT 17) AS bounded;
        IF amount > 16 THEN RETURN budget; END IF;
        FOR row_item IN SELECT ra.*, q.status AS task_status, q.completed_at AS task_completed_at,
                               COALESCE(q.pause_reason_code, (SELECT at.error_code FROM memoriesql.semantic_task_attempts AS at
                                                              WHERE at.tenant_id = q.tenant_id AND at.task_id = q.task_id
                                                              ORDER BY at.attempt_id DESC LIMIT 1)) AS task_reason
                        FROM memoriesql.relation_assessments AS ra
                        JOIN memoriesql.semantic_tasks AS q ON q.tenant_id = ra.tenant_id AND q.task_id = ra.task_id
                        WHERE ra.tenant_id = v.tenant_id AND ra.subject_bead_id = v.bead_id AND ra.created_at <= known
                        ORDER BY ra.created_at, ra.task_id LOOP
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(row_item.beads) AS pb(v)
                       JOIN memoriesql.accepted_bead_semantics AS a
                         ON a.tenant_id = row_item.tenant_id AND a.bead_id = (pb.v->>'bead_id')::uuid
                       WHERE NOT memoriesql.current_context_bead_version_authorized(a.tenant_id, a.workspace_id,
                                 a.access_scope_id, a.bead_version_id)) THEN
                RETURN unavailable;
            END IF;
            tasks := tasks || jsonb_build_array(jsonb_build_object(
                'task_id', row_item.task_id, 'subject_bead_version_id', row_item.subject_bead_version_id,
                'candidate_bead_ids', to_jsonb(row_item.candidate_bead_ids),
                'reconsiders_task_id', row_item.reconsiders_task_id, 'status', row_item.task_status,
                'status_reason', row_item.task_reason,
                'activated_at', memoriesql.relation_packet_time(row_item.created_at),
                'completed_at', memoriesql.relation_packet_time(row_item.task_completed_at)));
        END LOOP;
    END IF;

    SELECT COALESCE(jsonb_agg(memoriesql.relation_type_definition_v1(ids.id) ORDER BY ids.id), '[]'::jsonb) INTO types
    FROM (SELECT DISTINCT unnest(type_ids) AS id) AS ids;
    result := jsonb_build_object('contract_version', 2, 'outcome', 'available', 'bead_id', b.bead_id,
        'known_at', memoriesql.relation_packet_time(known), 'relation_types', types, 'relations', relations,
        'pair_dispositions', dispositions, 'candidate_assessments', assessments, 'relation_tasks', tasks);
    IF octet_length(memoriesql.canonical_semantic_json_text(result)) > 524288 THEN RETURN budget; END IF;
    -- Revalidate every disclosed source dependency at the return boundary.
    FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
        PERFORM memoriesql.revisiting_source_authorize(source);
    END LOOP;
    IF pg_catalog.clock_timestamp() - started > interval '2 seconds' THEN RETURN budget; END IF;
    RETURN result;
EXCEPTION
    WHEN insufficient_privilege THEN RETURN unavailable;
    -- A derivation lineage over its limit is reported, never truncated.
    WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.inspect_bead_relations_v2(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_bead_relations_v2(jsonb) TO memoriesql_application;

-- The active relation type revisions visible in the workspace, with full definitions,
-- for Q and for the specialist's composition.
CREATE FUNCTION memoriesql.inspect_relation_vocabulary_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; types jsonb;
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR request - ARRAY['contract_version'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'invalid_relation_vocabulary_inspection' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR c.expires_at <= pg_catalog.clock_timestamp()
       OR NOT memoriesql.current_context_has_capability('memory.query') THEN
        RETURN '{"contract_version":1,"outcome":"unavailable"}'::jsonb;
    END IF;
    SELECT COALESCE(jsonb_agg(memoriesql.relation_type_definition_v1(r.relation_type_revision_id)
                              ORDER BY ty.type_key), '[]'::jsonb) INTO types
    FROM memoriesql.relation_types AS ty
    JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
    WHERE (ty.tenant_id IS NULL OR (ty.tenant_id = c.tenant_id AND ty.workspace_id = c.workspace_id))
      AND r.status = 'active'
      AND r.revision = (SELECT max(latest.revision) FROM memoriesql.relation_type_revisions AS latest
                        WHERE latest.relation_type_id = ty.relation_type_id);
    RETURN jsonb_build_object('contract_version', 1, 'outcome', 'available', 'relation_types', types);
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.inspect_relation_vocabulary_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_relation_vocabulary_v1(jsonb) TO memoriesql_application;

INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES ('semantic-tasks-v1:a331612010e79e75b7fe8851d322b462b0e575c85103259e5a383aeaadb5ca89','memory.semantic.assess-relations',1,'memoriesql.kernel','804fb7f351db5924cbe1409391965fcc23899a06761f56613ef6786141260373','canonical_semantics','memory.maintain','capture',50,3,NULL,NULL);
