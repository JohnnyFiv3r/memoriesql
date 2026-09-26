# PR-05: agent-authored SELECT and reusable results — decision packet 3

Status: **completed interface proposal for exact approval; Phase A**, 2026-09-26.
Product direction is approved; this proposed wire contract and policy candidates
await exact approval and runtime qualification. This is the public proposal
authority, not an installed capability, executable contract or frozen interface.
Public main was reverified as `e8cfa0df3c1f8109c199a8126c0556624bc421b6`
(PR #45 accepted `2a5b9a6b3b1bcc3a3cbff21186ee95d85fea80a2`);
the merged Desktop planning amendment as `84f93ec272cc1e868efffb0765b8366bdc5bf987`.
This independently authored specification reconciles the owner amendment to
ADR-0012, SQL-02 §5.0 revision 3, SQL-10, EG-0001 and the PR-05 execution plan.
The corresponding product-doc amendment is merged. This packet contains no private
corpus, questions, gold, evidence artifacts or upstream source adaptation.

## Decision requested

Approve this whole packet and its pinned relation dependency, including the
explicit disclosure contexts and policy candidates in §5. The approved direction
remains genuine composable SELECT, immutable reusable results, relation-ready
recall and branching investigations. `11692c3`, its hash and its 48-hour policy
are superseded. Only consequential policy alternatives appear below; parser pin,
indexes and physical layout are engineering qualification work.

| Consequential choice | Recommended default | Material alternative / cost |
| --- | --- | --- |
| SQL and provenance | Closed composable matrix in §2, including sets/windows and bounded recursion | Unqualified shapes return unsupported; do not substitute templates or broaden functions |
| Changed authority | Refuse the entire affected result and its descendants, including metadata and aggregates | An explicitly new reduced-authority child needs a separately qualified exclusion contract; omit initially |
| Time | Same-frame saved-input refinement; fresh, labelled live children for expansion/refresh | Mixed-frame comparison needs a separate typed interpretation; omit initially |
| Investigation history | 20 retained automatic checkpoints per investigation, 30-day automatic lifetime; named saves persist while saved; restore creates a branch | Longer automatic history increases holds/storage; it is not a query/result-generation limit |
| Retention | Target 30 days for temporary results; saved investigations persist without routine expiry while saved, subject to disclosed quotas and erasure | Retain required dependency closure; no earliest-parent expiry trap, silent eviction or arbitrary ancestry-depth cap |
| Erasure/audit | Immediate logical withdrawal; owned primary cleanup within 24 hours; noncontent private tombstones for 30 days | A different cleanup/audit period changes operational/privacy commitments and needs explicit approval; backup/WAL lifecycle is separately declared |
| Work/storage | One disclosed baseline candidate in §5, justified by W1–W7 sizing; runtime fit measurements pending | Operator-admitted larger policy requires explicit new run/storage admission; no silent escalation or unsupported resource promise |
| Relation readiness | Complete and release the bounded assessed-assertion lifecycle dependency and expose qualified relation projections | Observation-only mechanics can progress independently but do not satisfy the intended relation-aware delivery; unrelated claims capabilities need not block it |

The owner authorized the five-step delivery sequence: reconcile this packet and
product roadmap; scope assessed-relation lifecycle completion; review and explicitly
approve the amended exact interface and remaining policy; independently freeze the
contract-bound unseen holdout; then implement, qualify and release publicly before
private consumption. This does not waive any gate, choose a version, approve a
protected upload, or authorize provider spending, owner-data access or deployment.

**Implementation gates:** an explicit owner decision must identify the complete
approval record below: packet commit/digest, pinned normative dependency blobs
and any recorded amendments. The packet digest alone is not the interface identity.
Separately, the independent PR-04/evaluation custodian must attest that a loadable
source-attributed corpus is ready and an unseen holdout was frozen **against that
approved record before any PR-05 runtime or candidate-condition implementation**.
Its attestation names the sealed holdout manifest digest, both conditions and
unchanged threshold/profile manifests, without exposing questions or gold.
The implementer neither creates nor reads that holdout. A prose merge, architecture
approval, or green CI satisfies neither gate. Both gates are presently **pending**.

Approval digest procedure v2: `packet_sha256` is lowercase SHA-256 of the exact
Git blob bytes at `<packet_commit>:docs/agent-sql-results-v1.md`, including its
final newline; no Markdown rendering, Unicode normalization or whitespace cleanup.
In Bash/Zsh, enable `set -o pipefail`, then reproduce with
`git show <packet_commit>:docs/agent-sql-results-v1.md | shasum -a 256`.
Record a digest only on zero pipeline exit; missing/mistyped commit or blob fails
the gate even if the last program printed an empty-input hash.
Every external document that defines interface semantics is also a required
normative dependency. In this revision that list is exactly
`docs/relation-assessment.md` (the **whole blob**, not a mutable heading/URL).
Resolve it at the same `packet_commit` in this public repository and hash its exact
Git blob bytes by the same procedure, including the final newline:
`git show <packet_commit>:docs/relation-assessment.md | shasum -a 256`, with
`pipefail` enabled and zero pipeline exit required. Never substitute a checkout,
branch tip, latest release or live hyperlink for an approved blob. Informational
research, compatibility/release-process links and private roadmap context cannot
silently add interface rules. Any further normative dependency must be listed
here and pinned before approval, including transitive interface dependencies.

Define `DependencyPin={path:text,commit:text(40 lowercase hex),sha256:text(64
lowercase hex)}` and `InterfacePin={packet_commit:text(40 lowercase hex),
packet_path:"docs/agent-sql-results-v1.md",algorithm:"sha256",
packet_sha256:text(64 lowercase hex),dependencies:[DependencyPin]}`. Paths are
unique repository-relative file paths, sorted lexicographically, without `..`,
absolute paths or URL fragments; every dependency commit equals `packet_commit`.
The closed approval record is `{version:2,interface:InterfacePin,approval_ref:text,
amendments:[{amendment_ref:text,supersedes:InterfacePin}]}`. Initial amendments is
empty. The custodian repeats the **entire** exact record and its separate sealed
evaluation manifest digests; it verifies every blob and required dependency entry.
Missing, duplicate, extra, mismatched or unpinned normative entries fail the gate.
Version 1 records are insufficient for this amended interface.

An amendment to either the packet or a normative dependency requires a newly
committed interface and explicit owner approval of its complete new record, then
a matching custodian freeze. `supersedes` identifies the preceding whole pin, not
just its packet hash: a lifecycle-only change can leave the packet hash unchanged.
Later edits elsewhere do not mutate the already approved pinned interface; using
those edits requires the new approval/freeze. No external prose exception or
inferred equivalence can change the effective contract.

Approval-verification acceptance must reject: a correct packet hash paired with
an altered lifecycle blob; a missing or wrong-commit dependency pin; missing blob
lookup despite an empty-input hash being printed; and a custodian record that
matches the packet but omits or changes dependency pins. Exact unchanged pins
must verify even if an unrelated branch tip later changes.

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
outside this first cut. The following assessed projections consume the single
PR-03 projector defined in the pinned whole
[relation assessment](relation-assessment.md#forward-lifecycle-completion) blob.
They are unavailable until the forward lifecycle dependency is qualified and
released; 0.0.12 rows or an empty placeholder cannot satisfy that capability.

| Relation (key) | Exact columns beyond the key |
| --- | --- |
| `assessed_relations` (`relation_id:uuid`) | `task_id:uuid`, `type_key:text`, `type_revision:int8`, `source_bead_id:uuid`, `source_bead_version_id:uuid`, `target_bead_id:uuid`, `target_bead_version_id:uuid`, `basis:text` = `source_stated\|agent_inferred`, `rationale:text`, `qualification:text?`, `author_confidence:numeric`, `author_run_ref:text`, `specialist_run_ref:text`, `acceptance_receipt_id:uuid`, `recorded_at:timestamptz`, `state:text` = `active\|disputed\|reassessment_pending\|retracted\|superseded`, `head_token:text`, `support_eligible:bool`, `support_reason:text?` = `disputed\|corrected\|retracted\|superseded`, `correction_pending:bool`, `roots_status:text` = `qualified\|indeterminate\|unsupported`, `independent_root_count:int8?` |
| `relation_statements` (`relation_id:uuid`, `role:text`, `statement_id:uuid`) | `role` = `source\|target\|basis`, `bead_id:uuid`, `bead_version_id:uuid`, `text:text`; exact immutable endpoint/basis text, never successor substitution |
| `relation_evidence` (`relation_id:uuid`, `statement_id:uuid`, `source_unit_id:uuid`) | `content_sha256:text`, `evidence_ref:uuid`, `roots_status:text` as above |
| `relation_events` (`event_id:uuid`) | `relation_id:uuid`, `action:text` = `confirm\|dispute\|retract\|retire`, `related_relation_id:uuid?`, `reason:text`, `origin:text` = `authored\|governed`, `authoring_bead_id:uuid?`, `effective_at:timestamptz?`, `recorded_at:timestamptz`, `event_number:int8?`, `previous_event_id:uuid?`, `recorded_by_principal_id:uuid`, `recorded_by_user_id:uuid?`, `idempotency_receipt_id:uuid` |
| `relation_event_evidence` (`event_id:uuid`, `statement_id:uuid`, `source_unit_id:uuid`) | `bead_id:uuid`, `bead_version_id:uuid`, `statement_text:text`, `content_sha256:text`, `evidence_ref:uuid`; human event evidence does not become assertion support |
| `relation_types` (`type_key:text`, `type_revision:int8`) | `namespace:text` = `memoriesql\|workspace`, `label:text`, `definition:text`, `endpoint_rule:text`, `forward_reading:text`, `inverse_reading:text`, `symmetric:bool`, `evidence_expectation:text?`, `example:text?`, `counterexample:text?`, `cycle_policy:text` = `permitted\|forbidden` |
| `relation_pairs` (`task_id:uuid`, `first_bead_id:uuid`, `second_bead_id:uuid`) | `first_bead_version_id:uuid`, `second_bead_version_id:uuid`, `disposition:text` = `related\|not_related\|abstained\|not_assessed`, `abstention:text?` = `no_fit\|insufficient_evidence\|ambiguous`, `reason:text?` |
| `relation_corrections` (`relation_id:uuid`, `role:text`, `correcting_bead_id:uuid`) | `role` = `source\|target\|basis`, `correcting_bead_version_id:uuid` (resolved at this frame, not falsely a lifecycle-event pin) |
| `relation_replacements` (`relation_id:uuid`, `replacement_relation_id:uuid`) | No additional columns; exact recorded replacement, never revival after its withdrawal |

Only accepted assessed assertions enter this SQL catalog; unaccepted proposals and
specialist judgments remain inspectable through the exact PR-03 v3 Assertion /
Judgment types. The SQL orientation is always stored source → target, never the
bead-relative incoming/outgoing/basis inspection direction. Type definitions are
the pinned revisions, including later-deactivated vocabulary. Relative direction,
full judgments and qualified evidence-root IDs/gaps are returned by `inspect` as
`relations:[Assertion],relation_types:[TypeDefinition],relation_pairs:[PairDisposition]`
using that dependency's closed types; no lifecycle writes are exposed here.
Root/gap arrays are paged witness members, not inferred corroboration. A count is
nonnull only for qualified roots; no partial independent count is a complete union.
Pinned relation/event text and evidence remain inspectable at their exact accepted
versions even when resolved observations withhold a corrected predecessor. Those
dedicated columns do not depend on a successful join to the resolved observation
population; a caller must not substitute successor text or lose the historical pin.

Current/as-of scans use the shared recording cutoff plus actual snapshot manifest;
effective time is attributed metadata, never operative scheduling. Confirmation
cannot rebind corrected pins; dispute is inspectable uncertainty, and terminal
withdrawal never revives a predecessor. Only support_eligible edges enter settled
path support. Conservative root gaps and distinct cycle reservations follow
PR-03 choices L1–L5. Saved frames retain original state/roots after later semantic
changes under current authority; they cannot claim current support without fresh
validation. Pair dispositions cover supplied bead pairs, not all statement pairs
or all competing assertions. No general conflict completeness, statement-level
roots or specialized causal-path guarantee follows from these projections.
Every row binds endpoint/basis, event evidence/actor attribution, type, judgment,
correction/replacement and applicable root/gap authority. Unreadable families
cannot enter a discovery population; no protected counts are disclosed. A targeted
read or reuse whose required closure loses authority refuses wholly, rather than
showing an older state or partial root count.

## 2. SQL admission and independent database authority

Propose maintained [SQLGlot](https://sqlglot.com/sqlglot.html), pure Python,
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
The engineering rationale is its maintained PostgreSQL dialect, inspectable AST
and no mandatory native build for supported Python. This is a proposal, not a
selected dependency: an exact version, parser/emitter differential corpus, license
closure and installed 3.13/3.14 results are **pending**. Minor SQLGlot releases can
break compatibility; never float its pin. Failure to qualify requires an explicit
engineering revision and equivalent admission evidence, not unchecked execution.

The proposed contract admits exactly one SELECT with explicit projections/aliases;
`WHERE`; `AND/OR/NOT`;
typed `= <> < <= > >=`, `IS [NOT] NULL`, `IN` over bound values/subselects;
typed scalar `= ANY($n::type[])` for array membership (only an admitted parameter
array cast, maximum 64 elements, no null elements; an empty array matches nothing);
`LIKE/ILIKE` over text; non-recursive SELECT-only CTEs; correlated `EXISTS` /
`NOT EXISTS`; inner and left equality joins; `GROUP BY`, `HAVING`, `DISTINCT`;
`ORDER BY ... ASC/DESC NULLS FIRST/LAST`; and an explicit nonnegative `LIMIT`.
No cross/natural joins or unconstrained theta joins. Inner/left/right/full equality
joins use catalog identity keys or compatible saved-column identity types;
self joins are allowed. Right/full nonmatches require both population witnesses.
Grouping/CTEs may compose, including aggregates of saved group facts, provided
the complete witness derivation is supported. Duplicate projected values retain
bag multiplicity; DISTINCT records collapsed multiplicities. This baseline is not
qualified at runtime merely by being listed here. Each shape below has mandatory
acceptance obligations; admission is unavailable until they pass.

The baseline closed function list is `count(*)`, `count(expr)`, `count(DISTINCT expr)`,
`sum/avg(numeric|int8)`, `min/max` on comparable scalar types; `lower/upper(text)`,
`coalesce` of one type, `nullif` of one type; and
`date_trunc('day'|'month'|'year', timestamptz, 'UTC')`. Numeric `+ - * /`,
searched `CASE`, and checked `int8↔numeric` casts are allowed. COUNT returns int8;
SUM/AVG follow PostgreSQL numeric promotion; wire numerics are decimal strings,
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

### Composition and provenance qualification matrix

All rows use one frame/catalog, preserve the whole required authority closure and
pay the same execution/provenance/storage budget. These are composable grammar
rules, not canned query texts. No runtime qualification is claimed in Phase A.

| Admitted shape | Saved derivation / mandatory qualification |
| --- | --- |
| Projection, predicates, scalar subqueries, SELECT-only CTEs | Exact row witnesses, expression tree, null/three-valued logic and scalar cardinality errors; CTE reuse cannot drop dependencies |
| Equality inner/left/right/full joins | Bag tuple witnesses and multiplicities; nonmatches retain searched population/predicate, including both sides for full joins; qualify skew/fanout and protected nonmatches |
| EXISTS/NOT EXISTS, IN/subqueries | Positive witnesses plus searched population and null/exclusion basis; protected excluded records cannot leak absence |
| GROUP/HAVING, admitted aggregates including FILTER(WHERE...), DISTINCT | Group members/multiplicity and full tested population, including rejected groups and empty aggregate; count tuples, distinct identities and roots separately |
| UNION ALL / UNION | Branch identity and bag contributions / collapsed duplicates with all witnesses; column counts, SQL types and semantic ref kinds must match, no implicit ref-to-text erasure |
| INTERSECT [ALL] / EXCEPT [ALL] | Both branch populations, equality classes and multiplicities; ALL uses min(left,right) / max(left-right,0); even a right branch with no returned contributor remains required authority |
| row_number, rank, dense_rank | Partition/order/peer witnesses; row_number needs a total visible key order; ties remain ties for rank/dense_rank, never changed by a hidden tie breaker |
| lag/lead(expr, offset, default?) | Offset is structural integer 0–64, default same scalar type; total visible key order, partition and chosen-neighbor/nonexistence witnesses; respect nulls |
| Window COUNT/SUM/AVG/MIN/MAX | Explicit ROWS frame with unbounded preceding/following, current row or structural 0–4096 row offsets; frame members and partition/order witnesses, shared DAG storage; omission of frame is invalid_request |
| ORDER BY/LIMIT/OFFSET | Stable sealed tie order and selected/excluded population; OFFSET 0–10000 is an explicit query subset, not a paging mechanism; whole computation is charged |
| One bounded recursive CTE | Rules below, path/iteration witnesses, cycle and depth-bound coverage; complete finite result or failure, never silently clipped paths |

Set equality uses PostgreSQL null/duplicate semantics and pinned collation;
order is guaranteed only by the outer ORDER BY and sealed tie order.
[PostgreSQL set semantics](https://www.postgresql.org/docs/current/queries-union.html).
Window facts depend on their partition, ordering and frame, not just the current
row. Require explicit ROWS frames for aggregate windows; RANGE/GROUPS, frame
exclusions and IGNORE NULLS remain unsupported because their peer/frame lineage
has no qualified v1 mapping. Ranking/lag/lead use partition/order semantics, not
an aggregate frame. [PostgreSQL windows](https://www.postgresql.org/docs/current/functions-window.html).

Bounded recursion admits `WITH RECURSIVE` with one SELECT seed, UNION ALL, one
self-reference and key-equality expansion over qualified relation endpoints or
correction edges. A request declares `recursion:{cte:text,depth_column:text,
node_column:text,max_depth:int}` (max_depth 1–8). The seed must bind at most 16
visible typed anchors and initialize depth to zero; the recursive arm increments
it by exactly one and conjunctively tests depth < max_depth. Admission verifies
this structural invariant and emits an independent guard; neither a user LIMIT
nor UNION deduplication proves termination. Native `CYCLE <node_column> SET
<cycle_flag> USING <path_column>` is required; cycling rows are recorded for
diagnostics and excluded from further expansion. The generated path is internal,
not a general array SQL surface. Final rows may select the depth and cycle flag;
path IDs/multiplicity are inspected through provenance. Expansion edges must be
support-eligible at this frame under PR-03's projector; disputed/withdrawn/gapped
assertions can be inspected in ordinary SELECT but cannot become path support.
Recursive v1 additionally requires qualified roots: this conservative path
admission is stricter than canonical support_eligible, which can be true while
roots_status is indeterminate. Ordinary SQL/inspection preserve both values.
At most eight levels is an explicit bounded-path question, not a promise to find
all reachable nodes. Coverage records `explicit_depth` even if the frontier ends
early. Qualify cycles, diamonds, repeated endpoints, high degree, all depth values,
erasure, cancellation and complete path witnesses. Depth bounds termination, not
fanout/work; provenance overflow or timeout fails the whole result.
[PostgreSQL recursive execution](https://www.postgresql.org/docs/current/queries-with.html).

Unsupported: mutual/general recursion, cross/theta/natural joins (no qualified
population/fanout mapping), LATERAL/set-returning functions (no v1 cardinality
mapping), regex (no qualified cost/admission profile), arbitrary arrays/JSON,
user functions/casts/collations and additional window/scalar functions (no closed
signature/witness mapping). No semantic traversal, causal inference, path ranking
or general conflict-completeness is inferred from relational composition. Add a
feature by a new qualified catalog/contract revision, not silent parser acceptance.

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

Every action has `{contract_version:1, run_ref:uuid, step_key:uuid, kind}`.
`ResultPin={result_id:uuid,content_digest:hash}`. Result-bearing requests use
`AccessContext={kind:standalone}` or
`{kind:checkpoint,checkpoint_id:uuid,manifest_digest:hash,root:ResultPin,
basis:RetentionBasis}`. `RetentionBasis={kind:automatic}` or
`{kind:named_save,save_id:uuid,revision:int8}` pins one specific active hold.
The latter must select that exact root in a currently retained checkpoint, under
the same authenticated principal/tenant/owner/workspace/access-scope identity.
It is an explicit access basis, not a bearer capability. For multiple inputs each
binding carries its own context; a checkpoint containing C cannot name ancestor P
as its root. No implicit context selection, inheritance or fallback is allowed.

| `kind` | Required payload (optional fields marked ?) |
| --- | --- |
| `query` | `catalog_hash`, `sql:text`, `parameters:[{position:int,type,value}]`, `inputs:[{alias,result:ResultPin,access:AccessContext}]`, `parents:[{result:ResultPin,access:AccessContext}]`, `scope:{source_refs:[uuid],known_at:timestamptz,view:resolved\|historical}`, `intent:discover\|enumerate\|refine\|expand\|refresh`, `max_result_bytes:int`, `page_size:int`, `recursion:{cte,depth_column,node_column,max_depth}?`, `candidate_profile_ref:uuid?`, `candidate_request:{surface:authored\|source\|both,query:text,max_candidates:int}?` |
| `reuse_result` | `result:ResultPin`, `access:AccessContext`, `page_size:int`, `cursor:text?` |
| `inspect` | `observation_refs:[{bead_id,bead_version_id}]`, `relation_refs:[uuid]`, `result:{pin:ResultPin,access:AccessContext}?`, `provenance_ref:uuid?`, `facets:[content\|provenance\|lifecycle\|evidence_metadata]`, `view`, `known_at:timestamptz`, `cursor:text?` (at least one target; at most 16 observation/relation targets; provenance_ref needs its visible result) |
| `hydrate_source` | `selections:[{evidence_ref:uuid,part_id:uuid,lineage_ordinal:int?,representation:raw_bytes\|normalized_text,start:int,end:int,origin:{result:ResultPin,access:AccessContext}?}]`, `max_bytes:int`, `cursor:text?` (1–8 selections, half-open intervals; raw needs its recorded lineage ordinal; saved-result evidence carries origin) |

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
at an arbitrary generation. Depth-independent continuation and retention rules
are defined below and require runtime qualification before delivery.
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
        frame:{frame_ref,known_at,snapshot_at,snapshot_digest,view,source_watermarks,
               lifecycle_projection_version:int?,projection_manifest_sha256:hash?},
        parents:[{result_id,content_digest,edge:refine|join|expand|refresh}],
        query_ref, lineage_ref, coverage, order_basis, total_rows}
page {rows:[{row_ref,values,fact_ref?,evidence_refs:[],provenance_ref}],
      next_cursor?,has_more}
visibility {new_refs:[],previous_refs:[],hydration_required:[]}
work {reserved_ms,observed_db_ms?,charged_ms,transport_bytes,retained_bytes,
      allocation_profile_hash,measured_physical_bytes?,
      cancellation_state,measurement_state:measured|unavailable}
```

`result.expires_at` is the immutable deadline for **standalone temporary access**
to this result, not a physical-deletion deadline or the lifetime of an admitted
checkpoint access context. It never changes on child creation, save or page read.
Each successful result/helper disclosure includes `access_bases:[{context:AccessContext,
checked_at:timestamptz,valid_until:timestamptz?,end_condition:standalone_expiry|
checkpoint_expiry_or_prune|save_release,hold_ref:uuid?}]`. Null valid_until is only
for the specified active named-save basis; it promises no bypass of authority,
erasure or storage admission. Context is part of request/response fingerprints,
cursors and disclosure receipts, including selected root digest. Save/release
creates/ends a basis without changing the immutable result or manifest.
Every page rechecks the context; switching contexts requires a new step and cursor.
Dependency-only holds have no AccessContext and cannot authorize independent
paging, inspection or SQL input admission of an ancestor.

`row_ref` is the saved 1-based int8 ordinal; `fact_ref` is
`{result_id:uuid,row_ref:int8}`; query/lineage/provenance/receipt/frame refs are UUIDs.
`evidence_refs`, findings and visibility lists contain the closed `EvidenceRef`
union: `{kind:observation,ref:uuid,version_ref:uuid}` (bead/version),
`{kind:statement,ref:uuid,version_ref:uuid}` (statement/bead version),
`{kind:source,ref:uuid,unit_ref:uuid,content_sha256:hash}` (evidence pin/unit), or
`{kind:result_fact,fact:fact_ref}`. Kinds are not interchangeable. `values` is an ordered
array matching result_schema. `order_basis` is `[{column:text,direction:asc|desc,
nulls:first|last}]` plus `tie_basis:witness_ordinal`; source_watermarks are
`[{source_ref:uuid,known_through:timestamptz?,coverage:complete|partial|unknown|
unavailable}]`. Result/parent hashes, dates, schema, coverage and query metadata
are all protected. `remaining` contains nonnegative `accesses:int`, `db_ms:int`,
`transport_bytes:int`, `allocation_bytes:int`, `run_expires_at:timestamptz`.
Cancellation state is `none|requested|rollback_confirmed|cleanup_pending|settled`.
Safe error codes are `syntax|binding|type|cursor|feature|provenance|unavailable|
expired|idempotency|branch_conflict|save_conflict|time|transport|storage|arithmetic|
database|settlement`. Authorized head/save conflicts use outcome invalid_request;
no competing protected value is returned.

`coverage` has `query_result:complete`, `population_basis:authorized_logical_scope |
retained_subset | limited_query`, `source_capture`, `authored`, `search_ready`,
`discovered` (each `complete|partial|unknown|unavailable`), `known_through?`,
`gaps:[{facet,reason}]` and `truncation:explicit_limit|explicit_depth|null`. Counts of inaccessible
records/gaps are never disclosed. Frame lifecycle fields are both null if no
relation projection was used; otherwise version 1 and the shared projector's
actual visible-dependency hash. A completed zero-row query means no match in
that admitted population/frame/profile. Even complete query execution and every
delivered page do not prove complete source coverage or supported absence.

Inspection returns exact accepted statements (including qualification statements),
render clauses/mappings/omissions, type/vocabulary pins, original clocks and actor/
trust facts, evidence pins and correction predecessors/successors/reasons/branches.
Result inspection returns its schema, query/typed parameters, coverage, protected
population manifest and paged contributor witnesses. All targets must be visible
in this run or explicitly admitted; lineage inspection may admit only authorized
correction neighbors. Unsupported claim/relation facets are refused, not empty.
Assessed-relation inspection uses the projection and qualifications in §1; it
never derives its own lifecycle state or silently substitutes a legacy read.
Hydration checks both observation and source dependencies and returns exact bytes
or lossless retained normalized text, unit/part hashes, returned-span SHA-256,
original locators, occurrence, attribution and any transformation/version label.
No synthetic coordinates, imports, file/network fetch or source reauthoring.
Whole-source completeness remains independent of completion of the selected spans.
Available helper payloads are `inspection:{observations:[ObservationInspection],
corrections:[CorrectionRow],source_units:[SourceUnitRow],result:ResultMetadata?,
relations:[Assertion],relation_types:[TypeDefinition],relation_pairs:[PairDisposition],
evidence_pins:[EvidencePin],provenance:ProvenancePage?}` or
`hydration:{slices:[{evidence_pin:EvidencePin,representation,start,end,content:text?,
bytes_base64:text?,returned_sha256:text}],selection_complete:bool}`; both include
visibility, work, access_bases, next_cursor? and has_more.
This new wrapper has the following self-contained closed types; existing published
helper payloads retain their own meanings and bytes, not mutable implicit schema
dependencies of this packet. `?` is required nullable; limits apply to whole
encoded responses and existing immutable input bounds, never silent truncation.

```text
ObservationInspection = {observation:ObservationRow,statements:StatementRow[],
  render:{revision:int,title:Clause,summary:Clause[],detail:Clause[],
          omissions:[{statement_id:uuid,reason:text}]}?,
  type:{key:text,revision_id:uuid,definition:text},
  authorship:{receipt_id:uuid,task_id:uuid,attempt_id:uuid,run_ref:text,
             task_contract_key:text,task_contract_version:int},
  mentions:EntityMentionRow[],entities:EntityRow[],topics:TopicRow[]}
Clause = {text:text,statement_ids:uuid[]}
UnitSupport = {event_id:uuid,source_unit_id:uuid,content_sha256:hash}
EvidencePin = {ref:uuid,bead_id:uuid,bead_version_id:uuid,statement_id:uuid,
  unit:UnitSupport,package:PackagePin?,part:PartPin?,native:NativeFacts,
  qualification:SourceQualification?,normalization_policy_version:text?}
PackagePin = {package_id:uuid,sealed_receipt_id:uuid,inventory_sha256:hash,
  required_parts:int,required_characters:int,required_utf8_bytes:int,
  source_revision_key:text,occurrence_key:text,
  occurrence_identity_basis:native|producer_assigned,package_revision:int}
PartPin = {part_id:uuid,ordinal:int,component_key:text,component_offset:int,
  parent_component_key:text?,kind:text,content_sha256:hash,characters:int,
  utf8_bytes:int,derivation:identity_utf8|producer_normalized,lineage:RawSpan[]}
RawSpan = {source_range_receipt_id:uuid,byte_start:int,byte_end_exclusive:int,
  source_bytes_sha256:hash,fold_receipt_id:uuid?,fold_outcome_ordinal:int?}
NativeFacts = {native_id:text?,parent_native_id:text?,session_native_id:text?,
  branch_native_id:text?,participant_native_id:text?,role:text?,source_order:int?,
  occurred_at:timestamptz?,occurred_at_raw:text?,time_precision:text?}
SourceQualification = {qualification_ref:text,boundary:qualified_native_unit|unresolved,
  boundary_basis:text,physical_records:complete|pending_tail,
  topology:known|partially_known|unknown,normalized_input:complete|incomplete,
  source_completeness:producer_attested|unresolved,
  exclusions:text[],unresolved_coverage:text[]}
```

All `*Row` types are the complete named logical row schema in §1, with both keys
and columns; singular names map to their plural relation. Relation Assertion /
TypeDefinition / PairDisposition are the pinned dependency's exact closed types,
encoded using this packet's scalar wire rules. EvidencePin binds actual unit
support, never invented sentence-to-span evidence. Absent package/part is
unsupported for part hydration; inspection remains useful. ResultMetadata is the result record above;
CorrectionRow/SourceUnitRow use the exact logical relation schemas. Unrequested
facets have empty collections/null, not a claim of no corrections/evidence.
ProvenancePage is `{population_ref:uuid,nodes:[{node_ref:uuid,operation:scan|filter|
project|join|group|distinct|exists|set|window|recursion|limit,input_refs:[uuid],member_refs:[uuid],
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
digest, contexts/root pins, refs, consuming run and current authority. Only refs in delivered rows,
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
Unavailable bindings never recreate a result under the original ID/key. A valid
checkpoint context may deliver already retained bytes under their original result
ID after standalone access expires; that is contextual reuse, not recreation or
renewal of the expired standalone route. Erased bytes can never be recovered here.

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
silently widen an old result. The recommended policy sets `expires_at` to
`created_at + 30 × 24 hours` UTC for temporary standalone access. Distinguish three things:

- **Standalone disclosure:** direct page/import/inspect/SQL-input reuse requires
  trusted time strictly before that result's original `expires_at`, as well as
  current authority. A child or physical retention hold does not extend this route.
- **Dependency retention:** an admitted child/checkpoint retains the exact bytes
  and provenance required for its own promised lifetime under the accounted quota.
  Such a hold prevents premature physical purge, but grants no standalone parent
  access. A valid child's computation and bounded provenance inspection may use
  required held dependencies despite the parent's standalone expiry; this does not
  admit the parent as a fresh query input or expose unrelated parent contents.
- **Checkpoint disclosure:** an explicit, currently valid retained checkpoint may
  expose the root result IDs/digests selected in its immutable manifest, under a
  separately receipted checkpoint-bound access context. That route survives the
  roots' standalone expiry while the checkpoint is retained (without routine
  expiry for an active named save), subject to current authority and erasure.
  Unselected ancestors are dependency-only, not automatically saved/queryable roots.

Do not cap a new child's life at its oldest parent's standalone expiry or mutate
the parent's immutable metadata/digest. Admit required holds atomically with the
child/checkpoint/save or refuse the allocation. A checkpoint may select a root
only while that root is accessible directly or through an already valid explicit
checkpoint context; physically held but otherwise expired data cannot be rescued
by guessing its ID. Extending a child chain alone never renews an ancestor's direct
access. New children are distinct, currently authorized and fully charged results.
After all disclosure contexts end, only independent live discovery can produce
new accessible results; it cannot claim reuse of unavailable original bytes.

Materialize the entire admitted query or return no result. Explicit SQL LIMIT is
part of the query and labelled limited coverage; delivery page limits never alter
SQL. Timeout, allocation overflow or incomplete provenance yields a terminal
failure receipt, not an unfinished aggregate or secretly clipped result. Persist
ORDER BY and internal tie witnesses; absent ordering, seal a deterministic
witness order. Duplicate values have distinct row ordinals. Authenticated cursors
bind result/digest/schema/frame/order/next ordinal and owner scope; current consuming
run authorization is checked on every page. Index changes do not invalidate saved
pages. Bad/tampered cursors are invalid_request; an expired disclosure context is
unavailable even if its bytes are still held. Cursors cannot switch from direct
to checkpoint disclosure implicitly.

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

PR-05 persists compact manifests and retention/access; PR-06 interprets progress
and chooses the next query. No database snapshot, open transaction, hidden model
reasoning, model internals or second canonical memory store is captured.

`CheckpointPin={checkpoint_id:uuid,manifest_digest:hash}` and
`CheckpointAccess={checkpoint:CheckpointPin,basis:RetentionBasis}`. An immutable manifest is
`{checkpoint_id,investigation_id:uuid,branch_id:uuid,sequence:int8,
predecessor:CheckpointPin?,fork_origin:CheckpointPin?,working_state_source:CheckpointPin?,created_at:timestamptz,
automatic_expires_at:timestamptz,question:text,progress:{status:investigating|
paused|ready_for_pr06,note:text},findings:[{text:text,facts:[fact_ref],
evidence_refs:[EvidenceRef]}],roots:[{result:ResultPin,frame_ref:uuid}]}`.
Question ≤4 KiB, note ≤2 KiB, ≤16 concise findings totaling ≤8 KiB; ≤32 roots,
whole canonical manifest ≤32 KiB. Referenced facts/evidence must have delivery
receipts and belong to selected roots or their inspected witness closure; text
is attributed working state, never accepted meaning or proof. Root frames may
differ in a manifest, each labelled separately; that grants no mixed-frame SQL.
The manifest digest uses result-json-v1, excluding itself. No result bodies are
copied. A predecessor/fork pin is historical lineage, not automatic disclosure of
that checkpoint or all its roots. Protect question/findings/names as result data.

These actions use the common run/step envelope and current identity equality:

| kind | Closed request payload | Available payload beyond common reply |
| --- | --- | --- |
| checkpoint | `investigation_id:uuid?,branch_id:uuid?,expected_head:CheckpointPin?,question,progress,findings,roots:[{result:ResultPin,access:AccessContext}]` | `checkpoint:Manifest,branch:{branch_id,head:CheckpointPin},retention:RetentionState` |
| read_checkpoint | `access:CheckpointAccess` | `checkpoint:Manifest,retention:RetentionState,root_contexts:[AccessContext]`; no result rows become visible |
| save_checkpoint | `access:CheckpointAccess,name:text,expected_save_revision:int8?` | `save:SaveBinding,retention:RetentionState` |
| resolve_save | `investigation_id:uuid,name:text` | `save:SaveBinding,access:CheckpointAccess,retention:RetentionState`; exact active binding after full reauthorization, no rows |
| release_save | `investigation_id:uuid,name:text,expected_save_revision:int8` | `released:{save_id:uuid,revision:int8,state:released}`; noncontent management receipt only |
| restore_checkpoint | `access:CheckpointAccess,selected_roots:[ResultPin]` | `branch:Branch,checkpoint:Manifest,root_contexts:[AccessContext]`; no implicit row/fact/evidence delivery |
| branch_investigation | `access:CheckpointAccess,selected_roots:[ResultPin]` | Same as restore; explicit fork without interpreting progress |

For the first checkpoint, investigation/branch/expected_head are all null; trusted
code allocates an investigation and branch. Later writes supply all three and
compare-and-swap the head under a branch lock. A mismatch is `branch_conflict`,
with no overwrite or leaked competing manifest. Roots are duplicate-free; their
digests and access contexts must verify before allocation. Each successful append
atomically publishes manifest, head, holds, storage charges and idempotency receipt;
concurrent identical keys get one manifest. Text/roots and all contexts enter the
fingerprint. No partial checkpoint or acknowledged head exists on allocation failure.
Sequence starts at one per branch and increases by one; predecessor is the
compared head, fork_origin is set only on a restored/branched initial checkpoint.
Automatic expiry is creation + 30 × 24 hours UTC. save/resolve never changes it.

`SaveBinding={save_id:uuid,investigation_id:uuid,name:text,revision:int8,
checkpoint:CheckpointPin,state:active|released,recorded_at:timestamptz}`.
Name is `[a-z][a-z0-9_-]{0,63}`, unique per investigation and owner. Null expected
revision creates a previously unused name; an existing name requires its exact
latest revision. Updating a name atomically transfers its hold to the new checkpoint
and appends revision+1; no silent overwrite. release requires the active revision,
appends released revision+1, and ends that save's hold. A mismatch is `save_conflict`.
Only the original authenticated owner identity may manage it; ordinary replays
still reauthorize. Save needs an already retained, fully authorized checkpoint:
expired/pruned/erased manifests cannot be rescued by IDs or dependency-only holds.
Releasing an owned name can proceed without disclosing its revoked contents, so
inaccessible saved bytes do not trap storage. Its management reply always contains
only opaque save ID/revision/state and noncontent charges, not checkpoint/name
metadata. Exact replay returns the original mutation receipt plus a new access
receipt; it cannot resurrect a released binding. New save/update is a new step.
resolve_save is the cross-run/restart name-to-pin lookup; missing, released or
denied names share unavailable. It discloses no protected name/checkpoint metadata
before the active binding's whole closure is reauthorized.

`RetentionState={automatic_until:timestamptz,automatic_retained:bool,
named_holds:[{save_id:uuid,revision:int8}],retained:bool,hold_ref:uuid?,
unique_bytes:int8,context_end:automatic_expiry_or_prune|last_save_release}` is
dynamic, protected and receipted; it is not part of the immutable manifest digest.
Retain the newest 20 automatic checkpoints across all branches of an investigation,
each for at most 30 days from creation, plus every actively named checkpoint.
An older automatic manifest may survive solely via named holds; pruning its
automatic slot does not end named access. No checkpoint-count limit constrains
query generations or branch depth. Appending checkpoint 21 atomically prunes the
oldest automatic slot; retain noncontent lineage pins for valid descendants and
never release holds needed by another checkpoint/result. No silent eviction of
an unexpired result or active named save. Allocation is refused if admitted quota
cannot honor these promises. Explicit release ends a save, not another holder.

Restore/branch lock the retained source checkpoint, reauthorize its **whole** root
closure, require selected_roots to be a subset of its exact roots, and atomically
create a new branch with a new automatic initial checkpoint containing only those
roots, copied question/progress and only findings whose references remain selected.
Empty selection creates a question-only branch; never attach dependency-only
ancestors as roots. Copied working text binds `working_state_source` to the source
checkpoint and retains/rechecks that whole protected dependency closure, even when
some roots are unselected; no reduced-authority text salvage. An ordinary authored
checkpoint has null working_state_source. A hold for this metadata does not grant
independent access to the source checkpoint or its unselected roots.
Return `Branch={branch_id:uuid,investigation_id:uuid,
fork_origin:CheckpointPin,head:CheckpointPin,created_at:timestamptz}`. New manifest
contexts carry its new ID/digest; source cursors cannot cross over. This admitted
hold transfer may extend contextual use, costs storage/work and leaves every
result's standalone expiry unchanged. No rows are admitted merely by restoring:
reuse/inspect/hydrate with the returned context explicitly delivers them to this
run. No canonical memory rollback, budget refund, revived permission or relabelling
of historical evidence. Restart preserves unfinished reservations/counters.

Checkpoint access validates its **specified** automatic or named-save basis;
cursors pin that basis plus checkpoint/root digests, never a mutable alias.
Release/retarget ends the old named-save revision's context immediately, even if
other holds retain the checkpoint. Automatic expiry/prune ends the automatic
context even if a save remains. Using a different valid basis requires an explicit
new step/context/cursor; there is no fallback. All holds
retain unique required result/provenance bytes and the manifest, not a new content
copy. Context closure, pruning and quota settlement are atomic with their receipt;
owned GC follows after. Revocation refuses the whole checkpoint, including question,
findings, root hashes, counts and save names. Erasure overrides all holds.

Required boundary examples (future installed-runtime acceptance, not executed
proof in this proposal):

| Situation | Required result |
| --- | --- |
| Parent P expires on day 30; child C created on day 29 has its own day-59 expiry | On day 31, direct P paging/query-input reuse is unavailable. C remains usable with necessary held provenance; P's original digest/expiry do not change. |
| Before day 30, named save S explicitly selects P as a root | On day 31, direct P access is unavailable, but explicitly S-bound P read/refinement is allowed after current authorization; it preserves P's identity/frame and reports checkpoint ID/digest, root pin and S's hold revision. |
| P is only an unselected ancestor of C in save S | S preserves C and required evidence, not unrestricted P access. P cannot be promoted to a root after direct expiry merely because its bytes remain held. |
| S is released after P's direct expiry, but C still needs P | S-bound P access stops immediately; C's bounded dependency use remains. Retain P until no valid holder needs it; do not silently renew P or break C. |
| Revocation/regrant or governed erasure affects P | Revocation blocks every affected disclosure context. Regrant alone restores neither expired direct access nor a released save; an existing valid checkpoint may resume only if bytes remain and all current authority checks pass. Erasure overrides every hold and invalidates dependent disclosure. |
| Automatic checkpoint A is pruned/expired while named save S still holds it | An A-automatic cursor refuses; a new explicitly S-bound step/cursor may use selected roots. Retarget/release of S never switches the old cursor to another hold or checkpoint. |
| Checkpoint 21 and a branch restore after >20 result refinements | Only oldest automatic history is pruned; named saves/valid-child closure remain. A fresh branch gets new manifest/head/context; memory, prior frames, permissions and charged work are unchanged. |

## 5. Revocation, erasure, retention and work policy

Reauthorize the whole result closure, including parents, authoring context, source/
model dependencies, correction/identity dependencies, contributor/population
manifests, query text, parameters, counts, hashes and lineage. Losing any required
dependency refuses all access and derivation. No row filtering, count decrement,
old identity substitution or stale metadata escapes under the original ID.
Regrant may restore access only through a still-valid disclosure context (unexpired
standalone access or an explicit retained checkpoint selecting that root), with
all required bytes retained and no erasure. Physical retention alone cannot make
an expired direct route or released checkpoint usable again. The standalone
expiry of a held ancestor is not evidence loss for a valid child; missing, erased
or unauthorized required dependency content still refuses the whole child.
Reduction/salvage and sharing between principals are excluded from v1.

Governed erasure first blocks disclosure at the same authority fence and invalidates
affected descendants; purge result bodies, queries/parameters, manifests, hashes,
indexes and sensitive receipt bodies within a proposed 24-hour cleanup SLA.
Disclosure-context expiry/release blocks that route immediately. Ordinary physical
cleanup starts only when no unexpired standalone route or valid child/checkpoint
hold requires the data, with the same proposed cleanup SLA. Governed erasure
overrides those holds and blocks every affected route before purge. Leave only
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
| Run lifetime / operations | 30 minutes, 128 admitted accesses (pages, imports, helpers, checkpoints, restores, retries); counters/deadlines survive restart |
| Operation / cumulative DB work | 30 seconds per whole operation, 300 seconds per run, including authorization, lineage, persistence and cleanup; every constituent statement uses the remaining deadline; 500 ms lock timeout |
| Workspace admission | Two active runs, one executing DB operation; rolling 24-hour 1,800-second DB allowance and 256 MiB transport; no reset through new runs/branches |
| SQL structure | 32 KiB SQL, 64 parameters / 64 KiB parameter bytes, 8 saved inputs, 16 relation references, 8 CTEs, 16 join/group/set/window nodes, expression depth 32; one qualified recursive CTE with depth 1–8 and ≤16 seeds |
| Delivery | Default 20 / maximum 50 rows per page, 256 KiB whole response; helpers 16 observations / 8 source selections; hydration 64 KiB per response, at most 16 KiB per selected span |
| Transport | 16 MiB per run including metadata, cursors and redelivery; reserve 16 KiB for terminal diagnostics |
| Retention admission | 64 MiB charged allocation/result, 128 MiB new allocation/run, 512 MiB retained unique allocation/workspace including saved closure; no independent result-count/ancestry cap; reserve before execution |
| Checkpoint/control state | 32 KiB manifest, newest 20 automatic checkpoints/investigation for ≤30 days; named saves persist while saved under the same quota; history stubs/audits/holds are charged too |
| Query settings | `work_mem=4MiB`, `hash_mem_multiplier=1`, `temp_file_limit=64MiB`, parallel query disabled, JIT off; restricted login cannot change them |

These are the smallest recommended **qualification baseline**, not observed
capacity. W2's 2,000 × 2 KiB is 3.906 MiB of rows; 40 pages of 50 fit 128 accesses
with room for W4's 16 hydration calls, >20 refinements and checkpoints. 16 MiB
transport leaves space for page envelopes/provenance and rereads; still meter
actual encoded responses, not this arithmetic. W3's 100K lineage members at a
planning estimate of 256 bytes/member is 24.4 MiB before node/index overhead;
64 MiB/result is a candidate, not an asserted provenance size. Use shared DAG
nodes, not quadratic copied partition witnesses. Twenty 32 KiB manifests are
640 KiB, but twenty distinct 16 MiB result closures are 320 MiB before overhead;
512 MiB/workspace is a modest admission tier, not unlimited durable saves.
Shared roots are charged once; a save cannot fit by ignoring ancestors. At 30-day
retention, even 4 MiB/day consumes 120 MiB before provenance; sustained histories
can hit quota and require explicit release or operator admission. There is no
promise that every new save fits indefinitely.

Thirty seconds/operation and 300 seconds/run are measurement candidates giving
room for W3 cold aggregation and iterative W1/W5 work; they establish no latency
claim. Before delivery, measure cold/warm W1–W7 on declared PostgreSQL/server,
indexes, hardware and authorization skew: complete query plus witness generation,
allocation/GC, p50/p95 duration, encoded transport, physical heap/index/WAL growth,
spills and canceled cleanup. Report all failures. Useful enumeration, aggregation,
set/window and bounded-path investigations must actually complete within the
approved tier; a correctly enforced timeout alone does not qualify them. Runtime
measurements are pending because Phase A authorizes no PR-05 executor or fixtures.
If the tier fails, revise indexing/layout first; a material policy increase returns
to exact owner approval and matching freeze. An explicitly operator-admitted
larger tier is an alternative for larger workloads, never agent-selected fallback.

Storage unit `allocation_bytes` is canonical retained result/manifest/witness/query
bytes plus ledger/row/index overhead charged by a versioned allocation profile.
The implementation must qualify conservative physical-overhead reservations for
its layout, measure actual owned growth and settle charges before publication.
Publish `allocation_profile_hash`, charged bytes and separately measured physical
bytes/measurement state in receipts. Do not call encoded bytes physical disk use.
No allocation is acknowledged unless its rows, provenance and required holds fit
the reserved quota; uncertainty retains the reservation and stops new admission.
Shared allocations have one workspace ledger owner and reference-counted holds;
adding a hold charges only new manifest/ledger bytes while retaining its entire
required closure. No unbounded free checkpoint, lineage or idempotency metadata.

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

Store charged row/provenance allocations, separately measured physical growth and
shared attribution; no zero-cost descendants or accounting double counts.
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
| W2: million-row corpus; 2K authorized output rows at roughly 2 KiB/row | Complete immutable materialization, 40 pages of 50, restart and follow-up including metadata under the candidate 16 MiB/128-access envelope; truthful exhaustion is fence evidence, not successful enumeration |
| W3: 36 monthly groups over up to 100K eligible records, skew/fanout/corrections/late arrivals | Counts/distinct counts preserve multiplicity and contributor/population witnesses; useful cold/reused plans must fit proposed 30-second operation and 64 MiB result allocation or policy must be revised explicitly |
| W4: four 256-KiB retained source units | Sixteen 64-KiB calls using multiple ≤16-KiB exact spans transfer 1 MiB plus metadata; hashes/locators/attribution survive, gaps remain explicit; no new source family |
| W5: restart, cross-session reuse, >20 refinements/checkpoints, saved branch, dependency revocation and expiry/erasure | Same parent bytes/ID, explicit restore branches, no discovery rerun on import/page, no budget reset or ancestry cliff; test both sides of 30-day temporary expiry, durable saves and retention of their closure without duplication or stale access |
| W6: relation lifecycle and composition | Released assessed confirmation/dispute/retraction, current/as-of state and L1–L5 root/cycle rules; fresh support withdraws while historical saved values stay unchanged; revoked event evidence/actor metadata refuses the whole result, including independent counts |
| W7: SQL breadth and presentation | UNION/INTERSECT/EXCEPT bag/null cases, per-entity ranks/lag/running totals, bounded recursive depths/cycles/diamonds/high degree, full witnesses and protected negative populations; small preview then full saved count/page without discovery rerun |

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

This completed proposal stops at exact-interface approval. Parser/authority,
composition, useful workload fit and physical storage qualification remain
explicit Phase B acceptance obligations, not accomplished measurements. No unseen
holdout is created, read or inspected here, and documentation merge opens no gate.

Next dependency handoff: owner approval of the complete v2 interface pin record →
independent custodian freeze attestation → public implementation/qualification →
separately authorized public release → released-version Desktop consumption.
