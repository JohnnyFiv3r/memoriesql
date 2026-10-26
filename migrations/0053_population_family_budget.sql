-- Forward fix of one relation family failing every query in its workspace.
-- qualified_bead_roots_v2 refuses a derivation lineage of 128 beads or more
-- with root_budget (54000). The relation population reaches it per assessed
-- relation family (relation_assertion_row_v2 and relation_evidence_view_v4),
-- but the family block withheld only insufficient_privilege, so one deep
-- lineage failed the whole population: every query in the workspace, owner
-- and agent alike, returned budget_exhausted. The family is now withheld
-- exactly as an unreadable one: its dependants are dropped by the same
-- closure, and owner pair coverage that recorded it is withheld as before.
-- Every other 54000 (the population byte budget included) still fails.
-- prepare_relation_sql_population_v2 is 0039's definition with only that
-- arm and its message variable added.
CREATE OR REPLACE FUNCTION memoriesql.prepare_relation_sql_population_v2(
    requested_known_at timestamptz, byte_budget integer
,read_mode text) RETURNS jsonb
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
    families jsonb := '[]'; needs jsonb; kept jsonb; disclosed uuid[];
    discovered uuid[]; withheld uuid[] := '{}'; retirement record;
    budget_message text;
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
            assertion := memoriesql.relation_assertion_row_v2(
                c.tenant_id,'assessed',r.relation_id,NULL,known,read_mode);
            item := memoriesql.relation_projection_v1(c.tenant_id,'assessed',r.relation_id,known);
            -- AM-5: lifecycle history (events and their evidence) stays owner-only.
            -- The owner's decision of 2026-10-05: an agent cannot read
            -- relation_events, so its head_token hashes only what it may see,
            -- acceptance and its visible corrections with no events. Owner-only
            -- history never changes it. The owner's token is unchanged.
            IF read_mode='agent' THEN
                assertion := assertion || jsonb_build_object('events','[]'::jsonb,
                    'head_token',memoriesql.lifecycle_hash_v1(
                        (item->'head_manifest') || jsonb_build_object('events','[]'::jsonb)));
            END IF;
            family_deps := memoriesql.relation_assertion_records_v2(
                c.tenant_id,'assessed',r.relation_id,known,read_mode);
            family := memoriesql.relation_evidence_view_v4(
                c.tenant_id,'assessed_relation',r.relation_id,known,read_mode);
            family_deps := family_deps || (family->'dependencies');
            -- Retain the complete assertion itself: the narrower acceptance/head
            -- records alone do not bind rationale, qualification or confidence.
            family_deps := family_deps || jsonb_build_array(jsonb_build_object(
                'kind','assertion_projection','id',r.relation_id::text,'row',assertion));
            -- Owner decision 7 of 2026-10-06: the relation, with its author's
            -- rationale and qualification, only for a reader of its task's whole
            -- pinned population, in both modes. Otherwise it is withheld whole.
            family_deps := family_deps || memoriesql.relation_task_population_records_v1(
                c.tenant_id,(SELECT ar.task_id FROM memoriesql.assessed_relations ar
                    WHERE ar.tenant_id=c.tenant_id AND ar.relation_id=r.relation_id),
                known,read_mode);
            -- In owner mode the row also discloses retirement reasons, which the
            -- retiring task's author wrote: each needs that task's population.
            IF read_mode='owner' THEN
                FOR retirement IN SELECT DISTINCT x.task_id FROM memoriesql.relation_retirements x
                                  WHERE x.tenant_id=c.tenant_id AND x.retired_kind='assessed'
                                    AND x.retired_relation_id=r.relation_id AND x.recorded_at<=known
                                  ORDER BY x.task_id LOOP
                    family_deps := family_deps || memoriesql.relation_task_population_records_v1(
                        c.tenant_id,retirement.task_id,known,read_mode);
                END LOOP;
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                withheld := withheld || r.relation_id;
                CONTINUE;
            -- A family whose derivation roots exceed the lineage budget
            -- (qualified_bead_roots_v2's root_budget) is withheld exactly as
            -- an unreadable family is. Every other limit still fails.
            WHEN program_limit_exceeded THEN
                GET STACKED DIAGNOSTICS budget_message = MESSAGE_TEXT;
                IF budget_message IS DISTINCT FROM 'root_budget' THEN RAISE; END IF;
                withheld := withheld || r.relation_id;
                CONTINUE;
        END;
        -- The other assessed relations this family's records name: its
        -- replacement chain, and derived_from relations on its root lineage.
        SELECT COALESCE(jsonb_agg(DISTINCT dep#>'{row,relation_id}'),'[]') INTO needs
            FROM jsonb_array_elements(family_deps) x(dep)
            WHERE dep->>'kind'='assertion_acceptance' AND dep#>>'{row,kind}'='assessed'
              AND (dep#>>'{row,relation_id}')::uuid<>r.relation_id;
        families := families || jsonb_build_array(jsonb_build_object(
            'relation_id',r.relation_id,'needs',needs,'assertion',assertion,
            'correction',jsonb_build_object('relation_id',r.relation_id,'pins',item->'corrections'),
            'type',memoriesql.relation_type_definition_v1(r.relation_type_revision_id),
            'deps',family_deps));
        IF octet_length(memoriesql.lifecycle_canonical_json_v1(families)) + 8192 > byte_budget THEN
            RAISE EXCEPTION 'population_budget_exhausted' USING ERRCODE='54000';
        END IF;
    END LOOP;
    -- A family is disclosed only with every assessed relation its records name,
    -- so no visible row shows a withheld relation's identity, state or history
    -- (as a replacement or through root status). Repeat until stable. Both read
    -- modes: AM-5 for agents, and the owner's decision 5b for owner mode, which
    -- is not permission to bypass an evidence restriction.
    SELECT array_agg((f->>'relation_id')::uuid) INTO discovered
        FROM jsonb_array_elements(families) f;
    LOOP
        SELECT array_agg((f->>'relation_id')::uuid) INTO disclosed
            FROM jsonb_array_elements(families) f;
        SELECT COALESCE(jsonb_agg(f ORDER BY f->>'relation_id'),'[]') INTO kept
            FROM jsonb_array_elements(families) f
            WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(f->'needs') n(id)
                WHERE NOT n.id::uuid=ANY(COALESCE(disclosed,'{}')));
        EXIT WHEN jsonb_array_length(kept)=jsonb_array_length(families);
        families := kept;
    END LOOP;
    withheld := withheld || ARRAY(SELECT x FROM unnest(COALESCE(discovered,'{}')) x
        WHERE NOT x=ANY(COALESCE(disclosed,'{}')));
    SELECT COALESCE(jsonb_agg(f->'assertion' ORDER BY f->>'relation_id'),'[]'),
           COALESCE(jsonb_agg(f->'correction' ORDER BY f->>'relation_id'),'[]'),
           COALESCE(jsonb_agg(f->'type' ORDER BY f->>'relation_id'),'[]')
        INTO assertions, corrections, types FROM jsonb_array_elements(families) f;
    SELECT COALESCE(jsonb_agg(dep),'[]') INTO deps
        FROM jsonb_array_elements(families) f CROSS JOIN LATERAL jsonb_array_elements(f->'deps') x(dep);
    -- Pair coverage belongs to a captured task, including tasks with no accepted
    -- assertion. Authorize its entire captured bead population and all recorded
    -- proposals before exposing even a not_assessed pair. No task-status claim.
    -- AM-5: pair coverage stays owner-only for agents.
    FOR task IN SELECT a.* FROM memoriesql.relation_assessments a
                WHERE read_mode='owner' AND a.tenant_id=c.tenant_id AND a.workspace_id=c.workspace_id
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
                family_deps := family_deps || memoriesql.relation_bead_records_v2(c.tenant_id,pin.bead_id,known,read_mode);
            END LOOP;
            FOR r IN SELECT * FROM memoriesql.assessed_relations a
                     WHERE a.tenant_id=c.tenant_id AND a.task_id=task.task_id AND a.recorded_at<=known LOOP
                -- Owner decision 5b: a task that recorded a relation withheld
                -- from this reader discloses none of its pair coverage.
                IF r.relation_id=ANY(withheld) THEN
                    RAISE EXCEPTION 'pair_unavailable' USING ERRCODE='42501';
                END IF;
                family_deps := family_deps || memoriesql.relation_assertion_records_v2(c.tenant_id,'assessed',r.relation_id,known,read_mode);
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
REVOKE ALL ON FUNCTION memoriesql.prepare_relation_sql_population_v2(timestamptz,integer,text) FROM PUBLIC;
