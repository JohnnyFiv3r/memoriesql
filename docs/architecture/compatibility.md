# Compatibility policy

The `0.x` package line is experimental and provides no general API compatibility
guarantee. Releases use plain numeric `X.Y.Z` with `vX.Y.Z` tags; `0.0.10` (`v0.0.10`)
is the current published release. Alpha/beta/rc suffixes require a separate owner decision.
Published releases, their tags and archives remain immutable; the repository
retains only the current release inventory.

Two narrower integrity rules apply after publication:

1. A contract payload identified by a published contract ID and version is immutable.
2. Breaking contract-payload changes require a new contract version. A defective
   uploaded wheel/sdist is corrected by a new distribution version, never replacement.

The 0.0.10 preparation preserves dependencies, migrations
0001–0026, 61 existing contract records and generated catalogs. Python support stays
`>=3.13,<3.15`; 3.13 and 3.14 are tested.

## Schema and caller transitions

- **15 (published in 0.0.4):** successful legacy receipts retain their meaning and
  unfinished initial work can finish. Accepted meaning cannot receive another
  semantic version; such legacy writes fail with `accepted_bead_immutable`.
  Explicit version-2 corrections create separate beads and pinned supersession.
- **16 (included in 0.0.5):** evidence-package v1 adds exact immutable retained
  inventories and bounded authorized storage/reads. Storage/producer declarations
  do not prove independent source completeness, qualification or author exposure.
- **17 (included in 0.0.5):** qualified materialization adds stable occurrence/unit
  identity, one initial thin bead and an exact-package unavailable task binding.
  Complete units can progress independently of pending siblings. Partial-event
  accounting applies only to this explicit new path; legacy eight-unit/4,096-character
  payload limits and receipt meanings are unchanged.
- **18 (included in 0.0.5):** explicit activation creates a revision-2 successor in
  the same queue while retaining original inputs, unavailable snapshots, pins and
  receipts. Authorized reader v2 changes transport granularity only. Trusted
  attempt-bound complete exposure and current authority gate initial semantic apply.
  Cancellation that wins prevents transfer; all successful activation keys bind
  durably to their requests, including natural duplicates.

Installation is not opt-in to any transition: it does not migrate a database,
provision production trust, activate tasks, configure a provider or start a service.
Callers explicitly select the migration and approved composition described in the
[release guide](../releasing.md#caller-composition-and-explicit-opt-in). Migrations
are forward-only; historical successful receipts recheck current authorization.

Mechanical exposure proves exact supply to a trusted dispatch boundary, not model
comprehension or independent source completeness. Rolling-note quality and execution
ceilings remain experimental; complete-input correction/reauthoring is not delivered.
No schema-17 binding may use a legacy truncated-input path. Over-budget/incomplete
work retains evidence and its thin bead with an explicit truthful outcome.

See [observation compatibility](../immutable-observations.md),
[evidence packages](../evidence-packages.md), [materialization](../logical-unit-materialization.md)
and [execution](../complete-input-execution.md). Public release must precede Desktop
consumption. PR-02O/CP-2 remain incomplete; the six historical Desktop queue failures
remain unresolved. Provider qualification, production trust, owner data, deployment
and checkpoints are separate work.

## Schemas 19–20 retained from 0.0.6

Schema 19 adds authorized retained-fold recovery: bounded discovery, exact outcome
and lineage inspection, and retained raw-byte reads. No producer qualification or
semantic work is created. Schema 20 adds explicit revision-3 complete-unit authorship
with target/context-pinned typed rereads and trusted unique mandatory exposure through
the existing worker/executor. Current authority is rechecked through hydration,
dispatch and canonical application. Original task inputs, package pins, receipts and
accepted meaning remain immutable; existing revision-2 bindings cannot silently switch.

The 131,072-character target ceiling is enforced before activation and at typed input
and current authorization. Delivery, interaction, token and wall-time ceilings are
unchanged and experimental. Repeated usage does not inflate unique target coverage.
See [recovery](../transcript-fold-recovery.md) and [revisiting](../source-revisiting.md).

All published inventories and historical development candidate inventories remain unchanged.
Only the new 0.0.10 inventory is used by the prepared release gate. Production grants,
providers, implicit migration or task activation are not supplied by installation.

## Schema 21 source-stable opt-in in 0.0.7

The separate `memoriesql.source-stable-identity.v1` record supplies materialization
command/receipt v2. Package v1 continues to bind actual retained revisions. The
new source-stable binding retains the first package occurrence hash, task inputs,
receipt and pin, so existing activation, reader, exposure and apply contracts do
not change. Repackaging never selects newer evidence for an existing task.

An immutable, explicitly approved producer namespace scopes event/occurrence keys
inside one source and tenant. Unknown native identity cannot opt in. The new path
rejects changed event/native facts, parent or ordered normalized components/text;
parts and raw/fold revision lineage may differ. It adds no correction behavior.
Only a source without canonical events can enter this identity mode; existing
canonical events cause an explicit unsupported transition. Raw/fold evidence alone
does not prevent opt-in. The source-mode fence also prevents
legacy capture/materialization from creating a second interpretation afterward.
Unopted sources, published records, receipts and SQL 0001–0021 remain unchanged by release preparation.
No historical equivalence, migration of bindings or duplicate repair is inferred.
Production identity-policy approval and a separately authorized release precede
consumer adoption. PR-02O/CP-2 and the historical queue evidence remain incomplete.

## Published 0.0.10 public composition

Published 0.0.10 adds two owner-authorized forward runtime changes on top of
published 0.0.9: the executor binds authored statements to its own run reference
before hashing (single-run trees), and the worker settles data-error canonical-apply
refusals as invalid output. No migration, contract payload or dependency changes.

### Published 0.0.9 composition

The 0.0.9 candidate packages the already merged public prerequisites:

- **Bounded model admission:** exact caller-supplied model/profile/target qualification,
  pre-dispatch request/token reservation and attributable usage through the existing
  executor. No production transport or provider entitlement is supplied.
- **Schema 22:** atomic authored local mentions, including explicit authored-empty
  and unresolved/ambiguous local state, without inventing global entity resolution.
- **Schema 23:** a bounded evidence-backed classification packet, registered
  vocabulary revisions, explicit abstention/disagreement and one attributable
  accepted contribution. Mechanical acceptance does not prove semantic quality.
- **Schema 24:** currently authorized stored bead/result and exact-evidence reads;
  legacy, missing, hidden and authored-empty remain distinct. Reads do not run inference.
- **Accounting cancellation repair:** already-started database writes and late usage
  remain owned through repeated cancellation; callers still retain the worker/event
  loop until cleanup finishes.

These capabilities are merged public code. The release PR does not establish
publication, private adoption, live-provider quality or CP-2 completion. The owner has explicitly authorized the qualified release merge/tag sequence;
protected upload approval remains a separate owner gate.

The combined 0.0.9 candidate also includes schema 25 declared evidence scopes,
schema 26 explicitly approved managed dispatch and the terminal-settlement repair.
Local failure completion is distinct from durable event acknowledgement; started
writes and late usage retain existing ownership. Default hard admission, fences,
authorization and inference-free Q reads remain unchanged. Installing the package
neither provisions this opt-in nor authorizes provider calls. These are candidate
capabilities until independently verified publication.
