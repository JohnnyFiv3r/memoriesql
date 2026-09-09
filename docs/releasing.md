# Owner-controlled runtime release readiness

This lane prepares the converged N1/N2 provider-neutral runtime and the cancellation
correction for `0.0.2`. It does not authorize a merge, tag, upload, or external
configuration change. The published catalog-only `0.0.1a1` and its artifacts remain
immutable. The historical inventory is retained in
`docs/verification/package-artifact-inventory.json`.

PR #5 merged as `599df3d1c5b0feccbd467d1697614137f4120fda`; reviewed head
`747f14fed37856bfef3520ba635147dd68ec855d` is ancestral to that public main baseline.
A read-only check of the [PyPI project JSON](https://pypi.org/pypi/memoriesql/json)
on 2026-09-09 UTC found only `0.0.1a1`, with two published files, and no `0.0.2`.
Availability must be checked again immediately before any release. An empty or
absent version today is not a reservation; never reuse a published version.

Distribution versions follow the owner-approved plain numeric `X.Y.Z` convention,
with matching `vX.Y.Z` tags. Do not add alpha/beta/rc suffixes unless separately
requested. Numeric naming does not imply production readiness: the `0.x` line
remains experimental under the [compatibility policy](architecture/compatibility.md).
The previously prepared prerelease candidate was never published and must not be
released as an intermediate version. Build genuine `0.0.2` archives; do not rename
archives with different embedded metadata.

## Exact release identity

| Setting | Value |
| --- | --- |
| PyPI project | `memoriesql` |
| GitHub repository | `JohnnyFiv3r/memoriesql` (ID `1357510758`) |
| Workflow | `.github/workflows/publish-pypi.yml` |
| Environment | `pypi` |
| Only accepted new tag | `v0.0.2` |
| Package version | `0.0.2` |
| Committed release inventory | `docs/verification/runtime-package-artifact-inventory.json` |

The existing Trusted Publisher identity uses the same repository, workflow filename,
and environment. This lane requires no change to that identity and does not inspect
or modify PyPI account settings. The owner must verify that identity before release;
there is no token fallback and no new publisher registration in this PR.

## Artifact and approval controls

- Only creation of `v0.0.2` can trigger the publishing workflow. Historical tags,
  wildcard tags, manual dispatch, pull requests and branch pushes cannot trigger it.
- The tag commit must equal current main, the package version must equal `0.0.2`,
  and the latest exact-head `python-package.yml` **main push** run must have completed
  successfully. A PR check proves readiness, not eligibility to publish a merge.
- Download only `memoriesql-python-<exact SHA>` from that selected run ID. Verify the
  two filenames, version, sizes and SHA-256 hashes against the committed release
  inventory. A downloaded inventory cannot override the committed hashes. Extra,
  missing, altered or symlinked distribution files fail closed.
- The verify job stages only those two archives. The publish job downloads those
  staged bytes, uses pinned actions and OIDC, and executes no repository code.
  Only the protected publish job has `id-token: write`. There is no publish-time
  rebuild, dependency resolution, compatibility rerun, or `skip-existing` fallback.
- The `pypi` environment must retain required owner approval and disabled
  administrator bypass. Automation must not approve its own upload. The existing
  self-review allowance lets the sole owner approve a release they initiated.

Release preparation may use the existing deterministic build to prepare candidate
hashes and record the inventory. Fresh hosted CI artifacts from the final exact
head must independently match that committed inventory before readiness is claimed.
Release documentation under `docs/` and verification inventories are excluded
from the distributions,
so recording these hashes does not introduce a self-referential build. If packaged
bytes change during review, obtain new hosted artifacts and requalify the final
head; never substitute old local build output. The later owner merge still needs
its own successful exact-main push run and retained artifacts with matching hashes.

## Python and integration requirements

The runtime requires `>=3.13,<3.15`; Python 3.13 and 3.14 are qualified using installed
wheels and independently rebuilt sdists, with checkout access denied. Python
3.11/3.12 can install the older catalog-only `0.0.1a1` with an exact version pin;
that is not a runtime install. An unpinned request may fail rather than fall back
because pip excludes prereleases by default when a numeric release is present.
The older-Python CI fallback test explicitly allows prereleases with `--pre` and
separately proves that the `0.0.2` runtime is rejected.
After authorized publication, pin `memoriesql==0.0.2` when requesting this runtime
and verify the installed version. Preview payload IDs/versions, all 49 payloads,
twelve preview APIs and fourteen SQL migration resources remain unchanged.

Integrators must handle the experimental `cleanup_pending` worker receipt. The
foreground cancellation wait defaults to five seconds on a responsive event loop;
it does not guarantee callback, database-thread or process termination. Retain the
worker and its event loop, do not start replacement work to bypass its quarantine,
and observe `await worker.wait_for_cleanup()` for the eventual receipt or error.
Cancelling that observer does not abandon owned cleanup. Cleanup retention spans
normal settlement as well as cancellation drains and started run-event writes.
A callback can keep cleanup pending indefinitely; ordinary event-loop shutdown
is not a durable cleanup handoff. There is no second scheduler or service bootstrap.

Late output has no semantic write authority, but late usage remains accountable
against its original intent. A cancellation receipt is not proof of reconciled
usage; a failed late ledger write remains observable. Caller cancellation during
a started normal outcome write is not proof of transaction rollback. Failed or
indeterminate settlement must not be presented as settled; the existing PostgreSQL
lease/reaper and authorization/fencing rules remain authoritative. See
[the full owned-cleanup contract](runtime.md#cancellation-cleanup-ownership).

## Separate owner actions

1. Review and merge this release-readiness PR only after final exact-head CI and
   review closure. This task leaves it open and unmerged.
2. **External environment change:** the read-only 2026-09-09 check found that `pypi`
   permits only the tag `v0.0.1a1`. Replace that exact tag allowance with `v0.0.2`;
   do not allow branches or wildcards. Preserve the required owner reviewer,
   disabled administrator bypass, and existing self-review policy. This change
   has not been performed. Existing immutable historical tags must not be modified.
3. Recheck PyPI availability, existing Trusted Publisher identity, environment
   protection, current main, its latest successful main-push CI and artifact
   retention. Compare both artifacts with the committed runtime inventory. If
   `0.0.2` has been published, stop and prepare a separately reviewed new version.
4. Separately authorize release, then create `v0.0.2` once at that exact main
   commit. Do not move, delete, recreate or retarget published tags.
5. Inspect the selected run, SHA and both artifact hashes before approving the
   waiting `pypi` job. That approval permits the upload; this PR does not.
6. After upload, verify PyPI filenames/hashes and fresh exact-version installation
   on Python 3.13/3.14. Passing preparation checks is not proof of publication.

If main advances, CI is missing/pending/failed, artifacts expire, hashes differ,
or external protection is not correct, stop. Do not fall back to an earlier head,
rebuild inside publication, or weaken the gate. Private product adoption/N3,
providers, owner data and CP execution remain outside this release-preparation lane.
