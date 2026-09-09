CREATE TABLE memoriesql.users (
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    status text NOT NULL,
    display_name text,
    created_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT users_pk PRIMARY KEY (tenant_id, user_id),
    CONSTRAINT users_status_supported
        CHECK (status IN ('active', 'disabled', 'revoked')),
    CONSTRAINT users_display_name_nonempty
        CHECK (display_name IS NULL OR btrim(display_name) <> ''),
    CONSTRAINT users_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status <> 'revoked' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.auth_identities (
    tenant_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    user_id uuid NOT NULL,
    issuer text NOT NULL,
    subject text NOT NULL,
    assurance text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT auth_identities_pk PRIMARY KEY (tenant_id, identity_id),
    CONSTRAINT auth_identities_subject_uq UNIQUE (issuer, subject),
    CONSTRAINT auth_identities_user_fk FOREIGN KEY (tenant_id, user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT auth_identities_issuer_nonempty CHECK (btrim(issuer) <> ''),
    CONSTRAINT auth_identities_subject_nonempty CHECK (btrim(subject) <> ''),
    CONSTRAINT auth_identities_assurance_supported
        CHECK (assurance IN ('local_interactive', 'local_recovery')),
    CONSTRAINT auth_identities_status_supported
        CHECK (status IN ('active', 'disabled', 'revoked')),
    CONSTRAINT auth_identities_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status <> 'revoked' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.principals (
    tenant_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    principal_kind text NOT NULL,
    user_id uuid,
    owner_user_id uuid,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT principals_pk PRIMARY KEY (tenant_id, principal_id),
    CONSTRAINT principals_kind_supported
        CHECK (principal_kind IN ('human', 'agent', 'service', 'device')),
    CONSTRAINT principals_status_supported
        CHECK (status IN ('active', 'disabled', 'revoked')),
    CONSTRAINT principals_user_fk FOREIGN KEY (tenant_id, user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT principals_owner_fk FOREIGN KEY (tenant_id, owner_user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT principals_kind_identity CHECK (
        (
            principal_kind = 'human'
            AND user_id IS NOT NULL
            AND owner_user_id = user_id
        )
        OR (
            principal_kind IN ('agent', 'service', 'device')
            AND user_id IS NULL
            AND owner_user_id IS NOT NULL
        )
    ),
    CONSTRAINT principals_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status <> 'revoked' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.workspaces (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    authority_mode text,
    owner_user_id uuid,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT workspaces_pk PRIMARY KEY (tenant_id, workspace_id),
    CONSTRAINT workspaces_owner_fk FOREIGN KEY (tenant_id, owner_user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT workspaces_status_supported
        CHECK (status IN ('active', 'suspended', 'legacy_unclaimed')),
    CONSTRAINT workspaces_personal_local_shape CHECK (
        (
            status = 'legacy_unclaimed'
            AND authority_mode IS NULL
            AND owner_user_id IS NULL
        )
        OR (
            status IN ('active', 'suspended')
            AND authority_mode = 'personal_local'
            AND owner_user_id IS NOT NULL
        )
    )
);

CREATE TABLE memoriesql.capabilities (
    capability_key text PRIMARY KEY,
    description text NOT NULL,
    CONSTRAINT capabilities_key_nonempty CHECK (btrim(capability_key) <> ''),
    CONSTRAINT capabilities_description_nonempty CHECK (btrim(description) <> '')
);

CREATE TABLE memoriesql.authorization_roles (
    role_key text PRIMARY KEY,
    description text NOT NULL,
    CONSTRAINT authorization_roles_key_nonempty CHECK (btrim(role_key) <> ''),
    CONSTRAINT authorization_roles_description_nonempty
        CHECK (btrim(description) <> '')
);

CREATE TABLE memoriesql.role_capabilities (
    role_key text NOT NULL,
    capability_key text NOT NULL,
    CONSTRAINT role_capabilities_pk PRIMARY KEY (role_key, capability_key),
    CONSTRAINT role_capabilities_role_fk FOREIGN KEY (role_key)
        REFERENCES memoriesql.authorization_roles (role_key),
    CONSTRAINT role_capabilities_capability_fk FOREIGN KEY (capability_key)
        REFERENCES memoriesql.capabilities (capability_key)
);

INSERT INTO memoriesql.capabilities (capability_key, description) VALUES
    ('client.pair', 'Pair and bound a local non-human principal.'),
    ('agent.run', 'Run an authorized agent operation.'),
    ('audit.read', 'Read privacy-safe authorization audit metadata.'),
    ('file.delete', 'Delete an authorized file object.'),
    ('file.export', 'Export an authorized file object.'),
    ('file.read', 'Read authorized file-object metadata or bytes.'),
    ('memory.capture', 'Attempt a canonical capture mutation.'),
    ('memory.inspect', 'Inspect authorized evidence and provenance.'),
    ('memory.maintain', 'Perform bounded structural maintenance.'),
    ('memory.query', 'Query authorized memory evidence.'),
    ('source.cite', 'Cite an authorized source.'),
    ('source.manage', 'Manage source identity and lifecycle metadata.'),
    ('source.query', 'Search authorized source evidence.'),
    ('source.read', 'Read authorized source evidence.'),
    ('source.share', 'Create or revise a source access grant.'),
    ('subject_model.delete', 'Delete an authorized subject-model resource.'),
    ('subject_model.export', 'Export an authorized subject-model resource.'),
    ('subject_model.propose', 'Propose a governed subject-model change.'),
    ('subject_model.read', 'Read an authorized subject-model resource.'),
    ('subject_model.review', 'Review a governed subject-model proposal.'),
    ('workspace.manage', 'Manage personal-local workspace membership and policy.'),
    ('workspace.read', 'Read non-content workspace metadata.');

INSERT INTO memoriesql.authorization_roles (role_key, description) VALUES
    ('background_service', 'Paired service with pairing-grant-intersected authority.'),
    ('paired_agent', 'Paired agent with pairing-grant-intersected authority.'),
    ('paired_device', 'Paired device with pairing-grant-intersected authority.'),
    ('personal_owner', 'Authenticated owner of one personal-local workspace.'),
    ('workspace_administrator', 'Workspace mechanics without private-content bypass.');

INSERT INTO memoriesql.role_capabilities (role_key, capability_key)
SELECT 'personal_owner', capability_key
FROM memoriesql.capabilities;

INSERT INTO memoriesql.role_capabilities (role_key, capability_key) VALUES
    ('workspace_administrator', 'audit.read'),
    ('workspace_administrator', 'file.delete'),
    ('workspace_administrator', 'file.export'),
    ('workspace_administrator', 'file.read'),
    ('workspace_administrator', 'source.manage'),
    ('workspace_administrator', 'source.query'),
    ('workspace_administrator', 'source.read'),
    ('workspace_administrator', 'source.share'),
    ('workspace_administrator', 'subject_model.read'),
    ('workspace_administrator', 'workspace.manage'),
    ('workspace_administrator', 'workspace.read'),
    ('paired_agent', 'agent.run'),
    ('paired_agent', 'file.read'),
    ('paired_agent', 'memory.inspect'),
    ('paired_agent', 'memory.query'),
    ('paired_agent', 'source.cite'),
    ('paired_agent', 'source.query'),
    ('paired_agent', 'source.read'),
    ('paired_agent', 'subject_model.read'),
    ('paired_agent', 'workspace.read'),
    ('background_service', 'file.read'),
    ('background_service', 'memory.inspect'),
    ('background_service', 'memory.maintain'),
    ('background_service', 'memory.query'),
    ('background_service', 'source.query'),
    ('background_service', 'source.read'),
    ('background_service', 'subject_model.read'),
    ('background_service', 'workspace.read'),
    ('paired_device', 'file.read'),
    ('paired_device', 'memory.capture'),
    ('paired_device', 'source.read'),
    ('paired_device', 'workspace.read');

CREATE TABLE memoriesql.workspace_memberships (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    role_key text NOT NULL,
    status text NOT NULL,
    revision integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT workspace_memberships_pk
        PRIMARY KEY (tenant_id, workspace_id, principal_id),
    CONSTRAINT workspace_memberships_workspace_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    CONSTRAINT workspace_memberships_principal_fk
        FOREIGN KEY (tenant_id, principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT workspace_memberships_role_fk FOREIGN KEY (role_key)
        REFERENCES memoriesql.authorization_roles (role_key),
    CONSTRAINT workspace_memberships_status_supported
        CHECK (status IN ('active', 'suspended', 'revoked')),
    CONSTRAINT workspace_memberships_revision_positive CHECK (revision > 0),
    CONSTRAINT workspace_memberships_time_order CHECK (updated_at >= created_at),
    CONSTRAINT workspace_memberships_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status <> 'revoked' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.access_scopes (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    owner_user_id uuid,
    mode text,
    current_policy_revision_id uuid,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT access_scopes_pk PRIMARY KEY (tenant_id, access_scope_id),
    CONSTRAINT access_scopes_workspace_uq
        UNIQUE (tenant_id, workspace_id, access_scope_id),
    CONSTRAINT access_scopes_workspace_fk FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    CONSTRAINT access_scopes_owner_fk FOREIGN KEY (tenant_id, owner_user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT access_scopes_mode_supported CHECK (
        mode IS NULL OR mode IN ('owner_private', 'explicit', 'workspace')
    ),
    CONSTRAINT access_scopes_status_supported
        CHECK (status IN ('active', 'revoked', 'legacy_unclaimed')),
    CONSTRAINT access_scopes_shape CHECK (
        (
            status = 'legacy_unclaimed'
            AND owner_user_id IS NULL
            AND mode IS NULL
            AND current_policy_revision_id IS NULL
        )
        OR (
            status IN ('active', 'revoked')
            AND mode IS NOT NULL
            AND current_policy_revision_id IS NOT NULL
            AND (mode <> 'owner_private' OR owner_user_id IS NOT NULL)
        )
    )
);

CREATE TABLE memoriesql.access_policy_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    policy_revision_id uuid NOT NULL,
    revision integer NOT NULL,
    mode text NOT NULL,
    owner_user_id uuid,
    reason text NOT NULL,
    actor_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT access_policy_revisions_pk
        PRIMARY KEY (tenant_id, policy_revision_id),
    CONSTRAINT access_policy_revisions_scope_revision_uq
        UNIQUE (tenant_id, access_scope_id, revision),
    CONSTRAINT access_policy_revisions_scope_identity_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        policy_revision_id
    ),
    CONSTRAINT access_policy_revisions_scope_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id
    ) REFERENCES memoriesql.access_scopes (
        tenant_id,
        workspace_id,
        access_scope_id
    ) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT access_policy_revisions_owner_fk
        FOREIGN KEY (tenant_id, owner_user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT access_policy_revisions_actor_fk
        FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT access_policy_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT access_policy_revisions_mode_supported
        CHECK (mode IN ('owner_private', 'explicit', 'workspace')),
    CONSTRAINT access_policy_revisions_owner_private CHECK (
        mode <> 'owner_private' OR owner_user_id IS NOT NULL
    ),
    CONSTRAINT access_policy_revisions_reason_nonempty CHECK (btrim(reason) <> '')
);

ALTER TABLE memoriesql.access_scopes
ADD CONSTRAINT access_scopes_current_policy_fk FOREIGN KEY (
    tenant_id,
    workspace_id,
    access_scope_id,
    current_policy_revision_id
) REFERENCES memoriesql.access_policy_revisions (
    tenant_id,
    workspace_id,
    access_scope_id,
    policy_revision_id
) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE memoriesql.access_grants (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    target_principal_id uuid NOT NULL,
    granted_by_principal_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT access_grants_pk PRIMARY KEY (tenant_id, grant_id),
    CONSTRAINT access_grants_scope_identity_uq UNIQUE (
        tenant_id,
        workspace_id,
        access_scope_id,
        grant_id
    ),
    CONSTRAINT access_grants_scope_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id
    ) REFERENCES memoriesql.access_scopes (
        tenant_id,
        workspace_id,
        access_scope_id
    ),
    CONSTRAINT access_grants_target_fk
        FOREIGN KEY (tenant_id, target_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT access_grants_actor_fk
        FOREIGN KEY (tenant_id, granted_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE TABLE memoriesql.access_grant_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    revision integer NOT NULL,
    permission_keys text[] NOT NULL,
    status text NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    actor_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT access_grant_revisions_pk
        PRIMARY KEY (tenant_id, grant_id, revision),
    CONSTRAINT access_grant_revisions_grant_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        access_scope_id,
        grant_id
    ) REFERENCES memoriesql.access_grants (
        tenant_id,
        workspace_id,
        access_scope_id,
        grant_id
    ),
    CONSTRAINT access_grant_revisions_actor_fk
        FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT access_grant_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT access_grant_revisions_permissions_nonempty CHECK (
        cardinality(permission_keys) > 0
        AND array_position(permission_keys, NULL) IS NULL
        AND permission_keys <@ ARRAY['read', 'write', 'share', 'export', 'delete']::text[]
    ),
    CONSTRAINT access_grant_revisions_status_supported
        CHECK (status IN ('active', 'revoked')),
    CONSTRAINT access_grant_revisions_expiry_order
        CHECK (expires_at IS NULL OR expires_at > valid_from),
    CONSTRAINT access_grant_revisions_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status = 'active' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.pairing_grants (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    pairing_grant_id uuid NOT NULL,
    paired_principal_id uuid NOT NULL,
    on_behalf_of_user_id uuid NOT NULL,
    issued_by_principal_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT pairing_grants_pk PRIMARY KEY (tenant_id, pairing_grant_id),
    CONSTRAINT pairing_grants_workspace_identity_uq
        UNIQUE (tenant_id, workspace_id, pairing_grant_id),
    CONSTRAINT pairing_grants_workspace_fk FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    CONSTRAINT pairing_grants_principal_fk
        FOREIGN KEY (tenant_id, paired_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT pairing_grants_user_fk
        FOREIGN KEY (tenant_id, on_behalf_of_user_id)
        REFERENCES memoriesql.users (tenant_id, user_id),
    CONSTRAINT pairing_grants_issuer_fk
        FOREIGN KEY (tenant_id, issued_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE TABLE memoriesql.pairing_grant_revisions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    pairing_grant_id uuid NOT NULL,
    revision integer NOT NULL,
    allowed_capabilities text[] NOT NULL,
    allowed_access_scope_ids uuid[] NOT NULL,
    status text NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    actor_principal_id uuid NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT pairing_grant_revisions_pk
        PRIMARY KEY (tenant_id, pairing_grant_id, revision),
    CONSTRAINT pairing_grant_revisions_pairing_grant_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        pairing_grant_id
    ) REFERENCES memoriesql.pairing_grants (
        tenant_id,
        workspace_id,
        pairing_grant_id
    ),
    CONSTRAINT pairing_grant_revisions_actor_fk
        FOREIGN KEY (tenant_id, actor_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT pairing_grant_revisions_revision_positive CHECK (revision > 0),
    CONSTRAINT pairing_grant_revisions_capabilities_nonempty CHECK (
        cardinality(allowed_capabilities) > 0
        AND array_position(allowed_capabilities, NULL) IS NULL
    ),
    CONSTRAINT pairing_grant_revisions_scopes_nonempty CHECK (
        cardinality(allowed_access_scope_ids) > 0
        AND array_position(allowed_access_scope_ids, NULL) IS NULL
    ),
    CONSTRAINT pairing_grant_revisions_status_supported
        CHECK (status IN ('active', 'revoked')),
    CONSTRAINT pairing_grant_revisions_expiry_order CHECK (expires_at > issued_at),
    CONSTRAINT pairing_grant_revisions_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status = 'active' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.authentication_credentials (
    tenant_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    credential_kind text NOT NULL,
    secret_sha256 text NOT NULL,
    status text NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT authentication_credentials_pk
        PRIMARY KEY (tenant_id, credential_id),
    CONSTRAINT authentication_credentials_secret_uq UNIQUE (secret_sha256),
    CONSTRAINT authentication_credentials_principal_fk
        FOREIGN KEY (tenant_id, principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT authentication_credentials_kind_supported
        CHECK (credential_kind IN ('local_session', 'paired_client')),
    CONSTRAINT authentication_credentials_sha256_format
        CHECK (secret_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT authentication_credentials_status_supported
        CHECK (status IN ('active', 'revoked')),
    CONSTRAINT authentication_credentials_expiry_order CHECK (expires_at > issued_at),
    CONSTRAINT authentication_credentials_revocation_state CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status = 'active' AND revoked_at IS NULL)
    )
);

CREATE TABLE memoriesql.local_auth_sessions (
    tenant_id uuid NOT NULL,
    session_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT local_auth_sessions_pk PRIMARY KEY (tenant_id, session_id),
    CONSTRAINT local_auth_sessions_credential_uq UNIQUE (tenant_id, credential_id),
    CONSTRAINT local_auth_sessions_credential_fk
        FOREIGN KEY (tenant_id, credential_id)
        REFERENCES memoriesql.authentication_credentials (tenant_id, credential_id),
    CONSTRAINT local_auth_sessions_identity_fk
        FOREIGN KEY (tenant_id, identity_id)
        REFERENCES memoriesql.auth_identities (tenant_id, identity_id)
);

CREATE TABLE memoriesql.principal_pairings (
    tenant_id uuid NOT NULL,
    pairing_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    pairing_grant_id uuid NOT NULL,
    paired_by_principal_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT principal_pairings_pk PRIMARY KEY (tenant_id, pairing_id),
    CONSTRAINT principal_pairings_credential_uq UNIQUE (tenant_id, credential_id),
    CONSTRAINT principal_pairings_principal_uq
        UNIQUE (tenant_id, workspace_id, principal_id),
    CONSTRAINT principal_pairings_credential_fk
        FOREIGN KEY (tenant_id, credential_id)
        REFERENCES memoriesql.authentication_credentials (tenant_id, credential_id),
    CONSTRAINT principal_pairings_principal_fk
        FOREIGN KEY (tenant_id, principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT principal_pairings_pairing_grant_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        pairing_grant_id
    ) REFERENCES memoriesql.pairing_grants (
        tenant_id,
        workspace_id,
        pairing_grant_id
    ),
    CONSTRAINT principal_pairings_actor_fk
        FOREIGN KEY (tenant_id, paired_by_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id)
);

CREATE TABLE memoriesql.authorization_contexts (
    context_id uuid PRIMARY KEY,
    backend_pid integer NOT NULL,
    transaction_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    principal_kind text NOT NULL,
    user_id uuid,
    on_behalf_of_user_id uuid,
    pairing_grant_id uuid,
    membership_revision integer NOT NULL,
    pairing_grant_revision integer,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT authorization_contexts_credential_fk
        FOREIGN KEY (tenant_id, credential_id)
        REFERENCES memoriesql.authentication_credentials (tenant_id, credential_id),
    CONSTRAINT authorization_contexts_membership_fk FOREIGN KEY (
        tenant_id,
        workspace_id,
        principal_id
    ) REFERENCES memoriesql.workspace_memberships (
        tenant_id,
        workspace_id,
        principal_id
    ),
    CONSTRAINT authorization_contexts_principal_kind_supported
        CHECK (principal_kind IN ('human', 'agent', 'service', 'device')),
    CONSTRAINT authorization_contexts_membership_revision_positive
        CHECK (membership_revision > 0),
    CONSTRAINT authorization_contexts_pairing_grant_revision_positive
        CHECK (pairing_grant_revision IS NULL OR pairing_grant_revision > 0),
    CONSTRAINT authorization_contexts_pairing_grant_shape CHECK (
        (pairing_grant_id IS NULL AND pairing_grant_revision IS NULL)
        OR (pairing_grant_id IS NOT NULL AND pairing_grant_revision IS NOT NULL)
    )
);

CREATE INDEX authorization_contexts_backend_transaction_idx
ON memoriesql.authorization_contexts (backend_pid, transaction_id, context_id);

CREATE TABLE memoriesql.authorization_audit_events (
    tenant_id uuid NOT NULL,
    authorization_audit_event_id uuid NOT NULL,
    request_id uuid NOT NULL,
    authenticated_principal_id uuid NOT NULL,
    principal_kind text NOT NULL,
    on_behalf_of_user_id uuid,
    pairing_grant_id uuid,
    workspace_id uuid NOT NULL,
    access_scope_id uuid,
    resource_kind text NOT NULL,
    resource_id uuid NOT NULL,
    capability_key text NOT NULL,
    permission_key text NOT NULL,
    policy_revision_id uuid,
    decision text NOT NULL,
    decision_reason text NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT authorization_audit_events_pk
        PRIMARY KEY (tenant_id, authorization_audit_event_id),
    CONSTRAINT authorization_audit_events_principal_fk
        FOREIGN KEY (tenant_id, authenticated_principal_id)
        REFERENCES memoriesql.principals (tenant_id, principal_id),
    CONSTRAINT authorization_audit_events_workspace_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES memoriesql.workspaces (tenant_id, workspace_id),
    CONSTRAINT authorization_audit_events_kind_supported
        CHECK (principal_kind IN ('human', 'agent', 'service', 'device')),
    CONSTRAINT authorization_audit_events_resource_kind_supported
        CHECK (resource_kind IN ('source', 'model', 'file', 'render')),
    CONSTRAINT authorization_audit_events_permission_supported
        CHECK (permission_key IN ('read', 'write', 'share', 'export', 'delete')),
    CONSTRAINT authorization_audit_events_decision_supported
        CHECK (decision IN ('allowed', 'denied')),
    CONSTRAINT authorization_audit_events_reason_nonempty
        CHECK (btrim(decision_reason) <> '')
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
    tenant_id,
    workspace_id,
    authority_mode,
    owner_user_id,
    status,
    created_at
)
SELECT
    tenant_id,
    workspace_id,
    NULL,
    NULL,
    'legacy_unclaimed',
    transaction_timestamp()
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
    tenant_id,
    workspace_id,
    access_scope_id,
    owner_user_id,
    mode,
    current_policy_revision_id,
    status,
    created_at
)
SELECT
    tenant_id,
    workspace_id,
    access_scope_id,
    NULL,
    NULL,
    NULL,
    'legacy_unclaimed',
    transaction_timestamp()
FROM observed_scopes
ON CONFLICT (tenant_id, access_scope_id) DO NOTHING;

CREATE TRIGGER access_policy_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.access_policy_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER access_grants_immutable
BEFORE UPDATE OR DELETE ON memoriesql.access_grants
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER access_grant_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.access_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER pairing_grants_immutable
BEFORE UPDATE OR DELETE ON memoriesql.pairing_grants
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER pairing_grant_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.pairing_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
CREATE TRIGGER authorization_audit_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.authorization_audit_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();

SET CONSTRAINTS ALL IMMEDIATE;

COMMENT ON TABLE memoriesql.access_scopes IS
    'Default-deny owner-private, explicit, or workspace content authority; legacy rows remain unclaimed and unreadable.';
COMMENT ON TABLE memoriesql.pairing_grant_revisions IS
    'Append-only direct personal-local pairing-grant revisions; generic delegation and organization policy are deferred.';
COMMENT ON TABLE memoriesql.authentication_credentials IS
    'Hashes of local session or paired-client secrets; loopback origin is never an identity.';
