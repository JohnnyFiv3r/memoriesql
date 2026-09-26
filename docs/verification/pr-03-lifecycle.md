# PR-03 lifecycle implementation qualification

## Authorization and dependency

The [durable exact approval](../approvals/pr-03-lifecycle-2026-09-26.md) authorizes
L1–L5 at `2baffc688291dbfdeccaa46f7ca05f6e9ef0e12c`. The approved document SHA-256
is `69a0d985466c6610a3a2abc73bcef9242749ff9535cb709e32867c7e06414d2a`; its bytes
are unchanged. Fresh public main `e8cfa0df3c1f8109c199a8126c0556624bc421b6` is an
ancestor. PR #46 remains open/unmerged; this implementation is stacked on its
approved head. Neither PR is merged by this work.

Migration **0030** belongs to PR-03. PR-05 reserves **0031+** and owns retrieval,
results and durable investigation frames. PR-03 does not depend on its holdout.
No previously published migration or contract record is rewritten.

## Implementation

The public contract `memoriesql.assessed-relation-lifecycle.v1` provides closed
`RecordAssessedRelationEvent` and `InspectBeadRelationsV3` models. The PostgreSQL
port owns short write transactions and authority-fenced repeatable-read frames.
Governance appends events, exact evidence, context attribution and idempotency
receipts atomically; it does not enqueue tasks or obtain model judgments.
Original accepted beads, statements, assertions, judgment/run attribution,
evidence pins, deliveries, receipts and authorship remain immutable.

`relation_projection_v1` is the common state/head/support interpreter for both
assertion kinds. Inspection, legacy readers, traversal eligibility, qualified
roots and cycle reservations consume it. Roots qualify only the admitted
historical graph; disputed/corrected/withdrawn/replacement gaps yield null roots
and counts, and same-bead derivation is unsupported. Legacy shapes refuse when
they cannot encode the qualification. No withdrawn target supplies fallback
support. Nonterminal accepted assertions reserve cycles across pinned revisions.

## PR-05 handoff revision: lifecycle-v1 / migration-0030

All SQL helpers below are in the `memoriesql` schema. They remain private and
must not be granted to the restricted PR-05 LOGIN. A trusted server bridge may
call them under already-authorized current context; none elevates a caller or
provides a write route to PR-05.

| Helper | Inputs | Result |
| --- | --- | --- |
| `relation_assertions_v1` | `(tenant uuid, known timestamptz)` | Both assertion kinds with immutable acceptance, exact endpoint/version pins, type revision and acceptance receipt |
| `relation_projection_v1` | `(tenant uuid, kind text, relation uuid, known timestamptz)` | State, head token/manifest, support eligibility/reason, correction flag/all successors, replacement IDs |
| `relation_assertion_row_v1` | `(tenant uuid, kind text, relation uuid, relative_bead uuid or NULL, known timestamptz)` | Complete exact assertion row, endpoint/basis statement text, original judgment/run attribution, complete events, evidence and root qualification; no inspection pagination/response ceiling |
| `relation_assertion_records_v1` | `(tenant uuid, kind text, relation uuid, known timestamptz)` | Protected actual dependency records including corrected/replaced/excluded closure |
| `qualified_bead_roots_v1` | `(tenant uuid, bead uuid, known timestamptz)` | `roots_status`, nullable `derivation_root_ids`, sorted `roots_gap_relation_ids`, protected `dependencies` |
| `qualified_unit_roots_v1` | `(tenant uuid, source_unit uuid, known timestamptz)` | Same shape over all readable same-tenant observing beads, including other workspaces |
| `relation_inspection_frame_v1` | Closed inspection-v3 JSON request | `{response, dependency_records, dependency_manifest}` when available, otherwise exact public refusal envelope |

`kind` is `authored` or `assessed`. `known` is one finite UTC cutoff no later than
DB snapshot time. NULL `known_at` in the public request resolves once. The
assertion-row helper accepts NULL relative bead for the stored outgoing
orientation. Root traversal retains the approved 128-per-starting-bead bound;
the complete observing union has no aggregate 128 cap. PR-05 owns its complete
result admission and may not treat the public 512 KiB inspection ceiling as SQL
pagination or silently prune event/dependency history.

The Python `relation_projection_frame(connection, *, credential_sha256,
workspace_id)` context manager owns a short source frame. Current authority is
established and a session shared authority fence acquired **before** the RR
snapshot; it yields the connection inside that snapshot and releases the fence
in `finally` after commit/rollback. The connection must begin idle. PR-05's
trusted bridge invokes the lower-level helpers, prepares/admit its complete
result/dependencies and checks final current authority **inside this context**
before handoff. It owns every refusal/cancellation cleanup and must not continue
through agent/model work. The one-shot `read_relation_projection(connection, *,
credential_sha256, workspace_id, query, payload)` uses that utility and returns
after closing the frame; it cannot be extended after return or called once per
relation to manufacture a shared snapshot. The public inspection wrapper stays
one-shot. Helpers do not create a second lifecycle state implementation.

Private frame dependencies are actual `{kind, id, row}` records. `id` is a UUID
string, or canonical JSON text encoding a UUID tuple. Manifest entries are
`{kind, id, content_sha256}` with SHA-256 of the normative canonical JSON of
`row`; entries are deduplicated and sorted by kind, ID and hash. The frame hash is
SHA-256 of that sorted manifest. It includes exact assertions/head/acceptance
receipts, statements/evidence, original specialist judgments, type definitions,
coverage/candidates/task diagnostics, all correction/replacement/governance
records, excluded root gaps, observing beads, primary events and source identities.
One frame has one known/snapshot pair. A saved PR-05 frame must retain these
records and values, and replay those pinned records under current complete
authority; rerunning a timestamp query is not saved-frame replay. Full refusal
exposes no hidden record/hash, and indeterminate/unsupported counts stay null.

## Mechanical proof and limits

The installed lifecycle suite covers serial disputes, repeated confirmation,
terminal withdrawal/replacement, all illegal transitions, corrected endpoint and
basis branches (including pre-acceptance correction), current/delegated human
scope, actor/tenant/workspace boundaries, revocation, exact evidence, normalized
replay/conflict/incomplete receipts, independent-key and same-key concurrency,
correction/confirmation and replacement/withdrawal races, as-of/current agreement,
protected saved records, uncertainty propagation, observing unions, SCC ties,
128/129 bounds, uncapped event history and whole-response budget refusal.
The final suite has 38 cases. Three final transition cases prove pending
retirement retaining correction, refusal to retire an unaccepted proposal and
equal recorded times ordered by event sequence despite reversed UUID order. The
last uses a generation-only recording-clock/UUID fixture through the installed
public governance function; it changes no stored event/receipt or lifecycle rule.
Two final projection refinements bind authored acceptance to the exact authoring
version and pass the once-resolved cutoff into legacy-v1 inspection. No signature
or approved document changes.

Four broad-review regression cases also qualify legacy authored mixed cycles and
later permitted revisions, separately cited query/raw-read-only evidence (using a
trusted denial-only policy fixture), and a canonical non-observation unit with
true no-observer fallback versus future-unit refusal. Migration0029 already used
the shared both-kind cycle checker; the forward restatement preserves that call
and every authorship/claims/coverage rule while extending sorted locks to all
pinned type keys. No duplicate cycle or lifecycle implementation is added.

Original-record snapshots and zero new task/event checks distinguish governance
from fresh authorship/model work. `prove_assessed_relation_restart.py` uses three
processes: committed response loss, fresh-process replay and isolated cleanup.

Qualification uses fictional PostgreSQL 18 databases and installed wheel/sdist
routes on Python 3.13/3.14 with checkout, home, subprocess and external network
access denied during acceptance. Artifact receipts and exact-head CI are recorded
in the implementation PR; these development artifacts retain existing package
metadata and are not a selected release or published distribution.

Source checks currently pass: 59 unit/contract tests, 136 mypy-checked files,
ruff, 65-record registry/catalog, 30 migrations and 61 runtime files. Installed
proof and final CI receipts must be read with the PR's exact head, rather than
inferred from these counts.

## Preserved failures

[Failure transcripts](pr-03-lifecycle-failures.txt) preserve earlier failures,
with local path prefixes normalized only. They include real defects found in
JSON normalization/concatenation, missing capability error classification,
ephemeral-context foreign keys, legacy uncertainty reads and dependency records;
fixture errors, stale artifact installation after a denied cache path, and an
authorization-context preparation race are identified separately. The terminal
race proof prepares contexts before competing apply transactions and exercises
the same installed public governance SQL without changing production authority.
Broad review5327463342 at4fe3094 identified an evidence-maintain overconstraint;
the installed refusal reproduced and the repaired raw/query-only path passes.
Its legacy cycle-routing claim referenced schema27; schema29 already calls the
shared checker, and installed mixed/later-policy regressions passed before the
repair. All-key lock hardening addresses the remaining lock issue. A separately
reproduced future-unit fallback defect is fixed by checking unit creation time.
Passing later checks do not erase these records. Historical PR #42/#45 failure
evidence in the approved document remains untouched. A later local text-cleanup
mistake removed part of a private inspection function; the reviewed installed
function was restored before qualification, and the full suite is rerun.

## Exclusions and readiness

Contract approval is explicit; implementation proof is mechanical and fictional.
Passing CI is not semantic/provider quality proof. This PR is left unmerged and
has no release readiness claim: the approved dependency, owner merge decisions,
future release/version/publication qualification and Desktop consumption remain
separate. PR-03 still excludes tracked claims, partial correction, statement-level
roots, specialist reconsideration/recovery and autonomous maintenance. No release,
publication, provider call, owner-data access, deployment or live checkpoint was run.
