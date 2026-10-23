-- Pairing-grant revision takes the outcome-authority fence before the grant.
--
-- revise_pairing_grant (migration 0006) locked the grant's row FOR UPDATE and
-- then inserted the revision, whose trigger waits for the tenant's
-- outcome-authority fence exclusively. Outcome settlement holds that fence
-- shared through its writes, and its last write to the task rechecks the
-- task's foreign key to its origin pairing grant: a FOR KEY SHARE lock on the
-- very row the revocation held. Each waited for the other, and PostgreSQL ended
-- one of them with SQLSTATE 40P01. Before migration 0040, a revocation's
-- context start waited on any stale context row that the settlement's own
-- context start was removing, which hid the cycle whenever there was one.
--
-- The kernel function below is migration 0006's text with one statement added:
-- it takes the fence, as revoke_exact_source_v1 (migration 0036) does, before
-- locking the grant. A revocation still has only the fence's two serial
-- positions: before a settlement's final authorization check, or after its
-- queue transition commits. Its signature, owner, grants, checks, refusals and
-- returned revision are unchanged. Forward-only; migrations 0001-0047 keep
-- their bytes.

CREATE OR REPLACE FUNCTION memoriesql.revise_pairing_grant(
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
    -- Take the outcome-authority fence before this grant's row. Outcome
    -- settlement holds the fence shared and then key-share locks this row when
    -- it rechecks its task's origin grant; holding the row while waiting for
    -- the fence closed that cycle.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            context_record.tenant_id::text || ':semantic_outcome_authority:',
            0
        )
    );
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
