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

## Bounded broad review repairs

Review of 0af2171 found four issues. The catalog extraction had omitted fields containing digits: four required content_sha256 columns, including the second source_units key. The registry and generated metadata now match the byte-version identity; native same-unit/different-hash fixtures preserve both versions and join exact bytes. Bound text parameters now carry explicit C collation, verified by native pg_collation_for and parameter-only matching/sets. DISTINCT ON's actual SQLGlot on argument was already rejected by the closed argument list, but it now has an explicit shape refusal and negative case protecting uniqueness derivation. Phase checks reject illegal aggregate/window placement and nesting while native SUM(COUNT(*)) OVER remains useful and admitted.

The first repair check had a strict-mypy loop-variable narrowing failure and one unit assertion expecting a different identifier-quoting spelling; both were corrected. All 74 source cases, 12 native composition cases and unchanged 5 authority cases pass. These repairs change package bytes; the earlier a3ef572a wheel / 30befa8c sdist receipt is historical and cannot qualify the repaired head. The native fixture now explicitly sets UTC through trusted setup, eliminating the earlier inherited-zoneinfo warning without weakening the isolation guard.

## Installed convergence assertion repair

Exact-head CI run 36278007879 at 9fb7111 failed both Python 3.13 and 3.14 installed
convergence lane: the runtime-ownership case still expected 58 modules, while
the approved inventory contains 61 after adding the three internal SQL modules.
Its subsequent dependency assertion also omitted the selected SQLGlot pin.
Both expectations now name the current inventory and exact dependency list;
the independent per-module installed-distribution ownership check and frozen
canonical task/executor hashes are unchanged. Shards 1 and 3 passed (116 and
113 cases); shard 2 ran 158 cases with that one failure. This failed run remains
historical evidence and does not qualify the repaired head.
