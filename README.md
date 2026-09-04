![memoriesQL — observe, process, store, recall](assets/trademarks/memoriesql-readme-banner.png)

# memoriesQL contract preview

This private staging repository contains the proposed public bootstrap for memoriesQL: a small, read-only Python package exposing 49 provider-neutral contract records through eight generated catalogs and 12 experimental Python/CLI APIs.

It is a pre-alpha contract preview—not the memoriesQL desktop product, a database runtime, a capture service, a recall engine, a background service, or a provider integration. It does not inspect local projects, transcripts, databases, credentials, or network services.

## Preview the catalogs

Python 3.11 through 3.14 is supported by the package metadata and verification matrix.

```console
python -m pip install --no-deps ./dist/memoriesql-0.0.1a1-py3-none-any.whl
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The wheel is pure Python and declares no runtime dependencies. Catalog reads use immutable package resources only.

## Authority and compatibility

[`contracts/public-registry.json`](contracts/public-registry.json) is the sole export and generation authority. It names every included record explicitly and denies every unlisted file by default. Generated package catalogs must match it byte-for-byte.

Every API is experimental throughout 0.x, with no general compatibility guarantee. Once a distribution is published, the payload for a published contract ID and version is immutable. Breaking contract changes require a new contract version, and an uploaded distribution is corrected only by publishing a new distribution version.

See [compatibility policy](docs/architecture/compatibility.md) and [repository boundary](docs/architecture/repository-boundary.md).

## Licensing and contribution

Source code and designated materials are licensed under [Apache License 2.0](LICENSE). The memoriesQL name and banner remain subject to [trademark guidance](TRADEMARKS.md). Contributions use the [Developer Certificate of Origin](CONTRIBUTING.md), not a contributor license agreement.

The repository remains private while the owner reviews this bootstrap. No package has been released, no Trusted Publisher is configured, and the publishing workflow is retained only as an inert template outside `.github/workflows`.
