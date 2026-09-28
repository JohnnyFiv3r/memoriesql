# PR-05 public-core internal result preparation

This public-core lane owns internal preparation, native membership witnesses and
atomic result construction. It is not the available query/result interface. The whole amended approval and matching
ea-3 are recorded in `../approvals/pr-05-settings-reconciliation.md`; both approved
normative blobs remain unchanged. M0030 and its canonical lifecycle projection are
an explicit owner-merged dependency, not reimplemented here.

M0034 is based on public main `cba4389ac937a17191aa84698b6be18b318dfd6b`,
including owner-merged #56 accepted head `5fb326ca886a84402ce86fa8ee95790d5e1476c9`.
PR-03 confirmed M0034 allocation, with no overlapping lifecycle migration.
M0031–M0033 and the canonical M0030 projector remain byte-identical dependencies.

The primary acceptance claim for M0034 is **atomic private persistence of native
bag-result values and complete witnesses for the qualified composition cut,
without repeating completed SELECT work**. The cut includes explicit projections,
typed filters, key and outer joins, nonrecursive CTE/derived-table composition,
explicit LIMIT/OFFSET subsets and
`UNION ALL`. PostgreSQL computes values and membership in one restricted-reader
statement; Python binds emitted membership to exact frozen keys and shares DAG
nodes. Duplicate tuples/branches retain distinct saved ordinals. Outer nonmatches
and empty outputs retain the searched population, predicate and protected closure.
No Python SQL interpreter, original unchecked SQL, guessed contributor or empty
witness substitute is used. The admitted full private SELECT surface is unchanged.
At that bag-only cut, groups/distinct, other sets, windows, correlated subqueries
and recursion still required their own witness-publication qualification. The
continuation below extends the internal cut without narrowing full admission. This is a delivery milestone, not a reduction of the approved interface.

The next authorized continuation starts at owner-merged #58, public main
`725b31eaf31df4542faf42036166e12e41578ac0`, whose tree equals accepted
`f5049fc9d1415bc44c313bffce0c4721bcdc11f4`. Its primary acceptance claim is
**complete native group and set witnesses composed with atomic private result
persistence under the original work bound**. PR-03 confirmed no overlapping
lifecycle work or migration allocation; this continuation adds no migration.
The accepted-head bag qualification remains historical evidence for that slice,
not qualification of the continuation.

PostgreSQL computes GROUP BY, HAVING, FILTER, COUNT/COUNT DISTINCT, SUM/AVG,
MIN/MAX, SELECT DISTINCT, UNION, INTERSECT and EXCEPT (including ALL) over the
same frozen frame. Native equivalence classes retain null/collation equality,
all collapsed contributors and left/right/output bag multiplicities. Group
records retain every member, argument/filter eligibility, COUNT DISTINCT classes,
all extremum ties, native group keys/aggregate values and HAVING truth. Rejected
groups and zero-output set classes remain tested dependencies; both empty set
branches record a zero population rather than an invented null tuple. Native
empty-left INTERSECT/EXCEPT skips the unneeded right arm, including its ledgers;
the witness records `right_evaluated: false` and an unknown right multiplicity,
never a fabricated zero or evaluation of skipped arguments. Its frozen source
population/program remain protected dependencies. Tuple,
distinct bead and canonical independent-root counts remain separate facts.
Scoped SQL aggregates create no canonical observation, evidence or corroboration.

Materialized private CTEs and ledger rows share the one restricted SELECT and
original deadline/reservation. Native output ordinals preserve the sealed order;
ledger rows cannot masquerade as output nulls. Unused CTEs are pruned without
forcing their arguments. FILTER arguments and HAVING projections retain native
short-circuit behavior. A restricted, deadline-bound `EXPLAIN (COSTS FALSE)` of
the admitted program checks original PostgreSQL phase validity; no estimate is
read, used as enforcement or presented as a work guarantee. Actual execution,
independent login/function privileges, authority fencing and cancellation remain
the boundary. Missing helper privileges refuse instead of granting or falling
back. Witness binding checks the same deadline/cancellation, shares exact-key DAG
nodes and charges encoded provenance together with values before any publication.

The private `native-bag-v1` graph has additive `composition_revision: 2` and
`tested_nodes` for these shapes; no public wire contract changes. M0034 commits
complete body/witness partitions atomically and recovers exact receipts without
redispatch. Windows, correlated/subquery membership, recursion, saved-input reuse,
paging/hydration, disclosure contexts and checkpoint/save/restore remain pending.
Whole-result authorization, revocation/erasure, host isolation, durable cumulative
work/physical-storage accounting and useful W1–W7 fit still gate public availability.
Correct exhaustion and small fictional native-composition cases do not establish
those workload or capacity claims. No policy ceiling or approved blob changes.

The next no-migration continuation starts from owner-merged #59 at public main
`d8d7ad4cee00490582520ce805704cfccb0d7d61`. Its single claim is **private
native ranking and neighbor window witnesses composed with the existing atomic
result commit**. One projected `row_number`, `rank`, `dense_rank`, `lag` or `lead`
window per SELECT is qualified, including partitions, peers and chosen or absent
neighbors, when the outer sort resolves to a projected alias or direct projected
source column. Other admitted outer sort expressions remain witness-publication
pending. Grouping in a prior CTE may feed that SELECT, and `UNION ALL` may
combine separately windowed branches with distinct partition ledgers. PostgreSQL
computes all visible values; a shared ordered partition ledger records full
input occurrences once. Each row points to its ordinal, peer span and exact
neighbor. The ledger, rows and witness DAG share one restricted statement,
reservation and deadline. The independently restricted reader profile adds only the exact
two- and three-argument lag/lead signatures used by this cut. Missing privilege,
cancelled binding, unqualified shapes and budget exhaustion leave no partial
result. The native `composition_revision: 2` graph and M0034 publication store
remain private and unchanged in wire shape; no migration or policy change occurs.
Full qualifications, measurements and exact-head receipts belong to the PR.

This cut does not qualify same-SELECT grouping or multiple windows, aggregate
ROWS frames,
scalar/correlated/EXISTS/IN subquery lineage or bounded recursive CTEs. They
remain admitted only where their mandatory native derivation is available;
otherwise witness publication refuses with `witness_qualification_pending`.
The public query/result and disclosure APIs, checkpoints, saves, cross-run
reuse, whole-closure reauthorization, erasure, cumulative work and physical
storage accounting, and representative W1–W7 workload fit also remain pending.
Passing small fictional windows and safe exhaustion is not semantic-quality or
capacity certification.

The next no-migration continuation starts from owner-merged #60 at public main
`8463f3b2beb572c327c73a35f3e1af318c0161d5`. Its single claim is **native
multi-window and aggregate ROWS-frame membership composed with ordinary
same-SELECT grouping, under the existing private atomic result commit**.
Previously qualified single ranking/neighbor windows remain covered. Multiple
different partition/order specifications may share a SELECT, including windows
used only in an outer ordering expression. A partition ledger is shared by exact
partition/order specification; each window node independently names its source,
partition and native peer rank. The ledger uses the hidden source trace only to
stabilize ordering within peers; it does not change rank/dense-rank peer groups
or the admitted visible-key requirement for row_number/lag/lead.

For COUNT/SUM/AVG/MIN/MAX with an explicit ROWS frame, a visible source-key
order proves a total partition order and PostgreSQL emits native first/last
ordinals in the same restricted SELECT as the value. The frame is then a span
of the shared partition ledger rather than a repeated array of every member.
The proof resolves positional table-column aliases against catalog key positions;
an alias for a non-key column cannot stand in for the key.
When that proof is unavailable, PostgreSQL emits actual frame-member traces;
the binder verifies each against the partition occurrence bag and records
contributor refs, multiplicities and empty frames. A contiguous span is
recorded only when it can be verified; tied-peer ROWS frames never receive a
guessed span. FILTER and native null
semantics remain evaluated by PostgreSQL; all frame members remain protected
dependencies. Ordinary GROUP BY/HAVING and their rejected-group ledger run before
the window phase, including nested ordinary aggregates such as
`SUM(COUNT(*)) OVER (...)` and the implicit single group. Outer sort expressions
are computed without changing the visible projection. No SQL privilege, public
result contract, migration, policy ceiling or approved blob changes.

This is private witness-publication qualification, not an available query or
disclosure API. Scalar/correlated/EXISTS/IN subquery lineage and bounded recursive
CTE witnesses remain pending, as do saved-input reuse, paging/hydration,
checkpoints, saves, whole-closure reauthorization, governed erasure, cumulative
work/physical-storage accounting and measured useful W1–W7 fit. Correct timeout
settlement and the fictional window cases below cannot certify capacity or
semantic answer quality. Full installed receipts and exact-head CI belong to the
PR; no hosted paid qualification is authorized by this continuation.
The first full installed 3.13 convergence at `275e3b7` exposed a stale
pending-shape regression that still expected a now-qualified ROWS window to
refuse before reader dispatch. The corrected refusal case uses an admitted
correlated scalar subquery, which remains witness-publication pending. The
failed head and logs are retained; only a fresh exact-head convergence can
qualify the correction.
The first hub-heavy development probe exposed PostgreSQL's fixed 64 MiB
temporary-file limit at a 256-row running frame whose order omitted part of
the logical source key. The source-key span optimization allows a qualified
eight-alias, 256-row running window to finish with the unchanged bounds and
the same visible values as the native statement (241 ms, 560,261 encoded bytes,
including 420,383 witness bytes, in the local fictional probe); an equal-time
32-row CURRENT ROW frame still uses exact member traces (128 ms, 96,486 encoded
bytes). SQLSTATE `53400` from the
owned temporary-file fence is reported as whole-query `budget_exhausted`,
never successful emptiness. These are fictional shape measurements, not W1–W7
capacity or physical-storage certification; the original non-total-key probe
and failure log remain retained.

The next no-migration internal continuation starts from owner-merged #61 at
public main `0bace52fcb12106db261041292a7d99b7e79755e`. Its single claim is
**native row-level EXISTS/NOT EXISTS match bags composed with the existing atomic
private result commit**. A WHERE predicate may contain multiple correlated or
uncorrelated EXISTS clauses over one logical relation, with `=`, `IS NULL` and
Boolean connectives over columns/bound scalars. The trusted
compiler uses a lateral aggregate to evaluate each
matching bag and its count once under the original restricted reader, frame and
deadline; LATERAL is not added to agent SQL admission. PostgreSQL's ignored
EXISTS projection is not evaluated for membership. The private witness links
each outer occurrence to actual matching inner traces, preserves multiplicity,
and retains the complete protected searched relation population for negative
results. The binder checks emitted count against the exact trace bag. Missing
privilege or incomplete execution never publishes a result.

This is a deliberately bounded provenance qualification inside the approved
composable interface, not a query template or product availability decision.
EXISTS in projection, aggregate/set/window/limited inner queries, broader inner
predicates, scalar/IN subqueries and bounded recursive CTEs remain
`witness_qualification_pending`; they receive no empty or inferred witness.
No migration, privilege expansion, numerical policy or approved contract blob
changes. Full installed qualification and useful W1/W7 fit remain separate
requirements; small fictional EXISTS cases cannot certify capacity or semantic
answer quality.

The first installed 3.13/3.14 convergence at `608ebd6` retained 18 failures
per lane: a post-compilation guard mistook trusted EXISTS guards for skipped
INTERSECT/EXCEPT arms for unqualified agent-authored EXISTS. Moving that check
to the original admitted tree restores the existing set witnesses without
loosening the qualification boundary. A focused regression also caught a
projection EXISTS with no outer WHERE slipping past an absent-clause identity
check; the corrected guard explicitly requires the owning WHERE. The failed
installed logs remain historical evidence, not qualification of the repair.

The next no-migration internal continuation starts from owner-merged #62 at
public main `a8709f8334ed7757f6695effe451fde858e54c0e`. Its single claim is
**native scalar and three-valued IN/NOT IN subquery witnesses under the existing
private atomic result commit**. A qualified inner SELECT projects one source
column from one versioned logical relation and uses row-level equality/null
predicates; the outer SELECT remains row-level. IN evaluates every selected
inner occurrence's native equality truth, retaining the positive match bag,
false/null exclusions and whole protected searched population. Match and
unknown counts are checked against the emitted comparison bag; NULL with an
empty set is false, while a positive match wins over unknown comparisons.
For IN in WHERE, a tested-row ledger records each candidate's native
true/false/unknown predicate truth even when no row is returned. Unused CTE
ledgers are pruned; an empty-left INTERSECT/EXCEPT does not force its right
ledger. Correlated and uncorrelated IN forms can compose with CTE and set
branches. Surrounding row predicates that cannot be safely evaluated for
every candidate remain pending.

Direct-projection scalar subqueries retain the searched inner bag and allow
PostgreSQL's original scalar expression to enforce zero-row NULL and native
multirow cardinality errors. The witness scan and value expression may both
evaluate the inner relation within the one restricted statement; actual work
is charged to the unchanged deadline rather than claimed as one inner scan.
No result is published after cardinality error or interrupted binding. Other
scalar contexts, richer inner queries, grouped/windowed outer SELECTs and
bounded recursive CTEs remain witness-publication pending. There is no public
result/disclosure API, migration, privilege expansion, policy increase or
approved-contract change. Complete installed receipts belong to this PR;
fictional mechanical cases do not qualify W1–W7 capacity or semantic answers.
The first tested-row development probe exposed a ledger that was built but
pruned from an empty output. The repair retains an unowned top-level ledger,
retains a CTE ledger only when its owner is reachable, and preserves the
existing empty-left set-arm evaluation fence. Its failed log is retained.

The next no-migration internal continuation starts from owner-merged #63 at
public main `082ea10013c2c01e6cb3ec0540fb616593692762`. Its single claim is
**bounded native CYCLE path witnesses over prepared assessed-relation rows,
atomically published as private results**. Admission still requires the
declared 1–8 depth bound, visible typed anchor, one UNION ALL self-reference,
key-equality edge expansion, independent guard, native CYCLE and qualified
support roots. The independently restricted reader uses bigint arithmetic and
comparison for the depth literal; its exact native-CYCLE function allowlist
adds only `record_eq(record,record)` and
`array_cat(anycompatiblearray,anycompatiblearray)`. Neither is agent-callable
through closed SQL admission.

The recursive CTE remains native inside a materialized wrapper. Each seed and
edge occurrence carries its original scan/join/filter trace and parent path;
an explicit ledger enumerates every native CYCLE row, including cycling and
depth-bound rows even when the outer SELECT selects a subset. Binding checks
each node, iteration depth, parent progression and native cycle flag against
the traced route. Duplicate anchors and diamond paths retain bag multiplicity;
an empty result still retains the prepared protected population and the
explicit depth coverage. Timeout, cancellation or witness overflow refuses
the whole result. The private commit binds the request's exact recursion
declaration to execution metadata and coverage before publishing immutable
partitions. Restarts and lost acknowledgements use the existing private
receipt/owner recovery; no disclosure API is activated.

This cut qualifies the currently prepared `memory_v1.assessed_relations`
anchor/edge population. Admission of an observation-seeded or correction-edge
query alone does not make its unprepared relation executable; richer recursive
arms and unused recursive CTEs refuse witness publication before reader
dispatch. Canonical assessed lifecycle and protection remain PR-03-owned.
No migration, approved-contract change, provider call, policy increase or
public query/result access is included. Full installed artifact receipts and
useful W1–W7 fit remain separate gates; fictional path correctness is not
semantic answer or capacity certification.

Development initially demonstrated the existing `unprepared_relation` refusal
for an observation seed. A restricted-reader probe then exposed implicit
bigint/integer depth operators and PostgreSQL's native CYCLE functions outside
the exact authority allowlist. The repaired admission casts only the already
verified structural depth literals to bigint; the two exact CYCLE built-ins
are fingerprinted by the independent authority profile. An initial 8192-byte
budget test expected a settled invocation but exhausted before dispatch; it
now checks no invocation was created. These failures are not qualification
receipts for the repaired head. A later filtered-path development assertion
hard-coded seven tested paths after a high-degree fixture had added 20 more;
the corrected case compares the filtered result with the complete independent
native path population and verifies every unselected path remains in the ledger.

Seen acceptance compares native values/schema/order to the witnessed statement,
then independently checks contributions and multiplicities, empty/all-null facts,
rejected groups, filtered unknown truth, compositional groups/sets, pinned C
collation/numeric equality, original native errors, unused CTEs, missing privilege,
cancellation/exhaustion during binding, atomic replay and fresh-process recovery.
Qualification receipts belong on the PR. All development failures remain retained,
including missing closed-profile helper privileges, an untyped helper-zero
comparison, collation-name harvesting, lost bare projection names and an invalid
fixture that attempted arithmetic on a semantic revision reference, a stress
fixture exceeding the existing 16 relation-reference ceiling, and forcing a
natively skipped empty-left set arm. These were
repaired without broad grants, admission reduction or resetting consumed work.

Typed ordered rows, original validated request/fingerprint, admitted program,
catalog/policy/independently checked privilege-profile pins, frame, coverage,
complete logical row population, exact canonical lifecycle bytes and evidence
bindings are sealed together. Canonical lifecycle records retain their original
numeric encoding in byte-preserving partitions, not a JSON float round-trip.
The result-json-v1 digest covers immutable schema/rows/query/frame/lineage/coverage
and witness hash; it excludes creation/delivery receipts and changing work state.
These private records are not yet the available wire response or admitted evidence.

Publication uses a fresh short trusted authority transaction while the original
source frame is held. Its repeatable-read snapshot cannot see a journal created
later by M0033, so publication independently checks that settled invocation and
the exact original issuer PID/start/virtual-transaction epoch. It also checks owner,
credential, query key/fingerprint, manifest and byte/digest pins. Sealing, result
identity and creation receipt commit atomically through M0031's single store;
there is no second body/allocation ledger. The acknowledgement follows commit.
The original deadline governs construction/publication; the next SQL statement's
timeout is set before dispatch. Publication records elapsed wall time since native
settlement separately. This is neither CPU/I/O measurement nor complete run work
accounting. A failed/uncertain write never grants ownership takeover or new SELECT.
Exact redelivery and authenticated private creation-receipt recovery require no
original snapshot or query rerun; they grant no result-byte disclosure. An unfinished
commit after the original source frame ends remains unavailable/pending.

Creation stores a fixed 30-day standalone deadline. No paging, standalone access,
expiry cleanup, saved-input/parent composition, checkpoint/named-save/restore hold,
whole retained-closure reauthorization or governed erasure API is available here.
Private owned receipt recovery is executor bookkeeping, never a model-facing
metadata route. Availability remains gated by all those surfaces, complete witness
qualification, actual host credential isolation, durable cumulative accounting,
physical storage qualification and useful W1–W7 fit. Existing M0031 parent holds
remain unchanged; M0034 does not claim to deliver refinement or expansion.

Seen fictional acceptance checks native bag multiplicity, both sides of outer
nonmatches, empty-filter population/predicate preservation, digest/frame/fingerprint
refusal, original-frame loss, deadline exhaustion, immutable bodies/receipt ACLs,
crash after sealing with no partial result, and restart/lost-response/concurrent
redelivery without native redispatch. Installed qualification and exact-head CI
belong on the PR. Earlier failed packaging/type/import/encoding fixtures remain
recorded; no retry erases a failure or establishes measured useful workload fit.
Broad review of `819e59a` identified missing OFFSET subset metadata and incorrect
base-table/partial CTE/derived alias handling. Three seen regression cases fail at
that exact installed head. The correction records both LIMIT and OFFSET with their
ordering/exclusion basis, scans canonical keys behind base aliases, and pads
partial alias lists before naming the hidden trace. All three corrected cases pass
without rejecting previously admitted SQL. The superseded head's in-progress
qualification is retained as historical evidence; it cannot qualify the repair.
That historical installed run also exposed stale resource assertions for 33
migrations and 68 modules. They now require 34 migrations and 71 modules; the
out-of-range refusal tests version 35. Its four failed assertions and all original
logs remain preserved, rather than being reported as passing convergence.

M0031 reserves one private ownership identity for an authenticated `(run,step)`
and request fingerprint before execution. Exact retry returns that same journal
identity; different semantic inputs conflict. No query dispatch or ownership
takeover exists. A restarted executor must recover/settle the owned operation,
never interpret a replayed reservation as permission to execute the query again.

Sealing atomically commits canonical content, witness and protected-dependency
partitions, their domain-separated private artifact hash, exact ordered parent
pins/holds and the private mutation receipt. Partition lengths enter the private
hash. This hash is deliberately separate from the approved public result digest;
no wire digest, result identity, creation time or disclosure deadline is selected
by this preparation layer. Parents are immutable and stored once. There is no
ancestry-depth cap. Malformed or noncanonical Python inputs are rejected before
the trusted database write. The SQL routine independently checks bytes/hash,
ownership, parent pins, authentication, shared authority fence and snapshot.

All four tables have forced RLS and no application/public table grants. Only the
three private write functions are granted to the trusted application role; no
read function, agent catalog entry, raw bytes, rows, counts or metadata disclosure
is added. This is a trusted executor dependency, not an agent capability. The
actual restricted query login still requires independent privilege qualification.
The canonical lifecycle/shared-frame API supplies authentication fencing; this
does not certify complete dependency authorization or provenance construction.

Workspace allocation serializes reservation, sealing, parent holds and discard.
A changed allocator row makes a stale repeatable-read snapshot serialize-fail
instead of counting pre-wait allocations. Recover the same private command in a
new fenced snapshot; there is no automatic query rerun. Reservation targets use
the approved 64 MiB/result, 128 MiB/run and 512 MiB/workspace ceilings. Shared parents
are retained once. Discard refunds body retention, preserves cumulative new
allocation and charges the remaining journal. A held parent cannot be discarded;
the internal discard is neither governed erasure nor query cancellation settlement.
Discarded keys refuse every reservation retry, including original inputs: cleanup
removes the protected fingerprint, so no tombstone is reported as an exact-input
replay or fresh execution grant. Repeating the owned discard remains idempotent.
It cannot be used to refund work while any remote execution is unconfirmed.

The current preparation charge is encoded partition bytes plus a minimum 8 KiB
control-state charge and 512 bytes per new parent hold. This is an internal minimum
charge, **not a qualified physical allocation profile**. It cannot support an
available result or a measured-physical-bytes claim. Actual physical overhead,
owned growth/WAL, cumulative database/transport accounting, abandoned preparation
cleanup and confirmed cancellation settlement remain integrated-executor
qualification obligations. No operational capacity guarantee is made here.

Seen fictional acceptance covers exact bytes/partition identities, duplicate-key
and alternate encoding refusal, durable restart/lost-response identity, immutable
parents and >20 continuations, wrong owners/tenants/pins, table/function ACLs,
authority-frame bypass, overflow without publication, crash injection between
body/holds/receipt, retained cumulative charges and simultaneous/stale-snapshot
allocation. Installed wheel/sdist and exact-head CI receipts belong on the PR.

The first strict-type check caught a test fixture accidentally shadowing
`unittest.TestCase.run`; the fixture field was renamed to `run_ref` before any
database acceptance. Installed fixture failures and corrections are retained in
`pr-05-preparation-failures.txt`: password-redacted reconnects, the immutable-change
error class, repeating the one-time bootstrap and a LIKE placeholder. The fixtures
were repaired without changing canonical behavior. No failure was considered
passing acceptance or runtime capacity evidence. Existing historical failures and
all SQL 0001–0030 bytes remain.

Remaining integrated delivery includes the remaining logical populations and
witness shapes, atomically available immutable results, paging/hydration,
current whole-closure authorization and revocation/erasure, explicit contexts and
checkpoint/save/restore holds, concurrency/settlement/work/storage and measured
useful W1–W7 fit. No observation-only substitute or lifecycle duplication, model/
provider call, holdout access, default candidate/fallback, owner data, CLI expansion,
production checkpoint, deployment, merge, version selection, release or Desktop
consumption. Mechanical preparation proof is not semantic-quality certification.

## Private saved-result closure gate (schema 35)

This forward-only continuation starts from owner-merged #64, public main
`63a0f963f7377d38ae03c685b64d9759a86eaf8f`. Its one acceptance claim is
**a saved standalone result can be privately recognized only for its original
owner identity and entire currently authorized protected dependency closure**.
M0035 captures principal kind, user/on-behalf-of and pairing-grant identity in
the same transaction as the M0034 result, witness and receipt. Results created
before that binding exists remain unavailable to this gate; a later caller
cannot supply an identity retroactively.

The private check runs inside PR-03's session-owned authority fence and a fresh
repeatable-read frame. It validates the original result and partition hashes,
exact canonical protected-record/manifest binding, original owner and standalone
expiry, then asks the unchanged PR-03 population projector for the *current*
authorized historical dependencies at the result's original `known_at`. Every
saved dependency pin must still be present. A later authorized record does not
change old rows, hashes or historical counts; loss of one source or event pin
refuses the entire result, including an aggregate. Regrant can restore this
private verdict only while the original standalone route is still valid.

The check returns only a Boolean verdict to trusted application code. Neither
the restricted SQL login nor the agent receives result bytes, metadata, pages,
facts, citations or an access receipt. This gate deliberately has no checkpoint
context, descendant retention or public reuse. Reprojecting the entire current
population is conservative and may cost more than a targeted checker; it is not
W1–W7 work fit. Physical allocation, durable cumulative work/transport, governed
erasure and cleanup, atomic disclosure receipts and context-bound paging remain
prerequisites before any result becomes available. Unsupported observation or
correction recursive populations and richer SQL witness shapes stay explicit.
No candidate default, contract change, semantic-quality claim or release follows.

Focused fictional installed checks cover exact-owner restart without redispatch,
same-workspace alternate-principal refusal, source revocation and regrant of a
stored aggregate, retained bytes after standalone expiry, missing identity,
late-assessed historical addition and crash injection into identity capture with
no partial result. The first development run was blocked by sandboxed localhost
network access; the escalated draft run exposed a reused fictional evidence key
and an unavailable delegated-grant fixture. Subsequent drafts exposed a bad
fixture expiry timestamp and a fixture DDL change inside a held projection
frame. Those are preserved as failed development logs; exact-head installed
qualification and CI are required separately.
