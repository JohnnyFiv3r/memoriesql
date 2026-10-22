# Trusted query host (`memoriesql.infrastructure.results_broker`)

Refresh source on immutable Core119: no installed/native/host qualification is
claimed for this composition. Historical donor evidence retains its original
head/scope; Core85 run39518702328 does not qualify these added interfaces.

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
| Provisioning | Rotation replaces both passwords, and the old ones stop working. New secrets stay durable if pinning fails after the roles change. A NOINHERIT membership, superuser, missing reader inheritance, default PUBLIC EXECUTE or drifted pin is refused with an operator message. `broker check` reports each condition. A connection lost while the role changes commit never discards their new secrets. Before any role change the run records its transaction's ID beside the staged file. After a lost or refused COMMIT it checks the stored verifiers itself. A match finishes the run. A mismatch reports that nothing changed only once PostgreSQL reports that transaction ended (`pg_xact_status`), so a commit still in flight is never read as a rollback. A run that cannot decide keeps the staged file and its record and reports `provisioning_outcome_unknown`; the next run settles that file first. The record is removed only after the file is live. A kept file without a record becomes the configuration only when the stored verifiers match it, and is never removed automatically. A connection lost before COMMIT was sent discards the staged file at once. Pinning refusals and the host preflight name their cause and the login that failed, never server text. |
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

The installed selector now requires exactly 17 named cases in
`query_host_acceptance.py`: all 16 existing host cases plus
`test_public_cli_query_and_reuse_cross_real_socket_with_current_authority`.
That new, still-unrun native case starts the real host and fresh public CLI
processes with only their own paired credential, workspace, state directory and
Unix socket. It checks query, persisted-run cursor reuse, digest refusal, current
pairing revocation, no extra query invocation and owned host shutdown. It injects
no transport and gives the CLI no database login. `query_host_fault.py` supplies
fault hooks for existing cases; it contains no acceptance cases. The five default
CLI runtime cases using an injected transport do not substitute for this route.
The helper rejects an omitted CLI case, any other missing case, duplicates,
extra cases, skips, failures or a zero selection. CPU stand-ins check only this
selection/error propagation, not native transport or semantic correctness.

### Prepared two-identity fixture: execution held

The public CLI's default peer check requires a different service owner. The
same-UID harness cannot qualify that route. `query_host_cli_identity.py` prepares
a fixed test worker and cleanup controller; neither run-as operation is approved
by this source change. No local UID probe or privileged execution has occurred.

Before execution, the owner and Root must approve and review the exact external
binding, source and launcher. The S-owned, C-nonwritable JSON binding is supplied
through `N1_TEST_CLI_IDENTITY_BINDING` and its SHA-256 environment variable. It
pins `source_sha`, `authority_sha256`, distinct nonroot
`service_uid`/`client_uid`, the service `gid`, permitted supplementary `groups`,
system `sudo_sha256`, `env_sha256` and `setpriv_sha256`, every executable/helper
input hash, and the installed interpreter's complete `import_roots`. The only
executable placeholders are `@installed-python` and `@copied-helper`; `root` must
be `@fresh-fixture`. `N1_TEST_CLI_IDENTITY_AUTHORITY_SHA256` must independently
match that approved record. Missing bindings fail before a process launch. Actual
hosted image, account availability, ownership, import closure and successful
cleanup remain unverified prerequisites, not evidence established by a JSON field.

`import_roots` binds the complete ordered, absolute import search list, including
nonexistent zip or directory candidates. The check covers each root itself,
its replacement ancestors and descendants, and the actual invoked interpreter's
lexical symlink path and targets. Client-owned entries are refused even when
currently read-only. Sticky protection applies only to an existing non-client
child whose parent prevents replacement; it cannot protect a missing search
entry from creation. Authority pins must each independently be present and be
matching lowercase 64-hex SHA-256 values; matching nulls are refused. These
metadata checks still do not confer authority.

The controller permits at most two fixed invocations. Each is `sudo -n --
/usr/bin/setpriv --reuid=C --regid=G --clear-groups --`, then an empty
environment, the bound installed Python and copied helper, and `run-case` or
`cleanup` with the bound manifest. sudo runs only the bound `setpriv` as root;
`setpriv` drops to C:G with no supplementary groups before the interpreter starts.
No group- or capability-keeping option is passed. This uses the hosted runner's
existing run-as-users rule: that rule refuses sudo's own `-g`, and sudo's `-u C -g
G` would add C's own groups, which the identity check rejects. `sudo`, `env` and
`setpriv` must each be root-owned, not group- or world-writable, and match the
binding, or the controller refuses before launch. The launcher's stderr is a
separate pipe from its protocol frames. Any stderr byte from sudo, `setpriv` or
the worker, at any point, latches the fixed `LauncherStderr` code; the text is
discarded, never parsed or kept. A failing worker may send one terminal frame
naming a code from the fixed `WORKER_FAILURE_CODES` list (its own literal failure
codes); the controller records only such a listed code, maps any other frame to
`worker_unlisted_failure`, never copies worker text, and still latches a
containment failure. A CLI child never reports. It sends the paired credential
only over a pipe after registration; the CLI receives no database login. The
parent's existing normal deadline and 120-second cleanup deadline are inherited,
never reset. CLI output, protocol frames, state identity and PID/start-time
registrations are bounded and checked. An unknown birth, lineage, monitor or
cleanup latches a private S-owned failure record and stops later tests/proofs.
Only bounded known-resource cleanup and evidence retention may follow. Disposable
hosted VM destruction is a failed-job fallback, never successful cleanup proof.

The driver sets umask 022 before creating its test root, venv and copied inputs,
because the run-as client shares the job's group. Right after installation, before
the default acceptance, it refuses with a fixed code any interpreter, copied
helper or import-root path, ancestor or link target that is client-owned or
client-writable (world-writable, or group-writable by the shared group, other than
a sticky non-client directory over a non-client child), and any symlink inside an
import root. The worker's own check stays authoritative.

Stop reception and acknowledgement use the original shared cleanup deadline,
including when the normal deadline has expired; new CLI work retains the normal
deadline. CLI stdout and stderr are drained separately under the same existing
output/deadline bounds. Any stderr, including whitespace, or paired credential
in raw or decoded JSON stdout fails before forwarding a reply, with fixed
content-free diagnostics.

The inert unit controls exercise the actual identity, registration, protocol and
runner methods with mocks. They do not establish OS isolation, native query
semantics, permissions, reaping, timing fit or privilege approval. Success still
requires all 17 host cases, the complete default suite and all four restart
triplets on both supported interpreters at the actual successor head.
