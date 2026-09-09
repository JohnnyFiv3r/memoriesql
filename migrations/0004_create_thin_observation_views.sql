CREATE VIEW memoriesql.source_event_cardinality
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
GROUP BY
    event_record.tenant_id,
    event_record.workspace_id,
    event_record.access_scope_id,
    event_record.event_id,
    event_record.observation_unit_count;

CREATE VIEW memoriesql.thin_observations
WITH (security_invoker = true) AS
SELECT
    bead.tenant_id,
    bead.workspace_id,
    bead.access_scope_id,
    bead.bead_id,
    bead.event_id,
    event_record.source_object_id,
    bead.source_unit_id,
    event_record.source_type,
    event_record.source_system,
    event_record.source_identity_key,
    unit_record.unit_kind,
    unit_record.external_unit_id,
    unit_record.unit_ordinal,
    unit_record.parent_unit_id,
    unit_record.content_text,
    unit_record.content_hash AS unit_content_hash,
    event_record.content_hash AS event_content_hash,
    unit_record.hydration_ref,
    unit_record.unit_source_occurred_at,
    unit_record.unit_source_occurred_end_at,
    event_record.source_occurred_at,
    event_record.source_occurred_end_at,
    event_record.source_sequence,
    event_record.captured_at,
    event_record.recorded_at,
    bead.created_at
FROM memoriesql.beads AS bead
JOIN memoriesql.source_units AS unit_record
  ON unit_record.tenant_id = bead.tenant_id
 AND unit_record.source_unit_id = bead.source_unit_id
JOIN memoriesql.source_events AS event_record
  ON event_record.tenant_id = bead.tenant_id
 AND event_record.event_id = bead.event_id;

CREATE VIEW memoriesql.observation_timeline
WITH (security_invoker = true) AS
SELECT
    observation.tenant_id,
    observation.workspace_id,
    observation.access_scope_id,
    observation.bead_id,
    observation.event_id,
    observation.source_object_id,
    observation.source_unit_id,
    observation.source_type,
    observation.source_system,
    observation.unit_kind,
    COALESCE(
        observation.unit_source_occurred_at,
        observation.source_occurred_at,
        observation.captured_at
    ) AS effective_at,
    CASE
        WHEN observation.unit_source_occurred_at IS NOT NULL THEN 'unit_source_time'
        WHEN observation.source_occurred_at IS NOT NULL THEN 'event_source_time'
        ELSE 'capture_time'
    END AS time_basis,
    unit_record.unit_time_precision AS unit_time_precision,
    event_record.source_time_precision AS event_time_precision,
    observation.source_sequence,
    observation.unit_ordinal,
    observation.captured_at,
    observation.recorded_at AS known_at
FROM memoriesql.thin_observations AS observation
JOIN memoriesql.source_units AS unit_record
  ON unit_record.tenant_id = observation.tenant_id
 AND unit_record.source_unit_id = observation.source_unit_id
JOIN memoriesql.source_events AS event_record
  ON event_record.tenant_id = observation.tenant_id
 AND event_record.event_id = observation.event_id;

COMMENT ON VIEW memoriesql.source_event_cardinality IS
    'Deterministic event/unit/bead counts over the append-only source substrate.';
COMMENT ON VIEW memoriesql.thin_observations IS
    'Stable observation identities and structural evidence only; no semantic defaults.';
COMMENT ON VIEW memoriesql.observation_timeline IS
    'Thin observations ordered with an explicit source-versus-capture time basis.';
