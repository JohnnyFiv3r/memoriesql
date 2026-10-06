# PR-05 standalone query and result reuse (preview cut)

Primary acceptance claim: **an authorized principal, through the trusted
executor, can run admitted SELECTs over fourteen prepared logical relations,
receive an immutable result pin, page it with disclosure receipts and
owner-bound cursors, and re-read it later without repeating discovery; every
disclosure first re-checks current authority over the whole saved dependency
closure.** This is the smallest useful public query/result path, not complete
PR-05 availability. Expiry cleanup is delivered, and governed erasure is
unavailable in this preview. Checkpoints, saved-input refinement,
inspection/hydration through this contract and measured W1–W7 fit remain
pending.

Base: #68's refreshed head `674745e` (public main `5290ad1` plus #68). The
approved packet `78bbc04e…` and whole lifecycle dependency `69a0d985…` at
`b3865f307b50bd94681b387ad0f3b16fbb20213a` and ea-3 `12e802be…` are unchanged.
M0037 and M0038 are reserved for PR-05 by the coordinator. SQL 0001–0036 and all
earlier records keep their bytes. No unseen questions, gold or access paths were
accessed or received.

## Schema 37: observation-family population

`prepare_query_sql_population_v2` consumes PR-03's unchanged relation projection
(M0032) and adds observations, statements, statement_sources, source_units and
corrections at the same explicit cutoff, under the same authority fence and
repeatable-read frame. It adds no second authorization evaluator. Each family is
admitted only if all of the following are authorized:

- the bead's event, for `memory.query`, and its accepted version and statements.
  These are bounded stored-bead inspection's predicates.
- every unit whose retained text the catalog exposes, for `memory.query` and
  `source.read` on its event. This is the canonical source-unit row authority
  of M0006.
- **every correction neighbour visible at the frame**.

Otherwise the whole family is withheld, so no successor count, branch or
older-as-current presentation escapes. The protected records
(`query_bead_records_v1`) have PR-03's record shapes and join one deduplicated
dependency manifest.

Unlike stored-bead inspection's source parts, this requires no raw source
authority. `source.raw.read` gates exact raw bytes, evidence packages and source
revisiting, and none of those are exposed here. `source_units.search_text` is
**retained unit text, not exact raw bytes or part hydration**.

**Owner decision (2026-09-28, at or before 17:15 CDT, relayed by the
coordinator):** paired agents holding `source.read` may read retained
source-unit text and unit facts through their own run-bound, receipted query
results. They also need `memory.query` on the bead's event, plus version and
statement authorization. `source.raw.read` stays owner-only. No PR-03 authority
or interface changes.

**Owner decision (2026-09-28, relayed by the coordinator): extend to package
text.** A materialized logical unit (M0017) retains no text of its own. An
authorized agent now receives its normalized projection, within these bounds:

- **Pinned package only.** Text comes only from the package pinned by the unit's
  materialization binding, never a newer representation of the same occurrence.
  The pin must still match the sealed inventory.
- **Normalized parts only.** Every part must be `producer_normalized`. A package
  with any `identity_utf8` (raw) part yields no text: the citation keeps the
  evidence ref, unit ID, pinned package and content SHA-256, with `text_state`
  `unsupported`.
- **Exact inventory.** The text is the sealed parts concatenated in ordinal
  order, unmodified. No neighbouring unit, unrelated part or raw source reader is
  reached.
- **Visible binding.** `package_revision_id` is the pinned package, and the
  unit's `content_sha256` is that package's inventory hash.
- **Labels.** Every result using `source_units` carries the coverage gap
  `source_units.search_text` / `normalized_text_not_exact_source`. Served
  package text adds `normalized_projection:<normalization version>` and the
  package's own declared limits (`package_exclusion:…`, `package_unresolved:…`).
  It is never described as raw evidence or as sanitized.
- **Closure.** The pin, its parts' hashes and derivations, and their raw lineage
  join the sealed dependency closure as a `unit_package` record. Reuse
  reauthorizes it whole, and the disclosure receipt binds the exact delivered
  response. The raw lineage is noncontent provenance that is never disclosed to
  agents; raw bytes stay owner-only.

Units that retain their own text (not materialized) return it as before.

**Custody: reconciled by ea-4.** The independent custodian's ea-4
reconciliation, relayed by the owner, is stored byte-identical in
`../approvals/pr-05-custody-ea-4.json` (12,447 bytes, SHA-256 `d118ded2…`). It
reconciles the source-text authority record
(`../approvals/pr-05-agent-source-text.md`, SHA-256 `63ecbb4c…`) with ea-3 and
the attributable owner approvals, as the custodian's determination on the
source-text notice required. That determination is stored verbatim in
`../approvals/pr-05-custody-source-text-determination.txt`.

**ea-4 covers semantics only.** It is not a statement of runtime correctness,
security qualification, implementation completion or release readiness.

**Exact-byte link.** ea-4 links to implementation commit `9a396dd`, where
migration 0038 is `76d94068…`. This PR's head changes migration 0038 to
`eabebb7f…`, for crash- and settlement-outcome handling and cleanup scheduling
only:

- outcome labels and a read-only preparation probe (`88f5f57`);
- a distinct settled-access refusal, so a committed result survives a failed
  first disclosure (`92f7ccf`), giving `8835d25e…`;
- the review fix that keeps pending cleanup subjects from starving purgeable
  ones, giving `eabebb7f…`.

None of it touches populations, source authority, search candidates or evidence
availability. Migration 0037 (`f8e2d468…`) is unchanged.

**Implementation linkage: ea-4-link-2 → `f6b1de2`** (migration 0038
`8835d25e…`). The custodian recorded it at 2026-09-29T01:53:32Z. It is stored
byte-identical in `../approvals/pr-05-custody-ea-4-link-2.json` (2,919 bytes,
SHA-256 `4e2e350b…`) and names change `92f7ccf` and migration 0037 `f8e2d468…`.

- **Supersedes ea-4-link-1 → `6aca830`,** also stored byte-identical in
  `../approvals/pr-05-custody-ea-4-link-1.json` (2,501 bytes, SHA-256
  `20447b37…`).
- **Semantics only.** The linkage is not a statement of runtime correctness or
  release readiness. It records a linkage update only, with no new authority or
  evidence-semantic reconciliation and no replacement freeze.
- **Its grading note:** a committed result or `settlement_pending` response is
  not successful receipted disclosure.
- **This head's migration 0038** (`eabebb7f…`) differs from link-2's `8835d25e…`
  only by the review fix to the cleanup pass. The linkage state is **ea-4-link-2
  → `f6b1de2`; link-3 for this head has been requested** from the custodian. No
  coverage is claimed beyond that.

The PR-05 part of the rules the custodian named:

- one authorization and representation rule set for every caller;
- unchanged raw-source restrictions;
- existing evidence requirements;
- source text returned by query labelled as not exact and hydration-required,
  so its readability earns no hydration or exact-source credit.

Evaluation-condition parity and grading credit are enforced outside this core.

PR-03's nine assessed relations keep their unchanged projection. How a paired
agent reads them is decided by the owner's AM-5 record
(`../approvals/pr-05-agent-relation-reads.md`) and implemented by migration 0039
(see "Schema 39: agent relation reads" below). A caller without `source.read`
gets `observation_tables`/`source_read_required`. All gaps describe only the
caller's own capabilities, never whether protected data exists.

Column mapping decisions (implementation readings of the approved enums; review
welcome):

| Column | Derivation |
| --- | --- |
| `observations` rows | One per accepted bead version (`accepted_bead_semantics`) authored at or before `known_at`. |
| `correction_state` | Direct successors recorded by `bead_supersessions` at the frame: 0 `unsuperseded`, 1 `superseded`, 2+ `branched`. |
| `view=resolved` | Withholds superseded and branched predecessors from `observations` only; keeps every unsuperseded successor branch; no recency winner. Other relations are view-independent, as §1 states. |
| `title/summary/detail`, `render_state` | Rich-render revision 2 with a payload is `present`; legacy or absent renders are `unsupported` with null text. Statement text is never replaced. |
| `effective_at`, `effective_basis` | Only an `instant`/`second` precision time of the bead's own source unit (or, absent unit time, its event), basis `source`; otherwise null/`unknown`. No authored effective time exists canonically. |
| `source_units` | Only units with a support link to an admitted observation (the bead's own unit and statement evidence units). Unit time is preferred; event raw text/time zone is reported only for event-level time. |
| `package_revision_id`, `trust_label`, `occurrence_ref` | Always null (unsupported canonical facet); disclosed as coverage gaps whenever `source_units` is used. |
| `corrections` | One row per recorded supersession, `recorded_at` = the successor version's authorship time (the table has no timestamp). |

`stage_query_population_v2` stages all fourteen relations with M0033's exact
issuer/epoch binding. Its size admission uses native jsonb text rather than the
character-by-character canonicalizer, so large source text does not consume the
operation deadline. Revision-2 populations run staging and preparation within
the admitted operation deadline (at most 30 s) instead of the canonical 2.5 s.
`check_query_result_closure_v2` is M0035's verdict for revision-2 results,
reprojecting the saved cutoff and view under current authority. Entity, alias,
mention and topic relations remain unprepared: queries naming them reply
`unsupported_query` with feature `unprepared_relation`, never an empty table.

## Schema 39: agent relation reads (AM-5)

**Authority.** The owner decision AM-5 is committed byte-identical as
`../approvals/pr-05-agent-relation-reads.md` (SHA-256 `4491ca29…`). It clarifies
the lifecycle dependency's rule that reads use current `memory.query` and
source-read authority over their entire disclosed dependency closure. PR-03's
read kernel required raw revisiting authority there instead, so paired agents
saw `relation_tables`/`source_raw_read_required`.

**Custody: reconciled by ea-6, a child of ea-5.** These are the independent
custodian's records, stored byte-identical:

- **ea-5** (`../approvals/pr-05-custody-ea-5.json`, 18,783 bytes, SHA-256
  `be36131e…`, issued 2026-09-29T12:26:52Z) reconciles AM-5's authority and
  evidence semantics with ea-2, ea-3 and ea-4. It binds AM-5's record
  (`4491ca29…`) and the content-free reply-visible addendum.
- **ea-5-link-1** (`../approvals/pr-05-custody-ea-5-link-1.json`, 7,752 bytes,
  SHA-256 `f1ea1a28…`) links ea-5 to implementation `f560fce`, with qualified
  head `7f8e4a8` and migration 0039 at `ed951d1a…`. That implementation predates
  the review repairs in `9f3aa77`.
- **ea-6** (`../approvals/pr-05-custody-ea-6.json`, 29,178 bytes, SHA-256
  `74221b6f…`, issued 2026-10-06T01:30:11Z) is ea-5's child. It binds the
  owner's scope clarification of 2026-10-01 (decision 12(a)). At schema 39,
  relation reads without raw-read authority are limited to the paired agents
  AM-5 authorizes. Every other caller keeps its existing restrictions, so
  `relation_tables`/`source_raw_read_required` remains at schema 39 for callers
  AM-5 does not authorize. ea-6 also binds the exact implementation:
  - head `9f3aa77`, with migration 0039 `ee846ac7…`;
  - the migration inventory, the reply coverage source, and this document as
    it stood at `9f3aa77` (`35fabf31…`);
  - the owner-reported qualification receipts.
- **History unchanged.** AM-5's record, the addendum and ea-5 stay as they were.
- **Semantics only.** None of these is runtime, security, semantic-quality or
  workload certification, release readiness or a combined-branch qualification.
- **Still requested:** ea-6 issues no implementation link. The next link after
  ea-5-link-1, for `9f3aa77`, and the custodian's determination on #80 are
  pending.

**One read mode in PR-03's single kernel.** Migration 0039 threads a read mode
through the eleven read-path functions of M0030 and M0032 that reach a raw gate.
The owner's mode keeps every existing check. The agent's mode changes exactly one
thing: where the owner's read requires raw revisiting authority over a source
object, it requires `source.read` on the same event.
- The ten gate sites are M0030 lines 142, 150, 153, 160, 255, 317, 329, 360, 382
  and 1493 at `33e1c42`; line 317 authorizes each derivation root through its
  primary event.
- Memory.query closure checks, whole-family withholding, the lifecycle projection
  and root computation are shared and unchanged.
- Every earlier function name becomes a one-line wrapper over its mode-threaded
  body in owner mode, so no second lifecycle interpreter exists.
- Governed writes refuse the agent mode.
- The population entry `prepare_query_sql_population_v2` is redefined in
  M0039. The M0037 file is not edited. It selects the owner mode for a caller
  holding `source.raw.read`, and the agent mode only for a paired agent (an
  `agent` principal with a pairing grant): AM-5 names paired agents. Any other
  caller, such as a paired background service or device, keeps the earlier
  gate, which withholds every relation without raw source authority, and gets
  `relation_tables`/`source_raw_read_required` as before. The mode is recorded
  in the internal frame field `relation_read_mode`, which never appears in a
  reply; it is empty for those other callers.
- M0039 grants nothing and adds no object the query reader can see, so the
  reviewed reader profile is unchanged.

**What an agent reads.** With `memory.query` and `source.read` over a relation's
whole closure, an agent reads whole rows of `assessed_relations`,
`relation_statements`, `relation_evidence`, `relation_types`,
`relation_corrections` and `relation_replacements`, with the owner's values at
the same frame.
- The closure is the endpoint and basis beads, versions and statements, every
  evidence event, lifecycle evidence, corrections and replacements, and the root
  units and events.
- A relation with any unreadable closure member is absent from every table. No
  gap, count, type pin or history is disclosed for it, and the query stays
  available.
- A relation is also absent when its records name another assessed relation
  that is withheld: a replacement in its chain, or a `derived_from` relation on
  its root lineage. Otherwise its state, `relation_replacements` row or root
  status would show that the withheld relation exists. The population drops
  such families until nothing more is withheld.
- `relation_events`, `relation_event_evidence` and `relation_pairs` stay
  owner-only. A query that references them gets
  `relation_history`/`owner_only`. "Owner" here means PR-03's own gate for
  every relation read, `source.raw.read`: in the shipped role model only
  `personal_owner` holds it, and a deployment that grants it to a service gives
  that service PR-03's raw-holder reads, as before migration 0039.
- A caller without `source.read` that references a relation table gets
  `relation_tables`/`source_read_required`.
- `source_raw_read_required` remains for frames from a schema-38 database and
  for callers that are neither raw-read holders nor paired agents.
- Root identities are source-object UUIDs; no relation column carries one.
  Raw bytes, lineage, source revisiting, the `relations` inspection reader,
  activation and governed writes stay owner-only.
- Returned relation rows earn no hydration or exact-source credit.

**Lifecycle.** State, support eligibility, head and roots come from the one
`relation_projection_v1`, so an agent sees exactly the owner's state at each
frame. Withdrawn and disputed assertions are not support-eligible, and recursive
path support additionally requires qualified roots.

## Schema 38: runs, accesses and disclosure

The approved baseline (packet §5) is enforced in the database and pinned by
policy hash `b19f408584b5d6aac158c0d122be3c132cda9b6d14f6e6809ad05510b15b18d1`.
These remain qualification targets, not measured capacity.

- **Runs** last 30 minutes. A workspace admits two active runs. The host starts
  a run after authentication; callers set no tenant, principal, allowance or
  policy.
- **Admission.** Every access, including retries and redeliveries, is one
  delivery admitted under a workspace lock before work starts. It reserves at
  most 30 s and a 256 KiB response against:
  - the run's 128 accesses, 300 s and 16 MiB (16 KiB kept for diagnostics);
  - the rolling 24-hour 1,800 s / 256 MiB workspace allowance;
  - one executing operation per workspace.
- **Refusals write nothing.** Any unsettled delivery blocks new admission, and a
  dead owner's work is reported as `settlement`.
- **Charges.** Charged time is the observed wall time of the whole operation
  (not CPU or I/O); commit latency is not separately metered. Unknown timing
  keeps the full reservation. Allocation remains M0031's 64/128/512 MiB ledger
  with its declared minimum charge profile (`641aed62…`). Physical bytes are
  reported as `unavailable`.
- **Idempotency.** `(run_ref, step_key)` is the idempotency identity. Identical
  bytes redeliver the same stored page under a new access receipt, with new
  charges and no query rerun. Different bytes are `idempotency_conflict`. A
  failed step replays its outcome.
- **Disclosure.** Each disclosure runs the revision-2 whole-closure verdict and
  records its receipt in the same fenced transaction, before any byte is
  returned. The receipt binds the response digest, context, page, delivered
  refs and credential. It also stores run visibility and an optional
  owner-bound, immutable cursor.
- **Crash recovery.** A session advisory lock proves only that an owner ended.
  Redelivery or the host's `recover_abandoned()` then settles that delivery at
  its full reservation.
  - A committed result is then disclosed without rerunning.
  - A single-use preparation that never committed fails as `execution_error`;
    the client uses a new step key.
  - A commit refused by its own ownership, issuer or invocation checks, for
    example after the owner's session ended, is also `execution_error`. So is
    the exact redelivery of a step whose preparation was discarded.
    `unavailable` stays reserved for missing, denied or dependency-lost IDs,
    including authority lost at the fence.
  - A committed result is immutable. A failed first disclosure never discards
    it. If recovery settled the access while the commit was in flight, the
    host replies `settlement_pending` and the exact redelivery discloses the
    committed result without rerunning.
- **Expiry cleanup** (owner Decision 1, item 3). Access ends at expiry through
  the closure verdict. Owned cleanup then removes the content and every
  sensitive copy:
  - **At run expiry** (30 minutes): step request digests and page bindings,
    delivery response digests and delivered refs. Ended owners' work settles
    first, exactly as in host recovery. A stranded preparation can no longer
    commit, so its reservation drops to the journal charge, and its unresolved
    step gets the terminal `execution_error` disposition.
  - **At result expiry** (30 days): the body, witnesses and dependency records
    (including query text and parameters), creation and identity rows,
    invocation hashes, disclosure receipts and cursors. Retained allocation
    drops to the 8 KiB noncontent journal charge.
  - **Thirty days after the last purge:** the noncontent tombstone and its
    journal charge. The tombstone holds opaque run, step and result
    identifiers, terminal state, timestamps, work charges and idempotency
    disposition.

  Each subject is purged atomically, so an interrupted pass leaves nothing
  partial and the next pass completes it. A pass's item bound counts purged
  subjects only. Pending, held and failing subjects are passed over within the
  pass's time budget, so they cannot starve purgeable ones behind them.
  Failures are recorded with their SQLSTATE and retried on every pass, after
  healthy subjects. M0038 grants the
  staged-row owner EXECUTE on `uuid_eq`, because the foreign-key check runs as
  that owner when cleanup deletes an invocation.
- **Cleanup deadline.** The database cannot run without a caller. The host runs
  a bounded pass for its workspace before every run start. It must also
  schedule `cleanup_expired()` for all workspaces, for example hourly.
  - Content cleanup more than 24 hours past due, whether missed or failed,
    refuses new runs in that workspace as `budget_exhausted` / `settlement`.
    `cleanup_status()` reports it.
  - A workspace whose host schedules nothing and starts no runs keeps expired,
    already inaccessible content past 24 hours; its status shows it overdue.
  - Rows are deleted logically. PostgreSQL vacuum reclaims their space, and WAL
    and backups follow their own storage lifecycle.

**Extension outside the approved packet (owner decision #11):**
`close_run(run_ref)` is a trusted-host, owner-only early close, never an agent
wire action. It is refused while any of the run's deliveries is unsettled,
refunds nothing, keeps charges in the rolling window and cannot be reopened.
"Active" means neither expired nor closed; the two-run cap and 30-minute
maximum are unchanged.

## Measured preview range (Decision 1, items 1 and 2)

Fictional workspaces were built through the real capture, package, materialize
and author path, from dev builds of head `dab96f2`. Each has one assessed
relation; every other observation is a unit carrying about 2 KB of normalized
conversation-projection text. Environment:

- Apple M3 Max host;
- Docker VM with 16 CPUs and 7.75 GiB;
- the pinned PostgreSQL 18.4 image (`a02db8ca…`) with default settings.

Each size ran eight useful query shapes three times: enumeration, aggregation,
a statement join, a window, a set operation, EXISTS, a finding-to-source
citation and a relation join. Each run also paged forward and reused every result
from a second run. The driver, logs and manifest are archived as fictional dev
evidence under `memoriesql-evidence/pr-05/measure-dab96f2`. They are not W1–W7
and not a capacity guarantee.

| Observations | Packaged text | Prepared population | Dependency records | Query median (worst) | Next page | Cross-run reuse | Logical allocation per result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 50 | 0.10 MB | 0.52 MB | 461 (0.18 MB) | 1.9–2.1 s (2.3 s) | ≤1.0 s | ≤1.0 s | 0.59–0.90 MB |
| 100 | 0.20 MB | 1.03 MB | 911 (0.36 MB) | 2.9–3.3 s (3.3 s) | ≤1.6 s | ≤1.6 s | 1.16–1.76 MB |
| 200 | 0.41 MB | 2.05 MB | 1,811 (0.71 MB) | 5.4–6.1 s (6.3 s) | ≤3.1 s | ≤3.1 s | 2.29–3.49 MB |

(MB = 10^6 bytes.)

- **Preview range.** The preview is qualified only within this measured range:
  up to 200 accepted observations with about 2 KB of unit text each, on the
  declared machine. Every useful query shape completed at every size, well
  inside the 30 s operation limit. Beyond 200 observations is unmeasured and
  not claimed.
- **Cost grows with the whole authorized population.** Each query prepares it,
  and every page and reuse re-verifies the whole closure.
- **Allocation binds before time.** Each result seals the population's
  dependency records, so it retains about 15 KB of logical allocation per
  observation. At 200 observations, the 128 MiB run allocation holds roughly
  40–60 results, and the 512 MiB workspace allocation roughly 150–230.
  Results live 30 days, so sustained querying can exhaust the workspace
  allocation (`budget_exhausted` / `storage`) until expiry cleanup reclaims it.
- **Budget exhaustion is clean.** At every size, a result budget smaller than
  the prepared population failed as `budget_exhausted` / `storage`, with no
  published result, no unsettled delivery or invocation, and a discarded
  preparation.
- **Database growth** is measured separately from logical allocation. The
  workload was 24 results with their pages and reuses:

  | Observations | Logical allocation | PR-05 tables growth | Database growth | PR-05 tables after cleanup and VACUUM |
  | --- | --- | --- | --- | --- |
  | 50 | 17.6 MB | +9.5 MB | +13.2 MB | 1.8 MB |
  | 100 | 34.4 MB | +18.4 MB | +22.1 MB | 2.3 MB |
  | 200 | 68.1 MB | +32.6 MB | +36.1 MB | 2.7 MB |

  Physical growth was about half the logical charge, because of TOAST
  compression. Logical allocation is not a physical disk guarantee, and the
  512 MiB quota is a logical allocation, never a statement about disk usage.
  Rows deleted by cleanup keep their space until VACUUM. WAL and backups were
  not measured.
- **Not measured here:** owner source inspection through the existing raw-gated
  readers, which this cut leaves unchanged.

## Host interface

`PostgresAgentSqlResults(control_factory, reader_factory, authority_profile,
credential_sha256, workspace_id)` exposes `start_run()`, `handle(request_bytes)`
for `query` and `reuse_result`, `close_run(run_ref)` and `recover_abandoned()`.
`cleanup_expired()` and `cleanup_status()` are trusted-host/operator
operations, never agent wire actions.
Replies are the packet's closed envelopes in result-json-v1 bytes. Other action
kinds reply `unsupported_query` with their kind as the feature. Unsupported
query features (saved inputs, parents, candidate profiles, source scopes) reply
`unsupported_query` before any preparation.

`provision_query_reader` creates the restricted LOGIN with exactly the reviewed
closure: SELECT on the fourteen prepared views, the reviewed builtins and the
two invocation predicates. `reviewed_query_reader_profile` rebuilds the profile
from that specification at host start, and the executor independently
re-qualifies it before every invocation.

A least-privilege control login (not a superuser; an inheriting member of
`memoriesql_application`) additionally needs three operator provisioning steps,
found by the trusted-host lane:
- USAGE on schemas `memoriesql_query` and `memory_v1`, so the reviewed profile
  can resolve its names;
- `ALTER DEFAULT PRIVILEGES FOR ROLE <control> REVOKE EXECUTE ON FUNCTIONS FROM
  PUBLIC`, which the authority qualification requires of every creator role;
- the reader role granted to the control login `WITH INHERIT TRUE, SET FALSE`, so
  it can observe the reader backend's identity in `pg_stat_activity`. This is
  narrower than `pg_read_all_stats`.

**The executor holds both connection classes.** Credential isolation against the
agent's actual shell, filesystem and process capabilities is not qualified here
and gates any agent-facing deployment (packet §2).

Reply metadata sealed into the digest:

- **Wire frame.** Lifecycle fields are null unless a relation projection was
  used. Otherwise they are version 1 and the relation projector's own manifest
  hash. Per-source watermarks are not computed.
- **Coverage.** The query result is complete. Source capture, authorship,
  search readiness and discovery are all `unknown`, never claimed complete.
  Missing caller capabilities are listed as gaps.
- **`order_basis`.** Projected outer sort keys, including qualified keys that
  are exactly a projected column, then the sealed witness order.
- **`total_rows`.**

A row's `evidence_refs` are the observation, statement or source pins its typed
identifier values name in the result's own sealed population; nothing is looked
up live. Visibility splits them into new and previously delivered refs, and
lists source refs as hydration-required.

## Acceptance and preserved development failures

Fictional installed tests (`test_agent_sql_results`, 25 cases) use the
production reader provisioning path. They cover:

- query, page, cursor and reuse
- a paired agent citing a finding to its supporting units under its own grant:
  statement evidence refs, unit content SHA-256 and the pinned package, with
  raw authority refused and identity-package units cited without text
- the pinned normalized package text: authorized access to exactly the pinned
  projection with its labels; a raw (identity) neighbour cited without text;
  raw bytes, a foreign unit and a newer representation of the same occurrence
  never delivered; saved-result reuse refused after revocation
- hostile requests through `handle()` (malformed envelopes, unknown fields,
  oversized pages, offset times, catalog/physical/DML/multi-statement SQL,
  functions, settings and literals, forged pins and cursors): closed refusals,
  no result or rows, and no preparation or invocation for refused SQL
- expiry cleanup: run purge keeping live results reusable, result purge with a
  scan of every PR-05 table for the query text, parameter, digests and
  delivered refs, tombstone expiry, stranded-reservation reclamation, an
  interrupted pass leaving nothing partial, and a visible failure that refuses
  runs until cleanup recovers
- a paired agent (pairing plus access grant, never the owner) reading its own
  granted observation families, with relation tables disclosed as a
  raw-authority gap, revocation ending reuse, and a `memory.query`-only agent
  seeing a `source_read_required` gap instead of silent absence
- exact redelivery with no rerun
- a zero-row available result versus unavailable, and the distinct
  unsupported, invalid and idempotency outcomes
- source revocation refusing a whole aggregate, and regrant restoring it
- another principal refused
- resolved versus historical views over a real correction, with correction lineage
- run admission, expiry and no budget reset
- crash after commit, host recovery and owner close
- host death mid-query: capacity, close and admission stay blocked until the
  reader backend is confirmed gone; recovery then settles the orphaned
  invocation at the full reservation, and a new step is admitted without
  replaying the lost one
- owner loss after the reader settles and before commit: the delivery settles
  once as `abandoned` with no receipt, the refused commit discards the
  preparation, and the exact redelivery is `execution_error` with no rerun
- a pending run sorted ahead of a purgeable result, with a one-item pass: the
  result is still purged
- a row larger than the page's transport limit, on the first and on a later
  page: `budget_exhausted` / `transport`, never an empty page whose cursor
  points back at the same row
- a late commit that succeeds after the access was abandoned: the host replies
  `settlement_pending`, the committed result stays sealed, and the exact
  redelivery discloses that same result with no rerun
- group, window, set, EXISTS and relation-join shapes, with frame lifecycle
  fields and coverage gaps
- AM-5 (schema 39), a fresh paired agent holding `memory.query` and
  `source.read` but no raw authority. It reads a real assessed relation, created
  through activation, author, specialist, attestor and apply.
  - Its rows in the six readable tables equal the owner's.
  - Pair coverage and history give `relation_history`/`owner_only`.
  - No source-object identity appears in any column of the six tables.
  - The inspection reader stays owner-only.
  - After one member's grant is revoked, the relation is absent from a fresh
    step of the same run, with no gap or type pin. Re-disclosing the saved
    result is `unavailable`.
- AM-5 withholding: the whole relation is withheld from each of these callers,
  and the source endpoint stays readable where its scope is.
  - An agent that reads only the source endpoint's scope.
  - An agent with `memory.query` over the whole closure but no `source.read`,
    which gets `relation_tables`/`source_read_required`.
  - An agent whose remote grant has expired.
- AM-5 lifecycle: after a dispute and then a retraction, agent and owner see the
  same state, support eligibility and reason at each frame. That includes the
  disputed frame, read again after the retraction. The history counts stay
  owner-only.
- AM-5 tenancy: a paired agent of a second tenant reads no relation of the
  first, and the first tenant's credential opens nothing in the second.

Database-free contract tests pin the policy hash, the prepared set, the order
basis, evidence refs and reply sizing. A structural test holds migration 0039
to these properties:
- it grants nothing;
- it adds nothing to the query reader's schema;
- it keeps raw revisiting only on the owner branches;
- every earlier kernel name delegates in owner mode.

Development failures retained:

- Qualified ORDER BY keys first produced an empty order basis; the rule now maps
  exactly projected qualified columns.
- Two test expectations were wrong and were corrected, not the code:
  - `EXISTS (SELECT 1 …)` is correctly refused, because literals must be `$n`
    parameters.
  - A lost single-use preparation correctly fails instead of rerunning.
- Inventory hashes drifted during SQL edits.
- The trusted-host lane's owner-loss-before-commit case found that the exact
  redelivery of a step whose preparation had been discarded reported
  `unavailable`, contrary to this document. Redelivery now probes the caller's
  own step preparation first, and ownership-refused commits map to
  `execution_error`. A regression test reproduces the case in-process.
- The broad review found that a cleanup pass whose first due subjects were all
  pending purged nothing and stopped, starving purgeable subjects behind them.
  It also found that a row larger than the page's transport limit produced an
  empty page pointing back at itself: an endless loop on later pages, and an
  `execution_error` on the first. Both are fixed, and their regression tests
  fail on the previous code.
- An agent reading the installed catalog found its admission stage still said
  query/result execution was "not yet delivered", so it expected its queries
  to be refused. The stage and the SQL recall catalog note now say that query
  and reuse_result execute through the trusted executor. They also say that
  inspect, hydrate_source and checkpoints are not delivered, with no delivered
  or released claim. The logical catalog hash changes with the stage text. A
  unit test fails on the previous text.
- A mutation that disabled the agent-mode `source.read` gate of migration 0039
  changed no test outcome.
  - M0011's bead-version authorization already requires `source.read` on each
    closure bead's event, and scope grants cover both capabilities. The gate is
    therefore implied by existing checks in every configuration today's
    authority model can express.
  - It remains the explicit AM-5 substitution point. It also checks that the
    event and the source object belong to the caller's tenant and workspace, so a
    mis-threaded gate withholds rather than authorizes.
- The first draft of the shared relation fixture paired agents over the
  owner-private default scope. That scope admits no grants to other principals,
  so the fixture now creates explicit scopes.
- The trusted-host lane's late-commit case found that a successful commit
  followed by a refused first disclosure deleted the committed result through
  the generic failure path, and replied `unavailable`. That disclosure was
  refused because recovery had already settled the access. Committed results
  now survive failed disclosures. A settled access is reported distinctly
  (`query_delivery_settled`) and replies `settlement_pending`.
- The hostile-request test found that refused SQL still reserved, then
  discarded, a preparation. SQL is now screened before any reservation. Only
  the population-dependent reference-anchor check waits for full admission.
- **Open finding:** `NOT (text = $1)` is rewritten by the planner to `<>`,
  whose `textne` lies outside the reviewed builtin closure. Such queries fail as
  `unavailable` rather than `unsupported_query`.
- Cleanup initially failed with `permission denied for function uuid_eq`: M0033
  had revoked builtins from the staged-row owner. It was fixed with the narrow
  grant above.
- **Paired agents saw zero observation rows** (PR-06 repro). The first
  observation families reused PR-03's provenance records, which require
  `source.raw.read`, a capability the `paired_agent` role can never hold. The
  regression test fails on that code and passes on the query-level records.

Exact-head installed 3.13/3.14 qualification and CI belong on the PR.

## Before any release

The owner's Decision 1 is recorded in `../approvals/pr-05-preview-amendments.md`.
It allows only a labelled preview with these terms:

- **Measured range.** The preview range is whatever the small-workspace
  qualification measures, never an extrapolation.
- **Logical accounting.** Allocation accounting is temporarily logical, with a
  before/after database-growth measurement. The 512 MiB quota is a logical
  allocation, never a statement about disk usage.
- **Erasure wording.** Every preview description states, verbatim:

  > Governed erasure is unavailable in this preview. Revocation prevents
  > subsequent authorized disclosure; it does not delete retained data or recall
  > previously delivered copies. Regrant may restore access while the result
  > remains valid. Expired derived-result content is removed through qualified
  > expiry cleanup. Canonical source deletion and immediate erasure are not
  > provided by this release.

Expired-result deletion is required work, not an exception. It is implemented
in this cut (see Schema 38) and qualifies with the exact-head run.

Still gating any preview release:

- keeping any preview within the measured range above (up to 200
  observations);
- exact-head qualification of expiry cleanup, and a host schedule for
  `cleanup_expired()`;
- integrated adversarial acceptance, including credential isolation against the
  agent's actual shell, filesystem and process access;
- **agent-identity access.** It is not available until the combined end-to-end
  proof passes: capture, then authoring, then the agent's own query through the
  trusted host, then the agent's source inspection. The paired-agent regression
  tests above prove only the population fix.
- the Desktop lane's trust boundary;
- the credential and transport posture of any agent-facing deployment.

Decision 1 implies no security waiver, no full-acceptance claim, no release
authorization and no owner-credential fallback for agents. No release, version,
tag, publication, provider call, owner data, evaluation run or Desktop
consumption is authorized by this slice.
