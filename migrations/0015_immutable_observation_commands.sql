-- Public-authored forward transition. SQL 0001-0014 and their payloads are unchanged.
-- Source-occurrence identity is separate from subsequent semantic authorship.
ALTER TABLE memoriesql.beads
    ADD COLUMN origin_kind text NOT NULL DEFAULT 'initial'
        CHECK (origin_kind IN ('initial', 'correction'));
ALTER TABLE memoriesql.beads DROP CONSTRAINT beads_one_per_source_unit_uq;
CREATE UNIQUE INDEX beads_one_initial_per_source_unit_uq
ON memoriesql.beads (tenant_id, source_unit_id) WHERE origin_kind = 'initial';

-- A seal points at the accepted historical version. Upgrade preserves all older
-- versions and their receipts; it does not reinterpret them as correction beads.
ALTER TABLE memoriesql.bead_versions ADD CONSTRAINT bead_versions_seal_identity_uq
    UNIQUE (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id);
CREATE TABLE memoriesql.accepted_bead_semantics (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, bead_id),
    UNIQUE (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        REFERENCES memoriesql.bead_versions
            (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
);
INSERT INTO memoriesql.accepted_bead_semantics
SELECT DISTINCT ON (tenant_id, bead_id)
    tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id
FROM memoriesql.bead_versions ORDER BY tenant_id, bead_id, version DESC;

CREATE TABLE memoriesql.bead_supersessions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    bead_version_id uuid NOT NULL,
    superseded_bead_id uuid NOT NULL,
    superseded_bead_version_id uuid NOT NULL,
    correction_reason text NOT NULL CHECK (
        btrim(correction_reason) <> '' AND char_length(correction_reason) <= 1024
    ),
    PRIMARY KEY (tenant_id, bead_id, superseded_bead_id),
    CHECK (bead_id <> superseded_bead_id),
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics
            (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (tenant_id, workspace_id, access_scope_id,
                 superseded_bead_id, superseded_bead_version_id)
        REFERENCES memoriesql.accepted_bead_semantics
            (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
);

CREATE FUNCTION memoriesql.guard_accepted_bead_semantics()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
DECLARE
    target_bead_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'bead_semantic_statement_evidence' THEN
        SELECT statement.bead_id INTO STRICT target_bead_id
        FROM memoriesql.bead_semantic_statements AS statement
        WHERE statement.tenant_id = NEW.tenant_id
          AND statement.statement_id = NEW.statement_id;
    ELSE
        target_bead_id := NEW.bead_id;
    END IF;
    IF TG_TABLE_NAME = 'bead_supersessions' AND NOT EXISTS (
        SELECT 1 FROM memoriesql.beads AS bead
        WHERE bead.tenant_id = NEW.tenant_id AND bead.bead_id = target_bead_id
          AND bead.origin_kind = 'correction'
    ) THEN
        RAISE EXCEPTION 'supersession requires a distinct correction bead'
            USING ERRCODE = '23514';
    END IF;
    -- Serialize first authorship, including trusted direct SQL callers. The
    -- existing tenant outcome authority lock is still held by canonical commands.
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        NEW.tenant_id::text || ':bead-semantic:' || target_bead_id::text, 0
    ));
    IF EXISTS (
        SELECT 1 FROM memoriesql.accepted_bead_semantics AS accepted
        WHERE accepted.tenant_id = NEW.tenant_id AND accepted.bead_id = target_bead_id
    ) OR (TG_TABLE_NAME = 'bead_versions' AND EXISTS (
        SELECT 1 FROM memoriesql.bead_versions AS version
        WHERE version.tenant_id = NEW.tenant_id AND version.bead_id = target_bead_id
    )) THEN
        RAISE EXCEPTION 'accepted_bead_immutable: use a distinct correction bead'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER accepted_version_guard BEFORE INSERT ON memoriesql.bead_versions
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_accepted_bead_semantics();
CREATE TRIGGER accepted_statement_guard BEFORE INSERT ON memoriesql.bead_semantic_statements
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_accepted_bead_semantics();
CREATE TRIGGER accepted_evidence_guard BEFORE INSERT ON memoriesql.bead_semantic_statement_evidence
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_accepted_bead_semantics();
CREATE TRIGGER accepted_revision_guard BEFORE INSERT ON memoriesql.bead_statement_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_accepted_bead_semantics();
CREATE TRIGGER accepted_lineage_guard BEFORE INSERT ON memoriesql.bead_supersessions
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_accepted_bead_semantics();

CREATE FUNCTION memoriesql.seal_bead_semantics()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
BEGIN
    INSERT INTO memoriesql.accepted_bead_semantics
        (tenant_id, workspace_id, access_scope_id, bead_id, bead_version_id)
    VALUES (NEW.tenant_id, NEW.workspace_id, NEW.access_scope_id,
            NEW.bead_id, NEW.bead_version_id);
    RETURN NULL;
END;
$$;
-- Deferred so the same atomic initial write can add statements and evidence.
CREATE CONSTRAINT TRIGGER seal_bead_semantics
AFTER INSERT ON memoriesql.bead_versions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.seal_bead_semantics();
CREATE TRIGGER accepted_bead_semantics_immutable
BEFORE UPDATE OR DELETE ON memoriesql.accepted_bead_semantics
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER bead_supersessions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.bead_supersessions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

ALTER TABLE memoriesql.accepted_bead_semantics ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.accepted_bead_semantics FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_supersessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.bead_supersessions FORCE ROW LEVEL SECURITY;
CREATE POLICY accepted_bead_semantics_read ON memoriesql.accepted_bead_semantics
FOR SELECT TO memoriesql_application, memoriesql_worker USING (
    memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, bead_version_id
    )
);
CREATE POLICY bead_supersessions_read ON memoriesql.bead_supersessions
FOR SELECT TO memoriesql_application, memoriesql_worker USING (
    memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, bead_version_id
    ) AND memoriesql.current_context_bead_version_authorized(
        tenant_id, workspace_id, access_scope_id, superseded_bead_version_id
    )
);
REVOKE ALL ON memoriesql.accepted_bead_semantics, memoriesql.bead_supersessions FROM PUBLIC;
GRANT SELECT ON memoriesql.accepted_bead_semantics, memoriesql.bead_supersessions
TO memoriesql_application, memoriesql_worker;
REVOKE ALL ON FUNCTION memoriesql.guard_accepted_bead_semantics(),
    memoriesql.seal_bead_semantics() FROM PUBLIC;
COMMENT ON TABLE memoriesql.bead_supersessions IS
    'Immutable explicit correction lineage, with pinned accepted targets. Branches and multi-target reconciliation are valid; no global winner is inferred.';

-- No thin correction placeholder may escape the atomic authorship transaction.
CREATE FUNCTION memoriesql.assert_correction_bead_ready()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM memoriesql.bead_versions AS version
        WHERE version.tenant_id=NEW.tenant_id AND version.bead_id=NEW.bead_id
    ) OR (SELECT count(*) FROM memoriesql.bead_supersessions AS edge
          WHERE edge.tenant_id=NEW.tenant_id AND edge.bead_id=NEW.bead_id) NOT BETWEEN 1 AND 8 THEN
        RAISE EXCEPTION 'correction bead requires accepted semantics and explicit targets'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER correction_bead_ready
AFTER INSERT ON memoriesql.beads DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW WHEN (NEW.origin_kind = 'correction')
EXECUTE FUNCTION memoriesql.assert_correction_bead_ready();
REVOKE ALL ON FUNCTION memoriesql.assert_correction_bead_ready() FROM PUBLIC;


-- Worker correction authority uses its existing memory.maintain capability,
-- never the user-query capability or wider database role grants. Like historical
-- reads, it checks every statement through the pinned version's watermark and
-- every supporting evidence event, including superseded historical statements.
CREATE FUNCTION memoriesql.current_context_accepted_bead_maintain_authorized(
    requested_tenant_id uuid, requested_workspace_id uuid,
    requested_access_scope_id uuid, requested_bead_version_id uuid
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
          AND memoriesql.current_context_event_authorized(
              version.access_scope_id, version.event_id, 'memory.maintain', 'read'
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
                    NOT memoriesql.current_context_event_authorized(
                        statement.access_scope_id, statement.event_id, 'memory.maintain', 'read'
                    ) OR NOT EXISTS (
                        SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS evidence
                        WHERE evidence.tenant_id = statement.tenant_id
                          AND evidence.statement_id = statement.statement_id
                    ) OR EXISTS (
                        SELECT 1 FROM memoriesql.bead_semantic_statement_evidence AS evidence
                        WHERE evidence.tenant_id = statement.tenant_id
                          AND evidence.statement_id = statement.statement_id
                          AND NOT memoriesql.current_context_event_authorized(
                              evidence.access_scope_id, evidence.evidence_event_id, 'memory.maintain', 'read'
                          )
                    )
                )
          )
    );
$$;
REVOKE ALL ON FUNCTION memoriesql.current_context_accepted_bead_maintain_authorized(uuid,uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_accepted_bead_maintain_authorized(uuid,uuid,uuid,uuid) TO memoriesql_worker;


CREATE OR REPLACE FUNCTION memoriesql.assert_observation_unit_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_source_type text;
    expected_observation_count integer;
    actual_observation_count integer;
BEGIN
    IF NOT NEW.is_observation THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM memoriesql.beads AS bead
        WHERE bead.tenant_id = NEW.tenant_id
          AND bead.source_unit_id = NEW.source_unit_id
          AND bead.origin_kind = 'initial'
    ) THEN
        RAISE EXCEPTION 'observation unit % requires exactly one bead', NEW.source_unit_id
            USING ERRCODE = '23514';
    END IF;

    SELECT event_record.source_type, event_record.observation_unit_count
      INTO STRICT event_source_type, expected_observation_count
      FROM memoriesql.source_events AS event_record
     WHERE event_record.tenant_id = NEW.tenant_id
       AND event_record.event_id = NEW.event_id;

    SELECT count(*)::integer
      INTO actual_observation_count
      FROM memoriesql.source_units AS unit_record
     WHERE unit_record.tenant_id = NEW.tenant_id
       AND unit_record.event_id = NEW.event_id
       AND unit_record.is_observation;

    IF actual_observation_count <> expected_observation_count THEN
        RAISE EXCEPTION 'event % observation unit count is sealed at %',
            NEW.event_id,
            expected_observation_count
            USING ERRCODE = '23514';
    END IF;

    IF event_source_type = 'transcript' THEN
        IF NEW.unit_kind <> 'turn' OR NOT EXISTS (
            SELECT 1 FROM memoriesql.conversation_turns AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'transcript observation unit % requires turn detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSIF event_source_type = 'document' THEN
        IF NEW.unit_kind NOT IN ('section', 'chunk') OR NOT EXISTS (
            SELECT 1 FROM memoriesql.document_segments AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'document observation unit % requires segment detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSIF event_source_type = 'media' THEN
        IF NEW.unit_kind <> 'media_window' OR NOT EXISTS (
            SELECT 1 FROM memoriesql.media_segments AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'media observation unit % requires media-window detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSIF event_source_type IN ('relational', 'operational') THEN
        IF NEW.unit_kind <> 'record_change' OR NOT EXISTS (
            SELECT 1 FROM memoriesql.record_events AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'record observation unit % requires record-event detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'source type % has no PR-01 typed detail contract', event_source_type
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE VIEW memoriesql.source_event_cardinality
WITH (security_invoker = true) AS
SELECT
    event_record.tenant_id,
    event_record.workspace_id,
    event_record.access_scope_id,
    event_record.event_id,
    event_record.observation_unit_count AS declared_observation_unit_count,
    count(unit_record.source_unit_id) AS source_unit_count,
    count(unit_record.source_unit_id) FILTER (
        WHERE unit_record.is_observation
    ) AS observation_unit_count,
    count(bead.bead_id) AS bead_count
FROM memoriesql.source_events AS event_record
LEFT JOIN memoriesql.source_units AS unit_record
  ON unit_record.tenant_id = event_record.tenant_id
 AND unit_record.event_id = event_record.event_id
LEFT JOIN memoriesql.beads AS bead
  ON bead.tenant_id = unit_record.tenant_id
 AND bead.source_unit_id = unit_record.source_unit_id
 AND bead.origin_kind = 'initial'
GROUP BY
    event_record.tenant_id,
    event_record.workspace_id,
    event_record.access_scope_id,
    event_record.event_id,
    event_record.observation_unit_count;


INSERT INTO memoriesql.semantic_task_admission_policies (
    semantic_registry_hash, task_kind, contract_revision, owning_module,
    task_contract_hash, target_kind, required_capability, queue_name,
    base_priority, max_attempts, concurrency_key, concurrency_limit
) VALUES (
    'semantic-tasks-v1:4679b92d3ab4c8247c085b4b89fafa4d5a414fb15df2853479ef50160c744057',
    'memory.semantic.author-observations', 2, 'memoriesql.kernel',
    '204bcfc528df06495cb504b7e427ca80964cb9e4f69d049eba4ee0d7a1fcc92d',
    'canonical_semantics', 'memory.capture', 'capture', 50, 3,
    'canonical-observation-authoring', 8
);

INSERT INTO memoriesql.semantic_task_admission_policies (
    semantic_registry_hash, task_kind, contract_revision, owning_module,
    task_contract_hash, target_kind, required_capability, queue_name,
    base_priority, max_attempts, concurrency_key, concurrency_limit
) VALUES (
    'semantic-tasks-v1:4679b92d3ab4c8247c085b4b89fafa4d5a414fb15df2853479ef50160c744057',
    'memory.semantic.correct-observation', 2, 'memoriesql.kernel',
    '44977ac3682b5d71d82329b2e33a18debd6ff613042d3f3941e5a378e1398858',
    'canonical_semantics', 'memory.maintain', 'capture', 50, 3,
    'canonical-observation-authoring', 8
);

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

CREATE OR REPLACE FUNCTION memoriesql.accept_source_event(
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
    authoring_revision integer;
BEGIN
    IF jsonb_typeof(requested_command) IS DISTINCT FROM 'object'
       OR pg_catalog.octet_length(requested_command::text) > 4194304
       OR NOT (
           (requested_command ->> 'contract_version' IS NOT DISTINCT FROM '1'
            AND requested_command ->> 'expected_schema_version' IS NOT DISTINCT FROM '11')
           OR (requested_command ->> 'contract_version' IS NOT DISTINCT FROM '2'
            AND requested_command ->> 'expected_schema_version' IS NOT DISTINCT FROM '15')
       )
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

    authoring_revision := (requested_command ->> 'contract_version')::integer;
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
           AND bead.origin_kind = 'initial'
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
           AND bead.origin_kind = 'initial'
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
            'memory.semantic.author-observations', authoring_revision,
            CASE authoring_revision WHEN 1 THEN
                'df6b7094bdc407ea10464a1b9ff9020cbbc99d57160a3b86d7702d57db89f92e'
            ELSE '204bcfc528df06495cb504b7e427ca80964cb9e4f69d049eba4ee0d7a1fcc92d' END,
            CASE authoring_revision WHEN 1 THEN
                'semantic-tasks-v1:ec2c9544c3eab504c9276e3f600ad656cbcc606dfa64c9f08aebdb6fb2e01bd4'
            ELSE 'semantic-tasks-v1:4679b92d3ab4c8247c085b4b89fafa4d5a414fb15df2853479ef50160c744057' END,
            command_event_id::text,
            (requested_command ->> 'expected_source_object_schema_version')::integer,
            jsonb_build_object(
                'task_id', command_task_id::text,
                'task_kind', 'memory.semantic.author-observations',
                'contract_revision', authoring_revision,
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
    is_correction boolean;
    operation_key text;
    target_pin jsonb;
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
    is_v2 := requested_command ->> 'contract_version' IS NOT DISTINCT FROM '2';
    is_correction := is_v2 AND requested_command ->> 'task_kind' IS NOT DISTINCT FROM
        'memory.semantic.correct-observation';
    operation_key := CASE WHEN is_correction THEN 'observation_correction.apply.v2'
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
        CASE WHEN is_v2 THEN operation_key ELSE 'semantic_annotations.applied' END,
        jsonb_build_object(
            'task_id', command_task_id,
            'attempt_id', command_attempt_id,
            'statement_count', cardinality(returned_statement_ids),
            'bead_version_count', cardinality(returned_bead_version_ids)
        ),
        jsonb_build_object('contract_version', CASE WHEN is_v2 THEN 2 ELSE 1 END), database_now, database_now
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

CREATE FUNCTION memoriesql.author_initial_observations_v2(
    requested_command jsonb, requested_worker_id text,
    requested_worker_instance_id text, requested_at timestamp with time zone
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
DECLARE result record; response jsonb;
BEGIN
    IF requested_command ->> 'contract_version' IS DISTINCT FROM '2'
       OR requested_command ->> 'task_kind' IS DISTINCT FROM 'memory.semantic.author-observations' THEN
        RAISE EXCEPTION 'explicit v2 observation command required' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT result FROM memoriesql.apply_semantic_annotations(
        requested_command, requested_worker_id, requested_worker_instance_id, requested_at
    );
    SELECT receipt.response_receipt INTO STRICT response
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = (requested_command ->> 'tenant_id')::uuid
      AND receipt.idempotency_receipt_id = result.idempotency_receipt_id;
    RETURN response || jsonb_build_object(
        'idempotency_receipt_id', result.idempotency_receipt_id, 'replayed', result.replayed
    );
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.author_initial_observations_v2(jsonb, text, text, timestamp with time zone) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.author_initial_observations_v2(jsonb, text, text, timestamp with time zone) TO memoriesql_worker;

CREATE FUNCTION memoriesql.correct_observation_v2(
    requested_command jsonb, requested_worker_id text,
    requested_worker_instance_id text, requested_at timestamp with time zone
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql AS $$
DECLARE result record; response jsonb;
BEGIN
    IF requested_command ->> 'contract_version' IS DISTINCT FROM '2'
       OR requested_command ->> 'task_kind' IS DISTINCT FROM 'memory.semantic.correct-observation' THEN
        RAISE EXCEPTION 'explicit v2 observation command required' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT result FROM memoriesql.apply_semantic_annotations(
        requested_command, requested_worker_id, requested_worker_instance_id, requested_at
    );
    SELECT receipt.response_receipt INTO STRICT response
    FROM memoriesql.idempotency_receipts AS receipt
    WHERE receipt.tenant_id = (requested_command ->> 'tenant_id')::uuid
      AND receipt.idempotency_receipt_id = result.idempotency_receipt_id;
    RETURN response || jsonb_build_object(
        'idempotency_receipt_id', result.idempotency_receipt_id, 'replayed', result.replayed
    );
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.correct_observation_v2(jsonb, text, text, timestamp with time zone) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.correct_observation_v2(jsonb, text, text, timestamp with time zone) TO memoriesql_worker;
