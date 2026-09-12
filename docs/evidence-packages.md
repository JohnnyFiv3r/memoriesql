# Complete-input evidence packages, version 1

This public-core path, included in the 0.0.5 candidate, stores and reads the complete normalized evidence
inventory declared for one source-native logical unit. It creates no source event,
source unit, observation, bead, semantic task, authoring run or accepted memory.
It neither invokes providers nor proves that an author received or understood input.

The new `memoriesql.evidence-package.v1` catalog record describes the typed commands,
reader and responses in `application.evidence_packages`. The PostgreSQL adapter is
`PostgresEvidencePackages`. Migration 0016 adds the canonical operations and two
explicitly inventoried tables. A released core dependency must precede product use;
0.0.5 release readiness is documented in [the release guide](releasing.md).
Installation does not provision producer qualification or opt into later
materialization/execution; those are separate commands and caller composition.

## Identity, inventory and qualification

A logical occurrence is identified by `(source_object_id, source_revision_key,
occurrence_key)` within its tenant. Acquisition windows, raw receipt IDs, operation
keys, content hashes and read sizes do not enter that occurrence identity. The
producer must reuse an occurrence key across retries/acquisitions of that occurrence.
Equal words in two distinct occurrences require distinct occurrence keys.

`occurrence_identity_basis` distinguishes a native identifier from a producer-assigned
stable key. `native.native_id` is mandatory for the native basis. Null native IDs,
parents, sessions, branches, participants, roles, order and timestamps mean unknown.
They must never be filled with internal UUIDs presented as native facts. Preserve
source timestamp text and precision alongside a normalized timestamp when known.

A package is a representation of that occurrence: normalization-policy version plus
`package_revision`. Its declaration, producer principal and eventual inventory seal
are immutable. A changed declaration under the same representation conflicts. A
new explicit revision may represent subsequently completed evidence or changed
normalization/receipt lineage without changing the logical occurrence. This slice
neither selects a winning revision nor changes an existing sealed package.

The declaration commits expected part, Unicode-character and UTF-8-byte totals and
an ordered inventory digest. Every part contains its opaque ID, ordinal, logical
component key and character offset, optional parent component, kind, native facts,
content hash, exact content and raw derivation. Inventory entries omit content but
include its exact lengths. The inventory digest is SHA-256 of concatenated lowercase
SHA-256 hex digests of the entries' canonical ASCII JSON, in ordinal order. Part IDs
are producer-stable storage identities, not source-native IDs.

Parts include all required messages, tool requests/results and structural context.
A large component may use consecutive storage fragments with the same component key,
unchanged component metadata and contiguous character offsets. These are transport
and storage portions of one logical component in one package, never semantic splits.
Parts and reader pages do not create memories. Content order is explicit; relationships
must not be inferred from a convenient acquisition window or a missing participant.

Each part references 1–16 exact raw-receipt byte intervals and their SHA-256 hashes.
Core verifies source object/revision, receipt bounds, retained byte continuity and
hashes. Where supplied, `(fold_receipt_id, fold_outcome_ordinal)` must name an outcome
covering that interval in the same source revision and physical file identity. A fold
reference preserves lineage; it does not itself qualify a logical boundary or override
a policy disposition. Producers must retain applicable fold references. Missing or
inapplicable fold references cannot be inferred by this provider-neutral store.
`identity_utf8` additionally requires exact equality to concatenated retained bytes;
`producer_normalized` records a transformation whose correctness the producer owns.
No second raw ledger is introduced.

The qualification assertion has separate fields for physical completeness, boundary
and its justification, topology certainty, normalized-input completeness, source
completeness attestation, exclusions and unresolved coverage. The qualified producer
owns source-format interpretation, the required component set, boundaries, truthful
exclusions and the assertion that normalization omitted no required content. An
exclusion must describe evidence outside the declared unit or legitimately excluded
by its named policy; it must not hide a missing required component. Shortened legacy
envelopes, disclosed truncation, previews and summaries do not satisfy this assertion.

Core independently validates storage coverage of the declared inventory, contiguous
ordering/component offsets, hashes, totals, retained lineage, immutability and current
authority. Core does **not** independently establish source completeness. The status
always returns `independently_proven_source_complete=false`, including for a dishonest
but internally consistent declaration. `producer_principal_id` records who asserted
qualification; `qualification_ref` names the producer's source-qualification basis,
not a core-issued certification. Qualification and normalization implementations
belong to the product/provider repository and must have their own source tests.

## Assembly, sealing and independent progress

- `create` declares one exact representation and returns a package/operation receipt.
- `append` adds exactly the next ordered part in an atomic transaction. A duplicate
  identical part is harmless; a changed part, hash, component metadata or same-key
  command fails. Replaying a successful operation preserves its receipt.
- `seal` checks all committed parts/totals and the exact ordered digest. Missing
  declared parts or a changed inventory fail with `evidence_inventory_incomplete`.
  Sealing scans small hashes, not the complete text. A failed or interrupted command
  commits neither its part/seal nor its shared idempotency receipt. A committed command
  whose reply was lost replays through that receipt.

Sealing means **inventory immutable**, not **source complete**. An unresolved or
physically pending inventory can be sealed and inspected truthfully, while readiness
remains `pending_source_qualification`. An otherwise qualified, physically complete,
fully assembled and sealed unit with no unresolved coverage becomes
`ready_producer_attested`. Unknown topology alone does not block it. Unsealed complete
candidates report `assembling`; unresolved boundaries/tails remain visibly pending.
A pending sibling, session or trailing record never gates another complete package.
To add evidence to a previously sealed pending representation, create an explicit new
package revision; raw evidence and the earlier pending inventory remain intact.

## Reader and current authority

`inspect` returns declaration, assembly/seal status, producer and readiness.
`inventory` pages ordered typed entries of a sealed package. `read` returns a bounded
Unicode character interval of one declared part, the sealed inventory hash, part
hash, interval content/hash, UTF-8 length, continuation and terminal flag. Joining
successive content intervals reconstructs the exact text, including JSON escapes,
non-ASCII characters, combining characters and newlines. Boundaries are Unicode code
points, not UTF-8 bytes or grapheme clusters. Raw byte intervals remain separately
specified in lineage. Reads of unresolved sealed packages do not promote readiness.

A continuation carries package ID, inventory hash, optional part ID and next ordinal
or character offset. Server validation rejects another package/hash/part and offsets
outside the sealed inventory. Valid bounded seeking is permitted: the cursor is not
a secret or proof of having consumed previous pages. A caller must use the same
package and append content in interval order to reconstruct it. There are no arbitrary
paths, URLs, SQL fragments, credentials or external handles in read requests.

Every operation resolves current credentials, tenant/workspace, scope, source resource,
origin/delegation and capability intersection. It uses existing resource authorization
and its mutation fence, rechecking after waits and before return. Writes require both
`memory.capture` and `source.raw.read`; status/inventory/content require current
`source.raw.read`. Existing role grants are unchanged: raw-read is initially an owner
capability, not implicitly a paired-device/service capability. Capture authority alone
never authorizes reading or provider disclosure. Revoked credentials, originating
principals, grants or source authority deny subsequent operations, including retries.
The two tables have forced RLS and no application table grants; only the authorized
security-definer operations are exposed. Inspection is audited by the existing
resource-authorization mechanism; those audits are not authoring exposure receipts.

## Bounds and overflow behavior

| Surface | Version-1 limit and rationale |
| --- | --- |
| Package | 256 parts; 16 MiB normalized UTF-8 total. A bounded inventory can hold many messages/tool results or 256 fragments without hydrating it at seal. |
| Append | One part, at most 64 KiB UTF-8; declaration and each inventory entry at most 8 KiB canonical JSON. This accommodates the 12,000-character fixture and keeps each operation small. |
| Raw derivation | At most 16 receipt intervals and 256 KiB of referenced bytes per append. Existing receipts contain at most 256 chunks/256 KiB each: at most 4,096 chunk rows and 4 MiB raw payload examined, with at most 256 KiB assembled slices. |
| Command | At most 512 KiB serialized JSON; adapter checks canonical JSON, SQL also checks its input representation before work. Worst-case escaping of a 64 KiB content string still fits with bounded metadata. Transport owners must cap incoming requests before JSON parsing. |
| Inventory read | 1–4 entries through the `(tenant, package, ordinal)` index; at most 32 KiB canonical entry data plus response envelope. |
| Content read | 1–1,024 Unicode characters from one part of at most 64 KiB. No scan of other parts. |
| Response | At most 64 KiB canonical JSON, checked in SQL and adapter. |
| Seal | At most 256 entry hashes (16 KiB digest input), plus a package row. No content scan. |
| Time | 500 ms per lock wait; two-second SQL work deadline. The adapter owns a short transaction and sets a two-second statement timeout, including authorization statements. This is a server work bound, not a network/client scheduling SLA. |

Per-append component checks inspect at most 256 bounded inventory entries (2 MiB
metadata). Raw reconstruction retains at most 256 KiB slices plus one bounded receipt
result set and command. Work is independent of total session length or unrelated
source history, aside from indexed lookup cost and the existing authorization engine.
No operation assembles the 16 MiB package in memory. The SQL guard is also applied to
direct function calls; trusted transport still owns pre-database parsing/admission.
Server-local idempotency hashes use deterministic PostgreSQL JSONB serialization to
avoid the inherited per-character canonicalizer over large text. Cross-language
inventory hashes retain their explicit canonical format.

These are a distinct versioned path; published eight-unit/4,096-character/4,096-byte
limits and payload meanings are unchanged. An input that exceeds this path's bounds
fails explicitly. Retained raw evidence remains durable; the producer must report it
as unsupported/pending, never truncate it, call it ready, manufacture extra logical
units or silently raise limits. A later contract/owner decision is required for a unit
that cannot be represented within these explicit bounds. This is not a claim of
unlimited source support.

## Later execution boundary

The reader makes exact evidence available for inspection. Later separately reviewed
work must bind trusted exposure coverage to a durable task, attempt/run, sealed package
and exact exposed part intervals, and recheck authorization during hydration and
canonical apply. A successful server read is not proof that an authoring execution
received the response. An untrusted “I used everything” assertion cannot supply
coverage. Exposure itself proves neither comprehension nor semantic correctness.

This contract adds no worker/executor/apply coverage, scheduling, mandatory author or
reviewer calls, maintenance, batching, provider-call-count assumptions or product UI.
The existing delivery checklist remains the program authority; private delivery
updates and product composition do not belong in this public-core slice.

## Public fictional acceptance

`tests/runtime/test_evidence_packages.py` uses only public-authored fictional orchard
content in disposable PostgreSQL databases. It covers all required message/tool/context
parts and known topology, user-only/tool-only and unknown topology, unresolved tails
and independent progress, 12,000-character Unicode/escape reconstruction, multiple raw
acquisition ranges and fold lineage, duplicate/changed-key behavior, missing parts and
hashes, invalid cursors, append/seal rollback and reply replay, credential/delegation
revocation, cross-principal/workspace denial and direct table denial. The delegation
fixture explicitly configures a fictional role grant; production role grants are
unchanged. A self-consistent incomplete declaration is tested as producer-attested,
never independently proven source-complete.

The maximum fixture exercises 256 64-KiB fragments in one package, bounded append/seal
latency and indexed inventory paging. It deliberately reuses a retained fictional
block to test storage limits, not source interpretation. Lock contention tests prove
bounded failure with no partial append or receipt. Installed-package checks verify
resource ownership, full migration history, unchanged published records and legacy
runtime behavior. None of this is semantic acceptance or a model-inspection claim.
