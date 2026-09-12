# Canonical migrations

Published `0.0.2` introduced the canonical migration substrate; `0.0.4` added
schema 15. The 0.0.5 candidate packages the already merged schemas 16–18: evidence
packages, qualified materialization and explicit complete-input execution. This
release preparation adds or rewrites no SQL. All 18 migration bytes are preserved.
**Python 3.13+ is required; the initial qualification matrix is 3.13 and 3.14
(`>=3.13,<3.15`).** It is not a complete capture, authoring, or recall runtime.
No release or upload is authorized by this change.

The published `0.0.1a1` remains an immutable catalog-only preview supporting
Python 3.11–3.14. On older Python, pin `memoriesql==0.0.1a1` for catalogs.
An unpinned request may fail because pip excludes prereleases by default;
`--pre` permits the historical fallback, which has no migration capabilities. Use the
exact reviewed artifact and version, never an unbounded `pip install memoriesql`
as a runtime setup instruction:

```console
python3.13 -m venv migration-env
migration-env/bin/python -m pip install ./memoriesql-0.0.5-py3-none-any.whl
migration-env/bin/python -c "from importlib.metadata import version; assert version('memoriesql') == '0.0.5'; from memoriesql.infrastructure.postgres.migration_runner import discover_migrations; assert len(discover_migrations()) == 18"
```

The installer rejects this artifact on unsupported interpreters. Publication and
consumer deployment require separate approval; use an exact version pin. Runtime dependencies remain pinned in package metadata; no provider extras are included.
Catalog reads remain independent of runtime imports and external I/O.

## Bounded application

With an explicitly supplied, idle `psycopg` connection to the intended database:

```python
from memoriesql.infrastructure.postgres.migration_runner import migrate
receipt = migrate(connection, expected_current_version=15, target_version=18)
```

The expected version is mandatory. The runner takes the existing advisory lock,
validates every recorded name/checksum/version, applies a contiguous prefix in
one transaction, and returns runner-contract-1 metadata. It rejects history
gaps, a database ahead of this package, unexpected versions, and downgrades.
`inspect_schema(connection)` in `memoriesql.infrastructure.postgres.schema_inspection`
returns normalized schema metadata; it reads no content rows.

`Migration.path` is a package resource implementing `Traversable`, not a promise
of a checkout filesystem path. Its read methods work for filesystem and ZIP
resources without an expired temporary-file context. Explicit-directory discovery
is a bounded test hook; `migrate()` always uses installed resources.

## Authority, history, and recovery

`contracts/migration-inventory.json` explicitly owns all 18 filenames and hashes. Root `migrations/` is the sole authored stream. Setuptools
stages those exact bytes into the wheel; the sdist retains root SQL and rebuilds
the same resources. There is no editable second stream or fallback discovery.
The public provenance inventory records copy/adapt decisions and opaque content
hashes. The three historical profile identifiers in migration 0014 remain only
in that unchanged SQL, schema snapshot, and fictional compatibility tests; they
establish no provider implementation or qualification.

No SQL is rewritten, renumbered, or added by release preparation. Historical
within-observation updates remain historical behavior; schema 15 supplies explicit
distinct-bead corrections. Complete-input correction/reauthoring remains deferred. Product
extensions must use a separate schema and ledger, never change core-owned
objects. The existing catalog CLI is unchanged.

There is no down migration. Back up before an authorized upgrade; recovery means
restoring a verified pre-upgrade backup to a separate database and checking its
history with a compatible package, or applying a separately reviewed forward
fix. Do not delete migration rows, rewrite checksums, or resume incompatible
work against a restored database. This cut does not automate backup or recovery.

## Verification

The public-owned suite covers fresh creation, direct frozen-prefix upgrades
4→14, 11→14, 13→14, no-op 14→14, immutable history receipts, schema/ACL parity,
and rejection cases. Wheel and independently rebuilt sdist-wheel tests run in
clean environments, from copied fictional tests, with checkout filesystem access
denied, `PYTHONPATH` unset and Python isolation enabled. Distribution RECORD
ownership, all SQL bytes, and the published 49 records/12 preview APIs are checked.
The installed schema-15 suite additionally proves a populated 14→15 upgrade,
immutable accepted semantics, occurrence replay, explicit correction branches,
old receipt replay, forced RLS and operational fences. Provider calls, product
adoption, checkpoints, tags and publication remain outside this change.

## Schema 15 transition

All SQL 0001–0014 remains byte-for-byte unchanged. Migration 0015 replaces the
active capture/cardinality logic to select the initial bead per occurrence,
backfills an accepted-version seal, and installs immutable semantic/lineage
guards and explicit version-2 commands. Existing historical versions are neither
rewritten nor renumbered. No global semantic head or winner is introduced.

An existing schema-14 database upgrades only through the explicit call
`migrate(connection, expected_current_version=14, target_version=15)`. Successful
version-1 receipts replay exactly; an unfinished initial version can complete.
Legacy accepted-bead revisions fail explicitly after upgrade. Prepare consumers
for the [version-2 commands](immutable-observations.md) before a separately
authorized deployment. There is no automatic product upgrade in this package.

## Schema 16 evidence storage in 0.0.5

Schema 16 is the evidence-storage prefix of the 18-resource inventory. The
transition preserves SQL 0001–0015 exactly and adds only `evidence_packages`, `evidence_package_parts`, their
immutability/RLS guards and versioned authorized operations. The existing shared
idempotency ledger supplies receipts. Package rows hold declaration/assembly/seal;
part rows hold independently bounded normalized content and raw lineage. No raw,
semantic-task or observation ledger is duplicated. See [the contract](evidence-packages.md).

An authorized consumer selecting only this prefix must explicitly request `migrate(connection,
expected_current_version=15, target_version=16)` under deployment authority. Neither
installing a package nor constructing a reader runs migration or selects product
composition. No deployed migration or consumer upgrade is authorized here.

## Schema 17 qualified materialization in 0.0.5

A qualified sealed package can atomically materialize its stable source-native
unit, initial thin bead, exact-package unavailable task, outbox effects and shared
receipts. Genuine complete units can progress independently of pending siblings.
No semantic output is authored. Schema-17 inputs, pins and availability snapshots
remain immutable, including after a schema-18 successor activation. See
[materialization](logical-unit-materialization.md).

## Schema 18 complete-input execution in 0.0.5

The installed inventory contains 18 resources. Migrations 0001–0017 remain
byte-for-byte unchanged. Migration 0018 adds explicit binding transfer, reader v2,
trusted dispatch exposure and fenced initial semantic application through the
existing queue and canonical sink. It seeds no producer/dispatch trust or worker
claim policy. Upgrade and caller composition require separate deployment authority;
this implementation does not deploy. See [the execution contract](complete-input-execution.md).

Installing/upgrading the Python package does not execute these migrations. Executing
an authorized migration does not provision production trust, activate a binding,
configure a provider or select worker composition. Use the explicit
[caller opt-in](releasing.md#caller-composition-and-explicit-opt-in). Mechanical
exposure is not comprehension or independent source completeness. Rolling-note
quality and execution ceilings remain experimental; complete-input corrections
and reauthoring are not delivered.
