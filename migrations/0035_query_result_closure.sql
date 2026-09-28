-- PR-05 private saved-result authority gate. No result bytes or public wire API.
-- M0030/M0032 remain the sole lifecycle projection and governance kernel.
CREATE TABLE memoriesql.query_result_identities (
 tenant_id uuid NOT NULL, result_id uuid NOT NULL, workspace_id uuid NOT NULL,
 principal_id uuid NOT NULL, principal_kind text NOT NULL,
 user_id uuid, on_behalf_of_user_id uuid, pairing_grant_id uuid,
 PRIMARY KEY(tenant_id,result_id),
 FOREIGN KEY(tenant_id,result_id) REFERENCES memoriesql.query_result_creations(tenant_id,result_id) ON DELETE CASCADE
);
ALTER TABLE memoriesql.query_result_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.query_result_identities FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.query_result_identities FROM PUBLIC,memoriesql_application;
CREATE TRIGGER query_result_identities_no_update BEFORE UPDATE ON memoriesql.query_result_identities
 FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

-- Capture the identity in the SAME publication transaction as body, witness,
-- dependency closure and creation receipt. Historical results lacking this
-- binding remain private and cannot acquire one from a later caller.
CREATE FUNCTION memoriesql.capture_query_result_identity_v1() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; o memoriesql.result_preparation_operations%ROWTYPE;
BEGIN
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=NEW.tenant_id AND operation_ref=NEW.operation_ref;
 IF c.tenant_id<>NEW.tenant_id OR o.workspace_id<>c.workspace_id
  OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id) THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 INSERT INTO memoriesql.query_result_identities VALUES(
  NEW.tenant_id,NEW.result_id,c.workspace_id,c.principal_id,c.principal_kind,
  c.user_id,c.on_behalf_of_user_id,c.pairing_grant_id);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION memoriesql.capture_query_result_identity_v1() FROM PUBLIC;
CREATE TRIGGER query_result_capture_identity AFTER INSERT ON memoriesql.query_result_creations
 FOR EACH ROW EXECUTE FUNCTION memoriesql.capture_query_result_identity_v1();

-- Internal verdict only. It must run within the PR-03 session-owned authority
-- fence and RR frame. Reprojection is an authority oracle, not a refresh of the
-- saved historical result: original rows/metadata never change. The saved exact
-- dependency manifest must be a subset of the currently authorized historical
-- projection. New later-admitted records do not change saved bytes or count.
CREATE FUNCTION memoriesql.check_query_result_closure_v1(result_ref uuid,digest text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
 r memoriesql.query_result_creations%ROWTYPE;
 ident memoriesql.query_result_identities%ROWTYPE;
 a memoriesql.result_preparation_artifacts%ROWTYPE;
 o memoriesql.result_preparation_operations%ROWTYPE;
 body jsonb; graph jsonb; deps jsonb; saved_records jsonb; saved_manifest jsonb;
 rebuilt jsonb; current_population jsonb; current_manifest jsonb;
 manifest_text text; records_text text; artifact_hash text;
BEGIN
 IF result_ref IS NULL OR digest IS NULL OR digest !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 c:=memoriesql.result_preparation_authority_v1();
 SELECT * INTO r FROM memoriesql.query_result_creations
  WHERE tenant_id=c.tenant_id AND result_id=result_ref FOR SHARE;
 SELECT * INTO ident FROM memoriesql.query_result_identities
  WHERE tenant_id=c.tenant_id AND result_id=result_ref FOR SHARE;
 IF r.result_id IS NULL OR ident.result_id IS NULL OR r.content_digest<>digest
  OR clock_timestamp()>=r.expires_at OR ident.workspace_id<>c.workspace_id
  OR ident.principal_id<>c.principal_id OR ident.principal_kind<>c.principal_kind
  OR ident.user_id IS DISTINCT FROM c.user_id
  OR ident.on_behalf_of_user_id IS DISTINCT FROM c.on_behalf_of_user_id
  OR ident.pairing_grant_id IS DISTINCT FROM c.pairing_grant_id THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT * INTO o FROM memoriesql.result_preparation_operations
  WHERE tenant_id=c.tenant_id AND operation_ref=r.operation_ref;
 SELECT * INTO a FROM memoriesql.result_preparation_artifacts
  WHERE tenant_id=c.tenant_id AND artifact_ref=result_ref FOR SHARE;
 IF o.operation_ref IS NULL OR a.artifact_ref IS NULL OR o.state<>'sealed'
  OR o.workspace_id<>c.workspace_id OR o.principal_id<>c.principal_id
  OR o.owner_user_id IS DISTINCT FROM COALESCE(c.on_behalf_of_user_id,c.user_id)
  OR encode(sha256(a.content_bytes),'hex')<>digest THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 artifact_hash:=encode(sha256(convert_to('memoriesql-result-preparation-v1','UTF8')||decode('00','hex')||
  int8send(octet_length(a.content_bytes)::bigint)||a.content_bytes||
  int8send(octet_length(a.witness_bytes)::bigint)||a.witness_bytes||
  int8send(octet_length(a.dependency_bytes)::bigint)||a.dependency_bytes),'hex');
 IF artifact_hash<>a.artifact_sha256 THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 body:=convert_from(a.content_bytes,'UTF8')::jsonb;
 graph:=convert_from(a.witness_bytes,'UTF8')::jsonb;
 deps:=convert_from(a.dependency_bytes,'UTF8')::jsonb;
 IF body->>'result_id' IS DISTINCT FROM result_ref::text
  OR body->>'witness_sha256' IS DISTINCT FROM encode(sha256(a.witness_bytes),'hex')
  OR graph->>'frame_ref' IS DISTINCT FROM body#>>'{frame,frame_ref}'
  OR body#>>'{query,run_ref}' IS DISTINCT FROM o.run_ref::text
  OR body#>>'{query,step_key}' IS DISTINCT FROM o.step_key::text
  OR body->>'query_fingerprint' IS DISTINCT FROM o.request_fingerprint
  OR jsonb_typeof(deps->'manifest_base64') IS DISTINCT FROM 'string'
  OR jsonb_typeof(deps->'records_base64') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 manifest_text:=convert_from(decode(deps->>'manifest_base64','base64'),'UTF8');
 records_text:=convert_from(decode(deps->>'records_base64','base64'),'UTF8');
 saved_manifest:=manifest_text::jsonb;
 saved_records:=records_text::jsonb;
 IF jsonb_typeof(saved_manifest)<>'array' OR jsonb_typeof(saved_records)<>'array'
  OR manifest_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(saved_manifest)
  OR records_text IS DISTINCT FROM memoriesql.lifecycle_canonical_json_v1(saved_records)
  OR encode(sha256(convert_to(manifest_text,'UTF8')),'hex') IS DISTINCT FROM body#>>'{frame,projection_manifest_sha256}'
  OR body#>>'{frame,snapshot_digest}' IS DISTINCT FROM body#>>'{frame,projection_manifest_sha256}' THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
  'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
  ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]'::jsonb)
 INTO rebuilt FROM jsonb_array_elements(saved_records) x(v);
 IF rebuilt IS DISTINCT FROM saved_manifest THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 -- PR-03 applies current resource, source, bead, statement, event and lifecycle
 -- authority while selecting the original recorded-knowledge cutoff. Missing
 -- required protected metadata removes its manifest entry and refuses all rows.
 current_population:=memoriesql.prepare_relation_sql_population_v1(
  (body#>>'{frame,known_at}')::timestamptz,67108864);
 current_manifest:=(current_population->>'dependency_manifest_json')::jsonb;
 IF EXISTS(
  SELECT 1 FROM (
   SELECT value FROM jsonb_array_elements(saved_manifest)
   EXCEPT ALL
   SELECT value FROM jsonb_array_elements(current_manifest)
  ) missing
 ) THEN RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501'; END IF;
 -- A concurrent authority mutation must acquire the same PR-03 fence after
 -- this frame. Recheck expiry and live credential before returning the verdict.
 PERFORM memoriesql.result_preparation_authority_v1();
 IF clock_timestamp()>=r.expires_at THEN
  RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire
 OR invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'query_result_unavailable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION memoriesql.check_query_result_closure_v1(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.check_query_result_closure_v1(uuid,text) TO memoriesql_application;
