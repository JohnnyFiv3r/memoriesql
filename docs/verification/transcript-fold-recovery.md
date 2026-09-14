# Unreleased transcript-fold recovery verification

Public base: `55a2031e1bbf3d98d5ef03a1e945bea9f1f96537`.
This is recovery-substrate evidence, not a release, producer qualification,
author source-revisiting proof, provider admission or checkpoint execution.

## Released baseline gap

A fresh isolated inspection of the hash-matched installed 0.0.5 distribution
verified all 73 installed package-owned files against the released wheel:

| Released archive | Bytes | SHA-256 |
| --- | ---: | --- |
| `memoriesql-0.0.5-py3-none-any.whl` | 373470 | `0c313d153c82521500afd7c85c6d4041d0326987374950eeee4e334306932a96` |
| `memoriesql-0.0.5.tar.gz` | 333081 | `7f11e8a0dc7849dc2a25616985b3b8bfae0a2b0fba9f1c3dbbb10c3781200b92` |

The installed `TranscriptFoldInboxItem` lacks exact envelope, lineage,
`source_revision_key`, and `file_identity_key`. Its session inbox accepts only a
source ID and provides no point/page selection. `DurableTranscriptReadRequest`
requires revision and file identity in addition to source and byte range. This is
a contract-level regression demonstrating the restart gap; it is not a claim
that a service completed recovery against the old release. No private code or
provider fixture was used as public implementation/provenance.

## Fictional acceptance

`tests/runtime/test_fold_recovery.py` and `fold_recovery_fixture.py` exercise:

- All three stored outcome kinds, original context, normalized envelope bytes
  distinct from raw derivation, unknown topology and policy scope reason.
- Repeating content/offsets across revisions/files; exact point keys, serialized
  continuation restart, fixed-watermark pagination and fresh post-watermark discovery.
- Concurrent receipt inserts demonstrably waiting on the per-source advisory
  fence, subsequent commit visibility, failed insertion rollback and no skipped
  eligible outcome or additional receipt.
- Cross-window Unicode byte pages, retained incomplete tails excluded from
  acknowledged outcomes, explicit missing lineage/chunk/envelope errors.
- Explicit fictional service policy/grant, missing capability, scope outside the
  delegation, resource revocation, revocation during a confirmed authority wait,
  bounded lock timeout, and rejection of stronger read snapshots.
- Existing public fold command and replay, unchanged legacy human-only raw read
  denial, and snapshots proving recovery adds no capture/fold progress, receipt,
  package, source event/unit, bead, task or outbox changes.

The exact-turn storage fixture is deliberately administrative and provider-neutral.
It exercises recovery of stored facts, **not** admission through the historical
provider-profile allowlist. The unknown-span command test separately exercises the
unchanged public capture/read/fold write path on schema 19. Missing-evidence tests
corrupt only disposable fictional rows inside rollback transactions.

`scripts/prove_fold_recovery_restart.py` runs setup, recovery and cleanup in three
separate installed Python processes. Setup seeds schema 18 then migrates to 19 and
compares all original fold receipt fields. Recovery imports no fixture helper and
uses only database location, service credential, workspace and source; no command,
receipt, revision/file identifier or external transcript is carried forward. It
has a filesystem/subprocess guard and discovers all six outcomes after restart.
The script is run for wheel and sdist installations in the existing Python matrix.

## Measured operational work

Initial focused measurements: Python 3.13.5, local disposable PostgreSQL 18 image
`sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636`.
These are observed fictional workload results, not production latency promises.

| Workload | Result |
| --- | --- |
| 96,001 UTF-8 bytes, 64 raw lineage parts | Exact byte equality; 4 lineage pages + 64 byte reads; 408 adapter SQL execute calls plus transaction control; 0 provider calls |
| Same workload latency / Python allocation peak | 0.243 s / 291,913 bytes (tracemalloc, includes accumulated test output) |
| Deep discovery seek in 4,097 receipt rows | Actual index scan `transcript_fold_recovery_order_idx`; 34 candidate rows, 9 shared-hit buffers, 0 shared-read buffers, 0 temp blocks, 0.030 ms measured execution |
| Fresh installed process, two revisions / six outcomes | 59 bounded operations, 840,668 recovered bytes including normalized envelopes, 0 provider calls, 0.326 s / 336,174 peak Python bytes |

The index load fixture supplies discovery metadata only; it is not qualification
or raw-byte proof for those extra synthetic rows. Raw/exact byte proof uses the
separate complete fixtures. Operation counts distinguish adapter statements from
PostgreSQL internal row work. The bound analysis and actual deep-seek plan are in
the regression and [contract](../transcript-fold-recovery.md).

## Artifact and compatibility evidence

[The candidate inventory](fold-recovery-candidate-artifacts.json) records the new
wheel/sdist hashes, sizes, members and 54 contract IDs. These are **unreleased**
archives retaining 0.0.5 metadata because no next version was selected. They must
not be confused with the published archives above. Two deterministic builds
matched byte-for-byte; archive inspection enforces explicit namespace/resource
ownership, genuine embedded versions, pinned dependencies and no private files.

The public package CI compares against this candidate inventory. The separate
publication verifier and workflow still require the immutable published 0.0.5
inventory and exact release controls; they were not redirected or loosened.
Historical inventories, migrations 0001–0018 and the prior 53 record files remain
byte-identical to base. The 160-test installed lane identified two stale schema/module-count assertions:
19 is now supported (20 is the unsupported target), and the inventory now has
41 runtime modules instead of 39. These two tests were corrected and rerun
selectively against the same byte-identical candidate; the other 158 passed
without changes. The installed lane checks resource hashes, historical
schema snapshots, legacy receipts, exposure/authorization/settlement behavior and
public ownership. The PR reports final convergence counts and exact-head hosted
run/archive comparison, so this document does not invent a future commit SHA.

An early local attempt could not reach loopback under the sandbox. Another test
setup overlapped a separate setup and encountered the historical cluster-wide
`ALTER ROLE` tuple-update conflict. Subsequent database setup is serialized;
neither setup failure is evidence about product recovery behavior. Focused
fixture corrections did not alter legacy runtime policies. No unchanged broad
suite was rerun to investigate unrelated historical queue failures.

## Scope stop

No semantic artifacts, executor changes, author source revisiting, producer
qualification, production grants, external transcripts, providers, real database
upgrades or private product changes. One relevant installed local convergence
lane precedes exact-head hosted Python 3.13/3.14 wheel/sdist compatibility.
Graphify has no configured public graph here; source/SQL/tests supplied focused
evidence and no graph rebuild was performed. Existing checkpoint closure stays
closed. The six historical unexplained product queue failures remain unresolved.
PR-02O/CP-2 require later public dependencies, release, product composition and
owner proof. Next dependent public slice: author-controlled source revisiting.
