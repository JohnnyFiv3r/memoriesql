DO $roles$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'memoriesql_application'
    ) THEN
        CREATE ROLE memoriesql_application;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'memoriesql_worker'
    ) THEN
        CREATE ROLE memoriesql_worker;
    END IF;
    ALTER ROLE memoriesql_application
        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
        NOREPLICATION NOBYPASSRLS;
    ALTER ROLE memoriesql_worker
        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
        NOREPLICATION NOBYPASSRLS;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS member_role
          ON member_role.oid = membership.member
        WHERE member_role.rolname IN (
            'memoriesql_application', 'memoriesql_worker'
        )
    ) THEN
        RAISE EXCEPTION
            'memorieSQL product roles must not be members of another database role';
    END IF;
END;
$roles$;

CREATE TABLE memoriesql.protected_resources (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    resource_id uuid NOT NULL,
    resource_kind text NOT NULL,
    owner_user_id uuid,
    status text NOT NULL,
    created_by_principal_id uuid,
    created_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT protected_resources_pk
        PRIMARY KEY (tenant_id, resource_kind, resource_id),
    CONSTRAINT protected_resources_scope_identity_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        resource_id,
        resource_kind
    ),
    CONSTRAINT protected_resources_scope_owner_identity_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        owner_user_id,
        resource_id,
        resource_kind
    ),
    CONSTRAINT protected_resources_scope_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id
    ) REFERENCES memoriesql.access_scopes (
        tenant_id,
        workspace_id,
        access_scope_id
    ),
    CONSTRAINT protected_resources_owner_fk
        FOREIGN KEY (tenant_id, owner_user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT protected_resources_creator_fk
        FOREIGN KEY (tenant_id, created_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT protected_resources_kind_supported
        CHECK (resource_kind IN ('source', 'model', 'file', 'render')),
    CONSTRAINT protected_resources_status_supported
        CHECK (status IN ('active', 'revoked', 'legacy_unclaimed')),
    CONSTRAINT protected_resources_shape CHECK (
        (
            status = 'legacy_unclaimed'
            AND owner_user_id IS NULL
            AND created_by_principal_id IS NULL
            AND revoked_at IS NULL
        )
        OR (
            status = 'active'
            AND owner_user_id IS NOT NULL
            AND created_by_principal_id IS NOT NULL
            AND revoked_at IS NULL
        )
        OR (
            status = 'revoked'
            AND owner_user_id IS NOT NULL
            AND created_by_principal_id IS NOT NULL
            AND revoked_at IS NOT NULL
        )
    )
);

WITH observed_workspaces AS (
    SELECT tenant_id, workspace_id FROM memoriesql.source_objects
    UNION SELECT tenant_id, workspace_id FROM memoriesql.source_events
    UNION SELECT tenant_id, workspace_id FROM memoriesql.source_units
    UNION SELECT tenant_id, workspace_id FROM memoriesql.conversation_turns
    UNION SELECT tenant_id, workspace_id FROM memoriesql.document_revisions
    UNION SELECT tenant_id, workspace_id FROM memoriesql.document_segments
    UNION SELECT tenant_id, workspace_id FROM memoriesql.media_segments
    UNION SELECT tenant_id, workspace_id FROM memoriesql.record_events
    UNION SELECT tenant_id, workspace_id FROM memoriesql.beads
    UNION SELECT tenant_id, workspace_id FROM memoriesql.idempotency_receipts
    UNION SELECT tenant_id, workspace_id FROM memoriesql.semantic_task_receipts
    UNION SELECT tenant_id, workspace_id FROM memoriesql.outbox_events
)
INSERT INTO memoriesql.workspaces (
    tenant_id, workspace_id, authority_mode, owner_user_id, status, created_at
)
SELECT
    tenant_id, workspace_id, NULL, NULL, 'legacy_unclaimed', transaction_timestamp()
FROM observed_workspaces
ON CONFLICT (tenant_id, workspace_id) DO NOTHING;

WITH observed_scopes AS (
    SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.source_objects
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.source_events
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.source_units
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.conversation_turns
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.document_revisions
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.document_segments
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.media_segments
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.record_events
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.beads
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.idempotency_receipts
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.semantic_task_receipts
    UNION SELECT tenant_id, workspace_id, access_scope_id FROM memoriesql.outbox_events
)
INSERT INTO memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id, owner_user_id,
    mode, current_policy_revision_id, status, created_at
)
SELECT
    tenant_id, workspace_id, access_scope_id, NULL,
    NULL, NULL, 'legacy_unclaimed', transaction_timestamp()
FROM observed_scopes
ON CONFLICT (tenant_id, access_scope_id) DO NOTHING;

SET CONSTRAINTS ALL IMMEDIATE;

INSERT INTO memoriesql.protected_resources (
    tenant_id,
    workspace_id,
    access_scope_id,
    resource_id,
    resource_kind,
    owner_user_id,
    status,
    created_by_principal_id,
    created_at,
    revoked_at
)
SELECT
    tenant_id,
    workspace_id,
    access_scope_id,
    source_object_id,
    'source',
    NULL,
    'legacy_unclaimed',
    NULL,
    created_at,
    NULL
FROM memoriesql.source_objects;

ALTER TABLE memoriesql.source_objects
ADD COLUMN owner_user_id uuid;

ALTER TABLE memoriesql.source_objects
ADD COLUMN resource_kind text
GENERATED ALWAYS AS ('source'::text) STORED;

ALTER TABLE memoriesql.source_objects
ADD CONSTRAINT source_objects_resource_fk FOREIGN KEY (
    tenant_id,
    workspace_id,
    access_scope_id,
    source_object_id,
    resource_kind
) REFERENCES memoriesql.protected_resources (
    tenant_id,
    workspace_id,
    access_scope_id,
    resource_id,
    resource_kind
) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE memoriesql.source_objects
ADD CONSTRAINT source_objects_owner_fk FOREIGN KEY (tenant_id, owner_user_id)
REFERENCES memoriesql.users (tenant_id, user_id);

CREATE TABLE memoriesql.file_objects (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    file_id uuid NOT NULL,
    resource_kind text GENERATED ALWAYS AS ('file'::text) STORED,
    owner_user_id uuid NOT NULL,
    purpose text NOT NULL,
    content_hash text NOT NULL,
    byte_size bigint NOT NULL,
    media_type text NOT NULL,
    storage_provider text NOT NULL,
    opaque_locator text NOT NULL,
    encryption_key_ref text,
    status text NOT NULL,
    created_by_principal_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT file_objects_pk PRIMARY KEY (tenant_id, file_id),
    CONSTRAINT file_objects_scope_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        file_id
    ),
    CONSTRAINT file_objects_resource_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        owner_user_id,
        file_id,
        resource_kind
    ) REFERENCES memoriesql.protected_resources (
        tenant_id,
        workspace_id,
        access_scope_id,
        owner_user_id,
        resource_id,
        resource_kind
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT file_objects_creator_fk
        FOREIGN KEY (tenant_id, created_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT file_objects_purpose_supported CHECK (
        purpose IN (
            'source_original',
            'attachment',
            'rendered_view',
            'export',
            'backup'
        )
    ),
    CONSTRAINT file_objects_hash_nonempty CHECK (btrim(content_hash) <> ''),
    CONSTRAINT file_objects_size_nonnegative CHECK (byte_size >= 0),
    CONSTRAINT file_objects_media_type_nonempty CHECK (btrim(media_type) <> ''),
    CONSTRAINT file_objects_provider_supported CHECK (
        storage_provider IN ('local_content_store')
    ),
    CONSTRAINT file_objects_locator_opaque CHECK (
        opaque_locator ~
            '^local_content_store:[A-Za-z0-9][A-Za-z0-9._/-]*$'
        AND length(opaque_locator) <= 1024
        AND split_part(opaque_locator, ':', 1) = storage_provider
        AND split_part(opaque_locator, ':', 2) !~ '(^|/)\.{1,2}(/|$)'
    ),
    CONSTRAINT file_objects_encryption_ref_nonempty
        CHECK (encryption_key_ref IS NULL OR btrim(encryption_key_ref) <> ''),
    CONSTRAINT file_objects_status_supported
        CHECK (status IN ('active', 'quarantined', 'deleted')),
    CONSTRAINT file_objects_deletion_state CHECK (
        (status = 'deleted' AND deleted_at IS NOT NULL)
        OR (status <> 'deleted' AND deleted_at IS NULL)
    )
);

ALTER TABLE memoriesql.source_objects
ADD CONSTRAINT source_objects_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.source_events
ADD CONSTRAINT source_events_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.source_units
ADD CONSTRAINT source_units_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.conversation_turns
ADD CONSTRAINT conversation_turns_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.document_revisions
ADD CONSTRAINT document_revisions_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.document_segments
ADD CONSTRAINT document_segments_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.media_segments
ADD CONSTRAINT media_segments_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.record_events
ADD CONSTRAINT record_events_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.beads
ADD CONSTRAINT beads_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.idempotency_receipts
ADD CONSTRAINT idempotency_receipts_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.semantic_task_receipts
ADD CONSTRAINT semantic_task_receipts_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);
ALTER TABLE memoriesql.outbox_events
ADD CONSTRAINT outbox_events_access_scope_fk FOREIGN KEY (
    tenant_id, workspace_id, access_scope_id
) REFERENCES memoriesql.access_scopes (
    tenant_id, workspace_id, access_scope_id
);

CREATE OR REPLACE FUNCTION memoriesql.protect_source_object_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        NEW.tenant_id,
        NEW.workspace_id,
        NEW.access_scope_id,
        NEW.source_object_id,
        NEW.owner_user_id,
        NEW.source_system,
        NEW.installation_id,
        NEW.object_kind,
        NEW.external_object_id,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id,
        OLD.workspace_id,
        OLD.access_scope_id,
        OLD.source_object_id,
        OLD.owner_user_id,
        OLD.source_system,
        OLD.installation_id,
        OLD.object_kind,
        OLD.external_object_id,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'memoriesql.source_objects stable identity and authority are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.enforce_resource_scope_shape()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    scope_status text;
    scope_owner uuid;
BEGIN
    SELECT scope.status, scope.owner_user_id
      INTO STRICT scope_status, scope_owner
      FROM memoriesql.access_scopes AS scope
     WHERE scope.tenant_id = NEW.tenant_id
       AND scope.workspace_id = NEW.workspace_id
       AND scope.access_scope_id = NEW.access_scope_id;

    IF NEW.status = 'legacy_unclaimed' THEN
        IF scope_status <> 'legacy_unclaimed' OR NEW.owner_user_id IS NOT NULL THEN
            RAISE EXCEPTION 'legacy resource must remain bound to an unclaimed scope'
                USING ERRCODE = '23514';
        END IF;
    ELSIF scope_status <> 'active'
       OR NEW.owner_user_id IS DISTINCT FROM scope_owner THEN
        RAISE EXCEPTION 'active resource owner must match its active access scope'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.enforce_source_resource_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    resource_owner uuid;
    resource_status text;
BEGIN
    SELECT resource.owner_user_id, resource.status
      INTO STRICT resource_owner, resource_status
      FROM memoriesql.protected_resources AS resource
     WHERE resource.tenant_id = NEW.tenant_id
       AND resource.workspace_id = NEW.workspace_id
       AND resource.access_scope_id = NEW.access_scope_id
       AND resource.resource_kind = 'source'
       AND resource.resource_id = NEW.source_object_id;

    IF NEW.owner_user_id IS DISTINCT FROM resource_owner THEN
        RAISE EXCEPTION 'source owner must match its protected resource'
            USING ERRCODE = '23514';
    END IF;
    IF resource_status NOT IN ('active', 'legacy_unclaimed') THEN
        RAISE EXCEPTION 'source cannot bind a revoked resource'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.validate_access_grant_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_revision integer;
BEGIN
    SELECT COALESCE(max(revision), 0) + 1
      INTO expected_revision
      FROM memoriesql.access_grant_revisions
     WHERE tenant_id = NEW.tenant_id
       AND grant_id = NEW.grant_id;
    IF NEW.revision <> expected_revision THEN
        RAISE EXCEPTION 'access grant revision must advance contiguously to %',
            expected_revision USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memoriesql.validate_pairing_grant_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_revision integer;
    paired_owner_user uuid;
BEGIN
    SELECT COALESCE(max(revision), 0) + 1
      INTO expected_revision
      FROM memoriesql.pairing_grant_revisions
     WHERE tenant_id = NEW.tenant_id
       AND pairing_grant_id = NEW.pairing_grant_id;
    IF NEW.revision <> expected_revision THEN
        RAISE EXCEPTION 'pairing-grant revision must advance contiguously to %',
            expected_revision USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM unnest(NEW.allowed_capabilities) AS requested(capability_key)
        LEFT JOIN memoriesql.capabilities AS capability
          ON capability.capability_key = requested.capability_key
        WHERE capability.capability_key IS NULL
    ) THEN
        RAISE EXCEPTION 'pairing grant contains an unknown capability'
            USING ERRCODE = '23514';
    END IF;
    SELECT pairing_grant.on_behalf_of_user_id
      INTO STRICT paired_owner_user
      FROM memoriesql.pairing_grants AS pairing_grant
     WHERE pairing_grant.tenant_id = NEW.tenant_id
       AND pairing_grant.workspace_id = NEW.workspace_id
       AND pairing_grant.pairing_grant_id = NEW.pairing_grant_id;
    IF EXISTS (
        SELECT 1
        FROM unnest(NEW.allowed_capabilities) AS requested(capability_key)
        JOIN memoriesql.pairing_grants AS pairing_grant
          ON pairing_grant.tenant_id = NEW.tenant_id
         AND pairing_grant.workspace_id = NEW.workspace_id
         AND pairing_grant.pairing_grant_id = NEW.pairing_grant_id
        JOIN memoriesql.workspace_memberships AS membership
          ON membership.tenant_id = pairing_grant.tenant_id
         AND membership.workspace_id = pairing_grant.workspace_id
         AND membership.principal_id = pairing_grant.paired_principal_id
        LEFT JOIN memoriesql.role_capabilities AS role_capability
          ON role_capability.role_key = membership.role_key
         AND role_capability.capability_key = requested.capability_key
        WHERE role_capability.capability_key IS NULL
    ) THEN
        RAISE EXCEPTION 'pairing-grant capability exceeds the paired role'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM unnest(NEW.allowed_access_scope_ids) AS requested(access_scope_id)
        LEFT JOIN memoriesql.access_scopes AS scope
          ON scope.tenant_id = NEW.tenant_id
         AND scope.workspace_id = NEW.workspace_id
         AND scope.access_scope_id = requested.access_scope_id
         AND scope.owner_user_id = paired_owner_user
         AND scope.status = 'active'
        WHERE scope.access_scope_id IS NULL
    ) THEN
        RAISE EXCEPTION 'pairing grant contains an inactive, foreign, or unowned scope'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER protected_resources_validate_scope
BEFORE INSERT OR UPDATE ON memoriesql.protected_resources
FOR EACH ROW EXECUTE FUNCTION memoriesql.enforce_resource_scope_shape();
CREATE TRIGGER source_objects_validate_resource
BEFORE INSERT OR UPDATE ON memoriesql.source_objects
FOR EACH ROW EXECUTE FUNCTION memoriesql.enforce_source_resource_binding();
CREATE TRIGGER protected_resources_no_delete
BEFORE DELETE ON memoriesql.protected_resources
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER file_objects_immutable
BEFORE UPDATE OR DELETE ON memoriesql.file_objects
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER access_grant_revisions_contiguous
BEFORE INSERT ON memoriesql.access_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.validate_access_grant_revision();
CREATE TRIGGER pairing_grant_revisions_validate
BEFORE INSERT ON memoriesql.pairing_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.validate_pairing_grant_revision();

CREATE FUNCTION memoriesql.current_authorization_context()
RETURNS SETOF memoriesql.authorization_contexts
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT context.*
    FROM memoriesql.authorization_contexts AS context
    WHERE context.context_id::text =
          pg_catalog.current_setting('memoriesql.authorization_context_id', true)
      AND context.backend_pid = pg_catalog.pg_backend_pid()
      AND context.transaction_id = pg_catalog.txid_current()
      AND context.expires_at > pg_catalog.statement_timestamp()
$$;

CREATE FUNCTION memoriesql.current_context_has_capability(
    requested_capability text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.authentication_credentials AS credential
          ON credential.tenant_id = context.tenant_id
         AND credential.credential_id = context.credential_id
         AND credential.principal_id = context.principal_id
         AND credential.status = 'active'
         AND credential.expires_at > pg_catalog.statement_timestamp()
        JOIN memoriesql.principals AS principal
          ON principal.tenant_id = context.tenant_id
         AND principal.principal_id = context.principal_id
         AND principal.status = 'active'
        JOIN memoriesql.users AS authority_user
          ON authority_user.tenant_id = context.tenant_id
         AND authority_user.user_id = COALESCE(
             context.user_id, context.on_behalf_of_user_id
         )
         AND authority_user.status = 'active'
        JOIN memoriesql.workspaces AS workspace
          ON workspace.tenant_id = context.tenant_id
         AND workspace.workspace_id = context.workspace_id
         AND workspace.status = 'active'
        JOIN memoriesql.workspace_memberships AS membership
          ON membership.tenant_id = context.tenant_id
         AND membership.workspace_id = context.workspace_id
         AND membership.principal_id = context.principal_id
         AND membership.status = 'active'
         AND membership.revision = context.membership_revision
        JOIN memoriesql.role_capabilities AS role_capability
          ON role_capability.role_key = membership.role_key
         AND role_capability.capability_key = requested_capability
        WHERE context.pairing_grant_id IS NULL
           OR EXISTS (
                SELECT 1
                FROM memoriesql.pairing_grants AS pairing_grant
                JOIN memoriesql.pairing_grant_revisions AS revision
                  ON revision.tenant_id = pairing_grant.tenant_id
                 AND revision.workspace_id = pairing_grant.workspace_id
                 AND revision.pairing_grant_id = pairing_grant.pairing_grant_id
                 AND revision.revision = context.pairing_grant_revision
                WHERE pairing_grant.tenant_id = context.tenant_id
                  AND pairing_grant.workspace_id = context.workspace_id
                  AND pairing_grant.pairing_grant_id = context.pairing_grant_id
                  AND pairing_grant.paired_principal_id = context.principal_id
                  AND revision.status = 'active'
                  AND revision.expires_at > pg_catalog.statement_timestamp()
                  AND requested_capability = ANY(revision.allowed_capabilities)
                  AND revision.revision = (
                      SELECT max(latest.revision)
                      FROM memoriesql.pairing_grant_revisions AS latest
                      WHERE latest.tenant_id = revision.tenant_id
                        AND latest.pairing_grant_id = revision.pairing_grant_id
                  )
           )
    )
$$;

CREATE FUNCTION memoriesql.current_context_scope_permits(
    requested_access_scope_id uuid,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.access_scopes AS scope
          ON scope.tenant_id = context.tenant_id
         AND scope.workspace_id = context.workspace_id
         AND scope.access_scope_id = requested_access_scope_id
         AND scope.status = 'active'
        WHERE requested_permission IN ('read', 'write', 'share', 'export', 'delete')
          AND (
              (
                  scope.mode = 'owner_private'
                  AND (
                      (
                          context.principal_kind = 'human'
                          AND context.user_id = scope.owner_user_id
                      )
                      OR (
                          context.pairing_grant_id IS NOT NULL
                          AND context.on_behalf_of_user_id = scope.owner_user_id
                          AND EXISTS (
                              SELECT 1
                              FROM memoriesql.pairing_grant_revisions AS revision
                              WHERE revision.tenant_id = context.tenant_id
                                AND revision.pairing_grant_id = context.pairing_grant_id
                                AND revision.revision = context.pairing_grant_revision
                                AND revision.status = 'active'
                                AND revision.expires_at > pg_catalog.statement_timestamp()
                                AND requested_access_scope_id =
                                    ANY(revision.allowed_access_scope_ids)
                          )
                      )
                  )
              )
              OR (
                  scope.mode = 'explicit'
                  AND EXISTS (
                      SELECT 1
                      FROM memoriesql.access_grants AS grant_record
                      JOIN memoriesql.access_grant_revisions AS revision
                        ON revision.tenant_id = grant_record.tenant_id
                       AND revision.workspace_id = grant_record.workspace_id
                       AND revision.access_scope_id = grant_record.access_scope_id
                       AND revision.grant_id = grant_record.grant_id
                      WHERE grant_record.tenant_id = context.tenant_id
                        AND grant_record.workspace_id = context.workspace_id
                        AND grant_record.access_scope_id = requested_access_scope_id
                        AND grant_record.target_principal_id = context.principal_id
                        AND revision.revision = (
                            SELECT max(latest.revision)
                            FROM memoriesql.access_grant_revisions AS latest
                            WHERE latest.tenant_id = revision.tenant_id
                              AND latest.grant_id = revision.grant_id
                        )
                        AND revision.status = 'active'
                        AND revision.valid_from <= pg_catalog.statement_timestamp()
                        AND (
                            revision.expires_at IS NULL
                            OR revision.expires_at > pg_catalog.statement_timestamp()
                        )
                        AND requested_permission = ANY(revision.permission_keys)
                  )
                  AND (
                      context.pairing_grant_id IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM memoriesql.pairing_grant_revisions AS grant_revision
                          WHERE grant_revision.tenant_id = context.tenant_id
                            AND grant_revision.pairing_grant_id = context.pairing_grant_id
                            AND grant_revision.revision = context.pairing_grant_revision
                            AND requested_access_scope_id =
                                ANY(grant_revision.allowed_access_scope_ids)
                      )
                  )
              )
              OR (
                  scope.mode = 'workspace'
                  AND (
                      context.pairing_grant_id IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM memoriesql.pairing_grant_revisions AS grant_revision
                          WHERE grant_revision.tenant_id = context.tenant_id
                            AND grant_revision.pairing_grant_id = context.pairing_grant_id
                            AND grant_revision.revision = context.pairing_grant_revision
                            AND requested_access_scope_id =
                                ANY(grant_revision.allowed_access_scope_ids)
                      )
                  )
              )
          )
    )
$$;

CREATE FUNCTION memoriesql.current_context_scope_authorized(
    requested_access_scope_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT memoriesql.current_context_has_capability(requested_capability)
       AND memoriesql.current_context_scope_permits(
           requested_access_scope_id,
           requested_permission
       )
$$;

CREATE FUNCTION memoriesql.resource_read_authorized(
    requested_access_scope_id uuid,
    requested_resource_kind text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT memoriesql.current_context_scope_authorized(
        requested_access_scope_id,
        CASE requested_resource_kind
            WHEN 'source' THEN 'source.read'
            WHEN 'model' THEN 'subject_model.read'
            WHEN 'file' THEN 'file.read'
            WHEN 'render' THEN 'file.read'
            ELSE 'denied.unknown_resource_kind'
        END,
        'read'
    )
$$;

CREATE FUNCTION memoriesql.current_context_source_authorized(
    requested_access_scope_id uuid,
    requested_source_object_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT memoriesql.current_context_scope_authorized(
               requested_access_scope_id,
               requested_capability,
               requested_permission
           )
       AND EXISTS (
            SELECT 1
            FROM memoriesql.current_authorization_context() AS context
            JOIN memoriesql.protected_resources AS resource
              ON resource.tenant_id = context.tenant_id
             AND resource.workspace_id = context.workspace_id
             AND resource.access_scope_id = requested_access_scope_id
             AND resource.resource_kind = 'source'
             AND resource.resource_id = requested_source_object_id
             AND resource.status = 'active'
       )
$$;

CREATE FUNCTION memoriesql.current_context_event_authorized(
    requested_access_scope_id uuid,
    requested_event_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.source_events AS event_record
          ON event_record.tenant_id = context.tenant_id
         AND event_record.workspace_id = context.workspace_id
         AND event_record.access_scope_id = requested_access_scope_id
         AND event_record.event_id = requested_event_id
        WHERE memoriesql.current_context_source_authorized(
            requested_access_scope_id,
            event_record.source_object_id,
            requested_capability,
            requested_permission
        )
    )
$$;

CREATE FUNCTION memoriesql.current_context_source_reference_authorized(
    requested_access_scope_id uuid,
    reference_kind text,
    reference_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT CASE
        WHEN reference_kind IS NULL AND reference_id IS NULL THEN true
        WHEN reference_kind IN ('source', 'source_object') THEN
            memoriesql.current_context_source_authorized(
                requested_access_scope_id,
                reference_id,
                requested_capability,
                requested_permission
            )
        WHEN reference_kind = 'source_event' THEN
            memoriesql.current_context_event_authorized(
                requested_access_scope_id,
                reference_id,
                requested_capability,
                requested_permission
            )
        WHEN reference_kind = 'source_unit' THEN EXISTS (
            SELECT 1
            FROM memoriesql.current_authorization_context() AS context
            JOIN memoriesql.source_units AS unit
              ON unit.tenant_id = context.tenant_id
             AND unit.workspace_id = context.workspace_id
             AND unit.access_scope_id = requested_access_scope_id
             AND unit.source_unit_id = reference_id
            WHERE memoriesql.current_context_event_authorized(
                requested_access_scope_id,
                unit.event_id,
                requested_capability,
                requested_permission
            )
        )
        ELSE true
    END
$$;

CREATE FUNCTION memoriesql.current_context_receipt_authorized(
    requested_idempotency_receipt_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.idempotency_receipts AS receipt
          ON receipt.tenant_id = context.tenant_id
         AND receipt.workspace_id = context.workspace_id
         AND receipt.idempotency_receipt_id = requested_idempotency_receipt_id
        WHERE memoriesql.current_context_scope_authorized(
                  receipt.access_scope_id,
                  requested_capability,
                  requested_permission
              )
          AND memoriesql.current_context_source_reference_authorized(
                  receipt.access_scope_id,
                  receipt.resource_kind,
                  receipt.resource_id,
                  requested_capability,
                  requested_permission
              )
          AND NOT EXISTS (
              SELECT 1
              FROM memoriesql.source_units AS unit
              WHERE unit.tenant_id = receipt.tenant_id
                AND unit.workspace_id = receipt.workspace_id
                AND unit.access_scope_id = receipt.access_scope_id
                AND unit.processing_receipt_id = receipt.idempotency_receipt_id
                AND NOT memoriesql.current_context_event_authorized(
                    unit.access_scope_id,
                    unit.event_id,
                    requested_capability,
                    requested_permission
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM memoriesql.semantic_task_receipts AS semantic_receipt
              WHERE semantic_receipt.tenant_id = receipt.tenant_id
                AND semantic_receipt.workspace_id = receipt.workspace_id
                AND semantic_receipt.access_scope_id = receipt.access_scope_id
                AND semantic_receipt.idempotency_receipt_id =
                    receipt.idempotency_receipt_id
                AND NOT memoriesql.current_context_event_authorized(
                    semantic_receipt.access_scope_id,
                    semantic_receipt.event_id,
                    requested_capability,
                    requested_permission
                )
          )
    )
$$;

CREATE FUNCTION memoriesql.current_context_outbox_authorized(
    requested_outbox_event_id uuid,
    requested_capability text,
    requested_permission text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        JOIN memoriesql.outbox_events AS outbox
          ON outbox.tenant_id = context.tenant_id
         AND outbox.workspace_id = context.workspace_id
         AND outbox.outbox_event_id = requested_outbox_event_id
        WHERE memoriesql.current_context_scope_authorized(
                  outbox.access_scope_id,
                  requested_capability,
                  requested_permission
              )
          AND memoriesql.current_context_source_reference_authorized(
                  outbox.access_scope_id,
                  outbox.aggregate_kind,
                  outbox.aggregate_id,
                  requested_capability,
                  requested_permission
              )
          AND memoriesql.current_context_receipt_authorized(
                  outbox.idempotency_receipt_id,
                  requested_capability,
                  requested_permission
              )
    )
$$;

CREATE FUNCTION memoriesql.begin_authorization_context(
    presented_secret_sha256 text,
    requested_workspace_id uuid
)
RETURNS TABLE (
    tenant_id uuid,
    workspace_id uuid,
    principal_id uuid,
    principal_kind text,
    user_id uuid,
    on_behalf_of_user_id uuid,
    pairing_grant_id uuid,
    membership_revision integer,
    pairing_grant_revision integer
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    credential_record memoriesql.authentication_credentials%ROWTYPE;
    principal_record memoriesql.principals%ROWTYPE;
    membership_record memoriesql.workspace_memberships%ROWTYPE;
    pairing_record memoriesql.principal_pairings%ROWTYPE;
    pairing_grant_record memoriesql.pairing_grants%ROWTYPE;
    latest_pairing_grant memoriesql.pairing_grant_revisions%ROWTYPE;
    resolved_on_behalf_of uuid;
    resolved_pairing_grant_id uuid;
    resolved_pairing_grant_revision integer;
    context_expiry timestamp with time zone;
    new_context_id uuid;
BEGIN
    IF presented_secret_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
    END IF;

    SELECT credential.*
      INTO credential_record
      FROM memoriesql.authentication_credentials AS credential
     WHERE credential.secret_sha256 = presented_secret_sha256
       AND credential.status = 'active'
       AND credential.expires_at > statement_timestamp();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
    END IF;

    SELECT principal.*
      INTO STRICT principal_record
      FROM memoriesql.principals AS principal
     WHERE principal.tenant_id = credential_record.tenant_id
       AND principal.principal_id = credential_record.principal_id
       AND principal.status = 'active';

    SELECT membership.*
      INTO membership_record
      FROM memoriesql.workspace_memberships AS membership
      JOIN memoriesql.workspaces AS workspace_record
        ON workspace_record.tenant_id = membership.tenant_id
       AND workspace_record.workspace_id = membership.workspace_id
       AND workspace_record.status = 'active'
     WHERE membership.tenant_id = credential_record.tenant_id
       AND membership.workspace_id = requested_workspace_id
       AND membership.principal_id = credential_record.principal_id
       AND membership.status = 'active';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
    END IF;

    context_expiry := credential_record.expires_at;
    IF credential_record.credential_kind = 'local_session' THEN
        IF principal_record.principal_kind <> 'human' OR NOT EXISTS (
            SELECT 1
            FROM memoriesql.local_auth_sessions AS session_record
            JOIN memoriesql.auth_identities AS identity
              ON identity.tenant_id = session_record.tenant_id
             AND identity.identity_id = session_record.identity_id
             AND identity.status = 'active'
            JOIN memoriesql.users AS user_record
              ON user_record.tenant_id = identity.tenant_id
             AND user_record.user_id = identity.user_id
             AND user_record.status = 'active'
            WHERE session_record.tenant_id = credential_record.tenant_id
              AND session_record.credential_id = credential_record.credential_id
              AND identity.user_id = principal_record.user_id
        ) THEN
            RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
        END IF;
    ELSE
        IF principal_record.principal_kind = 'human' THEN
            RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
        END IF;
        SELECT pairing.*
          INTO pairing_record
          FROM memoriesql.principal_pairings AS pairing
         WHERE pairing.tenant_id = credential_record.tenant_id
           AND pairing.credential_id = credential_record.credential_id
           AND pairing.principal_id = credential_record.principal_id
           AND pairing.workspace_id = requested_workspace_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
        END IF;
        SELECT pairing_grant.*
          INTO pairing_grant_record
          FROM memoriesql.pairing_grants AS pairing_grant
          JOIN memoriesql.users AS on_behalf_user
            ON on_behalf_user.tenant_id = pairing_grant.tenant_id
           AND on_behalf_user.user_id = pairing_grant.on_behalf_of_user_id
           AND on_behalf_user.status = 'active'
         WHERE pairing_grant.tenant_id = pairing_record.tenant_id
           AND pairing_grant.workspace_id = pairing_record.workspace_id
           AND pairing_grant.pairing_grant_id = pairing_record.pairing_grant_id
           AND pairing_grant.paired_principal_id = pairing_record.principal_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
        END IF;
        SELECT revision.*
          INTO latest_pairing_grant
          FROM memoriesql.pairing_grant_revisions AS revision
         WHERE revision.tenant_id = pairing_grant_record.tenant_id
           AND revision.pairing_grant_id = pairing_grant_record.pairing_grant_id
         ORDER BY revision.revision DESC
         LIMIT 1;
        IF NOT FOUND
           OR latest_pairing_grant.status <> 'active'
           OR latest_pairing_grant.expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'invalid authentication context' USING ERRCODE = '28000';
        END IF;
        resolved_on_behalf_of := pairing_grant_record.on_behalf_of_user_id;
        resolved_pairing_grant_id := pairing_grant_record.pairing_grant_id;
        resolved_pairing_grant_revision := latest_pairing_grant.revision;
        context_expiry := least(context_expiry, latest_pairing_grant.expires_at);
    END IF;

    DELETE FROM memoriesql.authorization_contexts AS old_context
     WHERE old_context.backend_pid = pg_backend_pid()
        OR old_context.expires_at <= statement_timestamp()
        OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_stat_activity AS activity
            WHERE activity.pid = old_context.backend_pid
        );
    new_context_id := pg_catalog.uuidv7();
    INSERT INTO memoriesql.authorization_contexts (
        context_id,
        backend_pid,
        transaction_id,
        tenant_id,
        workspace_id,
        credential_id,
        principal_id,
        principal_kind,
        user_id,
        on_behalf_of_user_id,
        pairing_grant_id,
        membership_revision,
        pairing_grant_revision,
        expires_at,
        created_at
    ) VALUES (
        new_context_id,
        pg_backend_pid(),
        txid_current(),
        credential_record.tenant_id,
        requested_workspace_id,
        credential_record.credential_id,
        principal_record.principal_id,
        principal_record.principal_kind,
        principal_record.user_id,
        resolved_on_behalf_of,
        resolved_pairing_grant_id,
        membership_record.revision,
        resolved_pairing_grant_revision,
        context_expiry,
        statement_timestamp()
    );
    PERFORM set_config(
        'memoriesql.authorization_context_id',
        new_context_id::text,
        true
    );

    RETURN QUERY
    SELECT
        credential_record.tenant_id,
        requested_workspace_id,
        principal_record.principal_id,
        principal_record.principal_kind,
        principal_record.user_id,
        resolved_on_behalf_of,
        resolved_pairing_grant_id,
        membership_record.revision,
        resolved_pairing_grant_revision;
END;
$$;

CREATE FUNCTION memoriesql.bootstrap_personal_local(
    new_tenant_id uuid,
    new_user_id uuid,
    new_identity_id uuid,
    new_principal_id uuid,
    new_workspace_id uuid,
    new_access_scope_id uuid,
    new_policy_revision_id uuid,
    new_credential_id uuid,
    new_session_id uuid,
    identity_issuer text,
    identity_subject text,
    user_display_name text,
    session_secret_sha256 text,
    issued_at timestamp with time zone,
    expires_at timestamp with time zone
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(7431886601115481014);
    IF EXISTS (
        SELECT 1 FROM memoriesql.workspaces WHERE status = 'active'
    ) THEN
        RAISE EXCEPTION 'personal-local authority is already bootstrapped'
            USING ERRCODE = '55000';
    END IF;
    IF session_secret_sha256 !~ '^[0-9a-f]{64}$' OR expires_at <= issued_at THEN
        RAISE EXCEPTION 'invalid local session credential' USING ERRCODE = '23514';
    END IF;

    INSERT INTO memoriesql.users VALUES (
        new_tenant_id, new_user_id, 'active', user_display_name, issued_at, NULL
    );
    INSERT INTO memoriesql.auth_identities VALUES (
        new_tenant_id, new_identity_id, new_user_id, identity_issuer,
        identity_subject, 'local_interactive', 'active', issued_at, NULL
    );
    INSERT INTO memoriesql.principals VALUES (
        new_tenant_id, new_principal_id, 'human', new_user_id, new_user_id,
        'active', issued_at, NULL
    );
    INSERT INTO memoriesql.workspaces VALUES (
        new_tenant_id, new_workspace_id, 'personal_local', new_user_id,
        'active', issued_at
    );
    INSERT INTO memoriesql.workspace_memberships VALUES (
        new_tenant_id, new_workspace_id, new_principal_id, 'personal_owner',
        'active', 1, issued_at, issued_at, NULL
    );
    INSERT INTO memoriesql.access_scopes VALUES (
        new_tenant_id, new_workspace_id, new_access_scope_id, new_user_id,
        'owner_private', new_policy_revision_id, 'active', issued_at
    );
    INSERT INTO memoriesql.access_policy_revisions VALUES (
        new_tenant_id, new_workspace_id, new_access_scope_id,
        new_policy_revision_id, 1, 'owner_private', new_user_id,
        'personal-local owner-private default', new_principal_id, issued_at
    );
    INSERT INTO memoriesql.authentication_credentials VALUES (
        new_tenant_id, new_credential_id, new_principal_id, 'local_session',
        session_secret_sha256, 'active', issued_at, expires_at, NULL
    );
    INSERT INTO memoriesql.local_auth_sessions VALUES (
        new_tenant_id, new_session_id, new_credential_id, new_identity_id, issued_at
    );
END;
$$;

CREATE FUNCTION memoriesql.create_access_scope(
    new_access_scope_id uuid,
    new_policy_revision_id uuid,
    new_mode text,
    reason text,
    recorded_at timestamp with time zone
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    IF context_record.principal_kind <> 'human'
       OR context_record.user_id IS NULL
       OR NOT memoriesql.current_context_has_capability('workspace.manage')
       OR new_mode NOT IN ('owner_private', 'explicit', 'workspace') THEN
        RAISE EXCEPTION 'access scope creation is not authorized'
            USING ERRCODE = '42501';
    END IF;
    INSERT INTO memoriesql.access_scopes VALUES (
        context_record.tenant_id,
        context_record.workspace_id,
        new_access_scope_id,
        context_record.user_id,
        new_mode,
        new_policy_revision_id,
        'active',
        recorded_at
    );
    INSERT INTO memoriesql.access_policy_revisions VALUES (
        context_record.tenant_id,
        context_record.workspace_id,
        new_access_scope_id,
        new_policy_revision_id,
        1,
        new_mode,
        context_record.user_id,
        reason,
        context_record.principal_id,
        recorded_at
    );
END;
$$;

CREATE FUNCTION memoriesql.pair_local_client(
    new_principal_id uuid,
    new_pairing_id uuid,
    new_pairing_grant_id uuid,
    new_credential_id uuid,
    new_principal_kind text,
    new_role_key text,
    allowed_capabilities text[],
    allowed_access_scope_ids uuid[],
    client_secret_sha256 text,
    issued_at timestamp with time zone,
    expires_at timestamp with time zone
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    IF context_record.principal_kind <> 'human'
       OR context_record.user_id IS NULL
       OR NOT memoriesql.current_context_has_capability('client.pair') THEN
        RAISE EXCEPTION 'client pairing is not authorized' USING ERRCODE = '42501';
    END IF;
    IF (new_principal_kind, new_role_key) NOT IN (
        ('agent', 'paired_agent'),
        ('service', 'background_service'),
        ('device', 'paired_device')
    ) THEN
        RAISE EXCEPTION 'pairing principal kind and role do not match'
            USING ERRCODE = '23514';
    END IF;
    IF client_secret_sha256 !~ '^[0-9a-f]{64}$' OR expires_at <= issued_at THEN
        RAISE EXCEPTION 'invalid paired-client credential' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM unnest(allowed_capabilities) AS requested(capability_key)
        LEFT JOIN memoriesql.role_capabilities AS role_capability
          ON role_capability.role_key = new_role_key
         AND role_capability.capability_key = requested.capability_key
        WHERE role_capability.capability_key IS NULL
    ) THEN
        RAISE EXCEPTION 'pairing capability exceeds the paired role'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO memoriesql.principals VALUES (
        context_record.tenant_id, new_principal_id, new_principal_kind,
        NULL, context_record.user_id, 'active', issued_at, NULL
    );
    INSERT INTO memoriesql.workspace_memberships VALUES (
        context_record.tenant_id, context_record.workspace_id, new_principal_id,
        new_role_key, 'active', 1, issued_at, issued_at, NULL
    );
    INSERT INTO memoriesql.pairing_grants VALUES (
        context_record.tenant_id, context_record.workspace_id, new_pairing_grant_id,
        new_principal_id, context_record.user_id, context_record.principal_id,
        issued_at
    );
    INSERT INTO memoriesql.pairing_grant_revisions VALUES (
        context_record.tenant_id, context_record.workspace_id, new_pairing_grant_id,
        1, allowed_capabilities, allowed_access_scope_ids, 'active', issued_at,
        expires_at, NULL, context_record.principal_id, issued_at
    );
    INSERT INTO memoriesql.authentication_credentials VALUES (
        context_record.tenant_id, new_credential_id, new_principal_id,
        'paired_client', client_secret_sha256, 'active', issued_at, expires_at, NULL
    );
    INSERT INTO memoriesql.principal_pairings VALUES (
        context_record.tenant_id, new_pairing_id, new_credential_id,
        new_principal_id, context_record.workspace_id, new_pairing_grant_id,
        context_record.principal_id, issued_at
    );
END;
$$;

CREATE FUNCTION memoriesql.create_access_grant(
    new_grant_id uuid,
    target_principal_id uuid,
    requested_access_scope_id uuid,
    permission_keys text[],
    valid_from timestamp with time zone,
    expires_at timestamp with time zone
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    IF context_record.principal_kind <> 'human'
       OR NOT memoriesql.current_context_has_capability('source.share')
       OR NOT EXISTS (
           SELECT 1
           FROM memoriesql.access_scopes AS scope
           WHERE scope.tenant_id = context_record.tenant_id
             AND scope.workspace_id = context_record.workspace_id
             AND scope.access_scope_id = requested_access_scope_id
             AND scope.status = 'active'
             AND scope.mode = 'explicit'
             AND (
                 scope.owner_user_id = context_record.user_id
                 OR (
                     memoriesql.current_context_scope_permits(
                         requested_access_scope_id, 'share'
                     )
                     AND NOT EXISTS (
                         SELECT 1
                         FROM unnest(permission_keys) AS requested(permission_key)
                         WHERE NOT memoriesql.current_context_scope_permits(
                             requested_access_scope_id,
                             requested.permission_key
                         )
                     )
                 )
             )
       ) THEN
        RAISE EXCEPTION 'grant creation is not authorized' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM memoriesql.workspace_memberships AS membership
        WHERE membership.tenant_id = context_record.tenant_id
          AND membership.workspace_id = context_record.workspace_id
          AND membership.principal_id = target_principal_id
          AND membership.status = 'active'
    ) THEN
        RAISE EXCEPTION 'grant target is not an active workspace principal'
            USING ERRCODE = '23503';
    END IF;
    INSERT INTO memoriesql.access_grants VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_access_scope_id, new_grant_id, target_principal_id,
        context_record.principal_id, valid_from
    );
    INSERT INTO memoriesql.access_grant_revisions VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_access_scope_id, new_grant_id, 1, permission_keys, 'active',
        valid_from, expires_at, NULL, context_record.principal_id, valid_from
    );
END;
$$;

CREATE FUNCTION memoriesql.revise_access_grant(
    requested_grant_id uuid,
    expected_revision integer,
    permission_keys text[],
    new_status text,
    valid_from timestamp with time zone,
    expires_at timestamp with time zone,
    recorded_at timestamp with time zone
)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    grant_record memoriesql.access_grants%ROWTYPE;
    current_revision integer;
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    SELECT grant_root.* INTO STRICT grant_record
    FROM memoriesql.access_grants AS grant_root
    WHERE grant_root.tenant_id = context_record.tenant_id
      AND grant_root.workspace_id = context_record.workspace_id
      AND grant_root.grant_id = requested_grant_id
    FOR UPDATE;
    IF context_record.principal_kind <> 'human'
       OR NOT memoriesql.current_context_has_capability('source.share')
       OR NOT EXISTS (
           SELECT 1
           FROM memoriesql.access_scopes AS scope
           WHERE scope.tenant_id = context_record.tenant_id
             AND scope.workspace_id = context_record.workspace_id
             AND scope.access_scope_id = grant_record.access_scope_id
             AND scope.status = 'active'
             AND scope.mode = 'explicit'
             AND (
                 scope.owner_user_id = context_record.user_id
                 OR (
                     memoriesql.current_context_scope_permits(
                         grant_record.access_scope_id, 'share'
                     )
                     AND (
                         new_status = 'revoked'
                         OR NOT EXISTS (
                             SELECT 1
                             FROM unnest(permission_keys)
                                  AS requested(permission_key)
                             WHERE NOT memoriesql.current_context_scope_permits(
                                 grant_record.access_scope_id,
                                 requested.permission_key
                             )
                         )
                     )
                 )
             )
       ) THEN
        RAISE EXCEPTION 'grant revision is not authorized' USING ERRCODE = '42501';
    END IF;
    SELECT max(revision) INTO current_revision
    FROM memoriesql.access_grant_revisions
    WHERE tenant_id = context_record.tenant_id
      AND grant_id = requested_grant_id;
    IF current_revision <> expected_revision THEN
        RAISE EXCEPTION 'stale access grant revision' USING ERRCODE = '40001';
    END IF;
    INSERT INTO memoriesql.access_grant_revisions VALUES (
        context_record.tenant_id, context_record.workspace_id,
        grant_record.access_scope_id, requested_grant_id, current_revision + 1,
        permission_keys, new_status, valid_from, expires_at,
        CASE WHEN new_status = 'revoked' THEN recorded_at ELSE NULL END,
        context_record.principal_id, recorded_at
    );
    RETURN current_revision + 1;
END;
$$;

CREATE FUNCTION memoriesql.revise_pairing_grant(
    requested_pairing_grant_id uuid,
    expected_revision integer,
    allowed_capabilities text[],
    allowed_access_scope_ids uuid[],
    new_status text,
    issued_at timestamp with time zone,
    expires_at timestamp with time zone,
    recorded_at timestamp with time zone
)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    pairing_grant_record memoriesql.pairing_grants%ROWTYPE;
    current_revision integer;
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    SELECT pairing_grant_root.* INTO STRICT pairing_grant_record
    FROM memoriesql.pairing_grants AS pairing_grant_root
    WHERE pairing_grant_root.tenant_id = context_record.tenant_id
      AND pairing_grant_root.workspace_id = context_record.workspace_id
      AND pairing_grant_root.pairing_grant_id = requested_pairing_grant_id
    FOR UPDATE;
    IF context_record.principal_kind <> 'human'
       OR context_record.user_id IS DISTINCT FROM pairing_grant_record.on_behalf_of_user_id
       OR NOT memoriesql.current_context_has_capability('client.pair') THEN
        RAISE EXCEPTION 'pairing-grant revision is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT max(revision) INTO current_revision
    FROM memoriesql.pairing_grant_revisions
    WHERE tenant_id = context_record.tenant_id
      AND pairing_grant_id = requested_pairing_grant_id;
    IF current_revision <> expected_revision THEN
        RAISE EXCEPTION 'stale pairing-grant revision' USING ERRCODE = '40001';
    END IF;
    INSERT INTO memoriesql.pairing_grant_revisions VALUES (
        context_record.tenant_id, context_record.workspace_id,
        requested_pairing_grant_id, current_revision + 1, allowed_capabilities,
        allowed_access_scope_ids, new_status, issued_at, expires_at,
        CASE WHEN new_status = 'revoked' THEN recorded_at ELSE NULL END,
        context_record.principal_id, recorded_at
    );
    RETURN current_revision + 1;
END;
$$;

CREATE FUNCTION memoriesql.application_authorize_resource(
    requested_resource_kind text,
    requested_resource_id uuid,
    requested_capability text,
    requested_permission text,
    request_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record memoriesql.authorization_contexts%ROWTYPE;
    resource_record memoriesql.protected_resources%ROWTYPE;
    allowed boolean := false;
    policy_revision_id uuid;
BEGIN
    SELECT context.* INTO STRICT context_record
    FROM memoriesql.current_authorization_context() AS context;
    SELECT resource.* INTO resource_record
    FROM memoriesql.protected_resources AS resource
    WHERE resource.tenant_id = context_record.tenant_id
      AND resource.workspace_id = context_record.workspace_id
      AND resource.resource_kind = requested_resource_kind
      AND resource.resource_id = requested_resource_id
      AND resource.status = 'active'
      AND (
          resource.resource_kind <> 'file'
          OR EXISTS (
              SELECT 1
              FROM memoriesql.file_objects AS file_object
              WHERE file_object.tenant_id = resource.tenant_id
                AND file_object.workspace_id = resource.workspace_id
                AND file_object.access_scope_id = resource.access_scope_id
                AND file_object.file_id = resource.resource_id
                AND file_object.status = 'active'
          )
      );
    IF FOUND THEN
        allowed := memoriesql.current_context_scope_authorized(
            resource_record.access_scope_id,
            requested_capability,
            requested_permission
        );
        SELECT scope.current_policy_revision_id INTO policy_revision_id
        FROM memoriesql.access_scopes AS scope
        WHERE scope.tenant_id = resource_record.tenant_id
          AND scope.access_scope_id = resource_record.access_scope_id;
    END IF;
    INSERT INTO memoriesql.authorization_audit_events (
        tenant_id,
        authorization_audit_event_id,
        request_id,
        authenticated_principal_id,
        principal_kind,
        on_behalf_of_user_id,
        pairing_grant_id,
        workspace_id,
        access_scope_id,
        resource_kind,
        resource_id,
        capability_key,
        permission_key,
        policy_revision_id,
        decision,
        decision_reason,
        recorded_at
    ) VALUES (
        context_record.tenant_id,
        pg_catalog.uuidv7(),
        request_id,
        context_record.principal_id,
        context_record.principal_kind,
        context_record.on_behalf_of_user_id,
        context_record.pairing_grant_id,
        context_record.workspace_id,
        CASE WHEN allowed THEN resource_record.access_scope_id ELSE NULL END,
        requested_resource_kind,
        requested_resource_id,
        requested_capability,
        requested_permission,
        CASE WHEN allowed THEN policy_revision_id ELSE NULL END,
        CASE WHEN allowed THEN 'allowed' ELSE 'denied' END,
        CASE WHEN allowed THEN 'current_policy_allowed' ELSE 'resource_unavailable' END,
        statement_timestamp()
    );
    RETURN allowed;
END;
$$;

CREATE POLICY protected_resources_read
ON memoriesql.protected_resources
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    status = 'active'
    AND memoriesql.resource_read_authorized(access_scope_id, resource_kind)
);

CREATE POLICY file_objects_read
ON memoriesql.file_objects
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    status = 'active'
    AND memoriesql.current_context_scope_authorized(
        access_scope_id, 'file.read', 'read'
    )
    AND EXISTS (
        SELECT 1
        FROM memoriesql.protected_resources AS resource
        WHERE resource.tenant_id = file_objects.tenant_id
          AND resource.workspace_id = file_objects.workspace_id
          AND resource.access_scope_id = file_objects.access_scope_id
          AND resource.resource_kind = 'file'
          AND resource.resource_id = file_objects.file_id
          AND resource.status = 'active'
    )
);

CREATE POLICY source_objects_read
ON memoriesql.source_objects
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.read', 'read'
    )
);
CREATE POLICY source_objects_write
ON memoriesql.source_objects
FOR INSERT TO memoriesql_application
WITH CHECK (
    memoriesql.current_context_source_authorized(
        access_scope_id, source_object_id, 'source.manage', 'write'
    )
);

CREATE POLICY source_events_read
ON memoriesql.source_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY source_units_read
ON memoriesql.source_units
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY conversation_turns_read
ON memoriesql.conversation_turns
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY document_revisions_read
ON memoriesql.document_revisions
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY document_segments_read
ON memoriesql.document_segments
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY media_segments_read
ON memoriesql.media_segments
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY record_events_read
ON memoriesql.record_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY beads_read
ON memoriesql.beads
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'source.read', 'read'));
CREATE POLICY idempotency_receipts_read
ON memoriesql.idempotency_receipts
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_receipt_authorized(
        idempotency_receipt_id, 'memory.maintain', 'read'
    )
);
CREATE POLICY semantic_task_receipts_read
ON memoriesql.semantic_task_receipts
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (memoriesql.current_context_event_authorized(access_scope_id, event_id, 'memory.maintain', 'read'));
CREATE POLICY outbox_events_read
ON memoriesql.outbox_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    memoriesql.current_context_outbox_authorized(
        outbox_event_id, 'memory.maintain', 'read'
    )
);
CREATE POLICY authorization_audit_events_read
ON memoriesql.authorization_audit_events
FOR SELECT TO memoriesql_application
USING (
    EXISTS (
        SELECT 1
        FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = authorization_audit_events.tenant_id
          AND context.workspace_id = authorization_audit_events.workspace_id
          AND (
              context.principal_id =
                  authorization_audit_events.authenticated_principal_id
              OR (
                  context.user_id IS NOT NULL
                  AND context.user_id =
                      authorization_audit_events.on_behalf_of_user_id
              )
              OR (
                  authorization_audit_events.access_scope_id IS NOT NULL
                  AND memoriesql.current_context_scope_permits(
                      authorization_audit_events.access_scope_id,
                      'read'
                  )
              )
          )
    )
    AND memoriesql.current_context_has_capability('audit.read')
);

SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE memoriesql.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.users FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.auth_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.auth_identities FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.principals FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.workspace_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.workspace_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_scopes FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_policy_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_policy_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_grant_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.access_grant_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.pairing_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.pairing_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.pairing_grant_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.pairing_grant_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.authentication_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.authentication_credentials FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.local_auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.local_auth_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.principal_pairings ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.principal_pairings FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.authorization_contexts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.authorization_contexts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.authorization_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.authorization_audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.protected_resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.protected_resources FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.file_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.file_objects FORCE ROW LEVEL SECURITY;

ALTER TABLE memoriesql.source_objects FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_units FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.conversation_turns FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.document_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.document_segments FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.media_segments FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.record_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.beads FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.idempotency_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.semantic_task_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.outbox_events FORCE ROW LEVEL SECURITY;

DO $product_role_ownership$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS namespace
        JOIN pg_catalog.pg_roles AS owner_role
          ON owner_role.oid = namespace.nspowner
        WHERE namespace.nspname = 'memoriesql'
          AND owner_role.rolname IN (
              'memoriesql_application', 'memoriesql_worker'
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_roles AS owner_role
          ON owner_role.oid = relation.relowner
        WHERE namespace.nspname = 'memoriesql'
          AND owner_role.rolname IN (
              'memoriesql_application', 'memoriesql_worker'
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        JOIN pg_catalog.pg_roles AS owner_role
          ON owner_role.oid = procedure.proowner
        WHERE namespace.nspname = 'memoriesql'
          AND owner_role.rolname IN (
              'memoriesql_application', 'memoriesql_worker'
          )
    ) THEN
        RAISE EXCEPTION
            'memorieSQL product roles must not own schema authority';
    END IF;
END;
$product_role_ownership$;

REVOKE ALL ON ALL TABLES IN SCHEMA memoriesql FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memoriesql FROM PUBLIC;
GRANT USAGE ON SCHEMA memoriesql TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.protected_resources TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.file_objects TO memoriesql_application, memoriesql_worker;
GRANT SELECT, INSERT ON memoriesql.source_objects TO memoriesql_application;
GRANT SELECT ON memoriesql.source_objects TO memoriesql_worker;
GRANT SELECT ON memoriesql.source_events TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.source_units TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.conversation_turns TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.document_revisions TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.document_segments TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.media_segments TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.record_events TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.beads TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.idempotency_receipts TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.semantic_task_receipts TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.outbox_events TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.source_event_cardinality TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.thin_observations TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.observation_timeline TO memoriesql_application, memoriesql_worker;
GRANT SELECT ON memoriesql.authorization_audit_events TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.begin_authorization_context(text, uuid)
    TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.application_authorize_resource(
    text, uuid, text, text, uuid
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.bootstrap_personal_local(
    uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid,
    text, text, text, text, timestamp with time zone, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.create_access_scope(
    uuid, uuid, text, text, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.pair_local_client(
    uuid, uuid, uuid, uuid, text, text, text[], uuid[], text,
    timestamp with time zone, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.create_access_grant(
    uuid, uuid, uuid, text[], timestamp with time zone, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.revise_access_grant(
    uuid, integer, text[], text, timestamp with time zone,
    timestamp with time zone, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.revise_pairing_grant(
    uuid, integer, text[], uuid[], text, timestamp with time zone,
    timestamp with time zone, timestamp with time zone
) TO memoriesql_application;
GRANT EXECUTE ON FUNCTION memoriesql.current_authorization_context()
    TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_has_capability(text)
    TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_scope_permits(uuid, text)
    TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_scope_authorized(uuid, text, text)
    TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.resource_read_authorized(uuid, text)
    TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_source_authorized(
    uuid, uuid, text, text
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_event_authorized(
    uuid, uuid, text, text
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_receipt_authorized(
    uuid, text, text
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.current_context_outbox_authorized(
    uuid, text, text
) TO memoriesql_application, memoriesql_worker;

COMMENT ON TABLE memoriesql.protected_resources IS
    'One default-deny scope binding for source, model, file, and render surfaces.';
COMMENT ON TABLE memoriesql.file_objects IS
    'Authenticated metadata with an opaque locator; no storage credential is represented.';
COMMENT ON FUNCTION memoriesql.begin_authorization_context(text, uuid) IS
    'Resolves transaction-local authority from an opaque authenticated credential and current database policy.';
