# CI cost and qualification policy

The owner approved reducing paid hosted repetition, not removing tests. This
policy applies to this public repository; Desktop has its own workflow policy.

| Stage | Required evidence | Execution |
| --- | --- | --- |
| Every PR/main push | `package`: database-free tests, contracts, boundary, lint/types, build/inspection and deterministic rebuild | Automatic hosted check |
| Before implementation handoff | Full installed acceptance, package isolation, sdist-to-wheel equality and fresh-process relation, fold and clock-recovery proofs on Python 3.13 and 3.14 | Local, disposable PostgreSQL |
| Explicit independent qualification | Same full matrix and exact-head archives | Owner-authorized manual hosted run |
| Release | Latest explicitly requested successful full run at exact current release main; actual successful package/3.13/3.14 jobs and approved archive hashes | Separate owner-approved release procedure |

`package` is the sole routine required status check. The repository's ruleset
must require it, rather than an automatically skipped compatibility job. Keep
review-thread resolution, main protection, tag rules and publishing protections.
Workflow changes take effect on their merged branch; do not claim this policy is
active on main before the owner merges it and the required-check configuration
is reconciled. Existing unique #51/#52 runs are preserved, not cancelled by this
policy rollout.

## Local qualification and evidence

Use the steps in `.github/workflows/python-package.yml` as the executable source
of truth, including its full `compatibility` job, not a hand-picked subset:

1. Work from a clean exact commit. Record its SHA and test-suite revision. Build
   the wheel and normalized sdist using the pinned development tools and
   `SOURCE_DATE_EPOCH=315532800`; retain both archives and the
   `inspect_python_distribution.py --source-commit <SHA>` development receipt.
   Run the normal package checks and deterministic second build locally first.
2. For each supported interpreter, use a fresh virtual environment and an
   explicitly disposable local PostgreSQL instance matching the workflow's
   pinned image. Set `N1_TEST_DATABASE_URL` only to that fictional test instance.
   Never source owner/provider credentials or point these tests at product data.
3. Follow the dependency download and independent sdist-to-wheel rebuild steps;
   retain dependency hashes. Rebuilt and reviewed wheels must be byte-identical.
   An installed run on those identical bytes covers both artifact routes without
   running the same suite twice per interpreter.
4. Copy the acceptance scripts, all migration/runtime test modules, fixture JSON
   and both inventories into the fresh temporary test directory exactly as in
   the workflow. Run package isolation and release installation with `python -I`;
   run **all three acceptance shards**, collecting every exit status; then run
   assessed-relation, fold-recovery and database-time-recovery `prepare`, `recover`,
   `cleanup` in separate processes. Do not run just source imports or the
   database-free discovery suite.
5. Retain complete stdout/stderr, interpreter/PostgreSQL versions, commands,
   timestamps, test counts/skips, all exit statuses, source SHA, artifact sizes
   and SHA-256 values. Link the evidence from the handoff. Preserve failures and
   separately label targeted corrections and full reruns. Missing/failed proof
   is a qualification gap, not a green result. Retain logs outside the package;
   clean up only the explicitly task-owned database/container afterward.

No hidden full-suite reruns on every intermediate edit: use focused checks while
working, then one full final-head local convergence. A later docs-only change may
reuse earlier runtime evidence only when the handoff explicitly names both SHAs,
the exact changed paths and why the qualified artifact/test inputs are unchanged;
it is not a new exact-head full qualification. Release still requires the exact
release-head hosted qualification below. Local tests are not an independent
hosted observation, and mechanical checks are not semantic-quality certification.

## Explicit hosted qualification

Obtain approval for a specific hosted run (and separately for any paid retry).
After this workflow exists on main, dispatch with the selected branch, its full
SHA and a durable reference to that owner approval:

```sh
gh workflow run python-package.yml --repo JohnnyFiv3r/memoriesql \
  --ref <approved-branch> \
  -f expected_sha=<approved-full-sha> \
  -f owner_approval_ref=<approval-reference>
```

The dispatcher must have repository write authority; the input reference records
approval but does not itself prove the owner gave it. Agents must check actual
authorization. The workflow rejects a changed branch SHA before installing tools.
Only a manual request runs the PostgreSQL matrix. Normal pushes show it skipped.
The package job has a 15-minute wall-clock cap; each matrix job has a 60-minute cap.
These are safety ceilings, not promised runtimes or minute reservations.

Automatic runs cancel older runs for the same PR/ref. Manual runs have unique
concurrency groups: neither another push nor another dispatch silently cancels
them. Do not dispatch duplicates, retry passing jobs or manufacture a green check.
Preserve the original failure evidence when a correction justifies a rerun.

## Release cannot use cheap-only green CI

The latest manual qualification on **main at the exact release SHA** must finish
successfully. The publishing verifier independently requires the `package`,
`compatibility (3.13)` and `compatibility (3.14)` jobs from that selected run and
SHA to have actually succeeded; missing, skipped, cancelled, failing or partial
job inventories are refused. There is no fallback to an older green manual run.

Only its exact-head archives can be compared with the committed approved release
inventory. The inexpensive main push is insufficient. Manual qualification does
not publish: the exact approved tag, repository identity, immutable hashes and
owner-only protected `pypi` upload gate remain unchanged. This CI change selects
no release version and authorizes no tag, upload or external publisher change.
