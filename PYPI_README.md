# memoriesQL catalogs and migration substrate

`memoriesql` 0.0.1a1 is an experimental, pre-alpha, read-only package for inspecting governed memoriesQL contract catalogs.

It bundles exactly 49 provider-neutral records: 43 JSON Schema records, five Python-surface records, and one synthetic reference connector. Five additional catalog kinds are present as explicit empty `not_implemented` documents. Twelve experimental Python and CLI APIs provide catalog listing and exact record lookup.

The unreleased `0.0.2a1` adds only canonical migrations and schema inspection. This package is not the memoriesQL desktop product. It does not provide capture, recall, authoring, background services, provider integration, or local discovery.

```console
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The new migration substrate requires Python 3.13+ (initially qualified on 3.13/3.14, metadata `>=3.13,<3.15`) and `psycopg[binary]==3.3.3`. The published `0.0.1a1` remains catalog-only with its original Python support. Install the exact reviewed `0.0.2a1` wheel for migration capabilities; an unpinned install on older Python may select the old catalog package instead. Catalog reads remain resource-only and independent of database imports.

All APIs are experimental throughout 0.x and have no general compatibility guarantee. A published contract ID and version payload is immutable; breaking contract changes require a new contract version. An uploaded distribution is corrected only through a new distribution version.

Source is licensed under Apache-2.0. The memoriesQL name and brand assets are not granted as trademarks by that license.
