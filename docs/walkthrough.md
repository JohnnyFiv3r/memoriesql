# Technical walkthrough

This is a short route through the main design decisions in the memoriesQL open
core: why each was made, where it is implemented, which tests exercise it, and
what that evidence does and does not establish. Source links are pinned to
`main` at
[`84d6965`](https://github.com/JohnnyFiv3r/memoriesql/commit/84d696504db12c7849dc06f5c9d81d049c127d91),
the revision qualified by hosted run
[41306333223](https://github.com/JohnnyFiv3r/memoriesql/actions/runs/41306333223).
The linked topic documents hold the detailed contracts.

## The problem

An agent's memory should preserve what was observed, distinguish it from an
interpretation, and keep an inspectable history when that interpretation
changes.

A memory that keeps only a summary or an embedding, and overwrites it as
understanding improves, loses what a later reader needs: the evidence behind a
statement, the qualifications it carried, such as "proposed" or "not yet done",
and the record of how an interpretation changed. memoriesQL keeps all three in
PostgreSQL as the single canonical store, with no separate vector or graph
database.

## The architecture in one pass

1. **Capture first.** Authorized source bytes are retained durably before any
   parsing or model work. Unknown structure never blocks retention.
2. **Author against evidence.** A typed task gives an agent the retained
   evidence. It returns statements that keep their qualifications, a short
   title and summary, and local entity mentions.
3. **Accept canonically.** PostgreSQL checks authorization, the worker's attempt
   fence and the evidence the author was actually shown, then stores the
   result. Accepted meaning is immutable from then on.
4. **Correct by adding.** A correction is a new observation that names the
   versions it supersedes and why. Branches are allowed; reconciliation is a
   further, explicit correction.
5. **Read under current authority.** Inspection and evidence reads establish
   authorization afresh on every call and never trigger authoring or model work.
6. **Relate and investigate.** Relationships use a versioned vocabulary and are
   accepted only after a separate assessment. In development source, agents
   compose SQL over an admitted logical schema.

## Two worked examples

Both examples are PostgreSQL integration tests over fictional data. The
fixtures supply the interpretations, with scripted outputs standing in for a
model, so the tests check preservation, persistence and access. Whether a model
reliably authors these interpretations is a separate milestone.

### Inspect an observation, read its evidence, then lose access

[`test_installed_public_cli_reads_and_revocation`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_stored_bead_inspection.py#L107-L177)
accepts a fictional observation whose statement reads:

> Alex proposed a count; no count has occurred.

The stored statement keeps a proposed action distinct from a completed one. The
test then drives the development CLI; these commands are not in the published
0.0.12 package.

| Step | Invocation | Asserted outcome |
| --- | --- | --- |
| Inspect | `memoriesql inspect <bead> --json` | `available`, lifecycle `accepted`, the statement above |
| Read evidence | `memoriesql source <bead> --selection-file <file> --json` | One bounded page: the first eight characters, `Alex pro` |
| Read relationships | `memoriesql relations <bead> --json` | `available` |
| Unrecognized credential | `memoriesql inspect <bead> --json` | Exit status 2, `unavailable`, no observation returned |
| Source revoked | `memoriesql inspect <bead> --json` | The same reply as for a random, nonexistent ID |

The test revokes the source directly in the database, as an administrator
would. A neighboring test,
[`test_read_does_not_add_tasks_meaning_or_usage`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_stored_bead_inspection.py#L377-L405),
inspects and reads evidence twice, then asserts that no semantic task, task run,
observation version, statement, entity mention, model-usage event or dispatch
receipt was added. Reading memory is a separate operation from authoring it.

### Correct an interpretation without erasing history

[`test_initial_replay_same_evidence_branches_and_reconciliation`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_immutable_observations.py#L355-L459):

- An initial observation records "Four fictional trees." Replaying the same
  result returns the original receipt instead of writing again.
- Two corrections are authorized against that same accepted version: "Three
  fictional trees." and "Five fictional trees." Each is a new observation whose
  receipt names the original as superseded; neither invalidates the other.
- A third correction, "Four fictional trees, with the two branch counts
  reconciled.", explicitly supersedes both branches.
- Accepting the same source event again, even under a new idempotency key,
  returns the original observation's identity.
- The original version still reads "Four fictional trees.", and four
  supersession links record the lineage.

Conflicting interpretations remain attributable records. Reconciliation is an
explicit operation, not an accident of write order.

The invariant does not rest on the Python types.
[`test_accepted_semantics_reject_appends_and_legacy_revision`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_immutable_observations.py#L628-L738)
writes with plain SQL, bypassing the typed command. PostgreSQL still refuses
new meaning for an accepted observation (`accepted_bead_immutable`), in-place
updates to its type, render or statement text, and a correction observation
that lacks accepted targets.

## Three source stops

### 1. The stored-inspection test

[`tests/runtime/test_stored_bead_inspection.py`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_stored_bead_inspection.py#L107-L177)

- **Decision:** accepted meaning, its evidence and current access are separate,
  inspectable concerns.
- **What the check shows:** the qualification survives; evidence comes back in
  bounded pages; refused and missing reads are indistinguishable.
- **What it does not show:** that a model authors the qualification correctly,
  or how the system behaves on real data.
- **If it failed, look at:** the adapter below, then the SQL readers
  `inspect_stored_bead_v1` and `read_stored_bead_evidence_v1`, introduced in
  migration 0024.

### 2. The inspection adapter

[`PostgresStoredBeadInspection`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/src/memoriesql/infrastructure/postgres/stored_bead_inspection.py#L26-L82)

- **Decision:** every read establishes authority afresh and is bounded.
- **What the code does:** each call runs in its own short transaction with a
  statement timeout and a 500 ms lock timeout, switches to the
  `memoriesql_application` role, derives its authorization context from the
  caller's credential, refuses a response over 256 KiB, and maps permission
  refusals and timeouts to typed `unavailable` or `budget_exhausted` outcomes.
- **Checked by:** stop 1 and
  [`test_revocation_and_missing_are_indistinguishable`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_stored_bead_inspection.py#L219-L232).
- **If it failed, look at:** `PostgresAuthorizationPort.begin_context` and the
  bounded retry in `transient_retry.run_owned`.

### 3. The immutable-observation tests

[`tests/runtime/test_immutable_observations.py`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/tests/runtime/test_immutable_observations.py#L355-L459)

- **Decision:** corrections add lineage; accepted meaning never changes.
- **What the checks show:** replay, coexisting branches, explicit
  reconciliation and the direct-SQL refusals described above.
- **What they do not show:** statement-level correction (a correction
  supersedes whole observations), or correction of observations authored
  through complete-input execution, which is not yet delivered.
- **If they failed, look at:** the path from
  [`CorrectObservationCommand`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/src/memoriesql/application/observation_commands.py#L207-L211)
  through
  [`PostgresCanonicalTransactions.correct_observation`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/src/memoriesql/infrastructure/postgres/canonical_transactions.py#L123-L137)
  to the SQL function
  [`correct_observation_v2`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/migrations/0015_immutable_observation_commands.sql#L2328-L2352)
  and the trigger function
  [`guard_accepted_bead_semantics()`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/migrations/0015_immutable_observation_commands.sql#L54-L103),
  which later migrations extend to entity mentions (0022) and claims (0027).

## Design decisions and their costs

| Decision | Why | Cost or difficulty |
| --- | --- | --- |
| Preserve accepted meaning and represent corrections explicitly | Keep history and changing interpretations inspectable | More lifecycle state, branching and storage |
| Separate evidence from authored meaning | Let an application inspect support and attribution | Evidence retention and disclosure need their own controls |
| Keep PostgreSQL as the one canonical store and enforce key invariants there | Evidence, meaning, receipts and lifecycle share one transactional authority, so invariants such as immutable accepted meaning hold even when the Python layer is bypassed | Much of the kernel is SQL, and every change ships as a forward migration |
| Use a constrained, versioned relationship vocabulary | Make relationships consistently interpretable across runs | Definitions, direction, coverage and qualifications need governance |
| Let agents compose SQL within an admitted surface | Support investigations and reusable intermediate results | Composed queries complicate authorization and dependency tracking |
| Recheck authority when reading and reusing results | Keep saved results subject to current access | Previously saved results can become unavailable |

## Relationships: meaning, navigation and support stay separate

The built-in vocabulary has eleven predicates with versioned definitions, from
`supports` and `contradicts` to `caused_by`, `led_to`, `enables` and
`supersedes`.
[`relation_profile.py`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/src/memoriesql/application/relation_profile.py#L1-L13)
separates three layers:

- the **predicate** carries the relationship's meaning, at a pinned definition
  revision;
- the **family**, from Mark Burgess's Semantic Spacetime (NEAR, LEADSTO,
  CONTAINS, EXPRESS), organizes navigation and presentation only. It is
  versioned separately, so a mapping change never becomes a change of meaning,
  and some predicates, such as `supersedes`, stay deliberately unmapped;
- the **basis** belongs to each assertion: stated by the source or inferred by
  the agent.

In the assessment path, an author proposes relationships between statements of
already accepted observations in a separate task, and a proposal is accepted
only when a separate specialist judges that exact assertion consistent. A
relation task never changes an observation. See
[relation assessment](relation-assessment.md).

## Agent-composed SQL: the current hard problem

The hard part is letting agents compose useful investigations while preserving
evidence and authorization through the resulting data.

In development source,
[`admit_query`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/src/memoriesql/application/agent_sql_admission.py#L1547)
parses a single SELECT with SQLGlot's PostgreSQL dialect and resolves every
relation, column and function against a closed logical catalog; physical tables
and system catalogs are not admitted.
[`PostgresAgentSqlResults`](https://github.com/JohnnyFiv3r/memoriesql/blob/84d696504db12c7849dc06f5c9d81d049c127d91/src/memoriesql/infrastructure/postgres/agent_sql_results.py#L395)
is the trusted executor. It runs the admitted query under a separately
restricted database login and returns an immutable result. Every later page or
reuse rechecks authority over that result's whole dependency closure, and
losing any dependency refuses the whole result rather than silently dropping
rows.

The [preview](verification/pr-05-public-results.md) delivers `query` and
`reuse_result`, measured only up to 200 fictional observations on one declared
machine. Each query prepares the whole authorized population, so cost grows
with memory size, and the restricted reader does not yet run text-pattern or
time-range predicates.
Saved-input refinement, named saves and governed erasure are not available, and
agent-facing use requires qualified credential isolation; see the
[trusted query host](trusted-query-host.md).

## Current status

Two dimensions are kept apart. **Delivery**: in the published package
(0.0.12), in development source on `main`, or planned. **Evidence**: specified,
implemented, tested at an identified revision, or shown to improve semantic or
product outcomes. "Tested at `84d6965`" means the hosted run above installed the
reviewed package build on Python 3.13 and 3.14 against PostgreSQL 18.4 and ran
every discovered test, with fictional data and no skips.

| Capability | Delivery | Evidence |
| --- | --- | --- |
| Immutable observations and explicit correction lineage | Published | Tested at `84d6965` |
| Stored-memory inspection and evidence reads | Published | Tested at `84d6965` |
| CLI `inspect`, `source` and `relations` | Development source | Tested at `84d6965` |
| Relationship vocabulary, family profile and assessment after acceptance | Published | Tested at `84d6965` with scripted author and specialist outputs; relationship quality not yet evaluated |
| Confirm, dispute and retract for assessed relationships | Development source | Tested at `84d6965` |
| Agent-composed SQL (`query`, `reuse_result`) | Development source | Tested at `84d6965`; measured to 200 observations; not qualified for agent-facing deployment |
| Automatic investigation checkpoints | Development source | Tested at `84d6965`; the [verification record](verification/pr-05-automatic-checkpoints.md) still lists distinct-identity host, privacy/resource and review qualification as pending |
| Recall that beats a competent baseline | Planned | Not evaluated |

No capability has yet been shown to improve semantic or product outcomes. The
milestones below are how that gets tested.

## Next milestones

| Milestone | Question to resolve | Evidence that would close it |
| --- | --- | --- |
| Qualify composed memory | Does the integrated capture, authorship, acceptance and inspection path preserve identity, qualifications and the supported correction lifecycle? | An attributable integration run and independent checks of the relevant semantic and lifecycle cases |
| Reliable investigations | Do supported queries and saved-result operations preserve authority and evidence dependencies? | Executed integration checks covering the supported operators, revocation and restart, at realistic memory sizes |
| Useful recall | Does this structure improve the user's task under a fair comparison? | Held-out tasks against a competent hybrid baseline with matched evidence, model and budgets |
| Useful maintenance | Does background exploration or learned navigation improve later recall? | Ablations that include maintenance cost, contradictory evidence and cases where the graph gives little help |

## Further reading

- [Immutable observations](immutable-observations.md) and
  [stored-memory inspection](stored-bead-inspection.md)
- [Relation assessment](relation-assessment.md), including assessed-relation
  lifecycle
- [Agent SQL interface](agent-sql-results-v1.md) and its
  [preview verification](verification/pr-05-public-results.md)
- [CLI](cli.md), [migrations](migrations.md) and the
  [repository boundary](architecture/repository-boundary.md)
