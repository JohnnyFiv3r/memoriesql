![memoriesQL](assets/trademarks/memoriesql-readme-banner.png)

# memoriesQL

**Build memories with meaning.**

memoriesQL is an open-source, local-first memory foundation for AI agents.
It preserves authorized evidence in PostgreSQL, supports agent-authored structured
observations, and keeps those observations connected to the source that supports
them. Applications can build inspectable memory without making a summary, model
response or external vector store the source of truth.

This repository contains the **provider-neutral open core**: canonical SQL,
versioned contracts and a Python runtime. It is not the memoriesQL desktop product.
It is for developers composing their own applications, services and agent hosts.

> **Status:** experimental, pre-alpha software. The published package includes
> the ledger, evidence handling and semantic execution mechanics, qualified with
> fictional installed-package tests. Installing it does not deliver a configured
> memory service, a qualified live-provider integration or production recall.

## What the core does

| Capability | What a builder can use |
|---|---|
| **Preserve evidence** | Durable authorized byte ranges, receipts, checkpoints and retained fold outcomes. Unknown transcript topology does not block raw capture. |
| **Keep identity and history** | Source-local identities, replay-safe canonical writes, immutable evidence pins and explicit distinct-bead corrections. Equal text alone is not identity. |
| **Author structured observations** | A durable task queue and fenced worker/executor path for statements, short human-readable renders, local entity mentions and attributable classification. |
| **Revisit source** | Exact evidence packages, bounded authorized readers and author-controlled rereads of retained target and explicitly supplied context. |
| **Inspect stored memory** | Authorized reads of stored meaning, mentions, classification provenance and source evidence, without running inference. |
| **Enforce operational boundaries** | Scoped authority, row-level security, attempt and cancellation fences, accounting intents, usage records and owned cleanup. |

These are composable mechanisms, not an automatically running product. Source
qualification, model admission, disclosure permission and semantic quality are
separate responsibilities; a passing schema or receipt proves none of them alone.

## From evidence to an observation

1. **Capture first.** Retain authorized evidence before parsing or model work.
2. **Establish the scope.** Bind source identity, exact evidence and what is known
   about its completeness. Never invent a turn from role alternation or a read
   window.
3. **Author meaning.** An agent inspects the required evidence and produces
   structured observations through a typed task. It can revisit authorized source
   when a qualification or identity needs checking.
4. **Accept with provenance.** Canonical application checks authorization,
   attempt fences and trusted evidence exposure before storing accepted meaning.
5. **Inspect and reuse.** Read the observation and its support; higher-level
   applications compose the user experience and eventual recall strategy.

A **bead** is a source-anchored observation, not necessarily one sentence or one
model call. Its statements carry substantive meaning, attribution, conditions and
uncertainty. Titles and short summaries help people scan that meaning; they do not
replace it or the underlying evidence. Accepted meaning is immutable. Supported
correction commands create a new linked bead instead of rewriting the old one.

A supplied evidence scope is not proof of a complete conversation, and mechanical
exposure is not proof of comprehension. If required evidence cannot be inspected,
the authoring path must report incomplete work rather than invent an answer.

## Start with the published package

Use **Python 3.13 or 3.14** (`>=3.13,<3.15`). Create an environment and pin the
current published runtime:

```console
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install memoriesql==0.0.8
memoriesql --version
memoriesql contracts --json
memoriesql contract memoriesql.capture.connector-cursor --json
```

Those commands inspect bundled contracts. They need no database or provider
credentials and do not capture data, start a worker or call a model.

```python
from memoriesql.contracts import get_contract, load_catalog

schemas = load_catalog("json_schema")
cursor_contract = get_contract("memoriesql.capture.connector-cursor")
```

The CLI currently exposes contract inspection, not a one-command running memory
service. The immutable `0.0.1a1` package is a historical catalog-only preview, not
a runtime fallback for unsupported Python versions.

### Compose a runtime deliberately

A running integration needs a compatible PostgreSQL database, explicit migration
selection and caller-owned composition:

- Authorized principals, workspace/source scopes and credentials.
- Qualified evidence producers and explicitly approved policies.
- Task/agent/model bindings, a worker host and trusted dispatch exposure.
- A separately qualified provider transport before any real model use.

Installing or upgrading the package provisions none of those. It does not migrate
a database, activate existing tasks, grant source access or select a model.
Provider-specific adapters and subscription credentials are not bundled.

Start with [migration operations](docs/migrations.md),
[caller composition](docs/releasing.md#caller-composition-and-explicit-opt-in),
[model admission](docs/real-model-admission.md) and
[complete-input execution](docs/complete-input-execution.md).
Worker hosts must keep their event loop and owned cleanup alive through
`wait_for_cleanup()`: cancellation is not proof of remote termination, database
rollback or final usage reconciliation.

## Published, implemented and planned

- **Published:** [0.0.8](https://pypi.org/project/memoriesql/0.0.8/) contains the
  canonical runtime, evidence recovery/revisiting, local mentions, classification,
  stored-result inspection and hard-bounded model-admission interface.
- **Implemented on main, not yet published:** [declared evidence scopes](docs/declared-evidence-scopes.md)
  let a qualified producer bind complete retained records without claiming a
  native turn or complete episode. This is not included in the installation above.
- **Not yet qualified as an end-to-end product:** live-provider composition,
  real-model observation quality, production source/trust provisioning and recall.

Complete-input correction/reauthoring is not yet delivered; the existing explicit
correction commands do not imply support for that execution path.

Development archives may retain the previous version number while their bytes
change. They are not replacements for a published release. Release identity and
artifact verification live in [release guidance](docs/releasing.md), not in this
introduction.

The next product proof connects real authorship to visible, authorized source
inspection. Agent-led recall and richer relationship/maintenance behavior remain
development goals, not capabilities provided by the current catalog CLI.

## Architectural commitments

- **PostgreSQL is canonical.** Evidence, observations, receipts and lifecycle
  history have one relational authority.
- **Evidence precedes inference.** Unknown topology cannot prevent authorized raw
  durability or justify fabricated identities.
- **Meaning is authored.** Agents or humans determine meaning and proposition
  boundaries. Code validates structure, authorization and persistence.
- **History is preserved.** Replay does not silently reauthor; correction does not
  erase the evidence or accepted interpretation it replaces.
- **Access stays explicit.** Source capture permission does not imply permission
  to disclose that source to a model, hosted service or another user.
- **Providers are bindings.** Public contracts remain provider-neutral.
  Operational limits and accounting must describe what a transport actually
  enforces and observes.
- **Uncertainty stays visible.** Unknown identity, missing evidence, incomplete
  work and unavailable usage are not successful results in disguise.

## Open core and product boundary

This Apache-2.0 core is independently usable by applications that provide the
required composition. The separate private Desktop product consumes released,
pinned core packages; the core never imports private product code.

Desktop UI, installers, provider-specific acquisition/parsing, Connections setup,
hosted services and sync are outside this repository. Their absence does not
change who owns the canonical schema: this public package owns it. See the
[repository boundary](docs/architecture/repository-boundary.md).

## Explore the contracts

- **Evidence:** [raw/fold recovery](docs/transcript-fold-recovery.md),
  [packages](docs/evidence-packages.md), [materialization](docs/logical-unit-materialization.md),
  [declared scopes](docs/declared-evidence-scopes.md).
- **Authorship:** [immutable observations](docs/immutable-observations.md),
  [source revisiting](docs/source-revisiting.md),
  [local mentions](docs/local-entity-mentions.md),
  [classification](docs/bead-classification.md).
- **Consumption:** [stored-memory inspection](docs/stored-bead-inspection.md),
  [public API inventory](docs/architecture/public-api-inventory.md),
  [runtime and cleanup](docs/runtime.md).

## Compatibility, verification and contribution

All 0.x APIs are experimental, with no general compatibility guarantee. Published
contract payloads remain immutable: a breaking payload change requires a new
contract version; replacing published package bytes requires a new distribution
version. Use exact release pins and explicit migration targets. See
[compatibility](docs/architecture/compatibility.md).

CI checks the explicit export inventories, historical contract/migration bytes,
deterministic archives and installed wheel/sdist behavior on Python 3.13/3.14.
Fictional acceptance proves the tested mechanics, not production readiness or
real-model semantic quality.

Contributions require a [Developer Certificate of Origin sign-off](CONTRIBUTING.md).
See [contributor guidance](AGENTS.md) and the
[security policy](SECURITY.md). Source is licensed under [Apache-2.0](LICENSE);
the memoriesQL name, elephant logo and banner belong to John Inniger and are
covered separately by the [trademark policy](TRADEMARKS.md).
