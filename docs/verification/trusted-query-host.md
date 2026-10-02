# Trusted query host (`memoriesql.infrastructure.results_broker`)

Primary acceptance claim: **the PR-05 executor can be hosted in one separate
process that alone holds both connection classes, and a paired agent reaches it
only through a peer-authenticated local socket with its own credential, receiving
the executor's reply bytes unchanged; losing the host or its owner session never
frees run or workspace capacity while database work is still executing.** This
is the host mechanism and its fictional proof. Whether a particular installation
and agent harness are isolated is a separate, per-installation qualification
(see [the operator guide](../trusted-query-host.md)); this document does not
claim one.

Base: PR-05's `claude/pr-05-public-results` at `33e1c42` (schema 38). The host
relies on its owned expiry cleanup, on committed results being kept when their
first disclosure fails, and on the refusal of result pages that cannot fit. No
migration, contract payload or dependency is added. The CLI binding
(`memoriesql broker …` and client mode) is PR-06's, in the unpublished, amended
`memoriesql.core-cli.v1` record.

## What is proved, and how

Database-free unit tests (`tests/unit/test_results_broker.py`) prove framing,
header closure, the client's impostor-socket refusal (a socket in a directory of
the caller's own uid is never even connected to; a kernel peer mismatch sends no
byte), peer-uid refusal before any authentication or work, byte-exact
pass-through, the 0600/0700 configuration discipline, the run registry, the
SCRAM-SHA-256 verifier shape and the expiry-cleanup schedule.

The installed acceptance (`tests/runtime/query_host_acceptance.py`, run by
`scripts/prove_query_host.py` because it needs Unix sockets and separate host
processes, which the confined acceptance runner denies) uses the production
provisioning path and a canonically paired fictional agent:

| Case | Result required |
| --- | --- |
| Replies | Host replies are exactly the executor's bytes; `inspect`/`relations` are byte-identical to the direct reader path. |
| Refusals | Unknown, owner (human), revoked, expired and other-workspace credentials, and too-short secrets, all get identical `unavailable` bytes, and none reaches the executor. |
| Request content | A sentinel in SQL text, a parameter value or a reader request never appears in any reply or in the host log; neither does the agent's secret or its digest. Each request's log line carries the reply's run and access-receipt identifiers, so it correlates with the executor's receipts; only canonical identifiers are logged, never reply content. |
| Unfittable page | A result page whose row cannot fit its transport limit is refused (`budget_exhausted`, `transport`) on first and later pages alike, never returned empty. |
| Other uid | A peer that is not the configured client uid is refused before any database work. |
| Admission bypass | Owner and helper kinds, DML, `set_config`, file functions and canonical tables are refused. A raw reader login cannot write, `SET ROLE` or create temporary objects; its `USERSET` change remains the packet's stated limitation. The control login cannot create roles or bypass RLS. |
| Operator close | Refused while unsettled; succeeds after recovery; unknown runs are refused. Listings print no credential digest. |
| Provisioning | Rotation replaces both passwords, and the old ones stop working. New secrets stay durable if pinning fails after the roles change. A NOINHERIT membership, superuser, missing reader inheritance, default PUBLIC EXECUTE or drifted pin is refused with an operator message. `broker check` reports each condition. A connection lost while the role changes commit never discards their new secrets. Before any role change the run records its transaction's ID beside the staged file. After a lost or refused COMMIT it checks the stored verifiers itself. A match finishes the run. A mismatch reports that nothing changed only once PostgreSQL reports that transaction ended (`pg_xact_status`), so a commit still in flight is never read as a rollback. A run that cannot decide keeps the staged file and its record and reports `provisioning_outcome_unknown`; the next run settles that file first. A connection lost before COMMIT was sent discards the staged file at once. Pinning refusals and the host preflight name their cause and the login that failed, never server text. |
| Restart | After a draining stop and restart, exact redelivery returns the same result pin without a second invocation. |
| Host death | The host is SIGKILLed while its reader backend is frozen inside the admitted SELECT. Until that backend ends, capacity is held: no recovery, closing refused, new steps refused, exact redelivery pending. Afterwards recovery charges at least the reservation; new work proceeds without replay; the lost step is never rerun. |
| Owner session loss | Terminating only the owner session while the reader is frozen holds capacity the same way; the delivery later settles exactly once. |
| Owner loss before commit | After the reader has settled, the owner session is lost and recovery takes the delivery over. The late commit succeeds, but the host reply is `settlement_pending` (`settlement`) with no result or page. The delivery settles once as abandoned, charged at least its reservation, with no disclosure receipt, and no second invocation occurs. The committed result stays sealed: the exact redelivery returns that same result with a new access receipt, without rerunning the SELECT. |
| Expiry cleanup | The host runs cleanup at start and on schedule; `broker cleanup` runs it now; `broker check` fails on a stale or failed cleanup. |

## Findings recorded during development

- **Least-privilege control login.** PR-05's own tests use a superuser control
  login. The host deliberately does not. A non-superuser INHERIT member of
  `memoriesql_application` additionally needs:
  - query-schema USAGE, to re-derive the reviewed reader profile by name;
  - no default PUBLIC EXECUTE on its own future functions, which the profile
    qualification requires;
  - visibility of its own reader backends in `pg_stat_activity`, to settle them.
  `broker provision` grants the reader role to the control login `WITH INHERIT
  TRUE, SET FALSE`, which covers the first and third, and revokes the default
  PUBLIC EXECUTE. The serve preflight names any missing piece. Schema 33 revokes
  PUBLIC EXECUTE on builtins (even operators), so a non-inheriting login fails
  its very first statement; the preflight maps that to the same message.
- **Owner-only readers.** The existing stored-bead inspection, source and
  relations readers authorize source revisiting, which requires raw-source
  authority a paired agent cannot hold. Through the host they return the same
  `unavailable`. This is the owner's chosen boundary: agents cite through their
  own receipted query results.
- **Committed results survive a failed first disclosure.** The owner-loss-before-commit
  case found that a committed result whose first disclosure failed was discarded,
  so its exact redelivery could only report `execution_error`. PR-05 fixed this
  in `92f7ccf`: the result stays sealed and the redelivery discloses it under
  current authority. The case asserts the corrected behavior.
- **Recovery on every admission.** Recovering once per host process could leave
  work stranded by an earlier host death blocking a workspace until the next
  restart. The host now runs PR-05's recovery before every run or query admission.
- Development failures corrected in the tests, not the code:
  - a pinned-peer test expected a connection that the client correctly refused
    earlier;
  - a positive control used a platform binary whose environment macOS hides
    from `ps`.

## Installation and harness qualification

Pre-installation evidence (confinement checks with Anthropic's standalone
sandbox runtime, and one approved run of the exact agent harness) is kept with
the installation record, outside this repository. It is not a qualification of
any installation: the operator guide's installation checks and harness
confinement checks must pass on the actual installation before agent query
access is enabled there.

## Honest limits

See the operator guide. In short, the agent still runs as the owner's OS user,
so the guarantee holds only while the owner's environment keeps no other copy of
the host's secrets or the owner's credential, the harness is configured and
qualified as documented, and the host's code, configuration and socket
directory stay unmodifiable by that user. Those conditions are checked, not
assumed.
