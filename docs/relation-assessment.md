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

**Status: exact contract proposal, approval pending (2026-09-26).** The owner
approved this bounded PR-03 dependency of relation-aware PR-05. Approval of that
direction, or merging documentation, does not approve the choices below. No
lifecycle runtime or migration accompanies this proposal. Revision 1 above
remains the implemented baseline. Replacement alone is not withdrawal.

Verified bases: public main `e8cfa0df3c1f8109c199a8126c0556624bc421b6` and Desktop
planning main `84f93ec272cc1e868efffb0765b8366bdc5bf987`, freshly fetched and with
the supplied merges verified as ancestors. This section owns the lifecycle
semantics; PR-05 owns its SQL catalog/result envelope in `agent-sql-results-v1.md`.
PR-05 consumes the canonical projection and does not derive a second lifecycle.

### Decisions requiring exact owner approval

The following defaults constitute one interface proposal. Material alternatives
are listed only to identify the decisions; none is implicitly authorized.

| Choice | Recommended contract | Material alternative / consequence |
| --- | --- | --- |
| L1 — Actor and authority | Authenticated humans only; current maintain/write on both endpoints, maintain/read on basis and judgment dependencies, and raw-source/read on evidence. Existing grants may authorize a human other than the original author. | Admitting paired agents/services would authorize autonomous lifecycle judgments and needs a separately specified decision/delegation interface. Requiring basis write authority would give every basis owner a veto over withdrawal. |
| L2 — Dispute and confirmation | One serial assertion history. Any L1-authorized human may confirm the exact assertion and close every earlier dispute at the compared head, including their own dispute. Confirmation requires exact evidence; dispute/retraction require a reason and may omit new evidence. | Independent adjudicators or separately open per-actor objections need additional authority/resolution rules and are outside this proposal. |
| L3 — Time, correction and finality | Governance acts when recorded; supplied effective time is attributed metadata. Retraction and replacement are final. Confirmation cannot clear a correction of a pinned endpoint or basis bead. | Scheduled/backdated operative changes or confirming across correction would add temporal transitions or renewed semantic acceptance. Neither is specified here. |
| L4 — Root qualification | Exclude ineligible derivations from support. Report a root qualification gap instead of certifying a new original source when disputed, corrected or withdrawn derivation leaves provenance uncertain. | Unconditionally falling back to the derivative's own source after withdrawal can manufacture apparent corroboration. This proposal deliberately withholds the independent count in that case. |
| L5 — Cycle reservation | Accepted nonterminal assertions, including disputed/pending ones, reserve cycle-forbidden topology. Retraction/replacement releases it atomically. | Releasing topology on dispute would permit an opposite edge and require cycle-refusal on later confirmation; that is a different transition policy. |

### Authority and evidence

The actor comes only from the current transaction's authenticated authorization
context; the command cannot supply a principal, tenant, workspace, user or
delegation. Human kind alone is insufficient. Credential, active principal,
membership, scope grants, capability, time bounds and source policy must all be
current. Explicit/workspace grants can authorize another human; ownership of the
original task, its worker credential or a past receipt grants nothing. Paired
agent, service and device credentials are refused even when acting on behalf of
a human. This cut adds no grant, pairing or authority-provisioning API.

At write time the human must have:

- `memory.maintain` read/write on each exact accepted endpoint bead version and
  its named statements; the existing accepted-bead checks cover acquisition,
  acceptance and supporting evidence events, not only scope membership;
- `memory.maintain` read on every exact basis bead/version/statement and its
  evidence events; basis write authority is unnecessary;
- authorized raw-source read on the assertion's evidence, every prior lifecycle
  evidence item needed to disclose its head/history, and all supplied new evidence;
- the same read checks over correcting/replacement dependencies exposed by its
  state. Unrelated candidates from the original task are unnecessary unless its
  full packet or coverage is being disclosed.

Reads use current `memory.query` and source-read authority over their entire
disclosed dependency closure, including endpoints, basis, lifecycle evidence,
corrections, replacements and root provenance. A timestamp never resurrects
revoked access. Missing, denied, cross-workspace and cross-tenant IDs share
`unavailable`; no IDs, counts, reasons, actors or receipt details leak on refusal.
There is no weaker withdrawal path when this closure is unreadable. Governed
writes do not require a qualified root count: a readable uncertain assertion can
still be disputed or retracted.
The human must also be authorized to inspect that compared head/history; a
maintain-write grant is not an implicit query or raw-evidence grant. The original
activator's later revocation does not revoke immutable acceptance or give it an
exclusive governance right; the current deciding human's own authority is checked.

Every action requires a nonblank human-authored `reason` of 1–1024 characters.
`confirm` additionally requires 1–8 evidence items; `dispute` and `retract` admit
0–8, so an objection or withdrawal need not fabricate supporting evidence. Each
item is `{statement_id:uuid,source_unit_id:uuid,content_hash:sha256}` naming an
exact existing statement/evidence pair in an accepted version of this workspace.
It may come from the assertion's pins or another accepted bead. Distinct pairs
are sorted by statement/unit UUID; duplicates, missing pairs and hash mismatch
are refused. No new statement, source text, predicate, confidence, endpoint or
specialist decision is created. Code checks binding and authority, not whether
the human's reason is persuasive. Event evidence never becomes assertion evidence
or a new derivation root merely by being cited.

### Write interface, storage and receipts

Proposed new contract ID `memoriesql.assessed-relation-lifecycle.v1`, version 1;
`record_assessed_relation_event_v1` / `RecordAssessedRelationEvent`. It targets
the next forward schema, provisionally 30 at these bases. A schema-number
collision must be reconciled and re-pinned before implementation approval.
The existing `record_relation_event_v1` still addresses authored relations only.
Schema 30 is a contract compatibility marker, not a release version. Require
that forward migration/capability; a later schema may support the unchanged
version-1 interface. No fallback to schema-29 replacement is permitted.

Closed request, with every field required (nullable means explicit JSON null):

| Field | Exact value/type |
| --- | --- |
| `contract_version` | `1` |
| `expected_schema_version` | `30` |
| `idempotency_key` | nonblank text, 1–512 characters |
| `relation_id` | UUID of an accepted assessed assertion |
| `action` | `confirm`, `dispute` or `retract` |
| `reason` | reason defined above |
| `evidence` | ordered array of exact pairs defined above |
| `effective_at` | finite timezone-aware instant or null |
| `expected_head_token` | required 64 lowercase hex SHA-256 from authorized projection |

UUIDs use lowercase hyphenated text, hashes lowercase hex, and timestamps the
existing UTC representation: `YYYY-MM-DDTHH:MM:SSZ` when microseconds are zero,
otherwise `YYYY-MM-DDTHH:MM:SS.ffffffZ` with exactly six fractional digits.
Use Gregorian years 0001–9999 and finite instants; input requires a `Z` or numeric
timezone offset and at most six fractional digits. Do not round extra precision.
This specifies `relation_packet_time` bytes without a source-code dependency.
Normalize request times to
UTC, evidence ordering and text types before hashing; preserve reason bytes
without Unicode normalization or trimming beyond nonblank validation. Reject
unknown fields, naive/nonfinite timestamps and a normalized request above 16 KiB;
never truncate. Evidence-order variants and equivalent timezone offsets denote
the same normalized request. A null head token is invalid, including before the
first event; the acceptance projection supplies the initial token.

The forward migration adds append-only `assessed_relation_events`, its exact
statement-evidence links, forced RLS, definer-only access, and one shared
projection. Each event stores `event_id`, `relation_id`, monotonically increasing
per-assertion `event_number` (first 1), `previous_event_id` (first null), action,
reason, effective/recorded times, actor principal/user, authorization-context
reference and receipt ID. Retain the exact accepted command and authenticated
decision context with its receipt. Context is historical attribution, never a
replay credential. Existing acceptance is event number 0 conceptually; its stored
row, receipt and all prior bytes are untouched. Existing `relation_retirements`
remain separate authored terminal events, joined into the projection.

Success receipt has exactly `{contract_version:1,relation_event_id:uuid,
relation_id:uuid,action,event_number:int,previous_event_id:uuid|null,
recorded_at:timestamptz,effective_at:timestamptz|null,
idempotency_receipt_id:uuid,head_token:sha256,state,support_eligible:boolean,
replayed:boolean}`. `state` and `head_token` describe the committed decision
frame, not a promise of current state on replay. One transaction commits event,
evidence links, attributed idempotency receipt and outbox event; no task, executor,
model call, specialist reconsideration or provider accounting is activated.

### Legal transitions and concurrency

For accepted assessed assertions, derive state in this order: terminal event;
open serial dispute; correction pending; otherwise `active`. Any correcting
successor of an exact pinned source, target or basis bead makes that dependency
pending, including a correction already visible when assessment was accepted.
All successor branches stay visible. Correction uses recorded bead lineage, never
an invented statement mapping. A dispute can coexist with correction pending;
`correction_pending` is a separate boolean and the successor pins explain it.
`not_accepted` proposals have no legal governance action and never become accepted
through confirmation. No automatic state change promotes them.
Successor dependency selection follows every visible branch transitively from
the pinned bead, with set-based cycle protection and the response byte bound;
no last-successor winner or hidden lineage truncation is permitted.

| Compared state/condition | `confirm` | `dispute` | `retract` | Existing accepted replacement's retirement |
| --- | --- | --- | --- | --- |
| `active`, no correction | append confirmation; stays active | disputed | retracted | superseded |
| `disputed`, no correction | active; closes all prior disputes at this head | append renewed dispute; stays disputed | retracted | superseded |
| Any nonterminal state with correction pending | refused: `reassessment_required` | disputed, pending flag retained | retracted | superseded |
| `retracted` or `superseded` | refused | refused | refused | refused |
| `not_accepted` | refused | refused | refused | refused |

Repeat judgments under fresh keys are events only for the nonterminal legal
rows; repeat retraction under a fresh key is refused. Same-key successful replay
remains possible under current authority. Retraction never revives what a
replacement retired; retracting that replacement also leaves the original
superseded. Confirmation preserves the original specialist judgment and
qualification. It resolves a human dispute of that same assertion, not a model
disagreement, correction or competing assertion. Rebinding meaning requires the
existing separately authorized assessment/reauthoring path, outside this cut.

`expected_head_token` compares acceptance, all recorded governance/retirement
events and correction lineage, not merely the last human event. Define its
manifest as `{projection_version:1,tenant_id,workspace_id,relation_id,
acceptance_receipt_id,acceptance,events,corrections}`. `events` is the complete
visible ordered array of `{origin,event_id,event_number,recorded_at}`: governed
events have their sequence; authored retirement has null sequence and terminates
the history. `corrections` contains every visible
`{role,pinned_bead_id,successor_bead_id,successor_bead_version_id,recorded_at}`,
deduplicated and sorted by role then UUIDs. Roles are source/target/basis.
Hash existing `canonical_json_bytes` of this closed manifest after the above
UUID/time normalization. Authority and effective times are not state tokens.
The token does not certify root qualification or authorize an operation.
For this interface that encoding is JSON with object keys sorted by Unicode code
point, array order preserved, no insignificant whitespace, ASCII string escaping
(`ensure_ascii=true`, lowercase hex escapes, surrogate pairs above U+FFFF), and
UTF-8 bytes without BOM or final newline. Escape JSON quotes/backslashes/control
characters; do not escape slash. Manifests contain only strings, integers,
booleans, null, arrays and objects, so no float serialization choice is hidden.

Serialize successful writes with the existing tenant authority/revocation fence,
an idempotency-key lock, sorted tenant/type cycle locks and sorted assertion locks,
in that order. Replacement apply and legacy governance must use the same
assertion/type locks and projector; correction commits must share a lineage fence
so correction cannot pass between head validation and commit. Acquire the lineage
fence with the authority fence before key/type/assertion locks, using a single
documented order across all affected commands. Recheck current authority and head
after waiting, immediately before the write. The first valid decision wins the
operational comparison; that does not select a semantic winner. A second distinct
key comparing the old head receives `head_conflict` without any event or receipt.
No automatic rebasing of its judgment. Retraction versus accepted replacement is
also serialized: exactly one terminal outcome; the losing assessment apply must
refuse without touching accepted bead authorship. Lost response after commit
replays the original receipt once, including after process restart.

Idempotency identity is `(tenant,workspace,actor_principal,
assessed_relation.event.v1,idempotency_key)`. Another actor cannot recover a
receipt through guessing a key. The normalized complete request, including the
head token, is hashed. Equal request returns the original event/receipt with
`replayed=true`; any difference is `idempotency_conflict`. Replay reauthorizes the
current complete closure before revealing or comparing a receipt, but does not
rerun transition, head or effective-time validation against the changed current
state. Missing/incomplete committed receipt is `receipt_incomplete`, never a
second decision. Refused/rolled-back writes reserve no successful key. Existing
receipt identities stay unchanged: the new operation stores as its internal key
SHA-256 of the canonical JSON array `[workspace_id,actor_principal_id,
idempotency_key]`, retaining the complete normalized command in the new event.
The receipt's tenant/operation plus this internal key implement the above scope
without altering legacy uniqueness. Qualify collisions/conflicts and replay.

Closed error codes: `invalid_request`, `schema_mismatch`, `unavailable`,
`idempotency_conflict`, `head_conflict`, `transition_invalid`,
`reassessment_required`, `receipt_incomplete`, `budget_exhausted`.
Authenticate and authorize before disclosing target-dependent conflicts. Use
`invalid_request` only for structural validation, `unavailable` for absent/denied
dependencies, `head_conflict` for an authorized stale head, `transition_invalid`
for an authorized terminal/unaccepted target, and `budget_exhausted` for timeout,
lock timeout or byte/lineage bound. Finite effective time later than the new
recorded time is `invalid_request`; an earlier instant is accepted only as the
human's attributed effective-time statement. Refusal leaks no partial content.
The write failure envelope is exactly `{contract_version:1,outcome:refused,
error:<one code above>}`. The adapter maps database refusal/timeout to that typed
envelope, without exception text or target details. An uncertain connection loss
is a transport failure, not a refusal receipt; recover/replay its original key.

### Recorded knowledge, effective time and shared reads

Governance is operative immediately on its database-recorded event. `effective_at`
is optional attributed valid-time metadata; it never schedules, delays, backdates
or reorders support eligibility. Null means unspecified, not inferred source time.
Keep it distinct from source occurrence clocks. A later-recorded, earlier-effective
retraction leaves a frame before recording active and withdraws support in a new
frame. This policy avoids rewriting already delivered historical knowledge.

Every read uses one short repeatable-read snapshot, one finite `known_at` recording
cutoff, and current authorization. Omitted cutoff resolves once to database now;
future cutoffs are refused. Include only assertion acceptance, lifecycle,
retirement and correction records visible in that snapshot and recorded at or
before the cutoff. Governed events follow their explicit sequence, including
timestamp ties; never use UUID recency as semantic authority. Frame manifests pin
actual visible acceptance/event/correction/evidence dependencies. A timestamp
alone cannot reconstruct past concurrent visibility or erased bytes. Replay of a
retained PR-05 frame uses its pinned manifest, not a new query at its old timestamp.
Acquire the current authority fence before establishing the data snapshot and
repeat authorization after any wait; an already captured snapshot cannot prove
current authority after revocation. A read/delivery linearizes at its final
authorized database handoff under that fence. Revocation that wins the fence
first refuses the entire response; later revocation governs every subsequent
access. Do not retain a database transaction through caller/model work.

One canonical projection serves v3 inspection, legacy state adapters, PR-05
retrieval eligibility, permitted traversal, existing root wrappers and cycle
checks. It exposes original immutable acceptance separately from derived state,
the compared head, correction flag and explicit eligibility. All six states remain
inspectable under current authority. Only `active` with no correction is settled
support. A disputed or pending assertion may be returned for uncertainty/history
with its reason and provenance, but cannot justify a settled traversal hop,
corroboration or an absence claim. Explicit uncertain navigation must carry the
state on every hop; it cannot project away that state and call the result settled.
Retracted/superseded/unaccepted assertions are history only and never support a
fresh current evaluation. Vocabulary deactivation changes new admission only;
it does not erase the assertion's pinned historical definition.

Proposed `inspect_bead_relations_v3` / `InspectBeadRelationsV3`, contract version 3,
request exactly `{contract_version:3,bead_id:uuid,known_at:timestamptz|null}`. Keep
the v2 success envelope, pair/task/candidate coverage and assertion fields, with
these exact extensions:

- response `frame:{known_at,snapshot_at,dependency_manifest_sha256}`;
- each assertion: immutable `acceptance` (`accepted` for authored rows),
  `acceptance_receipt_id`, `head_token`, `support_eligible`,
  `support_reason`, `correction_pending`, `basis_corrected_by:[uuid]`, and
  `roots_status:qualified|indeterminate|unsupported`;
- `independent_root_count` and each evidence item's `derivation_root_ids` become
  nullable; they are nonnull only when the corresponding roots are qualified;
- each event retains the v2 event fields and adds `event_number:int|null`,
  `previous_event_id:uuid|null`, `recorded_by_principal_id:uuid`,
  `recorded_by_user_id:uuid|null`, `idempotency_receipt_id:uuid`, and
  `evidence:[{statement_id,source_unit_id,content_hash}]`. Retirement remains
  `action=retire`, `origin=authored`, with null event number/previous event;
- each evidence item also carries `roots_status` and
  `roots_gap_relation_ids:[{kind:authored|assessed,relation_id:uuid,reason}]`, empty when
  qualified. Indeterminate/unsupported items retain their exact explanatory gaps;
  their disclosures require the same closure authority.

The closed row types below are the complete interface consumed by PR-05; prior
contract files/code names are implementation references, not additional normative
dependencies. `?` means required nullable field; `[]` means a JSON array. All
objects reject undeclared fields. `uuid`, `sha256` and times follow the encoding
above; `text` is a JSON string, `int` a JSON integer, `bool` a JSON boolean.
Projected author confidence is a finite JSON number with at most two decimal
places. For disclosed-row hashing encode 0/1 as `0.0`/`1.0` and other values as
their exact decimal with insignificant trailing zeros removed (`0.10` becomes
`0.1`), matching the existing bounded-confidence encoding. Other numbers in these
rows are integers; no unrestricted floating-point hashing profile is required.

```text
TypePin = {key:text, revision:int>=1}
Statement = {statement_id:uuid, bead_id:uuid, bead_version_id:uuid, text:text}
EvidencePin = {statement_id:uuid, source_unit_id:uuid, content_hash:sha256}
RootStatus = qualified | indeterminate | unsupported
State = active | disputed | reassessment_pending | retracted | superseded | not_accepted
RootGap = {kind:authored|assessed, relation_id:uuid,
           reason:disputed|corrected|withdrawn|replacement_gap|statement_roots_unsupported}
Evidence = {statement_id:uuid, source_unit_id:uuid, content_sha256:sha256,
            derivation_root_ids:uuid[]?, roots_status:RootStatus,
            roots_gap_relation_ids:RootGap[]}
Event = {event_id:uuid, target_id:uuid, action:confirm|dispute|retract|supersede|retire,
         related_id:uuid?, reason:text, origin:authored|governed,
         authoring_bead_id:uuid?, effective_at:timestamptz?, recorded_at:timestamptz,
         event_number:int>=1?, previous_event_id:uuid?,
         recorded_by_principal_id:uuid, recorded_by_user_id:uuid?,
         idempotency_receipt_id:uuid, evidence:EvidencePin[]}
Judgment = {proposal_id:uuid, outcome:assessed|abstained, consistent:bool?,
            warranted:[{relation_type:TypePin, direction:as_proposed|reversed}],
            abstention:no_fit|insufficient_evidence|ambiguous?, rationale:text}
Assertion = {kind:authored|assessed, relation_id:uuid, relation_type:TypePin,
             direction:outgoing|incoming|internal|basis,
             source_bead_id:uuid, source_bead_version_id:uuid,
             target_bead_id:uuid, target_bead_version_id:uuid,
             source_statements:Statement[], target_statements:Statement[],
             basis_statements:Statement[], basis:source_stated|agent_inferred,
             rationale:text, qualification:text?, author_confidence:decimal(0..1,2),
             evidence:Evidence[], independent_root_count:int>=0?,
             authoring_bead_id:uuid?, task_id:uuid?, author_run_ref:text,
             specialist_run_ref:text?, judgment:Judgment?, recorded_at:timestamptz,
             state:State, superseded_by:uuid[], endpoint_corrected_by:uuid[],
             basis_corrected_by:uuid[], events:Event[], acceptance:accepted|not_accepted,
             acceptance_receipt_id:uuid, head_token:sha256, support_eligible:bool,
             support_reason:not_accepted|disputed|corrected|retracted|superseded?,
             correction_pending:bool, roots_status:RootStatus}
TypeDefinition = {key:text, revision:int>=1, namespace:memoriesql|workspace,
                  label:text, definition:text, endpoint_rule:text, forward_reading:text,
                  inverse_reading:text, symmetric:bool, evidence_expectation:text?,
                  example:text?, counterexample:text?, cycle_policy:permitted|forbidden}
PairDisposition = {task_id:uuid, first_bead_id:uuid, first_bead_version_id:uuid,
                   second_bead_id:uuid, second_bead_version_id:uuid,
                   disposition:related|not_related|abstained|not_assessed,
                   abstention:no_fit|insufficient_evidence|ambiguous?, reason:text?}
CandidateAssessment = {candidate_bead_id:uuid, candidate_bead_version_id:uuid,
                       assessment:edge|no_edge|unassessed, reason:text?}
RelationTask = {task_id:uuid, subject_bead_version_id:uuid, candidate_bead_ids:uuid[],
                reconsiders_task_id:uuid?, status:text, status_reason:text?,
                activated_at:timestamptz, completed_at:timestamptz?,
                status_known_at:timestamptz}
Available = {contract_version:3, outcome:available, bead_id:uuid, known_at:timestamptz,
             frame:{known_at:timestamptz, snapshot_at:timestamptz,
                    dependency_manifest_sha256:sha256},
             relation_types:TypeDefinition[], relations:Assertion[],
             pair_dispositions:PairDisposition[], candidate_assessments:CandidateAssessment[],
             relation_tasks:RelationTask[]}
```

`acceptance_receipt_id` is the original relation-assessment apply receipt for
assessed rows, and the author-complete-unit acceptance receipt for authored rows.
Authored rows have null task/specialist/judgment, an authoring bead and no basis
statements; assessed rows have task/specialist/judgment and null authoring bead.
An assessed judgment is assessed with nonnull consistency/no abstention, or
abstained with null consistency, empty warranted and a named abstention. All
source/target/basis pins, qualifier and confidence retain their accepted bounds
and exact meaning specified above. Direction is relative to the inspected bead;
stored source-to-target orientation never changes for an inverse or symmetric
reading. In same-bead cases use `internal`; `basis` applies only if neither
endpoint is the inspected bead. PR-05 may query stored orientation independently.

`support_reason` is null exactly when eligible; otherwise follows state precedence
with corrected active/pending pins reported as `corrected`. Authored rows use the
same eligibility/correction floor without gaining any assessed write authority.
Renewed confirmation on the legacy authored route cannot make a corrected pin
eligible through an older read adapter. Event `related_id` is null for the new
three governed actions, and names the replacement for supersede/retire. Existing
events lacking a new sequence retain null sequence/previous ID. All actor/receipt
values are reconstructed from their recorded provenance; no invented actor.

Sort relation/type/task/candidate rows by their UUID or type key/revision, pair
rows by task/first/second UUID, statement/evidence/gap arrays by their identifying
UUID tuples, replacements/corrections by UUID, and events by the serialized
history above. Task status is a current diagnostic at `status_known_at=snapshot_at`,
not a past assertion or coverage verdict; retain its snapshot value on saved replay.
Pair/candidate coverage remains its immutable authored disposition, never rewritten
to `not_related` because an assertion was disputed or withdrawn. Coverage includes
only rows recorded by the cutoff; it makes no completeness or absence guarantee.

The internal frame manifest is the ordered set of actual visible dependency
records used for that response: accepted bead/statement/evidence identities and
hashes; assertion acceptance receipts; head manifests; lifecycle evidence pairs;
type/judgment/coverage pins; correcting/replacement pins; root-source and gap
dependencies; and the task-status values disclosed. Encode each entry as
`{kind:text,id:text,content_sha256:sha256}`, with kind naming its record class,
id its UUID or ordered composite UUID tuple encoded as canonical JSON text, and
content hash over that record's disclosed canonical row. Sort by kind/id/hash,
deduplicate, hash the canonical array, and retain its exact records in the caller's
protected result/provenance closure. Never hash a hidden dependency and use its
digest as permission to disclose. Snapshot/known timestamps label the selection;
equal cutoffs with different visible records have different manifest hashes.

Unaccepted assessed rows preserve `acceptance=not_accepted`; their evidence can
still have qualified roots without making the proposal support. Same-bead
accepted support-eligible derivation has `roots_status=unsupported` and null
`independent_root_count`, including when separately rooted basis evidence exists.
Its evidence items retain their own unit-level qualification; a qualified basis
unit does not establish the missing statement-level attribution. The propagation
rule below prevents an affected bead/unit from becoming an own-source fallback.
`derivation_root_ids` identifies canonical **source objects**, not source units,
beads, statements or assertion IDs. Root rows in PR-05 preserve that identity.
Other qualified evidence in that inspection stays usable on its own; aggregate
independent-root count is null if any required item is unqualified. No partial
count is reported as the union. `unavailable`/`budget_exhausted` responses contain
only `{contract_version:3,outcome}`, with no frame or payload. Preserve existing
512 KiB inspection response and 128-bead root bounds; overflow refuses the whole
read. Event histories are not silently pruned or capped to the latest entries.
The 512 KiB bound applies to this inspection response, not an entire PR-05 SQL
result. A scan uses the shared qualified per-root closure and 128-bead limit;
PR-05 owns admission of its complete result/provenance allocation. It may not
truncate root closure or convert the inspection ceiling into a hidden SQL LIMIT.
Malformed read/time/version inputs return exactly `{contract_version:3,
outcome:refused,error:invalid_request|schema_mismatch}`. Array root status is
`qualified` only if every required item and the assertion's own root attribution
are qualified; otherwise `unsupported`
takes precedence over `indeterminate`, and its aggregate count is null.

Legacy v1/v2 inspection and existing root-array/count helpers must delegate state
and root selection to this projection in the forward migration. They may keep
their existing response shape only where it represents the answer truthfully.
When they cannot encode indeterminate/unsupported roots, return `unavailable`
(internal helpers raise a mapped qualification refusal); never return a stale
integer, empty roots or a manufactured original. Legacy inspection has no new
assessed-write authority. Published contract payloads/migration bytes remain
unchanged; any new shape has its own version.

### Qualified roots and cycle consistency

The algorithm below is normative and self-contained. It states the historically
qualified bead/source-unit computation explicitly, with L4's forward eligibility
and gap checks. Historical SQL/code names do not supply additional rules. It adds
no statement-level root, proposition store, semantic inference or confidence
threshold. Shared sources count once; a transformation creates no corroborating
root. All record selection and authorization use the one frame defined above.

**Inputs and graph identity.** A bead record supplies its `bead_id`, immutable
`created_at`, primary `event_id` and observed `source_unit_id`. Its primary source
event supplies the canonical `source_object_id`. An evidence unit supplies its
`source_unit_id` and primary source event. These are stored identities, not IDs
inferred from matching text, names or hashes. A root is always a source-object
UUID. The graph has bead UUID vertices and a directed edge from source bead to
target bead for every eligible built-in `derived_from` assertion (canonical type
ID `30000000-0000-4000-8000-000000000009`) between different beads. Include authored
and accepted assessed assertions at their pinned historical revision; workspace
predicates with a similar name are not derivation edges. Collapse duplicate bead
edges to a set; do not collapse their assertion/provenance dependencies. Evidence
and governance statements do not create graph edges.

**Roots of one bead.** For starting bead B, compute the distinct reachable set R,
including B (zero-length reachability), using eligible derivation edges. A bead
with no eligible outgoing edge is a singleton terminal component, subject to the
gap checks below. Exploration uses sorted bead UUIDs and sorted distinct neighbor
UUIDs, with an already-visited set; depth does not impose a second limit. Exactly
128 distinct reached beads, including B, is admitted. Discovering a 129th refuses
the entire root computation as `budget_exhausted`, before returning any roots,
partial counts or partial gaps. This is a per-starting-bead reachable-set bound,
not a bound on path length, number of derivation assertions or final root count.
The result must not depend on query-plan/physical row order.

After the complete bounded set is known, form its complete eligible edge set and
reflexive/transitive reachability. Two vertices are in the same strongly connected
component exactly when each reaches the other. Select **bottom** components:
components with no outgoing edge to a different component. For each bottom
component choose the representative with minimum `(bead.created_at, bead_id)`;
timestamp comparison is by recorded UTC instant, followed by UUID unsigned
128-bit order (equivalently canonical lowercase UUID text order). This is stored
bead creation time, not relation acceptance, source occurrence/effective time or
most recent confirmation. The representative contributes the source-object UUID
of its primary source event. Return the distinct union over bottom components,
sorted by that same UUID order. Members of a cyclic bottom component contribute
one representative source before source-object deduplication. An upstream bead
adds no source of its own. A singleton original contributes its own primary source;
multiple bottom components contribute their roots, with shared source objects
counted once. SCC representative choice resolves a mechanical provenance tie,
not competing semantic assertions.

**Roots of one evidence unit.** For unit U, select every same-tenant bead record
whose exact `source_unit_id=U` and whose `created_at` is visible and at or before
the recording cutoff. This historically qualified observing set is not restricted
to the statement that cited U, the newest bead, one workspace-filtered subset or
a supplied candidate list. Compute the above bead roots for every member and
take their sorted distinct source-object union. Each member has its own 128-bead
reachable-set check; do not apply an invented 128-element cap to the combined
unit union or truncate the observing set. If there are no observing beads, U's
primary source object is its singleton original root. A caller must already have
selected U and its source event as visible by this frame; a future/missing unit
cannot acquire a fallback root. An authorized, structurally complete computation
on these nonempty inputs has at least one root. An unexpectedly empty union is
`unavailable`, not zero independent evidence and not permission for a fallback.

**Roots and count of an assertion.** Select its exact stored assertion-evidence
links recorded by the cutoff, compute unit roots for each, and return each item's
sorted root set. The assertion's `independent_root_count` is the cardinality of
the distinct union of those source-object IDs across all required evidence items.
Repeated source objects and repeated appearances of one unit contribute once.
Endpoint/basis statement count, relation confidence and governance-event evidence
do not alter this union. One independent count never means multiple citations
are independently corroborating. Roots of a governance evidence unit can be
inspected separately; citing it does not add it to assertion evidence.

**Qualification before output.** The following L4 checks apply to every reached
bead and every member of an evidence unit's observing set before any root set is
reported. Any gap makes that unit's root set null; union all its explanatory gap
records, deduplicate by `(kind,relation_id,reason)` and sort by kind, UUID and
reason. `disputed` takes precedence over `corrected` for one nonterminal assertion
that has both conditions; terminal retraction uses `withdrawn`, and an uncovered
supersession uses `replacement_gap`. Other assertions' gaps remain represented.
Any unqualified required unit makes the assertion count null, never a partial
union. No-observing-bead fallback is allowed only for the genuinely empty
observing set, never because an observing bead is denied or indeterminate.

Every observing/reached bead, used assertion, excluded assertion explaining a
gap, retirement/replacement/correction, primary source event and returned root
source is a protected dependency, even when only its ID/hash/count would be
returned. A required same-tenant bead in another workspace that is unreadable
refuses the complete computation; do not filter it and compute a smaller union.
Missing/inconsistent required source-event mappings also refuse as `unavailable`.
Authority loss takes precedence over disclosure of a semantic gap. Time/response
bounds can refuse the whole read in addition to the per-bead bound. Both roots
and their complete dependency manifests must fit their owning response/result
admission; an algorithm may not return a truncated set merely because the final
source-object union is small.

**Same-bead derivation and propagation.** At this frame, any accepted,
support-eligible built-in `derived_from` assertion whose source and target bead
IDs are equal represents a statement-level shape this contract does not compute.
For each root walk, examine such assertions on every reached bead (including its
starting bead) and on every observing seed of the unit. Do not turn the excluded
self-edge into a singleton original or a computed SCC representative. Instead
that bead-root computation has `roots_status=unsupported`, a null root array,
and a gap `{kind,relation_id,reason:statement_roots_unsupported}` for each such
assertion. It has no qualified original root to add to a union. The self-edge
does not add a vertex to the 128-bead bound and creates no new source object.
Cross-bead ancestors reaching the affected bead inherit that unsupported outcome;
the unsupported member is never dropped from an observing set or evidence union.

If any observing member's bead-root computation is unsupported, the entire unit
is unsupported with null `derivation_root_ids`, even if other members have
qualified roots or indeterminate gaps. An assertion requiring that unit has
`roots_status=unsupported` and null `independent_root_count`. Preserve all
authorized explanations from unsupported and indeterminate members/items, sorted
and deduplicated as above; precedence does not erase the lesser-status gaps.
Other individual evidence units may retain their independently qualified root
arrays, but cannot produce a partial aggregate count. Independently of these
unit results, an inspected assertion that is itself an accepted support-eligible
same-bead derivation also has unsupported aggregate root attribution, as specified
above; qualified foreign basis evidence does not change that subject-level floor.

An unaccepted same-bead proposal causes no graph edge or unsupported/gap outcome
for other beads/units. A disputed, correction-pending, retracted or superseded
same-bead assertion follows L4's noneligible gap/replacement rules below rather
than pretending to be an eligible statement-level edge. If another eligible
same-bead assertion still exists, unsupported takes precedence. Later removal
does not automatically certify an original: withdrawal still leaves its L4 gap.
Apply current authorization and the complete dependency/budget checks before
disclosing any unsupported outcome or explanatory ID. Denied/erased dependencies
produce `unavailable`, not an unsupported substitute or a smaller union; an
uncompleted over-budget closure produces `budget_exhausted`. No new semantic
judgment, statement-root algorithm or change to assertion acceptance/eligibility
is implied. Legacy root-array/count and v1/v2 inspection adapters refuse this
unsupported answer as already specified, rather than returning a singleton root.

Use only support-eligible `derived_from` assertions of either kind between
different beads as derivation edges at the selected frame. For each reached bead,
also examine accepted outgoing derivation assertions excluded by dispute,
correction or terminal withdrawal. Dispute/correction makes roots indeterminate.
Retraction makes roots indeterminate rather than promoting that bead's source to
an original. A superseded derivation is replaced without a gap only when its
recorded replacement chain ends in a support-eligible `derived_from` from that
same source bead to another bead; otherwise it also leaves a qualification gap.
Do not traverse withdrawn targets to produce current roots. Keep excluded
assertion IDs only as authorized explanations of the gap, not supporting edges.
An unrelated active derivation does not silently certify the withdrawn part.
A bead with no outgoing derivation assertion ever accepted by that frame remains
an original under the existing rule. A duplicate assertion with its own surviving
acceptance is a distinct recorded judgment, not revival of a withdrawn ID.

This is conservative qualification, not a new judgment about whether the old
source was truly derivative. Clearing an unresolved provenance gap beyond the
specified confirmation/replacement paths requires future explicit semantic work;
this cut must not invent it. Historical frames before the dispute/withdrawal retain
their qualified roots. Current authority loss refuses the whole relevant read,
including count/gap metadata, rather than laundering it into an independence gap.

Cycle checks use that same frame/state interpretation with a distinct integrity
predicate: **accepted and nonterminal** reserves topology, even if support is
disputed/pending. Reservations enforce structural safety and are never semantic
support. Checks span authored and assessed assertions, all revisions of the same
type key and all source-to-target statement combinations, under the existing
tenant/key lock. Use each assertion's pinned cycle policy: a newly completed
statement cycle containing a `forbidden` assertion is refused, including when
its new closing edge pins a permitted later revision. A cycle consisting only
of permitted assertions is allowed. A later definition never reinterprets an
earlier assertion's policy. Unaccepted, retracted and superseded rows never
reserve topology.
Confirmation therefore cannot introduce a previously unreserved cycle. Same-write
retirement releases the old reservation before checking the complete new set;
refusal rolls back both retirement and new assertions. No independent traversal
algorithm, effective-time bypass or second cycle graph is admitted.

### Qualification, coordination and handoff

PR-03 owns this blob, the shared projector, canonical governance and forward
migration. PR-05 owns `docs/agent-sql-results-v1.md`, logical column names, result
provenance/checkpoint closure and their wire mapping. Its contract must pin this
whole blob at the same public packet commit. Publish the combined pin only after
both owners reconcile their independent file changes. A PR-03 approval records
this exact document commit/SHA-256, choices L1–L5 and approval reference; it permits
this distinct implementation without a PR-05 holdout. The PR-05 packet still
requires its complete owner approval and independent unseen-holdout freeze.
Qualify/release the public dependency before Desktop consumes it. Saved results
remain explicitly historical: preserve their saved state/eligibility/root values
and allow composition in that same frame after a later lifecycle event, while
reauthorizing present access to the entire closure. Dispute/retraction alone is
not an authority-revocation reason to deny historical analysis or mutate a parent.
Using such a result to claim current support instead requires an explicit refresh
or current projector validation; fresh discovery/traversal uses the new frame.
PR-06 owns the terminal current-answer check. Authority revocation/erasure still
refuses the entire affected result/dependent access, including historical frames.

Required fictional installed-wheel and installed-sdist cases:

| Case | Required evidence |
| --- | --- |
| Every transition | Every allowed/refused table cell; renewed disputes, evidence-required confirm, terminal finality, same-key terminal replay, unaccepted proposal refusal, corrected endpoint and separate basis correction |
| Authority | Both tenants/workspaces; endpoint, basis, statement, evidence-event, source, replacement and correction denial; authorized second human; agent/service/device refusal; revoked credential/membership/grant/source; guessed IDs/keys disclose nothing |
| Races | Distinct keys against one head: one event; same-key race: one event/receipt; correction versus confirm; revocation versus decision/delivery; retraction versus accepted replacement; shared type locks versus reverse assertion |
| Time and replay | Null/past/future/naive effective time, late-recorded withdrawal, equal recorded timestamps, actual concurrent visibility manifests, restart/lost response, byte-identical old acceptance and receipts |
| Read agreement | New/legacy inspection, fresh eligibility and ordinary traversal, both kinds of derivation, root counts/gaps and cycle reservation agree at one frame before/during/after dispute, confirm, retract and replacement |
| Derived uncertainty | Multi-hop/shared roots, withdrawn/disputed edge inside lineage, no false own-source corroboration, eligible replacement chains, forbidden statement cycle versus permitted bead SCC, same-bead unsupported roots and explicit overflow |
| Exact root algorithm | Multiple observing beads per unit, union across evidence units, true no-observer fallback versus denied/gapped observers; SCC representative by created_at then UUID (including equal timestamps and conflicting source clocks); 128 reachable beads pass and 129 refuse for each seed; a combined unit union above 128 is not silently capped |
| Same-bead unsupported roots | Accepted eligible self-derivation: null bead/unit/assertion roots/count, never own-source fallback; propagation through a cross-bead ancestor and multiple observing/evidence members; unsupported over indeterminate with all authorized gaps retained; foreign basis roots stay separately qualified but do not certify the subject; unaccepted proposal has no effect; later dispute/retraction follows L4; denied dependencies refuse all metadata; legacy reads refuse unsupported answers |
| Preservation | Accepted bead version/statements/evidence/authorship receipts, proposal/judgment, author/specialist runs/deliveries, pair coverage, earlier lifecycle/retirement and failed/pending task evidence stay unchanged; governance schedules zero semantic/provider work |

Build exact wheel/sdist from the implementation head; install each in a fresh
environment and disposable PostgreSQL, with no editable/vendored core and no
owner data. Preserve hashes, commands, refusals and failing runs alongside later
passing proof. Run the complete relevant convergence lane once after focused
repairs, then exact-head CI on supported Python versions. Each implementation PR
uses DCO-signed-off commits, open/non-draft/subscribed status, one broad review and
at most one focused rereview; answer, fix and resolve actionable findings. Leave
unmerged. No version selection, release, provider calls, deployment, owner-data
access or checkpoint execution is authorized here.

Historical failure evidence stays part of the qualification history:
[PR #42](https://github.com/JohnnyFiv3r/memoriesql/pull/42) records the original
revoked-origin dispatch and unsupported causal-fixture defects, repaired in
`91c9c2e`; the earlier green `d99c91c` does not prove those cases. The
[PR #45 Python 3.14 run](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/36254323225)
records `test_shutdown_owns_worker_database_call_and_preserves_its_outcome`
failing at `len(cancellations)`: one expected, zero observed. That remains
distinct from later passing heads; its cause was not established. Both hosted
historical records were re-read during this contract pass. Do not call either
failure a new lifecycle reproduction, or use passing mechanical fixtures as
semantic quality evidence.

Handoff must report four separate states: exact contract approval; installed
implementation proof and exact PR head/CI/reviews; release readiness/publication
authorization; remaining PR-03 scope. This cut excludes tracked claims/updates,
partial correction, statement-level roots, interactive specialist evidence reads,
specialist reconsideration/recovery and maintenance. It does not qualify semantic
relationship accuracy, the live Desktop specialist or complete PR-03.

## Limitations of revision 1

- No tracked claims or claim updates after acceptance.
- Fixed packets; no interactive source rereads inside the task.
- No statement-level derivation roots and no partial-scope correction.
- No governed confirm, dispute or retract actions for assessed assertions. An
  accepted replacement can retire one, but nothing can simply withdraw it.
  Revision 1 is therefore an explicitly incomplete mechanical substrate:
  append-only dispute and retraction for assessed assertions must exist before
  assessed relations are used durably in live memory or qualified for recall.
