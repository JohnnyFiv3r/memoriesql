-- Forward-only authored claims, relations, lifecycle events and relation vocabulary.
-- Restore the pre-upgrade backup to roll back. No historical SQL/contract edits or inference.

-- Governed relation vocabulary: built-in product types plus workspace types that enter
-- only through an explicit proposal and an authorized decision. Revisions are immutable.
CREATE TABLE memoriesql.relation_types (
    relation_type_id uuid PRIMARY KEY,
    tenant_id uuid,
    workspace_id uuid,
    namespace text NOT NULL CHECK (namespace IN ('memoriesql', 'workspace')),
    type_key text NOT NULL CHECK (type_key ~ '^[a-z][a-z0-9_]{0,63}$'),
    introduced_in_schema_version integer NOT NULL CHECK (introduced_in_schema_version > 0),
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT relation_types_scope CHECK (
        (namespace = 'memoriesql') = (tenant_id IS NULL)
        AND (tenant_id IS NULL) = (workspace_id IS NULL)
    ),
    CONSTRAINT relation_types_key_uq UNIQUE NULLS NOT DISTINCT (tenant_id, workspace_id, type_key),
    CONSTRAINT relation_types_workspace_fk FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES memoriesql.workspaces (tenant_id, workspace_id)
);

CREATE TABLE memoriesql.relation_type_revisions (
    relation_type_revision_id uuid PRIMARY KEY,
    relation_type_id uuid NOT NULL REFERENCES memoriesql.relation_types (relation_type_id),
    revision integer NOT NULL CHECK (revision > 0),
    display_label text NOT NULL CHECK (btrim(display_label) <> '' AND char_length(display_label) <= 128),
    definition text NOT NULL CHECK (btrim(definition) <> '' AND char_length(definition) <= 4096),
    forward_reading text NOT NULL CHECK (btrim(forward_reading) <> '' AND char_length(forward_reading) <= 128),
    inverse_reading text NOT NULL CHECK (btrim(inverse_reading) <> '' AND char_length(inverse_reading) <= 128),
    is_symmetric boolean NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'inactive')),
    decided_candidate_id uuid,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT relation_type_revisions_uq UNIQUE (relation_type_id, revision)
);

CREATE TABLE memoriesql.relation_type_candidates (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    type_key text NOT NULL CHECK (type_key ~ '^[a-z][a-z0-9_]{0,63}$'),
    proposed_revision integer NOT NULL CHECK (proposed_revision > 0),
    display_label text NOT NULL CHECK (btrim(display_label) <> '' AND char_length(display_label) <= 128),
    definition text NOT NULL CHECK (btrim(definition) <> '' AND char_length(definition) <= 4096),
    forward_reading text NOT NULL CHECK (btrim(forward_reading) <> '' AND char_length(forward_reading) <= 128),
    inverse_reading text NOT NULL CHECK (btrim(inverse_reading) <> '' AND char_length(inverse_reading) <= 128),
    is_symmetric boolean NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'inactive')),
    reason text NOT NULL CHECK (btrim(reason) <> '' AND char_length(reason) <= 1024),
    proposed_by_principal_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    proposed_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, candidate_id),
    FOREIGN KEY (tenant_id, idempotency_receipt_id) REFERENCES memoriesql.idempotency_receipts (tenant_id, idempotency_receipt_id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    FOREIGN KEY (tenant_id, proposed_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE TABLE memoriesql.relation_type_decisions (
    tenant_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    reason text NOT NULL CHECK (btrim(reason) <> '' AND char_length(reason) <= 1024),
    relation_type_revision_id uuid REFERENCES memoriesql.relation_type_revisions (relation_type_revision_id),
    decided_by_principal_id uuid NOT NULL,
    idempotency_receipt_id uuid NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, candidate_id),
    FOREIGN KEY (tenant_id, idempotency_receipt_id) REFERENCES memoriesql.idempotency_receipts (tenant_id, idempotency_receipt_id),
    FOREIGN KEY (tenant_id, candidate_id) REFERENCES memoriesql.relation_type_candidates (tenant_id, candidate_id),
    FOREIGN KEY (tenant_id, decided_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CHECK ((decision = 'accepted') = (relation_type_revision_id IS NOT NULL))
);

INSERT INTO memoriesql.relation_types (relation_type_id, tenant_id, workspace_id, namespace, type_key, introduced_in_schema_version, created_at) VALUES
    ('30000000-0000-4000-8000-000000000001', NULL, NULL, 'memoriesql', 'supports', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000002', NULL, NULL, 'memoriesql', 'contradicts', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000003', NULL, NULL, 'memoriesql', 'caused_by', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000004', NULL, NULL, 'memoriesql', 'led_to', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000005', NULL, NULL, 'memoriesql', 'enables', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000006', NULL, NULL, 'memoriesql', 'part_of', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000007', NULL, NULL, 'memoriesql', 'depends_on', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000008', NULL, NULL, 'memoriesql', 'blocks', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-000000000009', NULL, NULL, 'memoriesql', 'derived_from', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-00000000000a', NULL, NULL, 'memoriesql', 'supersedes', 27, '2026-09-23T00:00:00Z'),
    ('30000000-0000-4000-8000-00000000000b', NULL, NULL, 'memoriesql', 'associated_with', 27, '2026-09-23T00:00:00Z');

INSERT INTO memoriesql.relation_type_revisions (relation_type_revision_id, relation_type_id, revision, display_label, definition, forward_reading, inverse_reading, is_symmetric, status, decided_candidate_id, recorded_at) VALUES
    ('31000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', 1, 'Supports',
     'The source bead''s cited propositions provide evidence for the target bead''s cited propositions. Evidential support only: not causation, endorsement or independent corroboration.',
     'supports', 'is supported by', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000002', 1, 'Contradicts',
     'The cited propositions of the two beads are incompatible for the same subject under compatible applicability. Evidential incompatibility; it selects no winner.',
     'contradicts', 'is contradicted by', true, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', 1, 'Caused by',
     'The source bead''s cited occurrence or state was caused by the target''s, as the source states or as inferred from cited evidence. Temporal order, co-occurrence or shared entities alone are not causation.',
     'is caused by', 'causes', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000004', '30000000-0000-4000-8000-000000000004', 1, 'Led to',
     'The source bead''s cited occurrence or decision led to the target''s, per cited evidence. A consequential reading kept distinct from caused_by; not merely a later event.',
     'led to', 'resulted from', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000005', '30000000-0000-4000-8000-000000000005', 1, 'Enables',
     'The source''s cited condition makes the target''s occurrence possible or easier without being required or necessarily an actual cause.',
     'enables', 'is enabled by', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000006', '30000000-0000-4000-8000-000000000006', 1, 'Part of',
     'The source''s cited element is a component of the target''s larger whole.',
     'is part of', 'includes', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000007', '30000000-0000-4000-8000-000000000007', 1, 'Depends on',
     'The source''s cited outcome requires the target''s cited condition; without it the source cannot proceed or hold.',
     'depends on', 'is required by', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000008', '30000000-0000-4000-8000-000000000008', 1, 'Blocks',
     'The source''s cited condition is an evidenced impediment to the target''s cited occurrence or goal.',
     'blocks', 'is blocked by', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-000000000009', '30000000-0000-4000-8000-000000000009', 1, 'Derived from',
     'The source''s cited content was produced from the target''s cited content by copying, summarizing or transforming it. Provenance, never independent corroboration.',
     'is derived from', 'is the source of', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-00000000000a', '30000000-0000-4000-8000-00000000000a', 1, 'Supersedes',
     'The source''s cited policy, plan or state explicitly replaces the target''s. Domain replacement: it does not mark the target''s historical observation erroneous and is not the correction or claim-currentness authority.',
     'supersedes', 'is superseded by', false, 'active', NULL, '2026-09-23T00:00:00Z'),
    ('31000000-0000-4000-8000-00000000000b', '30000000-0000-4000-8000-00000000000b', 1, 'Associated with',
     'A meaningful evidenced association not captured by a more specific type. It requires positive evidence and is never a label for ignorance or uncertain causality.',
     'is associated with', 'is associated with', true, 'active', NULL, '2026-09-23T00:00:00Z');

-- Optional authored claims: tracked propositions bound to statements of their own
-- accepted bead. Valid time is inherited from source clocks on read, never invented.
CREATE TABLE memoriesql.bead_claims (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    subject_text text NOT NULL CHECK (btrim(subject_text) <> '' AND char_length(subject_text) <= 256),
    subject_entity_mention_id uuid,
    slot_text text NOT NULL CHECK (btrim(slot_text) <> '' AND char_length(slot_text) <= 128),
    value_text text NOT NULL CHECK (btrim(value_text) <> '' AND char_length(value_text) <= 1024),
    applicability_text text CHECK (applicability_text IS NULL OR (btrim(applicability_text) <> '' AND char_length(applicability_text) <= 1024)),
    semantic_task_id uuid NOT NULL,
    semantic_attempt_id uuid NOT NULL,
    semantic_run_id text NOT NULL,
    authored_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, claim_id),
    CONSTRAINT bead_claims_scope_uq UNIQUE (tenant_id, workspace_id, access_scope_id, claim_id),
    CONSTRAINT bead_claims_bead_fk FOREIGN KEY (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT bead_claims_mention_fk FOREIGN KEY (tenant_id, workspace_id, access_scope_id, subject_entity_mention_id, bead_version_id)
        REFERENCES memoriesql.entity_mentions (tenant_id, workspace_id, access_scope_id, entity_mention_id, bead_version_id),
    CONSTRAINT bead_claims_run_fk FOREIGN KEY (tenant_id, semantic_attempt_id, semantic_run_id)
        REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    CONSTRAINT bead_claims_author_fk FOREIGN KEY (tenant_id, authored_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE TABLE memoriesql.bead_claim_statements (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    statement_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, claim_id, statement_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, claim_id)
        REFERENCES memoriesql.bead_claims (tenant_id, workspace_id, access_scope_id, claim_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, statement_id)
        REFERENCES memoriesql.bead_semantic_statements (tenant_id, workspace_id, access_scope_id, statement_id)
);

-- Append-only lifecycle judgments. Authored events come from an accepted bundle;
-- governed events carry an authorized principal and their own receipt.
CREATE TABLE memoriesql.bead_claim_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    claim_event_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('reaffirm', 'supersede', 'retract', 'dispute', 'resolve_dispute')),
    related_claim_id uuid,
    reason text NOT NULL CHECK (btrim(reason) <> '' AND char_length(reason) <= 1024),
    origin text NOT NULL CHECK (origin IN ('authored', 'governed')),
    authoring_bead_id uuid,
    authoring_bead_version_id uuid,
    semantic_task_id uuid,
    semantic_attempt_id uuid,
    semantic_run_id text,
    idempotency_receipt_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    effective_at timestamp with time zone,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, claim_event_id),
    FOREIGN KEY (tenant_id, claim_id) REFERENCES memoriesql.bead_claims (tenant_id, claim_id),
    FOREIGN KEY (tenant_id, related_claim_id) REFERENCES memoriesql.bead_claims (tenant_id, claim_id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id),
    FOREIGN KEY (tenant_id, semantic_attempt_id, semantic_run_id) REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    FOREIGN KEY (tenant_id, idempotency_receipt_id) REFERENCES memoriesql.idempotency_receipts (tenant_id, idempotency_receipt_id),
    CHECK ((action IN ('supersede', 'dispute', 'resolve_dispute')) = (related_claim_id IS NOT NULL)),
    CHECK (related_claim_id IS DISTINCT FROM claim_id),
    CHECK ((origin = 'authored') = (
        authoring_bead_id IS NOT NULL AND authoring_bead_version_id IS NOT NULL
        AND semantic_task_id IS NOT NULL AND semantic_attempt_id IS NOT NULL AND semantic_run_id IS NOT NULL
    )),
    CHECK (origin = 'governed' OR (action IN ('supersede', 'dispute', 'reaffirm') AND effective_at IS NULL))
);
CREATE INDEX bead_claim_events_claim_idx ON memoriesql.bead_claim_events (tenant_id, claim_id, recorded_at);
CREATE INDEX bead_claim_events_related_idx ON memoriesql.bead_claim_events (tenant_id, related_claim_id)
    WHERE related_claim_id IS NOT NULL;
CREATE INDEX bead_claims_bead_idx ON memoriesql.bead_claims (tenant_id, bead_id);

-- Authored bead-to-bead relations. Both endpoints are pinned accepted versions in one
-- workspace; the stored row reads "source <forward_reading> target".
CREATE TABLE memoriesql.bead_relations (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    source_access_scope_id uuid NOT NULL,
    source_bead_id uuid NOT NULL,
    source_bead_version_id uuid NOT NULL,
    target_access_scope_id uuid NOT NULL,
    target_bead_id uuid NOT NULL,
    target_bead_version_id uuid NOT NULL,
    relation_type_revision_id uuid NOT NULL REFERENCES memoriesql.relation_type_revisions (relation_type_revision_id),
    basis text NOT NULL CHECK (basis IN ('source_stated', 'inferred')),
    rationale_text text NOT NULL CHECK (btrim(rationale_text) <> '' AND char_length(rationale_text) <= 1024),
    uncertainty_text text CHECK (uncertainty_text IS NULL OR (btrim(uncertainty_text) <> '' AND char_length(uncertainty_text) <= 1024)),
    author_confidence numeric(3, 2) NOT NULL CHECK (author_confidence BETWEEN 0 AND 1),
    authoring_bead_id uuid NOT NULL,
    semantic_task_id uuid NOT NULL,
    semantic_attempt_id uuid NOT NULL,
    semantic_run_id text NOT NULL,
    authored_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, relation_id),
    CHECK (source_bead_id <> target_bead_id),
    CHECK (authoring_bead_id IN (source_bead_id, target_bead_id)),
    CONSTRAINT bead_relations_source_fk FOREIGN KEY (tenant_id, workspace_id, source_access_scope_id, source_bead_id, source_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT bead_relations_target_fk FOREIGN KEY (tenant_id, workspace_id, target_access_scope_id, target_bead_id, target_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT bead_relations_run_fk FOREIGN KEY (tenant_id, semantic_attempt_id, semantic_run_id)
        REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id),
    CONSTRAINT bead_relations_author_fk FOREIGN KEY (tenant_id, authored_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id)
);
CREATE INDEX bead_relations_source_idx ON memoriesql.bead_relations (tenant_id, source_bead_id);
CREATE INDEX bead_relations_target_idx ON memoriesql.bead_relations (tenant_id, target_bead_id);

CREATE TABLE memoriesql.bead_relation_statements (
    tenant_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    endpoint text NOT NULL CHECK (endpoint IN ('source', 'target')),
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    statement_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, relation_id, statement_id),
    FOREIGN KEY (tenant_id, relation_id) REFERENCES memoriesql.bead_relations (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, statement_id)
        REFERENCES memoriesql.bead_semantic_statements (tenant_id, workspace_id, access_scope_id, statement_id)
);

CREATE TABLE memoriesql.bead_relation_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    relation_event_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('confirm', 'dispute', 'retract', 'supersede')),
    replacement_relation_id uuid,
    reason text NOT NULL CHECK (btrim(reason) <> '' AND char_length(reason) <= 1024),
    origin text NOT NULL CHECK (origin = 'governed'),
    idempotency_receipt_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    effective_at timestamp with time zone,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, relation_event_id),
    FOREIGN KEY (tenant_id, relation_id) REFERENCES memoriesql.bead_relations (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, replacement_relation_id) REFERENCES memoriesql.bead_relations (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, recorded_by_principal_id) REFERENCES memoriesql.principals (tenant_id, principal_id),
    FOREIGN KEY (tenant_id, idempotency_receipt_id) REFERENCES memoriesql.idempotency_receipts (tenant_id, idempotency_receipt_id),
    CHECK ((action = 'supersede') = (replacement_relation_id IS NOT NULL)),
    CHECK (replacement_relation_id IS DISTINCT FROM relation_id)
);
CREATE INDEX bead_relation_events_relation_idx ON memoriesql.bead_relation_events (tenant_id, relation_id, recorded_at);

-- Every supplied candidate is assessed with an edge, without one, or not at all.
-- Unassessed is never unrelated.
CREATE TABLE memoriesql.relation_candidate_assessments (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    authoring_bead_id uuid NOT NULL,
    authoring_bead_version_id uuid NOT NULL,
    candidate_access_scope_id uuid NOT NULL,
    candidate_bead_id uuid NOT NULL,
    candidate_bead_version_id uuid NOT NULL,
    assessment text NOT NULL CHECK (assessment IN ('edge', 'no_edge', 'unassessed')),
    reason_text text CHECK (reason_text IS NULL OR (btrim(reason_text) <> '' AND char_length(reason_text) <= 1024)),
    semantic_task_id uuid NOT NULL,
    semantic_attempt_id uuid NOT NULL,
    semantic_run_id text NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, authoring_bead_id, candidate_bead_id),
    CHECK (assessment <> 'unassessed' OR reason_text IS NOT NULL),
    CHECK (authoring_bead_id <> candidate_bead_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, authoring_bead_id, authoring_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (tenant_id, workspace_id, candidate_access_scope_id, candidate_bead_id, candidate_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, semantic_attempt_id, semantic_run_id)
        REFERENCES memoriesql.semantic_task_runs (tenant_id, attempt_id, run_id)
);

-- Typed evidence for relations and lifecycle events: an exact existing statement-evidence
-- pair. Derivation roots are computed deterministically as of a known time on read.
CREATE TABLE memoriesql.semantic_evidence_links (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    evidence_link_id uuid NOT NULL,
    owner_kind text NOT NULL CHECK (owner_kind IN ('relation', 'claim_event', 'relation_event')),
    owner_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('supports', 'contradicts', 'context')),
    statement_id uuid NOT NULL,
    evidence_source_unit_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    PRIMARY KEY (tenant_id, evidence_link_id),
    UNIQUE (tenant_id, owner_kind, owner_id, statement_id, evidence_source_unit_id),
    FOREIGN KEY (tenant_id, statement_id, evidence_source_unit_id)
        REFERENCES memoriesql.bead_semantic_statement_evidence (tenant_id, statement_id, evidence_source_unit_id)
);
CREATE INDEX semantic_evidence_links_owner_idx ON memoriesql.semantic_evidence_links (tenant_id, owner_kind, owner_id);

-- Claims and authored relations exist only in their authoring bead's acceptance
-- transaction; the seal then freezes them with the rest of its meaning.
CREATE TRIGGER accepted_claim_guard BEFORE INSERT ON memoriesql.bead_claims
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_accepted_bead_semantics();

CREATE FUNCTION memoriesql.guard_authored_relation_record()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
DECLARE authoring uuid;
BEGIN
    IF TG_TABLE_NAME = 'bead_claim_events' THEN
        IF NEW.origin <> 'authored' THEN RETURN NEW; END IF;
    END IF;
    authoring := NEW.authoring_bead_id;
    IF EXISTS (
        SELECT 1 FROM memoriesql.accepted_bead_semantics AS accepted
        WHERE accepted.tenant_id = NEW.tenant_id AND accepted.bead_id = authoring
    ) THEN
        RAISE EXCEPTION 'accepted_bead_immutable: authored relations need a new authoring bead'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER authored_relation_guard BEFORE INSERT ON memoriesql.bead_relations
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_authored_relation_record();
CREATE TRIGGER authored_assessment_guard BEFORE INSERT ON memoriesql.relation_candidate_assessments
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_authored_relation_record();
CREATE TRIGGER authored_claim_event_guard BEFORE INSERT ON memoriesql.bead_claim_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_authored_relation_record();

-- Endpoint and claim propositions join their parent only before the authoring bead seals.
CREATE FUNCTION memoriesql.guard_authored_statement_link()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
DECLARE authoring uuid;
BEGIN
    IF TG_TABLE_NAME = 'bead_claim_statements' THEN
        SELECT bead_id INTO authoring FROM memoriesql.bead_claims
        WHERE tenant_id = NEW.tenant_id AND claim_id = NEW.claim_id;
    ELSE
        SELECT authoring_bead_id INTO authoring FROM memoriesql.bead_relations
        WHERE tenant_id = NEW.tenant_id AND relation_id = NEW.relation_id;
    END IF;
    IF authoring IS NULL OR EXISTS (
        SELECT 1 FROM memoriesql.accepted_bead_semantics AS accepted
        WHERE accepted.tenant_id = NEW.tenant_id AND accepted.bead_id = authoring
    ) THEN
        RAISE EXCEPTION 'accepted_bead_immutable: propositions join only before acceptance'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER authored_claim_statement_guard BEFORE INSERT ON memoriesql.bead_claim_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_authored_statement_link();
CREATE TRIGGER authored_relation_statement_guard BEFORE INSERT ON memoriesql.bead_relation_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_authored_statement_link();

CREATE TRIGGER relation_types_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_types
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_type_revisions_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_type_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_type_candidates_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_type_candidates
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_type_decisions_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_type_decisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_claims_immutable BEFORE UPDATE OR DELETE ON memoriesql.bead_claims
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_claim_statements_immutable BEFORE UPDATE OR DELETE ON memoriesql.bead_claim_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_claim_events_immutable BEFORE UPDATE OR DELETE ON memoriesql.bead_claim_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_relations_immutable BEFORE UPDATE OR DELETE ON memoriesql.bead_relations
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_relation_statements_immutable BEFORE UPDATE OR DELETE ON memoriesql.bead_relation_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_relation_events_immutable BEFORE UPDATE OR DELETE ON memoriesql.bead_relation_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER relation_candidate_assessments_immutable BEFORE UPDATE OR DELETE ON memoriesql.relation_candidate_assessments
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER semantic_evidence_links_immutable BEFORE UPDATE OR DELETE ON memoriesql.semantic_evidence_links
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Definer functions own every read and write; roles receive no table grants.
ALTER TABLE memoriesql.relation_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_types FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_type_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_type_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_type_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_type_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_type_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_type_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_claims FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_claim_statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_claim_statements FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_claim_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_claim_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_relations FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_relation_statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_relation_statements FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_relation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_relation_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_candidate_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.relation_candidate_assessments FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_evidence_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_evidence_links FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.relation_types, memoriesql.relation_type_revisions,
    memoriesql.relation_type_candidates, memoriesql.relation_type_decisions,
    memoriesql.bead_claims, memoriesql.bead_claim_statements, memoriesql.bead_claim_events,
    memoriesql.bead_relations, memoriesql.bead_relation_statements, memoriesql.bead_relation_events,
    memoriesql.relation_candidate_assessments, memoriesql.semantic_evidence_links FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.guard_authored_relation_record() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.guard_authored_statement_link() FROM PUBLIC;

COMMENT ON TABLE memoriesql.bead_claims IS
    'Optional authored tracked propositions of one accepted bead, bound to its statements. Currentness is derived from lifecycle events and bead lineage, never from recency.';
COMMENT ON TABLE memoriesql.bead_relations IS
    'Authored bead-to-bead relations between pinned accepted versions with endpoint propositions, evidence, basis, rationale and diagnostic author confidence. Not causal truth.';
COMMENT ON TABLE memoriesql.semantic_evidence_links IS
    'Exact statement-evidence pairs. Derivation roots are computed as of a known time; shared roots are dependent evidence, never independent corroboration.';

-- The current relation type revision, or NULL when the latest revision is inactive.
CREATE FUNCTION memoriesql.relation_type_active_revision(t uuid, w uuid, key text, rev integer)
RETURNS memoriesql.relation_type_revisions
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT r.* FROM memoriesql.relation_types AS ty
    JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
    WHERE ty.type_key = key AND r.revision = rev AND r.status = 'active'
      AND (ty.tenant_id IS NULL OR (ty.tenant_id = t AND ty.workspace_id = w))
      AND r.revision = (SELECT max(latest.revision) FROM memoriesql.relation_type_revisions AS latest
                        WHERE latest.relation_type_id = ty.relation_type_id)
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_type_active_revision(uuid, uuid, text, integer) FROM PUBLIC;

-- A relation's lifecycle as of a known time. Retraction is terminal; a supersession
-- counts while its replacement is not retracted; dispute stays open until confirmed.
CREATE FUNCTION memoriesql.bead_relation_state_v1(t uuid, relation uuid, known timestamp with time zone)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    WITH rel AS (
        SELECT * FROM memoriesql.bead_relations WHERE tenant_id = t AND relation_id = relation AND recorded_at <= known
    ), ev AS (
        SELECT e.* FROM memoriesql.bead_relation_events AS e
        WHERE e.tenant_id = t AND e.relation_id = relation AND e.recorded_at <= known
    ), replacements AS (
        SELECT e.replacement_relation_id AS id FROM ev AS e
        WHERE e.action = 'supersede' AND NOT EXISTS (
            SELECT 1 FROM memoriesql.bead_relation_events AS x
            WHERE x.tenant_id = t AND x.relation_id = e.replacement_relation_id
              AND x.action = 'retract' AND x.recorded_at <= known)
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
REVOKE ALL ON FUNCTION memoriesql.bead_relation_state_v1(uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- A claim's lifecycle as of a known time. Neither recency nor write order selects a
-- winner: competing supersessions stay visible and an open dispute stays disputed.
CREATE FUNCTION memoriesql.bead_claim_state_v1(t uuid, claim uuid, known timestamp with time zone)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    WITH c AS (
        SELECT * FROM memoriesql.bead_claims WHERE tenant_id = t AND claim_id = claim AND recorded_at <= known
    ), ev AS (
        SELECT e.* FROM memoriesql.bead_claim_events AS e
        WHERE e.tenant_id = t AND e.recorded_at <= known
          AND (e.claim_id = claim OR (e.related_claim_id = claim AND e.action IN ('dispute', 'resolve_dispute')))
    ), retracted_ids AS (
        SELECT DISTINCT x.claim_id FROM memoriesql.bead_claim_events AS x
        WHERE x.tenant_id = t AND x.action = 'retract' AND x.recorded_at <= known
    ), superseders AS (
        SELECT DISTINCT e.related_claim_id AS id FROM ev AS e
        WHERE e.claim_id = claim AND e.action = 'supersede'
          AND e.related_claim_id NOT IN (SELECT claim_id FROM retracted_ids)
    ), disputes AS (
        SELECT CASE WHEN e.claim_id = claim THEN e.related_claim_id ELSE e.claim_id END AS other, e.recorded_at
        FROM ev AS e WHERE e.action = 'dispute'
    ), resolutions AS (
        SELECT CASE WHEN e.claim_id = claim THEN e.related_claim_id ELSE e.claim_id END AS other, e.recorded_at
        FROM ev AS e WHERE e.action = 'resolve_dispute'
    ), open_disputes AS (
        SELECT DISTINCT d.other FROM disputes AS d
        WHERE d.other NOT IN (SELECT claim_id FROM retracted_ids)
          AND NOT EXISTS (SELECT 1 FROM resolutions AS r WHERE r.other = d.other AND r.recorded_at >= d.recorded_at)
    ), corrections AS (
        SELECT DISTINCT s.bead_id FROM c
        JOIN memoriesql.bead_supersessions AS s ON s.tenant_id = t AND s.superseded_bead_id = c.bead_id
        JOIN memoriesql.bead_versions AS v ON v.tenant_id = t AND v.bead_version_id = s.bead_version_id
        WHERE v.authored_at <= known
    )
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM c) THEN NULL ELSE jsonb_build_object(
        'state', CASE
            WHEN EXISTS (SELECT 1 FROM ev WHERE claim_id = claim AND action = 'retract') THEN 'retracted'
            WHEN EXISTS (SELECT 1 FROM superseders) THEN 'superseded'
            WHEN EXISTS (SELECT 1 FROM open_disputes) THEN 'disputed'
            ELSE 'current' END,
        'superseded_by', COALESCE((SELECT jsonb_agg(id ORDER BY id) FROM superseders), '[]'::jsonb),
        'disputed_with', COALESCE((SELECT jsonb_agg(other ORDER BY other) FROM open_disputes), '[]'::jsonb),
        'origin_corrected_by', COALESCE((SELECT jsonb_agg(bead_id ORDER BY bead_id) FROM corrections), '[]'::jsonb)
    ) END
$$;
REVOKE ALL ON FUNCTION memoriesql.bead_claim_state_v1(uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- Active derived_from targets of one bead as known at a time. Retracted and superseded
-- provenance no longer applies; disputed or reassessment-pending provenance still does.
CREATE FUNCTION memoriesql.derived_from_targets(t uuid, bead uuid, known timestamp with time zone)
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT r.target_bead_id FROM memoriesql.bead_relations AS r
    JOIN memoriesql.relation_type_revisions AS tr ON tr.relation_type_revision_id = r.relation_type_revision_id
    WHERE r.tenant_id = t AND r.source_bead_id = bead AND r.recorded_at <= known
      AND tr.relation_type_id = '30000000-0000-4000-8000-000000000009'
      AND memoriesql.bead_relation_state_v1(t, r.relation_id, known)->>'state' NOT IN ('retracted', 'superseded')
$$;
REVOKE ALL ON FUNCTION memoriesql.derived_from_targets(uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- Deterministic derivation roots of a bead as known at a time: the source objects of the
-- terminal beads of its active derived_from chains, or its own source object. A bounded or
-- cyclic chain falls back to every visited bead. Transformation never adds a root.
CREATE FUNCTION memoriesql.bead_derivation_roots(t uuid, bead uuid, known timestamp with time zone)
RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    WITH RECURSIVE walk(bead_id, depth, path) AS (
        SELECT bead, 0, ARRAY[bead]
        UNION ALL
        SELECT d.target, w.depth + 1, w.path || d.target
        FROM walk AS w CROSS JOIN LATERAL memoriesql.derived_from_targets(t, w.bead_id, known) AS d(target)
        WHERE w.depth < 8 AND NOT d.target = ANY(w.path)
    ), visited AS (
        SELECT DISTINCT w.bead_id, e.source_object_id,
               NOT EXISTS (SELECT 1 FROM memoriesql.derived_from_targets(t, w.bead_id, known)) AS terminal
        FROM walk AS w
        JOIN memoriesql.beads AS b ON b.tenant_id = t AND b.bead_id = w.bead_id
        JOIN memoriesql.source_events AS e ON e.tenant_id = t AND e.event_id = b.event_id
    )
    SELECT CASE WHEN EXISTS (SELECT 1 FROM visited WHERE terminal)
        THEN ARRAY(SELECT DISTINCT source_object_id FROM visited WHERE terminal ORDER BY 1 LIMIT 64)
        ELSE ARRAY(SELECT DISTINCT source_object_id FROM visited ORDER BY 1 LIMIT 64) END
$$;
REVOKE ALL ON FUNCTION memoriesql.bead_derivation_roots(uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- Roots of one evidence unit as known at a time: the union over beads observing it, or
-- the unit's own source object when no bead observed it yet.
CREATE FUNCTION memoriesql.source_unit_derivation_roots(t uuid, unit uuid, known timestamp with time zone)
RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT COALESCE(NULLIF(ARRAY(
        SELECT DISTINCT root FROM memoriesql.beads AS b,
             unnest(memoriesql.bead_derivation_roots(t, b.bead_id, known)) AS root
        WHERE b.tenant_id = t AND b.source_unit_id = unit AND b.created_at <= known ORDER BY 1 LIMIT 64
    ), '{}'::uuid[]), ARRAY(
        SELECT e.source_object_id FROM memoriesql.source_units AS u
        JOIN memoriesql.source_events AS e ON e.tenant_id = u.tenant_id AND e.event_id = u.event_id
        WHERE u.tenant_id = t AND u.source_unit_id = unit
    ))
$$;
REVOKE ALL ON FUNCTION memoriesql.source_unit_derivation_roots(uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- Independent roots across an owner's evidence as known at a time: the size of their
-- union. A shared root counts once; a multi-root derivative adds no root of its own.
CREATE FUNCTION memoriesql.evidence_independent_root_count(t uuid, owner_kind_value text, owner uuid, known timestamp with time zone)
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT count(DISTINCT root)::integer FROM memoriesql.semantic_evidence_links AS l,
           unnest(memoriesql.source_unit_derivation_roots(t, l.evidence_source_unit_id, known)) AS root
    WHERE l.tenant_id = t AND l.owner_kind = owner_kind_value AND l.owner_id = owner AND l.recorded_at <= known
$$;
REVOKE ALL ON FUNCTION memoriesql.evidence_independent_root_count(uuid, text, uuid, timestamp with time zone) FROM PUBLIC;

-- Record one exact statement-evidence link. Callers authorize first.
CREATE FUNCTION memoriesql.record_semantic_evidence_link(
    t uuid, w uuid, owner_kind_value text, owner uuid, role_value text,
    statement uuid, unit uuid, at timestamp with time zone
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
BEGIN
    INSERT INTO memoriesql.semantic_evidence_links (
        tenant_id, workspace_id, evidence_link_id, owner_kind, owner_id, role,
        statement_id, evidence_source_unit_id, recorded_at
    ) VALUES (
        t, w, pg_catalog.uuidv7(), owner_kind_value, owner, role_value, statement, unit, at
    ) ON CONFLICT DO NOTHING;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.record_semantic_evidence_link(uuid, uuid, text, uuid, text, uuid, uuid, timestamp with time zone) FROM PUBLIC;

-- Maintain authority over one accepted bead version: write on its scope, and
-- maintain-read of its event, every statement and every supporting evidence event.
CREATE FUNCTION memoriesql.lifecycle_bead_authorized(t uuid, w uuid, scope uuid, version uuid)
RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT memoriesql.current_context_scope_authorized(scope, 'memory.maintain', 'write')
       AND memoriesql.current_context_scope_time_authorized(scope, 'write', pg_catalog.clock_timestamp())
       AND memoriesql.current_context_accepted_bead_maintain_authorized(t, w, scope, version)
$$;
REVOKE ALL ON FUNCTION memoriesql.lifecycle_bead_authorized(uuid, uuid, uuid, uuid) FROM PUBLIC;

-- One lifecycle evidence item: an existing statement-evidence pair of an accepted
-- bead in the workspace, with its exact content hash, both events maintain-readable.
CREATE FUNCTION memoriesql.lifecycle_evidence_authorized(t uuid, w uuid, item jsonb)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT jsonb_typeof(item) = 'object'
       AND item - ARRAY['statement_id', 'source_unit_id', 'content_hash'] = '{}'::jsonb
       AND COALESCE(item->>'content_hash' ~ '^[a-f0-9]{64}$', false)
       AND EXISTS (
        SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS e
        JOIN memoriesql.bead_semantic_statements AS s
          ON s.tenant_id = e.tenant_id AND s.statement_id = e.statement_id
        JOIN memoriesql.accepted_bead_semantics AS a
          ON a.tenant_id = s.tenant_id AND a.bead_id = s.bead_id
        WHERE e.tenant_id = t AND s.workspace_id = w
          AND e.statement_id = (item->>'statement_id')::uuid
          AND e.evidence_source_unit_id = (item->>'source_unit_id')::uuid
          AND e.evidence_content_hash = item->>'content_hash'
          AND memoriesql.current_context_event_authorized(s.access_scope_id, s.event_id, 'memory.maintain', 'read')
          AND memoriesql.current_context_event_authorized(e.access_scope_id, e.evidence_event_id, 'memory.maintain', 'read'))
$$;
REVOKE ALL ON FUNCTION memoriesql.lifecycle_evidence_authorized(uuid, uuid, jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.record_claim_event_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    target memoriesql.bead_claims%ROWTYPE;
    related memoriesql.bead_claims%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE;
    item jsonb; state jsonb; response jsonb; latest uuid; lock_id uuid;
    rid uuid := pg_catalog.uuidv7();
    eid uuid := pg_catalog.uuidv7();
    action_value text := request->>'action';
    request_hash text;
    started timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'claim_id',
            'action', 'related_claim_id', 'reason', 'evidence', 'effective_at', 'expected_last_event_id'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1'
       OR request->>'expected_schema_version' IS DISTINCT FROM '27'
       OR octet_length(request::text) > 16384
       OR COALESCE(char_length(request->>'idempotency_key'), 0) NOT BETWEEN 1 AND 512
       OR COALESCE(action_value, '') NOT IN ('reaffirm', 'supersede', 'retract', 'dispute', 'resolve_dispute')
       OR COALESCE(btrim(request->>'reason'), '') = '' OR char_length(request->>'reason') > 1024
       OR (action_value IN ('supersede', 'dispute', 'resolve_dispute'))
            IS DISTINCT FROM COALESCE(jsonb_typeof(request->'related_claim_id') = 'string', false)
       OR request->>'related_claim_id' IS NOT DISTINCT FROM request->>'claim_id'
       OR COALESCE(request->>'claim_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR (request ? 'related_claim_id' AND jsonb_typeof(request->'related_claim_id') NOT IN ('string', 'null'))
       OR (jsonb_typeof(request->'related_claim_id') = 'string' AND request->>'related_claim_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (request ? 'expected_last_event_id' AND jsonb_typeof(request->'expected_last_event_id') NOT IN ('string', 'null'))
       OR (jsonb_typeof(request->'expected_last_event_id') = 'string' AND request->>'expected_last_event_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (request ? 'effective_at' AND jsonb_typeof(request->'effective_at') NOT IN ('string', 'null'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(request->'evidence') = 'array'
                                                          THEN request->'evidence' ELSE '[]'::jsonb END) AS x(v)
                  WHERE jsonb_typeof(v) IS DISTINCT FROM 'object' OR COALESCE(v->>'statement_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                     OR COALESCE(v->>'source_unit_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR COALESCE(jsonb_typeof(request->'evidence'), '') <> 'array'
       OR jsonb_array_length(request->'evidence') > 8 THEN
        RAISE EXCEPTION 'invalid_claim_lifecycle_event' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    -- Governed judgments are human acts; services and agents author only through bundles.
    IF NOT FOUND OR c.principal_kind <> 'human' THEN
        RAISE EXCEPTION 'claim_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    -- Shared tenant authority fence: settle against concurrent revocation.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(c.tenant_id::text || ':semantic_outcome_authority:', 0));
    SELECT * INTO target FROM memoriesql.bead_claims
    WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id AND claim_id = (request->>'claim_id')::uuid;
    IF NOT FOUND OR NOT memoriesql.lifecycle_bead_authorized(
            c.tenant_id, c.workspace_id, target.access_scope_id, target.bead_version_id) THEN
        RAISE EXCEPTION 'claim_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    IF request->>'related_claim_id' IS NOT NULL THEN
        SELECT * INTO related FROM memoriesql.bead_claims
        WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id
          AND claim_id = (request->>'related_claim_id')::uuid;
        IF NOT FOUND OR NOT memoriesql.lifecycle_bead_authorized(
                c.tenant_id, c.workspace_id, related.access_scope_id, related.bead_version_id) THEN
            RAISE EXCEPTION 'claim_lifecycle_unavailable' USING ERRCODE = '42501';
        END IF;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
        IF NOT memoriesql.lifecycle_evidence_authorized(c.tenant_id, c.workspace_id, item) THEN
            RAISE EXCEPTION 'claim_lifecycle_evidence_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
    IF (SELECT count(*) <> count(DISTINCT value->>'statement_id') FROM jsonb_array_elements(request->'evidence')) THEN
        RAISE EXCEPTION 'invalid_claim_lifecycle_event' USING ERRCODE = '22023';
    END IF;

    request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(request::text, 'UTF8')), 'hex');
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':bead_claim.event.v1:' || (request->>'idempotency_key'), 0));
    SELECT * INTO old FROM memoriesql.idempotency_receipts
    WHERE tenant_id = c.tenant_id AND operation_kind = 'bead_claim.event.v1'
      AND idempotency_key = request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash <> request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF old.status <> 'succeeded' OR old.response_receipt IS NULL THEN
            RAISE EXCEPTION 'claim lifecycle receipt is incomplete' USING ERRCODE = '55000';
        END IF;
        RETURN old.response_receipt || '{"replayed":true}'::jsonb;
    END IF;

    -- Lock both claims in a deterministic order, then compare-and-swap on the latest event.
    FOR lock_id IN SELECT DISTINCT x FROM unnest(ARRAY[target.claim_id, related.claim_id]) AS x
                  WHERE x IS NOT NULL ORDER BY x LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            c.tenant_id::text || ':bead-claim:' || lock_id::text, 0));
    END LOOP;
    IF NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.access_scope_id, target.bead_version_id)
       OR (related.claim_id IS NOT NULL AND NOT memoriesql.lifecycle_bead_authorized(
            c.tenant_id, c.workspace_id, related.access_scope_id, related.bead_version_id)) THEN
        RAISE EXCEPTION 'claim_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT claim_event_id INTO latest FROM memoriesql.bead_claim_events
    WHERE tenant_id = c.tenant_id AND claim_id = target.claim_id
    ORDER BY recorded_at DESC, claim_event_id DESC LIMIT 1;
    IF latest IS DISTINCT FROM (request->>'expected_last_event_id')::uuid THEN
        RAISE EXCEPTION 'claim_lifecycle_conflict' USING ERRCODE = '40001';
    END IF;
    state := memoriesql.bead_claim_state_v1(c.tenant_id, target.claim_id, pg_catalog.clock_timestamp());
    IF state->>'state' = 'retracted' THEN
        RAISE EXCEPTION 'claim_retracted' USING ERRCODE = '55000';
    END IF;
    IF related.claim_id IS NOT NULL AND memoriesql.bead_claim_state_v1(
            c.tenant_id, related.claim_id, pg_catalog.clock_timestamp())->>'state' = 'retracted' THEN
        RAISE EXCEPTION 'related_claim_retracted' USING ERRCODE = '55000';
    END IF;
    IF action_value = 'resolve_dispute' AND NOT (state->'disputed_with') @> to_jsonb(related.claim_id) THEN
        RAISE EXCEPTION 'claim_dispute_not_open' USING ERRCODE = '55000';
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id, operation_kind,
        idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        c.tenant_id, c.workspace_id, target.access_scope_id, rid, 'bead_claim.event.v1',
        request->>'idempotency_key', request_hash, 'in_progress', 'bead_claim', target.claim_id, 1, started, started
    );
    INSERT INTO memoriesql.bead_claim_events (
        tenant_id, workspace_id, claim_event_id, claim_id, action, related_claim_id, reason, origin,
        idempotency_receipt_id, recorded_by_principal_id, effective_at, recorded_at
    ) VALUES (
        c.tenant_id, c.workspace_id, eid, target.claim_id, action_value, related.claim_id,
        request->>'reason', 'governed', rid, c.principal_id,
        NULLIF(request->>'effective_at', '')::timestamp with time zone, pg_catalog.clock_timestamp()
    );
    FOR item IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
        PERFORM memoriesql.record_semantic_evidence_link(c.tenant_id, c.workspace_id, 'claim_event', eid,
            'supports', (item->>'statement_id')::uuid, (item->>'source_unit_id')::uuid, pg_catalog.clock_timestamp());
    END LOOP;
    response := jsonb_build_object('contract_version', 1, 'claim_event_id', eid, 'claim_id', target.claim_id,
        'action', action_value, 'idempotency_receipt_id', rid);
    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id, idempotency_receipt_id, aggregate_kind,
        aggregate_id, event_kind, payload, headers, recorded_at, available_at
    ) VALUES (
        c.tenant_id, c.workspace_id, target.access_scope_id, pg_catalog.uuidv7(), rid, 'bead_claim',
        target.claim_id, 'bead_claim.event.v1', response, '{"contract_version":1}'::jsonb,
        pg_catalog.clock_timestamp(), pg_catalog.clock_timestamp()
    );
    UPDATE memoriesql.idempotency_receipts SET status = 'succeeded', response_receipt = response,
        updated_at = pg_catalog.clock_timestamp(), completed_at = pg_catalog.clock_timestamp()
    WHERE tenant_id = c.tenant_id AND idempotency_receipt_id = rid;
    RETURN response || '{"replayed":false}'::jsonb;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.record_claim_event_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_claim_event_v1(jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.record_relation_event_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    target memoriesql.bead_relations%ROWTYPE;
    replacement memoriesql.bead_relations%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE;
    item jsonb; response jsonb; latest uuid; lock_id uuid;
    rid uuid := pg_catalog.uuidv7();
    eid uuid := pg_catalog.uuidv7();
    action_value text := request->>'action';
    request_hash text;
    started timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'relation_id',
            'action', 'replacement_relation_id', 'reason', 'evidence', 'effective_at', 'expected_last_event_id'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1'
       OR request->>'expected_schema_version' IS DISTINCT FROM '27'
       OR octet_length(request::text) > 16384
       OR COALESCE(char_length(request->>'idempotency_key'), 0) NOT BETWEEN 1 AND 512
       OR COALESCE(action_value, '') NOT IN ('confirm', 'dispute', 'retract', 'supersede')
       OR COALESCE(btrim(request->>'reason'), '') = '' OR char_length(request->>'reason') > 1024
       OR (action_value = 'supersede')
            IS DISTINCT FROM COALESCE(jsonb_typeof(request->'replacement_relation_id') = 'string', false)
       OR request->>'replacement_relation_id' IS NOT DISTINCT FROM request->>'relation_id'
       OR COALESCE(request->>'relation_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR (request ? 'replacement_relation_id' AND jsonb_typeof(request->'replacement_relation_id') NOT IN ('string', 'null'))
       OR (jsonb_typeof(request->'replacement_relation_id') = 'string' AND request->>'replacement_relation_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (request ? 'expected_last_event_id' AND jsonb_typeof(request->'expected_last_event_id') NOT IN ('string', 'null'))
       OR (jsonb_typeof(request->'expected_last_event_id') = 'string' AND request->>'expected_last_event_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (request ? 'effective_at' AND jsonb_typeof(request->'effective_at') NOT IN ('string', 'null'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(request->'evidence') = 'array'
                                                          THEN request->'evidence' ELSE '[]'::jsonb END) AS x(v)
                  WHERE jsonb_typeof(v) IS DISTINCT FROM 'object' OR COALESCE(v->>'statement_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                     OR COALESCE(v->>'source_unit_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR COALESCE(jsonb_typeof(request->'evidence'), '') <> 'array'
       OR jsonb_array_length(request->'evidence') > 8 THEN
        RAISE EXCEPTION 'invalid_relation_lifecycle_event' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR c.principal_kind <> 'human' THEN
        RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(c.tenant_id::text || ':semantic_outcome_authority:', 0));
    SELECT * INTO target FROM memoriesql.bead_relations
    WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id AND relation_id = (request->>'relation_id')::uuid;
    IF NOT FOUND
       OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.source_access_scope_id, target.source_bead_version_id)
       OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.target_access_scope_id, target.target_bead_version_id) THEN
        RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    IF request->>'replacement_relation_id' IS NOT NULL THEN
        SELECT * INTO replacement FROM memoriesql.bead_relations
        WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id
          AND relation_id = (request->>'replacement_relation_id')::uuid;
        IF NOT FOUND
           OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, replacement.source_access_scope_id, replacement.source_bead_version_id)
           OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, replacement.target_access_scope_id, replacement.target_bead_version_id) THEN
            RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
        END IF;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
        IF NOT memoriesql.lifecycle_evidence_authorized(c.tenant_id, c.workspace_id, item) THEN
            RAISE EXCEPTION 'relation_lifecycle_evidence_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
    IF (SELECT count(*) <> count(DISTINCT value->>'statement_id') FROM jsonb_array_elements(request->'evidence')) THEN
        RAISE EXCEPTION 'invalid_relation_lifecycle_event' USING ERRCODE = '22023';
    END IF;

    request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(request::text, 'UTF8')), 'hex');
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':bead_relation.event.v1:' || (request->>'idempotency_key'), 0));
    SELECT * INTO old FROM memoriesql.idempotency_receipts
    WHERE tenant_id = c.tenant_id AND operation_kind = 'bead_relation.event.v1'
      AND idempotency_key = request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash <> request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        IF old.status <> 'succeeded' OR old.response_receipt IS NULL THEN
            RAISE EXCEPTION 'relation lifecycle receipt is incomplete' USING ERRCODE = '55000';
        END IF;
        RETURN old.response_receipt || '{"replayed":true}'::jsonb;
    END IF;

    FOR lock_id IN SELECT DISTINCT x FROM unnest(ARRAY[target.relation_id, replacement.relation_id]) AS x
                  WHERE x IS NOT NULL ORDER BY x LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            c.tenant_id::text || ':bead-relation:' || lock_id::text, 0));
    END LOOP;
    IF NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.source_access_scope_id, target.source_bead_version_id)
       OR NOT memoriesql.lifecycle_bead_authorized(c.tenant_id, c.workspace_id, target.target_access_scope_id, target.target_bead_version_id) THEN
        RAISE EXCEPTION 'relation_lifecycle_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT relation_event_id INTO latest FROM memoriesql.bead_relation_events
    WHERE tenant_id = c.tenant_id AND relation_id = target.relation_id
    ORDER BY recorded_at DESC, relation_event_id DESC LIMIT 1;
    IF latest IS DISTINCT FROM (request->>'expected_last_event_id')::uuid THEN
        RAISE EXCEPTION 'relation_lifecycle_conflict' USING ERRCODE = '40001';
    END IF;
    IF memoriesql.bead_relation_state_v1(c.tenant_id, target.relation_id, pg_catalog.clock_timestamp())->>'state' = 'retracted' THEN
        RAISE EXCEPTION 'relation_retracted' USING ERRCODE = '55000';
    END IF;
    IF replacement.relation_id IS NOT NULL AND memoriesql.bead_relation_state_v1(
            c.tenant_id, replacement.relation_id, pg_catalog.clock_timestamp())->>'state' = 'retracted' THEN
        RAISE EXCEPTION 'replacement_relation_retracted' USING ERRCODE = '55000';
    END IF;

    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id, operation_kind,
        idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        c.tenant_id, c.workspace_id, target.source_access_scope_id, rid, 'bead_relation.event.v1',
        request->>'idempotency_key', request_hash, 'in_progress', 'bead_relation', target.relation_id, 1, started, started
    );
    INSERT INTO memoriesql.bead_relation_events (
        tenant_id, workspace_id, relation_event_id, relation_id, action, replacement_relation_id, reason,
        origin, idempotency_receipt_id, recorded_by_principal_id, effective_at, recorded_at
    ) VALUES (
        c.tenant_id, c.workspace_id, eid, target.relation_id, action_value, replacement.relation_id,
        request->>'reason', 'governed', rid, c.principal_id,
        NULLIF(request->>'effective_at', '')::timestamp with time zone, pg_catalog.clock_timestamp()
    );
    FOR item IN SELECT value FROM jsonb_array_elements(request->'evidence') LOOP
        PERFORM memoriesql.record_semantic_evidence_link(c.tenant_id, c.workspace_id, 'relation_event', eid,
            'supports', (item->>'statement_id')::uuid, (item->>'source_unit_id')::uuid, pg_catalog.clock_timestamp());
    END LOOP;
    response := jsonb_build_object('contract_version', 1, 'relation_event_id', eid,
        'relation_id', target.relation_id, 'action', action_value, 'idempotency_receipt_id', rid);
    INSERT INTO memoriesql.outbox_events (
        tenant_id, workspace_id, access_scope_id, outbox_event_id, idempotency_receipt_id, aggregate_kind,
        aggregate_id, event_kind, payload, headers, recorded_at, available_at
    ) VALUES (
        c.tenant_id, c.workspace_id, target.source_access_scope_id, pg_catalog.uuidv7(), rid, 'bead_relation',
        target.relation_id, 'bead_relation.event.v1', response, '{"contract_version":1}'::jsonb,
        pg_catalog.clock_timestamp(), pg_catalog.clock_timestamp()
    );
    UPDATE memoriesql.idempotency_receipts SET status = 'succeeded', response_receipt = response,
        updated_at = pg_catalog.clock_timestamp(), completed_at = pg_catalog.clock_timestamp()
    WHERE tenant_id = c.tenant_id AND idempotency_receipt_id = rid;
    RETURN response || '{"replayed":false}'::jsonb;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.record_relation_event_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.record_relation_event_v1(jsonb) TO memoriesql_application;

-- Workspace vocabulary governance. Built-in keys cannot be proposed or shadowed; a
-- proposal records a candidate only, and only an authorized decision materializes it.
CREATE FUNCTION memoriesql.relation_vocabulary_authorized(scope uuid)
RETURNS memoriesql.authorization_contexts
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT FOUND OR NOT memoriesql.current_context_has_capability('workspace.manage')
       OR NOT EXISTS (SELECT 1 FROM memoriesql.access_scopes AS s
                      WHERE s.tenant_id = c.tenant_id AND s.workspace_id = c.workspace_id
                        AND s.access_scope_id = scope AND s.status = 'active')
       OR NOT memoriesql.current_context_scope_permits(scope, 'write') THEN
        RAISE EXCEPTION 'relation_vocabulary_unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN c;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_vocabulary_authorized(uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.propose_relation_type_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE;
    rid uuid := pg_catalog.uuidv7();
    cid uuid := pg_catalog.uuidv7();
    next_revision integer;
    response jsonb;
    request_hash text;
    started timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'access_scope_id', 'key',
            'label', 'definition', 'forward_reading', 'inverse_reading', 'symmetric', 'status', 'reason'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1'
       OR request->>'expected_schema_version' IS DISTINCT FROM '27'
       OR octet_length(request::text) > 16384
       OR COALESCE(char_length(request->>'idempotency_key'), 0) NOT BETWEEN 1 AND 512
       OR COALESCE(request->>'access_scope_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR COALESCE(request->>'key' ~ '^[a-z][a-z0-9_]{0,63}$', false) IS NOT TRUE
       OR jsonb_typeof(request->'label') IS DISTINCT FROM 'string' OR btrim(request->>'label') = '' OR char_length(request->>'label') > 128
       OR jsonb_typeof(request->'definition') IS DISTINCT FROM 'string' OR btrim(request->>'definition') = '' OR char_length(request->>'definition') > 4096
       OR jsonb_typeof(request->'forward_reading') IS DISTINCT FROM 'string' OR btrim(request->>'forward_reading') = '' OR char_length(request->>'forward_reading') > 128
       OR jsonb_typeof(request->'inverse_reading') IS DISTINCT FROM 'string' OR btrim(request->>'inverse_reading') = '' OR char_length(request->>'inverse_reading') > 128
       OR char_length(request->>'reason') > 1024
       OR jsonb_typeof(request->'symmetric') IS DISTINCT FROM 'boolean'
       OR COALESCE(request->>'status', '') NOT IN ('active', 'inactive')
       OR COALESCE(btrim(request->>'reason'), '') = '' THEN
        RAISE EXCEPTION 'invalid_relation_type_proposal' USING ERRCODE = '22023';
    END IF;
    c := memoriesql.relation_vocabulary_authorized((request->>'access_scope_id')::uuid);
    IF EXISTS (SELECT 1 FROM memoriesql.relation_types WHERE namespace = 'memoriesql' AND type_key = request->>'key') THEN
        RAISE EXCEPTION 'built_in_relation_type_immutable' USING ERRCODE = '22023';
    END IF;
    request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(request::text, 'UTF8')), 'hex');
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':relation_type.propose.v1:' || (request->>'idempotency_key'), 0));
    SELECT * INTO old FROM memoriesql.idempotency_receipts
    WHERE tenant_id = c.tenant_id AND operation_kind = 'relation_type.propose.v1'
      AND idempotency_key = request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash <> request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        RETURN old.response_receipt || '{"replayed":true}'::jsonb;
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':relation-type:' || c.workspace_id::text || ':' || (request->>'key'), 0));
    SELECT COALESCE(max(r.revision), 0) + 1 INTO next_revision
    FROM memoriesql.relation_types AS ty
    JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
    WHERE ty.tenant_id = c.tenant_id AND ty.workspace_id = c.workspace_id AND ty.type_key = request->>'key';
    IF next_revision = 1 AND request->>'status' = 'inactive' THEN
        RAISE EXCEPTION 'invalid_relation_type_proposal' USING ERRCODE = '22023';
    END IF;
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id, operation_kind,
        idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        c.tenant_id, c.workspace_id, (request->>'access_scope_id')::uuid, rid, 'relation_type.propose.v1',
        request->>'idempotency_key', request_hash, 'in_progress', 'relation_type_candidate', cid, 1, started, started
    );
    INSERT INTO memoriesql.relation_type_candidates (
        tenant_id, workspace_id, candidate_id, type_key, proposed_revision, display_label, definition,
        forward_reading, inverse_reading, is_symmetric, status, reason,
        proposed_by_principal_id, idempotency_receipt_id, proposed_at
    ) VALUES (
        c.tenant_id, c.workspace_id, cid, request->>'key', next_revision, request->>'label', request->>'definition',
        request->>'forward_reading', request->>'inverse_reading', (request->>'symmetric')::boolean,
        request->>'status', request->>'reason', c.principal_id, rid, started
    );
    response := jsonb_build_object('contract_version', 1, 'candidate_id', cid, 'key', request->>'key',
        'proposed_revision', next_revision, 'idempotency_receipt_id', rid);
    UPDATE memoriesql.idempotency_receipts SET status = 'succeeded', response_receipt = response,
        updated_at = pg_catalog.clock_timestamp(), completed_at = pg_catalog.clock_timestamp()
    WHERE tenant_id = c.tenant_id AND idempotency_receipt_id = rid;
    RETURN response || '{"replayed":false}'::jsonb;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.propose_relation_type_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.propose_relation_type_v1(jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.decide_relation_type_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off SET lock_timeout = '500ms' AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    candidate memoriesql.relation_type_candidates%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE;
    rid uuid := pg_catalog.uuidv7();
    type_id uuid;
    revision_id uuid;
    latest integer;
    response jsonb;
    request_hash text;
    started timestamp with time zone := pg_catalog.clock_timestamp();
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['contract_version', 'expected_schema_version', 'idempotency_key', 'access_scope_id',
            'candidate_id', 'decision', 'reason'] <> '{}'::jsonb
       OR request->>'contract_version' IS DISTINCT FROM '1'
       OR request->>'expected_schema_version' IS DISTINCT FROM '27'
       OR COALESCE(char_length(request->>'idempotency_key'), 0) NOT BETWEEN 1 AND 512
       OR COALESCE(request->>'access_scope_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR COALESCE(request->>'candidate_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR COALESCE(request->>'decision', '') NOT IN ('accepted', 'rejected')
       OR COALESCE(btrim(request->>'reason'), '') = '' OR char_length(request->>'reason') > 1024 THEN
        RAISE EXCEPTION 'invalid_relation_type_decision' USING ERRCODE = '22023';
    END IF;
    c := memoriesql.relation_vocabulary_authorized((request->>'access_scope_id')::uuid);
    IF c.principal_kind <> 'human' THEN
        RAISE EXCEPTION 'relation_vocabulary_unavailable' USING ERRCODE = '42501';
    END IF;
    request_hash := encode(pg_catalog.sha256(pg_catalog.convert_to(request::text, 'UTF8')), 'hex');
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':relation_type.decide.v1:' || (request->>'idempotency_key'), 0));
    SELECT * INTO old FROM memoriesql.idempotency_receipts
    WHERE tenant_id = c.tenant_id AND operation_kind = 'relation_type.decide.v1'
      AND idempotency_key = request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash <> request_hash THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        RETURN old.response_receipt || '{"replayed":true}'::jsonb;
    END IF;
    SELECT * INTO candidate FROM memoriesql.relation_type_candidates
    WHERE tenant_id = c.tenant_id AND workspace_id = c.workspace_id AND candidate_id = (request->>'candidate_id')::uuid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'relation_vocabulary_unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        c.tenant_id::text || ':relation-type:' || c.workspace_id::text || ':' || candidate.type_key, 0));
    IF EXISTS (SELECT 1 FROM memoriesql.relation_type_decisions
               WHERE tenant_id = c.tenant_id AND candidate_id = candidate.candidate_id) THEN
        RAISE EXCEPTION 'relation_type_already_decided' USING ERRCODE = '55000';
    END IF;
    IF request->>'decision' = 'accepted' THEN
        SELECT ty.relation_type_id, COALESCE(max(r.revision), 0) INTO type_id, latest
        FROM memoriesql.relation_types AS ty
        LEFT JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
        WHERE ty.tenant_id = c.tenant_id AND ty.workspace_id = c.workspace_id AND ty.type_key = candidate.type_key
        GROUP BY ty.relation_type_id;
        IF COALESCE(latest, 0) <> candidate.proposed_revision - 1 THEN
            RAISE EXCEPTION 'relation_type_revision_conflict' USING ERRCODE = '40001';
        END IF;
        IF type_id IS NULL THEN
            type_id := pg_catalog.uuidv7();
            INSERT INTO memoriesql.relation_types (relation_type_id, tenant_id, workspace_id, namespace, type_key,
                introduced_in_schema_version, created_at)
            VALUES (type_id, c.tenant_id, c.workspace_id, 'workspace', candidate.type_key, 27, started);
        END IF;
        revision_id := pg_catalog.uuidv7();
        INSERT INTO memoriesql.relation_type_revisions (relation_type_revision_id, relation_type_id, revision,
            display_label, definition, forward_reading, inverse_reading, is_symmetric, status,
            decided_candidate_id, recorded_at)
        VALUES (revision_id, type_id, candidate.proposed_revision, candidate.display_label, candidate.definition,
            candidate.forward_reading, candidate.inverse_reading, candidate.is_symmetric,
            candidate.status, candidate.candidate_id, started);
    END IF;
    INSERT INTO memoriesql.idempotency_receipts (
        tenant_id, workspace_id, access_scope_id, idempotency_receipt_id, operation_kind,
        idempotency_key, request_hash, status, resource_kind, resource_id, attempt_count, created_at, updated_at
    ) VALUES (
        c.tenant_id, c.workspace_id, (request->>'access_scope_id')::uuid, rid, 'relation_type.decide.v1',
        request->>'idempotency_key', request_hash, 'in_progress', 'relation_type_candidate', candidate.candidate_id,
        1, started, started
    );
    INSERT INTO memoriesql.relation_type_decisions (tenant_id, candidate_id, decision, reason,
        relation_type_revision_id, decided_by_principal_id, idempotency_receipt_id, decided_at)
    VALUES (c.tenant_id, candidate.candidate_id, request->>'decision', request->>'reason', revision_id,
        c.principal_id, rid, started);
    response := jsonb_build_object('contract_version', 1, 'candidate_id', candidate.candidate_id,
        'decision', request->>'decision',
        'relation_type', CASE WHEN revision_id IS NULL THEN NULL
            ELSE jsonb_build_object('key', candidate.type_key, 'revision', candidate.proposed_revision) END,
        'relation_type_revision_id', revision_id, 'idempotency_receipt_id', rid);
    UPDATE memoriesql.idempotency_receipts SET status = 'succeeded', response_receipt = response,
        updated_at = pg_catalog.clock_timestamp(), completed_at = pg_catalog.clock_timestamp()
    WHERE tenant_id = c.tenant_id AND idempotency_receipt_id = rid;
    RETURN response || '{"replayed":false}'::jsonb;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.decide_relation_type_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.decide_relation_type_v1(jsonb) TO memoriesql_application;

-- Revision-6 related authorship: explicit candidates and vocabulary pinned at activation.
ALTER TABLE memoriesql.complete_input_executions
    ADD COLUMN relation_candidates jsonb,
    ADD COLUMN relation_vocabulary jsonb;
ALTER TABLE memoriesql.complete_input_executions
    DROP CONSTRAINT complete_input_executions_execution_contract_revision_check,
    ADD CONSTRAINT complete_input_executions_execution_contract_revision_check
        CHECK (execution_contract_revision IN (2, 3, 4, 5, 6));
ALTER TABLE memoriesql.complete_input_dispatch_policies
    DROP CONSTRAINT complete_input_dispatch_polic_execution_contract_revision_check,
    ADD CONSTRAINT complete_input_dispatch_polic_execution_contract_revision_check
        CHECK (execution_contract_revision IN (2, 3, 4, 5, 6));
ALTER TABLE memoriesql.complete_input_executions ADD CONSTRAINT complete_input_executions_relation_pins CHECK (
    (execution_contract_revision = 6) = (relation_candidates IS NOT NULL AND relation_vocabulary IS NOT NULL)
);

-- UTC text identical to the Python contract's JSON form, independent of session time zone.
CREATE FUNCTION memoriesql.relation_packet_time(value timestamp with time zone)
RETURNS text
LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT CASE WHEN value IS NULL THEN NULL ELSE
        to_char(value AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
        || CASE WHEN extract(microseconds FROM value AT TIME ZONE 'UTC')::bigint % 1000000 <> 0
                THEN '.' || lpad((extract(microseconds FROM value AT TIME ZONE 'UTC')::bigint % 1000000)::text, 6, '0')
                ELSE '' END
        || 'Z' END
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_packet_time(timestamp with time zone) FROM PUBLIC;

-- Inherited source clocks of one bead. Unit time wins over event time; capture time is
-- labeled as such and is never occurrence time. Precision and order are as stored.
CREATE FUNCTION memoriesql.bead_source_clock_v1(t uuid, bead uuid)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
    SELECT jsonb_build_object(
        'event_id', b.event_id, 'source_unit_id', b.source_unit_id, 'source_object_id', e.source_object_id,
        'occurred_at', memoriesql.relation_packet_time(COALESCE(u.unit_source_occurred_at, e.source_occurred_at, e.captured_at)),
        'occurred_end_at', memoriesql.relation_packet_time(CASE
            WHEN u.unit_source_occurred_at IS NOT NULL THEN u.unit_source_occurred_end_at
            WHEN e.source_occurred_at IS NOT NULL THEN e.source_occurred_end_at END),
        'time_precision', CASE
            WHEN u.unit_source_occurred_at IS NOT NULL THEN COALESCE(u.unit_time_precision, u.structure #>> '{native,time_precision}')
            WHEN e.source_occurred_at IS NOT NULL THEN COALESCE(e.source_time_precision, m.declaration #>> '{native,time_precision}') END,
        'time_basis', CASE WHEN u.unit_source_occurred_at IS NOT NULL THEN 'unit_source_time'
                           WHEN e.source_occurred_at IS NOT NULL THEN 'event_source_time' ELSE 'capture_time' END,
        'unit_ordinal', u.unit_ordinal,
        'event_sequence', e.source_sequence,
        'recorded_at', memoriesql.relation_packet_time(e.recorded_at))
    FROM memoriesql.beads AS b
    JOIN memoriesql.source_events AS e ON e.tenant_id = b.tenant_id AND e.event_id = b.event_id
    JOIN memoriesql.source_units AS u ON u.tenant_id = b.tenant_id AND u.source_unit_id = b.source_unit_id
    LEFT JOIN memoriesql.source_event_materializations AS m ON m.tenant_id = b.tenant_id AND m.event_id = b.event_id
    WHERE b.tenant_id = t AND b.bead_id = bead
$$;
REVOKE ALL ON FUNCTION memoriesql.bead_source_clock_v1(uuid, uuid) FROM PUBLIC;

-- One explicitly supplied candidate: its accepted meaning, clocks, mentions, claims and
-- lineage as currently stored. Supplied coverage, not relevance. Callers authorize first.
CREATE FUNCTION memoriesql.relation_candidate_packet_v1(t uuid, w uuid, candidate uuid)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE;
    amount integer; statements jsonb; mentions jsonb; claims jsonb;
BEGIN
    SELECT bead.* INTO b FROM memoriesql.beads AS bead
    WHERE bead.tenant_id = t AND bead.workspace_id = w AND bead.bead_id = candidate;
    SELECT version.* INTO v FROM memoriesql.accepted_bead_semantics AS a
    JOIN memoriesql.bead_versions AS version
      ON version.tenant_id = a.tenant_id AND version.bead_version_id = a.bead_version_id
    WHERE a.tenant_id = t AND a.workspace_id = w AND a.bead_id = candidate;
    IF b.bead_id IS NULL OR v.bead_version_id IS NULL THEN
        RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
        WHERE tenant_id = t AND bead_version_id = v.bead_version_id LIMIT 33) AS bounded;
    IF amount NOT BETWEEN 1 AND 32 THEN
        RAISE EXCEPTION 'relation_candidate_budget' USING ERRCODE = '54000';
    END IF;
    SELECT jsonb_agg(jsonb_build_object(
        'statement_id', s.statement_id, 'statement_kind', s.statement_kind, 'statement_text', s.statement_text,
        'evidence', COALESCE((SELECT jsonb_agg(jsonb_build_object('source_unit_id', x.evidence_source_unit_id,
                         'content_hash', x.evidence_content_hash) ORDER BY x.evidence_source_unit_id)
                     FROM memoriesql.bead_semantic_statement_evidence AS x
                     WHERE x.tenant_id = t AND x.statement_id = s.statement_id), '[]'::jsonb)
    ) ORDER BY s.statement_sequence) INTO statements
    FROM memoriesql.bead_semantic_statements AS s
    WHERE s.tenant_id = t AND s.bead_version_id = v.bead_version_id;
    SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.entity_mentions
        WHERE tenant_id = t AND bead_version_id = v.bead_version_id LIMIT 33) AS bounded;
    IF amount > 32 THEN
        RAISE EXCEPTION 'relation_candidate_budget' USING ERRCODE = '54000';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'entity_mention_id', m.entity_mention_id, 'surface_text', m.surface_text,
        'local_identity_state', m.local_identity_state) ORDER BY m.entity_mention_id), '[]'::jsonb) INTO mentions
    FROM memoriesql.entity_mentions AS m WHERE m.tenant_id = t AND m.bead_version_id = v.bead_version_id;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'claim_id', cl.claim_id,
        'statement_ids', (SELECT jsonb_agg(cs.statement_id ORDER BY cs.statement_id) FROM memoriesql.bead_claim_statements AS cs
                          WHERE cs.tenant_id = t AND cs.claim_id = cl.claim_id),
        'subject', cl.subject_text, 'slot', cl.slot_text, 'value', cl.value_text,
        'applicability', cl.applicability_text,
        'state_at_activation', memoriesql.bead_claim_state_v1(t, cl.claim_id, pg_catalog.clock_timestamp())->>'state'
    ) ORDER BY cl.claim_id), '[]'::jsonb) INTO claims
    FROM memoriesql.bead_claims AS cl WHERE cl.tenant_id = t AND cl.bead_id = candidate;
    RETURN jsonb_build_object(
        'bead_id', b.bead_id,
        'bead_version_id', v.bead_version_id,
        'origin_kind', b.origin_kind,
        'bead_type', (SELECT jsonb_build_object('key', ty.stable_key, 'revision', tr.revision)
                      FROM memoriesql.bead_type_revisions AS tr
                      JOIN memoriesql.bead_types AS ty ON ty.bead_type_id = tr.bead_type_id
                      WHERE tr.bead_type_revision_id = v.bead_type_revision_id),
        'title', CASE WHEN v.render_contract_revision = 2 THEN v.render_payload #>> '{title,text}' END,
        'summary', CASE WHEN v.render_contract_revision = 2 THEN COALESCE((
            SELECT jsonb_agg(clause.value->>'text' ORDER BY clause.ordinality)
            FROM jsonb_array_elements(v.render_payload->'summary') WITH ORDINALITY AS clause(value, ordinality)), '[]'::jsonb)
            ELSE '[]'::jsonb END,
        'source', memoriesql.bead_source_clock_v1(t, candidate),
        'statements', statements,
        'mentions', mentions,
        'claims', claims,
        'superseded_by', COALESCE((SELECT jsonb_agg(sup.bead_id ORDER BY sup.bead_id) FROM memoriesql.bead_supersessions AS sup
                                   WHERE sup.tenant_id = t AND sup.superseded_bead_id = candidate), '[]'::jsonb)
    );
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_candidate_packet_v1(uuid, uuid, uuid) FROM PUBLIC;

-- Pinned candidates remain under the current context's maintain authority.
CREATE FUNCTION memoriesql.relation_candidates_authorize(t uuid, task uuid)
RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; item jsonb; a memoriesql.accepted_bead_semantics%ROWTYPE;
BEGIN
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id = t AND execution_task_id = task;
    IF e.execution_contract_revision IS DISTINCT FROM 6 THEN RETURN; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(e.relation_candidates) LOOP
        SELECT * INTO a FROM memoriesql.accepted_bead_semantics
        WHERE tenant_id = t AND bead_id = (item->>'bead_id')::uuid
          AND bead_version_id = (item->>'bead_version_id')::uuid;
        IF a.bead_id IS NULL OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(
                t, a.workspace_id, a.access_scope_id, a.bead_version_id) THEN
            RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE = '42501';
        END IF;
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.relation_candidates_authorize(uuid, uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.activate_complete_input_v5(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    old memoriesql.idempotency_receipts%ROWTYPE; e memoriesql.complete_input_executions%ROWTYPE;
    original memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    a memoriesql.accepted_bead_semantics%ROWTYPE; resolved memoriesql.relation_type_revisions%ROWTYPE;
    rid uuid:=uuidv7(); tid uuid:=uuidv7(); eid uuid; input jsonb; result jsonb; item jsonb;
    vocabulary jsonb:='[]'; candidates jsonb:='[]'; request_hash text; started timestamptz:=clock_timestamp();
BEGIN
    IF request->>'contract_version' IS DISTINCT FROM '5' OR request->>'expected_schema_version' IS DISTINCT FROM '27' OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','binding_task_id','dispatch_policy_id','authorized_context','relation_candidates','relation_vocabulary']<>'{}'::jsonb OR
       COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR octet_length(request::text)>16384
       OR jsonb_typeof(request->'relation_candidates') IS DISTINCT FROM 'array' OR jsonb_array_length(request->'relation_candidates')>8
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(request->'relation_candidates') x
                 WHERE jsonb_typeof(x) IS DISTINCT FROM 'string' OR x#>>'{}' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (SELECT count(*)<>count(DISTINCT x) FROM jsonb_array_elements_text(request->'relation_candidates') x)
       OR jsonb_typeof(request->'relation_vocabulary') IS DISTINCT FROM 'array'
       OR jsonb_array_length(request->'relation_vocabulary') NOT BETWEEN 1 AND 32
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(request->'relation_vocabulary') x
                 WHERE jsonb_typeof(x) IS DISTINCT FROM 'object' OR x-ARRAY['key','revision']<>'{}'::jsonb
                    OR jsonb_typeof(x->'key') IS DISTINCT FROM 'string' OR x->>'revision' !~ '^[1-9][0-9]{0,8}$')
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(request->'relation_vocabulary') x GROUP BY x->>'key' HAVING count(*)<>1) THEN
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
    IF (b.package_pin->>'required_characters')::bigint>131072 THEN
        RAISE EXCEPTION 'source_revisiting_target_budget' USING ERRCODE='54000'; END IF;
    PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id));
    IF NOT memoriesql.revisiting_context_valid(c.tenant_id,b.workspace_id,b.access_scope_id,b.package_id,request->'authorized_context')
      OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=6)
    THEN RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501'; END IF;
    request_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    SELECT * INTO old FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='complete_input.activate.v5' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old.request_hash<>request_hash THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old.response_receipt||'{"replayed":true}'::jsonb;
    END IF;
    -- Exact latest active revisions only; an author never mints vocabulary while authoring.
    FOR item IN SELECT value FROM jsonb_array_elements(request->'relation_vocabulary') ORDER BY value->>'key' LOOP
        resolved:=memoriesql.relation_type_active_revision(c.tenant_id,b.workspace_id,item->>'key',(item->>'revision')::integer);
        IF resolved.relation_type_revision_id IS NULL THEN
            RAISE EXCEPTION 'relation_vocabulary_unavailable' USING ERRCODE='22023'; END IF;
        vocabulary:=vocabulary||jsonb_build_array(jsonb_build_object('key',item->>'key','revision',resolved.revision,
            'namespace',(SELECT ty.namespace FROM memoriesql.relation_types ty WHERE ty.relation_type_id=resolved.relation_type_id),
            'label',resolved.display_label,'definition',resolved.definition,
            'forward_reading',resolved.forward_reading,'inverse_reading',resolved.inverse_reading,'symmetric',resolved.is_symmetric));
    END LOOP;
    IF octet_length(memoriesql.canonical_semantic_json_text(vocabulary))>32768 THEN
        RAISE EXCEPTION 'relation_vocabulary_budget' USING ERRCODE='54000'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(request->'relation_candidates') LOOP
        SELECT * INTO a FROM memoriesql.accepted_bead_semantics WHERE tenant_id=c.tenant_id AND workspace_id=b.workspace_id AND bead_id=(item#>>'{}')::uuid;
        IF a.bead_id IS NULL OR a.bead_id=b.bead_id OR NOT memoriesql.current_context_accepted_bead_maintain_authorized(c.tenant_id,a.workspace_id,a.access_scope_id,a.bead_version_id) THEN
            RAISE EXCEPTION 'relation_candidate_unavailable' USING ERRCODE='42501'; END IF;
    END LOOP;
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,b.access_scope_id,rid,'complete_input.activate.v5',request->>'idempotency_key',request_hash,'in_progress','semantic_task',b.task_id,1,started,started);
    SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND binding_task_id=b.task_id;
    IF FOUND THEN
        -- A different candidate set or vocabulary is a conflict, never a silent re-pin.
        IF e.execution_contract_revision<>6 OR e.relation_vocabulary IS DISTINCT FROM vocabulary
           OR (SELECT COALESCE(jsonb_agg(z.x->>'bead_id' ORDER BY z.o),'[]'::jsonb) FROM jsonb_array_elements(e.relation_candidates) WITH ORDINALITY z(x,o))
              IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(z.x ORDER BY z.o),'[]'::jsonb) FROM jsonb_array_elements_text(request->'relation_candidates') WITH ORDINALITY z(x,o))
           OR e.authorized_context IS DISTINCT FROM request->'authorized_context' OR e.dispatch_policy_id<>(request->>'dispatch_policy_id')::uuid THEN
            RAISE EXCEPTION 'complete_input_activation_conflict' USING ERRCODE='23505'; END IF;
        SELECT response_receipt INTO result FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=e.activation_receipt_id;
        result:=result||jsonb_build_object('idempotency_receipt_id',rid,'replayed',true);
        PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
        PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id));
        IF NOT memoriesql.revisiting_context_valid(c.tenant_id,b.workspace_id,b.access_scope_id,b.package_id,request->'authorized_context')
          OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=6)
        THEN RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501'; END IF;
        UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
        RETURN result;
    END IF;
    IF original.status<>'policy_paused' OR original.pause_reason_code<>'complete_input_executor_unavailable' OR original.attempt_count<>0 OR original.cancel_requested_at IS NOT NULL THEN
        RAISE EXCEPTION 'complete_input_binding_not_transferable' USING ERRCODE='55000'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(request->'relation_candidates') LOOP
        candidates:=candidates||jsonb_build_array(memoriesql.relation_candidate_packet_v1(c.tenant_id,b.workspace_id,(item#>>'{}')::uuid));
    END LOOP;
    IF octet_length(memoriesql.canonical_semantic_json_text(candidates))>65536 THEN
        RAISE EXCEPTION 'relation_candidate_budget' USING ERRCODE='54000'; END IF;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id;
    INSERT INTO memoriesql.complete_input_executions (tenant_id,binding_task_id,execution_task_id,dispatch_policy_id,source_schema_version,
        activation_receipt_id,execution_contract_revision,authorized_context,classification_vocabulary,relation_candidates,relation_vocabulary)
    VALUES(c.tenant_id,b.task_id,tid,(request->>'dispatch_policy_id')::uuid,(SELECT schema_version FROM memoriesql.source_objects WHERE tenant_id=c.tenant_id AND source_object_id=p.source_object_id),
        rid,6,request->'authorized_context',NULL,candidates,vocabulary);
    IF memoriesql.cancel_semantic_task(c.tenant_id,b.task_id,'complete_input.execution_transferred',clock_timestamp())<>'cancelled' THEN
        RAISE EXCEPTION 'complete_input_transfer_failed' USING ERRCODE='55000'; END IF;
    input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',6,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||tid::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',request->>'dispatch_policy_id','declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_source_revisiting_v1','authorized_context',request->'authorized_context','relation_candidates',candidates,'relation_vocabulary',vocabulary));
    SELECT q.idempotency_receipt_id INTO eid FROM memoriesql.enqueue_semantic_task(tid,'complete-input.v5:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',6,'1b544faaf1775aab29e29ff1447aba2c5e68b89059903d8da32543f4c45f08f9','semantic-tasks-v1:490157cbf9802d8199a4615835df2905531e6aeaa152ccbfdaa17da69495c502',b.source_unit_id::text,0,input,input#>>'{evidence_manifest,manifest_id}',b.access_scope_id,started,b.task_id,started) q;
    result:=jsonb_build_object('contract_version',5,'authorized_context',request->'authorized_context','binding_task_id',b.task_id,'execution_task_id',tid,'idempotency_receipt_id',rid,'enqueue_receipt_id',eid,'package',b.package_pin,'relation_candidates',request->'relation_candidates','replayed',false);
    PERFORM memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);
    PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=b.package_id));
    IF NOT memoriesql.revisiting_context_valid(c.tenant_id,b.workspace_id,b.access_scope_id,b.package_id,request->'authorized_context')
      OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=6)
    THEN RAISE EXCEPTION 'source_revisiting_qualification_or_context_unavailable' USING ERRCODE='42501'; END IF;
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=rid;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v5(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.activate_complete_input_v5(jsonb) TO memoriesql_application;

-- Claims, relations, coverage and claim updates of one revision-6 bundle, inside the
-- canonical apply transaction after the authored statements and mentions exist.
-- Every refusal of authored content is a data error (22023) so it settles as invalid output.
CREATE FUNCTION memoriesql.apply_authored_relations_v1(
    t uuid, w uuid, scope uuid, task uuid, attempt uuid, authored_bead uuid, authored_version uuid,
    receipt uuid, payload jsonb, run_ref text, principal uuid, at timestamp with time zone
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off AS $$
DECLARE
    x memoriesql.complete_input_executions%ROWTYPE;
    rel jsonb; cand jsonb; item jsonb; claim jsonb; upd jsonb; ref jsonb;
    statement uuid; unit uuid; claim_event uuid; type_revision uuid; named uuid[];
    candidate_scope uuid; candidate_version uuid;
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
            RAISE EXCEPTION 'relation_type_not_pinned' USING ERRCODE = '22023';
        END IF;
        SELECT r.relation_type_revision_id INTO type_revision FROM memoriesql.relation_types AS ty
        JOIN memoriesql.relation_type_revisions AS r ON r.relation_type_id = ty.relation_type_id
        WHERE ty.type_key = rel#>>'{relation_type,key}' AND r.revision = (rel#>>'{relation_type,revision}')::integer
          AND (ty.tenant_id IS NULL OR (ty.tenant_id = t AND ty.workspace_id = w));
        IF type_revision IS NULL OR rel->>'basis' IS NULL OR rel->>'basis' NOT IN ('source_stated', 'inferred')
           OR rel->>'direction' IS NULL OR rel->>'direction' NOT IN ('from_authored', 'to_authored')
           OR jsonb_typeof(rel->'rationale') IS DISTINCT FROM 'string' OR btrim(rel->>'rationale') = '' OR char_length(rel->>'rationale') > 1024
           OR jsonb_typeof(rel->'uncertainty') NOT IN ('string', 'null')
           OR (jsonb_typeof(rel->'uncertainty') = 'string' AND (btrim(rel->>'uncertainty') = '' OR char_length(rel->>'uncertainty') > 1024))
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
            rationale_text, uncertainty_text, author_confidence, authoring_bead_id, semantic_task_id,
            semantic_attempt_id, semantic_run_id, authored_by_principal_id, recorded_at
        ) SELECT
            t, w, (rel->>'relation_id')::uuid,
            CASE WHEN rel->>'direction' = 'from_authored' THEN scope ELSE candidate_scope END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN authored_bead ELSE (cand->>'bead_id')::uuid END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN authored_version ELSE candidate_version END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN candidate_scope ELSE scope END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN (cand->>'bead_id')::uuid ELSE authored_bead END,
            CASE WHEN rel->>'direction' = 'from_authored' THEN candidate_version ELSE authored_version END,
            type_revision, rel->>'basis', rel->>'rationale', NULLIF(rel->'uncertainty', 'null'::jsonb) #>> '{}',
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
REVOKE ALL ON FUNCTION memoriesql.apply_authored_relations_v1(uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, jsonb, text, uuid, timestamp with time zone) FROM PUBLIC;

-- Exact restatements: revision 6 joins every revisiting, exposure, dispatch,
-- binding, apply and inspection site; earlier revisions keep their meaning.
CREATE OR REPLACE FUNCTION memoriesql.source_revisiting_authorize(t uuid, task uuid, selected uuid DEFAULT NULL) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
 c memoriesql.authorization_contexts%ROWTYPE; item jsonb; principal uuid;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=t AND execution_task_id=task;
 b:=memoriesql.complete_input_authorize(t,e.binding_task_id,e.dispatch_policy_id);
 IF (b.package_pin->>'required_characters')::bigint>131072 THEN
  RAISE EXCEPTION 'source_revisiting_target_budget' USING ERRCODE='54000'; END IF;
 IF (e.execution_contract_revision IS NULL OR e.execution_contract_revision NOT IN (3,4,5,6)) OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
  WHERE d.tenant_id=t AND d.dispatch_policy_id=e.dispatch_policy_id AND d.execution_contract_revision=e.execution_contract_revision) THEN
  RAISE EXCEPTION 'source_revisiting_qualification_required' USING ERRCODE='42501'; END IF;
 -- Tenant authority fence above, then policy rows in deterministic order. Preserve current producer authority.
 SELECT origin_principal_id INTO principal FROM memoriesql.semantic_tasks WHERE tenant_id=t AND task_id=e.binding_task_id;
 PERFORM cap.role_key FROM memoriesql.role_capabilities cap JOIN memoriesql.workspace_memberships m ON m.role_key=cap.role_key
 WHERE m.tenant_id=t AND m.workspace_id=b.workspace_id AND m.principal_id IN (principal,c.principal_id)
 AND cap.capability_key IN ('source.raw.read','memory.capture','memory.maintain') ORDER BY cap.role_key,cap.capability_key FOR SHARE OF cap;
 PERFORM memoriesql.revisiting_source_authorize((SELECT source_object_id FROM memoriesql.evidence_packages WHERE tenant_id=t AND package_id=b.package_id));
 PERFORM memoriesql.complete_input_authorize(t,e.binding_task_id,e.dispatch_policy_id);
 IF selected IS NOT NULL AND selected<>b.package_id THEN
  SELECT value INTO item FROM jsonb_array_elements(e.authorized_context) WHERE (value#>>'{package,package_id}')::uuid=selected;
  IF item IS NULL OR NOT memoriesql.revisiting_context_valid(t,b.workspace_id,b.access_scope_id,b.package_id,jsonb_build_array(item)) THEN
   RAISE EXCEPTION 'source_revisiting_out_of_scope' USING ERRCODE='42501'; END IF;
 END IF;
 -- Used optional context remains authorized; unused neighbors never become mandatory coverage.
 FOR item IN SELECT value FROM jsonb_array_elements(e.authorized_context) WHERE EXISTS(
  SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d JOIN memoriesql.model_provider_request_intents i USING(tenant_id,request_id)
  JOIN memoriesql.semantic_tasks q ON q.tenant_id=i.tenant_id AND q.task_id=i.task_id
  JOIN memoriesql.semantic_task_attempts a ON a.tenant_id=i.tenant_id AND a.attempt_id=i.attempt_id
  WHERE i.tenant_id=t AND i.task_id=task AND a.lease_generation=q.lease_generation AND d.delivery_version=2
   AND d.selection->>'package_id'=value#>>'{package,package_id}') LOOP
  IF NOT memoriesql.revisiting_context_valid(t,b.workspace_id,b.access_scope_id,b.package_id,jsonb_build_array(item)) THEN
   RAISE EXCEPTION 'source_revisiting_context_unavailable' USING ERRCODE='42501'; END IF;
 END LOOP;
END; $$;

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
    RETURN 'authorized';
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.assert_complete_execution_binding() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,memoriesql AS $$
DECLARE e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
    t memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; expected jsonb;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' OR NEW.contract_revision NOT IN (2,3,4,5,6) THEN RETURN NULL; END IF;
        SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=NEW.tenant_id AND execution_task_id=NEW.task_id;
    ELSE e:=NEW; END IF;
    SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=e.tenant_id AND task_id=e.execution_task_id;
    SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=e.tenant_id AND task_id=e.binding_task_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=e.tenant_id AND package_id=b.package_id;
    expected:=jsonb_build_object('task_id',t.task_id,'task_kind','memory.semantic.author-complete-unit','contract_revision',2,'target_reference',b.source_unit_id,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
      'evidence_manifest',jsonb_build_object('manifest_id','complete-input.'||t.task_id::text,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',b.source_unit_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
      'payload',jsonb_build_object('binding_task_id',b.task_id,'source_object_id',p.source_object_id,'event_id',b.event_id,'source_unit_ids',jsonb_build_array(b.source_unit_id),'bead_ids',jsonb_build_array(b.bead_id),'package',b.package_pin,'producer_policy_id',b.producer_policy_id,'dispatch_policy_id',e.dispatch_policy_id,'declaration',p.declaration,'event_declaration',(SELECT declaration FROM memoriesql.source_event_materializations WHERE tenant_id=b.tenant_id AND event_id=b.event_id),'parent_source_unit_id',(SELECT parent_unit_id FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'parent_resolution',(SELECT structure->'parent_resolution' FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id),'required_execution','trusted_complete_input_exposure_v1'));
    IF e.execution_contract_revision IN (3,4,5,6) THEN
        expected:=jsonb_set(expected,'{contract_revision}',to_jsonb(e.execution_contract_revision));
        expected:=jsonb_set(expected,'{payload,required_execution}','"trusted_source_revisiting_v1"');
        expected:=jsonb_set(expected,'{payload,authorized_context}',e.authorized_context);
    END IF;
    IF e.execution_contract_revision=5 THEN expected:=jsonb_set(expected,'{payload,classification_vocabulary}',e.classification_vocabulary); END IF;
    IF e.execution_contract_revision=6 THEN
        expected:=jsonb_set(jsonb_set(expected,'{payload,relation_candidates}',e.relation_candidates),'{payload,relation_vocabulary}',e.relation_vocabulary);
    END IF;
    IF e.execution_task_id IS NULL OR b.task_id IS NULL OR t.task_id IS NULL OR t.input_payload IS DISTINCT FROM expected OR t.rerun_of_task_id IS DISTINCT FROM b.task_id OR
       t.origin_principal_id IS DISTINCT FROM p.producer_principal_id OR t.access_scope_id IS DISTINCT FROM b.access_scope_id OR t.workspace_id IS DISTINCT FROM b.workspace_id OR
       t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=6 THEN '1b544faaf1775aab29e29ff1447aba2c5e68b89059903d8da32543f4c45f08f9' WHEN e.execution_contract_revision=5 THEN 'ff9fe274fc8f9fe4d2a7d5f4e3c0f53b58e0fec4158a525e3aca484ab347e493' WHEN e.execution_contract_revision=4 THEN '8488d4fddf19e65a2eb41203d6b26d7b400d73516c044700246ab3a7e75adce5' WHEN e.execution_contract_revision=3 THEN 'c8c172146c570bde75077745d7f00afa116b5db8f1b78bca4aebada561066e2d' ELSE '5288a780557724d7c5be81afd28f50a0cc5e5b86a3c8e89ae241033e12f3f3fe' END) OR
       t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=6 THEN 'semantic-tasks-v1:490157cbf9802d8199a4615835df2905531e6aeaa152ccbfdaa17da69495c502' WHEN e.execution_contract_revision=5 THEN 'semantic-tasks-v1:a351bceba3c90d5e9edf2e5e3c4d98c20148fc061f635c3b23199bae4caf0e25' WHEN e.execution_contract_revision=4 THEN 'semantic-tasks-v1:0876addc48bcf62db405dff9d1a8b75eb497cbec739945d741d4db72aea4b6dd' WHEN e.execution_contract_revision=3 THEN 'semantic-tasks-v1:116ac71ae8ac94ad8abc3018a0577a60a5e5e543ddf3660ae7db467f09764aef' ELSE 'semantic-tasks-v1:7e8a296cf1143deb86715144ed06cceefcb28dcc9955892ba7df5a06ded8f71f' END) THEN
        RAISE EXCEPTION 'complete_execution_binding_conflict' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END; $$;

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
        ELSIF NEW.contract_revision IN (2,3,4,5,6) THEN
            IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=NEW.tenant_id AND e.execution_task_id=NEW.task_id) THEN
                RAISE EXCEPTION 'complete_input_activation_required' USING ERRCODE='55000'; END IF;
            IF NEW.status='succeeded' AND (NOT memoriesql.complete_input_exposure_valid(NEW.tenant_id,NEW.task_id,NEW.result_attempt_id,NEW.lease_generation) OR
                NOT EXISTS(SELECT 1 FROM memoriesql.bead_versions v JOIN memoriesql.bead_statement_revisions s USING(tenant_id,bead_version_id) JOIN memoriesql.semantic_task_receipts r ON r.tenant_id=v.tenant_id AND r.semantic_task_receipt_id=v.semantic_task_receipt_id JOIN memoriesql.idempotency_receipts i ON i.tenant_id=r.tenant_id AND i.idempotency_receipt_id=r.idempotency_receipt_id WHERE v.tenant_id=NEW.tenant_id AND v.bead_id=(NEW.input_payload#>>'{payload,bead_ids,0}')::uuid AND i.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5') AND i.resource_id=NEW.task_id)) THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        ELSE RAISE EXCEPTION 'complete_input_execution_unsupported' USING ERRCODE='55000'; END IF;
    ELSIF TG_TABLE_NAME='semantic_task_attempts' THEN
        SELECT * INTO t FROM memoriesql.semantic_tasks WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
        IF t.task_kind='memory.semantic.author-complete-unit' AND (t.contract_revision NOT IN (2,3,4,5,6) OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t.tenant_id AND e.execution_task_id=t.task_id)) THEN
            RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
    ELSE
        SELECT u.materialization_version=1 INTO complete FROM memoriesql.beads b JOIN memoriesql.source_units u USING(tenant_id,source_unit_id) WHERE b.tenant_id=NEW.tenant_id AND b.bead_id=NEW.bead_id;
        IF complete THEN
            SELECT q.* INTO t FROM memoriesql.semantic_task_receipts r JOIN memoriesql.idempotency_receipts i USING(tenant_id,idempotency_receipt_id)
            JOIN memoriesql.semantic_tasks q ON q.tenant_id=i.tenant_id AND q.task_id=i.resource_id
            WHERE r.tenant_id=NEW.tenant_id AND r.semantic_task_receipt_id=NEW.semantic_task_receipt_id AND i.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5');
            SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=t.tenant_id AND task_id=t.task_id AND lease_generation=t.lease_generation AND status='running';
            IF t.task_id IS NULL OR t.task_kind<>'memory.semantic.author-complete-unit' OR t.contract_revision NOT IN (2,3,4,5,6) OR t.status<>'running' OR t.cancel_requested_at IS NOT NULL OR
               NEW.bead_id::text IS DISTINCT FROM t.input_payload#>>'{payload,bead_ids,0}' OR NEW.version<>1 OR
               NOT memoriesql.complete_input_exposure_valid(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation) OR
               memoriesql.reauthorize_semantic_task(t.tenant_id,t.task_id,a.attempt_id,t.lease_generation,t.lease_owner,t.worker_instance_id,'hydrate',clock_timestamp())<>'authorized' THEN
                RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.apply_semantic_annotations(
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
    database_now timestamp with time zone := pg_catalog.statement_timestamp();
BEGIN
    is_complete := requested_command->>'contract_version' IN ('3','4','5','6','7');
    is_v2 := requested_command ->> 'contract_version' IS NOT DISTINCT FROM '2';
    is_correction := is_v2 AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM
        'memory.semantic.correct-observation';
    operation_key := CASE WHEN requested_command->>'contract_version'='7' THEN 'complete_input.apply.v5' WHEN requested_command->>'contract_version'='6' THEN 'complete_input.apply.v4' WHEN requested_command->>'contract_version'='5' THEN 'complete_input.apply.v3' WHEN requested_command->>'contract_version'='4' THEN 'complete_input.apply.v2' WHEN is_complete THEN 'complete_input.apply.v1' WHEN is_correction THEN 'observation_correction.apply.v2'
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
                AND requested_command->>'output_contract_hash'='67e1260a739f6fc796d87f4f32b412d393420f483ea3562d6fbb950c94b6fa21')
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
    IF requested_command->>'contract_version' IN ('4','5','6','7') THEN
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

        IF requested_command->>'contract_version' IN ('5','6','7') THEN
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
        jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='7' THEN 7 WHEN requested_command->>'contract_version'='6' THEN 6 WHEN requested_command->>'contract_version'='5' THEN 5 WHEN requested_command->>'contract_version'='4' THEN 4 WHEN is_complete THEN 3 WHEN is_v2 THEN 2 ELSE 1 END), database_now, database_now
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

CREATE OR REPLACE FUNCTION memoriesql.record_source_delivery_v1(request_id uuid, payload_hash text, frame jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; i memoriesql.model_provider_request_intents%ROWTYPE;
 e memoriesql.complete_input_executions%ROWTYPE; b memoriesql.logical_unit_materializations%ROWTYPE;
 task memoriesql.semantic_tasks%ROWTYPE; a memoriesql.semantic_task_attempts%ROWTYPE; part memoriesql.evidence_package_parts%ROWTYPE;
 item jsonb; selection jsonb; items jsonb:='[]'; actual jsonb; fh text; delivered integer:=0; lo integer; hi integer;
 covered int4multirange; missing int4range;
BEGIN
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO i FROM memoriesql.model_provider_request_intents WHERE tenant_id=c.tenant_id AND model_provider_request_intents.request_id=record_source_delivery_v1.request_id;
 SELECT * INTO e FROM memoriesql.complete_input_executions WHERE tenant_id=c.tenant_id AND execution_task_id=i.task_id;
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=c.tenant_id AND task_id=e.binding_task_id;
 IF c.principal_kind IS DISTINCT FROM 'service' OR NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d
  WHERE d.tenant_id=c.tenant_id AND d.dispatch_policy_id=e.dispatch_policy_id AND d.attestor_principal_id=c.principal_id
   AND d.status='active' AND d.expires_at>clock_timestamp() AND d.execution_contract_revision=e.execution_contract_revision)
 OR (e.execution_contract_revision=5 AND i.run_role<>'direct_leaf')
 OR i.request_payload_hash IS DISTINCT FROM payload_hash OR i.task_id::text IS DISTINCT FROM frame->>'task_id'
 OR i.attempt_id::text IS DISTINCT FROM frame->>'attempt_id' OR frame->'package' IS DISTINCT FROM b.package_pin
 OR frame->'contract_version' IS DISTINCT FROM '2'::jsonb OR octet_length(frame::text)>264192
 OR frame-ARRAY['contract_version','task_id','attempt_id','package','window','read']<>'{}'::jsonb
 OR NOT EXISTS(SELECT 1 FROM memoriesql.model_usage_events u WHERE u.tenant_id=c.tenant_id AND u.request_id=i.request_id AND u.outcome='succeeded') THEN
  RAISE EXCEPTION 'trusted_source_delivery_required' USING ERRCODE='42501'; END IF;
 SELECT * INTO task FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=i.task_id FOR UPDATE;
 SELECT * INTO a FROM memoriesql.semantic_task_attempts WHERE tenant_id=c.tenant_id AND attempt_id=i.attempt_id;
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 IF c.principal_id=a.claimant_principal_id OR task.contract_revision NOT IN (3,4,5,6) OR task.status<>'running' OR a.status<>'running'
 OR task.lease_generation<>a.lease_generation OR task.cancel_requested_at IS NOT NULL
 OR task.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'source_revisiting_stale_attempt' USING ERRCODE='42501'; END IF;
 fh:=encode(sha256(convert_to(frame::text,'UTF8')),'hex');
 IF EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d WHERE d.tenant_id=c.tenant_id AND d.request_id=i.request_id) THEN
  IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_receipts d WHERE d.tenant_id=c.tenant_id AND d.request_id=i.request_id AND d.frame_hash=fh AND d.delivery_version=2) THEN
   RAISE EXCEPTION 'source_delivery_replay_conflict' USING ERRCODE='23505'; END IF;
  RETURN true;
 END IF;
 IF NULLIF(frame->'window','null') IS NOT NULL THEN
  IF NULLIF(frame->'read','null') IS NOT NULL OR frame#>>'{window,task_id}' IS DISTINCT FROM i.task_id::text
   OR frame#>>'{window,attempt_id}' IS DISTINCT FROM i.attempt_id::text OR frame#>'{window,package}' IS DISTINCT FROM b.package_pin
   OR frame#>>'{window,contract_version}' IS DISTINCT FROM '2' OR jsonb_typeof(frame#>'{window,slices}') IS DISTINCT FROM 'array'
   OR jsonb_array_length(frame#>'{window,slices}') NOT BETWEEN 1 AND 256 THEN
   RAISE EXCEPTION 'source_delivery_window_invalid' USING ERRCODE='22023'; END IF;
  items:=frame#>'{window,slices}';
  SELECT sum(char_length(value->>'content')) INTO delivered FROM jsonb_array_elements(items);
  IF delivered IS NULL OR delivered>65536 THEN RAISE EXCEPTION 'source_delivery_window_bound' USING ERRCODE='54000'; END IF;
  selection:=jsonb_build_object('package_id',b.package_id,'kind','window','intervals',
   (SELECT jsonb_agg(jsonb_build_object('part_id',value#>'{inventory,part_id}','start_character',value->'start_character','characters',char_length(value->>'content'))) FROM jsonb_array_elements(items)));
 ELSIF NULLIF(frame->'read','null') IS NOT NULL THEN
  actual:=memoriesql.source_revisiting_read(c.tenant_id,i.task_id,frame#>'{read,request}');
  IF actual IS DISTINCT FROM frame->'read' THEN RAISE EXCEPTION 'source_delivery_evidence_mismatch' USING ERRCODE='22023'; END IF;
  selection:=actual->'request';
  IF selection->>'representation'='normalized' THEN
   delivered:=char_length(actual->>'content');
   IF selection->>'package_id'=b.package_id::text THEN
    items:=jsonb_build_array(jsonb_build_object('inventory',actual->'inventory','start_character',selection->'offset','content',actual->'content'));
   END IF;
  ELSE delivered:=octet_length(decode(actual->>'bytes_hex','hex')); END IF;
 ELSE selection:=jsonb_build_object('package_id',b.package_id,'kind','no_evidence'); END IF;
 IF (SELECT count(*)>=12 OR COALESCE(sum(d.delivered_units),0)+delivered>262144 FROM memoriesql.complete_input_dispatch_receipts d
  JOIN memoriesql.model_provider_request_intents intent USING(tenant_id,request_id)
  WHERE intent.tenant_id=c.tenant_id AND intent.task_id=i.task_id AND intent.attempt_id=i.attempt_id AND d.delivery_version=2) THEN
  RAISE EXCEPTION 'source_delivery_budget_exhausted' USING ERRCODE='54000'; END IF;
 -- Persist only previously uncovered target intervals. Every dispatch still has its own accounting intent/receipt.
 FOR item IN SELECT value FROM jsonb_array_elements(items) LOOP
  SELECT * INTO part FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=b.package_id AND part_id=(item#>>'{inventory,part_id}')::uuid;
  lo:=(item->>'start_character')::integer; hi:=lo+char_length(item->>'content');
  IF part.part_id IS NULL OR part.inventory IS DISTINCT FROM item->'inventory' OR lo IS NULL OR lo<0 OR hi<=lo OR hi>(part.inventory->>'characters')::integer
   OR substring(part.content FROM lo+1 FOR hi-lo) IS DISTINCT FROM item->>'content' THEN
   RAISE EXCEPTION 'source_delivery_evidence_mismatch' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(range_agg(int4range(x.start_character,x.end_character,'[)')),'{}'::int4multirange) INTO covered
   FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=c.tenant_id AND x.attempt_id=i.attempt_id AND x.part_id=part.part_id;
  FOR missing IN SELECT unnest(int4multirange(int4range(lo,hi,'[)'))-covered) LOOP
   INSERT INTO memoriesql.complete_input_exposures VALUES(c.tenant_id,i.task_id,i.attempt_id,a.lease_generation,i.request_id,
    b.package_id,b.package_pin->>'inventory_sha256',part.part_id,lower(missing),upper(missing),
    encode(sha256(convert_to(substring(part.content FROM lower(missing)+1 FOR upper(missing)-lower(missing)),'UTF8')),'hex'),c.principal_id,e.dispatch_policy_id,clock_timestamp());
  END LOOP;
 END LOOP;
 INSERT INTO memoriesql.complete_input_dispatch_receipts VALUES(c.tenant_id,i.request_id,fh,2,selection,delivered);
 PERFORM memoriesql.source_revisiting_authorize(c.tenant_id,i.task_id);
 IF task.lease_expires_at<=clock_timestamp() OR a.deadline_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'source_revisiting_stale_attempt' USING ERRCODE='42501'; END IF;
 RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.complete_input_exposure_valid(t uuid, task uuid, attempt uuid, generation bigint) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
 SELECT memoriesql.supervised_acceptance_valid(t,task) AND EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e
 JOIN memoriesql.logical_unit_materializations b ON b.tenant_id=e.tenant_id AND b.task_id=e.binding_task_id
 WHERE e.tenant_id=t AND e.execution_task_id=task
 AND (e.execution_contract_revision NOT IN (3,4,5,6) OR (SELECT count(*)=(b.package_pin->>'required_parts')::integer FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id))
 AND NOT EXISTS(
  SELECT 1 FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id AND
   CASE WHEN e.execution_contract_revision IN (3,4,5,6) THEN NOT COALESCE(
    (SELECT range_agg(int4range(x.start_character,x.end_character,'[)')) @> int4range(0,(p.inventory->>'characters')::integer,'[)')
     FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
      AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id),false)
   ELSE NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
    AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id AND x.end_character=(p.inventory->>'characters')::integer) END))
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
 OR i->>'task_kind' IS DISTINCT FROM 'memory.semantic.author-complete-unit'
 OR (i->>'task_contract_revision')::integer NOT IN (2,3,4,5,6)
 THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'supervised_requires_read_committed'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(q.tenant_id::text||':supervised-dispatch:'||q.qualification_id::text,0));
 SELECT r.value,r.ordinality INTO route,ordinal FROM jsonb_array_elements(q.configuration->'routes') WITH ORDINALITY r
 WHERE r.value#>>'{reference,profile_key}'=i->>'model_profile_key'
 AND r.value#>'{reference,revision}'=i->'model_profile_revision';
 IF route IS NULL OR route->'target' IS DISTINCT FROM i->'target'
 OR route->>'qualification_revision' IS DISTINCT FROM i->>'qualification_revision'
 OR route->'observable_dispatch_allowance' IS DISTINCT FROM '1'::jsonb
 OR COALESCE(i->>'dispatch_boundary','single_inference') IS DISTINCT FROM (CASE WHEN ordinal=1 THEN 'managed_turn' ELSE 'single_inference' END)
 OR (ordinal=1 AND route#>>'{target,boundary_kind}' IS DISTINCT FROM 'managed_turn')
 OR (ordinal=2 AND route#>'{target,transport_retries_disabled}' IS DISTINCT FROM 'true'::jsonb)
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
  b.access_scope_id,b.event_id,'memory.query','read') THEN RETURN unavailable; END IF;
 SELECT * INTO ev FROM memoriesql.source_events WHERE tenant_id=b.tenant_id AND event_id=b.event_id;
 SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 sources:=array_append(sources,ev.source_object_id);
 PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id);
 -- Immutable accepted identity, never newest visible version.
 SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics a
 JOIN memoriesql.bead_versions bv USING(tenant_id,bead_version_id)
 WHERE a.tenant_id=b.tenant_id AND a.bead_id=b.bead_id;
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
  THEN RETURN unavailable; END IF;
  SELECT * INTO r FROM memoriesql.semantic_task_receipts WHERE tenant_id=v.tenant_id AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  SELECT * INTO ir FROM memoriesql.idempotency_receipts WHERE tenant_id=r.tenant_id AND idempotency_receipt_id=r.idempotency_receipt_id;
  IF r.semantic_task_receipt_id IS NULL OR ir.status IS DISTINCT FROM 'succeeded' THEN RETURN unavailable; END IF;
  -- Accepted provenance is bound to its own receipt, never the newest source task.
  SELECT * INTO execution FROM memoriesql.complete_input_executions
   WHERE tenant_id=r.tenant_id AND execution_task_id=ir.resource_id;
  IF ir.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5')
   AND execution.execution_task_id IS NULL THEN RETURN unavailable; END IF;
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
   WHERE q.tenant_id=r.tenant_id AND q.task_id=execution.execution_task_id;
  -- Every accepted author can depend on optional context, including revisions 3/4
  -- without a classifier. Conservatively authorize every pinned source.
  FOR item IN SELECT value FROM jsonb_array_elements(execution.authorized_context) LOOP
   source:=(item->>'source_object_id')::uuid;
   PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
  END LOOP;
  SELECT statement_watermark INTO watermark FROM memoriesql.bead_statement_revisions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id;
  SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
   WHERE tenant_id=v.tenant_id AND bead_id=v.bead_id AND statement_sequence<=watermark LIMIT 33) bounded;
  IF amount>32 THEN RETURN budget; END IF;
  FOR s IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=v.tenant_id
   AND bead_id=v.bead_id AND statement_sequence<=watermark ORDER BY statement_sequence LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(s.tenant_id,s.workspace_id,s.access_scope_id,s.statement_id)
    THEN RETURN unavailable; END IF;
   -- Context is provenance, not support; still requires authorization before disclosure.
   FOREACH source IN ARRAY s.context_source_ids LOOP
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
   END LOOP;
   evidence:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id LIMIT 65) bounded;
   IF amount>64 THEN RETURN budget; END IF;
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
     (r.task_contract_version=6 AND ir.operation_kind='complete_input.apply.v5')) THEN
   mentions:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id LIMIT 33) bounded;
   IF amount>32 THEN RETURN budget; END IF;
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
  IF v.classification_contribution IS NOT NULL THEN
   IF r.task_contract_version<>5 OR r.task_contract_key<>'memory.semantic.author-complete-unit'
    OR ir.operation_kind<>'complete_input.apply.v4' OR ir.resource_id IS DISTINCT FROM execution.execution_task_id THEN RETURN unavailable; END IF;
   SELECT * INTO intent FROM memoriesql.model_provider_request_intents WHERE tenant_id=v.tenant_id
    AND request_id=(v.classification_contribution->>'request_id')::uuid AND task_id=ir.resource_id;
   IF intent.request_id IS NULL OR intent.workspace_id<>c.workspace_id OR
    NOT memoriesql.current_context_scope_authorized(intent.access_scope_id,'memory.query','read') THEN RETURN unavailable; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(v.classification_contribution->'evidence') LOOP
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=v.tenant_id AND package_id=(item#>>'{selection,package_id}')::uuid;
    IF p.package_id IS NULL OR p.inventory_hash IS DISTINCT FROM item#>>'{selection,inventory_sha256}' THEN RETURN unavailable; END IF;
    PERFORM memoriesql.revisiting_source_authorize(p.source_object_id); sources:=array_append(sources,p.source_object_id);
   END LOOP;
   provenance:=jsonb_build_object('request_id',intent.request_id,'task_id',intent.task_id,'attempt_id',intent.attempt_id,
    'model_id',intent.model_id,'model_profile_key',intent.model_profile_key,'model_profile_revision',intent.model_profile_revision,
    'authorization_policy_revision',intent.authorization_policy_revision,'quality_policy_revision_id',intent.quality_policy_revision_id,'pricing_revision_id',intent.pricing_revision_id);
  ELSIF r.task_contract_version=5 AND r.task_contract_key='memory.semantic.author-complete-unit' THEN RETURN unavailable;
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
 IF octet_length(memoriesql.canonical_semantic_json_text(result))>262144 THEN RETURN budget; END IF;
 -- Revalidate all disclosed dependencies at the return boundary; audit writes only.
 FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
  PERFORM memoriesql.revisiting_source_authorize(source);
 END LOOP;
 IF clock_timestamp()-started>interval '2 seconds' THEN RETURN budget; END IF;
 RETURN result;
EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;
END;
$$;

INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES ('semantic-tasks-v1:490157cbf9802d8199a4615835df2905531e6aeaa152ccbfdaa17da69495c502','memory.semantic.author-complete-unit',6,'memoriesql.kernel','1b544faaf1775aab29e29ff1447aba2c5e68b89059903d8da32543f4c45f08f9','canonical_semantics','memory.capture','capture',50,3,NULL,NULL);

-- Q-owned read of one bead's claims, relations, candidate coverage and authored claim
-- judgments as known at a time. Read-only; states are derived, never stored; no inference.
-- Any unauthorized disclosed dependency makes the whole response unavailable.
CREATE FUNCTION memoriesql.inspect_bead_relations_v1(request jsonb) RETURNS jsonb
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
            SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_claim_events
                WHERE tenant_id = cl.tenant_id AND claim_id = cl.claim_id AND recorded_at <= known LIMIT 65) AS bounded;
            IF amount > 64 THEN RETURN budget; END IF;
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'event_id', e.claim_event_id, 'action', e.action, 'related_id', e.related_claim_id, 'reason', e.reason,
                'origin', e.origin, 'authoring_bead_id', e.authoring_bead_id,
                'effective_at', memoriesql.relation_packet_time(e.effective_at),
                'recorded_at', memoriesql.relation_packet_time(e.recorded_at)) ORDER BY e.recorded_at, e.claim_event_id), '[]'::jsonb)
            INTO events FROM memoriesql.bead_claim_events AS e
            WHERE e.tenant_id = cl.tenant_id AND e.claim_id = cl.claim_id AND e.recorded_at <= known;
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
                       'label', tr.display_label, 'forward_reading', tr.forward_reading,
                       'inverse_reading', tr.inverse_reading, 'symmetric', tr.is_symmetric)
            INTO rtype FROM memoriesql.relation_type_revisions AS tr
            JOIN memoriesql.relation_types AS ty ON ty.relation_type_id = tr.relation_type_id
            WHERE tr.relation_type_revision_id = rel.relation_type_revision_id;
            SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_relation_events
                WHERE tenant_id = rel.tenant_id AND relation_id = rel.relation_id AND recorded_at <= known LIMIT 65) AS bounded;
            IF amount > 64 THEN RETURN budget; END IF;
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'event_id', e.relation_event_id, 'action', e.action, 'related_id', e.replacement_relation_id,
                'reason', e.reason, 'origin', e.origin, 'authoring_bead_id', NULL,
                'effective_at', memoriesql.relation_packet_time(e.effective_at),
                'recorded_at', memoriesql.relation_packet_time(e.recorded_at)) ORDER BY e.recorded_at, e.relation_event_id), '[]'::jsonb)
            INTO events FROM memoriesql.bead_relation_events AS e
            WHERE e.tenant_id = rel.tenant_id AND e.relation_id = rel.relation_id AND e.recorded_at <= known;
            state := memoriesql.bead_relation_state_v1(rel.tenant_id, rel.relation_id, known);
            relations := relations || jsonb_build_array(jsonb_build_object(
                'relation_id', rel.relation_id, 'relation_type', rtype,
                'direction', CASE WHEN rel.source_bead_id = v.bead_id THEN 'outgoing' ELSE 'incoming' END,
                'source_bead_id', rel.source_bead_id, 'source_bead_version_id', rel.source_bead_version_id,
                'target_bead_id', rel.target_bead_id, 'target_bead_version_id', rel.target_bead_version_id,
                'authoring_bead_id', rel.authoring_bead_id, 'basis', rel.basis, 'rationale', rel.rationale_text,
                'uncertainty', rel.uncertainty_text, 'author_confidence', rel.author_confidence,
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
EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.inspect_bead_relations_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.inspect_bead_relations_v1(jsonb) TO memoriesql_application;
