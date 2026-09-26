# Relation assessment after acceptance

Schema 29 adds `memory.semantic.assess-relations` revision 1: a separate relation
task that runs only after a bead version is accepted, and only against immutable
accepted versions. A relation task never adds statements, never changes a bead and
never vetoes acceptance. A refused, failed, cancelled or paused relation task leaves
every bead and receipt untouched and keeps its own visible status. It uses the
existing queue, authorization, fences, model accounting and receipt ledger; there is
no second queue or executor.

Revision 1 is limited to relations. Beads keep recording decisions, assertions,
conditions, attribution and uncertainty as statements; tracked claim records and
claim updates after acceptance wait for a later revision. Revision-6 authorship,
its claims, relations and coverage keep their meaning and are still readable.

Code validates identity, pins, evidence, authority and integrity. It never infers a
relation, votes between models or picks a winner.

## Activation

`activate_relation_assessment_v1` (`ActivateRelationAssessment`, contract version 1)
pins, in `relation_assessments` and the task payload:

- the subject bead's accepted version;
- up to 8 explicitly supplied candidate beads' accepted versions;
- 1–32 relation types, each the exact latest active revision with its full
  definition; and
- the evidence units behind every pinned statement.

The activating principal needs maintain authority over the subject, which it may
write, and every candidate, and raw-source authority over every evidence source.
It must keep that authority while the task runs: every later check repeats it for
the activating principal, separately from the worker's or attestor's own authority.
Candidates are supplied coverage, not a relevance ranking; coverage never searches
other beads. The same idempotency key with a different request is a conflict,
never a silent re-pin. Pinned beads are bounded at 131,072 canonical bytes,
vocabulary at 32,768, and evidence at 72 units and 131,072 declared characters; a
larger set is refused, never truncated. Pinned bead packets carry no tracked claims.

Each activation names an attestor policy, `relation_assessment_dispatch_policies`,
which a trusted operator provisions as for complete-input dispatch. No public
function creates one, and only its status may change, from active to revoked.

## Evidence

The author and the specialist receive the actual authorized evidence behind every
pinned statement, not only statement texts and identifiers. Evidence pins name
whole units, so each excerpt is an observation unit's text or every normalized part
of a complete unit's sealed package; there is no narrower span. Evidence stays
behind `read_relation_assessment_evidence_v1`, the worker's fenced read, and is
never copied into the task payload. Revision 1 uses fixed packets: interactive
source rereads inside the task are not part of it.

Authority is rechecked before hydration, immediately before each provider dispatch
(`authorize_relation_delivery_v1`), when a delivery is recorded and at apply. Each
check covers two principals separately: the current worker or attestor, and the
activating principal over every pinned bead, statement, evidence event and evidence
source, whichever scope it sits in. After each request the attestor, a
service principal other than the claimant, records what the request received with
`record_relation_delivery_v1`. It re-derives every excerpt from storage under its
own authority and compares the packet byte for byte with the pins. Acceptance
requires the author's recorded delivery (`relation_assessment_exposure_valid`) and
any supervised usage within its reported stops.

## Author output

The author returns proposals and a disposition for every pinned pair.

- **Endpoints.** A pinned accepted bead version and 1–8 of its statements. Neither
  endpoint has to be the subject, and both may sit in one bead when their
  statements differ. The stored row reads "source *forward reading* target".
- **Basis statements.** Up to 8 existing statements, separate from both endpoints,
  from any pinned bead. They state or support the relationship and carry its
  attribution; there is no separate speaker field. A `source_stated` assertion
  names at least one.
- **Evidence.** 1–8 exact statement-evidence pairs of the proposal's own endpoint
  or basis statements.
- **Type, basis and qualification.** One pinned `{key, revision}`,
  `source_stated` or `agent_inferred`, a required rationale, an optional
  qualification for material conditions, scope and hedging, and a 0–1
  `author_confidence` with at most two decimals. Confidence is a diagnostic,
  never authority.
- **Coverage.** Every unordered pair of pinned beads, each bead with itself, is
  `related` (requires, and alone permits, a proposal between them),
  `not_related`, `abstained` with `no_fit`, `insufficient_evidence` or
  `ambiguous`, or `not_assessed` with a reason. Not assessed never means
  unrelated, and a pair's disposition does not show that every statement pair in
  it was considered.

The task binds only statements that already exist in the pinned versions. When
they cannot represent an assertion truthfully, the author abstains; the remedy is
reauthoring, never a new statement.

## Specialist and agreement

A provider-neutral specialist, `memory.semantic.relation-specialist`, assesses each
exact proposed assertion (its endpoints, basis statements, attribution,
qualification, rationale, predicate, revision and direction) against every pinned
definition and the same evidence the author received. For each proposal it returns
whether that exact assertion is consistent, the set of predicate, revision and
direction triples it finds warranted, or one abstention. Its run is a separate,
attributable child run; it writes nothing, and the attestor records its decision.

Code accepts a proposal only when the specialist finds that exact assertion
consistent and its warranted set contains the author's predicate, revision and
direction. Two runs choosing the same predicate while differing on what relates to
what, or under which conditions, is a disagreement. A disagreement or an abstention
leaves the proposal stored as `not_accepted` with both contributions; it is never an
assertion. An agreed assertion needs no further model call. There is no retry
loop, relabeling or code voting.

The first profile batches every proposal into one specialist turn. Batching is a
profile choice, not part of the judgment contract: every proposal is judged exactly
once across the contributions. An incomplete or oversized batch is refused
truthfully, never truncated.

**Reconsideration** is a new, explicitly linked activation (`reconsiders_task_id`)
of an applied assessment of the same subject that left at least one proposal
unaccepted. Its input carries that recorded disagreement: the unaccepted proposals
and their judgments. It pins every bead the earlier assessment pinned, at the same
immutable accepted versions, so the carried disagreement names only statements the
new task pins and authorizes. When evidence or understanding has changed, it may add
candidates and pin newer vocabulary revisions. No count limits reconsideration: an
assessment may be reconsidered again and a reconsideration may itself be
reconsidered, each by its own explicit, separately authorized and bounded
activation. Every link and every earlier proposal and judgment stays recorded.
Nothing activates a reconsideration automatically.

**Attempts.** Each relation task runs at most one attempt, so nothing retries
automatically. A transient failure or an expired lease dead-letters the task, and
a task paused during its attempt is never resumed. Running the assessment again is
a new, explicit activation.

## Correction, cycles and roots

An accepted proposal may retire one earlier active assertion of either kind whose
endpoints are among the pinned beads, in the same apply, for example to replace it
with the opposite direction. The retirement is an authored, final lifecycle event
with the task's provenance; the retired assertion reads as `superseded` with its
replacement named, including in the revision-1 relations read.

One cycle check, `relation_cycle_closes_v1`, covers authored and assessed
assertions under the existing per-tenant, per-key lock. Retracted, superseded,
retired and unaccepted assertions never count. Revision-6 authorship uses the same
check.

Accepted assessed `derived_from` between different beads feeds bead-level
derivation roots, like revision-6 relations. Statement-level roots within one bead
are not computed, and partial-scope correction is not supported.

## Apply

`apply_relation_assessment_v1` (`ApplyRelationAssessment`, contract version 1)
commits proposals, judgments, statements, evidence links, pair dispositions and
retirements in one transaction with the task receipt. It requires the exact run
tree: the author's root run and one settled specialist run per contribution, each
contribution exactly as its attestor recorded it. Every refusal of authored content
is a data error, so the attempt settles as invalid output. A lapse of either
principal's authority over any pinned bead, statement or evidence source, or of the
attestor policy, pauses the task instead, and accepted beads stay untouched.

## Storage

`relation_assessments`, `relation_assessment_deliveries`, `assessed_relations`
(every proposal, exactly as proposed, with its judgment and acceptance),
`assessed_relation_statements` (source, target and basis roles),
`relation_pair_dispositions` and `relation_retirements` are append-only with forced
row-level security and no role grants; definer functions own every read and write.
They reuse existing statement identities and create no proposition store.
`semantic_evidence_links` accepts the `assessed_relation` owner kind.

## Reads

`inspect_bead_relations_v2` (`InspectBeadRelationsV2`) returns, around one accepted
bead as known at a time:

- revision-6 relations and assessed proposals where the bead is an endpoint or
  supplies a basis statement, with endpoint and basis statements, evidence and
  independent roots, the specialist's judgment, derived state and lifecycle
  events;
- every relation type revision they name, once, with its full pinned definition,
  endpoint rule, readings and evidence expectation;
- pair dispositions involving the bead and revision-6 candidate assessments; and
- the status of the relation tasks whose subject it is.

Every disclosed bead, including basis statements' beads, and every disclosed
statement and evidence item is reauthorized; any unreadable dependency makes the
whole read unavailable. `inspect_relation_vocabulary_v1` lists the active relation
type revisions with their full definitions.

## Supervised dispatch

A supervised qualification for a relation task may admit a managed turn for the
specialist's second route, not only a hard-bounded call. Every other rule of
supervised dispatch is unchanged, and no provider, credential, profile binding or
default composition is included. Composition binds the author and specialist
profiles explicitly.

## Forward lifecycle completion

The owner approved completing governed assessed-relation lifecycle as a near-term
dependency of relation-aware retrieval. This section is a forward scope, not a
claim that revision 1 implements it. Replacement alone is not withdrawal.

The next contract must provide append-only confirmation, dispute and retraction
for assessed assertions without mutating accepted bead authorship or obtaining a
new model judgment. Before interface freeze, specify eligible actors and current
delegated authority, reason/evidence requirements, legal transitions, optimistic
concurrency, idempotency and recorded/effective-time semantics. Preserve existing
acceptance, replacement, specialist attribution and historical receipts.

Qualify one consistent current/as-of interpretation across authorized inspection,
retrieval eligibility, traversal, applicable evidence roots and cycle checks. A
retracted assertion cannot remain current support through another read path;
historical inspection must preserve what was known at its frame. Dispute must
remain visible as uncertainty, neither erased nor silently treated as settled.
Endpoint and basis-statement authorization still apply at access and write time.

Acceptance needs installed-artifact tests for tenant isolation, revocation,
concurrent governance, replay, every transition and agreement among those read
paths. Use a forward contract/migration; do not rewrite historical SQL or relabel
the existing human governance route for bead relations as assessed governance.
Release the qualified public change before product composition or relation-aware
retrieval consumes it. The exact state/admission interface remains an owner
decision before freeze.

This cut does not require tracked claims, partial correction, statement-level
derivation roots, specialist reconsideration or maintenance automation. Their
separate limitations remain; applicable roots here means only qualified existing
root semantics. Nor does this mechanical cut certify semantic relationship quality.

## Limitations of revision 1

- No tracked claims or claim updates after acceptance.
- Fixed packets; no interactive source rereads inside the task.
- No statement-level derivation roots and no partial-scope correction.
- No governed confirm, dispute or retract actions for assessed assertions. An
  accepted replacement can retire one, but nothing can simply withdraw it.
  Revision 1 is therefore an explicitly incomplete mechanical substrate:
  append-only dispute and retraction for assessed assertions must exist before
  assessed relations are used durably in live memory or qualified for recall.
