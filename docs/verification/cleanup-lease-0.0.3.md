# Cleanup-lease handoff qualification

The public baseline is `5c51863669493117a511abf6d65a63dc6e731f49` (`0.0.2`).
This correction prepares `0.0.3`; it does not publish it.

## Permanent failing regression

The public-authored `tests/runtime/test_cleanup_lease_handoff.py` was first run
against the unmodified installed PyPI `memoriesql==0.0.2`, from a disposable
directory with only public fixture dependencies copied alongside it. No source
file rewriting, editable installation, or exit-status suppression was used.

```sh
python -m unittest -v test_cleanup_lease_handoff.CleanupLeaseHandoff.test_blocked_heartbeat_cannot_make_unfinished_executor_reapable
```

The regression creates a generated fictional PostgreSQL database, applies the
fourteen installed migrations, and uses the public orchard bootstrap/queue
fixture. A gate pauses an ordinary heartbeat before its database operation.
The executor ignores cancellation until explicitly released. A two-second
persisted expiry is fixture setup only; production SQL and lease bounds are
unchanged. The real queue reaper runs after that expiry.

On installed `0.0.2`, the permanent regression confirms both operations remain
unfinished, then fails with `1 != 0: unfinished execution must not become
reapable`. Process exit status is **1**; the final recorded run took 2.615 seconds.
All gates are released and owned operations drained in bounded finalizers even
when the assertion fails. The initial pre-repair run also failed with exit 1.

## Repair and local qualification

The worker cancels the heartbeat without awaiting it at the retention handoff.
The existing teardown continues to own and await it, while cleanup renewal stays
alive. Historical SQL already prevents a late ordinary heartbeat from shortening
the retained task/attempt/slot lease. No schema, persisted ID, task contract,
authorization, scheduler, or generation-fencing change is required.

The local complete installed acceptance lane passed **55 tests** in 18.451 seconds
on Python 3.13.5, with checkout filesystem access denied and loopback-only access
to disposable PostgreSQL. Focused tests cover blocked-heartbeat expiry/reaping,
late completion, initial and renewal failures, total control outage, lost fences,
repeated cancellation, quarantine after executor completion, ordinary success,
settlement failure and late accounting. The baseline regression is permanent in
the same suite and also runs in the hosted wheel/sdist Python 3.13/3.14 matrix.

The source suite has 35 tests. An initial local boundary check correctly rejected
an in-checkout development environment; moving that environment outside the
repository restored the existing boundary without changing the guard. Lint and
strict typing cover 64 source files. Migration export checks preserve fourteen
SQL files byte-for-byte, and catalog checks preserve 49 payloads and twelve APIs.

Genuine `0.0.3` wheel/sdist metadata and two deterministic local builds were
verified. Candidate hashes are committed in
[the preserved 0.0.3 inventory](runtime-0.0.3-package-artifact-inventory.json). Final hosted
exact-head artifacts must independently match it. The unchanged published
`0.0.2` inventory is preserved in
[the historical inventory](runtime-0.0.2-package-artifact-inventory.json).

Independent control retention is not guaranteed during total database
unavailability. Failed calls retain the unavailable signal and retry; expiry
remains possible, and an expired/lost fence cannot be revived. Foreground
cancellation is not termination, settlement, or transaction rollback. The
owning worker and event loop remain necessary until cleanup completes.

Final hosted CI, review responses and artifact comparisons belong to the owning
PR's exact head. The [release prerequisites](../releasing.md) still require owner
merge, successful exact-main push CI, approved tag protection, fresh availability
and artifact checks, separate tag authorization, and protected publish approval.
No settings change, tag, publication, product adoption, provider activation,
owner-data access, or checkpoint execution is part of this qualification.
