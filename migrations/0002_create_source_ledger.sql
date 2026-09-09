CREATE TABLE memoriesql.source_objects (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    source_system text NOT NULL,
    installation_id text,
    installation_key text GENERATED ALWAYS AS (COALESCE(installation_id, '')) STORED,
    object_kind text NOT NULL,
    external_object_id text NOT NULL,
    locator text,
    current_content_hash text,
    schema_version integer NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    last_observed_at timestamp with time zone NOT NULL,
    CONSTRAINT source_objects_pk PRIMARY KEY (tenant_id, source_object_id),
    CONSTRAINT source_objects_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        source_object_id
    ),
    CONSTRAINT source_objects_scope_origin_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        source_object_id,
        source_system,
        installation_key
    ),
    CONSTRAINT source_objects_source_system_nonempty
        CHECK (btrim(source_system) <> ''),
    CONSTRAINT source_objects_installation_nonempty
        CHECK (installation_id IS NULL OR btrim(installation_id) <> ''),
    CONSTRAINT source_objects_object_kind_nonempty CHECK (btrim(object_kind) <> ''),
    CONSTRAINT source_objects_external_id_nonempty
        CHECK (btrim(external_object_id) <> ''),
    CONSTRAINT source_objects_schema_version_positive CHECK (schema_version > 0),
    CONSTRAINT source_objects_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT source_objects_observed_after_created
        CHECK (last_observed_at >= created_at)
);

CREATE UNIQUE INDEX source_objects_natural_identity_uq
ON memoriesql.source_objects (
    tenant_id,
    source_system,
    installation_key,
    object_kind,
    external_object_id
);

CREATE FUNCTION memoriesql.protect_source_object_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        NEW.tenant_id,
        NEW.source_object_id,
        NEW.source_system,
        NEW.installation_id,
        NEW.object_kind,
        NEW.external_object_id,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id,
        OLD.source_object_id,
        OLD.source_system,
        OLD.installation_id,
        OLD.object_kind,
        OLD.external_object_id,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'memoriesql.source_objects stable identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER source_objects_identity_immutable
BEFORE UPDATE ON memoriesql.source_objects
FOR EACH ROW EXECUTE FUNCTION memoriesql.protect_source_object_identity();

CREATE TABLE memoriesql.source_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    source_type text NOT NULL,
    source_system text NOT NULL,
    installation_id text,
    installation_key text GENERATED ALWAYS AS (COALESCE(installation_id, '')) STORED,
    external_event_id text,
    external_id_scope text NOT NULL,
    source_identity_key text NOT NULL,
    session_id text,
    actor_id text NOT NULL,
    actor_kind text NOT NULL,
    source_occurred_at timestamp with time zone,
    source_occurred_end_at timestamp with time zone,
    source_occurred_at_raw text,
    source_timezone text,
    source_time_precision text,
    source_sequence bigint,
    source_revision_key text,
    parser_contract_version text NOT NULL,
    observation_unit_policy_version text NOT NULL,
    observation_unit_count integer NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone NOT NULL DEFAULT transaction_timestamp(),
    source_ref text NOT NULL,
    content_hash text NOT NULL,
    metadata jsonb NOT NULL,
    CONSTRAINT source_events_pk PRIMARY KEY (tenant_id, event_id),
    CONSTRAINT source_events_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id
    ),
    CONSTRAINT source_events_scope_type_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ),
    CONSTRAINT source_events_scope_hash_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        content_hash
    ),
    CONSTRAINT source_events_object_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        source_object_id,
        source_system,
        installation_key
    ) REFERENCES memoriesql.source_objects (
        tenant_id,
        workspace_id,
        access_scope_id,
        source_object_id,
        source_system,
        installation_key
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT source_events_source_type_supported CHECK (
        source_type IN ('transcript', 'document', 'media', 'relational', 'operational')
    ),
    CONSTRAINT source_events_source_system_nonempty
        CHECK (btrim(source_system) <> ''),
    CONSTRAINT source_events_installation_nonempty
        CHECK (installation_id IS NULL OR btrim(installation_id) <> ''),
    CONSTRAINT source_events_external_scope_nonempty
        CHECK (btrim(external_id_scope) <> ''),
    CONSTRAINT source_events_identity_key_nonempty
        CHECK (btrim(source_identity_key) <> ''),
    CONSTRAINT source_events_actor_id_nonempty CHECK (btrim(actor_id) <> ''),
    CONSTRAINT source_events_actor_kind_nonempty CHECK (btrim(actor_kind) <> ''),
    CONSTRAINT source_events_parser_version_nonempty
        CHECK (btrim(parser_contract_version) <> ''),
    CONSTRAINT source_events_policy_version_nonempty
        CHECK (btrim(observation_unit_policy_version) <> ''),
    CONSTRAINT source_events_observation_count_positive
        CHECK (observation_unit_count > 0),
    CONSTRAINT source_events_source_ref_nonempty CHECK (btrim(source_ref) <> ''),
    CONSTRAINT source_events_content_hash_nonempty CHECK (btrim(content_hash) <> ''),
    CONSTRAINT source_events_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT source_events_occurrence_interval
        CHECK (
            source_occurred_end_at IS NULL
            OR source_occurred_at IS NULL
            OR source_occurred_end_at >= source_occurred_at
        ),
    CONSTRAINT source_events_source_sequence_nonnegative
        CHECK (source_sequence IS NULL OR source_sequence >= 0),
    CONSTRAINT source_events_time_precision_supported CHECK (
        source_time_precision IS NULL
        OR source_time_precision IN (
            'instant',
            'second',
            'minute',
            'hour',
            'day',
            'month',
            'year',
            'interval',
            'unknown'
        )
    )
);

CREATE UNIQUE INDEX source_events_natural_identity_uq
ON memoriesql.source_events (
    tenant_id,
    source_system,
    external_id_scope,
    source_identity_key
);

CREATE UNIQUE INDEX source_events_revision_uq
ON memoriesql.source_events (tenant_id, source_object_id, source_revision_key)
WHERE source_revision_key IS NOT NULL;

CREATE UNIQUE INDEX source_events_sequence_uq
ON memoriesql.source_events (tenant_id, source_object_id, source_sequence)
WHERE source_sequence IS NOT NULL;

CREATE TABLE memoriesql.source_units (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    event_id uuid NOT NULL,
    parent_unit_id uuid,
    unit_kind text NOT NULL,
    is_observation boolean NOT NULL,
    external_unit_id text NOT NULL,
    unit_ordinal bigint NOT NULL,
    unit_source_occurred_at timestamp with time zone,
    unit_source_occurred_end_at timestamp with time zone,
    unit_time_precision text,
    time_start_seconds numeric,
    time_end_seconds numeric,
    content_text text,
    content_hash text NOT NULL,
    structure jsonb NOT NULL,
    hydration_ref text,
    schema_version integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT source_units_pk PRIMARY KEY (tenant_id, source_unit_id),
    CONSTRAINT source_units_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        source_unit_id
    ),
    CONSTRAINT source_units_scope_event_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id
    ),
    CONSTRAINT source_units_scope_event_kind_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ),
    CONSTRAINT source_units_scope_event_observation_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        is_observation
    ),
    CONSTRAINT source_units_event_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT source_units_parent_same_event_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        parent_unit_id
    ) REFERENCES memoriesql.source_units (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT source_units_kind_supported CHECK (
        unit_kind IN (
            'turn',
            'section',
            'chunk',
            'element',
            'media_window',
            'record_change'
        )
    ),
    CONSTRAINT source_units_not_self_parent
        CHECK (parent_unit_id IS DISTINCT FROM source_unit_id),
    CONSTRAINT source_units_external_id_nonempty
        CHECK (btrim(external_unit_id) <> ''),
    CONSTRAINT source_units_ordinal_nonnegative CHECK (unit_ordinal >= 0),
    CONSTRAINT source_units_content_hash_nonempty CHECK (btrim(content_hash) <> ''),
    CONSTRAINT source_units_structure_object
        CHECK (jsonb_typeof(structure) = 'object'),
    CONSTRAINT source_units_schema_version_positive CHECK (schema_version > 0),
    CONSTRAINT source_units_occurrence_interval CHECK (
        unit_source_occurred_end_at IS NULL
        OR unit_source_occurred_at IS NULL
        OR unit_source_occurred_end_at >= unit_source_occurred_at
    ),
    CONSTRAINT source_units_media_interval CHECK (
        (time_start_seconds IS NULL AND time_end_seconds IS NULL)
        OR (
            time_start_seconds IS NOT NULL
            AND time_start_seconds >= 0
            AND (time_end_seconds IS NULL OR time_end_seconds >= time_start_seconds)
        )
    ),
    CONSTRAINT source_units_time_precision_supported CHECK (
        unit_time_precision IS NULL
        OR unit_time_precision IN (
            'instant',
            'second',
            'minute',
            'hour',
            'day',
            'month',
            'year',
            'interval',
            'unknown'
        )
    )
);

CREATE UNIQUE INDEX source_units_external_id_uq
ON memoriesql.source_units (tenant_id, event_id, external_unit_id);

CREATE UNIQUE INDEX source_units_root_ordinal_uq
ON memoriesql.source_units (tenant_id, event_id, unit_ordinal)
WHERE parent_unit_id IS NULL;

CREATE UNIQUE INDEX source_units_child_ordinal_uq
ON memoriesql.source_units (tenant_id, event_id, parent_unit_id, unit_ordinal)
WHERE parent_unit_id IS NOT NULL;

CREATE TABLE memoriesql.conversation_turns (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    source_type text GENERATED ALWAYS AS ('transcript'::text) STORED,
    unit_kind text GENERATED ALWAYS AS ('turn'::text) STORED,
    conversation_id text NOT NULL,
    session_id text NOT NULL,
    branch_id text,
    turn_id text NOT NULL,
    participant_id text,
    participant_role text NOT NULL,
    CONSTRAINT conversation_turns_pk PRIMARY KEY (tenant_id, source_unit_id),
    CONSTRAINT conversation_turns_event_turn_uq
        UNIQUE (tenant_id, event_id, turn_id),
    CONSTRAINT conversation_turns_event_type_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT conversation_turns_unit_kind_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) REFERENCES memoriesql.source_units (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT conversation_turns_conversation_nonempty
        CHECK (btrim(conversation_id) <> ''),
    CONSTRAINT conversation_turns_session_nonempty CHECK (btrim(session_id) <> ''),
    CONSTRAINT conversation_turns_turn_nonempty CHECK (btrim(turn_id) <> ''),
    CONSTRAINT conversation_turns_participant_role_nonempty
        CHECK (btrim(participant_role) <> '')
);

CREATE TABLE memoriesql.document_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_type text GENERATED ALWAYS AS ('document'::text) STORED,
    document_version text NOT NULL,
    mime_type text NOT NULL,
    source_title text,
    content_hash text NOT NULL,
    CONSTRAINT document_revisions_pk PRIMARY KEY (tenant_id, event_id),
    CONSTRAINT document_revisions_event_type_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT document_revisions_event_hash_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        content_hash
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        content_hash
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT document_revisions_version_nonempty
        CHECK (btrim(document_version) <> ''),
    CONSTRAINT document_revisions_mime_nonempty CHECK (btrim(mime_type) <> '')
);

CREATE TABLE memoriesql.document_segments (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    source_type text GENERATED ALWAYS AS ('document'::text) STORED,
    unit_kind text NOT NULL,
    page_number integer,
    char_start bigint,
    char_end bigint,
    section_path text[],
    CONSTRAINT document_segments_pk PRIMARY KEY (tenant_id, source_unit_id),
    CONSTRAINT document_segments_event_type_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT document_segments_unit_kind_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) REFERENCES memoriesql.source_units (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT document_segments_kind_supported
        CHECK (unit_kind IN ('section', 'chunk', 'element')),
    CONSTRAINT document_segments_page_positive
        CHECK (page_number IS NULL OR page_number > 0),
    CONSTRAINT document_segments_char_interval CHECK (
        (char_start IS NULL AND char_end IS NULL)
        OR (
            char_start IS NOT NULL
            AND char_start >= 0
            AND (char_end IS NULL OR char_end >= char_start)
        )
    )
);

CREATE TABLE memoriesql.media_segments (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    source_type text GENERATED ALWAYS AS ('media'::text) STORED,
    unit_kind text NOT NULL,
    track_id text,
    speaker_ref text,
    transcript_language text,
    time_start_seconds numeric NOT NULL,
    time_end_seconds numeric NOT NULL,
    CONSTRAINT media_segments_pk PRIMARY KEY (tenant_id, source_unit_id),
    CONSTRAINT media_segments_event_type_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT media_segments_unit_kind_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) REFERENCES memoriesql.source_units (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT media_segments_kind_supported
        CHECK (unit_kind IN ('media_window', 'element')),
    CONSTRAINT media_segments_time_interval CHECK (
        time_start_seconds >= 0 AND time_end_seconds >= time_start_seconds
    )
);

CREATE TABLE memoriesql.record_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    source_type text NOT NULL,
    unit_kind text GENERATED ALWAYS AS ('record_change'::text) STORED,
    record_system text NOT NULL,
    record_object_type text NOT NULL,
    record_object_key text NOT NULL,
    record_action text NOT NULL,
    effective_at timestamp with time zone,
    changed_fields jsonb NOT NULL,
    CONSTRAINT record_events_pk PRIMARY KEY (tenant_id, source_unit_id),
    CONSTRAINT record_events_event_type_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) REFERENCES memoriesql.source_events (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_type
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT record_events_unit_kind_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) REFERENCES memoriesql.source_units (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        unit_kind
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT record_events_source_type_supported
        CHECK (source_type IN ('relational', 'operational')),
    CONSTRAINT record_events_system_nonempty CHECK (btrim(record_system) <> ''),
    CONSTRAINT record_events_object_type_nonempty
        CHECK (btrim(record_object_type) <> ''),
    CONSTRAINT record_events_object_key_nonempty
        CHECK (btrim(record_object_key) <> ''),
    CONSTRAINT record_events_action_nonempty CHECK (btrim(record_action) <> ''),
    CONSTRAINT record_events_changed_fields_object
        CHECK (jsonb_typeof(changed_fields) = 'object')
);

CREATE TABLE memoriesql.beads (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    observation_marker boolean GENERATED ALWAYS AS (true) STORED,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT beads_pk PRIMARY KEY (tenant_id, bead_id),
    CONSTRAINT beads_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        bead_id
    ),
    CONSTRAINT beads_scope_source_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        bead_id
    ),
    CONSTRAINT beads_one_per_source_unit_uq UNIQUE (tenant_id, source_unit_id),
    CONSTRAINT beads_observation_unit_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        observation_marker
    ) REFERENCES memoriesql.source_units (
        tenant_id,
        workspace_id,
        access_scope_id,
        event_id,
        source_unit_id,
        is_observation
    ) DEFERRABLE INITIALLY DEFERRED
);

CREATE FUNCTION memoriesql.assert_source_event_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    actual_observation_count integer;
BEGIN
    SELECT count(*)::integer
      INTO actual_observation_count
      FROM memoriesql.source_units AS unit_record
     WHERE unit_record.tenant_id = NEW.tenant_id
       AND unit_record.event_id = NEW.event_id
       AND unit_record.is_observation;

    IF actual_observation_count <> NEW.observation_unit_count THEN
        RAISE EXCEPTION 'source event % declared % observation units but has %',
            NEW.event_id,
            NEW.observation_unit_count,
            actual_observation_count
            USING ERRCODE = '23514';
    END IF;

    IF NEW.source_type = 'document' AND NOT EXISTS (
        SELECT 1
        FROM memoriesql.document_revisions AS revision
        WHERE revision.tenant_id = NEW.tenant_id
          AND revision.event_id = NEW.event_id
    ) THEN
        RAISE EXCEPTION 'document event % requires a document revision', NEW.event_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION memoriesql.assert_observation_unit_ready()
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

CREATE FUNCTION memoriesql.assert_source_unit_acyclic()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cycle_found boolean;
BEGIN
    IF NEW.parent_unit_id IS NULL THEN
        RETURN NULL;
    END IF;

    WITH RECURSIVE ancestors (
        source_unit_id,
        parent_unit_id,
        path,
        cycle
    ) AS (
        SELECT
            parent.source_unit_id,
            parent.parent_unit_id,
            ARRAY[parent.source_unit_id],
            parent.source_unit_id = NEW.source_unit_id
        FROM memoriesql.source_units AS parent
        WHERE parent.tenant_id = NEW.tenant_id
          AND parent.event_id = NEW.event_id
          AND parent.source_unit_id = NEW.parent_unit_id

        UNION ALL

        SELECT
            parent.source_unit_id,
            parent.parent_unit_id,
            ancestors.path || parent.source_unit_id,
            parent.source_unit_id = ANY(ancestors.path)
                OR parent.source_unit_id = NEW.source_unit_id
        FROM ancestors
        JOIN memoriesql.source_units AS parent
          ON parent.tenant_id = NEW.tenant_id
         AND parent.event_id = NEW.event_id
         AND parent.source_unit_id = ancestors.parent_unit_id
        WHERE NOT ancestors.cycle
    )
    SELECT COALESCE(bool_or(ancestors.cycle), false)
      INTO cycle_found
      FROM ancestors;

    IF cycle_found THEN
        RAISE EXCEPTION 'source unit % creates a cyclic parent hierarchy',
            NEW.source_unit_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER source_events_ready
AFTER INSERT ON memoriesql.source_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_source_event_ready();

CREATE CONSTRAINT TRIGGER source_units_observation_ready
AFTER INSERT ON memoriesql.source_units
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_observation_unit_ready();

CREATE CONSTRAINT TRIGGER source_units_hierarchy_acyclic
AFTER INSERT ON memoriesql.source_units
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_source_unit_acyclic();

CREATE TRIGGER source_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.source_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER source_units_immutable
BEFORE UPDATE OR DELETE ON memoriesql.source_units
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER conversation_turns_immutable
BEFORE UPDATE OR DELETE ON memoriesql.conversation_turns
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER document_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.document_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER document_segments_immutable
BEFORE UPDATE OR DELETE ON memoriesql.document_segments
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER media_segments_immutable
BEFORE UPDATE OR DELETE ON memoriesql.media_segments
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER record_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.record_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER beads_immutable
BEFORE UPDATE OR DELETE ON memoriesql.beads
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

ALTER TABLE memoriesql.source_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.conversation_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.document_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.document_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.media_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.record_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.beads ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE memoriesql.source_objects IS
    'Stable tenant-scoped external source identity; changes arrive as source events.';
COMMENT ON TABLE memoriesql.source_events IS
    'Immutable accepted source occurrence or revision with no authored semantics.';
COMMENT ON TABLE memoriesql.source_units IS
    'Immutable structural evidence units; observation selection is explicit.';
COMMENT ON TABLE memoriesql.beads IS
    'Stable thin observation identity bound one-to-one to an observation unit.';
