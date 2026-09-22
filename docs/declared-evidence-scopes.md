# Declared evidence scopes (schema 25, published in 0.0.9)

An explicitly authorized producer can materialize a source-local declared evidence
scope without asserting a native turn, terminal, conversation, branch or parent.
The new `memoriesql.declared-evidence-scope.v1` contract reuses evidence packages,
canonical materialization, the existing semantic task and source-revisiting path,
and immutable canonical apply. It adds no model, executor, queue or evidence store.
This change is published in 0.0.9.
Verified publication must precede downstream consumption.

## Declaration and truthful completeness

The caller persists a `DeclaredEvidenceScope.scope_id` and reuses it on retry.
Create a normal v1 evidence package with `occurrence_key=scope.occurrence_key`,
`occurrence_identity_basis=producer_assigned`, and the same `boundary_basis`.
The native boundary and `source_completeness` remain `unresolved`, topology is
`unknown`, and every declaration-level native fact is null. A declared selection
is not a native event: session, branch, participant, role, ordering and timestamps
must not be promoted onto its canonical event. Known facts on individual evidence
parts remain preserved; they do not become facts about the scope as a whole.
Complete physical records, complete normalized scope input and no unresolved
**required scope coverage** are necessary. A physically pending package may still
be retained/sealed, but cannot be materialized by this route. Core verifies the
inventory and raw derivation; a qualified producer remains responsible for truthful
source-format/physical-record assertions. Core does not parse arbitrary formats.

`MaterializeDeclaredScope` (materialization version 3) binds this exact sealed
inventory and declaration. Its policy must explicitly enable `allow_declared_scopes`
and supply the existing approved source-stable namespace. The new flag defaults to
false. The authenticated producer, current source authority, qualification and
normalization policy, scope, schema and expiry must all match. Installation grants
no production trust. No inference, exposure or acceptance follows from sealing or
materialization; a thin bead and unavailable binding task are created atomically.

`episode_completeness=unknown` remains explicit in the scope declaration. Supplying
all records in the declared scope proves neither complete episode coverage nor
semantic sufficiency. Reader/acquisition windows, role alternation, token budgets
and equal text do not create declarations or determine proposition boundaries.
One bead can hold multiple independently inspectable authored statements, including
necessary attribution, qualifications and uncertainty. No significance classifier,
mandatory segmentation agent or replacement authoring policy is introduced.

## Identity, overlap, carry-forward and amendments

The source-local key is `(tenant, source_object, approved_namespace, scope_id)` in
the existing materialization ledger, with a distinct declared-scope hash domain.
Native v2 keys and first pins are unchanged. Native/scoped bindings may coexist in
an already compatible approved namespace; legacy/different-namespace transition
fences remain intact. A scope does not relabel or replace a native binding.

Replays and repackaging use the same persisted declaration. The original package,
unit, bead and task remain pinned. Same-revision ordered raw membership is checked
independently of receipt/chunk/part splits; normalized component order/content and
facts must also remain identical. Moving an equal-text selection within a revision
conflicts. This is consistency checking, not content-derived identity. Distinct
declarations may overlap or contain identical text and still represent distinct
observations. No universal semantic duplicate detector is implied.

Initial scoped authorship does not require native identity or cross-revision
matching. Reusing a bound scope with another raw revision requires explicit
`ScopeCarryForward` referencing the **original** package and recording the qualified
producer's correspondence basis. Core verifies that existing binding and unchanged
normalized component content/facts; it does not independently discover or certify
record equivalence. Missing correspondence rejects the carry-forward claim, not
initial scope creation. Changed content conflicts even with an assertion. The
carry-forward is retained in the shared operation receipt; it never replaces first
pins. A same-revision carry-forward or an assertion without a prior binding fails.

A real boundary amendment declares a new scope and names the prior scoped unit
plus its reason. The target must be a bound scope in the same authorized source,
workspace/scope and namespace. That append-only lineage is preserved in canonical
binding/event metadata and inspection; it is not a source-native parent or automatic
semantic supersession. Existing accepted meaning cannot be overwritten. Use the
existing explicit correction/supersession path when changing accepted interpretation;
this slice does not add complete-input correction execution or choose a winning
observation merely because it is newer. Immutable package declarations and conflicting
membership under one scope identity continue to fail.

## Existing execution and versioned inspection

After separate explicit activation, existing revision-3/4/5 task payloads already
carry the immutable package declaration and producer-assigned event declaration,
including selection basis, unresolved boundary/completeness and unknown native
facts. No schema coercion into a native turn or new execution payload is needed.
The forward authorization helper recognizes only an actually bound, explicitly
qualified scope; every prior source, producer, dispatch, attempt, cancellation,
exposure, accounting and cleanup fence still applies. Native authorization keeps
its existing native-boundary/completeness checks.

Complete target exposure is mandatory. The same author controls authorized typed
revisits and can return incomplete if **required** evidence is unavailable or cannot
be inspected within budget. Optional context does not become mandatory coverage.
A model response, server read or confidence value does not attest coverage or prove
meaning. An incomplete/failed/cancelled attempt leaves retained evidence and its
thin observation without accepted meaning. No live transport or soft-budget admission
exception is introduced.

`PostgresEvidencePackages.inspect_scope(InspectScopedEvidencePackage(...))` is the
version-2 inspection projection. It returns unchanged v1 package status plus an
actual `scope_binding` and separate scope readiness. Old native readiness remains
`pending_source_qualification` for an unresolved-native scope; it is never silently
relabeled as native-qualified. `materialized_declared_scope` says a scope binding
exists, not that semantic authorship succeeded. Query the receipt's original bound
package to inspect that binding; a submitted repackaged archive is not a replacement
bound package. Existing inventory/raw readers and stored-bead inspection remain
unchanged, inference-free and currently authorized. Missing/protected package reads
remain unavailable. Producer revocation does not itself erase a human's independent
permission to inspect already retained evidence.

All declared-scope operations retain bounded existing transactions. Membership
verification visits at most 256 parts × 16 indexed raw ranges, coalescing adjacent
intervals without hydrating source content. Component consistency reuses the
existing at-most-16-MiB content hash. Package inspection uses an indexed binding
lookup; the adapter's existing response, statement and lock bounds apply.

## Qualification and limits

Fictional, network-disabled installed tests cover unknown-topology initial scopes,
Unicode across acquisition cuts, replay/repackaging/first pins, supported versus
unsupported carry-forward, distinct equal-text/overlapping scopes, amendments and
conflicting membership, physical tails, default-deny policy, native coexistence,
concurrency, authorized rereads, incomplete output, exposure, cancellation,
revocation and canonical acceptance. A scripted conditional shipping proposition
checks that persisted statements/render retain its qualifier and fallback. It does
not prove that a real model understands the source or discovers the right boundary.

The existing installed Python 3.13/3.14 acceptance lane qualifies both artifact
routes and preserves native-path regressions. Historical SQL 0001–0024, published
contract payloads and release inventories remain immutable. Unimplemented causal
relationship capability is not exercised; implemented evidence/identity/amendment
associations are tested only for their actual stated mechanics. Provider qualification,
private adoption/inspection, production trust/migration, publication and owner
checkpoint execution/verdict remain separate gates.
