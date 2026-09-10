# 0.0.4 release-control preparation evidence

Verified on 2026-09-10 against public main
`683724c9ac1bbdd9ea237395ad62a3aa08fc6276`. PR #9 merged the reviewed head
`64b89c35794e6aa9352159e2ed8c1aba2e0b9637`; ancestry and an empty tree diff were
verified. This record prepares controls, not publication or consumer deployment.

## Reused acceptance and artifact comparison

- [Reviewed implementation CI](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/34423996087)
  passed at the exact reviewed head. Its broad review completed without actionable
  findings. The [original acceptance record](immutable-observations.md) remains intact.
- [Merge main-push CI](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/34425213901)
  passed at `683724c9ac1bbdd9ea237395ad62a3aa08fc6276`. Downloaded artifact
  `memoriesql-python-683724c9ac1bbdd9ea237395ad62a3aa08fc6276` was independently
  inspected; its full metadata/member/size/hash inventory matched the committed
  implementation candidate inventory and hosted inventory.
- Runtime source, migrations, contracts, dependency metadata and build configuration
  remain unchanged. All 71 repository source members in the original sdist were
  compared with this worktree: only README.md release-tag guidance changed.
- Because setuptools includes README.md in the sdist, one genuine pinned build and
  existing deterministic normalization refreshed the artifacts. Strict Twine and
  member/metadata inspection passed. The wheel is byte-identical to the reviewed
  and merge-CI wheel. Comparing every old/new sdist member found exactly one changed
  member: `memoriesql-0.0.4/README.md`; the member set is unchanged.
- The reviewed candidate inventory remains unchanged. Published 0.0.3's complete
  inventory was copied byte-for-byte to
  [runtime-0.0.3-package-artifact-inventory.json](runtime-0.0.3-package-artifact-inventory.json).
  Its historical evidence link now points to that snapshot.

## Current approved artifacts

[The authoritative inventory](runtime-package-artifact-inventory.json) records:

| Archive | Bytes | SHA-256 |
| --- | ---: | --- |
| `memoriesql-0.0.4-py3-none-any.whl` | 304641 | `928bd443e30d1d5fb70b0aa4b1c96245ad31ba67fe4bb20b2f7335d62166fa7c` |
| `memoriesql-0.0.4.tar.gz` | 272988 | `505ef59a2401813250816334f370b8ea05359b08c174f4865fe49fd8623bdf6e` |

The original candidate sdist hash was
`7f798002e5ac099b67ad686a4b7172bd48cbe0a290d46b6aed19909b2846f3ce` (272986 bytes).
No published archive or tag was altered. No archive was renamed.

## Focused verification and final-head gate

The release-control and bootstrap modules pass 20 focused tests covering exact
main/tag/version/repository identity, latest-run failure rejection, historical and
future version denial, inventory promotion, pinned publisher/OIDC boundaries,
missing/extra/altered/symlinked archives and frozen historical hashes. Ruff and
strict mypy pass for the three changed Python files. Explicit export provenance
and public-boundary verification pass. The release CLI verifies both genuine
archives and stages only those files; its selector accepts the successful recorded
main-push run only with the exact 0.0.4 tag/version/main tuple.

No local full runtime/convergence suite was repeated: runtime inputs are unchanged.
The readiness PR still requires its own exact-head hosted CI, independent hosted
artifact comparison and one bounded broad review. Final PR status and those run
links are recorded on the PR after the commit exists. The eventual owner merge
requires a new successful exact-main push run; this base run cannot authorize a
later merge or substitute for expired/missing artifacts.

## External prerequisites, read-only observations

- PyPI 0.0.4 version JSON returned HTTP 404 on 2026-09-10; availability is not reserved.
- Authenticated GitHub API reads found only tag `v0.0.3` allowed in `pypi`, required
  reviewer `JohnnyFiv3r`, `can_admins_bypass=false`, and `prevent_self_review=false`.
- Public 0.0.3 wheel provenance identifies repository `JohnnyFiv3r/memoriesql`,
  workflow `publish-pypi.yml`, environment `pypi`. This is historical publisher
  provenance, not authenticated verification of current PyPI configuration.

The [owner release guide](../releasing.md#separate-owner-actions) requires merging
this PR, separately authorizing the exact-tag environment allowance change,
rechecking version/publisher/protection/main-CI/artifacts, separately authorizing
the tag, and owner approval of the waiting upload. Schema-15 migration and version-2
consumer opt-in are separate deployment decisions. No external setting, tag,
upload, consumer, provider, owner data or checkpoint was changed here.
