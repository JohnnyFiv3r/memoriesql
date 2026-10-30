-- Forward closure of hidden package length in query work and retained bytes.
-- Hidden rows may not affect what callers see. A caller with memory.query and
-- source.read but not source.raw.read is charged database time and retained
-- bytes; neither may vary with a unit's raw content, part count or raw-lineage
-- offsets. This closes retained bytes exactly and stops raw content reads;
-- package reads still visit each part's inventory, so time tracks part count
-- and lineage size until per-materialization facts are sealed.
-- Approved disclosure is unchanged (0037 owner decision of 2026-09-28):
-- normalized text only, never parts or lineage.
-- 0037, 0039, 0044 and 0046 stay as installed; they reach these by name.

-- The pinned package's protected provenance: 0037's package value without
-- 'text', key for key. No part content is referenced.
CREATE FUNCTION memoriesql.query_unit_package_record_v1(t uuid,id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT jsonb_build_object(
  'package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,
  'inventory_sha256',p.inventory_hash,
  'normalization_policy_version',p.declaration->>'normalization_policy_version',
  'package_revision',p.declaration->'package_revision',
  'required_parts',m.package_pin->'required_parts',
  'exclusions',COALESCE(p.declaration#>'{qualification,exclusions}','[]'::jsonb),
  'unresolved_coverage',COALESCE(p.declaration#>'{qualification,unresolved_coverage}','[]'::jsonb),
  'normalized',p.sealed_receipt_id IS NOT NULL
   AND m.package_pin->>'inventory_sha256'=p.inventory_hash
   AND count(pp.part_id)=(m.package_pin->>'required_parts')::integer
   AND bool_and(pp.inventory->>'derivation'='producer_normalized'),
  'parts',jsonb_agg(jsonb_build_object('part_id',pp.part_id,'ordinal',pp.ordinal,
   'content_sha256',pp.inventory->'content_sha256','derivation',pp.inventory->'derivation',
   'lineage',pp.inventory->'lineage') ORDER BY pp.ordinal))
 FROM memoriesql.logical_unit_materializations m
 JOIN memoriesql.evidence_packages p ON p.tenant_id=m.tenant_id AND p.package_id=m.package_id
 JOIN memoriesql.evidence_package_parts pp ON pp.tenant_id=p.tenant_id AND pp.package_id=p.package_id
 WHERE m.tenant_id=t AND m.source_unit_id=id
 GROUP BY p.package_id,p.sealed_receipt_id,p.inventory_hash,p.declaration,m.package_pin
$$;
REVOKE ALL ON FUNCTION memoriesql.query_unit_package_record_v1(uuid,uuid) FROM PUBLIC;

-- The same value as 0037 for every package. An aggregate inside CASE runs for
-- every group, so 0037 concatenated raw parts and discarded them; a sub-select
-- runs only on its taken branch, so only normalized text is ever read.
CREATE OR REPLACE FUNCTION memoriesql.query_unit_package_v1(t uuid,id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
 SELECT q.pkg||jsonb_build_object('text',CASE WHEN (q.pkg->>'normalized')::boolean
   THEN (SELECT string_agg(pp.content,'' ORDER BY pp.ordinal) FROM memoriesql.evidence_package_parts pp
    WHERE pp.tenant_id=t AND pp.package_id=(q.pkg->>'package_id')::uuid) END)
 FROM memoriesql.query_unit_package_record_v1(t,id) AS q(pkg)
 WHERE q.pkg IS NOT NULL
$$;

-- 0046's helper-frame pattern for query records: a unit_package record holds
-- only the fixed-width digest of 0037's record row, so population estimates,
-- stored dependency records and their charge do not grow with hidden parts or
-- offsets. The full row is built only here, to be hashed: every manifest still
-- binds it, and 0044's rebuild compares digests. A result saved before this
-- migration retains the full row, no longer rebuilds equal, and is unavailable.
CREATE OR REPLACE FUNCTION memoriesql.query_unit_records_v1(t uuid,id uuid,known timestamptz) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE u record; pkg jsonb;
BEGIN
 SELECT su.*,ev.source_object_id,ev.recorded_at,ev.content_hash event_content_hash INTO u
  FROM memoriesql.source_units su JOIN memoriesql.source_events ev ON ev.tenant_id=su.tenant_id AND ev.event_id=su.event_id
  WHERE su.tenant_id=t AND su.source_unit_id=id AND su.created_at<=known AND ev.recorded_at<=known;
 IF NOT FOUND OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'memory.query','read')
  OR NOT memoriesql.current_context_event_authorized(u.access_scope_id,u.event_id,'source.read','read') THEN
  RAISE EXCEPTION 'dependency_unavailable' USING ERRCODE='42501';
 END IF;
 pkg:=memoriesql.query_unit_package_record_v1(t,id);
 RETURN CASE WHEN pkg IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object(
  'kind','unit_package','id',id::text,'row',jsonb_build_object('content_sha256',
   memoriesql.lifecycle_hash_v1(pkg||jsonb_build_object('source_unit_id',id)))))
 END || jsonb_build_array(
  jsonb_build_object('kind','source_unit','id',id::text,'row',jsonb_build_object('source_unit_id',id,'event_id',u.event_id,'content_sha256',u.content_hash,'created_at',memoriesql.relation_packet_time(u.created_at))),
  jsonb_build_object('kind','source_event','id',u.event_id::text,'row',jsonb_build_object('event_id',u.event_id,'source_object_id',u.source_object_id,'content_sha256',u.event_content_hash,'recorded_at',memoriesql.relation_packet_time(u.recorded_at))),
  jsonb_build_object('kind','source_object','id',u.source_object_id::text,'row',jsonb_build_object('source_object_id',u.source_object_id)));
END $$;
