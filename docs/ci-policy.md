# CI cost and qualification policy

The standing engineering delegation in
[contributor guidance](../AGENTS.md#standing-engineering-delegation) authorizes
eligible fictional qualification on standard isolated hosted CI within existing
ceilings. It covers
bounded implementation, testing, diagnosis, repair, justified reruns, publication
of eligible public source changes, independent Codex review and eligible merge
for an approved outcome. Routine steps require no new owner/run approval or
coordinator START. This replaces the former owner-per-hosted-run and mandatory
duplicate local-full-proof requirements for this eligible path. Substantive
contract, custody, privacy, safety, quality and release gates stay. Public core
owns these semantics; Desktop has its own workflow policy.

| Stage | Evidence | Selection |
| --- | --- | --- |
| Every PR/main push | `package`: database-free tests, contracts, boundary, lint/types, archive inspection and deterministic rebuild | Automatic, inexpensive required check |
| Ready fictional candidate / material proof-input change | Exact-head installed wheel and sdist equality, isolation, all installed tests and four restart proofs, Python 3.13 and 3.14, pinned PostgreSQL | Standing eligible hosted path below |
| Local diagnostic | Focused source/CPU checks; DB work only with its coordinator slot | No duplicate full local run required when matching hosted proof is complete |
| Release | Latest explicitly requested successful full run at exact release main, approved archive hashes and actual successful package, part and aggregation jobs for 3.13/3.14 | Separate explicit release authority and existing publication gates |

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

Technical failures lead to diagnosis, repair, appropriate review and a justified
bounded rerun within the approved outcome and verified remaining resources, not
another routine owner prompt. A documented diagnostic purpose can justify a
bounded repeat; unchanged blind retries cannot. Keep complete coverage and the
same acceptance thresholds. Preserve failed/spent attempts and UNKNOWN accounting;
never relabel them successful or infer historical ceiling compliance. A successor
uses current delegated authority and verified capacity, not a revived old grant.

An accounting hold applies to its actual grant, inputs and resources. It does not
freeze unrelated authorized public source/log work. Keep explicit cumulative
allowances intact; the delivery owner can allocate existing capacity but cannot
silently enlarge limits or assume headroom after UNKNOWN consumption. Serialize
conflicting local database runs and verify cleanup before resource reuse.

## Installed proof and claim equivalence

The `compatibility` part jobs, each interpreter's `qualification` aggregation
and `run_hosted_qualification.py` are the executable proof. Each interpreter's
acceptance is split into three parts, each on its own standard `ubuntu-24.04`
runner with its own disposable PostgreSQL service. Only tracked public scripts, inventories, test modules and
fictional JSON fixtures are copied; no local evidence directory is uploaded.
A public file remains subject to source review: tracking a file is not privacy
classification. Never port a held one-attempt local packet or treat a historical
exact local authorization as a new hosted authorization.

| Required claim | Hosted evidence / limit |
| --- | --- |
| Exact reviewed source and archives | Clean tracked checkout, full source SHA, archive inventory verification, sizes/hashes; exact artifacts transferred from `package` |
| Python coverage | Actual separate jobs per interpreter, `compatibility (<minor>, part 1–3)` and `qualification (<minor>)` for 3.13 and 3.14; interpreter and installed dependency versions retained, and equal across an interpreter's parts; each part's `setup-python` resolves the newest patch (`check-latest: true`), not its runner image's cached one |
| PostgreSQL identity | Existing image index `postgres@sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636`; Linux amd64 child `sha256:4cc13dede823cab4e05290c7fb3350fb4e599ecabd9b07e6706b5d5e8f5bc929`; runtime server 18.4 asserted; Docker image ID and service state retained |
| Same archive routes | Pinned build tools, `SOURCE_DATE_EPOCH=315532800`, independent sdist-to-wheel rebuild byte-identical to reviewed wheel in every part, each installing those identical bytes; the aggregation requires every part's wheel, interpreter, dependencies, environment, image and copied inputs to be equal |
| Installed isolation | Fresh venv and copied test directory, `python -I`, no `PYTHONPATH`, existing package-isolation and release-installation checks; installed acceptance guard denies checkout/home reads, subprocesses and non-loopback connections |
| Full installed suite | All six shards of one longest-first plan run, two per part; every child exit/log retained. The aggregation recomputes the plan from the exact source: every report must discover the same suite and run exactly its planned modules, together covering every module exactly once, executing all discovered tests, with zero skips/errors/failures. No selected subset substitutes |
| Fresh-process restart proof | Once per interpreter, in its host part (part 1) after that part's acceptance: assessed relation, fold recovery, database time recovery and query result each execute prepare/recover/cleanup as distinct processes. Failure does not skip cleanup or later restart attempts |
| Failure and cleanup truth | Command manifest is written before child launch, with actual exits, timestamps and log hashes. Deadlines/interruption stop owned process groups; a single reserved cleanup window and always-run exact-service stop retain failure metadata and PostgreSQL state. A part record never claims a proof: only the aggregation writes `proof_complete`, after every part job of the same run attempt succeeded and each part artifact matched GitHub's recorded digest. The part result it reads covers both interpreters, so a failed part of either refuses both aggregations. Missing/failed/cancelled proof is not qualification |

The pinned image is a multi-platform index; this runner selects its Linux amd64
child, not an owner's macOS image. This is equivalent fictional Linux installed
proof for the claims above. It does not certify owner-host resource behavior,
a local-only fault reproduction, semantic quality, custody, live recall or
integrated product acceptance. A separately scoped local-specific claim still
needs its own evidence. No live model/provider calls belong to this path.

The package job remains capped at 15 minutes; each part job at 100 minutes and
each aggregation at 15. Within a part, the helper has a 90-minute proof deadline and one shared additional
2-minute cleanup allowance; the proof step is capped at 95 minutes, exact-service
cleanup at 3 minutes. Two shards share each part's isolated service: four on one
runner saturated its CPUs without finishing sooner (F-3), so more runners, not more
processes per runner, shorten the proof. Standard public
repository runners use GitHub's documented standard resources (currently four
CPUs, 16 GB memory and 14 GB disk for this Linux class); no larger or paid runner
class is selected. See [GitHub runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
These are ceilings, not runtime promises. Child regular-file/log writes are
capped at 16 MiB, at most 40 commands (640 MiB of child logs); reaching the log cap
fails proof rather than silently giving truncated output success. Runner/job
limits additionally bound the environment; these harness limits change no core
runtime contract. Broadening the resource/spend class needs explicit authority.

Always-run uploads retain admission and complete qualification evidence for 30
days, per part and per aggregation, including failures, all child logs/exits,
shard reports, runner CPU, memory and pressure samples in every part (observed
only, never a gate), the aggregation manifest binding each part artifact's
name, ID and digest, dependency hashes,
source/archive/input pins, versions and owned PostgreSQL cleanup state. GitHub
retains job logs for failures before the helper can start. Artifact-upload or
cleanup failure fails the job; an absent artifact is never a passing receipt.
No harness can finish uploads after a hard runner loss: that run is incomplete
and cannot qualify. Only cheap superseded package jobs cancel; compatibility
jobs have unique per-run/interpreter/part concurrency, fail-fast disabled, and are
not cancelled by a later documentation-only push. No duplicate local full run is
required after complete equivalent hosted evidence.

Consequential changes require a Codex review separate from the author, using
actual source/logs, contracts and adversarial cases. Preserve custody separation,
unseen-fixture isolation and executable evidence. This is not outside human
validation, and unresolved safety/correctness claims stay open. Do not invent a
missing human technical reviewer as a prerequisite. New material findings justify
focused rereview after repair; no fixed review-count cap excuses unresolved
findings. Avoid unchanged review loops.

Compatible increments may merge in dependency order after applicable final-head
CI and review pass, without waiting for the whole program. Keep requested PRs
open/non-draft, subscribe to comments, and fix and answer actionable Codex review
findings. Verify that merge cannot trigger unauthorized release or activation.
An approved isolated integration may consume an immutable, attributable core
candidate through public interfaces under `AGENTS.md`; this does not relax
export boundaries, permit editable/private-interface substitutes or update
default/released runtime pins. Candidate and hosted proof remain distinct from
semantic quality, end-to-end acceptance and released consumption.

## Release remains separately authorized

A manual main run is release-only: `purpose=release`, exact `expected_sha` and a
nonempty durable `owner_approval_ref` for separately granted release-main
qualification. Candidate manual dispatch on main is refused because the existing
release verifier selects manual main runs. Automatic main candidate proof has
`event=push` and cannot satisfy that gate. An input reference records approval;
it does not grant it. Check the actual owner authority before release dispatch.

The publishing verifier requires the latest manual qualification at exact
current release main to succeed, with exactly `package`, the six
`compatibility (<minor>, part <n>)` jobs and both `qualification (<minor>)`
aggregations actually successful. Skipped,
missing, cancelled, failed or partial jobs are refused; no older-pass fallback.
Only its exact-head archives can be compared with the committed approved release
inventory. Immutable hashes, approved tag/repository identity and owner-only
protected `pypi` upload remain unchanged. This direction authorizes no version,
tag, release, upload, publisher change or released product consumption. Eligible
incremental merge and approved isolated candidate integration remain subject to
the standing engineering and substantive gates above.

The host/CLI refresh additionally requires the separately invoked
`prove_query_host.py` on both supported interpreters, in each interpreter's host
part after the four restart triplets and before proof completion. It requires exactly 17 named cases in
`query_host_acceptance.py`: the 16 existing host cases and the public CLI case
using the real Unix socket. `query_host_fault.py` is support code, not a second
acceptance module. Missing, extra, duplicate, failed or skipped cases prevent
proof completion. Default installed discovery does not select these cases.
This adds no runner, deadline, privilege or release
route. The helper keeps its existing40-command cap; current host composition and
actual complete-suite/host timing remain to be qualified at their exact head.

The prepared two-identity CLI fixture remains disabled without its separately
approved exact source/launcher/authority binding. This source preparation grants
no sudo, account, group, capability or workflow privilege. Unknown process birth,
lineage or cleanup irreversibly fails the host helper and job and stops later
proofs. Its private S-owned failure record is retained by the ordinary evidence
path; failure to retain that record also fails the job. Only bounded cleanup of
already registered resources, PostgreSQL stop and evidence upload may follow.
Standard disposable hosted VM destruction is failed-job infrastructure fallback,
not a success oracle, cleanup/reaping evidence, elapsed-time credit or refund.
All existing cases, restart proofs, deadlines and resource caps remain required.
