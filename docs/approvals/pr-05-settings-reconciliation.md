# PR-05 settings amendment: exact-record handoff

The candidate record is `pr-05-settings-approval-candidate-v2.json`. It pins the
whole amended packet and whole unchanged lifecycle dependency at the same stable
commit. Both SHA-256 values were computed from exact Git blob bytes, including
final LF. This is a candidate for exact owner approval, not an approval record;
its approval_ref placeholder must be replaced with the durable exact-record owner
decision after that decision is received. Boundary selection cannot fill that gate.

The owner-selected boundary and both required clarifications are durably recorded
at https://github.com/JohnnyFiv3r/memoriesql/pull/48#issuecomment-5851120561.
That comment records the verified owner message, source task/turn/item and scope.
The selection preserves independent database write/elevation/tenant/resource/scope
protections and discloses raw-login USERSET mutability. Credential isolation must
work against the agent's actual shell/filesystem/process capabilities. Cancellation
is initiated at the deadline; ownership/reservations/accounting persist through
confirmed settlement. A timeout is not evidence that remote work already stopped.
Integrated adversarial proof is required before any executor-safety claim.

Separately, the owner clarified that shipped source is visible: the current
unreleased owned type/function names are AdmittedQuery/admit_query, and current
qualification guidance uses query-admission. The product-neutral packet heading
uses agent-authored SQL and reusable investigation results. Actual SQL SELECT,
SQLGlot upstream AST names, necessary grammar explanations, licenses and historical
failure evidence are preserved. This editorial cleanup changes no SQL semantics,
wire shape, numerical policy, supported relation capability or evaluation threshold.
The old owned symbols were introduced only by unreleased #48; no published API
compatibility conflict was found. A normalized Python AST comparison verifies only
those owned symbol changes in the admission kernel.

The original owner record and custody ea-2 are immutable historical gate evidence
for their original whole pin. Neither silently extends to this amendment. The
existing independent PR-04 custodian must receive the complete new approved v2
record, verify every dependency and supply matching content-free custody
reconciliation before dependent executor implementation. Do not ask for or receive
unseen questions, gold, hints or access paths. Corpus preparation, both EG-0001
conditions, thresholds and prior failures remain the custodian's unchanged lane.

No result/investigation executor, migration, candidate condition, release/version
selection, merge, provider/model call/spend, owner data, deployment, production
checkpoint or Desktop consumption is added. #48 remains at its qualified head;
#46 and #47 remain explicit unmerged dependencies. Public release precedes Desktop
consumption. Compiler and privilege fixtures are not integrated executor safety,
useful W1-W7 capacity or semantic-quality certification.

Qualification preparation preserved: the first local isolated build could not
fetch its build dependencies under the network sandbox. The retry uses the already
installed, independently verified setuptools 80.9.0 / wheel 0.45.1 pins with
--no-isolation; it does not change build requirements or package behavior.
Source checks pass: 74 cases, strict mypy on 135 files, Ruff, generated catalogs,
61-file runtime closure, 64 unchanged historical records and all 29 SQL bytes.
Renamed installed package and final-head CI qualification are recorded on the PR;
older artifact bytes cannot qualify the renamed distribution.
