-- Terminal revocation of local client pairing grants.
--
-- revise_pairing_grant (migration 0006) appends the next revision of a pairing
-- grant with whatever status its caller supplies. So an owner holding
-- client.pair could append an active revision after a revoked one. A
-- revocation leaves the paired client's principal, credential and source
-- grants in place, and authentication reads only the grant's latest revision,
-- so that revision made the client's old secret authenticate again. The
-- pairing contract and the CLI call revocation terminal, and no product command
-- reactivates a grant.
--
-- From this migration on, a pairing grant that has a revoked revision accepts
-- no further revision, whatever status is asked for. Every revision is inserted
-- through the trigger below: pair_local_client's first revision and every
-- revise_pairing_grant call. Authentication already refuses a grant whose
-- latest revision is revoked, so a revoked client's secret never authenticates
-- again.
--
-- A grant reactivated by hand before this migration would keep its active
-- revision. Installation is refused while any such grant exists; revoke it
-- first with `clients revoke`, then install.
--
-- Forward-only; migrations 0001-0041 keep their bytes.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM memoriesql.pairing_grant_revisions AS revoked
        WHERE revoked.status = 'revoked'
          AND (
              SELECT latest.status
              FROM memoriesql.pairing_grant_revisions AS latest
              WHERE latest.tenant_id = revoked.tenant_id
                AND latest.pairing_grant_id = revoked.pairing_grant_id
              ORDER BY latest.revision DESC
              LIMIT 1
          ) = 'active'
    ) THEN
        RAISE EXCEPTION 'a pairing grant was reactivated after its revocation; revoke it first'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

-- Refuses any revision of a grant that has been revoked. Runs with its
-- caller's rights inside the owner-only functions that insert revisions.
CREATE FUNCTION memoriesql.reject_pairing_grant_revival() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM memoriesql.pairing_grant_revisions AS revision
        WHERE revision.tenant_id = NEW.tenant_id
          AND revision.pairing_grant_id = NEW.pairing_grant_id
          AND revision.status = 'revoked'
    ) THEN
        RAISE EXCEPTION 'a revoked pairing grant accepts no further revision'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION memoriesql.reject_pairing_grant_revival() FROM PUBLIC;

CREATE TRIGGER pairing_grant_revisions_terminal
BEFORE INSERT ON memoriesql.pairing_grant_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_pairing_grant_revival();
