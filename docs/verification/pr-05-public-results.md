# PR-05 standalone query and result reuse (preview cut)

Primary acceptance claim: **an authorized principal, through the trusted
executor, can run admitted SELECTs over fourteen prepared logical relations,
receive an immutable result pin, page it with disclosure receipts and
owner-bound cursors, and re-read it later without repeating discovery; every
disclosure first re-checks current authority over the whole saved dependency
closure.** This is the smallest useful public query/result path, not complete
PR-05 availability. Checkpoints, saved-input refinement, inspection/hydration
through this contract, erasure/cleanup and measured W1–W7 fit remain pending.

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
repeatable-read frame. It adds no second authorization evaluator: each family is
admitted only if `relation_bead_records_v1` authorizes the bead, its accepted
version, statements, statement evidence and source units, **and every correction
neighbour visible at the frame**. Otherwise the whole family is withheld, so no
successor count, branch or older-as-current presentation escapes. Their
protected records join one dependency manifest.

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

**Extension outside the approved packet (owner decision #11):**
`close_run(run_ref)` is a trusted-host, owner-only early close, never an agent
wire action. It is refused while any of the run's deliveries is unsettled,
refunds nothing, keeps charges in the rolling window and cannot be reopened.
"Active" means neither expired nor closed; the two-run cap and 30-minute
maximum are unchanged.

## Host interface

`PostgresAgentSqlResults(control_factory, reader_factory, authority_profile,
credential_sha256, workspace_id)` exposes `start_run()`, `handle(request_bytes)`
for `query` and `reuse_result`, `close_run(run_ref)` and `recover_abandoned()`.
Replies are the packet's closed envelopes in result-json-v1 bytes. Other action
kinds reply `unsupported_query` with their kind as the feature. Unsupported
query features (saved inputs, parents, candidate profiles, source scopes) reply
`unsupported_query` before any preparation.

`provision_query_reader` creates the restricted LOGIN with exactly the reviewed
closure: SELECT on the fourteen prepared views, the reviewed builtins and the
two invocation predicates. `reviewed_query_reader_profile` rebuilds the profile
from that specification at host start, and the executor independently
re-qualifies it before every invocation.

**The executor holds both connection classes.** Credential isolation against the
agent's actual shell, filesystem and process capabilities is not qualified here
and gates any agent-facing deployment (packet §2).

Reply metadata sealed into the digest:

- **Wire frame.** Lifecycle fields are null unless a relation projection was
  used. Otherwise they are version 1 and the relation projector's own manifest
  hash. Per-source watermarks are not computed.
- **Coverage.** The query result is complete. Source capture, authorship,
  search readiness and discovery are all `unknown`, never claimed complete.
- **`order_basis`.** Projected outer sort keys, including qualified keys that
  are exactly a projected column, then the sealed witness order.
- **`total_rows`.**

A row's `evidence_refs` are the observation, statement or source pins its typed
identifier values name in the result's own sealed population; nothing is looked
up live. Visibility splits them into new and previously delivered refs, and
lists source refs as hydration-required.

## Acceptance and preserved development failures

Fictional installed tests (`test_agent_sql_results`, 10 cases) use the
production reader provisioning path. They cover:

- query, page, cursor and reuse
- exact redelivery with no rerun
- a zero-row available result versus unavailable, and the distinct
  unsupported, invalid and idempotency outcomes
- source revocation refusing a whole aggregate, and regrant restoring it
- another principal refused
- resolved versus historical views over a real correction, with correction lineage
- run admission, expiry and no budget reset
- crash after commit, host recovery and owner close
- group, window, set, EXISTS and relation-join shapes, with frame lifecycle
  fields and coverage gaps

Database-free contract tests pin the policy hash, the prepared set, the order
basis, evidence refs and reply sizing.

Development failures retained:

- Qualified ORDER BY keys first produced an empty order basis; the rule now maps
  exactly projected qualified columns.
- Two test expectations were wrong and were corrected, not the code:
  - `EXISTS (SELECT 1 …)` is correctly refused, because literals must be `$n`
    parameters.
  - A lost single-use preparation correctly fails instead of rerunning.
- Inventory hashes drifted during SQL edits.

Exact-head installed 3.13/3.14 qualification and CI belong on the PR.

## Before any release: owner approvals required

Releasing this cut as a labelled preview needs John's explicit approval of:

1. **Availability gating.** Delivery before the packet's availability gates:
   - W1–W7 measured fit;
   - a physical storage profile;
   - integrated adversarial acceptance, including credential isolation;
   - checkpoints;
   - cleanup and erasure.

   Interface shapes and numeric policy are unchanged.
2. **No physical purge yet.** Result bytes are not purged. Access ends at 30
   days or on authority loss, but the 24-hour purge commitment is not met.
   Allocations accumulate toward the 512 MiB workspace quota.
3. **Credential posture.** The credential and transport posture of any
   agent-facing deployment.

No release, version, tag, publication, provider call, owner data, evaluation run
or Desktop consumption is authorized by this slice.
