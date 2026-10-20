# CI cost and qualification policy

John's 2026-10-19 direction authorizes moving eligible fictional qualification to
standard isolated hosted CI within existing ceilings. Authority: human message
`01a15724-1bc8-7830-b633-23ea16bd7b6b`, implemented from delivery audit commit
`08820a17fe41db37e1d5cdaa70d15a927d8ce5f4`. This supersedes the former
owner-per-hosted-run and mandatory duplicate local-full-proof requirements for
this eligible path. Substantive authority, custody, safety and release gates stay.
Public core owns these semantics; Desktop has its own workflow policy.

| Stage | Evidence | Selection |
| --- | --- | --- |
| Every PR/main push | `package`: database-free tests, contracts, boundary, lint/types, archive inspection and deterministic rebuild | Automatic, inexpensive required check |
| Ready fictional candidate / material proof-input change | Exact-head installed wheel and sdist equality, isolation, all installed tests and four restart proofs, Python 3.13 and 3.14, pinned PostgreSQL | Standing eligible hosted path below |
| Local diagnostic | Focused source/CPU checks; DB work only with its coordinator slot | No duplicate full local run required when matching hosted proof is complete |
| Release | Latest explicitly requested successful full run at exact release main, approved archive hashes and actual successful package/3.13/3.14 jobs | Separate explicit release authority and existing publication gates |

`package` remains the sole routine required status. Do not make an intentionally
skipped compatibility job a required check. Keep review resolution, main/tag
protection and publishing protections. Branch implementation is not proof that
this policy is active on main or that ruleset configuration has changed.

## Eligible selection and repetition

`select_hosted_qualification.py` records source SHA, authority, purpose, reason and
changed proof inputs in `qualification-request.json`. It accepts only the named
public repository, with no owner/provider/held/unseen inputs:

- An internal non-draft PR with `qualification-ready` selects a candidate when
  labeled ready, opened/reopened ready, or made ready for review. Later pushes
  select full proof only for changed proof inputs. The coordinator applies the
  label to a final ready candidate; ordinary intermediate edits use cheap checks.
- A main push selects full candidate proof only for material proof-input changes.
- An exact branch `workflow_dispatch` with `purpose=candidate` selects explicit
  standing qualification, including a justified failure correction. It needs no
  new owner-per-run approval. The dispatcher still needs repository write access.

Proof inputs are `src/`, `tests/`, `scripts/`, `migrations/`, `contracts/`,
`.github/`, package configuration, root `requirements*.txt`, and the package-authored
`README.md`, `PYPI_README.md`, `LICENSE` and `NOTICE`. Those four root files change
reviewed archives and are material even when their content is prose. Other
documentation-only changes use cheap checks unless explicitly selected ready. They do not acquire a
new exact-head runtime qualification from an earlier head: name both SHAs, paths
and unchanged qualified inputs when reusing earlier evidence. Missing comparison
commits fail selection rather than silently counting as documentation-only.

After this workflow is available on main, a coordinator can select a branch:

```sh
gh workflow run python-package.yml --repo JohnnyFiv3r/memoriesql \
  --ref <ready-branch> -f expected_sha=<full-commit-sha> -f purpose=candidate
```

Do not dispatch duplicate runs for the same passing head, retry passing jobs, or
rerun the full suite on every edit. Preserve failures; a correction or changed
proof input can justify another bounded run. Source and archive receipts bind the
actual selected head, not a PR merge simulation or moving branch name. Forks and
private repositories are ineligible for this standing matrix.

## Installed proof and claim equivalence

The `compatibility` job and `run_hosted_qualification.py` are the executable proof.
Each interpreter has its own standard `ubuntu-24.04` runner and disposable
PostgreSQL service. Only tracked public scripts, inventories, test modules and
fictional JSON fixtures are copied; no local evidence directory is uploaded.
A public file remains subject to source review: tracking a file is not privacy
classification. Never port PR80's held one-attempt packet or treat a historical
exact local authorization as a new hosted authorization.

| Required claim | Hosted evidence / limit |
| --- | --- |
| Exact reviewed source and archives | Clean tracked checkout, full source SHA, archive inventory verification, sizes/hashes; exact artifacts transferred from `package` |
| Python coverage | Actual separate `compatibility (3.13)` and `compatibility (3.14)` jobs; interpreter and installed dependency versions retained |
| PostgreSQL identity | Existing image index `postgres@sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636`; Linux amd64 child `sha256:4cc13dede823cab4e05290c7fb3350fb4e599ecabd9b07e6706b5d5e8f5bc929`; runtime server 18.4 asserted; Docker image ID and service state retained |
| Same archive routes | Pinned build tools, `SOURCE_DATE_EPOCH=315532800`, independent sdist-to-wheel rebuild byte-identical to reviewed wheel, then one installed proof of those identical bytes per interpreter |
| Installed isolation | Fresh venv and copied test directory, `python -I`, no `PYTHONPATH`, existing package-isolation and release-installation checks; installed acceptance guard denies checkout/home reads, subprocesses and non-loopback connections |
| Full installed suite | All three acceptance shards run; every child exit/log retained. Reports must discover the same suite, cover every module exactly once, execute all discovered tests, and have zero skips/errors/failures. No selected subset substitutes |
| Fresh-process restart proof | Assessed relation, fold recovery, database time recovery and query result each execute prepare/recover/cleanup as distinct processes. Failure does not skip cleanup or later restart attempts |
| Failure and cleanup truth | Command manifest is written before child launch, with actual exits, timestamps and log hashes. Deadlines/interruption stop owned process groups; a single reserved cleanup window and always-run exact-service stop retain failure metadata and PostgreSQL state. Missing/failed/cancelled proof is not qualification |

The pinned image is a multi-platform index; this runner selects its Linux amd64
child, not an owner's macOS image. This is equivalent fictional Linux installed
proof for the claims above. It does not certify owner-host resource behavior,
a local-only fault reproduction, semantic quality, custody, live recall or
integrated product acceptance. A separately scoped local-specific claim still
needs its own evidence. No live model/provider calls belong to this path.

The package job remains capped at 15 minutes; each interpreter at 60 minutes.
Within that, the helper has a 50-minute proof deadline and one shared additional
2-minute cleanup allowance; the proof step is capped at 55 minutes, exact-service
cleanup at 3 minutes. Three shards share each isolated service. Standard public
repository runners use GitHub's documented standard resources (currently four
CPUs, 16 GB memory and 14 GB disk for this Linux class); no larger or paid runner
class is selected. See [GitHub runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
These are ceilings, not runtime promises. Child regular-file/log writes are
capped at 16 MiB, at most 40 commands (640 MiB of child logs); reaching the log cap
fails proof rather than silently giving truncated output success. Runner/job
limits additionally bound the environment; these harness limits change no core
runtime contract. Broadening the resource/spend class needs explicit authority.

Always-run uploads retain admission and complete qualification evidence for 30
days, including failures, all child logs/exits, shard reports, dependency hashes,
source/archive/input pins, versions and owned PostgreSQL cleanup state. GitHub
retains job logs for failures before the helper can start. Artifact-upload or
cleanup failure fails the job; an absent artifact is never a passing receipt.
No harness can finish uploads after a hard runner loss: that run is incomplete
and cannot qualify. Only cheap superseded package jobs cancel; compatibility
jobs have unique per-run/interpreter concurrency, fail-fast disabled, and are
not cancelled by a later documentation-only push. No duplicate local full run is
required after complete equivalent hosted evidence.

## Release remains separately authorized

A manual main run is release-only: `purpose=release`, exact `expected_sha` and a
nonempty durable `owner_approval_ref` for separately granted release-main
qualification. Candidate manual dispatch on main is refused because the existing
release verifier selects manual main runs. Automatic main candidate proof has
`event=push` and cannot satisfy that gate. An input reference records approval;
it does not grant it. Check the actual owner authority before release dispatch.

The unchanged publishing verifier requires the latest manual qualification at
exact current release main to succeed, with exactly `package`,
`compatibility (3.13)` and `compatibility (3.14)` actually successful. Skipped,
missing, cancelled, failed or partial jobs are refused; no older-pass fallback.
Only its exact-head archives can be compared with the committed approved release
inventory. Immutable hashes, approved tag/repository identity and owner-only
protected `pypi` upload remain unchanged. This direction authorizes no version,
merge, tag, release, upload, publisher change or Desktop consumption.
