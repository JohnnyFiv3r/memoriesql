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

## Acceptance coverage

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
resolved; no third review is requested. Final-head hosted CI covers the final fix,
which necessarily follows the focused review that identified it. Artifacts below
include all corrections.

The wheel installs in a fresh environment. An independently extracted sdist builds
a byte-identical wheel, which also installs in another fresh environment and exposes
schema 18 and execution revision 2. Strict Twine, archive-member ownership,
namespace/resource closure, generators and provenance checks pass: 39 runtime files,
18 SQL resources and 53 contract records. All 68 historical SQL/contract files
(17 migrations and 51 records) compare byte-for-byte with the base. Published
release inventories, version metadata and publication workflow bytes are unchanged.

Development artifact SHA-256 values (not a published release inventory):

- Wheel: `56c64c6f5795110a8c27ecd3f4b49411ca4d4608512b469a7b067a86312e7a43`
- Sdist: `d348ab2c4bd4fd1fe1d094653b89eb24559426d492ca7fa12321e3a86c1b8252`

Exact-head hosted CI and review state are recorded on the PR. No release version
is selected.
PR-02O and CP-2 stay unchecked in the existing delivery checklist; the proposed
update belongs with that item, not a new tracker. Public merge/release must precede
Desktop consumption. Production trust, provider integration, owner-data proof,
UI, CP execution and PR-02P/Q advancement remain deferred.
