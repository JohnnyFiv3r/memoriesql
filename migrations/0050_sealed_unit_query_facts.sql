-- Forward closure of hidden part count and lineage size in query time.
-- After hidden_package_length, a caller without source.raw.read is charged no
-- bytes that vary with what is hidden, and no raw content is read; but every
-- query still aggregated each part's inventory, raw lineage included, so time
-- tracked the hidden part count and lineage size, and a normalized unit's
-- text was gathered part by part. The digest, the normalized flag and the
-- normalized text of a unit's pinned package are fixed once the unit is
-- materialized: the package is sealed and immutable, and the materialization
-- pin is insert-only. Record them once, there; queries read one row and no
-- part row at all. Values equal hidden_package_length's, so its saved results
-- stay valid.

CREATE TABLE memoriesql.logical_unit_query_facts (
 tenant_id uuid NOT NULL, source_unit_id uuid NOT NULL, package_id uuid NOT NULL,
 record_sha256 text NOT NULL CHECK(record_sha256 ~ '^[a-f0-9]{64}$'),
 normalized boolean,
 -- Disclosable normalized text only; a raw or mixed package keeps none.
 normalized_text text,
 CHECK((normalized IS TRUE)=(normalized_text IS NOT NULL)),
 PRIMARY KEY(tenant_id,source_unit_id),
 FOREIGN KEY(tenant_id,source_unit_id) REFERENCES memoriesql.logical_unit_materializations(tenant_id,source_unit_id),
 FOREIGN KEY(tenant_id,package_id) REFERENCES memoriesql.evidence_packages(tenant_id,package_id)
);
ALTER TABLE memoriesql.logical_unit_query_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.logical_unit_query_facts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.logical_unit_query_facts FROM PUBLIC,memoriesql_application;
CREATE TRIGGER unit_query_facts_immutable BEFORE UPDATE OR DELETE ON memoriesql.logical_unit_query_facts
 FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_materialization_identity();

-- The one definition of a unit's facts: exactly hidden_package_length's
-- record digest, text gate and text, computed from its full provenance record.
CREATE FUNCTION memoriesql.unit_query_facts_v1(t uuid,id uuid) RETURNS memoriesql.logical_unit_query_facts
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT t,id,(q.pkg->>'package_id')::uuid,
  memoriesql.lifecycle_hash_v1(q.pkg||jsonb_build_object('source_unit_id',id)),
  (q.pkg->>'normalized')::boolean,
  CASE WHEN (q.pkg->>'normalized')::boolean
   THEN (SELECT string_agg(pp.content,'' ORDER BY pp.ordinal) FROM memoriesql.evidence_package_parts pp
    WHERE pp.tenant_id=t AND pp.package_id=(q.pkg->>'package_id')::uuid) END
 FROM memoriesql.query_unit_package_record_v1(t,id) AS q(pkg)
 WHERE q.pkg IS NOT NULL
$$;
REVOKE ALL ON FUNCTION memoriesql.unit_query_facts_v1(uuid,uuid) FROM PUBLIC;

-- Written by the materializing writer, under its own authority, once.
CREATE FUNCTION memoriesql.record_unit_query_facts() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
BEGIN
 INSERT INTO memoriesql.logical_unit_query_facts
  SELECT f.* FROM memoriesql.unit_query_facts_v1(NEW.tenant_id,NEW.source_unit_id) f
  WHERE f.source_unit_id IS NOT NULL;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION memoriesql.record_unit_query_facts() FROM PUBLIC;
CREATE TRIGGER unit_query_facts_record AFTER INSERT ON memoriesql.logical_unit_materializations
 FOR EACH ROW EXECUTE FUNCTION memoriesql.record_unit_query_facts();

-- Every existing materialization, once; the trigger's lock holds new ones back.
-- With row_security off, a migrating role that RLS would restrict fails here
-- instead of silently skipping rows.
SET LOCAL row_security = off;
INSERT INTO memoriesql.logical_unit_query_facts
 SELECT f.* FROM memoriesql.logical_unit_materializations m
 CROSS JOIN LATERAL memoriesql.unit_query_facts_v1(m.tenant_id,m.source_unit_id) f
 WHERE f.source_unit_id IS NOT NULL;
SET LOCAL row_security = on;

-- The unit's record holds its sealed digest. No part row is read. Facts
-- describe the package sealed for the pin; a pin that no longer names that
-- package (impossible through 0016/0017, so defense in depth) refuses whole.
CREATE OR REPLACE FUNCTION memoriesql.query_unit_records_v1(t uuid,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE u record; digest text; pinned boolean;
BEGIN
 SELECT su.*,ev.source_object_id,ev.recorded_at,ev.content_hash event_content_hash INTO u
  FROM memoriesql.source_units su JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id
  WHERE su.tenant_id=t AND su.source_unit_id=id AND su.created_at<=known AND ev.recorded_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'memory.query','read')
  OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'source.read','read') THEN
  RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT f.record_sha256,f.package_id=m.package_id INTO digest,pinned
  FROM memoriesql.logical_unit_query_facts f
  JOIN memoriesql.logical_unit_materializations m ON m.tenant_id=f.tenant_id AND m.source_unit_id=f.source_unit_id
  WHERE f.tenant_id=t AND f.source_unit_id=id;
 IF digest IS NOT NULL AND pinned IS NOT TRUE THEN
  RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN CASE WHEN digest IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object(
  'kind','unit_package','id',id::text,'row',jsonb_build_object('content_sha256',digest)))
 END || jsonb_build_array(
  jsonb_build_object('kind','source_unit','id',id::text,'row',jsonb_build_object('source_unit_id',id,'event_id',u.event_id,'content_sha256',u.content_hash,'created_at',memoriesql.relation_packet_time(u.created_at))),
  jsonb_build_object('kind','source_event','id',u.event_id::text,'row',jsonb_build_object('event_id',u.event_id,'source_object_id',u.source_object_id,'content_sha256',u.event_content_hash,'recorded_at',memoriesql.relation_packet_time(u.recorded_at))),
  jsonb_build_object('kind','source_object','id',u.source_object_id::text,'row',jsonb_build_object('source_object_id',u.source_object_id)));
END $$;

-- The population's package view: one facts row, the pin and the package the
-- facts were sealed from, never a part row; normalized text is the sealed
-- copy, so reading it costs its visible length only. A pin that no longer
-- names that package refuses, as the unit's records do. The unread
-- provenance 'parts' list is no longer built; every other value is
-- hidden_package_length's.
CREATE OR REPLACE FUNCTION memoriesql.query_unit_package_v1(t uuid,id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE result jsonb; pinned boolean;
BEGIN
 SELECT jsonb_build_object(
  'package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,
  'inventory_sha256',p.inventory_hash,
  'normalization_policy_version',p.declaration->>'normalization_policy_version',
  'package_revision',p.declaration->'package_revision',
  'required_parts',m.package_pin->'required_parts',
  'exclusions',COALESCE(p.declaration#>'{qualification,exclusions}','[]'::jsonb),
  'unresolved_coverage',COALESCE(p.declaration#>'{qualification,unresolved_coverage}','[]'::jsonb),
  'normalized',f.normalized,
  'text',f.normalized_text),f.package_id=m.package_id INTO result,pinned
 FROM memoriesql.logical_unit_query_facts f
 JOIN memoriesql.logical_unit_materializations m ON m.tenant_id=f.tenant_id AND m.source_unit_id=f.source_unit_id
 JOIN memoriesql.evidence_packages p ON p.tenant_id=f.tenant_id AND p.package_id=f.package_id
 WHERE f.tenant_id=t AND f.source_unit_id=id;
 IF result IS NOT NULL AND pinned IS NOT TRUE THEN
  RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN result;
END $$;
