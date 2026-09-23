# memoriesQL

**Build memories with meaning.**

memoriesQL is an open-source, local-first memory foundation for AI agents.
It preserves authorized evidence in PostgreSQL and supports agent-authored
structured observations with inspectable source support.

The `memoriesql` package provides the provider-neutral open core: canonical
migrations, versioned contracts and composable Python runtime mechanics. It is
not the memoriesQL desktop product or a configured background memory service.

**Experimental / pre-alpha.** Installed fictional tests qualify mechanics, not
real-provider quality or production readiness. Applications must supply source
qualification, authority, worker composition and qualified model bindings.

## Install and explore

Use Python **3.13 or 3.14** (`>=3.13,<3.15`). This page describes the published
0.0.10 distribution; install the exact pin below. Published 0.0.9 remains available
without the run-reference binding and refusal-settlement changes.

```console
python3.13 -m pip install memoriesql==0.0.10
memoriesql --version
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor_contract = get_contract("memoriesql.capture.connector-cursor")
```

Catalog inspection uses bundled resources without a database, provider credentials
or external service access. The CLI does not start a memory service.

## What you can build on

- Durable authorized raw capture, receipt-backed progress and retained-fold recovery.
- Stable qualified source identity and immutable evidence packages.
- A durable semantic-task queue with fenced execution, cancellation and accounting.
- Structured observations, explicit distinct-bead corrections, local entity
  mentions and attributable classification.
- Authorized source revisiting and stored-memory/evidence inspection without
  inference on reads.

A bead is a source-anchored observation whose statements carry meaning,
attribution, conditions and uncertainty. Its short title and summary are human
navigation, not replacement evidence. Meaning is authored by an agent or human;
deterministic code validates and persists it.

Version 0.0.10 binds authored statements to the executor's run reference before
hashing and settles data-error canonical-apply refusals as invalid output;
schemas, contract payloads and dependencies are unchanged from 0.0.9. Version
0.0.9 combined declared evidence scopes (schema 25), explicit supervised
managed dispatch (schema 26), and terminal-failure cleanup that retains errors
without claiming a failed write persisted. Hard-bounded model admission remains
the default; supervised managed dispatch requires separate exact approval and
records its allowances and reported or unavailable usage truthfully. The package
supplies no provider adapter or live-call permission. No live-provider semantic
quality, production recall or complete-input correction/reauthoring is claimed.

## Explicit composition, not automatic setup

Installation does not migrate PostgreSQL, grant access, provision trusted
producers, activate tasks, start workers or configure a provider. Consumers must
explicitly compose those operations and authorize any disclosure to a model.
Core dependencies do not include provider extras.

Required target evidence must be supplied through the trusted execution path;
mechanical exposure does not prove comprehension or whole-source completeness.
Incomplete work stays incomplete. Hosts retain the worker/event loop through
`wait_for_cleanup()`; cancellation is not proof of remote termination, rollback
or reconciled usage.

Start with the [repository guide](https://github.com/JohnnyFiv3r/memoriesql),
[migration operations](https://github.com/JohnnyFiv3r/memoriesql/blob/main/docs/migrations.md),
[caller composition](https://github.com/JohnnyFiv3r/memoriesql/blob/main/docs/releasing.md#caller-composition-and-explicit-opt-in)
and [model admission](https://github.com/JohnnyFiv3r/memoriesql/blob/main/docs/real-model-admission.md).
Development documentation may describe changes not yet published; check its
release status before adopting them.

## Compatibility and license

All 0.x APIs are experimental, with no general compatibility guarantee. Published
contract payloads remain immutable; breaking payload changes require a new
contract version, and changed published artifacts require a new distribution
version. Pin releases and apply migrations explicitly.

Public core owns the canonical schema and provider-neutral runtime. Desktop
surfaces, source-specific acquisition/parsing, installers, provider bindings,
hosted services and sync are outside this package.

Source is [Apache-2.0 licensed](https://github.com/JohnnyFiv3r/memoriesql/blob/main/LICENSE).
The memoriesQL name and artwork remain John Inniger's trademarks under the
[trademark policy](https://github.com/JohnnyFiv3r/memoriesql/blob/main/TRADEMARKS.md).
Contributions use the [Developer Certificate of Origin](https://github.com/JohnnyFiv3r/memoriesql/blob/main/CONTRIBUTING.md).
