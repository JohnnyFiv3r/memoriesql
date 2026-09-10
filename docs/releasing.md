# Release controls and unreleased development

`0.0.4` was published from `b89dec8819a74aca6ae06f4116172f5c964731f7`.
Its existing tag, artifacts, provenance and committed release inventories remain
immutable. The readiness record below is historical; its pre-publication availability
check and pending owner actions do not describe the current release state.

The evidence-package/schema-16 development slice selects no future distribution
version. Metadata and CI archive filenames still use `0.0.4`, but these development
artifacts are not the published release. Ordinary branch/PR CI checks their new
contents, deterministic rebuilds and installed behavior without comparing them to
immutable published archive hashes. The separate publish workflow retains its exact
tag, committed release-hash and protected-approval gates; none is weakened or advanced.
A later separately reviewed release must select a new version and inventory. Never
replace the existing published inventory to make a development build appear released.
Product adoption remains gated on that later public release.

# Historical owner-controlled 0.0.4 release readiness

This lane promotes the reviewed `0.0.4` candidate into exact-version repository
release controls. It does not authorize a merge, tag, upload, consumer deployment,
or external configuration change. The runtime, SQL, contracts and dependencies are unchanged from reviewed PR #9 head
`64b89c35794e6aa9352159e2ed8c1aba2e0b9637`, merged as
`683724c9ac1bbdd9ea237395ad62a3aa08fc6276` with an identical tree. Updating the packaged repository README refreshes the
source archive only; the wheel remains identical.

Published `0.0.1a1`, `0.0.2` and `0.0.3` remain immutable. Their inventories are
retained as `package-artifact-inventory.json`,
`runtime-0.0.2-package-artifact-inventory.json` and
`runtime-0.0.3-package-artifact-inventory.json` under `docs/verification/`.
The last is a byte-for-byte snapshot of the published 0.0.3 inventory.
The reviewed 0.0.4 candidate inventory is also retained unchanged. The authoritative
`runtime-package-artifact-inventory.json` retains its wheel and records the refreshed
sdist whose only changed member is README.md release guidance.

A read-only check of [the PyPI version endpoint](https://pypi.org/pypi/memoriesql/0.0.4/json)
on 2026-09-10 returned HTTP 404. This is not a reservation. Recheck immediately
before release. Versions follow plain numeric `X.Y.Z` with `vX.Y.Z` tags; no
alpha/beta/rc version or renamed archive is an intermediate release. The `0.x`
line remains experimental under the [compatibility policy](architecture/compatibility.md).

## Exact release identity

| Setting | Value |
| --- | --- |
| PyPI project | `memoriesql` |
| GitHub repository | `JohnnyFiv3r/memoriesql` (ID `1357510758`) |
| Workflow | `.github/workflows/publish-pypi.yml` |
| Environment | `pypi` |
| Only accepted new tag | `v0.0.4` |
| Package version | `0.0.4` |
| Committed release inventory | `docs/verification/runtime-package-artifact-inventory.json` |

The existing Trusted Publisher must retain this repository, workflow filename and
environment. Public [0.0.3 wheel provenance](https://pypi.org/integrity/memoriesql/0.0.3/memoriesql-0.0.3-py3-none-any.whl/provenance)
identifies that publisher historically. It does not verify the current authenticated
PyPI account configuration. The owner must check that configuration before release;
there is no token fallback or new publisher registration in this preparation.

## Artifact and approval controls

- Only creation of `v0.0.4` can trigger publishing. Historical tags, wildcard tags,
  manual dispatch, pull requests and branch pushes cannot trigger it.
- The tag commit must equal current main, the package version must equal `0.0.4`,
  and the latest exact-head `python-package.yml` **main push** run must have completed
  successfully. A PR check does not establish eligibility to publish its merge.
- Download only `memoriesql-python-<exact SHA>` from that selected run ID. Verify
  both filenames, version, sizes and SHA-256 hashes against the committed inventory.
  The downloaded inventory cannot override committed hashes. Extra, missing,
  altered or symlinked distribution files fail closed.
- The verify job stages only those two archives. The protected publish job downloads
  those bytes, uses pinned actions and OIDC, and executes no repository code. Only
  that job has `id-token: write`. There is no publication-time rebuild, dependency
  resolution, compatibility rerun, or `skip-existing` fallback.
- The `pypi` environment must retain owner approval and disabled administrator
  bypass. Automation must not approve its own upload. The existing self-review
  allowance permits the sole owner to approve a release they initiated.

Final exact-head hosted artifacts must independently match the committed inventory.
Documentation under `docs/`, workflow controls and their tests are excluded from
the distributions, but setuptools includes the repository README in the sdist.
Its release-tag guidance changes here, requiring a genuine source-archive refresh.
The wheel and all runtime inputs are unchanged, so existing installed-package
acceptance is reused without a local full convergence rerun. Hosted CI verifies
the final PR head and independently rebuilt sdist wheel. If packaged bytes change
further, refresh artifacts and their evidence. The later
owner merge needs its own successful exact-main push CI and retained matching bytes.

## Schema 15 upgrade and consumer opt-in

The runtime requires Python `>=3.13,<3.15`. Python 3.13 and 3.14 have installed-wheel
and independently rebuilt-sdist coverage with checkout access denied. Python
3.11/3.12 can use the catalog-only `0.0.1a1` with an exact pin; it is not a runtime
fallback. Older-Python CI separately rejects `0.0.4`. After separately authorized
publication, consumers requesting this version must pin `memoriesql==0.0.4`.

Schema 15 is a forward-only upgrade. Migrations 0001–0014 and all 49 published
preview payloads remain byte-for-byte unchanged; migration 0015 adds accepted-bead
seals and explicit correction lineage. Before an authorized deployment, the
consumer must deliberately plan its database upgrade and writer composition:

1. Historical bead versions, IDs, statements, reads and successful receipts survive.
   Existing receipt replay retains its original meaning under current authorization.
   Initial source-occurrence replay still returns the initial bead after corrections.
2. Accepted meaning, type and summary/render are immutable after upgrade. Legacy
   commands attempting another accepted semantic version fail explicitly with
   `accepted_bead_immutable`; they are never translated into corrections. Unaccepted
   initial work can still finish using the legacy registry.
3. Version-2 initial authorship and correction require explicit opt-in:
   `AcceptSourceEventV2Command` / `accept_source_event_v2()`,
   `load_observation_task_registry()`, and the version-2 initial/correction commands.
   Installing the package does not select that composition. Corrections create
   distinct beads with pinned supersession; independent branches and multi-target
   reconciliation remain valid, without selecting a global winner.
4. Use the guarded forward migration from schema 14 to 15 only after consumer
   readiness and deployment authorization. There is no historical reauthoring or
   down migration. Current authorization, evidence and attempt fences remain in force.

See [migration operations](migrations.md) and the complete
[contract/caller map](immutable-observations.md). This preparation does not execute
an upgrade, change consumer code, or authorize desktop adoption.

The 0.0.3 cleanup ownership contract also remains in force. A bounded cancellation
receipt is not proof of termination, rollback or reconciled usage. Retain a worker
reporting `cleanup_pending`, its event loop and quarantine, and observe
`await worker.wait_for_cleanup()` for eventual settlement/error. Late usage remains
accountable; failed or indeterminate settlement must remain visible. See the
[owned-cleanup contract](runtime.md#cancellation-cleanup-ownership).

## Separate owner actions

1. Review and merge this readiness PR after final exact-head CI and review closure.
   This task leaves it open and unmerged. Its base SHA is not the eventual release SHA.
2. **External environment action, separately authorized:** authenticated GitHub API
   reads on 2026-09-10 found only the exact tag `v0.0.3` allowed in `pypi`, required
   reviewer `JohnnyFiv3r`, administrator bypass disabled, and self-review allowed.
   Replace only that tag allowance with `v0.0.4`; retain those protection rules and
   reject branches/wildcards. This preparation has not changed the environment.
3. Recheck PyPI version availability, the authenticated Trusted Publisher identity,
   protection rules, current main, its latest successful main-push CI, and retained
   artifacts. Independently compare both archives with the committed inventory.
   If 0.0.4 already exists, stop for a separately reviewed version; never overwrite it.
4. Separately authorize publication, then create `v0.0.4` once at that verified main
   SHA. Never move, delete, recreate or retarget an existing published tag.
5. Inspect the selected CI run, SHA and both hashes before approving the waiting
   `pypi` job. That owner approval permits upload; this PR does not.
6. After upload, verify PyPI filenames/hashes, provenance and fresh exact-version
   installations on Python 3.13/3.14. Preparation CI is not proof of publication.

If main advances, CI is missing/pending/failed, artifacts expire, hashes differ or
protection is incorrect, stop. Do not fall back to an earlier head, rebuild during
publication, or weaken the gate. Product adoption, providers, owner data and
checkpoint execution require separate authorization.
