# Unreleased source-revisiting qualification

Base: public `2ff951385405a45a1018a105f8a6641758ece78a`, the verified PR #15
merge. Reviewed head `4b68631861484eaf920ef3a464bc04d99fc94514` is ancestral.
This is fictional mechanical qualification, not provider quality, comprehension,
producer admission, source completeness or checkpoint proof.

`tests/runtime/test_source_revisiting.py` exercises the existing worker/executor,
accounting, canonical sink and cleanup against disposable PostgreSQL 18. Its
FunctionModel emits explicitly fictional annotations. No provider, owner data,
external transcript or private import is used.

## Concrete cases and bounds

- Complete-context delivery: 40,000 characters across three retained storage parts
  arrive in one new-version dispatch. The unchanged revision-2 task still uses
  three interactions on schema 20, preserving its original inputs and receipts.
  This justifies a new 65,536-character window bound while retaining the existing
  131,072-byte canonical JSON bound; it does not redefine logical units.
- A larger target takes multiple windows. After the final forward window, the
  same author rereads its first normalized characters and raw bytes, then chooses
  another step before finishing. Repeated dispatch increases receipts/usage, not
  unique required coverage.
- A 64-part Unicode unit supplies 64,000 characters / 192,000 UTF-8 bytes through
  eight reader batches and one typed reread. It needs six fictional model
  interactions, zero real provider calls. The initial measured full worker run
  took 4.292 seconds with 2,354,695 peak Python bytes under tracemalloc, including
  collected test frames. Reader pages do not prescribe model interactions.
- Exact transformed normalized text and raw JSON derivation remain distinct,
  including raw chunks of three bytes. Part/native/lineage metadata is preserved;
  no finer character-to-byte support mapping is invented.
- Optional neighboring evidence can supply a short selected interval without
  becoming another mandatory complete transcript. An immutable explicit allowlist
  and current source authority constrain every selection.
- Unique coverage is an interval union, not maximum endpoint or delivery volume.
  Omitting the first window and supplying only the last cannot pass application.
  Tampering, cross-attempt frames, conflicting receipts and direct canonical apply
  without exposure are rejected. Equal attestation replay adds nothing.
- Twelve author interactions exhaust explicitly even after complete exposure.
  Separately, a 131,072-character target plus repeated delivery reaches the
  262,144-unit delivery ceiling after ten interactions. No semantic result is
  committed. These budgets are experimental bounds, not provider-cost approval.
- A worker credential expiring during an observed advisory-lock wait denies
  hydration. Producer revocation during a reread response preserves accounted
  usage while blocking acceptance; dispatch-policy revocation before canonical
  apply also blocks it. Missing raw evidence and stale/out-of-scope selections
  fail explicitly.
- Restart/reaping requires exposure in the new attempt. Stale attempts cannot
  apply prior results. Cancellation preserves existing accounting and cleanup:
  an already-started attestor write drains, the worker stays quarantined and no
  placeholder meaning is applied.
- Original revision-2 policies cannot certify this new dispatch boundary. The
  fictional tests insert separately reviewed revision-3 policy fixtures. No
  production policy is provisioned or inferred from a digest.

Early focused iterations found a migration expression-parenthesization error and
fixture issues (an absent status field, missing imports, and the append-only guard
correctly blocking a corruption fixture). These were corrected before convergence.
Administrative corruption is confined to disposable fictional databases; guards
are restored before the read-under-test. No production repair is implied.

## Broad-review correction

The broad review identified an unenforced target ceiling. Permanent installed
regressions reproduced that a 131,073-character target could activate and its
typed input was accepted. The existing worker rejected that test run later;
with `budget_exhausted`; this is not evidence of a successful oversized
canonical application. Schema 20 now rejects oversized
targets before activation effects and during current revisiting authorization;
the typed input also rejects them. The exact 131,072-character boundary remains
supported. The focused correction verifies retained evidence, unchanged original
task/receipts/outbox state and zero semantic/provider work after rejection.
All three new installed regressions and the regenerated installed contract-hash
check pass. No unchanged full local suite was rerun.

## Compatibility and artifact procedure

Migrations 0001–0019, all 54 prior record payloads and historical release/candidate
inventories are compared byte-for-byte with base. Registry/catalog/resource closure
uses the existing generators and verifiers. The package remains 0.0.5 only because
no next version is selected; schema-20 archives are unreleased and are not the
published 0.0.5 distribution. Their candidate inventory was retired from the repository on 2026-09-22.

The single installed local Python 3.13 convergence ran 188 tests: all 187 runtime
and other cases passed; the new registry entry had a stale canonical-payload hash.
That metadata was corrected, then the failed installed hash check was rerun alone.
All 22 revisiting/schema-20 compatibility cases passed in that lane. Its Unicode
measurement was 4.202 seconds and 2,281,136 peak Python bytes. Earlier source-level
checks likewise caught two old candidate-verifier/record-list expectations; their
focused reruns passed after updating those expectations. No published payload changed.

The PR records final-head hosted
Python 3.13/3.14 wheel/sdist checks, deterministic rebuilds and independent download
comparison against the committed inventory. This document does not manufacture a
future commit SHA or claim CI before it completes. Publication controls and the
published inventory are unchanged; they do not accept these candidate bytes.

Graphify has no configured graph in this public worktree. Source, SQL, contracts
and focused tests supply the evidence; no graph rebuild or unrelated suite rerun
was performed. The existing private checklist remains the progress authority and
is not edited here. PR-02O/CP-2 still require release, product composition and owner
proof. The six historical unexplained Desktop queue failures remain unresolved.
