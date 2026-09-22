# Source-stable occurrence identity qualification (unreleased PR-02O slice)

Base: `6045605f98744a378168b08894ee192378fe12e7`, freshly fetched public main.
The [materialization contract](../logical-unit-materialization.md#proposed-source-stable-opt-in-pr-02o-schema-21)
records the pre-implementation design and conservative compatibility decision.
This note is evidence in the existing public verification lane, not a new tracker.
PR-02O/CP-2 remain incomplete; release must precede private consumption.

Independent public-authored orchard fixtures reproduce one source/native identity
retained under two raw revisions in released 0.0.6: two canonical events, units,
beads and tasks. Package representations remain revision-bound intentionally;
the forward opt-in changes only canonical identity selection. The released wheel
SHA-256 is `4d270e2ae1e29c6a5ca68408fb3ded19c73dca6ea26a0b4b58de1adafc72a16a`;
all 79 installed package files were independently compared with it.

The thirteen focused candidate regressions pass (11.681 seconds on Python 3.13.5 /
PostgreSQL 18), followed by two direct legacy-mode cases (1.338 seconds). They exercise exact replay, fresh-key natural duplicates, changed
requests, concurrent first submissions across revisions, Unicode repartitioning,
changed native facts/content/event/namespace, identical content under distinct
identities, pending/unknown input, producer admission and revocation, forged
revision lineage, rollback, legacy behavior and unsupported transition.

The full-path fixture recovers the original fold and raw bytes after a later
representation has been submitted; activates the original binding through
revision 3; replays activation under a fresh key; rereads its exact original raw
lineage; and completes through the existing fictional executor, trusted exposure
and canonical sink. No reader, execution, exposure, task-input or semantic-output
contract was changed. This is mechanical fictional verification, not semantic
quality, production producer approval or provider qualification.

The source-exclusive transition intentionally does not adopt any source with
existing canonical events. Legacy capture/materialization cannot later populate
an opted-in source under a second identity interpretation. Historical receipts,
original bindings and pins are not rewritten; no historical duplicates are merged.
The source-mode fence includes legacy canonical event insertion. Production identity
namespaces and qualification evidence require separate administrator approval.

The complete installed wheel lane ran 202 tests in 174.934 seconds. Its three
assertion failures were confined to two stale compatibility test methods: schema
21 was still treated as out of range, and ownership expected 43 runtime modules
instead of 44. Those two corrected methods and the new partial-field constraint
case passed in 1.233 seconds; no unchanged full suite was rerun. The later
clock-aware authorization change passed all thirteen focused tests, including an
observed source-lock wait past credential expiry with zero binding/receipt effects.

The wheel rebuilt independently from the normalized sdist is byte-identical to
the direct wheel. A fresh sdist-route installation passed all thirteen identity
regressions in 11.740 seconds, plus both direct legacy-mode cases in 1.338 seconds,
with checkout access denied. Ruff, strict mypy (97 files), 38 metadata/contract
checks, public-boundary/provenance checks and artifact inspection pass. All twenty
historical migrations and fifty-five record files remain byte-for-byte frozen.
The released-wheel reproduction was independently repeated after comparing all 79
installed files to the hash-verified published archive (0.639 seconds). An earlier
parallel attempt conflicted on PostgreSQL's cluster-wide role DDL; the serialized
reproduction passed. This was fixture setup, not an identity behavior failure.

The broad review found a missing tenant/source index on the source-mode fence.
Its initial correction removed unrelated-history scans, but the focused rereview
correctly identified remaining linear scans within one source. The final correction
indexes tenant/source/mode and compares the first and last indexed modes with
one-row limits. Canonical insertion and both materialization versions share the
same private check. No additional identity store or maintenance process is added.

The strengthened permanent fixture accepts 129 stable events on one source and
256 legacy events on another, with ordinary canonical constraints and triggers
active. The previous installed fence still scanned this history; indexed range
predicates alone also allowed PostgreSQL to choose a sequential scan. First/last
ordered lookups address that planner behavior. The final plan assertions require
two forward/backward index lookups, one returned row each, no sequential scan,
no sort and no rows removed by filtering, for both stable and legacy sources.
This proves the access path, not production throughput qualification. The broad
review and focused rereview budget is consumed; the final correction is reported
for owner recheck, without requesting a third review.

Independent local inspection also found global component coalescing could erase
interleaving: `a:A, b:B, a:C` was accepted as equivalent to `a:AC, b:B`.
The new regression failed against the installed reviewed candidate before the fix.
The fingerprint now coalesces only contiguous component runs, preserving order
while permitting adjacent refragmentation. All seventeen final identity cases pass from the checkout-denied direct wheel
(19.685 seconds) and independently rebuilt, byte-identical sdist wheel (19.968
seconds). The final first/last plans touch four shared blocks for either source,
with measured 0.010/0.009-ms execution on the small fictional fixture. Corrections
affect only the new migration; published SQL and records are unchanged. Final-head hosted
CI is pending at this writing. The separate candidate inventory was retired from the repository on 2026-09-22; its
unchanged 0.0.6 development metadata never authorized replacing the published release. Publication workflow,
release verification controls and published archives remained unchanged; at the time
CI compared the unreleased candidate to its own inventory, which has since been
retired.

No private provider code, fixtures or evidence is exported. No real provider,
owner data, production policy, deployment, checkpoints, P/Q, release-version
selection, tag or publication is included. Rolling-note quality and execution
ceilings remain experimental. Complete-input correction/reauthoring is not added.
The six historical unexplained Desktop queue failures remain unresolved evidence.
