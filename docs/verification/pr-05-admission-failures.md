# PR-05 preserved qualification failures

## Development environment boundary false positive

The first public-boundary verifier invocation failed because the development
virtual environment was inside the worktree. The verifier recursively examined
ignored third-party packages and rejected their upstream links and environment
paths. The approval-record commit itself included only the three content-free
gate records. The environment was moved outside the worktree, preserving the
verifier unchanged; the source-only boundary check is rerun. This is a tooling
preparation failure, not runtime qualification evidence.

Native qualification, first run: 10 cases, two errors. SQLGlot 30.19.0 represents IS NOT NULL using Is.negate; the closed admission list omitted that flag. Added its closed boolean flag, preserving native IS NOT NULL semantics. The other failure was a fictional test using an invented accepted_at column; the approved observations field is recorded_at. Corrected the fixture, not the approved catalog. Neither failure was a measured useful-workload or semantic-quality result.

## Independent effective privilege qualification

The initial four authority cases all refused the fixture: stock PostgreSQL grants PUBLIC write access to pg_catalog.pg_settings. The fixture now explicitly revokes that write grant; the qualifier was not weakened. The subsequent run correctly refused a trusted-session identity probe using ungranted current_setting. It now uses session_user/current_user and the wire-reported server version, with no added callable grant. All four authority cases then passed. Policy-definition drift, procedure drift and downward SET ROLE are separately refused.

## Pending settings enforcement gate

On PostgreSQL 18.4, direct SET work_mem remains usable by the restricted real login after REVOKE SET ON PARAMETER work_mem FROM PUBLIC. The observed value was 8MB. This is preserved as an enforcement limitation, not a passing settings fence. The proposed qualified interpretation requires the sole trusted executor to enforce settings, closed SELECT admission, revocation of callable set_config and pg_settings writes, no exposed credentials/general SQL client and independent supervision. The owner has been asked about the material security amendment; dependent query execution remains gated until exact owner approval and matching custody reconciliation, or a separately qualified server fence. Approved blobs and their records remain unchanged.

The added sequence-privilege preflight initially raised WrongObjectType because PostgreSQL reordered an AND predicate ahead of its relkind guard. A CASE expression now gates the type-specific privilege function. All 15 focused native cases and 13 unit cases passed after this correction; the USERSET case records the limitation rather than claiming a fence.

Source convergence first run: 71 cases, one failure in the historical three-dependency metadata assertion after selecting the new SQLGlot pin. Updated that authored dependency allowlist and the package inspector's matching exact Requires-Dist list; no published contract or migration bytes changed. The complete source rerun passes 71 cases.

Artifact inspection initially rejected the new dependency notice against the historical two-line NOTICE constant. Updated the inspector's exact expected text to include the selected dependency's MIT attribution; archive-to-source byte equality remains enforced. Both normalized deterministic builds were byte-identical. The initial failed inspector output was empty and is not used as a qualification receipt.
