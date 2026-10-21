# Legacy7 cutover assertions

This is source preparation, not native qualification. Migrations0001–0044 and
the exact approved0045 restriction remain unchanged. No new product lock,
privilege, switch or admission protocol is added. The installed migration stream
contains45 resources; record payloads remain historical and the approved catalog
note distinguishes artifact presence from execution availability.

`tests/runtime/test_legacy7_cutover.py` contains seven additive fictional cases:

| Schedule | Actual oracle when executed |
| --- | --- |
| Old activation entered before waiting | Block on its existing tenant/operation key, observe the real blocker PID, release and join the original call, then install45 and refuse fresh/replayed activation without new effects. |
| Replacement without drain, negative control | Hold a real activation transaction after the function returns, observe its native uncommitted state, install without draining, then allow the old transaction to commit. This counterexample must never count as effective cutover. |
| Apply checked before commit | Hold the actual successful canonical adapter return inside its original transaction, observe native uncommitted state and absence of visible meaning, release/join the owned worker and cleanup, then install45. Preserve committed history and refuse canonical replay. |
| Old provider-intent native wait | Use the original worker-produced request and existing per-request advisory key. Observe actual SQL waiting, request foreground cancellation, release the owned writer, and await actual cleanup before installing. No model callback, no invented usage, no late meaning. Preserve the original intent and refuse its later replay. |
| New policy after installation | Create a real owner fixture policy after45 and attempt fresh activation on an unactivated binding. It must refuse without task/execution/receipt effects. |
| Original request parked outside SQL | Pause the real worker immediately before its first intent SQL, install45 while this exact fixture ingress is parked, then resume. Its first intent must refuse with no ledger row, model callback or meaning; await original cleanup. |
| Direct canonical caller | With the original queued execution and current worker authority, call the canonical task-bound source authorizer directly after45. Require the actual42501 gate rather than a worker-only restriction. |

The failed installed candidate `119e412ed08f1e58270fe7165e3e59c5e1c377a6`
is preserved in hosted run `39518702328`. Its successor fixture corrections
retain all native oracles: the uncommitted-activation control uses the public SQL
function inside its original authorized transaction, because the Python adapter
owns and commits its own transaction. The cancellation fixture selects a 20ms
foreground return bound inside its existing five-second observer deadline.
Direct worker EXECUTE must still fail under the historical ACL; a second call
uses the existing fixture owner's SQL privilege with the same current worker
authorization context to reach the canonical gate, without granting privileges.
Historical relation equality compares the same explicit `known_at` frame.
The separate blocked-recovery case observes the real rolled-back start-time
constraint before installing its blocker, and retains the 200ms transaction and
three-second observer bounds while exposing the actual owned database error.
These are unqualified source repairs until installed native evidence passes.

The finite fixture roster is closed by construction. OwnedCall completion joins
the actual target, including its transaction context exits. Observing a reply,
requesting cancellation or setting an Event alone is not drain evidence. Failed
coordination releases owned blockers and observes the original worker cleanup;
missing completion fails the case. No fake predecessor, canonical ledger row,
provider request identity, usage event or successful settlement is manufactured.

The original12 cases remain additive controls, including authorized historical
reads, revisions4/6 and the hidden/missing family. Full earlier2–6, query/inspection
and recovery obligations remain in repository qualification. None may be replaced
by this focused module. The seven new schedules and original12 have not run here;
no skip, source assertion, CPU mock or type-check is a native pass.

Before native execution, bind the actual new candidate artifacts, installed test
closure, copied-input hashes, launcher/resource limits and a fresh named exclusive
slot. The old973 twelve-case proposal does not select these19 cases or authorize
their execution. Later full supported-interpreter qualification and independent
review/custody applicability remain required. A successful fictional run would
prove only this owned fixture, not enumeration or cessation of every production
custom host, provisioning path, old transaction or provider. Actual deployment
ingress-stop/drain binding remains necessary before an operational availability
claim. No owner data, live model, merge, publication or release is authorized by
this verification document.
