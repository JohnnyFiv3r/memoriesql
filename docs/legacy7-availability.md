# Legacy revision 7 availability

Forward migration 0045 restricts `memory.semantic.author-complete-unit` revision 7
through two existing canonical functions. It does not redesign corrections.
After effective installation, `activate_complete_input_v6(jsonb)` validates the
existing request and current authority, then refuses with the existing 42501
unavailable classification before any success replay, receipt, transfer or
enqueue. The task-bound `source_revisiting_authorize(uuid,uuid,uuid)` refuses
actual execution revision 7 after existing binding/authority/qualification checks.
The remaining source authorizer body is unchanged for other revisions.

Canonical source delivery, provider-intent admission/replay and canonical apply
already reauthorize through this task-bound authority. A worker-only or CLI-only
restriction is insufficient. Claims/resume do not confer permission to execute
or reset attempts/allowances. Original terminal states, accepted notes, receipts,
usage, reservations and authorized historical inspection remain intact. Old
success replays through the gated endpoints refuse; separate historical reads
still require current authorization. The raw source-object authorizer used by
stored inspection is distinct and is not replaced.

Already admitted provider work retains its original identity and owner until
actual cessation/settlement. Late output cannot acquire canonical apply
authority. Existing worker reauthorization failure retains usage and settles
through the existing failure path. Usage append, deduplication, cancellation,
cleanup and recovery are not disabled by the revision 7 gate. An unknown outcome
is not zero usage, a refund or proof that work stopped.

## Installation and cutover are separate obligations

The existing migration runner installs both replacements and migration history
in one transaction. This gives atomic installation; it does not prove that every
old invocation or provider worker has drained. Existing tenant authority fences
order covered authorization/outcome writes, but are not a proven global
admission or deployment drain boundary. Include a call already entered into an
old function body before waiting on a fence in any cutover proof.

No new global lock, mutable switch, privilege or unrelated kernel pause is added.
Before representing a running deployment as restricted, its owner must identify
an existing complete ingress-stop/drain boundary for activation, source delivery,
custom-host/provider admission and apply; independently account for old caller
transactions and owned work; install the pair; verify actual refusal; and resume
only after the boundary is proven. Historical commits preceding that boundary
remain historical commits. Unaccounted work or an unproven boundary means
enforcement pending. A missing all-caller admission/drain mechanism is an explicit
engineering scope dependency, not silently supplied by this migration. This
document authorizes no deployment stop or DB execution.

## Evidence and remaining acceptance

Database-free source checks cover the two-function-only delta, historical 0041
bytes, migration inventory hashes, unchanged other-revision body, absence of
activation success/effects, and truthful contract text/JSON. The generator retains
historical record payloads/status/hash identities and 70 total records/63 json_schema
entries. Available labels describe bundled artifacts. Neither source checks nor
catalog text prove installed enforcement.

The additive fictional `tests/runtime/test_legacy7_unavailable.py` contains12
cases for activation/replay, canonical apply/replay, late output, initial intent
and usage acknowledgement loss, usage redelivery, resume/allowances, other
revisions and hidden-family inspection. Its reviewed case bodies are preserved.
The separate `tests/runtime/test_legacy7_cutover.py` adds seven owned fictional
cutover schedules; see `docs/verification/legacy7-cutover.md`. All19 SQL/runtime
cases remain UNRUN at this source checkpoint. They use canonical historical
fixtures, not invented receipts or live providers. Source CPU checks and type
checks are not native execution or a deployment ingress/drain proof.

Required remaining acceptance includes new policies, direct/body apply and
successful apply replay, generic enqueue/claim/input and supported custom-host
intent admission/replay, claimed/resumed/max-attempt states, late output, actual
usage/dedupe/lost acknowledgements, failure/cancel/reaper/restart/cleanup, and held
old activation/apply/intent transactions across the actual cutover. Preserve
revisions 2–6, relations and hidden-family/current-authority inspection controls.
These are unmet proof obligations. No timing/constant-time, resource capacity,
installed/integrated qualification, activation, release or availability claim
follows from this checkpoint.
