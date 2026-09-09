![memoriesQL — observe, process, store, recall](assets/trademarks/memoriesql-readme-banner.png)

# memoriesQL catalogs and provider-neutral runtime

This public repository preserves the memoriesQL contract preview: a small, read-only Python package exposing 49 provider-neutral contract records through eight generated catalogs and 12 experimental Python/CLI APIs.

The unreleased `0.0.2a1` adds canonical PostgreSQL migrations and the provider-neutral authorization, canonical transaction, capture/range/fold, queue, executor and accounting runtime. It is not the memoriesQL desktop product, a capture service, a recall engine, a background service, or a provider integration. Catalog reads do not access external services.

## Preview the catalogs

Python 3.13+ is required for the unreleased substrate; Python 3.13 and 3.14 are initially qualified. The immutable `0.0.1a1` catalog-only release retains its original Python 3.11–3.14 support.

```console
python3.13 -m pip install ./dist/memoriesql-0.0.2a1-py3-none-any.whl
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The runtime depends on `psycopg[binary]==3.3.3`, `pydantic==2.13.3` and `pydantic-ai-slim==2.27.0` without provider extras. Catalog reads use immutable package resources and do not import the database dependency. See [migration setup and recovery](docs/migrations.md).

## Authority and compatibility

[`contracts/public-registry.json`](contracts/public-registry.json) is the sole export and generation authority. It names every included record explicitly and denies every unlisted file by default. Generated package catalogs must match it byte-for-byte.

Every API is experimental throughout 0.x, with no general compatibility guarantee. Once a distribution is published, the payload for a published contract ID and version is immutable. Breaking contract changes require a new contract version, and an uploaded distribution is corrected only by publishing a new distribution version.

See [compatibility policy](docs/architecture/compatibility.md) and [repository boundary](docs/architecture/repository-boundary.md).

## Licensing and contribution

Source code and designated materials are licensed under [Apache License 2.0](LICENSE). The memoriesQL name and banner remain subject to [trademark guidance](TRADEMARKS.md). Contributions use the [Developer Certificate of Origin](CONTRIBUTING.md), not a contributor license agreement.

The repository is public; package publication is a separate owner-approved step. The release workflow is restricted to the approved preview tag, exact-main-head CI artifacts, and the protected `pypi` environment. See [release controls](docs/releasing.md). The published `0.0.1a1` stays immutable. This change authorizes no new release.
