# PR-05 retained saved-frame closure — development candidate

Canonical late relation-history commits have been reproduced on exact installed
`de328bb5afe8d94431c1b814432ccf416625c4f3` / schema 43 on Python 3.13 and
3.14. Four cases refuse saved redelivery/reuse despite unchanged authority and
content/witness/dependency bytes: hidden confirm, retract and supersede, and an
owner-visible retraction. Unchanged, rolled-back and original-source-revocation
controls behaved as required. This is development evidence, not qualification.

The approved temporal contract says `known_at` does not prove a closed set of
commits; the actual retained revisions, frame and manifest pin the snapshot.
Migration 0044 replaces only closure v2's fresh-population subset comparison.
The existing owner binding, expiry, operation, partition checksums, content
identity, canonical manifest and witness checks remain byte exact. Private
helpers check the saved canonical leaf records and current authority, original
assertions, original events, correction paths, evidence and task populations.
They do not select a new lifecycle state, replacement closure or root population.
An original replacement must remain in the retained assertion dependencies;
a later replacement cannot add facts to a saved result. Unknown/missing/changed
records refuse the whole result. No new privilege is granted.

Fresh query preparation, lifecycle projection, inspection and discovery are
unchanged. Fresh root metadata after hidden retract/supersede still changes from
qualified/1 to indeterminate/null in the original private reproduction. That
privacy finding remains open; timestamp drift alone does not resolve it. The
original seven-probe script and its failed evidence are preserved unchanged.
The new repository regressions isolate saved-frame obligations while retaining
the hidden-confirm fresh-values/digests control. Do not describe a narrower
saved-frame pass as passing all seven original privacy probes.

Public disclosure/reuse routes through `query_result_access_v1` to the existing
`check_query_result_closure_v2`; no Python executor change or new sealed-result
format is needed for this candidate. Old population-revision-2 artifacts with
complete recorded dependencies are expected to work without rewriting bytes;
missing mandatory dependencies refuse. The schema-35 private standalone v1
checker has a different artifact format and is preserved, not routed to v2 or
claimed repaired. Its old-format temporal behavior remains a separate scope.
All migrations 0001–0043 and public contracts/catalogs are preserved byte exact.

CPU validation: 135 unit tests on each Python 3.13.5 and 3.14.0, strict typing
over 200 files, Ruff lint, migration/catalog/boundary checks and local wheel build
pass. Repository-wide formatting has pre-existing differences; new test files
pass formatting. These checks do not execute PostgreSQL.

Database validation pending: real PostgreSQL parsing, schema-43 artifact upgrade
compatibility, positive unchanged-frame coverage,
all four reproduced saved failures, original source and newly revoked nonendpoint
task-candidate authority, protected new replacements, storage corruption and
canonical-statement corruption. Database runs require a new coordinator slot.
Source-only checks and skipped DB tests are not installed database proof. No
constant-time, resource-policy, full qualification, release or custody claim.
