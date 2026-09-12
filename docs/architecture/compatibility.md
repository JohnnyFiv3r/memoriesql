# Compatibility policy

The `0.x` package line is experimental and provides no general API compatibility
guarantee. Releases use plain numeric `X.Y.Z` with `vX.Y.Z` tags. This preparation
selects genuine `0.0.5` metadata and exact `v0.0.5` repository controls; it does not
publish or authorize a tag. Alpha/beta/rc suffixes require a separate owner decision.
Published `0.0.1a1`, `0.0.2`, `0.0.3` and `0.0.4`, their tags and inventories remain
immutable. The previous `runtime-package-artifact-inventory.json` still records
published 0.0.4; the new versioned 0.0.5 candidate inventory is separate.

Two narrower integrity rules apply after publication:

1. A contract payload identified by a published contract ID and version is immutable.
2. Breaking contract-payload changes require a new contract version. A defective
   uploaded wheel/sdist is corrected by a new distribution version, never replacement.

The 0.0.5 preparation preserves all runtime behavior, dependencies, migrations
0001–0018, 53 existing contract records and generated catalogs. Python support stays
`>=3.13,<3.15`; 3.13 and 3.14 are tested. Older Python may explicitly pin catalog-only
`0.0.1a1`; it cannot consume the runtime by falling back to that package.

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
