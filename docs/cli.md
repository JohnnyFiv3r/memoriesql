# Public headless CLI

Status: implementation in this development branch; no release or production
source enrollment is claimed. Published `memoriesql==0.0.12` still has only the
contract-catalog commands.

The public package owns the single `memoriesql` executable. It is useful without
Desktop and never loads private Python modules or arbitrary plugins. `contracts`,
`contract` and `--version` keep their existing behavior. This cut adds:

| Command | Behavior |
| --- | --- |
| `doctor` | Reports installed version and whether local read inputs are configured. It contacts no database or model. |
| `capabilities` | Lists the static core command set and names unavailable product capabilities. |
| `inspect <bead-id>` | Uses the installed authorized stored-bead inspection; pending/thin/failed authorship remains distinct from accepted meaning. |
| `source <bead-id> --selection-file selection.json` | Reads one exact selection through the installed source-evidence reader. The file contains a `StoredEvidenceSelection`, including package and inventory pins. No source is inferred from a bead ID alone. |
| `relations <bead-id> [--known-at timestamp]` | Uses the installed authored/assessed relation reader. `known-at` requires an explicit UTC offset. |

The installed versioned command reference is
`memoriesql contract memoriesql.core-cli.v1 --json`; `capabilities --json`
reports its available command names and explicit pending capabilities. The
generated CLI catalog is derived from the reviewed public registry.

For `source`, create the selection from an authorized bead inspection's package
pin and the needed part ordinal. For example, the file shape is:

```json
{
  "package_id": "<inspected package UUID>",
  "inventory_sha256": "<inspected 64-character digest>",
  "part_ordinal": 0,
  "representation": "normalized",
  "offset": 0,
  "limit": 16384
}
```

The placeholders must be replaced with actual authorized pins; the CLI does not
discover or guess them. The installed reader validates the selection and
rechecks authority on every page.

Each command accepts `--json`. Read commands serialize the same typed core
result for human and JSON use; JSON is compact and contains no branding. Their
exit status is 0 for `available`, 2 for `unavailable` and 3 for failure or
exhausted work. An unavailable read does not disclose partial content or reveal
whether an ID was missing, revoked or outside the caller's scope. Source content
is printed only after a successful exact authorized read; terminal output and
shell history are therefore part of the operator's disclosure decision.

Reads require `MEMORIESQL_DATABASE_URL`, `MEMORIESQL_LOCAL_CREDENTIAL` and
`MEMORIESQL_WORKSPACE_ID`. The credential is an existing authenticated local
secret; it is never accepted as a principal ID or echoed. The core reader derives
its hash, establishes current authorization inside its own short transaction,
and rechecks protected dependencies. A terminal, TTY or `--yes` grants nothing.
Missing/invalid configuration returns explicit unavailability. `doctor` checks
syntax/presence only; it is not proof that the database, grant or source is ready.
The CLI creates no grant, ingests no historical source and makes no model call.

This slice does not route Desktop's Textual interface or provider adapters. A
future, reviewed static service/client seam must preserve public core commands,
both installation orders and core-only usefulness without a core-to-Desktop
import. Source enrollment and durable grants remain a separate public-first
contract; PR-05 query/results/checkpoints and qualified recall transport retain
their release and qualification gates. No placeholder operation is reported as
successful.
