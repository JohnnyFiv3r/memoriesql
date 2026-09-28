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
| `sources enroll --request-file request.json` | Enrolls one exact, explicitly confirmed provider-neutral source through the installed authority adapter. |
| `sources grant --request-file request.json` | Grants bounded read/write permission for one enrolled source to a specified principal, subject to current authority and the existing membership/pairing checks. |
| `sources revoke --request-file request.json` | Terminally revokes one exact source with an actor-attributed reason and receipt. |
| `schema` | Describes the installed logical query catalog, which relations are prepared, reserved or not prepared, the admission rules and delivery limits. It contacts no database and starts no run. |
| `query --file query.sql --intent discover\|enumerate --view resolved\|historical` | Submits one caller-authored admitted SELECT with typed `$n` parameters through the trusted results executor and prints its closed reply. |
| `result <result-id> --digest <content-digest> [--cursor C]` | Pages one retained immutable result by its exact pin under current authority, without rerunning its query. |

Bare `sources` reports `source_inventory_not_released`; there is no public
source-inventory reader yet. Provider-specific `sources connect` remains a
Desktop composition dependency, not an enrollment synonym.

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

For `sources enroll`, use the installed `EnrollExactSource` shape. For example:

```json
{
  "request_id": "<new request UUID>",
  "source_system": "<provider-neutral system key>",
  "installation_id": "<selected installation identity or omit>",
  "object_kind": "<exact source kind>",
  "external_object_id": "<exact selected object identity>",
  "source_schema_version": 1,
  "exact_source_confirmed": true
}
```

This confirmation records operator intent for that exact identity; it does not
prove discovery, validate a provider format, import historical sessions or
start capture. `sources grant` and `sources revoke` use the installed
`GrantExactSource` and `RevokeExactSource` shapes in
`memoriesql.source-enrollment.v1`, respectively. Their request UUIDs make an
identical retry idempotent; use a new UUID for a new action. Revocation is
terminal in v1. Grant creation alone does not activate capture or bypass
current target membership and pairing authority.

Each command accepts `--json`. Read commands serialize the same typed core
result for human and JSON use. Source authority commands return an `available`
envelope with their typed receipt, or an `unavailable`/`failed` reason. JSON is
compact and contains no branding. Exit status is 0 for `available`, 2 for
`unavailable` and 3 for failure or exhausted work. An unavailable operation
does not disclose partial content or reveal whether an ID was missing, revoked
or outside the caller's scope. Source content is printed only after a successful
exact authorized read; terminal output and shell history are therefore part of
the operator's disclosure decision.

Database-backed commands require `MEMORIESQL_DATABASE_URL`, `MEMORIESQL_LOCAL_CREDENTIAL` and
`MEMORIESQL_WORKSPACE_ID`. The credential is an existing authenticated local
secret; it is never accepted as a principal ID or echoed. The core reader derives
its hash, establishes current authorization inside its own short transaction,
and rechecks protected dependencies. A terminal, TTY or `--yes` grants nothing.
Missing/invalid configuration returns explicit unavailability. `doctor` checks
syntax/presence only; it is not proof that the database, grant or source is ready.
The source authority commands require that credential to represent a human with
the appropriate current management/share capabilities; they never take a
caller-supplied actor or workspace override. The CLI makes no model call.

`query` and `result` speak `memoriesql.agent-sql-results.v1` through a trusted
executor that alone holds the control login and the restricted reader login.
`query --file` reads at most 32 KiB of SQL; `--parameters-file` holds the typed
parameter list (`[{"position": 1, "type": "uuid", "value": "..."}]`). All values
are parameters; the executor refuses SQL literals except structural ones,
unprepared relations (`unsupported_query`, feature `unprepared_relation`) and
not-yet-qualified refinement, expansion, refresh or source scopes. Replies are
printed unchanged; `--json` prints the executor's exact reply bytes. Exit status
is 0 only for `available`, 2 for `unavailable` and 3 for every other outcome,
including `unsupported_query`, `invalid_request`, `budget_exhausted` and
`settlement_pending`. A zero-row result is still `available`.

The CLI keeps one run per authenticated principal and workspace in an owner-only
state file under `MEMORIESQL_STATE_DIR` (default `$XDG_STATE_HOME/memoriesql`),
reuses it across invocations and starts a new run shortly before the stored run
expires; it never retries a refused or budgeted request. Runs are bounded
(30 minutes, two active per workspace). A follow-up `result` needs both the
`result_id` and `content_digest` from the query reply, plus its `next_cursor` for
the following page. Access is rechecked on every page; a revoked dependency
refuses the whole result.

The trusted host is configured only in the process that may hold database
credentials: `MEMORIESQL_DATABASE_URL` (a login that can assume the application
role), `MEMORIESQL_QUERY_READER_URL` and `MEMORIESQL_QUERY_READER_ROLE` for the
reviewed restricted reader, the local credential and workspace, and optionally
`MEMORIESQL_QUERY_AUTHORITY_SHA256` to refuse a drifted reader profile. Never
give those values to an agent's shell, files or processes: an agent that can
read them could bypass admitted SQL. Without them `query` and `result` report
`trusted_query_host_not_configured`.

This slice does not route Desktop's Textual interface or provider adapters. A
future, reviewed static service/client seam must preserve public core commands,
both installation orders and core-only usefulness without a core-to-Desktop
import. Provider-specific source connection and capture remain Desktop-owned;
PR-05 query/results/checkpoints and qualified recall transport retain
their release and qualification gates. No placeholder operation is reported as
successful.
