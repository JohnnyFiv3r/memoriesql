# Immutable observations: candidate acceptance

Public base: `d4e409d4daa4285ce6868274e6bb55593aa209f1`.
This records local acceptance for the unreleased `0.0.4` candidate. Exact-head
hosted CI and review are required on the PR; this file is not a release approval.
The [contract/caller map](../immutable-observations.md) was written before implementation.

## Verified behavior

| Proof | Result |
| --- | --- |
| Initial authoring and occurrence replay | Version-2 initial writes succeed; receipt and natural-identity replay return the original bead after corrections. Cardinality remains one initial bead per source unit. |
| Accepted semantics | Database guards reject later versions, type/render updates and semantic/evidence/watermark appends. A correction without accepted semantics and explicit targets cannot commit. |
| Corrections and lineage | Same-evidence correction, two independently queued branches against one target, and reconciliation of both branches succeed. Each creates a new bead; no global winner is stored. |
| Fences and authority | Wrong generation/tenant/output contract, mismatched or unavailable target pins, wrong evidence hashes, cancellation, expired lease and revocation deny atomic writes. A controlled advisory-lock gate proves expiry is rechecked after blocking. Unrelated metadata maintenance does not invalidate the correction. |
| History and upgrade | A populated schema 14 with two legacy semantic versions upgrades to 15 with byte-identical JSON snapshots of historical records, evidence, watermarks, task receipts, idempotency receipts and outbox payloads. Both old successful semantic receipts and the old capture receipt replay. A later legacy update is explicitly denied. |
| Forced RLS | Seal and lineage tables force RLS; unauthenticated reads return no rows, application mutation is denied, and revoked source authority hides the accepted history. Lineage policies check both accepted endpoints. |
| Atomic settlement | A forced failure at final root-run settlement rolls back the new bead, lineage, receipt and task outcome. The original attempt remains owned/running and succeeds when retried after the fictional failure is removed. |
| Existing runtime | The complete installed suite retains cancellation/quarantine, cleanup lease, late accounting, capture/range/fold and executor behavior checks. Worker lifecycle and SQL 0001–0014 are unchanged. |
| Ownership and limits | 32 explicitly inventoried runtime modules and all 15 SQL resources are distribution-owned. Version-1 task/output hashes and all 49 preview record payloads remain frozen. The existing eight-item/4,096-character/4,096-byte evidence limits remain. |

All inputs are public-owned fictional orchard fixtures. No providers, owner data,
product source or private evidence are used.

## Checks and artifacts

- Focused installed observation suite: **11 passed**. The final storage-guard
  addition was followed by **3 focused passes** covering initial/correction
  branches, immutable storage and failed settlement.
- Source-level contract/release tests: **36 passed**; Ruff, strict mypy (67 source
  files), compile checks, catalog generation and explicit boundary/provenance
  checks passed.
- Complete installed convergence on Python 3.13.5 and disposable PostgreSQL
  18.4: **66 passed in 28.218 seconds**. Python isolation and the filesystem audit
  guard deny checkout reads, subprocesses and non-loopback network access.
- The convergence lane's first attempt found two stale test assumptions: the
  historical allowlist selected the last migration, and runtime ownership expected
  30 modules. Tests now select historical migration 14 explicitly, require no
  historical provider identifiers in later SQL, and check all 32 inventoried
  modules. Their three focused checks passed before the complete lane passed.
  No assertion was relaxed; no package bytes changed for these test fixes.
- Two local deterministic wheel/sdist builds are byte-identical; strict Twine
  inspection, exact member/metadata inspection and candidate hash verification
  passed. Hosted CI additionally rebuilds the sdist wheel and runs installed
  acceptance on Python 3.13 and 3.14.

Wheel SHA-256:
`928bd443e30d1d5fb70b0aa4b1c96245ad31ba67fe4bb20b2f7335d62166fa7c`

Sdist SHA-256:
`7f798002e5ac099b67ad686a4b7172bd48cbe0a290d46b6aed19909b2846f3ce`

The authoritative candidate member/size/hash inventory is
[immutable-observations-candidate-artifacts.json](immutable-observations-candidate-artifacts.json).
The original published 0.0.3 inventory remains unchanged.

Complete installed log SHA-256:
`11497501f80ee3929fe6cdbf0132cb60a065aff76f6283400bcfa8267da325e5`.
First-attempt log SHA-256:
`862403c5b8f2cd370881242e26c33180457758137c011906d170f3d781185642`.

## Limits and deferrals

This is deterministic contract/database/runtime proof with fictional inputs,
not model-quality, production provider or product adoption evidence. Commands
retain the existing single-scope and bounded-evidence limits. Historical archives
are not reauthored. Relation propagation, global winner selection, wider evidence
handoff, UI/providers and later work are outside this change. Publication and
product adoption need separate authorization; no settings or tags are changed.
