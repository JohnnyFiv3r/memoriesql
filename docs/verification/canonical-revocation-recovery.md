# Canonical recovery across credential revocation

Primary acceptance: an owned canonical-refusal recovery reports loss of its
authorization context without pretending that a task settled or a fence was
checked. A context opened before revocation still follows the existing later
authority/fence checks. The owner authorized this separate public-core repair
after #56's retained installed qualification failure.

Base is merged main `522a68a362456ad11f7f82a1a21ce5664aaefdfb`. #56 stays at
`9aa2ab2f3fdb0d6eb06683276d397e3d3adf3008`, with its failure evidence and completed
review cycle unchanged. This repair includes no #56 code or migration. SQL
0001–0032, existing contract payloads, approved packet/dependency/custody records,
runtime inventory, dependencies and release artifacts remain byte-exact.

## Behavior

At the worker's context-opening call, recognize only SQLSTATE 28000 with the
kernel's exact message and direct `begin_authorization_context(text,uuid)` RAISE
origin. Unknown diagnostics fail visibly. The exception leaves the transaction
and connection scopes before persistence reports `authorization_unavailable`.
That receipt carries no claimed canonical task status. A later native
`stale_fence` outcome keeps `late_output_discarded/stale_fence`; no unconditional
conversion to that outcome is introduced. The normalization does not enclose
queue reauthorization, persistence, connection creation or unrelated SQL.

No revoked credential settles an attempt. Rollback and started-operation ownership
remain intact, including cancellation draining and late accounting. Recovery does
not reauthorize with a substitute credential, widen scope, reauthor, redispatch,
restore a consumed budget or change a stored lease/deadline. The separately
authorized existing reaper can recover after the original lease expires. The
worker's requested/cumulative bounds, refusal cadence and SQL timeouts are unchanged.

## Permanent acceptance cases

| Explicit sequence | Required outcome |
| --- | --- |
| Commit revocation before the real context call | `authorization_unavailable`, no task-status assertion, context transaction rolled back, attempt unchanged |
| Open real context, then commit revocation before queue checks | `late_output_discarded/stale_fence`, no settlement, attempt unchanged |
| Cancel owned recovery while either context boundary is held | Started database call drains; same truthful owned receipt; original deadline preserved |
| Reap after original lease expiry | Separate active reaper returns one then zero; same attempt/fence, no dispatch |
| Unknown context SQLSTATE 28000, same message from another function, or SQLSTATE 28000 after context opening | Error stays visible, transaction rolls back, no canonical output |
| Existing refusal, late-output cancellation, requested-budget and restart cases | Historical invariants preserved |

Barriers establish the before/after sequences. Watchdogs detect a stuck fixture;
they are not timing tolerances that choose the interleaving.

## Evidence and handoff

Tests-first commit `eab06704518dfc5ff2592b47c1b2fb6f74da578b` reproduced one error
in four cases using the unchanged accepted #55 installed wheel: before-context
revocation raised SQLSTATE 28000; after-context and unrelated-error controls passed.
The first repaired focused run retained two errors and two failures from incorrect
fixture assumptions: reaping at the long deadline violated the existing reaper
clock window, and owned cleanup returns its stored cancellation receipt. The
fixtures now use the original lease expiry and existing cancellation contract;
all 15 focused cases pass. A launch-path failure is retained separately.

Final exact-head artifacts, Python 3.13/3.14 full installed convergence, all three
fresh-process proofs, cheap package CI and bounded review evidence are recorded on
the repair PR before handoff. Passing component tests are not semantic-quality,
clock-root-cause or PR-05 useful-workload certification.

Leave this repair unmerged. After the owner merges it, integrate its accepted
merged commit into #56 and complete that PR's installed and restart qualification.
No integration of an unmerged repair, release/version selection, publication,
provider calls/spending, unseen holdout access, owner data, deployment, Desktop
consumption, owner checkpoint or PR-06 work occurs here.
