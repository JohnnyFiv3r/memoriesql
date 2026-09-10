# memoriesQL catalogs and provider-neutral runtime

`memoriesql` is an experimental, pre-alpha package for governed contract catalogs and provider-neutral runtime mechanics. The immutable `0.0.1a1` release remains catalog-only.

It preserves the 49 published provider-neutral records: 43 JSON Schema records, five Python-surface records, and one synthetic reference connector. Current unreleased development adds `memoriesql.evidence-package.v1` as the 50th record (44 JSON Schema records). Five additional catalog kinds are present as explicit empty `not_implemented` documents. Twelve experimental Python and CLI APIs provide catalog listing and exact record lookup.

Published `0.0.2` introduced canonical migrations and the provider-neutral authorization, capture/range/fold, canonical transaction, queue, executor and accounting runtime. Synthetic models exercise execution mechanics. Desktop composition, live acquisition, real-provider bindings, recall and a running background service are not included.

```console
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor = get_contract("memoriesql.capture.connector-cursor")
```

The new migration substrate requires Python 3.13+ (initially qualified on 3.13/3.14, metadata `>=3.13,<3.15`) and the audited dependencies `psycopg[binary]==3.3.3`, `pydantic==2.13.3`, `pydantic-ai-slim==2.27.0` without provider extras. The published `0.0.1a1` remains catalog-only with its original Python support. Install an exact released runtime version for migration capabilities; an unpinned install on older Python may fail because the runtime is incompatible and pip excludes prereleases by default. For catalogs on older Python, explicitly pin `memoriesql==0.0.1a1`; allowing prereleases with `--pre` also makes that historical fallback eligible. Catalog reads remain resource-only and independent of database imports.

All APIs are experimental throughout 0.x and have no general compatibility guarantee. A published contract ID and version payload is immutable; breaking contract changes require a new contract version. An uploaded distribution is corrected only through a new distribution version.

Source is licensed under Apache-2.0. The memoriesQL name and brand assets are not granted as trademarks by that license.
Published `0.0.3` establishes cleanup retention independently of a blocked ordinary heartbeat while retaining ownership of unfinished work.

Published `0.0.4` adds schema 15 and explicit version-2 initial-authoring and correction commands. Accepted meaning, type and summary are immutable. Corrections create new beads with explicit pinned supersession; independent branches and multi-target reconciliation are supported without choosing a global winner. Occurrence replay still returns the original initial bead. Historical versions and successful version-1 receipts remain readable under current authority. Legacy attempts to revise an accepted bead fail explicitly and require an updated caller. Publication and product adoption require separate authorization.

Unreleased schema 16 adds immutable complete-input evidence inventories and bounded
authorized reads. Storage completeness remains distinct from producer-attested source
completeness. No semantic task, provider call or accepted memory is created by this
path. No future release version is selected; development artifacts must not replace
published `0.0.4` artifacts.
