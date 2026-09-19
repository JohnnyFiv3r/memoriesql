# Explicit bounded model admission

Public composition may opt into `RealModelAdmission` for an exact
`BoundedProviderModel` instance, model profile, accounting target and immutable
qualification revision. Ordinary SDK models remain denied. Exact built-in
`TestModel` and `FunctionModel` doubles retain their characterized path; subclasses
cannot silently introduce another transport. A registry cannot mix characterized
and admitted profiles in one execution tree.

This interface is a trusted composition boundary, not proof supplied by a model.
The integrator must characterize the adapter's actual transport before constructing
an admission. Each `request_bounded` call must perform at most one inference,
without transport retries, fallback, hidden history, delegated authors or an
internal inference/tool loop. It must enforce the complete request's input ceiling
before provider dispatch (including instructions, tool schemas/results, repeated
and cached context), and a generated-token ceiling including reasoning. A prompt
or a post-hoc usage estimate does not satisfy this interface. Unsupported hard
bounds require rejection before inference. Adapters must forward the exact supplied
messages and preserve cancellation and late settlement ownership.

The existing executor retains concurrency, queue/attempt fencing, authorization,
trusted source delivery and canonical acceptance. It reserves the next request's
maximum input and output under the execution tree's request lock before durable
intent and inference. All reservations count against request, directional and total
token limits, starting from the trusted initial usage snapshot. Reservations are never refunded, even when usage is small, missing,
cached, failed or cancelled. The durable intent records these maxima; usage remains
truthful and independent. Reported overruns are accounted but cannot authorize
exposure or successful output. This detects a broken adapter; it cannot undo a
transport overrun and is not the hard-cap mechanism.

Reservations are scoped to one existing executor tree. They do not independently
provide a restart-safe multi-attempt batch allowance. A caller authorizing a batch
must reconcile existing durable intents across its attempts and must not restart
with a fresh allowance. This change does not add a budget service or claim such a
batch has been qualified.

No production adapter, provider credentials, subscription entitlement, billing
fallback or live qualification is included. Subscription and unknown monetary
states retain the existing accounting representation. Deployment composition must
prove transport bounds, exposure, cancellation, cleanup and available entitlement
before admission. An opaque task API without enforceable per-inference bounds
cannot be admitted merely by wrapping it in this interface.
