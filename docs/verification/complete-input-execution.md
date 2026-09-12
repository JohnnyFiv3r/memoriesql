# Fictional complete-input execution evidence

Base: public main `bb459ddef14e5627777b21c1366a896b75f24ca8`, the merge of PR #12.
Live GitHub inspection confirmed its final head
`fdcb742f7b8215bc6145598ff052ca794be95c7b` merged and ancestral to this base.
The focused review covered `83538b21e9311649a8c554a07bfda422d74f8de7`; the final
commit changed only a fixture timestamp and provenance hash. Existing worktrees
were preserved; this work uses `codex/pr-02o-complete-input-execution`.

All source content, users, service principals, trust policies and model callbacks
in these tests are fictional. PostgreSQL runs in a disposable container with no
owner-data mount. No real provider request, production provisioning or CP run is
part of this evidence. The six documented unexplained queue-test failures remain
unresolved historical evidence; this slice neither reruns nor labels them fixed.

## Reader throughput

Measured in the isolated installed-wheel environment on local Python 3.13.5 and the pinned PostgreSQL 18 Docker image. These
are instrumented workload observations, not production latency or model-quality
SLOs. Tracemalloc measures incremental Python reader allocations after fixture
setup. It does not measure PostgreSQL RSS, native allocations or model memory.
Client SQL counts cover `Connection.execute` calls, including authorization and
transaction setup, but exclude implicit BEGIN/COMMIT and statements internal to
SQL functions. Each authorized operation owns one transaction.

| Fictional unit | Reader | Authorized operations | Client SQL calls | Read latency | Peak traced memory |
| --- | --- | ---: | ---: | ---: | ---: |
| 131,072 Unicode code points / 327,680 UTF-8 bytes / 8 parts | v1 | 130 | 520 | 1,163.355 ms | 50,257 B |
| Same complete inventory | v2 | 1 | 6 | 11.412 ms | 2,194,168 B |
| 12,800 code points / 20,480 UTF-8 bytes / 256 parts | v1 | 320 | 1,280 | 1,578.403 ms | 48,324 B |
| Same complete inventory | v2 | 32 | 192 | 190.743 ms | 101,358 B |
| 81,920 astral code points / 327,680 UTF-8 bytes / 5 parts | v1 | 82 | 328 | 651.730 ms | 61,835 B |
| Same complete inventory | v2 | 1 | 6 | 7.125 ms | 2,315,422 B |

Every reader run reconstructed and hashed the complete exact text. Reader
operations made **zero** model interactions. Execution separately used eight
fictional model interactions for the Unicode unit (2,404.262 ms, maximum prompt
JSON 107,022 bytes), and two for 256 parts (4,077.013 ms, maximum prompt JSON
134,012 bytes). Each execution produced one accepted bead. Provider request intents
and usage use the existing accounting ledger. These synthetic timings do not
predict real provider latency, billing or comprehension.

An earlier 256-part follow-up measured a concrete optimization: exposure validation now
checks the immediately preceding part through its indexed ordinal instead of
rescanning every preceding part. Contiguous ordered recording makes that check
sufficient by induction. The earlier workload took 4,098.896 ms for execution;
the focused follow-up took 3,668.224 ms. This is a single diagnostic comparison,
not a statistically established speedup. The table uses the later final packing
verification for 256 parts; its execution took 4,077.013 ms. Timing variation is
reported rather than presented as a stable performance guarantee.

`EXPLAIN (ANALYZE, BUFFERS)` for the bounded reader's selection used
`evidence_package_parts_pkey`, one index search and eight returned rows. The
Unicode fixture hit three shared blocks; the 256-part fixture hit four. No temp
blocks, shared reads or dirtied blocks were reported for those selections.
The returned text is bounded by eight existing 64-KiB parts; no database operation
or prompt assembles the 16-MiB storage maximum. The additional ~2.1-MiB reader peak
is the measured cost of removing 129 short transactions in the Unicode fixture.

## Acceptance coverage before the owner activation repair

Focused regressions cover exact multi-window exposure and one atomic initial bead;
original unavailable snapshots and activation replay; missing trust composition;
reader/model claims without exposure; unapproved attestors; omitted final evidence;
changed content, package and attempt; reply-loss exposure replay; producer/dispatch
revocation; authorization waits and stale snapshot rejection; cancellation; typed
budget failure retaining raw evidence and a thin bead; crash-before-apply and
fresh-attempt coverage; owned exposure writes during cancellation; direct canonical
apply denial; and independent ninth-unit progress with a pending sibling.

The installed convergence lane covers 119 tests: 96 unaffected cases passed in
the first run, then all 23 affected/new cases passed under the same checkout and
external-network isolation guard in 42.713 seconds. The initial run exposed a
missing test-only runtime import and an obsolete out-of-range assertion for schema
18; both were corrected. No runtime change was required by that run. Repository
checks cover 37 tests, with only the three changed inventory expectations rerun
after correction. Ruff and mypy (81 files) passed; changed-test mypy also passed. A later targeted inspection found that the worker
reader route omitted the standalone reader's server timeout settings. The route
now applies the same two-second statement and 500-ms lock bounds. The two affected
installed authorization-wait/complete-apply checks pass in 2.755 seconds; no
unchanged local suite was rerun. The broad review identified one additional defect: astral Unicode can require
12 escaped JSON bytes per character, so a valid 64-KiB part exceeded a window's
byte bound. A fictional all-emoji part reproduced the terminal failure. Window
packing now finds the largest exact prefix fitting the unchanged byte/character
ceilings. The new regression and mixed-Unicode case pass from the installed wheel
in 3.799 seconds: the full part is exposed in two windows and produces one accepted
bead. This adds one case (120 total); no unchanged convergence suite was rerun.
The single focused rereview found that the prefix search also needed to fill
partially occupied windows. Five full astral parts reproduced unnecessary budget
exhaustion before that correction. The final packing now exposes all 81,920 code
points in eight interactions (2,287.552 ms, maximum prompt JSON 134,360 bytes),
from one reader operation. This regression and the affected 256-part throughput
case pass in 12.958 seconds, verifying exact supplied-text hashes and one bead per
unit. This adds another case (121 total). Both review findings are answered and
resolved; no third review is requested. Hosted CI at reviewed head
`5cc001334aca2d5b2b99a6bef4a5692faa262a3a` covered that packing fix, which
followed the focused review that identified it. The baseline artifacts below include
those earlier corrections; the subsequent owner activation repair is recorded below.

The wheel installs in a fresh environment. An independently extracted sdist builds
a byte-identical wheel, which also installs in another fresh environment and exposes
schema 18 and execution revision 2. Strict Twine, archive-member ownership,
namespace/resource closure, generators and provenance checks pass: 39 runtime files,
18 SQL resources and 53 contract records. All 68 historical SQL/contract files
(17 migrations and 51 records) compare byte-for-byte with the base. Published
release inventories, version metadata and publication workflow bytes are unchanged.

Reviewed baseline `5cc001334aca2d5b2b99a6bef4a5692faa262a3a` development artifact
SHA-256 values (not a published release inventory):

- Wheel: `56c64c6f5795110a8c27ecd3f4b49411ca4d4608512b469a7b067a86312e7a43`
- Sdist: `d348ab2c4bd4fd1fe1d094653b89eb24559426d492ca7fa12321e3a86c1b8252`

Exact-head hosted CI and review state are recorded on the PR. No release version
is selected.
PR-02O and CP-2 stay unchecked in the existing delivery checklist; the proposed
update belongs with that item, not a new tracker. Public merge/release must precede
Desktop consumption. Production trust, provider integration, owner-data proof,
UI, CP execution and PR-02P/Q advancement remain deferred.


## Owner activation repair for PR #13

Repair base: reviewed head `5cc001334aca2d5b2b99a6bef4a5692faa262a3a`.
The public PR base remains `bb459ddef14e5627777b21c1366a896b75f24ca8`.
The before run freshly installed the hash-matched baseline wheel above, copied the
fictional regressions, and denied checkout access and external network access.
It ran eight activation cases in 6.389 seconds, with eight failure reports across
six cases (including subtest failures). Cancellation-first and original transfer
rollback already passed. The failures established:

- Activation really waited on a held binding or operation advisory lock, yet
  created a successor after owner cancellation committed. An independently held
  cancellation row lock reproduced the stale-state transfer too.
- A successful natural duplicate under K2 left zero K2 activation receipts;
  activating a different binding under K2 then succeeded, adding a successor,
  execution link and outbox event.
- Concurrent fresh keys produced only one acknowledged-key receipt, and an
  injected failure at duplicate receipt completion did not fire because no
  duplicate acknowledgement was written.

The correction is confined to `activate_complete_input_v1` in unmerged migration
0018. It reloads and locks the original task after both keyed locks, then rechecks
authority and current transfer eligibility. The order is authority fence,
operation key, binding key, original task row. Completed activation replays are
handled before eligibility for a new transfer, preserving valid replay without
mistaking unrelated cancellation for transfer. Each successful new key, including
a natural duplicate, receives a request-bound receipt in the existing shared
ledger. The original successor, package pin and original receipt stay intact.

The same eight focused cases pass from the corrected installed wheel in 6.285
seconds. The single relevant installed convergence lane then passed all 128 tests
in 101.344 seconds with checkout and external-network access denied, including
legacy receipts, exposure, authorization, accounting and cleanup ownership. A test-only correction lets the no-meaning assertion account for two
separately materialized thin beads in multi-binding cases; no runtime change was
needed after the SQL repair. Seven cases are permanent additions; the existing
transfer-rollback case now also compares task/binding snapshots, all receipt counts
and execution/outbox/accounting effects. Duplicate acknowledgement fault injection
rolls back atomically and permits an exact retry after the fictional fault is
removed. Rejected operations create no successor, outbox, attempt, request intent,
usage, exposure or accepted meaning. Concurrent same/fresh keys converge on one
successor; replay remains stable after that successor is cancelled.

All 70 prior SQL/contract files (0001–0017 and all 53 records at the reviewed head)
remain byte-identical. Original schema-17 inputs, unavailable snapshots, pins and
receipts remain intact. No exposure, authorization, accounting, settlement or
cleanup implementation changed. The convergence lane retained the throughput
fixtures: the Unicode and five-part astral units each used one reader operation
and eight fictional interactions; the 256-part unit used 32 reads and two
interactions. Reader-v2 latencies were 12.292 / 11.385 / 175.282 ms respectively,
with peak traced allocations 2,194,168 / 2,315,210 / 101,358 bytes. Each produced
one bead; these observations do not qualify note quality or production ceilings. Migration inventory and extraction/export
provenance reflect the corrected 0018 bytes. No version metadata, published
inventory or publication control changed.

Corrected development artifact SHA-256 values:

- Wheel: `523baaa148706dbd5965ee6699629c061c3d7cfb9d8f87e132d2b0bce2bed6e8`
- Sdist: `52213ab54bade5cfdcb0de65302a25d015301702ac13365b51cb5ad9b48f558d`

An independent extraction/rebuild of the corrected sdist produced a byte-identical
wheel. Both wheels installed in fresh environments and passed namespace/resource
isolation checks. Strict Twine and explicit archive-member ownership checks pass.
Repository validation passes 37 tests, Ruff and mypy over 81 files, contract/catalog
generators, boundary and provenance checks. Exact-final-head hosted CI and an
independent hosted/local artifact comparison are recorded on the existing PR.

The broad review and focused rereview budget is exhausted. No third Codex review
is requested; this evidence is for the owner's recheck. The existing PR remains
open, non-draft, subscribed and unmerged. Proposed update to the existing execution
checklist: record this activation repair and its verification under the current
PR-02O item; keep PR-02O and CP-2 incomplete. No separate tracker or Desktop edit
is introduced. The six unexplained historical queue-test failures remain unresolved
and were not rerun. Rolling-note quality and execution ceilings remain experimental,
with authoring-strategy and real-provider qualification deferred. No release,
publication, owner-data access, production provisioning, deployment or CP execution
is part of this repair.
