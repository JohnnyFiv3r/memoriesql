# Proposed settings enforcement amendment — not approved

Native PostgreSQL 18.4 demonstrated that a real restricted LOGIN can execute
`SET work_mem='8MB'` after `REVOKE SET ON PARAMETER work_mem FROM PUBLIC`.
USERSET settings cannot be made immutable solely by those PostgreSQL ACLs.
Callable `set_config` and write access to `pg_settings` can be revoked, and the
independent privilege tests enforce those revocations; direct SET remains.
[PostgreSQL SET](https://www.postgresql.org/docs/18/sql-set.html).

The approved packet's §5 query-settings row says “restricted login cannot change
them.” That literal guarantee is not met. No approved blob or record has been
modified. Dependent execution remains gated.

Recommended exact replacement for that sentence:

> The agent cannot change these settings through the retrieval interfaces.
> The sole trusted executor owns the restricted connections, accepts only the
> closed admitted SELECT tree and no session-setting requests, revokes executable
> `set_config` and writes to `pg_settings`, and checks the configured settings
> before dispatch. Agents receive neither credentials nor a general SQL client.
> PostgreSQL USERSET parameters remain changeable by a holder of raw login
> credentials; database ACLs do not make them immutable. A compromised raw login
> is outside this settings guarantee. The independent supervisor enforces the
> owned-operation deadline and cancellation/settlement regardless of client-side
> statement-timeout settings. No hard CPU, I/O, examined-row or total-memory
> guarantee follows. Independently enforced database privileges still prevent
> canonical writes, elevation and scope widening in admission-bypass tests.

The numerical policy, admitted composable SQL, provenance obligations, retention,
current authority, erasure, work/storage settlement, workload-fit requirements
and both evaluation conditions remain unchanged. This changes the claimed
settings security boundary and therefore requires explicit approval; it is not
an incidental implementation choice or an accepted fallback.

The material alternative is a separately qualified server-side settings fence
covering both direct SET and callable equivalents for this LOGIN. Its closure,
installed artifact compatibility and native adversarial proof must pass before
making the original stronger claim. No unqualified extension is proposed here.

After owner selection, reconcile the chosen language into one new stable packet
commit, with the whole unchanged relation-assessment blob at that same commit.
Return the complete version-2 InterfacePin and amendments record that supersedes
the approved original pin. Obtain exact owner approval and a matching content-free
attestation from the existing custodian before dependent executor implementation.
The custodian determines custody reconciliation; the implementer never receives
questions, gold, hints or access paths. Current ea-2 attests only the original
approved record and cannot silently authorize the amendment.
