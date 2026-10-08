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
unchanged. Independent lane6 review reconciled the approved packet, lifecycle L4
and AM-5 with the original recorded replies. Fresh qualified/1→indeterminate/null
is correct for withdrawal and the fixture's B2→C replacement: B2 is a different
source bead, so it does not close B's derivation gap. The original probe's universal
fresh-invariance assertion is an oracle mismatch, masked by its earlier real
saved-frame refusal. Preserve that script and failed evidence byte-for-byte;
do not freeze fresh roots to make it pass. Only unchanged complete visible values
and authorized metadata require equal visible digests.

The additive tests score original saved pins separately from these qualified fresh
oracles. They add a same-source-bead B→D replacement, actual ungranted lifecycle
material evidence, changes within an already unreadable material family, revoked
original lifecycle evidence and a revoked original nonendpoint derivation root.
The negative controls check all nine relation tables, total rows, coverage/digests,
protected identifier absence, applicable owner targeted refusal and whole saved
refusal; measured charges stay unnormalized. These controls are prepared, not
executed. A new late protected replacement and an original dependency revocation
are distinct conditions. TG01–08 and their exact contract pins are recorded in
lane-owned coordinator evidence; no policy or timestamp interpretation changes.

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
canonical-statement corruption, corrected fresh conservative semantics and genuine
material-closure controls. Database runs require a new coordinator slot.
Source-only checks and skipped DB tests are not installed database proof. No
constant-time, resource-policy, full qualification, release or custody claim.
