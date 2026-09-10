# Immutable observations: forward contract map

This is the public-core implementation plan against public base
`d4e409d4daa4285ce6868274e6bb55593aa209f1`. It describes an unreleased schema-15
transition, not a publication or a product rollout. All examples and acceptance
fixtures are public-owned fictional material.

## Contract and caller map before implementation

| Surface | Current behavior | Forward behavior and compatibility |
| --- | --- | --- |
| `AcceptSourceEventCommand`, `accept_source_event` | Version 1/schema 11 captures one source occurrence, creates thin beads and enqueues canonical task revision 1. Receipts seal exact commands. | Keep version-1 payloads and receipts. Mark existing beads as initial, retain one initial bead per source unit, and select only initial beads during occurrence replay. Add an explicit version-2 capture entry point that selects the new initial-authoring task. |
| `ApplySemanticAnnotationsCommand`, `apply_semantic_annotations` | Version 1 can append statements and rich renders to an existing bead using an expected bead version. | Preserve the model, historical migrations and successful receipt replay. Unaccepted initial work remains valid. After schema 15, an attempt to revise accepted semantics fails explicitly and must use the correction command; never reinterpret that request as a new bead. |
| Version-2 initial-authoring command | Absent. | Author only unaccepted initial beads, with the existing evidence/output bounds. It cannot update an accepted payload. |
| Version-2 correction command | Absent. | Atomically create one distinct bead, its complete accepted payload and explicit links to one or more pinned accepted targets. Same evidence is permitted. Branches are not invalidated by another branch; reconciliation may target several branches. |
| Canonical task definitions and queue sink | Static revision-1 task, typed result, existing worker/fences/accounting. | Keep legacy task definitions and registry identity. Add an explicit opt-in static version-2 registry and dispatch its two result types through the existing canonical sink. No new scheduler or provider. |
| `beads`, `bead_versions`, statement/evidence/revision tables | One bead per occurrence; rows immutable, later semantic versions allowed. | Forward-only migration 0015 separates initial/correction identity and seals accepted semantics. Preserve every historical version, statement, watermark and receipt. No archive reauthoring. |
| Supersession lineage | Within-bead statement supersession, with a single successor constraint. | New bead-level lineage references exact accepted bead versions. Existing statement history is untouched. No unique successor, mutable winner, global CAS or automatic edge inheritance. |
| Authorization and task settlement | Trusted contexts, forced RLS, exact evidence hashes, attempt generation, cancellation/deadlines, tenant authority lock and atomic outcome/run settlement. | Reuse these guarantees, including rechecking after blocking locks. Validate both new evidence and every supersession target. Preserve old reads under current authority and deny stale fences without checking unrelated revisions. |
| Packaged migrations and artifacts | Explicit 14-migration inventory and released artifact checks. | Append one public-authored migration; keep all first-14 hashes and frozen preview records. Test installed wheel/sdist outside the checkout. Keep publication authorization pinned to the already approved release. |

## Storage and compatibility decisions

- A partial unique constraint identifies the initial bead for a source occurrence;
  correction beads use distinct IDs without relaxing initial replay uniqueness.
- A seal records the accepted version at upgrade or first successful authorship.
  Historical multi-version beads retain all rows and their existing current-version
  read. No existing version is renumbered, converted to a correction or deleted.
- Database guards reject new versions and semantic/evidence appends after a seal.
  Initial transaction writes complete before the deferred seal; a second semantic
  version within the transaction is also denied.
- Supersession targets must be existing, authorized sealed versions. The new bead
  cannot preexist, so lineage cannot introduce cycles. Targets need not be leaves;
  adding an independent correction does not invalidate another correction.
- Existing capture and semantic receipt namespaces retain their exact meaning.
  New version-2 operations use distinct receipt namespaces and explicit receipts.
- Read compatibility does not restore revoked authority. Version-1 accepted-bead
  mutation is an explicit schema-15 error, not a guessed translation. Consumers
  must opt into version-2 capture, task composition and correction after a separate
  authorized release; old initial tasks can finish through the legacy registry.

## Acceptance and stop conditions

Focused installed tests must prove occurrence replay before/after corrections;
immutable type/statements/render/evidence; same-evidence corrections; independent
branches and multi-target reconciliation; current authority and forced-RLS denial;
stale generation/deadline/cancellation and post-lock-expiry denial; unrelated-work
independence; exact old receipt replay; upgrade from a populated schema 14 with
multiple historical versions; and atomic failure/settlement behavior.

Keep all current limits: at most eight observations/targets, 4,096 hydrated
characters/evidence JSON bytes and the existing output budget. Run one complete
installed convergence lane and exact-head CI, followed by one broad review and
at most one focused rereview. Stop if preserving a receipt requires guessing its
meaning, capture duplicates initial beads, accepted history is rewritten, or a
new product/owner decision is needed. Publication, product adoption, providers,
UI, broader evidence handoff, claims/relations and checkpoint execution are outside
this public slice.

## Calling the opt-in runtime

Use `AcceptSourceEventV2Command` with
`PostgresCanonicalTransactions.accept_source_event_v2()` to select initial task
`memory.semantic.author-observations@2`. Its output is
`InitialObservationsPayload`; `AuthorInitialObservationsCommand` accepts only
`expected_bead_version=0`. A legacy capture still selects revision 1, including
when it is replayed through an existing receipt.

For correction, enqueue `OBSERVATION_CORRECTION_TASK` through the existing typed
`EnqueueSemanticTask`/queue adapter. `ObservationCorrectionInput` carries one new
bead ID, its existing observation event/source-unit anchor, and one to eight
`AcceptedBeadPin` targets. The normal manifest carries exact evidence hashes.
`ObservationCorrectionPayload` supplies the complete new meaning/type/render,
the same target pins and a reason. `CorrectObservationCommand` persists that
result. A target need not be a leaf, and its evidence may also support the new
bead. The task input and output must name the same anchor and targets.

`load_observation_task_registry()` is an explicit static composition choice. The
SQL admission pins use the existing empty-module `core` profile; the legacy
registry/admission rows are preserved. The two new task definitions retain the
existing budgets, agent mechanics and canonical outcome sink. These are internal
static composition mechanics, not a plugin platform.

Version-2 SQL entry points are `author_initial_observations_v2` and
`correct_observation_v2`. Their receipts include contract version, operation,
new bead IDs, target pins, statement/version IDs and the existing task/attempt
receipt identity. Separate idempotency namespaces prevent cross-operation
replay. They invoke the same fenced transaction engine used by version 1;
no command is translated into a different semantic operation.

## Database authority and read behavior

The forward migration restates existing functions because PostgreSQL stores
complete function bodies. Their task/attempt, authority-lock, post-lock clock,
evidence, run-tree and outcome-settlement checks remain in place. New checks
separate command versions, enforce initial-only writes, and validate correction
pins. `source_event_cardinality` counts initial beads, so correction branches do
not inflate occurrence counts. Historical version/timeline reads retain their
identities; no automatic winner filtering or edge inheritance is added.

Worker correction authority uses its existing `memory.maintain` capability. It
checks the target event and every evidence event through the pinned accepted
version's statement watermark, including superseded historical statements.
User reads retain the existing query authorization. New seal and lineage tables
have forced RLS; lineage reads require authorization for both endpoints. Neither
application nor worker roles receive direct mutation grants.

## Candidate and release boundary

`0.0.4` was absent from the public version endpoint during this lane's read-only
availability check. This is an unreleased candidate, not a reserved or published
version. The candidate inventory is separate from the unchanged published
`0.0.3` inventory. CI checks its deterministic bytes using `candidate-artifacts`;
the publisher still rejects versions other than the previously approved `0.0.3`.
A separate release authorization must refresh availability and exact-main
artifacts and authorize any publisher/tag changes. Product adoption follows that
release; this PR performs neither publication nor adoption.
