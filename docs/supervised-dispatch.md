# Supervised managed dispatch (published in 0.0.9)

Schema 26 and `memoriesql.supervised-dispatch.v1` add an explicit qualification
profile for a host-visible managed turn whose underlying inference requests cannot
be independently counted or bounded. The hard-bounded single-inference interface
remains the default. This profile is not part of published 0.0.8 and includes no
provider adapter, credentials, provisioning, live-call permission or release.

## Approval and dispatch

A separately authorized administrator provisions one immutable
`model_supervised_qualifications` record for one existing complete-input execution
task. Its exact `SupervisedQualification` pins the route/profile, model, credential
identity, billing and quality policy, transport qualification revision, absolute
deadline and reported-usage stop triggers. The database also binds tenant,
workspace, access scope and origin principal; both origin and recorded approver
must exist in that tenant. The approver field is the trusted administrator's
record of external approval, as in the existing producer/dispatch policy tables,
not an authenticated runtime caller or a new permission grant. The administrator
is responsible for verifying that approval before provisioning. Worker/application roles cannot
provision or change it. Only revocation is permitted; the task cannot receive a
replacement approval. No policies are installed by migration.

The profile permits one managed turn and optionally one subsequent hard-bounded
inference on a distinct profile, or, for a relation task only, a second managed
turn. All bindings share the same exact approval.
`ManagedModelAdmission` requires the `ManagedProviderModel` interface; an arbitrary
SDK model remains denied. The external adapter must start one fresh turn with the
supplied host-visible frame and disable host retries and fallback. Hidden provider
context, inference requests and retries are explicitly not independently observed
or bounded. Interface conformance alone is not adapter qualification.

The existing accounting transaction commits an intent before dispatch. A
qualification lock serializes allowance consumption across workers and attempts;
all committed intents consume the allowance, including crashes before dispatch,
failures and ambiguous completion. An intent replay is not permission to dispatch
again. Restarting a worker does not replenish the allowance. The second route
requires successfully accounted reported usage and must fit its conservative
single-inference reservation into the remaining allowance. It cannot run in
parallel or use an unsettled first answer. From schema 29, a
[relation task](relation-assessment.md) may qualify its specialist's second route
as a managed turn too; the database admits that for no other task, and the
reported stops still bound both turns.

## Accounting without invented precision

`ManagedUsageObservation` separates turn completion, nullable underlying inference
count and its basis, reported aggregate, estimated aggregate and optional distinct
request observations. Complete observed requests must agree with a reported total.
Accounting selects one aggregate: reported total, otherwise complete request
observations, otherwise estimate, otherwise unavailable. It never adds totals and
parts. Reasoning is included in generated tokens, not added a second time.

`model_dispatch_usage_fold_v2` projects both boundaries with their original intent
and events, explicit count basis and managed observation. The existing
`model_request_usage_fold` and its dependent legacy rollups remain single-inference
views; they deliberately exclude managed turns. The executor's process-local
`UsageSummary.requests` counts observable model calls, not hidden inferences.
Use the v2 ledger projection for supervised qualification evidence. Unavailable
usage remains null in the ledger even when framework scratch counters need zeros.

Existing cash fields retain their provenance: provider-reported money where
supplied, catalog-derived estimates, covered-subscription zero incremental cash,
or unavailable. A reported-usage stop is not a remote hard token or cash cap.
Unpriced or uncertain exposure cannot pass a configured cash stop. A managed
intent has null hard token/cash bounds and zero conservative reservation; this
means no hard reservation claim, not free inference. No new pricing source exists.

Only completed turns with reported usage below token stops can authorize meaning.
Estimates, unavailable totals, ambiguous completion, revocation, elapsed deadline
and reached stops retain accounting but cannot authorize another dispatch or
canonical acceptance. Reported cash must not exceed its stop. The immutable
deadline bounds local waiting and further authorization; it cannot guarantee
remote termination or prevent an already dispatched provider from exceeding a
reported-usage threshold.

## Existing execution and evidence controls

This uses the same worker, queue, task/run attribution, executor, authority fences,
source authorization, trusted exposure, canonical acceptance and cleanup owner.
There is no alternate execution lane. Supply receipts attest only to host-visible
material. They prove neither hidden provider context nor comprehension, source
completeness or semantic truth. Source contents remain untrusted data.

Cancellation/lease loss closes dispatch and meaning while the original cleanup
owner drains started work and records late usage. Keep that owner and event loop
alive; a foreground timeout is not proof of remote termination or settled usage.
The migration preserves all earlier SQL and published contract payloads. Installed
fictional tests prove mechanics, not provider transport, useful authorship or demo
completion. Provider composition, real-model quality evaluation, source/trust
provisioning, publication/private consumption and CP-2 remain separate gates.
