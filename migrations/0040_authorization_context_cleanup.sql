-- Authorization context cleanup that never waits on another session.
--
-- begin_authorization_context (migration 0006) removed every stale context row
-- with one DELETE and kept those row locks until its transaction ended. A long
-- transaction, such as a relation read or a query's source frame, therefore
-- made every other session's context start wait on the rows it was removing.
-- A caller with a 500 ms lock timeout failed with SQLSTATE 55P03, and a caller
-- without one waited for the holder's whole transaction. Under snapshot
-- isolation the same DELETE could instead fail with 40001 when another session
-- removed a row after the transaction's snapshot.
--
-- A context row is usable only by the backend and transaction that began it
-- (current_authorization_context checks both). So only this transaction's own
-- earlier context must be removed at once; everything else is garbage that any
-- later caller may collect, skipping rows that are locked.
--
-- The kernel function below is migration 0006's text with that one statement
-- replaced. Its signature, owner, grants, comment, credential checks, refusals
-- and returned row are unchanged. Forward-only; migrations 0001-0039 keep their
-- bytes.

-- Private to the kernel: removes the context rows nothing can use again (this
-- backend's, expired ones and those of ended backends), skipping any row that
-- another session has locked. Called before the caller's new context exists.
CREATE FUNCTION memoriesql.collect_authorization_contexts_v1() RETURNS void
LANGUAGE sql VOLATILE
SET search_path = pg_catalog, memoriesql
AS $$
    WITH collectable AS MATERIALIZED (
        SELECT stale_context.context_id
          FROM memoriesql.authorization_contexts AS stale_context
         WHERE stale_context.backend_pid = pg_backend_pid()
            OR stale_context.expires_at <= statement_timestamp()
            OR NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_stat_activity AS activity
                WHERE activity.pid = stale_context.backend_pid
            )
           FOR UPDATE OF stale_context SKIP LOCKED
    )
    DELETE FROM memoriesql.authorization_contexts AS old_context
     USING collectable
     WHERE old_context.context_id = collectable.context_id
$$;
REVOKE ALL ON FUNCTION memoriesql.collect_authorization_contexts_v1() FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.begin_authorization_context(
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

    -- A second context in one transaction revokes the first. Those rows were
    -- written by this transaction, so no other session can see or lock them.
    DELETE FROM memoriesql.authorization_contexts AS own_context
     WHERE own_context.backend_pid = pg_backend_pid()
       AND own_context.transaction_id = txid_current();
    -- Every other removable row is garbage: a context is usable only in the
    -- backend and transaction that began it. Collecting it never waits.
    IF current_setting('transaction_isolation', true)
       IN ('read committed', 'read uncommitted') THEN
        PERFORM memoriesql.collect_authorization_contexts_v1();
    ELSE
        -- Under snapshot isolation a row that another session removed after
        -- this transaction's snapshot fails the collection. That says nothing
        -- about this caller: undo the attempt and leave it to a later start.
        BEGIN
            PERFORM memoriesql.collect_authorization_contexts_v1();
        EXCEPTION WHEN serialization_failure THEN
            NULL;
        END;
    END IF;
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
