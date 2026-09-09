# Repository boundary

The canonical public repository, `JohnnyFiv3r/memoriesql`, is the authority for provider-neutral memoriesQL contracts and, after separate audits, the open-core ledger, authorization, idempotency, capture protocol, typed retrieval, and canonical schema migrations. The owner authorized public visibility on 2026-09-08. This does not expand the audited package or runtime boundary.

The initial `0.0.1a1` preview contains only the explicit contract registry, 49 approved records, generated catalogs, the read-only Python/CLI facade, focused verification, and minimal project governance. It intentionally contains no runtime implementation candidates or migrations.

Private product material stays outside this repository: desktop surfaces and supervision, installers, signing, updates, hosted services, sync, provider-specific discovery/parsing/acquisition/support profiles, Connections product behavior, product module manifests, private evidence, and user data.

The eventual product repository may depend only on released, pinned public-core versions. The public core must never import the product repository. Public and product migrations will use separate namespaces and ledgers; product migrations must not alter public-core tables.

## Default-deny extraction

`contracts/public-registry.json` is the only catalog-generation authority. The generator reads only its explicit entries, validates record hashes and dispositions, rejects unregistered record files, and never scans another repository. The prior classification-by-prefix spike is evidence, not authority.

The approved migration substrate cut includes only SQL 0001–0014, migration runner, schema inspection, and minimal resource/initializer closure. `contracts/migration-inventory.json` and `docs/provenance/migration-extraction.json` enumerate that public-owned scope. All other runtime candidates remain deferred. See [migration guidance](../migrations.md).
