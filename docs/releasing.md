# Owner-controlled preview publishing

The owner approved public visibility and publishing configuration on 2026-09-08,
not creation of a release tag or a package upload. Repository visibility does not
change the 49-record / 12-experimental-API contract-only scope.

## Exact identity

| Setting | Value |
| --- | --- |
| PyPI project | `memoriesql` |
| GitHub owner | `JohnnyFiv3r` |
| GitHub repository | `memoriesql` (repository ID `1357510758`) |
| Workflow filename | `publish-pypi.yml` |
| Workflow path | `.github/workflows/publish-pypi.yml` |
| GitHub environment | `pypi` |
| Only accepted preview tag | `v0.0.1a1` |

Register these values as a pending GitHub Trusted Publisher in the intended
PyPI owner's account. A pending publisher does not reserve a project name or
create a PyPI project; first publication does. Never register the private
product repository or use long-lived PyPI tokens as a fallback.

## Controls

- The `pypi` environment requires approval from `JohnnyFiv3r`, disallows
  administrator bypass, and accepts only the **tag** `v0.0.1a1` (no branches).
- Self-review is allowed so the sole owner can approve a release they initiated.
  Automation must not approve its own publishing job on the owner's behalf.
- Only creation of that exact tag can trigger the workflow. No manual dispatch,
  pull-request trigger, branch-push trigger, or TestPyPI target is configured.
- Before requesting approval, the workflow requires the tag commit to equal
  current `main`, the package version to match, and the latest exact-head
  `python-package.yml` push run to be completed successfully.
- It downloads that CI run's immutable artifacts, verifies both archive hashes
  and sizes against the committed inventory, and stages only the wheel and
  sdist. It does not rebuild or repeat the already-passed compatibility matrix.
- Publishing uses a separate environment-gated job, pinned actions, OIDC,
  and no repository-code execution. Only that job receives `id-token: write`.
- Workflow activation and publisher registration are configuration evidence,
  not proof that OIDC exchange or an upload has succeeded.

## Later release authorization

After separate owner approval to release, reverify the canonical repository,
publisher identity, environment rules, version, exact current-main CI, retained
artifacts, and inventory. Only then create `v0.0.1a1` at that exact commit.
The owner must separately approve the waiting environment job after inspecting
the selected CI run and artifact hashes. Do not recreate, move, or delete a
published tag or replace an uploaded distribution. Corrections require a new
distribution version and an explicitly reviewed update to these narrow rules.

If exact-head CI is missing, pending, failed, or its artifacts have expired,
the release fails closed. Obtain fresh authorized evidence before retrying;
do not select an older commit's artifacts or weaken the verification.

References: [PyPI pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [GitHub environment protection](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).
