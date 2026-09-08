# memoriesQL contract preview

`memoriesql` 0.0.1a1 is an experimental, pre-alpha, read-only package for inspecting governed memoriesQL contract catalogs.

It bundles exactly 49 provider-neutral records: 43 JSON Schema records, five Python-surface records, and one synthetic reference connector. Five additional catalog kinds are present as explicit empty `not_implemented` documents. Twelve experimental Python and CLI APIs provide catalog listing and exact record lookup.

This package is not the memoriesQL desktop product. It does not provide a database runtime, capture, recall, background services, provider integration, local discovery, or access to user content.

```console
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The package supports Python 3.11 through 3.14, is pure Python, has no runtime dependencies, and reads only immutable resources bundled in the wheel.

All APIs are experimental throughout 0.x and have no general compatibility guarantee. A published contract ID and version payload is immutable; breaking contract changes require a new contract version. An uploaded distribution is corrected only through a new distribution version.

Source is licensed under Apache-2.0. The memoriesQL name and brand assets are not granted as trademarks by that license.
