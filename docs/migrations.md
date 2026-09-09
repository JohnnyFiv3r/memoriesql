# Canonical migration substrate (unreleased)

The `0.0.2` development artifact adds the canonical migration substrate.
**Python 3.13+ is required; the initial qualification matrix is 3.13 and 3.14
(`>=3.13,<3.15`).** It is not a complete capture, authoring, or recall runtime.
No release or upload is authorized by this change.

The published `0.0.1a1` remains an immutable catalog-only preview supporting
Python 3.11–3.14. An unpinned installer on older Python may select that older
catalog distribution; that does not install migration capabilities. Use the
exact reviewed artifact and version, never an unbounded `pip install memoriesql`
as a runtime setup instruction:

```console
python3.13 -m venv migration-env
migration-env/bin/python -m pip install ./memoriesql-0.0.2-py3-none-any.whl
migration-env/bin/python -c "from importlib.metadata import version; assert version('memoriesql') == '0.0.2'; from memoriesql.infrastructure.postgres.migration_runner import discover_migrations; assert len(discover_migrations()) == 14"
```

The installer rejects this artifact on unsupported interpreters. A future runtime
release needs separate approval and an explicit version pin. The only runtime
dependency in this cut is `psycopg[binary]==3.3.3`; there are no model SDK extras.
Catalog reads remain independent of runtime imports and external I/O.

## Bounded application

With an explicitly supplied, idle `psycopg` connection to the intended database:

```python
from memoriesql.infrastructure.postgres.migration_runner import migrate
receipt = migrate(connection, expected_current_version=11, target_version=14)
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

`contracts/migration-inventory.json` explicitly owns all fourteen historical
filenames and hashes. Root `migrations/` is the sole authored stream. Setuptools
stages those exact bytes into the wheel; the sdist retains root SQL and rebuilds
the same resources. There is no editable second stream or fallback discovery.
The public provenance inventory records copy/adapt decisions and opaque content
hashes. The three historical profile identifiers in migration 0014 remain only
in that unchanged SQL, schema snapshot, and fictional compatibility tests; they
establish no provider implementation or qualification.

No SQL is rewritten, renumbered, or added. Historical within-observation semantic
updates remain historical behavior; correction semantics are deferred. Product
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
N2 runtime, N3 consumption, correction contracts, provider calls, product
checkpoints, tags and publication are deferred.
