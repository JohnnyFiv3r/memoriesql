# PR-05: agent-authored SELECT and reusable results — decision packet 2

Status: **owner-amended contract proposal; Phase A**, 2026-09-26. The product
decisions below are approved; the amended exact interface and measured policy
qualification remain incomplete. This is the public proposal authority, not an
installed capability, executable contract or frozen interface.
Public main was reverified as `4ee8243045cf52ec2b0b7e81a842cb3b02f716de`;
the merged Desktop planning reference as `f2d03ecac4831f9231d32fea9d04728f8fd415a6`.
This independently authored specification reconciles the owner amendment to
ADR-0012, SQL-02 §5.0 revision 3, SQL-10, EG-0001 and the PR-05 execution plan.
The corresponding product-doc amendment is not yet merged. It contains no private
corpus, questions, gold, evidence artifacts or upstream source adaptation.

## Decision requested

The owner approved broader qualified SQL composition, relation-ready recall,
resumable branching investigation checkpoints, whole-result refusal after required
dependency loss, a 30-day temporary-result target and durable explicitly saved
investigations. The prior packet at `11692c3` is not approved as-is. Complete the
relation and checkpoint wire cuts and workload-grounded policy choices below,
then present one revised exact interface for approval. Existing proposed fields
are retained for review, not silently frozen by this amendment.

| Consequential choice | Recommended default | Material alternative / cost |
| --- | --- | --- |
| SQL and provenance | Useful standard composition, including set/window forms; qualify each supported provenance shape | Any exclusion needs a concrete authorization, provenance or execution limitation; bounded recursion needs its own proof, not a blanket grant |
| Changed authority | Refuse the entire affected result and its descendants, including metadata and aggregates | An explicitly new reduced-authority child needs a separately qualified exclusion contract; omit initially |
| Time | Same-frame saved-input refinement; fresh, labelled live children for expansion/refresh | Mixed-frame comparison needs a separate typed interpretation; omit initially |
| Investigation history | Target 20 automatic checkpoints plus named saved checkpoints; restore creates a branch | Checkpoint count is not a query/result-generation limit; exact persistence and resume envelopes need approval |
| Retention | Target 30 days for temporary results; saved investigations persist without routine expiry while saved, subject to disclosed quotas and erasure | Retain required dependency closure; no earliest-parent expiry trap, silent eviction or arbitrary ancestry-depth cap |
| Work | Ground duration/byte/reservation/concurrency limits in representative workloads and provenance/storage measurements | Table values below remain unapproved qualification candidates, not capacity guarantees; no silent escalation or examined-row promise without a fence |
| Relation readiness | Complete and release the bounded assessed-assertion lifecycle dependency and expose qualified relation projections | Observation-only mechanics can progress independently but do not satisfy the intended relation-aware delivery; unrelated claims capabilities need not block it |

The owner authorized the five-step delivery sequence: reconcile this packet and
product roadmap; scope assessed-relation lifecycle completion; review and explicitly
approve the amended exact interface and remaining policy; independently freeze the
contract-bound unseen holdout; then implement, qualify and release publicly before
private consumption. This does not waive any gate, choose a version, approve a
protected upload, or authorize provider spending, owner-data access or deployment.

**Implementation gates:** an explicit owner decision must identify the approved
packet commit and its contract/policy digest, including any recorded amendments.
Separately, the independent PR-04/evaluation custodian must attest that a loadable
source-attributed corpus is ready and an unseen holdout was frozen **against that
approved digest before any PR-05 runtime or candidate-condition implementation**.
Its attestation names the sealed holdout manifest digest, both conditions and
unchanged threshold/profile manifests, without exposing questions or gold.
The implementer neither creates nor reads that holdout. A prose merge, architecture
approval, or green CI satisfies neither gate. Both gates are presently **pending**.

Approval digest procedure v1: `packet_sha256` is lowercase SHA-256 of the exact
Git blob bytes at `<packet_commit>:docs/agent-sql-results-v1.md`, including its
final newline; no Markdown rendering, Unicode normalization or whitespace cleanup.
In Bash/Zsh, enable `set -o pipefail`, then reproduce with
`git show <packet_commit>:docs/agent-sql-results-v1.md | shasum -a 256`.
Record a digest only on zero pipeline exit; missing/mistyped commit or blob fails
the gate even if the last program printed an empty-input hash.
The closed approval record is `{version:1,packet_commit:text(40 lowercase hex),
packet_path:"docs/agent-sql-results-v1.md",algorithm:"sha256",packet_sha256:text,
approval_ref:text,amendments:[{amendment_ref:text,supersedes_packet_sha256:text}]}`.
Initial amendments is empty. Any amendment must first be incorporated into a new
committed packet; the record lists its amendment ref/preceding digest, and the
owner explicitly approves the new whole-packet digest. An external prose exception
cannot alter the effective contract. The custodian attestation repeats that exact
record/digest and its separate sealed evaluation manifest digests; a mismatch
requires a new approval/freeze rather than an inferred equivalent contract.

## 1. Exact logical catalog

Proposed contract ID `memoriesql.agent-sql-results.v1`, contract version 1;
logical namespace `memory_v1`, catalog revision 1. One future entry in
`contracts/public-registry.json` must generate admission tables, types, installed
help and references. Do not register this proposal as delivered. Only the named
columns below exist; no physical catalogs, credentials, tenant predicates or
generic introspection. PostgreSQL types apply: `uuid`, `text`, `bool`, `int8`,
`timestamptz`, `numeric`, `float8`; `?` means nullable. Text enums are closed as
specified. Result schemas carry each column's PostgreSQL and semantic reference
type, nullability, and evidence effect. No arbitrary JSON/array SQL operators.

Every relation is scope/frame-bound and authorization-filtered **before** agent
expressions. Every row has a protected internal witness/dependency binding;
agents cannot alter it or select internal authorization columns.

| Relation (key) | Exact public columns beyond the key |
| --- | --- |
| `observations` (`bead_id:uuid`, `bead_version_id:uuid`) | `event_id:uuid`, `source_unit_id:uuid`, `bead_type_key:text`, `bead_type_revision_id:uuid`, `title:text?`, `summary:text?`, `detail:text?`, `render_state:text` = `present\|unsupported`, `recorded_at:timestamptz`, `effective_at:timestamptz?`, `effective_basis:text` = `authored\|source\|unknown`, `correction_state:text` = `unsuperseded\|superseded\|branched` |
| `statements` (`statement_id:uuid`) | `bead_id:uuid`, `bead_version_id:uuid`, `sequence:int8`, `kind:text` = `observation\|context\|qualification\|correction`, `text:text`, `supersedes_statement_id:uuid?`, `correction_reason:text?`, `recorded_at:timestamptz` |
| `statement_sources` (`statement_id:uuid`, `source_unit_id:uuid`) | `event_id:uuid`, `content_sha256:text` (64 lowercase hex), `evidence_ref:uuid` (server-issued exact unit-support pin) |
| `source_units` (`source_unit_id:uuid`, `content_sha256:text`) | `event_id:uuid`, `source_object_id:uuid`, `source_kind:text` = `transcript\|document\|media\|relational\|operational`, `package_revision_id:uuid?`, `search_text:text?`, `text_state:text` = `available\|unsupported`, `source_occurred_at:timestamptz?`, `source_time_original:text?`, `source_timezone:text?`, `source_precision:text`, `recorded_at:timestamptz`, `actor_ref:text?`, `actor_role:text?`, `trust_label:text?`, `occurrence_ref:uuid?` |
| `corrections` (`predecessor_version_id:uuid`, `successor_version_id:uuid`) | `predecessor_bead_id:uuid`, `successor_bead_id:uuid`, `reason:text`, `recorded_at:timestamptz` |
| `entity_mentions` (`mention_id:uuid`) | `bead_id:uuid`, `bead_version_id:uuid`, `surface_text:text`, `local_state:text?` = `unresolved\|ambiguous`, `local_reason:text?`, `resolution_state:text` = `unresolved\|resolved\|ambiguous\|rejected\|unsupported`, `resolution_id:uuid?`, `recorded_at:timestamptz` |
| `mention_entities` (`mention_id:uuid`, `resolution_id:uuid`, `entity_id:uuid`) | `entity_revision_id:uuid` (candidate identity from the recorded decision; revision resolved at the frame, not falsely presented as pinned by that decision) |
| `entities` (`entity_id:uuid`, `entity_revision_id:uuid`) | `entity_type_key:text?`, `canonical_label:text`, `recorded_at:timestamptz` |
| `entity_aliases` (`alias_id:uuid`) | `entity_id:uuid`, `alias:text`, `alias_kind:text` = `name\|acronym\|identifier\|nickname`, `recorded_at:timestamptz` |
| `topics` (`topic_id:uuid`, `topic_revision_id:uuid`) | `label:text`, `recorded_at:timestamptz` |
| `observation_topics` (`link_id:uuid`) | `bead_id:uuid`, `bead_version_id:uuid`, `topic_id:uuid`, `topic_revision_id:uuid`, `recorded_at:timestamptz` |
| `input.<alias>` (saved `row_ref`) | The exact immutable saved column schema; binding is through the request, never a physical table identifier |

Reserved evaluation capability `evaluation_v1.candidates` has key
`candidate_ref:uuid` and columns `bead_id:uuid`, `bead_version_id:uuid`,
`evidence_ref:uuid?`, `surface:text` = `authored|source`, `score:float8` (finite
diagnostic), `match_ref:uuid` (paged terms/spans/structured-match explanation).
It exists only in an explicitly selected, qualified evaluation profile. The same
schema covers FTS/pg_trgm and the optional in-Postgres vector condition; profile
and ranking/index revisions are receipted. All endpoints are authorized before
ranking and disclosure. Candidate/match IDs and scores are discovery diagnostics,
not evidence or visibility of underlying source contents. No production/default
profile or automatic embedding/provider call is admitted.

`source_precision` is `instant|second|minute|hour|day|month|year|interval|unknown`.
Original timestamps, qualifications, render clause/support/omission mappings and
locators remain losslessly available through inspection. Null renders stay null;
accepted statement text is never replaced with a synthesized title or summary.
`effective_at` is populated only from an actually represented instant; imprecise
or absent dates stay null. Missing actor/trust/occurrence facts stay null.
Source rows require actual support links to accepted observations; unlinked raw
source discovery and new producer/parser families are excluded. Unit links are
not invented statement-to-span citations. Identity/alias/topic rows require all
recorded model and evidence dependencies. Colliding labels return multiple IDs.
A protected latest resolution must not reveal an older visible decision or a
partial candidate set. Legacy unsupported identity facets remain unsupported,
with coverage explicitly reported; no inferred identities or memberships.

`view=historical` exposes accepted predecessors as known at the frame.
`view=resolved` follows recorded corrections, withholds superseded predecessors
from `observations`, and retains every unresolved successor branch; it never
chooses a recency winner. `corrections` and inspection expose the exact lineage.
If a required correction dependency is protected, withhold that observation family
as unavailable rather than present an older version as current. This view says
nothing about tracked-claim currentness. Tracked claims and proposal-ledger relations remain
outside this first cut. Assessed relation projections are an intended dependency-
gated part of the revised interface, not empty placeholder tables. Before exact
freeze, specify their keys, endpoint/basis statement and evidence bindings, pinned
type/direction/qualification, current/as-of lifecycle and coverage, against the
forward contract described in [relation assessment](relation-assessment.md#forward-lifecycle-completion).
Expose them only after that dependency is qualified and released. Neither an
accepted replacement nor the legacy inline governance route supplies assessed
dispute/retraction. No general conflict-completeness, statement-level root or
specialized causal-path claim follows from a basic relation projection.

## 2. SQL admission and independent database authority

Recommend maintained [SQLGlot](https://sqlglot.com/sqlglot.html), pure Python,
MIT-licensed, with explicit PostgreSQL dialect and an exact qualified pin in
Phase B. Use its AST, not its optimizer, execution engine or cross-dialect
transpilation. It is not a complete PostgreSQL validator: reject unknown AST
nodes/warnings, bind types and names independently, emit only resolved PostgreSQL
from the admitted tree, and qualify parser/emitter/native-Postgres agreement on
3.13/3.14. Never execute the original unchecked text or fall back to another parser.
The material alternative is maintained
[libpg_query](https://pganalyze.com/blog/pg-query-2-0-postgres-query-parser), using the actual extracted
PostgreSQL parser but requiring a qualified native Python binding and build
closure. Do not silently adopt pglast's different license or port upstream code.

The retained baseline proposes exactly one SELECT with explicit projections/aliases;
`WHERE`; `AND/OR/NOT`;
typed `= <> < <= > >=`, `IS [NOT] NULL`, `IN` over bound values/subselects;
typed scalar `= ANY($n::type[])` for array membership (only an admitted parameter
array cast, maximum 64 elements, no null elements; an empty array matches nothing);
`LIKE/ILIKE` over text; non-recursive SELECT-only CTEs; correlated `EXISTS` /
`NOT EXISTS`; inner and left equality joins; `GROUP BY`, `HAVING`, `DISTINCT`;
`ORDER BY ... ASC/DESC NULLS FIRST/LAST`; and an explicit nonnegative `LIMIT`.
No cross/natural/right/full joins or unconstrained theta joins. Join edges must
match catalog identity keys or saved-column identity types; self joins are allowed.
Grouping/CTEs may compose, including aggregates of saved group facts, provided
the complete witness derivation is supported. Duplicate projected values retain
bag multiplicity; DISTINCT records collapsed multiplicities. This baseline is
not the complete amended admission matrix: its exclusions also need documented
product/authority/provenance/work rationale before exact approval.

The baseline closed function list is `count(*)`, `count(expr)`, `count(DISTINCT expr)`,
`sum(numeric|int8)`, `min/max` on comparable scalar types; `lower/upper(text)`,
`coalesce` of one type, `nullif` of one type; and
`date_trunc('day'|'month'|'year', timestamptz, 'UTC')`. Numeric `+ - * /`,
searched `CASE`, and checked `int8↔numeric` casts are allowed. COUNT returns int8;
SUM follows PostgreSQL int8/numeric promotion; wire numerics are decimal strings,
never lossy JSON floats. Null/empty aggregate semantics are PostgreSQL's. All
operators/functions/casts resolve to inventoried builtin OIDs and signatures;
text comparison uses the catalog's pinned deterministic collation. No identifier
casts, arbitrary collations, overloaded user functions or dynamic identifiers.

All values use `$1…$64` typed parameters (structural constants above and LIMIT
excepted). Semantic ID parameters are registered vocabulary pins or currently
visible/server-admitted typed anchors; a guessed ID, a UUID string cast or a SQL
literal cannot manufacture visibility. Bound arrays use the typed ANY form;
`IN ($1)` accepts one scalar, never an array expansion. Arrays cannot supply
relation names. Catalog relations are discovery
surfaces, so an unanchored content query may discover new authorized records.
Reject multiple statements, DML/DDL (also inside CTEs), SELECT INTO, row locking,
session commands, side effects, filesystem/network/large-object functions,
volatile functions, SEM_* calls and physical/system schema names. Return the
unsupported construct and safe source position; do not rewrite the question.

The earlier blanket exclusions of windows and set operations are superseded.
Before exact freeze, extend the admission/provenance matrix for useful UNION and
window compositions (including per-entity ranking, preceding observations and
partitioned aggregates). Declare each form's multiplicity, ordering, frame and
contributor semantics. Bounded recursion needs finite termination, cycle handling,
path/provenance and cancellation/work cases consistent with the existing traversal
contract; a tiny successful query is not qualification. Other syntax such as
OFFSET, lateral operations, regex and additional scalar functions is evaluated
against concrete use cases and safe execution, not excluded merely for convenience.
Unqualified forms remain explicitly unsupported until their contracts pass; this
amendment is neither a promise of all PostgreSQL syntax nor permission to execute
unregistered functions. Preserve full composition, not a fixed-template facade.

**Authority implementation:** use separate trusted bookkeeping and restricted
query connections, not a privileged login that merely SET ROLEs downward.
The existing trusted authorization kernel establishes/authenticates context and
writes its audits. A new narrowly scoped bridge issues a one-invocation capability
bound to principal/workspace, run, scope, frame, backend PID/start identity,
transaction and policy revisions. Only the trusted issuer may install it; a
client-settable GUC, guessed backend ID or result handle cannot establish context.
The restricted login is NOSUPERUSER/NOBYPASSRLS, owns no objects, has no role
membership enabling elevation, no TEMP/CREATE/write privileges, and no access to
canonical tables, receipt tables or capability administration. Use a READ ONLY
query transaction; trusted authorization and persistence are separate read/write
transactions. Neither an entire operation nor existing authorization setup is
claimed to be READ ONLY. No model/network wait holds a transaction open.

Grant SELECT only on security-barrier logical views and admitted input views.
Their dedicated owner is not superuser/BYPASSRLS; forced RLS and the read-only
capability check enforce tenant, resource, support and correction dependencies
before joins/groups. Use a forward extension of the existing authorization kernel,
not a second ACL evaluator. View-owner semantics must be tested explicitly:
[RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) and
[view privileges](https://www.postgresql.org/docs/current/sql-createview.html)
are distinct. Revoke unsafe PUBLIC EXECUTE/CREATE/TEMP inheritance in the query
database; grant trusted roles their explicit closure and the reader only admitted
functions/private read checks. Preflight the effective privilege closure, including
extension functions and default grants; incompatible provisioning is unavailable.
AST admission restricts the language; database privileges independently prevent
canonical writes and scope widening even in negative tests bypassing admission.
Catalog-name admission prevents physical introspection; do not claim stock
PostgreSQL hides every builtin system catalog from a compromised database client.
Agents receive neither connection credentials nor a general database client.

Revalidate current permissions/policy under the canonical authority fence before
committing/delivering any result or helper response. A concurrent revocation that
wins that fence prevents disclosure; no old transaction snapshot grants access.
Sanitize DB errors and telemetry; protected values, plans, parameters and counts
never enter generic diagnostics. Replayed access gets a new authorization receipt.

## 3. Wire envelopes and evidence admission

All wire objects are closed, versioned, immutable JSON; UUIDs are opaque typed
refs, hashes are 64 lowercase hex, timestamps UTC RFC3339, bytes base64, int8 and
numeric values decimal strings, float8 values exact hexadecimal strings, and
booleans/null/text native JSON. Reject unknown
fields, nonfinite scores, invalid UTF-8, mismatched types or missing parameters.
The trusted host starts `Run {run_ref, catalog_hash, policy_hash, started_at,
expires_at, default_known_at, remaining}` after authentication; callers cannot
set tenant, principal, workspace, allowances or admission policy.

Every action has `{contract_version:1, run_ref:uuid, step_key:uuid, kind}`:

| `kind` | Required payload (optional fields marked ?) |
| --- | --- |
| `query` | `catalog_hash`, `sql:text`, `parameters:[{position:int,type,value}]`, `inputs:[{alias,result_id}]`, `parents:[result_id]`, `scope:{source_refs:[uuid],known_at:timestamptz,view:resolved\|historical}`, `intent:discover\|enumerate\|refine\|expand\|refresh`, `max_result_bytes:int`, `page_size:int`, `candidate_profile_ref:uuid?`, `candidate_request:{surface:authored\|source\|both,query:text,max_candidates:int}?` |
| `reuse_result` | `result_id:uuid`, `page_size:int`, `cursor:text?` |
| `inspect` | `observation_refs:[{bead_id,bead_version_id}]`, `result_id:uuid?`, `provenance_ref:uuid?`, `facets:[content\|provenance\|lifecycle\|evidence_metadata]`, `view`, `known_at:timestamptz`, `cursor:text?` (at least one target; at most 16 observations; provenance_ref needs its visible result) |
| `hydrate_source` | `selections:[{evidence_ref:uuid,part_id:uuid,lineage_ordinal:int?,representation:raw_bytes\|normalized_text,start:int,end:int}]`, `max_bytes:int`, `cursor:text?` (1–8 selections, half-open intervals; raw needs its recorded lineage ordinal) |

An empty `source_refs` means the run's already admitted workspace scope, never
all tenants. Nonempty source IDs must be admitted anchors. Omitted UI dates must
be resolved to the run's explicit default cutoff before constructing the request.
Input aliases match `[a-z][a-z0-9_]{0,31}` and cannot shadow catalog/CTE names.
No rationale, target facet or interpretation field can affect authorization.
Integers used as limits/positions are nonnegative JSON integers (parameter positions
are 1–64). Parameter types are the scalar SQL types above, typed reference kinds,
or their bounded typed arrays for ANY; contextual type mismatches are rejected. Projected
column names must be unique. Hash/ref fields have their declared type even inside
nested records; an untyped object cannot smuggle a different identifier kind.
Live cutoffs cannot exceed trusted server time; saved-input scope/view/cutoff
must match the input frame. Catalog_hash must name an installed catalog, with
no implicit downgrade or assumption that a package version proves compatibility.
Parents are duplicate-free, at most eight, and include every bound input;
refine requires inputs, expand/refresh require parents, and discovery/enumeration
require neither. Do not impose the former 16-level ancestry cap: bound lineage
inspection and actual work/storage without silently ending a valid investigation
at an arbitrary generation. Exact depth-independent continuation and retention
rules must qualify before the amended interface freezes.
Candidate profile/request must occur together; query text is 1–4,096 UTF-8 bytes,
max_candidates 1–2,000. The evaluation relation is query-local and may participate
in ordinary admitted SELECT joins/CTEs/groups; requesting a profile never silently
changes LIKE or another base SQL predicate into a ranked search. Profiles bind
preregistered tokenizer/FTS/trigram/vector/ranking parameters and their input
artifacts; vector qualification here uses fictional retained development vectors,
with no inference hidden in query execution. Match inspection through its visible
result/provenance ref returns `{match_ref,candidate_ref,surface,terms:[text],
spans:[{evidence_ref,start:int,end:int}],structured_keys:[text],profile_ref:uuid}`.

Every reply has `{contract_version:1, run_ref, step_key, outcome, receipt_ref,
access_receipt_ref, remaining, error?}`. Outcomes are `available | unavailable |
unsupported_query | invalid_request | idempotency_conflict | budget_exhausted |
cancelled | execution_error | settlement_pending`. Non-available replies contain
no rows/facts/protected metadata. A validated safe error is
`{code,position?,feature?}`: syntax/type/binding errors are invalid_request;
missing features/provenance shapes are unsupported_query; missing, denied,
erased or dependency-lost IDs share unavailable. Expiry may be explained only
to a currently authorized result owner; otherwise it is the same unavailable
shape. Unknown commit/cleanup ownership is settlement_pending, never false failure
or a fresh attempt. A timeout/storage/work limit is budget_exhausted.

An available query/reuse reply additionally has:

```text
result {result_id, content_digest, originating_run_ref, created_at, expires_at,
        catalog_hash, policy_hash, result_schema:[{name,pg_type,ref_type?,nullable}],
        frame:{frame_ref,known_at,snapshot_digest,view,source_watermarks},
        parents:[{result_id,content_digest,edge:refine|join|expand|refresh}],
        query_ref, lineage_ref, coverage, order_basis, total_rows}
page {rows:[{row_ref,values,fact_ref?,evidence_refs:[],provenance_ref}],
      next_cursor?,has_more}
visibility {new_refs:[],previous_refs:[],hydration_required:[]}
work {reserved_ms,observed_db_ms?,charged_ms,transport_bytes,retained_bytes,
      cancellation_state,measurement_state:measured|unavailable}
```

`row_ref` is the saved 1-based int8 ordinal; `fact_ref` is
`{result_id:uuid,row_ref:int8}`; query/lineage/provenance/receipt/frame refs are UUIDs.
`evidence_refs` and visibility lists contain `{kind:observation|statement|source|
result_fact,ref:uuid,version_ref:uuid?,content_sha256:text?}`; result facts instead
carry the typed fact_ref, and a source ref must pin its unit hash. The discriminator
fixes the payload, so these kinds are not interchangeable. `values` is an ordered
array matching result_schema. `order_basis` is `[{column:text,direction:asc|desc,
nulls:first|last}]` plus `tie_basis:witness_ordinal`; source_watermarks are
`[{source_ref:uuid,known_through:timestamptz?,coverage:complete|partial|unknown|
unavailable}]`. Result/parent hashes, dates, schema, coverage and query metadata
are all protected. `remaining` contains nonnegative `accesses:int`, `db_ms:int`,
`transport_bytes:int`, `allocation_bytes:int`, `run_expires_at:timestamptz`.
Cancellation state is `none|requested|rollback_confirmed|cleanup_pending|settled`.
Safe error codes are `syntax|binding|type|cursor|feature|provenance|unavailable|
expired|idempotency|time|transport|storage|arithmetic|database|settlement`.

`coverage` has `query_result:complete`, `population_basis:authorized_logical_scope |
retained_subset | limited_query`, `source_capture`, `authored`, `search_ready`,
`discovered` (each `complete|partial|unknown|unavailable`), `known_through?`,
`gaps:[{facet,reason}]` and `truncation:explicit_limit|null`. Counts of inaccessible
records/gaps are never disclosed. A completed zero-row query means no match in
that admitted population/frame/profile. Even complete query execution and every
delivered page do not prove complete source coverage or supported absence.

Inspection returns exact accepted statements (including qualification statements),
render clauses/mappings/omissions, type/vocabulary pins, original clocks and actor/
trust facts, evidence pins and correction predecessors/successors/reasons/branches.
Result inspection returns its schema, query/typed parameters, coverage, protected
population manifest and paged contributor witnesses. All targets must be visible
in this run or explicitly admitted; lineage inspection may admit only authorized
correction neighbors. Unsupported claim/relation facets are refused, not empty.
The intended assessed-relation inspection shape is still a gap in this amended
envelope and must be specified against the forward lifecycle contract before freeze.
Hydration checks both observation and source dependencies and returns exact bytes
or lossless retained normalized text, unit/part hashes, returned-span SHA-256,
original locators, occurrence, attribution and any transformation/version label.
No synthetic coordinates, imports, file/network fetch or source reauthoring.
Whole-source completeness remains independent of completion of the selected spans.
Available helper payloads are `inspection:{observations:[StoredBead],
corrections:[CorrectionRow],source_units:[SourceUnitRow],result:ResultMetadata?,
evidence_pins:[EvidencePin],provenance:ProvenancePage?}` or
`hydration:{slices:[{evidence_pin:EvidencePin,representation,start,end,content:text?,
bytes_base64:text?,returned_sha256:text}],selection_complete:bool}`; both include
visibility, work, next_cursor? and has_more. StoredBead/AcceptedMeaning,
PackageDeclaration, SealedPackagePin and EvidenceInventoryEntry reuse their exact
published types. EvidencePin is `{ref:uuid,bead_id:uuid,bead_version_id:uuid,
statement_id:uuid,unit:UnitSupport,package:SealedPackagePin?,declaration:
PackageDeclaration?,part:EvidenceInventoryEntry?}`; absent packages/parts are
explicitly unsupported for hydration. ResultMetadata is the result record above;
CorrectionRow/SourceUnitRow use the exact logical relation schemas. Unrequested
facets have empty collections/null, not a claim of no corrections/evidence.
ProvenancePage is `{population_ref:uuid,nodes:[{node_ref:uuid,operation:scan|filter|
project|join|group|distinct|exists|limit,input_refs:[uuid],member_refs:[uuid],
multiplicities:[int8]}],members:[{member_ref:uuid,relation:text,key_values:[],
frame_ref:uuid,evidence_refs:[],parent_fact_ref?}],next_cursor?,has_more}`
with typed key_values matching the named logical key and equal member/multiplicity
lengths; member refs resolve to protected pinned logical rows or typed saved facts
through the same inspector. Pages may split member lists without losing node
identity. Unsupported witness operations cannot appear as an empty member list.
Inspection of a requested nonexistent/denied dependency refuses the whole reply.
Hydration offsets are normalized characters or raw bytes as selected; metadata
never changes those units. The new base64 wrapper does not change older hex readers.

Visibility is delivery-bound: a trusted disclosure receipt binds exact response
digest, refs, consuming run and current authority. Only refs in delivered rows,
inspection or hydration become visible. An aggregate makes its scoped fact visible,
not every contributor's full content. A lost response can be redelivered under
the same step key, with a new access receipt and charges. Cross-run reuse requires
equality of the originating authenticated principal, tenant, owner, workspace and
access-scope identity (including on-behalf-of/pairing identity), plus current
reauthorization of the complete dependency
closure, and explicit delivery of selected rows/facts into the new run. Retained
evidence is not automatically current-run evidence; a source snippet is not exact
hydration. PR-06 owns final material-invalidation checking, finish and answers.

## 4. Atomic storage, temporal frames and lineage

An immutable result is committed in one trusted PostgreSQL transaction together
with its typed ordered rows, query/parameters and fingerprints, catalog/profile/
index pins, complete protected dependency/population manifest, contributor
witnesses, parent digests/edges, scope/frame/coverage, canonical content digest,
creation receipt and `(run_ref,step_key)` idempotency binding. Reservations and
execution ownership are durable before dispatch. No identity is acknowledged
before atomic commit; crash injection between any writes must expose neither a
partial result nor a successful receipt. Concurrent identical requests have one
executor/result; different request bytes under the same key conflict. Fingerprint
all semantic inputs including limits, frames, profile, input digests and types.
The version-1 digest is SHA-256 over canonical UTF-8 JSON using `result-json-v1`
(sorted object keys, ordered arrays, no whitespace, explicit nulls and typed scalars)
of immutable schema/rows/query/lineage/frame/coverage metadata; exclude the digest
itself, delivery receipts and changing access/work counters. Catalog and serializer/
provenance revisions must match for saved-input composition. Initial reuse requires
the same catalog hash and a v1 reader; future compatibility mappings require
explicit qualified registry entries. Unchanged catalog hashes can survive package
upgrades. Incompatibility is unsupported_query, never silent refresh/re-execution.

`result-json-v1` uses the existing public canonical JSON encoder with
`ensure_ascii=True`, `sort_keys=True`, separators `(',', ':')`, after these closed
scalar conversions: UUIDs lowercase hyphenated; timestamps UTC
`YYYY-MM-DDTHH:MM:SS.ffffffZ` (six fractional digits); SQL int8/numeric decimal
strings with no plus/exponent/leading zeroes, no fractional trailing zeroes and
zero always `"0"`; finite float8 values use exact IEEE-754 hexadecimal strings
as Python `float.hex()` defines (including the sign of zero); raw bytes standard
padded RFC4648 base64. Boolean/null are JSON primitives; control integers are
ordinary decimal JSON integers. No JSON floats, duplicate keys or unpaired
Unicode surrogates are admitted. Sort keys by Unicode code point; never normalize
source Unicode. Strings escape quote/backslash, use short `\b\f\n\r\t` escapes,
lowercase `\uXXXX` for other controls/DEL/non-ASCII BMP, and lowercase UTF-16
surrogate-pair escapes for non-BMP. Printable ASCII including `/` stays literal;
do not emit alternate literal UTF-8/optional slash escapes. This describes exact
bytes for independent implementations; a serializer-profile change needs a new
contract/catalog revision, not rehashing old results.
Retries/redelivery do not rerun the query; uncertain ownership is recovered first.
Expired/erased bindings never recreate a result under the original ID/key.

Use a short repeatable-read query snapshot, with an explicit recorded-knowledge
cutoff and transaction-visible input manifest. `known_at` excludes later-recorded
facts; it is not source-effective time or proof that all earlier-timestamped
transactions had committed. Frame identity pins the actual visible revisions,
snapshot digest and per-source watermarks, not a timestamp alone. No snapshot is
held through a model turn. This cut preserves late arrivals and makes no promise
to reconstruct the entire past database from its timestamp after vacuum/erasure.
Source-effective windows are `[lower,upper)` UTC; null instants cannot pass an
effective window and cause a safe coverage gap. Original precision/timezone remain.

Live discovery/enumeration has no saved SQL inputs. Refinement/joins of saved
inputs use only those inputs, identical frame/view/catalog and their unchanged
columns; materialized page position never invokes the original SELECT. An explicit
expansion/refresh has parent lineage but queries live logical relations in a new
frame: admitted parent observation IDs may be re-resolved as anchors at that frame,
with changes/removals disclosed. Expansion declares a superset scope; refresh
declares its new cutoff/scope. Neither copies old-frame values into the live
computation. General live-plus-saved or mixed-frame value joins are unsupported.
New children get new IDs/receipts; parents never change. A later grant cannot
silently widen an old result. Temporary results target 30 days from creation;
access alone does not renew retention. A successfully admitted child or saved
checkpoint must retain the dependencies required for its promised lifetime, under
the same accounted quota. Do not cap a fresh child's lifetime at its oldest
parent's expiry or silently mutate that parent's original metadata. Model retention
holds separately from immutable result contents; allocation is refused if its
closure cannot be retained. Saving never resurrects expired/erased content or
resets work counters. After effective expiry, independent live discovery can
create a new result but cannot claim reuse of unavailable original bytes.

Materialize the entire admitted query or return no result. Explicit SQL LIMIT is
part of the query and labelled limited coverage; delivery page limits never alter
SQL. Timeout, allocation overflow or incomplete provenance yields a terminal
failure receipt, not an unfinished aggregate or secretly clipped result. Persist
ORDER BY and internal tie witnesses; absent ordering, seal a deterministic
witness order. Duplicate values have distinct row ordinals. Authenticated cursors
bind result/digest/schema/frame/order/next ordinal and owner scope; current consuming
run authorization is checked on every page. Index changes do not invalidate saved
pages. Bad/tampered cursors are invalid_request; expiry is unavailable.

Each output row/group has a compositional witness DAG: exact input row/revision
pins, intermediate result-fact refs, membership, multiplicity and transformation.
COUNT(*) counts joined tuples; COUNT(DISTINCT bead_id) counts distinct bead IDs,
not independent roots. DISTINCT retains collapsed witnesses. Left nonmatches,
NOT EXISTS, zero groups and HAVING also retain the declared authorized population,
predicate and exclusion/coverage basis; positive contributors alone are insufficient.
Witness construction executes under the same snapshot and work/storage budgets,
not an unreceipted later recomputation. If a shape cannot preserve this derivation,
refuse it. Contributor/provenance inspection is paged and reauthorized.

`fact_ref={result_id,row_ref}` denotes a scoped computation, never a new observation,
claim, source root or sentence citation. PR-06 may use a delivered exact count over
a retained subset with its qualifications without hydrating every member; claims
about specific source contents still require the eligible record and hydration.
Source-root sets are preserved when recorded; unavailable root qualification stays
unknown. No corroboration count is invented from joins, aliases or repeated chunks.

### Investigation checkpoints and explicit resume

This is approved product behavior; its exact public mutation/read envelopes,
atomicity, idempotency, concurrent branch updates and retention-release rules must
be completed before exact-contract approval. It adds derived investigation state
under PostgreSQL authority, not a second memory ledger or task executor.

Persist a checkpoint manifest referencing immutable result IDs/digests and their
frames, the question, explicit investigation progress, concise recorded findings
and predecessor/branch identity. Do not store hidden model reasoning or promise
restoration of model internals. PR-05 owns provider-neutral manifests and result
retention/access; PR-06 interprets agent working state and chooses the next query.
Do not copy the full database or duplicate result bodies at each checkpoint.

Target 20 automatic checkpoints per investigation, plus explicitly named saves.
This is a history-management target, not a limit on queries, refinements or result
generations. Restoring an authorized checkpoint creates a new branch and admits
selected saved evidence into the current run; it does not alter canonical memories,
erase receipts, refund consumed allowance or recover revoked permissions. Restart
preserves unfinished run counters; only an explicitly admitted new run receives
its own policy allowance. Old-frame evidence cannot silently become current.

Named saved investigations persist while saved, subject to current authority,
declared operator quotas and governed erasure, without routine expiry. Retain their
complete required result/provenance closure. Pruning automatic history removes only
unneeded references: it cannot remove content supporting another retained save or
valid child. Explicit deletion, release of a save and subsequent garbage collection
must have defined ownership and replay behavior. Measure actual unique retained
bytes, shared attribution, provenance and indexes before settling quotas. Stable
IDs and manifest pointers alone do not prove inexpensive storage or usable resume.

## 5. Revocation, erasure, retention and work policy

Reauthorize the whole result closure, including parents, authoring context, source/
model dependencies, correction/identity dependencies, contributor/population
manifests, query text, parameters, counts, hashes and lineage. Losing any required
dependency refuses all access and derivation. No row filtering, count decrement,
old identity substitution or stale metadata escapes under the original ID.
Regrant may restore access only if content remains retained under its effective
retention policy (including any admitted save/closure hold), and not erased.
Reduction/salvage and sharing between principals are excluded from v1.

Governed erasure first blocks disclosure at the same authority fence and invalidates
affected descendants; purge result bodies, queries/parameters, manifests, hashes,
indexes and sensitive receipt bodies within a proposed 24-hour cleanup SLA.
Retention expiry blocks access immediately, with the same cleanup SLA. Leave only
a 30-day private tombstone/audit containing opaque run/step/result identifiers,
terminal state, timestamps, noncontent work charges and idempotency disposition;
no source IDs, content/query digests, counts or contributor names. It is not an
agent-readable evidence store. Deletion never turns a saved aggregate into an
independent fact. Backup/WAL physical erasure follows governed storage lifecycle;
this interface promises immediate logical withdrawal, not instantaneous deletion
of every physical replica. Failed cleanup remains owned and explicitly pending.

The following values are **unapproved qualification candidates**, not product
capacity guarantees or a settled single allowance. The amended workload set must
justify them or replace them explicitly before exact policy approval. Larger
allowances require operator admission; no automatic escalation or hidden reset.
Separate query materialization/storage from preview/page delivery. Values are
binary byte units:

| Counter / fence | Proposed default |
| --- | --- |
| Run lifetime / operations | 30 minutes, 64 admitted accesses (including pages, imports, helpers, retries); counters/deadlines survive restart |
| Operation / cumulative DB work | 15 seconds per whole operation, 120 seconds per run, including authorization, lineage, persistence and cleanup; every constituent statement uses the remaining operation deadline; 500 ms lock timeout |
| Workspace admission | Two active runs, one executing DB operation; rolling 24-hour 1,200-second DB allowance and 128 MiB transport allowance; new runs cannot bypass these persisted counters |
| SQL structure | 16 KiB SQL, 64 parameters / 64 KiB parameter bytes, 8 saved inputs, 8 relation references, 4 CTEs, 8 join/group nodes, expression depth 32 |
| Delivery | Default 20 / maximum 50 rows per page, 256 KiB whole response; helpers 16 observations / 8 source selections; hydration 64 KiB per response, at most 16 KiB per selected span |
| Transport | 8 MiB per run, including helper bytes, metadata, cursors and redelivery; reserve 16 KiB for terminal diagnostics |
| Retention admission | Candidate 16 MiB per result and 64 MiB new allocation/run; former 128 MiB/256-result workspace caps require requalification for 30-day results, 20 checkpoints and durable saves; account shared dependency holds and provenance, reserve before execution and settle actual allocation |
| Query settings | `work_mem=4MiB`, `hash_mem_multiplier=1`, `temp_file_limit=32MiB`, parallel query disabled, JIT off; restricted login cannot change them |

**Work unit is reserved database duration plus measured storage/transport**, not
examined rows. Atomically reserve the operation's maximum remaining duration and
storage/delivery capacity before starting. Debit confirmed observed duration;
refund unused reservation only after owned settlement. Unknown timing retains the
full reservation. Duration includes cancelled/failed/provenance work, repeated
access and bookkeeping, with no free child work. Charge observed overrun and stop
new admission while cleanup/settlement is uncertain. SQL LIMIT, EXPLAIN cost and
returned-row count cannot fence scans, fanout, sorts or aggregates. Qualify timeouts
with a supervisor that cancels, confirms rollback or terminates its owned backend,
and drains trusted writes; foreground cancellation is not settlement. The deadline
is an execution/cancellation fence, not a hard real-time kill guarantee.

PostgreSQL's [statement timeout](https://www.postgresql.org/docs/current/runtime-config-client.html)
and [temporary-file bound](https://www.postgresql.org/docs/current/runtime-config-resource.html)
support these controls. `work_mem` is per executor operation, not a total RSS cap;
elapsed time is not CPU, IO or examined-row measurement. No precise visit, hard
total-memory or IO ceiling is promised. Hard process/instance resource containment
requires a separately qualified host policy before a stronger resource claim.
Diagnostic EXPLAIN ANALYZE/BUFFERS runs on fictional data consume work too; they
are measurements, not authorization or an enforcement substitute. Preflight actual
server/parser/role compatibility and fail closed if fences are unavailable.

Store actual row/provenance allocations (including index/row overhead) and shared
storage attribution; no zero-cost descendants or accounting double counts.
Reservations block excess allocation; do not evict still-valid results or silently
extend expiry on page access. Explicit retention holds are separately admitted and
accounted; revocation/erasure override them. Provenance overflow refuses an
otherwise small aggregate. A response too large for direct display uses a labelled
bounded preview/page while the complete committed result remains addressable;
preview clipping is never stored-result truncation or a coverage claim. Response
overflow never emits half a row; a single oversized field needs a bounded read or
explicit limitation. No query result or cache becomes a second canonical authority.

## 6. Acceptance, exclusions and next handoff

These fictional workloads are capacity/behavior tests, not new unseen-holdout
cases. Their parameters must freeze with the approved policy before Phase B:

| Workload | Required acceptance / honest failure |
| --- | --- |
| W1: 100K observations; ≤2K candidates, alias collisions and permission skew | Useful independently authored filters/joins/CTEs and saved-input refinement; missing tenant predicates cannot widen scope; revoked/latest-hidden identities do not leak |
| W2: million-row corpus; 2K authorized output rows at roughly 2 KiB/row | Complete immutable materialization, 40 pages of 50, restart and follow-up **including** metadata. Test the candidate 8 MiB/64-access envelope and revise it explicitly if it cannot support this product workload; honest budget_exhausted proves the fence, not successful enumeration or capacity qualification |
| W3: 36 monthly groups over up to 100K eligible records, skew/fanout/corrections/late arrivals | Counts and distinct counts preserve multiplicity and paged contributor/population witnesses; prove useful cold and reused plans. A candidate 15-second or 16-MiB provenance miss must fail honestly, but does not qualify the representative workload; measure and resolve the mismatch before claiming delivery |
| W4: four 256-KiB retained source units | Sixteen 64-KiB calls using multiple ≤16-KiB exact spans transfer 1 MiB plus metadata; hashes/locators/attribution survive, gaps remain explicit; no new source family |
| W5: restart, cross-session reuse, >20 refinements/checkpoints, saved branch, dependency revocation and expiry/erasure | Same parent bytes/ID, explicit restore branches, no discovery rerun on import/page, no budget reset or ancestry cliff; test both sides of 30-day temporary expiry, durable saves and retention of their closure without duplication or stale access |
| W6: relation lifecycle and composition | Released assessed confirmation/dispute/retraction, current/as-of state, endpoint/basis evidence and applicable roots; withdrawal cannot remain traversable in a fresh evaluation, while historical saved results remain historical rather than current support |
| W7: SQL breadth and presentation | Useful set/window compositions and separately admitted bounded traversal with contributor/coverage semantics; a tiny preview over a larger saved result must support later complete paging/counting without new discovery |

Additional acceptance: tenant/resource/mixed-authority isolation through joins,
outer nonmatches and aggregates; guessed anchors and SQL/function/catalog/CTE
bypasses rejected; direct restricted-role tests with admission bypassed; parser/
Postgres semantic agreement; cancellation/repeated cancellation/settlement and
backend loss; failure injection before atomic commit; lost response and concurrent
idempotency; stable duplicate paging; new grants without parent mutation; zero
rows versus coverage unknown; dependency revocation during commit/delivery; and
no canonical capture/authorship/task/usage side effects. Immutable facts are
compared before and after restart; only named incidental identities/times may
normalize in fictional replay. No test is a semantic-quality claim.

Preserve EG-0001's **FTS/pg_trgm** and **optional in-Postgres vector candidate**
conditions as separately named evaluation-only peers, with identical logical SQL,
reuse, authority, hydration and policy. `candidate_profile_ref` has no default;
if unavailable, return unsupported_query, never empty/fallback. The reserved
evaluation catalog above and profile/ranking parameters require custodian
preregistration against the approved contract before implementation; no
candidate-condition code in Phase A. Any schema amendment returns to the exact
contract approval/freeze gates rather than silently modifying the frozen interface.
Preserve the existing signed quality floors/material-lift thresholds unchanged;
this packet sets operational budgets, not new evaluation thresholds. Selection
uses sealed returned-evidence/status/coverage bundles. Generated-answer diagnostics
cannot rank/pass a retrieval condition; a forbidden-answer veto remains a separate
integrated-product safety gate. Source/indexing cost and amortized reuse cost are
reported separately. W1–W7 do not waive SQL-10's later 10M-row production gate.

After both entry attestations, implement the smallest coherent public slice with
fictional development fixtures. Run focused checks, one complete relevant
convergence lane, installed wheel/sdist qualification on Python 3.13 and 3.14,
and final-head CI. Each non-draft subscribed PR has one primary acceptance claim,
one broad review and at most one focused rereview per substantive slice; fix,
answer and resolve findings, leaving PRs unmerged. A documentation PR claims only
a reviewable specification, not runtime/quality acceptance.

Published IDs/payloads, migration bytes 0001–0029 and canonical capture/authorship
behavior remain unchanged. Add new versions and forward migrations only as
required, following [compatibility](architecture/compatibility.md) and
[release gates](releasing.md). No runtime, generated catalog, parser dependency,
candidate implementation or new migration is authorized in this Phase A packet.
No Gridex dependency/adaptation, model/provider calls, spending, owner data,
private core workaround, release/tag/publication, deployment or checkpoint.
Any later approved adaptation needs exact source revision/files, license/NOTICE,
modification attribution and boundary tests. Unsupported claims stay unavailable;
the intended relation-aware delivery requires its named lifecycle dependency, not
an indefinite exclusion. PR-06 owns model investigation, checkpoint working-state
interpretation, finish and answers.

Before this amended packet is ready for exact approval, finish the relation
projection and lifecycle dependency reference; the checkpoint/save/restore/branch
wire and retention-hold contracts; the broadened SQL/provenance matrix; and justified
work/storage policy candidates. Do not treat the prior packet's review or digest
as approval of these new contracts. No unseen holdout is frozen against this
incomplete revision, and no PR-05 runtime gate is opened by the documentation edit.

Next dependency handoff: owner approval of the exact packet commit/digest →
independent custodian freeze attestation → public implementation/qualification →
separately authorized public release → released-version Desktop consumption.
