# Authored claims, relations and lifecycle history

Schema 27 lets the primary author of one complete unit optionally propose tracked
claims, relations to explicitly supplied accepted beads, lifecycle judgments about
those beads' claims, and an assessment of every supplied bead. Activation version 5
selects author-complete-unit task revision 6; apply version 7 accepts its distinct
output contract. Earlier revisions, contracts and SQL files keep their bytes and
meaning. Code validates identity, pins, evidence and integrity. It never infers a
relation, a conflict, a dependency or a winner.

## Binding decisions

- **Endpoints.** A relation connects the authored bead's accepted version and one
  pinned candidate's accepted version in the same workspace. The stored row reads
  "source *forward reading* target"; the author states direction as
  `from_authored` or `to_authored`.
- **Propositions.** Each endpoint names 1–8 statements of its own bead. The author
  may cite only statements of its bundle and of the candidate packets it was shown.
- **Evidence.** 1–8 references, each an existing statement-evidence pair
  (`source_unit_id`, `content_hash`) of one of the relation's named statements.
  Relations never cite free-floating source text.
- **Type.** One `{key, revision}` from the vocabulary pinned at activation.
- **Basis and uncertainty.** `source_stated` or `inferred`, a required rationale,
  an optional uncertainty note and a 0–1 `author_confidence` with at most two
  decimals. Confidence is a diagnostic, never authority.
- **Coverage.** Every pinned candidate is assessed `edge` (requires, and alone
  permits, relations to it), `no_edge`, or `unassessed` with a reason.
  Unassessed never means unrelated.
- **Claims.** Optional tracked propositions (subject, optional authored subject
  mention, slot, value, optional applicability) bound to 1–8 of the authored
  bead's statements. Valid time is inherited from source clocks on read; it is
  never invented.
- **Claim updates.** The author may `supersede` or `dispute` a pinned candidate's
  claim with a claim of its own bundle, or `reaffirm` it. Updates cite the authored
  statements that are their basis.

The whole canonical output is limited to 32,768 UTF-8 bytes. Relations, claims and
claim updates may be empty; an empty set is authored only when the accepted
version's own apply receipt is `complete_input.apply.v5`.

## Activation and candidate packets

`activate_complete_input_v5` pins, in the execution row and the task payload:

- up to 8 explicitly supplied candidate beads, each currently maintain-readable by
  the activating principal, rendered as a bounded packet (at most 32 statements
  with evidence pins, 32 mentions, claims with their state at activation, render
  title and summary, lineage and inherited source clock); and
- 1–32 relation types, each the exact latest active revision.

Candidates are supplied coverage, not relevance ranking. A different candidate set
or vocabulary under the same binding is a conflict, never a silent re-pin. Before
hydration and again at apply, the worker's current maintain authority over every
pinned candidate is rechecked; a lapse pauses the task before any provider
dispatch. Candidate packets are bounded at 65,536 canonical bytes, vocabulary at
32,768.

## Source clocks

Unit source time wins over event source time. When neither exists, the capture time
is reported with basis `capture_time` and is never occurrence time. Precision, unit
ordinal and event sequence are reported exactly as stored. A late-arriving bead with
an earlier occurrence is a new record with its own clocks; occurrence order and
recording order select nothing.

## Lifecycle as derived state

Lifecycle records are append-only. States are derived as of a known time:

| Record | Derived states |
| --- | --- |
| Claim | `current`, `superseded` (by every unretracted replacement: competing branches stay visible), `disputed` (while a dispute with an unretracted claim is open), `retracted` (terminal) |
| Relation | `active`, `disputed` (until a later confirmation), `superseded` (while the replacement is not retracted), `reassessment_pending` (an endpoint was corrected and no later confirm, retract or supersede exists), `retracted` (terminal) |

Governed commands record human judgments with their own receipts, optional exact
evidence and an optional compare-and-swap on the latest event:
`record_claim_event_v1` (`reaffirm`, `supersede`, `dispute`, `resolve_dispute`,
`retract`) and `record_relation_event_v1` (`confirm`, `dispute`, `retract`,
`supersede`). Services and agents change lifecycle only through an accepted
authored bundle.

Claims of a corrected bead are not copied or deleted. They stay as recorded and
expose `origin_corrected_by`. Relations whose endpoint was corrected become
`reassessment_pending`. Complete-unit beads cannot yet be corrected in core (see
[migrations](migrations.md)); the reassessment state applies to corrected
observation beads today.

## Evidence independence

Derivation roots are computed as of the known time. A bead's roots are the source
objects of the terminal beads of its active `derived_from` chains, or its own source
object. An evidence unit's roots are the union over the beads observing it. A
relation's `independent_root_count` is the size of the union over its evidence, so a
shared root counts once and a derivative of A and B adds no root of its own.

## Vocabulary governance

Eleven immutable built-in revision-1 types: `supports`, `contradicts`, `caused_by`,
`led_to`, `enables`, `part_of`, `depends_on`, `blocks`, `derived_from`,
`supersedes`, `associated_with`. `supersedes` is domain replacement, not the
correction or claim-currentness authority. There is no `resolves` type and no
selectable `superseded_by`.

A workspace type or a new revision of one enters only through
`propose_relation_type_v1` and a human `decide_relation_type_v1`, both requiring
workspace management authority. Revisions are immutable; an accepted inactive
revision retires the type for new activations while accepted relations keep their
exact historical revision. Built-in keys cannot be proposed or shadowed.

Core defines no relation family. Any grouping of types for navigation or display is a
separately versioned projection that never gates authoring, acceptance or truth.

## Inspection

`inspect_bead_relations_v1` returns one bead's claims, incoming and outgoing
relations, candidate assessments and authored claim judgments as of `known_at`.
Every disclosed bead, statement, evidence event and derivation root must be
currently readable; otherwise the whole response is `unavailable`. Responses are
bounded at 262,144 canonical bytes.

## Not included

- Specialist relation judgment. Revision 6 labels are attributed to the
  primary author's run only; a qualified specialist review is a later revision.
- Agent recall changes, neighbor or conflict query operations, inferred edges,
  ontology expansion, a proposal framework, provider bindings, production
  activation, migration deployment and live qualification.
