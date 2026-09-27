-- Trusted preparation only: consume schema-30's single lifecycle/authority kernel.
-- No agent grants, persisted results, SQL executor or second lifecycle interpreter.
CREATE FUNCTION memoriesql.prepare_relation_sql_population_v1(
    requested_known_at timestamptz, byte_budget integer
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET row_security = off
AS $$
DECLARE
    c memoriesql.authorization_contexts%ROWTYPE;
    known timestamptz; snapshot_at timestamptz := statement_timestamp();
    r record; task record; pin record; pair record; d jsonb; item jsonb;
    assertion jsonb; family jsonb; family_deps jsonb; task_pairs jsonb;
    assertions jsonb := '[]'; corrections jsonb := '[]'; types jsonb := '[]';
    pairs jsonb := '[]'; deps jsonb := '[]'; manifest jsonb; result jsonb;
BEGIN
    IF byte_budget IS NULL OR byte_budget NOT BETWEEN 8192 AND 67108864 THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    known := COALESCE(requested_known_at, snapshot_at);
    IF NOT isfinite(known) OR known > snapshot_at
       OR extract(year FROM known AT TIME ZONE 'UTC') NOT BETWEEN 1 AND 9999 THEN
        RAISE EXCEPTION 'invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id)
       OR NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    -- Discovery withholds an entire unreadable assertion family, including all
    -- counts, corrections, type pins, history and root gaps. Nothing is paginated.
    FOR r IN SELECT * FROM memoriesql.relation_assertions_v1(c.tenant_id,known)
             WHERE kind='assessed' AND acceptance='accepted'
               AND workspace_id=c.workspace_id ORDER BY relation_id LOOP
        BEGIN
            assertion := memoriesql.relation_assertion_row_v1(
                c.tenant_id,'assessed',r.relation_id,NULL,known);
            family_deps := memoriesql.relation_assertion_records_v1(
                c.tenant_id,'assessed',r.relation_id,known);
            family := memoriesql.relation_evidence_view_v3(
                c.tenant_id,'assessed_relation',r.relation_id,known);
            family_deps := family_deps || (family->'dependencies');
            item := memoriesql.relation_projection_v1(c.tenant_id,'assessed',r.relation_id,known);
            -- Retain the complete assertion itself: the narrower acceptance/head
            -- records alone do not bind rationale, qualification or confidence.
            family_deps := family_deps || jsonb_build_array(jsonb_build_object(
                'kind','assertion_projection','id',r.relation_id::text,'row',assertion));
        EXCEPTION WHEN insufficient_privilege THEN CONTINUE;
        END;
        assertions := assertions || jsonb_build_array(assertion);
        corrections := corrections || jsonb_build_array(jsonb_build_object(
            'relation_id',r.relation_id,'pins',item->'corrections'));
        types := types || jsonb_build_array(memoriesql.relation_type_definition_v1(r.relation_type_revision_id));
        deps := deps || family_deps;
        IF octet_length(memoriesql.lifecycle_canonical_json_v1(
            jsonb_build_array(assertions,corrections,types,pairs,deps))) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    -- Pair coverage belongs to a captured task, including tasks with no accepted
    -- assertion. Authorize its entire captured bead population and all recorded
    -- proposals before exposing even a not_assessed pair. No task-status claim.
    FOR task IN SELECT a.* FROM memoriesql.relation_assessments a
                WHERE a.tenant_id=c.tenant_id AND a.workspace_id=c.workspace_id
                  AND a.created_at<=known AND EXISTS(
                    SELECT 1 FROM memoriesql.relation_pair_dispositions p
                    WHERE p.tenant_id=a.tenant_id AND p.task_id=a.task_id AND p.recorded_at<=known)
                ORDER BY a.task_id LOOP
        BEGIN
            family_deps := '[]'; task_pairs := '[]';
            FOR d IN SELECT value FROM jsonb_array_elements(task.beads) LOOP
                SELECT v.* INTO pin FROM memoriesql.bead_versions v
                WHERE v.tenant_id=c.tenant_id AND v.bead_version_id=(d->>'bead_version_id')::uuid;
                IF NOT FOUND OR pin.authored_at>known OR NOT memoriesql.current_context_bead_version_authorized(
                    c.tenant_id,pin.workspace_id,pin.access_scope_id,pin.bead_version_id) THEN
                    RAISE EXCEPTION 'pair_unavailable' USING ERRCODE='42501';
                END IF;
                family_deps := family_deps || memoriesql.relation_bead_records_v1(c.tenant_id,pin.bead_id,known);
            END LOOP;
            FOR r IN SELECT * FROM memoriesql.assessed_relations a
                     WHERE a.tenant_id=c.tenant_id AND a.task_id=task.task_id AND a.recorded_at<=known LOOP
                family_deps := family_deps || memoriesql.relation_assertion_records_v1(c.tenant_id,'assessed',r.relation_id,known);
            END LOOP;
            FOR pair IN SELECT p.* FROM memoriesql.relation_pair_dispositions p
                        WHERE p.tenant_id=c.tenant_id AND p.task_id=task.task_id AND p.recorded_at<=known
                        ORDER BY p.first_bead_id,p.second_bead_id LOOP
                item := jsonb_build_object('task_id',pair.task_id,
                    'first_bead_id',pair.first_bead_id,'first_bead_version_id',pair.first_bead_version_id,
                    'second_bead_id',pair.second_bead_id,'second_bead_version_id',pair.second_bead_version_id,
                    'disposition',pair.disposition,'abstention',pair.abstention,'reason',pair.reason_text);
                task_pairs := task_pairs || jsonb_build_array(item);
                family_deps := family_deps || jsonb_build_array(jsonb_build_object('kind','pair_disposition',
                    'id',memoriesql.lifecycle_canonical_json_v1(jsonb_build_array(pair.task_id,pair.first_bead_id,pair.second_bead_id)),
                    'row',item));
            END LOOP;
            family_deps := family_deps || jsonb_build_array(jsonb_build_object('kind','pair_task_population',
                'id',task.task_id::text,'row',jsonb_build_object('task_id',task.task_id,'beads',task.beads)));
        EXCEPTION WHEN insufficient_privilege THEN CONTINUE;
        END;
        pairs := pairs || task_pairs; deps := deps || family_deps;
        IF octet_length(memoriesql.lifecycle_canonical_json_v1(
            jsonb_build_array(assertions,corrections,types,pairs,deps))) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    SELECT COALESCE(jsonb_agg(v ORDER BY v->>'key',(v->>'revision')::integer),'[]') INTO types
        FROM (SELECT DISTINCT value v FROM jsonb_array_elements(types)) x;
    SELECT COALESCE(jsonb_agg(v ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO deps
        FROM (SELECT DISTINCT value v FROM jsonb_array_elements(deps)) x;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',v->'kind','id',v->'id',
        'content_sha256',memoriesql.lifecycle_hash_v1(v->'row'))
        ORDER BY v->>'kind',v->>'id',memoriesql.lifecycle_hash_v1(v->'row')),'[]') INTO manifest
        FROM jsonb_array_elements(deps) x(v);
    result := jsonb_build_object('assertions',assertions,'corrections',corrections,'types',types,'pairs',pairs,
        'frame',jsonb_build_object('known_at',memoriesql.relation_packet_time(known),
            'snapshot_at',memoriesql.relation_packet_time(snapshot_at),
            'dependency_manifest_sha256',memoriesql.lifecycle_hash_v1(manifest)),
        'dependency_records_json',memoriesql.lifecycle_canonical_json_v1(deps),
        'dependency_manifest_json',memoriesql.lifecycle_canonical_json_v1(manifest));
    IF octet_length(result::text) + 8192 > byte_budget THEN
        RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
    END IF;
    IF NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
    RETURN result;
END $$;
REVOKE ALL ON FUNCTION memoriesql.prepare_relation_sql_population_v1(timestamptz,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.prepare_relation_sql_population_v1(timestamptz,integer) TO memoriesql_application;

-- Validate the live clock/credential at the trusted Python handoff after decoding.
-- Resource changes remain serialized by PR-03's shared authority fence. This is
-- not a saved-manifest authorization dispatcher or an agent callable helper.
CREATE FUNCTION memoriesql.check_relation_sql_population_authority_v1() RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET row_security=off AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    IF NOT memoriesql.relation_read_frame_authorized_v1(c.tenant_id)
       OR NOT memoriesql.relation_current_authority_v1(c.tenant_id) THEN
        RAISE EXCEPTION 'population_unavailable' USING ERRCODE='42501';
    END IF;
END $$;
REVOKE ALL ON FUNCTION memoriesql.check_relation_sql_population_authority_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.check_relation_sql_population_authority_v1() TO memoriesql_application;
