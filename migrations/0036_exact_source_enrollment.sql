-- One confirmed source gets one new explicit scope. Product roles cannot create
-- protected resources or source objects directly; this function is the only
-- enrollment write and derives actor, tenant, workspace, and owner from the
-- current authenticated context.
CREATE FUNCTION memoriesql.enroll_exact_source_v1(
    request_id uuid,
    source_system text,
    installation_id text,
    object_kind text,
    external_object_id text,
    source_schema_version integer,
    exact_source_confirmed boolean
)
RETURNS TABLE (
    source_object_id uuid,
    access_scope_id uuid,
    policy_revision_id uuid,
    replayed boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    previous memoriesql.source_objects%ROWTYPE;
    resource memoriesql.protected_resources%ROWTYPE;
    scope_id uuid;
    policy_id uuid;
    recorded_at timestamptz := pg_catalog.statement_timestamp();
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL THEN
        RAISE EXCEPTION 'source enrollment is unavailable' USING ERRCODE = '42501';
    END IF;
    -- Authority mutations already use this tenant fence. Acquire it before
    -- checking current membership/credential so revocation has one order.
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        authority.tenant_id::text || ':semantic_outcome_authority:', 0
    ));
    IF authority.principal_kind <> 'human'
       OR authority.user_id IS NULL
       OR NOT memoriesql.current_context_has_capability('workspace.manage')
       OR NOT memoriesql.current_context_has_capability('source.manage')
       OR NOT memoriesql.current_context_has_capability('source.share') THEN
        RAISE EXCEPTION 'source enrollment is unavailable' USING ERRCODE = '42501';
    END IF;
    IF request_id IS NULL OR exact_source_confirmed IS DISTINCT FROM TRUE
       OR source_system IS NULL
       OR source_system !~ '^[a-z][a-z0-9._-]{0,127}$'
       OR object_kind IS NULL
       OR object_kind !~ '^[a-z][a-z0-9._-]{0,127}$'
       OR installation_id IS NOT NULL AND (
           length(installation_id) NOT BETWEEN 1 AND 1024
       )
       OR external_object_id IS NULL
       OR length(external_object_id) NOT BETWEEN 1 AND 4096
       OR btrim(external_object_id) = ''
       OR source_schema_version IS NULL
       OR source_schema_version NOT BETWEEN 1 AND 65535 THEN
        RAISE EXCEPTION 'invalid exact source selection' USING ERRCODE = '22023';
    END IF;

    -- A caller-chosen request UUID is the stable source identity and retry key.
    SELECT source.* INTO previous
      FROM memoriesql.source_objects AS source
     WHERE source.tenant_id = authority.tenant_id
       AND source.source_object_id = request_id;
    IF FOUND THEN
        SELECT protected.* INTO resource
          FROM memoriesql.protected_resources AS protected
         WHERE protected.tenant_id = previous.tenant_id
           AND protected.resource_kind = 'source'
           AND protected.resource_id = previous.source_object_id;
        IF resource.status <> 'active'
           OR resource.created_by_principal_id <> authority.principal_id
           OR previous.workspace_id <> authority.workspace_id
           OR previous.owner_user_id <> authority.user_id
           OR previous.source_system <> source_system
           OR previous.installation_id IS DISTINCT FROM installation_id
           OR previous.object_kind <> object_kind
           OR previous.external_object_id <> external_object_id
           OR previous.schema_version <> source_schema_version
           OR NOT memoriesql.current_context_scope_authorized(
               previous.access_scope_id, 'source.manage', 'write'
           ) THEN
            RAISE EXCEPTION 'source enrollment is unavailable'
                USING ERRCODE = '42501';
        END IF;
        SELECT scope.current_policy_revision_id INTO policy_id
          FROM memoriesql.access_scopes AS scope
         WHERE scope.tenant_id = authority.tenant_id
           AND scope.access_scope_id = previous.access_scope_id
           AND scope.status = 'active'
           AND scope.mode = 'explicit';
        IF policy_id IS NULL THEN
            RAISE EXCEPTION 'source enrollment is unavailable'
                USING ERRCODE = '42501';
        END IF;
        RETURN QUERY SELECT previous.source_object_id,
                            previous.access_scope_id, policy_id, true;
        RETURN;
    END IF;

    -- Never co-locate sources in a scope: granting the scope must never grant
    -- an unselected historical source. An existing natural identity is not
    -- reused or disclosed to another requester.
    IF EXISTS (
        SELECT 1 FROM memoriesql.source_objects AS source
         WHERE source.tenant_id = authority.tenant_id
           AND source.source_system = enroll_exact_source_v1.source_system
           AND source.installation_key = COALESCE(enroll_exact_source_v1.installation_id, '')
           AND source.object_kind = enroll_exact_source_v1.object_kind
           AND source.external_object_id = enroll_exact_source_v1.external_object_id
    ) THEN
        RAISE EXCEPTION 'source enrollment is unavailable' USING ERRCODE = '42501';
    END IF;
    scope_id := pg_catalog.uuidv7();
    policy_id := pg_catalog.uuidv7();
    PERFORM memoriesql.create_access_scope(
        scope_id, policy_id, 'explicit', 'exact source enrollment', recorded_at
    );
    -- Explicit scopes do not confer implicit owner reads or writes. Establish
    -- the enrolling human's current grant before binding the resource.
    PERFORM memoriesql.create_access_grant(
        pg_catalog.uuidv7(), authority.principal_id, scope_id,
        ARRAY['read','write','share']::text[], recorded_at, NULL
    );
    INSERT INTO memoriesql.protected_resources (
        tenant_id, workspace_id, access_scope_id, resource_id, resource_kind,
        owner_user_id, status, created_by_principal_id, created_at
    ) VALUES (
        authority.tenant_id, authority.workspace_id, scope_id, request_id,
        'source', authority.user_id, 'active', authority.principal_id, recorded_at
    );
    INSERT INTO memoriesql.source_objects (
        tenant_id, workspace_id, access_scope_id, source_object_id,
        source_system, installation_id, object_kind, external_object_id,
        schema_version, metadata, created_at, last_observed_at, owner_user_id
    ) VALUES (
        authority.tenant_id, authority.workspace_id, scope_id, request_id,
        source_system, installation_id, object_kind, external_object_id,
        source_schema_version, '{}'::jsonb, recorded_at, recorded_at,
        authority.user_id
    );
    RETURN QUERY SELECT request_id, scope_id, policy_id, false;
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'source enrollment is unavailable' USING ERRCODE = '42501';
END;
$$;

-- Bind an existing grant operation to the one enrolled source. The target must
-- separately have an active pairing whose allowed scopes include this scope.
CREATE FUNCTION memoriesql.grant_exact_source_v1(
    request_id uuid,
    requested_source_object_id uuid,
    target_principal_id uuid,
    permission_keys text[],
    valid_from timestamptz,
    expires_at timestamptz
)
RETURNS TABLE (grant_id uuid, replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    selected memoriesql.source_objects%ROWTYPE;
    prior memoriesql.access_grants%ROWTYPE;
    initial memoriesql.access_grant_revisions%ROWTYPE;
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL THEN
        RAISE EXCEPTION 'source grant is unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        authority.tenant_id::text || ':semantic_outcome_authority:', 0
    ));
    IF authority.principal_kind <> 'human'
       OR NOT memoriesql.current_context_has_capability('source.share') THEN
        RAISE EXCEPTION 'source grant is unavailable' USING ERRCODE = '42501';
    END IF;
    IF request_id IS NULL OR requested_source_object_id IS NULL
       OR target_principal_id IS NULL OR valid_from IS NULL
       OR expires_at IS NULL OR expires_at <= valid_from
       OR permission_keys IS NULL OR permission_keys NOT IN (
           ARRAY['read']::text[], ARRAY['write']::text[],
           ARRAY['read','write']::text[]
       ) THEN
        RAISE EXCEPTION 'invalid source grant' USING ERRCODE = '22023';
    END IF;
    SELECT source.* INTO selected
      FROM memoriesql.source_objects AS source
      JOIN memoriesql.protected_resources AS resource
        ON resource.tenant_id = source.tenant_id
       AND resource.workspace_id = source.workspace_id
       AND resource.access_scope_id = source.access_scope_id
       AND resource.resource_kind = 'source'
       AND resource.resource_id = source.source_object_id
       AND resource.status = 'active'
      JOIN memoriesql.access_scopes AS scope
        ON scope.tenant_id = source.tenant_id
       AND scope.access_scope_id = source.access_scope_id
       AND scope.status = 'active' AND scope.mode = 'explicit'
     WHERE source.tenant_id = authority.tenant_id
       AND source.workspace_id = authority.workspace_id
       AND source.source_object_id = requested_source_object_id;
    IF selected.source_object_id IS NULL
       OR NOT memoriesql.current_context_source_authorized(
           selected.access_scope_id, requested_source_object_id,
           'source.share', 'share'
       ) OR (
           SELECT count(*) FROM memoriesql.source_objects AS source
            WHERE source.tenant_id = selected.tenant_id
              AND source.access_scope_id = selected.access_scope_id
       ) <> 1 THEN
        RAISE EXCEPTION 'source grant is unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT root.* INTO prior
      FROM memoriesql.access_grants AS root
     WHERE root.tenant_id = authority.tenant_id
       AND root.grant_id = request_id;
    IF FOUND THEN
        SELECT revision.* INTO initial
          FROM memoriesql.access_grant_revisions AS revision
         WHERE revision.tenant_id = prior.tenant_id
           AND revision.grant_id = prior.grant_id
           AND revision.revision = 1;
        IF prior.workspace_id <> authority.workspace_id
           OR prior.access_scope_id <> selected.access_scope_id
           OR prior.target_principal_id <> target_principal_id
           OR prior.granted_by_principal_id <> authority.principal_id
           OR initial.permission_keys <> permission_keys
           OR initial.valid_from <> valid_from
           OR initial.expires_at <> expires_at
           OR initial.status <> 'active'
           OR EXISTS (
               SELECT 1 FROM memoriesql.access_grant_revisions AS revision
                WHERE revision.tenant_id = prior.tenant_id
                  AND revision.grant_id = prior.grant_id
                  AND revision.revision > 1
           ) THEN
            RAISE EXCEPTION 'source grant is unavailable' USING ERRCODE = '42501';
        END IF;
        RETURN QUERY SELECT prior.grant_id, true;
        RETURN;
    END IF;
    PERFORM memoriesql.create_access_grant(
        request_id, target_principal_id, selected.access_scope_id,
        permission_keys, valid_from, expires_at
    );
    RETURN QUERY SELECT request_id, false;
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'source grant is unavailable' USING ERRCODE = '42501';
END;
$$;

CREATE TABLE memoriesql.source_revocation_receipts (
    tenant_id uuid NOT NULL,
    request_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    actor_principal_id uuid NOT NULL,
    reason text NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, request_id),
    FOREIGN KEY (tenant_id, source_object_id)
        REFERENCES memoriesql.source_objects (tenant_id, source_object_id),
    FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CHECK (btrim(reason) <> '' AND length(reason) <= 1024)
);
CREATE TRIGGER source_revocation_receipts_immutable
BEFORE UPDATE OR DELETE ON memoriesql.source_revocation_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

CREATE FUNCTION memoriesql.revoke_exact_source_v1(
    request_id uuid,
    requested_source_object_id uuid,
    reason text
)
RETURNS TABLE (replayed boolean, recorded_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authority memoriesql.authorization_contexts%ROWTYPE;
    selected memoriesql.source_objects%ROWTYPE;
    resource memoriesql.protected_resources%ROWTYPE;
    prior memoriesql.source_revocation_receipts%ROWTYPE;
    revocation_time timestamptz := pg_catalog.statement_timestamp();
BEGIN
    SELECT context.* INTO authority
      FROM memoriesql.current_authorization_context() AS context;
    IF authority.tenant_id IS NULL THEN
        RAISE EXCEPTION 'source revocation is unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        authority.tenant_id::text || ':semantic_outcome_authority:', 0
    ));
    IF authority.principal_kind <> 'human'
       OR authority.user_id IS NULL
       OR NOT memoriesql.current_context_has_capability('source.manage') THEN
        RAISE EXCEPTION 'source revocation is unavailable' USING ERRCODE = '42501';
    END IF;
    IF request_id IS NULL OR requested_source_object_id IS NULL
       OR reason IS NULL OR btrim(reason) = '' OR length(reason) > 1024 THEN
        RAISE EXCEPTION 'invalid source revocation' USING ERRCODE = '22023';
    END IF;
    SELECT source.* INTO selected
      FROM memoriesql.source_objects AS source
     WHERE source.tenant_id = authority.tenant_id
       AND source.workspace_id = authority.workspace_id
       AND source.source_object_id = requested_source_object_id;
    IF selected.source_object_id IS NULL
       OR selected.owner_user_id <> authority.user_id
       OR NOT memoriesql.current_context_scope_authorized(
           selected.access_scope_id, 'source.manage', 'write'
       ) THEN
        RAISE EXCEPTION 'source revocation is unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT receipt.* INTO prior
      FROM memoriesql.source_revocation_receipts AS receipt
     WHERE receipt.tenant_id = authority.tenant_id
       AND receipt.request_id = revoke_exact_source_v1.request_id;
    IF FOUND THEN
        IF prior.workspace_id <> authority.workspace_id
           OR prior.source_object_id <> requested_source_object_id
           OR prior.actor_principal_id <> authority.principal_id
           OR prior.reason <> revoke_exact_source_v1.reason THEN
            RAISE EXCEPTION 'source revocation is unavailable'
                USING ERRCODE = '42501';
        END IF;
        RETURN QUERY SELECT true, prior.recorded_at;
        RETURN;
    END IF;
    SELECT protected.* INTO resource
      FROM memoriesql.protected_resources AS protected
     WHERE protected.tenant_id = selected.tenant_id
       AND protected.resource_kind = 'source'
       AND protected.resource_id = selected.source_object_id
     FOR UPDATE;
    IF resource.status <> 'active' THEN
        RAISE EXCEPTION 'source revocation is unavailable' USING ERRCODE = '42501';
    END IF;
    UPDATE memoriesql.protected_resources AS protected
       SET status = 'revoked', revoked_at = revocation_time
     WHERE protected.tenant_id = selected.tenant_id
       AND protected.resource_kind = 'source'
       AND protected.resource_id = selected.source_object_id;
    INSERT INTO memoriesql.source_revocation_receipts VALUES (
        authority.tenant_id, request_id, authority.workspace_id,
        requested_source_object_id, authority.principal_id, reason, revocation_time
    );
    RETURN QUERY SELECT false, revocation_time;
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'source revocation is unavailable' USING ERRCODE = '42501';
END;
$$;

REVOKE ALL ON TABLE memoriesql.source_revocation_receipts FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.enroll_exact_source_v1(
    uuid,text,text,text,text,integer,boolean
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.grant_exact_source_v1(
    uuid,uuid,uuid,text[],timestamptz,timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.revoke_exact_source_v1(uuid,uuid,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.enroll_exact_source_v1(
    uuid,text,text,text,text,integer,boolean
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.grant_exact_source_v1(
    uuid,uuid,uuid,text[],timestamptz,timestamptz
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.revoke_exact_source_v1(uuid,uuid,text)
    TO memoriesql_application;
