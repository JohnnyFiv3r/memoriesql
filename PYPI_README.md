# memoriesQL catalogs and provider-neutral runtime

`memoriesql` 0.0.1a1 is an experimental, pre-alpha, read-only package for inspecting governed memoriesQL contract catalogs.

It bundles exactly 49 provider-neutral records: 43 JSON Schema records, five Python-surface records, and one synthetic reference connector. Five additional catalog kinds are present as explicit empty `not_implemented` documents. Twelve experimental Python and CLI APIs provide catalog listing and exact record lookup.

The unreleased `0.0.2` adds canonical migrations and the provider-neutral authorization, capture/range/fold, canonical transaction, queue, executor and accounting runtime. Synthetic models exercise execution mechanics. Desktop composition, live acquisition, real-provider bindings, recall and a running background service are not included.

```console
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The new migration substrate requires Python 3.13+ (initially qualified on 3.13/3.14, metadata `>=3.13,<3.15`) and the audited dependencies `psycopg[binary]==3.3.3`, `pydantic==2.13.3`, `pydantic-ai-slim==2.27.0` without provider extras. The published `0.0.1a1` remains catalog-only with its original Python support. Install the exact reviewed `0.0.2` wheel for migration capabilities; an unpinned install on older Python may select the old catalog package instead. Catalog reads remain resource-only and independent of database imports.

All APIs are experimental throughout 0.x and have no general compatibility guarantee. A published contract ID and version payload is immutable; breaking contract changes require a new contract version. An uploaded distribution is corrected only through a new distribution version.

Source is licensed under Apache-2.0. The memoriesQL name and brand assets are not granted as trademarks by that license.
