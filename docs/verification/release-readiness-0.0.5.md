# 0.0.5 release-readiness evidence

This prepares a public readiness PR only. It does not publish, tag, approve upload,
change external configuration, deploy or authorize Desktop adoption.

## Baseline and immutable inputs

On 2026-09-12, fetched `origin/main` was
`b9301105a5f6841654be7b9437afbd12959f81e8`. GitHub confirms PR #13 merged at
2026-09-12T13:34:49Z with reviewed head
`1b9bf83deae265ae24a1565c8f933d2151a975d9`. That head is ancestral to main and
its tree is identical. This work uses isolated `codex/release-readiness-005`;
adjacent worktrees are preserved. Neither this base nor the future PR head is
identified as the eventual release SHA.

All 18 migration files and all 53 existing contract records compare byte-for-byte
with the base. Every file under `src/memoriesql` is unchanged except the package
version fallback in `__init__.py`; generated catalogs, runtime/migration inventories
and dependencies are unchanged. Comparing parsed `pyproject.toml` found only the
version changed. All five existing artifact inventory files are frozen by whole-file
hash assertions; published releases, tags and historical verification records are
untouched. The new versioned candidate inventory is separate from the historical
`runtime-package-artifact-inventory.json`, which still records published 0.0.4.

## Version availability and external prerequisites

Read-only PyPI JSON inspection at 2026-09-12T13:36:08Z listed exactly `0.0.1a1`,
`0.0.2`, `0.0.3` and `0.0.4`, with two non-yanked artifacts each. The exact 0.0.5
endpoint returned HTTP 404 and remote tags contained no `v0.0.5`. This is not a
reservation. Recheck before release and stop for owner direction if unavailable.
No renamed archive, suffix or alternative version substitutes for genuine 0.0.5.

Authenticated read-only GitHub API inspection found the `pypi` environment permits
only tag `v0.0.4`, requires reviewer `JohnnyFiv3r`, disables administrator bypass
and allows owner self-review. These settings were not changed. A later separately
authorized exact-tag allowance change and authenticated PyPI publisher check remain
prerequisites; historical public publisher provenance is not current account proof.

## Reused runtime evidence and genuine artifacts

[PR #13 exact-head CI](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/34552994039)
passed at the reviewed implementation head, including 128 installed tests for each
wheel/sdist installation on Python 3.13 and 3.14. The existing
[activation correction evidence](complete-input-execution.md#owner-activation-repair-for-pr-13)
retains both reproduced-before/passing-after defects: cancellation winning keyed
waits must prevent transfer; successful natural-duplicate keys must receive durable
shared-ledger request bindings. This completed review is not reopened by readiness.

[Merged-main push CI](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/34696819752)
also passed at the exact baseline. Its downloaded wheel has SHA-256
`523baaa148706dbd5965ee6699629c061c3d7cfb9d8f87e132d2b0bce2bed6e8`, matching the
reviewed implementation artifact. Both it and the genuine 0.0.5 wheel own the same
73 package files. Comparing every package member found only the version fallback
changed; runtime, catalogs and SQL resources are byte-identical. Distribution
metadata and packaged guidance change intentionally. No local runtime/convergence
suite or Graphify pass is repeated for unchanged behavior.

Pinned builds use `build==1.3.0`, `setuptools==80.9.0`, `wheel==0.45.1` and
`SOURCE_DATE_EPOCH=315532800`, with the existing deterministic sdist normalization.
Two independent candidate builds produce identical wheel and sdist bytes. A separate
extraction/build from the sdist produces the identical wheel. Strict Twine and exact
archive/member/metadata inspection pass. Wheel METADATA and sdist PKG-INFO identify
0.0.5; archive roots, filenames and installed version agree. These are genuine builds,
not renamed development 0.0.4 artifacts.

[Committed candidate inventory](runtime-0.0.5-package-artifact-inventory.json):

| Archive | Bytes | SHA-256 |
| --- | ---: | --- |
| `memoriesql-0.0.5-py3-none-any.whl` | 373470 | `0c313d153c82521500afd7c85c6d4041d0326987374950eeee4e334306932a96` |
| `memoriesql-0.0.5.tar.gz` | 333081 | `7f11e8a0dc7849dc2a25616985b3b8bfae0a2b0fba9f1c3dbbb10c3781200b92` |

The release verifier checks and stages only these two files against that committed
inventory. Candidate package CI checks it too. The separate publication workflow
still requires exact current main, its latest successful main-push run, the exact
new `v0.0.5` tag and protected owner approval; no publication-time rebuild or
historical/future-version fallback is introduced.

## Verification and bounded review

The 20 focused metadata/control tests passed in 0.056 seconds. Ruff and strict mypy
passed for all six changed Python files; generators, boundary, migration/runtime
export and provenance checks passed. Fresh direct-wheel and independently rebuilt
sdist-wheel checks passed on Python 3.13.5 and 3.14.0 (four installations, each with
five catalog tests). They verified: exact installed version,
unchanged direct dependencies, all 73 package-file ownership hashes, 18 installed
migration resources, revision-2 complete-input task import and catalog behavior.
Checkout access and external network access are denied during those checks.

The initial broad review and hosted CI identified two stale `0.0.4` expectations
in installed-migration metadata tests. Both supported-Python wheel runs passed
126 of 128 cases and failed only those two version assertions. The expectations
now match genuine 0.0.5. Only those two metadata/resource methods were rerun locally
in each of the four existing isolated installs, without their unused database
setup; all passed. No runtime behavior or artifact byte changed. A compatibility
anchor also preserves the historical 0.0.4 evidence link to owner actions. The
review's missing-DCO report was disproved by the hosted commit API: the original
commit already includes the contributor's `Signed-off-by` trailer.

One broad review and at most one focused rereview are allowed for this readiness
PR. Findings must be answered/resolved and final-head CI obtained. Independently
downloaded final-head archives must match the committed candidate inventory, not
just a downloaded CI receipt. Run IDs and exact final head belong on the PR once
available. Its later merge requires a new exact-current-main push CI and retained
matching artifacts; the PR head is never the eventual release SHA by assertion.

## Product impact and separate owner actions

0.0.5 makes the already merged schemas 16–18 available as a distinctly versioned
candidate with explicit caller guidance: retained evidence packages, qualified
materialization, successor activation, authorized reader v2, trusted exposure and
fenced initial application. Installation/upgrading does not migrate a database,
provision production trust, activate tasks, configure a provider or start a service.
Mechanical exposure is not comprehension or independent source completeness.
Rolling-note quality and execution ceilings remain experimental; complete-input
correction/reauthoring is not delivered. Existing cleanup/accounting ownership remains.

The existing PR-02O/CP-2 checklist remains incomplete. The six historical Desktop
queue failures stay unresolved and are not rerun or called fixed. No new tracker,
Desktop change, provider call, owner-data access, production provisioning, deployment
or checkpoint execution is included. The [existing release guide](../releasing.md#remaining-separately-authorized-release-actions)
records the remaining owner actions: merge, external exact-tag allowance and
publisher/protection verification, exact-main CI/artifact verification, separately
authorized tag creation, owner upload approval and post-publication verification.
Public release must precede separately authorized Desktop adoption.
