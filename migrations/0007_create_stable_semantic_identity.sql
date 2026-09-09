CREATE TABLE memoriesql.bead_types (
    bead_type_id uuid PRIMARY KEY,
    stable_key text NOT NULL UNIQUE,
    introduced_in_schema_version integer NOT NULL,
    CONSTRAINT bead_types_key_grammar
        CHECK (stable_key ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT bead_types_introduced_positive
        CHECK (introduced_in_schema_version > 0)
);

CREATE TABLE memoriesql.bead_type_revisions (
    bead_type_revision_id uuid PRIMARY KEY,
    bead_type_id uuid NOT NULL,
    revision integer NOT NULL,
    display_name text NOT NULL,
    definition text NOT NULL,
    authorable boolean NOT NULL,
    introduced_in_schema_version integer NOT NULL,
    CONSTRAINT bead_type_revisions_type_revision_uq
        UNIQUE (bead_type_id, revision),
    CONSTRAINT bead_type_revisions_exact_pin_uq
        UNIQUE (bead_type_id, bead_type_revision_id),
    CONSTRAINT bead_type_revisions_authorable_pin_uq
        UNIQUE (bead_type_id, bead_type_revision_id, authorable),
    CONSTRAINT bead_type_revisions_type_fk FOREIGN KEY (bead_type_id)
        REFERENCES memoriesql.bead_types (bead_type_id),
    CONSTRAINT bead_type_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT bead_type_revisions_name_nonempty
        CHECK (btrim(display_name) <> ''),
    CONSTRAINT bead_type_revisions_definition_nonempty
        CHECK (btrim(definition) <> ''),
    CONSTRAINT bead_type_revisions_introduced_positive
        CHECK (introduced_in_schema_version > 0)
);

INSERT INTO memoriesql.bead_types (
    bead_type_id, stable_key, introduced_in_schema_version
) VALUES
    ('10000000-0000-4000-8000-000000000001', 'observation', 7),
    ('10000000-0000-4000-8000-000000000002', 'decision', 7),
    ('10000000-0000-4000-8000-000000000003', 'goal_declaration', 7),
    ('10000000-0000-4000-8000-000000000004', 'goal_update', 7),
    ('10000000-0000-4000-8000-000000000005', 'action', 7),
    ('10000000-0000-4000-8000-000000000006', 'outcome', 7),
    ('10000000-0000-4000-8000-000000000007', 'lesson', 7),
    ('10000000-0000-4000-8000-000000000008', 'constraint', 7),
    ('10000000-0000-4000-8000-000000000009', 'incident', 7),
    ('10000000-0000-4000-8000-00000000000a', 'preference', 7),
    ('10000000-0000-4000-8000-00000000000b', 'instruction', 7),
    ('10000000-0000-4000-8000-00000000000c', 'hypothesis', 7);

INSERT INTO memoriesql.bead_type_revisions (
    bead_type_revision_id,
    bead_type_id,
    revision,
    display_name,
    definition,
    authorable,
    introduced_in_schema_version
) VALUES
    ('11000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 1, 'Observation', 'Grounded observed statement or change with no more specific semantic class', true, 7),
    ('11000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002', 1, 'Decision', 'An attributable choice, commitment, or selected alternative', true, 7),
    ('11000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000003', 1, 'Goal declaration', 'An explicit observed desired future state or objective', true, 7),
    ('11000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000004', 1, 'Goal update', 'An observed progress, scope, status, blocking, completion, or abandonment update about a goal', true, 7),
    ('11000000-0000-4000-8000-000000000005', '10000000-0000-4000-8000-000000000005', 1, 'Action', 'An attributable attempted or completed action', true, 7),
    ('11000000-0000-4000-8000-000000000006', '10000000-0000-4000-8000-000000000006', 1, 'Outcome', 'An observed result or consequence', true, 7),
    ('11000000-0000-4000-8000-000000000007', '10000000-0000-4000-8000-000000000007', 1, 'Lesson', 'An explicitly stated evidence-grounded takeaway from experience', true, 7),
    ('11000000-0000-4000-8000-000000000008', '10000000-0000-4000-8000-000000000008', 1, 'Constraint', 'An observed requirement, dependency, limit, or blocker', true, 7),
    ('11000000-0000-4000-8000-000000000009', '10000000-0000-4000-8000-000000000009', 1, 'Incident', 'An observed disruptive event requiring attention or response', true, 7),
    ('11000000-0000-4000-8000-00000000000a', '10000000-0000-4000-8000-00000000000a', 1, 'Preference', 'An explicitly expressed choice tendency; not automatically an endorsed subject-model rule', true, 7),
    ('11000000-0000-4000-8000-00000000000b', '10000000-0000-4000-8000-00000000000b', 1, 'Instruction', 'An explicitly observed directive or standing rule; source presence alone does not grant authority', true, 7),
    ('11000000-0000-4000-8000-00000000000c', '10000000-0000-4000-8000-00000000000c', 1, 'Hypothesis', 'An explicitly stated testable possibility, not a confirmed fact', true, 7);

CREATE TABLE memoriesql.entity_types (
    entity_type_id uuid PRIMARY KEY,
    stable_key text NOT NULL UNIQUE,
    introduced_in_schema_version integer NOT NULL,
    CONSTRAINT entity_types_key_grammar
        CHECK (stable_key ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT entity_types_introduced_positive
        CHECK (introduced_in_schema_version > 0)
);

CREATE TABLE memoriesql.entity_type_revisions (
    entity_type_revision_id uuid PRIMARY KEY,
    entity_type_id uuid NOT NULL,
    revision integer NOT NULL,
    display_name text NOT NULL,
    definition text NOT NULL,
    active boolean NOT NULL,
    introduced_in_schema_version integer NOT NULL,
    CONSTRAINT entity_type_revisions_type_revision_uq
        UNIQUE (entity_type_id, revision),
    CONSTRAINT entity_type_revisions_exact_pin_uq
        UNIQUE (entity_type_id, entity_type_revision_id),
    CONSTRAINT entity_type_revisions_active_pin_uq
        UNIQUE (entity_type_id, entity_type_revision_id, active),
    CONSTRAINT entity_type_revisions_type_fk FOREIGN KEY (entity_type_id)
        REFERENCES memoriesql.entity_types (entity_type_id),
    CONSTRAINT entity_type_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT entity_type_revisions_name_nonempty
        CHECK (btrim(display_name) <> ''),
    CONSTRAINT entity_type_revisions_definition_nonempty
        CHECK (btrim(definition) <> ''),
    CONSTRAINT entity_type_revisions_introduced_positive
        CHECK (introduced_in_schema_version > 0)
);

INSERT INTO memoriesql.entity_types (
    entity_type_id, stable_key, introduced_in_schema_version
) VALUES
    ('20000000-0000-4000-8000-000000000001', 'person', 7),
    ('20000000-0000-4000-8000-000000000002', 'organization', 7),
    ('20000000-0000-4000-8000-000000000003', 'group', 7),
    ('20000000-0000-4000-8000-000000000004', 'place', 7),
    ('20000000-0000-4000-8000-000000000005', 'project', 7),
    ('20000000-0000-4000-8000-000000000006', 'product', 7),
    ('20000000-0000-4000-8000-000000000007', 'system', 7),
    ('20000000-0000-4000-8000-000000000008', 'agent', 7),
    ('20000000-0000-4000-8000-000000000009', 'artifact', 7),
    ('20000000-0000-4000-8000-00000000000a', 'event', 7);

INSERT INTO memoriesql.entity_type_revisions (
    entity_type_revision_id,
    entity_type_id,
    revision,
    display_name,
    definition,
    active,
    introduced_in_schema_version
) VALUES
    ('21000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 1, 'Person', 'An individual human being.', true, 7),
    ('21000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', 1, 'Organization', 'A formally identified institution or company.', true, 7),
    ('21000000-0000-4000-8000-000000000003', '20000000-0000-4000-8000-000000000003', 1, 'Group', 'A collection of people or other entities acting together.', true, 7),
    ('21000000-0000-4000-8000-000000000004', '20000000-0000-4000-8000-000000000004', 1, 'Place', 'A geographically or logically located place.', true, 7),
    ('21000000-0000-4000-8000-000000000005', '20000000-0000-4000-8000-000000000005', 1, 'Project', 'A bounded coordinated effort with an intended result.', true, 7),
    ('21000000-0000-4000-8000-000000000006', '20000000-0000-4000-8000-000000000006', 1, 'Product', 'A product or service offered for use.', true, 7),
    ('21000000-0000-4000-8000-000000000007', '20000000-0000-4000-8000-000000000007', 1, 'System', 'A technical or socio-technical system.', true, 7),
    ('21000000-0000-4000-8000-000000000008', '20000000-0000-4000-8000-000000000008', 1, 'Agent', 'An autonomous or delegated software agent.', true, 7),
    ('21000000-0000-4000-8000-000000000009', '20000000-0000-4000-8000-000000000009', 1, 'Artifact', 'A created document, file, record, or other durable artifact.', true, 7),
    ('21000000-0000-4000-8000-00000000000a', '20000000-0000-4000-8000-00000000000a', 1, 'Event', 'A bounded occurrence represented as a stable subject.', true, 7);

ALTER TABLE memoriesql.semantic_task_receipts
ADD CONSTRAINT semantic_task_receipts_exact_bead_uq UNIQUE (
    tenant_id,
    workspace_id,
    access_scope_id,
    event_id,
    source_unit_id,
    bead_id,
    semantic_task_receipt_id
);

CREATE TABLE memoriesql.bead_versions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    version integer NOT NULL,
    bead_type_id uuid NOT NULL,
    bead_type_revision_id uuid NOT NULL,
    required_authorable boolean GENERATED ALWAYS AS (true) STORED,
    authored_by_principal_id uuid NOT NULL,
    semantic_task_receipt_id uuid,
    authored_at timestamp with time zone NOT NULL,
    CONSTRAINT bead_versions_pk PRIMARY KEY (tenant_id, bead_version_id),
    CONSTRAINT bead_versions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT bead_versions_identity_uq UNIQUE (tenant_id, bead_id, version),
    CONSTRAINT bead_versions_scope_identity_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ),
    CONSTRAINT bead_versions_bead_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        event_id, source_unit_id, bead_id
    ) REFERENCES memoriesql.beads (
        tenant_id, workspace_id, access_scope_id,
        event_id, source_unit_id, bead_id
    ),
    CONSTRAINT bead_versions_type_revision_fk FOREIGN KEY (
        bead_type_id, bead_type_revision_id, required_authorable
    ) REFERENCES memoriesql.bead_type_revisions (
        bead_type_id, bead_type_revision_id, authorable
    ),
    CONSTRAINT bead_versions_author_fk FOREIGN KEY (
        tenant_id, authored_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT bead_versions_task_receipt_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, semantic_task_receipt_id
    ) REFERENCES memoriesql.semantic_task_receipts (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, semantic_task_receipt_id
    ),
    CONSTRAINT bead_versions_version_positive CHECK (version > 0)
);

CREATE TABLE memoriesql.entity_mentions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_mention_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    surface_text text NOT NULL,
    start_offset integer,
    end_offset integer,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_mentions_pk PRIMARY KEY (tenant_id, entity_mention_id),
    CONSTRAINT entity_mentions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, entity_mention_id
    ),
    CONSTRAINT entity_mentions_origin_version_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        entity_mention_id, bead_version_id
    ),
    CONSTRAINT entity_mentions_bead_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ),
    CONSTRAINT entity_mentions_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_mentions_surface_nonempty
        CHECK (btrim(surface_text) <> ''),
    CONSTRAINT entity_mentions_offset_pair CHECK (
        (start_offset IS NULL AND end_offset IS NULL)
        OR (
            start_offset IS NOT NULL
            AND end_offset IS NOT NULL
            AND start_offset >= 0
            AND end_offset > start_offset
        )
    )
);

CREATE TABLE memoriesql.entities (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    resource_kind text GENERATED ALWAYS AS ('model') STORED,
    initial_status text NOT NULL,
    originating_entity_mention_id uuid,
    originating_bead_version_id uuid NOT NULL,
    created_by_principal_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT entities_pk PRIMARY KEY (tenant_id, entity_id),
    CONSTRAINT entities_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entities_protected_resource_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, entity_id, resource_kind
    ) REFERENCES memoriesql.protected_resources (
        tenant_id, workspace_id, access_scope_id, resource_id, resource_kind
    ),
    CONSTRAINT entities_origin_mention_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        originating_entity_mention_id, originating_bead_version_id
    ) REFERENCES memoriesql.entity_mentions (
        tenant_id, workspace_id, access_scope_id,
        entity_mention_id, bead_version_id
    ),
    CONSTRAINT entities_origin_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, originating_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entities_creator_fk FOREIGN KEY (
        tenant_id, created_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entities_initial_status_supported
        CHECK (initial_status IN ('provisional', 'active')),
    CONSTRAINT entities_provisional_origin CHECK (
        initial_status <> 'provisional'
        OR originating_entity_mention_id IS NOT NULL
    )
);

CREATE TABLE memoriesql.entity_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_revision_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    revision integer NOT NULL,
    entity_type_id uuid,
    entity_type_revision_id uuid,
    required_entity_type_active boolean GENERATED ALWAYS AS (true) STORED,
    canonical_label text NOT NULL,
    description text,
    attributes jsonb NOT NULL,
    evidence_bead_version_id uuid NOT NULL,
    authored_by_principal_id uuid NOT NULL,
    authored_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_revisions_pk PRIMARY KEY (tenant_id, entity_revision_id),
    CONSTRAINT entity_revisions_identity_uq
        UNIQUE (tenant_id, entity_id, revision),
    CONSTRAINT entity_revisions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        entity_id, entity_revision_id
    ),
    CONSTRAINT entity_revisions_entity_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_revisions_type_fk FOREIGN KEY (
        entity_type_id, entity_type_revision_id, required_entity_type_active
    ) REFERENCES memoriesql.entity_type_revisions (
        entity_type_id, entity_type_revision_id, active
    ),
    CONSTRAINT entity_revisions_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entity_revisions_author_fk FOREIGN KEY (
        tenant_id, authored_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT entity_revisions_type_pair CHECK (
        (entity_type_id IS NULL AND entity_type_revision_id IS NULL)
        OR (entity_type_id IS NOT NULL AND entity_type_revision_id IS NOT NULL)
    ),
    CONSTRAINT entity_revisions_label_nonempty
        CHECK (btrim(canonical_label) <> ''),
    CONSTRAINT entity_revisions_description_nonempty
        CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT entity_revisions_attributes_object
        CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE FUNCTION memoriesql.serialize_bead_type_revision_writes()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        702,
        ('x' || substr(replace(NEW.bead_type_id::text, '-', ''), 1, 8))
            ::bit(32)::integer
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER bead_type_revisions_serialize
BEFORE INSERT ON memoriesql.bead_type_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.serialize_bead_type_revision_writes();

CREATE TRIGGER bead_versions_type_serialize
BEFORE INSERT ON memoriesql.bead_versions
FOR EACH ROW EXECUTE FUNCTION memoriesql.serialize_bead_type_revision_writes();

CREATE FUNCTION memoriesql.assert_latest_authorable_bead_type_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    latest_revision_id uuid;
    latest_authorable boolean;
BEGIN
    SELECT revision_record.bead_type_revision_id,
           revision_record.authorable
      INTO STRICT latest_revision_id, latest_authorable
      FROM memoriesql.bead_type_revisions AS revision_record
     WHERE revision_record.bead_type_id = NEW.bead_type_id
     ORDER BY revision_record.revision DESC
     LIMIT 1;

    IF latest_revision_id IS DISTINCT FROM NEW.bead_type_revision_id
       OR NOT latest_authorable THEN
        RAISE EXCEPTION 'bead version requires the latest authorable type revision'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER bead_versions_latest_authorable_type
AFTER INSERT ON memoriesql.bead_versions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION memoriesql.assert_latest_authorable_bead_type_revision();

CREATE FUNCTION memoriesql.serialize_entity_type_revision_writes()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.entity_type_id IS NOT NULL THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(
            703,
            ('x' || substr(replace(NEW.entity_type_id::text, '-', ''), 1, 8))
                ::bit(32)::integer
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER entity_type_revisions_serialize
BEFORE INSERT ON memoriesql.entity_type_revisions
FOR EACH ROW
EXECUTE FUNCTION memoriesql.serialize_entity_type_revision_writes();

CREATE TRIGGER entity_revisions_type_serialize
BEFORE INSERT ON memoriesql.entity_revisions
FOR EACH ROW
EXECUTE FUNCTION memoriesql.serialize_entity_type_revision_writes();

CREATE FUNCTION memoriesql.assert_latest_active_entity_type_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    latest_revision_id uuid;
    latest_active boolean;
BEGIN
    IF NEW.entity_type_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT revision_record.entity_type_revision_id,
           revision_record.active
      INTO STRICT latest_revision_id, latest_active
      FROM memoriesql.entity_type_revisions AS revision_record
     WHERE revision_record.entity_type_id = NEW.entity_type_id
     ORDER BY revision_record.revision DESC
     LIMIT 1;

    IF latest_revision_id IS DISTINCT FROM NEW.entity_type_revision_id
       OR NOT latest_active THEN
        RAISE EXCEPTION 'entity revision requires the latest active type revision'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER entity_revisions_latest_active_type
AFTER INSERT ON memoriesql.entity_revisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION memoriesql.assert_latest_active_entity_type_revision();

CREATE TABLE memoriesql.entity_aliases (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_alias_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    alias text NOT NULL,
    alias_kind text NOT NULL,
    language_code text,
    evidence_bead_version_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_aliases_pk PRIMARY KEY (tenant_id, entity_alias_id),
    CONSTRAINT entity_aliases_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, entity_alias_id
    ),
    CONSTRAINT entity_aliases_entity_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_aliases_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entity_aliases_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_aliases_alias_nonempty CHECK (btrim(alias) <> ''),
    CONSTRAINT entity_aliases_kind_supported
        CHECK (alias_kind IN ('name', 'acronym', 'identifier', 'nickname')),
    CONSTRAINT entity_aliases_language_grammar
        CHECK (
            language_code IS NULL
            OR language_code ~ '^[a-z]{2,3}(-[A-Z]{2})?$'
        )
);

CREATE TABLE memoriesql.entity_external_references (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_external_reference_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    source_system text NOT NULL,
    external_scope text NOT NULL,
    external_kind text NOT NULL,
    external_identifier text NOT NULL,
    evidence_bead_version_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_external_references_pk
        PRIMARY KEY (tenant_id, entity_external_reference_id),
    CONSTRAINT entity_external_references_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        entity_external_reference_id
    ),
    CONSTRAINT entity_external_references_provider_scope_uq UNIQUE (
        tenant_id, source_system, external_scope,
        external_kind, external_identifier
    ),
    CONSTRAINT entity_external_references_entity_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_external_references_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entity_external_references_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_external_references_source_system_grammar
        CHECK (source_system ~ '^[a-z][a-z0-9_.-]*$'),
    CONSTRAINT entity_external_references_scope_nonempty
        CHECK (btrim(external_scope) <> ''),
    CONSTRAINT entity_external_references_kind_grammar
        CHECK (external_kind ~ '^[a-z][a-z0-9_.-]*$'),
    CONSTRAINT entity_external_references_identifier_nonempty
        CHECK (btrim(external_identifier) <> '')
);

CREATE TABLE memoriesql.entity_external_reference_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_external_reference_event_id uuid NOT NULL,
    entity_external_reference_id uuid NOT NULL,
    event_sequence integer NOT NULL,
    event_kind text NOT NULL,
    target_entity_id uuid,
    evidence_bead_version_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_external_reference_events_pk PRIMARY KEY (
        tenant_id, entity_external_reference_event_id
    ),
    CONSTRAINT entity_external_reference_events_sequence_uq UNIQUE (
        tenant_id, entity_external_reference_id, event_sequence
    ),
    CONSTRAINT entity_external_reference_events_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        entity_external_reference_event_id
    ),
    CONSTRAINT entity_external_reference_events_reference_fk FOREIGN KEY (
        tenant_id, entity_external_reference_id
    ) REFERENCES memoriesql.entity_external_references (
        tenant_id, entity_external_reference_id
    ),
    CONSTRAINT entity_external_reference_events_target_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, target_entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_external_reference_events_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entity_external_reference_events_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_external_reference_events_sequence_positive
        CHECK (event_sequence > 0),
    CONSTRAINT entity_external_reference_events_kind_supported
        CHECK (event_kind IN ('superseded', 'retracted')),
    CONSTRAINT entity_external_reference_events_shape CHECK (
        (event_kind = 'superseded' AND target_entity_id IS NOT NULL)
        OR (event_kind = 'retracted' AND target_entity_id IS NULL)
    )
);

CREATE FUNCTION memoriesql.assert_next_external_reference_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence integer;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        704,
        ('x' || substr(
            replace(NEW.entity_external_reference_id::text, '-', ''), 1, 8
        ))::bit(32)::integer
    );

    SELECT COALESCE(max(event_record.event_sequence), 0) + 1
      INTO expected_sequence
      FROM memoriesql.entity_external_reference_events AS event_record
     WHERE event_record.tenant_id = NEW.tenant_id
       AND event_record.entity_external_reference_id =
           NEW.entity_external_reference_id;

    IF NEW.event_sequence <> expected_sequence THEN
        RAISE EXCEPTION 'external-reference event sequence must be %',
            expected_sequence USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER entity_external_reference_events_next
BEFORE INSERT ON memoriesql.entity_external_reference_events
FOR EACH ROW
EXECUTE FUNCTION memoriesql.assert_next_external_reference_event();

CREATE TABLE memoriesql.entity_mention_resolutions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_mention_resolution_id uuid NOT NULL,
    entity_mention_id uuid NOT NULL,
    decision_sequence integer NOT NULL,
    resolution_status text NOT NULL,
    resolved_entity_id uuid,
    evidence_bead_version_id uuid NOT NULL,
    decided_by_principal_id uuid NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_mention_resolutions_pk
        PRIMARY KEY (tenant_id, entity_mention_resolution_id),
    CONSTRAINT entity_mention_resolutions_sequence_uq
        UNIQUE (tenant_id, entity_mention_id, decision_sequence),
    CONSTRAINT entity_mention_resolutions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        entity_mention_resolution_id
    ),
    CONSTRAINT entity_mention_resolutions_mention_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, entity_mention_id
    ) REFERENCES memoriesql.entity_mentions (
        tenant_id, workspace_id, access_scope_id, entity_mention_id
    ),
    CONSTRAINT entity_mention_resolutions_entity_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, resolved_entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_mention_resolutions_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entity_mention_resolutions_actor_fk FOREIGN KEY (
        tenant_id, decided_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_mention_resolutions_sequence_positive
        CHECK (decision_sequence > 0),
    CONSTRAINT entity_mention_resolutions_status_supported
        CHECK (resolution_status IN ('unresolved', 'resolved', 'ambiguous', 'rejected')),
    CONSTRAINT entity_mention_resolutions_shape CHECK (
        (resolution_status = 'resolved' AND resolved_entity_id IS NOT NULL)
        OR (resolution_status <> 'resolved' AND resolved_entity_id IS NULL)
    )
);

CREATE TABLE memoriesql.entity_resolution_candidates (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_mention_resolution_id uuid NOT NULL,
    candidate_entity_id uuid NOT NULL,
    candidate_ordinal integer NOT NULL,
    CONSTRAINT entity_resolution_candidates_pk PRIMARY KEY (
        tenant_id, entity_mention_resolution_id, candidate_entity_id
    ),
    CONSTRAINT entity_resolution_candidates_ordinal_uq UNIQUE (
        tenant_id, entity_mention_resolution_id, candidate_ordinal
    ),
    CONSTRAINT entity_resolution_candidates_resolution_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        entity_mention_resolution_id
    ) REFERENCES memoriesql.entity_mention_resolutions (
        tenant_id, workspace_id, access_scope_id,
        entity_mention_resolution_id
    ),
    CONSTRAINT entity_resolution_candidates_entity_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, candidate_entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_resolution_candidates_ordinal_positive
        CHECK (candidate_ordinal > 0)
);

CREATE TABLE memoriesql.entity_identity_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    entity_identity_event_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    event_sequence integer NOT NULL,
    event_kind text NOT NULL,
    target_entity_id uuid,
    evidence_bead_version_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    effective_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT entity_identity_events_pk
        PRIMARY KEY (tenant_id, entity_identity_event_id),
    CONSTRAINT entity_identity_events_sequence_uq
        UNIQUE (tenant_id, entity_id, event_sequence),
    CONSTRAINT entity_identity_events_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, entity_identity_event_id
    ),
    CONSTRAINT entity_identity_events_entity_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_identity_events_target_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, target_entity_id
    ) REFERENCES memoriesql.entities (
        tenant_id, workspace_id, access_scope_id, entity_id
    ),
    CONSTRAINT entity_identity_events_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT entity_identity_events_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT entity_identity_events_sequence_positive CHECK (event_sequence > 0),
    CONSTRAINT entity_identity_events_kind_supported CHECK (
        event_kind IN ('activated', 'merged', 'split', 'deprecated', 'redirected')
    ),
    CONSTRAINT entity_identity_events_target_shape CHECK (
        (event_kind IN ('merged', 'split', 'redirected') AND target_entity_id IS NOT NULL)
        OR (event_kind IN ('activated', 'deprecated') AND target_entity_id IS NULL)
    ),
    CONSTRAINT entity_identity_events_not_self_target
        CHECK (target_entity_id IS NULL OR target_entity_id <> entity_id),
    CONSTRAINT entity_identity_events_time_order
        CHECK (recorded_at >= effective_at)
);

CREATE TABLE memoriesql.topic_mentions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    topic_mention_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    surface_text text NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT topic_mentions_pk PRIMARY KEY (tenant_id, topic_mention_id),
    CONSTRAINT topic_mentions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, topic_mention_id
    ),
    CONSTRAINT topic_mentions_origin_version_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        topic_mention_id, bead_version_id
    ),
    CONSTRAINT topic_mentions_bead_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ),
    CONSTRAINT topic_mentions_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT topic_mentions_surface_nonempty CHECK (btrim(surface_text) <> '')
);

CREATE TABLE memoriesql.topics (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    topic_id uuid NOT NULL,
    resource_kind text GENERATED ALWAYS AS ('model') STORED,
    originating_topic_mention_id uuid,
    originating_bead_version_id uuid NOT NULL,
    created_by_principal_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT topics_pk PRIMARY KEY (tenant_id, topic_id),
    CONSTRAINT topics_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, topic_id
    ),
    CONSTRAINT topics_protected_resource_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, topic_id, resource_kind
    ) REFERENCES memoriesql.protected_resources (
        tenant_id, workspace_id, access_scope_id, resource_id, resource_kind
    ),
    CONSTRAINT topics_origin_mention_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        originating_topic_mention_id, originating_bead_version_id
    ) REFERENCES memoriesql.topic_mentions (
        tenant_id, workspace_id, access_scope_id,
        topic_mention_id, bead_version_id
    ),
    CONSTRAINT topics_origin_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, originating_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT topics_creator_fk FOREIGN KEY (
        tenant_id, created_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE FUNCTION memoriesql.enforce_model_resource_binding()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    resource_status text;
BEGIN
    SELECT resource.status
      INTO STRICT resource_status
      FROM memoriesql.protected_resources AS resource
     WHERE resource.tenant_id = NEW.tenant_id
       AND resource.workspace_id = NEW.workspace_id
       AND resource.access_scope_id = NEW.access_scope_id
       AND resource.resource_kind = 'model'
       AND resource.resource_id = (to_jsonb(NEW) ->> TG_ARGV[0])::uuid
     FOR UPDATE;

    IF resource_status <> 'active' THEN
        RAISE EXCEPTION 'semantic root cannot bind a revoked model resource'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER entities_active_model_resource
BEFORE INSERT ON memoriesql.entities
FOR EACH ROW
EXECUTE FUNCTION memoriesql.enforce_model_resource_binding('entity_id');

CREATE TRIGGER topics_active_model_resource
BEFORE INSERT ON memoriesql.topics
FOR EACH ROW
EXECUTE FUNCTION memoriesql.enforce_model_resource_binding('topic_id');

CREATE TABLE memoriesql.topic_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    topic_revision_id uuid NOT NULL,
    topic_id uuid NOT NULL,
    revision integer NOT NULL,
    label text NOT NULL,
    description text,
    evidence_bead_version_id uuid NOT NULL,
    authored_by_principal_id uuid NOT NULL,
    authored_at timestamp with time zone NOT NULL,
    CONSTRAINT topic_revisions_pk PRIMARY KEY (tenant_id, topic_revision_id),
    CONSTRAINT topic_revisions_identity_uq
        UNIQUE (tenant_id, topic_id, revision),
    CONSTRAINT topic_revisions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, topic_id, topic_revision_id
    ),
    CONSTRAINT topic_revisions_topic_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, topic_id
    ) REFERENCES memoriesql.topics (
        tenant_id, workspace_id, access_scope_id, topic_id
    ),
    CONSTRAINT topic_revisions_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT topic_revisions_author_fk FOREIGN KEY (
        tenant_id, authored_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT topic_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT topic_revisions_label_nonempty CHECK (btrim(label) <> ''),
    CONSTRAINT topic_revisions_description_nonempty
        CHECK (description IS NULL OR btrim(description) <> '')
);

CREATE TABLE memoriesql.topic_mention_resolutions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    topic_mention_resolution_id uuid NOT NULL,
    topic_mention_id uuid NOT NULL,
    decision_sequence integer NOT NULL,
    resolution_status text NOT NULL,
    resolved_topic_id uuid,
    evidence_bead_version_id uuid NOT NULL,
    decided_by_principal_id uuid NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    CONSTRAINT topic_mention_resolutions_pk
        PRIMARY KEY (tenant_id, topic_mention_resolution_id),
    CONSTRAINT topic_mention_resolutions_sequence_uq
        UNIQUE (tenant_id, topic_mention_id, decision_sequence),
    CONSTRAINT topic_mention_resolutions_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id,
        topic_mention_resolution_id
    ),
    CONSTRAINT topic_mention_resolutions_mention_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, topic_mention_id
    ) REFERENCES memoriesql.topic_mentions (
        tenant_id, workspace_id, access_scope_id, topic_mention_id
    ),
    CONSTRAINT topic_mention_resolutions_topic_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, resolved_topic_id
    ) REFERENCES memoriesql.topics (
        tenant_id, workspace_id, access_scope_id, topic_id
    ),
    CONSTRAINT topic_mention_resolutions_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT topic_mention_resolutions_actor_fk FOREIGN KEY (
        tenant_id, decided_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT topic_mention_resolutions_sequence_positive
        CHECK (decision_sequence > 0),
    CONSTRAINT topic_mention_resolutions_status_supported CHECK (
        resolution_status IN ('unresolved', 'accepted', 'ambiguous', 'rejected')
    ),
    CONSTRAINT topic_mention_resolutions_shape CHECK (
        (resolution_status = 'accepted' AND resolved_topic_id IS NOT NULL)
        OR (resolution_status <> 'accepted' AND resolved_topic_id IS NULL)
    )
);

CREATE TABLE memoriesql.topic_resolution_candidates (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    topic_mention_resolution_id uuid NOT NULL,
    candidate_topic_id uuid NOT NULL,
    candidate_ordinal integer NOT NULL,
    CONSTRAINT topic_resolution_candidates_pk PRIMARY KEY (
        tenant_id, topic_mention_resolution_id, candidate_topic_id
    ),
    CONSTRAINT topic_resolution_candidates_ordinal_uq UNIQUE (
        tenant_id, topic_mention_resolution_id, candidate_ordinal
    ),
    CONSTRAINT topic_resolution_candidates_resolution_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id,
        topic_mention_resolution_id
    ) REFERENCES memoriesql.topic_mention_resolutions (
        tenant_id, workspace_id, access_scope_id,
        topic_mention_resolution_id
    ),
    CONSTRAINT topic_resolution_candidates_topic_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, candidate_topic_id
    ) REFERENCES memoriesql.topics (
        tenant_id, workspace_id, access_scope_id, topic_id
    ),
    CONSTRAINT topic_resolution_candidates_ordinal_positive
        CHECK (candidate_ordinal > 0)
);

CREATE TABLE memoriesql.bead_topic_links (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_topic_link_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    topic_id uuid NOT NULL,
    topic_revision_id uuid NOT NULL,
    evidence_bead_version_id uuid NOT NULL,
    accepted_by_principal_id uuid NOT NULL,
    accepted_at timestamp with time zone NOT NULL,
    CONSTRAINT bead_topic_links_pk PRIMARY KEY (tenant_id, bead_topic_link_id),
    CONSTRAINT bead_topic_links_identity_uq
        UNIQUE (tenant_id, bead_version_id, topic_id),
    CONSTRAINT bead_topic_links_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, bead_topic_link_id
    ),
    CONSTRAINT bead_topic_links_bead_version_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, event_id,
        source_unit_id, bead_id, bead_version_id
    ),
    CONSTRAINT bead_topic_links_topic_revision_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, topic_id, topic_revision_id
    ) REFERENCES memoriesql.topic_revisions (
        tenant_id, workspace_id, access_scope_id, topic_id, topic_revision_id
    ),
    CONSTRAINT bead_topic_links_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT bead_topic_links_actor_fk FOREIGN KEY (
        tenant_id, accepted_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE TABLE memoriesql.topic_identity_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    topic_identity_event_id uuid NOT NULL,
    topic_id uuid NOT NULL,
    event_sequence integer NOT NULL,
    event_kind text NOT NULL,
    target_topic_id uuid,
    evidence_bead_version_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    effective_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT topic_identity_events_pk
        PRIMARY KEY (tenant_id, topic_identity_event_id),
    CONSTRAINT topic_identity_events_sequence_uq
        UNIQUE (tenant_id, topic_id, event_sequence),
    CONSTRAINT topic_identity_events_scope_uq UNIQUE (
        tenant_id, workspace_id, access_scope_id, topic_identity_event_id
    ),
    CONSTRAINT topic_identity_events_topic_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, topic_id
    ) REFERENCES memoriesql.topics (
        tenant_id, workspace_id, access_scope_id, topic_id
    ),
    CONSTRAINT topic_identity_events_target_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, target_topic_id
    ) REFERENCES memoriesql.topics (
        tenant_id, workspace_id, access_scope_id, topic_id
    ),
    CONSTRAINT topic_identity_events_evidence_fk FOREIGN KEY (
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    ) REFERENCES memoriesql.bead_versions (
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ),
    CONSTRAINT topic_identity_events_actor_fk FOREIGN KEY (
        tenant_id, recorded_by_principal_id
    ) REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT topic_identity_events_sequence_positive CHECK (event_sequence > 0),
    CONSTRAINT topic_identity_events_kind_supported CHECK (
        event_kind IN ('merged', 'deprecated', 'redirected')
    ),
    CONSTRAINT topic_identity_events_target_shape CHECK (
        (event_kind IN ('merged', 'redirected') AND target_topic_id IS NOT NULL)
        OR (event_kind = 'deprecated' AND target_topic_id IS NULL)
    ),
    CONSTRAINT topic_identity_events_not_self_target
        CHECK (target_topic_id IS NULL OR target_topic_id <> topic_id),
    CONSTRAINT topic_identity_events_time_order
        CHECK (recorded_at >= effective_at)
);

CREATE FUNCTION memoriesql.assert_entity_resolution_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_count integer;
BEGIN
    SELECT count(*)::integer INTO candidate_count
    FROM memoriesql.entity_resolution_candidates AS candidate
    WHERE candidate.tenant_id = NEW.tenant_id
      AND candidate.entity_mention_resolution_id =
          NEW.entity_mention_resolution_id;

    IF NEW.resolution_status = 'ambiguous' AND candidate_count < 2 THEN
        RAISE EXCEPTION 'ambiguous resolution % requires at least two candidates',
            NEW.entity_mention_resolution_id USING ERRCODE = '23514';
    ELSIF NEW.resolution_status <> 'ambiguous' AND candidate_count <> 0 THEN
        RAISE EXCEPTION 'only ambiguous resolutions may retain candidates'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER entity_mention_resolutions_ready
AFTER INSERT ON memoriesql.entity_mention_resolutions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_entity_resolution_ready();

CREATE FUNCTION memoriesql.assert_entity_candidate_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_count integer;
    resolution_state text;
BEGIN
    SELECT resolution_record.resolution_status INTO STRICT resolution_state
    FROM memoriesql.entity_mention_resolutions AS resolution_record
    WHERE resolution_record.tenant_id = NEW.tenant_id
      AND resolution_record.entity_mention_resolution_id =
          NEW.entity_mention_resolution_id;

    SELECT count(*)::integer INTO candidate_count
    FROM memoriesql.entity_resolution_candidates AS candidate
    WHERE candidate.tenant_id = NEW.tenant_id
      AND candidate.entity_mention_resolution_id =
          NEW.entity_mention_resolution_id;

    IF resolution_state <> 'ambiguous' THEN
        RAISE EXCEPTION 'only ambiguous resolutions may retain candidates'
            USING ERRCODE = '23514';
    ELSIF candidate_count < 2 THEN
        RAISE EXCEPTION 'ambiguous resolution % requires at least two candidates',
            NEW.entity_mention_resolution_id USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER entity_resolution_candidates_ready
AFTER INSERT ON memoriesql.entity_resolution_candidates
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_entity_candidate_ready();

CREATE FUNCTION memoriesql.assert_topic_resolution_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_count integer;
BEGIN
    SELECT count(*)::integer INTO candidate_count
    FROM memoriesql.topic_resolution_candidates AS candidate
    WHERE candidate.tenant_id = NEW.tenant_id
      AND candidate.topic_mention_resolution_id =
          NEW.topic_mention_resolution_id;

    IF NEW.resolution_status = 'ambiguous' AND candidate_count < 2 THEN
        RAISE EXCEPTION 'ambiguous topic resolution % requires at least two candidates',
            NEW.topic_mention_resolution_id USING ERRCODE = '23514';
    ELSIF NEW.resolution_status <> 'ambiguous' AND candidate_count <> 0 THEN
        RAISE EXCEPTION 'only ambiguous topic resolutions may retain candidates'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER topic_mention_resolutions_ready
AFTER INSERT ON memoriesql.topic_mention_resolutions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_topic_resolution_ready();

CREATE FUNCTION memoriesql.assert_topic_candidate_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_count integer;
    resolution_state text;
BEGIN
    SELECT resolution_record.resolution_status INTO STRICT resolution_state
    FROM memoriesql.topic_mention_resolutions AS resolution_record
    WHERE resolution_record.tenant_id = NEW.tenant_id
      AND resolution_record.topic_mention_resolution_id =
          NEW.topic_mention_resolution_id;

    SELECT count(*)::integer INTO candidate_count
    FROM memoriesql.topic_resolution_candidates AS candidate
    WHERE candidate.tenant_id = NEW.tenant_id
      AND candidate.topic_mention_resolution_id =
          NEW.topic_mention_resolution_id;

    IF resolution_state <> 'ambiguous' THEN
        RAISE EXCEPTION 'only ambiguous topic resolutions may retain candidates'
            USING ERRCODE = '23514';
    ELSIF candidate_count < 2 THEN
        RAISE EXCEPTION 'ambiguous topic resolution % requires at least two candidates',
            NEW.topic_mention_resolution_id USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER topic_resolution_candidates_ready
AFTER INSERT ON memoriesql.topic_resolution_candidates
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_topic_candidate_ready();

CREATE FUNCTION memoriesql.serialize_semantic_identity_redirects()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.event_kind IN ('merged', 'redirected') THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(
            701,
            ('x' || substr(replace(NEW.tenant_id::text, '-', ''), 1, 8))
                ::bit(32)::integer
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER entity_identity_events_serialize_redirect
BEFORE INSERT ON memoriesql.entity_identity_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.serialize_semantic_identity_redirects();

CREATE TRIGGER topic_identity_events_serialize_redirect
BEFORE INSERT ON memoriesql.topic_identity_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.serialize_semantic_identity_redirects();

CREATE FUNCTION memoriesql.assert_entity_redirect_acyclic()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    check_at timestamp with time zone;
    cycle_found boolean;
BEGIN
    IF NEW.event_kind NOT IN ('merged', 'redirected') THEN
        RETURN NULL;
    END IF;

    FOR check_at IN
        SELECT DISTINCT identity_event.effective_at
        FROM memoriesql.entity_identity_events AS identity_event
        WHERE identity_event.tenant_id = NEW.tenant_id
          AND identity_event.event_kind IN ('merged', 'redirected')
          AND identity_event.effective_at >= NEW.effective_at
        ORDER BY identity_event.effective_at
    LOOP
        WITH RECURSIVE active_redirects AS (
            SELECT DISTINCT ON (identity_event.entity_id)
                   identity_event.entity_id,
                   identity_event.target_entity_id
            FROM memoriesql.entity_identity_events AS identity_event
            WHERE identity_event.tenant_id = NEW.tenant_id
              AND identity_event.event_kind IN ('merged', 'redirected')
              AND identity_event.effective_at <= check_at
            ORDER BY identity_event.entity_id,
                     identity_event.effective_at DESC,
                     identity_event.event_sequence DESC
        ), redirect_walk(origin_entity_id, entity_id, path, cycle) AS (
            SELECT redirect.entity_id,
                   redirect.target_entity_id,
                   ARRAY[redirect.entity_id, redirect.target_entity_id],
                   redirect.target_entity_id = redirect.entity_id
            FROM active_redirects AS redirect
            UNION ALL
            SELECT redirect_walk.origin_entity_id,
                   redirect.target_entity_id,
                   redirect_walk.path || redirect.target_entity_id,
                   redirect.target_entity_id = ANY(redirect_walk.path)
            FROM redirect_walk
            JOIN active_redirects AS redirect
              ON redirect.entity_id = redirect_walk.entity_id
            WHERE NOT redirect_walk.cycle
        )
        SELECT COALESCE(bool_or(redirect_walk.cycle), false)
        INTO cycle_found
        FROM redirect_walk;

        IF cycle_found THEN
            RAISE EXCEPTION 'entity redirect creates a cycle at %', check_at
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER entity_identity_events_acyclic
AFTER INSERT ON memoriesql.entity_identity_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_entity_redirect_acyclic();

CREATE FUNCTION memoriesql.assert_topic_redirect_acyclic()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    check_at timestamp with time zone;
    cycle_found boolean;
BEGIN
    IF NEW.event_kind NOT IN ('merged', 'redirected') THEN
        RETURN NULL;
    END IF;

    FOR check_at IN
        SELECT DISTINCT identity_event.effective_at
        FROM memoriesql.topic_identity_events AS identity_event
        WHERE identity_event.tenant_id = NEW.tenant_id
          AND identity_event.event_kind IN ('merged', 'redirected')
          AND identity_event.effective_at >= NEW.effective_at
        ORDER BY identity_event.effective_at
    LOOP
        WITH RECURSIVE active_redirects AS (
            SELECT DISTINCT ON (identity_event.topic_id)
                   identity_event.topic_id,
                   identity_event.target_topic_id
            FROM memoriesql.topic_identity_events AS identity_event
            WHERE identity_event.tenant_id = NEW.tenant_id
              AND identity_event.event_kind IN ('merged', 'redirected')
              AND identity_event.effective_at <= check_at
            ORDER BY identity_event.topic_id,
                     identity_event.effective_at DESC,
                     identity_event.event_sequence DESC
        ), redirect_walk(origin_topic_id, topic_id, path, cycle) AS (
            SELECT redirect.topic_id,
                   redirect.target_topic_id,
                   ARRAY[redirect.topic_id, redirect.target_topic_id],
                   redirect.target_topic_id = redirect.topic_id
            FROM active_redirects AS redirect
            UNION ALL
            SELECT redirect_walk.origin_topic_id,
                   redirect.target_topic_id,
                   redirect_walk.path || redirect.target_topic_id,
                   redirect.target_topic_id = ANY(redirect_walk.path)
            FROM redirect_walk
            JOIN active_redirects AS redirect
              ON redirect.topic_id = redirect_walk.topic_id
            WHERE NOT redirect_walk.cycle
        )
        SELECT COALESCE(bool_or(redirect_walk.cycle), false)
        INTO cycle_found
        FROM redirect_walk;

        IF cycle_found THEN
            RAISE EXCEPTION 'topic redirect creates a cycle at %', check_at
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER topic_identity_events_acyclic
AFTER INSERT ON memoriesql.topic_identity_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_topic_redirect_acyclic();

CREATE VIEW memoriesql.current_bead_type_revisions AS
WITH latest_revisions AS (
    SELECT DISTINCT ON (revision_record.bead_type_id)
           revision_record.bead_type_id,
           type_record.stable_key,
           revision_record.bead_type_revision_id,
           revision_record.revision,
           revision_record.display_name,
           revision_record.definition,
           revision_record.authorable
    FROM memoriesql.bead_type_revisions AS revision_record
    JOIN memoriesql.bead_types AS type_record
      ON type_record.bead_type_id = revision_record.bead_type_id
    ORDER BY revision_record.bead_type_id, revision_record.revision DESC
)
SELECT bead_type_id,
       stable_key,
       bead_type_revision_id,
       revision,
       display_name,
       definition
FROM latest_revisions
WHERE authorable;

CREATE VIEW memoriesql.current_entity_type_revisions AS
WITH latest_revisions AS (
    SELECT DISTINCT ON (revision_record.entity_type_id)
           revision_record.entity_type_id,
           type_record.stable_key,
           revision_record.entity_type_revision_id,
           revision_record.revision,
           revision_record.display_name,
           revision_record.definition,
           revision_record.active
    FROM memoriesql.entity_type_revisions AS revision_record
    JOIN memoriesql.entity_types AS type_record
      ON type_record.entity_type_id = revision_record.entity_type_id
    ORDER BY revision_record.entity_type_id, revision_record.revision DESC
)
SELECT entity_type_id,
       stable_key,
       entity_type_revision_id,
       revision,
       display_name,
       definition
FROM latest_revisions
WHERE active;

CREATE VIEW memoriesql.current_bead_versions
WITH (security_invoker = true) AS
SELECT DISTINCT ON (version_record.tenant_id, version_record.bead_id)
       version_record.*
FROM memoriesql.bead_versions AS version_record
ORDER BY version_record.tenant_id,
         version_record.bead_id,
         version_record.version DESC;

CREATE FUNCTION memoriesql.current_context_model_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_model_resource_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT memoriesql.current_context_scope_authorized(
               requested_access_scope_id,
               'subject_model.read',
               'read'
           )
       AND EXISTS (
            SELECT 1
            FROM memoriesql.current_authorization_context() AS context
            JOIN memoriesql.protected_resources AS resource
              ON resource.tenant_id = context.tenant_id
             AND resource.workspace_id = context.workspace_id
             AND resource.access_scope_id = requested_access_scope_id
             AND resource.resource_kind = 'model'
             AND resource.resource_id = requested_model_resource_id
             AND resource.status = 'active'
            WHERE context.tenant_id = requested_tenant_id
              AND context.workspace_id = requested_workspace_id
       )
$$;

CREATE FUNCTION memoriesql.current_context_bead_version_authorized(
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
$$;

CREATE FUNCTION memoriesql.current_context_entity_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_entity_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT memoriesql.current_context_model_authorized(
               requested_tenant_id,
               requested_workspace_id,
               requested_access_scope_id,
               requested_entity_id
           )
       AND EXISTS (
            SELECT 1
            FROM memoriesql.entities AS entity_record
            WHERE entity_record.tenant_id = requested_tenant_id
              AND entity_record.workspace_id = requested_workspace_id
              AND entity_record.access_scope_id = requested_access_scope_id
              AND entity_record.entity_id = requested_entity_id
              AND memoriesql.current_context_bead_version_authorized(
                      entity_record.tenant_id,
                      entity_record.workspace_id,
                      entity_record.access_scope_id,
                      entity_record.originating_bead_version_id
                  )
       )
$$;

CREATE FUNCTION memoriesql.current_context_topic_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_topic_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT memoriesql.current_context_model_authorized(
               requested_tenant_id,
               requested_workspace_id,
               requested_access_scope_id,
               requested_topic_id
           )
       AND EXISTS (
            SELECT 1
            FROM memoriesql.topics AS topic_record
            WHERE topic_record.tenant_id = requested_tenant_id
              AND topic_record.workspace_id = requested_workspace_id
              AND topic_record.access_scope_id = requested_access_scope_id
              AND topic_record.topic_id = requested_topic_id
              AND memoriesql.current_context_bead_version_authorized(
                      topic_record.tenant_id,
                      topic_record.workspace_id,
                      topic_record.access_scope_id,
                      topic_record.originating_bead_version_id
                  )
       )
$$;

CREATE FUNCTION memoriesql.current_context_topic_revision_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_topic_id uuid,
    requested_topic_revision_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT memoriesql.current_context_topic_authorized(
               requested_tenant_id,
               requested_workspace_id,
               requested_access_scope_id,
               requested_topic_id
           )
       AND EXISTS (
            SELECT 1
            FROM memoriesql.topic_revisions AS revision_record
            WHERE revision_record.tenant_id = requested_tenant_id
              AND revision_record.workspace_id = requested_workspace_id
              AND revision_record.access_scope_id = requested_access_scope_id
              AND revision_record.topic_id = requested_topic_id
              AND revision_record.topic_revision_id = requested_topic_revision_id
              AND memoriesql.current_context_bead_version_authorized(
                      revision_record.tenant_id,
                      revision_record.workspace_id,
                      revision_record.access_scope_id,
                      revision_record.evidence_bead_version_id
                  )
       )
$$;

CREATE FUNCTION memoriesql.current_context_entity_candidates_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_entity_mention_resolution_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT count(*) >= 2
       AND COALESCE(
               bool_and(
                   memoriesql.current_context_entity_authorized(
                       candidate.tenant_id,
                       candidate.workspace_id,
                       candidate.access_scope_id,
                       candidate.candidate_entity_id
                   )
               ),
               false
           )
    FROM memoriesql.entity_resolution_candidates AS candidate
    WHERE candidate.tenant_id = requested_tenant_id
      AND candidate.workspace_id = requested_workspace_id
      AND candidate.access_scope_id = requested_access_scope_id
      AND candidate.entity_mention_resolution_id =
          requested_entity_mention_resolution_id
$$;

CREATE FUNCTION memoriesql.current_context_topic_candidates_authorized(
    requested_tenant_id uuid,
    requested_workspace_id uuid,
    requested_access_scope_id uuid,
    requested_topic_mention_resolution_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT count(*) >= 2
       AND COALESCE(
               bool_and(
                   memoriesql.current_context_topic_authorized(
                       candidate.tenant_id,
                       candidate.workspace_id,
                       candidate.access_scope_id,
                       candidate.candidate_topic_id
                   )
               ),
               false
           )
    FROM memoriesql.topic_resolution_candidates AS candidate
    WHERE candidate.tenant_id = requested_tenant_id
      AND candidate.workspace_id = requested_workspace_id
      AND candidate.access_scope_id = requested_access_scope_id
      AND candidate.topic_mention_resolution_id =
          requested_topic_mention_resolution_id
$$;

CREATE FUNCTION memoriesql.current_context_entity_mention_resolutions()
RETURNS SETOF memoriesql.entity_mention_resolutions
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT latest.*
    FROM (
        SELECT DISTINCT ON (
                   resolution_record.tenant_id,
                   resolution_record.entity_mention_id
               ) resolution_record.*
        FROM memoriesql.entity_mention_resolutions AS resolution_record
        JOIN memoriesql.current_authorization_context() AS scope_context
          ON scope_context.tenant_id = resolution_record.tenant_id
         AND scope_context.workspace_id = resolution_record.workspace_id
        ORDER BY resolution_record.tenant_id,
                 resolution_record.entity_mention_id,
                 resolution_record.decision_sequence DESC
    ) AS latest
    WHERE EXISTS (
              SELECT 1
              FROM memoriesql.current_authorization_context() AS context
              JOIN memoriesql.entity_mentions AS mention
                ON mention.tenant_id = latest.tenant_id
               AND mention.workspace_id = latest.workspace_id
               AND mention.access_scope_id = latest.access_scope_id
               AND mention.entity_mention_id = latest.entity_mention_id
              WHERE context.tenant_id = latest.tenant_id
                AND context.workspace_id = latest.workspace_id
                AND memoriesql.current_context_event_authorized(
                        mention.access_scope_id,
                        mention.event_id,
                        'source.read',
                        'read'
                    )
          )
      AND (
          latest.resolved_entity_id IS NULL
          OR memoriesql.current_context_entity_authorized(
                 latest.tenant_id,
                 latest.workspace_id,
                 latest.access_scope_id,
                 latest.resolved_entity_id
             )
      )
      AND (
          latest.resolution_status <> 'ambiguous'
          OR memoriesql.current_context_entity_candidates_authorized(
                 latest.tenant_id,
                 latest.workspace_id,
                 latest.access_scope_id,
                 latest.entity_mention_resolution_id
             )
      )
      AND memoriesql.current_context_bead_version_authorized(
              latest.tenant_id,
              latest.workspace_id,
              latest.access_scope_id,
              latest.evidence_bead_version_id
          )
$$;

CREATE VIEW memoriesql.current_entity_mention_resolutions
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_entity_mention_resolutions();

CREATE FUNCTION memoriesql.current_context_topic_mention_resolutions()
RETURNS SETOF memoriesql.topic_mention_resolutions
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT latest.*
    FROM (
        SELECT DISTINCT ON (
                   resolution_record.tenant_id,
                   resolution_record.topic_mention_id
               ) resolution_record.*
        FROM memoriesql.topic_mention_resolutions AS resolution_record
        JOIN memoriesql.current_authorization_context() AS scope_context
          ON scope_context.tenant_id = resolution_record.tenant_id
         AND scope_context.workspace_id = resolution_record.workspace_id
        ORDER BY resolution_record.tenant_id,
                 resolution_record.topic_mention_id,
                 resolution_record.decision_sequence DESC
    ) AS latest
    WHERE EXISTS (
              SELECT 1
              FROM memoriesql.current_authorization_context() AS context
              JOIN memoriesql.topic_mentions AS mention
                ON mention.tenant_id = latest.tenant_id
               AND mention.workspace_id = latest.workspace_id
               AND mention.access_scope_id = latest.access_scope_id
               AND mention.topic_mention_id = latest.topic_mention_id
              WHERE context.tenant_id = latest.tenant_id
                AND context.workspace_id = latest.workspace_id
                AND memoriesql.current_context_event_authorized(
                        mention.access_scope_id,
                        mention.event_id,
                        'source.read',
                        'read'
                    )
          )
      AND (
          latest.resolved_topic_id IS NULL
          OR memoriesql.current_context_topic_authorized(
                 latest.tenant_id,
                 latest.workspace_id,
                 latest.access_scope_id,
                 latest.resolved_topic_id
             )
      )
      AND (
          latest.resolution_status <> 'ambiguous'
          OR memoriesql.current_context_topic_candidates_authorized(
                 latest.tenant_id,
                 latest.workspace_id,
                 latest.access_scope_id,
                 latest.topic_mention_resolution_id
             )
      )
      AND memoriesql.current_context_bead_version_authorized(
              latest.tenant_id,
              latest.workspace_id,
              latest.access_scope_id,
              latest.evidence_bead_version_id
          )
$$;

CREATE VIEW memoriesql.current_topic_mention_resolutions
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_topic_mention_resolutions();

CREATE FUNCTION memoriesql.current_context_entity_revisions()
RETURNS SETOF memoriesql.entity_revisions
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT latest.*
    FROM (
        SELECT DISTINCT ON (
                   revision_record.tenant_id,
                   revision_record.entity_id
               ) revision_record.*
        FROM memoriesql.entity_revisions AS revision_record
        JOIN memoriesql.current_authorization_context() AS scope_context
          ON scope_context.tenant_id = revision_record.tenant_id
         AND scope_context.workspace_id = revision_record.workspace_id
        ORDER BY revision_record.tenant_id,
                 revision_record.entity_id,
                 revision_record.revision DESC
    ) AS latest
    WHERE memoriesql.current_context_entity_authorized(
              latest.tenant_id,
              latest.workspace_id,
              latest.access_scope_id,
              latest.entity_id
          )
      AND memoriesql.current_context_bead_version_authorized(
              latest.tenant_id,
              latest.workspace_id,
              latest.access_scope_id,
              latest.evidence_bead_version_id
          )
$$;

CREATE VIEW memoriesql.current_entity_revisions
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_entity_revisions();

CREATE FUNCTION memoriesql.current_context_topic_revisions()
RETURNS SETOF memoriesql.topic_revisions
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT latest.*
    FROM (
        SELECT DISTINCT ON (
                   revision_record.tenant_id,
                   revision_record.topic_id
               ) revision_record.*
        FROM memoriesql.topic_revisions AS revision_record
        JOIN memoriesql.current_authorization_context() AS scope_context
          ON scope_context.tenant_id = revision_record.tenant_id
         AND scope_context.workspace_id = revision_record.workspace_id
        ORDER BY revision_record.tenant_id,
                 revision_record.topic_id,
                 revision_record.revision DESC
    ) AS latest
    WHERE memoriesql.current_context_topic_authorized(
              latest.tenant_id,
              latest.workspace_id,
              latest.access_scope_id,
              latest.topic_id
          )
      AND memoriesql.current_context_bead_version_authorized(
              latest.tenant_id,
              latest.workspace_id,
              latest.access_scope_id,
              latest.evidence_bead_version_id
          )
$$;

CREATE VIEW memoriesql.current_topic_revisions
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_topic_revisions();

CREATE FUNCTION memoriesql.current_context_entity_external_references()
RETURNS TABLE (
    tenant_id uuid,
    workspace_id uuid,
    access_scope_id uuid,
    entity_external_reference_id uuid,
    entity_id uuid,
    source_system text,
    external_scope text,
    external_kind text,
    external_identifier text,
    evidence_bead_version_id uuid,
    recorded_by_principal_id uuid,
    recorded_at timestamp with time zone
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT reference_record.tenant_id,
           COALESCE(
               latest_event.workspace_id,
               reference_record.workspace_id
           ),
           COALESCE(
               latest_event.access_scope_id,
               reference_record.access_scope_id
           ),
           reference_record.entity_external_reference_id,
           COALESCE(latest_event.target_entity_id, reference_record.entity_id),
           reference_record.source_system,
           reference_record.external_scope,
           reference_record.external_kind,
           reference_record.external_identifier,
           COALESCE(
               latest_event.evidence_bead_version_id,
               reference_record.evidence_bead_version_id
           ),
           COALESCE(
               latest_event.recorded_by_principal_id,
               reference_record.recorded_by_principal_id
           ),
           COALESCE(latest_event.recorded_at, reference_record.recorded_at)
    FROM memoriesql.current_authorization_context() AS scope_context
    JOIN memoriesql.entity_external_references AS reference_record
      ON reference_record.tenant_id = scope_context.tenant_id
    LEFT JOIN LATERAL (
        SELECT event_record.entity_external_reference_event_id,
               event_record.workspace_id,
               event_record.access_scope_id,
               event_record.event_kind,
               event_record.target_entity_id,
               event_record.evidence_bead_version_id,
               event_record.recorded_by_principal_id,
               event_record.recorded_at
        FROM memoriesql.entity_external_reference_events AS event_record
        WHERE event_record.tenant_id = reference_record.tenant_id
          AND event_record.entity_external_reference_id =
              reference_record.entity_external_reference_id
        ORDER BY event_record.event_sequence DESC
        LIMIT 1
    ) AS latest_event ON true
    WHERE scope_context.workspace_id = COALESCE(
              latest_event.workspace_id,
              reference_record.workspace_id
          )
      AND latest_event.event_kind IS DISTINCT FROM 'retracted'
      AND (
          (
              latest_event.entity_external_reference_event_id IS NULL
              AND memoriesql.current_context_bead_version_authorized(
                      reference_record.tenant_id,
                      reference_record.workspace_id,
                      reference_record.access_scope_id,
                      reference_record.evidence_bead_version_id
                  )
              AND memoriesql.current_context_entity_authorized(
                      reference_record.tenant_id,
                      reference_record.workspace_id,
                      reference_record.access_scope_id,
                      reference_record.entity_id
                  )
          )
          OR (
              latest_event.entity_external_reference_event_id IS NOT NULL
              AND memoriesql.current_context_bead_version_authorized(
                      reference_record.tenant_id,
                      latest_event.workspace_id,
                      latest_event.access_scope_id,
                      latest_event.evidence_bead_version_id
                  )
              AND memoriesql.current_context_entity_authorized(
                      reference_record.tenant_id,
                      latest_event.workspace_id,
                      latest_event.access_scope_id,
                      latest_event.target_entity_id
                  )
          )
      )
$$;

CREATE VIEW memoriesql.current_entity_external_references
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_entity_external_references();

CREATE FUNCTION memoriesql.current_context_entity_redirect_history()
RETURNS TABLE (
    tenant_id uuid,
    workspace_id uuid,
    access_scope_id uuid,
    entity_id uuid,
    target_entity_id uuid,
    entity_identity_event_id uuid,
    valid_from timestamp with time zone,
    valid_to timestamp with time zone
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT history.tenant_id,
           history.workspace_id,
           history.access_scope_id,
           history.entity_id,
           history.target_entity_id,
           history.entity_identity_event_id,
           history.valid_from,
           history.valid_to
    FROM (
        SELECT event_record.tenant_id,
               event_record.workspace_id,
               event_record.access_scope_id,
               event_record.entity_id,
               event_record.target_entity_id,
               event_record.entity_identity_event_id,
               event_record.evidence_bead_version_id,
               event_record.effective_at AS valid_from,
               lead(event_record.effective_at) OVER (
                   PARTITION BY event_record.tenant_id, event_record.entity_id
                   ORDER BY event_record.effective_at,
                            event_record.event_sequence
               ) AS valid_to,
               lead(event_record.target_entity_id) OVER (
                   PARTITION BY event_record.tenant_id, event_record.entity_id
                   ORDER BY event_record.effective_at,
                            event_record.event_sequence
               ) AS next_target_entity_id,
               lead(event_record.evidence_bead_version_id) OVER (
                   PARTITION BY event_record.tenant_id, event_record.entity_id
                   ORDER BY event_record.effective_at,
                            event_record.event_sequence
               ) AS next_evidence_bead_version_id
        FROM memoriesql.entity_identity_events AS event_record
        JOIN memoriesql.current_authorization_context() AS scope_context
          ON scope_context.tenant_id = event_record.tenant_id
         AND scope_context.workspace_id = event_record.workspace_id
        WHERE event_record.event_kind IN ('merged', 'redirected')
    ) AS history
    WHERE memoriesql.current_context_entity_authorized(
              history.tenant_id, history.workspace_id,
              history.access_scope_id, history.entity_id
          )
      AND memoriesql.current_context_entity_authorized(
              history.tenant_id, history.workspace_id,
              history.access_scope_id, history.target_entity_id
          )
      AND memoriesql.current_context_bead_version_authorized(
              history.tenant_id, history.workspace_id,
              history.access_scope_id, history.evidence_bead_version_id
          )
      AND (
          history.valid_to IS NULL
          OR (
              memoriesql.current_context_entity_authorized(
                  history.tenant_id, history.workspace_id,
                  history.access_scope_id, history.next_target_entity_id
              )
              AND memoriesql.current_context_bead_version_authorized(
                  history.tenant_id, history.workspace_id,
                  history.access_scope_id,
                  history.next_evidence_bead_version_id
              )
          )
      )
$$;

CREATE VIEW memoriesql.entity_redirects_as_of
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_entity_redirect_history();

CREATE VIEW memoriesql.current_entity_redirects
WITH (security_invoker = true) AS
SELECT history.tenant_id,
       history.workspace_id,
       history.access_scope_id,
       history.entity_id,
       history.target_entity_id,
       history.entity_identity_event_id,
       history.valid_from AS effective_at
FROM memoriesql.current_context_entity_redirect_history() AS history
WHERE history.valid_to IS NULL;

CREATE FUNCTION memoriesql.current_context_topic_redirect_history()
RETURNS TABLE (
    tenant_id uuid,
    workspace_id uuid,
    access_scope_id uuid,
    topic_id uuid,
    target_topic_id uuid,
    topic_identity_event_id uuid,
    valid_from timestamp with time zone,
    valid_to timestamp with time zone
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    SELECT history.tenant_id,
           history.workspace_id,
           history.access_scope_id,
           history.topic_id,
           history.target_topic_id,
           history.topic_identity_event_id,
           history.valid_from,
           history.valid_to
    FROM (
        SELECT event_record.tenant_id,
               event_record.workspace_id,
               event_record.access_scope_id,
               event_record.topic_id,
               event_record.target_topic_id,
               event_record.topic_identity_event_id,
               event_record.evidence_bead_version_id,
               event_record.effective_at AS valid_from,
               lead(event_record.effective_at) OVER (
                   PARTITION BY event_record.tenant_id, event_record.topic_id
                   ORDER BY event_record.effective_at,
                            event_record.event_sequence
               ) AS valid_to,
               lead(event_record.target_topic_id) OVER (
                   PARTITION BY event_record.tenant_id, event_record.topic_id
                   ORDER BY event_record.effective_at,
                            event_record.event_sequence
               ) AS next_target_topic_id,
               lead(event_record.evidence_bead_version_id) OVER (
                   PARTITION BY event_record.tenant_id, event_record.topic_id
                   ORDER BY event_record.effective_at,
                            event_record.event_sequence
               ) AS next_evidence_bead_version_id
        FROM memoriesql.topic_identity_events AS event_record
        JOIN memoriesql.current_authorization_context() AS scope_context
          ON scope_context.tenant_id = event_record.tenant_id
         AND scope_context.workspace_id = event_record.workspace_id
        WHERE event_record.event_kind IN ('merged', 'redirected')
    ) AS history
    WHERE memoriesql.current_context_topic_authorized(
              history.tenant_id, history.workspace_id,
              history.access_scope_id, history.topic_id
          )
      AND memoriesql.current_context_topic_authorized(
              history.tenant_id, history.workspace_id,
              history.access_scope_id, history.target_topic_id
          )
      AND memoriesql.current_context_bead_version_authorized(
              history.tenant_id, history.workspace_id,
              history.access_scope_id, history.evidence_bead_version_id
          )
      AND (
          history.valid_to IS NULL
          OR (
              memoriesql.current_context_topic_authorized(
                  history.tenant_id, history.workspace_id,
                  history.access_scope_id, history.next_target_topic_id
              )
              AND memoriesql.current_context_bead_version_authorized(
                  history.tenant_id, history.workspace_id,
                  history.access_scope_id,
                  history.next_evidence_bead_version_id
              )
          )
      )
$$;

CREATE VIEW memoriesql.topic_redirects_as_of
WITH (security_invoker = true) AS
SELECT * FROM memoriesql.current_context_topic_redirect_history();

CREATE VIEW memoriesql.current_topic_redirects
WITH (security_invoker = true) AS
SELECT history.tenant_id,
       history.workspace_id,
       history.access_scope_id,
       history.topic_id,
       history.target_topic_id,
       history.topic_identity_event_id,
       history.valid_from AS effective_at
FROM memoriesql.current_context_topic_redirect_history() AS history
WHERE history.valid_to IS NULL;

CREATE FUNCTION memoriesql.resolve_entity_as_of(
    requested_tenant_id uuid,
    requested_entity_id uuid,
    requested_at timestamp with time zone
)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    WITH RECURSIVE resolution(entity_id, path, authorized) AS (
        SELECT requested_entity_id,
               ARRAY[requested_entity_id],
               EXISTS (
                   SELECT 1
                   FROM memoriesql.entities AS root
                   WHERE root.tenant_id = requested_tenant_id
                     AND root.entity_id = requested_entity_id
                     AND memoriesql.current_context_entity_authorized(
                             root.tenant_id, root.workspace_id,
                             root.access_scope_id, root.entity_id
                         )
               )
        UNION ALL
        SELECT next_event.target_entity_id,
               resolution.path || next_event.target_entity_id,
               next_event.authorized
        FROM resolution
        JOIN LATERAL (
            SELECT event_record.target_entity_id,
                   memoriesql.current_context_entity_authorized(
                       event_record.tenant_id, event_record.workspace_id,
                       event_record.access_scope_id, event_record.entity_id
                   )
                   AND memoriesql.current_context_entity_authorized(
                       event_record.tenant_id, event_record.workspace_id,
                       event_record.access_scope_id,
                       event_record.target_entity_id
                   )
                   AND memoriesql.current_context_bead_version_authorized(
                       event_record.tenant_id, event_record.workspace_id,
                       event_record.access_scope_id,
                       event_record.evidence_bead_version_id
                   ) AS authorized
            FROM memoriesql.entity_identity_events AS event_record
            WHERE event_record.tenant_id = requested_tenant_id
              AND event_record.entity_id = resolution.entity_id
              AND event_record.event_kind IN ('merged', 'redirected')
              AND event_record.effective_at <= requested_at
            ORDER BY event_record.effective_at DESC,
                     event_record.event_sequence DESC
            LIMIT 1
        ) AS next_event ON resolution.authorized
        WHERE next_event.target_entity_id <> ALL(resolution.path)
    )
    SELECT CASE WHEN authorized THEN entity_id END
    FROM resolution
    ORDER BY cardinality(path) DESC
    LIMIT 1
$$;

CREATE FUNCTION memoriesql.resolve_topic_as_of(
    requested_tenant_id uuid,
    requested_topic_id uuid,
    requested_at timestamp with time zone
)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
SET row_security = off
AS $$
    WITH RECURSIVE resolution(topic_id, path, authorized) AS (
        SELECT requested_topic_id,
               ARRAY[requested_topic_id],
               EXISTS (
                   SELECT 1
                   FROM memoriesql.topics AS root
                   WHERE root.tenant_id = requested_tenant_id
                     AND root.topic_id = requested_topic_id
                     AND memoriesql.current_context_topic_authorized(
                             root.tenant_id, root.workspace_id,
                             root.access_scope_id, root.topic_id
                         )
               )
        UNION ALL
        SELECT next_event.target_topic_id,
               resolution.path || next_event.target_topic_id,
               next_event.authorized
        FROM resolution
        JOIN LATERAL (
            SELECT event_record.target_topic_id,
                   memoriesql.current_context_topic_authorized(
                       event_record.tenant_id, event_record.workspace_id,
                       event_record.access_scope_id, event_record.topic_id
                   )
                   AND memoriesql.current_context_topic_authorized(
                       event_record.tenant_id, event_record.workspace_id,
                       event_record.access_scope_id,
                       event_record.target_topic_id
                   )
                   AND memoriesql.current_context_bead_version_authorized(
                       event_record.tenant_id, event_record.workspace_id,
                       event_record.access_scope_id,
                       event_record.evidence_bead_version_id
                   ) AS authorized
            FROM memoriesql.topic_identity_events AS event_record
            WHERE event_record.tenant_id = requested_tenant_id
              AND event_record.topic_id = resolution.topic_id
              AND event_record.event_kind IN ('merged', 'redirected')
              AND event_record.effective_at <= requested_at
            ORDER BY event_record.effective_at DESC,
                     event_record.event_sequence DESC
            LIMIT 1
        ) AS next_event ON resolution.authorized
        WHERE next_event.target_topic_id <> ALL(resolution.path)
    )
    SELECT CASE WHEN authorized THEN topic_id END
    FROM resolution
    ORDER BY cardinality(path) DESC
    LIMIT 1
$$;

REVOKE ALL ON FUNCTION memoriesql.assert_entity_resolution_ready() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_entity_candidate_ready() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_topic_resolution_ready() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_topic_candidate_ready() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_entity_redirect_acyclic() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_topic_redirect_acyclic() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.serialize_semantic_identity_redirects()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.serialize_bead_type_revision_writes()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_latest_authorable_bead_type_revision()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.serialize_entity_type_revision_writes()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_latest_active_entity_type_revision()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.enforce_model_resource_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_next_external_reference_event()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_entity_redirect_history()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_topic_redirect_history()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_entity_mention_resolutions()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_topic_mention_resolutions()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_entity_revisions()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_topic_revisions()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_entity_external_references()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.resolve_entity_as_of(
    uuid, uuid, timestamp with time zone
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.resolve_topic_as_of(
    uuid, uuid, timestamp with time zone
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_model_authorized(
    uuid, uuid, uuid, uuid
)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_bead_version_authorized(
    uuid, uuid, uuid, uuid
)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_entity_authorized(
    uuid, uuid, uuid, uuid
)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_topic_authorized(
    uuid, uuid, uuid, uuid
)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_topic_revision_authorized(
    uuid, uuid, uuid, uuid, uuid
)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_entity_candidates_authorized(
    uuid, uuid, uuid, uuid
)
FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.current_context_topic_candidates_authorized(
    uuid, uuid, uuid, uuid
)
FROM PUBLIC;

CREATE POLICY bead_versions_read ON memoriesql.bead_versions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(
    access_scope_id, event_id, 'source.read', 'read'
));
CREATE POLICY entity_mentions_read ON memoriesql.entity_mentions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(
    access_scope_id, event_id, 'source.read', 'read'
));
CREATE POLICY entities_read ON memoriesql.entities
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_entity_authorized(
    tenant_id, workspace_id, access_scope_id, entity_id
));
CREATE POLICY entity_revisions_read ON memoriesql.entity_revisions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_entity_authorized(
        tenant_id, workspace_id, access_scope_id, entity_id
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY entity_aliases_read ON memoriesql.entity_aliases
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_entity_authorized(
        tenant_id, workspace_id, access_scope_id, entity_id
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY entity_external_references_read
ON memoriesql.entity_external_references
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_entity_authorized(
        tenant_id, workspace_id, access_scope_id, entity_id
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY entity_external_reference_events_read
ON memoriesql.entity_external_reference_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
    AND (
        target_entity_id IS NULL
        OR memoriesql.current_context_entity_authorized(
            tenant_id, workspace_id, access_scope_id, target_entity_id
        )
    )
);
CREATE POLICY entity_mention_resolutions_read
ON memoriesql.entity_mention_resolutions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.entity_mentions AS mention
        WHERE mention.tenant_id = entity_mention_resolutions.tenant_id
          AND mention.entity_mention_id =
              entity_mention_resolutions.entity_mention_id
          AND memoriesql.current_context_event_authorized(
              mention.access_scope_id, mention.event_id, 'source.read', 'read'
          )
    )
    AND (
        resolved_entity_id IS NULL
        OR memoriesql.current_context_entity_authorized(
            tenant_id, workspace_id, access_scope_id, resolved_entity_id
        )
    )
    AND (
        resolution_status <> 'ambiguous'
        OR memoriesql.current_context_entity_candidates_authorized(
            tenant_id, workspace_id, access_scope_id,
            entity_mention_resolution_id
        )
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY entity_resolution_candidates_read
ON memoriesql.entity_resolution_candidates
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_entity_authorized(
        tenant_id, workspace_id, access_scope_id, candidate_entity_id
    )
    AND EXISTS (
        SELECT 1
        FROM memoriesql.entity_mention_resolutions AS resolution_record
        WHERE resolution_record.tenant_id =
                  entity_resolution_candidates.tenant_id
          AND resolution_record.entity_mention_resolution_id =
                  entity_resolution_candidates.entity_mention_resolution_id
    )
);
CREATE POLICY entity_identity_events_read
ON memoriesql.entity_identity_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_entity_authorized(
        tenant_id, workspace_id, access_scope_id, entity_id
    )
    AND (
        target_entity_id IS NULL
        OR memoriesql.current_context_entity_authorized(
            tenant_id, workspace_id, access_scope_id, target_entity_id
        )
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY topic_mentions_read ON memoriesql.topic_mentions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(
    access_scope_id, event_id, 'source.read', 'read'
));
CREATE POLICY topics_read ON memoriesql.topics
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_topic_authorized(
    tenant_id, workspace_id, access_scope_id, topic_id
));
CREATE POLICY topic_revisions_read ON memoriesql.topic_revisions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_topic_authorized(
        tenant_id, workspace_id, access_scope_id, topic_id
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY topic_mention_resolutions_read
ON memoriesql.topic_mention_resolutions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.topic_mentions AS mention
        WHERE mention.tenant_id = topic_mention_resolutions.tenant_id
          AND mention.topic_mention_id =
              topic_mention_resolutions.topic_mention_id
          AND memoriesql.current_context_event_authorized(
              mention.access_scope_id, mention.event_id, 'source.read', 'read'
          )
    )
    AND (
        resolved_topic_id IS NULL
        OR memoriesql.current_context_topic_authorized(
            tenant_id, workspace_id, access_scope_id, resolved_topic_id
        )
    )
    AND (
        resolution_status <> 'ambiguous'
        OR memoriesql.current_context_topic_candidates_authorized(
            tenant_id, workspace_id, access_scope_id,
            topic_mention_resolution_id
        )
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);
CREATE POLICY topic_resolution_candidates_read
ON memoriesql.topic_resolution_candidates
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_topic_authorized(
        tenant_id, workspace_id, access_scope_id, candidate_topic_id
    )
    AND EXISTS (
        SELECT 1
        FROM memoriesql.topic_mention_resolutions AS resolution_record
        WHERE resolution_record.tenant_id =
                  topic_resolution_candidates.tenant_id
          AND resolution_record.topic_mention_resolution_id =
                  topic_resolution_candidates.topic_mention_resolution_id
    )
);
CREATE POLICY bead_topic_links_read ON memoriesql.bead_topic_links
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, bead_version_id
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
    AND memoriesql.current_context_topic_authorized(
        tenant_id, workspace_id, access_scope_id, topic_id
    )
    AND memoriesql.current_context_topic_revision_authorized(
        tenant_id, workspace_id, access_scope_id,
        topic_id, topic_revision_id
    )
);
CREATE POLICY topic_identity_events_read
ON memoriesql.topic_identity_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_topic_authorized(
        tenant_id, workspace_id, access_scope_id, topic_id
    )
    AND (
        target_topic_id IS NULL
        OR memoriesql.current_context_topic_authorized(
            tenant_id, workspace_id, access_scope_id, target_topic_id
        )
    )
    AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, evidence_bead_version_id
    )
);

ALTER TABLE memoriesql.bead_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_mentions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_mentions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entities FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_aliases FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_external_references ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_external_references FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_external_reference_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_external_reference_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_mention_resolutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_mention_resolutions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_resolution_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_resolution_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_identity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.entity_identity_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_mentions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_mentions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topics FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_mention_resolutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_mention_resolutions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_resolution_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_resolution_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_topic_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_topic_links FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_identity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.topic_identity_events FORCE ROW LEVEL SECURITY;

CREATE TRIGGER bead_types_immutable BEFORE UPDATE OR DELETE
ON memoriesql.bead_types FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_type_revisions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.bead_type_revisions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_types_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_types FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_type_revisions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_type_revisions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_versions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.bead_versions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_mentions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_mentions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entities_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entities FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_revisions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_revisions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_aliases_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_aliases FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_external_references_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_external_references FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_external_reference_events_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_external_reference_events FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_mention_resolutions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_mention_resolutions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_resolution_candidates_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_resolution_candidates FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER entity_identity_events_immutable BEFORE UPDATE OR DELETE
ON memoriesql.entity_identity_events FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER topic_mentions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.topic_mentions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER topics_immutable BEFORE UPDATE OR DELETE
ON memoriesql.topics FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER topic_revisions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.topic_revisions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER topic_mention_resolutions_immutable BEFORE UPDATE OR DELETE
ON memoriesql.topic_mention_resolutions FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER topic_resolution_candidates_immutable BEFORE UPDATE OR DELETE
ON memoriesql.topic_resolution_candidates FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_topic_links_immutable BEFORE UPDATE OR DELETE
ON memoriesql.bead_topic_links FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER topic_identity_events_immutable BEFORE UPDATE OR DELETE
ON memoriesql.topic_identity_events FOR EACH ROW
EXECUTE FUNCTION memoriesql.reject_immutable_change();

GRANT SELECT ON memoriesql.bead_types,
    memoriesql.bead_type_revisions,
    memoriesql.current_bead_type_revisions,
    memoriesql.entity_types,
    memoriesql.entity_type_revisions,
    memoriesql.current_entity_type_revisions
TO memoriesql_application, memoriesql_worker;

GRANT SELECT ON memoriesql.bead_versions,
    memoriesql.current_bead_versions,
    memoriesql.entity_mentions,
    memoriesql.entities,
    memoriesql.entity_revisions,
    memoriesql.current_entity_revisions,
    memoriesql.entity_aliases,
    memoriesql.entity_external_references,
    memoriesql.entity_external_reference_events,
    memoriesql.current_entity_external_references,
    memoriesql.entity_mention_resolutions,
    memoriesql.current_entity_mention_resolutions,
    memoriesql.entity_resolution_candidates,
    memoriesql.entity_identity_events,
    memoriesql.entity_redirects_as_of,
    memoriesql.current_entity_redirects,
    memoriesql.topic_mentions,
    memoriesql.topics,
    memoriesql.topic_revisions,
    memoriesql.current_topic_revisions,
    memoriesql.topic_mention_resolutions,
    memoriesql.current_topic_mention_resolutions,
    memoriesql.topic_resolution_candidates,
    memoriesql.bead_topic_links,
    memoriesql.topic_identity_events,
    memoriesql.topic_redirects_as_of,
    memoriesql.current_topic_redirects
TO memoriesql_application, memoriesql_worker;

GRANT EXECUTE ON FUNCTION memoriesql.resolve_entity_as_of(
    uuid, uuid, timestamp with time zone
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.resolve_topic_as_of(
    uuid, uuid, timestamp with time zone
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_entity_redirect_history()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_topic_redirect_history()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_entity_mention_resolutions()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_topic_mention_resolutions()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_entity_revisions()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_topic_revisions()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_entity_external_references()
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_model_authorized(
    uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_bead_version_authorized(
    uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_entity_authorized(
    uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_topic_authorized(
    uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_topic_revision_authorized(
    uuid, uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_entity_candidates_authorized(
    uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_topic_candidates_authorized(
    uuid, uuid, uuid, uuid
)
TO memoriesql_application, memoriesql_worker;

COMMENT ON TABLE memoriesql.bead_types IS
    'Stable centrally allocated governed bead-type IDs; labels never generate identity.';
COMMENT ON TABLE memoriesql.bead_type_revisions IS
    'Append-only definitions; authored bead versions pin one exact revision.';
COMMENT ON TABLE memoriesql.bead_versions IS
    'Append-only governed type classification over a stable thin bead; authored statement and render storage remains separately gated.';
COMMENT ON TABLE memoriesql.entities IS
    'Stable tenant entity roots protected as model resources; labels live only in revisions.';
COMMENT ON TABLE memoriesql.entity_mention_resolutions IS
    'Append-only unresolved, resolved, ambiguous, or rejected mention decisions.';
COMMENT ON TABLE memoriesql.entity_identity_events IS
    'Append-only lifecycle and redirect history; prior entity references are never rewritten.';
COMMENT ON TABLE memoriesql.topics IS
    'Stable tenant topic roots with no built-in product-specific seed or hierarchy.';
COMMENT ON VIEW memoriesql.entity_redirects_as_of IS
    'Identity redirect intervals for ID-first historical reads without rewriting references.';
